#!/usr/bin/env python3
"""用交通部 TDX 的公車／捷運資料產生 data/routes.json（路線實際走法、站序、班距）。

需要環境變數 TDX_CLIENT_ID、TDX_CLIENT_SECRET（GitHub Secrets）。
輸出格式與 build_data.py 的 OSM 路線相同，app 不需要區分來源：
  {'built','src':'tdx','routes':[{id,r,ref,to,from,name,iv,col,line,stops}]}
  r: 'bus' / 'subway'；iv: 平均班距（分鐘），沒有資料就是 None；line/stops: [[lat,lon],...]
"""
import json, math, os, re, sys, time, zlib, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_data as B

ROOT = B.ROOT
AUTH = 'https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token'
API = 'https://tdx.transportdata.tw/api/basic'
BUS_CITIES = ['Taipei', 'NewTaipei']          # 新北的公車也有進臺北市
METRO = [('TRTC', 'subway')]                  # 臺北捷運
METRO_COL = {'BL': '#0070bd', 'R': '#e3002c', 'G': '#008659', 'O': '#f8b61c', 'BR': '#c48c31', 'Y': '#fddb00'}
MARGIN = 0.03                                 # 度；路線只保留城市範圍外 ~3 公里內的點


class Tdx:
    def __init__(self, cid, secret, pause=1.0):
        self.cid, self.secret, self.pause, self.tok, self.exp = cid, secret, pause, None, 0

    def token(self):
        if self.tok and time.time() < self.exp - 60:
            return self.tok
        body = urllib.parse.urlencode({'grant_type': 'client_credentials', 'client_id': self.cid, 'client_secret': self.secret}).encode()
        req = urllib.request.Request(AUTH, data=body, headers={'Content-Type': 'application/x-www-form-urlencoded'})
        j = json.load(urllib.request.urlopen(req, timeout=60))
        self.tok, self.exp = j['access_token'], time.time() + int(j.get('expires_in', 3600))
        return self.tok

    def get(self, path, tries=6):
        url = f'{API}{path}{"&" if "?" in path else "?"}%24format=JSON'
        last = None
        for i in range(tries):
            try:
                req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + self.token(), 'Accept-Encoding': 'gzip', 'User-Agent': 'taipei-life-map'})
                with urllib.request.urlopen(req, timeout=180) as r:
                    raw = r.read()
                    if r.headers.get('Content-Encoding') == 'gzip':
                        import gzip; raw = gzip.decompress(raw)
                time.sleep(self.pause)
                j = json.loads(raw.decode('utf-8'))
                return j if isinstance(j, list) else (j.get('Items') or j.get('data') or [])
            except urllib.error.HTTPError as e:
                last = f'HTTP {e.code} {e.read()[:200]!r}'
                if e.code == 401:
                    self.tok = None
                time.sleep((20 if e.code == 429 else 3) * (i + 1) if e.code in (429, 500, 502, 503, 504, 401) else 0)
                if e.code in (400, 403, 404):
                    break
            except Exception as e:
                last = repr(e); time.sleep(3 * (i + 1))
        raise RuntimeError(f'{path}: {last}')


def zh(o):
    if isinstance(o, dict):
        return o.get('Zh_tw') or o.get('En') or ''
    return o or ''


def wkt_lines(g):
    """WKT LINESTRING / MULTILINESTRING → 獨立線段清單，絕不把不相連的片段串接。"""
    if not g:
        return []
    body = g[g.find('(') + 1:g.rfind(')')]
    chunks = re.split(r'\)\s*,\s*\(', body) if 'MULTILINESTRING' in g.upper() else [body]
    lines = []
    for chunk in chunks:
        pts = [[float(b), float(a)] for a, b in re.findall(r'(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)', chunk)]
        if len(pts) >= 2:
            lines.append(pts)
    return lines


def wkt_points(g):
    """相容舊呼叫：只回傳單一 LINESTRING；MULTILINESTRING 不跨片段合併。"""
    lines = wkt_lines(g)
    return max(lines, key=len) if lines else []


