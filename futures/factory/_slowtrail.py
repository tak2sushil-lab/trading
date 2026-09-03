"""One shot per day, 1 contract, full-MLL stop, and a SLOWED trail.

Live trail (REGIME_AWARE_EXITS): CHOPPY/QUIET lock BE at +90 then tighten to a 35pt gap
once +200 is reached; TRENDING gets 550/110. A day that wants to travel 300-500pts is cut
at ~215 on any non-TRENDING day. This scales every activation threshold AND every trail gap
by M, so the trail stays further behind price and engages later.

argv: <stop_pts> <trail_multiplier> <rev_exit 0|1> <no_move 0|1> [start]
"""
import sys, os, datetime as _dt
sys.path.insert(0, '/Users/sushil/trading')
import pandas as pd
import futures.sim_replay as srp

S     = float(sys.argv[1])
M     = float(sys.argv[2])
REV   = sys.argv[3] == '1'
NOMV  = sys.argv[4] == '1'
START = sys.argv[5] if len(sys.argv) > 5 else '2026-01-01'
OUT   = '/Users/sushil/trading/futures/factory'

srp.GRADUATED_RVOL = True; srp.RVOL_GRAD_FLOOR = 0.70; srp.REGIME_AWARE_EXITS = True
srp.NO_OVN_SKIP = True; srp.MAX_DAILY_TRADES = 5; srp.HERO_GATE_ENABLED = True
srp.PARTIAL_TAKE_PTS = None          # inert at 1 contract anyway (needs contracts>=2)
srp.BASE_STOP_PTS = S
if 1500.0 / S < srp.MIN_RR:
    srp.BASE_TARGET_PTS = round(S * 1.5)

srp.REV_EXIT = (2, 0.30, 120.0) if REV else None
if not NOMV:
    srp.NO_MOVE_MINUTES = 10 ** 6    # never cut a trade just for sitting still

# ── slow the trail: push activations later AND keep the stop further behind ──
if M >= 999:                          # "no trail at all" — pure stop, ride to the close
    for k in srp.EXIT_PARAMS_BY_REGIME:
        srp.EXIT_PARAMS_BY_REGIME[k] = {'be_pts': 1e9, 'be_frac': 0.0,
                                        'wide_pts': 1e9, 'wide_gap': 1e9,
                                        'tight_pts': 1e9, 'tight_gap': 1e9}
else:
    for k, p in srp.EXIT_PARAMS_BY_REGIME.items():
        srp.EXIT_PARAMS_BY_REGIME[k] = dict(p,
            be_pts=p['be_pts'] * M, wide_pts=p['wide_pts'] * M, wide_gap=p['wide_gap'] * M,
            tight_pts=p['tight_pts'] * M, tight_gap=p['tight_gap'] * M)
    srp.BE_ACTIVATE_PTS *= M; srp.TRAIL_WIDE_PTS *= M; srp.TRAIL_WIDE_GAP *= M
    srp.TRAIL_TIGHT_PTS *= M; srp.TRAIL_TIGHT_GAP *= M

_ls = (_dt.date.fromisoformat(START) - _dt.timedelta(days=110)).isoformat()
ab = srp.load_bars('MNQ', start=_ls, end='2026-08-18')
rth = srp.filter_ny_session(ab)
dates = sorted(d for d in set(rth.index.date) if d >= _dt.date.fromisoformat(START))
r = srp._run_scenario(ab, dates, False, False, 'NY', verbose=False)
if r.get('df') is None or len(r['df']) == 0:
    print('ZERO TRADES', flush=True); sys.exit(0)
df = r['df'].copy()
tag = f'{int(S)}_m{M:g}_rev{int(REV)}_nm{int(NOMV)}' + ('' if START == '2021-06-01' else '_2026')
df.to_csv(f'{OUT}/_slow_{tag}.csv', index=False)
y = pd.to_datetime(df['date'].astype(str)).dt.year
print(f'{tag}  n={len(df)}  c={df.contracts.value_counts().to_dict()}  '
      f'pnl={df.pnl.sum():+,.0f}  exits={df.exit_reason.value_counts().head(5).to_dict()}', flush=True)
