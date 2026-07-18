# TriVega Glossary — Canonical Names for Every Moving Part
**Adopted Jul 18 2026. Rule: use these names in all docs, Telegram messages, session notes,
and conversation. Code identifiers, DB tables, and launchd service names are NEVER renamed —
they are listed here as "code name" so old logs stay searchable.**

---

## 1. The Six Word Types (use exactly one per concept)

| Word | Strict meaning | Analogy |
|------|---------------|---------|
| **Gate** | A boolean pre-entry check that can BLOCK a trade. Nothing else is called a gate. | Bouncer at the door |
| **Score** | A number that ranks or grades a setup. A score alone never blocks — a gate reading a score does. | Judge's scorecard |
| **Exit rule** | Anything that closes an open position. | Ejector seat |
| **Auditor** | Watches and scores decisions after the fact. Changes nothing live. | Referee reviewing tape |
| **Learner** | Nightly job that adjusts live weights/grades from results. The only auditor-type that DOES change behavior. | Night school |
| **Shadow book** | A strategy tracked on paper inside our logs, placing no orders. | Flight simulator |

Retire the words: "check", "filter", "screen" (as nouns), "mechanism" — each was being used
for all six types interchangeably. When speaking, pick the type word above.

---

## 2. Futures (NY session — futures_trader.py unless noted)

| Canonical name | Code name (unchanged) | Type | What it does |
|---|---|---|---|
| **Weather Report** | `detect_regime()` → CHOPPY / QUIET / TRENDING | Score | Classifies the day from first-hour IB range; sticky after 10:30. Everything downstream dresses for this weather. |
| **Trend Jury** | Hero gate / Avengers composite, `hero_score.py` (H1–H6), `HERO` in gate_blocks | Gate (reads a score) | 5 independent jurors (MTF align, RSI momentum, VWAP reclaim, etc.) vote on whether the trend is real; entry needs a conviction. Known blind spot: 1H-lag on grinding intraday rallies. |
| **Volume Pulse gate** | RVOL gate, `calc_session_rvol()`, `RVOL_GRAD_FLOOR=0.70`, `RVOL_ENTRY` in gate_blocks | Gate | Blocks entries when relative volume is dead. Graduated: 0.70–0.85 allowed if Trend Jury score is GOLD. Fixed Jul 17 to use completed bars only. |
| **Setup Grade** | `grade_entry()` A+/A, `GRADE` in gate_blocks | Score + Gate | Points-based entry quality; currently A+-only passes. |
| **Higher-Timeframe Agreement gate** | HTF trend gate, `HTF` in gate_blocks | Gate | 30-min trend must agree with trade direction. |
| **Overnight Veto** | `OVN_SKIP` | Gate | Overnight bias can veto the whole NY day. Known architecture flaw (whole-day veto, no reconsideration) — same disease London's skip-day had. |
| **Elephant (dip-buyer)** | `_scan_elephant()` | Entry module | Mean-reversion: buys sharp dips on STRONG_BULL days. "Looks bad at entry" is its normal premise. N≈4/yr — do not fit new gates to it. |
| **Morning Breakout entries** | ORB_LONG / ORB_SHORT, PM_LONG (PM_SHORT is DISABLED, 0-for-7) | Entry module | Pre-market-level and opening-range breakout entries. |
| **Weather-Aware Profit Locks** | `EXIT_PARAMS_BY_REGIME`, BE/trail tiers | Exit rule | BE + trail distances chosen per Weather Report (CHOPPY/QUIET lock fast+tight; TRENDING gets room). |
| **Catastrophic Backstop** | `BASE_STOP_PTS=200` | Exit rule | Wide hard stop; not the primary loss-cutter. |
| **Trade Cop** | parity harness, `parity_check.py`, launchd `parity_check` 22:45 ET | Auditor | Nightly: replays today in sim, diffs vs live trades. Divergence = sim and live have drifted apart. Update its `SIM_FLAGS` whenever live config changes. |
| **Bouncer Report Card** | gate audit, `futures/gate_audit.py --score`, `gate_blocks` table, launchd `gate_score` 22:00 ET | Auditor | Nightly: for every trade a gate blocked, scores whether the bouncer turned away a good customer (forward 30m/60m move). |
| **Nightly P&L Ledger** | expectancy ledger, `futures/expectancy_ledger.py`, launchd `expectancy_ledger` 22:35 ET | Auditor | Nightly: trailing-14d expectancy per book, evaluates the Mirror Book, builds `gate_blocks_ctx` feature store. |
| **Black Box Recorder** | the "decoder", `futures/live_rule_sim.py`, launchd `mnq_decoder`, 22,973+ snapshots | Auditor | 24/7 flight recorder: logs full market state (VWAP, phase, flow, ADX…) every 60s across all CME sessions. Trades nothing. Its raw signals are proven ANTI-predictive — that's data, not failure. |
| **Mirror Book** | shadow fish-net, `shadow_fishnet` table | Shadow book | Fades (trades the mirror of) the Black Box Recorder's LONG signals. +16.3 IS / +10.0 OOS pts/episode. Shadow only — constitution requires 30+ green days before promotion talk. |
| **Recorder rule codes** | `A_ext` (extension), `C_stale` (stale signal), `X1_rvol`, `X4_grade` in gate_blocks | — | Internal hypothesis codes inside the Black Box Recorder, not live gates. |

