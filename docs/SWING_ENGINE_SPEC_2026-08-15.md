# Swing Catalyst Engine — Build Spec (v1)
**Date:** Aug 15 2026 · **Status:** SPEC — nothing wired · **Author:** diagnosis + isolation study Aug 14–15 2026
**Decision owner:** Sushil · **Target:** paper-live Mon Aug 17 as *edge #1* of a multi-edge roadmap

---

## 0. One-sentence thesis
A stock that pops ≥3% on a real catalyst **keeps drifting in that direction for days** (the post-catalyst / post-earnings drift anomaly — one of the most replicated effects in finance). We currently enter this correctly and then **force-close it the same afternoon**, at the one horizon where the edge is ~zero and costs win. **Same entries, held ~3 days = the fix.**

## 1. Why this exists (the reframe)
| | Current intraday system | Swing engine |
|---|---|---|
| Holds | minutes–hours, flat by 3:45pm | ~3 trading days, overnight |
| Exit driver | 13-mechanism intraday stack + T+5 BE stop | fixed hold + wide stop + earnings-exit |
| Edge captured | ~0% (front of the drift) → negative after costs | the multi-day drift |
| 2026 result | **−$655 live** | **+$8,568 backtest (honest fills)** |

The current exit stack is *architecturally wrong* for this edge. This is a rebuild of the exit path, not a tweak. The **entry** logic is largely reused.

## 2. The edge — evidence (what's validated, honestly)
Built from **14,571 real catalyst events, 2024–2026, 290 symbols**, 5-min bars. Designed on 2024–H1'25, forward-tested on sealed H2'25 + 2026.

