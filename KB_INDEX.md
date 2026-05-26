# Trading System Knowledge Base
**Last updated:** May 13 2026 | **Account:** IBKR Paper DU9952463 | **Clean run since:** May 1 2026

---

## 1. What the System Does

Automated intraday momentum trader. Scans 110 symbols every 5 minutes. Enters bull (LONG) and bear (SHORT) positions based on a scored setup system. Manages exits via 11 mechanisms. Learns nightly from closed trades.

**Capital:** $10,000 deployed across up to 5 positions ($2,000 each)
**WR (live paper May 2026):** 54-61% (learner broken — fix weekend May 17-18)
**WR (backtest):** 90% | $5,855/month avg | Sharpe 15.24

---

## 2. System Architecture

```
IB Gateway (IBC, headless)
  └── bridge.py              FastAPI port 8000 — translates REST to IBKR TWS API
        └── auto_trader.py   Main loop: scan → grade → order → monitor → exit → learn
              ├── learner.py            Nightly: RSI/volume weights (broken, fix May 17-18)
              ├── database.py           SQLite: trades.db + trading.db
              └── catalyst_detector.py  IBKR scanner + static universe merge

options/
  ├── engine.py              Quant: HV30, expected move, 5-gate scoring, Greeks, MC EV
  ├── news_engine.py         5-source scanner + Groq/Llama classify (free) + Telegram
  ├── options_trader.py      OPT Telegram commands + bull spread calculator + orders
  ├── watchman.py            15-min monitor + 3-stage trailing stops + EOD summary
  ├── learner_options.py     Nightly: what-if fill analysis + decision log
  └── backtester_options.py  B-S backtester (Phase 5 update pending)
```

**Interface:** Telegram bot (primary). Viewer account (Ruhi — REGIME/STATUS/charts only).

---

## 3. launchd Services (Mac)

| Service | Controls | Restart Command |
|---------|----------|-----------------|
| com.sushil.trading.gateway | IB Gateway via IBC | `launchctl kickstart -k gui/$(id -u)/com.sushil.trading.gateway` |
| com.sushil.trading.bridge | bridge.py (port 8000) | `launchctl kickstart -k gui/$(id -u)/com.sushil.trading.bridge` |
| com.sushil.trading.autotrader | auto_trader.py | `launchctl kickstart -k gui/$(id -u)/com.sushil.trading.autotrader` |
| com.sushil.trading.news_engine | options/news_engine.py | `launchctl kickstart -k gui/$(id -u)/com.sushil.trading.news_engine` |
| com.sushil.trading.options_trader | options/options_trader.py | `launchctl kickstart -k gui/$(id -u)/com.sushil.trading.options_trader` |

**After any code change:** restart the relevant service, then run `venv/bin/python sim_today.py`

---

## 4. Key Constants (auto_trader.py — verified May 13 2026)

| Constant | Value | Meaning |
|----------|-------|---------|
| TOTAL_CAPITAL | $10,000 | 5 × $2K positions |
| MAX_OPEN_TRADES | 5 | Slots, each side independent |
| MAX_DAILY_BULL/BEAR | 5 each | Independent counters |
| MAX_LOSS_PER_TRADE | $100 | Dollar circuit breaker |
| MAX_DAILY_LOSS | $200 | Stop new entries for day |
| DAILY_PROFIT_TARGET | $400 | Protect gains — no new entries |
| NO_ENTRY_BEFORE | 10:00am ET | Let ORB establish |
| NO_ENTRY_AFTER | 3:00pm ET | No late chasing |
| LUNCH_AVOID | 11:30am–12:45pm | Chop window |
| EOD_CLOSE | 3:45pm ET | Hold only on conviction |
| NO_MOVE_MINUTES | 240 min | Exit if flat -0.3% to +2.0% |
| MIN_TODAY_GAIN | 3.0% | Early-stage movers only |
| MIN_RR | 2.5 | Reward:risk floor |
| ATR_STOP_MULT | 2.0× | Initial stop |
| ATR_TRAIL_MULT | 1.5× | Trailing stop |

