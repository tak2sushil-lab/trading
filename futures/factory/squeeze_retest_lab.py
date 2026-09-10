"""
SQUEEZE + RETEST LAB — two more ideas on MNQ, in response to the user's follow-up:
"tune the Bollinger lead, find another version pros use."

(A) BOLLINGER SQUEEZE BREAKOUT. Not from either source document — this is John Bollinger's
    own documented technique (bandwidth contracts to a multi-month low = "the squeeze",
    the breakout that follows tends to run) and one of the most widely cited professional
    setups built on the same indicator the Bollinger-extreme lever already uses. Cheap to
    test with infrastructure we already built (boll_bw from indicator_feature_lab.py).
    Squeeze = bandwidth in the bottom decile of its trailing 100-bar history. Signal = the
    first close outside the bands after a squeeze.

(B) BREAKOUT-RETEST. TS.pptx's "Swing Breakout Sequence" (breakout -> tap -> scalp -> return
    to liquidate -> double-bottom reversal -> target new high) cannot be tested honestly: the
    course's own example is annotated on 15-SECOND bars (we collect 5-min/1-min), and steps
    like "quick scalp" and "double bottom reversal" require a discretionary judgment call
    about which wiggle counts — exactly the curve-fit risk this program has been burned by
    before (Sep 5 day_chg>=7% filter). What DOES have an objective definition, and is a
    real, widely-used professional technique (also literally in the Fidelity deck as
    "throwback"/"retracement" and "confirm a breakout"): waiting for price to come back and
    RETEST the breakout level itself (not a Fibonacci zone — the Sep-07 finding already
    showed the 50% zone doesn't beat a generic pullback). This reuses the existing 10:30 IB
    level, since that is this book's own breakout reference, not an invented one.
    Signal = first 5-min bar after an IB breakout whose range touches back to the IB
    boundary (within 0.15 ATR) and closes back in the breakout direction.

Both use the same forward-return-label method as ict_discount_lab.py (ATR-normalized,
no stop/target simulated, isolates signal quality from exit design) and the same walk-forward
per-year discipline as everything else in this program.

Run: venv/bin/python -m futures.factory.squeeze_retest_lab
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
from futures.factory.conditions import _sess, RTH_OPEN, RTH_END  # noqa: E402

IB_END = _dt.time(10, 30)
TRAIN_END = 2023


def boll_cols(close: pd.Series, n=20, k=2.0) -> pd.DataFrame:
    mid = close.rolling(n, min_periods=n).mean()
    sd = close.rolling(n, min_periods=n).std()
    upper, lower = mid + k * sd, mid - k * sd
    return pd.DataFrame({'upper': upper, 'lower': lower, 'mid': mid,
                          'bw': (upper - lower) / mid})


def _daily_atr(rth: pd.DataFrame) -> pd.Series:
    daily = rth.groupby(rth.index.date).agg(h=('high', 'max'), l=('low', 'min'), c=('close', 'last'))
    daily.index = pd.to_datetime(list(daily.index)).tz_localize(rth.index.tz)
    pc = daily['c'].shift(1)
    tr = pd.concat([daily['h'] - daily['l'], (daily['h'] - pc).abs(), (daily['l'] - pc).abs()],
                    axis=1).max(axis=1)
    return tr.rolling(20, min_periods=10).mean().shift(1)


def _fwd_labels(rth: pd.DataFrame, sig_ts, sgn: int, atr: float) -> tuple[float, float] | None:
    pos = rth.index.get_loc(sig_ts)
    if pos + 12 >= len(rth) or not np.isfinite(atr) or atr <= 0:
        return None
    entry = float(rth['close'].iloc[pos])
    fwd6 = sgn * (float(rth['close'].iloc[pos + 6]) - entry) / atr
    fwd12 = sgn * (float(rth['close'].iloc[pos + 12]) - entry) / atr
    return fwd6, fwd12


# ── (A) squeeze breakout ─────────────────────────────────────────────────────
def squeeze_signals(rth: pd.DataFrame, daily_atr: pd.Series) -> pd.DataFrame:
    bb = boll_cols(rth['close'])
    bw_rank = bb['bw'].rolling(100, min_periods=50).apply(
        lambda w: (w < w[-1]).mean(), raw=True)     # trailing percentile, causal
    squeeze = bw_rank <= 0.10
    was_squeezed = squeeze.shift(1).rolling(6, min_periods=1).max().fillna(0).astype(bool)
    up_break = was_squeezed & (rth['close'] > bb['upper']) & (rth['close'].shift(1) <= bb['upper'].shift(1))
    dn_break = was_squeezed & (rth['close'] < bb['lower']) & (rth['close'].shift(1) >= bb['lower'].shift(1))

    rows = []
    for sgn, mask, direction in ((1, up_break, 'UP'), (-1, dn_break, 'DOWN')):
        for ts in rth.index[mask.fillna(False)]:
            atr = daily_atr.asof(ts)
            lab = _fwd_labels(rth, ts, sgn, atr)
            if lab is None:
                continue
            rows.append(dict(date=str(ts.date()), year=ts.year, direction=direction,
                              fwd6=lab[0], fwd12=lab[1]))
    return pd.DataFrame(rows)


# ── (B) breakout-retest of the IB level ──────────────────────────────────────
def retest_signals(bars: pd.DataFrame, rth: pd.DataFrame, daily_atr: pd.Series) -> pd.DataFrame:
    rows = []
    for d, day in rth.groupby(rth.index.date):
        ib = _sess(day, RTH_OPEN, IB_END)
        pm = day[day.index.time > IB_END]
        if len(ib) < 6 or len(pm) < 15:
            continue
        ts0 = pd.Timestamp(d)
        atr = daily_atr.asof(pm.index[0])
        if not np.isfinite(atr) or atr <= 0:
            continue
        ib_hi, ib_lo = float(ib['high'].max()), float(ib['low'].min())
        tol = 0.15 * atr

        # find the FIRST confirmed breakout beyond the IB in either direction
        up_break = pm.index[pm['close'] > ib_hi]
        dn_break = pm.index[pm['close'] < ib_lo]
        for sgn, break_idx, level, direction in ((1, up_break, ib_hi, 'UP'),
                                                   (-1, dn_break, ib_lo, 'DOWN')):
            if len(break_idx) == 0:
                continue
            bts = break_idx[0]
            after = pm[pm.index > bts]
            if len(after) < 8:
                continue
            if sgn == 1:
                touch = after.index[(after['low'] <= level + tol) & (after['close'] > level)]
            else:
                touch = after.index[(after['high'] >= level - tol) & (after['close'] < level)]
            if len(touch) == 0:
                continue
            sig_ts = touch[0]
            lab = _fwd_labels(rth, sig_ts, sgn, atr)
            if lab is None:
                continue
            rows.append(dict(date=str(d), year=ts0.year, direction=direction,
                              fwd6=lab[0], fwd12=lab[1]))
    return pd.DataFrame(rows)


def _report(m: pd.DataFrame, name: str):
    print(f'\n{"="*100}')
    print(f'  {name}')
    print(f'{"="*100}')
    if len(m) == 0:
        print('  zero signals.')
        return
    for lab in ('fwd6', 'fwd12'):
        print(f'  {lab}: n={len(m)}  mean={m[lab].mean():+.3f} ATR  median={m[lab].median():+.3f}'
              f'  win%={100*(m[lab]>0).mean():.0f}%')
    print(f'\n  Per-year mean fwd12 (must hold up per year, not just in total):')
    line = '  '
    for y in sorted(m.year.unique()):
        g = m[m.year == y]
        line += f'{y}:{g["fwd12"].mean():+.2f}(n{len(g)})  '
    print(line)
    tr, te = m[m.year <= TRAIN_END], m[m.year > TRAIN_END]
    print(f'\n  TRAIN 2021-23: n={len(tr)} mean={tr["fwd12"].mean():+.3f}   '
          f'TEST 2024-26: n={len(te)} mean={te["fwd12"].mean():+.3f}')
    print()


def main():
    bars = load_bars('MNQ', start='2021-01-04', end='2026-08-15')
    rth = _sess(bars, RTH_OPEN, RTH_END).sort_index()
    atr = _daily_atr(rth)

    sq = squeeze_signals(rth, atr)
    sq.to_csv(os.path.join(ROOT, 'futures', 'factory', '_squeeze_signals.csv'), index=False)
    _report(sq, 'A) BOLLINGER SQUEEZE BREAKOUT')

    rt = retest_signals(bars, rth, atr)
    rt.to_csv(os.path.join(ROOT, 'futures', 'factory', '_retest_signals.csv'), index=False)
    _report(rt, 'B) BREAKOUT-RETEST OF THE IB LEVEL (SBS operationalized honestly)')

    print(f'  (for scale: the ICT discount-zone control group averaged fwd12 ≈ +0.03-0.05 ATR')
    print(f'   at ~53-58% win rate — that is the bar a "real discovery" here needs to clear.)\n')


if __name__ == '__main__':
    main()
