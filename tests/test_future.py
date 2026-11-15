"""未來捷運（scripts/build_future.py）單元測試"""
import json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts')); sys.path.insert(0, HERE)
import build_future as F, future_mock as M
fails = []
def check(ok, name):
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok: fails.append(name)
check(F.line_name('捷運環狀線北環段 (興建中)') == '環狀線北環段' and F.line_name('捷運萬大-中和-樹林線') == '萬大線' and F.line_name('', 'SB12') == '民生汐止線' and F.line_name('台灣高速鐵路') is None, '不同寫法的路段歸到同一條線；高鐵不算')
check(F.kind_status({'railway': 'construction', 'construction': 'subway'}) == ('subway', 'construction') and F.kind_status({'railway': 'proposed', 'proposed': 'rail'}) is None and F.kind_status({'proposed:railway': 'light_rail'}) == ('light_rail', 'proposed'), '只收捷運、輕軌，分興建中／規劃中')
with tempfile.TemporaryDirectory() as d:
    out = F.build(M.mock(), out_dir=d, built='2026-11-15', log=lambda *a: None)
    f = json.load(open(os.path.join(d, 'future.json'), encoding='utf-8'))
    names = {L['name']: L for L in f['lines']}
    check(set(names) == {'環狀線北環段', '民生汐止線'} and names['環狀線北環段']['status'] == 'construction' and names['民生汐止線']['status'] == 'proposed' and len(names['環狀線北環段']['segs']) == 2, '輸出兩條線：環狀線北環段（興建中、兩段合併）、民生汐止線（規劃中）')
    st = {(s['line'], s['n']) for s in f['stations']}
    check(len(f['stations']) == 2 and ('環狀線北環段', 'Y20站') in st and ('民生汐止線', '民生社區') in st, f'車站歸到最近的線、重複標註只留一個、高鐵站排除 {st}')
    try:
        F.build([M.mock()[4]], out_dir=os.path.join(d, 'x'), log=lambda *a: None); ok = False
    except RuntimeError:
        ok = not os.path.exists(os.path.join(d, 'x', 'future.json'))
    check(ok, '找不到任何路線：建置失敗、不寫出資料')
print(f"\n{'全部通過' if not fails else str(len(fails)) + ' 項失敗'}"); sys.exit(1 if fails else 0)