---

## 5. Telegram Commands (Equity — auto_trader.py)

| Command | What it does |
|---------|-------------|
| `STATUS` | Open positions, P&L, regime |
| `REGIME` | Current market regime + factors |
| `PORTFOLIO` | Full account snapshot |
| `PAUSE` | Halt new entries (manual override) |
| `RESUME` | Re-enable entries after PAUSE |
| `CLOSE <sym>` | Manual position close |
| `HELP` | Full command list |

## 5b. Telegram Commands (Options — options_trader.py, prefix OPT)

| Command | What it does |
|---------|-------------|
| `OPT BUY <SYM>` | Run 5-gate calculator → recommended spread |
| `OPT STATUS` | Open options positions + P&L |
| `OPT PORTFOLIO` | Options account snapshot |
| `OPT NEWS` | Options news leaderboard (top conviction symbols) |
| `OPT KB` | Session brief: conviction hits, decisions, P&L vs EV |
| `OPT PAUSE` | Halt auto-suggestions |
| `OPT RESUME` | Re-enable auto-suggestions |
| `OPT HELP` | Full options command list |

---

## 6. Database Tables

### trades.db / trading.db (equity)
| Table | Contents |
|-------|---------|
| `trades` | All trade entries and exits, P&L, WR calculation |
| `strategy_weights` | Learner outputs: RSI bucket weights, volume tier weights |
| `sector_grades` | (pending — learner fix May 17-18): sector WR, grade STRONG/NEUTRAL/WEAK |

### options/ tables (options_trader.py)
| Table | Contents |
|-------|---------|
| `opt_calc_log` | Every calculator run: IV, HV30, 5 gates pass/fail, verdict, MC EV, user action |
| `opt_suggestions` | Auto-suggest log: conviction source, decision, underlying 7d/14d actual, what-if P&L |
| `opt_trade_outcomes` | Actual P&L vs predicted EV when a paper trade closes |

---

## 7. Entry Signal Stack (Bull)

**Hard gates (any = SKIP):**
- Earnings within 0–3 days
- Price below MA20
- Volume below threshold
- Today's gain < 3.0%
- R:R < 2.5
- Gap-and-crap: price 5%+ below today's open

**Pattern required (at least one):**
ORB breakout | VWAP reclaim | Bull flag | HOD break | Strong momo (≥5% + beats SPY ≥3%)

**Score thresholds:** A+ ≥80pts | A ≥65pts | Negative SPY day: A+ only

**Position sizing:**
- A+ catalyst: $2,000 | A+: $1,800 | A catalyst: $1,600 | A: $1,400
- First-Bar Quality: ×1.15 (first 30min up >1% + vol >1.3×)

---

## 8. Exit Stack (11 mechanisms, priority order)

1. Hard stop — 5% SL
2. Dollar circuit breaker — -$100 unrealized
3. Partial exit — at +5% (1R): sell 50%, trail 50%
4. Break-even stop — at +2.5%: SL moves to entry + 0.5×risk
5. VWAP cross below (only when profitable >0.5%)
6. Momentum fade — >1×ATR drop from session high while >0.3% profitable
7. No-move exit — flat 240 min (range -0.3% to +2.0%)
8. ATR trailing stop (activates at +1 ATR profit)
9. 5m bar trailing stop (at +3% profit, trail to 2-bar low)
10. EOD conviction close — 3:45pm ET
11. Hard time stop — 1 business day max hold

---

## 9. Options System (All 6 Phases Complete — May 13 2026)

**Strategy:** Bull call spreads, 30-45 DTE, delta 0.30-0.50 long leg
**Universe:** 25 symbols (Tier 1: NVDA META AMZN MSFT AAPL TSLA AMD GOOGL ORCL COIN)
**Capital:** $3-5K start, max 30% deployed, spreads only until $10K+

