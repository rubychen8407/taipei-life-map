#!/usr/bin/env python3
"""產生 data/land.json：臺北市各「地段（小段）」的公告土地現值（地圖圖層「土地價值」）。

資料（皆為政府資料開放平臺，政府資料開放授權條款第 1 版）：
  - 臺北市數值地籍地段圖（dataset 157685，地政局土地開發總隊）：589 個小段的範圍，SHP、TWD97
  - 臺北市土地公告現值及公告地價（dataset 122058，地政局）：每筆地號的公告土地現值（元／㎡）。
    目前開放的最新一期是 110 年（全部地號）
  - 臺北市公告土地現值漲跌幅一覽表（dataset 132106）：各行政區每年的漲跌幅（%）
  - 臺北市公有土地資料（dataset 128228，每月更新）：公有地號的「當期」公告現值，當作沒有 110 年資料的小段的備援

計算：每個小段取地號公告現值的中位數。
  主要：110 年中位數 × 該行政區 111 年起每年漲跌幅連乘 → 推估當年度（est）
  備援：公有土地當期公告現值中位數（pub），用在 110 年資料不足的小段（例如重測後改名）
輸出：{'built','year','base_year','sections':[{'c','n','d','v'(元/㎡),'b'('est'|'pub'),'k'(地號數),'g'(當年漲跌%),'r':[[[lat,lon],..],..]}],'lo','hi','stats'}
"""
import csv, io, json, math, os, re, struct, sys, zipfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_data as B
from build_realprice import twd97, http

API = 'https://data.gov.tw/api/v2/rest/dataset/'
DS_SECT, DS_VALUE, DS_RATE, DS_PUB = 157685, 122058, 132106, 128228
MIN_EST, MIN_PUB = 5, 3                # 小段內至少幾筆地號才採用
PING = 3.305785                        # 1 坪 = 3.305785 ㎡


