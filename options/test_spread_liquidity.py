"""Pinned regression test for the Spread Toll Gate (Aug 3 2026).

Quotes below were recorded LIVE on Jul 29/31 2026 during the diagnosis of the
old per-leg <=15%-of-mid gate (which failed 37/37 candidates Jul 22-Aug 3).
If _spread_liquidity or LIQ_COST_PCT_MAX changes, these expectations define
the behavior that must survive: liquid mega-cap spreads pass, marginal
small-cap spreads stay excluded.

Run: venv/bin/python options/test_spread_liquidity.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from options_trader import _spread_liquidity, LIQ_COST_PCT_MAX

CASES = [
    # (name, (leg_a bid, ask), (leg_b bid, ask), width, expect_ok)
    ("CAT bear call credit",  (7.80, 9.75),  (0.20, 3.05), 110.0, True),   # 4.4% of width
    ("AMAT bear call credit", (9.70, 14.25), (3.45, 7.15),  90.0, True),   # 9.2%
    ("COHR bear call credit", (5.80, 8.90),  (1.80, 4.10),  65.0, True),   # 8.3%
    ("IREN bear put debit",   (5.50, 7.10),  (0.80, 1.29),  13.0, False),  # 16.1%
]


def main() -> int:
    failures = 0
    for name, (b1, a1), (b2, a2), width, expect in CASES:
        liq = _spread_liquidity(b1, a1, b2, a2, width)
        if liq['ok'] != expect:
            failures += 1
            print(f"FAIL {name}: cost={liq['cost_pct']}% -> {liq['ok']}, expected {expect}")
        else:
            print(f"pass {name}: cost={liq['cost_pct']}% (threshold {LIQ_COST_PCT_MAX}%)")

    z = _spread_liquidity(0, 0, 0, 0, 0)
    if z['ok'] is not False:
        failures += 1
        print("FAIL zero-width spread must fail closed")
    z2 = _spread_liquidity(1.0, 1.0, 2.0, 2.0, 10.0)
    if not (z2['ok'] and z2['cost_pct'] == 0.0):
        failures += 1
        print("FAIL zero-bid-ask spread must pass with cost 0")

    print("ALL PASS" if failures == 0 else f"{failures} FAILURE(S)")
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
