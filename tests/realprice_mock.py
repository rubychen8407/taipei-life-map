"""實價登錄格式的模擬資料：買賣／租賃 CSV 的 ZIP，以及對應的門牌索引。價格有已知的規律，方便檢查模型。"""
import io, math, os, random, sys, zipfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))
import build_realprice as R

SALE_COLS = ['鄉鎮市區', '交易標的', '土地位置建物門牌', '土地移轉總面積平方公尺', '都市土地使用分區', '非都市土地使用分區', '非都市土地使用編定', '交易年月日', '交易筆棟數', '移轉層次', '總樓層數', '建物型態', '主要用途', '主要建材', '建築完成年月', '建物移轉總面積平方公尺', '建物現況格局-房', '建物現況格局-廳', '建物現況格局-衛', '建物現況格局-隔間', '有無管理組織', '總價元', '單價元平方公尺', '車位類別', '車位移轉總面積平方公尺', '車位總價元', '備註', '編號', '主建物面積', '附屬建物面積', '陽台面積', '電梯', '移轉編號']
RENT_COLS = ['鄉鎮市區', '交易標的', '土地位置建物門牌', '土地面積平方公尺', '租賃年月日', '租賃層次', '總樓層數', '建物型態', '主要用途', '建築完成年月', '建物總面積平方公尺', '總額元', '備註', '編號', '出租型態']
CN = '零一二三四五六七八九'
DIST = {'大安區': (25.030, 121.543, 100), '松山區': (25.056, 121.557, 90), '文山區': (24.992, 121.565, 60), '內湖區': (25.075, 121.585, 70)}
ROADS = ['和平路', '仁愛路', '光明街', '中華路']


def cn(n):
    return ('' if n < 10 else ('' if n < 20 else CN[n // 10]) + '十') + (CN[n % 10] if n % 10 or n < 10 else '')


def roc(y, m, d=15):
    return f'{y - 1911}{m:02d}{d:02d}'


def fw(s):
    return str(s).translate(str.maketrans('0123456789', '０１２３４５６７８９'))


def true_unit(dist, t, age, fl, tot, ping, ym_i):
    """模擬的「真實」單價（萬／坪）：區位 × 屋齡每年 −0.8% × 公寓 −15% × 每季 +1%"""
    return DIST[dist][2] * math.exp(-0.008 * age) * (0.85 if t == 2 else 1.0) * (1.01 ** (ym_i // 3 - (2023 * 12) // 3))


def mock(n=4800, seed=7, last=(2026, 10)):
    rnd = random.Random(seed); addr = R.AddressIndex(); sales, rents, truth = [], [], []
    end = last[0] * 12 + last[1] - 1
    for i in range(n):
        dist = rnd.choice(list(DIST)); la0, lo0, _ = DIST[dist]; road = dist[:2] + rnd.choice(ROADS); no = rnd.randrange(1, 400)
        key = (road, '', '', f'{no}號')
        if key not in addr.exact:
            p = (la0 + rnd.uniform(-.006, .006), lo0 + rnd.uniform(-.006, .006)); addr.exact[key] = p; addr.lane.setdefault(key[:3], []).append((no, p))
        ymi = end - rnd.randrange(0, 44); y, m = ymi // 12, ymi % 12 + 1
        t = rnd.choice([0, 0, 1, 2]); tot = {0: rnd.randrange(10, 25), 1: rnd.randrange(6, 11), 2: rnd.choice([4, 5])}[t]; fl = rnd.randrange(2, tot + 1)
        age = rnd.uniform(3, 20) if t == 0 else rnd.uniform(15, 35) if t == 1 else rnd.uniform(35, 50); ping = rnd.uniform(15, 45)
        up = true_unit(dist, t, age, fl, tot, ping, ymi) * math.exp(rnd.gauss(0, 0.07))
        pk = t == 0 and rnd.random() < .5; pk_price = 2500000 if pk else 0; pk_area = 33.0 if pk else 0
        price = round(up * ping * 1e4) + pk_price; area = ping * R.PING + pk_area
        by = y - int(age); bm = max(1, m - 1)
        row = dict.fromkeys(SALE_COLS, '')
        row.update({'鄉鎮市區': dist, '交易標的': '房地(土地+建物+車位)' if pk else '房地(土地+建物)', '土地位置建物門牌': f'臺北市{dist}{road}{fw(no)}號{cn(fl)}樓',
                    '交易年月日': roc(y, m), '移轉層次': f'{cn(fl)}層', '總樓層數': f'{cn(tot)}層', '建物型態': ['住宅大樓(11層含以上有電梯)', '華廈(10層含以下有電梯)', '公寓(5樓含以下無電梯)'][t],
                    '主要用途': '住家用', '建築完成年月': roc(by, bm, 1), '建物移轉總面積平方公尺': f'{area:.2f}', '總價元': str(price), '車位移轉總面積平方公尺': f'{pk_area}', '車位總價元': str(pk_price), '編號': f'RP{i:06d}'})
        sales.append(row); truth.append((row['編號'], up))
        if i % 3 == 0:
            rents.append({'鄉鎮市區': dist, '交易標的': '租賃房屋', '土地位置建物門牌': row['土地位置建物門牌'], '租賃年月日': roc(y, m), '建物型態': row['建物型態'], '主要用途': '住家用',
                          '建物總面積平方公尺': f'{ping * R.PING:.2f}', '總額元': str(round(ping * 1300)), '備註': '', '編號': f'RR{i:06d}', '出租型態': '整棟(戶)出租'})
    return sales, rents, addr, dict(truth)


def to_csv(cols, rows):
    out = io.StringIO(); out.write('﻿' + ','.join(cols) + '\n' + ','.join('en' for _ in cols) + '\n')
    for r in rows:
        out.write(','.join(str(r.get(c, '')) for c in cols) + '\n')
    return out.getvalue()


def to_zip(sales, rents):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('a_lvr_land_a.csv', to_csv(SALE_COLS, sales)); z.writestr('a_lvr_land_c.csv', to_csv(RENT_COLS, rents)); z.writestr('f_lvr_land_a.csv', to_csv(SALE_COLS, sales[:5]))
    return buf.getvalue()


def layers():
    """生活機能：每區中心附近各類一個設施"""
    return {lid: [{'id': f'{lid}{i}', 'n': lid, 'c': [la + .002 * j, lo + .002 * j]} for i, (la, lo, _) in enumerate(DIST.values())] for j, lid in enumerate(R.POI)}
