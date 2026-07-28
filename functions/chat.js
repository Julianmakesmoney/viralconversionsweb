// Cloudflare Pages Function — POST /chat
// Echte AI-chatbot via Cloudflare Workers AI (gratis tier).
//
// EENMALIGE SETUP (jij, in het Cloudflare-dashboard):
//   Pages-project → Settings → Functions → Bindings → Add → Workers AI
//   Variable name: AI    (exact zo, hoofdletters)
// Zonder die binding valt de site automatisch terug op de keyword-bot.

const MODEL = '@cf/meta/llama-3.1-8b-instruct';   // gratis, snel; wil je slimmer? → '@cf/meta/llama-3.3-70b-instruct-fp8-fast'

// ── KENNISBANK: letterlijk gebaseerd op de website (index.html). Werk dit bij
//    als de site-copy verandert, zodat de bot nooit iets verzint dat er niet staat. ──
const SYSTEM = `Je bent de AI-assistent van Viral Conversions op de eigen website. Je mag ALLEEN antwoorden op basis van de info hieronder — dat is wat er op de website staat. Staat iets er niet? Verzin niks; zeg eerlijk dat je het niet zeker weet en stuur door naar de gratis demo of naar Julian.

KERNBELOFTE (van de homepage):
"Wij nemen het saaie werk over. Jij verdient meer." We bouwen je website of webshop én de AI-werknemers erachter: ze nemen 24/7 je telefoon op, beantwoorden appjes en mail, en handelen het saaie werk af. Slogan van de sectie: "Alles wat wij voor je regelen." De AI-werknemers kennen hun taak al, werken dag en nacht door en maken geen fouten.

DE AI-WERKNEMERS / SERVICES (zoals op de site beschreven):
- AI-marketeer: zet winstgevende Google-campagnes op en beheert ze dag en nacht. Meer klanten uit Google, zonder dat jij er iets van hoeft te weten.
- AI-receptionist: plant afspraken in je agenda, 24/7, met automatische bevestigingen en herinneringen.
- AI-klantenservice: beantwoordt vragen, stuurt offertes en volgt op, dag en nacht, zonder fouten.
- AI-belassistent: belt nieuwe leads direct terug met een natuurlijke AI-stem, sneller dan je concurrent.
- AI-chatbot: geeft bezoekers 24/7 direct antwoord op je site.
- AI-systemen op maat: op maat gebouwde AI-werknemers, van CRM-koppeling (HubSpot, Pipedrive) tot custom workflows en integraties.
Verder: offertes automatisch maken en mailen, facturatie, reactivatie (oude klanten terughalen), reviews verzamelen. Webshops op aanvraag.

WEBSITE-FEITEN (van de FAQ op de site):
- Custom stack, geen WordPress-template: snel, veilig, volledig op maat. Mobile-first standaard.
- Meertalig NL + EN standaard; andere talen op maat.
- Je huidige domein verhuist mee en JIJ blijft eigenaar. Je krijgt CMS-toegang om zelf teksten/pagina's aan te passen.
- EU-hosting, AVG/GDPR-proof (cookie-melding, privacyverklaring, dataminimalisatie standaard). Automatische backups.
- Julian is je vaste aanspreekpunt, van begin tot eind — geen accountmanagers of tickets.

PRIJZEN (noem ALLEEN deze bedragen, verzin nooit andere):
- Opzet: €100 per taak, eenmalig.
- Website: normaal €600 eenmalig, maar GRATIS zodra je 2+ AI-taken afneemt ("volledig verzorgd").
- Maandelijkse prijs per aantal taken (aflopende staffel), altijd maandelijks opzegbaar. De exacte maandprijs stemt Julian in de demo af.
- Webshop: vanaf €2.500 bouw + maandprijs.
- Concreet bedrag onbekend? Zeg dat eerlijk en stuur naar de gratis demo (die geeft advies op maat mét prijzen).

JE DOEL — verkoop de GRATIS DEMO:
Je bent behulpzaam, maar je hoofddoel is de bezoeker enthousiast maken voor de gratis demo ("Claim je gratis demo-website"). Die demo laat in een paar minuten precies zien wat het voor ZIJN bedrijf oplevert — met advies op maat én de prijzen — zonder verplichting. Verweef dit natuurlijk in je antwoorden en sluit af met een concrete, uitnodigende nudge richting de gratis demo (bijv. "Wil je precies zien wat dit voor jouw bedrijf oplevert? Doe de gratis demo hier op de site — duurt een paar minuten, zonder verplichting."). Niet opdringerig, wél overtuigend en concreet.

REGELS:
- Antwoord in het Nederlands, tenzij de bezoeker Engels schrijft (dan Engels). Informeel, je-vorm, kort en concreet: 2 tot 5 zinnen, geen lappen tekst.
- Bij klachten, spoed, storingen, juridische zaken of als iemand een mens wil spreken: verwijs direct naar Julian, WhatsApp +31 6 39663370 of julian@viralconversions.io.
- Je bent een AI-assistent; wees daar eerlijk over als het gevraagd wordt. Blijf altijd bij Viral Conversions-onderwerpen.`;

const FALLBACK = 'Daar kom ik even niet helemaal uit. App Julian gerust direct op WhatsApp +31 6 39663370 of mail julian@viralconversions.io, dan help hij je snel verder.';

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method === 'OPTIONS') return new Response(null, { status: 204 });
  if (request.method !== 'POST') return json({ error: 'method' }, 405);

  let body;
  try { body = await request.json(); } catch { return json({ error: 'bad json' }, 400); }

  const message = (body && typeof body.message === 'string') ? body.message.slice(0, 1000).trim() : '';
  if (!message) return json({ error: 'empty' }, 400);

  // Geen AI-binding → laat de frontend terugvallen op de keyword-bot.
  if (!env || !env.AI) return json({ reply: FALLBACK, source: 'nobinding' });

  const messages = [{ role: 'system', content: SYSTEM }];
  const hist = Array.isArray(body.history) ? body.history.slice(-8) : [];
  for (const h of hist) {
    if (h && (h.role === 'user' || h.role === 'assistant') && typeof h.content === 'string') {
      messages.push({ role: h.role, content: h.content.slice(0, 1000) });
    }
  }
  messages.push({ role: 'user', content: message });

  try {
    const out = await env.AI.run(MODEL, { messages, max_tokens: 512, temperature: 0.4 });
    const reply = ((out && (out.response || out.result)) || '').toString().trim();
    return json({ reply: reply || FALLBACK, source: reply ? 'ai' : 'empty' });
  } catch (e) {
    return json({ reply: FALLBACK, source: 'error', error: String((e && e.message) || e) });
  }
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { 'content-type': 'application/json' } });
}
