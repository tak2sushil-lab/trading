# Options Trading System — Full Design
**Status: LOCKED May 9 2026 — Approved for build**

---

## Folder Structure

```
trading/
  options/
    news_engine.py         ← Phase 3: multi-source news + LLM classifier
    watchman.py            ← Phase 4: position monitor + trailing stops
    options_trader.py      ← Phase 5: calculator + order execution + Telegram
    backtester_options.py  ← Phase 6: pending (data source decision needed)
    docs/
      options_system_design.md  ← this file
```

Shared (zero changes to equity files):
```
  bridge.py       ← Phase 2: add 5 /options/* endpoints
  database.py     ← Phase 1: add 4 options tables
  trades.db       ← shared DB, all new tables additive
```

---

## System Architecture

```mermaid
graph TB
    subgraph SOURCES["News Sources — Free to start"]
        YF["yfinance\nFree · 15-30 min delay"]
        PG["Polygon.io\n$29/mo when ready · 2-5 min"]
        AV["Alpha Vantage\nFree tier · sentiment scores built-in"]
    end

    subgraph OPTIONS_LAYER["Options Layer — /options/"]
        NE["news_engine.py\nEvery 15 min during market hours\nFetch → Dedup → Claude API → Store → Alert"]
        WM["watchman.py\nEvery 15 min intraday · 4pm EOD\nMonitor positions · Update trailing stops · Threshold alerts"]
        OT["options_trader.py\nEvent-driven via Telegram\nCalculator · Order execution · Reallocation"]
        BT["backtester_options.py\n⏳ PENDING — data source TBD\nBlack-Scholes approx or IBKR historical chain"]
    end

    subgraph SHARED["Shared Infrastructure — no equity changes"]
        BR["bridge.py\n+5 /options/* endpoints\nchain · quote · iv_rank · order · portfolio"]
        DB["trades.db\n+4 options tables\ncatalyst_calendar · options_trades\noptions_snapshots · options_news"]
    end

    subgraph TELEGRAM["Telegram Bot"]
        EQ["Equity commands\nSTATUS · CANCEL · RESUME\n(unchanged)"]
        OP["Options commands\nOPT prefix · numbered replies\nOPT STATUS · OPT POSITIONS · OPT CALENDAR\nOPT CLOSE · OPT PAUSE · OPT RESUME · OPT ADD"]
    end

    IBKR["IB Gateway · Port 4002\nPaper account DU9952463"]
    LEARN["Learning Loop\nTrigger: trade 20 closed\nthen every 10 · advisory only"]
    CLAUDE["Claude API\nNews classification\n~$0.50/day at 250 headlines"]

    YF & PG & AV -->|"raw headlines"| NE
    NE -->|"dedup · classify"| CLAUDE
    CLAUDE -->|"structured output"| NE
    NE -->|"HIGH signal + open position"| OP
    NE -->|"store all MEDIUM/HIGH"| DB
    NE -->|"creates_future_event=YES"| DB
    OP -->|"1 / 2 / 3 / 4 / 5"| OT
    OT -->|"combo limit order"| BR
    BR -->|"ib_async Option contract"| IBKR
    IBKR -->|"fill confirmed"| BR
    BR -->|"fill details"| OT
    OT -->|"write options_trades"| DB
    WM -->|"fetch prices + greeks"| BR
    WM -->|"read + update stops"| DB
    WM -->|"write options_snapshots"| DB
    WM -->|"threshold alert"| OP
    DB -->|"20+ closed trades"| LEARN
    LEARN -->|"advisory report"| OP

    style OPTIONS_LAYER fill:#1a0a2e,stroke:#9b59b6,color:#ddd
    style SHARED fill:#0a1a2e,stroke:#2980b9,color:#ddd
    style SOURCES fill:#0a2010,stroke:#27ae60,color:#ddd
    style TELEGRAM fill:#2e1a0a,stroke:#e67e22,color:#ddd
```

---

## Trade Lifecycle

