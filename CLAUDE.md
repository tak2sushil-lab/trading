# TriVega Trading System — Ground Truth
**Auto-loaded by Claude Code at session start. Update this file whenever code changes.**
Last updated: Sep 25 2026 (futures deep review)

---

## Quick Facts

- **Account:** IBKR Paper DU9952463, port 4002 | Canada
- **Python:** Always use `venv/bin/python` or `venv/bin/python3`
- **Working dir:** `/Users/sushil/trading/`
- **DB:** `trades.db` (primary), `trading.db`
- **Bridge:** `http://localhost:8000`
- **Clean run since:** May 1 2026 — do NOT change parameters without explicit user approval
- **Universe:** 241 symbols since Jul 18 2026 (pro-grade refresh: 128 incumbents kept incl 4 ETFs, 38 pruned — low-ATR mega caps + sub-$5, 113 S&P1500 DNA-screen adds; `find_candidates_results.csv`). ⚠️ Monitor Monday: scan cycle must stay under 5 min at 241 names.
- **Terminology:** `GLOSSARY.md` (adopted Jul 18 2026) is the canonical name for every gate/score/auditor/shadow book — use its names in docs and Telegram; never rename code/DB/launchd identifiers

---

## Active Work Board (live-maintained snapshot — overwritten each session, not appended)

**Read this first for "what's the status."** The sections below this one are a
chronological log (useful for "why did we do X"); this one is always current for
"what's shipped, what's running, what's still open." Last refreshed: Sep 20 2026.

**🟡 EQUITY DAY TRADER — current state (Oct 1 2026, read first for equity):**
- **LIVE since Oct 1 09:46 ET:** `yf_cache_fix.py` (FD leak) · `DAILY_ROW_FROM_LIVE_BARS=True` (grader reads
  live "today gain"/RSI/MA, not a frozen first-fetch value) · `SCANNER_PICKS_TRADE=False` (non-universe names
  graded + logged, never traded) · `CATALYST_OVERRIDE_ENABLED=False` (duplicate entry path that skipped the
  batting order / Layer 2 / Layer 3 — retired) · **`VOL_SCALED_RISK=True` — LIVE TRIAL** (stop = 1 daily ATR,
  same $ risk per trade; new entries only). scan_log logs `today_gain`, `ret_5d`, `atr_pct`, `is_thrust`.
- **⏰ Volatility-stop trial review ~Oct 29 2026 (20 trading days):**
  `./research_replay_ab.sh voltrial oct 2026-10-01 2026-10-14 2026-10-15 2026-10-28` then
  `venv/bin/python research_replay_score.py oct fixed`. **Keep only if the `vol` arm is not worse than
  `fixed`** (P&L and hard-stop losses) AND live hard-stop losses since Oct 1 are not larger than the
  Aug–Sep baseline (12 hard stops, −$608 in the replay base). Otherwise `VOL_SCALED_RISK=False`.
- OFF and staying off: `MULTIDAY_FRESH_MAX_5D` (freshness, failed the replay), `THRUST_PRIORITY` (one-day result).
- ⚠️ **Backtests start no earlier than 2026-08-04** — bars_5m volume before that is the DataBento backfill
  (~2–5% of consolidated). See the Sep 30 section at the bottom.

**🟢 FUTURES — CURRENT STATE (Sep 30 2026, read first for futures):**
- TC = TopStep **$100K** paper combine (`prop_rules.TC_ACCOUNT_SIZE`), started **Sep 30**: 4 MNQ per long, shorts 1,
  200pt stop, target $6,000 / MLL $3,000 / DLL $2,000. Day 1: **+$303.08**. IBKR unchanged (2c).
- **Daily Tide LIVE** on both accounts (shorts blocked while MNQ > 200d MA). Shared code: `futures/daily_tide.py`.
- Prop state is **rebuilt from the ledger** (NY + London) at startup/EOD — never hand-edit the state file;
  change `TC_COMBINE_START` when a real combine begins.
- All fills are real (`/executions`), with the IBKR paper split-lot artifact cleaned (`futures/fills.py`).
  Trades before Sep 29 (London) / Sep 25 (NY exits) are flagged "est." on the dashboard — user chose NOT to
  back-correct them.
- **London stays ON for TC (user decision Sep 30)** despite the sim saying it cannot survive slippage — WATCH its
  real fills (`entry_signal`/`exit_signal` vs fills) over the next weeks and report the measured slippage.
- Open: forming-bar entries (re-measure), TopStep MLL lock-at-start rule (verify), `commission_rt_tc` placeholder.

**⚠️ NEXT SESSION PRIORITY (rewritten Sep 20 2026 — read this before anything else):**

**THE ONE THING THAT CHANGED THIS MONTH: 91% of our universe's return accrues OVERNIGHT
(Sharpe 2.23) and all four equity engines trade the intraday half (Sharpe 0.16, +4%/yr).**
Confirmed on unselected ETFs and in every year; replicates Lou/Polk/Skouras (JFE 2019).
Full trail: the Sep 20 dated sections below and [[strategy-hunt-overnight-sep20]].

**Clockwork (`factory/live/overnight.py`) is now the book to watch.** Reconfigured Sep 20:

| | before | now |
|---|---|---|
| positions | 10 x ~$1,000 | **3 x $3,333** |
| budget | shared | **its own $10,000**, not taken from intraday |
| fee model | FIXED ($2.00 rt) | **TIERED ($0.70 rt)** |
| earnings | none | **blackout, 1 day** |
| position cap | none (a real bug) | **capped at TOP_N in total** |

Same signal, official prints, 673 nights: old config **−2.78bp/night, negative in all three
years**; new config **+19.09bp, Sharpe 2.01, maxDD −13.7%**. The fee exceeded the edge every
year — it was never a strategy problem.

**TRACK FROM MON SEP 21, in this order:**
1. **09:00-09:31** — the 10 legacy positions ($9,593) exit on MOO. They were entered Sep 18
   under the old config; the book must reach FLAT before the new sizing means anything.
2. **15:40-15:49** — first MOC under the new config: expect **3 names at ~$3,333**, not 10.
   Dry-scan on Sep 20 picked CENX / NUTX / P. Watch for `EARNINGS_BLACKOUT` skips in the log.
3. **16:00-16:40** — entry fills confirm.
4. **17:05 daily** — `com.sushil.trading.overnight_reference` writes `ref_entry` / `ref_exit` /
   `ref_pnl` / `exec_drag`. ⭐ **`ref_pnl` IS THE HONEST READ ON THIS BOOK; `pnl` IS NOT** —
   IBKR paper fabricates auction fills (Sep 18: PI filled 179.00 when the 09:30 bar was
   open=high=low=182.29). Judge the strategy on `ref_pnl`, the plumbing on `exec_drag`.
5. **Over months, not days** — 97% of this book's return comes from the best 5% of nights.
   A long flat or losing stretch is NORMAL and is not evidence the edge is gone.
6. **Benchmark the PICK against a random 3** from the same eligible pool. The selection beat
   random by +7.5bp overall but LOST in 2025 (−2.8bp) — roughly 3/4 of the value is the
   window, 1/4 the sort. If the sort stops earning, widen the book rather than defend it.

**ALSO LIVE, lower priority:**
- `auto_trader.py` — score components + the corrected forward label now log on every scan
  (shipped Sep 18). In ~4-6 weeks the 15 grader weights can be FITTED instead of guessed;
  that is the only untried lever on the intraday book. Do not hand-tune a weight before then.
- Short candidates are GRADED AND LOGGED again from Sep 21 (`BEAR_OBSERVE_ONLY=True`), after
  going dark on Jul 31. Zero orders possible. Short TRADING stays retired.
- Wave Rider / Contrarian — 8 and 2 own-exit trades. Not evaluable. Leave them alone.
- **Options — spreads go LIVE on paper Mon Sep 21 (Sep 20 pm5). Scalps stay frozen.** Root cause found: the book bought structures whose
  median breakeven was a **+8.1% move**, which our universe clears **15.6% of the time in
  7 days** — 17 of 22 closed trades lost, almost exactly the rate the structure dictated.
  Fixed a real bug (expected move was ~20% overstated by a calendar/252 day-count mix, so
  every strike sat 20% too far OTM), switched both debit calculators to **delta-anchored**
  strikes (**breakeven +6.40% → −0.20%** on identical inputs), and shipped the **Edge
  Budget** gate on all four calculators — a structure may only trade when the signal's own
  expected move clears its breakeven. Our A+/A LONG signals measure **−0.79% at 7d vs the
  universe's +0.81%**, so the gate correctly refuses them. ⚠️ **Do not unfreeze to force
  activity** — see the Sep 20 (pm4) section for the one-line flip and exactly what it costs.
  Next real step is Contrarian → options, gated on ~20 own-exit trades.

**⚠️ BEFORE ANY REAL MONEY:** `IBKR_COMMISSION_PLAN='TIERED'` is a MODELLING assumption. The
API does not expose the plan and the live account has never traded. Enroll in Tiered in IBKR
Client Portal or the model understates cost by ~$1.30/trade.

**⚠️ ANY BACKTEST ON THIS UNIVERSE:** 113 of 241 names were screened in Jul 2026 on backtest
win rates of 88-93% over 2024-2026. Benchmark against the universe's own equal-weight
buy-and-hold — never zero, never SPY.

**Equity regime router DEMOTED to a modifier** (`REGIME_AS_MODIFIER=True`, auto_trader.py) on
the completed A/B: router -$2,320 vs modifier -$892, matched-day spread +$1,428, t=+2.18.
Both arms still LOSE — this is "lose less", not "make money", and +$1,345 of the +$1,428 is
August alone. Revert = flip the flag.

**Open, not started:** `BUY <SYM>` Telegram bug (auto_trader.py:2729) opens a new LONG
regardless of an existing position — still not fixed. Reversion shadow book's sunset review
(due Sep 3) still not done. Chart Gate / Thesis Check write to `auto_trader.log` with NO table,
so their weekly reviews have to grep text.

**⚠️ STAGED PLAN (user-set Aug 8 2026, confirmed same night — do not forget or skip
stages). No automation set up for this — user explicitly wants it documented, not a
proactive scheduled check-in. Only resurfaces when the user brings it up next.**
1. **Next few days:** monitor Fish Finder live entries + confirm Chart Gate/Thesis Check
   are actually logging correctly in real live conditions (not just the manual test calls
   from Aug 8) — check `[CHART GATE LOG]` and `[THESIS CHECK LOG]` lines are appearing,
   the Friday weekly reviews fire, no silent failures.
2. **If (1) looks healthy, wire stall-based capital recycling within days — CONFIRMED,
   supersedes the original Aug 8 recommendation.** User explicitly chose speed over
   clean attribution: accepted the risk of two simultaneous live behavior changes
   (Fish Finder + recycling) confounding one observation window, rather than waiting for
   the full Sep 8 Fish Finder review. When this is picked back up: re-read the Aug 8
   backtest section below (90% of stalled 0-0.5% trades eventually lose, banking early
   beat holding by $15,639/11,520 trades) before wiring, and pick an actual threshold
   (time-in-trade + %-band) — none was chosen yet, only the finding was validated.
3. **After (1) and (2): let everything run in pure observe mode for ~2 weeks** to
   accumulate enough Chart Gate / Thesis Check data for the weekly reviews to say
   something statistically real, not just "did it run."

**Shipped and live:**
- ✅ Regime-Adaptive Suite (equity) — **redesigned Aug 8 2026, see dated section below.**
  Internal engine replaced: was one Weather Report reading picking one strategy for all
  241 symbols (`REGIME_STRATEGY_MAP`); now Fish Finder tests every symbol against all 3
  templates on its own signals every scan, plus 3 fixes (Crowd Gauge, Bite Check, Fair
  Cast) diagnosed from the redesign's own OOS failures. `setup_type` now `FISHFINDER_*`
  (was `REGIME_*`). Beats the OLD design in all 4 backtested historical periods for the
  first time in this investigation ($3,942 vs $458 full-history 2024-2026) — but every
  number comes from the same tape used to find the fixes; this live paper trial is the
  actual out-of-sample test. NOT gated by Book Health, shares the existing exit stack
  unmodified. Sunset review **Sep 8 2026**. Rollback point: `git tag
  checkpoint-2026-08-08-pre-fishfinder-wiring`. Full research trail: dated Aug 6-8
  sections below, `analysis_pending` memory. Original Aug 5 daily-trade-cap bug (fixed
  same day it was found) carried forward unchanged into the new engine.
- ✅ `equity_replay.py` v2 rebuild (Aug 6 2026) — now calls auto_trader.py's REAL live
  functions (`_scan_and_enter`, `_scan_and_enter_bear`, `_scan_catalyst_override`,
  `_scan_regime_adaptive`, `monitor_open_trades`) under a frozen clock, not a hand
  approximation. v1 was ~88% parity and had zero coverage of `_scan_regime_adaptive`;
  v2 verified EXACT match at baseline settings. Supersedes v1 and stale `sim_today.py`
  as the equity backtest tool going forward. Closes the equity_replay TODO from Aug 5.
- ✅ `collect_bars.load_bars()` timestamp bug fixed (Aug 7 2026) — `bars_5m.ts_utc` has
  two coexisting string formats (original DataBento backfill vs daily incremental
  collector, overlapping rather than cutting over cleanly ~May 2026); pandas silently
  turned the minority format into NaT (~9% of rows for a spot-checked symbol) plus
  ~9% genuine duplicate bars with conflicting OHLCV from the two sources. Fixed with
  `format='mixed'` parsing + `keep='last'` dedup. Affects ANY code calling
  `load_bars`/`load_multi` across that boundary, not just backtest research.
- ✅ Equity reversion shadow book — `REVERSION_LONG` candidates logging, log-only,
  zero live capital. Sunset review **Sep 3 2026** (~20 trading days needed).
- ✅ Spread Toll Gate (options liquidity decider) — replaced the rule that blocked
  37/37 candidates since Jul 22. Sunset review **Sep 3 2026**.
- ✅ Book Pulse — options book-level Greeks + concentration, now inside the Options
  Positions card itself (moved off System Health Aug 4 to de-dupe).
- ✅ T3a_NEAR options alert tier (80%-to-target, INFO, once/day).
- ✅ Daily option chain snapshots — collector + launchd job running, held positions only.
- ✅ HMM regime shadow logger (futures) — EOD daily, log-only, 508-day history
  backfilled. Live-computable (gating-capable) version still not built.
- ✅ `futures/close_all_now.sh` — the only sanctioned way to manually flatten futures.
- ✅ Dashboard: mobile horizontal-scroll bug fixed, web app manifest added (Safari
  "Add to Home Screen" now gives a real standalone-app feel on iOS).

**Active research threads (not wired into anything live):**
- 🔬 Book Health graded/asymmetric-confirm redesign — graded sizing tested and
  REJECTED (binary holds up better once tested across window lengths); asymmetric
  "2-day confirm ON, instant OFF" showed a real, if thin (n=1 transition), edge —
  proposed, not shipped, awaiting decision.
- 🔬 Sector-conditioned reversion — blocked on the reversion shadow book (above)
  accumulating enough rows — natural cut once the Sep 3 review lands.

**Known gaps, not yet started:**
- ⬜ `BUY <SYM>` manual Telegram command bug (see NEXT SESSION PRIORITY above) — not fixed.
- ⬜ Fish Finder dashboard card (gate states, per-template funnel, P&L split by
  `FISHFINDER_*`) — being built this session, check Active Work Board next refresh.
- ⬜ Regime-Adaptive Suite's "big fish vs small fish" partial-exit idea (predates the
  Aug 8 redesign, still applies) — don't build new logic, redirect to the existing
  `monitor_open_trades` partial-exit-at-1R mechanism, currently hardcoded off for
  Fish Finder trades (`first_bar_strong_trades[trade_id] = False`, always) and
  calibrated for a wider move than these trades typically show.
- ⬜ Options: strike ladder / theta-decay chart (needs more days of chain snapshots).
- ⬜ IV-rank-from-own-chain-history (unblocked by the snapshot collector, not built).
- ⬜ Real-money IBKR funding gap: `IBKR_FLOOR=$5,000` undersized for the system's
  own 2-contract cap at current live margin (~$8,750 to hold size with zero buffer).

**Full detail on all of the above:** `docs/IDEATION_2026-08-03_strategy_and_options_dashboard_review.md`,
`analysis_pending` memory, and the dated log entries below.

---

## Knowledge Graph (graphify)

A knowledge graph of the entire codebase lives at `graphify-out/graph.json` (1,155 nodes, 2,275 edges, 93 communities).

**Session start:** Before reading files, use the graphify skill to orient:
```
/graphify query "where is X defined"
/graphify query "how does auto_trader connect to bridge"
```

**After significant code changes:** The `graphify_watch` launchd service auto-syncs the graph on every `.py` save (AST only — free, no LLM cost). To force a full rebuild (e.g. after adding new files):
```bash
ANTHROPIC_API_KEY=$(grep ANTHROPIC_KEY .env | grep -v "^#" | cut -d= -f2) venv/bin/graphify . --backend claude --update
```

**Services:** graphify_watch is launchd-managed (`com.sushil.trading.graphify_watch`). Restart it the same way as other services if needed.

---

## Service Restart (always use launchctl — never ask user to do it manually)

```bash
launchctl kickstart -k gui/$(id -u)/com.sushil.trading.<name>
# Services: gateway | bridge | autotrader | news_engine | options_trader | watchman
curl -s http://localhost:8000/   # verify bridge is up
```

**⚠️ Futures service names are misleading — verify before restarting (confirmed Jul 7 2026):**
| Service name | Actually runs | Account |
|---|---|---|
| `com.sushil.trading.futures_personal` | `futures/futures_trader.py` | IBKR paper DU9952463, port 8000 |
| `com.sushil.trading.futures_trader` | `futures/tc_trader.py` | TC Sandbox DUQ640500, port 8002 |

Editing `futures_trader.py`? Restart `futures_personal`, not `futures_trader` — the name is the opposite of what you'd guess. Check `~/Library/LaunchAgents/com.sushil.trading.<name>.plist` → `ProgramArguments` if ever unsure.

After any equity code change: restart service → run `venv/bin/python sim_today.py`.
After any futures NY-session code change: restart `futures_personal` → validate with a direct backtest against the actual edited functions (FakeDatetime monkey-patch replay pattern — see any recent futures backtest script). **`futures/sim_replay.py` is stale and does NOT call current `futures_trader.py` logic** (confirmed Jul 7 2026 — its trade log shows no thesis-invalidation/backstop reasons and different stop-distance dollar values than production actually uses) — do not treat it as a validator until someone rewires it to import and call the live functions.

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
  watchman.py                — 15-min monitor (launchd-managed, always running — KeepAlive=true)
  learner_options.py         — nightly what-if analysis
  backtester_options.py      — B-S backtest [Phase 5 update pending]

backtest_scalp.py            — OPT_SCALP Mode A backtest (scan_log A+ + 5-min bars proxy)

futures/
  futures_trader.py            — live IBKR personal (port 8000, DU9952463). LONDON_ENABLED=True
                                 Jul 7 2026: exit stack redesigned — BASE_STOP_PTS=500 (rare
                                 catastrophic backstop, was 150) + signal-based "thesis
                                 invalidation" exit in monitor_open_trades() (cuts on regime/
                                 HTF/momentum/VWAP turning against the position, 2-of-4 votes
                                 sustained 2 closed bars) does the real loss-cutting now, not
                                 stop distance. Validated: May-Jul $8,561→$10,997 (+28%), full
                                 2026 YTD $11,010→$27,779 (+152%), SHORT side flips from -$1,315
                                 to +$8,118. Entry logic (regime/RVOL/HTF/A+-only) UNCHANGED —
                                 this only touched the exit. See "Changes Applied Jul 7 2026"
                                 below for the full validation trail and known limitations.
  tc_trader.py                 — TC eval mode (TopStepX, port 8002)
  london_trader.py             — London session live trader (LIVE Jun 17 2026, paper validation)
                                 3am–9am ET. IB formation 3am–4am. Signal A entries 4am–8am.
                                 Champion: stop=2.0 target=6.0 BE=0.10. $5k/$1,250 DLL model.
                                 Plug/unplug: flip LONDON_ENABLED in futures_trader.py + restart
  sim_replay.py                — NY session bar-by-bar replay (mirrors futures_trader.py)
  london_sim.py                — London session simulator. Validated 2025-2026: 467t 42.4% $+10,608
                                 Default: --signals A --no-ib-clean --start 2025-01-01
  collect_bars.py              — futures bar collector (futures_bars_5m, 2021→today)
```

### London Session Backtest Commands
```bash
# Standard run — file defaults now match champion (stop=2.0, target=6.0, BE=0.10)
venv/bin/python futures/london_sim.py --signals A --no-ib-clean
venv/bin/python futures/london_sim.py --signals A --no-ib-clean --start 2025-01-01   # faster
venv/bin/python futures/london_sim.py --compare --no-ib-clean --start 2025-01-01    # 7 combos
venv/bin/python futures/london_sim.py --signals A --no-ib-clean --detail            # trade-by-trade
venv/bin/python futures/london_sim.py --stats --start 2025-01-01                    # data dist

# Trail params (tuning complete Jun 15 — champion locked):
# --be-mult 0.10       break-even trigger (ATR×) — CHAMPION, do not change
# --trail-wide-atr 1.00   wide trail activation — CHAMPION
# --trail-tight-atr 1.50  tight trail activation — CHAMPION
```

### London Tuning Summary (Jun 15 2026 — complete, $5k model)
| Parameter | Old default | Champion | Change |
|-----------|------------|----------|--------|
| STOP_ATR_MULT | 1.5× | **2.0×** | Wider stop → higher WR, less noise |
| TARGET_ATR_MULT | 3.0× | **6.0×** | Pure trail — target almost never fires |
| BE_ATR_MULT | 0.50× | **0.10×** | Protect entry at +5pts → saves fakeout losses |
| 2025-2026 P&L | baseline | **$10,608** | 467t, 42.4% WR, MaxDD $321 |
| Risk model | $100/trade | **$250/trade** | $5k account, $1,250 DLL |

---

## Futures NY Session — Changes Applied Jul 7 2026 (futures_trader.py)

Same day: (1) regime detection rebuild (RVOL gate + hybrid day/session reference + 5-bar
trend), (2) entry gates tightened to RVOL≥0.85 + 30-min HTF trend agreement + A+-only grading
(was A-or-A+), (3) exit stack redesigned (this section). Entry logic and exit logic are
independent changes — (1)+(2) already validated and live before (3) was designed.

**Entry-gate tightening (1+2) impact, measured on the 15 trading days before this change:**
pre-gate baseline -$7,701 (54 trades, 40.7% WR) → post-gate +$712 (25 trades, 56.0% WR). An
$8,413 swing on the exact same window, mostly from trading far less but much better.

**Exit-stack redesign (3):** `BASE_STOP_PTS` 150→**500** — no longer the primary loss-cutting
mechanism, now a rare catastrophic backstop (0% hit rate across every backtest run). Real exit
logic is new **thesis invalidation** in `monitor_open_trades()`: exits at market when 2-of-4
signals turn against the position (regime flip, 30-min HTF flip, opposing momentum, opposing
VWAP cross/reclaim) and stay that way for 2 consecutive closed 5-min bars. Existing profit-lock
trail tiers (`BE_ACTIVATE_PTS`/`TRAIL_WIDE_PTS`/`TRAIL_TIGHT_PTS`) unchanged — still protect
winners the same way. `MAX_RISK_PER_TRADE` raised 300→2000 for documentation consistency, but
confirmed **dead code** (only reader, `calc_contracts()`, is never called — live sizing is
`calc_contracts_dynamic()`, RVOL/IB-range tiers, 1-2 contracts, unrelated to stop width).

Validated (FakeDatetime monkey-patch replay against real 5-min bars, not synthetic data):
| Window | Old (150pt stop) | New (backstop + thesis-invalidation) |
|---|---|---|
| May-Jul 2026 (97-98 trades) | $8,561, 61% WR, 59% thesis-confirm | **$10,997 (+28%)**, 59% WR, 59% thesis |
| Full 2026 YTD (262-267 trades) | $11,010, 53% WR | **$27,779 (+152%)**, 54% WR |
| SHORT side, full 2026 YTD | -$1,315 (losing) | **+$8,118** — fixes the known short-side weak spot |
| Split-window (robustness) | — | May $3,865 / Jun-Jul $7,131 — both positive, not concentrated |

**Known limitation — read before assuming this solves choppy-day losses:** on the 15 trading
days *before* this exit change (a purely choppy stretch, no trending days to harvest big wins),
the new exit stack was roughly neutral-to-slightly-negative vs the old flat stop (-$209 vs
+$712), and its worst-case single-trade loss (-$1,005 to -$1,195) can exceed the old system's
hard-capped -$600 — the 2-bar confirmation window can let price whip further against a position
during fast chop before confirming failure. **This is a full-cycle improvement (trend-day gains
outweigh chop-day noise over a multi-month window), not a chop-specific safety improvement.**
Four different live-computable chop detectors were tried to make the exit regime-aware (tighten
confirmation to 1 bar during detected chop) — none worked: within-session VWAP-crossing count
(too few trades ever flagged in time), ADX-at-entry (no effect at full sample size, best thesis-
confirm was actually the HIGH-ADX bucket), morning-session character (backwards — choppy
mornings preceded *better* afternoon trades), rolling regime-flip count (never triggered
differently at the moments that mattered). Chop-aware exit switching remains an **open
problem**, not solved — do not assume a future session can trivially crack this without new data
or a genuinely different signal.

**Does this apply to London?** No — `london_trader.py` is a separate module with its own session
window (3am-9am ET vs NY's 9:30am-4pm) and its own champion parameters (ATR-multiple stops, not
point-based). None of Jul 7's testing touched it; it would need separate validation.

---

## Futures NY Session — Changes Applied Jul 8 2026 (futures_trader.py)

**Context:** Jul 8 was a down day (-$556 IBKR, -$264 TC) driven almost entirely by the Elephant
module (`_scan_elephant`) buying dips into what turned into a real trend reversal (day_chg
+0.5%→-1.4% over ~2hrs). Investigated whether Elephant needs regime-awareness added.

**Elephant module — investigated, NO CHANGE SHIPPED.** Tested two hypotheses against the full
5.5yr backtest (`elephant_backtest.py`, N=20 trades total, only 4 in all of 2026): (1) invalidate
the day's STRONG_BULL classification once day_chg turns meaningfully negative — **disproven**,
the two most-negative historical day_chg-at-entry trades (-0.89%, -0.96%) were both winners
(+$291, +$20), since this is a mean-reversion strategy where "looks bad at entry" is the normal
premise, not a red flag. (2) Faster ES co-confirmation window (20min vs 60min) — also doesn't
separate historical winners from losers cleanly. **Conclusion: N=20 (4/yr) is too small to fit a
reliable new gate; today's 2-of-3 losers is ordinary variance for a 65% WR / avg-win-$194 /
avg-loss-$140 strategy at this frequency.** Do not add an Elephant regime-invalidation gate
without a much larger sample or a cleaner separating signal than the two tested here.

**Graduated RVOL — SHIPPED.** Second real-world instance (after a Jul 7 near-miss) of the hard
RVOL<0.85 cliff blocking good setups: three A+ SHORT signals (150/130/130pts) killed at
0.73/0.68/0.72 during Jul 8's confirmed WEAK-regime downtrend, right as price kept falling.
Backtested via `sim_replay.py --graduated-rvol --rvol-floor N` (full 2026 YTD, Jan 1–Jul 7,
complete pipeline incl. Hero gate + 14:00 cutoff): entries with RVOL between a floor and 0.85 are
now allowed through if the Hero score already clears that regime's GOLD threshold on its own
(compensating quality signal), sized naturally thin by `calc_contracts_dynamic` since RVOL stays
well under its own 2.0× scale-up tier.

| Floor | Trades | WR | Total P&L | MaxDD |
|---|---|---|---|---|
| Baseline (hard 0.85) | 57 | 64.9% | $3,906 | -$1,699 |
| 0.75 | 73 | 64.4% | $3,919 | -$1,970 (not worth it — no P&L gain, worse DD) |
| **0.70 (shipped)** | 84 | 60.7% | $4,495 (+15%) | -$2,027 (+19%) |
| 0.60 (tested, rejected) | 99 | 59.6% | $4,912 (+26%) | -$2,363 (+39% — worse risk/reward at the margin) |

Shipped `RVOL_GRAD_FLOOR = 0.70` in `futures_trader.py` (both LONG and SHORT gate blocks) plus
`hero_score.is_gold_score()` helper, mirrored in `sim_replay.py --graduated-rvol --rvol-floor`.
Restarted `futures_personal` same day, verified clean startup + bridge connected. TC
(`tc_trader.py`) has no Hero gate infrastructure to compensate with — not touched, not applicable.

---

## Futures NY Session — Jul 7 Deep Dive (Jul 8 2026 pm, log-forensics, not sim)

User watched Jul 7 live (clean short 9:30-10:40 / consolidate+rally 10:40-13:55 / short 14:00-15:10
day) and correctly pushed back that a backtest-only answer wasn't good enough — asked for the real
gaps. Traced the actual production log line-by-line (not sim_replay) and found 3 causes:

1. **"Large IB gate" delayed IBKR's first entry to 10:45am** (any day with pre-10:30 IB range
   >200pts) — but Jul 7's whole first-hour decline had already happened by 10:20am, so IBKR
   shorted at 29270-29279, just 60-70pts above the actual low (29210 @ 10:40), right as the move
   was ending. TC (no such gate) shorted at 10:32-10:33 for clean +$92.5/+$85.5 wins on the same
   thesis. **Already fixed** same evening, commit `1038c1a` (8:00pm ET, after close) — code
   comment confirms: *"Was blocking the confirmed 09:55am SHORT entry on Jul 7 itself."*
2. **`MAX_DAILY_TRADES` was still hardcoded at 2 for the entire live session** (the "raised to 5"
   part of that same commit didn't land until 8pm). 17 more fully-graded A+ SHORT signals
   (heroes=5/TRENDING) fired and got hard-BLOCKED between 11:32-11:49am alone. **Already fixed**,
   same commit.
3. **Hero gate cannot confirm intraday grinding trends without an ORB break — STILL OPEN.**
   13:11-13:59pm, price ground up ~270pts (RSI 76-81, consistently 85-95pts above VWAP) — LONG
   fired every scan, Hero-skipped every time. `H2_MTF_ALIGNED`/`H3_RSI_MOMENTUM` are computed on
   1H-resampled bars (14-20hr lookback): at 13:11 the 1H RSI read **41.67** (bearish!) because
   that morning's crash was still inside the same 14-period window, and the 20-period 1H MA
   (29644.76) sat above current price (29534.5) — anchored to days-old levels. Same-session 5-min
   RSI read 72-81 the whole time. Confirmed grade-level fix (`--rsi-trend-exempt`, waive RSI
   penalty when vwap_reclaim+momentum confirm) has **zero effect** full 2026 YTD — Hero gate was
   always the real wall, not grade. Built a same-day-only substitute hero
   (`hero_score.score_h6_intraday_trend`, opt-in `H6_WEIGHT`) and grid-searched weight 1/2/3 —
   **monotonically worse at every weight** (baseline 54t/64.8%/+$1,991 → weight=3: 67t/58.2%/
   **-$226**, net loss). REJECTED, not shipped. Code kept as a disabled research hook
   (`H6_WEIGHT=0` reproduces live exactly) — see [[futures_jul7_deep_dive]] memory for full trail.

**Methodology lesson:** read the actual production log first for "why did we miss X" questions —
`sim_replay.py` correctly mirrors `grade_entry()` byte-for-byte, but two of the three causes here
were real production bugs only visible in the raw log, not in any backtest.

---

## Futures NY Session — PM_SHORT disabled + regime-aware exit stack (Jul 8 2026 evening)

User pushed further: "not curve fitting, but the system doesn't read the chart in time" — asked
for a full re-evaluation of the entry/exit design, not another single-gate patch. Pulled real
trade history (60 days, `futures_trades` excl. RECONCILED) and measured actual 30-min forward
price action after every entry — found the real story is different from "wrong direction":

- **~45-58% of trades DO see a genuine ≥100pt favorable move within 30min** ("thesis right" rate)
  — direction-calling works close to half the time. The system is not blind to real moves.
- **But average capture was only 19-31% of the available move**, and 4-of-15 trades in one 15-day
  sample turned a real 100+pt favorable move into a net LOSS. Not a single "Target hit" exit in 15
  days — every exit was a stop or trailing-stop hit. Root cause: `BE_ACTIVATE_PTS` (profit-lock
  activation) sat at the same 150pt distance as the hard stop itself, but empirical real moves
  cluster at 75-160pts — under that bar most of the time, so genuine moves round-tripped back to
  the full original stop with zero profit protection ever engaging.
- **PM_SHORT: 0-for-7 lifetime, -$1,544, DISABLED.** All 4 independent episodes (Jun 16/17, Jul
  1/7) show the identical mechanical failure — it shorts right at/near the exhaustion LOW of a
  decline (a stop-hunt through the pre-market low), not a genuine breakdown continuation. Same
  phenomenon Elephant already trades correctly in the opposite direction. `sig['pm_bear']` hard-
  coded to `False` in `get_signals()` — near-zero cost, no evidence it ever worked.
- **PM_LONG: kept, not disabled.** Its entire -$130 lifetime deficit is ONE bad day (Jun 12,
  -$588); excluding it, PM_LONG is +$475/10 trades. Different problem than PM_SHORT — genuinely
  works most of the time, needs a guard against the "buy the top" failure mode it showed once, not
  a shutdown.
- **Exit stack: user explicitly rejected a static-number fix ("smarter, day-aware, not threshold-
  specific")** — reused the SAME IB-range day classification already computed for Hero-gate
  weighting (`detect_regime`, CHOPPY/QUIET/TRENDING, sticky at 10:30 IB formation) rather than
  inventing a new real-time chop detector (those were tried and rejected on the entry side, see
  [[futures_exit_stack_jul7]]). **SHIPPED** `EXIT_PARAMS_BY_REGIME` in `futures_trader.py` —
  CHOPPY/QUIET lock in fast+tight (BE at 90pts, tightens to a 35pt trail), TRENDING gets real room
  (BE at 110pts, trail stays 110-180pts even at its tightest) so a genuine trend doesn't get
  choked early. `BASE_STOP_PTS` widened 150→200 to support this (decouples "how wide is the hard
  stop" from "when does profit-lock engage" — they don't need to be the same number).

Full 2026 YTD backtest (re-run fresh at ship time, since `market_data.db` drifted mid-session from
the nightly collector — see note below): baseline (flat 150/150) N=57 WR=64.9% $+3,906 MaxDD=
-$1,699 → regime-aware N=57 WR=68.4% **$+4,824 (+23.5%)** MaxDD=**-$1,460 (14% better)**. Beats
baseline on every metric.

**Known limitation, not yet fixed:** TRENDING's low lock-fraction (0.20, tuned for genuinely huge
moves) under-protects a *modestly*-trending day that gets misclassified as TRENDING purely because
its first-hour IB range crossed 200pts without a real sustained trend (e.g. Jun 18 2026 — a slow
+0.34% grind). A v4 attempt raising the fraction to 0.40 fixed that case but cost more on
genuinely-huge trends elsewhere (worse in aggregate, $2,583 vs v3's $3,095) — IB range alone can't
cleanly separate "real trend" from "wide whipsaw," the same fundamental problem already flagged
for entry-side chop detection. A real fix needs a smoothly graduated lock-fraction (scales
continuously with how far peak actually got), not a flat per-regime number — parked for a future
session, not solved today.

**Also tested and rejected:** pulling the IB-ready entry window earlier (9:55 instead of 10:30,
hypothesis: catch more of the morning move) — full 2026 YTD, adds 26 more trades but total $ stays
flat (actually slightly down) because most of the added trades are PM_SHORT (the just-disabled
setup) and other lower-quality signals that a more-mature 10:30 IB window naturally filters out.
10:10 (a "middle" compromise) was worse still — there's a genuine bad-timing zone around
10:00-10:15, not just "later is always better."

**Data-integrity note (recurring):** `market_data.db` bar data visibly shifted mid-session more
than once (baseline backtest numbers moved between otherwise-identical re-runs), consistent with
the nightly collector actively refreshing recent days' bars while this session was running past
market close. Always re-verify the *relative* comparison (A vs B, same run) rather than trusting
an absolute number from earlier in a long session — this doesn't invalidate any conclusion here
since every comparison in this section was re-run fresh immediately before the ship decision.

Full diagnostic trail (60-day capture-efficiency analysis, PM_SHORT/PM_LONG episode-by-episode
price charts, the full v3/v4 exit-stack grid search, entry-window test) in
[[futures_jul8_gap_hunt]] memory.

---

## Futures — London skip-day bug found + fixed (not shipped) + confirmed cold streak (Jul 8-9 2026)

User read an actual live chart (not a backtest) and spotted a missed 4:15am London short (MNQ
29409→28982, 427pts on 24,146 volume vs ~5-9K typical). Traced to a real architectural bug:
`compute_overnight_bias_london()` (both `london_trader.py` and `london_sim.py`) looks only at
7pm-prev-day→3am-today, and if the 3am read is "ambiguous" (pos 0.20-0.40), sets `skip_day=True`
— **vetoing the ENTIRE 3am-9am session** with no way to reconsider once trading is underway. Jul 8:
pos=0.21, day skipped, missed the move 15 minutes later.

**Fix built** (`london_sim.py: _scan_skip_override`) — ports the equity side's already-validated
"dynamic catalyst upgrade" pattern (auto_trader.py `_scan_and_enter`): watch a skip-day for a
self-referential volume spike (2.5x preceding-hour average) + large directional move (150pts/4
bars), take one trade if found. Found and fixed a real bug during testing (baseline lookback
needed bars only from `entry_bars`, which start at 4am — couldn't evaluate until 5am, missing the
4:15am window it was built for; fixed by extending into the 3-4am IB-formation bars).

**Backtest, full 1.5yr history**: N=14 override trades ever, WR=21.4%, net +$172 — thin, unproven,
worse MaxDD ($321→$422). **Not shipped**, `SKIP_OVERRIDE_ENABLED=False` by default.
**Backtest, last 10 trading days**: baseline -$13 (WR 16.7%) → with override +$490 (WR 21.4%) —
helps a lot recently, but almost entirely from the one Jul 8 catch; don't over-read N=1.

**Separately confirmed**: champion London strategy's recent 16.7% 10-day WR is a genuine anomaly —
only 6/238 rolling 10-day windows in 1.5yr history were ever this bad (bottom 3rd percentile).
Ruled out thin IB ranges (actually wider than average, 79th percentile) and dirty IBs (doesn't
explain most losses). Real pattern: near-instant fakeout stop-outs (-$0.24, -$0.74 repeatedly) —
false breakouts, not failed real moves. Signal A has no volume or retest confirmation at all.
**Two untested candidate fixes for next session**: (1) require above-average volume on the
breakout bar (mirrors NY's RVOL, not yet applied to London), (2) require 2 consecutive closes
past the IB level before entering ("acceptance," not just a touch). Neither built yet — start
here. Full trail: [[futures_london_skip_day_and_cold_streak]] memory.

---

## Jul 17 2026 — Full 10-day postmortem (all 3 systems) + RVOL partial-bar bug fix

10-day scoreboard (Jul 2–17): Equity **-$300** (46t, 28% WR — May was +$1,670/57%). NY futures
IBKR **+$305** but only 10 trades with 5 zero-trade days (June was -$775). TC **-$594**. London
live **-$395 since Jun 17** (29 of 30 exits = 'stop', mostly ±$0.24 BE scratches).

**FIXED same day (clear-bug rule): `calc_session_rvol` partial-bar bug** in `futures_trader.py`.
Live `get_bars()` includes the currently-forming 5-min bar, so entry-gate RVOL saw-toothed
0.05→0.9 within every bar (visible in each regime_detail log line) — the 0.85 gate was calibrated
on completed bars (sim_replay/decoder), so the effective live gate was ~2× stricter than anything
ever backtested, and the Jul 8 graduated floor (0.70) changed almost nothing live. Now scores the
last *completed* bar only. Service restarted + verified 22:50 ET Jul 17.

**Gate-audit verdicts (gate_blocks, scored vs actual 30m/60m forward moves — decisions pending
user approval, do NOT ship without it):**
- **A_EXT: harmful.** 245 scored blocks, 30% correct, blocked signals averaged **+57pts favorable
  at 60m** (LONG +49, SHORT +65). Recommend disabling.
- **GRADE (A+-only for LONG): too strict.** Blocked LONGs averaged +27pts at 60m (38% correct).
  Blocked SHORTs averaged -66pts (correct to block). Recommend re-allowing A-grade LONGs only.
- REGIME (59% correct, blocked avg -18pts) and HERO (59%, +0.2pts) are earning their keep on
  average. RVOL_ENTRY scored 48% = noise, but all its data is from the partial-bar era — re-score
  after the fix beds in before judging.
- **Zero-trade days explained:** Jul 13/16 = OVN_SKIP vetoed the entire NY session (120+ blocks
  each day — same whole-day-veto architecture flaw as London's skip-day). Jul 9/10 = REGIME +
  RVOL_ENTRY walls. Jul 17 = HERO wall (90+ consecutive A+ LONG signals skipped via the known
  1H-lag issue; note Jul 17 was a V-chop, skip roughly broke even — verified against bars).

**Equity diagnosis:** L3 T+5 gate is NOT the villain — counterfactual vs bars_5m shows only 4 of
29 L3-scratched trades since Jun 22 would have reached +2.5%; most went further adverse. The real
disease: **the right tail is amputated** — since Jun 1 only TWO winners ≥$50 vs FOURTEEN losers
≤-$50 (May: CATALYST LONG +$1,319 at 60% WR). L2 HALF-sizing + PCT trail (1.5%) + 5m trail cap
winners at ~+$25-40 while 5% stops on thin catalysts still lose $50-300. Plus July churn:
BEAR_MOMENTUM fired 28 of 46 trades (25% WR, -$205), batch-shorting 3-5 correlated names within
seconds at ~10:03am, majority down at T+5 → L3-ejected.

**London go-live (was scheduled Jul 17): NOT recommended — paper validation failed.** Live -$395
vs sim +$148 on the identical window (sim itself shows the champion in a genuine cold streak:
22% WR). Live-specific gap: BE=0.10×ATR (~+5pts) triggers within ~1 min on the 15-second monitor
then noise stops out at entry — the 5-min-bar sim never modeled this churn; the champion's BE
tuning is granularity-dependent. Before any go-live: build the two parked entry-confirmation
fixes (breakout-bar volume, 2-bar acceptance), re-tune BE on a tick/1-min-granularity sim, and
demand a positive paper month.

---

## Jul 18 2026 — Equity BOOK HEALTH SELECTOR shipped + futures redesign step 1

**The redesign direction (user-approved Jul 17): trade only what is currently working, stand
down when nothing is.** Executed equity-first, then futures IBKR.

**Equity — BOOK HEALTH SELECTOR (SHIPPED, live):** `book_is_on()` / `compute_book_health()` in
`auto_trader.py`, gating `_scan_and_enter` (LONG), `_scan_and_enter_bear` (SHORT), and
`_scan_catalyst_override` (LONG). Health = trailing-10-trading-day mean favorable post-signal
drift (`actual_day_pct - intra_chg`, sign-flipped for SHORT) of ALL enriched A+ scan_log
candidates in that direction — entered or not, so it measures the SIGNAL's current edge, not our
execution. Book trades only when health > 0; cold start (<4 days / <30 rows) defaults ON.
- **Validated on all 259 live trades May 1–Jul 17, no look-ahead:** kept book +$1,796 (162t,
  59% WR) vs skipped -$1,135 (97t, 38% WR) vs actual system +$661. Selector shut the LONG book
  Jun 5 (right at June bleed onset), shut SHORT Jul 6 (July churn), kept June shorts (+$134,
  65% WR). Robust across window 10–15d and thresholds −0.25…+0.25 (plateau); 5d window is
  much worse — do not shorten it.
- Values at ship time: LONG −0.51 → OFF, SHORT −0.60 → OFF (system correctly flat until tape
  turns). Telegram posts book status daily at first scan.
- **Why the edge died in June (postmortem for context):** signal-level fwd60 of A+ LONG
  candidates flipped from +0.40%/64% pos (May) to −0.24…−0.28% (Jun/Jul). Headroom-to-day-high
  after signal is stable (~+2%) in ALL months, but since June price fades below signal by the
  close (drift −0.6…−0.7%, worse the more extended the stock). Tested and REJECTED as fixes:
  SPY-VWAP tape filter (SPY bars end Jun 1; May-only), universe breadth filter (no separation),
  3-day signal-health (inverts, no persistence), 5-day continuation-rate weather gauge (corr≈0),
  earlier-entry / HOD-distance / time-of-day pockets (none positive since June), fixed
  target/stop harvest exits on live entries (negative in Jun/Jul under EVERY combo — the June+
  entries lose under any exit scheme; exits were never the problem, May's live exits BEAT all
  simple exit sims by riding runners).

**Futures IBKR — step 1 (same session):**
- RVOL partial-bar bug fix already live (see Jul 17 section) — sim always used completed bars,
  so this closes the main live/sim divergence going forward.
- **A-grade-LONG relaxation: RE-TESTED post-RVOL-fix and REJECTED** (sim_replay full pipeline,
  Jan 1–Jul 17: baseline 92t/65.2%/$+5,198 vs variant 93t/65.6%/$+5,145 — one extra trade, no
  gain). Third confirmation that gate-audit per-gate drift scores (+27pts on blocked LONGs) do
  NOT survive the full pipeline. Do not re-attempt without a new mechanism.
- **⚠️ sim_replay.py `--end` defaults to 2026-06-16 (hardcoded)** — any "full YTD" run without
  an explicit `--end` silently excludes everything after Jun 16. Always pass `--end`.
- Equity-style book-health selector for MNQ: **deferred, not validatable yet** — per-side
  pts_60m health ≈ recent market direction at MNQ (near-mirror LONG/SHORT, flips daily) and only
  18 days of scored gate_blocks data exist. Revisit at ~60 days of post-fix audit data.
- OVN_SKIP whole-day veto (4 full days vetoed since Jun 26): same architecture flaw as London
  skip-day, but modeled identically in sim (not a divergence source) — left alone, needs its own
  validated override design later.

---

## Jul 18 2026 — Redesign build session (decoder harness, London rebuild, parity, ledger)

User authorized finishing the redesign ("overhaul this entire system"). All shipped same night:

**① Parity harness — `parity_check.py` (launchd `com.sushil.trading.parity_check`, 22:45 ET
nightly):** replays today via sim_replay with production flags, diffs trades vs live
futures_trades (IBKR). First run immediately caught a real divergence: Jul 15 live took TWO
ORB_SHORT entries 1 min apart, sim takes one. `SIM_FLAGS` constant inside must be updated
whenever live futures config changes. Logs → `logs/parity.log`, exit 1 on divergence.

**② Expectancy ledger — `futures/expectancy_ledger.py` (launchd
`com.sushil.trading.expectancy_ledger`, 22:35 ET nightly):** (a) evaluates the SHADOW fish-net
(below) into `shadow_fishnet` table, (b) joins nearest decoder snapshot onto every gate_blocks
row → `gate_blocks_ctx` (feature store for the future scored entry model — 7,579 rows
backfilled; train only at ~60 days), (c) prints trailing-14d expectancy per book to
`logs/expectancy_ledger.log`.

**③ Decoder fish-net (SHADOW ONLY — places no orders):** mined all 22,973 decoder snapshots
(Jun 18–Jul 17) against 1-min bars, IS/OOS split at Jul 6. Decoder's raw signals are
ANTI-predictive at 60m in both halves (LONG −20/−13pts, SHORT −9/−11 favorable) — that's why
its internal sim loses; no conditioning (flow/ADX/phase/vol/session) rescues them. But FADING
them survives everywhere: +16.3 IS / +10.0 OOS pts per episode (547 episodes, deduped), and the
edge is entirely in fading LONG signals (+34/+15; fading SHORTs ≈ 0). Shadow book = fade
decoder LONG-signal episodes, one position, 60-min time exit, 60pt stop. **This is a
mean-revert-regime edge measured in ONE month of mean-revert tape — constitution Arts. 1/7:
30+ days green shadow + health gate before promotion is even discussed.**

**④ London rebuilt — verdict from `futures/london_v2_sim.py` (new, 1-MIN granularity,
2025-01→2026-07, 535K bars):**
- **v1's $10,608 champion was a 5-min-bar granularity artifact.** At 1-min truth the same
  mechanics make $3–4/trade (~$2K/18mo, 90% WR of tiny BE scratches).
- **BE=0.10 is load-bearing armor, NOT the bug** (reversing the Jul 17 hypothesis): without it
  the raw IB-breakout LOSES −$1,647/18mo. The IB break has no follow-through edge; early BE is
  what keeps it alive.
- **Parked "fixes" both REJECTED:** volume confirmation and 2-bar acceptance make it WORSE
  (+$3,067 → +$86) — they delay entry past the only good part of the move.
- **Fading London breakouts REJECTED:** loses both years at every BE setting (NY fish-net does
  not generalize to London hours).
- **Skip-day veto removal VALIDATED and SHIPPED:** +$1,114/18mo, flips 2026 from −$15 to +$963,
  MaxDD improves to −$834. `london_trader.py` now trades through "ambiguous" overnights;
  ambiguity still logged as `OVN_SKIP_INFO` for scoring. Sunset review Aug 17 2026.
  Survives 1.0pt round-trip slippage (+$1,581 net). Expectation is MODEST (~$130/mo/contract) —
  London stays paper; no go-live conversation before a positive shadow/paper month.

**⑤ Equity book-health selector** — see Jul 18 section above (shipped previous night).

Restarts done: futures_personal (London change), autotrader (book health). Both verified.

---

## Jul 18 2026 (evening) — Approved redesign wave 1 SHIPPED (audit: docs/AUDIT_2026-07-18_weekend_redesign.md)

User approved all audit decisions except MES addition (staying MNQ-only; expansion happens on
the equity side). Shipped, each with hypothesis + auto-scoring + sunset per CONSTITUTION.md:

1. **F1 fix (futures):** `_confirmed_scans` now counts CONSECUTIVE same-regime reads advanced
   once per completed 5-min bar (was: cumulative per-day tally at 60s cadence — SHORT could
   unlock from 3 scattered WEAK minutes). Live now matches sim_replay, which was always the
   validated reference. Scored by: parity harness nightly.
2. **F2 fix (futures):** `calc_htf_trend` drops the forming 5-min bar before resampling
   (same class as the Jul 17 RVOL partial-bar fix). Live now matches sim.
3. **NY Overnight Veto REMOVED (log-only)** — same architecture fix as London Jul 18.
   Hypothesis: ambiguous overnight (pos 0.20-0.40) doesn't predict a bad RTH day under the
   2026 gate stack. Validated fresh: sim_replay --no-ovn-skip YTD $5,198/92t → **$6,732/113t
   (+29.5%, WR 65.2→67.3%)**; last 30d $3,299/20t → **$3,977/25t (+21%, same MaxDD)**.
   Still logged as OVN_SKIP (same name) so gate_audit keeps scoring it. **Sunset review Aug 17.**
   parity_check SIM_FLAGS updated with --no-ovn-skip.
4. **Equity right-tail experiment:** RSI 70-80 LONG hard skip → **-15 penalty when
   vol_ratio ≥ 2.5×** (low-vol stays hard skip). Hypothesis from scan_log: blocked 70-80
   candidates with vol≥2.5 ran +0.06% drift / +0.39% fwd60 (N=118) — better than the accepted
   A+ book in every month; vol<2.5 ran -0.66% (stays blocked). Book Health still gates the
   whole book. Scored by: scan_log enrichment (these signals now graded, not skipped).
   **Sunset review Aug 18.**
5. **Equity decision-parity cop** added to parity_check.py: every live trade must trace to a
   graded A+/A scan_log row, entry-window and daily-cap invariants checked nightly. Full
   bar-level equity replay remains a separate build (sim_today.py is STALE — no L2/L3/book
   health; do NOT treat it as validation until rebuilt).
6. **sim_replay new flags:** --no-ovn-skip, --entry-start HH:MM.
7. **Canonical-name headers** added to both trader files (identifiers/DB values/launchd names
   are never renamed — GLOSSARY.md governs; the headers teach the mapping in-code).
8. **Risk-model doc drift flagged:** code runs $15K basis / $3,750 soft DLL (prop_rules.py,
   grid-searched Jul 6) — older notes saying "IBKR $5K/$1,250" are STALE. User to confirm
   which model is intended; until then code is authoritative.
9. **9:30 entry-window retest: REJECTED (third and final time).** With the full modern stack
   (Trend Jury + Volume Pulse + HTF + graduated floor + no-veto), YTD 9:30-start = 142t/$6,903/
   64.1% WR vs 10:30-start 113t/$6,732/67.3% — +29 trades buys +$171 and -3pp WR. The 10:30 IB
   wait is now VALIDATED under current gates, not folklore. Do not re-test without a new mechanism.

**Universe expansion (equity, user-directed "pro-grade universe"):** live evidence — when the
edge is ON (May), the $5-20 band was the TOP earner (+$1,023/38t/58% WR vs $100+ +$110/53t);
since June the cheap band fades hardest (drift -2.12%) — small caps are the highest-beta
expression of the edge, now guarded by the Book Health Selector. Direction approved: expand
via find_candidates.py DNA screen toward ~300 names. Criteria (pro-grade): price ≥ $5,
market cap ≥ $300M, ≥1M shares/day AND ≥$10M dollar volume, ATR% ≥ 3%, shortable, sector caps.
Current screener already enforces ≥$5. Run scheduled as next session's job (needs market-hours
data quality checks). NOT yet executed.

---

## Jul 18 2026 (night) — Redesign wave 2: equity replay harness, ablation matrix, risk model, universe screen

**① equity_replay.py BUILT (replaces stale sim_today.py as equity validator).** Imports
auto_trader and calls the LIVE decision chain (get_regime → get_intraday_signals →
grade_setup → L2 → book_is_on → get_position_capital) against stored bars_5m + a yfinance
daily cache, FakeDatetime-frozen per 5-min bar. Exit engine mirrors the live stack incl.
L3 T+5 probation. **First parity run: 88% decision parity vs live scan_log (150/170
symbol+grade pairs, Jul 15)** — divergences map to the documented v1 stubs (earnings=999,
no catalyst/sympathy flags, empty sector_strength/key_levels). Modes: `--parity DATE`,
`--start/--end`, `--no-book-health`, `--detail`. Jul 6-17 replay with Book Health ON:
0 trades (selector correctly flat) — reproduced through live code.

**② Futures gate ablation matrix (historical re-scoring — no 60-day wait needed).** Full
YTD sim on Databento-collected bars, new production base ($6,732/113t/67.3%):
Trend Jury OFF → $6,180 (-$552, MaxDD -$2,887): **Jury validated, keep.**
2pm cutoff OFF → $6,593 (+22t, -$139): **cutoff validated, keep.**
short-confirm 1 vs 3 → $6,825 (+$93, worse MaxDD): flat — keep 3.
DLL $1,250 vs $3,750 → **IDENTICAL** (never bound): tightening free.

**③ Risk model DECIDED (user-confirmed): $15K total = $10K equity + $5K futures.**
prop_rules.py: IBKR_FLOOR 15000→5000, IBKR_DLL_SOFT 3750→**1250** (25% of futures
allocation). parity SIM_FLAGS += --dll 1250. futures_personal restarted. Note:
futures/ibkr_state.json balance history stays on the old $15K baseline (bookkeeping
only; the DLL halt reads IBKR_DLL_SOFT fresh). Real money still gated on results —
paper until a positive month on the new config.

**④a Results landed same night:** DNA deep screen: **113/300 pass all 5 rules**
(85 INSTITUTIONAL / 16 MOMENTUM / 12 HIGH_VOL; top EV: CAR $+123/93%, TGTX, CVNA, VSAT,
CENX — full table `find_candidates_results.csv`). Equity replay no-book-health
counterfactual (Jul 6-17, live code incl. new RSI band): **76 trades, 23.7% WR, +$32** vs
Book Health ON: 0 trades $0 — the selector verdict confirmed through live-code replay
(76 round-trips of churn for ~zero P&L; flat is correct until the tape turns).
**Universe refresh SHIPPED same night (user approved full add + prune):** all 166
incumbents re-screened under identical criteria (the earlier "76 pass" only covered
S&P1500 members) → keep 128 (ETF sector vehicles exempt), **prune 38** (27 low-ATR mega
caps incl AAPL/MSFT/JPM/V/MA, 7 sub-$5, 2 thin/delisted, 2 no-data). Added all **113 DNA
passers** with clusters from find_candidates_results.csv (12 HIGH_VOL / 85 INSTITUTIONAL /
16 MOMENTUM) + yfinance-derived sectors (30 map to OTHER = neutral scoring; refine later).
Final universe **241**. Bars bootstrapped (yfinance 60d) for the 113 new names;
collect_bars follows FULL_UNIVERSE automatically. **Databento 2-yr 1-min backfill COMPLETED
same night (user-approved): 111/113 new names now have full history to Jan 2024 — equal
replay footing with incumbents (P + PENG are newer listings, shallow only; nothing missing).** Parity re-run post-refresh: divergences
= exactly the pruned names in Friday's history (expected). SYMPATHY_MAP triggers (AAPL etc.)
unchanged — triggers don't need to be tradeable. Databento 2yr backfill (~$33) NOT run —
ask user if deeper history wanted for the new names.

**④ Universe screen EXECUTED (S&P 1500 base, pro-grade criteria).** 867/1,499 pass
price≥$5 + $vol≥$10M + ATR≥3%. Only **76 of the current 166 universe names pass** —
refresh must prune, not just add. Top-300 new candidates ranked (ATR% × liquidity) in
`universe_screen_2026-07-18.csv`; find_candidates.py gained `--csv/--limit` and the full
5-rule DNA deep screen over the 300 was launched (results → next session; early hits:
MXL 97% bt_wr 6/6yr, P 98% 6/6yr). Criteria provenance: $5 floor + $300M cap + $10M ADV
are industry conventions; ATR≥3% is OUR system requirement (MIN_TODAY_GAIN needs movers).

---

## Jul 18 2026 (late night) — Flip-cooldown REJECTED (inverted finding), Telegram + dashboard upgrades

**Flip-cooldown experiment (oscillating-regime hypothesis): REJECTED — the data inverts it.**
sim_replay `--flip-cooldown N` + per-trade `flip_age` diagnostics, full YTD on production
config: entries on the FIRST bar of a fresh regime are the system's BEST trades (49t, 73% WR,
avg +$88, $4,326 of the $6,732 total). Cooldown=2 cuts P&L to $3,654; cooldown=3 destroys it
(-$325). **The regime flip IS the entry signal** — when the flip and all four confirms line up
in the same bar, that's a breakout caught early; waiting = entering late. The one weak pocket
is MID-regime entries (4-5 bars in: 21t, 43% WR, -$525) — chasing, not flipping. N too small
to gate on; logged as a watch-item. Conclusion for the reversal problem: entry-side is NOT
where flips hurt us (F1 fixed the false-unlock case); reversal pain is exit-side (Weather-
Aware Locks) + environment-level (Mirror Book, currently +505pts/75 shadow trades, +369 last
14d — on track for its 30-day review ~Aug 17).

**Telegram audit — verdict: structure good, three gaps fixed:** (1) parity/Trade Cop
divergences now SEND TELEGRAM (were file-only — nobody was alerted); (2) futures EOD message
gained a signal-funnel line (entries + top gate blocks from gate_blocks); equity EOD gained
Books ON/OFF + A+ signal counts; (3) canonical names in cards ("Weather:", "Day Shape:").
Inbound command strings (FUT BIAS etc.) untouched. Recommendation logged, not built: message
tiering (action-required vs journal) if volume becomes noise.

**Dashboard upgrade — SYSTEM HEALTH panel added** (`get_system_health()` in dashboard/app.py
+ panel in index.html/app.js): Book Health per direction with drift, today's signal funnel
(equity A+ counts, futures gate blocks), Trade Cop last verdict, Mirror Book running total,
universe count. Verified live: books OFF (-0.51/-0.62 drift), parity OK, Mirror +505pts.

**System score after the weekend: 65 → 74 / 100.** Remaining points to 80+ are earned, not
built: a green live week on the new config, closing the three equity-replay stubs (earnings
calendar, catalyst/sympathy flags, sector-strength scoring), Mirror Book 30-day graduation
(~Aug 17). Read docs/AUDIT_2026-07-18_weekend_redesign.md for the scorecard rubric.

---

## Jul 18 2026 (post-close) — Bug sweep of the redesign + dashboard v2

**FIXED (clear-bug rule, commit c50decf):** weekend guard. `is_entry_allowed()`
(futures) and London's `run_scan()` were time-of-day only — Saturday 9:30-16:00 read as
MIDDAY/AFTERNOON and the full scan pipeline ran against frozen Friday bars, writing 298
junk GRADE/REGIME rows into gate_blocks (deleted) at a stale price, with theoretical
resting-order risk into a closed market. First weekend the bridge stayed connected is
what exposed it. Both traders now check `weekday() >= 5`; futures_personal restarted.

**FLAGGED, NOT FIXED — decisions pending user approval:**
1. **Graduated-RVOL sizing divergence:** sim_replay caps RVOL-compensated entries at
   1 contract (`min(contracts, 1)`); live has no cap and `calc_contracts_dynamic`'s
   ib_range≥150 tier gives those entries 2 contracts on wide-IB days. The Jul 8 backtest
   that shipped the 0.70 floor assumed 1 contract. Parity harness can't see it (matches
   time/side, not size). Fix = add the cap live + contract count to parity diff.
2. **Premarket scanner bypasses Book Health:** `_scan_premarket_catalyst` has no
   `book_is_on('LONG')` check while both main books + catalyst override are gated —
   premarket longs can fire 9:20-9:29 with the LONG book OFF.
3. **F1 is a half-fix:** `get_regime` still reads the forming 5-min bar (price/RSI/
   5-bar trend), so an intra-bar flicker flips the label and resets the consecutive-bar
   streak to 1 — live confirmation can lag sim. Same partial-bar class as the RVOL
   (Jul 17) and HTF (Jul 18) fixes; regime read is the remaining instance.
Minor notes: exits fall back to CHOPPY params when `_day_regime` is None (pre-10:30
entries); `date(ts)` in gate-block SQL converts to UTC (wrong date for post-8pm rows);
dashboard imports auto_trader inside a request (slow first hit).

**Why the falling MNQ week (Jul 14-17, -1,082pts) didn't pay:** 62% of the decline was
overnight gaps (-674pts, incl. Jul 17's -592) — system is structurally flat overnight.
Inside the 10:30-14:00 entry window the four days netted +183/-22/-249/+75. The one real
in-window down-day (Jul 16, -249) was fully vetoed by OVN_SKIP — already removed Jul 18.
NY futures actually made ~+$550 on the week (2 ORB_SHORTs Jul 15 +$475). Structural gap
worth a future instrumented experiment: no multi-day trend memory (each day starts
stateless at the 10:30 IB); overnight-bias classifier is the natural input, now log-only.

**Dashboard v2 (commit 2c7aa84):** SYSTEM HEALTH renders glossary names + hover
tooltips, funnel shows "N entered" first, Trade Cop verdict decoded to a sentence;
7-day equity chart → 15-session stacked daily P&L (equity/options/futures incl London)
with a bright zero line; new 15-DAY SCORECARD (per-book trades/WR/P&L/avg/best/worst).
futures_trades now split by account_mode in dashboard queries — Futures NY = IBKR only,
TC eval its own row (was silently blended).

---

## Jul 18 2026 (night) — Options system full audit (counterfactual gate scoring)

Same treatment as equity/futures redesign. Scored all 2,024 opt_calc_log rows (deduped
374 bull + 28 bear per symbol-day) against actual forward prices vs each row's own
breakeven. Full trail: [[options-audit-jul18]] memory + scratchpad opt_gate_audit2.py.

**Scoreboard:** lifetime -$4,169/14 closed (mostly pre-rebuild SYSTEM_RESET closes).
Post-Jun-23 rebuild: 2 trades in 4 weeks (CHPT bear put +$360 = only AUTO_TARGET winner
ever; USAR $22 LEAP open Jul 1, stock $15.65, no LEAP stop rule — watchman alert-only).
Zero ENTER verdicts since Jul 2; scalp engine has NEVER fired since built May 25.

**Core findings:**
- **Verdict system is ANTI-predictive:** ENTER rows dirRight 16.7% / fwd10 -9.2% vs SKIP
  32.7% / -5.0%. Every gate neutral or backwards (tech/vol/conviction/momentum);
  liquidity gate = cost control only. MC model uncalibrated (WR-60+ bucket ≈ WR<40).
- **News HIGH BULL conviction = fade signal** (65-86% short-right by month) — mirrors the
  equity Mirror Book finding. Conviction table runs 47 BULL-HIGH vs 5 BEAR-HIGH →
  structural bull bias in a bear tape.
- **Bear puts are the only edge** (93% hitBE, 68% dirRight, n=28) and are structurally
  starved: bull candidates always routed first, bear requires regime==WEAK exactly,
  CHOPPY/CAUTIOUS = ALL strategies blocked (dead zone), bull blocked 16/22 days since
  Jun 23 → WEAK-regime ERROR+cooldown churn burns the scan cycle on untradeable bulls.
- **What-if verdict on the funnel wall:** 27 skipped suggestions netted -$600 → the
  silence was correct, but because the tape fell, not because the gates knew.

**FIXED (clear-bug rule, database.py `fill_whatif_prices`):** learning loop was
dead-on-arrival since built — WHERE filtered `user_decision` (always NULL) instead of
`status`, AND `fast_info.get('last_price')` returns None (key is `lastPrice`), AND
backfill stamped today's price as the 7d/14d price. Now uses historical closes at true
+7d/+14d. Verified: 27 rows backfilled. Nightly learner needs no restart (one-shot).

**Doc corrections:** options tables live in **trades.db** (trading.db is empty — older
docs wrong); SCALP_UNIVERSE is ~170 symbols (not 15); outcome logging fires only on
AUTO_* exits (manual/reset closes never logged).

**APPROVED and SHIPPED Jul 19 2026 (build spec: `docs/OPTIONS_REDESIGN_2026-07-18_PLAN.md`).
What shipped:**
- **Options Book Gate** (`_book_health_on()` in options_trader.py): exact mirror of equity
  Book Health Selector SQL (verified parity: LONG -0.51 / SHORT -0.62 at ship). Equity A+
  scan triggers (`_check_equity_scan_triggers`) are now the ONLY trade source, per-direction
  book-gated. Scalp engine gated behind LONG book. Both books OFF at ship ⇒ **options is
  intentionally FLAT until the tape turns — do not "fix" this.**
- **News queue → Ghost Ledger only:** suggestions still run the calculator (so strikes land
  for nightly what-if scoring) but always mark NO_TRADE. `_proactive_recycle` +
  `_handle_queued_suggestion` no longer called (kept as dead code/research hooks).
- **Verdict policy in all 4 calculators:** liquidity gate decides ENTER/SKIP; regime walls
  removed (CHOPPY/CAUTIOUS dead zone gone); 5-gate score + MC EV/WR demoted to
  instrumentation (`legacy_verdict` kept in result; DB gate columns unchanged for scoring).
  VIX>25 block and IVR 20-50 debit band KEPT. LEAP calculator untouched (manual OPT cmds only).
- **Telegram revamp:** news_engine per-HIGH-signal alerts → log-only (top noise source, no
  longer precede trades); watchman auto-close-failure alerts deduped to once/day (was
  re-alerting every 15 min — this was the USAR spam); daily options-book-status JOURNAL
  message (weekdays, once/day).
- **Dashboard:** options row in SYSTEM HEALTH (open positions, funnel today, closed-14d,
  Ghost Ledger 14d) + **fixed pre-existing P&L bug**: three queries used
  `exit_value - net_debit` (per-share vs dollars — CHPT showed +$585 instead of +$360);
  now `exit_value - premium_paid`.
- **Bug-sweep findings during build:** watchman LEAP stop machinery already existed (USAR
  stop_value=$582 set — the failure was auto-close retry spam, fixed above); outcome logging
  already wired on manual + auto closes (no change needed).
- Services restarted + verified: options_trader, watchman, news_engine, dashboard.
- **PENDING Monday Jul 21: close USAR LEAP manually** (`OPT CLOSE USAR`, limit order) —
  approved by user; market was closed at ship time. **1-week evaluation, judge ~Jul 27:**
  trades vs book direction, Ghost Ledger P&L of skips, Telegram volume.

Original proposal (all points above implement it): (A) direction
from tape/book-health, not news — port equity Book Health Selector; SHORT book ON → bear
puts from A+ SHORT equity triggers (the one validated path), LONG book ON → debit calls;
both OFF → flat. (B) fix router ordering + kill CHOPPY/CAUTIOUS dead zone. (C) demote
tech/vol/conviction/momentum gates + MC EV to instrumentation-only (constitution:
instrument-first); deciders = liquidity + IV routing only. (D) define LEAP exit rule +
decide USAR fate. (E) scalp engine tied to LONG book health or shelved.

---

## Jul 19 2026 (night) — Field Report shipped (Market Context Engine, Phase 0 LOG-ONLY)

User asked how to give the system big-picture awareness (multi-day trend, macro events,
world themes, S/R levels) and approved building it same night, instrument-first.

**Shipped: `market_context.py` ("Field Report" — GLOSSARY 5b), launchd
`com.sushil.trading.market_context` at 9:15am ET weekdays** (⚠️ launchd
StartCalendarInterval is LOCAL time — Mac runs America/Toronto; note that
collect_bars' "21:30 UTC" plist comment is wrong, it actually fires 9:30pm ET —
harmless, still after close, not changed).

- **Layer 1 (mechanical):** SPY/QQQ daily trend state via yfinance (bars_5m SPY
  feed dead since Jun 1 — daily download is the reliable path); MNQ trend + S/R
  levels from our own `futures_bars_5m` with proper CME trading-day bucketing
  (6pm ET rolls to next day; pre-market runs split the current overnight session
  out as `overnight` levels + gap%, so `prior_day_*` always means the last
  COMPLETED session); FOMC/CPI/NFP dates (copy of dashboard MACRO_EVENTS —
  keep in sync) + catalyst_calendar next-3-days.
- **Layer 2 (LLM):** one `claude-opus-4-8` call/day (~$0.03; 1.7K in/1K out
  measured), structured JSON via `output_config.format` json_schema: stance
  RISK_ON/NEUTRAL/RISK_OFF + confidence + event_risk + themes + sectors +
  one-line thesis + watch items. API failure → stance UNAVAILABLE, mechanical
  layer still logged (day never lost). Uses ANTHROPIC_KEY from .env.
- **Storage:** `market_brief` table in trades.db (one row/day, INSERT OR
  REPLACE, stance immutable-by-convention once the open passes — that's what
  makes it scoreable). Telegram: one pre-market JOURNAL message. Dashboard:
  Field Report row in SYSTEM HEALTH (stance chip + themes, thesis on hover).
- **LOG-ONLY (Constitution: instrument-first).** No trader reads market_brief.
  Scoring: `market_context.py --score` (stance vs SPY open→close alignment) —
  meaningless before ~20 trading days. Graduation path agreed with user:
  event-day stand-down (mechanical, likely first) → sizing tilt → gate, each
  component individually, only on measured separation, with hypothesis + sunset.
- First live brief runs Monday Jul 20 9:15am. Verified end-to-end tonight
  (weekend --force run: RISK_OFF/MEDIUM, correct MNQ levels, telegram
  delivered, dashboard rendering).

---

## Jul 19 2026 (late night) — Trade Cop v2: London + options legs (all 4 books covered)

Pre-Monday readiness ask from user. Both IB gateways were down (paper + TC) — restarted,
both bridges reconnected (DU9952463 / DUQ640500). Then closed the two parity gaps:

- **London leg added to parity_check.py:** replays the session through london_v2_sim with
  `LONDON_CFG` (champion: no confirms, no skip-day, BE=0.10 — update when live changes).
  Entry-side diff only (exits diverge by design: 15s live monitor vs 1m sim). Runs **one
  day lagged** — futures collect_bars `--update` fetches Databento 1m only through
  YESTERDAY (availability lag), so 1m bars for day D land on D+1 evening. First live
  check: Jul 16 sim=2 live=2 matched=2 → OK (real parity confirmed, not just plumbing).
- **Options leg added:** decision-invariant cop from `OPTIONS_COP_SINCE='2026-07-19'` —
  every options trade must have (1) an ENTER verdict in opt_calc_log that day, (2) an A+
  scan_log signal same symbol/direction, (3) that direction's equity book ON (trailing-10d
  drift recomputed as-of the day, mirroring `_book_health_on`). No bar-level options
  replay is possible (option-chain quotes aren't stored) — this is the parity mechanism.
  Mechanics verified: retroactively flags the Jul 1 USAR LEAP with all 3 violations
  (exactly the entry pattern the redesign banned); correctly ignores pre-enforcement dates.
- london_v2_sim now records entry `time` per trade (behavior-neutral; parity needs it).
- **Parity coverage now: NY futures (full diff) + London (entry diff, lag-1) + equity
  (invariants; equity_replay for depth) + options (invariants). Telegram fires on any leg.**

---

## Jul 20 2026 — USAR auto-close storm root-caused + fixed (watchman.py, options_trader.py)

USAR LEAP (entered Jul 1, $970 premium, stop $582) hit its stop this morning. It never
closed all day — instead generated **~150 order attempts and 100s of Telegram messages**
by market close, all logged as `Error 202: Order Canceled` with a blank reason, order
status never advancing past `PendingSubmit`. User pushed for a real root cause, not a
manual close (explicitly declined a manual workaround) — traced to three compounding bugs,
all now fixed:

1. **Alert-dedup gap:** the Jul 18 "once per day" fix covered only the *outer* T3b_FAIL/
   T4_FAIL alert in `_check_trade`. `_auto_close_position`'s own internal 3-attempt retry
   loop (`MAX_CLOSE_TRIES=3`, ~90s per call) sent its own "retry X/3" and "FAILED after N
   attempts" Telegram messages on **every** watchman scan cycle (every 5 min, all day) —
   never covered by the dedup. Removed both internal sends; the outer alert already tells
   the user once and points them at `OPT CLOSE {sym}`.
2. **Close price wasn't marketable on wide spreads:** LEAP/OPT_SCALP closes priced at a
   flat `mid - $0.05`, fine for normal spreads but USAR's LEAP had a $1.30-wide spread
   (bid $4.65 / ask $5.95) — the nickel-off-mid price ($5.25) never crossed the bid, so
   the order could never fill. New `_sell_limit_price(bid, ask)` scales the discount to
   half the spread width — lands exactly on the bid for wide spreads, reproduces the old
   nickel-off-mid price unchanged for tight ones. Ported into `options_trader.py`'s manual
   `OPT CLOSE` path too, and upgraded its credit/debit spread-combo pricing (previously
   also a flat-nickel approximation) to the fully-marketable natural-credit/natural-debit
   crossing that `watchman.py` already used correctly for those legs.
3. **No cap on retry cycles:** nothing stopped watchman from re-attempting a stuck close
   every single 5-min scan indefinitely — ~150 submit/cancel cycles on one contract in one
   session is a plausible trigger for an IBKR order-churn throttle, which would explain why
   every attempt today (including manually-placed test orders at the live bid) sat
   unacknowledged regardless of price. Added `MAX_DAILY_CLOSE_ATTEMPTS=5` (per trade,
   reset at EOD like the other daily trackers) so a stuck position still gets real chances
   without hammering the same contract 40+ times a day.

**Also found (informational, not a bug):** today's zero equity/options activity was
correctly-behaving, not broken — Book Health Selector (both books OFF, computed once at
first scan from trailing 10-day data, unrelated to today's chop) plus CHOPPY/WEAK regime
routing meant the full-universe scan never ran, so `scan_log` legitimately has zero rows
for the day. Options had nothing to trigger on for the same upstream reason. Confirmed via
full-day `auto_trader.log` read (continuous 5-6 min cycles, no errors, regime bounced
CHOPPY/WEAK/NORMAL/CAUTIOUS all day) — not a scan failure, just two gates agreeing to
stand down before per-symbol evaluation began.

Commit `14ef9a3`. watchman + options_trader restarted and verified healthy same session.
USAR itself is still OPEN as of this fix — market was closed (after-hours, no bid/ask)
by the time the fix landed; first real test is tomorrow's open. Given the daily cap and
the ~150-attempt history today, watch the first attempt closely rather than assuming it's
fully solved — the IBKR-throttle theory is the best available explanation but unconfirmed.

---

## Jul 21 2026 — USAR discovered actually SHORT 15 contracts (Jul 20 fix exposed a worse bug)

Deep-read follow-up the next evening found the Jul 20 fix did NOT close USAR — it made
things worse in a way the DB never showed. **Live IBKR portfolio: SHORT 15 USAR
20280121 $22 calls** (qty=-15, avgCost=$532.75, marketValue=-$8,900.16, unrealizedPnL=
-$908.94), not the DB's `OPEN / LONG 1 contract`. Paper money — no real dollar loss —
but the DB was completely blind to the real position.

**Root cause:** `_auto_close_position`'s retry loop places an order, sleeps 30s, checks
status, and cancels if not filled — but never reads the *result* of that cancel call. If
a fill lands at IBKR just after the 30s check (common, since it's a fixed timer not an
event), the cancel arrives too late, IBKR rejects it (`Error 10148: cannot be cancelled,
state: Filled`), and the code silently treats the attempt as failed anyway — so it sells
the same (already-sold) contract again next cycle. Confirmed **10 silent real fills**
since Jul 20 via the 10148-rejection log pattern (order IDs 472371, 475414, 492576,
492586, 492602, 493020, 493112, 493174, 493339, 493356); the position's actual 16-contract
swing implies more that didn't hit this exact log signature. The portfolio-fallback safety
check (`any_leg_open`, checks `abs(qty) > 0`) is also blind to this — it can't tell "long"
from "short," so it never flags the position as wrong either.

**This was exposed, not caused, by the Jul 20 fix.** Making the close price marketable
(`_sell_limit_price`, scales to the bid on wide spreads) was correct and necessary — but
it turned a latent race condition that almost never fired (orders essentially never filled
before) into one that fires constantly (orders now fill routinely). The Jul 20 daily-cap
fix (`MAX_DAILY_CLOSE_ATTEMPTS=5`) DID work as designed — today's activity was ~6
order-bursts vs. ~150 before — it just capped a bug nobody knew was there yet.

**Actions taken same session (user chose "reconcile DB, no new orders tonight" over
manually flattening or leaving it running):**
- `options_trades` id=19: `contracts` corrected 1→15 (true absolute size), `lesson` field
  carries the full incident note and an explicit "do not trust contracts/premium_paid/
  status for P&L" warning. `status` left OPEN (still technically true) and `premium_paid`
  left at the original $970 (real historical entry cost) — deliberately did **not**
  fabricate a P&L number from incomplete fill data.
- `watchman.py`: added `_AUTO_CLOSE_DISABLED_TRADE_IDS = {19}`, checked at the top of
  `_check_trade()` before ANY per-trade logic runs (not just the close attempt) — every
  calc in that function assumes a normal long position and can't be trusted once the sign
  flipped. Restarted and verified (compiles, logs "monitoring disabled" for #19 on the
  next scan cycle).
- The underlying fill-detection race condition is **NOT fixed** — only contained. Do not
  re-enable auto-close on trade #19 (or trust the retry loop on any other position) until
  the cancel-result is actually checked and reconciled against a live IBKR fill before the
  next attempt fires.

**Follow-up same evening — race condition fixed + buyback placed (user directed):**
- `watchman.py` `_auto_close_position`: after the cancel-and-retry call, now re-checks
  order status (+ a `_leg_qty` portfolio comparison against a baseline captured before the
  order went out) instead of trusting the bridge's fire-and-forget `/cancel` response. If
  IBKR rejects the cancel because the order already filled (`Error 10148`), that's now
  correctly recorded as a real close instead of a failed attempt. Also hardened the
  existing portfolio-fallback check the same way — "any nonzero qty" couldn't tell long
  from short; now compares against the pre-order baseline. Compiles clean, watchman
  restarted. **Still needs a live test** — the exact race (fill landing between status
  check and cancel call) hasn't recurred yet to confirm the fix catches it.
- Placed a BUY 15 USAR 20280121 $22C @ $6.50 limit (orderId 505355) to flatten the short
  back to 0. No live quotes available after-hours (delayed last=$5.70); priced with a
  buffer above both that and yesterday's real ask ($5.95) since the order won't actually
  hit the exchange until tomorrow's 9:30am ET open regardless. Placed as a single one-shot
  order, deliberately NOT routed through the automated retry/cancel loop — left resting,
  not auto-cancelled. **Verify fill after tomorrow's open** (`/portfolio/options`, not
  `/order/{id}/status` — a bridge/gateway restart between now and then would drop local
  order tracking but not the broker-side order or position).

**Also found same session, real but separate:**
- **Equity book-health selector structurally cannot self-correct.** Read `_scan_and_enter`
  line-by-line: `book_is_on('LONG')` gates the function *before* the 241-symbol scan loop,
  so while a book is OFF, no new `scan_log` rows are ever written — including on
  STRONG-regime days. The trailing-10-day health calculation only reads *already-enriched*
  rows, so once a book goes OFF it is frozen on whatever window existed the moment it went
  off (currently LONG on Jul 16-and-earlier, SHORT on Jul 17-and-earlier) — confirmed by
  identical -0.51%/-0.62% readings two days running with zero new data. It will not turn
  back ON because the tape improved; it can only turn back ON via manual intervention or by
  eventually running out of enriched rows and hitting the cold-start default (recovery by
  data starvation, not by evidence). Options cascades from the same cause (its trigger
  source is equity's A+ signals, which don't exist while equity is silent). **Not fixed —
  flagging for a deliberate design decision** (e.g. a small always-on canary slice of the
  universe purely to keep the health measurement current).
- **Futures Trade Cop caught a real unresolved parity divergence on Jul 20:** live took two
  `VWAP_LONG` entries (10:37, 10:38) that `sim_replay` never reproduces. Yesterday's futures
  profit included those two trades — profitable this time, but currently untested against
  the validated logic. Not root-caused yet.

---

## Jul 21 2026 (evening) — equity book-health confirmed frozen FOREVER (traced all 6 write sites); options auto-close blocks made durable

**Equity, precise answer to "will the book ever turn back on":** traced every one of the
exactly 6 `log_scan_candidate()` call sites in the entire codebase (grep-verified, not
inferred) — all 6 sit inside `_scan_and_enter` / `_scan_and_enter_bear`, both gated by
`book_is_on()` before reaching them. `_scan_premarket_catalyst` and `_scan_catalyst_override`
call `log_scan_candidate()` zero times each, and no shadow/canary equity mechanism exists
(unlike futures' Mirror Book). Correction to the earlier note above: the "cold-start
default" is **not** a real escape hatch either — since `compute_book_health`'s query is
`scan_date < today ORDER BY scan_date DESC LIMIT 10` against a pool that can never grow,
the "10 most recent enriched days" is a **permanently fixed set** (not shrinking, not
growing), currently sitting at 10 days / 374-514 rows — nowhere near the 4-day/30-row
cold-start floor, and it never will be. **Under current code the book cannot turn back on
by itself, period, with zero exceptions** — not "rarely," not "eventually," never. It isn't
evaluating "is today bad" — it evaluated Jul 16/17 once and has silently repeated that exact
verdict every day since, with zero awareness of anything that happened after. Also confirmed:
book_is_on isn't the *only* pre-loop gate (daily loss brake, afternoon gate, recycled-slot
gate also sit ahead of it) — but those reset every morning, so only book_is_on causes the
multi-day freeze. Not fixed — user wants to debate the design before acting (data-freshness
vs. contamination-risk tradeoff of scanning while a book is nominally off).

**Options — auto-close blocks made durable (commit `0f596b6`), per explicit "no time
pressure, fix it properly" direction.** The Jul 21 fixes above stored trade #19's block (and
any future partial-fill flag) in an in-memory Python set — exactly the kind of gap that
caused this whole incident class: a watchman restart would have silently un-blocked a
known-broken position. New `options_auto_close_blocks` DB table (`trade_id`, `reason`,
`flagged_at`) + `block_auto_close`/`unblock_auto_close`/`is_auto_close_blocked`/
`get_auto_close_blocks` in `database.py`. `watchman.py` now calls `init_db()` at startup
(matches `options_trader.py`'s existing pattern) and checks `is_auto_close_blocked()` from
the DB instead of the two removed constructs (`_AUTO_CLOSE_DISABLED_TRADE_IDS`,
`_PARTIAL_FILL_FLAGGED` — both deleted, single source of truth now). Migrated trade #19's
block into the new table. **Verified live**: restarted watchman, confirmed via
`logs/watchman.log` it read the block from the DB (not a reset in-memory set) and correctly
skipped trade #19 on the next EOD run. Re-read the full modified `_auto_close_position`
end to end once more per request — no further issues found on this pass.

---

## Aug 10 2026 — Options resumed live (book reset window fully fresh) + dashboard multi-leg P&L bug fixed

Both equity books flipped back ON (LONG +0.45%/10d/927 rows, SHORT +0.71%/5d/249 rows — the
Jul 21 `BOOK_HEALTH_RESET_DATE=2026-07-22` window is now fully fresh), which unlocked options'
trigger source. First real options entries since the reset: PLTR OPT_SCALP (1x $177.50C 8/21,
$633 debit), JOBY bull spread (5x $10/$11C 9/18, $125 debit), XLE bull spread (5x $61/$62.50C
9/18, $265 debit) — all cleared the Aug 3 Spread Toll Gate comfortably (liq_cost_pct 5-10% vs
12% max). FTNT also got an ENTER verdict but never filled after 4 order attempts — gave up
cleanly, no retry storm, no phantom position (unlike the Jul 20 USAR incident).

**Bug found + fixed same session (commit pending):** `get_options_positions()` in
`dashboard/app.py` built `live_map` keyed by symbol from the raw bridge position list — for a
multi-leg spread (2 rows per symbol, one per leg), the dict overwrite meant only the LAST leg's
`unrealizedPnL`/`marketValue` survived, silently dropping the other leg. XLE showed +$172 on the
dashboard when the true net-of-both-legs P&L was near flat; JOBY showed +$25 when it was
actually -$36. Different bug from the Aug 3 `exit_value - net_debit` fix (that was closed-trade
logging; this is live open-position display) but same root cause shape: code assumed one leg per
symbol. Fixed by summing `marketValue`/`unrealizedPnL` across all legs sharing a symbol instead
of overwriting. Single-leg positions (LEAP, scalp) unaffected. Verified via direct function call
post-fix (XLE -$3.28, JOBY -$33.75 — both now sane). Dashboard restarted, HTTP 302 (healthy).

---

## Jul 21 2026 (night) — Book Health Selector SHIPPED FIX: gate moved to entry-only + reset date

After several rounds of debate (documented in the session, not re-derived here), user approved
and directed implementation of both agreed pieces. Deep-read both `_scan_and_enter` and
`_scan_and_enter_bear` end to end before touching anything, to identify the exact boundary
between "safe to always run" and "must stay gated."

**Finding that made the fix low-risk:** both functions already had a clean, pre-existing
seam — a full candidate-grading-and-logging block (every symbol graded, every grade logged to
`scan_log` including `entered=False` for non-entries) runs completely separately from, and
*before*, the actual entry-placement loop that calls `place_trade()`. No new logic had to be
written; the fix only had to change *where* one `if` check sits, not what anything computes.

**Change 1 — gate moved (`auto_trader.py`):** `book_is_on('LONG'/'SHORT')` no longer sits at
the top of `_scan_and_enter`/`_scan_and_enter_bear` (which made it exit before grading ever
ran). Now checked immediately before the entry loop only — grading and `scan_log` logging run
on every single scan regardless of book status; the book only ever blocks the step that commits
real capital (`place_trade`). Implementation is minimal-diff by design: `for pick in candidates:`
became `for pick in (candidates if book_is_on('LONG') else []):` — no re-indentation of the
100+ line entry-loop body, reducing risk of a transcription bug in a large edit.

**Change 2 — reset date (`auto_trader.py` + `options/options_trader.py`):** added
`BOOK_HEALTH_RESET_DATE = '2026-07-22'` and an `AND scan_date>=?` filter to both
`compute_book_health()`'s query in `auto_trader.py` *and* the separately-duplicated
`_book_health_on()` in `options_trader.py` (comment there literally says "mirrors
auto_trader.py exactly," but it had drifted — was still reading the un-reset window). Both
were frozen by the identical root cause since they read the same `scan_log` table; found this
by tracing options' trigger requirements and confirming the user's "options starts trading
tomorrow too" expectation actually held up under the code, not just assumed.

**Why reset, not just let the window roll (debated at length, decided against blending):** the
existing 10-day window was confirmed 57% dominated by a single Jul 1 burst day (214 of 374
LONG signals) — blending it into a fresh reading would import a known distortion rather than
preserve useful information. Reset discards it cleanly instead.

**What actually happens starting Jul 22:** cold-start default (`< 4 days / < 30 rows → ON`,
pre-existing behavior, same posture as any new deployment) governs for roughly the first 4
trading days while fresh data accumulates from scratch. By ~day 10 (~2 calendar weeks), the
window is fully fresh with zero pre-reset influence. The book can go OFF again during this
period if fresh data genuinely warrants it — the fix restores the ability to find out either
way, it does not predetermine the outcome.

**Side-effect audit (done as part of the "dig properly" request, not skipped):** read every
line between the top of each function and the entry loop. Only one non-trivial side effect
found — `catalyst_priority.append(symbol)` on a strong intraday mover — judged safe to run
unconditionally (pure watchlist-priority bookkeeping, no capital or order implications, and
was already running whenever the book happened to be on before this fix). `_scan_catalyst_override`
(a separate, smaller function with no grading/logging phase to protect) intentionally left
untouched — its `book_is_on('LONG')` gate still blocks its entire body, which is correct since
there's nothing to separate there.

**Verified:** both files `py_compile` clean, `autotrader` and `options_trader` restarted,
bridge healthy, no new errors post-restart. **Not live-tested yet** — market was closed at
ship time; the real test is tomorrow's open. Watch the first few scans of Jul 22 for the new
book-health log line and confirm `scan_log` starts accumulating rows again regardless of ON/OFF
status.

---

## Jul 25 2026 — Give-back fix SHIPPED (Reversal Exit + Partial Scale-Out) + full TC alignment

**Diagnosis (user-driven, confirmed by 1-min bar forensics):** since Jul 8, trades peaking
≥$150 kept only 40% of peak ($2,312 of $6,800); total give-back $8,470 vs net +$466. Root
cause: Weather-Aware Profit Locks activation cliffs (TRENDING wide trail needs +300pts —
Jul 15 trades peaked +296 and kept 59pts; Jul 24 peaked +96 on a TRENDING day = ZERO
protection → rode to the -$1,279 DLL breaker, which then correctly blocked fully-qualified
13:11 A+ SHORTs worth ~+$400-800 in the afternoon trend).

**Timing study (70 trades, peak ≥75pts, 1-min bars):** entry→real peak median 176min;
peak→30%-retrace median 7min (21/70 never retrace 30% — the runners). Both user intuitions
partially right: reversals ARE violent, but real peaks come ~3hrs in, so any exit fast
enough to catch the 7-min reversal amputates the 3-hr runners that are ALL the profit.
**Exit-lab (1-min replay, same entries, exits only):** every faster-exit variant LOSES at
trade level — "exit the minute it reverses" (20% retrace) turns +$4,340 into -$264 while
capture% RISES 55→77 (capture% and P&L anti-correlate; give-back is the premium for the
right tail). Dynamic IB-scaled "enough" thresholds: worst tested (-$1,192). What wins is
**exit-plus-re-entry**: cut a CONFIRMED reversal, let the entry engine re-enter.

**SHIPPED (both futures traders):**
- **Reversal Exit**: peak ≥120pts + 2 consecutive adverse CLOSED 5-min bars + ≥30% of peak
  gone → market exit. sim_replay `--rev-exit 2,0.30,120`. Validated: 2026 YTD $6,072→$7,449
  (+23%), 2025 OOS $2,277→$2,731 (+20%), MaxDD flat, WR flat/up, best trade GREW (+$1,042→
  +$1,512 via re-entry), fired only 10×/7mo. Param plateau wide (0.30-0.35 × 120-150).
  Rejected: 1-min confirmation (fires on noise), retrace-only ratchets, graduated lock-frac,
  IB-scaled floors, TRENDING be_pts 90 (costs ~$700/yr; 90-120pt peaks stay a LOGGED
  watch-item — Jul 24's exact case, unprotected by design per two-year aggregate).
- **Partial Scale-Out**: 2-contract trades bank 1 at +150pts (limit-fill semantics live via
  market order at touch), runner stop→BE. rev+partial: $7,538 YTD26 / $2,818 2025 — beats
  rev-alone both years. Partial logged as its own CLOSED row (`notes='partial of #id'`).
- **TC full alignment (tc_trader.py)** — was still the pre-Jul-7 system end to end. Ported:
  A+-only entries, Trend Jury (hero gate), RVOL 0.85 gate + graduated 0.70 floor w/ Hero-GOLD
  compensation (completed-bar calc), 30-min HTF agreement, F1 consecutive-bar regime
  confirmation, rebuilt get_regime (RVOL≥0.65 + hybrid session/day reference + 5-bar trend),
  Jul 7 signal redefinitions (ORB w/o session restriction, 3-bar VWAP streaks, 3-of-4
  momentum), PM_SHORT disabled, 10:30 IB classification (_day_regime/_ib_kind incl.
  BEAR_DIRECTIONAL short unlock), 200/1500pt stop/target, regime-aware locks, Reversal Exit,
  Partial Scale-Out (dormant at TC's 1-contract sizing). Prop rules UNTOUCHED (DLL $700 soft,
  2 trades/day, TC_DAILY_CAP). Validated under TC caps: $6,017→$7,395 YTD26.
- **Bug sweep during build:** (1) partial rows would have consumed MAX_DAILY_TRADES slots —
  all 3 daily-count queries now exclude `notes LIKE 'partial of %'`; (2) same exclusion added
  to Trade Cop live query + SIM_FLAGS updated (`--rev-exit 2,0.30,120 --partial 150`);
  (3) rev-exit uses forming-bar age check (same class as RVOL/HTF fixes); (4) residual risk
  NOTED: if a partial MARKET order submits but never fills (near-impossible on MNQ RTH),
  DB/IBKR qty drift 1 contract until next reconcile.
- **London: rev-exit tested and REJECTED** (london_v2_sim, 5 variants, 18mo 1-min): baseline
  $3,177 vs $2,826-2,981 — London's edge is riding rare runners behind BE=0.10 armor; keep as is.
- **Equity same session:** Jul 21 book-health fix VERIFIED live (scan_log 1,620/2,401/2,381
  rows Jul 22-24, trades resumed, books cold-start ON, first real verdict ~Jul 28-29). Week
  -$124/15t. Jul 24 zero trades = genuinely thin (3 A+ LONG, 0 A+ SHORT all day), not a bug.
  **FIXED: enrich_scan_log LIMIT 2000→6000** — at 241 symbols (~2,400 rows/day) newest-first
  ordering left ~400 rows/day permanently unenriched, silently biasing the Book Health sample.
- Services restarted + verified; sunset review for both new exit rules Aug 24 2026.

---

## Aug 3 2026 — Manual FUT CLOSE hit wrong account (fixed) + dashboard TC blind spot (fixed) + strategy/options ideation

**Manual futures close bug, found + fixed.** User's first manual override ("exit all futures
now") was executed by importing `futures_trader.py`/`tc_trader.py` directly, outside the env
context only `launch_futures_personal.sh`/`launch_futures_trader.sh` set. `ACCOUNT_MODE` (from
`prop_rules.py`) silently defaulted to `'TC'` while the bridge URL still defaulted to IBKR (port
8000) — a mismatched combination that doesn't exist in any real launch config. Net effect: the
real IBKR position closed for real, but the DB write and `prop_state.json` update landed on a
live **TC** trade instead, briefly leaving a real TC position unmonitored (no more BE-lock/
trailing) until caught via a broker-vs-DB cross-check and fixed live (reverted the DB row,
corrected `prop_state.json`, then properly closed both accounts with the right scoping).
**Fixed permanently: `futures/close_all_now.sh [ibkr|tc|all]`** — bakes in the exact env each
real launch script sets per account and asserts `ACCOUNT_MODE`/`BRIDGE` match before acting.
**This is now the only sanctioned way to manually flatten futures — never re-import the trader
files directly.** Also learned: closing a position frees the "max open trades" slot immediately —
if the day's regime still qualifies, the bot can re-enter within one scan cycle. Flattening
positions and stopping new entries are two separate actions (`FUT CLOSE`/this script, vs.
`FUT PAUSE` via Telegram). Full incident + standing rule #14 in `incidents_and_rules` memory.

**Dashboard bug fixed (commit `e52bf31`):** `get_futures_positions()` in `dashboard/app.py` only
ever queried the IBKR bridge for live price/uPL, so open **TC**-account trades always rendered
"---" even though the TC bridge was healthy. Now prices each row off its own `account_mode`
(IBKR → 8000, TC → 8002).

**Strategy/options ideation review (ideation only — nothing built).** Reviewed two external
videos (a systematic strategy-family backtest across 30 assets; a 4-layer options monitoring
dashboard) and wrote a full connect-the-dots analysis:
`docs/IDEATION_2026-08-03_strategy_and_options_dashboard_review.md` (indexed in
`analysis_pending` memory). Headlines:
1. **No mean-reversion entry book exists anywhere in TriVega** — every book (equity LONG/SHORT,
   futures NY/London) is momentum/breakout-shaped. A category-level OOS Sharpe comparison (not
   ours — external, unverified against our data) found mean reversion was the only strategy
   family with positive mean OOS Sharpe across a diverse asset universe. **Mirror Book already
   *is* a mean-reversion structure** (fading decoder LONG signals) — never framed that way; worth
   revisiting at its Aug 17 30-day review.
2. **Chop/trend separation remains twice-documented as unsolved** (Jul 7: four hand-tuned chop
   detectors tried, none worked; Jul 8: "IB range alone can't cleanly separate real trend from
   wide whipsaw"). An HMM-based (learned, multi-feature) regime classifier is a genuinely
   untried technique against this exact problem — research candidate, not a fix.
3. **Options monitoring depth is a real, independent gap from the (already-diagnosed) broken
   trading logic** — no portfolio-level aggregate Greeks, concentration caps, strike ladder,
   theta-decay chart, or severity-tiered alerts. Buildable regardless of when/whether the
   liquidity-gate fix ships.

**Real-money funding note (IBKR futures, live-checked, not from memory):** current IBKR margin
~$4,374/contract intraday, ~$6,249 overnight for MNQ (moves over time — verify live before
funding). At the system's 2-contract hard cap that's ~$8,750 minimum just to hold size with zero
buffer — the Jul 18-decided `IBKR_FLOOR=$5,000` futures allocation is undersized for the
system's own validated sizing. Flagged, not yet revisited.

---

## Aug 3 2026 (night) — Options overhaul wave 1 SHIPPED: Spread Toll Gate + Book Pulse monitoring

Save point first: git tag `checkpoint-2026-08-03-pre-options-overhaul` (revert =
`git reset --hard` to it). User directive going forward: research backtests use 2yr data, not 5.

**① Spread Toll Gate (replaces the broken liquidity decider — the "options silent since
Jul 22" fix).** Hypothesis: the per-leg ≤15%-of-mid rule measured *moneyness*, not
*tradability* — a 0.10-delta far leg quotes wide relative to its own tiny mid on ANY
underlying (CAT's far call: $0.20/$3.05 = 175% of mid on one of the deepest chains in the
market), which is why it failed **37/37 candidates Jul 22–Aug 3** and starved the whole
system of both trades and learning data. New decider: **total two-leg bid-ask cost ≤ 12% of
spread width** (`_spread_liquidity()` + `LIQ_COST_PCT_MAX=12.0`), i.e. worst-case round-trip
friction vs max payoff — the economically meaningful quantity. Calibrated on quotes recorded
LIVE during the Jul 29/31 diagnosis: CAT 4.4% / COHR 8.3% / AMAT 9.2% (liquid, wrongly
blocked → now pass) vs IREN 16.1% (marginal small-cap → stays excluded). Threshold sits
mid-plateau (10–14% gives identical verdicts). All 4 calculators swapped (bull/bear debit,
bull-put/bear-call credit); LEAP calculator untouched (manual-only, absolute gate).
- **Validation honesty:** no historical chain data exists anywhere, so no 2yr backtest is
  possible for this gate. Evidence = (a) mechanistic proof from recorded quotes, (b) pinned
  regression test `options/test_spread_liquidity.py` (run it after any gate change),
  (c) forward-scoring of the 37 blocked candidates: 8 right / 7 wrong on +7d direction
  (~53%, coin flip) — which is the EXPECTED result, since liquidity-block reasons are
  orthogonal to direction; direction quality is the Options Book Gate's job, not this gate's.
- **Scoring wired:** both metrics now log to opt_calc_log (`liq_cost_pct`,
  `liq_legacy_max_rel` — idempotent ALTERs in database.py init_db), so the Ghost Ledger /
  nightly what-if can compare old-rule vs new-rule verdicts on every future candidate.
  skip_reason now carries the actual numbers. **Sunset review Sep 3 2026:** judge on
  (1) did trades actually flow, (2) realized entry/exit slippage vs the 12% bound,
  (3) Ghost Ledger PnL of new-rule ENTERs vs old-rule ENTERs.

**② Book Pulse (options monitoring depth — from the Aug 3 ideation doc).** watchman
`_snapshot_book_greeks()` writes book-level **net delta / daily theta bleed $ / net vega**
(long legs add, short legs subtract; credit spreads sign-flipped) to new `options_book_greeks`
table every full 5-min scan + at EOD; dashboard Options row renders it (freshness-gated to
today) plus **premium concentration per symbol**. Also new **T3a_NEAR alert tier**: INFO
telegram at ≥80% of the way to target (once/day per trade via the existing `fired` dedup set,
fact-only phrasing) — awareness before the hard T3b auto-close fires.

**③ Deferred from the ideation list (not built tonight):** strike ladder, theta-decay
projection chart, new-strike/new-expiry chain diffing (needs daily chain snapshots we don't
store yet), IV-rank-from-own-snapshots. Mean-reversion category sweep + HMM regime research
remain queued research items (2yr data when run).

Services restarted + verified: options_trader, watchman, dashboard. Smoke-tested the full
log path (calc dict → opt_calc_log round-trip). First live test of the gate = next market
open — watch the funnel line on the dashboard: if 0 calcs still, the block is upstream
(triggers/book health), not liquidity.

---

## Aug 5 2026 — Regime-Adaptive Suite SHIPPED to equity (live paper trial)

**Context:** multi-day thread starting from a YouTube review (Aug 3) through a fully
validated research trail (see `analysis_pending` memory sections -6 through -2) —
2yr backtest → 3yr re-confirm (4/5 regimes identical winner) → re-tested under the
REAL exit constraint this system enforces (EOD close / `MAX_HOLD_DAYS=1`, not the
original 10-day-hold assumption) → user-approved wiring, capital/sizing decided
explicitly, full code read-through done before writing anything, signal logic
unit-tested before shipping.

**What it is:** a second, independent equity entry path — `_scan_regime_adaptive()`
in `auto_trader.py` — that picks strategy AND direction from today's Weather Report
reading instead of always trading momentum:

| Regime | Strategy | Direction | Size |
|---|---|---|---|
| STRONG | ADX Trend (ADX>25 + 5d price confirm) | LONG | full |
| NORMAL | ADX Trend | LONG | full |
| WEAK | ADX Trend | SHORT | full |
| CAUTIOUS | Keltner Revert (price > EMA20+2×ATR) | SHORT | full |
| CHOPPY | RSI Revert (RSI>70) | SHORT | **half** (thinnest backtest evidence) |

**Explicit design decisions (so nobody has to reverse-engineer them later):**
- **Not gated by `book_is_on()`.** This is the whole point — it trades through
  periods the Book Health Selector has the LONG/SHORT books shut down, using a
  different signal. Whether that's actually good is exactly what this trial answers.
- **Shares the existing $10,000 / `MAX_OPEN_TRADES=5` pool** (`get_position_capital`/
  `get_deployed_capital`) — user's explicit call Aug 5, not a separate allocation.
- **Exits are 100% unmodified.** New trades get a normal `calc_sl_target()` stop via
  `place_trade()`, then `monitor_open_trades()` — ATR trail, PCT trail, hard stop,
  dollar circuit breaker, VWAP cross, momentum fade, EOD close, hard time stop, T+5
  confirmation — takes over exactly as it does for every other equity trade, because
  none of it branches on setup_type. Verified by reading the function, not assumed.
- Trades tagged `setup_type='REGIME_ADX_TREND'` / `'REGIME_KELTNER_REVERT'` /
  `'REGIME_RSI_REVERT'` — cleanly separable from LONG/SHORT book P&L and from Book
  Health's own measurement, so this trial can't contaminate either.
- **Known gap, not yet closed:** `equity_replay.py` calls the underlying decision
  pieces (`get_regime`, `get_intraday_signals`, `grade_setup`, `book_is_on`, ...)
  directly rather than the full `run_scan()` orchestration, so it does **not** yet
  include `_scan_regime_adaptive()` — the live paper trial itself is the validation
  for now. Close this gap before trusting `equity_replay.py` parity checks for this
  path specifically.
- **Sunset review: Sep 5 2026** (one month), per CONSTITUTION.md governance.
- **Dashboard visibility not yet built** — next step, not done in this session.

Restarted autotrader same session, verified clean startup. First real test is the
next trading day this qualifies on — watch the Telegram `📊 REGIME-ADAPTIVE` alerts
and the `REGIME_*` setup_type tags in `trades` to confirm it's firing as designed.

---

## Aug 6-7 2026 — Fish Finder / Weather Advisory equity regime redesign (research only, NOT wired) + two real infra bug fixes

Started from user tracking the Aug 5 Regime-Adaptive Suite's rough first live day.
**Daily-trade-cap bug found and fixed same session**: `_scan_regime_adaptive()`
increments `daily_bull_count`/`daily_bear_count` but never checked either against
`MAX_DAILY_BULL/BEAR_TRADES` before entering (every other equity scanner does) —
confirmed live: Bear hit 26/20 the same day. One-line fix, shipped, restarted.

**The redesign idea (ideation → validated architecture change, not yet trustworthy
enough to ship):** today's live `REGIME_STRATEGY_MAP` lets ONE market-wide regime
(SPY/QQQ/VIX rule stack) pick ONE strategy template for all 241 symbols at once.
Renamed for clarity mid-session (user asked for analogies): **Fish Finder** = test
every symbol against all three templates (`ADX_TREND`/`KELTNER_REVERT`/`RSI_REVERT`)
using its own live signals, not gated by which one template the market regime
selected. **Weather Advisory** = the regime label demoted from hard router to a soft
threshold modifier. Built as fully separate research code —
`research_fish_finder_weather_advisory.py` — never touches live
`REGIME_STRATEGY_MAP`/`_regime_adaptive_signal_fires`.

**Infra had to be fixed before any of this could be trusted (both are real, standalone
fixes, not specific to this research):**
1. `equity_replay.py` rebuilt to v2 — v1 hand-approximated auto_trader.py's logic
   (~88% parity, zero coverage of `_scan_regime_adaptive` at all, the priority flagged
   at the end of the Aug 5 session above). v2 mirrors `run_scan()`'s own routing and
   calls the REAL live functions (`_scan_and_enter`, `_scan_and_enter_bear`,
   `_scan_catalyst_override`, `_scan_regime_adaptive`, `monitor_open_trades`) under a
   frozen clock (`FakeDatetime`/`FakeDate`, patches `database.py`'s separate datetime
   binding too). New `FillSimulator` replaces `place_trade()` (instant fill, real DB
   write via real `log_trade_entry`, no bridge order/polling). A process-wide sqlite
   guard (`REPLAY_DB_PATH`, env-configurable `REPLAY_DB_SUFFIX` for parallel runs)
   redirects all `trades.db` writes to an isolated replay DB — required because 8
   call sites in auto_trader.py do `import sqlite3` INSIDE the function body, which an
   attribute-patch can't intercept. Verified: baseline-equivalence checkpoint (new
   harness restricted to the live-selected template only) reproduced live's real
   entries EXACTLY. Known accepted v1-style gaps carried forward: `_check_layer2_fitness`
   fails open, `book_is_on()` reads real production `scan_log` (correctly — but
   `BOOK_HEALTH_RESET_DATE=2026-07-22` means it's unconditionally cold-start-ON for
   virtually this entire 2024-2026 backtest window), VIX has no real intraday data on
   any DataBento dataset this account can reach (confirmed via `list_datasets()`) so
   it's a flat-daily-value proxy — `vix_val` threshold checks work, `vix_rising` never
   fires. Supersedes both v1 and the already-stale `sim_today.py` as the equity
   backtest tool going forward.
2. `collect_bars.load_bars()` had a real data-integrity bug, found because the
   overnight full-history run showed 5 positions stuck open with ZERO trades for 8+
   straight days starting exactly when DataBento-backfill-era data met daily-collector-
   era data (~May 2026). Root cause: `bars_5m.ts_utc` has two coexisting string formats
   ('2024-01-02T14:30:00' vs '2026-05-28 13:30:00+00:00') that overlap rather than
   cutting over cleanly — `pd.read_sql_query(..., parse_dates=[...])` infers one format
   from the majority sample and silently turns the minority format into NaT (confirmed:
   8,580/55,482 rows for one symbol), which then gets invisibly dropped by any date
   filter (`bars5_upto`'s 1-day lookback returned 0 rows past the boundary → `get_live_price`
   → `None` → `monitor_open_trades` silently `continue`s past the position forever,
   including skipping the unconditional EOD-close-for-shorts check). Same overlap also
   produced ~4,900 genuine duplicate bars with CONFLICTING OHLCV from the two sources.
   Fixed: explicit `format='mixed'` parse + `keep='last'` dedup. **Affects any code
   calling `load_bars`/`load_multi` across that date boundary, not just this
   research** — worth remembering next time something inexplicably goes quiet on a
   backtest spanning mid-2026.

**Research trail (all on real 5-min DataBento bars via the v2 harness, not synthetic):**
- Data-driven parameter checks using ALREADY-LOGGED trade data (no reruns needed): RSI_REVERT's
  SHORT threshold (live: 70) showed a clean, monotonic, large-n cumulative-P&L peak at
  **75** (+$61 vs +$8 at 70, n=270) — LONG threshold (30) showed no such pattern, left
  alone. `ADX_TREND`'s per-symbol ADX magnitude showed NO clean threshold pattern
  (ruling out "just raise the ADX bar" as a fix).
- Regime-quality dig (motivated by "is our regime detector missing dimensions a pro
  system would have?"): found a REAL bug — `equity_replay.py`'s `_bridge_df` patch
  ignored `bar_size`, so the live breadth check (IWM/MDY vs SPY) computed a near-zero,
  meaningless delta in every backtest to that point. Fixed. Then tested two NEW
  candidate dimensions against real logged `ADX_TREND` trades (reusing cached bars, no
  new backtest run): **universe breadth** (% of the 241-symbol universe green
  intraday) and **universe correlation** (mean pairwise correlation, 60-symbol sample,
  trailing 20 daily returns) both showed a strong TAIL effect — extreme values (very
  high breadth OR very high correlation) were where `ADX_TREND` lost most of its
  money, moderate/low values were fine. Correlation's direction was the OPPOSITE of
  the original hypothesis (low correlation = idiosyncratic, real stock-specific moves
  = good for trend; high correlation = herd/panic moves = bad and prone to snap back)
  — a genuine reversal of the initial guess, confirmed by data.
- Old design (live, unmodified) vs new design ladder, H1 2025 (6mo): unrestricted Fish
  Finder alone was WORSE than live (-$1,721 vs OLD's -$626) — but with a real,
  actionable split underneath: `RSI_REVERT` flipped from -$432 to +$86 (freed from
  CHOPPY-only gating, competes on conviction instead), `ADX_TREND` got much worse
  (-$991, no market-wide trend backdrop requirement anymore). Categorical hybrid gate
  (`ADX_TREND` restricted to STRONG/NORMAL/WEAK, matching live's own regime map) got to
  -$1,186. **Breadth gate (`ADX_TREND` blocked above 60% universe breadth) alone: -$773.
  Correlation gate (blocked above 0.35) alone: +$145 — first profitable variant, and
  better than breadth alone.** Correlation + tuned RSI-75 together: **+$803**, the
  H1-2025 winner. `KELTNER_REVERT` degraded in every variant tested (both alone and
  combined) and its root cause is UNRESOLVED — band-distance-past-Keltner-band and
  breadth-sensitivity were both tested as hypotheses against real logged trades,
  neither explained it.
- **Full-history verdict (2024-01-02 → 2026-08-06, correlation35+RSI75 vs live,
  ground-truth DB totals — the harness's own printed running total had a second bug,
  see below): NEW +$2,361.32 (20,861 trades) vs OLD +$458.10 (14,994 trades) —
  NEW wins in aggregate, but the split by period is the real finding:**

| Period | OLD | NEW | NEW − OLD |
|---|---|---|---|
| 2024 | +$322.78 | +$1,888.07 | +$1,565 |
| H1 2025 (tuning window) | -$848.35 | +$1,007.76 | +$1,856 |
| H2 2025 (OOS) | +$530.56 | +$112.42 | **-$418** |
| 2026 YTD (OOS) | +$453.11 | -$646.93 | **-$1,100** |

  **NEW's entire edge is back-loaded into 2024/H1 2025 — it LOSES to the live design in
  both genuinely out-of-sample periods.** Exactly the failure mode a real OOS check
  exists to catch. Not validated enough to wire. Next session priority: understand WHY
  performance flipped in the last ~13 months before considering this further — see
  Active Work Board.
- **Second harness bug found via this same run**: `_run_replay()`'s printed "Total
  P&L" summed each day's `get_daily_pnl()` snapshot, which filters `entry_date=<that
  day>` — so any trade held overnight (entered day N, exited day N+k) was invisible to
  BOTH day N's snapshot (still OPEN when taken) and day N+k's (only counts trades that
  ENTERED that day). Confirmed: 54 multi-day-held trades worth +$2,500.87 were
  completely uncounted by the old summary math on the full-history NEW run (printed
  "-$140", true DB total was +$2,361.32) — same bug affected OLD's printed total too
  (-$624 printed vs +$458.10 true). Fixed: `_run_replay()` now queries the replay DB
  directly for the grand total instead of accumulating from daily snapshots.
  Interesting side-note surfaced by the fix: those 54 multi-day trades averaged
  +$46/trade vs ~breakeven for the mass of same-day trades — consistent with the
  original "let winners run" idea that started this whole thread (see below).

**Original idea, still not built:** the whole redesign thread started from asking how
to bank small profits on ordinary days while letting a real runner ride on a big day.
Answer landed on: don't build new logic — `monitor_open_trades()` already has a
partial-exit-at-1R mechanism (banks 50% at +5%, moves stop, lets the rest ride), it's
just hardcoded off for regime-adaptive trades (`first_bar_strong_trades[trade_id] =
False`, always) and calibrated for a wider move than these trades typically show.
Redirect + recalibrate threshold, don't rebuild. Not implemented yet.

**Also found live, same session (real, unrelated bug):** `BUY <SYM>` Telegram command
(`auto_trader.py:2729`) unconditionally opens a new manual LONG regardless of an
existing position — confirmed during a real SOUN incident (user's BUY-to-cover attempt
opened a new long instead of closing the short; the short had actually already closed
via its own automated trailing stop moments earlier — a race condition between manual
action and live automation, not the BUY bug directly, but the BUY bug is why "cover"
doesn't work at all right now). `SELL <SYM>`/`CLOSEALL` are correct — they check
`is_short` from the actual open position and reverse direction accordingly. Manually
reconciled SOUN back to flat same session (verified against live IBKR portfolio, not
just DB). Not fixed yet — flagged on Active Work Board.

---

## Aug 8 2026 — Fish Finder wired live (replaces Aug 5 single-template Regime-Adaptive Suite)

**Hypothesis:** the Aug 6-7 finding — Fish Finder beat the live design full-history but
lost in both OOS periods (H2 2025, 2026 YTD) — had two distinct, real, fixable root
causes rather than being an unfit idea. If both could be diagnosed and fixed (not
curve-fit) and the fixed version beat the live design across the FULL history including
the periods it used to lose, it would be a legitimate replacement candidate for a live
paper trial — the missing piece being genuine out-of-sample data, which no amount of
further backtesting against 2024-2026 can manufacture.

**Investigation (same session, extending Aug 6-7's work):**
- **2026 YTD failure, root-caused:** joined every `FISHFINDER_ADX_TREND` trade to its
  real entry-time regime (reusing cached SPY/QQQ/IWM/MDY bars, no new backtest needed).
  Ruled out the obvious suspect first — adding `--hybrid` (restrict `ADX_TREND` to
  STRONG/NORMAL/WEAK) would NOT have fixed it, since 89% of 2026's loss (-$546.75 of
  -$615.81) sat *inside* those already-live-eligible regimes. Real driver: WEAK-regime
  `ADX_TREND` alone lost -$813.91/1,133 trades in 2026 — bigger than the category's
  entire net loss — both LONG and SHORT simultaneously unprofitable, spread across 5 of
  7 months (not one bad day). Confirmed the SAME degradation exists in the currently-
  live (pre-redesign) design's own WEAK-regime SHORT `ADX_TREND` (-$191/816 trades,
  2026) — this was never Fish-Finder-specific, just newly exposed by it.
- **H2 2025 failure, root-caused:** unrelated mechanism. Live only ever fires
  `KELTNER_REVERT` SHORT, only in CAUTIOUS regime (+$215/143 trades, H2 2025). Under
  Fish Finder the SAME CAUTIOUS-regime opportunity set collapsed to just 11-35 trades
  per period (as low as 11.8% of what live captures on identical days) — traced to
  Keltner's conviction score being a hardcoded flat 1.0 while `ADX_TREND`/`RSI_REVERT`
  scale continuously; ADX's own winning-trade conviction distribution sits almost
  exactly on that 1.0 (median 1.00, IQR 0.47-1.67, n=15,276) — a real coin-flip loss in
  3-way tie-breaks, not a landslide, but enough to starve Keltner out of its own best
  regime.
- **Two candidate fixes tested and REJECTED before landing on what shipped:**
  (1) scaling Keltner's conviction by real ATR band-distance — ~0 correlation to P&L in
  every period, full 3,393-trade history; this is a tie-break FREQUENCY problem, not a
  quality-ranking problem. (2) A trailing-P&L health gate on Keltner overall (the same
  mechanism that fixed the WEAK-ADX problem) — noisy, non-monotonic across window
  sizes, mostly worse than baseline; ADX's problem was a genuine multi-month signal
  decay (well-suited to a trailing-window gate), Keltner's was a selection-rate
  problem (not suited to one). Also rejected: regime-eligibility exclusion for Keltner
  (CHOPPY looked bad only in H2 2025, was Keltner's *best* regime in 2024/H1-2025/2026 —
  same aggregate-hides-the-period trap flagged repeatedly in this file's history).
- **What shipped instead:** **Bite Check** — a trailing 7-trading-day P&L gate scoped
  specifically to WEAK-regime `ADX_TREND` (not the whole book, not all of Keltner).
  Window chosen from a real plateau (5-9 days all worked in a post-hoc sweep), not a
  single cherry-picked number. **Fair Cast** — raised `KELTNER_CONVICTION` 1.0→2.0
  (beats ~82% of ADX's own conviction distribution) so Keltner stops losing ties it
  should often win. Both combined with the two pieces already in the Aug 6-7 combo:
  **Crowd Gauge** (correlation gate, 0.35, unchanged) and the RSI-75 retune.
- **Validation — full 2024-2026 backtest, all 4 periods, real scanner-integrated reruns
  (not post-hoc trade removal):**

| Period | OLD (live) | Baseline Fish Finder | +Bite Check only | +Bite Check +Fair Cast (shipped) |
|---|---|---|---|---|
| 2024 | +$323 | +$1,888 | +$2,018 | +$1,293 |
| H1 2025 | -$848 | +$1,008 | +$349 | +$387 |
| H2 2025 | +$531 | +$112 | +$357 | +$1,171 |
| 2026 YTD | +$453 | -$647 | +$1,154 | +$1,092 |
| **Total** | +$458 | +$2,361 | +$3,878 | **+$3,942** |

  Beats OLD in **all 4 periods** for the first time in this whole investigation. Two
  honest surprises found by checking every period instead of trusting totals (same
  discipline this file has flagged repeatedly for *other* research, now caught in our
  own): Bite Check alone REGRESSED H1-2025 by -$658.83 despite never touching that
  period's `ADX_TREND` logic directly — traced to a side effect, not a bug: blocking
  WEAK-regime `ADX_TREND` frees `MAX_OPEN_TRADES` slots, and in H1-2025 those slots got
  filled by +352 extra Keltner trades and +357 extra RSI trades that were markedly
  lower quality than what those templates normally get (the SAME mechanism helped in
  2026 and H2-2025 — the redistribution's sign is genuinely period-dependent, not a
  free lunch). Separately, adding Fair Cast on top cost 2024 -$725 vs Bite-Check-alone
  — Keltner winning more ties now steals some *good* `ADX_TREND` trades too, not just
  idle ones it lost fairly (~$2/trade average quality of what got displaced vs ~$0.04
  from the extra Keltner volume that replaced it). Net effect across all periods still
  strongly positive; both surprises are why `KELTNER_CONVICTION=2.0` is flagged
  PROVISIONAL rather than final.

**Decision (user-directed):** wire it — replace the Aug 5 design outright (not run in
parallel off the shared capital pool, which would make results uninterpretable), since
paper capital + a real, mechanistically-diagnosed, full-history-validated edge is
exactly the situation forward paper trading is for. Every number above comes from the
same 2024-2026 tape used to find the fixes — no genuine blind data has touched this yet.
This live trial IS that missing test, not a formality.

**Shipped same session:**
- `regime_at_entry` column added to `trades` table (idempotent ALTER, `database.py`) +
  `set_regime_at_entry()`/`get_weak_regime_adx_history()` helpers — makes Bite Check
  DB-backed instead of an in-memory list, so it survives autotrader restarts correctly
  (an in-memory version would have silently cold-started on every restart, which is NOT
  the thing that was backtested — a single continuous process).
- `equity_replay.py`'s parity coverage for `_scan_regime_adaptive` — checked, already
  closed by the Aug 6-7 v2 rebuild (confirmed: `equity_replay.py:501` already calls
  `at._scan_regime_adaptive()` through the real orchestration path; the CLAUDE.md note
  calling this an open gap was stale, predating that rebuild landing).
- Crowd Gauge's live cost — timed at 2.1s for a real 61-symbol `yf.download()` call;
  designed as a once-per-calendar-day cached computation (correlation is a daily-
  frequency signal, no reason to recompute every 5-min scan), so the "scan cycle must
  stay under 5 min" constraint was never actually at risk once built this way.
- `auto_trader.py`: `REGIME_STRATEGY_MAP`/`_regime_adaptive_signal_fires` replaced by
  `_fishfinder_candidates()`/`_fishfinder_resolve_tie()`/`_crowd_gauge_correlation()`/
  `_bite_check_ok()`; `_scan_regime_adaptive()` rewritten around them, same function
  signature (no equity_replay.py changes needed). `setup_type` now `FISHFINDER_*`.
- `GLOSSARY.md` updated: Fish Finder / Crowd Gauge / Bite Check / Fair Cast rows added,
  Regime-Adaptive Suite row updated to point at the new engine.
- Savepoint: `git tag checkpoint-2026-08-08-pre-fishfinder-wiring` (commit `c600ee4`,
  which also bundled this week's already-tested prior fixes — collect_bars timestamp
  bug, equity_replay v2, the Aug 5 daily-cap fix — that were sitting uncommitted).
  Rollback: `git reset --hard checkpoint-2026-08-08-pre-fishfinder-wiring`.

**Still open, not done this session:** Fish Finder dashboard card (gate states,
per-template funnel, P&L split) — next. Restart + live verification — next.

**Post-wiring bug sweep (same night) — two rounds, one real bug found and fixed, plus two
user questions investigated with real data:**
- Round 1 (fresh re-read + empirical testing, not just reading): all new DB functions
  (`set_regime_at_entry`, `get_weak_regime_adx_history`, the two `log_fishfinder_*`
  upserts) tested with real writes/reads including the no-lookahead boundary condition.
  Ran the actual wired code through `equity_replay.py` directly (not the research
  script's separate scanner — the real `_scan_regime_adaptive` as now written) for two
  short smoke windows (Aug 3-5 2026, Feb 2-4 2026 for WEAK-regime coverage) — confirmed
  end to end: `FISHFINDER_*` trades write correctly, every trade gets `regime_at_entry`
  tagged (verified 100% coverage, zero gaps), `fishfinder_gate_log` populates correctly
  including the WEAK-regime-only Bite Check path. Zero errors either run.
- Round 2 (line-by-line cross-check vs the validated research script): found and fixed
  one real discrepancy — `_crowd_gauge_correlation()` used pandas' pairwise `.corr()`,
  which handles missing data differently per symbol-pair than the validated script's
  min-length-aligned `np.corrcoef()` approach. Could disagree right at the 0.35
  threshold. Fixed to match the validated algorithm exactly. Everything else (constants,
  gate ordering, tie-break logic, capital/sizing calc) confirmed identical or a verified
  no-op simplification (breadth gate and regime-eligibility restriction were never
  active in the tested combo, so correctly omitted from the live port).
- **User caught a real question, investigated with actual trade data:** confirmed
  `MAX_DAILY_BULL/BEAR_TRADES=20` is NOT currently being exceeded — the only overage in
  the last 15 days was Aug 5's 26 SHORT trades, which is the exact already-documented
  incident (fixed same day, mid-day). Aug 6 and Aug 7 (the actual last trading day) both
  correctly respect the cap; Aug 7 hit exactly 20/20 both sides, which is the cap
  working as intended on a busy day, not a violation.
- **15-day retrospective (user-requested, reused already-computed backtest data — no
  new heavy compute needed):** last 15 real trading days (Jul 20 – Aug 7 2026), OLD
  design (what actually ran) vs Fish Finder combo backtested on the identical window:
  **OLD +$279.59 (293 trades) beats Fish Finder +$129.65 (530 trades)** — not the
  direction anyone would hope for right after wiring. Driven almost entirely by
  `ADX_TREND`: OLD +$434.62/177 trades vs Fish Finder +$112.48/336 trades — unrestricted
  regime eligibility (no `--hybrid`, matching what was validated) nearly doubled trade
  count and diluted quality badly in this specific window. `KELTNER_REVERT` (Fair Cast)
  and `RSI_REVERT` (tuned threshold) both looked healthy in the same window (Keltner
  captured 125 trades vs OLD's 4, held P&L roughly even at 30x the sample; RSI flipped
  from -$68.76 to +$33.30). Read honestly, not spun: this is a genuine data point, not a
  verdict — a 15-day window is exactly where variance dominates, and this window isn't
  one of the four multi-month periods actually validated against. But the `ADX_TREND`
  dilution effect from unrestricted eligibility is a real, recurring pattern (also hurt
  H1-2025 in the earlier full-history check) worth watching as live data accumulates —
  candidate: revisit whether `--hybrid` (restrict `ADX_TREND` to STRONG/NORMAL/WEAK, the
  regimes live's original design used) should be added back, now that Bite Check handles
  the WEAK-regime-specific decay separately from the eligibility question.
- **WFA (walk-forward analysis) — NOT done, and not really reconstructable for these
  specific fixes.** Bite Check and Fair Cast were reverse-engineered by looking directly
  at the 2026/H2-2025 failures — there's no unseen slice of 2024-2026 left to walk
  forward into with this same tape; a WFA-shaped split now would just re-test on data
  already used to design the fix. Real forward-looking validation from here is live
  paper trading (genuinely unseen data), not more backtesting against 2024-2026. A
  cheaper partial substitute floated but not yet done: refit `BITE_CHECK_WINDOW`/
  `KELTNER_CONVICTION` using ONLY 2024 data, check if those independently-derived values
  still hold on 2025-2026 unchanged — tests parameter robustness, not full blindness.

---

## Aug 8 2026 (late night) — Exit-side psychology: stall-recycling backtest + Thesis Check LLM observer + Chart Gate finally wired

User spent a week watching every trade live (equity + futures) and noticed a specific
pattern: cutting winners early on gut feel (0.75-1.5% "good enough, free the capital"),
but NOT cutting losers early — deferring to the system, and regretting it in hindsight.
Asked whether an LLM should be "in the loop" to formalize this kind of judgment. Answer
split into three genuinely different questions, not one:

**1. Early winner-cutting — NOT built, the data already argues against it.** The Jul 25
futures give-back research already tested this exact idea and found every faster-exit
variant LOSES money (+$4,340 → -$264 for "exit the minute it reverses") — give-back is
the entry fee for the rare huge trend that pays for every small loser. Told the user
directly rather than build something the existing evidence contradicts.

**2. Stall-based capital recycling — backtested (real bar data, not live), validated,
QUEUED behind Fish Finder's Sep 8 review, not shipped.** Different question from #1: not
"is this trade good," but "has this trade earned the right to keep holding capital."
Joined all 20,711 Fish Finder combo trades (full 2024-2026) against real 5-min bars to
check unrealized P&L at the 30/60-min mark post-entry. Result, n=11,520+:
**a trade that's barely positive (0-0.5%) 30 minutes after entry goes on to LOSE money
90% of the time** (eventual WR 10.5%). Banking at the 30-min stalled mark beats holding
to the actual exit by $15,639 across the sample (bank $9,059 vs actual -$6,580). 60-min
checkpoint shows the same pattern, slightly stronger in dollars. **Distinct from and not
contradicted by #1**: #1 is about proven winners giving back gains; this is about trades
that never showed real strength in the first place — "let a real winner run" and "a
trade still flat after 30min hasn't earned more time" are both true simultaneously. More
robust than the Aug 8 WEAK-gate finding earlier tonight because it doesn't depend on what
the freed capital does next — holding this specific position further loses even if the
alternative is just cash. Not shipped live — queued deliberately to avoid confounding
Fish Finder's own clean observation window with a second simultaneous behavior change.

**3. Early loss judgment — Thesis Check, SHIPPED live tonight, LOG MODE only.** Equity
had no analog to futures' thesis-invalidation exit (2-of-4 signal vote cutting losers
before the hard stop) — it leaned on the fixed 5% stop and little else for longs. Built
`_thesis_check_position()`: on any OPEN LOSING position (≤-0.5%, throttled to once per
15min/trade so it doesn't spam the 30s monitor loop), Claude vision reads the live 5m
chart and logs INTACT or BREAKING vs the original entry thesis. Same instrument-first
doctrine as everything else in this system — does NOT touch the trade, scored Fridays
4:36pm (`thesis_check_weekly_review()`) against real outcomes before graduation is even
discussed. See GLOSSARY.md.

**Bonus, found in the process: Chart Gate was dead code.** `_chart_alignment_check()` was
fully built (1h/5m chart + Claude vision + a weekly review job) but had ZERO call sites
anywhere in `auto_trader.py` — never once ran, zero data ever accumulated, since whenever
it was originally built. Wired into `_scan_and_enter()` right after a successful LONG
entry (background thread, log-only, matches its original design). **Testing it live for
the first time immediately found a real bug**: `_generate_chart_b64()` passed
`addplot=None` to `mplfinance.plot()` whenever there was no VWAP overlay (the 1h chart
never gets one) — mplfinance's validator rejects `None` outright and crashes. Fixed:
omit the `addplot` kwarg entirely instead of passing `None`. This bug would have silently
killed every 1h-chart call since Chart Gate was written; nobody could have known without
actually running it, which is exactly the point of wiring log-only observers early rather
than leaving them built-but-dormant.

**Both #3 and the Chart Gate fix are live now** (log-only, zero behavioral change, no
confound with Fish Finder's observation window) — first real data expected within days,
scored weekly. #2 stays backtested-only, queued for after Sep 8.

---

## Aug 9 2026 — TC prop-rules review + contract-sizing fix + London wired to TC + Crest Watch (futures LLM observer)

**⚠️ NONE OF THIS IS LIVE YET — code changes only, no service restarted.** User is
preparing to wire TS (TopStepX) to the platform and start a real $50K Combine evaluation
next week; asked for a full review of TC vs IBKR parity plus prep work. Real TopStep $50K
plan numbers confirmed via a screenshot of TopStep's own pricing page (not guessed):
profit target $3,000, consistency 50%, Max Loss Limit (trailing) $2,000, Daily Loss Limit
$1,000, contract limit 5 mini / 50 micro. Every number in `prop_rules.py` already matched
except contract sizing, which had been hardcoded to 1 contract since account-open and
never revisited.

**① IBKR/TC code-parity audit (diffed function-by-function, not assumed from docs):**
genuinely in sync as of commit `38803b0` (Jul 26) — Reversal Exit, Partial Scale-Out,
regime-aware exit locks, PM_SHORT disable, Trend Jury/RVOL graduated floor, F1 fix, and
the rebuilt `get_regime` all identical or intentionally mirrored. Two real gaps found and
fixed:
- `compute_overnight_bias()` (automated overnight-position classifier) existed only in
  `futures_trader.py`. Ported to `tc_trader.py` verbatim (globals, `_OVN_*` thresholds,
  the log-only `OVN_SKIP` gate, daily reset, `FUT BIAS` override reset). Not purely
  cosmetic — `_daily_macro_bias` (auto-set LONG/SHORT when overnight position ≥0.85 or
  ≤0.20) is a real directional gate in `grade_entry()`, TC was silently missing it.
- TC's contract sizing was hardcoded to 1 via **two** stale mechanisms:
  `prop_rules.get_max_contracts()`'s TC branch (`min(base_contracts, 1)`) and TC's own
  `MAX_RISK_PER_TRADE=$100` (comment still said "50-tick stop" — the real stop is
  200pts/800 ticks since Jul 8). **Fixed:** new `TC_TRADING_MAX_CONTRACTS = 2`
  (prop_rules.py) — mirrors `IBKR_MAX_CONTRACTS`, chosen because a single worst-case
  2-contract stop-out ($800) sits inside the $2,000 MLL and roughly at (not past) the
  $700 soft DLL; user confirmed this cap over a larger one. Ported `calc_contracts_dynamic`
  (RVOL/IB-range tiered sizing, + `had_loss_today`/`load_avg_volumes`/`calc_rvol_current`)
  from `futures_trader.py` into `tc_trader.py` verbatim, wired into `place_trade()`
  replacing the flat `calc_contracts()` (kept as dead code, same precedent as IBKR's own
  unused `MAX_RISK_PER_TRADE`). This also auto-activates Partial Scale-Out on TC, which
  was already fully wired but dormant at 1-contract sizing. `TC_MAX_CONTRACTS=50` (the
  real platform ceiling) was already sitting in `prop_rules.py` but never wired to
  anything — now documented as a disaster ceiling, not a trading size; kept unused on
  purpose.

**② London wired to TC — new architecture requirement, not a flag flip.** Investigated
before touching anything: `london_trades` had no `account_mode` column (one IBKR instance
only, ever), and `london_trader.py` never called `prop_rules.check_can_trade()` /
`record_trade_pnl()` at all — a London loss was invisible to prop_rules' DLL/MLL tracking
on **both** accounts. Low-cost gap for IBKR (soft/internal limits only); would have been a
real compliance risk for TC (TopStep enforces the $1,000 DLL / $2,000 MLL account-wide,
regardless of which session caused the loss). User confirmed: build this properly before
the eval, not as a fast-follow. Shipped:
- `london_trades` gets an idempotent `account_mode TEXT DEFAULT 'IBKR'` column (verified
  the backfill against a real trades.db copy — all 55 existing rows correctly tagged
  'IBKR', zero data loss).
- `london_trader.py` now imports `ACCOUNT_MODE`/`check_can_trade`/`record_trade_pnl` from
  `prop_rules`; every DB write tags `account_mode`, `get_london_daily_pnl()` filters by it
  (previously would have blended IBKR+TC P&L once TC started writing — this fed a real
  DLL gate, not just reporting). `place_london_trade()` now calls `check_can_trade()`
  before submitting (approximates `unrealized_pnl=0` — doesn't see the NY session's own
  open unrealized; the $300 `SOFT_STOP_BUFFER` absorbs that imprecision). `_log_exit()`
  now calls `record_trade_pnl()` on every close — this is the load-bearing fix, it's what
  makes DLL/MLL genuinely shared between NY and London on the same account.
- **London's own strategy logic, champion params, and position-sizing formula were NOT
  touched** — deliberately, per the standing no-mid-run-tinkering rule. `MAX_CONTRACTS=2`
  in `london_trader.py` already happened to match the new TC cap, so no sizing-math
  changes were needed to make it TC-safe.
- `tc_trader.py` gets the same `LONDON_ENABLED = True` + scheduler-job pattern as
  `futures_trader.py` (IB 3-4am, entries 4-8am ET, 15s monitor). `london_trader.py`
  resolves its own `BRIDGE`/`ACCOUNT_MODE` from whichever process's env it's threaded
  into (`FUTURES_BRIDGE_URL`/`FUTURES_ACCOUNT_MODE`), same pattern as everything else in
  this file.
- **Known gap, not fixed this session:** `parity_check.py`, `expectancy_ledger.py`, and
  `dashboard/app.py`'s London queries are not yet `account_mode`-filtered — once TC starts
  writing rows, those reports will blend IBKR+TC numbers. Reporting-only, not a live-
  trading-correctness risk (unlike the two fixes above) — flagged, not urgent, fix before
  trusting those specific reports once TC London has real trades.

**③ Crest Watch — new LLM in-trade observer, LOG MODE only.** See `futures/thesis_check.py`
+ GLOSSARY.md. Built from a real 2-week review of IBKR manual `FUT CLOSE` decisions
(Jul 26–Aug 9): 7 distinct manual-close events, 4 premature (Aug 3, Aug 4 — genuine trend
days, 128-322pt of further favorable move left on the table with near-zero pullback), 3
correct (Aug 4 last one, both Aug 6 — a genuine reversal day, 58-219pt of give-back
avoided). Net: the misses cost more than the catches saved. Reframed the ask from
"exit timing" to "trend-vs-reversal classification for the day" — the same problem
flagged unsolved twice before (Jul 7/8: RVOL, ADX, IB-range, VWAP-cross-count all tried,
all failed). On any OPEN, PROFITABLE position past a 100pt peak (matches Reversal Exit's
own `REV_EXIT_PEAK_MIN_PTS` floor so the two are judged on the same population), checked
at most every 15min/trade, Claude vision reads the 5m chart and logs CONTINUE or
REVERSAL_RISK to a new `futures_thesis_check` table. Does not touch the trade. Shared
module (`futures/thesis_check.py`), used by both traders, account-isolated. Weekly review
Fridays 4:40pm cross-refs verdicts against real exit prices. Same instrument-first
doctrine as equity's Thesis Check/Chart Gate — graduation to an actual gate only after
real data shows it beats what Reversal Exit's fixed thresholds already do.

**Before the eval starts:** restart `futures_personal` (IBKR — picks up Crest Watch) and
`futures_trader` (TC — picks up all of the above) via `launchctl kickstart -k`, verify
clean startup in logs (no import errors — `futures/thesis_check.py` pulls in `anthropic`
+ `mplfinance`, both already used by equity so should be present in venv, but confirm),
confirm the `🇬🇧 London session ENABLED (TC)` Telegram message arrives, and watch the
first TC scan for the new `calc_contracts_dynamic` sizing (contracts should show up to 2,
not stuck at 1) and the overnight-bias log line before trusting any of this live. This
touches a real account with a real evaluation fee — do not skip the smoke-test day.

**Restart actually done Aug 9 2026, late night (follow-up session, log audit).** Found
both processes were still on pre-session code: `futures_personal` had been running since
17:02, three minutes *before* commit `a6fd51a` (17:05) landed — zero Crest Watch, zero
contract-sizing fix, nothing from today. `futures_trader` (TC) had been restarted at
18:32 (after `a6fd51a`, so it had Crest Watch) but *before* `463680f` (18:35, the
`MAX_DAILY_TRADES` 2→5 raise) — running with the stale cap. Also found and fixed a real
gap while auditing `thesis_check.py` before trusting it for a week of unattended data
collection: `_chart_b64()`/`_ask_claude()` swallow their own exceptions and return
`None`, and `_run_check()` silently `return`ed on either — an API hiccup, rate limit, or
render failure would vanish with zero trace, indistinguishable from "no trade qualified"
at the Friday review. Added a `[FUTURES THESIS CHECK] ... SKIPPED — <reason>` log line on
both failure paths. Both traders restarted twice (once for the day's backlog, once for
the logging fix), verified clean startup both times (no import errors, London enabled,
RVOL loaded), both bridges reconnected (`DU9952463` paper / `DUQ640500` LIVE). Market
closed (Sunday night, weekend guard active) — zero `futures_thesis_check` rows yet, first
real data starts once a position peaks ≥100pts profitable during a live session.
Committed `91e64a4`.

**Crest Watch redesigned same night (design review with user, before any data had
accumulated — 0 rows logged, zero migration cost).** Chart-vision dropped entirely —
`mplfinance`/image generation removed from this file. Rationale (debated with user
first, see the session, not re-derived here): a vision model reading candlestick pixels
is strictly worse at the numeric judgments (momentum, distance-to-level, volume regime)
than the indicators this codebase already computes precisely every cycle: institutional
practice is to feed engineered/structured features to a model and reserve the LLM for
genuinely qualitative synthesis text can't reduce to a number — not to have it re-derive
what ADX/RVOL already compute better from a picture.

**What changed:**
- **Input:** text-only structured snapshot built from state the exit logic already
  computed that cycle — NY passes `regime` (`_day_regime`), `rvol` (`calc_session_rvol`),
  `rsi` (`calc_rsi`), `price_vs_vwap`; London passes `atr_pts`, `overnight_bias` (`_ovn_pos`),
  `ib_range` (now also stored on the in-memory `_position` dict, wasn't before). Both pass
  `stop_distance_pts` and an `active trail tier` label (`be_lock`/`wide_trail`/`tight_trail`/
  `none`, derived from the same tier thresholds the trail-update code uses). No chart image
  is generated or sent — cheaper and faster per call, one fewer dependency in this file.
- **Output:** `output_config`/`json_schema` structured call (same pattern as
  `market_context.py`'s Field Report) returns `{risk_score: 0-100, reasoning}` instead of
  a binary CONTINUE/REVERSAL_RISK label — replaces a switch with a dial. Legacy verdict
  label still derived (`risk_score >= RISK_SCORE_ALERT_THRESHOLD=55`, **PROVISIONAL**,
  untuned) for readable logs and weekly-review scoring continuity.
- **Persistence required:** DB-backed (not in-memory) per-trade streak counter — same
  restart-survives-correctly lesson already learned once for Bite Check (Aug 8) — mirrors
  Reversal Exit's own "2 consecutive confirmations before acting" pattern rather than
  trusting a single point-in-time read.
- **Full receipt kept:** `raw_response` (complete JSON, not just a trimmed sentence) +
  `model_version` (captured from `resp.model`, not hardcoded) — this call can't be
  cheaply re-run against historical market state later, so whatever isn't logged now is
  gone for good.
- **Field Report folded in, logged separately, not blended:** today's `market_brief`
  stance + one-line thesis added to the prompt and stored in their own columns
  (`field_report_stance`/`field_report_thesis`) — NOT merged into `risk_score` itself, so
  it can be judged independently later. Deliberate caution: this codebase already found
  once that a raw sentiment signal can run backwards (options' HIGH-BULL-conviction was a
  *fade* signal, Jul 18 audit) — a new qualitative ingredient earns trust from scored
  data, it isn't assumed to have it. Soft-fails to `None` if today's brief hasn't run yet
  (weekends, pre-9:15am) — never blocks a check.
- **Errors are loud now, with a real reason, not just present:** `_ask_claude` returns a
  specific error string per failure mode (`anthropic.RateLimitError` → `'rate limited'`,
  `APIStatusError` → `'api {code}'`, `APIConnectionError`, malformed JSON, missing key) —
  logged AND written to the DB (`error` column) rather than only logged, so a week of
  silent failures is countable, not just visible in a log grep.
- **Wired to all four books, one shared module:** `futures_trader.py` (IBKR NY) and
  `tc_trader.py` (TC NY) each build their own NY-shaped context dict; `london_trader.py`
  got a new `thesis_check` import + call inside `_monitor_position_locked` — since that
  module resolves `ACCOUNT_MODE` from whichever process threads it in, wiring it once
  there covers **both** IBKR-London and TC-London automatically. `session` column
  (`'NY'`/`'LONDON'`) added so a future join always picks the right trade-id table —
  NY trade_ids come from `futures_trades`, London's from `london_trades`, two separate
  autoincrement id spaces that must never be joined without it.
- **`weekly_review()` rewritten** to branch per session (separate `futures_trades`/
  `london_trades` join), report per-session check counts + REVERSAL_RISK counts +
  streak≥2 subset, and score accuracy against real exit price using the risk-score-
  derived verdict. NY and London have different signal profiles — blending them would
  hide which one the signal actually works for, if either does.

**Two real bugs caught by testing before trusting this for a week, not just reading the
code:** (1) forgot `session` in the idempotent-ALTER column list on the first pass —
caught immediately by a schema-inspection smoke test, fixed before it ever ran live.
(2) the JSON-schema call 400'd on first real API call — `minimum`/`maximum` constraints
on an integer property aren't supported by this API's `json_schema` output format
(confirmed live, not documented anywhere obvious); removed the constraint, kept the
0-100 range enforced by the prompt text, added a defensive `max(0, min(100, ...))` clamp
in code in case the model ignores it. Full smoke test after both fixes: real API call,
verified DB row (all columns populated correctly), verified streak increments 1→2 on a
second same-direction call for the same fake trade_id, test rows deleted after.

**Deliberately NOT wired into any backtest/sim harness** (`sim_replay.py`,
`london_v2_sim.py`, `equity_replay.py`) — a decision, not a gap, documented in
`thesis_check.py`'s own module docstring so a future session doesn't "discover" and
re-litigate it: (1) it makes zero trading decisions, so CONSTITUTION.md's "sim must
match live" parity requirement — which exists to keep *decision-affecting* logic honest
— doesn't apply here. (2) a live model call can't be backtested the way a quant signal
can: not free, not instant, not reproducible (model behavior next month won't match
today's), so replaying it against 2024-2026 bars would manufacture false precision
rather than real validation. Same treatment equity's Chart Gate/Thesis Check already
got. If this ever graduates toward gating a real decision, the validation path is more
weeks of live-scored data, not a backtest.

All 4 files (`thesis_check.py`, `futures_trader.py`, `tc_trader.py`, `london_trader.py`)
compiled clean, both traders restarted, both bridges verified connected. Still market-
closed (weekend) — first real Crest Watch data starts once a position both accounts hold
peaks ≥100pts profitable during a live NY or London session.

**Same night, follow-up: dashboard column + bug sweep (commit `0135edd`).** Added a
Crest Watch column to the futures open-positions table (latest risk_score/streak/
reasoning per position, "not yet checked" until one fires) — required fixing a real,
pre-existing, unrelated gap first: `get_futures_positions()` only ever queried
`futures_trades`, so any OPEN `london_trades` row was invisible in that table (only
ever showed up in the recent-activity feed). Now unions both, each row tagged with its
OWN session instead of being relabeled with the current wall-clock hour. Verified via
direct function calls + a temporary fake open position/Crest Watch row (cleaned up
after). Bug sweep on the wiring itself found one real (minor) issue: the RVOL/RSI/
context-building block ran on every monitor tick for every open trade regardless of
whether the position was even eligible for a check — only the throttle timer inside
`thesis_check.py` gated the actual API call. Added an eligibility pre-check at all
three call sites referencing the module's own `MIN_PEAK_PTS` constant, so cheap
indicator work only happens when a check could actually fire. All three services
(`futures_personal`, `futures_trader`, `dashboard`) restarted post-fix, verified clean,
bridges reconnected, `git status` clean.

**Dashboard split IBKR/TC (same session, follow-up ask).** `get_today_summary()`,
`get_pnl_by_book()`, and `get_scorecard()` in `dashboard/app.py` all previously blended
IBKR and TC together in at least one place (today-summary blended both NY sessions
outright; the 15-day chart's London leg had no account filter; the scorecard's 'London'
row blended both). All three now report IBKR and TC separately, each figure combining
that account's own NY + London leg — matches the summary cards (now 4: Equity/Options/
Futures·IBKR/Futures·TC), the 15-day stacked chart (`futures_ibkr`/`futures_tc` series),
and the scorecard (`IBKR NY`/`IBKR London`/`TC NY`/`TC London`, 4 rows instead of 3).
Open-positions table gets an Account column. Corrected a misconception while explaining
this: IBKR and TC are **not** "true copies except DLL/MLL" — Elephant Trade is IBKR-only
by design (never ran on TC), `MAX_DAILY_TRADES` is 5 (IBKR) vs 2 (TC, TopStep's
consistency rule), and the prop-rule *shape* differs beyond the raw numbers (TC has a
real trailing MLL + hard DLL + profit target + consistency check; IBKR has none of
those, just soft internal limits). Entry/exit trading logic itself is genuinely
identical. Dashboard restarted + verified (HTTP 302 to login, clean startup). The
`account_mode` column added to `london_trades` this session is now live on the real
`trades.db` (dashboard's own idempotent-ALTER safety net ran it independently of the
trader restarts, so the dashboard doesn't depend on restart order) — confirmed via
direct query: all 55 pre-existing rows correctly backfilled `'IBKR'`, zero data loss.
**Known gap still open:** `parity_check.py` and `expectancy_ledger.py`'s London queries
are still unfiltered by `account_mode` — same flagged item as above, not dashboard-facing
so lower priority, fix before trusting those specific reports once TC London has trades.

**`futures/bridge_projectx.py` built (same session) — TopStepX/ProjectX Gateway bridge,
NOT wired in.** User confirmed TC has been running against a second **IBKR** paper
account (DUQ640500, port 8002, via ordinary `bridge.py`) as a stand-in this whole time —
no real TopStep connection has ever existed. Prior research (closed before choosing
TopStep) had already identified the real path: `tc_trader.py`'s own header comment named
`bridge_projectx.py ←→ TopStepX / ProjectX API [to build]`. Built it now per user's
explicit ask ("build it, leave it dormant, re-hook once registered") — implements the
exact same REST contract `tc_trader.py`/`london_trader.py` already call (`/futures/order`,
`/futures/position`, `/futures/quote/{symbol}`, `/futures/cancel/{id}`,
`/order/{id}/status`, `/history/futures/{symbol}`), translating to real ProjectX Gateway
calls — endpoint schemas fetched directly from https://gateway.docs.projectx.com/
(Auth/loginKey, Order/place, Order/cancel, Position/searchOpen, History/retrieveBars all
confirmed with full schemas; Contract/available and order-status inference are marked
UNVERIFIED in the file's own docstrings — confirm against the real Swagger UI once
credentials exist, don't trust blindly). Reads `TOPSTEP_API_KEY`/`TOPSTEP_USERNAME`/
`TOPSTEP_ACCOUNT_ID` from `.env-tc` — none of which exist there yet, so the service starts
cleanly but reports `connected: false` until real credentials are added (verified: ran it
standalone on port 8099, confirmed `GET /` and `/connected` both respond correctly in the
"not configured" state). **`launch_futures_trader.sh`/`.env-tc` were NOT touched** — TC
still points at the DUQ640500/bridge.py stand-in on port 8002; nothing about current live
paper testing changed. Real-time quotes are a known gap (ProjectX's live feed is a SignalR
WebSocket hub, not REST — `/futures/quote` currently proxies off the most recent historical
bar instead, which is a materially worse quote than today's yfinance/IBKR path; fine for a
dormant scaffold, build the real SignalR client before relying on it for live entries).
**To go live:** add real credentials to `.env-tc`, smoke-test this file standalone first
(confirm real account balance/positions read correctly), THEN re-point
`launch_futures_trader.sh` at it and re-restart — do not skip the standalone smoke test.

**Dashboard glossary page + service-row gap (same session).** User asked several "what does
X mean / does it decide anything" dashboard questions and flagged re-asking them repeatedly.
Two fixes: (1) new `/glossary` page (`dashboard/templates/glossary.html`, linked from the
header) — reorganizes GLOSSARY.md's terms by where they appear on the dashboard, with the
same analogy style, and an explicit "decides vs watches" column answering the recurring
"does this auto-correct anything" question for Trade Cop (no — alerts only, human has to
act), Mirror Book (no — 100% paper, zero live orders), Crest Watch/Chart Gate/Thesis Check
(no — log-only). (2) Sector grades tooltip + section subtitle now state the real refresh
cadence explicitly (nightly 23:00 ET from trailing 30 days, NOT intraday) — this was already
true (`nightly_learning()`, unchanged), just not visible on the page itself before. (3) Found
a real gap while auditing the top-left service-health row: `futures_personal` and
`futures_trader` (TC) — the two services this entire session was about — weren't in the
health-check list at all, alongside their gateways/bridges (`gateway`, `tc_gateway`,
`tc_bridge`, `futures_collect_bars`). Added all 6. Dashboard restarted + verified (`/` and
`/glossary` both 302-to-login as expected, service dict confirmed 13 entries).

**Same night, two-round bug sweep + go-live:** found + fixed a real bug (pre-existing in
`futures_trader.py`, ported into `tc_trader.py` today): `log_block(..., f'pos={_overnight_position:.3f}',
...)` crashes with `TypeError` when `_overnight_position` is `None` — the COMPRESSION overnight-
day path never sets it before returning. Silently swallowed by the surrounding
`try/except: pass`, so no crash ever surfaced — just a permanent gap in `gate_blocks` OVN_SKIP
scoring on every compression day, in both files, since `compute_overnight_bias` was written.
Fixed in both. Also found: both underlying IB Gateway processes (`gateway`, `tc_gateway`) were
down going into tonight's restart — unrelated to code, would have silently blocked the whole
London session. Restarted, confirmed both bridges connected (DU9952463, DUQ640500), then
restarted `futures_personal`/`futures_trader` with everything live. Committed + pushed
(`a6fd51a`).

**TC `MAX_DAILY_TRADES` raised 2→5, same night, follow-up.** User asked precisely whether TC
checks the consistency rule and whether it "stops/exits" on violation. Confirmed from
`prop_rules.check_can_trade()`: yes it checks (only once `total_profit>=TC_DAILY_CAP=$1,200`
cumulative — skipped before that), but it only **blocks new entries**, never closes an open
position — that's a real correction, not just a confirmation. The code's own reasoning (already
in a comment) is why raising to 5 is safe even before the consistency check activates:
`TC_DAILY_CAP` ($1,200) is a separate, always-active gate that already caps any single day at
≤40% of the $3,000 target — under the 50% consistency bar by construction, independent of trade
count. `MAX_DAILY_TRADES` was never the thing protecting the account; it was only capping
upside. Shipped same night, TC restarted again, clean startup verified. Not backtested against
TC's exact 2-contract sizing/sequencing — reasoned from the real gate code, not from historical
replay; revisit with real TC data once enough accumulates.

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

## Regime + Entry Gates (updated Jun 28 2026)

### Changes applied Jun 28 2026:
- **Fix: `_scan_catalyst_override` dead code wired** — function existed but had zero call sites. Now called from CHOPPY, WEAK×1-2, and WEAK×3+ routing paths.
- **Intraday catalyst refresh (Path B):** `_scan_catalyst_override` now has two paths. Path A = pre-market gap ≥6% (original). Path B = intraday momentum ≥5% / intraday vol_ratio ≥3x / price above VWAP. Catches stocks not moving at 8:15am (e.g. MRNA/NUTX on Jun 26 which gapped flat but ran +11%/+9% during the session). Intraday signals fetched first (fast IBKR call); yfinance daily bars only fetched for Path A.
- **Dynamic catalyst upgrade in `_scan_and_enter`:** During NORMAL/CAUTIOUS scans, if a universe stock hits 5%+ intraday / 3x intraday vol / above VWAP but isn't in `catalyst_priority`, it gets added in-flight. The `is_catalyst` flag is then True for that scan cycle, enabling the CAUTIOUS/CHOPPY bypass in `grade_setup`.

### Changes applied Jun 2 2026 (backtest-validated):
- **Fix 1:** Catalyst stocks (is_catalyst=True) now bypass CAUTIOUS/CHOPPY regime block. Previously all entries blocked on CAUTIOUS. Catalyst = market-independent move (earnings/news). `grade_setup()` accepts `is_catalyst` param; `_scan_and_enter` computes it BEFORE calling grade_setup.
- **Fix 2:** Earnings date unknown + stock running >5% on 3x+ vol → allow (previously hard skip). Unknown earnings calendar = likely post-earnings gap (binary event resolved). Bears: still skip on unknown (gap-up risk).
- **SECTOR_ETF_MAP:** 8/11 sectors upgraded to data-driven ETFs (2yr correlation analysis Jun 2). Key: QUANTUM_CRYPTO QQQ→BITQ (corr 0.43→0.70), NUCLEAR NLR→URA, COMMODITIES GLD→GDX, SEMIS SMH→SOXX.

### ETF gate — decided NOT to build (Jun 2 2026 full backtest):
- Tested 4 modes: baseline / EOD gate / intraday 10am gate / position sizing
- Result: ALL ETF variants trail baseline ($626K). System's A+/A scoring already captures sector momentum.
- **Decision: do not add ETF gate to auto_trader.py.** SECTOR_ETF_MAP upgrade (Fix 3) is sufficient — it improves the nightly learner's sector grade benchmark, which is already live.
- `sim_today.py` updated (commit 2dbc087): catalyst bypass CHOPPY/CAUTIOUS now mirrors auto_trader Fix 1.
- `backtest_enhanced.py` deleted (commit d44879a). `backtest_strategy.py` ETF code removed.

## Bull Entry (NORMAL/STRONG regime)

Hard gates: earnings 0-3d | price < MA20 | vol low | gain < 3% | R:R < 2.5 | gap-and-crap | failed ORB
Catalyst gate: earnings unknown + running >5%/3x vol → allow (post-earnings catalyst)
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

## Entry Gates (updated Jun 30 2026)

Afternoon gate: no new entries (LONG or SHORT) after 12pm ET if morning realized P&L ≥ $150.
Data: afternoon LONG 44.8% WR / -$0.91 avg (vs 58.5% morning), afternoon SHORT 18.2% WR / -$6.56 avg.

STRONG-day exhaustion: STRONG regime + 8≤intraday_chg<12% → SKIP. N=41, 54% reverse from scan price avg -0.48%.
12%+ excluded (N=24, VELO +12.8% shows breakouts possible). No catalyst bypass (data shows exhausted catalysts still reverse).

5m RSI hard gate: `rsi_5m > 85` → SKIP (was -20pt penalty). Intraday blow-off top signal.
Different from daily RSI: daily 80+ = true momentum continuation. 5m 85+ = overheated right now.

FVG vol tier (Jun 30): `vol_ratio` minimum by price: ≥$100 → 2.0×, $20-100 → 3.5×, <$20 → 5.0× (was flat 5.0×).

Pre-market scanner (Jun 30 — was dead code since May 1):
- `PREMARKET_HOLD_PCT = 0.93` (was 0.97 — too tight, zero entries in 5 days)
- Dispatch bug fixed: `elif is_premarket_window(): run_scan()` in main loop
- Fires at 9:20–9:29am ET, max 2 positions, half size, 6% stop, limit orders (outsideRth)
- Gate tiers: gap ≥6% (≥$150), ≥8% ($50–149), ≥10% (<$50) | vol ≥200K | hold ≥93% of PM high
- Near-miss logging: hold 85-93% logs "near-miss" for threshold calibration

pvh ≤ -10% gate: PENDING (not yet built). Backtest N=3, all losers, zero FP. One-liner in grade_setup().

---

## Standing Rules

0. **CONSTITUTION.md governs all changes** (adopted Jul 18 2026) — hypothesis + auto-scoring +
   sunset date for every new rule; max 3 boolean entry gates per system; sim must match live
   code path/granularity; instrument-first-gate-later for new context sources; right-tail
   counterfactual required for exit changes. Read it before proposing any change.

0b. **GLOSSARY.md is the naming authority** (adopted Jul 18 2026) — one canonical name per
   concept (Trend Jury = hero gate, Black Box Recorder = decoder, Mirror Book = shadow fish-net,
   Weather Report = regime, etc.). New gates/books/nightly jobs get a glossary row in the same
   session they ship. Code identifiers, DB tables, and launchd names are never renamed.

1. **No tinkering mid-run.** May 1 2026 = Day 1. Parameter changes require data + explicit approval.
2. **Validate before build.** Always backtest first. Never suggest building without data.
3. **No mid-run changes** unless: (a) clear bug, (b) system crashing, (c) market condition system cannot handle.
4. **After any change:** `sim_today.py` replay immediately.
5. **Equity go-live timeline:** Jun 25% → Jul-Aug 50% → Sep full. Pre-flight checklist items must be done first (see checklist below).
5b. **Futures go-live timeline:** London paper Jun 17–Jul 17 (30 days) → Jul 17 real money at 25% capital. No tinkering Jun 11–Jul 9 (evaluation window). Do NOT compress this.

---

## US Market Holidays 2026

| Date | Holiday |
|------|---------|
| ~~May 25~~ | ~~Memorial Day~~ |
| ~~Jun 19~~ | ~~Juneteenth~~ |
| Jul 3 | Independence Day (observed) ← **next closed day** |
| Sep 7 | Labor Day |
| Nov 26 | Thanksgiving |
| Dec 25 | Christmas |

`US_HOLIDAYS_2026` set in `auto_trader.py` — `is_market_open()` and `is_premarket_window()` both check it. No orders possible on these days. Update for 2027 before year-end.

---

## Go-Live Pre-Flight Checklist (before June 25% capital phase)

These must be verified/built before any real-money trading begins. Do NOT go live until all are ticked.

| # | Item | Status | Notes |
|---|------|--------|-------|
| 1 | **Gateway reconnect simulation test** | ⬜ pending | Kill bridge mid-scan, confirm freeze Telegram fires, no orders placed. Manual test. |
| 2 | **Partial fill handling in place_trade()** | ✅ done May 29 | Reads `filled` qty + `avgFillPrice` from bridge. Partial fill → Telegram alert. Zero-qty fill returns None. |
| 3 | **IBKR market data subscriptions** | ✅ confirmed Jun 10 | Live data already active on paper account (reqMarketDataType=1, not delayed). Subscription is per-user at IBKR — carries over to live account automatically. |
| 4 | **Buying power pre-check** | ✅ done May 29 | Pre-order BP check vs position_cost×1.05. Fails open on account query error. Paper account ($3.5M) never triggers. |
| 5 | **TFSA isolation double-check** | ✅ confirmed | Bridge pins IBKR_ACCOUNT on every order. Individual account already identified and saved. |
| 6 | **PROD_EQUITY_ENABLED flag test** | ⬜ pending | Flip flag in dry-run, confirm orders reach paper Individual account, not TFSA. Flag infrastructure already in auto_trader.py:4104. |
| 7 | **Prod `.env` credentials audit** | ⬜ pre-go-live | User to audit before deploy — never automated. |
| 8 | **watchman.py exit logging** | ✅ wired | log_trade_outcome() present. Options-side only — equity go-live not blocked. |
| 9 | **backtester_options.py Phase 5** | ⬜ post-go-live | Options-side item. Equity go-live not blocked. |
| 10 | **Prod gateway launchd bootstrap** | ⬜ pre-go-live | Re-enable before go-live: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.sushil.trading-prod.gateway.plist` |
| 11 | **Bridge streaming subscriptions** | ✅ done May 29 | reqMktData after every place_order (commit f2cd105). Prevents stale monitoring on intraday entries. |
| 12 | **Float gate for scanner stocks** | ✅ done May 29 | float < 500K shares → skip for IBKR scanner-discovered stocks not in 166-symbol universe. |

---

## Known Issues (Active)

| Issue | Status |
|-------|--------|
| watchman.py exits not logged | Wire log_trade_outcome() |
| backtester_options.py Phase 5 | After first paper trade |
| Short side WR gap (50% vs 77% long) | Monitor 60-day window; 3-scan fix + DNA modifiers now in place |

## Changes Applied May 28 2026 (fine-tuning session — 136 trades, 2 months data)

| Change | Details |
|--------|---------|
| Burst timing scoring | Fresh burst 30-90m = baseline; aging 90-150m = -10pts; stale >150m = -20pts; 2-4 consec new highs = +10pts; 1 consec = -5pts; 0 consec = -10pts. Short side: stale >150m = -20pts, aging = -10pts |
| Afternoon gate fix | Now uses `peak_session_pnl` (realized + unrealized) instead of realized-only; would have blocked 6 junk entries today saving -$97 |
| Recycled slot gate | No new longs/shorts after 12:30 if any slot was vacated today. Data: 15.4% WR / -$11 avg → +$145/May, ~+$1,743/yr |
| **Power-play batting order** | Slot selection now ranked (not first-in-scan-order). Sort: tier (sympathy→catalyst A+→catalyst A→universe) → sector strength → `intra_chg` DESC → `vol_ratio` DESC → score. CONSUMER sector last. Data: catalyst flag predicts top movers 2×; `intra_chg` at scan time is the pitch report. scan_log now records "Slot #N in batting order" for top-5 vs "awaiting slot" for rest. |
| ENERGY blocked for shorts | 0% WR, 4 trades, -$17.73 avg — sector fully blocked on bear side |
| Restart resilience | `peak_session_pnl` + `_morning_pnl_snap` restored from trades.db + live portfolio on startup; no more broken afternoon gate after mid-day restart |
| HOD capture | `hod_at_entry` written from `bars_5m` on every new trade entry (best-effort, non-blocking) |
| Options: PendingSubmit/Unknown/Cancelled fix | Portfolio check confirms fill before DB write on all pending states |
| Options: perpetual re-queue fix | Suggestions now expire after 15 min instead of looping forever |
| Options: OPT STATUS / OPT POSITIONS | Live uPnL per position from bridge added to both commands |
| Texture gate — refined design | May 28 confirmed choppy (SPY ORB 0.16%, drift +0.01%) but CATALYST stocks ran strongly. **Do NOT build blunt 5→3 cap.** Correct design: catalyst entries always 5 slots; ambient scanner capped at 3 on choppy days. Needs re-backtest before building. |

**Data note:** Texture gate NOT being built yet — needs re-backtest with catalyst-exempt split logic. $168/60d result was blunt version; refined version unknown.

---

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
venv/bin/python backtest_strategy.py        # bull edge (daily bars, 5yr)
venv/bin/python backtest_bear.py            # bear edge (daily bars, 5yr)
venv/bin/python backtest_walkforward.py     # OOS walk-forward (8 windows)
venv/bin/python backtest_stress.py          # crisis periods
venv/bin/python monte_carlo.py              # ruin risk
venv/bin/python batch_backtest.py           # full suite for new candidates
venv/bin/python dna_analysis.py             # re-cluster universe (quarterly)
venv/bin/python collect_bars.py --summary   # equity 5-min bar counts and date ranges
venv/bin/python futures/collect_bars.py --summary  # futures bar counts (MNQ/ES/RTY)
venv/bin/python backtest_scalp.py           # OPT_SCALP Mode A backtest
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
- watchman.py: launchd-managed (KeepAlive=true), always running — no manual start needed

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

---

## Aug 15-16 2026 — Alpha Factory pivot (branch `alpha-factory`) — see NEXT SESSION PRIORITY (top of file)

Multi-day pivot from single-strategy tinkering to a **factory** that manufactures / validates / retires
edges — judged on Sharpe not peak return, everything scored on market-neutral ALPHA so nothing passes by
riding the bull tide. Built `factory/` (contracts → data → engines → Proving Ground gate → Captain →
Fill Desk → Lookout) + a live layer (`factory/live/wave_rider.py` shadow trader, `lookout.py`) + dashboard
`/factory` page (architecture diagram + live Roster/fleet/scan-funnel). Two uncorrelated Roster engines
validated on 2.5yr: Wave Rider (momentum·wild, live-paper SHADOW) + Contrarian (market-neutral reversal,
sleeve). Gate honestly rejected 5 (Bargain Hunter, low-vol, PEAD/Earnings-Drift, XS-momentum, tide-throttle)
and caught a fatal lookahead bug (Sharpe 5.3→1.2) via property tests. Fish Finder + equity bear book
DECOMMISSIONED. Full trail: `docs/ALPHA_FACTORY_DESIGN.md`, `alpha-factory` + `equity-swing-edge-found-aug14`
+ `equity-regime-diagnosis-aug14` memories. Savepoints: git tags `checkpoint-2026-08-15-alpha-factory-v1`/
`-v2`/`-factory-dashboard`. PARKED Sun Aug 16, resume Mon Aug 17 (free 2022 bear stress-test + watch Wave
Rider's first shadow scan + branch-merge decision).

---

## Aug 16 2026 — Options challenged to the ground: NO own edge → freeze + "Turbo" design (factory→options bridge)

User asked to challenge the whole options system: mechanism works (takes/exits trades) but
candidate selection loses. **Deep-dive (scored all 2,089 `opt_calc_log` rows vs the
underlying's real forward path, yfinance daily — same proxy as the Jul 18 audit): options has
NO tape-independent selection edge.** Every apparent edge (verdict gate, IV routing, news
conviction, signal_count) dissolves when the month is held constant — it was May's
momentum-beta + composition (469 of 498 all-time ENTERs were in May; ENTER term −23% in June).
Bull hit-breakeven decayed 90→74→28→25% May→Aug on UNCHANGED logic = long momentum-beta that
died after May (same disease as [[equity-regime-diagnosis-aug14]]). Two mechanically-real
survivors: time-to-breakeven median 15-27d (theta eats debit spreads before direction pays),
and IV rank <25 reliably bad every month. **Groq/Llama is NOT gone** (GROQ_KEY set,
news_engine.py runs 70B→8B→Haiku) — it was decoupled from trading on Jul 19 (Ghost Ledger
only). Lifetime closed options P&L −$4,502; realized post-rebuild is bear-puts-win /
bull-debit-loses, matching the Jul 18 audit.

**SHIPPED (operational, user-approved): equity-echo entry freeze.** `EQUITY_ECHO_FROZEN=True`
(durable code constant, NOT the transient `_paused` — survives restarts) gates both auto-entry
paths (`_check_equity_scan_triggers` + `scalp_scan_loop`). Watchman EXITS untouched (the 4 open
positions #24-27 run normal exits); news→Ghost-Ledger logging untouched. Reversible. Restarted
+ verified.

**DESIGN written for approval (NOT built): `docs/OPTIONS_FACTORY_BRIDGE_DESIGN_2026-08-16.md`
— "Turbo".** Options rebuilt as a leverage/structure execution transform on a
Factory-VALIDATED engine (Wave Rider = first client; Contrarian is a sleeve, not option-able),
NOT a signal source. Core = **Edge-Budget gate** (the options `MIN_RR` that was missing:
E_move ≥ (BE_move+Carry)×MARGIN) + IV-routing rule (SKIP<25 / debit-call-spread 25-65 /
bull-put-credit >65) + 2-4wk DTE for the 3-day hold + max-loss sized to the engine's $160/slot
risk + exit mirrors the engine. LEAP reserved for a future long-horizon engine. Ships
SHADOW-on-shadow first (`options_shadow` table, mark to live chain), scored on **leverage
premium net of carry** vs holding shares — design explicitly allows the honest conclusion
"options adds nothing to a +0.5%-alpha engine." Equity-echo + news retired as direction.

**TURBO BUILT + LIVE-SHADOW (same session).** `factory/live/turbo.py` + `options_shadow` table
+ launchd `com.sushil.trading.turbo` (SHADOW, 5-min, mirrors wave_rider; loaded + verified clean,
0 tickets — Wave Rider's first shadow picks land Monday). Turbo plans an options structure on each
Wave Rider `wave_trades` ticket, marks to live chain, scores **leverage-premium vs shares**.
**Edge-Budget gate upgraded** from the doc's crude "median move clears breakeven" to **convex
expected value**: reprice the real structure (BS `engine._bs_spread_vals`) at day-3 across Wave
Rider's 6,481 real WILD `cl3` outcomes (8% stop modeled); gate = EV/risk ≥ 0.10 + IV≥25.
Validated at build: discriminates (cheap-IV OTM debit +18% PASS vs rich-IV ATM +2% SKIP); the
IV>65 credit route is NEGATIVE-EV on a momentum engine (wrong convexity — gate refuses it). Design
doc §4 updated. Nothing places orders (SHADOW). **Monday: watch `logs/turbo.log`.**

**VALIDATION DONE Aug 16 (both asks):** EQUITY SIZING **sound** (risk-based dual cap
`shares=min(capital/price, MAX_LOSS_PER_TRADE/risk_per_share)`, side-correct, all 4 paths).
OPTIONS CALCULATORS **math correct** (debit/credit/scalp/LEAP formulas verified; live arithmetic
re-check Monday since calcs hard-abort w/o IV rank = bridge). OPTIONS SIZING **redesigned** (user
call): `_auto_qty_calc` now sizes on true MAX-LOSS to a single number
`OPTIONS_MAX_LOSS_PER_TRADE = 10% of pool = $500` = both sizing target AND per-trade cap, for
debit (premium) AND credit (margin); **credit spreads now auto-size** (were stuck at 1×);
per-trade risk cut $1200→$500. Bug fixed: debit/credit calc **key inconsistency**
(`max_loss_$` vs `max_loss`) that bit turbo.py. options_trader restarted, unit-tested. Full trail:
[[options-edge-dive-aug16]]. Turbo dashboard card also built on /factory (picks + strike ladder).

**NEXT (options) BUILD/VALIDATION:** design doc §10 (own-strike EV-max v2, extend
`collect_chain_snapshots.py` for shadow marks) + the two validation asks above. See
[[options-edge-dive-aug16]].

---

## Aug 16-17 2026 — Futures put on the Factory bench (IBKR + TC) — clarity on TopStep confidence

User asked to deep-read both futures traders (IBKR `futures_trader.py` + TC `tc_trader.py`)
ahead of hooking TC to a real TopStep $50k combine in ~2 weeks — wanted an honest read on
"are we good / scope to improve / appetite for more contracts / wire to the factory?" Code
unchanged since ~Jul 26; nobody had ever asked *does the automated book have a real OOS edge,
or is it the user's manual hand?* Built a futures-side **Proving Ground** applying the equity
factory *doctrine* (OOS / strip-manual / right-ruler / prop-realistic), NOT its cross-sectional
code (single instrument MNQ). Full trail: [[futures-alpha-factory-aug16]].

**Built (`futures/factory/`, offline — no live orders):**
- `bench.py` — drives the validated `sim_replay` at live-parity SIM_FLAGS over 5.5yr MNQ bars
  (2021→2026). Per-engine attribution, per-year OOS, PASS/WATCH/FAIL, **TopStep gauntlet**
  (DLL$1k / trailing-MLL$2k / consistency-50% simulator + day-bootstrap → P(pass)/P(blow)),
  give-back/MFE peak-exit analysis, rev-exit sweep, green-light validation. Scoring the sim =
  manual-free by construction. `FRICTION_PER_CONTRACT=6.0` conservative haircut baked in.
  Modes: (default scorecard+gauntlet), `--giveback`, `--rev-sweep`, `--green-light`.
- `calibrate.py` — matched sim↔live trade comparison (execution) + day-availability.

**The live-data reality first (both accounts, RECONCILED/partials excluded):** the two accounts
are NOT the same system. IBKR NY **automated** ≈ breakeven (−$68/54t); its whole realized edge
(+$3,696/14t, 100% WR) was the user's **manual FUT CLOSE** hand on 2 trend days (Aug 3-6). TC NY
automated ≈ breakeven too (+$114/86t). IBKR-only engines (Elephant, PM_LONG) don't run on TC, so
TC — unwatched, pure automated — is the honest preview of TopStep, and it's flat. TC's biggest
lifetime leak (VWAP_LONG −$979/49t/39%) was mostly **pre-Jul25** (the give-back era the Reversal
Exit was built to fix); post-Jul25 too thin to confirm.

**Five bench findings (measured, not believed):**
1. **Sim over-states edge, but the gap is SELECTION not EXECUTION.** 2026 sim +$9,327 vs live
   ~breakeven — yet on the 15 trades sim+live both took, per-contract P&L is comparable (no fill
   inflation; entry slippage even favorable). The gap = trades the sim takes that live never did.
   Only 15 clean matched pairs exist → **can't empirically calibrate fills yet** (user's "reality is
   weeks old" point, confirmed); using $6/contract friction until the hands-off weeks accumulate pairs.
2. **PEAK-EXIT is NOT a fixable leak.** Rev-exit sweep (2026): live `2,0.30,120` WINS ($8,421,
   Sharpe 4.19); every faster/tighter exit loses monotonically (aggressive 1,0.20,80 → $3,317).
   Give-back/MFE: of 57 trades hitting a +100pt peak, only **2 (4%) round-trip to a loss** — 55/57
   end green. Give-back = right-tail premium, not capturable. Confirms Jul-25 on fresh data. User's
   "stop giving back → good PL" disproven for a *systematic* rule (their manual wins = human selective
   reversal-reading, mechanizable only via Crest Watch). Only real exit leak: **ORB_LONG keeps 1% of a
   112pt peak (n=7)** — targeted dig, not yet done.
3. **"More trades/day" has NO juice — signal scarcity, not the cap.** Daily cap 2 vs 8 nearly identical
   in BOTH 2026 (180... 126t; +$8,552 vs +$8,421) and the 2022 bear year (180 vs 182t) — raising the
   cap adds 2-3 trades that lose. System generates ~2 quality signals/day by design (A+-only + hero +
   RVOL gates). The live "trade #3+ = 100% winners" was pure manual-trend-day contamination.
   **Green-Light Extender verdict: DON'T WIRE IT** — the doctrine avoided plugging a bad idea into the
   eval account.
4. **⚠️ THE BOOK IS REGIME-DEPENDENT AND NET-NEGATIVE EX-2026 (the decisive TopStep finding).**
   Full 5.5yr per-year (friction-adj): **2021 −$1,138 / 2022 −$1,615 / 2023 −$1,314 / 2024 −$2,480 /
   2025 +$1,480 / 2026 +$8,421.** LOST money in 4 of 6 years; the entire lifetime edge is 2025-2026,
   and 2026 is a massive outlier. Strip 2026 → −$5,067 over 2021-2025. Same disease as
   [[equity-regime-diagnosis-aug14]]. **TopStep gauntlet over the full-history distribution:
   P(pass $3k)=8.5% vs P(blow $2k trailing MLL)=29% — you blow ~3.4× more often than you pass; the
   real historical ordering BLEW the MLL on day 75.** Only reason it looks good now = we're in the
   favorable 2025-26 regime.
5. **Bootstrap P(pass) is path-optimistic:** 2026 real ordering BLEW the trailing MLL on day 34 while
   shuffled bootstrap said 88% pass (bootstrap breaks losing-streak autocorrelation).

**Engine verdicts — CORRECTED by full history (2026-only was misleading):** over 5.5yr only
**PM_LONG survives as a marginal PASS** (Sharpe 1.10, +$10.7/t — but negative in 2022, ~0 in 2024;
regime-dependent too). **ORB_SHORT, which looked like a co-carrier in 2026-only (+$2,060), FAILS over
5.5yr: −$1,733/318t, Sharpe −0.44, MaxDD −$5,544** — its 2026 profit is a regime artifact. Everything
else WATCH/THIN. Textbook case of why OOS/multi-year matters over a single favorable window.

**Scaling answer (all three levers the user imagined — dead):** NOT more contracts (DLL math caps at
2: 200pt stop × 2c = $800 < $1k DLL; 3c breaches), NOT more trades/day (signal-scarce), NOT tighter
exits (already optimal). The only honest lever is finding whether the automated book has edge at all
once calibrated — and adding a **defensive/bear leg** so a 2022-type regime doesn't blow the account.

**Shipped:** **Elephant PARKED** (`ELEPHANT_ENABLED=False`, futures_trader.py:247) — too infrequent
to ever prove (9 trades/2026 −$175, ~4/yr), IBKR-only asymmetry vs TC. Code kept, revertible.
Restarted futures_personal, clean startup verified (RVOL loaded, London enabled, scheduler up; bridge
showed disconnected = weekend gateway down, unrelated). TC never had Elephant, untouched.

**CONFIDENCE VERDICT: real TopStep in 2 weeks is a LOSING BET on the evidence — do not fund.** The
full-history gauntlet is 8.5% pass vs 29% blow. The book only works in the current 2025-26 regime and
loses in 4 of 6 years; there is no proven all-weather automated edge, and no scaling lever exists
(contracts DLL-capped at 2, trades signal-scarce, exits already optimal). What would change this is
NOT more backtesting of 2024-2026 (already used) — it's (a) a genuinely new DEFENSIVE/bear engine so a
2021-24-type regime doesn't blow the account, and (b) TC run hands-off proving positive automated
expectancy forward without the manual overlay. Until then TopStep = paper only. See
[[futures-tc-readiness-aug9]], [[futures-alpha-factory-aug16]].

**ENGINE HUNT (Aug 17) — factory discovery half built (`futures/factory/engines.py`) + verdicts.**
Factory now COMPLETE for futures (grader `bench.py` + pluggable discovery `engines.py`; a new engine
= a spec that hunts through the same gauntlet). Candidates: (1) `stretch_fade` mean-reversion — DEAD
(−$62k, neg every year; single-instrument intraday fade has no edge — equity mean-rev is
cross-sectional). (2) `bear_breakdown` defensive short leg + daily-50MA-downtrend filter — anti-
correlated (best years 2022/2024 = momentum's worst), but SHELVED: on the gauntlet it doubles pass
yet quadruples blow (annual diversification doesn't survive daily-path prop rules).
**THE CLEAN WIN — daily-downtrend filter on the EXISTING momentum book (stand aside on down days):
P(blow) 29%→11.5% (halved), 0 DLL days, blow pushed day 75→131, pass ~flat. Low-overfit one-line rule
that fixes the real disease (book bleeds trading into daily downtrends) — WORTH ADOPTING on live after
forward-val; would go to BOTH futures_trader.py + tc_trader.py.**
**STRUCTURAL VERDICT: NO config is fundable on $50k TopStep** — best (filtered momentum) still
P(blow)11.5% > P(pass)8.7%. ~$12/day mean can't outrun a 4% trailing MLL; more engine-hunting won't
fix the arithmetic. **$50k/$2k-trailing rule set is structurally hostile to this strategy** — a larger
account / looser %-trail / different prop structure suits it far better. See [[futures-alpha-factory-aug16]].

**Open/next:** (a) forward-validate the daily-downtrend momentum filter (the one shippable win) before
wiring; (b) empirical fill calibration over hands-off weeks; (c) chop-year (2021/23) leg — but note it
won't fix the structural pass-rate wall for TopStep; (d) any live logic change goes to BOTH
futures_trader.py + tc_trader.py. ORB_LONG pathology dig + Green-Light shadow: both shelved per bench.

---

## Aug 19 2026 — Sizing thesis TESTED AND REJECTED (`futures/factory/sizing_lab.py`, no live change)

Continued the Aug 18 thread. Full write-up `docs/FUTURES_SIZING_VERDICT_2026-08-19.md`,
output `futures/factory/_out/sizing_lab_2026-08-19.txt`, memory [[futures-sizing-verdict-aug19]].
Reused caches (recovered the prior session's `mom_mtf.csv` = full pipeline on MTF-filtered
days into `futures/factory/`). **Nothing wired; recommendation pending user decision.**

- **ATR-normalised sizing FAILS.** Lifts the barrier design ($/DD 2.24→3.19), CUTS the real
  exit stack (4.46→2.87) — with a fixed 200pt stop dollar risk/contract is ALREADY constant.
  Walk-forward peaky (train-opt $600 ≠ test-opt $700). Decisive control: ATR terciles WITHIN
  each year → 2.67, worse than flat-2's 3.18 ⇒ the gain was the ATR 189→455 **time trend**.
- **Room as a SIZE input FAILS** (flat mean and sd across R1-R5; $/DD 3.19 vs flat 3.18).
  Third independent rejection of room.
- **The Aug 18 barrier geometry LOSES to the live exit stack** on the same 343 entries,
  1 position, 1c: real stack +$4,260/DD −1,303/**3.27**/Sh 1.78 vs barrier +$3,472/**2.08**/0.97.
  No-stop = 1000pt = 600pt brake are byte-identical ⇒ the wide brake never fires.
  **"green 6/6" was slot double-booking** — one-position-at-a-time drops 343→277 trades and
  flips 2025 negative ⇒ 5/6 (and MA100 variants ARE green in 2022, so it was parameter-dependent).
- **SURVIVES: the daily-trend day filter.** MTF-LONG under the real exit stack n=343
  **+$7,311 / DD −1,640 / $/DD 4.46 / Sh 2.38** (2021 +333 / 2022 −683 / 2023 +1,744 /
  2024 +1,330 / 2025 +846 / 2026 +3,742). Real plateau (MA50+rising 3/5/10d = 4.34/4.46/4.38,
  MA100 4.89-5.08, MA20 too fast, MA200 too slow). **Day-level pipeline-cleanliness now
  VERIFIED** — filtering the cached full book reproduces the dedicated run exactly, 0 diff.
- **⚠️ TRAP RECORDED:** scoring the book against "hold 1 long 10:30→15:10 on days it traded"
  gives alpha −$4,182 — a **tautology**. That benchmark is lookahead; buying 10:30 on ALL
  MA50-up days LOSES $2,609; **79% of the day's move happens BEFORE entry**. Against the
  deployable benchmark (hold from the real entry) **the exit stack is a net POSITIVE**.
- **Conviction sizing (hero ladder, already live) is the only sizing input with signal** —
  MTF-LONG sized-2 +$42/contract vs +$9, 6/6 years, beats 97.9% of permutations at identical
  average exposure — **but it is noise on the full 949-trade book (82.8%)**. Suggestive only.
- **LIVE CHECK (Jun 5-Aug 17, manual FUT CLOSE stripped — automated book = −$1,100/145t):**
  the rule = **−$105/50t**, worst day −$1,510→−$981, **DLL breaches 2→0**, trading days 43→16.
  **Risk-reducing, P&L-null.** Sim says 2026 +$3,742/34t — live/sim divergence inside the same year.
- **Forward horizon: 12 months** (book trades 4.5 active days/mo; p10 stays negative until then).
- **RECOMMENDATION:** wire the day filter LOG-ONLY on both traders; wire nothing else. The
  bigger open item is still the **duplicate-entry gap** — every backtest describes a
  1-position book while the account trades a 2-position one.

---

## Aug 19 2026 — What-if: wide stop + 1 contract + time-based exit (`futures/factory/wide_stop_lab.py`)

User's design tested end to end: **1000pt SL every trade, 1 contract, existing entries (NO MA50
filter), same on IBKR NY / TC NY / London, exit derived from data, flat same day via a time rule.**
Doc `docs/FUTURES_WIDE_STOP_WHATIF_2026-08-19.md`, memory [[futures-wide-stop-whatif-aug19]].
**Nothing wired. Verdict: do not wire — it fails on the tape, not on tuning.**

- **REUSABLE**: cached sim entries are valid at ANY stop width. `hero_score.contracts_from_regime_score()`
  skips on the SCORE alone; `calc_contracts_result` only caps the GOLD tier at `min(2,cc)` — widening
  the stop takes cc 2→1 and **removes no entries**. No 45-min re-run needed for stop-width studies.
- **The tape kills the target.** Median MFE **59pts** (MAE median 61 — bigger). Reach-before-−1000:
  +50 57% · +100 29% · **+150 15%** · **+200 8%**. "Entry right 61%" only holds at a low bar —
  MFE-beats-MAE is **51.3%**, a coin flip.
- **The time rule is the WORST lever tested.** Best cell in the whole target×time grid is **no target
  and no time rule** (hold to close, +$2,202). 30m −$4,812 · 60m −$1,437 · 180m −$1,461; clock-time
  worse (flat-by-12:00 −$12,139). **Control with the trade set fixed at 944 confirms it is the RULE,
  not freed-slot churn** (hold +$2,874 vs 60m −$1,382). ⇒ "not at target by X ⇒ entry was wrong that
  day" is **disproven** — futures intraday trades recover by the close (OPPOSITE of the equity
  stall-recycling finding). The one good cell (cut 60m if worse than −100, +$6,378) is a **spike** —
  neighbours halve or flip sign, and **every cell in the neighbourhood is 3/6 green**.
- **Narrower beats wider.** Hold-to-EOD stop sweep: 1000pt +$2,202/$/DD 0.34/worst day −$2,007 ·
  850 +$2,802 · **625 +$3,702/0.74/−$1,257 (best)** · 500 +$3,198 · 200 (live) +$1,346/0.35/−$814.
  Stop fires **1 in 472** ⇒ decoration. **The widest stop is the worst member of its own family.**
- **"risk ≤ MLL" is NOT met.** 1000pt × $2 = **$2,000** = 100% of TC's trailing MLL (breaches with the
  $300 buffer) and **200% of the $1,000 DLL**; IBKR soft DLL $1,250 = 625pt. A single-trade risk bigger
  than the DLL means the daily halt **cannot** protect the account. Gauntlet: P(pass) ~10% vs
  **P(blow) 35-43%** — fifth independent route to the same TopStep verdict.
- **London: every variant loses** (−$2,252 … −$7,895); MFE median 40pts and the 1000pt stop is touched
  by **0.0%** of paths (confirms Jul 18 — London's edge IS the BE=0.10 armour).
  **⚠️ NEW, separate: `london_v2_sim` charges NO commission and NO slippage** (verified in source).
  Champion 5.5yr = +$3,851 / 2,496 trades = **+$1.54/trade** → less commission only +$756;
  **less 1pt slippage −$4,236; less $6/c −$14,220.** London does not survive its own costs — check
  this before treating London as a live book at all.
- **Closest workable version**: 625pt stop / no target / no time rule / hold to close / 1 contract —
  +$3,702, $/DD 0.74, **green 3/6** (2021 −1,454 · 2022 +2,440 · 2023 −1,530 · 2024 +825 ·
  2025 −1,370 · 2026 +4,790). Beats the live stack at 1c (+$938) but still loses half its years and
  its worst day exceeds the IBKR DLL. Carry-forward: **625pt > 200pt for a HOLD-TO-CLOSE book** — a
  different design needing its own validation, not a parameter change.

---

## Aug 19-24 2026 — NY futures redesign: pipeline-confirmed candidate (NOTHING WIRED)

**📌 START HERE: `docs/FUTURES_CATCHUP_2026-08-24.md`** (full pack + paste-ready prompt).
Memory entry point [[futures-catchup-aug24]]. Labs/caches in `futures/factory/`.

**⚠️ GOVERNING CODE FACT — read before proposing any futures change.** `grade_entry()` is
**ONE additive score per side** (SHORT needs `any()` of the bear bundle, then +20 orb / +15
vwap_rej / +10 mom / +10 open / +25 pm; A+ ≥80). **`setup_name()` is only a naming-priority tag
applied AFTER the entry decision.** There are NOT 9 strategies — one LONG, one SHORT. Proof:
zeroing `sig['orb_bear']` removed only 9 of 276 shorts; **235 relabelled to VWAP_SHORT**.
⇒ The only real levers are: remove a **signal**, remove a **side**, or raise the **threshold**.

**Scope of a change:** `futures_trader.py` → IBKR NY only · `tc_trader.py` → TC NY only ·
`london_trader.py` → BOTH accounts' London · `sim_replay.py` → backtest only. **IBKR and TC NY
are DUPLICATED code** (`get_signals` differs by one dead variable `last3v`). **Any real change
is 3 files.** London is a separate IB-range-break signal, untouched by NY changes.

**Pipeline-confirmed candidate** (5.5yr, real `_run_scenario`, $6/contract):
**1000pt stop / 1 contract / one position at a time / no trail / no rev-exit / no no-move /
LONG ONLY** → n=493, **+$5,520**, maxDD **−$3,253**, **0 TC blow-ups**, **green 4/6**
(2021 −59 · 2022 +1,568 · 2023 +1,375 · 2024 +249 · 2025 −1,223 · 2026 +3,610).
vs LIVE TODAY (200pt, 2c, 2 open): +$3,353 / −$7,715 / **6 TC blows** / green 2/6.
⇒ +65% P&L, −58% drawdown, blow-ups 6→0. **~1.5 trades/week** (1.3-1.7 every year).

**Settled — do not re-litigate without a new mechanism:** wider stops lose (1000pt = −$1,142
over 5.5yr; 625 > 850 > 1000); **1200 ≡ 1000 byte-for-byte**, and **any stop >1071pt yields ZERO
trades** (MIN_RR 1.4 vs the 1500 target) unless the target is scaled; the wide stop fires 1 in
378 (inert insurance); "slow the trail" is **non-monotonic — only ZERO works** (2×/3× are worse
than live); time-based loss-booking is the worst lever tested; ATR-normalised sizing is a
time-trend artifact; room fails as a size input (3rd rejection); VWAP_LONG is **not** a killer
(best $/trade in the sim).

**Live state through Aug 21:** automated book **−$3,080**/149 trades (IBKR −$1,787, TC −$1,292;
the positive headline is +$4,209 of manual `FUT CLOSE`). **SHORT −$2,198 vs LONG −$882.**
August automated −$2,617 of which **SHORT is −$2,235 (85%)**; the new config over the same 15
days = **6 trades, +$232**. **Aug 21 (Fri) is the worst live day ever: −$1,979** — 4 trades, all
SHORT/ORB_SHORT, duplicate pairs ~1 min apart on both accounts, IBKR −$1,270 breaching the
$1,250 soft DLL. All five worst live days are 4-5 trade duplicate clusters (but Aug 7's cluster
MADE +$1,168 — a variance amplifier, not a uniform loss).

**TC comparability:** entry logic aligned `72b3bb5` (Jul 25 2026); sizing + daily cap only
`a6fd51a`/`463680f` (Aug 9 2026) ⇒ TC is fully comparable for ~**7 trading days**. Older TC
numbers blend three different systems.

**Open decisions:** (1) ship **LONG-ONLY** (strongest independent evidence; one-line per file,
same pattern as `pm_bear`) as a log-only shadow or live; (2) the exit rebuild; (3) **duplicate
entries** — `MAX_OPEN_TRADES` 2→1 and/or a post-ENTRY cooldown; a real bug, flagged by the
parity cop twice (Jul 15, Jul 20) and never root-caused; (4) **UNEXPLAINED**: live fires a very
different setup mix than the sim (VWAP_LONG 5% of sim vs 32% of live, 1 vs 26 trades in the same
17 days) — until root-caused, live setup-level P&L cannot judge a setup.

---

## Aug 24 2026 — NY futures: 3 fixes SHIPPED LIVE + regime-flip exit rejected (3rd time)

**LIVE NOW on both `futures_trader.py` (IBKR) and `tc_trader.py` (TC)** — restarted 22:08 ET,
clean startup verified, 0 errors, bridges 8000/8002. Full trail: [[futures-regime-exit-aug24]],
lab `futures/factory/regime_exit_lab.py`.

1. **`get_regime` forming-bar fix.** It read `df5['close'].iloc[-1]` — the *unfinished* 5-min
   candle — for price/VWAP/RSI/5-bar trend, so the label flickered within a bar and reset the
   consecutive-same-regime confirmation streak. **Third and final instance of this bug class**
   after `calc_session_rvol` (Jul 17) and `calc_htf_trend` (Jul 18). New `df5c` drops the
   forming bar. **`calc_session_rvol` deliberately keeps the UNTRIMMED frame** (it trims
   internally; passing `df5c` would drop two bars). Unit-tested.
2. **`MAX_OPEN_TRADES` 2 → 1.** `sim_replay.py:1127` has ALWAYS modelled one position, so every
   Sharpe / MaxDD / P(blow) figure in this program understated live risk ~2×. Live now matches
   the validated book. **Contracts unchanged (still 1-2)** — 1 pos × 2 ctr = 2 MNQ, was 4.
3. **`ENTRY_COOLDOWN_MINUTES = 2.0` (new constant + `_last_entry_time`).** `COOLDOWN_MINUTES`
   only counted from the last EXIT, so the 60s scan loop re-fired a still-valid signal on the
   next scan. Live gaps between consecutive same-side entries: **median 1 min, 57 of 73 were
   <2 min and lost −$832**; every genuine re-entry was ≥2 min away.

**London deliberately NOT changed** — verified empirically that it has never opened an entry
while another was live (min gap 4 min; `place_london_trade` guards on `_position is not None`).
No `parity_check.SIM_FLAGS` change needed — sim was always a 1-position book, so parity improves.

**Regime-flip exit — REJECTED, and this time the sensor was not the excuse.** The gap it targets
is real: live ledger shows *every* adaptive exit profitable (trail +$5,901 · rev_exit +$1,922 at
100% WR · no_move +$519) and the whole loss in trades that never earned one (real stop −$5,988 ·
circuit breaker −$5,458), because Reversal Exit requires peak ≥120pts first.
**But the regime label is `NORMAL` 85.5% of bars** (STRONG 7.3 / WEAK 7.2) — it is an *entry*
gate, built to be rare, which makes it structurally unusable as an exit trigger. Plain
regime-flip is negative at every confirmation (1/2/3/4 bars). The Jul-7-style 4-signal vote
(regime | VWAP side | 30m HTF | 2 adverse closes) reaches +$5,338 vs +$3,353 baseline but is
**green 2/6 in every variant**, barely improves drawdown, and makes the **worst day worse
(−$1,342 vs −$814)**. REACTION FAILS — 8th confirmation.
**⚠️ Look-ahead caught mid-lab worth $10,446:** `resample('30min').last()` labels a window by its
**START**, so every 5-min bar in it saw the future. **Rule: after any `resample()`, shift the
index forward one full period before joining back.**

**Also settled this session:** the "~100pt stop" anomaly is **not a bug** — every instance was a
TC trade *before* the Jul 25 alignment commit `72b3bb5`. Post-Jul-25 both accounts run exactly
200pt, and it fires on **2 of 33 trades (6%)** ⇒ **do not widen it**. Pre-10:30 entries rejected
a 4th time (09:45 start −$318 vs +$3,353; the 654 extra trades are −$5,442, of which early
SHORTs are −$6,377 and early LONGs +$935). 90-min no-move is not a leak (+$519 live).
Book Health rejected — the Jul 18 deferral was right (signal version is a −0.957 mirror; trade
version is anti-predictive live, spread −$420).
**⚠️ London since Jun 5: 81 trades, +$104 total, +$1.29/trade, 80 of 81 exits 'stop'** — and
`london_v2_sim` charges zero commission/slippage. Verify London survives its own costs.

**Still open:** decoder-in-real-time + nightly futures learner (not started); whether to ship
**LONG + price>MA50** (+$7,092, DD −2,569, worst −$813, green 5/6 — beats every other candidate
including the ★ 1000pt config on worst day and DLL-days).

---

## Aug 24 2026 (late) — ATR research: real mechanism, no shippable edge; decoder root-caused

Full trail [[futures-regime-exit-aug24]]. Labs `futures/factory/{thesis_lab,atr_exit_lab,
atr_thesis_lab,regime_exit_lab}.py`. **Nothing new wired to live beyond the 3 fixes above.**

**THE STRUCTURAL FACT.** Split the 949-trade sim book by whether MFE ever reached +120pts (below
which no adaptive exit can arm): **ORPHANS 733 (77%) = -$34,268, green 0/6** vs **MOVERS 216
(23%) = +$37,621, green 6/6**. The movers are all-weather; the book's regime-dependence is
entirely the orphan mix. The +$3,353 net is the residual of two huge opposing flows.

**THE UNITS FINDING (real, and the strongest stable relationship the program has found).**
Median MFE is a near-constant **~2.4-2.75 ATR every year**, but the trail-arm floor is a fixed
120pt = **7.21 ATR in 2021 vs 3.03 ATR in 2026**. Within-year ATR vs reach-120: train +0.250 /
test +0.250 — identical. So whether a trade can ever arm its trail is set by the year's
volatility, not the setup. `--atr-exits` wired into `sim_replay.py` (default OFF, **no-op gate
passed: identical trade frames**).
**BUT the 5.5yr pipeline says +$633 (x1.0) / +$307 (x1.25) — green stays 2/6, worst day
unchanged. Only drawdown improves (-7% / -18%). Risk-reducing, P&L-null. NOT SHIPPED.**

**ATR does NOT rescue thesis invalidation** (`atr_thesis_lab.py`): cutting at X ATR adverse is
catastrophic at every X (-$27k to -$32k vs -$2,015 baseline); "not +Y ATR within N bars" loses
in every cell. ⭐ **UNIFYING PRINCIPLE, 9 confirmations: every change that gives trades MORE
room helps; every change that cuts them EARLIER hurts.** ATR helps exactly where it widens a
threshold and fails exactly where it tightens one.

**DECODER root-caused + ATR context SHIPPED.** Every decoder field is scale-free or volume-based;
correlation with the day's real ATR: adx **-0.008**, rvol +0.088, range_pos -0.121, vwap_ext
-0.146, vwap_slope -0.155. It is structurally blind to price volatility — instrumented on the
axis proven 8x unforecastable, blind to the one that survives a walk-forward split.
`futures/expectancy_ledger.py` now writes `atr5` + `atr_ratio` to `gate_blocks_ctx`, computed
**from BARS not the live feed, so fully backfillable over all history — no year of waiting.**

**⚠️⚠️ METHOD LESSON: correlation is NOT a calibration gate.** The frame lab predicted +$7,713;
the pipeline delivered +$633 — **12.2x overstatement**, because the frame engine's own baseline
(-$2,015) did not reproduce the real stack (+$3,353). Per-trade correlation +0.856 passed and
was still insufficient. **Gate a within-engine A/B on total P&L AND exit-mix agreement, not
correlation. If the level is off, fix the engine before running any variant.**

---

## Aug 25 2026 — ❌ overnight-range gate RETRACTED (look-ahead); live fixes VERIFIED

**The Aug 24 "overnight range" lead is VOID.** Found while specifying the window to wire it.
I built it as `between_time('18:00','09:29')`, which **wraps midnight** and therefore included
`D 18:00-23:59` — the evening AFTER day D's session. **The post-session half set the extreme in
85% of 542 sessions.** Correct window (as `conditions.py:150` always had it):
`prev_session 18:00 -> today 09:30`. Causal rebuild: corr(mover) **+0.146 -> -0.048 (sign flips)**;
filter P&L **+$13,314 / green 6/6 -> +$130 / green 2/6**. Nothing was wired; no gate was built.

**Why five checks missed it:** family-wise permutation, walk-forward, within-ATR and
within-time-of-day controls, and drop-best-days **cannot detect look-ahead** — it produces a
genuine correlation with the real label and is present in every split and bucket. The check that
DID fire was **cross-build replication** (conditions.py's own build correlated only +0.133 and
made -$500) and **I explained it away.**
⇒ **RULE: a failed independent replication IS the finding — reconcile definitions first.**
⇒ **RULE: never use `between_time()` for overnight windows; anchor to an explicit prior timestamp.**

**Corrected feature-hunt result: 19 of 19 entry features FAILED.** Nothing currently measurable
separates the 0/6-green ORPHANS (77% of trades, -$34k) from the 6/6-green MOVERS (23%, +$38k).
The split is real; it is not predictable from anything we have.

**LIVE FIXES VERIFIED (first full day, Aug 25):** regime forming-bar fix **CONFIRMED WORKING** —
share of 5-min bars whose RSI/VWAP/day_chg changed mid-bar fell **100% -> 8.8%** (residual is the
bar-boundary case). Zero tracebacks/exceptions since the restart. `MAX_OPEN_TRADES=1` and the
2-min entry cooldown are shipped but **not yet exercised** — zero NY entries Aug 24 and Aug 25
(GRADE never reached A+; best was A(75) SHORT). London took 4 trades on Aug 25, all BE scratches,
net exactly $0.00.

---

## Sep 2 2026 — 9-day observation verdict: Aug-24 fixes WORK · exit rebuild KILLED · LONG-only + daily-trend filter is the candidate

Ten-day monitoring window closed and evaluated. Both NY traders have run the Aug-24 code
continuously since 22:08 that night (`ps` start time == file mtime, and `Max trades open (1)`
appears in BOTH logs, so the fixes are confirmed live, not just on disk). **Zero manual
`FUT CLOSE` in the window** ⇒ this is the first clean read of the automated book.

**① THE AUG-24 FIXES ARE VALIDATED.**

| | Jun 5 – Aug 23 | Aug 24 – Sep 2 |
|---|---|---|
| automated P&L | **−$2,536** / 155 trades / 45 days | **+$955** / 6 trades / 4 days |
| entries <5 min apart | **78** | **0** |
| parity cop (NY) | 2-5 live vs 0-1 sim, most days | 5 of 8 days clean |

The duplicate-entry bug is **gone**. `MAX_OPEN_TRADES=1` is what does the work (1,353 scan
cycles skipped while a position was live); `ENTRY_COOLDOWN_MINUTES` has **never fired** — it is
the backup for the fast-exit case, keep it. n=6 is NOT evidence of profitability; it IS
evidence the bug is fixed. Regime forming-bar fix confirmed working Aug 25 (mid-bar RSI/VWAP
churn 100% → 8.8%).

**② EXIT REBUILD (1000pt / 1c / no-trail / no-rev-exit / no-no-move) — KILLED, user-agreed.**
Replayed the observation window both ways: **live exits +$227 sim (+$955 actual) vs ★ config
−$454.** And 100% of the real live profit came from the two mechanisms ★ deletes:
Aug 28 partial +$302 & rev_exit +$140 · Sep 1 partial +$302 & rev_exit +$210. Over 5.5yr they
tie on P&L (+$8,107 vs +$8,722) but ★ has a **−$2,001 worst day vs −$802** and 3 TC DLL halts
vs 0. **Do not re-open without a new mechanism.** Keep: 200pt stop, regime-aware trail,
Reversal Exit `2,0.30,120`, Partial Scale-Out 150.

**③ NEW — the instant-kill trades have a cause, and it is not stop width.**
Split the 949-trade 5.5yr sim book by "exited ≤45min AND losing": **17 trades (2%) = −$8,547**,
the other 929 = **+$11,910**. All 17 are full 200pt stop hits. Two percent of trades eat 2.5×
the book's entire net profit. What those days had in common: **15 of 17 were below the daily
MA50; 16 of 17 below MA50 AND MA200** (−$8,140 blocked). You are not ambushed on random days —
you are ambushed on days the tape was already walking downhill.
**A 1000pt stop does NOT fix it**: it converts most into slow EOD bleeds (good) but once
(2025-11-20) into **−$2,001** (the whole TC MLL in one fill). 500pt is the best of the
stop-width family (+$8,622, green 4/6); **1000pt is the worst member of its own family.**

**④ PIPELINE-CONFIRMED CANDIDATE (real `_run_scenario`, live exit stack, 200pt stop).**
Day filter is fully causal — PREVIOUS close vs PREVIOUS MA (`conditions.py:209-211`).
Runner: `futures/factory/_longonly_ma.py <200 | 50,200>`.

| config | n | total | maxDD | worstDay | TC blows | green | /week |
|---|---|---|---|---|---|---|---|
| LIVE TODAY (both sides, 2c) | 949 | +3,353 | −7,715 | −814 | **6** | 2/6 | 3.5 |
| LONG-only, live exits | 601 | +8,722 | −3,201 | −802 | 1 | 5/6 | 2.2 |
| **LONG-only + day>MA200** | 419 | **+9,848** | −2,198 | −801 | **0** | **6/6** | 1.5 |
| LONG-only + day>MA50 & >MA200 | 326 | +10,113 | **−1,194** | −801 | **0** | 5/5 (2022 = 0 trades) | 1.2 |

**Frame estimate == pipeline result EXACTLY** (both configs, to the dollar) — because the book
is signal-scarce, a freed slot never gets refilled. Frame filters are trustworthy on THIS book.
**Recommend MA200** (green all six years incl. 2022; one condition; still trades). MA50&200's
better DD is only because it sat out all of 2022 — no 2022 evidence, not 2022 survival.
Levers are independent and additive: day-filter alone +$6,324 · LONG-only alone +$5,376 ·
both +$9,848. **REJECTED: "skip high-ATR days" (−$1,325)** — it is the TREND, not the volatility.

**⑤ WOULD IT HAVE SAVED THE TWO BAD LIVE DAYS? Half.**
- **Aug 21 (−$1,979, worst day ever): YES, fully.** All 4 trades SHORT ⇒ long-only deletes the
  day. (Dup-fix alone already halves it to −$1,000.)
- **Aug 17 (−$1,510): NO.** All 4 LONG, day above BOTH MA50 and MA200 — every proposed filter
  waves them through. Only the already-live dup-fix helps (→ −$739). Entered ~30,275 at 11:06,
  slid 200pts, IBKR held 4h50m to a backup stop at 15:55; never reached +120pts so the trail /
  rev-exit / partial never armed. **This is the ORPHAN class** (77% of trades, −$34k, 19 of 19
  entry features failed to predict it — see Aug 25 entry). Unsolved, and this change does not
  claim to solve it.

**Aug 1 → Sep 2 restated (automated, partials merged into their parent entry event):**
actual **−$1,360** (6 losing days of 12, worst −$1,979) → live-now/dup-fix **−$14** (worst
−$1,000) → +LONG-only&MA200 **+$599** (4 losing days, worst −$739, 9 trading days instead of 12).
**Cost of the rule, stated: it gives back Sep 1's profitable SHORT (+$512) and skips Aug 14.**

**⑥ LONDON — untouched, and the good week is NOT evidence.** Since Aug 24: +$1,078/16 trades,
but **4 trades are the entire $1,079 and 12 of 16 were BE scratches** (net −$0.48). Jun 18–Aug 23
was +$1.31/trade over 80 trades — i.e. indistinguishable from `london_v2_sim`'s +$1.54/trade.
Live rows ARE net of commission (`london_trader.py:675`, $1.24 round-turn) and of real fills,
so the live record (+$1,183 / 96 trades lifetime) is real; it is the SIM that is inflated
(zero commission/slippage). Verdict unchanged: razor-thin, paper only.

**⑦ STILL OPEN.** (a) live/sim divergence UNEXPLAINED — Aug 28 sim entered 29,683 vs live
29,649 on the same signal (34pt gap, different bar); parity cop flagged Sep 1 live-only too
(that one made +$512). Until root-caused, live setup-level P&L cannot judge a setup.
(b) ATR-scaled exits — settled: risk-reducing, P&L-null, NOT shipped (`--atr-exits`, default
OFF). (c) TC has taken 1 NY trade in 9 days (−$84) — not evaluable. (d) decoder-in-real-time +
nightly futures learner — not started. (e) `parity_check.py` / `expectancy_ledger.py` London
queries still not `account_mode`-filtered.

**DECISION PENDING:** ship LONG-only + day>MA200 live on all 3 files
(`futures_trader.py`, `tc_trader.py`, `sim_replay.py` + `parity_check.SIM_FLAGS`), or run it
log-only first. Nothing wired this session.

---

## Sep 2-3 2026 — SHIPPED: short size cap (live) + Daily Tide (log-only) + TC gaps + commission bugs
### ⚠️ ALSO: two of this session's own numbers were WRONG and are corrected here

**⚠️⚠️ READ THIS FIRST — THE `+$3,353` "LIVE TODAY" BASELINE CARRIES A $6/CONTRACT
FRICTION HAIRCUT; THE RAW `_run_scenario` OUTPUT DOES NOT.** `_mom_2021-06-01_2026-08-14.csv`
(and every table in `docs/FUTURES_CATCHUP_2026-08-24.md`) is friction-adjusted. A fresh run of
the *same nominal config* via `_stoprun.py`/`_capab.py` gives **+$9,252 raw**, which is
**+$2,622 after $6/contract** — i.e. the same book. Mixing the two bases overstates every
improvement by ~25-30%. **Always state the friction basis before quoting a total.**

**ALL FIGURES BELOW ARE AT $6/CONTRACT, apples to apples:**

| config | n | total | maxDD | worstDay | green |
|---|---|---|---|---|---|
| baseline (cap off) | 951 | +2,622 | −7,808 | −814 | 2/6 |
| **SHIPPED short cap** | 951 | **+4,063** | −6,735 | −814 | 2/6 |
| LONG-only + >MA200 | 419 | +6,962 | −2,906 | −813 | 5/6 |
| SHORT-only + <MA200, 1c | 97 | +2,507 | −917 | −407 | 3/4 |
| **★ DAILY TIDE (both legs)** | 516 | **+9,469** | **−2,906** | −813 | **5/6** |

**① SHORT SIZE CAP — SHIPPED LIVE** (`SHORT_MAX_CONTRACTS=1`, futures_trader.py +
tc_trader.py + sim_replay.py + parity SIM_FLAGS). The RVOL/IB conviction ladder is
anti-predictive on the SHORT side ONLY: LONG 2c +$26.7/t green 6/6 vs **SHORT 2c −$85.3/t,
green 1/6, negative in 5 of 6 years** (dropping its worst single trade still leaves −$4,221 ⇒
systematic). Mechanism: the ladder scales on high ATR + big gap; for LONGs that is a +0.21-ATR
momentum gap, for SHORTs a **−0.36-ATR gap-down at ATR-rank 0.80** — panic read as conviction,
doubled into the snapback. Removes ZERO trades.
**⚠️ CORRECTED MAGNITUDE — the clean within-engine A/B (`futures/factory/_capab.py`, one
variable, same script) says +$1,081, NOT the +$2,517 frame estimate and NOT the +$7,544 a
cross-cache diff claimed.** Decomposition: 60 resized shorts **+$2,276**, minus **−$1,195**
knock-on on 4 same-day trades. **NEWLY DISCOVERED COST: Partial Scale-Out needs 2 contracts, so
capping shorts at 1c means shorts can never scale out** — those 4 trades each lost a $298.76
partial leg. maxDD −$5,175 → −$4,292 (raw basis). **RETRACTED: "blow-ups 6→4" — the clean A/B
has 3 TC blow-ups in BOTH arms, unchanged.** Verdict: keep it (risk-reducing, small P&L gain,
~$200/yr) but it is not a big win.

**② DAILY TIDE — SHIPPED LOG-ONLY** (`TIDE_GATE_ENABLED=False`, gate name `TIDE_INFO`,
review after one week). LONG only when the PREVIOUS daily close is above its PREVIOUS 200-day
MA, SHORT only when below. Both legs pipeline-confirmed separately (`_longonly_ma.py`,
`_shortonly_ma.py`), day sets provably disjoint ⇒ exact merge. `get_daily_tide()` is causal and
**fails OPEN** so a missing daily bar can never halt trading; cached per calendar day.
Also validated: **frame estimate == pipeline result to the dollar** on this book (signal-scarce
⇒ a freed slot is never refilled). REJECTED: "skip high-ATR days" (−$1,325) — it is the TREND,
not the volatility.
Caveats under watch: the short leg is **bear insurance** (72 of 97 trades are 2022; ZERO trades
in 2021 and 2024; dormant since Apr 8 2026); it already cost a real winner (Sep 1, +$512); NY
zero-trade days rise **43% → 68%**; London shorts the same tape ungated.

**③ WHY WE GET AMBUSHED (answers a long-standing question).** 17 of 949 trades (2%) exit ≤45min
at the full stop for **−$8,547**, while the other 929 make **+$11,910**. **15 of 17 were below
the daily MA50; 16 of 17 below MA50 AND MA200.** Not random days — days the tape was already
walking downhill. A 1000pt stop does NOT fix it (one 2025 case goes −$407 → **−$2,001**, the
whole TC MLL in one fill). **Aug 21 (−$1,979) would be fully prevented** (all 4 trades SHORT);
**Aug 17 (−$1,510) would NOT** (all LONG, day above both MAs) — that is the ORPHAN class,
still unsolved.

**④ TC GAPS — found by AST-diffing all 52 shared functions with comments stripped.** The two NY
traders are duplicated code and three real divergences had accrued, all now fixed in
`tc_trader.py`: (a) **NO WEEKEND GUARD** — the Jul 18 2026 fix is recorded as applied to "both
traders" but only ever landed in futures_trader.py; (b) no stop-sanity ceiling; (c) **RECONCILED
rows counted in daily/all-time P&L**, and on TC that figure feeds `check_can_trade()`'s DLL gate
and the daily circuit breaker, so a phantom row could halt a live prop account.
**Note the premise "TC trades less" is FALSE over the comparable window** — since Jul 25 TC took
20 trades vs IBKR's 17. **UNEXPLAINED: TC took 8 ORB_LONG / 0 PM_LONG while IBKR took 3 PM_LONG /
0 ORB_LONG on the same days and minutes, with byte-identical `setup_name()`** ⇒ the two gateway
sessions see different bars. Same thread as the live/sim divergence.

**⑤ COMMISSION — three factual bugs, and one changes a conclusion.**
(a) `london_v2_sim.py` charged **ZERO** commission and zero slippage. (b) `london_trader.py`
subtracted `COMMISSION` once despite declaring it per-contract, and London always trades 2.
(c) **`sim_replay.py` had the same bug** — every 2-contract trade in every backtest this program
has ever run was under-charged one round turn (~$190 over the 5.5yr book).
**LONDON DOES NOT SURVIVE ITS OWN COSTS.** Champion, 805 trades Jan 2025→Sep 2026:
zero-cost +$1,299 · commission-only 1c **−$348** · **commission-only 2c (= live) −$697**
(2025 +$2,209 / 2026 −$2,906) · +0.5pt/side −$3,917 · +1.0pt/side −$7,137. Gross edge +$1,299 vs
$1,996 of round turns ⇒ **commission alone flips it negative, and 2026 is negative in every
model.** Slippage stays opt-in (`--slippage`, default 0) and `--contracts` models live size, so
measured costs and estimated costs remain separable. Counterweight: LIVE London is +$1,183 over
96 trades on real fills — but 4 trades in the last 8 days carry all of it, and Jun 18–Aug 23 was
+$1.31/trade over 80 trades. **London decision pending.**

**⑥ OPS.** Both NY traders restarted 23:50 ET Sep 2, verified running new code (process start
after last file mtime), clean startup, no errors. **Both IB gateways were found DOWN** and
restarted — that would have silently killed the Sep 3 London session (same failure class as
Aug 9; check gateways, not just bridges).

**⚠️⚠️ METHOD — I broke a rule this file already recorded, twice in one session.** The Aug 24
entry says: *gate a within-engine A/B on total P&L, not correlation / not a cross-cache diff.*
I first validated the cap by diffing against `_mom_2021-06-01_2026-08-14.csv` and reported
+$7,544 — invalid, because **949 of 949 trades differed, including 1-contract trades the cap
cannot touch.** Then I compared friction-adjusted and raw books. **RULES: (1) never compare two
caches unless the same script generated both; use `_capab.py`'s pattern. (2) State the friction
basis with every total. (3) If trades the change cannot possibly touch have moved, the
comparison is contaminated — stop and find out why.**

**OPEN / NEXT.** (a) **live/sim divergence — highest priority**, now with a third data point
(IBKR vs TC setup mix on identical minutes); until root-caused, live setup-level P&L cannot
judge anything. (b) Daily Tide log-review ~Sep 9. (c) London decision. (d) `IBKR_FLOOR=$5,000`
is unfundable — 2 contracts need ~$8,750 margin = **175% utilisation**; Sep 1's live short was
$116,619 notional (23× the configured allocation, 2.3× a $50k TopStep account). Real risk was
$800 (the stop), but the broker demands the margin. (e) Nightly futures learner — deferred on
purpose until (a) is fixed, or it will learn the divergence.

---

## Sep 3 2026 — reboot cost the London session: RunAtLoad was missing everywhere; TC unblocked

**① WHY LONDON TOOK NO TRADES — the Mac rebooted, and nothing restarted.** `last reboot`:
**shutdown 01:29, reboot 01:31** — the exact minute both trader logs stop. (An earlier
"process hang" theory in this session was WRONG; the user's reboot guess was right. There was
no hang and no trader bug.) The real gap: **only `watchman` and `options_trader` had
`RunAtLoad=true`.** Every futures service, BOTH gateways, BOTH bridges and `autotrader` had
none, so after a reboot each waited for its next `StartCalendarInterval`:
reboot 01:31 → gateway 02:50 → traders 08:00. **London's 3-8am window was never covered.**
**FIXED: `RunAtLoad=true` added to gateway, tc_gateway, bridge, tc_bridge, futures_personal,
futures_trader, autotrader** (plists backed up to `~/Library/LaunchAgents/_bak-20260903/`),
booted out + bootstrapped, all 7 verified running, both bridges reconnected (DU9952463 /
DUQ640500). Note `KeepAlive` is `{Crashed: true}` on all of them — that covers a crash, it
never covered a reboot.
**⚠️ STILL MISSING: a heartbeat/watchdog.** Nothing alerts when a trader stops. This one was
only found by reading logs a day later. Before any funded eval, the traders should write a
heartbeat and something independent should Telegram on silence.

**② TC WAS ADMINISTRATIVELY FROZEN — by $45, and it could not escape.** `check_can_trade()`
refuses everything when `balance + unrealized < effective_floor + SOFT_STOP_BUFFER($300)`:
balance $49,500 · HWM $51,245 · floor = 51,245−2,000 = **$49,245** · needs **$49,545**.
Blocked by **$45**, on BOTH books. Last TC NY trade Aug 26 (−$84, the loss that pushed it
under); last TC London trade Aug 25. **Deadlock — it cannot earn its way out because it cannot
trade.** Same architecture class as the Jul 21 equity Book-Health freeze: a gate with no
self-release path.
**RESET (user-directed) to a clean combine baseline:** balance/HWM $50,000, total_profit 0,
best_day 0, qualifying_days 0 (old file kept at `futures/prop_state.json.bak-20260903-105334`).
Verified `check_can_trade → True`, floor now $48,000 with the full $2,000 of room, and **0 MLL
blocks since the restart**.
**⚠️ The freeze will recur** — nothing warns as the balance approaches the buffer, and there is
no reset path other than editing the state file by hand.

**③ TC LONDON — checked, as asked. 12 trades ever, +$5.12 total, Aug 10-25 only**, then the
MLL guard shut it off. It is not a strategy sample; it is 12 trades of nothing.

**④ WHAT THE TC FIXES DO: accuracy, not volume.** None opens the throttle — weekend guard and
stop-sanity ceiling both *remove* junk, `RECONCILED`-exclusion corrects the number feeding TC's
own DLL gate, and the short cap only resizes. **The premise "TC trades less" is FALSE**: since
Jul 25 TC took 20 trades vs IBKR's 17 — until the MLL guard froze it.

**⑤ TOPSTEP READINESS — do not buy the subscription yet.** Three independent reasons: (a) the
TC account was already at −$500 against a +$3,000 target with **$1,745 of its $2,000 trailing
MLL consumed** — 87% of the way to a blow-up with zero progress to target; (b) the arithmetic:
~1.9 trades/week at ~$12/day mean vs a 4% trailing MLL, prior gauntlet **P(pass) 8.5% vs
P(blow) 29%** (Daily Tide improves this to 0 blow-ups in the *historical ordering*, but the
path-shuffled bootstrap has NOT been re-run on the Tide book); (c) infrastructure — a reboot
silently cost a whole session and nobody was told.

**⑥ LIVE CONFIG, both NY traders, verified in the running processes (restarted 10:54 ET Sep 3,
clean startup, MIDDAY scans, IB classified, London enabled):**
`MAX_OPEN_TRADES=1` · `ENTRY_COOLDOWN_MINUTES=2.0` · `SHORT_MAX_CONTRACTS=1` (LIVE) ·
`TIDE_GATE_ENABLED=False` (**Daily Tide == the day>MA200 side check — LOG ONLY on BOTH
accounts**, gate name `TIDE_INFO`, review ~Sep 9) · `TIDE_MA_DAYS=200`.

---

## Sep 3 2026 (pm) — live/sim divergence ROOT-CAUSED (3 defects) + heartbeat watchdog SHIPPED

**① THE DIVERGENCE WAS NEVER A STRATEGY DIFFERENCE — it is three separate defects.**
Diagnosed from one signal that BOTH accounts took at the same second (11:00:44 Sep 3), which
finally made the comparison controlled.

**Defect 1 — `tc_trader.py`'s setup label was a TWO-WAY STUB.** IBKR labels by a 5-way
priority list (`pm_bull`→PM · `orb_bull`→ORB · `vwap_reclaim`→VWAP · `momentum_bull`→MOM ·
else OPEN); TC had **one line**: `setup = f"ORB_{side}" if orb else f"VWAP_{side}"`. TC could
**never emit PM_/MOM_/OPEN_**. Sep 3 proof: identical trade, `pm_bull` AND `orb_bull` both
true → IBKR wrote `PM_LONG`, TC wrote `ORB_LONG`. **That single line is the entire "TC fires a
different setup mix" mystery** (the Sep 2 entry's 8 ORB_LONG / 0 PM_LONG vs 3 PM_LONG /
0 ORB_LONG). Every cross-account per-setup P&L table has been comparing a 5-name vocabulary
against a 2-name one. FIXED — TC now carries IBKR's exact list, asserted identical by test.

**Defect 2 — the DB stored the SCAN price, not the FILL price.** `place_trade()` verified fill
SIZE (`_actual_filled`) but never fill PRICE. Measured on that same signal:
IBKR scan 29378.25 / actual 29378.06 (**0.19pt**) · TC scan 29378.25 / actual 29392.93
(**14.68pt = $58.72 on 2c**). Stops, targets, trail tiers and P&L all keyed off a price we did
not pay — material when the whole edge is ~$12/day. FIXED: new `_get_fill_price()` in both
traders, wired into `place_trade` BEFORE the backup stop and the DB write, and `sl/target` are
recomputed from the real fill so a 200pt stop is 200pt from where we actually got in.
**⚠️ PRIMARY SOURCE IS PORTFOLIO `avg_cost`, NOT `/order/{id}/status`** — verified live that a
genuinely filled entry still returns `{status: PendingSubmit, filled: 0.0, avgFillPrice: 0.0}`,
the same staleness class as the Jul 20 2026 USAR incident. Because `MAX_OPEN_TRADES==1` every
entry opens from FLAT, so `avg_cost` IS that entry's fill with no blending. Guarded by
`MAX_FILL_DRIFT_PTS=60` (reject stale/blended) and falls back to the scan price on any failure
so an entry is never lost. **This is also why nobody could have built this before: the obvious
endpoint has always been broken.**

**Defect 3 — the two gateways genuinely quote and fill differently, and that part is NOT a
bug.** Bar-derived values were byte-identical on both (`vwap=29252.30 rsi=50.7 rvol=1.21
day_chg=+0.44`) ⇒ the BARS agree. Only the live quote differed (29378.5 vs 29381.0) and the
fills by ~15pts. Two separate IBKR paper sessions simulate fills independently. Consequence to
remember: **TC's P&L is systematically penalised vs IBKR by fill simulation, not by strategy** —
do not read a TC-vs-IBKR P&L gap as a logic difference.

**② HEARTBEAT + WATCHDOG SHIPPED (`futures/heartbeat.py`, launchd
`com.sushil.trading.heartbeat`, every 5 min, `RunAtLoad`).** Built because the Sep 3 reboot cost
a whole London session and **nobody was told** — found by reading logs a day later.
- **writer**: `beat(name)` is the FIRST statement of each `run_scan` (both NY traders +
  `london_trader`, which covers both accounts since it is threaded into each process), so it
  stamps every 60s whether or not a trade fires. Atomic `os.replace`, never raises into the
  caller. Silence therefore means the LOOP stopped, not that no signal qualified.
- **checker**: `python -m futures.heartbeat --check` runs from launchd **outside the traders** —
  that independence is the whole point, a watchdog inside the process it watches cannot report
  that process dying. Checks beat staleness (>300s) AND both bridges' `connected` flag (catches
  a dead gateway, which is what actually bit us on Aug 9 and Sep 2).
- **ACTIVE_HOURS per service** — London only beats 03:00-08:59 ET (its cron window), so outside
  it a missing beat is normal. Without this the watchdog would report `london: NO heartbeat file`
  every 5 min from 9am to 3am and you would learn to ignore it — the same alert-fatigue failure
  as the Jul 20 USAR retry storm. **A watchdog that cries wolf is worse than none.**
- Alerts deduped to once per issue per 60min, suppressed outside 03:00-16:00 ET (logged, not
  sent), and it sends a RECOVERED notice when everything comes back.
- Verified end to end: fresh beats → OK/exit 0 · aged beat → detected, Telegram sent, exit 1 ·
  immediate re-run → deduped · real trader beat returns → OK + recovery · London out of window →
  correctly silent. `--status` gives an at-a-glance view.

**③ OPS.** Both NY traders restarted 12:29 ET, verified beating with their real PIDs
(22489 / 22492), both bridges connected. Watchdog loaded and running.

**OPEN / NEXT.** (a) Daily Tide log-review ~Sep 9. (b) London decision (does not survive its own
commission — see Sep 2-3 entry). (c) `IBKR_FLOOR=$5,000` unfundable at 2 contracts (~$8,750
margin = 175%). (d) **The evidence clock started Sep 3.** The live NY strategy changed **20 times
in 90 days**, longest stable window ever **15 calendar days (~4 trades at 1.9/week)** — which is
why no live number has ever described any version of the code. Freeze entry/exit logic now; only
touch infrastructure, logging and the divergence work. At 1.9 trades/week: ~4 trades in 2wks
(mechanism check only), ~16 in 2mo (noisy direction), ~48 in 6mo (a real win rate), ~95 in 12mo
(bootstrap p10 clears zero). **A bad day proves the machine held; it does not prove the edge.**

---

## Sep 3 2026 (pm2) — Exit Map: show distance to the exits that ACTUALLY fire

**The problem (user-raised):** the DB's `target_price` shows ~1500pts away (e.g. 30,878 on a
29,378 entry) and is the only "target" visible anywhere. It is `BASE_TARGET_PTS`, a disaster cap
and the numerator of the `MIN_RR` gate. **It has fired ZERO times in 951 trades over 5.5yr; the
best trade this book has ever produced ran 378pts.** So nothing on screen answered the question
that matters when deciding whether to close by hand: *how close was I to the system doing it?*

**SHIPPED (display-only — no trade selection touched, safe inside the frozen window):**
- **`exit_map(trade, price)`** in both NY traders — distance in pts AND % to every exit that can
  really fire, nearest first: trail stop (with the $ it locks), Reversal Exit trigger level (or
  "not armed — peak +Xpt of 120 needed", plus the adverse-bar streak), Partial at +150 (only
  while 2 contracts and untaken), no-move (time left + whether P&L is inside the band so it
  *can* fire), EOD.
- **`FUT STATUS`** now prints it under each position — the phone answer.
- **`_publish_exit_map()`** writes `logs/heartbeat/exitmap_{ACCOUNT}.json` each monitor cycle
  (atomic, no bridge call when flat). **Required because the rev-exit level depends on the
  running peak `_session_high`, which exists ONLY in the trader's memory — the dashboard cannot
  derive it from the DB.**
- **Dashboard**: new "Nearest real exit" column (colour-coded ≤25pt / ≤60pt / far, full list on
  hover, red "stale Nm" if the file ages past 180s = the trader is not running). The old Target
  column is renamed **"Cap"**, greyed out, with a tooltip explaining it never fires.

**⚠️ SIDE EFFECT LEARNED THE HARD WAY — restarting mid-position DISARMS the Reversal Exit.**
`_session_high` / `_rev_state` are in-memory only. Restarting the traders at 13:10 while both
held a runner reset peak +166 → +118, so rev-exit went from ARMED at 29,494 to *not armed*
(needs 120). **The trail stop is unaffected — it lives in the DB** — so the locked floor
survived, but the give-back protection did not. **Do not restart mid-position unless the change
is worth losing peak state; prefer flat periods.** (Candidate fix, NOT built: persist peak to
the DB the way Bite Check and the Crest Watch streak already are.)

**Live at time of writing:** IBKR banked $308.50 + unreal $238.83 = **$547** · TC $308.00 +
$221.64 = **$530**; stops 29,479.75 / 29,481.00 ≈ 16pt below price.

---

## Sep 5 2026 — Clockwork + Contrarian WIRED LIVE (paper orders) + MOC/MOO added to bridge

Equity swing deep-read first (see [[equity-swing-deep-read-sep5]]): **Wave Rider is not broken.**
Over its live window it picked **−2.15%/trade from an opportunity set averaging −2.13%** —
selection cost 0.02pts; tide was −1.87%. ~90% of the −$904 is the tape. In 2026 the engine's
entire profit is **Apr+May (+$4,265); every other month combined is −$917**, and alpha
day-clustered t = **+0.70**. The 8% stop is irrelevant (mean return flat from 5% to no stop),
hedging does not save it (alpha is negative in the same months), and no causal SPY market-state
gate helps. **Wave Rider left in SHADOW, unchanged.**

**Two corrections to my own analysis, recorded so they are not repeated:** (1) "the picker is
noise" was WRONG — a flat top-5 test misses that 3-day holds mean the book usually fills only
1-2 slots, so it mostly takes rank 1-2, the good ranks; under real slot dynamics the ranker
beats random in all 3 years. **Test selection under the book's real capacity constraint.**
(2) A `day_chg>=7%` filter looked excellent in 2026 (z=+2.83, clean 7-9 plateau, both halves
positive) and is a **curve-fit** — 2024 z=−1.63, 2025 z=−0.08. Plateau and split-half both
passed and were both wrong; **only the cross-year check caught it.**

**⭐ THE FACTORY GATE HAS A HOLE — `COST_DRAG` is a PERCENTAGE-only model that never checks
position size.** Contrarian as validated holds ~100 concurrent positions (10 long + 10 short ×
5d). At $10k that is $100/position, where IBKR's **$1 minimum commission is 1% per side**: net
**−3.65% at $10k, −1.49% at $25k, −0.67% at $50k**. A breadth strategy sails through a
percentage cost gate and dies on a fixed-cost floor. Any future cross-sectional engine must be
checked at the position size we can actually fund.

**⚠️ TWO BIASES THAT INFLATE ANY CONTRARIAN BACKTEST, both worst under concentration:**
(1) `daily_close.csv` is built from RAW bars with **no split adjustment**, so a split reads as a
~−90% 3-day "crash" — CMG (50:1 Jun 2024), AVGO (10:1 Jul 2024), NFLX, BKNG, KLAC were all top
picks; **12.6% of #1 picks were split artifacts**, and they "returned" +12.83% vs +2.71% for
real fallers (fake recoveries from the known dual-format `bars_5m` problem). (2) The 241-name
universe was **screened in Jul 2026 partly on past performance**, so "buy the biggest crasher"
is exactly the axis survivorship bias inflates. **Treat every Contrarian backtest number as
having no credible prior. Only the forward run is clean.**

**SHIPPED:**
- **`bridge.py`: MOC + MOO order types** (additive branch in `/order`; MARKET/LIMIT untouched).
  Needed because Clockwork's backtest prices entries at the daily CLOSE and exits at the daily
  OPEN, while live placed MARKET orders at 15:47 and 09:31 — paying the spread at the two widest
  moments and 13 min early. It was measuring our slippage, not the edge. Bridge restarted
  (Saturday, gateway down — zero-risk window).
- **Clockwork → LIVE** (`CLOCKWORK_MODE=LIVE`). Now two-phase per side: submit MOC 15:40-15:49 →
  confirm the fill 16:00-16:40; submit MOO 09:00-09:27 → confirm 09:31-09:59, with a **MARKET
  fallback after 09:45** so an unsold overnight position is never carried into the day. Nothing
  is marked OPEN or CLOSED on a price we did not fill at (the Jul-20 USAR lesson).
  Shadow record it graduates on: **150 trades, +$1,034 — but 83% of that is ONE real event**
  (MRNA +177% on 200M shares vs 4M normal). **Ex-MRNA +$379**, which is ~breakeven at the
  factory's own 0.20% cost assumption. Going live is how the true fill cost finally gets measured.
- **Contrarian → BUILT + LIVE** (`factory/live/contrarian.py`, `contrarian_trades` /
  `contrarian_scan_log`, launchd `com.sushil.trading.contrarian`). **Concentrated form the
  Proving Ground never scored**, per user's design: **2 slots × ~$5,000, recycled as they
  close** — which puts commission at 0.02%/side instead of 1%. Ranks the universe by trailing
  3-day move, buys the biggest fallers, 5-day hold, 15% stop, earnings block, and a
  **`SPLIT_MAX_DROP=-35%` artifact filter** (it LOWERS the 2026 backtest $10,791→$6,900 —
  correct, because it removes fiction). **LONG-ONLY on purpose:** the short leg carried only
  +0.116% of the +0.355% alpha for all the borrow/squeeze risk. That drops market-neutrality,
  so **judge this book on alpha vs the Tide, not raw P&L.**
- **Mode isolation on both engines' open-position queries** — caught before Monday: Clockwork
  had 10 OPEN rows from SHADOW, and in LIVE `exit_at_open()` would have placed real SELL orders
  for stock never bought = **10 naked shorts**. `get_open()`/`rows_with_status()` now filter on
  `mode=MODE`; the same guard was added to Contrarian pre-emptively. The 10 shadow rows were
  settled flat (commission only — we do not get to claim Monday's open on an abandoned book),
  so LIVE starts from zero.

**Corrections to the ask:** Contrarian was **never** wired (no service, no table) — what existed
was Clockwork and **Trend Rider** (MNQ futures, whose LIVE order path is still a `TODO` stub).

**⚠️ MONDAY SEP 8 — FIRST LIVE SESSION, WATCH IT.** Neither order path has ever placed a real
order; both were built and smoke-tested on a Saturday with the gateway down. Watch, in order:
(1) `logs/contrarian.log` from 10:00 — first real BUY, confirm fill price is recorded;
(2) `logs/clockwork.log` 15:40-15:49 — MOC submitted, then **16:00-16:40 the fill must confirm**;
(3) Tuesday 09:00-09:27 MOO submitted, 09:31+ filled. **If MOC/MOO do not fill on the IBKR paper
simulator, revert to MARKET** — the fallback covers the exit side only, not entry.
Revert either engine: set its `*_MODE` back to `SHADOW` in the plist and reload.

---

## Sep 7 2026 (Labor Day) — TC London root-caused: the gateway was never running during London

**TC London has never really traded, and no London code was at fault.** `LONDON_ENABLED=True`
on TC since Aug 9, the scheduler fires, `run_scan` runs every 60s — and logs
`Bridge disconnected — skipping scan` **291 times, 03:00 -> 07:51, EVERY weekday** (deterministic;
291 min is exactly that span). Cause: **IB Gateway logs itself off nightly at 23:45** (see the
23:45 mtimes on `~/ibc/logs/*.txt`), so each gateway comes back ONLY at its own launchd time:

| launchd job | started (Mon Sep 7) | London needs |
|---|---|---|
| `com.sushil.trading.gateway` (IBKR) | **02:50:03** | 03:00 IB formation ✅ |
| `com.sushil.trading.tc_gateway` (TC) | **07:50:04** | entry window 04:00-08:00 ❌ |

**The 07:50 was correct when it was written** — TC was NY-only and 07:50 gives ~100 min of
warm-up before the 09:30 open. It stopped being correct on **Aug 9 2026**, when commit
`a6fd51a` wired London onto TC: that commit touched `london_trader.py`, `tc_trader.py`,
`prop_rules.py` and the dashboard, **but no plist**. Nobody revisited the gateway schedule.
Proof in the trade record: **12 of TC London's 14 lifetime entries are stamped 07:51-07:58** —
the last nine minutes of a four-hour window, taken the instant the gateway came up. The one
exception (Aug 14, entries 04:01/04:05) is a day the gateway happened to already be running.
So "TC London: 12 trades, +$5.12" was never a London sample at all.

**FIXED:** added `{Hour 2, Minute 50}` for all five weekdays to `com.sushil.trading.tc_gateway`,
matching the IBKR gateway. The existing 07:50 entries are KEPT as a retry — launchd skips a
StartCalendarInterval for a job already running, so a second chance costs nothing if the 02:50
login fails. Verified: launchd holds all 10 entries (5×02:50 + 5×07:50), gateway restarted,
TC bridge reconnected (DUQ640500). Backup: `scratchpad/tc_gateway.plist.bak`.
**Tomorrow is the first day TC London gets its full 04:00-08:00 window.**

**Also fixed (both ops-only, no trading logic):**
- **The watchdog overstated its scope.** Its alert ended with a blanket *"Nothing is trading
  until this is fixed"* — false on Sep 7, when only the TC gateway was down and IBKR traded
  London normally. It now appends **"Still healthy: ..."** and only claims total failure when
  everything is genuinely down. Same alert-fatigue lesson as the Jul 20 USAR retry storm.
- **`auto_trader` went silent on holidays.** Its main loop had no case for 09:31-16:00 on a
  closed day, so a market holiday looked identical to a hung process in exactly the window you
  would check. Now logs once an hour. Verified today placed **0 trades, 0 scan_log rows**.

**⚠️ Watch item, NOT fixed:** both accounts' London instances write to the SAME
`logs/london_trader.log` with no account tag, so `Bridge disconnected` lines cannot be attributed
without counting cadence. Add `ACCOUNT_MODE` to the London log prefix before trusting that file
for per-account diagnosis.

**Open question this re-opens:** London still does not survive its own commission (Sep 2-3:
gross +$1,299 vs $1,996 of round turns). TC London now *can* trade a full window for the first
time — treat the coming weeks as its first real sample, not a continuation of the old 12 trades.

---

## Sep 7 2026 (pm) — London logs tagged per account + NY commission finally deducted

**① London logging is now attributable.** Both accounts thread `london_trader.py` into their
own process and BOTH write to one `logs/london_trader.log`, tagged only `[LON]` — which is
exactly why the TC-gateway outage had to be inferred from scan cadence instead of read. The
logger is now per-account (`logging.getLogger(f'london.{ACCOUNT_MODE}')`) and every line reads
**`[LON:IBKR]`** or **`[LON:TC]`**. Verified both render. Historical lines stay untagged.

**② NY futures P&L was recorded GROSS of commission — fixed.** `pnl_usd = pnl_ticks *
TICK_VALUE * contracts` went straight into `log_futures_exit()` **and** `record_trade_pnl()`
with nothing subtracted, on BOTH NY traders, while `london_trader.py` has always recorded NET.
So the two books were never comparable, live NY was overstated by `COMMISSION x contracts` on
every trade (~$200-370 across the 149 automated trades), and — the part that actually matters —
`record_trade_pnl()` is what feeds prop_rules, so on TC a **gross figure makes the account look
further from its DLL / trailing-MLL than it really is.**

New `_net_usd(gross, contracts)` in both traders, applied at all four realized-write sites each:
partial scale-out (1 contract), backup-stop fill, main exit, and the `FUT CLOSE` reconcile.
The orphan-row cleanup stays $0 (no trade happened, no commission).

**⚠️ Deliberately NOT applied to the live decision variable.** The `pnl_usd` at
`monitor_open_trades`'s top also drives the trail tiers and the dollar circuit breaker.
Charging commission there would move real exit thresholds — a strategy change, which the Sep 3
evidence freeze forbids. **Books get accurate; not one trading decision moves.** Telegram now
reports the booked (net) number so the message matches the DB.

**③ Commission is now ACCOUNT-AWARE.** `strategy_core.COMMISSION` resolves from
`FUTURES_ACCOUNT_MODE`: `commission_rt` for IBKR, `commission_rt_tc` for TC. IBKR (broker +
CME + regulatory) and TopStep (its own bundled schedule) do not charge the same round turn, and
TC is heading for a funded subscription where this number gates the account.
**⚠️ `commission_rt_tc` is currently set EQUAL to IBKR's 1.24 as a placeholder — UNVERIFIED.
Confirm it against TopStep's published fee schedule before the subscription; it is a one-line
edit in `futures/instruments/MNQ.json`, no code change.**

Verified: 11 static + arithmetic checks pass (helper defined before use, `contracts` bound in
scope at every site, cost always charged never credited, 2c/-$800 stop → -$802.48). Both NY
traders restarted clean, 0 errors, London enabled on both, bridges connected.

**Still gross by design:** `risk_usd` / `target_usd` in the sizing path — those are pre-trade
risk estimates, and folding commission in there would change position sizing.

---

## Sep 7 2026 (pm2) — does IBKR charge commission on paper trades? Now answerable with data

User asked the right question: if IBKR charges commission on a paper fill, are we
double-counting by also subtracting a model?

**Checked first — we are NOT double-counting.** Every P&L in this system is recomputed from the
PRICE DIFFERENCE and then has a MODELLED commission subtracted; nothing ever reads the broker's
reported P&L. Equity: `database.log_trade_exit()` does `gross = (exit-entry)*shares` then
`- equity_commission()`. Futures: `pnl_ticks * TICK_VALUE * contracts` then `- COMMISSION *
contracts` (as of today). So subtracting a model is correct — **but nothing verified the
model.** `1.24` per MNQ round turn and `$0.005/share, $1 min` have been assumptions all along.

**SHIPPED:**
- **`bridge.py` `GET /executions`** — every fill with the commission IBKR ITSELF charged
  (`commissionReport`) plus `realizedPNL`. Additive, no existing endpoint touched. A **null**
  commission means the report has not arrived yet (it lands a moment after the exec) and is
  explicitly NOT treated as zero.
- **`commission_check.py`** — per instrument, IBKR actual vs our model, printing the
  per-contract round turn both ways; flags any bucket off by >10%. Rows without a
  commissionReport are excluded from the averages, not counted as free.

**COULD NOT VERIFY TODAY, and did not pretend to:** Sep 7 is a US holiday and **IBKR serves
executions for the CURRENT trading session only**, so `reqExecutions` returned 0 fills. The
"yes it charges" answer above is from how the API is built, not from account data.
**Run `venv/bin/python commission_check.py` after tomorrow's close** — first real calibration.

**⚠️ Note for the TopStep number:** TC currently runs against a SECOND IBKR paper account
(DUQ640500), not TopStep. Tomorrow's check therefore yields IBKR's real rate for BOTH accounts.
`commission_rt_tc` stays an unverified placeholder until we are actually on ProjectX — it must
come from TopStep's published fee schedule, not from this tool.

---

## Sep 18 2026 — Equity deep read: give-back is a SYMPTOM. The diseases are cost and a 1.19 signal.

Full lab set in the session scratchpad (`mfe_ledger.py`, `stall_lab.py`, `addon_lab.py`,
`horizon_lab.py`, `sel_lab2.py`, `stack_lab.py`). **One infrastructure fix shipped
(`collect_bars.py`); no strategy change, the Sep 3 evidence freeze holds.**

**① THE GIVE-BACK LEDGER, on 668 replayed trades (Apr 15 – Sep 18, real 5-min bars).**
Split the book by how far each trade ever got:

| lifetime peak (MFE) | n | share | P&L | avg peak | avg realized | capture |
|---|---|---|---|---|---|---|
| < 0.5% | 302 | 45% | **−$2,960** | 0.0% | −0.8% | n/a |
| 0.5–1% | 86 | 13% | −$715 | 0.7% | −0.5% | — |
| 1–2% | 119 | 18% | −$89 | 1.5% | −0.1% | — |
| 2–3% | 79 | 12% | +$1,386 | 2.4% | +1.2% | 49% |
| 3–5% | 59 | 9% | +$1,745 | 3.7% | +2.1% | 56% |
| > 5% | 14 | 2% | +$571 | 6.9% | +4.5% | **65%** |

**Capture on the trades that actually move is already 49–65%.** The money is lost in the 58%
of trades that never see +1% — and you cannot keep money a trade never showed you. This is
the same ORPHANS/MOVERS structure the futures bench found (77% orphans −$34k vs 23% movers
+$38k), now confirmed on equity. Median MFE 0.60%, median MAE −0.74%.

**② THREE FRESH TESTS, THREE REJECTIONS — the exit side is not where the cure is.**
- **Stall-cut** (the Aug 8 finding that was queued behind Fish Finder and never shipped):
  cut at T+15/30/45/60/90 when the mark is below 0 / +0.25 / +0.5%. **NEGATIVE in 14 of 15
  cells** (best −$81, one +$57 outlier). The Aug 8 +$15,639 came from *Fish Finder simulated*
  trades; on the real book a trade at −1% at T+30 still has avg MFE +0.68% left in it.
  Fish Finder is decommissioned — **de-queue this, it is dead.**
- **Add-on to proven winners** (nobody had tried exiting LATER with MORE size): at T+30,
  mark ≥ +1% → **−$327, 0 of 6 months positive.** And the control **inverts**: adding to
  trades that have NOT worked is **+$304**. ⭐ **So forward return after T+30 is negative for
  winners and positive for losers — everything mean-reverts once the first half hour is done.
  82% of a trade's lifetime peak is already reached by T+30.** The book's whole profit is
  made in the first 30 minutes.
- **Flat exit horizon** (the direct implication of the above): T+15 −$418 · T+30 +$277 ·
  T+45 −$211 · T+60 −$50 · T+90 +$70. **Sign flips between adjacent horizons = a spike, not a
  plateau.** Rejected on the same rule that killed the futures wide-stop cell.

That is **6 independent rejections** of "keep more of the peak" (trails, conditional arms,
1-min granularity, fade-exit segmentation, stall-cut, horizon). ⭐ The mechanism is now
measurable, not just asserted: **median forward MFE 0.99% vs MAE −0.83% = an edge ratio of
1.19.** The peak is mostly noise on a ~3%-ATR stock, so any rule that catches the top also
catches the noise on the trades that were going to keep running.

**③ THE COST DISEASE — nobody had applied the Sep 5 factory finding to the main book.**
`pnl` is already net (`log_trade_exit` subtracts `equity_commission`). Adding it back:

| | net | commission | **gross** |
|---|---|---|---|
| Apr | −$443 | $250 | −$193 |
| May | +$1,670 | $262 | **+$1,932** |
| Jun | −$722 | $171 | −$551 |
| Jul | −$287 | $183 | −$104 |
| Aug | −$1,199 | $664 | −$535 |
| Sep | −$603 | $111 | −$493 |
| **total** | **−$1,584** | **$1,641** | **+$57** |

**96% of trades (777 of 810) pay IBKR's $1 minimum, not $0.005/share — so commission is a
flat $2 per trade regardless of size.** Median position is **$1,312** and the median trade's
entire lifetime peak is ~1%, so **commission eats ~16% of the best moment the median trade
ever sees**; on the 114 sub-$500 positions it is 0.53% round trip. Commission roughly DOUBLES
the loss in every month. It is **not** the whole disease — gross is negative in 5 of 6 months
— but it is the half we control by arithmetic. Halving trade count at double size would take
−$1,584 to about −$763 with identical gross exposure.

**④ ENTRY-SIDE HUNT — real separation at signal level, and it FAILS at trade level.**
Corrected a measurement trap first: **`actual_day_high_pct` is the stock's day measured from
the OPEN (`database.py:1993`), not forward headroom from the signal** — it mostly re-reports
the `MIN_TODAY_GAIN` 3% entry gate (it says 94% of candidates are "movers"; the truth is
23%). Recomputed true forward MFE from bars for 2,699 enriched A+/A LONG candidates:

| feature | orphan end | mover end | monthly consistency |
|---|---|---|---|
| `burst_age_min` | >150min → **10%** mover | 30–90min → 31% | monotone, all 5 months |
| signal hour | 13:00+ → **10%** | 09:xx → 32%, 08:xx → 48% | monotone, all months |
| `vol_ratio` | <1.5× → **11%** | >10× → 44% | monotone, all months |
| `price_vs_hod_pct` | pinned at HOD → 20% | 2–5% BELOW HOD → 37–39% | **inverts the HOD-break preference** |
| `score` | 80–90 → 8.8% | 100+ → 24% | **saturated and useless** (1,995 of 2,699 are >100) |

Stacked, `fresh burst 30-90 + vol≥3x` lifts the edge ratio 1.19 → **1.56** (mover 23%→40%,
median MFE 0.99%→1.63%), 4 of 5 months beating baseline. `stale AND thin` is a 3.2%-mover
pocket (ratio 0.74).

**⚠️ DO NOT SHIP IT. The trade-level check inverts the sign:** the 76 real trades that pass
`fresh+vol≥3x` made **−$779** while the 364 that fail made **+$463**, and pass is negative in
5 of 6 months. ⭐ **Mechanism, and it is the finding that matters: the filter raises MFE
(0.99→1.63) and MAE (−0.83→−1.04) TOGETHER, and our exit stack is stop-dominated, so a better
opportunity realises a worse outcome.** Better candidate ≠ better trade under this exit stack.
Same class as the "capture is a trap" lesson, running in reverse.

**⑤ EXIT-STACK SCALE MISMATCH (documented, not acted on).** Every protective threshold sits
outside the distribution the book lives in: `PCT_TRAIL_ACTIVATE` 1.5% vs median peak 0.99%
(arms on ~30% of trades) · partial exit at +5% when **2.1% of trades ever reach it — it has
fired 5 times in 818 trades** · break-even stop at +2.5% (23% of trades) · ATR trail at
+1 ATR ≈ +2% · hard stop 5% vs median MAE −0.83% (the adaptive exits, not the stop, are what
actually close losers at −1.14% avg). Tightening any of them has been tested and loses,
because the few trades that do run are the entire profit. Recorded so nobody re-derives it.

**⑥ ENGINE SCOREBOARD (all four alive and scanning, verified live 11:30 ET Sep 18).**

| book | live P&L | n closed | own exits | read |
|---|---|---|---|---|
| `auto_trader` | −$603 Sep, −$1,584 life | 810 | 100% | strategy is being measured; negative gross in 5 of 6 months |
| Wave Rider | **−$893** | 14 | **8** | 5 of 8 own exits are the 8% stop; AEHR/MRVL/ARM were one correlated semis cluster entered the same day (−$676) |
| Contrarian | **−$375** | 4 | **2** | both own exits are −3.5% 5-day time exits |
| Clockwork | **−$555** | 70 | 70 | **shadow was +$1,034 over 150 — the live book is −$7.93/night.** First real read of MOC/MOO fills, and the shadow edge did not survive them (and 83% of that shadow edge was one MRNA event) |

Wave Rider and Contrarian still have **8 and 2** own-exit trades — their P&L describes
plumbing, not strategy. Clockwork is the one with enough nights to say something, and what it
says is that the auction-fill cost ate the edge. Equity SHORT book confirmed dormant since
Aug 14 (decommissioned); LONG-only across all four.

**⑦ SHIPPED — `collect_bars.load_bars()` date-boundary bug (third in this family).**
`ts_utc` holds two string shapes and the bound was built with `isoformat()`, so the
comparison was made against a `'T'` separator while modern rows use a space. **`'T'`(0x54) >
`' '`(0x20), so every space-format row on the START day compared BELOW the lower bound and
was dropped, while rows on the END day compared below the upper bound and leaked in** — the
first requested day vanished, the last was included despite `end` being documented exclusive,
and a single-day request returned **nothing at all**. All-`T` before Aug 4 2026, all-space
after, so it bites hardest on the newest data. Three research files already carried local
workaround comments ("its range handling is off by a day") and `wave_rider.py:342` carries
"single-day returns empty" — **the symptom was known and the cause was never found.**
Fixed by normalising both sides to `'YYYY-MM-DD HH:MM:SS'` (`_to_cmp_str` + a `replace(substr(
ts_utc,1,19),'T',' ')` predicate); docstring now states that a date-only `end` is midnight so
`start == end` is a zero-width window. Verified: start day returns, end day excluded,
T-format and the Mar–Aug overlap window both still load, 18 ms per call.
**Blast radius checked: `futures_bars_5m` and `bars_1m` are 100% T-format, so no futures
research is affected.** Live impact measured, not assumed: Clockwork's `consistency_signal()`
asks for `end=today` and was silently including today — **0 of 92 scores change and the
top-10 is identical**, because `bars_5m` only fills after the close, so nothing had leaked at
run time. Behaviour-neutral live; it fixes research windows and honours the documented
contract.

**VERDICT ON THE USER'S QUESTION.** *"If we keep most of the money a trade sees, we win."*
Measured and **false for this book**: capture on the movers is already 49–65%, the losses sit
in trades that never had a peak, and the peak on the median trade (0.99% against a 0.83%
adverse swing) is noise rather than an achievement. **Do we have an edge? No — gross is
+$57 across 810 trades and negative in 5 of 6 months; May alone carries it.** That is the
same verdict as [[equity-regime-diagnosis-aug14]], now with the exit side eliminated as a
suspect and the cost side quantified.

**OPEN / NEXT.** (a) **Minimum position size** is the one arithmetic win available and needs
no edge: 172 of 440 traced trades were under $1,000 against a flat $2 fee. (b) The
`fresh+vol≥3x` inversion deserves the real harness — `equity_replay.py` A/B, not a signal-level
correlation — specifically to test whether it works *paired with a wider stop*, since the
filter's cost is MAE. (c) `score` is saturated (A+ threshold 80, virtually everything >100):
the grader no longer grades. (d) Clockwork's live-vs-shadow gap is the most informative number
any engine has produced — measure realised MOC/MOO slippage per trade before judging it.
(e) CLAUDE.md's Active Work Board predates the Sep 10–11 session (order-status fix, peak
measurement fix, `FADE_EXIT_ON_RIDE`) and should be refreshed.

---

## Sep 18 2026 (pm) — Freshness gate A/B REJECTED · commission is a MODEL not a bill · sizing is a multiplier

**① FRESHNESS GATE — A/B RUN, REJECTED.** `equity_replay.py` (real live decision chain),
Aug 1 → Sep 17, three arms via the new default-off `auto_trader.FRESHNESS_GATE` +
`--freshness-gate`:

| arm | trades | WR | P&L |
|---|---|---|---|
| 0 — off (= live) | 235 | 30.6% | **−$1,061** |
| 1 — block stale AND thin | 235 | 30.6% | **−$1,061** (byte-identical: a **NO-OP**) |
| 2 — block stale OR thin | 225 | 31.6% | **−$1,124** (12 trades cut, **$63 worse**) |

Arm 1 changed nothing because **`MIN_VOLUME_RATIO=1.3` already hard-blocks thin volume under
$100** and the threshold is 1.0 above it, so "stale AND thin" never reaches an entry. Arm 2
cuts the thin-volume trades, which trade-level data had already shown were not the losers.
⭐ The signal-level separation is REAL (3.2% vs 23.4% mover rate) and **still does not
convert** — the same wall every entry-side idea in this repo has hit: candidate quality and
realised P&L are not the same axis under a stop-dominated exit stack. Flag kept as a disabled
research hook with the verdict in its own comment; **do not re-run this gate on a different
window and call it new evidence.**

**② COMMISSION IS A MODEL WE INVENTED, NOT A BILL — verified on a live session.**
`GET /executions` during Sep 18 trading: **33 of 33 fills charged $0.00**, with the
`commissionReport` present (a real zero, not a missing value). IBKR does not charge the paper
account. So the entire **$1,641** of "commission" in trades.db is a Sep-3 model that had never
been checked. Modelling it is correct — paper P&L should forecast a funded account — but the
plan must match the one the real account is on, and **the API cannot report that**.

At this book's **median 14 shares/trade the per-share rate is irrelevant — the minimum binds
on 96% of trades** — so plan choice is a straight 2.6× on friction:

| IBKR Pro plan | per share | min/order | our round trip | 810-trade total |
|---|---|---|---|---|
| **FIXED** (what we model) | $0.005 | **$1.00** | $2.00 | **$1,641** |
| **TIERED** | $0.0035 | **$0.35** | **$0.77** | **$621** |

**⚠️ The live account (`trading-prod/trades.db`) has NEVER placed an order** — 0 rows,
untouched since May 24 — and IBKR serves executions for the current session only, so there is
**nothing to measure**. Also found: `trading-prod/bridge.py` is from **Jun 5**, three months
stale, and has no `/executions`; the prod bridge runs but `connected:false` (its gateway is
deliberately down, pre-flight item #10). Shipped: `database.IBKR_COMMISSION_PLAN`
(`'FIXED'|'TIERED'`) + `_IBKR_SCHEDULE`, default FIXED so **nothing changes** until confirmed
from IBKR Client Portal. ⚠️ Not cosmetic — realized P&L feeds `MAX_DAILY_LOSS` and
`peak_session_pnl`, so a plan switch moves those by ~$1.20/trade.

**③ FLAT SIZING — REJECTED. Sizing is a multiplier, not an edge.** Last session's +$1,327
was a whole-book number and it does not survive a period check:

| window | gross as-traded | gross at flat $2,000 | |
|---|---|---|---|
| Apr–Sep (all) | +$579 | +$2,818 | amplifies a WIN |
| May only | +$1,926 | +$3,154 | amplifies a WIN |
| Jun–Jul | −$670 | −$404 | amplifies a LOSS |
| **Aug–Sep** | **−$484** | **−$590** | **amplifies a LOSS** |

⭐ **No sizing change can help while gross expectancy is negative — it only magnifies the
sign that is already there.** The whole-book gain was May being carried into every bucket.
Same aggregate-hides-the-period trap this file has flagged repeatedly.

**④ WHAT SURVIVES: the catalyst up-size, and it is sign-independent.** `get_position_capital`
gives `is_catalyst` the most capital, and catalyst is the worse cohort in **all four months
with data** — and bigger in all four:

| month | catalyst n / gross% / size | non-catalyst n / gross% / size |
|---|---|---|
| Jun | 42 / **−0.614%** / $2,093 | 41 / +0.157% / $1,739 |
| Jul | 20 / **−0.371%** / $1,135 | 49 / +0.134% / $893 |
| Aug | 20 / **−0.417%** / $1,126 | 9 / −0.466% / $862 |
| Sep | 17 / **−0.835%** / $1,925 | 12 / −0.231% / $1,690 |

Sizing catalyst the same as non-catalyst is worth **+$428** full book / **+$169** Aug–Sep.
Unlike flat sizing this is a *relative* reallocation, so it does not depend on the book's
overall sign. **⚠️ equity_replay CANNOT test it** — the harness has no catalyst/sympathy
scanning at all (`is_catalyst` is False throughout, a documented v1 gap), so this needs the
live book or a new harness.

**⑤ A POSITION-SIZE FLOOR IS WRONG — retracting the Sep 18 (am) recommendation.** Cutting
every trade under $1,000 removes 296 trades worth **+$209 net**; small positions had the
*best* gross return (+0.54%/trade under $500) and large ones the worst (−0.43% over $2,000).
The floor sweep is non-monotone ($400 +$37, $700 −$10, $1,000 −$209, $1,400 +$340) = noise.
The fee is flat per trade, so a floor deletes good trades without saving proportional money.

**⑥ WHAT CAN AND CANNOT BE REPLAYED — decision map, so this is not re-litigated.**
- **Fee plan: no replay, and a replay would be WORSE.** It changes no decision, so it is exact
  arithmetic on the trades we took. Replaying it would only add simulation error.
- **Sizing: no replay needed** for the first-order effect (same trades, re-weighted); only the
  "did capital run out for a later entry" second-order effect needs one. Verdict is already no.
- **Catalyst de-weight/removal: replay IMPOSSIBLE** (no catalyst path in the harness).
- **Freshness gate: replay REQUIRED and DONE** — it changes selection, and slot redistribution
  is the only thing arithmetic cannot model. Rejected above.

**OPEN / NEXT (unchanged priorities from the am session, minus the two now-rejected ones):**
(a) fix the scan_log label — `actual_day_high_pct` measures the stock's day from the OPEN, so
148,388 enriched rows are scored against the wrong outcome; true forward MFE/MAE is
backfillable from bars, no waiting. (b) make the grade a RANK — 138,139 SKIP / **3,694 A+** /
92 A / 30 B / 8 C means the grader is a stamp, and the book must choose 5 from ~16 A+ every
day with no way to order them. (c) all 11 sectors are graded WEAK on 5–35 trades — the sector
modifier is now a uniform −20 and has stopped discriminating; the learner has emitted
identical weights (1.0/1.7/1.0/1.0/1.0) for 8+ days. (d) confirm the IBKR plan from the
portal. (e) selection skill measured at **+0.25pp forward MFE vs the pool we pass over
(t=+1.17, p=0.25)** — real, not significant, and smaller than the friction it pays.

---

## Sep 18 2026 (pm2) — The grader is provably not a ranker · score components + correct label now LOGGED

User asked to make grade_setup rank candidates instead of stamping them, so the book can pick
the top 5 of ~16 A+ a day. Labs: `research_equity_rank_lab.py`, `research_equity_component_lab.py`,
`backfill_scan_forward.py`. **Nothing that affects a trading decision changed.**

**① THE SCORE CARRIES NO RANK INFORMATION — measured, not asserted.**
Built a per-candidate outcome by replaying auto_trader's own exit stack (hard stop, PCT trail,
ATR trail, momentum fade, VWAP cross, EOD, hold-to-close suspension) on each candidate's own
forward bars, then charging commission because `trades.pnl_pct` is NET (`database.py:610`).
Label validation against the 283 candidates we really traded: **corr +0.668**, mean sim
−0.028% vs real −0.106%. ⚠️ Win rate 57.6% sim vs 45.6% real — the sim still lacks L3
stop-to-BE, the dollar circuit breaker, no-move and regime-flip exits, and slippage.
**Per the Aug 24 rule (correlation is not a calibration gate) this label is trusted to RANK
— which needs only monotonicity — and NOT to forecast levels.**

| score bucket | n | sim net % | mfe | mae |
|---|---|---|---|---|
| 80–100 | 233 | −0.031 | 1.18 | −1.33 |
| 100–115 | 182 | +0.040 | 1.43 | −1.30 |
| 115–130 | 283 | +0.285 | 2.00 | −1.47 |
| 130–150 | 481 | +0.158 | 2.08 | −1.46 |
| **150+** | **1,447** | **+0.126** | 2.07 | −1.78 |

**Spearman score vs outcome = +0.072 (simulated), +0.088 (real).** The score rises with
liveliness — mfe climbs 1.18 → 2.07 — but **mae climbs with it (−1.33 → −1.78)**, so it is a
volatility detector, not a profitability ranker. It also stops helping above ~130 while 54% of
candidates sit above 150.

**② WHY IT COULD NEVER HAVE BEEN TUNED: the components were never written down.**
`grade_setup` returns `(grade, reasons, score)` and `log_scan_candidate` stored only the
TOTAL. For A+ rows `skip_reason` is literally "Qualified — awaiting slot". **3,694 A+
candidates logged over five months, and not one has its breakdown** — so ORB +30, VWAP reclaim
+25, bull flag +25, HOD break +20, vol +25, RSI +20 have never once been scored against an
outcome. You cannot tune 15 weights when none of the 15 was recorded.

**③ FEATURE HUNT ON WHAT *WAS* LOGGED — nothing survives.** Scored every logged feature by
WITHIN-DAY rank (the real decision is "which 5 of today's 16", so a cross-day correlation is
mostly the market's mood). Only two cleared ≥5/6 months and |rho|≥0.05: `atr_pct` (+0.218,
**6/6**) and hour-of-signal (−0.100, 5/6). **Both then FAILED against real trades**: atr_pct
+0.228 simulated → **+0.093 real**, hour −0.132 simulated → **+0.020 real**. atr_pct is largely
an artifact of fixed-% exits in the simulation — a high-ATR stock arms a 1.5% trail more often
— which is the equity, cross-sectional twin of the Aug 24 futures units finding: *whether a
trade can arm its trail is set by volatility, not by setup quality.*

**④ FIRST REAL COMPONENT EVIDENCE — reconstructed from bars, same definitions as
`get_intraday_signals` (auto_trader.py:1556/1579/1617). Metric is `fwd_mfe + fwd_mae`
(favourable move NET of the adverse swing), on 2,666 candidates:**

| component | weight | n on | edge ON | edge OFF | delta | months better |
|---|---|---|---|---|---|---|
| **VWAP reclaim** | +25 | 78 | **+0.685** | +0.167 | **+0.518** | **5/5** ✅ |
| ORB breakout | **+30** | 1,434 | +0.079 | +0.296 | −0.217 | 3/6 |
| above VWAP | +10 | 2,333 | +0.170 | +0.269 | −0.099 | 3/6 |
| **HOD break** | **+20** | 542 | **−0.084** | +0.250 | **−0.334** | **1/6** ❌ |

**HOD break survives every control**: negative in 5 of 6 months, negative inside every
hour bucket (−0.630 / −0.057 / −0.135), and negative independently of ORB (−0.299 within
orb=1, −0.314 within orb=0). It is a **−0.288 hit to forward MFE**, i.e. those candidates
simply go up less. This is the second independent route to the same conclusion — the
`price_vs_hod_pct` sweep also found candidates pinned at the day high are the WORST movers
(20%) and those 2–5% below it the best (37–39%). **We pay +20 points for buying the high.**
⚠️ **AND IT INVERTS ON REAL TRADES**: the 27 real trades with hod_break made **+0.220%** vs
−0.182% for the 225 without. n=27 settles nothing, but it is the same failure-to-transfer that
killed every other signal-level finding this session. **NOT SHIPPED. No weight was changed.**

**⑤ SHIPPED — instrumentation only, no decision touched.**
- `scan_log.score_components` (TEXT/JSON) — `log_scan_candidate(..., reasons=...)` now parses
  grade_setup's reason list into `{canonical component: points}`. Keys are canonicalised
  (`2.6x vol` / `11.4x vol` → `vol`) so they aggregate; verified stable across values. Most
  values are `null` because grade_setup only prints points for penalties — **that is fine and
  is the point: presence/absence of each component is the regression feature, and the fitted
  coefficients ARE the new weights.** ⚠️ It parses prose, which is fragile; if anyone reworks
  `grade_setup`, have it emit a dict directly and delete `_canon_component`.
- `scan_log.fwd_mfe_pct` / `fwd_mae_pct` / `fwd_window_min` + **`backfill_scan_forward.py`**,
  the correct learning target: excursions from the SIGNAL price, direction-signed.
  **Backfilled 145,488 rows over all history** (LONG mfe +0.948 / mae −0.905, mover 11.5%;
  SHORT mfe +1.319 / mae −1.014, **mover 21.1%** — the decommissioned short book has the
  better forward edge of the two, worth a look). This replaces `actual_day_high_pct`, which
  measures the stock's day from the OPEN and reports 94% of candidates as movers against a
  true 23%.
- ⚠️ **`auto_trader` NOT RESTARTED** (market open, SECZ still held; entry window shuts at
  13:00 so nothing was lost today). **Component logging starts at the next restart —
  `launchctl kickstart -k gui/$(id -u)/com.sushil.trading.autotrader` after the close.**

**⑥ WHERE THIS LEAVES THE RANKER.** Every signal-level finding this session separated
candidates well and then failed to transfer to realised P&L — freshness gate (A/B, rejected),
fresh+vol≥3x (inverted), atr_pct (2.4× overstated), hour (vanished), HOD break (inverted at
n=27). The common cause is now clear and is the thing to fix next: **we can only rank
candidates on forward price behaviour, and this book's realised outcome is dominated by a
stop-based exit stack whose thresholds sit outside the distribution those candidates move in.**
Until real trades carry their own component breakdown there is no way to fit weights against
what we actually earn — which is exactly what ⑤ now makes possible. **Re-run
`research_equity_component_lab.py` and the regression once ~4–6 weeks of component-logged
trades exist; do not hand-tune a weight before then.**

---

## Sep 18 2026 (pm3) — Exit re-scale REJECTED (7th) · engine diagnosis · two of my own claims corrected

**① PARTIAL SCALE-OUT RE-SCALED — REJECTED, and monotonically.** The one exit mechanism that
WORKED on futures (Jul 25: bank 1 of 2 contracts) and that equity has had wired but dead
(+5% threshold, fired 5 times in 818 trades). Re-scaled to the real distribution and replayed
on 431 real LONG trades' own bars (`research_equity_exit_scale_lab.py`), banking half and
letting the runner finish where the trade really finished:

| partial at | delta vs actual | fired | months better |
|---|---|---|---|
| 0.75% | **−$383** | 215 | 3/6 |
| 1.0% | −$340 | 188 | 2/6 |
| 1.5% | −$331 | 141 | 2/6 |
| 2.0% | −$322 | 105 | 0/6 |
| 3.0% | −$89 | 47 | 0/6 |
| 5.0% (≈today) | +$4 | 12 | 1/6 |

**Monotone: the closer to the current setting, the less harm.** There is no scale at which
banking half helps — half a winner costs more than it saves on the faders, because the winners
are too few. **7th rejection of touching the exit.** ⭐ The unifying principle holds on equity
exactly as on futures: *more room helps, cutting earlier hurts* — and "partial" is not an
exception to it. **The exit question is closed. Do not reopen without a new mechanism.**

**② ENGINE DIAGNOSIS — and TWO CORRECTIONS TO MY OWN Sep 18 (am) CLAIMS.**
- ❌ **RETRACTED: "Clockwork's shadow edge did not survive real fills."** Unsupported.
  SHADOW ran Aug 17 – Sep 4, LIVE runs Sep 9 – 17 — **zero date overlap**, so the −1.38pp/trade
  gap is confounded with the market fortnight, not attributed to MOC/MOO fills. Also **2 of 7
  nights carry 117% of the loss** (Sep 11 −$421, Sep 9 −$230; the other five run −$44…+$145).
  n=7 nights. Not evaluable either way.
- ❌ **RETRACTED: the short book as a hidden edge.** At A+/A grade SHORT is WORSE than LONG
  (edge ratio **0.942 vs 1.150**). The earlier 21.1% mover figure came from ungraded rows, and
  **SHORT candidates are 99.8% sampled in WEAK regime** — within WEAK, SHORT 1.301 vs LONG
  1.248, i.e. the apparent gap was almost entirely regime sampling.
- **Wave Rider: the 8% stop realises −10.64%.** AEHR −15.26 · MRVL −9.68 · ARM −9.80 ·
  HUT −8.71 · COIN −9.75. It holds 3 days, so overnight gaps jump the stop. **Not a
  tighter-stop problem — an overnight-exposure/size one.** Its only profits are the 3 time
  exits (+$207, avg +3.62%); the 5 stops are −$1,033. 9 of its 14 entries landed on one day.
- **Contrarian: 2 own exits.** Nothing to diagnose.

**③ WEAK-REGIME LONGS and UNKNOWN-REGIME — both confounds, both resolved.** LONG edge ratio by
regime looks like WEAK 1.248 > STRONG 1.097 > NORMAL 1.010 > CAUTIOUS 0.917 > CHOPPY 0.838
(so CHOPPY/CAUTIOUS being blocked is the system working). But **WEAK-LONG rows exist only in
Aug–Sep** (post the Sep 6 REGIME_AS_MODIFIER change) and the 12 real WEAK trades ran −0.475%
at an 8% win rate. And the one positive real bucket, `UNKNOWN` regime (163 trades, +$276,
55% win), is **entirely Apr 15 – May 22** — it is a legacy tag for "May", not a signal.

**④ HORIZON — the ratio improves, the consistency does not.** Same A+ candidates, same
signals, longer window (`research_equity_horizon_edge.py`, n=2,446):

| horizon | mfe | mae | ratio | buy&hold | months +ve |
|---|---|---|---|---|---|
| 2h | 1.36 | −1.22 | 1.117 | +0.116% | 2/6 |
| **1d** | 4.44 | −3.49 | **1.274** | +0.391% | 3/6 |
| **3d** | 6.67 | −5.44 | 1.226 | **+0.808%** | 2/6 |
| 5d | 8.35 | −6.81 | 1.225 | +0.373% | 2/6 |
| 10d | 12.09 | −9.60 | 1.260 | −0.086% | 2/6 |

The Aug-14 "we day-trade a swing edge" read is **half right**: the ratio does lift 1.12 → ~1.25
once you hold past the session. But every horizon is still carried by May (2–3 of 6 months) and
the swings triple (mae −1.22 → −5.44). **And we already run that experiment — Wave Rider is a
3-day-hold engine and is −$893.**

**⑤ THE BOTTOM NUMBER, stated plainly.** Across 145,488 labelled rows the entire logged
universe has a forward edge ratio of ~1.04, and A+ grading lifts it to ~1.15. That +0.11 is
the whole of our selection skill, at every horizon tested. Against a flat $2 fee on a $1,300
position it does not survive. **The material is close to symmetric; no gate, exit, horizon or
sizing rule tested this session changes that.** The only untried lever left on this book is
fitting the 15 grader weights to outcomes — instrumented today, needs ~4–6 weeks.

---

## Sep 18 2026 (pm4) — Components BACKFILLED, weights FITTED: we systematically buy extension

User pushed back on "wait 4-6 weeks for live component data" — correctly. Most of
`grade_setup`'s inputs are price-derived and therefore reconstructable. They were.

**① `backfill_score_components.py` — grade_setup's inputs rebuilt at signal time.**
Point-in-time by construction: `df1d` aggregated from 5-min bars with the signal day as a
PARTIAL bar (what live reads intraday), `df5` truncated to the trailing 5 sessions like live's
`history(period='5d')`. **2,666 of 2,699 A+/A LONG candidates reconstructed.**
**CALIBRATION GATE PASSED — and this time properly:** `intra_chg` reproduces what live logged
**exactly (corr +1.000, median |diff| 0.00, 100% within 1pp)**. `rsi` is corr +0.761 /
median |diff| 0.80 / 87% within 3 points — live reads IBKR daily bars, the reconstruction
aggregates DataBento 5-min, an irreducible ~1-3pt gap; treat reconstructed RSI as noisy.
**Found and fixed a definition error before trusting anything**: `today_gain` is *today's
partial close vs yesterday's close* — verified by testing three candidate definitions against
the ≥3% gate that every graded row must clear (98.3% vs 34.5% vs 72.4%).
**NOT reconstructable, and why — these are point-in-time STATE, not price:** `is_catalyst`
(IBKR scanner output, never persisted), pre-market high (`bars_5m` starts 09:30 ET),
sector grade / strategy weights (learner state as of that morning), sympathy triggers, VIX
intraday. Deriving any of them from today's data would leak the future. ⚠️ Also confirmed:
**`bars_1m` stopped collecting 2026-08-04** (1,838 rows in Aug vs 2.0M in May).

**② `research_equity_fit_ranker.py` — the weights FITTED, walk-forward.** Ridge on 16
features, demeaned WITHIN DAY (the decision is "which 5 of today's ~16"; a cross-day
correlation is the market's mood), fit on all months before the test month, two independent
targets. **15 of 16 features agree in sign across both targets:**

| we PAY most for | current weight | fitted (edge / sim_net) |
|---|---|---|
| `today_gain` | **+10/20/30** | **−0.239 / −0.144** ← most negative feature |
| `hod_break` | +20 | −0.188 / −0.081 |
| `is_bull_flag` | +25 | −0.153 / −0.080 |
| `intra_chg` | batting-order sort, DESC | −0.044 / −0.016 |
| `is_tight` | +10 | −0.035 / −0.094 |

| we UNDER-pay for | current weight | fitted |
|---|---|---|
| `rsi_5m` | **−10 if >75** | **+0.146 / +0.045** |
| `fvg_count` | +10/20/30 | +0.141 / +0.023 |
| `range_pct` | **unscored** | +0.071 / +0.096 |
| `vwap_reclaim` | +25 | +0.071 / +0.045 |
| `orb_break` | **+30** | +0.005 / +0.039 (≈nothing for the biggest weight) |

⭐ **ONE COHERENT READING: every feature measuring "already extended" is negative, every
feature measuring "room left" is positive — and FOUR separate mechanisms all push the book
toward the most-extended name**: the ≥3% hard gate, the today_gain bonus, the HOD/bull-flag
bonuses, and the batting order's `intra_chg` DESC sort plus its at-HOD preference.
**We are systematically buying extension and paying a premium for it.** This is the fourth
independent route to the same place — `price_vs_hod_pct` (pinned at the high = 20% movers vs
37-39% for 2-5% below), the HOD-break component test (−0.334, 1/6 months), and grade_setup's
own STRONG-day exhaustion gate all said it first.

**OOS decision test (top 5 per day, walk-forward):** fitted beats the current score on
**62% of days on BOTH targets**, and in 4/4 months (edge) / 3/4 (sim_net).
⚠️ **But the absolute level stays NEGATIVE** (−0.291 vs −0.594 on edge; −0.216 vs −0.393 on
sim_net). **This is "lose less", not "make money".** OOS rank correlation is only +0.051
(edge) and +0.021 (sim_net, where the current score actually scores higher at +0.062).

**③ SHIPPED — `EXTENSION_TILT` A/B switch, default 0 = live unchanged.**
`1` zeroes the four extension bonuses (the ≥3% hard gate untouched — eligibility does not
change, only ranking); `2` also reverses the batting order's two extension rungs. Unit-tested:
a maximally-extended candidate scores 257 at tilt=0 and 172 at tilt=1, **still A+ in both** —
which is the whole point. `equity_replay.py --extension-tilt N`.

**❌ A/B RESULT — REJECTED (8th).** equity_replay, Aug 3 → Sep 17, read off the replay DB
(see the bug below — the printed total was wrong):

| arm | n | WR | total | vs live |
|---|---|---|---|---|
| 0 — live today | 264 | 35.6% | **+$155** | — |
| 1 — drop the four extension bonuses | 280 | 33.6% | **−$81** | **−$236** |
| 2 — + flip the batting order | 278 | 33.5% | **−$148** | **−$303** |

Worse in BOTH months and BOTH variants (Aug +$177 → −$16 / −$97; Sep −$22 → −$64 / −$51).
The re-rank did what it was built to do — 29-45 of the picks changed — it simply did not help.
⚠️ **But t=+0.36, p=0.720: the gap is indistinguishable from noise.** Honest verdict is "no
evidence it helps", not "proof it hurts". **Not shipped; flag left at 0.**
⭐ So the fitted weights joined every other finding this session: real and month-consistent at
signal level, gone once the full pipeline and its exit stack are in the loop. That is now
**8 for 8** — and the consistency of that result is itself the most reliable thing measured.

**⚠️ BUG FOUND AND FIXED — `equity_replay.py`'s printed total was wrong, a REGRESSION of the
Aug 6-7 2026 fix.** Line 613 summed per-day `get_daily_pnl()` snapshots, each filtered on
`entry_date = that day`, so a trade entered day N and closed day N+k is invisible to BOTH —
day N's snapshot (still open) and day N+k's (did not enter that day). Measured here: **29
multi-day trades worth +$1,216 dropped, printing −$1,061 for a true +$155.** The total is now
re-derived from the replay DB, and it prints an explicit note when the snapshot sum disagrees.
⚠️ **This means every A/B run before the fix was read on a biased basis, including this
session's freshness-gate A/B.** That one's core conclusion survives anyway — gate=1 was
*byte-identical* to gate=0 (same trade count, same P&L), so "no-op" holds on any basis — but
its gate=2 margin (−$63) was read off the broken number and is no longer trustworthy. The
arms' biases are not equal, because each holds a different number of multi-day trades.

**④ WHY NO SHORT TRADES — a real defect, found by the user's question.** Two failures stacked:
the bear book was RETIRED Aug 15 (Alpha Factory), and that retirement lives inside
`elif not REGIME_AS_MODIFIER and regime == 'WEAK'` — while `REGIME_AS_MODIFIER = True` since
Sep 6, so **the branch is unreachable**. The damage is not the missing trades:
**SHORT scan_log rows stop dead on 2026-07-31** (Apr 5 · May 1,211 · Jun 8,398 · Jul 17,591 ·
**Aug 0 · Sep 0**). We stopped GRADING short candidates, so the retirement decision can never
be re-examined for any period after July. That is exactly the defect the same function's own
docstring warns about ten lines below — *"a gate that stops trading must never also stop
measuring, or the data needed to judge the gate is destroyed by the gate itself."*
**SHIPPED (user-approved, same session): `BEAR_OBSERVE_ONLY = True`.** `_scan_and_enter_bear`
gained `observe_only` mirroring `_scan_and_enter`'s contract, and run_scan now calls it every
scan inside the entry window — OUTSIDE the `if/elif` routing chain, so no regime branch can
make it unreachable again. Verified: exactly ONE order path exists in that function and it
sits inside `for pick in (candidates if _short_book_on else [])`, which `observe_only` empties
— zero orders are possible regardless of book state.
⚠️ **Found and fixed the same defect INSIDE the function**: three early `return`s (daily-loss
brake, afternoon gate, recycled-slot gate) sat BEFORE the grading block, so on a bad day the
short side would have gone dark again. All three are now `if not observe_only:` — a
capital-protection brake must never stop measurement when no capital is at risk.
**Cost:** one extra signal pass over 241 symbols. The bear scan runs first and does the
`prefetch_df5`, which the long scan then reuses inside `DF5_BATCH_TTL=150s`, so it is one
extra compute pass, not an extra fetch. Current cycle is 333s of a 300s interval with ~33s of
work — headroom is large, but **confirm the Monday cycle time**.
Short TRADING stays retired: at A+ grade the short side measured WORSE (edge ratio 0.942 vs
1.150). autotrader restarted and verified (market closed, clean startup, 0 positions).

---

## Sep 20 2026 — STRATEGY HUNT: we have been trading the dead half of the clock

Day-1 posture, no assumptions. `research_strategy_hunt.py` (panel + `--control/--select/--stress/--verdict/--config`).
**Nothing wired. This is a finding, not a change.**

**① THE BENCH IS BIASED — establish this before any backtest.** 113 of our 241 names were
added Jul 2026 by a screen on `bt_wr` — **backtest win rates of 88–93%** — computed over
2024-2026, the same window any backtest here uses. Measured: 2024-01 → 2026-09 median
buy-and-hold was **+95% (added) / +87% (incumbents) vs SPY +60%**. ⇒ **The only honest
benchmark on this universe is its own equal-weight buy-and-hold, never zero and never SPY.**
(Our live window Apr 15 – Sep 18 was NOT a tailwind: universe median +0.7%, 51% of names up,
mean +11.6% — against our book's −15.3%.)

**② THE FINDING — where the return actually accrues (187,731 symbol-days):**

| period | overnight bp/day | intraday bp/day | on win% | id win% |
|---|---|---|---|---|
| 2024 | **+18.47** | −2.67 | 56.5 | 49.0 |
| 2025 | **+11.44** | +5.34 | 54.9 | 50.1 |
| 2026 | **+12.85** | +2.15 | 52.0 | 49.2 |
| **ALL** | **+14.36** | **+1.60** | **54.7** | **49.5** |

**91% of the universe's return accrues overnight.** Compounded: overnight +44%/yr vs intraday
+4%/yr. Equal-weight, no selection at all: **overnight Sharpe +2.23, maxDD −19.6%, total
+160%** vs **intraday Sharpe +0.16, total +3.2%** over 2.7 years.
⭐ **All four of our equity engines trade the intraday window and close before the overnight
one.** We built the whole system on the half of the clock with a 49.5% win rate.

**③ CONTROL — it is not our universe's selection bias.** Same split on instruments nobody
selected on performance: SPY +5.98 / +2.47 · QQQ +9.22 / +0.87 · IWM +6.24 / +1.24 ·
SMH +22.81 / −1.67 · URA +23.21 / **−8.82**. Ten of eleven ETFs show it (only XLF inverts).
This replicates **Lou, Polk & Skouras (JFE 2019)**, who found on 30 years of US intraday data
that momentum's entire abnormal return is overnight (3-factor alpha 0.95%/mo, t=3.65) against
0.11% intraday. Held to **Hou, Xue & Zhang (RFS 2020)**'s bar — 65% of 452 published anomalies
fail t>1.96 once microcaps are controlled — this one survives on our own tape, in every year,
and in unselected ETFs.

**④ SELECTION INSIDE THE WINDOW HELPS, and Clockwork already has the best signal.**
Top-10 each night, held close→open, $20M ADV floor, gross:

| sort | 2024 | 2025 | 2026 | Sharpe (all) | maxDD |
|---|---|---|---|---|---|
| **gap consistency (Clockwork's own)** | +18.4 | +16.3 | +17.1 | **+2.30** | **−16.9%** |
| today's intraday gainers | +26.8 | +14.3 | +17.5 | +2.26 | −17.2% |
| equal-weight everything | — | — | — | +2.23 | −19.6% |

Gap consistency is the **most stable across years and has the best drawdown**. Name count
barely matters (5→50 all give 15.5-17.6bp) ⇒ a broad premium with real capacity, not a fragile
pick. Deflations found and kept: the effect is **largely a volatility premium** (a pure vol
sort earns +38.1bp vs the gainer sort's +38.9bp unfiltered) and **concentrated in illiquid
names** (unfiltered +38.9bp → $50M ADV floor +16.0bp). It is a RISK premium, not free money.

**⑤ WHY CLOCKWORK LOSES ANYWAY — and it is neither the signal nor the tape.**
Fee arithmetic first. 2026 gross edge **+17.12bp/night**:

| position | FIXED net | TIERED net | |
|---|---|---|---|
| **$1,000** (what Clockwork runs) | **−2.88bp** | +10.12 | **fee eats it entirely** |
| $2,000 | +7.12 | +13.62 | marginal |
| $5,000 | +13.12 | +15.72 | viable either way |

⭐⭐ **But the real killer is EXECUTION.** Matched 62 live Clockwork trades against the panel's
own 15:55 close and 09:30 open:

| | |
|---|---|
| entry fill vs 15:55 close | **+15.7bp** (we pay up to get in) |
| **exit fill vs 09:30 open** | **−77.2bp** |
| modelled overnight return | +48.0bp |
| **actually realised, fill to fill** | **−45.9bp** |
| **execution drag** | **−94.0bp per night** |

**The edge is 17bp and the execution is losing 94bp — five times the entire edge.** And it
reconciles: −94 + 17 ≈ −77bp, against Clockwork's live −64bp/trade. **The live loss is almost
entirely execution.**
Mechanism, from the exit timestamps: a true MOO fills AT the 09:30 print. Ours fill
**09:31-09:34 (47 trades, avg −0.380%)**, i.e. after the open, not in the auction — plus
**23 trades exiting late (09:46-09:48 fallback and a 10:44 cluster) averaging −1.184%**, 3×
worse. Worst cases sold 3-4% below the opening print (ACLS open 107.41 → filled 103.00;
PI open 175.55 → 170.00), several at suspiciously round numbers.

**⑥ WHAT THIS MEANS.** We do not need a new engine. We need to (a) move capital from the
intraday window (Sharpe 0.16) to the overnight one (Sharpe 2.2-2.3), (b) fund it at
$2,000-5,000 per name instead of $1,000, (c) settle the fee plan, and (d) **fix the auction
execution, which is currently destroying 5x the edge.** (d) is plumbing and comes first —
until exits actually clear in the opening auction, no overnight configuration can be judged.

---

## Sep 20 2026 (pm) — Clockwork resized + fee plan settled + the paper simulator caught faking auction fills

**① ⭐⭐ THE −94bp "EXECUTION DRAG" IS LARGELY AN IBKR PAPER ARTIFACT — I had this wrong
earlier today and the correction matters.** Our order path is CORRECT: MOO is `MKT` with
`tif='OPG'` (bridge.py:524), submitted 09:01, and the fills land at **09:30:01** — the auction
path works. But the prices are fabricated. Sep 18, ten MOO sells vs our own 09:30 bar:

| symbol | filled | that bar's open / high / low | |
|---|---|---|---|
| PI | **179.00** | 182.29 / 182.29 / **182.29** | never traded there |
| ACLS | **105.00** | 106.15 / 106.15 / **105.61** | never traded there |
| VST | **143.00** | 143.40 / 144.45 / **143.20** | never traded there |
| SMTC | 180.00 | 182.33 / 182.89 / 179.10 | whole dollar |

**Four of ten filled at whole-dollar prices and three were outside the stock's actual traded
range.** That is not an opening auction print; it is the paper simulator inventing a number.
The non-round fills average near zero, which is what a real auction fill looks like.
⇒ **Clockwork's live P&L has never been a measurement of its strategy.** Same family as the
Sep 3 finding that the two paper gateways fill ~15pts apart on identical signals.
**Deliberately NOT "fixed" by switching to LIMIT orders** — MOC/MOO is the correct construction
for real trading, and making paper look better by trading differently than we intend to live
would be measuring the wrong thing. Instrumented instead (③).

**② SHIPPED — the configuration the data supports.**
- **Clockwork: 10 x $1,000 → 3 x $3,000**, on its OWN $10,000 (not taken from the intraday
  book). Measured on 673 nights, $20M ADV floor, gross: 3 names +21.4bp / Sharpe 2.25 /
  maxDD −13.2% · 5 +17.6 / 2.25 / −16.5 · 10 +17.2 / 2.30 / −16.9 · 20 +16.9 / 2.40 / −16.3.
  The gross edge is nearly flat in name count, so concentration buys the fee saving almost
  free — and the **worst single night was better at 3 names (−637bp) than at 10 (−784bp)**.
- **`IBKR_COMMISSION_PLAN = 'TIERED'`** (user-directed). At 14 shares/trade only the per-order
  minimum binds, so the plan is a flat 2.6x on all friction: $2.00 → $0.77 round trip.
  ⚠️ **This is a MODELLING assumption. IBKR does not expose the plan through the API and the
  live account has never traded, so it cannot be verified from here — enroll in Tiered in
  Client Portal before funding, or the model understates real cost by ~$1.30/trade.**

**⭐ THE OLD CONFIG COULD NOT HAVE WON.** Same signal, same nights, official prints:

| config | bp/night | Sharpe | maxDD | on $10k/yr | 2024 / 2025 / 2026 |
|---|---|---|---|---|---|
| OLD 10 x $1,000, FIXED | **−2.78** | −0.37 | −31.7% | **−$677** | −1.6 / −3.7 / −2.9 |
| NEW 3 x $3,000, TIERED | **+19.09** | **+2.01** | −13.7% | **+$5,553** | +13.8 / +22.4 / +21.0 |

**Negative in all three years at the old sizing; positive in all three at the new one.** The
fee exceeded the edge every year. This was never a strategy problem.

**③ SHIPPED — `backfill_overnight_reference.py` + launchd `com.sushil.trading.overnight_reference`
(17:05 weekdays, after collect_bars writes the session).** Adds `ref_entry` / `ref_exit` /
`ref_pnl` / `exec_drag` to `overnight_trades`: every night the book is marked BOTH at the
broker's fill and at the session's official close/open from our own bars, so strategy and
execution can never be conflated again. Backfilled 216 rows:

| mode | n | actual | **strategy** | exec_drag |
|---|---|---|---|---|
| LIVE | 70 | **−$554.89** | **+$223.12** | +$778.01 |
| SHADOW | 146 | +$1,049.22 | +$798.47 | −$250.75 |

**Clockwork's seven live nights were PROFITABLE at the official prints (+$223).** The −$555 is
the simulator. (SHADOW's gap is the reverse sign and equals its modelled fees — an arithmetic
check that the calculation is right.)

**④ REPLAY of the actual live picks, three ways** (`research_clockwork_replay.py`, same
symbols, same nights, no re-selection): as filled **−$555** · official prints at live size
**+$132** · official prints at $3,000/TIERED **+$780**. Execution +$687, sizing and fees +$648.

**OPEN.** (a) `TOP_N=3` needs no restart — launchd runs `python -m factory.live.overnight`
fresh every 300s. (b) **Clockwork still holds 10 positions entered before the resize**; they
unwind naturally at the next open. (c) The real question is unchanged and unanswerable on
paper: **does a live MOC/MOO fill at the official print?** Until a funded account places one,
`ref_pnl` is the honest read on this book and `pnl` is not.

---

## Sep 20 2026 (pm2) — Clockwork audit: one real bug, one missing filter, two traps declined

**TOMORROW'S SEQUENCE (Mon Sep 21), confirmed by tracing `run_once`:** 09:00-09:27 MOO SELL
submitted for the **10 positions still open** ($9,593 notional, entered Sep 18) → 09:31-09:59
fills confirm → **15:40-15:49 MOC BUY for the new TOP_N=3 at $3,333 each** → 16:00-16:40
confirm. No restart needed; launchd runs `python -m factory.live.overnight` fresh every 300s.
Dry-scan for tomorrow: **CENX, NUTX, P** (all 70% consistency).

**① 🐛 REAL BUG FOUND AND FIXED — capital overcommitment if the morning exits fail.**
`scan_and_enter()` guarded only on `entered_today()`; it never looked at open positions. If the
09:00 MOO submissions had failed (gateway down, as happened Aug 9 and Sep 2), the book would
hold yesterday's names AND buy a full new set at the close — well past its $10,000 budget, with
no cap anywhere in the path. Now caps at TOP_N **in total**: blocks entirely if already at the
cap, otherwise enters only the remaining room and logs why.

**② ADDED — earnings blackout (`EARNINGS_BLACKOUT_DAYS = 1`).** This book holds a name through
exactly one overnight gap, which is when earnings land. Audit of **1,932 held name-nights**:
only 4 worse than −10%, and the identifiable ones are all earnings — **DELL −12.0% (reported
2024-11-26) · AMZN −9.4% and AAPL −9.4% (both reported 2024-08-01 after the close) · ABBV
−10.5%**, plus CEG −15.8% on the Jan-2025 AI selloff. Ten name-nights beyond ±10% carry **15%
of all P&L**, in both directions.
Sizing the filter by clipping the tail (upper bound on what a perfect filter buys):

| clip | bp/night | Sharpe | worst night |
|---|---|---|---|
| none | 20.56 | 2.16 | −637bp |
| ±10% | 18.60 | 2.11 | −637bp |
| **±5%** | **17.97** | **2.26** | **−500bp** |

**It costs 2.6bp of mean and buys ~0.10 of Sharpe and a 137bp better worst night.** On a 3-name
book one −15.8% gap is −5.3% of the entire book in one night. **This is variance reduction, not
a P&L improvement — do not expect it to raise returns.** Unknown earnings date ⇒ allow the
trade (never skip the whole book on a data outage).

**③ TWO TUNE-UPS TESTED AND DECLINED.**
- **LOOKBACK 30 → 20.** Tempting: 20 gives 23.2bp vs 30's 20.6bp. But per year — 20: 13.9 /
  27.8 / 28.1 · **30: 15.6 / 23.1 / 23.2** · 40: 22.4 / 11.2 / 16.6. **30 sits in the middle of
  the plateau: never the best, never the worst, solid in all three years.** 20 is best only in
  2025-26 and second-worst in 2024. Changing it would be fitting the recent half. **Left at 30.**
- **Day-of-week.** Tue→Wed (29.5bp) and Wed→Thu (33.4bp) beat Mon→Tue (10.3bp) and Thu→Fri
  (13.2bp), and the ranking holds in all three years. **Declined anyway:** five buckets of ~130
  nights sliced after the fact, day-of-week is among the most p-hacked results in finance, and
  acting on it would cut the book to two nights a week. **Logged as a watch item.**

**④ CONFIRMED CORRECT, no change.** Round-tripping vs holding repeats: **2.27 of 3 names
repeat each night** (40% of nights the whole top-3 is unchanged), so the book sells and rebuys
the same names constantly. Holding them through the day instead would save the 2.1bp fee but
collect their intraday return, which is **−3.44bp** — holding is worse by 1.34bp overall and
mixed by year (2024 −10.5, 2025 +5.4, 2026 +0.6). ⭐ Note those names' intraday return is
negative while the universe averages +1.6bp — the "tug of war" showing up directly in our own
book. **Round-trip stays.** Also: no liquidity floor needed — at $3,333 a name against a $20M
ADV that is 0.017% of daily volume, so market impact is nil, and the WILD screen already
implies ≥$10M.

**⑤ ⚠️ THE RISK NOBODY SHOULD MISS: 97% of the total return comes from the best 5% of nights.**
644 nights, 18 worse than −300bp and 26 better than +300bp. This is a lottery-shaped payoff:
most nights are ~flat and a handful of big up-gaps carry everything. Consequences — you cannot
time it, you must be in every night, and a long flat or negative stretch is entirely normal
and is NOT evidence the edge is gone. Judge this book on months, not days.

**⑥ DO PROS DO THIS? Yes — and the cautionary tale is exact.** NightShares launched ETFs to
harvest precisely this premium (buy the index at the close, sell at the open) and **closed them
about a year after launch, explicitly because turning the portfolio over in full twice a day ate
the return**. Elm Wealth ("Still Working the Night Shift", Mar 2025) tracks the same effect;
it has been documented again in the Bitcoin ETFs (IBIT ~+200% overnight vs ~+40% buy-and-hold
since Jan 2024). ⭐ **Our position differs in exactly one way that matters: we trade 3 names at
$3,333 with a known $0.70 round trip and no market impact, where a fund turning over hundreds
of millions twice daily pays impact we simply do not.** Being small is the entire edge here —
which also means it does not scale, and that is worth knowing before anyone grows it.

**Tie note (not acted on):** with a 30-day window the consistency score is quantised (n/30), so
ties at the cut are common — tomorrow CENX/NUTX/P/SMTC all sit at 70% and the 3rd slot is
decided by sort order. A secondary tie-break would be a fitted choice; logged, not built.

---

## Sep 20 2026 (pm3) — Is the 3-name pick air-tight? No. But the design survives the challenge.

**① THE PICK BEATS RANDOM — BUT NOT RELIABLY.** Same WILD pool, same $20M ADV floor, same
fee, 3 names, gross bp/night:

| year | top-3 by consistency | random 3 (15-40 draws) | edge | all eligible WILD, EW |
|---|---|---|---|---|
| 2024 | 32.3 | 27.7 | **+4.6** | 27.0 |
| 2025 | 21.7 | 24.4 | **−2.8** | 21.0 |
| 2026 | 35.3 | 15.7 | **+19.6** | 18.1 |
| **ALL** | **29.1** | **21.5** | **+7.5** | 22.0 |

**The selection LOSES to random in 2025 and its whole-sample edge is carried by 2026.** One
good year in three. That is not air-tight and should not be described as such.
⭐ **The honest decomposition: roughly three quarters of the value is being in the overnight
window at all (a random 3 earns 21.5bp), and about a quarter is the pick (+7.5bp).** The
window is the edge; the pick is a modest, unreliable bonus.

**② CONCENTRATION TO 3 IS FORCED BY FEES, NOT CHOSEN FOR ALPHA.** Equal-weighting all
eligible WILD names has the best Sharpe of anything tested (1.83 vs our 1.68) — but the median
night has 30 eligible names, so $10,000 spread across them is $333 each, and at $0.70 a round
trip the fee is **21bp against a 22bp edge: +1bp net.** Unusable. Three names at $3,333 pay
2.1bp. **We are concentrated because we are small, not because concentration is better.**

**③ REAL WEAKNESSES IN THE SELECTION, named.**
- **Ties.** A 30-day window quantises the score to n/30, so ties at the cut are routine —
  tomorrow CENX / NUTX / P / SMTC all sit at 70% and the third slot goes to sort order.
- **Sector clustering.** The 3 picks average 2.31 distinct sectors, and on **11% of nights all
  three are in the same sector.**
- **Single-name gap risk**, now partly handled by the earnings blackout (pm2 entry).

**④ THE ETF ALTERNATIVE — tested, and it loses for an instructive reason.** A fund would reach
for a broad instrument (NightShares used index futures). Held overnight, net of a $0.70 fee on
one $10,000 position:

| implementation | net bp | Sharpe | maxDD |
|---|---|---|---|
| **our 3 WILD names** | **26.40** | 1.68 | **−13.2%** |
| most-volatile ETF each night (a RULE) | 13.11 | 1.17 | −27.5% |
| 3 most-volatile ETFs | 15.29 | 1.72 | −24.6% |
| all 11 ETFs equal weight | 10.75 | **1.97** | −16.0% |
| SPY only (the unbiased default) | 5.47 | 1.35 | −16.2% |

⚠️ **URA tops a per-ticker table at 22.5bp / Sharpe 2.26 — but that is hindsight.** Choosing it
because it ranked first is the same error as the Jul-2026 universe screen. The RULE version of
the same idea ("hold the most volatile ETF") earns **13.1bp, not 22.5**, and was −1.1bp in 2025.
⭐ **Why the ETF route earns less: the overnight premium scales with volatility, and the
diversification inside an ETF removes exactly the volatility we are being paid for.** Single
WILD names carry more of it than any sector ETF. That is a real argument for the current design.

**⑤ DO PROS DO THIS? Not with single names — and the reason is size, not principle.** The
professional instinct is to take a broad premium with a broad instrument, which is what
NightShares did (index futures) before closing on turnover costs. But a fund running hundreds
of millions *cannot* hold three small caps overnight; we can. **At $10,000 the single-name
route is open to us precisely because we are too small to move anything** — the same fact that
makes our fee tolerable. It does not scale, and it should not be grown without re-testing.

**VERDICT: keep the current design, drop the claim that the pick is proven.** The window is
doing the work. Track the pick's contribution explicitly — if `ref_pnl` keeps beating a
random-3 benchmark over the coming months, the selection earns its place; if it does not, the
book still works, and the honest response would be to widen it rather than defend the sort.

---

## Sep 20 2026 (pm4) — OPTIONS: the book was never a signal problem, it was arithmetic

Deep read of the whole options stack + full re-evaluation. Labs
`research_options_structure_lab.py` (structure/Edge Budget), VRP test in-session.
**SHIPPED: 1 real bug fix + delta-anchored structures + the Edge Budget gate + tests.**
⚠️ **The "entries stay frozen" conclusion in this section was SUPERSEDED the same night —
see Sep 20 (pm5) below. Spreads are LIVE on paper from Sep 21.** The analysis here stands
unchanged; what changed is the decision about what to do with it.

**① WHY IT TAKES NO TRADES — three layers, only the first is deliberate.**
`EQUITY_ECHO_FROZEN = True` (options_trader.py, Aug 16) returns early from BOTH automated
entry paths — `_check_equity_scan_triggers` and `scalp_scan_loop`. That is the whole answer
to "why no trades"; everything downstream is untested consequence. Behind it,
`_book_health_on('SHORT')` is **frozen on Jul 22-31 data** because SHORT `scan_log` rows
stopped dead 2026-07-31 — the same freeze-forever architecture that bit equity on Jul 21,
back through a different door. It self-heals from Sep 21 now that `BEAR_OBSERVE_ONLY=True`
resumes SHORT grading. Watchman EXITS were never frozen; there are **0 open positions**.

**② THE DISEASE, quantified. Median breakeven required: +8.1%.**
Every debit spread ever traded was anchored at `underlying + EM x 0.33 / 0.67`, putting the
long leg 8-12% OTM. Across all 22 closed trades: median long leg **+5.6% OTM**, median
**breakeven move +8.1%**. Against 194,715 symbol-days of our own universe:

| hold | P(move >= +8.1%) | P(>= +4%) | P(>= +2%) |
|---|---|---|---|
| 1d | 2.4% | 8.8% | 20.6% |
| 3d | 7.7% | 19.4% | 32.7% |
| **7d** | **15.6%** | 29.9% | 40.9% |
| 21d | 30.1% | 41.9% | 48.7% |

We bought structures needing a 1-in-6.4 event **just to return the premium**, then held them
~7 days. Realized: **17 of 22 closed trades lost**. The 84% predicted loss rate and the 77%
actual are the same number. ⭐ **The book performed exactly as its structure dictated.
Signal quality was never the binding constraint.**

**③ 🐛 REAL BUG FIXED — every strike has been ~20% too far OTM since the calculators were
written.** `engine.compute_expected_move` used `sqrt(dte/252)` while `days_to_expiry()`
returns **calendar** days. Mixing a calendar numerator with a trading-day denominator
overstates every expected move by `sqrt(365/252)` = **1.204x**, and because all four
calculators anchor strikes on a multiple of it, every strike sat 20% further OTM than the
template intended. Fixed at source (`engine.py` now delegates to `options/structure.py`).
Blast radius checked: 4 call sites, all in options_trader — watchman, the backtester, turbo
and the chain collector never call it.

**④ STRUCTURE LAB — what each structure needs, and the control that matters.**
Black-Scholes at the universe median IV (74%), friction from our OWN
`options_chain_snapshots`, forward distribution from `bars_5m`. **Detrended** (universe mean
move removed) so the 2024-26 bull tide cannot flatter anything — per this file's own
benchmark rule:

| structure | BE move | detrended exp. return, 21d |
|---|---|---|
| debit spr .40/.20 | +5.94% | **-20.32%** <- what we traded |
| debit spr .60/.35 | +2.03% | -10.22% |
| debit spr .75/.50 | -0.55% | -5.89% |
| bull put cr .30/.15 | -3.56% | **-2.22%** <- least bad |
| shares | +0.02% | -0.02% |

⭐ **No structure is positive once drift is removed — options lose to simply holding the
shares, on this universe, at every horizon.** But the spread between worst and best is
**~18pp**, and we had picked the worst available. At <=7 days *every* structure is deeply
negative, which is the decisive quantitative **NO** to optioning the overnight edge: the
cheapest 1-day structure needs **+0.79%** to break even and Clockwork's whole edge is
**+0.19%/night**.

**⑤ THE INPUT THAT SETTLES IT — our signals terminal move, measured.**
745 unique symbol-days of A+/A LONG candidates since Apr, terminal move vs the universe over
identical windows (the only honest benchmark on a universe screened on past results):

| horizon | our A+/A LONG | universe | selection |
|---|---|---|---|
| 1d | +0.22% | +0.14% | +0.08pp |
| 3d | +0.01% | +0.37% | -0.36pp |
| **7d** | **-0.79%** | **+0.81%** | **-1.60pp** |
| 21d | -4.21% | +2.58% | **-6.79pp** |

⭐ **Beyond one day our graded signals are worse than a name picked at random from the same
universe** — consistent with the Sep 18 finding that the grader systematically buys
extension, which mean-reverts over days. A negative expected move cannot clear any positive
breakeven. **An independent second route to the Aug 16 freeze verdict.**

**⑥ TESTED AND REJECTED — selling premium is not the escape hatch.** If we are the wrong
side of the volatility premium, invert. Measured on 441 unique symbol-days (`opt_calc_log`
IV vs realized vol over the next 30d): **VRP +1.67 vol pts, t=+1.77, p=0.078**, and
month-inconsistent (May -6.96 / Jun +0.53 / Jul +3.61). Not significant, not shippable — the
index VRP does not survive on 74%-IV small caps. The structure lab agrees independently:
credit structures are negative detrended too. Two methods, same answer.

**⑦ TURBO (options on Wave Rider) — existing evidence, read honestly.** 45 tickets,
**5 PASS / 40 SKIP** (89% skip: 18 illiquid, 14 IV<25 — the WILD universe has no option
market). The 5 passes: leverage premium **-24.19 / +9.24 / -2.96 / -46.64 / +13.31 =
-51.24pp**. Options on that engine destroyed value vs holding the shares. Leave it SHADOW.

**⑧ SHIPPED.**
- **`options/structure.py`** (new) — Black-Scholes, correct `expected_move`, delta-anchored
  strike selection, `breakeven_move_pct`, `edge_budget`. Pure, no I/O.
- **`options/test_structure.py`** (new) — 33 pinned tests incl. reproducing the real MRVL
  trade's +12.46% breakeven. **Run after ANY change to strike selection or the gate.**
- **Delta-anchored strikes** in both debit calculators (`STRUCTURE_MODE='DELTA'`, long 0.70 /
  short 0.45 delta), falling back to EM anchoring only when a coarse ladder cannot express
  the structure. Verified end-to-end on a mocked chain: **breakeven +6.40% -> -0.20% on
  identical inputs, 6.60pp less move required.**
- **The Edge Budget gate — the permanent admission test, on all FOUR calculators.** A
  structure may only be traded when the SIGNAL's own expected move over the intended hold
  clears the structure's breakeven with a 25% buffer. **Fails closed**: an entry path that
  cannot state the move it expects does not get to trade. Automated paths must pass
  `signal_move_pct`; manual `OPT BUY`/`OPT SELL` pass `enforce_budget=False` (the human is
  the decider and already routes through CONFIRM) but still see the budget on screen.
  Closed a hole found mid-build: the IV>=50 route sends candidates to the **credit**
  calculators, which were ungated — gated now too.
  ⚠️ **The gate is necessary, never sufficient** — it asks "can the signal reach breakeven?",
  not "does this beat holding the shares?" On this universe, detrended, nothing beats shares.
  Documented in `structure.edge_budget`'s own docstring.
- Edge Budget line added to the Telegram spread card.
- options_trader restarted 22:48 ET, clean, 0 open positions, new PID verified.

**⑨ WHY I INITIALLY HELD (SUPERSEDED by pm5 — kept for the reasoning).** Entries stayed frozen. Unfreezing the equity echo would
produce **zero trades anyway** — the gate rejects a -0.79% signal — and forcing trades by
lowering the bar would be buying a structure the evidence says loses. **The one source that
PASSES the gate is Contrarian** (buy the biggest 3-day fallers, 5-day hold): terminal move
**+2.82% mean / +0.47% median at 5d, alpha +2.02pp vs the universe, positive in all three
years** (2024 +4.12 / 2025 +2.30 / 2026 +1.70). Not wired, on purpose — it has **2 own-exit
trades live**, its mean is right-tail driven (a debit spread caps exactly that tail), and its
backtest carries this file's own acknowledged survivorship bias on precisely the "buy the
crasher" axis. Wiring options onto a 2-trade engine is how Turbo happened.
**Precondition to revisit: ~20 Contrarian own-exit trades with positive alpha vs the Tide.**

**TO MAKE IT TRADE ANYWAY** (one line, reversible): set `EQUITY_ECHO_FROZEN = False`. The
Edge Budget will still refuse the echo; to get fills you would also have to override
`OPT_ECHO_SIGNAL_MOVE` or set `OPT_EDGE_BUDGET=0`, which means trading a structure against a
measured negative signal. Recorded so the cost of that choice is explicit.

**OPEN / NEXT.** (a) Contrarian -> options once it has a real sample (above). (b) Wire
turbo's leverage-premium comparison into the Edge Budget so a PASS also has to beat shares.
(c) `options_chain_snapshots` covers only held positions — 6 symbols, Aug 10-18; widening it
is the prerequisite for ever backtesting an options structure on real quotes instead of
Black-Scholes. (d) The scalp engine (naked ATM weeklies, 7-12 DTE) has no Edge Budget and has
never had an edge — it should be deleted or rebuilt, not just left frozen.

---

## Sep 20 2026 (pm5) — options UNFROZEN for a paper trial; the gate becomes a scored prediction

Pushback, and it was right: **options is the only book here that cannot be backtested.**
`options_chain_snapshots` holds **6 symbols over 9 days**, so every structure number in the
pm4 section is Black-Scholes approximation, never a real quote. A gate that blocks every
trade also destroys the data needed to judge the gate. The account is paper. So the
instrument-first doctrine this codebase already applies to Chart Gate, Thesis Check and
Crest Watch applies here too: **let it trade and score it.**

**WHAT WAS ACTUALLY BLOCKING IT — checked, not assumed:**
| | state | blocker? |
|---|---|---|
| `EQUITY_ECHO_FROZEN` | True | **yes** — gated BOTH spread and scalp paths |
| Edge Budget | ENFORCE @ −0.79% signal | **yes** — rejected everything |
| Book Health | LONG ON (+0.69) · SHORT ON (+0.71) | no |
| capital / slots | $5,000, 4 free | no |
| entry window | spreads to 14:30 | no |
| **A+ SHORT candidates** | **0 on every session since Jul 31** | **yes, for the bear side only** |

⭐ The bear path was never blocked by a flag — it had **no fuel**. Both spread calculators
exist, but `scan_log` has written zero A+ SHORT rows since 2026-07-31. `BEAR_OBSERVE_ONLY`
(shipped Sep 18) resumes SHORT grading from Sep 21, so **tomorrow is the first day the bear
put spread has candidates at all.**

**SHIPPED:**
- **`EQUITY_ECHO_FROZEN = False`** — spread entries live on paper. Max exposure 4 slots ×
  $500 max-loss = **$2,000**.
- **`SCALP_FROZEN = True`** (new, separate flag) — the scalp engine stays off. Naked ATM
  weeklies at 7-12 DTE are the worst structure in the lab, have no Edge Budget wired, and
  have never shown an edge. Unfreezing spreads is not a reason to unfreeze scalps.
- **`EDGE_BUDGET_MODE = 'OBSERVE'`** (was ENFORCE). The budget is computed and **persisted**
  on every candidate but does not block. ⭐ **This converts my objection into a testable
  prediction instead of a veto** — every trade now carries the gate's PASS/FAIL, so we find
  out whether the gate is right. Flip to `ENFORCE` (or delete it) on the evidence.
- **`opt_calc_log` +6 columns** (idempotent ALTER): `be_move_pct`, `signal_move_pct`,
  `edge_required_pct`, `edge_budget_ok`, `structure_template`, `skip_reason`.
  ⚠️ **`skip_reason` was never persisted before** — "why did nothing trade" was literally
  unanswerable from the DB. That is fixed.
- **`options_trial_report.py`** (new) — scores the trial: funnel, structure + toll-gate
  pass rate, the **Edge Budget graduation test** (PASS vs FAIL realized P&L), and the
  **leverage premium vs holding the shares** (the question the Edge Budget cannot answer).

**VERIFIED BEFORE GOING LIVE:**
- **The Spread Toll Gate does NOT strangle ITM legs** — the real risk of the delta-anchored
  change. Tested on all 1,724 real call quotes in `options_chain_snapshots`: delta-anchored
  passes **17/18**, the old EM anchoring **16/17**. Toll is higher in absolute terms
  (ITM legs cost more) but nowhere near the 12% cap. The single block is JOBY at $8.84 —
  a $1 strike ladder too coarse to express either structure.
- Edge-budget fields round-trip to the DB (real `log_calc_run`, row read back, cleaned up).
- 33 structure tests + the pinned liquidity test pass. options_trader restarted 23:01 ET.

**⭐ THE HISTORICAL NUMBER THIS REPORT IMMEDIATELY PRODUCED.** Run over the August trades,
options vs simply holding the underlying for the same hold: **mean leverage premium
−29.9pp over 7 trades** (JOBY −87.6 · INTC −43.0 · PLTR −34.5/−29.3 · MRVL −25.4 · XLE −1.3 ·
NFLX **+11.6**). MRVL is the thesis in one line: **the stock rose +6.7% and the option still
lost 18.6%, because the structure needed +12.46%.** Third independent confirmation after
Turbo's −51pp and the detrended lab.

**HOW TO READ THE TRIAL — this matters more than the P&L.** The signal feeding it measures
**−0.79% terminal move at 7d** against a universe that did +0.81%. **The signal is expected
to lose money, so a negative total is NOT a verdict on the structure work.** What this trial
CAN answer, and analysis could not: (1) do delta-anchored spreads fill at all, (2) what do
they really cost vs the mid we calculate on, (3) does the toll gate pass ITM legs live,
(4) is the Edge Budget's verdict predictive. Judge those, not the total.

**❌ CORRECTED Sep 22 — THE TRIAL NEVER STARTED. The options CIRCUIT BREAKER is tripped.**
`check_circuit_breaker()` blocks new entries once cumulative realized options P&L is worse
than `OPTIONS_CIRCUIT_BREAKER` ($5,000). Lifetime realized is **−$5,333.08**, so it returns
False and `_check_equity_scan_triggers` bails on EVERY cycle, before it looks at a single
candidate. Everything shipped Sep 20 — delta-anchored strikes, the Edge Budget, unfreezing
`EQUITY_ECHO_FROZEN` — sits DOWNSTREAM of a gate that was already shut. The one opt_calc_log
row (ARM, Sep 21) came from the **news Ghost-Ledger path**, which runs before the breaker
check; it was never the echo.
⭐ **And the earlier "the catalyst filter starves the funnel" diagnosis was WRONG.** Measured
on Mon Sep 21's 25 A+ names, close→next-day: catalyst-blocked names averaged **+0.50%**,
eligible names **+1.85%** — the filter is selecting correctly. There WAS opportunity
(MRNA +5.56%, QBTS +5.37%, OUST +5.25%, all three **eligible**, none blocked); the breaker
is why none of them was ever priced.
**DECISION NEEDED (user's call, not to be taken unilaterally):** the breaker is a real risk
control working as designed — the book HAS lost $5.3k. But that total is dominated by
pre-rebuild damage (SYSTEM_RESET closes, the Jul USAR short-15 incident) rather than by the
current logic. The precedent for scoping it to the rebuild exists —
`BOOK_HEALTH_RESET_DATE = '2026-07-22'` does exactly that for Book Health. Options: (a) leave
it tripped; (b) add an `OPTIONS_CB_SINCE` date so the breaker measures the CURRENT system.
Do not simply raise the limit.

**WATCH MON SEP 21:** first candidates after 10:00 (cutoff 14:30) — `logs/options_trader.log`
for `[options] scan trigger`. Expect `Delta-Anchored` templates with breakevens near 0%
rather than +8%, and **the first bear put spreads since July** once SHORT grading resumes.
Then `venv/bin/python options_trial_report.py`.

**🐛 PRE-EXISTING BUG FOUND IN THE BUG SWEEP AND FIXED — it sat in the manual escape hatch
for the book we just turned on.** `_execute_close_bg` (manual `OPT CLOSE` on a
BULL_SPREAD / BEAR_PUT_SPREAD) called `sqlite3.connect(DB_PATH)` while **neither `sqlite3`
nor `DB_PATH` is imported at module level** in options_trader.py — every other call site in
the file imports them locally. The function runs in a **daemon thread with no try/except**,
so the two-leg close SUCCEEDS, the DB is updated by watchman's closer, and then the
confirmation line raises `NameError` and the thread dies silently: **no Telegram ever
arrives and the user concludes OPT CLOSE failed on a position that actually closed.** Same
failure shape as the Jul 20 2026 USAR retry storm. Found by running pyflakes over the file —
it was the only undefined name in 5,000 lines. Fixed with local imports plus a fallback
message when the read-back returns no row.

**REVERT:** `EQUITY_ECHO_FROZEN = True`. **ENFORCE the gate:** `OPT_EDGE_BUDGET_MODE=ENFORCE`.

---

## Sep 22 2026 — META oversell root-caused and fixed · 5 latent bugs · scoring loop finally closed

Postmortem of the first days after the Sep 20 options work. **Five bugs found, all five
fixed, all verified. One of them cost real money.**

**① 🚨 THE META OVERSELL — the only one that cost money.**
META was held overnight (by design) and was the week's best call. At **09:23 PRE-MARKET** its
trailing stop fired. Every strategy exit in `auto_trader.py` writes the DB row CLOSED
**before** submitting the order — the comment says *"DB write first, reconcile corrects
state"*. The instant that row closed, the still-real 2-share position looked like an **orphan**
to `reconcile_with_ibkr()`, which fired **six more closes across three attempts** (each attempt
sent a MARKET order, waited **2 seconds**, re-read the portfolio, saw it unchanged because
nothing can fill pre-market, and sent a LIMIT too).

All seven landed together at the 09:30 open:

| | |
|---|---|
| DB records | **+$63.22** ✅ WIN |
| Broker actually did | SOLD **14** @ 732.94, then BOT **12** @ 748.88 |
| Real result | **−$123.38** |
| Oversell cost | **−$191.28** |

⭐ Same failure family as the Jul 20 2026 USAR storm and the Sep 2026 28-symbol incident.
The earlier fix ("verify against the real portfolio after a close attempt") **cannot work
pre-market** — the verification reads "still there", concludes the order failed, and fires
again. **Three independent fixes, all pinned by tests:**
- **`_exit_in_flight` registry** — every strategy exit stamps the symbol *before* the POST
  (before, because a POST that times out may still have placed the order). Reconcile never
  races a working exit. The flag **never ages out while the market is closed**, because a
  resting order cannot fill so elapsed time says nothing. Cleared the moment IBKR reports flat.
- **Reconcile places no orders outside regular hours** — it logs once per symbol and waits.
- **One order per cycle.** The market→limit escalation now happens *across* cycles (90s
  apart, verified each time), not inside one. Three attempts used to mean six live orders.
- `tests_reconcile_guards.py` — 10 pinned tests including a full replay of the META
  pre-market window (**0 orders** where the old code fired 6), plus proof the genuine orphan
  safety net and the other-book guard still work. **Run after any reconcile or exit change.**

**② VICR — the dashboard showed two different positions as one row.**
Wave Rider held 9 @ $212.88; auto_trader bought 8 @ $244.00. IBKR blends them: **17 @
$227.64**. The dashboard reported the **broker's** blended `unrealizedPnL` next to a **%
computed from this book's own entry** — hence "+0.95% / +$300". Both numbers were individually
right and described different positions. Now the dollar figure is derived from the row's own
shares and entry, so $ and % always agree (verified live: 8 sh, +3.86%, **+$75.44**).
⚠️ **Not fixed, flagged:** two books independently sizing the same name is real concentration
nobody accounts for. Exits are safe — every engine sells its own recorded shares
(`min(shares, ibkr_qty)`), never the broker position.

**③ 🐛 `sys` was never imported** — yet `reset_daily_state()` calls `sys.exit()` twice: the
duplicate-instance guard and the **`PROD_EQUITY_ENABLED` live-mode guard**. Both raised
NameError instead of exiting cleanly. Fail-safe in effect (the process died either way), but
the live-mode guard is a **go-live checklist item that had never once executed as written**.

**④ 🐛 The evening signal-funnel line has never rendered — not once since Jul 18 2026.**
`evening_summary()` calls `get_connection()`, which was never imported there; the NameError
was swallowed by a bare `except Exception`. **Zero occurrences in the entire log.** Same shape
as the Aug 8 Chart Gate discovery: built, shipped, silently dead. Now verified rendering:
`📖 Books: LONG ON / SHORT ON | A+ signals: 8L / 0S`.

**⑤ THE SCORING LOOP WAS OPEN AT BOTH ENDS.** `backfill_scan_forward.py` (the CORRECT forward
label, built Sep 18) **was never scheduled** — so `fwd_mfe_pct` went blank from Sep 18 onward
while `score_components` kept logging. We were recording the guesses and not the answers.
**SHIPPED: `com.sushil.trading.scan_forward_label`, 17:15 weekdays** (after collect_bars
writes the session at 16:30; ~20s/day). Caught up on the spot: **12,505 rows labelled**,
Sep 15-21 now 95-100% covered. Clockwork's `ref_pnl` was blank for the same reason (bars not
yet written when it ran) — re-ran, 10 more rows.

**⭐ SCORING IS NOW LIVE AND CLOSED.** Components + correct outcome label both accumulate
automatically. Re-run `research_equity_component_lab.py` and the weight regression at ~4-6
weeks of data. Do not hand-tune a weight before then.

**THE WEEK, honestly (Sep 21-22, a strongly trending tape):**
- auto_trader **+$109** / 17 trades / **41% WR**
- Captured **26% of peak**. Same entries held to the close: **+$314 vs our +$59** — the exit
  stack **cost $254 this week**.
  ⚠️ **Do not over-read this.** "Exit later" has now been tested 7 times across 668 trades and
  loses on average. The honest reading is a **regime mismatch**: these exits are calibrated for
  chop and this was a trend week. It is not evidence the stops are wrong, and it is not
  licence to loosen them without a test that survives a non-trending sample.
- Wave Rider −$589 (16 closed, 4 open) · Contrarian −$375 closed but **2 open well up**
  (ONTO +$510, SITM +$186) · Clockwork Mon −$49, resize confirmed working (~$3.3k/name).
- **Clockwork at official prints: +$452 strategy vs −$413 actual** across 80 LIVE trades. The
  paper simulator's fabricated auction fills remain the whole gap, exactly as Sep 20 found.

**WHY OPTIONS STILL HAS NOT TRADED — and it is NOT the new gate.**
**One** calculator run in two sessions. Of 33 distinct A+ names Monday, **20 were
catalyst-flagged and auto-skipped** by a Jun 20 rule (catalyst ⇒ IV already elevated). Of the
14 survivors only ARM reached a calculator, and it **failed the liquidity toll (15% vs 12%
cap)**. ⭐ The structure work IS functioning: ARM priced at a **−2.00% breakeven** where the
old EM anchoring needed **+8%**. The blockage is upstream of everything built on Sep 20.
Not changed — the catalyst rule is data-backed and removing it is a strategy decision, not a
bug fix. Flagged for a deliberate call.

**⑥ THE DASHBOARD REPORTED ONE EQUITY BOOK AS IF IT WERE ALL FOUR.**
Found by asking why Wave Rider showed nothing in the Today column. Wave Rider's blank was
honest — it closed nothing that day (its exits are 15:45). The real defect was underneath:
**three of the four live equity books were absent from every headline panel.** Wave Rider,
Contrarian and Clockwork trade the SAME real IBKR account through their own tables
(`wave_trades` / `contrarian_trades` / `overnight_trades`), and `get_today_summary()`,
`get_pnl_by_book()` and `get_scorecard()` all read `trades` alone.

Measured the moment it was found: the Today card showed **+$70.57** when the true equity
figure was **+$21.52** — Clockwork's **−$49.05** simply was not counted. The per-engine
breakdown has existed since Sep 11 in the ENGINES scoreboard; the headline never caught up.

- **Today card** — now the total across all four books, plus an `eq.books` composition
  rendered on the card itself (`Day Trader +$71 / Clockwork −$49`) and in its tooltip, so a
  book can never go missing from this number invisibly again.
- **15-day chart** — the three engines folded into the `equity` series (this chart is P&L per
  VERTICAL; per-engine detail belongs in the scoreboard).
- **15-day scorecard** — `Equity` renamed **`Day Trader`** and the other three given their own
  rows, because this table exists to COMPARE books. Each row now wrapped so one missing table
  cannot blank the whole panel.
- Verified: chart equity series and the scorecard's four equity rows reconcile to the dollar
  (−$1,841.84 over the window), and both agree with the Today card.

⭐ **The rule this keeps proving: a number that reads from one table while the system trades
from four is not a summary, it is a sample.** Same shape as the Sep 2026 reconcile gap, where
`_other_book_symbols()` had to be taught the other engines exist.

**Still open:** (a) the equity exit stack is chop-calibrated and this is a trend tape — a real
question, needs a test that survives both regimes; (b) two books can hold the same name with
no shared concentration limit; (c) the options catalyst filter starves the funnel; (d) DB exit
prices are the *intended* price, not the fill (META: recorded 731.60, filled 732.94) — the
futures side solved this Sep 3 with `_get_fill_price()`; equity has no equivalent;
(e) the Equity Positions TABLE still lists only `trades`, so the other books' open positions
appear in the count but not the table — the ENGINES scoreboard carries their open counts.

**Live note (Sep 22):** VICR was held by Wave Rider (9 sh @ 212.88, +19%) AND auto_trader
(8 sh @ 244.00). auto_trader's slice was **entered 09:38, before the 10:00 `HOLD_TO_CLOSE_BEFORE`
cutoff, which makes it a `_ride` trade — ATR trail, PCT trail, break-even stop and momentum
fade are ALL disabled by design**, leaving only the hard stop 9% away. At 15:45 the overnight
test (`pnl>1.5% AND above VWAP`) would have passed, so it was on track to be held overnight and
exited on the 1-business-day stop — exactly META's path. User closed it manually via
`SELL VICR` at 12:02 for **+$90.66 (+4.64%)**; the command correctly sold `min(own 8, broker 17)`
and left Wave Rider's 9 untouched.

---

## Sep 22 2026 (pm) — options breaker SCOPED (book can finally trade) + dashboard health/chart/colour pass

**① OPTIONS CIRCUIT BREAKER SCOPED — the book can actually trade now.** User-approved.
Lifetime realized options P&L was **−$5,333** against a **$5,000** limit, so
`check_circuit_breaker()` returned False on every cycle and the whole Sep-20 rebuild sat
behind a shut gate. That −$5,333 is damage from code that no longer exists (pre-Jun-23
build, SYSTEM_RESET closes, the Jul 20-21 USAR fill-race that sold the same contract ten
times). The logic has been rewritten twice since.
**The limit was NOT raised — it was scoped.** `get_options_total_pnl(since=)` +
`OPTIONS_CB_SINCE = '2026-09-21'` (first session of the corrected structure). Same precedent
as `BOOK_HEALTH_RESET_DATE`. ⚠️ **Raising `OPTIONS_CIRCUIT_BREAKER` instead would hide the
next real drawdown — don't.** Set `OPTIONS_CB_SINCE=None` to re-arm against full history.
Verified the entire echo chain is now open: not paused · echo unfrozen · scalps still frozen ·
slots free · **breaker OK** · LONG book on · structure DELTA · Edge Budget OBSERVE.

**② SYSTEM HEALTH — stale data was being presented as live.**
- **Book Health now carries the AGE of its reading.** A side that stops producing A+ signals
  freezes its window silently: **SHORT was reporting "ON +0.71%/sig" from data last written
  2026-07-31 — 53 days old.** Chips now show `STALE 53d` with the last-signal date.
  (The bear scan IS running — 4,225 rows today — it simply grades nothing A+ in this tape.
  That part is honest.)
- **Options row now shows the circuit breaker** (`breaker OK · $5,000 room` / `BREAKER
  TRIPPED`) and an `ENTRIES FROZEN` chip. A gate that can stop the book is now visible on
  the book — the invisibility is what let this run unnoticed for the whole trial.
  ⚠️ Needed a `sys.path` fix: `options/` is not importable from the dashboard process by
  default, and the bare `except` was swallowing it — the chip would have silently vanished.
- **NEW: Fleet capital** — cost basis per book vs its allocation (**$26,991 of $40,000**).
  "How much is actually invested" had no answer anywhere.
- **NEW: Scoring loop** — components / labels / **real trades fittable (552)** and the newest
  label date, so the nightly `scan_forward_label` job's health is visible.
- **REMOVED: dead `renderFishFinderHealth`** — Fish Finder was decommissioned Aug 15 2026 and
  this renderer had no call site since.

**③ CHART** — `.chart-tall` 150px → **260px**, bars widened (`categoryPercentage 0.92`,
`barPercentage 0.96`), y-axis `grace: '8%'` + `maxTicksLimit: 9`. A $21 equity segment next to
$1,300 of futures was a hairline you could not attribute to a book.

**④ ENGINE COLOUR SYSTEM** — one hue per engine (Day Trader green / Wave Rider blue /
Contrarian gold / Clockwork purple) used identically in the positions table, the ENGINES
scoreboard, the 15-day scorecard and the Fleet chips. ⭐ **Colour always travels WITH the
name** (`engBadge()` renders a colour bar + the name) — a second channel, never the only one,
so the tables stay readable under red/green colour-vision deficiency.

**BUG SWEEP before commit:** pyflakes clean (no undefined names across app.py, database.py,
options_trader.py, auto_trader.py) · all 4 files parse · JS braces/parens/backticks balanced,
every referenced renderer defined, dead renderer gone · **all 9 dashboard data functions
return and JSON-serialise** · full `/api/data` payload round-trips (25 keys, new keys
present) · 3 test suites pass · 4 services verified running.

---

## Sep 22 2026 (pm2) — my splice broke two cards · service pills were meaningless · health card rebuilt

**① 🐛 I BROKE IT, AND THE WAY I CHECKED COULD NOT HAVE CAUGHT IT.** Removing the dead
`renderFishFinderHealth` by splicing between two markers also deleted **`renderFieldReport`**,
which sat between them. `app.js` still parsed — delimiters balanced, a real JavaScriptCore
syntax check passed — but `renderSystemHealth()` threw `ReferenceError` at runtime, and
because `renderAll()` calls renderers in sequence, **SYSTEM HEALTH and every card after it
(RECENT ACTIVITY) rendered blank.**
⭐ **Counting braces cannot catch a missing function. Running it can.**
**SHIPPED: `dashboard/test_render.py`** — executes EVERY renderer against the live
`/api/data` payload in a real JS engine (JavaScriptCore via `osascript`). **Run it after any
change to `app.js`.** It found this in one shot and would have prevented it.

**② SERVICE PILLS WERE REPORTING "IS THE PLIST LOADED", NOT "IS IT RUNNING".**
`get_services()` was `label in launchctl_list_output`, which is True for a crashed daemon,
for a scheduled job correctly idle, and for a job sitting on a non-zero exit. **The row could
not report a failure of any kind** — `parity_check` was on exit code 1 and showed green.
Now parses `launchctl list`'s PID and exit-status columns into four real states:
`up` (daemon holds a PID) · `down` (daemon with no PID — the real alarm) · `idle` (scheduled
job between runs, styled muted so it never reads as live) · `failing` (scheduled job whose
last run exited non-zero). A daemon's exit code is ignored while it holds a PID — `-15` is
just SIGTERM from the last `kickstart -k`.

**③ SYSTEM HEALTH REBUILT around what this system actually is** — four equity books, an
options book and two futures accounts sharing one broker, all on paper, all building
evidence. The card should answer **"can each book trade right now, is the data feeding it
fresh, and is anything frozen?"** Added:
- **Data feed** — newest `bars_5m` timestamp + per-trader heartbeat ages. Everything
  downstream reads those bars (Book Health, the forward label, the swing engines, every
  backtest); if the feed stops the system degrades quietly instead of failing loudly.
  London's beat is expected to be stale outside 03:00-08:59 ET and is not flagged.
- **Prop room (TC)** — balance vs the trailing-MLL floor. On Sep 3 TC locked itself out of
  trading **by $45** with nothing on this page saying so, and it could not earn its way back
  because it could not trade. The remaining room IS the warning.
- **Book Health age** (pm), **options circuit breaker** (pm), **Fleet capital** (pm),
  **Scoring loop** (pm).
- Mirror Book kept, tooltip now explains that gaps are normal for a low-frequency shadow book.

**BUG SWEEP:** pyflakes clean · 4 Python files parse · app.js syntax-checked in a real JS
engine · **all renderers executed against the live payload** · all 10 dashboard data
functions JSON-serialise · 3 test suites pass · dashboard restarted and serving.

---

## Sep 22 2026 (pm3) — service row covered 13 of 36 jobs, including none of the factory engines

**① THE SERVICE PILLS LISTED 13 OF 36 LOADED JOBS — and the omissions were the ones that
matter.** Missing: **`wave_rider`, `contrarian` and `clockwork`** — all three factory engines
whose open positions the dashboard now displays. Any of them could have died and the page
would have said nothing while continuing to show their positions as if live. Also missing:
**`heartbeat`** (the watchdog itself — built Sep 3 precisely because a silent death cost a
London session), **`scan_forward_label`** (the scoring job scheduled this morning),
`market_context` (Field Report), `overnight_reference` (Clockwork's honest-price marks),
`turbo`, and **`parity_check`** — which was sitting on a non-zero exit.
Rebuilt to **22 services** grouped by what they do: brokers/bridges · the books that place
orders · the data they depend on · instrumentation · watchdogs. Each row now carries its
`kind` (daemon vs scheduled) and a note saying what a non-zero exit actually MEANS for that
job, instead of a generic "failed".

**② `parity_check` exit 1 is a FINDING, not a crash.** It exits non-zero when it detects a
divergence — that is its designed signal. It shows amber with "found a divergence — by
design, read logs/parity.log" rather than being mislabelled as a failure. (It is currently
amber for real: Sep 21 flagged entry-window violations on AXTI/BNC and a trade with no graded
scan_log signal behind it — worth reading.)

**③ "6t" was unlabelled jargon.** Nothing on the page defined `t`. The ENGINES table now
reads "6 trades" / "1 trade", muted so the P&L beside it stays the thing the eye lands on,
and the TODAY / 7-DAY / WIN headers carry tooltips stating that they count **closed** trades
only — open positions are not in those numbers.

**SWEEP:** pyflakes clean · app.js syntax-checked in a real JS engine · every renderer
executed against the live payload · all 10 data functions JSON-serialise · 3 test suites
pass · dashboard restarted and serving.

---

## Sep 22 2026 (pm4) — service roles · alerts that can see the system · dead calendar table · ⚠️ a query I added cost half a day of bars

**① ⚠️ MY OWN CHANGE BROKE BAR COLLECTION — caught by the alert card I had just built.**
The Data-feed row I added earlier ran `SELECT MAX(replace(ts_utc,'T',' ')) FROM bars_5m`
on **every** `/api/data` poll. `market_data.db` is **~12 GB** and `ts_utc` only exists inside
a composite `(symbol, ts_utc)` index, so that is a **full scan taking ~6 seconds** — fired
**every 30 seconds** by the dashboard's refresh. The read lock it held made `collect_bars`
fail its commit with `database is locked`: **Sep 22 finished with 9,110 of ~18,500 rows**, and
`futures_collect_bars` died the same way.
**Fixed:** cached for 10 minutes (the value changes once a day, when the collector runs) —
**5.61s → 0.09s**. Both collectors re-run clean: equities **18,579 rows** (matching Sep 21's
18,564), futures through 2026-09-22, both exit 0. Forward labels re-run: Sep 22 now 96%.
⭐ **Lesson: never put an unbounded aggregate on a multi-GB table behind a 30-second poll.**
⭐ And the alerts card earned itself on its first run by catching this.

**② SERVICE ROLES — Flask was silently alphabetising the row.** `jsonify` sorts dict keys,
so the role grouping shipped in pm3 was destroyed in transit. The API now returns **ordered
groups**, rendered with role labels and separators:
**Brokers** (4) · **Books** (8) · **Data** (4) · **Instruments** (3) · **Watchdogs** (3).
"Which layer is broken" now reads at a glance instead of hunting 22 alphabetical pills.

**③ THE ALERTS CARD COULD ONLY SEE POSITION STOPS.** With every stop far away it rendered
empty — while the Trade Cop was flagging a real divergence, SHORT book health was 53 days
stale, and the options circuit breaker had silently blocked an entire paper trial. It now
also reads the system-health payload it is rendered beside: service down/failing · stale book
health · options breaker tripped or entries frozen · **TC prop room under $500** (it froze by
$45 once) · bars more than 3 days old · a trader that has stopped writing heartbeats · Trade
Cop verdict. First run surfaced ① immediately.

**④ THE CALENDAR QUERIED AN EMPTY TABLE.** `earnings_calendar` has **0 rows** and always has,
so the card was permanently blank — while **`catalyst_calendar` sat beside it with 2,437 rows
(915 upcoming)**, actively written by news_engine. Now queries that, deduped on symbol+date
(news_engine writes one row per pass), with `earnings_calendar` kept as a fallback.
⚠️ **Only 50% of `catalyst_calendar.event_date` values are real dates** — the rest are LLM
free text ("TBD", "ongoing", "tonight"), so a `GLOB` date filter is required. When no held
symbol has a dated event, it falls back to the next universe-wide catalysts rather than
rendering blank.
**Macro was empty for a different reason:** it shared the 30-day cutoff and `MACRO_EVENTS`'
nearest future entry is **2026-11-04**. Cutoff dropped; shows the next 4 regardless, because
"nothing for six weeks" is itself worth knowing. ⚠️ `MACRO_EVENTS` is hand-maintained and has
only **4 future entries** — October CPI/NFP are missing.

**⑤ "6t" spelled out** as "6 trades", with TODAY/7-DAY/WIN tooltips stating they count
**closed** trades only.

**SWEEP:** pyflakes clean · app.js syntax-checked in a real JS engine · every renderer
executed against the live payload · 3 test suites pass · both collectors re-run to exit 0 ·
dashboard restarted and serving.

---

## Sep 22 2026 (pm5) — service row restructured: one line per layer, and "idle" now says when it last ran

**① "WHY ARE THE ENGINES GREY?" — a fair question, and the chip was the problem.**
`idle` is correct for Wave Rider / Contrarian / Clockwork: they are scheduled jobs between
runs, not daemons. But a bare grey chip reads as **off**, which is alarming for a trading
engine. Grey alone also could not separate *"ran 35 seconds ago"* from *"has not run in
three days"* — and only one of those is healthy.
Every scheduled service now reports **when it last ran**, from its log mtime, with a
per-service staleness budget (5-minute jobs: 24h · daily jobs: 96h, to survive a long
weekend). A new `stale` state (amber ⏱) fires when a job is overdue.

    BOOKS   ● autotrader  ○ wave_rider 35s  ○ contrarian 2m  ○ clockwork 54s  ● options …
    DATA    ○ collect_bars 12m  ○ futures_bars 9m  ● news_engine  ○ field_report 12h

**② ONE ROW PER LAYER, each with its own colour.** 22 pills on a single wrapping line was
unreadable. Now five labelled rows — **Brokers** (blue) · **Books** (green) · **Data** (cyan) ·
**Instruments** (purple) · **Watchdogs** (amber) — in a `86px + 1fr` grid so the pills align.
`.statusbar` switched to `align-items: flex-start` since services is a five-row block now.
Verified in a real JS engine: 5 rows, 5 roles, 11 `up` / 10 `idle` / 1 `failing`, 10
"ran ago" chips.

**THE CHIP VOCABULARY, in one place:**
| chip | means |
|---|---|
| **green ●** | daemon holding a PID — genuinely alive |
| **grey ○ + age** | scheduled job between runs, and when it last ran. **Normal.** |
| **amber ⏱** | scheduled job overdue against its own budget |
| **amber !** | last run exited non-zero (for `trade_cop`, that means it FOUND a divergence) |
| **red ✕** | daemon with no PID — **not running** |

**SWEEP:** pyflakes clean · app.js syntax-checked in a real JS engine · every renderer
executed against the live payload · rendered HTML structurally verified · 3 test suites pass ·
dashboard restarted and serving.

**NEXT SESSION:** work the alerts — currently two, both real: the **Trade Cop divergence**
(Sep 21: entry-window violations on AXTI/BNC, and INTC with no graded scan_log signal behind
it) and **SHORT book health 53 days stale**.

---

## Sep 24 2026 — futures: today root-caused (it was the ENTRY), 6 defects fixed, TC was never the same system

**TODAY: IBKR −$401.22 · TC −$702.22 = −$1,103** (IBKR's figure is the CORRECTED one; see ①).

**① IT WAS NOT A LATE REACTION. There was nothing to react to.** The 11:20 VWAP_SHORT
(30517) spent 55 minutes going nowhere — **best-ever excursion +14.5pts**, oscillating ±40 —
then the single **12:15 bar ran 30556 → 30748 on 31,735 contracts vs ~4,000 normal (8× volume)**
and blew through the 200pt stop inside that one bar. Reversal Exit needs a +120pt peak to arm;
the trail tiers need +90/+110. **Neither could ever arm.** No exit operating on closed 5-min
bars can help here. This is the documented ORPHAN class (77% of trades, −$34k over 5.5yr,
19 of 19 entry features failed to predict it). Then the 13:05/13:20 PM_LONG bought back into
the top of that spike.
⭐ **The trade should not have been open, and a rule we have already validated said so:**
`[TIDE] would block (LOG ONLY): SHORT while prev close 30,747 is above the 200d MA 27,508`
— logged on BOTH accounts 47 seconds before entry. The Field Report also said **RISK_ON,
"favor momentum longs"** that morning. Both are log-only.

**② 🐛 EVERY FUTURES EXIT EVER TAKEN BOOKED AN ESTIMATE, NOT A FILL.** Both exit paths
recorded `price` — the `get_live_price()` read from the top of the monitor cycle — not the
fill. Today's stop really filled at **30717.50** (IBKR `GET /executions`, order 1315874,
16:17:38Z); we booked **30746.75**, a **−$461.36 loss that was really −$401.62**, because
price ran another 29.25pt in the ~40s before the monitor noticed the position was flat.
The pre-existing "actual fill" branch used `/order/{id}/status`, which returns
`avgFillPrice 0.0` for a filled-then-cancelled stop — **it has fired 0 times in 61
backup-stop exits**. FIXED: `_get_exit_fill_price()` reads `/executions`, matched on
orderId **and** side, volume-weighted across partial fills (TC's London order 79173 really
did fill 2c at two prices 30pt apart), falling back to the estimate on any failure so an
exit can never be stranded for want of a price. ⚠️ **Historical damage runs BOTH ways and
reaches ~$500 on single trades** — Jul 27 booked a **+$100 win on a trade that hit its
200pt stop**; Jul 23 booked +$3 on a −$236. Net across 61 exits is only +$256, but that is
cancellation, not accuracy. **Per-trade live analysis of exits before today is unreliable.**

**③ ⚠️⚠️ TC WAS NEVER RUNNING THE SAME SYSTEM. Four real divergences, all fixed.**
AST-diffed all 63 shared functions with comments stripped.
- **TC was missing the validated 14:00 ET entry cutoff entirely.** Cost on TC's own record:
  **12 entries at/after 14:00, 9 losers, −$988.46 = 22% of the automated book's whole loss**;
  −$620 of it is Sep 3/16/17, i.e. AFTER the duplicate-entry fix, so not a dup artifact.
  Also missing: the low-RVOL (0.3) and thin-IB (50pt) scan skips. All three ported.
- **TC's software exit never checked whether its own order succeeded.** It cancelled the
  backup stop, fired a market order, and closed the DB row without reading the result — a
  rejected exit left a **REAL, LIVE, UNPROTECTED position recorded as CLOSED**. IBKR has had
  this guard all along. On a funded prop account that is how a trailing MLL gets blown.
- **TC's stop-replace failure was silent**: on a failed trail update it wrote
  `stop_order_id=NULL` plus the NEW stop_price and alerted nobody — no stop at the broker,
  a DB row claiming one. IBKR alerts and leaves the row untouched.
- `calc_htf_trend` hardcoded `30min` instead of `HTF_BARS_MIN`, **which TC never defined**.
⭐ The Jul 25 2026 commit `72b3bb5` is recorded as having brought TC to entry parity. It
brought the GRADE/hero/RVOL_ENTRY/HTF stack and missed these. **Every IBKR-vs-TC comparison
in this program has therefore compared two different strategies, not two accounts.**

**④ 🐛 THE TWO ACCOUNTS SAMPLED THE TAPE AT DIFFERENT INSTANTS, BY ACCIDENT.**
Both used an unanchored 60s interval. Restarted in the **same second** (Sep 22 23:18:54),
IBKR reached the read at **:59** and TC at **:00** — opposite sides of `calc_session_rvol`'s
hard `now - bar_ts < 300` test (at 299s the newest bar is dropped as forming; at 300s it is
kept). Their `regime_detail` differed on **145 of 208 bar-close minutes (70%) and 0 of 828
other minutes**. It decided a real trade:
`13:04:59 IBKR A+ LONG, rvol 0.71 (stale) → RVOL SKIP` · `13:05:00 TC same signal,
rvol 1.28 → ENTERED, −$300` · `13:05:59 IBKR rvol 1.30 passes but the signal is GONE` →
IBKR entered at 13:19, **75pts better, +$1.64**.
⭐ **The race is arbitrary, not directional — being stale was LUCKY here.** That is the
point: neither account matched `sim_replay`, which evaluates at bar close, and the phase
was re-rolled silently on every restart. **SHIPPED `SCAN_ANCHOR_SECOND = 15`** (cron, fixed
second, 60s cadence unchanged; 15 not 0 because the just-closed bar's volume is still
settling at :00 — 1.28 vs 1.30 on the same bar today). Fourth fix in the family of the
Jul 17 RVOL, Jul 18 HTF and Aug 24 regime forming-bar bugs, shipped on the same grounds:
live now samples where the validated sim always did. **It does change which trades fire —
that is the fix — and it is neutral in expectation, not an edge improvement.** `None` reverts.
Verified post-restart: both accounts log at identical timestamps and agree exactly.

**⑤ 🐛 The running peak lived only in memory.** `max_gain_ticks` was NULL in **all 208 rows**,
and a mid-position restart reset the peak — **disarming the Reversal Exit, the only exit
mechanism in this book with a 100% win rate (n=11, +$2,272 avg +$207)**. Observed live
Sep 3: a 13:10 restart took the peak +166 → +118 and unarmed it. Flagged then as a candidate
fix, not built. Now persisted on every improvement and restored on first touch after a restart.

**⑥ The NY logs had no DATE** — only `[HH:MM:SS]` on 30MB rolling files, so today's session
had to be located by bracketing it between the dated `[LON:*]` lines that happen to share the
file. Now dated, matching the London logger.

**⑦ OPS: the TC bridge (port 8002) was running Sep 3 code** — 21 days and 3 commits stale,
with **no `/executions` at all**, so TC's fills could not be checked even in principle.
Restarted, DUQ640500 reconnected. TC's real errors today turn out small ($2–3); IBKR's was
$60 only because it exited into the violent bar.

**⑧ THE HONEST SCOREBOARD — the headline has been hiding the book.**
Automated NY futures, June → Sep 24, RECONCILED and partials excluded:

| | n | P&L |
|---|---|---|
| **automated book** | **176** | **−$4,535.52** |
| manual `FUT CLOSE` (the user's hand) | 18 | **+$4,209.50** |
| LONG side | 110 | −$1,040.64 — **negative in all 4 months** |
| SHORT side | 66 | **−$3,494.88** (77% of the loss; Aug −$2,235, Sep −$1,297) |

Exit-bucket P&L: Reversal exit **+$2,272 (n=11)** · trailing-stop exits +$1,804 · no-move
+$243 · target +$256 — **every adaptive exit makes money**; the loss is entirely in trades
that never earn one (backup-stop −$3,313/59, circuit breaker −$5,757/16, EOD −$701/10).
⚠️ **The circuit breaker is NOT the leak** — counterfactual against real bars says holding
those 16 instead would have made **−$5,115 vs −$5,757**, i.e. the breaker cost $642 net, and
**11 of 16 times it SAVED money**; the one +$1,267 case (Jun 16) dominates the average. It is
where the day's damage gets *booked*, not caused. **Do not loosen it.**

**⑨ DECISION PENDING — the Daily Tide. I did not flip it; it is a strategy change.**
`TIDE_GATE_ENABLED` is still `False` (log-only). Scored on the automated live book:

| month | kept (long) | blocked (short) | as traded | with Tide ON |
|---|---|---|---|---|
| 2026-06 | −350.50 | **+238.00** | −112.50 | −350.50 |
| 2026-07 | −149.00 | −564.50 | −713.50 | −149.00 |
| 2026-08 | −241.50 | −2,235.00 | −2,476.50 | −241.50 |
| 2026-09 | −299.64 | −1,238.38 | −1,538.02 | −299.64 |
| **total** | **−1,040.64** | **−3,799.88** | **−4,840.52** | **−1,040.64** |

**+$3,800 improvement over 175 trades, helps in 3 of 4 months.** Backed by the 5.5yr
pipeline-confirmed run (+$9,469 vs +$2,622 at $6/contract, green 6/6). Today it would have
blocked the −$801 short pair, leaving the day at about **−$298 instead of −$1,103**.
⚠️ **Read the costs honestly before flipping:** it removes **30 winners worth +$3,674**
(the 65 blocked trades were 35 losers/−$7,474 and 30 winners); **MNQ has been above its
200d MA on every live trading day, so in practice this IS "long-only"** until MNQ breaks
below it — the live record cannot distinguish the two; and **the kept long side is still
negative in every month** — it removes ~77% of the loss, it does not make the book
profitable. Its own log-only trial has just **16 rows** (the logging was broken for three
weeks until `090935b`/`aba23ce`), so the live case rests on the trade-level rescoring above,
not on the gate's own trial.

**⑩ INSTRUMENTATION AUDIT (all of it).** `gate_blocks` 49,940 rows, current, scored.
`gate_blocks_ctx` 28,953 rows, current (nightly 22:35). Field Report current, 50 briefs.
Heartbeat healthy, both bridges connected, London STALE only outside its 03:00–08:59 window
(by design). **Crest Watch: 33 checks, 5 trades, 4 days — 18 errors, ALL `api 400` on Sep 3
only, clean since; recovered, not broken.** It fires only above a +100pt peak, which 23% of
trades reach, so at ~2 trades/week it needs months. **Not evaluable.**
**Mirror Book review (was due ~Aug 17, 5 weeks overdue) — ANSWER: NO.** 258 shadow trades
Jun 22–Sep 22, +352pts/+$704 total, **37.6% WR, and negative in 3 of 4 months** (Jun −$454,
Jul **+$3,025**, Aug −$265, Sep −$1,602). Its entire edge is July. Same one-good-month shape
as everything else here. Do not promote.
**Parity cop** flagged a real Sep 23 divergence (live-only ORB_SHORT at 10:35); ④ is now the
leading candidate explanation and should be re-checked once a week of anchored data exists.

**OPEN / NEXT.** (a) **The Daily Tide decision — ⑨.** (b) A **volume-spike guard** is the only
mechanism that speaks to today's actual loss, and it is unbuilt and untested: the 12:15 bar
was 8× normal volume. Whether an 8×-volume adverse bar can be reacted to *at all* on 5-min
closes is exactly what six independent exit studies say no to — treat it as a research
question, not a fix. (c) TC's `eod_snapshot` has no signal-funnel line (IBKR's does) — the
account heading for a funded eval gets less reporting. (d) `contract=unset` appears in 25,848
log lines; pre-existing and cosmetic, both bridges resolve `20261218` correctly. (e) The
evidence clock **restarted today** for entry timing — ④ changes which trades fire, so the
Sep 3 clock now applies only to the exit stack.

---

## Sep 24 2026 (pm) — the asymmetry investigated: "we lose big" SOLVED (already shipped), sizing rejected a 4th time, Tide refinements all FAIL

User declined to ship the Tide until the short-side/reversal/asymmetry questions were investigated.
All figures: 5.5yr sim book (`_mom_2021-06-01_2026-08-14.csv`), $6/contract, day features causal
(prev-session daily values + 9:30-10:30 IB only). Feature table:
`scratchpad/dayfeat.csv` recipe in-session. **Nothing shipped in this pass.**

**① THE ASYMMETRY IS REAL AND MEASURABLE — and the Tide fixes it.** At flat 1 contract:

| | win rate | avg win | avg loss | payoff | needed | verdict |
|---|---|---|---|---|---|---|
| baseline | 51.6% | +$107.71 | −$117.23 | **0.89** | 0.94 | **FAILS** |
| Daily Tide | 54.2% | +$105.58 | −$99.89 | **1.06** | 0.85 | **PASSES** |

⭐ **The whole deficit is a 0.05 payoff gap.** And the Tide closes it the way the user asked —
**losses get smaller, not wins bigger**: avg loss −$117 → −$100, trades worse than −$300 fall
63 → 27 (6.6% → 5.0%), avg win essentially unchanged. **The "make wins bigger" half is not
available**: 7 prior exit tests plus this session's no-move counterfactual all reject it.

**② ⭐ "WHEN WE LOSE WE LOSE BIG" — ROOT-CAUSED, AND THE FIX SHIPPED THREE WEEKS AGO.**
**All 12 trades worse than −$600 in 5.5 years are 2-CONTRACT trades. 8 of the 12 are SHORT.**
Worst 1-contract loss is −$413; worst 2-contract loss −$825. The <−$600 bucket is −$9,903.
`SHORT_MAX_CONTRACTS=1` (shipped Sep 2 2026) already halves 8 of those 12.

| step | n | total | maxDD | worst trade | green | payoff/need | /wk |
|---|---|---|---|---|---|---|---|
| original (pre-Sep-2) | 948 | −$3,319 | −11,988 | −825 | 2/6 | 0.89/0.94 | 3.30 |
| **+ SHORT cap (LIVE NOW)** | 948 | **−$447** | −10,223 | −825 | 2/6 | 0.93/0.94 | 3.30 |
| + Daily Tide (pending) | 541 | **+$6,833** | −3,672 | −825 | **4/6** | 1.06/0.85 | 1.89 |

Per year, the shipped short cap alone: 2021 −1,160 · 2022 −2,615 · 2023 −2,634 · 2024 −2,650 ·
2025 +1,008 · 2026 +7,604 (ex-2026 −8,051). **+$2,871 vs original.**
With the Tide: −178 · **+1,668** · +22 · −1,041 · +528 · +5,835, **ex-2026 +$999** — still the
only configuration that is positive ex-2026 and the only one that survives 2022.

**③ CONVICTION SIZING IS SIDE-SPECIFIC, and the live config is already correct.** The 2c hero
ladder: **LONGS +$689 and it helped in 5 of 6 years; SHORTS −$2,871.** So the Sep-2 cap was
aimed exactly right. ⚠️ **Do NOT cap longs as well on IBKR** — costs $671.
**BUT worth considering for TC only:** capping longs too takes the worst trade −$825 → **−$413**
and the worst DAY −$825 → **−$433** for $671 over 5.5yr (~$122/yr). Against a $2,000 trailing
MLL that is probably the right purchase. Decision, not shipped.

**④ ❌ "RISK LESS ON BAD DAYS, MORE ON GOOD DAYS" — TESTED AND REJECTED (4th sizing rejection).**
Built a 0-3 "day vibe" score (MA200 slope agrees + first hour wide vs ATR + not shorting after a
down day), thresholds from 2021-24 only, and sized 2c on good days / 1c on the rest.
**Per unit of exposure it LOSES: Tide flat-1c = +6,162 per unit vs vibe-sized = +5,119 per unit**
(+$9,726 total but avgC 1.90 — the headline gain is pure leverage, and maxDD doubles to −7,490).
⭐⭐ **The decisive tell is the INVERSE CONTROL: sizing up on BAD days scored better in train
(+$2,018 vs −$1,700) with half the drawdown.** When both directions "work", the score is noise.
Per-quality-bucket P&L inside the Tide is non-monotonic and inverted (q0 +$24.5 · q1 +$51.7 ·
q2 +$1.0 · q3 +$15.7). **Sizing is not the lever — joins ATR-normalised sizing, room (3×), and
the hero ladder as rejected.**

**⑤ ❌ CAN THE TIDE BE MADE MORE MARKET-AWARE? NO — every refinement made it worse.**
Train/test split (thresholds from 2021-24, tested on 2025-26):

| config | n | total | green | TRAIN | payoff |
|---|---|---|---|---|---|
| **Daily Tide (level only)** | 541 | **+6,200** | 3/6 | **+516** | 1.03 |
| + MA200 slope agrees | 487 | +4,326 | 2/6 | −1,146 | 1.06 |
| + wide first hour (IB/ATR) | 255 | +4,531 | 3/6 | −912 | 1.11 |
| + no short after a down day | 483 | +4,538 | 2/6 | −1,365 | 1.05 |
| + all three | 210 | +3,615 | 2/6 | −1,800 | **1.25** |

Every addition cuts total AND flips TRAIN negative. They *do* improve payoff (1.03 → 1.25) but
by removing so many trades that the book shrinks faster than it improves.
⭐ **The answer to "how do we get the vibe of the day": the vibe IS the Tide.** Every feature
that separated in the single-feature scan — `ma200_slope` (short side, spread −$51/trade,
monotonic), `dist200` (long side, +$36), `ib_vs_atr` (long side, +$35), `pd_ret` (short side,
+$31) — is a *trend-context* measure. They add nothing on top of each other because they are the
same information. You do not need a composite; you need the one thing.

**⑥ COULD WE HAVE PREDICTED THE 12:15 SPIKE? NO — quantified.** Bars with ≥5× the prior hour's
volume AND ≥100pt range: **29 in 5.5 years, 0.03% of bars.** The bar immediately before one has
median volume **0.88× vs 0.84×** for ordinary bars — indistinguishable. **Today's 12:10 bar was
0.82×, quieter than average.** A "prior bar ≥1.5×" warning catches 34% of shocks while firing on
7% of all bars = **0.1% precision**. There is no precursor to build on. Do not revisit without a
genuinely new data source (order flow / depth), not a new statistic on the same bars.

**⑦ THE BIG-LOSS COHORT — what the sibling days share.** 63 trades worse than −$300 = −$25,690.
**The Tide blocks 36 of them (57%), worth −$14,614**; it lets 27 through (−$11,075).
⚠️ **38 of the 63 are LONG (−$15,622) vs 25 SHORT (−$10,068) — big losses are NOT a short-side
problem.** Median profile vs all other days: `pd_ret` −0.34 vs +0.10 · `pd_close_pos` 0.47 vs
0.61 · `gap_pct` −0.17 vs +0.04 · `dist200` **+4.10 vs +8.82** (big losses happen CLOSER to the
200d MA). **Today matched three of four** (gapped down −0.74%, prior day fell −0.92%, prior day
closed at 0.39 of range) — but `dist200` was +11.8%, the safe end. And ⑤ already showed these
fail as filters. Real pattern, not a tradeable one.

**⑧ THE NO-MOVE RULE IS THE UPSIDE CAP — and removing it is still not shippable.** 588 of 949
trades (62%) exit on the 90-minute no-move rule; its best outcome ever is **+$215** while the stop
sets the downside at −$400/−$825. That is the payoff asymmetry, by construction. Counterfactual
(hold each to its own stop or 15:55): **+$7,195 vs −$2,088, delta +$9,283, only 5% would have hit
their stop** — but **green only 3/6, carried by 2022 (+$5,065), and NEGATIVE in both 2025 (−$385)
and 2026 (−$659)**. In the current regime the rule is helping. Not shippable; logged.

**⑨ "WE BLEED IN CHOP" — half right, and the half that is wrong matters.** Raw CHOPPY-day P&L is
−$7,106 over 486 trades, losing in 5 of 6 years. But the 100/200pt thresholds are FIXED POINTS set
when MNQ was 13,000 and it is now 30,700: the CHOP label covers 50% of 2021 and 35% of 2026 while
TREND covers 6% of 2021 and **62% of 2026**. Re-bucketed by IB% **within each year**, the spread
collapses from **$30.5/trade to $10.9/trade** — same volatility-drift artifact as the Aug 24 ATR
finding. And it is **redundant with the Tide**: Tide alone +$6,200 vs Tide + chop filter +$4,234.
⭐ **The chop bleed and the wrong-side bleed were largely the same trades.**

**⑩ Day-shape (`_ib_kind`) is noise — a 4-month illusion.** Live Jun-Sep said BULL_DIRECTIONAL +
LONG was the one good cell (+$32.54/trade, n=37). Over 5.5yr it is **+$0.2/trade over 339 trades,
green 3/6**, and ROT+LONG *flips sign* between the live and sim samples. **Do not build on
`_ib_kind`.** Method note: this is the third time this program has been shown a strong 4-month
cell that vanished cross-year (cf. the Sep 5 `day_chg>=7%` curve-fit).

**OPEN / NEXT.** (a) **The Tide decision still stands open** (⑨/② above; +$7,281 on top of the
live config, green 2/6 → 4/6, payoff 0.93 → 1.06, 3.30 → 1.89 trades/week). (b) **Cap longs at 1
on TC only** — halves the worst trade for ~$122/yr, sensible against a $2,000 trailing MLL. (c) Do
not re-test: sizing (4 rejections), Tide refinements (⑤), chop as a separate gate (⑨), `_ib_kind`
(⑩), spike prediction on bar data (⑥). (d) The next honest day-level candidates are ones NOT
tested here: multi-day trend memory (the book starts every day stateless at the 10:30 IB) and
prior-day close location as a *stand-down* rather than a side filter.

---

## Sep 25 2026 — futures deep review: live was not running the validated strategy (5 parity defects fixed) · the Tide becomes the strongest config ever measured · MNQ's edge is overnight

User asked for a no-assumptions re-think after the Sep 24 loss was written off as "unforeseeable".
**It was not.** Scratch labs in the session scratchpad; A/B runner `futures/factory/_ibab.py`.

**① Sep 24's −$1,103 ROOT-CAUSED — a live-only bug, and the Trade Cop flagged it that night.**
Live fetches bars with `rth=false` and took "today" as `index.date == today` = since MIDNIGHT. So
VWAP, the regime's session open, its prev close (the 23:55 bar — why `day_chg` and `sess_chg`
always logged identical values), the Day Shape IB close-location, and the hero score's prior-day
high/low/close/POC were all anchored to the overnight session. Sep 24 11:20: price 30,518.50 was
EXACTLY the 09:30 open (30,519) — the RTH session had gone nowhere, the whole drop was overnight.
Live measured −0.46% from the midnight bar → WEAK×3 → shorted at RSI 30. The sim (RTH-anchored)
measured ~0%, its chop filter held NORMAL, and it never shorted — parity.log Sep 24: *"live-only
trade (sim never took it): 11:21 SHORT VWAP_SHORT"*. Replayed the real bars through live
`get_regime`: OLD = WEAK 11:00–11:20, NEW = NORMAL/choppy all morning, VWAP 30,571.08 = the sim's.
**FIXED (both traders): `_rth_today()`, `_prev_rth_close()`, `_prev_rth_bars()`** feed VWAP,
regime, open-play, IB Day Shape (09:30–10:30 only) and the hero score's prior session.

**② Scan-level RVOL gate read the FORMING bar — killed 92% of bar-close scans.** `calc_rvol_current`
divides seconds of volume by a full-bar average; IBKR 10:30–14:00 since Aug 24: **550 of 597
first-minute scans skipped** — exactly the moment the sim decides. Cost a real trade: Sep 21
11:40 TC entered PM_LONG **+$669** while IBKR read 0.00x the same minute and skipped. TC gained the
same gate Sep 24. **FIXED: new `calc_rvol_completed()` for the gate only.** `calc_rvol_current`
still feeds sizing, deliberately — ⚠️ **live SIZING ≠ sim sizing** (live: RVOL/IB-range/had_loss
ladder on the forming bar; sim: hero-score ladder). Open decision, not a bug fix.

**③ Contract roll: IBKR could not trade for 4 sessions (Sep 15–18).** `_active_contract_month`
was only set inside `get_live_price()`, which `run_scan()` never calls — after every restart bars
came from IBKR ContFuture (26,444 `contract=unset` lines). ContFuture stayed on the expiring
September contract through expiry while orders had rolled to December → signals and fills on two
contracts, and the dying contract's volume drove the scan gate to RVOL 0.107/0.048/0.045/**0.000**
while real volume was normal (145–170k/session). **FIXED: `_ensure_contract_month()`** pins bars to
the order contract (15-min recheck). Verified: first scan after restart logs `contract=20261218`.
**`bridge.py` roll cutoff 5 → 9 days** = CME's roll Thursday (Dec 2026: rolls Thu Dec 10). Both
bridges restarted, reconnected (DU9952463 / DUQ640500).

**④ Entry fill double-charged commission.** IBKR `avgCost` folds the $0.62/side commission into
the basis, so every entry since Sep 3 was booked 0.31pt worse (TC order 85514: real 30868.00,
avg_cost 30868.31) and `_net_usd` charged it again. **FIXED: `/executions` is now the primary
entry source** (orderId + side), avg_cost fallback de-commissioned and tick-snapped. Historical
rows NOT rewritten (~$0.62/contract/trade).

**⑤ The sim classified the day at 09:45, live at 10:30.** Sim classified on the first bar whose
range hit 50pts = the 09:45 bar (20-min range) EVERY year; live uses the 60-min IB. Agreement:
day label 53%, Day Shape 44% (2026: live TRENDING 116 days, sim 45). Every exit-lock tuning was
fit on labels live never used. **`IB_CLASSIFY_AT_1030 = True` is now the sim DEFAULT**
(`--legacy-ib-0945` reproduces all prior results — verified line-for-line).

**⑥ 5.5yr within-engine A/B (Jun 2021 → Sep 24 2026, $6/contract, one variable):**

| config | net | maxDD | worst day | green | payoff |
|---|---|---|---|---|---|
| legacy sim (09:45 day label) | +$4,074 | −6,735 | −814 | 2/6 | 0.92 |
| **as live runs (10:30)** | **+$2,836** | −4,805 | **−1,222** | 2/6 | 0.89 |
| **as live + Daily Tide** | **+$12,153** | **−2,943** | −814 | **5/6** | **1.06** |
| as live, LONG only | +$395 | −5,221 | −1,222 | 3/6 | 0.83 |

The live book has been worse than every number we quoted, and its worst day breaches TC's $1,000
DLL. **With the Tide it is the best config this program has measured; 2022 = +$3,636.** ⚠️ **The
Tide is NOT long-only** — long-only on the real classification loses $4,127 in 2022; the short
leg in a genuine bear tape is what the Tide keeps. Post-Aug-24 live: Tide would have added +$202
IBKR / +$734 TC. **DECISION PENDING (user).** Per-year: 182 / 3,636 / 925 / −572 / 2,245 / 5,737.

**⑦ Live vs sim, Jun 1 → Sep 24, trade-matched (±15 min):** IBKR live 70 events −$290, only 16
match the sim; 54 live-only −$1,670; **37 sim-only +$4,856 never taken.** TC: 89 live-only
−$1,957, 36 sim-only +$5,286. On MATCHED trades live did better than sim (+$1,380 vs +$235) ⇒
execution is fine, SELECTION diverged — the Aug 16 finding, now with mechanisms (①②③⑤ + the
pre-Aug-24 duplicates + since-removed gates). ⚠️ Honest scope: **post-Aug-24 the gap mostly
closes** (sim +$269/11 vs IBKR live +$891/11; TC −$767 of which −$704 was the after-14:00 trades
fixed Sep 24). **STILL OPEN: `get_signals()` computes every entry signal on the forming bar**
(price, 3-bar VWAP streak, momentum, ORB/PM break) while the sim decides once per completed bar.
Not changed — on current code it has not measurably hurt; re-run the match after 4–6 weeks.

**⑧ ⭐ WHERE MNQ ACTUALLY PAYS — the equity overnight finding holds for futures.** 1 MNQ, Tide-up
days only, $3.24/round trip, 2021→2026: **15:55→next 09:30 hold +$27,218, Sharpe 1.29** vs
**09:30→15:55 hold −$559, Sharpe −0.02** vs our 10:30→13:55 window +$6,413 (0.38). It is the
WINDOW, not bull beta: the RTH tape we trade has no drift under the Tide — which is WHY nine
"let winners run" exit studies all failed. Best variant **15:55→02:55 (exit before London):
+$21,615, Sharpe 1.32, maxDD −$3,997, positive all 6 years**; Mon–Thu Sharpe 1.44; 02:55→09:30 adds
nothing (0.16). ⚠️ First half (2021–mid 2024) Sharpe 0.57, second half 1.76. Largest after RED
RTH days (≤ −0.5%: $36.7/night, Sharpe 2.01, 6/6) — NOT after the book's winning days (average).
Uncorrelated with the NY book (−0.01): diversifies (Tide-up days Sharpe −0.34 → 0.85), but not
a hedge (recovers 7% of bad days; worst day −$825 → −$1,933 from gaps). **IBKR only** — TopStep
forbids overnight holds; margin ~$6.2k/contract. **Candidate "Night Tide" book — NOT BUILT.**

**⑨ LONDON IS THE MOST CONSISTENT FUTURES BOOK.** IBKR London since Jun: **+$2,481 / 112 trades,
+$1,105 ex-top-5%, 31 green days vs 22, worst day −$328**; Sep +$1,921. TC London Sep **+$1,013/28**
(first real month since the Sep 7 gateway fix). Live rows are net of commission on real paper
fills; the "doesn't survive costs" verdict came from the SIM. Keep running; stop calling it dead.

**OPEN / NEXT, in order.** (a) **Tide: ship live on both accounts** — the recommendation.
(b) TC only: cap longs at 1c (worst trade −$825 → −$413 for ~$122/yr) against the $2k trailing
MLL. (c) Night Tide as an IBKR paper book (15:55→02:55, Tide-up, 1c). (d) Live sizing ladder vs
the sim's hero ladder — pick one and make both use it. (e) Forming-bar `get_signals` — re-measure
in 4–6 weeks. (f) ⚠️ Every 5.5yr number before today used the 09:45 day label; re-run anything
still being relied on. **Evidence clock for NY entries restarts Sep 28** (①②③ change which
trades fire — toward the sim).

---

## Sep 28 2026 — Dashboard hosting: ngrok → Tailscale Funnel + MFA

**ngrok free plan ran out** (1 GB/month data; a separate one-time $5 credit was also being
drained by the 24/7 endpoint). Cause: `/api/data` was 69 KB **uncompressed, every 30 s**, even
in hidden tabs — ~8 MB/hour per open tab. Fixed in code: gzip `after_request` in
`dashboard/app.py` (69 → 12 KB) and `app.js` now polls only while the tab is visible.

**Now public at `https://trivega.bombay-pomfret.ts.net`** via Tailscale Funnel → `127.0.0.1:8080`
(free Personal plan, fixed URL, Let's Encrypt cert auto-renewed). Chosen over private tailnet
access because the user views it from an employer MacBook where installing Tailscale is not
appropriate. **Funnel has NO auth of its own — the login page is the only wall**, so it now needs
**password + a 6-digit authenticator code** (TOTP, `dashboard/totp.py`, stdlib, RFC-vector tested),
code checked only after the password passes, single-use; lockout 5 fails/IP or 20 global per
15 min; `/api/*` → 401 when signed out; separate `DASHBOARD_SECRET_KEY`. Setup/rotate/check:
`venv/bin/python dashboard/totp_setup.py [--rotate | --check CODE]` (QR opens on the Mac mini
screen only). Verified from outside: login page only, data/close endpoints 401.
Tailscale CLI: `/Applications/Tailscale.app/Contents/MacOS/Tailscale funnel status` (run in the
foreground; off = `funnel --https=443 off`). ngrok plist parked as
`~/Library/LaunchAgents/com.sushil.trading.dashboard_tunnel.plist.disabled-2026-09-28`.

---

## Sep 29 2026 — futures review: bad days are shorts against the tide; "win big" and "lose small via exits" both closed

Labs in the session scratchpad (`pyramid.py`, `feats.py`, `stopw.py`, `astdiff.py`/`srcdiff.py`).
Baseline = the Sep 25 "as live" book `futures/factory/_ibab_on.csv` (10:30 IB, $6/contract).
**No code changed.**

**① Sep 28 (IBKR −$307, TC −$304) — both accounts shorted at 10:40 (RSI 24.6, Tide "would block"),
Reversal Exit banked +$116 at 11:05, then re-entered the SAME short at 11:07 (score 100 vs 115,
`trend_up=True`, RVOL passed only via Hero-GOLD) → 200pt stop −$423.** Trade Cop flagged 11:07 as
live-only (entered 2 min into the forming 11:05 bar, which closed +50pts). ⚠️ **But the sim lost
too**: it shorted one bar later at 30386.75, never reached +120, stopped for −$401. The day lost
because both sim and live shorted a tape 12% above its 200d MA — not because of the re-entry.

**② THE BOOK IN ONE LINE:** 75 Reversal Exits **+$15,836** vs 151 stop-outs **−$14,307**; the
other 793 trades net ~+$1,300. Winning big already happens. The question is only the losing side.

**③ REJECTED (do not re-test without a new mechanism):**
- **Pyramiding** (add 1c once a trade is +60/+80/+100/+120/+150): negative at every threshold,
  green 1-2/6. After a trade proves itself, the rest of its path mean-reverts (same as equity).
- **Narrower initial stop** inside the Tide book: 100/125/150/175pt all make LESS than 200pt
  (−$1,698…−$2,953); avg loss does not shrink because more trades get stopped. **10th
  confirmation that cutting earlier costs more than it saves.**
- **RSI-exhaustion filter** ("don't short oversold"): buckets flip sign by side and neighbour. Noise.
- **Crest Watch as a bad-day tool**: 19 valid checks on 5 trades, all 5 finished green — it only
  sees trades already past +100pts, and the stop-outs never get there. Structurally blind to losers.

**④ LEAD, not proven:** no same-side re-entry after a Reversal Exit — 24 such trades −$1,366,
negative in 4/4 years with any; on top of the Tide +$861 (13 trades). But the ≤15-min re-entries
(yesterday's type) are +$49/9 in sim, and the Jul-25 design's best re-entry (+$749) would be lost.

**⑤ THE TIDE CASE, all four lines of evidence now agree:** 5.5yr pipeline +$12,153 vs +$2,836,
green 5/6, worst day −814 vs −1,222 · log-only trial 78 signals, blocked shorts −23pts at 60m,
right 64% · **live automated since Aug 24: IBKR +$646 → +$1,155 (days ≤−$200: 3 → 0), TC
−$1,003 → +$36 (4 → 2)** · every bad day of the last week (Sep 23/24/28) was a short it logged
"would block". Cost: in the current tape it is long-only (gave back Sep 1 +$211, Sep 28 +$116).
**Tide + every trade at 1 contract:** −$422 over 5.5yr, worst day −$814 → **−$503**.

**⑥ Residual bad days inside the Tide book:** 32 days −$14,422; 30 of 32 are "the first trade went
straight against us" (median best excursion 15pts vs 42pts on all days) → the ORPHAN class, no
precursor found (19+ features, spike study). Not solvable by exits or filters we have; only by size.

**⑦ CODE STATE:** IBKR vs TC now run the same entry/exit logic (12 of 71 shared functions differ,
all cosmetic / Elephant / timestamp placement). Contract pinned (0 `contract=unset`). Today (Sep 29)
both accounts made the identical RVOL-skip decision in the same second. **Still open: `get_signals()`
reads the forming bar** (yesterday's 11:07 entry is an example).

**⑧ 🚨 TC IS $281 FROM FREEZING AGAIN.** balance $48,581, trailing floor $48,000, +$300 buffer ⇒
`check_can_trade()` fails below $48,300. One 200pt stop ≈ −$402. Same deadlock as Sep 3.
Decision (user's): reset the paper baseline, or let it freeze as the honest TopStep verdict.

### Sep 29 2026 (pm) — ✅ DAILY TIDE LIVE on both accounts · sim parity defect fixed · unfinished bar did not hurt

**SHIPPED (user-approved): `TIDE_GATE_ENABLED = True`** in `futures_trader.py` + `tc_trader.py`.
LONG only when the previous daily close is above its previous 200d MA, SHORT only below. Gate
sits in `place_trade()` after every cooldown/count check and has no side effects; LONG is
evaluated before SHORT each scan, so a blocked short can never hide a long. Both traders
restarted 18:33 ET while flat; verified loaded: prev close 30,442 vs MA200 27,585 ⇒
LONG allowed, SHORT blocked. User declined the TC 1-contract cap and the re-entry rule.

**🐛 SIM PARITY DEFECT FIXED — `_run_scenario` hardcoded `large_ib_gate_pts=200, early_ib_pts=200`.**
Every simulated day (Trade Cop, factory bench, every 5.5yr number) (a) held entries to 10:45
when the 09:30-10:30 range exceeded 200pts and (b) started trading at 10:00 when the range hit
200pts by 10:00. **Live has had neither since Jul 7 2026.** Found tracing Sep 28 (sim entered
10:45, live 10:40). Defaults now 0/0 = live; `--legacy-ib-gates` (or `LEGACY_IB_GATES=True`)
reproduces every older result — verified: legacy Tide-off = the Sep 25 book exactly (1,019t,
+$9,634 raw). Sep 28 now replays identically to live (10:35 short, rev_exit +$115, 11:05
re-entry, stop). New `--tide` flag in sim_replay (reads `futures_bars_1d`, same causal anchor
as live, fails open); added to `parity_check.SIM_FLAGS`. Runner `futures/factory/_tideab2.py`.

**5.5yr, $6/contract, live-parity sim (one script, one variable):**
| | n | net | maxDD | worst day | green |
|---|---|---|---|---|---|
| Tide off | 1,008 | +$3,609 | −4,461 | −1,222 | 3/6 |
| **Tide ON (= live now)** | **588** | **+$9,916** | **−2,755** | **−814** | **5/6** |
Per year ON: 149 / 2,184 / 1,282 / −385 / 975 / 5,711. Short leg +$2,634 (bear years).
⚠️ Every pre-Sep-29 5.5yr number was on the legacy gates; the Sep 25 "+$12,153" is superseded.

**UNFINISHED (FORMING) BAR — did it hurt? No measurable harm.** Since the scan anchor (Sep 25):
9 bars had an A+ read mid-bar; the finished bar confirmed 7; the 2 it rejected (both LONG) never
became trades. Sep 28's 11:07 re-entry was confirmed A+ by the finished bar AND got a 21pt
BETTER price than the sim's bar-close entry — the loss was the short itself. Live P&L split by
position-in-bar flips sign between eras (Jun-Aug mid-bar −$2/trade vs bar-start −$72; since
Aug 24 −$76 vs −$45, ~16 independent trades) — inconclusive. **Left unchanged; the Trade Cop
now runs at true parity, so any trade the unfinished bar causes will show as live-only.**
If live-only trades net negative over ~4 weeks, the fix is to allow entries only on the first
scan after a bar closes (bar <60s old).

### Sep 29 2026 (night) — Tide audited (data bug fixed, no smarter variant) · TC deep dive: state file was wrong, book is slow not risky

**Tide data bug FIXED (`0cc8a40`).** `futures_bars_1d` was written at 21:30 ET with INSERT OR IGNORE; at that
hour yfinance's newest daily bar is the NEXT session labelled with its start date, so half-built bars froze
as closes (63 of 2026's rows; Sep 28 "close" 30,442 = the 21:25 price, real ~30,566). New
`futures/daily_tide.py` is the one Tide for both traders + sim: session close = our own 15:55 5-min bar close,
yfinance only as fallback; STALE warning if the latest close predates the previous weekday.
`collect_bars.store_daily()` now skips today-or-later bars and overwrites (repaired; old rows in
`futures_bars_1d_bak_20260929`). Decision impact: 1 day in 5.3yr (2022-02-10, price on the MA).
**Variant sweep (live-parity Tide-off book, frame filter):** SMA150 +$9,331 · **SMA200 +$10,162** · SMA250
+$8,787 · EMA200 +$9,822 · bands, N-day hysteresis, slope all worse · long-only +$4,410 (2022 −$4,461).
SMA200 is the centre of a 150-250 plateau. The 427 trades it removes net −$6,553. Keep as is.

**🐛 TC STATE FILE WAS WRONG — London P&L erased nightly.** `eod_snapshot()` passed NY-only P&L to
`update_eod_balance`, whose `eod_pnl - session_pnl` subtracted every London trade (already in session_pnl via
london_trader) back out. State said balance $48,581.30 / −$1,418.70 (= NY −$918.70 − a $500.00 carryover
matching the pre-reset file). **Truth from the ledger: balance $50,239.94, +$239.94, HWM $51,090.38, room to
the block $849.56 (not $281).** Code fixed (NY + London reconcile). ⚠️ `futures/prop_state.json` itself NOT
yet corrected — awaiting user.

**TopStep gauntlet (live-parity Tide-ON NY book, every start date, real order, our soft rules, $6/c):**
| config | pass ≤3mo | ≤6mo | blow ≤6mo |
|---|---|---|---|
| today's sizing (1-2c) | 3% | 10% | 0% |
| 2c every long (shorts 1c) | 14% | 25% | 7% |
| 3c every long | 20% | 32% | 19% (one 3c stop = −$1,206 > $1,000 DLL) |
Stop-after-loss / one-trade-a-day: no change. **The NY book is too slow, not too risky.** Pass odds depend
heavily on regime (starts in 2026: ~74% within a year; 2023-24: 2-11%).

**⚠️ LONDON'S LIVE RECORD IS NOT TRUSTWORTHY EITHER WAY.** All 113 stop exits since Aug were booked at exactly
the stop level; entries booked at the intended price. IBKR's own fills for Sep 29: each 2-lot market entry
filled at two prices ~30pts apart on BOTH accounts (30,636.50 + 30,667.00), exits a few pts off the stop —
booked +$75/account, fills −$57. The 30pt second lot is almost certainly the paper simulator (identical on
both accounts, same second), so neither number is the real one. Bridge logs keep no historical fills.
Next step: London should record /executions fills alongside the booked price (NY got this Sep 24).

### Sep 29-30 2026 — P&L sweep (commit 4afadc5) + TC sizing / time-to-pass

**Fixed (all live, both traders restarted flat 21:13 ET):** (1) prop state now rebuilt from the ledger
(`prop_rules.reconcile_from_ledger`, NY + London, TC since `TC_COMBINE_START`) — TC file had been reset to
ALL-TIME NY P&L on every restart and had London erased nightly; IBKR's was missing +$2,462.06 of London.
(2) Daily-loss check + circuit breaker use `account_realized_today()` (NY + London; TopStep's DLL is
account-wide). (3) Real fills for NY partials and FUT CLOSE; London books /executions fills (never had
real-fill booking since it was built; stop-fill reader used the broken /order status) with model prices
in `entry_signal`/`exit_signal`. Sep 29 London corrected +$75.04 → −$56.96 per account.
(4) **futures_bars_5m since Mar 2026 was mostly yfinance** (MNQ volume ≈ 1/4 of CME) because Databento
arrives a day later and INSERT OR IGNORE kept yfinance — inflating live RVOL (sizing tiers + scan gate).
2026 rebuilt from Databento 1-min; Databento now overwrites. Consistency 0.50 → 0.55 (TopStep page).
**Tide-on live-parity book on corrected bars (`_tideab3_on.csv`): 2021-25 unchanged, 2026 +$6,455 →
+$4,233 raw** — yfinance volumes had flattered 2026.

**TC sizing ($6/c, same 579 trades, win rate 59.4% at every size):**
| size | net | worst trade | worst day | maxDD | 2024 |
|---|---|---|---|---|---|
| current (1-2c) | +$7,548 | −$814 | −$848 | −$2,755 | −$385 |
| 2c every long | +$11,402 | −$814 | −$1,007 | −$6,101 | −$1,712 |

**⚠️ The combine's real bottleneck is our own $300 MLL buffer, not blow-ups:** most non-passing combines
are FROZEN (balance within $300 of the floor → no trade allowed → can never recover or fail). At 2c in
the recent regime 81/81 non-passes were frozen. Freeze ⇒ reset. With reset-on-freeze ($85/mo, 1 reset
credit/mo): 2c longs — all history 64% pass within 12mo (median 7.9mo, ~1 reset); combines started
Oct 2025–Mar 2026: 61% within 3mo, 100% within 6mo, median 2.6mo, cost ~$292. Current sizing: 16%/12mo
all history; recent 63%/6mo, median 4.9mo. Size-aware step-down instead of the $300 buffer: no better.

### Sep 29 2026 (late) — TC 2c longs LIVE · TopStep rule audit · TC London fixes · pass estimate incl. London

**LIVE (user-approved): `TC_LONG_CONTRACTS = 2`** in tc_trader.py — every TC long trades 2 (prop cap 2),
shorts stay 1. Plus: `prop_rules.dll_contracts()` sizes TC NY and TC London entries so a full stop-out
cannot carry TODAY's account P&L (NY + London) past TopStep's $1,000 DLL (e.g. London −$300 then a 2c
NY stop −$812 = −$1,112 was possible). London on TC now uses the $700 soft DLL (was IBKR's $1,250) and
account-wide daily P&L. **London restart recovery**: today's OPEN row is rebuilt into `_position`
(monitoring resumes; a stop filled while down is booked from /executions), older OPEN rows → ORPHANED +
Telegram, and the day's trade count is restored (a restart used to reset it to 0). **NY startup no longer
cancels TODAY's backup stops** (it cancelled every open trade's broker stop on any restart). Dashboard
floor now `max(hwm−2000, 48000)` like prop_rules.

**TopStep rule audit:** target $3k ✅ · consistency 55% ✅ (was 50%) · MLL $2k EOD-trailing ✅ (ours never
stops trailing; TopStep reportedly locks at the $50k start — verify; ours is only stricter above $52k) ·
DLL now account-wide + risk-aware ✅ · 2 of 50 micros ✅ · NY hard close 4:00pm ET = 3:00pm CT, London by
9am ✅ · `commission_rt_tc` still = IBKR's 1.24 placeholder ⚠️.

**London 5.5yr sim (live config, 2c, `_london_2c_slip*.csv`):** no slippage +$2,069 (2023 −$1,484,
2024 −$1,910, 2025 +$3,018, 2026 +$1,957); **0.5pt/side slippage −$8,148** — most wins are BE scratches.
**Combine incl. London (NY 2c longs, reset on blow/freeze, fees ×1.13 tax):**
| book | starts Oct25–Mar26 | all history |
|---|---|---|
| NY only | 61% ≤3mo, median 2.6mo, ~$330 | 64% ≤12mo, median 7.9mo |
| NY + London (0 slip) | 69% ≤3mo, median 2.3mo, ~$288 | 54% ≤12mo |
| NY + London (0.5pt) | 69% ≤3mo, median 2.4mo | 49% ≤12mo, 1.84 resets |
London helps in the current regime, hurts across history. **Measure its real slippage** (new
`entry_signal`/`exit_signal` columns vs fills) for ~2 weeks; if ≳0.5pt/side, turn London off on TC.
⚠️ **2026 sim by month (NY 2c longs): Feb–Jun strong (+$9.2k), Jul–Sep flat (+$165).** Live TC since
Sep 3 under the new rules ≈ +$513 (NY longs −$513, London +$1,027 on old booking). "Pass in ~3 months"
applies to starts into a strong stretch, not necessarily today.

### Sep 29 2026 (night) — $100K plan: wide stop / chop-sizing / NQ all tested and rejected

**Accounts (TopStep pages):** 50K $3k/$2k MLL/$1k DLL $85 · 100K $6k/$3k/$2k $129 · 150K $9k/$4.5k/$3k
$199 (+~13% tax). Target÷MLL: 1.5 / 2.0 / 2.0. At max DLL-fitting size (2/4/6 MNQ on a 200pt stop) all
three pass at the same pace (recent starts median 2.6mo); all-history 63% / 70% / 70% within 12mo; fees to
pass ~$330 / ~$490 / ~$760. 100K = cheapest per contract once funded ($36 vs $48). Runner: scratchpad
`accounts.py` logic; see topstep_gauntlet.py.

**Wide stop at EQUAL dollar risk on the 100K (`_stopab_{200,500,1000}.csv`, same script, one variable):**
| config | per contract 5.5yr | net | pass ≤12mo |
|---|---|---|---|
| 4 MNQ @ 200pt | +$6,770 | +$22,804 | 70% |
| 2 MNQ @ 500pt | +$6,192 | +$9,700 | 0% |
| 1 MNQ @ 1000pt | +$3,261 | +$3,261 | 0% |
Wider stops earn LESS per contract and force fewer contracts — the 11th confirmation that more room per
trade does not pay on this book. **Chop:** with the Tide on, CHOPPY days are +$1,876/contract; labels drift
with volatility (TRENDING 5% of 2021 days, 61% of 2026); within-year IB terciles earn the same per trade —
no stable "play big" day. **NQ:** 1 NQ @200pt = $4,000 > the 100K's $2k DLL and $3k MLL; TopStep would
liquidate at ~100 NQ pts. Not viable. **Fixed tonight:** daily_tide crashed and failed OPEN when our 5m bars
had a session the daily table lacked (`cc8b94c`) — caught by this A/B.

### Sep 29 2026 (night) — TC switched to the $100K plan (user-approved)

`prop_rules.TC_ACCOUNT_SIZE = '100K'` (env-overridable; plans for 50K/100K/150K in `TC_PLANS`). Every TC
limit derives from it: start $100,000 · target $6,000 · MLL $3,000 (floor ≥ $97,000) · DLL $2,000 · our
soft DLL $1,400 (70%) · MLL buffer $450 (15%) · day cap $2,400 (40% of target) · **4 MNQ per long**
(`tc_trader.TC_LONG_CONTRACTS = TC_TRADING_MAX_CONTRACTS`), shorts stay 1, 200pt stop unchanged. The
fractions reproduce the old $50K constants exactly. Paper combine restarted: `TC_COMBINE_START =
'2026-09-30 00:00'` (old $50K state backed up as prop_state.json.bak-20260929-pre100k). The state file
now publishes floor/buffer/plan; the dashboard reads them. **London on TC unchanged** (≤$250 risk, 2c) —
its limits follow the plan (daily stop $1,400); don't scale it until its real slippage is measured.

**Last 5 days (Sep 23-29) under the new rules:** actual TC −$982. Tide removes all 5 shorts (−$961 of it);
Sep 25 long at 4c +$137; Sep 24 long at 4c ≈ −$730 (the old $700 breaker no longer fires — price came
within 3pts of the −$1,626 stop, then recovered); London +$211 ⇒ ≈ −$380. Sim for the same days: one
trade (Sep 25, ≈ +$170 at 4c) — it no longer takes the Sep 24 long.

### Sep 29 2026 (late night) — dashboard "to date" totals + sign bug + Telegram accuracy

**Dashboard:** each summary card now has a **To date** row (realized, closed trades, same definitions as
Today: equity = all four books' LIVE rows; futures = NY + London per account; RECONCILED excluded) and
the top bar a **TO DATE** own-money total (equity + options + IBKR futures; TC excluded — prop eval, shows
its combine progress/room instead). `get_totals()` in app.py. At ship: equity −$2,250.37 since Apr 15
(Day Trader −1,465.80 · Wave −251.52 · Contrarian +390.58 · Clockwork −923.63 at paper auction fills),
options −$5,333.08, IBKR futures +$5,429.48 (NY +3,099 incl. manual FUT CLOSE · London +2,330), TC
combine $0 of $6,000, own total −$2,154. **Bugs fixed:** (1) headline and top-bar numbers printed
Math.abs() with '+' only for gains — losses showed as a bare red "$280.56"; now signed everywhere (money()
also gained thousands separators). (2) options P&L used exit_value − premium_paid for every trade in 5
places; credit spreads are reversed — now one `OPT_PNL_SQL` matching database.get_options_total_pnl (no
credit spread had closed, so nothing was wrong yet). Verified by rendering the cards in JavaScriptCore.

**Telegram:** London entry/exit and NY partial messages now show the booked FILLS (were signal/estimate
prices); partial shows booked net $. Both FUTURES EOD messages show Day P&L = NY + London with the split
(were NY only while the account total included London).

### Sep 30 2026 — first day on the new config · paper split-fill artifact found and neutralised · London verdict

**Worked as planned:** TC's first 4-MNQ long (PM_LONG 10:32 → no-move 12:02, +44.5pts) and IBKR's same trade
at 2c, identical fills on both accounts; Tide verdict logged LONG-only (no shorts signalled); DLL-aware sizing
did not need to trim; ledger-rebuilt state and NY+London EOD messages correct. TC $100K combine: **+$303.08**
(NY +$351.04 · London −$47.96).

**🐛 IBKR PAPER SPLIT-FILL ARTIFACT.** Part of some multi-contract MARKET orders fills ~30pts worse than the rest
at a price that never traded (yfinance 1m confirms): London exit 04:03 lots 30693.25 + 30662.75 (market
30686.00–30707.50); NY exit 12:02 lots 30881.25 + 30850.50 on BOTH accounts (market 30869.25–30888.75); Sep 29
London entries 30636.50 + 30667.00. Always adverse, never on 1 lot. Real-fill booking (Sep 24/29) was recording
it. **New `futures/fills.py::clean_vwap`**: lots of one order that disagree by >10pts → book all contracts at
the lots agreeing with the one nearest the expected price, log what was set aside (never triggers on a real
account). Wired into NY entry + exit helpers (both traders) and London `_exec_fill`. Rows corrected: NY #235
+$228.04→+$351.04, #236 +$114.02→+$175.52; London #161 −$85.48→−$24.48; Sep 29 #157/158 → +$60.52,
#159/160 → +$4.52 (backup trades.db.bak-20260930-splitfill). ⚠️ Today's Telegram EOD was sent with the old
(artifact) figures.

**London — no configuration survives real costs** (5.5yr 1m sim, 2c, commission): BE 0.10 (live) +$2,075 at
0 slippage = $0.81/trade; **0.5pt/side −$8,149, 1pt −$18,373**; BE 0.25/0.5/1.0/none all worse; London + Tide
worse at 0 slip (+$1,619) and negative with any slippage. Measured real stop-exit slippage (4 exits, artifact
removed): 3.75 / −1.75 / 5.5 / 5.25 pts ≈ 3pts avg — the exits are software-detected on a 15s monitor then sent
as market orders. Even perfect execution (0.5pt) loses. **Recommendation: switch London off on TC**
(`LONDON_ENABLED=False` in tc_trader.py); keep IBKR London running to keep measuring real fills. Pending user.

**Unverified fills (Sep 30 2026):** the 15-day scorecard now marks, per futures book, how many trades were
recorded at the INTENDED price instead of the broker's fill (London before Sep 29, NY exits before Sep 25 except
the hand-corrected #225) — "≈ 24/28 est." with a tooltip. At ship: TC London 24/28, IBKR London 24/28, TC NY
13/17, IBKR NY 5/10. Their real fills are not in our system (IBKR API serves only today's executions; bridge logs
keep only cancels). **To correct them: export the IBKR Activity Statement / Trade Confirmation Flex Query for
DUQ640500 (TC) and DU9952463 (IBKR) from Sep 10, then match fills to rows.** Do not adjust them by estimate.
`futures/ibkr_state.json` untracked (runtime state, like prop_state.json).

---

## Sep 30 2026 — Equity day trader review: the loss is a left tail with three sources (decisions pending)

Started from the day's two losers (XRPN, IONQ — both `CATALYST_OVERRIDE`, both −5% hard stops).
Labs: `research_daytrader_entry_lab.py` (481 live trades × point-in-time bar features) and
`research_daytrader_exhaustion_2y.py` (9,449 candidate-days, Sep 2024–Sep 2026, universe names).
Outputs in `research_out/`. **Nothing that changes a trading decision is live; three flags wait on the user.**

**① OPS BUG, FIXED + LIVE — file-descriptor leak.** yfinance 1.3's tz/cookie caches are peewee SQLite DBs
with thread-local connections; every `yf.download(threads=True)` thread opened handles that were never closed.
auto_trader (up since Sep 22) held 179 cache handles at a 256 launchd soft limit → errors 11 → 471/day, Sep 30:
75 scan errors, 3 exit errors, "Bridge unreachable [Errno 24]". bridge.py had 58 after 5 days. `yf_cache_fix.py`
swaps the caches for in-memory dicts (tz map seeded from disk, no extra Yahoo calls). Repro: 40→82 handles in 4
downloads without it, 0 with it, identical data. Installed in auto_trader, bridge, options_trader, news_engine.

**② DATA BUG, FLAG OFF — frozen daily bars.** `bridge.py` caches '1 day' bars 24h, so df1d's last row (today's
partial) froze at the symbol's first fetch; prev_chg (the ≥3% gate, the +10/20/30 bonus, the strong-momo pattern
bypass, rs_vs_spy), daily RSI, MA20 and EMAs read it all day. Proof: IONQ Sep 29 logged "Only +1.1% today" at
11:23 and 12:55 while truly −1.1%/−1.3%. 12,081 Jul–Sep SKIP rows: off by >1pt 39%, 1,167 true ≥3% movers
rejected. **equity_replay.daily_upto() always built today's row live — every recent equity A/B measured a system
that live was not running.** Fix written + tested (also repairs yesterday's row when the cache still serves
yesterday's partial at the open, detected by volume) behind `DAILY_ROW_FROM_LIVE_BARS=False`. Caveat: the freeze
has acted as an accidental early-mover filter — 2y: first-qualifying ≤09:40 +0.18–0.22%/trade vs ~+0.02% later;
unfreezing admits later movers (≈breakeven).

**③ THE LOSS IS A LEFT TAIL.** Since Jun 1: 27 hard-stop exits realized **−6.21%** avg (5% stop + slippage) =
**−$2,297, more than the whole book's −$2,319**; every other exit type nets ≈0. Sources:
- **Scanner-discovered names** (not in FULL_UNIVERSE): 58 trades **−$1,506, −1.67%/trade, 26–43% hard stops**
  (universe: 201 trades −0.32%, 3–8%), negative Jun/Jul/Aug/Sep, via BOTH paths (main CATALYST 23t −$868,
  override 35t −$638). **+$965 in May** — a lottery cohort. XRPN = de-SPAC at $10.55 trust value for months;
  its RSI 99.5 / 51× vol / ATR were computed on that history. It closed $16.39, +8% above our stopped entry.
- **`_scan_catalyst_override`**: since REGIME_AS_MODIFIER it runs every cycle BEFORE the main scan, buys
  first-come (no batting order — 46% of entries since Sep 6 bypassed it), no Layer 2, no Layer 3, hardcoded
  'NORMAL' regime (STRONG exhaustion gate can't fire), no scanner-pick rules, no sector cap, no scan_log row.
  107 trades −$842 (−0.70%/trade). Universe override trades re-run through L2+L3 on bars: 73% L2-skipped,
  −0.20%→−0.05%/trade. ⚠️ Its ENTRY RULE is fine on universe names (2y: ≥5% from open + rvol≥3 = +0.45%/trade
  vs +0.07% other candidates, but only 3/5 halves, 32% stop hits) — the problem is what it buys and how it holds.
  Retiring it routes these candidates through the main scan as catalysts at FULL size (override used half).
- **Fixed 5% stop vs volatility**: high-ATR names hit it 31% vs 3%. 2y: ATR-scaled stop (1.0×) with equal-risk
  sizing = +36% R/trade vs fixed 5% (0.023R vs 0.017R), 6/8 quarters; high-ATR tercile ≈0 edge at any width.

**④ YOUR "EXHAUSTED/FADED" HYPOTHESIS, TESTED (2y, 9,449 candidates).** Intraday exhaustion — fade from HOD,
retrace of the run, 30-min run, 5m RSI, up-volume, VWAP extension — does NOT predict worse outcomes (flat or
slightly positive; within-day |ρ|<0.06). Only **multi-day** extension holds: prior-5-day return top decile
(>18.6%) is the only negative decile (−0.14%/trade, 29% stop hits), Q5<Q1 in 7/8 quarters; fresh moves (5d ≤0)
best. IONQ Sep 30 *was* a top-of-thrust entry (two volume bars 45.1→47.14, bought 47.15, closed 43.87) — a
coin-flip pattern on average; L3 would have exited ~−2%. Thesis Check (LLM) flags BREAKING on 81% of losers;
acting on it = +0.15%/trade — weak.

**⑤ "Best batsman" scoring:** score_components = LOGGING only (since Sep 18); EXTENSION_TILT=0 (A/B rejected);
batting order live but bypassed by the override path.

**DECISIONS (user, Sep 30 night):** `SCANNER_PICKS_TRADE=False` and `DAILY_ROW_FROM_LIVE_BARS=True` — LIVE
(autotrader restarted 22:16). `CATALYST_OVERRIDE_ENABLED` still undecided (True). Estimate Jul 22–Sep 30 with
override off + scanner names off: −$481 vs −$1,382 actual (universe book alone still −$429 — stops the bleed,
not an edge). Freshness / volatility / thrust: user agreed, but ONLY after a backtest — measured live from
Sep 30 (scan_log `ret_5d`/`atr_pct`/`is_thrust`/`today_gain`) and A/B'd via equity_replay.
2-year bar-lab, per half-year: freshness gate (>18%) improves 4/5 halves; ATR stop better per unit risk 5/5;
thrust better only 3/5 (an earlier "4/5" was a miscount).

**⚠️⚠️ DATA FINDING (Sep 30 night) — bars_5m VOLUME IS ON TWO SCALES. Read before ANY backtest that uses volume.**
The DataBento backfill rows (ts_utc '...T13:30:00', all of 2024 → ~Jul 2026) carry only **~2–5% of
consolidated volume** (IONQ 2024-03-05: 717,948 vs Yahoo 13,959,100; NVDA 2.6%; AMD 3.8%). Prices are fine.
The collector rows ('... 13:30:00+00:00', from ~Mar 2026; sole source from Aug 4 2026) are consolidated
(~80–95%). In the Mar–Aug 4 overlap BOTH rows exist and `load_bars` keeps the backfill one (keep='last').
Consequences: (1) **equity_replay before Aug 4 2026 is volume-starved** — vol_ratio = backfill 5-min volume ÷
Yahoo consolidated daily average ≈ 0.03× true, so the ≥1.3 gate blocks almost everything; every replay A/B
spanning Jun–Jul was effectively an August test (cf. the Sep 6 regime A/B: "+$1,345 of +$1,428 is August").
(2) Research rvol across the Aug 4 switch is distorted. (3) Live books use bars_5m for prices / same-day
VWAP only — unaffected. Fixed tonight in `tod_relative_volume` (upper date bound — it also read FUTURE days
in a replay — and one row per bar, preferring the collector's). Real fix (NOT done): rescale or replace the
backfill volume, e.g. per-symbol factors calibrated on the Mar–Aug overlap, then re-run any volume-based study.
Until then: **replay only from Aug 4 2026 onward.** Also found: equity_replay never stubbed the Thesis Check, so
replays since Aug 8 made real Claude vision calls (stubbed now, at `_claude_analyse_image` itself).

**WHY EARLIER TESTS DIDN'T STICK (read before testing any new entry idea — avoid circling):** (1) the replay
computed "today gain" live while production used a frozen value; (2) the replay failed Layer 2 open; (3) the
replay never saw scanner names and under-represented the override — so A/B verdicts were about a different
system, and the two largest leaks were invisible to the bench. (4) Reviews sliced losses by setup/month, never
by universe membership — that one split is 65% of the Jun–Sep loss. (5) scan_log stored the grader's OUTPUTS,
not its inputs, so a stale input could not be seen; `today_gain` is now logged for that reason. Rule: slice by
cohort first, and confirm replay INPUTS match live before trusting any replay verdict.

**BACKTEST RESULT (Sep 30 night) — full-pipeline replay, Aug 4 → Sep 29 2026 (37 trading days, consistent
volume data), live config incl. tonight's fixes, Layer 2 active, LLM stubbed. `research_replay_ab.sh sep30 …` / `research_replay_score.py sep30 base`.**

| arm | trades | P&L | WR | hard stops | maxDD | vs base (matched days) |
|---|---|---|---|---|---|---|
| base (live today) | 228 | −$255 | 39.5% | 12 (−$608) | −$503 | — |
| freshness >18% | 206 | −$450 | 37.4% | 9 | −$648 | −$195, t=−0.91, worse both months |
| **volatility stop** | 223 | **−$94** | 39.9% | 11 | −$485 | **+$161, t=+1.14, better 57% of days, both months; +$34 without its best 2 days** |
| thrust + override off | 243 | −$129 | 29.6% | 6 (−$407) | −$552 | +$126, t=+0.26 — ALL from Aug 6 (+$289); −$297 without best 2 days |
| all three | 224 | −$321 | 28.6% | 5 | −$481 | −$67, t=−0.20 |

Verdict: **volatility stop is the only one positive in BOTH tests** (2y bar lab 5/5 half-years + replay both
months) — small, not significant, risk-neutral by design. **Freshness REJECTED** (fails the replay despite
2y 4/5). **Thrust inconclusive** (one-day result; halves hard stops but more small losses). Combining hurts.
None makes the book profitable (best arm −$94 over 8 weeks). All three stay OFF pending the user; their
inputs keep logging to scan_log. ⇒ Stop tuning day-trader ENTRIES; remaining value is the bench/data fixes.

**OCT 1 2026 — DECISIONS + LIVE CHANGES (user):** override OFF (a removal of a duplicate path, not a gate)
and the volatility stop ON as a live trial — "if it is not harming us, continue, else cut it later".
Restarted 09:46 ET while the book was flat of in-flight orders. ⚠️ Restart note (my error, recorded so it
is not repeated): the old process had opened IT #880 and EPAM #881 at 09:45:01/04, my pre-restart check only
looked at VICR, and the restart dropped their in-memory Layer 3 (T+5) checks. Re-applied by hand at 09:50
with the system's own rule (both FLAT → stop to signal price) → both exited on the fast stop (−$17.53,
−$48.77), as the live check would have done. **Rule: gate any mid-session restart on `status='OPEN'` = 0
and no entry in the last 5 minutes** (Layer 3 pending checks live only in memory).
Replay tooling now committed: `research_replay_ab.sh` (presets `sep30` = reproduces the Sep 30 A/B,
`voltrial` = the trial review) + `research_replay_score.py`. equity_replay switches are tri-state
(omitted = mirror live code; `--vol-risk/--no-vol-risk`, `--override/--no-override`, `--thrust-priority`,
`--fresh-max-5d N`).

**OCT 1 2026 — LIVE WATCH (user: "live system gives more insights than history").** Day: 8 trades,
−$110.59 — VICR/IT/EPAM (old code) and AXTI/AMAT/CACI scratched by Layer 3, COHR +$21.44 (PCT trail),
MRNA −$23.34 (regime-flip exit). Verified live: grader's `today_gain` matched the true move for every
entry (≤0.06pt); override silent; volatility stop applied (COHR 6.6%, MRNA 6.6%, ~$90-105 risk); Layer 2
rejected ~30 candidates, which then moved **−0.02% on average** (no money left on the table, none saved).
**Fixed + deployed 13:03 (commit c587981, user-approved):** (1) stops and Layer 3 now anchored to the real
fill, not the 30-60s-old scan price (fills averaged +0.16% above it since Aug, +0.41% today; EPAM's
"break-even" was −1.1%); (2) slot counter counted every entry twice → a scan could fill only 3 of 5 slots.
**Open questions the watch surfaced (not changed — need a test first):**
- Layer 2's "HOD×N resistance" counts bars within 0.5% of the window high, so it rejects a grinder making
  new highs (FN, +7.5% from open, rejected all morning) the same as a stall (CRDO). It is really a "don't buy
  at the high" rule — and the batting order ranks names AT their high FIRST. The two work against each other.
- On CHOPPY/CAUTIOUS scans grade_setup still hard-skips every non-catalyst (CTSH +10.5% on 10.8× vol was
  skipped), so REGIME_AS_MODIFIER is only a partial modifier.
- The day trader buys names other books already hold (AXTI held 24, COHR 6 by another book) — no
  cross-book concentration limit.
