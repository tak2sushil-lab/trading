"""Long-history robustness of the overnight model — DAILY features only (Oct 4 2026, RESEARCH_REGISTRY §F).

Night Owl's 15:40 model draws ~57% of its ranking power from intraday shape (today's to 15:40, yesterday's),
which our 5-min bars only have from 2024. This tests the DAILY part across many more years and three bear
markets, at no data cost:
  * yfinance daily OHLCV (auto-adjusted for splits/dividends), Jan 2015 → today, for our universe;
  * the same feature code as research_ml_panel.features() (intraday-only columns dropped; VWAP approximated
    by the typical price), every feature LAGGED one session + today's opening gap → decision-time honest;
  * walk-forward monthly re-fit, expanding window, purged; out of sample Jan 2018 → today;
  * "WILD" = top third of trailing 60-day realised volatility each day (causal — fixes the factory's
    full-history personality look-ahead);
  * Clockwork's own signal (30-night up-gap consistency) scored on the same years for comparison.
Reports IC and top-3/top-5 WILD excess per year and in the bear windows (2018-Q4, Feb-Apr 2020, 2022).

⚠️ Survivorship gets worse going back: the universe is TODAY's 241 names (chosen partly for having
moved); names that IPO'd later are simply absent early on. Cross-sectional (market-neutral) scoring limits
but does not remove this. Treat early-year levels as upper bounds; the year-by-year SIGN is the test.

Usage: venv/bin/python research_ml_longrun.py
"""
import os, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
import research_ml_panel as P
import research_ml_model as M

OUT = os.path.join(ROOT, 'research_out')
YF = os.path.join(OUT, 'yf_daily_2015.parquet')
LONG = os.path.join(OUT, 'ml_longrun_panel.parquet')
TEST_START = pd.Timestamp('2018-01-01')
INTRADAY_ONLY = ['ret_first_hour', 'ret_last_hour', 'ret_last_30m', 'ivol_intraday', 'upvol_share']


def download(universe):
    if os.path.exists(YF):
        return pd.read_parquet(YF)
    import yf_cache_fix  # noqa: F401
    import yfinance as yf
    frames = []
    for i in range(0, len(universe), 40):
        chunk = universe[i:i + 40]
        df = yf.download(chunk, start='2015-01-01', auto_adjust=True, progress=False, group_by='ticker', threads=True)
        for s in chunk:
            try:
                x = df[s].dropna(how='all')
            except KeyError:
                continue
            if len(x) < 300:
                continue
            x = x.rename(columns=str.lower)[['open', 'high', 'low', 'close', 'volume']]
            x['symbol'] = s; x.index.name = 'date'
            frames.append(x.reset_index())
        print(f'  downloaded {min(i + 40, len(universe))}/{len(universe)}', flush=True)
    d = pd.concat(frames, ignore_index=True)
    d['date'] = pd.to_datetime(d.date).dt.tz_localize(None)
    d.to_parquet(YF, index=False)
    return d


def build_panel():
    if os.path.exists(LONG):
        return pd.read_parquet(LONG)
    k = P._at_constants()
    uni = [s for s in k['FULL_UNIVERSE'] if s not in set(k.get('ETF_SYMBOLS', []))]
    raw = download(uni)
    parts = []
    for s, d in raw.groupby('symbol'):
        d = d.set_index('date').sort_index()
        d = d[(d.open > 0) & (d.close > 0)].copy()
        d['vwap'] = (d.high + d.low + d.close) / 3
        for c in INTRADAY_ONLY:
            d[c] = np.nan
        d['runup_open'] = d.high / d.open - 1
        d['drawdown_open'] = d.low / d.open - 1
        f = P.features(d).drop(columns=INTRADAY_ONLY)
        f['symbol'] = s; f['sector'] = k['SECTOR_MAP'].get(s, 'OTHER')
        f['dna'] = 'HIGH_VOL' if s in set(k['HIGH_VOL_SYMBOLS']) else 'INSTITUTIONAL' if s in set(k['INSTITUTIONAL_SYMBOLS']) else 'MOMENTUM'
        parts.append(f.reset_index())
    p = pd.concat(parts, ignore_index=True)
    g = p.groupby('date')
    p['mkt_r1'] = g.ret_1d_for_beta.transform('mean'); p['mkt_r5'] = g.r5.transform('mean')
    p['mkt_r20'] = g.r20.transform('mean'); p['xs_dispersion'] = g.ret_1d_for_beta.transform('std')
    p['breadth'] = g.ret_1d_for_beta.transform(lambda x: (x > 0).mean())
    p['sector_rel_r5'] = p.r5 - p.groupby(['date', 'sector']).r5.transform('median')
    p = p.sort_values(['symbol', 'date'])
    gs = p.groupby('symbol')
    cov = (p.ret_1d_for_beta * p.mkt_r1).groupby(p.symbol).transform(lambda x: x.rolling(60).mean()) - \
        gs.ret_1d_for_beta.transform(lambda x: x.rolling(60).mean()) * gs.mkt_r1.transform(lambda x: x.rolling(60).mean())
    p['beta60'] = cov / gs.mkt_r1.transform(lambda x: x.rolling(60).var(ddof=0)).replace(0, np.nan)
    resid = p.ret_1d_for_beta - p.beta60 * p.mkt_r1
    p['idio_vol60'] = resid.groupby(p.symbol).transform(lambda x: x.rolling(60).std())
    p['vix'] = np.nan
    p = p.drop(columns=['ret_1d_for_beta'])
    p.to_parquet(LONG, index=False)
    return p


