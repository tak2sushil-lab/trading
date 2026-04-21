@echo off
title TRADING SYSTEM — SHUTDOWN
color 0C

echo.
echo ==========================================
echo   TRADING SYSTEM — SHUTTING DOWN
echo ==========================================
echo.

cd /d C:\trading

REM ── Send final WhatsApp summary before closing ─────────
echo Sending final summary to WhatsApp...
python -c "
from dotenv import load_dotenv
load_dotenv()
import os
from twilio.rest import Client
from database import get_daily_pnl, get_win_rate

TWILIO_SID      = os.getenv('TWILIO_SID')
TWILIO_TOKEN    = os.getenv('TWILIO_TOKEN')
TWILIO_WHATSAPP = os.getenv('TWILIO_WHATSAPP')
MY_WHATSAPP     = os.getenv('MY_WHATSAPP')

twilio = Client(TWILIO_SID, TWILIO_TOKEN)
daily  = get_daily_pnl()
wr     = get_win_rate(days=30)

win_rate = (daily['wins'] / daily['trades'] * 100) if daily['trades'] > 0 else 0
emoji    = '✅' if daily['pnl'] > 0 else '❌'

msg = (
    f'{emoji} Trading system shutting down\n'
    f'--- Final Summary ---\n'
    f'Trades today: {daily[\"trades\"]} | Wins: {daily[\"wins\"]}\n'
    f'Win rate today: {win_rate:.0f}%%\n'
    f'P&L today: \${daily[\"pnl\"]:+.2f}\n'
    f'30d win rate: {wr:.0f}%%\n'
    f'System offline. See you tomorrow!'
)
twilio.messages.create(from_=TWILIO_WHATSAPP, to=MY_WHATSAPP, body=msg)
print('Final summary sent to WhatsApp')
"

echo.
echo Closing all trading terminals...
timeout /t 3 /nobreak >nul

REM ── Close all trading terminal windows ────────────────────
taskkill /FI "WINDOWTITLE eq BRIDGE*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq SCHEDULER*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq TRADER*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq GUARDIAN*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq TUNNEL*" /T /F >nul 2>&1

echo.
echo ==========================================
echo   ALL SYSTEMS STOPPED
echo ==========================================
echo.
echo Final summary sent to WhatsApp.
echo Trading database saved.
echo.
timeout /t 5 /nobreak >nul
exit
