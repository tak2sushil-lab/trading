# The Alpha Factory — Design & Reference
**Built Aug 15 2026 on branch `alpha-factory`. Offline research + validation system. Places NO live orders.**
Run: `venv/bin/python -m factory.run all`  ·  Tests: `venv/bin/python -m factory.tests.test_factory`

---

## 0. Why this exists (the mission, in one breath)
A single strategy is a boat; it rots (edges decay). A *business* is a **factory** that keeps
building boats, tests each honestly, sails the ones that pass, and scraps the ones that start
leaking. We don't bet the business on a strategy — we bet it on the *process*. This repo is that
process, in code, judged on **smoothness (Sharpe), not peak return.**

## 1. Vocabulary (plain-English analogies — the whole system in 8 words)
| Term | Analogy | What it really is |
|---|---|---|
| **Tide** | the ocean tide that lifts every boat | *beta* — the market's drift; return you get for just being invested |
| **Sailing** | how well you sail vs other boats on the same tide | *alpha* — skill above the tide; the only thing that survives a bear |
| **Personality** | a boat's temperament (Calm / Mid / Wild) | a stock's volatility class (daily range terciles) |
| **Engine** | a boat / a fishing method | one strategy that emits trade tickets |
| **The Proving Ground** | sea-trials before a boat joins the fleet | the QC gate: 7 honest checks a candidate must pass |
| **The Roster** | the fleet that made the cut | the library of *validated* engines |
| **The Captain** | decides which boats sail and how much sail each carries | portfolio construction (weights + risk) |
| **The Fill Desk** | the harbour where voyages are actually run & costed | execution: discrete-slot portfolio sim → equity curve |
| **The Tide Gauge (throttle)** | reef the sails when the tide goes out | slow bear-market exposure cut *(currently OFF — see §6)* |

## 2. The wiring
```
 ① HUNTING GROUNDS            ② R&D BENCH          ③ THE PROVING GROUND (qc_dyno.py)
   anomaly catalog,            build a prototype     7 checks on the SAILING (alpha), never
   event triggers,     ─idea─▶ engine on 2.5yr  ─▶   the raw return:
   cross-sectional,           of tape                  OOS alpha · significance · walk-forward
   structure, LLM copilot     (MANY engines)           · cost survival · robustness ·
        ▲                                               uncorrelated · direction
        │ new hypotheses                          PASS ✓│        │✗ FAIL → back to bench
        │                          ┌───────────────────▼──┐
        │                          │ ④ THE ROSTER          │  validated engines only —
        │                          │   (alpha library)     │  Wave Rider (slot), Contrarian
        │                          └───────────┬───────────┘  (sleeve), + future engines…
        │                          ┌───────────▼───────────┐
        │                          │ ⑤ THE CAPTAIN         │  weights by edge/risk,
        │                          │   (risk_brain.py)     │  tide-throttle, drawdown budget
        │                          └───────────┬───────────┘
        │                          ┌───────────▼───────────┐   HOW a proven voyage is run:
        │                          │ ⑥ THE FILL DESK       │──▶ (a) SHARES  → equity curve
        │                          │   (execution.py)      │       (execution.py / wave_rider)
        │                          └────┬──────────────────┘──▶ (b) TURBO ⚡ → OPTIONS
        │                               │                          leverage/structure on the
        │                               │                          SAME proven signal — only if
        │        ┌──────────────────────┘                          the move pays the carry
        │        │                  ┌────────────────────────┐     (factory/live/turbo.py →
        │        │ decay alarm      │ ⑦ THE LOOKOUT (live)   │      options_shadow · Edge-Budget
        └────────◀──────────────────│  live vs backtest;      │      gate · IV routing)
                                    │  retire decayed engines │
                                    └────────────────────────┘
```
**Options is NOT a separate strategy — it is execution path (b) at the Fill Desk.** The
Roster/Proving-Ground/Captain machinery is shared; Turbo just takes a *validated* engine's
signal and expresses it as a leveraged, defined-risk options structure instead of shares.
It never picks direction and never runs on an unvalidated engine. Full design:
`docs/OPTIONS_FACTORY_BRIDGE_DESIGN_2026-08-16.md`.
Data spine: `data.py` builds **Personality**, the **Tide**, and the unified **event table** (every
≥3% morning mover with 15-day forward price paths) once, cached to `factory/cache/`.

