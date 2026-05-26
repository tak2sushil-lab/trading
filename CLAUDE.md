# Claude Code — Trading System Ground Truth
**Auto-loaded by Claude Code at session start. Update this file whenever code changes.**
Last updated: May 26 2026

---

## Quick Facts

- **Account:** IBKR Paper DU9952463, port 4002 | Canada
- **Python:** Always use `venv/bin/python` or `venv/bin/python3`
- **Working dir:** `/Users/sushil/trading/`
- **DB:** `trades.db` (primary), `trading.db`
- **Bridge:** `http://localhost:8000`
- **Clean run since:** May 1 2026 — do NOT change parameters without explicit user approval
- **Universe:** 159 symbols (was 110 — 49 added May 24 2026 via DNA batch)

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
learner.py                   — nightly RSI/volume/sector weights
database.py                  — SQLite helpers
catalyst_detector.py         — IBKR scanner + static universe

dna_analysis.py              — DNA fingerprint clustering (re-run quarterly)
find_candidates.py           — Universe expansion screener (DNA + 5-rule filter)
batch_backtest.py            — Full validation suite for new candidates
backtest_dna.py              — A/B comparison: baseline vs DNA-modified scoring
collect_bars.py              — Passive 5-min OHLCV collector (market_data.db, 159 symbols)

options/
  engine.py                  — HV30, 5-gate, Greeks, MC EV
  news_engine.py             — Groq/Llama-3.3-70b (free), 30-min scan
  options_trader.py          — OPT commands + calculator + OPT_SCALP auto-scalp engine
  watchman.py                — 15-min monitor (start manually when first position opens)
  learner_options.py         — nightly what-if analysis
  backtester_options.py      — B-S backtest [Phase 5 update pending]

