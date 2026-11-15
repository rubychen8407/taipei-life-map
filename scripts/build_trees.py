#!/usr/bin/env python3
"""用臺北市「行道樹分布圖」產生 data/trees.json（地圖上的行道樹圖層）。

資料：政府資料開放平臺 dataset 128274（臺北市政府工務局公園路燈工程管理處），
     CSV 欄位 TreeID、Dist、Region、RegionRemark、TreeType、Diameter、TreeHeight、
     SurveyDate、TWD97X、TWD97Y、UpdDate。座標是 TWD97 二度分帶，轉成 WGS84。
只收行道樹（不含公園樹木）。app 勾選圖層「行道樹」時才下載。

輸出（壓縮格式，減少下載量）：
  {'built','src':'taipei-tree','n','base':[lat0,lon0],'sp':[樹種],'rd':[路段],
   'a':[dlat,dlon,樹種序號,路段序號,樹高×10,胸徑cm, ...]}   # dlat/dlon 單位 1e-5 度，相對 base
"""
import csv, io, json, os, sys, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_data as B
from build_realprice import twd97

URL = 'https://tppkl.blob.core.windows.net/blobfs/TaipeiTree.csv'
BBOX = (24.95, 121.43, 25.22, 121.68)          # 臺北市外框（略放寬）
FIELDS = 6                                     # a 陣列每棵樹的欄位數（與 index.html 一致）


def fetch(url=URL, tries=3):
    last = None
    for _ in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'taipei-life-map'}), timeout=300) as r:
                return r.read().decode('utf-8-sig')
        except Exception as e:                  # noqa: BLE001
            last = e
    raise RuntimeError(f'下載行道樹資料失敗：{last}')


def num(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def build(text, out_dir=os.path.join(B.ROOT, 'data'), built=None, min_trees=50000, log=print):
    rows = csv.DictReader(io.StringIO(text))
    seen, trees, stats = set(), [], {'rows': 0, 'dup': 0, 'nocoord': 0, 'outside': 0}
    for r in rows:
        stats['rows'] += 1
        tid = (r.get('TreeID') or '').strip()
        if tid and tid in seen:
            stats['dup'] += 1; continue
        seen.add(tid)
        x, y = num(r.get('TWD97X')), num(r.get('TWD97Y'))
        if not x or not y:
            stats['nocoord'] += 1; continue
        la, lo = twd97(x, y)
        if not (BBOX[0] <= la <= BBOX[2] and BBOX[1] <= lo <= BBOX[3]):
            stats['outside'] += 1; continue
        road = ((r.get('Region') or '').strip() + ' ' + (r.get('RegionRemark') or '').strip()).strip()
        h, d = num(r.get('TreeHeight')), num(r.get('Diameter'))
        trees.append((la, lo, (r.get('TreeType') or '').strip() or '未知樹種', road,
                      max(0, min(999, round((h or 0) * 10))), max(0, min(999, round(d or 0)))))
    if len(trees) < min_trees:
        raise RuntimeError(f'只讀到 {len(trees)} 棵行道樹（少於 {min_trees}），不更新')
    trees.sort(key=lambda t: (round(t[0], 3), t[1]))
    lat0, lon0 = min(t[0] for t in trees), min(t[1] for t in trees)
    cnt = {}
    for t in trees:
        cnt[t[2]] = cnt.get(t[2], 0) + 1
    sp = sorted(cnt, key=lambda s: (-cnt[s], s))   # 依數量排序，常見樹種序號小（JSON 較短）
    rd = sorted({t[3] for t in trees})
    si, ri = {s: i for i, s in enumerate(sp)}, {s: i for i, s in enumerate(rd)}
    a = []
    for la, lo, s, road, h, d in trees:
        a += [round((la - lat0) * 1e5), round((lo - lon0) * 1e5), si[s], ri[road], h, d]
    out = {'built': built or datetime.now(timezone.utc).strftime('%Y-%m-%d'), 'src': 'taipei-tree', 'n': len(trees),
           'base': [round(lat0, 5), round(lon0, 5)], 'sp': sp, 'rd': rd, 'a': a, 'stats': stats}
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, 'trees.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    log(f'行道樹 {len(trees)} 棵、{len(sp)} 種，{os.path.getsize(p) / 1e6:.1f} MB；{stats}')
    return out


def main():
    build(fetch())


if __name__ == '__main__':
    main()
