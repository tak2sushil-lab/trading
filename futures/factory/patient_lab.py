"""
PATIENT LAB — "give it room to grow, judge its character later."

The engine (user, Aug 19 2026), distinct from the hard time-stop already rejected:
  * 1 contract, always. A WIDE stop at entry, and that is the ONLY thing active at first.
  * A BREATHE window of hours: no trail, no target, no cutting. Let the trade settle.
  * At the end of the breathe window a TRAIL switches on, set WIDE (not tucked in behind
    price). From then on the trade either keeps making new highs and rides, or the trail
    takes it out. "I gave you all day to show character; you did not, so I cut you."
  * Flat at the session close regardless.
  * Possibly an EARLIER entry window (09:45) to buy the trade more hours.

The question asked, and the one this file leads with: HOW MANY TIMES WOULD IT HAVE BLOWN
THE ACCOUNT — measured on our own transaction data first, then on the 5.5yr sim.

Blow-up accounting is per account, using prop_rules' real numbers:
  TC $50k : DLL $1,000 halts the day; trailing MLL $2,000 from the high-water mark = BLOWN.
  IBKR    : soft DLL $1,250 halts the day; no trailing MLL — blown = the $5,000 futures
            allocation is gone.
After a blow the account is reset and we keep counting, so "3 blows" means three accounts.

Run: venv/bin/python -m futures.factory.patient_lab --live --sim --grid --early
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

from futures.factory.wide_stop_lab import (FAC, POINT_VALUE, COMMISSION, FRICTION,  # noqa: E402
                                           EOD, bars_1m, ny_entries, maxdd)

TC_MLL, TC_DLL = 2_000.0, 1_000.0
IB_ALLOC, IB_DLL = 5_000.0, 1_250.0


# ── the engine ───────────────────────────────────────────────────────────────
def patient(paths, stop=1000.0, breathe_min=180, trail_gap=150.0,
            one_at_a_time=True) -> pd.DataFrame:
    """Wide stop only for `breathe_min` minutes; then a ratcheting trail `trail_gap`
    points behind the running peak. Stop is checked against the level set by the PREVIOUS
    bar (no same-bar race), mirroring sim_replay's own convention."""
    rows, free_until = [], None
    for p in paths:
        if one_at_a_time and free_until is not None and p['entry_dt'] < free_until:
            continue
        fav, adv, close = p['fav'], p['adv'], p['close']
        n = len(fav)
        L = -float(stop)          # stop level in points from entry (negative = below entry)
        peak = 0.0
        pts, why, idx = None, None, n - 1
        for i in range(n):
            if adv[i] >= -L:                      # bar traded through the stop level
                pts, why, idx = L, ('stop' if L <= -stop + 1e-9 else 'trail'), i
                break
            peak = max(peak, float(fav[i]))
            if (i + 1) >= breathe_min:
                # A stop can only REST BELOW THE MARKET. If peak-gap is already above the
                # current price, the market left that level during the breathe window and
                # we cannot be filled there — the order becomes a market exit at the price
                # that is actually available. Without this clamp the engine books exits at
                # prices the tape had already passed (21% of activations, median 91pts too
                # good, p90 297) — a look-ahead that roughly triples the measured P&L.
                L = max(L, min(peak - float(trail_gap), float(close[i])))
        if pts is None:
            pts, why, idx = float(close[-1]), 'eod', n - 1
        free_until = pd.Timestamp(p['ts'][idx])
        rows.append(dict(date=p['date'], y=p['y'], side=p['side'], setup=p['setup'],
                         entry_time=str(p['entry_dt'])[11:16], pts=pts, why=why,
                         mfe=float(np.maximum.accumulate(fav)[-1]),
                         pnl=pts * POINT_VALUE - COMMISSION - FRICTION))
    return pd.DataFrame(rows)


