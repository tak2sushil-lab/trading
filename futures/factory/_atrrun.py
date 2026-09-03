"""Full-pipeline run of the ATR-scaled exit candidate (Aug 24 2026).

Mirrors _longonly.py: sets sim_replay module globals to the LIVE production config
(parity_check.SIM_FLAGS) and runs the real _run_scenario over 5.5yr of MNQ bars.
The ONLY difference from the cached baseline `_mom_2021-06-01_2026-08-14.csv` is
ATR_EXIT_SCALE — verified a byte-for-byte no-op when None (scratchpad/noop_check.py).

argv: <scale|none> [parts]     e.g.  _atrrun.py 1.0 trail,nomove
"""
import sys, datetime as _dt
sys.path.insert(0, '/Users/sushil/trading')
import pandas as pd
import futures.sim_replay as srp

SCALE = None if sys.argv[1].lower() == 'none' else float(sys.argv[1])
PARTS = tuple(sys.argv[2].split(',')) if len(sys.argv) > 2 else ('trail', 'nomove')
OUT = '/Users/sushil/trading/futures/factory'

# --- production config, identical to parity_check.SIM_FLAGS
srp.GRADUATED_RVOL = True; srp.RVOL_GRAD_FLOOR = 0.70
srp.REGIME_AWARE_EXITS = True; srp.BASE_STOP_PTS = 200.0
srp.NO_OVN_SKIP = True; srp.MAX_DAILY_TRADES = 5; srp.HERO_GATE_ENABLED = True
srp.MAX_DAILY_LOSS = 1250.0
srp.REV_EXIT = (2, 0.30, 120.0); srp.PARTIAL_TAKE_PTS = 150.0
# --- the candidate
srp.ATR_EXIT_SCALE = SCALE
srp.ATR_EXIT_PARTS = PARTS

tag = 'base' if SCALE is None else f"{SCALE}".replace('.', 'p')
print(f"ATR run: scale={SCALE} parts={PARTS} ref={srp.ATR_EXIT_REF}", flush=True)

ab = srp.load_bars('MNQ', start='2021-03-01', end='2026-08-18')
rth = srp.filter_ny_session(ab)
dates = sorted(d for d in set(rth.index.date) if d >= _dt.date(2021, 6, 1))
print(f"  {len(dates)} sessions to replay", flush=True)
r = srp._run_scenario(ab, dates, False, False, 'NY', verbose=False)
df = r['df'].copy()
path = f'{OUT}/_atr_{tag}.csv'
df.to_csv(path, index=False)

y = pd.to_datetime(df['date'].astype(str)).dt.year
FR = 6.0
df['net'] = df.pnl - FR * df.contracts.clip(lower=1)
dd = df.groupby('date').net.sum().sort_index(); eq = dd.cumsum()
print(f"  n={len(df)}  raw=${df.pnl.sum():+,.0f}  net(-$6/c)=${df.net.sum():+,.0f}  "
      f"maxDD=${(eq-eq.cummax()).min():+,.0f}  worstDay=${dd.min():+,.0f}", flush=True)
print("  per year (net): " + " ".join(
    f"{a}:{df.loc[y == a, 'net'].sum():+,.0f}" for a in sorted(y.unique())), flush=True)
print(f"  exits: {df.exit_reason.value_counts().to_dict()}", flush=True)
print(f"  -> {path}", flush=True)
