#!/usr/bin/env python3
"""用內政部實價登錄產生中古屋估價資料：data/realprice.json（成交與租賃點位）、data/valuation.json（模型、市場指標、回測）。

資料：
  實價登錄批次 CSV（政府資料開放授權）：https://plvr.land.moi.gov.tw/DownloadOpenData
    本期（每月 1、11、21 日）：Download?type=zip&fileName=lvr_landcsv.zip
    各季：DownloadSeason?season=115S3&type=zip&fileName=lvr_landcsv.zip
    臺北市：a_lvr_land_a.csv（買賣）、a_lvr_land_c.csv（租賃）
  臺北市門牌位置數值資料（民政局，data.gov.tw 155472，TWD97，每月）：把門牌轉成座標。
  生活機能：既有的 data/city.json。

估價（與 index.html 的 estimate() 相同，改這裡也要改那裡）：
  ln 單價 = b0 + b·[屋齡, 屋齡², 樓層比, ln 坪數, 一樓, 頂樓, 生活機能 S] + 型態 + 行政區 + 季
  物件單價第 q 分位 = 模型預測 × exp(附近可比案例殘差的第 q 分位)
"""
import csv, io, json, math, os, re, sys, time, urllib.request, zipfile
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_data as B

BASE = 'https://plvr.land.moi.gov.tw/'
ADDR_API = 'https://data.gov.tw/api/v2/rest/dataset/155472'
PING = 3.305785            # 平方公尺／坪
TYPES = ['住宅大樓', '華廈', '公寓']   # 型態代碼 0、1、2
POI = ['park', 'school', 'sport', 'market', 'bus', 'mrt']   # 生活機能分數用的類別（等權重）
KEEP_MONTHS = 24           # 前端可比案例用近 24 個月
FIT_MONTHS = 36            # 模型用近 36 個月
RADII = [1200]             # 可比案例最遠距離（公尺）
MIN_COMPS = (25, 10)       # 取最相似的 25 筆；少於 10 筆就不給出價
AGE_BAND = 12              # 屋齡差距上限（年）
SIZE_BAND = (0.5, 2.0)     # 坪數比例上下限
# 相似度（越小越像）：距離每 300 公尺、屋齡每差 5 年、坪數每差 30%（對數）各算 1 分，加上樓層差異。
# 回測（2026-11 真實資料）：比原本「半徑內全部」的中位數誤差 8.4% → 7.7%
BAND = 0.4              # 保守／積極 = 合理分位 ∓ 0.4（回測：P10–P90 約涵蓋 75% 的實際成交）
SHIFT = 0.15           # 市場燈號 H（−1 買方～+1 賣方）讓合理分位移動 0.15·H
BAD_NOTE = re.compile(r'親友|員工|共有人|特殊關係|親等|債權|債務|急買|急賣|瑕疵|凶宅|地上權|持分|毛胚|協議價購|政府機關|公共設施保留地|法拍|塔位|畸零|無償|贈與')
FW = str.maketrans('０１２３４５６７８９－', '0123456789-')
CN = {'零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


# ---------- 小工具 ----------
def cn_num(s):
    """'十二' → 12、'四' → 4、'二十一' → 21、'12' → 12；看不懂回傳 None"""
    s = (s or '').translate(FW).strip()
    if s.isdigit():
        return int(s)
    if not s or any(ch not in CN and ch not in '十百' for ch in s):
        return None
    n, cur = 0, 0
    for ch in s:
        if ch == '百':
            n += (cur or 1) * 100; cur = 0
        elif ch == '十':
            n += (cur or 1) * 10; cur = 0
        else:
            cur = CN[ch]
    return n + cur


def floor_of(s):
    """移轉層次 → 樓層（只接受單一地上樓層；'四層'、'四層，陽台' 可，'一層，騎樓'、'地下一層'、'三層，四層' 不可）"""
    parts = [p for p in re.split(r'[，,、\s]+', (s or '').strip()) if p]
    fl = [p for p in parts if p.endswith('層')]
    if len(fl) != 1 or '地下' in fl[0] or any(re.search(r'騎樓|地下|夾層|見其他|電梯樓梯間|屋頂', p) for p in parts):
        return None
    return cn_num(fl[0][:-1])


def roc_date(s):
    """'1150902' → date(2026, 9, 2)"""
    s = (s or '').strip()
    if not s.isdigit() or len(s) < 6:
        return None
    y, m, d = int(s[:-4]) + 1911, int(s[-4:-2]), int(s[-2:])
    try:
        return date(y, max(1, m), max(1, d))
    except ValueError:
        return None


def ym(d):
    return d.year * 12 + d.month - 1


def ym_str(n):
    return f'{n // 12}-{n % 12 + 1:02d}'


def quantile(xs, q):
    """線性內插分位數（與 index.html 的 quantile() 相同）"""
    xs = sorted(xs)
    if not xs:
        return None
    p = (len(xs) - 1) * q; lo = int(math.floor(p)); hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (p - lo)


def dist_m(a, b):
    return math.hypot((a[0] - b[0]) * 110574.0, (a[1] - b[1]) * 111320.0 * math.cos(math.radians(25.05)))


def twd97(x, y):
    """TWD97 二度分帶（TM2，中央經線 121°）→ WGS84 (lat, lon)"""
    a, b, lon0, k0, dx = 6378137.0, 6356752.314245, math.radians(121), 0.9999, 250000.0
    e = math.sqrt(1 - (b / a) ** 2); x -= dx
    mu = (y / k0) / (a * (1 - e ** 2 / 4 - 3 * e ** 4 / 64 - 5 * e ** 6 / 256))
    e1 = (1 - math.sqrt(1 - e ** 2)) / (1 + math.sqrt(1 - e ** 2))
    fp = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
          + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    e2 = (e * a / b) ** 2; c1 = e2 * math.cos(fp) ** 2; t1 = math.tan(fp) ** 2
    r1 = a * (1 - e ** 2) / (1 - e ** 2 * math.sin(fp) ** 2) ** 1.5; n1 = a / math.sqrt(1 - e ** 2 * math.sin(fp) ** 2)
    d = x / (n1 * k0)
    lat = fp - (n1 * math.tan(fp) / r1) * (d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * e2) * d ** 4 / 24
                                           + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 3 * c1 ** 2 - 252 * e2) * d ** 6 / 720)
    lon = lon0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6 + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * e2 + 24 * t1 ** 2) * d ** 5 / 120) / math.cos(fp)
    return math.degrees(lat), math.degrees(lon)


