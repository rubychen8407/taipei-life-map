#!/usr/bin/env python3
"""產生 data/market.json：「整體房市」頁面的指標、燈號與未來 12 個月房價方向機率。

全部是政府開放資料（政府資料開放授權條款第 1 版），每月由 GitHub Actions 更新：
  價格  臺北市住宅價格月指數（地政局，data.gov.tw 121381）
        臺北市住宅租金指數（地政局，171234）→ 毛租金投報率
        實價登錄（內政部，每季臺北市 A 買賣、B 預售檔）→ 預售 vs 中古單價差
  交易量 實價登錄臺北市住宅買賣筆數（依成交月份）、預售成交筆數
  供給  臺北市建造執照／使用執照摘要（建管處，128204/128200、128203/128199）→ 每月核發戶數
        臺北市預售屋推案戶數（地政局，173425）
  信貸與成本  五大銀行新承做購屋貸款金額與利率（央行，6551）
             購置住宅貸款、建築貸款餘額年增率（央行，6549）
             住宅不動產放款占放款總額（央行金融健全指標，23854）
             臺北市營造工程物價指數（主計處 TSIS）

預測：目標＝臺北市住宅價格月指數「未來 12 個月」變動，分 上漲（> +3%）／持平／下跌（< −3%）。
特徵（都是當月已知）：成交量年增、新承做房貸金額年增、房貸利率 12 個月變動（回測比較後保留這三個）。模型：多元邏輯斯迴歸（標準化、L2）。
回測：逐月往前滾動（只用當時已知答案的資料訓練），和「延續過去 12 個月方向」的基準比較命中率。
"""
import csv, io, json, math, os, re, sys, time, urllib.parse, urllib.request
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_data as B

API = 'https://data.gov.tw/api/v2/rest/dataset/'
LVR = 'https://plvr.land.moi.gov.tw/DownloadSeason?season={s}&fileName=A_lvr_land_{k}.csv'
CBC_LOAN_RATE = 'https://www.cbc.gov.tw/public/data/OpenData/經研處/EH45M01.csv'
CBC_LOANS = 'https://www.cbc.gov.tw/public/data/OpenData/經研處/EF99M01.csv'
CBC_FSI = 'https://www.cbc.gov.tw/public/data/opendata/financialstability/FSI-不動產市場.csv'
TSIS_COST = ('https://tsis.dbas.gov.taipei/statis/webMain.aspx?sys=220&ymf=9201&kind=21&type=0&funid=a04004101'
             '&cycle=1&outmode=12&compmode=0&outkind=1&deflst=2&nzo=1')
DS = {'price': 121381, 'rent': 171234, 'presale_supply': 173425,
      'permit_hist': 128204, 'permit_cur': 128200, 'use_hist': 128203, 'use_cur': 128199}
PING = 3.305785
HORIZON, THR = 12, 3.0          # 預測 12 個月後；±3% 以內算持平
CLASSES = ('up', 'flat', 'down')
LVR_FIRST = (101, 3)            # 實價登錄 101 年第 3 季開始
INCOMPLETE = 2                  # 實價登錄最近 2 個月還沒申報完，不列入


# ---------- 小工具 ----------
def M(y, m):                    # 月序號（西元）
    return y * 12 + m - 1


def ym_str(k):
    return f'{k // 12}-{k % 12 + 1:02d}'


