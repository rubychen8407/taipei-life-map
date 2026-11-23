# 暫時：桌面尺寸的地圖截圖（簡報背景用）
import asyncio, os
from playwright.async_api import async_playwright
URL = 'https://rubychen8407.github.io/taipei-life-map/'
OUT = 'demo'; os.makedirs(OUT, exist_ok=True)
READY = "typeof state!=='undefined'&&state.res&&!!document.querySelector('#head .sumline')"
HIDE = "#head,#sheet,.fab,#fabs,.leaflet-control-container,#bottomNav,nav,#toast,#layersPop{display:none!important}"
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={'width': 1920, 'height': 1080}, device_scale_factor=1.5, locale='zh-TW')
        page = await ctx.new_page(); ev = page.evaluate
        await page.goto(URL); await page.wait_for_function(READY, timeout=90000); await page.wait_for_timeout(1500)
        st = await page.add_style_tag(content=HIDE)
        async def snap(n):
            await page.wait_for_timeout(3000); await page.locator('#map').screenshot(path=f'{OUT}/w_{n}.png'); print('shot', n, flush=True)
        await ev("void map.setView([25.075,121.545],12,{animate:false})"); await snap('city')
        await ev("void map.setView([25.052,121.552],14,{animate:false})"); await snap('mid')
        await ev("void map.setView([25.0535,121.5565],16,{animate:false})"); await snap('street')
        await ev("el=>el.remove()", await st.element_handle()) if False else await page.evaluate("document.querySelectorAll('style').forEach(s=>{if(s.textContent.includes('#bottomNav,nav'))s.remove()})")
        await page.click('#layersBtn'); await page.wait_for_timeout(500); await page.click('#layersPop [data-layer=priceheat]'); await page.wait_for_function("typeof phLayer!=='undefined'&&!!phLayer", timeout=20000); await page.click('#layersBtn')
        await page.add_style_tag(content=HIDE)
        await ev("void map.setView([25.06,121.55],12,{animate:false})"); await snap('price')
        await ctx.close(); await b.close()
asyncio.run(main())
