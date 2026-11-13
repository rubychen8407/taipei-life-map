"""端對端測試：iPhone 尺寸無頭 Chrome 開 index.html；資料、Overpass 與底圖都用模擬資料。
執行：tests/run.sh   （全部通過才可 commit / push）
  B 組：有預建資料（scripts/build_data.py 用模擬資料實際產生 data/*.json）— 正常使用情境
  A 組：沒有預建資料 — 即時查 Overpass 的備援流程
設 REAL_DATA=1 時，改用 repo 內真實的 data/ 跑一遍並截圖。
"""
import json, asyncio, urllib.parse, base64, sys, os, re, shutil, math, time, io, contextlib
from playwright.async_api import async_playwright
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
_M = json.load(open(os.path.join(HERE, 'mock.json')))
MOCK, ROUTES = _M['layers'], _M['routes']
LF = os.path.join(HERE, 'node_modules', 'leaflet', 'dist') + '/'
PD = os.path.join(HERE, 'node_modules', 'leaflet-polylinedecorator', 'dist', 'leaflet.polylineDecorator.js')
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=')
URL = 'https://rubychen8407.github.io/taipei-life-map/'
SNIP = {'park': 'leisure"="park"', 'green': 'landuse', 'school': 'amenity', 'sport': 'sports_centre', 'bus': 'bus_stop', 'mrt': 'subway'}
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
            if el['type'] == 'way' and lid in ('school', 'sport') and 'out center' in q:
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
    if u.startswith(URL):
        return await r.fulfill(path=os.path.join(ROOT, 'index.html'), content_type='text/html; charset=utf-8')
    if 'leaflet.css' in u:
        return await r.fulfill(path=LF + 'leaflet.css', content_type='text/css')
    if 'leaflet-polylinedecorator' in u:
        return await r.fulfill(path=PD, content_type='application/javascript')
    if 'leaflet.js' in u:
        return await r.fulfill(path=LF + 'leaflet.js', content_type='application/javascript')
    if 'tile.openstreetmap.org' in u or 'cartocdn.com' in u:
        return await r.fulfill(body=PNG, content_type='image/png')
    if 'interpreter' in u:
        q = urllib.parse.parse_qs(r.request.post_data or '').get('data', [''])[0]
        calls.append({'host': u.split('/')[2], 'ct': r.request.headers.get('content-type', ''), 'layers': [] if 'rel(' in q else layers_in(q), 'q': q})
        if 'overpass-api.de' in u and 'school' in layers_in(q):
            return await r.fulfill(status=504, body='timeout')
        if any(l in MODE['fail'] for l in layers_in(q)):
            return await r.fulfill(status=429, body='busy')
        await asyncio.sleep(0.3)
        return await r.fulfill(body=json.dumps({'elements': answer(q)}), content_type='application/json', headers={'Access-Control-Allow-Origin': '*'})
    return await r.abort()


async def new_page(b, errs, **kw):
    ctx = await b.new_context(**kw)
    page = await ctx.new_page()
    page.on('pageerror', lambda e: errs.append(str(e)))
    await page.route('**/*', route)
    return page


async def shot(page, name):
    await page.wait_for_timeout(450)
    await page.screenshot(path=os.path.join(OUT, name + '.png'))


READY = "state.res&&document.getElementById('head').innerText.includes('分類步行距離分析')"
LINES = "transitLayer.getLayers().filter(l=>l instanceof L.Polyline&&!(l instanceof L.Polygon))"
HANDLE = "(()=>{const r=document.querySelector('.handle').getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]})()"


