"""端對端測試：iPhone 尺寸無頭 Chrome 開 index.html；Overpass 與地圖底圖用模擬資料。
執行：tests/run.sh   （全部通過才可 commit / push）
"""
import json, asyncio, urllib.parse, base64, sys, os, re, shutil
from playwright.async_api import async_playwright
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
_M = json.load(open(os.path.join(HERE, 'mock.json')))
MOCK, ROUTES = _M['layers'], _M['routes']
import math
LF = os.path.join(HERE, 'node_modules', 'leaflet', 'dist') + '/'
PD = os.path.join(HERE, 'node_modules', 'leaflet-polylinedecorator', 'dist', 'leaflet.polylineDecorator.js')
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=')
URL = 'https://rubychen8407.github.io/taipei-life-map/'
SNIP = {'park': 'leisure"="park"', 'green': 'landuse', 'school': 'amenity', 'sport': 'sports_centre', 'bus': 'bus_stop', 'mrt': 'subway'}
FAIL = {}
calls = []
data_hits = []
MODE = {'baked': False, 'missing': set()}
DATA_DIR = os.path.join(HERE, 'out', 'data')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))


def layers_in(q):
    return [k for k, s in SNIP.items() if s in q]


def center(e):
    if e['type'] == 'node':
        return e['lat'], e['lon']
    b = e['bounds']
    return (b['minlat'] + b['maxlat']) / 2, (b['minlon'] + b['maxlon']) / 2


def mdist(a, b):
    return math.hypot((a[0] - b[0]) * 110540, (a[1] - b[1]) * 101000)


def answer_routes(q):
    R, la, lo = map(float, re.search(r'around:([\d.]+),([\d.]+),([\d.]+)', q).group(1, 2, 3))
    return [rel for rel in ROUTES if any(m['type'] == 'node' and mdist((m['lat'], m['lon']), (la, lo)) <= R for m in rel['members'])]


def answer(q):
    if 'rel(bn.s)' in q:
        return answer_routes(q)
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

fails = []


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    if not cond:
        fails.append(msg)


