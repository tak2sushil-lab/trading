"""Pipeline confirmation: LONG-only + a daily-trend DAY FILTER, live exit stack.

The frame-filter version (dropping rows from the cached long-only book) is only an
UPPER BOUND — it never refills a freed position slot. This runs the real
`_run_scenario` over only the days the filter allows, so the book is rebuilt.

Day filter is fully causal: PREVIOUS day's close vs PREVIOUS day's MA, from
`_day_table.csv` (conditions.py:209-211 anchors both to `prev`).

argv: <ma spec e.g. 200 | 50,200>
"""
import sys, datetime as _dt
sys.path.insert(0, '/Users/sushil/trading')
import pandas as pd
import futures.sim_replay as srp

SPEC = sys.argv[1]
MAS  = [int(x) for x in SPEC.split(',')]
OUT  = '/Users/sushil/trading/futures/factory'

srp.GRADUATED_RVOL = True; srp.RVOL_GRAD_FLOOR = 0.70; srp.REGIME_AWARE_EXITS = True
srp.NO_OVN_SKIP = True; srp.MAX_DAILY_TRADES = 5; srp.HERO_GATE_ENABLED = True
srp.BASE_STOP_PTS = 200.0
srp.REV_EXIT = (2, 0.30, 120.0); srp.PARTIAL_TAKE_PTS = 150.0   # live exit stack, unchanged

_orig = srp.get_signals
def _patched(*a, **k):
    sig = _orig(*a, **k)
    for key in ('orb_bear', 'vwap_rejection', 'momentum_bear', 'open_play_bear', 'pm_bear'):
        sig[key] = False
    return sig
srp.get_signals = _patched

day = pd.read_csv(f'{OUT}/_day_table.csv')
ok = day
for n in MAS:
    ok = ok[ok[f'd_ma{n}'] > 0]
allowed = set(pd.to_datetime(ok['date']).dt.date)

ab   = srp.load_bars('MNQ', start='2021-03-01', end='2026-08-18')
rth  = srp.filter_ny_session(ab)
dates = sorted(d for d in set(rth.index.date)
               if d >= _dt.date(2021, 6, 1) and d in allowed)
print(f'days allowed by MA{SPEC} filter: {len(dates)}', flush=True)

r  = srp._run_scenario(ab, dates, False, False, 'NY', verbose=False)
df = r['df'].copy()
df.to_csv(f'{OUT}/_longonly_ma{SPEC.replace(",","_")}.csv', index=False)
y = pd.to_datetime(df['date'].astype(str)).dt.year
daily = df.groupby('date').pnl.sum(); eq = daily.cumsum()
print(f'LONG-ONLY + >MA{SPEC}  n={len(df)}  shorts={int((df.side=="SHORT").sum())}  '
      f'total={df.pnl.sum():+,.0f}  maxDD={(eq-eq.cummax()).min():+,.0f}  '
      f'worstDay={daily.min():+,.0f}', flush=True)
print('  per year: ' + ' '.join(f'{a}:{df.loc[y==a,"pnl"].sum():+,.0f}'
                                for a in sorted(y.unique())), flush=True)
