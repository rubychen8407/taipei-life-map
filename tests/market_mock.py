"""整體房市模擬資料：小段原始格式文字（測解析）＋整理好的 src（測建置、燈號、預測）。
房價刻意由「9 個月前的成交量」與「12 個月前的利率變動」帶動，模型應該學得到。"""
import math, os, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
import build_market as K

TODAY = (2026, 11, 16)
START, END = K.M(2010, 1), K.M(2026, 10)      # 原始數列範圍


# ---------- 原始格式（各來源前幾列）----------
PRICE_CSV = ('住宅價格月指數類別,期別,月指數,季移動平均數,半年移動平均數,月指數變動率,季移動平均數變動率,半年移動平均數變動率,標準住宅總價（新台幣萬元）,標準住宅單價（新台幣萬元每坪）\n'
             '全市,101/08,91.69,-,-,0%,-,-,1452,46.26\n全市,101/09,91.27,-,-,-0.45%,-,-,1446,46.05\n全市公寓,101/09,88.00,-,-,0%,-,-,1100,40.00\n')
RENT_CSV = '住宅租金指數類別,期別,季指數,季變動率,標準租金單價（新台幣元每坪每月）\n全市,107Q3,88.26,0.42,"1,172"\n公寓,107Q3,80.00,0.1,"1,000"\n'
CBC_RATE_CSV = ('"期間","購屋貸款-金額","購屋貸款-利率","資本支出貸款-金額","資本支出貸款-利率","週轉金貸款-金額","週轉金貸款-利率","消費性貸款-金額","消費性貸款-利率","合計-金額","合計-利率"\n'
                '"2026M08","57165","2.321","121826","2.435","1055372","2.147","9414","2.568","1243777","2.187"\n'
                '"2026M09","69783","2.315","90874","2.477","1156276","2.160","8714","2.628","1325647","2.193"\n')
CBC_LOANS_CSV = ('"期間","消費者貸款-小計-原始值","消費者貸款-小計-年增率","消費者貸款-購置住宅貸款-原始值","消費者貸款-購置住宅貸款-年增率","建築貸款-原始值","建築貸款-年增率"\n'
                 '"1988M01","-","-","-","-","-","-"\n"2026M09","14032891","5.03957","12023429","5.01505","3397229","-0.98718"\n')
FSI_CSV = '民國年,都市地價總指數(比率),住宅價格指數(比率),住宅不動產放款/放款總額(比率),商業不動產放款/放款總額(比率)\n91,75.83,-,-,-\n113,-,150.98,34.16,19.78\n114,-,145.52,34.27,19.7\n'
COST_CSV = ('"統計期","臺北市營造工程物價指數/總指數","臺北市營造工程物價指數/總指數漲跌率[%]","黃金[飾金][元/每臺錢]"\n'
            '"115年 9月",117.70,0.40,17406.67\n"115年 10月",117.91,0.18,0\n')
PERMIT_XML = ('<?xml version="1.0" encoding="UTF-8"?>\n<Datas>\n'
              '<Data><執照年度>115</執照年度><發照日期>1150106</發照日期><建造類別>新建</建造類別><建物資訊><棟數>2</棟數><戶數>10</戶數></建物資訊></Data>\n'
              '"<Data><執照年度>115</執照年度><發照日期>1150120</發照日期><建造類別>新建</建造類別><建物資訊><戶數>5</戶數></建物資訊><建物資訊><戶數>3</戶數></建物資訊></Data>"\n'
              '<Data><發照日期>1150125</發照日期><建造類別>增建</建造類別><建物資訊><戶數>99</戶數></建物資訊></Data>\n'
              '<Data><發照日期>1150203</發照日期><建造類別>新建</建造類別><備註說明>無戶數</備註說明></Data>\n</Datas>\n')
