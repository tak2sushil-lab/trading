"""
Engine harness — the DISCOVERY half of the futures factory.

The Proving Ground (bench.py) can already GRADE any trade frame. This module lets us
DEFINE new candidate engines that run over the 5.5yr MNQ bars and emit trades in the
same schema, so a brand-new strategy goes through the identical gauntlet (per-year OOS
+ TopStep P(pass)/P(blow)). That is what "hunting for an engine" means here.

Design rules (anti-overfit, per the equity factory's hard-won lessons):
  • Each engine has ONE fixed parameter set from a PRIOR (a real market mechanism),
    NOT grid-searched per year. We test the SAME params across all 6 years.
  • Decisions use only bars up to and including the signal bar; entry is next-bar open.
  • An engine "passes the all-weather bar" only if it is positive in a MAJORITY of the
    6 years INCLUDING a bad one (2022/2024), and P(pass) >> P(blow) on the gauntlet.

First candidate: STRETCH-FADE (mean-reversion) — fade price stretched >K·ATR from
session VWAP once it shows a reversal bar. Direction-neutral → the theoretical
best shot at regime-independence (also what the Mirror Book hinted at).

Run:  venv/bin/python -m futures.factory.engines --engine stretch_fade --start 2021-06-01 --end 2026-08-14
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

from futures.factory.bench import (scorecard, topstep_report, giveback_report,   # noqa: E402
                                   FRICTION_PER_CONTRACT, DOLLARS_PER_PT_MNQ)

ET = None  # set on load


# ── shared helpers ────────────────────────────────────────────────────────────
def load_rth():
    import futures.sim_replay as srp
    global ET
    ET = srp.ET
    return srp


def session_vwap(day: pd.DataFrame) -> pd.Series:
    tp = (day['high'] + day['low'] + day['close']) / 3.0
    return (tp * day['volume']).cumsum() / day['volume'].cumsum().replace(0, np.nan)


def atr_series(day: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = day['close'].shift(1)
    tr = pd.concat([day['high'] - day['low'],
                    (day['high'] - pc).abs(),
                    (day['low'] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def _simulate_exit(day: pd.DataFrame, i_entry: int, side: str, entry: float,
                   target: float, stop_pts: float):
    """Walk bars from i_entry+1; exit on target/stop/EOD. Returns (exit_price, exit_i, reason)."""
    sgn = 1 if side == 'LONG' else -1
    stop = entry - sgn * stop_pts
    for j in range(i_entry + 1, len(day)):
        hi, lo = day['high'].iloc[j], day['low'].iloc[j]
        # stop first (conservative)
        if (side == 'LONG' and lo <= stop) or (side == 'SHORT' and hi >= stop):
            return stop, j, 'stop'
        if (side == 'LONG' and hi >= target) or (side == 'SHORT' and lo <= target):
            return target, j, 'target'
    return float(day['close'].iloc[-1]), len(day) - 1, 'eod'


# ── ENGINES ───────────────────────────────────────────────────────────────────
def engine_stretch_fade(day: pd.DataFrame, params: dict) -> list[dict]:
    """Mean-reversion: fade price stretched >K·ATR from session VWAP on a reversal bar.
    Target = return to VWAP; stop = STOP_ATR·ATR further into the stretch. Both sides."""
    K        = params.get('k', 2.0)
    stop_atr = params.get('stop_atr', 1.0)
    n_atr    = params.get('atr_n', 14)
    max_tr   = params.get('max_trades', 3)
    t_start  = params.get('start', _dt.time(10, 0))
    t_stop   = params.get('cutoff', _dt.time(14, 30))

    vwap = session_vwap(day)
    atr  = atr_series(day, n_atr)
    trades = []
    i = 0
    n = len(day)
    while i < n and len(trades) < max_tr:
        t = day.index[i].time()
        a = atr.iloc[i]
        if (t < t_start) or (t > t_stop) or np.isnan(a) or a <= 0 or i == 0 or i + 1 >= n:
            i += 1
            continue
        c, pc = day['close'].iloc[i], day['close'].iloc[i - 1]
        stretch = c - vwap.iloc[i]
        side = None
        if stretch >= K * a and c < pc:            # stretched UP + turning down → fade short
            side = 'SHORT'
        elif stretch <= -K * a and c > pc:         # stretched DOWN + turning up → fade long
            side = 'LONG'
        if side:
            entry = float(day['open'].iloc[i + 1])
            target = float(vwap.iloc[i])            # revert to VWAP
            stop_pts = stop_atr * a
            ep, j, reason = _simulate_exit(day, i + 1, side, entry, target, stop_pts)
            sgn = 1 if side == 'LONG' else -1
            trades.append(dict(
                entry_time=day.index[i + 1].strftime('%H:%M'),
                exit_time=day.index[j].strftime('%H:%M'),
                side=side, entry=entry, exit=ep, setup='STRETCH_FADE',
                pnl_pts=sgn * (ep - entry), exit_reason=reason))
            i = j + 1                               # resume after exit
            continue
        i += 1
    return trades


def engine_bear_breakdown(day: pd.DataFrame, params: dict) -> list[dict]:
    """DEFENSIVE LEG (short-only trend follower). Prior: in weak/bear regimes index
    futures show strong intraday downside follow-through (mirror of the momentum-long
    edge). Fires ONLY when the day is confirmed weak — below the opening range low AND
    below a declining session VWAP — so it earns in down years and stands aside in bull
    years. Judged on: positive in 2022 & 2024 (the bad years), even if flat in bull years."""
    or_end   = params.get('or_end', _dt.time(10, 0))
    cutoff   = params.get('cutoff', _dt.time(14, 0))
    tgt_mult = params.get('target_mult', 2.0)      # target = tgt_mult × opening range, downward
    stop_atr = params.get('stop_atr', 1.5)
    max_tr   = params.get('max_trades', 2)

    # Higher-timeframe filter: only short when the DAILY trend is already down
    # (classic "trade with the higher timeframe" — avoids shorting bear-traps in an
    # uptrend/recovery year like 2023). Stand aside otherwise.
    dd = params.get('daily_downtrend')
    if dd is not None and str(day.index[0].date()) not in dd:
        return []

    or_bars = day[day.index.time <= or_end]
    if len(or_bars) < 3:
        return []
    or_low, or_high = float(or_bars['low'].min()), float(or_bars['high'].max())
    or_range = max(or_high - or_low, 1.0)
    vwap = session_vwap(day)
    atr  = atr_series(day)
    trades = []
    i, n = 0, len(day)
    while i < n and len(trades) < max_tr:
        t = day.index[i].time()
        a = atr.iloc[i]
        if t <= or_end or t > cutoff or i + 1 >= n or np.isnan(a) or a <= 0:
            i += 1
            continue
        c = float(day['close'].iloc[i])
        vwap_declining = vwap.iloc[i] <= vwap.iloc[max(0, i - 3)]
        if c < or_low and c < vwap.iloc[i] and vwap_declining:      # confirmed weak breakdown
            entry = float(day['open'].iloc[i + 1])
            target = entry - tgt_mult * or_range
            stop_pts = stop_atr * a
            ep, j, reason = _simulate_exit(day, i + 1, 'SHORT', entry, target, stop_pts)
            trades.append(dict(
                entry_time=day.index[i + 1].strftime('%H:%M'),
                exit_time=day.index[j].strftime('%H:%M'),
                side='SHORT', entry=entry, exit=ep, setup='BEAR_BREAKDOWN',
                pnl_pts=(entry - ep), exit_reason=reason))
            i = j + 1
            continue
        i += 1
    return trades


def engine_orb_cont(day: pd.DataFrame, params: dict) -> list[dict]:
    """ARCHETYPE A — 'smooth road': continuation. Break of the 9:30-10:30 opening range
    → go WITH it. Stops are ATR-SCALED (not the live book's fixed 200pt, which the
    Condition Lab showed is inside the daily noise on most days)."""
    or_end   = params.get('or_end', _dt.time(10, 30))
    cutoff   = params.get('cutoff', _dt.time(14, 0))
    stop_atr = params.get('stop_atr', 1.0)
    rr       = params.get('rr', 2.0)
    max_tr   = params.get('max_trades', 2)
    or_bars = day[day.index.time <= or_end]
    if len(or_bars) < 8:
        return []
    or_hi, or_lo = float(or_bars['high'].max()), float(or_bars['low'].min())
    atr = atr_series(day)
    trades, i, n = [], 0, len(day)
    while i < n and len(trades) < max_tr:
        t = day.index[i].time()
        a = atr.iloc[i]
        if t <= or_end or t > cutoff or i + 1 >= n or not np.isfinite(a) or a <= 0:
            i += 1
            continue
        c = float(day['close'].iloc[i])
        side = 'LONG' if c > or_hi else ('SHORT' if c < or_lo else None)
        if side:
            entry = float(day['open'].iloc[i + 1])
            stop_pts = stop_atr * a
            sgn = 1 if side == 'LONG' else -1
            target = entry + sgn * rr * stop_pts
            ep, j, reason = _simulate_exit(day, i + 1, side, entry, target, stop_pts)
            trades.append(dict(entry_time=day.index[i + 1].strftime('%H:%M'),
                               exit_time=day.index[j].strftime('%H:%M'), side=side,
                               entry=entry, exit=ep, setup='ORB_CONT',
                               pnl_pts=sgn * (ep - entry), exit_reason=reason))
            i = j + 1
            continue
        i += 1
    return trades


def engine_fade_edge(day: pd.DataFrame, params: dict) -> list[dict]:
    """ARCHETYPE B — 'rocky road': failed-breakout fade. Price breaks the opening range
    then closes back INSIDE it → fade back toward the other edge. This is the engine the
    'chop day' half of the fleet thesis needs."""
    or_end   = params.get('or_end', _dt.time(10, 30))
    cutoff   = params.get('cutoff', _dt.time(14, 0))
    stop_atr = params.get('stop_atr', 1.0)
    max_tr   = params.get('max_trades', 2)
    or_bars = day[day.index.time <= or_end]
    if len(or_bars) < 8:
        return []
    or_hi, or_lo = float(or_bars['high'].max()), float(or_bars['low'].min())
    atr = atr_series(day)
    trades, i, n = [], 1, len(day)
    broke_up = broke_dn = False
    while i < n and len(trades) < max_tr:
        t = day.index[i].time()
        a = atr.iloc[i]
        if t <= or_end or i + 1 >= n or not np.isfinite(a) or a <= 0:
            i += 1
            continue
        c, pc = float(day['close'].iloc[i]), float(day['close'].iloc[i - 1])
        if pc > or_hi:
            broke_up = True
        if pc < or_lo:
            broke_dn = True
        side = None
        if broke_up and c < or_hi and t <= cutoff:      # failed upside break → fade short
            side, target = 'SHORT', or_lo
            broke_up = False
        elif broke_dn and c > or_lo and t <= cutoff:    # failed downside break → fade long
            side, target = 'LONG', or_hi
            broke_dn = False
        if side:
            entry = float(day['open'].iloc[i + 1])
            sgn = 1 if side == 'LONG' else -1
            ep, j, reason = _simulate_exit(day, i + 1, side, entry, target, stop_atr * a)
            trades.append(dict(entry_time=day.index[i + 1].strftime('%H:%M'),
                               exit_time=day.index[j].strftime('%H:%M'), side=side,
                               entry=entry, exit=ep, setup='FADE_EDGE',
                               pnl_pts=sgn * (ep - entry), exit_reason=reason))
            i = j + 1
            continue
        i += 1
    return trades


def engine_vwap_pullback(day: pd.DataFrame, params: dict) -> list[dict]:
    """ARCHETYPE C — 'uphill grind': trend-pullback. With price on one side of session
    VWAP all morning, buy the pullback INTO VWAP and the reclaim off it (mirrored short).
    The classic 'narrow drift' engine — needs no big range, only a persistent lean."""
    or_end   = params.get('or_end', _dt.time(10, 30))
    cutoff   = params.get('cutoff', _dt.time(14, 0))
    stop_atr = params.get('stop_atr', 0.75)
    rr       = params.get('rr', 2.0)
    max_tr   = params.get('max_trades', 2)
    vwap = session_vwap(day)
    atr = atr_series(day)
    trades, i, n = [], 1, len(day)
    while i < n and len(trades) < max_tr:
        t = day.index[i].time()
        a = atr.iloc[i]
        if t <= or_end or t > cutoff or i + 1 >= n or not np.isfinite(a) or a <= 0:
            i += 1
            continue
        pre = day.iloc[:i + 1]
        lean = float((pre['close'] > vwap.iloc[:i + 1]).mean())      # morning bias
        c, pc = float(day['close'].iloc[i]), float(day['close'].iloc[i - 1])
        v = float(vwap.iloc[i])
        side = None
        if lean >= 0.70 and pc <= v < c:            # uptrend day, reclaim off VWAP
            side = 'LONG'
        elif lean <= 0.30 and pc >= v > c:          # downtrend day, rejection at VWAP
            side = 'SHORT'
        if side:
            entry = float(day['open'].iloc[i + 1])
            sgn = 1 if side == 'LONG' else -1
            stop_pts = stop_atr * a
            ep, j, reason = _simulate_exit(day, i + 1, side, entry,
                                           entry + sgn * rr * stop_pts, stop_pts)
            trades.append(dict(entry_time=day.index[i + 1].strftime('%H:%M'),
                               exit_time=day.index[j].strftime('%H:%M'), side=side,
                               entry=entry, exit=ep, setup='VWAP_PULLBACK',
                               pnl_pts=sgn * (ep - entry), exit_reason=reason))
            i = j + 1
            continue
        i += 1
    return trades


def engine_fomc_drift(day: pd.DataFrame, params: dict) -> list[dict]:
    """FOMC AFTERNOON DRIFT — the only intraday condition the Condition Lab found that is
    (a) knowable weeks in advance, (b) mechanistically grounded, (c) green in all 6 years.

    Mechanism: the 2:00pm ET decision is a scheduled repricing. Condition Lab measured
    FOMC sessions at +48% directional room and 58% clean-run vs a 29% baseline, with 63%
    of that room living AFTER 14:00 — a window the live book's 14:00 entry cutoff excludes
    by construction. The drift is LONG and it dies at ~15:10: 14:00→15:10 is green every
    year, while 15:10→16:00 is −$4,170 over the same sessions (late-day give-back).

    Wide stop by design — 200-250pt is a plateau, and tightening to 150 turns 2022 negative.
    That echoes the factory's standing finding that a 200pt intraday stop sits inside MNQ's
    own noise; this engine needs room, not protection.

    NOT a standalone book: ~8 events/yr. It is a SLEEVE."""
    fomc = params.get('fomc_days') or set()
    if str(day.index[0].date()) not in fomc:
        return []
    entry_t = params.get('entry_time', _dt.time(14, 0))
    exit_t = params.get('exit_time', _dt.time(15, 10))
    stop_pts = params.get('stop_pts', 250.0)

    pre = day[day.index.time <= entry_t]
    post = day[(day.index.time > entry_t) & (day.index.time <= exit_t)]
    if len(pre) < 40 or len(post) < 5:
        return []
    entry = float(pre['close'].iloc[-1])
    stop = entry - stop_pts
    exit_px, exit_i, reason = float(post['close'].iloc[-1]), len(post) - 1, 'eod'
    lows = post['low'].to_numpy()
    hit = np.nonzero(lows <= stop)[0]
    if len(hit):
        exit_px, exit_i, reason = stop, int(hit[0]), 'stop'
    return [dict(entry_time=post.index[0].strftime('%H:%M'),
                 exit_time=post.index[exit_i].strftime('%H:%M'),
                 side='LONG', entry=entry, exit=exit_px, setup='FOMC_DRIFT',
                 pnl_pts=exit_px - entry, exit_reason=reason)]


ENGINES = {'stretch_fade': engine_stretch_fade,
           'bear_breakdown': engine_bear_breakdown,
           'orb_cont': engine_orb_cont,
           'fade_edge': engine_fade_edge,
           'vwap_pullback': engine_vwap_pullback,
           'fomc_drift': engine_fomc_drift}


def tournament(start: str, end: str, contracts: int = 1):
    """THE FLEET TEST — 'do different clothes suit different weather?'

    Runs every archetype over the same 5.5yr tape, then slices each one's P&L by the
    ROOM FORECAST bucket (causal, known 10:30) and by YEAR. A real fleet thesis needs
    an archetype × condition interaction that is STABLE ACROSS YEARS — not one engine
    that happened to own one bucket in one regime."""
    from futures.factory.conditions import load_day_table, room_forecast
    from futures.factory.bench import engine_stats

    day_tbl = load_day_table()
    day_tbl['room_fc'] = room_forecast(day_tbl)
    d = day_tbl.dropna(subset=['room_fc']).copy()
    d['bucket'] = pd.qcut(d['room_fc'], 5,
                          labels=['R1 dead', 'R2', 'R3', 'R4', 'R5 wide']).astype(str)
    bmap = dict(zip(d['date'].astype(str), d['bucket']))

    names = ['orb_cont', 'fade_edge', 'vwap_pullback']
    frames = {}
    for nm in names:
        print(f'  running archetype {nm} …')
        df = run_engine(nm, start, end, contracts=contracts)
        if len(df):
            df['bucket'] = df['date'].astype(str).map(bmap).fillna('n/a')
            df['year'] = pd.to_datetime(df['date'].astype(str)).dt.year
        frames[nm] = df

    print(f'\n{"="*96}')
    print(f'  ARCHETYPE TOURNAMENT  {start}→{end}   (1 contract, ATR-scaled stops, ${FRICTION_PER_CONTRACT}/c friction)')
    print(f'{"="*96}')
    print('  {:<16}{:>7}{:>11}{:>8}{:>9}{:>9}'.format('archetype', 'n', 'total', 'WR', 'avg', 'Sharpe'))
    print('  ' + '-' * 60)
    for nm, df in frames.items():
        if not len(df):
            print(f'  {nm:<16}   (no trades)'); continue
        st = engine_stats(df)
        print('  {:<16}{:>7}{:>+11,.0f}{:>7.0f}%{:>+9.0f}{:>9.2f}'.format(
            nm, st['n'], st['total'], st['wr'], st['avg'], st['sharpe']))

    buckets = ['R1 dead', 'R2', 'R3', 'R4', 'R5 wide']
    print('\n  P&L by ROOM FORECAST bucket  (the fleet claim: each engine owns a weather)')
    print('  {:<16}'.format('archetype') + ''.join(f'{b:>12}' for b in buckets))
    for nm, df in frames.items():
        if not len(df):
            continue
        line = f'  {nm:<16}'
        for b in buckets:
            g = df[df['bucket'] == b]
            line += f'{g["pnl"].sum():>+12,.0f}' if len(g) else f'{"·":>12}'
        print(line)

    print('\n  PER-YEAR × bucket  (the overfit test — an owned bucket must repeat)')
    for nm, df in frames.items():
        if not len(df):
            continue
        print(f'\n   {nm}')
        print('   {:<12}'.format('bucket') + ''.join(f'{y:>9}' for y in sorted(df['year'].unique())))
        for b in buckets:
            line = f'   {b:<12}'
            for y in sorted(df['year'].unique()):
                g = df[(df['bucket'] == b) & (df['year'] == y)]
                line += f'{g["pnl"].sum():>+9,.0f}' if len(g) else f'{"·":>9}'
            print(line)
    print()


# ── runner ────────────────────────────────────────────────────────────────────
def _daily_downtrend_set(srp, ma_n: int = 50) -> set:
    """Dates where MNQ daily close < ma_n-day MA (higher-timeframe downtrend)."""
    import sqlite3
    con = sqlite3.connect(os.path.join(ROOT, 'market_data.db')
                          if os.path.exists(os.path.join(ROOT, 'market_data.db'))
                          else os.path.join(ROOT, 'trades.db'))
    try:
        d = pd.read_sql_query(
            "SELECT ts_utc, close FROM futures_bars_1d WHERE symbol='MNQ' ORDER BY ts_utc",
            con)
    finally:
        con.close()
    d['date'] = pd.to_datetime(d['ts_utc'], utc=True, format='ISO8601').dt.date
    d['ma'] = d['close'].rolling(ma_n, min_periods=ma_n).mean()
    return {str(row.date) for row in d.itertuples() if not np.isnan(row.ma) and row.close < row.ma}


def run_engine(name: str, start: str, end: str, contracts: int = 2,
               friction: float = FRICTION_PER_CONTRACT, daily_filter: bool = False,
               **params) -> pd.DataFrame:
    srp = load_rth()
    start_dt = _dt.date.fromisoformat(start)
    end_cut  = (_dt.date.fromisoformat(end) + _dt.timedelta(days=1)).isoformat()
    bars = srp.filter_ny_session(srp.load_bars(
        'MNQ', start=(start_dt - _dt.timedelta(days=5)).isoformat(), end=end_cut))
    if daily_filter:
        params['daily_downtrend'] = _daily_downtrend_set(srp)
    if name == 'fomc_drift':
        from futures.factory.conditions import FOMC_DAYS
        params.setdefault('fomc_days', FOMC_DAYS)
    fn = ENGINES[name]
    rows = []
    for d, day in bars.groupby(bars.index.date):
        if d < start_dt:
            continue
        day = day.sort_index()
        for tr in fn(day, params):
            pnl = tr['pnl_pts'] * DOLLARS_PER_PT_MNQ * contracts - friction * contracts
            rows.append(dict(date=str(d), engine=tr['setup'], setup=tr['setup'],
                             side=tr['side'], entry_time=tr['entry_time'],
                             exit_time=tr['exit_time'], entry=tr['entry'], exit=tr['exit'],
                             contracts=contracts, pnl=pnl, exit_reason=tr['exit_reason']))
    return pd.DataFrame(rows)


def portfolio_gauntlet(start: str, end: str, regime_switch: bool = False):
    """The payoff test: does momentum (existing book) + the bear defensive leg pass the
    TopStep gauntlet BETTER than momentum alone? Combines both trade streams into one
    account and runs the same DLL/MLL/consistency simulator.

    regime_switch=True: run each leg ONLY in its own daily regime (momentum on uptrend
    days, bear on downtrend days) so the two legs never fire the same day — tests whether
    that kills the drawdown-stacking that made the always-on portfolio worse on blow-rate."""
    from futures.factory.bench import run_ny
    srp = load_rth()
    print('\n  Building momentum stream (sim_replay)…')
    mom = run_ny(start, end)
    mom['engine'] = 'MOM_' + mom['engine'].astype(str)
    print('  Building bear defensive-leg stream…')
    bear = run_engine('bear_breakdown', start, end, daily_filter=True)

    tag = 'always-on'
    if regime_switch:
        dd = _daily_downtrend_set(srp)
        before = len(mom)
        mom = mom[~mom['date'].astype(str).isin(dd)].copy()   # momentum only on non-downtrend days
        tag = f'regime-switched (dropped {before-len(mom)} momentum trades on downtrend days)'

    combined = pd.concat([mom, bear], ignore_index=True)
    scorecard(combined, f'PORTFOLIO momentum+bear [{tag}]  {start}→{end}')
    print('\n  ── momentum ALONE gauntlet ──')
    topstep_report(mom)
    print(f'\n  ── PORTFOLIO gauntlet [{tag}] ──')
    topstep_report(combined)


def intraday_filter_test(start: str, end: str):
    """Motivated by Aug-17 (an UPTREND day the daily filter couldn't catch — momentum longs
    bought an 11am pullback and rode to EOD stops). Test an INTRADAY-tape filter: only keep
    momentum trades that are WITH the intraday trend at entry (LONG at/above session VWAP,
    SHORT at/below). Reconstructs VWAP-at-entry from 5-min bars. Compares gauntlet raw vs
    intraday-filtered."""
    from futures.factory.bench import run_ny
    srp = load_rth()
    print('\n  Building momentum stream…')
    mom = run_ny(start, end)
    mom['engine'] = 'MOM_' + mom['engine'].astype(str)

    start_dt = _dt.date.fromisoformat(start)
    bars = srp.filter_ny_session(srp.load_bars(
        'MNQ', start=(start_dt - _dt.timedelta(days=5)).isoformat(),
        end=(_dt.date.fromisoformat(end) + _dt.timedelta(days=1)).isoformat()))
    # per-day VWAP series
    vwap_by_day = {}
    for d, day in bars.groupby(bars.index.date):
        day = day.sort_index()
        vwap_by_day[str(d)] = (session_vwap(day), day.index)

    keep = []
    for _, t in mom.iterrows():
        dv = vwap_by_day.get(str(t['date']))
        if dv is None:
            keep.append(True); continue
        vser, idx = dv
        try:
            e_h, e_m = map(int, str(t['entry_time']).split(':'))
        except Exception:
            keep.append(True); continue
        mask = idx.time <= _dt.time(e_h, e_m)
        if not mask.any():
            keep.append(True); continue
        vwap_at = float(vser[mask].iloc[-1])
        with_trend = (t['side'] == 'LONG' and t['entry'] >= vwap_at) or \
                     (t['side'] == 'SHORT' and t['entry'] <= vwap_at)
        keep.append(bool(with_trend))
    mom_f = mom[pd.Series(keep, index=mom.index)].copy()

    print(f'\n  Intraday-VWAP filter: kept {len(mom_f)}/{len(mom)} momentum trades '
          f'({len(mom)-len(mom_f)} dropped as fighting the intraday tape)')
    scorecard(mom_f, f'MOMENTUM + intraday-VWAP filter  {start}→{end}')
    print('\n  ── momentum RAW gauntlet ──')
    topstep_report(mom)
    print('\n  ── momentum + INTRADAY-VWAP filter gauntlet ──')
    topstep_report(mom_f)


def filter_lab(start: str, end: str):
    """Compare selectivity filters on the momentum book over the FULL history (runs
    momentum once, applies each filter cheaply). Forward-validates the live '11am pocket
    is a disaster' finding on 5.5yr, alongside the daily-downtrend filter."""
    from futures.factory.bench import (run_ny, daily_pnls_with_dll, monte_carlo_eval,
                                       engine_stats)
    srp = load_rth()
    print('\n  Building momentum stream…')
    mom = run_ny(start, end)
    dd = _daily_downtrend_set(srp)
    hr = mom['entry_time'].astype(str).str[:2]
    on_down = mom['date'].astype(str).isin(dd)

    filters = {
        'raw momentum':             mom,
        'block 11:00 hour':         mom[hr != '11'],
        'daily-downtrend filter':   mom[~on_down],
        'block-11 + daily-filter':  mom[(hr != '11') & (~on_down)],
        'ONLY 10:00 hour':          mom[hr == '10'],
    }
    print(f'\n{"="*88}')
    print(f'  FILTER LAB — momentum robustness levers  {start}→{end}  (full-history gauntlet)')
    print(f'{"="*88}')
    print('  {:<26}{:>6}{:>11}{:>8}{:>10}{:>10}'.format(
        'filter', 'n', 'Total', 'Sharpe', 'P(pass)', 'P(blow)'))
    print('  ' + '-' * 74)
    for name, df in filters.items():
        if len(df) == 0:
            print(f'  {name:<26}   (no trades)'); continue
        st = engine_stats(df)
        dp = [v for _, v in daily_pnls_with_dll(df)]
        mc = monte_carlo_eval(dp)
        print('  {:<26}{:>6}{:>+11,.0f}{:>8.2f}{:>9.0f}%{:>9.0f}%'.format(
            name, st['n'], st['total'], st['sharpe'],
            mc.get('p_pass', 0) * 100, mc.get('p_blow', 0) * 100))
    # per-year for the 11am pocket (is it structural or a recent artifact?)
    print('\n  11:00-hour pocket, per year (structural check):')
    m11 = mom[hr == '11']
    yr = pd.to_datetime(m11['date'].astype(str)).dt.year
    for y in sorted(set(yr)):
        g = m11[yr == y]
        print(f'    {y}: n={len(g):3d}  ${g["pnl"].sum():+,.0f}  (WR {(g["pnl"]>0).mean()*100:.0f}%)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--engine', default='stretch_fade', choices=list(ENGINES))
    ap.add_argument('--start', default='2021-06-01')
    ap.add_argument('--end',   default='2026-08-14')
    ap.add_argument('--giveback', action='store_true')
    ap.add_argument('--daily-filter', action='store_true', help='only short when daily trend down')
    ap.add_argument('--portfolio', action='store_true', help='momentum+bear combined gauntlet')
    ap.add_argument('--regime-switch', action='store_true', help='each leg only in its own daily regime')
    ap.add_argument('--intraday-filter', action='store_true', help='momentum with intraday-VWAP tape filter')
    ap.add_argument('--filter-lab', action='store_true', help='compare momentum selectivity filters')
    ap.add_argument('--tournament', action='store_true', help='archetype fleet × weather-bucket test')
    args = ap.parse_args()

    if args.tournament:
        tournament(args.start, args.end)
        return
    if args.filter_lab:
        filter_lab(args.start, args.end)
        return
    if args.intraday_filter:
        intraday_filter_test(args.start, args.end)
        return
    if args.portfolio:
        portfolio_gauntlet(args.start, args.end, regime_switch=args.regime_switch)
        return

    print(f'\n Hunting engine: {args.engine}  |  {args.start} → {args.end}'
          f'{"  [daily-downtrend filter ON]" if args.daily_filter else ""}')
    df = run_engine(args.engine, args.start, args.end, daily_filter=args.daily_filter)
    scorecard(df, f'CANDIDATE {args.engine}  {args.start}→{args.end}')
    topstep_report(df)
    if args.giveback:
        giveback_report(df)
    print()


if __name__ == '__main__':
    main()
