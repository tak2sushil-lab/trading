"""
PROFIT LOCK — "let ordinary trades ride, but once one has PROVEN itself, protect it."

This is deliberately NOT the idea this codebase has already killed. What has been tested and
rejected, repeatedly, is exiting EARLIER in general:
  futures, Jul 25 2026 — every faster-exit variant lost money; "exit the minute it reverses"
    raised capture from 55% to 77% and turned +$4,340 into -$264, because give-back is the
    premium paid for the right tail.
  equity, Aug 8 2026 — the user's own instinct to bank winners at +0.75-1.5% was tested and
    the data argued against it.
So a blanket tighter exit is not on the table and this script does not test one.

The hypothesis here is conditional and narrower, and it is the user's actual words: "we are
right some times and those times should give us plenty." Ordinary trades keep behaving exactly
as they do now — ride, unprotected, which is what the hold-to-close measurement validated
(09:30-10:00 entries beat a 60-min-earlier exit by +0.374%, t=+2.54). A trail ARMS only after a
trade has already gone big enough to have proven the thesis:

    if peak_gain >= ARM%:  stop = running_high * (1 - GAP%)

Below ARM nothing changes at all. That is the whole difference: this does not shorten the
average trade, it only refuses to hand back an exceptional one. POET (peak +5.83% -> closed
+0.50%, kept 9%) is the case it is aimed at; DELL (+3.4% peak, drifting back) is today's.

METHOD. Replay each closed LONG trade on its own 5-min bars from entry. If the armed trail is
touched before the real exit, take the trail price; otherwise keep the real outcome. So the
measured delta is the effect of ADDING the lock and nothing else. Results are reported per
MONTH and split hold-to-close vs trailed, because this codebase has been burned repeatedly by
a single good period carrying an aggregate (Aug 8 2026: a fix that helped overall hid a
per-period regression).

READ THE DOLLAR COLUMN, NOT THE CAPTURE COLUMN.

Run: venv/bin/python research_profit_lock.py
"""
from __future__ import annotations

import argparse
import sqlite3

import pandas as pd

from collect_bars import load_bars

DB = 'trades.db'
HOLD_TO_CLOSE_BEFORE = (10, 0)


def load_trades(start: str) -> pd.DataFrame:
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT id, symbol, entry_date, entry_time, entry_price, exit_time, shares, side, "
        "       pnl, pnl_pct, max_gain_pct, exit_reason "
        "FROM trades WHERE status IN ('WIN','LOSS') AND setup_type != 'RECONCILED' "
        f"AND side='LONG' AND entry_date >= '{start}' ORDER BY entry_date, entry_time", con)
    con.close()
    if df.empty:
        return df
    hm = df['entry_time'].astype(str).str.slice(0, 5).str.split(':', expand=True)
    df['mins'] = pd.to_numeric(hm[0], errors='coerce') * 60 + pd.to_numeric(hm[1], errors='coerce')
    df['hold_to_close'] = df['mins'] < (HOLD_TO_CLOSE_BEFORE[0] * 60 + HOLD_TO_CLOSE_BEFORE[1])
    df['month'] = df['entry_date'].str.slice(0, 7)
    return df


_BARCACHE: dict = {}


def day_bars(sym: str, d: str):
    key = (sym, d)
    if key in _BARCACHE:
        return _BARCACHE[key]
    out = None
    try:
        d0 = pd.Timestamp(d)
        b = load_bars(sym, start=(d0 - pd.Timedelta(days=1)).strftime('%Y-%m-%d'),
                       end=(d0 + pd.Timedelta(days=2)).strftime('%Y-%m-%d'))
        if b is not None and len(b) and getattr(b.index, 'tz', None) is not None:
            b = b[b.index.date == d0.date()]
            out = b if len(b) else None
    except Exception:
        out = None
    _BARCACHE[key] = out
    return out


