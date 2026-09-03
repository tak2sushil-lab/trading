"""Run the LIVE code path with ONE change: BASE_STOP_PTS. Everything else stays at the
parity SIM_FLAGS the live traders run (graduated RVOL, regime-aware exits, no-ovn-skip,
rev-exit 2/0.30/120, partial 150, max-trades 5, hero gate on). Contract count is NOT
overridden — calc_contracts() derives it from the stop, which is the point:
  500pt -> 2 contracts -> $2,000 max risk      1000pt -> 1 contract -> $2,000 max risk
"""
import sys, os, datetime as _dt
sys.path.insert(0, '/Users/sushil/trading')
import pandas as pd
import futures.sim_replay as srp

S = float(sys.argv[1])
START = sys.argv[2] if len(sys.argv) > 2 else '2021-06-01'
MAXRISK = float(sys.argv[3]) if len(sys.argv) > 3 else None   # control: force contract count
OUT = '/Users/sushil/trading/futures/factory'
srp.GRADUATED_RVOL = True; srp.RVOL_GRAD_FLOOR = 0.70; srp.REGIME_AWARE_EXITS = True
srp.NO_OVN_SKIP = True; srp.REV_EXIT = (2, 0.30, 120.0); srp.PARTIAL_TAKE_PTS = 150.0
srp.MAX_DAILY_TRADES = 5; srp.HERO_GATE_ENABLED = True
srp.BASE_STOP_PTS = S                                   # <-- the change
if MAXRISK is not None:
    srp.MAX_RISK_PER_TRADE = MAXRISK   # CONTROL ONLY: isolates "1 contract" from "wide stop"
# The 1500pt target is a dead backstop (comment in sim_replay: "essentially never fires").
# But MIN_RR=1.4 is checked against it, so a stop >1071pt rejects EVERY entry. Scale the
# backstop so RR stays 1.5 — the same RR the 1000pt run gets — instead of silently
# testing "no trades at all".
if 1500.0 / S < srp.MIN_RR:
    srp.BASE_TARGET_PTS = round(S * 1.5)
    print(f'  (target backstop scaled to {srp.BASE_TARGET_PTS:.0f} to keep RR=1.5 past MIN_RR)', flush=True)

import datetime as _d2
_ls = (_d2.date.fromisoformat(START) - _d2.timedelta(days=110)).isoformat()
ab = srp.load_bars('MNQ', start=_ls, end='2026-08-18')
rth = srp.filter_ny_session(ab)
dates = sorted(d for d in set(rth.index.date) if d >= _dt.date.fromisoformat(START))
r = srp._run_scenario(ab, dates, False, False, 'NY', verbose=False)
if r.get('df') is None or len(r['df']) == 0:
    print(f'STOP={int(S)}  ZERO TRADES — check MIN_RR ({srp.MIN_RR}) vs target/stop '
          f'({srp.BASE_TARGET_PTS}/{S} = {srp.BASE_TARGET_PTS/S:.2f})', flush=True)
    sys.exit(0)
df = r['df'].copy()
_sfx = ('' if START == '2021-06-01' else '_' + START[:4]) + ('' if MAXRISK is None else f'_r{int(MAXRISK)}')
df.to_csv(f'{OUT}/_mom_livestop{int(S)}{_sfx}.csv', index=False)
y = pd.to_datetime(df['date'].astype(str)).dt.year
print(f'STOP={int(S)}  n={len(df)}  contracts={df.contracts.value_counts().to_dict()}', flush=True)
print('  per year: ' + ' '.join(f'{a}:{df.loc[y==a,"pnl"].sum():+,.0f}' for a in sorted(y.unique())), flush=True)