def main():
    p = build_panel()
    p = p[(p.close >= 5) & p.log_dollar_vol.notna()].copy()
    feats = [c for c in p.columns if c not in M.NON_FEATURES and not c.startswith('y_')]
    stock = [c for c in feats if c not in M.MARKET_COLS]
    p = p.sort_values(['symbol', 'date']).reset_index(drop=True)
    g = p.groupby('date')
    X = pd.DataFrame(index=p.index)
    for c in stock:
        X[c] = g[c].rank(pct=True) - 0.5
    for c in M.MARKET_COLS:
        X[c] = p[c]
    X['sector_code'] = p.sector.astype('category').cat.codes; X['dna_code'] = p.dna.astype('category').cat.codes
    gap_today = X['on'].copy()
    lag_cols = stock + [c for c in M.MARKET_COLS if c not in ('dow', 'month_end')]
    X[lag_cols] = X.groupby(p.symbol)[lag_cols].shift(1)              # decision-time honest
    X['gap_today'] = gap_today
    p['y_on_xs'] = p.y_on - g.y_on.transform('mean'); p['y_on_rk'] = g.y_on.rank(pct=True) - 0.5
    p['wild'] = (p.groupby('symbol').vol20.shift(1)).groupby(p.date).rank(pct=True) >= 2 / 3   # causal WILD
    p['cw'] = p.groupby('symbol').on_cons30.shift(0)                    # through today's gap (known at open)
    old_start = M.TEST_START
    M.TEST_START = TEST_START
    preds, _ = M.walk_forward(p, X, 'y_on', 1)
    M.TEST_START = old_start
    q = p[['date', 'symbol', 'y_on', 'y_on_xs', 'wild', 'cw']].copy(); q['model'] = preds
    q = q[q.date >= TEST_START].dropna(subset=['model', 'y_on'])
    q.to_parquet(os.path.join(OUT, 'ml_longrun_preds.parquet'), index=False)
    print(f"daily-only overnight model, out of sample {q.date.min().date()} → {q.date.max().date()}, "
          f"{q.symbol.nunique()} names (names per night: median {q.groupby('date').size().median():.0f})\n")
    w = q[q.wild]
    def yearly(sub, sig):
        ic = M.daily_ic(sub, sig, 'y_on_xs')
        top = sub.dropna(subset=[sig]).sort_values(sig, ascending=False).groupby('date').head(3)
        exc = (top.groupby('date').y_on.mean() - sub.groupby('date').y_on.mean()).dropna() * 1e4
        return ic, exc
    for lab, sig in (('model (daily-only)', 'model'), ("Clockwork's signal", 'cw')):
        ic, exc = yearly(w, sig)
        yr = pd.DataFrame({'IC': ic.groupby(ic.index.year).mean(), 'top3_excess_bp': exc.groupby(exc.index.year).mean()})
        print(f"  {lab} — WILD names: IC {ic.mean():+.4f} (t {ic.mean() / (ic.std() / np.sqrt(len(ic))):+.2f}), "
              f"top-3 excess {exc.mean():+.1f}bp/night (t {exc.mean() / (exc.std() / np.sqrt(len(exc))):+.2f})")
        print('    by year:  ' + '  '.join(f"{y}: IC {r.IC:+.3f} top3 {r.top3_excess_bp:+.0f}bp" for y, r in yr.iterrows()))
        for bl, a, b in (('2018-Q4 bear', '2018-10-01', '2018-12-31'), ('COVID crash', '2020-02-15', '2020-04-30'),
                         ('2022 bear', '2022-01-01', '2022-12-31')):
            m_ = (ic.index >= a) & (ic.index <= b); e_ = (exc.index >= a) & (exc.index <= b)
            print(f"    {bl:13} IC {ic[m_].mean():+.3f}  top3 excess {exc[e_].mean():+.0f}bp/night  ({m_.sum()} nights)")
    both = pd.DataFrame({'m': yearly(w, 'model')[1], 'c': yearly(w, 'cw')[1]}).dropna()
    print(f"\n  correlation of the two books' nightly excess: {both.m.corr(both.c):+.2f}")


if __name__ == '__main__':
    main()