def inbox(p, box):
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


def clip_segment(a, b, box):
    """Liang–Barsky：裁切單一線段，回傳框內端點；完全在框外則回傳 None。"""
    south, west, north, east = box
    dx, dy = b[1] - a[1], b[0] - a[0]
    p = (-dx, dx, -dy, dy)
    q = (a[1] - west, east - a[1], a[0] - south, north - a[0])
    lo, hi = 0.0, 1.0
    for pi, qi in zip(p, q):
        if pi == 0:
            if qi < 0:
                return None
            continue
        t = qi / pi
        if pi < 0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
        if lo > hi:
            return None
    return [[a[0] + lo * dy, a[1] + lo * dx], [a[0] + hi * dy, a[1] + hi * dx]]


def clip_polyline(points, box):
    """裁切折線並保留不連續片段，避免刪除框外點後錯誤跨區連線。"""
    segments, current = [], []
    for a, b in zip(points, points[1:]):
        clipped = clip_segment(a, b, box)
        if not clipped:
            if len(current) >= 2:
                segments.append(current)
            current = []
            continue
        start, end = clipped
        if current and math.hypot(current[-1][0] - start[0], current[-1][1] - start[1]) < 1e-8:
            current.append(end)
        else:
            if len(current) >= 2:
                segments.append(current)
            current = clipped[:]
    if len(current) >= 2:
        segments.append(current)
    return segments


def headway_mid(obj):
    """從任意巢狀結構收集 Min/MaxHeadwayMins，回傳平均班距。"""
    vals = []
    def walk(x):
        if isinstance(x, dict):
            lo, hi = x.get('MinHeadwayMins'), x.get('MaxHeadwayMins')
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and hi > 0:
                vals.append((lo + hi) / 2)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(obj)
    return sum(vals) / len(vals) if vals else None