async def live_suite(page, errs):
    ev = page.evaluate
    DAAN = '4'   # 大安區


    # 1. 開啟：還沒選區就不下載
    await page.goto(URL)
    await page.wait_for_timeout(800)
    check(len(calls) == 0, '開啟時不下載任何資料（要先選行政區）')
    check('先選一個行政區' in await ev("document.getElementById('result').innerText"), '顯示「先選行政區」提示')
    check(await ev("cityLayer.getLayers().length") == 12, '地圖顯示 12 個行政區可點選')
    await page.screenshot(path=os.path.join(HERE, 'out', 's0.png'))

    # 2. 選大安區：一次查詢、含周邊緩衝
    await page.select_option('#scope', DAAN)
    await page.wait_for_function("document.getElementById('result').innerHTML.includes('推薦區域')", timeout=30000)
    c1 = [c for c in calls if c['host'] != 'overpass-api.de' or 'school' not in c['layers']]
    check(len(c1) == 1 and set(c1[0]['layers']) == {'park', 'school', 'mrt'}, f"選區後只送一次查詢、包含已勾選三類 {[c['layers'] for c in c1]}")
    bb = list(map(float, re.search(r'\[bbox:([^\]]+)\]', calls[-1]['q']).group(1).split(',')))
    dbb = await ev(f"DISTRICTS[{DAAN}].bb")
    pad_m = min((dbb[0] - bb[0]) * 110540, (bb[2] - dbb[2]) * 110540)
    check(1300 < pad_m < 1500, f'查詢範圍＝大安區外擴約 1.4 公里（實際 {pad_m:.0f} 公尺）')
    check(all(re.search(r'\[bbox:', c['q']) for c in calls) and 'out center' in calls[-1]['q'], '只查範圍內資料，學校／捷運只取中心點以減少下載量')
    r = await ev("({n:['park','school','mrt'].map(i=>document.getElementById('n-'+i).textContent),shown:['park','school','mrt'].map(i=>map.hasLayer(state.groups[i])),zones:state.zones.length,cells:state.cells.length})")
    check(all(x.isdigit() and int(x) > 0 for x in r['n']) and all(r['shown']), f"三類載入、顯示數量並畫在地圖上 {r['n']}")
    check(r['zones'] > 0, f"自動算出推薦區域 {r['zones']} 個")
    outside = await ev(f"data('mrt').filter(f=>!inPoly(DISTRICTS[{DAAN}],f.center[0],f.center[1])).length")
    check(outside > 0, f'區界外的捷運站也有載入（{outside} 站）')
    usedOut = await ev(f"state.computed.res.filter((x,k)=>{{const fi=x.near.mrt[1];const f=data('mrt')[fi];return f&&!inPoly(DISTRICTS[{DAAN}],f.center[0],f.center[1]);}}).length")
    check(usedOut > 0, f'靠區界的格子會用區界外較近的捷運站計算（{usedOut} 格）')
    check(await ev(f"state.cells.every(c=>inPoly(DISTRICTS[{DAAN}],c.la,c.lo))"), '只分析大安區內的格子')
    zs = await ev("state.zones.map(z=>z.area)")
    check(max(zs) < 400, f'推薦區域大小合理（最大 {max(zs):.0f} 公頃）')
    await page.screenshot(path=os.path.join(HERE, 'out', 's1.png'))

    # 3. 加勾公車站：只補抓公車站
    n0 = len(calls)
    await page.click('#lab-bus input')
    await page.wait_for_function("state.computed&&state.computed.act.includes('bus')", timeout=20000)
    new = calls[n0:]
    check(len(new) == 1 and new[0]['layers'] == ['bus'], f"加勾公車站只補抓公車站 {[c['layers'] for c in new]}")
    check(await ev("map.hasLayer(state.groups.bus)"), '公車站畫在地圖上並重算')

    # 4. 伺服器全忙 → 紅字；重勾可重試
    FAIL['sport'] = True
    await page.click('#lab-sport input')
    await page.wait_for_function("document.getElementById('status').className==='err'", timeout=20000)
    check('下載失敗' in await ev("document.getElementById('status').textContent"), '伺服器全忙時顯示紅字錯誤')
    FAIL['sport'] = False
    await page.click('#lab-sport input'); await page.click('#lab-sport input')
    await page.wait_for_function("state.computed&&state.computed.act.includes('sport')", timeout=20000)
    check(await ev("document.getElementById('status').textContent") == '' and await ev("map.hasLayer(state.groups.sport)"), '取消再勾選即可重試成功')
    await page.click('#lab-sport input')
    await page.wait_for_function("!state.computed.act.includes('sport')", timeout=5000)

    # 5. 調分鐘數
    g10 = await ev("state.computed.res.filter(x=>x.ok===state.computed.act.length).length")
    await ev("const m=document.getElementById('mins');m.value=5;m.dispatchEvent(new Event('input'))"); await page.wait_for_timeout(700)
    g5 = await ev("state.computed.res.filter(x=>x.ok===state.computed.act.length).length")
    check(await ev("state.computed.mins") == 5 and g5 < g10, f'拉到 5 分鐘自動重算且符合格數變少（{g10}→{g5}）')
    await ev("const m=document.getElementById('mins');m.value=10;m.dispatchEvent(new Event('input'))"); await page.wait_for_timeout(700)

    # 6. 點推薦區域
    await page.click('[data-z="0"]'); await page.wait_for_timeout(800)
    pop = await ev("document.querySelector('.leaflet-popup-content')?.innerText||''")
    check(pop.startswith('#1') and pop.count('✅') == 4, '點推薦區域 #1 放大並列出各類步行時間')
    await page.screenshot(path=os.path.join(HERE, 'out', 's2.png'))

    # 6b. 點色塊（地圖空白處）顯示該格步行時間
    await ev("void map.closePopup()")
    k = await ev("state.computed.res.findIndex(x=>x.ok===state.computed.act.length)")
    await ev(f"(()=>{{const c=state.cells[{k}];map.fire('click',{{latlng:L.latLng(c.la,c.lo)}});}})()")
    await page.wait_for_timeout(300)
    check('步行時間' in await ev("document.querySelector('.leaflet-popup-content')?.innerText||''") and not await ev("cellLayer.getLayers()[0].options.interactive"), '點地圖上的色塊會顯示該格步行時間（色塊不擋住設施點）')
    await ev("void map.closePopup()")

    # 6c. 大眾運輸：點公車站 → 地圖上畫出可到達方向與班次
    cands = []
    for rel in ROUTES:
        if rel['tags']['route'] != 'bus' or rel['tags'].get('interval') != '6' or int(rel['id']) - 800000 >= 36:
            continue
        st = [m for m in rel['members'] if m['type'] == 'node']
        cands.append((st[0]['lat'], st[0]['lon']))
    pick = await ev(f"(()=>{{const C={json.dumps(cands)};const d=data('bus');for(const [a,b] of C){{const f=d.find(f=>Math.abs(f.center[0]-a)<1e-9&&Math.abs(f.center[1]-b)<1e-9);if(f)return [a,b,f.name];}}return null;}})()")
    check(pick is not None, '找到一個有路線經過的公車站可供點選')
    stop = (pick[0], pick[1])
    exp = []
    for rel in ROUTES:
        st = [(m['lat'], m['lon']) for m in rel['members'] if m['type'] == 'node']
        near = [i for i, p in enumerate(st) if mdist(p, stop) <= 40]
        if near and near[0] < len(st) - 1:
            exp.append(rel)
    check(await ev("transitLayer.getLayers().length") == 0 and not await ev("!!document.querySelector('.tr-legend')"), '沒點站牌前不顯示任何路線')
    await page.click('#toggle')   # 先收起面板，點地圖上看得到的站牌
    await ev(f"void map.setView([{stop[0]},{stop[1]}],19,{{animate:false}})"); await page.wait_for_timeout(400)
    pt = await ev(f"(()=>{{const p=map.latLngToContainerPoint([{stop[0]},{stop[1]}]);return [p.x,p.y];}})()")
    n0 = len(calls)
    await page.mouse.click(pt[0], pt[1])
    await page.wait_for_function("state.transit&&state.transit.loading===false", timeout=20000)
    check(any('rel(bn.s)' in c['q'] for c in calls[n0:]), '點公車站會查詢經過此站的路線')
    check(await ev("state.transit.f.name") == pick[2], f'點到的是指定的站牌（{pick[2]}）')
    lines = await ev("transitLayer.getLayers().filter(l=>l instanceof L.Polyline&&!(l instanceof L.Polygon)).map(l=>({w:l.options.weight,c:l.options.color,dash:l.options.dashArray||null,first:l.getLatLngs()[0],n:l.getLatLngs().length}))")
    check(len(lines) == len(exp), f'地圖上畫出所有經過此站的路線（{len(lines)}/{len(exp)} 條）')
    check(all(mdist((l['first']['lat'], l['first']['lng']), stop) < 60 for l in lines), '每條路線都從這一站往下游畫（只畫可到達的方向）')
    allpts = await ev("transitLayer.getLayers().filter(l=>l instanceof L.Polyline&&!(l instanceof L.Polygon)).flatMap(l=>l.getLatLngs().map(p=>[p.lat,p.lng]))")
    corners = [(g['lat'], g['lon']) for rel in exp for mm in rel['members'] if mm['type'] == 'way' for g in mm['geometry'][1:-1]]
    hit = sum(1 for c in corners if any(abs(c[0] - p[0]) < 2e-5 and abs(c[1] - p[1]) < 2e-5 for p in allpts))
    check(hit > 0 and all(l['n'] > 2 for l in lines), f'路線沿著實際行駛道路畫（經過道路轉彎點 {hit} 個），不是站與站直線相連')
    ivs = [rel['tags'].get('interval') for rel in exp]
    check(len(lines) >= 4, f'測試站有多條路線經過（{len(lines)} 條）')
    check(any(l['w'] == 8 and l['c'] == '#1b5e20' for l in lines), '6 分一班的路線畫成最粗的深綠線')
    check(any(l['w'] == 4.5 for l in lines) and any(l['dash'] for l in lines), '20 分一班畫成細黃線、無班距資料畫成虛線')
    check(await ev("transitLayer.getLayers().some(l=>l.options&&l.options.icon&&l.options.icon.options.className==='tr-end')"), '路線終點有路線編號標記')
    check(await ev("!!document.querySelector('.tr-legend')") and not await ev("map.hasLayer(cellLayer)") and not await ev("map.hasLayer(state.groups.bus)"), '顯示班距圖例，暫時隱藏分析色塊與設施點')

    await page.screenshot(path=os.path.join(HERE, 'out', 's_transit.png'))
    await page.click('.tr-legend .x'); await page.wait_for_timeout(300)
    check(await ev("!state.transit&&!document.querySelector('.tr-legend')&&map.hasLayer(cellLayer)&&map.hasLayer(state.groups.bus)"), '關閉路線後恢復分析色塊與設施點')
    await page.click('#toggle')   # 打開面板

    # 6d. 捷運站也可點
    await ev("void showTransit(data('mrt').find(f=>f.name==='捷運0站')||data('mrt')[0],'mrt')")
    await page.wait_for_function("state.transit&&state.transit.loading===false", timeout=20000)
    check(await ev("state.transit.n>=0") and any('around:300' in c['q'] for c in calls), '點捷運站以 300 公尺範圍查詢路線')
    check(await ev("document.getElementById('panel').classList.contains('min')"), '面板開著時點車站，手機上會自動收起面板讓地圖完整顯示')
    await ev("closeTransit()")
    check(not await ev("document.getElementById('panel').classList.contains('min')"), '關閉路線後面板恢復')

    # 7. 換區 → 下載；換回 → 不重抓
    n0 = len(calls)
    await page.select_option('#scope', '3')   # 松山區
    await page.wait_for_function("state.computed&&state.computed.key==='d:松山區'", timeout=20000)
    check(len(calls) - n0 >= 1, '換到松山區會下載松山區資料')
    n0 = len(calls)
    await page.select_option('#scope', DAAN)
    await page.wait_for_function("state.computed&&state.computed.key==='d:大安區'", timeout=20000)
    check(len(calls) == n0, '換回大安區不重新下載（記憶體快取）')

    # 8. 重新整理頁面 → 讀裝置快取
    await page.reload(); await page.wait_for_timeout(500)
    n0 = len(calls)
    await page.select_option('#scope', DAAN)
    await page.wait_for_function("document.getElementById('result').innerHTML.includes('推薦區域')", timeout=20000)
    check(len(calls) == n0, '重新整理後再選大安區，直接用裝置上的快取、不重新下載')

    # 9. 框選
    await page.click('#mDraw'); await page.mouse.click(120, 150); await page.mouse.click(280, 330); await page.click('#fin')
    await page.wait_for_function("state.computed&&state.computed.key.startsWith('custom')", timeout=20000)
    r = await ev("({sum:document.querySelector('.sum').textContent,cells:state.cells.length,inside:state.cells.every(c=>inPoly(state.custom,c.la,c.lo))})")
    check(r['sum'].startswith('自選範圍') and r['cells'] > 0 and r['inside'], f"框選範圍只分析框內（{r['cells']} 格）")
    await page.screenshot(path=os.path.join(HERE, 'out', 's3.png'))

    # 10. 全部取消 / 回到選區
    for i in ['park', 'school', 'mrt']:
        await page.click(f'#lab-{i} input')
    await page.wait_for_timeout(300)
    check('請至少勾選' in await ev("document.getElementById('result').innerText"), '全部取消勾選時顯示提示')
    await page.click('#lab-park input'); await page.wait_for_function("state.computed&&state.computed.act.includes('park')", timeout=20000)
    await page.click('#backCity'); await page.wait_for_timeout(300)
    check(await ev("state.scope") == '' and await ev("cityLayer.getLayers().length") == 12, '「換行政區」回到全市選區畫面')

    check(all('x-www-form-urlencoded' in c['ct'] for c in calls), 'Overpass 請求格式正確')
    check(any(c['host'] != 'overpass-api.de' and 'school' in c['layers'] for c in calls), '主伺服器失敗會改用備用伺服器')


