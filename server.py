"""
Viral Conversions Server
Run: python3 server.py  |  or:  PORT=8080 python3 server.py
"""

import psycopg2
import psycopg2.extras
import os
import json
import string
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify, send_from_directory, make_response, redirect
from flask.json.provider import DefaultJSONProvider
from datetime import datetime, date as date_type
import hashlib
import secrets

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class CustomJSONProvider(DefaultJSONProvider):
    def default(self, obj):
        if isinstance(obj, (datetime, date_type)):
            return obj.isoformat()
        return super().default(obj)


app = Flask(__name__, static_folder='.')
app.json_provider_class = CustomJSONProvider
app.json = CustomJSONProvider(app)

DATABASE_URL = os.getenv('DATABASE_URL')

# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    return psycopg2.connect(DATABASE_URL)

def dict_cur(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS waitlist (
            id          SERIAL PRIMARY KEY,
            email       TEXT    UNIQUE NOT NULL,
            ref_code    TEXT    UNIQUE NOT NULL,
            referred_by TEXT    DEFAULT NULL,
            trial_days  INTEGER DEFAULT 7,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS sent_emails (
            id         SERIAL PRIMARY KEY,
            subject    TEXT NOT NULL,
            body       TEXT NOT NULL,
            sent_at    TIMESTAMPTZ DEFAULT NOW(),
            recipients INTEGER DEFAULT 0
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            email      TEXT NOT NULL,
            phone      TEXT DEFAULT '',
            date       TEXT NOT NULL,
            time       TEXT NOT NULL,
            notes      TEXT DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS onboarding (
            id           TEXT PRIMARY KEY,
            data         TEXT NOT NULL,
            submitted_at TIMESTAMPTZ DEFAULT NOW()
        )
    ''')
    conn.commit()
    conn.close()
    print("[DB] Initialised (PostgreSQL/Supabase)")

def generate_ref_code(length=8):
    chars = string.ascii_lowercase + string.digits
    return 'cd_' + ''.join(random.choices(chars, k=length))

def unique_ref_code():
    conn = get_db()
    try:
        cur = conn.cursor()
        while True:
            code = generate_ref_code()
            cur.execute('SELECT id FROM waitlist WHERE ref_code = %s', (code,))
            if not cur.fetchone():
                return code
    finally:
        conn.close()

# Ensure tables exist at import time (works under gunicorn, not just __main__)
init_db()

# ── API Routes ────────────────────────────────────────────────────────────────

@app.route('/api/signup', methods=['POST'])
def signup():
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    ref_code_used = (data.get('ref_code') or '').strip()

    if not email or '@' not in email or '.' not in email:
        return jsonify({'success': False, 'error': 'Invalid email address.'}), 400

    conn = get_db()
    try:
        cur = dict_cur(conn)
        cur.execute('SELECT * FROM waitlist WHERE email = %s', (email,))
        existing = cur.fetchone()
        if existing:
            cur.execute('SELECT COUNT(*) as c FROM waitlist WHERE referred_by = %s', (existing['ref_code'],))
            referral_count = cur.fetchone()['c']
            conn.close()
            return jsonify({
                'success': False,
                'already_exists': True,
                'email': existing['email'],
                'ref_code': existing['ref_code'],
                'trial_days': existing['trial_days'],
                'referral_count': referral_count
            })

        new_code = unique_ref_code()
        trial_days = 7
        referred_by = None

        if ref_code_used:
            cur.execute('SELECT * FROM waitlist WHERE ref_code = %s', (ref_code_used,))
            referrer = cur.fetchone()
            if referrer:
                referred_by = ref_code_used
                trial_days = 14
                if referrer['trial_days'] < 14:
                    cur.execute('UPDATE waitlist SET trial_days = 14 WHERE ref_code = %s', (ref_code_used,))

        cur.execute(
            'INSERT INTO waitlist (email, ref_code, referred_by, trial_days) VALUES (%s, %s, %s, %s)',
            (email, new_code, referred_by, trial_days)
        )
        conn.commit()
        conn.close()

        print(f"[SIGNUP] {email} | ref: {new_code} | via: {referred_by or 'direct'} | trial: {trial_days}d")

        return jsonify({
            'success': True,
            'email': email,
            'ref_code': new_code,
            'trial_days': trial_days,
            'referral_count': 0
        })

    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"[ERROR] signup: {e}")
        return jsonify({'success': False, 'error': 'Server error. Please try again.'}), 500


@app.route('/api/count', methods=['GET'])
def count():
    conn = get_db()
    cur = dict_cur(conn)
    cur.execute('SELECT COUNT(*) as c FROM waitlist')
    row = cur.fetchone()
    conn.close()
    return jsonify({'count': row['c']})


@app.route('/api/stats', methods=['GET'])
def stats():
    conn = get_db()
    cur = dict_cur(conn)
    cur.execute('SELECT COUNT(*) as c FROM waitlist')
    total = cur.fetchone()['c']
    cur.execute('SELECT COUNT(*) as c FROM waitlist WHERE referred_by IS NOT NULL')
    via_referral = cur.fetchone()['c']
    cur.execute('SELECT COUNT(*) as c FROM waitlist WHERE trial_days = 14')
    fourteen_day = cur.fetchone()['c']
    cur.execute('SELECT COUNT(*) as c FROM waitlist WHERE trial_days = 7')
    seven_day = cur.fetchone()['c']
    cur.execute('SELECT COUNT(*) as c FROM sent_emails')
    emails_sent = cur.fetchone()['c']
    conn.close()
    return jsonify({
        'total': total,
        'via_referral': via_referral,
        'fourteen_day_trial': fourteen_day,
        'seven_day_trial': seven_day,
        'emails_sent': emails_sent
    })


@app.route('/api/emails', methods=['GET'])
def list_emails():
    """Returns all waitlist emails (used by the agent and dashboard)."""
    conn = get_db()
    cur = dict_cur(conn)
    cur.execute('SELECT email, ref_code, trial_days, referred_by, created_at FROM waitlist ORDER BY created_at DESC')
    rows = cur.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/history', methods=['GET'])
def email_history():
    """Returns sent email campaign history."""
    conn = get_db()
    cur = dict_cur(conn)
    cur.execute('SELECT id, subject, body, recipients, sent_at FROM sent_emails ORDER BY sent_at DESC')
    rows = cur.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/send-email', methods=['POST'])
def send_email_api():
    """Send an email to the whole waitlist. Called from agent.py."""
    data = request.get_json(silent=True) or {}
    subject = (data.get('subject') or '').strip()
    body = (data.get('body') or '').strip()
    test_recipient = data.get('test_recipient')

    if not subject or not body:
        return jsonify({'success': False, 'error': 'Subject and body are required.'}), 400

    smtp_host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
    smtp_port = int(os.getenv('SMTP_PORT', 587))
    smtp_user = os.getenv('SMTP_USER', '')
    smtp_pass = os.getenv('SMTP_PASS', '')
    from_name = os.getenv('FROM_NAME', 'Viral Conversions')
    from_email = os.getenv('FROM_EMAIL', smtp_user)

    if not smtp_user or not smtp_pass:
        return jsonify({
            'success': False,
            'error': 'Email credentials not configured. Add SMTP_USER and SMTP_PASS to your .env file.'
        }), 500

    conn = get_db()
    cur = dict_cur(conn)
    if test_recipient:
        recipients = [test_recipient]
    else:
        cur.execute('SELECT email FROM waitlist')
        recipients = [r['email'] for r in cur.fetchall()]

    if not recipients:
        conn.close()
        return jsonify({'success': False, 'error': 'No recipients on waitlist.'}), 400

    sent = 0
    failed = 0

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)

            for recipient in recipients:
                try:
                    msg = MIMEMultipart('alternative')
                    msg['Subject'] = subject
                    msg['From'] = f'{from_name} <{from_email}>'
                    msg['To'] = recipient

                    text_part = MIMEText(body, 'plain')
                    html_body = body_to_html(body, subject, from_name)
                    html_part = MIMEText(html_body, 'html')

                    msg.attach(text_part)
                    msg.attach(html_part)

                    server.sendmail(from_email, recipient, msg.as_string())
                    sent += 1
                except Exception as e:
                    failed += 1
                    print(f"[MAIL] Failed for {recipient}: {e}")

        if not test_recipient:
            cur.execute(
                'INSERT INTO sent_emails (subject, body, recipients) VALUES (%s, %s, %s)',
                (subject, body, sent)
            )
            conn.commit()

    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'error': f'SMTP error: {str(e)}'}), 500

    conn.close()
    print(f"[MAIL] Sent: {sent} | Failed: {failed} | Subject: {subject}")
    return jsonify({'success': True, 'sent': sent, 'failed': failed})


def body_to_html(text, subject, brand='Viral Conversions'):
    """Wraps plain text in a branded HTML email template."""
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
            <div style="font-size:16px;line-height:1.7;color:#5C5347;">
              {paragraphs}
            </div>
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
    data = request.get_json(silent=True) or {}
    bid   = str(data.get('id', '')) or str(int(datetime.utcnow().timestamp() * 1000))
    name  = (data.get('name')  or '').strip()
    email = (data.get('email') or '').strip()
    phone = (data.get('phone') or '').strip()
    date  = (data.get('date')  or '').strip()
    time  = (data.get('time')  or '').strip()
    notes = (data.get('notes') or '').strip()

    if not all([name, email, date, time]):
        return jsonify({'success': False, 'error': 'Verplichte velden ontbreken.'}), 400

    conn = get_db()
    try:
        cur = dict_cur(conn)
        cur.execute('SELECT id FROM bookings WHERE date = %s AND time = %s', (date, time))
        if cur.fetchone():
            conn.close()
            return jsonify({'success': False, 'error': 'Dit tijdslot is al geboekt.'}), 409

        cur.execute(
            'INSERT INTO bookings (id, name, email, phone, date, time, notes) VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (bid, name, email, phone, date, time, notes)
        )
        conn.commit()
        conn.close()
        print(f"[BOOKING] {name} | {date} {time} | {email}")
        return jsonify({'success': True, 'id': bid})
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"[ERROR] booking: {e}")
        return jsonify({'success': False, 'error': 'Server error.'}), 500


@app.route('/api/bookings', methods=['GET'])
def list_bookings():
    today = datetime.utcnow().strftime('%Y-%m-%d')
    conn = get_db()
    cur = dict_cur(conn)
    cur.execute('DELETE FROM bookings WHERE date < %s', (today,))
    conn.commit()
    cur.execute('SELECT * FROM bookings ORDER BY date ASC, time ASC')
    rows = cur.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/booking/<bid>', methods=['DELETE'])
def delete_booking(bid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('DELETE FROM bookings WHERE id = %s', (bid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/booked-slots', methods=['GET'])
def booked_slots():
    """Returns {date: [time, ...]} map — used by the website calendar."""
    conn = get_db()
    cur = dict_cur(conn)
    cur.execute('SELECT date, time FROM bookings')
    rows = cur.fetchall()
    conn.close()
    result = {}
    for r in rows:
        result.setdefault(r['date'], []).append(r['time'])
    return jsonify(result)


@app.route('/api/availability', methods=['GET'])
def get_availability():
    conn = get_db()
    cur = dict_cur(conn)
    cur.execute("SELECT value FROM settings WHERE key = 'availability'")
    row = cur.fetchone()
    conn.close()
    if row:
        return jsonify(json.loads(row['value']))
    return jsonify(DEFAULT_AVAILABILITY)


@app.route('/api/availability', methods=['PUT'])
def set_availability():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'success': False, 'error': 'No data.'}), 400
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings (key, value) VALUES ('availability', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (json.dumps(data),)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# ── Onboarding API ───────────────────────────────────────────────────────────

@app.route('/api/onboarding', methods=['GET'])
def list_onboarding():
    conn = get_db()
    cur = dict_cur(conn)
    cur.execute('SELECT id, data, submitted_at FROM onboarding ORDER BY submitted_at DESC')
    rows = cur.fetchall()
    conn.close()
    result = []
    for r in rows:
        client = json.loads(r['data'])
        client['id'] = r['id']
        client['submittedAt'] = r['submitted_at'].isoformat() if r['submitted_at'] else None
        result.append(client)
    return jsonify(result)

@app.route('/api/onboarding', methods=['POST'])
def create_onboarding():
    data = request.get_json(silent=True) or {}
    cid = data.get('id') or str(int(datetime.utcnow().timestamp() * 1000))
    data['id'] = cid
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute('INSERT INTO onboarding (id, data) VALUES (%s, %s)', (cid, json.dumps(data)))
        conn.commit()
        conn.close()
        print(f"[ONBOARDING] {data.get('naam','?')} | {data.get('email','?')}")
        return jsonify({'success': True, 'id': cid})
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"[ERROR] onboarding: {e}")
        return jsonify({'success': False, 'error': 'Server error.'}), 500

@app.route('/api/onboarding/<cid>', methods=['PUT'])
def update_onboarding(cid):
    data = request.get_json(silent=True) or {}
    conn = get_db()
    cur = dict_cur(conn)
    cur.execute('SELECT data FROM onboarding WHERE id = %s', (cid,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'Not found.'}), 404
    client = json.loads(row['data'])
    client.update(data)
    cur.execute('UPDATE onboarding SET data = %s WHERE id = %s', (json.dumps(client), cid))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/onboarding/<cid>', methods=['DELETE'])
def delete_onboarding(cid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('DELETE FROM onboarding WHERE id = %s', (cid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# ── Auth ─────────────────────────────────────────────────────────────────────

ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'viralconversions2024')
AUTH_COOKIE    = 'vc_admin_token'
_valid_tokens  = set()

def _check_auth():
    token = request.cookies.get(AUTH_COOKIE, '')
    return token in _valid_tokens

LOGIN_HTML = '''<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Login — Viral Conversions</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: "Plus Jakarta Sans", -apple-system, sans-serif;
      background: #07090F;
      color: #fff;
      min-height: 100dvh;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      -webkit-font-smoothing: antialiased;
    }

    /* Animated background blobs */
    .bg {
      position: fixed; inset: 0; z-index: 0; pointer-events: none;
      background: #07090F;
    }
    .blob {
      position: absolute; border-radius: 50%; filter: blur(80px);
      animation: drift 12s ease-in-out infinite alternate;
    }
    .blob-1 { width: 600px; height: 600px; top: -200px; left: -150px; background: rgba(29,78,216,0.28); animation-delay: 0s; }
    .blob-2 { width: 400px; height: 400px; bottom: -100px; right: -100px; background: rgba(37,99,235,0.18); animation-delay: -4s; }
    .blob-3 { width: 300px; height: 300px; top: 40%; left: 50%; background: rgba(59,130,246,0.10); animation-delay: -8s; }
    @keyframes drift {
      from { transform: translate(0, 0) scale(1); }
      to   { transform: translate(40px, 30px) scale(1.08); }
    }
    .dots {
      position: absolute; inset: 0; opacity: 0.07;
      background-image: radial-gradient(circle at 1px 1px, rgba(255,255,255,0.55) 1px, transparent 0);
      background-size: 22px 22px;
    }

    /* Card */
    .card {
      position: relative; z-index: 1;
      background: rgba(255,255,255,0.055);
      backdrop-filter: blur(40px) saturate(180%);
      -webkit-backdrop-filter: blur(40px) saturate(180%);
      border: 1px solid rgba(255,255,255,0.09);
      box-shadow: 0 2px 60px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.12);
      border-radius: 28px;
      padding: 48px 40px 40px;
      width: calc(100% - 32px);
      max-width: 400px;
      animation: cardIn 0.6s cubic-bezier(0.16,1,0.3,1) both;
    }
    @keyframes cardIn {
      from { opacity: 0; transform: translateY(24px) scale(0.96); }
      to   { opacity: 1; transform: translateY(0) scale(1); }
    }

    /* Logo */
    .logo-wrap {
      display: flex; align-items: center; justify-content: center; gap: 10px;
      margin-bottom: 32px;
    }
    .logo-img { width: 32px; height: 32px; filter: brightness(0) invert(1); }
    .logo-name { font-size: 15px; font-weight: 800; letter-spacing: -0.02em; }

    /* Headings */
    h1 { font-size: 22px; font-weight: 900; letter-spacing: -0.025em; margin-bottom: 8px; text-align: center; }
    .sub { font-size: 13px; color: rgba(255,255,255,0.45); text-align: center; margin-bottom: 32px; }

    /* Input */
    .input-wrap { position: relative; margin-bottom: 14px; }
    .input-wrap svg { position: absolute; left: 16px; top: 50%; transform: translateY(-50%); opacity: 0.35; pointer-events: none; }
    input[type=password] {
      width: 100%;
      background: rgba(255,255,255,0.06);
      border: 1px solid rgba(255,255,255,0.09);
      border-radius: 14px;
      padding: 14px 16px 14px 44px;
      color: #fff;
      font-size: 15px;
      font-family: inherit;
      font-weight: 500;
      outline: none;
      transition: border-color 0.2s, background 0.2s;
      letter-spacing: 0.1em;
    }
    input[type=password]::placeholder { letter-spacing: 0; color: rgba(255,255,255,0.3); }
    input[type=password]:focus { border-color: rgba(37,99,235,0.6); background: rgba(255,255,255,0.09); }

    /* Button */
    button[type=submit] {
      width: 100%; background: #fff; color: #06040F;
      border: none; border-radius: 100px;
      padding: 15px; font-size: 14px; font-weight: 800;
      cursor: pointer; font-family: inherit;
      transition: transform 0.2s cubic-bezier(0.16,1,0.3,1), filter 0.2s;
      letter-spacing: -0.01em;
    }
    button[type=submit]:hover  { transform: scale(1.02); filter: brightness(1.04); }
    button[type=submit]:active { transform: scale(0.98); }

    /* Error */
    .error-msg {
      background: rgba(255,107,107,0.1);
      border: 1px solid rgba(255,107,107,0.25);
      border-radius: 10px;
      color: #FF8080;
      font-size: 13px; font-weight: 600;
      padding: 10px 14px;
      margin-bottom: 16px;
      text-align: center;
      animation: shake 0.4s cubic-bezier(0.16,1,0.3,1);
    }
    @keyframes shake {
      0%,100%{ transform: translateX(0); }
      25%    { transform: translateX(-6px); }
      75%    { transform: translateX(6px); }
    }
  </style>
</head>
<body>
  <div class="bg">
    <div class="blob blob-1"></div>
    <div class="blob blob-2"></div>
    <div class="blob blob-3"></div>
    <div class="dots"></div>
  </div>

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
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
        </svg>
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
    html = LOGIN_HTML.replace('{error}', '').replace('{next}', next_url)
    return html

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
            return redirect(f'/login?next={request.path}')
        return f(*args, **kwargs)
    return decorated


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

@app.route('/videos/<path:filename>')
def video_files(filename):
    return send_from_directory('Viralconversions website/videos', filename)

@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory('.', filename)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print(f"\n{'='*50}")
    print(f"  Viral Conversions Server")
    print(f"  Running at http://localhost:{port}")
    print(f"{'='*50}\n")
    app.run(host='0.0.0.0', port=port, debug=False)
