"""Night Owl retrained on a BROAD universe vs Clockwork's one-line rule on the same stocks and nights (Oct 6 2026,
RESEARCH_REGISTRY §M10).

Live Night Owl learns from our 237 hand-picked names — the selection that made Contrarian look good (§M6). Here the
same feature code (factory/features_daily.features: ~85 daily inputs, every stock input LAGGED one session, plus today's
opening gap) is trained on every liquid currently-listed US stock with tickers A→OGG (yfinance daily, 2018 → today,
research_out/broad_daily_2018 + 2024), walk-forward with a yearly re-fit, and judged where the money is made: the top-3
WILD names held close → next open.

Universe each day: previous close ≥ $5 and previous 20-day median $vol ≥ $20M. WILD = top third by trailing 60-day vol.
No sector / DNA inputs (unknown for most of these names). Cost 2.1bp a night.
Usage: venv/bin/python research_nightowl_broad.py
"""
import os, sys, time, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
from factory.features_daily import features
import research_portfolio_lab as P
OUT = os.path.join(ROOT, 'research_out')
PANEL = os.path.join(OUT, 'nightowl_broad_panel.parquet')
INTRADAY_ONLY = ['ret_first_hour', 'ret_last_hour', 'ret_last_30m', 'ivol_intraday', 'upvol_share']
MKT = ['mkt_r1', 'mkt_r5', 'mkt_r20', 'xs_dispersion', 'breadth']
RAW_KEEP = ('dow', 'month_end')


def build():
    if os.path.exists(PANEL):
        return pd.read_parquet(PANEL)
    a = pd.read_parquet(os.path.join(OUT, 'broad_daily_2018.parquet')); b = pd.read_parquet(os.path.join(OUT, 'broad_daily_2024.parquet'))
    syms = set(a.symbol)
    d = pd.concat([a[a.date < b.date.min()], b[b.symbol.isin(syms)]], ignore_index=True)
    d = d[(d.open > 0) & (d.close > 0) & (d.high >= d.low)].sort_values(['symbol', 'date'])
    # market context from every liquid name (pass 1)
    c = d.pivot(index='date', columns='symbol', values='close'); v = d.pivot(index='date', columns='symbol', values='volume')
    r1 = c.pct_change().clip(-0.5, 0.5); dv = (c * v).rolling(20, min_periods=10).median()
    liq = (c.shift(1) >= 5) & (dv.shift(1) >= 20e6)
    m = pd.DataFrame({'mkt_r1': r1.where(liq).mean(axis=1), 'xs_dispersion': r1.where(liq).std(axis=1),
                      'breadth': (r1 > 0).where(liq).mean(axis=1)})
    m['mkt_r5'] = (1 + m.mkt_r1.fillna(0)).rolling(5).apply(np.prod, raw=True) - 1
    m['mkt_r20'] = (1 + m.mkt_r1.fillna(0)).rolling(20).apply(np.prod, raw=True) - 1
    del c, v, r1, dv
    parts, t0 = [], time.time()
    for i, (s, x) in enumerate(d.groupby('symbol', sort=False)):
        if len(x) < 260:
            continue
        x = x.set_index('date')[['open', 'high', 'low', 'close', 'volume']].astype(float)
        x['vwap'] = (x.high + x.low + x.close) / 3
        for col in INTRADAY_ONLY:
            x[col] = np.nan
        x['runup_open'] = x.high / x.open - 1; x['drawdown_open'] = x.low / x.open - 1
        f = features(x).drop(columns=INTRADAY_ONLY)
        f = f.join(m)
        mr = f['mkt_r1']; r = f.pop('ret_1d_for_beta')
        cov = (r * mr).rolling(60).mean() - r.rolling(60).mean() * mr.rolling(60).mean()
        f['beta60'] = cov / mr.rolling(60).var(ddof=0)
        f['idio_vol60'] = (r - f['beta60'] * mr).rolling(60).std()
        stock = [k for k in f.columns if not k.startswith('y_') and k not in ('close',) and k not in MKT and k not in RAW_KEEP]
        lagged = f[stock + MKT].shift(1)                         # every input known before today's open…
        lagged['gap_today'] = f['on']                            # …plus today's opening gap (known at 09:30)
        for k in RAW_KEEP:
            lagged[k] = f[k]
        lagged['prev_close'] = f['close'].shift(1)
        lagged['prev_dv20'] = (x.close * x.volume).rolling(20, min_periods=10).median().shift(1)
        lagged['y_on'] = f['y_on']
        lagged['symbol'] = s
        keep = (lagged.prev_close >= 5) & (lagged.prev_dv20 >= 20e6) & lagged.y_on.notna()
        lagged = lagged[keep]
        if len(lagged):
            num = lagged.select_dtypes('number').columns
            lagged[num] = lagged[num].astype('float32')
            parts.append(lagged.reset_index().rename(columns={'index': 'date'}))
        if i % 300 == 0:
            print(f'  {i} symbols {time.time() - t0:.0f}s', flush=True)
    p = pd.concat(parts, ignore_index=True)
    p.to_parquet(PANEL, index=False)
    print(f'panel {len(p):,} rows, {p.symbol.nunique():,} symbols, {time.time() - t0:.0f}s', flush=True)
    return p


