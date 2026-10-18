// GET /cal/slots → { busy: { 'YYYY-MM-DD': ['HH:MM', ...] }, avail: {weekdag: {enabled,start,end}}, google: bool }
// Bezette kwartieren komen rechtstreeks uit Julians Google Agenda (de boekingen van de site staan daar zelf ook in).
import { json, isOn, availability, busyQuarters } from '../../lib/vc-cal.js';

export async function onRequest({ env }) {
  let busy = {}, ok = isOn(env);
  try { busy = await busyQuarters(env); } catch (e) { console.log('[cal] busy mislukt', e.message); ok = false; }
  return json({ busy, avail: availability(env), google: ok });
}
