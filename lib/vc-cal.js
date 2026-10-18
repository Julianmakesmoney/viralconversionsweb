// Agenda voor de website, volledig op Cloudflare: Julians Google Agenda is de bron van waarheid.
// - vrije tijden = werktijden min alles wat bezet is in Google (boekingen van de site zijn zelf ook afspraken in Google)
// - boeken = afspraak in Google met Meet-link (Google stuurt de lead een uitnodiging)
// - mails (bevestiging aan de lead, briefing aan Julian) via Gmail, vanaf Julians eigen adres
// Env (Cloudflare Pages → Settings → Environment variables):
//   GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN (tools/google_auth.py), optioneel GOOGLE_CALENDAR_ID,
//   FROM_EMAIL (afzender, standaard julian@viralconversions.io), NOTIFY_EMAIL (briefing, standaard FROM_EMAIL),
//   AVAILABILITY (JSON per weekdag, zie DEFAULT_AVAIL), MOCK_GOOGLE=1 alleen voor lokaal testen.

const TZ = 'Europe/Amsterdam';
export const DEFAULT_AVAIL = {
  1: { enabled: true, start: '09:00', end: '17:00' }, 2: { enabled: true, start: '09:00', end: '17:00' },
  3: { enabled: true, start: '09:00', end: '17:00' }, 4: { enabled: true, start: '09:00', end: '17:00' },
  5: { enabled: true, start: '09:00', end: '17:00' }, 6: { enabled: false }, 0: { enabled: false },
};
export const DAYS_AHEAD = 60, LEAD_MS = 2 * 3600e3;

export const json = (data, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } });
export const isOn = env => !!(env.GOOGLE_CLIENT_ID && env.GOOGLE_CLIENT_SECRET && env.GOOGLE_REFRESH_TOKEN);
export function availability(env) { try { if (env.AVAILABILITY) return JSON.parse(env.AVAILABILITY); } catch (e) {} return DEFAULT_AVAIL; }
const calId = env => (env.GOOGLE_CALENDAR_ID || 'primary').trim() || 'primary';
const fromAddr = env => (env.FROM_EMAIL || 'julian@viralconversions.io').trim();

// ── tijd in Nederland ──
const FMT = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
export function nl(d) {   // Date → { date: 'YYYY-MM-DD', time: 'HH:MM' } in Nederlandse tijd
  const p = Object.fromEntries(FMT.formatToParts(d).map(x => [x.type, x.value]));
  return { date: `${p.year}-${p.month}-${p.day}`, time: `${p.hour === '24' ? '00' : p.hour}:${p.minute}` };
}
export function fromNL(date, time) {   // Nederlandse datum + tijd → Date (houdt rekening met zomer-/wintertijd)
  const guess = new Date(`${date}T${time}:00Z`), p = nl(guess);
  const off = Date.parse(`${p.date}T${p.time}:00Z`) - guess.getTime();
  return new Date(guess.getTime() - off);
}
export function quarters(a, b) {   // alle kwartieren die het blok a–b raken, als [datum, 'HH:MM'] in NL-tijd
  const out = [], Q = 15 * 60e3; let t = Math.floor(a.getTime() / Q) * Q;   // NL wijkt hele uren af van UTC: kwartiergrenzen vallen samen
  for (; t < b.getTime(); t += Q) { const p = nl(new Date(t)); out.push([p.date, p.time]); }
  return out;
}

