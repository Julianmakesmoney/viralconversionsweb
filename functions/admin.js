export async function onRequest(context) {
  const url = new URL(context.request.url);
  const target = 'https://viralconversionsweb.onrender.com' + url.pathname + url.search;
  return fetch(new Request(target, {
    method: context.request.method,
    headers: context.request.headers,
    body: ['GET', 'HEAD'].includes(context.request.method) ? undefined : context.request.body,
    redirect: 'manual',
  }));
}
