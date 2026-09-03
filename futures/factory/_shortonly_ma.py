"""Pipeline confirmation of the SHORT leg of the symmetric daily-tide rule.

SHORT only, only on days whose PREVIOUS close was BELOW its PREVIOUS 200-day MA,
with shorts hard-capped at 1 contract (the conviction ladder is anti-predictive on
the short side: SHORT-2c is negative in 5 of 6 years, -$85/trade, while SHORT-1c is
+$10/trade).

Live exit stack unchanged (200pt stop, regime trail, rev-exit, partial).
argv: <ma, e.g. 200>  [--no-cap]
"""
import sys, datetime as _dt
sys.path.insert(0, '/Users/sushil/trading')
import pandas as pd
import futures.sim_replay as srp

MA   = int(sys.argv[1])
CAP  = '--no-cap' not in sys.argv
OUT  = '/Users/sushil/trading/futures/factory'

srp.GRADUATED_RVOL = True; srp.RVOL_GRAD_FLOOR = 0.70; srp.REGIME_AWARE_EXITS = True
srp.NO_OVN_SKIP = True; srp.MAX_DAILY_TRADES = 5; srp.HERO_GATE_ENABLED = True
srp.BASE_STOP_PTS = 200.0
srp.REV_EXIT = (2, 0.30, 120.0); srp.PARTIAL_TAKE_PTS = 150.0

# zero every BULL signal — the mirror of _longonly.py
_orig = srp.get_signals
def _patched(*a, **k):
    sig = _orig(*a, **k)
    for key in ('orb_bull', 'vwap_reclaim', 'momentum_bull', 'open_play_bull', 'pm_bull'):
        sig[key] = False   # KeyError here is intentional — a typo must not pass silently
    return sig
srp.get_signals = _patched

if CAP:                                   # hard 1-contract cap (short-side only run)
    _cfrs = srp.contracts_from_regime_score
    srp.contracts_from_regime_score = lambda *a, **k: min(_cfrs(*a, **k), 1)

day = pd.read_csv(f'{OUT}/_day_table.csv')
allowed = set(pd.to_datetime(day.loc[day[f'd_ma{MA}'] < 0, 'date']).dt.date)

ab    = srp.load_bars('MNQ', start='2021-03-01', end='2026-08-18')
rth   = srp.filter_ny_session(ab)
dates = sorted(d for d in set(rth.index.date)
               if d >= _dt.date(2021, 6, 1) and d in allowed)
print(f'days BELOW MA{MA}: {len(dates)}   contract cap: {CAP}', flush=True)

r  = srp._run_scenario(ab, dates, False, False, 'NY', verbose=False)
df = r['df'].copy()
tag = f'ma{MA}' + ('_cap1' if CAP else '')
df.to_csv(f'{OUT}/_shortonly_{tag}.csv', index=False)
y = pd.to_datetime(df['date'].astype(str)).dt.year
daily = df.groupby('date').pnl.sum(); eq = daily.cumsum()
print(f'SHORT-ONLY + <MA{MA} cap={CAP}  n={len(df)}  longs={int((df.side=="LONG").sum())}  '
      f'maxContracts={df.contracts.max()}  total={df.pnl.sum():+,.0f}  '
      f'maxDD={(eq-eq.cummax()).min():+,.0f}  worstDay={daily.min():+,.0f}', flush=True)
print('  per year: ' + ' '.join(f'{a}:{df.loc[y==a,"pnl"].sum():+,.0f}'
                                for a in sorted(y.unique())), flush=True)