// ── Google ──
let TOK = { t: null, exp: 0 };
async function gfetch(env, url, init = {}) {
  if (env.MOCK_GOOGLE) return mock(url, init);
  const r = await fetch(url, init);
  if (!r.ok) throw new Error(`Google ${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.json();
}
async function token(env) {
  if (TOK.t && TOK.exp > Date.now() + 60e3) return TOK.t;
  const d = await gfetch(env, 'https://oauth2.googleapis.com/token', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ client_id: env.GOOGLE_CLIENT_ID, client_secret: env.GOOGLE_CLIENT_SECRET, refresh_token: env.GOOGLE_REFRESH_TOKEN, grant_type: 'refresh_token' }) });
  TOK = { t: d.access_token, exp: Date.now() + (d.expires_in || 3600) * 1000 };
  return TOK.t;
}
const auth = async env => ({ Authorization: 'Bearer ' + await token(env), 'Content-Type': 'application/json' });

export async function busyQuarters(env) {   // { 'YYYY-MM-DD': ['HH:MM', ...] } bezet in Julians agenda
  if (!isOn(env)) return {};
  const now = new Date(), until = new Date(now.getTime() + (DAYS_AHEAD + 1) * 864e5);
  const d = await gfetch(env, 'https://www.googleapis.com/calendar/v3/freeBusy', { method: 'POST', headers: await auth(env),
    body: JSON.stringify({ timeMin: now.toISOString(), timeMax: until.toISOString(), timeZone: TZ, items: [{ id: calId(env) }] }) });
  const cal = (d.calendars || {})[calId(env)] || {};
  if (cal.errors && cal.errors.length) throw new Error('freeBusy: ' + JSON.stringify(cal.errors));
  const busy = {};
  for (const b of cal.busy || []) for (const [day, q] of quarters(new Date(b.start), new Date(b.end))) (busy[day] = busy[day] || new Set()).add(q);
  return Object.fromEntries(Object.entries(busy).map(([k, v]) => [k, [...v].sort()]));
}

export async function createEvent(env, o) {   // → { id, meet }
  const a = fromNL(o.date, o.time), b = new Date(a.getTime() + o.minutes * 60e3);
  const ev = await gfetch(env, `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calId(env))}/events?conferenceDataVersion=1&sendUpdates=all`, {
    method: 'POST', headers: await auth(env), body: JSON.stringify({
      summary: o.title, description: (o.notes || '').slice(0, 7000),
      start: { dateTime: a.toISOString(), timeZone: TZ }, end: { dateTime: b.toISOString(), timeZone: TZ },
      attendees: [{ email: o.email, displayName: o.name }],
      conferenceData: { createRequest: { requestId: 'vc-' + o.bid, conferenceSolutionKey: { type: 'hangoutsMeet' } } },
      extendedProperties: { private: { vcBid: String(o.bid), vcKind: o.kind } },
      reminders: { useDefault: true } }) });
  return { id: ev.id, meet: ev.hangoutLink || '' };
}
export async function findEvent(env, bid) {
  const q = new URLSearchParams({ privateExtendedProperty: 'vcBid=' + bid, maxResults: '1', showDeleted: 'false' });
  const d = await gfetch(env, `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calId(env))}/events?` + q, { headers: await auth(env) });
  return (d.items || [])[0] || null;
}
export async function patchEvent(env, id, body) {
  return gfetch(env, `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calId(env))}/events/${encodeURIComponent(id)}?sendUpdates=none`,
    { method: 'PATCH', headers: await auth(env), body: JSON.stringify(body) });
}

// ── mail via Gmail (vanaf Julians eigen adres) ──
const b64 = s => { const u = new TextEncoder().encode(s); let bin = ''; for (let i = 0; i < u.length; i += 0x8000) bin += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000)); return btoa(bin); };
const wrap = s => s.replace(/.{1,76}/g, '$&\r\n');
export async function sendMail(env, { to, subject, text, html, replyTo }) {
  const bd = 'vc' + Math.random().toString(36).slice(2);
  const raw = [`From: Julian | Viral Conversions <${fromAddr(env)}>`, `To: ${to}`, replyTo ? `Reply-To: ${replyTo}` : '',
    `Subject: =?UTF-8?B?${b64(subject)}?=`, 'MIME-Version: 1.0', `Content-Type: multipart/alternative; boundary="${bd}"`, '',
    `--${bd}`, 'Content-Type: text/plain; charset=UTF-8', 'Content-Transfer-Encoding: base64', '', wrap(b64(text)),
    ...(html ? [`--${bd}`, 'Content-Type: text/html; charset=UTF-8', 'Content-Transfer-Encoding: base64', '', wrap(b64(html))] : []),
    `--${bd}--`, ''].filter((l, i) => l !== '' || i > 3).join('\r\n');
  const rawUrl = b64(raw).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return gfetch(env, 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send', { method: 'POST', headers: await auth(env), body: JSON.stringify({ raw: rawUrl }) });
}

// ── teksten ──
const NL_DAYS = ['zondag', 'maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag'];
const NL_MONTHS = ['januari', 'februari', 'maart', 'april', 'mei', 'juni', 'juli', 'augustus', 'september', 'oktober', 'november', 'december'];
export function whenText(date, time, en) {
  const d = new Date(date + 'T12:00:00Z');
  if (en) return new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' }).format(d) + ' at ' + time + ' (CET)';
  return `${NL_DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${NL_MONTHS[d.getUTCMonth()]} om ${time}`;
}
const esc = s => String(s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// één-klik 'zet in je agenda'-links (zelfde in de mail en op de site)
export function calLinks({ start, end, title, meet, base = 'https://viralconversions.io' }) {
  const z = d => new Date(d).toISOString().replace(/[-:]/g, '').replace(/\.\d+/, '');
  const details = meet ? 'Videolink: ' + meet : 'Online videogesprek met Julian van Viral Conversions.';
  const g = 'https://calendar.google.com/calendar/render?' + new URLSearchParams({ action: 'TEMPLATE', text: title, dates: z(start) + '/' + z(end), details, location: meet || '' });
  const o = 'https://outlook.office.com/calendar/0/deeplink/compose?' + new URLSearchParams({ path: '/calendar/action/compose', rru: 'addevent', subject: title,
    startdt: new Date(start).toISOString(), enddt: new Date(end).toISOString(), body: details, location: meet || '' });
  const a = base + '/cal/ics?' + new URLSearchParams({ s: new Date(start).toISOString(), e: new Date(end).toISOString(), t: title, m: meet || '' });
  return { google: g, outlook: o, apple: a };
}

export function confirmMail({ first, when, mins, meet, kind, en, estimate, start, end, title }) {
  const call = kind === 'call', scan = kind === 'scan';
  const subject = en ? (scan ? 'Your profit scan call is booked ✅' : 'Our call is booked ✅') : (scan ? 'Je winstscan-gesprek staat gepland ✅' : 'Ons gesprek staat gepland ✅');
  const head = en ? 'You’re booked!' : 'Staat erin!';
  const intro = en ? `Hi ${first}, thanks for booking. Here are the details.` : `Hoi ${first}, dank je voor het inplannen. Hieronder alles op een rij.`;
  const what = en ? `${mins} minutes · online with Julian` : `${mins} minuten · online met Julian`;
  const estLine = scan && estimate ? (en ? `Based on your answers you leave roughly ${estimate} on the table. In the call we look at where exactly it leaks and what pays off first.`
    : `Op basis van je antwoorden laat je naar schatting ${estimate} liggen. In het gesprek kijken we waar dat precies lekt en wat als eerste iets oplevert.`) : '';
  const agenda = en ? 'You also get a Google Calendar invite, so it’s in your calendar with one click.' : 'Je krijgt ook een uitnodiging in Google Agenda, zodat het met één klik in je agenda staat.';
  const resched = en ? 'Doesn’t suit you after all? Just reply to this email and we’ll pick another time.' : 'Past het toch niet? Antwoord gewoon op deze mail, dan prikken we een ander moment.';
  const sign = en ? 'See you then!' : 'Tot dan!';
  const btn = en ? 'Join with Google Meet' : 'Deelnemen via Google Meet';
  const L = start && end ? calLinks({ start, end, title: title || 'Gesprek met Julian (Viral Conversions)', meet }) : null;
  const addH = en ? 'Add to your calendar:' : 'Zet in je agenda:';
  const text = [intro, '', `${when}`, what, meet ? `${en ? 'Video link' : 'Videolink'}: ${meet}` : '', L ? `${addH} ${L.google}` : '', '', estLine, estLine ? '' : null, agenda, resched, '', sign, 'Julian, Viral Conversions', 'https://viralconversions.io']
    .filter(l => l !== null).join('\n').replace(/\n{3,}/g, '\n\n');
  const html = `<!doctype html><html><body style="margin:0;background:#EEF0F8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Arial,sans-serif;color:#0A0618">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#EEF0F8;padding:28px 12px"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px">
<tr><td style="padding:0 0 16px;text-align:center;font-weight:800;font-size:15px;letter-spacing:-.02em">Viral Conversions</td></tr>
<tr><td style="background:#ffffff;border-radius:22px;padding:30px 28px;box-shadow:0 20px 50px -30px rgba(29,78,216,.45)">
<div style="width:52px;height:52px;border-radius:50%;background:#16a34a;color:#fff;font-size:28px;line-height:52px;text-align:center;font-weight:800">&#10003;</div>
<h1 style="margin:18px 0 6px;font-size:24px;letter-spacing:-.03em">${esc(head)}</h1>
<p style="margin:0 0 18px;font-size:15px;line-height:1.5;color:rgba(10,6,24,.66)">${esc(intro)}</p>
<div style="background:rgba(29,78,216,.07);border:1px solid rgba(29,78,216,.22);border-radius:16px;padding:14px 16px">
<div style="font-size:16px;font-weight:800">${esc(when.charAt(0).toUpperCase() + when.slice(1))}</div>
<div style="font-size:13.5px;color:rgba(10,6,24,.62);margin-top:2px">${esc(what)}</div></div>
${L ? `<p style="margin:10px 0 0;font-size:13.5px;color:rgba(10,6,24,.62)">${esc(addH)} <a href="${esc(L.google)}" style="color:#1D4ED8;font-weight:700;text-decoration:none">Google</a> · <a href="${esc(L.outlook)}" style="color:#1D4ED8;font-weight:700;text-decoration:none">Outlook</a> · <a href="${esc(L.apple)}" style="color:#1D4ED8;font-weight:700;text-decoration:none">Apple</a></p>` : ''}
${meet ? `<p style="margin:18px 0 0"><a href="${esc(meet)}" style="display:inline-block;background:#1D4ED8;color:#ffffff;text-decoration:none;font-weight:800;font-size:15px;padding:13px 22px;border-radius:12px">${esc(btn)} →</a></p>` : ''}
${estLine ? `<p style="margin:18px 0 0;font-size:14.5px;line-height:1.55">${esc(estLine)}</p>` : ''}
<p style="margin:18px 0 0;font-size:14px;line-height:1.55;color:rgba(10,6,24,.66)">${esc(agenda)}<br>${esc(resched)}</p>
<p style="margin:20px 0 0;font-size:14.5px">${esc(sign)}<br><b>Julian</b>, Viral Conversions</p>
</td></tr>
<tr><td style="padding:16px 0 0;text-align:center;font-size:12px;color:rgba(10,6,24,.45)"><a href="https://viralconversions.io" style="color:rgba(10,6,24,.55);text-decoration:none">viralconversions.io</a> · <a href="https://wa.me/31639663370" style="color:rgba(10,6,24,.55);text-decoration:none">WhatsApp</a></td></tr>
</table></td></tr></table></body></html>`;
  return { subject, text, html };
}

// ── nep-Google voor lokaal testen (MOCK_GOOGLE=1): vaste bezette blokken, alles wordt gelogd ──
export const MOCK_LOG = [];
async function mock(url, init) {
  MOCK_LOG.push({ url, method: init.method || 'GET', body: init.body && typeof init.body === 'string' ? init.body : String(init.body || '') });
  if (url.includes('oauth2')) return { access_token: 'mock', expires_in: 3600 };
  if (url.includes('freeBusy')) {
    const day = nl(new Date(Date.now() + 3 * 864e5)).date;   // over 3 dagen 13:05–13:40 bezet
    return { calendars: { primary: { busy: [{ start: fromNL(day, '13:05').toISOString(), end: fromNL(day, '13:40').toISOString() }] } } };
  }
  if (url.includes('/events?conferenceDataVersion')) return { id: 'mockev', hangoutLink: 'https://meet.google.com/mock-test-abc' };
  if (url.includes('privateExtendedProperty')) return { items: [{ id: 'mockev', description: 'oud', attendees: [{ email: 'test@voorbeeld.nl' }] }] };
  if (url.includes('/events/')) return { id: 'mockev' };
  if (url.includes('gmail')) return { id: 'mockmail' };
  throw new Error('mock: onbekend ' + url);
}
