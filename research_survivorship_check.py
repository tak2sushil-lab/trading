"""How much does survivorship flatter our books? (Oct 6 2026, RESEARCH_REGISTRY §M9)

Every US-listed instrument, live AND delisted, Jul 2024 → Oct 2026: DataBento EQUS.SUMMARY ohlcv-1d (official
consolidated open/close/volume, raw prices) — research_out/equs_daily_2024_raw.parquet ($10.54) + tickers over time from
the free symbology service (research_out/equs_symbology.parquet). Splits are detected as integer-ratio overnight jumps
and back-adjusted per instrument (factory.features_daily._split_adjust). Common stock only: ETFs (Nasdaq Trader
directory flag), and tickers with '.', '+', '-', '=', '^' or Nasdaq 5-letter W/U/R suffixes are excluded.

Universe ALL = every eligible instrument (previous close ≥ $5, 20-day median $vol ≥ $20M) on that day.
Universe SURVIVORS = the same, restricted to instruments still trading on the last day — what a "listed today" panel sees.
Books (as in research_portfolio_lab.py): Clockwork rule top-3, the WILD overnight basket, Contrarian 2 slots.
Usage: venv/bin/python research_survivorship_check.py
"""
import io, os, sys, warnings
import numpy as np, pandas as pd, requests
warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
from factory.features_daily import _split_adjust
import research_portfolio_lab as P
OUT = os.path.join(ROOT, 'research_out')


def etf_symbols():
    a = pd.read_csv(io.StringIO(requests.get('https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt', timeout=30).text), sep='|')
    b = pd.read_csv(io.StringIO(requests.get('https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt', timeout=30).text), sep='|')
    return set(a.loc[a.ETF == 'Y', 'Symbol']) | set(b.loc[b.ETF == 'Y', 'ACT Symbol'])


def load():
    d = pd.read_parquet(os.path.join(OUT, 'equs_daily_2024_raw.parquet'))
    d['date'] = pd.to_datetime(d.ts_event).dt.tz_localize(None).dt.normalize()
    M = pd.read_parquet(os.path.join(OUT, 'equs_symbology.parquet'))
    M['d0'], M['d1'] = pd.to_datetime(M.d0), pd.to_datetime(M.d1)
    d = d.merge(M, on='instrument_id', how='left')
    d = d[(d.date >= d.d0) & (d.date < d.d1)]
    etf = etf_symbols()
    s = d.symbol.astype(str)
    bad = s.str.contains(r'[\.\+\-=\^/ ]', regex=True) | s.isin(etf) | ((s.str.len() == 5) & s.str[-1].isin(list('WUR')))
    d = d[~bad & (d.open > 0) & (d.close > 0)]
    last_any = d.date.max()
    alive = set(d.loc[d.date >= last_any - pd.Timedelta(days=4), 'instrument_id'])
    parts = []
    for iid, x in d.groupby('instrument_id'):
        x = x.sort_values('date').drop_duplicates('date', keep='last').set_index('date')[['open', 'high', 'low', 'close', 'volume', 'symbol']]
        if len(x) < 70:
            continue
        x = x.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': float})
        x['vwap'] = (x.high + x.low + x.close) / 3
        x = _split_adjust(x)
        x['iid'] = iid
        parts.append(x.reset_index())
    p = pd.concat(parts, ignore_index=True)
    p['alive'] = p.iid.isin(alive)
    return p


def books(p, label):
    o = p.pivot(index='date', columns='iid', values='open'); c = p.pivot(index='date', columns='iid', values='close')
    v = p.pivot(index='date', columns='iid', values='volume')
    pc = c.shift(1); on = (o.shift(-1) / c - 1).clip(-0.9, 3); cc = (c / pc - 1).clip(-0.9, 3)
    gap = o / pc - 1
    cons30 = (gap > 0).astype(float).where(gap.notna()).rolling(30, min_periods=25).mean().shift(1)
    vol60 = cc.rolling(60, min_periods=40).std().shift(1); dv20 = (c * v).rolling(20, min_periods=10).median().shift(1)
    elig = (pc >= 5) & (dv20 >= 20e6) & on.notna()
    wild = elig & (vol60.where(elig).rank(axis=1, pct=True) >= 2 / 3)
    rng = np.random.default_rng(42)
    days = [x for x in c.index if x >= pd.Timestamp('2024-10-15') and x < c.index[-1]]
    cw, ew = {}, {}
    for x in days:
        s = cons30.loc[x].where(wild.loc[x]).dropna(); s = s + rng.uniform(0, 1e-9, len(s))
        cw[x] = on.loc[x, s.nlargest(3).index].mean() - 2.1e-4
        ew[x] = on.loc[x][wild.loc[x]].mean()
    r3 = c / c.shift(3) - 1; eligct = (pc >= 5) & ((c * v).rolling(20, min_periods=10).median() >= 5e6)
    di = {x: i for i, x in enumerate(c.index)}; held, pnl, trades = [], {}, []
    for x in days:
        i = di[x]; pp = 0.0; still = []
        for sym, ei, px0, sh in held:
            px_prev, px = c.iat[i - 1, c.columns.get_loc(sym)], c.at[x, sym]
            if np.isnan(px):                                  # stopped trading (delisted / halted): exit at last price
                trades.append(c[sym].iloc[:i].dropna().iloc[-1] / px0 - 1); continue
            pp += sh * (px - px_prev)
            if i - ei >= 5 or px <= px0 * 0.85:
                pp -= 5; trades.append(px / px0 - 1)
            else:
                still.append((sym, ei, px0, sh))
        held = still
        if len(held) < 2:
            cand = r3.loc[x].where(eligct.loc[x]).dropna(); cand = cand[(cand <= -0.08) & (cand > -0.35)].sort_values()
            for sym in cand.index:
                if len(held) >= 2: break
                if sym in {h[0] for h in held}: continue
                held.append((sym, i, c.at[x, sym], 5000 / c.at[x, sym]))
        pnl[x] = pp
    print(f'\n{label}: {elig.sum(axis=1).median():.0f} eligible / {wild.sum(axis=1).median():.0f} WILD names a day (median), '
          f'{len(days)} sessions')
    print(P.stats(pd.Series(cw) * 10_000, 10_000, 'Clockwork rule top-3'))
    print(P.stats(pd.Series(ew) * 10_000, 10_000, 'every WILD name overnight'))
    print(P.stats(pd.Series(pnl), 10_000, 'Contrarian 2 slots'))
    t = pd.Series(trades)
    print(f'  Contrarian trades {len(t)}, mean {t.mean() * 100:+.2f}%, worse than −30%: {(t < -0.3).mean():.1%}')
    return pd.Series(cw), pd.Series(ew), pd.Series(pnl)


def main():
    p = load()
    n_all, n_alive = p.iid.nunique(), p[p.alive].iid.nunique()
    print(f'{n_all:,} common-stock instruments Jul 2024 → Oct 2026; {n_all - n_alive:,} ({1 - n_alive / n_all:.0%}) no longer trade')
    a = books(p, 'ALL (survivorship-free)')
    s = books(p[p.alive], 'SURVIVORS only (what a "listed today" panel sees)')
    for k, (x, y) in zip(['Clockwork', 'WILD basket', 'Contrarian'], zip(a, s)):
        print(f'  survivorship gap — {k}: ${y.sum() - x.sum():+,.0f} on $10k over the window ({(y.mean() - x.mean()) * (1e4 if k != "Contrarian" else 1):+.1f}'
              f'{"bp/night" if k != "Contrarian" else "$/day"})')


if __name__ == '__main__':
    main()