# ---------- 門牌 → 座標 ----------
SEC = re.compile(r'([一二三四五六七八九十]+)段')
ADDR = re.compile(r'^(?P<road>.+?(?:路|街|大道)(?:\d+段)?)?(?P<lane>\d+巷)?(?P<alley>\d+弄)?(?P<no>\d+(?:之\d+)?號)')


def norm_addr(s):
    """'臺北市大同區昌吉街６１巷９弄１９號四樓' → ('昌吉街', '61巷', '9弄', '19號')；看不懂回傳 None"""
    s = re.sub(r'\s+', '', (s or '').translate(FW)).replace('台北市', '臺北市')
    s = re.sub(r'^臺北市', '', s); s = re.sub(r'^.{1,3}?區', '', s, count=1)
    s = SEC.sub(lambda m: f'{cn_num(m.group(1))}段', s)
    m = ADDR.match(s)
    if not m or not (m.group('road') or m.group('lane')):
        return None
    return (m.group('road') or '', m.group('lane') or '', m.group('alley') or '', m.group('no'))


class AddressIndex:
    """臺北市門牌位置數值資料的索引：完整門牌 → 同號（去掉「之」）→ 同路同巷弄最接近的號"""

    def __init__(self):
        self.exact, self.lane = {}, {}

    def add(self, road, area, lane, alley, no, x, y):
        k = norm_addr((road or area or '') + (lane or '') + (alley or '') + (no or ''))
        if not k or k in self.exact:
            return
        try:
            p = twd97(float(x), float(y))
        except ValueError:
            return
        self.exact[k] = p
        self.lane.setdefault(k[:3], []).append((int(re.match(r'\d+', k[3]).group()), p))

    def load_csv(self, f):
        r = csv.reader(f); next(r, None)
        for row in r:
            if len(row) >= 11:
                self.add(row[4], row[5], row[6], row[7], row[8], row[9], row[10])
        return self

    def locate(self, addr):
        """回傳 ((lat, lon), 精度)；精度 0 = 完整門牌、1 = 同號、2 = 同巷弄鄰近門牌；找不到回傳 (None, None)"""
        k = norm_addr(addr)
        if not k:
            return None, None
        if k in self.exact:
            return self.exact[k], 0
        base = k[:3] + (re.sub(r'之\d+', '', k[3]),)
        if base in self.exact:
            return self.exact[base], 1
        n = int(re.match(r'\d+', k[3]).group()); c = self.lane.get(k[:3])
        if c:
            same = [x for x in c if x[0] % 2 == n % 2] or c
            best = min(same, key=lambda x: abs(x[0] - n))
            if abs(best[0] - n) <= 20:
                return best[1], 2
        return None, None


