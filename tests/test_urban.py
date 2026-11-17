"""都市更新地區（scripts/build_urban.py）單元測試"""
import json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts')); sys.path.insert(0, HERE)
import build_urban as U, urban_mock as M
fails = []
def check(ok, name):
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok: fails.append(name)
check([U.kind_of(x) for x in ['本府89年06月26日公告劃定', '依都市更新條例第十一條劃定', '震災及高氯離子劃定', ' ']] == ['gov', 'self', 'hazard', 'other'], '依劃定說明分類：政府劃定、自行劃定更新單元、震災／海砂屋、其他')
r = U.rings_of({'type': 'Polygon', 'coordinates': [M.sq(25.04, 121.543)]})
check(len(r) == 1 and r[0][0] == [25.04, 121.543] and len(r[0]) == 5, '座標轉成 [緯度, 經度]、保留 5 位小數')
check(abs(U.area_ha(r) - 0.71) < 0.03, f'面積約 0.7 公頃（80×89 公尺）（{U.area_ha(r)}）')
with tempfile.TemporaryDirectory() as d:
    out = U.build(M.mock(), out_dir=d, built='2026-11-17', min_areas=3, log=lambda *a: None)
    f = json.load(open(os.path.join(d, 'urban.json'), encoding='utf-8'))
    check(len(f['areas']) == 6 and f['count'] == {'gov': 3, 'self': 1, 'hazard': 1, 'other': 1}, f"輸出 6 處（沒有範圍的略過）與各類數量 {f['count']}")
    a0 = f['areas'][0]
    check(a0['d'] == '1990-06-26' or a0['d'].startswith('1990-06'), f"公告日期轉成日期 {a0['d']}")
    check(len(f['areas'][5]['r']) == 3, '多塊範圍與洞都保留')
    try:
        U.build(M.mock(), out_dir=d, min_areas=100, log=lambda *a: None); check(False, '太少時不更新')
    except SystemExit:
        check(True, '來源筆數太少時建置失敗、不覆蓋舊資料')
print(f'\n{len(fails)} 項失敗：' + '、'.join(fails) if fails else '\n全部通過')
sys.exit(1 if fails else 0)
