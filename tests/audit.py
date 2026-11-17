# UI 稽核（ui-ux-pro-max 規則）：沿用 e2e 的模擬資料與路由，量各畫面的觸控尺寸、字級、對比、無名稱的圖示按鈕。
# 用法：python3 tests/audit.py  → 結果寫到 tests/out/audit.json，截圖 tests/out/audit_*.png
import os, json, asyncio, sys
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE); sys.path.insert(0, HERE)
src = open('e2e.py', encoding='utf-8').read().replace('sys.exit(1 if asyncio.run(main()) else 0)', '')
G = {'__name__': 'e2e_audit', '__file__': os.path.join(HERE, 'e2e.py')}
exec(compile(src, 'e2e.py', 'exec'), G)

MEASURE = r"""
(()=>{const lum=c=>{const m=c.match(/[\d.]+/g);if(!m)return null;const a=m.length>3?+m[3]:1;const [r,g,b]=m.slice(0,3).map(v=>{v/=255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)});return {L:.2126*r+.7152*g+.0722*b,a}};
 const bgOf=el=>{for(let e=el;e;e=e.parentElement){const s=getComputedStyle(e),c=s.backgroundColor;const l=lum(c);if(l&&l.a>.5)return l.L;}return 1};
 const vis=e=>{const r=e.getBoundingClientRect();if(r.width<1||r.height<1||r.bottom<0||r.top>innerHeight||r.right<0||r.left>innerWidth)return false;const s=getComputedStyle(e);return s.visibility!=='hidden'&&s.display!=='none'&&+s.opacity>0.05};
 const name=e=>(e.getAttribute('aria-label')||e.innerText||e.title||(e.querySelector('input')&&e.innerText)||'').trim();
 const lbl=e=>{const t=(e.getAttribute('aria-label')||e.innerText||e.id||e.className||e.tagName).toString().replace(/\s+/g,' ').trim();return t.slice(0,24)};
 const small=[],noname=[],tiny=[],lowc=[],inputs=[];
 for(const e of document.querySelectorAll('button,a[href],input:not([type=hidden]),select,textarea,label,[role=button],[data-d],[data-z]')){
   if(!vis(e)||e.closest('.leaflet-control-attribution'))continue;if(e.tagName==='INPUT'&&e.type==='file')continue;
   const r0=e.getBoundingClientRect(),af=getComputedStyle(e,'::after'),ex=k=>af.content&&af.content!=='none'&&af.position==='absolute'?Math.max(0,-parseFloat(af[k])||0):0,r={width:r0.width+ex('left')+ex('right'),height:r0.height+ex('top')+ex('bottom')};if(Math.min(r.width,r.height)<44)small.push([lbl(e),Math.round(r.width),Math.round(r.height)]);
   if(e.tagName==='BUTTON'&&!name(e))noname.push(lbl(e));
   if(/INPUT|SELECT|TEXTAREA/.test(e.tagName)){const f=parseFloat(getComputedStyle(e).fontSize);if(f<16)inputs.push([lbl(e)||e.placeholder,f]);}}
 const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let n;const seen=new Set();
 while(n=walker.nextNode()){const t=n.textContent.trim();if(!t)continue;const e=n.parentElement;if(!e||seen.has(e)||!vis(e)||e.closest('.leaflet-control-attribution,.leaflet-tile-pane,svg'))continue;seen.add(e);
   const s=getComputedStyle(e),f=parseFloat(s.fontSize),w=+s.fontWeight;if(f<12)tiny.push([t.slice(0,18),f]);
   const fg=lum(s.color);if(!fg)continue;const bl=bgOf(e),fl=fg.L*fg.a+bl*(1-fg.a);const cr=(Math.max(fl,bl)+.05)/(Math.min(fl,bl)+.05);const big=f>=24||(f>=18.66&&w>=700);
   if(cr<(big?3:4.5))lowc.push([t.slice(0,18),Math.round(cr*10)/10,f]);}
 const uniq=a=>{const m=new Map();for(const x of a)m.set(x[0],x);return [...m.values()]};
 return {small:uniq(small),noname:[...new Set(noname)],tiny:uniq(tiny),lowc:uniq(lowc),inputs:uniq(inputs),hscroll:document.documentElement.scrollWidth>innerWidth+1};})()
"""

async def audit(b, errs):
    page = await G['new_page'](b, errs, **G['PHONE'], geolocation={'latitude': 25.0335, 'longitude': 121.5436}, permissions=['geolocation'])
    ev = page.evaluate
    await page.goto(G['URL']); await page.wait_for_function(G['READY'], timeout=20000); await page.wait_for_timeout(600)
    res = {}
    async def grab(name):
        await page.wait_for_timeout(500)
        res[name] = await ev(MEASURE)
        await page.screenshot(path=os.path.join(G['OUT'], 'audit_' + name + '.png'))
    await grab('home')
    await page.click('#listBtn'); await grab('list')
    await ev("closeDetail();openCell(state.zones[0].best,{title:'測試位置'})"); await grab('cell')
    await ev("closeDetail()"); await page.click('#layersBtn'); await grab('layers'); await ev("closePops()")
    await page.click('#regionBtn'); await grab('region'); await ev("closePops()")
    await page.click('#savedBtn'); await grab('saved'); await ev("closePops()")
    await ev("closeDetail()"); await page.click('#exportBtn'); await grab('hunt')
    await ev("closeDetail();openSettings()"); await grab('settings')
    await ev("closeDetail()"); await page.click('#marketBtn'); await page.wait_for_timeout(800); await grab('market')
    await ev("closeDetail()"); await page.click('#homesBtn'); await grab('homes'); await ev("closePops()")
    try:
        await ev("closeDetail();openVal([25.030,121.543])"); await page.wait_for_timeout(1200); await grab('val')
    except Exception as e:
        res['val_err'] = str(e)
    css = await ev(r"""(()=>{const t=[...document.querySelectorAll('style')].map(s=>s.textContent).join('\n');
      const hex=(t.match(/#[0-9a-fA-F]{3,8}\b/g)||[]).length,vars=(t.match(/var\(--/g)||[]).length,
      fs=[...new Set((t.match(/font-size:\s*[\d.]+px/g)||[]).map(x=>parseFloat(x.split(':')[1])))].sort((a,b)=>a-b),
      rad=[...new Set((t.match(/border-radius:\s*[\d.]+px/g)||[]).map(x=>parseFloat(x.split(':')[1])))].sort((a,b)=>a-b),
      sh=new Set(t.match(/box-shadow:[^;}]+/g)||[]).size,dur=[...new Set(t.match(/\b\d*\.?\d+m?s\b(?=[^;{}]*(?:ease|linear|cubic|;|}))/g)||[])];
      return {hex,vars,distinctHex:new Set((t.match(/#[0-9a-fA-F]{3,8}\b/g)||[]).map(x=>x.toLowerCase())).size,fontSizes:fs,radii:rad,shadows:sh,
        reducedMotion:/prefers-reduced-motion/.test(t),focusVisible:/:focus-visible|:focus\b/.test(t),emoji:/[\u{1F300}-\u{1FAFF}☀-➿]/u.test(document.body.innerText),
        viewport:document.querySelector('meta[name=viewport]').content}})()""")
    res['css'] = css
    json.dump(res, open(os.path.join(G['OUT'], 'audit.json'), 'w'), ensure_ascii=False, indent=1)
    print('audit written')

async def noop(b, errs): pass
G['baked_suite'] = audit
G['live_suite'] = noop
asyncio.run(G['main']())
