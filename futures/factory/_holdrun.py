"""ONE-SHOT HOLD-ALL-DAY design — full pipeline confirmation (Aug 25 2026).

The user's own design: enter once, hold as long as possible in the day, with a stop small
enough that a single loss cannot breach the DLL/MLL.
Frame result (atr_exit_lab ctx): first LONG of the day, hold to close, 300pt stop ->
+$8.5-9.1k, maxDD -$2,218, worst day -$606, green 5/6, vs baseline +$3,353 / -$7,715 / 2/6.
FIRST is genuinely special: it beat 100% of 300 random same-day picks; LAST loses -$2,562.

This runs it through the REAL pipeline instead of the frame, because the ATR-exit lab
overstated by 12x when its baseline was miscalibrated. Composition:
  MAX_DAILY_TRADES = 1        -> one entry per day (the first that qualifies)
  bear signals zeroed         -> LONG only (same pattern as _longonly.py / live pm_bear)
  trail + rev-exit + no-move disabled -> hold to close
  BASE_STOP_PTS = <stop>      -> the only exit besides EOD
argv: <stop_pts>
"""
import sys, datetime as _dt
sys.path.insert(0, '/Users/sushil/trading')
import pandas as pd
import futures.sim_replay as srp

S = float(sys.argv[1])
ONE_CONTRACT = (len(sys.argv) < 3) or sys.argv[2] != 'dyn'
OUT = '/Users/sushil/trading/futures/factory'

srp.GRADUATED_RVOL = True; srp.RVOL_GRAD_FLOOR = 0.70
srp.REGIME_AWARE_EXITS = True; srp.NO_OVN_SKIP = True; srp.HERO_GATE_ENABLED = True
srp.MAX_DAILY_LOSS = 1250.0
srp.BASE_STOP_PTS = S
srp.MAX_DAILY_TRADES = 1                      # <- one shot per day
if 1500.0 / S < srp.MIN_RR:
    srp.BASE_TARGET_PTS = round(S * 1.5)
# hold to close: no trail, no reversal exit, no no-move, no partial
srp.REV_EXIT = None; srp.PARTIAL_TAKE_PTS = None; srp.NO_MOVE_MINUTES = 10 ** 6
for k in srp.EXIT_PARAMS_BY_REGIME:
    srp.EXIT_PARAMS_BY_REGIME[k] = {'be_pts': 1e9, 'be_frac': 0.0, 'wide_pts': 1e9,
                                    'wide_gap': 1e9, 'tight_pts': 1e9, 'tight_gap': 1e9}
_orig = srp.get_signals
def _patched(*a, **k):
    sig = _orig(*a, **k)
    for key in ('orb_bear', 'vwap_rejection', 'momentum_bear', 'open_play_bear', 'pm_bear'):
        sig[key] = False
    return sig
srp.get_signals = _patched

# The user's design is explicitly 1 CONTRACT so a single stop-out cannot breach the DLL/MLL.
# Left alone the pipeline sizes 1-2 via the hero ladder; at a 300pt stop that is $1,200 of
# single-trade risk, which breaches TC's $1,000 DLL. The first pipeline run showed exactly
# that: contracts_max=2, worstTrade -$1,213, 2 DLL days. Force 1 so the test matches the
# design as specified.
if ONE_CONTRACT:
    srp.calc_contracts = lambda *_a, **_k: 1
    srp.contracts_from_regime_score = lambda h_score, regime, cc, h5_fib=None: (0 if cc == 0 else 1)

ab = srp.load_bars('MNQ', start='2021-03-01', end='2026-08-18')
rth = srp.filter_ny_session(ab)
dates = sorted(d for d in set(rth.index.date) if d >= _dt.date(2021, 6, 1))
print(f"ONE-SHOT hold-all-day | stop={S:.0f}pt | contracts={'FORCED 1' if ONE_CONTRACT else 'dynamic 1-2'} | {len(dates)} sessions", flush=True)
df = srp._run_scenario(ab, dates, False, False, 'NY', verbose=False)['df'].copy()
df.to_csv(f'{OUT}/_hold_{int(S)}.csv', index=False)
df['net'] = df.pnl - 6.0 * df.contracts.clip(lower=1)
y = pd.to_datetime(df.date.astype(str)).dt.year
dd = df.groupby('date').net.sum().sort_index(); eq = dd.cumsum()
print(f"  n={len(df)} net=${df.net.sum():+,.0f} maxDD=${(eq-eq.cummax()).min():+,.0f} "
      f"worstTrade=${df.net.min():+,.0f} worstDay=${dd.min():+,.0f} "
      f"DLLdays={int((dd<=-1000).sum())} contracts_max={int(df.contracts.max())}", flush=True)
print("  per year: " + " ".join(f"{a}:{df.loc[y==a,'net'].sum():+,.0f}" for a in sorted(y.unique())), flush=True)
print(f"  exits: {df.exit_reason.value_counts().to_dict()}", flush=True)
