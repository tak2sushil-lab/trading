"""Stop-width A/B on the live-parity Tide-on book (Sep 29 2026). argv: stop points.
Same script, same bars, same flags as _tideab3 -- ONE variable: BASE_STOP_PTS."""
import sys, datetime as _dt
sys.path.insert(0, '/Users/sushil/trading')
import futures.sim_replay as srp
STOP = float(sys.argv[1]); OUT = '/Users/sushil/trading/futures/factory'
srp.GRADUATED_RVOL = True; srp.RVOL_GRAD_FLOOR = 0.70; srp.REGIME_AWARE_EXITS = True
srp.NO_OVN_SKIP = True; srp.REV_EXIT = (2, 0.30, 120.0); srp.PARTIAL_TAKE_PTS = 150.0
srp.MAX_DAILY_TRADES = 5; srp.HERO_GATE_ENABLED = True; srp.BASE_STOP_PTS = STOP
srp.SHORT_MAX_CONTRACTS = 1; srp.IB_CLASSIFY_AT_1030 = True; srp.TIDE_GATE = True
srp.MAX_DAILY_LOSS = 100000.0     # no sim DLL halt: account rules are applied afterwards
ab = srp.load_bars('MNQ', start='2021-02-11', end='2026-09-25')
rth = srp.filter_ny_session(ab)
dates = sorted(d for d in set(rth.index.date) if _dt.date(2021, 6, 1) <= d <= _dt.date(2026, 9, 24))
r = srp._run_scenario(ab, dates, False, False, 'NY', verbose=False, large_ib_gate_pts=0.0, early_ib_pts=0.0)
r['df'].to_csv(f'{OUT}/_stopab_{int(STOP)}.csv', index=False)
print(f"STOP={STOP:.0f} n={len(r['df'])} raw={r['df'].pnl.sum():+,.0f}", flush=True)
