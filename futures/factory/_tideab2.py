"""Within-engine A/B for the Daily Tide gate (Sep 29 2026). Same script,
same bars, same window, same live-config flags -- ONE variable changes:
srp.IB_CLASSIFY_AT_1030. Obeys the Aug 24 2026 rule: never compare caches
produced by different scripts.

argv: <off|on>   off = legacy (classify on the ~09:45 bar), on = 09:30-10:30 IB
"""
import sys, datetime as _dt
sys.path.insert(0, '/Users/sushil/trading')
import pandas as pd
import futures.sim_replay as srp

MODE = sys.argv[1]
OUT  = '/Users/sushil/trading/futures/factory'
srp.GRADUATED_RVOL = True; srp.RVOL_GRAD_FLOOR = 0.70; srp.REGIME_AWARE_EXITS = True
srp.NO_OVN_SKIP = True; srp.REV_EXIT = (2, 0.30, 120.0); srp.PARTIAL_TAKE_PTS = 150.0
srp.MAX_DAILY_TRADES = 5; srp.HERO_GATE_ENABLED = True; srp.BASE_STOP_PTS = 200.0
srp.SHORT_MAX_CONTRACTS = 1
srp.IB_CLASSIFY_AT_1030 = True
srp.TIDE_GATE = (MODE == "on")

ab    = srp.load_bars('MNQ', start='2021-02-11', end='2026-09-25')
rth   = srp.filter_ny_session(ab)
dates = sorted(d for d in set(rth.index.date) if _dt.date(2021, 6, 1) <= d <= _dt.date(2026, 9, 24))
r  = srp._run_scenario(ab, dates, False, False, 'NY', verbose=False, large_ib_gate_pts=0.0, early_ib_pts=0.0)
df = r['df'].copy()
df.to_csv(f'{OUT}/_tideab2_{MODE}.csv', index=False)
y = pd.to_datetime(df['date'].astype(str)).dt.year
daily = df.groupby('date').pnl.sum(); eq = daily.cumsum()
print(f'PARITY TIDE={MODE}  n={len(df)}  total={df.pnl.sum():+,.0f}  maxDD={(eq-eq.cummax()).min():+,.0f}  '
      f'worstDay={daily.min():+,.0f}', flush=True)
print('  per year: ' + ' '.join(f'{a}:{df.loc[y==a,"pnl"].sum():+,.0f}' for a in sorted(y.unique())), flush=True)
