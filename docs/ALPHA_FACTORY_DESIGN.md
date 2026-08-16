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
   structure, LLM copilot                              · cost survival · robustness ·
        ▲                                               uncorrelated · direction
        │ new hypotheses                          PASS ✓│        │✗ FAIL → back to bench
        │                          ┌───────────────────▼──┐
        │                          │ ④ THE ROSTER          │  validated engines only
        │                          │   (alpha library)     │
        │                          └───────────┬───────────┘
        │                          ┌───────────▼───────────┐
        │                          │ ⑤ THE CAPTAIN         │  weights by edge/risk,
        │                          │   (risk_brain.py)     │  tide-throttle, drawdown budget
        │                          └───────────┬───────────┘
        │                          ┌───────────▼───────────┐
        │                          │ ⑥ THE FILL DESK       │  discrete slots, costs →
        │                          │   (execution.py)      │  equity curve, Sharpe, MaxDD
        │                          └───────────┬───────────┘
        │        decay alarm       ┌───────────▼───────────┐
        └──────────◀───────────────│ ⑦ THE LOOKOUT (live)  │  live vs backtest; retire decayed
                                    └───────────────────────┘
```
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
| **Bargain Hunter** (meanrev_mid) | **FAIL ❌** | uncorrelated & positive OOS, but **fails robustness** (67%) — held back |
| **Steady Hand** (xsec_lowvol) | **FAIL ❌** | engine-#3 candidate (betting-against-beta). Alpha **−1.1%**, robustness 0% — the low-vol anomaly is *inverted* in this high-beta bull tape. Gate rejected. |
| Cross-Sectional Momentum (bench) | **FAIL ❌** | best config looked great (OOS1 +2.4%) but **44% robustness** — an overfit spike |

**The Roster now holds TWO uncorrelated engines** (Wave Rider + Contrarian, corr −0.13) — a directional momentum boat and a market-neutral long-short boat. Slot fleet (Wave Rider), skill-only: **+232%/2.6y, Sharpe 1.8, MaxDD −14%** (inflated — see §7). Contrarian sleeve: +0.36%/leg, t=3.9, pays bull or bear.

**The Roster today has ONE engine.** Fleet (Wave Rider only), honest market-neutral basis:
**+134% / 2.6y, CAGR +38%, Sharpe 1.21, MaxDD −24%** (raw, incl. bull tide: Sharpe ~1.8-2.4).

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
- **Retirement queue (live code, not yet cut):** Fish Finder and the equity bear book
  (`BEAR_MOMENTUM`) both failed the honest tests — flagged for retirement when Wave Rider graduates
  from shadow to live paper. Not removed yet (still running).

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
venv/bin/python -m factory.tests.test_factory      # full test suite
```
