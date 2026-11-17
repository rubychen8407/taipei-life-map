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
R.now_effect = now_full
oc, oe = R.comps_for, R.estimate
def wq(xs, ws, q):
    z = sorted(zip(xs, ws)); tot = sum(ws); acc = 0
    for x, w in z:
        acc += w
        if acc >= q * tot: return x
    return z[-1][0]
def make(mode, near=500, nmin=10, scale=400):
    def comps_for(subject, recs, now_ym):
        c = []
        for r in recs:
            if r is subject or r['t'] != subject['t'] or abs(r['age'] - subject['age']) > R.AGE_BAND or now_ym - r['ym'] >= R.KEEP_MONTHS: continue
            if not R.SIZE_BAND[0] <= r['ping'] / subject['ping'] <= R.SIZE_BAND[1]: continue
            d = R.dist_m(subject['pt'], r['pt'])
            if d <= R.RADII[-1]: c.append((R.similarity(subject, r, d), d, r))
        c.sort(key=lambda x: x[0])
        if mode == 'near':
            nn = [x for x in c if x[1] <= near]
            if len(nn) >= nmin: c = nn
        c = c[:25]
        subject['_d'] = {id(r): d for _, d, r in c}
        return [r for _, _, r in c], (max(d for _, d, _ in c) if c else R.RADII[-1])
    def estimate(subject, model, recs, now_ym, tq=None):
        out = oe(subject, model, recs, now_ym, tq)
        if mode == 'wq' and out['q']:
            comps, _ = comps_for(subject, recs, now_ym); ds = subject['_d']
            res = [r['e'] for r in comps]; ws = [1 / (1 + (ds[id(r)] / scale) ** 2) for r in comps]
            for q in out['q']: out['q'][q] = out['base'] * math.exp(wq(res, ws, q))
        return out
    return comps_for, estimate
for name, kw in [('base', dict(mode='base')), ('near500', dict(mode='near', near=500)), ('near300', dict(mode='near', near=300, nmin=8)), ('wq400', dict(mode='wq', scale=400)), ('wq250', dict(mode='wq', scale=250))]:
    R.comps_for, R.estimate = make(**kw)
    bt = R.backtest(recs); m = R.fit(recs)
    for r in recs: r['e'] = math.log(r['up']) - R.predict_ln(m, r)
    est = R.estimate(sub, m, recs, now, R.now_effect(m))
    print(f"exp={name:8}: MdAPE {bt['mdape']:.4f} p80 {bt['p80_ape']:.4f} hit50 {bt['hit_25_75']:.3f} hit80 {bt['hit_10_90']:.3f} noest {bt['no_estimate']} | 本案 中位 {est['q'][0.5]*36.07:.0f} 萬 帶 {est['q'][0.25]*36.07:.0f}–{est['q'][0.75]*36.07:.0f}", flush=True)