def main():
    from sklearn.ensemble import HistGradientBoostingRegressor
    p = build()
    p['date'] = pd.to_datetime(p['date'])
    g = p.groupby('date')
    p['y_on_rk'] = g['y_on'].rank(pct=True) - 0.5
    p['vol_rank'] = g['vol60'].rank(pct=True)
    p['wild'] = p.vol_rank >= 2 / 3
    feats = [k for k in p.columns if k not in ('date', 'symbol', 'y_on', 'y_on_rk', 'prev_close', 'prev_dv20', 'wild', 'vol_rank')]
    X = pd.DataFrame(index=p.index)
    for k in feats:
        X[k] = p[k] if (k in MKT or k in RAW_KEEP) else g[k].rank(pct=True) - 0.5
    X = X.astype('float32')
    pred = pd.Series(np.nan, index=p.index)
    for Y in range(2020, 2027):
        te = p.date.dt.year == Y
        cut = pd.Timestamp(f'{Y}-01-01') - pd.Timedelta(days=4)
        tr = (p.date < cut)
        t0 = time.time()
        mdl = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=500,
                                            l2_regularization=1.0, random_state=7).fit(X[tr], p.loc[tr, 'y_on_rk'])
        pred[te] = mdl.predict(X[te])
        print(f'  fit for {Y}: {tr.sum():,} training rows, {time.time() - t0:.0f}s', flush=True)
    p['pred'] = pred
    R = p[p.pred.notna() & p.wild].copy()
    rng = np.random.default_rng(42)
    R['tie'] = rng.uniform(0, 1e-9, len(R))
    ic = R.groupby('date').apply(lambda d: d.pred.rank().corr(d.y_on.rank()) if len(d) >= 20 else np.nan).dropna()
    print(f'\nOOS {R.date.min().date()} → {R.date.max().date()}: {R.groupby("date").size().median():.0f} WILD names a night; '
          f'model IC {ic.mean():+.4f} (t {ic.mean() / (ic.std() / np.sqrt(len(ic))):+.1f}), by year ' +
          ' '.join(f'{k % 100:02d}:{v:+.3f}' for k, v in ic.groupby(ic.index.year).mean().items()))
    top = lambda col: R.assign(s=R[col] + R.tie).sort_values('s', ascending=False).groupby('date').head(3)
    no, cw = top('pred'), top('on_cons30')
    no_d = no.groupby('date').y_on.mean() - 2.1e-4
    cw_d = cw.groupby('date').y_on.mean() - 2.1e-4
    ew = R.groupby('date').y_on.mean()
    both = pd.concat([cw[['date', 'symbol', 'y_on']], no[['date', 'symbol', 'y_on']]]).groupby('date').y_on.mean() - 2.1e-4
    print(P.stats(no_d * 1e4, 1e4, 'Night Owl (broad-trained) top-3'))
    print(P.stats(cw_d * 1e4, 1e4, 'Clockwork rule top-3'))
    print(P.stats(both * 2e4, 2e4, 'both books, overlap allowed ($20k)'))
    print(P.stats(ew * 1e4, 1e4, 'every WILD name overnight'))
    print(f'  daily corr Night Owl vs Clockwork {no_d.corr(cw_d):+.2f}; nights NO beats CW {(no_d > cw_d).mean():.0%}; '
          f'NO − CW {((no_d - cw_d) * 1e4).mean():+.1f}bp/night (t {(no_d - cw_d).mean() / ((no_d - cw_d).std() / np.sqrt(len(no_d))):+.2f})')
    p[['date', 'symbol', 'pred', 'y_on', 'wild', 'on_cons30']].to_parquet(os.path.join(OUT, 'nightowl_broad_preds.parquet'), index=False)


if __name__ == '__main__':
    main()
