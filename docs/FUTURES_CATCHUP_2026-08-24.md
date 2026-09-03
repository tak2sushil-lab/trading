# Futures strategy thread — catch-up pack (written Aug 24 2026)

Supersedes `docs/FUTURES_SIZING_CATCHUP_2026-08-18.md`. Paste the PROMPT block into a new
session. Everything below it is the detail that prompt points at.

---

## PROMPT (paste this)

> Continue the futures strategy work. Read `docs/FUTURES_CATCHUP_2026-08-24.md` first, then the
> memories `futures-one-shot-design-aug19`, `futures-wide-stop-whatif-aug19`,
> `futures-patient-engine-aug19`, `futures-sizing-verdict-aug19` and
> `futures-live-duplicate-entries-aug18`. Do NOT re-run anything those already answered — read
> the verdicts. Reuse the caches in `futures/factory/` (a 5.5yr `sim_replay` run is ~45 min;
> a single-month window is ~1 min).
>
> Where we are: over several sessions we tested a redesign of the NY futures book. The
> pipeline-confirmed best config is **1000pt stop / 1 contract / one position at a time / no
> trail / no rev-exit / no no-move / LONG only**: 5.5yr +$5,520 vs the live book's +$3,353,
> maxDD −$3,253 vs −$7,715, TC blow-ups 6 → 0, green 4/6 years. It trades ~1.5x/week.
>
> The single architectural fact that governs everything: **`grade_entry` is ONE additive score
> per side, and `setup_name` is only a naming-priority tag applied after the entry decision.**
> There are not 9 strategies — there is one LONG and one SHORT strategy. Proof: zeroing
> `sig['orb_bear']` removed only 9 of 276 shorts; 235 relabelled to VWAP_SHORT. So you can only
> remove a SIGNAL, a SIDE, or raise the grade threshold.
>
> My decision is still open. The change with the strongest independent evidence is LONG-ONLY.
> Nothing has been wired and no service restarted. Start by confirming nothing has changed in
> live code, then help me decide whether to ship long-only as a log-only shadow or live.
>
> Always report per year, never totals alone. Always say whether a benchmark is deployable.

---

## The one-paragraph state

The live NY automated book is **losing** (−$3,080 over 149 trades since Jun 5 2026; the
positive headline is the user's manual `FUT CLOSE` closes, +$4,209). Research across several
sessions converged on a redesign whose pieces were each tested and mostly corrected. What
survived: **1 contract, one position at a time, no trail, long only.** What did not: the wide
stop as a profit lever (it is inert insurance), ATR-normalised sizing, room-as-size, partial
trail-slowing, and time-based loss-booking. The open decision is whether to ship long-only.

## THE GOVERNING CODE FACT — read this before proposing any change

`grade_entry()` builds **one additive score per side**. The SHORT branch requires `any()` of
`[orb_bear, vwap_rejection, momentum_bear, open_play_bear, pm_bear]` then adds
20/15/10/10/25 points; A+ is ≥80, so a qualifying entry needs several signals co-firing.
`setup_name()` then tags the trade with whichever signal ranks first in a fixed priority list.

**The setup name is a LABEL, not a strategy.** Zeroing `sig['orb_bear']` removed only 9 of 276
shorts — 235 simply relabelled to VWAP_SHORT (live config: VWAP_SHORT 27 → 298). "Drop
ORB_SHORT" is not a change that exists. The three real levers are: remove a **signal** (changes
grading for that entire side), remove a **side**, or raise the **grade threshold**.

## WHAT A CHANGE TOUCHES (asked and answered)

| edit | affects |
|---|---|
| `futures/futures_trader.py` | **IBKR NY only** |
| `futures/tc_trader.py` | **TC NY only** |
| `futures/london_trader.py` | **both accounts' London** (threaded into each process) |
| `futures/sim_replay.py` | the backtest mirror only |

IBKR NY and TC NY are **duplicated code, not shared** — `get_signals` diffs to a single dead
variable (`last3v`, assigned and never used in IBKR). **Any real change is 3 files.** London is
a separate single IB-range-break signal and is untouched by any NY change. Elephant
(`_scan_elephant`) is IBKR-only and already PARKED (`ELEPHANT_ENABLED=False`).

## PIPELINE-CONFIRMED RESULTS (5.5yr, real `_run_scenario`, $6/contract friction)

