#!/bin/bash
# Wrapper for launchd — kills stale auto_trader before starting fresh
pkill -f "auto_trader.py" 2>/dev/null
sleep 2
exec /Users/sushil/trading/venv/bin/python -u /Users/sushil/trading/auto_trader.py
