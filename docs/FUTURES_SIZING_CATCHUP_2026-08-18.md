# Futures / sizing thread — catch-up pack (written Aug 18 2026)

Paste the **PROMPT** block below into a new session. Everything after it is the detail that
prompt points at, so you can hand the session either the short version or the whole file.

---

## PROMPT (paste this)

> Continue the futures sizing research from Aug 18 2026. Read the memory
> `futures-condition-lab-aug18` and `futures-live-duplicate-entries-aug18` first, plus
> `docs/FUTURES_SIZING_CATCHUP_2026-08-18.md`. Do NOT re-run the labs that already answered —
> read their verdicts.
>
> Where we are: after 11 labs, everything sorts into SELECTION works / REACTION fails. The best
> configuration found is: existing entry signals, LONG only, on days where daily close > MA50 and
> MA50 rising, target +200pt, stop −1000pt, exit 15:10, contracts = $1,000 risk ÷ (day ATR × $2)
> capped 1-3. That gives +$12,233 over 5.5yr, maxDD −$3,636, $/DD 3.36, green in all 6 years, and
> needs roughly $9-10k of capital at 1 contract. It is PROVISIONAL: it is a barrier simulation on
> existing entries that replaces the live trail/partial/reversal-exit rather than modelling them,
> and the filter was chosen on the same data.
>
> My thesis is that SIZING is the remaining lever. Next steps I want:
> 1. Full-pipeline sim run of that exact config (real exit stack, not a barrier sim) — does it hold?
> 2. Walk-forward the ATR-normalised sizing (fit the risk-per-trade on 2021-23, test 2024-26).
> 3. Test whether risk-per-trade should scale with anything else that survived (room forecast is
>    symmetric so it failed as a GATE — does it work as a SIZE input?).
> 4. Decide: wire to IBKR futures_trader.py as a forward-validated shadow, or keep researching.
>
> Reuse the caches in `futures/factory/` (`_day_table.csv`, `_mom_2021-06-01_2026-08-14.csv`,
> `_mtf_trades.csv`) — the 5.5yr sim takes ~45 min. Report per-year always, never just totals.

---

## The one-paragraph state

We have no fundable TopStep intraday edge and that is now settled by arithmetic, not opinion (a
1000pt stop is $2,000 = the entire $2,000 trailing MLL). What we DO have is an IBKR-side
configuration that is green in all six years on ~$10k of capital, built from three things that
each survived independently: a day-level daily-MA50 LONG filter, a wide brake with a small
target, and ATR-normalised position sizing. The open question is whether it survives the real
exit stack and a walk-forward on the sizing parameter.

## What is settled — do not re-litigate without a NEW mechanism

| question | verdict | evidence |
|---|---|---|
| Can we forecast day character? | Only MAGNITUDE (room), never direction or trend-quality | IC +0.41 walk-forward vs ≤ noise floor |
| Does room convert to money? | **No — it is symmetric** | failed twice: Room Gate, room-gated swing |
| Fleet of condition-specific engines? | **No** — no archetype owns a weather | 3 archetypes × bucket × year |
| Intraday mean reversion? | **Anti-edge** | −$8.87/trade; 2nd independent proof |
| 1-min / 30-sec reversal warning? | **No** — all 8 at the 33% base rate | 214,356 bars; early and accurate are opposites |
| Flip to short on the reversal? | **No** — net after fire ≈ 0 pts | same test |
| Equity breadth as a lead? | Significant but 4-6 pts/hr vs 3pt friction | 43,267 joined stamps |
| 1:1 scalping? | **No** — friction is 32% of a 5-min move; our entries are 47% at ±40 | breakout entries have negative short-horizon edge |
| Wider fixed stops? | **Worse** (200 > 300 > 400) | full-pipeline sweep |
| Lower timeframe confirmation? | **Worthless** — big losers separate on NO micro feature | −0.10 / −0.03 / −0.10 sd |
| "Wait for a pullback"? | **Backwards** — +$3,353 → −$4,798 | entering at the extreme IS the edge |
| Daily-trend filter on EQUITY? | **Does not transfer** | 8,363 Wave Rider trades, inverts per-year |

## What is open

1. **Full-pipeline validation** of the best config (the barrier sim replaces the exit stack).
2. **Walk-forward the sizing parameter** ($1,000 risk-per-trade was picked from the full sample).
3. **Room as a SIZE input** rather than a gate — untested; it failed as a gate because it is
   symmetric, but symmetry does not disqualify it from scaling exposure.
4. **Live duplicate entries**: live runs ~2× the sim (MAX_OPEN_TRADES=2, no post-entry cooldown).
   User's call = leave sizing as is. But every backtest still understates live risk ~2× — the
   sim and the live book are different systems, and that gap is unresolved.
5. **Equity `observe_only` fix needs live confirmation** — watch the first WEAK/CHOPPY scan for
   the `👁️ OBSERVE-ONLY` log line and fresh scan_log rows.
6. **`equity_replay.py` is stale** (still runs decommissioned Fish Finder) — rewire before trusting.

## Key numbers worth carrying in your head

- MNQ: $2/point. Margin ~$4,374 intraday / ~$6,249 overnight (Aug 3 figures — verify live).
- MNQ ATR: 193pts (2021) → **459pts (2026)**. The fixed 200pt stop decayed 1.04 ATR → 0.44 ATR.
- Median move: 10pts/5min, 14/10min, 16/15min, 24/30min. Friction $6/contract = 3pts.
- Base rate: a random minute is followed by ≥60pts adverse within 60min **33%** of the time.
- The 1000pt stop fires **1 in 472 trades**; 91% of exits are EOD.
- Trend strength is an **inverted U**: distance above MA50 by quartile → +4.0 / +9.7 / **+21.0** /
  +4.1 pts. Moderate trend is best; far-extended is as bad as barely-above. Never size up on it.

## Method rules this thread earned the hard way

- **Always report per-year, never just totals.** Several findings inverted per-year.
- **Print the dollar spread next to any IC.** With n=43k, significance is cheap and meaningless.
- **Day-level filters are pipeline-safe; trade-level filters are not** (`_run_scenario` resets per
  day). That is why the MA50 filter survives where A_EXT / GRADE-for-LONG / H6 / the 11am block died.
- **A compelling live anecdote is not evidence.** Aug 17 would have been blocked by an equity
  filter that the full 8,363-trade history says is worthless.