# ── blow-up accounting ───────────────────────────────────────────────────────
def daily_with_dll(d: pd.DataFrame, dll: float) -> pd.Series:
    """Collapse to per-day P&L, applying the daily-loss halt the engine does not model:
    once the day's running total breaches -dll, later trades that day never happen."""
    out = {}
    for day, g in d.sort_values(['date', 'entry_time']).groupby('date'):
        cum = 0.0
        for v in g['pnl']:
            cum += v
            if cum <= -dll:
                break
        out[day] = cum
    return pd.Series(out).sort_index()


def blowups(daily: pd.Series, mode: str):
    """Count how many times the account dies. Reset and keep counting after each death."""
    cum = hwm = 0.0
    events, halts = [], 0
    for day, v in daily.items():
        cum += v
        hwm = max(hwm, cum)
        if mode == 'TC':
            floor = min(hwm - TC_MLL, 0.0)          # trailing MLL, locks at start balance
            dead = cum <= floor
        else:
            dead = cum <= -IB_ALLOC                 # IBKR: no trailing MLL, just the allocation
        if dead:
            events.append((day, cum))
            cum = hwm = 0.0
    dll = IB_DLL if mode == 'IBKR' else TC_DLL
    halts = int((daily <= -dll + 1e-9).sum())
    return events, halts


def report(lbl, d, mode, years=None, show_years=True):
    if not len(d):
        print(f'  {lbl:<38}  (no trades)'); return
    dll = IB_DLL if mode == 'IBKR' else TC_DLL
    daily = daily_with_dll(d, dll)
    ev, halts = blowups(daily, mode)
    tot = float(daily.sum())
    dd = float((daily.cumsum() - daily.cumsum().cummax()).min())
    per = ''
    if show_years and years:
        yy = pd.to_datetime(daily.index).year
        per = '  ' + ''.join(f'{daily[yy == y].sum():>+9,.0f}' for y in years)
    print(f'  {lbl:<38}{len(d):>5}{tot:>+9,.0f}{dd:>+9,.0f}{daily.min():>+9,.0f}'
          f'{halts:>7}{len(ev):>7}{per}')
    return dict(tot=tot, dd=dd, worst=float(daily.min()), halts=halts, blows=len(ev), ev=ev)


def hdr(years=None):
    print(f'  {"config":<38}{"n":>5}{"total":>9}{"maxDD":>9}{"wrstDay":>9}'
          f'{"halts":>7}{"BLOWS":>7}' + ('  ' + ''.join(f'{y:>9}' for y in years) if years else ''))
    print('  ' + '-' * (38 + 5 + 9 + 9 + 9 + 7 + 7 + (2 + 9 * len(years) if years else 0)))


# ── LIVE transaction data ────────────────────────────────────────────────────
def live_entries() -> pd.DataFrame:
    con = sqlite3.connect(os.path.join(ROOT, 'trades.db'))
    t = pd.read_sql_query(
        """SELECT entry_date date, entry_time, entry_price entry, side, setup_type setup,
                  contracts, pnl real_pnl, account_mode, exit_reason
             FROM futures_trades WHERE status='CLOSED' AND setup_type!='RECONCILED'
              AND (notes IS NULL OR notes NOT LIKE 'partial of %')""", con)
    con.close()
    t['y'] = pd.to_datetime(t['date']).dt.year
    return t.sort_values(['date', 'entry_time']).reset_index(drop=True)


def live_paths(t: pd.DataFrame, bars):
    dm = {str(k): v.sort_index() for k, v in bars.groupby(bars.index.date)}
    out, miss = [], 0
    for _, r in t.iterrows():
        dd = dm.get(str(r['date']))
        if dd is None:
            miss += 1; continue
        hh, mm = map(int, str(r['entry_time'])[:5].split(':'))
        fwd = dd[(dd.index.time > _dt.time(hh, mm)) & (dd.index.time <= EOD)]
        if len(fwd) < 3:
            miss += 1; continue
        e = float(r['entry']); sg = 1.0 if r['side'] == 'LONG' else -1.0
        fav = (fwd['high'].to_numpy() - e) * sg if sg > 0 else (e - fwd['low'].to_numpy())
        adv = (e - fwd['low'].to_numpy()) if sg > 0 else (fwd['high'].to_numpy() - e)
        out.append(dict(date=str(r['date']), y=int(r['y']), side=r['side'], setup=r['setup'],
                        acct=r['account_mode'],
                        entry_dt=pd.Timestamp(f"{r['date']} {hh:02d}:{mm:02d}"),
                        fav=fav, adv=adv, close=(fwd['close'].to_numpy() - e) * sg,
                        ts=fwd.index.to_numpy()))
    return out, miss


