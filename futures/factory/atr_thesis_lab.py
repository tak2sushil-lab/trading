"""
ATR THESIS-INVALIDATION LAB (Aug 24 2026)

User's question: "will ATR help fix ENTRY, or also understanding thesis invalidation so we
can plan exits based on that?"

WHY THIS IS NOT A RE-RUN OF A REJECTED IDEA. Cutting losers early has been tested and
rejected three times — but every one of those tests was denominated in POINTS or in % of peak:
  * Jul 25 exit-lab: retrace fractions of peak -> every faster variant LOSES.
  * Aug 19 wide-stop lab: time stops + point thresholds -> "worst lever tested".
  * Aug 24 regime-flip vote: marginal, green 2/6.
The 200pt stop IS an adverse-excursion cut, but at A0=31.4 that is ~6.4 ATR — so far away it
fires on 6% of trades. NOBODY HAS TESTED INVALIDATION IN ATR UNITS, which is the whole point
of the units finding. Median MAE is 61pts ~= 1.9 ATR, so a 1.5-2 ATR rule is a completely
different instrument from a 200pt stop.

Two rules, both using ATR snapshotted at entry:
  A) ADVERSE  — cut when the trade goes >= X * ATR against the entry ("thesis broken").
  B) STALLED  — cut when, after N bars, the trade has not reached >= Y * ATR in its favour
                ("thesis never held"). This is the ATR version of the orphan problem.

Baseline = the same replay engine with the live point-based stack, so the comparison is
within-engine and fair. NOTE the engine is ~$5.7/trade pessimistic vs the cached book
(see atr_exit_lab.py calibration gate) — read DELTAS, not absolute totals.

Run: venv/bin/python -m futures.factory.atr_thesis_lab
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

ROOT = '/Users/sushil/trading'
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from futures.factory.atr_exit_lab import (load_bars, build_ctx, replay_trade, EXIT_PARAMS,
                                          day_regime, FRICTION, DPP, YRS, FAC, ET)


def replay_with_rule(t, post, reg, atr_e, mode, X=None, N=None, Y=None):
    """Live point-based stack, PLUS an ATR invalidation rule that can fire first."""
    sgn = 1 if t.side == 'LONG' else -1
    entry = float(t.entry)
    bars = 0
    for ts, bar in post.iterrows():
        bars += 1
        hi, lo, cl = float(bar.high), float(bar.low), float(bar.close)
        adverse = sgn * (entry - (lo if sgn > 0 else hi))
        favour = sgn * ((hi if sgn > 0 else lo) - entry)
        if mode == 'adverse' and adverse >= X * atr_e:
            return (-X * atr_e, 'atr_invalidated')
        if mode == 'stalled' and bars >= N:
            # peak favour so far, measured on closed bars
            run = post.iloc[:bars]
            mfe = float((sgn * ((run.high - entry) if sgn > 0 else (entry - run.low))).max())
            if mfe < Y * atr_e:
                return (sgn * (cl - entry), 'atr_stalled')
    return None      # rule never fired -> fall through to the normal stack


def run(ctx, mode=None, **kw):
    rows = []
    for t, rth, post, atr_e, reg in ctx:
        r = replay_with_rule(t, post, reg, atr_e, mode, **kw) if mode else None
        if r is None:
            r = replay_trade(t, post, reg, 1.0)
        if r is None:
            continue
        pts, why = r
        rows.append(dict(date=t.date, year=t.year, side=t.side, cached=t.pnl,
                         pnl=pts * DPP * t.contracts - FRICTION * max(1, t.contracts),
                         why=why, atr=atr_e))
    return pd.DataFrame(rows)


def line(g, label):
    y = g.groupby('year').pnl.sum().reindex(YRS).fillna(0)
    dd = g.groupby('date').pnl.sum().sort_index(); eq = dd.cumsum()
    fired = int((g.why.isin(['atr_invalidated', 'atr_stalled'])).sum())
    print(f"  {label:30s} ${g.pnl.sum():+8,.0f} DD={(eq-eq.cummax()).min():8,.0f} "
          f"fired={fired:4d} green={(y>0).sum()}/6  " + " ".join(f"{a[2:]}:{v:+6.0f}" for a, v in y.items()))


def main():
    t = pd.read_csv(os.path.join(FAC, '_mom_2021-06-01_2026-08-14.csv'))
    t['date'] = t.date.astype(str); t['year'] = t.date.str[:4]
    t['ent'] = pd.to_datetime(t.date + ' ' + t.entry_time.astype(str)).dt.tz_localize(ET)
    ctx = build_ctx(t, load_bars())
    base = run(ctx)
    med_atr = float(np.median([c[3] for c in ctx]))
    print(f"{len(base)} trades | median ATR at entry {med_atr:.1f}pts "
          f"| the live 200pt stop = {200/med_atr:.1f} ATR, the 120pt trail-arm = {120/med_atr:.1f} ATR\n")
    print("=" * 96)
    print("A) THESIS BROKEN — cut at X ATR adverse  (the live stop sits at ~6.4 ATR)")
    print("=" * 96)
    line(base, 'baseline (live stack)')
    for X in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
        line(run(ctx, mode='adverse', X=X), f'cut at {X:.1f} ATR adverse')
    print("\n" + "=" * 96)
    print("B) THESIS NEVER HELD — cut if not +Y ATR favourable within N bars (N x 5 min)")
    print("=" * 96)
    line(base, 'baseline (live stack)')
    for N in (6, 12, 18):
        for Y in (0.5, 1.0, 1.5):
            line(run(ctx, mode='stalled', N=N, Y=Y), f'{N*5:3d}min without +{Y:.1f} ATR')


if __name__ == '__main__':
    main()
