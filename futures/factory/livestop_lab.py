"""
LIVE-STOP LAB — "keep doing exactly what the live code does, just change the SL."

No replacement exit engine. This runs sim_replay (the validated mirror of the live traders)
with the full live exit stack intact — BE lock, regime-aware trail tiers, rev-exit 2/0.30/120,
partial scale-out at 150, no-move, VWAP cross, DLL circuit, EOD — and changes exactly one
constant: BASE_STOP_PTS.

Contract count is deliberately NOT overridden. calc_contracts() derives it from the stop, so
the risk budget is what moves:
    200pt (live) -> 2 contracts -> $  800 max risk
    500pt        -> 2 contracts -> $2,000 max risk     <- "500 x 2"
   1000pt        -> 1 contract  -> $2,000 max risk     <- "1000 x 1"
The 500 and 1000 rows are the SAME $2,000 risk budget spent two different ways.

Blow accounting per prop_rules:  TC $50k = DLL $1,000 halt, $2,000 trailing MLL = dead.
                                 IBKR    = DLL $1,250 halt, $5,000 allocation gone = dead.

Run: venv/bin/python -m futures.factory.livestop_lab
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from futures.factory.patient_lab import (blowups, daily_with_dll, TC_DLL, IB_DLL)  # noqa: E402

FAC, FRICTION, END = os.path.join(ROOT, 'futures', 'factory'), 6.0, '2026-08-14'


def load(stop: int, window: str = 'full') -> pd.DataFrame:
    """Return the trade frame for a given BASE_STOP_PTS, friction applied UNIFORMLY.
    The 200pt base cache was written by bench.run_ny which already subtracted friction;
    the _stoprun.py files are raw. Normalise both to raw, then charge friction once."""
    sfx = '' if window == 'full' else '_2026'
    fn = os.path.join(FAC, f'_mom_livestop{stop}{sfx}.csv')
    if stop == 200 and window == 'full':
        d = pd.read_csv(os.path.join(FAC, '_mom_2021-06-01_2026-08-14.csv'))
        d['pnl'] = d['pnl'] + FRICTION * d['contracts'].clip(lower=1)      # undo
    elif os.path.exists(fn):
        d = pd.read_csv(fn)
    else:
        return pd.DataFrame()
    d['date'] = d['date'].astype(str)
    d = d[d.date <= END].copy()                       # common window across all runs
    d['pnl'] = d['pnl'] - FRICTION * d['contracts'].clip(lower=1)
    d['y'] = pd.to_datetime(d['date']).dt.year
    return d.sort_values(['date', 'entry_time']).reset_index(drop=True)


def row(lbl, d, acct, years, cap=None):
    if cap:
        d = d.groupby('date', group_keys=False).head(cap)
    dll = IB_DLL if acct == 'IBKR' else TC_DLL
    daily = daily_with_dll(d, dll)
    ev, halts = blowups(daily, acct)
    tot = float(daily.sum())
    cum = daily.cumsum()
    dd = float((cum - cum.cummax()).min())
    yy = pd.to_datetime(daily.index).year
    per = ''.join(f'{daily[yy == y].sum():>+9,.0f}' for y in years)
    print(f'  {lbl:<30}{len(d):>5}{d.contracts.mean():>6.2f}{100*(d.pnl>0).mean():>5.0f}%'
          f'{int((d.pnl<0).sum()):>6}{tot:>+9,.0f}{dd:>+9,.0f}{daily.min():>+9,.0f}'
          f'{halts:>6}{len(ev):>6}  {per}')
    return dict(tot=tot, dd=dd, worst=float(daily.min()), halts=halts, blows=len(ev), ev=ev)


def hdr(years):
    print(f'  {"BASE_STOP_PTS":<30}{"n":>5}{"avg c":>6}{"WR":>6}{"lose":>6}{"total":>9}'
          f'{"maxDD":>9}{"wrstDay":>9}{"halt":>6}{"BLOW":>6}  '
          + ''.join(f'{y:>9}' for y in years))
    print('  ' + '-' * (30 + 5 + 6 + 6 + 6 + 9 + 9 + 9 + 6 + 6 + 2 + 9 * len(years)))


STOPS = [200, 500, 1000, 1200]


def _risk(s):
    return s * (2 if s <= 500 else 1) * 2


def main():
    for window, label in (('full', 'FULL HISTORY 2021-06 -> 2026-08'),
                          ('2026', '2026 ONLY (the current market regime)')):
        frames = {s: load(s, window) for s in STOPS}
        frames = {s: d for s, d in frames.items() if len(d)}
        if not frames:
            continue
        missing = [s for s in STOPS if s not in frames]
        years = sorted(next(iter(frames.values())).y.unique())
        print(f'\n\n{"#"*168}')
        print(f'#  {label}' + (f'      (not built yet: {missing})' if missing else ''))
        print(f'{"#"*168}')
        for acct in ('IBKR', 'TC'):
            print(f'\n  LIVE CODE, ONE CHANGE: BASE_STOP_PTS   —   {acct}'
                  f'   (DLL ${IB_DLL if acct=="IBKR" else TC_DLL:,.0f} halt, '
                  f'{"$5,000 allocation" if acct=="IBKR" else "$2,000 trailing MLL"} = dead)')
            hdr(years)
            for s in sorted(frames):
                row(f'{s}pt  (${_risk(s):,} max risk)', frames[s], acct, years,
                    cap=(2 if acct == 'TC' else 5))
    print('\n  "lose" = losing trades. "halt" = days the daily loss limit stopped trading.')
    print('  "BLOW" = times the account died outright (reset and kept counting afterwards).')
    print('  Contract count is NOT overridden — calc_contracts() derives it from the stop.')


if __name__ == '__main__':
    main()
