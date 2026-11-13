"""預先下載臺北市各行政區（外擴 1.4 公里）的設施資料與全市大眾運輸路線，存成 data/*.json。
由 GitHub Actions 每週執行（.github/workflows/build-data.yml），app 直接讀這些檔案，不必即時查 Overpass。
本機執行：python3 scripts/build_data.py
"""
import json, math, os, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
ENDPOINTS = ['https://overpass-api.de/api/interpreter', 'https://overpass.private.coffee/api/interpreter',
             'https://maps.mail.ru/osm/tools/overpass/api/interpreter', 'https://overpass.kumi.systems/api/interpreter']
BUFFER = 1400  # 公尺；與 index.html 相同
LAYERS = [  # 與 index.html 的 LAYERS 相同
    ('park', True, 'nwr["leisure"="park"]', lambda t: t.get('leisure') == 'park'),
    ('green', True, 'nwr["landuse"~"^(grass|forest|meadow|recreation_ground|village_green)$"];nwr["leisure"="garden"];nwr["natural"~"^(wood|scrub|grassland)$"]',
     lambda t: bool(re.match(r'^(grass|forest|meadow|recreation_ground|village_green)$', t.get('landuse', ''))) or t.get('leisure') == 'garden'
     or bool(re.match(r'^(wood|scrub|grassland)$', t.get('natural', '')))),
    ('school', False, 'nwr["amenity"~"^(school|kindergarten|university|college)$"]', lambda t: bool(re.match(r'^(school|kindergarten|university|college)$', t.get('amenity', '')))),
    ('sport', False, 'nwr["leisure"~"^(sports_centre|pitch|stadium|track|swimming_pool|fitness_centre)$"]',
     lambda t: bool(re.match(r'^(sports_centre|pitch|stadium|track|swimming_pool|fitness_centre)$', t.get('leisure', '')))),
    ('bus', False, 'node["highway"="bus_stop"]', lambda t: t.get('highway') == 'bus_stop'),
    ('mrt', False, 'nwr["railway"="station"]["station"="subway"];nwr["public_transport"="station"]["subway"="yes"]',
     lambda t: (t.get('railway') == 'station' and t.get('station') == 'subway') or (t.get('public_transport') == 'station' and t.get('subway') == 'yes')),
]
ROUTE_RE = '^(bus|subway|light_rail|train|monorail)$'


def overpass(q, timeout=900):
    err = None
    for url in ENDPOINTS:
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, data=urllib.parse.urlencode({'data': q}).encode(),
                                             headers={'User-Agent': 'taipei-life-map data builder (github.com/rubychen8407/taipei-life-map)'})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.load(r)
            except Exception as e:
                err = e
                print('  overpass failed', url, e, file=sys.stderr)
                time.sleep(20)
    raise err


def r5(v):
    return round(v, 5)


def simplify(pts, tol_m=6):
    """Douglas-Peucker（公尺）"""
    if len(pts) < 3:
        return pts
    kx = 111320 * math.cos(math.radians(pts[0][0])); ky = 110540
    def d(p, a, b):
        ax, ay, bx, by, px, py = a[1] * kx, a[0] * ky, b[1] * kx, b[0] * ky, p[1] * kx, p[0] * ky
        dx, dy = bx - ax, by - ay
        if dx == dy == 0:
            return math.hypot(px - ax, py - ay)
        t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - ax - t * dx, py - ay - t * dy)
    keep = [False] * len(pts); keep[0] = keep[-1] = True; st = [(0, len(pts) - 1)]
    while st:
        i, j = st.pop(); best, bi = 0, -1
        for k in range(i + 1, j):
            dd = d(pts[k], pts[i], pts[j])
            if dd > best:
                best, bi = dd, k
        if best > tol_m:
            keep[bi] = True; st += [(i, bi), (bi, j)]
    return [p for p, k in zip(pts, keep) if k]


def districts():
    g = json.load(open(os.path.join(ROOT, 'taipei_districts.geojson'), encoding='utf-8'))
    out = []
    for f in g['features']:
        geom = f['geometry']; polys = geom['coordinates'] if geom['type'] == 'MultiPolygon' else [geom['coordinates']]
        xs = [c[0] for p in polys for c in p[0]]; ys = [c[1] for p in polys for c in p[0]]
        out.append((f['properties']['TOWNNAME'], [min(ys), min(xs), max(ys), max(xs)]))
    return out


