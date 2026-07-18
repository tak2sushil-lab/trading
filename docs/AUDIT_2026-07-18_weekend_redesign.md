# Weekend Redesign Audit — Jul 18 2026
Fresh-eyes review of equity + futures: line-review of decision-critical paths, sim/live
alignment, last-week counterfactuals, gate-strictness measurement, pro-grade scoring.
**Nothing here is shipped — every fix candidate needs explicit approval (Standing Rule 1).**

---

## 1. Findings — Futures (futures_trader.py)

| # | Finding | Severity | Evidence |
|---|---------|----------|----------|
| F1 | **"3 consecutive WEAK scans" for SHORT unlock is actually 3 CUMULATIVE scans per day, at 60-second cadence.** `_regime_scan_counts` never resets on regime change (futures_trader.py:2125), so scattered WEAK reads across a choppy morning unlock shorts. sim_replay tracks truly consecutive 5-min bars — live can unlock in 3 minutes non-consecutively, sim needs 15 sustained minutes. | HIGH (live/sim divergence + looser than documented) | Live: cumulative dict; sim_replay.py:782 "consecutive... mirrors live" (it doesn't) |
| F2 | **Higher-Timeframe Agreement gate reads the forming bar.** Live `calc_htf_trend` resamples the raw df5 (includes the in-progress 5-min bar and a partial 30-min bucket); sim uses completed bars only. Same bug class as the Volume Pulse partial-bar bug fixed Jul 17. Verdict can flip intra-bar live. | MEDIUM | futures_trader.py:1061 vs sim_replay.py:465 |
| F3 | **VWAP-cross exit fires on ANY profit > $0** — even +1 tick. Comment says "exit losing longs" (mismatch). Interacts badly with the known capture-efficiency problem (thesis right ~50%, capture 19–31%): small winners get chopped at first VWAP touch. Sim mirrors it, so backtests price it in — a design question, not a parity bug. | MEDIUM (design) | futures_trader.py:1653-1658 |
| F4 | **NY_OPEN +15 session bonus is dead code** — entries are blocked until 10:30 (IB window), so grade_entry can never run in NY_OPEN. Also LUNCH −20 makes A+ nearly impossible 12–1pm: an undocumented 4th time gate. | LOW (misleading) | grade_entry:943 vs run_scan:2139 |
| F5 | **Constitution Art. "max 3 boolean entry gates" — LONG path has ~8**: 10:30 IB wait → 14:00 cutoff → Overnight Veto → RVOL≥0.3 → IB≥50pts → Weather/regime → A+ grade (containing macro-bias + RSI hard skips) → Trend Jury → Volume Pulse → HTF Agreement. Effective entry window: 3.5h of a 6.5h session. | STRUCTURAL | run_scan lines 2129–2390 |
| F6 | **Risk-model doc drift:** code = $15K model, MAX_DAILY_LOSS $3,750, 280pt-stop sizing (Jul 6 re-size); memory/CLAUDE.md still say "IBKR $5K/$1,250 DLL (Jun 16)". Code mirrors prop_rules deliberately — but the documented risk model is 3× tighter than what runs. Needs a decision on which is intended. | MEDIUM (governance) | futures_trader.py:74 |
| F7 | A_EXT verdict from the Jul 17 postmortem applies to TC only — IBKR removed the A_ext gate Jul 6. Recent A_EXT gate_blocks rows come from the TC side. | INFO | futures_trader.py:85 |

## 2. Findings — Equity (auto_trader.py)

| # | Finding | Severity | Evidence |
|---|---------|----------|----------|
| E1 | **sim_today.py is a stale sim** — zero references to Fitness Gate (L2), Probation Exit (L3, live Jun 22) or Book Health Selector (live Jul 18). "Run sim_today after every change" currently validates against a system that no longer exists. Same disease futures/sim_replay had before its rebuild. | HIGH | grep: 0 hits for book_health/layer2 in sim_today.py (last modified Jun 18) |
| E2 | **Stacked momentum amputation:** RSI 70–80 hard skip + STRONG-day 8–12% exhaustion skip + 5m-RSI>85 skip + burst-age penalties (−10/−20) + stale-burst −20. Each was individually validated on May tape; together they systematically veto the strongest movers — consistent with the Jul 17 postmortem's "right tail amputated" (2 winners ≥$50 vs 14 losers ≤−$50 since Jun 1). | STRUCTURAL | grade_setup:1441,1410,1454,1594-1609 |
| E3 | 13 hard SKIP gates run before any scoring; A+/A grades are near-vestigial — the boolean gates do the real selection, the 200-point score mostly ranks survivors. Same pattern as futures. | STRUCTURAL | grade_setup:1345-1477 |
| E4 | Comment/code mismatches: "5m RSI — scoring only, not a hard gate" (line 1364) vs hard gate at >85 (line 1454); `prev_chg` actually holds TODAY's change (daily close[-1] vs [-2]) — name suggests yesterday. | LOW | auto_trader.py:1364,1454,982 |
| E5 | Book Health Selector code reviewed line-by-line — correct (daily cache, per-direction, cold-start ON, SHORT sign-flip right). No issues found. | ✓ | auto_trader.py:3472-3523 |

## 3. Last week through the new lens (Jul 6–17 counterfactual)

**Futures NY (IBKR):**
- **Live actual: 10 trades, +$305** — traded only Jul 6/7/8/14/15; five zero-trade days.
- **Sim with today's production config** (completed-bar RVOL = the Jul 17 fix, graduated
  floor 0.70, Weather-Aware Profit Locks, 200pt backstop): **7 trades, 85.7% WR, +$489.**
