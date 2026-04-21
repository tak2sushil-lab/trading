# AI-Powered Swing Trading System

An automated swing trading system built on Interactive Brokers (paper account), combining technical analysis, market regime detection, ML-based strategy optimization, and WhatsApp-based human-in-the-loop execution.

---

## Business Concept

The system acts as an AI trading assistant that does the heavy lifting — scanning, scoring, and monitoring — while the human trader retains final execution authority via WhatsApp. Each morning it surfaces the top 5 trade candidates ranked by confidence. The trader approves or rejects with a simple YES/NO. The system then manages the trade autonomously (stops, trailing, partial exits) and delivers a P&L summary at end of day.

```mermaid
graph LR
    A([Market Opens<br/>9:30am ET]) --> B[AI Scans 76+ Stocks]
    B --> C[Top 5 Picks Ranked<br/>by Confidence Score]
    C --> D[WhatsApp Notification<br/>to Trader]
    D --> E{Trader Approves?}
    E -- YES --> F[Auto Entry via IBKR]
    E -- NO --> G[Skip Trade]
    F --> H[Autonomous Management<br/>Stops · Trailing · Partials]
    H --> I{Profit Target Hit?}
    I -- Market Strong --> J[Extend Target +50%]
    I -- Market Choppy --> K[Dock Boat / Exit]
    H --> L[EOD Summary<br/>WhatsApp + Voice]
    L --> M[Nightly ML Learning<br/>Update Strategy Weights]
    M --> A
```

---

## Architecture

```mermaid
graph TB
    subgraph External["External Services"]
        IBKR[IB Gateway<br/>Port 4002 Paper<br/>Port 4001 Live]
        YF[Yahoo Finance<br/>Historical OHLCV]
        TWILIO[Twilio<br/>WhatsApp API]
        CLAUDE[Anthropic<br/>Claude API]
        NGROK[ngrok<br/>Webhook Tunnel]
    end

    subgraph Core["Core Engine — auto_trader.py"]
        AT[Auto Trader<br/>Single Process 24/7]
        SCHED[APScheduler<br/>Cron Tasks]
    end

    subgraph Analysis["Analysis Layer"]
        SCR[screener.py<br/>Morning Scanner 8:30am]
        CAT[catalyst_detector.py<br/>Event Scanner 8:15am]
        REG[regime_detector.py<br/>Market Regime SPY+VIX]
        FVG[fvg_detector.py<br/>Fair Value Gaps + S/R]
        SR[strategy_router.py<br/>Per-Stock Strategy]
    end

    subgraph Risk["Risk & Learning"]
        DG[day_guardian.py<br/>P&L Guardian + Boat Rule]
        LRN[learner.py<br/>Nightly ML Engine]
        BT[backtest.py<br/>Strategy Backtester v4]
    end

    subgraph Infra["Infrastructure"]
        BRIDGE[bridge.py<br/>FastAPI REST Gateway]
        DB[(trades.db<br/>SQLite)]
        TUNNEL[tunnel.py<br/>ngrok Manager]
    end

    subgraph Notify["Notifications"]
        WA[WhatsApp<br/>Alerts + Commands]
        VOICE[pyttsx3<br/>Voice Summaries]
    end

    IBKR <-->|ib_async| BRIDGE
    YF -->|yfinance fallback| SCR & CAT & BT
    CLAUDE -->|SDK| AT
    TWILIO <-->|REST| BRIDGE
    NGROK <-->|tunnel| TUNNEL

    AT --> SCHED
    SCHED --> SCR & CAT & DG & LRN
    SCR --> SR --> AT
    CAT --> AT
    REG --> AT & DG
    FVG --> SR

    AT <-->|HTTP| BRIDGE
    BRIDGE -->|orders / quotes| IBKR
    AT --> DB
    LRN --> DB
    DG --> DB

    BRIDGE --> WA
    AT --> WA & VOICE
    TUNNEL --> BRIDGE
    WA -->|YES/NO commands| BRIDGE
```