def mode_live():
    t = live_entries()
    bars = bars_1m()
    manual = t.exit_reason.fillna('').str.contains('manual|Manual|FUT CLOSE', regex=True)
    print(f'\n{"="*140}')
    print('  A. OUR OWN TRANSACTION DATA — how often would the patient engine have blown the account?')
    print(f'{"="*140}')
    print(f'  live CLOSED futures trades: {len(t)}  ({t.date.min()} -> {t.date.max()})')
    print(f'  manual FUT CLOSE trades present: {int(manual.sum())} (${t.loc[manual,"real_pnl"].sum():+,.0f}) '
          f'— they are ENTRIES like any other here, only the exit is replaced by the engine')
    for acct in ('IBKR', 'TC'):
        sub = t[t.account_mode == acct].reset_index(drop=True)
        paths, miss = live_paths(sub, bars)
        real = sub.groupby('date').real_pnl.sum()
        ev_r, halt_r = blowups(real, acct)
        print(f'\n  ── {acct} ──  {len(sub)} live trades, {len(paths)} replayable ({miss} without bars)')
        print(f'     WHAT ACTUALLY HAPPENED: ${sub.real_pnl.sum():+,.0f}  worst day ${real.min():+,.0f}  '
              f'DLL-halt days {halt_r}  BLOWS {len(ev_r)}')
        hdr()
        for S in (1000, 850, 625):
            for B, G in ((180, 150), (240, 150), (180, 250), (300, 200)):
                report(f'stop {S} · breathe {B}m · trail {G}', patient(paths, S, B, G), acct)
        report('wide 1000 · hold to close (no trail)', patient(paths, 1000, 10**6, 10**6), acct)
    print('\n  "halts" = days the account hit its daily loss limit and stopped. "BLOWS" = times the')
    print('  account died outright (TC: $2,000 trailing MLL. IBKR: the $5,000 allocation gone).')


# ── 5.5yr sim ────────────────────────────────────────────────────────────────
def sim_paths(entries=None):
    from futures.factory.wide_stop_lab import _paths
    return _paths(entries if entries is not None else ny_entries(), bars_1m())


def mode_sim(paths=None):
    if paths is None:
        paths = sim_paths()
    years = sorted({p['y'] for p in paths})
    print(f'\n{"="*160}')
    print('  B. THE SAME ENGINE OVER 5.5 YEARS — this is where the blow-up count means something')
    print('     (2.5 months of live data cannot see a 2022; the sim can)')
    print(f'{"="*160}')
    for acct in ('IBKR', 'TC'):
        print(f'\n  ── {acct} ──   (DLL ${IB_DLL if acct=="IBKR" else TC_DLL:,.0f} halt, '
              f'{"$5,000 allocation" if acct=="IBKR" else "$2,000 trailing MLL"} = dead)')
        hdr(years)
        for S in (1000, 850, 625, 500):
            for B, G in ((180, 150), (240, 150)):
                report(f'stop {S} · breathe {B}m · trail {G}', patient(paths, S, B, G), acct, years)
        report('wide 1000 · hold to close', patient(paths, 1000, 10**6, 10**6), acct, years)
        report('live exit stack (reference, 1c)', _live_stack(), acct, years)


def _live_stack():
    d = ny_entries().copy()
    sg = np.where(d.side == 'LONG', 1.0, -1.0)
    d['pnl'] = (d['exit'] - d['entry']) * sg * POINT_VALUE - COMMISSION - FRICTION
    return d[['date', 'y', 'side', 'setup', 'entry_time', 'pnl']]


