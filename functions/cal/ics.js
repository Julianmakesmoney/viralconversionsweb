// GET /cal/ics?s=<ISO start>&e=<ISO eind>&t=<titel>&m=<meet-link> → agenda-bestand. Op iPhone/Mac opent dit direct 'Zet in agenda'.
export async function onRequest({ request }) {
  const q = new URL(request.url).searchParams;
  const s = new Date(q.get('s') || ''), e = new Date(q.get('e') || '');
  if (isNaN(s) || isNaN(e) || e <= s || e - s > 4 * 3600e3) return new Response('Ongeldige tijd', { status: 400 });
  const clean = v => String(v || '').replace(/[\r\n]+/g, ' ').replace(/[,;\\]/g, m => '\\' + m).slice(0, 200);
  const meet = /^https:\/\/meet\.google\.com\/[\w-]+$/.test(q.get('m') || '') ? q.get('m') : '';
  const z = d => d.toISOString().replace(/[-:]/g, '').replace(/\.\d+/, '');
  const ics = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Viral Conversions//Agenda//NL', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH', 'BEGIN:VEVENT',
    `UID:${z(s)}-${Math.abs([...(q.get('t') || '')].reduce((h, c) => (h * 31 + c.charCodeAt(0)) | 0, 7))}@viralconversions.io`, `DTSTAMP:${z(new Date())}`,
    `DTSTART:${z(s)}`, `DTEND:${z(e)}`, `SUMMARY:${clean(q.get('t') || 'Gesprek met Julian (Viral Conversions)')}`,
    `DESCRIPTION:${clean(meet ? 'Videolink: ' + meet : 'Online videogesprek met Julian van Viral Conversions.')}`, meet ? `LOCATION:${meet}` : '', meet ? `URL:${meet}` : '',
    'BEGIN:VALARM', 'TRIGGER:-PT15M', 'ACTION:DISPLAY', 'DESCRIPTION:Gesprek met Julian', 'END:VALARM', 'END:VEVENT', 'END:VCALENDAR'].filter(Boolean).join('\r\n');
  return new Response(ics, { headers: { 'Content-Type': 'text/calendar; charset=utf-8', 'Content-Disposition': 'inline; filename="gesprek-viral-conversions.ics"', 'Cache-Control': 'no-store' } });
}