---

## Data Flow

```mermaid
flowchart TD
    subgraph Morning["Morning Cycle — 8:15–9:00am ET"]
        A1[catalyst_detector.py<br/>Scans 76 stocks] --> A2{Gap >3%?<br/>Volume >5x?<br/>Earnings beat >5%?}
        A2 -- Yes --> A3[Catalyst Pick Added]
        A2 -- No --> A4[Skip]

        B1[screener.py<br/>Scans 22 FINAL_UNIVERSE] --> B2[Score Each Stock<br/>RSI + Volume + Momentum<br/>MA Position + Tech Boost]
        B2 --> B3[Rank by Confidence]
        A3 --> B3
        B3 --> B4[Top 5 Picks]
        B4 --> B5[WhatsApp Message<br/>to Trader]
    end

    subgraph Entry["Entry Decision"]
        B5 --> C1{Trader Reply}
        C1 -- YES --> C2[Hard Filters Check<br/>SPY green · Stock up today<br/>Above MA · Volume ≥1.8x<br/>RSI <80 · Not near earnings]
        C1 -- NO --> C3[Trade Skipped]
        C2 -- All Pass --> C4[Calculate Position<br/>$400/trade · ATR stop<br/>Min R:R 2.5]
        C2 -- Any Fail --> C3
    end

    subgraph Execution["Order Execution"]
        C4 --> D1[bridge.py<br/>FastAPI Gateway]
        D1 --> D2[IB Gateway<br/>MARKET or LIMIT order]
        D2 --> D3[Order Filled]
        D3 --> D4[Log to trades.db<br/>entry price · shares · stop · target]
    end

    subgraph Monitor["Position Monitoring — Every 5 min"]
        D4 --> E1[auto_trader.py<br/>Monitor Loop]
        E1 --> E2[Fetch Live Quote<br/>via bridge.py]
        E2 --> E3{Conditions?}
        E3 -- Hit +4.5% target --> E4[Exit 50% Position<br/>Trail remaining 50%]
        E3 -- Hit ATR stop --> E5[Full Exit — Stop Loss]
        E3 -- Momentum fade<br/>drop >1×ATR from high --> E6[Full Exit — Fade]
        E3 -- 3 days flat --> E7[Time Stop Exit]
        E3 -- Still open --> E8[Update Trailing Stop<br/>1.5×ATR below session high]
        E8 --> E1
    end

    subgraph Regime["Regime Overlay — day_guardian.py"]
        F1[SPY daily change<br/>VIX level] --> F2{Regime?}
        F2 -- STRONG → 0.5%+ SPY, VIX<15 --> F3[Extend target +50%<br/>Hold winners longer]
        F2 -- NORMAL --> F4[Standard rules apply]
        F2 -- CAUTIOUS / CHOPPY --> F5[Dock boat at profit target<br/>Tighten stops]
        F2 -- WEAK → SPY <-0.5% --> F6[Hard stop: exit all<br/>if daily loss >$50 CAD]
        F3 & F4 & F5 & F6 --> E1
    end

    subgraph Evening["Nightly Cycle — 4:30–11:00pm ET"]
        G1[EOD Summary<br/>4:30pm] --> G2[Voice P&L Report<br/>pyttsx3]
        G2 --> G3[WhatsApp Day Summary]
        G3 --> G4[learner.py runs 11pm<br/>Analyze all today's trades]
        G4 --> G5[RSI range performance<br/>Volume level performance<br/>Sector wins/losses<br/>Earnings proximity]
        G5 --> G6[Update strategy_weights<br/>in trades.db]
        G6 --> G7[Weights used by screener<br/>next morning]
    end

    D4 --> G1
    E4 & E5 & E6 & E7 --> G1
```

---

## Strategy Flow

