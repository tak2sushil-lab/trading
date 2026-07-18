# Options Redesign — APPROVED Build Plan (Jul 18 2026)

**Status: USER APPROVED ALL. Build everything, then bug-sweep, then 1-week evaluation
(judge ~Jul 27). This doc is the resume point if the session is interrupted.**

## Context (from the Jul 18 audit — full trail in CLAUDE.md "Options system full audit" + memory `options-audit-jul18`)

- Verdict system anti-predictive (ENTER dirRight 16.7% vs SKIP 32.7%); every gate neutral/backwards except liquidity (cost control).
- News HIGH-BULL conviction = fade signal (65–86% short-right). Conviction table 47 BULL-HIGH vs 5 BEAR-HIGH.
- Bear puts = only validated edge (93% hitBE, 68% dirRight, n=28); CHPT +$360 via equity A+ SHORT trigger = only auto-target winner.
- Funnel: ~11 sequential conditions → zero ENTERs since Jul 2. Scalp engine never fired since May 25.
- ALREADY FIXED this session (database.py `fill_whatif_prices`): user_decision→status WHERE bug + fast_info `lastPrice` key bug + historical-close backfill. Verified 27 rows filled, net -$600.
- Audit scripts: scratchpad `opt_gate_audit2.py` + `opt_audit2.csv` (session scratchpad, may be gone — regenerate from opt_calc_log if needed; method: dedupe symbol/day/kind, score fwd price vs row's own breakeven_pct).

## Approved decisions

1. **USAR LEAP: CLOSE MONDAY (Jul 21) at open session.** $22 LEAP Jan-2028, 1 contract, premium $970, stock ~$15.65. Use limit order at mid (market orders can fail Error 10089). Log outcome via log_trade_outcome.
2. **Direction from Book Health, not news** (port equity selector):
   - In options_trader.py: read equity book health (same computation as auto_trader `compute_book_health()` — trailing-10d A+ signal drift from scan_log; read-only reimplementation or import-safe copy; do NOT import auto_trader — too heavy, see HIGH_VOL_SYMBOLS comment precedent).
   - LONG book ON → bull side enabled (debit call spreads + scalp Mode A) from equity A+ LONG triggers.
   - SHORT book ON → bear puts from equity A+ SHORT triggers (the validated path).
   - Both OFF → options flat (no proactive recycle at all). Telegram states book status once daily, not per-skip.
   - News conviction: DEMOTED to logging/context only. `_proactive_recycle` conviction-leaderboard entry path REMOVED as a trade trigger (keep function for logging or delete; keep ticker_conviction table updating for future scoring).
3. **Deciders shrink to:** liquidity gate (≤15% mid per leg) + IV routing (IVR≥50 → credit variant, <50 → debit) + circuit breaker/caps/slots + book-health direction. 
   - Tech/vol/conviction/momentum gates + MC EV/WR: KEEP COMPUTING + LOGGING to opt_calc_log (instrumentation-first, constitution), but they no longer block/decide.
   - Remove per-strategy regime walls (NORMAL/STRONG for bull, WEAK-only for bear) — book health replaces them. This kills the CHOPPY/CAUTIOUS dead zone and the WEAK-regime ERROR/cooldown churn.
   - Remove "ENTER_REDUCED demoted to SKIP" rule 1 and MC rules 2/3 as blockers (log the would-have-been verdict for scoring).
   - VIX>25 block: KEEP (macro circuit breaker, cheap, defensible).
4. **LEAP exit rule (new):** auto-close at -50% of premium OR underlying crosses below its 200MA... (pick simple: -50% premium stop, watchman enforced, same AUTO_STOP path as spreads). LEAP entries remain allowed only when LONG book ON.
5. **Scalp engine:** gate Mode A/B behind LONG-book-ON. Otherwise unchanged (it will finally get chances when book turns on).
6. **Outcome logging completeness:** log_trade_outcome on ALL close paths (manual OPT CLOSE, resets) not just AUTO_*.
7. **Telegram revamp (user: "overwhelming, mostly no value"):**
   - Tier messages: ACTION (fills, closes, confirm requests, circuit breaker) → send always. JOURNAL (book status, daily EOD summary) → once daily. NOISE (per-symbol proactive errors/skips, "cooldown applied", repeated watchman alerts for same position) → log-file only, never Telegram.
   - Watchman: alert once per position per day max (currently re-alerts same USAR breach repeatedly).
   - EOD options summary gains: book status, funnel line (N candidates → N calc'd → N entered), open positions w/ uPnL.
8. **Dashboard options panel (user asked for "more info, not sure what"):** add to SYSTEM HEALTH/scorecard: options book status (LONG/SHORT enabled?), open positions + uPnL, funnel today (calcs/enters), what-if ledger trailing-14d (would-have-been P&L of skips — now fillable), scalp engine status (armed/idle + why). Keep it one compact panel in dashboard/app.py `get_system_health()` + index.html/app.js.
9. **Evaluation: 1 week (judge ~Jul 27).** Score: trades taken by direction vs book health, what-if of everything skipped (learning loop now works), Telegram volume reduction. No further tuning during the week except clear bugs (constitution).

## Build checklist (execute in order)

- [ ] 1. USAR close: NOT automated — do it Monday via `OPT CLOSE USAR` telegram cmd or a one-off script; verify fill + outcome row + status=CLOSED.
- [ ] 2. `options_trader.py`: add `_book_health()` reader (scan_log trailing-10d drift, mirror auto_trader logic, cold-start ON like equity? NO — cold-start follows equity behavior: <4 days/<30 rows → ON. Both books currently OFF ⇒ options stays flat — correct).
- [ ] 3. Rewire `_process_pending_suggestions`: Step 1 (news queue) → log-only, no trades. Step 2 equity A+ triggers (`_check_equity_scan_triggers`) → primary path, gated by book health per direction, both LONG and SHORT. Step 3 proactive recycle → DELETE as trade source.
- [ ] 4. `run_calculator` / `run_put_spread_calc` / credit calcs: remove regime-wall early-returns; remove MC/5-gate verdict demotions; verdict = liquidity gate + pricing sanity. Keep logging everything incl. would-have-been gates.
- [ ] 5. LEAP -50% stop in watchman `compute_new_stop`/`_check_trade`.
- [ ] 6. Outcome logging on all close paths (`_execute_close_bg`, cmd_close).
- [ ] 7. Telegram tiering (options_trader + watchman): helper `send_tg(tier, msg)`; NOISE→print only; JOURNAL→daily dedupe (state file `.tg_journal_sent.json`).
- [ ] 8. Dashboard options panel (app.py + templates).
- [ ] 9. Restart services: `launchctl kickstart -k gui/$(id -u)/com.sushil.trading.options_trader` and `...watchman`. Verify logs clean startup.
- [ ] 10. BUG SWEEP (user-mandated): re-read every diff; check — book-health SQL matches equity semantics (LONG uses actual_day_pct-intra_chg, SHORT sign-flipped); no import of auto_trader; credit-spread sign conventions (see memory `options_bug_sweep_jun29`); paused/_pending flows still work; scalp cooldown table unaffected; verify with a dry `venv/bin/python -c "import options.options_trader"` compile + smoke_test.py if runnable.
- [ ] 11. Update CLAUDE.md (replace "PENDING USER APPROVAL" para with SHIPPED summary), memory `options-audit-jul18` → status shipped, GLOSSARY.md rows for new names (suggest: options book gate = "Gatekeeper", what-if ledger = "Ghost Ledger" — confirm with user), commit all with clear message.

## Constraints / gotchas for the builder

- venv python always. Services: options_trader + watchman launchd names per CLAUDE.md service table.
- Options tables in **trades.db** (trading.db empty).
- Do NOT rename code/DB identifiers (GLOSSARY rule).
- fill_whatif fix already in database.py — don't re-break the status filter.
- `_get_current_regime()` reads scan_log — keep function (used for logging), just not as wall.
- Equity books currently OFF ⇒ after ship, options will be FLAT until tape turns. That is the intended behavior; say so in the ship summary so nobody "fixes" it.
