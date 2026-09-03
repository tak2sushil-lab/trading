# Futures sizing thread — the verdict (Aug 19 2026)

Continues `docs/FUTURES_SIZING_CATCHUP_2026-08-18.md`. All work is in
`futures/factory/sizing_lab.py` (`venv/bin/python -m futures.factory.sizing_lab --all`),
full output archived at `futures/factory/_out/sizing_lab_2026-08-19.txt`. No live code
was changed. Everything below is per-year; nothing is a total-only claim.

## The headline

**Sizing is not the remaining lever, and the Aug 18 "best config" is beaten by what is
already running.** The four questions came back:

| # | question | answer |
|---|---|---|
| 1 | does the config hold under the real exit stack? | the FILTER holds and is better than reported; the GEOMETRY does not — the live exit stack beats the barrier on the same entries |
| 2 | does ATR-normalised sizing walk forward? | **no** — no within-year edge, train-optimal ≠ test-optimal, and it only ever helped the geometry that itself loses |
| 3 | does room work as a SIZE input? | **no** — flat mean across all five buckets, `$/DD` identical to flat sizing |
| 4 | wire it, or keep researching? | wire the **day filter** as a log-only shadow; do **not** wire the geometry or the sizing |

## Q1 — the config under the real exit stack

Cached full-pipeline runs (`sim_replay` at parity SIM_FLAGS, $6/contract friction).
`$/DD` = total ÷ |maxDD| = the capital-efficiency number. Flat sizing cannot change it.

| config | n | total | maxDD | $/DD | Sh | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline book, all days | 949 | +3,353 | −7,715 | 0.43 | 0.32 | −1,138 | −1,615 | −1,314 | −2,480 | +1,480 | +8,421 |
| baseline, LONG only | 593 | +5,376 | −4,029 | 1.33 | 0.88 | +152 | −2,839 | +1,012 | −291 | +1,372 | +5,970 |
| MTF days, all sides | 543 | +5,547 | −2,492 | 2.23 | 1.09 | −593 | −275 | +183 | −434 | +693 | +5,973 |
| **MTF days, LONG only** | **343** | **+7,311** | **−1,640** | **4.46** | **2.38** | +333 | **−683** | +1,744 | +1,330 | +846 | +3,742 |
| MTF days, SHORT only | 200 | −1,764 | −4,651 | −0.38 | −0.87 | −926 | +254 | −927 | −230 | +235 | +369 |

The day filter is **pipeline-clean, verified**: filtering the cached full book reproduces
the dedicated MTF pipeline run exactly, zero trades differ. That confirms empirically what
the thread asserted — day-level filters survive where trade-level filters die.

### The geometry does not hold

Same 343 entries, only the exit differs, one position at a time, 1 contract:

| exit | n | total | maxDD | $/DD | Sh |
|---|---|---|---|---|---|
| **real exit stack (200pt, trail, rev-exit)** | 276 | **+4,260** | **−1,303** | **3.27** | **1.78** |
| hold to 15:10, no stop / 1000pt / 600pt brake | 277 | +3,796 | −1,666 | 2.28 | 1.04 |
| barrier −1000 / +200 (the Aug 18 config) | 277 | +3,472 | −1,666 | 2.08 | 0.97 |
| hold to 15:10, 200pt stop | 277 | +2,787 | −2,283 | 1.22 | 0.83 |

The no-stop, 1000pt and 600pt rows are **byte-identical** — confirming the wide brake never
fires (1 in 472, as the memory said). It is not a brake, it is decoration.

Two corrections to the Aug 18 record:
- **"green 6/6" was an artifact.** The barrier sim scored every entry independently,
  including entries a hold-to-EOD position would still have been occupying the slot for.
  Enforcing one position at a time drops 343 → 277 trades and flips 2025 to −$467:
  **green 5/6, not 6/6.**
- **The barrier's ATR-sizing gain does not transfer.** ATR-normalised sizing lifts the
  barrier's `$/DD` 2.24 → 3.19 but *cuts* the real stack's 4.46 → 2.87. The mechanism is
  clean: with a fixed 200pt stop, dollar risk per contract is **already constant**, so
  normalising by ATR solves a problem that only exists in a hold-to-close design where
  real risk scales with the day's range.

## Q2 — the ATR sizing walk-forward: it fails

Fit on 2021-23, test on 2024-26 (MTF-LONG book):

- Train-optimal risk-per-trade by `$/DD` is **$600**; test-optimal is **$700**; the test
  `$/DD` range across the whole $400–$2,000 sweep is 3.66 … 6.43 — **peaky, not a plateau.**
  The train curve is noise (0.61 … 1.49) because train maxDD barely moves with the parameter.
- **No value of risk-per-trade beats the pipeline's own sizing** (`$/DD` 4.46).

The confound that kills it: MNQ ATR rose 189 → 455 almost monotonically, so "size down when
ATR is high" is nearly the same instruction as "size down in later years."

