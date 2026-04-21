@echo off
title TRADING SYSTEM LAUNCHER
color 0A

echo.
echo ==========================================
echo   SUSHIL'S TRADING SYSTEM — STARTING UP
echo ==========================================
echo.
echo Starting all 5 components...
echo.

cd /d C:\trading

REM ── Terminal 1: bridge.py (IBKR connection) ──────────────
start "BRIDGE — IBKR Connection" cmd /k "color 0B && echo [BRIDGE] Starting IBKR bridge... && python bridge.py"
timeout /t 5 /nobreak >nul

REM ── Terminal 2: scheduler.py (master scheduler) ──────────
start "SCHEDULER — Master Scheduler" cmd /k "color 0E && echo [SCHEDULER] Starting master scheduler... && python scheduler.py"
timeout /t 3 /nobreak >nul

REM ── Terminal 3: trader.py (trade monitor) ────────────────
start "TRADER — Trade Monitor" cmd /k "color 0A && echo [TRADER] Starting trade monitor... && python trader.py"
timeout /t 3 /nobreak >nul

REM ── Terminal 4: day_guardian.py (P&L guardian) ───────────
start "GUARDIAN — Day Guardian" cmd /k "color 0D && echo [GUARDIAN] Starting day guardian... && python day_guardian.py"
timeout /t 3 /nobreak >nul

REM ── Terminal 5: tunnel.py (WhatsApp ngrok) ────────────────
start "TUNNEL — WhatsApp Tunnel" cmd /k "color 09 && echo [TUNNEL] Starting ngrok tunnel... && python tunnel.py"
timeout /t 3 /nobreak >nul

echo.
echo ==========================================
echo   ALL SYSTEMS STARTED SUCCESSFULLY
echo ==========================================
echo.
echo   BRIDGE    → IBKR connection (blue)
echo   SCHEDULER → Morning screen, tasks (yellow)
echo   TRADER    → WhatsApp replies + trades (green)
echo   GUARDIAN  → P&L limits, boat rule (purple)
echo   TUNNEL    → WhatsApp webhook (cyan)
echo.
echo   WhatsApp commands:
echo     YES      → approve next pick
echo     NO       → skip pick
echo     YES ALL  → approve all picks
echo     STATUS   → see P&L + open trades
echo     CANCEL   → cancel pending picks
echo.
echo   This window will close in 30 seconds.
echo   All trading terminals remain open.
echo.
timeout /t 30 /nobreak >nul
exit
