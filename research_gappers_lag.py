"""Honest re-test of the evening gapper model (Oct 5 2026, RESEARCH_REGISTRY §J).

research_gappers.py section 3 builds its inputs from day t's CLOSE and measures the target from that same
close (open(t+1)/close(t)) — the shared-print problem that inflated Night Owl ~30% (registry F3). Here every
input is lagged one session (known at day t's OPEN: features through close(t-1) + today's opening gap), so no
input touches close(t). Decision in the morning, buy MOC at close(t), sell MOO at open(t+1) — Night Owl's design,
on the broad universe. If the edge survives this, it is not a closing-print artefact.
Usage: venv/bin/python research_gappers_lag.py
"""
import os, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
from research_gappers import FEATS, tstat, tradeable
OUT = os.path.join(ROOT, 'research_out')


def main():
    from sklearn.ensemble import HistGradientBoostingRegressor
    p = pd.read_parquet(os.path.join(OUT, 'gappers_panel.parquet')).sort_values(['symbol', 'date'])
    g = p.groupby('symbol')
    lagf = [f for f in FEATS if f != 'dow']
    L = g[lagf + ['price', 'dvol20']].shift(1)
    L.columns = [c + '_l' for c in L.columns]
    p = pd.concat([p, L], axis=1)
    p['gap_today'] = p['gap']                                   # open(t)/close(t-1): known at 09:30
    p = p[(p.price_l >= 2) & (p.dvol20_l >= 1e6) & p.gap_n.notna() & p.id_n.notna()]
    feats = [f + '_l' for f in lagf] + ['gap_today', 'dow']
    X = p[feats].copy()
    for c in feats:
        if c not in ('log_price_l', 'log_dvol_l', 'age_l', 'dow'):
            X[c] = p.groupby('date')[c].rank(pct=True) - 0.5
    y = p.groupby('date')['gap_n'].rank(pct=True) - 0.5
    pred = pd.Series(np.nan, index=p.index)
    qs = pd.date_range('2025-01-01', p.date.max() + pd.offsets.QuarterBegin(1), freq='QS')
    for a, b in zip(qs[:-1], qs[1:]):
        te = (p.date >= a) & (p.date < b); tr = p.next_date < a
        if te.sum() == 0:
            continue
        m = HistGradientBoostingRegressor(max_iter=250, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=400,
                                          l2_regularization=1.0, random_state=7).fit(X[tr], y[tr])
        pred[te] = m.predict(X[te])
    r = p.assign(pred=pred).dropna(subset=['pred'])
    ic = r.groupby('date').apply(lambda d: d.pred.rank().corr(d.gap_n.rank())).dropna()
    print(f'LAGGED (decide at the open, no close(t) input): OOS {r.date.min().date()} → {r.date.max().date()}, {r.date.nunique()} nights')
    print(f'  IC with tonight\'s gap {ic.mean():+.4f} (t {tstat(ic):+.2f}); by quarter ' +
          ' '.join(f'{k}:{v:+.3f}' for k, v in ic.groupby(ic.index.to_period("Q")).mean().items()))
    for k in (5, 10, 25):
        top = r.sort_values('pred', ascending=False).groupby('date').head(k)
        avg = r.groupby('date')['gap_n'].mean()
        on = top.groupby('date')['gap_n'].mean() * 1e4
        ex = (on / 1e4 - avg).dropna() * 1e4
        idn = top.groupby('date')['id_n'].mean() * 1e4
        q = on.groupby(on.index.to_period('Q')).mean()
        print(f'  top-{k:<2d} overnight {on.mean():+6.1f}bp (excess {ex.mean():+6.1f}, t {tstat(ex):+.2f}, median {top.gap_n.median() * 1e4:+.0f}) '
              f'| then open→close {idn.mean():+6.1f}bp | median price ${top.price.median():.1f} | quarters ' + ' '.join(f'{a}:{b:+.0f}' for a, b in q.items()))
    for mp in (5.0, 10.0):
        rr = r[r.price_l >= mp]
        top = rr.sort_values('pred', ascending=False).groupby('date').head(10)
        on = top.groupby('date')['gap_n'].mean() * 1e4
        ex = (on / 1e4 - rr.groupby('date')['gap_n'].mean()).dropna() * 1e4
        print(f'  price ≥ ${mp:.0f}: top-10 overnight {on.mean():+6.1f}bp (excess {ex.mean():+.1f}, t {tstat(ex):+.2f}) '
              f'then open→close {top.groupby("date")["id_n"].mean().mean() * 1e4:+.1f}bp')
    r[['date', 'symbol', 'pred', 'gap_n', 'id_n', 'price', 'dvol20']].to_parquet(os.path.join(OUT, 'gappers_lag_preds.parquet'), index=False)


if __name__ == '__main__':
    main()