- **Robust across all 3 periods incl. the fade regime** (IS +$28.9k / H2'25 +$3.5k / 2026 +$8.6k, honest gap-down fills, cap 5, $2k, $2 cost).
- **Broad, not a mirage:** 2026 = 52% win, **top-5 trades only 8% of profit**, works in every price bucket, 280 distinct names. (Contrast: May's live "edge" was 70% from 3 trades.)
- **Known anomaly** — finding it is confirmatory, not a fluke.

**Honest ceiling:** magnitude is optimistic — universe survivorship (these names were partly chosen *for* moving), idealized fills, no catastrophic-earnings-gap modeling. Real ≈ lower; **halve it and it's still a real edge.** The Monday paper run is the true forward test, not proof.

## 3. Signal spec (entry) — every gate justified
Reuse the existing catalyst detection. A candidate qualifies when, at the 10:00 ET scan:

| Gate | Value | Why (from data) |
|---|---|---|
| Move | day change ≥ **+3%** vs prior close | The catalyst population; below this there's no event |
| Direction | **LONG only** | Down-movers *bounce* — shorting weakness loses every period (−0.15%/2026). No short book. |
| Location | price **≥ opening VWAP** | Weak "holding its gains" filter; mechanistic, cheap. (Note: unfiltered also works — this is a mild quality tilt, not load-bearing) |
| Earnings | **not within [N] days** (see §12) | Removes the fatal single-name gap; earnings held overnight is a coin-flip, not the drift edge |
| Price | $5–$800 | Liquidity / tradability |
| No dupe | not already held | — |

**No fancy multi-feature filter.** Tested and rejected — the tight gap/extension combos and move-size conditioning **failed forward** (looked great in-sample, broke in 2026). Less fitting = more robust.

**Selection when >5 qualify:** rank is low-impact (3-day holds mean ~1.7 entries/day, slots rarely contested). Default: take first-to-trigger; ranking is a §12 open item, not a core parameter.

## 4. Execution spec (the new exit engine) — every parameter justified
| Parameter | Value | Why (from data, not a round number) |
|---|---|---|
| **Hold** | **3 trading days** (exit at 3:45pm on entry_day+3) | Only hold **robustly positive in ALL regimes** incl. the fade (1–2 day holds collapse in fade regimes; 5-day is fine too — robust zone is 3–5, 3 is primary for least capital lock-up + least market-beta contamination) |
| **Stop** | **8%** (hard, checked live; 10% acceptable) | Set at the **10th percentile of winner drawdown** (winners dip median −2.4%, 1-in-10 past −6.6%). Tighter *cuts winners* — this is exactly why our 5% intraday stop and 0% T+5 BE stop fail |
| Earnings exit | force-close before an earnings date landing inside the hold | Same reason as the entry block |
| EOD | **do NOT close** on days 0–2 | Holding overnight is the whole point |
| T+5 BE stop | **removed** | It's the disease — gives up in 5 min on a 3-day edge |
| Intraday trails/VWAP/no-move | **removed** for swing trades | All calibrated for intraday; they amputate the drift |

**Optional (TEST this weekend, don't assume):** *partial scale-out* — bank 50% at the day-1 close if up ≥ [X]%, ride 50% for 3 days. Captures the front-loaded Day-1 chunk **and** the drift, without predicting anything. Include only if the backtest shows it beats the flat 3-day hold.

## 5. Sizing & portfolio
- **$2,000 × 5 concurrent = full $10k** (owner's choice Aug 15; revisit at real-money door).
- Multi-day holds occupy slots → **~1.7 new entries/day** — this *automatically* fixes the 28-trades/day overtrading.
- **At full size, swing consumes the entire equity pool** → running the current intraday books alongside would starve one or the other. This effectively argues for **swing-only** (see §11 transition).

## 6. Risk architecture (surviving a "new fatal disease" without predicting it)
1. **Per-trade stop** 8% → caps single-name blow-ups.
2. **Earnings-block** → removes the biggest single-name gap tail.
3. **Position sizing** = the drawdown dial. Honest-sim maxDD ≈ **$4–5k (~40–50%)** at full size. This is real — you must be able to sit through a July-type month (−$2.6k) without flinching.
4. **Portfolio circuit breaker** → hard stand-down + alert if account drawdown exceeds [line, §12].
5. **Correlation cap (v2):** avoid 5 slots all in one sector/theme on the same day.

## 7. A day in the life (operational)
- 10:00–3:00 ET: scan finds ≥3% movers holding VWAP, not near earnings → enter up to open slots, market order, tag `SWING_CATALYST`, log all features.
- Continuously: 8% hard-stop check (reuse fast monitor, swing stop only).
- 3:45pm on **entry_day+3**: close at market. (Earlier if stop or pre-earnings.)
- Telegram: entry alert (symbol, move, why), exit alert (P&L, day-of-hold), daily swing summary.
- Dashboard: Swing card — open positions w/ day-of-hold + unrealized, closed P&L, win rate, backtest-vs-live tracker.

## 8. Expected performance (set expectations before wiring)
- **Trades:** ~1.7/day, ~30–40/month.
- **Per trade (honest):** ~+$16–34 net in normal/good regimes; fade months near-flat to negative.
- **Monthly shape (2026 honest sim):** mostly + with occasional real red months (Mar −$1.4k, Jul −$2.6k) fully paid for by green ones (Apr +$5.7k).
- **Drawdown:** ~$4–5k peak-to-trough at full size. Recovers, ends strongly + — but it *will* test your stomach.
- **What "working" looks like week 1–4:** entries flowing at ~1–3/day, win rate 45–55%, per-trade P&L in the +$10–30 band, no runaway losers past the 8% stop. Scored against these numbers weekly.

## 9. Instrumentation & forward validation (know it's real, live)
- New setup_type `SWING_CATALYST`; log entry features + `regime_at_entry` + earnings distance.
- Nightly/weekly **backtest-vs-live scorecard**: is live per-trade P&L, win rate, and drift-capture inside the backtest's confidence band?
- **Live-code-path backtest:** the swing engine must be replayable (equity_replay or a dedicated swing replay) so implementation parity is proven — per CONSTITUTION "sim must match live." The event-study is the *alpha* proof; the replay is the *implementation* proof.
- Sunset/graduation review: **~4 weeks** of forward paper → decide real-money.

## 10. What this does NOT do (scope discipline)
- ❌ No adaptive hold / signal-conditioned dose — **tested, overfits** (the signal→dose relationship inverts between periods; that's the Fish Finder trap).
- ❌ No regime switching / prediction — the regime is unpredictable at our timescale (proven Aug 14).
- ❌ No short book — down-movers bounce.
- ❌ No "pharmacy of many strategies at once" yet — **edge #1 first**, validated, then add uncorrelated edges one brick at a time, each **always-on** and combined by expectancy (the real multi-strat model), never regime-switched.

## 11. Transition (owner decides after this spec)
- **A — Swing-only (recommended):** disable Fish Finder + intraday books Monday; only swing runs. Clean attribution, bleed stops. (Full sizing already implies this — see §5.)
- **B — Parallel:** keep intraday running; confounds attribution + continues the ~$780/mo bleed + competes for the pool.

## 12. Open decisions to close during the build (this weekend)
1. **Earnings block window N** (e.g. skip if earnings within 4 trading days *or* inside the hold). yfinance historical earnings are unreliable → can't cheaply backtest exact impact; apply conservatively as risk control.
2. **Stop 8% vs 10%** — both defensible; 8% = tighter tail, 10% = marginally higher expectancy. Default 8%.
3. **Partial scale-out** — build the backtest toggle; include only if it beats flat 3-day.
4. **Portfolio circuit-breaker line** (e.g. −$1,500 account drawdown → stand down + alert).
5. **Selection ranking** when >5 qualify (low impact) — default first-to-trigger.
6. **Entry price** — market at 10:00-scan trigger vs a limit; default market (matches backtest entry assumption).

## 13. Build & validate timeline
- **Sat–Sun Aug 15–16:** build swing module (entry reuse + new swing monitor + earnings block + logging + dashboard card); build/run the live-code-path swing backtest to confirm parity + re-confirm expected numbers; resolve §12 items with data where possible.
- **Sun eve:** owner reviews built system + backtest report + expected-performance sheet. Transition decision (§11).
- **Mon Aug 17 open:** paper-live as edge #1. Watch first entries closely (flow, sizing, overnight hold behaves, no intraday exit fires on swing trades).
- **~4 weeks:** forward scorecard vs backtest → real-money decision.