| config | n | total | maxDD | worstDay | TC blows | green | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **LIVE TODAY** (200pt, 2c, 2 open) | 949 | +3,353 | −7,715 | −814 | **6** | 2/6 | −1,138 | −1,615 | −1,314 | −2,480 | +1,480 | +8,421 |
| orb_bear off (RELABELS) | 925 | +3,784 | −7,330 | −814 | 5 | 2/6 | −1,242 | −1,402 | −1,332 | −2,236 | +1,892 | +8,104 |
| LONG ONLY, live exits | 600 | +4,929 | −4,029 | −814 | 2 | 4/6 | +152 | −2,857 | +1,016 | −539 | +1,450 | +5,707 |
| 1000pt/1c/no-trail, everything | 756 | +5,766 | −6,210 | −2,007 | 3 | 3/6 | −1,135 | +3,596 | −987 | +633 | −3,330 | +6,990 |
| orb_bear off + no-trail (RELABELS) | 748 | +6,589 | −5,846 | −2,007 | 1 | 3/6 | −1,385 | +4,126 | −1,112 | +748 | −3,167 | +7,379 |
| **★ 1000pt/1c/no-trail/LONG ONLY** | **493** | **+5,520** | **−3,253** | −2,007 | **0** | **4/6** | −59 | +1,568 | +1,375 | +249 | −1,223 | +3,610 |

★ vs live today: **+65% P&L, −58% drawdown, 6 blow-ups → 0.** Frame-filter estimates from
earlier sessions were ~15% optimistic; these are the real numbers.

## WHAT IS SETTLED — do not re-litigate without a NEW mechanism

| question | verdict |
|---|---|
| Wider stop as a profit lever? | **No.** 1000pt loses outright over 5.5yr (−$1,142). 625pt > 850 > 1000 on every metric |
| Does 1200pt help? | **No — 1200 ≡ 1000 byte-for-byte.** Stop never touched between them. Also **>1071pt yields ZERO trades** (MIN_RR 1.4 vs the 1500 target) unless the target is scaled |
| Is the wide stop doing work? | **No** — fires 2 times in 756 trades (1 in 378). Pure insurance |
| Slow the trail? | **Right idea, only at ZERO.** Non-monotonic: 2× and 3× slower are WORSE than the live trail |
| Time-based "book the loss"? | **No** — worst lever tested; underwater trades recover by the close. Proven on a fixed 944-trade control |
| ATR-normalised sizing? | **No** — its gain is the ATR 189→455 time trend; within-year terciles are worse than flat |
| Room forecast as a size input? | **No** — third independent rejection of room |
| "Drop ORB_SHORT"? | **Not implementable** — the label relabels (see governing code fact) |
| Is VWAP_LONG a killer? | **No** — best $/trade in the 5.5yr sim (+$26). Only the 2.5-month live sample says otherwise |
| Does the conviction (hero) ladder carry signal? | Weak — beats random allocation on the filtered book (p≈0.02) but is noise on the full book (p≈0.15) |
| Is "one at a time" already modelled? | **Yes in the sim** (`sim_replay.py:1127`); **NO in live** (`MAX_OPEN_TRADES=2`, cooldown is post-EXIT only) |

## LIVE STATE through 2026-08-21 (167 closed trades, 45 trading days)

Automated book **−$3,080** / 149 trades. IBKR automated −$1,787 (manual +$3,333); TC automated
−$1,292 (manual +$876). **SHORT n=57 −$2,198 · LONG n=92 −$882.**

**August 2026, automated only:** 20 trades, **−$2,617** (IBKR −$1,495, TC −$1,122).
LONG n=12 −$382 · **SHORT n=8 −$2,235 — 85% of the month's loss is the short side.**
The new config replayed over the same 15 trading days: **6 trades, +$232** (IBKR swing +$1,727).

**Aug 21 (Friday) is the worst live day on record: −$1,979.** Four trades, all SHORT, all
ORB_SHORT, entries 10:30:50 / 10:31:50 (TC) and 10:31:50 / 10:32:51 (IBKR) — a duplicate pair
on each account. Both hit the daily circuit breaker; IBKR's −$1,270 breached the $1,250 soft
DLL. Under long-only those four trades could not exist; the replay takes one LONG for −$168.
**All five worst live days are 4-5 trade duplicate clusters.** But note Aug 7's cluster MADE
+$1,168 — duplicates are a variance amplifier, not a uniform loss.

