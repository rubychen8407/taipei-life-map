"""行道樹模擬資料：TaipeiTree.csv 格式（TWD97 座標），大安區一帶與信義區幾棵＋幾筆異常資料"""
import math, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
HEAD = 'TreeID,Dist,Region,RegionRemark,TreeType,Diameter,TreeHeight,SurveyDate,TWD97X,TWD97Y,UpdDate'


def to_twd97(lat, lon):
    """WGS84 → TWD97 TM2（測試用，與 build_realprice.twd97 互為反函數）"""
    a, b = 6378137.0, 6356752.314245; e2 = 1 - (b / a) ** 2; ep2 = e2 / (1 - e2); k0, lon0 = 0.9999, math.radians(121)
    p, l = math.radians(lat), math.radians(lon)
    N = a / math.sqrt(1 - e2 * math.sin(p) ** 2); T = math.tan(p) ** 2; C = ep2 * math.cos(p) ** 2; A = (l - lon0) * math.cos(p)
    M = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * p - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * p)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * p) - (35 * e2 ** 3 / 3072) * math.sin(6 * p))
    x = 250000 + k0 * N * (A + (1 - T + C) * A ** 3 / 6 + (5 - 18 * T + T ** 2 + 72 * C - 58 * ep2) * A ** 5 / 120)
    y = k0 * (M + N * math.tan(p) * (A ** 2 / 2 + (5 - T + 9 * C + 4 * C ** 2) * A ** 4 / 24 + (61 - 58 * T + T ** 2 + 600 * C - 330 * ep2) * A ** 6 / 720))
    return round(x, 2), round(y, 2)


def mock():
    """回傳 CSV 文字。忠孝東路（25.0415）一排 40 棵、仁愛路 20 棵、信義區 5 棵；另有重複、沒座標、市外各一筆"""
    rows, i = [HEAD], 0
    def add(lat, lon, dist, road, side, sp, d, h, tid=None):
        nonlocal i; i += 1; x, y = to_twd97(lat, lon)
        rows.append(f'{tid or "T%05d" % i},{dist},{road},{side},{sp},{d},{h},2023-05-01,{x},{y},2026-11-13 09:28:42')
    for k in range(40):
        add(25.0415, 121.538 + k * 0.0002, '大安區', '忠孝東路四段', '南側人行道', ['臺灣欒樹', '樟樹'][k % 2], 20 + k, 8.5)
    for k in range(20):
        add(25.0375, 121.540 + k * 0.0002, '大安區', '仁愛路四段', '分隔島', '樟樹', 35, 12)
    for k in range(5):
        add(25.033, 121.565 + k * 0.0003, '信義區', '松仁路', '東側人行道', '大葉欖仁', 18, 7.2)
    add(25.033, 121.567, '信義區', '松仁路', '東側人行道', '大葉欖仁', 18, 999)                         # 樹高登錄錯誤
    add(25.0415, 121.538, '大安區', '忠孝東路四段', '南側人行道', '臺灣欒樹', 20, 8.5, tid='T00001')   # 重複
    rows.append('T99998,大安區,某路,,榕樹,10,5,2023-05-01,,,2026-11-13')                          # 沒座標
    add(24.70, 121.10, '外縣市', '某路', '', '榕樹', 10, 5)                                             # 市外
    return '\n'.join(rows) + '\n'
