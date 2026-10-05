"""What did the honest overnight model learn? (Oct 4 2026 — docs/RESEARCH_REGISTRY.md §F)

Train once on rows before 2025-07-01 (purged), test on 2025-07-01 → latest. Permutation importance by
SIGNAL FAMILY: shuffle a whole family's columns across stocks within each day (one permutation per day,
so the family's internal structure is kept) and measure the drop in daily rank IC. Also: how similar is
the model's ranking to Clockwork's single signal (mean within-day rank correlation)?

Usage: venv/bin/python research_ml_explain.py
"""
import re, warnings
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
warnings.filterwarnings('ignore')
import research_ml_model as M
from research_ml_overnight_1540 import build_honest

FAMILIES = [
    ('overnight persistence', r'^(on|on_avg\d+|on_cons30|gap_today)$'),
    ("today's shape at 15:40", r'^t_'),
    ('intraday shape (yesterday)', r'^(id|id_avg\d+|id_cons20|ret_first_hour|ret_last_hour|ret_last_30m|clv|close_vs_vwap|upvol_share|ivol_intraday|runup_open|drawdown_open)$'),
    ('returns / reversal', r'^(r\d+|r20_skip5|sector_rel_r5)$'),
    ('volatility / tails', r'^(vol20|vol60|vol_ratio|atr_pct|downvol20|skew20|max20|min20|beta60|idio_vol60)$'),
    ('trend / breakouts', r'^(dist_ma\d+|ma50_slope|hi52_prox|donch\d+_(pos|break))$'),
    ('oscillators', r'^(rsi14|rsi2|willr14|cci20|macd_hist|adx14|di_diff|boll_\w+|stoch14)$'),
    ('candles / patterns', r'^(nr7|nr4|inside_day|outside_day|gap|gap_filled|up_streak|down_streak|hammer|bull_engulf|doji)$'),
    ('Fibonacci (retracement + pivots)', r'^(fib_\w+|swing\d+_size_atr|piv_\w+)$'),
    ('volume / liquidity / price', r'^(rvol|obv_slope20|log_dollar_vol|log_price)$'),
    ('market context', r'^(mkt_\w+|xs_dispersion|breadth|vix|dow|month_end)$'),
    ('sector / DNA', r'^(sector_code|dna_code)$'),
]


def daily_ic(dates, score, y):
    df = pd.DataFrame({'d': dates.values, 's': score, 'y': y.values}).dropna()
    df = df[df.groupby('d').s.transform('size') >= 30]
    return df.groupby('d').apply(lambda g: g.s.rank().corr(g.y.rank())).mean()


def main():
    p, X, Xl, B = build_honest()
    y = 'y_on'
    cut = pd.Timestamp('2025-07-01')
    dates = np.sort(p.date.unique()); prior = dates[dates < np.datetime64(cut)]
    train = (p.date < prior[-2]) & p[y + '_rk'].notna()
    test = (p.date >= cut) & p[y + '_xs'].notna()
    feats = list(Xl.columns)
    m = HistGradientBoostingRegressor(categorical_features=[feats.index('sector_code'), feats.index('dna_code')], **M.HGB)
    m.fit(Xl.loc[train, feats], p.loc[train, y + '_rk'])
    Xt = Xl.loc[test, feats].copy(); dt = p.loc[test, 'date']; yt = p.loc[test, y + '_xs']
    base = daily_ic(dt, m.predict(Xt), yt)
    print(f"trained < {prior[-2]}, tested {dt.min().date()} → {dt.max().date()} ({dt.nunique()} nights): IC {base:+.4f}\n")
    rng = np.random.default_rng(5)
    rows = []
    covered = set()
    for name, pat in FAMILIES:
        cols = [c for c in feats if re.match(pat, c)]
        covered |= set(cols)
        drops = []
        for _ in range(3):
            Xp = Xt.copy()
            for d, idx in Xp.groupby(dt.values).groups.items():
                perm = rng.permutation(len(idx))
                Xp.loc[idx, cols] = Xp.loc[idx, cols].values[perm]
            drops.append(base - daily_ic(dt, m.predict(Xp), yt))
        rows.append((name, len(cols), np.mean(drops), np.std(drops)))
    missing = [c for c in feats if c not in covered]
    r = pd.DataFrame(rows, columns=['family', 'n_features', 'ic_drop', 'sd']).sort_values('ic_drop', ascending=False)
    r['share_of_ic'] = r.ic_drop / base
    print(r.round(4).to_string(index=False))
    if missing:
        print('\nunassigned features:', missing)
    s = pd.Series(m.predict(Xt), index=Xt.index)
    sim = pd.DataFrame({'d': dt, 'model': s, 'cw': B.loc[test, 'on_cons30']}).dropna()
    rc = sim.groupby('d').apply(lambda g: g.model.rank().corr(g.cw.rank())).mean()
    print(f"\nmean within-night rank correlation, model score vs Clockwork's on_cons30: {rc:+.2f}")


if __name__ == '__main__':
    main()
