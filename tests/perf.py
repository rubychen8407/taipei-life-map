"""效能量測：用 repo 內真實 data/，模擬手機（CPU 降速 4 倍），量載入與常用操作時主執行緒被卡住的時間。
用法：python3 tests/perf.py [--profile]   （--profile 會列出最花時間的函式）"""
import asyncio, os, sys, time, json, collections
sys.argv_saved = sys.argv; sys.argv = [sys.argv[0]]
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import importlib.util
spec = importlib.util.spec_from_file_location('e2e_mod', os.path.join(HERE, 'e2e.py'))
src = open(os.path.join(HERE, 'e2e.py'), encoding='utf-8').read().replace('sys.exit(1 if asyncio.run(main()) else 0)', '')
E = importlib.util.module_from_spec(spec); exec(compile(src, 'e2e.py', 'exec'), E.__dict__)
from playwright.async_api import async_playwright
import shutil

LT = """window.__lt=[];new PerformanceObserver(l=>{for(const e of l.getEntries())window.__lt.push([e.startTime,e.duration]);}).observe({type:'longtask',buffered:true});"""


async def step(page, name, fn, res):
    await page.evaluate("window.__lt&&(window.__lt.length=0)")
    t0 = time.time(); await fn(); wall = time.time() - t0
    await page.wait_for_timeout(300)
    lt = await page.evaluate("window.__lt||[]")
    tot = sum(d for _, d in lt); mx = max([d for _, d in lt] or [0])
    res.append((name, wall, tot, mx, len(lt)))
    print(f'{name:28s} wall {wall:5.2f}s  long-task total {tot:6.0f}ms  max {mx:5.0f}ms  n={len(lt)}')


async def main():
    prof = '--profile' in sys.argv_saved
    E.MODE.update(baked=True, dir=os.path.join(E.ROOT, 'data'))
    errs, res = [], []
    async with async_playwright() as p:
        exe = shutil.which('google-chrome') or shutil.which('chromium')
        b = await (p.chromium.launch(executable_path=exe, args=['--no-sandbox']) if exe else p.chromium.launch())
        ctx = await b.new_context(**E.PHONE)
        page = await ctx.new_page(); page.on('pageerror', lambda e: errs.append(str(e)))
        await page.route('**/*', E.route); await page.add_init_script(LT)
        cdp = await ctx.new_cdp_session(page)
        await cdp.send('Emulation.setCPUThrottlingRate', {'rate': 4})
        if prof:
            await cdp.send('Profiler.enable'); await cdp.send('Profiler.setSamplingInterval', {'interval': 200}); await cdp.send('Profiler.start')
        hits0 = len(E.data_hits)

        async def load():
            await page.goto(E.URL); await page.wait_for_function(E.READY, timeout=120000)
        await page.goto("about:blank")
        await step(page, "載入到可操作", load, res)
        print('  啟動時下載：', E.data_hits[hits0:])
        await page.wait_for_timeout(1500)

        async def chip(i):
            await page.click(f'.chip[data-id={i}]'); await page.wait_for_function(f"state.res&&state.res.act.includes('{i}')", timeout=60000)
        for i in ['market', 'bus']:
            await step(page, f'加類別 {i}', lambda i=i: chip(i), res)

        async def slide():
            await page.click('.chip[data-id=park]'); await page.wait_for_timeout(200)
            await page.evaluate("""(async()=>{const s=document.querySelector('[data-distance]');if(!s)return;for(let m=10;m<=20;m++){s.value=m;s.dispatchEvent(new Event('input'));await new Promise(r=>setTimeout(r,16));}s.dispatchEvent(new Event('change'));})()""")
            await page.wait_for_timeout(200)
        await step(page, '拖距離滑桿 10→20 分', slide, res)

        async def pan():
            await page.evaluate("void map.closePopup()")
            for k in range(6):
                await page.mouse.move(200, 500); await page.mouse.down(); await page.mouse.move(120 + k * 5, 420, steps=8); await page.mouse.up()
        await step(page, '拖地圖 6 次', pan, res)

        async def zoom():
            for z in [14, 15, 16, 13]:
                await page.evaluate(f"void map.setZoom({z},{{animate:false}})"); await page.wait_for_timeout(150)
        await step(page, '縮放 4 次', zoom, res)

        async def sheet():
            await page.evaluate("void (closeDetail&&closeDetail())"); await page.click('#listBtn'); await page.wait_for_timeout(500)
        await step(page, '開結果清單', sheet, res)

        if prof:
            r = await cdp.send('Profiler.stop'); P = r['profile']
            nodes = {n['id']: n for n in P['nodes']}; cnt = collections.Counter(P['samples'])
            dt = P['timeDeltas']; per = collections.Counter()
            for sid, d in zip(P['samples'], dt):
                n = nodes[sid]['callFrame']; per[(n['functionName'] or '(anon)', n['url'].split('/')[-1][:20], n['lineNumber'] + 1)] += d
            print('\n最花時間的函式（self time，含 4 倍降速）：')
            for (fn, u, ln), us in per.most_common(25):
                print(f'  {us/1000:8.0f}ms  {fn}  {u}:{ln}')
        await b.close()
    print('JS errors:', errs[:3])
    json.dump(res, open(os.path.join(E.OUT, 'perf.json'), 'w'))

asyncio.run(main())
