"""
Viral Conversions Server
Run: python3 server.py  |  or:  PORT=8080 python3 server.py
"""

import os
import json
import re
import string
import random
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify, send_from_directory, make_response, redirect
from datetime import datetime, timedelta, timezone
import secrets
from supabase import create_client

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__, static_folder='.')
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024  # 25 MB — room for WhatsApp playbook screenshots (base64 in JSON)

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY environment variables must be set")

db = create_client(SUPABASE_URL, SUPABASE_KEY)

def init_db():
    try:
        db.table('bookings').select('id').limit(1).execute()
        print("[DB] Connected to Supabase ✓")
    except Exception as e:
        print(f"[DB WARNING] {e}")

init_db()

# ── Helpers ───────────────────────────────────────────────────────────────────

def generate_ref_code(length=8):
    chars = string.ascii_lowercase + string.digits
    return 'cd_' + ''.join(random.choices(chars, k=length))

def unique_ref_code():
    while True:
        code = generate_ref_code()
        res = db.table('waitlist').select('id').eq('ref_code', code).execute()
        if not res.data:
            return code

# ── API Routes ────────────────────────────────────────────────────────────────

@app.route('/api/signup', methods=['POST'])
def signup():
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    ref_code_used = (data.get('ref_code') or '').strip()

    if not email or '@' not in email or '.' not in email:
        return jsonify({'success': False, 'error': 'Invalid email address.'}), 400

    try:
        res = db.table('waitlist').select('*').eq('email', email).limit(1).execute()
        existing = res.data[0] if res.data else None

        if existing:
            count_res = db.table('waitlist').select('*', count='exact').eq('referred_by', existing['ref_code']).execute()
            return jsonify({
                'success': False,
                'already_exists': True,
                'email': existing['email'],
                'ref_code': existing['ref_code'],
                'trial_days': existing['trial_days'],
                'referral_count': count_res.count or 0
            })

        new_code = unique_ref_code()
        trial_days = 7
        referred_by = None

        if ref_code_used:
            ref_res = db.table('waitlist').select('*').eq('ref_code', ref_code_used).limit(1).execute()
            referrer = ref_res.data[0] if ref_res.data else None
            if referrer:
                referred_by = ref_code_used
                trial_days = 14
                if referrer['trial_days'] < 14:
                    db.table('waitlist').update({'trial_days': 14}).eq('ref_code', ref_code_used).execute()

        db.table('waitlist').insert({
            'email': email, 'ref_code': new_code,
            'referred_by': referred_by, 'trial_days': trial_days
        }).execute()

        print(f"[SIGNUP] {email} | ref: {new_code} | via: {referred_by or 'direct'} | trial: {trial_days}d")
        return jsonify({'success': True, 'email': email, 'ref_code': new_code, 'trial_days': trial_days, 'referral_count': 0})

    except Exception as e:
        print(f"[ERROR] signup: {e}")
        return jsonify({'success': False, 'error': 'Server error. Please try again.'}), 500


@app.route('/api/count', methods=['GET'])
def count():
    res = db.table('waitlist').select('*', count='exact').execute()
    return jsonify({'count': res.count or 0})


@app.route('/api/stats', methods=['GET'])
def stats():
    total       = db.table('waitlist').select('*', count='exact').execute().count or 0
    via_referral = db.table('waitlist').select('*', count='exact').filter('referred_by', 'not.is', 'null').execute().count or 0
    fourteen_day = db.table('waitlist').select('*', count='exact').eq('trial_days', 14).execute().count or 0
    seven_day    = db.table('waitlist').select('*', count='exact').eq('trial_days', 7).execute().count or 0
    emails_sent  = db.table('sent_emails').select('*', count='exact').execute().count or 0
    return jsonify({
        'total': total, 'via_referral': via_referral,
        'fourteen_day_trial': fourteen_day, 'seven_day_trial': seven_day,
        'emails_sent': emails_sent
    })


@app.route('/api/emails', methods=['GET'])
def list_emails():
    res = db.table('waitlist').select('email,ref_code,trial_days,referred_by,created_at').order('created_at', desc=True).execute()
    return jsonify(res.data)


@app.route('/api/history', methods=['GET'])
def email_history():
    res = db.table('sent_emails').select('id,subject,body,recipients,sent_at').order('sent_at', desc=True).execute()
    return jsonify(res.data)


@app.route('/api/send-email', methods=['POST'])
def send_email_api():
    data = request.get_json(silent=True) or {}
    subject = (data.get('subject') or '').strip()
    body    = (data.get('body') or '').strip()
    test_recipient = data.get('test_recipient')

    if not subject or not body:
        return jsonify({'success': False, 'error': 'Subject and body are required.'}), 400

    smtp_host  = os.getenv('SMTP_HOST', 'smtp.gmail.com')
    smtp_port  = int(os.getenv('SMTP_PORT', 587))
    smtp_user  = os.getenv('SMTP_USER', '')
    smtp_pass  = os.getenv('SMTP_PASS', '')
    from_name  = os.getenv('FROM_NAME', 'Viral Conversions')
    from_email = os.getenv('FROM_EMAIL', smtp_user)

    if not smtp_user or not smtp_pass:
        return jsonify({'success': False, 'error': 'Email credentials not configured.'}), 500

    if test_recipient:
        recipients = [test_recipient]
    else:
        res = db.table('waitlist').select('email').execute()
        recipients = [r['email'] for r in res.data]

    if not recipients:
        return jsonify({'success': False, 'error': 'No recipients on waitlist.'}), 400

    sent = 0
    failed = 0

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo(); server.starttls(); server.login(smtp_user, smtp_pass)
            for recipient in recipients:
                try:
                    msg = MIMEMultipart('alternative')
                    msg['Subject'] = subject
                    msg['From']    = f'{from_name} <{from_email}>'
                    msg['To']      = recipient
                    msg.attach(MIMEText(body, 'plain'))
                    msg.attach(MIMEText(body_to_html(body, subject, from_name), 'html'))
                    server.sendmail(from_email, recipient, msg.as_string())
                    sent += 1
                except Exception as e:
                    failed += 1
                    print(f"[MAIL] Failed for {recipient}: {e}")

        if not test_recipient:
            db.table('sent_emails').insert({'subject': subject, 'body': body, 'recipients': sent}).execute()

    except Exception as e:
        return jsonify({'success': False, 'error': f'SMTP error: {str(e)}'}), 500

    print(f"[MAIL] Sent: {sent} | Failed: {failed} | Subject: {subject}")
    return jsonify({'success': True, 'sent': sent, 'failed': failed})


def body_to_html(text, subject, brand='Viral Conversions'):
    paragraphs = ''.join(f'<p style="margin:0 0 16px;">{p}</p>' for p in text.split('\n') if p.strip())
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#FDFAF5;font-family:Georgia,serif;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 16px;">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:#FFFFFF;border-radius:16px;border:1px solid #EDE8DF;overflow:hidden;">
        <tr>
          <td style="padding:32px 40px;background:#1A1611;text-align:center;">
            <span style="font-size:22px;font-weight:700;color:#FDFAF5;font-family:Georgia,serif;">&#10011; {brand}</span>
          </td>
        </tr>
        <tr>
          <td style="padding:40px 40px 32px;">
            <h2 style="margin:0 0 24px;font-size:24px;color:#1A1611;font-family:Georgia,serif;">{subject}</h2>
            <div style="font-size:16px;line-height:1.7;color:#5C5347;">{paragraphs}</div>
          </td>
        </tr>
        <tr>
          <td style="padding:24px 40px;border-top:1px solid #EDE8DF;text-align:center;font-size:13px;color:#9C9188;font-family:Arial,sans-serif;">
            You're receiving this because you joined the Viral Conversions waitlist.<br/>
            © 2025 Viral Conversions. All rights reserved.
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ── Booking API ──────────────────────────────────────────────────────────────

DEFAULT_AVAILABILITY = {
    "1": {"enabled": True,  "start": "09:00", "end": "17:00"},
    "2": {"enabled": True,  "start": "09:00", "end": "17:00"},
    "3": {"enabled": True,  "start": "09:00", "end": "17:00"},
    "4": {"enabled": True,  "start": "09:00", "end": "17:00"},
    "5": {"enabled": True,  "start": "09:00", "end": "17:00"},
    "6": {"enabled": False, "start": "10:00", "end": "14:00"},
    "0": {"enabled": False, "start": "10:00", "end": "14:00"},
}


@app.route('/api/ping', methods=['GET'])
def ping():
    return jsonify({'ok': True})

# ── SITE TRACKING ─────────────────────────────────────────────────────────────
@app.route('/api/track', methods=['POST'])
def track_visit():
    data = request.get_json(silent=True) or {}
    try:
        row = db.table('site_visits').insert({
            'session_id': (data.get('session_id') or '')[:64],
            'page':       (data.get('page') or '/')[:200],
            'referrer':   (data.get('referrer') or '')[:500],
        }).execute()
        vid = row.data[0]['id'] if row.data else None
        return jsonify({'id': vid})
    except Exception as e:
        return jsonify({'id': None, 'error': str(e)})

@app.route('/api/track/<vid>', methods=['PATCH', 'POST'])
def track_update(vid):
    data = request.get_json(silent=True, force=True) or {}
    update = {}
    if 'duration_seconds' in data:
        update['duration_seconds'] = max(0, int(data['duration_seconds'] or 0))
    if data.get('booked'):
        update['booked'] = True
    if update:
        try:
            db.table('site_visits').update(update).eq('id', vid).execute()
        except Exception:
            pass
    return jsonify({'ok': True})

@app.route('/api/booking', methods=['POST'])
def create_booking():
    data  = request.get_json(silent=True) or {}
    bid   = str(data.get('id', '')) or str(int(datetime.utcnow().timestamp() * 1000))
    name  = (data.get('name')  or '').strip()
    email = (data.get('email') or '').strip()
    phone = (data.get('phone') or '').strip()
    date  = (data.get('date')  or '').strip()
    time  = (data.get('time')  or '').strip()
    notes = (data.get('notes') or '').strip()

    if not all([name, email, date, time]):
        return jsonify({'success': False, 'error': 'Verplichte velden ontbreken.'}), 400

    try:
        existing = db.table('bookings').select('id').eq('date', date).eq('time', time).execute()
        if existing.data:
            return jsonify({'success': False, 'error': 'Dit tijdslot is al geboekt.'}), 409

        db.table('bookings').insert({
            'id': bid, 'name': name, 'email': email,
            'phone': phone, 'date': date, 'time': time, 'notes': notes
        }).execute()
        print(f"[BOOKING] {name} | {date} {time} | {email}")
        return jsonify({'success': True, 'id': bid})
    except Exception as e:
        print(f"[ERROR] booking: {e}")
        return jsonify({'success': False, 'error': 'Server error.'}), 500


@app.route('/api/bookings', methods=['GET'])
def list_bookings():
    today = datetime.utcnow().strftime('%Y-%m-%d')
    db.table('bookings').delete().lt('date', today).execute()
    res = db.table('bookings').select('*').order('date').order('time').execute()
    return jsonify(res.data)


@app.route('/api/booking/<bid>', methods=['DELETE'])
def delete_booking(bid):
    db.table('bookings').delete().eq('id', bid).execute()
    return jsonify({'success': True})


@app.route('/api/booked-slots', methods=['GET'])
def booked_slots():
    res = db.table('bookings').select('date,time').execute()
    result = {}
    for r in res.data:
        result.setdefault(r['date'], []).append(r['time'])
    return jsonify(result)


@app.route('/api/availability', methods=['GET'])
def get_availability():
    res = db.table('settings').select('value').eq('key', 'availability').limit(1).execute()
    if res.data:
        return jsonify(json.loads(res.data[0]['value']))
    return jsonify(DEFAULT_AVAILABILITY)


@app.route('/api/availability', methods=['PUT'])
def set_availability():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'success': False, 'error': 'No data.'}), 400
    db.table('settings').upsert({'key': 'availability', 'value': json.dumps(data)}).execute()
    return jsonify({'success': True})


# ── Onboarding API ───────────────────────────────────────────────────────────

@app.route('/api/onboarding', methods=['GET'])
def list_onboarding():
    res = db.table('onboarding').select('id,data,submitted_at').order('submitted_at', desc=True).execute()
    result = []
    for r in res.data:
        client = json.loads(r['data'])
        client['id'] = r['id']
        client['submittedAt'] = r['submitted_at']
        result.append(client)
    return jsonify(result)

@app.route('/api/onboarding', methods=['POST'])
def create_onboarding():
    data = request.get_json(silent=True) or {}
    cid  = data.get('id') or str(int(datetime.utcnow().timestamp() * 1000))
    data['id'] = cid
    try:
        db.table('onboarding').insert({'id': cid, 'data': json.dumps(data)}).execute()
        tag = 'DEMO' if data.get('formType') == 'demo' else 'ONBOARDING'
        print(f"[{tag}] {data.get('naam') or data.get('contact','?')} | {data.get('email','?')}")
        return jsonify({'success': True, 'id': cid})
    except Exception as e:
        print(f"[ERROR] onboarding: {e}")
        return jsonify({'success': False, 'error': 'Server error.'}), 500

@app.route('/api/onboarding/<cid>', methods=['PUT'])
def update_onboarding(cid):
    data = request.get_json(silent=True) or {}
    res  = db.table('onboarding').select('data').eq('id', cid).limit(1).execute()
    if not res.data:
        return jsonify({'success': False, 'error': 'Not found.'}), 404
    client = json.loads(res.data[0]['data'])
    client.update(data)
    db.table('onboarding').update({'data': json.dumps(client)}).eq('id', cid).execute()
    return jsonify({'success': True})

@app.route('/api/onboarding/<cid>', methods=['DELETE'])
def delete_onboarding(cid):
    db.table('onboarding').delete().eq('id', cid).execute()
    return jsonify({'success': True})


# ── Auth ─────────────────────────────────────────────────────────────────────

ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'viralconversions2024')
AUTH_COOKIE    = 'vc_admin_token'
_valid_tokens  = set()

SALES_AUTH_COOKIE = 'vc_sales_token'
_sales_sessions   = {}   # token → member_id

def _check_auth():
    return request.cookies.get(AUTH_COOKIE, '') in _valid_tokens

LOGIN_HTML = '''<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Login — Viral Conversions</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: "Plus Jakarta Sans", sans-serif; font-weight: 500; background: #07090F; color: #fff; min-height: 100dvh; display: flex; align-items: center; justify-content: center; overflow: hidden; -webkit-font-smoothing: antialiased; }
    .bg2 { position: fixed; inset: 0; z-index: 0; overflow: hidden; pointer-events: none; }
    .bg2-grad { position: absolute; inset: 0; background: radial-gradient(ellipse 160% 140% at 0% 0%, rgb(10,20,60) 0%, rgb(6,10,28) 55%, rgb(0,0,0) 100%); mask: radial-gradient(ellipse 130% 110% at 3% 3%, rgba(0,0,0,0.65) 0%, rgba(0,0,0,0.35) 42%, rgba(0,0,0,0.1) 65%, rgba(0,0,0,0) 100%); -webkit-mask: radial-gradient(ellipse 130% 110% at 3% 3%, rgba(0,0,0,0.65) 0%, rgba(0,0,0,0.35) 42%, rgba(0,0,0,0.1) 65%, rgba(0,0,0,0) 100%); opacity: 0.70; }
    .bg2-streak { position: absolute; inset: 0; opacity: 0.22; background: linear-gradient(135deg, rgb(96,165,250) 0%, rgba(96,165,250,0) 52%); }
    .bg2-dots { position: absolute; inset: 0; opacity: 0.14; background-image: radial-gradient(circle at 1px 1px, rgba(255,255,255,0.55) 1px, transparent 0); background-size: 22px 22px; }
    .card { position: relative; z-index: 1; background: rgba(255,255,255,0.055); backdrop-filter: blur(40px) saturate(180%); -webkit-backdrop-filter: blur(40px) saturate(180%); border: 1px solid rgba(255,255,255,0.09); box-shadow: 0 2px 60px rgba(0,0,0,0.65), inset 0 1px 0 rgba(255,255,255,0.12), inset 0 -1px 0 rgba(0,0,0,0.08); border-radius: 28px; padding: 44px 40px 40px; width: calc(100% - 32px); max-width: 400px; animation: cardIn 0.55s cubic-bezier(0.16,1,0.3,1) both; }
    @keyframes cardIn { from { opacity: 0; transform: translateY(20px) scale(0.97); } to { opacity: 1; transform: none; } }
    .logo-wrap { display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 28px; }
    .logo-img { height: 26px; width: auto; filter: brightness(0) invert(1); }
    .logo-name { font-size: 15px; font-weight: 800; letter-spacing: -0.02em; }
    h1 { font-size: 22px; font-weight: 900; letter-spacing: -0.03em; margin-bottom: 6px; text-align: center; }
    .sub { font-size: 13px; color: rgba(255,255,255,0.42); text-align: center; margin-bottom: 28px; line-height: 1.5; }
    .input-wrap { position: relative; margin-bottom: 12px; }
    .input-wrap svg { position: absolute; left: 16px; top: 50%; transform: translateY(-50%); opacity: 0.32; pointer-events: none; }
    input[type=password] { width: 100%; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.09); border-radius: 14px; padding: 14px 16px 14px 44px; color: #fff; font-size: 15px; font-family: inherit; font-weight: 500; outline: none; transition: border-color 0.2s, background 0.2s; letter-spacing: 0.08em; }
    input[type=password]::placeholder { letter-spacing: 0; color: rgba(255,255,255,0.28); }
    input[type=password]:focus { border-color: rgba(37,99,235,0.55); background: rgba(37,99,235,0.06); }
    button[type=submit] { width: 100%; background: rgba(255,255,255,0.93); color: #06040F; border: none; border-radius: 100px; padding: 15px; font-size: 14px; font-weight: 800; cursor: pointer; font-family: inherit; letter-spacing: -0.01em; margin-top: 4px; box-shadow: 0 0 6px rgba(0,0,0,0.03), 0 2px 6px rgba(0,0,0,0.08), inset 3px 3px 0.5px -3px rgba(0,0,0,0.9), inset -3px -3px 0.5px -3px rgba(0,0,0,0.85), inset 1px 1px 1px -0.5px rgba(0,0,0,0.6), inset -1px -1px 1px -0.5px rgba(0,0,0,0.6), inset 0 0 6px 6px rgba(0,0,0,0.12), 0 0 12px rgba(255,255,255,0.15); transition: filter 0.18s, transform 0.15s cubic-bezier(0.16,1,0.3,1); }
    button[type=submit]:hover { filter: brightness(1.05); }
    button[type=submit]:active { transform: scale(0.98); }
    .error-msg { background: rgba(239,68,68,0.10); border: 1px solid rgba(239,68,68,0.25); border-radius: 10px; color: #f87171; font-size: 13px; font-weight: 600; padding: 10px 14px; margin-bottom: 16px; text-align: center; animation: shake 0.38s cubic-bezier(0.16,1,0.3,1); }
    @keyframes shake { 0%,100%{ transform: translateX(0); } 25%{ transform: translateX(-5px); } 75%{ transform: translateX(5px); } }
  </style>
</head>
<body>
  <div class="bg2"><div class="bg2-grad"></div><div class="bg2-streak"></div><div class="bg2-dots"></div></div>
  <div class="card">
    <div class="logo-wrap">
      <img class="logo-img" src="/logo\'s/VC%20black%20logo.png" alt="VC" />
      <span class="logo-name">Viral Conversions</span>
    </div>
    <h1>Welkom terug</h1>
    <p class="sub">Voer het wachtwoord in om toegang te krijgen</p>
    {error}
    <form method="POST" action="/login">
      <input type="hidden" name="next" value="{next}" />
      <div class="input-wrap">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
        <input type="password" name="password" placeholder="Wachtwoord" autofocus autocomplete="current-password" />
      </div>
      <button type="submit">Inloggen &nbsp;→</button>
    </form>
  </div>
</body>
</html>'''

@app.route('/login', methods=['GET', 'POST'])
def login():
    next_url = request.args.get('next') or request.form.get('next') or '/admin'
    if request.method == 'POST':
        pw = request.form.get('password', '')
        if pw == ADMIN_PASSWORD:
            token = secrets.token_hex(32)
            _valid_tokens.add(token)
            resp = make_response(redirect(next_url))
            resp.set_cookie(AUTH_COOKIE, token, httponly=True, samesite='Lax', max_age=60*60*24*30)
            return resp
        html = LOGIN_HTML.replace('{error}', '<p style="color:#FF6B6B;margin-bottom:12px">Onjuist wachtwoord.</p>').replace('{next}', next_url)
        return html, 401
    return LOGIN_HTML.replace('{error}', '').replace('{next}', next_url)

@app.route('/logout')
def logout():
    token = request.cookies.get(AUTH_COOKIE, '')
    _valid_tokens.discard(token)
    resp = make_response(redirect('/login'))
    resp.delete_cookie(AUTH_COOKIE)
    return resp

def require_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _check_auth():
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Niet ingelogd. Herlaad de pagina en log opnieuw in.'}), 401
            return redirect(f'/login?next={request.path}')
        return f(*args, **kwargs)
    return decorated

@app.route('/api/admin/traffic', methods=['GET'])
@require_auth
def admin_traffic():
    from datetime import timezone, timedelta
    from collections import Counter
    import re
    now = datetime.now(timezone.utc)
    thirty_days_ago = (now - timedelta(days=30)).isoformat()
    today_str = now.date().isoformat()
    week_ago  = (now - timedelta(days=7)).date().isoformat()
    res = db.table('site_visits').select('*').gte('created_at', thirty_days_ago).order('created_at', desc=True).execute()
    visits = res.data or []
    def unique(lst):
        return len(set(v['session_id'] for v in lst if v.get('session_id')))
    def avg_dur(lst):
        d = [v['duration_seconds'] for v in lst if v.get('duration_seconds')]
        return round(sum(d) / len(d)) if d else 0
    today_v = [v for v in visits if v['created_at'][:10] == today_str]
    week_v  = [v for v in visits if v['created_at'][:10] >= week_ago]
    def clean_ref(r):
        if not r: return 'Direct'
        m = re.search(r'(?:https?://)?(?:www\.)?([^/]+)', r)
        return m.group(1) if m else r
    ref_counts = Counter(clean_ref(v.get('referrer')) for v in visits)
    top_refs = [{'source': k, 'count': c} for k, c in ref_counts.most_common(8)]
    daily = {}
    for i in range(13, -1, -1):
        d = (now - timedelta(days=i)).date().isoformat()
        daily[d] = {'date': d, 'visitors': 0, 'booked': 0}
    for v in visits:
        d = v['created_at'][:10]
        if d in daily:
            daily[d]['visitors'] += 1
            if v.get('booked'):
                daily[d]['booked'] += 1
    total_booked = sum(1 for v in visits if v.get('booked'))
    conv = round(total_booked / len(visits) * 100, 1) if visits else 0
    return jsonify({
        'today':  {'visitors': len(today_v), 'unique': unique(today_v)},
        'week':   {'visitors': len(week_v),  'unique': unique(week_v)},
        'month':  {'visitors': len(visits),  'unique': unique(visits), 'avg_duration': avg_dur(visits), 'conversion': conv, 'booked': total_booked},
        'referrers': top_refs,
        'daily':  list(daily.values()),
    })

def _get_sales_token():
    return request.cookies.get(SALES_AUTH_COOKIE, '')

def _check_sales_auth():
    return _get_sales_token() in _sales_sessions

def _get_sales_member_id():
    return _sales_sessions.get(_get_sales_token())

SALES_LOGIN_HTML = '''<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sales Portal — Viral Conversions</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: "Plus Jakarta Sans", sans-serif; font-weight: 500; background: #07090F; color: #fff; min-height: 100dvh; display: flex; align-items: center; justify-content: center; overflow: hidden; -webkit-font-smoothing: antialiased; }
    .bg2 { position: fixed; inset: 0; z-index: 0; overflow: hidden; pointer-events: none; }
    .bg2-grad { position: absolute; inset: 0; background: radial-gradient(ellipse 160% 140% at 0% 0%, rgb(10,20,60) 0%, rgb(6,10,28) 55%, rgb(0,0,0) 100%); mask: radial-gradient(ellipse 130% 110% at 3% 3%, rgba(0,0,0,0.65) 0%, rgba(0,0,0,0.35) 42%, rgba(0,0,0,0.1) 65%, rgba(0,0,0,0) 100%); -webkit-mask: radial-gradient(ellipse 130% 110% at 3% 3%, rgba(0,0,0,0.65) 0%, rgba(0,0,0,0.35) 42%, rgba(0,0,0,0.1) 65%, rgba(0,0,0,0) 100%); opacity: 0.70; }
    .bg2-streak { position: absolute; inset: 0; opacity: 0.22; background: linear-gradient(135deg, rgb(96,165,250) 0%, rgba(96,165,250,0) 52%); }
    .bg2-dots { position: absolute; inset: 0; opacity: 0.14; background-image: radial-gradient(circle at 1px 1px, rgba(255,255,255,0.55) 1px, transparent 0); background-size: 22px 22px; }
    .card { position: relative; z-index: 1; background: rgba(255,255,255,0.055); backdrop-filter: blur(40px) saturate(180%); -webkit-backdrop-filter: blur(40px) saturate(180%); border: 1px solid rgba(255,255,255,0.09); box-shadow: 0 2px 60px rgba(0,0,0,0.65), inset 0 1px 0 rgba(255,255,255,0.12), inset 0 -1px 0 rgba(0,0,0,0.08); border-radius: 28px; padding: 44px 40px 40px; width: calc(100% - 32px); max-width: 400px; animation: cardIn 0.55s cubic-bezier(0.16,1,0.3,1) both; }
    @keyframes cardIn { from { opacity: 0; transform: translateY(20px) scale(0.97); } to { opacity: 1; transform: none; } }
    .logo-wrap { display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 20px; }
    .logo-img { height: 26px; width: auto; filter: brightness(0) invert(1); }
    .logo-name { font-size: 15px; font-weight: 800; letter-spacing: -0.02em; }
    .badge { display: inline-flex; align-items: center; background: rgba(37,99,235,0.15); border: 1px solid rgba(37,99,235,0.32); color: #60a5fa; font-size: 10px; font-weight: 800; letter-spacing: 0.10em; text-transform: uppercase; padding: 3px 11px; border-radius: 100px; margin-bottom: 16px; }
    h1 { font-size: 22px; font-weight: 900; letter-spacing: -0.03em; margin-bottom: 6px; text-align: center; }
    .sub { font-size: 13px; color: rgba(255,255,255,0.42); text-align: center; margin-bottom: 28px; line-height: 1.5; }
    .input-wrap { position: relative; margin-bottom: 12px; }
    .input-wrap svg { position: absolute; left: 16px; top: 50%; transform: translateY(-50%); opacity: 0.32; pointer-events: none; }
    input[type=email], input[type=password] { width: 100%; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.09); border-radius: 14px; padding: 14px 16px 14px 44px; color: #fff; font-size: 15px; font-family: inherit; font-weight: 500; outline: none; transition: border-color 0.2s, background 0.2s; }
    input[type=email]::placeholder, input[type=password]::placeholder { color: rgba(255,255,255,0.28); }
    input[type=email]:focus, input[type=password]:focus { border-color: rgba(37,99,235,0.55); background: rgba(37,99,235,0.06); }
    button[type=submit] { width: 100%; background: rgba(255,255,255,0.93); color: #06040F; border: none; border-radius: 100px; padding: 15px; font-size: 14px; font-weight: 800; cursor: pointer; font-family: inherit; letter-spacing: -0.01em; margin-top: 4px; box-shadow: 0 0 6px rgba(0,0,0,0.03), 0 2px 6px rgba(0,0,0,0.08), inset 3px 3px 0.5px -3px rgba(0,0,0,0.9), inset -3px -3px 0.5px -3px rgba(0,0,0,0.85), inset 1px 1px 1px -0.5px rgba(0,0,0,0.6), inset -1px -1px 1px -0.5px rgba(0,0,0,0.6), inset 0 0 6px 6px rgba(0,0,0,0.12), 0 0 12px rgba(255,255,255,0.15); transition: filter 0.18s, transform 0.15s cubic-bezier(0.16,1,0.3,1); }
    button[type=submit]:hover { filter: brightness(1.05); }
    button[type=submit]:active { transform: scale(0.98); }
    .error-msg { background: rgba(239,68,68,0.10); border: 1px solid rgba(239,68,68,0.25); border-radius: 10px; color: #f87171; font-size: 13px; font-weight: 600; padding: 10px 14px; margin-bottom: 16px; text-align: center; animation: shake 0.38s cubic-bezier(0.16,1,0.3,1); }
    @keyframes shake { 0%,100%{ transform: translateX(0); } 25%{ transform: translateX(-5px); } 75%{ transform: translateX(5px); } }
    .apply-link { text-align: center; margin-top: 20px; font-size: 13px; color: rgba(255,255,255,0.38); }
    .apply-link a { color: #60a5fa; text-decoration: none; font-weight: 700; transition: color 0.15s; }
    .apply-link a:hover { color: #93c5fd; }
  </style>
</head>
<body>
  <div class="bg2"><div class="bg2-grad"></div><div class="bg2-streak"></div><div class="bg2-dots"></div></div>
  <div class="card">
    <div class="logo-wrap">
      <img class="logo-img" src="/logo\'s/VC%20black%20logo.png" alt="VC" />
      <span class="logo-name">Viral Conversions</span>
    </div>
    <div style="text-align:center;margin-bottom:4px"><span class="badge">Sales Team</span></div>
    <h1>Sales Portal</h1>
    <p class="sub">Log in met jouw account</p>
    {error}
    <form method="POST" action="/sales-login">
      <input type="hidden" name="next" value="{next}" />
      <div class="input-wrap">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>
        <input type="email" name="email" placeholder="jouw@email.com" autofocus autocomplete="email" />
      </div>
      <div class="input-wrap">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
        <input type="password" name="password" placeholder="Wachtwoord" autocomplete="current-password" />
      </div>
      <button type="submit">Inloggen &nbsp;→</button>
    </form>
    <p class="apply-link">Wil je ons team joinen? <a href="/sales-apply">Solliciteer hier</a></p>
  </div>
</body>
</html>'''

@app.route('/sales-login', methods=['GET', 'POST'])
def sales_login():
    from werkzeug.security import check_password_hash
    next_url = request.args.get('next') or request.form.get('next') or '/sales'
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        pw    = request.form.get('password', '')
        try:
            res = db.table('sales_members').select('*').eq('email', email).eq('status', 'active').limit(1).execute()
            member = res.data[0] if res.data else None
        except Exception:
            member = None
        if member and member.get('password_hash') and check_password_hash(member['password_hash'], pw):
            token = secrets.token_hex(32)
            _sales_sessions[token] = member['id']
            resp = make_response(redirect(next_url))
            resp.set_cookie(SALES_AUTH_COOKIE, token, httponly=True, samesite='Lax', max_age=60*60*24*30)
            return resp
        html = SALES_LOGIN_HTML.replace('{error}', '<div class="error-msg">Onjuist e-mail of wachtwoord.</div>').replace('{next}', next_url)
        return html, 401
    return SALES_LOGIN_HTML.replace('{error}', '').replace('{next}', next_url)

@app.route('/sales-logout')
def sales_logout():
    token = request.cookies.get(SALES_AUTH_COOKIE, '')
    _sales_sessions.pop(token, None)
    resp = make_response(redirect('/sales-login'))
    resp.delete_cookie(SALES_AUTH_COOKIE)
    return resp

def require_sales_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _check_sales_auth():
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Sessie verlopen'}), 401
            return redirect(f'/sales-login?next={request.path}')
        return f(*args, **kwargs)
    return decorated


# ── Sales API ─────────────────────────────────────────────────────────────────

def _gen_sales_ref_code():
    chars = string.ascii_lowercase + string.digits
    return 'sm_' + ''.join(random.choices(chars, k=8))

def _unique_sales_ref():
    while True:
        code = _gen_sales_ref_code()
        res = db.table('sales_members').select('id').eq('ref_code', code).execute()
        if not res.data:
            return code

WA_DAILY_MINIMUM = 25
WA_PENALTY_HOURS = 48
WA_RECOVERY_DAYS = 7
WA_BONUS_DAYS = 14
WA_INSURANCE_WINDOW_DAYS = 30  # 1 freebie miss-day allowed per rolling window

def _is_sunday(d):
    return d.weekday() == 6

def _step_back_skip_sunday(d):
    """Move cursor one day back, then keep stepping back over any Sundays."""
    d = d - timedelta(days=1)
    while _is_sunday(d):
        d = d - timedelta(days=1)
    return d

def _sundays_between(start_date, end_date):
    """Count Sundays inclusive of both end dates."""
    if start_date > end_date:
        return 0
    count = 0
    d = start_date
    while d <= end_date:
        if _is_sunday(d):
            count += 1
        d += timedelta(days=1)
    return count

def _compute_whatsapp_state(member_id):
    """Computes WhatsApp commission state from prospect_list WA outreach logs.

    Only logs with source='prospect' count toward the streak. Sundays are
    rust-/rest-days: they do not count toward streak length, but never break
    a streak — the streak walks straight through them.
    Returns rich dict with streak, insurance status, yesterday/today,
    longest streak ever, and daily counts for visualization.
    """
    from datetime import timezone, timedelta
    now = datetime.now(timezone.utc)
    today = now.date()

    logs_res = db.table('wa_outreach_log') \
        .select('created_at') \
        .eq('member_id', str(member_id)) \
        .eq('source', 'prospect') \
        .order('created_at', desc=True) \
        .execute()
    logs = logs_res.data or []

    daily_counts = {}
    last_at = None
    parsed_dts = []
    for r in logs:
        ts = r.get('created_at')
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            continue
        if last_at is None:
            last_at = dt
        parsed_dts.append(dt)
        d = dt.astimezone(timezone.utc).date().isoformat()
        daily_counts[d] = daily_counts.get(d, 0) + 1

    today_iso = today.isoformat()
    yesterday_iso = (today - timedelta(days=1)).isoformat()
    today_count = daily_counts.get(today_iso, 0)
    yesterday_count = daily_counts.get(yesterday_iso, 0)

    if last_at is None:
        return {
            'rate': 0.25, 'state': 'base', 'streak_days': 0, 'streak_raw': 0,
            'today_count': 0, 'yesterday_count': 0,
            'hours_since_last': None, 'daily_counts': {}, 'recent_penalty': False,
            'longest_streak_ever': 0,
            'insurance_available': True, 'insurance_in_use': False,
        }

    hours_since_last = (now - last_at).total_seconds() / 3600.0
    # Sundays are rest days — subtract 24h per Sunday between last outreach and now
    sundays_in_idle_gap = _sundays_between(
        last_at.astimezone(timezone.utc).date(), today
    )
    effective_hours_since_last = max(0.0, hours_since_last - 24.0 * sundays_in_idle_gap)

    def is_active(d):
        return daily_counts.get(d.isoformat(), 0) >= WA_DAILY_MINIMUM

    # Compute current streak: consecutive active non-Sunday days ending today.
    # Sundays are skipped entirely: they don't add to the streak, but they
    # don't break it either — the walk steps over them.
    streak_raw = 0
    cursor = today
    while _is_sunday(cursor):
        cursor = cursor - timedelta(days=1)
    if not is_active(cursor):
        # Today isn't complete yet — walk back to find streak that ended yesterday
        cursor = _step_back_skip_sunday(cursor)
    while is_active(cursor):
        streak_raw += 1
        cursor = _step_back_skip_sunday(cursor)

    # Streak insurance: allow 1 missed day in current streak, if no other
    # missed day has been "consumed" by insurance in the last 30 days.
    # Strategy: extend backwards across exactly 1 inactive (non-Sunday) day
    # if the day before it is active, and no prior insurance use is detected
    # within the past WA_INSURANCE_WINDOW_DAYS.
    insurance_available = True
    insurance_in_use = False
    streak = streak_raw

    if streak_raw > 0:
        gap_day = cursor  # the (non-Sunday) day that broke the streak
        pre_gap = _step_back_skip_sunday(gap_day)
        if is_active(pre_gap):
            # Check whether any "insurance event" has happened in last 30d.
            scan_until = today - timedelta(days=WA_INSURANCE_WINDOW_DAYS)
            d = pre_gap
            prior_insurance_used = False
            while d >= scan_until:
                if _is_sunday(d):
                    d -= timedelta(days=1)
                    continue
                if not is_active(d):
                    prev_d = _step_back_skip_sunday(d)
                    next_d = d + timedelta(days=1)
                    while _is_sunday(next_d):
                        next_d += timedelta(days=1)
                    if is_active(prev_d) and is_active(next_d):
                        prior_insurance_used = True
                        break
                d -= timedelta(days=1)
            if not prior_insurance_used:
                # Apply insurance: extend streak across the gap (still skipping Sundays)
                insurance_in_use = True
                insurance_available = False
                extra = 0
                cursor2 = _step_back_skip_sunday(gap_day)
                while is_active(cursor2):
                    extra += 1
                    cursor2 = _step_back_skip_sunday(cursor2)
                streak = streak_raw + extra
            else:
                insurance_available = False

    # Detect recent penalty (48h+ gap in last 14 days), Sundays subtracted
    recent_penalty = False
    cutoff = now - timedelta(days=WA_BONUS_DAYS)
    recent_dts = [dt for dt in parsed_dts if dt >= cutoff]
    for i in range(len(recent_dts) - 1):
        later = recent_dts[i]
        earlier = recent_dts[i + 1]
        gap_h = (later - earlier).total_seconds() / 3600.0
        sg = _sundays_between(
            earlier.astimezone(timezone.utc).date(),
            later.astimezone(timezone.utc).date(),
        )
        gap_h -= 24.0 * sg
        if gap_h > WA_PENALTY_HOURS:
            recent_penalty = True
            break

    # Longest streak ever (scan all daily_counts) — skip Sundays
    longest = 0
    if daily_counts:
        sorted_days = sorted(daily_counts.keys())
        first = datetime.fromisoformat(sorted_days[0]).date()
        last = datetime.fromisoformat(sorted_days[-1]).date()
        d = first
        run = 0
        while d <= last:
            if _is_sunday(d):
                d += timedelta(days=1)
                continue
            if daily_counts.get(d.isoformat(), 0) >= WA_DAILY_MINIMUM:
                run += 1
                if run > longest:
                    longest = run
            else:
                run = 0
            d += timedelta(days=1)
    longest = max(longest, streak)

    # Determine rate
    if effective_hours_since_last > WA_PENALTY_HOURS:
        rate, state = 0.20, 'penalty'
    elif streak >= WA_BONUS_DAYS and not recent_penalty:
        rate, state = 0.30, 'bonus'
    elif streak >= WA_RECOVERY_DAYS:
        rate, state = 0.25, 'base'
    elif recent_penalty:
        rate, state = 0.20, 'recovering'
    else:
        rate, state = 0.25, 'base'

    return {
        'rate': rate, 'state': state,
        'streak_days': streak, 'streak_raw': streak_raw,
        'today_count': today_count, 'yesterday_count': yesterday_count,
        'hours_since_last': hours_since_last,
        'daily_counts': daily_counts, 'recent_penalty': recent_penalty,
        'longest_streak_ever': longest,
        'insurance_available': insurance_available,
        'insurance_in_use': insurance_in_use,
    }

def _compute_whatsapp_rate(member_id):
    return _compute_whatsapp_state(member_id)['rate']

def _maybe_log_streak_break(member_id, member_name, current_streak):
    """Lazy detection: if member's streak just dropped to 0 from >=7, log to feed.
    Uses sales_members.last_known_streak to track transitions.
    """
    try:
        res = db.table('sales_members').select('last_known_streak').eq('id', str(member_id)).limit(1).execute()
        if not res.data:
            return
        last_known = res.data[0].get('last_known_streak')
        last_known = int(last_known) if last_known is not None else 0
        if current_streak != last_known:
            db.table('sales_members').update({'last_known_streak': current_streak}).eq('id', str(member_id)).execute()
            if last_known >= WA_RECOVERY_DAYS and current_streak < last_known and current_streak == 0:
                _log_activity(member_id, member_name, 'streak_break',
                              f'verloor zijn/haar {last_known}-dagen WhatsApp-streak 💔')
            elif current_streak in (WA_RECOVERY_DAYS, WA_BONUS_DAYS, 21, 30) and current_streak > last_known:
                _log_activity(member_id, member_name, 'streak_milestone',
                              f'bereikte een {current_streak}-dagen WhatsApp-streak 🔥')
    except Exception as e:
        print(f'[STREAK-BREAK] {e}')

def _get_effective_rate(member):
    """Returns the effective commission rate (0–1) for a member dict."""
    if member.get('name') == 'Julian Verboom' or member.get('email') == 'julian@viralconversions.io':
        return 0.0
    override = member.get('commission_override')
    if override is not None:
        return float(override) / 100.0
    contract_type = member.get('contract_type') or 'legacy'
    if contract_type == 'whatsapp':
        return _compute_whatsapp_rate(member.get('id'))
    if contract_type == 'legacy':
        return 0.40
    # New contract: tier based on total cumulative commission earned
    mid = member.get('id')
    earned_res = db.table('warm_leads').select('commission_amount').eq('added_by_id', mid).eq('status', 'closed').execute()
    total_earned = sum(float(r['commission_amount'] or 0) for r in earned_res.data)
    if total_earned >= 2500:
        return 0.35
    elif total_earned >= 1000:
        return 0.30
    else:
        return 0.25

def _period_filter(q, table_alias='created_at'):
    from datetime import timezone, timedelta
    now = datetime.now(timezone.utc)
    period = request.args.get('period', 'total')
    if period == 'daily':
        cutoff = (now - timedelta(days=1)).isoformat()
    elif period == 'weekly':
        cutoff = (now - timedelta(weeks=1)).isoformat()
    elif period == 'monthly':
        cutoff = (now - timedelta(days=30)).isoformat()
    else:
        cutoff = None
    return cutoff

@app.route('/api/sales/me', methods=['GET'])
@require_sales_auth
def sales_me():
    mid = _get_sales_member_id()
    now_iso = datetime.utcnow().isoformat()
    db.table('sales_members').update({'last_login': now_iso}).eq('id', str(mid)).execute()
    res = db.table('sales_members').select('id,name,email,phone,ref_code,bonus_owed,first_sale_counted,contract_type,commission_override,callmebot_key,whatsapp_phone,is_calling,session_start,last_login').eq('id', mid).limit(1).execute()
    if not res.data:
        return jsonify({'error': 'Not found'}), 404
    m = res.data[0]
    rate = _get_effective_rate(m)
    m['effective_rate_pct'] = round(rate * 100)
    return jsonify(m)

@app.route('/api/sales/stats', methods=['GET'])
@require_sales_auth
def sales_stats():
    from datetime import timezone, timedelta
    now = datetime.now(timezone.utc)
    periods = {
        'total':   None,
        'monthly': (now - timedelta(days=30)).isoformat(),
        'weekly':  (now - timedelta(weeks=1)).isoformat(),
        'daily':   (now - timedelta(days=1)).isoformat(),
    }
    result = {}
    for period, cutoff in periods.items():
        q_leads    = db.table('warm_leads').select('*', count='exact')
        q_closed   = db.table('warm_leads').select('closed_amount,commission_amount').eq('status', 'closed')
        q_prospect = db.table('prospect_list').select('*', count='exact').eq('called', True)
        if cutoff:
            q_leads    = q_leads.gte('created_at', cutoff)
            q_closed   = q_closed.gte('closed_at', cutoff)
            q_prospect = q_prospect.gte('called_at', cutoff)
        leads_res    = q_leads.execute()
        closed_res   = q_closed.execute()
        prospect_res = q_prospect.execute()
        revenue    = sum(float(r['closed_amount'] or 0) for r in closed_res.data)
        commission = sum(float(r['commission_amount'] or 0) for r in closed_res.data)
        result[period] = {
            'revenue':      revenue,
            'commission':   commission,
            'closes':       len(closed_res.data),
            'warm_leads':   leads_res.count or 0,
            'called_leads': prospect_res.count or 0,
        }
    return jsonify(result)

@app.route('/api/sales/leaderboard/today', methods=['GET'])
@require_sales_auth
def leaderboard_today():
    from datetime import timezone, timedelta
    # Use NL timezone (UTC+1 conservative) so midnight matches Amsterdam
    nl_tz = timezone(timedelta(hours=1))
    today = datetime.now(nl_tz).date().isoformat()
    calls_res   = db.table('prospect_list').select('called_by_name').eq('called', True).gte('called_at', today).execute()
    leads_res   = db.table('warm_leads').select('added_by_name').gte('created_at', today).execute()
    members_res = db.table('sales_members').select('name').eq('status', 'active').execute()
    call_counts = {}
    for r in calls_res.data:
        n = r.get('called_by_name') or 'Onbekend'
        call_counts[n] = call_counts.get(n, 0) + 1
    lead_counts = {}
    for r in leads_res.data:
        n = r.get('added_by_name') or 'Onbekend'
        lead_counts[n] = lead_counts.get(n, 0) + 1
    result = [{'name': m['name'], 'calls': call_counts.get(m['name'], 0), 'leads': lead_counts.get(m['name'], 0)} for m in members_res.data]
    result.sort(key=lambda x: x['calls'], reverse=True)
    return jsonify(result)


@app.route('/api/sales/my-stats', methods=['GET'])
@require_sales_auth
def my_sales_stats():
    from datetime import timezone, timedelta
    mid = _get_sales_member_id()
    now = datetime.now(timezone.utc)
    periods = {
        'total':   None,
        'monthly': (now - timedelta(days=30)).isoformat(),
        'weekly':  (now - timedelta(weeks=1)).isoformat(),
        'daily':   (now - timedelta(days=1)).isoformat(),
    }
    result = {}
    for period, cutoff in periods.items():
        q_leads    = db.table('warm_leads').select('*', count='exact').eq('added_by_id', mid)
        q_closed   = db.table('warm_leads').select('closed_amount,commission_amount').eq('added_by_id', mid).eq('status', 'closed')
        q_prospect = db.table('prospect_list').select('*', count='exact').eq('called_by_id', str(mid)).eq('called', True)
        if cutoff:
            q_leads    = q_leads.gte('created_at', cutoff)
            q_closed   = q_closed.gte('closed_at', cutoff)
            q_prospect = q_prospect.gte('called_at', cutoff)
        leads_res    = q_leads.execute()
        closed_res   = q_closed.execute()
        prospect_res = q_prospect.execute()
        result[period] = {
            'warm_leads':   leads_res.count or 0,
            'called_leads': prospect_res.count or 0,
            'closes':       len(closed_res.data),
            'revenue':      sum(float(r['closed_amount'] or 0) for r in closed_res.data),
            'commission':   sum(float(r['commission_amount'] or 0) for r in closed_res.data),
        }
    return jsonify(result)


@app.route('/api/sales/kpi-stats', methods=['GET'])
@require_sales_auth
def sales_kpi_stats():
    """KPI dashboard data: warm-lead funnel, demo funnel, prospect→lead conversion
    by source (phone/whatsapp) and afhaak-analyse.
    Accepts:
      ?period=total|daily|weekly|monthly             — preset window
      ?from=YYYY-MM-DD&to=YYYY-MM-DD                  — custom calendar range
      ?preset=today|yesterday|this_week|last_week|...— extended presets
      ?member_id=<id>                                 — filter all data to one
                                                       sales member's owned
                                                       leads/clients/prospects
      ?close_status=geclosed|aanbetaling|volledig_betaald
                                                     — which demo_status counts
                                                       as a 'close' for the
                                                       warm-lead→close metric
    """
    from datetime import timezone, timedelta, date as _date
    period = (request.args.get('period') or '').strip()
    preset = (request.args.get('preset') or '').strip()
    f_str  = (request.args.get('from')   or '').strip()
    t_str  = (request.args.get('to')     or '').strip()
    member_id    = (request.args.get('member_id')    or '').strip()
    close_status = (request.args.get('close_status') or 'geclosed').strip()
    if close_status not in ('geclosed', 'aanbetaling', 'volledig_betaald'):
        close_status = 'geclosed'
    now = datetime.now(timezone.utc)
    start, end = None, None

    def _parse_d(s):
        try:    return datetime.strptime(s, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        except Exception: return None

    if f_str or t_str:
        start = _parse_d(f_str)
        end   = _parse_d(t_str)
        if end:
            end = end + timedelta(days=1)  # inclusive of the chosen end-date
    elif preset:
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if   preset == 'today':       start, end = today, today + timedelta(days=1)
        elif preset == 'yesterday':   start, end = today - timedelta(days=1), today
        elif preset == 'this_week':   start = today - timedelta(days=now.weekday()); end = start + timedelta(days=7)
        elif preset == 'last_week':   start = today - timedelta(days=now.weekday() + 7); end = start + timedelta(days=7)
        elif preset == 'this_month':  start = today.replace(day=1); end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        elif preset == 'last_month':
            first_this = today.replace(day=1)
            end = first_this
            start = (first_this - timedelta(days=1)).replace(day=1)
    else:
        # Backwards-compat 'period' param
        if   period == 'daily':   start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == 'weekly':  start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == 'monthly': start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # period == 'total' or anything else: no filter

    start_iso = start.isoformat() if start else None
    end_iso   = end.isoformat()   if end   else None

    def _fetch_all(table, columns, date_col=None):
        rows = []
        page_size = 1000
        offset = 0
        while True:
            q = db.table(table).select(columns)
            if start_iso and date_col:
                q = q.gte(date_col, start_iso)
            if end_iso and date_col:
                q = q.lt(date_col, end_iso)
            res = q.range(offset, offset + page_size - 1).execute()
            batch = res.data or []
            rows.extend(batch)
            if len(batch) < page_size: break
            offset += page_size
        return rows

    # ── Warm-lead funnel (split by contact_method)
    # Normalize legacy values so historic data slots into the new buckets.
    LEGACY_PS = {'nieuw': 'forum_nog_sturen', 'whatsapp': 'forum_nog_sturen', 'afgewezen': 'afgehaakt'}
    WARM_STAGES = ['forum_nog_sturen','forum_gestuurd','forum_gezien','forum_ingevuld','ik_bel_terug','zij_bellen_terug','afgehaakt','gesloten']
    CONTACT_METHODS = ('phone', 'whatsapp', 'extern')   # NEW: 'extern' is referral / network / DM
    # Demos now end at 'show' instead of geleverd+gezien (the demo is shown
    # during the Calendly meeting). 'no_show' is a parallel non-terminal bucket
    # (a no-show lead can be recovered via follow-up + re-schedule).
    DEMO_STAGES = ['moet_gebouwd','klaar','show','geclosed','aanbetaling','volledig_betaald']
    DEMO_PARALLEL_STAGES = ['no_show', 'afgehaakt']
    MEETING_STAGES = ['pending_link','link_sent','scheduled','show','no_show','no_show_followup','afgehaakt']

    warm_rows = _fetch_all('warm_leads', 'id,company_name,phone,contact_method,pipeline_status,dropoff_stage,created_at,added_by_id,closed_amount,commission_amount,status,closed_at,meeting_state,meeting_outcome,meeting_link_sent_at,meeting_scheduled_at,meeting_no_show_followup_at', 'created_at')
    if member_id:
        warm_rows = [r for r in warm_rows if str(r.get('added_by_id') or '') == member_id]
    # Normalize
    for r in warm_rows:
        ps = r.get('pipeline_status') or ''
        r['pipeline_status'] = LEGACY_PS.get(ps, ps)
        # Some rows may have null contact_method — treat as 'unknown'
        if r.get('contact_method') not in CONTACT_METHODS:
            r['contact_method'] = 'unknown'

    def _funnel_split(rows, stages, status_field):
        out = {m: {s: 0 for s in stages} for m in CONTACT_METHODS}
        out['all'] = {s: 0 for s in stages}
        for r in rows:
            s = r.get(status_field)
            if s not in stages: continue
            m = r.get('contact_method') or 'unknown'
            if m in CONTACT_METHODS:
                out[m][s] += 1
            out['all'][s] += 1
        for bucket in out.values():
            bucket['total'] = sum(bucket.values())
        return out

    warm_funnel = _funnel_split(warm_rows, WARM_STAGES, 'pipeline_status')

    # ── Demo funnel (clients)
    # geleverd/gezien/afspraak_bekijken are legacy stages from before demos
    # were shown live during Calendly meetings — they all collapse into 'show'.
    LEGACY_DS = {
        'demo_zonder_forum': 'klaar',
        'afspraak_bekijken': 'show',
        'geleverd':          'show',
        'gezien':            'show',
    }
    client_rows = _fetch_all('clients', 'id,name,demo_status,dropoff_stage,created_at,total_amount,commission_amount,meeting_state,meeting_outcome,meeting_link_sent_at,meeting_scheduled_at,meeting_no_show_followup_at,added_by_id', 'created_at')
    # Enrich with contact_method from the matching warm_lead (by name).
    # When member_id is set, warm_rows is already filtered, so clients
    # without a matching warm_lead get dropped from the member view.
    lead_method_by_name = {(r.get('company_name') or '').strip().lower(): (r.get('contact_method') or 'unknown')
                          for r in warm_rows}
    if member_id:
        client_rows = [c for c in client_rows if (c.get('name') or '').strip().lower() in lead_method_by_name]
    for r in client_rows:
        ds = r.get('demo_status') or ''
        r['demo_status'] = LEGACY_DS.get(ds, ds)
        r['contact_method'] = lead_method_by_name.get((r.get('name') or '').strip().lower(), 'unknown')
        # If a client has meeting_outcome='show' but demo_status is still
        # 'klaar' (legacy data not yet bumped), surface it under 'show' so the
        # funnel reflects reality.
        if r.get('meeting_outcome') == 'show' and r['demo_status'] in ('klaar', 'moet_gebouwd', ''):
            r['demo_status'] = 'show'
    demo_funnel = _funnel_split(client_rows, DEMO_STAGES, 'demo_status')

    # Parallel buckets: no_show + afgehaakt aren't part of the main monotonic
    # funnel but count toward dropoff metrics.
    def _parallel_bucket():
        b = {m: 0 for m in CONTACT_METHODS}; b['all'] = 0; return b
    demo_parallel = {'no_show': _parallel_bucket(), 'afgehaakt': _parallel_bucket()}
    for r in client_rows:
        m = r.get('contact_method') or 'unknown'
        if r.get('meeting_outcome') == 'no_show':
            demo_parallel['no_show']['all'] += 1
            if m in CONTACT_METHODS: demo_parallel['no_show'][m] += 1
        if r.get('demo_status') == 'afgehaakt':
            demo_parallel['afgehaakt']['all'] += 1
            if m in CONTACT_METHODS: demo_parallel['afgehaakt'][m] += 1

    # ── Meeting funnel (built over warm_leads + clients)
    # Each lead/client maps to ONE meeting stage based on its (meeting_state,
    # meeting_outcome) pair. show/no_show come from meeting_outcome so they
    # count once regardless of whether the row is still warm or already a client.
    def _meeting_stage(r):
        out = r.get('meeting_outcome')
        if out == 'show':    return 'show'
        if out == 'no_show': return 'no_show'
        st = r.get('meeting_state') or 'pending_link'
        if st in MEETING_STAGES: return st
        return 'pending_link'

    meeting_rows_for_funnel = list(warm_rows) + list(client_rows)
    # Annotate with a uniform 'meeting_stage' field for _funnel_split.
    for r in meeting_rows_for_funnel:
        r['_meeting_stage'] = _meeting_stage(r)
    meeting_funnel = _funnel_split(meeting_rows_for_funnel, MEETING_STAGES, '_meeting_stage')

    # ── Bridges (split): forum_ingevuld → meeting ingepland → show
    def _bridge_counts(stage_a_count, stage_b_count):
        conv = (stage_b_count / stage_a_count * 100) if stage_a_count else 0.0
        loss = max(0, stage_a_count - stage_b_count)
        return {'a': stage_a_count, 'b': stage_b_count, 'conv_pct': round(conv, 1), 'loss': loss}

    def _bridge_for_bucket(bucket):
        wf = warm_funnel.get(bucket, {}) or {}
        mf = meeting_funnel.get(bucket, {}) or {}
        # Forum ingevuld cumulative: leads in forum_ingevuld PLUS anyone past it
        # in the meeting funnel (link_sent/scheduled/show/no_show/no_show_followup).
        forum_filled_cum = int(wf.get('forum_ingevuld', 0)) + sum(int(mf.get(k, 0)) for k in ('link_sent','scheduled','show','no_show','no_show_followup'))
        meeting_planned_cum = sum(int(mf.get(k, 0)) for k in ('scheduled','show','no_show','no_show_followup'))
        shows = int(mf.get('show', 0))
        return {
            'forum_to_meeting': _bridge_counts(forum_filled_cum, meeting_planned_cum),
            'meeting_to_show':  _bridge_counts(meeting_planned_cum, shows),
        }
    bridges = {k: _bridge_for_bucket(k) for k in (*CONTACT_METHODS, 'all')}

    # ── Source conversion — prospects benaderd → became warm lead → became close
    # (the same prospect_rows feeds both the chart-1 entry conversion and the
    #  chart-2 close-conversion).
    prospect_rows = _fetch_all('prospect_list', 'id,company_name,phone,called,contact_method,called_at,called_by_id', 'called_at')
    if member_id:
        prospect_rows = [p for p in prospect_rows if str(p.get('called_by_id') or '') == member_id]
    norm = lambda s: ''.join(c for c in str(s or '') if c.isdigit())
    warm_phones = {norm(r.get('phone')) for r in warm_rows if r.get('phone')}
    warm_names  = {(r.get('company_name') or '').strip().lower() for r in warm_rows if r.get('company_name')}
    # Names of warm leads that reached the chosen close stage (configurable)
    close_names = {(r.get('name') or '').strip().lower()
                   for r in client_rows if r.get('demo_status') == close_status}
    source = {m: {'benaderd': 0, 'warm_leads': 0, 'closes': 0, 'meeting_scheduled': 0, 'shows': 0}
              for m in CONTACT_METHODS}
    # Names of leads/clients that ever reached scheduled-or-later in the meeting
    # funnel, and those that hit 'show'. Channel resolves from contact_method.
    meeting_scheduled_names_by_ch = {m: set() for m in CONTACT_METHODS}
    show_names_by_ch              = {m: set() for m in CONTACT_METHODS}
    for r in meeting_rows_for_funnel:
        ch = r.get('contact_method')
        if ch not in CONTACT_METHODS: continue
        nm = (r.get('company_name') or r.get('name') or '').strip().lower()
        if not nm: continue
        st = r.get('_meeting_stage')
        if st in ('scheduled','show','no_show','no_show_followup'):
            meeting_scheduled_names_by_ch[ch].add(nm)
        if st == 'show':
            show_names_by_ch[ch].add(nm)

    # ── Extern leads don't appear in prospect_list — they're added directly
    # to warm_leads via the lead-add modal. Count them straight off warm_rows
    # so the 'extern' bucket in source_conversion isn't artificially empty.
    extern_warm_names = set()
    for r in warm_rows:
        if r.get('contact_method') != 'extern': continue
        nm = (r.get('company_name') or '').strip().lower()
        if not nm: continue
        extern_warm_names.add(nm)
        source['extern']['benaderd']  += 1   # extern leads are "benaderd" by definition (you reached out)
        source['extern']['warm_leads'] += 1   # every extern row IS a warm lead
        if nm in close_names:
            source['extern']['closes'] += 1
        if nm in meeting_scheduled_names_by_ch['extern']:
            source['extern']['meeting_scheduled'] += 1
        if nm in show_names_by_ch['extern']:
            source['extern']['shows'] += 1

    for p in prospect_rows:
        if not p.get('called'): continue
        m = p.get('contact_method')
        if m not in CONTACT_METHODS: continue
        source[m]['benaderd'] += 1
        ph = norm(p.get('phone'))
        nm = (p.get('company_name') or '').strip().lower()
        if (ph and ph in warm_phones) or (nm and nm in warm_names):
            source[m]['warm_leads'] += 1
            if nm and nm in close_names:
                source[m]['closes'] += 1
            if nm and nm in meeting_scheduled_names_by_ch[m]:
                source[m]['meeting_scheduled'] += 1
            if nm and nm in show_names_by_ch[m]:
                source[m]['shows'] += 1
    for bucket in source.values():
        bucket['benaderd_to_warm_pct'] = round((bucket['warm_leads'] / bucket['benaderd'] * 100), 1) if bucket['benaderd'] else 0.0
        bucket['warm_to_close_pct']    = round((bucket['closes']     / bucket['warm_leads'] * 100), 1) if bucket['warm_leads'] else 0.0
        bucket['warm_to_meeting_pct']  = round((bucket['meeting_scheduled'] / bucket['warm_leads'] * 100), 1) if bucket['warm_leads'] else 0.0
        bucket['meeting_to_show_pct']  = round((bucket['shows'] / bucket['meeting_scheduled'] * 100), 1) if bucket['meeting_scheduled'] else 0.0
        # Backwards-compat alias still consumed by old frontends
        bucket['conversion_pct'] = bucket['benaderd_to_warm_pct']

    # ── Drop-off analyse: where did 'afgehaakt' rows come from?
    def _dropoff(rows, status_field, stages):
        out = {'all': {s: 0 for s in stages}, 'unknown': 0}
        for m in CONTACT_METHODS:
            out[m] = {s: 0 for s in stages}
        for r in rows:
            if r.get(status_field) != 'afgehaakt': continue
            ds = r.get('dropoff_stage')
            m = r.get('contact_method') or 'unknown'
            if ds in stages:
                out['all'][ds] += 1
                if m in CONTACT_METHODS:
                    out[m][ds] += 1
            else:
                out['unknown'] += 1
        return out

    dropoff_warm  = _dropoff(warm_rows, 'pipeline_status', WARM_STAGES)
    # For demos, the dropoff_stage values from legacy rows could be old stage
    # names (geleverd/gezien); keep them visible by including in the stage list
    # used here only (the funnel itself already collapsed them via LEGACY_DS).
    DEMO_DROPOFF_STAGES = DEMO_STAGES + ['geleverd', 'gezien']
    dropoff_demos = _dropoff(client_rows, 'demo_status', DEMO_DROPOFF_STAGES)

    # ── Extra dropoff buckets specific to the meeting flow:
    # - meeting_link_pending: forum_ingevuld leads with pending_link state, >7 days old
    # - meeting_no_show_final: no_show without any follow-up, >14 days old
    from datetime import timedelta as _td
    now_dt = datetime.now(timezone.utc)
    cutoff_link    = (now_dt - _td(days=7)).isoformat()
    cutoff_no_show = (now_dt - _td(days=14)).isoformat()
    meeting_extra = {'all': {'meeting_link_pending': 0, 'meeting_no_show_final': 0}}
    for m in CONTACT_METHODS:
        meeting_extra[m] = {'meeting_link_pending': 0, 'meeting_no_show_final': 0}
    for r in warm_rows + client_rows:
        ch = r.get('contact_method') if r.get('contact_method') in CONTACT_METHODS else None
        st = r.get('meeting_state') or 'pending_link'
        out = r.get('meeting_outcome')
        followup_at = r.get('meeting_no_show_followup_at')
        created_at  = r.get('created_at') or ''
        if (r.get('pipeline_status') == 'forum_ingevuld'
                and st == 'pending_link'
                and created_at and created_at < cutoff_link):
            meeting_extra['all']['meeting_link_pending'] += 1
            if ch: meeting_extra[ch]['meeting_link_pending'] += 1
        if out == 'no_show' and not followup_at and created_at and created_at < cutoff_no_show:
            meeting_extra['all']['meeting_no_show_final'] += 1
            if ch: meeting_extra[ch]['meeting_no_show_final'] += 1

    # ── Per-member revenue + commission within the period
    # Sum from warm_leads (closed_amount + commission_amount) since revenue lives there;
    # only counts deals that closed inside the requested window. When member_id is set
    # we already filtered warm_rows; otherwise we sum over the whole team.
    member_stats = None
    if member_id or True:                                      # always return — frontend may
        revenue, commission = 0.0, 0.0                          # show team totals if no member
        for r in warm_rows:
            if r.get('status') != 'closed': continue
            # Optional second-pass date filter on closed_at so revenue lines up with
            # the chosen window even when warm_leads.created_at was outside it
            if start_iso and r.get('closed_at') and r['closed_at'] < start_iso: continue
            if end_iso   and r.get('closed_at') and r['closed_at'] >= end_iso:  continue
            try:    revenue    += float(r.get('closed_amount')     or 0)
            except (ValueError, TypeError): pass
            try:    commission += float(r.get('commission_amount') or 0)
            except (ValueError, TypeError): pass
        # Meeting metrics for the current scope (member_id or team)
        scheduled_count = sum(1 for r in warm_rows + client_rows
                               if (r.get('meeting_state') in ('scheduled','no_show_followup'))
                               or (r.get('meeting_outcome') in ('show','no_show')))
        shows_count     = sum(1 for r in warm_rows + client_rows if r.get('meeting_outcome') == 'show')
        no_shows_count  = sum(1 for r in warm_rows + client_rows if r.get('meeting_outcome') == 'no_show')
        forum_filled_total = sum(1 for r in warm_rows if r.get('pipeline_status') == 'forum_ingevuld') \
                            + scheduled_count  # cumulative: anyone who advanced past forum_ingevuld
        member_stats = {
            'member_id':  member_id or None,
            'revenue':    round(revenue, 2),
            'commission': round(commission, 2),
            'closes':     sum(1 for r in warm_rows if r.get('status') == 'closed'),
            'meeting_scheduled': scheduled_count,
            'shows':             shows_count,
            'no_shows':          no_shows_count,
            'schedule_rate':     round(scheduled_count / forum_filled_total * 100, 1) if forum_filled_total else 0.0,
            'show_rate':         round(shows_count     / scheduled_count    * 100, 1) if scheduled_count    else 0.0,
        }

    # ── Roster of sales members for the dropdown (always returned)
    roster = []
    try:
        m_res = db.table('sales_members').select('id,name,status').eq('status', 'active').order('name').execute()
        for m in (m_res.data or []):
            roster.append({'id': str(m['id']), 'name': m.get('name') or '—'})
    except Exception as e:
        print(f'[KPI] roster fetch failed: {e}')

    # ── Active strategies during the period
    active_strategies = []
    try:
        strat_q = db.table('wa_strategies').select('*').order('started_at', desc=False)
        strat_res = strat_q.execute()
        for s in (strat_res.data or []):
            s_start = s.get('started_at') or ''
            s_end   = s.get('ended_at')   or ''  # null/empty = still active
            # Overlap test: [s_start, s_end) intersects [start_iso, end_iso)
            if start_iso and s_end and s_end <= start_iso:  continue
            if end_iso   and s_start and s_start >= end_iso: continue
            active_strategies.append({
                'id': s.get('id'),
                'name': s.get('name'),
                'started_at': s_start,
                'ended_at': s_end or None,
            })
    except Exception as e:
        print(f'[KPI] strategies fetch failed: {e}')

    return jsonify({
        'period': period,
        'from':   start_iso,
        'to':     end_iso,
        'member_id':    member_id or None,
        'close_status': close_status,
        'warm_leads_funnel': warm_funnel,
        'demo_funnel':       demo_funnel,
        'demo_parallel':     demo_parallel,
        'meeting_funnel':    meeting_funnel,
        'bridges':           bridges,
        'source_conversion': source,
        'dropoff':           {'warm_leads': dropoff_warm, 'demos': dropoff_demos, 'meeting_extra': meeting_extra},
        'member_stats':      member_stats,
        'roster':            roster,
        'active_strategies': active_strategies,
        'stage_labels': {
            'warm_leads': {
                'forum_nog_sturen':'Forum nog sturen',
                'forum_gestuurd':'Forum gestuurd', 'forum_gezien':'Forum gezien', 'forum_ingevuld':'Forum ingevuld',
                'ik_bel_terug':'Ik bel terug', 'zij_bellen_terug':'Zij bellen terug',
                'afgehaakt':'Afgehaakt', 'gesloten':'Gesloten',
            },
            'demos': {
                'moet_gebouwd':'Moet gebouwd', 'klaar':'Demo klaar', 'show':'Show op meeting',
                'geclosed':'Geclosed', 'aanbetaling':'Aanbetaling',
                'volledig_betaald':'Volledig betaald',
                'no_show':'No-show', 'afgehaakt':'Afgehaakt',
                # Legacy labels kept so historic charts don't show raw keys
                'geleverd':'Show op meeting', 'gezien':'Show op meeting',
            },
            'meeting': {
                'pending_link':'Link nog sturen', 'link_sent':'Link gestuurd',
                'scheduled':'Meeting ingepland', 'show':'Show', 'no_show':'No-show',
                'no_show_followup':'No-show opgevolgd', 'afgehaakt':'Afgehaakt',
            },
            'meeting_extra': {
                'meeting_link_pending':'Link blijven liggen (>7d)',
                'meeting_no_show_final':'No-show zonder follow-up (>14d)',
            },
        },
    })


@app.route('/api/sales/top-earners', methods=['GET'])
@require_sales_auth
def sales_top_earners():
    from datetime import timezone, timedelta
    now = datetime.now(timezone.utc)
    periods = {
        'total':   None,
        'monthly': (now - timedelta(days=30)).isoformat(),
        'weekly':  (now - timedelta(weeks=1)).isoformat(),
        'daily':   (now - timedelta(days=1)).isoformat(),
    }
    result = {}
    for period, cutoff in periods.items():
        q_closed   = db.table('warm_leads').select('added_by_id,added_by_name,closed_amount,commission_amount').eq('status', 'closed')
        q_all      = db.table('warm_leads').select('added_by_id,added_by_name,pipeline_status,status')
        q_prospect = db.table('prospect_list').select('called_by_id').eq('called', True)
        if cutoff:
            q_closed   = q_closed.gte('closed_at', cutoff)
            q_prospect = q_prospect.gte('called_at', cutoff)
        closed_res   = q_closed.execute()
        prospect_res = q_prospect.execute()
        totals = {}
        for r in closed_res.data:
            mid  = r['added_by_id']
            name = r['added_by_name'] or 'Onbekend'
            totals.setdefault(mid, {'name': name, 'revenue': 0, 'commission': 0, 'closes': 0, 'called_leads': 0})
            totals[mid]['revenue']    += float(r['closed_amount'] or 0)
            totals[mid]['commission'] += float(r['commission_amount'] or 0)
            totals[mid]['closes']     += 1
        for r in prospect_res.data:
            mid = r.get('called_by_id')
            if mid:
                totals.setdefault(mid, {'name': mid, 'revenue': 0, 'commission': 0, 'closes': 0, 'called_leads': 0})
                totals[mid]['called_leads'] += 1
        sorted_earners = sorted(totals.values(), key=lambda x: x['revenue'], reverse=True)[:3]
        result[period] = sorted_earners
    return jsonify(result)

@app.route('/api/sales/all-earners', methods=['GET'])
@require_sales_auth
def sales_all_earners():
    closed_res   = db.table('warm_leads').select('added_by_id,added_by_name,closed_amount,commission_amount').eq('status', 'closed').execute()
    members_res  = db.table('sales_members').select('id,name,last_login').eq('status', 'active').execute()
    prospect_res = db.table('prospect_list').select('called_by_id,called_by_name').eq('called', True).execute()
    member_logins = {m['id']: m.get('last_login') for m in members_res.data}
    totals = {}
    for r in closed_res.data:
        mid  = r['added_by_id']
        name = r['added_by_name'] or 'Onbekend'
        totals.setdefault(mid, {'name': name, 'revenue': 0, 'commission': 0, 'closes': 0, 'called_leads': 0, 'last_login': member_logins.get(mid)})
        totals[mid]['revenue']    += float(r['closed_amount'] or 0)
        totals[mid]['commission'] += float(r['commission_amount'] or 0)
        totals[mid]['closes']     += 1
    for m in members_res.data:
        totals.setdefault(m['id'], {'name': m['name'], 'revenue': 0, 'commission': 0, 'closes': 0, 'called_leads': 0, 'last_login': m.get('last_login')})
    for r in prospect_res.data:
        mid  = r.get('called_by_id')
        name = r.get('called_by_name') or 'Onbekend'
        if mid:
            totals.setdefault(mid, {'name': name, 'revenue': 0, 'commission': 0, 'closes': 0, 'called_leads': 0, 'last_login': member_logins.get(mid)})
            totals[mid]['called_leads'] += 1
    sorted_all = sorted(totals.values(), key=lambda x: x['revenue'], reverse=True)
    total_commission = sum(v['commission'] for v in totals.values())
    return jsonify({'members': sorted_all, 'total_commission': total_commission})

@app.route('/api/sales/leads-by-member', methods=['GET'])
@require_sales_auth
def sales_leads_by_member():
    from datetime import timezone, timedelta
    now = datetime.now(timezone.utc)
    periods = {
        'total':   None,
        'monthly': (now - timedelta(days=30)).isoformat(),
        'weekly':  (now - timedelta(weeks=1)).isoformat(),
        'daily':   (now - timedelta(days=1)).isoformat(),
    }
    result = {}
    for period, cutoff in periods.items():
        q = db.table('warm_leads').select('added_by_id,added_by_name')
        if cutoff:
            q = q.gte('created_at', cutoff)
        res = q.execute()
        counts = {}
        for r in res.data:
            mid  = r['added_by_id']
            name = r['added_by_name'] or 'Onbekend'
            counts.setdefault(mid, {'name': name, 'count': 0})
            counts[mid]['count'] += 1
        result[period] = sorted(counts.values(), key=lambda x: x['count'], reverse=True)
    return jsonify(result)

@app.route('/api/sales/leads', methods=['GET'])
@require_sales_auth
def list_sales_leads():
    res = db.table('warm_leads').select('*').order('created_at', desc=True).execute()
    leads = res.data or []
    # WA-pogingen per lead
    try:
        wa_res = db.table('wa_outreach_log').select('lead_id').eq('source', 'lead').execute()
        wa_counts = {}
        for r in (wa_res.data or []):
            lid = r.get('lead_id')
            if lid:
                wa_counts[str(lid)] = wa_counts.get(str(lid), 0) + 1
        for l in leads:
            l['wa_count'] = wa_counts.get(str(l['id']), 0)
    except Exception as e:
        print(f'[LEADS] wa_count failed: {e}')
    return jsonify(leads)

def _log_activity(mid, member_name, atype, description):
    try:
        db.table('activity_feed').insert({
            'member_id': str(mid), 'member_name': member_name,
            'type': atype, 'description': description,
        }).execute()
    except Exception as e:
        print(f'[ACTIVITY] {e}')


@app.route('/api/sales/leads', methods=['POST'])
@require_sales_auth
def add_sales_lead():
    data         = request.get_json(silent=True) or {}
    company_name = (data.get('company_name') or '').strip()
    phone        = (data.get('phone') or '').strip()
    maps_url     = (data.get('maps_url') or '').strip()
    added_by_id  = (data.get('added_by_id') or '').strip()
    added_by_name= (data.get('added_by_name') or '').strip()
    if not company_name or not added_by_id:
        return jsonify({'success': False, 'error': 'Bedrijfsnaam en lid zijn verplicht.'}), 400
    lid = str(int(datetime.utcnow().timestamp() * 1000))
    lead_score = data.get('lead_score')
    row = {
        'id': lid, 'company_name': company_name, 'phone': phone,
        'maps_url': maps_url, 'added_by_id': added_by_id,
        'added_by_name': added_by_name, 'status': 'warm',
        'pipeline_status': 'forum_nog_sturen', 'closed_amount': None, 'closed_at': None,
    }
    if lead_score:
        try:
            row['lead_score'] = int(lead_score)
        except (ValueError, TypeError):
            pass
    # Inherit contact_method from prospect if provided, and lock rate immediately.
    # Rate rules:
    #   - 'whatsapp' → member's current WA-tier rate (via _compute_whatsapp_rate).
    #   - 'extern'   → fixed 0.40 (external/referral leads always 40%).
    #   - 'phone'    → no lock at create-time, falls back to _get_effective_rate
    #                  at close (which for legacy contracts is 40% and for new
    #                  contracts depends on tier).
    from_method = data.get('contact_method')
    if from_method in ('phone', 'whatsapp', 'extern'):
        row['contact_method'] = from_method
        if from_method == 'whatsapp':
            try:
                row['commission_rate_locked'] = _compute_whatsapp_rate(added_by_id)
            except Exception as e:
                print(f"[LEAD INSERT] lock WA rate failed: {e}")
        elif from_method == 'extern':
            row['commission_rate_locked'] = 0.40
    try:
        db.table('warm_leads').insert(row).execute()
    except Exception as e:
        print(f"[LEAD INSERT ERROR] {e}")
        return jsonify({'success': False, 'error': f'Database fout: {str(e)}'}), 500
    _log_activity(added_by_id, added_by_name, 'lead_added', f'voegde {company_name} toe als warm lead')
    print(f"[LEAD] {company_name} | by {added_by_name}")
    return jsonify({'success': True, 'id': lid})

@app.route('/api/sales/leads/<lid>/close', methods=['PUT'])
@require_sales_auth
def close_sales_lead(lid):
    data   = request.get_json(silent=True) or {}
    amount = data.get('amount')
    if amount is None:
        return jsonify({'success': False, 'error': 'Bedrag is verplicht.'}), 400
    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Ongeldig bedrag.'}), 400

    res = db.table('warm_leads').select('*').eq('id', lid).limit(1).execute()
    if not res.data:
        return jsonify({'success': False, 'error': 'Lead niet gevonden.'}), 404
    lead = res.data[0]
    locked = lead.get('commission_rate_locked')
    if locked is not None:
        rate = float(locked)
    else:
        member_for_rate = db.table('sales_members').select('id,name,email,contract_type,commission_override').eq('id', lead['added_by_id']).limit(1).execute()
        rate = _get_effective_rate(member_for_rate.data[0]) if member_for_rate.data else 0.40
    commission = round(amount * rate, 2)
    db.table('warm_leads').update({
        'status': 'closed', 'pipeline_status': 'gesloten',
        'closed_amount': amount, 'commission_amount': commission,
        'closed_at': datetime.utcnow().isoformat(),
    }).eq('id', lid).execute()

    # check first-sale bonus for referrer
    member_id = lead['added_by_id']
    member_res = db.table('sales_members').select('*').eq('id', member_id).limit(1).execute()
    if member_res.data:
        member = member_res.data[0]
        if not member.get('first_sale_counted'):
            db.table('sales_members').update({'first_sale_counted': True}).eq('id', member_id).execute()
            ref_code = member.get('referred_by_code')
            if ref_code:
                ref_res = db.table('sales_members').select('bonus_owed').eq('ref_code', ref_code).limit(1).execute()
                if ref_res.data:
                    old_bonus = float(ref_res.data[0].get('bonus_owed') or 0)
                    db.table('sales_members').update({'bonus_owed': old_bonus + 20}).eq('ref_code', ref_code).execute()

    print(f"[CLOSE] Lead {lid} closed at €{amount}")
    return jsonify({'success': True})

def _insert_wa_outreach_log(entry, phone_line):
    """Insert a WA outreach log row, including phone_line. Falls back to
    inserting without phone_line if the column doesn't exist yet."""
    payload = dict(entry)
    payload['phone_line'] = phone_line
    try:
        db.table('wa_outreach_log').insert(payload).execute()
        return
    except Exception as e:
        msg = str(e).lower()
        if 'phone_line' not in msg and 'column' not in msg and 'schema' not in msg:
            print(f"[WA-OUTREACH] log insert failed: {e}")
    # Fallback: column missing — insert without it so feature still works
    try:
        payload.pop('phone_line', None)
        db.table('wa_outreach_log').insert(payload).execute()
    except Exception as e:
        print(f"[WA-OUTREACH] log insert fallback failed: {e}")


@app.route('/api/sales/leads/<lid>/wa-outreach', methods=['POST'])
@require_sales_auth
def log_lead_wa_outreach(lid):
    mid = _get_sales_member_id()
    if not mid:
        return jsonify({'success': False, 'error': 'Niet ingelogd.'}), 401

    lead_res = db.table('warm_leads').select('id,added_by_id,phone,pipeline_status,commission_rate_locked').eq('id', lid).limit(1).execute()
    if not lead_res.data:
        return jsonify({'success': False, 'error': 'Lead niet gevonden.'}), 404
    lead = lead_res.data[0]
    if str(lead.get('added_by_id')) != str(mid):
        return jsonify({'success': False, 'error': 'Niet jouw lead.'}), 403

    # WA outreach happened → lock the member's CURRENT WA-tier rate to this
    # lead. We use _compute_whatsapp_rate() directly so a legacy-contract
    # member still earns the WA tier on WA-acquired leads (never the 40%).
    current_rate = _compute_whatsapp_rate(mid)

    body = request.get_json(silent=True) or {}
    phone_line = body.get('phone_line') if body.get('phone_line') in ('business', 'personal') else 'business'
    _insert_wa_outreach_log({
        'member_id': str(mid),
        'lead_id': str(lid),
        'phone': lead.get('phone') or '',
        'source': 'lead',
    }, phone_line)

    update = {'contact_method': 'whatsapp'}
    # Always (re-)lock the rate to the WA tier — the column should never
    # show the legacy 40% for a lead that came in via WhatsApp.
    locked_now = lead.get('commission_rate_locked')
    if locked_now is None or float(locked_now) >= 0.39:
        # Either never locked, or locked at the legacy 40% by accident — fix it.
        update['commission_rate_locked'] = current_rate
    if lead.get('pipeline_status') in (None, 'nieuw', 'whatsapp', 'forum_nog_sturen'):
        update['pipeline_status'] = 'forum_gestuurd'
    try:
        db.table('warm_leads').update(update).eq('id', lid).execute()
    except Exception as e:
        print(f"[WA-OUTREACH] lead update failed: {e}")

    locked_rate = lead.get('commission_rate_locked')
    if locked_rate is None:
        locked_rate = current_rate
    return jsonify({'success': True, 'rate': float(locked_rate), 'pipeline_status': update.get('pipeline_status', lead.get('pipeline_status'))})


@app.route('/api/sales/prospects/<pid>/wa-outreach', methods=['POST'])
@require_sales_auth
def log_prospect_wa_outreach(pid):
    mid = _get_sales_member_id()
    if not mid:
        return jsonify({'success': False, 'error': 'Niet ingelogd.'}), 401

    res = db.table('sales_members').select('name').eq('id', mid).limit(1).execute()
    member_name = res.data[0]['name'] if res.data else 'Onbekend'

    prospect_res = db.table('prospect_list').select('id,phone,called').eq('id', pid).limit(1).execute()
    prospect = prospect_res.data[0] if prospect_res.data else None
    phone = prospect.get('phone') if prospect else ''

    body = request.get_json(silent=True) or {}
    phone_line = body.get('phone_line') if body.get('phone_line') in ('business', 'personal') else 'business'
    _insert_wa_outreach_log({
        'member_id': str(mid),
        'lead_id': None,
        'phone': phone or '',
        'source': 'prospect',
    }, phone_line)

    called = bool(prospect and prospect.get('called'))
    if prospect and not called:
        update_data = {
            'called': True,
            'called_by_id': str(mid),
            'called_by_name': member_name,
            'called_at': datetime.utcnow().isoformat(),
        }
        try:
            db.table('prospect_list').update({**update_data, 'contact_method': 'whatsapp'}).eq('id', pid).execute()
            called = True
        except Exception:
            try:
                db.table('prospect_list').update(update_data).eq('id', pid).execute()
                called = True
            except Exception as e:
                print(f"[WA-OUTREACH] mark called failed: {e}")
    elif prospect and called and (prospect.get('contact_method') in (None, 'phone')):
        # If already marked called via phone, upgrade to whatsapp on WA click
        try:
            db.table('prospect_list').update({'contact_method': 'whatsapp'}).eq('id', pid).execute()
        except Exception:
            pass
    return jsonify({'success': True, 'called': called, 'called_by_name': member_name, 'contact_method': 'whatsapp'})


@app.route('/api/sales/clients/<cid>/wa-outreach', methods=['POST'])
@require_sales_auth
def log_client_wa_outreach(cid):
    mid = _get_sales_member_id()
    if not mid:
        return jsonify({'success': False, 'error': 'Niet ingelogd.'}), 401
    client_res = db.table('clients').select('id,phone').eq('id', cid).limit(1).execute()
    phone = client_res.data[0].get('phone') if client_res.data else ''
    body = request.get_json(silent=True) or {}
    phone_line = body.get('phone_line') if body.get('phone_line') in ('business', 'personal') else 'business'
    _insert_wa_outreach_log({
        'member_id': str(mid),
        'lead_id': None,
        'phone': phone or '',
        'source': 'client',
    }, phone_line)
    return jsonify({'success': True})


@app.route('/api/sales/whatsapp-stats', methods=['GET'])
@require_sales_auth
def sales_whatsapp_stats():
    mid = _get_sales_member_id()
    if not mid:
        return jsonify({'success': False, 'error': 'Niet ingelogd.'}), 401

    member_res = db.table('sales_members').select('id,name,contract_type,commission_override').eq('id', mid).limit(1).execute()
    member = member_res.data[0] if member_res.data else {}
    contract_type = member.get('contract_type') or 'legacy'
    member_name = member.get('name') or 'Onbekend'

    s = _compute_whatsapp_state(mid)
    rate = s['rate']
    state = s['state']
    streak = s['streak_days']
    daily_counts = s['daily_counts'] or {}

    # Lazy streak-break / milestone logging
    _maybe_log_streak_break(mid, member_name, streak)

    today = datetime.now(timezone.utc).date()
    history = []
    for i in range(29, -1, -1):
        d = today - timedelta(days=i)
        cnt = daily_counts.get(d.isoformat(), 0)
        history.append({'date': d.isoformat(), 'count': cnt, 'active': cnt >= WA_DAILY_MINIMUM})

    hours_since_last = s['hours_since_last']
    hours_until_penalty = None
    if hours_since_last is not None:
        hours_until_penalty = max(0.0, WA_PENALTY_HOURS - hours_since_last)

    if state == 'penalty' or state == 'recovering':
        next_tier_rate, days_to_next = 0.25, max(0, WA_RECOVERY_DAYS - streak)
    elif state == 'base':
        next_tier_rate, days_to_next = 0.30, max(0, WA_BONUS_DAYS - streak)
    else:  # bonus
        next_tier_rate, days_to_next = None, 0

    # Average deal value for this member (last 90 days), used for €-impact
    avg_deal_value = None
    try:
        cutoff_90 = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        deals_res = db.table('warm_leads').select('closed_amount').eq('added_by_id', str(mid)).eq('status', 'closed').gte('closed_at', cutoff_90).execute()
        amounts = [float(r['closed_amount'] or 0) for r in (deals_res.data or []) if r.get('closed_amount')]
        if amounts:
            avg_deal_value = round(sum(amounts) / len(amounts), 2)
    except Exception as e:
        print(f'[WA-STATS] avg deal failed: {e}')
    # Fallback to team average if member has no deals yet
    if not avg_deal_value:
        try:
            cutoff_90 = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
            team_deals = db.table('warm_leads').select('closed_amount').eq('status', 'closed').gte('closed_at', cutoff_90).execute()
            tamts = [float(r['closed_amount'] or 0) for r in (team_deals.data or []) if r.get('closed_amount')]
            if tamts:
                avg_deal_value = round(sum(tamts) / len(tamts), 2)
        except Exception:
            pass
    if not avg_deal_value:
        avg_deal_value = 2000.0  # safe default

    # Team-wide leaderboard + tier breakdown (only members on WhatsApp contract)
    leaderboard = []
    tier_breakdown = {'penalty': 0, 'recovering': 0, 'base': 0, 'bonus': 0}
    longest_team_streak = {'name': None, 'streak': 0}
    try:
        wa_members_res = db.table('sales_members').select('id,name,contract_type,commission_override').eq('status', 'active').execute()
        for m in (wa_members_res.data or []):
            if (m.get('contract_type') or 'legacy') != 'whatsapp':
                continue
            ms = _compute_whatsapp_state(m['id'])
            tier_breakdown[ms.get('state') or 'base'] = tier_breakdown.get(ms.get('state') or 'base', 0) + 1
            leaderboard.append({
                'member_id': m['id'],
                'name': m['name'],
                'streak': ms['streak_days'],
                'rate': ms['rate'],
                'state': ms['state'],
                'is_me': str(m['id']) == str(mid),
            })
            if ms['longest_streak_ever'] > longest_team_streak['streak']:
                longest_team_streak = {'name': m['name'], 'streak': ms['longest_streak_ever']}
    except Exception as e:
        print(f'[WA-STATS] leaderboard failed: {e}')
    leaderboard.sort(key=lambda x: x['streak'], reverse=True)

    # Total WA messages sent today (all sources) — for the 40/day ban-risk cap.
    # Split per phone_line so the cap applies independently to business vs personal number.
    today_total_wa     = 0
    today_wa_business  = 0
    today_wa_personal  = 0
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    try:
        rows_res = db.table('wa_outreach_log').select('id,phone_line').eq('member_id', str(mid)).gte('created_at', today_start).execute()
        rows = rows_res.data or []
        today_total_wa = len(rows)
        for r in rows:
            line = (r.get('phone_line') or 'business')
            if line == 'personal':
                today_wa_personal += 1
            else:
                today_wa_business += 1
    except Exception as e:
        # Fallback: phone_line column may not exist yet — treat all as business
        try:
            total_res = db.table('wa_outreach_log').select('id', count='exact').eq('member_id', str(mid)).gte('created_at', today_start).execute()
            today_total_wa = total_res.count or 0
            today_wa_business = today_total_wa
        except Exception as e2:
            print(f'[WA-STATS] daily total failed: {e2}')

    return jsonify({
        'current_rate': rate,
        'state': state,
        'streak_days': streak,
        'streak_raw': s.get('streak_raw'),
        'longest_streak_ever': s.get('longest_streak_ever', 0),
        'today_count': s['today_count'],
        'today_total_wa': today_total_wa,
        'today_wa_business': today_wa_business,
        'today_wa_personal': today_wa_personal,
        'wa_daily_cap': 40,
        'yesterday_count': s.get('yesterday_count', 0),
        'today_required': WA_DAILY_MINIMUM,
        'hours_since_last': hours_since_last,
        'hours_until_penalty': hours_until_penalty,
        'days_to_next_tier': days_to_next,
        'next_tier_rate': next_tier_rate,
        'contract_type': contract_type,
        'applies_to_contract': contract_type == 'whatsapp' and member.get('commission_override') is None,
        'insurance_available': s.get('insurance_available', True),
        'insurance_in_use': s.get('insurance_in_use', False),
        'avg_deal_value': avg_deal_value,
        'leaderboard': leaderboard[:8],
        'tier_breakdown': tier_breakdown,
        'longest_team_streak': longest_team_streak,
        'history_30d': history,
    })


@app.route('/api/sales/leads/<lid>/pipeline', methods=['PUT'])
@require_sales_auth
def update_lead_pipeline(lid):
    data   = request.get_json(silent=True) or {}
    status = data.get('pipeline_status')
    valid  = ('forum_nog_sturen','forum_gestuurd','forum_gezien','forum_ingevuld','ik_bel_terug','zij_bellen_terug','afgehaakt',
              # Legacy values kept valid so historic rows keep working until migrated:
              'nieuw','whatsapp','afgewezen','geinteresseerd','gesloten')
    if status not in valid:
        return jsonify({'success': False, 'error': 'Ongeldige status.'}), 400

    # Read old status once so we can log the transition + capture dropoff
    old_status = None
    try:
        old = db.table('warm_leads').select('pipeline_status').eq('id', lid).limit(1).execute()
        if old.data:
            old_status = old.data[0].get('pipeline_status')
    except Exception as e:
        print(f'[PIPELINE] old status read failed: {e}')

    update = {'pipeline_status': status}
    if status == 'afgehaakt' and old_status and old_status != 'afgehaakt':
        update['dropoff_stage'] = old_status
    elif status != 'afgehaakt':
        update['dropoff_stage'] = None

    try:
        db.table('warm_leads').update(update).eq('id', lid).execute()
    except Exception:
        update.pop('dropoff_stage', None)
        db.table('warm_leads').update(update).eq('id', lid).execute()

    # Log to status_history audit table
    mid = _get_sales_member_id()
    mname = None
    try:
        mres = db.table('sales_members').select('name').eq('id', mid).limit(1).execute() if mid else None
        mname = mres.data[0]['name'] if (mres and mres.data) else None
    except Exception:
        pass
    _log_status_change('warm_lead', lid, old_status, status, mid=mid, member_name=mname)
    return jsonify({'success': True})

@app.route('/api/sales/leads/<lid>/notes', methods=['PUT'])
@require_sales_auth
def update_lead_notes(lid):
    data  = request.get_json(silent=True) or {}
    notes = data.get('notes', '')
    db.table('warm_leads').update({'notes': notes}).eq('id', lid).execute()
    return jsonify({'success': True})

@app.route('/api/sales/clients/<cid>/notes', methods=['PUT'])
@require_sales_auth
def update_client_notes(cid):
    data  = request.get_json(silent=True) or {}
    notes = data.get('notes', '')
    try:
        db.table('clients').update({'notes': notes}).eq('id', cid).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sales/leads/<lid>/followup', methods=['PUT'])
@require_sales_auth
def update_lead_followup(lid):
    data = request.get_json(silent=True) or {}
    date = data.get('followup_date')
    db.table('warm_leads').update({'followup_date': date}).eq('id', lid).execute()
    return jsonify({'success': True})

@app.route('/api/sales/leads/<lid>/followup-detail', methods=['PUT'])
@require_sales_auth
def update_lead_followup_detail(lid):
    data   = request.get_json(silent=True) or {}
    update = {}
    if 'followup_done' in data:
        update['followup_done'] = bool(data['followup_done'])
    if 'followup_type' in data:
        ft = data.get('followup_type')
        if ft in (None, 'gebeld', 'whatsapp'):
            update['followup_type'] = ft
    if 'still_interested' in data:
        si = data.get('still_interested')
        update['still_interested'] = None if si is None else bool(si)
    if 'whatsapp_read' in data:
        wr = data.get('whatsapp_read')
        update['whatsapp_read'] = None if wr is None else bool(wr)
    if update:
        db.table('warm_leads').update(update).eq('id', lid).execute()
    return jsonify({'success': True})

# ── MEETING ENDPOINTS ────────────────────────────────────────────────────────
# Meeting state machine:
#   pending_link → link_sent → scheduled → show / no_show
#                                            ↘ no_show_followup → (retry cycle)
#                                            ↘ afgehaakt
# Lives on warm_leads first; meeting columns are carried over to clients when
# /to-client runs. After a lead becomes a client, the SAME endpoints exist
# under /api/sales/clients/<cid>/meeting/...
MEETING_STATE_ENUM = ('pending_link','link_sent','scheduled','no_show_followup','afgehaakt')

def _meeting_member_ctx():
    """Return (member_id, member_name) for the current session, best-effort."""
    mid = _get_sales_member_id()
    mname = None
    try:
        if mid:
            mres = db.table('sales_members').select('name').eq('id', mid).limit(1).execute()
            mname = mres.data[0]['name'] if mres.data else None
    except Exception:
        pass
    return mid, mname

def _meeting_apply(table, eid, entity_type, update, new_state, mid=None, mname=None):
    """Apply a meeting update to warm_leads or clients, with optional
    status_history transition. Returns the updated row or None."""
    try:
        old_res = db.table(table).select('meeting_state').eq('id', eid).limit(1).execute()
        old_state = (old_res.data[0].get('meeting_state') if old_res.data else None) or 'pending_link'
    except Exception:
        old_state = None
    try:
        db.table(table).update(update).eq(  'id', eid).execute()
    except Exception as e:
        print(f'[MEETING] update {table} {eid} failed: {e}')
        return None
    if new_state and new_state != old_state:
        _log_status_change(entity_type, eid, f'meeting:{old_state}', f'meeting:{new_state}', mid=mid, member_name=mname)
    try:
        res = db.table(table).select('*').eq('id', eid).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception:
        return None

def _meeting_send_link(table, entity_type, eid):
    data = request.get_json(silent=True) or {}
    url  = (data.get('calendly_url') or '').strip()
    if not url or not (url.startswith('http://') or url.startswith('https://')):
        return jsonify({'success': False, 'error': 'Ongeldige Calendly URL.'}), 400
    mid, mname = _meeting_member_ctx()
    now_iso = datetime.now(timezone.utc).isoformat()
    update = {
        'meeting_calendly_url': url,
        'meeting_state':        'link_sent',
        'meeting_link_sent_at': now_iso,
    }
    row = _meeting_apply(table, eid, entity_type, update, 'link_sent', mid=mid, mname=mname)
    if not row:
        return jsonify({'success': False, 'error': 'Bijwerken mislukt.'}), 500
    name = row.get('company_name') or row.get('name') or ''
    _log_activity(mid, mname, 'meeting_link_sent', f'Calendly link gestuurd naar {name}')
    return jsonify({'success': True, 'row': row})

def _meeting_schedule(table, entity_type, eid):
    data = request.get_json(silent=True) or {}
    scheduled_at = (data.get('scheduled_at') or '').strip()
    join_url     = (data.get('join_url') or '').strip() or None
    if not scheduled_at:
        return jsonify({'success': False, 'error': 'scheduled_at verplicht.'}), 400
    # Basic ISO sanity check
    try:
        datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
    except Exception:
        return jsonify({'success': False, 'error': 'Ongeldige datum/tijd.'}), 400
    mid, mname = _meeting_member_ctx()
    update = {
        'meeting_state':         'scheduled',
        'meeting_scheduled_at':  scheduled_at,
        'meeting_join_url':      join_url,
        # Clear any stale outcome so the row is "fresh" for this meeting cycle
        'meeting_outcome':       None,
    }
    row = _meeting_apply(table, eid, entity_type, update, 'scheduled', mid=mid, mname=mname)
    if not row:
        return jsonify({'success': False, 'error': 'Bijwerken mislukt.'}), 500
    name = row.get('company_name') or row.get('name') or ''
    _log_activity(mid, mname, 'meeting_scheduled', f'Meeting ingepland met {name}')
    return jsonify({'success': True, 'row': row})

def _meeting_outcome(table, entity_type, eid):
    data = request.get_json(silent=True) or {}
    outcome = (data.get('outcome') or '').strip()
    if outcome not in ('show', 'no_show', 'afgehaakt'):
        return jsonify({'success': False, 'error': 'outcome moet show|no_show|afgehaakt zijn.'}), 400
    mid, mname = _meeting_member_ctx()
    update = {}
    new_state = None
    if outcome == 'show':
        update['meeting_outcome'] = 'show'
        # Keep meeting_state='scheduled' (the meeting was kept); mark show
        # On clients, also bump demo_status to 'show' so the funnel reflects it
        if table == 'clients':
            update['demo_status'] = 'show'
    elif outcome == 'no_show':
        update['meeting_outcome'] = 'no_show'
        if table == 'clients':
            update['demo_status'] = 'no_show'
    else:  # afgehaakt
        update['meeting_outcome'] = None
        update['meeting_state']   = 'afgehaakt'
        new_state = 'afgehaakt'
        if table == 'clients':
            update['demo_status'] = 'afgehaakt'
    row = _meeting_apply(table, eid, entity_type, update, new_state, mid=mid, mname=mname)
    if not row:
        return jsonify({'success': False, 'error': 'Bijwerken mislukt.'}), 500
    # Status_history note for the outcome (separate from state change above)
    _log_status_change(entity_type, eid, 'meeting:pending', f'meeting_outcome:{outcome}', mid=mid, member_name=mname)
    name = row.get('company_name') or row.get('name') or ''
    label = {'show':'Show','no_show':'No-show','afgehaakt':'Afgehaakt'}[outcome]
    _log_activity(mid, mname, 'meeting_outcome', f'Meeting met {name}: {label}')
    return jsonify({'success': True, 'row': row})

def _meeting_no_show_followup(table, entity_type, eid):
    data = request.get_json(silent=True) or {}
    method = (data.get('method') or '').strip()
    if method not in ('phone', 'whatsapp'):
        return jsonify({'success': False, 'error': 'method moet phone|whatsapp zijn.'}), 400
    # Require the row is currently in no_show before we mark followup
    try:
        cur = db.table(table).select('meeting_outcome').eq('id', eid).limit(1).execute()
        cur_out = cur.data[0].get('meeting_outcome') if cur.data else None
    except Exception:
        cur_out = None
    if cur_out != 'no_show':
        return jsonify({'success': False, 'error': 'Alleen mogelijk bij no_show.'}), 400
    mid, mname = _meeting_member_ctx()
    now_iso = datetime.now(timezone.utc).isoformat()
    update = {
        'meeting_state':                'no_show_followup',
        'meeting_no_show_followup_at':  now_iso,
    }
    row = _meeting_apply(table, eid, entity_type, update, 'no_show_followup', mid=mid, mname=mname)
    if not row:
        return jsonify({'success': False, 'error': 'Bijwerken mislukt.'}), 500
    name = row.get('company_name') or row.get('name') or ''
    label = 'WhatsApp' if method == 'whatsapp' else 'belletje'
    _log_activity(mid, mname, 'meeting_no_show_followup', f'No-show follow-up ({label}) naar {name}')
    return jsonify({'success': True, 'row': row})


@app.route('/api/sales/leads/<lid>/meeting/link', methods=['POST'])
@require_sales_auth
def warm_meeting_send_link(lid):
    return _meeting_send_link('warm_leads', 'warm_lead', lid)

@app.route('/api/sales/leads/<lid>/meeting/scheduled', methods=['PUT'])
@require_sales_auth
def warm_meeting_schedule(lid):
    return _meeting_schedule('warm_leads', 'warm_lead', lid)

@app.route('/api/sales/leads/<lid>/meeting/outcome', methods=['PUT'])
@require_sales_auth
def warm_meeting_outcome(lid):
    return _meeting_outcome('warm_leads', 'warm_lead', lid)

@app.route('/api/sales/leads/<lid>/meeting/no-show-followup', methods=['PUT'])
@require_sales_auth
def warm_meeting_no_show_followup(lid):
    return _meeting_no_show_followup('warm_leads', 'warm_lead', lid)

@app.route('/api/sales/clients/<cid>/meeting/link', methods=['POST'])
@require_sales_auth
def client_meeting_send_link(cid):
    return _meeting_send_link('clients', 'client', cid)

@app.route('/api/sales/clients/<cid>/meeting/scheduled', methods=['PUT'])
@require_sales_auth
def client_meeting_schedule(cid):
    return _meeting_schedule('clients', 'client', cid)

@app.route('/api/sales/clients/<cid>/meeting/outcome', methods=['PUT'])
@require_sales_auth
def client_meeting_outcome(cid):
    return _meeting_outcome('clients', 'client', cid)

@app.route('/api/sales/clients/<cid>/meeting/no-show-followup', methods=['PUT'])
@require_sales_auth
def client_meeting_no_show_followup(cid):
    return _meeting_no_show_followup('clients', 'client', cid)


@app.route('/api/sales/leads/<lid>', methods=['DELETE'])
@require_sales_auth
def delete_sales_lead(lid):
    db.table('warm_leads').delete().eq('id', lid).execute()
    return jsonify({'success': True})

@app.route('/api/sales/leads/<lid>', methods=['PUT'])
@require_sales_auth
def update_sales_lead(lid):
    data         = request.get_json(silent=True) or {}
    company_name = (data.get('company_name') or '').strip()
    phone        = (data.get('phone') or '').strip()
    maps_url     = (data.get('maps_url') or '').strip()
    if not company_name:
        return jsonify({'success': False, 'error': 'Bedrijfsnaam is verplicht.'}), 400
    db.table('warm_leads').update({
        'company_name': company_name,
        'phone': phone,
        'maps_url': maps_url,
    }).eq('id', lid).execute()
    return jsonify({'success': True})

@app.route('/api/sales/followups', methods=['GET'])
@require_sales_auth
def get_followups():
    from datetime import timezone
    today = datetime.now(timezone.utc).date().isoformat()
    res = db.table('warm_leads').select('*').lte('followup_date', today).neq('status', 'closed').order('followup_date').execute()
    return jsonify(res.data)


@app.route('/api/sales/meeting-reminders', methods=['GET'])
@require_sales_auth
def get_meeting_reminders():
    """Meetings that need attention: scheduled within the next 30 min OR already
    started but no outcome recorded yet. Scoped to the logged-in salesperson via
    added_by_id (otherwise reps see each other's meetings). Returns unified
    shape: [{entity_type:'warm_lead'|'client', id, name, phone, scheduled_at,
    join_url, calendly_url, is_due, is_overdue}]."""
    from datetime import timedelta
    mid = _get_sales_member_id()
    if not mid:
        return jsonify([])
    now_dt   = datetime.now(timezone.utc)
    cutoff   = (now_dt + timedelta(minutes=30)).isoformat()
    earliest = (now_dt - timedelta(hours=24)).isoformat()   # show last 24h of "due"
    out = []
    try:
        wq = (db.table('warm_leads')
              .select('id,company_name,phone,meeting_state,meeting_outcome,meeting_scheduled_at,meeting_join_url,meeting_calendly_url,added_by_id')
              .eq('added_by_id', str(mid))
              .eq('meeting_state', 'scheduled')
              .is_('meeting_outcome', 'null')
              .gte('meeting_scheduled_at', earliest)
              .lte('meeting_scheduled_at', cutoff)
              .order('meeting_scheduled_at')
              .execute())
        for r in (wq.data or []):
            sched = r.get('meeting_scheduled_at') or ''
            is_due = sched and sched <= now_dt.isoformat()
            out.append({
                'entity_type':   'warm_lead',
                'id':            r.get('id'),
                'name':          r.get('company_name') or '',
                'phone':         r.get('phone') or '',
                'scheduled_at':  sched,
                'join_url':      r.get('meeting_join_url'),
                'calendly_url':  r.get('meeting_calendly_url'),
                'is_due':        bool(is_due),
                'is_overdue':    bool(sched and sched < (now_dt - timedelta(minutes=15)).isoformat()),
            })
    except Exception as e:
        print(f'[MEETING-REMINDERS] warm_leads fetch failed: {e}')
    try:
        cq = (db.table('clients')
              .select('id,name,phone,meeting_state,meeting_outcome,meeting_scheduled_at,meeting_join_url,meeting_calendly_url,added_by_id')
              .eq('added_by_id', str(mid))
              .eq('meeting_state', 'scheduled')
              .is_('meeting_outcome', 'null')
              .gte('meeting_scheduled_at', earliest)
              .lte('meeting_scheduled_at', cutoff)
              .order('meeting_scheduled_at')
              .execute())
        for r in (cq.data or []):
            sched = r.get('meeting_scheduled_at') or ''
            is_due = sched and sched <= now_dt.isoformat()
            out.append({
                'entity_type':   'client',
                'id':            r.get('id'),
                'name':          r.get('name') or '',
                'phone':         r.get('phone') or '',
                'scheduled_at':  sched,
                'join_url':      r.get('meeting_join_url'),
                'calendly_url':  r.get('meeting_calendly_url'),
                'is_due':        bool(is_due),
                'is_overdue':    bool(sched and sched < (now_dt - timedelta(minutes=15)).isoformat()),
            })
    except Exception as e:
        print(f'[MEETING-REMINDERS] clients fetch failed: {e}')
    out.sort(key=lambda r: r.get('scheduled_at') or '')
    return jsonify(out)

# ── CLIENTS ──────────────────────────────────────────────────────────────────

@app.route('/api/sales/leads/<lid>/to-client', methods=['PUT'])
@require_sales_auth
def lead_to_client(lid):
    res = db.table('warm_leads').select('*').eq('id', lid).limit(1).execute()
    if not res.data:
        return jsonify({'success': False, 'error': 'Lead niet gevonden.'}), 404
    lead = res.data[0]

    # ── Gate: must have meeting scheduled + forum ingevuld ───────────────────
    # Allow ?force=1 in body to override (used by legacy/admin tooling).
    body = request.get_json(silent=True) or {}
    force = bool(body.get('force'))
    ps = lead.get('pipeline_status') or ''
    ms = lead.get('meeting_state') or 'pending_link'
    if not force:
        if ps != 'forum_ingevuld':
            return jsonify({'success': False,
                            'error': 'Forum moet eerst ingevuld zijn voordat de lead naar Clients gaat.',
                            'code': 'forum_not_filled'}), 400
        if ms not in ('scheduled', 'no_show_followup'):
            return jsonify({'success': False,
                            'error': 'Meeting moet eerst ingepland zijn voordat de lead naar Clients gaat.',
                            'code': 'meeting_not_scheduled'}), 400

    # Carry meeting columns + added_by_id + contact_method onto the new client.
    # Meeting fields are HIGH PRIORITY — drop them last so the state survives
    # even if the clients table is missing newer columns.
    base_payload = {
        'name':              lead.get('company_name', ''),
        'phone':             lead.get('phone', '') or '',
        'maps_url':          lead.get('maps_url', '') or '',
        'added_by_name':     lead.get('added_by_name', '') or '',
        'demo_status':       'moet_gebouwd',
    }
    meeting_payload = {
        'meeting_state':                lead.get('meeting_state'),
        'meeting_calendly_url':         lead.get('meeting_calendly_url'),
        'meeting_link_sent_at':         lead.get('meeting_link_sent_at'),
        'meeting_scheduled_at':         lead.get('meeting_scheduled_at'),
        'meeting_join_url':             lead.get('meeting_join_url'),
        'meeting_outcome':              lead.get('meeting_outcome'),
        'meeting_no_show_followup_at':  lead.get('meeting_no_show_followup_at'),
    }
    cheap_payload = {
        'added_by_id':                  lead.get('added_by_id'),
        'contact_method':               lead.get('contact_method'),
        'hermes_started_by_id':         lead.get('hermes_started_by_id'),
        'hermes_started_by_name':       lead.get('hermes_started_by_name'),
    }
    # Build full payload, only including non-None values
    full = dict(base_payload)
    for k, v in cheap_payload.items():
        if v is not None: full[k] = v
    for k, v in meeting_payload.items():
        if v is not None: full[k] = v

    # Try tiers in order: full → drop cheap_payload (most likely missing) →
    # drop meeting fields only as last resort → minimal base. Meeting state is
    # the load-bearing field per Julian's spec, so it gets kept until the end.
    tier_with_meeting = dict(base_payload)
    for k, v in meeting_payload.items():
        if v is not None: tier_with_meeting[k] = v

    client_id = None
    last_err = None
    for tier in (full, tier_with_meeting, base_payload):
        try:
            client_res = db.table('clients').insert(tier).execute()
            client_id = client_res.data[0]['id'] if client_res.data else None
            print(f"[TO-CLIENT] Lead {lid} → Client {client_id} (tier cols: {sorted(tier.keys())})")
            break
        except Exception as e:
            last_err = e
            print(f"[TO-CLIENT] tier insert failed: {e}")
    if client_id is None:
        return jsonify({'success': False, 'error': f'Client aanmaken mislukt: {last_err}'}), 500

    # Only update pipeline after successful client creation
    old_ps = lead.get('pipeline_status')
    db.table('warm_leads').update({'pipeline_status': 'gesloten'}).eq('id', lid).execute()
    _log_status_change('warm_lead', lid, old_ps, 'gesloten',
                       mid=lead.get('added_by_id'), member_name=lead.get('added_by_name'))
    _log_activity(lead.get('added_by_id',''), lead.get('added_by_name',''), 'demo', f'bracht {lead.get("company_name","")} naar demo 🚀')
    return jsonify({'success': True, 'client_id': client_id})


@app.route('/api/sales/clients/<cid>/close', methods=['PUT'])
@require_sales_auth
def close_client(cid):
    data   = request.get_json(silent=True) or {}
    amount = data.get('amount')
    if amount is None:
        return jsonify({'success': False, 'error': 'Bedrag is verplicht.'}), 400
    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Ongeldig bedrag.'}), 400

    res = db.table('clients').select('*').eq('id', cid).limit(1).execute()
    if not res.data:
        return jsonify({'success': False, 'error': 'Client niet gevonden.'}), 404
    client = res.data[0]

    # Find warm lead by name match
    name = client.get('name', '')
    lead_res = db.table('warm_leads').select('id,added_by_id,added_by_name,commission_rate_locked,contact_method,hermes_started_by_id,hermes_started_by_name').eq('company_name', name).eq('pipeline_status', 'gesloten').limit(1).execute()
    commission = None
    if lead_res.data:
        lead_id      = lead_res.data[0]['id']
        added_by_id  = lead_res.data[0]['added_by_id']
        added_by_name = (lead_res.data[0].get('added_by_name') or '').strip()
        locked       = lead_res.data[0].get('commission_rate_locked')
        hermes_starter_id = lead_res.data[0].get('hermes_started_by_id')
        is_hermes_lead = added_by_name.lower() == 'hermes (ai)' or bool(hermes_starter_id)

        # ── Hermes commissie regel: Timon 75%, anderen (incl Julian) 0% ──
        # Wint over alle andere regels (locked rate, WA tier, etc.) want
        # de gebruiker spec'd dit expliciet als override voor AI-leads.
        if is_hermes_lead:
            rate = _hermes_commission_rate_for_starter(hermes_starter_id)
            # Voor leaderboard-attributie: zet added_by_id naar Timon's id
            # zodat de commissie onder zijn naam verschijnt (alleen als hij
            # commissie krijgt). Anders blijft added_by_id NULL = niemand.
            timon_id = _hermes_get_timon_id()
            if rate > 0 and timon_id:
                if added_by_id != timon_id:
                    try:
                        db.table('warm_leads').update({'added_by_id': timon_id}).eq('id', lead_id).execute()
                        added_by_id = timon_id
                    except Exception as e:
                        print(f'[CLOSE-CLIENT] hermes-attribute to Timon failed: {e}')
            commission = round(amount * rate, 2)
            print(f"[CLOSE-CLIENT] HERMES lead — rate={rate} starter={hermes_starter_id!r} commission={commission}")
        else:
            # Use the client's contact_method as the authoritative source — it
            # was carried over from the warm_lead at to-client time and reflects
            # what channel the lead came in through.
            lead_method  = (client.get('contact_method') or lead_res.data[0].get('contact_method') or '').strip()
            member_for_rate = db.table('sales_members').select('id,name,email,contract_type,commission_override').eq('id', added_by_id).limit(1).execute()
            if locked is not None:
                # Locked rate was captured at WA outreach / lead creation — honour it.
                rate = float(locked)
            elif lead_method == 'whatsapp' and member_for_rate.data:
                # WhatsApp lead without a locked rate (e.g. created before lock-on-
                # create was added). Compute the member's current WA-tier rate so
                # WA leads never silently fall back to the legacy 40%.
                rate = _compute_whatsapp_rate(added_by_id)
                # Backfill commission_rate_locked so re-closes are consistent.
                try:
                    db.table('warm_leads').update({'commission_rate_locked': rate}).eq('id', lead_id).execute()
                except Exception as e:
                    print(f"[CLOSE-CLIENT] backfill locked rate failed: {e}")
            else:
                rate = _get_effective_rate(member_for_rate.data[0]) if member_for_rate.data else 0.40
            commission = round(amount * rate, 2)
            print(f"[CLOSE-CLIENT] rate={rate} (locked={locked}, method={lead_method!r}) commission={commission}")
        db.table('warm_leads').update({
            'status': 'closed',
            'closed_amount': amount,
            'commission_amount': commission,
            'closed_at': datetime.utcnow().isoformat(),
        }).eq('id', lead_id).execute()

        # first-sale bonus for referrer
        member_res = db.table('sales_members').select('*').eq('id', added_by_id).limit(1).execute()
        if member_res.data:
            member = member_res.data[0]
            if not member.get('first_sale_counted'):
                db.table('sales_members').update({'first_sale_counted': True}).eq('id', added_by_id).execute()
                ref_code = member.get('referred_by_code')
                if ref_code:
                    ref_res = db.table('sales_members').select('bonus_owed').eq('ref_code', ref_code).limit(1).execute()
                    if ref_res.data:
                        old_bonus = float(ref_res.data[0].get('bonus_owed') or 0)
                        db.table('sales_members').update({'bonus_owed': old_bonus + 20}).eq('ref_code', ref_code).execute()

    old_demo_status = client.get('demo_status')
    db.table('clients').update({
        'total_amount': amount,
        'commission_amount': commission,
        'demo_status': 'geclosed',
    }).eq('id', cid).execute()
    _log_status_change('client', cid, old_demo_status, 'geclosed',
                       mid=client.get('added_by_id'), member_name=client.get('added_by_name'))

    closer_name = client.get('added_by_name') or (member_for_rate.data[0].get('name') if lead_res.data and member_for_rate.data else '') or ''
    closer_id   = added_by_id if lead_res.data else ''
    _log_activity(closer_id, closer_name, 'deal_closed', f'sloot een deal van €{int(amount)} 💰')
    print(f"[CLOSE-CLIENT] Client {cid} closed at €{amount}")
    return jsonify({'success': True})


@app.route('/api/sales/clients/<cid>/to-lead', methods=['PUT'])
@require_sales_auth
def client_to_lead(cid):
    res = db.table('clients').select('*').eq('id', cid).limit(1).execute()
    if not res.data:
        return jsonify({'success': False, 'error': 'Client niet gevonden.'}), 404
    client = res.data[0]
    # Find matching warm lead by name and gesloten pipeline status
    name = client.get('name', '')
    lead_res = db.table('warm_leads').select('id').eq('company_name', name).eq('pipeline_status', 'gesloten').limit(1).execute()
    if lead_res.data:
        lead_id = lead_res.data[0]['id']
        # Carry meeting columns BACK to the warm lead so the meeting history
        # is not lost when the client row is deleted. Keep pipeline_status at
        # 'forum_ingevuld' (was the prerequisite for becoming a client).
        update = {
            'pipeline_status':              'forum_ingevuld',
            'closed_amount':                None,
            'commission_amount':            None,
            'closed_at':                    None,
            'meeting_state':                client.get('meeting_state'),
            'meeting_calendly_url':         client.get('meeting_calendly_url'),
            'meeting_link_sent_at':         client.get('meeting_link_sent_at'),
            'meeting_scheduled_at':         client.get('meeting_scheduled_at'),
            'meeting_join_url':             client.get('meeting_join_url'),
            'meeting_outcome':              client.get('meeting_outcome'),
            'meeting_no_show_followup_at':  client.get('meeting_no_show_followup_at'),
        }
        try:
            db.table('warm_leads').update(update).eq('id', lead_id).execute()
        except Exception as e:
            # Schema mismatch fallback: drop meeting fields and retry
            print(f"[TO-LEAD] full update failed, retrying minimal: {e}")
            for k in list(update):
                if k.startswith('meeting_'): update.pop(k)
            db.table('warm_leads').update(update).eq('id', lead_id).execute()
    db.table('clients').delete().eq('id', cid).execute()
    print(f"[TO-LEAD] Client {cid} ({name})")
    return jsonify({'success': True})


@app.route('/api/sales/clients/<cid>', methods=['DELETE'])
@require_sales_auth
def delete_client(cid):
    # Find the matching closed warm_lead by name so we can remove it too —
    # otherwise its closed_amount keeps counting toward team revenue/commission
    # even after the client is gone.
    name = None
    try:
        c_res = db.table('clients').select('name').eq('id', cid).limit(1).execute()
        if c_res.data:
            name = (c_res.data[0].get('name') or '').strip()
    except Exception as e:
        print(f"[DELETE-CLIENT] lookup failed: {e}")

    if name:
        try:
            db.table('warm_leads').delete().eq('company_name', name).eq('pipeline_status', 'gesloten').execute()
        except Exception as e:
            print(f"[DELETE-CLIENT] warm_lead cleanup failed: {e}")

    db.table('clients').delete().eq('id', cid).execute()
    print(f"[DELETE-CLIENT] Client {cid} (name={name!r})")
    return jsonify({'success': True})


@app.route('/api/sales/admin/hermes-backfill-starter-attribution', methods=['POST'])
@require_auth
def admin_hermes_backfill_starter_attribution():
    """Backfill hermes_started_by_id / hermes_started_by_name op bestaande
    warm_leads + clients die door Hermes zijn aangemaakt. Volgt de keten:
      warm_lead.id → prospect_list.hermes_warm_lead_id → prospect.hermes_run_id
                  → hermes_runs.started_by_id/name."""
    try:
        # Pak warm leads die door Hermes zijn aangemaakt zonder attributie
        leads_res = db.table('warm_leads').select('id,company_name,hermes_started_by_name,added_by_name').eq('added_by_name', 'Hermes (AI)').execute()
        leads = leads_res.data or []
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:200]}), 500
    fixed_leads = []
    fixed_clients = []
    # Cache van run_id → (starter_id, starter_name)
    run_cache = {}
    for lead in leads:
        if (lead.get('hermes_started_by_name') or '').strip():
            continue
        # Zoek de gekoppelde prospect via hermes_warm_lead_id
        try:
            pr = db.table('prospect_list').select('id,hermes_run_id').eq('hermes_warm_lead_id', lead['id']).limit(1).execute()
            if not pr.data: continue
            run_id = pr.data[0].get('hermes_run_id')
            if not run_id: continue
            if run_id not in run_cache:
                rr = db.table('hermes_runs').select('started_by_id,started_by_name').eq('id', run_id).limit(1).execute()
                run_cache[run_id] = (rr.data[0].get('started_by_id'), rr.data[0].get('started_by_name')) if rr.data else (None, None)
            starter_id, starter_name = run_cache[run_id]
            if not starter_name: continue
            db.table('warm_leads').update({
                'hermes_started_by_id':   starter_id,
                'hermes_started_by_name': starter_name,
            }).eq('id', lead['id']).execute()
            fixed_leads.append({'lead_id': lead['id'], 'company': lead.get('company_name'), 'starter': starter_name})
            # Als de warm lead al een client is geworden (gematched op company_name) — backfill ook daar
            try:
                cl = db.table('clients').select('id').eq('name', lead.get('company_name')).limit(1).execute()
                if cl.data:
                    db.table('clients').update({
                        'hermes_started_by_id':   starter_id,
                        'hermes_started_by_name': starter_name,
                    }).eq('id', cl.data[0]['id']).execute()
                    fixed_clients.append({'client_id': cl.data[0]['id'], 'company': lead.get('company_name')})
            except Exception as ce:
                print(f'[ADMIN-BACKFILL] client update for {lead.get("company_name")} failed: {ce}')
        except Exception as e:
            print(f'[ADMIN-BACKFILL] failed for warm_lead {lead.get("id")}: {e}')
    return jsonify({
        'success': True,
        'fixed_leads_count':   len(fixed_leads),
        'fixed_clients_count': len(fixed_clients),
        'sample_leads':   fixed_leads[:10],
        'sample_clients': fixed_clients[:10],
    })


@app.route('/api/sales/admin/hermes-reclassify-transport-errors', methods=['POST'])
@require_auth
def admin_hermes_reclassify_transport_errors():
    """Backfill prospects that landed on 'benaderd' with a transport-level
    Vapi error (call.start.error, get-transport, pipeline-error, etc.).
    Zet ze terug naar niet_opgenomen + called=false zodat ze in een
    volgende Hermes ronde opnieuw gebeld worden."""
    try:
        res = db.table('prospect_list').select('id,company_name,hermes_status,hermes_outcome,hermes_ended_reason,called').eq('hermes_outcome', 'benaderd').execute()
        rows = res.data or []
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:200]}), 500
    fixed = []
    for r in rows:
        er = (r.get('hermes_ended_reason') or '').lower()
        if not er: continue
        # Substring-match op alle bekende transport / pipeline / connection errors
        if ('call.start.error' in er or 'get-transport' in er or 'get transport' in er
            or 'pipeline-error' in er or 'assistant-error' in er
            or 'twilio-failed-to-connect' in er or 'customer-busy' in er):
            try:
                db.table('prospect_list').update({
                    'hermes_status':   'niet_opgenomen',
                    'hermes_outcome':  'no_answer',
                    'called':          False,
                }).eq('id', r['id']).execute()
                fixed.append({'id': r['id'], 'name': r.get('company_name'), 'reason': er[:80]})
            except Exception as e:
                print(f'[ADMIN-RECLASSIFY] update failed for {r["id"]}: {e}')
    return jsonify({'success': True, 'fixed_count': len(fixed), 'sample': fixed[:10]})


@app.route('/api/sales/admin/relock-wa-rates', methods=['POST'])
@require_auth
def admin_relock_wa_rates():
    """Backfill commission_rate_locked for warm_leads where contact_method
    is whatsapp but the locked rate is either NULL or the legacy 40%.
    Computes each member's CURRENT WA-tier rate and writes it. Safe to run
    multiple times — only touches rows that are clearly wrong."""
    leads_res = db.table('warm_leads').select('id,added_by_id,contact_method,commission_rate_locked,company_name').eq('contact_method', 'whatsapp').execute()
    rows = leads_res.data or []
    fixed = []
    skipped_no_member = 0
    # Cache WA rate per member to avoid recomputing per lead
    rate_cache = {}
    for r in rows:
        mid = r.get('added_by_id')
        if not mid:
            skipped_no_member += 1
            continue
        locked = r.get('commission_rate_locked')
        # Fix: NULL OR >= 39% (legacy 40% slip-through). WA tiers are 0.25/0.30/0.35 at most.
        if locked is not None and float(locked) < 0.39:
            continue
        if mid not in rate_cache:
            try:
                rate_cache[mid] = _compute_whatsapp_rate(mid)
            except Exception as e:
                print(f"[RELOCK-WA] _compute_whatsapp_rate({mid}) failed: {e}")
                rate_cache[mid] = None
        new_rate = rate_cache[mid]
        if new_rate is None:
            continue
        try:
            db.table('warm_leads').update({'commission_rate_locked': new_rate}).eq('id', r['id']).execute()
            fixed.append({
                'id': r['id'],
                'company_name': r.get('company_name'),
                'old_rate': locked,
                'new_rate': new_rate,
            })
        except Exception as e:
            print(f"[RELOCK-WA] update {r['id']} failed: {e}")
    return jsonify({
        'success': True,
        'scanned': len(rows),
        'fixed': len(fixed),
        'skipped_no_member': skipped_no_member,
        'samples': fixed[:8],
    })


@app.route('/api/sales/admin/cleanup-orphan-closed-leads', methods=['POST'])
@require_auth
def cleanup_orphan_closed_leads():
    """One-off: delete closed warm_leads whose client row no longer exists.
    Returns the list of removed company_names so the admin can verify."""
    try:
        clients_res = db.table('clients').select('name').execute()
        client_names = {(c.get('name') or '').strip().lower() for c in (clients_res.data or [])}

        leads_res = db.table('warm_leads').select('id,company_name,closed_amount').eq('status', 'closed').execute()
        orphans = [r for r in (leads_res.data or [])
                   if (r.get('company_name') or '').strip().lower() not in client_names]
        removed = []
        for r in orphans:
            try:
                db.table('warm_leads').delete().eq('id', r['id']).execute()
                removed.append({'company_name': r.get('company_name'), 'closed_amount': r.get('closed_amount')})
            except Exception as e:
                print(f"[CLEANUP-ORPHAN] failed to delete {r.get('id')}: {e}")
        return jsonify({'success': True, 'removed': removed, 'count': len(removed)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sales/schedule', methods=['GET'])
@require_sales_auth
def get_my_schedule():
    from datetime import date, timedelta
    mid = _get_sales_member_id()
    week_param = request.args.get('week')
    try:
        week_start = date.fromisoformat(week_param) if week_param else date.today() - timedelta(days=date.today().weekday())
    except Exception:
        week_start = date.today() - timedelta(days=date.today().weekday())
    week_end = week_start + timedelta(days=6)
    try:
        res = db.table('work_schedule').select('*').eq('member_id', str(mid)).gte('date', week_start.isoformat()).lte('date', week_end.isoformat()).execute()
        return jsonify({'week_start': week_start.isoformat(), 'entries': res.data})
    except Exception as e:
        return jsonify({'error': str(e), 'entries': []}), 500


@app.route('/api/sales/schedule/team', methods=['GET'])
@require_sales_auth
def get_team_schedule():
    from datetime import date, timedelta
    week_param = request.args.get('week')
    try:
        week_start = date.fromisoformat(week_param) if week_param else date.today() - timedelta(days=date.today().weekday())
    except Exception:
        week_start = date.today() - timedelta(days=date.today().weekday())
    week_end = week_start + timedelta(days=6)
    try:
        res = db.table('work_schedule').select('*').gte('date', week_start.isoformat()).lte('date', week_end.isoformat()).execute()
        return jsonify({'week_start': week_start.isoformat(), 'entries': res.data})
    except Exception as e:
        return jsonify({'error': str(e), 'entries': []}), 500


@app.route('/api/sales/last-week-recap', methods=['GET'])
@require_sales_auth
def last_week_recap():
    from datetime import timezone, timedelta
    mid = _get_sales_member_id()
    now = datetime.now(timezone.utc)
    last_monday = (now - timedelta(days=now.weekday() + 7)).replace(hour=0, minute=0, second=0, microsecond=0)
    last_sunday = (last_monday + timedelta(days=6)).replace(hour=23, minute=59, second=59)
    try:
        leads_res    = db.table('warm_leads').select('id', count='exact').eq('added_by_id', mid).gte('created_at', last_monday.isoformat()).execute()
        prospect_res = db.table('prospect_list').select('id', count='exact').eq('called_by_id', str(mid)).eq('called', True).gte('called_at', last_monday.isoformat()).lte('called_at', last_sunday.isoformat()).execute()
        sched_res    = db.table('work_schedule').select('actual_hours,planned_hours,worked').eq('member_id', str(mid)).gte('date', last_monday.date().isoformat()).lte('date', last_sunday.date().isoformat()).execute()
        hours = sum(float(r.get('actual_hours') or r.get('planned_hours') or 0) for r in sched_res.data if r.get('worked'))
        return jsonify({'leads': leads_res.count or 0, 'prospects': prospect_res.count or 0, 'hours': round(hours, 1)})
    except Exception as e:
        return jsonify({'leads': 0, 'prospects': 0, 'hours': 0})


SESSION_MIN_SECONDS = 60       # below this, the session is treated as accidental and not logged
SESSION_MAX_HOURS   = 12       # safety cap: a single session longer than this gets clamped


def _parse_iso_to_naive_utc(s):
    """Parse a timestamp string from Supabase (often tz-aware like
    '2026-05-16T14:30:00+00:00' or 'Z') and return a naive UTC datetime
    so it can be subtracted from datetime.utcnow()."""
    if not s:
        return None
    try:
        from datetime import timezone
        norm = s.replace('Z', '+00:00') if s.endswith('Z') else s
        dt = datetime.fromisoformat(norm)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception as e:
        print(f'[ISO-PARSE] failed for {s!r}: {e}')
        return None


@app.route('/api/sales/session/start', methods=['POST'])
@require_sales_auth
def start_calling_session():
    mid = _get_sales_member_id()
    if not mid:
        print('[SESSION-START] failed: no member id')
        return jsonify({'success': False, 'error': 'no_member'}), 401
    data = request.get_json(silent=True) or {}
    try:
        goal_val = int(data.get('goal') or 0)
    except (TypeError, ValueError):
        goal_val = 0
    try:
        now_iso = datetime.utcnow().isoformat()
        now_dt  = datetime.utcnow()
        # Fetch the real member name up-front — never trust update()'s return shape.
        m_res = db.table('sales_members').select('name').eq('id', str(mid)).limit(1).execute()
        if not m_res.data:
            print(f'[SESSION-START] no sales_members row for mid={mid}')
            return jsonify({'success': False, 'error': 'no_member_row'}), 404
        member_name = m_res.data[0].get('name') or ''
        # Mark the member as calling
        res = db.table('sales_members').update({
            'is_calling': True,
            'session_start': now_iso,
        }).eq('id', str(mid)).execute()
        if not res.data:
            print(f'[SESSION-START] update returned no rows for mid={mid}')
            return jsonify({'success': False, 'error': 'update_failed'}), 500
        session_id = None
        # Insert into session_logs (best-effort — works only if table exists)
        try:
            # Auto-close any orphans with their real elapsed duration, never 0
            orphan_res = db.table('session_logs').select('id,start_ts').eq('member_id', str(mid)).is_('end_ts', None).execute()
            for o in (orphan_res.data or []):
                o_start = _parse_iso_to_naive_utc(o.get('start_ts'))
                o_secs  = int((now_dt - o_start).total_seconds()) if o_start else 0
                if o_secs > SESSION_MAX_HOURS * 3600:
                    o_secs = SESSION_MAX_HOURS * 3600
                db.table('session_logs').update({
                    'end_ts': now_iso,
                    'duration_seconds': max(o_secs, 0),
                }).eq('id', o['id']).execute()
                print(f'[SESSION-START] auto-closed orphan {o["id"]} secs={o_secs}')
            sess_res = db.table('session_logs').insert({
                'member_id': str(mid),
                'member_name': member_name,
                'start_ts': now_iso,
                'goal': goal_val,
            }).execute()
            if sess_res.data:
                session_id = sess_res.data[0].get('id')
        except Exception as e:
            print(f'[SESSION-START] session_logs insert skipped (table missing or error): {e}')
        print(f'[SESSION-START] mid={mid} name={member_name!r} at {now_iso} sess_id={session_id}')
        return jsonify({'success': True, 'session_start': now_iso, 'session_id': session_id})
    except Exception as e:
        print(f'[SESSION-START] error mid={mid}: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sales/session/stop', methods=['POST'])
@require_sales_auth
def stop_calling_session():
    from datetime import timedelta, date
    mid = _get_sales_member_id()
    if not mid:
        print('[SESSION-STOP] failed: no member id')
        return jsonify({'success': False, 'duration_hours': 0, 'reason': 'no_member'}), 401
    member_res = db.table('sales_members').select('name,session_start').eq('id', str(mid)).limit(1).execute()
    if not member_res.data:
        print(f'[SESSION-STOP] member not found mid={mid}')
        return jsonify({'success': False, 'duration_hours': 0, 'reason': 'no_member_row'})
    member        = member_res.data[0]
    member_name   = member.get('name', '')
    # Prefer session_logs.start_ts as the canonical source — sales_members.session_start
    # is just a denormalized marker and has been stripped before in some edge cases.
    session_start = member.get('session_start')
    open_log_id   = None
    open_log_start = None
    try:
        open_log_res = db.table('session_logs').select('id,start_ts').eq('member_id', str(mid)).is_('end_ts', None).order('start_ts', desc=True).limit(1).execute()
        if open_log_res.data:
            open_log_id    = open_log_res.data[0].get('id')
            open_log_start = open_log_res.data[0].get('start_ts')
    except Exception as e:
        print(f'[SESSION-STOP] open session_logs lookup failed: {e}')
    canonical_start = open_log_start or session_start
    secs = 0
    if canonical_start:
        start_dt = _parse_iso_to_naive_utc(canonical_start)
        if start_dt is None:
            print(f'[SESSION-STOP] could not parse start={canonical_start!r} mid={mid}')
        else:
            secs = (datetime.utcnow() - start_dt).total_seconds()
            print(f'[SESSION-STOP] mid={mid} start={canonical_start} secs={secs:.0f} (source={"session_logs" if open_log_start else "sales_members"})')
    # Clamp safety: very long sessions (e.g. forgot to stop) are capped, not logged at face value
    if secs > SESSION_MAX_HOURS * 3600:
        print(f'[SESSION-STOP] clamping over-long session mid={mid} raw_secs={secs:.0f}')
        secs = SESSION_MAX_HOURS * 3600
    duration_hours = round(max(secs, 0) / 3600, 2)
    logged = False
    reason = None
    session_log_id = None
    written_row = None
    end_iso = datetime.utcnow().isoformat()
    # Use canonical_start for "was there a session?" — sales_members.session_start
    # can be cleared by a parallel stop while session_logs still has the open row.
    if not canonical_start:
        reason = 'no_session_start'
    elif secs < SESSION_MIN_SECONDS:
        reason = 'below_min_threshold'
        print(f'[SESSION-STOP] below threshold mid={mid} secs={secs:.0f}')
    else:
        today      = date.today().isoformat()
        week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
        # Bulletproof write: SELECT, then explicit INSERT or UPDATE.
        # Avoids needing a (member_id,date) unique constraint for upsert().
        try:
            existing = db.table('work_schedule').select('id,actual_hours').eq('member_id', str(mid)).eq('date', today).limit(1).execute()
            if existing.data:
                prev_hours = float(existing.data[0].get('actual_hours') or 0)
                new_hours  = round(prev_hours + duration_hours, 2)
                upd_res = db.table('work_schedule').update({
                    'actual_hours': new_hours,
                    'worked':       True,
                    'member_name':  member_name,
                    'week_start':   week_start,
                }).eq('id', existing.data[0]['id']).execute()
                written_row = upd_res.data[0] if upd_res.data else None
                print(f'[SESSION-STOP] UPDATE work_schedule id={existing.data[0]["id"]} prev={prev_hours} +{duration_hours} = {new_hours}')
            else:
                new_hours = round(duration_hours, 2)
                ins_res = db.table('work_schedule').insert({
                    'member_id':    str(mid),
                    'member_name':  member_name,
                    'date':         today,
                    'week_start':   week_start,
                    'actual_hours': new_hours,
                    'worked':       True,
                }).execute()
                written_row = ins_res.data[0] if ins_res.data else None
                print(f'[SESSION-STOP] INSERT work_schedule mid={mid} date={today} hours={new_hours}')
            _log_activity(mid, member_name, 'hours_worked', f'heeft {round(duration_hours,1)}u gebeld')
            logged = True
        except Exception as e:
            print(f'[SESSION-STOP] log error mid={mid}: {e!r}')
            reason = f'log_error:{e}'
    # Close the open session_logs row (best-effort; only if table exists)
    session_log_id = open_log_id
    if session_log_id:
        try:
            db.table('session_logs').update({
                'end_ts': end_iso,
                'duration_seconds': int(max(secs, 0)),
            }).eq('id', session_log_id).execute()
            print(f'[SESSION-STOP] closed session_log {session_log_id} dur_secs={int(max(secs, 0))}')
        except Exception as e:
            print(f'[SESSION-STOP] session_logs close failed for {session_log_id}: {e}')
    db.table('sales_members').update({'is_calling': False, 'session_start': None}).eq('id', str(mid)).execute()
    return jsonify({
        'success': True,
        'duration_hours': round(duration_hours, 1),
        'duration_seconds': int(max(secs, 0)),
        'session_id': session_log_id,
        'logged': logged,
        'reason': reason,
        'min_seconds': SESSION_MIN_SECONDS,
        'canonical_start': canonical_start,
        'canonical_source': 'session_logs' if open_log_start else ('sales_members' if session_start else None),
        'work_schedule_row': written_row,
    })


@app.route('/api/sales/_debug-rooster-state', methods=['GET'])
@require_sales_auth
def debug_rooster_state():
    """Returns everything we know about the current member's session/rooster
    state, so we can quickly tell why a session wasn't logged."""
    from datetime import date, timedelta
    mid = _get_sales_member_id()
    out = {'mid': str(mid) if mid else None}
    if not mid:
        return jsonify({**out, 'error': 'no_member_id'}), 401
    try:
        m = db.table('sales_members').select('id,name,email,is_calling,session_start,status').eq('id', str(mid)).limit(1).execute()
        out['sales_member'] = m.data[0] if m.data else None
    except Exception as e:
        out['sales_member_error'] = str(e)
    today = date.today().isoformat()
    try:
        ws = db.table('work_schedule').select('*').eq('member_id', str(mid)).eq('date', today).execute()
        out['work_schedule_today'] = ws.data
    except Exception as e:
        out['work_schedule_error'] = str(e)
    try:
        ws_week = db.table('work_schedule').select('*').eq('member_id', str(mid)).gte('date', (date.today() - timedelta(days=date.today().weekday())).isoformat()).execute()
        out['work_schedule_week'] = ws_week.data
    except Exception as e:
        out['work_schedule_week_error'] = str(e)
    try:
        sl = db.table('session_logs').select('*').eq('member_id', str(mid)).order('start_ts', desc=True).limit(10).execute()
        out['session_logs_recent'] = sl.data
        out['session_logs_exists'] = True
    except Exception as e:
        out['session_logs_exists'] = False
        out['session_logs_error']  = str(e)
    return jsonify(out)


@app.route('/api/sales/session-table-check', methods=['GET'])
@require_sales_auth
def check_session_logs_table():
    """Quick health check so the UI can tell the user whether they still
    need to run the session_logs CREATE TABLE SQL in Supabase."""
    try:
        db.table('session_logs').select('id').limit(1).execute()
        return jsonify({'exists': True})
    except Exception as e:
        return jsonify({'exists': False, 'error': str(e)})


SESSION_GAP_SECONDS = 30 * 60  # gap > 30 min splits a new inferred session


def _build_inferred_sessions(activities):
    """Given a chronologically ordered list of activities for one member,
    chunk them into sessions by gaps > SESSION_GAP_SECONDS. Returns a list
    of session dicts."""
    sessions = []
    cur = []
    last_dt = None
    for a in activities:
        a_dt = _parse_iso_to_naive_utc(a.get('called_at') or a.get('ts'))
        if not a_dt:
            continue
        if last_dt and (a_dt - last_dt).total_seconds() > SESSION_GAP_SECONDS:
            sessions.append(_session_from_chunk(cur))
            cur = []
        cur.append((a_dt, a))
        last_dt = a_dt
    if cur:
        sessions.append(_session_from_chunk(cur))
    return sessions


def _session_from_chunk(chunk):
    start_dt = chunk[0][0]
    end_dt   = chunk[-1][0]
    # +60s tail so a single-activity session still registers as 1 min,
    # not 0. Visualizes the action as having taken at least a minute.
    secs = max(int((end_dt - start_dt).total_seconds()) + 60, 60)
    first = chunk[0][1]
    return {
        'id':              f"inferred|{first.get('called_by_id') or ''}|{start_dt.isoformat()}|{end_dt.isoformat()}",
        'member_id':       first.get('called_by_id') or '',
        'member_name':     first.get('called_by_name') or '',
        'start_ts':        start_dt.isoformat(),
        'end_ts':          end_dt.isoformat(),
        'duration_seconds': secs,
        'goal':            0,
        'activity_count':  len(chunk),
        'inferred':        True,
    }


@app.route('/api/sales/sessions', methods=['GET'])
@require_sales_auth
def get_sessions_for_week():
    """Derive sessions from prospect_list activity within the week.
    Does NOT require session_logs to exist — every WA/call already lives
    in prospect_list, so sessions are reconstructed from the timestamps
    with a 30-minute gap as the splitter."""
    from datetime import date, timedelta
    scope      = request.args.get('scope', 'me')   # 'me' or 'team'
    mid_filter = request.args.get('member_id')
    week_param = request.args.get('week')
    try:
        week_start = date.fromisoformat(week_param) if week_param else date.today() - timedelta(days=date.today().weekday())
    except Exception:
        week_start = date.today() - timedelta(days=date.today().weekday())
    week_end = week_start + timedelta(days=7)
    try:
        q = db.table('prospect_list').select('id,company_name,called_by_id,called_by_name,called_at,contact_method').gte('called_at', week_start.isoformat()).lt('called_at', week_end.isoformat()).order('called_at', desc=False)
        if scope == 'me':
            my_id = _get_sales_member_id()
            q = q.eq('called_by_id', str(my_id))
        elif mid_filter:
            q = q.eq('called_by_id', str(mid_filter))
        res = q.execute()
    except Exception as e:
        print(f'[SESSIONS] prospect_list fetch error: {e}')
        return jsonify({'week_start': week_start.isoformat(), 'sessions': [], 'error': str(e)})
    # Group activities by member
    by_member = {}
    for r in (res.data or []):
        mid_r = str(r.get('called_by_id') or '')
        if not mid_r:
            continue
        if mid_r not in by_member:
            by_member[mid_r] = {'name': r.get('called_by_name') or '', 'activities': []}
        by_member[mid_r]['activities'].append(r)
    # Build inferred sessions per member
    sessions = []
    for mid_r, info in by_member.items():
        for s in _build_inferred_sessions(info['activities']):
            # Patch the member_name from the grouped info in case the row is missing it
            s['member_name'] = s.get('member_name') or info['name']
            sessions.append(s)
    sessions.sort(key=lambda s: s.get('start_ts') or '')
    return jsonify({'week_start': week_start.isoformat(), 'sessions': sessions})


@app.route('/api/sales/session-details/<path:sess_id>', methods=['GET'])
@require_sales_auth
def get_session_details(sess_id):
    """Return the activity that happened during a single session's time
    window. Session id is the synthetic form `inferred:<member_id>:<start_iso>:<end_iso>`
    produced by /api/sales/sessions."""
    if not sess_id.startswith('inferred|'):
        return jsonify({'error': 'bad_id', 'detail': 'expected inferred|* id'}), 400
    parts = sess_id.split('|', 3)
    if len(parts) < 4:
        return jsonify({'error': 'bad_id'}), 400
    _, mid, start_iso, end_iso = parts
    out = {
        'session': {
            'id':           sess_id,
            'member_id':    mid,
            'start_ts':     start_iso,
            'end_ts':       end_iso,
            'inferred':     True,
        },
        'phone_calls': [], 'whatsapps': [], 'leads_added': [], 'deals_closed': []
    }
    # Resolve member_name
    try:
        m = db.table('sales_members').select('name').eq('id', mid).limit(1).execute()
        if m.data:
            out['session']['member_name'] = m.data[0].get('name') or ''
    except Exception:
        pass
    # Activities (prospect_list) in window
    try:
        p_res = db.table('prospect_list').select('id,company_name,phone,called_at,contact_method').eq('called_by_id', mid).gte('called_at', start_iso).lte('called_at', end_iso).order('called_at').execute()
        for p in (p_res.data or []):
            if (p.get('contact_method') or '') == 'whatsapp':
                out['whatsapps'].append(p)
            else:
                out['phone_calls'].append(p)
    except Exception as e:
        print(f'[SESSION-DETAILS] prospects fetch: {e}')
    # Warm leads added during window
    try:
        l_res = db.table('warm_leads').select('id,company_name,created_at,pipeline_status').eq('added_by_id', mid).gte('created_at', start_iso).lte('created_at', end_iso).order('created_at').execute()
        out['leads_added'] = l_res.data or []
    except Exception as e:
        print(f'[SESSION-DETAILS] leads fetch: {e}')
    # Deals closed during window
    try:
        d_res = db.table('warm_leads').select('id,company_name,closed_at,closed_amount,commission_amount').eq('added_by_id', mid).eq('status', 'closed').gte('closed_at', start_iso).lte('closed_at', end_iso).order('closed_at').execute()
        out['deals_closed'] = d_res.data or []
    except Exception as e:
        print(f'[SESSION-DETAILS] deals fetch: {e}')
    # Compute duration the same way as get_sessions_for_week
    start_dt = _parse_iso_to_naive_utc(start_iso)
    end_dt   = _parse_iso_to_naive_utc(end_iso)
    if start_dt and end_dt:
        out['session']['duration_seconds'] = max(int((end_dt - start_dt).total_seconds()) + 60, 60)
    out['totals'] = {
        'phone_calls': len(out['phone_calls']),
        'whatsapps':   len(out['whatsapps']),
        'leads_added': len(out['leads_added']),
        'deals_closed': len(out['deals_closed']),
    }
    return jsonify(out)


@app.route('/api/sales/session/team', methods=['GET'])
@require_sales_auth
def get_team_calling_status():
    res = db.table('sales_members').select('id,name,is_calling,session_start,contract_type,last_known_streak').eq('status', 'active').execute()
    members = res.data or []
    for m in members:
        # Cheap path: trust last_known_streak (kept in sync by stats endpoint).
        # Avoids N+1 log scans on this endpoint which is polled frequently.
        if (m.get('contract_type') or 'legacy') == 'whatsapp':
            m['wa_streak'] = int(m.get('last_known_streak') or 0)
        else:
            m['wa_streak'] = 0
    return jsonify(members)


@app.route('/api/sales/streak', methods=['GET'])
@require_sales_auth
def get_my_streak():
    from datetime import date, timedelta
    mid = _get_sales_member_id()
    try:
        res = db.table('work_schedule').select('date').eq('member_id', str(mid)).eq('worked', True).execute()
        worked = set(r['date'] for r in res.data)
        streak = 0
        check = date.today()
        if check.isoformat() not in worked:
            check -= timedelta(days=1)
        while check.isoformat() in worked:
            streak += 1
            check -= timedelta(days=1)
        return jsonify({'streak': streak})
    except Exception:
        return jsonify({'streak': 0})


@app.route('/api/sales/feed', methods=['GET'])
@require_sales_auth
def get_activity_feed():
    try:
        res = db.table('activity_feed').select('*').order('created_at', desc=True).limit(25).execute()
        return jsonify(res.data)
    except Exception:
        return jsonify([])


@app.route('/api/sales/schedule/<date_str>', methods=['PUT'])
@require_sales_auth
def upsert_schedule_day(date_str):
    from datetime import date, timedelta
    mid = _get_sales_member_id()
    member_res = db.table('sales_members').select('name').eq('id', str(mid)).limit(1).execute()
    member_name = member_res.data[0]['name'] if member_res.data else ''
    try:
        d = date.fromisoformat(date_str)
    except Exception:
        return jsonify({'success': False, 'error': 'Ongeldige datum.'}), 400
    week_start = d - timedelta(days=d.weekday())
    payload = request.get_json(silent=True) or {}
    entry = {
        'member_id': str(mid),
        'member_name': member_name,
        'date': date_str,
        'week_start': week_start.isoformat(),
    }
    for field in ('planned_hours', 'actual_hours', 'worked'):
        if field in payload:
            entry[field] = payload[field]
    try:
        db.table('work_schedule').upsert(entry, on_conflict='member_id,date').execute()
        if payload.get('worked') is True:
            hours = payload.get('actual_hours') or payload.get('planned_hours') or ''
            hours_str = f'{hours}u ' if hours else ''
            _log_activity(mid, member_name, 'hours_worked', f'heeft {hours_str}gewerkt')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _log_status_change(entity_type, entity_id, old_status, new_status, mid=None, member_name=None):
    """Append a row to the status_history audit log. Best-effort: never raises,
    so calling endpoints stay responsive even if the table doesn't exist yet."""
    if old_status == new_status:
        return
    try:
        db.table('status_history').insert({
            'entity_type':     entity_type,
            'entity_id':       str(entity_id),
            'old_status':      old_status,
            'new_status':      new_status,
            'changed_by_id':   str(mid) if mid else None,
            'changed_by_name': member_name,
        }).execute()
    except Exception as e:
        # Table missing or RLS denied — log once and move on
        print(f'[STATUS-HISTORY] log failed for {entity_type}/{entity_id}: {e}')


# ── Canonical demo_status enum + legacy normalization ─────────────────────────
# Demos are shown live during the Calendly meeting → 'geleverd'/'gezien' are
# folded into 'show'. 'afspraak_bekijken'/'demo_zonder_forum' are old aliases.
DEMO_STATUS_CANONICAL = ('moet_gebouwd','klaar','show','no_show','geclosed','aanbetaling','volledig_betaald','afgehaakt')
DEMO_STATUS_LEGACY_MAP = {
    'demo_zonder_forum': 'klaar',
    'afspraak_bekijken': 'show',
    'geleverd':          'show',
    'gezien':            'show',
}

def _normalize_demo_status(s):
    """Return canonical demo_status for any incoming (possibly legacy) value.
    Returns None if the value isn't recognised at all (caller should 400)."""
    s = (s or '').strip()
    s = DEMO_STATUS_LEGACY_MAP.get(s, s)
    return s if s in DEMO_STATUS_CANONICAL else None


def _update_client_status_with_dropoff(cid, status, mid=None, member_name=None):
    """Apply demo_status update and auto-capture dropoff_stage when going to 'afgehaakt'.
    Also writes a status_history row for the transition."""
    update = {'demo_status': status}
    old_status = None
    try:
        old = db.table('clients').select('demo_status').eq('id', cid).limit(1).execute()
        if old.data:
            old_status = old.data[0].get('demo_status')
    except Exception as e:
        print(f'[CLIENT-STATUS] old status read failed: {e}')
    if status == 'afgehaakt' and old_status and old_status != 'afgehaakt':
        update['dropoff_stage'] = old_status
    elif status != 'afgehaakt':
        update['dropoff_stage'] = None
    try:
        db.table('clients').update(update).eq('id', cid).execute()
    except Exception:
        update.pop('dropoff_stage', None)
        db.table('clients').update(update).eq('id', cid).execute()
    _log_status_change('client', cid, old_status, status, mid=mid, member_name=member_name)


@app.route('/api/sales/clients/<cid>/status', methods=['PUT'])
@require_sales_auth
def sales_update_client_status(cid):
    data   = request.get_json(silent=True) or {}
    status = _normalize_demo_status(data.get('demo_status', ''))
    if status is None:
        return jsonify({'success': False, 'error': 'Ongeldige status.'}), 400
    mid = _get_sales_member_id()
    mname = None
    try:
        mres = db.table('sales_members').select('name').eq('id', mid).limit(1).execute() if mid else None
        mname = mres.data[0]['name'] if (mres and mres.data) else None
    except Exception:
        pass
    _update_client_status_with_dropoff(cid, status, mid=mid, member_name=mname)
    return jsonify({'success': True})

@app.route('/api/sales/clients/<cid>/contact', methods=['PUT'])
@require_sales_auth
def update_client_contact(cid):
    data = request.get_json(silent=True) or {}
    update = {}
    if 'phone' in data:
        update['phone'] = (data['phone'] or '').strip()
    if 'maps_url' in data:
        update['maps_url'] = (data['maps_url'] or '').strip()
    if update:
        try:
            db.table('clients').update(update).eq('id', cid).execute()
        except Exception:
            update.pop('maps_url', None)
            if update:
                db.table('clients').update(update).eq('id', cid).execute()
    return jsonify({'success': True})


@app.route('/api/sales/clients/<cid>/commission-paid', methods=['PUT'])
@require_sales_auth
def update_client_commission_paid(cid):
    data = request.get_json(silent=True) or {}
    paid = bool(data.get('paid', False))
    db.table('clients').update({'commission_paid': paid}).eq('id', cid).execute()
    return jsonify({'success': True})


@app.route('/api/sales/clients', methods=['GET'])
@require_sales_auth
def list_my_clients():
    res = db.table('clients').select('*').order('created_at', desc=True).execute()
    clients = res.data or []
    # Enrich met contact_method + wa_count uit gekoppelde warm_lead (op company_name)
    try:
        leads_res = db.table('warm_leads').select('id,company_name,contact_method').execute()
        lead_by_name = {}
        for l in (leads_res.data or []):
            nm = (l.get('company_name') or '').strip().lower()
            if nm:
                lead_by_name[nm] = l
        wa_res = db.table('wa_outreach_log').select('lead_id').eq('source', 'lead').execute()
        wa_counts = {}
        for r in (wa_res.data or []):
            lid = r.get('lead_id')
            if lid:
                wa_counts[str(lid)] = wa_counts.get(str(lid), 0) + 1
        for c in clients:
            nm = (c.get('name') or '').strip().lower()
            wl = lead_by_name.get(nm)
            if wl:
                c['contact_method'] = wl.get('contact_method')
                c['wa_count'] = wa_counts.get(str(wl['id']), 0)
            else:
                c['contact_method'] = None
                c['wa_count'] = 0
    except Exception as e:
        print(f'[CLIENTS] enrich failed: {e}')
    return jsonify(clients)


@app.route('/api/admin/clients', methods=['GET'])
@require_auth
def admin_list_clients():
    res = db.table('clients').select('*').order('created_at', desc=True).execute()
    return jsonify(res.data)


@app.route('/api/admin/clients/<cid>/status', methods=['PUT'])
@require_auth
def admin_update_client_status(cid):
    data   = request.get_json(silent=True) or {}
    status = _normalize_demo_status(data.get('demo_status', ''))
    if status is None:
        return jsonify({'success': False, 'error': 'Ongeldige status.'}), 400
    _update_client_status_with_dropoff(cid, status, member_name='admin')
    return jsonify({'success': True})


@app.route('/api/admin/clients/<cid>/payments', methods=['GET'])
@require_auth
def admin_list_payments(cid):
    res = db.table('client_payments').select('*').eq('client_id', cid).order('created_at', desc=True).execute()
    return jsonify(res.data)


@app.route('/api/admin/clients/<cid>/payments', methods=['POST'])
@require_auth
def admin_add_payment(cid):
    data   = request.get_json(silent=True) or {}
    amount = data.get('amount')
    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Ongeldig bedrag.'}), 400
    db.table('client_payments').insert({
        'client_id': cid,
        'amount': amount,
        'payment_type': data.get('payment_type', ''),
        'note': data.get('note', ''),
        'paid_at': data.get('paid_at') or datetime.utcnow().date().isoformat(),
    }).execute()
    return jsonify({'success': True})

@app.route('/api/sales/my-goal', methods=['GET'])
@require_sales_auth
def get_my_goal():
    mid = _get_sales_member_id()
    res = db.table('sales_members').select('monthly_goal').eq('id', mid).limit(1).execute()
    goal = float(res.data[0].get('monthly_goal') or 0) if res.data else 0
    return jsonify({'monthly_goal': goal})

@app.route('/api/sales/my-goal', methods=['PUT'])
@require_sales_auth
def set_my_goal():
    mid  = _get_sales_member_id()
    data = request.get_json(silent=True) or {}
    try:
        goal = float(data.get('monthly_goal', 0))
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Ongeldig bedrag.'}), 400
    db.table('sales_members').update({'monthly_goal': goal}).eq('id', mid).execute()
    return jsonify({'success': True})

@app.route('/api/sales/announcements', methods=['GET'])
def get_announcements():
    res = db.table('announcements').select('*').eq('active', True).order('created_at', desc=True).execute()
    return jsonify(res.data)

@app.route('/api/sales/announcements', methods=['POST'])
@require_auth
def create_announcement():
    try:
        data    = request.get_json(silent=True) or {}
        message = (data.get('message') or '').strip()
        if not message:
            return jsonify({'success': False, 'error': 'Bericht is leeg.'}), 400
        aid = str(int(datetime.utcnow().timestamp() * 1000))
        db.table('announcements').insert({'id': aid, 'message': message, 'active': True}).execute()
        return jsonify({'success': True, 'id': aid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sales/announcements/<aid>', methods=['DELETE'])
@require_auth
def delete_announcement(aid):
    try:
        db.table('announcements').update({'active': False}).eq('id', aid).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sales/meet', methods=['GET'])
def get_meet_link():
    try:
        res = db.table('settings').select('value').eq('key', 'sales_meet_link').limit(1).execute()
        link = res.data[0]['value'] if res.data else ''
        return jsonify({'link': link})
    except Exception as e:
        return jsonify({'link': '', 'error': str(e)})

@app.route('/api/sales/meet', methods=['PUT'])
@require_auth
def set_meet_link():
    try:
        data = request.get_json(silent=True) or {}
        link = (data.get('link') or '').strip()
        db.table('settings').upsert({'key': 'sales_meet_link', 'value': link}).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sales/whatsapp', methods=['GET'])
def get_whatsapp_link():
    try:
        res = db.table('settings').select('value').eq('key', 'sales_whatsapp_link').limit(1).execute()
        link = res.data[0]['value'] if res.data else ''
        return jsonify({'link': link})
    except Exception as e:
        return jsonify({'link': '', 'error': str(e)})

@app.route('/api/sales/whatsapp', methods=['PUT'])
@require_auth
def set_whatsapp_link():
    try:
        data = request.get_json(silent=True) or {}
        link = (data.get('link') or '').strip()
        db.table('settings').upsert({'key': 'sales_whatsapp_link', 'value': link}).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sales/pitch', methods=['GET'])
def get_pitch_script():
    try:
        res = db.table('settings').select('value').eq('key', 'sales_pitch_script').limit(1).execute()
        if res.data and res.data[0]['value']:
            import json as _json
            return jsonify(_json.loads(res.data[0]['value']))
    except Exception:
        pass
    return jsonify({'opener': '', 'qualify_questions': [], 'close_script': '', 'objections': []})

@app.route('/api/sales/pitch', methods=['PUT'])
@require_auth
def set_pitch_script():
    try:
        import json as _json
        data = request.get_json(silent=True) or {}
        db.table('settings').upsert({'key': 'sales_pitch_script', 'value': _json.dumps(data)}).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sales/wa-playbook', methods=['GET'])
def get_wa_playbook():
    try:
        res = db.table('settings').select('value').eq('key', 'sales_wa_playbook').limit(1).execute()
        if res.data and res.data[0]['value']:
            import json as _json
            return jsonify(_json.loads(res.data[0]['value']))
    except Exception:
        pass
    return jsonify({'intro': '', 'sections': []})

@app.route('/api/sales/wa-playbook', methods=['PUT'])
@require_auth
def set_wa_playbook():
    """Save WhatsApp playbook. Requires a strategy_name field — if it differs
    from the currently-active strategy, close the previous one and start a new
    one (so we can correlate KPI timeframes with which strategy was live)."""
    try:
        import json as _json
        data = request.get_json(silent=True) or {}
        strategy_name = (data.pop('strategy_name', '') or '').strip()
        if not strategy_name:
            return jsonify({'success': False, 'error': 'Strategy-naam is verplicht.'}), 400

        # 1) Save playbook content as before
        db.table('settings').upsert({'key': 'sales_wa_playbook', 'value': _json.dumps(data)}).execute()

        # 2) Maintain the wa_strategies timeline
        try:
            active = db.table('wa_strategies').select('id,name').is_('ended_at', 'null').order('started_at', desc=True).limit(1).execute()
            cur = active.data[0] if active.data else None
            if not cur or (cur.get('name') or '') != strategy_name:
                now_iso = datetime.utcnow().isoformat()
                if cur:
                    db.table('wa_strategies').update({'ended_at': now_iso}).eq('id', cur['id']).execute()
                db.table('wa_strategies').insert({'name': strategy_name, 'started_at': now_iso}).execute()
        except Exception as e:
            # Table missing or insert failed — log but don't break the save
            print(f'[WA-STRATEGY] timeline update failed: {e}')

        return jsonify({'success': True, 'strategy_name': strategy_name})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sales/wa-strategies', methods=['GET'])
def list_wa_strategies():
    """Returns the full strategy timeline so the KPI dashboard can show which
    strategy was active during any given timeframe."""
    try:
        res = db.table('wa_strategies').select('*').order('started_at', desc=True).execute()
        return jsonify(res.data or [])
    except Exception:
        return jsonify([])


@app.route('/api/sales/wa-strategies/current', methods=['GET'])
def get_current_wa_strategy():
    """Currently active strategy — used to prefill the modal on the next save."""
    try:
        res = db.table('wa_strategies').select('*').is_('ended_at', 'null').order('started_at', desc=True).limit(1).execute()
        if res.data:
            return jsonify(res.data[0])
    except Exception:
        pass
    return jsonify(None)

@app.route('/api/sales/members', methods=['GET'])
@require_sales_auth
def list_sales_members():
    res = db.table('sales_members').select('*').eq('status', 'active').order('created_at').execute()
    return jsonify(res.data)

@app.route('/api/sales/apply', methods=['POST'])
def sales_apply():
    from werkzeug.security import generate_password_hash
    data         = request.get_json(silent=True) or {}
    name             = (data.get('name') or '').strip()
    email            = (data.get('email') or '').strip().lower()
    phone            = (data.get('phone') or '').strip()
    city             = (data.get('city') or '').strip()
    motivation       = (data.get('motivation') or '').strip()
    sales_background = (data.get('sales_background') or '').strip()
    discipline       = (data.get('discipline') or '').strip()
    rejection        = (data.get('rejection') or '').strip()
    strengths        = (data.get('strengths') or '').strip()
    weaknesses       = (data.get('weaknesses') or '').strip()
    hours_per_week   = (data.get('hours_per_week') or '').strip()
    referred_by      = (data.get('referred_by') or '').strip()
    ref_code_used    = (data.get('ref_code') or '').strip()
    password         = (data.get('password') or '').strip()

    if not name or not email or not phone:
        return jsonify({'success': False, 'error': 'Naam, email en telefoonnummer zijn verplicht.'}), 400
    if not password or len(password) < 6:
        return jsonify({'success': False, 'error': 'Kies een wachtwoord van minimaal 6 tekens.'}), 400

    try:
        existing = db.table('sales_members').select('id').eq('email', email).execute()
        if existing.data:
            return jsonify({'success': False, 'error': 'Dit e-mailadres is al geregistreerd.'}), 409
    except Exception as e:
        print(f"[APPLY ERROR] email check failed: {e}")
        return jsonify({'success': False, 'error': f'Database fout bij controle: {e}'}), 500

    # Pack all answers into motivation field (no extra columns needed)
    parts = []
    if motivation:        parts.append(f"[MOTIVATIE]\n{motivation}")
    if sales_background:  parts.append(f"[ACHTERGROND]\n{sales_background}")
    if discipline:        parts.append(f"[DISCIPLINE]\n{discipline}")
    if rejection:         parts.append(f"[REJECTION]\n{rejection}")
    if strengths:         parts.append(f"[STERKTES]\n{strengths}")
    if weaknesses:        parts.append(f"[ZWAKTES]\n{weaknesses}")
    if hours_per_week:    parts.append(f"[UREN/WEEK]\n{hours_per_week}")
    full_motivation = "\n\n".join(parts)

    ref_code = _unique_sales_ref()
    mid = str(int(datetime.utcnow().timestamp() * 1000))
    try:
        db.table('sales_members').insert({
            'id': mid, 'name': name, 'email': email, 'phone': phone,
            'city': city, 'motivation': full_motivation,
            'referred_by_name': referred_by, 'referred_by_code': ref_code_used,
            'ref_code': ref_code, 'status': 'applicant',
            'password_hash': generate_password_hash(password, method='pbkdf2:sha256'),
            'first_sale_counted': False, 'bonus_owed': 0,
        }).execute()
    except Exception as e:
        print(f"[APPLY ERROR] insert failed for {email}: {e}")
        return jsonify({'success': False, 'error': f'Opslaan mislukt: {e}'}), 500

    print(f"[APPLY] {name} | {email} | via: {referred_by or 'direct'}")
    return jsonify({'success': True, 'id': mid})

@app.route('/api/sales/applicants', methods=['GET'])
@require_auth
def list_sales_applicants():
    res = db.table('sales_members').select('*').order('created_at', desc=True).execute()
    members = res.data
    earned_res = db.table('warm_leads').select('added_by_id,commission_amount').eq('status', 'closed').execute()
    totals = {}
    for r in earned_res.data:
        mid = r['added_by_id']
        totals.setdefault(mid, 0.0)
        totals[mid] += float(r['commission_amount'] or 0)
    for m in members:
        m['total_earned'] = round(totals.get(m['id'], 0.0), 2)
        if (m.get('contract_type') or 'legacy') == 'whatsapp':
            try:
                m['whatsapp_rate'] = _compute_whatsapp_rate(m['id'])
            except Exception as e:
                print(f"[WA-RATE] compute failed for {m['id']}: {e}")
                m['whatsapp_rate'] = 0.25
    return jsonify(members)

@app.route('/api/sales/applicants/<mid>/status', methods=['PUT'])
@require_auth
def update_sales_member_status(mid):
    data   = request.get_json(silent=True) or {}
    status = data.get('status')
    if status not in ('active', 'rejected', 'applicant'):
        return jsonify({'success': False, 'error': 'Ongeldige status.'}), 400
    db.table('sales_members').update({'status': status}).eq('id', mid).execute()
    return jsonify({'success': True})

@app.route('/api/sales/applicants/<mid>', methods=['DELETE'])
@require_auth
def delete_sales_member(mid):
    db.table('warm_leads').delete().eq('added_by_id', mid).execute()
    db.table('sales_members').delete().eq('id', mid).execute()
    return jsonify({'success': True})

@app.route('/api/sales/applicants/<mid>/contract', methods=['PUT'])
@require_auth
def set_sales_member_contract(mid):
    data = request.get_json(silent=True) or {}
    url  = (data.get('contract_url') or '').strip()
    db.table('sales_members').update({'contract_url': url}).eq('id', mid).execute()
    return jsonify({'success': True})

@app.route('/api/sales/applicants/<mid>/reset-password', methods=['PUT'])
@require_auth
def reset_sales_password(mid):
    from werkzeug.security import generate_password_hash
    data     = request.get_json(silent=True) or {}
    password = (data.get('password') or '').strip()
    if not password or len(password) < 6:
        return jsonify({'success': False, 'error': 'Wachtwoord minimaal 6 tekens.'}), 400
    db.table('sales_members').update({'password_hash': generate_password_hash(password, method='pbkdf2:sha256')}).eq('id', mid).execute()
    return jsonify({'success': True})


# ── Prospect List (Bel Lijst) ────────────────────────────────────────────────

# Stricter dedup helpers (shared by /api/prospects/import and the admin
# cleanup endpoint). Goal: catch the duplicates that the older lenient
# normalisation slipped past.
#  - Phone: digits only + last 9, so "0612345678", "+31612345678" and
#    "0031612345678" all collapse to "612345678" (the canonical NL mobile
#    suffix). Falls back to the full digit string for sub-9-digit inputs.
#  - Name : lowercase, strip punctuation (B.V. ↔ BV), collapse repeated
#    whitespace so "ABC  Auto" matches "ABC Auto".
def _norm_phone_dedupe(p):
    digits = ''.join(c for c in str(p or '') if c.isdigit())
    return digits[-9:] if len(digits) >= 9 else digits

def _norm_name_dedupe(n):
    s = str(n or '').lower().strip()
    s = re.sub(r'[^\w\s]', ' ', s, flags=re.UNICODE)   # drop punctuation
    s = re.sub(r'\s+', ' ', s).strip()                  # collapse spaces
    return s


@app.route('/api/prospects', methods=['GET'])
@require_sales_auth
def list_prospects():
    try:
        # PostgREST caps each response at 1000 rows — page through with .range()
        # so the bellijst shows the full list even beyond 1000 prospects.
        all_rows = []
        page_size = 1000
        start = 0
        while True:
            res = (db.table('prospect_list').select('*')
                   .order('called').order('created_at')
                   .range(start, start + page_size - 1).execute())
            batch = res.data or []
            all_rows.extend(batch)
            if len(batch) < page_size:
                break
            start += page_size
        return jsonify(all_rows)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/prospects/import', methods=['POST'])
@require_sales_auth
def import_prospects():
    try:
        data  = request.get_json(silent=True) or {}
        rows  = data.get('rows', [])
        if not rows:
            return jsonify({'success': False, 'error': 'Geen rijen gevonden.'}), 400

        # Stricter dedup so +31/0/0031 variants and punctuation differences all
        # collapse — see _norm_phone_dedupe / _norm_name_dedupe above.
        norm_phone = _norm_phone_dedupe
        norm_name  = _norm_name_dedupe

        # Page past the 1000-row cap so the duplicate check sees the FULL
        # existing list (otherwise imports beyond 1000 stop catching dupes).
        def _fetch_all(table, columns):
            out = []
            start = 0
            while True:
                res = db.table(table).select(columns).range(start, start + 999).execute()
                batch = res.data or []
                out.extend(batch)
                if len(batch) < 1000:
                    break
                start += 1000
            return out

        existing = _fetch_all('warm_leads', 'phone,company_name') + _fetch_all('prospect_list', 'phone,company_name')
        blocked_phones = set()
        blocked_names  = set()
        for r in existing:
            n = norm_phone(r.get('phone', ''))
            if n: blocked_phones.add(n)
            nm = norm_name(r.get('company_name', ''))
            if nm: blocked_names.add(nm)

        batch_id = str(int(datetime.utcnow().timestamp() * 1000))
        now = datetime.utcnow().isoformat()
        records = []
        skipped = 0
        # also track within-CSV duplicates
        seen_phones = set()
        seen_names  = set()
        for i, r in enumerate(rows):
            name  = str(r.get('company_name') or r.get('name') or '').strip()
            phone = str(r.get('phone') or '').strip()
            if not name and not phone:
                continue
            np = norm_phone(phone)
            nn = norm_name(name)
            # skip if phone or name already exists (DB or earlier in this CSV)
            if (np and (np in blocked_phones or np in seen_phones)) or \
               (nn and (nn in blocked_names  or nn in seen_names)):
                skipped += 1
                continue
            if np: seen_phones.add(np)
            if nn: seen_names.add(nn)
            raw_rating = r.get('rating')
            try:
                rating = round(float(str(raw_rating).replace(',', '.'))) if raw_rating not in (None, '') else None
            except (ValueError, TypeError):
                rating = None
            # Skip low-quality prospects (rating <= 2). Prospects without a
            # rating slip through — better to keep ambiguous data than drop it.
            if rating is not None and rating <= 2:
                skipped += 1
                continue
            # Optional review count (number of Google reviews) — many scrapers
            # output 'review_count', 'reviews_count' or 'num_reviews'
            raw_rc = r.get('review_count') or r.get('reviews_count') or r.get('num_reviews')
            try:
                review_count = int(float(str(raw_rc).replace(',', '.'))) if raw_rc not in (None, '') else None
            except (ValueError, TypeError):
                review_count = None
            records.append({
                'id': f"{batch_id}_{i}",
                'company_name': name, 'phone': phone,
                'rating': rating,
                'review_count': review_count,
                'maps_url': str(r.get('maps_url') or '').strip(),
                'city': str(r.get('city') or '').strip(),
                'niche': str(r.get('niche') or '').strip(),
                'website': str(r.get('website') or '').strip(),
                'website_url': str(r.get('website_url') or r.get('site_url') or '').strip(),
                'booking': str(r.get('booking') or '').strip(),
                'booking_url': str(r.get('booking_url') or '').strip(),
                'called': False,
                'import_batch': batch_id,
                'created_at': now,
            })
        if not records:
            return jsonify({'success': False, 'error': f'Geen nieuwe prospects — alle {skipped} rijen staan al in de bel lijst of warm leads.'}), 400

        # Insert in chunks so a large CSV doesn't hit payload/timeout limits.
        # If optional columns (website_url/booking_url, then website/booking)
        # don't exist in the schema, drop them once and apply to all chunks.
        def _strip(recs, cols):
            for rec in recs:
                for c in cols:
                    rec.pop(c, None)

        CHUNK = 500
        dropped = set()
        idx = 0
        while idx < len(records):
            chunk = records[idx:idx + CHUNK]
            if dropped:
                _strip(chunk, dropped)
            while True:
                try:
                    db.table('prospect_list').insert(chunk).execute()
                    break
                except Exception as e:
                    msg = str(e).lower()
                    new_drop = None
                    if 'review_count' in msg and 'review_count' not in dropped:
                        new_drop = {'review_count'}
                    elif (('website_url' in msg or 'booking_url' in msg)
                            and not {'website_url', 'booking_url'} <= dropped):
                        new_drop = {'website_url', 'booking_url'}
                    elif (any(k in msg for k in ('website', 'booking', 'column', 'schema'))
                            and not {'website', 'booking'} <= dropped):
                        new_drop = {'website', 'booking'}
                    if new_drop:
                        dropped |= new_drop
                        _strip(chunk, new_drop)
                        print(f"[PROSPECTS] dropping unsupported cols {new_drop} and retrying chunk")
                    else:
                        raise
            idx += CHUNK
        print(f"[PROSPECTS] Imported {len(records)} rows, skipped {skipped} duplicates (batch {batch_id})")
        return jsonify({'success': True, 'count': len(records), 'skipped': skipped, 'batch_id': batch_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/prospects/<pid>/call', methods=['PUT'])
@require_sales_auth
def mark_prospect_called(pid):
    try:
        data = request.get_json(silent=True) or {}
        method = data.get('contact_method') if data.get('contact_method') in ('phone', 'whatsapp') else 'phone'
        mid = _get_sales_member_id()
        res = db.table('sales_members').select('name').eq('id', mid).limit(1).execute()
        member_name = res.data[0]['name'] if res.data else 'Onbekend'
        update = {
            'called': True,
            'called_by_id': str(mid),
            'called_by_name': member_name,
            'called_at': datetime.utcnow().isoformat(),
            'no_answer': False,  # marking as Benaderd clears any previous niet-opgenomen state
        }
        try:
            update['contact_method'] = method
            db.table('prospect_list').update(update).eq('id', pid).execute()
        except Exception:
            # Either contact_method or no_answer column missing — retry without each
            update.pop('contact_method', None)
            try:
                db.table('prospect_list').update(update).eq('id', pid).execute()
            except Exception:
                update.pop('no_answer', None)
                db.table('prospect_list').update(update).eq('id', pid).execute()
        return jsonify({'success': True, 'called_by_name': member_name, 'contact_method': method})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/prospects/<pid>/no-answer', methods=['PUT'])
@require_sales_auth
def mark_prospect_no_answer(pid):
    try:
        mid = _get_sales_member_id()
        res = db.table('sales_members').select('name').eq('id', mid).limit(1).execute()
        member_name = res.data[0]['name'] if res.data else 'Onbekend'
        update = {
            'called': False,
            'no_answer': True,
            'called_by_id': str(mid),
            'called_by_name': member_name,
            'called_at': datetime.utcnow().isoformat(),
        }
        try:
            db.table('prospect_list').update(update).eq('id', pid).execute()
        except Exception as e:
            msg = str(e).lower()
            if 'no_answer' in msg or 'column' in msg or 'schema' in msg:
                return jsonify({'success': False, 'error': "Voeg eerst de 'no_answer' kolom toe in Supabase (ALTER TABLE prospect_list ADD COLUMN no_answer boolean NOT NULL DEFAULT false)."}), 500
            raise
        return jsonify({'success': True, 'called_by_name': member_name})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/prospects/<pid>/uncall', methods=['PUT'])
@require_sales_auth
def unmark_prospect_called(pid):
    try:
        update = {
            'called': False, 'no_answer': False, 'called_by_id': None,
            'called_by_name': None, 'called_at': None,
        }
        try:
            db.table('prospect_list').update(update).eq('id', pid).execute()
        except Exception:
            update.pop('no_answer', None)
            db.table('prospect_list').update(update).eq('id', pid).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/prospects/<pid>/website-status', methods=['PUT'])
@require_sales_auth
def set_prospect_website_status(pid):
    """Mark a prospect's website as 'broken', 'outdated', or clear (NULL).
    Used by Hermes multi-agent feature to target the right cold-call agent."""
    data = request.get_json(silent=True) or {}
    status = (data.get('website_status') or '').strip().lower()
    if status not in ('broken', 'outdated', '', 'clear', 'good', 'none'):
        return jsonify({'success': False, 'error': 'Ongeldige website_status — alleen "broken", "outdated", of "clear"/"good"/"none"/leeg (allen mappen naar geen tag).'}), 400
    if status in ('', 'clear', 'good', 'none'):
        new_val = None
    else:
        new_val = status
    try:
        db.table('prospect_list').update({'website_status': new_val}).eq('id', pid).execute()
        return jsonify({'success': True, 'website_status': new_val})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:200]}), 500

@app.route('/api/prospects/<pid>', methods=['DELETE'])
@require_sales_auth
def delete_prospect(pid):
    try:
        db.table('prospect_list').delete().eq('id', pid).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/prospects/batch/<batch_id>', methods=['DELETE'])
@require_auth
def delete_prospect_batch(batch_id):
    try:
        db.table('prospect_list').delete().eq('import_batch', batch_id).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Admin prospect management (admin-only) ─────────────────────────────────────

@app.route('/api/admin/prospects', methods=['GET'])
@require_auth
def admin_list_prospects():
    try:
        # Page past the 1000-row PostgREST cap
        all_rows = []
        page_size = 1000
        start = 0
        while True:
            res = (db.table('prospect_list').select('*')
                   .order('created_at', desc=True)
                   .range(start, start + page_size - 1).execute())
            batch = res.data or []
            all_rows.extend(batch)
            if len(batch) < page_size:
                break
            start += page_size
        return jsonify(all_rows)
    except Exception as e:
        return jsonify([])

@app.route('/api/admin/prospects/<pid>', methods=['DELETE'])
@require_auth
def admin_delete_prospect(pid):
    try:
        db.table('prospect_list').delete().eq('id', pid).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/admin/prospects/dedup-cleanup', methods=['POST'])
@require_auth
def admin_dedup_cleanup():
    """One-time cleanup of accumulated duplicates in prospect_list.
    Groups records that share a normalized phone OR name (mirrors the
    OR-dedup that import uses), keeps the newest in each group, and
    deletes the older copies — but only when they're not 'called' so we
    never throw away outreach history. Returns a summary.

    Optional query params:
      ?dry_run=1   — count only, no deletes (default: false)
      ?limit=N     — page only the first N records (for spot-checks)
    """
    dry_run = request.args.get('dry_run') in ('1', 'true', 'yes')
    limit = request.args.get('limit', type=int)

    try:
        # 1) Page through everything we need to dedupe on
        all_rows = []
        page_size = 1000
        start = 0
        while True:
            res = (db.table('prospect_list')
                   .select('id, company_name, phone, called, created_at')
                   .order('created_at', desc=True)   # newest first
                   .range(start, start + page_size - 1).execute())
            batch = res.data or []
            all_rows.extend(batch)
            if len(batch) < page_size:
                break
            start += page_size
            if limit and len(all_rows) >= limit:
                all_rows = all_rows[:limit]
                break

        # 2) Union-find on (phone OR name). Two records get merged into the
        #    same group if they share *either* a normalized phone or a
        #    normalized name. Mirrors the OR-dedup in import_prospects.
        parent = {r['id']: r['id'] for r in all_rows}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]   # path compression
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        phone_seed = {}    # norm_phone -> first record id seen
        name_seed  = {}    # norm_name  -> first record id seen
        for r in all_rows:
            rid = r['id']
            pk = _norm_phone_dedupe(r.get('phone'))
            nk = _norm_name_dedupe(r.get('company_name'))
            if pk:
                if pk in phone_seed:
                    union(rid, phone_seed[pk])
                else:
                    phone_seed[pk] = rid
            if nk:
                if nk in name_seed:
                    union(rid, name_seed[nk])
                else:
                    name_seed[nk] = rid

        # 3) Bucket records by their root component
        groups = {}
        for r in all_rows:
            groups.setdefault(find(r['id']), []).append(r)

        # 4) Within each dupe-group, apply Julian's rule:
        #    - NEVER delete a 'called' record (called == benaderd is precious
        #      history — outreach happened, do not throw away).
        #    - If ANY record in the group is called, the uncalled duplicates
        #      MUST be deleted so the same person doesn't show up on the
        #      bellijst again.
        #    - If no record in the group is called, keep the NEWEST and
        #      delete the rest (standard dedup).
        # Previously this loop always kept recs[0] (newest) which silently
        # kept an uncalled new import alongside an older called copy, so the
        # bellijst still surfaced the same person — exactly Timon's bug.
        to_delete = []
        preserved_called = 0
        dupe_groups = 0
        for recs in groups.values():
            if len(recs) <= 1:
                continue
            dupe_groups += 1
            called = [r for r in recs if r.get('called')]
            uncalled = [r for r in recs if not r.get('called')]
            if called:
                # Keep ALL called records (per Julian's hard rule). Delete every
                # uncalled duplicate in the group — Timon must never re-dial.
                preserved_called += len(called)
                for r in uncalled:
                    to_delete.append(r['id'])
            else:
                # No history yet — keep the newest of the group, drop the rest.
                # recs were fetched newest-first, so recs[0] is newest.
                for r in recs[1:]:
                    to_delete.append(r['id'])

        # Safety check: assert no called record was queued for deletion.
        # If this ever triggers, the dedup logic above has a regression.
        called_ids = {r['id'] for r in all_rows if r.get('called')}
        accidental = called_ids.intersection(to_delete)
        if accidental:
            print(f"[DEDUP-CLEANUP] ABORTED: would have deleted {len(accidental)} called records: {list(accidental)[:5]}")
            return jsonify({
                'success': False,
                'error': f'Safety check faalde — {len(accidental)} called records waren bijna verwijderd. Geen wijzigingen gedaan.',
                'accidental_called_ids': list(accidental)[:20],
            }), 500

        # 5) Apply deletions in chunks (unless dry-run)
        if not dry_run and to_delete:
            DEL_CHUNK = 100
            for i in range(0, len(to_delete), DEL_CHUNK):
                chunk = to_delete[i:i + DEL_CHUNK]
                db.table('prospect_list').delete().in_('id', chunk).execute()

        return jsonify({
            'success':           True,
            'dry_run':           dry_run,
            'total_scanned':     len(all_rows),
            'dupe_groups':       dupe_groups,
            'would_delete' if dry_run else 'deleted': len(to_delete),
            'preserved_called':  preserved_called,
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Callback sub-funnel ──────────────────────────────────────────────────
# Cohort = warm_leads that ever transitioned into 'ik_bel_terug' or
# 'zij_bellen_terug' (tracked via status_history) PLUS the ones currently
# in callback (fallback for leads from before history-logging was added).
# Output: how many are still stuck in callback, how many progressed onward,
# how many afgehaakt — so the user can judge callback as its own micro-funnel
# independent of the main warm-lead → forum_gestuurd flow.
@app.route('/api/sales/kpi-callback-funnel', methods=['GET'])
@require_sales_auth
def kpi_callback_funnel():
    start_iso = request.args.get('from')
    end_iso   = request.args.get('to')
    src       = (request.args.get('source') or 'all').strip()
    member_id = (request.args.get('member_id') or '').strip() or None

    # 1) Collect cohort via status_history
    cohort_ids = set()
    try:
        hist_res = (db.table('status_history')
                    .select('entity_id')
                    .eq('entity_type', 'warm_lead')
                    .in_('new_status', ['ik_bel_terug', 'zij_bellen_terug'])
                    .execute())
        for h in (hist_res.data or []):
            if h.get('entity_id'):
                cohort_ids.add(str(h['entity_id']))
    except Exception as e:
        print(f'[CALLBACK-FUNNEL] status_history fetch failed: {e}')

    # 2) Fallback: also include anyone currently in callback (legacy data
    # from before status_history existed)
    try:
        cur_res = (db.table('warm_leads')
                   .select('id')
                   .in_('pipeline_status', ['ik_bel_terug', 'zij_bellen_terug'])
                   .execute())
        for r in (cur_res.data or []):
            if r.get('id'):
                cohort_ids.add(str(r['id']))
    except Exception as e:
        print(f'[CALLBACK-FUNNEL] current-callback fetch failed: {e}')

    if not cohort_ids:
        return jsonify({
            'total': 0,
            'still_in_callback': {'count': 0, 'ik_bel_terug': 0, 'zij_bellen_terug': 0},
            'progressed': {'count': 0, 'forum_nog_sturen': 0, 'forum_gestuurd': 0, 'forum_gezien': 0, 'forum_ingevuld': 0, 'gesloten': 0},
            'afgehaakt': 0, 'progression_pct': 0.0,
            'period': {'from': start_iso, 'to': end_iso}, 'source': src,
        })

    # 3) Fetch the current state of each cohort lead, batched to dodge
    # PostgREST's URL-length limit on .in_()
    rows = []
    cohort_list = list(cohort_ids)
    BATCH = 80
    for i in range(0, len(cohort_list), BATCH):
        chunk = cohort_list[i:i+BATCH]
        try:
            q = (db.table('warm_leads')
                 .select('id, pipeline_status, status, contact_method, added_by_id, created_at')
                 .in_('id', chunk))
            if start_iso: q = q.gte('created_at', start_iso)
            if end_iso:   q = q.lt('created_at', end_iso)
            if member_id: q = q.eq('added_by_id', member_id)
            r = q.execute()
            rows.extend(r.data or [])
        except Exception as e:
            print(f'[CALLBACK-FUNNEL] lookup chunk failed: {e}')

    if src in ('phone', 'whatsapp', 'extern'):
        rows = [r for r in rows if r.get('contact_method') == src]

    # 4) Bucket by current pipeline_status
    still = {'count': 0, 'ik_bel_terug': 0, 'zij_bellen_terug': 0}
    progressed = {'count': 0, 'forum_gestuurd': 0, 'forum_gezien': 0, 'forum_ingevuld': 0, 'gesloten': 0}
    afgehaakt = 0
    for r in rows:
        ps = r.get('pipeline_status')
        if ps in still:
            still['count'] += 1
            still[ps] += 1
        elif ps in progressed:
            progressed['count'] += 1
            progressed[ps] += 1
        elif ps == 'afgehaakt':
            afgehaakt += 1

    total = len(rows)
    progression_pct = round(progressed['count'] / total * 100, 1) if total else 0.0
    return jsonify({
        'total': total,
        'still_in_callback': still,
        'progressed': progressed,
        'afgehaakt': afgehaakt,
        'progression_pct': progression_pct,
        'period': {'from': start_iso, 'to': end_iso},
        'source': src,
    })


# ── KPI extras: supplementary info shown UNDER the Volledige funnel chart
# One round-trip that returns all eight sub-blocks (geld, diagnose, bronnen,
# leaderboard, actie_queue, vs_prev, velocity, strategy). Each block has a
# graceful empty shape so the frontend can render even when data is missing.
@app.route('/api/sales/kpi-extras', methods=['GET'])
@require_sales_auth
def kpi_extras():
    """Supplementary cards under the Volledige funnel chart. Wraps the whole
    body so the frontend always gets a JSON response — even on internal
    errors — rather than a bare 500 with HTML."""
    try:
        return _kpi_extras_impl()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f'[KPI-EXTRAS] uncaught: {e}')
        return jsonify({'success': False, 'error': str(e)[:300]}), 500


def _kpi_extras_impl():
    from datetime import timezone, timedelta
    period = (request.args.get('period') or '').strip()
    preset = (request.args.get('preset') or '').strip()
    f_str  = (request.args.get('from')   or '').strip()
    t_str  = (request.args.get('to')     or '').strip()
    member_id = (request.args.get('member_id') or '').strip()
    src_filter = (request.args.get('source') or 'all').strip()
    if src_filter not in ('all', 'phone', 'whatsapp', 'extern'):
        src_filter = 'all'

    now = datetime.now(timezone.utc)
    start, end = None, None
    def _parse_d(s):
        try: return datetime.strptime(s, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        except Exception: return None
    if f_str or t_str:
        start = _parse_d(f_str); end = _parse_d(t_str)
        if end: end = end + timedelta(days=1)
    elif preset:
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if   preset == 'today':       start, end = today, today + timedelta(days=1)
        elif preset == 'yesterday':   start, end = today - timedelta(days=1), today
        elif preset == 'this_week':   start = today - timedelta(days=now.weekday()); end = start + timedelta(days=7)
        elif preset == 'last_week':   start = today - timedelta(days=now.weekday() + 7); end = start + timedelta(days=7)
        elif preset == 'this_month':  start = today.replace(day=1); end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        elif preset == 'last_month':
            first_this = today.replace(day=1); end = first_this
            start = (first_this - timedelta(days=1)).replace(day=1)
    else:
        if   period == 'daily':   start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == 'weekly':  start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == 'monthly': start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # SAFETY: never let the endpoint do an unbounded all-time fetch — that's
    # what was causing the production 500 (gunicorn worker timeout when the
    # warm_leads + clients + status_history fetches all run unbounded).
    # Default to the last 90 days when no period is provided.
    if not start:
        start = now - timedelta(days=90)
    if not end:
        end = now + timedelta(days=1)

    start_iso = start.isoformat() if start else None
    end_iso   = end.isoformat() if end else None
    period_len_days = max(1, (end - start).days) if (start and end) else 30
    prev_start = (start - timedelta(days=period_len_days)) if start else None
    prev_end   = start if start else None
    prev_start_iso = prev_start.isoformat() if prev_start else None
    prev_end_iso   = prev_end.isoformat() if prev_end else None

    # ── Fetch helpers ─────────────────────────────────────────────────
    # SAFETY: progressively fall back to a smaller column set if the wide
    # select fails (e.g. a column doesn't exist on this DB yet). Each tier
    # is the union of stuff the downstream code needs at that level.
    _WARM_COLS_FULL = 'id,company_name,phone,contact_method,pipeline_status,status,added_by_id,added_by_name,closed_amount,commission_amount,closed_at,created_at,commission_rate_locked,followup_date,followup_done,meeting_state,meeting_outcome,meeting_scheduled_at,meeting_no_show_followup_at,meeting_link_sent_at'
    _WARM_COLS_LITE = 'id,company_name,phone,contact_method,pipeline_status,status,added_by_id,added_by_name,closed_amount,commission_amount,closed_at,created_at,followup_date,meeting_state,meeting_outcome,meeting_scheduled_at,meeting_no_show_followup_at'
    _WARM_COLS_MIN  = 'id,company_name,contact_method,pipeline_status,status,added_by_id,closed_amount,commission_amount,closed_at,created_at'

    def _fetch_warm_window(s_iso, e_iso):
        for cols in (_WARM_COLS_FULL, _WARM_COLS_LITE, _WARM_COLS_MIN):
            rows = []; page = 1000; offset = 0; tier_ok = True
            try:
                while True:
                    q = db.table('warm_leads').select(cols)
                    if s_iso: q = q.gte('created_at', s_iso)
                    if e_iso: q = q.lt('created_at', e_iso)
                    res = q.range(offset, offset + page - 1).execute()
                    batch = res.data or []
                    rows.extend(batch)
                    if len(batch) < page: break
                    offset += page
                return rows
            except Exception as e:
                print(f'[KPI-EXTRAS] warm fetch tier failed ({len(cols)} chars): {e}')
                tier_ok = False
        # Last-resort: every tier failed → return empty so the rest can render
        return []

    def _filter_member(rows):
        if member_id:
            return [r for r in rows if str(r.get('added_by_id') or '') == member_id]
        return rows

    def _filter_src(rows, allow_extern_no_phone=True):
        if src_filter == 'all': return rows
        return [r for r in rows if r.get('contact_method') == src_filter]

    warm_now  = _filter_src(_filter_member(_fetch_warm_window(start_iso, end_iso)))
    warm_prev = _filter_src(_filter_member(_fetch_warm_window(prev_start_iso, prev_end_iso))) if prev_start_iso else []

    # Clients in window (for revenue + show metrics)
    _CLIENT_COLS_FULL = 'id,name,demo_status,total_amount,commission_amount,created_at,added_by_id,added_by_name,contact_method,meeting_state,meeting_outcome,meeting_scheduled_at,meeting_no_show_followup_at'
    _CLIENT_COLS_LITE = 'id,name,demo_status,total_amount,commission_amount,created_at,added_by_id,contact_method,meeting_state,meeting_outcome'
    _CLIENT_COLS_MIN  = 'id,name,demo_status,total_amount,commission_amount,created_at'
    def _fetch_clients_window(s_iso, e_iso):
        for cols in (_CLIENT_COLS_FULL, _CLIENT_COLS_LITE, _CLIENT_COLS_MIN):
            rows = []; page = 1000; offset = 0
            try:
                while True:
                    q = db.table('clients').select(cols)
                    if s_iso: q = q.gte('created_at', s_iso)
                    if e_iso: q = q.lt('created_at', e_iso)
                    res = q.range(offset, offset + page - 1).execute()
                    batch = res.data or []
                    rows.extend(batch)
                    if len(batch) < page: break
                    offset += page
                return rows
            except Exception as e:
                print(f'[KPI-EXTRAS] clients fetch tier failed ({len(cols)} chars): {e}')
        return []
    clients_now  = _filter_src(_filter_member(_fetch_clients_window(start_iso, end_iso)))
    clients_prev = _filter_src(_filter_member(_fetch_clients_window(prev_start_iso, prev_end_iso))) if prev_start_iso else []

    # ── 1. GELD ───────────────────────────────────────────────────────
    def _money_stats(warm_rows):
        revenue = 0.0; commission = 0.0; closes = 0
        for r in warm_rows:
            if r.get('status') != 'closed': continue
            try: revenue    += float(r.get('closed_amount')     or 0)
            except (TypeError, ValueError): pass
            try: commission += float(r.get('commission_amount') or 0)
            except (TypeError, ValueError): pass
            closes += 1
        avg_deal = (revenue / closes) if closes else 0.0
        open_pipe = [r for r in warm_rows
                     if r.get('status') != 'closed'
                     and r.get('pipeline_status') not in ('afgehaakt', 'gesloten')]
        close_rate = (closes / len(warm_rows)) if warm_rows else 0.0
        projected = len(open_pipe) * close_rate * avg_deal
        return {
            'revenue': round(revenue, 2),
            'commission': round(commission, 2),
            'avg_deal': round(avg_deal, 2),
            'projected_pipeline': round(projected, 2),
            'closes': closes,
            'open_pipeline_count': len(open_pipe),
            'close_rate_pct': round(close_rate * 100, 1),
        }
    geld_now  = _money_stats(warm_now)
    geld_prev = _money_stats(warm_prev) if warm_prev else None

    # ── 3. BRONNEN (per source breakdown of leads + revenue + commissie) ─
    bronnen = []
    for m in ('phone', 'whatsapp', 'extern'):
        rows_m = [r for r in warm_now if r.get('contact_method') == m]
        rev = 0.0; comm = 0.0; closes = 0
        for r in rows_m:
            if r.get('status') != 'closed': continue
            try: rev  += float(r.get('closed_amount')     or 0)
            except: pass
            try: comm += float(r.get('commission_amount') or 0)
            except: pass
            closes += 1
        leads = len(rows_m)
        conv  = (closes / leads * 100) if leads else 0.0
        bronnen.append({
            'method': m,
            'leads': leads,
            'closes': closes,
            'conv_pct': round(conv, 1),
            'revenue': round(rev, 2),
            'commission': round(comm, 2),
        })
    # Mark winner: highest revenue first, falls back to closes
    if bronnen:
        winner = max(bronnen, key=lambda b: (b['revenue'], b['closes']))
        for b in bronnen:
            b['is_winner'] = (b['method'] == winner['method']) and (b['leads'] > 0)

    # ── 4. LEADERBOARD (top 3 members in current window) ─────────────
    member_agg = {}
    for r in warm_now:
        mid = r.get('added_by_id'); mname = r.get('added_by_name') or '—'
        if not mid: continue
        agg = member_agg.setdefault(str(mid), {'id': str(mid), 'name': mname, 'closes': 0, 'revenue': 0.0, 'commission': 0.0, 'meetings_scheduled': 0, 'shows': 0, 'leads': 0})
        agg['leads'] += 1
        if r.get('meeting_state') in ('scheduled', 'no_show_followup') or r.get('meeting_outcome') in ('show', 'no_show'):
            agg['meetings_scheduled'] += 1
        if r.get('meeting_outcome') == 'show':
            agg['shows'] += 1
        if r.get('status') == 'closed':
            agg['closes'] += 1
            try: agg['revenue']    += float(r.get('closed_amount')     or 0)
            except: pass
            try: agg['commission'] += float(r.get('commission_amount') or 0)
            except: pass
    for r in clients_now:
        mid = r.get('added_by_id'); mname = r.get('added_by_name') or '—'
        if not mid: continue
        agg = member_agg.setdefault(str(mid), {'id': str(mid), 'name': mname, 'closes': 0, 'revenue': 0.0, 'commission': 0.0, 'meetings_scheduled': 0, 'shows': 0, 'leads': 0})
        if r.get('meeting_outcome') == 'show': agg['shows'] += 1
    # Compute rates
    for agg in member_agg.values():
        agg['schedule_rate_pct'] = round((agg['meetings_scheduled'] / agg['leads'] * 100), 1) if agg['leads'] else 0.0
        agg['show_rate_pct']     = round((agg['shows'] / agg['meetings_scheduled'] * 100), 1) if agg['meetings_scheduled'] else 0.0
    leaderboard = sorted(member_agg.values(), key=lambda a: (a['revenue'], a['closes']), reverse=True)[:5]
    # Attach WA streak for each
    try:
        sm_res = db.table('sales_members').select('id,last_known_streak').execute()
        streak_map = {str(r['id']): int(r.get('last_known_streak') or 0) for r in (sm_res.data or [])}
        for a in leaderboard:
            a['wa_streak'] = streak_map.get(a['id'], 0)
    except Exception:
        for a in leaderboard:
            a['wa_streak'] = 0
    for a in leaderboard:
        a['revenue']    = round(a['revenue'], 2)
        a['commission'] = round(a['commission'], 2)

    # ── 5. ACTIE QUEUE (counts only — details are in their own endpoints) ─
    today_iso = now.date().isoformat()
    soon_iso  = (now + timedelta(minutes=30)).isoformat()
    past15_iso = (now - timedelta(minutes=15)).isoformat()
    three_days_ago_iso = (now - timedelta(days=3)).isoformat()

    actie = {
        'meetings_due_now': 0,
        'meetings_overdue': 0,
        'no_show_pending': 0,
        'callback_stuck': 0,
        'followup_overdue': 0,
    }
    me_id = _get_sales_member_id()
    me_id_str = str(me_id) if me_id else None

    # Live fetches (scoped to current user when possible)
    try:
        q = db.table('warm_leads').select('id,added_by_id,meeting_state,meeting_outcome,meeting_scheduled_at,pipeline_status,meeting_no_show_followup_at,followup_date,followup_done').execute()
        for r in (q.data or []):
            if me_id_str and str(r.get('added_by_id') or '') != me_id_str: continue
            ms = r.get('meeting_state'); mo = r.get('meeting_outcome')
            sched = r.get('meeting_scheduled_at') or ''
            if ms == 'scheduled' and mo is None and sched:
                if sched <= soon_iso and sched >= past15_iso:
                    actie['meetings_due_now'] += 1
                elif sched < past15_iso:
                    actie['meetings_overdue'] += 1
            if mo == 'no_show' and not r.get('meeting_no_show_followup_at'):
                actie['no_show_pending'] += 1
            ps = r.get('pipeline_status')
            if ps in ('ik_bel_terug', 'zij_bellen_terug'):
                # Stuck means created_at older than 3 days — we don't have that here, use ms instead
                actie['callback_stuck'] += 1
            fu = r.get('followup_date')
            if fu and fu <= today_iso and not r.get('followup_done'):
                actie['followup_overdue'] += 1
    except Exception as e:
        print(f'[KPI-EXTRAS] actie warm scan failed: {e}')
    try:
        q = db.table('clients').select('id,added_by_id,meeting_state,meeting_outcome,meeting_scheduled_at,meeting_no_show_followup_at').execute()
        for r in (q.data or []):
            if me_id_str and str(r.get('added_by_id') or '') != me_id_str: continue
            ms = r.get('meeting_state'); mo = r.get('meeting_outcome')
            sched = r.get('meeting_scheduled_at') or ''
            if ms == 'scheduled' and mo is None and sched:
                if sched <= soon_iso and sched >= past15_iso:
                    actie['meetings_due_now'] += 1
                elif sched < past15_iso:
                    actie['meetings_overdue'] += 1
            if mo == 'no_show' and not r.get('meeting_no_show_followup_at'):
                actie['no_show_pending'] += 1
    except Exception as e:
        print(f'[KPI-EXTRAS] actie clients scan failed: {e}')

    # ── 6. VS PREV (compare current vs previous same-length window) ──
    def _agg_window_basic(warm_rows, client_rows):
        benaderd_proxy = len(warm_rows)   # warms-in-period as a proxy for activity
        closes = sum(1 for r in warm_rows if r.get('status') == 'closed')
        revenue = 0.0
        for r in warm_rows:
            if r.get('status') == 'closed':
                try: revenue += float(r.get('closed_amount') or 0)
                except: pass
        eindconv = (closes / benaderd_proxy * 100) if benaderd_proxy else 0.0
        return {'leads': benaderd_proxy, 'closes': closes, 'revenue': round(revenue, 2), 'eindconv_pct': round(eindconv, 2)}
    vs_now  = _agg_window_basic(warm_now, clients_now)
    vs_prev_data = _agg_window_basic(warm_prev, clients_prev) if prev_start_iso else None
    def _delta(now_v, prev_v):
        if prev_v is None or prev_v == 0:
            return None
        return round((now_v - prev_v) / prev_v * 100, 1)
    vs_prev = {
        'now': vs_now,
        'prev': vs_prev_data,
        'change_pct': {
            'leads':    _delta(vs_now['leads'],    vs_prev_data['leads'])   if vs_prev_data else None,
            'closes':   _delta(vs_now['closes'],   vs_prev_data['closes'])  if vs_prev_data else None,
            'revenue':  _delta(vs_now['revenue'],  vs_prev_data['revenue']) if vs_prev_data else None,
            'eindconv': (round(vs_now['eindconv_pct'] - vs_prev_data['eindconv_pct'], 2)
                        if vs_prev_data else None),
        },
        'window': {'now': {'from': start_iso, 'to': end_iso},
                   'prev': {'from': prev_start_iso, 'to': prev_end_iso}},
    }

    # ── 7. VELOCITY (avg days from created → closed; per-stage best-effort) ──
    completed = [r for r in warm_now if r.get('status') == 'closed' and r.get('closed_at') and r.get('created_at')]
    durations_days = []
    for r in completed:
        try:
            c0 = datetime.fromisoformat(r['created_at'].replace('Z','+00:00'))
            c1 = datetime.fromisoformat(r['closed_at'].replace('Z','+00:00'))
            d = (c1 - c0).total_seconds() / 86400
            if d >= 0: durations_days.append(d)
        except Exception: pass
    avg_total_days = round(sum(durations_days) / len(durations_days), 1) if durations_days else 0.0
    fastest = round(min(durations_days), 1) if durations_days else None
    slowest = round(max(durations_days), 1) if durations_days else None
    # Per-stage timing via status_history (best effort; large queries truncated)
    stage_timing = {}
    try:
        warm_ids_list = [r['id'] for r in warm_now][:300]   # cap to 300 for perf
        if warm_ids_list:
            sh = (db.table('status_history')
                  .select('entity_id,old_status,new_status,created_at')
                  .eq('entity_type', 'warm_lead')
                  .in_('entity_id', warm_ids_list)
                  .order('entity_id')
                  .execute())
            by_id = {}
            for h in (sh.data or []):
                by_id.setdefault(h['entity_id'], []).append(h)
            # For each lead, collect transition deltas
            transition_buckets = {}
            for eid, events in by_id.items():
                events.sort(key=lambda x: x['created_at'] or '')
                for i in range(1, len(events)):
                    prev_ev = events[i - 1]; ev = events[i]
                    key = f"{prev_ev.get('new_status')}→{ev.get('new_status')}"
                    try:
                        t0 = datetime.fromisoformat(prev_ev['created_at'].replace('Z','+00:00'))
                        t1 = datetime.fromisoformat(ev['created_at'].replace('Z','+00:00'))
                        d = (t1 - t0).total_seconds() / 86400
                        if d >= 0:
                            transition_buckets.setdefault(key, []).append(d)
                    except Exception: continue
            for k, arr in transition_buckets.items():
                if arr:
                    stage_timing[k] = {'avg_days': round(sum(arr)/len(arr), 1), 'samples': len(arr)}
    except Exception as e:
        print(f'[KPI-EXTRAS] velocity history fetch failed: {e}')
    # Identify slowest stage
    slowest_stage = None
    if stage_timing:
        slowest_stage = max(stage_timing.items(), key=lambda kv: kv[1]['avg_days'])
        slowest_stage = {'transition': slowest_stage[0], 'avg_days': slowest_stage[1]['avg_days'], 'samples': slowest_stage[1]['samples']}
    velocity = {
        'avg_total_days': avg_total_days,
        'fastest_close_days': fastest,
        'slowest_close_days': slowest,
        'completed_count': len(durations_days),
        'slowest_stage': slowest_stage,
        'stage_timing': stage_timing,
    }

    # ── 8. STRATEGY (currently active WA-strategy + perf snapshot) ──
    strategy = {'current': None, 'active_for_days': None, 'closes_per_day_now': None, 'closes_per_day_prev': None}
    try:
        sr = db.table('wa_strategies').select('*').is_('ended_at', 'null').order('started_at', desc=True).limit(1).execute()
        if sr.data:
            s = sr.data[0]
            strategy['current'] = {'id': s.get('id'), 'name': s.get('name'), 'started_at': s.get('started_at')}
            try:
                started = datetime.fromisoformat((s.get('started_at') or '').replace('Z','+00:00'))
                strategy['active_for_days'] = max(0, int((now - started).total_seconds() / 86400))
            except Exception: pass
            # Closes per day during current strategy
            try:
                cs_rows = db.table('warm_leads').select('id,closed_at').eq('status','closed').gte('closed_at', s.get('started_at')).execute()
                closes_count = len(cs_rows.data or [])
                days_active = max(1, strategy['active_for_days'] or 1)
                strategy['closes_per_day_now'] = round(closes_count / days_active, 2)
            except Exception: pass
        # Previous strategy comparison: fetch a small slice and find the most
        # recent ENDED row in Python. Cheaper than relying on .not_.is_(...) syntax.
        sr_prev_res = db.table('wa_strategies').select('*').order('started_at', desc=True).limit(10).execute()
        ended_rows = [x for x in (sr_prev_res.data or []) if x.get('ended_at')]
        if ended_rows:
            # Sort by ended_at desc explicitly to pick the most recent ended one
            ended_rows.sort(key=lambda x: x.get('ended_at') or '', reverse=True)
            p = ended_rows[0]
            try:
                p_start = datetime.fromisoformat((p.get('started_at') or '').replace('Z','+00:00'))
                p_end   = datetime.fromisoformat((p.get('ended_at')   or '').replace('Z','+00:00'))
                p_days = max(1, int((p_end - p_start).total_seconds() / 86400))
                cs_prev = db.table('warm_leads').select('id').eq('status','closed').gte('closed_at', p.get('started_at')).lt('closed_at', p.get('ended_at')).execute()
                strategy['closes_per_day_prev'] = round(len(cs_prev.data or []) / p_days, 2)
                strategy['previous_name'] = p.get('name')
            except Exception: pass
    except Exception as e:
        print(f'[KPI-EXTRAS] strategy fetch failed: {e}')

    # ── 2. DIAGNOSE (biggest leak + recovery rates + stuck alerts) ──
    # Reuse logic from sales_kpi_stats for the funnel — but compute leaks locally.
    # Stuck alerts: pending_link/forum_nog_sturen > 7 days, no_show no follow-up > 14 days
    seven_days_ago_iso = (now - timedelta(days=7)).isoformat()
    fourteen_days_ago_iso = (now - timedelta(days=14)).isoformat()
    stuck_link_pending = 0
    stuck_forum_nog = 0
    stuck_noshow = 0
    for r in warm_now + clients_now:
        if r.get('pipeline_status') == 'forum_nog_sturen' and r.get('created_at') and r['created_at'] < seven_days_ago_iso:
            stuck_forum_nog += 1
        ms = r.get('meeting_state'); mo = r.get('meeting_outcome')
        if (ms == 'pending_link' and r.get('pipeline_status') == 'forum_ingevuld'
                and r.get('created_at') and r['created_at'] < seven_days_ago_iso):
            stuck_link_pending += 1
        if mo == 'no_show' and not r.get('meeting_no_show_followup_at') and r.get('created_at') and r['created_at'] < fourteen_days_ago_iso:
            stuck_noshow += 1
    # Callback + no-show recovery rate (sample via status_history-based logic)
    callback_recovery_pct = None
    no_show_recovery_pct = None
    try:
        # Reuse the callback funnel logic locally
        cb_total = sum(1 for r in warm_now if (r.get('pipeline_status') in ('ik_bel_terug','zij_bellen_terug')))
        cb_progressed = 0
        # Approximate progression by looking at warm leads that moved past callback
        # within the period via status_history.
        if warm_now:
            # Plain incremental builder — avoids the brittle conditional
            # ternary that occasionally returned a non-builder object.
            q2 = db.table('status_history').select('entity_id,new_status,old_status,created_at').eq('entity_type', 'warm_lead')
            if start_iso: q2 = q2.gte('created_at', start_iso)
            if end_iso:   q2 = q2.lt('created_at', end_iso)
            res2 = q2.execute()
            cohort_ids = set()
            for h in (res2.data or []):
                if h.get('new_status') in ('ik_bel_terug','zij_bellen_terug'):
                    cohort_ids.add(h.get('entity_id'))
            # Of cohort, how many are NOW not in callback?
            if cohort_ids:
                ids_list = list(cohort_ids)
                cur_states = {}
                BATCH = 80
                for i in range(0, len(ids_list), BATCH):
                    chunk = ids_list[i:i+BATCH]
                    qres = db.table('warm_leads').select('id,pipeline_status').in_('id', chunk).execute()
                    for r in (qres.data or []):
                        cur_states[r['id']] = r.get('pipeline_status')
                progressed_states = ('forum_nog_sturen','forum_gestuurd','forum_gezien','forum_ingevuld','gesloten')
                progressed = sum(1 for s in cur_states.values() if s in progressed_states)
                callback_recovery_pct = round(progressed / len(cur_states) * 100, 1) if cur_states else 0.0
    except Exception as e:
        print(f'[KPI-EXTRAS] callback recovery calc failed: {e}')
    try:
        ns_cohort = sum(1 for r in (warm_now + clients_now) if r.get('meeting_outcome') == 'no_show')
        ns_recovered = sum(1 for r in (warm_now + clients_now)
                           if r.get('meeting_outcome') == 'show'
                           and r.get('meeting_no_show_followup_at'))
        no_show_recovery_pct = round((ns_recovered / ns_cohort * 100), 1) if ns_cohort else 0.0
    except Exception as e:
        print(f'[KPI-EXTRAS] no-show recovery calc failed: {e}')

    diagnose = {
        'callback_recovery_pct': callback_recovery_pct,
        'no_show_recovery_pct': no_show_recovery_pct,
        'stuck_forum_nog_sturen': stuck_forum_nog,
        'stuck_link_pending':     stuck_link_pending,
        'stuck_noshow':           stuck_noshow,
    }

    return jsonify({
        'period': {'from': start_iso, 'to': end_iso, 'prev_from': prev_start_iso, 'prev_to': prev_end_iso, 'days': period_len_days},
        'source':   src_filter,
        'member_id': member_id or None,
        'geld':     geld_now,
        'geld_prev': geld_prev,
        'diagnose': diagnose,
        'bronnen':  bronnen,
        'leaderboard': leaderboard,
        'actie':    actie,
        'vs_prev':  vs_prev,
        'velocity': velocity,
        'strategy': strategy,
    })


# ── No-show recovery sub-funnel ──────────────────────────────────────────
# Cohort = warm_leads + clients that ever hit meeting_outcome='no_show' (via
# status_history) UNION the ones currently in no_show or no_show_followup
# (fallback for legacy data before history-logging was added).
# Output: how many are still stuck (no follow-up yet), how many got follow-up,
# how many recovered (later show), how many afgehaakt — same shape as the
# callback funnel so the frontend can mirror the rendering pattern.
@app.route('/api/sales/kpi-no-show-funnel', methods=['GET'])
@require_sales_auth
def kpi_no_show_funnel():
    start_iso = request.args.get('from')
    end_iso   = request.args.get('to')
    src       = (request.args.get('source') or 'all').strip()
    member_id = (request.args.get('member_id') or '').strip() or None

    # 1) Cohort via status_history (warm_lead + client transitions to no_show)
    warm_ids, client_ids = set(), set()
    try:
        hr = (db.table('status_history').select('entity_type,entity_id')
              .like('new_status', 'meeting_outcome:no_show')
              .execute())
        for h in (hr.data or []):
            if not h.get('entity_id'): continue
            if h.get('entity_type') == 'warm_lead': warm_ids.add(str(h['entity_id']))
            if h.get('entity_type') == 'client':    client_ids.add(str(h['entity_id']))
    except Exception as e:
        print(f'[NO-SHOW-FUNNEL] history fetch failed: {e}')

    # 2) Fallback: anyone CURRENTLY in no_show / no_show_followup
    try:
        wr = (db.table('warm_leads').select('id')
              .or_('meeting_outcome.eq.no_show,meeting_state.eq.no_show_followup')
              .execute())
        for r in (wr.data or []):
            if r.get('id'): warm_ids.add(str(r['id']))
    except Exception as e:
        print(f'[NO-SHOW-FUNNEL] warm fallback failed: {e}')
    try:
        cr = (db.table('clients').select('id')
              .or_('meeting_outcome.eq.no_show,meeting_state.eq.no_show_followup')
              .execute())
        for r in (cr.data or []):
            if r.get('id'): client_ids.add(str(r['id']))
    except Exception as e:
        print(f'[NO-SHOW-FUNNEL] client fallback failed: {e}')

    if not (warm_ids or client_ids):
        return jsonify({
            'total': 0,
            'still_no_show':       {'count': 0},
            'followup_sent':       {'count': 0},
            'recovered':           {'count': 0},
            'afgehaakt':            0,
            'recovery_pct':         0.0,
            'period': {'from': start_iso, 'to': end_iso}, 'source': src,
        })

    # 3) Fetch current state of cohort, batched
    rows = []
    def _batch_fetch(table, ids, cols):
        ids_list = list(ids); BATCH = 80
        for i in range(0, len(ids_list), BATCH):
            chunk = ids_list[i:i+BATCH]
            try:
                q = db.table(table).select(cols).in_('id', chunk)
                if start_iso: q = q.gte('created_at', start_iso)
                if end_iso:   q = q.lt('created_at', end_iso)
                if member_id: q = q.eq('added_by_id', member_id)
                r = q.execute()
                for x in (r.data or []):
                    x['_entity_type'] = 'warm_lead' if table == 'warm_leads' else 'client'
                    rows.append(x)
            except Exception as e:
                print(f'[NO-SHOW-FUNNEL] lookup {table} chunk failed: {e}')
    _batch_fetch('warm_leads', warm_ids, 'id,contact_method,added_by_id,created_at,meeting_state,meeting_outcome')
    _batch_fetch('clients',    client_ids, 'id,contact_method,added_by_id,created_at,meeting_state,meeting_outcome')

    if src in ('phone', 'whatsapp', 'extern'):
        rows = [r for r in rows if r.get('contact_method') == src]

    # 4) Bucket by current (meeting_state, meeting_outcome)
    still_no_show = 0
    followup_sent  = 0
    recovered      = 0
    afgehaakt      = 0
    for r in rows:
        st  = r.get('meeting_state')
        out = r.get('meeting_outcome')
        if   out == 'show':                              recovered     += 1
        elif st == 'afgehaakt' or out == 'afgehaakt':    afgehaakt     += 1
        elif st == 'no_show_followup':                   followup_sent += 1
        elif out == 'no_show':                           still_no_show += 1
        # else: row was in no_show once but moved past (e.g. fully re-scheduled
        # without an outcome yet) — count as still recovering for visibility:
        else:                                            followup_sent += 1

    total = len(rows)
    recovery_pct = round(recovered / total * 100, 1) if total else 0.0
    return jsonify({
        'total':            total,
        'still_no_show':    {'count': still_no_show},
        'followup_sent':    {'count': followup_sent},
        'recovered':        {'count': recovered},
        'afgehaakt':        afgehaakt,
        'recovery_pct':     recovery_pct,
        'period': {'from': start_iso, 'to': end_iso}, 'source': src,
    })


# ── Test data: seed + wipe ───────────────────────────────────────────────
# Every test row has '[TEST]' as the prefix of its company name. The wipe
# endpoint deletes anything matching that prefix in prospect_list /
# warm_leads / clients — nothing else is touched. So you can experiment
# with realistic KPI data and roll back with one click.

@app.route('/api/admin/test-data/seed', methods=['POST'])
@require_auth
def admin_seed_test_data():
    try:
        rng = random.Random()
        now = datetime.utcnow()

        # Sales members get the seed distributed across them so per-member
        # KPI stats actually have variance to look at.
        members = []
        try:
            res = db.table('sales_members').select('id, name').eq('status', 'active').execute()
            members = res.data or []
        except Exception:
            pass
        if not members:
            return jsonify({'success': False, 'error': 'Geen actieve sales members — voeg er eerst eentje toe.'}), 400

        # Period spread: most data this week, less further back, so the
        # week-compare mode has a real story to tell.
        weeks_dist = [0]*40 + [1]*30 + [2]*20 + [3]*10
        method_dist = ['phone']*60 + ['whatsapp']*40

        def random_ts_n_weeks_ago(n):
            days_back = n * 7 + rng.randint(0, 6)
            hours = rng.randint(8, 20)
            mins = rng.randint(0, 59)
            return (now - timedelta(days=days_back, hours=24-hours, minutes=mins)).isoformat()

        company_names = [
            "Garage Janssen", "Auto De Vries", "Bakkerij Smit",
            "Klusbedrijf Bos", "Schoonmaak Mulder", "Schilders Visser",
            "Loodgieter Peters", "Stylisten Dekker", "Cafe Brouwer",
            "Restaurant De Boer", "Yoga Hendriks", "Fysio Bakker",
            "Sportschool Dijkstra", "Coiffeur Van Dam", "Bloemen Vermeer",
            "Slagerij Kuipers", "Apotheek Jansen", "Boekwinkel Verhoeven",
            "Fietsen De Wit", "Kinderopvang Maas",
        ]
        cities = ["Amsterdam", "Rotterdam", "Den Haag", "Utrecht", "Eindhoven", "Groningen", "Tilburg"]
        niches = ["auto", "horeca", "retail", "diensten", "wellness", "bouw", "zorg"]

        batch = 'testseed_' + str(int(now.timestamp() * 1000))

        # Build a "business pool" first so prospects → warm_leads → clients
        # all share the same names. The KPI stats endpoint links these
        # tables by company_name (warm_leads_funnel + source_conversion +
        # demo_funnel contact_method inheritance) — without name overlap
        # the funnels look disconnected.
        NUM_PROSPECTS = 50
        NUM_WARM      = 25     # subset of prospects that became warm leads
        NUM_CLIENTS   = 15     # subset of warm leads that reached demo phase

        businesses = []
        for i in range(NUM_PROSPECTS):
            businesses.append({
                'name':           f"[TEST] {rng.choice(company_names)} #{i+1:03d}",
                'phone':          "+316" + str(90000000 + i).zfill(8),
                'contact_method': rng.choice(method_dist),
                'city':           rng.choice(cities),
                'niche':          rng.choice(niches),
                'rating':         rng.randint(3, 5),
                'week':           rng.choice(weeks_dist),
                'member':         rng.choice(members),
            })

        # ── Prospects (all 50 businesses) ─────────────────────────────────
        prospects_rows = []
        for i, biz in enumerate(businesses):
            ts = random_ts_n_weeks_ago(biz['week'])
            prospects_rows.append({
                'id':              f"{batch}_{i}",
                'company_name':    biz['name'],
                'phone':           biz['phone'],
                'city':            biz['city'],
                'niche':           biz['niche'],
                'rating':          biz['rating'],
                'called':          True,
                'contact_method':  biz['contact_method'],
                'called_at':       ts,
                'called_by_id':    biz['member']['id'],
                'called_by_name':  biz['member']['name'],
                'created_at':      ts,
                'import_batch':    batch,
            })

        # ── Warm leads (a subset of the businesses, same names so the
        # source_conversion match in /api/sales/kpi-stats lands) ──────────
        WARM_DIST = (
            ['forum_nog_sturen']*15 + ['forum_gestuurd']*25 + ['forum_gezien']*15 + ['forum_ingevuld']*10 +
            ['ik_bel_terug']*10  + ['zij_bellen_terug']*10 +
            ['afgehaakt']*10     + ['gesloten']*5
        )
        warm_indices = rng.sample(range(NUM_PROSPECTS), NUM_WARM)
        warm_rows = []
        warm_meta = []  # tracks status + id so we can synthesize status_history
        for j, idx in enumerate(warm_indices):
            biz = businesses[idx]
            status = rng.choice(WARM_DIST)
            ts = random_ts_n_weeks_ago(biz['week'])
            warm_id = f"{batch}_w_{idx}"
            # warm_leads has no auto-generated id — we have to supply one
            row = {
                'id':              warm_id,
                'company_name':    biz['name'],
                'phone':           biz['phone'],
                'contact_method':  biz['contact_method'],
                'pipeline_status': status,
                # Valid status enum: 'warm' (default) or 'closed'
                'status':          'closed' if status == 'gesloten' else 'warm',
                'added_by_id':     biz['member']['id'],
                'added_by_name':   biz['member']['name'],
                'created_at':      ts,
            }
            if status == 'afgehaakt':
                row['dropoff_stage'] = rng.choice(['forum_nog_sturen', 'forum_gestuurd', 'forum_gezien', 'forum_ingevuld', 'ik_bel_terug'])
            if status == 'gesloten':
                amount = rng.randint(500, 2500)
                row['closed_amount']     = amount
                row['commission_amount'] = round(amount * 0.40, 2)
                row['closed_at']         = ts
            # Meeting state distribution — biased so we have data in every bucket.
            # forum_ingevuld leads typically have a link sent or meeting scheduled;
            # earlier-stage leads are usually still pending_link.
            if status == 'forum_ingevuld':
                meeting = rng.choices(
                    ['scheduled','link_sent','scheduled','scheduled'],  # 75% scheduled
                    weights=[1,1,1,1])[0]
            elif status in ('forum_gezien', 'forum_gestuurd'):
                meeting = rng.choices(['pending_link','link_sent','scheduled'], weights=[2,2,1])[0]
            elif status == 'gesloten':
                meeting = 'scheduled'  # closed deals all had a meeting
            elif status == 'afgehaakt':
                meeting = rng.choices(['pending_link','link_sent','scheduled','afgehaakt'], weights=[1,1,1,2])[0]
            else:
                meeting = 'pending_link'
            row['meeting_state'] = meeting
            if meeting in ('link_sent','scheduled','no_show_followup','afgehaakt'):
                row['meeting_calendly_url'] = f'https://calendly.com/viralconversions/intake-{idx}'
                row['meeting_link_sent_at'] = ts
            if meeting == 'scheduled' or meeting == 'no_show_followup':
                # Schedule ~3 days after ts for variety; some past, some future
                from datetime import timedelta as _tdseed
                offset_days = rng.randint(-7, 10)
                try:
                    sched_dt = datetime.fromisoformat(ts.replace('Z','+00:00')) + _tdseed(days=offset_days)
                    row['meeting_scheduled_at'] = sched_dt.isoformat()
                except Exception:
                    pass
                row['meeting_join_url'] = f'https://calendly.com/viralconversions/events/intake-{idx}'
            # If meeting is scheduled and date is in the past, set an outcome
            if meeting == 'scheduled' and row.get('meeting_scheduled_at'):
                if row['meeting_scheduled_at'] < datetime.now(timezone.utc).isoformat():
                    outcome = rng.choices(['show','no_show', None], weights=[5,2,1])[0]
                    if outcome:
                        row['meeting_outcome'] = outcome
            if meeting == 'no_show_followup':
                row['meeting_outcome'] = 'no_show'
                row['meeting_no_show_followup_at'] = ts
            warm_rows.append(row)
            warm_meta.append({'id': warm_id, 'status': status, 'member': biz['member'], 'meeting': meeting, 'outcome': row.get('meeting_outcome')})

        # ── status_history rows so the callback sub-funnel widget has
        # something to chew on. Anyone currently in callback gets one
        # history row; some progressed/afgehaakt leads get a synthetic
        # "passed through callback" episode so the cohort isn't only
        # currently-stuck leads.
        history_rows = []
        for meta in warm_meta:
            cur = meta['status']
            went_through = False
            if cur in ('ik_bel_terug', 'zij_bellen_terug'):
                went_through = True
                cb_status = cur
            elif cur in ('forum_nog_sturen', 'forum_gestuurd', 'forum_gezien', 'forum_ingevuld', 'gesloten') and rng.random() < 0.40:
                went_through = True
                cb_status = rng.choice(['ik_bel_terug', 'zij_bellen_terug'])
            elif cur == 'afgehaakt' and rng.random() < 0.30:
                went_through = True
                cb_status = rng.choice(['ik_bel_terug', 'zij_bellen_terug'])

            if went_through:
                history_rows.append({
                    'entity_type':     'warm_lead',
                    'entity_id':       meta['id'],
                    'old_status':      None,
                    'new_status':      cb_status,
                    'changed_by_id':   meta['member']['id'],
                    'changed_by_name': meta['member']['name'],
                })

        # ── Clients (subset of warm_leads, same names so kpi-stats can
        # inherit contact_method via the company_name → warm_lead lookup) ─
        # New demo enum: moet_gebouwd → klaar → show → geclosed → aanbetaling
        # → volledig_betaald. no_show + afgehaakt sit as parallel buckets.
        DEMO_DIST = (
            ['moet_gebouwd']*15 + ['klaar']*15 + ['show']*25 +
            ['geclosed']*15     + ['aanbetaling']*10 +
            ['volledig_betaald']*5 + ['no_show']*8 + ['afgehaakt']*7
        )
        client_indices = rng.sample(warm_indices, NUM_CLIENTS)
        client_rows = []
        for idx in client_indices:
            biz = businesses[idx]
            status = rng.choice(DEMO_DIST)
            ts = random_ts_n_weeks_ago(biz['week'])
            row = {
                'name':        biz['name'],
                'demo_status': status,
                'created_at':  ts,
                'added_by_id': biz['member']['id'],
                'added_by_name': biz['member']['name'],
                'contact_method': biz['contact_method'],
            }
            if status == 'afgehaakt':
                row['dropoff_stage'] = rng.choice(['moet_gebouwd', 'klaar', 'show'])
            if status in ('geclosed', 'aanbetaling', 'volledig_betaald'):
                amount = rng.randint(500, 2500)
                row['total_amount']      = amount
                row['commission_amount'] = round(amount * 0.40, 2)
            # Meeting fields: everyone made it to clients = they had a meeting scheduled
            row['meeting_state'] = 'scheduled' if status != 'afgehaakt' else 'afgehaakt'
            row['meeting_calendly_url'] = f'https://calendly.com/viralconversions/intake-{idx}'
            row['meeting_join_url']     = f'https://calendly.com/viralconversions/events/intake-{idx}'
            from datetime import timedelta as _tdc
            try:
                sched_dt = datetime.fromisoformat(ts.replace('Z','+00:00')) + _tdc(days=rng.randint(-14, 7))
                row['meeting_link_sent_at'] = ts
                row['meeting_scheduled_at'] = sched_dt.isoformat()
            except Exception:
                pass
            if status in ('show', 'geclosed', 'aanbetaling', 'volledig_betaald'):
                row['meeting_outcome'] = 'show'
            elif status == 'no_show':
                row['meeting_outcome'] = 'no_show'
                # Half got a follow-up sent already
                if rng.random() < 0.5:
                    row['meeting_state'] = 'no_show_followup'
                    row['meeting_no_show_followup_at'] = ts
            client_rows.append(row)

        # ── Insert ────────────────────────────────────────────────────────
        # Chunked + per-chunk-fallback so a schema mismatch doesn't take the
        # whole seed down. Errors get captured so the response can surface
        # them in the admin popup — no more silent failures.
        def _bulk_insert(table, rows, optional_cols=()):
            inserted = 0
            failed_samples = []
            CHUNK = 50
            for i in range(0, len(rows), CHUNK):
                chunk = rows[i:i+CHUNK]
                try:
                    db.table(table).insert(chunk).execute()
                    inserted += len(chunk)
                    continue
                except Exception as e:
                    chunk_err = str(e)
                # Strip optional cols mentioned in the error and retry
                msg = chunk_err.lower()
                stripped = []
                for col in optional_cols:
                    if col.lower() in msg:
                        for r in chunk: r.pop(col, None)
                        stripped.append(col)
                if stripped:
                    try:
                        db.table(table).insert(chunk).execute()
                        inserted += len(chunk)
                        continue
                    except Exception as e2:
                        chunk_err = str(e2)
                # Row-by-row so one bad row doesn't kill the rest
                for r in chunk:
                    try:
                        db.table(table).insert(r).execute()
                        inserted += 1
                    except Exception as e3:
                        if len(failed_samples) < 2:
                            failed_samples.append(str(e3)[:300])
                        print(f"[TEST-SEED] row insert into {table} failed: {e3}")
            return inserted, failed_samples

        # Meeting-related synthetic history so the no-show recovery sub-funnel
        # and the meeting bridges have non-empty cohorts. Each meeting outcome
        # 'no_show' gets a logged transition, and follow-ups get one too.
        for meta in warm_meta:
            if meta.get('outcome') == 'no_show':
                history_rows.append({
                    'entity_type':     'warm_lead',
                    'entity_id':       meta['id'],
                    'old_status':      'meeting:pending',
                    'new_status':      'meeting_outcome:no_show',
                    'changed_by_id':   meta['member']['id'],
                    'changed_by_name': meta['member']['name'],
                })
            if meta.get('meeting') == 'no_show_followup':
                history_rows.append({
                    'entity_type':     'warm_lead',
                    'entity_id':       meta['id'],
                    'old_status':      'meeting:no_show',
                    'new_status':      'meeting:no_show_followup',
                    'changed_by_id':   meta['member']['id'],
                    'changed_by_name': meta['member']['name'],
                })

        meeting_optional = (
            'meeting_state', 'meeting_calendly_url', 'meeting_link_sent_at',
            'meeting_scheduled_at', 'meeting_join_url', 'meeting_outcome',
            'meeting_no_show_followup_at',
        )
        p_count, p_err = _bulk_insert('prospect_list', prospects_rows, optional_cols=('rating', 'niche', 'city'))
        w_count, w_err = _bulk_insert('warm_leads',    warm_rows,      optional_cols=('dropoff_stage', 'closed_at', 'closed_amount', 'commission_amount', 'contact_method') + meeting_optional)
        c_count, c_err = _bulk_insert('clients',       client_rows,    optional_cols=('dropoff_stage', 'total_amount', 'commission_amount', 'added_by_id', 'added_by_name', 'contact_method') + meeting_optional)
        h_count, h_err = _bulk_insert('status_history', history_rows,  optional_cols=('changed_by_id', 'changed_by_name', 'old_status'))

        return jsonify({
            'success': True,
            'inserted':  {'prospects': p_count, 'warm_leads': w_count, 'clients': c_count, 'status_history': h_count},
            'errors':    {'prospects': p_err,   'warm_leads': w_err,   'clients': c_err,   'status_history': h_err},
            'attempted': {'prospects': len(prospects_rows), 'warm_leads': len(warm_rows), 'clients': len(client_rows), 'status_history': len(history_rows)},
            'batch': batch,
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/test-data/wipe', methods=['POST'])
@require_auth
def admin_wipe_test_data():
    """Undo for admin_seed_test_data — deletes every row whose company name
    (or .name for clients) starts with '[TEST]'. Also cleans status_history
    rows tied to those warm_leads. Production data is never touched."""
    counts = {'prospects': 0, 'warm_leads': 0, 'clients': 0, 'status_history': 0}

    # 1) Grab warm_lead IDs first so we can also clean their status_history
    test_warm_ids = []
    try:
        wres = db.table('warm_leads').select('id').like('company_name', '[TEST]%').execute()
        test_warm_ids = [str(r['id']) for r in (wres.data or []) if r.get('id')]
    except Exception as e:
        print(f"[TEST-WIPE] warm_lead id lookup failed: {e}")

    # 2) Delete status_history rows that belong to those warm_leads
    if test_warm_ids:
        try:
            BATCH = 100
            removed = 0
            for i in range(0, len(test_warm_ids), BATCH):
                chunk = test_warm_ids[i:i+BATCH]
                cres = (db.table('status_history').select('id', count='exact')
                        .eq('entity_type', 'warm_lead').in_('entity_id', chunk).execute())
                removed += cres.count or 0
                (db.table('status_history').delete()
                 .eq('entity_type', 'warm_lead').in_('entity_id', chunk).execute())
            counts['status_history'] = removed
        except Exception as e:
            print(f"[TEST-WIPE] status_history delete failed: {e}")
            counts['status_history_error'] = str(e)

    # 3) Delete the actual records
    try:
        for table, field, key in [
            ('prospect_list', 'company_name', 'prospects'),
            ('warm_leads',    'company_name', 'warm_leads'),
            ('clients',       'name',         'clients'),
        ]:
            try:
                cres = db.table(table).select('id', count='exact').like(field, '[TEST]%').execute()
                counts[key] = cres.count or 0
                if counts[key]:
                    db.table(table).delete().like(field, '[TEST]%').execute()
            except Exception as e:
                print(f"[TEST-WIPE] {table} delete failed: {e}")
                counts[key + '_error'] = str(e)
        return jsonify({'success': True, 'deleted': counts})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/prospects/reset-all', methods=['PUT'])
@require_auth
def admin_reset_all_prospects():
    try:
        db.table('prospect_list').update({
            'called': False, 'called_by_id': None,
            'called_by_name': None, 'called_at': None,
        }).eq('called', True).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sales/my-ref-link', methods=['GET'])
@require_sales_auth
def sales_my_ref_link():
    mid = _get_sales_member_id()
    res = db.table('sales_members').select('ref_code,name').eq('id', mid).limit(1).execute()
    if not res.data:
        return jsonify({'error': 'Not found'}), 404
    m = res.data[0]
    base = request.url_root.rstrip('/')
    link = f"{base}/sales-apply?ref={m['ref_code']}"
    return jsonify({'link': link, 'ref_code': m['ref_code'], 'name': m['name']})

@app.route('/api/sales/my-referral-earnings', methods=['GET'])
@require_sales_auth
def sales_my_referral_earnings():
    from datetime import timezone
    mid = _get_sales_member_id()
    me_res = db.table('sales_members').select('ref_code').eq('id', mid).limit(1).execute()
    if not me_res.data:
        return jsonify({'referrals': [], 'total_bonus_this_month': 0.0})
    my_ref_code = me_res.data[0].get('ref_code')
    if not my_ref_code:
        return jsonify({'referrals': [], 'total_bonus_this_month': 0.0})
    referred_res = db.table('sales_members').select('id,name,first_sale_counted').eq('referred_by_code', my_ref_code).eq('status', 'active').execute()
    now = datetime.now(timezone.utc)
    first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    result = []
    total_bonus = 0.0
    for ref_m in referred_res.data:
        monthly_comm = 0.0
        if ref_m.get('first_sale_counted'):
            monthly_res = db.table('warm_leads').select('commission_amount').eq('added_by_id', ref_m['id']).eq('status', 'closed').gte('closed_at', first_of_month).execute()
            monthly_comm = sum(float(r['commission_amount'] or 0) for r in monthly_res.data)
        bonus = round(monthly_comm * 0.05, 2) if ref_m.get('first_sale_counted') else 0.0
        total_bonus += bonus
        result.append({'name': ref_m['name'], 'first_sale_counted': ref_m.get('first_sale_counted', False), 'monthly_commission': round(monthly_comm, 2), 'bonus': bonus})
    return jsonify({'referrals': result, 'total_bonus_this_month': round(total_bonus, 2)})

@app.route('/api/sales/ref-info', methods=['GET'])
def sales_ref_info():
    code = (request.args.get('code') or '').strip()
    if not code:
        return jsonify({'found': False})
    res = db.table('sales_members').select('name').eq('ref_code', code).eq('status', 'active').limit(1).execute()
    if res.data:
        return jsonify({'found': True, 'name': res.data[0]['name']})
    return jsonify({'found': False})

@app.route('/api/admin/members/<mid>/commission-settings', methods=['PUT'])
@require_auth
def update_member_commission_settings(mid):
    data = request.get_json(silent=True) or {}
    update = {}
    if 'contract_type' in data:
        if data['contract_type'] in ('legacy', 'new', 'whatsapp'):
            update['contract_type'] = data['contract_type']
        else:
            return jsonify({'success': False, 'error': f"invalid contract_type: {data['contract_type']}"}), 400
    if 'commission_override' in data:
        val = data['commission_override']
        update['commission_override'] = float(val) if val not in (None, '') else None
    if update:
        db.table('sales_members').update(update).eq('id', mid).execute()
    return jsonify({'success': True, 'updated': list(update.keys())})

@app.route('/api/admin/monthly-payout', methods=['GET'])
@require_auth
def admin_monthly_payout():
    from datetime import timezone
    now = datetime.now(timezone.utc)
    first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    members_res = db.table('sales_members').select('*').eq('status', 'active').execute()
    members = members_res.data

    monthly_res = db.table('warm_leads').select('added_by_id,closed_amount,commission_amount').eq('status', 'closed').gte('closed_at', first_of_month).execute()
    monthly_by_member = {}
    for r in monthly_res.data:
        m_id = r['added_by_id']
        monthly_by_member.setdefault(m_id, {'commission': 0.0, 'revenue': 0.0})
        monthly_by_member[m_id]['commission'] += float(r['commission_amount'] or 0)
        monthly_by_member[m_id]['revenue']    += float(r['closed_amount'] or 0)

    all_res = db.table('warm_leads').select('added_by_id,commission_amount').eq('status', 'closed').execute()
    total_by_member = {}
    for r in all_res.data:
        m_id = r['added_by_id']
        total_by_member.setdefault(m_id, 0.0)
        total_by_member[m_id] += float(r['commission_amount'] or 0)

    result = []
    for m in members:
        m_id = m['id']
        contract_type = m.get('contract_type') or 'legacy'
        override = m.get('commission_override')
        total_earned = total_by_member.get(m_id, 0.0)
        if override is not None:
            rate_label = f"{int(float(override))}% (handmatig)"
        elif contract_type == 'legacy':
            rate_label = "40% (legacy)"
        elif contract_type == 'whatsapp':
            wa_rate = _compute_whatsapp_rate(m_id)
            rate_label = f"{int(wa_rate * 100)}% (WhatsApp)"
        else:
            if total_earned >= 2500:
                rate_label = "35% (tier 3 — max)"
            elif total_earned >= 1000:
                rate_label = "30% (tier 2)"
            else:
                rate_label = "25% (tier 1)"
        monthly_commission = monthly_by_member.get(m_id, {}).get('commission', 0.0)
        monthly_revenue    = monthly_by_member.get(m_id, {}).get('revenue', 0.0)
        my_ref_code = m.get('ref_code')
        referral_bonus = 0.0
        referral_details = []
        if my_ref_code:
            for ref_m in members:
                if ref_m.get('referred_by_code') == my_ref_code and ref_m.get('first_sale_counted'):
                    ref_commission = monthly_by_member.get(ref_m['id'], {}).get('commission', 0.0)
                    bonus = round(ref_commission * 0.05, 2)
                    referral_bonus += bonus
                    referral_details.append({'name': ref_m['name'], 'monthly_commission': round(ref_commission, 2), 'bonus': bonus})
        result.append({
            'id': m_id, 'name': m['name'],
            'contract_type': contract_type, 'rate_label': rate_label,
            'total_earned_ever': round(total_earned, 2),
            'monthly_revenue': round(monthly_revenue, 2),
            'monthly_commission': round(monthly_commission, 2),
            'referral_bonus': round(referral_bonus, 2),
            'total_payout': round(monthly_commission + referral_bonus, 2),
            'referral_details': referral_details,
            'referred_by_name': m.get('referred_by_name') or None,
        })
    result.sort(key=lambda x: x['total_payout'], reverse=True)
    month_str = now.strftime('%B %Y')
    return jsonify({'month': month_str, 'members': result, 'total': round(sum(r['total_payout'] for r in result), 2)})

@app.route('/api/admin/prospects/uncalled', methods=['DELETE'])
@require_auth
def admin_delete_uncalled_prospects():
    try:
        db.table('prospect_list').delete().eq('called', False).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/admin/prospects/all', methods=['DELETE'])
@require_auth
def admin_delete_all_prospects():
    try:
        db.table('prospect_list').delete().gte('created_at', '2000-01-01').execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Static file serving ───────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('Viralconversions website', 'index.html')

@app.route('/admin')
@require_auth
def admin():
    return send_from_directory('VC website dash', 'dashboard.html')

@app.route('/onboarding')
def onboarding():
    return send_from_directory('onboarding', 'onboarding.html')

@app.route('/demo')
def demo_form():
    return send_from_directory('demo', 'demo.html')

@app.route('/webshop')
def webshop_form():
    return send_from_directory('webshop', 'webshop.html')

@app.route('/onboarding-webshop')
def onboarding_webshop_form():
    return send_from_directory('webshop', 'webshop.html')

# ── Contract serving ─────────────────────────────────────────────────────
# Static De Cargo Winkel HTML/DOCX (fallback / backup) — generated by
# sidequest/generate_contract_docx.py with the DEFAULT_CLIENT constants.
@app.route('/contract/cargo-winkel')
def contract_cargo_html():
    # Render the template with the DEFAULT_CLIENT (Theo / De Cargo Winkel)
    # tokens so the static URL keeps working even after the HTML was
    # templatised. Points the download bar at the static .docx.
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'sidequest'))
    from generate_contract_docx import render_contract_html, DEFAULT_CLIENT
    html = render_contract_html(DEFAULT_CLIENT, docx_url='/contract/cargo-winkel.docx')
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route('/contract/cargo-winkel.docx')
def contract_cargo_docx():
    return send_from_directory('sidequest', 'Contract_DeCargoWinkel.docx', as_attachment=True)

# Per-klant routes — pull data from the onboarding table and render on-the-fly
# so every webshop client has their own pre-populated contract URL the moment
# they finish the intake form.
def _fetch_onboarding_client(cid):
    """Return the dict stored in onboarding.data for the given row id, or None."""
    try:
        res = db.table('onboarding').select('*').eq('id', cid).limit(1).execute()
        if not res.data:
            return None
        row = res.data[0]
        client = json.loads(row['data']) if isinstance(row.get('data'), str) else (row.get('data') or {})
        client['id']          = row.get('id')
        client['submittedAt'] = row.get('submitted_at')
        return client
    except Exception as e:
        print(f'[CONTRACT] fetch failed for {cid}: {e}')
        return None

@app.route('/contract/client/<cid>')
def contract_for_client_html(cid):
    """HTML preview of the contract with the klant's onboarding-data
    auto-populated. Visible in-browser; the download bar at the top
    points at the .docx route below."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'sidequest'))
    from generate_contract_docx import render_contract_html_for_onboarding_row
    client = _fetch_onboarding_client(cid)
    if client is None:
        return 'Klant niet gevonden.', 404
    html = render_contract_html_for_onboarding_row(client, docx_url=f'/contract/client/{cid}.docx')
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route('/contract/client/<cid>.docx')
def contract_for_client_docx(cid):
    """DOCX download with the klant's onboarding-data filled in. Built
    fresh on every request — no caching — so any form-correction shows
    up the next time the link is opened."""
    import sys, tempfile
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'sidequest'))
    from generate_contract_docx import build_contract_docx_for_onboarding_row
    client = _fetch_onboarding_client(cid)
    if client is None:
        return 'Klant niet gevonden.', 404
    safe_name = ''.join(c for c in (client.get('naam') or 'Klant') if c.isalnum() or c in '-_ ').strip().replace(' ', '_') or 'Klant'
    out_path = os.path.join(tempfile.gettempdir(), f'Contract_{safe_name}_{cid}.docx')
    build_contract_docx_for_onboarding_row(client, out_path)
    return send_from_directory(os.path.dirname(out_path), os.path.basename(out_path), as_attachment=True, download_name=f'Contract_{safe_name}.docx')

# ─────────────────────────────────────────────────────────────────────────────
# HERMES — Vapi cold-call agent integration
# ─────────────────────────────────────────────────────────────────────────────
# Vapi (https://vapi.ai) is the voice-AI platform that actually places the
# calls. We push prospects into Vapi via POST /call, Vapi calls the customer,
# and when the call ends Vapi POSTs the result back to /api/vapi/webhook.
# Two tools defined on the Vapi assistant tell us the outcome unambiguously:
#   mark_warm_lead(reason)          → create warm_lead row + status='warm'
#   mark_not_interested(reason)     → status='benaderd' (called, no warmth)
# Anything else (no-answer, voicemail, invalid number) is classified from the
# Vapi endedReason field.
# ─────────────────────────────────────────────────────────────────────────────

import requests as _requests

VAPI_BASE_URL    = 'https://api.vapi.ai'
VAPI_API_KEY     = os.environ.get('VAPI_API_KEY', '').strip()
VAPI_WEBHOOK_SECRET = os.environ.get('VAPI_WEBHOOK_SECRET', '').strip()

# Fallback defaults — gebruikt als hermes_settings rij leeg is.
# IDs (geen secrets) — staan hier zodat een fresh deploy meteen kan bellen
# zonder dat iemand handmatig in /sales → Settings hoeft te klikken.
HERMES_DEFAULT_ASSISTANT_ID                  = '1a7bc214-e14b-476b-8edc-82e364d180ca'  # zonder website
HERMES_DEFAULT_ASSISTANT_ID_BROKEN_WEBSITE   = '06e8817d-4502-46e4-9b3f-dcf8b8b8e689'
HERMES_DEFAULT_ASSISTANT_ID_OUTDATED_WEBSITE = '6ca76503-4cab-4b4b-add1-7de1e3ce8b78'
HERMES_DEFAULT_PHONE_NUMBER_ID               = '68247d07-832f-4803-b8c1-427f41c6b19d'

def _hermes_settings():
    """Returns the singleton settings row (id=1) as a dict, creating it if
    missing. Falls back to HERMES_DEFAULT_* constants for assistant_id /
    phone_number_id when the DB has nothing set yet."""
    try:
        res = db.table('hermes_settings').select('*').eq('id', 1).limit(1).execute()
        if res.data:
            row = res.data[0]
        else:
            # First-run: insert the default row
            db.table('hermes_settings').insert({'id': 1}).execute()
            res = db.table('hermes_settings').select('*').eq('id', 1).limit(1).execute()
            row = res.data[0] if res.data else {}
        # Bake in fallback defaults so a fresh install can dial without
        # someone first opening the Settings modal.
        if not (row.get('assistant_id') or '').strip():
            row['assistant_id'] = HERMES_DEFAULT_ASSISTANT_ID
        if not (row.get('assistant_id_broken_website') or '').strip():
            row['assistant_id_broken_website'] = HERMES_DEFAULT_ASSISTANT_ID_BROKEN_WEBSITE
        if not (row.get('assistant_id_outdated_website') or '').strip():
            row['assistant_id_outdated_website'] = HERMES_DEFAULT_ASSISTANT_ID_OUTDATED_WEBSITE
        if not (row.get('phone_number_id') or '').strip():
            row['phone_number_id'] = HERMES_DEFAULT_PHONE_NUMBER_ID
        return row
    except Exception as e:
        print(f'[HERMES] settings read failed: {e}')
        return {
            'assistant_id':                  HERMES_DEFAULT_ASSISTANT_ID,
            'assistant_id_broken_website':   HERMES_DEFAULT_ASSISTANT_ID_BROKEN_WEBSITE,
            'assistant_id_outdated_website': HERMES_DEFAULT_ASSISTANT_ID_OUTDATED_WEBSITE,
            'phone_number_id':               HERMES_DEFAULT_PHONE_NUMBER_ID,
        }

def _vapi_headers():
    return {
        'Authorization': f'Bearer {VAPI_API_KEY}',
        'Content-Type':  'application/json',
    }

def _vapi_start_call(assistant_id, phone_number_id, customer_number, customer_name, variable_values=None):
    """Place one outbound call via Vapi. Returns the Vapi call id (str) or
    raises on failure. Customer.number must be in E.164 format (+31...)."""
    if not VAPI_API_KEY:
        raise RuntimeError('VAPI_API_KEY ontbreekt — voeg toe aan env vars.')
    payload = {
        'assistantId':    assistant_id,
        'phoneNumberId':  phone_number_id,
        'customer': {
            'number': customer_number,
            'name':   customer_name or '',
        },
    }
    if variable_values:
        payload['assistantOverrides'] = {'variableValues': variable_values}
    r = _requests.post(f'{VAPI_BASE_URL}/call', headers=_vapi_headers(), json=payload, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f'Vapi error {r.status_code}: {r.text[:300]}')
    data = r.json()
    return data.get('id') or data.get('callId')

def _normalize_phone_e164(raw, default_cc='+31'):
    """Best-effort NL E.164. Returns None for clearly-invalid numbers."""
    if not raw: return None
    s = ''.join(c for c in str(raw) if c.isdigit() or c == '+')
    if s.startswith('+'):  return s
    if s.startswith('00'): return '+' + s[2:]
    if s.startswith('0'):  return default_cc + s[1:]
    if s.startswith('31'): return '+' + s
    if len(s) >= 9:        return default_cc + s
    return None

def _hermes_classify_ended_reason(reason):
    """Maps Vapi endedReason → our hermes_outcome bucket. Only triggered
    wanneer de AI GEEN tool aanroept (callback-cases, voicemail, errors).

    'no_answer' = blijft op bellijst voor retry:
      - Telefoon ging over zonder opnemen
      - Echt gesprek waar AI geen tool aanriep (= waarschijnlijk terugbel-
        verzoek; AI hangt op zonder tool call)
      - Transport / connection errors (Vapi/Twilio konden niet eens
        verbinding opzetten — geen bewijs dat de prospect onbereikbaar is)
      - Customer-busy (één keer in gesprek ≠ permanent kwijt)

    'benaderd' = OFF de bellijst (called=true):
      - Voicemail (we hebben 'm bereikt, voicemail volstaat als signaal)
      - Invalid-number (dead nummer, niet meer proberen)"""
    r = (reason or '').lower()
    # Phone-level "kon niet bereiken" → retry
    if r in ('no-answer','customer-did-not-answer','silence-timed-out-without-customer-answering'):
        return 'no_answer'
    # Echt gesprek waar AI geen tool aanriep (callback cases) → niet opgenomen
    if r in ('customer-ended-call','assistant-ended-call','customer-hung-up'):
        return 'no_answer'
    # Vapi/Twilio transport errors: call kwam niet eens tot stand → niet
    # opgenomen + retry mogelijk. Substring check vangt varianten op zoals
    # 'call.start.error.get-transport', 'pipeline-error-call-start-failed', etc.
    if ('call.start.error' in r or 'get-transport' in r or 'get transport' in r
        or 'pipeline-error' in r or 'assistant-error' in r
        or 'twilio-failed-to-connect' in r
        or 'customer-busy' in r):
        return 'no_answer'
    # Voicemail / dead numbers / onbekende reasons → off the list
    return 'benaderd'

_HERMES_PICKUP_REASONS = {
    # Een echt mens nam de telefoon op (= 'opgenomen' in Julian's UI).
    # Voicemail telt expliciet NIET — een antwoordapparaat is geen mens.
    'customer-ended-call', 'assistant-ended-call', 'customer-hung-up',
    'assistant-forwarded-call',
}

def _hermes_compute_counts(run_id):
    """Compute live counts for a Hermes run from prospect_list. Returns a
    dict with num_warm / num_no_answer / num_not_interested / num_called /
    num_picked_up plus the bool 'in_flight' (True als er nog prospects met
    status 'queued' of 'calling' rondlopen). Niet-persisterende helper —
    gebruikt door zowel _hermes_recount_run als de GET endpoints."""
    out = {'num_warm': 0, 'num_no_answer': 0, 'num_not_interested': 0,
           'num_failed': 0, 'num_called': 0, 'num_picked_up': 0, 'in_flight': False}
    try:
        rows = db.table('prospect_list').select('hermes_outcome,hermes_status,hermes_ended_reason').eq('hermes_run_id', run_id).execute()
        for r in (rows.data or []):
            o  = r.get('hermes_outcome'); st = r.get('hermes_status')
            er = (r.get('hermes_ended_reason') or '').lower()
            # 'queued' of 'calling' = nog niet klaar → run is still in_flight
            if st in ('queued', 'calling'):
                out['in_flight'] = True
            if o == 'warm':
                out['num_warm'] += 1; out['num_called'] += 1
                out['num_picked_up'] += 1
            elif o in ('no_answer','invalid_number','failed'):
                out['num_no_answer'] += 1
            elif o in ('benaderd','not_interested'):
                out['num_not_interested'] += 1; out['num_called'] += 1
            if o != 'warm' and er in _HERMES_PICKUP_REASONS:
                out['num_picked_up'] += 1
    except Exception as e:
        print(f'[HERMES] compute_counts {run_id} failed: {e}')
    return out

def _hermes_recount_run(run_id):
    """Persist de live counters op de hermes_runs rij + zet 'completed'
    als er geen prospects meer in flight zijn. Best-effort."""
    c = _hermes_compute_counts(run_id)
    update = {
        'num_warm':           c['num_warm'],
        'num_no_answer':      c['num_no_answer'],
        'num_not_interested': c['num_not_interested'],
        'num_failed':         0,
        'num_called':         c['num_called'],
    }
    # Run alleen op completed zetten als ECHT geen prospects meer queued/calling
    if not c['in_flight']:
        update['status']   = 'completed'
        update['ended_at'] = datetime.now(timezone.utc).isoformat()
    try:
        db.table('hermes_runs').update(update).eq('id', run_id).execute()
    except Exception as e:
        print(f'[HERMES] recount run {run_id} update failed: {e}')


# ── Settings ──────────────────────────────────────────────────────────────
@app.route('/api/sales/hermes/settings', methods=['GET'])
@require_sales_auth
def hermes_settings_get():
    s = _hermes_settings()
    return jsonify({
        'success': True,
        'settings': s,
        'vapi_configured': bool(VAPI_API_KEY and s.get('assistant_id') and s.get('phone_number_id')),
        'vapi_api_key_set': bool(VAPI_API_KEY),
    })

@app.route('/api/sales/hermes/settings', methods=['PUT'])
@require_auth   # admin-only: prompt + voice + cron mag alleen Julian zelf
def hermes_settings_put():
    data = request.get_json(silent=True) or {}
    allowed = ('assistant_id','phone_number_id','system_prompt','voice_id','first_message',
               'max_calls_default','max_parallel_default','filter_no_website','filter_uncalled_only',
               'cron_enabled','cron_time','cron_weekdays_only',
               # Per-categorie assistant IDs (multi-agent feature)
               'assistant_id_no_website','assistant_id_broken_website','assistant_id_outdated_website')
    update = {k: data[k] for k in allowed if k in data}
    update['updated_at'] = datetime.now(timezone.utc).isoformat()
    try:
        db.table('hermes_settings').update(update).eq('id', 1).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:200]}), 500


# ── Multi-agent helpers: per-categorie agent + prospect selectie ──────────
HERMES_CATEGORIES = ('no_website', 'broken_website', 'outdated_website')

# Default openingstijden voor prospects zonder eigen opening_hours data.
# Sleutels = Python weekday() (0=ma, 6=zo). Format: dag → list of [open, close]
# of None (gesloten). Bewust generieke B2B uren — restaurants/kappers etc.
# moeten hun eigen opening_hours krijgen.
DEFAULT_OPENING_HOURS = {
    '0': [['09:00', '17:00']],   # ma
    '1': [['09:00', '17:00']],   # di
    '2': [['09:00', '17:00']],   # wo
    '3': [['09:00', '17:00']],   # do
    '4': [['09:00', '17:00']],   # vr
    '5': None,                   # za = gesloten
    '6': None,                   # zo = gesloten
}

def _nl_now():
    """Current NL local time as datetime (CET/CEST, ruwe DST heuristiek)."""
    now_utc = datetime.now(timezone.utc)
    offset_hours = 2 if 3 <= now_utc.month <= 10 else 1
    return now_utc + timedelta(hours=offset_hours)

def _is_open_now(prospect, now_local=None):
    """True als prospect volgens zijn opening_hours nu open is.
    Fallback: DEFAULT_OPENING_HOURS (ma-vr 09:00-17:00) als prospect geen
    eigen hours heeft. Defensief — een parse-error telt als 'open' zodat
    je geen prospects mist door bagger data."""
    if now_local is None:
        now_local = _nl_now()
    weekday = str(now_local.weekday())
    cur_minutes = now_local.hour * 60 + now_local.minute

    raw = prospect.get('opening_hours') if isinstance(prospect, dict) else None
    hours = None
    if raw:
        try:
            hours = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            hours = None
    if not isinstance(hours, dict):
        hours = DEFAULT_OPENING_HOURS

    day_periods = hours.get(weekday)
    if not day_periods:
        return False   # expliciet gesloten op deze dag

    for period in day_periods:
        try:
            if not isinstance(period, (list, tuple)) or len(period) != 2:
                continue
            open_h, open_m   = map(int, str(period[0]).split(':'))
            close_h, close_m = map(int, str(period[1]).split(':'))
            open_min  = open_h  * 60 + open_m
            close_min = close_h * 60 + close_m
            if open_min <= cur_minutes <= close_min:
                return True
        except Exception:
            # Bagger data → wees coulant, retourneer True zodat de prospect
            # toch gebeld kan worden (anders worden ze door slechte data
            # nooit meer gebeld).
            return True
    return False

def _assistant_id_for_category(settings, category):
    """Returns the Vapi assistant_id for a category.
    - no_website     : per-category column → legacy assistant_id → DEFAULT
    - broken_website : per-category column ONLY (returns '' if unset)
    - outdated_website: per-category column ONLY (returns '' if unset)
    The strict broken/outdated behaviour is intentional: if Julian ticks
    those categories without configuring their agent, the validation in
    hermes_start_run() should error rather than silently dialing with the
    wrong (no-website) agent."""
    s = settings or {}
    if category == 'no_website':
        return ((s.get('assistant_id_no_website') or '').strip()
                or (s.get('assistant_id') or '').strip()
                or HERMES_DEFAULT_ASSISTANT_ID)
    if category == 'broken_website':
        return (s.get('assistant_id_broken_website') or '').strip()
    if category == 'outdated_website':
        return (s.get('assistant_id_outdated_website') or '').strip()
    # Onbekende categorie → val terug op default assistant
    return (s.get('assistant_id') or '').strip() or HERMES_DEFAULT_ASSISTANT_ID

def _auto_website_category(website_val):
    """Derive category from the website analysis value.
    Matches the frontend _websiteCategory() logic but maps mediocre/bad → outdated:
      - '' / null / 0 / 'no...' / 'none'                    → None (= no_website)
      - 1 / '1' / >=90 / 'good ...'                         → 'good' (don't call)
      - 1-89 number / 'mediocre' / 'ok' / 'bad'             → 'outdated'
      - 'broken...'                                          → 'broken'
    Returns: 'broken' | 'outdated' | 'good' | None"""
    v = (website_val or '').strip().lower()
    if not v: return None
    # Numerieke score?
    try:
        n = float(v)
        if n == 0:    return None
        if n >= 90:   return 'good'
        if n >= 1:    return 'outdated'    # mediocre OR bad — Julian wil beide als outdated
        return None
    except (ValueError, TypeError):
        pass
    # String classificatie (zelfde mapping als de frontend _analysisBadge)
    if v.startswith('good'):                              return 'good'
    if v.startswith('mediocre') or v.startswith('ok'):    return 'outdated'
    if v == 'bad' or v.startswith('bad '):                return 'outdated'
    if v.startswith('broken'):                            return 'broken'
    if v.startswith('no ') or v == 'none':                return None
    return None

def _resolve_prospect_category(row):
    """Return the SINGLE Hermes category a prospect belongs to (or None if
    it shouldn't be cold-called — e.g. has a good working website).
    Manual website_status override wint over de auto-derive uit website-score."""
    website = (row.get('website') or '').strip()
    wstatus = (row.get('website_status') or '').strip().lower()

    # 1. Handmatige override
    if wstatus == 'broken':   return 'broken_website'
    if wstatus == 'outdated': return 'outdated_website'

    # 2. Auto-derive uit website-analyse score
    auto = _auto_website_category(website)
    if auto == 'broken':   return 'broken_website'
    if auto == 'outdated': return 'outdated_website'
    if auto == 'good':     return None   # skip — werkende website, niet bellen

    # 3. Geen website / onleesbare score → no_website
    return 'no_website'

def _prospect_matches_category(row, category):
    """True als de prospect-rij in de gevraagde categorie valt."""
    return _resolve_prospect_category(row) == category


# ── Runs: list / detail / start / stop ────────────────────────────────────
@app.route('/api/sales/hermes/runs', methods=['GET'])
@require_sales_auth
def hermes_runs_list():
    try:
        res = db.table('hermes_runs').select('*').order('started_at', desc=True).limit(50).execute()
        runs = res.data or []
        # Voor elke 'running' run: recount en injecteer num_picked_up dynamisch
        # (zonder migratie afhankelijk te zijn van de num_picked_up kolom).
        running_ids = [r['id'] for r in runs if (r.get('status') or '') == 'running']
        for rid in running_ids:
            try: _hermes_recount_run(rid)
            except Exception: pass
        if running_ids:
            try:
                res2 = db.table('hermes_runs').select('*').order('started_at', desc=True).limit(50).execute()
                runs = res2.data or runs
            except Exception: pass
        # Inject num_picked_up dynamisch (column hoeft niet te bestaan)
        for r in runs:
            try:
                c = _hermes_compute_counts(r['id'])
                r['num_picked_up'] = c['num_picked_up']
            except Exception: r['num_picked_up'] = r.get('num_picked_up') or 0
        return jsonify({'success': True, 'runs': runs})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:200]}), 500

@app.route('/api/sales/hermes/runs/<rid>', methods=['GET'])
@require_sales_auth
def hermes_run_detail(rid):
    try:
        run_r = db.table('hermes_runs').select('*').eq('id', rid).limit(1).execute()
        if not run_r.data:
            return jsonify({'success': False, 'error': 'Run niet gevonden.'}), 404
        run_row = run_r.data[0]
        # Recount on every detail-fetch zodat de counters meteen actueel zijn,
        # zelfs als dispatch-errors (die geen webhook triggeren) ondertussen
        # statussen hebben veranderd. Recount is een goedkope query op de
        # prospect_list rijen van deze run.
        if (run_row.get('status') or '') == 'running':
            try: _hermes_recount_run(rid)
            except Exception: pass
            # Re-fetch run row zodat we de nieuwe counters teruggeven
            try:
                rr2 = db.table('hermes_runs').select('*').eq('id', rid).limit(1).execute()
                if rr2.data: run_row = rr2.data[0]
            except Exception: pass
        rows = db.table('prospect_list').select('id,company_name,phone,city,niche,website,hermes_status,hermes_outcome,hermes_ended_reason,hermes_called_at,hermes_summary,hermes_recording_url,hermes_call_id,hermes_warm_lead_id').eq('hermes_run_id', rid).execute()
        # Inject num_picked_up dynamisch (geen DB-kolom vereist)
        try:
            c = _hermes_compute_counts(rid)
            run_row['num_picked_up'] = c['num_picked_up']
        except Exception: run_row.setdefault('num_picked_up', 0)
        return jsonify({'success': True, 'run': run_row, 'prospects': rows.data or []})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:200]}), 500

@app.route('/api/sales/hermes/runs/<rid>/cancel', methods=['POST'])
@require_sales_auth
def hermes_run_cancel(rid):
    """Cancels a Hermes run AND tries to forcibly end any in-flight Vapi
    calls so they don't keep dialing after the user clicks Stop."""
    terminated = 0
    failed     = 0
    try:
        # 1. Mark run as cancelled
        db.table('hermes_runs').update({
            'status':   'cancelled',
            'ended_at': datetime.now(timezone.utc).isoformat(),
        }).eq('id', rid).execute()

        # 2. Find prospects in this run that are still actively calling
        active = db.table('prospect_list').select('id,hermes_call_id').eq('hermes_run_id', rid).eq('hermes_status', 'calling').execute()

        # 3. Forcibly end each active Vapi call
        for row in (active.data or []):
            call_id = (row.get('hermes_call_id') or '').strip()
            if not call_id:
                continue
            # Best-effort: try Vapi's PATCH /call/{id} to end the call.
            # If Vapi rejects it (older API versions), we at least mark our
            # DB as cancelled so the dashboard shows the truth.
            try:
                _requests.patch(
                    f'{VAPI_BASE_URL}/call/{call_id}',
                    headers=_vapi_headers(),
                    json={'endedReason': 'assistant-forwarded-call'},
                    timeout=10,
                )
                terminated += 1
            except Exception as e:
                failed += 1
                print(f'[HERMES-CANCEL] vapi terminate failed for {call_id}: {e}')
            # Always update our own DB row so the UI doesn't keep showing "calling"
            try:
                db.table('prospect_list').update({
                    'hermes_status':       'niet_opgenomen',
                    'hermes_outcome':      'no_answer',
                    'hermes_ended_reason': 'cancelled_by_user',
                }).eq('id', row['id']).execute()
            except Exception: pass

        # 4. Recount the run buckets so num_called etc. reflect reality
        _hermes_recount_run(rid)
        return jsonify({'success': True, 'terminated': terminated, 'failed': failed})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:200]}), 500


# Vapi prijs per minuut (NL outbound) — single source of truth voor cost calc.
HERMES_COST_PER_MINUTE_EUR = 0.23

# Speciale commissie-regel voor Hermes (AI cold-call) leads:
# alleen Timon Slingerland krijgt commissie wanneer hij de Hermes ronde
# startte (75%). Iedereen anders (Julian, rest van team) → 0%.
HERMES_TIMON_COMMISSION_RATE = 0.75

_HERMES_TIMON_ID_CACHE = None
def _hermes_get_timon_id():
    """Lookup Timon Slingerland's sales_member id (gecached). Returns
    None als 'ie niet in de tabel staat."""
    global _HERMES_TIMON_ID_CACHE
    if _HERMES_TIMON_ID_CACHE is not None:
        return _HERMES_TIMON_ID_CACHE
    try:
        res = db.table('sales_members').select('id,name').ilike('name', '%timon%').limit(2).execute()
        if res.data:
            # Eerste resultaat dat 'slingerland' in naam heeft, anders eerste hit
            for r in res.data:
                if 'slingerland' in (r.get('name') or '').lower():
                    _HERMES_TIMON_ID_CACHE = str(r['id'])
                    return _HERMES_TIMON_ID_CACHE
            _HERMES_TIMON_ID_CACHE = str(res.data[0]['id'])
            return _HERMES_TIMON_ID_CACHE
    except Exception as e:
        print(f'[HERMES-COMMISSION] timon lookup failed: {e}')
    return None

def _hermes_commission_rate_for_starter(starter_id):
    """Returns commission rate (0.0 - 1.0) voor Hermes lead, gebaseerd op
    wie de run startte. Alleen Timon krijgt 75%, anders 0%."""
    if not starter_id:
        return 0.0
    timon_id = _hermes_get_timon_id()
    if timon_id and str(starter_id) == timon_id:
        return HERMES_TIMON_COMMISSION_RATE
    return 0.0

@app.route('/api/sales/hermes/stats', methods=['GET'])
@require_sales_auth
def hermes_stats():
    """Per-persoon Hermes cold-call statistieken + team totaal.
    Query params:
      - period: daily / weekly / monthly / total (default total)
    Aggregeert vanuit hermes_runs (gefilterd op started_at) gejoind met
    prospect_list (voor duration + outcome counts per run).
    Pricing: HERMES_COST_PER_MINUTE_EUR (= €0.23/min).
    """
    from datetime import timezone, timedelta
    now = datetime.now(timezone.utc)
    period = (request.args.get('period') or 'total').strip().lower()
    # Calendar-aligned windows zodat 'Maand' = 'deze kalendermaand' (matcht
    # de commission-overzichten ipv rolling 30-dagen).
    if period == 'daily':
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    elif period == 'weekly':
        monday = now - timedelta(days=now.weekday())
        cutoff = monday.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    elif period == 'monthly':
        cutoff = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    else:
        period = 'total'
        cutoff = None

    # 1) Fetch alle hermes_runs (gefilterd op cutoff)
    try:
        rq = db.table('hermes_runs').select('id,started_by_id,started_by_name,started_at,trigger,num_prospects,num_warm,num_called,num_no_answer')
        if cutoff: rq = rq.gte('started_at', cutoff)
        runs = (rq.execute().data or [])
    except Exception as e:
        return jsonify({'success': False, 'error': f'Run fetch faalde: {e}'}), 500

    # NB: ook als runs leeg is, gaan we door zodat orphan-closes (warm leads
    # die in deze periode sluiten van runs buiten de periode) wel revenue
    # tonen. De normale aggregatie-loop draait dan met team={...,zero,...}.

    # 2) Voor alle runs: fetch prospect_list rijen (duration + outcome) per run_id
    run_ids = [r['id'] for r in runs]
    # Supabase .in_() heeft limiet — chunk in batches van 100
    prospects_by_run = {rid: [] for rid in run_ids}
    try:
        cols = 'hermes_run_id,hermes_outcome,hermes_ended_reason,hermes_call_duration_sec'
        for i in range(0, len(run_ids), 100):
            chunk = run_ids[i:i+100]
            res = db.table('prospect_list').select(cols).in_('hermes_run_id', chunk).execute()
            for r in (res.data or []):
                rid = r.get('hermes_run_id')
                if rid in prospects_by_run:
                    prospects_by_run[rid].append(r)
    except Exception as e:
        # Defensief: kolom kan nog niet bestaan → fetch zonder duration en val terug
        if 'hermes_call_duration_sec' in str(e).lower():
            try:
                cols_min = 'hermes_run_id,hermes_outcome,hermes_ended_reason'
                for i in range(0, len(run_ids), 100):
                    chunk = run_ids[i:i+100]
                    res = db.table('prospect_list').select(cols_min).in_('hermes_run_id', chunk).execute()
                    for r in (res.data or []):
                        rid = r.get('hermes_run_id')
                        if rid in prospects_by_run:
                            prospects_by_run[rid].append(r)
            except Exception as e2:
                print(f'[HERMES-STATS] fallback prospect fetch failed: {e2}')
        else:
            print(f'[HERMES-STATS] prospect fetch failed: {e}')

    # 3) Fetch closed warm_leads die uit Hermes komen (omzet + commissie)
    #    in dezelfde periode. Per-starter aggregeren.
    revenue_by_starter   = {}   # starter_id → sum closed_amount
    commission_by_starter = {}  # starter_id → sum commission_amount
    closes_by_starter    = {}   # starter_id → count closes
    try:
        lq = db.table('warm_leads').select('hermes_started_by_id,closed_amount,commission_amount,closed_at,status').not_.is_('hermes_started_by_id', 'null').eq('status', 'closed')
        if cutoff: lq = lq.gte('closed_at', cutoff)
        closed_leads = lq.execute().data or []
        for r in closed_leads:
            sid = r.get('hermes_started_by_id') or ''
            revenue_by_starter[sid]    = revenue_by_starter.get(sid, 0.0)    + float(r.get('closed_amount') or 0)
            commission_by_starter[sid] = commission_by_starter.get(sid, 0.0) + float(r.get('commission_amount') or 0)
            closes_by_starter[sid]     = closes_by_starter.get(sid, 0)       + 1
    except Exception as e:
        print(f'[HERMES-STATS] closed_leads fetch failed: {e}')

    # 4) Aggregeer per (started_by_id, started_by_name) en team totaal
    def _make_bucket():
        return {'runs': 0, 'calls_placed': 0, 'duration_sec': 0,
                'warm_leads': 0, 'picked_up': 0, 'no_answer': 0, 'benaderd': 0,
                'revenue_eur': 0.0, 'commission_eur': 0.0, 'closes': 0}

    per_person_map = {}   # key = (id, name) → bucket
    team = _make_bucket()
    sid_to_key = {}       # starter_id → key (zodat we revenue kunnen mappen)

    for run in runs:
        sid   = run.get('started_by_id') or ''
        sname = run.get('started_by_name') or ('Cron (auto)' if (run.get('trigger') == 'cron') else 'Onbekend')
        key   = (sid, sname)
        if key not in per_person_map:
            per_person_map[key] = _make_bucket()
        if sid:
            sid_to_key[sid] = key
        bucket = per_person_map[key]

        bucket['runs'] += 1
        team['runs']   += 1

        # Per prospect in deze run
        for p in prospects_by_run.get(run['id'], []):
            dur = int(p.get('hermes_call_duration_sec') or 0)
            out = (p.get('hermes_outcome') or '').lower()
            er  = (p.get('hermes_ended_reason') or '').lower()
            # Tel als 'placed' alleen prospects die echt een terminal outcome
            # hebben gehad (warm, benaderd, no_answer) — exclude pending/queued
            if out in ('warm', 'benaderd', 'no_answer', 'not_interested',
                       'invalid_number', 'failed'):
                bucket['calls_placed'] += 1
                team['calls_placed']   += 1
                bucket['duration_sec'] += dur
                team['duration_sec']   += dur
            if out == 'warm':
                bucket['warm_leads'] += 1
                team['warm_leads']   += 1
                bucket['picked_up']  += 1
                team['picked_up']    += 1
            elif out in ('benaderd', 'not_interested'):
                bucket['benaderd'] += 1
                team['benaderd']   += 1
            elif out in ('no_answer', 'invalid_number', 'failed'):
                bucket['no_answer'] += 1
                team['no_answer']   += 1
            if out != 'warm' and er in _HERMES_PICKUP_REASONS:
                bucket['picked_up'] += 1
                team['picked_up']   += 1

    # 5) Merge revenue/commission/closes per persoon. Voor starters die wel
    #    een close hebben in deze periode maar geen run (key niet bestaand):
    #    voeg een aparte bucket toe met enkel revenue (cost = 0).
    for sid, rev in revenue_by_starter.items():
        key = sid_to_key.get(sid)
        if key is None:
            # Orphan close: starter heeft geen run in deze periode. Hang 'm
            # op een aparte bucket — name lookup via sales_members.
            sname = 'Onbekend'
            try:
                if sid:
                    mr = db.table('sales_members').select('name').eq('id', sid).limit(1).execute()
                    if mr.data: sname = mr.data[0].get('name') or sname
            except Exception: pass
            key = (sid, sname)
            per_person_map[key] = _make_bucket()
            sid_to_key[sid] = key
        per_person_map[key]['revenue_eur']    += rev
        per_person_map[key]['commission_eur'] += commission_by_starter.get(sid, 0.0)
        per_person_map[key]['closes']         += closes_by_starter.get(sid, 0)
        team['revenue_eur']    += rev
        team['commission_eur'] += commission_by_starter.get(sid, 0.0)
        team['closes']         += closes_by_starter.get(sid, 0)

    def _finalize(b, sid='', sname=''):
        dur_min  = round(b['duration_sec'] / 60.0, 1)
        cost     = round((b['duration_sec'] / 60.0) * HERMES_COST_PER_MINUTE_EUR, 2)
        revenue  = round(b['revenue_eur'], 2)
        commission = round(b['commission_eur'], 2)
        # Winst = omzet - (kosten van Hermes runs + commissie die uitbetaald wordt)
        profit   = round(revenue - cost - commission, 2)
        placed   = b['calls_placed']
        conv     = round((b['warm_leads'] / placed) * 100, 1) if placed > 0 else 0.0
        pickrate = round((b['picked_up'] / placed) * 100, 1) if placed > 0 else 0.0
        # Gemiddelde kosten per (geprobeerde) prospect — null als nog geen
        # placed call zodat de UI 'n duidelijke '—' kan tonen ipv €0.
        avg_cost_per_prospect = round(cost / placed, 4) if placed > 0 else None
        # Gemiddelde kosten per warme lead — null tot er ten minste 1 warm is.
        avg_cost_per_warm = round(cost / b['warm_leads'], 2) if b['warm_leads'] > 0 else None
        # ROI = (omzet - kosten) / kosten × 100. Null als er nog geen kosten
        # gemaakt zijn of nog geen omzet (UI toont 'n placeholder).
        roi_pct = round(((revenue - cost) / cost) * 100, 1) if cost > 0 and revenue > 0 else None
        out = {
            'runs':                b['runs'],
            'calls_placed':        b['calls_placed'],
            'duration_sec':        b['duration_sec'],
            'duration_min':        dur_min,
            'cost_eur':            cost,
            'warm_leads':          b['warm_leads'],
            'picked_up':           b['picked_up'],
            'benaderd':            b['benaderd'],
            'no_answer':           b['no_answer'],
            'conversion_rate_pct':     conv,
            'pickup_rate_pct':         pickrate,
            'avg_cost_per_prospect':   avg_cost_per_prospect,
            'avg_cost_per_warm_lead':  avg_cost_per_warm,
            'roi_pct':                 roi_pct,
            'revenue_eur':         revenue,
            'commission_eur':      commission,
            'profit_eur':          profit,
            'closes':              b['closes'],
        }
        if sname is not None:
            out['starter_id']   = sid or None
            out['starter_name'] = sname
        return out

    per_person = sorted(
        [_finalize(b, sid, sname) for (sid, sname), b in per_person_map.items()],
        key=lambda r: r['revenue_eur'],
        reverse=True,
    )

    return jsonify({
        'success': True,
        'period': period,
        'cost_per_minute_eur': HERMES_COST_PER_MINUTE_EUR,
        'team_total': _finalize(team),
        'per_person': per_person,
    })


@app.route('/api/sales/hermes/start', methods=['POST'])
@require_sales_auth
def hermes_start_run():
    """Kicks off a Hermes run: selects prospects matching filters, fires
    outbound Vapi calls, marks them as 'calling'. Returns immediately —
    individual call results land via the webhook."""
    s = _hermes_settings()
    if not VAPI_API_KEY:
        return jsonify({'success': False, 'error': 'VAPI_API_KEY ontbreekt op de server.'}), 400
    if not s.get('phone_number_id'):
        return jsonify({'success': False, 'error': 'Vapi phone_number_id ontbreekt — zet in Settings.'}), 400

    body = request.get_json(silent=True) or {}

    # ── Categorieën / filters parsen ──────────────────────────────────────
    only_uncalled  = bool(body.get('only_uncalled') if 'only_uncalled' in body else s.get('filter_uncalled_only'))
    niche_filter   = (body.get('niche') or '').strip().lower()
    only_open_now  = bool(body.get('only_open_now'))
    dry_run        = bool(body.get('dry_run'))
    now_local      = _nl_now() if only_open_now else None

    # ── Optioneel: expliciete prospect_ids lijst ──────────────────────────
    # Als gevuld → die specifieke prospects worden gebeld (max_parallel uit
    # de eerste category cfg of een default). Categorieën worden afgeleid
    # uit elke prospect z'n website-data.
    explicit_ids = body.get('prospect_ids') or []
    if not isinstance(explicit_ids, list): explicit_ids = []
    explicit_ids = [str(x) for x in explicit_ids if x]

    # Nieuwe shape: categories = [{category, max_calls, max_parallel}, ...]
    raw_cats = body.get('categories') or []
    cats_cfg = []
    if raw_cats:
        for c in raw_cats:
            if not isinstance(c, dict): continue
            cat = (c.get('category') or '').strip()
            if cat not in HERMES_CATEGORIES: continue
            mc  = max(1, int(c.get('max_calls')    or s.get('max_calls_default')    or 50))
            mp  = max(1, int(c.get('max_parallel') or s.get('max_parallel_default') or 2))
            cats_cfg.append({'category': cat, 'max_calls': mc, 'max_parallel': mp})
    else:
        max_calls    = int(body.get('max_calls')    or s.get('max_calls_default')    or 50)
        max_parallel = int(body.get('max_parallel') or s.get('max_parallel_default') or 2)
        cats_cfg = [{'category': 'no_website', 'max_calls': max_calls, 'max_parallel': max_parallel}]
        if explicit_ids:
            # Bij explicit_ids willen we ALLE 3 categorieën als fallback omdat
            # we per prospect de juiste agent moeten kunnen kiezen.
            cats_cfg = [{'category': c, 'max_calls': len(explicit_ids), 'max_parallel': max_parallel} for c in HERMES_CATEGORIES]

    if not cats_cfg:
        return jsonify({'success': False, 'error': 'Geen categorieën geselecteerd.'}), 400

    # Validatie: elke categorie moet een assistant_id hebben (kan ook fallback zijn)
    missing = []
    for c in cats_cfg:
        if not _assistant_id_for_category(s, c['category']):
            missing.append(c['category'])
    if missing:
        return jsonify({'success': False, 'error': f"Geen Vapi assistant_id voor: {', '.join(missing)}. Zet 'm in Settings."}), 400

    # ── Per-categorie prospect selectie ──────────────────────────────────
    # ── Auto-recovery: prospects die "stuck" zitten in queued/calling status
    # van een run die al lang geleden is gestopt of mislukt. Anders blijven
    # die prospects permanent geblokkeerd voor toekomstige runs. We zetten
    # ze terug op niet_opgenomen zodat ze opnieuw geselecteerd kunnen
    # worden in deze ronde. Threshold: alle queued/calling van runs die
    # NIET running zijn, OF van running runs die > 30 min oud zijn.
    try:
        from datetime import timezone as _tz_, timedelta as _td_
        stale_cutoff = (datetime.now(_tz_.utc) - _td_(minutes=30)).isoformat()
        # 1. Verzamel run IDs die "niet meer actief" zijn
        try:
            old_runs = db.table('hermes_runs').select('id,status,started_at').execute().data or []
        except Exception: old_runs = []
        recoverable_run_ids = []
        for orun in old_runs:
            st = (orun.get('status') or '').lower()
            if st in ('completed','cancelled','failed'):
                recoverable_run_ids.append(orun['id'])
            elif st == 'running' and (orun.get('started_at') or '') < stale_cutoff:
                recoverable_run_ids.append(orun['id'])
        # 2. Reset prospects met hermes_status in stuck-set, hermes_run_id in recoverable
        if recoverable_run_ids:
            try:
                # in_ heeft een max — chunk
                recovered_total = 0
                for i in range(0, len(recoverable_run_ids), 100):
                    chunk = recoverable_run_ids[i:i+100]
                    upd = db.table('prospect_list').update({
                        'hermes_status': 'niet_opgenomen',
                    }).in_('hermes_status', ['queued','calling']).in_('hermes_run_id', chunk).execute()
                    recovered_total += len(upd.data or [])
                if recovered_total:
                    print(f'[HERMES-START] auto-recovered {recovered_total} stuck prospects')
            except Exception as e:
                print(f'[HERMES-START] auto-recovery failed: {e}')
    except Exception as e:
        print(f'[HERMES-START] auto-recovery outer failed: {e}')

    # Aparte server-side fetch per categorie voorkomt starvation: als categorie
    # A toevallig de hele buffer vult, krijgen B/C anders 0 prospects ook al
    # zijn er genoeg in de DB. Per-categorie fetch fixt dat.
    def _fetch_cat_rows(cat, limit):
        cols = 'id,company_name,phone,city,niche,website,website_status,opening_hours,called,hermes_status'
        # Hogere buffer voor categorieën die auto-gedetecteerd worden uit
        # de 'website' kolom (broken/outdated) — die rijen hebben meestal
        # GEEN website_status manual override, dus we kunnen ze niet
        # server-side filteren. Pak dus een ruime batch en filter Python-side.
        # no_website: meestal de grootste groep, ruime batch werkt prima.
        # broken/outdated: vaak een kleinere fractie, dus 10x buffer.
        if cat in ('broken_website', 'outdated_website'):
            buf_mult = 20 if only_open_now else 10
        else:
            buf_mult = 6 if only_open_now else 3
        q = db.table('prospect_list').select(cols).limit(max(limit * buf_mult, 100))
        if only_uncalled: q = q.eq('called', False)
        # GEEN server-side website_status filter meer — prospects worden
        # auto-geclassificeerd uit de 'website' kolom in Python via
        # _prospect_matches_category. Een server-side filter op
        # website_status zou alle auto-classified prospects missen.
        try:
            return q.execute().data or []
        except Exception as e:
            print(f'[HERMES-START] cat-fetch {cat} fallback: {e}')
            try:
                cols_min = 'id,company_name,phone,city,niche,website,called,hermes_status'
                qf = db.table('prospect_list').select(cols_min).limit(max(limit * buf_mult, 100))
                if only_uncalled: qf = qf.eq('called', False)
                return qf.execute().data or []
            except Exception as e2:
                print(f'[HERMES-START] fallback fetch also failed: {e2}')
                return []

    seen_ids = set()
    selected = []   # list of (category, prospect_dict)
    per_cat_count = {c['category']: 0 for c in cats_cfg}
    # Skip-reason counters voor shortfall diagnostiek
    skipped = {'already_inflight': 0, 'no_phone': 0, 'wrong_niche': 0,
               'wrong_category': 0, 'closed_now': 0}

    if explicit_ids:
        # ── Explicit prospect_ids mode ───────────────────────────────────
        # Fetch alleen die prospects; resolve hun category per stuk.
        try:
            cols = 'id,company_name,phone,city,niche,website,website_status,opening_hours,called,hermes_status'
            qe = db.table('prospect_list').select(cols).in_('id', explicit_ids)
            rows = qe.execute().data or []
        except Exception as e:
            print(f'[HERMES-START] explicit fetch failed: {e}')
            rows = []
        for r in rows:
            if r.get('hermes_status') in ('calling','queued'):    skipped['already_inflight'] += 1; continue
            if not _normalize_phone_e164(r.get('phone')):         skipped['no_phone'] += 1; continue
            if only_open_now and not _is_open_now(r, now_local):  skipped['closed_now'] += 1; continue
            actual_cat = _resolve_prospect_category(r)
            if not actual_cat:                                    skipped['wrong_category'] += 1; continue
            seen_ids.add(r['id'])
            selected.append((actual_cat, r))
            per_cat_count[actual_cat] = per_cat_count.get(actual_cat, 0) + 1
    else:
        # ── Normale categorie-gedreven selectie ──────────────────────────
        for cat_cfg in cats_cfg:
            cat   = cat_cfg['category']
            limit = cat_cfg['max_calls']
            rows  = _fetch_cat_rows(cat, limit)
            for r in rows:
                if r['id'] in seen_ids:                                continue
                if r.get('hermes_status') in ('calling','queued'):    skipped['already_inflight'] += 1; continue
                if niche_filter and niche_filter not in (r.get('niche') or '').lower(): skipped['wrong_niche'] += 1; continue
                if not _normalize_phone_e164(r.get('phone')):         skipped['no_phone'] += 1; continue
                if not _prospect_matches_category(r, cat):            skipped['wrong_category'] += 1; continue
                if only_open_now and not _is_open_now(r, now_local):  skipped['closed_now'] += 1; continue
                seen_ids.add(r['id'])
                selected.append((cat, r))
                per_cat_count[cat] += 1
                if per_cat_count[cat] >= limit: break

    if not selected:
        # ── Diagnostiek: laat zien wat WEL beschikbaar is per categorie ──
        diag = {}
        try:
            cols = 'id,phone,niche,website,website_status,called,hermes_status'
            qd = db.table('prospect_list').select(cols).limit(20000)
            if only_uncalled: qd = qd.eq('called', False)
            all_rows = qd.execute().data or []
            for c in HERMES_CATEGORIES:
                diag[c] = 0
            for r in all_rows:
                if r.get('hermes_status') in ('calling','queued'): continue
                if niche_filter and niche_filter not in (r.get('niche') or '').lower(): continue
                if not _normalize_phone_e164(r.get('phone')): continue
                cat = _resolve_prospect_category(r)
                if cat in diag: diag[cat] += 1
        except Exception as e:
            print(f'[HERMES-START] diagnose failed: {e}')
        # Stel een leesbare tekst samen
        diag_txt = ', '.join(f"{k.replace('_website','').replace('_',' ')}={v}" for k, v in diag.items()) if diag else ''
        selected_cats = ', '.join(c['category'].replace('_website','').replace('_',' ') for c in cats_cfg)
        filter_hint = []
        if only_uncalled: filter_hint.append('nog-niet-benaderde')
        if niche_filter:  filter_hint.append(f"niche='{niche_filter}'")
        if only_open_now: filter_hint.append('nu open')
        filter_txt = (' (filters: ' + ', '.join(filter_hint) + ')') if filter_hint else ''
        err = (
            f"Geen prospects voldoen aan de filters{filter_txt}. "
            f"Je selecteerde: {selected_cats}. "
            + (f"Beschikbaar in de DB: {diag_txt}." if diag_txt else '')
        )
        return jsonify({'success': False, 'error': err, 'diagnostic': diag}), 400

    mid = _get_sales_member_id()
    mname = None
    try:
        mres = db.table('sales_members').select('name').eq('id', mid).limit(1).execute() if mid else None
        mname = mres.data[0]['name'] if (mres and mres.data) else None
    except Exception: pass

    # ── Run row aanmaken ──────────────────────────────────────────────────
    cat_summary = ','.join(c['category'] for c in cats_cfg)
    # SUM ipv MAX: per-cat max_parallel is additief — als user 5+3+1 zet wil
    # 'ie 9 totaal parallel, niet 5. Vapi's eigen concurrency-limit + retry
    # logic in _fire vangt overschrijdingen sowieso op.
    max_parallel_overall = sum(c['max_parallel'] for c in cats_cfg)
    max_calls_overall    = sum(c['max_calls'] for c in cats_cfg)
    filter_summary = (
        f"categories={cat_summary}"
        + (f" niche={niche_filter}" if niche_filter else '')
        + (' uncalled_only' if only_uncalled else '')
        + (' open_now' if only_open_now else '')
    )

    run_id = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    run_row = {
        'id':              run_id,
        'started_by_id':   str(mid) if mid else None,
        'started_by_name': mname,
        'trigger':         'manual',
        'status':          'running',
        'num_prospects':   len(selected),
        'max_calls':       max_calls_overall,
        'max_parallel':    max_parallel_overall,
        'categories':      cat_summary,
        'filter_summary':  filter_summary,
    }
    try:
        db.table('hermes_runs').insert(run_row).execute()
    except Exception as e:
        # Defensief: als de `categories` kolom nog niet bestaat (migratie
        # niet gedraaid), retry zonder die kolom. Anders crashed elke run.
        if 'categories' in str(e).lower():
            try:
                run_row.pop('categories', None)
                db.table('hermes_runs').insert(run_row).execute()
            except Exception as e2:
                return jsonify({'success': False, 'error': f'Run aanmaken faalde: {e2}'}), 500
        else:
            return jsonify({'success': False, 'error': f'Run aanmaken faalde: {e}'}), 500

    # ── Dry-run: laat zien wat geselecteerd zou zijn ─────────────────────
    if dry_run:
        try:
            db.table('hermes_runs').update({'status': 'completed',
                                            'ended_at': datetime.now(timezone.utc).isoformat(),
                                            'notes': 'dry-run — geen echte calls gemaakt'}).eq('id', run_id).execute()
        except Exception: pass
        return jsonify({
            'success': True, 'run_id': run_id, 'dry_run': True,
            'per_category': per_cat_count,
            'queued': [{'id': r['id'], 'name': r['company_name'], 'phone': r['phone'], 'category': cat}
                       for (cat, r) in selected],
        })

    # NB: queue-marking is verplaatst naar de background thread zodat het
    # /start request <1s blijft. Anders deden we N sequentiële DB-updates
    # (1 per prospect) vóór de return en triggerde dat 'verbindingsfout'
    # toasts in de UI bij grote runs.

    # ── Background dispatch (gunicorn worker zou anders 120s timeout halen) ──
    # Spawn een daemon thread die de calls feitelijk naar Vapi vuurt; het
    # request endpoint zelf returnt direct met 'queued' status zodat de
    # gebruiker geen "geen verbinding" toast krijgt door een lang request.
    def _background_dispatch(s_local, run_id_local, selected_local, max_parallel_local):
        import concurrent.futures, time as _time
        # Eerst: markeer alle geselecteerde prospects als 'queued' zodat ze
        # in de UI verschijnen voordat Vapi überhaupt heeft kunnen oppakken.
        try:
            queued_now = datetime.now(timezone.utc).isoformat()
            for (cat, p) in selected_local:
                try:
                    db.table('prospect_list').update({
                        'hermes_status':    'queued',
                        'hermes_run_id':    run_id_local,
                        'hermes_category':  cat,
                        'hermes_called_at': queued_now,
                    }).eq('id', p['id']).execute()
                except Exception as e:
                    print(f'[HERMES-BG] queue mark failed for {p.get("id")}: {e}')
        except Exception as e:
            print(f'[HERMES-BG] queue marking loop failed: {e}')

        def _fire(item):
            cat, prospect = item
            try:
                num = _normalize_phone_e164(prospect.get('phone'))
                raw_name  = (prospect.get('company_name') or '').strip()
                safe_name = raw_name[:40]
                assistant_id = _assistant_id_for_category(s_local, cat)
                call_id   = None
                last_err  = None
                for attempt in range(10):
                    try:
                        call_id = _vapi_start_call(
                            assistant_id    = assistant_id,
                            phone_number_id = s_local.get('phone_number_id'),
                            customer_number = num,
                            customer_name   = safe_name,
                            variable_values = {
                                'company_name': raw_name,
                                'city':         prospect.get('city')  or '',
                                'niche':        prospect.get('niche') or '',
                            },
                        )
                        break
                    except RuntimeError as ve:
                        msg = str(ve).lower()
                        last_err = ve
                        if ('over concurrency limit' in msg
                            or 'rate limit' in msg
                            or 'vapi error 429' in msg):
                            wait = min(2 + attempt * 3, 25)
                            _time.sleep(wait)
                            continue
                        raise
                if not call_id:
                    raise last_err or RuntimeError('dispatch_giveup')
                try:
                    db.table('prospect_list').update({
                        'hermes_status':    'calling',
                        'hermes_run_id':    run_id_local,
                        'hermes_call_id':   call_id,
                        'hermes_called_at': datetime.now(timezone.utc).isoformat(),
                        'hermes_category':  cat,
                    }).eq('id', prospect['id']).execute()
                except Exception as up_err:
                    print(f'[HERMES-BG] post-dispatch update failed for {prospect.get("id")}: {up_err}')
                return True
            except Exception as e:
                print(f'[HERMES-BG] dispatch failed for {prospect.get("id")} ({cat}): {e}')
                try:
                    db.table('prospect_list').update({
                        'hermes_status':       'niet_opgenomen',
                        'hermes_run_id':       run_id_local,
                        'hermes_outcome':      'no_answer',
                        'hermes_ended_reason': f'dispatch_error: {str(e)[:120]}',
                        'hermes_category':     cat,
                    }).eq('id', prospect['id']).execute()
                except Exception: pass
                return False
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, max_parallel_local)) as pool:
                results = list(pool.map(_fire, selected_local))
            placed = sum(1 for ok in results if ok)
            print(f'[HERMES-BG] run {run_id_local}: {placed}/{len(selected_local)} dispatched')
        except Exception as e:
            print(f'[HERMES-BG] thread crashed for run {run_id_local}: {e}')
        finally:
            try: _hermes_recount_run(run_id_local)
            except Exception: pass

    import threading
    t = threading.Thread(
        target=_background_dispatch,
        args=(s, run_id, selected, max_parallel_overall),
        daemon=True,
        name=f'hermes-dispatch-{run_id}',
    )
    t.start()

    requested_total = sum(c['max_calls'] for c in cats_cfg) if not explicit_ids else len(explicit_ids)
    shortfall = max(0, requested_total - len(selected))
    return jsonify({
        'success':           True,
        'run_id':            run_id,
        'queued':            len(selected),
        'requested':         requested_total,
        'shortfall':         shortfall,
        'skipped':           skipped,
        'placed':            0,            # background — UI ziet count groeien via polling
        'per_category':      per_cat_count,
        'per_category_requested': {c['category']: c['max_calls'] for c in cats_cfg},
        'background':        True,
    })


@app.route('/api/sales/hermes/test-call', methods=['POST'])
@require_auth
def hermes_test_call():
    """Plaats een test-call naar een opgegeven nummer zodat je de Vapi setup
    kunt valideren zonder een hele bellijst af te schieten."""
    s = _hermes_settings()
    if not VAPI_API_KEY:
        return jsonify({'success': False, 'error': 'VAPI_API_KEY ontbreekt.'}), 400
    if not s.get('assistant_id') or not s.get('phone_number_id'):
        return jsonify({'success': False, 'error': 'assistant_id / phone_number_id niet gezet.'}), 400
    data = request.get_json(silent=True) or {}
    phone = _normalize_phone_e164(data.get('phone'))
    if not phone:
        return jsonify({'success': False, 'error': 'Ongeldig telefoonnummer.'}), 400
    try:
        # Dummy variabelen meesturen zodat {{company_name}} / {{city}} / {{niche}}
        # niet leeg renderen tijdens de test — anders kapt de AI midden in de
        # opening af ("spreek ik met de eigenaar van ___").
        test_company = data.get('company_name') or 'Test Bedrijf'
        test_city    = data.get('city')         or 'Amsterdam'
        test_niche   = data.get('niche')        or 'kapsalon'
        call_id = _vapi_start_call(
            assistant_id    = s['assistant_id'],
            phone_number_id = s['phone_number_id'],
            customer_number = phone,
            customer_name   = data.get('name') or 'Test',
            variable_values = {
                'company_name': test_company,
                'city':         test_city,
                'niche':        test_niche,
            },
        )
        return jsonify({'success': True, 'call_id': call_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:300]}), 500


# ── Webhook: Vapi calls this when a call ends ─────────────────────────────
@app.route('/api/sales/hermes/cron-tick', methods=['POST', 'GET'])
def hermes_cron_tick():
    """Heartbeat endpoint for an external scheduler (Render Cron Job, GitHub
    Actions, cron-job.org, etc.). Hit this URL every ~10 minutes. The endpoint
    decides itself if NOW falls inside the configured cron_time window and
    triggers a Hermes run only when:
      - settings.cron_enabled = true
      - current local NL time is within ±10 min of settings.cron_time
      - weekday matches (if cron_weekdays_only)
      - no other run for today is already 'running' or completed today

    Shared secret via x-cron-secret header compared to env CRON_SECRET so
    randos can't trigger it.
    """
    secret_env = os.environ.get('CRON_SECRET', '').strip()
    if secret_env:
        got = request.headers.get('x-cron-secret') or request.headers.get('X-Cron-Secret') or request.args.get('s', '')
        if got != secret_env:
            return jsonify({'ok': False, 'error': 'invalid cron secret'}), 401

    s = _hermes_settings()
    if not s.get('cron_enabled'):
        return jsonify({'ok': True, 'skipped': 'cron disabled'})
    if not VAPI_API_KEY or not s.get('assistant_id') or not s.get('phone_number_id'):
        return jsonify({'ok': False, 'error': 'Vapi not configured'}), 400

    # NL local time (UTC+1 winter / +2 summer — simple offset; works for the
    # ±10 min window check without needing a tz library).
    from datetime import timezone as _tz, timedelta as _td
    now_utc = datetime.now(_tz.utc)
    # crude DST: NL is UTC+2 between last sun of march and last sun of oct.
    # Good enough for a 10-min window check; off by one hour twice a year.
    is_dst = 3 <= now_utc.month <= 10
    nl_offset = 2 if is_dst else 1
    now_nl = now_utc + _td(hours=nl_offset)
    weekday = now_nl.weekday()   # mon=0
    if s.get('cron_weekdays_only') and weekday >= 5:
        return jsonify({'ok': True, 'skipped': f'weekend (wkday={weekday})'})

    target_hhmm = s.get('cron_time') or '10:00'
    try:
        target_h, target_m = [int(x) for x in target_hhmm.split(':')[:2]]
    except Exception:
        return jsonify({'ok': False, 'error': f'bad cron_time format: {target_hhmm}'}), 500
    target_minutes = target_h * 60 + target_m
    now_minutes    = now_nl.hour * 60 + now_nl.minute
    if abs(target_minutes - now_minutes) > 10:
        return jsonify({'ok': True, 'skipped': f'outside window (now_nl={now_nl.strftime("%H:%M")}, target={target_hhmm})'})

    # Already ran today?
    today_iso = now_nl.date().isoformat()
    try:
        existing = (db.table('hermes_runs').select('id,status,started_at')
                    .gte('started_at', today_iso + 'T00:00:00')
                    .eq('trigger', 'cron')
                    .limit(1).execute())
        if existing.data:
            return jsonify({'ok': True, 'skipped': f'cron already ran today (run_id={existing.data[0]["id"]})'})
    except Exception as e:
        print(f'[HERMES-CRON] duplicate-check failed: {e}')

    # ── Run via the start endpoint's logic ────────────────────────────
    # We can't reuse hermes_start_run() directly because it requires
    # @require_sales_auth. Inline the same flow, bypassing auth.
    max_calls    = int(s.get('max_calls_default')    or 50)
    max_parallel = int(s.get('max_parallel_default') or 3)
    try:
        q = db.table('prospect_list').select('id,company_name,phone,city,niche,website,called,hermes_status').limit(max_calls * 5)
        if s.get('filter_uncalled_only'): q = q.eq('called', False)
        rows = (q.execute().data or [])
    except Exception as e:
        return jsonify({'ok': False, 'error': f'prospect fetch failed: {e}'}), 500

    cands = []
    only_no_website = bool(s.get('filter_no_website'))
    for r in rows:
        if r.get('hermes_status') in ('calling','queued'):  continue
        if only_no_website and (r.get('website') or '').strip():  continue
        if not _normalize_phone_e164(r.get('phone')):  continue
        cands.append(r)
        if len(cands) >= max_calls: break

    if not cands:
        return jsonify({'ok': True, 'skipped': 'no candidates'})

    run_id = str(int(datetime.now(_tz.utc).timestamp() * 1000))
    try:
        db.table('hermes_runs').insert({
            'id':              run_id,
            'started_by_id':   None,
            'started_by_name': 'Cron',
            'trigger':         'cron',
            'status':          'running',
            'num_prospects':   len(cands),
            'max_calls':       max_calls,
            'max_parallel':    max_parallel,
            'filter_summary':  'cron: ' + ('no_website ' if only_no_website else '') + ('uncalled_only' if s.get('filter_uncalled_only') else ''),
        }).execute()
    except Exception as e:
        return jsonify({'ok': False, 'error': f'run insert failed: {e}'}), 500

    import concurrent.futures
    def _fire(prospect):
        try:
            call_id = _vapi_start_call(
                assistant_id    = s.get('assistant_id'),
                phone_number_id = s.get('phone_number_id'),
                customer_number = _normalize_phone_e164(prospect.get('phone')),
                customer_name   = prospect.get('company_name'),
                variable_values = {
                    'company_name': prospect.get('company_name') or '',
                    'city':         prospect.get('city')         or '',
                    'niche':        prospect.get('niche')         or '',
                },
            )
            db.table('prospect_list').update({
                'hermes_status':    'calling',
                'hermes_run_id':    run_id,
                'hermes_call_id':   call_id,
                'hermes_called_at': datetime.now(_tz.utc).isoformat(),
            }).eq('id', prospect['id']).execute()
            return True
        except Exception as e:
            print(f'[HERMES-CRON] dispatch failed for {prospect.get("id")}: {e}')
            return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, max_parallel)) as pool:
        results = list(pool.map(_fire, cands))
    _hermes_recount_run(run_id)
    return jsonify({'ok': True, 'run_id': run_id, 'queued': len(cands), 'placed': sum(1 for x in results if x)})


@app.route('/api/vapi/webhook', methods=['POST'])
def vapi_webhook():
    """End-of-call webhook from Vapi. Optional shared-secret verification
    via x-vapi-secret header (compared to VAPI_WEBHOOK_SECRET env var)."""
    if VAPI_WEBHOOK_SECRET:
        got = request.headers.get('x-vapi-secret') or request.headers.get('X-Vapi-Secret')
        if got != VAPI_WEBHOOK_SECRET:
            return jsonify({'ok': False, 'error': 'invalid secret'}), 401

    body = request.get_json(silent=True) or {}
    msg  = body.get('message') or body
    mtype = msg.get('type') or ''
    # We only act on end-of-call-report. Other types (status updates, tool
    # calls fired mid-conversation) are accepted with a 200 but ignored here.
    if mtype not in ('end-of-call-report', 'status-update'):
        return jsonify({'ok': True, 'ignored_type': mtype})

    call = msg.get('call') or {}
    call_id = call.get('id') or msg.get('callId')
    if not call_id:
        return jsonify({'ok': False, 'error': 'no call id'}), 400

    # Look up the prospect by call_id
    try:
        pr = db.table('prospect_list').select('*').eq('hermes_call_id', call_id).limit(1).execute()
        if not pr.data:
            print(f'[VAPI-WEBHOOK] call_id {call_id} not in prospect_list — accepted but ignored')
            return jsonify({'ok': True, 'unknown_call': True})
        prospect = pr.data[0]
    except Exception as e:
        print(f'[VAPI-WEBHOOK] prospect lookup failed: {e}')
        return jsonify({'ok': False, 'error': 'lookup failed'}), 500

    if mtype == 'status-update':
        # nothing to commit on intermediate status updates — wait for end-of-call-report
        return jsonify({'ok': True})

    # ── end-of-call-report ────────────────────────────────────────────────
    ended_reason   = msg.get('endedReason') or call.get('endedReason') or ''
    transcript     = msg.get('transcript') or ''
    summary        = msg.get('summary') or ''
    recording_url  = msg.get('recordingUrl') or msg.get('stereoRecordingUrl') or ''
    messages_log   = msg.get('messages') or msg.get('artifact', {}).get('messages') or []

    # ── Duration extraction (voor kosten/stats per persoon) ──────────────
    # Vapi geeft duurinfo via meerdere velden afhankelijk van API versie:
    #   - msg.durationSeconds (meest gangbaar bij end-of-call-report)
    #   - msg.durationMinutes (sommige varianten)
    #   - call.startedAt / call.endedAt (fallback berekening)
    # We slaan altijd op in seconden — kosten worden client-side berekend.
    duration_sec = 0
    try:
        ds = msg.get('durationSeconds') or call.get('durationSeconds')
        if ds is not None:
            duration_sec = int(float(ds))
        else:
            dm = msg.get('durationMinutes') or call.get('durationMinutes')
            if dm is not None:
                duration_sec = int(float(dm) * 60)
            else:
                started = call.get('startedAt') or msg.get('startedAt')
                ended   = call.get('endedAt')   or msg.get('endedAt')
                if isinstance(started, str) and isinstance(ended, str):
                    from datetime import datetime as _dt
                    # Strip sub-microsecond precisie (Python < 3.11 faalt op
                    # nanoseconds) en normaliseer 'Z' → '+00:00'.
                    def _norm(v):
                        v = v.replace('Z', '+00:00')
                        # 9-digit nanoseconds? trim naar microseconds (6 digits)
                        if '.' in v:
                            head, _, tail = v.partition('.')
                            # tail tot eerste niet-cijfer + offset
                            i = 0
                            while i < len(tail) and tail[i].isdigit(): i += 1
                            v = head + '.' + tail[:6] + tail[i:]
                        return v
                    s = _dt.fromisoformat(_norm(started))
                    e = _dt.fromisoformat(_norm(ended))
                    # Force beide naar UTC-aware om naive/aware mismatch te
                    # vermijden — als één naive is, behandel als UTC.
                    if s.tzinfo is None: s = s.replace(tzinfo=timezone.utc)
                    if e.tzinfo is None: e = e.replace(tzinfo=timezone.utc)
                    duration_sec = max(0, int((e - s).total_seconds()))
    except Exception as e:
        print(f'[VAPI-WEBHOOK] duration parse failed: {e}')

    # Look for our 2 tool calls in the message log
    warm_reason    = None
    not_int_reason = None
    for m in messages_log:
        tcs = m.get('toolCalls') or []
        for tc in tcs:
            fn = (tc.get('function') or {}).get('name') or tc.get('name')
            args_raw = (tc.get('function') or {}).get('arguments') or tc.get('arguments') or '{}'
            try:    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except Exception: args = {}
            if fn == 'mark_warm_lead':      warm_reason    = args.get('reason') or 'warm'
            if fn == 'mark_not_interested': not_int_reason = args.get('reason') or 'not interested'

    # Decide the bucket.
    if warm_reason:
        outcome = 'warm'
        status  = 'warm'
    elif not_int_reason:
        outcome = 'benaderd'
        status  = 'benaderd'
    else:
        # GEEN tool call — bv. terugbel-verzoek waar AI gewoon ophangt, of
        # voicemail / dispatch fail. Classifier mapt customer-ended-call
        # NU naar 'no_answer' (niet opgenomen) zodat callback-cases op de
        # bellijst blijven staan. Voicemail/busy/invalid/pipeline-error
        # blijven 'benaderd' (off the list).
        outcome = _hermes_classify_ended_reason(ended_reason)
        status  = 'niet_opgenomen' if outcome == 'no_answer' else 'benaderd'

    # ── Mark prospect.called=true only when we actually reached someone ──
    # Callback-later, no-answer, voicemail, invalid number → laat called=false
    # zodat de prospect bij de volgende run weer wordt opgepakt.
    set_called = outcome in ('warm', 'benaderd')

    update = {
        'hermes_status':        status,
        'hermes_outcome':       outcome,
        'hermes_ended_reason':  ended_reason,
        'hermes_summary':       summary[:2000] if isinstance(summary, str) else None,
        'hermes_transcript':    transcript[:20000] if isinstance(transcript, str) else None,
        'hermes_recording_url': recording_url or None,
        'hermes_call_duration_sec': duration_sec,
    }
    if set_called:
        update['called']     = True
        update['called_at']  = update.get('called_at') or datetime.now(timezone.utc).isoformat()

    try:
        db.table('prospect_list').update(update).eq('id', prospect['id']).execute()
    except Exception as e:
        # Defensief: bij missende hermes_call_duration_sec kolom → drop en retry
        if 'hermes_call_duration_sec' in str(e).lower():
            try:
                update.pop('hermes_call_duration_sec', None)
                db.table('prospect_list').update(update).eq('id', prospect['id']).execute()
            except Exception as e2:
                print(f'[VAPI-WEBHOOK] prospect update retry failed: {e2}')
        else:
            print(f'[VAPI-WEBHOOK] prospect update failed: {e}')

    # ── Create a warm_lead row when the AI flagged it ───────────────────
    if outcome == 'warm':
        try:
            warm_id = str(int(datetime.now(timezone.utc).timestamp() * 1000))
            # ── Rich note so Julian heeft direct context als hij de lead opent ──
            from datetime import timezone as _tz_, timedelta as _td_
            now_local = (datetime.now(_tz_.utc) + _td_(hours=2 if 3 <= datetime.now(_tz_.utc).month <= 10 else 1))
            stamp = now_local.strftime('%-d %B %Y · %H:%M')
            note_parts = [
                f'🤖 Via Hermes (AI cold-call) op {stamp}',
                '',
            ]
            if warm_reason:
                note_parts.append(f'⭐ Reden warm gemarkeerd:')
                note_parts.append(warm_reason.strip())
                note_parts.append('')
            if summary:
                note_parts.append('📝 Samenvatting gesprek:')
                note_parts.append(summary.strip())
                note_parts.append('')
            if prospect.get('city') or prospect.get('niche'):
                ctx = []
                if prospect.get('city'):  ctx.append(prospect['city'])
                if prospect.get('niche'): ctx.append(prospect['niche'])
                note_parts.append(f'📍 Context: {" · ".join(ctx)}')
            if recording_url:
                note_parts.append(f'🎧 Opname: {recording_url}')
            if ended_reason:
                note_parts.append(f'☎ Vapi endedReason: {ended_reason}')
            note_text = '\n'.join(note_parts)[:4000]

            # Haal op wie deze Hermes run is gestart zodat we het kunnen
            # bewaren op de warm lead (zichtbaar in Warm Leads + Clients tab).
            run_starter_id = None
            run_starter_name = None
            try:
                run_id_for_lead = prospect.get('hermes_run_id')
                if run_id_for_lead:
                    rr = db.table('hermes_runs').select('started_by_id,started_by_name').eq('id', run_id_for_lead).limit(1).execute()
                    if rr.data:
                        run_starter_id   = rr.data[0].get('started_by_id')
                        run_starter_name = rr.data[0].get('started_by_name')
            except Exception as e:
                print(f'[VAPI-WEBHOOK] hermes_run lookup for starter failed: {e}')

            wrow = {
                'id':              warm_id,
                'company_name':    prospect.get('company_name'),
                'phone':           prospect.get('phone'),
                'maps_url':        '',
                'contact_method':  'phone',
                'pipeline_status': 'forum_nog_sturen',
                'status':          'warm',
                'added_by_id':     None,
                'added_by_name':   'Hermes (AI)',
                'created_at':      datetime.now(timezone.utc).isoformat(),
                'notes':           note_text,
                'hermes_started_by_id':   run_starter_id,
                'hermes_started_by_name': run_starter_name,
            }
            # Defensief: bij missende columns drop ze en retry. Houdt code
            # werkend zonder dat de migratie eerst gedraaid hoeft te zijn.
            try:
                db.table('warm_leads').insert(wrow).execute()
            except Exception as e:
                if 'hermes_started_by' in str(e).lower():
                    wrow.pop('hermes_started_by_id', None)
                    wrow.pop('hermes_started_by_name', None)
                    db.table('warm_leads').insert(wrow).execute()
                else:
                    raise
            db.table('prospect_list').update({'hermes_warm_lead_id': warm_id}).eq('id', prospect['id']).execute()
            # Activity log met starter-attributie zodat het overzicht klopt
            actor_label = f'Hermes (AI · gestart door {run_starter_name})' if run_starter_name else 'Hermes (AI)'
            _log_activity(run_starter_id, actor_label, 'lead_added', f'voegde {prospect.get("company_name")} toe als warm lead via Hermes 🤖')
        except Exception as e:
            print(f'[VAPI-WEBHOOK] warm_lead insert failed: {e}')

    # ── Recount the run buckets ──────────────────────────────────────────
    if prospect.get('hermes_run_id'):
        _hermes_recount_run(prospect['hermes_run_id'])

    return jsonify({'ok': True, 'outcome': outcome})


@app.route('/onboarding-dashboard')
@require_auth
def onboarding_dashboard():
    return send_from_directory('onboarding dash', 'onboardingVC.html')

@app.route('/sales')
@require_sales_auth
def sales_dashboard():
    return send_from_directory('sales dash', 'sales.html')

@app.route('/sales-apply')
def sales_apply_form():
    return send_from_directory('sales apply', 'apply.html')

@app.route('/videos/<path:filename>')
def video_files(filename):
    return send_from_directory('Viralconversions website/videos', filename)

@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory('.', filename)


# ── Main ──────────────────────────────────────────────────────────────────────

def _reset_julian_commission():
    """One-time fix: zero out stored commission amounts for Julian Verboom."""
    try:
        res = db.table('sales_members').select('id').eq('name', 'Julian Verboom').limit(1).execute()
        if res.data:
            jid = res.data[0]['id']
            db.table('warm_leads').update({'commission_amount': 0}).eq('added_by_id', jid).execute()
            print(f"[STARTUP] Reset commission_amount to 0 for Julian Verboom (id={jid})")
    except Exception as e:
        print(f"[STARTUP] Could not reset Julian commission: {e}")

threading.Thread(target=_reset_julian_commission, daemon=True).start()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print(f"\n{'='*50}\n  Viral Conversions Server\n  Running at http://localhost:{port}\n{'='*50}\n")
    app.run(host='0.0.0.0', port=port, debug=False)
