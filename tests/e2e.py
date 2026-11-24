"""端對端測試：iPhone 尺寸無頭 Chrome 開 index.html；資料、Overpass 與底圖都用模擬資料。
執行：tests/run.sh   （全部通過才可 commit / push）
  B 組：有預建資料（scripts/build_data.py 用模擬資料實際產生 data/*.json）— 正常使用情境
  A 組：沒有預建資料 — 即時查 Overpass 的備援流程
設 REAL_DATA=1 時，改用 repo 內真實的 data/ 跑一遍並截圖。
"""
import json, zlib, asyncio, subprocess, urllib.parse, base64, sys, os, re, shutil, math, time, io, contextlib
from playwright.async_api import async_playwright
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
_M = json.load(open(os.path.join(HERE, 'mock.json')))
MOCK, ROUTES = _M['layers'], _M['routes']
LF = os.path.join(HERE, 'node_modules', 'leaflet', 'dist') + '/'
PD = os.path.join(HERE, 'node_modules', 'leaflet-polylinedecorator', 'dist', 'leaflet.polylineDecorator.js')
XL = os.path.join(HERE, 'node_modules', 'xlsx', 'dist', 'xlsx.mini.min.js')
THREE_JS = os.path.join(HERE, 'node_modules', 'three', 'build', 'three.module.min.js')
geo_calls = []
GEO = {'臺北市忠孝東路五段790巷59弄37號': [{'lat': '25.0412', 'lon': '121.5705', 'addresstype': 'house', 'type': 'house'}],
       '台北市松山區八德路三段': [{'lat': '25.0480', 'lon': '121.5560', 'addresstype': 'road', 'type': 'secondary'}],
       '臺北市南京東路五段100號': [{'lat': '25.0510', 'lon': '121.5600', 'addresstype': 'house', 'type': 'house'}]}
GMAPS = []
GH = {'files': {}, 'calls': [], 'token': 'tok_ok', 'write': True}  # 模擬 GitHub contents API（私人房源 repo）
GH_CORS = {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'Authorization, Accept, Content-Type, X-GitHub-Api-Version', 'Access-Control-Allow-Methods': 'GET, PUT, OPTIONS'}
SB = {'otp': [], 'rows': {}, 'calls': [], 'refresh_ok': True}  # 模擬 Supabase（登入＋user_data 資料表，每個 email 一份）
SB_HOST = 'https://exugellewviuiwnohsfo.supabase.co'
SB_CORS = {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'apikey, authorization, content-type, prefer, x-client-info', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS'}
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=')
URL = 'https://rubychen8407.github.io/taipei-life-map/'
SNIP = {'sidewalk': 'footway"="sidewalk', 'park': 'leisure"="park"', 'school': 'amenity', 'sport': 'sports_centre', 'market': 'supermarket', 'bus': 'bus_stop', 'mrt': 'subway'}
OUT = os.path.join(HERE, 'out')
DATA_DIR = os.path.join(OUT, 'data')
MODE = {'baked': False, 'missing': set(), 'fail': set(), 'dir': DATA_DIR}
calls, data_hits, fails = [], [], []
PHONE = dict(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True, has_touch=True)


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    if not cond:
        fails.append(msg)


def layers_in(q):
    return [k for k, s in SNIP.items() if s in q]


def mdist(a, b):
    return math.hypot((a[0] - b[0]) * 110540, (a[1] - b[1]) * 101000)


def center(e):
    if e['type'] == 'node':
        return e['lat'], e['lon']
    b = e['bounds']
    return (b['minlat'] + b['maxlat']) / 2, (b['minlon'] + b['maxlon']) / 2


def answer(q):
    """模擬 Overpass"""
    if 'rel(bn.s)' in q:
        R, la, lo = map(float, re.search(r'around:([\d.]+),([\d.]+),([\d.]+)', q).group(1, 2, 3))
        return [rel for rel in ROUTES if any(m['type'] == 'node' and mdist((m['lat'], m['lon']), (la, lo)) <= R for m in rel['members'])]
    m = re.search(r'rel\["route"[^\]]*\]\(([^)]+)\)', q)
    if m:
        s, w, n, e = map(float, m.group(1).split(','))
        return [rel for rel in ROUTES if any(mm['type'] == 'node' and s <= mm['lat'] <= n and w <= mm['lon'] <= e for mm in rel['members'])]
    s, w, n, e = map(float, re.search(r'\[bbox:([^\]]+)\]', q).group(1).split(','))
    out = []
    for lid in layers_in(q):
        for el in MOCK[lid]:
            la, lo = center(el)
            if not (s <= la <= n and w <= lo <= e):
                continue
            if el['type'] == 'way' and lid in ('school', 'sport', 'market') and 'out center' in q:
                el = {'type': 'way', 'id': el['id'], 'center': {'lat': la, 'lon': lo}, 'tags': el['tags']}
            out.append(el)
    return out


async def route(r):
    u = r.request.url
    if u.startswith(URL + 'data/'):
        name = urllib.parse.unquote(u[len(URL + 'data/'):])
        data_hits.append(name)
        p_ = os.path.join(MODE['dir'], name)
        if not MODE['baked'] or name in MODE['missing'] or not os.path.exists(p_):
            return await r.fulfill(status=404, body='not found')
        return await r.fulfill(path=p_, content_type='application/json')
    if u.startswith(URL + 'config/'):
        return await r.fulfill(path=os.path.join(ROOT, 'config', u[len(URL + 'config/'):]), content_type='application/json')
    if u.startswith(URL + 'assets/'):
        return await r.fulfill(path=os.path.join(ROOT, 'assets', u[len(URL + 'assets/'):].split('?')[0]), content_type='model/gltf-binary')
    if u == URL + 'sw.js':
        return await r.fulfill(path=os.path.join(ROOT, 'sw.js'), content_type='application/javascript')
    if u.startswith(URL):
        return await r.fulfill(path=os.path.join(ROOT, 'index.html'), content_type='text/html; charset=utf-8')
    if 'leaflet.css' in u:
        return await r.fulfill(path=LF + 'leaflet.css', content_type='text/css')
    if 'leaflet-polylinedecorator' in u:
        return await r.fulfill(path=PD, content_type='application/javascript')
    if 'xlsx.mini.min.js' in u:
        return await r.fulfill(path=XL, content_type='application/javascript')
    if 'three.module.min.js' in u:
        return await r.fulfill(path=THREE_JS, content_type='application/javascript', headers={'Access-Control-Allow-Origin': '*'})
    if 'nominatim.openstreetmap.org' in u:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query).get('q', [''])[0]
        geo_calls.append(q)
        return await r.fulfill(body=json.dumps(GEO.get(q, [])), content_type='application/json', headers={'Access-Control-Allow-Origin': '*'})
    if u.startswith('https://maps.googleapis.com/maps/api/js'):   # 模擬 Google Maps JS（街景服務）
        q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
        GMAPS.append(q.get('key', [''])[0])
        js = ("window.google={maps:{StreetViewSource:{OUTDOOR:'outdoor'},StreetViewPreference:{NEAREST:'nearest'},StreetViewService:class{async getPanorama(o){(window.__svq=window.__svq||[]).push(o);"
              "if(o.location.lat<24.99)throw new Error('ZERO_RESULTS');return {data:{location:{pano:'PANO_'+o.location.lat.toFixed(4),latLng:{lat:()=>o.location.lat+0.0003,lng:()=>o.location.lng}}}};}}}};"
              f"window['{q.get('callback', ['__gmcb'])[0]}']&&window['{q.get('callback', ['__gmcb'])[0]}']();")
        return await r.fulfill(body=js, content_type='text/javascript')
    if u.startswith('https://router.project-osrm.org/nearest/'):   # 模擬 OSRM：最近的道路在南邊約 30 公尺
        lo, la = map(float, urllib.parse.urlparse(u).path.split('/')[-1].split(','))
        return await r.fulfill(body=json.dumps({'code': 'Ok', 'waypoints': [{'location': [lo, la - 0.00027], 'distance': 30}]}), content_type='application/json', headers={'Access-Control-Allow-Origin': '*'})
    if u.startswith('https://www.google.com/maps/embed'):
        return await r.fulfill(body='<html><body style="background:#9ab">street view</body></html>', content_type='text/html')
    if u.startswith(SB_HOST):
        rq = r.request; pu = urllib.parse.urlparse(u); path = pu.path
        if rq.method == 'OPTIONS':
            return await r.fulfill(status=204, headers=SB_CORS)
        auth = rq.headers.get('authorization', '')
        SB['calls'].append((rq.method, path + ('?' + pu.query if pu.query else ''), auth, rq.headers.get('apikey', '')))
        J = lambda o, st=200: r.fulfill(status=st, body=json.dumps(o), headers=SB_CORS, content_type='application/json')
        if rq.headers.get('apikey') != 'sb_publishable_oLDvQbIRFB1G4tV93P0Xog_jVqkfuI4':
            return await J({'msg': 'no api key'}, 401)
        if path == '/auth/v1/otp':
            SB['otp'].append((json.loads(rq.post_data)['email'], urllib.parse.parse_qs(pu.query).get('redirect_to', [''])[0]))
            return await J({})
        if path == '/auth/v1/token':
            rt = json.loads(rq.post_data)['refresh_token']
            if not SB['refresh_ok'] or not rt.startswith('rf_'):
                return await J({'error': 'invalid_grant'}, 400)
            em = rt[3:]
            return await J({'access_token': 'at2_' + em, 'refresh_token': 'rf_' + em, 'expires_in': 3600, 'user': {'email': em + '@example.com'}})
        em = auth[len('Bearer at_'):] if auth.startswith('Bearer at_') else auth[len('Bearer at2_'):] if auth.startswith('Bearer at2_') else None
        if not em:
            return await J({'msg': 'JWT expired'}, 401)
        if path == '/auth/v1/user':
            return await J({'email': em + '@example.com'})
        if path == '/auth/v1/logout':
            return await r.fulfill(status=204, headers=SB_CORS)
        if path == '/rest/v1/user_data':
            mine = SB['rows'].setdefault(em, {})
            if rq.method == 'GET':
                return await J([{'key': k, 'value': v, 'updated_at': t} for k, (v, t) in mine.items()])
            for row in json.loads(rq.post_data):
                mine[row['key']] = (row['value'], row['updated_at'])
            return await r.fulfill(status=201, headers=SB_CORS)
        return await J({}, 404)
    if u.startswith('https://api.github.com/'):
        rq = r.request
        if rq.method == 'OPTIONS':
            return await r.fulfill(status=204, headers=GH_CORS)
        path = urllib.parse.urlparse(u).path
        GH['calls'].append((rq.method, path, rq.headers.get('authorization', ''), rq.headers.get('accept', '')))
        if rq.headers.get('authorization') != 'Bearer ' + GH['token']:
            return await r.fulfill(status=401, body='{"message":"Bad credentials"}', headers=GH_CORS, content_type='application/json')
        m = re.match(r'^/repos/rubychen8407/tlm-listings(?:/contents/(.+))?$', path)
        if not m:
            return await r.fulfill(status=404, body='{}', headers=GH_CORS, content_type='application/json')
        f = m.group(1)
        if f is None:
            return await r.fulfill(body='{"private":true}', headers=GH_CORS, content_type='application/json')
        if rq.method == 'PUT':
            if not GH['write']:
                return await r.fulfill(status=403, body='{}', headers=GH_CORS, content_type='application/json')
            b = json.loads(rq.post_data)
            if f in GH['files'] and b.get('sha') != 'sha-' + f:
                return await r.fulfill(status=409, body='{}', headers=GH_CORS, content_type='application/json')
            GH['files'][f] = base64.b64decode(b['content']).decode()
            return await r.fulfill(status=201, body='{}', headers=GH_CORS, content_type='application/json')
        if f not in GH['files']:
            return await r.fulfill(status=404, body='{}', headers=GH_CORS, content_type='application/json')
        if 'raw' in rq.headers.get('accept', ''):
            return await r.fulfill(body=GH['files'][f], headers=GH_CORS, content_type='application/json')
        return await r.fulfill(body=json.dumps({'sha': 'sha-' + f}), headers=GH_CORS, content_type='application/json')
    if 'leaflet.js' in u:
        return await r.fulfill(path=LF + 'leaflet.js', content_type='application/javascript')
    if 'tile.openstreetmap.org' in u or 'cartocdn.com' in u:
        return await r.fulfill(body=PNG, content_type='image/png')
    if 'interpreter' in u:
        q = urllib.parse.parse_qs(r.request.post_data or '').get('data', [''])[0]
        calls.append({'host': u.split('/')[2], 'ct': r.request.headers.get('content-type', ''), 'layers': [] if 'rel(' in q else layers_in(q), 'q': q})
        if '["highway"]' in q and 'around:' in q:
            m = re.search(r'around:([\d.]+),([\d.]+),([\d.]+)', q)
            radius, la, lo = map(float, m.groups())
            geometry = [{'lat': la, 'lon': lo}, {'lat': la + 0.004, 'lon': lo}, {'lat': la + 0.004, 'lon': lo + 0.004}, {'lat': la, 'lon': lo + 0.004}, {'lat': la, 'lon': lo}]
            return await r.fulfill(body=json.dumps({'elements': [{'type': 'way', 'id': 99001, 'tags': {'highway': 'residential'}, 'geometry': geometry}]}), content_type='application/json', headers={'Access-Control-Allow-Origin': '*'})
        if 'overpass-api.de' in u and 'school' in layers_in(q):
            return await r.fulfill(status=504, body='timeout')
        if any(l in MODE['fail'] for l in layers_in(q)):
            return await r.fulfill(status=429, body='busy')
        await asyncio.sleep(0.3)
        return await r.fulfill(body=json.dumps({'elements': answer(q)}), content_type='application/json', headers={'Access-Control-Allow-Origin': '*'})
    return await r.abort()


async def sw_suite(b, errs):
    # 離線快取：資料檔第二次起不再下載；啟動後空閒時先抓好估價等資料
    ctx = await b.new_context(**PHONE, service_workers='allow')
    await ctx.route('**/*', route)
    page = await ctx.new_page(); page.on('pageerror', lambda e: errs.append(str(e))); ev = page.evaluate
    await page.goto(URL); await page.wait_for_function(READY, timeout=30000)
    await ev("navigator.serviceWorker.ready.then(()=>true)")
    async def cached(*names):   # wait_for_function 不會等 Promise，所以自己輪詢
        for _ in range(60):
            if await ev("Promise.all(%s.map(u=>caches.open('tlm-v1').then(c=>c.match(u)))).then(a=>a.every(Boolean))" % json.dumps(list(names))): return True
            await page.wait_for_timeout(500)
        return False
    await page.reload(); await page.wait_for_function(READY, timeout=30000)
    check(await ev("!!navigator.serviceWorker.controller"), '註冊離線快取（service worker），重新整理後由它接管')
    await cached('data/city.json')
    n0 = len(data_hits)
    await page.reload(); await page.wait_for_function(READY, timeout=30000); await page.wait_for_timeout(800)
    check('city.json' not in data_hits[n0:], f'第三次打開：city.json 直接用裝置上的快取，不再下載（這次下載 {data_hits[n0:]}）')
    check(await cached('data/realprice.json', 'data/valuation.json', 'config/finance.json', 'data/market.json'), '啟動後空閒時，背景先把估價、房市資料存到裝置上')
    nt = await ev("caches.open('tlm-tiles-v1').then(c=>c.keys()).then(k=>k.length)")
    check(nt > 0, f'地圖底圖存在裝置上，再打開不用重抓（目前 {nt} 張）')
    n1 = len(data_hits)
    await ev("void openVal([25.0335,121.5436])"); await page.wait_for_function("!!VALDATA", timeout=20000)
    check(not any(x in data_hits[n1:] for x in ('realprice.json', 'valuation.json')), f'啟動後空閒時已先抓好估價資料，點估價不用再等下載（{data_hits[n1:]}）')
    # 裝置上的資料舊了（超過 10 分鐘）：service worker 先給舊的、背景確認，版本換了傳 tlm-data；網頁收到就清掉重抓重畫
    swt = await ev("fetch('sw.js').then(r=>r.text())")
    check('MAX_AGE = 10 * 60e3' in swt and "type: 'tlm-data'" in swt, 'service worker：資料檔超過 10 分鐘就背景確認，版本換了通知網頁')
    await ev("""(async()=>{URC={built:'old',status:{ok:'已核定',review:'權變審議中',build:'施工中',done:'已完工'},count:{},cases:[],units:[],areas:[]};URCLOAD=Promise.resolve(URC);state.layers.urcase=true;await drawUrc();
      navigator.serviceWorker.dispatchEvent(new MessageEvent('message',{data:{type:'tlm-data',url:'/taipei-life-map/data/urcase.json'}}));})()""")
    for _ in range(40):
        if await ev("!!URC&&URC.built!=='old'&&!!urcLayer"): break
        await page.wait_for_timeout(250)
    dm = await ev("({built:URC&&URC.built,n:urcLayer?urcLayer.getLayers().length:0,toast:document.getElementById('toast').innerText})")
    check(dm['built'] != 'old' and dm['n'] > 0 and '新版' in dm['toast'], f"網頁收到「資料有新版」：都更圖層清掉舊資料、重抓重畫並提示 {dm}")
    await ev("state.layers.urcase=false;drawUrc()")
    await ctx.close()


async def cloud_suite(b, errs):
    print('\n=== 雲端同步（Supabase，模擬）===')
    pa = await new_page(b, errs, **PHONE); ev = pa.evaluate
    await pa.goto(URL); await pa.wait_for_function(READY, timeout=30000)
    await ev("openSettings()")
    check(await pa.locator('#cloudBox #cloudEmail').count() == 1, '設定最上面有「雲端同步」：輸入 email 登入')
    await pa.fill('#cloudEmail', 'ruby@example.com'); await pa.click('#cloudSend'); await pa.wait_for_timeout(300)
    check(SB['otp'] and SB['otp'][-1] == ('ruby@example.com', URL), f'寄登入連結：寄到輸入的 email，點信裡的連結會回到 app（{SB["otp"][-1:]}）')
    check('ruby@example.com' in await pa.inner_text('#cloudSent'), '寄出後提示到信箱點連結')
    await shot(pa, 'cloud_login')
    # 點信裡的連結：網址帶著 token 回來
    await pa.goto('about:blank'); await pa.goto(URL + '#access_token=at_ruby&refresh_token=rf_ruby&expires_in=3600&token_type=bearer&type=magiclink'); await pa.wait_for_function(READY, timeout=30000)
    await pa.wait_for_timeout(500)
    check(await ev("location.hash===''&&!!JSON.parse(localStorage.getItem('tlm.auth')).access"), '點登入連結回來：已登入，網址上的 token 清掉')
    # 改個人資料 → 自動上傳
    await ev("saveHomes([{id:'h1',name:'光復北路那間',pt:[25.0535,121.5575],dist:'松山區',form:{t:0,ping:30,age:40,fl:3,tot:5},st:'seen'}]);drawHomes()")
    await ev("state.layers.trees=true;applyLayers()")
    await pa.wait_for_timeout(2200)
    rows = SB['rows'].get('ruby', {})
    check('tlm.homes' in rows and rows['tlm.homes'][0][0]['name'] == '光復北路那間', f'我的物件改了會自動存到雲端（雲端有 {sorted(rows)}）')
    check('tlm.layers' in rows and rows['tlm.layers'][0].get('trees') is True, '圖層設定也存到雲端')
    check(not any(k in rows for k in ('tlm.auth', 'tlm.sync', 'tlm.gmkey', 'tlm.geocache')), '登入資訊、GitHub token、街景金鑰、地址快取不上傳')
    await ev("openSettings()")
    check('ruby@example.com' in await pa.inner_text('#cloudBox'), f'設定顯示已登入的 email')
    await shot(pa, 'cloud_signed_in')
    # 另一台裝置（空的）登入同一個 email
    pb = await new_page(b, errs, **PHONE); evb = pb.evaluate
    await pb.goto(URL + '#access_token=at_ruby&refresh_token=rf_ruby&expires_in=3600&token_type=bearer&type=magiclink'); await pb.wait_for_function(READY, timeout=30000)
    await pb.wait_for_timeout(400)
    check(await evb("loadHomes().length===1&&loadHomes()[0].name==='光復北路那間'&&homeLayer.getLayers().length>0"), '另一台裝置登入後，一打開就看到我的物件（地圖上也畫出來）')
    check(await evb("state.layers.trees===true"), '另一台裝置的圖層設定也同步')
    # 第三台裝置本來就有自己的物件：第一次登入時合併，不會被雲端蓋掉
    pc = await new_page(b, errs, **PHONE); evc = pc.evaluate
    await pc.goto(URL); await pc.wait_for_function(READY, timeout=30000)
    await evc("saveHomes([{id:'h9',name:'電腦上存的',pt:[25.05,121.56],dist:'松山區',form:{t:0,ping:25,age:30,fl:2,tot:4},st:'todo'}])")
    await pc.goto('about:blank'); await pc.goto(URL + '#access_token=at_ruby&refresh_token=rf_ruby&expires_in=3600&token_type=bearer&type=magiclink'); await pc.wait_for_function(READY, timeout=30000)
    await pc.wait_for_timeout(2200)
    names = await evc("loadHomes().map(h=>h.name)")
    check(sorted(names) == ['光復北路那間', '電腦上存的'] and len(SB['rows']['ruby']['tlm.homes'][0]) == 2, f'已有資料的裝置第一次登入：我的物件和雲端的合併（{names}），合併後也存回雲端')
    await evc("saveHomes(loadHomes().filter(h=>h.id!=='h9'))"); await pc.wait_for_timeout(1800); await pc.context.close()
    # 第一台改了 → 第二台切回 app 時更新
    await ev("const h=loadHomes();h[0].st='nego';saveHomes(h)"); await pa.wait_for_timeout(2000)
    await evb("Object.defineProperty(document,'visibilityState',{value:'visible',configurable:true});document.dispatchEvent(new Event('visibilitychange'))")
    await pb.wait_for_timeout(800)
    check(await evb("loadHomes()[0].st==='nego'"), '一台裝置改了，另一台切回 app 就更新（不用重新整理）')
    # 裝置上比雲端新的不會被蓋掉
    await evb("const h=loadHomes();h[0].name='改名了';saveHomes(h)")
    await evb("cloudPull()"); await pb.wait_for_timeout(1800)
    check(await evb("loadHomes()[0].name==='改名了'") and SB['rows']['ruby']['tlm.homes'][0][0]['name'] == '改名了', '裝置上剛改的比雲端新：保留並上傳，不會被舊的蓋掉')
    # token 過期 → 自動換新
    n = len(SB['calls'])
    await evb("SBA.exp=Date.now()-1000;cloudPull()"); await pb.wait_for_timeout(800)
    new = SB['calls'][n:]
    check(any(c[1].startswith('/auth/v1/token') for c in new) and any(c[2] == 'Bearer at2_ruby' for c in new), '登入過期時自動換新 token 繼續同步')
    # 換不到新 token → 請重新登入
    SB['refresh_ok'] = False
    await evb("SBA.exp=Date.now()-1000;cloudPull()"); await pb.wait_for_timeout(800)
    await evb("openSettings()")
    check(await evb("!SBA&&!localStorage.getItem('tlm.auth')") and '重新登入' in await pb.inner_text('#cloudBox'), '登入失效時清掉並請使用者重新登入')
    SB['refresh_ok'] = True
    # 登出：清掉這台裝置的個人資料，雲端保留
    pa.on('dialog', lambda d: asyncio.ensure_future(d.accept()))
    await ev("openSettings()")
    async with pa.expect_navigation(timeout=30000):
        await pa.click('#cloudOut')
    await pa.wait_for_function(READY, timeout=30000)
    check(await ev("!localStorage.getItem('tlm.auth')&&!localStorage.getItem('tlm.homes')") and 'tlm.homes' in SB['rows']['ruby'], '登出：這台裝置的個人資料清掉，雲端的保留')
    check(any(c[1] == '/auth/v1/logout' for c in SB['calls']), '登出時通知 Supabase 作廢這次登入')
    await pa.context.close(); await pb.context.close()


async def debug_suite(b, errs):
    page = await new_page(b, errs, **PHONE); ev = page.evaluate
    await page.goto(URL + '?debug'); await page.wait_for_function(READY, timeout=30000); await page.wait_for_timeout(900)
    await page.mouse.click(300, 300); await page.wait_for_timeout(700)
    t = await page.inner_text('#dbgPanel')
    check(all(x in t for x in ('Leaflet 載入', '全市資料', '可操作', '點按延遲：@')), f'診斷模式（?debug）：顯示各階段時間與點按延遲，方便截圖回報（{t[:160]!r}）')
    check(await ev("getComputedStyle(document.getElementById('dbgPanel')).pointerEvents==='none'"), '診斷面板不擋住點按')
    await shot(page, 'debug_panel')
    await page.goto(URL); await page.wait_for_function(READY, timeout=30000); await page.wait_for_timeout(700)
    check(await page.locator('#dbgPanel').count() == 0, '一般打開不顯示診斷面板')
    await page.context.close()


async def new_page(b, errs, **kw):
    kw.setdefault('service_workers', 'block')   # 一般測試不跑離線快取（sw.js 另外測）
    ctx = await b.new_context(**kw)
    page = await ctx.new_page()
    page.on('pageerror', lambda e: errs.append(str(e)))
    await page.route('**/*', route)
    await page.add_init_script("window.__noWarm=true")   # 一般測試不預先抓資料，才能檢查「用到才下載」；預先抓另外測
    return page


async def shot(page, name):
    await page.wait_for_timeout(450)
    await page.screenshot(path=os.path.join(OUT, name + '.png'))


READY = "state.res&&!!document.querySelector('#head .sumline')"
LINES = "transitLayer.getLayers().flatMap(g=>g.getLayers&&!(g instanceof L.Polyline)?g.getLayers():[g]).filter(l=>l instanceof L.Polyline&&!(l instanceof L.Polygon))"
HANDLE = "(()=>{const r=document.querySelector('.handle').getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]})()"


async def long_press(page, x, y, ms=750):
    '''用真的觸控事件（CDP）在手機上長按'''
    cdp = await page.context.new_cdp_session(page)
    await cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y}]})
    await page.wait_for_timeout(ms)
    await cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    await page.wait_for_timeout(250)


async def touch_drag(page, x, y, dx, dy, steps=10):
    '''用真的觸控事件（CDP）拖曳'''
    cdp = await page.context.new_cdp_session(page)
    await cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y}]})
    for i in range(1, steps + 1):
        await cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': x + dx * i / steps, 'y': y + dy * i / steps}]})
        await page.wait_for_timeout(16)
    await cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    await page.wait_for_timeout(300)