def bus_interval(sched):
    """公車班距：優先用班距格式；否則取週一各班首站發車時間的相鄰間隔中位數（少於 3 班就不推算）。"""
    iv = headway_mid(sched.get('Frequencys'))
    if iv:
        return round(iv, 1)
    deps = []
    for t in sched.get('Timetables') or []:
        if t.get('ServiceDay') and t['ServiceDay'].get('Monday') not in (1, True):
            continue
        for st in (t.get('StopTimes') or [])[:1]:
            m = re.match(r'^(\d{1,2}):(\d{2})', st.get('DepartureTime') or st.get('ArrivalTime') or '')
            if m:
                deps.append(int(m.group(1)) * 60 + int(m.group(2)))
    deps.sort()
    gaps = sorted(b - a for a, b in zip(deps, deps[1:]) if b > a)
    return round(gaps[len(gaps) // 2], 1) if len(deps) >= 3 and gaps else None


def bus_key(x):
    return (x.get('SubRouteUID') or x.get('RouteUID') or '', int(x.get('Direction') or 0))


def build_bus(t, box, log=print):
    out, seen, diag = [], set(), [0]
    for city in BUS_CITIES:
        try:
            stops = t.get(f'/v2/Bus/StopOfRoute/City/{city}')
            shapes = t.get(f'/v2/Bus/Shape/City/{city}')
        except Exception as e:
            log(f'  bus {city} FAILED {e}'); continue
        try:
            sch = {bus_key(s): s for s in t.get(f'/v2/Bus/Schedule/City/{city}')}
        except Exception as e:
            log(f'  bus schedule {city} FAILED {e}'); sch = {}
        sh = {bus_key(s): s for s in shapes}
        log(f'  bus {city}: stopOfRoute {len(stops)}, shape {len(shapes)}, schedule {len(sch)}')
        for s in stops:
            k = bus_key(s)
            if k in seen:
                continue
            seen.add(k)
            pts = [(p, st) for st in sorted(s.get('Stops') or [], key=lambda z: z.get('StopSequence') or 0)
                   for p in [[st['StopPosition']['PositionLat'], st['StopPosition']['PositionLon']]] if st.get('StopPosition')]
            if len(pts) < 2 or not any(inbox(p, box) for p, _ in pts):
                continue
            lines = wkt_lines((sh.get(k) or {}).get('Geometry'))
            if not lines:
                lines = [[p for p, _ in pts]]       # 沒有行駛軌跡時退而求其次：依站序連線
            clipped = [seg for raw in lines for seg in clip_polyline(raw, box)]
            if not clipped:
                continue
            # 目前前端路線格式是一條折線；只取最長的連續框內片段，絕不跨越框外空隙硬接。
            line = max(clipped, key=len)
            names = [zh(st.get('StopName')) for _, st in pts]
            ref = zh(s.get('SubRouteName')) or zh(s.get('RouteName'))
            line = [[B.r5(a), B.r5(b)] for a, b in B.simplify(line, 8)]
            if len(line) < 2:
                continue
            iv = bus_interval(sch.get(k) or {})
            out.append({'id': f'{city[0]}{zlib.crc32(repr(k).encode())}', 'r': 'bus', 'ref': ref, 'to': names[-1], 'from': names[0], 'name': ref,
                        'iv': iv, 'col': None, 'line': line,
                        'stops': [[B.r5(p[0]), B.r5(p[1])] for p, _ in pts if inbox(p, box)]})
    return out


def build_metro(t, box, log=print):
    out = []
    for op, kind in METRO:
        try:
            st = {x['StationID']: x for x in t.get(f'/v2/Rail/Metro/Station/{op}')}
            sor = t.get(f'/v2/Rail/Metro/StationOfRoute/{op}')
            shp = t.get(f'/v2/Rail/Metro/Shape/{op}')
        except Exception as e:
            log(f'  metro {op} FAILED {e}'); continue
        try:
            fq = t.get(f'/v2/Rail/Metro/Frequency/{op}')
        except Exception as e:
            log(f'  metro frequency FAILED {e}'); fq = []
        log(f'  metro {op}: station {len(st)}, route {len(sor)}, shape {len(shp)}, frequency {len(fq)}')
        shape_by = {}
        for s in shp:
            lid0 = s.get('LineID') or s.get('LineNo')
            for segment in wkt_lines(s.get('Geometry')):
                shape_by.setdefault(lid0, []).append(segment)
        iv_by = {}
        for f in fq:
            iv_by.setdefault(f.get('LineID') or f.get('LineNo'), []).append(f)
        seen = set()
        for r in sor:
            lid = r.get('LineID') or r.get('LineNo') or ''
            seq = []
            for s in sorted(r.get('Stations') or [], key=lambda z: z.get('Sequence') or 0):
                p = (st.get(s.get('StationID')) or {}).get('StationPosition')
                if p:
                    seq.append(([p['PositionLat'], p['PositionLon']], zh(s.get('StationName'))))
            if len(seq) < 2:
                continue
            # 只採用能對應本方向起訖站的完整幾何；不串接同線不同支線／斷裂片段。
            candidates = shape_by.get(lid) or []
            def endpoint_cost(candidate):
                a, b = candidate[0], candidate[-1]
                s0, s1 = seq[0][0], seq[-1][0]
                forward = math.hypot(a[0]-s0[0], a[1]-s0[1]) + math.hypot(b[0]-s1[0], b[1]-s1[1])
                reverse = math.hypot(b[0]-s0[0], b[1]-s0[1]) + math.hypot(a[0]-s1[0], a[1]-s1[1])
                # Endpoints alone can match a wrong branch. Require every ordered
                # station to be close to the candidate geometry as well.
                def point_segment_distance(p, x, y):
                    dx, dy = y[0]-x[0], y[1]-x[1]
                    den = dx*dx + dy*dy
                    if den == 0: return math.hypot(p[0]-x[0], p[1]-x[1])
                    u = max(0.0, min(1.0, ((p[0]-x[0])*dx + (p[1]-x[1])*dy)/den))
                    return math.hypot(p[0]-(x[0]+u*dx), p[1]-(x[1]+u*dy))
                def station_fit(points):
                    return max(min(point_segment_distance(p, x, y) for x, y in zip(candidate, candidate[1:])) for p, _ in points)
                fit = station_fit(seq)
                return min(forward, reverse) + fit * 4
            line = min(candidates, key=endpoint_cost) if candidates else [p for p, _ in seq]
            s0, s1 = seq[0][0], seq[-1][0]
            cost = endpoint_cost(line)
            # Reject route shapes that skip stations or accidentally cross a branch.
            if cost > 0.025:
                line = [p for p, _ in seq]
            elif math.hypot(line[-1][0]-s0[0], line[-1][1]-s0[1]) + math.hypot(line[0][0]-s1[0], line[0][1]-s1[1]) < endpoint_cost(line):
                line = line[::-1]
            ivs = [headway_mid(x) for x in iv_by.get(lid, [])]; ivs = [v for v in ivs if v]
            iv = round(sum(ivs) / len(ivs), 1) if ivs else None
            col = next((c for k, c in METRO_COL.items() if re.match(k + r'\d*$', lid or '')), None)
            name = zh(r.get('RouteName')) or lid
            for fwd in (True, False):
                ps = seq if fwd else seq[::-1]
                ln = line if fwd else line[::-1]
                key = (lid, ps[-1][1])
                if key in seen:
                    continue
                seen.add(key)
                out.append({'id': f'M{zlib.crc32(repr(key).encode())}', 'r': kind, 'ref': name, 'to': ps[-1][1], 'from': ps[0][1], 'name': name,
                            'iv': iv, 'col': col, 'line': [[B.r5(a), B.r5(b)] for a, b in B.simplify(ln, 8)],
                            'stops': [[B.r5(p[0]), B.r5(p[1])] for p, _ in ps]})
    return out


def city_box():
    ds = B.districts()
    bb = [min(b[0] for _, b in ds), min(b[1] for _, b in ds), max(b[2] for _, b in ds), max(b[3] for _, b in ds)]
    return [bb[0] - MARGIN, bb[1] - MARGIN, bb[2] + MARGIN, bb[3] + MARGIN]


def build(t, out_dir=os.path.join(ROOT, 'data'), log=print):
    box = city_box()
    routes = build_metro(t, box, log) + build_bus(t, box, log)
    nb = sum(1 for r in routes if r['r'] == 'bus'); nm = len(routes) - nb
    withiv = sum(1 for r in routes if r['iv'])
    log(f'routes: bus {nb}, metro {nm}, with interval {withiv}')
    if nb < 50 or nm < 4:
        raise RuntimeError('TDX 資料量異常少，不覆蓋既有路線檔')
    os.makedirs(out_dir, exist_ok=True)
    built = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    json.dump({'built': built, 'src': 'tdx', 'routes': routes}, open(os.path.join(out_dir, 'routes.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    # 同步 index.json 的路線數量與更新日期，避免前端／診斷資訊顯示舊狀態。
    index_path = os.path.join(out_dir, 'index.json')
    if os.path.exists(index_path):
        try:
            index = json.load(open(index_path, encoding='utf-8'))
        except (OSError, ValueError):
            index = {}
        index['routes'] = len(routes)
        index['routes_built'] = built
        json.dump(index, open(index_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return {'bus': nb, 'metro': nm, 'with_interval': withiv}


if __name__ == '__main__':
    cid, sec = os.environ.get('TDX_CLIENT_ID'), os.environ.get('TDX_CLIENT_SECRET')
    if not cid or not sec:
        print('缺少 TDX_CLIENT_ID / TDX_CLIENT_SECRET', file=sys.stderr); sys.exit(2)
    print(json.dumps(build(Tdx(cid, sec)), ensure_ascii=False))
