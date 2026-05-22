# Claude Code — Trading System Ground Truth
**Auto-loaded by Claude Code at session start. Update this file whenever code changes.**
Last updated: May 13 2026

---

## Quick Facts

- **Account:** IBKR Paper DU9952463, port 4002 | Canada
- **Python:** Always use `venv/bin/python` or `venv/bin/python3`
- **Working dir:** `/Users/sushil/trading/`
- **DB:** `trades.db` (primary), `trading.db`
- **Bridge:** `http://localhost:8000`
- **Clean run since:** May 1 2026 — do NOT change parameters without explicit user approval

---

## Service Restart (always use launchctl — never ask user to do it manually)

```bash
launchctl kickstart -k gui/$(id -u)/com.sushil.trading.<name>
# Services: gateway | bridge | autotrader | news_engine | options_trader
curl -s http://localhost:8000/   # verify bridge is up
```

After any code change: restart service → run `venv/bin/python sim_today.py`

---

## Architecture

```
bridge.py (port 8000)        — FastAPI → IBKR TWS API
auto_trader.py               — main loop, 5-min scan, entry/exit/monitor
learner.py                   — nightly RSI/volume weights [BROKEN — fix May 17-18]
database.py                  — SQLite helpers
catalyst_detector.py         — IBKR scanner + static universe

options/
  engine.py                  — HV30, 5-gate, Greeks, MC EV
  news_engine.py             — Groq/Llama-3.3-70b (free), 30-min scan
  options_trader.py          — OPT commands + calculator
  watchman.py                — 15-min monitor (start manually when first position opens)
  learner_options.py         — nightly what-if analysis
  backtester_options.py      — B-S backtest [Phase 5 update pending]
```

---

## Key Constants (auto_trader.py — do not change mid-run)

| Constant | Value |
|----------|-------|
| TOTAL_CAPITAL | $10,000 |
| MAX_OPEN_TRADES | 5 |
| MAX_DAILY_BULL/BEAR | 5 each |
| MAX_LOSS_PER_TRADE | $100 |
| MAX_DAILY_LOSS | $200 |
| DAILY_PROFIT_TARGET | $400 |
| NO_ENTRY_BEFORE/AFTER | 10:00am / 3:00pm ET |
| LUNCH_AVOID | 11:30am–12:45pm ET |
| EOD_CLOSE | 3:45pm ET |
| NO_MOVE_MINUTES | 240 |
| MIN_TODAY_GAIN | 3.0% |
| MIN_RR | 2.5 |
| ATR_STOP_MULT | 2.0× |
| ATR_TRAIL_MULT | 1.5× |
| SCAN_INTERVAL | 300s (5 min) |
| MONITOR_INTERVAL | 30s |

---

## Bull Entry (NORMAL/STRONG regime)

Hard gates: earnings 0-3d | price < MA20 | vol low | gain < 3% | R:R < 2.5 | gap-and-crap
Patterns: ORB | VWAP reclaim | bull flag | HOD break | strong momo ≥5%
Score: A+ ≥80pts | A ≥65pts

## Bear Entry (WEAK regime only)

Mirror of bull. Post-lunch (12:45-2pm): needs 3 consecutive WEAK scans.
BEAR_EXCLUDED = {'RDW'}
Regime flip exit: auto-covers losing shorts on NORMAL/STRONG ≥3 scans (changed from 2 — May 22 2026).

## Exit Stack (13 mechanisms, priority order)

0a. P&L protection (peak session ≥$200 drops 25% → cut non-runners pnl<-0.3%)
0b. Regime flip exit (SHORT only, ≥3 consecutive NORMAL/STRONG scans, losing position)
1. Hard stop (5% SL)
2. Dollar circuit breaker (-$150)
3. Partial exit (50% at +5%, trail rest)
4. Break-even stop (+2.5% → move SL)
5. VWAP cross (if profitable >0.5%)
6. Momentum fade (>1×ATR drop from high)
7. No-move exit (240 min, range -0.3% to +2.0%)
8. ATR trail (activates at +1 ATR)
9. PCT trail (activates at +1.5%, 0.5% gap — BOTH long and short)
10. 5m bar trail (at +3%, trail to 2-bar low)
11. EOD close (3:45pm ET)
12. Hard time stop (1 business day)