```mermaid
flowchart LR
    subgraph Universe["Stock Universe"]
        U1[BLUE_CHIPS<br/>13 stocks]
        U2[PROVEN_STOCKS<br/>14 stocks]
        U3[FINAL_UNIVERSE<br/>23 stocks — live trading]
        U4[CATALYST_UNIVERSE<br/>76 stocks — catalyst scan]
    end

    subgraph Router["strategy_router.py"]
        R1{FVG present?}
        R2{Trend above 50MA?}
        R3{Gap-up catalyst?}
        R4{SBS breakout?}

        R1 -- Yes --> S1[FVG_FILL Strategy]
        R2 -- Yes --> S2[TREND_PULLBACK Strategy]
        R3 -- Yes --> S3[GAP_GO Strategy]
        R4 -- Yes --> S4[SBS_BREAKOUT Strategy]
    end

    subgraph Signals["Entry Signals"]
        S1 --> SIG[Confidence Score<br/>0–100]
        S2 --> SIG
        S3 --> SIG
        S4 --> SIG
        SIG --> THRESH{Score ≥75?}
        THRESH -- Yes --> TRADE[Trade Candidate]
        THRESH -- No --> DROP[Filtered Out]
    end

    U3 --> R1 & R2 & R4
    U4 --> R3
```

---

## ML Learning Loop

```mermaid
flowchart TD
    A[Trade Closed<br/>Entry + Exit logged to DB] --> B[learner.py<br/>runs nightly 11pm ET]

    B --> C1[RSI Range Analysis<br/>Which RSI band wins most?]
    B --> C2[Volume Level Analysis<br/>What multiplier performs best?]
    B --> C3[Sector Performance<br/>Last 14 days by sector]
    B --> C4[Earnings Proximity<br/>Near vs far from earnings]

    C1 --> D{Win Rate by Category}
    C2 --> D
    C3 --> D
    C4 --> D

    D -- Win rate >65% --> E[Weight = 1.5 — Important]
    D -- Win rate 45–65% --> F[Weight = 1.0 — Neutral]
    D -- Win rate <45% --> G[Weight = 0.7 — Reduce]

    E & F & G --> H[(trades.db<br/>strategy_weights table)]
    H --> I[screener.py reads weights<br/>next morning]
    I --> J[Adjusted confidence scores<br/>on next scan]
```

---

## Scheduling Timeline

```mermaid
gantt
    title Daily Task Schedule (Eastern Time)
    dateFormat HH:mm
    axisFormat %H:%M

    section Pre-Market
    Catalyst Scan (76 stocks)       :a1, 08:15, 15m
    Morning Screen (22 stocks)      :a2, 08:30, 15m
    Voice Summary + WhatsApp        :a3, 09:00, 5m
    MSFT Auto-Buy (Mon only)        :a4, 09:31, 5m

    section Market Hours
    Trade Entry Window              :crit, active, b1, 09:31, 330m
    Position Monitor (every 5 min) :b2, 09:31, 390m
    WhatsApp Poll (every 15 min)   :b3, 09:31, 390m
    Stop-Loss Check (every 30 min) :b4, 09:31, 390m
    Day Guardian P&L Check         :b5, 09:31, 390m

    section Post-Market
    No New Entries After            :milestone, c1, 15:00, 0m
    Market Close                    :milestone, c2, 16:00, 0m
    EOD P&L Summary + Voice        :c3, 16:30, 10m

    section Overnight
    Nightly ML Learning Cycle       :d1, 23:00, 30m
```

---

## Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| Language | Python 3.11 | Core runtime |
| Broker | IB Gateway (ib_async) | Order execution, live quotes |
| Market Data | yfinance (fallback) | Historical OHLCV, ticker info |
| Technical Analysis | `ta` library | RSI, MAs, ATR, volume indicators |
| Web API | FastAPI + uvicorn | REST gateway for IBKR bridge |
| AI Reasoning | Anthropic Claude SDK | Trade reasoning (experimental) |
| Messaging | Twilio WhatsApp API | Alerts + human-in-the-loop |
| Tunnel | pyngrok (ngrok) | Expose webhook to internet |
| Scheduling | APScheduler | Cron-style task automation |
| Persistence | SQLite3 | Trades, P&L, strategy weights |
| Data Processing | pandas + numpy | OHLCV manipulation, statistics |
| Voice | pyttsx3 | End-of-day spoken summaries |
| Backtesting | `backtesting` library + matplotlib | Historical strategy validation |
| Config | python-dotenv (.env) | API keys, IBKR settings |