| year | n | ATR | low-ATR half $/contract | high-ATR half $/contract |
|---|---|---|---|---|
| 2021 | 40 | 189 | +12 | +1 |
| 2023 | 93 | 209 | +25 | +5 |
| 2024 | 80 | 238 | +40 | −18 |
| 2025 | 80 | 306 | +12 | −1 |
| 2026 | 34 | 455 | +20 | **+129** |

Low-ATR days paid more in 4 of 5 years — but 2026 inverts hard, and the decisive control
settles it: sizing 3/2/1 by ATR tercile **inside each year** (same instruction, zero
cross-year drift) gives `$/DD` **2.67 — worse than flat 2 (3.18) and far worse than the
pipeline's 4.46.** The raw-ATR gain was the time trend, not the volatility signal.

## Q3 — room as a size input: no

| room bucket | n | $/contract | WR | sd |
|---|---|---|---|---|
| R1 dead | 69 | +15 | 61% | 134 |
| R2 | 68 | −2 | 59% | 143 |
| R3 | 69 | +24 | 61% | 119 |
| R4 | 68 | +14 | 62% | 143 |
| R5 wide | 69 | +21 | 65% | 126 |

No monotone mean, no monotone spread. "Size up with room" returns `$/DD` **3.19** against
flat-2's **3.18** — it buys leverage, not allocation. Room now has **three** independent
rejections: as a gate on the book, as a gate on the wide-stop engine, and as a size input.

## Q4 — the one sizing input that does carry signal (already live)

The hero-score ladder (`contracts_from_regime_score`) puts 2 contracts on high Trend-Jury
scores. Nothing here was fitted by this lab — it is read out of the sim.

| book | tier | n | $/contract | WR | sd | years won |
|---|---|---|---|---|---|---|
| MTF-LONG | sized 1 | 286 | +9 | 59% | 137 | — |
| MTF-LONG | sized 2 | 57 | **+42** | **74%** | 108 | **6/6** |
| full book | sized 1 | 784 | −1 | 55% | 157 | — |
| full book | sized 2 | 165 | +13 | 65% | 160 | 3/6 |

Permutation test (reassign the high-conviction label to k random trades, 2,000 shuffles,
1/4 stretch):

| book | metric | real | random median | beats | verdict |
|---|---|---|---|---|---|
| MTF-LONG | $/DD | 7.26 | 2.67 | 97.9% | real signal |
| MTF-LONG | Sharpe | 2.78 | 1.57 | 98.4% | real signal |
| full book | $/DD | 0.77 | 0.19 | 82.8% | **inside the noise band** |

So: **conviction sizing beats random sizing at identical average exposure — but only on the
subset that was itself chosen from this data.** On the unfiltered book it is not
significant. Leave-one-year-out on MTF-LONG passes 5/6 (fails when 2022 is dropped).
Treat as *suggestive*, not established. Its practical value is the comparison it enables:
the live book currently allocates its second contract essentially **at random** (duplicate
entries, see below), and random allocation is the 2.67 column.

## Q7 — is the MA50 filter a lucky pick? No — there is a plateau

Swept on the cached full book (legitimate, because day filters are pipeline-clean):

| filter | n | total | maxDD | $/DD | Sh |
|---|---|---|---|---|---|
| no filter, LONG only | 593 | +5,376 | −4,029 | 1.33 | 0.88 |
| close > MA20 | 358 | +3,337 | −1,718 | 1.94 | 1.03 |
| close > MA50 | 380 | +7,177 | −2,569 | 2.79 | 2.01 |
| close > MA100 | 407 | +8,258 | −1,690 | 4.89 | 2.22 |
| MA50 + rising 3d | 342 | +7,118 | −1,640 | 4.34 | 2.32 |
| **MA50 + rising 5d (shipped candidate)** | 343 | +7,311 | −1,640 | **4.46** | 2.38 |
| MA50 + rising 10d | 332 | +6,885 | −1,571 | 4.38 | 2.30 |
| MA100 + rising 3d | 376 | +8,594 | −1,690 | **5.08** | 2.49 |

The whole MA50–MA100 family lands 4.2–5.1; MA20 is too fast, MA200 too slow. That is a
mechanism with a plateau around it, not a spike. Note MA100 variants are green in 2022,
so **"green 6/6" is parameter-dependent and was never robust.** 2022 carries only 16
MTF-LONG trades — the year where a green/red verdict means least.

## The trap I walked into, and the correction

Measuring the book against "hold 1 long contract 10:30 → 15:10 on the days it traded" gives
tide **+$21,662** vs book **+$7,311**, alpha **−$4,182**, negative in 5 of 6 years. That
reads as "the exit stack destroys the day's drift." **It is a tautology, and it is wrong.**

