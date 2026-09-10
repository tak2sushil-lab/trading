"""
INDICATOR FEATURE LAB — does MACD or Bollinger Bands add anything, on MNQ?

Motivation: TS.pptx's TradingView-indicator slide lists MACD and "Bollinger Band + RSI" among
the tools the course uses. Neither exists anywhere in this codebase (grep-verified against
auto_trader.py, futures_trader.py, tc_trader.py) — RSI, ADX, VWAP, and Keltner already do, but
MACD and Bollinger are a genuine gap. This is the honest way to check whether that gap is worth
closing: not "does MACD look right on a chart," but does it survive the same two tests this
program holds every other candidate feature to —

  1. TRADE-LEVEL LEVER (mtf_lab.py's method): reconstruct MACD/Bollinger state at the entry
     of every trade the existing momentum book already took (cached _mom_*.csv, friction-
     adjusted). Does aligning with/against them separate winners from losers, per year?
  2. DAY-LEVEL FORECAST (conditions.py's method): does MACD/Bollinger state at the prior
     close predict tomorrow's day character (room, trend-quality, clean-run)? Walk-forward,
     train 2021-23 / test 2024-26, same noise floor discipline as conditions.py's mode_ic.

Definitions (standard, not tuned to this data):
  MACD(12,26,9)   — EMA12 - EMA26 on close; signal = EMA9 of that; histogram = MACD - signal.
  Bollinger(20,2) — SMA20 +/- 2*std(20) on close; %B = (price-lower)/(upper-lower);
                    bandwidth = (upper-lower)/middle (a squeeze/expansion measure).
  Trade-level: computed on a CONTINUOUS cross-session 5-min series (not reset at 9:30, unlike
    this program's VWAP/EMA9 conventions) — this is how MACD/Bollinger are actually read on a
    live chart, and the first version of this lab reset them daily, which meant MACD(26) had
    no valid value until bar 26 of each session (~11:40am ET). Since this book enters almost
    entirely in the 10:00-11:59 and 13:00-13:59 hours (grep-verified: 417/263/269 of 949 trades,
    zero in between), a daily reset silently dropped 70% of the trade population before any
    lever could be measured — fixed before trusting the result below.
  Day-level: computed on the DAILY close series (same daily frame construction as
    conditions.py's build_day_table), reused via the cached _day_table.csv's own date range —
    that file is current through 2026-08-14 (~3 weeks stale vs today); across a 1,419-day
    walk-forward split that gap does not change a train/test verdict, so it is reused as-is
    rather than paying for a full daily-frame rebuild.

Run: venv/bin/python -m futures.factory.indicator_feature_lab
"""
from __future__ import annotations

import datetime as _dt
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from futures.collect_bars import load_bars                       # noqa: E402
from futures.factory.conditions import (                          # noqa: E402
    _sess, _ic, load_day_table, RTH_OPEN, RTH_END, TRAIN_END,
)

TRADES = os.path.join(ROOT, 'futures', 'factory', '_mom_2021-06-01_2026-08-14.csv')
BIG_LOSS = -400.0


# ── indicator primitives ────────────────────────────────────────────────────
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


# ── (1) trade-level lever test ──────────────────────────────────────────────
def build_trade_features() -> pd.DataFrame:
    d = pd.read_csv(TRADES)
    d['dts'] = d['date'].astype(str)
    bars = load_bars('MNQ', start='2021-01-01', end='2026-08-15')
    rth = _sess(bars, RTH_OPEN, RTH_END).sort_index()

    # continuous, cross-session indicator series — matches how these are actually read on a
    # live chart (a trader's MACD doesn't reset to blank every morning at 9:30)
    ind = pd.concat([macd_cols(rth['close']), boll_cols(rth['close'])], axis=1)
    ind_ts = ind.index

    rows = []
    for _, t in d.iterrows():
        try:
            hh, mm = map(int, str(t['entry_time'])[:5].split(':'))
        except Exception:
            continue
        try:
            entry_ts = pd.Timestamp(f"{t['dts']} {hh:02d}:{mm:02d}:00").tz_localize(ind_ts.tz)
        except Exception:
            continue
        pos = ind_ts.searchsorted(entry_ts, side='right') - 1
        if pos < 0:
            continue
        row = ind.iloc[pos]
        if abs((ind_ts[pos] - entry_ts).total_seconds()) > 3600:
            continue    # stale match (missing data around this timestamp) — skip, don't guess
        if not np.isfinite(row['macd_hist']) or not np.isfinite(row['boll_pctb']):
            continue
        sgn = 1 if t['side'] == 'LONG' else -1
        rows.append(dict(
            date=t['dts'], y=pd.Timestamp(t['dts']).year, side=t['side'], pnl=float(t['pnl']),
            macd_hist_aligned=sgn * row['macd_hist'],     # >0 = histogram agrees with our side
            macd_above_sig=sgn * (row['macd'] - row['macd_sig']),
            macd_above_zero=sgn * row['macd'],
            boll_pctb=sgn * (row['boll_pctb'] - 0.5),      # >0 = upper-half agrees with our side
            boll_extreme=1 if (row['boll_pctb'] > 1.0 or row['boll_pctb'] < 0.0) else 0,
            boll_bw=row['boll_bw'],
        ))
    return pd.DataFrame(rows)