async def baked_suite(page, errs):
    ev = page.evaluate
    n0, d0 = len(calls), len(data_hits)
    bs = len(data_hits)
    await page.goto(URL); await page.wait_for_timeout(500)
    await page.select_option('#scope', '4')
    await page.wait_for_function("document.getElementById('result').innerHTML.includes('推薦區域')", timeout=20000)
    check(len(calls) == n0, '選大安區直接讀預建檔案，完全不查 Overpass')
    check(data_hits[d0:] == ['大安區.json'], f'只讀取一個檔案 {data_hits[d0:]}')
    check(await ev("['park','school','mrt'].every(i=>+document.getElementById('n-'+i).textContent>0)"), '三類設施數量顯示正常')
    check('更新' in await ev("document.getElementById('result').innerText"), '頁尾顯示資料更新日期')
    d0 = len(data_hits)
    for i in ['bus', 'sport', 'green']:
        await page.click(f'#lab-{i} input')
    await page.wait_for_function("state.computed.act.length===6", timeout=10000)
    check(len(calls) == n0 and len(data_hits) == d0, '加勾其他類別立即顯示，不再下載任何東西')
    for i in ['sport', 'green']:
        await page.click(f'#lab-{i} input')
    # 路線
    stop = await ev("(()=>{const d=data('bus');return d.map(f=>f.center)})()")
    hub = None
    for rel in ROUTES[:36]:
        st = [mm for mm in rel['members'] if mm['type'] == 'node']
        c = (round(st[0]['lat'], 5), round(st[0]['lon'], 5))
        if any(abs(c[0] - p[0]) < 1e-6 and abs(c[1] - p[1]) < 1e-6 for p in stop):
            hub = c; break
    check(hub is not None, '找到測試用站牌')
    f_name = await ev(f"data('bus').find(f=>Math.abs(f.center[0]-{hub[0]})<1e-6&&Math.abs(f.center[1]-{hub[1]})<1e-6).name")
    check(await ev("transitLayer.getLayers().length") == 0, '沒點站牌前不顯示路線')
    await ev(f"void showTransit(data('bus').find(f=>f.name==='{f_name}'),'bus')")
    await page.wait_for_function("state.transit&&state.transit.loading===false", timeout=20000)
    check(len(calls) == n0 and data_hits[-1] == 'routes.json', '點站牌時讀預建路線檔，不查 Overpass')
    n = await ev("transitLayer.getLayers().filter(l=>l instanceof L.Polyline&&!(l instanceof L.Polygon)).length")
    pts = await ev("transitLayer.getLayers().filter(l=>l instanceof L.Polyline&&!(l instanceof L.Polygon)).map(l=>l.getLatLngs().length)")
    check(n >= 4 and min(pts) > 2, f'預建路線同樣沿道路畫出 {n} 條')
    await page.wait_for_timeout(900)   # 等縮放動畫結束
    vis = await ev("(()=>{const lg=document.querySelector('.tr-legend').getBoundingClientRect(),pn=document.getElementById('panel').getBoundingClientRect();return [...document.querySelectorAll('.tr-end')].map(el=>{const r=el.getBoundingClientRect();const over=(a,b)=>!(a.right<=b.left||a.left>=b.right||a.bottom<=b.top||a.top>=b.bottom);return r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight&&!over(r,lg)&&!over(r,pn);})})()")
    check(all(vis), f'所有路線終點標記完整在畫面內、不被圖例或面板遮住（{sum(vis)}/{len(vis)}）')
    await page.screenshot(path=os.path.join(HERE, 'out', 's_transit_baked.png'))
    await ev("closeTransit()")
    rh = data_hits.count('routes.json')
    await ev(f"void showTransit(data('bus').find(f=>f.name==='{f_name}'),'bus')")
    await page.wait_for_function("state.transit&&state.transit.loading===false", timeout=20000)
    check(data_hits.count('routes.json') == rh, '路線檔只下載一次')
    await ev("closeTransit()")
    # 框選跨區 → 讀多個區檔
    d0 = len(data_hits)
    await page.click('#mDraw'); await page.mouse.click(60, 120); await page.mouse.click(330, 380); await page.click('#fin')
    await page.wait_for_function("state.computed&&state.computed.key.startsWith('custom')", timeout=20000)
    got = data_hits[d0:]
    check(len(calls) == n0 and len(got) >= 1, f'框選範圍讀相關的區檔，不查 Overpass（{got}）')
    # 某區檔不存在 → 自動改用 Overpass
    MODE['missing'] = {'文山區.json'}
    assert '文山區.json' not in data_hits[bs:]
    await page.select_option('#scope', '11')
    await page.wait_for_function("state.computed&&state.computed.key==='d:文山區'", timeout=20000)
    check(len(calls) > n0, '某區沒有預建檔時，自動改用即時查詢')
    MODE['missing'] = set()


