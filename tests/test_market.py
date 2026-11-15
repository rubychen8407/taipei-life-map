"""整體房市（scripts/build_market.py）單元測試"""
import json, os, sys, tempfile, time
from datetime import date
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts')); sys.path.insert(0, HERE)
import build_market as K, market_mock as MM
fails = []
def check(ok, name):
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok: fails.append(name)
M = K.M
p = K.parse_price_index(MM.PRICE_CSV)
check(p == {M(2012, 8): (91.69, 1452, 46.26), M(2012, 9): (91.27, 1446, 46.05)}, '讀住宅價格月指數（只取全市；民國年/月）')
check(K.parse_rent(MM.RENT_CSV) == {M(2018, 9): 1172}, '讀租金指數（季→季末月，千分位逗號）')
check(K.parse_cbc_rate(MM.CBC_RATE_CSV)[M(2026, 9)] == (69783, 2.315), '讀五大銀行新承做購屋貸款金額與利率')
lo = K.parse_cbc_loans(MM.CBC_LOANS_CSV)
check(len(lo) == 1 and abs(lo[M(2026, 9)][0] - 5.01505) < 1e-9 and abs(lo[M(2026, 9)][1] + 0.98718) < 1e-9, '讀購置住宅貸款與建築貸款年增率（略過 "-"）')
check(K.parse_fsi(MM.FSI_CSV) == {2024: 34.16, 2025: 34.27}, '讀住宅不動產放款占比（略過 "-"）')
check(K.parse_tsis_cost(MM.COST_CSV) == {M(2026, 9): 117.70, M(2026, 10): 117.91}, '讀臺北市營造工程物價指數（「115年 9月」格式）')
pm = K.parse_permits(MM.PERMIT_XML)
check(pm == {M(2026, 1): [2, 18], M(2026, 2): [1, 0]}, f'讀建照／使照摘要：只算新建、加總多棟戶數、容忍被引號包住的列 {pm}')
check(K.parse_presale_supply(MM.PRESALE_CSV) == {M(2025, 3): 400, M(2025, 6): 100}, '讀預售推案戶數（各區加總成全市）')
la = K.parse_lvr(MM.LVR_A); lb = K.parse_lvr(MM.LVR_B, presale=True)
check(len(la) == 2 and la[0][0] == M(2026, 8) and abs(la[0][1] - 100.0) < 0.1 and la[0][2] and abs(la[0][3] - 20.4) < 0.1 and not la[1][2], f'讀實價登錄：只收住家用房地、單價換成萬／坪、屋齡、是否住宅大樓 {la}')
check(len(lb) == 1 and lb[0][3] is None and lb[0][2], '讀預售檔（主要用途空白也收）')
check(K.seasons_since((114, 3), date(2026, 1, 5)) == ['114S3', '114S4'] and K.seasons_since((115, 3), date(2026, 11, 16)) == ['115S3'], '季檔清單只到上一季（本季還沒有季檔）')
src = MM.src()
with tempfile.TemporaryDirectory() as d:
    t0 = time.time()
    res = K.build(src, out_dir=d, built='2026-11-16', today=date(*MM.TODAY), log=lambda *a: None)
    dt = time.time() - t0
    f = json.load(open(os.path.join(d, 'market.json'), encoding='utf-8'))
    keys = {i['key'] for i in f['indicators']}
    check({'price', 'premium', 'yield', 'vol', 'pvol', 'permit', 'use', 'presale_supply', 'rate', 'loan', 'house_loan', 'build_loan', 'cost', 'pay', 'fsi'} <= keys, f'產生 15 項指標（四大構面）{sorted(keys)}')
    check(all(i['how'] and i['pts'] and i['signal'] in (-1, 0, 1) for i in f['indicators']), '每項指標都有解釋、歷史數列與訊號（支撐／中性／壓力）')
    vol = next(i for i in f['indicators'] if i['key'] == 'vol')
    check(vol['t'] == '2026-08', f'實價登錄最近 2 個月申報不完整，不列入（最新到 {vol["t"]}）')
    pr = next(i for i in f['indicators'] if i['key'] == 'premium')
    check(35 < pr['v'] < 55, f'預售比中古貴約 45%（模擬設定）：{pr["v"]}%')
    fc = f['forecast']; bt = fc['backtest']
    check(abs(sum(fc['probs'].values()) - 1) < 0.01 and fc['call'] in ('up', 'flat', 'down') and fc['horizon'] == 12, f'未來 12 個月上漲／持平／下跌機率加總 100% {fc["probs"]}')
    check(bt['n'] >= 40 and bt['acc'] is not None and bt['acc'] >= bt['maj_acc'] - 0.05, f'回測：{bt["n"]} 個月，命中 {bt["acc"]}，多數類別基準 {bt["maj_acc"]}，延續基準 {bt["base_acc"]}')
    check(all(h[0] <= f['through'] for h in fc['history']) and fc['history'][-1][4] is None, '回測紀錄只用當時已知的資料；最近 12 個月還沒有答案')
    check(len(fc['contrib']) == len(K.FEATS) == 7 and all('push' in c for c in fc['contrib']), '列出 7 個特徵對預測的推力（說明為什麼）')
    lt = f['light']
    check(lt and 0 <= lt['level'] <= 4 and lt['label'] in ('藍燈', '黃藍燈', '綠燈', '黃紅燈', '紅燈') and len(lt['parts']) >= 4, f'房市溫度燈號：{lt and lt["label"]}（{lt and lt["score"]}）')
    check(len(f['chain']) == 4 and all(c['status'] in (-1, 0, 1) for c in f['chain']), '四階段傳導鏈：信貸→量→供給→成本，各給支撐／中性／壓力')
    check(dt < 60, f'建置時間 {dt:.1f} 秒')
    try:
        bad = dict(src); bad['price'] = dict(list(src['price'].items())[:30])
        K.build(bad, out_dir=os.path.join(d, 'x'), today=date(*MM.TODAY), log=lambda *a: None); ok = False
    except RuntimeError:
        ok = not os.path.exists(os.path.join(d, 'x', 'market.json'))
    check(ok, '房價指數太短（下載不完整）：建置失敗、不寫出資料')
print(f"\n{'全部通過' if not fails else str(len(fails)) + ' 項失敗'}"); sys.exit(1 if fails else 0)
