#!/usr/bin/env python3
"""用交通部 TDX 的公車／捷運資料產生 data/routes.json（路線實際走法、站序、班距）。

需要環境變數 TDX_CLIENT_ID、TDX_CLIENT_SECRET（GitHub Secrets）。
輸出格式與 build_data.py 的 OSM 路線相同，app 不需要區分來源：
  {'built','src':'tdx','routes':[{id,r,ref,to,from,name,iv,col,line,stops}]}
  r: 'bus' / 'subway'；iv: 平均班距（分鐘），沒有資料就是 None；line/stops: [[lat,lon],...]
"""
import json, os, re, sys, time, zlib, urllib.parse, urllib.request, urllib.error
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


def wkt_points(g):
    """WKT LINESTRING / MULTILINESTRING → [[lat,lon],...]（經緯度順序對調）。"""
    if not g:
        return []
    return [[float(b), float(a)] for a, b in re.findall(r'(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)', g)]


def inbox(p, box):
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


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
    """公車班距：優先用班距格式，沒有就用時刻表的班次數（約 16 小時營運）推算。"""
    iv = headway_mid(sched.get('Frequencys'))
    if iv:
        return round(iv, 1)
    tt = sched.get('Timetables') or []
    wk = [t for t in tt if not t.get('ServiceDay') or t['ServiceDay'].get('Monday') in (1, True)]
    return round(960 / len(wk), 1) if wk else None


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
            line = wkt_points((sh.get(k) or {}).get('Geometry'))
            if len(line) < 2:
                line = [p for p, _ in pts]       # 沒有行駛軌跡時退而求其次：依站序連線
            names = [zh(st.get('StopName')) for _, st in pts]
            ref = zh(s.get('SubRouteName')) or zh(s.get('RouteName'))
            line = [[B.r5(a), B.r5(b)] for a, b in B.simplify([p for p in line if inbox(p, box)], 8)]
            if len(line) < 2:
                continue
            iv = bus_interval(sch.get(k) or {})
            if iv and iv >= 240 and diag[0] < 3:
                diag[0] += 1; log('  [診斷] 班距 %s 的班表：%s' % (iv, json.dumps(sch.get(k), ensure_ascii=False)[:700]))
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
            shape_by.setdefault(s.get('LineID') or s.get('LineNo'), []).extend(wkt_points(s.get('Geometry')))
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
            line = shape_by.get(lid) or [p for p, _ in seq]
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
    return {'bus': nb, 'metro': nm, 'with_interval': withiv}


if __name__ == '__main__':
    cid, sec = os.environ.get('TDX_CLIENT_ID'), os.environ.get('TDX_CLIENT_SECRET')
    if not cid or not sec:
        print('缺少 TDX_CLIENT_ID / TDX_CLIENT_SECRET', file=sys.stderr); sys.exit(2)
    print(json.dumps(build(Tdx(cid, sec)), ensure_ascii=False))
