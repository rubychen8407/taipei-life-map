"""診斷：實價登錄清理時各規則刪掉了什麼（只印統計與樣本，不寫檔）。"""
import collections, io, re, sys, zipfile, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_realprice as R
C = collections.Counter
use, note_kw, note_ex, pk, typ, inc, tgt = C(), C(), [], C(), C(), C(), C()
pk_notes = C(); ages = C()
for s in ['115S2', '115S3']:
    z = zipfile.ZipFile(io.BytesIO(R.http(R.BASE + f'DownloadSeason?season={s}&type=zip&fileName=lvr_landcsv.zip')))
    for r in R.read_csv(z, 'a_lvr_land_a.csv'):
        tg = r.get('交易標的') or ''; tgt[tg] += 1
        if not tg.startswith('房地'): continue
        bt = r.get('建物型態') or ''
        t = next((i for i, n in enumerate(R.TYPES) if bt.startswith(n)), None)
        u = r.get('主要用途') or ''
        if t is not None and u != '住家用': use[u] += 1
        if t is None: typ[bt[:12]] += 1; continue
        if u != '住家用': continue
        m = R.BAD_NOTE.findall(r.get('備註') or '')
        if m:
            for k in set(m): note_kw[k] += 1
            if len(note_ex) < 40: note_ex.append((r.get('備註') or '')[:90])
            continue
        d, b = R.roc_date(r.get('交易年月日')), R.roc_date(r.get('建築完成年月'))
        fl, tot = R.floor_of(r.get('移轉層次')), R.cn_num((r.get('總樓層數') or '').replace('層', ''))
        if not d or not b or not fl or not tot or fl > tot:
            inc[('no_date' if not d else 'no_built' if not b else 'floor:' + (r.get('移轉層次') or '')[:10])] += 1; continue
        age = (d - b).days / 365.25
        if age < 1: ages['<1'] += 1; continue
        pp, pa = R.num(r.get('車位總價元')), R.num(r.get('車位移轉總面積平方公尺') or r.get('車位移轉總面積(平方公尺)'))
        if ('車位' in tg or pa > 0) and (pp <= 0 or pa <= 0):
            pk[f"price={'0' if pp<=0 else '>0'} area={'0' if pa<=0 else '>0'} n_pk={r.get('建物現況格局-車位') or r.get('交易筆棟數','')[-4:]}"] += 1
            pk_notes[(r.get('備註') or '')[:40]] += 1
p = lambda t, c, n=25: print(f'\n== {t}', *[f'  {v:6d}  {k}' for k, v in c.most_common(n)], sep='\n')
p('交易標的', tgt, 10); p('三種住宅型態但主要用途不是「住家用」', use); p('不是三種型態的建物型態', typ, 12)
p('備註被排除的關鍵字', note_kw); print('\n== 被排除的備註樣本', *note_ex, sep='\n  ')
p('資料不完整', inc, 15); p('屋齡', ages); p('車位沒拆價', pk); p('車位沒拆價的備註', pk_notes, 15)
