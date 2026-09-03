# The one-shot design — 1 contract, wide stop, no trail, long only (Aug 19 2026)

Built iteratively with the user across one session. Each step corrected the previous one.
Labs: `futures/factory/livestop_lab.py`, `_stoprun.py`, `_slowtrail.py`, `patient_lab.py`.
**Nothing wired.** All numbers 1 contract, $6/contract friction + commission unless stated.

## The design, and what each piece turned out to be worth

| step | the idea | verdict |
|---|---|---|
| 1 contract | cap size | **the single biggest risk lever** — not the stop |
| one at a time | sequential, re-enter after close | already what `sim_replay` models; live does **not** |
| full-MLL stop (1000pt) | absorb turbulence | **never fires** — pure insurance, and 1200 ≡ 1000 exactly |
| slow the trail | let 300-500pt days run | **right, but only at zero** — partial slowing is worse than live |
| entry accuracy | "if we're right, time rewards us" | **true for LONG only**; shorts are the drag |

## The backup IBKR stop is not a separate wide brake

`futures_trader.py:1600` places it at `sl` — the *same* level as the software stop — and
`_update_backup_stop()` drags it up to each BE/trail level. It is a hardware mirror. On live
data: 58 of 163 exits (36%) came through it, max distance from entry when it filled was
**200pt exactly**; 40 fills were *above* entry (trail locks worth +$2,707), and 15 fills at
100-205pt were the real 200pt stop (−$4,038). **There is nothing separate to widen** —
"widen the backup" is `BASE_STOP_PTS`.
*Doc drift to fix:* the comment at line 1601 says the backup is "never moved". It is moved,
at lines 1750/1758/1766/1805.

## Stop width: wider is worse, and past 1000 it does nothing

Live code, only `BASE_STOP_PTS` changed. `calc_contracts` derives size from the stop, so
500pt→2 contracts and 1000pt→1 contract are **the same $2,000 risk budget**:

| 2026 only, IBKR | risk/trade | total | maxDD | worst day | TC blows |
|---|---|---|---|---|---|
| 200pt × 2c — live today | $800 | +8,421 | −2,251 | −813 | 1 |
| **200pt × 1c — control** | $400 | +5,921 | −1,450 | −480 | 0 |
| 500pt × 2c | $2,000 | +7,463 | −2,791 | −1,486 | 1 |
| 1000pt × 1c | $2,000 | +5,168 | −1,751 | −1,101 | 0 |
| 1200pt × 1c | $2,400 | +5,168 | −1,751 | −1,101 | 0 |

**1200 ≡ 1000, byte for byte** (same total, same maxDD, same worst day, same 36 stop-hits) —
between those widths the stop is never touched. Same over full history (+$3,933 vs +$4,058,
differing only in 2025). Initial-stop hits by width: 200pt → 49, 500pt → 37, 1000pt → 36,
1200pt → 36. Diminishing to nothing.

