#!/bin/bash
# TC trader launcher — runs tc_trader.py against TC bridge (port 8002 → DUQ640500)
TRADING_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load shared vars, then TC overrides (FUTURES_BRIDGE_URL=http://localhost:8002)
set -a
source "$TRADING_DIR/.env"
source "$TRADING_DIR/.env-tc"
set +a

pkill -f "$TRADING_DIR/futures/tc_trader.py" 2>/dev/null
sleep 2
exec "$TRADING_DIR/venv/bin/python" -u "$TRADING_DIR/futures/tc_trader.py"