```mermaid
flowchart TD
    A(["News engine fires\nHIGH relevance signal"]) --> B{"IV Rank?"}

    B -->|"< 30%\nCatalyst 4-8 wk out"| C["LEAP route\nDelta 0.65-0.75 · 15-24 months\n1 option shown"]
    B -->|"30-45%\nCatalyst 1-3 wk"| D["Bull Spread route\n3 templates: conservative / balanced / aggressive"]
    B -->|"> 45%"| E(["SKIP\nPremium too expensive\nLog to options_news only"])

    C --> F["📱 Telegram alert\nNumbered options + SKIP"]
    D --> F

    F -->|"Reply: 1-4\nwithin 30 min"| G["options_trader.py\nRun liquidity check\nBid-ask width gate"]
    F -->|"Reply: 5 or timeout"| H(["Log to watchlist\nAlert when slot opens"])
    F -->|"Capital fully deployed"| I["Show reallocation candidates\nUser confirms which to close"]

    G -->|"Liquidity OK"| J["Combo limit order\nStart at mid · increment $0.05 · max slippage $0.15"]
    G -->|"Liquidity fail"| K(["Cancel · notify user\nBid-ask too wide"])

    J --> L["Fill confirmed\nWrite to options_trades\nStop_value = -50% of true cost"]

    L --> M(["watchman.py\nstarts monitoring"])

    M -->|"Every 15 min"| N{"Threshold\nhit?"}
    M -->|"4pm EOD"| O["Full snapshot\nUpdate trailing stop\nCatalyst countdown check"]

    N -->|"Stock move > 3%\nIV jump > 8 pts\nStop crossed\nCatalyst < 10 days\nDTE < 180 LEAP only"| P["📱 Alert sent\nCurrent P&L · suggested action"]
    N -->|"Nothing notable"| M

    O --> Q{"Exit\ntrigger?"}
    P --> Q

    Q -->|"Stage 1: spread -50% of cost"| R["Place close order"]
    Q -->|"Stage 2: +25% profit → breakeven lock"| S["Update stop_value to entry cost\nCannot lose money now"]
    Q -->|"Stage 3: trail triggered"| R
    Q -->|"21 DTE hard close"| R
    Q -->|"Catalyst miss 24h SPREAD / 72h LEAP"| R
    Q -->|"OPT CLOSE command"| R
    Q -->|"Nothing"| M

    S --> M

    R --> T["Combo limit close\nMid price · increment toward bid"]
    T --> U["Write exit to options_trades\nexit_reason · return_pct · thesis_correct"]
    U --> V["📱 P&L confirmed\nCapital freed · next signal considered"]
    V --> W(["Learning loop check\nTrade #20? Run analysis"])

    style C fill:#1a1a3a,stroke:#2980b9,color:#ddd
    style D fill:#1a3a1a,stroke:#27ae60,color:#ddd
    style E fill:#3a0000,stroke:#c0392b,color:#ddd
    style H fill:#2a2a2a,stroke:#7f8c8d,color:#ddd
    style K fill:#3a0000,stroke:#c0392b,color:#ddd
    style W fill:#2e1a3a,stroke:#9b59b6,color:#ddd
```

---

## Watchman Logic

```mermaid
flowchart LR
    subgraph INTRADAY["Every 15 min · 9:30am-4:00pm ET"]
        I1["Fetch current price\n+ spread/LEAP mid value\nvia bridge /options/quote"]
        I2{"Compare to\nDB stop_value"}
        I3["Update trailing stop\nif position at new high"]
        I4["📱 Alert only\nif threshold crossed\nSilent otherwise"]
    end

    subgraph EOD["4:00pm ET · Daily"]
        E1["Write options_snapshots\nfor all open positions\nprice · value · delta · IV rank · DTE"]
        E2["Update trailing stops\nfrom today's high"]
        E3["Check catalyst countdown\nfor all open positions"]
        E4["📱 EOD summary\nalways sent\nP&L · open positions · next catalyst"]
    end

    subgraph THRESHOLDS["6 Alert Triggers"]
        T1["Stock move > 3% today"]
        T2["IV Rank change > 8 pts"]
        T3["Contract value up > 30% from entry"]
        T4["Contract value down past stop_value"]
        T5["Catalyst date within 10 days"]
        T6["LEAP: DTE crosses below 180"]
    end

    INTRADAY --> THRESHOLDS
    EOD --> THRESHOLDS
```

---

## Database Schema (4 new tables)