## 3. Components (files)
| File | Role | Key idea |
|---|---|---|
| `contracts.py` | the framework | `Engine` base + `Trade` columns. A new engine implements ONE method (`select`); the base computes the outcome + market-neutral alpha identically for all, so no engine can flatter itself. |
| `data.py` | data spine | one pass over 2.5yr → personality.csv, tide.csv, events.csv (25,494 events, 293 stocks). |
| `engines.py` | the boats | registry. Add a class → it flows through the whole pipeline. |
| `qc_dyno.py` | The Proving Ground | `evaluate(engine) → Scorecard` (PASS/FAIL + reasons). |
| `risk_brain.py` | The Captain | `weights()` (info-ratio) + `throttle()` (slow tide gauge). |
| `execution.py` | The Fill Desk | `run_fleet()` → realistic slot-based equity curve. |
| `run.py` | CLI | `build-data` / `validate` / `fleet` / `all`. |

## 4. The Proving Ground — the 7 checks (why engines die)
1. **OOS alpha** — market-neutral alpha positive in *both* sealed windows (H2-2025, 2026), not just in-sample.
2. **Significance** — enough OOS trades + t-stat ≥ 1.5 (not luck).
3. **Walk-forward** — positive across ≥55% of rolling 6-month windows (stable, not one lucky split).
4. **Cost survival** — still positive after round-trip friction.
5. **Robustness** — ≥80% of neighboring hold/stop configs positive (a *plateau*, not a spike).
6. **Uncorrelated** — |corr| < 0.5 to every engine already on the Roster (must add diversification).
7. **Direction** — in-sample alpha positive (the idea points the right way).

## 5. Current results (Aug 15 2026, 2.5yr, market-neutral)
| Engine | Verdict | Note |
|---|---|---|
| **Wave Rider** (momentum_wild) | **PASS ✅ Roster · LIVE-shadow** | slot-based momentum; OOS alpha +0.54/+0.49%, t=6.7, walk-forward **13/13**, robust 100%. Running live-paper in SHADOW (see §10). |
| **Contrarian** (xsec_reversal) | **PASS ✅ Roster** | market-neutral long-short *sleeve*; OOS +0.30/+0.36% (per-leg), t=3.9, robust 100%, **corr to Wave Rider −0.13** — the diversifier. Fully integrated. |
| **Clockwork** (xsec_overnight_consist) | **FAIL ❌ (cost) — directional, LIVE-shadow** | Night Shift v2 — long WILD names by 30d up-night *consistency*, hold overnight. Passes 6/7 (OOS2 +0.066, t=4.77, WF 13/13, uncorrelated) but **fails cost as a market-neutral sleeve**: an overnight book goes flat intraday → **full round-trip every night**, and its thin +0.057% alpha doesn't clear 0.10% RT. (The turnover-aware discount does NOT apply — see §5d bug-sweep.) It is NOT a market-neutral roster engine. It IS a **directional long-only overnight book** whose RAW net return (carries overnight beta) is positive at realistic costs; on live-shadow via `factory/live/overnight.py` to get real net-of-fills data. Skill (alpha) after full nightly cost is marginal — the forward test decides. (Aug 16 2026.) |
| **Bargain Hunter** (meanrev_mid) | **FAIL ❌** | uncorrelated & positive OOS, but **fails robustness** (67%) — held back |
| **Steady Hand** (xsec_lowvol) | **FAIL ❌** | engine-#3 candidate (betting-against-beta). Alpha **−1.1%**, robustness 0% — the low-vol anomaly is *inverted* in this high-beta bull tape. Gate rejected. |
| Cross-Sectional Momentum (bench) | **FAIL ❌** | best config looked great (OOS1 +2.4%) but **44% robustness** — an overfit spike |
| **Earnings Drift** (pead_gap) | **FAIL ❌** | PEAD via a **gap proxy** (earnings_calendar is empty, so a big overnight gap ≥5% stands in for an earnings surprise). Textbook overfit: strong in-sample (+1.3%) but **negative both OOS** (−0.96% / −0.54%), t=1.0. Notably walk-forward (85%) *and* robustness (100%) **passed** — the OOS-alpha + significance checks are what caught it. A good illustration of why the gate has 7 layers, not 1. A *true* earnings-surprise PEAD is untested (no earnings data). |
| **Whiplash** (xsec_st_reversal) | **DISCARDED ❌** | 1-day cross-sectional reversal (Jegadeesh 1990). Dead in this universe (IS −0.05%, t=−0.25, WF 38%) AND negative in every bear-test regime. **Unregistered** Aug 16 (class kept in `xsec.py` for reference). |
| **Smooth Sailing** (xsec_riskadj_mom) | **FAIL ❌ (direction)** | Engine-#4 hunt — risk-adjusted momentum (60d per-stock Sharpe), long smooth risers / short choppy fallers. Passes 6/7 with strong OOS (+1.49/+1.34%, t=6.19, uncorrelated, 13% turnover) but **negative in-sample** (−0.20% in 2024's junk rally) → gate refuses it (positive OOS could be regime luck). Bears mixed: +COVID/+2022 but −2018Q4/−2023. The defensive slot stays UNFILLED — every defensive/quality attempt (low-vol, risk-adj mom) is regime-dependent; the market-neutral gate strips the varying-net-exposure that IS defense. Next-session problem. (Aug 16 2026.) |
| **Night Shift** (xsec_overnight) | **FAIL ❌ — bench WATCH** | Overnight-drift persistence (Lou-Polk-Skouras "A Tug of War"). Passes 6 of 7 checks (t=2.53, WF 13/13, robust 100%, cost-survive, uncorrelated); fails **only** OOS alpha (2026 decayed to ≈0). **Structural refinement found — LONG × WILD clears the bar (OOS2 +0.092%, t=2.80) and is uncorrelated (+0.009 vs Wave Rider)** → see §5b. Top bench-watch candidate; register+full-gate before promotion. (Added Aug 16 2026.) |

