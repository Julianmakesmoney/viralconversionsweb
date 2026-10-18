// POST /cal/intake → aanvulling na een winstscan-boeking (onderzoeksvragen + volledige analyse) in de agenda-afspraak, en een mail aan Julian.
import { json, isOn, findEvent, patchEvent, sendMail } from '../../lib/vc-cal.js';

export async function onRequest({ request, env }) {
  if (request.method !== 'POST') return json({ success: false }, 405);
  let d; try { d = await request.json(); } catch (e) { return json({ success: false }, 400); }
  const bid = String(d.bid || '').replace(/[^\w-]/g, '').slice(0, 40), email = String(d.email || '').trim().toLowerCase(), notes = String(d.notes || '').trim().slice(0, 20000);
  if (!bid || !email || !notes) return json({ success: false, error: 'Verplichte velden ontbreken.' }, 400);
  if (!isOn(env)) return json({ success: false, error: 'De agenda is nog niet gekoppeld.' }, 503);
  try {
    const ev = await findEvent(env, bid);
    if (!ev || !(ev.attendees || []).some(x => (x.email || '').toLowerCase() === email)) return json({ success: false, error: 'Boeking niet gevonden.' }, 404);   // alleen wie boekte, kan aanvullen
    await patchEvent(env, ev.id, { description: ((ev.description || '') + '\n\n── AANVULLING NA DE BOEKING ──\n' + notes).slice(0, 7900) });
    const notify = (env.NOTIFY_EMAIL || env.FROM_EMAIL || 'julian@viralconversions.io').trim();
    try { await sendMail(env, { to: notify, subject: `Aanvulling winstscan: ${String(d.company || '').slice(0, 120) || email}`, text: `Na de boeking vulde ${email} de volledige analyse in.\n\n${notes}` }); } catch (e) { console.log('[cal] mail', e.message); }
    return json({ success: true });
  } catch (e) { console.log('[cal] intake', e.message); return json({ success: false, error: 'Server error.' }, 502); }
}
