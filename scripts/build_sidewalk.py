#!/usr/bin/env python3
"""用內政部「各縣市人行道資訊」產生 data/sidewalk.json（人行道類別）。

資料：政府資料開放平臺 dataset 58791（內政部營建署／國土管理署），每半年更新，
     ZIP 內有各縣市 GeoJSON，欄位含 NAME、SW_LENG（長度）、SW_WTH（寬度）、SWW_WTH（淨寬）。
只保留「淨寬 ≥ MIN_NET 公尺」的人行道（臺北市規定人行道淨寬原則上至少 1.5 公尺），
範圍是臺北市＋周邊 BUFFER 公尺內的新北市路段（與設施資料一致，邊界附近也算得準）。

輸出格式與 city.json 的設施相同，app 直接當成一個類別：
  {'built','src':'moi-sidewalk','min_net','features':[{'id','n','c':[lat,lon],'p':[[[lat,lon],...]]}],'stats':{...}}
"""
import io, json, os, re, sys, urllib.request, zipfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_data as B

API = 'https://data.gov.tw/api/v2/rest/dataset/58791'
MIN_NET = 1.5
CITIES = ('台北市', '臺北市', '新北市')


def latest_zip(meta):
    """從資料集的檔案清單挑最新一期的 SIDEWALK_YYYYMM（全國 ZIP）"""
    best = None
    for d in (meta.get('result') or meta).get('distribution', []):
        m = re.fullmatch(r'SIDEWALK_(\d{6})', (d.get('resourceDescription') or '').strip())
        if m and (d.get('resourceFormat') or '').upper() == 'ZIP' and (best is None or m.group(1) > best[0]):
            best = (m.group(1), d['resourceDownloadUrl'])
    if not best:
        raise RuntimeError('找不到 SIDEWALK_YYYYMM 的 ZIP')
    return best


def read_zip(raw):
    """回傳 {縣市: features}，只讀臺北市、新北市的 WGS84 版本"""
    z = zipfile.ZipFile(io.BytesIO(raw)); out = {}
    for n in z.namelist():
        m = re.search(r'SIDEWALK_(.+?)_\d{6}_WGS84\.geojson$', n)
        if m and m.group(1) in CITIES:
            out[m.group(1)] = json.loads(z.read(n).decode('utf-8-sig')).get('features', [])
    if not any(c in out for c in ('台北市', '臺北市')):
        raise RuntimeError('ZIP 裡沒有臺北市的 WGS84 檔')
    return out


def rings_of(geom):
    if not geom:
        return []
    polys = geom['coordinates'] if geom['type'] == 'MultiPolygon' else [geom['coordinates']] if geom['type'] == 'Polygon' else []
    return [p[0] for p in polys if p and len(p[0]) >= 4]


def build(by_city, out_dir=os.path.join(B.ROOT, 'data'), built=None, period='', log=print):
    ds = B.districts()
    bb = B.buffered([min(b[0] for _, b in ds), min(b[1] for _, b in ds), max(b[2] for _, b in ds), max(b[3] for _, b in ds)])
    feats, stats = [], {'total': 0, 'narrow': 0, 'outside': 0, 'kept': 0}
    for city, fs in by_city.items():
        for f in fs:
            stats['total'] += 1
            pr = f.get('properties') or {}
            try:
                net = float(pr.get('SWW_WTH') or 0)
            except (TypeError, ValueError):
                net = 0
            if net < MIN_NET:
                stats['narrow'] += 1; continue
            for ring in rings_of(f.get('geometry')):
                pts = [[c[1], c[0]] for c in ring]                       # [lon,lat] → [lat,lon]
                if not any(bb[0] <= la <= bb[2] and bb[1] <= lo <= bb[3] for la, lo in pts):
                    stats['outside'] += 1; continue
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                pts = [[B.r5(a), B.r5(b)] for a, b in B.simplify(pts, 1.5)]
                if len(pts) < 4:
                    continue
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                la = sum(p[0] for p in pts[:-1]) / (len(pts) - 1); lo = sum(p[1] for p in pts[:-1]) / (len(pts) - 1)
                name = (pr.get('NAME') or '').strip()
                feats.append({'id': f's{len(feats) + 1}', 'n': f'{name}人行道（淨寬 {net:g} 公尺）' if name else f'人行道（淨寬 {net:g} 公尺）',
                              'c': [B.r5(la), B.r5(lo)], 'p': [pts]})
    stats['kept'] = len(feats)
    log(f'sidewalk {period}: {stats}')
    if len(feats) < 1000:
        raise RuntimeError(f'人行道資料量異常少（{len(feats)}），不覆蓋既有檔案')
    os.makedirs(out_dir, exist_ok=True)
    out = {'built': built or datetime.now(timezone.utc).strftime('%Y-%m-%d'), 'src': 'moi-sidewalk', 'period': period,
           'min_net': MIN_NET, 'features': feats, 'stats': stats}
    json.dump(out, open(os.path.join(out_dir, 'sidewalk.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    return stats


def fetch(url, timeout=600):
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'taipei-life-map'}), timeout=timeout).read()


if __name__ == '__main__':
    period, url = latest_zip(json.loads(fetch(API, 60)))
    print('download', period, url, flush=True)
    print(json.dumps(build(read_zip(fetch(url)), period=period), ensure_ascii=False))
