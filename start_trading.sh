#!/bin/bash
# ==========================================
#   SUSHIL'S TRADING SYSTEM — MAC STARTUP
# ==========================================

TRADING_DIR="/Users/sushil/trading"
PYTHON="$TRADING_DIR/venv/bin/python"
LOGS="$TRADING_DIR/logs"

# Don't start if already running
if pgrep -f "bridge.py" > /dev/null; then
    echo "Already running. Use stop_trading.sh first."
    exit 1
fi

echo "Starting bridge..."
PYTHONUNBUFFERED=1 "$PYTHON" -u "$TRADING_DIR/bridge.py" >> "$LOGS/bridge.log" 2>&1 &
sleep 5

echo "Starting auto_trader..."
PYTHONUNBUFFERED=1 "$PYTHON" -u "$TRADING_DIR/auto_trader.py" >> "$LOGS/auto_trader.log" 2>&1 &
sleep 3

echo "Done. Both running in background."
echo "  Logs: tail -f $LOGS/bridge.log"
echo "        tail -f $LOGS/auto_trader.log"
