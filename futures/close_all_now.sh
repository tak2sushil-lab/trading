#!/bin/bash
# Emergency "pull the plug" — flattens live futures positions on request.
#
# Built Aug 3 2026 after a manual close went wrong: importing futures_trader.py
# directly (without the env vars the real launchd services set) let
# FUTURES_ACCOUNT_MODE silently default to 'TC' while bridge calls still hit
# IBKR (port 8000) — closed the real IBKR position but wrote the DB/state
# update to a TC trade row instead. This script bakes in the exact same env
# each launch_*.sh uses, so account scoping can't drift or be forgotten.
#
# Usage:
#   futures/close_all_now.sh          # close both IBKR and TC
#   futures/close_all_now.sh ibkr     # close IBKR only
#   futures/close_all_now.sh tc       # close TC only
#
# Calls the same _force_close_all() the bots run on their own "FUT CLOSE"
# Telegram command — verifies against the real broker qty first, cancels
# backup stops, sends one sized market order, confirms flat, then writes the
# DB/state update. Safe to re-run; a flat account is a no-op ("No open
# futures positions.").

set -e
TRADING_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-all}"

close_ibkr() {
  echo "=== Closing IBKR (port 8000, DU9952463) ==="
  ( set -a
    source "$TRADING_DIR/.env"
    FUTURES_BRIDGE_URL=http://localhost:8000
    FUTURES_ACCOUNT_MODE=IBKR
    FUTURES_STATE_FILE="$TRADING_DIR/futures/ibkr_state.json"
    set +a
    cd "$TRADING_DIR"
    venv/bin/python3 -c "
import sys; sys.path.insert(0, 'futures')
import futures_trader as ft
assert ft.ACCOUNT_MODE == 'IBKR', f'ACCOUNT_MODE={ft.ACCOUNT_MODE}, expected IBKR — abort'
assert ft.BRIDGE == 'http://localhost:8000', f'BRIDGE={ft.BRIDGE}, expected 8000 — abort'
print(f'[verified] ACCOUNT_MODE={ft.ACCOUNT_MODE}  BRIDGE={ft.BRIDGE}')
ft._force_close_all()
"
  )
}

close_tc() {
  echo "=== Closing TC (port 8002, DUQ640500) ==="
  ( set -a
    source "$TRADING_DIR/.env"
    source "$TRADING_DIR/.env-tc"
    set +a
    cd "$TRADING_DIR"
    venv/bin/python3 -c "
import sys; sys.path.insert(0, 'futures')
import tc_trader as tc
assert tc.ACCOUNT_MODE == 'TC', f'ACCOUNT_MODE={tc.ACCOUNT_MODE}, expected TC — abort'
assert tc.BRIDGE == 'http://localhost:8002', f'BRIDGE={tc.BRIDGE}, expected 8002 — abort'
print(f'[verified] ACCOUNT_MODE={tc.ACCOUNT_MODE}  BRIDGE={tc.BRIDGE}')
tc._force_close_all()
"
  )
}

case "$TARGET" in
  ibkr) close_ibkr ;;
  tc)   close_tc ;;
  all)  close_ibkr; echo; close_tc ;;
  *) echo "Usage: $0 [ibkr|tc|all]"; exit 1 ;;
esac

echo
echo "=== Final live positions ==="
echo -n "IBKR: "; curl -s http://localhost:8000/futures/position; echo
echo -n "TC:   "; curl -s http://localhost:8002/futures/position; echo
