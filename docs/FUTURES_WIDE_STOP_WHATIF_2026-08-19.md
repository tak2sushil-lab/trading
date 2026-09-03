# What-if: wide stop, 1 contract, derived exit (Aug 19 2026)

The design as specified: **1000pt SL on every trade, 1 contract at a time, existing entry
mechanism unchanged (no MA50 filter), same on IBKR NY / TC NY / London, exit derived from
data, flat same day via a time rule.** Built in `futures/factory/wide_stop_lab.py`
(`--all`), output `futures/factory/_out/wide_stop_lab_2026-08-19.txt`. Nothing wired.

**Why the cached entries are valid** (checked, not assumed): `hero_score
.contracts_from_regime_score()` skips on the score alone — `calc_contracts_result` only caps
the *gold* tier at `min(2, cc)`. Widening the stop takes `cc` 2→1, which **removes no
entries**; it only makes gold trades 1 contract, which is the design anyway. So the 949
cached NY entries are exactly what a 1000pt-stop pipeline would produce.

## The risk arithmetic, first

MNQ is **$2/point**, so a 1000pt stop is **$2,000 per contract**:

| limit | value | 1000pt stop vs it | max compliant width |
|---|---|---|---|
| TC trailing MLL | $2,000 | **100%** — one stop-out ends the account | 1000pt (850pt with the $300 buffer) |
| TC DLL | $1,000 | **200%** — breached by a single trade | **500pt** |
| IBKR soft DLL | $1,250 | **160%** | **625pt** |

"Risk ≤ MLL" is satisfiable at 1000pt only against TC's *raw* MLL, and only exactly. It
breaches every DLL. **A single-trade risk larger than the DLL means the daily halt cannot
protect the account** — the loss arrives in one fill, before any daily rule can intervene.

## Does the tape deliver 150 or 200 points? No — it delivers 59

Existing entries, both sides, 1-min paths, flat 15:10 (n=944):

| target | reached before −1000 | % | median min to hit | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|
| +50 | 540 | **57%** | 38 | 40% | 67% | 45% | 52% | 62% | 74% |
| +100 | 278 | **29%** | 72 | 11% | 33% | 16% | 24% | 38% | 53% |
| +150 | 141 | **15%** | 104 | 4% | 15% | 6% | 9% | 23% | 34% |
| +200 | 79 | **8%** | 127 | 1% | 9% | 2% | 6% | 13% | 20% |
| +300 | 26 | 3% | 117 | 0% | 4% | 0% | 1% | 3% | 9% |

Excursion distribution (points per trade):

| | p10 | p25 | median | p75 | p90 | mean |
|---|---|---|---|---|---|---|
| best it ever got (MFE) | 15 | 29 | **59** | 112 | 182 | 86 |
| worst it ever got (MAE) | 17 | 30 | **61** | 121 | 206 | 93 |
| where it ended (EOD) | −133 | −51 | 10 | 67 | 130 | 5 |

**MAE (61) is larger than MFE (59).** These entries go against you as much as they go for
you. A 150pt target is a 15% event and a 200pt target an 8% event — a target set there is
not "keeping what the tape gives", it is waiting for a tail.

### On "the entry is right 61% of the time"

That number is only true for a low bar, and the bar is the whole trade-off:

| bar | rate |
|---|---|
| ever green at all | 99.8% |
| ends the day green | 53.6% |
| MFE beats MAE (went our way first) | **51.3%** |
| reaches +50 before −1000 | 57.2% |
| reaches +100 before −1000 | 29.4% |
| reaches +150 before −1000 | 14.9% |

At the level that matters for a real target, the entry is right **8–29%** of the time, not
61%. The stop is 1000 wide; at a 200pt target the break-even hit rate is 83%.

## The time rule is the design's weakest part, not its strongest

Grid of target × time-stop, 1 contract, 1000pt stop, one position at a time, total $:

