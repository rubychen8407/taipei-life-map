# 暫時：WebKit（接近 iPhone Safari）＋使用者實際的裝置狀態（私人房源 3,731 間、舊版存在裝置的房源、地址快取），量打開與點按反應
import asyncio, os, sys, time, json, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
src=open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'e2e.py'),encoding='utf-8').read().replace('sys.exit(1 if asyncio.run(main()) else 0)','')
E={'__file__':os.path.abspath('tests/e2e.py'),'__name__':'x'}; exec(compile(src,'e2e.py','exec'),E)
from playwright.async_api import async_playwright
async def main():
    E['MODE'].update(baked=True, dir=os.path.join(E['ROOT'],'data'))
    random.seed(1); types=['公寓','華廈','大樓']
    rows=[[f'{k%5+1} 主塊','範圍內',f'測試路{k%300}巷{k}號',types[k%3],1500+k%2000,20+k%40,"'%d/%d"%(k%5+1,5+k%10),10+k%40,-5+k%10,'',f'https://www.sinyi.com.tw/buy/house/X{k}','','','','',round(25.02+random.random()*0.07,6),round(121.50+random.random()*0.09,6)] for k in range(3731)]
    E['GH']['files']['listings.json']=json.dumps({'updated':'2026-11-16T18:12:00Z','rows':[['區塊','位置判定','地址／路段','型態','開價(萬)','建坪','樓層','屋齡','開價 vs 實價(%)',"'591",'信義','永慶','樂屋','住商','備註','緯度','經度']]+rows},ensure_ascii=False)
    async with async_playwright() as p:
      for name in ('webkit',):
        b=await getattr(p,name).launch()
        ctx=await b.new_context(**E['PHONE'],service_workers='block'); page=await ctx.new_page()
        errs=[]; page.on('pageerror',lambda e:errs.append(str(e))); page.on('console',lambda m: m.type=='error' and errs.append(m.text))
        await ctx.route(lambda u: not u.startswith('blob:') and not u.startswith('data:'),E['route'])
        pend={}; page.on('request',lambda r: pend.__setitem__(r.url,r.method)); page.on('requestfinished',lambda r: pend.pop(r.url,None)); page.on('requestfailed',lambda r: errs.append('FAILED '+r.url[-80:]+' '+str(r.failure)))
        await page.goto(E['URL'])
        try: await page.wait_for_function(E['READY'],timeout=30000)
        except Exception as e:
            print('== NOT READY', name, errs[:10]); print('pending', list(pend.items())[:20]); print(await page.evaluate("JSON.stringify({res:!!(window.state&&state.res),loading:window.state&&state.loading,bar:document.getElementById('bar').style.display,head:document.getElementById('head').innerText.slice(0,200)})")); await b.close(); continue
        await page.evaluate("""localStorage.setItem('tlm.sync',JSON.stringify({repo:'rubychen8407/tlm-listings',token:'tok_ok'}));
          const old=[];for(let k=0;k<3731;k++)old.push({addr:'測試路'+(k%300)+'巷'+k+'號',lat:25.03+k*1e-5,lng:121.5+k*1e-5,sync:1,type:'公寓',price:2000,ping:30,links:{sinyi:'https://www.sinyi.com.tw/buy/house/X'+k}});
          localStorage.setItem('tlm.listings',JSON.stringify(old));const g={};for(let k=0;k<3000;k++)g['臺北市測試路'+k+'號']=[25.03,121.5];localStorage.setItem('tlm.geocache',JSON.stringify(g));""")
        for run in (1,2,3):
            await page.goto('about:blank'); T0=time.time()
            await page.goto(E['URL'],wait_until='commit')
            lat=[]
            for i in range(48):
                s=time.time()
                try: r=await page.evaluate("[!!document.querySelector('#head .sumline'),(window.LISTINGS||[]).length]")
                except Exception as e: r=[False,0]
                lat.append((round(time.time()-T0,1),round((time.time()-s)*1000),r)); await page.wait_for_timeout(250)
            print(f'== {name} run{run} ready at', next((t for t,_,r in lat if r[0]),None),'listings at', next((t for t,_,r in lat if r[1]>3000),None))
            print('  slow probes(ms>200)', [(t,ms) for t,ms,_ in lat if ms>200])
            s=time.time(); await page.click('.chip >> nth=2',timeout=10000); print('  tap chip', round((time.time()-s)*1000),'ms')
            s=time.time(); await page.mouse.click(195,330); await page.wait_for_timeout(30); await page.evaluate("1"); print('  tap map', round((time.time()-s)*1000),'ms')
            print('  ls KB', await page.evaluate("Object.keys(localStorage).map(k=>k+':'+Math.round(localStorage.getItem(k).length/1024)).join(' ')"))
        print('  errs', errs[:8]); await b.close()
asyncio.run(main())
