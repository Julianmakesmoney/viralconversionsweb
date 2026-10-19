// Cloudflare Pages Function — GET /scan?url=…&name=…&product=…&lang=nl
// De echte winstscan: kijkt live naar de website van de prospect terwijl die de vragen invult.
//
// Wat het checkt (alles echte data, niets verzonnen):
//   - de homepage zelf: https, mobiel, zoekomschrijving, schema, offerteformulier, WhatsApp/telefoon,
//     B2B-bestelomgeving, meetcode
//   - robots.txt: mogen AI-zoekmachines (GPTBot, ClaudeBot, PerplexityBot …) de site lezen?
//   - Google PageSpeed (mobiel): snelheidsscore en laadtijd van het grootste element
//   - AI-concurrententest: 3 echte inkopersvragen over het product. Wie raadt AI aan, en jij?
//     Met env ANTHROPIC_API_KEY via Claude met web search (zoekt live, eerste keus; CLAUDE_MODEL optioneel),
//     anders met PPLX_KEY via Perplexity, anders via Workers AI (binding 'AI'; kennis van het model, geen live zoeken).
// Optioneel: env PSI_KEY (Google API-sleutel) voor PageSpeed. Zonder sleutel is het gedeelde quotum vaak op.
// ?only=hook → site + AI (snel, voor de directe zichtbaarheidsscan) · ?only=psi → alleen PageSpeed.
// Resultaten worden 24 uur gecachet per site + product (een refresh kost niets).
//
// Antwoord: { ok, url, checks, psi, ai, score, findings: [{ zone, status: 'ok'|'warn'|'bad', nl, en }] }

const MODELS = [
  '@cf/meta/llama-3.3-70b-instruct-fp8-fast',
  '@cf/meta/llama-4-scout-17b-16e-instruct',
  '@cf/mistralai/mistral-small-3.1-24b-instruct',
  '@cf/meta/llama-3.1-8b-instruct-fast',
];
const AI_BOTS = ['GPTBot', 'OAI-SearchBot', 'ChatGPT-User', 'ClaudeBot', 'PerplexityBot', 'Google-Extended', 'CCBot'];
const UA = 'Mozilla/5.0 (compatible; VC-Winstscan/1.0; +https://viralconversions.io/winstscan)';

export async function onRequest({ request, env }) {
  if (request.method === 'OPTIONS') return new Response(null, { status: 204 });
  const q = new URL(request.url).searchParams;
  const lang = q.get('lang') === 'en' ? 'en' : 'nl';
  const name = (q.get('name') || '').trim().slice(0, 80);
  const product = (q.get('product') || '').trim().slice(0, 80);
  const only = q.get('only') || '';
  const target = normUrl(q.get('url') || '');

  const cache = typeof caches !== 'undefined' && caches.default, ck = cache && new Request('https://scan.cache/' + encodeURIComponent([only, target, product.toLowerCase(), name.toLowerCase(), lang].join('|')));
  if (cache) { const hit = await cache.match(ck).catch(() => null); if (hit) return hit; }

  const [site, psi, raw] = await Promise.all([
    target && only !== 'psi' ? scanSite(target) : null,
    target && only !== 'hook' ? pageSpeed(target, env.PSI_KEY) : null,
    product && only !== 'psi' ? aiAsk(env, product, lang) : null,
  ]);
  const ai = raw ? aiMatch(raw, name, target, site) : null;
  const body = only === 'psi'
    ? { ok: true, psi, findings: findings(null, psi, null, product) }
    : (target && !site)
      ? { ok: false, error: 'unreachable', url: target, ai, score: score(null, psi, ai), findings: findings(null, psi, ai, product) }
      : { ok: true, url: site ? site.url : null, checks: site, psi, ai, score: score(site, psi, ai), findings: findings(site, psi, ai, product) };
  const res = json(body);
  if (cache && body.ok) { const c = res.clone(); c.headers.set('Cache-Control', 'public, max-age=86400'); await cache.put(ck, c).catch(() => {}); }
  return res;
}

