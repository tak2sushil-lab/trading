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

# Step 1: Start IB Gateway via IBC (if not already up)
echo "Step 1/3: Launching IB Gateway..."
bash "$TRADING_DIR/launch_gateway.sh"
echo "Waiting 40s for Gateway to log in..."
sleep 40

# Step 2: Start bridge (auto-reconnect handles any remaining delay)
echo "Step 2/3: Starting bridge..."
PYTHONUNBUFFERED=1 "$PYTHON" -u "$TRADING_DIR/bridge.py" >> "$LOGS/bridge.log" 2>&1 &
sleep 5

# Step 3: Start auto trader
echo "Step 3/3: Starting auto_trader..."
PYTHONUNBUFFERED=1 "$PYTHON" -u "$TRADING_DIR/auto_trader.py" >> "$LOGS/auto_trader.log" 2>&1 &
sleep 3

echo ""
echo "✅ All systems running."
echo "   Gateway log:  tail -f $HOME/ibc/logs/ibc.log"
echo "   Bridge log:   tail -f $LOGS/bridge.log"
echo "   Trader log:   tail -f $LOGS/auto_trader.log"
