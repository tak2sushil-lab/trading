# TriVega Trading System — Ground Truth
**Auto-loaded by Claude Code at session start. Update this file whenever code changes.**
Last updated: Jul 21 2026 (night)

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
