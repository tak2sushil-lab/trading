#!/bin/bash
# ==========================================
#   SUSHIL'S TRADING SYSTEM — MAC STARTUP
# ==========================================

TRADING_DIR="/Users/sushil/trading"
PYTHON="$TRADING_DIR/venv/bin/python"

echo ""
echo "=========================================="
echo "  SUSHIL'S TRADING SYSTEM — STARTING UP"
echo "=========================================="
echo ""

# ── Terminal 1: bridge.py (IBKR connection) ──────────────
osascript -e "
tell application \"Terminal\"
    do script \"cd $TRADING_DIR && PYTHONUNBUFFERED=1 $PYTHON -u bridge.py\"
    set custom title of front window to \"BRIDGE — IBKR\"
end tell"
sleep 5

# ── Terminal 2: auto_trader.py (scanner + monitor + scheduler) ──
osascript -e "
tell application \"Terminal\"
    do script \"cd $TRADING_DIR && PYTHONUNBUFFERED=1 $PYTHON -u auto_trader.py\"
    set custom title of front window to \"AUTO TRADER\"
end tell"

echo ""
echo "=========================================="
echo "  STARTED: bridge + auto_trader"
echo "=========================================="
echo ""
echo "  Telegram: STATUS | CANCEL | RESUME"
echo ""
