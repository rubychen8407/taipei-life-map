"""土地價值模擬資料：自己寫出小型 SHP/DBF（測試 read_shp）、110 年地號公告現值、漲跌幅、公有土地 CSV"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trees_mock import to_twd97

# 三個小段：大安區（貴）、信義區（中）、北投區（便宜，只有公有土地資料），外加一個沒有任何資料的小段
SECTS = [('0101', '仁愛段一小段', '大安區', (25.036, 121.540, 25.042, 121.548)),
         ('0102', '仁愛段二小段', '大安區', (25.030, 121.540, 25.036, 121.548)),   # 同一小段分兩筆圖形的情況用 0101 第二筆
         ('0201', '永春段一小段', '信義區', (25.040, 121.570, 25.046, 121.578)),
         ('0301', '湖山段一小段', '北投區', (25.150, 121.540, 25.156, 121.548)),
         ('0401', '空白段', '南港區', (25.050, 121.600, 25.054, 121.604))]


def ring(b):
    s, w, n, e = b
    return [to_twd97(*p) for p in [(s, w), (n, w), (n, e), (s, e), (s, w)]]


def shp_dbf(sects=SECTS, extra=True):
    recs = [(c, nm, d, [ring(b)]) for c, nm, d, b in sects]
    if extra:   # 0101 的第二塊（同一小段兩筆圖形）
        recs.append(('0101', '仁愛段一小段', '大安區', [ring((25.042, 121.540, 25.044, 121.542))]))
    body = b''
    for i, (_, _, _, rings) in enumerate(recs):
        pts = [p for r in rings for p in r]; parts = []; k = 0
        for r in rings:
            parts.append(k); k += len(r)
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        c = struct.pack('<i4d2i', 5, min(xs), min(ys), max(xs), max(ys), len(parts), len(pts)) + struct.pack(f'<{len(parts)}i', *parts) + b''.join(struct.pack('<2d', *p) for p in pts)
        body += struct.pack('>2i', i + 1, len(c) // 2) + c
    shp = struct.pack('>7i', 9994, 0, 0, 0, 0, 0, (100 + len(body)) // 2) + struct.pack('<2i4d4d', 1000, 5, 0, 0, 0, 0, 0, 0, 0, 0) + body
    fields = [('序號', 'N', 5), ('段代碼', 'C', 10), ('地段名', 'C', 50), ('行政區', 'C', 50)]
    rl = 1 + sum(f[2] for f in fields); hl = 32 + 32 * len(fields) + 1
    dbf = struct.pack('<BBBBIHH20x', 3, 126, 1, 1, len(recs), hl, rl)
    for n, t, sz in fields:
        dbf += n.encode('utf-8').ljust(11, b'\0') + t.encode() + b'\0' * 4 + bytes([sz, 0]) + b'\0' * 14
    dbf += b'\r'
    for i, (c, nm, d, _) in enumerate(recs):
        vals = [str(i + 1).rjust(5), c, nm, d]
        dbf += b' ' + b''.join(v.encode('utf-8').ljust(sz, b' ')[:sz] for v, (_, _, sz) in zip(vals, fields))
    return shp, dbf + b'\x1a'


def values_csv():
    rows = ['縣市別,行政區,段小段,地號,公告土地現值（新臺幣元每平方公尺）']
    for k, v in enumerate([800000, 820000, 900000, 790000, 810000, 10]):        # 中位數 805000
        rows.append(f'臺北市, 大安區,仁愛段一小段,{k:04d}0000,{v}')
    for k in range(6):
        rows.append(f'臺北市, 大安區,仁愛段二小段,{k:04d}0000,700000')
    for k in range(5):
        rows.append(f'臺北市, 信義區,永春段一小段,{k:04d}0000,400000')
    rows.append('臺北市, 北投區,湖山段一小段,00010000,50000')                    # 只有 1 筆：不足 5 筆，改用公有地
    return '\n'.join(rows) + '\n'


def rates_csv():
    return ('行政區,113年公告土地現值漲跌幅（比率）,114年公告土地現值漲跌幅（比率）,115年公告土地現值漲跌幅（比率）\n'
            '全市,1,1,1\n大安區,10,0,-1\n信義區,0,0,2\n北投區,0,0,0.5\n')


def public_csv():
    rows = ['行政區代碼,行政區,段代碼,段小段,地號,面積,當期公告現值金額,當期公告地價金額,土地權屬情形,土地權屬情形_比例,管理機關']
    for v in (60000, 62000, 64000):
        rows.append(f'08,北投區,0301,湖山段一小段,00020000,100,{v},10000,市有,市有(100%),臺北市政府工務局')
    rows.append('03,大安區,0101,仁愛段一小段,00090000,100,999999,10000,國有,國有(100%),某機關')
    return '\n'.join(rows) + '\n'
