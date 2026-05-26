#!/bin/bash
# Full regression suite — run after any parameter change or weekly on Saturday.
# Takes ~35 min. All 5 must pass before deploying parameter changes to prod.
set -e
cd "$(dirname "$0")"
PYTHON=venv/bin/python
PASS=0; FAIL=0
run() {
    local name="$1"; local file="$2"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Running: $name"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    if $PYTHON "$file"; then
        echo "  ✓ PASS: $name"
        PASS=$((PASS+1))
    else
        echo "  ✗ FAIL: $name"
        FAIL=$((FAIL+1))
    fi
}
echo "======================================================"
echo "  REGRESSION SUITE — $(date '+%Y-%m-%d %H:%M')"
echo "======================================================"
run "Bull strategy (full universe)"   backtest_strategy.py
run "Bear/short edge"                  backtest_bear.py
run "Walk-forward OOS (8 windows)"    backtest_walkforward.py
run "Stress test (crisis periods)"    backtest_stress.py
run "Monte Carlo (ruin risk)"         monte_carlo.py
echo ""
echo "======================================================"
echo "  RESULTS: $PASS passed, $FAIL failed"
echo "======================================================"
if [ $FAIL -gt 0 ]; then
    echo "  !! DO NOT deploy parameter changes until all pass !!"
    exit 1
fi
echo "  All green — safe to deploy."
