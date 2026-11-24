// 離線快取：資料檔（data/*.json、config/*.json）與 Leaflet 先用裝置上的版本立刻顯示，
// 超過 10 分鐘就在背景向伺服器確認（有 ETag，沒變只回 304）；內容換了就通知網頁重畫（tlm-data 訊息）；網頁本身（index.html）先抓網路、3 秒沒回應或離線才用快取。
const CACHE = 'tlm-v1', TILES = 'tlm-tiles-v1', MAX_TILES = 3000, MAX_AGE = 10 * 60e3;
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (k !== CACHE && k !== TILES) await caches.delete(k);
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
const ver = r => r.headers.get('etag') || r.headers.get('last-modified') || '';
// 有 ETag／Last-Modified 就比對它，沒有就比對內容大小
async function changed(oldRes, newRes) {
  const a = ver(oldRes), b = ver(newRes);
  if (a && b) return a !== b;
  return (await oldRes.blob()).size !== (await newRes.blob()).size;
}
async function dataFirst(e) {
  const c = await caches.open(CACHE), hit = await c.match(e.request);
  if (hit) {
    if (Date.now() - (+hit.headers.get('sw-time') || 0) > MAX_AGE) {
      const old = hit.clone();
      e.waitUntil(refresh(e.request, c).then(async res => {
        if (!res.ok || !(await changed(old, res.clone()))) return;
        for (const cl of await self.clients.matchAll({ type: 'window' })) cl.postMessage({ type: 'tlm-data', url: new URL(e.request.url).pathname });
      }).catch(() => {}));
    }
    return hit;
  }
  return refresh(e.request, c);
}
async function pageFirst(e) {
  const c = await caches.open(CACHE);
  try {
    const res = await Promise.race([fetch(e.request.url, { cache: 'no-cache', credentials: 'same-origin' }), new Promise((_, rej) => setTimeout(() => rej(new Error('slow')), 3000))]);
    if (res.ok) e.waitUntil(stamp(res.clone()).then(r => c.put(e.request, r)));
    return res;
  } catch (err) {
    const hit = await c.match(e.request);
    if (hit) return hit;
    return fetch(e.request);
  }
}
// 地圖底圖：看過的圖磚存在裝置上（最多約 3,000 張，超過就刪最舊的），再打開不用重抓
let tileN = 0;
async function tileFirst(e) {
  const c = await caches.open(TILES), hit = await c.match(e.request);
  if (hit) return hit;
  const res = await fetch(e.request);
  if (res.ok || res.type === 'opaque') {
    e.waitUntil((async () => {
      await c.put(e.request, res.clone());
      if (++tileN % 200 === 0) { const ks = await c.keys(); for (const k of ks.slice(0, Math.max(0, ks.length - MAX_TILES))) await c.delete(k); }
    })());
  }
  return res;
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
  if (u.hostname === 'tile.openstreetmap.org') return e.respondWith(tileFirst(e));
});