**The Roster now holds TWO uncorrelated engines** (Wave Rider + Contrarian, corr −0.13) — a directional momentum boat and a market-neutral long-short boat. Slot fleet (Wave Rider), skill-only: **+232%/2.6y, Sharpe 1.8, MaxDD −14%** (inflated — see §7). Contrarian sleeve: +0.36%/leg, t=3.9, pays bull or bear.

**The Roster today has ONE engine.** Fleet (Wave Rider only), honest market-neutral basis:
**+134% / 2.6y, CAGR +38%, Sharpe 1.21, MaxDD −24%** (raw, incl. bull tide: Sharpe ~1.8-2.4).

## 5b. Bear stress-test (Aug 16 2026 — FREE, yfinance daily, no Databento spent)
`factory/bear_test.py` — the factory's 2.5yr backend is one regime (2024-26 high-beta bull); this
pulls daily OHLC (2017-2024, yfinance) and re-scores every DAILY engine on market-neutral alpha
across **three independent bears + a recovery**. Names that didn't exist then are absent
(survivorship worsens going back: 238 names in 2018 → 285 in 2022). Cache: `factory/cache/bear_*.csv`.
Run: `venv/bin/python -m factory.bear_test`.

| Engine | 2018 Q4 (−20%) | 2020 COVID (−34%) | 2022 bear (−19%) | 2023 recovery | Read |
|---|---|---|---|---|---|
| **Contrarian** (reversal, Roster) | **+0.46% t=3.1 ✅** | **+1.77% t=5.7 ✅** | **+0.25% t=2.9 ✅** | −0.17% ❌ | **Positive & significant in all 3 bears** (spectacular in the COVID whipsaw), loses only the smooth momentum recovery. A genuine crisis-alpha engine — its −0.13 corr to Wave Rider is *the regime hedge*. |
| **Steady Hand** (low-vol, gate-FAIL) | **+0.26% t=1.9 ✅** | **+0.54% t=1.9 ✅** | **+0.57% t=6.8 ✅** | −0.89% ❌ | Clean defensive signature: **positive in all 3 bears, negative in every rally** (incl. 2020's post-March ramp, −0.95%). The gate rejection was *bull-specific*, not "junk". Best **bear-hedge / engine-#3 candidate** — now n=3 bears, all positive. |
| **Night Shift** (overnight) | −0.01% flat | +0.31% t=3.8 ✅ | −0.02% flat | +0.13% t=4.4 ✅ | Small, mostly-positive, **never really negative** micro-edge. Not a hedge; a low-variance diversifier. See LONG×WILD refinement below. |
| **Wave Rider** *(daily proxy)* | **−0.81% t=−3.3 ❌** | +0.34% ⚠ | +0.04% flat | +0.29% t=2.2 ✅ | Long-momentum **can lose real alpha in a sharp momentum-crash bear** (2018 Q4), flat in 2022 — it *needs* a hedge. *Proxy caveat below.* |

**Findings that matter:**
1. **The engines have clean, complementary REGIME signatures** across 3 bears + 2 recoveries: Wave Rider (momentum) pays in rallies / hurts-or-flat in bears; Contrarian & low-vol pay in bears / lose in rallies. This is a real **all-weather set forming on the regime axis** — the diversification that's actually validating is regime, not time-horizon.
2. **Low-vol (Steady Hand) is the missing defensive leg — now n=3 bears, all positive.** Much stronger than the "n=1" caveat from the first pass. Still fails an *always-on* gate (it bleeds in bulls), so its deployment question is "how to hold a defensive factor without market-timing" (a fleet-blend / small-always-on tradeoff, not a regime switch). **Top engine-#3 candidate**, pending a fleet-blend Sharpe test.
3. **Databento verdict: hold the $59.** Free daily bears answered the regime question. Wave Rider's *daily proxy* is the only thing a 5-min pull would sharpen (2018 Q4 −0.81% may be proxy-pessimistic — entry at close, no intraday VWAP-hold filter). Spend only if we build a regime-paired fleet and need Wave Rider's *real* intraday bear drawdown for sizing. Treat proxy bear numbers as directional.

**Night Shift deployability (answering "is there a condition where it's usable"):** yes — a **structural**
cut, not a regime-timed one. Decomposed by leg×personality: the edge lives in **LONG × WILD** (buy the
highest-overnight-drift wild names) — clears the standalone gate bar (OOS1 +0.82%, OOS2 +0.092%, t=2.80),
holds in 4 of 5 out-of-regime windows (2022 mildly soft), and is **genuinely uncorrelated (+0.009 vs Wave
Rider, +0.20 vs Contrarian)** — a real diversifier, not a Wave Rider clone. Caveat: slice-derived (found
by cutting the same tape), OOS2 is thin, and a tradable version is long-biased overnight (net beta) unless
paired. Status: **top bench-WATCH candidate**, register + full-gate + live-track before any promotion.
(SHORT × CALM looked great in-sample, t=4.7, but failed out-of-regime — slice-fitting, dropped.)

## 5c. Day-horizon (intraday) probe — why there's no intraday engine (Aug 16 2026)
User observation (correct): live trades routinely peaked +0.5-1.5% intraday, then gave it back by the
close. Quantified on 4,974 WILD up-mover event-days from our 5-min bars (`scratchpad/day_probe.py`):
- **The give-back is real and huge:** mean intraday peak **+3.30%**, mean EOD close **+0.10%**. 76% hit
  +1%; of those, **87% closed >0.5% below their peak and 37% closed negative.** The opportunity is there.
- **But naive intraday profit-taking makes it WORSE, not better.** "Bank at +X% else hold to EOD" loses
  vs holding at every threshold (+0.10% EOD → **−0.05 to −0.08%** banking at +0.5/1/1.5/2%). It caps the
  right-tail runners (mean peak +3.3% *is* those runners) while keeping every loser. This is the **third
  independent confirmation** of the give-back law (futures Jul 25, equity Aug 8, now equity intraday).
- **Verdict:** the intraday horizon on our mover signal has **~zero edge** (+0.10% before costs = negative
  after) — it confirms the Aug 14 swing finding rather than opening a new engine. A standalone intraday
  day-engine is **not supported by the data.** The right response to the give-back pain is the **Aug 8
  stall-recycling exit** (bank the trades still flat at 30min — they lose 90% of the time), which is an
  *exit refinement to Wave Rider*, not a new sleeve. Horizon coverage below swing is provided by **overnight
  (Night Shift)**, not intraday.

## 5d. Fleet-blend test + Night Shift promotion (Aug 16 2026)
`scratchpad/fleet_blend.py` — each engine → a daily-alpha series stitched over the full **2018→2026**
(bear data early, factory data late; Wave Rider uses its daily proxy pre-2024), risk-parity-blended,
Sharpe measured with bears actually in the sample.

**Engine-#3 (Steady Hand / low-vol) — REJECTED for deployment.** This is the honest way to judge a
defensive factor without market-timing, and it fails clearly. Solo Sharpe **−2.26** (−1.28 even
bear-inclusive — the bear *months* don't offset the bleed across the non-crashing rest of 2018-23).
Adding it to the roster **lowers Sharpe at every weight**: roster +1.94 → +1.09 (10%) → +0.21
(equal-risk) → negative (2×). It only helps if you can *time* the crash — the regime prediction the
factory forbids and we've repeatedly shown is impossible. The gate was right; low-vol stays a
documented bear-conditional factor, **not** an engine.

**Night Shift — PROMOTED to active bench candidate (improves the roster).** Solo Sharpe **+1.52 full,
+1.92 bear-inclusive (best of all four engines)** — tiny magnitude but very steady + uncorrelated.
Adding it to {Wave Rider, Contrarian}: full Sharpe **+1.94 → +2.35**, bear-inclusive **+1.20 → +1.96**,
and **max-drawdown −48 → −15** — it smooths the whole fleet. (Risk-parity over-weights it to ~66% for
its low vol; a sane deployment weight is ~10%, which keeps Wave Rider's punch: Sharpe +2.28, and even
lifts the bull to +3.37.)

**User-inspired refinement VALIDATED (`scratchpad/gap_persistence.py`) — "pick the consistent
repeat-gappers, not the biggest single gap":** ranking WILD names by **20-day up-night *consistency*
(fraction of recent nights that gapped up)** beats the raw mean-overnight magnitude — OOS2 (2026)
**+0.157% vs +0.076%/night** (≈2×), and it holds **out-of-regime**: positive in 3 of 4 crises and it
**fixes the 2022 bear** (+0.074% t=2.5) where the magnitude signal was *negative* (−0.016%). This is the
concrete basis for a **Night Shift v2** (long WILD names by overnight consistency).

**Honest caveats before capital:** (1) magnitude is thin (0.07-0.16%/night in hard periods) — overnight
= daily turnover + open-auction spreads, so a realistic cost model must come before trusting it;
(2) long-biased overnight (carries overnight beta) unless paired with a short leg; (3) all figures are
gross per-leg alpha on backtest — **live-shadow is the real test**, same on-ramp Wave Rider took.
**Recommendation:** build Night Shift v2 as a registered engine (consistency factor) → full gate →
live-shadow. Do NOT add Steady Hand.

**Night Shift v2 BUILT + gated — "Clockwork" (`xsec_overnight_consist`, lookback 30d, plateau 20-40).**
Full 7-check gate: **passes 6 of 7** — revives 2026 (OOS alpha IS +0.054 / OOS1 +0.056 / **OOS2 +0.066**,
vs Night Shift's −0.001), t=**4.77**, walk-forward 13/13, robust 100%, uncorrelated (+0.23 vs Night Shift).
**Fails only cost survival** as a market-neutral long-short sleeve (+0.057% alpha < 0.10% round-trip).
**But that's a turnover artifact** — the gate assumes 100% nightly turnover; the 30d-consistency signal is
slow, so real turnover is **19.6%/night**. The **deployable form is LONG-ONLY** (top-decile WILD by
consistency, hold overnight): gross alpha **+0.29%/night (+0.23% OOS2)**, and turnover-aware **NET stays
+0.17%/night in 2026 even at a 0.30% round-trip** — survives realistic costs comfortably. Caveat: long-only
⇒ carries overnight beta (raw +0.46%/night incl. tide); it's a directional overnight-long book, not pure
market-neutral alpha. **Both forks SHIPPED Aug 16 2026** (cost model CORRECTED same day in the bug sweep — see below): (1)
**turnover-aware cost gate** (`qc_dyno._turnover`) — a *continuous-hold* sleeve (hold ≥ 2 days) pays the
spread only on the fraction of names that changes: Contrarian repriced (49% turnover, still PASS), Wave
Rider (slot) unchanged. **Bug sweep correction:** the discount does NOT apply to a 1-night overnight book
(Clockwork), which goes flat intraday and round-trips FULLY each night → full cost → Clockwork **fails the
market-neutral gate** (its original honest verdict). **Roster = 2** (Wave Rider + Contrarian). (2) **`factory/live/overnight.py` (Clockwork
live-paper trader)** — ranks WILD by fresh 30d up-night consistency near the close, buys top 10 equal-weight,
sells at the open; DB `overnight_trades`/`overnight_scan_log`, launchd `com.sushil.trading.clockwork`,
**SHADOW default**, run-lock, self-gates close/open windows. Dry-scan verified (sane picks: CC 73%, CLF/NWL/P
63%…). Dashboard `/factory` shows the overnight book. **Monday = shadow first** (neither Wave Rider nor
Clockwork has placed a live order and the gateway was down = untested path); flip each to LIVE via env var
(`WAVE_RIDER_MODE` / `CLOCKWORK_MODE`) after the Monday-EOD review is clean. **Contrarian still has no live
executor** (long-short sleeve + shorting) — pending build, not forced live.

## 6. Honest findings baked in (the factory working)
- **The lookahead bug we caught:** first fleet showed Sharpe 5.3 / +1674% — because slot selection sorted by *realized* return. Fixed to select on an entry-time feature; guarded by `test_no_lookahead` + `test_shuffle_invariance`. Result fell to a believable Sharpe ~1.2.
- **The tide-throttle FAILED validation** → defaulted OFF. On our (bull-only) data it cut ~20% CAGR *and* worsened drawdown — reefing sails during normal pullbacks missed recoveries. Its bear-protection value is **unproven** (we have no sustained bear in the data). Enable only with real reason.
- **Bargain Hunter is a real diversifier but too fragile to trade** — kept as a documented candidate, not on the Roster.

## 7. Known limitations (read before trusting a number)
1. **Survivorship bias** — the 293 symbols are today's universe, partly chosen *for* having moved. Inflates all historical returns. The market-neutral *alpha* is the most defensible figure; live-forward is the real test.
2. **Personality look-ahead (mild)** — volatility class uses full-history median range. A stock's class is stable, so the effect is small, but the correct fix is a *trailing-window* personality as-of each date. **TODO.**
3. **Idealised stop fills** — stops exit exactly at −8%; real gaps fill worse. Minor downward adjustment to expect live.
4. **Sparse-equity Sharpe** — P&L is booked at exit, so the daily series has many zero days; Sharpe is approximate. The alpha **t-stat (6.7)** is the more robust skill measure.
5. **Alpha includes risk-management** — engine return is stop-protected; the tide benchmark is not. The edge survives without the stop too (raw cluster study), so this is a feature, not the whole edge.

## 8. How to add an engine (the factory floor)
1. Write a class in `engines.py` with a `select(events)` method + an `EngineSpec` (give it an analogy nickname).
2. Register it in `REGISTRY`.
3. `venv/bin/python -m factory.run validate <name>` → read the scorecard.
4. If it PASSES → it auto-joins the fleet. If it FAILS → back to the bench. Nothing else changes.

## 9. Roadmap (phased, one validated brick at a time)
- **DONE:** Factory built + tested. **Two uncorrelated Roster engines** — Wave Rider (momentum,
  slot) + Contrarian (reversal, market-neutral sleeve). Wave Rider **live-paper in SHADOW** (§10).
  Live **Lookout** built. Engine-#3 candidate (low-vol) hunted → failed honestly.
- **Contrarian tradability caveats (unchanged):** it's a *breadth* strategy needing many small
  long+short positions + **shorting** (borrow frictions) → a scaled market-neutral **sleeve**, not a
  5-slot $10k engine. The immediately tradable path at $10k stays long-only **Wave Rider**.
- **Next builds:** blend the sleeve's curve into the slot fleet's combined equity; conviction sizing
  (lag-tolerant); an LLM-as-feature experiment (news → number → backtested); keep hunting engine #3.
- **RETIRED Aug 15 2026 (done):** Fish Finder (`FISHFINDER_*`) and the equity bear book
  (`BEAR_MOMENTUM`) both failed the honest tests — **disabled in `auto_trader.py`**
  (`FISH_FINDER_ENABLED=False`; WEAK-regime bear branch → catalyst-override + monitor only). Code
  kept as reference, revertible. Wave Rider (live-shadow) is the replacement. Surfaced on the
  dashboard `/factory` page.

## 12. Dashboard visibility (`/factory` page)
`dashboard/app.py` `get_factory_state()` + `templates/factory.html` render: the **architecture
diagram**, the **engine roster** (pass/fail + scorecards from `factory/cache/factory_snapshot.json`),
the **fleet** result, and **Wave Rider live** — mode, open/closed trades, and the **scan funnel**
(scanned → rejected-reasons → qualified → entered, from the `wave_scan_log` table) so the assembly
is visibly *running*, not sitting out. Refresh the snapshot after engine changes:
`venv/bin/python -m factory.snapshot`. New tables this build: `wave_trades`, `wave_scan_log`.

## 10. The Live Layer (`factory/live/`)
| Piece | Analogy | What it does |
|---|---|---|
| `wave_rider.py` | the boat, at sea | **LIVE-PAPER trader for Wave Rider.** Scans WILD ≥3% movers holding VWAP ~10:00, holds 3 business days, 8% stop, earnings-block. **MODE=SHADOW** (default): records intended trades + marks them to live prices, places **NO orders**. **MODE=LIVE**: real orders on the IBKR *paper* account. Stateless between runs (DB-backed `wave_trades`), launchd every 5 min, self-gates market hours, run-lock prevents overlap. |
| `lookout.py` | the crow's-nest | Watches live/shadow results vs the backtest; sounds **DRIFT** (edge below band) / **DECAY** (recent window negative) alarms. Read-only. |

**On-ramp (how Wave Rider goes truly live):**
1. **SHADOW** (now) — launchd `com.sushil.trading.wave_rider` loaded, fires Mon 9:30+; zero account risk. Logs → `logs/wave_rider.log`, table `wave_trades`.
2. Review a few days of shadow picks + `python -m factory.live.lookout`.
3. Flip to paper orders: set `WAVE_RIDER_MODE=LIVE` in the plist → `launchctl kickstart -k …wave_rider`. **One env var.**
4. ~4 weeks live-paper → the Lookout confirms it tracks the backtest → real-money conversation.

**Verify Monday:** the bridge was IBKR-disconnected at build (weekend), so `live_signal`/order paths
are logic-verified against the bridge contract but not yet exercised live. Watch the first
`logs/wave_rider.log` entries Monday — confirm it logs real WILD picks (not silent) before any LIVE flip.

## 11. Run-book
```
venv/bin/python -m factory.run all                 # validate every engine + sail the fleet
venv/bin/python -m factory.run validate            # scorecards for all engines
venv/bin/python -m factory.research.xsec_prototype # cross-sectional bench prototypes
venv/bin/python -m factory.live.wave_rider --dryscan 2026-08-14   # prove pick logic on a past day
venv/bin/python -m factory.live.lookout            # live-vs-backtest monitor
venv/bin/python -m factory.bear_test               # FREE 2022-23 bear stress-test (yfinance daily)
venv/bin/python -m factory.tests.test_factory      # full test suite
```
