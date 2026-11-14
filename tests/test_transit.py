"""TDX 路線建置（scripts/build_transit.py）的單元測試：用 TDX 回傳格式的模擬資料。"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
import build_transit as T

fails = []
def check(ok, name):
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok: fails.append(name)

# ---- 模擬 TDX 回傳（欄位照 TDX v2 格式）----
def stop(i, lat, lon, seq):
    return {'StopUID': f'S{i}', 'StopName': {'Zh_tw': f'站{i}'}, 'StopSequence': seq, 'StopPosition': {'PositionLon': lon, 'PositionLat': lat}}
def wkt(pts): return 'LINESTRING(' + ', '.join(f'{lo} {la}' for la, lo in pts) + ')'
BUS_STOPS, BUS_SHAPE, BUS_SCH = [], [], []
for r in range(60):
    for d in (0, 1):
        base = (25.02 + r * 0.001, 121.50 + r * 0.001)
        pts = [(base[0] + k * 0.003, base[1] + k * 0.004) for k in range(8)]
        if d: pts = pts[::-1]
        uid = f'TPE{r}'
        BUS_STOPS.append({'RouteUID': uid, 'SubRouteUID': uid, 'Direction': d, 'RouteName': {'Zh_tw': f'{r}路'}, 'SubRouteName': {'Zh_tw': f'{r}路'},
                          'Stops': [stop(k, la, lo, k + 1) for k, (la, lo) in enumerate(pts)][::-1 if r % 2 else 1]})  # 亂序也要能排好
        BUS_SHAPE.append({'RouteUID': uid, 'SubRouteUID': uid, 'Direction': d, 'Geometry': wkt(pts)})
        if r % 3 == 0: BUS_SCH.append({'SubRouteUID': uid, 'Direction': d, 'Frequencys': [{'MinHeadwayMins': 6, 'MaxHeadwayMins': 10}, {'MinHeadwayMins': 10, 'MaxHeadwayMins': 14}]})
        elif r % 3 == 1: BUS_SCH.append({'SubRouteUID': uid, 'Direction': d, 'Timetables': [{'ServiceDay': {'Monday': 1}, 'StopTimes': [{'DepartureTime': '%02d:%02d' % (6 + 20 * j // 60, 20 * j % 60)}]} for j in range(48)] + [{'ServiceDay': {'Monday': 0, 'Sunday': 1}, 'StopTimes': [{'DepartureTime': '07:00'}]}]})
# 新北公車：站全部在城市外，應被濾掉
BUS_STOPS.append({'RouteUID': 'NTP1', 'SubRouteUID': 'NTP1', 'Direction': 0, 'RouteName': {'Zh_tw': '遠'}, 'Stops': [stop(1, 24.5, 121.0, 1), stop(2, 24.51, 121.01, 2)]})
METRO_ST = [{'StationID': f'BL{i}', 'StationName': {'Zh_tw': f'藍{i}'}, 'StationPosition': {'PositionLon': 121.50 + i * 0.01, 'PositionLat': 25.03}} for i in range(1, 9)]
METRO_ST += [{'StationID': f'R{i}', 'StationName': {'Zh_tw': f'紅{i}'}, 'StationPosition': {'PositionLon': 121.52, 'PositionLat': 25.00 + i * 0.01}} for i in range(1, 9)]
METRO_SOR = [{'LineNo': 'BL', 'LineID': 'BL', 'RouteID': 'BL-1', 'RouteName': {'Zh_tw': '板南線'}, 'Stations': [{'Sequence': i, 'StationID': f'BL{i}', 'StationName': {'Zh_tw': f'藍{i}'}} for i in range(1, 9)]},
             {'LineNo': 'R', 'LineID': 'R', 'RouteID': 'R-1', 'RouteName': {'Zh_tw': '淡水信義線'}, 'Stations': [{'Sequence': i, 'StationID': f'R{i}', 'StationName': {'Zh_tw': f'紅{i}'}} for i in range(1, 9)]}] * 2
METRO_SOR += [{'LineNo': k, 'LineID': k, 'RouteName': {'Zh_tw': k}, 'Stations': [{'Sequence': i, 'StationID': f'BL{i}', 'StationName': {'Zh_tw': f'藍{i}'}} for i in range(1, 4)]} for k in ('G', 'O')]
METRO_SHP = [{'LineID': 'BL', 'Geometry': wkt([(25.03, 121.50 + i * 0.01) for i in range(1, 9)])}, {'LineID': 'R', 'Geometry': wkt([(25.00 + i * 0.01, 121.52) for i in range(1, 9)])}]
METRO_FQ = [{'LineID': 'BL', 'Headways': [{'PeakFlag': '1', 'MinHeadwayMins': 3, 'MaxHeadwayMins': 5}, {'PeakFlag': '0', 'MinHeadwayMins': 5, 'MaxHeadwayMins': 7}]}]

class Fake(T.Tdx):
    def __init__(self, broken=()): self.broken = broken
    def get(self, path, tries=4):
        for b in self.broken:
            if b in path: raise RuntimeError(f'{path}: HTTP 500')
        for key, val in [('Bus/StopOfRoute/City/Taipei', BUS_STOPS), ('Bus/Shape/City/Taipei', BUS_SHAPE), ('Bus/Schedule/City/Taipei', BUS_SCH),
                         ('Bus/', []), ('Metro/StationOfRoute', METRO_SOR), ('Metro/Station/', METRO_ST), ('Metro/Shape', METRO_SHP), ('Metro/Frequency', METRO_FQ)]:
            if key in path: return val
        raise AssertionError(path)

quiet = lambda *a: None
box = T.city_box()
# WKT MultiLineString 與框選裁切必須保留不連續片段，不能把框外區段硬接起來。
multi = T.wkt_lines('MULTILINESTRING((121.0 25.0,121.1 25.0),(121.2 25.0,121.3 25.0))')
check(len(multi) == 2 and len(multi[0]) == 2 and len(multi[1]) == 2, 'WKT MultiLineString 保留獨立片段')
clipped = T.clip_polyline([(25.0,121.2),(25.0,121.5),(25.1,121.5),(25.1,121.2)], [24.9,121.1,25.1,121.3])
check(len(clipped) == 2 and all(len(seg) >= 2 for seg in clipped), '路線框選裁切不跨越框外空隙')
rs = T.build_bus(Fake(), box, quiet)
check(len(rs) == 120, f'公車：60 條路線 × 兩個方向 = 120（新北城外那條被濾掉）→ {len(rs)}')
r0 = rs[0]
check(r0['r'] == 'bus' and r0['ref'] and r0['to'] == '站7' and r0['from'] == '站0', '公車：站序排好（即使 API 順序相反），起訖站正確')
check(len(r0['line']) >= 2 and r0['line'][0][0] < 26, '公車：行駛軌跡從 WKT 轉成 [緯度,經度]')
ivs = sorted({r['iv'] for r in rs if r['iv']})
check(10 in ivs and 20 in ivs, f'公車：班距格式取平均（6–10、10–14→10 分）、時刻表取週一首站發車間隔中位數 20 分 → {ivs}')
few = {'SubRouteUID': 'x', 'Timetables': [{'ServiceDay': {'Monday': 1}, 'StopTimes': [{'DepartureTime': '06:00'}]}, {'ServiceDay': {'Monday': 1}, 'StopTimes': [{'DepartureTime': '17:00'}]}]}
check(T.bus_interval(few) is None, '公車：週一只有 1–2 班（尖峰專車）→ 不推算班距（iv=None），不會顯示成「960 分一班」')
check(any(r['iv'] is None for r in rs), '公車：沒有班表的路線 iv=None（app 畫成灰色虛線，不亂猜）')
check(len({r['id'] for r in rs}) == len(rs), '路線 id 不重複')
class MissingOneBusShape(Fake):
    def get(self, path, tries=4):
        if 'Bus/Shape/City/Taipei' in path:
            return BUS_SHAPE[2:]  # 同一路線往返兩個方向都缺少軌跡
        return super().get(path, tries)
missing_shape_routes = T.build_bus(MissingOneBusShape(), box, quiet)
check(len(missing_shape_routes) == 120 and all(r['line'] == [] for r in missing_shape_routes if r['ref'] == '0路') and sum(1 for r in missing_shape_routes if r['line']) == 118, '單一路線缺少幾何時不畫線（line 為空、仍列在清單），不用站點直線穿越建築物')
check(T.build_bus(Fake(broken=['Bus/Schedule']), box, quiet)[0]['iv'] is None, '班表 API 失敗不影響路線本身')
check(T.build_bus(Fake(broken=['Bus/Shape']), box, quiet) == [], '軌跡 API 失敗：該城市整批略過、不崩潰')
_keep = [dict(x) for x in BUS_SHAPE]
for x in BUS_SHAPE:
    x.pop('SubRouteUID')                      # 軌跡只有主路線編號（沒有子路線）
    if x['RouteUID'] == 'TPE5': x['Geometry'] = wkt([(25.20, 121.40), (25.21, 121.41)])   # 這條的軌跡根本不經過站牌
rs2 = T.build_bus(Fake(), box, quiet)
ok5 = [r for r in rs2 if r['ref'] == '5路']
check(len(rs2) == 120 and sum(1 for r in rs2 if len(r['line']) >= 2) == 118 and all(r['line'] == [] for r in ok5), f"軌跡只有主路線編號時仍配得到（118/120 有線）；軌跡不經過站牌的路線保留在清單但不畫線")
check(all(T.project(r['line'], r['stops'][0])[1] <= T.project(r['line'], r['stops'][-1])[1] for r in rs2 if r['line']), '同一主路線的去程／返程各自配到線，方向與站序一致')
BUS_SHAPE[:] = _keep

ms = T.build_metro(Fake(), box, quiet)
main = [m for m in ms if m['ref'] in ('板南線', '淡水信義線')]
check(len(main) == 4, f'捷運：板南、淡水信義各兩個方向 = 4（API 重複給的路線去重）→ {len(main)}')
bl = [m for m in ms if m['ref'] == '板南線']
check({m['to'] for m in bl} == {'藍8', '藍1'} and bl[0]['stops'][0] != bl[1]['stops'][0], '捷運：往返兩個方向的站序與終點')
check(bl[0]['col'] == '#0070bd' and bl[0]['iv'] == 5 and bl[0]['r'] == 'subway', f"捷運：路線顏色、班距平均（3–5、5–7→5）→ {bl[0]['col']} {bl[0]['iv']}")
check(bl[0]['line'][0] != bl[1]['line'][0], '捷運：反方向的路線軌跡也反轉')

# 即使錯誤軌跡的起訖點吻合，只要中間站點偏離，也必須退回站序線，不能畫出跨區錯誤連線。
class WrongMetroShape(Fake):
    def get(self, path, tries=4):
        if 'Metro/Shape' in path:
            return [{'LineID': 'BL', 'Geometry': wkt([(25.03, 121.50), (25.08, 121.56), (25.03, 121.57)])}]
        return super().get(path, tries)
wrong = [m for m in T.build_metro(WrongMetroShape(), box, quiet) if m['ref'] == '板南線']
# build_metro intentionally applies Douglas-Peucker simplification (8 m),
# so this straight synthetic station sequence reduces to its two endpoints.
expected = [[T.B.r5(25.03), T.B.r5(121.51)],
            [T.B.r5(25.03), T.B.r5(121.58)]]
check(len(wrong) == 2 and wrong[0]['line'] == expected and wrong[1]['line'] == expected[::-1],
      '捷運：起訖點吻合但中間站偏離時，退回正確站序（並保留既有簡化規則）')

# 軌跡被切成兩段＋有支線：逐站沿軌跡走，轉彎點要保留，不能站與站拉直線
segA = [[25.00, 121.50], [25.00, 121.52], [25.02, 121.52]]            # 主線前半（有一個轉彎）
segB = [[25.02, 121.52], [25.04, 121.52], [25.04, 121.54]]            # 主線後半
segC = [[25.02, 121.52], [25.02, 121.56]]                             # 支線
sts = [[25.00, 121.505], [25.01, 121.52], [25.03, 121.52], [25.04, 121.535]]
ln, straight = T.path_through([segC, segB, segA], sts)
check(straight == 0 and [25.00, 121.52] in ln and [25.04, 121.52] in ln and [25.02, 121.56] not in ln, f'捷運軌跡分段／有支線：沿正確片段走並保留轉彎點（{len(ln)} 點），不走到支線')
ln2, straight2 = T.path_through([segA], sts)
check(straight2 == 2 and ln2[-1] == sts[-1], '缺軌跡的站間才用直線，並回報數量')
good = [[25.0, 121.5], [25.0, 121.6]]; frag = [[25.2, 121.5 + i * 0.001] for i in range(50)]
sp = [[25.0, 121.52], [25.0, 121.55]]
check(max([frag, good], key=lambda seg: (sum(1 for p in sp if T.project(seg, p)[0] <= 60), len(seg))) is good, '公車軌跡有多個片段時選貼著站牌的那段，而不是點數最多的那段')

with tempfile.TemporaryDirectory() as d:
    try:
        T.build(Fake(broken=['Metro/']), d, quiet); ok = False
    except RuntimeError:
        ok = not os.path.exists(os.path.join(d, 'routes.json'))
    check(ok, '捷運抓不到（資料量異常少）時拒絕寫檔、不覆蓋既有路線檔')
    s = T.build(Fake(), d, quiet)
    j = json.load(open(os.path.join(d, 'routes.json'), encoding='utf-8'))
    check(j['src'] == 'tdx' and len(j['routes']) == s['bus'] + s['metro'] and s['bus'] == 120, f'輸出 routes.json（src=tdx）→ {s}')
    keys = {'id', 'r', 'ref', 'to', 'from', 'name', 'iv', 'col', 'line', 'stops'}
    check(all(keys <= set(r) for r in j['routes']), '每條路線都有 app 需要的欄位（與 OSM 版格式相同）')

import build_data as B
with tempfile.TemporaryDirectory() as d:
    # 舊版留下的各區檔會在合併時清掉，只留 city.json
    for name, _ in B.districts():
        json.dump({'built': 'old', 'layers': {}}, open(os.path.join(d, name + '.json'), 'w', encoding='utf-8'))
    f = {'id': 'n1', 'n': '捷運站', 'c': [25.0, 121.5]}
    r = B.merge_city(d, 'new', [{'layers': {'mrt': [f], 'park': [{'id': 'n2', 'n': '', 'c': [25, 121.5]}]}}, {'layers': {'mrt': [f]}}])
    check(r['mrt'] == 1 and r['park'] == 1 and os.listdir(d) == ['city.json'], f'合併成單一 city.json（去重複），舊的各區檔被移除 → {sorted(os.listdir(d))}')

with tempfile.TemporaryDirectory() as d:
    json.dump({'built': 'x', 'src': 'tdx', 'routes': [{'id': 1}]}, open(os.path.join(d, 'routes.json'), 'w'))
    asked = []
    def fetch(q):
        asked.append(q); return {'elements': []}
    B.build(fetch=fetch, out_dir=d, pause=0)
    check(not any('route' in q and 'rel[' in q for q in asked) and json.load(open(os.path.join(d, 'routes.json')))['src'] == 'tdx',
          '設施建置遇到已有 TDX 路線檔：不再向 OSM 抓路線、不覆蓋 TDX 檔')

print('\n全部通過' if not fails else f'\n失敗 {len(fails)} 項：{fails}')
sys.exit(1 if fails else 0)
