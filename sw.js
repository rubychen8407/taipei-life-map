// 離線快取：資料檔（data/*.json、config/*.json）與 Leaflet 先用裝置上的版本立刻顯示，
// 超過 6 小時才在背景向伺服器更新；網頁本身（index.html）先抓網路、3 秒沒回應或離線才用快取。
const CACHE = 'tlm-v1', MAX_AGE = 6 * 3600e3;
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (k !== CACHE) await caches.delete(k);
  await self.clients.claim();
})()));
const stamp = async res => {
  const h = new Headers(res.headers); h.set('sw-time', String(Date.now()));
  return new Response(await res.blob(), { status: res.status, statusText: res.statusText, headers: h });
};
async function refresh(req, c) {
  const res = await fetch(req, { cache: 'no-cache' });
  if (res.ok) await c.put(req, await stamp(res.clone()));
  return res;
}
async function dataFirst(e) {
  const c = await caches.open(CACHE), hit = await c.match(e.request);
  if (hit) {
    if (Date.now() - (+hit.headers.get('sw-time') || 0) > MAX_AGE) e.waitUntil(refresh(e.request, c).catch(() => {}));
    return hit;
  }
  return refresh(e.request, c);
}
async function pageFirst(e) {
  const c = await caches.open(CACHE);
  try {
    const res = await Promise.race([fetch(e.request), new Promise((_, rej) => setTimeout(() => rej(new Error('slow')), 3000))]);
    if (res.ok) e.waitUntil(stamp(res.clone()).then(r => c.put(e.request, r)));
    return res;
  } catch (err) {
    const hit = await c.match(e.request);
    if (hit) return hit;
    return fetch(e.request);
  }
}
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  if (u.origin === location.origin) {
    if (/\/(data|config)\/[^/]+\.json$/.test(u.pathname)) return e.respondWith(dataFirst(e));
    if (e.request.mode === 'navigate') return e.respondWith(pageFirst(e));
    return;
  }
  if (u.hostname === 'unpkg.com' && /leaflet/.test(u.pathname)) return e.respondWith(dataFirst(e));
});
