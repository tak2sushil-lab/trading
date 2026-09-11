"""
GIVE-BACK LEDGER — is the unprotected hold-to-close actually paying for itself?

The question (user, live, Sep 11 2026): DELL is +3.4% with its stop still at the original
entry-day level, because entries before 10:00 are flagged hold-to-close and ALL trailing is
suspended for them. The user's instinct is that we hand back what we are making. The same
week already contains POET (peak +5.83% -> closed +0.50%, kept 9%) next to CRWV, a comparable
move entered 56 minutes later, which had a trail and kept 81%.

The design is not arbitrary — holds_to_close() cites a real measurement, day-clustered on
4.9M 2026 bars: for 09:30-10:00 entries, holding to the close beat a 60-minutes-earlier exit
by +0.374% (t=+2.54). But that measurement is about the AVERAGE over the whole distribution,
and "better on 58% of days" means it is worse on 42% — so the give-back the user keeps seeing
is real, it is just not the whole story. The honest way to settle it is to measure OUR OWN
live trades, not re-argue the backtest.

WHAT THIS DOES. For every closed trade it compares:
    peak capture = exit_pct / max_gain_pct        (how much of the best moment we kept)
split by whether the trade was hold-to-close (entry < 10:00) or trailed (entry >= 10:00), and
then runs the counterfactual that actually matters: replay each hold-to-close trade's own
5-min bars and ask what a PCT trail (0.5% below the running session high, the same rule the
trailed population uses) would have exited at. That is a like-for-like answer to "should this
trade have been protected", computed from the trade's real bars rather than an assumption.

READ IT HONESTLY. Peak capture on its own is a trap — this codebase has already proven that
(futures, Jul 25 2026: every faster-exit variant RAISED capture% from 55 to 77 while turning
+$4,340 into -$264, because give-back is the premium paid for the right tail). So the column
that decides anything is the P&L delta, not the capture ratio. A trail that improves capture
while lowering total P&L is a worse trail.

Run: venv/bin/python research_giveback_holdclose.py [--days N]
"""
from __future__ import annotations

import argparse
import sqlite3

import pandas as pd

from collect_bars import load_bars

DB = 'trades.db'
HOLD_TO_CLOSE_BEFORE = (10, 0)   # mirrors auto_trader.HOLD_TO_CLOSE_BEFORE
PCT_TRAIL_ACTIVATE = 1.5         # mirrors auto_trader constants
PCT_TRAIL_GAP = 0.5


def load_trades(days: int) -> pd.DataFrame:
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT id, symbol, entry_date, entry_time, entry_price, exit_price, shares, side, "
        "       pnl, pnl_pct, max_gain_pct, exit_reason, setup_type "
        "FROM trades WHERE status IN ('WIN','LOSS') AND setup_type != 'RECONCILED' "
        f"AND entry_date >= date('now', '-{days} day') ORDER BY entry_date, entry_time", con)
    con.close()
    if df.empty:
        return df
    hm = df['entry_time'].astype(str).str.slice(0, 5).str.split(':', expand=True)
    df['h'] = pd.to_numeric(hm[0], errors='coerce')
    df['m'] = pd.to_numeric(hm[1], errors='coerce')
    df['hold_to_close'] = (df['h'] * 60 + df['m']) < (HOLD_TO_CLOSE_BEFORE[0] * 60 + HOLD_TO_CLOSE_BEFORE[1])
    return df


def pct_trail_counterfactual(row) -> float | None:
    """Replay this trade's own 5-min bars and return the % result a PCT trail would have got.

    Long-only (the hold-to-close population is long). Walks the bars from entry, tracks the
    running session high, arms at +1.5%, and exits the first time a bar's LOW pierces
    (high * (1 - 0.5%)). If never hit, the trade runs to its real exit — so this measures ONLY
    the effect of adding the trail, nothing else."""
    if str(row['side']).upper() != 'LONG':
        return None
    # load_bars' end is exclusive and its range handling is off by a day, so pull a window
    # and filter to the entry date ourselves rather than trusting the boundary.
    d0 = pd.Timestamp(row['entry_date'])
    try:
        bars = load_bars(row['symbol'],
                          start=(d0 - pd.Timedelta(days=1)).strftime('%Y-%m-%d'),
                          end=(d0 + pd.Timedelta(days=2)).strftime('%Y-%m-%d'))
    except Exception:
        return None
    if bars is None or bars.empty:
        return None
    bars = bars[bars.index.date == d0.date()]
    if bars.empty:
        return None
    try:
        t0 = pd.Timestamp(f"{row['entry_date']} {str(row['entry_time'])[:8]}")
        if bars.index.tz is not None:
            t0 = t0.tz_localize(bars.index.tz)
    except Exception:
        return None
    fwd = bars[bars.index >= t0]
    if len(fwd) < 2:
        return None

    entry = float(row['entry_price'])
    high = entry
    for _, b in fwd.iterrows():
        high = max(high, float(b['high']))
        gain = (high - entry) / entry * 100
        if gain >= PCT_TRAIL_ACTIVATE:
            stop = high * (1 - PCT_TRAIL_GAP / 100)
            if float(b['low']) <= stop and stop > entry:
                return (stop - entry) / entry * 100
    return float(row['pnl_pct'])      # trail never triggered — same as the real outcome


