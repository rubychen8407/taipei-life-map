"""土地價值（scripts/build_land.py）單元測試"""
import json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts')); sys.path.insert(0, HERE)
import build_land as G, land_mock as M
fails = []
def check(ok, name):
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok: fails.append(name)
shp, dbf = M.shp_dbf()
secs = G.read_shp(shp, dbf)
check(len(secs) == 6 and secs[0][0]['段代碼'] == '0101' and secs[0][0]['地段名'] == '仁愛段一小段' and secs[0][0]['行政區'] == '大安區' and len(secs[0][1][0]) == 5, '讀 SHP/DBF：小段代碼、名稱、行政區與範圍（TWD97）')
rates = G.read_rates(M.rates_csv())
check(rates['大安區'] == {113: 10.0, 114: 0.0, 115: -1.0}, '讀各區每年公告現值漲跌幅')
vals = G.read_values(M.values_csv())
check(len(vals[('大安區', '仁愛段一小段')]) == 6, '讀地號公告現值（行政區前面的空白要去掉）')
with tempfile.TemporaryDirectory() as d:
    out = G.build(secs, vals, 113, rates, G.read_public(M.public_csv()), out_dir=d, built='2026-11-16', min_sections=3, log=lambda *a: None)
    f = json.load(open(os.path.join(d, 'land.json'), encoding='utf-8'))
    S = {s['c']: s for s in f['sections']}
    check(f['year'] == 115 and f['base_year'] == 113 and set(S) == {'0101', '0102', '0201', '0301'} and f['stats']['none'] == 1, f"輸出 4 個小段，沒有資料的小段略過（{f['stats']}）")
    check(abs(S['0101']['v'] - 805000 * 1.0 * 0.99) < 1 and S['0101']['b'] == 'est' and S['0101']['k'] == 6 and S['0101']['g'] == -1.0, f"取中位數，再乘上之後每年的漲跌幅（{S['0101']['v']}）")
    check(len(S['0101']['r']) == 2, '同一小段分成兩筆圖形：合併成一個小段、兩個範圍')
    check(S['0301']['b'] == 'pub' and S['0301']['v'] == 62000, '地號資料不足 5 筆：改用公有土地當期公告現值中位數')
    la, lo = S['0201']['r'][0][0]
    check(abs(la - 25.040) < 2e-5 and abs(lo - 121.570) < 2e-5, f'範圍從 TWD97 轉回經緯度（{la},{lo}）')
    try:
        G.build(secs, vals, 113, rates, {}, out_dir=os.path.join(d, 'x'), log=lambda *a: None); ok = False
    except RuntimeError:
        ok = not os.path.exists(os.path.join(d, 'x', 'land.json'))
    check(ok, '小段太少（下載不完整）：建置失敗、不寫出資料')
print(f"\n{'全部通過' if not fails else str(len(fails)) + ' 項失敗'}"); sys.exit(1 if fails else 0)
