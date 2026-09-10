"""
ICT DISCOUNT-ZONE LAB — one specific, strictly-defined claim from TS.pptx's Smart-Money-
Concepts slides, tested on MNQ. Not the whole ICT vocabulary — just the one piece that can be
given a hard, non-discretionary definition: "entering at the 50% retracement (discount) of the
prior higher-timeframe swing, with a rejection candle, beats a generic pullback entry."

Why only this piece: the deck's other ICT terms ("unmitigated" zone, "manipulation phase", MSB
trigger) require a human judgment call about which swing points "count" — exactly the kind of
subjective parameter choice this program has been burned by before (the Sep 5 day_chg>=7%
filter that looked like a clean edge and was a curve-fit, caught only by a cross-year check).
A swing-point fractal and a fixed retracement band have none of that: anyone re-running this
script gets the identical trade list.

STRICT DEFINITIONS (no fitting, no sweep — these are the standard textbook values):
  HTF swings: on 30-min bars, a bar is a confirmed swing high/low if its high/low is the max/min
    of a 5-bar window centered on it (2 bars each side) — a standard fractal, CAUSALLY shifted:
    a swing at bar (i-2) is only knowable once bar i has printed (needs the 2 bars after it).
  Current leg: the most recent swing-low-then-swing-high pair (uptrend) or swing-high-then-
    swing-low pair (downtrend), refreshed every time a new swing confirms.
  Discount / premium zone: 45%-55% retracement of that leg (the ICT "50% zone", given a small
    band instead of an exact point so the sample isn't a single-tick coincidence).
  Generic pullback (control group): 10%-90% retracement EXCLUDING the 45-55% band — same
    rejection-candle requirement, different depth. This isolates the ONE claim under test
    (does the specific 50% depth matter) from the un-controversial claim (pullbacks exist).
  Entry trigger: the first 5-min bar per leg whose LOW enters the zone AND whose own candle
    closes bullish (uptrend leg) / bearish (downtrend leg) — an objective "rejection candle",
    not a discretionary "MSB" read. At most one discount-zone signal and one generic-pullback
    signal per leg, so one leg cannot flood one bucket with correlated bars.
  Label: forward return over the next 6 and 12 five-min bars (30min / 60min) from the signal
    bar's close, in ATR(20, daily) units, signed for the leg's direction (+ = continuation).
    No stop/target is simulated — this isolates the forecast question from an unrelated exit-
    design choice.

Run: venv/bin/python -m futures.factory.ict_discount_lab
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from futures.collect_bars import load_bars                       # noqa: E402
from futures.factory.conditions import _sess, RTH_OPEN, RTH_END  # noqa: E402

FRACTAL_N = 2
ZONE_LO, ZONE_HI = 0.45, 0.55
CTRL_LO, CTRL_HI = 0.10, 0.90
TRAIN_END = 2023


def find_swings(h: pd.Series, l: pd.Series, n=FRACTAL_N):
    """Causally-confirmed swing high/low booleans, aligned to CONFIRMATION time (n bars after
    the actual pivot — a swing point cannot be known before price has moved away from it)."""
    win = 2 * n + 1
    roll_max = h.rolling(win, center=True).max()
    roll_min = l.rolling(win, center=True).min()
    raw_high = (h == roll_max)
    raw_low = (l == roll_min)
    return raw_high.shift(n).fillna(False), raw_low.shift(n).fillna(False)


def build_legs(bars30: pd.DataFrame) -> pd.DataFrame:
    """Walk the confirmed swings in chronological order and emit one leg per consecutive
    ALTERNATING pair (low then high = uptrend leg, high then low = downtrend leg). Two
    same-type pivots in a row (no intervening opposite pivot ever confirmed) define no leg
    and are skipped — simplest possible unambiguous rule, no discretion."""
    sh, sl = find_swings(bars30['high'], bars30['low'])
    pivots = []
    for ts, is_h, is_l in zip(bars30.index, sh, sl):
        if is_h:
            pivots.append((ts, 'H', float(bars30.loc[ts, 'high'])))
        if is_l:
            pivots.append((ts, 'L', float(bars30.loc[ts, 'low'])))
    pivots.sort(key=lambda x: x[0])

    legs = []
    for (ts0, k0, px0), (ts1, k1, px1) in zip(pivots, pivots[1:]):
        if k0 == 'L' and k1 == 'H':
            legs.append(dict(direction='UP', confirmed_at=ts1, lo_ts=ts0, lo_px=px0,
                              hi_ts=ts1, hi_px=px1))
        elif k0 == 'H' and k1 == 'L':
            legs.append(dict(direction='DOWN', confirmed_at=ts1, hi_ts=ts0, hi_px=px0,
                              lo_ts=ts1, lo_px=px1))
    return pd.DataFrame(legs).sort_values('confirmed_at').reset_index(drop=True)


def label_signals(legs: pd.DataFrame, bars5: pd.DataFrame, daily_atr: pd.Series) -> pd.DataFrame:
    rows = []
    for _, leg in legs.iterrows():
        rng = leg['hi_px'] - leg['lo_px']
        if rng <= 0:
            continue
        start = leg['confirmed_at']
        # leg stays "active" until price closes back beyond its own start (a crude but
        # objective invalidation rule — no discretionary "mitigation" judgment)
        fwd = bars5[bars5.index > start]
        if leg['direction'] == 'UP':
            invalid = fwd[fwd['close'] < leg['lo_px']]
        else:
            invalid = fwd[fwd['close'] > leg['hi_px']]
        end = invalid.index[0] if len(invalid) else (fwd.index[-1] if len(fwd) else start)
        window = bars5[(bars5.index > start) & (bars5.index <= end)]
        if len(window) < 15:
            continue

        if leg['direction'] == 'UP':
            retr = (leg['hi_px'] - window['low']) / rng
            bull_candle = window['close'] > window['open']
        else:
            retr = (window['high'] - leg['lo_px']) / rng
            bull_candle = window['close'] < window['open']

        zone_hits = window.index[(retr >= ZONE_LO) & (retr <= ZONE_HI) & bull_candle]
        ctrl_hits = window.index[(((retr >= CTRL_LO) & (retr < ZONE_LO)) |
                                   ((retr > ZONE_HI) & (retr <= CTRL_HI))) & bull_candle]

        for group, hits in (('discount_zone', zone_hits), ('generic_pullback', ctrl_hits)):
            if len(hits) == 0:
                continue
            sig_ts = hits[0]
            pos = bars5.index.get_loc(sig_ts)
            if pos + 12 >= len(bars5):
                continue
            atr = daily_atr.asof(sig_ts)
            if not np.isfinite(atr) or atr <= 0:
                continue
            entry = float(bars5['close'].iloc[pos])
            sgn = 1 if leg['direction'] == 'UP' else -1
            fwd6 = sgn * (float(bars5['close'].iloc[pos + 6]) - entry) / atr
            fwd12 = sgn * (float(bars5['close'].iloc[pos + 12]) - entry) / atr
            rows.append(dict(
                date=str(sig_ts.date()), year=sig_ts.year, direction=leg['direction'],
                group=group, retr=float(retr.loc[sig_ts]), fwd6=fwd6, fwd12=fwd12,
            ))
    return pd.DataFrame(rows)


def build(start='2021-01-04', end='2026-08-15') -> pd.DataFrame:
    bars = load_bars('MNQ', start=start, end=end)
    rth = _sess(bars, RTH_OPEN, RTH_END).sort_index()

    daily = rth.groupby(rth.index.date).agg(h=('high', 'max'), l=('low', 'min'), c=('close', 'last'))
    daily.index = pd.to_datetime(list(daily.index)).tz_localize(rth.index.tz)
    pc = daily['c'].shift(1)
    tr = pd.concat([daily['h'] - daily['l'], (daily['h'] - pc).abs(), (daily['l'] - pc).abs()],
                    axis=1).max(axis=1)
    atr20 = tr.rolling(20, min_periods=10).mean().shift(1)   # yesterday's completed ATR, causal

    o = rth['open'].resample('30min').first()
    h = rth['high'].resample('30min').max()
    l = rth['low'].resample('30min').min()
    c = rth['close'].resample('30min').last()
    bars30 = pd.concat([o, h, l, c], axis=1).dropna()
    bars30.columns = ['open', 'high', 'low', 'close']

    legs = build_legs(bars30)
    print(f'  {len(legs)} confirmed HTF swing legs ({(legs.direction=="UP").sum()} up / '
          f'{(legs.direction=="DOWN").sum()} down)')
    return label_signals(legs, rth, atr20)


def report(m: pd.DataFrame):
    print(f'\n{"="*100}')
    print('  ICT DISCOUNT-ZONE LAB — does the 45-55% retracement zone beat a generic pullback?')
    print(f'{"="*100}')
    if len(m) == 0:
        print('  zero signals — check the leg/zone logic before concluding anything.')
        return

    for lab in ('fwd6', 'fwd12'):
        print(f'\n  ── label: {lab} (continuation, ATR units, + = favorable) ' + '─' * 30)
        print('  {:<18}{:>7}{:>10}{:>10}{:>8}'.format('group', 'n', 'mean', 'median', 'win%'))
        for grp in ('discount_zone', 'generic_pullback'):
            g = m[m['group'] == grp]
            if len(g) == 0:
                continue
            print('  {:<18}{:>7}{:>10.3f}{:>10.3f}{:>7.0f}%'.format(
                grp, len(g), g[lab].mean(), g[lab].median(), 100 * (g[lab] > 0).mean()))
        dz, gp = m[m.group == 'discount_zone'][lab], m[m.group == 'generic_pullback'][lab]
        if len(dz) > 10 and len(gp) > 10:
            se = np.sqrt(dz.var(ddof=1) / len(dz) + gp.var(ddof=1) / len(gp))
            t = (dz.mean() - gp.mean()) / se if se > 0 else 0.0
            print(f'  gap (discount - generic): {dz.mean()-gp.mean():+.3f}   t≈{t:+.2f}   '
                  f'({"|t|>2 = worth a second look" if abs(t) > 2 else "not distinguishable from noise"})')

    print(f'\n  Per-year mean fwd12, discount_zone vs generic_pullback (must hold up per year,')
    print(f'  not just in total — same discipline as every other lab in this program):')
    years = sorted(m.year.unique())
    for grp in ('discount_zone', 'generic_pullback'):
        line = f'  {grp:<18}'
        for y in years:
            g = m[(m.group == grp) & (m.year == y)]
            line += f'  {y}:{g["fwd12"].mean():+.2f}n{len(g)}' if len(g) else f'  {y}:· '
        print(line)

    tr, te = m[m.year <= TRAIN_END], m[m.year > TRAIN_END]
    for label, g in (('TRAIN 2021-23', tr), ('TEST 2024-26', te)):
        dz, gp = g[g.group == 'discount_zone']['fwd12'], g[g.group == 'generic_pullback']['fwd12']
        print(f'\n  {label}: discount n={len(dz)} mean={dz.mean():+.3f}   '
              f'generic n={len(gp)} mean={gp.mean():+.3f}   gap={dz.mean()-gp.mean():+.3f}')
    print()


def main():
    m = build()
    out = os.path.join(ROOT, 'futures', 'factory', '_ict_discount_signals.csv')
    m.to_csv(out, index=False)
    print(f'  saved {len(m)} signals -> {out}')
    report(m)


if __name__ == '__main__':
    main()