---

## Key Configuration

| Parameter | Value | Notes |
|---|---|---|
| Capital per trade | $400 | Fixed position size |
| Max open trades | 50 | Paper testing mode |
| Min risk:reward | 2.5 | Filters low-quality setups |
| Profit target | +4.5% | 50% exit here, trail remainder |
| Stop loss | −3.0% / 1.5×ATR | Whichever is tighter |
| Max RSI entry | 80 | No overbought entries |
| Min volume ratio | 1.8× | Volume confirmation required |
| No entry after | 3:00pm ET | Avoids end-of-day traps |
| Daily loss limit | $50 CAD | Hard stop, exit all positions |
| Daily profit target | $80 CAD | Boat rule trigger threshold |
| IBKR port (paper) | 4002 | Switch to 4001 for live |

---

## Backtesting Results (v4 — 2024–2026)

| Metric | Result |
|---|---|
| Total Trades | 173 |
| Win Rate | 62.4% |
| Average P&L | $3.18/trade |
| Total P&L | $550.09 |
| Best Performer | AAPL (100% win, 4/4) |
| Top Sector | SEMI (73% win rate) |
| Avoid | NVDA (0% — 3 losses), AFRM (42.9%) |

**Focus stocks (top 11 by backtest):** AAPL, CNQ, PLTR, COHR, HOOD, NUTX, LITE, VST, ITA, ORCL, TOST

---

## How to Run

### Prerequisites
- IB Gateway running on port 4002 (paper) or 4001 (live)
- Python 3.11 with all dependencies
- `.env` file configured with API keys

### Start All Services
```bat
start_trading.bat
```

This opens 5 terminals:
1. `python bridge.py` — FastAPI REST gateway
2. `python auto_trader.py` — Core trading engine (absorbs scheduler + trader)
3. `python day_guardian.py` — P&L risk guardian
4. `python tunnel.py` — ngrok WhatsApp webhook
5. _(legacy scheduler/trader — retired, absorbed by auto_trader.py)_

### WhatsApp Commands
| Command | Action |
|---|---|
| `YES` | Approve the latest trade pick |
| `NO` | Reject the latest trade pick |
| `YES ALL` | Approve all pending picks |
| `STATUS` | Get current positions + P&L |
| `CANCEL` | Cancel pending order |

---

## Project Structure

```
c:/trading/
├── auto_trader.py        # Core engine — entry, monitoring, scheduling
├── bridge.py             # FastAPI gateway for IBKR + WhatsApp
├── database.py           # SQLite trade logging + strategy weights
├── screener.py           # Morning stock scanner (8:30am)
├── catalyst_detector.py  # Event/gap-up scanner (8:15am)
├── regime_detector.py    # Market regime (STRONG/NORMAL/WEAK/CHOPPY)
├── fvg_detector.py       # Fair Value Gap + Support/Resistance
├── strategy_router.py    # Per-stock strategy assignment
├── learner.py            # Nightly ML weight updater
├── backtest.py           # Historical strategy backtester (v4)
├── watchlist.py          # Stock universe definitions
├── day_guardian.py       # P&L guardian + "boat rule"
├── tunnel.py             # ngrok tunnel manager
├── trades.db             # SQLite database
├── .env                  # API keys + config
├── start_trading.bat     # Windows multi-terminal launcher
└── logs/                 # Per-process log files
```

---

*Paper trading on Interactive Brokers — Brampton, ON*
