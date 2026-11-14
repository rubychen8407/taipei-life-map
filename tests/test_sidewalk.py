"""人行道建置（scripts/build_sidewalk.py）的單元測試：用內政部格式的模擬資料。"""
import io, json, os, sys, tempfile, zipfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts')); sys.path.insert(0, HERE)
import build_sidewalk as S
from sidewalk_mock import mock, feature, strip

fails = []
def check(ok, name):
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok: fails.append(name)

meta = {'result': {'distribution': [
    {'resourceDescription': 'SIDEWALK_202412_WGS84-1', 'resourceFormat': 'ZIP', 'resourceDownloadUrl': 'u1'},
    {'resourceDescription': 'SIDEWALK_202606', 'resourceFormat': 'ZIP', 'resourceDownloadUrl': 'u2'},
    {'resourceDescription': 'SideWalk_202312', 'resourceFormat': 'JSON', 'resourceDownloadUrl': 'u3'},
    {'resourceDescription': 'SIDEWALK_202512', 'resourceFormat': 'ZIP', 'resourceDownloadUrl': 'u4'}]}}
check(S.latest_zip(meta) == ('202606', 'u2'), '從檔案清單挑最新一期的全國 ZIP（SIDEWALK_202606）')

data = mock()
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w') as z:
    for city, fs in data.items():
        z.writestr(f'SIDEWALK_{city}_202606_WGS84.geojson', json.dumps({'type': 'FeatureCollection', 'features': fs}, ensure_ascii=False))
        z.writestr(f'SIDEWALK_{city}_202606_TWD97.geojson', '{"features":[]}')
    z.writestr('SIDEWALK_高雄市_202606_WGS84.geojson', '{"features":[]}')
r = S.read_zip(buf.getvalue())
check(set(r) == {'台北市', '新北市'} and len(r['台北市']) == 1600, '從 ZIP 只讀臺北市、新北市的 WGS84 檔')

with tempfile.TemporaryDirectory() as d:
    st = S.build(r, d, built='x', period='202606', log=lambda *a: None)
    j = json.load(open(os.path.join(d, 'sidewalk.json'), encoding='utf-8'))
    fs = j['features']
    expect = sum(1 for f in data['台北市'] if f['properties']['SWW_WTH'] >= 1.5) + 1
    check(st['kept'] == expect and len(fs) == expect, f'只保留淨寬 ≥ 1.5 公尺的人行道（{st["kept"]}／{st["total"]}）')
    check(any('板橋近邊界路' in f['n'] for f in fs) and not any('三峽遠方路' in f['n'] for f in fs), '新北市只收臺北市邊界外 1.4 公里內的路段')
    f0 = fs[0]
    check(set(f0) == {'id', 'n', 'c', 'p'} and f0['p'][0][0] == f0['p'][0][-1] and 24 < f0['c'][0] < 26 and 121 < f0['c'][1] < 122, '輸出和設施資料同格式：[緯度,經度]、封閉的面、有中心點')
    check('淨寬' in f0['n'] and '公尺' in f0['n'], '名稱帶路名與淨寬（點選時看得到）')
    check(j['src'] == 'moi-sidewalk' and j['period'] == '202606' and j['min_net'] == 1.5, '記錄資料來源、期別與門檻')
    try:
        S.build({'台北市': data['台北市'][:20]}, d, log=lambda *a: None); ok = False
    except RuntimeError:
        ok = True
    check(ok and json.load(open(os.path.join(d, 'sidewalk.json')))['period'] == '202606', '資料量異常少時拒絕覆蓋既有檔案')

print('\n全部通過' if not fails else f'\n失敗 {len(fails)} 項：{fails}')
sys.exit(1 if fails else 0)