# ---------- 生活機能分數 ----------
class PoiIndex:
    """每一類設施的點（中心＋面的頂點）放進 500 公尺的格子，查最近距離"""
    CELL = 0.005

    def __init__(self, layers):
        self.grid = {}
        for lid in POI:
            g = self.grid[lid] = {}
            for f in layers.get(lid, []):
                for p in [f['c']] + [q for ring in f.get('p', []) for q in ring]:
                    g.setdefault((int(p[0] / self.CELL), int(p[1] / self.CELL)), []).append(p)

    def nearest(self, lid, pt):
        g = self.grid[lid]; ci, cj = int(pt[0] / self.CELL), int(pt[1] / self.CELL); best = 1e9
        for ring in range(0, 5):
            for i in range(ci - ring, ci + ring + 1):
                for j in range(cj - ring, cj + ring + 1):
                    if max(abs(i - ci), abs(j - cj)) == ring:
                        for p in g.get((i, j), ()):
                            d = dist_m(pt, p)
                            if d < best:
                                best = d
            if best < ring * 500:
                break
        return best

    def score(self, pt):
        """S：每類依步行分鐘給分（10 分鐘內 1 分、線性遞減到 20 分鐘 0 分），各類平均。與 index.html 的 poiScore() 相同"""
        return sum(max(0.0, min(1.0, (20 - self.nearest(lid, pt) * 1.25 / 80) / 10)) for lid in POI) / len(POI)


# ---------- 讀實價登錄 CSV ----------
def read_csv(z, name):
    if name not in z.namelist():
        return []
    rows = list(csv.reader(io.StringIO(z.read(name).decode('utf-8-sig', 'replace'))))
    if len(rows) < 3:
        return []
    return [dict(zip(rows[0], r)) for r in rows[2:] if len(r) >= len(rows[0]) - 2]   # 第 2 列是英文欄名


def num(v):
    try:
        return float(str(v).replace(',', '') or 0)
    except ValueError:
        return 0.0


def parse_sales(rows, stats=None):
    """買賣主檔 → 乾淨的中古住宅成交。每筆：id、區、門牌、成交日、型態、坪數、屋齡、樓層、總樓層、單價（萬／坪）、總價（萬）"""
    out = []; st = stats if stats is not None else {}
    def skip(why):
        st[why] = st.get(why, 0) + 1
    for r in rows:
        st['rows'] = st.get('rows', 0) + 1
        if not (r.get('交易標的') or '').startswith('房地'):
            skip('not_house'); continue
        t = next((i for i, n in enumerate(TYPES) if (r.get('建物型態') or '').startswith(n)), None)
        if t is None:
            skip('type'); continue
        if (r.get('主要用途') or '') != '住家用':
            skip('use'); continue
        if BAD_NOTE.search(r.get('備註') or ''):
            skip('special'); continue
        d, built = roc_date(r.get('交易年月日')), roc_date(r.get('建築完成年月'))
        fl, tot = floor_of(r.get('移轉層次')), cn_num((r.get('總樓層數') or '').replace('層', ''))
        if not d or not built or not fl or not tot or fl > tot:
            skip('incomplete'); continue
        age = (d - built).days / 365.25
        if age < 1:                                   # 新成屋／預售不算中古屋
            skip('new'); continue
        price, area = num(r.get('總價元')), num(r.get('建物移轉總面積平方公尺'))
        pk_price, pk_area = num(r.get('車位總價元')), num(r.get('車位移轉總面積平方公尺') or r.get('車位移轉總面積(平方公尺)'))
        has_pk = '車位' in (r.get('交易標的') or '') or pk_area > 0
        if has_pk and (pk_price <= 0 or pk_area <= 0):  # 有車位但沒拆價：單價會失真
            skip('parking_unsplit'); continue
        ping = (area - pk_area) / PING
        if ping < 5 or ping > 150 or price - pk_price <= 0:
            skip('size'); continue
        out.append({'id': r.get('編號') or f"{r.get('土地位置建物門牌')}|{r.get('交易年月日')}|{price}", 'dist': (r.get('鄉鎮市區') or '').strip(),
                    'addr': r.get('土地位置建物門牌') or '', 'ym': ym(d), 't': t, 'ping': ping, 'age': age, 'fl': fl, 'tot': tot,
                    'up': (price - pk_price) / 1e4 / ping, 'total': price / 1e4, 'pk': pk_price / 1e4})
    return out


