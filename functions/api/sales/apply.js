// Cloudflare Pages Function — POST /api/sales/apply
// Stuurt de sollicitatie door naar het salesportaal op Render (zoals de rest van /api/*) en mailt Julian bij succes.
// Het wachtwoord van de sollicitant gaat nooit mee in de mail. Een mislukte mail laat de sollicitatie nooit mislukken.
import { isOn, sendMail } from '../../../lib/vc-cal.js';

const LABELS = [['name', 'Naam'], ['email', 'E-mail'], ['phone', 'Telefoon'], ['city', 'Woonplaats'], ['hours_per_week', 'Uren per week'],
  ['bron', 'Bron'], ['bron_detail', 'Bron (detail)'], ['ref_code', 'Referral-code'], ['motivation', 'Motivatie'], ['sales_background', 'Sales-ervaring'],
  ['discipline', 'Discipline'], ['rejection', 'Omgaan met afwijzing'], ['strengths', 'Sterke punten'], ['weaknesses', 'Zwakke punten']];

export async function onRequest({ request, env, waitUntil }) {
  const body = request.method === 'POST' ? await request.text() : undefined;
  const r = await fetch('https://viralconversionsweb.onrender.com/api/sales/apply', {
    method: request.method, headers: { 'Content-Type': request.headers.get('Content-Type') || 'application/json' }, body });
  const out = await r.text();

  let ok = false, d = {};
  try { ok = r.ok && JSON.parse(out).success === true; d = JSON.parse(body || '{}'); } catch (e) {}
  if (ok && isOn(env)) {
    const s = v => String(v == null ? '' : v).trim().slice(0, 2000);
    const text = ['Nieuwe sollicitatie als freelance cold caller.', '',
      ...LABELS.filter(([k]) => s(d[k])).map(([k, l]) => `${l}: ${s(d[k])}`), '',
      'Bekijk en beoordeel in het salesportaal.'].join('\n');
    const mail = sendMail(env, { to: (env.NOTIFY_EMAIL || env.FROM_EMAIL || 'julian@viralconversions.io').trim(),
      subject: `Nieuwe sollicitatie: ${s(d.name).slice(0, 80) || 'onbekend'}`, text, replyTo: /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s(d.email)) ? s(d.email) : undefined })
      .catch(e => console.log('[apply] mail', e.message));
    if (waitUntil) waitUntil(mail); else await mail;
  }
  return new Response(out, { status: r.status, headers: { 'Content-Type': r.headers.get('Content-Type') || 'application/json', 'Cache-Control': 'no-store' } });
}
