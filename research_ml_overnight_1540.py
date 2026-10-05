"""Overnight model, decision-time honest (Oct 4 2026 — docs/RESEARCH_REGISTRY.md §F).

research_ml_model.py's overnight model scored IC +0.065 using features built on the 16:00 close. But an
overnight book must decide by ~15:45 (MOC orders close at 15:50), and the target is measured FROM the 16:00
print — so any feature that sees the last 15-20 minutes both peeks past the decision and shares the closing
print's noise with the target (a bounce-type artifact). This rebuild uses only what is known at 15:40:
  * every daily panel feature LAGGED one session (computed through yesterday's close), plus
  * today's facts known by 15:40: the opening gap, open→15:40 move, 15:40 close-location in the day's
    range so far, distance from VWAP-to-15:40, the 15:10→15:40 and 14:40→15:40 moves, the range so far.
Entry = today's 16:00 close (MOC), exit = tomorrow's open — the same target as before.
Compares, walk-forward on identical months: the 16:00 model (look-ahead) vs this 15:40 model vs
Clockwork's signal, and reports the WILD-only top-3 book Clockwork actually runs.

Usage: venv/bin/python research_ml_overnight_1540.py
"""
import os, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
from collect_bars import load_bars  # noqa: E402
import research_ml_model as M  # noqa: E402

OUT = os.path.join(ROOT, 'research_out')
SNAP = os.path.join(OUT, 'ml_snap1540.parquet')


def snapshots(symbols):
    if os.path.exists(SNAP):
        return pd.read_parquet(SNAP)
    end = (pd.Timestamp.today() + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    rows = []
    for s in symbols:
        b = load_bars(s, start='2024-01-01', end=end)
        if b is None or len(b) < 3000:
            continue
        b = b.between_time('09:30', '15:35').copy()           # bars that START by 15:35 → known at 15:40
        b['d'] = b.index.normalize().tz_localize(None)
        b['tpv'] = (b.high + b.low + b.close) / 3 * b.volume
        mins = b.index.hour * 60 + b.index.minute
        g = b.groupby('d')
        d = pd.DataFrame({'o': g.open.first(), 'px': g.close.last(), 'hi': g.high.max(), 'lo': g.low.min(),
                          'vw': g.tpv.sum() / g.volume.sum().replace(0, np.nan), 'n': g.close.size()})
        d['p1510'] = b[mins == 905].groupby('d').close.last()
        d['p1440'] = b[mins == 875].groupby('d').close.last()
        d = d[d.n >= 70]
        rng = (d.hi - d.lo).replace(0, np.nan)
        rows.append(pd.DataFrame({'date': d.index, 'symbol': s,
                                  't_open_1540': (d.px / d.o - 1).values,
                                  't_clv1540': ((d.px - d.lo) / rng).values,
                                  't_vwapdist1540': (d.px / d.vw - 1).values,
                                  't_ret_1510_1540': (d.px / d.p1510 - 1).values,
                                  't_ret_1440_1540': (d.px / d.p1440 - 1).values,
                                  't_range1540': (rng / d.o).values}))
    sn = pd.concat(rows, ignore_index=True)
    sn.to_parquet(SNAP, index=False)
    return sn


def build_honest():
    """(p, X_1600, X_honest, B): panel, the 16:00-close features, the 15:40-honest features, benchmarks."""
    p, X, stock_feats, B = M.load()
    p = p.copy()
    # LAG every stock-level panel feature one session; keep today's gap (known at the open)
    order = p.sort_values(['symbol', 'date']).index
    Xl = X.loc[order].copy()
    Xl[stock_feats] = Xl.groupby(p.loc[order, 'symbol'])[stock_feats].shift(1)
    Xl['gap_today'] = X.loc[order, 'on'] if 'on' in X else np.nan
    for c in M.MARKET_COLS:                                    # market context: yesterday's values too
        if c not in ('dow', 'month_end'):
            Xl[c] = Xl.groupby(p.loc[order, 'symbol'])[c].shift(1)
    Xl = Xl.loc[p.index]
    sn = snapshots(sorted(p.symbol.unique()))
    sn['date'] = pd.to_datetime(sn.date)
    q = p[['date', 'symbol']].reset_index().merge(sn, on=['date', 'symbol'], how='left').set_index('index')
    g = q.groupby('date')
    for c in [c for c in sn.columns if c.startswith('t_')]:
        Xl[c] = g[c].rank(pct=True) - 0.5
    return p, X, Xl, B


def main():
    p, X, Xl, B = build_honest()
    # walk-forward, overnight only
    y, k = 'y_on', 1
    pr_honest, _ = M.walk_forward(p, Xl, y, k)
    pr_1600, _ = M.walk_forward(p, X, y, k)
    res = p[['date', 'symbol', y, y + '_xs']].copy()
    res['model_1600_lookahead'] = pr_1600
    res['model_1540_honest'] = pr_honest
    res['clockwork_on_cons30'] = B['on_cons30']
    res = res[res.date >= M.TEST_START]
    pe = pd.read_csv('/Users/sushil/trading/factory/cache/personality.csv')
    wild = set(pe[pe.cluster == 'WILD'].symbol)
    res.to_parquet(os.path.join(OUT, 'ml_preds_overnight_1540.parquet'), index=False)
    print(f"overnight, out-of-sample {res.date.min().date()} → {res.date.max().date()}, cost 0.10% round trip")
    for lab, sub in (('ALL names', res), ('WILD only (Clockwork universe)', res[res.symbol.isin(wild)])):
        print(f"\n  {lab}:")
        for sig in ('model_1600_lookahead', 'model_1540_honest', 'clockwork_on_cons30'):
            ic = M.daily_ic(sub, sig, y + '_xs'); m, t, h = M.ic_stats(ic, 1)
            pf = M.portfolio(sub, sig, y, M.COST[y])
            top = sub.dropna(subset=[sig]).sort_values(sig, ascending=False).groupby('date').head(3)
            raw = top.groupby('date')[y].mean() * 1e4
            print(f"    {sig:22} IC {m:+.4f} t={t:+.2f} halves[{' '.join(f'{v:+.3f}' for v in h.values)}] | "
                  f"decile {pf['decile_spread_bp']:+5.1f}bp | top3 excess {pf['top3_excess_pct'] * 100:+.1f}bp "
                  f"raw {raw.mean():+.1f}bp/night net {raw.mean() - 10:+.1f} | top5 net excess {pf['top5_net_pct'] * 100:+.1f}bp")


if __name__ == '__main__':
    main()
