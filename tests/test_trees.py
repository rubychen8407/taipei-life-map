"""行道樹（scripts/build_trees.py）單元測試"""
import json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts')); sys.path.insert(0, HERE)
import build_trees as T, trees_mock as M
fails = []
def check(ok, name):
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok: fails.append(name)
with tempfile.TemporaryDirectory() as d:
    out = T.build(M.mock(), out_dir=d, built='2026-11-16', min_trees=10, log=lambda *a: None)
    f = json.load(open(os.path.join(d, 'trees.json'), encoding='utf-8'))
    check(f['n'] == 66 and len(f['a']) == 66 * T.FIELDS and f['stats']['dup'] == 1 and f['stats']['nocoord'] == 1 and f['stats']['outside'] == 1, f"收 66 棵；重複、沒座標、市外各排除 1 筆 {f['stats']}")
    trees = [(f['base'][0] + f['a'][i] / 1e5, f['base'][1] + f['a'][i + 1] / 1e5, f['sp'][f['a'][i + 2]], f['rd'][f['a'][i + 3]], f['a'][i + 4] / 10, f['a'][i + 5]) for i in range(0, len(f['a']), T.FIELDS)]
    first = [t for t in trees if t[3] == '忠孝東路四段 南側人行道' and abs(t[1] - 121.538) < 1e-5]
    check(len(first) == 1 and abs(first[0][0] - 25.0415) < 2e-5 and first[0][2] == '臺灣欒樹' and first[0][4] == 8.5 and first[0][5] == 20, f'TWD97 轉回經緯度誤差 < 2 公尺，樹種、路段、樹高、胸徑正確 {first[:1]}')
    bad = [t for t in trees if abs(t[1] - 121.567) < 1e-5 and abs(t[0] - 25.033) < 1e-5]
    check(len(bad) == 1 and bad[0][4] == 0, '樹高超過 50 公尺（登錄錯誤）當作未知，不顯示')
    check(f['sp'][0] == '樟樹' and set(f['sp']) == {'樟樹', '臺灣欒樹', '大葉欖仁'}, f"樹種依數量排序（最常見的序號 0）{f['sp']}")
    try:
        T.build(M.mock(), out_dir=os.path.join(d, 'x'), log=lambda *a: None); ok = False
    except RuntimeError:
        ok = not os.path.exists(os.path.join(d, 'x', 'trees.json'))
    check(ok, '資料太少（下載不完整）：建置失敗、不寫出資料')
print(f"\n{'全部通過' if not fails else str(len(fails)) + ' 項失敗'}"); sys.exit(1 if fails else 0)
