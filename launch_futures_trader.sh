#!/bin/bash
# Runs tc_trader.py against the existing paper bridge (port 8000, DU9952463).
# Will switch to ORDER_BRIDGE=8002 once DUQ640500 gateway is approved by IBKR.
TRADING_DIR="$(cd "$(dirname "$0")" && pwd)"

set -a
source "$TRADING_DIR/.env"
set +a

pkill -f "$TRADING_DIR/futures/tc_trader.py" 2>/dev/null
sleep 2
exec "$TRADING_DIR/venv/bin/python" -u "$TRADING_DIR/futures/tc_trader.py"
