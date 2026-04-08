#!/usr/bin/env python3
"""
ChristianDaily Waitlist Agent
─────────────────────────────
Run: python3 agent.py

Commands:
  list          — Show all waitlist emails
  stats         — Show waitlist stats
  send          — Compose and send an email to all subscribers
  test          — Send a preview email to yourself
  history       — Show sent email history
  help          — Show available commands
  exit / quit   — Exit the agent
"""

import sqlite3
import os
import sys
import json
import smtplib
import textwrap
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DB_PATH = os.path.join(os.path.dirname(__file__), 'waitlist.db')

# ── Colors ────────────────────────────────────────────────────────────────────
RESET  = '\033[0m'
BOLD   = '\033[1m'
DIM    = '\033[2m'
GOLD   = '\033[38;5;178m'
GREEN  = '\033[32m'
RED    = '\033[31m'
CYAN   = '\033[36m'
WHITE  = '\033[97m'
YELLOW = '\033[33m'

def c(text, color): return f"{color}{text}{RESET}"
def header(title):
    print(f"\n{c('─'*50, DIM)}")
    print(f"  {c(title, BOLD + GOLD)}")
    print(f"{c('─'*50, DIM)}")

# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    if not os.path.exists(DB_PATH):
        print(c(f"\n⚠ Database not found at {DB_PATH}", RED))
        print(c("  Make sure server.py is running first.\n", DIM))
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ── Commands ──────────────────────────────────────────────────────────────────
def cmd_stats():
    header("Waitlist Stats")
    conn = get_db()
    total = conn.execute('SELECT COUNT(*) as c FROM waitlist').fetchone()['c']
    via_ref = conn.execute('SELECT COUNT(*) as c FROM waitlist WHERE referred_by IS NOT NULL').fetchone()['c']
    fourteen = conn.execute('SELECT COUNT(*) as c FROM waitlist WHERE trial_days=14').fetchone()['c']
    seven = conn.execute('SELECT COUNT(*) as c FROM waitlist WHERE trial_days=7').fetchone()['c']
    emails_sent = conn.execute('SELECT COUNT(*) as c FROM sent_emails').fetchone()['c']
    conn.close()

    rows = [
        ("Total subscribers",    c(str(total),       BOLD + WHITE)),
        ("Via referral link",    c(str(via_ref),      CYAN)),
        ("14-day trial (invited)", c(str(fourteen),   GREEN)),
        ("7-day trial (direct)", c(str(seven),        YELLOW)),
        ("Campaign emails sent", c(str(emails_sent),  GOLD)),
    ]
    for label, val in rows:
        print(f"  {label:<28} {val}")
    print()

def cmd_list(limit=None):
    header("Waitlist Emails")
    conn = get_db()
    query = 'SELECT email, trial_days, referred_by, created_at FROM waitlist ORDER BY created_at DESC'
    if limit:
        query += f' LIMIT {int(limit)}'
    rows = conn.execute(query).fetchall()
    conn.close()

    if not rows:
        print(c("  No subscribers yet.\n", DIM))
        return

    print(f"  {'#':<4} {'Email':<38} {'Trial':<8} {'Source':<12} {'Joined'}")
    print(f"  {c('─'*80, DIM)}")
    for i, r in enumerate(rows, 1):
        trial = c(f"{r['trial_days']}d", GREEN if r['trial_days'] == 14 else YELLOW)
        source = c("referral", CYAN) if r['referred_by'] else c("direct", DIM)
        joined = r['created_at'][:10] if r['created_at'] else '—'
        print(f"  {str(i):<4} {r['email']:<38} {trial:<18} {source:<22} {joined}")
    print(f"\n  {c(f'{len(rows)} subscriber(s) shown', DIM)}\n")