def decode(raw):
    for enc in ('utf-8-sig', 'cp950'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode('cp950', errors='replace')


def num(v):
    try:
        s = str(v).replace(',', '').replace('%', '').strip()
        return float(s) if s not in ('', '-', '...', '…') else None
    except ValueError:
        return None


def median(xs):
    s = sorted(xs); n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def rows_of(text):
    return list(csv.reader(io.StringIO(text)))


# ---------- 解析各來源 ----------
def parse_price_index(text):
    """臺北市住宅價格月指數 → {月: (指數, 標準住宅總價萬, 標準單價萬/坪)}（全市）"""
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        cat = (r.get('住宅價格月指數類別') or list(r.values())[0] or '').strip()
        m = re.match(r'(\d+)/(\d+)', (r.get('期別') or '').strip())
        if cat != '全市' or not m:
            continue
        v = num(r.get('月指數'))
        if v is None:
            continue
        tot = num(next((x for k, x in r.items() if k and k.startswith('標準住宅總價')), None))
        up = num(next((x for k, x in r.items() if k and k.startswith('標準住宅單價')), None))
        out[M(int(m.group(1)) + 1911, int(m.group(2)))] = (v, tot, up)
    return out


def parse_rent(text):
    """臺北市住宅租金季指數 → {季末月: 標準租金（元/坪/月）}（全市）"""
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        cat = (r.get('住宅租金指數類別') or list(r.values())[0] or '').strip()
        m = re.match(r'(\d+)Q(\d)', (r.get('期別') or '').strip())
        if cat != '全市' or not m:
            continue
        v = num(next((x for k, x in r.items() if k and k.startswith('標準租金單價')), None))
        if v:
            out[M(int(m.group(1)) + 1911, int(m.group(2)) * 3)] = v
    return out


def parse_cbc_rate(text):
    """五大銀行新承做 → {月: (購屋貸款金額百萬元, 利率%)}"""
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        m = re.match(r'(\d{4})M(\d{2})', (r.get('期間') or '').strip())
        a, i = num(r.get('購屋貸款-金額')), num(r.get('購屋貸款-利率'))
        if m and a is not None and i is not None:
            out[M(int(m.group(1)), int(m.group(2)))] = (a, i)
    return out


def parse_cbc_loans(text):
    """消費者貸款及建築貸款餘額 → {月: (購置住宅貸款年增率, 建築貸款年增率)}"""
    rd = csv.reader(io.StringIO(text)); h = next(rd)
    ih = next(i for i, c in enumerate(h) if '購置住宅貸款' in c and '年增率' in c)
    ib = next(i for i, c in enumerate(h) if '建築貸款' in c and '年增率' in c)
    out = {}
    for r in rd:
        m = re.match(r'(\d{4})M(\d{2})', (r[0] if r else '').strip())
        if m and len(r) > max(ih, ib) and num(r[ih]) is not None and num(r[ib]) is not None:
            out[M(int(m.group(1)), int(m.group(2)))] = (num(r[ih]), num(r[ib]))
    return out


def parse_fsi(text):
    """央行金融健全指標 → {年: 住宅不動產放款/放款總額%}"""
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        y = num(r.get('民國年'))
        v = num(next((x for k, x in r.items() if k and k.startswith('住宅不動產放款')), None))
        if y and v is not None:
            out[int(y) + 1911] = v
    return out


def parse_tsis_cost(text):
    """臺北市營造工程物價指數（月）→ {月: 總指數}"""
    out = {}
    rd = csv.reader(io.StringIO(text)); h = next(rd, [])
    i = next((k for k, c in enumerate(h) if c.endswith('/總指數')), 1)
    for r in rd:
        m = re.match(r'(\d+)年\s*(\d+)月', (r[0] if r else '').strip())
        if m and len(r) > i and num(r[i]):
            out[M(int(m.group(1)) + 1911, int(m.group(2)))] = num(r[i])
    return out


DATA_RE = re.compile(r'<Data>(.*?)</Data>', re.S)


def parse_permits(text):
    """建造／使用執照摘要（XML，偶有格式不完整，用正規式逐筆擷取）→ {月: [件數, 戶數]}（新建）"""
    out = {}
    for blk in DATA_RE.findall(text):
        d = re.search(r'<發照日期>(\d{6,7})</發照日期>', blk)
        if not d:
            continue
        kind = re.search(r'<建造類別>([^<]*)</建造類別>', blk)
        if kind and '新建' not in kind.group(1):
            continue
        s = d.group(1).zfill(7); y, mo = int(s[:3]) + 1911, int(s[3:5])
        if not 1 <= mo <= 12:
            continue
        hh = sum(int(x) for x in re.findall(r'<戶數>(\d+)</戶數>', blk))
        c = out.setdefault(M(y, mo), [0, 0]); c[0] += 1; c[1] += hh
    return out


def parse_presale_supply(text):
    """預售屋推案戶數（季）→ {季末月: 總戶數}"""
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        y = re.match(r'(\d+)', r.get('年份') or ''); q = re.search(r'(\d)', r.get('季別') or '')
        v = num(r.get('總戶數'))
        if y and q and v is not None:
            k = M(int(y.group(1)) + 1911, int(q.group(1)) * 3)
            out[k] = out.get(k, 0) + v
    return out


TYPES = ('住宅大樓', '華廈', '公寓', '套房')


def roc_ym(s):
    s = re.sub(r'\D', '', s or '')
    if len(s) < 6:
        return None
    s = s.zfill(7); y, m = int(s[:3]) + 1911, int(s[3:5])
    return (y, m) if 1 <= m <= 12 and y > 1950 else None


def parse_lvr(text, presale=False):
    """實價登錄臺北市季檔 → [(成交月, 單價萬/坪, 是否住宅大樓, 屋齡年或 None)]，只取住家用房地"""
    rows = rows_of(text)
    if not rows:
        return []
    h = rows[0]; out = []
    for r in rows[1:]:
        if not r or r[0].startswith('The villages') or len(r) < len(h) - 2:
            continue
        d = dict(zip(h, r))
        if not (d.get('交易標的') or '').startswith('房地'):
            continue
        if '住' not in (d.get('主要用途') or '住'):        # 預售檔有時沒填主要用途
            continue
        t = d.get('建物型態') or ''
        if not any(t.startswith(x) for x in TYPES):
            continue
        ym = roc_ym(d.get('交易年月日'))
        if not ym:
            continue
        up = num(d.get('單價元平方公尺'))
        built = roc_ym(d.get('建築完成年月'))
        age = None if presale or not built else (ym[0] - built[0]) + (ym[1] - built[1]) / 12
        out.append((M(*ym), up * PING / 1e4 if up else None, t.startswith('住宅大樓'), age))
    return out


def seasons_since(first, today):
    y, q = first; out = []
    ty, tq = today.year - 1911, (today.month - 1) // 3 + 1
    while (y, q) < (ty, tq):
        out.append(f'{y}S{q}')
        q += 1
        if q == 5:
            y += 1; q = 1
    return out


# ---------- 數列運算 ----------
def yoy(s, k, n=12, avg=1):
    """年增率 %：近 avg 個月平均 vs 一年前同期平均"""
    a = [s.get(k - i) for i in range(avg)]; b = [s.get(k - n - i) for i in range(avg)]
    if None in a or None in b or sum(b) == 0:
        return None
    return (sum(a) / sum(b) - 1) * 100


def chg(s, k, n):
    return None if s.get(k) is None or s.get(k - n) is None else s[k] - s[k - n]


def pct(s, k, n):
    return None if not s.get(k) or not s.get(k - n) else (s[k] / s[k - n] - 1) * 100


def ffill(s, upto, limit=3):
    """補最近幾個月還沒公布的值（最多 limit 個月）"""
    if not s:
        return s
    last = max(s); out = dict(s)
    for k in range(last + 1, min(upto, last + limit) + 1):
        out[k] = s[last]
    return out


def rolling_sum(s, k, n=12):
    v = [s.get(k - i) for i in range(n)]
    return None if None in v else sum(v)


# ---------- 模型 ----------
def softmax(z):
    m = max(z); e = [math.exp(x - m) for x in z]; t = sum(e)
    return [x / t for x in e]


def fit_logit(X, y, lam=1.0, iters=300, lr=0.5):
    """多元邏輯斯迴歸（梯度下降、L2）。X 已標準化；回傳 W[class][feature+1]"""
    k, d = len(CLASSES), len(X[0]); W = [[0.0] * (d + 1) for _ in range(k)]; n = len(X)
    for _ in range(iters):
        G = [[0.0] * (d + 1) for _ in range(k)]
        for x, c in zip(X, y):
            p = softmax([w[0] + sum(wj * xj for wj, xj in zip(w[1:], x)) for w in W])
            for j in range(k):
                g = p[j] - (1 if j == c else 0)
                G[j][0] += g
                for f in range(d):
                    G[j][f + 1] += g * x[f]
        for j in range(k):
            for f in range(d + 1):
                W[j][f] -= lr * (G[j][f] / n + (lam * W[j][f] if f else 0))
    return W


def predict(W, x):
    return softmax([w[0] + sum(wj * xj for wj, xj in zip(w[1:], x)) for w in W])


def standardize(X, mu=None, sd=None):
    d = len(X[0])
    if mu is None:
        mu = [sum(r[f] for r in X) / len(X) for f in range(d)]
        sd = [max(1e-9, math.sqrt(sum((r[f] - mu[f]) ** 2 for r in X) / len(X))) for f in range(d)]
    return [[(r[f] - mu[f]) / sd[f] for f in range(d)] for r in X], mu, sd


def label(change):
    return 0 if change > THR else 2 if change < -THR else 1


# 用真實資料回測比較過（2018～2025）：只用傳導鏈前兩環的領先指標（成交量、房貸、利率）最好；
# 加入房價動能、建照、營造成本反而變差（樣本少、容易過度配適）
FEATS = [('vol_yoy', '成交量年增'), ('loan_yoy', '新承做房貸金額年增'), ('rate_chg', '房貸利率 12 個月變動')]


def features(S, k):
    f = {'mom12': pct(S['price'], k, 12), 'mom6': pct(S['price'], k, 6), 'vol_yoy': yoy(S['vol'], k, avg=3), 'rate_chg': chg(S['rate'], k, 12),
         'loan_yoy': yoy(S['loan_amt'], k, avg=3), 'permit_yoy': None, 'cost_yoy': pct(S['cost'], k, 12)}
    a, b = rolling_sum(S['permit_hh'], k), rolling_sum(S['permit_hh'], k - 12)
    if a is not None and b:
        f['permit_yoy'] = max(-80, min(200, (a / b - 1) * 100))
    return [f[n] for n, _ in FEATS] if all(f[n] is not None for n, _ in FEATS) else None



def forecast(S, min_train=48):
    P = S['price']; ks = sorted(P)
    rows = []
    for k in ks:
        x = features(S, k)
        if x is None:
            continue
        fut = pct(P, k + HORIZON, HORIZON) if (k + HORIZON) in P else None
        past = pct(P, k, HORIZON)
        rows.append((k, x, None if fut is None else label(fut), None if past is None else label(past)))
    lab = [r for r in rows if r[2] is not None]
    if len(lab) < min_train + 6:
        raise RuntimeError(f'可用來訓練的月份太少（{len(lab)}）')
    hist, hits, base_hits, maj_hits, n = [], 0, 0, 0, 0
    W = None
    for i, (k, x, c, past) in enumerate(rows):
        train = [r for r in lab if r[0] + HORIZON <= k]          # 當時已經知道答案的月份
        if len(train) < min_train:
            continue
        if W is None or i % 3 == 0:                              # 每 3 個月重新訓練一次（加快）
            Xs, mu, sd = standardize([r[1] for r in train]); W = fit_logit(Xs, [r[2] for r in train])
        p = predict(W, standardize([x], mu, sd)[0][0])
        hist.append([ym_str(k), round(p[0], 3), round(p[1], 3), round(p[2], 3), c])
        if c is not None:
            n += 1; hits += (max(range(3), key=lambda j: p[j]) == c)
            base_hits += (past == c) if past is not None else 0
            cnt = [sum(1 for r in train if r[2] == j) for j in range(3)]
            maj_hits += (max(range(3), key=lambda j: cnt[j]) == c)
    Xs, mu, sd = standardize([r[1] for r in lab]); W = fit_logit(Xs, [r[2] for r in lab])
    k, x = rows[-1][0], rows[-1][1]; xs = standardize([x], mu, sd)[0][0]; p = predict(W, xs)
    # 各特徵對「上漲 vs 下跌」的貢獻（標準化值 × 係數差），讓頁面可以說明原因
    contrib = [{'key': FEATS[f][0], 'name': FEATS[f][1], 'v': round(x[f], 2), 'z': round(xs[f], 2),
                'push': round(xs[f] * (W[0][f + 1] - W[2][f + 1]), 3)} for f in range(len(FEATS))]
    return {'horizon': HORIZON, 'thr': THR, 'as_of': ym_str(k), 'probs': {c: round(v, 3) for c, v in zip(CLASSES, p)},
            'call': CLASSES[max(range(3), key=lambda j: p[j])], 'contrib': contrib,
            'backtest': {'n': n, 'acc': round(hits / n, 3) if n else None, 'base_acc': round(base_hits / n, 3) if n else None,
                         'maj_acc': round(maj_hits / n, 3) if n else None,
                         'skill': round((hits - max(base_hits, maj_hits)) / n, 3) if n else None, 'from': hist[0][0] if hist else None,
                         'to': next((h[0] for h in reversed(hist) if h[4] is not None), None)},
            'history': hist, 'train_n': len(lab)}


# ---------- 燈號 ----------
LIGHTS = [('藍燈', '偏冷'), ('黃藍燈', '轉弱'), ('綠燈', '穩定'), ('黃紅燈', '轉熱'), ('紅燈', '過熱')]


def zscore(s, k, fn, years=10):
    """fn(s,k) 的值相對過去 years 年的 z 分數"""
    v = fn(k)
    if v is None:
        return None
    past = [fn(j) for j in range(k - years * 12, k)]
    past = [p for p in past if p is not None]
    if len(past) < 24:
        return None
    mu = sum(past) / len(past); sd = math.sqrt(sum((p - mu) ** 2 for p in past) / len(past)) or 1
    return max(-3, min(3, (v - mu) / sd))


def light(S, k):
    parts = [('vol', '成交量年增', lambda j: yoy(S['vol'], j, avg=3), 1),
             ('loan', '新承做房貸年增', lambda j: yoy(S['loan_amt'], j, avg=3), 1),
             ('rate', '房貸利率 12 個月變動', lambda j: chg(S['rate'], j, 12), -1),
             ('mom', '房價 6 個月動能', lambda j: pct(S['price'], j, 6), 1),
             ('build', '建築貸款年增', lambda j: S['build_loan'].get(j), 1)]
    out, tot, w = [], 0, 0
    for key, name, fn, sign in parts:
        z = zscore(None, k, fn)
        if z is None:
            continue
        out.append({'key': key, 'name': name, 'z': round(z * sign, 2)}); tot += z * sign; w += 1
    if not w:
        return None
    sc = tot / w
    lv = 0 if sc < -1 else 1 if sc < -0.35 else 2 if sc <= 0.35 else 3 if sc <= 1 else 4
    return {'score': round(sc, 2), 'level': lv, 'label': LIGHTS[lv][0], 'mood': LIGHTS[lv][1], 'parts': out}


# ---------- 組合 ----------
def series_pts(s, since, fn=None, nd=2, scale=1):
    out = []
    for k in sorted(s):
        if k >= since:
            v = fn(k) if fn else s[k]
            if v is not None:
                out.append([ym_str(k), round(v * scale, nd)])
    return out


def sig(v, pos, neg):
    """對房價的影響：v 高於 pos → +1（支撐）；低於 neg → −1（壓力）"""
    return 0 if v is None else 1 if v > pos else -1 if v < neg else 0


def build(src, out_dir=os.path.join(B.ROOT, 'data'), built=None, today=None, log=print):
    today = today or date.today()
    pidx = src['price']
    if len(pidx) < 60:
        raise RuntimeError(f'住宅價格指數只有 {len(pidx)} 個月，不更新')
    S = {'price': {k: v[0] for k, v in pidx.items()}, 'price_tot': {k: v[1] for k, v in pidx.items() if v[1]},
         'price_up': {k: v[2] for k, v in pidx.items() if v[2]}}
    last = max(S['price'])
    # 實價登錄：成交筆數、預售筆數、預售 vs 中古單價
    sales, pre = src['lvr_a'], src['lvr_b']
    # 實價登錄：成交後 1～3 個月才申報，最新季檔結束前 2 個月內成交的還沒申報完整
    cut = src['lvr_through'] - 2 if src.get('lvr_through') else M(today.year, today.month) - INCOMPLETE
    vol, pvol = {}, {}
    for k, *_ in sales:
        if k < cut:
            vol[k] = vol.get(k, 0) + 1
    for k, *_ in pre:
        if k < cut:
            pvol[k] = pvol.get(k, 0) + 1
    first = min(vol) if vol else cut
    for k in range(first, cut):                      # 沒有成交的月份補 0
        vol.setdefault(k, 0); pvol.setdefault(k, 0)
    S['vol'], S['pvol'] = vol, pvol
    prem = {}
    for k in range(first + 5, cut):
        a = [u for m, u, tw, age in sales if k - 5 <= m <= k and tw and u and age is not None and age >= 1]
        b = [u for m, u, tw, _ in pre if k - 5 <= m <= k and tw and u]
        if len(a) >= 30 and len(b) >= 10:
            prem[k] = (median(b) / median(a) - 1) * 100
    S['premium'] = prem
    upto = max(last, cut - 1)
    S['rate'] = ffill({k: v[1] for k, v in src['cbc_rate'].items()}, upto)
    S['loan_amt'] = ffill({k: v[0] for k, v in src['cbc_rate'].items()}, upto)
    S['house_loan'] = ffill({k: v[0] for k, v in src['cbc_loans'].items()}, upto)
    S['build_loan'] = ffill({k: v[1] for k, v in src['cbc_loans'].items()}, upto)
    S['cost'] = ffill(src['cost'], upto)
    S['permit_hh'] = {k: v[1] for k, v in src['permits'].items()}
    S['use_hh'] = {k: v[1] for k, v in src['uses'].items()}
    pm = M(today.year, today.month)
    for s in ('permit_hh', 'use_hh'):                 # 執照摘要：沒有核發的月份是 0（本月還沒過完不算）
        if S[s]:
            for k in range(min(S[s]), pm):
                S[s].setdefault(k, 0)
    S['rent'] = src['rent']
    # 毛租金投報率（季）：標準租金×12 ÷ 標準住宅單價
    yld = {}
    for k, r in S['rent'].items():
        up = S['price_up'].get(k) or S['price_up'].get(k - 1)
        if up:
            yld[k] = r * 12 / (up * 1e4) * 100
    S['yield'] = yld
    # 標準住宅月付：總價 8 成、30 年、五大銀行利率
    pay = {}
    for k, tot in S['price_tot'].items():
        i = S['rate'].get(k)
        if i:
            r = i / 100 / 12; n = 360; L = tot * 1e4 * 0.8
            pay[k] = L * r / (1 - (1 + r) ** -n)
    S['pay'] = pay

    since = M(2012, 8)
    def latest(s):
        return (max(s), s[max(s)]) if s else (None, None)
    ind = []
    def add(key, group, name, unit, series, cur_fn, chg_label, chg_fn, signal, how, src_name, nd=2, freq='m', scale=1):
        k, v = latest(series)
        if k is None:
            log(f'  ⚠ {name} 沒有資料，略過'); return
        c = chg_fn(k)
        ind.append({'key': key, 'group': group, 'name': name, 'unit': unit, 'freq': freq, 't': ym_str(k),
                    'v': round(cur_fn(k), nd) if cur_fn(k) is not None else None, 'chg_label': chg_label,
                    'chg': None if c is None else round(c, 2), 'signal': signal(c, cur_fn(k)), 'how': how, 'src': src_name,
                    'pts': series_pts(series, since, None, nd, scale)})
    P = S['price']
    add('price', 'price', '臺北市住宅價格指數', '指數', P, lambda k: P[k], '年增', lambda k: pct(P, k, 12),
        lambda c, v: sig(c, 6, 0), '實價登錄算出的「標準住宅」價格。年增率連續放緩、甚至轉負，代表房價進入盤整或修正。', '臺北市地政局')
    add('premium', 'price', '預售比中古貴多少', '%', prem, lambda k: prem[k], '一年變化', lambda k: chg(prem, k, 12),
        lambda c, v: -1 if v is not None and v > 60 else 0, '同期住宅大樓：預售中位數單價 ÷ 中古（屋齡 1 年以上）中位數 − 1。超過 60% 代表預售透支未來漲幅，交屋時容易鑑價不足。', '實價登錄（近 6 個月）', nd=1)
    add('yield', 'price', '毛租金投報率', '%', yld, lambda k: yld[k], '對房貸利率差', lambda k: yld[k] - (S['rate'].get(k) or 0),
        lambda c, v: sig(c, 0.5, -0.3), '標準住宅的年租金 ÷ 房價。低於房貸利率就是「負利差」：買來出租不夠付利息，房價只能靠增值預期撐住。', '臺北市地政局租金指數', freq='q')
    add('vol', 'volume', '住宅買賣筆數（月）', '筆', vol, lambda k: vol[k], '近 3 月年增', lambda k: yoy(vol, k, avg=3),
        lambda c, v: sig(c, 15, -15), '「量先價行」：成交量持續萎縮通常比房價先轉弱。成交後要 1～3 個月才會申報，最近的月份還沒完整，不列入。', '實價登錄（臺北市）', nd=0)
    add('pvol', 'volume', '預售屋成交筆數（月）', '筆', pvol, lambda k: pvol[k], '近 3 月年增', lambda k: yoy(pvol, k, avg=3),
        lambda c, v: sig(c, 20, -20), '預售市場對信用管制最敏感；預售量急縮常是建商放慢推案的前兆。', '實價登錄（臺北市預售）', nd=0)
    p12 = {k: rolling_sum(S['permit_hh'], k) for k in S['permit_hh'] if rolling_sum(S['permit_hh'], k) is not None}
    u12 = {k: rolling_sum(S['use_hh'], k) for k in S['use_hh'] if rolling_sum(S['use_hh'], k) is not None}
    add('permit', 'supply', '建照核發戶數（近 12 個月）', '戶', p12, lambda k: p12[k], '年增', lambda k: pct(p12, k, 12),
        lambda c, v: sig(c, 25, -25), '建照是 2～4 年後的新增供給；建照減少代表建商看淡、主動減緩推案。', '臺北市建管處建照摘要', nd=0)
    add('use', 'supply', '使照核發戶數（近 12 個月）', '戶', u12, lambda k: u12[k], '年增', lambda k: pct(u12, k, 12),
        lambda c, v: 0 if c is None else -1 if c > 25 else 1 if c < -25 else 0, '使照＝新屋完工可交屋。使照大增代表交屋潮，短期賣壓與餘屋增加（訊號方向與其他相反）。', '臺北市建管處使照摘要', nd=0)
    ps = src['presale_supply']
    add('presale_supply', 'supply', '預售屋推案戶數（季）', '戶', ps, lambda k: ps[k], '年增', lambda k: pct(ps, k, 12),
        lambda c, v: sig(c, 30, -30), '建商實際開賣的戶數。推案多但成交少，表示去化變慢。', '臺北市地政局', nd=0, freq='q')
    R = S['rate']
    add('rate', 'credit', '五大銀行新承做房貸利率', '%', R, lambda k: R[k], '12 個月變動（百分點）', lambda k: chg(R, k, 12),
        lambda c, v: 0 if c is None else -1 if c > 0.15 else 1 if c < -0.1 else 0, '利率上升讓每月房貸變重、壓抑買氣；利率下降則相反。', '中央銀行', nd=3)
    LA = S['loan_amt']
    add('loan', 'credit', '新承做購屋貸款金額（月）', '億元', LA, lambda k: LA[k] / 100, '近 3 月年增', lambda k: yoy(LA, k, avg=3),
        lambda c, v: sig(c, 15, -15), '銀行每月新撥的房貸。央行信用管制或銀行控管房貸總量時會明顯下降，是最直接的資金閘門。', '中央銀行（五大銀行）', nd=0, scale=0.01)
    HL = S['house_loan']
    add('house_loan', 'credit', '購置住宅貸款餘額年增率', '%', HL, lambda k: HL[k], '一年變化（百分點）', lambda k: chg(HL, k, 12),
        lambda c, v: sig(v, 8, 3), '全體銀行房貸餘額的成長速度；增速放緩代表信用持續收緊。', '中央銀行', nd=2)
    BL = S['build_loan']
    add('build_loan', 'credit', '建築貸款餘額年增率', '%', BL, lambda k: BL[k], '一年變化（百分點）', lambda k: chg(BL, k, 12),
        lambda c, v: sig(v, 8, 2), '建商向銀行借來買地、蓋房的錢；被視為建商推案信心的風向球。', '中央銀行', nd=2)
    C = S['cost']
    add('cost', 'credit', '臺北市營造工程物價指數', '指數', C, lambda k: C[k], '年增', lambda k: pct(C, k, 12),
        lambda c, v: sig(c, 5, 0), '蓋房子的材料與工資。成本高漲時新案很難降價，形成房價的成本底線。', '臺北市主計處', nd=2)
    PY = S['pay']
    add('pay', 'credit', '標準住宅每月房貸', '萬元', PY, lambda k: PY[k] / 1e4, '年增', lambda k: pct(PY, k, 12),
        lambda c, v: 0 if c is None else -1 if c > 8 else 1 if c < -3 else 0, '臺北市標準住宅總價貸 8 成、30 年、五大銀行利率的每月本息，反映房價＋利率的綜合負擔。', '本 app 計算', nd=1, scale=1e-4)
    fsi = src['fsi']
    if fsi:
        y = max(fsi); FS = {M(yy, 12): v for yy, v in fsi.items()}
        add('fsi', 'credit', '住宅不動產放款占放款總額', '%', FS, lambda k: FS[k], '一年變化（百分點）', lambda k: chg(FS, k, 12),
            lambda c, v: 0 if v is None else -1 if v > 35 else 1 if v < 30 else 0, '銀行資金集中在房貸的程度。偏高時央行傾向管制；從高點滑落後，管制才有放寬空間。', '中央銀行金融健全指標', nd=2, freq='y')

    fc = forecast(S)
    lt = light(S, max(k for k in S['price'] if features(S, k) is not None) if any(features(S, k) for k in S['price']) else last)
    # 四階段傳導鏈：每個環節取對應指標的訊號加總
    by = {i['key']: i for i in ind}
    def stage(keys):
        s = sum(by[k]['signal'] for k in keys if k in by)
        return 1 if s > 0 else -1 if s < 0 else 0
    chain = [{'key': 'credit', 'name': '金融信貸與管制', 'keys': ['loan', 'rate', 'house_loan'], 'status': stage(['loan', 'rate', 'house_loan'])},
             {'key': 'volume', 'name': '成交量與流動性', 'keys': ['vol', 'pvol'], 'status': stage(['vol', 'pvol'])},
             {'key': 'supply', 'name': '供給管線', 'keys': ['permit', 'presale_supply', 'build_loan'], 'status': stage(['permit', 'presale_supply', 'build_loan'])},
             {'key': 'cost', 'name': '成本底線', 'keys': ['cost'], 'status': stage(['cost'])}]
    res = {'built': built or datetime.now(timezone.utc).strftime('%Y-%m-%d'), 'through': ym_str(last), 'indicators': ind,
           'light': lt, 'forecast': fc, 'chain': chain,
           'price_hist': series_pts(S['price'], M(2012, 8), None, 2)}
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, 'market.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, separators=(',', ':'))
    bt = fc['backtest']
    log(f'整體房市：{len(ind)} 項指標，資料到 {res["through"]}；燈號 {lt and lt["label"]}（{lt and lt["score"]}）；'
        f'未來 12 個月 上漲 {fc["probs"]["up"]:.0%}／持平 {fc["probs"]["flat"]:.0%}／下跌 {fc["probs"]["down"]:.0%}；'
        f'回測 {bt["n"]} 個月命中 {bt["acc"]}（延續基準 {bt["base_acc"]}）；{os.path.getsize(p) / 1e3:.0f} KB')
    return res


# ---------- 下載 ----------
def http(url, timeout=300, tries=3):
    url = urllib.parse.quote(url, safe=':/?&=%')      # 網址裡有中文（央行檔名）
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 taipei-life-map'}), timeout=timeout).read()
        except Exception as e:                       # noqa: BLE001
            print(f'  retry {url[:90]} {e}'); time.sleep(5 * (i + 1))
    raise RuntimeError('下載失敗 ' + url)


def ds_csv(ds):
    m = json.loads(http(API + str(ds)))
    d = (m.get('result') or m).get('distribution', [])
    return next(x['resourceDownloadUrl'] for x in d if (x.get('resourceFormat') or '').upper() in ('CSV', 'XML'))


def fetch(today=None, log=print):
    today = today or date.today(); src = {}
    def opt(name, fn, default):
        try:
            v = fn(); log(f'  {name}: {len(v)}'); return v
        except Exception as e:                       # noqa: BLE001
            log(f'  ⚠ {name} 讀取失敗：{e}'); return default
    src['price'] = parse_price_index(decode(http(ds_csv(DS['price']))))
    log(f'  住宅價格指數：{len(src["price"])} 個月')
    src['rent'] = opt('租金指數', lambda: parse_rent(decode(http(ds_csv(DS['rent'])))), {})
    src['presale_supply'] = opt('預售推案', lambda: parse_presale_supply(decode(http(ds_csv(DS['presale_supply'])))), {})
    src['cbc_rate'] = parse_cbc_rate(decode(http(CBC_LOAN_RATE)))
    src['cbc_loans'] = parse_cbc_loans(decode(http(CBC_LOANS)))
    src['fsi'] = opt('金融健全指標', lambda: parse_fsi(decode(http(CBC_FSI))), {})
    src['cost'] = opt('營造工程物價', lambda: parse_tsis_cost(decode(http(TSIS_COST))), {})
    def permits(a, b):
        out = {}
        for ds in (a, b):
            for k, v in parse_permits(decode(http(ds_csv(DS[ds]), 900))).items():
                c = out.setdefault(k, [0, 0]); c[0] = max(c[0], v[0]); c[1] = max(c[1], v[1])
        return out
    src['permits'] = permits('permit_hist', 'permit_cur'); log(f'  建照：{len(src["permits"])} 個月')
    src['uses'] = permits('use_hist', 'use_cur'); log(f'  使照：{len(src["uses"])} 個月')
    a, b, through = [], [], None
    for s in seasons_since(LVR_FIRST, today):
        try:
            a += parse_lvr(decode(http(LVR.format(s=s, k='A'))))
            b += parse_lvr(decode(http(LVR.format(s=s, k='B'))), presale=True)
            y, q = s.split('S'); through = M(int(y) + 1911, int(q) * 3)     # 這一季的最後一個月
        except RuntimeError as e:
            log(f'  ⚠ {s} {e}')
    src['lvr_through'] = through
    src['lvr_a'], src['lvr_b'] = a, b
    log(f'  實價登錄：買賣 {len(a)}、預售 {len(b)}')
    return src


def main():
    build(fetch())


if __name__ == '__main__':
    main()
