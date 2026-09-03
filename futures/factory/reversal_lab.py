"""
REVERSAL LAB — "is there a 1-min technical that warns as the storm starts?"

User's question (Aug 18 2026), asked precisely: when the market reverses in real time, is
there an indicator on a 1-min / 30-sec frame that fires early enough to get the boat out —
or even to turn around and ride it the other way?

The question conflates two things that must be measured SEPARATELY, and that is the whole
reason every previous attempt here failed:

  DETECTION  — "price is reversing right now". Trivial on 1-min. VWAP loss, EMA cross,
               lower lows: all fire reliably. This is a rear-view mirror.
  WARNING    — "price is ABOUT to keep going against me, and enough of the move is still
               ahead to be worth acting on". This is the only thing worth money.

So this lab does NOT score indicators by P&L first. It scores them as CLASSIFIERS:

  precision   of the fires, how many are followed by real continued adverse movement?
  lead time   at the moment of the fire, how much adverse move is STILL AHEAD vs already
              behind? (the decisive number — a detector with 90% precision is worthless if
              it fires after 90% of the damage)
  cost        how often does it fire on a wobble that immediately recovers? Each of those
              is a winner amputated — the Jul-25 exit lab measured this as the thing that
              turned +$4,340 into −$264 while capture% ROSE 55→77.

Prior art in this codebase, all negative, all on coarser frames: four chop detectors tried
Jul 7 (VWAP-crossing count, ADX-at-entry, morning character, rolling regime-flip count) —
none worked. Jul 25 exit-lab: every faster-exit variant lost money. Aug 17: ML on 5-min
features, AUC train 0.875 → test 0.48. This lab drops to the 1-MINUTE frame the user asked
about, and adds the one input never tried: EQUITY-UNIVERSE BREADTH (293 symbols of 5-min
bars, 2024→now) — a market-wide tell that MNQ's own tape cannot contain.

Run: venv/bin/python -m futures.factory.reversal_lab --detect
     venv/bin/python -m futures.factory.reversal_lab --breadth
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from futures.collect_bars import load_bars  # noqa: E402

RTH_OPEN, RTH_END = _dt.time(9, 30), _dt.time(16, 0)
DOLLARS_PER_PT = 2.0


def _sess(df, t0=RTH_OPEN, t1=RTH_END):
    t = df.index.time
    return df[(t >= t0) & (t <= t1)]


def load_1m(start: str, end: str) -> pd.DataFrame:
    df = load_bars('MNQ', start=start, end=end, table='futures_bars_1m')
    return _sess(df).sort_index()


# ── indicator battery (all causal: computed from bars up to and including t) ───
def add_indicators(day: pd.DataFrame) -> pd.DataFrame:
    d = day.copy()
    tp = (d['high'] + d['low'] + d['close']) / 3.0
    d['vwap'] = (tp * d['volume']).cumsum() / d['volume'].cumsum().replace(0, np.nan)
    d['ema9'] = d['close'].ewm(span=9, adjust=False).mean()
    d['ema21'] = d['close'].ewm(span=21, adjust=False).mean()
    delta = d['close'].diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    d['rsi'] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    pc = d['close'].shift(1)
    tr = pd.concat([d['high'] - d['low'], (d['high'] - pc).abs(),
                    (d['low'] - pc).abs()], axis=1).max(axis=1)
    d['atr'] = tr.rolling(14, min_periods=14).mean()
    d['vol_z'] = ((d['volume'] - d['volume'].rolling(30, min_periods=10).mean())
                  / d['volume'].rolling(30, min_periods=10).std())
    d['run_max'] = d['close'].cummax()
    d['giveback'] = d['run_max'] - d['close']
    return d


def triggers(d: pd.DataFrame) -> dict[str, pd.Series]:
    """Each returns a boolean series: 'a long position should be warned RIGHT NOW'."""
    c, v9, v21 = d['close'], d['ema9'], d['ema21']
    lower_low = (d['low'] < d['low'].shift(1)) & (d['low'].shift(1) < d['low'].shift(2))
    return {
        'VWAP lost':            (c < d['vwap']) & (c.shift(1) >= d['vwap'].shift(1)),
        'EMA9 cross down':      (c < v9) & (c.shift(1) >= v9.shift(1)),
        'EMA9<EMA21':           (v9 < v21) & (v9.shift(1) >= v21.shift(1)),
        '2 lower lows':         lower_low & ~lower_low.shift(1).fillna(False),
        'RSI<40':               (d['rsi'] < 40) & (d['rsi'].shift(1) >= 40),
        'vol spike down bar':   (d['vol_z'] > 2.0) & (c < d['open']),
        'giveback>0.5 ATR':     (d['giveback'] > 0.5 * d['atr']) &
                                (d['giveback'].shift(1) <= 0.5 * d['atr'].shift(1)),
        'VWAP + EMA9 both':     (c < d['vwap']) & (c < v9) &
                                ~((c.shift(1) < d['vwap'].shift(1)) & (c.shift(1) < v9.shift(1))),
    }


def detect_report(start: str, end: str, horizon: int = 60, adverse_pts: float = 60.0):
    """For a LONG held into each bar, score every trigger as an early-warning classifier."""
    bars = load_1m(start, end)
    rows = []
    for day, d0 in bars.groupby(bars.index.date):
        d = add_indicators(d0.sort_index())
        if len(d) < 120:
            continue
        c = d['close'].to_numpy()
        low = d['low'].to_numpy()
        n = len(c)
        # forward stats from each bar
        fwd_min = np.array([low[i + 1:i + 1 + horizon].min() if i + 1 < n else np.nan
                            for i in range(n)])
        fwd_end = np.array([c[min(i + horizon, n - 1)] for i in range(n)])
        peak_so_far = d['run_max'].to_numpy()
        for name, sig in triggers(d).items():
            idx = np.nonzero(sig.fillna(False).to_numpy())[0]
            for i in idx:
                if i + 20 >= n or not np.isfinite(fwd_min[i]):
                    continue
                rows.append(dict(
                    date=str(day), trig=name, i=i,
                    ahead=c[i] - fwd_min[i],          # adverse move STILL to come
                    behind=peak_so_far[i] - c[i],     # adverse move already taken
                    net=fwd_end[i] - c[i],            # where price is one horizon later
                ))
    r = pd.DataFrame(rows)
    if r.empty:
        print('  no trigger events'); return

    print(f'\n{"="*104}')
    print(f'  1-MINUTE REVERSAL DETECTORS AS EARLY-WARNING CLASSIFIERS   {start} → {end}')
    print(f'  horizon {horizon} min · "real reversal" = ≥{adverse_pts:.0f}pts more adverse after the fire')
    print(f'{"="*104}')
    print('  {:<22}{:>8}{:>9}{:>10}{:>10}{:>11}{:>12}'.format(
        'trigger', 'fires/day', 'precision', 'ahead', 'behind', '% early', 'net after'))
    print('  ' + '-' * 92)
    ndays = r.date.nunique()
    for name, g in r.groupby('trig'):
        real = g.ahead >= adverse_pts
        early = g.ahead > g.behind          # more damage ahead than behind = genuine warning
        print('  {:<22}{:>8.1f}{:>8.0f}%{:>10.0f}{:>10.0f}{:>10.0f}%{:>+12.0f}'.format(
            name, len(g) / ndays, 100 * real.mean(), g.ahead.mean(), g.behind.mean(),
            100 * early.mean(), g.net.mean()))
    print('\n  precision = share of fires followed by a real further adverse move')
    print('  ahead/behind = points of adverse move still to come vs already taken at the fire')
    print('  % early = fires where MORE damage is ahead than behind (a true warning, not a mirror)')
    print('  net after = average MNQ points from the fire to +horizon (negative ⇒ shorting it pays)\n')

    print('  BASE RATE (the number that decides everything):')
    allbars = []
    for day, d0 in bars.groupby(bars.index.date):
        d = add_indicators(d0.sort_index())
        if len(d) < 120:
            continue
        c = d['close'].to_numpy(); low = d['low'].to_numpy(); n = len(c)
        for i in range(0, n - horizon):
            allbars.append(c[i] - low[i + 1:i + 1 + horizon].min())
    ab = pd.Series(allbars)
    print(f'    a RANDOM minute is followed by ≥{adverse_pts:.0f}pts adverse within {horizon}min '
          f'{100*(ab>=adverse_pts).mean():.0f}% of the time (n={len(ab):,})')
    print('    ⇒ any detector whose precision is not WELL above this is telling you nothing.\n')


# ── breadth: the input never tried ────────────────────────────────────────────
def build_breadth(start: str, end: str) -> pd.DataFrame:
    """Intraday breadth across the equity universe (293 symbols of 5-min bars).
    A market-wide tell that MNQ's own tape cannot contain."""
    con = sqlite3.connect(os.path.join(ROOT, 'market_data.db'))
    try:
        df = pd.read_sql_query(
            "SELECT symbol, ts_utc, open, close, volume FROM bars_5m "
            "WHERE ts_utc >= ? AND ts_utc <= ?", con, params=[start, end + 'T23:59'])
    finally:
        con.close()
    if df.empty:
        return pd.DataFrame()
    ts = pd.to_datetime(df['ts_utc'], format='mixed', utc=True)
    df['ts'] = ts.dt.tz_convert('America/New_York').dt.tz_localize(None)
    df = df.drop_duplicates(['symbol', 'ts'], keep='last')
    df['date'] = df['ts'].dt.date
    df = df.sort_values(['symbol', 'ts'])
    # session open per symbol-day, then intraday return of every bar
    first = df.groupby(['symbol', 'date'])['open'].transform('first')
    df['ret'] = df['close'] / first - 1.0
    df['cummax'] = df.groupby(['symbol', 'date'])['close'].cummax()
    df['at_high'] = (df['close'] >= df['cummax'] - 1e-9).astype(float)
    g = df.groupby('ts')
    b = pd.DataFrame({
        'pct_up': g['ret'].apply(lambda s: float((s > 0).mean())),
        'median_ret': g['ret'].median(),
        'pct_at_high': g['at_high'].mean(),
        'n': g['ret'].size(),
    })
    b = b[b['n'] >= 50]
    b['pct_up_d30'] = b['pct_up'].diff(6)         # 30-min change (6 × 5-min bars)
    b['pct_high_d30'] = b['pct_at_high'].diff(6)
    return b


