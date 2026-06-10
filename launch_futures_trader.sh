#!/bin/bash
# Runs tc_trader.py against TC Sandbox bridge (port 8002, DUQ640500).
# .env-tc is sourced after .env — overrides FUTURES_BRIDGE_URL, IBKR creds, Telegram.
TRADING_DIR="$(cd "$(dirname "$0")" && pwd)"

set -a
source "$TRADING_DIR/.env"
source "$TRADING_DIR/.env-tc"
set +a

pkill -f "$TRADING_DIR/futures/tc_trader.py" 2>/dev/null
sleep 2
exec "$TRADING_DIR/venv/bin/python" -u "$TRADING_DIR/futures/tc_trader.py"