def cmd_send():
    header("Compose Email to Waitlist")

    conn = get_db()
    count = conn.execute('SELECT COUNT(*) as c FROM waitlist').fetchone()['c']
    conn.close()

    if count == 0:
        print(c("  No subscribers on the waitlist yet.\n", YELLOW))
        return

    print(c(f"  This will send to {count} subscriber(s).\n", DIM))

    # Subject
    print(c("  Subject line:", BOLD))
    subject = input("  > ").strip()
    if not subject:
        print(c("  Cancelled — empty subject.\n", RED))
        return

    # Body
    print(c(f"\n  Email body {c('(type your message, then type END on a new line):', DIM)}", BOLD))
    lines = []
    while True:
        line = input("  ")
        if line.strip().upper() == 'END':
            break
        lines.append(line)
    body = '\n'.join(lines).strip()

    if not body:
        print(c("  Cancelled — empty body.\n", RED))
        return

    # Preview
    print(f"\n{c('─'*50, DIM)}")
    print(c("  Preview:", BOLD))
    print(f"  {c('To:', DIM)} {count} subscriber(s)")
    print(f"  {c('Subject:', DIM)} {subject}")
    print(f"  {c('Body:', DIM)}")
    for line in body.split('\n'):
        print(f"    {line}")
    print(f"{c('─'*50, DIM)}\n")

    confirm = input(c("  Send this email? (yes/no): ", BOLD + GOLD)).strip().lower()
    if confirm not in ('yes', 'y'):
        print(c("  Cancelled.\n", DIM))
        return

    _send_email(subject, body, count)

def cmd_test():
    header("Send Test Email")
    test_addr = input(c("  Your email address (for preview): ", BOLD)).strip()
    if not test_addr or '@' not in test_addr:
        print(c("  Invalid email.\n", RED))
        return

    print(c("  Subject:", BOLD))
    subject = input("  > ").strip() or "Test — ChristianDaily Waitlist"

    print(c(f"\n  Body {c('(type END to finish):', DIM)}", BOLD))
    lines = []
    while True:
        line = input("  ")
        if line.strip().upper() == 'END':
            break
        lines.append(line)
    body = '\n'.join(lines).strip() or "This is a test email from ChristianDaily."

    _send_email(subject, body, 1, test_recipient=test_addr)

def _send_email(subject, body, count, test_recipient=None):
    smtp_host  = os.getenv('SMTP_HOST',  'smtp.gmail.com')
    smtp_port  = int(os.getenv('SMTP_PORT', 587))
    smtp_user  = os.getenv('SMTP_USER',  '')
    smtp_pass  = os.getenv('SMTP_PASS',  '')
    from_name  = os.getenv('FROM_NAME',  'ChristianDaily')
    from_email = os.getenv('FROM_EMAIL', smtp_user)

    if not smtp_user or not smtp_pass:
        print(c("\n  ⚠ Email credentials not configured.", RED))
        print(c("  Add SMTP_USER and SMTP_PASS to your .env file.\n", DIM))
        print(c("  Example .env:", BOLD))
        print(c("    SMTP_HOST=smtp.gmail.com", DIM))
        print(c("    SMTP_PORT=587", DIM))
        print(c("    SMTP_USER=you@gmail.com", DIM))
        print(c("    SMTP_PASS=your-app-password", DIM))
        print(c("    FROM_NAME=ChristianDaily\n", DIM))
        return

    conn = get_db()
    if test_recipient:
        recipients = [test_recipient]
    else:
        rows = conn.execute('SELECT email FROM waitlist').fetchall()
        recipients = [r['email'] for r in rows]

    sent = 0
    failed = 0

    print(c(f"\n  Sending to {len(recipients)} recipient(s)...", DIM))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)

            for i, recipient in enumerate(recipients, 1):
                try:
                    msg = MIMEMultipart('alternative')
                    msg['Subject'] = subject
                    msg['From'] = f'{from_name} <{from_email}>'
                    msg['To'] = recipient

                    text_part = MIMEText(body, 'plain')
                    html_body = _body_to_html(body, subject, from_name)
                    html_part = MIMEText(html_body, 'html')
                    msg.attach(text_part)
                    msg.attach(html_part)

                    server.sendmail(from_email, recipient, msg.as_string())
                    sent += 1
                    print(f"  {c('✓', GREEN)} {recipient}  ({i}/{len(recipients)})")
                except Exception as e:
                    failed += 1
                    print(f"  {c('✗', RED)} {recipient} — {e}")

        # Log to DB
        if not test_recipient:
            conn.execute(
                'INSERT INTO sent_emails (subject, body, recipients) VALUES (?, ?, ?)',
                (subject, body, sent)
            )
            conn.commit()

    except smtplib.SMTPAuthenticationError:
        print(c("\n  ✗ SMTP authentication failed. Check SMTP_USER/SMTP_PASS.\n", RED))
        conn.close()
        return
    except Exception as e:
        print(c(f"\n  ✗ SMTP error: {e}\n", RED))
        conn.close()
        return

    conn.close()
    print(f"\n  {c('Done!', BOLD + GREEN)} Sent: {c(str(sent), GREEN)} | Failed: {c(str(failed), RED if failed else DIM)}\n")

