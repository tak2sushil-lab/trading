"""FOMC-afternoon entry window test (Aug 25 2026).

conditions.py --calendar: FOMC days show room 0.79 ATR vs 0.53 baseline (+48%) and clean-run
58% vs 29%, stable in ALL SIX years. The 14:00-15:10 window moves 2.27x a normal afternoon.
Our ENTRY_CUTOFF is 14:00, so we are excluded from it BY CONSTRUCTION — and FOMC dates are
known years in advance, so there is zero forecasting risk in using them.

The open question this answers: FOMC gives ROOM, but conditions.py also showed DIRECTION is not
forecastable — so the only way to trade it is if OUR OWN entry signals work in that window.

Method: days are independent (one position at a time, daily reset), so we can compose
  non-FOMC days  from a normal-cutoff run
  FOMC days      from an extended-cutoff run
and compare against the all-normal baseline. argv: <cutoff HH:MM for FOMC days>
"""
import sys, datetime as _dt
sys.path.insert(0, '/Users/sushil/trading')
import pandas as pd, numpy as np
import futures.sim_replay as srp
from futures.factory.conditions import FOMC_DAYS

CUT = sys.argv[1] if len(sys.argv) > 1 else '15:00'
YRS = ['2021', '2022', '2023', '2024', '2025', '2026']

def cfg():
    srp.GRADUATED_RVOL = True; srp.RVOL_GRAD_FLOOR = 0.70
    srp.REGIME_AWARE_EXITS = True; srp.BASE_STOP_PTS = 200.0
    srp.NO_OVN_SKIP = True; srp.MAX_DAILY_TRADES = 5; srp.HERO_GATE_ENABLED = True
    srp.MAX_DAILY_LOSS = 1250.0
    srp.REV_EXIT = (2, 0.30, 120.0); srp.PARTIAL_TAKE_PTS = 150.0
    srp.ATR_EXIT_SCALE = None

cfg()
ab = srp.load_bars('MNQ', start='2021-03-01', end='2026-08-18')
rth = srp.filter_ny_session(ab)
alld = sorted(d for d in set(rth.index.date) if d >= _dt.date(2021, 6, 1))
fomc = [d for d in alld if str(d) in FOMC_DAYS]
print(f"{len(alld)} sessions, {len(fomc)} of them FOMC", flush=True)

def run(dates, cutoff):
    srp.ENTRY_CUTOFF = _dt.time(*map(int, cutoff.split(':')))
    return srp._run_scenario(ab, dates, False, False, 'NY', verbose=False)['df'].copy()

def rep(df, label):
    if df.empty: print(f"  {label:38s} no trades"); return
    df = df.copy(); df['net'] = df.pnl - 6.0 * df.contracts.clip(lower=1)
    y = pd.to_datetime(df.date.astype(str)).dt.year.astype(str)
    ys = df.groupby(y).net.sum().reindex(YRS).fillna(0)
    dd = df.groupby('date').net.sum().sort_index(); eq = dd.cumsum()
    print(f"  {label:38s} n={len(df):4d} ${df.net.sum():+8,.0f} DD={(eq-eq.cummax()).min():8,.0f} "
          f"worst={dd.min():7,.0f} green={(ys>0).sum()}/6  " + " ".join(f"{a[2:]}:{v:+6.0f}" for a, v in ys.items()))

base = run(alld, '14:00')
rep(base, 'BASELINE (14:00 cutoff everywhere)')
fo_norm = base[base.date.astype(str).isin(FOMC_DAYS)]
rep(fo_norm, f'  ...of which FOMC days ({len(fo_norm)} trades)')
fo_ext = run(fomc, CUT)
rep(fo_ext, f'FOMC days with {CUT} cutoff')
combo = pd.concat([base[~base.date.astype(str).isin(FOMC_DAYS)], fo_ext], ignore_index=True)
rep(combo, f'COMBINED (FOMC extended to {CUT})')
d = fo_ext.copy(); d['t'] = d.entry_time.astype(str)
late = d[d.t >= '14:00']
if len(late):
    late = late.copy(); late['net'] = late.pnl - 6.0 * late.contracts.clip(lower=1)
    y = pd.to_datetime(late.date.astype(str)).dt.year.astype(str)
    ys = late.groupby(y).net.sum()
    print(f"\n  the NEW post-14:00 FOMC trades only: n={len(late)} ${late.net.sum():+,.0f} "
          f"WR {(late.net>0).mean()*100:.0f}%  per year {ys.round(0).to_dict()}")
    print(f"  by side: {late.groupby('side').net.agg(['size','sum']).round(0).to_dict('index')}")
