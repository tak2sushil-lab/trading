#!/bin/bash
# TC bridge launcher — FastAPI bridge on port 8002 → IBC port 4003 (DUQ640500)
# Separate from the main bridge on port 8000.

TRADING_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load shared vars, then TC overrides (BRIDGE_PORT=8002, IBKR_PORT=4003, IBKR_ACCOUNT=DUQ640500)
set -a
source "$TRADING_DIR/.env"
source "$TRADING_DIR/.env-tc"
set +a

BRIDGE_PORT="${BRIDGE_PORT:-8002}"

lsof -ti :"$BRIDGE_PORT" | xargs kill -9 2>/dev/null
sleep 2

echo "$(date): Starting TC bridge (port $BRIDGE_PORT → IBKR port $IBKR_PORT)..."
exec "$TRADING_DIR/venv/bin/python" -u "$TRADING_DIR/bridge.py"
