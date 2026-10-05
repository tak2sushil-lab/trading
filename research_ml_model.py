"""Walk-forward learned multi-signal model (Oct 4 2026 programme — docs/RESEARCH_REGISTRY.md §F).

Reads research_out/ml_panel.parquet (research_ml_panel.py). For each horizon it:
  1. scores EVERY feature alone (daily cross-sectional rank IC, half-year consistency) — the
     "indicator scan", judged against a multiple-testing bar (|t| > 3.5 over ~450 tests);
  2. fits a gradient-boosted model and a ridge model WALK-FORWARD: re-fit at the start of every month
     on all earlier rows, purging the last (horizon + 1) trading days so no training target overlaps
     the test month; predicts that month. Every prediction is out of sample (Jan 2025 → latest);
  3. compares them with the factory's single signals on the SAME data (Contrarian = 3-day reversal,
     Clockwork = 30-night up-consistency) on rank IC, decile spread and long-only top-N excess return
     net of cost.
Features are cross-sectionally ranked per day (level shifts that hit every name on one day, like the
Jun 2026 volume-source switch, cannot leak); market-wide features stay raw (trees use them as context).
Targets are market-neutral: return minus that day's universe mean; the model trains on the rank of it.

Usage: venv/bin/python research_ml_model.py [horizons...]   (default: y_on y_id y_r1 y_r3 y_r5)
Outputs: research_out/ml_feature_ic.csv, research_out/ml_preds_<h>.parquet, research_out/ml_summary.txt
"""
import os, sys, time, warnings
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge

warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'research_out')
PANEL = os.path.join(OUT, 'ml_panel.parquet')
TEST_START = pd.Timestamp('2025-01-01')
HORIZON_DAYS = {'y_on': 1, 'y_id': 2, 'y_r1': 1, 'y_r3': 3, 'y_r5': 5, 'y_r10': 10}
COST = {'y_on': 0.10, 'y_id': 0.15, 'y_r1': 0.15, 'y_r3': 0.20, 'y_r5': 0.20, 'y_r10': 0.20}  # % round trip
MARKET_COLS = ['mkt_r1', 'mkt_r5', 'mkt_r20', 'xs_dispersion', 'breadth', 'vix', 'dow', 'month_end']
NON_FEATURES = {'date', 'symbol', 'sector', 'dna', 'close'}
BENCH = {'contrarian_r3': ('r3', -1), 'clockwork_on_cons30': ('on_cons30', +1), 'reversal_r5': ('r5', -1)}
HGB = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=500,
           l2_regularization=1.0, early_stopping=False, random_state=7)


def load():
    p = pd.read_parquet(PANEL)
    p = p[(p.close >= 5) & p.log_dollar_vol.notna()].copy()
    ys = [c for c in p.columns if c.startswith('y_')]
    feats = [c for c in p.columns if c not in NON_FEATURES and not c.startswith('y_')]
    stock_feats = [c for c in feats if c not in MARKET_COLS]
    g = p.groupby('date')
    X = pd.DataFrame(index=p.index)
    for c in stock_feats:                                  # per-day rank, centred
        X[c] = g[c].rank(pct=True) - 0.5
    for c in MARKET_COLS:
        X[c] = p[c]
    X['sector_code'] = p.sector.astype('category').cat.codes
    X['dna_code'] = p.dna.astype('category').cat.codes
    # benchmark signals (NOT model inputs): Contrarian ranks the 3-day return, which the panel lacks
    p = p.sort_values(['symbol', 'date'])
    p['r3_bench'] = p.groupby('symbol').close.transform(lambda c: c / c.shift(3) - 1)
    p = p.loc[X.index]
    B = pd.DataFrame(index=p.index)
    B['r3'] = p.groupby('date').r3_bench.rank(pct=True) - 0.5
    B['on_cons30'] = X['on_cons30']; B['r5'] = X['r5']
    for y in ys:                                           # market-neutral targets
        p[y + '_xs'] = p[y] - g[y].transform('mean')
        p[y + '_rk'] = g[y].rank(pct=True) - 0.5
    return p, X, stock_feats, B


def daily_ic(df, sig, tgt):
    x = df[['date', sig, tgt]].dropna()
    x = x[x.groupby('date')[sig].transform('size') >= 30]
    return x.groupby('date').apply(lambda d: d[sig].rank().corr(d[tgt].rank())).dropna()


def ic_stats(ic, k=1):
    """Mean IC, t on NON-OVERLAPPING days (every k-th date for a k-day target), half-year means."""
    sub = ic.iloc[::k]
    t = sub.mean() / (sub.std() / np.sqrt(len(sub))) if len(sub) > 3 else np.nan
    halves = ic.groupby(lambda d: f"{d.year}{'H1' if d.month <= 6 else 'H2'}").mean()
    return ic.mean(), t, halves


def portfolio(df, sig, y, cost, n_top=(3, 5, 10)):
    """Decile spread (top-bottom, market-neutral) and long-only top-N excess vs universe, % per trade."""
    x = df[['date', sig, y + '_xs']].dropna()
    x = x[x.groupby('date')[sig].transform('size') >= 30]
    x['q'] = (x.groupby('date')[sig].rank(pct=True) * 10).clip(upper=9.999).astype(int)
    sp = x.groupby(['date', 'q'])[y + '_xs'].mean().unstack()
    out = {'decile_spread_bp': (sp[9] - sp[0]).mean() * 1e4}
    for n in n_top:
        top = x.sort_values(sig, ascending=False).groupby('date').head(n)
        exc = top.groupby('date')[y + '_xs'].mean() * 100
        out[f'top{n}_excess_pct'] = exc.mean()
        out[f'top{n}_net_pct'] = exc.mean() - cost
        out[f'top{n}_pos_months'] = (exc.groupby(exc.index.to_period('M')).mean() > cost).mean()
    return out


