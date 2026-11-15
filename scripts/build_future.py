#!/usr/bin/env python3
"""興建中／規劃中的捷運與輕軌（OpenStreetMap）→ data/future.json。build-data.yml 每週跑一次。

只收 subway、light_rail（不含高鐵、台鐵），範圍是臺北市外加約 5 公里。
狀態：construction（興建中）、proposed（規劃中）。車站依最近的路線歸線。
"""
import json, math, os, re, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_data as B

BBOX = (24.93, 121.43, 25.22, 121.68)   # 南、西、北、東
KINDS = ('subway', 'light_rail', 'monorail')
QUERY = ('[out:json][timeout:180][bbox:{0},{1},{2},{3}];('
         'way["railway"~"^(construction|proposed)$"];way["construction:railway"~"^(subway|light_rail|monorail)$"];way["proposed:railway"~"^(subway|light_rail|monorail)$"];'
         ')->.w;.w out geom;('
         'node["railway"~"^(construction|proposed)$"]["construction"~"station"];node["railway"~"^(construction|proposed)$"]["proposed"~"station"];'
         'node["construction:railway"="station"];node["proposed:railway"="station"];node["railway"="station"]["construction"];'
         ')->.n;.n out;').format(*BBOX)

# 名稱 → 線名（同一條線在 OSM 常有很多段、不同寫法）
LINES = [(r'環狀線.*北環|北環段', '環狀線北環段'), (r'環狀線.*東環|東環段', '環狀線東環段'), (r'環狀線.*南環|南環段', '環狀線南環段'),
         (r'萬大', '萬大線'), (r'民生汐止', '民生汐止線'), (r'汐止東湖', '汐東線'), (r'南港基隆', '基隆捷運'),
         (r'五股泰山', '五泰輕軌'), (r'泰山板橋', '泰板輕軌'), (r'安坑', '安坑輕軌延伸'), (r'三鶯', '三鶯線'), (r'淡海', '淡海輕軌')]
REF_LINE = [(r'^Y', '環狀線'), (r'^LG', '萬大線'), (r'^SB', '民生汐止線')]


def line_name(name, ref=''):
    for pat, n in LINES:
        if re.search(pat, name or ''):
            return n
    for pat, n in REF_LINE:
        if re.search(pat, ref or ''):
            return n
    return None


def kind_status(t):
    """回傳 (類型, 狀態)；不是捷運／輕軌回傳 None"""
    r = t.get('railway')
    if r in ('construction', 'proposed'):
        k = t.get(r) or t.get(f'{r}:railway')
        return (k, r) if k in KINDS else None
    for st in ('construction', 'proposed'):
        k = t.get(f'{st}:railway')
        if k in KINDS:
            return k, st
    return None


def dist_m(a, b):
    return math.hypot((a[0] - b[0]) * 110574.0, (a[1] - b[1]) * 111320.0 * math.cos(math.radians(25.05)))


def seg_dist(p, a, b):
    ax, ay, bx, by, px, py = a[1], a[0], b[1], b[0], p[1], p[0]
    dx, dy = bx - ax, by - ay
    t = 0 if dx == dy == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return dist_m(p, (ay + t * dy, ax + t * dx))


def simplify(pts, tol=8.0):
    """Douglas–Peucker（公尺）"""
    if len(pts) < 3:
        return pts
    d = [seg_dist(p, pts[0], pts[-1]) for p in pts[1:-1]]
    i = max(range(len(d)), key=d.__getitem__)
    if d[i] <= tol:
        return [pts[0], pts[-1]]
    return simplify(pts[:i + 2], tol)[:-1] + simplify(pts[i + 1:], tol)


def build(elements, out_dir=os.path.join(B.ROOT, 'data'), built=None, log=print):
    lines = {}
    for e in elements:
        if e['type'] != 'way' or not e.get('geometry'):
            continue
        t = e.get('tags', {}); ks = kind_status(t)
        if not ks:
            continue
        name = line_name(t.get('name', ''), t.get('ref', ''))
        if not name:
            continue
        g = [[round(p['lat'], 5), round(p['lon'], 5)] for p in e['geometry']]
        L = lines.setdefault(name, {'name': name, 'kind': ks[0], 'status': set(), 'segs': []})
        L['status'].add(ks[1]); L['segs'].append({'s': ks[1], 'p': simplify(g)})
    stations = []
    for e in elements:
        if e['type'] != 'node':
            continue
        t = e.get('tags', {}); pt = (round(e['lat'], 5), round(e['lon'], 5))
        st = 'construction' if t.get('railway') == 'construction' or 'construction:railway' in t or t.get('construction') else 'proposed'
        ref = t.get('ref', ''); name = (t.get('name') or '').strip()
        best, bd = None, 1e9
        for L in lines.values():
            for sg in L['segs']:
                for a, b in zip(sg['p'], sg['p'][1:]):
                    d = seg_dist(pt, a, b)
                    if d < bd:
                        bd, best = d, L['name']
        ln = line_name(name, ref) or (best if bd <= 250 else None)
        if ln == '環狀線':   # 只知道是環狀線：歸到最近的環狀線段
            ln = best if best and best.startswith('環狀線') and bd <= 600 else None
        if not ln or ln not in lines:
            continue
        stations.append({'n': re.sub(r'^環狀線|\(環狀線\)|（環狀線）', '', name).strip() or (ref + ' 站' if ref else '未命名站'), 'ref': ref, 'line': ln, 's': st, 'c': list(pt)})
    # 同一站重複標註（同線 80 公尺內）只留一個，優先有名字的
    stations.sort(key=lambda x: (x['n'].endswith('站') and not re.search(r'[一-鿿]', x['n'][:-1])))
    uniq = []
    for s in stations:
        if not any(u['line'] == s['line'] and dist_m(u['c'], s['c']) < 80 for u in uniq):
            uniq.append(s)
    out = {'built': built or datetime.now(timezone.utc).strftime('%Y-%m-%d'), 'src': 'osm',
           'lines': [{'name': L['name'], 'kind': L['kind'], 'status': 'construction' if 'construction' in L['status'] else 'proposed', 'segs': L['segs']} for L in sorted(lines.values(), key=lambda L: L['name'])],
           'stations': sorted(uniq, key=lambda s: (s['line'], s['ref'], s['n']))}
    if not out['lines']:
        raise RuntimeError('沒有找到任何興建中／規劃中的捷運路線，不更新')
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'future.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    log(f"  未來捷運：{len(out['lines'])} 條、車站 {len(out['stations'])} 個 " + '、'.join(f"{L['name']}（{'興建中' if L['status'] == 'construction' else '規劃中'}）" for L in out['lines']))
    return out


if __name__ == '__main__':
    build(B.overpass(QUERY)['elements'])