def _yr_line(g: pd.DataFrame, years) -> str:
    return ' '.join(f'{y}:{g.loc[g.y == y, "pnl"].sum():+,.0f}' for y in years)


def report_trade_level(m: pd.DataFrame):
    years = sorted(m.y.unique())
    print(f'\n{"="*100}')
    print(f'  (1) TRADE-LEVEL LEVER — MACD/Bollinger state at entry, n={len(m)} trades')
    print(f'      (reconstructed for every trade the existing momentum book already took)')
    print(f'{"="*100}')
    print('  {:<34}{:>6}{:>7}{:>10}{:>8}   {}'.format(
        'lever (keep only trades where…)', 'n', 'WR', 'total', 'big-L', 'per year'))
    print('  ' + '-' * 118)
    print('  {:<34}{:>6}{:>6.0f}%{:>+10,.0f}{:>8}   {}'.format(
        'BASELINE (no filter)', len(m), 100 * (m.pnl > 0).mean(), m.pnl.sum(),
        int((m.pnl <= BIG_LOSS).sum()), _yr_line(m, years)))

    levers = {
        'MACD histogram agrees w/ side':   m.macd_hist_aligned > 0,
        'MACD line above signal (our way)': m.macd_above_sig > 0,
        'MACD above zero (our way)':        m.macd_above_zero > 0,
        'Bollinger upper-half (our way)':   m.boll_pctb > 0,
        'Bollinger extreme (>1 or <0)':     m.boll_extreme == 1,
        'NOT Bollinger extreme':            m.boll_extreme == 0,
        'MACD+Bollinger both agree':        (m.macd_hist_aligned > 0) & (m.boll_pctb > 0),
    }
    for name, mask in levers.items():
        g = m[mask]
        if len(g) < 20:
            print(f'  {name:<34}   (too few trades: {len(g)})'); continue
        print('  {:<34}{:>6}{:>6.0f}%{:>+10,.0f}{:>8}   {}'.format(
            name, len(g), 100 * (g.pnl > 0).mean(), g.pnl.sum(),
            int((g.pnl <= BIG_LOSS).sum()), _yr_line(g, years)))
    print()


# ── (2) day-level forecast test ─────────────────────────────────────────────
def build_daily_closes(start='2021-01-04', end='2026-08-15') -> pd.Series:
    bars = load_bars('MNQ', start=start, end=end)
    rth = _sess(bars, RTH_OPEN, RTH_END)
    daily = rth.groupby(rth.index.date)['close'].last()
    daily.index = pd.to_datetime(list(daily.index))
    return daily.sort_index()


def report_day_level():
    df = load_day_table()
    closes = build_daily_closes()
    m = macd_cols(closes)
    b = boll_cols(closes)
    ind = pd.concat([m, b], axis=1)
    ind.index = ind.index.astype(str)
    df = df.copy()
    df['ts'] = pd.to_datetime(df['date']).astype(str)
    # prior-day indicator state only — never today's own close (that's lookahead)
    prev_ind = ind.shift(1)
    for c in ('macd_hist', 'macd', 'macd_sig', 'boll_pctb', 'boll_bw'):
        df[c] = df['ts'].map(prev_ind[c].to_dict())

    print(f'\n{"="*100}')
    print('  (2) DAY-LEVEL FORECAST — does prior-close MACD/Bollinger predict tomorrow\'s')
    print('      day character?  (Spearman IC, walk-forward, same method as conditions.py)')
    print(f'{"="*100}')
    tr, te = df[df.year <= TRAIN_END], df[df.year > TRAIN_END]
    floor_te = 1.96 / np.sqrt(max(len(te), 1))
    print(f'  train n={len(tr)}  test n={len(te)}   95% noise floor on test: |IC| < {floor_te:.3f}\n')
    feats = ['macd_hist', 'macd', 'boll_pctb', 'boll_bw']
    for lab in ('pm_er', 'room_atr', 'clean_run', 'abs_net_pm'):
        print(f'  ── label: {lab} ' + '─' * (78 - len(lab)))
        out = []
        for f in feats:
            ic_tr, _ = _ic(tr[f], tr[lab])
            ic_te, n_te = _ic(te[f], te[lab])
            if not (np.isfinite(ic_tr) and np.isfinite(ic_te)):
                continue
            same = np.sign(ic_tr) == np.sign(ic_te)
            real = same and abs(ic_te) > floor_te and abs(ic_tr) > 0.05
            out.append((abs(ic_te) if real else -1, f, ic_tr, ic_te, real, same))
        for _, f, a, b_, real, same in sorted(out, reverse=True):
            flag = '✅ SURVIVES' if real else ('~ sign ok' if same else '✗ flips')
            print(f'    {f:<14}{a:>+10.3f}{b_:>+10.3f}   {flag}')
        print()


def main():
    print('  building trade-level MACD/Bollinger reconstruction (one pass over 5.5yr of bars)…')
    m = build_trade_features()
    m.to_csv(os.path.join(ROOT, 'futures', 'factory', '_indicator_trades.csv'), index=False)
    print(f'  reconstructed {len(m)} trades')
    report_trade_level(m)
    report_day_level()


if __name__ == '__main__':
    main()
