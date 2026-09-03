"""
SCALP LAB — "play small: 1:1, short gains, many trades, out before anything hurts."

User's proposal (Aug 18 2026), two engines:
  A  SCALP   — symmetric 1:1 risk/reward on a 5-10 minute horizon. Target sized to what the
               tape ACTUALLY moves in that window. Out before a reversal can do damage.
  B  SWING   — rare, wide (≈1000pt stop), taken only when the day is genuinely moving and
               unlikely to reverse hard. Pays for the occasional big win.

Why this is not naive, and why it deserves a real test:
  • The live book already scratches most of its trades — 62% of exits are `no_move`, 588
    trades netting +$2,046. The user's read ("we fail in two types: big when the DLL is hit,
    scratch losses where it doesn't hurt") is exactly what the data shows. Engine A is the
    logical conclusion: make EVERY trade scratch-sized and delete the big-loss tail.
  • Engine B is the only design that USES the one thing this whole research program proved
    forecastable: ROOM (expected magnitude), IC +0.41 walk-forward, monotone in every year.
    Direction is unforecastable, magnitude is not — so "only take the wide trade when the
    day has room" is the single validated forecast pointed at the one place it fits.

The trap it must survive: at 1:1 with a symmetric barrier, P(hit +X before −X) is ≈50% for a
driftless random walk BY CONSTRUCTION. So Engine A only works if either (a) our entries carry
real short-horizon directional edge, or (b) the tape has harvestable microstructure. Friction
decides it: at a 20pt target, $6/contract round-trip IS 3 points = 15% of the target. The
break-even hit rate is not 50%, it is 50% + friction/(2·target).

Run: venv/bin/python -m futures.factory.scalp_lab --move     (what does the tape actually give?)
     venv/bin/python -m futures.factory.scalp_lab --barrier  (1:1 on OUR real entries)
     venv/bin/python -m futures.factory.scalp_lab --swing    (wide stop, room-gated)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from futures.collect_bars import load_bars                       # noqa: E402
from futures.factory.conditions import _sess, room_forecast, load_day_table  # noqa: E402

RTH_OPEN, RTH_END = _dt.time(9, 30), _dt.time(15, 10)
DPP = 2.0                      # $ per MNQ point
TRADES = os.path.join(ROOT, 'futures', 'factory', '_mom_2021-06-01_2026-08-14.csv')


def load_1m(start='2021-06-01', end='2026-08-16'):
    d = load_bars('MNQ', start=start, end=end, table='futures_bars_1m')
    d = _sess(d, RTH_OPEN, RTH_END).sort_index()
    if getattr(d.index, 'tz', None) is not None:
        d.index = d.index.tz_localize(None)
    return d


# ── A: what does the tape actually move? ──────────────────────────────────────
def move_report(bars: pd.DataFrame):
    print(f'\n{"="*92}')
    print('  WHAT DOES MNQ ACTUALLY MOVE IN 5-15 MINUTES?  (this sets the achievable target)')
    print(f'{"="*92}')
    print('  {:<10}{:>10}{:>10}{:>10}{:>10}{:>12}'.format(
        'horizon', 'median |Δ|', 'p25', 'p75', 'median range', 'friction as %'))
    print('  ' + '-' * 62)
    for h in (5, 10, 15, 30):
        absmv, rng = [], []
        for _, d in bars.groupby(bars.index.date):
            c = d['close'].to_numpy(); hi = d['high'].to_numpy(); lo = d['low'].to_numpy()
            n = len(c)
            for i in range(0, n - h, 5):
                absmv.append(abs(c[i + h] - c[i]))
                rng.append(hi[i:i + h + 1].max() - lo[i:i + h + 1].min())
        a, r = pd.Series(absmv), pd.Series(rng)
        print('  {:<10}{:>10.0f}{:>10.0f}{:>10.0f}{:>10.0f}{:>11.0f}%'.format(
            f'{h} min', a.median(), a.quantile(.25), a.quantile(.75), r.median(),
            100 * 3.0 / max(a.median(), 1)))    # $6/contract = 3 MNQ pts
    print('\n  "friction as %" = the 3-point ($6/contract) round-trip cost as a share of the')
    print('  median move. That is the tax every scalp pays before it can be right.\n')


# ── B: the 1:1 symmetric-barrier test on OUR real entries ─────────────────────
def barrier_report(bars: pd.DataFrame, friction=6.0):
    tr = pd.read_csv(TRADES)
    day_map = {str(k): v for k, v in bars.groupby(bars.index.date)}
    print(f'\n{"="*100}')
    print('  1:1 SYMMETRIC BARRIER ON OUR OWN ENTRIES   (same signals, scalp geometry)')
    print(f'{"="*100}')
    print('  {:<10}{:>7}{:>10}{:>12}{:>11}{:>12}{:>11}{:>9}'.format(
        'target', 'n', 'hit rate', 'break-even', 'edge', 'net $/trade', 'total $', 'ambig'))
    print('  ' + '-' * 85)
    rows = {}
    for X in (10, 15, 20, 25, 30, 40):
        res = []
        for _, t in tr.iterrows():
            d = day_map.get(str(t['date']))
            if d is None:
                continue
            try:
                hh, mm = map(int, str(t['entry_time'])[:5].split(':'))
            except Exception:
                continue
            fwd = d[d.index.time > _dt.time(hh, mm)]
            if len(fwd) < 3:
                continue
            e = float(t['entry']); sgn = 1 if t['side'] == 'LONG' else -1
            hi = fwd['high'].to_numpy(); lo = fwd['low'].to_numpy()
            fav = (hi - e) * sgn if sgn > 0 else (e - lo)
            adv = (e - lo) if sgn > 0 else (hi - e)
            i_win = np.nonzero(fav >= X)[0]
            i_los = np.nonzero(adv >= X)[0]
            w = i_win[0] if len(i_win) else 10**9
            l = i_los[0] if len(i_los) else 10**9
            tie = (w == l) and w != 10**9      # both barriers inside the SAME 1-min bar:
            #  the fill order is genuinely unknowable from bar data. Counting these as losses
            #  (the earlier convention) biased small targets badly — a single 1-min bar often
            #  spans 10pts BOTH ways. Track them separately and report the unbiased subset.
            if w == l == 10**9:
                pts = sgn * (float(fwd['close'].iloc[-1]) - e)     # neither hit — EOD
            else:
                pts = X if w < l else -X
            res.append(dict(date=t['date'], pts=pts, tie=tie,
                            pnl=pts * DPP * int(t['contracts']) - friction * int(t['contracts'])))
        r = pd.DataFrame(res)
        clean = r[~r.tie]                          # unambiguous resolutions only
        hit = float((clean.pts > 0).mean()) if len(clean) else float('nan')
        be = 0.5 + (friction / DPP) / (2 * X)      # break-even hit rate incl. friction
        rows[X] = r
        print('  {:<10}{:>7}{:>9.1f}%{:>11.1f}%{:>+10.1f}%{:>+12.2f}{:>+11,.0f}{:>9.0f}%'.format(
            f'±{X}pts', len(r), 100 * hit, 100 * be, 100 * (hit - be),
            r.pnl.mean(), r.pnl.sum(), 100 * r.tie.mean()))
    print('\n  break-even = the hit rate a 1:1 trade needs just to pay friction')
    print('  edge = hit rate − break-even. Negative ⇒ the geometry cannot work on these entries.\n')
    best = max(rows, key=lambda k: rows[k].pnl.sum())
    r = rows[best]
    r['y'] = pd.to_datetime(r['date'].astype(str)).dt.year
    print(f'  Best target (±{best}pts) per year: ' +
          ' '.join(f'{y}:{r.loc[r.y == y, "pnl"].sum():+,.0f}' for y in sorted(r.y.unique())))
    print('  ⚠️ this reuses the EXISTING entries. It tests the GEOMETRY, not a new signal.\n')


# ── C: the rare wide-stop swing, gated by the one forecast that works ─────────
def swing_report(bars: pd.DataFrame, friction=6.0):
    """Engine B: wide stop, few trades, taken only when the ROOM forecast says the day
    can actually travel. Room is the ONLY thing this program proved forecastable."""
    day_tbl = load_day_table()
    day_tbl['room_fc'] = room_forecast(day_tbl)
    d = day_tbl.dropna(subset=['room_fc']).copy()
    d['bucket'] = pd.qcut(d['room_fc'], 5, labels=['R1', 'R2', 'R3', 'R4', 'R5']).astype(str)
    room = dict(zip(d['date'].astype(str), d['bucket']))

    tr = pd.read_csv(TRADES)
    tr['bucket'] = tr['date'].astype(str).map(room)
    day_map = {str(k): v for k, v in bars.groupby(bars.index.date)}
    print(f'\n{"="*100}')
    print('  ENGINE B — WIDE STOP, RARE, ROOM-GATED   (hold to EOD; stop is a catastrophe brake)')
    print(f'{"="*100}')
    print('  {:<26}{:>7}{:>9}{:>12}{:>11}   {}'.format(
        'config', 'n', 'WR', 'net $/trade', 'total $', 'per year'))
    print('  ' + '-' * 92)
    for stop in (400, 700, 1000):
        for gate, sel in (('all days', tr),
                          ('room R4+R5 only', tr[tr.bucket.isin(['R4', 'R5'])]),
                          ('room R5 only', tr[tr.bucket == 'R5'])):
            res = []
            for _, t in sel.iterrows():
                dd = day_map.get(str(t['date']))
                if dd is None:
                    continue
                try:
                    hh, mm = map(int, str(t['entry_time'])[:5].split(':'))
                except Exception:
                    continue
                fwd = dd[dd.index.time > _dt.time(hh, mm)]
                if len(fwd) < 3:
                    continue
                e = float(t['entry']); sgn = 1 if t['side'] == 'LONG' else -1
                hi = fwd['high'].to_numpy(); lo = fwd['low'].to_numpy()
                adv = (e - lo) if sgn > 0 else (hi - e)
                hit = np.nonzero(adv >= stop)[0]
                pts = -stop if len(hit) else sgn * (float(fwd['close'].iloc[-1]) - e)
                res.append(dict(date=t['date'], pts=pts,
                                pnl=pts * DPP * int(t['contracts']) - friction * int(t['contracts'])))
            r = pd.DataFrame(res)
            if len(r) < 20:
                continue
            r['y'] = pd.to_datetime(r['date'].astype(str)).dt.year
            per = ' '.join(f'{y}:{r.loc[r.y == y, "pnl"].sum():+,.0f}' for y in sorted(r.y.unique()))
            print('  {:<26}{:>7}{:>8.0f}%{:>+12.2f}{:>+11,.0f}   {}'.format(
                f'{stop}pt stop · {gate}', len(r), 100 * (r.pts > 0).mean(),
                r.pnl.mean(), r.pnl.sum(), per))
    print('\n  Hold-to-EOD with a wide brake = "let the day decide". The room gate is the only')
    print('  validated forecast we have, and this is the one design that actually needs it.\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--move', action='store_true')
    ap.add_argument('--barrier', action='store_true')
    ap.add_argument('--swing', action='store_true')
    a = ap.parse_args()
    bars = load_1m()
    print(f'  MNQ 1-min RTH bars: {len(bars):,}  {bars.index.min()} → {bars.index.max()}')
    if a.move:
        move_report(bars)
    if a.barrier:
        barrier_report(bars)
    if a.swing:
        swing_report(bars)


if __name__ == '__main__':
    main()