def breadth_report(start: str, end: str, horizon: int = 60):
    print('  building equity-universe breadth panel (this is the never-tried input)…')
    b = build_breadth(start, end)
    if b.empty:
        print('  no breadth data'); return
    print(f'  breadth panel: {len(b):,} 5-min stamps, {b.index.min()} → {b.index.max()}')
    mnq = load_bars('MNQ', start=start, end=end, table='futures_bars_5m')
    mnq = _sess(mnq).sort_index()
    if getattr(mnq.index, 'tz', None) is not None:      # breadth panel is tz-naive NY
        mnq.index = mnq.index.tz_localize(None)
    m = pd.DataFrame({'close': mnq['close']})
    m['fwd'] = m.groupby(m.index.date)['close'].transform(
        lambda s: s.shift(-(horizon // 5)) - s)
    j = m.join(b, how='inner').dropna(subset=['fwd', 'pct_up', 'pct_up_d30'])
    j['y'] = j.index.year
    print(f'  joined MNQ ↔ breadth: {len(j):,} stamps\n')

    print(f'{"="*96}')
    print(f'  DOES EQUITY BREADTH LEAD MNQ?   forward {horizon}min MNQ move by breadth state')
    print(f'{"="*96}')
    for col, lbl in (('pct_up', 'share of universe green'),
                     ('pct_up_d30', '30-min CHANGE in that share'),
                     ('pct_at_high', 'share at session high'),
                     ('pct_high_d30', '30-min change in at-high')):
        q = pd.qcut(j[col], 5, labels=['Q1 worst', 'Q2', 'Q3', 'Q4', 'Q5 best'], duplicates='drop')
        print(f'\n  {lbl}:')
        for k, g in j.groupby(q, observed=True):
            per = '  '.join(f'{y}:{g.loc[g.y == y, "fwd"].mean():+.0f}'
                            for y in sorted(j.y.unique()) if (g.y == y).any())
            print(f'    {str(k):<10} n={len(g):6,}  fwd {g["fwd"].mean():+7.1f} pts   [{per}]')
        ic = j[col].rank().corr(j['fwd'].rank())
        tr, te = j[j.y <= 2024], j[j.y >= 2025]
        ic_tr = tr[col].rank().corr(tr['fwd'].rank())
        ic_te = te[col].rank().corr(te['fwd'].rank())
        floor = 1.96 / np.sqrt(len(te))
        ok = (np.sign(ic_tr) == np.sign(ic_te)) and abs(ic_te) > floor
        print(f'    IC all {ic:+.3f}  |  train(≤2024) {ic_tr:+.3f}  test(≥2025) {ic_te:+.3f}  '
              f'(floor {floor:.3f})  {"✅ SURVIVES" if ok else "✗"}')
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--detect', action='store_true')
    ap.add_argument('--breadth', action='store_true')
    ap.add_argument('--start', default='2024-01-02')
    ap.add_argument('--end', default='2026-08-16')
    ap.add_argument('--horizon', type=int, default=60)
    a = ap.parse_args()
    if a.detect:
        detect_report(a.start, a.end, a.horizon)
    if a.breadth:
        breadth_report(a.start, a.end, a.horizon)


if __name__ == '__main__':
    main()
