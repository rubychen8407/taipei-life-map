"""都市更新地區（ArcGIS GeoJSON）模擬資料"""
def sq(lat, lon, d=0.0008):
    return [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]

def mock(n=6):
    fs = []
    descs = [('本府89年06月26日公告劃定', 646358400000), ('依都市更新條例第十一條劃定', None), ('震災及高氯離子劃定', 1600000000000),
             ('本府91年10月28日公告劃定', 720230400000), (' ', None), ('本府89年06月26日公告劃定', 646358400000)]
    pts = [(25.0400, 121.5430), (25.0420, 121.5500), (25.0350, 121.5600), (25.0500, 121.5450), (25.0300, 121.5300), (25.0450, 121.5380)]
    for i in range(n):
        d, ms = descs[i % len(descs)]; la, lo = pts[i % len(pts)]
        g = {'type': 'Polygon', 'coordinates': [sq(la, lo)]}
        if i == 5:   # 多塊 + 洞
            g = {'type': 'MultiPolygon', 'coordinates': [[sq(la, lo), sq(la + 0.0002, lo + 0.0002, 0.0002)], [sq(la + 0.002, lo, 0.0004)]]}
        fs.append({'type': 'Feature', 'geometry': g, 'properties': {'OBJECTID': i + 1, 'PLANDES': d, 'PLANDATE': ms}})
    fs.append({'type': 'Feature', 'geometry': None, 'properties': {'OBJECTID': 99, 'PLANDES': '沒有範圍'}})
    return fs