def _body_to_html(text, subject, brand='ChristianDaily'):
    paragraphs = ''.join(f'<p style="margin:0 0 16px;">{p}</p>' for p in text.split('\n') if p.strip())
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#FDFAF5;font-family:Georgia,serif;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 16px;">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:#FFFFFF;border-radius:16px;border:1px solid #EDE8DF;overflow:hidden;">
        <tr><td style="padding:32px 40px;background:#1A1611;text-align:center;">
          <span style="font-size:22px;font-weight:700;color:#FDFAF5;font-family:Georgia,serif;">&#10011; {brand}</span>
        </td></tr>
        <tr><td style="padding:40px 40px 32px;">
          <h2 style="margin:0 0 24px;font-size:24px;color:#1A1611;font-family:Georgia,serif;">{subject}</h2>
          <div style="font-size:16px;line-height:1.7;color:#5C5347;">{paragraphs}</div>
        </td></tr>
        <tr><td style="padding:24px 40px;border-top:1px solid #EDE8DF;text-align:center;font-size:13px;color:#9C9188;font-family:Arial,sans-serif;">
          You're receiving this because you joined the ChristianDaily waitlist.<br/>
          © 2025 ChristianDaily. All rights reserved.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""

def cmd_history():
    header("Sent Email History")
    conn = get_db()
    rows = conn.execute('SELECT subject, recipients, sent_at FROM sent_emails ORDER BY sent_at DESC').fetchall()
    conn.close()

    if not rows:
        print(c("  No emails sent yet.\n", DIM))
        return

    for r in rows:
        print(f"  {c(r['sent_at'][:16], DIM)}  {c(str(r['recipients']) + ' recipients', CYAN)}  —  {r['subject']}")
    print()

def cmd_help():
    header("Available Commands")
    cmds = [
        ("stats",        "Show waitlist stats and counts"),
        ("list",         "List all subscriber emails"),
        ("list 50",      "List the last 50 subscribers"),
        ("send",         "Compose and send email to all subscribers"),
        ("test",         "Send a test email to yourself"),
        ("history",      "Show sent email campaign history"),
        ("help",         "Show this help message"),
        ("exit / quit",  "Exit the agent"),
    ]
    for cmd, desc in cmds:
        print(f"  {c(cmd, BOLD + CYAN):<30} {c(desc, DIM)}")
    print()

# ── Main Loop ─────────────────────────────────────────────────────────────────

def main():
    os.system('clear')
    print(f"\n  {c('✟', GOLD)}  {c('ChristianDaily Waitlist Agent', BOLD + WHITE)}")
    print(f"  {c('Type help for available commands.', DIM)}\n")

    # Show stats on startup
    if os.path.exists(DB_PATH):
        cmd_stats()
    else:
        print(c("  ⚠ Database not found. Start server.py first.\n", YELLOW))

    while True:
        try:
            raw = input(c("  agent > ", BOLD + GOLD)).strip()
        except (KeyboardInterrupt, EOFError):
            print(c("\n\n  Goodbye! God bless. ✟\n", DIM))
            break

        if not raw:
            continue

        parts = raw.split()
        cmd = parts[0].lower()

        if cmd in ('exit', 'quit', 'q'):
            print(c("\n  Goodbye! God bless. ✟\n", DIM))
            break
        elif cmd == 'stats':
            cmd_stats()
        elif cmd == 'list':
            limit = parts[1] if len(parts) > 1 else None
            cmd_list(limit)
        elif cmd == 'send':
            cmd_send()
        elif cmd == 'test':
            cmd_test()
        elif cmd == 'history':
            cmd_history()
        elif cmd == 'help':
            cmd_help()
        else:
            print(c(f"  Unknown command: {cmd}. Type help to see available commands.\n", YELLOW))

if __name__ == '__main__':
    main()
