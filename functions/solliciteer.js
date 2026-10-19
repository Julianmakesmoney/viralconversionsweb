// Cloudflare Pages Function — /solliciteer → het sollicitatieformulier van het salesportaal (Render /sales-apply).
// Stond vroeger als Netlify-redirect in netlify.toml; op Cloudflare moet het een functie zijn (externe 200-rewrite kan niet in _redirects).
export async function onRequest({ request }) {
  const url = new URL(request.url);
  const r = await fetch('https://viralconversionsweb.onrender.com/sales-apply' + url.search, { headers: { 'Accept': 'text/html' }, redirect: 'follow' });
  return new Response(r.body, { status: r.status, headers: { 'Content-Type': r.headers.get('Content-Type') || 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } });
}
