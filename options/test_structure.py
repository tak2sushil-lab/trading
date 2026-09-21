"""Pinned tests for options/structure.py — run after ANY change to strike
selection or the Edge Budget gate.  venv/bin/python options/test_structure.py"""
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import structure as st

fails = []
def check(name, cond, detail=''):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ''))
    if not cond: fails.append(name)

print("── expected_move: calendar days over 365 ──")
# the bug: sqrt(dte/252) overstates by sqrt(365/252)=1.204x
em = st.expected_move(100.0, 74.0, 36)
old = 100.0 * 0.74 * math.sqrt(36/252)
check("EM uses 365 not 252", abs(em - 100*0.74*math.sqrt(36/365)) < 0.01, f"{em}")
check("EM is 1.204x smaller than the old buggy value", abs(old/em - 1.204) < 0.01, f"old {old:.2f} new {em:.2f}")
check("EM guards zero/None dte", st.expected_move(100, 74, 0) == 0.0 and st.expected_move(100, 74, None) == 0.0)

print("\n── Black-Scholes sanity ──")
c = st.bs_call(100, 100, 30/365, 0.74); p = st.bs_put(100, 100, 30/365, 0.74)
check("ATM call > 0", c > 0, f"{c:.2f}")
check("put-call parity", abs((c - p) - (100 - 100*math.exp(-0.04*30/365))) < 1e-6)
check("deep ITM call ~ intrinsic + extrinsic", st.bs_call(100, 50, 30/365, 0.74) > 50)
check("call delta in (0,1)", 0 < st.call_delta(100, 100, 30/365, 0.74) < 1)
check("put delta negative", st.put_delta(100, 100, 30/365, 0.74) < 0)

print("\n── strike_for_delta inversion ──")
for d in (0.30, 0.50, 0.70, 0.85):
    K = st.strike_for_call_delta(100, 45/365, 0.74, d)
    back = st.call_delta(100, K, 45/365, 0.74)
    check(f"call delta {d} round-trips", abs(back - d) < 1e-4, f"K={K:.2f} delta={back:.4f}")
for d in (0.15, 0.30, 0.50):
    K = st.strike_for_put_delta(100, 45/365, 0.74, d)
    back = abs(st.put_delta(100, K, 45/365, 0.74))
    check(f"put delta {d} round-trips", abs(back - d) < 1e-4, f"K={K:.2f} |delta|={back:.4f}")
check("higher call delta -> lower strike",
      st.strike_for_call_delta(100,45/365,0.74,0.70) < st.strike_for_call_delta(100,45/365,0.74,0.35))

print("\n── pick_delta_strikes ordering ──")
ladder = [float(x) for x in range(50, 200, 5)]
kl, ks = st.pick_delta_strikes(ladder, 100, 74, 45, 0.70, 0.45, 'C')
check("call: long strike below short", kl is not None and ks is not None and kl < ks, f"{kl}/{ks}")
check("call: long leg near/inside the money", kl is not None and kl <= 100*1.02, f"long {kl} vs spot 100")
pl, ps = st.pick_delta_strikes(ladder, 100, 74, 45, 0.70, 0.45, 'P')
check("put: long strike above short", pl is not None and ps is not None and pl > ps, f"{pl}/{ps}")
check("coarse ladder that cannot express the structure returns (None,None)",
      st.pick_delta_strikes([100.0], 100, 74, 45, 0.70, 0.45, 'C') == (None, None))
check("missing inputs return (None,None)", st.pick_delta_strikes(ladder, 0, 74, 45, .7, .45) == (None, None))

print("\n── breakeven_move_pct ──")
check("call BE = (K+debit-S)/S", st.breakeven_move_pct(100, 102, 110, 3.0, 'C') == 5.0)
check("put BE signed in our favour (positive = move needed)",
      st.breakeven_move_pct(100, 98, 90, 3.0, 'P') == 5.0)
check("ITM call structure has a small breakeven",
      st.breakeven_move_pct(100, 95, 105, 6.0, 'C') == 1.0)
# the structure we actually traded: MRVL 227.22, long 250, debit 5.53
check("reproduces the MRVL trade's real breakeven",
      abs(st.breakeven_move_pct(227.22, 250.0, 270.0, 5.53, 'C') - 12.46) < 0.02,
      f"{st.breakeven_move_pct(227.22,250.0,270.0,5.53,'C')}%")
check("None inputs are safe", st.breakeven_move_pct(None, 100, 110, 1.0) is None)

print("\n── edge_budget gate ──")
g = st.edge_budget(2.0, 3.0, margin=1.25)
check("signal 3.0% clears 1.25x of 2.0% BE", g['ok'] and g['required_pct'] == 2.5)
g = st.edge_budget(2.0, 2.4, margin=1.25)
check("signal 2.4% fails the 2.5% requirement", not g['ok'], g['reason'])
g = st.edge_budget(8.1, 1.0, margin=1.25)
check("the book's real median BE 8.1% vs a ~1% signal is REJECTED", not g['ok'], g['reason'])
g = st.edge_budget(-0.5, 0.2, margin=1.25)
check("negative breakeven (theta-positive) passes a small signal", g['ok'], g['reason'])
check("margin TIGHTENS a negative breakeven, never loosens it",
      st.edge_budget(-0.5, 0.0, margin=1.25)['required_pct'] > -0.5,
      f"required {st.edge_budget(-0.5, 0.0, margin=1.25)['required_pct']} vs BE -0.5")
check("margin is monotone: more margin never makes the gate easier",
      st.edge_budget(2.0, 0, 1.5)['required_pct'] > st.edge_budget(2.0, 0, 1.25)['required_pct']
      and st.edge_budget(-2.0, 0, 1.5)['required_pct'] > st.edge_budget(-2.0, 0, 1.25)['required_pct'])
check("unknown inputs fail closed", not st.edge_budget(None, 3.0)['ok'] and not st.edge_budget(2.0, None)['ok'])

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}  ({len(fails)} failed)")
sys.exit(1 if fails else 0)
