#!/usr/bin/env python3
"""都更案件：從財團法人都市更新研究發展基金會（URF）製作的 Google 我的地圖「臺北市都市更新地圖」
（https://www.ur.org.tw/classroom/map_view/11 內嵌）匯出 KML，產生 data/urcase.json。

三種資料：
- cases：已核定重建更新事業（優先劃定／自行劃定／迅行劃定／都市計畫四個圖層）與「中央及市府主導」
- units：自劃單元（只留「已核准」，已失效的只計數）——都發局官方圖層只有少數自劃單元，這裡補齊
- areas：更新地區（優先劃定／迅行劃定／都市計畫）
只當資訊顯示（圖層、估價位置分頁），不計入估價。資料來源標示：財團法人都市更新研究發展基金會。
"""
import json, os, re, sys, time, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_data as B

MID = '1HIJwAuZc21A7FnoPBaIWPARkF7g'
KML_URL = f'https://www.google.com/maps/d/kml?mid={MID}&forcekml=1'
SRC_PAGE = 'https://www.ur.org.tw/classroom/map_view/11'
CREDIT = '財團法人都市更新研究發展基金會'
NS = '{http://www.opengis.net/kml/2.2}'
# 圖層名稱 → 劃定依據
FOLDERS = [('優先劃定', 'pri'), ('自行劃定', 'self'), ('迅行劃定', 'fast'), ('都市計畫', 'plan'), ('中央及市府主導', 'pub')]
BASIS = {'pri': '優先劃定更新地區', 'self': '自行劃定更新單元', 'fast': '迅行劃定更新地區', 'plan': '都市計畫劃定', 'pub': '政府主導'}
STATUS = {'ok': '已核定', 'review': '權變審議中', 'build': '施工中', 'done': '已完工'}


