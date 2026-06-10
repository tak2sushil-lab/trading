#!/bin/bash
# Runs futures_trader.py against IBKR personal bridge (port 8000, DU9952463).
# Sources .env only — no .env-tc override.
# FUTURES_ACCOUNT_MODE=IBKR and FUTURES_STATE_FILE set here.
TRADING_DIR="$(cd "$(dirname "$0")" && pwd)"

set -a
source "$TRADING_DIR/.env"
FUTURES_BRIDGE_URL=http://localhost:8000
FUTURES_ACCOUNT_MODE=IBKR
FUTURES_STATE_FILE="$TRADING_DIR/futures/ibkr_state.json"
set +a

pkill -f "$TRADING_DIR/futures/futures_trader.py" 2>/dev/null
sleep 2
exec "$TRADING_DIR/venv/bin/python" -u "$TRADING_DIR/futures/futures_trader.py"
