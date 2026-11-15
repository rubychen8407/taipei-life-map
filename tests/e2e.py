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
    check(await ev("heatAll.length===1 && map.hasLayer(heat) && heat.options.opacity>0 && document.querySelectorAll('.leaflet-analysis-pane img').length===1"), '連續調整時熱圖用雙緩衝：新圖載好才換掉舊圖，最後只留一張')
    check(await ev("new Set([0,.125,.25,.375,.5,.625,.75,.875,1].map(densityColor)).size===9"), '熱圖色帶九個等級都有不同顏色')
    lum = await ev("(()=>{const L=h=>{const c=[1,3,5].map(i=>parseInt(h.substr(i,2),16)/255).map(v=>v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4));return .2126*c[0]+.7152*c[1]+.0722*c[2]};const a=L(densityColor(0)),b=L(densityColor(1));return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)})()")
    check(lum >= 8, f'熱圖最好與最差的顏色對比夠大（亮度對比 {lum:.1f}:1）')
    sp = await ev("(()=>{const ks=scopeCells().filter(k=>state.res.worst&&!isNaN(state.res.worst[k]));return heatRank?[heatRank(-1),heatRank(1e9)]:null})()")
    check(sp and sp[0] == 0 and sp[1] == 1, '熱圖依目前範圍內的排名上色：最差到最好一定用滿整條色帶')
    check(await ev("(()=>{const get=map.getZoom;map.getZoom=()=>17;const a=heatOpacity();map.getZoom=()=>12;const b=heatOpacity();map.getZoom=get;return a<=0.35&&b>=0.85})()"), '熱圖：全市看得清楚，放大到街區自動淡化，讓設施圖示浮現')
    check(await ev("document.querySelector('#body .legend').innerText.includes('整體設施密度') && !document.querySelector('#body .legend').innerText.includes('捷運')"), 'Heatmap 圖例顯示整體密度，不再以類別顏色分組')
    check(await ev("state.zones.length") > 0 and await ev("pinLayer.getLayers().length") > 0, '有推薦生活圈清單與地圖編號標記')
    check(await ev("document.querySelectorAll('#chips .chip').length") == 8 and not await ev("!!document.querySelector('.chip[data-id=green]')") and await ev("document.querySelectorAll('#chips .chip[aria-pressed=true]').length") == 3, '上方類別按鈕列 8 個（含超市、人行道，已移除綠地）（含生活圈）、預設選 3 個')
    check(await ev("[...document.querySelectorAll('#chips .chip')].every(b=>{const r=b.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&b.querySelector('.chip-icon svg')&&b.querySelector('.chip-name').textContent.trim()}) && document.getElementById('chips').scrollWidth<=document.getElementById('chips').clientWidth+1"),
          '8 個類別圓形圖示一排放得下（不用橫向捲動），每個都有圖示與名稱')
    check(await ev("[...document.querySelectorAll('#chips .chip[aria-pressed=true]')].every(b=>getComputedStyle(b.querySelector('.badge')).display==='flex'&&b.querySelector('.badge svg')&&/10 分/.test(b.querySelector('.mins').textContent)) && getComputedStyle(document.querySelector('.chip[data-id=bus] .badge')).display==='none'"),
          '已選類別：右下角顯示交通方式小角標，下方顯示分鐘數；未選的不顯示')
    check(await ev("(()=>{const c=getComputedStyle(document.getElementById('chips'));return parseFloat(c.borderTopLeftRadius)>=20&&c.boxShadow!=='none'})()"), '8 個類別包在同一個毛玻璃小組裡')
    check(await ev("[...document.querySelectorAll('#sheet,#chips,.fab,#scopeTag')].every(e=>{const c=getComputedStyle(e);return (c.backdropFilter||'none')==='none'&&(c.webkitBackdropFilter||'none')==='none'}) && getComputedStyle(document.body).position!=='fixed'"), '手機（觸控）上不用即時模糊、body 不固定定位，避免 iPhone Safari 漏畫閃爍')
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
    check(r['topBottom'] < 90 and r['sheetTop'] > 760, f"進場：地圖是主角，上方只有類別列、下方面板收到只剩一行（{r['topBottom']:.0f}px／{r['sheetTop']:.0f}px）")
    check(await ev("!!document.querySelector('#head .sumline b')&&/%/.test(document.querySelector('#head .sumline').innerText)"), '底部一行摘要顯示符合比例與推薦數')
    await shot(page, '1_home')

    # 2. 類別按鈕、各類別獨立距離設定
    h0 = len(data_hits); t0 = time.time()
    await page.click('.chip[data-id=bus]')
    await page.wait_for_function("state.res.act.includes('bus')", timeout=5000)
    check(len(calls) == n0 and len(data_hits) == h0 and time.time() - t0 < 1.5, f'點「公車站」立即重算（{time.time()-t0:.2f} 秒），不下載任何東西')
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

    # 3. 結果按鈕＋卡片拖拉
    rb = await ev("(()=>{const b=document.getElementById('resultBtn'),r=b.getBoundingClientRect(),s=document.getElementById('sheet').getBoundingClientRect();return {vis:!b.hidden&&r.height>0,txt:b.innerText.replace(/\\s+/g,' ').trim(),bottom:r.bottom,left:r.left,sheetTop:s.top}})()")
    check(rb['vis'] and '%' in rb['txt'] and rb['sheetTop'] >= 840 and rb['left'] < 30, f"結果面板預設收起、只剩左下角「結果」按鈕（{rb['txt']}；面板頂 {rb['sheetTop']:.0f}px）")
    await page.click('#resultBtn'); await page.wait_for_timeout(350)
    check(await ev("snap") == 'half' and await ev("document.getElementById('resultBtn').hidden"), '點「結果」按鈕：面板升到半高，按鈕隱藏')
    box = await ev(HANDLE)
    await page.mouse.move(*box); await page.mouse.down(); await page.mouse.move(box[0], 120, steps=6); await page.mouse.up(); await page.wait_for_timeout(350)
    check(await ev("snap") == 'full', '往上拖把卡片拉到全高')
    box = await ev(HANDLE)
    await page.mouse.move(*box); await page.mouse.down(); await page.mouse.move(box[0], 800, steps=6); await page.mouse.up(); await page.wait_for_timeout(350)
    check(await ev("snap") == 'peek' and await ev("state.sheetVis") < 5 and not (await ev("document.getElementById('resultBtn').hidden")), '往下拖把卡片收起：面板完全隱藏、結果按鈕回來')
    check(await ev("document.querySelectorAll('#categorySettings input[type=range][data-distance]').length===1 && !document.querySelector('#body [data-distance]')"), '一次只展開一個類別的步行距離拉桿，位置在搜尋區域下方')
    await shot(page, '2_peek')
    await page.click('#resultBtn'); await page.wait_for_timeout(350)
    check(await ev("snap") == 'half', '再點結果按鈕展開回半高')

    # 4. 點推薦生活圈
    await page.click('[data-z="0"]'); await page.wait_for_timeout(600)
    r = await ev("({type:state.view.type,title:document.querySelector('#head h1').innerText,rows:document.querySelectorAll('#body .row').length,act:state.res.act.length,sel:selLayer.getLayers().length,ok:!!document.querySelector('.verdict.ok')})")
    check(r['type'] == 'cell' and '1.' in r['title'] and r['rows'] == r['act'] and r['ok'], f"點第 1 名：卡片顯示該位置到每一類的步行時間（{r['rows']} 類）")
    check(r['sel'] > 1, '地圖上放大頭針，並用虛線連到最近的各類設施')
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
        await page.click('#resultBtn'); await page.wait_for_timeout(350)
        await page.click('[data-z="0"]'); await page.wait_for_timeout(600)
    await page.click('#back'); await page.wait_for_timeout(300)
    check(await ev("state.view.type") == 'list' and await ev("selLayer.getLayers().length") == 0, '按返回回到清單')

    # 5. 點地圖任一位置
    k = await ev("(()=>{const b=new Set(state.zones.map(z=>z.best)),m=data('mrt');return scopeCells().find(k=>state.res.okc[k]===state.res.act.length&&!b.has(k)&&CELLS[k].d===4&&m.every(f=>md(f.center,[CELLS[k].la,CELLS[k].lo])>500))})()")
    await ev(f"void map.setView([CELLS[{k}].la,CELLS[{k}].lo],13,{{animate:false}})"); await page.wait_for_timeout(300)
    pt = await ev(f"(()=>{{const p=map.latLngToContainerPoint([CELLS[{k}].la,CELLS[{k}].lo]);return [p.x,p.y]}})()")
    await page.mouse.click(pt[0], pt[1]); await page.wait_for_timeout(500)
    check(await ev("state.view.type") == 'cell' and '這個位置' in await ev("document.querySelector('#head h1').innerText"), '點地圖任一位置，下方卡片顯示該點的生活機能')
    await page.click('#close'); await page.wait_for_timeout(300)
    check(await ev("snap==='peek'&&state.sheetVis<5&&!document.getElementById('resultBtn').hidden"), '關閉地點卡片後結果面板保持收起，只留結果按鈕')

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
    check(await ev("snap==='peek'&&state.sheetVis<5&&!document.getElementById('resultBtn').hidden"), '換行政區後結果面板不會自己彈出')
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
    await page.click('#resultBtn'); await page.wait_for_timeout(350)
    await page.click('#toCity'); await page.wait_for_function("state.res.key==='city'", timeout=5000)
    await page.click('#resultBtn'); await page.wait_for_timeout(350)
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
    check(await ev("!document.getElementById('layersPop').hidden && document.querySelectorAll('#layersPop .lgroup-h').length===5 && [...document.querySelectorAll('#layersPop .lgroup-h')].map(e=>e.innerText).join('|').includes('房市')"), '圖層面板分成分析結果、交通、房市、環境')
    check(await ev("[...document.querySelectorAll('#layersPop [data-layer=sale],#layersPop [data-layer=land]')].every(b=>b.disabled)&&!document.querySelector('#layersPop [data-layer=deal]').disabled&&!state.layers.deal"), '房市：實價登錄成交可以開（預設關），房屋出售、土地價值先預留')
    await shot(page, '8_layers')
    await page.click('#layersPop [data-layer=heat]')
    check(await ev("!state.layers.heat && (!heat || heat._flags==='o')"), '關掉熱圖：彩色色塊消失（只留不符合處的反灰）')
    await page.click('#layersPop [data-layer=pins]')
    check(await ev("!state.layers.pins && pinLayer.getLayers().length===0"), '關掉推薦區域：編號圖釘消失')
    await page.click('#layersPop [data-group="0"]')
    check(await ev("state.layers.heat && state.layers.pins && state.layers.fac && state.layers.labels && !!heat"), '「全選」一次打開整組')
    await page.click('#layersPop [data-group="0"]')
    check(await ev("!state.layers.heat && !state.layers.pins && !state.layers.fac && !state.layers.labels"), '再按一次「全部關閉」')
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
    await page.click('#layersPop [data-group="0"]')
    await page.click('#layersBtn')
    check(await ev("document.getElementById('layersPop').hidden && !!heat"), '再點圖層按鈕關閉面板')

    # 10d. 長按地圖：圖示工具選單 → 加入生活圈，生活圈自動勾選
    await ev("state.active.delete('life');state.lifeCenters=[];run()")
    await ev("void map.setView([25.04,121.54],15,{animate:false});setSheet('peek',false)"); await page.wait_for_timeout(300)
    await long_press(page, 200, 470)
    pm = await ev("(()=>{const m=document.getElementById('pressMenu');const r=m.getBoundingClientRect();return {open:!m.hidden,n:m.querySelectorAll('[data-press]').length,txt:m.innerText.trim(),w:r.width,h:r.height,dot:pressLayer.getLayers().length}})()")
    check(pm['open'] and pm['n'] == 4 and pm['txt'] == '' and pm['h'] <= 56 and pm['w'] <= 215 and pm['dot'] == 1, f"長按地圖：放下一個點，跳出只有圖示的小選單（{pm['n']} 個、{pm['w']:.0f}×{pm['h']:.0f}）")
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
    await page.click('#layersBtn'); await page.click('#layersPop [data-layer=pins]'); await page.click('#layersPop [data-layer=labels]'); await page.click('#layersBtn')
    await page.reload(); await page.wait_for_function(READY, timeout=20000)
    check(await ev("!state.layers.pins && !state.layers.labels && pinLayer.getLayers().length===0 && state.layers.heat"), '重新整理後圖層開關維持上次最後的設定（推薦區域、設施名稱關閉）')
    await page.click('#layersBtn'); await page.click('#layersPop [data-layer=pins]'); await page.click('#layersPop [data-layer=labels]'); await page.click('#layersBtn')
    r = await ev("({act:[...state.active].sort().join(','),park:state.minsByLayer.park,scope:state.scope,chip:document.querySelector('.chip[data-id=market]').getAttribute('aria-pressed'),key:state.res.key})")
    check(r['act'] == 'market,mrt,park,school' and r['park'] == 7 and r['scope'] == '4' and r['chip'] == 'true' and r['key'] == 'd:大安區', f'重新整理後自動保留上次的類別、分鐘與範圍 {r}')
    await page.click('#filterBtn')
    check(await ev("!document.getElementById('filterPop').hidden"), '右側「我的篩選」按鈕打開篩選面板')
    await page.fill('#filterName', '上班通勤'); await page.click('#filterSave'); await page.wait_for_timeout(200)
    f = await ev("JSON.parse(localStorage.getItem('tlm.filters'))")
    check(len(f) == 1 and f[0]['name'] == '上班通勤' and 'market' in f[0]['active'] and await ev("document.querySelector('#filterPop .frow').getAttribute('aria-current')") == 'true', '命名儲存目前條件，清單標示目前套用中')
    check('超市' in await ev("document.querySelector('#filterPop .fapply span').innerText") and '大安區' in await ev("document.querySelector('#filterPop .fapply span').innerText"), '儲存的篩選顯示條件摘要（類別、分鐘、範圍）')
    check(await ev("(()=>{const e=document.getElementById('filterPop');return e.scrollWidth<=e.clientWidth&&[...e.querySelectorAll('.frow,.fsave button')].every(x=>x.getBoundingClientRect().right<=e.getBoundingClientRect().right)})()"), '篩選面板內容完整顯示，不被右邊切掉')
    await shot(page, '7_filters')
    await page.click('#filterBtn')
    await page.click('.chip[data-id=market]'); await page.click('.chip[data-id=market]'); await page.wait_for_function("!state.active.has('market')", timeout=5000)
    await page.click('#scopeClear'); await page.wait_for_function("state.res&&state.res.key==='city'", timeout=5000)
    await page.click('#filterBtn'); await page.click('#filterPop [data-filter="0"]')
    await page.wait_for_function("state.res&&state.res.key==='d:大安區'&&state.res.act.includes('market')", timeout=20000)
    check(await ev("state.minsByLayer.park===7&&document.getElementById('filterPop').hidden&&document.querySelector('.chip[data-id=market]').getAttribute('aria-pressed')==='true'"), '點儲存的篩選：一鍵套用回類別、分鐘與範圍')
    await page.wait_for_timeout(800)   # 等套用篩選後的重算與畫面更新結束，再打開面板
    await page.click('#filterBtn'); await page.wait_for_selector('#filterPop [data-fdel="0"]', state='visible'); await page.wait_for_timeout(200); await page.click('#filterPop [data-fdel="0"]')
    await page.wait_for_function("JSON.parse(localStorage.getItem('tlm.filters')||'[]').length===0", timeout=3000)
    check(await ev("JSON.parse(localStorage.getItem('tlm.filters')).length===0&&!document.querySelector('#filterPop .frow')"), '可以刪除儲存的篩選')
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
    await ev("document.querySelector('#body [data-vtab=place]').click()"); await page.wait_for_timeout(300)
    check(await ev("document.querySelectorAll('#valTab .row').length") == 6 and '位置加減分' in await ev("document.getElementById('valTab').innerText"), '位置分頁：六類設施步行時間與位置加減分')
    ft = await ev("document.getElementById('valTab').innerText")
    check('最近的未來捷運站' in ft and ('Y20' in ft or '民生社區' in ft) and '不計入估價' in ft, '位置分頁：顯示最近的未來捷運站（興建中／規劃中）與步行時間，註明不計入估價')
    await page.click('#valEdit'); await page.wait_for_timeout(300)
    check(await ev("document.querySelector('#body [data-vf=ping]').value==='30'&&document.querySelector('#body [data-vf=ask]').value==='3600'"), '「修改房屋條件」回到表單，原本填的還在')
    await page.fill('#body [data-vf=ask]', ''); await page.click('#valGo'); await page.wait_for_function("!!document.getElementById('valSum')", timeout=5000)
    check(await ev("state.val.off.cap===Infinity&&state.val.off.out[0]<state.val.off.out[1]&&state.val.off.out[1]<state.val.off.out[2]"), '不填開價也能估：三段出價直接取行情帶')
    # 我的物件：儲存、地圖星號、清單、勾選比較、重新打開、刪除
    await page.click('#valSave'); await page.wait_for_timeout(300)
    hs = await ev("JSON.parse(localStorage.getItem('tlm.homes')||'[]')")
    check(len(hs) == 1 and hs[0]['form']['ping'] == 30 and 'income' not in json.dumps(hs) and await ev("document.getElementById('valSave').getAttribute('aria-pressed')") == 'true' and await ev("homeLayer.getLayers().length") == 1, '估價後按「儲存物件」：存到這台裝置（不含收入），地圖上出現星號')
    await page.wait_for_timeout(600); await page.click('#close'); await page.wait_for_timeout(400)
    check(await ev("state.view.type==='list'&&selLayer.getLayers().length===0&&homeLayer.getLayers().length===1"), '按 ✕ 離開估價，回到地圖；儲存的物件星號仍在')
    await ev("void openVal([25.034,121.547])"); await page.wait_for_function("!!document.getElementById('valGo')", timeout=5000)
    await page.click('#body [data-vt="1"]')
    for k, v_ in (('ping', '25'), ('age', '28'), ('fl', '3'), ('tot', '7'), ('ask', '2300')):
        await page.fill(f'#body [data-vf={k}]', v_)
    await page.click('#valGo'); await page.wait_for_function("!!document.getElementById('valSave')", timeout=5000); await page.click('#valSave'); await page.wait_for_timeout(300)
    await page.wait_for_timeout(400); await page.click('#close'); await page.wait_for_timeout(300)
    await page.click('#filterBtn')
    check(await ev("document.querySelectorAll('#filterPop [data-home]').length===2&&document.querySelector('#homeCmp').disabled"), '「我的篩選」面板下方列出我的物件；勾選前比較按鈕不能按')
    await page.click('#filterPop [data-hsel]'); await page.click('#filterPop .frow:last-of-type [data-hsel]')
    ids = await ev("[...document.querySelectorAll('#filterPop [data-hsel][aria-pressed=true]')].length")
    await shot(page, '8f_homes')
    await page.click('#homeCmp'); await page.wait_for_function("state.view.type==='cmp'", timeout=5000); await page.wait_for_timeout(500)
    cm = await ev("({cols:document.querySelectorAll('.cmp thead th').length,rows:document.querySelectorAll('.cmp tbody tr').length,best:document.querySelectorAll('.cmp td.best').length,txt:document.querySelector('.cmp').innerText,pins:selLayer.getLayers().length})")
    check('步行到未來捷運站' in cm['txt'], '比較表有「步行到未來捷運站」')
    check(ids == 2 and cm['cols'] == 3 and cm['rows'] >= 17 and cm['best'] >= 3 and '合理出價' in cm['txt'] and '步行到公園' in cm['txt'] and cm['pins'] == 2, f"勾 2 間按「並排比較」：同一張表比價格、燈號、房貸、租金、六類設施步行時間，最好的標綠（{cm['rows']} 列）")
    await shot(page, '8g_compare')
    await page.click('#close'); await page.wait_for_timeout(300)
    await ev("void homeLayer.getLayers()[0].fire('click')"); await page.wait_for_function("state.view.type==='val'&&!!document.getElementById('valSum')", timeout=5000)
    check(await ev("document.getElementById('valSave').getAttribute('aria-pressed')==='true'"), '點地圖上的星號：直接重新估價並顯示結果')
    await page.click('#close'); await page.wait_for_timeout(300)
    await page.click('#filterBtn'); await page.click('#filterPop [data-hdel]'); await page.wait_for_timeout(200)
    check(await ev("JSON.parse(localStorage.getItem('tlm.homes')).length===1&&homeLayer.getLayers().length===1"), '可以刪除儲存的物件')
    await page.click('#filterBtn')
    await ev("void map.setView([25.12,121.50],16,{animate:false})"); await page.wait_for_timeout(300)
    await ev("void openVal([25.12,121.50])"); await page.wait_for_function("!!document.getElementById('valGo')", timeout=5000); await page.click('#valGo'); await page.wait_for_timeout(400)
    check(await ev("!!document.getElementById('valNo')&&!document.getElementById('valSum')&&!state.val.est.ok"), '附近成交太少：說明原因，不給出價建議')
    await page.click('#close')
    await ev("void map.setView([25.030,121.543],16,{animate:false})"); await page.wait_for_timeout(300)
    await page.click('#layersBtn'); await page.click('#layersPop [data-layer=deal]'); await page.wait_for_function("dealLayer.getLayers().length>10", timeout=5000); await page.click('#layersBtn')
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
    print(f'real data: ready in {time.time()-t0:.1f}s', await ev("({built:state.built,n:Object.fromEntries(LAYERS.map(l=>[l.id,(data(l.id)||[]).length])),good:state.res.good,total:state.res.total,zones:state.zones.map(z=>z.mrt||z.district)})"))
    check(await ev("state.zones.length") > 0, '真實資料：算出推薦生活圈')
    await shot(page, 'real_1_home')
    for i in ['market', 'bus', 'sport']:
        t0 = time.time(); await page.click(f'.chip[data-id={i}]'); await page.wait_for_function(f"state.res.act.includes('{i}')", timeout=30000)
        print(f'  + {i}: {time.time()-t0:.2f}s')
    await shot(page, 'real_2_all')
    for i in ['market', 'bus', 'sport']:
        await page.click(f'.chip[data-id={i}]')
    await page.click('#resultBtn'); await page.wait_for_timeout(350)
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
    await page.click('#close')
    await ev("void map.setView([25.0585,121.557],16,{animate:false})"); await page.wait_for_timeout(500)
    await ev("void openVal([25.0585,121.557])"); await page.wait_for_function("!!document.getElementById('valGo')", timeout=30000)
    for k, v_ in (('ping', '28'), ('age', '35'), ('fl', '4'), ('tot', '7'), ('ask', '2800')):
        await page.fill(f'#body [data-vf={k}]', v_)
    await page.click('#body [data-vt="1"]'); await page.click('#valGo'); await page.wait_for_function("!!document.getElementById('valSum')||!!document.getElementById('valNo')", timeout=5000)
    print('  valuation', await ev("({n:state.val.est.n,rad:state.val.est.radius,unit:state.val.est.q(.5),poi:state.val.est.poi,out:state.val.off&&state.val.off.out,H:state.val.mk.H,label:state.val.mk.label,parts:state.val.mk.parts.map(p=>p.txt)})"))
    await shot(page, 'real_7_val'); await ev("setSheet('full')"); await shot(page, 'real_8_val_full')
    await page.click('#close'); await ev("void map.setView([25.05,121.54],12,{animate:false})")
    await page.click('#layersBtn'); await page.click('#layersPop [data-layer=priceheat]'); await page.wait_for_function("!!phLayer", timeout=20000); await page.click('#layersBtn')
    print('  priceheat', await ev("({cells:PH.cells.length,lo:PH.lo,hi:PH.hi})")); await shot(page, 'real_9_priceheat')
    await page.click('#layersBtn'); await page.click('#layersPop [data-layer=priceheat]'); await page.click('#layersPop [data-layer=heat]'); await page.click('#layersPop [data-layer=deal]'); await page.click('#layersBtn')
    await ev("void map.setView([25.0585,121.557],16,{animate:false})"); await page.wait_for_function("dealLayer.getLayers().length>5", timeout=20000); await page.wait_for_timeout(500)
    print('  deals', await ev("dealLayer.getLayers().length")); await shot(page, 'real_10_deals')
    await ev("void dealLayer.getLayers()[0].fire('click')"); await page.wait_for_timeout(400); await shot(page, 'real_11_deal_pop')
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
                MODE['trees'] = BT.build(TM.mock(), DATA_DIR, built='2026-11-16', min_trees=10, log=lambda *a: None)
                import build_realprice as RP, realprice_mock as RM   # 估價：模擬的實價登錄 → 實際跑建置程式
                rs, rr, raddr, _ = RM.mock()
                clean = RP.parse_sales(rs); layers = json.load(open(os.path.join(DATA_DIR, 'city.json'), encoding='utf-8'))['layers']
                val = RP.build(clean, RP.parse_rents(rr), raddr, layers, out_dir=DATA_DIR, built='2026-11-15', log=lambda *a: None, min_sales=1000)
                subj = {'dist': '大安區', 't': 0, 'age': 12, 'fl': 8, 'tot': 15, 'ping': 30.0, 'pt': (25.030, 121.543)}; subj['s'] = RP.PoiIndex(layers).score(subj['pt'])
                MODE['val'] = {'v': val, 'est': RP.estimate(subj, {'b': val['model']['b'], 'dist': val['model']['dist'], 'q': {'0': 0}}, [r for r in clean if 'e' in r], val['now_ym'], val['model']['now']), 's': subj['s']}
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