def buffered(bb):
    lat0 = (bb[0] + bb[2]) / 2; pl = BUFFER / 110540; po = BUFFER / (111320 * math.cos(math.radians(lat0)))
    return [bb[0] - pl, bb[1] - po, bb[2] + pl, bb[3] + po]


def stitch(ways):
    """把 multipolygon 的 outer ways 依端點接成封閉環（接不起來的原樣保留）"""
    eq = lambda a, b: abs(a[0] - b[0]) < 1e-7 and abs(a[1] - b[1]) < 1e-7
    ways = [list(w) for w in ways if len(w) > 1]; out = []
    while ways:
        cur = ways.pop(0); grew = True
        while grew and not eq(cur[0], cur[-1]):
            grew = False
            for i, w in enumerate(ways):
                if eq(cur[-1], w[0]):
                    cur += w[1:]
                elif eq(cur[-1], w[-1]):
                    cur += w[::-1][1:]
                elif eq(cur[0], w[-1]):
                    cur = w[:-1] + cur
                elif eq(cur[0], w[0]):
                    cur = w[::-1][:-1] + cur
                else:
                    continue
                ways.pop(i); grew = True; break
        out.append(cur)
    return out


def feature(e, geom):
    t = e.get('tags', {})
    name = t.get('name:zh') or t.get('name') or ''
    if e['type'] == 'node':
        c = [e['lat'], e['lon']]
    elif 'center' in e:
        c = [e['center']['lat'], e['center']['lon']]
    elif 'bounds' in e:
        b = e['bounds']; c = [(b['minlat'] + b['maxlat']) / 2, (b['minlon'] + b['maxlon']) / 2]
    else:
        return None
    f = {'id': e['type'][0] + str(e['id']), 'n': name, 'c': [r5(c[0]), r5(c[1])]}
    if geom:
        polys = []
        if e['type'] == 'way' and e.get('geometry'):
            polys = [[[p['lat'], p['lon']] for p in e['geometry']]]
        elif e['type'] == 'relation':
            polys = stitch([[[p['lat'], p['lon']] for p in m['geometry']] for m in e.get('members', []) if m.get('geometry') and m.get('role') != 'inner'])
        polys = [[[r5(a), r5(b)] for a, b in simplify(p, 4)] for p in polys if len(p) > 1]
        if polys:
            f['p'] = polys
    return f


def build_district(name, bb, fetch):
    s, w, n, e = buffered(bb)
    stm = lambda ls: ''.join(''.join(x + ';' for x in l[2].split(';')) for l in ls)
    g = [l for l in LAYERS if l[1]]; c = [l for l in LAYERS if not l[1]]
    q = f'[out:json][timeout:600][bbox:{s:.5f},{w:.5f},{n:.5f},{e:.5f}];({stm(g)})->.g;.g out geom;({stm(c)})->.c;.c out center;'
    j = fetch(q)
    layers = {l[0]: [] for l in LAYERS}; seen = set()
    for el in j['elements']:
        t = el.get('tags', {})
        for lid, geom, _, ok in LAYERS:
            if not ok(t):
                continue
            f = feature(el, geom)
            if not f:
                continue
            if lid == 'mrt':
                k = re.sub('站$', '', f['n'])
                if not f['n'] or k in seen:
                    continue
                seen.add(k)
            layers[lid].append(f)
    return {'district': name, 'bbox': [r5(v) for v in (s, w, n, e)], 'layers': layers}


def chain(ways):
    eq = lambda a, b: abs(a[0] - b[0]) < 1e-7 and abs(a[1] - b[1]) < 1e-7
    segs, cur = [], None
    for w in ways:
        if cur is None:
            cur = list(w); continue
        if eq(cur[-1], w[0]):
            cur += w[1:]
        elif eq(cur[-1], w[-1]):
            cur += w[::-1][1:]
        elif not segs and (eq(cur[0], w[0]) or eq(cur[0], w[-1])):
            cur.reverse(); cur += (w[1:] if eq(cur[-1], w[0]) else w[::-1][1:])
        else:
            segs.append(cur); cur = list(w)
    if cur:
        segs.append(cur)
    return [p for s in segs for p in s]