```sql
-- Intelligence layer: auto-populated by news_engine + manual DOMAIN_INSIGHT
CREATE TABLE IF NOT EXISTS catalyst_calendar (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol              TEXT NOT NULL,
    catalyst_type       TEXT NOT NULL,   -- EARNINGS/CONFERENCE/PRODUCT_LAUNCH/
                                         -- MACRO_EVENT/SECTOR_EVENT/ANALYST_EVENT/DOMAIN_INSIGHT
    event_name          TEXT,
    event_date          TEXT,
    your_confidence     TEXT,            -- HIGH / MEDIUM / LOW
    expected_move_pct   REAL,
    iv_rank_when_noted  REAL,
    news_source         TEXT,
    notes               TEXT,
    created_date        TEXT
);

-- Position record: entry → hold → exit, full lifecycle
CREATE TABLE IF NOT EXISTS options_trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy            TEXT NOT NULL,   -- LEAP / BULL_SPREAD
    symbol              TEXT NOT NULL,
    cap_type            TEXT,            -- LARGE / MID
    underlying_price    REAL,
    strike              REAL,            -- LEAP: single strike
    long_strike         REAL,            -- SPREAD: buy leg
    short_strike        REAL,            -- SPREAD: sell leg
    expiry              TEXT,
    right               TEXT,            -- C / P
    contracts           INTEGER,
    delta_entry         REAL,
    iv_rank_entry       REAL,
    iv_pct_entry        REAL,
    premium_paid        REAL,            -- true cost incl. commission
    max_profit          REAL,
    max_loss            REAL,
    net_debit           REAL,            -- spreads only
    stop_value          REAL,            -- current stop threshold (updated by watchman)
    stop_stage          INTEGER,         -- 1=hard / 2=breakeven / 3=trailing
    catalyst_id         INTEGER REFERENCES catalyst_calendar(id),
    days_to_catalyst    INTEGER,
    entry_grade         TEXT,            -- A+ / A / B / C
    entry_thesis        TEXT,
    entry_date          TEXT,
    exit_date           TEXT,
    exit_value          REAL,
    exit_reason         TEXT,            -- PROFIT_TARGET/HARD_STOP/TRAIL/21DTE/
                                         -- CATALYST_MISS/REALLOCATION/MANUAL
    return_pct          REAL,
    return_on_risk      REAL,
    thesis_correct      TEXT,            -- YES / NO / PARTIAL
    lesson              TEXT,
    status              TEXT DEFAULT 'OPEN'  -- OPEN / CLOSED / ROLLED
);

-- Daily + intraday snapshots: every 15-min threshold check + 4pm EOD
CREATE TABLE IF NOT EXISTS options_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id            INTEGER NOT NULL REFERENCES options_trades(id),
    snapshot_date       TEXT,
    snapshot_time       TEXT,
    underlying_price    REAL,
    contract_value      REAL,
    pnl_unrealized      REAL,
    delta               REAL,
    iv_rank             REAL,
    iv_pct              REAL,
    days_to_expiry      INTEGER,
    stop_value          REAL,            -- what stop was at this snapshot
    notes               TEXT
);

-- Classified news: stored if MEDIUM or HIGH, discarded otherwise
CREATE TABLE IF NOT EXISTS options_news (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol              TEXT NOT NULL,
    headline            TEXT,
    source              TEXT,            -- yfinance / polygon / alpha_vantage
    source_first        INTEGER,         -- 1 if this source reported it first
    published_at        TEXT,
    relevance           TEXT,            -- HIGH / MEDIUM / LOW / NOISE
    news_type           TEXT,            -- PARTNERSHIP/REGULATORY/EARNINGS_SIGNAL/
                                         -- PRODUCT/LAYOFF/MACRO/ANALYST/CEO_COMMENT/LEGAL/SECTOR
    direction           TEXT,            -- BULLISH / BEARISH / NEUTRAL
    time_horizon        TEXT,            -- IMMEDIATE / SHORT / LONG
    already_priced_in   TEXT,            -- YES / NO / UNCLEAR
    creates_future_event INTEGER,        -- 1 if auto-created a catalyst_calendar entry
    catalyst_id         INTEGER REFERENCES catalyst_calendar(id),
    one_line_reason     TEXT,
    linked_trade_id     INTEGER REFERENCES options_trades(id),
    created_at          TEXT
);
```

---

## Telegram Commands

### Options commands (OPT prefix — never conflicts with equity)

```
OPT STATUS              P&L summary of all open positions
OPT POSITIONS           Detailed view: each position + Greeks + stop level + DTE
OPT CALENDAR            Upcoming catalysts next 30 days
OPT PAUSE               Pause news engine alerts (equity unaffected)
OPT RESUME              Resume news engine alerts
OPT CLOSE [symbol]      Manually close a position (shows confirm step)
OPT ADD [sym] [date]    Add manual DOMAIN_INSIGHT catalyst entry
                        e.g. OPT ADD NVDA 2026-11-30 AWS re:Invent keynote HIGH
```

### Alert responses (numbered, context-aware, 30-min timeout)

```
Signal alert:  reply 1 / 2 / 3 / 4 = choose template, or 5 = SKIP
Capital full:  reply 1 = view reallocation candidates, 2 = watchlist, 3 = skip
Reallocation:  reply REALLOCATE or HOLD
Close confirm: reply YES or NO
```

### Equity commands (unchanged)
```
STATUS    CANCEL    RESUME
```

---

## Exit Rules

### Bull Spread

