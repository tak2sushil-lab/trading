"""Disable ORB_SHORT at source — sig['orb_bear'] = False — the same one-line pattern live
already uses for pm_bear (futures_trader.py:981). Then run the REAL pipeline, so freed slots
are genuinely refilled and daily counts are genuinely re-derived. This is the honest test of
"just drop ORB_SHORT"; the frame-filter estimate cannot model slot refill.

argv: <stop_pts> <no_trail 0|1>
"""
import sys, datetime as _dt
sys.path.insert(0, '/Users/sushil/trading')
import pandas as pd
import futures.sim_replay as srp

S  = float(sys.argv[1])
NT = sys.argv[2] == '1'
OUT = '/Users/sushil/trading/futures/factory'

srp.GRADUATED_RVOL = True; srp.RVOL_GRAD_FLOOR = 0.70; srp.REGIME_AWARE_EXITS = True
srp.NO_OVN_SKIP = True; srp.MAX_DAILY_TRADES = 5; srp.HERO_GATE_ENABLED = True
srp.BASE_STOP_PTS = S
if 1500.0 / S < srp.MIN_RR:
    srp.BASE_TARGET_PTS = round(S * 1.5)
if NT:
    srp.REV_EXIT = None; srp.PARTIAL_TAKE_PTS = None; srp.NO_MOVE_MINUTES = 10 ** 6
    for k in srp.EXIT_PARAMS_BY_REGIME:
        srp.EXIT_PARAMS_BY_REGIME[k] = {'be_pts': 1e9, 'be_frac': 0.0, 'wide_pts': 1e9,
                                        'wide_gap': 1e9, 'tight_pts': 1e9, 'tight_gap': 1e9}
else:
    srp.REV_EXIT = (2, 0.30, 120.0); srp.PARTIAL_TAKE_PTS = 150.0

_orig = srp.get_signals
def _patched(*a, **k):
    sig = _orig(*a, **k)
    sig['orb_bear'] = False          # ← the whole change
    return sig
srp.get_signals = _patched

ab = srp.load_bars('MNQ', start='2021-03-01', end='2026-08-18')
rth = srp.filter_ny_session(ab)
dates = sorted(d for d in set(rth.index.date) if d >= _dt.date(2021, 6, 1))
r = srp._run_scenario(ab, dates, False, False, 'NY', verbose=False)
df = r['df'].copy()
tag = f'{int(S)}_{"notrail" if NT else "livetrail"}'
df.to_csv(f'{OUT}/_noorb_{tag}.csv', index=False)
y = pd.to_datetime(df['date'].astype(str)).dt.year
print(f'NO-ORB_SHORT {tag}  n={len(df)}  setups={df.setup.value_counts().to_dict()}', flush=True)
print('  per year: ' + ' '.join(f'{a}:{df.loc[y==a,"pnl"].sum():+,.0f}' for a in sorted(y.unique())), flush=True)
