#!/bin/bash
# Wrapper for launchd — kills stale auto_trader for THIS directory only, then starts fresh.
# Using full path in pkill ensures UAT and Prod auto_traders don't kill each other.
TRADING_DIR="$(cd "$(dirname "$0")" && pwd)"
pkill -f "$TRADING_DIR/auto_trader.py" 2>/dev/null
sleep 2
exec "$TRADING_DIR/venv/bin/python" -u "$TRADING_DIR/auto_trader.py"
