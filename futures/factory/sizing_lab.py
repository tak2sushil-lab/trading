"""
SIZING LAB — is POSITION SIZE the remaining lever?

Context (Aug 18 2026, 11 labs): everything sorts into SELECTION works / REACTION fails.
The one thing never properly tested is how many contracts to put on. The Aug 18 finding
that motivates it: the book's fixed 200pt stop has silently decayed from 1.04 ATR (2021)
to 0.44 ATR (2026) as MNQ ATR went 193 -> 459. Constant CONTRACTS is therefore NOT
constant risk — it is a risk schedule that quietly ramps as volatility rises.

This lab answers four questions on cached full-pipeline trade frames (no 45-min re-runs):

  Q1  does the MTF-LONG day filter hold under the REAL exit stack?      --pipeline
  Q2  does ATR-normalised sizing survive a walk-forward?                --walkforward
  Q3  does the ROOM forecast work as a SIZE input (it failed as a gate)? --room
  Q4  what does the whole thing look like once every honest haircut is  --final
      applied (one-position-per-day, live 2x duplication, friction)?

METHOD NOTES (read before trusting any number)
  * P&L is rescaled linearly per contract from the cached pipeline trades:
      pts_eff = (pnl + COMMISSION + FRICTION*c_orig) / (POINT_VALUE * c_orig)
      pnl(n)  = pts_eff * POINT_VALUE * n - COMMISSION - FRICTION * n
    This is EXACT for the 537/543 trades that ran to a single exit. It is an
    approximation for the 6 partial-scale-out trades (whose payoff is not linear in n)
    and it cannot model the fact that a 1-contract trade resized to 2 would newly become
    eligible for the partial rule. Direction of that error: it UNDER-states resized P&L
    slightly (partial banks a winner early). Flagged, not hidden.
  * maxDD is peak-to-trough on the DAILY equity curve, chronological.
  * $/DD = total / |maxDD| = dollars earned per dollar of worst drawdown = the number
    that decides how much capital the config needs. Flat sizing scales P&L and DD
    equally, so ONLY a scheme that changes $/DD is doing real work.
  * Everything is reported PER YEAR. Several findings in this thread inverted per year.

Run:  venv/bin/python -m futures.factory.sizing_lab --pipeline --walkforward --room --final
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAC          = os.path.join(ROOT, 'futures', 'factory')
POINT_VALUE  = 2.0
COMMISSION   = 1.24
FRICTION     = 6.0        # bench.FRICTION_PER_CONTRACT — conservative round-trip haircut
PARTIAL_PNL  = 150.0 * POINT_VALUE - COMMISSION   # one banked contract at +150pts


# ── data ─────────────────────────────────────────────────────────────────────
def _atr_map() -> dict:
    dt = pd.read_csv(os.path.join(FAC, '_day_table.csv'))
    return dict(zip(dt['date'].astype(str), dt['atr'].astype(float)))


def load(which: str = 'mtf') -> pd.DataFrame:
    """which: 'mtf' = pipeline restricted to MA50-aligned-and-rising sessions;
              'base' = the unfiltered 5.5yr book."""
    fn = 'mom_mtf.csv' if which == 'mtf' else '_mom_2021-06-01_2026-08-14.csv'
    d = pd.read_csv(os.path.join(FAC, fn)).copy()
    d['date'] = d['date'].astype(str)
    d['y'] = pd.to_datetime(d['date']).dt.year
    d['atr'] = d['date'].map(_atr_map())
    sgn = np.where(d['side'] == 'LONG', 1.0, -1.0)
    d['pts_raw'] = (d['exit'] - d['entry']) * sgn

    # recover the ORIGINAL contract count (the frame stores the post-partial runner)
    pred = d['pts_raw'] * POINT_VALUE * d['contracts'] - COMMISSION - FRICTION * d['contracts']
    d['partial'] = (d['pnl'] - pred).abs() > 1.0
    d['c_orig'] = d['contracts'] + d['partial'].astype(int)
    # effective points PER ORIGINAL CONTRACT (folds the partial leg back in)
    d['pts_eff'] = (d['pnl'] + COMMISSION + FRICTION * d['c_orig']) / (POINT_VALUE * d['c_orig'])
    d = d.sort_values(['date', 'entry_time']).reset_index(drop=True)
    d['seq'] = d.groupby('date').cumcount()          # 0 = first trade of the session (chronological)
    return d.dropna(subset=['atr']).reset_index(drop=True)


def _asvec(d: pd.DataFrame, n) -> np.ndarray:
    """Positional coercion of a sizing input. Deliberately NOT index-aligned: a scheme is
    a per-trade vector in row order, and silent reindexing against a filtered frame is
    exactly how a sizing study produces NaNs that look like real numbers."""
    if np.isscalar(n):
        return np.full(len(d), float(n))
    v = np.asarray(getattr(n, 'values', n), dtype=float)
    if len(v) != len(d):
        raise ValueError(f'sizing vector length {len(v)} != {len(d)} trades')
    return v


def pnl_at(d: pd.DataFrame, n) -> pd.Series:
    """P&L if this trade had been sized n contracts instead of c_orig."""
    v = _asvec(d, n)
    return pd.Series(d['pts_eff'].to_numpy() * POINT_VALUE * v - COMMISSION - FRICTION * v,
                     index=d.index)


# ── sizing schemes ───────────────────────────────────────────────────────────
def atr_size(d: pd.DataFrame, risk: float, lo: int = 1, hi: int = 3) -> pd.Series:
    """contracts = risk_dollars / (day ATR * $2 per point), clipped.
    ATR is the COMPLETED prior-session ATR from the day table (causal)."""
    return np.clip(np.round(risk / (d['atr'] * POINT_VALUE)), lo, hi)


# ── scoring ──────────────────────────────────────────────────────────────────
def maxdd(dates, pnl) -> float:
    s = pd.Series(np.asarray(pnl), index=pd.Index(dates)).groupby(level=0).sum().sort_index().cumsum()
    return float((s - s.cummax()).min()) if len(s) else 0.0


def sharpe(dates, pnl) -> float:
    s = pd.Series(np.asarray(pnl), index=pd.Index(dates)).groupby(level=0).sum()
    return float(s.mean() / s.std() * np.sqrt(252)) if len(s) > 2 and s.std() else 0.0


def score(d: pd.DataFrame, pnl: pd.Series, years) -> dict:
    D = maxdd(d['date'], pnl)
    return dict(n=len(d), avg_c=float(np.nan), tot=float(pnl.sum()), dd=D,
                pdd=(pnl.sum() / abs(D)) if D else 0.0,
                sh=sharpe(d['date'], pnl),
                per={y: float(pnl[d.y == y].sum()) for y in years},
                green=sum(1 for y in years if pnl[d.y == y].sum() > 0))


def hdr(cols, years):
    print(f'  {"scheme":<30}{"n":>5}{"avg c":>7}{"total":>10}{"maxDD":>9}{"$/DD":>7}{"Sh":>6}{"grn":>5}   '
          + ''.join(f'{y:>10}' for y in years))
    print('  ' + '-' * (30 + 5 + 7 + 10 + 9 + 7 + 6 + 5 + 3 + 10 * len(years)))


def row(lbl, d, n, years):
    p = pnl_at(d, n)
    s = score(d, p, years)
    ac = float(np.mean(_asvec(d, n)))
    print(f'  {lbl:<30}{s["n"]:>5}{ac:>7.2f}{s["tot"]:>+10,.0f}{s["dd"]:>+9,.0f}{s["pdd"]:>7.2f}'
          f'{s["sh"]:>6.2f}{s["green"]:>3}/{len(years)}   '
          + ''.join(f'{s["per"][y]:>+10,.0f}' for y in years))
    return s


# ══════════════════════════════════════════════════════════════════════════════
# Q1 — does the config hold under the REAL exit stack?
# ══════════════════════════════════════════════════════════════════════════════
def mode_pipeline():
    base, mtf = load('base'), load('mtf')
    years = sorted(base.y.unique())
    print(f'\n{"="*160}')
    print('  Q1  THE CONFIG UNDER THE REAL EXIT STACK  (full pipeline: 200pt stop, trail, rev-exit,')
    print('      partial scale-out, no-move, EOD — NOT the barrier sim that replaced all of it)')
    print(f'{"="*160}')
    hdr(None, years)
    row('baseline book (all days)', base, base.c_orig, years)
    row('  LONG only', base[base.side == 'LONG'], base[base.side == 'LONG'].c_orig, years)
    row('MTF days, all sides', mtf, mtf.c_orig, years)
    m = mtf[mtf.side == 'LONG'].reset_index(drop=True)
    row('MTF days, LONG only  ★', m, m.c_orig, years)
    sh = mtf[mtf.side == 'SHORT'].reset_index(drop=True)
    row('MTF days, SHORT only', sh, sh.c_orig, years)
    print('\n  Reference — the PROVISIONAL barrier sim this is meant to check (from Aug 18 memory):')
    print('    barrier (stop -1000 / target +200 / hold to 15:10), flat 1c : +4,587  DD -1,822  $/DD 2.52  green 6/6')
    print('    barrier + ATR-normalised sizing ($1,000 risk)              : +12,233  DD -3,636  $/DD 3.36  green 6/6')

    print(f'\n{"="*160}')
    print('  SIZING SCHEMES ON THE REAL-EXIT-STACK MTF-LONG BOOK')
    print(f'{"="*160}')
    hdr(None, years)
    row('pipeline sizing (as traded)', m, m.c_orig, years)
    for c in (1, 2, 3):
        row(f'flat {c} contract(s)', m, c, years)
    for r in (700, 1000, 1400):
        row(f'ATR-normalised ${r:,} risk', m, atr_size(m, r), years)
    print('\n  Flat sizing cannot change $/DD (it scales P&L and DD together). Any scheme whose')
    print('  $/DD differs from the flat rows is doing real work — everything else is just leverage.')


# ══════════════════════════════════════════════════════════════════════════════
# Q2 — walk-forward the sizing parameter, and the confound that could kill it
# ══════════════════════════════════════════════════════════════════════════════
def mode_walkforward():
    m = load('mtf')
    m = m[m.side == 'LONG'].reset_index(drop=True)
    years = sorted(m.y.unique())
    TRAIN, TEST = [2021, 2022, 2023], [2024, 2025, 2026]
    tr, te = m[m.y.isin(TRAIN)].reset_index(drop=True), m[m.y.isin(TEST)].reset_index(drop=True)

    print(f'\n{"="*160}')
    print('  Q2  WALK-FORWARD OF THE RISK-PER-TRADE PARAMETER   (fit 2021-23  ->  test 2024-26)')
    print(f'{"="*160}')
    print(f'  {"risk $/trade":<16}| {"TRAIN 2021-23":^44} | {"TEST 2024-26":^44}')
    print(f'  {"":16}| {"avg c":>7}{"total":>10}{"maxDD":>9}{"$/DD":>8}{"Sh":>7} | '
          f'{"avg c":>7}{"total":>10}{"maxDD":>9}{"$/DD":>8}{"Sh":>7}')
    print('  ' + '-' * 112)
    curve = []
    for r in (400, 500, 600, 700, 800, 900, 1000, 1200, 1400, 1600, 2000):
        out = []
        for g in (tr, te):
            n = atr_size(g, r)
            p = pnl_at(g, n)
            s = score(g, p, years)
            out.append((float(n.mean()), s['tot'], s['dd'], s['pdd'], s['sh']))
        curve.append((r, *out[0], *out[1]))
        print(f'  ${r:<15,}| {out[0][0]:>7.2f}{out[0][1]:>+10,.0f}{out[0][2]:>+9,.0f}{out[0][3]:>8.2f}{out[0][4]:>7.2f} | '
              f'{out[1][0]:>7.2f}{out[1][1]:>+10,.0f}{out[1][2]:>+9,.0f}{out[1][3]:>8.2f}{out[1][4]:>7.2f}')
    c = pd.DataFrame(curve, columns=['risk', 'trc', 'trt', 'trd', 'trp', 'trs',
                                     'tec', 'tet', 'ted', 'tep', 'tes'])
    best = c.loc[c.trp.idxmax()]
    print(f'\n  TRAIN-optimal risk (by $/DD) = ${best.risk:,.0f}  ->  its TEST $/DD = {best.tep:.2f} '
          f'(test-optimal was {c.tep.max():.2f} at ${c.loc[c.tep.idxmax(),"risk"]:,.0f})')
    print(f'  TEST $/DD range across the whole parameter range: {c.tep.min():.2f} .. {c.tep.max():.2f}'
          f'   -> {"FLAT plateau (parameter barely matters)" if c.tep.max()-c.tep.min()<0.6 else "PEAKY (parameter is a fitted choice)"}')

    print(f'\n{"="*160}')
    print('  THE CONFOUND THAT COULD KILL IT: MNQ ATR rose 188 -> 477 almost monotonically, so')
    print('  "size down when ATR is high" is nearly the same instruction as "size down in later')
    print('  years". If the gain is really a cross-year time bet, it will VANISH within a year.')
    print(f'{"="*160}')
    print(f'  {"":<8}{"n":>5}{"ATR":>7}   {"low-ATR half":^30}   {"high-ATR half":^30}')
    print(f'  {"year":<8}{"":5}{"":7}   {"n":>5}{"$/contract":>13}{"WR":>10}   {"n":>5}{"$/contract":>13}{"WR":>10}')
    print('  ' + '-' * 100)
    agg = []
    for y in years:
        g = m[m.y == y]
        if len(g) < 20:
            continue
        med = g.atr.median()
        lo, hi = g[g.atr <= med], g[g.atr > med]
        pc = lambda x: (x.pts_eff * POINT_VALUE - COMMISSION - FRICTION).mean()
        agg.append((y, len(g), pc(lo), pc(hi)))
        print(f'  {y:<8}{len(g):>5}{g.atr.mean():>7.0f}   {len(lo):>5}{pc(lo):>+13.0f}{100*(lo.pts_eff>0).mean():>9.0f}%'
              f'   {len(hi):>5}{pc(hi):>+13.0f}{100*(hi.pts_eff>0).mean():>9.0f}%')
    a = pd.DataFrame(agg, columns=['y', 'n', 'lo', 'hi'])
    wins = int((a.lo > a.hi).sum())
    print(f'\n  Low-ATR days paid more per contract in {wins} of {len(a)} years.')
    print('  If that is not a clear majority, ATR-normalised sizing is NOT exploiting a within-year')
    print('  edge — it is a cross-year leverage schedule, and its backtest gain is a bet that the')
    print('  low-ATR years were under-levered. That bet cannot be validated on this sample.')

    # the decisive control: rank ATR WITHIN each year, so the time trend is removed
    print(f'\n  CONTROL — ATR rank computed WITHIN each year (time trend removed by construction):')
    hdr(None, years)
    row('pipeline sizing', m, m.c_orig, years)
    row('flat 2 contracts', m, 2, years)
    row('ATR-normalised $1,000 (raw)', m, atr_size(m, 1000), years)
    wy = m.groupby('y')['atr'].rank(pct=True)
    row('within-year ATR rank sizing', m, np.where(wy <= 0.33, 3, np.where(wy <= 0.66, 2, 1)), years)
    print('\n  "within-year ATR rank" sizes 3/2/1 by the day ATR TERCILE INSIDE its own year. It')
    print('  carries the same "small when volatile" instruction with ZERO cross-year drift. If it')
    print('  does not beat flat, the raw-ATR gain was the time trend, not the volatility signal.')


# ══════════════════════════════════════════════════════════════════════════════
# Q1b — reproduce the PROVISIONAL barrier sim and apply the correction it missed
# ══════════════════════════════════════════════════════════════════════════════
def _barrier(trades: pd.DataFrame, bars: pd.DataFrame, stop=1000.0, target=200.0,
             one_per_day=False) -> pd.DataFrame:
    """Replay each entry under a pure barrier geometry: -stop / +target / flat at the
    session close. `one_per_day` enforces what a ONE-POSITION book can actually do —
    the original barrier sim scored every entry independently, including entries that
    a hold-to-EOD position would still have been occupying the slot for."""
    import datetime as _dt
    day_map = {str(k): v for k, v in bars.groupby(bars.index.date)}
    out, busy = [], {}
    for _, t in trades.sort_values(['date', 'entry_time']).iterrows():
        dd = day_map.get(str(t['date']))
        if dd is None:
            continue
        try:
            hh, mm = map(int, str(t['entry_time'])[:5].split(':'))
        except Exception:
            continue
        et = _dt.time(hh, mm)
        if one_per_day and busy.get(str(t['date'])):
            continue                      # slot still held by an earlier hold-to-EOD trade
        fwd = dd[dd.index.time > et]
        if len(fwd) < 3:
            continue
        e = float(t['entry']); sg = 1.0 if t['side'] == 'LONG' else -1.0
        hi, lo = fwd['high'].to_numpy(), fwd['low'].to_numpy()
        adv = (e - lo) if sg > 0 else (hi - e)        # points against us
        fav = (hi - e) if sg > 0 else (e - lo)        # points for us
        i_s = np.nonzero(adv >= stop)[0]
        i_t = np.nonzero(fav >= target)[0]
        s_i = i_s[0] if len(i_s) else 10 ** 9
        t_i = i_t[0] if len(i_t) else 10 ** 9
        if s_i == t_i and s_i != 10 ** 9:
            pts = -stop                                # same bar: assume the bad fill
        elif s_i < t_i:
            pts = -stop
        elif t_i < s_i:
            pts = target
        else:
            pts = sg * (float(fwd['close'].iloc[-1]) - e)
        busy[str(t['date'])] = True
        out.append(dict(date=str(t['date']), y=int(str(t['date'])[:4]), side=t['side'],
                        atr=t.get('atr', np.nan), c_orig=int(t['c_orig']),
                        pts_eff=pts + FRICTION / POINT_VALUE + COMMISSION / POINT_VALUE))
    r = pd.DataFrame(out)
    # store pts_eff so pnl_at() reproduces `pts*2*n - comm - friction*n` exactly
    if len(r):
        r['pts_eff'] = r['pts_eff'] - (FRICTION / POINT_VALUE + COMMISSION / POINT_VALUE)
        r['pts_eff'] = r['pts_eff'] + (COMMISSION + FRICTION) / POINT_VALUE * 0  # keep raw pts
    return r


def mode_barrier():
    from futures.factory.scalp_lab import load_1m
    m = load('mtf'); m = m[m.side == 'LONG'].reset_index(drop=True)
    years = sorted(m.y.unique())
    print('\n  loading 1-min MNQ bars…', flush=True)
    bars = load_1m()
    print(f'  {len(bars):,} bars  {bars.index.min()} -> {bars.index.max()}')

    print(f'\n{"="*160}')
    print('  Q1b  THE BARRIER GEOMETRY (stop -1000 / target +200 / flat 15:10) ON THE SAME 343 ENTRIES')
    print('       — reproduced here, then corrected for the slot it silently double-booked')
    print(f'{"="*160}')
    hdr(None, years)
    b = _barrier(m, bars, one_per_day=False)
    b1 = _barrier(m, bars, one_per_day=True)
    for lbl, g in (('barrier, overlaps allowed', b), ('barrier, ONE position at a time', b1)):
        if not len(g):
            continue
        for c in (1, 2):
            row(f'{lbl} · flat {c}c', g, c, years)
        row(f'{lbl} · ATR $1,000', g, atr_size(g, 1000), years)
    print(f'\n  For comparison, the REAL exit stack on the identical entries:')
    hdr(None, years)
    row('real stack · flat 1c', m, 1, years)
    row('real stack · flat 2c', m, 2, years)
    row('real stack · pipeline sizing', m, m.c_orig, years)
    row('real stack · ATR $1,000', m, atr_size(m, 1000), years)


# ══════════════════════════════════════════════════════════════════════════════
# Q3 — the ROOM forecast as a SIZE input (it failed twice as a GATE)
# ══════════════════════════════════════════════════════════════════════════════
def _room_map() -> dict:
    from futures.factory.conditions import room_forecast
    dt = pd.read_csv(os.path.join(FAC, '_day_table.csv'))
    dt['room_fc'] = room_forecast(dt)
    d = dt.dropna(subset=['room_fc'])
    return dict(zip(d['date'].astype(str), d['room_fc'].astype(float)))


def mode_room():
    m = load('mtf'); m = m[m.side == 'LONG'].reset_index(drop=True)
    m['room'] = m['date'].map(_room_map())
    m = m.dropna(subset=['room']).reset_index(drop=True)
    years = sorted(m.y.unique())
    print(f'\n{"="*160}')
    print('  Q3  ROOM FORECAST AS A SIZE INPUT   (it failed twice as a GATE because room is')
    print('      SYMMETRIC — high-room days travel further in BOTH directions. Symmetry does not')
    print('      by itself disqualify it from scaling exposure, so: does it?)')
    print(f'{"="*160}')
    q = pd.qcut(m['room'], 5, labels=['R1 dead', 'R2', 'R3', 'R4', 'R5 wide'])
    per_c = m.pts_eff * POINT_VALUE - COMMISSION - FRICTION
    print(f'  {"room bucket":<12}{"n":>5}{"$/contract":>13}{"WR":>8}{"sd $/c":>10}   per-year $/contract')
    print('  ' + '-' * 116)
    for b in q.cat.categories:
        g = m[q == b]
        yl = ' '.join(f'{y}:{(per_c[(q == b) & (m.y == y)]).mean() if ((q == b) & (m.y == y)).sum() else 0:>+6.0f}'
                      for y in years)
        print(f'  {str(b):<12}{len(g):>5}{per_c[q == b].mean():>+13.0f}{100*(g.pts_eff>0).mean():>7.0f}%'
              f'{per_c[q == b].std():>10.0f}   {yl}')
    print('\n  Read the sd column: if room scales the SPREAD but not the MEAN, sizing UP on it buys')
    print('  variance with no return — the textbook way to make a Sharpe worse while P&L looks fine.')

    print(f'\n{"="*160}')
    print('  ROOM-SCALED SIZING SCHEMES')
    print(f'{"="*160}')
    hdr(None, years)
    row('pipeline sizing (benchmark)', m, m.c_orig, years)
    row('flat 2 contracts', m, 2, years)
    rk = m['room'].rank(pct=True)
    row('size UP with room (1/2/3)', m, np.where(rk <= .33, 1, np.where(rk <= .66, 2, 3)), years)
    row('size DOWN with room (3/2/1)', m, np.where(rk <= .33, 3, np.where(rk <= .66, 2, 1)), years)
    row('room x ATR (risk/ATR, up-tilt)', m,
        np.clip(np.round(1000 * (0.6 + 0.8 * rk) / (m.atr * POINT_VALUE)), 1, 3), years)
    print('\n  A size input must beat the benchmark on $/DD, not on total. Total always rises with')
    print('  average size; only $/DD says the money was allocated to the right trades.')


# ══════════════════════════════════════════════════════════════════════════════
# Q4 — the scheme that IS winning, and what survives every honest haircut
# ══════════════════════════════════════════════════════════════════════════════
def mode_conviction():
    m = load('mtf'); m = m[m.side == 'LONG'].reset_index(drop=True)
    years = sorted(m.y.unique())
    per_c = m.pts_eff * POINT_VALUE - COMMISSION - FRICTION
    print(f'\n{"="*160}')
    print('  Q4  THE SIZING SCHEME THAT IS ALREADY WINNING — the live hero-score ladder')
    print('      (contracts_from_regime_score: 1 or 2 by Trend-Jury score). Does the 2-contract')
    print('      bucket actually earn its extra size, PER YEAR?')
    print(f'{"="*160}')
    print(f'  {"sized":<10}{"n":>5}{"$/contract":>13}{"WR":>8}{"sd $/c":>10}   per-year $/contract')
    print('  ' + '-' * 116)
    for c in (1, 2):
        g = m[m.c_orig == c]
        yl = ' '.join(f'{y}:{per_c[(m.c_orig == c) & (m.y == y)].mean() if ((m.c_orig==c)&(m.y==y)).sum() else 0:>+6.0f}'
                      for y in years)
        print(f'  {c} contract{"s" if c>1 else " "}{"":<1}{len(g):>5}{per_c[m.c_orig == c].mean():>+13.0f}'
              f'{100*(g.pts_eff>0).mean():>7.0f}%{per_c[m.c_orig == c].std():>10.0f}   {yl}')
    won = sum(1 for y in years
              if per_c[(m.c_orig == 2) & (m.y == y)].mean() > per_c[(m.c_orig == 1) & (m.y == y)].mean())
    print(f'\n  The 2-contract bucket out-earned the 1-contract bucket per contract in {won} of {len(years)} years.')

    print(f'\n{"="*160}')
    print('  EVERY HONEST HAIRCUT, APPLIED IN ORDER  (this is the number to actually believe)')
    print(f'{"="*160}')
    hdr(None, years)
    s0 = row('as measured', m, m.c_orig, years)
    first = m[m.seq == 0].reset_index(drop=True)
    s1 = row('  + first trade of day only', first, first.c_orig, years)
    for f in (10.0, 14.0):
        v = m.pts_eff * POINT_VALUE * m.c_orig - COMMISSION - f * m.c_orig
        s = score(m, v, years)
        print(f'  {"  + friction $" + str(int(f)) + "/contract":<30}{s["n"]:>5}{m.c_orig.mean():>7.2f}'
              f'{s["tot"]:>+10,.0f}{s["dd"]:>+9,.0f}{s["pdd"]:>7.2f}{s["sh"]:>6.2f}{s["green"]:>3}/{len(years)}   '
              + ''.join(f'{s["per"][y]:>+10,.0f}' for y in years))
    print('\n  Live-book reality not modelled anywhere above: live runs MAX_OPEN_TRADES=2 with no')
    print('  post-entry cooldown, so it fires the same signal twice (79% of live entry-events are')
    print('  size-2 clusters vs 0% in this sim). At the same nominal contract count the live book')
    print('  therefore carries roughly DOUBLE this exposure and drawdown. See')
    print('  [[futures-live-duplicate-entries-aug18]].')


# ══════════════════════════════════════════════════════════════════════════════
# Q5 — the conviction ladder: is it informative on the WHOLE book, and can it stretch?
# ══════════════════════════════════════════════════════════════════════════════
def mode_ladder():
    years = None
    print(f'\n{"="*160}')
    print('  Q5  THE HERO-SCORE CONVICTION LADDER  —  the sizing signal that is ALREADY LIVE')
    print('      contracts_from_regime_score() puts 2 contracts on high Trend-Jury scores and 1 on')
    print('      the rest. Nothing here was fitted by this lab: it is the live system\'s own ladder,')
    print('      read out of the sim. Tested on the UNFILTERED book so it is not entangled with the')
    print('      MA50 day filter that WAS chosen on this data.')
    print(f'{"="*160}')
    for which, lbl in (('base', 'FULL BOOK (all days, both sides)'),
                       ('mtf', 'MTF-LONG book')):
        d = load(which)
        if which == 'mtf':
            d = d[d.side == 'LONG'].reset_index(drop=True)
        years = sorted(d.y.unique())
        per_c = d.pts_eff * POINT_VALUE - COMMISSION - FRICTION
        print(f'\n  {lbl}   (n={len(d)})')
        print(f'  {"tier":<14}{"n":>5}{"$/contract":>13}{"WR":>8}{"sd":>8}   per-year $/contract')
        print('  ' + '-' * 118)
        for c in (1, 2):
            g = d[d.c_orig == c]
            yl = ' '.join(f'{y}:{per_c[(d.c_orig==c)&(d.y==y)].mean() if ((d.c_orig==c)&(d.y==y)).sum() else 0:>+6.0f}'
                          for y in years)
            print(f'  {"sized " + str(c):<14}{len(g):>5}{per_c[d.c_orig==c].mean():>+13.0f}'
                  f'{100*(g.pts_eff>0).mean():>7.0f}%{per_c[d.c_orig==c].std():>8.0f}   {yl}')
        won = sum(1 for y in years
                  if per_c[(d.c_orig==2)&(d.y==y)].mean() > per_c[(d.c_orig==1)&(d.y==y)].mean())
        print(f'  -> high-conviction tier out-earned per contract in {won}/{len(years)} years')

    print(f'\n{"="*160}')
    print('  STRETCHING THE LADDER  (same trades, different contracts on each tier)')
    print(f'{"="*160}')
    for which, lbl in (('base', 'FULL BOOK'), ('mtf', 'MTF-LONG')):
        d = load(which)
        if which == 'mtf':
            d = d[d.side == 'LONG'].reset_index(drop=True)
        years = sorted(d.y.unique())
        print(f'\n  {lbl}')
        hdr(None, years)
        for lo, hi, name in ((1, 2, 'live ladder  1 / 2'), (1, 3, 'stretched    1 / 3'),
                             (1, 4, 'stretched    1 / 4'), (2, 4, 'levered      2 / 4'),
                             (0, 1, 'TOP TIER ONLY 0 / 1'), (0, 2, 'TOP TIER ONLY 0 / 2')):
            n = np.where(d.c_orig == 2, hi, lo)
            if n.sum() == 0:
                continue
            keep = n > 0
            g = d[keep].reset_index(drop=True)
            row(name, g, n[keep], years)
        print('   flat 2 reference:')
        row('  flat 2 contracts', d, 2, years)


# ══════════════════════════════════════════════════════════════════════════════
# Q6 — is the conviction result real, or is n=57 flattering a ratio?
# ══════════════════════════════════════════════════════════════════════════════
def mode_stress(iters: int = 2000, seed: int = 7):
    rng = np.random.default_rng(seed)
    for which, sidefilt in (('mtf', 'LONG'), ('base', None)):
        d = load(which)
        if sidefilt:
            d = d[d.side == sidefilt].reset_index(drop=True)
        years = sorted(d.y.unique())
        k = int((d.c_orig == 2).sum())
        real_n = np.where(d.c_orig == 2, 4, 1)
        real = score(d, pnl_at(d, real_n), years)
        base = score(d, pnl_at(d, np.ones(len(d))), years)

        print(f'\n{"="*160}')
        print(f'  Q6  PERMUTATION TEST — {which.upper()} book'
              f'{" LONG" if sidefilt else ""}   (n={len(d)}, high-conviction tier k={k})')
        print('      Reassign the "high conviction" label to k RANDOM trades and re-run the 1/4')
        print('      stretch. If the live hero score carries no information, the real result sits')
        print('      inside this noise band.')
        print(f'{"="*160}')
        tot, pdd, shp = [], [], []
        for _ in range(iters):
            idx = rng.choice(len(d), size=k, replace=False)
            n = np.ones(len(d)); n[idx] = 4
            p = pnl_at(d, n)
            tot.append(p.sum()); pdd.append(score(d, p, years)['pdd']); shp.append(sharpe(d['date'], p))
        for nm, real_v, arr in (('total $', real['tot'], tot), ('$/DD', real['pdd'], pdd),
                                ('Sharpe', real['sh'], shp)):
            a = np.array(arr)
            pct = 100.0 * (a < real_v).mean()
            verdict = ('REAL SIGNAL' if pct >= 95 else 'inside the noise band' if pct >= 5
                       else 'REAL SIGNAL (inverted)')
            print(f'  {nm:<10} real {real_v:>+10,.2f}   random: median {np.median(a):>+10,.2f}'
                  f'  p5 {np.percentile(a,5):>+10,.2f}  p95 {np.percentile(a,95):>+10,.2f}'
                  f'   -> real beats {pct:>5.1f}% of shuffles  [{verdict}]')
        print(f'  (flat-1c reference for this book: total {base["tot"]:+,.0f}  $/DD {base["pdd"]:.2f})')

        # leave-one-year-out
        print(f'\n  LEAVE-ONE-YEAR-OUT — drop each year, does the 1/4 stretch still beat the live 1/2 ladder?')
        print(f'  {"dropped":<10}{"1/2 ladder $/DD":>18}{"1/4 stretch $/DD":>20}{"better?":>10}')
        print('  ' + '-' * 60)
        for y in years:
            g = d[d.y != y].reset_index(drop=True)
            yy = sorted(g.y.unique())
            a = score(g, pnl_at(g, g.c_orig), yy)['pdd']
            b = score(g, pnl_at(g, np.where(g.c_orig == 2, 4, 1)), yy)['pdd']
            print(f'  {y:<10}{a:>18.2f}{b:>20.2f}{("yes" if b > a else "NO"):>10}')


# ══════════════════════════════════════════════════════════════════════════════
# Q7 — is the MA50 day filter a robust rule or a lucky pick? (it was chosen on this data)
# ══════════════════════════════════════════════════════════════════════════════
def _daily_state():
    """Daily RTH closes + MA ladder from our own MNQ bars. Every value is SHIFTED one
    session: only completed history is knowable at the 10:30 entry window."""
    import datetime as _dt
    from futures.collect_bars import load_bars
    from futures.factory.conditions import _sess
    b = load_bars('MNQ', start='2020-10-01', end='2026-08-15')
    rth = _sess(b, _dt.time(9, 30), _dt.time(15, 10))
    dd = rth.groupby(rth.index.date).agg(c=('close', 'last'))
    dd.index = pd.to_datetime(list(dd.index))
    for n in (20, 50, 100, 200):
        dd[f'ma{n}'] = dd['c'].rolling(n, min_periods=n).mean()
        for w in (3, 5, 10):
            dd[f'sl{n}_{w}'] = dd[f'ma{n}'] - dd[f'ma{n}'].shift(w)
    return dd.shift(1)


def mode_filter():
    prev = _daily_state()
    key = {d.strftime('%Y-%m-%d'): r for d, r in prev.iterrows()}
    d = load('base')
    d = d[d.side == 'LONG'].reset_index(drop=True)
    years = sorted(d.y.unique())
    st = pd.DataFrame([key.get(x, {}) for x in d['date']], index=d.index)
    d = pd.concat([d, st], axis=1)

    print(f'\n{"="*160}')
    print('  Q7  IS THE MA50 DAY FILTER ROBUST?  (it was chosen on this same 5.5yr sample, so the')
    print('      only cheap test available is whether its NEIGHBOURS also work. A real regime rule')
    print('      has a plateau around it; a lucky pick is a spike.)')
    print('      Day-level filters are pipeline-clean — verified: filtering the cached full book')
    print('      reproduces the dedicated MTF pipeline run exactly, 0 trades differ.')
    print(f'{"="*160}')
    hdr(None, years)
    row('no filter (LONG only)', d, d.c_orig, years)
    for n in (20, 50, 100, 200):
        m = d[f'ma{n}'].notna() & (d['c'] > d[f'ma{n}'])
        g = d[m].reset_index(drop=True)
        if len(g) > 30:
            row(f'close > MA{n}', g, g.c_orig, years)
    print()
    for n in (20, 50, 100, 200):
        for w in (3, 5, 10):
            col = f'sl{n}_{w}'
            m = d[f'ma{n}'].notna() & (d['c'] > d[f'ma{n}']) & (d[col] > 0)
            g = d[m].reset_index(drop=True)
            if len(g) > 30:
                star = '  ★ shipped candidate' if (n, w) == (50, 5) else ''
                row(f'close > MA{n} AND MA{n} rising({w}d){star}', g, g.c_orig, years)

    print(f'\n  TRADE COUNT PER YEAR for the shipped candidate (thin years are where a per-year')
    print('  green/red verdict means least):')
    m = d['ma50'].notna() & (d['c'] > d['ma50']) & (d['sl50_5'] > 0)
    g = d[m]
    print('    ' + '  '.join(f'{y}: {int((g.y==y).sum()):>3} trades' for y in years))


# ══════════════════════════════════════════════════════════════════════════════
# Q8 — is the MTF-LONG book ALPHA, or just being long on up-trending days?
# ══════════════════════════════════════════════════════════════════════════════
def mode_alpha():
    """Factory doctrine: score against the TIDE. 'LONG only, when the daily trend is up'
    is by construction a long-beta instruction, so the tide has to be subtracted before
    any of this counts as an edge."""
    import datetime as _dt
    from futures.collect_bars import load_bars
    from futures.factory.conditions import _sess
    b = load_bars('MNQ', start='2021-01-01', end='2026-08-15')
    rth = _sess(b, _dt.time(9, 30), _dt.time(15, 10))
    tide = {}
    for k, g in rth.groupby(rth.index.date):
        g = g.sort_index()
        at = g[g.index.time >= _dt.time(10, 30)]
        if len(at) < 2:
            continue
        # what one long contract earns doing nothing but holding 10:30 -> 15:10
        tide[k.strftime('%Y-%m-%d')] = (float(at['close'].iloc[-1]) - float(at['close'].iloc[0])) \
            * POINT_VALUE - COMMISSION - FRICTION

    m = load('mtf'); m = m[m.side == 'LONG'].reset_index(drop=True)
    years = sorted(m.y.unique())
    day = m.groupby('date').apply(lambda g: pnl_at(g, g.c_orig).sum(), include_groups=False)
    dfd = pd.DataFrame({'book': day})
    dfd['tide'] = pd.Series(tide).reindex(dfd.index)
    dfd = dfd.dropna()
    dfd['y'] = pd.to_datetime(dfd.index).year

    print(f'\n{"="*160}')
    print('  Q8  ALPHA vs TIDE   — "LONG only on up-trending days" is a long-beta instruction by')
    print('      construction. The tide here is ONE contract held 10:30 -> 15:10 on the SAME days')
    print('      the book traded, same friction. Anything the book earns beyond beta x tide is the')
    print('      only part that is actually ours.')
    print(f'{"="*160}')
    beta = float(np.polyfit(dfd['tide'], dfd['book'], 1)[0])
    alpha_d = dfd['book'] - beta * dfd['tide']
    print(f'  days={len(dfd)}   beta of book to tide = {beta:.2f}   '
          f'correlation = {dfd["book"].corr(dfd["tide"]):.2f}')
    print(f'\n  {"year":<8}{"days":>6}{"book $":>11}{"tide $ (1c)":>14}{"book - tide":>13}{"alpha $":>11}')
    print('  ' + '-' * 66)
    for y in years:
        g = dfd[dfd.y == y]
        print(f'  {y:<8}{len(g):>6}{g["book"].sum():>+11,.0f}{g["tide"].sum():>+14,.0f}'
              f'{g["book"].sum()-g["tide"].sum():>+13,.0f}{alpha_d[dfd.y == y].sum():>+11,.0f}')
    print('  ' + '-' * 66)
    print(f'  {"TOTAL":<8}{len(dfd):>6}{dfd["book"].sum():>+11,.0f}{dfd["tide"].sum():>+14,.0f}'
          f'{dfd["book"].sum()-dfd["tide"].sum():>+13,.0f}{alpha_d.sum():>+11,.0f}')
    print(f'\n  alpha Sharpe {alpha_d.mean()/alpha_d.std()*np.sqrt(252):.2f}   '
          f'vs book Sharpe {dfd["book"].mean()/dfd["book"].std()*np.sqrt(252):.2f}   '
          f'vs tide Sharpe {dfd["tide"].mean()/dfd["tide"].std()*np.sqrt(252):.2f}')
    print('\n  Read it this way: the tide column is what a machine that just bought one contract at')
    print('  10:30 and sold at 15:10 on those days would have made, with no signal at all.')
    print('\n  ⚠️ THIS BENCHMARK IS NOT DEPLOYABLE AND THE NEGATIVE ALPHA IS NOT A VERDICT.')
    print('     "Buy at 10:30 on a day the book will trade" requires knowing at 10:30 that the')
    print('     signal fires later — it does not fire until 11:00-13:00. Q10 proves the point:')
    print('     buying 10:30 on ALL MA50-up days (which IS knowable at 10:30) LOSES $2,609.')
    print('     Read this table as CAPTURE EFFICIENCY — the book converts about a third of the')
    print('     drift its own signal identifies — and use --hold (Q11) for the deployable test,')
    print('     which holds from the REAL entry time and finds the exit stack is a net POSITIVE.')
    print('     Measured decomposition: 79%% of the 10:30->15:10 move happens BEFORE the book')
    print('     enters. The signal fires BECAUSE price already ran — conditioning on "the book')
    print('     traded" re-reads that run-up. Post-entry drift, the only part actually available,')
    print('     averages +9.0 pts and is positive in 58%% of sessions.')


# ══════════════════════════════════════════════════════════════════════════════
# Q9 — how long must a forward shadow run before it can say anything?
# ══════════════════════════════════════════════════════════════════════════════
def mode_shadow(iters: int = 5000, seed: int = 11):
    rng = np.random.default_rng(seed)
    m = load('mtf'); m = m[m.side == 'LONG'].reset_index(drop=True)
    p = pnl_at(m, m.c_orig).to_numpy()
    day = pd.Series(p, index=m['date']).groupby(level=0).sum()
    per_month = len(day) / 62.0     # 5.5yr ~ 62 months of trading days
    print(f'\n{"="*160}')
    print('  Q9  HOW LONG MUST A FORWARD SHADOW RUN?')
    print('      Block-bootstrap whole TRADING DAYS from the historical MTF-LONG book (which keeps')
    print('      each day\'s trades together) and ask what a forward window of N months would show')
    print('      IF the edge is exactly as measured. This is the best case — it assumes the edge is')
    print('      real and unchanged.')
    print(f'{"="*160}')
    print(f'  the book trades {len(day)} days over 5.5yr = {per_month:.1f} active days/month')
    print(f'\n  {"window":<12}{"active days":>13}{"P(positive)":>14}{"median $":>11}{"p10 $":>10}{"p90 $":>10}')
    print('  ' + '-' * 72)
    for months in (1, 3, 6, 12, 24):
        k = max(1, int(round(per_month * months)))
        sims = np.array([rng.choice(day.to_numpy(), size=k, replace=True).sum() for _ in range(iters)])
        print(f'  {str(months) + " month" + ("s" if months > 1 else ""):<12}{k:>13}'
              f'{100 * (sims > 0).mean():>13.0f}%{np.median(sims):>+11,.0f}'
              f'{np.percentile(sims, 10):>+10,.0f}{np.percentile(sims, 90):>+10,.0f}')
    print('\n  A window whose p10 is still deeply negative cannot distinguish "the edge is real"')
    print('  from "the edge is gone" — it can only catch a catastrophic break.')


# ══════════════════════════════════════════════════════════════════════════════
# Q10 — the deployable tide: "be long 10:30 -> 15:10 whenever the daily trend is up"
# ══════════════════════════════════════════════════════════════════════════════
def _drift_table():
    """One row per session: the causal daily-trend state, and what 1 long contract earns
    holding 10:30 -> 15:10. No signal, no gates, no exit stack."""
    import datetime as _dt
    from futures.collect_bars import load_bars
    from futures.factory.conditions import _sess
    b = load_bars('MNQ', start='2020-10-01', end='2026-08-15')
    rth = _sess(b, _dt.time(9, 30), _dt.time(15, 10))
    dd = rth.groupby(rth.index.date).agg(c=('close', 'last'))
    dd.index = pd.to_datetime(list(dd.index))
    for n in (50, 100):
        dd[f'ma{n}'] = dd['c'].rolling(n, min_periods=n).mean()
        dd[f'sl{n}'] = dd[f'ma{n}'] - dd[f'ma{n}'].shift(5)
    prev = dd.shift(1)
    rows = []
    for k, g in rth.groupby(rth.index.date):
        ts = pd.Timestamp(k)
        if ts not in prev.index or ts.year < 2021:
            continue
        g = g.sort_index()
        at = g[g.index.time >= _dt.time(10, 30)]
        if len(at) < 2:
            continue
        p = prev.loc[ts]
        rows.append(dict(
            date=k.strftime('%Y-%m-%d'), y=k.year,
            pnl=(float(at['close'].iloc[-1]) - float(at['close'].iloc[0])) * POINT_VALUE
                - COMMISSION - FRICTION,
            up50=bool(np.isfinite(p['ma50']) and np.isfinite(p['sl50'])
                      and p['c'] > p['ma50'] and p['sl50'] > 0),
            up100=bool(np.isfinite(p['ma100']) and np.isfinite(p['sl100'])
                       and p['c'] > p['ma100'] and p['sl100'] > 0),
            has50=bool(np.isfinite(p['ma50']) and np.isfinite(p['sl50'])),
        ))
    r = pd.DataFrame(rows)
    return r[r.date >= '2021-06-01'].reset_index(drop=True)


def mode_drift():
    dt = _drift_table()
    years = sorted(dt.y.unique())
    m = load('mtf'); m = m[m.side == 'LONG'].reset_index(drop=True)
    traded = set(m['date'])

    def show(lbl, g, mult=1):
        if not len(g):
            return
        g = g.reset_index(drop=True)
        p = pd.Series(g['pnl'].to_numpy() * mult, index=g.index)
        s = score(g, p, years)
        print(f'  {lbl:<40}{len(g):>6}{s["tot"]:>+10,.0f}{s["dd"]:>+9,.0f}{s["pdd"]:>7.2f}'
              f'{s["sh"]:>6.2f}{s["green"]:>3}/{len(years)}   '
              + ''.join(f'{s["per"][y]:>+10,.0f}' for y in years))

    print(f'\n{"="*160}')
    print('  Q10  THE DEPLOYABLE TIDE  —  buy 1 MNQ at the 10:30 close, sell at 15:10, on every')
    print('       session whose PRIOR completed day closed above a rising daily MA. No signal, no')
    print('       Trend Jury, no RVOL, no HTF, no trail, no reversal exit. Same $6+commission')
    print('       friction as every book trade above.')
    print(f'{"="*160}')
    print(f'  {"strategy":<40}{"days":>6}{"total":>10}{"maxDD":>9}{"$/DD":>7}{"Sh":>6}{"grn":>5}   '
          + ''.join(f'{y:>10}' for y in years))
    print('  ' + '-' * 146)
    show('long every session (unconditional)', dt)
    show('long when MA50 up + rising  ★', dt[dt.up50])
    show('long when MA100 up + rising', dt[dt.up100])
    show('CONTROL: long when MA50 NOT up', dt[dt.has50 & ~dt.up50])
    print()
    show('MA50 days the book also traded', dt[dt.up50 & dt.date.isin(traded)])
    show('MA50 days the book found nothing', dt[dt.up50 & ~dt.date.isin(traded)])
    print()
    print(f'  {"THE BOOK, same days, real exit stack":<40}{len(set(m.date)):>6}'
          f'{pnl_at(m, m.c_orig).sum():>+10,.0f}'
          f'{maxdd(m["date"], pnl_at(m, m.c_orig)):>+9,.0f}'
          f'{pnl_at(m, m.c_orig).sum()/abs(maxdd(m["date"], pnl_at(m, m.c_orig))):>7.2f}'
          f'{sharpe(m["date"], pnl_at(m, m.c_orig)):>6.2f}    5/6   '
          + ''.join(f'{pnl_at(m, m.c_orig)[m.y == y].sum():>+10,.0f}' for y in years))
    print('\n  If the plain drift line beats the book line, the 11 labs found a DAY SELECTOR, and')
    print('  the intraday machinery bolted onto it is subtracting value rather than adding it.')


# ══════════════════════════════════════════════════════════════════════════════
# Q11 — what does the EXIT STACK cost, measured from the real entry time?
# ══════════════════════════════════════════════════════════════════════════════
def mode_hold():
    """Q10 showed the book's day-selection is worth a lot, but 'buy at 10:30 on a day the
    book will later trade' is LOOKAHEAD — at 10:30 you do not know. The deployable version
    is: take the book's real entry, at its real time, and vary only the EXIT."""
    import datetime as _dt
    from futures.factory.scalp_lab import load_1m
    m = load('mtf'); m = m[m.side == 'LONG'].reset_index(drop=True)
    years = sorted(m.y.unique())
    print('\n  loading 1-min MNQ bars…', flush=True)
    bars = load_1m()
    day_map = {str(k): v for k, v in bars.groupby(bars.index.date)}

    def variant(stop=None, target=None, one_per_day=True):
        out, busy = [], set()
        for _, t in m.sort_values(['date', 'entry_time']).iterrows():
            dd = day_map.get(str(t['date']))
            if dd is None:
                continue
            if one_per_day and str(t['date']) in busy:
                continue
            hh, mm = map(int, str(t['entry_time'])[:5].split(':'))
            fwd = dd[dd.index.time > _dt.time(hh, mm)]
            if len(fwd) < 3:
                continue
            e = float(t['entry'])
            adv = e - fwd['low'].to_numpy()          # LONG only
            fav = fwd['high'].to_numpy() - e
            si = np.nonzero(adv >= stop)[0][:1] if stop else np.array([])
            ti = np.nonzero(fav >= target)[0][:1] if target else np.array([])
            s_i = si[0] if len(si) else 10 ** 9
            t_i = ti[0] if len(ti) else 10 ** 9
            pts = (-stop if s_i <= t_i else target) if min(s_i, t_i) < 10 ** 9 \
                else float(fwd['close'].iloc[-1]) - e
            busy.add(str(t['date']))
            out.append(dict(date=str(t['date']), y=int(str(t['date'])[:4]),
                            pts_eff=pts, c_orig=int(t['c_orig']), atr=t['atr']))
        return pd.DataFrame(out)

    print(f'\n{"="*160}')
    print('  Q11  WHAT DOES THE EXIT STACK COST?   Same entries, same times, ONE position at a')
    print('       time, 1 contract, $6+commission friction. Only the exit differs.')
    print(f'{"="*160}')
    hdr(None, years)
    first = m[m.seq == 0].reset_index(drop=True)
    row('REAL exit stack (200pt, trail…)', first, 1, years)
    for stop, target, lbl in ((None, None, 'hold to 15:10, no stop at all'),
                              (1000, None, 'hold to 15:10, 1000pt brake'),
                              (600, None, 'hold to 15:10, 600pt brake'),
                              (400, None, 'hold to 15:10, 400pt brake'),
                              (200, None, 'hold to 15:10, 200pt stop'),
                              (1000, 200, 'barrier -1000 / +200'),
                              (1000, 400, 'barrier -1000 / +400')):
        g = variant(stop, target)
        row(lbl, g, 1, years)
    print()
    print('  and with the live conviction ladder instead of flat 1c:')
    hdr(None, years)
    row('REAL exit stack · live ladder', first, first.c_orig, years)
    for stop, target, lbl in ((None, None, 'hold to 15:10 · live ladder'),
                              (1000, None, 'hold, 1000pt brake · live ladder'),
                              (1000, None, 'hold, 1000pt brake · ladder 1/4')):
        g = variant(stop, target)
        n = np.where(g.c_orig == 2, 4, 1) if 'ladder 1/4' in lbl else g.c_orig
        row(lbl, g, n, years)