// ── url: alleen publieke http(s)-adressen ─────────────────────
function normUrl(raw) {
  let s = raw.trim(); if (!s) return null;
  if (!/^https?:\/\//i.test(s)) s = 'https://' + s;
  let u; try { u = new URL(s); } catch (e) { return null; }
  const h = u.hostname.toLowerCase();
  if (!h.includes('.') || h === 'localhost' || h.endsWith('.local') || h.endsWith('.internal') || /^[\d.]+$/.test(h) || h.includes(':')) return null;
  return u.origin + '/';
}

async function get(url, ms, max) {
  const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), ms), t0 = Date.now();
  try {
    const r = await fetch(url, { headers: { 'User-Agent': UA, 'Accept': 'text/html,text/plain,*/*' }, redirect: 'follow', signal: ctl.signal });
    const ttfb = Date.now() - t0;
    let text = '';
    if (r.body) { const rd = r.body.getReader(), dec = new TextDecoder(); let n = 0;
      while (true) { const { done, value } = await rd.read(); if (done) break; n += value.length; text += dec.decode(value, { stream: true }); if (n > (max || 1.5e6)) { try { rd.cancel(); } catch (e) {} break; } } }
    return { status: r.status, url: r.url || url, ttfb, text, type: r.headers.get('content-type') || '' };
  } catch (e) { return null; } finally { clearTimeout(timer); }
}