def replay(row, arm_pct: float, gap_pct: float):
    """Return the trade's % result with an armed profit-lock, or None if not replayable."""
    b = day_bars(row['symbol'], row['entry_date'])
    if b is None:
        return None
    try:
        t0 = pd.Timestamp(f"{row['entry_date']} {str(row['entry_time'])[:8]}").tz_localize(b.index.tz)
    except Exception:
        return None
    t1 = None
    if row['exit_time']:
        try:
            t1 = pd.Timestamp(f"{row['entry_date']} {str(row['exit_time'])[:8]}").tz_localize(b.index.tz)
        except Exception:
            t1 = None
    w = b[b.index >= t0]
    if t1 is not None:
        w = w[w.index <= t1]
    if len(w) < 2:
        return None

    entry = float(row['entry_price'])
    high = entry
    for _, bar in w.iterrows():
        high = max(high, float(bar['high']))
        if (high - entry) / entry * 100 >= arm_pct:
            stop = high * (1 - gap_pct / 100)
            if float(bar['low']) <= stop:
                return (stop - entry) / entry * 100
    return float(row['pnl_pct'])          # never armed/triggered — unchanged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2026-04-01')
    a = ap.parse_args()

    df = load_trades(a.start)
    if df.empty:
        print('no trades'); return
    df = df[df['entry_price'].notna() & df['shares'].notna()]
    print(f'  {len(df)} closed LONG trades since {a.start}; replaying on 5-min bars…')

    grid = [(arm, gap) for arm in (2.0, 3.0, 4.0, 5.0) for gap in (0.5, 1.0, 1.5)]
    months = sorted(df['month'].unique())

    print(f'\n{"="*104}')
    print('  PROFIT LOCK GRID — $ delta vs what actually happened (positive = the lock helped)')
    print(f'{"="*104}')
    hdr = '  {:<12}{:>9}{:>10}'.format('arm / gap', 'n fired', 'total $')
    for m in months:
        hdr += f'{m[5:]:>9}'
    print(hdr)
    print('  ' + '-' * (31 + 9 * len(months)))

    best = None
    for arm, gap in grid:
        deltas, fired = [], 0
        per_month = {m: 0.0 for m in months}
        for _, r in df.iterrows():
            cf = replay(r, arm, gap)
            if cf is None:
                continue
            d = (cf - r['pnl_pct']) / 100 * float(r['entry_price']) * int(r['shares'])
            if abs(d) > 1e-9:
                fired += 1
            deltas.append(d)
            per_month[r['month']] += d
        tot = sum(deltas)
        line = '  {:<12}{:>9}{:>+10.0f}'.format(f'{arm:.0f}% / {gap:.1f}%', fired, tot)
        for m in months:
            line += f'{per_month[m]:>+9.0f}'
        print(line)
        greens = sum(1 for m in months if per_month[m] > 0)
        if best is None or tot > best[0]:
            best = (tot, arm, gap, greens, fired)

    print()
    if best:
        tot, arm, gap, greens, fired = best
        print(f'  best cell: arm {arm:.0f}% / gap {gap:.1f}%  ->  ${tot:+,.0f} '
              f'across {fired} affected trades, positive in {greens}/{len(months)} months')
    print('\n  ⚠️  A cell only counts if it is positive in MOST months, not just in total — one')
    print('      good month carrying an aggregate is the exact trap this codebase keeps hitting.')
    print('  ⚠️  The replay assumes a clean fill at the trail price; a fast spike-and-revert')
    print('      would fill worse, so these numbers are optimistic.\n')

    # split the best cell by population
    if best:
        _, arm, gap, _, _ = best
        print(f'  Best cell ({arm:.0f}%/{gap:.1f}%) split by population:')
        for lbl, g in (('hold-to-close', df[df.hold_to_close]), ('trailed (10:00+)', df[~df.hold_to_close])):
            tot = 0.0; n = 0
            for _, r in g.iterrows():
                cf = replay(r, arm, gap)
                if cf is None:
                    continue
                d = (cf - r['pnl_pct']) / 100 * float(r['entry_price']) * int(r['shares'])
                tot += d
                if abs(d) > 1e-9:
                    n += 1
            print(f'    {lbl:<18} {n:>4} trades affected   ${tot:>+9.0f}')
        print()


if __name__ == '__main__':
    main()
