# TRADING SYSTEM CONTEXT
# Paste this at the start of any new Claude session to resume

## WHO I AM
- Name: Sushil, Brampton Ontario Canada
- IBKR Canada account — TFSA + Paper trading account
- Mac (migrated from Windows 11), Python 3.11, VS Code

## SYSTEM OVERVIEW
AI-powered trading system connecting:
  Claude AI → bridge.py → IB Gateway → IBKR Paper Account
  WhatsApp (Twilio sandbox) → bridge.py → Claude → trades

## FILES IN /Users/sushil/trading/
- bridge.py          → FastAPI server, IBKR + Claude + Telegram webhook
- auto_trader.py     → Master scheduler + trade monitor (absorbed scheduler.py + trader.py)
- day_guardian.py    → P&L risk guardian (boat rule, daily loss limit)
- screener.py        → Morning stock screener (8:30am ET)
- catalyst_detector.py → Scans for events/news/gap-ups (8:15am ET)
- learner.py         → Nightly learning engine
- database.py        → SQLite trade logging
- watchlist.py       → Stock universe (FINAL_UNIVERSE + CATALYST_UNIVERSE)
- backtest.py        → Strategy backtester (ran v1-v4)
- tunnel.py          → ngrok tunnel for webhooks
- .env               → All credentials (Anthropic, Telegram, IBKR)
- venv/              → Python 3.11 virtual environment (run scripts with venv/bin/python)
- requirements.txt   → All Python dependencies

## DAILY STARTUP (Mac)
  Option A — one command:  bash start_trading.sh   (opens 4 Terminal windows)
  Option B — VS Code terminals (activate venv first: source venv/bin/activate):
    Terminal 1: python bridge.py
    Terminal 2: python auto_trader.py
    Terminal 3: python day_guardian.py
    Terminal 4: python tunnel.py
  Shutdown: bash stop_trading.sh

## AUTOMATED SCHEDULE
  8:15am ET  → catalyst_detector.py scans 76 stocks for events
  8:30am ET  → screener.py scans FINAL_UNIVERSE (22 stocks)
  9:00am ET  → Morning portfolio summary (voice)
  9:31am Mon → Weekly MSFT buy ($250)
  Every 15m  → trader.py monitors open trades + checks WhatsApp replies
  Every 30m  → Stop loss check
  4:30pm ET  → Evening P&L summary on WhatsApp
  11:00pm    → learner.py nightly learning cycle

## WHATSAPP COMMANDS
  YES      → approve next pick
  NO       → skip next pick
  YES ALL  → approve all picks at once
  STATUS   → see open trades + P&L
  CANCEL   → cancel all pending picks

## TRADING STRATEGY (v4 FINAL)
  Backtest result: 63.1% win rate, $3.30 expectancy/trade
  Stop loss:    3.0% (trailing — moves up as price rises)
  Target:       4.5% (extended on STRONG market days)
  Min confidence: 75/100
  Min volume:   1.8x normal
  Market regime: STRONG/NORMAL/CAUTIOUS/WEAK detection
  Hold time:    2-5 days (swing trading)

## FINAL UNIVERSE (daily screen — 22 stocks)
  Blue chips: AAPL, MSFT, AMZN, GOOGL, META, TSLA, NVDA, AMD,
              JPM, GS, MS, NFLX, UBER, AVGO
  Proven:     COHR, NUTX, HPE, TOST, HOOD, LITE, ORCL, CLS,
              RKLB, VST, PLTR, NBIS, TSLA, AMD

## CATALYST UNIVERSE (event scanning — 76 stocks)
  All original watchlist US stocks + blue chips
  Detects: GAP_UP 3%+, VOLUME_SURGE 5x+, EARNINGS_BEAT 5%+, MOMENTUM_SURGE 5%+
  Catalyst picks appear FIRST in morning WhatsApp picks

## STRATEGY RULES
  - Only trade when SPY is green (market filter)
  - Must be up today (hard filter)
  - Must be above at least one MA (hard filter)
  - Volume must be 1.8x+ (hard filter)
  - Avoid stocks within 3 days of earnings
  - Trailing stop moves up as price rises
  - Min profit 1.0% before any exit (no tiny exits)
  - Strong market day = wider target, hold longer

## BACKTEST HISTORY
  v1: 49.4% win | $0.64/trade | 6419 trades (too many signals)
  v2: 52.6% win | $1.04/trade | 3719 trades (wider stops)
  v3: 58.9% win | $2.75/trade |  168 trades (laser focused)
  v4: 63.1% win | $3.30/trade |  179 trades (FINAL - blue chips added)

## AVOID STOCKS (underperformers)
  MSFT (too expensive >$400), LINE, SMCI, BEAM, S,
  NEE, CCJ, CRWD (v3), ENB, HBM, UUUU (v3)

## PAPER TRADING PLAN
  Week 1 (current): Trade freely, no PDT restrictions
  Week 2: Simulate real constraints ($1,000 capital, max 3 day trades/week)
  Week 3: Review results, tune if needed
  Week 4: Go/no-go decision for live trading

## KNOWN ISSUES FIXED
  - ORCL exited at +0.2% (too early) → fixed with min profit threshold
  - No trailing stop → fixed in trader.py v2
  - Market ignored → fixed with regime detection
  - Catalyst picks had KeyError → fixed by normalizing keys in screener
  - MSFT/TSLA missing → added to FINAL_UNIVERSE
  - pyaudio install error (Python 3.14) → downgraded to Python 3.11
  - ngrok needs restart after laptop reboot → run tunnel.py
  - Mac migration done (Apr 2026): venv at venv/, shell scripts replace .bat files

## CURRENT STATUS
  Week 1 paper trading active
  Day 1 result: 3 wins 2 losses, 60% win rate, P&L -$2.91
  (Loss due to early exits — fixed with trailing stops)
  Day 2: Starting fresh with improved trader.py + catalyst detector
  Day 1: 3W/2L, 60% win, -$2.91 (early exits fixed)
Day 2: catalyst detector working, trailing stops active
Files updated: trader.py v2, screener.py (catalyst fix), 
watchlist.py (blue chips), catalyst_detector.py

## NEXT STEPS
  - Monitor paper trading results daily
  - Backtest short selling strategy (when account reaches $2k+)
  - Set up VPS for 24/7 operation (week 2)
  - Create start.bat one-click launcher
  - Go live with $1,000 when paper results confirm 55%+ win rate

## TECH STACK
  Python 3.11, FastAPI, uvicorn, ib_async, anthropic SDK,
  twilio, yfinance, ta, APScheduler, pyngrok, sqlite3,
  python-dotenv, backtesting, matplotlib, pytz
