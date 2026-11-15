"""Overpass `out geom` 格式的模擬：興建中的環狀線北環、規劃中的民生汐止線、一段高鐵（要被排除）、幾個車站"""
def way(i, tags, pts):
    return {'type': 'way', 'id': i, 'tags': tags, 'geometry': [{'lat': a, 'lon': b} for a, b in pts]}
def node(i, tags, la, lo):
    return {'type': 'node', 'id': i, 'lat': la, 'lon': lo, 'tags': tags}
def mock():
    return [
        way(1, {'railway': 'construction', 'construction': 'subway', 'name': '捷運環狀線北環段 (興建中)', 'ref': 'Y'}, [(25.040, 121.530), (25.045, 121.540), (25.050, 121.548)]),
        way(2, {'railway': 'construction', 'construction': 'subway', 'name': '捷運環狀線北環段地下引道', 'ref': 'Y'}, [(25.050, 121.548), (25.052, 121.552)]),
        way(3, {'railway': 'proposed', 'proposed': 'subway', 'name': '捷運民生汐止線'}, [(25.058, 121.540), (25.058, 121.560), (25.060, 121.580)]),
        way(4, {'railway': 'proposed', 'proposed': 'rail', 'name': '台灣高速鐵路'}, [(25.05, 121.60), (25.06, 121.62)]),
        way(5, {'railway': 'rail', 'name': '臺鐵縱貫線'}, [(25.04, 121.50), (25.05, 121.51)]),
        node(10, {'railway': 'construction', 'construction': 'station', 'name': '環狀線Y20站', 'ref': 'Y20'}, 25.0451, 121.5401),
        node(11, {'railway': 'station', 'construction': 'yes', 'ref': 'Y20'}, 25.0452, 121.5402),
        node(12, {'proposed:railway': 'station', 'name': '民生社區', 'ref': 'SB05'}, 25.0581, 121.5600),
        node(13, {'railway': 'proposed', 'proposed': 'station', 'name': '高鐵某站'}, 25.055, 121.61),
    ]
