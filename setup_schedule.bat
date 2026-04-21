@echo off
title SETUP WINDOWS TASK SCHEDULER
color 0A

echo.
echo ==========================================
echo   SETTING UP AUTOMATIC DAILY SCHEDULE
echo ==========================================
echo.
echo This will schedule:
echo   8:00 AM  → Auto-start all trading terminals
echo   4:30 PM  → Auto-stop + send final summary
echo.
echo Run this script ONCE as Administrator.
echo.
pause

cd /d C:\trading

REM ── Create START task (8:00 AM weekdays) ─────────────────
schtasks /create /tn "TradingSystem_START" ^
  /tr "C:\trading\start_trading.bat" ^
  /sc weekly ^
  /d MON,TUE,WED,THU,FRI ^
  /st 08:00 ^
  /ru "%USERNAME%" ^
  /f ^
  /RL HIGHEST

echo.
if %ERRORLEVEL% EQU 0 (
    echo [OK] START task created — 8:00 AM weekdays
) else (
    echo [ERROR] Failed to create START task. Run as Administrator.
)

REM ── Create STOP task (4:30 PM weekdays) ──────────────────
schtasks /create /tn "TradingSystem_STOP" ^
  /tr "C:\trading\stop_trading.bat" ^
  /sc weekly ^
  /d MON,TUE,WED,THU,FRI ^
  /st 16:30 ^
  /ru "%USERNAME%" ^
  /f ^
  /RL HIGHEST

echo.
if %ERRORLEVEL% EQU 0 (
    echo [OK] STOP task created — 4:30 PM weekdays
) else (
    echo [ERROR] Failed to create STOP task. Run as Administrator.
)

echo.
echo ==========================================
echo   SCHEDULE SETUP COMPLETE
echo ==========================================
echo.
echo Tasks created:
echo   TradingSystem_START — runs 8:00 AM Mon-Fri
echo   TradingSystem_STOP  — runs 4:30 PM Mon-Fri
echo.
echo To view tasks: Open Task Scheduler app
echo To remove:     schtasks /delete /tn "TradingSystem_START" /f
echo                schtasks /delete /tn "TradingSystem_STOP" /f
echo.
echo IMPORTANT: Make sure your laptop does NOT sleep!
echo Run this command in any terminal to disable sleep:
echo   powercfg /change standby-timeout-ac 0
echo.
pause
