# Turbo — the Factory→Options Bridge (options as leverage on a proven engine) — design for approval

**Status:** DESIGN ONLY. No code written beyond the Aug 16 equity-echo freeze. Nothing
ships without your approval + the shadow validation below.
**Author session:** Aug 16 2026. **Read first:** `options-edge-dive-aug16` memory,
`docs/ALPHA_FACTORY_DESIGN.md`, CONSTITUTION.md, GLOSSARY.md.

---

## 0. Why this exists (the one-paragraph case)

The Aug 16 deep-dive scored all 2,089 `opt_calc_log` rows against the underlying's real
forward path and proved **options has no tape-independent selection edge of its own** —
every apparent edge (verdict gate, IV routing, news conviction) dissolved when the month
was held constant; bull hit-breakeven decayed 90→74→28→25% May→Aug on *unchanged* logic.
Options was a leverage wrapper on momentum-beta, and momentum-beta died after May. The
fix is not a better filter (there isn't one hiding in there — all were tested). The fix is
to change what options *is*: stop generating direction, and become a **structure/leverage
transform on a Factory-validated engine**, where direction is already proven on
market-neutral alpha (beta stripped — the exact poison killing options today).

**Analogy (GLOSSARY §8 extends cleanly):** the Factory builds **engines** (strategies) whose
**sailing** (alpha) is proven above the **tide** (beta). A **turbocharger** bolts onto a
*running, healthy* engine and multiplies its power — but it burns extra fuel (carry: spread +
theta), and bolting one onto a *weak* engine just blows the gasket. **Turbo = the options
layer.** It multiplies a *proven* engine's move, and only fires when the move pays for the
fuel. If it doesn't, the engine runs unturbo'd (trade the shares, or don't trade). **The layer
is allowed to say "no structure wins here" — that refusal is the whole point** (turbocharging a
zero-edge signal is exactly what bled the old options book).

---

## 1. Non-negotiable principles

1. **Options never picks direction.** Direction/symbol/horizon come from a Factory engine
   that is *in the Roster* (validated: OOS alpha > 0, walk-forward passed, correlation-
   checked). Bench/unvalidated engines are never executed as options.
2. **Every options trade must clear the Edge-Budget gate** (§4) — the options analog of
   equity `MIN_RR`. Expected move over the hold must beat carry (spread + theta) by a
   margin. This gate is the thing that was missing and is the core of the rebuild.
3. **Instrument-first (CONSTITUTION):** Turbo ships in SHADOW — it computes and marks
   the structure it *would* trade, places no orders, and is scored before a dollar moves.
4. **Retired as direction sources:** the equity-A+ echo (frozen Aug 16) and the news engine
   (already Ghost-Ledger-only). News/Groq stays as *context*, never a trade trigger.
5. **Max 3 boolean gates (CONSTITUTION):** Turbo's three deciders are
   (a) engine-is-Roster, (b) Edge-Budget clears, (c) IV rank ≥ 25. Everything else
   (structure choice, sizing) is deterministic, not a gate.

---

## 2. Which engine is the first (and only near-term) client

| Engine | Shape | Options-able? |
|---|---|---|
| **Wave Rider** (`momentum_wild`) | discrete LONG, WILD stock, hold 3d, 8% stop | **YES — first client.** Discrete, directional, defined horizon, large raw move on WILD names. |
| Contrarian (`xsec_reversal`) | market-neutral long-short **sleeve** (basket) | No. You can't cheaply option-ize a 40-name neutral basket; it's a return stream, not discrete bets. |
| Bench engines (Night Shift, etc.) | not validated | No — Roster-only rule. |

Wave Rider is the correct first test *precisely because its validated edge is small*
(OOS **alpha ≈ +0.5%**). If a 3-day momentum edge is too thin to pay options carry, Turbo
will reject it — and that honest "no" is a successful outcome (it stops the bleed). If the
raw 3-day excursion on WILD pops (much larger than the alpha, since alpha is net-of-tide) does
clear carry in cheap-IV names, we've found options' first real home. The shadow phase decides.

---

## 3. The signal contract (input)

Turbo subscribes to the **same** signal Wave Rider already emits — it does NOT run its own
scan. A Wave Rider entry ticket (from `wave_trades` / `scan_and_enter`) carries:

```
symbol, side=LONG, entry_price, day_chg (entry-time), cluster=WILD,
hold_days=3, stop_pct=8.0, exit_on_date, engine="momentum_wild"
```

Turbo implements one function against this ticket + the live option chain:

```
plan_structure(ticket, chain, iv_rank, hv30) -> OptionStructure | None
```

`None` = "no structure clears the gate; trade the shares (or skip)." This mirrors the
Factory's swappable-contract design: Turbo is a Fill-Desk-side transform, testable in
isolation, nothing upstream changes.

---

## 4. The Edge-Budget gate — UPGRADED to convex expected value (built Aug 16)

The design first proposed a crude "typical move clears breakeven" test. When built, the data
killed it: Wave Rider's *typical* 3-day move is only **+0.4% median / +1.26% mean, 52% positive**
— a median-move gate rejects everything and misses the whole point. **Options are convex**, so the
honest question is not the typical move but the **expected value of the structure's payoff across
the engine's real 3-day outcome distribution**, which includes the fat right tail that convex
structures exist to harvest.

**How it works (implemented in `factory/live/turbo.py`):**
1. Take Wave Rider's **6,481 real WILD outcomes** from the Factory event table (`cl3`, terminal
   3-day return), with its **8% stop modeled** (any day with `lo ≤ −8%` → exit at −8%). This is
   the exact, validated distribution — no assumption.
2. **Reprice the actual structure** (Black-Scholes, `engine._bs_spread_vals` /
   `_bs_put_spread_vals`) at day-3 *with remaining time value* at each scenario underlying
   `entry × (1 + move)`.
3. `EV = mean(structure value at day-3) − entry cost`; `roi = EV / risk`.
4. **Gate:** `roi ≥ EDGE_MIN` (provisional **0.10**) **and** IV rank ≥ 25.

**Validated at build time** (the reason this is worth shadowing): the gate *discriminates* —
cheap-IV, slightly-OTM, wider debit spreads score **+18%** EV/risk (PASS) while rich-IV ATM score
**+2%** (SKIP), exactly as theory predicts. And it caught a real structural truth: the IV>65
**credit** route shows **negative EV (−13%, 26% win)** on a momentum engine — selling put premium
is the *wrong convexity* for an up-momentum signal, and the gate refuses it unprompted.

**Honest limits (what the live shadow measures):** the BS EV is idealized — no vol-crush after
the pop, and only the bid-ask the live chain already prices. Real carry will erode the +18%.
That erosion is precisely what the shadow marks (§8) capture. `EDGE_MIN` is set to leave room for
it and is tuned once real marks land. v1 reuses the existing calculators' strike selection
(EM-anchored); a v2 refinement is letting Turbo choose its own EV-maximizing strikes.

---

## 5. Structure selection (deterministic, by IV regime × horizon)

This is where debit / credit / LEAP / bear finally get a *rule* instead of a coin flip.

**Horizon rule (from the engine, not from options):** Wave Rider holds **3 days**. Never buy
a structure whose theta over 3 days is material → **use 2–4 week DTE** so the 3-day hold is a
small fraction of the option's life. You exit at day-3 EOD, far from expiry. (Short-DTE weeklies
are theta-suicide for a 3-day hold; that's why the PLTR scalp bled.)

**IV-rank routing (real, replaces the crude ≥50 flip):**

| IV rank | Posture | Structure (LONG engine) | Why |
|---|---|---|---|
| **< 25** | — | **SKIP** | The one data-validated exclusion — quiet/left-behind names, term drift was negative every month. |
| 25–45 | buy premium | **debit call spread**, 2–4wk DTE, ~25-delta long / ~15-delta short | Vol cheap → own delta+vega into the expected move; spread caps cost. |
| 45–65 | buy premium (tighter) | **debit call spread**, narrower width | Neutral vol → Edge-Budget is the arbiter. |
| > 65 | sell premium | **bull put credit spread**, 2–4wk DTE | Vol rich, theta harsh for buyers → collect it, stay bullish. Negative skew, so Edge-Budget must clear with extra margin. |

**SHORT engine** (a future bear Factory engine): mirror → put debit spread (low IV) /
bear call credit (high IV). The empirically-validated bear-put edge lives here when a
validated SHORT engine exists.

**LEAP: reserved, not used by Wave Rider.** A LEAP expresses a multi-*month* thesis; a 3-day
swing has no use for it. LEAP becomes the execution venue for a *future long-horizon* Factory
engine (trend/quality holding weeks–months). Documented here so a future session doesn't force
LEAP onto the wrong engine. This answers "we have LEAP too" — it's a structure waiting for its
matching engine, not an orphan to be shoehorned in.

---

## 6. Sizing — leverage without extra risk

Options add *upside per dollar*, not *risk beyond the validated envelope*. Wave Rider risks
`8% × PER_SLOT ($2,000) = $160` per slot. Turbo sizes the structure so **max-loss ≈ $160**
(same risk), then takes whatever leverage that buys. Result: the options book carries the exact
risk the Factory already validated, with more convexity. Slot count + capital pool mirror the
engine (5 slots / $10k) so the two never double-spend a slot.

---

## 7. Exit — mirror the engine, don't invent

The options position exits on the **engine's** signal, never an independent option-price rule
(the edge is defined on the underlying's path):
- Underlying hits Wave Rider's 8% stop level → close structure.
- `exit_on_date` EOD (3-day time exit) → close structure.
- Optional profit-take if the structure reaches ~80% of max-profit early (pure risk
  management, logged separately, doesn't touch the thesis).

Reuses the existing (working, well-tested) watchman close machinery — that layer is *not* the
problem and is left intact.

---

## 8. Validation path (instrument-first, CONSTITUTION-compliant)

- **Phase 0 — SHADOW (first ship).** On every Wave Rider pick (itself currently shadow),
  Turbo computes `plan_structure`, records the would-be structure to a new
  `options_shadow` table, and marks it to **live option chain prices daily** (we already run a
  chain snapshot collector — extend it to shadowed structures). Zero capital.
  **Scored on:** (1) structure P&L vs simply holding the shares = the *leverage premium net of
  carry* (the only number that justifies options existing); (2) did the Edge-Budget gate
  separate winners from losers; (3) Factory basis — alpha & Sharpe of the options stream.
- **Phase 1 — LIVE paper.** Only after ~20+ shadowed picks show a positive leverage premium
  net of carry AND the Edge-Budget gate demonstrates separation. Flip one constant
  (`TURBO_MODE=LIVE`), IBKR paper only.
- **Sunset review:** 30 days after Phase 0, per CONSTITUTION. If the leverage premium is ≤ 0
  net of carry, the honest conclusion is *options adds nothing to this engine* — and we either
  wait for a bigger-edge engine or retire the options book entirely. That must be an acceptable
  outcome of this design, or the design isn't honest.

---

## 9. Where it lives (architecture)

New module `factory/live/turbo.py` (a Fill-Desk-side execution transform), plus an
`options_shadow` table. It imports the Wave Rider ticket shape and the existing options
calculators/chain helpers from `options/options_trader.py` (reused for strike/greek math —
those mechanics work; it's the *selection* that didn't). It does **not** touch
`_check_equity_scan_triggers` (frozen) or the news engine. GLOSSARY gets a **Turbo** row
in the session it ships.

---

## 10. Open decisions for the build session (pick before coding)

1. **`E_move` estimator weighting** — min(engine-realized, implied) as proposed, or blend?
   (Proposed: min. Conservative is correct while unproven.)
2. **`MARGIN`** starting value (proposed 1.3) — will be tuned in shadow, needs a start.
3. **Chain snapshot cadence for shadow marking** — reuse `collect_chain_snapshots.py` daily,
   or intraday? (Proposed: daily EOD is enough for a 3-day hold.)
4. **Does Turbo run only when Wave Rider is LIVE, or also shadow-on-shadow now?**
   (Proposed: shadow-on-shadow immediately — max learning, zero risk, and it pressure-tests
   the pick pipeline before either goes live.)

---

## 11. What this explicitly is NOT

- Not a new scanner. Not a news signal. Not a "smarter gate" on the old echo path.
- Not a promise options will trade — it may correctly conclude Wave Rider's edge is too thin
  for options, and stay flat. That is a *win* over today's guaranteed carry-bleed.