def parse_rents(rows):
    """租賃主檔 → 整戶出租的住宅。每筆：id、門牌、起租月、坪數、月租（元）"""
    out = []
    for r in rows:
        if (r.get('交易標的') or '') != '租賃房屋' or (r.get('主要用途') or '') != '住家用' or '整棟' not in (r.get('出租型態') or '整棟'):
            continue
        if not any((r.get('建物型態') or '').startswith(n) for n in TYPES) or re.search(r'社會住宅|包租|代管|親友|員工', r.get('備註') or ''):
            continue
        d, rent, area = roc_date(r.get('租賃年月日')), num(r.get('總額元')), num(r.get('建物總面積平方公尺'))
        ping = area / PING
        if not d or rent < 5000 or rent > 300000 or ping < 5 or ping > 150:
            continue
        out.append({'id': r.get('編號') or f"{r.get('土地位置建物門牌')}|{r.get('租賃年月日')}", 'addr': r.get('土地位置建物門牌') or '', 'ym': ym(d), 'ping': ping, 'rent': rent})
    return out


# ---------- 模型 ----------
def solve(A, b):
    """高斯消去法解 Ax=b（A 會被改動）"""
    n = len(b)
    for i in range(n):
        p = max(range(i, n), key=lambda r: abs(A[r][i])); A[i], A[p] = A[p], A[i]; b[i], b[p] = b[p], b[i]
        piv = A[i][i]
        if abs(piv) < 1e-12:
            continue
        for r in range(i + 1, n):
            f = A[r][i] / piv
            if f:
                for c in range(i, n):
                    A[r][c] -= f * A[i][c]
                b[r] -= f * b[i]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (b[i] - sum(A[i][c] * x[c] for c in range(i + 1, n))) / A[i][i] if abs(A[i][i]) >= 1e-12 else 0.0
    return x


def quarter(n):
    return n // 3


def base_x(r):
    """與 index.html 的 modelX() 相同的特徵順序"""
    return [1.0, r['age'], r['age'] ** 2 / 100, r['fl'] / r['tot'], math.log(r['ping']), 1.0 if r['fl'] == 1 else 0.0,
            1.0 if r['fl'] == r['tot'] else 0.0, r['s'], 1.0 if r['t'] == 1 else 0.0, 1.0 if r['t'] == 2 else 0.0,
            1.0 if r['t'] == 2 and r['fl'] >= 4 else 0.0]   # 無電梯公寓的四、五樓


FEATS = ['const', 'age', 'age2', 'floor_ratio', 'ln_ping', 'first_floor', 'top_floor', 'poi', 'type_huasha', 'type_apt', 'apt_high']


