"""
BOLLINGER-EXTREME, EQUITY SIDE — does the futures finding (futures/factory/indicator_feature_lab.py)
generalize to the equity A+ book, or is it a futures/MNQ-only artifact?

Context: on MNQ, "entering with price beyond the Bollinger(20,2) band in the trade's own
direction" cuts big losers 62->8 and is positive in 5 of 6 years, INCLUDING the two worst years
for the underlying book (2022, 2023 — where it goes from deeply negative to strongly positive).
The one open question the user raised: is this a real, portable momentum-confirmation signal,
or something specific to one instrument's mechanics? This is the direct test — same definition,
same lever, run against every real (non-RECONCILED) closed equity trade instead of MNQ trades.

Data: `trades` table in trades.db (WIN/LOSS, setup_type != RECONCILED) — 765 real trades,
Apr 15 - Sep 4 2026, 218 symbols. Bollinger/MACD reconstructed from each symbol's own 5-min
bars (market_data.db, via collect_bars.load_multi), continuous per symbol (not session-reset),
same convention as the fixed futures version.

Run: venv/bin/python research_bollinger_extreme_equity.py
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from collect_bars import load_multi

DB = 'trades.db'


def macd_cols(close: pd.Series) -> pd.DataFrame:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    line = ema12 - ema26
    signal = line.ewm(span=9, adjust=False).mean()
    return pd.DataFrame({'macd': line, 'macd_sig': signal, 'macd_hist': line - signal})


def boll_cols(close: pd.Series, n=20, k=2.0) -> pd.DataFrame:
    mid = close.rolling(n, min_periods=n).mean()
    sd = close.rolling(n, min_periods=n).std()
    upper, lower = mid + k * sd, mid - k * sd
    pctb = (close - lower) / (upper - lower)
    bw = (upper - lower) / mid
    return pd.DataFrame({'boll_pctb': pctb, 'boll_bw': bw})


def load_trades() -> pd.DataFrame:
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "select entry_date, entry_time, symbol, side, setup_type, entry_price, pnl "
        "from trades where status in ('WIN','LOSS') and setup_type != 'RECONCILED' "
        "order by entry_date", con)
    con.close()
    return df


def build() -> pd.DataFrame:
    trades = load_trades()
    symbols = sorted(trades['symbol'].unique())
    print(f'  loading 5-min bars for {len(symbols)} symbols…')
    bars = load_multi(symbols, start='2026-01-01', end='2026-09-05')

    rows = []
    for sym, g in trades.groupby('symbol'):
        df = bars.get(sym)
        if df is None or len(df) < 30:
            continue
        ind = pd.concat([macd_cols(df['close']), boll_cols(df['close'])], axis=1)
        ind_ts = ind.index
        for _, t in g.iterrows():
            try:
                et = str(t['entry_time'])[:5]
                hh, mm = map(int, et.split(':'))
                entry_ts = pd.Timestamp(f"{t['entry_date']} {hh:02d}:{mm:02d}:00")
                entry_ts = entry_ts.tz_localize(ind_ts.tz) if ind_ts.tz else entry_ts
            except Exception:
                continue
            pos = ind_ts.searchsorted(entry_ts, side='right') - 1
            if pos < 0:
                continue
            if abs((ind_ts[pos] - entry_ts).total_seconds()) > 3600:
                continue
            row = ind.iloc[pos]
            if not np.isfinite(row['macd_hist']) or not np.isfinite(row['boll_pctb']):
                continue
            sgn = 1 if str(t['side']).upper() == 'LONG' else -1
            rows.append(dict(
                date=t['entry_date'], month=str(t['entry_date'])[:7], symbol=sym,
                setup=t['setup_type'], side=t['side'], pnl=float(t['pnl']),
                macd_hist_aligned=sgn * row['macd_hist'],
                boll_pctb=sgn * (row['boll_pctb'] - 0.5),
                boll_extreme=1 if (row['boll_pctb'] > 1.0 or row['boll_pctb'] < 0.0) else 0,
            ))
    return pd.DataFrame(rows)


def report(m: pd.DataFrame):
    print(f'\n{"="*100}')
    print(f'  EQUITY — Bollinger-extreme lever, n={len(m)} reconstructed trades '
          f'(of 765 real closed trades)')
    print(f'{"="*100}')
    if len(m) == 0:
        print('  zero matched trades — bar coverage or timestamp join failed.')
        return

    months = sorted(m.month.unique())

    def _line(g):
        return ' '.join(f'{mo}:{g.loc[g.month==mo,"pnl"].sum():+,.0f}' for mo in months)

    print('  {:<32}{:>6}{:>7}{:>10}   {}'.format('lever', 'n', 'WR', 'total', 'per month'))
    print('  ' + '-' * 118)
    print('  {:<32}{:>6}{:>6.0f}%{:>+10,.0f}   {}'.format(
        'BASELINE (no filter)', len(m), 100*(m.pnl>0).mean(), m.pnl.sum(), _line(m)))
    levers = {
        'Bollinger extreme (our side)':  m.boll_extreme == 1,
        'NOT Bollinger extreme':         m.boll_extreme == 0,
        'MACD histogram agrees w/ side': m.macd_hist_aligned > 0,
        'Bollinger upper-half (our way)': m.boll_pctb > 0,
    }
    for name, mask in levers.items():
        g = m[mask]
        if len(g) < 15:
            print(f'  {name:<32}   (too few: {len(g)})'); continue
        print('  {:<32}{:>6}{:>6.0f}%{:>+10,.0f}   {}'.format(
            name, len(g), 100*(g.pnl>0).mean(), g.pnl.sum(), _line(g)))

    print(f'\n  Bollinger-extreme by setup_type:')
    ext = m[m.boll_extreme == 1]
    for s, g in ext.groupby('setup'):
        print(f'    {s:<28} n={len(g):<5} total=${g.pnl.sum():>+8,.0f}  WR={100*(g.pnl>0).mean():>4.0f}%')
    print(f'\n  (for comparison, ALL trades by setup_type:)')
    for s, g in m.groupby('setup'):
        print(f'    {s:<28} n={len(g):<5} total=${g.pnl.sum():>+8,.0f}  WR={100*(g.pnl>0).mean():>4.0f}%')
    print()


def main():
    m = build()
    m.to_csv('research_bollinger_extreme_equity_results.csv', index=False)
    report(m)


if __name__ == '__main__':
    main()