def fetch(url=KML_URL, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (taipei-life-map)'})
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def status_of(s, built_name='', kind=''):
    s = (s or '').strip()
    if '完工' in s:
        return 'done'
    if '施工' in s:
        return 'build'
    if '審' in s:
        return 'review'
    if kind == 'pub':
        return 'done' if built_name else 'ok'
    return 'ok'


def ymd(s):
    """'2019/7/25'、'2012/05/18'、'2000' → '2019-07-25'、'2012-05-18'、'2000'"""
    m = re.match(r'\s*(\d{4})(?:[/.-](\d{1,2})(?:[/.-](\d{1,2}))?)?', s or '')
    if not m:
        return ''
    return '-'.join([m.group(1)] + [x.zfill(2) for x in m.groups()[1:] if x])


def year(s):
    m = re.search(r'(19|20)\d{2}', s or '')
    return int(m.group(0)) if m else None


def num(s):
    try:
        return round(float(str(s).replace(',', '').strip()))
    except ValueError:
        return None


def short_case(s):
    """「擬訂臺北市南港區玉成段二小段166地號等12筆土地都市更新事業計畫案」→「南港區玉成段二小段166地號等12筆」"""
    s = re.sub(r'^(擬訂|變更)?(臺|台)北市', '', (s or '').strip())
    s = re.sub(r'(土地)?(\(.*?\)|（.*?）)?(土地)?都市更新事業計畫.*$', '', s)
    return s.strip()


def rings_of(pm):
    out = []
    for lr in pm.iter(NS + 'LinearRing'):
        c = lr.find(NS + 'coordinates')
        r = []
        for tok in (c.text or '').split() if c is not None else []:
            p = tok.split(',')
            if len(p) < 2:
                continue
            q = [round(float(p[1]), 5), round(float(p[0]), 5)]
            if not r or r[-1] != q:
                r.append(q)
        if len(r) >= 4:
            out.append(r)
    return out


def parse(kml_bytes):
    root = ET.fromstring(kml_bytes)
    cases = []
    for folder in root.iter(NS + 'Folder'):
        fname = (folder.findtext(NS + 'name') or '').strip()
        kind = next((k for key, k in FOLDERS if key in fname), None)
        if not kind or (kind != 'pub' and '已核定' not in fname and '更新事業' not in fname):
            continue
        for pm in folder.findall(NS + 'Placemark'):
            rings = rings_of(pm)
            if not rings:
                continue
            d = {}
            for x in pm.iter(NS + 'Data'):
                d[x.get('name')] = (x.findtext(NS + 'value') or '').strip()
            name = (pm.findtext(NS + 'name') or '').strip() or d.get('建案案名') or d.get('完工案名') or ''
            if kind == 'pub':
                c = {'k': kind, 'n': name or d.get('位置', ''), 'no': d.get('編號', ''), 'dist': d.get('區別', ''),
                     's': status_of('', d.get('完工案名', ''), 'pub'), 'd': ymd(d.get('公告年度', '')), 'y': None,
                     'i': d.get('實施者') or d.get('得標廠商') or '', 'm': d.get('主導類型', ''), 'c': d.get('位置', ''),
                     'note': d.get('說明', ''), 'a': num(d.get('面積(㎡)', ''))}
            else:
                c = {'k': kind, 'n': name, 'no': d.get('編號', ''), 'dist': d.get('行政區', ''),
                     's': status_of(d.get('目前狀態', '')), 'd': ymd(d.get('事業計畫核定日期', '')), 'y': year(d.get('完工年度', '')),
                     'i': d.get('實施者', ''), 'm': d.get('實施方式', ''), 'c': short_case(d.get('案名', '')), 'note': '',
                     'a': num(d.get('基地面積', ''))}
            c = {k: v for k, v in c.items() if v not in ('', None)}
            c['r'] = rings
            cases.append(c)
    return cases


def parse_more(kml_bytes):
    """自劃單元（已核准）與更新地區"""
    root = ET.fromstring(kml_bytes)
    units, areas, lapsed = [], [], 0
    for folder in root.iter(NS + 'Folder'):
        fname = (folder.findtext(NS + 'name') or '').strip()
        is_unit = '自劃單元' in fname
        akind = next((k for key, k in FOLDERS if key in fname), None) if fname.startswith('更新地區') else None
        if not is_unit and not akind:
            continue
        for pm in folder.findall(NS + 'Placemark'):
            rings = rings_of(pm)
            if not rings:
                continue
            d = {x.get('name'): (x.findtext(NS + 'value') or '').strip() for x in pm.iter(NS + 'Data')}
            name = (pm.findtext(NS + 'name') or '').strip()
            if is_unit:
                if '失效' in d.get('失效與否', ''):
                    lapsed += 1
                    continue
                u = {'n': re.sub(r'^(臺|台)北市', '', re.sub(r'為更新單元$', '', name)), 'd': ymd(d.get('核准日期', '')), 'a': num(d.get('基地面積', ''))}
                u = {k: v for k, v in u.items() if v not in ('', None)}
                u['r'] = rings
                units.append(u)
            else:
                a = {'k': akind, 'n': name, 'd': ymd(d.get('公告日期', '')), 'c': d.get('計畫書名稱', ''), 'a': num(d.get('地區範圍面積', ''))}
                a = {k: v for k, v in a.items() if v not in ('', None)}
                a['r'] = rings
                areas.append(a)
    return units, areas, lapsed


def build(kml_bytes, out_dir=os.path.join(B.ROOT, 'data'), built=None, min_cases=300, log=print):
    cases = parse(kml_bytes)
    if len(cases) < min_cases:
        raise SystemExit(f'都更案件只有 {len(cases)} 件（少於 {min_cases}），可能是來源異常，不更新')
    units, areas, lapsed = parse_more(kml_bytes)
    cnt = {k: sum(c['s'] == k for c in cases) for k in STATUS}
    cnt.update({'unit': len(units), 'unit_lapsed': lapsed, 'area': len(areas)})
    out = {'built': built or datetime.now(timezone.utc).strftime('%Y-%m-%d'), 'src': 'urf-mymaps', 'source': SRC_PAGE, 'credit': CREDIT,
           'status': STATUS, 'basis': BASIS, 'count': cnt, 'cases': cases, 'units': units, 'areas': areas}
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'urcase.json'), 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(',', ':'))
    log(f'都更案件 {len(cases)} 件、自劃單元 {len(units)} 塊、更新地區 {len(areas)} 處 {cnt}')
    return out


if __name__ == '__main__':
    build(fetch())
