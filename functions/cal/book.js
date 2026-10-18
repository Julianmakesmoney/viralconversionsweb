// POST /cal/book → afspraak in Julians Google Agenda (met Meet-link), bevestigingsmail aan de lead en briefing aan Julian.
// kind: 'call' (via /plan, 15 min) · 'scan' (winstscan-gesprek, 30 min) · 'intro' (kennismaking via de oude modal, 30 min)
import { json, isOn, availability, busyQuarters, quarters, fromNL, nl, createEvent, sendMail, confirmMail, whenText, DAYS_AHEAD, LEAD_MS } from '../../lib/vc-cal.js';

const TITLES = { call: ['Gesprek met Julian', 'Call with Julian'], scan: ['Winstscan', 'Profit scan'], intro: ['Kennismaking met Julian', 'Intro call with Julian'] };

export async function onRequest(ctx) {
  const { request, env } = ctx;
  if (request.method !== 'POST') return json({ success: false, error: 'Alleen POST.' }, 405);
  let d; try { d = await request.json(); } catch (e) { return json({ success: false, error: 'Ongeldige invoer.' }, 400); }
  const s = (v, n) => String(v || '').trim().slice(0, n);
  const name = s(d.name, 120), email = s(d.email, 160), phone = s(d.phone, 40), company = s(d.company, 160), notes = s(d.notes, 12000);
  const date = s(d.date, 10), time = s(d.time, 5), en = d.lang === 'en', estimate = s(d.estimate, 200);
  const kind = ['call', 'scan', 'intro'].includes(d.kind) ? d.kind : 'scan', minutes = kind === 'call' ? 15 : 30;
  if (name.length < 2 || !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email) || !/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^\d{2}:\d{2}$/.test(time) || +time.slice(3) % 15)
    return json({ success: false, error: 'Verplichte velden ontbreken.' }, 400);
  if (!isOn(env)) return json({ success: false, error: 'De agenda is nog niet gekoppeld.' }, 503);

  // past het binnen de werktijden, niet te vroeg en niet te ver vooruit?
  const start = fromNL(date, time), end = new Date(start.getTime() + minutes * 60e3);
  const a = availability(env)[new Date(date + 'T12:00:00Z').getUTCDay()];
  const endT = nl(end).time === '00:00' ? '24:00' : nl(end).time;
  if (!a || !a.enabled || time < a.start || endT > a.end || start.getTime() < Date.now() + LEAD_MS - 60e3 || start.getTime() > Date.now() + DAYS_AHEAD * 864e5)
    return json({ success: false, error: 'Dit tijdslot is niet beschikbaar.' }, 409);

  // vlak voor het boeken: verse stand uit Google
  let busy;
  try { busy = await busyQuarters(env); } catch (e) { console.log('[cal] busy', e.message); return json({ success: false, error: 'De agenda is even niet bereikbaar.' }, 502); }
  if (quarters(start, end).some(([day, q]) => (busy[day] || []).includes(q))) return json({ success: false, error: 'Dit tijdslot is al geboekt.' }, 409);

  const bid = s(d.id, 40) || String(Date.now()), who = company || name;
  const title = `${TITLES[kind][en ? 1 : 0]} · ${who}`;
  const desc = [`${name}${company ? ' · ' + company : ''}`, `E-mail: ${email}`, phone ? `Telefoon: ${phone}` : '', estimate ? `Schatting: ${estimate}` : '', '', notes].filter((l, i) => l || i === 4).join('\n');
  let ev;
  try { ev = await createEvent(env, { bid, date, time, minutes, email, name, kind, title: title + ' (Viral Conversions)', notes: desc }); }
  catch (e) { console.log('[cal] event', e.message); return json({ success: false, error: 'De agenda is even niet bereikbaar.' }, 502); }

  // mails: niet wachten met antwoorden (waitUntil), en een mislukte mail laat de boeking nooit mislukken
  const when = whenText(date, time, en), first = name.split(' ')[0];
  const m = confirmMail({ first, when, mins: minutes, meet: ev.meet, kind, en, estimate, start, end, title: TITLES[kind][en ? 1 : 0] + ' (Viral Conversions)' });
  const notify = (env.NOTIFY_EMAIL || env.FROM_EMAIL || 'julian@viralconversions.io').trim();
  const brief = `${kind === 'call' ? 'Nieuw gesprek (via /plan)' : kind === 'scan' ? 'Nieuwe winstscan' : 'Nieuwe kennismaking'}: ${who}\nWanneer: ${when} (${minutes} min)\nNaam: ${name}\nE-mail: ${email}\nTelefoon: ${phone || '-'}\n${ev.meet ? 'Meet: ' + ev.meet + '\n' : ''}\nStaat al in je Google Agenda.\n\n${notes}`;
  const mails = Promise.allSettled([
    sendMail(env, { to: email, subject: m.subject, text: m.text, html: m.html }),
    sendMail(env, { to: notify, subject: `${kind === 'call' ? 'Gesprek gepland' : kind === 'scan' ? 'Winstscan gepland' : 'Kennismaking gepland'}: ${who} · ${when}`, text: brief, replyTo: email }),
  ]).then(r => r.forEach(x => x.status === 'rejected' && console.log('[cal] mail', x.reason && x.reason.message)));
  if (ctx.waitUntil) ctx.waitUntil(mails); else await mails;

  return json({ success: true, id: bid, meet: ev.meet, start: start.toISOString(), end: end.toISOString(), title: TITLES[kind][en ? 1 : 0] + ' (Viral Conversions)' });
}
