"""實驗：「現在」時間效果的算法 × 距離權重，看回測誤差與這個地址的估價。"""
import json, math, os, sys, tempfile, time
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_realprice as R, build_data as B
today = date.today()
sales, rents, _ = R.fetch_all(today, log=lambda *a: None); addr = R.fetch_addresses(log=lambda *a: None)
layers = json.load(open(os.path.join(B.ROOT, 'data', 'city.json'), encoding='utf-8'))['layers']
cap = {}; ob = R.backtest; R.backtest = lambda recs, **k: (cap.setdefault('recs', recs), {'mdape': 0.05})[1]
with tempfile.TemporaryDirectory() as d:
    val = R.build(sales, rents, addr, layers, out_dir=d, today=today, log=lambda *a: None)
R.backtest = ob; recs = cap['recs']; now = val['now_ym']
of = R.fit
def fit2(rs):
    m = of(rs); n = {}
    for r in rs: n[str(R.quarter(r['ym']))] = n.get(str(R.quarter(r['ym'])), 0) + 1
    m['n'] = n; return m
R.fit = fit2
on = R.now_effect
def now_full(m):
    ks = sorted(m['q'], key=int); med = sorted(m['n'][k] for k in ks)[len(ks) // 2]
    ks = [k for k in ks if m['n'][k] >= 0.5 * med][-2:]
    return sum(m['q'][k] for k in ks) / len(ks)
pt, _ = addr.locate('臺北市信義區光復南路421巷6弄12號'); poi = R.PoiIndex(layers)
sub = dict(dist='信義區', t=2, ping=36.07, age=42, fl=2, tot=4, pt=pt, s=poi.score(pt), ym=now)
for nowv in ('avg2', 'full'):
    R.now_effect = on if nowv == 'avg2' else now_full
    for ds in (300, 200, 150, 100):
        R.similarity = (lambda ds: lambda s, r, d: d / ds + abs(r['age'] - s['age']) / 5 + abs(math.log(r['ping'] / s['ping'])) / 0.3 + R.floor_pen(s, r))(ds)
        t0 = time.time(); bt = R.backtest(recs)
        m = R.fit(recs)
        for r in recs: r['e'] = math.log(r['up']) - R.predict_ln(m, r)
        est = R.estimate(sub, m, recs, now, R.now_effect(m))
        comps, rad = R.comps_for(sub, recs, now)
        print(f"now={nowv:4} dist/{ds:3}: MdAPE {bt['mdape']:.4f} p80 {bt['p80_ape']:.4f} hit50 {bt['hit_25_75']:.3f} hit80 {bt['hit_10_90']:.3f} n {bt['n']} | 本案 中位 {est['q'][0.5]:.1f} 萬/坪 = {est['q'][0.5]*36.07:.0f} 萬 帶 {est['q'][0.25]*36.07:.0f}–{est['q'][0.75]*36.07:.0f} 最遠 {rad:.0f}m ({time.time()-t0:.0f}s)", flush=True)