| target ↓ / time → | 30m | 45m | 60m | 90m | 120m | 180m | **none** |
|---|---|---|---|---|---|---|---|
| 100 | −5,044 | −2,370 | −2,125 | −753 | −1,738 | −3,110 | +351 |
| 150 | −6,609 | −4,074 | −3,193 | −2,574 | −3,025 | −4,389 | +42 |
| 200 | −6,458 | −4,007 | −3,706 | −2,017 | −3,514 | −3,685 | +1,105 |
| **none** | −4,812 | −2,203 | −1,437 | +385 | −989 | −1,461 | **+2,202** |

**Every time-stop loses, and every target loses.** The best cell in the entire grid is *no
target and no time rule* — hold to the close.

Control, with the trade set fixed at all 944 (so a freed slot cannot let extra trades in):
hold-to-EOD +$2,874 · 30m −$4,512 · 60m −$1,382 · 90m −$385 · 180m −$1,405. **The damage is
the rule itself, not the extra trades.**

This is a direct answer to "if it hasn't hit the target by X, the entry was wrong that day":
**the data says it wasn't.** Trades still underwater at 30–120 minutes recover by the close
often enough that cutting them is expensive. Clock-time versions are worse still (flat by
12:00 = −$12,139; by 13:00 = −$14,489).

### The one cell that looked good is a spike

"Cut at 60m only if worse than −100" returns +$6,378 ($/DD 1.45). Its neighbourhood:

| worse than ↓ / min → | 30m | 45m | **60m** | 75m | 90m | 120m |
|---|---|---|---|---|---|---|
| −50 | −381 | +4,226 | +1,158 | −613 | −2,560 | +149 |
| −75 | −3,169 | +3,089 | +5,925 | +1,759 | +308 | +1,739 |
| **−100** | −1,828 | +2,828 | **+6,378** | +2,066 | +1,941 | +2,640 |
| −150 | +1,717 | +1,060 | +4,489 | +2,335 | +3,039 | +1,363 |

Adjacent cells fall by half or more in both directions and the surface flips sign. It is a
fitted spike, not a plateau — the exact failure mode this codebase has recorded for A_EXT,
GRADE-for-LONG, H6 and the 11am block. **And every cell in that table is 3/6 green.** The
rule moves the magnitude and never the regime dependence.

## Narrower beats wider — the wide stop is decoration

Hold to EOD, no target, no time rule — the best geometry — swept across stop width:

| stop | $/contract | total | maxDD | $/DD | worst day | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1000pt | $2,000 | +2,202 | −6,427 | 0.34 | **−2,007** | −1,454 | +2,440 | −1,530 | +825 | −2,870 | +4,790 |
| 850pt | $1,700 | +2,802 | −5,827 | 0.48 | −1,707 | −1,454 | +2,440 | −1,530 | +825 | −2,270 | +4,790 |
| **625pt** | $1,250 | **+3,702** | −5,005 | **0.74** | −1,257 | −1,454 | +2,440 | −1,530 | +825 | −1,370 | +4,790 |
| 500pt | $1,000 | +3,198 | −5,542 | 0.58 | −1,479 | −1,454 | +2,440 | −1,530 | +655 | −1,815 | +4,901 |
| 200pt (live) | $400 | +1,346 | −3,796 | 0.35 | −814 | −1,312 | −8 | −1,982 | +1,134 | +118 | +3,396 |

The 1000pt stop is hit by **0.2% of trades — 1 in 472**. The per-year columns for 1000 /
850 / 625 differ *only* in 2024–25, which is the tell: widening past ~625 adds nothing but
extra loss on the handful of trades that reach it. **The widest stop is the worst member of
its own family.** The user's intuition that a wide stop is rarely hit is confirmed; the
inference that it therefore helps is not.

Best available width is **625pt — which is exactly the IBKR soft DLL.** That is a coincidence
worth noticing, not a reason to trust it.

## London: the design loses on every variant

Same design on London's own entries (2,496 champion entries, flat 09:00 ET). London's MFE
median is **40 points**; the 1000pt stop is touched by **0.0% of paths.**