**5-Gate Calculator:**
1. IV < HV30 + 5% (cheap options)
2. Stock above 200MA (technical alignment)
3. news_engine conviction = HIGH BULLISH
4. Bid/ask spread < $0.30/leg (liquidity)
5. Stock up >1% last 5 days OR fresh catalyst

5/5 = ENTER | 4/5 = ENTER reduced | ≤3/5 = SKIP

**Auto-suggest flow:** news_engine HIGH conviction → PENDING in opt_suggestions → options_trader polls 30s → runs calc → Telegram CONFIRM/SKIP

**IBKR Paper limits:**
- No OPRA: use `reqMarketDataType(3)` (15-min delayed)
- `Option()`: 7 args, multiplier='' not 'USD'
- Combo orders: must set `order.tif='DAY'` and `order.transmit=True`

---

## 10. Backtest Commands

```bash
venv/bin/python sim_today.py                   # fast regression check — run after EVERY code change
venv/bin/python backtest_strategy.py           # bull edge (full universe or top 20)
venv/bin/python backtest_bear.py               # bear edge
venv/bin/python backtest_walkforward.py        # OOS (8 windows) — run after param changes
venv/bin/python backtest_stress.py             # 2022 bear, COVID, carry trade
venv/bin/python monte_carlo.py                 # ruin risk
venv/bin/python options/backtester_options.py  # options B-S backtest
```

**Cadence rule:** Backtests triggered by code changes, NOT by time.

---

## 11. Validated Backtest Numbers

| Test | Result |
|------|--------|
| Universe (110 sym, 6.3yr) | 90% WR, $5,855/month avg, Sharpe 15.24, MDD -8.0% |
| Walk-forward (8 windows) | 8/8 OOS profitable ✅ |
| Stress (2022 bear) | 89% WR ✅ |
| Monte Carlo (1K shuffles) | 0% ruin risk ✅ |
| Bear (266 trades) | 93.6% WR, $68 EV ✅ |
| Options spread (382 trades) | +25.6% EV ✅ |
| Options LEAP | 66.7% WR, +38.2% EV ✅ |

### Sector WR (174 live paper trades)
| Sector | WR | Avg P&L |
|--------|-----|---------|
| SEMIS | 87.5% | +$17.87 |
| NUCLEAR | 81.8% | +$15.51 |
| TECH | 79.2% | +$7.81 |
| DEFENCE | 75.0% | +$21.62 |
| BIOTECH | 31.3% | -$9.11 |
| FINTECH | 16.7% | -$3.08 |

71-point WR spread = largest single untapped edge in system (learner fix pending)

---

## 12. Known Issues (May 2026)

| Issue | Status | Fix |
|-------|--------|-----|
| Learner weights stuck at 1.0 (16 nights) | PENDING | Weekend May 17-18 |
| watchman.py exits not logged to outcomes | PENDING | Wire log_trade_outcome() |
| backtester_options.py Phase 5 (MC EV) | PENDING | After first paper trade |
| 14 open audit items (see roadmap.md for full list) | PENDING | May 15 review |

---

## 13. Roadmap Summary

| Timeline | Milestone |
|----------|-----------|
| May 17-18 | Learner fix (4 bugs) |
| May 15 | Audit review (14 items) |
| This week | First paper options trade (OPT BUY TSLA) |
| After 5+ paper trades | VIX circuit breaker (Layer 1) |
| After 30+ paper bull trades | Bear put spreads (Layer 2) |
| June 2026 | Equity go-live 25% (if May WR ≥60%) |
| June 2026 | News→equity correlation check |
| July-Aug 2026 | Equity 50% size |
| Sep 2026 | Equity full size |
| Q3 2026 | Futures/MNQ via Tradovate paper |
| Q4 2026 | Apex prop firm evaluation |

---

## 14. Environment

```bash
Python:     venv/bin/python3   (NOT system Python)
Working dir: /Users/sushil/trading/
DB files:   trades.db, trading.db
Bridge:     http://localhost:8000
```

**Mac gotcha:** `pyobjc-framework-EventKit` conflicts with ib_async — do NOT reinstall.