backtest_scalp.py            — OPT_SCALP Mode A backtest (scan_log A+ + 5-min bars proxy)
```

---

## Key Constants (auto_trader.py — do not change mid-run)

| Constant | Value |
|----------|-------|
| TOTAL_CAPITAL | $10,000 |
| MAX_OPEN_TRADES | 5 |
| MAX_DAILY_BULL/BEAR | 20 each (recycling) |
| MAX_LOSS_PER_TRADE | $150 |
| MAX_DAILY_LOSS | $200 |
| DAILY_PROFIT_TARGET | $400 |
| NO_ENTRY_BEFORE/AFTER | 10:00am / 3:00pm ET |
| LUNCH_AVOID | 11:30am–12:45pm ET |
| EOD_CLOSE | 3:45pm ET |
| NO_MOVE_MINUTES | 240 (INSTITUTIONAL: 300) |
| MIN_TODAY_GAIN | 3.0% |
| MIN_RR | 2.5 |
| ATR_TRAIL_MULT | 1.5× (HIGH_VOL: 1.0×) |
| PCT_TRAIL_ACTIVATE | +1.5% |
| SCAN_INTERVAL | 300s (5 min) |
| MONITOR_INTERVAL | 30s |

---

## DNA Factor Model (added May 24 2026)

Three DNA clusters assigned in `auto_trader.py` — re-run `dna_analysis.py` quarterly:

| Cluster | Symbols | L1 Entry modifier | L3 Exit modifier |
|---------|---------|-------------------|-----------------|
| HIGH_VOL | 35 symbols | ORB without VWAP reclaim → -15pts; VWAP reclaim → +15pts | ATR trail 1.0× (tighter) |
| INSTITUTIONAL | 68 symbols | ORB break → +5pts | No-move timer 300 min (vs 240) |
| MOMENTUM | remainder | No modifier | Standard exits |

**Short side is mirrored:** HIGH_VOL short needs VWAP rejection before ORB breakdown is rewarded. INSTITUTIONAL short gets small ORB breakdown bonus.

**Key insight:** HIGH_VOL stocks fill their gaps 70% of the time intraday. Entering on a naked ORB (before pullback) = buying into the gap-fill zone. Waiting for VWAP reclaim means the bounce absorbed, momentum confirmed.

---

## Bull Entry (NORMAL/STRONG regime)

Hard gates: earnings 0-3d | price < MA20 | vol low | gain < 3% | R:R < 2.5 | gap-and-crap | failed ORB
Patterns: ORB | VWAP reclaim | bull flag | HOD break | strong momo ≥5%
Score: A+ ≥80pts | A ≥65pts

## Bear Entry (WEAK regime only)

Mirror of bull. 3 consecutive WEAK scans required all-day.
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
7. No-move exit (240 min std / 300 min INSTITUTIONAL, range -0.3% to +2.0%)
8. ATR trail (activates at +1 ATR — 1.0× HIGH_VOL, 1.5× others)
9. PCT trail (activates at +1.5%, 0.5% gap — BOTH long and short)
10. 5m bar trail (at +3%, trail to 2-bar low)
11. EOD close (3:45pm ET)
12. Hard time stop (1 business day)

## Entry Gates (added May 22 2026)

Afternoon gate: no new entries (LONG or SHORT) after 12pm ET if morning realized P&L ≥ $150.
Data: afternoon LONG 44.8% WR / -$0.91 avg (vs 58.5% morning), afternoon SHORT 18.2% WR / -$6.56 avg.

---

## Standing Rules

1. **No tinkering mid-run.** May 1 2026 = Day 1. Parameter changes require data + explicit approval.
2. **Validate before build.** Always backtest first. Never suggest building without data.
3. **No mid-run changes** unless: (a) clear bug, (b) system crashing, (c) market condition system cannot handle.
4. **After any change:** `sim_today.py` replay immediately.
5. **Go-live timeline:** June 25% → July-Aug 50% → Sep full. Do NOT compress this.

---

## US Market Holidays 2026

| Date | Holiday |
|------|---------|
| ~~May 25~~ | ~~Memorial Day~~ |
| Jun 19 | Juneteenth ← **next closed day** |
| Jul 3 | Independence Day (observed) |
| Sep 7 | Labor Day |
| Nov 26 | Thanksgiving |
| Dec 25 | Christmas |

`US_HOLIDAYS_2026` set in `auto_trader.py` — `is_market_open()` and `is_premarket_window()` both check it. No orders possible on these days. Update for 2027 before year-end.

---

## Go-Live Pre-Flight Checklist (before June 25% capital phase)

These must be verified/built before any real-money trading begins. Do NOT go live until all are ticked.

| # | Item | Status | Notes |
|---|------|--------|-------|
| 1 | **Gateway reconnect simulation test** | ⬜ pending | Kill bridge mid-scan, confirm freeze Telegram fires, no orders placed |
| 2 | **Partial fill handling in place_trade()** | ⬜ pending | Prod orders can partially fill; paper always fills 100%. Add `filled_qty` tracking before 50% capital phase |
| 3 | **IBKR market data subscriptions** | ⬜ pending | Paper uses `reqMarketDataType(3)` (delayed). Prod needs live subscriptions for all 159 symbols. Verify none return Error 10089 |
| 4 | **Buying power pre-check** | ⬜ pending | Paper ignores buying power limits; prod hard-rejects. Add pre-order check: `available_cash >= position_cost` before submitting |
| 5 | **TFSA isolation double-check** | ⬜ pending | Confirm `BRIDGE_URL` in prod `.env` targets port 4002 Individual account (U22303375), never 4001 TFSA (U15022563). CRA compliance |
| 6 | **PROD_EQUITY_ENABLED flag test** | ⬜ pending | Flip flag in dry-run, confirm orders reach paper Individual account, not TFSA |
| 7 | **Prod `.env` credentials audit** | ⬜ pending | Never overwritten by `deploy_to_prod.sh` — verify before each deploy |
| 8 | **watchman.py exit logging** | ⬜ pending | Wire `log_trade_outcome()` before options paper trading generates real outcomes |
| 9 | **backtester_options.py Phase 5** | ⬜ pending | Complete after first options paper trade closes |

---

## Known Issues (Active)

| Issue | Status |
|-------|--------|
| watchman.py exits not logged | Wire log_trade_outcome() |
| backtester_options.py Phase 5 | After first paper trade |
| Short side WR gap (50% vs 77% long) | Monitor 60-day window; 3-scan fix + DNA modifiers now in place |

## Changes Applied May 26 2026 (May 25 incident postmortem)

Root cause: gateway reconnect mid-scan → filled orders returned Cancelled → reconcile adopted orphans → 24 phantom trades.

| Change | Details |
|--------|---------|
| place_trade() Cancelled verify | Before treating Cancelled as failure, check IBKR portfolio; record fill if position exists |
| reconcile: close orphans immediately | Market order → limit order (yfinance price) → Telegram alert. No more adoption of phantom positions |
| reconcile: handle short orphans | Was checking `qty > 0` only. Now handles `qty < 0` (BUY to close) |
| MAX_OPEN bypass fix | `attempted` counter in all 4 order-placing loops (bull, bear, pre-market, catalyst override) so failed orders count toward cap |
| 3-layer gateway stability gate | Layer 1: bridge connected check. Layer 2: 10-min post-reconnect freeze. Layer 3: IBKR/DB parity check. All block new entries; monitoring always runs |
| Telegram backoff (options_trader) | DNS failures back off to 60s max; was hammering every 10s causing 4 service restarts and zero options messages all day |
| RECONCILED exclusion | All trades queries in database.py and learner.py filter `setup_type != 'RECONCILED'` — phantom trades never touch WR or nightly learner weights |
| USAR close | Limit BUY order (yfinance price) cleared the orphan short position; market orders fail with Error 10089 (no data subscription) — limit order bypasses this |

**Data note:** Today's 26 trades are all setup_type='RECONCILED'. They are excluded from all WR, P&L, sector grade, and learner calculations. Real strategy P&L for May 26 = $0.

---

## Changes Applied May 22 2026 (postmortem-driven)

| Change | Details |
|--------|---------|
| Flip exit 2→3 scans | +$754/yr — 6/24 covers were premature |
| P&L protection floor | Peak≥$200 drops 25% → cut non-runners. +$539/yr, 0 false positives |
| Afternoon gate | No new entries after 12pm if morning realized ≥$150 |
| Short PCT trail | Bug fix — PCT trail was completely absent for shorts |
| COIN/HOOD sector | COIN→QUANTUM_CRYPTO, HOOD→TECH (was FINTECH = -20 penalty) |

## Changes Applied May 25 2026 (Holiday + Options session)

| Change | Details |
|--------|---------|
| Holiday guard | news_engine, watchman, auto_trader APScheduler — all skip on US_HOLIDAYS_2026 |
| Conviction gate fix | `direction == 'BULL'` (was 'BULLISH') — gate was never passing, affected all auto-suggest |
| actual_pnl fix | watchman + options_trader `_execute_close_bg` — was logging exit_value, now logs exit - premium_paid |
| OPT_SCALP engine | Mode A (A+ equity scan) + Mode B (HIGH news) → ATM weekly call, auto-execute |
| Phase 1 auto-execute | 5/5 gates → auto-execute spread/LEAP (no CONFIRM). 4/5 → CONFIRM as before |
| backtest_scalp.py | Mode A proxy backtest (19 trades / 37 days, growing with scan_log) |

## Changes Applied May 24 2026 (DNA session)

| Change | Details |
|--------|---------|
| DNA factor model | dna_analysis.py: 17 features, KMeans clustering, 3 archetypes |
| L1 entry modifier | HIGH_VOL ORB penalty/VWAP bonus; INSTITUTIONAL ORB bonus (both sides) |
| L3 exit modifier | HIGH_VOL: 1.0× ATR trail; INSTITUTIONAL: 300 min no-move |
| Universe 110→159 | 49 candidates: DNA screen + 5yr backtest + IS/OOS + stress. Avg OOS WR 89.5% |
| Bear backtest (49) | All 49 pass short side too. Best: TT/CTRA/WFRD 100% WR. Weakest: WULF 54%, CIFR 3 trades |
| PANW re-added | Was dropped at 43% WR (gap-and-go only). Full A/A+ strategy: 93.2% WR |
| ⚠️ Lower conviction | BSX (OOS 60%), HOLX (OOS 67%), CIFR (N=22) — added but monitor |
| Redundant gate fix | Removed dead `today_gain >= 2.0` / `today_chg <= -2.0` in DNA modifier (hard gate already ≥3%) |
| Holiday calendar | US_HOLIDAYS_2026 set added — is_market_open() + is_premarket_window() block on NYSE holidays |

---

## Sector Grades (learner writes nightly, grade_setup reads)

| Sector | Current Grade | Effect |
|--------|--------------|--------|
| SEMIS | STRONG | +15 pts |
| NUCLEAR | STRONG | +15 pts |
| TECH | STRONG | +15 pts |
| DEFENCE | STRONG | +15 pts |
| QUANTUM_CRYPTO | NEUTRAL | no effect |
| CLEAN_ENERGY | NEUTRAL | no effect |
| CONSUMER | NEUTRAL | no effect |
| COMMODITIES | NEUTRAL | no effect |
| BIOTECH | WEAK | -20 pts |
| ENERGY | WEAK | -20 pts |
| FINTECH | WEAK | -20 pts |

Grades reset nightly from last 30 days, min 5 trades. Affects entry scoring directly.
**Note:** 10 new symbols in BIOTECH/ENERGY/FINTECH will trade less frequently while those sectors are WEAK.

---

## Backtest Commands

```bash
venv/bin/python sim_today.py                # REQUIRED after every code change
venv/bin/python backtest_strategy.py        # bull edge
venv/bin/python backtest_bear.py            # bear edge
venv/bin/python backtest_walkforward.py     # OOS (8 windows)
venv/bin/python backtest_stress.py          # crisis periods
venv/bin/python monte_carlo.py              # ruin risk
venv/bin/python batch_backtest.py           # full suite for new candidates
venv/bin/python dna_analysis.py             # re-cluster universe (quarterly)
venv/bin/python collect_bars.py --bootstrap # one-time: seed 60 days of 5-min history
venv/bin/python collect_bars.py --summary   # show DB row counts and date ranges
venv/bin/python backtest_scalp.py           # OPT_SCALP Mode A backtest (grows as scan_log accumulates)
```

---

## 5-Min Data Collection (collect_bars.py)

Passive OHLCV collector for all 159 symbols. **Bootstrapped May 24 2026** — 726K rows, 60 days history.

```python
from collect_bars import load_bars, load_multi

