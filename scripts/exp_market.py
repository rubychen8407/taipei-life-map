import pickle, os, sys, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_market as K
src = K.fetch(log=lambda *a: None)
pickle.dump(src, open('/tmp/src.pkl', 'wb'))
# 建 S（沿用 build 的前段）
import types
orig = K.forecast
cap = {}
def grab(S, min_train=48):
    cap['S'] = S; return orig(S, min_train)
K.forecast = grab
K.build(src, out_dir='/tmp/o', log=print)
S = cap['S']
from collections import Counter
P = S['price']
labs = Counter(K.label(K.pct(P, k + 12, 12)) for k in P if (k + 12) in P and (k - 12) in P)
print('class dist (up,flat,down)', labs)
allf = list(K.FEATS)
def run(feats, lam, thr):
    K.FEATS = feats; K.THR = thr
    old = K.fit_logit
    K.fit_logit = lambda X, y, lam_=lam, **kw: old(X, y, lam=lam_)
    try:
        r = orig(S)
    except Exception as e:
        return str(e)
    finally:
        K.fit_logit = old
    b = r['backtest']; return f"acc {b['acc']} base {b['base_acc']} maj {b['maj_acc']} n {b['n']} probs {r['probs']}"
for thr in (2.0, 3.0, 5.0):
    for lam in (0.3, 1.0, 3.0):
        print('ALL', thr, lam, run(allf, lam, thr))
    for sub in (['mom12', 'vol_yoy', 'loan_yoy'], ['mom12', 'mom6', 'vol_yoy', 'rate_chg'], ['vol_yoy', 'loan_yoy', 'rate_chg'], ['mom6', 'vol_yoy', 'loan_yoy', 'cost_yoy'], ['mom12', 'vol_yoy', 'loan_yoy', 'rate_chg', 'permit_yoy']):
        fs = [f for f in allf if f[0] in sub]
        print(sub, thr, run(fs, 1.0, thr))
