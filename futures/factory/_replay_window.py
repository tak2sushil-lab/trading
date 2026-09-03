"""Replay a date window under (a) the live config and (b) the proposed one-shot config.
argv: <start> <end> <mode: live|new>
new = 1000pt stop, 1 contract (calc_contracts derives it), no trail, no rev-exit,
      no no-move, LONG only (every bear signal zeroed, the pm_bear pattern).
"""
import sys, datetime as _dt
sys.path.insert(0, '/Users/sushil/trading')
import pandas as pd
import futures.sim_replay as srp

START, END, MODE = sys.argv[1], sys.argv[2], sys.argv[3]
srp.GRADUATED_RVOL = True; srp.RVOL_GRAD_FLOOR = 0.70; srp.REGIME_AWARE_EXITS = True
srp.NO_OVN_SKIP = True; srp.MAX_DAILY_TRADES = 5; srp.HERO_GATE_ENABLED = True

if MODE == 'live':
    srp.BASE_STOP_PTS = 200.0; srp.REV_EXIT = (2, 0.30, 120.0); srp.PARTIAL_TAKE_PTS = 150.0
else:
    srp.BASE_STOP_PTS = 1000.0; srp.BASE_TARGET_PTS = 1500.0
    srp.REV_EXIT = None; srp.PARTIAL_TAKE_PTS = None; srp.NO_MOVE_MINUTES = 10 ** 6
    for k in srp.EXIT_PARAMS_BY_REGIME:
        srp.EXIT_PARAMS_BY_REGIME[k] = {'be_pts': 1e9, 'be_frac': 0.0, 'wide_pts': 1e9,
                                        'wide_gap': 1e9, 'tight_pts': 1e9, 'tight_gap': 1e9}
    _orig = srp.get_signals
    def _p(*a, **k):
        s = _orig(*a, **k)
        for key in ('orb_bear', 'vwap_rejection', 'momentum_bear', 'open_play_bear', 'pm_bear'):
            s[key] = False
        return s
    srp.get_signals = _p

_ls = (_dt.date.fromisoformat(START) - _dt.timedelta(days=110)).isoformat()
ab = srp.load_bars('MNQ', start=_ls, end=END)
rth = srp.filter_ny_session(ab)
dates = sorted(d for d in set(rth.index.date)
               if _dt.date.fromisoformat(START) <= d <= _dt.date.fromisoformat(END))
r = srp._run_scenario(ab, dates, False, False, 'NY', verbose=False)
df = r['df'] if r.get('df') is not None else pd.DataFrame()
out = f'/Users/sushil/trading/futures/factory/_win_{MODE}_{START}_{END}.csv'
if len(df):
    df.to_csv(out, index=False)
    print(f'{MODE.upper()}  trading days offered={len(dates)}  trades={len(df)}  '
          f'gross={df.pnl.sum():+,.0f}  net(after $6/c)={df.pnl.sum()-6*df.contracts.clip(lower=1).sum():+,.0f}', flush=True)
    print(df[['date','entry_time','side','setup','contracts','entry','exit','pnl','exit_reason']].to_string(index=False), flush=True)
else:
    print(f'{MODE.upper()}  trading days offered={len(dates)}  trades=0', flush=True)
