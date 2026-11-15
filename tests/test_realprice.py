"""實價登錄建置與估價（scripts/build_realprice.py）的單元測試：用實價登錄格式的模擬資料。"""
import io, json, math, os, sys, tempfile, zipfile
from datetime import date
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts')); sys.path.insert(0, HERE)
import build_realprice as R
import realprice_mock as M

fails = []
def check(ok, name):
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok: fails.append(name)

# --- 小工具 ---
la, lo = R.twd97(306847.96600313165, 2772208.373611303)
check(abs(la - 25.05699) < 1e-4 and abs(lo - 121.56344) < 1e-4, f'TWD97 → WGS84（松山區三民路 91 號：{la:.5f}, {lo:.5f}）')
check(R.norm_addr('臺北市大同區昌吉街６１巷９弄１９號四樓') == ('昌吉街', '61巷', '9弄', '19號') and R.norm_addr('台北市大安區忠孝東路四段２１６巷２７弄１６之１號') == ('忠孝東路4段', '216巷', '27弄', '16之1號'), '門牌正規化：全形數字、國字段數、樓層、之號')
check(R.floor_of('四層') == 4 and R.floor_of('十二層，陽台') == 12 and R.floor_of('一層，騎樓') is None and R.floor_of('地下一層') is None and R.floor_of('三層，四層') is None, '移轉層次：只接受單一地上樓層')
check(R.seasons(date(2026, 11, 15), 3) == ['115S1', '115S2', '115S3'] and R.seasons(date(2026, 1, 5), 2) == ['114S3', '114S4'], '季別代碼：只取已發布的季')
check(abs(R.quantile([1, 2, 3, 4], .5) - 2.5) < 1e-9 and R.quantile([5], .9) == 5, '分位數線性內插')

idx = R.AddressIndex().load_csv(io.StringIO('省市縣市代碼,鄉鎮市區代碼,村里,鄰,街路段,地區,巷,弄,號,橫座標,縱座標\n63000,63000010,三民里,002,三民路,,,,９１號,306847.966,2772208.373\n63000,63000010,三民里,002,三民路,,,,９１號二樓,306847.966,2772208.373\n63000,63000010,三民里,002,三民路,,,,９５號,306847.14,2772217.11\n63000,63000010,三民里,002,忠孝東路四段,,２１６巷,２７弄,１６號,306000,2770000\n'))
check(idx.locate('臺北市松山區三民路９１號五樓')[1] == 0 and idx.locate('臺北市大安區忠孝東路四段２１６巷２７弄１６之１號')[1] == 1 and idx.locate('臺北市松山區三民路９３號')[1] == 2 and idx.locate('臺北市松山區不存在路１號') == (None, None), '門牌定位：完整門牌 → 同號（去掉之）→ 同巷弄鄰近門牌；找不到就放棄')

# --- 清理 ---
sales, rents, addr, truth = M.mock()
z = zipfile.ZipFile(io.BytesIO(M.to_zip(sales, rents)))
rows = R.read_csv(z, 'a_lvr_land_a.csv')
check(len(rows) == len(sales) and rows[0]['鄉鎮市區'] in M.DIST, '讀 ZIP 裡的臺北市買賣檔，跳過第 2 列英文欄名')
st = {}; clean = R.parse_sales(rows, st)
by = {r['id']: r for r in clean}
check(len(clean) == len(sales) and all(abs(by[k]['up'] - v) / v < 0.002 for k, v in truth.items()), '單價先扣掉車位價與車位面積再算（和模擬的真實單價相同）')
bad = [dict(sales[0], 備註='親友、員工、共有人或其他特殊關係間之交易；', 編號='X1'), dict(sales[0], 主要用途='商業用', 編號='X2'), dict(sales[0], 移轉層次='一層，騎樓', 編號='X3'),
       dict(sales[0], 交易標的='車位', 編號='X4'), dict(sales[0], 建物型態='店面(店鋪)', 編號='X5'), dict(sales[0], 交易標的='房地(土地+建物+車位)', 車位總價元='0', 車位移轉總面積平方公尺='30', 編號='X6'),
       dict(sales[0], 建築完成年月=sales[0]['交易年月日'], 編號='X7')]
st = {}; check(R.parse_sales(bad, st) == [] and st.get('special') == 1 and st.get('use') == 1 and st.get('parking_unsplit') == 1 and st.get('new') == 1, f'排除特殊關係、非住家、騎樓、純車位、店面、車位未拆價、新成屋 {st}')
rc = R.parse_rents(R.read_csv(z, 'a_lvr_land_c.csv'))
check(len(rc) == len(rents) and all(r['rent'] > 5000 for r in rc), '讀租賃檔：整戶出租的住宅')

