# Options System Rebuild Plan
Last updated: Jun 23 2026

## Strategic Direction

**Two debit strategies already built:** Bull call spread + Bear put spread (complete).  
**Missing:** Credit spreads (bull put + bear call) — the high-WR daily-trade engine.  
**Master routing switch:** IV percentile — below 50% = debit, at or above 50% = credit.  
**Research basis:** tastytrade 200K+ trade study, CBOE data, tradealgo.com decision framework.

---

## Phase 1 — Fix Existing Debit Strategies (1 day)

*Make what we have correct before adding anything new.*

### Gate fixes in `engine.py` + `options_trader.py`

| Fix | Detail |
|-----|--------|
| WR ≥ 50% hard floor | MC model must predict ≥50% win rate or verdict = SKIP. Debit spreads are structurally 30–45% WR — if model says lower, the setup isn't there. |
| EV ≥ $50 hard floor | If MC EV ≤ $50, verdict = SKIP. MARA fired with EV = -$1. |
| IV rank hard floor: 20% | Debit entry blocked if IV rank < 20%. Cheap options = no vol expected = stock dormant. |
| IV rank hard ceiling: 50% | Debit entry blocked if IV rank > 50%. Above 50% = overpaying premium, theta wins, realized vol underperforms. Above 50% → defer to credit (Phase 3). |
| IV rank: silent pass when HV30=None is a bug | When HV30 unavailable, vol gate must SKIP (not silently pass). |
| 5/5 gates required | Remove ENTER_REDUCED as an executable signal. 4/5 = informational Telegram only, no auto-execute, no CONFIRM prompt. |
| Equity regime gate | Read `current_regime` from DB before any spread entry. Bull call spread: NORMAL or STRONG regime only. Bear put spread: WEAK regime only. CHOPPY/CAUTIOUS = no new spreads. |
| VIX auto-gate | VIX > 25 → no new spread entries (equivalent to automatic OPT PAUSE). |

### DTE fix

| Strategy | Current | Fix |
|----------|---------|-----|
| Debit spread (non-catalyst) | 14–21 DTE | Raise to **30–45 DTE**. Less theta drag, more time to be right. |
| Debit spread (catalyst only) | 14–21 DTE | Keep at 14–21 DTE. Binary event = fast move or nothing. |
| Credit spread (Phase 3) | N/A | **45 DTE** entry, exit at 21 DTE or 50% profit. |

VIX-adjusted DTE for credit (Phase 3):
- VIX < 15 → 30–40 DTE
- VIX 15–25 → 45 DTE  
- VIX > 25 → 50–60 DTE

---

## Phase 2 — Fix Fill Quality / Slippage (1 day)

*Affects all 3 existing strategies immediately. Corrupt fill data = corrupt learner.*

| Fix | Detail |
|-----|--------|
| Read `avgFillPrice` from IBKR | Replace `limit_price` recorded in DB. `filled_at` must come from order status `avgFillPrice`. |
| Fix fill anchor for debit entry | Current: starts at `ask_long - bid_short` (natural = worst case) and walks UP. Fix: start at `(mid_long - mid_short)` and walk toward natural in $0.05 steps (max 6 attempts). Never exceed natural. |
| Switch to IBKR MidPrice order type | `order.orderType = 'MidPx'` in `bridge.py`. IBKR's own data: ~$3 improvement per trade. Automatically pegs to mid and adjusts. Use for all spread and LEAP entries. |
| Relative bid-ask gate | Before entry: `(ask - bid) / mid ≤ 15%` per leg. If wider, skip — round-trip cost is too high. Replaces $0.30 absolute check. |
| Slippage tracking in DB | Add `slippage_dollars` column to `options_trades`. Log `(filled_at - mid_at_entry) × 100 × contracts`. Alert if slippage > $30/contract. |
| Cancel by order ID, not global | `cancel_all_orders()` between attempts kills ALL open orders globally. Fix: cancel specific order by ID. |
| Auto-close retry in watchman | `_auto_close_position()` is single-attempt. Fix: 3 attempts with 30s gap between. Alert only after all 3 fail. |

---

## Phase 3 — Credit Spread Strategy (2 days)

*New build. The high-WR (60–75%) daily-trade engine that theta works for.*

### Strategy design (research-validated)

| Parameter | Bull Put Spread | Bear Call Spread |
|-----------|----------------|-----------------|
| Regime required | NORMAL / STRONG | WEAK |
| IV rank required | ≥ 50% | ≥ 50% |
| Entry DTE | 45 (VIX-adjusted) | 45 (VIX-adjusted) |
| Short leg delta | 30 (sell) | 30 (sell) |
| Long leg delta | 10 (buy) | 10 (buy) |
| Profit target | 50% of credit received | 50% of credit received |
| Stop loss | 2× credit received | 2× credit received |
| Time exit | 21 DTE (if not at target) | 21 DTE (if not at target) |
| Expected WR | 60–75% | 60–75% |

### Files to build/modify

**`engine.py`:**
- `_bs_bull_put_vals()` — MC model with selling=True flag (win = spread expires worthless)
- `_bs_bear_call_vals()` — mirror for calls
- `get_iv_percentile()` — true 52-week IV percentile (not IV vs HV30). This is the master routing switch.

