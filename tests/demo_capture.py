# 暫時：在 GitHub Actions 用真實網路開線上版，錄 demo 影片與高解析截圖（簡報用）
import asyncio, time, json, os
from playwright.async_api import async_playwright
URL = 'https://rubychen8407.github.io/taipei-life-map/'
OUT = 'demo'; os.makedirs(OUT, exist_ok=True)
READY = "window.state&&state.res&&!!document.querySelector('#head .sumline')"
TAP = """window.__tap=(x,y)=>{const d=document.createElement('div');d.style.cssText=`position:fixed;left:${x-22}px;top:${y-22}px;width:44px;height:44px;border-radius:50%;background:rgba(11,87,208,.28);border:2px solid rgba(11,87,208,.85);z-index:99999;pointer-events:none;transition:transform .45s ease-out,opacity .45s ease-out`;document.body.appendChild(d);requestAnimationFrame(()=>{d.style.transform='scale(1.6)';d.style.opacity='0'});setTimeout(()=>d.remove(),600)};
addEventListener('pointerdown',e=>window.__tap&&__tap(e.clientX,e.clientY),true);"""
marks = []
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                                  record_video_dir=OUT, record_video_size={'width': 780, 'height': 1688}, locale='zh-TW')
        await ctx.add_init_script(TAP)
        page = await ctx.new_page(); ev = page.evaluate; T0 = time.time()
        page.on('pageerror', lambda e: print('PAGEERR', e, flush=True)); page.on('console', lambda m: m.type in ('error','warning') and print('CONSOLE', m.text[:200], flush=True))
        page.on('requestfailed', lambda r: print('REQFAIL', r.url[:120], r.failure, flush=True))
        def mark(n): marks.append((n, round(time.time() - T0, 2))); print('MARK', n, marks[-1][1], flush=True)
        async def shot(n): await page.screenshot(path=f'{OUT}/{n}.png')
        async def tapxy(x, y): await page.mouse.click(x, y)
        async def tapsel(sel): await page.click(sel)
        await page.goto(URL)
        try: await page.wait_for_function(READY, timeout=60000)
        except Exception:
            print('NOTREADY', await ev("JSON.stringify({st:!!window.state,res:!!(window.state&&state.res),head:document.getElementById('head')&&document.getElementById('head').innerText.slice(0,200),L:typeof L})")); await page.screenshot(path=f'{OUT}/fail.png'); raise
        await page.wait_for_timeout(2500)
        mark('home'); await shot('s_home')
        await page.wait_for_timeout(1500)
        # 加條件：超市
        await tapsel('.chip[data-id=market]'); await page.wait_for_function("state.res.act.includes('market')", timeout=30000); await page.wait_for_timeout(2200)
        await shot('s_home_market')
        await tapsel('.chip[data-id=market]'); await page.wait_for_timeout(1500)
        # 放大到松山：設施圖示
        mark('zoom')
        await ev("map.flyTo([25.0535,121.5565],15,{duration:2.2})"); await page.wait_for_timeout(3800); await shot('s_zoom')
        # 點捷運站：路線
        mark('transit')
        pt = await ev("(()=>{const m=data('mrt').find(f=>/南京三民/.test(f.name))||data('mrt').find(f=>/小巨蛋/.test(f.name));map.panTo(m.center,{animate:true,duration:.8});return m.name})()")
        await page.wait_for_timeout(1100)
        xy = await ev("(()=>{const m=data('mrt').find(f=>f.name===%s);const q=map.latLngToContainerPoint(m.center);const r=map.getContainer().getBoundingClientRect();return [q.x+r.left,q.y+r.top]})()" % json.dumps(pt))
        await ev(f"__tap({xy[0]},{xy[1]})")
        await ev("(()=>{const m=data('mrt').find(f=>f.name===%s);void openPlace(m,LAYER.mrt)})()" % json.dumps(pt))
        await page.wait_for_function("state.transit&&state.transit.loading===false", timeout=90000); await page.wait_for_timeout(3000); await shot('s_transit')
        await tapsel('#close'); await page.wait_for_timeout(800)
        # 估價：點一間房
        mark('val')
        await ev("map.flyTo([25.0478,121.5502],16,{duration:1.2})"); await page.wait_for_timeout(1700)
        await ev("__tap(195,470)"); await ev("void openVal([25.0478,121.5502])"); await page.wait_for_function("!!document.getElementById('valGo')", timeout=30000); await page.wait_for_timeout(900)
        for k, v_ in (('ping', '30'), ('age', '32'), ('fl', '4'), ('tot', '5'), ('ask', '2680')):
            await page.click(f'#body [data-vf={k}]'); await page.fill(f'#body [data-vf={k}]', ''); await page.type(f'#body [data-vf={k}]', v_, delay=90)
        await tapsel('#body [data-vt="0"]'); await page.wait_for_timeout(400)
        await tapsel('#valGo'); await page.wait_for_function("!!document.getElementById('valSum')||!!document.getElementById('valNo')", timeout=8000); await page.wait_for_timeout(2600); await shot('s_val')
        await ev("setSheet('full')"); await page.wait_for_timeout(1500); await shot('s_val_full')
        await ev("document.querySelector('#body [data-vtab=fin]')&&document.querySelector('#body [data-vtab=fin]').click()"); await page.wait_for_timeout(2200); await shot('s_val_fin')
        await tapsel('#close'); await page.wait_for_timeout(700)
        # 單價熱圖
        mark('heat')
        await ev("map.flyTo([25.05,121.545],12,{duration:1.6})"); await page.wait_for_timeout(2000)
        await tapsel('#layersBtn'); await page.wait_for_selector('#layersPop [data-layer=priceheat]', state='visible'); await page.wait_for_timeout(600)
        await tapsel('#layersPop [data-layer=priceheat]'); await page.wait_for_function("!!window.phLayer||typeof phLayer!=='undefined'&&!!phLayer", timeout=20000); await page.wait_for_timeout(500)
        await tapsel('#layersBtn'); await page.wait_for_timeout(2600); await shot('s_heat')
        await tapsel('#layersBtn'); await tapsel('#layersPop [data-layer=priceheat]'); await tapsel('#layersBtn'); await page.wait_for_timeout(600)
        # 整體房市
        mark('market')
        await tapsel('#marketBtn'); await page.wait_for_function("state.view.type==='market'", timeout=30000); await page.wait_for_timeout(1500)
        await ev("setSheet('full')"); await page.wait_for_timeout(1600); await shot('s_market')
        await ev("document.querySelector('#mkFc').scrollIntoView({behavior:'smooth'})"); await page.wait_for_timeout(2400); await shot('s_market_fc')
        await page.wait_for_timeout(800)
        mark('end')
        info = await ev("({val:state.val?null:null,mk:window.MARKET?{light:MARKET.light&&MARKET.light.label,fc:MARKET.forecast.probs}:null})")
        print('INFO', json.dumps(info, ensure_ascii=False))
        await page.close(); v = await page.video.path(); await ctx.close(); await b.close()
        os.rename(v, f'{OUT}/raw.webm')
        json.dump(marks, open(f'{OUT}/marks.json', 'w'))
asyncio.run(main())