## 3. Futures (London — london_trader.py / london_v2_sim.py)

| Canonical name | Code name | Type | What it does |
|---|---|---|---|
| **London Breakout** | Signal A, IB break 4–8am ET | Entry module | Breakout of the 3–4am Initial Balance. |
| **Early Shield** | BE=0.10×ATR break-even | Exit rule | Near-instant break-even stop. Load-bearing armor — without it the breakout LOSES money. Do not remove. |
| **Skip-Day Veto (REMOVED Jul 18)** | `compute_overnight_bias_london()` skip_day; ambiguity now logged as `OVN_SKIP_INFO` | Gate (retired) | Used to veto the whole session on ambiguous overnight. Removal validated +$1,114/18mo. Sunset review Aug 17 2026. |

## 4. Equity (auto_trader.py)

| Canonical name | Code name | Type | What it does |
|---|---|---|---|
| **Entry Score** | L1, `grade_setup()` A+/A | Score + Gate | Points-based setup grading (patterns, sector grade, DNA modifiers, burst timing). |
| **Fitness Gate** | L2 (HOD×3, RUN×4, VWAP>2.5×), HALF-sizing | Gate | Pre-entry fitness screen; marginal passes get half size. |
| **Probation Exit** | L3, T+5 confirmation | Exit rule | New trade is on probation for 5 bars; ejected if it doesn't confirm. |
| **Book Health Selector** | `book_is_on()` / `compute_book_health()` | Gate | Hot-hand rule: each direction (LONG/SHORT book) only trades while its trailing-10d signal drift is positive. Measures the SIGNAL's edge, not our execution. |
| **Stock Personality** | DNA clusters: HIGH_VOL / INSTITUTIONAL / MOMENTUM, `dna_analysis.py` | Score modifier | Per-stock archetype adjusting entry points and exit style. Re-cluster quarterly. |
| **Weather Report (equity)** | market regime: NORMAL / STRONG / CAUTIOUS / WEAK / CHOPPY | Score | Market-level regime; routes bull/bear scanning. |
| **Catalyst Wildcard** | `_scan_catalyst_override`, dynamic catalyst upgrade | Entry module | News/gap movers bypass CAUTIOUS/CHOPPY weather because their move is market-independent. |
| **Exit Stack** | 13 mechanisms, priority-ordered | Exit rules | See CLAUDE.md table — keep calling individual rules by their existing names. |
| **Equity Night School** | `learner.py` via `nightly_learning()` 23:00 ET inside autotrader | Learner | Adjusts RSI/volume/momentum/sector/earnings weight multipliers + sector grades from closed trades. The ONLY writer of strategy weights and sector grades. |

## 5. Options

| Canonical name | Code name | Type |
|---|---|---|
| **Options Night School** | `options/learner_options.py`, launchd `options_learner` 22:15 ET | Learner |
| **Watchman** | `options/watchman.py` | Auditor + Exit rules (already well-named — keep) |
| **Scalp Engine** | OPT_SCALP | Entry module |

---

## 6. The Nightly Assembly Line (all times ET, Mon–Fri)

```
21:30  Bar collectors        collect_bars.py + futures/collect_bars.py   (raw data in)
22:00  Bouncer Report Card   gate_audit --score                          (were the gates right?)
22:15  Options Night School  learner_options.py                          (options what-ifs)
22:35  Nightly P&L Ledger    expectancy_ledger.py                        (book expectancy + Mirror Book)
22:45  Trade Cop             parity_check.py                             (sim still matches live?)
23:00  Equity Night School   learner.py via autotrader scheduler         (reweight equity scoring)
```
Each stage feeds the next morning's decisions. None overlap; all six stay.

---

## 7. Naming Rules Going Forward

1. New blocking check → name it `<what>_gate`, register it in the Bouncer Report Card, add a row here.
2. New paper-traded strategy → `shadow_<name>` table + "X Book" canonical name.
3. New nightly job → say which assembly-line slot it fills; auditor or learner, pick one.
4. One canonical name per concept — in Telegram, logs you add, docs, and conversation. Old code
   names stay in code forever (grep-ability > purity); never rename identifiers, DB tables, or
   launchd services for terminology reasons.
5. Update this file in the same session any time a part is added, retired, or renamed.