df  = load_bars('TSLA', start='2026-05-01', end='2026-05-15')   # single symbol
dfs = load_multi(['NVDA', 'AAPL'], start='2026-05-01')           # multi → dict
# Index is DatetimeIndex, America/New_York. Cols: open, high, low, close, volume
```

- **Storage:** `market_data.db` → table `bars_5m` (symbol, ts_utc PRIMARY KEY)
- **Schedule:** launchd `com.sushil.trading.collect_bars` fires 4:30pm ET Mon-Fri (21:30 UTC)
- **Daily mode:** fetches 3-day lookback (overlap prevents gaps if a day is missed)
- **Holiday guard:** skips weekends and `US_HOLIDAYS_2026` automatically
- **Log:** `logs/collect_bars.log`

---

## OPT_SCALP — Automated Naked Call Scalp Engine (added May 25 2026)

Three-cylinder model: equity spreads + news engine + **scalp ATM calls**.

| Parameter | Value |
|-----------|-------|
| Universe | 15 symbols (IONQ, MARA, WULF, RIOT, SOUN, RKLB, HIMS, AFRM, CELH, UPST, RIVN, RDW, JOBY, HOOD, NOK) |
| Budget | $1,000 scalp pool (separate from $4,000 spread/LEAP pool) |
| Trade size | $250/trade, max 2 concurrent scalps |
| DTE | 7–12 days (ATM weekly calls) |
| Delta gate | 0.38–0.60 |
| Spread gate | bid-ask ≤ $0.30 |
| IV rank gate | < 75% |
| Premium gate | ≤ $1.50/contract |
| Total cost gate | ≤ $300/trade |
| Entry window | 10:00am–1:30pm ET |
| Profit target | +80% (1.80× premium) — AUTO-CLOSE |
| Stop loss | −50% (0.50× premium) — AUTO-CLOSE |
| Time stop | 3 calendar days — AUTO-CLOSE |
| Dedup cooldown | 4 hours per symbol |

**Mode A:** equity scanner fires A+ LONG on a SCALP_UNIVERSE symbol in the last 10 min → scalp trigger.
**Mode B:** news_engine rates a SCALP_UNIVERSE symbol HIGH BULL within last 4 hours → scalp trigger.

All 6 gates must pass → auto-execute (no CONFIRM). Scan runs every ~5 min in options_trader.py.

**Phase 1 (also added May 25 2026):** `_process_pending_suggestions` and `cmd_buy` now auto-execute on 5/5 gates (ENTER verdict). 4/5 gates (ENTER_REDUCED) still requires CONFIRM.

**Backtest note (May 25 2026):** Mode A backtest shows 19 trades (6 symbols) over 37 days — too small for conclusions. RKLB positive (40% WR, +71% avg). Re-run `backtest_scalp.py` every 30 days as data accumulates.

---

## Options System

All 6 phases complete + OPT_SCALP live. Paper trading active.
- First paper trade: IONQ spread (-$480, IONQ pulled back — HV30=103% drove extreme strikes)
- Strategy A: bull call spreads, 30-45 DTE, 5-gate entry (auto on 5/5 gates, CONFIRM on 4/5)
- Strategy B: LEAP calls, 450-730 DTE, 5% OTM
- Strategy C: OPT_SCALP ATM weekly calls, auto-execute both modes
- Capital: $4,000 spread/LEAP pool (4 slots) + $1,000 scalp pool (2 slots)
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