def walk_forward(p, X, y, k):
    dates = np.sort(p.date.unique())
    months = pd.date_range(TEST_START, p.date.max() + pd.offsets.MonthBegin(1), freq='MS')
    preds = pd.Series(np.nan, index=p.index); preds_lin = pd.Series(np.nan, index=p.index)
    feats = list(X.columns)
    for m0, m1 in zip(months[:-1], months[1:]):
        test = (p.date >= m0) & (p.date < m1)
        if not test.any():
            continue
        prior = dates[dates < np.datetime64(m0)]
        cut = prior[-(k + 1)] if len(prior) > k + 1 else prior[0]       # purge overlap + 1-day embargo
        train = (p.date < cut) & p[y + '_rk'].notna()
        Xt, yt = X.loc[train, feats], p.loc[train, y + '_rk']
        m = HistGradientBoostingRegressor(categorical_features=[feats.index('sector_code'), feats.index('dna_code')], **HGB)
        m.fit(Xt, yt)
        preds[test] = m.predict(X.loc[test, feats])
        lin_feats = [f for f in feats if f not in ('sector_code', 'dna_code')]
        r = Ridge(alpha=10.0).fit(Xt[lin_feats].fillna(0), yt)
        preds_lin[test] = r.predict(X.loc[test, lin_feats].fillna(0))
    return preds, preds_lin


def main():
    horizons = sys.argv[1:] or ['y_on', 'y_id', 'y_r1', 'y_r3', 'y_r5']
    t0 = time.time()
    p, X, stock_feats, B = load()
    lines = [f"panel {len(p):,} rows, {p.symbol.nunique()} symbols, {p.date.min().date()} → {p.date.max().date()}; "
             f"{X.shape[1]} model inputs; test (walk-forward) from {TEST_START.date()}"]
    # ── 1. single-feature scan on the TEST window (same window the model is judged on) ──
    test = p.date >= TEST_START
    rows = []
    for y in horizons:
        k = HORIZON_DAYS[y]
        for f in stock_feats:
            ic = daily_ic(p[test].assign(**{f: X.loc[test, f]}), f, y + '_xs')
            if len(ic) < 50:
                continue
            m, t, h = ic_stats(ic, k)
            rows.append(dict(horizon=y, feature=f, ic=m, t=t, halves_same_sign=int((np.sign(h) == np.sign(m)).sum()),
                             n_halves=len(h), **{f'ic_{i}': v for i, v in h.items()}))
    fic = pd.DataFrame(rows)
    fic.to_csv(os.path.join(OUT, 'ml_feature_ic.csv'), index=False)
    lines.append(f"\n== single-feature scan: {len(fic)} feature×horizon tests (multiple-testing bar |t| > 3.5) ==")
    for y in horizons:
        f = fic[fic.horizon == y].assign(abs_t=lambda d: d.t.abs()).sort_values('abs_t', ascending=False)
        lines.append(f"  {y}: {int((f.abs_t > 3.5).sum())} pass |t|>3.5 — top 8: " +
                     ', '.join(f"{r.feature} {r.ic:+.3f}(t={r.t:+.1f},{r.halves_same_sign}/{r.n_halves})" for r in f.head(8).itertuples()))
    # ── 2+3. walk-forward model vs benchmarks ──
    for y in horizons:
        k = HORIZON_DAYS[y]
        preds, preds_lin = walk_forward(p, X, y, k)
        q = p[['date', 'symbol', y, y + '_xs']].copy()
        q['gbm'] = preds; q['ridge'] = preds_lin
        for name, (col, sign) in BENCH.items():
            q[name] = sign * B[col]
        q = q[q.date >= TEST_START]
        q.to_parquet(os.path.join(OUT, f'ml_preds_{y}.parquet'), index=False)
        lines.append(f"\n== {y} (hold {k}d, cost {COST[y]}% round trip) — out-of-sample {q.date.min().date()} → {q.date.max().date()} ==")
        for sig in ('gbm', 'ridge', *BENCH):
            ic = daily_ic(q, sig, y + '_xs')
            m, t, h = ic_stats(ic, k)
            pf = portfolio(q, sig, y, COST[y])
            lines.append(f"  {sig:22} IC {m:+.4f} t={t:+.2f} halves[{' '.join(f'{v:+.3f}' for v in h.values)}] | "
                         f"decile {pf['decile_spread_bp']:+6.1f}bp | top3 {pf['top3_excess_pct']:+.3f}% net {pf['top3_net_pct']:+.3f}% "
                         f"| top5 net {pf['top5_net_pct']:+.3f}% ({pf['top5_pos_months']:.0%} months>cost) | top10 net {pf['top10_net_pct']:+.3f}%")
        print('\n'.join(lines[-7:]), flush=True)
    lines.append(f"\nelapsed {time.time() - t0:.0f}s")
    open(os.path.join(OUT, 'ml_summary.txt'), 'w').write('\n'.join(lines))
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