// ── de homepage + robots.txt + llms.txt ───────────────────────
async function scanSite(url) {
  const home = await get(url, 12000);
  if (!home || home.status >= 400 || !/html/i.test(home.type + home.text.slice(0, 200))) return null;
  const origin = new URL(home.url).origin;
  const [robots, llms] = await Promise.all([get(origin + '/robots.txt', 6000, 2e5), get(origin + '/llms.txt', 6000, 2e5)]);
  const h = home.text, low = h.toLowerCase();
  const vis = low.replace(/<script[\s\S]*?<\/script>|<style[\s\S]*?<\/style>|<!--[\s\S]*?-->/g, ' ');   /* zichtbare tekst + links, zonder code */
  const hrefs = [...vis.matchAll(/href=["']([^"']+)["']/g)].map(m => m[1]).join(' ');
  const meta = n => { const m = h.match(new RegExp('<meta[^>]+name=["\']' + n + '["\'][^>]*>', 'i')); return m ? ((m[0].match(/content=["']([^"']*)["']/i) || [])[1] || '').trim() : ''; };
  const ld = [...h.matchAll(/<script[^>]+application\/ld\+json[^>]*>([\s\S]*?)<\/script>/gi)].map(m => m[1]).join(' ');
  const forms = [...h.matchAll(/<form[\s\S]*?<\/form>/gi)].map(m => m[0].toLowerCase());
  const robotsTxt = robots && robots.status < 400 && !/<html/i.test(robots.text) ? robots.text : '';
  return {
    url: home.url,
    https: home.url.startsWith('https://'),
    ttfb: home.ttfb,
    mobile: /<meta[^>]+name=["']viewport["']/i.test(h),
    title: ((h.match(/<title[^>]*>([^<]*)<\/title>/i) || [])[1] || '').trim().slice(0, 120),
    description: meta('description').slice(0, 200),
    schema: /"@type"\s*:\s*\[?\s*"(Organization|LocalBusiness|Store|Corporation|WholesaleStore|Product|OnlineStore|ProfessionalService)"/i.test(ld),
    quoteForm: forms.some(f => /offerte|quote|aanvraag|aanvragen|prijsopgave|request/.test(f)) || /href=["'][^"']*(offerte|quote|aanvraag)[^"']*["']/i.test(h),
    form: forms.length > 0,
    whatsapp: /wa\.me\/|api\.whatsapp\.com|whatsapp/.test(hrefs) || /whatsapp/.test(vis),
    phone: /href=["']tel:/i.test(h) || /(?:\+31|0031|\b0)[\s.-]?\(?\d{1,3}\)?(?:[\s.-]?\d){6,8}\b/.test(vis.replace(/<[^>]+>/g, ' '))
      || /(?:\+31|\b0)\d{1,3}[ -]\d{2,4}[ -]?\d{2,4}\b/.test(h),   /* ook als gewone tekst, of in de paginadata van een site die met JavaScript wordt opgebouwd (dan alleen in de gangbare schrijfwijze met spaties of streepjes) */
    portal: /(winkelwagen|winkelmand|\/cart|checkout|\/webshop|\/shop|\/account|\/login|inloggen|\/bestellen|\/order|b2b|klantportaal|dealer)/.test(hrefs) || />\s*(winkelwagen|winkelmand|in winkelwagen|bestellen|inloggen|mijn account)\s*</.test(vis)
      || /winkelwagen|winkelmand|add-to-cart|addtocart|mini-?cart|shopping-?cart|minicart|woocommerce|shopify|magento|lightspeed|ccvshop/.test(low)
      || /bestelgeschiedenis|bestellijst|mijn bestellingen|order history|my orders/.test(low),   /* ook in JS-data van de pagina; webshop-code: ook als de site met JavaScript wordt opgebouwd */
    tracking: /googletagmanager|gtag\(|google-analytics|plausible|matomo|clarity\.ms|hotjar|fathom|posthog/i.test(low),
    aiBlocked: blockedBots(robotsTxt),
    sitemap: /^\s*sitemap:/im.test(robotsTxt),
    llms: !!(llms && llms.status === 200 && !/<html/i.test(llms.text.slice(0, 500)) && llms.text.trim().length > 20),
  };
}

// welke AI-bots robots.txt de hele site ontzegt (eigen groep, of de *-groep)
function blockedBots(txt) {
  if (!txt) return [];
  const groups = []; let cur = null, lastUA = false;
  txt.split(/\r?\n/).forEach(line => {
    const l = line.replace(/#.*/, '').trim(); const m = l.match(/^([a-z-]+)\s*:\s*(.*)$/i); if (!m) return;
    const k = m[1].toLowerCase(), v = m[2].trim();
    if (k === 'user-agent') { if (!lastUA) { cur = { ua: [], dis: [], allow: [] }; groups.push(cur); } cur.ua.push(v.toLowerCase()); lastUA = true; }
    else { lastUA = false; if (!cur) return; if (k === 'disallow') cur.dis.push(v); if (k === 'allow') cur.allow.push(v); }
  });
  const fullBlock = g => g.dis.includes('/') && !g.allow.includes('/');
  const star = groups.find(g => g.ua.includes('*'));
  return AI_BOTS.filter(b => { const g = groups.find(x => x.ua.includes(b.toLowerCase())); return g ? fullBlock(g) : !!(star && fullBlock(star)); });
}

// ── Google PageSpeed (mobiel) ─────────────────────────────────
async function pageSpeed(url, key) {
  const api = 'https://www.googleapis.com/pagespeedonline/v5/runPagespeed?strategy=mobile&category=performance&url=' + encodeURIComponent(url) + (key ? '&key=' + key : '');
  const r = await get(api, 50000, 6e6); if (!r || r.status !== 200) return null;
  try { const d = JSON.parse(r.text).lighthouseResult; const a = d.audits || {};
    return { score: Math.round((d.categories.performance.score || 0) * 100), lcp: (a['largest-contentful-paint'] || {}).displayValue || '', lcpMs: (a['largest-contentful-paint'] || {}).numericValue || 0 };
  } catch (e) { return null; }
}

// ── AI-concurrententest: 3 inkopersvragen, wie wordt aangeraden? ──
function prompts(product, lang) {
  /* alleen bedrijven die het product LEVEREN: anders noemt het model aannemers of bouwbedrijven die het gebruiken (gezien bij 'bouwmaterialen': BAM, Heijmans) */
  const scope = lang === 'en' ? ' Only name wholesalers, distributors or suppliers that sell it to businesses; no contractors, builders or end users.' : ' Noem alleen groothandels, distributeurs of leveranciers die het aan bedrijven verkopen; geen aannemers, bouwbedrijven of eindgebruikers.';
  const tail = lang === 'en' ? ' Answer with only a list of company names, one per line, no explanation.' : ' Antwoord alleen met een lijst bedrijfsnamen, één per regel, geen uitleg.';
  return (lang === 'en'
    ? [`I am a buyer at a company in the Netherlands looking for a reliable wholesaler or supplier for ${product}. Which companies do you recommend?`,
       `What are the best B2B suppliers of ${product} in the Netherlands?`,
       `Where can a business buy ${product} in bulk in the Netherlands?`]
    : [`Ik ben inkoper bij een bedrijf in Nederland en zoek een betrouwbare groothandel of leverancier voor ${product}. Welke bedrijven raad je aan?`,
       `Wat zijn de beste B2B-leveranciers van ${product} in Nederland?`,
       `Waar kan ik als bedrijf ${product} in bulk inkopen in Nederland?`]).map(p => p + scope + tail);
}
// Claude met live web search: één verzoek stelt de drie inkopersvragen en geeft per vraag de bedrijven die het
// zou aanraden. Rauwe fetch i.p.v. de SDK: Pages Functions draaien hier zonder build/bundel-stap.
async function claudeAsk(env, product, lang) {
  const P = prompts(product, lang).map(p => p.replace(/ (Antwoord alleen|Answer with only).*$/, ''));
  const ask = (lang === 'en'
    ? 'You are helping test how AI assistants answer business buyers. For each of the three buyer questions below, search the web the way an AI assistant with web search would, and list the companies you would actually recommend (max 6 per question, real company names only, best first).'
    : 'Je helpt testen hoe AI-assistenten inkopers antwoorden. Zoek voor elk van de drie inkopersvragen hieronder op het web, zoals een AI-assistent met web search dat doet, en noem de bedrijven die je echt zou aanraden (max 6 per vraag, alleen echte bedrijfsnamen, beste eerst).')
    + '\n\n' + P.map((p, i) => `${i + 1}. ${p}`).join('\n')
    + '\n\n' + (lang === 'en' ? 'Reply with JSON only, no other text:' : 'Antwoord alleen met JSON, geen andere tekst:') + ' {"answers": [["…"], ["…"], ["…"]]}';
  const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), 55000);
  try {
    const r = await fetch('https://api.anthropic.com/v1/messages', { method: 'POST', signal: ctl.signal,
      headers: { 'x-api-key': env.ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json' },
      body: JSON.stringify({ model: env.CLAUDE_MODEL || 'claude-sonnet-5-5', max_tokens: 1500,
        tools: [{ type: 'web_search_20260209', name: 'web_search', max_uses: 5, user_location: { type: 'approximate', country: 'NL', timezone: 'Europe/Amsterdam' } }],
        messages: [{ role: 'user', content: ask }] }) });
    if (!r.ok) { console.log('[scan] claude', r.status, (await r.text()).slice(0, 300)); return null; }
    const d = await r.json();
    const txt = (d.content || []).filter(b => b.type === 'text').map(b => b.text).join('');
    const m = txt.match(/\{[\s\S]*\}/); if (!m) return null;
    const lists = JSON.parse(m[0]).answers;
    if (!Array.isArray(lists) || !lists.some(l => Array.isArray(l) && l.length)) return null;
    return { source: 'claude', model: d.model, answers: P.map((_, i) => Array.isArray(lists[i]) && lists[i].length ? lists[i].map(String).join('\n') : null) };
  } catch (e) { console.log('[scan] claude', e.message); return null; }
  finally { clearTimeout(t); }
}
async function aiAsk(env, product, lang) {
  const P = prompts(product, lang);
  if (env.ANTHROPIC_API_KEY) { const c = await claudeAsk(env, product, lang); if (c) return c; }
  if (env.PPLX_KEY) {   // live zoeken op het web, zoals ChatGPT search en Perplexity zelf
    const out = await Promise.all(P.map(p => fetch('https://api.perplexity.ai/chat/completions', { method: 'POST',
        headers: { 'Authorization': 'Bearer ' + env.PPLX_KEY, 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: 'sonar', messages: [{ role: 'user', content: p }], max_tokens: 260, temperature: 0.2 }) })
      .then(r => r.ok ? r.json() : null).then(d => d && d.choices && d.choices[0] ? d.choices[0].message.content : null).catch(() => null)));
    if (out.some(Boolean)) return { source: 'perplexity', answers: out };
  }
  if (!env.AI) return null;
  for (const m of MODELS) {
    try {
      const out = await Promise.all(P.map(p => env.AI.run(m, { messages: [{ role: 'user', content: p }], max_tokens: 220, temperature: 0.3 })
        .then(r => String((r && (r.response || r.result || '')) || '').trim() || null)));
      if (out.some(Boolean)) return { source: env.MOCK ? 'mock' : 'workers-ai', model: m.split('/').pop(), answers: out };   /* mock: lokaal voorbeeld, zie tools/site/run_fn.mjs */
    } catch (e) { /* volgend model */ }
  }
  return null;
}
function names(txt) {
  return String(txt || '').split(/\n+/).map(s => s.replace(/\[\d+\]/g, '').replace(/^[\s\d.)*\-•#]+/, '').replace(/\*\*/g, '').split(/\s[-–:]\s|:\s/)[0].trim())
    .filter(s => s && s.length > 1 && s.length < 60 && !/^(hier|here|enkele|some|de beste|the best|bedrijven|companies)/i.test(s)).slice(0, 10);
}
function aiMatch(raw, name, url, site) {
  const host = url ? new URL(url).hostname.replace(/^www\./, '') : '';
  const you = [core(name), core(host.split('.')[0]), site && site.title ? core(site.title.split(/[|\-–:]/)[0]) : ''].filter(v => v.length > 3);
  /* jouw naam moet als heel woord voorkomen (of de hele naam zijn): 'Eddy' is niet 'Eddyline Kayaks', 'Verdouw' wel 'Verdouw Bouwmaterialen' */
  const isYou = n => { const c = core(n); if (c.length < 3) return false; const words = String(n).toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
    return you.some(v => c === v || words.indexOf(v) > -1 || words.join('') === v || (v.length >= 8 && c.indexOf(v) === 0)); };
  const tally = {}; let hits = 0, asked = 0;
  raw.answers.forEach(a => { if (!a) return; asked++; const L = names(a); if (L.some(isYou)) hits++;
    const seen = new Set(); L.filter(n => !isYou(n)).forEach(n => { const k = core(n); if (!k || seen.has(k)) return; seen.add(k); (tally[k] = tally[k] || { name: n, count: 0 }).count++; }); });
  const top = Object.values(tally).sort((a, b) => b.count - a.count).slice(0, 5);
  const others = Object.values(tally).reduce((n, t) => n + t.count, 0);   /* alle aanbevelingen aan anderen: voor de blokjes op het scherm */
  return { source: raw.source, model: raw.model, asked, hits, mentioned: hits > 0, top, others, names: top.slice(0, 3).map(t => t.name) };
}

// ── zichtbaarheidsscore 0–100 (indicatie, alleen wat we echt gemeten hebben) ──
function score(s, psi, ai) {
  let got = 0, max = 0; const add = (pts, ok) => { max += pts; got += ok * pts; };
  if (ai && ai.asked) add(40, ai.hits / ai.asked);
  if (s) { add(10, s.aiBlocked.length ? 0 : 1); add(10, s.schema ? 1 : 0); add(10, s.description && s.title ? 1 : s.title ? .5 : 0); add(5, s.mobile ? 1 : 0); add(5, s.sitemap ? 1 : 0); add(5, s.llms ? 1 : 0); }
  if (psi) add(15, psi.score / 100); else if (s) add(15, s.ttfb < 600 ? 1 : s.ttfb < 1500 ? .6 : .2);
  return max ? Math.round(got / max * 100) : null;
}
const core = s => String(s).toLowerCase().replace(/\b(b\.?v\.?|bv|groothandel|wholesale|nederland|nl|holland|the)\b/g, '').replace(/[^a-z0-9]/g, '');

// ── bevindingen per zone (0 gevonden worden · 1 klant worden · 2 na de order) ──
function findings(s, psi, ai, product) {
  const F = [], add = (zone, status, nl, en) => F.push({ zone, status, nl, en });
  if (ai && ai.asked) {
    const wel = ai.names.length ? '. Wel: ' + ai.names.join(', ') : '', does = ai.names.length ? '. It does name: ' + ai.names.join(', ') : '';
    if (ai.hits === ai.asked) add(0, 'ok', `AI raadt je aan bij “${product}” (${ai.hits} van ${ai.asked} vragen)`, `AI recommends you for “${product}” (${ai.hits} of ${ai.asked} questions)`);
    else if (ai.hits) add(0, 'warn', `AI raadt je maar in ${ai.hits} van ${ai.asked} vragen aan bij “${product}”${wel}`, `AI recommends you in only ${ai.hits} of ${ai.asked} questions for “${product}”${does}`);
    else add(0, 'bad', `AI raadt je niet aan bij “${product}” (0 van ${ai.asked} vragen)${wel}`, `AI does not recommend you for “${product}” (0 of ${ai.asked} questions)${does}`);
  }
  if (s) {
    if (s.aiBlocked.length) add(0, 'bad', `Je site blokkeert AI-zoekmachines (${s.aiBlocked.slice(0, 3).join(', ')})`, `Your site blocks AI search engines (${s.aiBlocked.slice(0, 3).join(', ')})`);
    else add(0, 'ok', 'AI-zoekmachines mogen je site lezen', 'AI search engines may read your site');
    add(0, s.schema ? 'ok' : 'bad', s.schema ? 'Bedrijfsgegevens staan gestructureerd voor Google en AI' : 'Geen gestructureerde bedrijfsgegevens (schema) voor Google en AI', s.schema ? 'Company data is structured for Google and AI' : 'No structured company data (schema) for Google and AI');
    if (!s.description) add(0, 'warn', 'Geen zoekomschrijving: Google en AI bedenken zelf wat je doet', 'No meta description: Google and AI guess what you do');
  }
  if (psi) {
    const st = psi.score >= 90 ? 'ok' : psi.score >= 50 ? 'warn' : 'bad';
    add(1, st, `Snelheid op mobiel: ${psi.score}/100${psi.lcp ? ' (hoofdbeeld na ' + psi.lcp.replace(/ /g, ' ') + ')' : ''}`, `Mobile speed: ${psi.score}/100${psi.lcp ? ' (main content after ' + psi.lcp.replace(/ /g, ' ') + ')' : ''}`);
  } else if (s && s.ttfb > 1500) add(1, 'warn', `Je server reageert traag (${(s.ttfb / 1000).toFixed(1)} s)`, `Your server responds slowly (${(s.ttfb / 1000).toFixed(1)} s)`);
  if (s) {
    if (!s.mobile) add(1, 'bad', 'Je site is niet ingericht voor mobiel', 'Your site is not set up for mobile');
    if (s.quoteForm) add(1, 'ok', 'Offerte aanvragen kan direct op je site', 'Buyers can request a quote on your site');
    else if (!s.portal) add(1, 'bad', 'Geen offerteformulier gevonden op je homepage', 'No quote form found on your homepage');   /* webshop: bestellen gaat online, dan is een offerteformulier geen gemis */
    if (!s.whatsapp) add(1, 'warn', 'Geen WhatsApp: inkopers kunnen je niet even snel appen', 'No WhatsApp: buyers cannot quickly message you');
    if (!s.tracking) add(1, 'bad', 'Geen meetcode: je ziet niet wat je site oplevert', 'No analytics: you cannot see what your site brings in');
    add(2, s.portal ? 'ok' : 'bad', s.portal ? 'Er is een online bestelomgeving' : 'Geen online bestelomgeving: orders komen via mail en telefoon', s.portal ? 'There is an online ordering environment' : 'No online ordering: orders come in by email and phone');
  }
  return F;
}

function json(o, status) { return new Response(JSON.stringify(o), { status: status || 200, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } }); }