# ---------- 讀檔 ----------
def decode(raw):
    for enc in ('utf-8-sig', 'cp950'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode('cp950', errors='replace')


def read_shp(shp, dbf, encoding='utf-8'):
    """讀 Polygon SHP + DBF（不需要外部套件）。回傳 [(屬性 dict, [ring(TWD97 [(x,y)...])...])]"""
    recs = []
    n, hl, rl = struct.unpack('<I', dbf[4:8])[0], *struct.unpack('<HH', dbf[8:12])
    fields, o = [], 32
    while dbf[o] != 0x0D:
        name = dbf[o:o + 11].split(b'\0')[0].decode(encoding, errors='replace'); fields.append((name, dbf[o + 16])); o += 32
    for i in range(n):
        p = hl + i * rl + 1; row = {}
        for name, size in fields:
            row[name] = dbf[p:p + size].decode(encoding, errors='replace').strip(); p += size
        recs.append(row)
    out, o, i = [], 100, 0
    while o < len(shp) and i < len(recs):
        clen = struct.unpack('>I', shp[o + 4:o + 8])[0] * 2; c = o + 8
        st = struct.unpack('<i', shp[c:c + 4])[0]; rings = []
        if st in (5, 15, 25):
            nparts, npts = struct.unpack('<ii', shp[c + 36:c + 44])
            parts = list(struct.unpack(f'<{nparts}i', shp[c + 44:c + 44 + 4 * nparts])) + [npts]
            q = c + 44 + 4 * nparts
            pts = [struct.unpack('<dd', shp[q + 16 * k:q + 16 * k + 16]) for k in range(npts)]
            rings = [pts[parts[k]:parts[k + 1]] for k in range(nparts)]
        out.append((recs[i], rings)); o += 8 + clen; i += 1
    return out


def read_rates(text):
    """{行政區: {年: 漲跌%}}"""
    rows = list(csv.reader(io.StringIO(text)))
    years = [int(m.group(1)) if (m := re.match(r'\s*(\d+)年', h)) else None for h in rows[0]]
    out = {}
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        out[r[0].strip()] = {y: float(v) for y, v in zip(years, r) if y and v.strip() not in ('', '-')}
    return out


def read_values(text):
    """110 年全部地號：{(行政區, 段小段): [元/㎡...]}"""
    out = {}
    rd = csv.reader(io.StringIO(text)); next(rd, None)
    for r in rd:
        if len(r) < 5:
            continue
        try:
            v = float(r[4])
        except ValueError:
            continue
        if v > 0:
            out.setdefault((r[1].strip(), r[2].strip()), []).append(v)
    return out


def read_public(text):
    """公有土地：{段代碼: [當期元/㎡...]}"""
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        try:
            v = float(r.get('當期公告現值金額') or 0)
        except ValueError:
            continue
        if v > 0:
            out.setdefault((r.get('段代碼') or '').strip().zfill(4), []).append(v)
    return out


def median(xs):
    s = sorted(xs); n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


# ---------- 建置 ----------
def build(sections, values, base_year, rates, public, out_dir=os.path.join(B.ROOT, 'data'), built=None, min_sections=400, log=print):
    year = max((y for d in rates.values() for y in d), default=base_year)
    stats = {'sections': len(sections), 'est': 0, 'pub': 0, 'none': 0}
    out = []
    for rec, rings in sections:
        code, name, dist = rec.get('段代碼', '').zfill(4), rec.get('地段名', ''), rec.get('行政區', '')
        vs, pv = values.get((dist, name), []), public.get(code, [])
        rt = rates.get(dist) or rates.get('全市') or {}
        if len(vs) >= MIN_EST:
            f = 1.0
            for y in range(base_year + 1, year + 1):
                f *= 1 + rt.get(y, 0) / 100
            v, b, k = median(vs) * f, 'est', len(vs)
        elif len(pv) >= MIN_PUB:
            v, b, k = median(pv), 'pub', len(pv)
        else:
            stats['none'] += 1; continue
        stats[b] += 1
        rr = []
        for ring in rings:
            pts = [twd97(x, y) for x, y in ring]
            pts = [[B.r5(a), B.r5(c)] for a, c in B.simplify(pts, 4)]
            if len(pts) >= 4:
                rr.append(pts)
        if not rr:
            continue
        out.append({'c': code, 'n': name, 'd': dist, 'v': round(v), 'b': b, 'k': k, 'g': rt.get(year), 'r': rr})
    if len(out) < min_sections:
        raise RuntimeError(f'只算出 {len(out)} 個小段（少於 {min_sections}），不更新 {stats}')
    vals = sorted(s['v'] for s in out)
    res = {'built': built or datetime.now(timezone.utc).strftime('%Y-%m-%d'), 'year': year, 'base_year': base_year,
           'src': 'taipei-land', 'lo': vals[int(len(vals) * .05)], 'hi': vals[int(len(vals) * .95)], 'sections': out, 'stats': stats}
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, 'land.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, separators=(',', ':'))
    log(f'土地價值：{len(out)} 個小段（{year} 年），{os.path.getsize(p) / 1e6:.2f} MB；{stats}；'
        f'中位數 {median(vals) * PING / 1e4:.0f} 萬／坪')
    return res


# ---------- 下載 ----------
def dists(ds):
    m = json.loads(http(API + str(ds)))
    return (m.get('result') or m).get('distribution', [])


def fetch(log=print):
    shp_url = next(d['resourceDownloadUrl'] for d in dists(DS_SECT) if (d.get('resourceFormat') or '').upper() in ('SHP', 'ZIP'))
    z = zipfile.ZipFile(io.BytesIO(http(shp_url)))
    nm = {os.path.splitext(n)[1].lower(): n for n in z.namelist()}
    enc = z.read(nm['.cpg']).decode().strip().lower().replace('utf_8', 'utf-8') if '.cpg' in nm else 'utf-8'
    sections = read_shp(z.read(nm['.shp']), z.read(nm['.dbf']), 'utf-8' if 'utf' in enc else 'cp950')
    best = None
    for d in dists(DS_VALUE):
        m = re.search(r'(\d+)年公告現值', d.get('resourceDescription') or '')
        if m and (best is None or int(m.group(1)) > best[0]):
            best = (int(m.group(1)), d['resourceDownloadUrl'])
    log(f'地段 {len(sections)} 個；公告現值用 {best[0]} 年')
    values = read_values(decode(http(best[1])))
    rates = read_rates(decode(http(next(d['resourceDownloadUrl'] for d in dists(DS_RATE) if (d.get('resourceFormat') or '').upper() == 'CSV'))))
    public = read_public(decode(http(next(d['resourceDownloadUrl'] for d in dists(DS_PUB) if (d.get('resourceFormat') or '').upper() == 'CSV'))))
    return sections, values, best[0], rates, public


def main():
    build(*fetch())


if __name__ == '__main__':
    main()
