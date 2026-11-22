import asyncio, time, json
from playwright.async_api import async_playwright
URL='https://rubychen8407.github.io/taipei-life-map/'
async def main():
    async with async_playwright() as p:
        b=await p.webkit.launch()
        for net in ('fast',):
          ctx=await b.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,device_scale_factor=3,user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1')
          page=await ctx.new_page(); errs=[]
          page.on('pageerror',lambda e:errs.append('PE '+str(e))); page.on('console',lambda m: m.type in('error','warning') and errs.append(m.type+' '+m.text))
          reqs=[]; page.on('requestfinished',lambda r: reqs.append((round(time.time()-T0,2),r.url[-60:])))
          await page.add_init_script("window.__lt=[];try{new PerformanceObserver(l=>{for(const e of l.getEntries())window.__lt.push([Math.round(e.startTime),Math.round(e.duration)]);}).observe({type:'longtask',buffered:true});}catch(e){}")
          for run in (1,2):
            T0=time.time(); reqs.clear()
            await page.goto(URL,wait_until='commit')
            lat=[]
            for i in range(80):
                s=time.time()
                try: r=await page.evaluate("[!!document.querySelector('#head .sumline'),document.getElementById('bar')&&document.getElementById('bar').style.display]")
                except Exception as e: r=[False,str(e)[:40]]
                lat.append((round(time.time()-T0,1),round((time.time()-s)*1000),r)); await page.wait_for_timeout(250)
            print(f'== {net} run{run} ready at', next((t for t,_,r in lat if r[0]),None), 'bar off at', next((t for t,_,r in lat if r[0] and r[1]=='none'),None))
            print(' slow probes', [(t,ms) for t,ms,_ in lat if ms>250])
            lt=await page.evaluate("__lt"); print(' long tasks>150', [x for x in lt if x[1]>150], 'total', sum(d for _,d in lt))
            print(' reqs', len(reqs))
            for sel in ['#settingsBtn','.chip >> nth=0','.tab >> nth=1']:
                pass
            # tap test: click a chip, measure response
            s=time.time()
            try:
                await page.click('.chip >> nth=2',timeout=5000); await page.wait_for_timeout(50)
                print(' tap ok', round((time.time()-s)*1000))
            except Exception as e: print(' tap fail', str(e)[:200])
            await page.mouse.click(195,420); await page.wait_for_timeout(1500)
            st=await page.evaluate("JSON.stringify({view:state.view&&state.view.type,mapZoom:map.getZoom()})"); print(' after map tap', st)
            print(' cloud code', await page.evaluate("typeof cloudPull"), 'sw', await page.evaluate("!!navigator.serviceWorker&&!!navigator.serviceWorker.controller"))
          print(' errs', errs[:15]); await ctx.close()
        await b.close()
asyncio.run(main())