| Stage | Trigger | Action |
|-------|---------|--------|
| 1 — Hard stop | Spread value drops -50% of true cost | Close immediately |
| 2 — Breakeven lock | Spread value reaches +25% profit | Move stop_value to entry cost |
| 3 — Trail | Spread value reaches +50% of max profit | Trail: stop = high - (15% of max profit) |
| Time | 21 DTE | Always close regardless of P&L |
| Catalyst miss | Catalyst passed, stock flat, 24h | Close — IV crush incoming |

### LEAP

| Stage | Trigger | Action |
|-------|---------|--------|
| 1 — Hard stop | Contract down -40% of premium | Close |
| 2 — Breakeven lock | Contract up +30% profit | Move stop_value to entry cost |
| 3 — Trail | Contract up +50% of premium | Trail: stop = high - (20% of premium paid) |
| Roll alert | DTE crosses below 180 | Telegram alert to roll or close |
| Catalyst miss | 72h after event, no move | Review and likely close |

---

## Bid/Ask Execution Rules

```
Entry (combo order — always, never leg separately):
  Start limit at mid price (long_mid - short_mid)
  Increment $0.05 every 45 seconds
  Cancel if slippage exceeds $0.15 from mid (too illiquid)

Exit (combo order):
  Start limit at mid
  Increment $0.05 toward bid every 45 seconds
  Accept fill — getting out cleanly > squeezing last $5

Pre-filter (before generating templates):
  Long leg bid-ask > $0.30  → flag ILLIQUID
  Short leg bid-ask > $0.25 → flag ILLIQUID
  Net debit < $0.80         → skip (commissions eat edge)

Commission per round-trip: ~$3.20-3.60 (2 legs × open + close)
Always use post-commission numbers in EV and R:R calculations
```

---

## Signal Routing (IV Rank decides instrument)

```
Same news signal → different instrument based on IV Rank:

  IV Rank < 30%  + catalyst 4-8 wk  → LEAP preferred
  IV Rank 30-45% + catalyst 1-3 wk  → Bull Spread preferred
  IV Rank > 45%                      → SKIP (premium too expensive)
```

---

## Capital Allocation

```
Options budget:  $3-4K  (separate from equity $10K — never mixed)
Max per LEAP:    $2,200 (RKLB upper bound in mid-cap universe)
Max per spread:  $200   (aggressive template upper bound)
Max positions:   4-5 open simultaneously

Mid-cap LEAP universe (now):    PLTR · RKLB · APP · HOOD · IONQ
Large-cap LEAP universe (later, $8K+): NVDA · META · AMZN · MSFT
Bull spread universe:           all of above + SPY/QQQ (liquid)
Wheel / CSP:                    deferred until $15K+ options capital
```

---

## Learning Loop

```
Frequency:    First run after trade #20 closes
              Then every 10 additional closed trades
              Daily EOD: lightweight stats only (no analysis)

What it analyses:
  Return by grade (A+ vs A vs B vs C)
  Return by IV rank at entry (<25% vs 25-35% vs >35%)
  Return by cap_type (LARGE vs MID)
  Return by catalyst present vs absent
  Exit reason distribution (too many hard stops = entry quality issue)
  News source quality (which source caught the most unique HIGH signals)

Output:       Advisory Telegram report
              No auto-config changes until 50+ trades
              User confirms any threshold adjustments via OPT commands
```

---

## Backtesting — Design Pending

Options backtesting requires historical options chain data (IV, bid/ask, greeks per timestamp).

| Approach | Data | Cost | Accuracy |
|----------|------|------|----------|
| IBKR historical bars + Black-Scholes approx | Stock price + VIX as IV proxy | Free | ~70% |
| OptionsDX | Full chain history | ~$50/mo | High |
| IBKR reqHistoricalData on Option contract | Real chain snapshots, limited lookback | Free (capped) | High for recent |

**Decision needed before building `backtester_options.py`:**
- Confirm acceptable accuracy level (Black-Scholes approx vs real chain data)
- Confirm lookback period needed (1 year vs 3 year)
- Start with IBKR free historical + B-S approx, upgrade data if backtests show promise

**`backtester_options.py` is Phase 6 — after Phase 1-5 are built and paper trading is running.**

---

## Build Order

```
Phase 1 — database.py        Add 4 tables (IF NOT EXISTS, zero equity risk)
Phase 2 — bridge.py          Add 5 /options/* endpoints (additive)
Phase 3 — news_engine.py     Multi-source + dedup + Claude API + alerts (most complex)
Phase 4 — watchman.py        15-min scan + EOD + trailing stops + threshold alerts
Phase 5 — options_trader.py  Calculator + order execution + Telegram commands
Phase 6 — backtester_options.py  (pending data source decision)
```

---

*Design locked May 9 2026. Approved for build.*
