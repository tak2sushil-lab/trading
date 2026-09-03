# The patient engine — "give it room, judge its character later" (Aug 19 2026)

Design as specified: **1 contract; a wide stop at entry and nothing else; let it breathe for
hours; then switch on a wide trail — if it kept making highs it rides, if it showed no
character it gets cut; flat at the close; possibly enter earlier (09:45) to buy more hours.**
Distinct from the hard time-stop already rejected — this cuts *via a trail*, not on the clock.

Lab: `futures/factory/patient_lab.py` (`--live --sim --grid --early`). Nothing wired.

> **A look-ahead bug was found and fixed mid-analysis.** The first run showed +$22,705 over
> 5.5yr with 0 blows. That was wrong. When the trail activates, `peak − gap` is often *above*
> the current market — the tape left that level during the breathe window, so no fill is
> possible there. **21% of activations were affected, booking exits a median 91 points (p90
> 297) better than reality.** A stop can only rest below the market; on activation it becomes
> a market exit at the price actually available. Corrected, the same config makes **+$705**.
> The look-ahead was ~90% of the apparent result. Every number below is post-fix.

## The answer you asked for: how often does it blow the account?

**IBKR — zero. TC/TopStep — every single configuration blows, 1 to 5 times.**

Live transaction data (Jun 5 – Aug 17 2026, real entries, real prices, engine exits):

| | IBKR | TC |
|---|---|---|
| live trades / replayable / after 1-at-a-time | 70 / 60 / 27 | 93 / 76 / 38 |
| what actually happened | +$2,822, worst day −$1,279, **0 blows** | +$287, worst day −$710, **0 blows** |
| engine, stop 1000 · breathe 180m · trail 150 | +$1,389, worst −$855, **0 blows** | −$665, worst −$2,007, **2 blows** |
| engine, stop 625 · breathe 180m · trail 150 | +$1,389, worst −$855, **0 blows** | +$85, worst −$1,257, **1 blow** |

*(IBKR's +$2,822 actual includes +$4,210 of manual `FUT CLOSE` on three days — the automated
book was negative. The engine's 27 trades are what one-position-at-a-time leaves of 60 entries,
which incidentally dedupes the live duplicate-entry clusters.)*

5.5yr sim — the window that can actually see a bad year:

| config | n | total | maxDD | worst day | halts | IBKR blows | TC blows | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| stop 1000 · 180m · trail 150 | 751 | +2,255 | −5,229 | −1,722 | 2 | **0** | **4** | −913 | +1,166 | −1,469 | +70 | −347 | +3,748 |
| stop 1000 · 240m · trail 150 | 748 | +705 | −6,485 | −2,007 | 2 | **0** | **5** | −1,157 | +961 | −1,425 | +530 | −2,387 | +4,183 |
| **stop 625 · 180m · trail 150** | 751 | **+2,855** | −5,064 | −1,257 | 2 | **0** | **3** | −913 | +1,166 | −1,469 | +70 | +253 | +3,748 |
| stop 500 · 180m · trail 150 | 752 | +2,038 | −5,612 | −1,479 | 1 | **0** | 4 | −913 | +1,166 | −1,469 | +70 | −564 | +3,748 |
| wide 1000 · hold to close | 748 | +2,202 | −6,427 | −2,007 | 2 | **0** | 5 | −1,454 | +2,440 | −1,530 | +825 | −2,870 | +4,790 |
| live exit stack (reference, 1c) | 949 | +938 | −7,455 | −814 | 0 | 1 | 4 | −736 | −1,989 | −1,645 | −2,033 | +1,420 | +5,921 |

**IBKR is never at risk of dying** — the $5,000 allocation is never lost, in the sim or live.
The danger there is bleed, not death. **TC is a different animal**: a $2,000 trailing MLL with
a $1,250–$2,000 single-trade risk means one bad fill can end the account, and it does — 3–5
times in 5.5 years, roughly once a year.

## There is no blow-free region

Breathe × trail surface at stop 625 (the whole point of a surface is to find a *region*):

**TC blows** — rows = breathe, cols = trail gap:

| | 75p | 100p | 150p | 200p | 300p | 500p |
|---|---|---|---|---|---|---|
| 60m | 4 | 4 | 3 | 3 | 5 | 3 |
| 120m | 3 | 4 | **1** | 4 | 5 | 3 |
| 180m | 3 | 4 | 3 | 4 | 3 | 3 |
| 240m | 4 | 4 | 4 | 5 | 5 | 3 |
| 300m | 2 | 3 | 3 | 3 | 3 | 2 |

**Green years** are 3/6 almost everywhere, 4/6 at best, never higher. **Total $** ranges
+$908 to +$4,391 with no structure (120m/75p = +4,391 sits next to 180m/75p = +908). **Worst
day** is −$902 to −$1,376 *regardless of trail setting* — it is set by the stop and the day,
not by the trail. The single 1-blow cell is the outlier of a noisy surface, not a plateau.

## The premise itself does not hold

The engine rests on: *by hour 3 a trade has either shown character or it hasn't, and that
tells you what to do next.* Tested directly — split trades at the decision point by the peak
they had reached, then measure the rest of the day from that point:

**decision point 180 min (n=658)**

| peak-so-far bucket | n | peak | P&L now | **rest of day** | % rest positive |
|---|---|---|---|---|---|
| Q1 no character | 166 | 16 | −74 | **+2.5** | 54% |
| Q2 | 163 | 45 | −39 | +7.1 | 60% |
| Q3 | 164 | 79 | +6 | +8.3 | 61% |
| Q4 strong | 165 | 194 | +101 | **−1.9** | 57% |

correlation(peak-so-far, rest-of-day) = **−0.076** at 180m, **+0.017** at 120m, **+0.032** at
240m — and the sign flips every year (2021 −0.11 · 2022 −0.26 · 2023 +0.16 · 2024 +0.20 ·
2025 +0.16 · 2026 −0.11 at 120m). At 120 minutes the "strong character" quartile has the
**worst** rest-of-day (−10.4 pts, 52% positive) and the "no character" quartile the best
(+7.4 pts, 59%).

**At the decision point, what the trade has done tells you nothing about what it will do.**
Both halves of the rule lose their basis at once: the runner is not more likely to keep
running, and the trade that showed nothing is not more likely to keep failing. This is the
same shape as the Aug 18 finding that room is symmetric, and the same shape as six other
reaction rules this program has tested.

## What the engine does and does not achieve

It is **not** worthless — it is the best exit variant tested in these two sessions:

| | total | maxDD | worst day |
|---|---|---|---|
| live exit stack, 1c | +$938 | −$7,455 | −$814 |
| wide stop, hold to close | +$2,202 | −$6,427 | −$2,007 |
| **patient: 625 · 180m · trail 150** | **+$2,855** | **−$5,064** | −$1,257 |

It roughly triples the live stack's P&L at one contract and cuts drawdown by a third. But it
gets there by being a **better-shaped stop**, not by reading character — it caps the trades
that never worked while leaving winners alone, which is what any wide trailing stop does. And
it is still **green in only 3 of 6 years**, which is the same wall every configuration in this
program hits.

## Verdict

- **How many times would it have blown the account?** IBKR: **never** (sim or live). TC/TopStep:
  **3–5 times in 5.5 years** in the sim, and **1–2 times in 2.5 months** of live data. There is
  no setting that avoids it, because a single-trade risk of $1,250–$2,000 against a $2,000
  trailing MLL is not survivable by exit tuning.
- **The wide stop is still the wrong half of the idea.** 625pt beats 1000pt on total, drawdown,
  worst day and blow count. The patience is doing the work; the width is doing harm.
- **The character premise is not supported.** Correlation ≈ 0 and sign-unstable per year.
- **The engine is worth keeping as a candidate exit for IBKR only**, where it cannot kill the
  account, and where it beats the live stack at one contract on both P&L and drawdown. It is
  not a TopStep design at any setting.
- Recommended if pursued: **stop 625, breathe ~120–180 min, trail 150, IBKR only, 1 contract,
  log-only shadow first.** Not because the surface picked it — the surface is noisy — but
  because it sits in the least-bad region and its stop width is the only one inside the IBKR DLL.