**`options_trader.py`:**
- `run_bull_put_calc()` — entry calculator
- `run_bear_call_calc()` — entry calculator
- `_execute_credit_spread_bg()` — SELL as primary action, receive credit
- `OPT INCOME BULL` / `OPT INCOME BEAR` commands (or auto-triggered by regime scan)

**`watchman.py`:**
- Credit spread monitoring: value going UP = losing money (inverse of debit)
- Stop at 2× credit received (current_value ≥ 3× credit = stop)
- Profit at 50% of credit (current_value ≤ 0.5× credit = take profit)
- Same 21 DTE time exit

---

## Phase 4 — OPT_SCALP Quality (1 day)

*Bring scalp to pro-level standard.*

| Fix | Detail |
|-----|--------|
| Mode B price confirmation | Stock must be up ≥ 2% intraday at time of news signal. News from 3h ago + flat stock = no entry. |
| IV rank floor: 20% | No scalp entries at IV rank < 20%. Cheap options = dormant stock. |
| IV rank ceiling: 60% (already 75%) | Tighten from 75% to 60%. Above 60% = overpaying vol for scalp. |
| Watchman interval for scalps | 1-minute monitor for OPT_SCALP positions (currently 15 min). At 7–12 DTE, -50% can happen in 20 min. |
| Fill: use MidPrice order type | Inherits from Phase 2. IBKR MidPrice for scalp entries. |
| WR floor: ≥ 50% from MC model | If model predicts < 50% WR for scalp, skip. |

---

## Phase 5 — News Engine & Noise Cleanup (half day)

| Fix | Detail |
|-----|--------|
| Silence DTE errors from Telegram | "No suitable expiry for X" = log only, no Telegram message. |
| Scan window | Proactive scans: 9:30am–3:30pm ET only. No midnight/3am runs on stale data. |
| Net signal floor | Require net ≥ +2 same-direction signals before conviction fires Telegram. 3 bull + 2 bear = net +1 = insufficient. |
| `already_priced_in=YES` | LLM already classifies it. Wire it: if yes → downgrade conviction to NEUTRAL. |
| News magnitude weighting | Add `magnitude: 1–5` to LLM prompt. FDA approval (5) > partnership PR (2) > analyst note (1). Weight conviction score by magnitude, not raw count. |

---

## Summary Feature Table

| Feature | Phase | Files | Status |
|---------|-------|-------|--------|
| WR ≥ 50% gate | 1 | engine.py | ✅ LIVE Jun 23 |
| EV ≥ $50 gate | 1 | engine.py | ✅ LIVE Jun 23 |
| IV rank 20–50% window for debit | 1 | options_trader.py | ✅ LIVE Jun 23 |
| 5/5 gates required | 1 | options_trader.py | ✅ LIVE Jun 23 |
| Equity regime gate | 1 | options_trader.py | ✅ LIVE Jun 23 |
| VIX auto-gate | 1 | options_trader.py | ✅ LIVE Jun 23 |
| DTE 30–45 for non-catalyst debit | 1 | options_trader.py | ✅ LIVE Jun 23 |
| avgFillPrice from IBKR | 2 | options_trader.py, watchman.py | ✅ LIVE Jun 23 |
| MidPrice order type | 2 | bridge.py | ✅ LIVE Jun 23 |
| Relative bid-ask gate (15% of mid) | 2 | options_trader.py | ✅ LIVE Jun 23 |
| Slippage tracking in Telegram | 2 | options_trader.py | ✅ LIVE Jun 23 (DB column deferred) |
| Cancel by order ID | 2 | options_trader.py, bridge.py | ✅ LIVE Jun 23 |
| Auto-close retry (3 attempts) | 2 | watchman.py | ✅ LIVE Jun 23 |
| Bull put spread | 3 | engine.py, options_trader.py, watchman.py | ✅ LIVE Jun 23 |
| Bear call spread | 3 | engine.py, options_trader.py, watchman.py | ✅ LIVE Jun 23 |
| IV percentile routing switch | 3 | engine.py, options_trader.py | ✅ LIVE Jun 23 |
| Mode B price confirmation | 4 | options_trader.py | ✅ LIVE Jun 23 |
| IV rank floor for scalp (20%) | 4 | options_trader.py | ✅ LIVE Jun 23 |
| 1-min watchman interval for scalps | 4 | watchman.py | ✅ LIVE Jun 23 |
| DTE error silence | 5 | options_trader.py | ✅ LIVE Jun 23 |
| Net signal floor (≥+2) | 5 | database.py | ✅ LIVE Jun 23 |
| already_priced_in gate | 5 | database.py | ✅ LIVE Jun 23 |
| News magnitude weighting | 5 | news_engine.py + database.py | ✅ LIVE Jun 23 |

---

## Validation Before Build

Before coding Phase 1, run backtest against historical `opt_calc_log` (60+ runs):
- Apply new gates retroactively to each signal
- Check: how many losing signals would have been blocked?
- Check: would any winners have been blocked?
- Gate must block ≥80% of losers while keeping ≥90% of winners

Run: `venv/bin/python options/validate_gates.py` (to be created)
