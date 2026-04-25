#!/bin/bash
# Wrapper for launchd — kills stale bridge before starting fresh
pkill -f "bridge.py" 2>/dev/null
sleep 2
# Clear port 8000 if something else grabbed it
lsof -ti :8000 | xargs kill -9 2>/dev/null
sleep 1
exec /Users/sushil/trading/venv/bin/python -u /Users/sushil/trading/bridge.py