def report(df: pd.DataFrame, counterfactual: bool):
    print(f'\n{"="*100}')
    print('  GIVE-BACK LEDGER — peak capture, hold-to-close vs trailed')
    print(f'{"="*100}')
    if df.empty:
        print('  no closed trades in window'); return

    d = df[df['max_gain_pct'].notna()].copy()

    # Capture is only meaningful on trades that actually HAD a peak worth keeping, and it must
    # be aggregated as sum(exit)/sum(peak) — averaging per-trade ratios lets one trade with a
    # 0.01% peak and a -1% exit produce a -10,000% "average" and swamp everything real.
    print('  {:<16}{:>5}{:>11}{:>11}{:>14}{:>10}'.format(
        'population', 'n', 'avg peak%', 'avg exit%', 'capture(>=1%)', 'total $'))
    print('  ' + '-' * 70)
    for lbl, g in (('hold-to-close', d[d.hold_to_close]), ('trailed (10:00+)', d[~d.hold_to_close])):
        if g.empty:
            continue
        w = g[g['max_gain_pct'] >= 1.0]
        cap = (w['pnl_pct'].sum() / w['max_gain_pct'].sum() * 100) if len(w) and w['max_gain_pct'].sum() else float('nan')
        print('  {:<16}{:>5}{:>11.2f}{:>11.2f}{:>13.0f}%{:>10.2f}'.format(
            lbl, len(g), g['max_gain_pct'].mean(), g['pnl_pct'].mean(), cap, g['pnl'].sum()))
    print('  (capture = sum of exits / sum of peaks, over trades that peaked >= +1%)')

    # the winners are where give-back actually costs something
    print(f'\n  Trades that reached +2% or better at their peak (where give-back has teeth):')
    print('  {:<8}{:<7}{:>9}{:>9}{:>10}  {}'.format('sym', 'entry', 'peak%', 'exit%', 'kept', 'population'))
    print('  ' + '-' * 66)
    big = d[d['max_gain_pct'] >= 2.0].sort_values('max_gain_pct', ascending=False)
    for _, r in big.iterrows():
        print('  {:<8}{:<7}{:>9.2f}{:>9.2f}{:>9.0f}%  {}'.format(
            r['symbol'], str(r['entry_time'])[:5], r['max_gain_pct'], r['pnl_pct'],
            (r['pnl_pct'] / r['max_gain_pct'] * 100) if r['max_gain_pct'] else 0,
            'hold-to-close' if r['hold_to_close'] else 'trailed'))
    if big.empty:
        print('  (none yet)')

    if not counterfactual:
        print('\n  (run without --no-cf to replay what a PCT trail would have done)')
        return

    htc = d[d.hold_to_close].copy()
    if htc.empty:
        print('\n  no hold-to-close trades to replay'); return
    print(f'\n{"="*100}')
    print('  COUNTERFACTUAL — what a 0.5% PCT trail would have done to the hold-to-close book')
    print('  (replayed on each trade\'s own 5-min bars; everything else unchanged)')
    print(f'{"="*100}')
    print('  {:<8}{:<7}{:>9}{:>10}{:>12}{:>11}'.format('sym', 'entry', 'peak%', 'actual%', 'w/ trail%', 'delta $'))
    print('  ' + '-' * 60)
    tot_delta = 0.0
    for _, r in htc.iterrows():
        cf = pct_trail_counterfactual(r)
        if cf is None:
            continue
        delta = (cf - r['pnl_pct']) / 100 * float(r['entry_price']) * int(r['shares'])
        tot_delta += delta
        print('  {:<8}{:<7}{:>9.2f}{:>10.2f}{:>12.2f}{:>11.2f}'.format(
            r['symbol'], str(r['entry_time'])[:5], r['max_gain_pct'] or 0, r['pnl_pct'], cf, delta))
    print('  ' + '-' * 60)
    verdict = ('the trail would have HELPED' if tot_delta > 0 else
               'the trail would have COST us' if tot_delta < 0 else 'no difference')
    print(f'  net effect of adding a PCT trail to the hold-to-close book: ${tot_delta:+.2f}  '
          f'-> {verdict}')
    print('\n  ⚠️  Judge on this dollar line, NOT on the capture ratio. Raising capture while')
    print('      lowering P&L is a worse exit — proven on the futures book (Jul 25 2026),')
    print('      where capture 55% -> 77% turned +$4,340 into -$264.')
    print(f'  ⚠️  n={len(htc)} hold-to-close trades. This is a direction, not a verdict, until')
    print('      the sample is much larger.\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--no-cf', action='store_true', help='skip the bar-replay counterfactual')
    a = ap.parse_args()
    df = load_trades(a.days)
    report(df, counterfactual=not a.no_cf)


if __name__ == '__main__':
    main()