- The benchmark is lookahead: at 10:30 you cannot know the signal will fire at 11:45.
- Buying 10:30 on **all** MA50-up days — which *is* knowable at 10:30 — **loses $2,609**
  over 809 sessions.
- Decomposing the move: **79% of the 10:30 → 15:10 travel happens BEFORE the book enters.**
  The signal fires *because* price already ran. Post-entry drift, the only part actually
  available, averages **+9.0 pts** and is positive in 58% of sessions.

Against the deployable benchmark — hold from the **real entry time** — the exit stack is a
net **positive** (`$/DD` 3.27 vs 2.28, Sharpe 1.78 vs 1.04). Kept in the lab as a
capture-efficiency diagnostic with the caveat printed next to the number.

## Q12 — the rule on the live account (the only data the sim never saw)

Live CLOSED futures trades Jun 5 → Aug 17 2026, **manual `FUT CLOSE` stripped** (18 trades,
+$4,210, all on Aug 3/4/6 — the Aug 16 bench established IBKR's realised edge was the user's
hand, not the automated book):

**Automated-only live book: −$1,100 on 145 trades.**

| bucket | n | total | WR | IBKR / TC |
|---|---|---|---|---|
| MTF PASS + LONG (**the rule**) | 50 | **−105** | 38% | IBKR +333 / TC −438 |
| MTF PASS + SHORT (blocked) | 31 | +322 | 55% | IBKR −1,077 / TC +1,398 |
| MTF FAIL any side (blocked) | 60 | +194 | 55% | IBKR +1,040 / TC −846 |

| | actual | under the rule |
|---|---|---|
| P&L | −$1,100 | −$105 |
| worst single day | −$1,510 | −$981 |
| days ≤ −$1,000 | 2 | **0** |
| trading days | 43 | 16 |

**The rule is risk-reducing and P&L-null on live data.** It removes both DLL breaches and
most of the bleed, but it does not produce the sim's profit. The sim says MTF-LONG 2026 =
+$3,742 over 34 trades; live automated MTF-LONG over 2.5 months = −$105 over 50 trades — a
live/sim divergence *within the same year*, consistent with the two already-documented
gaps (selection, not execution; and live carries ~2× the sim's exposure through duplicate
entries).

## How long a forward shadow must run

Block-bootstrap of whole trading days from the historical MTF-LONG book, assuming the edge
is real and unchanged (the best case):

| window | active days | P(positive) | median | p10 | p90 |
|---|---|---|---|---|---|
| 1 month | 4 | 64% | +102 | −321 | +554 |
| 3 months | 13 | 71% | +335 | −485 | +1,156 |
| 6 months | 27 | 79% | +716 | −461 | +1,883 |
| 12 months | 54 | 87% | +1,466 | −202 | +3,080 |
| 24 months | 107 | 94% | +2,818 | +465 | +5,061 |

The book trades **4.5 active days per month**. Anything under a year cannot distinguish
"the edge is real" from "the edge is gone" — it can only catch a catastrophic break.

## Recommendation

**Wire the day filter as a log-only shadow. Wire nothing else.**

1. **Do not wire the barrier geometry.** It loses to the live exit stack on the same
   entries (2.08 vs 3.27 `$/DD`), and its wide brake is inert.
2. **Do not wire ATR-normalised sizing.** It fails walk-forward, has no within-year edge,
   and its apparent gain belongs to a geometry that itself loses.
3. **Do not wire room-scaled sizing.** Third independent rejection.
4. **Do wire the daily-trend day filter — log-only.** It is subtractive, day-level (so
   pipeline-clean), sits on a parameter plateau, and is independently corroborated: the
   Aug 16-17 bench found the same lever cut P(blow) 29% → 11.5%. Log per live trade
   whether the day passed; change no entry behaviour. That starts the clock at zero risk
   and costs nothing, which matters because the honest forward-validation horizon is
   **12 months**, not weeks.
5. **The real open item is not sizing — it is the duplicate-entry gap.** Live allocates
   its second contract at random (79% of live entry-events are size-2 clusters; the sim
   produces 0%), and random allocation is measurably the worst way to spend that contract
   (`$/DD` 2.67 vs 7.26 for conviction allocation). Every backtest in this program still
   describes a 1-position book while the account trades a 2-position one. **Closing that
   gap is worth more than any sizing scheme tested here** and it is a decision the user
   has already deferred once.

## Method rules this session adds

- **State whether a benchmark is deployable before drawing a verdict from it.** The
  −$4,182 "negative alpha" was real arithmetic on an impossible strategy.
- **A ratio can improve because its denominator happened not to move.** The 1/4 stretch's
  `$/DD` 7.26 comes from n=57 never landing inside the worst drawdown path — which is why
  it needed a permutation test, and why it passed on one book and failed on the other.
- **Independent-per-trade replays silently double-book the position slot.** Enforcing one
  position at a time cut the Aug 18 config by 19% of its trades and cost it its 6/6.