| config | n | total | maxDD | $/DD | grn |
|---|---|---|---|---|---|
| wide 1000 · hold to 09:00 | 1260 | −2,619 | −8,058 | −0.33 | 2/6 |
| 625 · hold to 09:00 | 1260 | −2,619 | −8,058 | −0.33 | 2/6 |
| 1000 · target 150 | 1262 | −5,037 | −9,474 | −0.53 | 1/6 |
| 1000 · time 60m | 1317 | −4,865 | −5,578 | −0.87 | 1/6 |
| 1000 · cut 60m if worse −100 | 1262 | −2,252 | −6,881 | −0.33 | 2/6 |

Consistent with the Jul 18 rebuild: London's edge *is* the BE=0.10 armour, and a wide stop
removes it. A 1000pt brake in a session with a 40pt median excursion is not a stop.

**Separate finding, and it matters:** `london_v2_sim` charges **no commission and no
slippage** — `pnl = sign × (exit − entry) × DOLLARS_PER_PT`, verified in source. The
champion's +$3,851 is 2,496 trades at **+$1.54 each**:

| champion, less… | net |
|---|---|
| commission only ($1.24) | +$756 |
| 1pt slippage + commission ($3.24) | **−$4,236** |
| this lab's $6 friction + commission ($7.24) | **−$14,220** |

London does not survive its own transaction costs at 5.5yr scope. That is independent of
this what-if and should be checked before London is treated as a live book at all.

## Under the real account rules

Per-account daily caps barely bite — with hold-to-EOD and one position at a time the book
rarely gets past 2 trades/day anyway, so IBKR (5/day) and TC (2/day) give identical results.

TopStep $50k gauntlet (DLL $1,000 halt, $2,000 trailing MLL, $3,000 target, 60-day bootstrap):

| config | P(pass) | P(blow) | worst day | single-trade risk |
|---|---|---|---|---|
| live exit stack (reference) | 2.6% | 20.1% | −814 | $400 |
| wide 1000 · hold EOD | 9.9% | **40.8%** | −1,000 | $2,000 |
| 625 · hold EOD | 9.9% | **40.8%** | −1,000 | $1,250 |
| 625 · cut 60m if worse −100 | 10.3% | 34.8% | −1,000 | $1,250 |
| 500 · hold EOD | 9.7% | **42.6%** | −1,000 | $1,000 |

The wide-stop design roughly **quadruples P(pass) and doubles P(blow)**. You still blow
3.5–4× more often than you pass. This is the fifth independent route to the same TopStep
verdict.

## What holds up, and what does not

| component of the design | verdict |
|---|---|
| 1 contract at a time | fine — and forced anyway, since cc drops to 1 at a wide stop |
| existing entries, no day filter | keeps the book's known regime dependence: **3/6 green at best in every variant tested** |
| 1000pt stop | **worst member of its own family.** 625pt beats it on every metric; the stop fires 1 in 472 and does no work |
| target at 150/200 | **not supported** — median MFE is 59pts; +150 is a 15% event, +200 an 8% event; every target row loses to no-target |
| time-based "book the loss" | **actively harmful** — worst lever tested; underwater trades recover by the close often enough that cutting is expensive. The one good cell is a spike |
| same design on London | **loses on every variant**, and London's own champion does not survive real costs |
| risk ≤ MLL | **not met at 1000pt** — 200% of the DLL on both accounts |

The closest thing to a workable version of the idea is **625pt stop, no target, no time
rule, hold to the close, 1 contract**: +$3,702, maxDD −$5,005, $/DD 0.74, worst day −$1,257,
green 3/6 (2021 −1,454 · 2022 +2,440 · 2023 −1,530 · 2024 +825 · 2025 −1,370 · 2026 +4,790).
That beats the live stack's +$938 at 1 contract — but it is still a book that loses in half
its years and whose worst day exceeds the IBKR DLL.

**Recommendation: do not wire this.** The design does not fail on tuning, it fails on the
tape — the entries do not produce the excursions the target needs, and the time rule cuts
trades that recover. The measured obstacle remains the one the Aug 16 bench named: this book
has no all-weather edge, and no exit geometry repairs that. If any single piece is worth
carrying forward it is the observation that **625pt is a better stop than 200pt for a
hold-to-close book** — which is a different design from the live one and would need its own
validation, not a parameter change.
