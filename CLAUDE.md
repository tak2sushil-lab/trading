# TriVega Trading System — Ground Truth
**Auto-loaded by Claude Code at session start. Update this file whenever code changes.**
Last updated: Aug 7 2026

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
"what's shipped, what's running, what's still open." Last refreshed: Aug 8 2026.

**⚠️ NEXT SESSION PRIORITY (rewritten Aug 16 2026 — MAJOR PIVOT; read the `alpha-factory` memory first):**
The equity system pivoted to an **Alpha Factory** — an offline engine-discovery/validation system
(`factory/`, branch `alpha-factory`; live services run from this working tree). Full design +
architecture diagram: `docs/ALPHA_FACTORY_DESIGN.md`; dashboard `/factory` page.
**Fish Finder (`FISHFINDER_*`/`_scan_regime_adaptive`) + the equity bear book (`BEAR_MOMENTUM`/
`_scan_and_enter_bear`) are DECOMMISSIONED** (`FISH_FINDER_ENABLED=False` in auto_trader.py; both
failed every factory market-neutral test, Fish Finder bled ~$780/mo; code kept, revertible). The
dated Aug 6-8 Fish Finder sections below are now HISTORICAL. Two uncorrelated Roster engines validated
on 2.5yr: **Wave Rider** (momentum·wild, 3-day swing — now LIVE-PAPER in SHADOW via
`com.sushil.trading.wave_rider`, `wave_trades`/`wave_scan_log` tables) + **Contrarian** (market-neutral
cross-sectional reversal, a sleeve). Rejected by the gate: Bargain Hunter, low-vol, PEAD, XS-momentum,
the tide-throttle. Live equity right now = old LONG/SHORT momentum + catalyst books (Book Health ON),
with Wave Rider shadowing as the intended replacement.
**⏸ PARKED Sun Aug 16 — resume Mon Aug 17:** (1) run the FREE 2022 bear stress-test (yfinance DAILY,
no Databento) — do our edges survive a bear? more data does NOT rescue failed engines, it stress-tests
the 2 passers. (2) watch Wave Rider's first live shadow scan (`logs/wave_rider.log` + /factory funnel).
(3) decide: merge `alpha-factory`→main or keep on branch. Still open (pre-pivot): `BUY <SYM>` Telegram
bug (auto_trader.py:2729) opens a new LONG regardless of existing position — not fixed.

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
