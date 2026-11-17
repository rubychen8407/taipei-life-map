"""診斷：一個地址的估價怎麼來的（同巷全部成交＋app 的可比案例與分位數）。只印出，不寫檔。"""
import io, json, math, os, sys, tempfile, zipfile
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_realprice as R, build_data as B
LANE = os.environ.get('LANE', '光復南路421巷')
SUBJ = dict(addr=os.environ.get('ADDR', '臺北市信義區光復南路421巷6弄12號'), dist='信義區', t=2, ping=36.07, age=42, fl=2, tot=4)
today = date.today()
print('== 同巷所有買賣（近 4 年原始資料）')
seen = set()
for s in R.seasons(today, 16) + ['__now']:
    u = R.BASE + ('Download?type=zip&fileName=lvr_landcsv.zip' if s == '__now' else f'DownloadSeason?season={s}&type=zip&fileName=lvr_landcsv.zip')
    try: z = zipfile.ZipFile(io.BytesIO(R.http(u)))
    except Exception as e: print('skip', s, e); continue
    for r in R.read_csv(z, 'a_lvr_land_a.csv'):
        a = (r.get('土地位置建物門牌') or '').translate(R.FW)
        if LANE not in a or r.get('編號') in seen: continue
        seen.add(r.get('編號'))
        st = {}; kept = R.parse_sales([r], st)
        why = 'KEPT' if kept else ','.join(k for k in st if k != 'rows')
        area = R.num(r.get('建物移轉總面積平方公尺')) / R.PING; price = R.num(r.get('總價元')) / 1e4
        up_lvr = R.num(r.get('單價元平方公尺')) * R.PING / 1e4
        print(f"{r.get('交易年月日')} {a[-22:]:<22} {(r.get('建物型態') or '')[:4]} {r.get('移轉層次')}/{r.get('總樓層數')} 建完{r.get('建築完成年月')} {area:5.1f}坪 總{price:6.0f}萬 車{R.num(r.get('車位總價元'))/1e4:.0f} 網站單價{up_lvr:5.1f}萬/坪 → {why} {('ours %.1f' % kept[0]['up']) if kept else ''} | {(r.get('備註') or '')[:30]}")
print('\n== 跑完整建置，重算 app 的估價')
sales, rents, _ = R.fetch_all(today, log=lambda *a: None); addr = R.fetch_addresses(log=lambda *a: None)
layers = json.load(open(os.path.join(B.ROOT, 'data', 'city.json'), encoding='utf-8'))['layers']
cap = {}
orig_bt = R.backtest; R.backtest = lambda recs, **k: (cap.setdefault('recs', recs), orig_bt(recs, **k))[1]
with tempfile.TemporaryDirectory() as d:
    val = R.build(sales, rents, addr, layers, out_dir=d, today=today, log=lambda *a: None)
recs = cap['recs']; now = val['now_ym']
model = {'b': val['model']['b'], 'dist': val['model']['dist'], 'q': None}
pt, prec = addr.locate(SUBJ['addr']); poi = R.PoiIndex(layers)
sub = dict(SUBJ, pt=pt, s=poi.score(pt), ym=now)
print('定位', pt, '精度', prec, 'now', R.ym_str(now), '模型 now 效果', val['model']['now'])
comps, rad = R.comps_for(sub, recs, now)
base = math.exp(sum(b * v for b, v in zip(model['b'], R.base_x(sub))) + model['dist'].get('信義區', 0) + val['model']['now'])
print(f'模型基準單價 {base:.1f} 萬/坪；可比 {len(comps)} 筆、最遠 {rad:.0f} m')
res = sorted(r['e'] for r in comps)
for q in (.1, .25, .5, .75, .9): print(f'  分位 {q}: {base*math.exp(R.quantile(res, q)):.1f} 萬/坪 → ×{SUBJ["ping"]} 坪 = {base*math.exp(R.quantile(res, q))*SUBJ["ping"]:.0f} 萬')
sbar = sum(r['s'] for r in comps) / len(comps); print('位置分數 本案', round(sub['s'], 3), '可比平均', round(sbar, 3))
print('\n可比案例（相似度排序）：成交月 地址 距離 屋齡 樓層 坪 原始單價 → 換算到本案條件')
for r in sorted(comps, key=lambda r: R.similarity(sub, r, R.dist_m(pt, r['pt']))):
    adj = base * math.exp(r['e'])
    print(f"  {R.ym_str(r['ym'])} {r['addr'][-20:]:<20} {R.dist_m(pt, r['pt']):4.0f}m {r['age']:4.0f}年 {r['fl']}/{r['tot']} {r['ping']:5.1f} {r['up']:5.1f} → {adj:5.1f}")
print('\n== 時間效果（季別係數，越大越貴）')
fitm = R.fit([r for r in recs]); ks = sorted(fitm['q'], key=int)
print([(R.ym_str(int(k)*3), round(math.exp(fitm['q'][k]), 3)) for k in ks])