def mode_grid(paths=None):
    if paths is None:
        paths = sim_paths()
    years = sorted({p['y'] for p in paths})
    B_ = [60, 120, 180, 240, 300]
    G_ = [75, 100, 150, 200, 300, 500]
    print(f'\n{"="*160}')
    print('  C. THE BREATHE x TRAIL SURFACE  (stop 625, 1 contract, one position at a time)')
    print('     A real setting has neighbours that work too. Read across AND down.')
    print(f'{"="*160}')
    cache = {}
    for metric in ('total $', 'green years', 'TC blows', 'worst day $'):
        print(f'\n  {metric} — rows = breathe (minutes), cols = trail gap (points)')
        print(f'  {"":>9}' + ''.join(f'{str(g)+"p":>10}' for g in G_))
        print('  ' + '-' * (9 + 10 * len(G_)))
        for B in B_:
            cells = ''
            for G in G_:
                if (B, G) not in cache:
                    cache[(B, G)] = patient(paths, 625, B, G)
                d = cache[(B, G)]
                daily = daily_with_dll(d, TC_DLL)
                if metric == 'total $':
                    cells += f'{daily.sum():>+10,.0f}'
                elif metric == 'green years':
                    yy = pd.to_datetime(daily.index).year
                    cells += f'{sum(1 for y in years if daily[yy==y].sum()>0):>7}/{len(years)}'
                elif metric == 'TC blows':
                    cells += f'{len(blowups(daily, "TC")[0]):>10}'
                else:
                    cells += f'{daily.min():>+10,.0f}'
            print(f'  {str(B)+"m":>9}{cells}')
    print('\n  Look for a REGION that is green and blow-free, not a single best cell.')


# ── earlier entry window ─────────────────────────────────────────────────────
def mode_early():
    """Does starting at 09:45 instead of 10:30 buy the trade enough extra hours to matter?
    This needs a REAL pipeline re-run (IB_READY_TIME gates the entry set), not a filter."""
    fn = os.path.join(FAC, '_mom_entry0945.csv')
    if not os.path.exists(fn):
        print('\n  [early] _mom_entry0945.csv not built yet — run the 09:45 sim first.')
        return
    from futures.factory.wide_stop_lab import _paths
    base = ny_entries()
    early = pd.read_csv(fn)
    early['date'] = early['date'].astype(str)
    early['y'] = pd.to_datetime(early['date']).dt.year
    early = early.sort_values(['date', 'entry_time']).reset_index(drop=True)
    bars = bars_1m()
    years = sorted(base.y.unique())
    print(f'\n{"="*160}')
    print('  D. EARLIER ENTRY WINDOW — 09:45 IB-ready instead of 10:30')
    print('     (⚠️ the 09:30 version of this has been tested and REJECTED three times; the')
    print('      rationale here is different — more hours to breathe — so it is re-tested, but')
    print('      the prior result is the prior.)')
    print(f'{"="*160}')
    print(f'  entries 10:30-start: {len(base):>4}   09:45-start: {len(early):>4}')
    print(f'  entry hour mix 09:45-start: {early.entry_time.str[:2].value_counts().sort_index().to_dict()}')
    pe = _paths(early, bars)
    pb = _paths(base, bars)
    for acct in ('IBKR', 'TC'):
        print(f'\n  ── {acct} ──')
        hdr(years)
        for lbl, pp in (('10:30 start', pb), ('09:45 start', pe)):
            for B, G in ((120, 150), (180, 150), (240, 150)):
                report(f'{lbl} · breathe {B}m · trail {G}', patient(pp, 625, B, G), acct, years)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    for f in ('live', 'sim', 'grid', 'early'):
        ap.add_argument(f'--{f}', action='store_true')
    ap.add_argument('--all', action='store_true')
    a = ap.parse_args()
    paths, ran = None, False
    if a.all or a.live:
        mode_live(); ran = True
    for f in ('sim', 'grid'):
        if a.all or getattr(a, f):
            if paths is None:
                paths = sim_paths()
            globals()[f'mode_{f}'](paths); ran = True
    if a.all or a.early:
        mode_early(); ran = True
    if not ran:
        ap.print_help()


if __name__ == '__main__':
    main()