def fit(recs):
    """全市一個模型，行政區與季用虛擬變數（資料較少的區也穩定）。回傳 {'b', 'dist', 'q'}"""
    dists = sorted({r['dist'] for r in recs}); qs = sorted({quarter(r['ym']) for r in recs})
    di = {d: i for i, d in enumerate(dists[1:])}; qi = {q: i for i, q in enumerate(qs[1:])}
    nb = len(FEATS); n = nb + len(di) + len(qi)
    A = [[0.0] * n for _ in range(n)]; bb = [0.0] * n
    for r in recs:
        idx = [(i, v) for i, v in enumerate(base_x(r)) if v]
        if r['dist'] in di:
            idx.append((nb + di[r['dist']], 1.0))
        if quarter(r['ym']) in qi:
            idx.append((nb + len(di) + qi[quarter(r['ym'])], 1.0))
        y = math.log(r['up'])
        for i, vi in idx:
            bb[i] += vi * y
            for j, vj in idx:
                A[i][j] += vi * vj
    for i in range(n):
        A[i][i] += 1e-6
    x = solve(A, bb)
    return {'b': x[:nb], 'dist': {dists[0]: 0.0, **{d: x[nb + i] for d, i in di.items()}},
            'q': {str(qs[0]): 0.0, **{str(q): x[nb + len(di) + i] for q, i in qi.items()}}}


def predict_ln(model, r, q=None):
    """ln 單價；q 沒給就用成交當季，季別不在模型裡時用最後一季"""
    qk = str(quarter(r['ym']) if q is None else q)
    tq = model['q'].get(qk)
    if tq is None:
        tq = model['q'][max(model['q'], key=int)]
    return sum(b * v for b, v in zip(model['b'], base_x(r))) + model['dist'].get(r['dist'], 0.0) + tq


def now_effect(model):
    """「現在」的時間效果：最近兩季平均（最後一季筆數少，單看一季太跳）"""
    ks = sorted(model['q'], key=int)[-2:]
    return sum(model['q'][k] for k in ks) / len(ks)


def floor_pen(s, r):
    """樓層差異：無電梯公寓看實際樓層（爬樓梯），其他看樓層比；一樓和非一樓差很多"""
    one = 1.0 if (s['fl'] == 1) != (r['fl'] == 1) else 0.0
    if s['t'] == 2:
        return one * 1.5 + 0.4 * min(3, abs(s['fl'] - r['fl']))
    return one + abs(s['fl'] / s['tot'] - r['fl'] / r['tot']) / 0.3


def similarity(s, r, d):
    """與 index.html 的 similarity() 相同"""
    return d / 300 + abs(r['age'] - s['age']) / 5 + abs(math.log(r['ping'] / s['ping'])) / 0.3 + floor_pen(s, r)


def comps_for(subject, recs, now_ym):
    """可比案例：同型態、近 24 個月、1.2 公里內、屋齡 ±12 年、坪數 0.5～2 倍，依相似度取最像的 25 筆。回傳 (案例, 最遠距離)"""
    c = []
    for r in recs:
        if r is subject or r['t'] != subject['t'] or abs(r['age'] - subject['age']) > AGE_BAND or now_ym - r['ym'] >= KEEP_MONTHS:
            continue
        if not SIZE_BAND[0] <= r['ping'] / subject['ping'] <= SIZE_BAND[1]:
            continue
        d = dist_m(subject['pt'], r['pt'])
        if d <= RADII[-1]:
            c.append((similarity(subject, r, d), d, r))
    c.sort(key=lambda x: x[0]); c = c[:MIN_COMPS[0]]
    return [r for _, _, r in c], (max(d for _, d, _ in c) if c else RADII[-1])


def estimate(subject, model, recs, now_ym, tq=None):
    """回傳 {'ok', 'n', 'radius', 'base'（模型單價）, 'q'：{分位: 單價}, 'poi'：位置加減分}"""
    comps, rad = comps_for(subject, recs, now_ym)
    s = dict(subject, ym=now_ym)
    base = math.exp(sum(b * v for b, v in zip(model['b'], base_x(s))) + model['dist'].get(s['dist'], 0.0) + (now_effect(model) if tq is None else tq))
    out = {'ok': len(comps) >= MIN_COMPS[1], 'n': len(comps), 'radius': round(rad), 'base': base, 'q': {}, 'poi': 0.0}
    if comps:
        res = [r['e'] for r in comps]
        for q in (0.1, 0.15, 0.25, 0.5, 0.75, 0.85, 0.9):
            out['q'][q] = base * math.exp(quantile(res, q))
        sbar = sum(r['s'] for r in comps) / len(comps)
        out['poi'] = max(-0.08, min(0.08, math.exp(model['b'][FEATS.index('poi')] * (subject['s'] - sbar)) - 1))
    return out