def interval_min(v):
    if not v:
        return None
    v = str(v).strip(); m = re.match(r'^(\d{1,2}):(\d{2})', v)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.search(r'\d+(\.\d+)?', v)
    return float(m.group(0)) if m else None


def build_routes(bb, fetch):
    s, w, n, e = buffered(bb)
    j = fetch(f'[out:json][timeout:900][maxsize:2000000000];rel["route"~"{ROUTE_RE}"]({s:.5f},{w:.5f},{n:.5f},{e:.5f});out geom;')
    out = []
    for rel in j['elements']:
        if rel.get('type') != 'relation':
            continue
        t = rel.get('tags', {}); mem = rel.get('members', [])
        ways = [[[p['lat'], p['lon']] for p in m['geometry']] for m in mem if m.get('type') == 'way' and m.get('geometry') and 'platform' not in (m.get('role') or '')]
        if not ways:
            continue
        stops = [[r5(m['lat']), r5(m['lon'])] for m in mem if m.get('type') == 'node' and m.get('lat') is not None and (m.get('role') or '').startswith('stop')]
        if len(stops) < 2:
            stops = [[r5(m['lat']), r5(m['lon'])] for m in mem if m.get('type') == 'node' and m.get('lat') is not None and re.search('stop|platform', m.get('role') or '')]
        line = [[r5(a), r5(b)] for a, b in simplify(chain(ways), 8)]
        out.append({'id': rel['id'], 'r': t.get('route'), 'ref': t.get('ref', ''), 'to': t.get('to', ''), 'from': t.get('from', ''), 'name': t.get('name', ''),
                    'iv': interval_min(t.get('interval') or t.get('interval:conditional')), 'col': t.get('colour'), 'line': line, 'stops': stops})
    return out


def merge_city(out_dir, built=None):
    """把 12 區檔案合併成一個去重複的 city.json（app 開啟時只讀這一個檔）"""
    layers = {l[0]: {} for l in LAYERS}; dates = []
    for name, _ in districts():
        p = os.path.join(out_dir, name + '.json')
        if not os.path.exists(p):
            return None
        d = json.load(open(p, encoding='utf-8')); dates.append(d.get('built', ''))
        for lid in layers:
            for f in d['layers'].get(lid, []):
                layers[lid].setdefault(f['id'], f)
    seen = set(); mrt = []
    for f in layers['mrt'].values():   # 跨區再去一次重複站名
        k = re.sub('站$', '', f['n'])
        if k not in seen:
            seen.add(k); mrt.append(f)
    city = {'built': built or min(dates), 'layers': {lid: (mrt if lid == 'mrt' else list(v.values())) for lid, v in layers.items()}}
    json.dump(city, open(os.path.join(out_dir, 'city.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    return {k: len(v) for k, v in city['layers'].items()}


def build(fetch=overpass, out_dir=os.path.join(ROOT, 'data'), pause=10):
    os.makedirs(out_dir, exist_ok=True)
    ds = districts(); built = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    index = {'built': built, 'districts': {}, 'routes': False}
    for name, bb in ds:
        print('district', name, flush=True)
        try:
            d = build_district(name, bb, fetch); d['built'] = built
            json.dump(d, open(os.path.join(out_dir, name + '.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
            index['districts'][name] = {k: len(v) for k, v in d['layers'].items()}
        except Exception as e:
            print('  FAILED', name, e, file=sys.stderr)
        time.sleep(pause)
    city = [min(b[0] for _, b in ds), min(b[1] for _, b in ds), max(b[2] for _, b in ds), max(b[3] for _, b in ds)]
    try:
        print('routes', flush=True)
        rs = build_routes(city, fetch)
        json.dump({'built': built, 'routes': rs}, open(os.path.join(out_dir, 'routes.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
        index['routes'] = len(rs)
    except Exception as e:
        print('  routes FAILED', e, file=sys.stderr)
    index['city'] = merge_city(out_dir, built)
    json.dump(index, open(os.path.join(out_dir, 'index.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps(index, ensure_ascii=False))
    return index


if __name__ == '__main__':
    if sys.argv[1:] == ['merge']:
        print(merge_city(os.path.join(ROOT, 'data'))); sys.exit(0)
    idx = build()
    sys.exit(0 if idx['districts'] else 1)