## TC COMPARABILITY — count from here, not from June

| commit | date | change |
|---|---|---|
| `72b3bb5` | **2026-07-25** | TC entry logic aligned to the NY stack |
| `a6fd51a` | **2026-08-09** | TC contract sizing fixed (was hardcoded to 1) |
| `463680f` | **2026-08-09** | TC `MAX_DAILY_TRADES` 2 → 5 |

TC is entry-comparable only from **Jul 25** and fully comparable from **Aug 9** — about
**7 trading days**. Any TC number spanning June onward blends three different systems.

## TRADE FREQUENCY of the proposed config — plan around this

**1.5 trades per week**, stable per year (2021 1.3 · 2022 1.4 · 2023 1.7 · 2024 1.6 ·
2025 1.5 · 2026 1.6). ~6-7 trades a month. A losing month is statistically meaningless at this
rate; forward validation needs roughly a year (block-bootstrap: p10 stays negative until ~12
months).

## OPEN DECISIONS

1. **Ship long-only?** Strongest independent evidence (85% of August's automated loss is
   shorts; 5.5yr SHORT −$5,426 vs LONG +$8,356 held-to-close). One-line change per file, the
   same pattern already used for `pm_bear`. Log-only shadow vs live is undecided.
2. **The exit rebuild** (1000pt / no trail / no rev-exit / no no-move) — bigger change, argued
   from 5.5yr history only; recent live data neither supports nor contradicts it.
3. **Duplicate entries** — `MAX_OPEN_TRADES` 2→1 and/or a post-ENTRY cooldown. A genuine bug,
   not a strategy opinion. Cost $1,979 on Aug 21 alone. Never root-caused despite the parity
   cop flagging it twice (Jul 15, Jul 20).
4. **Unexplained**: live fires a very different setup mix than the sim — VWAP_LONG is 5% of sim
   trades but 32% of live trades in the same post-fix window (1 vs 26 trades in 17 days). Until
   this is root-caused, live setup-level P&L cannot be used to judge a setup.

## HONEST CAVEATS ON THE PROPOSED CONFIG

- Green **4/6**, not 5/6 (2021 −$59, 2025 −$1,223).
- Worst day **−$2,007** — the whole TC MLL in one fill. It never blew in the historical
  ordering, but the path-shuffled gauntlet still blows often: survival is partly sequence luck.
- "Long only" was chosen partly by looking at this same data.
- 2026 is the book's best year by a wide margin and it dominates every aggregate.

## FILE INVENTORY (`futures/factory/`)

**Labs:** `sizing_lab.py` (13 modes, `--all`) · `wide_stop_lab.py` · `patient_lab.py` (blow
accounting: `blowups`, `daily_with_dll`) · `livestop_lab.py` · `bench.py` (TopStep gauntlet) ·
`conditions.py` · `mtf_lab.py` · `reversal_lab.py` · `scalp_lab.py` (`load_1m`) · `engines.py`.
**Runners:** `_stoprun.py <stop> [start] [maxrisk]` · `_slowtrail.py <stop> <mult> <rev> <nomove> [start]`
· `_longonly.py <stop> <no_trail>` · `_noorbshort.py` · `_replay_window.py <start> <end> <live|new>`.
**Key caches:** `_mom_2021-06-01_2026-08-14.csv` (live config 5.5yr) ·
`_longonly_1000_notrail.csv` (**the ★ config**) · `_slow_1000_m999_rev0_nm0.csv` ·
`_mom_livestop{200,500,1000,1200}[_2026].csv` · `_day_table.csv` · `_london_trades.csv`.
**Archived output:** `_out/`.

## METHOD RULES THIS THREAD EARNED

- **Always report per year.** Several findings inverted per year.
- **Say whether a benchmark is DEPLOYABLE before drawing a verdict.** A "negative alpha vs the
  tide" reading was a tautology — 79% of the day's move happens before the book enters.
- **A ratio can improve because its denominator happened not to move** (n=57 never landed in the
  worst drawdown path) — permutation-test it.
- **Independent-per-trade replays silently double-book the position slot.**
- **A late-activating trail must be clamped to the current price** — an unclamped one inflated a
  result 10× (+$22,705 → +$705); 21% of activations rested above the market.
- **Frame filters are an upper bound** — they do not refill freed slots. Confirm in the pipeline.