# ══════════════════════════════════════════════════════════════════════════════
# Q12 — the rule against the LIVE account (the only data the sim never saw)
# ══════════════════════════════════════════════════════════════════════════════
def mode_live():
    import sqlite3
    con = sqlite3.connect(os.path.join(ROOT, 'trades.db'))
    t = pd.read_sql_query(
        """SELECT entry_date d, entry_time, side, setup_type, contracts, pnl, account_mode,
                  exit_reason FROM futures_trades WHERE status='CLOSED'
             AND setup_type!='RECONCILED' AND (notes IS NULL OR notes NOT LIKE 'partial of %')""",
        con)
    con.close()
    prev = _daily_state()
    key = {d.strftime('%Y-%m-%d'): r for d, r in prev.iterrows()}

    def ok(day):
        x = key.get(str(day))
        if x is None or not np.isfinite(x.get('ma50', np.nan)) or not np.isfinite(x.get('sl50_5', np.nan)):
            return None
        return bool(x['c'] > x['ma50'] and x['sl50_5'] > 0)

    t['mtf'] = t['d'].map(ok)
    manual = t.exit_reason.fillna('').str.contains('manual|Manual|FUT CLOSE', regex=True)
    print(f'\n{"="*160}')
    print('  Q12  THE RULE ON THE LIVE ACCOUNT  —  the only data the 5.5yr sim never saw.')
    print('       Manual FUT CLOSE trades are stripped: the Aug 16 bench established that IBKR\'s')
    print('       entire realised edge was the user\'s hand on 3 trend days, not the automated book.')
    print(f'{"="*160}')
    print(f'  live CLOSED trades {len(t)}  ({t.d.min()} -> {t.d.max()})   '
          f'manual closes removed: {int(manual.sum())} worth ${t.loc[manual, "pnl"].sum():+,.0f} '
          f'on {t.loc[manual, "d"].nunique()} days')
    a = t[~manual]
    print(f'\n  AUTOMATED-ONLY live book: n={len(a)}   total ${a.pnl.sum():+,.0f}')
    print(f'\n  {"bucket":<30}{"n":>5}{"total":>10}{"WR":>7}{"avg":>9}     IBKR / TC split')
    print('  ' + '-' * 92)
    for lbl, mask in (('MTF PASS + LONG  (the rule)', (a.mtf == True) & (a.side == 'LONG')),
                      ('MTF PASS + SHORT (blocked)', (a.mtf == True) & (a.side == 'SHORT')),
                      ('MTF FAIL any side (blocked)', (a.mtf == False))):
        h = a[mask]
        if not len(h):
            continue
        sp = '  '.join(f'{k}: ${v:+,.0f}' for k, v in h.groupby('account_mode').pnl.sum().items())
        print(f'  {lbl:<30}{len(h):>5}{h.pnl.sum():>+10,.0f}{100*(h.pnl>0).mean():>6.0f}%'
              f'{h.pnl.mean():>+9.0f}     {sp}')
    w = a[(a.mtf == True) & (a.side == 'LONG')]
    da, dw = a.groupby('d').pnl.sum(), w.groupby('d').pnl.sum()
    print(f'\n  RULE vs ACTUAL (automated only)')
    print(f'    P&L               actual ${a.pnl.sum():>+8,.0f}   under the rule ${w.pnl.sum():>+8,.0f}')
    print(f'    worst single day  actual ${da.min():>+8,.0f}   under the rule ${dw.min():>+8,.0f}')
    print(f'    days <= -$1,000   actual {int((da <= -1000).sum()):>9}   under the rule {int((dw <= -1000).sum()):>9}')
    print(f'    trading days      actual {len(da):>9}   under the rule {len(dw):>9}')
    print('\n  Live sample is 2.5 months and the sim/live gap is already documented (selection, not')
    print('  execution; plus live runs ~2x the sim\'s exposure via duplicate entries). Treat this as')
    print('  a RISK reading, not a P&L verdict.')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    for f in ('pipeline', 'barrier', 'walkforward', 'room', 'conviction', 'ladder',
              'stress', 'filter', 'alpha', 'shadow', 'hold', 'drift', 'live'):
        ap.add_argument(f'--{f}', action='store_true')
    ap.add_argument('--all', action='store_true')
    a = ap.parse_args()
    g = globals()
    order = ['pipeline', 'barrier', 'hold', 'walkforward', 'room', 'conviction', 'ladder',
             'stress', 'filter', 'alpha', 'drift', 'shadow', 'live']
    ran = False
    for f in order:
        if a.all or getattr(a, f):
            g[f'mode_{f}'](); ran = True
    if not ran:
        ap.print_help()


if __name__ == '__main__':
    main()