- The fixed system trades *different days*: catches Jul 9 (+$17), Jul 10 (**PM_LONG +$515**),
  Jul 14 (+$81) that live missed during the partial-bar-RVOL era, and skips Jul 6's
  lucky +$564 day. Only loser: Jul 8 ORB_SHORT −$401.
- **Overnight Veto still killed Jul 13 (pos 0.32) and Jul 16 (pos 0.24) whole days in BOTH
  live and sim; Jul 17 blocked by Trend Jury wall.** The veto is now the single largest
  quantified opportunity cost (544 OVN_SKIP blocks in 10 days; 4 of 10 recent no-trade
  days were whole-day vetoes).
- Verdict: **yes, modestly better P&L (+$489 vs +$305) with far better quality (6/7 winners),
  and the improvement mechanism is real (bug fix, not luck) — but the trade count is still
  0.7/day. The changes fix correctness, not opportunity capture.**

**Equity:**
- **Live actual: 41 trades, ~32% WR, −$227** (LONG 13t −$22; SHORT 28t −$205 — BEAR_MOMENTUM churn).
- **With Book Health Selector as shipped (both books OFF): ~0 trades, $0.** Better by ~$227
  and 41 round-trips of commissions/attention. The system would have correctly sat out.
- Verdict: **yes, better — but by not playing.** The selector is working as designed
  (capital preservation while the signal edge is dead); it does not restore the edge.

## 4. How strict is the machine? (gate_blocks, Jul 6–17, IBKR MNQ)

REGIME 3,003 · GRADE 834 · OVN_SKIP 544 · RVOL_ENTRY 343 · RVOL 268 · HERO 154 · HTF 3
— versus **ENTER 53** logged pass-events and **10 actual trades**.
Counts are inflated by the 60-second scan cadence, but the shape is unambiguous: the
funnel passes ~1% of scored opportunities, the entry window is 10:30–14:00, shorts need a
separate unlock, and one gate (Overnight Veto) can close the entire day before it starts.
**The system is currently built to trade rarely and win often — the opposite of the stated
goal ("trades often, never misses opportunities"). No parameter tweak changes this; it is
the architecture.**

## 5. Pro-grade scorecard (fresh eyes, against a professional desk standard)

| Dimension | Score | Notes |
|---|---|---|
| Instrumentation & feedback loops | 9/10 | Bouncer Report Card, Nightly P&L Ledger, Black Box Recorder, Book Health, parity harness — genuinely professional. Better than many prop desks. |
| Ops & resilience | 8/10 | launchd everything, reconciliation, backup stops, Telegram control, holiday guards. |
| Risk management | 7/10 | Caps/circuit breakers/backup stops all present. −1 for F6 doc drift, −1 for dead constants (MAX_RISK_PER_TRADE) muddying what's real. |
| Sim/live parity | 6/10 futures, **3/10 equity** | Futures: harness live, 2 known divergences (F1, F2). Equity: sim_today validates a ghost system (E1). |
| Strategy design | 5/10 | Over-gated (F5/E3), amputated right tail (E2), single instrument (MNQ), 3.5h window, whole-day vetoes. Edge measurement says the *signals* work ~50% — the machinery around them is what's leaving money on the table. |
| Capital efficiency / opportunity capture | 4/10 | 0.7 futures trades/day; equity flat; London paper. Most days most capital does nothing. |
| **Overall** | **≈65/100** | World-class measurement bolted onto an over-defended strategy core. |

## 6. Big decisions proposed for this weekend (all need explicit approval)

1. **Fix F1 + F2 as clear bugs** (consecutive-scan counting; HTF completed-bars). Both are
   live/sim divergences — Constitution Art. 4 material. Validate via sim_replay before restart.
2. **Rebuild the equity sim** to import live `grade_setup`/L2/L3/book-health directly
   (the london_v2/parity pattern). Until then, stop treating sim_today as validation.
3. **Overnight Veto override** — biggest quantified opportunity cost on both NY and London.
   London's veto removal is already validated (+$1,114/18mo); NY needs its own sim run:
   same test, `sim_replay` with the veto disabled, full YTD + last 30 days.
4. **Right-tail experiment (equity):** a capped A/B — RSI 70–80 becomes −15pts instead of
   hard skip *only when* Book Health is ON and vol_ratio ≥ 2.5. Backtestable from scan_log
   before any live change.
5. **Entry-window widening (futures):** re-test 9:30–10:30 entries now that Trend Jury +
   Volume Pulse + HTF exist (the old "9:55 is worse" result predates all three gates and
   the graduated floor). If it fails again, accept the 10:30 start as validated, not folklore.
6. **Trade-more architecture:** pick ONE of — (a) second instrument (MES alongside MNQ,
   same signals), (b) London go-live path (needs 1-min-granularity BE re-tune first),
   (c) equity universe expansion via existing `find_candidates.py` DNA screen (quarterly
   re-run is due anyway). Doing all three at once violates the constitution's
   one-change-one-measurement principle.
7. **Decide the risk model (F6):** $15K/280pt/$3,750 DLL (what runs) vs $5K/$1,250 (what's
   documented). Whichever wins, make code and docs agree.

## 7. What was already done this session (no approval needed — docs/housekeeping only)

- GLOSSARY.md extended (Day Shape, Gold Standard, Sympathy Play, Batting Order, parked/
  retired table); CLAUDE.md naming rule 0b added.
- Archived: 13 root one-off scripts, 3 orphaned state JSONs, and 4 dead futures modules
  including `futures_learner.py` (claimed to run nightly; was never wired). All moves
  verified: everything live still compiles, all launchd paths intact.
- Confirmed the 11pm Equity Night School is NOT duplicated by the new nightly auditors —
  it is the only writer of strategy weights + sector grades. All six nightly jobs stay.
