"""都更案件（scripts/build_urcase.py）單元測試"""
import json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts')); sys.path.insert(0, HERE)
import build_urcase as U, urcase_mock as M
fails = []
def check(ok, name):
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok: fails.append(name)
check([U.status_of(x) for x in ['完工', '已完工', '施工中', '施工', '權變審議', '權利變換審查', '核定', '權變核定', '']] == ['done', 'done', 'build', 'build', 'review', 'review', 'ok', 'ok', 'ok'], '目前狀態歸成：已核定／權變審議中／施工中／已完工')
check(U.status_of('', '崇仁花園', 'pub') == 'done' and U.status_of('', '', 'pub') == 'ok', '政府主導：有完工案名＝已完工，否則已核定')
check([U.ymd(x) for x in ['2019/7/25', '2012/05/18', '2000 ', '']] == ['2019-07-25', '2012-05-18', '2000', ''], '日期轉成 YYYY-MM-DD')
check(U.short_case('擬訂臺北市南港區玉成段二小段166地號等12筆土地都市更新事業計畫案') == '南港區玉成段二小段166地號等12筆' and U.short_case('變更臺北市中山區吉林段四小段705地號等46筆土地(原某社區)都市更新事業計畫案') == '中山區吉林段四小段705地號等46筆', '案名縮短成地段地號')
cs = U.parse(M.mock())
check(len(cs) == 6 and not any('逸仙' in c.get('n', '') and c['k'] != 'pub' for c in cs), f'案件只取已核定重建更新事業與政府主導（沒有範圍的略過）{len(cs)}')
un, ar, lp = U.parse_more(M.mock())
check(len(un) == 1 and lp == 1 and un[0]['n'] == '大安區懷生段一小段1地號等6筆土地' and un[0]['d'] == '2002-05-14' and un[0]['a'] == 3378, f'自劃單元：只留已核准（已失效的只計數），名稱去掉「臺北市」「為更新單元」 {[{k: v for k, v in u.items() if k != "r"} for u in un]}')
check(len(ar) == 1 and ar[0]['k'] == 'pri' and ar[0]['d'] == '2005-11-24' and ar[0]['a'] == 1098 and '計畫案' in ar[0]['c'], '更新地區：劃定依據、公告日期、計畫書名稱、面積')
c0 = next(c for c in cs if c['n'] == '親家JIA')
check(c0['s'] == 'done' and c0['y'] == 2024 and c0['d'] == '2013-06-22' and c0['i'] == '敦寶建設股份有限公司' and c0['m'] == '權利變換' and c0['a'] == 1292 and c0['k'] == 'self' and c0['dist'] == '大安區', f'欄位：狀態、完工年、核定日、實施者、實施方式、基地面積、劃定依據 {dict((k, v) for k, v in c0.items() if k != "r")}')
check(c0['r'][0][0] == [25.0345, 121.5458] and len(c0['r'][0]) == 5, '座標轉成 [緯度, 經度]')
c1 = next(c for c in cs if c['no'] == '北396')
check(c1['n'] == '' if 'n' in c1 else True, '沒有建案名就不填（app 用地段地號代替）')
check(c1['a'] == 2198 and c1['c'] == '大安區懷生段二小段166地號等12筆' and 'y' not in c1, '面積有千分位也能讀、空的欄位不輸出')
pub = [c for c in cs if c['k'] == 'pub']
check(len(pub) == 2 and {c['s'] for c in pub} == {'ok', 'done'} and pub[0]['i'] == '東家建設股份有限公司', '政府主導案件：實施者、招商成功＝已核定、有完工案名＝已完工')
with tempfile.TemporaryDirectory() as d:
    out = U.build(M.mock(), out_dir=d, built='2026-11-24', min_cases=3, log=lambda *a: None)
    f = json.load(open(os.path.join(d, 'urcase.json'), encoding='utf-8'))
    check(f['count'] == {'ok': 2, 'review': 1, 'build': 1, 'done': 2, 'unit': 1, 'unit_lapsed': 1, 'area': 1} and len(f['units']) == 1 and len(f['areas']) == 1 and f['credit'] == '財團法人都市更新研究發展基金會' and f['source'].startswith('https://www.ur.org.tw/'), f"輸出各狀態數量與資料來源標示 {f['count']}")
    try:
        U.build(M.mock(), out_dir=d, min_cases=300, log=lambda *a: None); check(False, '太少時不更新')
    except SystemExit:
        check(True, '案件太少時建置失敗、不覆蓋舊資料')
print(f'\n{len(fails)} 項失敗：' + '、'.join(fails) if fails else '\n全部通過')
sys.exit(1 if fails else 0)