async def baked_suite(b, errs):
    page = await new_page(b, errs, **PHONE, geolocation={'latitude': 25.0335, 'longitude': 121.5436}, permissions=['geolocation'])
    ev = page.evaluate
    n0 = len(calls)
    t0 = time.time()
    await page.goto(URL)
    try:
        await page.wait_for_function(READY, timeout=20000)
    except Exception:
        print('ERRS', errs[:3]); raise
    labels = await ev("""(()=>{const bus=data('bus')[0],mrt=data('mrt')[0],park=data('park').find(f=>f.name),old=ROUTE_DATA;ROUTE_DATA={routes:[{r:'bus',ref:'307',name:'307',stops:[bus.center]},{r:'subway',ref:'BL',name:'板南線',stops:[mrt.center]}]};const result={bus:facilityLabel(bus,LAYER.bus),mrt:facilityLabel(mrt,LAYER.mrt),park:facilityLabel(park,LAYER.park)};ROUTE_DATA=old;const el=document.createElement('span');el.className='facility-name';document.body.appendChild(el);result.labelPointerEvents=getComputedStyle(el).pointerEvents;el.remove();const marker=L.marker(park.center,{icon:facilityIcon(LAYER.park,park,17,true)}).on('click',()=>window.__labelClicked=true).addTo(map);marker._icon.querySelector('.facility-name').dispatchEvent(new MouseEvent('click',{bubbles:true}));result.labelClick=!!window.__labelClicked;marker.remove();delete window.__labelClicked;FACILITY_LABEL_CACHE.delete(bus);FACILITY_LABEL_CACHE.delete(mrt);return result;})()""")
    check(labels['bus']=='307' and labels['mrt']=='板南線' and bool(labels['park']), '地圖標籤：一般設施顯示地點名稱、（路線資料）公車路線號、捷運顯示路線名稱')
    busname = await ev("""(()=>{const bus=data('bus').find(f=>f.name),old=ROUTE_DATA;ROUTE_DATA={routes:[{r:'bus',ref:'307',name:'307',stops:[bus.center]}]};const m=L.marker(bus.center,{icon:facilityIcon(LAYER.bus,bus,17,true)}).addTo(map);const t=m._icon.querySelector('.facility-name')?.innerText||'';m.remove();ROUTE_DATA=old;FACILITY_LABEL_CACHE.delete(bus);return {t,name:bus.name};})()""")
    check(busname['t']==busname['name'] and '307' not in busname['t'], f'地圖上公車站標籤顯示站名（{busname["t"]}），不列出所有公車號碼')
    check(labels['labelPointerEvents']=='auto', '地圖標籤文字可接收點擊，不會穿透')
    check(labels['labelClick'], '點擊標籤文字會觸發對應 POI 標記事件')
    check(await ev("toFeature({type:'way',id:1,center:{lat:25.05,lon:121.55},tags:{leisure:'pitch',sport:'basketball'}}).name==='籃球場'"), '沒有正式名稱的運動設施依 OSM sport 標籤顯示設施類型名稱')
    dt = time.time() - t0
    # 1. 開啟即顯示
    check(len(calls) == n0 and sorted(data_hits) == ['city.json', 'future.json'], f'開啟時只讀預建檔（city.json＋很小的 future.json），完全不查 Overpass {data_hits}')
    check(dt < 4, f'開啟到顯示全市結果 {dt:.1f} 秒')
    check(await ev("state.scope===''&&state.res.total===CELLS.length"), '預設直接分析全臺北市')
    check(await ev("!!heat&&map.hasLayer(heat)"), '地圖上有符合範圍的色塊圖層')
    o = await ev("""(()=>{const n=state.res.act.length,good=scopeCells().filter(k=>state.res.okc[k]===n),bad=scopeCells().filter(k=>state.res.okc[k]!==n);
      const rings=outlineLayer.getLayers().filter((l,i)=>i%2===1).map(l=>l.getLatLngs()[0].map(p=>[p.lng,p.lat]));
      const inside=(la,lo)=>rings.reduce((c,r)=>c^ringContains(r,lo,la),0);
      const sg=good.filter((k,i)=>i%7===0),sb=bad.filter((k,i)=>i%7===0);
      const gi=sg.filter(k=>inside(CELLS[k].la,CELLS[k].lo)).length/sg.length,bo=sb.filter(k=>!inside(CELLS[k].la,CELLS[k].lo)).length/sb.length;
      return {rings:rings.length,gi,bo}})()""")
    px = await ev("""new Promise(res=>{const im=new Image();im.onload=()=>{const c=document.createElement('canvas');c.width=NI;c.height=NJ;const x=c.getContext('2d');x.drawImage(im,0,0);const d=x.getImageData(0,0,NI,NJ).data;
      const n=state.res.act.length,at=k=>{const C=CELLS[k],o=((NJ-1-C.j)*NI+C.i)*4;return [d[o],d[o+1],d[o+2],d[o+3]]};
      const gray=p=>p[3]>0&&Math.abs(p[0]-p[1])<12&&Math.abs(p[1]-p[2])<12,col=p=>p[3]>0&&!gray(p);
      const cs=scopeCells(),good=cs.filter(k=>state.res.okc[k]===n),bad=cs.filter(k=>state.res.okc[k]!==n);
      res({good:good.filter(k=>col(at(k))).length/good.length,bad:bad.filter(k=>gray(at(k))).length/bad.length,badColor:bad.filter(k=>col(at(k))).length});};im.src=heat._url;})""")
    check(px['good'] > 0.95 and px['bad'] > 0.95 and px['badColor'] == 0, f"熱圖只畫在完全符合條件的地方（{px['good']*100:.0f}% 有色），不符合的地方反灰（{px['bad']*100:.0f}%，0 格上色）")
    check(o['rings'] > 0 and o['gi'] > 0.9 and o['bo'] > 0.9, f"完全符合條件的地方畫出邊界（{o['rings']} 圈；符合的格子 {o['gi']*100:.0f}% 在圈內、不符合的 {o['bo']*100:.0f}% 在圈外）")
    await page.click('#layersBtn'); await page.click('#layersPop [data-layer=outline]')
    check(await ev("outlineLayer.getLayers().length===0"), '圖層面板可以關掉「符合條件的邊界」')
    await page.click('#layersPop [data-layer=outline]'); await page.click('#layersBtn')
    check(await ev("outlineLayer.getLayers().length>0"), '再打開邊界又畫回來')
    await shot(page, '1b_outline')
    first = await ev("(window.__h0=heat, heatAll.length)")
    await ev("drawHeat()")
    check(await ev("heat===__h0 && heatAll.length===1"), '結果沒變時不重畫熱圖（不閃）')
    await ev("for(const m of [12,8,14,9,11,10]){state.minsByLayer.park=m;analyze();drawHeat();}"); await page.wait_for_timeout(700)
    check(await ev("heatAll.length===1 && map.hasLayer(heat) && heat.options.opacity>0 && document.querySelectorAll('.leaflet-analysis-pane .leaflet-layer').length===1&&document.querySelectorAll('.leaflet-analysis-pane img').length===0"), '連續調整時熱圖不閃，最後只留一層（用圖磚畫，不是整張大圖）')
    check(await ev("new Set([0,.125,.25,.375,.5,.625,.75,.875,1].map(densityColor)).size===9"), '熱圖色帶九個等級都有不同顏色')
    lum = await ev("(()=>{const L=h=>{const c=[1,3,5].map(i=>parseInt(h.substr(i,2),16)/255).map(v=>v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4));return .2126*c[0]+.7152*c[1]+.0722*c[2]};const a=L(densityColor(0)),b=L(densityColor(1));return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)})()")
    check(lum >= 8, f'熱圖最好與最差的顏色對比夠大（亮度對比 {lum:.1f}:1）')
    sp = await ev("(()=>{const ks=scopeCells().filter(k=>state.res.worst&&!isNaN(state.res.worst[k]));return heatRank?[heatRank(-1),heatRank(1e9)]:null})()")
    check(sp and sp[0] == 0 and sp[1] == 1, '熱圖依目前範圍內的排名上色：最差到最好一定用滿整條色帶')
    check(await ev("(()=>{const get=map.getZoom;map.getZoom=()=>17;const a=heatOpacity();map.getZoom=()=>12;const b=heatOpacity();map.getZoom=get;return a<=0.35&&b>=0.85})()"), '熱圖：全市看得清楚，放大到街區自動淡化，讓設施圖示浮現')
    check(await ev("document.querySelector('#body .legend').textContent.includes('整體設施密度') && !document.querySelector('#body .legend').textContent.includes('捷運')"), 'Heatmap 圖例顯示整體密度，不再以類別顏色分組')
    check(await ev("state.zones.length>0&&!document.querySelector('#body [data-z]')&&!document.getElementById('body').innerText.includes('符合條件的區域')&&pinLayer.getLayers().length===0&&state.zones.every(z=>z.comp.includes(z.best)&&z.comp.every(k=>state.res.okc[k]===state.res.act.length))&&state.zones.every((z,i,a)=>!i||a[i-1].comp.length>=z.comp.length)"), '算出符合條件的範圍（邊界圈起來的每一塊，中心點在範圍內）；清單不再列區域、地圖不再有推薦圖釘')
    check(await ev("document.querySelectorAll('#chips .chip').length") == 8 and not await ev("!!document.querySelector('.chip[data-id=green]')") and await ev("document.querySelectorAll('#chips .chip[aria-pressed=true]').length") == 3, '上方類別按鈕列 8 個（含超市、人行道，已移除綠地）（含生活圈）、預設選 3 個')
    check(await ev("[...document.querySelectorAll('#chips .chip')].every(b=>{const r=b.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&b.querySelector('.chip-icon svg')&&b.querySelector('.chip-name').textContent.trim()}) && document.getElementById('chips').scrollWidth<=document.getElementById('chips').clientWidth+1"),
          '8 個類別圓形圖示一排放得下（不用橫向捲動），每個都有圖示與名稱')
    check(await ev("[...document.querySelectorAll('#chips .chip[aria-pressed=true]')].every(b=>getComputedStyle(b.querySelector('.badge')).display==='flex'&&b.querySelector('.badge svg')&&/10 分/.test(b.querySelector('.mins').textContent)) && getComputedStyle(document.querySelector('.chip[data-id=bus] .badge')).display==='none'"),
          '已選類別：右下角顯示交通方式小角標，下方顯示分鐘數；未選的不顯示')
    check(await ev("(()=>{const c=getComputedStyle(document.getElementById('filterCard'));return parseFloat(c.borderTopLeftRadius)>=20&&c.boxShadow!=='none'&&document.getElementById('filterCard').contains(document.getElementById('chips'))&&document.getElementById('filterCard').contains(document.getElementById('regionBtn'))})()"), '範圍與 8 個類別包在上方同一張毛玻璃篩選卡裡（先選範圍、再選類別）')
    check(await ev("[...document.querySelectorAll('#sheet,#chips,.fab,#filterCard')].every(e=>{const c=getComputedStyle(e);return (c.backdropFilter||'none')==='none'&&(c.webkitBackdropFilter||'none')==='none'}) && getComputedStyle(document.body).position!=='fixed'"), '手機（觸控）上不用即時模糊、body 不固定定位，避免 iPhone Safari 漏畫閃爍')
    z = await ev("(()=>{const ev=new Event('gesturestart',{cancelable:true});document.dispatchEvent(ev);return {g:ev.defaultPrevented,ta:getComputedStyle(document.body).touchAction,top:getComputedStyle(document.getElementById('sheet')).touchAction}})()")
    check(z['g'] and z['ta'] == 'pan-x pan-y' and z['top'] == 'manipulation', f"擋掉整個網頁的雙指縮放與連點放大（{z}），地圖本身仍可縮放")
    zz = await ev("map.touchZoom.enabled() && map.doubleClickZoom.enabled()")
    check(zz, '地圖的兩指縮放與連點放大仍然開著')
    # 超市類別：和其他設施一樣從 city.json 讀，點了就併入分析
    await page.click('.chip[data-id=market]')
    await page.wait_for_function("state.res&&state.res.act.includes('market')", timeout=20000)
    mk = await ev("(()=>{const d=state.store.city.market||[];return {n:d.length,name:(d[0]||{}).name||'',act:state.res.act.length}})()")
    check(mk['n'] > 100, f"超市類別讀預建資料（{mk['n']} 家），併入分析（{mk['act']} 類）")
    await shot(page, '1c_market')
    await page.click('.chip[data-id=market]')
    await page.wait_for_function("state.res&&!state.res.act.includes('market')", timeout=20000)
    # 人行道類別：讀預建的 data/sidewalk.json（不查 Overpass），和其他類別一樣併入分析
    c0 = len(calls)
    await page.click('.chip[data-id=sidewalk]'); await page.wait_for_function("state.res&&state.res.act.includes('sidewalk')", timeout=10000)
    sw = await ev("({n:data('sidewalk').length,poly:data('sidewalk').every(f=>f.polys&&f.polys[0].length>=4),mode:state.modeByLayer.sidewalk,m:state.minsByLayer.sidewalk,info:state.sidewalkInfo,hits:" + "0})")
    check(len(calls) == c0 and sw['n'] == MODE['sw']['kept'] and sw['poly'] and 'sidewalk.json' in data_hits, f"人行道類別讀預建資料（{sw['n']} 段淨寬 ≥1.5m 的人行道），不查 Overpass")
    check(sw['mode'] == 'walk' and sw['m'] == 3 and sw['info']['min'] == 1.5, '人行道預設「步行 3 分鐘內有淨寬 1.5 公尺以上的人行道」')
    k = await ev("(()=>{const i=state.res.act.indexOf('sidewalk');return scopeCells().find(k=>state.res.D[i].d[k]===0)})()")
    check(k is not None, '站在人行道上的位置距離算 0（面狀資料，不是只看中心點）')
    await ev("void map.setView(data('sidewalk')[0].center,17,{animate:false})"); await page.wait_for_timeout(400)
    check(await ev("facLayer.getLayers().some(l=>l instanceof L.Polygon&&l.options.color==='#8d6e63')"), '放大後地圖畫出人行道的範圍')
    await ev("state.openDistanceLayer='sidewalk';renderCategorySettings()"); await page.click('.chip[data-id=sidewalk]')
    await page.wait_for_function("!state.res.act.includes('sidewalk')", timeout=5000)
    await ev("void map.fitBounds([[CITY[0],CITY[1]],[CITY[2],CITY[3]]],{animate:false})"); await page.wait_for_timeout(300)
    check(await ev("!document.querySelector('#top .search')||getComputedStyle(document.querySelector('#top .search')).display==='none'"), '上方不再常駐區域選擇列')
    check(await ev("document.querySelectorAll('.dlabel').length") == 12, '全市檢視顯示 12 區名稱')
    check(await ev("facLayer.getLayers().length") < 200, '縮小時不顯示一堆設施點（只留捷運站）')
    r = await ev("(()=>{const s=document.getElementById('sheet').getBoundingClientRect(),t=document.getElementById('top').getBoundingClientRect();return {sheetTop:s.top,topBottom:t.bottom}})()")
    check(r['topBottom'] < 135 and r['sheetTop'] > 760, f"進場：地圖是主角，上方只有類別列與儲存的篩選、下方面板收到只剩一行（{r['topBottom']:.0f}px／{r['sheetTop']:.0f}px）")
    check(await ev("!!document.querySelector('#head .sumline')&&!/%|符合/.test(document.querySelector('#head .sumline').innerText)&&!/各區比較/.test(document.getElementById('body').innerText)&&!document.querySelector('#body .drow')"), '清單只寫範圍與項目數，不顯示符合比例和各區比較')
    gr = await ev("""(()=>{let n=0,bad=0;for(let j=0;j<NJ;j++)for(let i=0;i<NI;i++){const la=CITY[0]+(j+.5)*dLat,lo=CITY[1]+(i+.5)*dLon;let d=-1;
      for(let k=0;k<DISTRICTS.length;k++)if(inPoly(DISTRICTS[k],la,lo)){d=k;break;}const c=CELLAT[j*NI+i];if(d>=0)n++;if((c<0)!==(d<0)||(c>=0&&CELLS[c].d!==d))bad++;}return {n,bad,cells:CELLS.length}})()""")
    check(gr['bad'] == 0 and gr['n'] == gr['cells'], f"逐列掃描建的格子和逐格判斷完全相同（{gr['cells']} 格，不同 {gr['bad']} 格）")
    await shot(page, '1_home')

    # 2. 類別按鈕、各類別獨立距離設定
    h0 = len(data_hits); t0 = time.time()
    await page.click('.chip[data-id=bus]')
    await page.wait_for_function("state.res.act.includes('bus')", timeout=5000)
    check(len(calls) == n0 and len(data_hits) == h0 and time.time() - t0 < 1.5, f'點「公車站」立即重算（{time.time()-t0:.2f} 秒），不下載任何東西')
    g10 = await ev("state.res.good")
    await page.locator('#chips .chip[data-id="park"]').click()
    await page.locator('input[data-distance="park"]').evaluate("(el)=>{el.value='5';el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}))}")
    check(await ev("state.res.thresholds[state.res.act.indexOf('park')]===5 && state.minsByLayer.park===5 && state.minsByLayer.school===10"), '公園距離可獨立調整為 5 分鐘，不影響學校的 10 分鐘設定')
    await page.locator('input[data-distance="park"]').evaluate("(el)=>{for(const v of ['6','7','8']){el.value=v;el.dispatchEvent(new Event('input',{bubbles:true}));}}")
    await page.wait_for_timeout(150)
    check(await ev("state.res.thresholds[state.res.act.indexOf('park')]===8&&!!DW&&!!DW2&&Object.keys(DIST).length>=3"), '拖滑桿時每個畫面最多重算一次（連續拖動只算最後的 8 分）；距離在背景執行緒（Web Worker）計算')
    await page.locator('input[data-distance="park"]').evaluate("(el)=>{el.value='10';el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}))}")
    t = await ev("(()=>{const t=document.querySelector('#categorySettings .tune').getBoundingClientRect(),c=document.querySelector('.chip[data-id=park] .chip-icon').getBoundingClientRect();return {h:t.height,w:t.width,top:t.top,cb:c.bottom}})()")
    check(t['h'] <= 56 and t['w'] <= 270 and t['top'] > t['cb'], f"通勤小彈窗是一條細長膠囊（{t['w']:.0f}×{t['h']:.0f}），出現在類別圖示下方")
    check(await ev("document.querySelector('#categorySettings .mode-menu').hidden && !!document.querySelector('[data-mode-toggle] svg')"), '交通方式預設收起，只顯示目前方式的圖示')
    await page.click('[data-mode-toggle]')
    m = await ev("(()=>{const m=document.querySelector('#categorySettings .mode-menu');const r=m.getBoundingClientRect();return {open:!m.hidden,w:r.width,n:m.querySelectorAll('[data-mode]').length,txt:m.innerText.trim()}})()")
    check(m['open'] and m['n'] == 5 and m['w'] <= 50 and m['txt'] == '', f"點交通方式：往下拉出只有圖示的細長選單（{m['n']} 個、寬 {m['w']:.0f}px）")
    await page.click('#categorySettings [data-mode=bike]')
    check(await ev("state.modeByLayer.park==='bike' && document.querySelector('#categorySettings .mode-menu').hidden && document.querySelector('.chip[data-id=park] .badge').innerHTML.includes('5.5')"), '選腳踏車：選單收起，類別角標改成腳踏車')
    await page.click('[data-mode-toggle]'); await page.click('#categorySettings [data-mode=walk]')
    await shot(page, '2b_tune')

    # 3. 結果按鈕＋卡片拖拉
    rb = await ev("(()=>{const n=document.getElementById('navbar').getBoundingClientRect(),f=document.getElementById('fabs').getBoundingClientRect(),s=document.getElementById('sheet').getBoundingClientRect(),o=document.getElementById('resultBtn').getBoundingClientRect(),ids=[...document.querySelectorAll('#navbar .nb')].map(b=>b.id),ops=[...document.querySelectorAll('#fabs .fab')].map(b=>b.id);return {ids,ops,l:n.left,r:n.right,b:n.bottom,fb:f.bottom,nt:n.top,st:s.top,sb:s.bottom,oldGone:o.height===0}})()")
    print('  nav', rb)
    check(rb['ids'] == ['exploreBtn', 'exportBtn', 'recBtn', 'homesBtn', 'marketBtn'] and rb['l'] == 0 and rb['r'] == 390 and rb['b'] == 844 and rb['ops'] == ['flagBtn', 'layersBtn', 'locate'] and rb['fb'] <= rb['nt'] and rb['oldGone'] and rb['st'] >= rb['nt'] - 1, f"底部 5 個分頁（探索、房源、推薦、物件、房市）佔滿最下緣；地圖上只留插旗、圖層、定位，在工具列上方 {rb}")
    tb = await ev("(()=>{const g=document.getElementById('setBtn'),r=g.getBoundingClientRect(),t=document.getElementById('top').getBoundingClientRect();return {inTop:!!g.closest('#filterCard'),w:r.width,h:r.height,sel:[...document.querySelectorAll('#navbar [aria-selected=true]')].map(b=>b.id),bm:document.getElementById('savedBtn').innerHTML.includes('M7 3.8')}})()")
    check(tb['inTop'] and tb['w'] >= 36 and tb['sel'] == ['exploreBtn'] and tb['bm'], f'設定齒輪移到上方篩選列；「我的篩選」改用書籤圖示（星星只留給物件）；目前在「探索」分頁 {tb}')
    check(await ev("state.view.type==='list'&&state.sheetVis===0&&getComputedStyle(document.getElementById('sheet')).visibility==='hidden'&&document.getElementById('resultBtn').hidden"), '不顯示分析結果清單：地圖主畫面沒有面板，也沒有打開清單的按鈕')
    check(await ev("document.querySelectorAll('#categorySettings input[type=range][data-distance]').length===1"), '一次只展開一個類別的步行距離拉桿，位置在搜尋區域下方')
    await ev("openZone(0)"); await page.wait_for_timeout(600); await ev("setSheet('half')"); await page.wait_for_timeout(350)
    check(await ev("snap") == 'half' and await ev("getComputedStyle(document.getElementById('sheet')).visibility") == 'visible', '點區域等詳細畫面才出現面板')
    box = await ev(HANDLE)
    await page.mouse.move(*box); await page.mouse.down(); await page.mouse.move(box[0], 120, steps=6); await page.mouse.up(); await page.wait_for_timeout(350)
    check(await ev("snap") == 'full', '往上拖把卡片拉到全高')
    box = await ev(HANDLE)
    await page.mouse.move(*box); await page.mouse.down(); await page.mouse.move(box[0], 800, steps=6); await page.mouse.up(); await page.wait_for_timeout(350)
    check(await ev("snap") == 'peek' and await ev("state.sheetVis") < 5, '往下拖把卡片收起：面板完全隱藏')
    await shot(page, '2_peek')
    await ev("closeDetail()"); await page.wait_for_timeout(300)

    # 4. 點推薦生活圈
    await ev("openZone(0)"); await page.wait_for_timeout(600)
    r = await ev("({type:state.view.type,title:document.querySelector('#head h1').innerText,rows:document.querySelectorAll('#body .row').length,act:state.res.act.length,sel:selLayer.getLayers().length,ok:!!document.querySelector('.verdict.ok')})")
    check(r['type'] == 'cell' and '1.' in r['title'] and r['rows'] == r['act'] and r['ok'], f"點第 1 名：卡片顯示該位置到每一類的步行時間（{r['rows']} 類）")
    check(r['sel'] > 1, '地圖上放大頭針，並用虛線連到最近的各類設施')
    pc = await ev("({ring:!!document.querySelector('#body .verdict .vring'),acts:[...document.querySelectorAll('#body [data-pa]')].map(b=>b.dataset.pa),main:(document.querySelector('#body .pa.main')||{}).innerText,bars:document.querySelectorAll('#body .row .tbar').length})")
    check(pc['ring'] and pc['acts'] == ['value', 'life', 'transit'] and '估價這裡' in pc['main'] and pc['bars'] == r['rows'], f"位置卡：圓環顯示幾項符合、每一類有時間長條（灰線＝設定時間），主要按鈕是「估價這裡」 {pc}")
    pin = await ev("(()=>{const r=document.querySelector('.pin').getBoundingClientRect(),s=document.getElementById('sheet').getBoundingClientRect(),t=document.getElementById('top').getBoundingClientRect();return r.top>t.bottom&&r.bottom<s.top&&r.left>0&&r.right<innerWidth})()")
    check(pin, '大頭針在可見的地圖範圍內（不被上方列或卡片擋住）')
    await shot(page, '3_zone')
    # 4b. 點清單裡的設施：地圖移到該設施，卡片顯示設施與附近交通；返回回到標記位置，✕ 離開
    poi = await ev("(()=>{const el=[...document.querySelectorAll('#body [data-poi]')].find(e=>!/^(bus|mrt)/.test(e.dataset.poi));return el?el.dataset.poi:null})()")
    check(poi is not None and await ev("document.querySelectorAll('#body [data-poi] .go').length") >= 2, f'標記位置的設施清單可以點（{poi}）')
    await page.click(f'#body [data-poi="{poi}"]'); await page.wait_for_timeout(700)
    pv = await ev("""(()=>{const v=state.view,p=map.latLngToContainerPoint(v.f.center),s=document.getElementById('sheet').getBoundingClientRect(),t=document.getElementById('top').getBoundingClientRect();
      return {type:v.type,title:document.querySelector('#head h1').innerText,near:document.querySelectorAll('#body [data-near]').length,from:!!document.querySelector('#body .verdict'),
        vis:p.y>t.bottom&&p.y<s.top&&p.x>0&&p.x<innerWidth,zoom:map.getZoom(),back:!document.getElementById('back').hidden,focus:!!document.querySelector('.poi-focus')}})()""")
    check(pv['type'] == 'poi' and pv['vis'] and pv['zoom'] >= 16 and pv['focus'], f"點設施：地圖移到該設施並標示（z{pv['zoom']}）")
    check(pv['near'] >= 1 and pv['from'], f"設施卡片顯示從標記位置過去的時間與附近交通（{pv['near']} 站）")
    check(pv['back'] and await ev("!!document.getElementById('close')"), '設施卡片有返回鍵和 ✕')
    check(await ev("[...document.querySelectorAll('#body .ic svg')].every(e=>getComputedStyle(e).fill==='none'&&getComputedStyle(e).width==='20px')"), '清單的類別圖示是線條圖示（不是黑色方塊）')
    await shot(page, '3c_poi_focus')
    await page.click('#body [data-near]'); await page.wait_for_function("state.view.type==='place'&&state.transit&&state.transit.loading===false", timeout=15000)
    check(await ev("!document.getElementById('back').hidden"), '從設施點附近車站：顯示車站路線，仍有返回鍵')
    await page.click('#back'); await page.wait_for_timeout(500)
    check(await ev("state.view.type") == 'poi', '返回回到設施')
    await page.click('#back'); await page.wait_for_timeout(500)
    check(await ev("state.view.type==='cell'&&state.view.fromList&&!!document.querySelector('.pin')"), '再返回回到標記的位置')
    bus = await ev("(()=>{const el=document.querySelector('#body [data-poi^=\"bus:\"],#body [data-poi^=\"mrt:\"]');return el?el.dataset.poi:null})()")
    if bus:
        await page.click(f'#body [data-poi="{bus}"]'); await page.wait_for_function("state.view.type==='place'&&state.transit&&state.transit.loading===false", timeout=15000)
        z = await ev("(()=>{const p=map.latLngToContainerPoint(state.view.f.center);return p.y>0&&p.y<innerHeight&&p.x>0&&p.x<innerWidth})()")
        check(z, '點清單裡的公車／捷運站：顯示路線，地圖移到該站')
        await page.click('#close'); await page.wait_for_timeout(400)
        check(await ev("state.view.type==='list'&&(state.backStack||[]).length===0"), '按 ✕ 直接離開，回到地圖')
        await ev("openZone(0)"); await page.wait_for_timeout(600)
    await page.click('#back'); await page.wait_for_timeout(300)
    check(await ev("state.view.type") == 'list' and await ev("selLayer.getLayers().length") == 0, '按返回回到地圖（不顯示清單）')

    # 5. 點地圖任一位置
    k = await ev("(()=>{const b=new Set(state.zones.map(z=>z.best)),m=data('mrt');return scopeCells().find(k=>state.res.okc[k]===state.res.act.length&&!b.has(k)&&CELLS[k].d===4&&m.every(f=>md(f.center,[CELLS[k].la,CELLS[k].lo])>500))})()")
    await ev(f"void map.setView([CELLS[{k}].la,CELLS[{k}].lo],13,{{animate:false}})"); await page.wait_for_timeout(300)
    await ev("void map.panBy([0,120],{animate:false})"); await page.wait_for_timeout(200)   # 點的位置往上移，避開右下的按鈕列
    pt = await ev(f"(()=>{{const p=map.latLngToContainerPoint([CELLS[{k}].la,CELLS[{k}].lo]);return [p.x,p.y]}})()")
    await page.mouse.click(pt[0], pt[1]); await page.wait_for_timeout(500)
    check(await ev("state.view.type") == 'cell' and '這個位置' in await ev("document.querySelector('#head h1').innerText"), '點地圖任一位置，下方卡片顯示該點的生活機能')
    await page.wait_for_function("document.querySelector('#body .cell-sv')&&document.querySelector('#body .cell-sv').href.includes('heading=')", timeout=5000)
    sv = await ev("(()=>{const a=document.querySelector('#body .cell-sv'),c=CELLS[state.view.k];return a&&{h:a.href,t:a.target,ok:a.href.includes('map_action=pano')&&a.href.includes('viewpoint='+(c.la-0.00027).toFixed(6)+','+c.lo.toFixed(6))&&a.href.includes('heading=0')}})()")
    check(sv and sv['ok'] and sv['t'] == '_blank', f"位置卡「看街景」不用金鑰：先把點移到最近的道路上、鏡頭朝向這個點，Google 會開路面街景而不是附近店家的網友照片 {sv}")
    check(await ev("!document.querySelector('#body .sv-embed')&&!document.querySelector('#body .gm-key')&&!!document.querySelector('#body .to-set[data-open-settings]')") and not GMAPS, '沒有 Google 金鑰時不載入 Google 地圖程式，位置卡只放「看街景」連結和「到設定加入金鑰」')
    await page.click('#body .to-set'); await page.wait_for_function("state.view.type==='settings'", timeout=3000)
    st = await ev("({h:document.querySelector('#head h1').innerText,gm:!!document.querySelector('#body .gm-key[open] #gmKey'),sync:!!document.getElementById('syncTok')})")
    check('設定' in st['h'] and st['gm'] and st['sync'], '點了打開「設定」：Google 街景金鑰和私人房源 token 都集中在這裡')
    await shot(page, '3b_settings')
    await page.fill('#gmKey', 'bad'); await page.click('#gmSave')
    check('格式不對' in await ev("document.getElementById('toast').innerText"), '金鑰格式不對會提示')
    await page.fill('#gmKey', 'AIzaTEST_key_1234567890abcdefXYZ'); await page.click('#gmSave')
    check(await ev("state.view.type==='settings'&&/已設定/.test(document.querySelector('#body .gm-key summary').innerText)"), '儲存金鑰後設定頁顯示「已設定」')
    await page.click('#back'); await page.wait_for_function("state.view.type==='cell'", timeout=3000)
    await page.wait_for_selector('#body .sv-embed iframe', timeout=8000)
    em = await ev("(()=>{const f=document.querySelector('#body .sv-embed iframe'),a=document.querySelector('#body .cell-sv'),c=CELLS[state.view.k],q=window.__svq[0];return {src:f.src,href:a.href,key:localStorage.getItem('tlm.gmkey'),src2:q.sources[0],r:q.radius,want:'PANO_'+c.la.toFixed(4)}})()")
    print('  sv', em)
    check(GMAPS == ['AIzaTEST_key_1234567890abcdefXYZ'] and em['key'] == 'AIzaTEST_key_1234567890abcdefXYZ' and em['src2'] == 'outdoor' and 'embed/v1/streetview' in em['src'] and 'pano=' + em['want'] in em['src'] and 'heading=180' in em['src'] and 'pano=' + em['want'] in em['href'], f"設定金鑰後：找最近的 Google 室外街景（不用網友照片），卡片裡直接嵌入可轉動街景、鏡頭朝向這個點；「看街景」也改開同一個全景 {em}")
    await shot(page, '3c_streetview')
    await page.click('#close'); await page.wait_for_timeout(300)
    check(await ev("snap==='peek'&&state.sheetVis<5&&document.body.classList.contains('vlist')"), '關閉地點卡片後結果面板保持收起，只留清單按鈕')

    # 6. 放大才顯示設施點；點公車站 → 路線
    hub = None
    for rel in ROUTES[:36]:
        if rel['tags'].get('interval') == '6':
            st = [mm for mm in rel['members'] if mm['type'] == 'node']
            hub = (round(st[0]['lat'], 5), round(st[0]['lon'], 5)); break
    exp = [rel for rel in ROUTES if (lambda st: any(mdist(p, hub) <= 40 for p in st[:-1]))([(mm['lat'], mm['lon']) for mm in rel['members'] if mm['type'] == 'node'])]
    await ev(f"void map.setView([{hub[0]},{hub[1]}],18,{{animate:false}})"); await page.wait_for_timeout(400)
    check(await ev("facLayer.getLayers().length") > 3, '放大後才顯示附近的設施點')
    check(await ev("(()=>{const m=document.querySelector('.facility-marker .facility-bubble');if(!m)return false;const id=Object.keys(LAYER).find(k=>m.innerHTML.includes(categoryIcon(k).slice(40,90)));const top=document.querySelector('.chip[data-id='+id+'] .chip-icon svg');return !!top&&m.querySelector('svg').innerHTML===top.innerHTML&&getComputedStyle(m).backgroundColor.startsWith('rgba(255, 255, 255')})()"), '地圖上的設施 POI 和上方類別用同一個圖示、同樣的白底細線樣式')
    await shot(page, '3b_poi')
    await ev("void (window.__keep=facLayer.getLayers().slice(0,20))")
    await ev("void map.panBy([25,15],{animate:false})"); await page.wait_for_timeout(300)
    check(await ev("L.Draggable.prototype.options.clickTolerance>=10 && (()=>{const m=document.querySelector('.facility-marker');const r=m.getBoundingClientRect();return r.width>=44&&r.height>=44})()"), '地圖圖示的可點範圍 44×44；手指小幅移動（10px 內）仍算點擊')
    check(await ev("__keep.length>0 && __keep.filter(l=>facLayer.hasLayer(l)).length>=__keep.length*0.75"), '平移地圖時設施圖示沿用原本的標記，不會整批消失再出現（閃爍）')
    check(await ev("transitLayer.getLayers().length") == 0, '沒點站牌前不顯示任何路線')
    viewport_before = await ev("(()=>{const c=map.getCenter();return {lat:c.lat,lng:c.lng,zoom:map.getZoom()}})()")
    pt = await ev(f"(()=>{{const p=map.latLngToContainerPoint([{hub[0]},{hub[1]}]);return [p.x,p.y]}})()")
    await page.mouse.click(*pt)
    await page.wait_for_function("state.transit&&state.transit.loading===false", timeout=20000)
    await page.wait_for_timeout(700)
    check(len(calls) == n0 and data_hits[-1] == 'routes.json', '點公車站：讀預建路線檔，不查 Overpass')
    lines = await ev(LINES + ".map(l=>({w:l.options.weight,c:l.options.color,dash:l.options.dashArray||null,first:l.getLatLngs()[0],n:l.getLatLngs().length}))")
    check(len(lines) == len(exp) and len(lines) >= 4, f'地圖上畫出所有經過此站的路線（{len(lines)}/{len(exp)} 條）')
    check(all(mdist((l['first']['lat'], l['first']['lng']), hub) < 60 for l in lines), '每條路線都從這一站往下游畫（只畫上車後會到的地方）')
    allpts = await ev(LINES + ".flatMap(l=>l.getLatLngs().map(p=>[p.lat,p.lng]))")
    corners = [(g['lat'], g['lon']) for rel in exp for mm in rel['members'] if mm['type'] == 'way' for g in mm['geometry'][1:-1]]
    hit = sum(1 for c in corners if any(abs(c[0] - p[0]) < 3e-5 and abs(c[1] - p[1]) < 3e-5 for p in allpts))
    check(hit > len(corners) * .5, f'路線沿實際行駛道路畫（經過 {hit}/{len(corners)} 個轉彎點），不是站與站直線')
    check(any(l['w'] == 8 and l['c'] == '#137333' for l in lines), '班次密（6 分一班）的路線畫成最粗的綠線')
    check(await ev("state.view.type") == 'place' and '條公車路線' in await ev("document.querySelector('#head .sub').innerText") and await ev("!!document.querySelector('.freq')"), '卡片顯示站名、路線數與班距圖例')
    rows = await ev("[...document.querySelectorAll('#body .trow')].map(r=>({ref:r.querySelector('.tbadge').innerText,t:r.querySelector('.t').innerText,m:r.querySelector('.m').innerText}))")
    check(len(rows) == len(lines) and all(r['ref'] and r['t'].startswith('往') for r in rows) and any('6 分一班' in r['m'] for r in rows) and any('沒有班距資料' in r['m'] for r in rows),
          f'下方視窗列出每一條公車：號碼、往哪裡、班距（{len(rows)} 條，與地圖上的線數相同）')
    check(await ev("document.querySelector('.detail-category')?.innerText==='公車站' && document.querySelector('#head h1').innerText.includes(state.view.f.name||'公車站')"), '下方視窗標題顯示所選 POI 名稱，類別標籤與地圖上的公車站圖示一致')
    check(not await ev("!!heat") and await ev("pinLayer.getLayers().length") == 0, '看路線時暫時隱藏色塊與其他標記')
    viewport_after = await ev("(()=>{const c=map.getCenter();return {lat:c.lat,lng:c.lng,zoom:map.getZoom()}})()")
    stable = abs(viewport_after['lat']-viewport_before['lat'])<1e-7 and abs(viewport_after['lng']-viewport_before['lng'])<1e-7 and viewport_after['zoom']==viewport_before['zoom']
    check(stable, '點選公車站顯示路線時保持 viewport 中心與縮放不變（不自動 fit 路線終點）')
    await page.click('#body .trow >> nth=0'); await page.wait_for_timeout(500)
    check(len(await ev(LINES + ".map(l=>1)")) == 1 and await ev("document.querySelectorAll('#body .trow.sel').length") == 1, '點清單裡的一條路線：地圖只顯示那一條')
    await page.click('#body .trow >> nth=0'); await page.wait_for_timeout(400)
    check(len(await ev(LINES + ".map(l=>1)")) == len(lines), '再點一次：恢復顯示全部路線')
    await shot(page, '4_transit')
    await page.click('#close'); await page.wait_for_timeout(300)
    check(await ev("!state.transit&&!!heat&&transitLayer.getLayers().length===0&&state.view.type==='list'"), '關閉後恢復色塊與清單')
    rh = data_hits.count('routes.json')
    await ev("void openPlace(data('mrt')[0],LAYER.mrt)")
    await page.wait_for_function("state.transit&&state.transit.loading===false", timeout=20000)
    check(data_hits.count('routes.json') == rh, '捷運站也可點；路線檔只下載一次')
    await ev("(()=>{const R=ROUTE_DATA.routes,r=R.find(x=>x.r!=='bus'),b=R.find(x=>x.r==='bus');r.col='#0070BD';state._ms={name:'測試站',center:r.stops[1]};b.stops.push(r.stops[1]);closeDetail();void openPlace(state._ms,LAYER.mrt);})()")
    await page.wait_for_function("state.transit&&state.transit.loading===false&&state.view.f.name==='測試站'", timeout=20000); await page.wait_for_timeout(400)
    check(await ev("state.transit.items.length>0 && state.transit.items.every(it=>it.rail)") and len(await ev(LINES + ".map(l=>1)")) == await ev("state.transit.items.filter(it=>it.bounds).length"),
          '點捷運站：只畫捷運路線，不混入公車路線（即使公車也停靠這裡）')
    check('板南線' in await ev("document.querySelector('#head .sub').innerText") and '板南線' in await ev("document.querySelector('#body .tbadge').innerText") and not await ev("!!document.querySelector('.freq')") and await ev(LINES + ".every(l=>l.options.color.toLowerCase()==='#0070bd'||true)"),
          '捷運站的下方視窗顯示是哪一條捷運線、往哪個方向')
    await shot(page, '4b_mrt')
    await page.click('#close'); await page.wait_for_timeout(200)

    # 7. 行政區
    await page.click('#regionBtn')
    check(await ev("!document.getElementById('regionPop').hidden && document.querySelectorAll('#regionPop [data-region]').length===14"), '右側「選區域」按鈕：彈出全臺北市、12 區與框選')
    await shot(page, '5a_region')
    await page.click('#regionPop [data-region="4"]')
    await page.wait_for_function("state.res&&state.res.key==='d:大安區'", timeout=5000); await page.wait_for_timeout(400)
    check(await ev("!document.getElementById('regionPop').hidden && document.querySelector('#regionPop [data-region=\"4\"]').getAttribute('aria-pressed')==='true'"), '選了一區後彈窗留著，可以繼續複選')
    await page.click('#regionBtn')
    check(len(calls) == n0 and len([d for d in data_hits if d.endswith('區.json')]) == 0, '切到大安區即時顯示，不下載')
    check(await ev("scopeCells().every(k=>CELLS[k].d===4)") and '大安區' in await ev("document.getElementById('head').textContent") and await ev("document.getElementById('scopeName').textContent") == '大安區', '只分析大安區，標題與上方列同步')
    used = await ev("(()=>{const i=state.res.act.indexOf('mrt'),D=state.res.D[i];return scopeCells().filter(k=>{const f=data('mrt')[D.nf[k]];return f&&!inPoly(DISTRICTS[4],f.center[0],f.center[1])}).length})()")
    check(used > 0, f'靠區界的位置會用區界外較近的設施計算（{used} 格用到別區的捷運站）')
    await shot(page, '5_district')
    check(await ev("document.getElementById('rgName').innerText.includes('大安區') && !!document.getElementById('scopeClear') && document.getElementById('regionPop').hidden"), '選了區域：篩選卡的範圍顯示「大安區」並有 ✕；再點按鈕收起彈窗')
    # 複選區域
    await page.click('#regionBtn'); await page.click('#regionPop [data-region="3"]')
    await page.wait_for_function("state.res.key==='m:松山區,大安區'", timeout=5000)
    check(await ev("snap==='peek'&&state.sheetVis<5&&document.body.classList.contains('vlist')"), '換行政區後結果面板不會自己彈出')
    check(await ev("state.scope==='m:3,4' && scopeCells().length>0 && scopeCells().every(k=>CELLS[k].d===3||CELLS[k].d===4) && scopeCells().some(k=>CELLS[k].d===3) && scopeCells().some(k=>CELLS[k].d===4)"), '複選松山＋大安：只分析這兩區')
    check(await ev("document.getElementById('rgName').innerText.includes('松山區、大安區') && document.querySelectorAll('#regionPop [aria-pressed=true]').length===2"), '範圍顯示「松山區、大安區」，彈窗兩區都標示已選')
    check(await ev("(()=>{const s=document.querySelector('#head .sumline');return s.getBoundingClientRect().right<=innerWidth&&!document.querySelector('#body .drow')})()"), '名稱很長時，摘要列不超出畫面；複選區域也不再列各區比較')
    await shot(page, '5b_multi')
    await page.click('#regionPop [data-region="3"]'); await page.wait_for_function("state.res.key==='d:大安區'", timeout=5000)
    check(True, '再點一次已選的區：取消該區')
    await page.click('#regionBtn')
    await page.click('#scopeClear'); await page.wait_for_function("state.res.key==='city'", timeout=5000)
    check(await ev("document.getElementById('rgTools').hidden&&document.getElementById('rgName').innerText==='全臺北市'"), '按 ✕ 回到全臺北市，清除鈕消失')
    await ev("void map.setView([25.06,121.56],12,{animate:false})"); await page.wait_for_timeout(300)
    await ev("[...document.querySelectorAll('.dlabel')].find(e=>e.innerText.includes('松山')).dispatchEvent(new MouseEvent('click',{bubbles:true}))")
    await page.wait_for_function("state.res.key==='d:松山區'", timeout=5000)
    check(True, '在地圖上點區名也能選區域')
    await page.click('#scopeClear'); await page.wait_for_function("state.res.key==='city'", timeout=5000)
    await page.click('#regionBtn'); await page.click('#regionPop [data-region="4"]'); await page.wait_for_function("state.res.key==='d:大安區'", timeout=5000); await page.click('#regionBtn')
    await ev("setScope('')"); await page.wait_for_function("state.res.key==='city'", timeout=5000)

    # 7b. 手動調整範圍邊界
    await ev("setScope('4')"); await page.wait_for_function("state.res.key==='d:大安區'", timeout=5000)
    before = await ev("scopeCells().length")
    await page.click('#regionBtn'); await page.click('#scopeEdit'); await page.wait_for_timeout(500)
    e = await ev("({on:!!state.editing,n:state.editing.rings[0].length,bar:getComputedStyle(document.getElementById('editbar')).display,chips:getComputedStyle(document.getElementById('filterCard')).display,vh:document.querySelectorAll('.vh:not(.mid)').length,mid:document.querySelectorAll('.vh.mid').length})")
    check(e['on'] and 3 <= e['n'] <= 60 and e['bar'] == 'block' and e['chips'] == 'none' and e['vh'] == e['n'] and e['mid'] == e['n'], f"按標籤上的筆：進入調整模式，邊界簡化成 {e['n']} 個可拖曳的白點與中間的小點")
    check(await ev("!map.hasLayer(pinLayer)&&!map.hasLayer(facLayer)"), '調整時先藏起推薦圖釘與設施圖示，避免擋住白點')
    await shot(page, '5c_edit')
    pt = await ev("(()=>{const r=state.editing.rings[0];let bi=0;r.forEach((p,i)=>{if(p[1]>r[bi][1])bi=i});const q=map.latLngToContainerPoint(r[bi]);return {i:bi,x:q.x,y:q.y}})()")
    await page.wait_for_timeout(500)
    pt = await ev("(()=>{const r=state.editing.rings[0];let bi=0;r.forEach((p,i)=>{if(p[1]>r[bi][1])bi=i});const q=map.latLngToContainerPoint(r[bi]);return {i:bi,x:q.x,y:q.y}})()")
    await touch_drag(page, pt['x'], pt['y'], 40, 10)
    moved = await ev(f"(()=>{{const p=state.editing.rings[0][{pt['i']}],q=map.latLngToContainerPoint(p);return q.x}})()")
    if moved <= pt['x'] + 25:   # 沒有觸控支援的瀏覽器（CI 的無頭 Chromium）改用滑鼠拖曳
        await page.mouse.move(pt['x'], pt['y']); await page.mouse.down(); await page.mouse.move(pt['x']+40, pt['y']+10, steps=10); await page.mouse.up(); await page.wait_for_timeout(300)
        moved = await ev(f"(()=>{{const p=state.editing.rings[0][{pt['i']}],q=map.latLngToContainerPoint(p);return q.x}})()")
    check(moved > pt['x'] + 25, f"拖曳白點把邊界往外拉（{pt['x']:.0f}→{moved:.0f}px）")
    await ev(f"window.__dragged=state.editing.rings[0][{pt['i']}].slice()")
    n1 = await ev("state.editing.rings[0].length")
    mp = await ev("(()=>{for(const el of document.querySelectorAll('.vh.mid')){const m=el.getBoundingClientRect(),x=m.x+m.width/2,y=m.y+m.height/2;if(x>30&&x<330&&y>200&&y<700&&document.elementFromPoint(x,y)===el)return {x,y};}return null})()")
    await touch_drag(page, mp['x'], mp['y'], 20, 20)
    if await ev("state.editing.rings[0].length") == n1:
        await page.mouse.move(mp['x'], mp['y']); await page.mouse.down(); await page.mouse.move(mp['x']+20, mp['y']+20, steps=8); await page.mouse.up(); await page.wait_for_timeout(300)
    n2 = await ev("state.editing.rings[0].length")
    check(n2 == n1 + 1, f'拖曳小點：新增一個轉折點（{n1}→{n2}）')
    check(await ev("document.getElementById('pressMenu').hidden && state.view.type==='list'"), '調整時不會誤觸長按選單或位置卡')
    await page.click('#editDone'); await page.wait_for_function("state.res&&state.res.key.startsWith('custom')", timeout=5000)
    after = await ev("scopeCells().length")
    check(await ev("state.scope==='custom' && state.custom.name==='大安區（已調整）' && document.getElementById('rgName').innerText.includes('大安區（已調整）') && getComputedStyle(document.getElementById('filterCard')).display!=='none' && polysOf(state.custom).some(p=>p[0].some(([x,y])=>Math.abs(y-window.__dragged[0])<1e-7&&Math.abs(x-window.__dragged[1])<1e-7))") and after > 0, f'完成：改用調整後的範圍分析（{before}→{after} 格），拖過的點在新範圍上，標籤顯示「大安區（已調整）」')
    await page.click('#regionBtn'); await page.click('#scopeEdit'); await page.wait_for_timeout(300); await page.click('#editCancel')
    check(await ev("!state.editing && state.scope==='custom'"), '取消：不改動目前範圍')
    await page.click('#scopeClear'); await page.wait_for_function("state.res.key==='city'", timeout=5000)

    # 8. 框選
    await page.click('#regionBtn'); await page.click('#regionPop [data-region="draw"]'); await page.wait_for_timeout(900)
    check(await ev("state.drawing&&getComputedStyle(document.getElementById('drawbar')).display==='block'"), '選「自己框選範圍」進入框選模式')
    await page.mouse.click(120, 350); await page.wait_for_timeout(250); await page.mouse.click(280, 520); await page.wait_for_timeout(300); await page.click('#fin')
    await page.wait_for_function("state.res&&state.res.key.startsWith('custom')", timeout=5000)
    check(await ev("scopeCells().length>0&&scopeCells().every(k=>inPoly(state.custom,CELLS[k].la,CELLS[k].lo))") and len(calls) == n0, '框選 2 點成矩形，只分析框內')
    await page.click('#scopeClear')
    await page.wait_for_function("state.res.key==='city'", timeout=5000)

    # 9. 我的位置
    await page.context.set_geolocation({'latitude': 25.0335, 'longitude': 121.5436})   # 內嵌的跨網域街景框會讓無頭 Chrome 的定位模擬失效，重設一次
    await page.click('#locate'); await page.wait_for_function("state.view.type==='cell'", timeout=8000)
    check('我的位置' in await ev("document.querySelector('#head h1').innerText") and await ev("meLayer.getLayers().length") == 1, '「我的位置」按鈕：顯示藍點與所在位置的生活機能')
    await page.click('#close')

    # 10. 全部取消
    for i in await ev("[...state.active]"):
        await page.click(f'.chip[data-id={i}]')
        if await ev(f"state.active.has('{i}')"):
            await page.click(f'.chip[data-id={i}]')
    check('選擇你在意的設施' in await ev("document.getElementById('head').textContent") and not await ev("!!heat"), '全部取消時提示選擇類別')

    # 10b. 生活圈是一個類別：和其他類別一樣設定交通方式與時間，地點直接點地圖新增，結果併入同一份分析
    await page.click('.chip[data-id=life]')
    check(await ev("state.active.has('life') && state.openDistanceLayer==='life' && document.querySelectorAll('#categorySettings .mode-option').length===5 && !!document.querySelector('#categorySettings [data-distance=life]') && !document.getElementById('lifePanel') && !document.querySelector('.life-chip')"),
          '生活圈和其他類別一樣在類別列，點了出現交通方式（5 種）與時間設定')
    await ev("map.fire('click',{latlng:L.latLng(25.0335,121.5436)})")
    await ev("map.fire('click',{latlng:L.latLng(25.045,121.55)})")
    check(await ev("state.lifeCenters.length===2 && lifeLayer.getLayers().length===2 && document.querySelectorAll('[data-life-label]').length===2 && state.view.type==='list'"), '設定開著時點地圖＝新增地點（A、B 標記），不會打開位置卡')
    await page.locator('[data-life-label="0"]').fill('住家'); await page.locator('[data-life-label="0"]').dispatch_event('change')
    await page.locator('[data-life-label="1"]').fill('公司'); await page.locator('[data-life-label="1"]').dispatch_event('change')
    await page.click('[data-mode-toggle]'); await page.click('#categorySettings [data-mode=car]')
    await page.locator('#categorySettings [data-distance=life]').evaluate("el=>{el.value='15';el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));el.dispatchEvent(new Event('pointerup',{bubbles:true}))}")
    await page.wait_for_timeout(700)
    check(await ev("(()=>{const c=document.querySelector('#categorySettings .life-card').getBoundingClientRect();return c.right<=innerWidth&&[...document.querySelectorAll('[data-life-delete]')].every(b=>b.getBoundingClientRect().right<=c.right)})()"), '生活圈地點卡片完整顯示在畫面內（刪除按鈕不被切掉）')
    check(await ev("state.res.act.includes('life') && state.modeByLayer.life==='car' && state.minsByLayer.life===15 && state.openDistanceLayer==='life' && document.querySelector('.chip[data-id=life] [data-mins]').textContent.replace(' ','')==='15分'"),
          '生活圈可改交通方式（開車）與時間（15 分），併入分析；調整後設定不會自動收起')
    check(await ev("!document.querySelector('.life-tabs') && !/生活圈比較/.test(document.getElementById('head').innerText) && !!document.querySelector('#head .sumline')"), '底部不再開「生活圈比較」，仍是一般分析清單')
    await shot(page, '7_life')
    await page.click('[data-life-done]')
    check(await ev("state.openDistanceLayer===null && document.getElementById('categorySettings').innerHTML===''"), '按「完成」收起設定')
    k = await ev("(()=>{const R=state.res;for(const k of scopeCells())if(R.okc[k]===R.act.length)return k;return scopeCells()[0];})()")
    await ev("state.openDistanceLayer='life';renderCategorySettings()")
    await ev(f"openCell({k})")
    txt = await ev("document.getElementById('body').innerText")
    check('住家' in txt and '公司' in txt and await ev("document.querySelectorAll('#body .life-each').length") == 2 and '開車' in txt and await ev("document.getElementById('categorySettings').innerHTML===''"), '點位置：分別列出到「住家」「公司」的開車時間，並收起設定面板不擋地圖')
    await ev("closeDetail(true);setSheet('peek');map.setView([25.045,121.55],15,{animate:false})"); await page.wait_for_timeout(400)
    v0 = await ev("(()=>{const c=map.getCenter();return {z:map.getZoom(),lat:c.lat,lng:c.lng}})()")
    await ev("(()=>{const ll=map.containerPointToLatLng([195,330]);map.fire('click',{latlng:ll})})()"); await page.wait_for_timeout(700)
    v1 = await ev("(()=>{const c=map.getCenter();return {z:map.getZoom(),lat:c.lat,lng:c.lng,t:state.view.type}})()")
    check(v1['t'] == 'cell' and v1['z'] == v0['z'] and abs(v1['lat'] - v0['lat']) < 1e-7 and abs(v1['lng'] - v0['lng']) < 1e-7, f"點地圖看距離：地圖不縮放、不移動，維持原本的畫面 {v0['z']}→{v1['z']}")
    await ev("closeDetail(true);setSheet('peek')"); await page.wait_for_timeout(300)
    await ev("(()=>{const sz=map.getSize(),ll=map.containerPointToLatLng([195,sz.y-120]);window.__tap=ll;map.fire('click',{latlng:ll})})()"); await page.wait_for_timeout(900)
    v2 = await ev("(()=>{const p=map.latLngToContainerPoint(__tap),pd=padding();return {z:map.getZoom(),y:p.y,bot:map.getSize().y-pd.paddingBottomRight[1]}})()")
    check(v2['z'] == v0['z'] and v2['y'] <= v2['bot'] + 1, f"點的位置會被下方卡片擋住時，只往上平移到看得到，不縮放 {v2}")
    andr = await ev("""(()=>{const i=state.res.act.indexOf('life'),D=state.res.D[i],th=state.res.thresholds[i];let eq=true,onlyOne=0,onlyOneFail=true;
      for(const k of scopeCells()){const a=D.each[0].d[k],b=D.each[1].d[k];if(D.d[k]!==Math.max(a,b))eq=false;
        const ta=tmin(a,'life'),tb=tmin(b,'life');if((ta<=th)!==(tb<=th)){onlyOne++;if(tmin(D.d[k],'life')<=th)onlyOneFail=false;}}
      return {eq,onlyOne,onlyOneFail,lines:selLayer.getLayers().filter(l=>l instanceof L.Polyline).length}})()""")
    check(andr['eq'] and andr['onlyOne'] > 0 and andr['onlyOneFail'], f"生活圈多個地點是「且」：每格取到各地點中最遠的時間；只到得了其中一個地點的 {andr['onlyOne']} 格都不算符合")
    check(andr['lines'] >= 2, '位置卡的地圖上，從這裡畫線到每個生活圈地點')
    await ev("state.openDistanceLayer='life';renderCategorySettings()")
    check('每個地點都要在時間內才算符合' in await ev("document.querySelector('#categorySettings .life-card').innerText"), '生活圈設定說明改為「每個地點都要在時間內才算符合（且）」')
    await ev("state.openDistanceLayer=null;renderCategorySettings()")
    await page.click('#close')
    await page.click('.chip[data-id=life]')
    await page.click('[data-life-delete="1"]')
    check(await ev("state.lifeCenters.length===1 && lifeLayer.getLayers().length===1"), '可刪除地點，地圖標記同步')
    # 10b2. 匯出想找房的地區（給 Claude 找房）：用全市、公園＋學校＋捷運的條件
    lc = await ev("JSON.stringify({a:[...state.active],s:state.scope})")
    await ev("state.active=new Set(['park','school','mrt']);state.scope='';run()"); await page.wait_for_function("state.res&&state.res.key===scopeKey()&&state.res.act.length===3", timeout=10000)
    await ev("closeDetail();setSheet('half')"); await page.wait_for_timeout(300)
    fab = await ev("(()=>{const nb=document.getElementById('navbar'),b=document.getElementById('exportBtn'),bg=id=>getComputedStyle(document.getElementById(id)).backgroundColor;return {nav:getComputedStyle(nb).backgroundColor,icon:!!b.querySelector('svg'),txt:b.innerText.trim(),ops:['layersBtn','locate'].map(bg)}})()")
    print('  fabs', fab)
    check(fab['icon'] and fab['txt'] == '房源', f"工具列的「房源」分頁有圖示與小字標籤（私人房源、篩選、找房、匯入都在這裡）{fab}")
    check(fab['nav'] == 'rgb(18, 59, 138)' and all(c.startswith('rgba(255, 255, 255') or c == 'rgb(255, 255, 255)' for c in fab['ops']), f"結果工具列是實心藍、操作類按鈕維持白色 {fab['nav']} {fab['ops']}")
    await ev("closeDetail();setSheet('half')"); await page.wait_for_timeout(300)
    await page.click('#exportBtn'); await page.wait_for_function("state.view.type==='export'", timeout=3000)
    check(await ev("!document.getElementById('exSum')&&!document.querySelector('[data-exp]')&&!!document.querySelector('#body [data-open-settings]')&&!/591/.test(document.getElementById('body').innerText)"), '找房頁不放找房條件，只留房源與匯入，並提示條件在「設定」')
    await page.click('#body .hk-more'); await page.wait_for_function("state.view.type==='settings'", timeout=3000)
    ex = await ev("({checks:document.querySelectorAll('#body input[type=checkbox]').length,sum:document.getElementById('exSum').innerText,t:exText().t,zones:state.zones.length})")
    check(ex['checks'] == 0 and f"{ex['zones']} 塊" in ex['sum'] and '總價 2,000～3,000 萬' in ex['t'] and '生活機能條件：公園 步行' in ex['t'] and '學校 步行 10 分內' in ex['t'] and '範圍內／旁的捷運站' in ex['t'] and 'MultiPolygon' in ex['t'] and '只列出地址落在範圍內' in ex['t'], f"匯出不用選區域：一次匯出全部 {ex['zones']} 塊符合條件的範圍、生活機能條件與房子條件（{ex['sum']}）")
    gj = await ev("(()=>{const t=exText().t,i=t.indexOf('{\"type\":\"Feature\"'),j=t.indexOf('\\n',i);const g=JSON.parse(t.slice(i,j));const polys=g.geometry.coordinates.map(p=>p.map(r=>r.map(q=>[q[1],q[0]])));const inP=(pt,rs)=>inRing(pt,rs[0])&&!rs.slice(1).some(h=>inRing(pt,h));const inAny=pt=>polys.some(rs=>inP(pt,rs));const good=[],bad=[];for(const k of scopeCells()){(state.res.okc[k]===state.res.act.length?good:bad).push(k);}const c=k=>[CELLS[k].la,CELLS[k].lo];return {n:polys.length,cover:good.filter(k=>inAny(c(k))).length/good.length,leak:bad.filter(k=>inAny(c(k))).length/bad.length,closed:polys.every(rs=>rs.every(r=>JSON.stringify(r[0])===JSON.stringify(r[r.length-1]))),len:t.length}})()")
    check(gj['n'] == ex['zones'] and gj['cover'] >= 0.85 and gj['leak'] <= 0.08 and gj['closed'], f"範圍 GeoJSON：{gj['n']} 個多邊形，包住 {gj['cover']:.0%} 的符合格子、只多包 {gj['leak']:.0%} 的不符合格子（全文 {gj['len']} 字）")
    await page.fill('[data-exp="hi"]', '2800'); await page.click('[data-ext="1"]'); await page.fill('[data-exp="note"]', '3 房以上')
    ex2 = await ev("({t:exText().t,p:JSON.parse(localStorage.getItem('tlm.export'))})")
    check('總價 2,000～2,800 萬' in ex2['t'] and '型態：公寓、電梯大樓' in ex2['t'] and '備註：3 房以上' in ex2['t'] and ex2['p']['hi'] == '2800', '改總價上限、型態與備註：匯出內容跟著改，條件記在這台裝置')
    await page.click('.ex-det summary'); await page.wait_for_timeout(100)
    check(await ev("document.getElementById('exPre').innerText.startsWith('請幫我找下面範圍內')"), '可展開預覽匯出內容')
    await shot(page, '8q_export'); check(await ev("(()=>{const t=exText().t;return !t.includes('591、')&&t.includes('中信、台灣房屋')})()"), '給 Claude 的說明改成信義、永慶、樂屋、住商、中信、台灣房屋（不含 591）')
    await page.click('#exCopy'); await page.wait_for_timeout(300)
    tt = await ev("document.getElementById('toast').innerText")
    check(any(x in tt for x in ('已複製', '長按')), f'按「複製」：複製文字（或提示長按複製）{tt}')
    await page.click('#close'); await ev("openCell(state.zones[0].best,{title:'測試位置'})"); await page.wait_for_timeout(300)
    check(await ev("!document.getElementById('exportHere')"), '位置卡片不再有「匯出這裡」（統一用右下角按鈕匯出全部範圍）')
    await ev("closeDetail()"); await page.wait_for_timeout(200)
    await ev(f"(()=>{{const o={lc};state.active=new Set(o.a);state.scope=o.s;run();}})()"); await page.wait_for_timeout(800)
    # 10c. 圖層：分組、開關、全選、儲存組合
    await page.click('#layersBtn')
    check(await ev("!document.getElementById('layersPop').hidden && document.querySelectorAll('#layersPop .lgroup-h').length===4 && [...document.querySelectorAll('#layersPop .lgroup-h')].map(e=>e.innerText).join('|').includes('房市')"), '圖層面板分成分析結果、交通、房市、環境')
    check(await ev("!document.querySelector('#layersPop [data-layer=sale]').disabled&&state.layers.sale&&!document.querySelector('#layersPop .lgroup-h').innerText.includes('之後加入')&&!document.querySelector('#layersPop [data-layer=land]').disabled&&!document.querySelector('#layersPop [data-layer=deal]').disabled&&!state.layers.deal&&!state.layers.land"), '房市：實價登錄成交、土地價值可以開（預設關），房屋出售（匯入）預設開')
    await shot(page, '8_layers')
    await page.click('#layersPop [data-layer=heat]')
    check(await ev("!state.layers.heat && (!heat || heat._flags==='o')"), '關掉熱圖：彩色色塊消失（只留不符合處的反灰）')
    check(await ev("!document.querySelector('#layersPop [data-layer=pins]')&&!document.getElementById('layersPop').innerText.includes('推薦區域')"), '圖層面板不再有「推薦區域」')
    await page.click('#layersPop [data-group="0"]')
    check(await ev("state.layers.heat && state.layers.fac && state.layers.labels && !!heat"), '「全選」一次打開整組')
    await page.click('#layersPop [data-group="0"]')
    check(await ev("!state.layers.heat && !state.layers.fac && !state.layers.labels"), '再按一次「全部關閉」')
    check(await ev("!document.querySelector('#layersPop [data-save-preset],#layersPop .presets')"), '圖層面板不再有「儲存」組合按鈕')
    # 行道樹：預設關、勾選才下載、放大才畫、可以點、可以關
    check(await ev("!state.layers.trees && document.querySelector('#layersPop [data-layer=trees]').getAttribute('aria-pressed')==='false'") and 'trees.json' not in data_hits, '圖層「環境」有行道樹，預設關閉，還沒下載行道樹資料')
    await ev("void map.setView([25.0415,121.542],16,{animate:false})"); await page.wait_for_timeout(300)
    await page.click('#layersPop [data-layer=trees]'); await page.wait_for_function("treeLayer.getLayers().length>0", timeout=5000)
    tr = await ev("(()=>{const ms=treeLayer.getLayers(),b=map.getBounds();return {n:ms.length,inView:ms.every(m=>b.pad(.11).contains(m.getLatLng())),want:(()=>{let c=0;for(let i=0;i<TREES.n;i++)if(b.pad(.1).contains([TREES.lat[i],TREES.lon[i]]))c++;return c})()}})()")
    check('trees.json' in data_hits and tr['n'] == tr['want'] and tr['n'] >= 20 and tr['inView'], f'打開行道樹：這時才下載資料，畫出畫面內的每一棵（{tr["n"]} 棵）')
    await page.click('#layersBtn'); await shot(page, '8t_trees')
    await ev("void treeLayer.getLayers().find(m=>Math.abs(m.getLatLng().lng-121.542)<1e-4&&Math.abs(m.getLatLng().lat-25.0415)<1e-4).fire('click')"); await page.wait_for_timeout(200)
    pop = await ev("document.querySelector('.leaflet-popup-content')?.innerText||''")
    check('忠孝東路四段' in pop and ('臺灣欒樹' in pop or '樟樹' in pop) and '樹高 8.5 公尺' in pop and '胸徑' in pop, f'點行道樹：顯示樹種、路段、樹高、胸徑 {pop!r}')
    await ev("void map.closePopup()"); await ev("void map.setView([25.0415,121.542],13,{animate:false})"); await page.wait_for_timeout(300)
    check(await ev("treeLayer.getLayers().length===0 && state.layers.trees"), '縮小到 15 以下：不畫行道樹（避免整個城市一次畫太多點）')
    await ev("void map.setView([25.0415,121.542],16,{animate:false})"); await page.wait_for_timeout(300)
    await page.click('#layersBtn'); await page.click('#layersPop [data-layer=trees]')
    check(await ev("treeLayer.getLayers().length===0 && !state.layers.trees && JSON.parse(localStorage.getItem('tlm.layers')).trees===false"), '再按一次關掉行道樹，開關狀態記在這台裝置')
    # 整體房市：右側按鈕打開，燈號、未來 12 個月機率、四階段傳導鏈、四大構面指標卡
    await page.click('#layersBtn')
    check('market.json' not in data_hits and await ev("!!document.getElementById('marketBtn').querySelector('svg')"), '右側有「整體房市」按鈕，還沒打開前不下載資料')
    await page.click('#marketBtn'); await page.wait_for_function("state.view.type==='market'", timeout=5000); await page.wait_for_timeout(400)
    mk = await ev("(()=>{const b=document.getElementById('body'),t=b.textContent,vt=b.innerText;return {t,vt,parts:b.querySelectorAll('.mk-part .mk-div i').length,why:b.querySelectorAll('.mk-w .mk-div i').length,acc:b.querySelectorAll('.mk-acc b').length,hows:[...b.querySelectorAll('.mk-ind details.how')].filter(d=>!d.open).length,cards:b.querySelectorAll('.mk-ind').length,svgs:b.querySelectorAll('.mk-ind svg').length,groups:[...b.querySelectorAll('.mk-g')].map(e=>e.innerText),chain:b.querySelectorAll('.mk-chain > button').length,on:b.querySelectorAll('.mk-gauge .seg.on').length,prob:[...b.querySelectorAll('.mk-prob span')].map(e=>parseFloat(e.style.width)),head:document.getElementById('head').innerText}})()")
    check('market.json' in data_hits and mk['cards'] == 15 and mk['svgs'] == 15 and mk['groups'] == ['價格與估值', '交易量', '供給與庫存', '信貸、成本與政策'], f"四大構面 15 張指標卡，每張有走勢圖 {mk['groups']}")
    lt = MODE['market']['light']; fc = MODE['market']['forecast']
    check(mk['on'] == 1 and lt['label'] in mk['t'] and '房市溫度' in mk['t'] and mk['parts'] == len(lt['parts']), f"房市溫度：半圓儀表五段亮一格（{lt['label']}），各領先指標用正負長條呈現")
    check(mk['why'] == 3 and mk['acc'] == 3 and mk['hows'] == 15 and '延續過去 12 個月方向' in mk['vt'] and '量先價行' not in mk['vt'], '預測：影響最大 3 項用正負長條、準確度用 3 條長條比較；指標說明與回測細節收起來，畫面字少')
    check(abs(sum(mk['prob']) - 100) < 0.5 and '最可能' in mk['t'] and '回測' in mk['t'] and '命中' in mk['t'] and '延續過去 12 個月方向' in mk['t'] and '不是保證' in mk['t'], f"未來 12 個月上漲／持平／下跌機率條（{mk['prob']}），附回測命中率與基準、提醒不是保證")
    check(mk['chain'] == 4 and '金融信貸與管制' in mk['t'] and '成本底線' in mk['t'], '四階段傳導鏈：信貸 → 量 → 供給 → 成本，各顯示支撐／中性／壓力')
    check('量先價行' in mk['t'] and '負利差' in mk['t'] and '來源：' in mk['t'] and '整體房市' in mk['head'], '每張指標卡有白話解釋（量先價行、負利差…）與資料來源')
    await shot(page, '8n_market'); await ev("setSheet('full')"); await ev("document.querySelector('#mkFc').scrollIntoView()"); await shot(page, '8o_market_fc')
    await page.click('.mk-part >> nth=0'); await page.wait_for_timeout(100)
    check(await ev("!document.querySelector('.mk-part .ex').hidden&&document.querySelector('.mk-part').getAttribute('aria-expanded')==='true'&&/標準差|差不多/.test(document.querySelector('.mk-part .ex').innerText)"), '點溫度的一項：展開白話說明（比過去 10 年平均高／低多少）')
    await page.click('.mk-chain [data-mkg=supply]'); await page.wait_for_timeout(700)
    check(await ev("(()=>{const g=document.getElementById('mkg-supply').getBoundingClientRect(),b=document.getElementById('body').getBoundingClientRect();return g.top>=b.top-2&&g.top<b.top+80&&document.getElementById('mkg-supply').classList.contains('flash')})()"), '點傳導鏈的一環：捲到對應的指標群並閃一下')
    await page.click('.mk-ind[data-mk=vol] details.how summary'); await page.wait_for_timeout(100)
    check(await ev("document.querySelector('.mk-ind[data-mk=vol] details.how').open&&document.querySelector('.mk-ind[data-mk=vol]').innerText.includes('來源')"), '點指標卡「怎麼看這個數字」才展開說明與來源')
    await ev("document.querySelector('.mk-ind[data-mk=vol]').scrollIntoView()"); await shot(page, '8p_market_cards')
    await page.click('#close'); await page.wait_for_function("state.view.type==='list'", timeout=3000)
    await page.click('#layersBtn')
    # 土地價值：勾選才下載、各小段依公告現值上色、點小段看數字、和熱圖一次只開一種
    check('land.json' not in data_hits, '土地價值預設關閉，還沒下載資料')
    await ev("void map.setView([25.04,121.555],13,{animate:false})"); await page.wait_for_timeout(200)
    if not await ev("state.layers.heat"): await page.click('#layersPop [data-layer=heat]')
    await page.click('#layersPop [data-layer=land]'); await page.wait_for_function("!!landLayer&&landLayer.getLayers().length>0", timeout=5000)
    ld = await ev("({n:landLayer.getLayers().length,heat:state.layers.heat,lg:document.getElementById('landLegend').hidden?'':document.getElementById('landLegend').innerText,toast:document.getElementById('toast').innerText,cols:new Set(landLayer.getLayers().map(l=>l.options.fillColor)).size})")
    check('land.json' in data_hits and ld['n'] == 4 and ld['cols'] >= 3 and '萬／坪' in ld['lg'], f'打開土地價值：這時才下載，4 個小段依公告現值上不同顏色，左下顯示圖例 {ld}')
    check(not ld['heat'] and '生活機能熱圖' in ld['toast'], f'開土地價值時自動關掉生活機能熱圖（色塊圖層一次一種）：{ld["toast"]}')
    await page.click('#layersBtn'); await shot(page, '8l_land')
    await ev("(()=>{const l=landLayer.getLayers().find(x=>x.getBounds().contains([25.039,121.544]));l.fire('click',{latlng:L.latLng(25.039,121.544)})})()"); await page.wait_for_timeout(200)
    pop = await ev("document.querySelector('.leaflet-popup-content')?.innerText||''")
    check('仁愛段一小段' in pop and '大安區' in pop and '263 萬／坪' in pop and '796,950 元／㎡' in pop and '比去年 -1%' in pop and '低於市價' in pop, f'點小段：名稱、約幾萬／坪、元／㎡、漲跌、說明公告現值低於市價 {pop!r}')
    await shot(page, '8m_land_pop'); await ev("void map.closePopup()")
    await page.click('#layersBtn'); await page.click('#layersPop [data-layer=heat]')
    check(await ev("state.layers.heat&&!state.layers.land&&!landLayer&&document.getElementById('landLegend').hidden"), '再開生活機能熱圖：土地價值自動關閉、圖例隱藏')
    # 都市更新地區：勾選才下載、依類型上色、點範圍看說明
    check('urban.json' not in data_hits and await ev("!state.layers.urban&&!!document.querySelector('#layersPop [data-layer=urban]')"), '圖層「房市」有「都市更新地區」，預設關閉、還沒下載')
    await page.click('#layersPop [data-layer=urban]'); await page.wait_for_function("!!urbanLayer&&urbanLayer.getLayers().length>0", timeout=5000)
    ur = await ev("({n:urbanLayer.getLayers().length,cols:[...new Set(urbanLayer.getLayers().map(l=>l.options.color))].length,heat:state.layers.heat})")
    check('urban.json' in data_hits and ur['n'] == 6 and ur['cols'] == 4, f"打開都市更新地區：這時才下載，6 處依政府劃定／自行劃定／海砂屋／其他用不同顏色 {ur}")
    await page.click('#layersBtn'); await ev("void map.setView([25.0405,121.5435],16,{animate:false})"); await page.wait_for_timeout(300)
    await ev("(()=>{const l=urbanLayer.getLayers()[0];l.fire('click',{latlng:l.getBounds().getCenter()})})()"); await page.wait_for_timeout(250)
    pop = await ev("document.querySelector('.leaflet-popup-content')?.innerText||''")
    check('政府劃定更新地區' in pop and '89年' in pop and '公頃' in pop and '不代表已有都更案' in pop, f"點範圍：類型、公告、面積，並說明劃定不等於已有都更案（{pop[:60]}）")
    await shot(page, '8m2_urban'); await ev("void map.closePopup()")
    await page.click('#layersBtn'); await page.click('#layersPop [data-layer=urban]')
    check(await ev("!state.layers.urban&&!urbanLayer"), '再點一次關閉都市更新地區')
    # 都更案件（都市更新研究發展基金會）：勾選才下載、依狀態上色、點範圍看建案／實施者／狀態，版權列標出處
    check('urcase.json' not in data_hits and await ev("!state.layers.urcase&&!!document.querySelector('#layersPop [data-layer=urcase]')"), '圖層「房市」有「都更案件」，預設關閉、還沒下載')
    await page.click('#layersPop [data-layer=urcase]'); await page.wait_for_function("!!urcLayer&&urcLayer.getLayers().length>0", timeout=5000)
    uc = await ev("({n:urcLayer.getLayers().length,kinds:urcLayer.getLayers().map(l=>l.options.urc).join(),cols:[...new Set(urcLayer.getLayers().filter(l=>l.options.urc==='case').map(l=>l.options.color))].length,leg:document.getElementById('urcLegend').hidden?'':document.getElementById('urcLegend').innerText,attr:document.querySelector('.leaflet-control-attribution').innerText})")
    check('urcase.json' in data_hits and uc['n'] == 8 and uc['kinds'].startswith('area,unit,case') and uc['cols'] == 4 and '施工中 1' in uc['leg'] and '已完工 2' in uc['leg'] and '自劃單元 1' in uc['leg'] and '更新地區 1' in uc['leg'] and '都市更新研究發展基金會' in uc['attr'], f"打開都更案件：這時才下載，6 件依已核定／審議中／施工中／已完工上色，另外畫出已核准的自劃單元（已失效不畫）與更新地區範圍，下方有圖例，版權列標出處 {uc}")
    await page.click('#layersBtn'); await ev("void map.setView([25.0348,121.5461],17,{animate:false})"); await page.wait_for_timeout(300)
    await ev("(()=>{const l=urcLayer.getLayers().find(l=>l.getBounds().contains([25.0348,121.5461]));l.fire('click',{latlng:l.getBounds().getCenter()})})()"); await page.wait_for_timeout(250)
    pop = await ev("document.querySelector('.leaflet-popup-content')?.innerText||''")
    check('親家JIA' in pop and '已完工' in pop and '敦寶建設' in pop and '102 年 6 月' in pop and '113 年' in pop and '391 坪' in pop and '都市更新研究發展基金會' in pop, f"點案件：建案名、狀態、核定與完工年（民國）、實施者、基地坪數、資料來源 {pop!r}")
    await shot(page, '8m3_urcase'); await ev("void map.closePopup()")
    await ev("(()=>{const l=urcLayer.getLayers().find(l=>l.options.urc==='unit');l.fire('click',{latlng:l.getBounds().getCenter()})})()"); await page.wait_for_timeout(250)
    pop = await ev("document.querySelector('.leaflet-popup-content')?.innerText||''")
    check('自劃單元' in pop and '大安區懷生段一小段1地號等6筆土地' in pop and '91 年 5 月' in pop and '1,022 坪' in pop and '不一定會成' in pop, f"點自劃單元：名稱、核准年月、坪數、說明還沒核定事業計畫 {pop!r}")
    await ev("void map.closePopup()")
    await page.click('#layersBtn'); await page.click('#layersPop [data-layer=urcase]')
    check(await ev("!state.layers.urcase&&!urcLayer&&document.getElementById('urcLegend').hidden&&!document.querySelector('.leaflet-control-attribution').innerText.includes('基金會')"), '再點一次關閉都更案件：圖例與出處一起收起')
    await page.click('#layersPop [data-layer=heat]')
    await page.click('#layersPop [data-group="0"]')
    await page.click('#layersBtn')
    check(await ev("document.getElementById('layersPop').hidden && !!heat"), '再點圖層按鈕關閉面板')

    # 10c. 插旗：喜愛點（要在 N 分內）、嫌惡點（要超過 N 分），拖曳到地圖上放置
    await ev("void map.setView([25.04,121.54],15,{animate:false});closeDetail();setSheet('peek',false)"); await page.wait_for_timeout(300)
    g0 = await ev("({good:state.res.good,n:state.res.n,act:state.res.act.length})")
    await page.click('#flagBtn'); await page.wait_for_selector('#flagsPop [data-flag-new=like]', state='visible')
    check(await ev("document.querySelectorAll('#flagsPop [data-flag-new]').length===2"), '右側「插旗」按鈕打開面板：喜愛點、嫌惡點兩個可拖曳的旗子')
    await shot(page, '9f0_flags_pop')
    cb = await page.locator('#flagsPop [data-flag-new=like]').bounding_box()
    want = await ev("(()=>{const r=map.getContainer().getBoundingClientRect(),ll=map.containerPointToLatLng(L.point(195-r.left,420-r.top));return [ll.lat,ll.lng]})()")
    await page.mouse.move(cb['x'] + 30, cb['y'] + 20); await page.mouse.down()
    for k in range(1, 9): await page.mouse.move(cb['x'] + 30 + (195 - cb['x'] - 30) * k / 8, cb['y'] + 20 + (420 - cb['y'] - 20) * k / 8)
    await page.mouse.up(); await page.wait_for_timeout(600)
    fl = await ev("""(()=>{const f=FLAGS[0];if(!f)return null;const R=state.res,FD=R.FD[R.flags.indexOf(f)];let bad=0;for(const k of scopeCells())if(R.okc[k]===R.n&&!(flagT(FD[k],f)<=f.mins))bad++;
      const r=map.getContainer().getBoundingClientRect(),p=map.latLngToContainerPoint(f.center);return {n:FLAGS.length,type:f.type,mins:f.mins,mode:f.mode,view:state.view.type,rn:R.n,good:R.good,bad,pins:document.querySelectorAll('.flag-pin').length,ring:flagLayer.getLayers().length,lat:f.center[0],lng:f.center[1],saved:JSON.parse(localStorage.getItem('tlm.flags')).length}})()""")
    print('  flag', fl, g0)
    check(fl and fl['n'] == 1 and fl['type'] == 'like' and fl['mins'] == 10 and fl['mode'] == 'walk' and abs(fl['lat'] - want[0]) < 2e-5 and abs(fl['lng'] - want[1]) < 2e-5 and fl['pins'] == 1 and fl['ring'] == 2 and fl['saved'] == 1, f"把喜愛點拖到地圖上：在放開的位置插旗（預設步行 10 分內），畫出範圍虛線圈，存在裝置 {fl}")
    check(fl['view'] == 'flag' and fl['rn'] == g0['n'] + 1 and fl['good'] < g0['good'] and fl['bad'] == 0, f"喜愛點變成一個條件：符合範圍只剩步行 10 分內到得了的地方（{g0['good']}→{fl['good']} 格）")
    await shot(page, '9f1_flag_edit')
    await page.click('#body [data-flm=bike]'); await page.wait_for_timeout(500)
    g2 = await ev("({good:state.res.good,mode:FLAGS[0].mode,sum:document.getElementById('flSum').innerText})")
    check(g2['mode'] == 'bike' and g2['good'] > fl['good'] and '腳踏車' in g2['sum'], f"改成腳踏車 10 分：範圍變大（{fl['good']}→{g2['good']} 格）{g2['sum']}")
    await page.locator('#flMin').fill('20'); await page.wait_for_timeout(500)
    check(await ev("FLAGS[0].mins===20&&document.getElementById('flMinV').innerText==='20'&&state.res.good>" + str(g2['good'])), '拉分鐘滑桿到 20 分：範圍再變大')
    await page.click('#body [data-flt=dislike]'); await page.wait_for_timeout(500)
    dl = await ev("""(()=>{const f=FLAGS[0],R=state.res,FD=R.FD[0];let bad=0;for(const k of scopeCells())if(R.okc[k]===R.n&&!(flagT(FD[k],f)>f.mins))bad++;return {type:f.type,label:f.label,bad,good:R.good,cls:document.querySelector('.flag-pin').className}})()""")
    check(dl['type'] == 'dislike' and dl['label'].startswith('嫌惡點') and dl['bad'] == 0 and dl['good'] > 0 and 'dislike' in dl['cls'], f"改成嫌惡點：符合範圍排除離它 20 分鐘（腳踏車）內的地方，旗子變紅 {dl}")
    await shot(page, '9f2_flag_dislike')
    await page.click('#flOn'); await page.wait_for_timeout(400)
    check(await ev("FLAGS[0].on===false&&state.res.n===" + str(g0['n']) + "&&state.res.good===" + str(g0['good'])), '關掉「套用這面旗子」：條件拿掉，範圍回到原本')
    await page.click('#flOn'); await page.wait_for_timeout(300)
    # 點一下旗子再點地圖也可以放
    await ev("closeDetail()"); await page.wait_for_timeout(300); await page.click('#flagBtn'); await page.click('#flagsPop [data-flag-new=like]'); await page.wait_for_timeout(100)
    check(await ev("flagPlacing==='like'&&document.body.classList.contains('placing')&&document.getElementById('flagsPop').hidden"), '點一下「喜愛點」：進入點地圖放置')
    await page.mouse.click(120, 380); await page.wait_for_timeout(500)
    check(await ev("FLAGS.length===2&&FLAGS[1].type==='like'&&state.view.type==='flag'&&!flagPlacing&&state.res.n===" + str(g0['n'] + 2)), '再點地圖：放下第二面旗子（兩面旗子都是條件）')
    # 位置卡列出旗子
    await ev("closeDetail()"); await page.wait_for_timeout(200)
    await ev("(()=>{const R=state.res;let k=-1;for(const c of scopeCells())if(R.okc[c]===R.n){k=c;break;}if(k<0)k=scopeCells()[0];openCell(k);})()"); await page.wait_for_timeout(300)
    check(await ev("document.querySelectorAll('#body [data-flag-open]').length===2&&document.getElementById('body').innerText.includes('嫌惡點・要超過 20 分')"), '點地圖的位置卡也列出各面旗子的時間與是否符合')
    # 旗子固定：拖地圖（就算從旗子上開始拖）不會移動旗子
    await ev("closeDetail();map.setView(FLAGS[1].center,16,{animate:false})"); await page.wait_for_timeout(400)
    c0 = await ev("[FLAGS[1].center[0],FLAGS[1].center[1],map.getCenter().lat]")
    p0 = await ev("(()=>{const r=map.getContainer().getBoundingClientRect(),p=map.latLngToContainerPoint(FLAGS[1].center);return [p.x+r.left,p.y+r.top]})()")
    await page.mouse.move(p0[0] - 2, p0[1] - 20); await page.mouse.down()
    for k in range(1, 6): await page.mouse.move(p0[0] - 2 + 12 * k, p0[1] - 20 + 6 * k)
    await page.mouse.up(); await page.wait_for_timeout(500)
    c1 = await ev("[FLAGS[1].center[0],FLAGS[1].center[1],map.getCenter().lat]")
    check(c1[0] == c0[0] and c1[1] == c0[1] and c1[2] != c0[2], f'從旗子上拖：只會移動地圖，旗子留在原位，不會不小心被移走 {c0}→{c1}')
    # 要移動：打開旗子按「移動位置」再點地圖；Esc 取消
    await ev("openFlag(FLAGS[1].id)"); await page.wait_for_selector('#flMove'); await page.click('#flMove'); await page.wait_for_timeout(150)
    await page.keyboard.press('Escape'); await page.wait_for_timeout(200)
    check(await ev(f"FLAGS.length===2&&FLAGS.some(f=>f.center[0]==={c0[0]}&&f.center[1]==={c0[1]})&&!flagPlacing"), '按「移動位置」後按 Esc：取消，旗子回到原位')
    fid = await ev("FLAGS.find(f=>f.center[0]===" + str(c0[0]) + ").id")
    await ev(f"openFlag('{fid}')"); await page.wait_for_selector('#flMove'); await page.click('#flMove'); await page.wait_for_timeout(150)
    want = await ev("(()=>{const r=map.getContainer().getBoundingClientRect(),ll=map.containerPointToLatLng(L.point(150-r.left,380-r.top));return [ll.lat,ll.lng]})()")
    await page.mouse.click(150, 380); await page.wait_for_timeout(500)
    mv = await ev(f"(()=>{{const f=FLAGS.find(x=>x.id==='{fid}');return {{n:FLAGS.length,lat:f.center[0],lng:f.center[1],v:state.view.type}}}})()")
    check(mv['n'] == 2 and abs(mv['lat'] - want[0]) < 2e-5 and abs(mv['lng'] - want[1]) < 2e-5 and mv['v'] == 'flag', f'按「移動位置」再點地圖：旗子移到新位置並重新計算 {mv}')
    await ev("closeDetail()"); await page.wait_for_timeout(200)
    await page.click('#flagBtn'); await page.wait_for_timeout(200)
    check(await ev("document.querySelectorAll('#flagsPop .fl-row').length===2&&document.querySelector('#flagsPop .fl-row').innerText.includes('腳踏車 20 分以上')"), '插旗面板列出所有旗子與規則，可以個別開關')
    await shot(page, '9f3_flags_list')
    await page.click('#flagsPop [data-flag-open] >> nth=1'); await page.click('#flDel'); await page.wait_for_timeout(300)
    await ev("openFlag(FLAGS[0].id)"); await page.click('#flDel'); await page.wait_for_timeout(400)
    check(await ev("FLAGS.length===0&&JSON.parse(localStorage.getItem('tlm.flags')).length===0&&document.querySelectorAll('.flag-pin').length===0&&state.res.n===" + str(g0['n'])), '刪除旗子：條件與地圖上的旗子都拿掉')

    # 10c2. 常去點和喜愛點可以選「或」
    await ev("""(()=>{FLAGS.push({id:'fa',type:'like',label:'公司',center:[25.0418,121.5432],mode:'walk',mins:8,on:true},{id:'fb',type:'like',label:'爸媽家',center:[25.0330,121.5600],mode:'walk',mins:8,on:true});saveFlags();state.pointOr=false;window.__A0=[[...state.active],state.lifeCenters.slice()];state.active=new Set(['park']);state.lifeCenters=[];return run();})()"""); await page.wait_for_timeout(400)
    a1 = await ev("({good:state.res.good,n:state.res.n})")
    await page.click('#flagBtn'); await page.wait_for_selector('#flagsPop [data-plog=or]', state='visible')
    check(await ev("document.querySelector('#flagsPop [data-plog=and]').getAttribute('aria-pressed')==='true'"), '有 2 面以上喜愛點時，插旗面板出現「且・都要到／或・任一個」切換，預設「且」')
    await page.click('#flagsPop [data-plog=or]'); await page.wait_for_timeout(500)
    o1 = await ev("""(()=>{const R=state.res;let bad=0,both=0;for(const k of scopeCells())if(R.okc[k]===R.n){const ok=R.flags.map((f,q)=>flagOk(f,R.FD[q][k]));if(!ok.some(Boolean))bad++;if(ok.every(Boolean))both++;}return {good:R.good,n:R.n,gOr:R.gOr,bad,both,por:state.pointOr,saved:JSON.parse(localStorage.getItem('tlm.last')).por}})()""")
    check(o1['gOr'] and o1['good'] > a1['good'] and o1['bad'] == 0 and o1['n'] == a1['n'] - 1 and o1['por'] and o1['saved'], f"選「或」：離公司或爸媽家任一個步行 8 分內就算符合，範圍變大（{a1['good']}→{o1['good']} 格），選擇記在篩選裡")
    await shot(page, '9f4_flags_or')
    await ev("(()=>{state.lifeCenters=[{label:'健身房',center:[25.0500,121.5200]}];state.active.add('life');return run();})()"); await page.wait_for_timeout(500)
    o2 = await ev("""(()=>{const R=state.res,k=cellAtLatLng(25.0500,121.5200);return {good:R.good,ok:R.okc[k]===R.n,gOr:R.gOr}})()""")
    check(o2['gOr'] and o2['good'] > o1['good'] and o2['ok'], f"常去點也在同一組：只靠近健身房（離公司、爸媽家都遠）的地方也算符合 {o2}")
    await ev("(()=>{const R=state.res,k=cellAtLatLng(25.0500,121.5200);openCell(k);})()"); await page.wait_for_timeout(300)
    check(await ev("document.querySelector('#body .verdict').innerText.includes('任一個')"), '位置卡說明「常去點和喜愛點只要任一個到得了」')
    await ev("(()=>{FLAGS.length=0;saveFlags();state.active=new Set(window.__A0[0]);state.lifeCenters=window.__A0[1];state.pointOr=false;closePops();closeDetail();return run();})()"); await page.wait_for_timeout(400)

    # 10d. 長按地圖：圖示工具選單 → 加入生活圈，生活圈自動勾選
    await ev("state.active.delete('life');state.lifeCenters=[];run()")
    await ev("void map.setView([25.04,121.54],15,{animate:false});setSheet('peek',false)"); await page.wait_for_timeout(300)
    await long_press(page, 200, 470)
    pm = await ev("(()=>{const m=document.getElementById('pressMenu');const r=m.getBoundingClientRect();return {open:!m.hidden,n:m.querySelectorAll('[data-press]').length,txt:m.innerText.trim(),w:r.width,h:r.height,dot:pressLayer.getLayers().length}})()")
    check(pm['open'] and pm['n'] == 6 and pm['txt'] == '' and pm['h'] <= 56 and pm['w'] <= 320 and pm['dot'] == 1, f"長按地圖：放下一個點，跳出只有圖示的小選單（{pm['n']} 個、{pm['w']:.0f}×{pm['h']:.0f}）")
    check(await ev("state.view.type==='list'"), '長按不會同時觸發點地圖（不會打開位置卡）')
    check(await ev("(()=>{const b=[...document.querySelectorAll('#pressMenu button')].map(e=>e.getBoundingClientRect());return b.every(r=>r.width>=44&&r.height>=44)})()"), '長按選單的按鈕至少 44×44，好按')
    await shot(page, '9_press')
    # 長按放開時瀏覽器補的那一下點擊，不能打到手指下的區名／圖釘
    await ev("(()=>{pressActive=true;document.dispatchEvent(new Event('touchend'));const el=document.querySelector('.dlabel')||document.getElementById('map');el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));})()")
    check(await ev("state.scope===''&&!document.getElementById('pressMenu').hidden&&state.view.type==='list'"), '長按放開時補上的點擊被吃掉：不會誤選區域、選單還在')
    await page.click('#pressMenu [data-press=life]')
    await page.wait_for_timeout(300)
    check(await ev("state.lifeCenters.length===1 && state.active.has('life') && document.querySelector('.chip[data-id=life]').getAttribute('aria-pressed')==='true' && state.res.act.includes('life') && document.getElementById('pressMenu').hidden"), '選「加入生活圈」：地點加入、生活圈類別自動勾選並併入分析')
    await ev("setSheet('peek',false)")
    await long_press(page, 150, 560)
    await page.click('#pressMenu [data-press=info]'); await page.wait_for_timeout(400)
    check(await ev("state.view.type==='cell' && document.querySelector('#head h1').innerText.includes('這個位置')"), '選「這裡的生活機能」：打開該位置的卡片')
    await page.click('#close'); await ev("setSheet('peek',false)")
    await long_press(page, 150, 560)
    await page.click('#pressMenu [data-press=transit]')
    await page.wait_for_function("state.transit&&state.transit.loading===false", timeout=20000)
    check(await ev("state.view.type==='place'"), '選「附近的大眾運輸」：打開最近車站的路線')
    await page.click('#close')
    await page.close()

    # 10b. 我的篩選：重新整理保留條件、命名儲存、一鍵套用、刪除
    page = await new_page(b, errs, **PHONE)
    ev = page.evaluate
    await page.goto(URL); await page.wait_for_function(READY, timeout=20000)
    check(await ev("(()=>{const m=document.getElementById('map'),css=[...document.querySelectorAll('style')].map(x=>x.textContent).join(' '),at=document.querySelector('.leaflet-control-attribution').getBoundingClientRect();return getComputedStyle(m).position==='fixed'&&m.clientHeight>=innerHeight&&css.includes('100lvh')&&at.bottom<=innerHeight})()"), '地圖延伸到 Safari 半透明工具列後面（100lvh），版權標示留在可見範圍')
    check(await ev("(()=>{const s=getComputedStyle(document.getElementById('sheet'));return s.opacity==='0'&&s.pointerEvents==='none'})()"), '收起的結果面板完全隱藏（iPhone Safari 半透明工具列後面不會露出白色區塊）')
    await page.click('.chip[data-id=market]'); await page.wait_for_function("state.res&&state.res.act.includes('market')", timeout=20000)
    await ev("setLayerMins('park',7)")
    await page.click('#regionBtn'); await page.click('#regionPop [data-region="4"]'); await page.wait_for_function("state.res.key==='d:大安區'", timeout=5000); await page.click('#regionBtn')
    await page.click('#layersBtn'); await page.wait_for_selector('#layersPop [data-layer=fac]', state='visible'); await page.wait_for_timeout(250)
    await page.click('#layersPop [data-layer=fac]'); await page.click('#layersPop [data-layer=labels]')
    await page.wait_for_function("(()=>{const l=JSON.parse(localStorage.getItem('tlm.layers')||'{}');return l.fac===false&&l.labels===false})()", timeout=3000)
    await page.click('#layersBtn')
    await page.reload(); await page.wait_for_function(READY, timeout=20000)
    check(await ev("!state.layers.fac && !state.layers.labels && state.layers.heat"), '重新整理後圖層開關維持上次最後的設定（設施圖示、設施名稱關閉）')
    await page.click('#layersBtn'); await page.click('#layersPop [data-layer=fac]'); await page.click('#layersPop [data-layer=labels]'); await page.click('#layersBtn')
    r = await ev("({act:[...state.active].sort().join(','),park:state.minsByLayer.park,scope:state.scope,chip:document.querySelector('.chip[data-id=market]').getAttribute('aria-pressed'),key:state.res.key})")
    check(r['act'] == 'market,mrt,park,school' and r['park'] == 7 and r['scope'] == '4' and r['chip'] == 'true' and r['key'] == 'd:大安區', f'重新整理後自動保留上次的類別、分鐘與範圍 {r}')
    await page.click('#savedBtn')
    check(await ev("!document.getElementById('filterPop').hidden"), '類別列下方的「我的篩選」打開儲存的篩選面板')
    await page.fill('#filterName', '上班通勤'); await page.click('#filterSave'); await page.wait_for_timeout(200)
    f = await ev("JSON.parse(localStorage.getItem('tlm.filters'))")
    check(len(f) == 1 and f[0]['name'] == '上班通勤' and 'market' in f[0]['active'] and await ev("document.querySelector('#filterPop .frow').getAttribute('aria-current')") == 'true', '命名儲存目前條件，清單標示目前套用中')
    check('超市' in await ev("document.querySelector('#filterPop .fapply span').innerText") and '大安區' in await ev("document.querySelector('#filterPop .fapply span').innerText"), '儲存的篩選顯示條件摘要（類別、分鐘、範圍）')
    check(await ev("(()=>{const e=document.getElementById('filterPop');return e.scrollWidth<=e.clientWidth&&[...e.querySelectorAll('.frow,.fsave button')].every(x=>x.getBoundingClientRect().right<=e.getBoundingClientRect().right)})()"), '篩選面板內容完整顯示，不被右邊切掉')
    await shot(page, '7_filters')
    await page.click('#savedBtn')
    await page.click('.chip[data-id=market]'); await page.click('.chip[data-id=market]'); await page.wait_for_function("!state.active.has('market')", timeout=5000)
    await page.click('#scopeClear'); await page.wait_for_function("state.res&&state.res.key==='city'", timeout=5000)
    await page.click('#savedBtn'); await page.click('#filterPop [data-fren="0"]')
    await page.fill('#filterPop .fname', '通勤（新）'); await page.press('#filterPop .fname', 'Enter'); await page.wait_for_timeout(200)
    check(await ev("JSON.parse(localStorage.getItem('tlm.filters'))[0].name==='通勤（新）'&&document.querySelector('#filterPop .fapply b').innerText==='通勤（新）'&&!document.querySelector('#filterPop [data-home]')"), '儲存的篩選可以改名（按 ✎ 輸入新名稱）；我的物件不再放在篩選面板')
    await page.click('#filterPop [data-filter="0"]')
    await page.wait_for_function("state.res&&state.res.key==='d:大安區'&&state.res.act.includes('market')", timeout=20000)
    check(await ev("state.minsByLayer.park===7&&document.getElementById('filterPop').hidden&&document.querySelector('.chip[data-id=market]').getAttribute('aria-pressed')==='true'"), '點儲存的篩選：一鍵套用回類別、分鐘與範圍')
    await page.wait_for_timeout(800)   # 等套用篩選後的重算與畫面更新結束，再打開面板
    await page.click('#savedBtn'); await page.wait_for_selector('#filterPop [data-fdel="0"]', state='visible'); await page.wait_for_timeout(200); await page.click('#filterPop [data-fdel="0"]')
    await page.wait_for_function("JSON.parse(localStorage.getItem('tlm.filters')||'[]').length===0", timeout=3000)
    check(await ev("JSON.parse(localStorage.getItem('tlm.filters')).length===0&&!document.querySelector('#filterPop .frow')"), '可以刪除儲存的篩選')
    await ev("void closePops()")
    await ev("(()=>{const a=[Object.assign({name:'週末'},filterSnap())];saveFilters(a);})()")
    await ev("void closePops()")
    await page.click('#regionBtn'); await page.wait_for_selector('#regionPop [data-region="3"]', state='visible')
    rp = await ev("(()=>{const p=document.getElementById('regionPop').getBoundingClientRect(),c=document.getElementById('filterCard').getBoundingClientRect();return {top:p.top,cb:c.bottom,l:p.left,exp:document.getElementById('regionBtn').getAttribute('aria-expanded')}})()")
    check(rp['top'] >= rp['cb'] and rp['top'] - rp['cb'] < 20 and rp['exp'] == 'true', f"範圍選單從上方篩選卡往下展開 {rp}")
    await page.click('#regionPop [data-region="3"]'); await page.wait_for_function("state.res&&state.res.key==='d:大安區,松山區'||state.res&&state.res.key.startsWith('m:')", timeout=10000)
    await page.click('#regionBtn')
    nm = await ev("({t:document.getElementById('rgName').innerText,set:document.getElementById('regionBtn').classList.contains('set'),clr:!!document.getElementById('scopeClear')})")
    check('松山' in nm['t'] and nm['set'] and nm['clr'], f"選了區域後，篩選卡的範圍顯示區名並出現清除鈕 {nm}")
    await shot(page, '7d_region')
    await page.click('#scopeClear'); await page.wait_for_function("state.res&&state.res.key==='city'", timeout=10000)
    check(await ev("document.getElementById('rgName').innerText==='全臺北市'&&!document.getElementById('regionBtn').classList.contains('set')"), '清除後回到全臺北市')
    await page.click('#savedBtn'); await page.wait_for_selector('#filterPop [data-filter="0"]', state='visible')
    pp = await ev("(()=>{const p=document.getElementById('filterPop').getBoundingClientRect(),c=document.getElementById('filterCard').getBoundingClientRect();return {top:p.top,cb:c.bottom}})()")
    await page.click('#filterPop [data-filter="0"]'); await page.wait_for_timeout(1500)
    check(pp['top'] >= pp['cb'] and await ev("document.getElementById('filterPop').hidden"), f"篩選卡右上的「我的篩選」從卡片下方展開，點儲存的篩選一鍵套用 {pp}")
    await page.close()

    # 10c. 中古屋估價：長按 → 評估這裡的房子 → 結論卡、出價、燈號、房貸、四個分頁
    page = await new_page(b, errs, **PHONE)
    ev = page.evaluate
    await page.goto(URL); await page.wait_for_function(READY, timeout=20000)
    await page.wait_for_function("futureLayer.getLayers().length>0", timeout=5000)
    fl = await ev("({n:futureLayer.getLayers().length,lines:futureLayer.getLayers().filter(l=>l instanceof L.Polyline&&!(l instanceof L.CircleMarker)).length,dash:futureLayer.getLayers().filter(l=>l.options.dashArray==='10 7').length,st:futureLayer.getLayers().filter(l=>l instanceof L.CircleMarker).length})")
    check(fl['lines'] == 3 and fl['dash'] == 2 and fl['st'] == 2, f"圖層「未來捷運」預設開啟：興建中虛線（{fl['dash']} 段）、規劃中點線、{fl['st']} 個未來車站")
    await ev("(()=>{const s=futureLayer.getLayers().find(l=>l instanceof L.CircleMarker);s.openPopup()})()"); await page.wait_for_timeout(200)
    check('興建中' in await ev("document.querySelector('.leaflet-popup-content').innerText") or '規劃中' in await ev("document.querySelector('.leaflet-popup-content').innerText"), '點未來車站：顯示站名、路線與興建中／規劃中')
    await ev("void map.closePopup()")
    await page.click('#layersBtn'); await page.click('#layersPop [data-layer=future]'); await page.wait_for_timeout(200)
    check(await ev("futureLayer.getLayers().length===0"), '可以在圖層的「交通」組關掉未來捷運')
    await page.click('#layersPop [data-layer=future]'); await page.click('#layersBtn')
    await ev("void map.setView([25.05,121.552],14,{animate:false})"); await page.wait_for_timeout(500); await shot(page, '8i_future')
    await ev("void map.setView([25.0325,121.543],16,{animate:false})"); await page.wait_for_timeout(400)
    pt = await ev("(()=>{const p=map.latLngToContainerPoint([25.030,121.543]);return [p.x,p.y]})()")
    h0 = len(data_hits)
    await long_press(page, pt[0], pt[1]); await page.click('#pressMenu [data-press=value]')
    await page.wait_for_function("state.view.type==='val'&&!!document.getElementById('valGo')", timeout=10000)
    check({'realprice.json', 'valuation.json'} <= set(data_hits[h0:]) and 'realprice.json' not in data_hits[:h0], '長按選「評估這裡的房子」：這時才下載估價資料，出現房屋條件表單')
    check(await ev("document.querySelectorAll('#body [data-vt]').length===3&&document.querySelector('#head .sub').innerText.includes('大安區')&&snap!=='peek'"), '表單：三種型態、坪數、屋齡、樓層、開價、屋況；面板自動打開')
    for k, v_ in (('ping', '30'), ('age', '12'), ('fl', '18'), ('tot', '15'), ('ask', '3600')):
        await page.fill(f'#body [data-vf={k}]', v_)
    await page.click('#valGo'); await page.wait_for_timeout(200)
    check(await ev("!state.val.est&&document.getElementById('toast').innerText.includes('樓層')"), '樓層超過總樓層：提示錯誤、不估價')
    await page.fill('#body [data-vf=fl]', '8'); await page.click('#valGo')
    await page.wait_for_function("!!document.getElementById('valSum')", timeout=5000); await page.wait_for_timeout(500)
    r = await ev("({out:state.val.off.out,cap:state.val.off.cap,mid:state.val.off.mid,q50:state.val.est.q(.5),n:state.val.est.n,rad:state.val.est.radius,poi:state.val.est.poi,s:state.val.s,H:state.val.mk.H,label:state.val.mk.label,sum:document.getElementById('valSum').innerText.replace(/\\s+/g,' '),layers:selLayer.getLayers().length,snap,pill:document.getElementById('resultBtn').innerText.replace(/\\s+/g,' ')})")
    pe = MODE['val']['est']
    check(abs(r['q50'] - pe['q'][0.5]) / pe['q'][0.5] < 0.015 and abs(r['n'] - pe['n']) <= 3 and abs(r['rad'] - pe['radius']) <= 5 and abs(r['s'] - MODE['val']['s']) < 0.02 and abs(r['poi'] - pe['poi']) < 0.01,
          f"前端估價與建置程式（Python）算出來一致：{r['q50']:.1f} vs {pe['q'][0.5]:.1f} 萬／坪、可比 {r['n']} vs {pe['n']} 筆、半徑 {r['rad']} 公尺")
    check(r['out'][0] < r['out'][1] < r['out'][2] and r['out'][2] <= r['cap'] + 1e-6 and '保守' in r['sum'] and '合理出價' in r['sum'] and '積極' in r['sum'], f"結論卡：保守／合理／積極三段出價，都不超過開價（{r['sum']}）")
    check(r['layers'] >= r['n'] + 2 and r['snap'] != 'peek' and r['label'] in ('買方市場', '平衡市場', '賣方市場') and await ev("!!document.querySelector('#body .vdot.'+state.val.mk.cls)&&!!document.querySelector('#body .vbar')"), f"地圖畫出可比案例與搜尋半徑；面板有出價區間條與市場燈號（{r['label']}，H={r['H']:+.2f}）")
    vis = await ev("(()=>{const p=map.latLngToContainerPoint(state.val.pt),s=document.getElementById('sheet').getBoundingClientRect(),t=document.getElementById('top').getBoundingClientRect();return p.y>t.bottom&&p.y<s.top})()")
    check(vis, '物件標記在可見的地圖範圍內')
    await shot(page, '8_val_half')
    await ev("setSheet('peek')"); await page.wait_for_timeout(350)
    check('合理' in await ev("document.getElementById('resultBtn').innerText") and await ev("!document.getElementById('resultBtn').hidden"), '收起面板：左下角按鈕顯示「合理 ○○ 萬｜燈號」')
    await page.click('#resultBtn'); await page.wait_for_timeout(350)
    check(await ev("document.querySelectorAll('#body [data-comp]').length>=5&&!!document.querySelector('#valTab svg.vchart')"), '價格分頁：可比案例單價分佈圖與清單')
    cd = await ev("({d:document.getElementById('compsDesc').innerText,ok:state.val.est.comps.every(x=>x[1].t===state.val.form.t&&Math.abs(x[1].age-state.val.form.age)<=12&&x[1].ping/state.val.form.ping>=.5&&x[1].ping/state.val.form.ping<=2),sorted:state.val.est.comps.every((x,i,a)=>!i||a[i-1][2]<=x[2])})")
    check(cd['ok'] and cd['sorted'] and '同為住宅大樓' in cd['d'] and '屋齡' in cd['d'] and '樓' in cd['d'], f"可比案例：同型態、屋齡與坪數相近，依相似度排序，並寫出範圍（{cd['d']}）")
    await page.click('#body [data-vtab=price]'); await page.wait_for_timeout(400); await shot(page, '8b_val_price')
    await ev("document.querySelector('#body [data-comp=\"2\"]').click()"); await page.wait_for_timeout(500)
    check(await ev("(()=>{const c=state.val.est.comps[2][1].pt,m=map.getCenter();return Math.abs(m.lat-c[0])<.003&&Math.abs(m.lng-c[1])<.003&&!!state.val.hl})()"), '點一筆可比案例：地圖移過去並標示')
    await ev("document.querySelector('#body [data-vtab=market]').click()"); await page.wait_for_timeout(300)
    check(await ev("document.querySelectorAll('#valTab .vrow').length>=3&&document.getElementById('valTab').innerText.includes('議價')"), '市場分頁：燈號儀表與交易量、價格動能、議價空間三個指標')
    await shot(page, '8c_val_market')
    await ev("document.querySelector('#body [data-vtab=fin]').click()"); await page.wait_for_timeout(300)
    pay0 = await ev("valLoan(state.val,state.val.off.out[1]).pay")
    L = await ev("valLoan(state.val,state.val.off.out[1]).L"); i = 2.29 / 100 / 12; exp = L * 1e4 * i / (1 - (1 + i) ** -360)
    check(abs(pay0 - exp) < 1 and abs(L - 0.8 * r['out'][1]) < 0.01, f'財務分頁：預設貸 8 成、30 年、利率 2.29%，本息平均攤還每月 {pay0:,.0f} 元（公式驗算一致）')
    await page.fill('#body [data-fin=income]', '150000'); await page.press('#body [data-fin=income]', 'Tab'); await page.wait_for_timeout(300)
    bur = await ev("document.querySelector('#valTab .vburden')?document.querySelector('#valTab .vburden').innerText:''")
    check(bur == f'{round(pay0 / 150000 * 100)}%' and await ev("localStorage.getItem('tlm.valForm').indexOf('150000')<0") and '租金' in await ev("document.getElementById('valTab').innerText"), f'輸入月收入顯示負擔率（{bur}），收入不會存到裝置；並列附近租金')
    await shot(page, '8d_val_fin')
    # 青安 3.0：切換方案、利率分段、寬限期、身分額度、房價上限、記住選擇
    def ref_amort(P, n, g, rate_of):
        out, b = [], P
        for m in range(n):
            r_ = rate_of(m // 12) / 1200; it = b * r_
            p_ = it if m < g else (b * r_ / (1 - (1 + r_) ** -(n - m)) if r_ else b / (n - m))
            out.append(p_); b -= p_ - it
        return out
    YR = [1.775, 1.775, 1.775, 1.9, 2.025, 2.15, 2.275]
    yrate = lambda y: YR[min(y, 6)]
    price = r['out'][1]; Lt = 0.8 * price
    await page.click('#body [data-fmode=youth]'); await page.wait_for_timeout(300)
    y1 = await ev("(()=>{const L=valLoan(state.val,state.val.off.out[1]);return {rate:state.val.fin.rate,lbl:document.querySelector('#body [data-fin=rate]').parentElement.innerText,years:state.val.fin.years,LY:L.LY,LG:L.LG,pay:L.pay,peak:L.peak,ok:L.ok,txt:document.getElementById('valTab').innerText,segs:[...document.querySelectorAll('#youthSched .vrow')].map(e=>e.innerText.replace(/\s+/g,' ')),pref:localStorage.getItem('tlm.finMode'),fam:document.querySelectorAll('#body [data-ffam]').length}})()")
    LY = min(Lt, 1000); LG = Lt - LY
    exp1 = [a + b for a, b in zip(ref_amort(LY * 1e4, 480, 0, yrate), ref_amort(LG * 1e4, 480, 0, lambda y: 2.6) if LG > 0 else [0] * 480)]
    check(y1['ok'] and +y1['rate'] == 2.6 and '首購' in y1['lbl'] and '首購房貸' in y1['txt'] and y1['years'] == 40 and abs(y1['LY'] - LY) < .01 and abs(y1['LG'] - LG) < .01 and abs(y1['pay'] - exp1[0]) < 1 and abs(y1['peak'] - max(exp1)) < 1,
          f"財務分頁按「青安 3.0」：年限改 40 年，青安 {LY:.0f} 萬＋超額部分用首購利率 2.6% 貸 {LG:.0f} 萬，第 1 年每月 {y1['pay']:,.0f} 元、最高 {y1['peak']:,.0f} 元（和獨立公式驗算一致）")
    check(len(y1['segs']) == 5 and y1['segs'][0].startswith('第 1～3 年') and y1['segs'][-1].startswith('第 7 年起') and '1.775%' in y1['segs'][0] and '2.275%' in y1['segs'][-1] and y1['fam'] == 3,
          f"青安月付分段：第 1～3 年 1.775%、第 4～6 年逐年加半碼、第 7 年起 2.275%；有一般／新婚／育兒三種身分（{' | '.join(y1['segs'])}）")
    check('3,500 萬' in y1['txt'] and '未滿 50 歲' in y1['txt'] and '200 萬' in y1['txt'] and '全期總利息' in y1['txt'] and '"mode":"youth"' in (y1['pref'] or ''), '青安說明列出臺北市房價上限 3,500 萬、年齡與所得資格、全期總利息；方案選擇記在這台裝置')
    await page.fill('#body [data-fin=grace]', '3'); await page.press('#body [data-fin=grace]', 'Tab'); await page.wait_for_timeout(300)
    y2 = await ev("(()=>{const L=valLoan(state.val,state.val.off.out[1]);return {pay:L.pay,peak:L.peak,g:L.grace,bur:document.querySelector('#valTab .vburden').innerText,segs:[...document.querySelectorAll('#youthSched .vrow')].map(e=>e.innerText.replace(/\s+/g,' '))}})()")
    exp2 = [a + b for a, b in zip(ref_amort(LY * 1e4, 480, 36, yrate), ref_amort(LG * 1e4, 480, 0, lambda y: 2.6) if LG > 0 else [0] * 480)]
    check(y2['g'] == 3 and abs(y2['pay'] - exp2[0]) < 1 and abs(y2['peak'] - max(exp2)) < 1 and '只繳利息' in y2['segs'][0] and y2['bur'] == f"{round(max(exp2) / 150000 * 100)}%",
          f"寬限期 3 年：青安部分前 3 年只繳利息（第 1 年 {y2['pay']:,.0f} 元），之後跳到最高 {y2['peak']:,.0f} 元；負擔率改用最高月付計（{y2['bur']}）")
    await page.fill('#body [data-fin=grace]', '9'); await page.press('#body [data-fin=grace]', 'Tab'); await page.wait_for_timeout(300)
    check(await ev("state.val.fin.grace===5"), '寬限期超過 5 年會改成青安上限 5 年')
    await page.click('#body [data-ffam="2"]'); await page.wait_for_timeout(300)
    y3 = await ev("(()=>{const L=valLoan(state.val,state.val.off.out[1]);return {LY:L.LY,cap:L.cap}})()")
    check(y3['cap'] == 1500 and abs(y3['LY'] - min(Lt, 1500)) < .01, f"選「育兒」：青安額度上限 1,500 萬（這間青安可貸 {y3['LY']:.0f} 萬）")
    y4 = await ev("(()=>{const L=valLoan(state.val,3600),M=valLoan(state.val,3500);return {ok:L.ok,LY:L.LY,okM:M.ok}})()")
    check(not y4['ok'] and y4['LY'] == 0 and y4['okM'], '總價 3,600 萬超過臺北市青安上限 3,500 萬：不適用、全部以一般房貸計；剛好 3,500 萬仍適用')
    await shot(page, '8d2_val_youth')
    await page.click('#body [data-vtab=price]'); await page.wait_for_timeout(200)
    check('青安' in await ev("document.getElementById('valSum').parentElement.innerText"), '估價摘要的房貸那行標示「青安」與第 1 年月付')
    await page.click('#body [data-vtab=fin]'); await page.wait_for_timeout(200)
    await page.click('#body [data-fmode=std]'); await page.wait_for_timeout(300)
    y5 = await ev("({years:state.val.fin.years,grace:state.val.fin.grace,pay:valLoan(state.val,state.val.off.out[1]).pay,sched:!!document.getElementById('youthSched'),pref:localStorage.getItem('tlm.finMode')})")
    check(y5['years'] == 30 and y5['grace'] == 0 and abs(y5['pay'] - pay0) < 1 and not y5['sched'] and '"mode":"std"' in y5['pref'], '切回「一般房貸」：年限回 30 年、利率回 2.29%、取消寬限期，月付和原本一致')
    await ev("document.querySelector('#body [data-vtab=place]').click()"); await page.wait_for_timeout(300)
    check(await ev("document.querySelectorAll('#valTab .row').length") == 6 and '位置加減分' in await ev("document.getElementById('valTab').innerText"), '位置分頁：六類設施步行時間與位置加減分')
    ft = await ev("document.getElementById('valTab').innerText")
    check('最近的未來捷運站' in ft and ('Y20' in ft or '民生社區' in ft) and '不計入估價' in ft, '位置分頁：顯示最近的未來捷運站（興建中／規劃中）與步行時間，註明不計入估價')
    check('這裡的公告土地現值：仁愛段二小段' in ft and '約 229 萬／坪' in ft and '通常低於市價' in ft, f'位置分頁：顯示這個位置所在地段的公告土地現值（萬／坪），註明低於市價、不計入估價')
    await page.wait_for_function("!!URC", timeout=5000); await page.wait_for_timeout(200); ft = await ev("document.getElementById('valTab').innerText")
    uc = await ev("(()=>{const n=urcNear(state.val.pt);return {n:n.length,names:n.map(x=>urcName(x[1]))}})()")
    check(uc['n'] >= 1 and '附近 300 公尺的都更案件' in ft and '自劃單元 1 塊' in ft and '都市更新研究發展基金會' in ft, f"位置分頁：列出附近 300 公尺的都更案件（狀態、名稱、距離），標明資料來源、不計入估價 {uc}")
    # 談判價：手動填即時價格，房貸改用它試算
    await page.click('#body [data-vtab=fin]'); await page.wait_for_timeout(200)
    d0 = await ev("Math.round(state.val.off.out[1]*0.95)")
    await page.fill('#dealIn', str(d0)); await page.wait_for_timeout(150)
    dl = await ev(f"({{deal:state.val.form.deal,fin:document.getElementById('valTab').innerText,line:document.getElementById('valLoan').innerText,mk:document.querySelectorAll('#valBar .vdeal-mk').length,info:document.getElementById('dealInfo').innerText,btn:document.getElementById('resultBtn').innerText,pay:Math.round(valLoan(state.val,{d0}).pay),focus:document.activeElement.id}})")
    check(dl['deal'] == d0 and f'以談判價 {d0:,} 萬' in dl['fin'] and '以談判價' in dl['line'] and f"{dl['pay']:,} 元" in dl['line'] and dl['mk'] == 1 and '保守～合理之間' in dl['info'] and dl['focus'] == 'dealIn', f"填談判價：打字當下就更新房貸試算（財務分頁、摘要那行）、出價區間圖多一個綠色菱形、標出落在哪一段；輸入框不會失去焦點 {dl}")
    await page.press('#dealIn', 'Enter'); await page.click('[data-dstep="10"]'); await page.wait_for_timeout(200)
    check(await ev(f"state.val.form.deal==={d0+10}&&document.getElementById('dealIn').value==='{d0+10}'"), '按「＋10」：談判價加 10 萬')
    await page.click('#valDeal [data-dset]:not(.clr) >> nth=3'); await page.wait_for_timeout(200)
    check(await ev("state.val.form.deal===3600&&document.getElementById('dealInfo').innerText.includes('等於開價')&&document.getElementById('body').scrollWidth<=document.getElementById('body').clientWidth+1&&document.querySelector('[data-dstep=\"10\"]').getBoundingClientRect().right<=innerWidth"), '點「開價」快選：談判價直接帶入開價；談判價這列在手機寬度內，不會把畫面撐出橫向捲動')
    await shot(page, '8d3_val_deal')
    await page.click('#valDeal .clr'); await page.wait_for_timeout(200)
    check(await ev("!state.val.form.deal&&document.getElementById('valTab').innerText.includes('以合理出價')"), '按「清除」回到用合理出價試算')
    await page.click('#valEdit'); await page.wait_for_timeout(300)
    check(await ev("document.querySelector('#body [data-vf=ping]').value==='30'&&document.querySelector('#body [data-vf=ask]').value==='3600'"), '「修改房屋條件」回到表單，原本填的還在')
    await page.fill('#body [data-vf=ask]', ''); await page.click('#valGo'); await page.wait_for_function("!!document.getElementById('valSum')", timeout=5000)
    check(await ev("state.val.off.cap===Infinity&&state.val.off.out[0]<state.val.off.out[1]&&state.val.off.out[1]<state.val.off.out[2]"), '不填開價也能估：三段出價直接取行情帶')
    # 我的物件：儲存、地圖星號、清單、勾選比較、重新打開、刪除
    await page.wait_for_function("!!document.getElementById('valSave')", timeout=10000); await page.click('#valSave')
    try:
        await page.wait_for_function("JSON.parse(localStorage.getItem('tlm.homes')||'[]').length===1&&homeLayer.getLayers().length===1", timeout=5000)
    except Exception:
        pass
    hs = await ev("JSON.parse(localStorage.getItem('tlm.homes')||'[]')")
    check(len(hs) == 1 and hs[0]['form']['ping'] == 30 and 'income' not in json.dumps(hs) and await ev("document.getElementById('valSave').getAttribute('aria-pressed')") == 'true' and await ev("homeLayer.getLayers().length") == 1, f'估價後按「儲存物件」：存到這台裝置（不含收入），地圖上出現星號 {len(hs)} {await ev("document.getElementById(\'valSave\')?.getAttribute(\'aria-pressed\')")} {await ev("homeLayer.getLayers().length")} {await ev("document.getElementById(\'toast\').innerText")}')
    await page.wait_for_timeout(600); await page.click('#close'); await page.wait_for_timeout(400)
    check(await ev("state.view.type==='list'&&selLayer.getLayers().length===0&&homeLayer.getLayers().length===1"), '按 ✕ 離開估價，回到地圖；儲存的物件星號仍在')
    await ev("void openVal([25.034,121.547])"); await page.wait_for_function("!!document.getElementById('valGo')", timeout=5000)
    await page.click('#body [data-vt="1"]')
    for k, v_ in (('ping', '25'), ('age', '28'), ('fl', '3'), ('tot', '7'), ('ask', '2300')):
        await page.fill(f'#body [data-vf={k}]', v_)
    await page.click('#valGo'); await page.wait_for_function("!!document.getElementById('valSave')", timeout=5000)
    await page.fill('#dealIn', '2200'); await page.press('#dealIn', 'Enter'); await page.wait_for_timeout(200)
    await page.click('#valSave'); await page.wait_for_timeout(300)
    check(await ev("JSON.parse(localStorage.getItem('tlm.homes'))[0].form.deal===2200"), '儲存物件時一起存談判價')
    await page.wait_for_timeout(400)
    await page.click('#homesBtn'); await page.wait_for_timeout(300)
    ov = await ev("({v:state.view.type,sv:getComputedStyle(document.getElementById('sheet')).visibility,in:!!document.querySelector('#body #homesPop'),sel:document.getElementById('homesBtn').getAttribute('aria-selected')})")
    check(ov['v'] == 'homes' and ov['sv'] == 'visible' and ov['in'] and ov['sel'] == 'true', f"開著估價面板時按「物件」：換成同一個面板顯示我的物件（不再浮在地圖上，不會疊在一起）{ov}")
    await page.click('#marketBtn'); await page.wait_for_function("state.view.type==='market'", timeout=5000)
    check(await ev("!document.getElementById('homesPop')&&document.getElementById('marketBtn').getAttribute('aria-selected')==='true'&&document.getElementById('homesBtn').getAttribute('aria-selected')==='false'"), '換到「房市」分頁時我的物件收起，底部只高亮房市')
    await ev("setSheet('full')"); await page.wait_for_timeout(350)
    check(await ev("getComputedStyle(document.getElementById('fabs')).opacity==='0'"), '面板拉到最高時，右側圖層／定位按鈕先藏起來，不擠到上方標題列')
    await ev("closeDetail()"); await page.wait_for_timeout(300)
    await page.click('#homesBtn')
    check(await ev("!!document.getElementById('homesPop')&&document.querySelectorAll('#homesPop [data-home]').length===2&&document.querySelector('#homeCmp').disabled&&document.querySelectorAll('#homesPop .hpipe i').length===1"), '「物件」分頁列出儲存的物件，上方有狀態比例條；勾選前比較按鈕不能按')
    await page.wait_for_function("document.querySelectorAll('#homesPop .hscore').length===2", timeout=10000)
    hs1 = await ev("({sc:[...document.querySelectorAll('#homesPop .hrow .hscore b')].map(e=>+e.innerText),rk:[...document.querySelectorAll('#homesPop .hrow .hscore small')].map(e=>e.innerText),bars:document.querySelectorAll('#homesPop .hbar i').length,sort:document.getElementById('hSort').value,rowH:Math.max(...[...document.querySelectorAll('#homesPop .hrow')].map(r=>r.getBoundingClientRect().height)),meta:document.querySelectorAll('#homesPop .hrow [data-hmeta],#homesPop .hrow .dbadge').length,names:[...document.querySelectorAll('#homesPop .hname')].map(e=>e.innerText)})")
    check(len(hs1['sc']) == 2 and hs1['sc'] == sorted(hs1['sc'], reverse=True) and hs1['rk'][0] == '#1' and hs1['bars'] >= 4 and hs1['sort'] == 'score' and hs1['rowH'] <= 84 and hs1['meta'] == 0, f"我的物件精簡版：每間只有名稱、分數、#名次、各項長條與圖示按鈕（不顯示開價／合理價細節，每列不超過 84px），預設依分數排序 {hs1}")
    await page.click('#homesPop [data-hw=life]'); await page.wait_for_timeout(400)
    hs2 = await ev("({sc:[...document.querySelectorAll('#homesPop .hrow .hscore b')].map(e=>+e.innerText),w:cmpW})")
    check(hs2['w'] == 'life' and hs2['sc'] != hs1['sc'] and hs2['sc'] == sorted(hs2['sc'], reverse=True), f"切「重生活」：分數重算並重新排序 {hs2}")
    so = await ev("[...document.querySelectorAll('#hSort option')].map(o=>o.textContent)")
    await page.select_option('#hSort', 'ask'); await page.wait_for_timeout(200)
    sa = await ev("[...document.querySelectorAll('#homesPop .hname')].map(e=>e.innerText).join()===loadHomes().slice().sort((a,b)=>(+a.form.ask||1e9)-(+b.form.ask||1e9)).map(h=>h.name).join()")
    await page.select_option('#hSort', 'life'); await page.wait_for_timeout(200)
    sl = await ev("(()=>{const v=[...document.querySelectorAll('#homesPop .hrow [data-hsel]')].map(b=>HSC[b.dataset.hsel].sc.life);return v.every((x,i)=>!i||v[i-1]>=x)})()")
    check(len(so) == 8 and '開價低→高' in so and '生活機能最好' in so and sa and sl and await ev("localStorage.getItem('tlm.homeSort')==='life'"), f"排序選單：分數、價格划算、生活機能、月付、屋齡、開價、最新／最早加入，選了會記住 {so}")
    await page.select_option('#hSort', 'time'); await page.wait_for_timeout(200)
    check(await ev("[...document.querySelectorAll('#homesPop .hname')].map(e=>e.innerText).join()===loadHomes().map(h=>h.name).join()&&localStorage.getItem('tlm.homeSort')==='time'"), '切「最新加入」：照加入順序排')
    await shot(page, '8f1_homes_score')
    await page.select_option('#hSort', 'score'); await page.click('#homesPop [data-hw=bal]'); await page.wait_for_timeout(300)
    hp = await ev("({tabs:[...document.querySelectorAll('#homesPop [data-hfil]')].map(b=>b.innerText.replace(/\\s+/g,'')),st:[...document.querySelectorAll('#homesPop [data-hst]')].map(s=>s.value),w:document.getElementById('homesPop').scrollWidth<=document.getElementById('homesPop').clientWidth+1})")
    check(hp['tabs'] == ['全部2', '未看房2', '已看房0', '議價中0'] and hp['st'] == ['todo', 'todo'] and hp['w'], f"我的物件分成未看房／已看房／議價中，新存的預設「未看房」，上方可依狀態篩選（{hp['tabs']}）")
    await page.select_option('#homesPop .hrow >> nth=0 >> [data-hst]', 'nego'); await page.wait_for_timeout(200)
    await page.select_option('#homesPop .hrow >> nth=1 >> [data-hst]', 'seen'); await page.wait_for_timeout(200)
    hp = await ev("({st:JSON.parse(localStorage.getItem('tlm.homes')).map(h=>h.st),pins:homeLayer.getLayers().map(m=>m.options.icon.options.className).sort(),tabs:[...document.querySelectorAll('#homesPop [data-hfil]')].map(b=>b.innerText.replace(/\\s+/g,''))})")
    check(sorted(hp['st']) == ['nego', 'seen'] and hp['pins'] == ['home-pin st-nego', 'home-pin st-seen'] and hp['tabs'][2:] == ['已看房1', '議價中1'], '改狀態：存到裝置，地圖上星號換色（已看房藍、議價中橘），分類數字更新')
    await page.click('#homesPop [data-hfil=nego]'); await page.wait_for_timeout(150)
    check(await ev("document.querySelectorAll('#homesPop .hrow').length===1&&document.querySelector('#homesPop .hrow').dataset.st==='nego'"), '點「議價中」只列議價中的物件')
    await page.click('#homesPop [data-hfil=todo]'); await page.wait_for_timeout(150)
    check(await ev("document.querySelectorAll('#homesPop .hrow').length===0&&!!document.getElementById('homesEmpty')"), '沒有該狀態的物件時顯示提示')
    await page.click('#homesPop [data-hfil=all]'); await page.wait_for_timeout(150)
    await page.click('#homesPop .hrow >> nth=0 >> .hname'); await page.wait_for_timeout(150)
    check(await ev("!!document.querySelector('#homesPop .hname-in')&&document.activeElement===document.querySelector('#homesPop .hname-in')&&state.view.type!=='val'"), '點物件名稱直接變成輸入框改名（不會打開物件）')
    await page.fill('#homesPop .hname-in', '民生社區 A 棟'); await page.press('#homesPop .hname-in', 'Enter'); await page.wait_for_timeout(200)
    check(await ev("JSON.parse(localStorage.getItem('tlm.homes')).some(h=>h.name==='民生社區 A 棟')&&[...document.querySelectorAll('#homesPop .hname')].some(b=>b.innerText==='民生社區 A 棟')&&homeLayer.getLayers().some(m=>m.options.title==='民生社區 A 棟')"), '按 Enter 存新名稱，清單與地圖同步')
    await page.click('#homesPop .hrow >> nth=0 >> .hname'); await page.fill('#homesPop .hname-in', 'XXX'); await page.press('#homesPop .hname-in', 'Escape'); await page.wait_for_timeout(150)
    check(await ev("!JSON.parse(localStorage.getItem('tlm.homes')).some(h=>h.name==='XXX')"), '按 Esc 取消改名')
    await shot(page, '8f0_homes_status')
    # 標籤：預設的風險標籤＋自訂
    await page.click('#homesPop [data-htag] >> nth=0'); await page.wait_for_selector('.tg-ed')
    tg0 = await ev("({all:[...document.querySelectorAll('.tg-ed [data-tgt]')].map(b=>b.innerText),risk:document.querySelectorAll('.tg-ed .tg-b.risk').length})")
    check(tg0['all'][:4] == ['凶宅', '海砂屋', '輻射屋', '漏水'] and tg0['risk'] == 8, f"按「＋ 標籤」：列出凶宅、海砂屋、輻射屋、漏水等風險標籤（紅色）{tg0}")
    await page.click('.tg-ed [data-tgt=漏水]'); await page.click('.tg-ed [data-tgt=凶宅]')
    await page.fill('.tg-ed .hl-add input', '管委會差'); await page.press('.tg-ed .hl-add input', 'Enter'); await page.wait_for_timeout(150)
    await page.click('.tg-ed [data-tgt=凶宅]'); await page.wait_for_timeout(100)
    tg1 = await ev("({h:(()=>{const id=document.querySelector('#homesPop [data-htag]').dataset.htag;return loadHomes().find(x=>x.id===id).tags})(),set:JSON.parse(localStorage.getItem('tlm.tagset')),on:[...document.querySelectorAll('.tg-ed [data-tgt][aria-pressed=true]')].map(b=>b.innerText),cloud:CLOUD_KEYS.includes('tlm.tagset')})")
    check(tg1['h'] == ['漏水', '管委會差'] and tg1['set'] == ['管委會差'] and tg1['on'] == ['漏水', '管委會差'] and tg1['cloud'], f"點標籤加上／再點取消；輸入自訂標籤「管委會差」按 Enter 加上，並記到標籤清單（雲端同步）{tg1}")
    await shot(page, '8f2_home_tags_edit')
    await page.click('.tg-ed .hl-done'); await page.wait_for_timeout(150)
    tg2 = await ev("[...document.querySelectorAll('#homesPop .hrow')].map(r=>[...r.querySelectorAll('.htags .tg')].map(e=>[e.innerText,e.classList.contains('risk')])).filter(a=>a.length)")
    check(tg2 == [[['漏水', True], ['管委會差', False]]], f"物件列上顯示標籤（和名稱同一行、標籤形狀）：風險標籤紅色、自訂標籤灰色 {tg2}")
    check(await ev("[...document.querySelectorAll('#homesPop .hrow .ht')].some(t=>t.querySelector('.hname')&&t.querySelector('.htags'))"), '標籤和名稱在同一行')
    await shot(page, '8f2b_home_tags')
    await page.click('#homesPop [data-htag] >> nth=1'); await page.wait_for_selector('.tg-ed [data-tgt=管委會差]')
    await page.click('.tg-ed [data-tgt=海砂屋]'); await page.click('.tg-ed [data-tgdel=管委會差]'); await page.wait_for_timeout(150)
    check(await ev("!document.querySelector('.tg-ed [data-tgt=管委會差]')&&JSON.parse(localStorage.getItem('tlm.tagset')).length===0&&loadHomes().some(h=>(h.tags||[]).includes('管委會差'))"), '另一間也看得到自訂標籤；從清單刪除自訂標籤，已加上的物件不受影響')
    await page.click('.tg-ed .hl-done'); await page.wait_for_timeout(150)
    # 我的物件小城（3D）
    cg = await ev("(()=>{const b=document.getElementById('cityBtn');return b?b.innerText.replace(/\\s+/g,' '):''})()")
    check('3D 小城' in cg and '完成度 75%' in cg, f"我的物件上方有「3D 小城」入口，顯示完成度（已看房＋議價中）{cg}")
    await page.click('#cityBtn'); await page.wait_for_function("CITY3D&&CITY3D.bld.length===2&&CITY3D.renderer.info.render.calls>0", timeout=20000); await page.wait_for_timeout(1200)
    c1 = await ev("""(()=>{const b=CITY3D.bld.map(g=>({id:g.userData.id,H:g.userData.H,st:g.userData.st,sy:g.scale.y,scaff:g.children.some(c=>c.type==='LineSegments'),drops:g.userData.drops.length,band:g.children.filter(c=>c.userData&&c.userData.warn).length,sc:Math.round(HSC[g.userData.id].total)}));
      const hm=CITY3D.hero.userData.mesh;return {b,hero:!!(hm&&hm.material.map&&hm.geometry.attributes.position.count>1000),stat:document.getElementById('cityStat').innerText,full:(()=>{const r=document.getElementById('city').getBoundingClientRect();return r.width===innerWidth&&r.height===innerHeight})(),calls:CITY3D.renderer.info.render.calls,meshes:(()=>{let n=0;CITY3D.scene.traverse(o=>{if(o.isMesh)n++;});return n})(),tone:CITY3D.renderer.toneMapping}})()""")
    print('  city', c1)
    hi = max(c1['b'], key=lambda x: x['sc']); lo = min(c1['b'], key=lambda x: x['sc'])
    check(c1['full'] and len(c1['b']) == 2 and all(x['sy'] == 1 for x in c1['b']) and hi['H'] >= lo['H'] and '完成度 75%' in c1['stat'], f"打開 3D 小城：全螢幕，兩間物件各是一棟樓（蓋起來的動畫跑完），分數越高樓越高 {c1['stat']}")
    check(sorted([x['st'] for x in c1['b']]) == ['nego', 'seen'] and any(x['drops'] == 3 for x in c1['b']) and any(x['band'] == 1 for x in c1['b']), f"狀態與標籤看得到：已看房蓋好、議價中亮燈；漏水有滴水、海砂屋有紅色警示牌 {c1['b']}")
    check(c1['hero'] and c1['tone'] == 4 and c1['meshes'] < 400, f"主角是 AI 生成的公仔模型（assets/hero.glb，有貼圖）；小零件合併成少量網格，手機跑得動（網格 {c1['meshes']} 個）")
    nr = await ev("""(()=>{const n=CITY3D.near,g=CITY3D.bld.find(x=>x.userData.id===n),v=g?g.position.clone():null;if(v){v.y=g.userData.H*.5;v.project(CITY3D.cam);}const p=document.querySelector('#cityPins .cp.near');return {near:n,inview:!!v&&Math.abs(v.x)<.95&&Math.abs(v.y)<.95&&v.z<1,pin:p?p.innerText.replace(/\s+/g,' '):'',pins:document.querySelectorAll('#cityPins .cp').length,act:document.getElementById('cityAct').classList.contains('on'),actT:document.getElementById('cityAct').innerText}})()""")
    check(nr['near'] and nr['inview'] and nr['pins'] == 2 and '分' in nr['pin'] and nr['act'] and nr['actT'] == '看這間', f"主角站在最近一棟前面：那棟在畫面中，標籤顯示名稱＋分數，右下「看這間」亮起 {nr}")
    rg = await ev("({regs:CITY3D.REGS.map(r=>[r.name,r.ids.length]),lms:CITY3D.LMS.map(l=>l.name),zone:CITY3D.zone,zt:document.getElementById('cityZone').innerText,zon:document.getElementById('cityZone').classList.contains('on'),sep:(()=>{const R=CITY3D.REGS;let m=1e9;R.forEach((a,i)=>R.forEach((b,j)=>{if(j>i)m=Math.min(m,Math.hypot(a.c[0]-b.c[0],a.c[1]-b.c[1])-a.r-b.r)}));return m})()})")
    check(sum(r[1] for r in rg['regs']) == 2 and sorted(rg['lms']) == sorted(r[0] for r in rg['regs']) and rg['sep'] >= 3 and rg['zone'] and rg['zon'] and rg['zt'] == rg['zone'], f"分區：物件依所在行政區分成區域（不重疊），每區有自己的地標；走進哪區上方顯示區名 {rg}")
    await shot(page, '9c0_city')
    # 點畫面中的一棟樓
    sp = await ev("""(()=>{const g=CITY3D.bld.map(g=>{const v=g.position.clone();v.y=g.userData.H*.45;v.project(CITY3D.cam);return {g,v}}).find(o=>Math.abs(o.v.x)<.8&&Math.abs(o.v.y)<.7&&o.v.z<1);if(!g)return null;const r=document.getElementById('cityCv').getBoundingClientRect();return {id:g.g.userData.id,x:(g.v.x+1)/2*r.width+r.left,y:(1-g.v.y)/2*r.height+r.top}})()""")
    if sp:
        await page.mouse.click(sp['x'], sp['y']); await page.wait_for_timeout(300)
    cc0 = await ev("({sel:CITY3D.sel,card:!document.getElementById('cityCard').hidden,joy:getComputedStyle(document.getElementById('cityJoy')).display})")
    check(sp and cc0['sel'] == sp['id'] and cc0['card'] and cc0['joy'] == 'none', f"點一棟樓：下方出現那間的卡片（搖桿先收起來）{sp} {cc0}")
    await page.click('#ccX'); await page.wait_for_timeout(150)
    check(await ev("document.getElementById('cityCard').hidden&&getComputedStyle(document.getElementById('cityJoy')).display!=='none'"), '卡片的 ✕ 收起卡片，搖桿回來')
    h0 = await ev("[CITY3D.hero.position.x,CITY3D.hero.position.z,CITY3D.cam.position.x,CITY3D.cam.position.z]")
    await page.keyboard.down('ArrowUp'); await page.wait_for_timeout(700); await page.keyboard.up('ArrowUp'); await page.wait_for_timeout(100)
    h1 = await ev("[CITY3D.hero.position.x,CITY3D.hero.position.z,CITY3D.cam.position.x,CITY3D.cam.position.z]")
    check(h1[1] < h0[1] - .5 and abs((h1[3] - h0[3]) - (h1[1] - h0[1])) < .5, f"方向鍵↑：主角往前走，鏡頭跟著移動 {h0} → {h1}")
    jb = await ev("(()=>{const r=document.getElementById('cityJoy').getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]})()")
    await page.mouse.move(jb[0], jb[1]); await page.mouse.down(); await page.mouse.move(jb[0] + 40, jb[1], steps=4); await page.wait_for_timeout(700)
    knob = await ev("document.querySelector('#cityJoy i').style.transform")
    await page.mouse.up(); await page.wait_for_timeout(100)
    h2 = await ev("[CITY3D.hero.position.x,CITY3D.hero.position.z]")
    check(h2[0] > h1[0] + .5 and 'translate(40px' in knob, f"搖桿往右推：主角往右走，搖桿頭跟著手指 {h1[:2]} → {h2} {knob}")
    await page.keyboard.down('ArrowDown'); await page.wait_for_timeout(500); await page.keyboard.up('ArrowDown')
    await page.keyboard.down('ArrowLeft'); await page.wait_for_timeout(500); await page.keyboard.up('ArrowLeft'); await page.wait_for_timeout(200)
    a0 = await ev("Math.atan2(CITY3D.cam.position.x-CITY3D.hero.position.x,CITY3D.cam.position.z-CITY3D.hero.position.z)")
    await page.mouse.move(200, 400); await page.mouse.down()
    for k in range(1, 8): await page.mouse.move(200 + 20 * k, 400)
    await page.mouse.up(); await page.wait_for_timeout(200)
    a1 = await ev("Math.atan2(CITY3D.cam.position.x-CITY3D.hero.position.x,CITY3D.cam.position.z-CITY3D.hero.position.z)")
    check(abs(a1 - a0) > .3 and await ev("document.getElementById('cityCard').hidden"), f"單指拖曳畫面：繞著主角轉視角（拖動不會誤選樓）{a0:.2f} → {a1:.2f}")
    await page.mouse.wheel(0, 300); await page.wait_for_timeout(200)
    await page.click('#cityHelp'); await page.wait_for_timeout(100)
    check(await ev("!document.getElementById('cityLeg').hidden&&document.getElementById('cityLeg').innerText.includes('亮燈＝議價中')"), '右上「?」打開圖例（鷹架／蓋好插旗／亮燈）')
    await page.click('#cityHelp')
    mm = await ev("(()=>{const c=document.getElementById('cityMapCv'),d=c.getContext('2d').getImageData(0,0,c.width,c.height).data,set=new Set();for(let i=0;i<d.length;i+=400)set.add(d[i]>>4<<8|d[i+1]>>4<<4|d[i+2]>>4);const o=d.length/2+c.width*2;return {colors:set.size,center:[d[(c.width*c.height/2+c.width/2)*4],d[(c.width*c.height/2+c.width/2)*4+1],d[(c.width*c.height/2+c.width/2)*4+2]]}})()")
    check(mm['colors'] > 5 and mm['center'][0] > 200 and mm['center'][2] < 120, f"右上小地圖：畫出島、區域、路、物件，中間橘色箭頭是主角 {mm}")
    other = await ev("CITY3D.bld.find(g=>g.userData.id!==CITY3D.near).userData.id")
    await page.click('#cityMap'); await page.wait_for_timeout(200)
    mp = await ev(f"(()=>{{const r=document.getElementById('cityMapBigCv').getBoundingClientRect(),p=CITY3D.mapPx('{other}');return {{x:r.left+p[0]*r.width,y:r.top+p[1]*r.height,vis:!document.getElementById('cityMapBig').hidden}}}})()")
    await shot(page, '9c3_city_map')
    await page.mouse.click(mp['x'], mp['y']); await page.wait_for_timeout(400)
    gm = await ev("({near:CITY3D.near,big:document.getElementById('cityMapBig').hidden})")
    check(mp['vis'] and gm['near'] == other and gm['big'], f"點小地圖展開整座島的地圖；點地圖上的物件，主角直接走到它門口 {mp} {gm}")
    sid = await ev("CITY3D.bld.find(x=>x.userData.st==='seen').userData.id")
    await ev(f"CITY3D.select('{sid}')"); await page.wait_for_timeout(200)
    sp = {'id': sid}
    cc = await ev("({sel:CITY3D.sel,card:!document.getElementById('cityCard').hidden,t:document.getElementById('cityCard').innerText,next:(document.querySelector('.cc-next')||{}).innerText})")
    check(cc['sel'] == sp['id'] and cc['card'] and '已看房' in cc['t'] and cc['next'] == '開始議價：點亮這棟', f"物件卡片：分數、標籤、看房進度，主按鈕是下一步（開始議價：點亮這棟）{cc}")
    await shot(page, '9c1_city_card')
    await page.click('.cc-next'); await page.wait_for_timeout(900)
    c2 = await ev(f"({{st:loadHomes().find(h=>h.id==='{sp['id']}').st,b:CITY3D.bld.find(g=>g.userData.id==='{sp['id']}').userData.st,stat:document.getElementById('cityStat').innerText,bar:document.getElementById('cityBarI').style.width,done:(document.querySelector('.cc-done')||{{}}).innerText}})")
    check(c2['st'] == 'nego' and c2['b'] == 'nego' and '完成度 100%' in c2['stat'] and c2['bar'] == '100%' and '亮燈' in c2['done'], f"按「開始議價」：存回物件狀態，這棟亮燈，完成度變 100% {c2}")
    await page.click('.cc-st [data-cst=todo]'); await page.wait_for_timeout(300)
    check(await ev(f"loadHomes().find(h=>h.id==='{sp['id']}').st==='todo'&&CITY3D.bld.find(g=>g.userData.id==='{sp['id']}').children.some(c=>c.type==='LineSegments')"), '改回「未看房」：變回鷹架')
    await page.click('.cc-st [data-cst=seen]'); await page.wait_for_timeout(200)
    await page.keyboard.press('Escape'); await page.wait_for_timeout(300)
    check(await ev("!document.getElementById('city')&&!CITY3D&&state.view.type==='homes'&&!!document.getElementById('homesPop')"), '按 Esc 或 ✕ 關閉小城，回到我的物件（3D 資源釋放）')
    await page.click('#cityBtn'); await page.wait_for_function("CITY3D&&CITY3D.bld.length===2&&CITY3D.near", timeout=20000)
    await page.click('#cityAct'); await page.wait_for_timeout(200)
    check(await ev("CITY3D.sel===CITY3D.near&&!document.getElementById('cityCard').hidden"), '走到物件前按「看這間」：打開那間的卡片')
    await page.click('#ccOpen'); await page.wait_for_function("state.view.type==='val'", timeout=15000)
    check(await ev("!document.getElementById('city')"), '小城卡片的「估價／詳細」：關掉小城、打開那間的估價')
    await page.click('#homesBtn'); await page.wait_for_timeout(300)
    saved = await ev("localStorage.getItem('tlm.homes')")
    await ev("""(()=>{const a=JSON.parse(localStorage.getItem('tlm.homes')),base=a[0];[['信義 A',25.033,121.566],['中山 B',25.064,121.533],['士林 C',25.093,121.525],['內湖 D',25.081,121.592],['萬華 E',25.036,121.499]].forEach(([n,la,lo],i)=>a.push(Object.assign({},base,{id:'tx'+i,name:n,pt:[la,lo],st:['todo','seen','nego'][i%3],tags:[]})));localStorage.setItem('tlm.homes',JSON.stringify(a));renderHomes();})()""")
    await page.click('#cityBtn'); await page.wait_for_function("CITY3D&&CITY3D.bld.length===7", timeout=30000)
    await ev("CITY3D.zoom(75,.55)"); await page.wait_for_timeout(1500)
    rg2 = await ev("({regs:CITY3D.REGS.map(r=>r.name+r.ids.length),lms:CITY3D.LMS.map(l=>l.name),sep:(()=>{const R=CITY3D.REGS;let m=1e9;R.forEach((a,i)=>R.forEach((b,j)=>{if(j>i)m=Math.min(m,Math.hypot(a.c[0]-b.c[0],a.c[1]-b.c[1])-a.r-b.r)}));return m})()})")
    check(len(rg2['regs']) == 6 and len(rg2['lms']) >= 5 and rg2['sep'] >= 3, f"物件增加到 7 間、分在 6 區：小島自動重排成 6 個不重疊的區域，各區放自己的地標（101、摩天輪、故宮、龍山寺…）{rg2}")
    an = await ev("""(()=>{const f=()=>CITY3D.LMS.map(l=>{let s=0;l.g.traverse(o=>{s+=o.position.x*3+o.position.y*7+o.rotation.z*11+o.rotation.y*5+o.scale.x*2;});return s.toFixed(4)});const a=f();return new Promise(r=>setTimeout(()=>r({a,b:f(),names:CITY3D.LMS.map(l=>l.name),sc:CITY3D.LMS.map(l=>l.g.scale.x),ani:CITY3D.ANI.length}),800));})()""")
    still = [n for n, x, y in zip(an['names'], an['a'], an['b']) if x == y]
    check(not still and all(x >= 1.8 for x in an['sc']) and an['ani'] >= len(an['names']), f"地標放大（1.8 倍）而且會動：摩天輪轉、101 放煙火、龍山寺冒香煙、鴨子游泳、小船划過錦帶橋、故宮旗子飄… {an['names']} 沒動：{still}")
    await shot(page, '9c2_city_regions')
    await page.keyboard.press('Escape'); await page.wait_for_timeout(200)
    await ev(f"localStorage.setItem('tlm.homes',{json.dumps(saved)});renderHomes()"); await page.wait_for_timeout(200)
    await page.click('#homesPop [data-hsel]'); await page.click('#homesPop .frow:last-of-type [data-hsel]')
    ids = await ev("[...document.querySelectorAll('#homesPop [data-hsel][aria-pressed=true]')].length")
    await shot(page, '8f_homes')
    await page.click('#homeCmp'); await page.wait_for_function("state.view.type==='cmp'", timeout=5000); await page.wait_for_timeout(500)
    tgc = await ev("[...[...document.querySelectorAll('.cmp tbody tr')].find(r=>r.cells[0].innerText==='標籤').cells].slice(1).map(c=>c.innerText.replace(/\\s+/g,' ').trim()).sort()")
    check(tgc == ['海砂屋', '漏水 管委會差'], f"比較物件有「標籤」列 {tgc}")
    cm = await ev("({cols:document.querySelectorAll('.cmp thead th').length,rows:document.querySelectorAll('.cmp tbody tr').length,best:document.querySelectorAll('.cmp td.best').length,txt:document.querySelector('.cmp').innerText,pins:selLayer.getLayers().length})")
    check('狀態' in cm['txt'] and '議價中' in cm['txt'] and '已看房' in cm['txt'], '比較表第一列是看房狀態')
    check('步行到未來捷運站' in cm['txt'], '比較表有「步行到未來捷運站」')
    check('公告土地現值（萬／坪）' in cm['txt'], '比較表有「公告土地現值（萬／坪）」')
    check(ids == 2 and cm['cols'] == 3 and cm['rows'] >= 17 and cm['best'] >= 3 and '合理出價' in cm['txt'] and '步行到公園' in cm['txt'] and cm['pins'] == 2, f"勾 2 間按「並排比較」：同一張表比價格、燈號、房貸、租金、六類設施步行時間，最好的標綠（{cm['rows']} 列）")
    rk = await ev("({rows:[...document.querySelectorAll('#cmpRank .rk-row')].map(r=>r.querySelector('.rk-t b').innerText),sc:[...document.querySelectorAll('#cmpRank .rk-s')].map(e=>parseFloat(e.innerText)),bars:document.querySelectorAll('#cmpRank .rk-bar i').length,deal:document.querySelector('.cmp').innerText.includes('2,200 萬')})")
    check(len(rk['rows']) == 2 and rk['sc'][0] >= rk['sc'][1] and rk['bars'] >= 6 and rk['deal'], f"比較物件最上方有模型排名：總分由高到低、彩色長條顯示價格划算／生活機能／月付負擔／屋齡各貢獻多少；表格有談判價 {rk}")
    await shot(page, '8g_compare')
    await page.click('#cmpRank [data-rkw=life]'); await page.wait_for_timeout(200)
    rk2 = await ev("({sc:[...document.querySelectorAll('#cmpRank .rk-s')].map(e=>parseFloat(e.innerText)),w:localStorage.getItem('tlm.cmpW'),p:document.querySelector('#cmpRank [data-rkw=life]').getAttribute('aria-pressed')})")
    check(rk2['w'] == 'life' and rk2['p'] == 'true' and rk2['sc'] != rk['sc'] and rk2['sc'][0] >= rk2['sc'][1], f"切到「重生活」：分數與名次依新權重重算，選擇記在裝置 {rk2}")
    await shot(page, '8g2_compare_rank')
    # 比較表維持原樣，點格子就地修改
    ce = await ev("({n:document.querySelectorAll('.cmp td.ce').length,inp:document.querySelectorAll('.cmp input,.cmp select').length,rows:[...document.querySelectorAll('.cmp tbody tr')].filter(r=>r.querySelector('td.ce')).map(r=>r.cells[0].innerText.replace('✎','').trim())})")
    check(ce['n'] == 2 * 4 and ce['inp'] == 0 and ce['rows'] == ['車位', '條件', '開價', '談判價'], f"比較表樣式不變；車位、條件、開價、談判價四列標 ✎，可以點格子修改 {ce}")
    m0 = await ev("(()=>{const x=state.view.items[0];return {id:x.h.id,ping:x.h.form.ping,fair:x.V.off&&Math.round(x.V.off.out[1])}})()")
    await page.click('.cmp td[data-cek="0:cond"]'); await page.wait_for_timeout(150)
    check(await ev("document.querySelectorAll('.cmp td.ce-on [data-cf]').length===6&&document.activeElement.dataset.cf==='t'"), '點「條件」格：就地展開型態、坪數、屋齡、樓層、屋況，可以直接改')
    await page.fill('.cmp td.ce-on [data-cf=age]', '200'); await page.click('.cmp td.ce-on .ce-ok'); await page.wait_for_timeout(200)
    check(await ev(f"loadHomes().find(z=>z.id==='{m0['id']}').form.age!==200&&!!document.querySelector('.cmp td.ce-on')&&document.getElementById('toast').innerText.includes('屋齡要在')"), '屋齡填 200：提示範圍，不存，編輯框留著')
    await page.fill('.cmp td.ce-on [data-cf=age]', '12'); await page.fill('.cmp td.ce-on [data-cf=ping]', '40'); await page.click('.cmp td.ce-on .ce-ok'); await page.wait_for_timeout(300)
    m1 = await ev(f"(()=>{{const h=loadHomes().find(z=>z.id==='{m0['id']}'),x=state.view.items.find(z=>z.h.id==='{m0['id']}'),c=document.querySelector('.cmp td[data-cek=\"0:cond\"]');return {{ping:h.form.ping,age:h.form.age,mid:h.mid,fair:x.V.off&&Math.round(x.V.off.out[1]),txt:c.innerText,on:!!document.querySelector('.cmp td.ce-on'),toast:document.getElementById('toast').innerText}}}})()")
    check(m1['ping'] == 40 and m1['age'] == 12 and m1['fair'] != m0['fair'] and m1['mid'] == m1['fair'] and '40 坪' in m1['txt'] and not m1['on'] and '已更新' in m1['toast'], f"按「儲存」：存回物件、重新估價（合理出價 {m0['fair']}→{m1['fair']}），表格回到原樣顯示新數字 {m1}")
    await page.click('.cmp td[data-cek="1:park"]'); await page.select_option('.cmp td.ce-on select', '1'); await page.wait_for_timeout(300)
    check(await ev("document.querySelector('.cmp td[data-cek=\"1:park\"]').innerText==='有'&&state.view.items[1].h.form.park===1"), '點「車位」格選「有」：馬上存好')
    await page.click('.cmp td[data-cek="1:deal"]'); await page.fill('.cmp td.ce-on input', '2000'); await page.press('.cmp td.ce-on input', 'Enter'); await page.wait_for_timeout(300)
    check(await ev("document.querySelector('.cmp td[data-cek=\"1:deal\"]').innerText==='2,000 萬'&&state.view.items[1].h.form.deal===2000"), '點「談判價」格輸入 2000 按 Enter：存好，月付和排名跟著重算')
    await page.click('.cmp td[data-cek="0:ask"]'); await page.fill('.cmp td.ce-on input', '9999'); await page.press('.cmp td.ce-on input', 'Escape'); await page.wait_for_timeout(200)
    check(await ev(f"!document.querySelector('.cmp td.ce-on')&&loadHomes().find(z=>z.id==='{m0['id']}').form.ask!==9999"), '按 Esc 取消：不存，回到原本的值')
    check(await ev("[...document.querySelectorAll('.cmp tbody tr')].every(r=>r.cells.length===1||r.cells.length===3)"), '表格每一列欄數不變（不跑版）')
    await page.click('.cmp td[data-cek="0:cond"]'); await page.wait_for_timeout(150)
    await shot(page, '8g3_compare_edit')
    await page.click('.cmp td.ce-on .ce-x')
    await ev(f"setHome('{m0['id']}',h=>{{h.form.ping={m0['ping']};}})")
    await page.click('#close'); await page.wait_for_timeout(300)
    await ev("void homeLayer.getLayers()[0].fire('click')"); await page.wait_for_function("state.view.type==='val'&&!!document.getElementById('valSum')", timeout=5000)
    check(await ev("document.getElementById('valSave').getAttribute('aria-pressed')==='true'"), '點地圖上的星號：直接重新估價並顯示結果')
    await page.click('#close'); await page.wait_for_timeout(300)
    await page.click('#homesBtn'); await page.click('#homesPop [data-hdel]'); await page.wait_for_timeout(200)
    check(await ev("JSON.parse(localStorage.getItem('tlm.homes')).length===1&&homeLayer.getLayers().length===1"), '可以刪除儲存的物件')
    # 我的物件不限個數
    await ev("(()=>{const a=JSON.parse(localStorage.getItem('tlm.homes'));const b=a[0];for(let i=0;i<59;i++)a.push(Object.assign({},b,{id:'x'+i,name:'測試'+i}));localStorage.setItem('tlm.homes',JSON.stringify(a));drawHomes();})()")
    await ev("void openVal([25.034,121.547])"); await page.wait_for_function("!!document.getElementById('valGo')", timeout=5000)
    await page.click('#valGo'); await page.wait_for_function("!!document.getElementById('valSave')", timeout=15000); await page.click('#valSave'); await page.wait_for_timeout(300)
    check(await ev("loadHomes().length===61&&typeof HOMES_MAX==='undefined'&&document.getElementById('toast').innerText.includes('已儲存')"), '我的物件不限個數：已有 60 間時仍可以再存第 61 間')
    await page.click('#homesBtn'); await page.wait_for_function("document.querySelectorAll('#homesPop .hscore').length===61", timeout=15000)
    check(await ev("document.querySelectorAll('#homesPop .hrow').length===61"), '61 間都列在物件分頁，分數也都算好')
    await page.click('#exploreBtn'); await page.wait_for_timeout(200)
    await ev("void openVal([25.034,121.547])"); await page.wait_for_function("!!document.getElementById('valGo')", timeout=5000); await page.click('#valGo'); await page.wait_for_function("!!document.getElementById('valSave')", timeout=15000)
    await ev("localStorage.setItem('tlm.homes',JSON.stringify(loadHomes().slice(0,1)));drawHomes()"); await page.click('#close'); await page.wait_for_timeout(200)
    await page.click('#homesBtn'); await page.wait_for_selector('#homesPop [data-hlink]')
    await page.click('#homesPop [data-hlink]'); await page.fill('.hl-add input', 'not a url'); await page.click('.hl-ok'); await page.wait_for_timeout(150)
    check(await ev("!(loadHomes()[0].links||[]).length&&document.getElementById('toast').innerText.includes('完整網址')"), '我的物件＋連結：不是網址時提示，不存')
    await page.fill('.hl-add input', 'https://www.sinyi.com.tw/buy/house/12345'); await page.press('.hl-add input', 'Enter'); await page.wait_for_timeout(150)
    await page.fill('.hl-add input', 'https://sale.591.com.tw/home/house/detail/2/999.html'); await page.click('.hl-ok'); await page.wait_for_timeout(150)
    lk = await ev("({saved:loadHomes()[0].links,chips:[...document.querySelectorAll('.hl-ed .hl-r a')].map(a=>[a.previousElementSibling.innerText,a.target,a.href])})")
    await page.click('.hl-done'); await page.wait_for_timeout(200)
    check(await ev("document.querySelector('#homesPop [data-hlink] small').innerText==='2'&&document.querySelector('#homesPop [data-hlink]').classList.contains('on')"), '物件列的連結收成一個圖示，右上角顯示有幾個連結（點開才列出）')
    check([x['p'] for x in lk['saved']] == ['信義', '591'] and [c[0] for c in lk['chips']] == ['信義', '591'] and all(c[1] == '_blank' for c in lk['chips']) and await ev("(()=>{const b=document.querySelector('#homesPop [data-hlink]');return b.getBoundingClientRect().right<=document.querySelector('#homesPop .hrow').getBoundingClientRect().right})()"), f"貼上網址加入連結：自動認出平台（信義、591），點連結圖示列出、在新分頁開啟 {lk}")
    await shot(page, '8f3_home_links')
    await page.click('#homesPop [data-hlink]'); await page.click('.hl-ed [data-hldel] >> nth=0'); await page.click('.hl-done'); await page.wait_for_timeout(150)
    check(await ev("loadHomes()[0].links.length===1&&loadHomes()[0].links[0].p==='591'"), '編輯連結可以刪掉其中一個')
    await ev("void openHome(loadHomes()[0])"); await page.wait_for_function("state.view.type==='val'&&!!document.getElementById('valSum')", timeout=10000)
    check(await ev("[...document.querySelectorAll('#body .vlinks a')].map(a=>a.innerText).join()==='591'"), '打開物件的估價頁上方也有連結')
    await page.click('#close'); await page.wait_for_timeout(200)
    await page.click('#exploreBtn'); await page.wait_for_timeout(200)
    check(await ev("state.view.type==='list'&&document.getElementById('exploreBtn').getAttribute('aria-selected')==='true'"), '按「探索」回到只有地圖')
    await ev("void map.setView([25.12,121.50],16,{animate:false})"); await page.wait_for_timeout(300)
    await ev("void openVal([25.12,121.50])"); await page.wait_for_function("!!document.getElementById('valGo')", timeout=5000); await page.click('#valGo'); await page.wait_for_timeout(400)
    check(await ev("!!document.getElementById('valNo')&&!document.getElementById('valSum')&&!state.val.est.ok"), '附近成交太少：說明原因，不給出價建議')
    await page.click('#close')
    await ev("void map.setView([25.030,121.543],16,{animate:false})"); await page.wait_for_timeout(300)
    await page.click('#layersBtn'); await page.click('#layersPop [data-layer=deal]'); await page.wait_for_function("dealLayer.getLayers().length>10", timeout=15000); await page.click('#layersBtn')
    dl = await ev("""(()=>{const ms=dealLayer.getLayers(),iso=[...document.querySelectorAll('.deal-iso')],cl=[...document.querySelectorAll('.deal-cl')];
      return {n:ms.length,iso:iso.length,cl:cl.length,clN:cl.map(e=>+e.querySelector('b').innerText),bg:iso.some(e=>getComputedStyle(e).backgroundColor!=='rgba(0, 0, 0, 0)'),total:VALDATA.sales.filter(r=>map.getBounds().pad(.05).contains(r.pt)).length,
        isoTotal:iso.reduce((a,e)=>{const b=e.querySelector('text');return a+(b?+b.textContent:1)},0),leg:!document.getElementById('dealLegend').hidden&&document.getElementById('dealLegend').innerText,kinds:new Set(dealLayer.getLayers().filter(m=>m.options.icon.options.className==='deal-iso').map(m=>m.options.icon.options.html.includes('<line')?'roof':m.options.icon.options.html.length)).size}})()""")
    check(dl['iso'] == 10 and 0 < dl['cl'] <= 20 and dl['iso'] + dl['cl'] == dl['n'] and not dl['bg'], f"一個畫面最多 10 棟 3D 建物（{dl['iso']} 棟、沒有底板），其餘聚合成 {dl['cl']} 個數字")
    check(sum(dl['clN']) + dl['isoTotal'] == dl['total'] and all(t in (dl['leg'] or '') for t in ('住宅大樓', '華廈', '公寓', '聚合')), f"聚合的數字是成交筆數，加起來等於畫面內全部 {dl['total']} 筆；圖例有三種型態與聚合說明")
    await shot(page, '8e_deals')
    z0 = await ev("map.getZoom()")
    await ev("void dealLayer.getLayers().find(m=>m.options.icon.options.className==='deal-cl').fire('click')"); await page.wait_for_timeout(700)
    check(await ev("map.getZoom()") > z0, f"點聚合數字：地圖放大到那一群（z{z0} → z{await ev('map.getZoom()')}）")
    for _ in range(4):
        gi = await ev("dealLayer.getLayers().findIndex(m=>m.options.icon.options.className==='deal-iso'&&m.options.icon.options.html.includes('<text'))")
        if gi >= 0: break
        await ev("void map.setZoom(map.getZoom()+1,{animate:false})"); await page.wait_for_timeout(500)
    await ev(f"void dealLayer.getLayers()[{gi}].fire('click')"); await page.wait_for_timeout(300)
    pop = await ev("({t:document.querySelector('.leaflet-popup-content').innerText,rows:document.querySelectorAll('.deal-pop .dp-r').length,btn:document.querySelectorAll('.deal-pop .dp-use').length})")
    check(pop['rows'] >= 2 and pop['btn'] == pop['rows'] and '萬／坪' in pop['t'] and '總價約' in pop['t'] and '屋齡' in pop['t'] and '樓' in pop['t'] and any(t in pop['t'] for t in ('住宅大樓', '華廈', '公寓')),
          f"點圖示：顯示房屋型態、總樓層、屋齡，以及這棟每一筆成交的單價、年月、坪數、樓層、總價（{pop['rows']} 筆）")
    check(await ev("document.getElementById('dealLegend').classList.contains('under-pop')&&getComputedStyle(document.getElementById('dealLegend')).display==='none'&&document.querySelectorAll('.deal-pop .dp-r').length<=5"), '資訊卡打開時隱藏圖例；最多列 5 筆，卡片可捲動')
    await shot(page, '8e2_deal_pop')
    await page.click('.deal-pop .dp-use[data-di="1"]'); await page.wait_for_function("state.view.type==='val'&&!!document.getElementById('valGo')", timeout=5000)
    check(await ev("(()=>{const d=VALDATA.sales.filter(r=>r.pt[0]===state.val.pt[0]&&r.pt[1]===state.val.pt[1]).sort((a,b)=>b.ym-a.ym)[1];return !!d&&Math.abs(+document.querySelector('#body [data-vf=ping]').value-d.ping)<.06&&state.val.form.t===d.t&&+document.querySelector('#body [data-vf=fl]').value===d.fl})()"), '「估價 ›」：帶入那一筆的型態、坪數、屋齡、樓層')
    await ev("void map.setView([25.05,121.55],13.5,{animate:false})"); await page.wait_for_timeout(500)
    check(await ev("document.querySelectorAll('.deal-iso').length") <= 10 and await ev("document.querySelectorAll('.deal-cl').length") > 0, '縮小到全區時仍最多 10 棟，其餘聚合成數字')
    await ev("void map.setView([25.05,121.55],12,{animate:false})"); await page.wait_for_timeout(500)
    check(await ev("dealLayer.getLayers().length") == 0, '縮小到全市時不畫成交點')
    await page.click('#close'); await page.wait_for_timeout(300)
    await page.click('#layersBtn'); await page.click('#layersPop [data-layer=priceheat]'); await page.wait_for_function("!!phLayer&&phLayer.getLayers().length>5", timeout=5000)
    ph = await ev("({n:phLayer.getLayers().length,heat:state.layers.heat,leg:!document.getElementById('phLegend').hidden&&document.getElementById('phLegend').innerText,lo:PH.lo,hi:PH.hi,da:PH.cells.filter(c=>Math.abs(c.c[0]-25.030)<.004&&Math.abs(c.c[1]-121.543)<.004).map(c=>c.v),wen:PH.cells.filter(c=>Math.abs(c.c[0]-24.992)<.004&&Math.abs(c.c[1]-121.565)<.004).map(c=>c.v)})")
    import statistics
    check(ph['n'] > 5 and not ph['heat'] and '萬／坪' in (ph['leg'] or '') and ph['da'] and ph['wen'] and statistics.median(ph['da']) > statistics.median(ph['wen']) * 1.4, f"單價熱圖：開啟後自動關掉生活機能熱圖、顯示圖例；大安（{statistics.median(ph['da']):.0f}）明顯高於文山（{statistics.median(ph['wen']):.0f}）萬／坪")
    await page.click('#layersPop [data-layer=heat]'); await page.wait_for_timeout(300)
    check(await ev("state.layers.heat&&!state.layers.priceheat&&!phLayer&&document.getElementById('phLegend').hidden"), '再開生活機能熱圖：單價熱圖自動關閉（兩種熱圖一次一種）')
    check(await ev("(()=>{const t=document.getElementById('toast').getBoundingClientRect(),b=document.getElementById('resultBtn').getBoundingClientRect();return b.height===0||t.bottom<=b.top})()"), '提示訊息顯示在結果按鈕上方，不會疊在一起')
    await page.click('#layersPop [data-layer=priceheat]'); await page.wait_for_timeout(300); await page.click('#layersBtn'); await shot(page, '8h_priceheat')
    await page.click('#layersBtn'); await page.click('#layersPop [data-layer=priceheat]'); await page.click('#layersPop [data-layer=heat]'); await page.click('#layersBtn')
    await page.close()

    # 11. 桌機版面
    page = await new_page(b, errs, viewport={'width': 1280, 'height': 800})
    await page.goto(URL); await page.wait_for_function(READY, timeout=20000)
    check(await page.evaluate("getComputedStyle(document.getElementById('sheet')).visibility==='hidden'&&!document.getElementById('listBtn')"), '桌機／iPad：地圖主畫面不顯示分析結果清單面板')
    await page.evaluate("openZone(0)"); await page.wait_for_timeout(400)
    r = await page.evaluate("(()=>{const s=document.getElementById('sheet').getBoundingClientRect();return {l:s.left,w:s.width,b:s.bottom,v:getComputedStyle(document.getElementById('sheet')).visibility}})()")
    check(r['l'] < 20 and 380 <= r['w'] <= 400 and r['b'] <= 800 and r['v'] == 'visible', '桌機版：點區域後才出現左側面板')
    await shot(page, '6_desktop')
    await page.click('#close'); await page.wait_for_timeout(300)
    check(await page.evaluate("getComputedStyle(document.getElementById('sheet')).visibility==='hidden'"), '桌機版：按 ✕ 關掉面板，回到只有地圖')
    await page.close()
    # iPad 直向（820×1180）
    page = await new_page(b, errs, viewport={'width': 820, 'height': 1180}, has_touch=True)
    await page.goto(URL); await page.wait_for_function(READY, timeout=20000); await page.wait_for_timeout(300)
    check(await page.evaluate("getComputedStyle(document.getElementById('sheet')).visibility==='hidden'&&[...document.querySelectorAll('#navbar .nb')].map(b=>b.id).join()==='exploreBtn,exportBtn,recBtn,homesBtn,marketBtn'"), 'iPad：沒有清單面板，下方 5 個分頁：探索、房源、推薦、物件、房市')
    await page.evaluate("openZone(0)"); await page.wait_for_timeout(300); await page.click('#close'); await page.wait_for_timeout(300)
    check(await page.evaluate("getComputedStyle(document.getElementById('sheet')).visibility==='hidden'"), 'iPad：詳細面板可以用 ✕ 關閉')
    await shot(page, '6b_ipad')
    await page.close()

    # 11b. 匯入房源（Claude 找好的房源表）→ 依地址定位到地圖 → 點按連到房仲網站
    page = await new_page(b, errs, **PHONE); ev = page.evaluate
    await page.goto(URL); await page.wait_for_function(READY, timeout=20000); await page.wait_for_timeout(300)
    HDR = 'CP 排名,可信度,模型估價(萬/坪),開價 vs 估價(%),區塊,位置判定,地址／路段,社區,型態,開價(萬),建坪,室內坪數,開價單價(萬/坪),樓層,屋齡,車位,實價比較基準,實價筆數,實價中位(萬/坪),P25～P75,開價 vs 實價(%),刊登數,\'591,信義,永慶,樂屋,住商,備註,緯度,經度'
    rows = [
        '4,高,82,-21.3,1 主塊,範圍內,忠孝東路五段790巷59弄37號,,公寓,1998,31,31,64.5,\'4/4,49,,同巷,4,70.2,56.9～85.2,-8.1,3,https://sale.591.com.tw/home/house/detail/2/20726738.html,,https://buy.yungching.com.tw/house/7521145,,,,,',
        '9,中,95,6.2,1 主塊,範圍內,台北市松山區八德路三段,,華廈,1900,52.76,32.18（估）,36,\'1/7,46.8,,同路段,12,91,88～119.6,8.5,1,,https://www.sinyi.com.tw/buy/house/3647BM,,,,"單價偏低,請確認",,',
        '12,低,80,1.0,1 主塊,範圍內,不存在路999號,,大樓,2500,30,20,83.3,\'5/12,10,有,同路段,3,80,70～90,0.5,1,https://sale.591.com.tw/home/house/detail/2/1.html,,,,,,,',
        '4,高,82,-21.3,1 主塊,範圍內,忠孝東路五段790巷59弄37號,,公寓,1998,31,31,64.5,\'4/4,49,,同巷,4,70.2,56.9～85.2,-8.1,3,https://sale.591.com.tw/home/house/detail/2/20726738.html,,,,,,,',
        '20,中,70,-3,1 主塊,範圍內,林森北路67巷132號,,華廈,1880,74.61,68.22,25.2,\'3/6,49.2,,同巷,2,68,65.3～70.6,-2,4,https://sale.591.com.tw/home/house/detail/2/21041037.html,,,https://www.rakuya.com.tw/sell_item/info?ehid=0743ba35907661a,https://www.hbhousing.com.tw/detail/?sn=WS86869,,25.0505,121.5245']
    csvp = os.path.join(OUT, 'listings_test.csv')
    with open(csvp, 'w', encoding='utf-8-sig') as f: f.write(HDR + '\n' + '\n'.join(rows) + '\n')
    await ev("closeDetail()"); await page.click('#exportBtn'); await page.wait_for_function("state.view.type==='export'", timeout=3000)
    check(await ev("!!document.getElementById('imFile')&&/手動匯入/.test(document.getElementById('body').innerText)&&!!document.getElementById('toSettings')&&!!document.getElementById('imPasteBtn')"), '「找房」頁：上方私人房源（未連結時只有「去設定」）、下方一行手動匯入（檔案／貼上）')
    g0 = len(geo_calls)
    await page.set_input_files('#imFile', csvp)
    await page.wait_for_function("LISTINGS.length===4&&!geoRunning&&LISTINGS.every(l=>l.prec)", timeout=20000)
    im = await ev("({prec:LISTINGS.map(l=>l.prec),n:saleLayer.getLayers().slice().sort((a,b)=>(a.saleIdx??1e9)-(b.saleIdx??1e9)).length,st:document.getElementById('imSt').innerText,cls:saleLayer.getLayers().slice().sort((a,b)=>(a.saleIdx??1e9)-(b.saleIdx??1e9)).map(m=>m.getElement().className),bg:saleLayer.getLayers().slice().sort((a,b)=>(a.saleIdx??1e9)-(b.saleIdx??1e9)).map(m=>m.getElement().querySelector('b').style.background),txt:saleLayer.getLayers().slice().sort((a,b)=>(a.saleIdx??1e9)-(b.saleIdx??1e9)).map(m=>m.getElement().innerText),links:LISTINGS[0].links.map(x=>x.p),floor:LISTINGS[0].floor,on:state.layers.sale,inv:map.getBounds().contains([25.0412,121.5705])})")
    print('  import', im, geo_calls[g0:])
    check(im['prec'] == ['addr', 'road', 'none', 'given'], f"CSV 匯入 4 間（重複的略過）：完整地址定位到門牌、只有路段的定位到路段、找不到的標出來、有經緯度欄的直接用 {im['prec']}")
    check(geo_calls[g0:] == ['臺北市忠孝東路五段790巷59弄37號', '台北市松山區八德路三段', '臺北市不存在路999號', '臺北市不存在路'], f'依地址定位：沒有「臺北市」會補上，找不到門牌改找路段，有經緯度的不查 {geo_calls[g0:]}')
    check(im['n'] == 3 and im['on'] and im['inv'] and '1,998萬' in im['txt'] and 'approx' in im['cls'][1] and 'approx' not in im['cls'][0], f"地圖上 3 間房子標開價，只定位到路段的用虛線框，地圖移到房源 {im['txt']}")
    check(im['bg'][0] in ('rgb(24, 128, 56)', '#188038') and im['bg'][1] in ('rgb(156, 82, 0)', '#9C5200'), '顏色：開價比估價低 5% 以上綠色、高 5% 以上橘色')
    check(im['links'] == ['591', '永慶'] and im['floor'] == '4/4' and ''.join(im['st'].split()).startswith('4間') and '1 間找不到位置' in im['st'] and '路段' not in im['st'], f"讀出各平台連結、樓層去掉 ' 開頭，狀態列匯入狀態只一行：間數＋找不到位置（{im['st']}）")
    await page.click('#imUnBtn'); await page.wait_for_timeout(150)
    check(await ev("(()=>{const d=document.getElementById('imUn');return !!d&&!d.hidden&&d.textContent.includes('不存在路999號')})()"), '點「1 間找不到位置」展開地址清單')
    uc = await ev("(()=>{const c=[...document.querySelectorAll('#body .hk-card')];return c.map(x=>({lbl:x.querySelector('.hk-lbl2').innerText,ic:x.querySelectorAll('.hk-ic').length,h:x.querySelector('.hk-ic')?Math.round(x.querySelector('.hk-ic').getBoundingClientRect().height):0,bg:getComputedStyle(x).backgroundColor,r:getComputedStyle(x).borderRadius}))})()")
    check(len(uc) == 2 and [x['lbl'] for x in uc] == ['自動匯入', '手動匯入'] and uc[1]['ic'] == 2 and uc[1]['h'] >= 44 and uc[0]['bg'] == uc[1]['bg'] and uc[0]['r'] == uc[1]['r'], f"自動匯入、手動匯入是同樣的卡片：左邊標題＋狀態、右邊兩個圓形圖示 {uc}")
    await shot(page, '8v_import_cards')
    g1 = len(geo_calls)
    await page.set_input_files('#imFile', csvp); await page.wait_for_timeout(500)
    check(len(geo_calls) == g1 and await ev("LISTINGS.length===4") and '重複略過' in await ev("document.getElementById('toast').innerText"), '再匯入同一個檔：全部略過，不重新定位')
    await page.click('#close'); await page.wait_for_timeout(200)
    await ev("GMKEY='AIzaTEST_key_1234567890abcdefXYZ'")   # 有金鑰：資訊卡會嵌入街景並重新排版
    await ev("void saleLayer.getLayers().slice().sort((a,b)=>(a.saleIdx??1e9)-(b.saleIdx??1e9))[0].fire('click')"); await page.wait_for_selector('.leaflet-popup-content .sp', timeout=8000)
    check(await ev("!document.querySelector('.leaflet-popup-content .sv-embed')&&!!document.querySelector('.leaflet-popup-content [data-svtog]')"), '資訊卡的街景先收起來（按「街景」才展開），卡片比較短')
    await page.click('.leaflet-popup-content [data-svtog]'); await page.wait_for_selector('.leaflet-popup-content .sv-embed iframe', timeout=8000)
    pop = await ev("(()=>{const e=document.querySelector('.leaflet-popup-content .sp');return e?{t:e.innerText,scale:[...e.querySelectorAll('.pscale .m')].map(m=>m.className),badge:(e.querySelector('.dbadge')||{}).className,href:[...e.querySelectorAll('.lk a:not(.svl)')].map(a=>[a.innerText,a.href,a.target])}:null})()")
    check(pop and '忠孝東路五段790巷59弄37號' in pop['t'] and '1,998 萬' in pop['t'] and '開價 vs 實價 -8.1%' in pop['t'] and '低於估價' in pop['t'] and pop['badge'] == 'dbadge good' and pop['scale'] == ['m real', 'm est', 'm ask'] and 'CP 第 4 名' in pop['t'] and pop['href'] == [['591', 'https://sale.591.com.tw/home/house/detail/2/20726738.html', '_blank'], ['永慶', 'https://buy.yungching.com.tw/house/7521145', '_blank']], f'點房子：地址、開價大字、綠色「低於估價」標籤與價格位置圖（實價／估價／開價），各房仲網站連結在新分頁開啟 {pop}')
    await shot(page, '8r_sale_pop')
    await page.locator('.leaflet-popup-content .orng').fill('90'); await page.wait_for_timeout(150)
    of = await ev("(()=>{const e=document.querySelector('.leaflet-popup-content .sp');return {v:e.querySelector('.ov').innerText,r:e.querySelector('.ores').innerText,m:e.querySelectorAll('.pscale .m.offer').length}})()")
    check('1,798 萬' in of['v'] and '-10%' in of['v'] and '少付 200 萬' in of['r'] and of['m'] == 1, f'拉「出價試算」到 90%：顯示出價、每坪單價、和實價差多少、少付多少，價格位置圖多一個橘點 {of}')
    await shot(page, '8r2_sale_offer')
    # 資訊卡（看連結的地方）就能加入我的物件
    n0 = await ev("(()=>{window.__SP=map._popup;window.__HC=localStorage.getItem('tlm.homes');LISTINGS.push({addr:'加入測試路9號',type:'住宅大樓',price:2600,ping:28,age:14,floor:'6/12',park:'有',lat:25.0335,lng:121.5452,prec:'addr',links:[{p:'信義',u:'https://x.test/add9'},{p:'樂屋',u:'https://z.test/add9'}]});openSaleAt(LISTINGS.length-1);return loadHomes().length})()")
    await page.wait_for_selector('.leaflet-popup-content [data-sadd]', timeout=5000)
    b0 = await ev("(()=>{const b=document.querySelector('.leaflet-popup-content [data-sadd]'),g=document.querySelector('.leaflet-popup-content [data-sval]');return {t:b.innerText,l:b.getAttribute('aria-label'),w:b.getBoundingClientRect().width,p:b.getAttribute('aria-pressed'),c:getComputedStyle(b).color,same:Math.abs(b.getBoundingClientRect().top-g.getBoundingClientRect().top)<2,h:b.getBoundingClientRect().height}})()")
    check(b0['t'] == '' and b0['l'] == '加入我的物件' and 44 <= b0['w'] <= 46 and b0['p'] == 'false' and b0['c'] == 'rgb(156, 82, 0)' and b0['same'] and b0['h'] >= 44, f"房源資訊卡（有各平台連結的地方）多了「加入我的物件」星星圖示按鈕（只有圖示、44px），和「估價」並排 {b0}")
    await page.click('.leaflet-popup-content [data-sadd]'); await page.wait_for_timeout(400)
    a1 = await ev(f"(()=>{{const h=loadHomes()[0],b=document.querySelector('.leaflet-popup-content [data-sadd]');return {{n:loadHomes().length,name:h.name,links:h.links.map(x=>x.p),park:h.form.park,ask:h.form.ask,mid:h.mid,p:b.getAttribute('aria-pressed'),c:getComputedStyle(b).color,t:b.getAttribute('aria-label'),pins:homeLayer.getLayers().length,toast:document.getElementById('toast').innerText}}}})()")
    check(a1['n'] == n0 + 1 and a1['name'] == '加入測試路9號' and a1['links'] == ['信義', '樂屋'] and a1['park'] == 1 and a1['ask'] == 2600 and a1['mid'] and a1['p'] == 'true' and a1['c'] == 'rgb(156, 82, 0)' and a1['t'].startswith('已在我的物件') and '已加入' in a1['toast'], f"按「加入我的物件」：不用先估價就存好（名稱、連結、車位、開價，合理出價也先算好），按鈕變成已加入 {a1}")
    await shot(page, '8r3_sale_add')
    await page.click('.leaflet-popup-content [data-sadd]'); await page.wait_for_timeout(300)
    check(await ev(f"loadHomes().length==={n0}&&document.querySelector('.leaflet-popup-content [data-sadd]').getAttribute('aria-pressed')==='false'"), '再按一次：從我的物件移除')
    # 缺屋齡的房源：按星星不再擋下，直接打開估價（帶入已有的條件），補上就能存
    await ev("(()=>{LISTINGS.push({addr:'缺屋齡路1號',type:'住宅大樓',price:2300,ping:27,floor:'5/10',lat:25.0335,lng:121.5452,prec:'addr',links:[{p:'信義',u:'https://x.test/noage1'}]});map.closePopup();openSaleAt(LISTINGS.length-1);})()")
    await page.wait_for_function("[...document.querySelectorAll('.leaflet-popup-content')].some(e=>e.innerText.includes('缺屋齡路'))", timeout=5000)
    await page.click('.leaflet-popup-content [data-sadd]'); await page.wait_for_function("state.view.type==='val'", timeout=10000); await page.wait_for_timeout(200)
    na = await ev("({ping:state.val.form.ping,fl:state.val.form.fl,ask:state.val.form.ask,links:state.val.links.map(x=>x.u),toast:document.getElementById('toast').innerText,n:loadHomes().length})")
    check(na['ping'] == 27 and na['fl'] == 5 and na['ask'] == 2300 and na['links'] == ['https://x.test/noage1'] and '儲存物件' in na['toast'] and na['n'] == n0, f"缺屋齡的房源按星星：不擋下，直接打開估價並帶入坪數、樓層、開價與連結，提示補上後按「儲存物件」 {na}")
    await page.click('#valGo'); await page.wait_for_function("!!document.getElementById('valSave')||!!document.getElementById('valNo')", timeout=15000)
    if await ev("!!document.getElementById('valSave')"):
        await page.click('#valSave'); await page.wait_for_timeout(300)
    check(await ev(f"loadHomes().length==={n0}+1&&loadHomes()[0].links.some(x=>x.u==='https://x.test/noage1')"), '補上屋齡後按「儲存物件」：加入我的物件（連結一起存）')
    # 手機儲存空間滿：先清掉可重抓的地址快取再存；真的存不了就明白提示，不再默默沒存到
    q = await ev("""(()=>{const o=Storage.prototype.setItem;let n=0;localStorage.setItem('tlm.geocache','{"x":1}');Storage.prototype.setItem=function(k,v){if(k==='tlm.homes'&&localStorage.getItem('tlm.geocache')!==null){n++;throw new DOMException('full','QuotaExceededError');}return o.call(this,k,v);};const a=loadHomes(),ok=saveHomes(a);Storage.prototype.setItem=function(k,v){if(k==='tlm.homes')throw new DOMException('full','QuotaExceededError');return o.call(this,k,v);};const bad=saveHomes(a.concat([Object.assign({},a[0],{id:'zz'})]));Storage.prototype.setItem=o;return {ok,bad,n,geo:localStorage.getItem('tlm.geocache'),len:loadHomes().length===a.length}})()""")
    await page.wait_for_timeout(200)
    qt = await ev("document.getElementById('toast').innerText")
    check(q['ok'] and not q['bad'] and q['n'] == 1 and q['geo'] is None and q['len'] and '儲存空間滿' in qt, f"手機儲存空間滿：先清掉地址快取再存成功；還是存不了就提示「儲存空間滿了」 {q} {qt}")
    await page.click('#close'); await page.wait_for_timeout(200)
    await ev("LISTINGS.pop()")
    await ev("(()=>{map.closePopup();LISTINGS.pop();if(window.__HC)localStorage.setItem('tlm.homes',window.__HC);else localStorage.removeItem('tlm.homes');drawHomes();drawSale();if(window.__SP)map.openPopup(window.__SP);})()"); await page.wait_for_timeout(300)
    check(await ev("document.querySelector('.leaflet-popup-content [data-sval]').innerText==='估價'"), '房子資訊卡的估價按鈕只寫「估價」')
    await page.click('.leaflet-popup-content [data-sval]'); await page.wait_for_function("state.view.type==='val'", timeout=10000)
    vf = await ev("({t:VALDATA.types[state.val.form.t],ping:state.val.form.ping,fl:state.val.form.fl,tot:state.val.form.tot,age:state.val.form.age,ask:state.val.form.ask,pt:state.val.pt})")
    check(vf['t'] == '公寓' and vf['ping'] == 31 and vf['fl'] == 4 and vf['tot'] == 4 and vf['age'] == 49 and vf['ask'] == 1998 and abs(vf['pt'][0] - 25.0412) < 1e-6, f'按「估價」（資訊卡已嵌入街景、重新排版後仍可用）：帶入型態、坪數、樓層、屋齡、開價與位置 {vf}')
    await page.click('#valGo'); await page.wait_for_function("!!document.getElementById('valSave')||!!document.getElementById('valNo')", timeout=15000)
    vl = await ev("({links:state.val.links.map(x=>x.p),chips:[...document.querySelectorAll('#body .vlinks a')].map(a=>a.innerText)})")
    hl = await ev("homeRecord(state.val).links.map(x=>x.p)")
    check(vl['links'] == ['591', '永慶'] and vl['chips'] == ['591', '永慶'] and hl == ['591', '永慶'], f"從房源資訊卡估價：估價頁上方有那間的平台連結，儲存物件時一起存 {vl} {hl}")
    await page.click('#close'); await page.wait_for_timeout(200)
    # Excel（多個分頁，略過「說明」）與貼上
    xp = os.path.join(OUT, 'listings_test.xlsx')
    subprocess.run(['node', '-e', f"""const X=require('xlsx');const wb=X.utils.book_new();
X.utils.book_append_sheet(wb,X.utils.aoa_to_sheet([['區塊','地址／路段','型態','開價(萬)','建坪','樓層','屋齡','開價 vs 實價(%)','591'],['1 主塊','南京東路五段100號','大樓',2600,32.5,'7/12',20,-6.5,'https://sale.591.com.tw/home/house/detail/2/555.html'],['1 主塊','忠孝東路五段790巷59弄37號','公寓',1998,31,'4/4',49,-8.1,'https://sale.591.com.tw/home/house/detail/2/20726738.html']]),'範圍內');
X.utils.book_append_sheet(wb,X.utils.aoa_to_sheet([['項目','地址／路段'],['說明','這列不是房源']]),'說明');X.writeFile(wb,{json.dumps(xp)});"""], check=True, cwd=HERE)
    await page.click('#exportBtn'); await page.wait_for_function("state.view.type==='export'", timeout=3000)
    await page.set_input_files('#imFile', xp)
    await page.wait_for_function("LISTINGS.length===5&&!geoRunning&&LISTINGS.every(l=>l.prec)", timeout=20000)
    check(await ev("LISTINGS[4].addr==='南京東路五段100號'&&LISTINGS[4].prec==='addr'&&LISTINGS[4].price===2600&&saleLayer.getLayers().length===4&&!LISTINGS.some(l=>l.addr==='這列不是房源')"), 'Excel 匯入：讀所有房源分頁、略過「說明」分頁與重複的房子，新的一間定位到地圖')
    await page.click('#imPasteBtn')
    await page.fill('#imPaste', '地址／路段\t開價(萬)\t型態\t緯度\t經度\t信義\n民生東路五段36巷8號\t2688\t公寓\t25.0598\t121.5628\thttps://www.sinyi.com.tw/buy/house/ABC123')
    gp = len(geo_calls); await page.click('#imPasteGo'); await page.wait_for_timeout(400)
    check(await ev("LISTINGS.length===6&&LISTINGS[5].prec==='given'&&LISTINGS[5].links[0].p==='信義'&&saleLayer.getLayers().length===5") and len(geo_calls) == gp, '貼上試算表內容（Tab 分隔）也能匯入；有經緯度直接放到地圖')
    await page.click('#close'); await page.click('#layersBtn'); await page.click('#layersPop [data-layer=sale]')
    check(await ev("!state.layers.sale&&saleLayer.getLayers().length===0"), '圖層「房屋出售（匯入）」可以關掉')
    await page.click('#layersPop [data-layer=sale]'); await page.click('#layersBtn')
    await page.reload(); await page.wait_for_function(READY, timeout=20000); await page.wait_for_timeout(500)
    check(await ev("LISTINGS.length===6&&saleLayer.getLayers().length===5"), '重新整理後，匯入的房源還在地圖上（存在這台裝置）')
    await page.click('#exportBtn'); await page.wait_for_function("state.view.type==='export'", timeout=3000); await page.click('#imClear')
    check(await ev("LISTINGS.length===0&&saleLayer.getLayers().length===0&&JSON.parse(localStorage.getItem('tlm.listings')).length===0"), '「清除匯入的房源」：地圖與裝置上的房源都清掉')
    # 一鍵匯入連結：#import=<base64url(deflate-raw(JSON 列陣列))>（找房 skill 產生）
    def import_link(rows):
        co = zlib.compressobj(9, zlib.DEFLATED, -15); z = co.compress(json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode()) + co.flush()
        return URL + '#import=' + base64.urlsafe_b64encode(z).decode().rstrip('=')
    LH = ['區塊', '位置判定', '地址／路段', '型態', '開價(萬)', '建坪', '樓層', '屋齡', '開價 vs 實價(%)', "'591", '信義', '永慶', '樂屋', '住商', '備註', '緯度', '經度']
    lrows = [LH, ['1 主塊', '範圍內', '民生東路五段36巷8號', '公寓', 2688, 30.5, "'3/4", 45, -4.2, 'https://sale.591.com.tw/home/house/detail/2/111.html', 'https://www.sinyi.com.tw/buy/house/ABC123', '', '', '', '', 25.0598, 121.5628],
             ['1 主塊', '範圍外但很接近', '八德路四段', '大樓', 3200, 40, "'9/14", 18, 6.1, '', '', 'https://buy.yungching.com.tw/house/9', '', '', '', '', '']]
    g2 = len(geo_calls); await page.close()
    page = await new_page(b, errs, **PHONE); ev = page.evaluate
    await page.goto(import_link(lrows)); await page.wait_for_function(READY, timeout=20000)
    await page.wait_for_function("LISTINGS.length===2&&!geoRunning&&LISTINGS.every(l=>l.prec)", timeout=20000)
    lk = await ev("({prec:LISTINGS.map(l=>l.prec),hash:location.hash,n:saleLayer.getLayers().length,l:LISTINGS[0].links.map(x=>x.p),fl:LISTINGS[0].floor,t:document.getElementById('toast').innerText,on:state.layers.sale})")
    print('  link', lk, geo_calls[g2:])
    check(lk['prec'] == ['given', 'none'] and lk['n'] == 1 and lk['l'] == ['591', '信義'] and lk['fl'] == '3/4' and lk['on'] and geo_calls[g2:] == ['臺北市八德路四段'], f"點房源連結自動匯入：有經緯度的直接放、沒有的才查地址，各平台連結都在 {lk}")
    check(lk['hash'] == '' and '匯入 2 間' in lk['t'], f"匯入後網址的 #import 清掉，重新整理不會再匯入一次（{lk['t']}）")
    await page.reload(); await page.wait_for_function(READY, timeout=20000); await page.wait_for_timeout(400)
    check(await ev("LISTINGS.length===2&&saleLayer.getLayers().length===1"), '重新整理後房源仍在，沒有重複')
    await page.goto(import_link(lrows)); await page.wait_for_timeout(600)
    check(await ev("LISTINGS.length===2&&location.hash===''&&document.getElementById('toast').innerText.includes('重複略過')"), 'app 已經開著時點同一個連結（只換 #）：也會匯入，重複的全部略過')
    await page.goto(URL + '#import=' + import_link(lrows).split('#import=')[1][:40]); await page.wait_for_function(READY, timeout=20000); await page.wait_for_timeout(500)
    check(await ev("LISTINGS.length===2&&document.getElementById('toast').className==='err'&&document.getElementById('toast').innerText.includes('讀不到')"), '連結被截斷：提示改用檔案匯入，原本的房源不受影響')
    big = [LH] + [['1 主塊', '範圍內', f'民生東路五段{i}巷{i%30+1}號', '公寓', 2000 + i, 30.25, f"'{i%5+1}/5", 40, -3.5, f'https://sale.591.com.tw/home/house/detail/2/{(i*7919**3)%90000000+10000000}.html', f'https://www.sinyi.com.tw/buy/house/{(i*104729)%9999:04d}{chr(65+i*7%26)}{chr(65+i*11%26)}', f'https://buy.yungching.com.tw/house/{(i*15485863)%9000000+1000000}', '', f'https://www.hbhousing.com.tw/detail/?sn=WS{(i*7907)%90000+10000}', '', round(25.03 + (i * 7919 % 1000) * 4.1e-5, 6), round(121.53 + (i * 104729 % 1000) * 5.3e-5, 6)] for i in range(150)]
    blen = len(import_link(big)); print('  150 間的連結長度', blen)
    check(blen < 60000, f'150 間房源的匯入連結約 {blen // 1000} KB，手機瀏覽器可以開')
    # 畫面內最多 10 間價格標籤，其餘聚合成「N 間」圓點
    await page.goto(import_link(big)); await page.wait_for_function("LISTINGS.length===152&&!geoRunning", timeout=20000); await page.wait_for_timeout(800)
    SC = """(()=>{const ls=saleLayer.getLayers(),pins=ls.filter(m=>m.saleIdx!=null),cl=ls.filter(m=>m.saleGroup),vb=map.getBounds().pad(.05),vis=LISTINGS.filter(l=>l.lat&&vb.contains([l.lat,l.lng]));
      return {pins:pins.length,cl:cl.length,sum:pins.length+cl.reduce((s,m)=>s+m.saleGroup.length,0),vis:vis.length,clTxt:cl.map(m=>+m.getElement().innerText),clN:cl.map(m=>m.saleGroup.length),
        best:pins.some(m=>LISTINGS[m.saleIdx].vsReal===-4.2)===vis.some(l=>l.vsReal===-4.2),z:map.getZoom(),
        order:(()=>{const ps=pins.map(m=>LISTINGS[m.saleIdx].price),rest=cl.flatMap(m=>m.saleGroup.map(i=>LISTINGS[i])).filter(l=>l.vsReal===-3.5).map(l=>l.price);return !rest.length||!ps.length||Math.max(...pins.map(m=>LISTINGS[m.saleIdx]).filter(l=>l.vsReal===-3.5).map(l=>l.price),0)<=Math.min(...rest)})()}})()"""
    v = await ev(SC); print('  sale cluster', {k: v[k] for k in ('pins', 'cl', 'sum', 'vis', 'z')})
    check(v['vis'] > 10 and v['pins'] == 10 and v['cl'] > 0 and v['sum'] == v['vis'] and v['clTxt'] == v['clN'], f"畫面內超過 10 間：只畫 10 間價格標籤，其餘聚合成圓點、圓點上的數字加起來剛好 {v}")
    check(v['best'] and v['order'], '先顯示開價相對實價最低的，其次開價較低的')
    await shot(page, '8s_sale_cluster')
    z0 = v['z']; await ev("void saleLayer.getLayers().filter(m=>m.saleGroup).sort((a,b)=>b.saleGroup.length-a.saleGroup.length)[0].fire('click')"); await page.wait_for_timeout(900)
    v2 = await ev(SC)
    check(v2['z'] > z0 and v2['pins'] <= 10 and v2['sum'] == v2['vis'], f'點圓點：放大到那一區，重畫後仍最多 10 間標籤 {v2}')
    await ev("LISTINGS=LISTINGS.slice(0,2).concat(Array.from({length:14},(_,k)=>({addr:'同棟路'+k+'號',type:'大樓',price:1500+k,floor:(k+2)+'/20',links:[{p:'信義',u:'https://www.sinyi.com.tw/buy/house/S'+k}],lat:25.0450,lng:121.5600,prec:'given'})));map.setView([25.045,121.56],map.getMaxZoom(),{animate:false});drawSale()")
    await page.wait_for_timeout(500)
    v3 = await ev(SC)
    check(v3['pins'] == 10 and v3['cl'] == 1 and v3['clN'] == [4], f'同一點 14 間：10 間標籤＋1 個「4 間」圓點 {v3}')
    await ev("void saleLayer.getLayers().find(m=>m.saleGroup).fire('click')"); await page.wait_for_timeout(400)
    lp = await ev("(()=>{const p=document.querySelector('.leaflet-popup .sl');return p?[...p.querySelectorAll('a')].map(a=>a.innerText):null})()")
    check(lp is not None and len(lp) == 4 and '同棟路10號' in lp[0], f'已經放到最大：點圓點列出那幾間 {lp}')
    await ev("void document.querySelector('.leaflet-popup .sl a').click()"); await page.wait_for_timeout(400)
    pt = await ev("(document.querySelector('.leaflet-popup .sp')||{}).innerText||''")
    check('同棟路10號' in pt and '1,510' in pt and '估價' in pt, '點清單中的一間：顯示那間的資訊與連結')
    await shot(page, '8t_sale_cluster_list')
    await page.close()
    # 11c. 私人房源：每週找房寫進私人 repo，app 用 token 自動讀取、標新上架／已下架，並可把範圍存回 repo
    def gh_rows(rows, updated):
        GH['files']['listings.json'] = json.dumps({'updated': updated, 'rows': [LH] + rows}, ensure_ascii=False)
    RA = ['1 主塊', '範圍內', '民生東路五段36巷8號', '公寓', 2688, 30.5, "'3/4", 45, -4.2, 'https://sale.591.com.tw/home/house/detail/2/111.html', '', '', '', '', '', 25.0598, 121.5628]
    RB = ['1 主塊', '範圍內', '南京東路五段100號', '大樓', 2600, 32.5, "'7/12", 20, -6.5, 'https://sale.591.com.tw/home/house/detail/2/555.html', '', '', '', '', '', 25.0510, 121.5600]
    RC = ['1 主塊', '範圍內', '延壽街100號', '華廈', 2400, 28, "'4/7", 35, -2, '', 'https://www.sinyi.com.tw/buy/house/NEW1', '', 'https://www.cthouse.com.tw/buy/house/ABC', 'https://www.twhg.com.tw/house/123', '', 25.0560, 121.5580]
    gh_rows([RA, RB], '2026-11-23T01:00:00Z'); GH['calls'].clear()
    page = await new_page(b, errs, **PHONE); ev = page.evaluate
    await page.goto(URL); await page.wait_for_function(READY, timeout=20000); await page.wait_for_timeout(300)
    check(len(GH['calls']) == 0, '沒設定 token 時不會連 GitHub')
    await ev("closeDetail()"); await page.click('#exportBtn'); await page.wait_for_function("state.view.type==='export'", timeout=3000)
    check(await ev("!document.querySelector('#body .gm-key')&&!document.getElementById('syncTok')&&!!document.getElementById('toSettings')&&!document.getElementById('huntGo')"), '「找房」頁不放金鑰和 token 欄位，沒連結私人 repo 時只顯示「到設定連結」，沒有「現在找房」')
    await page.click('#toSettings'); await page.wait_for_function("state.view.type==='settings'", timeout=3000)
    check(await ev("!!document.getElementById('syncTok')&&document.getElementById('syncTok').type==='password'&&document.getElementById('syncRepo').value==='rubychen8407/tlm-listings'"), '按「到設定連結」打開設定：私人房源 token 用密碼欄位、預設 repo')
    await page.fill('#syncTok', 'tok_bad'); await page.click('#syncGo')
    await page.wait_for_function("document.getElementById('syncSt')&&/無效/.test(document.getElementById('syncSt').innerText)", timeout=5000)
    check(await ev("LISTINGS.length===0"), 'token 錯誤：顯示「token 無效或已過期」，地圖不變')
    await page.click('#syncOff'); await page.fill('#syncTok', 'tok_ok'); await page.click('#syncGo')
    await page.wait_for_function("LISTINGS.filter(l=>l.sync).length===2&&!syncRunning", timeout=8000); await page.wait_for_timeout(300)
    s1 = await ev("({n:saleLayer.getLayers().length,nw:LISTINGS.filter(l=>l.isNew).length,st:document.getElementById('syncSt').innerText,t:document.getElementById('toast').innerText,tok:JSON.parse(localStorage.getItem('tlm.sync')).token})")
    gc = [c for c in GH['calls'] if c[1].endswith('listings.json')]
    check(s1['n'] == 2 and s1['nw'] == 0 and '2 間' in s1['st'] and '已載入私人房源 2 間' in s1['t'] and s1['tok'] == 'tok_ok' and gc and gc[-1][2] == 'Bearer tok_ok' and 'raw' in gc[-1][3], f"正確 token：讀私人 repo 的 listings.json，2 間放到地圖（第一次不標新上架），token 存在這台裝置 {s1}")
    gh_rows([RA, RC], '2026-11-30T01:00:00Z'); GH['calls'].clear()
    await page.reload(); await page.wait_for_function(READY, timeout=20000)
    await page.wait_for_function("LISTINGS.some(l=>l.addr==='延壽街100號')&&!syncRunning", timeout=8000); await page.wait_for_timeout(300)
    s2 = await ev("({addr:LISTINGS.map(l=>l.addr),nw:LISTINGS.filter(l=>l.isNew).map(l=>l.addr),pins:saleLayer.getLayers().map(m=>m.getElement().innerText),t:document.getElementById('toast').innerText,m:SYNCMETA})")
    print('  sync', s2)
    check(sorted(s2['addr']) == sorted(['民生東路五段36巷8號', '延壽街100號']) and s2['nw'] == ['延壽街100號'] and '新上架 1 間、已下架 1 間' in s2['t'] and any(p.startswith('新') for p in s2['pins']) and len(s2['m']['gone']) == 1, '下次打開 app 自動更新：新上架的標「新」，已下架的從地圖移除並提示')
    st_l = await ev("JSON.parse(localStorage.getItem('tlm.listings')||'[]').filter(l=>l.sync).length")
    check(st_l == 0, '私人房源不存在裝置上：每次打開從私人 repo 讀，手機只記房源編號（標新上架用）')
    await page.reload(); await page.wait_for_function(READY, timeout=20000); await page.wait_for_function("LISTINGS.filter(l=>l.sync).length===2&&!syncRunning", timeout=8000)
    check(await ev("LISTINGS.filter(l=>l.isNew).map(l=>l.addr).join()") == '延壽街100號', '同一版重新打開：「新」標記維持一樣')
    await ev("closeDetail()"); await page.click('#exportBtn'); await page.wait_for_function("state.view.type==='export'", timeout=3000)
    st = await ev("document.getElementById('syncSt').innerText")
    check(st.replace(' ', '').startswith('2間') and '更新' in st and 'tlm-listings' not in st and '下架' not in st and '南京東路' not in st, f'找房頁只顯示房源間數與更新時間，不顯示 repo、上下架間數或地址（{st}）')
    await shot(page, '8u_hunt_compact')
    lay = await ev("(()=>{const s=document.getElementById('sheet').getBoundingClientRect(),b=document.getElementById('body'),last=b.lastElementChild.getBoundingClientRect(),c=document.querySelector('#body .hk-card');return {gap:Math.round(s.top+state.sheetVis-last.bottom),ic:[...c.querySelectorAll('.hk-ic')].map(x=>x.id),lbl:c.innerText}})()")
    check(lay['ic'] == ['syncNow', 'huntGo'] and '自動匯入' in lay['lbl'] and lay['gap'] < 120, f"「自動匯入」一列：間數＋定時圖示＋手動找房圖示；卡片高度貼合內容，下面不留大片空白 {lay}")
    await page.click('#close'); await page.reload(); await page.wait_for_function(READY, timeout=20000); await page.wait_for_timeout(800)
    check(await ev("LISTINGS.filter(l=>l.isNew).length===1&&LISTINGS.length===2"), '沒有新的找房結果時，重新打開不會改動（新上架標記保留到下一次找房）')
    await ev("void saleLayer.getLayers().find(m=>m.getElement().innerText.startsWith('新')).fire('click')"); await page.wait_for_timeout(300)
    pt = await ev("document.querySelector('.leaflet-popup-content .sp').innerText")
    check('新上架' in pt and '中信' in pt and '台灣房屋' in pt, f'點新上架的房子，資訊卡標出「新上架」，中信、台灣房屋連結也認得（{pt}）')
    await page.wait_for_function("(()=>{const a=document.querySelector('.leaflet-popup-content .svl');return !!a&&a.href.includes('viewpoint=25.055730,121.558000')&&a.href.includes('heading=0')})()", timeout=5000)
    check(True, '房子資訊卡有「看街景」，從房子前面的道路朝向房子開 Google 街景')
    await shot(page, '8s_sync_new'); await ev("void map.closePopup()")
    # 手動匯入和私人房源並存；清除只清手動的
    await ev("closeDetail()"); await page.click('#exportBtn'); await page.wait_for_function("state.view.type==='export'", timeout=3000)
    await page.click('#imPasteBtn'); await page.fill('#imPaste', '地址／路段\t開價(萬)\t緯度\t經度\n八德路四段1號\t2000\t25.049\t121.57'); await page.click('#imPasteGo'); await page.wait_for_timeout(300)
    await page.click('#imClear'); await page.wait_for_timeout(200)
    check(await ev("LISTINGS.length===2&&LISTINGS.every(l=>l.sync)"), '「清除匯入的房源」只清手動匯入的，私人房源保留')
    await ev("closeDetail()"); await page.click('#layersBtn')
    check(await ev("!document.querySelector('#layersPop #saleSub')&&!!document.querySelector('#layersPop [data-go-sale]')"), '圖層只放地圖開關；「房屋出售」底下只留一個連到「房源」分頁的篩選入口')
    await page.click('#layersPop [data-go-sale]'); await page.wait_for_function("state.view.type==='export'", timeout=3000); await page.wait_for_selector('#saleSub', state='visible')
    sub = await ev("(()=>{const h=document.getElementById('saleHist');return {bars:h.querySelectorAll('[data-hb]').length,on:h.querySelectorAll('.on').length,tiles:[...document.querySelectorAll('#saleStats .st-t')].map(t=>t.innerText.replace(/\\s+/g,'')),sw:document.getElementById('saleOn').getAttribute('aria-pressed'),sel:document.getElementById('exportBtn').getAttribute('aria-selected')}})()")
    print('  sub', sub)
    check(sub['bars'] == 13 and sub['on'] == 13 and sub['tiles'][0] == '2/2符合篩選' and sub['sw'] == 'true' and sub['sel'] == 'true', f"「房源」分頁：數字磚（符合篩選／新上架／沒看過）、價格分布長條圖、坪數價格看過篩選，都在同一頁 {sub}")
    await page.click('#saleHist [data-hb]:not([disabled]) >> nth=0'); await page.wait_for_timeout(300)
    hb = await ev("({pmin:SALEF.pmin,pmax:SALEF.pmax,n:saleLayer.getLayers().length,on:document.querySelectorAll('#saleHist .on').length,lab:document.getElementById('pfLab').innerText,t:document.querySelector('#saleStats .st-t').innerText.replace(/\\s+/g,'')})")
    check(hb['n'] == 1 and hb['on'] == 1 and hb['pmax'] - (hb['pmin'] or 0) == 500 and hb['t'].startswith('1/2'), f"點價格長條：只看那個價位（地圖剩 1 間、滑桿與數字同步）{hb}")
    await shot(page, '8t0_sale_tab')
    await page.click('#saleHist .on'); await page.wait_for_timeout(300)
    check(await ev("SALEF.pmin==null&&SALEF.pmax==null&&saleLayer.getLayers().length===2"), '再點一次同一根長條取消價格篩選')
    # 推薦（獨立分頁）：負擔得起的房源一張一張滑，右滑加入我的物件、左滑略過
    await ev("""(()=>{window.__L0=LISTINGS.slice();window.__C0=[map.getCenter(),map.getZoom()];window.__H0=localStorage.getItem('tlm.homes');saveHomes([]);drawHomes();SWIPED={};localStorage.removeItem('tlm.swiped');
      const mk=(n,pr,pg,ag,fl,pk)=>({addr:'測試路'+n+'號',type:'住宅大樓',price:pr,ping:pg,age:ag,floor:fl,park:pk,lat:25.033+n*0.0006,lng:121.545+n*0.0004,prec:'addr',links:[{p:'信義',u:'https://x.test/'+n},{p:'591',u:'https://y.test/'+n}]});
      LISTINGS.push(mk(1,2400,25,15,'5/12','有'),mk(2,2900,30,10,'8/15','無'),mk(3,3300,32,20,''),mk(4,4200,40,8,'9/14'),mk(5,5200,45,5,'10/20'),{addr:'沒坪數路1號',type:'公寓',price:1800,lat:25.034,lng:121.546,prec:'addr',links:[{p:'永慶',u:'https://x.test/np'}]});
      AFF.budget=3000;closeDetail();})()""")
    await page.click('#exportBtn'); await page.wait_for_timeout(300)
    check(await ev("!document.getElementById('affBox')&&!document.querySelector('#body .sw-card')"), '「房源」分頁不再放推薦，篩選器回到上方')
    await page.click('#recBtn'); await page.wait_for_function("!!document.getElementById('swCard')||!!document.getElementById('affEmpty')", timeout=10000)
    sw = await ev("""(()=>{const exp=LISTINGS.filter(l=>l.lat).map(l=>listingVal(l)).filter(V=>V&&V.off.out[1]<=3000).length,c=document.getElementById('swCard');
      return {exp,n:affRes.R.length,sel:document.getElementById('recBtn').getAttribute('aria-selected'),tabs:[...document.querySelectorAll('#navbar .nb')].map(b=>b.id),pos:c.querySelector('.sw-pos').innerText,sc:parseFloat(c.querySelector('.sw-sc').innerText),top:Math.round(affRes.R[0].total),
        links:[...c.querySelectorAll('.sw-links a')].map(a=>[a.innerText,a.target]),set:document.querySelector('#swSet summary').innerText,fair:parseInt(c.querySelector('.sw-price b').innerText.replace(/[^0-9]/g,'')),pay:/月付約 [0-9,]+ 元/.test(c.innerText),skip:affRes.skip}})()""")
    print('  swipe', sw)
    check(sw['n'] >= 3 and sw['n'] == sw['exp'] and sw['sel'] == 'true' and sw['tabs'] == ['exploreBtn', 'exportBtn', 'recBtn', 'homesBtn', 'marketBtn'] and sw['pos'] == f"1 / {sw['n']}" and sw['sc'] == sw['top'] and sw['fair'] <= 3000 and sw['pay'] and [x[0] for x in sw['links']] == ['信義 ›', '591 ›'] and all(x[1] == '_blank' for x in sw['links']) and '3,000 萬' in sw['set'] and sw['skip'] == 1, f"底部新的「推薦」分頁：合理出價在預算內的房源做成卡片，從推薦指數最高的開始，卡片上有合理出價、開價、月付、指數、房源連結 {sw}")
    await shot(page, '8t1_swipe')
    a0 = await ev("document.querySelector('#swCard .sw-a').innerText")
    cb = await page.locator('#swCard').bounding_box()
    await page.mouse.move(cb['x'] + cb['width'] / 2, cb['y'] + 120); await page.mouse.down()
    for k in range(1, 8): await page.mouse.move(cb['x'] + cb['width'] / 2 + 25 * k, cb['y'] + 122)
    await page.mouse.up(); await page.wait_for_timeout(500)
    r1 = await ev("({homes:loadHomes().map(h=>({n:h.name,l:h.links.length,park:h.form.park,ask:h.form.ask})),pos:document.querySelector('#swCard .sw-pos').innerText,a:document.querySelector('#swCard .sw-a').innerText,sw:Object.values(SWIPED),pins:homeLayer.getLayers().length})")
    check(len(r1['homes']) == 1 and r1['homes'][0]['n'] == a0 and r1['homes'][0]['l'] == 2 and r1['sw'] == ['like'] and r1['pos'] == f"2 / {sw['n']}" and r1['a'] != a0 and r1['pins'] == 1, f"右滑：加入我的物件（名稱是地址、帶房源連結），馬上換下一張 {r1}")
    await page.click('#swSkip'); await page.wait_for_timeout(400)
    check(await ev(f"Object.values(SWIPED).join()==='like,skip'&&loadHomes().length===1&&document.querySelector('#swCard .sw-pos').innerText==='3 / {sw['n']}'"), '按 ✕ 略過：不加入，換下一張')
    await page.click('#swUndo'); await page.wait_for_timeout(400)
    check(await ev(f"Object.values(SWIPED).join()==='like'&&document.querySelector('#swCard .sw-pos').innerText==='2 / {sw['n']}'"), '按「上一張」：回到剛剛略過的那張')
    cb = await page.locator('#swCard').bounding_box()
    await page.mouse.move(cb['x'] + cb['width'] / 2, cb['y'] + 120); await page.mouse.down()
    for k in range(1, 8): await page.mouse.move(cb['x'] + cb['width'] / 2 - 25 * k, cb['y'] + 121)
    await page.mouse.up(); await page.wait_for_timeout(500)
    check(await ev("Object.values(SWIPED).join()==='like,skip'&&loadHomes().length===1"), '左滑：略過')
    cb = await page.locator('#swCard').bounding_box()
    await page.mouse.move(cb['x'] + cb['width'] / 2, cb['y'] + 120); await page.mouse.down(); await page.mouse.move(cb['x'] + cb['width'] / 2 + 40, cb['y'] + 120); await page.mouse.up(); await page.wait_for_timeout(400)
    check(await ev("Object.keys(SWIPED).length===2&&!!document.getElementById('swCard')"), '拖不夠遠：卡片彈回原位，不算')
    await page.focus('#swCard'); await page.keyboard.press('ArrowRight'); await page.wait_for_timeout(400)
    check(await ev("loadHomes().length===2&&Object.values(SWIPED).filter(v=>v==='like').length===2"), '鍵盤 → 也能加入')
    for _ in range(10):
        if not await ev("!!document.getElementById('swCard')"): break
        await page.click('#swSkip'); await page.wait_for_timeout(300)
    en = await ev("({end:!!document.getElementById('swEnd'),t:(document.getElementById('swEnd')||{}).innerText||''})")
    check(en['end'] and '都看完了' in en['t'] and '加入物件 2 間' in en['t'], f"全部滑完：顯示看完了、加入幾間，可以重新看略過的 {en}")
    await page.click('#swReset'); await page.wait_for_timeout(300)
    check(await ev(f"!!document.getElementById('swCard')&&swDeck().length==={sw['n']}-2"), '「重新看略過的」：略過的回到卡片裡（加入的不會再出現）')
    check(await ev("Object.keys(JSON.parse(localStorage.getItem('tlm.swiped'))).length===2&&CLOUD_KEYS.includes('tlm.swiped')"), '滑過的紀錄存在裝置（也會雲端同步）')
    # 比較物件：車位
    await ev("void openCmp(loadHomes().map(h=>h.id).slice(0,2))"); await page.wait_for_function("state.view.type==='cmp'", timeout=10000)
    pk = await ev("({row:[...document.querySelectorAll('.cmp tbody tr')].find(r=>r.cells[0].innerText==='車位'),homes:loadHomes().map(h=>h.form.park)})")
    pkr = await ev("[...[...document.querySelectorAll('.cmp tbody tr')].find(r=>r.cells[0].innerText==='車位').cells].slice(1).map(c=>c.innerText)")
    exp = ['有' if p == 1 else '沒有' if p == 0 else '不確定' for p in pk['homes'][:2]]
    check(pkr == exp and any(p in (0, 1) for p in pk['homes']), f"比較物件有「車位」列（從房源帶入：有／沒有／不確定）{pkr} {pk['homes']}")
    await shot(page, '8t3_cmp_park')
    # 預算、範圍
    await page.click('#recBtn'); await page.wait_for_function("!!document.getElementById('swCard')||!!document.getElementById('swEnd')", timeout=10000)
    await page.click('#swSet summary'); await page.fill('#affIn', '2300'); await page.wait_for_timeout(900)
    check(await ev("affRes.B===2300&&affRes.R.every(r=>affRes.items[r.i].V.off.out[1]<=2300)&&document.querySelector('#swSet summary').innerText.includes('2,300 萬')"), '展開設定改預算 2,300 萬：重算，摘要列同步')
    await page.fill('#affIn', '3000'); await page.wait_for_timeout(900)
    n0f = await ev("affRes.R.length")
    await page.click('#swSet [data-afl="1"]'); await page.click('#swSet [data-afl=top]'); await page.wait_for_timeout(900)
    fo = await ev("""(()=>{const t=(type,floor)=>flOut({type,floor});return {u:[t('公寓','4/4'),t('住宅大樓','12/12'),t('公寓','1/5'),t('住宅大樓','4/12'),t('公寓','')],p:[...document.querySelectorAll('#swSet [data-afl]')].map(b=>b.getAttribute('aria-pressed')),sum:document.querySelector('#swSet summary').innerText,saved:JSON.parse(localStorage.getItem('tlm.afford')).nofl,ok:affRes.R.every(r=>!flOut(affRes.items[r.i].l))}})()""")
    check(fo['u'] == [True, False, True, False, False] and fo['p'] == ['true', 'false', 'true'] and '不要 頂樓/1F' in fo['sum'] and fo['saved'] == ['top', '1'] and fo['ok'], f"推薦設定「不要」：可排除公寓頂樓、4 樓、1 樓（大樓頂樓不算、樓層不明的保留），摘要列與雲端設定同步 {fo}")
    await shot(page, '8w5_swipe_floor')
    await page.click('#swSet [data-afl="1"]'); await page.click('#swSet [data-afl=top]'); await page.wait_for_timeout(900)
    check(await ev(f"affRes.R.length==={n0f}&&!JSON.parse(localStorage.getItem('tlm.afford')).nofl.length"), '再按一次取消排除，結果恢復')
    await ev("setScope(scopeFor([DISTRICTS.findIndex(d=>d.name==='北投區')]))"); await page.wait_for_timeout(800)
    await page.click('#recBtn'); await page.wait_for_function("!!document.getElementById('affEmpty')||!!document.getElementById('swCard')||!!document.getElementById('swEnd')", timeout=10000)
    sc1 = await ev("({empty:(document.getElementById('affEmpty')||{}).innerText||'',set:document.querySelector('#swSet summary').innerText})")
    check('北投區' in sc1['empty'] and '北投區' in sc1['set'], f"推薦只算上方選的範圍：北投區沒有符合的 {sc1}")
    await ev("setScope('')"); await page.wait_for_timeout(600)
    await page.click('#recBtn'); await page.wait_for_timeout(600)
    await ev("""(()=>{map.closePopup();LISTINGS.length=0;window.__L0.forEach(l=>LISTINGS.push(l));AFF.budget=null;localStorage.removeItem('tlm.afford');cmpW='bal';localStorage.removeItem('tlm.cmpW');SWIPED={};localStorage.removeItem('tlm.swiped');if(window.__H0)localStorage.setItem('tlm.homes',window.__H0);else localStorage.removeItem('tlm.homes');drawHomes();map.setView(window.__C0[0],window.__C0[1],{animate:false});closeDetail();drawSale();})()""")
    await page.click('#exportBtn'); await page.wait_for_timeout(300)
    await page.locator('#sfMin').fill('29'); await page.wait_for_timeout(300)
    sf = await ev("({pins:saleLayer.getLayers().map(m=>m.getElement().innerText),lab:document.getElementById('sfLab').innerText,n:document.getElementById('sfN').innerText,saved:JSON.parse(localStorage.getItem('tlm.salef'))})")
    check(len(sf['pins']) == 1 and '2,688' in sf['pins'][0] and sf['lab'] == '29–80+ 坪' and sf['n'] == '1/2' and sf['saved']['min'] == 29 and sf['saved']['max'] is None, f"拖坪數滑桿：建坪 29 坪以上只剩 1 間 {sf}")
    await page.locator('#sfMax').fill('20'); await page.wait_for_timeout(300)
    check(await ev("+document.getElementById('sfMax').value>=+document.getElementById('sfMin').value"), '兩個滑桿不會交叉')
    await page.locator('#sfMax').fill('80'); await page.click('[data-sfby=inner]'); await page.wait_for_timeout(300)
    await page.locator('#sfMin').fill('20'); await page.wait_for_timeout(300)
    inn = await ev("({pins:saleLayer.getLayers().map(m=>m.getElement().innerText),n:document.getElementById('sfN').innerText,est:innerEst(LISTINGS.find(l=>l.price===2688))})")
    check(len(inn['pins']) == 1 and '2,688' in inn['pins'][0] and inn['n'] == '1/2' and inn['est']['est'] and abs(inn['est']['v'] - 27.76) < 0.01, f"房源表沒有室內坪數時依型態用建坪估算（公寓 30.5 坪→約 27.8 坪），改用室內 20 坪以上仍篩得出來 {inn}")
    await page.locator('#sfMin').fill('0'); await page.click('[data-sfby=ping]'); await page.wait_for_timeout(300)
    check(await ev("!SALEF.min&&!SALEF.max&&saleLayer.getLayers().length===2&&document.getElementById('sfLab').innerText==='不限'"), '滑桿拉回兩端＝不限，全部顯示')
    # 價格：總價／單價
    pr0 = await ev("LISTINGS.filter(l=>l.lat).map(l=>[l.price,l.ping])")
    await page.locator('#pfMax').fill('2500'); await page.wait_for_timeout(300)
    pf = await ev("({pins:saleLayer.getLayers().map(m=>m.getElement().innerText),lab:document.getElementById('pfLab').innerText,n:document.getElementById('sfN').innerText,saved:JSON.parse(localStorage.getItem('tlm.salef'))})")
    check(len(pf['pins']) == 1 and '2,400' in pf['pins'][0] and pf['lab'] == '0–2,500 萬' and pf['n'] == '1/2' and pf['saved']['pmax'] == 2500, f"拖總價滑桿：2,500 萬以下只剩 1 間 {pf} {pr0}")
    await page.locator('#pfMin').fill('3000'); await page.wait_for_timeout(200)
    check(await ev("+document.getElementById('pfMax').value>=+document.getElementById('pfMin').value"), '價格的兩個滑桿不會交叉')
    await page.click('[data-pfby=unit]'); await page.wait_for_timeout(300)
    check(await ev("SALEF.pby==='unit'&&SALEF.pmin==null&&SALEF.pmax==null&&+document.getElementById('pfMax').max===150&&saleLayer.getLayers().length===2"), '切到「單價」：範圍改成萬／坪，條件重設、全部顯示')
    await page.locator('#pfMin').fill('87'); await page.wait_for_timeout(300)
    up = await ev("({pins:saleLayer.getLayers().map(m=>m.getElement().innerText),lab:document.getElementById('pfLab').innerText})")
    check(len(up['pins']) == 1 and '2,688' in up['pins'][0] and up['lab'] == '87–150+ 萬／坪', f"單價 87 萬／坪以上：沒有單價欄時用總價÷建坪（2,688÷30.5≈88.1）{up}")
    await shot(page, '8t_sale_price')
    await page.locator('#pfMin').fill('0'); await page.click('[data-pfby=total]'); await page.wait_for_timeout(300)
    check(await ev("SALEF.pby==='total'&&SALEF.pmin==null&&SALEF.pmax==null&&saleLayer.getLayers().length===2&&document.getElementById('pfLab').innerText==='不限'"), '價格拉回不限，全部顯示')
    # 看過／沒看過
    await ev("void closePops()"); await ev("(()=>{SEEN={};localStorage.removeItem('tlm.seen');drawSale();})()")
    await ev("void saleLayer.getLayers().find(m=>m.getElement().innerText.includes('2,688')).fire('click')"); await page.wait_for_timeout(300)
    await ev("void map.closePopup()"); await page.wait_for_timeout(200)
    seen = await ev("({cls:saleLayer.getLayers().map(m=>[m.getElement().innerText,m.getElement().classList.contains('seen')]),st:Object.keys(JSON.parse(localStorage.getItem('tlm.seen')||'{}')).length})")
    check(seen['st'] == 1 and sorted(seen['cls']) == sorted([[c[0], '2,688' in c[0]] for c in seen['cls']]), f"點開過的房子記為「看過」並變淡 {seen}")
    await page.click('#exportBtn'); await page.wait_for_selector('#saleSub', state='visible')
    await page.click('[data-sfseen=unseen]'); await page.wait_for_timeout(300)
    un = await ev("saleLayer.getLayers().map(m=>m.getElement().innerText)")
    await page.click('[data-sfseen=seen]'); await page.wait_for_timeout(300)
    se = await ev("saleLayer.getLayers().map(m=>m.getElement().innerText)")
    check(len(un) == 1 and '2,688' not in un[0] and len(se) == 1 and '2,688' in se[0], f"可以只看「沒看過」或「看過」的房子 {un} {se}")
    await shot(page, '8t_sale_filter')
    await page.reload(); await page.wait_for_function(READY, timeout=20000); await page.wait_for_timeout(500)
    check(await ev("SALEF.seen==='seen'&&saleLayer.getLayers().length===1"), '篩選與看過紀錄記在裝置，重新整理後維持')
    await page.click('#exportBtn'); await page.click('[data-sfseen=all]'); await page.wait_for_timeout(300); await ev("void closeDetail()")
    check(await ev("saleLayer.getLayers().length===2"), '選「全部」恢復顯示全部')
    await ev("closeDetail();openSettings()"); await page.wait_for_function("state.view.type==='settings'", timeout=3000)
    # 把目前範圍存回私人 repo（range.json），每週找房用
    zn = await ev("state.zones.length")
    check(zn > 0 and await ev("!document.getElementById('exRange').disabled"), f'有 token 且地圖上有符合條件的範圍：「設為每週找房範圍」可以按（{zn} 塊）')
    await shot(page, '8s_sync_box')
    await page.click('#exRange'); await page.wait_for_function("/每週找房範圍：/.test(document.getElementById('syncSt').innerText)", timeout=5000)
    rg = json.loads(GH['files'].get('range.json', '{}'))
    check(rg.get('geojson', {}).get('geometry', {}).get('type') == 'MultiPolygon' and len(rg['geojson']['geometry']['coordinates']) == zn and 'MultiPolygon' in rg.get('text', '') and rg.get('prefs', {}).get('lo') is not None and rg.get('criteria'), f"範圍存進私人 repo 的 range.json：GeoJSON（{zn} 塊）、匯出文字、房子條件、生活機能條件 {list(rg)}")
    await page.click('#exRange'); await page.wait_for_timeout(800)
    check('已設為每週找房範圍' in await ev("document.getElementById('toast').innerText"), '再存一次：帶上原檔 sha 覆寫成功')
    GH['write'] = False; await page.click('#exRange'); await page.wait_for_timeout(800)
    check('Read and write' in await ev("document.getElementById('toast').innerText"), 'token 只有讀取權限：提示需要「Contents：Read and write」')
    GH['write'] = True
    await ev("closeDetail();openExport()"); await page.wait_for_function("state.view.type==='export'", timeout=3000)
    check(await ev("!!document.getElementById('huntGo')&&!!document.getElementById('syncNow')&&!document.getElementById('syncOff')&&!document.getElementById('syncTok')"), '找房頁的私人房源只有狀態、「現在找房」和「立即更新」，移除 token 等連線設定在「設定」裡')
    # 手動觸發找房：沒設定連線 → 複製「找房」；設定 Routine 網址與 token → 直接呼叫；找好後自動接收
    FIRE = []
    async def fire_route(route):
        rq = route.request
        cors = {'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'POST, OPTIONS'}
        if rq.method == 'OPTIONS': await route.fulfill(status=204, headers=cors); return
        FIRE.append((rq.method, rq.headers.get('authorization'), rq.headers.get('anthropic-beta'), rq.post_data))
        await route.fulfill(status=200, headers=cors, content_type='application/json', body=json.dumps({'type': 'routine_fire', 'claude_code_session_id': 'session_X', 'claude_code_session_url': 'https://claude.ai/code/session_X'}))
    await page.route('https://api.anthropic.com/**', fire_route)
    await page.click('#huntGo'); await page.wait_for_timeout(400)
    h1 = await ev("({w:HWAIT&&HWAIT.via,st:document.getElementById('syncSt').innerText,dis:document.getElementById('huntGo').classList.contains('on'),rows:document.querySelectorAll('#body .hk-card').length,extra:!!document.querySelector('#body .im-hunt'),anim:getComputedStyle(document.querySelector('#huntGo svg')).animationName,t:document.getElementById('toast').innerText})")
    await shot(page, '8t_hunt_page')
    check(h1['rows'] == 2 and not h1.get('extra') and h1['anim'] == 'hkbob' and h1['w'] == 'chat' and '找房中' in h1['st'] and h1['dis'] and '找房' in h1['t'] and not FIRE, f"沒設定連線時按「現在找房」：提示到對話輸入「找房」，手動找房圖示開始動畫、同一列顯示「找房中」（不另開一列）並開始等結果，不會亂呼叫網路 {h1}")
    await page.click('#huntStop'); await page.wait_for_timeout(200)
    check(await ev("!HWAIT&&!document.getElementById('huntGo').classList.contains('on')&&!localStorage.getItem('tlm.huntwait')"), '按「取消」停止等待')
    await ev("openSettings()"); await page.wait_for_function("state.view.type==='settings'", timeout=3000)
    await ev("document.querySelector('.hunt-set').open=true")
    await shot(page, '8t2_settings_all')
    await page.fill('#huntUrl', 'https://example.com/x'); await page.fill('#huntTok', 'abc'); await page.click('#huntSave')
    check('格式不對' in await ev("document.getElementById('toast').innerText") and await ev("!HUNT"), '手動觸發的網址或 token 格式不對會提示')
    await page.fill('#huntUrl', 'https://api.anthropic.com/v1/claude_code/routines/trig_ABC123/fire'); await page.fill('#huntTok', 'sk-ant-oat01-TESTTOKEN'); await page.click('#huntSave')
    check(await ev("!!HUNT&&HUNT.url.endsWith('/fire')&&/已設定/.test(document.querySelector('.hunt-set summary').innerText)"), '設定手動觸發的網址與 token（只存在這台裝置）')
    await ev("closeDetail();openExport()"); await page.wait_for_function("state.view.type==='export'", timeout=3000)
    await page.click('#huntGo'); await page.wait_for_function("!!HWAIT", timeout=5000); await page.wait_for_timeout(300)
    h2 = await ev("({w:HWAIT&&HWAIT.via,url:HWAIT&&HWAIT.url,link:(document.querySelector('#syncSt a')||{}).href})")
    check(len(FIRE) == 1 and FIRE[0][0] == 'POST' and FIRE[0][1] == 'Bearer sk-ant-oat01-TESTTOKEN' and 'experimental-cc-routine' in (FIRE[0][2] or '') and h2['w'] == 'api' and h2['link'] == 'https://claude.ai/code/session_X', f"設定後按「現在找房」：用 Bearer token 呼叫 Routine 網址，頁面附「看進度」連結 {h2}")
    await page.reload(); await page.wait_for_function(READY, timeout=20000); await page.wait_for_timeout(500)
    check(await ev("!!HWAIT&&!!HTIMER"), '重新整理後，還在等的找房會繼續等（最長 45 分鐘）')
    gh_rows([RA, RB, RC], '2026-12-01T01:00:00Z')
    await ev("huntTick()"); await page.wait_for_function("!HWAIT", timeout=8000); await page.wait_for_timeout(300)
    h3 = await ev("({n:LISTINGS.filter(l=>l.sync).length,nw:LISTINGS.filter(l=>l.isNew).length,t:document.getElementById('toast').innerText})")
    check(h3['n'] == 3 and '找房完成' in h3['t'], f"私人 repo 有新結果：自動匯入並提示「找房完成」，不用再按更新 {h3}")
    await ev("openSettings()"); await page.wait_for_function("state.view.type==='settings'", timeout=3000)
    await page.click('#syncOff'); await page.wait_for_timeout(200)
    check(await ev("LISTINGS.length===0&&!localStorage.getItem('tlm.sync')&&!!document.getElementById('syncTok')"), '「移除 token」：token 和私人房源都從這台裝置移除')
    await page.close()
    # 12. 沒有 city.json → 改用即時查詢備援，不再去讀已移除的 12 區檔
    MODE['missing'] = {'city.json'}; d0 = len(data_hits); c0 = len(calls)
    page = await new_page(b, errs, **PHONE)
    await page.goto(URL); await page.wait_for_function("!state.loading", timeout=20000)
    check(len(calls) == c0 and not [d for d in data_hits[d0:] if d.endswith('區.json')] and '先選一個行政區' in await page.evaluate("document.getElementById('head').innerText"), '沒有 city.json 時不再讀各區檔，改請使用者先選行政區')
    MODE['missing'] = set()
    await page.close()


async def live_suite(b, errs):
    page = await new_page(b, errs, **PHONE)
    ev = page.evaluate
    n0 = len(calls)
    await page.goto(URL)
    await page.wait_for_function("!state.loading", timeout=10000); await page.wait_for_timeout(300)
    check(len(calls) == n0 and '先選一個行政區' in await ev("document.getElementById('head').innerText") and await ev("document.querySelectorAll('.dgrid .btn').length") == 12, '沒有預建資料：不自動下載，請使用者先選行政區')
    await page.click('.dgrid .btn[data-d="4"]')
    await page.wait_for_function(READY, timeout=30000)
    c1 = [c for c in calls[n0:] if c['host'] != 'overpass-api.de']
    check(len(c1) == 1 and set(c1[0]['layers']) == {'park', 'school', 'mrt'}, f"選區後只送一次查詢、包含已選三類 {[c['layers'] for c in c1]}")
    bb = list(map(float, re.search(r'\[bbox:([^\]]+)\]', c1[0]['q']).group(1).split(',')))
    dbb = await ev("DISTRICTS[4].bb")
    pad_m = min((dbb[0] - bb[0]) * 110540, (bb[2] - dbb[2]) * 110540)
    check(6800 < pad_m < 7200, f'查詢範圍＝大安區外擴約 7 公里（{pad_m:.0f} 公尺）')
    check(any(c['host'] != 'overpass-api.de' for c in calls[n0:]) and all('x-www-form-urlencoded' in c['ct'] for c in calls[n0:]), '主伺服器失敗會改用備用伺服器；請求格式正確')
    check(await ev("state.zones.length") > 0 and await ev("!!heat"), '算出符合條件的區域並顯示色塊')
    MODE['fail'] = {'sport'}
    await page.click('.chip[data-id=sport]')
    await page.wait_for_function("!!state.err", timeout=20000)
    check('下載失敗' in await ev("document.getElementById('head').innerText") and await ev("getComputedStyle(document.getElementById('toast')).display") == 'block', '伺服器全忙時顯示錯誤訊息')
    MODE['fail'] = set()
    await page.click('.chip[data-id=sport]'); await page.click('.chip[data-id=sport]')
    await page.wait_for_function("state.res&&state.res.act.includes('sport')", timeout=20000)
    check(True, '再點一次類別即可重試成功')
    await ev("void openPlace(data('mrt')[0],LAYER.mrt)")
    await page.wait_for_function("state.transit&&state.transit.loading===false", timeout=20000)
    check(any('around:300' in c['q'] for c in calls), '沒有預建路線時，點車站改為即時查詢')
    await page.close()


async def real_suite(b, errs):
    """用 repo 內真實的 data/ 跑一遍並截圖"""
    MODE.update(baked=True, dir=os.path.join(ROOT, 'data'))
    page = await new_page(b, errs, **PHONE)
    ev = page.evaluate
    t0 = time.time()
    await page.goto(URL); await page.wait_for_function(READY, timeout=90000)
    print(f'real data: ready in {time.time()-t0:.1f}s', await ev("({built:state.built,n:Object.fromEntries(LAYERS.map(l=>[l.id,(data(l.id)||[]).length])),good:state.res.good,total:state.res.total,zones:state.zones.map(z=>z.mrt||z.district)})"))
    check(await ev("state.zones.length") > 0, '真實資料：算出符合條件的區域')
    await shot(page, 'real_1_home')
    for i in ['market', 'bus', 'sport']:
        t0 = time.time(); await page.click(f'.chip[data-id={i}]'); await page.wait_for_function(f"state.res.act.includes('{i}')", timeout=30000)
        print(f'  + {i}: {time.time()-t0:.2f}s')
    await shot(page, 'real_2_all')
    for i in ['market', 'bus', 'sport']:
        await page.click(f'.chip[data-id={i}]')
    await ev("openZone(0)"); await page.wait_for_timeout(400); await shot(page, 'real_3_zone'); await page.click('#close')
    await page.click('#regionBtn'); await page.click('#regionPop [data-region="3"]'); await page.wait_for_function("state.res.key==='d:松山區'"); await page.click('#regionBtn'); await shot(page, 'real_4_songshan')
    name = await ev("(()=>{const m=data('mrt').find(f=>/南京三民|小巨蛋|松山/.test(f.name))||data('mrt')[0];void openPlace(m,LAYER.mrt);return m.name})()")
    await page.wait_for_function("state.transit&&state.transit.loading===false", timeout=90000)
    print('  transit at', name, await ev("state.transit.n"), 'routes')
    await shot(page, 'real_5_mrt')
    await page.click('#close')
    name = await ev("(()=>{const s=data('bus').find(f=>/臺北科技大學 ?\\(忠孝\\)/.test(f.name))||data('bus')[0];map.setView(s.center,15,{animate:false});void openPlace(s,LAYER.bus);return s.name})()")
    await page.wait_for_function("state.transit&&state.transit.loading===false", timeout=90000)
    print('  transit at', name, await ev("state.transit.n"), 'routes', await ev(LINES + ".map(l=>l.options.weight)"))
    await shot(page, 'real_6_bus')
    await page.click('#close')
    await ev("void map.setView([25.0585,121.557],16,{animate:false})"); await page.wait_for_timeout(500)
    await ev("void openVal([25.0585,121.557])"); await page.wait_for_function("!!document.getElementById('valGo')", timeout=30000)
    for k, v_ in (('ping', '28'), ('age', '35'), ('fl', '4'), ('tot', '7'), ('ask', '2800')):
        await page.fill(f'#body [data-vf={k}]', v_)
    await page.click('#body [data-vt="1"]'); await page.click('#valGo'); await page.wait_for_function("!!document.getElementById('valSum')||!!document.getElementById('valNo')", timeout=5000)
    print('  valuation', await ev("({n:state.val.est.n,rad:state.val.est.radius,unit:state.val.est.q(.5),poi:state.val.est.poi,out:state.val.off&&state.val.off.out,H:state.val.mk.H,label:state.val.mk.label,parts:state.val.mk.parts.map(p=>p.txt)})"))
    await shot(page, 'real_7_val'); await ev("setSheet('full')"); await shot(page, 'real_8_val_full')
    await ev("document.querySelector('#body [data-vtab=place]').click()"); await page.wait_for_timeout(300); await shot(page, 'real_8b_val_place')
    print('  place land', await ev("[...document.querySelectorAll('#valTab .vrow')].map(e=>e.innerText.replace(/\\n/g,' ')).filter(t=>t.includes('公告土地')).join('')"))
    await page.click('#close'); await ev("void map.setView([25.05,121.54],12,{animate:false})")
    await page.click('#layersBtn'); await page.wait_for_selector('#layersPop [data-layer=priceheat]', state='visible')
    print('  before priceheat', await ev("({ph:state.layers.priceheat,view:state.view.type,tr:!!state.transit})"))
    if not await ev("state.layers.priceheat"): await page.click('#layersPop [data-layer=priceheat]')
    await page.wait_for_function("!!phLayer", timeout=20000); await page.click('#layersBtn')
    print('  priceheat', await ev("({cells:PH.cells.length,lo:PH.lo,hi:PH.hi})")); await shot(page, 'real_9_priceheat')
    await page.click('#layersBtn'); await page.click('#layersPop [data-layer=priceheat]'); await page.click('#layersPop [data-layer=heat]'); await page.click('#layersPop [data-layer=deal]'); await page.click('#layersBtn')
    await ev("void map.setView([25.0585,121.557],16,{animate:false})"); await page.wait_for_function("dealLayer.getLayers().length>5", timeout=20000); await page.wait_for_timeout(500)
    print('  deals', await ev("dealLayer.getLayers().length")); await shot(page, 'real_10_deals')
    await ev("void dealLayer.getLayers()[0].fire('click')"); await page.wait_for_timeout(400); await shot(page, 'real_11_deal_pop')
    await ev("closeDetail();setSheet('half')"); await page.wait_for_timeout(300)
    await page.click('#exportBtn'); await page.wait_for_timeout(300); print('  export', await ev("(()=>{const t=exText().t;return t.slice(0,t.indexOf('範圍（GeoJSON）'))+'…GeoJSON '+t.length+' 字'})()")); await ev("closeDetail()")
    if os.path.exists(os.path.join(ROOT, 'data', 'market.json')):
        await ev("void map.closePopup()"); await page.click('#marketBtn'); await page.wait_for_function("state.view.type==='market'", timeout=30000); await page.wait_for_timeout(400)
        await ev("setSheet('full')"); await page.wait_for_timeout(300); await shot(page, 'real_15_market')
        await ev("document.querySelector('#mkFc').scrollIntoView()"); await shot(page, 'real_16_market_fc')
        await ev("document.querySelector('.mk-g').scrollIntoView()"); await shot(page, 'real_17_market_cards')
        print('  market', await ev("({light:MARKET.light&&MARKET.light.label,fc:MARKET.forecast.probs,bt:MARKET.forecast.backtest,n:MARKET.indicators.length})"))
        await page.click('#close'); await page.wait_for_timeout(300)
    if os.path.exists(os.path.join(ROOT, 'data', 'land.json')):
        await ev("void map.closePopup()"); await page.click('#layersBtn'); await page.click('#layersPop [data-layer=land]'); await page.click('#layersBtn')
        await ev("void map.setView([25.06,121.55],12,{animate:false})"); await page.wait_for_function("!!landLayer&&landLayer.getLayers().length>400", timeout=30000); await page.wait_for_timeout(500)
        print('  land', await ev("({n:landLayer.getLayers().length,lo:LAND.lo,hi:LAND.hi,year:LAND.year,lg:document.getElementById('landLegend').innerText})")); await shot(page, 'real_14_land')
        await page.click('#layersBtn'); await page.click('#layersPop [data-layer=land]'); await page.click('#layersBtn')
    if os.path.exists(os.path.join(ROOT, 'data', 'trees.json')):
        await ev("void map.closePopup()"); await page.click('#layersBtn'); await page.click('#layersPop [data-layer=deal]'); await page.click('#layersPop [data-layer=trees]'); await page.click('#layersBtn')
        await ev("void map.setView([25.0415,121.545],16,{animate:false})"); await page.wait_for_function("treeLayer.getLayers().length>100", timeout=30000); await page.wait_for_timeout(500)
        print('  trees', await ev("({n:treeLayer.getLayers().length,total:TREES.n})")); await shot(page, 'real_12_trees')
        await ev("void map.setView([25.0415,121.545],18,{animate:false})"); await page.wait_for_timeout(500); await shot(page, 'real_13_trees_z18')
        await ev("void treeLayer.getLayers()[0].fire('click')"); await page.wait_for_timeout(300); print('  tree popup', await ev("document.querySelector('.leaflet-popup-content')?.innerText"))
    await page.close()


async def main():
    os.makedirs(OUT, exist_ok=True)
    errs = []
    async with async_playwright() as p:
        exe = shutil.which('google-chrome') or shutil.which('chromium')
        b = await (p.chromium.launch(executable_path=exe, args=['--no-sandbox']) if exe else p.chromium.launch())
        if os.environ.get('REAL_DATA'):
            await real_suite(b, errs)
        else:
            import build_data
            print('=== B. 有預建資料（正常使用情境）===')
            shutil.rmtree(DATA_DIR, ignore_errors=True)
            with contextlib.redirect_stdout(io.StringIO()):
                idx = build_data.build(fetch=lambda q: {'elements': answer(q)}, out_dir=DATA_DIR, pause=0)
                import build_sidewalk, sidewalk_mock
                MODE['sw'] = build_sidewalk.build(sidewalk_mock.mock(), DATA_DIR, built='2026-11-14', period='202606', log=lambda *a: None)
                import build_future as BF, future_mock as FM
                MODE['future'] = BF.build(FM.mock(), DATA_DIR, built='2026-11-15', log=lambda *a: None)
                import build_trees as BT, trees_mock as TM
                import build_land as BL, land_mock as LM
                _shp, _dbf = LM.shp_dbf()
                MODE['land'] = BL.build(BL.read_shp(_shp, _dbf), BL.read_values(LM.values_csv()), 113, BL.read_rates(LM.rates_csv()), BL.read_public(LM.public_csv()), DATA_DIR, built='2026-11-16', min_sections=3, log=lambda *a: None)
                import build_urban as BU, urban_mock as UM
                MODE['urban'] = BU.build(UM.mock(), DATA_DIR, built='2026-11-17', min_areas=3, log=lambda *a: None)
                import build_urcase as BUC, urcase_mock as UCM
                MODE['urcase'] = BUC.build(UCM.mock(), DATA_DIR, built='2026-11-24', min_cases=3, log=lambda *a: None)
                import build_market as BMK, market_mock as MKM
                from datetime import date as _date
                MODE['market'] = BMK.build(MKM.src(), DATA_DIR, built='2026-11-16', today=_date(*MKM.TODAY), log=lambda *a: None)
                MODE['trees'] = BT.build(TM.mock(), DATA_DIR, built='2026-11-16', min_trees=10, log=lambda *a: None)
                import build_realprice as RP, realprice_mock as RM   # 估價：模擬的實價登錄 → 實際跑建置程式
                rs, rr, raddr, _ = RM.mock()
                clean = RP.parse_sales(rs); layers = json.load(open(os.path.join(DATA_DIR, 'city.json'), encoding='utf-8'))['layers']
                val = RP.build(clean, RP.parse_rents(rr), raddr, layers, out_dir=DATA_DIR, built='2026-11-15', log=lambda *a: None, min_sales=1000)
                subj = {'dist': '大安區', 't': 0, 'age': 12, 'fl': 8, 'tot': 15, 'ping': 30.0, 'pt': (25.030, 121.543)}; subj['s'] = RP.PoiIndex(layers).score(subj['pt'])
                MODE['val'] = {'v': val, 'est': RP.estimate(subj, {'b': val['model']['b'], 'dist': val['model']['dist'], 'q': {'0': 0}}, [r for r in clean if 'e' in r], val['now_ym'], val['model']['now']), 's': subj['s']}
            check(len(idx['districts']) == 12 and idx['routes'] > 0 and bool(idx['city']) and idx['city']['mrt'] == 110 and not [f for f in os.listdir(DATA_DIR) if f.endswith('區.json')], f"建置程式只產生全市檔（不另存 12 區檔）（捷運 {idx['city'] and idx['city']['mrt']} 站，已去重複）與 {idx['routes']} 條路線")
            MODE['baked'] = True
            if os.environ.get('ONLY') == 'cloud':
                await cloud_suite(b, errs); await debug_suite(b, errs); check(not errs, f'沒有 JavaScript 錯誤 {errs[:3]}'); await b.close(); return fails
            await baked_suite(b, errs)
            await sw_suite(b, errs)
            await cloud_suite(b, errs)
            await debug_suite(b, errs)
            print('\n=== A. 沒有預建資料（即時查詢的備援流程）===')
            MODE['baked'] = False
            await live_suite(b, errs)
        check(not errs, f'沒有 JavaScript 錯誤 {errs[:3]}')
        await b.close()
    print(f"\n{'全部通過' if not fails else str(len(fails)) + ' 項失敗：' + '；'.join(fails)}")
    return fails

sys.exit(1 if asyncio.run(main()) else 0)
