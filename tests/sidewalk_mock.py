"""產生內政部人行道格式的模擬資料（臺北市＋新北市），給單元測試與 e2e 共用。"""
import random


def strip(lat, lon, length_m=120, width_m=2.0, horizontal=True):
    dl, dw = length_m / 101000, width_m / 110540
    if horizontal:
        ring = [[lon, lat], [lon + dl, lat], [lon + dl, lat + dw], [lon, lat + dw], [lon, lat]]
    else:
        dl2, dw2 = length_m / 110540, width_m / 101000
        ring = [[lon, lat], [lon + dw2, lat], [lon + dw2, lat + dl2], [lon, lat + dl2], [lon, lat]]
    return {'type': 'Polygon', 'coordinates': [ring]}


def feature(name, net, geom, city='台北市'):
    return {'type': 'Feature', 'properties': {'NAME': name, 'SW_WTH': net + .3, 'SWW_WTH': net, 'SW_LENG': 120, 'COUNTY_NA': city}, 'geometry': geom}


def mock(seed=7, n=1600):
    rnd = random.Random(seed); tp = []
    for i in range(n):
        la, lo = rnd.uniform(24.99, 25.13), rnd.uniform(121.48, 121.62)
        net = rnd.choice([0.9, 1.2, 1.5, 2.0, 2.5, 3.5])
        tp.append(feature(f'測試路{i % 40}段', net, strip(la, lo, rnd.uniform(60, 300), net + .3, rnd.random() < .5)))
    ntp = [feature('板橋近邊界路', 2.0, strip(25.03, 121.47, 150, 2.2), '新北市'),     # 臺北市邊界外 ~1 公里：保留
           feature('三峽遠方路', 2.0, strip(24.93, 121.37, 150, 2.2), '新北市')]      # 遠在範圍外：丟掉
    return {'台北市': tp, '新北市': ntp}