⚠️ **At 1200pt the live code silently produces ZERO trades.** `MIN_RR` is 1.4 and the target
backstop is 1500, so 1500/1200 = 1.25 rejects every entry. Any stop > 1071pt needs the target
scaled (runs here use 1.5× stop to match the 1000pt run's RR).

Full history, IBKR: 200pt **+3,353** · 500pt +2,586 · 1000pt **−1,142**. The wide stop loses
money outright across 2021-2026.

## "Slow the trail" — right idea, and it only works at zero

Live trail with `REGIME_AWARE_EXITS`: CHOPPY/QUIET lock BE at +90 then tighten to a **35pt
gap** once +200 is reached (TRENDING gets 550/110). A day wanting to run 400 is cut at ~215.

2026, 1 contract, 1000pt stop:

| config | n | WR | total | maxDD | worst day | avg win | best trade |
|---|---|---|---|---|---|---|---|
| live trail | 123 | 69% | +5,168 | −1,751 | −1,101 | +151 | +749 |
| trail 2× slower | 117 | 64% | +4,231 | −1,751 | −1,101 | +172 | +749 |
| trail 3× slower | 117 | 64% | +4,147 | −1,751 | −1,101 | +171 | +749 |
| 2× · no rev-exit · no no-move | 96 | 65% | +6,106 | −1,268 | −1,101 | +246 | +1,306 |
| **no trail · no rev · no no-move** | 93 | 63% | **+6,990** | **−1,268** | −1,101 | **+260** | **+1,306** |

**Non-monotonic: partially slowing the trail is worse than the live trail.** Only removing it
entirely wins. Average win rises +151 → +260 (+73%), best trade +749 → +1,306, win rate falls
69% → 63%, and drawdown *improves* 29%. `$/DD` 5.51 vs live-today's 3.74.

With no trail the 2026 exit reasons are `eod: 88, vwap_cross: 7` — **the 1000pt stop never
fires once.** The −$1,101 worst day is an EOD loss, not a stop-out.

### Why the trail was costing so much

2026 give-back, 1000pt stop with the live trail (118 trades replayed on 1-min bars):

| MFE bucket | n | % | avg MFE | captured | capture% | if held to close |
|---|---|---|---|---|---|---|
| 0-100 | 53 | 45% | 49 | −54 | — | −66 |
| 100-200 | 40 | 34% | 144 | +66 | 46% | +51 |
| 200-300 | 12 | 10% | 237 | +85 | 36% | +147 |
| 300-500 | 9 | 8% | 359 | +158 | 44% | +224 |
| **500+** | 2 | 2% | 547 | **+53** | **10%** | **+503** |

11 of 118 trades (9%) reach +300. Their peaks total 4,328pts ($8,656); the trail kept 1,531
($3,061); **holding to the close would have kept 3,017 ($6,034)**. Whole-book capture is 16%.
The largest single exit reason is **`no_move` — 46 of 118 (39%)**, the 90-minute rule firing
on exactly the trades that need time.

## Entry accuracy — the question the no-trail design rests on

**Our own trades** (136 replayable, held from real entry to 15:10, 1c): right **57%**, avg
winner +206, avg loser −202 (ratio 1.02), breakeven 49% → expectancy **+29pts = +$51/trade**.
By setup: VWAP_SHORT 76% (+$5,804) · PM_LONG 71% (+$4,934) · ORB_LONG 100% n=6 · **ORB_SHORT
26% (−$3,676)** · **VWAP_LONG 48%, n=50, (−$5,533)**. By month: Jun 44%, Jul 56%, **Aug 83%
(+$7,379)** — essentially all of it is August. Suggestive, not established.

**5.5yr sim** (944 entries, held to close): right **54%** vs a **51%** breakeven — thin, and
green only 3/6 years. Decisive split: **LONG 58% (+$8,356) vs SHORT 46% (−$5,426).**

Green rate by setup by year — one thing is stable:

| setup | n | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | $ 1c |
|---|---|---|---|---|---|---|---|---|
| **PM_LONG** | 497 | 54 | 60 | 59 | 57 | 56 | 66 | **+8,053** |
| ORB_SHORT | 314 | 26 | 58 | 39 | 36 | 50 | 51 | −5,402 |
| ORB_LONG | 51 | 33 | 50 | 58 | 56 | 50 | 43 | −1,235 |

**PM_LONG is right 54-66% in every single year over 497 trades** — the most stable number this
program has produced.

## The result: dropping shorts removes every blow-up

Hold to close, 1 contract, 1000pt stop, one at a time, 5.5yr:

| book | n | WR | total | maxDD | worst day | IBKR blows | **TC blows** | green |
|---|---|---|---|---|---|---|---|---|
| everything (as now) | 748 | 52% | +2,202 | −6,427 | −2,007 | 0 | **5** | 3/6 |
| LONG only | 481 | 56% | +4,754 | −3,245 | −2,007 | 0 | **0** | 4/6 |
| drop ORB_SHORT | 513 | 56% | **+5,056** | −4,283 | −2,007 | 0 | **0** | **5/6** |
| **PM_LONG only** | 403 | 57% | +4,631 | **−3,279** | −2,007 | 0 | **0** | **5/6** |
| PM_LONG + VWAP_LONG | 431 | 56% | +4,872 | −3,401 | −2,007 | 0 | 0 | 5/6 |

**All five TC blow-ups came from the short side, not from the wide stop.** `PM_LONG only` is
the first config in this program that is green 5/6 **and** blow-free on both accounts
(2021 +275 · 2022 +1,462 · 2023 +681 · 2024 +429 · **2025 −1,382** · 2026 +3,165).

## Live sequential counterfactual (our own trades, Jun 5 – Aug 17)

| IBKR | trades | contracts | P&L | worst day | maxDD | blows |
|---|---|---|---|---|---|---|
| as traded (2 open × up to 2c) | 70 | **121** | +$2,822 | −$1,279 | −$2,260 | 0 |
| one at a time, 1 contract | 39 | **39** | +$1,372 | **−$406** | **−$579** | 0 |

Exposure −3.1×, worst day −3.2×, drawdown −3.9×, per-contract P&L $23 → $35. TC goes from
+$287 to −$666. **81% of IBKR days and 95% of TC days become single-trade days** — one position
at a time collapses this into a one-trade-per-day book; the second trade fired 7 times in 2.5
months. IBKR's +$1,372 still includes +$1,076 of manual `FUT CLOSE`; automated-only is **+$297**
against an as-traded automated baseline of −$511.

## Open caveats

- Everything above is one favourable regime (2026) plus a 5.5yr sim whose live counterpart
  fires a very different setup mix — VWAP_LONG is 3.5% of sim trades but **37% of live trades**.
- Worst day is **−$2,007** in every 1000pt variant. It never actually blew an account in the
  historical ordering, but on TC that is the entire MLL in one fill; the bootstrap gauntlet
  (path-shuffled) still showed high blow rates. Survival here is partly luck of sequence.
- 2025 is negative in every variant tested.
- Full-history runs of the no-trail config through the real 1000pt pipeline were still running
  at write time (`_slow_1000_m999_rev0_nm0.csv`); numbers above use the 200pt-pipeline entry set.
