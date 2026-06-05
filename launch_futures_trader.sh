#!/bin/bash
# Wrapper for launchd — kills any stale futures_trader for THIS directory, starts fresh.
TRADING_DIR="$(cd "$(dirname "$0")" && pwd)"
pkill -f "$TRADING_DIR/futures/futures_trader.py" 2>/dev/null
sleep 2
exec "$TRADING_DIR/venv/bin/python" -u "$TRADING_DIR/futures/futures_trader.py"