def backtest(recs, test_months=3):
    """用最後 3 個「資料夠多」的月份當測試集，只用更早的資料訓練與找可比案例"""
    by = {}
    for r in recs:
        by[r['ym']] = by.get(r['ym'], 0) + 1
    full = sorted(m for m, n in by.items() if n >= 0.4 * (sorted(by.values())[len(by) // 2]))   # 最近一兩個月申報還沒進來，筆數太少不算
    cut = full[-test_months]
    train = [r for r in recs if r['ym'] < cut]; test = [r for r in recs if r['ym'] >= cut]
    m = fit(train)
    for r in train:
        r['_e'] = r.get('e'); r['e'] = math.log(r['up']) - predict_ln(m, r)
    tq = now_effect(m); ape, hit50, hit70, hit80, no = [], 0, 0, 0, 0; per = {}
    for r in test:
        est = estimate(r, m, train, cut, tq)
        if not est['ok']:
            no += 1; continue
        a = abs(est['q'][0.5] - r['up']) / r['up']; ape.append(a); per.setdefault(r['dist'], []).append(a)
        hit50 += est['q'][0.25] <= r['up'] <= est['q'][0.75]; hit70 += est['q'][0.15] <= r['up'] <= est['q'][0.85]; hit80 += est['q'][0.1] <= r['up'] <= est['q'][0.9]
    for r in train:
        r['e'] = r.pop('_e')
    n = len(ape) or 1
    return {'test_from': ym_str(cut), 'n': len(ape), 'no_estimate': no, 'mdape': quantile(ape, 0.5), 'p80_ape': quantile(ape, 0.8),
            'hit_25_75': hit50 / n, 'hit_15_85': hit70 / n, 'hit_10_90': hit80 / n,
            'by_district': {d: {'n': len(v), 'mdape': round(quantile(v, 0.5), 4)} for d, v in sorted(per.items())}}


def market(recs, model, now_ym, fitted=None):
    """各區市場指標：交易量年增率（避開申報還沒到齊的最近 4 個月）、品質調整後的價格動能（最近兩季 vs 前兩季）"""
    # 實價登錄有申報落差：成交後 2～4 個月資料才大致到齊（實測最近 4 個月的筆數明顯偏少），所以看「5～7 個月前」那三個月
    out = {}; a0, a1 = now_ym - 7, now_ym - 5
    qs = sorted(model['q'], key=int); mom = None
    if len(qs) >= 4:
        mom = math.exp((model['q'][qs[-1]] + model['q'][qs[-2]]) / 2 - (model['q'][qs[-3]] + model['q'][qs[-4]]) / 2) - 1
    keys = sorted({r['dist'] for r in recs}) + ['全市'] + sorted({f"{r['dist']}|{r['t']}" for r in recs})
    for d in keys:
        dd, tt = (d.split('|')[0], int(d.split('|')[1])) if '|' in d else (d, None)
        rs = [r for r in recs if (dd == '全市' or r['dist'] == dd) and (tt is None or r['t'] == tt)]
        cnt = lambda lo, hi: sum(1 for r in rs if lo <= r['ym'] <= hi)
        cur = cnt(a0, a1); prev = cnt(a0 - 12, a1 - 12)
        # 和自己過去三年比：同樣長度（3 個月）的 10 個區間，平均與標準差 → z 分數（各區的「平常」不一樣）
        hist = [cnt(a0 - 3 * k, a1 - 3 * k) for k in range(1, 11)]
        mu = sum(hist) / len(hist); sd = (sum((h - mu) ** 2 for h in hist) / (len(hist) - 1)) ** 0.5
        months = {}
        for r in rs:
            months.setdefault(r['ym'], []).append(r['up'])
        series = [[ym_str(m), len(months.get(m, [])), round(quantile(months[m], 0.5), 1) if months.get(m) else None] for m in range(now_ym - 23, now_ym + 1)]
        out[d] = {'vol': cur, 'vol_prev': prev, 'vol_yoy': (cur / prev - 1) if prev >= 10 else None, 'vol_months': [ym_str(a0), ym_str(a1)],
                  'vol_avg': round(mu, 1), 'vol_z': round((cur - mu) / sd, 3) if mu >= 10 and sd > 0 else None,
                  'price_mom': mom, 'price_mom_local': False, 'series': series}
        # 各區價格動能：全市的季效果 ×（這區最近兩季殘差中位數 − 前兩季）；兩期各至少 30 筆才算，否則用全市
        # 同區同型態（例如「松山區|1」= 松山區華廈）門檻 20 筆
        if mom is not None and fitted and d != '全市':
            seg = [r for r in fitted if r['dist'] == dd and (tt is None or r['t'] == tt)]
            e1 = [r['e'] for r in seg if str(quarter(r['ym'])) in qs[-2:]]
            e0 = [r['e'] for r in seg if str(quarter(r['ym'])) in qs[-4:-2]]
            need = 30 if tt is None else 20
            if len(e1) >= need and len(e0) >= need:
                out[d]['price_mom'] = (1 + mom) * math.exp(quantile(e1, .5) - quantile(e0, .5)) - 1; out[d]['price_mom_local'] = True
    return out


# ---------- 下載 ----------
def http(url, timeout=300, tries=3):
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 taipei-life-map'}), timeout=timeout).read()
        except Exception as e:
            print(f'  retry {url} {e}'); time.sleep(5 * (i + 1))
    raise RuntimeError('下載失敗 ' + url)


def seasons(today, n):
    """最近 n 個已發布的季（'115S3'…），由舊到新"""
    y, q = today.year - 1911, (today.month - 1) // 3 + 1
    out = []
    for _ in range(n):
        q -= 1
        if q == 0:
            y -= 1; q = 4
        out.append(f'{y}S{q}')
    return out[::-1]


def fetch_all(today, log=print):
    sales, rents = {}, {}; stats = {}
    urls = [BASE + f'DownloadSeason?season={s}&type=zip&fileName=lvr_landcsv.zip' for s in seasons(today, 16)]
    # 本季還沒有季檔：補上本季已發布的各期（每月 1、11、21 日）與最新一期
    q0 = date(today.year, (today.month - 1) // 3 * 3 + 1, 1); d = q0
    while d <= today:
        for day in (1, 11, 21):
            p = date(d.year, d.month, day)
            if q0 < p <= today:
                urls.append(BASE + f'DownloadHistory?type=history&fileName={p:%Y%m%d}')
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    urls.append(BASE + 'Download?type=zip&fileName=lvr_landcsv.zip')
    for u in urls:
        try:
            z = zipfile.ZipFile(io.BytesIO(http(u)))
        except Exception as e:
            if 'DownloadSeason' in u:
                raise
            log('  skip', u, e); continue
        s = parse_sales(read_csv(z, 'a_lvr_land_a.csv'), stats); r = parse_rents(read_csv(z, 'a_lvr_land_c.csv'))
        for x in s:
            sales[x['id']] = x
        for x in r:
            rents[x['id']] = x
        log(f'  {u.split("?")[1][:40]}: 買賣 {len(s)}、租賃 {len(r)}')
    return list(sales.values()), list(rents.values()), stats


def fetch_addresses(log=print):
    meta = json.loads(http(ADDR_API, 60)); r = meta.get('result') or meta
    url = next(d['resourceDownloadUrl'] for d in r['distribution'] if (d.get('resourceFormat') or '').upper() == 'CSV')
    raw = http(url, 900); log(f'  門牌資料 {len(raw) / 1e6:.0f} MB')
    return AddressIndex().load_csv(io.StringIO(raw.decode('utf-8-sig', 'replace')))


# ---------- 主流程 ----------
def build(sales, rents, addr, layers, out_dir=os.path.join(B.ROOT, 'data'), today=None, built=None, log=print, min_sales=3000, max_mdape=0.12):
    today = today or date.today(); now_ym = max(r['ym'] for r in sales); poi = PoiIndex(layers)
    stats = {'sales_clean': len(sales), 'located': 0, 'precision': [0, 0, 0]}
    recs = []
    for r in sales:
        if now_ym - r['ym'] >= FIT_MONTHS + 12:
            continue
        pt, prec = addr.locate(r['addr'])
        if not pt:
            continue
        stats['precision'][prec] += 1; r['pt'] = pt; r['s'] = poi.score(pt); recs.append(r)
    stats['located'] = len(recs)
    # 極端值：各區單價前後 1%
    keep = []
    for d in {r['dist'] for r in recs}:
        rs = [r for r in recs if r['dist'] == d]; lo, hi = quantile([r['up'] for r in rs], 0.01), quantile([r['up'] for r in rs], 0.99)
        keep += [r for r in rs if lo <= r['up'] <= hi]
    all_recs = keep; recs = [r for r in keep if now_ym - r['ym'] < FIT_MONTHS]
    if len(recs) < min_sales:
        raise RuntimeError(f'可用成交只有 {len(recs)} 筆，少於 {min_sales}，不更新資料')
    model = fit(recs)
    for r in recs:
        r['e'] = math.log(r['up']) - predict_ln(model, r)
    bt = backtest(recs); log('  回測', json.dumps(bt, ensure_ascii=False)[:400])
    if bt['mdape'] is None or bt['mdape'] > max_mdape:
        raise RuntimeError(f"回測 MdAPE {bt['mdape']} 超過 {max_mdape}，不更新資料")
    out = [r for r in recs if now_ym - r['ym'] < KEEP_MONTHS]
    rent_out = []
    for r in rents:
        if now_ym - r['ym'] < 12 and r['ym'] <= now_ym:
            pt, _ = addr.locate(r['addr'])
            if pt:
                rent_out.append([round(pt[0], 5), round(pt[1], 5), r['ym'], round(r['ping'], 1), int(r['rent'])])
    built = built or datetime.now(timezone.utc).strftime('%Y-%m-%d')
    dists = sorted(model['dist'])
    rp = {'built': built, 'src': 'moi-realprice', 'through': ym_str(now_ym), 'districts': dists, 'types': TYPES,
          'cols': ['lat', 'lon', 'ym', 'dist', 'type', 'ping', 'age', 'floor', 'floors', 'unit', 's', 'e'],
          'sales': [[round(r['pt'][0], 5), round(r['pt'][1], 5), r['ym'], dists.index(r['dist']), r['t'], round(r['ping'], 1), round(r['age']), r['fl'], r['tot'],
                     round(r['up'], 1), round(r['s'], 3), round(r['e'], 4)] for r in sorted(out, key=lambda r: -r['ym'])],
          'rent_cols': ['lat', 'lon', 'ym', 'ping', 'rent'], 'rents': rent_out}
    val = {'built': built, 'through': ym_str(now_ym), 'now_ym': now_ym, 'feats': FEATS, 'poi_layers': POI,
           'model': {'b': [round(x, 6) for x in model['b']], 'dist': {k: round(v, 5) for k, v in model['dist'].items()}, 'now': round(now_effect(model), 5)},
           'comps': {'radii': RADII, 'min': list(MIN_COMPS), 'age_band': AGE_BAND, 'size_band': list(SIZE_BAND), 'months': KEEP_MONTHS}, 'offer': {'band': BAND, 'shift': SHIFT},
           'backtest': {k: (round(v, 4) if isinstance(v, float) else v) for k, v in bt.items()},
           'market': market(all_recs, model, now_ym, recs), 'stats': dict(stats, used=len(recs), shown=len(out), rents=len(rent_out))}
    os.makedirs(out_dir, exist_ok=True)
    for name, obj in (('realprice.json', rp), ('valuation.json', val)):
        with open(os.path.join(out_dir, name), 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))
    log(f"  成交 {len(out)} 筆、租賃 {len(rent_out)} 筆；定位精度 {stats['precision']}")
    return val


def main():
    today = date.today()
    print('下載實價登錄…'); sales, rents, st = fetch_all(today); print('  清理統計', st)
    print('下載門牌座標…'); addr = fetch_addresses()
    layers = json.load(open(os.path.join(B.ROOT, 'data', 'city.json'), encoding='utf-8'))['layers']
    if os.environ.get('RP_DUMP'):   # 除錯用：把清理後、已定位的資料存下來，方便離線調整模型
        for r in sales + rents:
            r['pt'] = addr.locate(r['addr'])[0]
        json.dump({'sales': sales, 'rents': rents, 'stats': st}, open(os.environ['RP_DUMP'], 'w'), ensure_ascii=False)
    val = build(sales, rents, addr, layers, today=today)
    print(json.dumps({k: val[k] for k in ('through', 'backtest', 'stats')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