## Entry Gates (added May 22 2026)

Afternoon gate: no new entries (LONG or SHORT) after 12pm ET if morning realized P&L ≥ $150.
Data: afternoon LONG 44.8% WR / -$0.91 avg (vs 58.5% morning), afternoon SHORT 18.2% WR / -$6.56 avg.

---

## Standing Rules

1. **No tinkering mid-run.** May 1 2026 = Day 1. No parameter changes until May 15 review, then only if data supports it.
2. **Validate before build.** Always backtest first. Never suggest building without data.
3. **No mid-run changes** unless: (a) clear bug, (b) system crashing, (c) market condition system cannot handle.
4. **After any change:** `sim_today.py` replay immediately.
5. **Go-live timeline:** June 25% → July-Aug 50% → Sep full. Do NOT compress this.

---

## Known Issues (Active)

| Issue | Status |
|-------|--------|
| Learner weights stuck at 1.0 | Fix weekend |
| watchman.py exits not logged | Wire log_trade_outcome() |
| backtester_options.py Phase 5 | After first paper trade |

## Changes Applied May 22 2026 (postmortem-driven)

| Change | Details |
|--------|---------|
| Flip exit 2→3 scans | +$754/yr — 6/24 covers were premature |
| P&L protection floor | Peak≥$200 drops 25% → cut non-runners. +$539/yr, 0 false positives |
| Afternoon gate | No new entries after 12pm if morning realized ≥$150 |
| Short PCT trail | Bug fix — PCT trail was completely absent for shorts |
| COIN/HOOD sector | COIN→QUANTUM_CRYPTO, HOOD→TECH (was FINTECH = -20 penalty) |

---

## Backtest Commands

```bash
venv/bin/python sim_today.py                # REQUIRED after every code change
venv/bin/python backtest_strategy.py        # bull edge
venv/bin/python backtest_bear.py            # bear edge
venv/bin/python backtest_walkforward.py     # OOS (8 windows)
venv/bin/python backtest_stress.py          # crisis periods
venv/bin/python monte_carlo.py              # ruin risk
```

---

## Options System

All 6 phases complete. Paper trading live.
- First paper trade: `OPT BUY TSLA` 10-11am ET
- Strategy: bull call spreads, 30-45 DTE, 5-gate entry system
- Capital: max 30% of options capital deployed at once
- IBKR paper: use `reqMarketDataType(3)` for delayed IV/delta
- watchman.py: start manually when first options position is open

**Macro risk until VIX circuit breaker built:** VIX >25 AND SPY below 20MA → manual `OPT PAUSE`

---

## Options Database Tables

| Table | Location | Contents |
|-------|----------|---------|
| opt_calc_log | trading.db | Every calculator run — gates, verdict, MC EV, user action |
| opt_suggestions | trading.db | Auto-suggest log — conviction, what-if P&L |
| opt_trade_outcomes | trading.db | Actual P&L vs EV when trades close |

---

## LLM Cost Awareness

- **news_engine.py:** Uses Groq/Llama-3.3-70b (free). Falls back to Claude Haiku only if no GROQ_API_KEY.
- **auto_trader.py:** Claude Sonnet 4.6 for chart gate only (~$0.02/trade entry attempt). Cheap — no optimization needed.
- **Telegram messages:** ~$0.001 each. Fine.
- **Do NOT add new LLM calls without cost estimate.**

---

## Mac Gotcha

`pyobjc-framework-EventKit` conflicts with ib_async — do NOT reinstall this package.
