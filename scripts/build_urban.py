#!/usr/bin/env python3
"""都市更新地區：從臺北市都市發展局「都市計畫整合查詢系統」的 ArcGIS 圖層「都市更新地區範圍」
（UrbanPlan2/PlanTheme/MapServer/0）產生 data/urban.json。只當資訊顯示，不計入估價。

圖層欄位：PLANDES（劃定說明，如「本府89年06月26日公告劃定」「依都市更新條例第十一條劃定」「震災及高氯離子劃定」）、
PLANDATE（公告日期，毫秒）。座標以 outSR=4326 取得。
"""
import json, os, re, sys, time, urllib.request
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_data as B

SERVICE = 'https://www.historygis.udd.gov.taipei/arcgis/rest/services/UrbanPlan2/PlanTheme/MapServer/0/query'
SRC_PAGE = 'https://webgis.udd.gov.taipei/upis_v2/'
KINDS = [('hazard', re.compile(r'震災|高氯離子|海砂')), ('self', re.compile(r'第十一條|第11條|自行劃定')), ('gov', re.compile(r'公告劃定|劃定'))]
LABEL = {'gov': '政府劃定更新地區', 'self': '自行劃定更新單元', 'hazard': '震災／海砂屋更新地區', 'other': '都市更新地區'}


def http_json(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'taipei-life-map/1.0'}), timeout=120) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def fetch(page=500):
    """分頁抓整個圖層（GeoJSON，WGS84）"""
    feats, off = [], 0
    while True:
        d = http_json(f'{SERVICE}?where=1%3D1&outFields=PLANDES,PLANDATE,OBJECTID&outSR=4326&f=geojson&orderByFields=OBJECTID&resultOffset={off}&resultRecordCount={page}')
        fs = d.get('features') or []
        feats += fs
        if len(fs) < page and not d.get('exceededTransferLimit'):
            return feats
        off += len(fs)


def kind_of(desc):
    for k, rx in KINDS:
        if rx.search(desc or ''):
            return k
    return 'other'


def rings_of(geom):
    """GeoJSON Polygon／MultiPolygon → [[ [lat, lon], ... ], ...]（外圈與洞都保留；約 1 公尺精度、去掉連續重複點）"""
    polys = [geom['coordinates']] if geom['type'] == 'Polygon' else geom['coordinates'] if geom['type'] == 'MultiPolygon' else []
    out = []
    for poly in polys:
        for ring in poly:
            r = []
            for lon, lat, *_ in ring:
                p = [round(lat, 5), round(lon, 5)]
                if not r or r[-1] != p:
                    r.append(p)
            if len(r) >= 4:
                out.append(r)
    return out


def area_ha(rings):
    """球面近似面積（公頃）：外圈減洞，用局部等距投影"""
    import math
    tot = 0.0
    for r in rings:
        la0 = sum(p[0] for p in r) / len(r)
        kx, ky = 111320 * math.cos(math.radians(la0)), 110574
        s = sum((r[i][1] * kx) * (r[i + 1][0] * ky) - (r[i + 1][1] * kx) * (r[i][0] * ky) for i in range(len(r) - 1))
        tot += s / 2
    return round(abs(tot) / 1e4, 2)


def build(features, out_dir=os.path.join(B.ROOT, 'data'), built=None, min_areas=100, log=print):
    areas = []
    for f in features:
        g, p = f.get('geometry'), f.get('properties') or {}
        if not g:
            continue
        rings = rings_of(g)
        if not rings:
            continue
        desc = (p.get('PLANDES') or '').strip()
        ms = p.get('PLANDATE')
        date = datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%d') if isinstance(ms, (int, float)) and ms > 0 else ''
        areas.append({'k': kind_of(desc), 't': desc, 'd': date, 'ha': area_ha(rings), 'r': rings})
    if len(areas) < min_areas:
        raise SystemExit(f'都市更新地區只有 {len(areas)} 處（少於 {min_areas}），可能是來源異常，不更新')
    cnt = {k: sum(a['k'] == k for a in areas) for k in LABEL}
    out = {'built': built or datetime.now(timezone.utc).strftime('%Y-%m-%d'), 'src': 'taipei-udd-arcgis', 'source': SRC_PAGE,
           'labels': LABEL, 'count': cnt, 'areas': areas}
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'urban.json'), 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(',', ':'))
    log(f'都市更新地區 {len(areas)} 處 {cnt}')
    return out


if __name__ == '__main__':
    build(fetch())