async def baked_suite(b, errs):
    page = await new_page(b, errs, **PHONE, geolocation={'latitude': 25.0335, 'longitude': 121.5436}, permissions=['geolocation'])
    ev = page.evaluate
    n0 = len(calls)
    t0 = time.time()
    await page.goto(URL)
    await page.wait_for_function(READY, timeout=20000)
    labels = await ev("""(()=>{const bus=data('bus')[0],mrt=data('mrt')[0],park=data('park').find(f=>f.name),old=ROUTE_DATA;ROUTE_DATA={routes:[{r:'bus',ref:'307',name:'307',stops:[bus.center]},{r:'subway',ref:'BL',name:'板南線',stops:[mrt.center]}]};const result={bus:facilityLabel(bus,LAYER.bus),mrt:facilityLabel(mrt,LAYER.mrt),park:facilityLabel(park,LAYER.park)};ROUTE_DATA=old;const el=document.createElement('span');el.className='facility-name';document.body.appendChild(el);result.labelPointerEvents=getComputedStyle(el).pointerEvents;el.remove();const marker=L.marker(park.center,{icon:facilityIcon(LAYER.park,park,17,true)}).on('click',()=>window.__labelClicked=true).addTo(map);marker._icon.querySelector('.facility-name').dispatchEvent(new MouseEvent('click',{bubbles:true}));result.labelClick=!!window.__labelClicked;marker.remove();delete window.__labelClicked;FACILITY_LABEL_CACHE.delete(bus);FACILITY_LABEL_CACHE.delete(mrt);return result;})()""")
    check(labels['bus']=='307' and labels['mrt']=='板南線' and bool(labels['park']), '地圖標籤：一般設施顯示地點名稱、公車顯示路線號、捷運顯示路線名稱')
    check(labels['labelPointerEvents']=='auto', '地圖標籤文字可接收點擊，不會穿透')
    check(labels['labelClick'], '點擊標籤文字會觸發對應 POI 標記事件')
    check(await ev("toFeature({type:'way',id:1,center:{lat:25.05,lon:121.55},tags:{leisure:'pitch',sport:'basketball'}}).name==='籃球場'"), '沒有正式名稱的運動設施依 OSM sport 標籤顯示設施類型名稱')
    dt = time.time() - t0
    # 1. 開啟即顯示
    check(len(calls) == n0 and data_hits == ['city.json'], f'開啟時只讀一個預建檔（city.json），完全不查 Overpass {data_hits}')
    check(dt < 4, f'開啟到顯示全市結果 {dt:.1f} 秒')
    check(await ev("state.scope===''&&state.res.total===CELLS.length"), '預設直接分析全臺北市')
    check(await ev("!!heat&&map.hasLayer(heat)"), '地圖上有符合範圍的色塊圖層')
    check(await ev("state.zones.length") > 0 and await ev("pinLayer.getLayers().length") > 0, '有推薦生活圈清單與地圖編號標記')
    check(await ev("document.querySelectorAll('#chips .chip').length") == 6 and await ev("document.querySelectorAll('#chips .chip[aria-pressed=true]').length") == 3, '上方類別按鈕列 6 個、預設選 3 個')
    check(await ev("document.querySelectorAll('.dlabel').length") == 12, '全市檢視顯示 12 區名稱')
    check(await ev("facLayer.getLayers().length") < 200, '縮小時不顯示一堆設施點（只留捷運站）')
    r = await ev("(()=>{const s=document.getElementById('sheet').getBoundingClientRect(),t=document.getElementById('top').getBoundingClientRect();return {sheetTop:s.top,topBottom:t.bottom}})()")
    check(r['topBottom'] < 330 and 380 < r['sheetTop'] < 520, f"版面：搜尋、類別與距離設定位於地圖上方，下方為結果卡片（{r['topBottom']:.0f}px／{r['sheetTop']:.0f}px）")
    await shot(page, '1_home')

    # 2. 類別按鈕、各類別獨立距離設定
    t0 = time.time()
    await page.click('.chip[data-id=bus]')
    await page.wait_for_function("state.res.act.includes('bus')", timeout=5000)
    check(len(calls) == n0 and len(data_hits) == 1 and time.time() - t0 < 1.5, f'點「公車站」立即重算（{time.time()-t0:.2f} 秒），不下載任何東西')
    g10 = await ev("state.res.good")
    await page.select_option('select[data-distance="park"]', '5')
    check(await ev("state.res.thresholds[state.res.act.indexOf('park')]===5 && state.minsByLayer.park===5 && state.minsByLayer.school===10"), '公園距離可獨立設定為 5 分鐘，不影響學校的 10 分鐘設定')
    await page.select_option('select[data-distance="park"]', '10')

    # 3. 卡片拖拉
    box = await ev(HANDLE)
    await page.mouse.move(*box); await page.mouse.down(); await page.mouse.move(box[0], 120, steps=6); await page.mouse.up(); await page.wait_for_timeout(350)
    check(await ev("snap") == 'full', '往上拖把卡片拉到全高')
    box = await ev(HANDLE)
    await page.mouse.move(*box); await page.mouse.down(); await page.mouse.move(box[0], 800, steps=6); await page.mouse.up(); await page.wait_for_timeout(350)
    check(await ev("snap") == 'peek' and await ev("state.sheetVis") < 160, '往下拖把卡片收到最小，地圖幾乎全螢幕')
    check(await ev("document.querySelectorAll('#categorySettings select[data-distance]').length===state.res.act.length && !document.querySelector('#body select[data-distance]')"), '每個已選類別的距離選擇器都在搜尋區域下方，不混入結果面板')
    await shot(page, '2_peek')
    box = await ev(HANDLE)
    await page.mouse.click(*box); await page.wait_for_timeout(350)
    check(await ev("snap") == 'half', '點一下把手展開回半高')

    # 4. 點推薦生活圈
    await page.click('[data-z="0"]'); await page.wait_for_timeout(600)
    r = await ev("({type:state.view.type,title:document.querySelector('#head h1').innerText,rows:document.querySelectorAll('#body .row').length,act:state.res.act.length,sel:selLayer.getLayers().length,ok:!!document.querySelector('.verdict.ok')})")
    check(r['type'] == 'cell' and '1.' in r['title'] and r['rows'] == r['act'] and r['ok'], f"點第 1 名：卡片顯示該位置到每一類的步行時間（{r['rows']} 類）")
    check(r['sel'] > 1, '地圖上放大頭針，並用虛線連到最近的各類設施')
    pin = await ev("(()=>{const r=document.querySelector('.pin').getBoundingClientRect(),s=document.getElementById('sheet').getBoundingClientRect(),t=document.getElementById('top').getBoundingClientRect();return r.top>t.bottom&&r.bottom<s.top&&r.left>0&&r.right<innerWidth})()")
    check(pin, '大頭針在可見的地圖範圍內（不被上方列或卡片擋住）')
    await shot(page, '3_zone')
    await page.click('#back'); await page.wait_for_timeout(300)
    check(await ev("state.view.type") == 'list' and await ev("selLayer.getLayers().length") == 0, '按返回回到清單')

    # 5. 點地圖任一位置
    k = await ev("(()=>{const b=new Set(state.zones.map(z=>z.best)),m=data('mrt');return scopeCells().find(k=>state.res.okc[k]===state.res.act.length&&!b.has(k)&&CELLS[k].d===4&&m.every(f=>md(f.center,[CELLS[k].la,CELLS[k].lo])>250))})()")
    await ev(f"void map.setView([CELLS[{k}].la,CELLS[{k}].lo],13,{{animate:false}})"); await page.wait_for_timeout(300)
    pt = await ev(f"(()=>{{const p=map.latLngToContainerPoint([CELLS[{k}].la,CELLS[{k}].lo]);return [p.x,p.y]}})()")
    await page.mouse.click(pt[0], pt[1]); await page.wait_for_timeout(500)
    check(await ev("state.view.type") == 'cell' and '這個位置' in await ev("document.querySelector('#head h1').innerText"), '點地圖任一位置，下方卡片顯示該點的生活機能')
    await page.click('#close'); await page.wait_for_timeout(300)

    # 6. 放大才顯示設施點；點公車站 → 路線
    hub = None
    for rel in ROUTES[:36]:
        if rel['tags'].get('interval') == '6':
            st = [mm for mm in rel['members'] if mm['type'] == 'node']
            hub = (round(st[0]['lat'], 5), round(st[0]['lon'], 5)); break
    exp = [rel for rel in ROUTES if (lambda st: any(mdist(p, hub) <= 40 for p in st[:-1]))([(mm['lat'], mm['lon']) for mm in rel['members'] if mm['type'] == 'node'])]
    await ev(f"void map.setView([{hub[0]},{hub[1]}],18,{{animate:false}})"); await page.wait_for_timeout(400)
    check(await ev("facLayer.getLayers().length") > 3, '放大後才顯示附近的設施點')
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
    check(await ev("state.view.type") == 'place' and '條路線' in await ev("document.querySelector('#head .sub').innerText") and await ev("!!document.querySelector('.freq')"), '卡片顯示站名、路線數與班距圖例')
    check(await ev("document.querySelector('.detail-category')?.innerText==='公車站' && document.querySelector('#head h1').innerText.includes(state.view.f.name||'公車站')"), '下方視窗標題顯示所選 POI 名稱，類別標籤與地圖上的公車站圖示一致')
    check(not await ev("!!heat") and await ev("pinLayer.getLayers().length") == 0, '看路線時暫時隱藏色塊與其他標記')
    viewport_after = await ev("(()=>{const c=map.getCenter();return {lat:c.lat,lng:c.lng,zoom:map.getZoom()}})()")
    stable = abs(viewport_after['lat']-viewport_before['lat'])<1e-7 and abs(viewport_after['lng']-viewport_before['lng'])<1e-7 and viewport_after['zoom']==viewport_before['zoom']
    check(stable, '點選公車站顯示路線時保持 viewport 中心與縮放不變（不自動 fit 路線終點）')
    await shot(page, '4_transit')
    await page.click('#close'); await page.wait_for_timeout(300)
    check(await ev("!state.transit&&!!heat&&transitLayer.getLayers().length===0&&state.view.type==='list'"), '關閉後恢復色塊與清單')
    rh = data_hits.count('routes.json')
    await ev("void openPlace(data('mrt')[0],LAYER.mrt)")
    await page.wait_for_function("state.transit&&state.transit.loading===false", timeout=20000)
    check(data_hits.count('routes.json') == rh, '捷運站也可點；路線檔只下載一次')
    await page.click('#close'); await page.wait_for_timeout(200)

    # 7. 行政區
    await page.select_option('#scope', '4')
    await page.wait_for_function("state.res&&state.res.key==='d:大安區'", timeout=5000); await page.wait_for_timeout(400)
    check(len(calls) == n0 and len([d for d in data_hits if d.endswith('區.json')]) == 0, '切到大安區即時顯示，不下載')
    check(await ev("scopeCells().every(k=>CELLS[k].d===4)") and '大安區' in await ev("document.getElementById('head').innerText") and await ev("document.getElementById('scopeName').textContent") == '大安區', '只分析大安區，標題與上方列同步')
    used = await ev("(()=>{const i=state.res.act.indexOf('mrt'),D=state.res.D[i];return scopeCells().filter(k=>{const f=data('mrt')[D.nf[k]];return f&&!inPoly(DISTRICTS[4],f.center[0],f.center[1])}).length})()")
    check(used > 0, f'靠區界的位置會用區界外較近的設施計算（{used} 格用到別區的捷運站）')
    await shot(page, '5_district')
    await page.click('#toCity'); await page.wait_for_function("state.res.key==='city'", timeout=5000)
    await page.click('.drow'); await page.wait_for_function("state.res.key.startsWith('d:')", timeout=5000)
    check(True, '「各區比較」點一區即可切換')

    # 8. 框選
    await page.select_option('#scope', 'draw'); await page.wait_for_timeout(300)
    check(await ev("state.drawing&&getComputedStyle(document.getElementById('drawbar')).display==='block'"), '選「自己框選範圍」進入框選模式')
    await page.mouse.click(120, 260); await page.mouse.click(280, 520); await page.click('#fin')
    await page.wait_for_function("state.res&&state.res.key.startsWith('custom')", timeout=5000)
    check(await ev("scopeCells().length>0&&scopeCells().every(k=>inPoly(state.custom,CELLS[k].la,CELLS[k].lo))") and len(calls) == n0, '框選 2 點成矩形，只分析框內')
    await page.select_option('#scope', '')
    await page.wait_for_function("state.res.key==='city'", timeout=5000)

    # 9. 我的位置
    await page.click('#locate'); await page.wait_for_function("state.view.type==='cell'", timeout=8000)
    check('我的位置' in await ev("document.querySelector('#head h1').innerText") and await ev("meLayer.getLayers().length") == 1, '「我的位置」按鈕：顯示藍點與所在位置的生活機能')
    await page.click('#close')

    # 10. 全部取消
    for i in await ev("[...state.active]"):
        await page.click(f'.chip[data-id={i}]')
    check('選擇你在意的設施' in await ev("document.getElementById('head').innerText") and not await ev("!!heat"), '全部取消時提示選擇類別')
    await page.close()

    # 11. 桌機版面
    page = await new_page(b, errs, viewport={'width': 1280, 'height': 800})
    await page.goto(URL); await page.wait_for_function(READY, timeout=20000)
    r = await page.evaluate("(()=>{const s=document.getElementById('sheet').getBoundingClientRect();return {l:s.left,w:s.width,b:s.bottom}})()")
    check(r['l'] < 20 and 380 <= r['w'] <= 400 and r['b'] <= 800, '桌機版：卡片變成左側面板')
    await shot(page, '6_desktop')
    await page.close()

    # 12. 沒有 city.json 但有各區檔 → 仍用預建資料
    MODE['missing'] = {'city.json'}; d0 = len(data_hits)
    page = await new_page(b, errs, **PHONE)
    await page.goto(URL); await page.wait_for_function(READY, timeout=20000)
    check(len(calls) == n0 and len([d for d in data_hits[d0:] if d.endswith('區.json')]) == 12, '沒有 city.json 時改讀 12 區檔，仍不查 Overpass')
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
    check(1300 < pad_m < 1500, f'查詢範圍＝大安區外擴約 1.4 公里（{pad_m:.0f} 公尺）')
    check(any(c['host'] != 'overpass-api.de' for c in calls[n0:]) and all('x-www-form-urlencoded' in c['ct'] for c in calls[n0:]), '主伺服器失敗會改用備用伺服器；請求格式正確')
    check(await ev("state.zones.length") > 0 and await ev("!!heat"), '算出推薦生活圈並顯示色塊')
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
    print(f'real data: ready in {time.time()-t0:.1f}s', await ev("({built:state.built,n:Object.fromEntries(LAYERS.map(l=>[l.id,data(l.id).length])),good:state.res.good,total:state.res.total,zones:state.zones.map(z=>z.mrt||z.district)})"))
    check(await ev("state.zones.length") > 0, '真實資料：算出推薦生活圈')
    await shot(page, 'real_1_home')
    for i in ['bus', 'sport', 'green']:
        t0 = time.time(); await page.click(f'.chip[data-id={i}]'); await page.wait_for_function(f"state.res.act.includes('{i}')", timeout=30000)
        print(f'  + {i}: {time.time()-t0:.2f}s')
    await shot(page, 'real_2_all')
    for i in ['bus', 'sport', 'green']:
        await page.click(f'.chip[data-id={i}]')
    await page.click('[data-z="0"]'); await shot(page, 'real_3_zone'); await page.click('#close')
    await page.select_option('#scope', '3'); await page.wait_for_function("state.res.key==='d:松山區'"); await shot(page, 'real_4_songshan')
    name = await ev("(()=>{const m=data('mrt').find(f=>/南京三民|小巨蛋|松山/.test(f.name))||data('mrt')[0];void openPlace(m,LAYER.mrt);return m.name})()")
    await page.wait_for_function("state.transit&&state.transit.loading===false", timeout=90000)
    print('  transit at', name, await ev("state.transit.n"), 'routes')
    await shot(page, 'real_5_mrt')
    await page.click('#close')
    name = await ev("(()=>{const c=DISTRICTS[3].ctr;const s=data('bus').slice().sort((a,b)=>md(a.center,c)-md(b.center,c))[0];void openPlace(s,LAYER.bus);return s.name})()")
    await page.wait_for_function("state.transit&&state.transit.loading===false", timeout=90000)
    print('  transit at', name, await ev("state.transit.n"), 'routes', await ev(LINES + ".map(l=>l.options.weight)"))
    await shot(page, 'real_6_bus')
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
            check(len(idx['districts']) == 12 and idx['routes'] > 0 and bool(idx['city']) and idx['city']['mrt'] == 110, f"建置程式產生 12 區檔、全市檔（捷運 {idx['city'] and idx['city']['mrt']} 站，已去重複）與 {idx['routes']} 條路線")
            MODE['baked'] = True
            await baked_suite(b, errs)
            print('\n=== A. 沒有預建資料（即時查詢的備援流程）===')
            MODE['baked'] = False
            await live_suite(b, errs)
        check(not errs, f'沒有 JavaScript 錯誤 {errs[:3]}')
        await b.close()
    print(f"\n{'全部通過' if not fails else str(len(fails)) + ' 項失敗：' + '；'.join(fails)}")
    return fails

sys.exit(1 if asyncio.run(main()) else 0)
