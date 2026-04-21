#!/bin/bash
# ==========================================
#   TRADING SYSTEM — MAC SHUTDOWN
# ==========================================

TRADING_DIR="/Users/sushil/trading"
PYTHON="$TRADING_DIR/venv/bin/python"

echo ""
echo "=========================================="
echo "  TRADING SYSTEM — SHUTTING DOWN"
echo "=========================================="
echo ""

# Send final Telegram summary
echo "Sending final summary via Telegram..."
"$PYTHON" -c "
import sys
sys.path.insert(0, '$TRADING_DIR')
from dotenv import load_dotenv
load_dotenv()
import os, requests
from database import get_daily_pnl, get_win_rate

TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT  = os.getenv('TELEGRAM_CHAT_ID')
if not TOKEN or not CHAT:
    print('No Telegram credentials — skipping summary')
    sys.exit(0)

daily = get_daily_pnl()
wr    = get_win_rate(days=30)
win_rate = (daily['wins'] / daily['trades'] * 100) if daily['trades'] > 0 else 0
emoji = '✅' if daily['pnl'] > 0 else '❌'
msg = (
    f'{emoji} Trading system shutting down\n'
    f'Trades: {daily[\"trades\"]} | Wins: {daily[\"wins\"]} | WR: {win_rate:.0f}%\n'
    f'P&L: \${daily[\"pnl\"]:+.2f} | 30d WR: {wr:.0f}%\n'
    f'System offline.'
)
requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',
              json={'chat_id': CHAT, 'text': msg}, timeout=10)
print('Final summary sent via Telegram')
"

echo ""
echo "Stopping trading processes..."
pkill -f "bridge.py"      2>/dev/null && echo "  Stopped: bridge.py"
pkill -f "auto_trader.py" 2>/dev/null && echo "  Stopped: auto_trader.py"

echo ""
echo "=========================================="
echo "  ALL SYSTEMS STOPPED"
echo "=========================================="
echo ""
