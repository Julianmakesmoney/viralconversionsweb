"""
ChristianDaily Waitlist Server
Run: python3 server.py
"""

import sqlite3
import os
import json
import string
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__, static_folder='.')

DB_PATH = os.path.join(os.path.dirname(__file__), 'waitlist.db')

# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS waitlist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT    UNIQUE NOT NULL,
            ref_code    TEXT    UNIQUE NOT NULL,
            referred_by TEXT    DEFAULT NULL,
            trial_days  INTEGER DEFAULT 7,
            created_at  TEXT    DEFAULT (datetime('now'))
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS sent_emails (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            subject    TEXT NOT NULL,
            body       TEXT NOT NULL,
            sent_at    TEXT DEFAULT (datetime('now')),
            recipients INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()
    print(f"[DB] Initialised at {DB_PATH}")

def generate_ref_code(length=8):
    chars = string.ascii_lowercase + string.digits
    return 'cd_' + ''.join(random.choices(chars, k=length))

def unique_ref_code():
    conn = get_db()
    while True:
        code = generate_ref_code()
        row = conn.execute('SELECT id FROM waitlist WHERE ref_code = ?', (code,)).fetchone()
        if not row:
            conn.close()
            return code

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
        # Check if already on list
        existing = conn.execute('SELECT * FROM waitlist WHERE email = ?', (email,)).fetchone()
        if existing:
            referral_count = conn.execute(
                'SELECT COUNT(*) as c FROM waitlist WHERE referred_by = ?', (existing['ref_code'],)
            ).fetchone()['c']
            conn.close()
            return jsonify({
                'success': False,
                'already_exists': True,
                'email': existing['email'],
                'ref_code': existing['ref_code'],
                'trial_days': existing['trial_days'],
                'referral_count': referral_count
            })

        # New signup — generate their ref code
        new_code = unique_ref_code()
        trial_days = 7
        referred_by = None

        # Handle referral — if they came via a referral link
        if ref_code_used:
            referrer = conn.execute(
                'SELECT * FROM waitlist WHERE ref_code = ?', (ref_code_used,)
            ).fetchone()
            if referrer:
                referred_by = ref_code_used
                # New user also gets 14 days for using an invite link
                trial_days = 14
                # Upgrade referrer to 14 days if not already
                if referrer['trial_days'] < 14:
                    conn.execute(
                        'UPDATE waitlist SET trial_days = 14 WHERE ref_code = ?', (ref_code_used,)
                    )

        conn.execute(
            'INSERT INTO waitlist (email, ref_code, referred_by, trial_days) VALUES (?, ?, ?, ?)',
            (email, new_code, referred_by, trial_days)
        )
        conn.commit()

        referral_count = 0
        conn.close()

        print(f"[SIGNUP] {email} | ref: {new_code} | via: {referred_by or 'direct'} | trial: {trial_days}d")

        return jsonify({
            'success': True,
            'email': email,
            'ref_code': new_code,
            'trial_days': trial_days,
            'referral_count': referral_count
        })

    except Exception as e:
        conn.close()
        print(f"[ERROR] signup: {e}")
        return jsonify({'success': False, 'error': 'Server error. Please try again.'}), 500


@app.route('/api/count', methods=['GET'])
def count():
    conn = get_db()
    row = conn.execute('SELECT COUNT(*) as c FROM waitlist').fetchone()
    conn.close()
    return jsonify({'count': row['c']})


@app.route('/api/stats', methods=['GET'])
def stats():
    conn = get_db()
    total = conn.execute('SELECT COUNT(*) as c FROM waitlist').fetchone()['c']
    via_referral = conn.execute(
        'SELECT COUNT(*) as c FROM waitlist WHERE referred_by IS NOT NULL'
    ).fetchone()['c']
    fourteen_day = conn.execute(
        'SELECT COUNT(*) as c FROM waitlist WHERE trial_days = 14'
    ).fetchone()['c']
    seven_day = conn.execute(
        'SELECT COUNT(*) as c FROM waitlist WHERE trial_days = 7'
    ).fetchone()['c']
    emails_sent = conn.execute('SELECT COUNT(*) as c FROM sent_emails').fetchone()['c']
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
    rows = conn.execute(
        'SELECT email, ref_code, trial_days, referred_by, created_at FROM waitlist ORDER BY created_at DESC'
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/history', methods=['GET'])
def email_history():
    """Returns sent email campaign history."""
    conn = get_db()
    rows = conn.execute(
        'SELECT id, subject, body, recipients, sent_at FROM sent_emails ORDER BY sent_at DESC'
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/send-email', methods=['POST'])
def send_email_api():
    """Send an email to the whole waitlist. Called from agent.py."""
    data = request.get_json(silent=True) or {}
    subject = (data.get('subject') or '').strip()
    body = (data.get('body') or '').strip()
    test_recipient = data.get('test_recipient')  # optional: send to one address for preview

    if not subject or not body:
        return jsonify({'success': False, 'error': 'Subject and body are required.'}), 400

    # Load SMTP config from env
    smtp_host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
    smtp_port = int(os.getenv('SMTP_PORT', 587))
    smtp_user = os.getenv('SMTP_USER', '')
    smtp_pass = os.getenv('SMTP_PASS', '')
    from_name = os.getenv('FROM_NAME', 'ChristianDaily')
    from_email = os.getenv('FROM_EMAIL', smtp_user)

    if not smtp_user or not smtp_pass:
        return jsonify({
            'success': False,
            'error': 'Email credentials not configured. Add SMTP_USER and SMTP_PASS to your .env file.'
        }), 500

    # Fetch recipients
    conn = get_db()
    if test_recipient:
        recipients = [test_recipient]
    else:
        rows = conn.execute('SELECT email FROM waitlist').fetchall()
        recipients = [r['email'] for r in rows]

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

                    # Plain text fallback
                    text_part = MIMEText(body, 'plain')
                    # HTML version
                    html_body = body_to_html(body, subject, from_name)
                    html_part = MIMEText(html_body, 'html')

                    msg.attach(text_part)
                    msg.attach(html_part)

                    server.sendmail(from_email, recipient, msg.as_string())
                    sent += 1
                except Exception as e:
                    failed += 1
                    print(f"[MAIL] Failed for {recipient}: {e}")

        # Log to DB
        if not test_recipient:
            conn.execute(
                'INSERT INTO sent_emails (subject, body, recipients) VALUES (?, ?, ?)',
                (subject, body, sent)
            )
            conn.commit()

    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': f'SMTP error: {str(e)}'}), 500

    conn.close()
    print(f"[MAIL] Sent: {sent} | Failed: {failed} | Subject: {subject}")
    return jsonify({'success': True, 'sent': sent, 'failed': failed})


def body_to_html(text, subject, brand='ChristianDaily'):
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
            You're receiving this because you joined the ChristianDaily waitlist.<br/>
            © 2025 ChristianDaily. All rights reserved.
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ── Static file serving ───────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/admin')
def admin():
    return send_from_directory('.', 'dashboard.html')

@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory('.', filename)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    port = int(os.getenv('PORT', 5000))
    print(f"\n{'='*50}")
    print(f"  ChristianDaily Waitlist Server")
    print(f"  Running at http://localhost:{port}")
    print(f"  Database: {DB_PATH}")
    print(f"{'='*50}\n")
    app.run(host='0.0.0.0', port=port, debug=False)