# --- 建置、模型、回測 ---
with tempfile.TemporaryDirectory() as d:
    val = R.build(clean, rc, addr, M.layers(), out_dir=d, built='2026-11-15', log=lambda *a: None, min_sales=1000)
    rp = json.load(open(os.path.join(d, 'realprice.json'), encoding='utf-8'))
    check(val['through'] == '2026-10' and len(rp['sales']) > 1500 and len(rp['sales'][0]) == len(rp['cols']) and all(val['now_ym'] - s[2] < 24 for s in rp['sales']), f"輸出近 24 個月成交（{len(rp['sales'])} 筆）與欄位表")
    check(len(rp['rents']) > 100 and all(val['now_ym'] - r[2] < 12 for r in rp['rents']), '輸出近 12 個月租賃')
    b = dict(zip(val['feats'], val['model']['b']))
    check(abs(b['age'] + b['age2'] * 2 * 25 / 100 + 0.008) < 0.004 and abs(b['type_apt'] + b['apt_high'] / 2 - math.log(.85)) < 0.06, f"模型學到模擬的規律：屋齡每年約 −0.8%（{b['age']:.4f}）、公寓約 −15%")
    dd = val['model']['dist']; check(abs((dd['大安區'] - dd['文山區']) - math.log(100 / 60)) < 0.05, '行政區價差與模擬一致（大安比文山高約 67%）')
    bt = val['backtest']
    check(bt['n'] > 200 and bt['mdape'] < 0.07 and 0.6 < bt['hit_10_90'] < 0.95, f"回測：只用更早的資料估最近 3 個月（MdAPE {bt['mdape']:.1%}、P10–P90 命中 {bt['hit_10_90']:.0%}）")
    mk = val['market']
    check('全市' in mk and len(mk['全市']['series']) == 24 and mk['大安區']['vol'] > 0 and mk['全市']['price_mom'] is not None and abs(mk['全市']['price_mom'] - 0.02) < 0.02, f"市場指標：交易量年增率、品質調整後價格動能（模擬每季 +1% → 兩季 {mk['全市']['price_mom']:+.1%}）")
    check(mk['大安區']['price_mom_local'] and abs(mk['大安區']['price_mom'] - 0.02) < 0.03 and not mk['全市']['price_mom_local'], f"各區價格動能：大安區 {mk['大安區']['price_mom']:+.1%}（模擬每季 +1%，各區相同）")
    seg = mk.get('大安區|2')
    check(seg and seg['vol'] > 0 and seg['vol'] < mk['大安區']['vol'] and len(seg['series']) == 24, f"市場指標也有「同區同型態」（大安區公寓 {seg and seg['vol']} 筆）")
    da = mk['大安區']; hist_ok = da['vol_avg'] > 0 and da['vol_z'] is not None and abs(da['vol_z']) < 3
    check(hist_ok, f"交易量和自己過去三年比：大安區近 3 個月 {da['vol']} 筆、過去平均 {da['vol_avg']} 筆、z = {da['vol_z']}（模擬資料各月均勻，不該偏離太多）")
    try:
        R.build(clean, rc, addr, M.layers(), out_dir=os.path.join(d, 'x'), log=lambda *a: None, min_sales=1000, max_mdape=0.001); ok = False
    except RuntimeError:
        ok = not os.path.exists(os.path.join(d, 'x', 'realprice.json'))
    check(ok, '回測誤差超過門檻：建置失敗、不寫出資料')
    try:
        R.build(clean[:500], rc, addr, M.layers(), out_dir=os.path.join(d, 'y'), log=lambda *a: None); ok = False
    except RuntimeError:
        ok = True
    check(ok, '可用成交太少：建置失敗、不寫出資料')

# --- 估價 ---
recs = [r for r in clean if 'e' in r]
subj = {'dist': '大安區', 't': 0, 'age': 12.0, 'fl': 8, 'tot': 15, 'ping': 30.0, 'pt': (25.030, 121.543)}
subj['s'] = R.PoiIndex(M.layers()).score(subj['pt'])
m = {'b': val['model']['b'], 'dist': val['model']['dist'], 'q': {'0': val['model']['now']}}
est = R.estimate(subj, m, recs, val['now_ym'], val['model']['now'])
tu = M.true_unit('大安區', 0, 12.0, 8, 15, 30.0, val['now_ym'])
check(est['ok'] and est['n'] >= 20 and abs(est['q'][0.5] - tu) / tu < 0.05 and est['q'][0.1] < est['q'][0.5] < est['q'][0.9], f"估價：大安區 12 年大樓中間值 {est['q'][0.5]:.1f} 萬／坪（模擬真實值 {tu:.1f}）")
check(est['n'] == 25 and est['radius'] <= 1200 and abs(est['poi']) <= 0.08, f"可比案例取最相似的 {est['n']} 筆（最遠 {est['radius']} 公尺）；位置加減分在 ±8% 內")
cs, _ = R.comps_for(subj, recs, val['now_ym'])
check(all(c['t'] == 0 and abs(c['age'] - 12) <= 12 and 15 <= c['ping'] <= 60 for c in cs), '可比案例都是同型態、屋齡 ±12 年、坪數 0.5～2 倍')
sims = [R.similarity(subj, c, R.dist_m(subj['pt'], c['pt'])) for c in cs]
check(sims == sorted(sims), '依相似度排序（距離、屋齡、坪數、樓層）')
apt = {'dist': '大安區', 't': 2, 'age': 40.0, 'fl': 4, 'tot': 5, 'ping': 25.0, 'pt': (25.030, 121.543), 's': subj['s']}
ca, _ = R.comps_for(apt, recs, val['now_ym'])
check(ca and all(c['t'] == 2 for c in ca) and sum(c['fl'] >= 3 for c in ca) >= len(ca) * 0.6, f"無電梯公寓 4 樓：可比案例都是公寓，且大多是 3 樓以上（{sum(c['fl'] >= 3 for c in ca)}/{len(ca)}）")
check(R.floor_pen(apt, dict(apt, fl=1)) > R.floor_pen(apt, dict(apt, fl=3)) and R.floor_pen(subj, dict(subj, fl=1)) > R.floor_pen(subj, dict(subj, fl=9)), '樓層差異：一樓和非一樓差最多')
far = dict(subj, pt=(25.2, 121.9)); far['s'] = 0
check(not R.estimate(far, m, recs, val['now_ym'], val['model']['now'])['ok'], '附近 1 公里內案例不足：不給估價')

print(f"\n{'全部通過' if not fails else str(len(fails)) + ' 項失敗：' + '；'.join(fails)}")
sys.exit(1 if fails else 0)