PRESALE_CSV = '行政區,年份,季別,案件數,總戶數\n大安區,114年,第1季,6,342\n信義區,114年,第1季,1,58\n大安區,114年,第2季,2,100\n'
LVR_HEAD = '鄉鎮市區,交易標的,土地位置建物門牌,交易年月日,建物型態,主要用途,建築完成年月,單價元平方公尺'
LVR_A = (LVR_HEAD + '\nThe villages and towns urban district,transaction sign,address,date,building state,main use,complete date,unit price\n'
         '大安區,房地(土地+建物),某路1號,1150812,住宅大樓(11層含以上有電梯),住家用,0950301,302500\n'
         '大安區,房地(土地+建物)+車位,某路2號,1150820,公寓(5樓含以下無電梯),住家用,0700101,180000\n'
         '大安區,土地,某段,1150820,其他,,,10000\n'
         '大安區,房地(土地+建物),某路3號,1150821,店面(店鋪),商業用,0900101,500000\n')
LVR_B = (LVR_HEAD + '\nThe villages and towns urban district,transaction sign,address,date,building state,main use,complete date,unit price\n'
         '大安區,房地(土地+建物),某路預售,1150815,住宅大樓(11層含以上有電梯),,,605000\n')


# ---------- 模擬數列 ----------
def series():
    rnd = random.Random(7)
    ks = list(range(START, END + 1))
    rate = {k: 1.9 + 0.4 * math.sin((k - START) / 30) + (0.5 if k > K.M(2022, 3) else 0) for k in ks}
    vol = {}
    for k in ks:
        vol[k] = 2600 * (1 + 0.35 * math.sin((k - START) / 14)) * (1 - 2.2 * (rate[k] - rate.get(k - 12, rate[k]))) + rnd.gauss(0, 60)
        vol[k] = max(400, vol[k])
    price, p = {}, 80.0
    for k in ks:
        drive = 0.010 * ((vol.get(k - 9, 2600) / 2600) - 1) * 4 - 0.006 * (rate.get(k - 12, rate[k]) - rate.get(k - 24, rate[k])) * 4
        p *= 1 + 0.002 + drive + rnd.gauss(0, 0.002)
        price[k] = p
    return ks, rate, vol, price


def src():
    ks, rate, vol, price = series()
    rnd = random.Random(3)
    s = {'price': {k: (round(price[k], 2), round(price[k] * 15.8), round(price[k] * 0.5, 2)) for k in ks if k >= K.M(2012, 8) and k <= K.M(2026, 9)},
         'rent': {k: 1100 + (k - START) * 1.2 for k in ks if k % 3 == 2 and k >= K.M(2018, 9)},
         'presale_supply': {k: 2000 + rnd.randint(-500, 500) for k in ks if k % 3 == 2 and k >= K.M(2023, 3)},
         'cbc_rate': {k: (60000 * vol[k] / 2600, round(rate[k], 3)) for k in ks if k <= K.M(2026, 9)},
         'cbc_loans': {k: (6 + 2 * math.sin((k - START) / 20), 3 + 4 * math.sin((k - START) / 25)) for k in ks if k <= K.M(2026, 9)},
         'fsi': {2022: 33.5, 2023: 33.54, 2024: 34.16, 2025: 34.27},
         'cost': {k: 60 * (1.004 ** (k - START)) for k in ks},
         'permits': {k: [20, int(900 * (1 + 0.3 * math.sin((k - START) / 18)))] for k in ks},
         'uses': {k: [15, int(800 * (1 + 0.3 * math.sin((k - START) / 18 - 2)))] for k in ks}}
    a, b = [], []
    for k in ks:
        if k < K.M(2012, 7):
            continue
        n = int(vol[k] / 20)                         # 每月約 100 筆（縮小規模，加快測試）
        for i in range(n):
            a.append((k, price[k] * 0.9 * (1 + rnd.gauss(0, 0.1)), i % 2 == 0, 5 + i % 30))
        for i in range(max(3, n // 8)):
            b.append((k, price[k] * 0.9 * 1.45 * (1 + rnd.gauss(0, 0.08)), True, None))
    s['lvr_a'], s['lvr_b'] = a, b
    return s
