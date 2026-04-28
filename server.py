"""
Viral Conversions Server
Run: python3 server.py  |  or:  PORT=8080 python3 server.py
"""

import os
import json
import string
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify, send_from_directory, make_response, redirect
from datetime import datetime
import secrets
from supabase import create_client

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__, static_folder='.')

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
        print(f"[ONBOARDING] {data.get('naam','?')} | {data.get('email','?')}")
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

def _get_effective_rate(member):
    """Returns the effective commission rate (0–1) for a member dict."""
    override = member.get('commission_override')
    if override is not None:
        return float(override) / 100.0
    contract_type = member.get('contract_type') or 'legacy'
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
    res = db.table('sales_members').select('id,name,email,phone,ref_code,bonus_owed,first_sale_counted,contract_type,commission_override').eq('id', mid).limit(1).execute()
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
    members_res  = db.table('sales_members').select('id,name').eq('status', 'active').execute()
    prospect_res = db.table('prospect_list').select('called_by_id,called_by_name').eq('called', True).execute()
    totals = {}
    for r in closed_res.data:
        mid  = r['added_by_id']
        name = r['added_by_name'] or 'Onbekend'
        totals.setdefault(mid, {'name': name, 'revenue': 0, 'commission': 0, 'closes': 0, 'called_leads': 0})
        totals[mid]['revenue']    += float(r['closed_amount'] or 0)
        totals[mid]['commission'] += float(r['commission_amount'] or 0)
        totals[mid]['closes']     += 1
    for m in members_res.data:
        totals.setdefault(m['id'], {'name': m['name'], 'revenue': 0, 'commission': 0, 'closes': 0, 'called_leads': 0})
    for r in prospect_res.data:
        mid  = r.get('called_by_id')
        name = r.get('called_by_name') or 'Onbekend'
        if mid:
            totals.setdefault(mid, {'name': name, 'revenue': 0, 'commission': 0, 'closes': 0, 'called_leads': 0})
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
    return jsonify(res.data)

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
    db.table('warm_leads').insert({
        'id': lid, 'company_name': company_name, 'phone': phone,
        'maps_url': maps_url, 'added_by_id': added_by_id,
        'added_by_name': added_by_name, 'status': 'warm',
        'pipeline_status': 'nieuw', 'closed_amount': None, 'closed_at': None,
    }).execute()
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
    member_for_rate = db.table('sales_members').select('id,contract_type,commission_override').eq('id', lead['added_by_id']).limit(1).execute()
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

@app.route('/api/sales/leads/<lid>/pipeline', methods=['PUT'])
@require_sales_auth
def update_lead_pipeline(lid):
    data   = request.get_json(silent=True) or {}
    status = data.get('pipeline_status')
    valid  = ('nieuw','gebeld','geinteresseerd','laterterugbellen','afspraak','offerte','afgewezen')
    if status not in valid:
        return jsonify({'success': False, 'error': 'Ongeldige status.'}), 400
    db.table('warm_leads').update({'pipeline_status': status}).eq('id', lid).execute()
    return jsonify({'success': True})

@app.route('/api/sales/leads/<lid>/notes', methods=['PUT'])
@require_sales_auth
def update_lead_notes(lid):
    data  = request.get_json(silent=True) or {}
    notes = data.get('notes', '')
    db.table('warm_leads').update({'notes': notes}).eq('id', lid).execute()
    return jsonify({'success': True})

@app.route('/api/sales/leads/<lid>/followup', methods=['PUT'])
@require_sales_auth
def update_lead_followup(lid):
    data = request.get_json(silent=True) or {}
    date = data.get('followup_date')
    db.table('warm_leads').update({'followup_date': date}).eq('id', lid).execute()
    return jsonify({'success': True})

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

@app.route('/api/prospects', methods=['GET'])
@require_sales_auth
def list_prospects():
    try:
        res = db.table('prospect_list').select('*').order('called').order('created_at').execute()
        return jsonify(res.data)
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

        # Build sets of phones and names that already exist in warm_leads or prospect_list
        def norm_phone(p):
            return ''.join(c for c in str(p or '') if c.isdigit())
        def norm_name(n):
            return str(n or '').strip().lower()

        warm_res     = db.table('warm_leads').select('phone,company_name').execute()
        prospect_res = db.table('prospect_list').select('phone,company_name').execute()
        blocked_phones = set()
        blocked_names  = set()
        for r in warm_res.data + prospect_res.data:
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
            records.append({
                'id': f"{batch_id}_{i}",
                'company_name': name, 'phone': phone,
                'rating': rating,
                'maps_url': str(r.get('maps_url') or '').strip(),
                'city': str(r.get('city') or '').strip(),
                'niche': str(r.get('niche') or '').strip(),
                'called': False,
                'import_batch': batch_id,
                'created_at': now,
            })
        if not records:
            return jsonify({'success': False, 'error': f'Geen nieuwe prospects — alle {skipped} rijen staan al in de bel lijst of warm leads.'}), 400
        db.table('prospect_list').insert(records).execute()
        print(f"[PROSPECTS] Imported {len(records)} rows, skipped {skipped} duplicates (batch {batch_id})")
        return jsonify({'success': True, 'count': len(records), 'skipped': skipped, 'batch_id': batch_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/prospects/<pid>/call', methods=['PUT'])
@require_sales_auth
def mark_prospect_called(pid):
    try:
        mid = _get_sales_member_id()
        res = db.table('sales_members').select('name').eq('id', mid).limit(1).execute()
        member_name = res.data[0]['name'] if res.data else 'Onbekend'
        db.table('prospect_list').update({
            'called': True,
            'called_by_id': str(mid),
            'called_by_name': member_name,
            'called_at': datetime.utcnow().isoformat(),
        }).eq('id', pid).execute()
        return jsonify({'success': True, 'called_by_name': member_name})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/prospects/<pid>/uncall', methods=['PUT'])
@require_sales_auth
def unmark_prospect_called(pid):
    try:
        db.table('prospect_list').update({
            'called': False, 'called_by_id': None,
            'called_by_name': None, 'called_at': None,
        }).eq('id', pid).execute()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

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
        res = db.table('prospect_list').select('*').order('created_at', desc=True).execute()
        return jsonify(res.data or [])
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
    if 'contract_type' in data and data['contract_type'] in ('legacy', 'new'):
        update['contract_type'] = data['contract_type']
    if 'commission_override' in data:
        val = data['commission_override']
        update['commission_override'] = float(val) if val not in (None, '') else None
    if update:
        db.table('sales_members').update(update).eq('id', mid).execute()
    return jsonify({'success': True})

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

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print(f"\n{'='*50}\n  Viral Conversions Server\n  Running at http://localhost:{port}\n{'='*50}\n")
    app.run(host='0.0.0.0', port=port, debug=False)
