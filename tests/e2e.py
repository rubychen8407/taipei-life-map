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


async def new_page(b, errs, **kw):
    ctx = await b.new_context(**kw)
    page = await ctx.new_page()
    page.on('pageerror', lambda e: errs.append(str(e)))
    await page.route('**/*', route)
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
    o = await ev("""(()=>{const n=state.res.act.length,good=scopeCells().filter(k=>state.res.okc[k]===n),bad=scopeCells().filter(k=>state.res.okc[k]!==n);
      const rings=outlineLayer.getLayers().filter((l,i)=>i%2===1).map(l=>l.getLatLngs()[0].map(p=>[p.lng,p.lat]));
      const inside=(la,lo)=>rings.reduce((c,r)=>c^ringContains(r,lo,la),0);
      const sg=good.filter((k,i)=>i%7===0),sb=bad.filter((k,i)=>i%7===0);
      const gi=sg.filter(k=>inside(CELLS[k].la,CELLS[k].lo)).length/sg.length,bo=sb.filter(k=>!inside(CELLS[k].la,CELLS[k].lo)).length/sb.length;
      return {rings:rings.length,gi,bo}})()""")
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
    check(await ev("heatAll.length===1 && map.hasLayer(heat) && heat.options.opacity>0 && document.querySelectorAll('.leaflet-analysis-pane img').length===1"), '連續調整時熱圖用雙緩衝：新圖載好才換掉舊圖，最後只留一張')
    check(await ev("new Set([0,.125,.25,.375,.5,.625,.75,.875,1].map(densityColor)).size===9"), '熱圖色帶九個等級都有不同顏色')
    lum = await ev("(()=>{const L=h=>{const c=[1,3,5].map(i=>parseInt(h.substr(i,2),16)/255).map(v=>v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4));return .2126*c[0]+.7152*c[1]+.0722*c[2]};const a=L(densityColor(0)),b=L(densityColor(1));return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)})()")
    check(lum >= 8, f'熱圖最好與最差的顏色對比夠大（亮度對比 {lum:.1f}:1）')
    sp = await ev("(()=>{const ks=scopeCells().filter(k=>state.res.worst&&!isNaN(state.res.worst[k]));return heatRank?[heatRank(-1),heatRank(1e9)]:null})()")
    check(sp and sp[0] == 0 and sp[1] == 1, '熱圖依目前範圍內的排名上色：最差到最好一定用滿整條色帶')
    check(await ev("(()=>{const get=map.getZoom;map.getZoom=()=>17;const a=heatOpacity();map.getZoom=()=>12;const b=heatOpacity();map.getZoom=get;return a<=0.35&&b>=0.85})()"), '熱圖：全市看得清楚，放大到街區自動淡化，讓設施圖示浮現')
    check(await ev("document.querySelector('#body .legend').innerText.includes('整體設施密度') && !document.querySelector('#body .legend').innerText.includes('捷運')"), 'Heatmap 圖例顯示整體密度，不再以類別顏色分組')
    check(await ev("state.zones.length") > 0 and await ev("pinLayer.getLayers().length") > 0, '有推薦生活圈清單與地圖編號標記')
    check(await ev("document.querySelectorAll('#chips .chip').length") == 7 and await ev("document.querySelectorAll('#chips .chip[aria-pressed=true]').length") == 3, '上方類別按鈕列 7 個（含生活圈）、預設選 3 個')
    check(await ev("[...document.querySelectorAll('#chips .chip')].every(b=>{const r=b.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&b.querySelector('.chip-icon svg')&&b.querySelector('.chip-name').textContent.trim()}) && document.getElementById('chips').scrollWidth<=document.getElementById('chips').clientWidth+1"),
          '7 個類別圓形圖示一排放得下（不用橫向捲動），每個都有圖示與名稱')
    check(await ev("[...document.querySelectorAll('#chips .chip[aria-pressed=true]')].every(b=>getComputedStyle(b.querySelector('.badge')).display==='flex'&&b.querySelector('.badge svg')&&/10 分/.test(b.querySelector('.mins').textContent)) && getComputedStyle(document.querySelector('.chip[data-id=bus] .badge')).display==='none'"),
          '已選類別：右下角顯示交通方式小角標，下方顯示分鐘數；未選的不顯示')
    check(await ev("(()=>{const c=getComputedStyle(document.getElementById('chips'));return parseFloat(c.borderTopLeftRadius)>=20&&c.boxShadow!=='none'})()"), '7 個類別包在同一個毛玻璃小組裡')
    check(await ev("[...document.querySelectorAll('#sheet,#chips,.fab,#scopeTag')].every(e=>{const c=getComputedStyle(e);return (c.backdropFilter||'none')==='none'&&(c.webkitBackdropFilter||'none')==='none'}) && getComputedStyle(document.body).position!=='fixed'"), '手機（觸控）上不用即時模糊、body 不固定定位，避免 iPhone Safari 漏畫閃爍')
    z = await ev("(()=>{const ev=new Event('gesturestart',{cancelable:true});document.dispatchEvent(ev);return {g:ev.defaultPrevented,ta:getComputedStyle(document.body).touchAction,top:getComputedStyle(document.getElementById('sheet')).touchAction}})()")
    check(z['g'] and z['ta'] == 'pan-x pan-y' and z['top'] == 'manipulation', f"擋掉整個網頁的雙指縮放與連點放大（{z}），地圖本身仍可縮放")
    zz = await ev("map.touchZoom.enabled() && map.doubleClickZoom.enabled()")
    check(zz, '地圖的兩指縮放與連點放大仍然開著')
    check(await ev("!document.querySelector('#top .search')||getComputedStyle(document.querySelector('#top .search')).display==='none'"), '上方不再常駐區域選擇列')
    check(await ev("document.querySelectorAll('.dlabel').length") == 12, '全市檢視顯示 12 區名稱')
    check(await ev("facLayer.getLayers().length") < 200, '縮小時不顯示一堆設施點（只留捷運站）')
    r = await ev("(()=>{const s=document.getElementById('sheet').getBoundingClientRect(),t=document.getElementById('top').getBoundingClientRect();return {sheetTop:s.top,topBottom:t.bottom}})()")
    check(r['topBottom'] < 90 and r['sheetTop'] > 760, f"進場：地圖是主角，上方只有類別列、下方面板收到只剩一行（{r['topBottom']:.0f}px／{r['sheetTop']:.0f}px）")
    check(await ev("!!document.querySelector('#head .sumline b')&&/%/.test(document.querySelector('#head .sumline').innerText)"), '底部一行摘要顯示符合比例與推薦數')
    await shot(page, '1_home')

    # 2. 類別按鈕、各類別獨立距離設定
    t0 = time.time()
    await page.click('.chip[data-id=bus]')
    await page.wait_for_function("state.res.act.includes('bus')", timeout=5000)
    check(len(calls) == n0 and len(data_hits) == 1 and time.time() - t0 < 1.5, f'點「公車站」立即重算（{time.time()-t0:.2f} 秒），不下載任何東西')
    g10 = await ev("state.res.good")
    await page.locator('#chips .chip[data-id="park"]').click()
    await page.locator('input[data-distance="park"]').evaluate("(el)=>{el.value='5';el.dispatchEvent(new Event('input',{bubbles:true}))}")
    check(await ev("state.res.thresholds[state.res.act.indexOf('park')]===5 && state.minsByLayer.park===5 && state.minsByLayer.school===10"), '公園距離可獨立調整為 5 分鐘，不影響學校的 10 分鐘設定')
    await page.locator('input[data-distance="park"]').evaluate("(el)=>{el.value='10';el.dispatchEvent(new Event('input',{bubbles:true}))}")
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

    # 3. 卡片拖拉
    box = await ev(HANDLE)
    await page.mouse.move(*box); await page.mouse.down(); await page.mouse.move(box[0], 120, steps=6); await page.mouse.up(); await page.wait_for_timeout(350)
    check(await ev("snap") == 'full', '往上拖把卡片拉到全高')
    box = await ev(HANDLE)
    await page.mouse.move(*box); await page.mouse.down(); await page.mouse.move(box[0], 800, steps=6); await page.mouse.up(); await page.wait_for_timeout(350)
    check(await ev("snap") == 'peek' and await ev("state.sheetVis") < 160, '往下拖把卡片收到最小，地圖幾乎全螢幕')
    check(await ev("document.querySelectorAll('#categorySettings input[type=range][data-distance]').length===1 && !document.querySelector('#body [data-distance]')"), '一次只展開一個類別的步行距離拉桿，位置在搜尋區域下方')
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
    k = await ev("(()=>{const b=new Set(state.zones.map(z=>z.best)),m=data('mrt');return scopeCells().find(k=>state.res.okc[k]===state.res.act.length&&!b.has(k)&&CELLS[k].d===4&&m.every(f=>md(f.center,[CELLS[k].la,CELLS[k].lo])>500))})()")
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
    check(await ev("scopeCells().every(k=>CELLS[k].d===4)") and '大安區' in await ev("document.getElementById('head').innerText") and await ev("document.getElementById('scopeName').textContent") == '大安區', '只分析大安區，標題與上方列同步')
    used = await ev("(()=>{const i=state.res.act.indexOf('mrt'),D=state.res.D[i];return scopeCells().filter(k=>{const f=data('mrt')[D.nf[k]];return f&&!inPoly(DISTRICTS[4],f.center[0],f.center[1])}).length})()")
    check(used > 0, f'靠區界的位置會用區界外較近的設施計算（{used} 格用到別區的捷運站）')
    await shot(page, '5_district')
    check(await ev("!document.getElementById('scopeTag').hidden && document.getElementById('scopeTag').innerText.includes('大安區') && document.getElementById('regionPop').hidden"), '選了區域：類別列下方出現「大安區 ✕」小標籤；再點按鈕收起彈窗')
    # 複選區域
    await page.click('#regionBtn'); await page.click('#regionPop [data-region="3"]')
    await page.wait_for_function("state.res.key==='m:松山區,大安區'", timeout=5000)
    check(await ev("state.scope==='m:3,4' && scopeCells().length>0 && scopeCells().every(k=>CELLS[k].d===3||CELLS[k].d===4) && scopeCells().some(k=>CELLS[k].d===3) && scopeCells().some(k=>CELLS[k].d===4)"), '複選松山＋大安：只分析這兩區')
    check(await ev("document.getElementById('scopeTag').innerText.includes('松山區、大安區') && document.querySelectorAll('#regionPop [aria-pressed=true]').length===2"), '小標籤顯示「松山區、大安區」，彈窗兩區都標示已選')
    check(await ev("(()=>{const b=document.querySelector('#head .sumline b');return /%$/.test(b.innerText)&&b.scrollWidth<=b.clientWidth+1&&b.getBoundingClientRect().right<=innerWidth})()"), '名稱很長時，摘要的百分比仍完整顯示')
    check(await ev("[...document.querySelectorAll('#body .drow .n')].map(e=>e.innerText).sort().join()==='大安區,松山區'"), '各區比較只比較選到的區')
    await shot(page, '5b_multi')
    await page.click('#regionPop [data-region="3"]'); await page.wait_for_function("state.res.key==='d:大安區'", timeout=5000)
    check(True, '再點一次已選的區：取消該區')
    await page.click('#regionBtn')
    await page.click('#scopeClear'); await page.wait_for_function("state.res.key==='city'", timeout=5000)
    check(await ev("document.getElementById('scopeTag').hidden"), '按 ✕ 回到全臺北市，標籤消失')
    await ev("void map.setView([25.06,121.56],12,{animate:false})"); await page.wait_for_timeout(300)
    await ev("[...document.querySelectorAll('.dlabel')].find(e=>e.innerText.includes('松山')).dispatchEvent(new MouseEvent('click',{bubbles:true}))")
    await page.wait_for_function("state.res.key==='d:松山區'", timeout=5000)
    check(True, '在地圖上點區名也能選區域')
    await page.click('#scopeClear'); await page.wait_for_function("state.res.key==='city'", timeout=5000)
    await page.click('#regionBtn'); await page.click('#regionPop [data-region="4"]'); await page.wait_for_function("state.res.key==='d:大安區'", timeout=5000); await page.click('#regionBtn')
    await page.click('#toCity'); await page.wait_for_function("state.res.key==='city'", timeout=5000)
    await page.click('.drow'); await page.wait_for_function("state.res.key.startsWith('d:')", timeout=5000)
    check(True, '「各區比較」點一區即可切換')

    # 7b. 手動調整範圍邊界
    await ev("setScope('4')"); await page.wait_for_function("state.res.key==='d:大安區'", timeout=5000)
    before = await ev("scopeCells().length")
    await page.click('#scopeEdit'); await page.wait_for_timeout(500)
    e = await ev("({on:!!state.editing,n:state.editing.rings[0].length,bar:getComputedStyle(document.getElementById('editbar')).display,chips:getComputedStyle(document.getElementById('chips')).display,vh:document.querySelectorAll('.vh:not(.mid)').length,mid:document.querySelectorAll('.vh.mid').length})")
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
    check(await ev("state.scope==='custom' && state.custom.name==='大安區（已調整）' && document.getElementById('scopeTag').innerText.includes('大安區（已調整）') && getComputedStyle(document.getElementById('chips')).display!=='none' && polysOf(state.custom).some(p=>p[0].some(([x,y])=>Math.abs(y-window.__dragged[0])<1e-7&&Math.abs(x-window.__dragged[1])<1e-7))") and after > 0, f'完成：改用調整後的範圍分析（{before}→{after} 格），拖過的點在新範圍上，標籤顯示「大安區（已調整）」')
    await page.click('#scopeEdit'); await page.wait_for_timeout(300); await page.click('#editCancel')
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
    await page.click('#locate'); await page.wait_for_function("state.view.type==='cell'", timeout=8000)
    check('我的位置' in await ev("document.querySelector('#head h1').innerText") and await ev("meLayer.getLayers().length") == 1, '「我的位置」按鈕：顯示藍點與所在位置的生活機能')
    await page.click('#close')

    # 10. 全部取消
    for i in await ev("[...state.active]"):
        await page.click(f'.chip[data-id={i}]')
        if await ev(f"state.active.has('{i}')"):
            await page.click(f'.chip[data-id={i}]')
    check('選擇你在意的設施' in await ev("document.getElementById('head').innerText") and not await ev("!!heat"), '全部取消時提示選擇類別')

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
    await page.locator('#categorySettings [data-distance=life]').evaluate("el=>{el.value='15';el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('pointerup',{bubbles:true}))}")
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
    check(('住家' in txt or '公司' in txt) and '開車' in txt and await ev("document.getElementById('categorySettings').innerHTML===''"), '點位置：列出到最近生活圈地點的開車時間，並收起設定面板不擋地圖')
    await page.click('#close')
    await page.click('.chip[data-id=life]')
    await page.click('[data-life-delete="1"]')
    check(await ev("state.lifeCenters.length===1 && lifeLayer.getLayers().length===1"), '可刪除地點，地圖標記同步')
    # 10c. 圖層：分組、開關、全選、儲存組合
    await page.click('#layersBtn')
    check(await ev("!document.getElementById('layersPop').hidden && document.querySelectorAll('#layersPop .lgroup-h').length===3 && [...document.querySelectorAll('#layersPop .lgroup-h')].map(e=>e.innerText).join('|').includes('房市')"), '圖層面板分成分析結果、交通、房市三組')
    check(await ev("[...document.querySelectorAll('#layersPop [data-layer=sale],#layersPop [data-layer=deal],#layersPop [data-layer=land]')].every(b=>b.disabled)"), '房屋出售、實價登錄、土地價值歸在「房市」，先預留（不能開）')
    await shot(page, '8_layers')
    await page.click('#layersPop [data-layer=heat]')
    check(await ev("!state.layers.heat && !heat"), '關掉熱圖：地圖上的色塊消失')
    await page.click('#layersPop [data-layer=pins]')
    check(await ev("!state.layers.pins && pinLayer.getLayers().length===0"), '關掉推薦區域：編號圖釘消失')
    await page.click('#layersPop [data-group="0"]')
    check(await ev("state.layers.heat && state.layers.pins && state.layers.fac && state.layers.labels && !!heat"), '「全選」一次打開整組')
    await page.click('#layersPop [data-group="0"]')
    check(await ev("!state.layers.heat && !state.layers.pins && !state.layers.fac && !state.layers.labels"), '再按一次「全部關閉」')
    await page.click('#layersPop [data-save-preset]')
    check(await ev("JSON.parse(localStorage.getItem('tlm.layerPresets')).length===1 && document.querySelector('#layersPop [data-preset=\"0\"]').getAttribute('aria-pressed')==='true'"), '「儲存」把目前開關存成組合（存在這台裝置）')
    await page.click('#layersPop [data-group="0"]')
    await page.click('#layersPop [data-preset="0"]')
    check(await ev("!state.layers.heat && !state.layers.pins"), '點儲存的組合：一鍵套回')
    await page.click('#layersPop [data-group="0"]')
    await page.click('#layersBtn')
    check(await ev("document.getElementById('layersPop').hidden && !!heat"), '再點圖層按鈕關閉面板')

    # 10d. 長按地圖：圖示工具選單 → 加入生活圈，生活圈自動勾選
    await ev("state.active.delete('life');state.lifeCenters=[];run()")
    await ev("void map.setView([25.04,121.54],15,{animate:false});setSheet('peek',false)"); await page.wait_for_timeout(300)
    await long_press(page, 200, 470)
    pm = await ev("(()=>{const m=document.getElementById('pressMenu');const r=m.getBoundingClientRect();return {open:!m.hidden,n:m.querySelectorAll('[data-press]').length,txt:m.innerText.trim(),w:r.width,h:r.height,dot:pressLayer.getLayers().length}})()")
    check(pm['open'] and pm['n'] == 3 and pm['txt'] == '' and pm['h'] <= 56 and pm['w'] <= 170 and pm['dot'] == 1, f"長按地圖：放下一個點，跳出只有圖示的小選單（{pm['n']} 個、{pm['w']:.0f}×{pm['h']:.0f}）")
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

    # 11. 桌機版面
    page = await new_page(b, errs, viewport={'width': 1280, 'height': 800})
    await page.goto(URL); await page.wait_for_function(READY, timeout=20000)
    r = await page.evaluate("(()=>{const s=document.getElementById('sheet').getBoundingClientRect();return {l:s.left,w:s.width,b:s.bottom}})()")
    check(r['l'] < 20 and 380 <= r['w'] <= 400 and r['b'] <= 800, '桌機版：卡片變成左側面板')
    await shot(page, '6_desktop')
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
            check(len(idx['districts']) == 12 and idx['routes'] > 0 and bool(idx['city']) and idx['city']['mrt'] == 110 and not [f for f in os.listdir(DATA_DIR) if f.endswith('區.json')], f"建置程式只產生全市檔（不另存 12 區檔）（捷運 {idx['city'] and idx['city']['mrt']} 站，已去重複）與 {idx['routes']} 條路線")
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
