import json, asyncio, urllib.parse, base64, sys, os, shutil
HERE=os.path.dirname(os.path.abspath(__file__))
ROOT=os.path.join(HERE,'..')
from playwright.async_api import async_playwright
MOCK=json.load(open(os.path.join(HERE,'mock.json')))
LF=os.path.join(HERE,'node_modules','leaflet','dist')+'/'
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=')
FAIL=dict(sport=True)
calls=[]
def layer_of(q):
    if 'bus_stop' in q:return 'bus'
    if 'subway' in q:return 'mrt'
    if 'amenity' in q:return 'school'
    if 'sports_centre' in q:return 'sport'
    if 'landuse' in q:return 'green'
    return 'park'
async def main():
  async with async_playwright() as p:
    exe=shutil.which('google-chrome') or shutil.which('chromium')
    b=await p.chromium.launch(executable_path=exe,args=['--no-sandbox']) if exe else await p.chromium.launch()
    ctx=await b.new_context(viewport={'width':390,'height':844},device_scale_factor=2,is_mobile=True,has_touch=True)
    page=await ctx.new_page()
    errs=[];page.on('console',lambda m: errs.append(m.text) if m.type in('error',) else None);page.on('pageerror',lambda e:errs.append('PAGEERR '+str(e)))
    async def route(r):
      u=r.request.url
      if u.startswith('https://rubychen8407.github.io/taipei-life-map/'): return await r.fulfill(path=os.path.join(ROOT,'index.html'),content_type='text/html; charset=utf-8')
      if 'unpkg.com/leaflet@1.9.4/dist/leaflet.css' in u: return await r.fulfill(path=LF+'leaflet.css',content_type='text/css')
      if 'unpkg.com/leaflet@1.9.4/dist/leaflet.js' in u: return await r.fulfill(path=LF+'leaflet.js',content_type='application/javascript')
      if 'tile.openstreetmap.org' in u: return await r.fulfill(body=PNG,content_type='image/png')
      if 'interpreter' in u:
        req=r.request; ct=req.headers.get('content-type','')
        q=urllib.parse.parse_qs(req.post_data or '').get('data',[''])[0]
        calls.append((u.split('/')[2],ct,layer_of(q),q[:80]))
        assert 'x-www-form-urlencoded' in ct and q.startswith('[out:json]'), (ct,q[:50])
        lid=layer_of(q)
        if 'overpass-api.de' in u and lid=='school': return await r.fulfill(status=504,body='timeout')  # test fallback
        if FAIL.get(lid): return await r.fulfill(status=429,body='busy')
        await asyncio.sleep(0.5)
        return await r.fulfill(body=json.dumps({"elements":MOCK[lid]}),content_type='application/json',headers={'Access-Control-Allow-Origin':'*'})
      return await r.abort()
    await page.route('**/*',route)
    fails=[]
    def check(cond,msg):
        print(('PASS ' if cond else 'FAIL ')+msg)
        if not cond: fails.append(msg)
    ev=page.evaluate
    await page.goto('https://rubychen8407.github.io/taipei-life-map/')
    await page.wait_for_function("document.getElementById('result').innerHTML.includes('推薦區域')",timeout=30000)
    r=await ev("({n:['park','school','mrt'].map(i=>document.getElementById('n-'+i).textContent),shown:['park','school','mrt'].map(i=>map.hasLayer(state.groups[i])),zones:state.zones.length,st:document.getElementById('status').textContent})")
    check(r['n']==['501','600','110'],f"預設三類載入並顯示數量 {r['n']}（捷運重複站已合併）")
    check(all(r['shown']),'預設三類畫在地圖上')
    check(r['zones']>0 and r['st']=='',f"自動算出推薦區域 {r['zones']} 個")
    zs=await ev("state.zones.map(z=>z.area)"); check(max(zs)<400,f"推薦區域大小合理（最大 {max(zs):.0f} 公頃）")
    await page.screenshot(path=os.path.join(HERE,'out','s1.png'))
    await page.click('#lab-bus input'); await page.wait_for_function("state.computed&&state.computed.act.includes('bus')",timeout=20000)
    r=await ev("({n:document.getElementById('n-bus').textContent,shown:map.hasLayer(state.groups.bus),sum:document.querySelector('.sum').textContent})")
    check(r['n']=='4000' and r['shown'] and '公車站' in r['sum'],'勾公車站後載入、顯示並重算')
    await page.click('#lab-sport input'); await page.wait_for_function("document.getElementById('status').className==='err'",timeout=20000)
    r=await ev("({st:document.getElementById('status').textContent,act:state.computed.act})")
    check('下載失敗' in r['st'] and 'sport' not in r['act'],'伺服器全忙時顯示紅字錯誤，其他類照常計算')
    await page.click('#lab-sport input'); FAIL['sport']=False
    await page.click('#lab-sport input'); await page.wait_for_function("state.computed.act.includes('sport')",timeout=20000)
    r=await ev("({st:document.getElementById('status').textContent,n:document.getElementById('n-sport').textContent,shown:map.hasLayer(state.groups.sport)})")
    check(r['st']=='' and r['n']=='500' and r['shown'],'重新勾選即可重試成功')
    await page.click('#lab-sport input')
    a10=await ev("state.computed.res.filter(x=>x.ok===state.computed.act.length).length")
    await ev("const m=document.getElementById('mins');m.value=5;m.dispatchEvent(new Event('input'))"); await page.wait_for_timeout(800)
    r=await ev("({mins:state.computed.mins,good:state.computed.res.filter(x=>x.ok===state.computed.act.length).length})")
    check(r['mins']==5 and r['good']<a10,f"拉到 5 分鐘自動重算且符合格數變少（{a10}→{r['good']}）")
    await ev("const m=document.getElementById('mins');m.value=10;m.dispatchEvent(new Event('input'))"); await page.wait_for_timeout(800)
    await page.click('[data-z="0"]'); await page.wait_for_timeout(800)
    pop=await ev("document.querySelector('.leaflet-popup-content')?.innerText||''")
    check(pop.startswith('#1') and pop.count('✅')==4,'點推薦區域 #1 放大並列出各類步行時間')
    await page.screenshot(path=os.path.join(HERE,'out','s2.png'))
    await page.click('[data-d]'); await page.wait_for_timeout(500)
    r=await ev("({scope:state.scope,sum:document.querySelector('.sum').textContent})")
    check(r['scope']!='all' and not r['sum'].startswith('全臺北市'),f"點各區排名切換到該區（{r['sum'][:4]}）")
    await page.click('#mDraw'); await page.mouse.click(100,150); await page.mouse.click(300,350); await page.click('#fin'); await page.wait_for_timeout(500)
    r=await ev("({scope:state.scope,sum:document.querySelector('.sum').textContent})")
    check(r['scope']=='custom' and r['sum'].startswith('自選範圍'),'框選 2 點成矩形並只算框內')
    await page.screenshot(path=os.path.join(HERE,'out','s3.png'))
    await page.select_option('#scope','all')
    for i in ['park','school','mrt','bus']: await page.click(f'#lab-{i} input')
    await page.wait_for_timeout(300)
    check('請至少勾選' in await ev("document.getElementById('result').innerText"),'全部取消勾選時顯示提示')
    check(all('x-www-form-urlencoded' in c[1] for c in calls),'Overpass 請求格式正確')
    check(any(c[0]!='overpass-api.de' and c[2]=='school' for c in calls),'主伺服器失敗會改用備用伺服器')
    bad=[e for e in errs if e.startswith('PAGEERR')]
    check(not bad,f'沒有 JavaScript 錯誤 {bad}')
    await b.close()
    print(f"\n{'全部通過' if not fails else str(len(fails))+' 項失敗'}")
    return fails
os.makedirs(os.path.join(HERE,'out'),exist_ok=True)
bad=asyncio.run(main())
sys.exit(1 if bad else 0)