async def main():
    async with async_playwright() as p:
        exe = shutil.which('google-chrome') or shutil.which('chromium')
        b = await (p.chromium.launch(executable_path=exe, args=['--no-sandbox']) if exe else p.chromium.launch())
        ctx = await b.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        errs = []

        async def route(r):
            u = r.request.url
            if u.startswith(URL + 'data/'):
                name = urllib.parse.unquote(u[len(URL + 'data/'):])
                data_hits.append(name)
                p_ = os.path.join(DATA_DIR, name)
                if not MODE['baked'] or name in MODE['missing'] or not os.path.exists(p_):
                    return await r.fulfill(status=404, body='not found')
                return await r.fulfill(path=p_, content_type='application/json')
            if u.startswith(URL):
                return await r.fulfill(path=os.path.join(ROOT, 'index.html'), content_type='text/html; charset=utf-8')
            if 'unpkg.com/leaflet@1.9.4/dist/leaflet.css' in u:
                return await r.fulfill(path=LF + 'leaflet.css', content_type='text/css')
            if 'leaflet-polylinedecorator' in u:
                return await r.fulfill(path=PD, content_type='application/javascript')
            if 'unpkg.com/leaflet@1.9.4/dist/leaflet.js' in u:
                return await r.fulfill(path=LF + 'leaflet.js', content_type='application/javascript')
            if 'tile.openstreetmap.org' in u:
                return await r.fulfill(body=PNG, content_type='image/png')
            if 'interpreter' in u:
                req = r.request
                q = urllib.parse.parse_qs(req.post_data or '').get('data', [''])[0]
                calls.append({'host': u.split('/')[2], 'ct': req.headers.get('content-type', ''), 'layers': [] if 'rel(bn.s)' in q else layers_in(q), 'q': q})
                if 'overpass-api.de' in u and 'school' in layers_in(q):
                    return await r.fulfill(status=504, body='timeout')          # 測試改用備用伺服器
                if any(FAIL.get(l) for l in layers_in(q)):
                    return await r.fulfill(status=429, body='busy')             # 測試全部伺服器忙碌
                await asyncio.sleep(0.3)
                return await r.fulfill(body=json.dumps({'elements': answer(q)}), content_type='application/json',
                                       headers={'Access-Control-Allow-Origin': '*'})
            return await r.abort()
        page = await ctx.new_page()
        page.on('pageerror', lambda e: errs.append(str(e)))
        await page.route('**/*', route)
        print('\n=== A. 沒有預建資料時（即時查 Overpass 的備援流程）===')
        MODE['baked'] = False
        await live_suite(page, errs)
        await page.close()
        # ---- B. 預建資料 ----
        print('\n=== B. 有預建資料時（data/*.json，GitHub Actions 每週更新）===')
        import build_data, shutil as _sh
        _sh.rmtree(DATA_DIR, ignore_errors=True)
        idx = build_data.build(fetch=lambda q: {'elements': answer(q)}, out_dir=DATA_DIR, pause=0)
        check(len(idx['districts']) == 12 and idx['routes'] > 0, f"建置程式產生 12 區資料與 {idx['routes']} 條路線")
        size = sum(os.path.getsize(os.path.join(DATA_DIR, f)) for f in os.listdir(DATA_DIR)) / 1e6
        MODE['baked'] = True
        ctx2 = await b.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        page = await ctx2.new_page()
        page.on('pageerror', lambda e: errs.append(str(e)))
        await page.route('**/*', route)
        await baked_suite(page, errs)
        check(not errs, f'沒有 JavaScript 錯誤 {errs}')
        await b.close()
        print(f"\n{'全部通過' if not fails else str(len(fails)) + ' 項失敗'}（{len(fails)} 失敗）")
        return fails

os.makedirs(os.path.join(HERE, 'out'), exist_ok=True)
sys.exit(1 if asyncio.run(main()) else 0)
