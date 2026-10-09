# Research Registry — every hypothesis we have tested

Started Oct 4 2026. One row per hypothesis, newest at the bottom of each section. **Nothing is deleted:
a rejected idea stays here so it is never re-tested by accident and so the count of tries keeps the
evidence bar honest** (with many tries, require t > 3 before believing a new effect — Harvey, Liu & Zhu 2016).

Verdict key: ✅ real and used · 🟡 real but too small / not shippable · ❌ rejected · 🔬 open

Cost reality (governs every intraday verdict): our round trip is ~10-20 bp (spread + IBKR commission at
$1-3k positions). An edge smaller than that cannot be traded by this account, however real.

## A. Market structure — where the return lives
| # | Hypothesis | Data / method | Result | Verdict |
|---|---|---|---|---|
| A1 | Universe return accrues overnight, not intraday (Lou-Polk-Skouras) | 187k stock-days 2024-26, ETFs as control | 91% overnight; overnight Sharpe 2.23 vs intraday 0.16 | ✅ (Clockwork) |
| A2 | Overnight momentum: past-20d avg overnight → next overnight | daily panel, rank IC, Oct 4 | IC +0.028, t=+3.2, 5/6 halves, ~10bp/night decile | ✅ |
| A3 | Overnight consistency (fraction of up-nights) beats magnitude | factory gate, Aug 16 | OOS2 +0.157 vs +0.076%/night; fixes 2022 | ✅ (Clockwork) |
| A4 | Tug-of-war intraday reversal across days | daily panel, Oct 4 | past-20d intraday → next intraday IC +0.009, n.s. | ❌ |
| A5 | Gap-ups fade intraday vs peers | daily panel, Oct 4 | IC −0.024, t=−3.0, 5/6; decile −4.9bp, n.s. | 🟡 |
| A6 | Today's intraday winners do worse overnight | daily panel, Oct 4 | IC −0.022, t=−2.6, 5/6 | 🔬 (Clockwork filter candidate) |
| A7 | Same-slot intraday periodicity (Heston-Korajczyk-Sadka) | 30-min slots, Oct 4 | IC +0.010, t=+4.5 (day-clustered), 6/6; ~2.3bp/slot | 🟡 below cost |
| A8 | 30-min cross-sectional reversal | 30-min slots, Oct 4 | t=−11 was bid-ask bounce; with 5-min gap ≈0 | ❌ |
| A9 | Market's early move predicts its rest of day | ES 675 days, Oct 2 | sign flips by year | ❌ |

## B. Equity day trader — entries, ranking, exits (intraday)
| # | Hypothesis | Result | Verdict |
|---|---|---|---|
| B1 | Stock-level entry features pick first-move direction | 25+ features × 9,449 candidates (2y): max |corr| ≈0.04 | ❌ |
| B2 | Batting order ranks better names first | within-scan corr(rank, outcome) +0.03 (t=0.29) | ❌ |
| B3 | Pullback-first vs at-high-first | within-day difference 0.00pp (t=−0.01) | ❌ (live, harmless) |
| B4 | Freshness gate (5-day run >18%) | 2y lab 4/5 halves; replay worse | ❌ |
| B5 | Fitted grader weights (ridge, 16 components) | picks better 62% of days but level negative; replay −$236 | ❌ |
| B6 | Volatility-scaled stop | 2y 5/5 halves per unit risk; replay +$161 | 🔬 live trial to Oct 29 |
| B7 | Remove T+5 check | replay +$1, hard-stop losses ×2.5 | ❌ keep check |
| B8 | Confirm-then-buy | replay −$442 | ❌ |
| B9 | One new name per sector per scan | replay noise | ❌ |
| B10 | Remove catalyst privileges | replay −$103, max drawdown halved | 🟡 risk option |
| B11 | Hold our picks longer (close / next open / next close) | 224 trades: worse than live exits | ❌ |
| B12 | Exit-later / give-back capture (stall-cut, add-on, horizon, partial re-scale) | 7 rejections Aug-Sep | ❌ |
| B13 | Choppy/cautious hard block | removing it −$616 (t=−2.55) | ✅ keep |

## C. Factory engines (multi-day, judged by the Proving Ground)
| # | Engine | Result | Verdict |
|---|---|---|---|
| C1 | Wave Rider — WILD ≥3% movers, hold 3d | gate PASS Aug 15; live: picks ≈ opportunity set | 🔬 live paper |
| C2 | Contrarian — 3-day losers, hold 5d | gate PASS; +2pp alpha all 3 years; bears positive | ✅ |
| C3 | Bargain Hunter — MID fallers bounce | robustness 67% | ❌ |
| C4 | Steady Hand — low volatility | inverted in bull; bears positive; fleet Sharpe falls | ❌ |
| C5 | XS momentum | 44% robustness (spike) | ❌ |
| C6 | Earnings Drift (gap proxy) | negative both OOS | ❌ |
| C7 | Whiplash — 1-day reversal | dead everywhere | ❌ |
| C8 | Smooth Sailing — risk-adjusted momentum | negative in-sample | ❌ |

## D. Patterns / technical levels
| # | Hypothesis | Result | Verdict |
|---|---|---|---|
| D1 | Fibonacci PIVOTS from prior-day range (futures MNQ, Jun 2026) | IS 2025-26 WR 45.1% vs 40.6%; OOS 2024 −6.3pp | ❌ tracked flag H5 only |
| D2 | Fibonacci RETRACEMENT entries: up-swing ≥2-3 ATR over 20/60d, close in the 38.2-61.8% zone, hold 3/5/10d | factory gate, split-adjusted panel, ~15k trades each: 12/12 configs FAIL; alpha within ±0.3%, t −1.4…+0.5, robustness 50%; raw returns positive only from the bull tide (`factory/research/fib_retracement.py`, Oct 4) | ❌ |
| D4 | 84 textbook / practitioner / academic indicators, one at a time (RSI14, RSI2, MACD, ADX, Bollinger + squeeze, Williams %R, CCI, stochastic, Donchian/Turtle, NR4/NR7, inside/outside day, hammer, engulfing, doji, gap-fill, Fib retracement + pivots, MAX/MIN, skew, idio vol, beta, 52w-high, MA distances, intraday shape, rvol, OBV) × 5 horizons = 420 tests, OOS Jan 2025-Oct 2026, bar |t|>3.5 | overnight: 3 pass — close vs VWAP −0.036 (t −4.5), close location −0.035 (t −4.2), last-30-min return −0.035 (t −4.1); Clockwork's on_cons30 +0.030 (t 3.4). Next-day intraday / 1-5 day: none pass; slow trend (dist MA200, 52w high, r120) +0.02-0.05 weakly. Classic oscillators and candle patterns ≈ 0 everywhere (`research_ml_model.py`, `research_out/ml_feature_ic.csv`) | 🔬 closing-strength → overnight reversal is the lead |
| D3 | Grader patterns (ORB, VWAP reclaim, bull flag, HOD break, FVG count) | component fit Sep 18: VWAP reclaim + FVG count positive, HOD break + bull flag negative | 🟡 |

## E. Futures (summary — full trail in CLAUDE.md)
Selection works, reaction fails (6+ methods); Daily Tide (prev close vs 200d MA) ✅ live; sizing not the
lever; wider stops lose; regime-flip exits ❌ ×3; overnight MNQ edge measured, not built.

## F. Oct 4 2026 programme — learned multi-signal model (in progress)
Nightly `learner.py` audited: it adjusts 5 grader multipliers from win rates of our own filtered trades;
2 weights never move, 1 is never read, all sector grades WEAK. Not a model. Building: causal feature panel
(~50 signals) → walk-forward gradient boosting → factory engine → Proving Ground. Results appended below.

| # | Hypothesis | Data / method | Result | Verdict |
|---|---|---|---|---|
| F1 | Nightly `learner.py` is a learning model | code audit, Oct 4 | adjusts 5 grader multipliers from win rates of our own filtered trades; momentum/sector weights never move, earnings weight never read, all 11 sector grades WEAK (stale ones persist) | ❌ not a model |
| F2 | A learned multi-signal model (84 causal features, gradient boosting, walk-forward monthly re-fit, purged) ranks the universe better than single signals | `research_ml_panel.py` + `research_ml_model.py`, OOS Jan 2025-Oct 2026, market-neutral targets | overnight IC +0.065 (t 4.95) on 16:00 data; next-day intraday, 1-day ≈ 0 after cost; 3/5-day ≈ simple reversal, t<2 | see F3 |
| F3 | ...but decided at 15:40 (MOC timing) — 16:00-close features peek past the decision and share the closing print with the target | `research_ml_overnight_1540.py`: daily features lagged 1 session + 15:40 snapshot | look-ahead inflated it ~30%. Honest: WILD IC +0.050 (t 4.29), 4/4 half-years, vs Clockwork's signal +0.019; top-3 excess ≈ Clockwork (+31 vs +32bp), top-5 +21 vs +16bp | ✅ real |
| F4 | "Night Owl" — the 15:40 model as a factory engine | `factory/research/ml_overnight_engine.py`, Proving Ground | long-only WILD top-5: **PASS 8/8** (alpha +0.23/+0.33/+0.35%/night, t 4.90, WF 8/8, net of 0.20% cost +0.11%, corr +0.18 vs Clockwork); top-3 PASS; long-short sleeve FAILS cost (−0.06%) | ✅ candidate |
| F5 | Night Owl vs Clockwork's live picks | 435 nights, random tie-breaks | overlap 0.2 of 3 names (81% nights none); excess correlation 0.00; union book Sharpe 2.22 vs 1.94/2.02 alone; Night Owl's latest half −4bp | 🔬 shadow next |
| F6 | Clockwork + skip names closing strong at 15:40 | `factory/research/clockwork_v3.py` | helps the backtest definition (+15.6 vs +10.4bp) but hurts the live rule (+13.0 vs +17.5bp) | ❌ |
| F7 | Clockwork's live rule (count lagged a day, alphabetical ties) vs its backtest definition | same | live +17.5bp/night excess (t 2.49), positive all 6 half-years with RANDOM ties; ~5bp of the earlier +22 was alphabetical luck | ✅ validated as-is |
| F8 | What Night Owl learned (family permutation importance, train <2025-07, test 315 nights) | `research_ml_explain.py` | today's shape at 15:40 38% · sector/DNA 20% · yesterday's intraday shape 19% · volatility/tails 16% · oscillators 12% · overnight persistence (Clockwork's family) 11% · trend 8% · volume 7% · Fibonacci/market/returns/candles ≈0; rank corr with Clockwork +0.18 | ✅ different method, same trade |
| F9 | Model or model+Contrarian blend beats Contrarian at 3-5 days | saved OOS preds 2025-26 | Contrarian's 3-day reversal top-5: +1.40%/5d excess (net +1.20%, t 2.26, all 4 halves +); model +0.81%; blends +0.80% | ❌ blend · ✅ Contrarian re-validated |
| F10 | Long history: Night Owl on DAILY features only (lagged + today's gap), trained from 2015, walk-forward OOS 2018-01 → 2026-10, causal WILD (trailing vol) | `research_ml_longrun.py`, yfinance official daily prints, 2,199 nights | IC +0.044 (t 9.7) positive EVERY year 2018-2026; top-3 excess +18.7bp/night (t 5.0) positive every year; bears: 2018-Q4 +5, COVID +71, 2022 +13bp. Clockwork's signal: IC +0.024, top-3 +15.7bp, negative 2018-Q4 (−14) and 2026. Excess corr +0.16. Daily-only ≈ the 15:40 model (more training years compensate) → shadow engine can run on official daily prints only | ✅ robust across regimes |
| F11 | Tail risk of a long-only overnight WILD book over 9 years | same | max drawdown (summed net bp): Night Owl −60%, Clockwork −51%, all WILD −80% — Nov 2021 → mid-2023 speculative bear (2022 WILD −56%). SPY>200d gate: NO fix (Clockwork worse −60%). WILD-basket trend gate: Night Owl −60% → −41% (MA200), Clockwork −51% → −49% (2023 −33% even gated). 2024-26-only window showed −14% | ⚠️ sizing/bear plan needed — applies to LIVE Clockwork too |
| F12 | Night Owl BUILT and LIVE on paper (Oct 5 2026) — production library re-run of F10 | `factory/night_owl_model.py` (model, `validate`), `factory/live/night_owl.py` (engine), `factory/tests/test_night_owl.py` | walk-forward 2018-01 → 2026-10-01 through the production code: WILD IC +0.0449 (t 9.36) positive every year; top-3 excess +18.3bp/night (t 5.06) — **2019 −1.2bp and 2023 −1.5bp** (F10's "positive every year" for top-3 does not survive the production build: the universe now filters on YESTERDAY's close and every input is lagged before filtering). Tests: live scoring = training scores to 0.0 on 3 dates; later data leaves past inputs unchanged; engine skips other books / earnings, caps at 3 incl. unconfirmed sells, waits for Clockwork until 15:46 | ✅ LIVE paper — judge on ref_pnl over months |
| F13 | Fair head-to-head, Night Owl vs Clockwork's rule (Oct 5 2026) — same WILD set (personality.csv), same 2,199 OOS nights, random tie-breaks, 2.1bp fee | production walk-forward preds + panel `on_cons30` through today's gap | Night Owl top-3 +18.3bp/night over the WILD average (t 5.1), Sharpe 1.96, maxDD −38%; **Clockwork's rule +19.2bp (t 6.2), Sharpe 2.63, maxDD −22%**; both books together Sharpe 2.54, maxDD −23%; shared names 0.6 of 3 (no overlap 50% of nights), excess corr +0.15. F10's Clockwork +15.7bp used a causal-volatility WILD set ⇒ the order flips with the WILD definition: **the two are equal, not ranked.** Lottery shape: best 5% of nights = 100% of net, worst 5% cancels the middle 90%, worst night −19.9%. Last 3 months excess +1.2bp | ⚖️ edge real vs random; NOT better than Clockwork — a second overnight book, judged live on ref_pnl |

## G. Earnings (real report dates + EPS surprise from yfinance, 9,327 events 2015-2026 — `research_earnings.py`)
| # | Hypothesis | Result | Verdict |
|---|---|---|---|
| G1 | Pre-earnings premium (Frazzini-Lamont): buy t-6, sell t-1, never through the report | +21bp excess per event (t +4.3 by month) but 7/12 years positive; 2020 alone +135bp; ≈ cost | 🟡 |
| G2 | Post-earnings drift by announcement reaction (EAR) | top quintile +30bp/10d (t 2.3); top−bottom flips sign by year; 0 at 20d | ❌ |
| G3 | Post-earnings drift by EPS surprise | weaker than EAR, inconsistent | ❌ |
| G4 | Typical reporter lags the universe for ~20 sessions after the report | middle quintiles −50…−105bp over 20 days | 🔬 avoidance-rule lead for Wave Rider / Contrarian |

## H. Other hunts (Oct 4 2026)
| # | Hypothesis | Result | Verdict |
|---|---|---|---|
| H1 | Industry momentum (Moskowitz-Grinblatt), sector's other members' 5/20/60d → member's next 5/20d, 2015-2026 | best IC +0.010 (t 1.8); others ≈ 0 | ❌ |
| H2 | Futures H5 Fibonacci pivot flag — re-evaluation due Oct 2026 | ⚠️ BUG: H5 read False on every trade since Jun 17 (housekeeping moved its modules; hero_score's try/except hid the ImportError). No trading effect (not weighted, boost never implemented). Fixed import paths; made fib_deep's sim_replay import lazy (hero_score runs inside live traders). Re-eval on 579 live-parity trades 2021-26: H5 +$26.2/contract vs +$12.1, better in 4/6 years (2023, 2025 worse); fixed 300pt threshold drifts with price level | 🟡 tracked flag; test an ATR-scaled version before any use |

## I. Oct 5 2026 — a learned model for the DAY TRADER ("Day Owl") — `research_ml_dayowl.py`, `research_dayowl_delay.py`
Night Owl's exact pipeline (90 daily inputs lagged one session + today's opening gap, 237 names, yfinance official
prints 2015→), target = TODAY's open→close; yearly re-fit walk-forward, OOS 2018-01 → 2026-10-05.
| # | Hypothesis | Result | Verdict |
|---|---|---|---|
| I1 | A pooled model ranks today's open→close better than chance | IC +0.044 (t 14) every year; top-3 +35bp/day over the day's average, long-short +80bp (t 8.5) — **measured from the official opening print** | ⚠️ see I2 |
| I2 | ...and it survives entering AFTER the open (leak / tradeability check, 2024-26, our bars_5m, exit at the last 5-min close) | top-3 excess: official open +21bp (t 2.1) → first 5-min-bar trade +14 → **09:35 +6.5bp (t 0.7)** → 09:45 +7.7 → 10:00 +7.2 → 10:30 +6.2. IC after the open +0.02 (t 3.5-4). By year at the official open 2018-25 +30…+57bp, **2023 0, 2026 +5**. The edge is mostly the opening auction print (gap input and target share it); after 09:35 it is ~5-8bp/day, below the ~10-20bp round trip | ❌ for the day trader · 🟡 MOO→MOC auction-reversal (decayed to ~0 in 2026; paper fakes auction fills) |
| I3 | The model separates gappers that keep running from faders | 9y: model-top-tercile gappers +54bp vs bottom −23bp; **2024-26: top tercile −18bp (official open) / −22 (09:35) / −29 (10:00)**; all gappers ≥+3% −11…−19bp from any entry | ❌ |
| I4 | "Train it on a few stocks" — model per stock vs a model on those 20 vs pooled, same 20 long-history names, same days | INTRADAY: per-stock timing works on 12/20 stocks (coin flip), pooled 18/20; top-3-of-20 edge per-stock +4.4bp vs pooled +10.0bp (t 3.2); per-stock ridge +6.5bp. OVERNIGHT (where pooled is proven): per-stock cross-sectional IC +0.006 (t 1.1), top-3 −0.0bp vs pooled +0.035 (t 5.7). ~2,000 days per stock is too little data for this noise level | ❌ pool across names |

## J. Oct 5 2026 — can the EVENING tell us tomorrow morning's top gainers? (XRPN, SDEV, IBRX) — `research_gappers*.py`
Broad universe: every currently-listed US common stock (5,637; Nasdaq Trader directory), yfinance daily 2024-01 →
2026-10; tradeable = close ≥ $2 and median $vol ≥ $1M (~3,300 names/night). ⚠️ survivorship (delisted pump-and-dumps
missing) — long results are upper bounds. The three named names TODAY: open→close SDEV −59%, XRPN −12%, IBRX −4%.
| # | Hypothesis | Result | Verdict |
|---|---|---|---|
| J1 | Buying the morning's top gappers at the open pays | top-10 gappers each morning: open→close **−108bp** (median −154, 41% up), negative 2024/25/26; gaps ≥+20% −218bp; ≥+10% −118bp; +5-10% −52bp. Same at price ≥ $5. In our 237 names 2024-26 gappers ≥+3% lose −11…−19bp from any entry | ❌ it is a SELL list for whoever held overnight |
| J2 | Single evening features predict who gaps big | magnitude yes, direction no: top-1% of today's return / volume surge / volatility / recent 10% gaps → 11-19× the base rate of a ≥+10% gap (0.29% → ~3-5%), but mean gap only +7…+40bp (they gap both ways) and then −25…−117bp open→close | 🟡 volatility, not direction (same as Sep 3 pre-market finding) |
| J3 | A learned evening model finds tomorrow's top gainers | 25 evening features, quarterly re-fit, OOS 2025-01 → 2026-09 (437 nights): only **1.7%** of its top-10 picks land in the morning top-10 (random 0.3%). With inputs built from day t's close: top-10 overnight +68bp (t 5.7) then −57bp open→close. **Honest version (every input lagged, no shared closing print): IC +0.032 (t 3.4), top-10 overnight +24bp gross / +19bp excess (t 3.0), then −26bp open→close; ≥$5 +15.5bp excess (t 2.6); ≥$10 +12.4 (t 2.1)** — the shared print inflated it ~65% | 🔬 broad-universe overnight tilt ≈ Night Owl size; needs a 15:30-price version + auction-cost check |
| J4 | "Frenzy" names (up ≥50-100% in 3 days at the close — XRPN/SDEV pattern) keep gapping up | ≥100%/3d: next gap mean +118bp but **median −87bp**, 43% up; then open→close −381bp. ≥50%/3d: mean +105bp, median 0, then −150bp. A lottery overnight, a crash after the open | ❌ |
| J5 | After-hours (16:00-20:00) move shows the gap early and continues | NOT RUN — hourly extended-hours download hit a Yahoo rate limit (10 min, live books unaffected). SDEV's gap was visible Friday after-hours (+9…+23% by 17:00); XRPN's formed in Monday pre-market | 🔬 throttled re-run after a close |

## K. Oct 5 2026 — SHORT the morning's top gappers (the reverse of J1) — `research_gapper_short.py`
Same broad panel (5,637 listed US stocks, 2024-01 → 2026-10, price ≥ $2, $vol ≥ $1M). Short at the official open,
cover at the close. Survivorship (delisted names missing) probably UNDERSTATES a short edge.
| # | Hypothesis | Result | Verdict |
|---|---|---|---|
| K1 | Shorting each morning's top-10 gappers (gap ≥ +5%) pays | +110bp/day gross (t 5.2), 2024 +75 / 2025 +131 / 2026 +128; median per stock +153bp, win 58%. Day-2 runners (up ≥20% the day before) +440bp/day; price $20+ +184bp; $5-20 only +34bp (t 1.0). Squeeze tail: 10.7% trade ≥20% above the open, 2.3% ≥50%, 0.5% ≥100%; worst day for a 10-name book −30%. Pessimistic +20% stop + 30bp cost: +66bp/day (t 3.9); +10% stop + 30bp: +9bp (dead) | see K2 |
| K2 | ...and it is not an opening-print artefact | Daily data: the 10% of gappers whose open WAS the day's high carry it (+1,093bp vs +23bp for the other 90%). 5-min check, 115 recent events: the official open = the first real trade (median 0.00%) — **those days are real**, but the fade is front-loaded: open=high days +895bp from the auction → +409 at 09:35 → +195 at 10:00; other gappers +187 → +122 → +41 | ✅ real · ⚠️ mostly capturable only in the opening auction |
| K3 | A model trained only on gappers picks better shorts | within-day IC +0.138 (t 12); **without any input that touches today's open +0.121 (t 10.8)** — volatility, ATR, recent 10% gaps, distance below 20d/52w highs, small dollar volume. Top-5 +241…+274bp/day from the official open, 2025 and 2026 both positive; on ordinary (not open=high) days +136bp per stock vs +23bp for the top-10-by-gap baseline | ✅ real selection, from the official open only |
| K4 | The shorts can be borrowed | IBKR public file (Oct 5 22:28): SDEV and XRPN NOT shortable; IBRX easy (0.48%/yr). Last 30 days' top-10 gappers TODAY: 97% listed, 17% fee ≥50%/yr (≥0.14%/day), 90th pct 156%/yr. The 6 unborrowable had the best fade (+637bp). Snapshot ≠ gap-morning availability | ⚠️ unmeasured on the day |
| K5 | Our own 237 names behave the same | bars_5m 2024-26: gaps 5-10% short +32bp (open) / +34 (09:35) / +19 (10:00); gaps ≥10% −20 / −2 / +1 — our universe's big gappers do NOT fade | ❌ for our universe |
| K6 | Paper can test it | IBKR paper fabricates auction fills (Sep 20) and its borrow is not the real market's — the honest score is official prints + a recorded borrow snapshot | — |
**Status 🔬:** first large raw effect this session that is not a print artefact. Before any book: (a) 09:35/10:00 entries over
the FULL 2024-26 history (needs 1-min bars for ~6,700 event days — DataBento, user approval), (b) gap-morning borrow
(snapshot IBKR's usa.txt ~09:00 daily), (c) squeeze sizing, LULD halts, SSR.
| K7 | PROPER TEST (Oct 6): 8 years, May 2018 → Oct 2026, 1-min bars for 45,498 gapper days (DataBento XNAS.ITCH, quote ≈ $8.90), decision on data through T and FILL at the next minute's close (one-bar gap), stops checked minute by minute (a jump through the stop fills at that minute's open), 30bp cost — `research_gapper_intraday.py`. No model, short the 10 biggest gaps | official open (auction) +55bp/day net (t 4.6, 15/18 halves); 09:31 +14bp (t 1.3); **09:35 + 20% stop −5.7bp (7/18 halves)**; 10:00 −18bp | ❌ at any time we can actually fill; the edge lives in the opening auction |
| K8 | Walk-forward model on gappers only (semi-annual re-fit, OOS 2020-07 → 2026-10). PRE-REGISTERED bar: 09:35 decision / 09:36 fill, top-5, +20% stop, 30bp — t ≥ 3, ≥75% of half-years positive, survives a 10bp/day borrow charge, beats random and simple rules | +34bp/day, **t 2.47**, **7/13 halves**, with borrow +24bp t 1.75, 2023 −54; random 5 −11bp; rule "5 most volatile gappers" +29bp | ❌ FAILS the pre-registered bar |
| K9 | What the model learned | within-day IC +0.08 (09:35) / +0.10 (09:31), positive every year; drivers are volatility — 20d vol, ATR, count of recent ≥10% gaps, biggest 1-day spike, distance below 20d/52w highs. A one-line volatility rule gets ~85% of the model's result | 🟡 real ranking, mostly "short the most volatile gappers" |
| K10 | Tail, concentration, timing | 09:35 top-5 with stop on a $10k book: best 5% of days = 190% of the total (the other 95% lose), 44 days worse than −$1,000, worst day −$3,564, worst stretch −$20,363, worst single short −186% (halt reopened through the stop). Stops HURT (no stop +66bp, t 4.1) but no-stop worst day −$4,038. Earlier decisions are better (09:31 +54bp t 3.7, 10/13 halves, 2023 −29) — NOT pre-registered, and small-cap spreads in the first minutes likely exceed 30bp | ❌ not deployable at $10k · 🔬 09:31 only with measured opening spreads |
Data notes: 2018-2023 daily panel covers tickers A→OGG only (Yahoo rate-limited the pull; alphabetical, so unbiased but thinner); 3% of events dropped where DataBento and Yahoo day moves disagreed > 3 points (ticker reuse/renames); survivorship (delisted names missing) probably understates a short edge. 1-min bars for all gapper days are kept in `research_out/gapper_1m/` (247MB) for future gapper research.

## L. Oct 6 2026 — do some stocks reliably RESPECT textbook patterns? (`research_pattern_persistence.py`)
User's thesis: many stocks follow textbook trends / respect FVGs, order blocks, breakouts, Fibonacci — learn which,
trade the patterns only there. Tested as persistence. 2,428 liquid US stocks (tickers A→OGG with continuous yfinance
daily history; close ≥ $5, 20d median $vol ≥ $5M), Jan 2018 → Sep 2026, 610,555 mechanically-defined pattern events.
Entry at the NEXT open, exit at the close 5 sessions later, minus the average stock (market-neutral). "Pattern alpha"
= pattern return − the stock's own normal 5-day return in that period (so a stock that simply rose is not credited).
| # | Hypothesis | Result | Verdict |
|---|---|---|---|
| L1 | 12 textbook patterns earn money on their own (one portfolio per day, t by day) | continuation NEGATIVE: gap-and-go −48bp/5d (t −3.1, 2/9 yrs+), volume breakout −36 (t −3.6, 1/9), 20d breakout −15 (t −2.7, 0/9), 52w high −17 (t −2.0), NR7 −10 (t −2.5). Support/retest ≈ 0: FVG retest +0.6 (t 0.1), order-block −5.5, 50d MA −6, 200d MA −5, Fib golden zone −12. Reversion: RSI(2) dip +7.6 (t 1.1, 5/9 yrs), lower Bollinger −1.2. Per-EVENT averages looked positive for the support/reversion set (+8…+24bp) only because crash-rebound days carry hundreds of simultaneous signals | ❌ none after costs; buying breakouts loses |
| L2 | "Respecting" a pattern is a stable trait of a stock (2018-01→2022-06 vs 2022-07→2026-10) | rank correlation of per-stock pattern alpha across the two periods: −0.094…+0.142 for the 12 patterns; **all support patterns combined −0.012, all continuation combined +0.023**. Two isolated top-vs-bottom spreads (52w high +48bp t 3.5, order block +40bp t 2.6) out of 14 tests, neither confirmed by the walk-forward | ❌ not a trait |
| L3 | The user's strategy, walk-forward: each year trade patterns only on last 2 years' top-quintile "respecters" | all patterns: +5.0bp vs +1.9bp for every stock → +3.2bp edge per 5-day trade (5/7 years) — far below a ~20bp round trip; RSI(2) dip respecters +13bp but decaying 37→30→1→15→1→5→3 | ❌ |
Why: the stocks that LOOK like they respect patterns are the ones that went up — remove each stock's own drift and
nothing persistent is left. This is the same trap as the Jul 2026 universe screen (picked on 88-93% backtest win rates,
lost live). Consistent with D4 (420 indicator tests ≈ 0) and I4 (per-stock models < pooled).

## M. Oct 6 2026 — the fleet replayed together: consolidation, bear plan, macro and earnings (`research_portfolio_lab.py`)
CW (Clockwork rule) + NO (Night Owl, yearly-refit walk-forward predictions `research_out/no_preds_all.parquet`) + CT
(Contrarian, 2-slot simulation), rules as live, causal WILD (trailing 60d vol), official prints, $10k each, 2018-03 →
2026-10. ⚠️ Run on the 237 hand-picked names first, then re-tested on ~2,400 UNSELECTED liquid stocks (tickers A→OGG).
| # | Hypothesis | Result | Verdict |
|---|---|---|---|
| M1 | The three books diversify each other | daily corr CW–NO +0.64 (same trade), CW–CT 0.00, NO–CT +0.03; fleet Sharpe 1.87 vs 1.54 / 1.52 / 0.99 alone (hand-picked) | ✅ but see M6 |
| M2 | Night Owl skipping Clockwork's names (live rule) helps | allowing overlap: CW+NO Sharpe 1.79 vs 1.68 with the skip; bear window −$4,123 vs −$6,070 | ❌ the skip rule hurts |
| M3 | One merged ranker (average rank, top-6) beats two top-3 books | Sharpe 1.53 vs 1.68 | ❌ |
| M4 | Bear plan: flat when the WILD basket is below its 200-day average | ⚠️ corrected Oct 6 for a warm-up bug (2018 counted as "off"): fleet (hand-picked) Sharpe 1.87 → 2.06, maxDD −83% → −23%, total −5.5%; by year saved 2022 +$19.5k, cost 2020 −$14.9k (V-rebound) and 2023 −$5.7k, 2026 −$391 (off 1 session); was: Sharpe 1.87 → 2.00, maxDD −83% → −23%, 2021-11→2023-06 −$18,612 → −$1,245; plateau 200-250d (100d worse); off 26% of sessions (2018, COVID, Jan 2022-Jan 2023, Mar-May 2025). UNSELECTED stocks, Clockwork rule: Sharpe 1.23 → 1.34, maxDD −53% → −29% (off 40% of sessions, costs return in 2023-24). ON today (+16.6%) | ✅ best bear plan |
| M5 | Other bear plans: volatility targeting / drawdown brake / short Russell 2000 futures overnight × rolling beta | Sharpe 1.65 / 1.65 / 1.45 vs 1.68 base — the hedge pays away the index's own +5bp/night premium at beta 1.68 | ❌ |
| M6 | The books survive on UNSELECTED stocks (survivorship check) | Clockwork rule +38%/yr, Sharpe 1.23 vs every WILD name overnight +20%, 0.98 — survives (8/9 yrs +). **Contrarian rule −36%/yr, Sharpe −0.28, maxDD −811%**; every 3-day faller held 5 days: −20bp/day vs market (t −2.3), negative 2021-25; liquid-only −6 (t −0.6); above-200d −36 (t −2.4). Same rule on the hand-picked 237: +51bp/trade | ✅ Clockwork · ❌ Contrarian (survivorship artifact) |
| M7 | Macro nights are dangerous and should be skipped | ⚠️ PARTLY RETRACTED Oct 6 (see M7b): the CPI/'mid-month' part used release mornings DETECTED by their 08:30 futures-volume spike — that picks the mornings the market reacted to most, inside the overnight hold = look-ahead. Original text: the OPPOSITE: 2021-26 scheduled-release nights (21% of nights) carry 78% of all overnight return. Mid-month 08:30 (CPI/PPI/retail) +44 vs +2bp (t 2.7, every year +); night before FOMC +24bp (t 2.0) and OUT OF SAMPLE 2018-20 +32bp (t 2.2, replicates Lucca-Moench); after FOMC +39 (2021-26) but −26 (2018-20); CPI could not be confirmed pre-2021 (CPI was not market-moving then). Dates: Fed FOMC schedule + 08:30 ES-volume spikes (GLBX 2018-20 bought, $3.81) | ✅ never skip macro nights · 🔬 pre-FOMC size-up |
| M8 | Earnings nights need the blackout | WILD names on their own report night: sd 992bp vs 226bp, worse than −10% 10.9% vs 0.2%, worse than −20% 1.9%; mean +90bp (survivorship-inflated) — CW without blackout earned more in-sample | ✅ keep as a variance rule |
| M9 | Survivorship-free check: EVERY US common stock incl. the 24% delisted since, Jul 2024 → Oct 2026 (DataBento EQUS.SUMMARY official daily, $10.54; tickers via free symbology; splits back-adjusted) — `research_survivorship_check.py` | ALL vs SURVIVORS-only: Clockwork +57%/yr Sharpe 1.76 vs +40% 1.21 (survivorship does NOT inflate it — liquid delistings are mostly takeovers); WILD basket +26% either way; Contrarian Sharpe 0.20 (maxDD −183%, worst day −$3,776 on $10k) vs 0.01 | ✅ Clockwork · ❌ Contrarian on any unselected universe |
| M10 | Night Owl retrained on ~1,800 unselected stocks (2018+, yearly re-fit, OOS 2020-26) vs Clockwork's rule on the same stocks/nights — `research_nightowl_broad.py` | IC +0.031 (t 6.9), every year +. NO top-3 +40%/yr Sharpe 1.16 maxDD −39%, positive EVERY year (2022 +34%); CW rule +43% 1.16 −55% (2022 −36%); daily corr only +0.39; both with overlap allowed Sharpe 1.39 maxDD −36%, + 200d gate 1.54 / −22%. Broad WILD basket today +5.7% above its 200d average (hand-picked basket +16.6%) | ✅ keep both, allow overlap, retrain NO broad |
| M7b | Macro nights on TRUE release dates (`macro_calendar` backfill 2018-2026, `research_macro_nights.py`) | night before an FOMC decision: every name +31 vs +6bp normal (t 2.6), 2018-20 +41 (t 2.1), 2021-26 +26 (t 1.7); broad WILD basket +36 vs +6 (t 2.6). Everything else is NOT reliable on its own: CPI +18 (t 0.7), payrolls +8, PPI +4, retail +8, GDP +21 (t 1.2), PCE +17 (t 1.1), post-FOMC +22 (t 0.9). Release nights = 24% of nights, 44% of the return (not 78%) | ✅ pre-FOMC (the documented Lucca-Moench drift) · ❌ the CPI-night claim · ✅ never skip release nights |

**SHIPPED Oct 6 2026 (user-approved):** M4 → the **Basket Tide** (`factory/live/basket_tide.py`) gates new entries on
Clockwork and Night Owl, live from Oct 7; M2/M10 → Night Owl may share Clockwork's names, at most $6,667 a name across
both. Contrarian (M6/M9) kept running by user decision.

## N. Oct 8 2026 — the losing week: "are we on the wrong side?" (flip / stand-aside tests)
Week Oct 5-8, fleet mark-to-market at fills **−$2,199** (Mon −82 · Tue −71 · **Wed −992 · Thu −1,054**). 93% of it is two
gap-down mornings: universe overnight −163 / −105bp, the WILD basket −235 / −156bp; Russell futures −111 / −52bp. Backdrop:
Treasury auctions ~45-60bp above the month before (10-yr 5.30% vs 4.83%, 30-yr 5.62%). Fleet held ~$37-40k long, zero short,
~$17k of it semiconductors — every book except the day trader lost on the same two nights. Scratch labs in the session
scratchpad (`flip.py`, `sides.py`, `flipbooks.py`, `mtm.py`); nightly series `research_out/portfolio_lab_nights.parquet`.
| # | Hypothesis | Result | Verdict |
|---|---|---|---|
| N1 | Flip (short) or stand aside the overnight books when the tape is bad — 8 ex-ante signals (last night, last 5 nights, WILD/market today, WILD intraday, WILD 5-day, vs 20d/50d avg), quintiles, 2018-04 → 2026-10 | After the WORST previous night the next night is the BEST: CW+NO +52bp vs +23 average (t 4.4, positive 9/9 years); after a bad 5-night stretch +38bp. Shorting the bottom quintile loses 15-59bp/night; always-short −30bp/night. Only "WILD fell hard intraday today" is ~flat next night (−9bp, t −0.8, 5/9 yrs) — noise | ❌ flipping is the wrong side; ❌ "stop after a losing streak" skips the best nights |
| N2 | Rising 10-yr yields (5/10/20-day change, measured at d and d−1) predict bad overnight nights | Top quintile (yields up fastest) next night +15 to +41bp — positive, t +1.4 to +3.6; stand aside when the 10-day change > 15/20/25/30/40bp: Sharpe 1.30 → 1.22/1.17/1.26/1.31/1.29 | ❌ no rates gate |
| N3 | This week is evidence the overnight edge broke | 2-night CW+NO loss ≈ −5.5% ≈ 2nd percentile; 2-night ≤ −5% happens 6.2×/yr; after a 2-night loss ≤ −4% (n 91) the next 5 nights average +3.19% vs +1.17%, next 20 +7.26% vs +4.63%. Clockwork live since Sep 9 at official prices +$33 vs ~+$380 expected; 1 sd of a month ≈ ±$950 | ℹ️ normal tail, not a broken edge |
| N4 | The day trader is on the wrong side — shorting what it buys would win | Mirror of its live trades (−gross − fees): Jun +$809 · Jul +239 · Aug +877 · Sep +286 · Oct +161 = **5/5 months positive since June**, but **May −$1,532**. The 2-yr first-qualify candidate pool is a coin flip (−0.08%/day, t −0.81, 50% of days down, months alternate); Aug-Sep candidates were POSITIVE to the close while our trades lost ⇒ the bleed is selection + exits, not direction. A+ SHORT candidates, unique per symbol-day: ≈ 0 on the 13 days both sides were graded; Oct 7 the 46 A+ shorts rose +0.67% against us while the 6 A+ longs fell −5.17% | 🟡 track the mirror monthly (free, from `trades`); ❌ do not flip the book |
| N5 | Book Health LONG gate has value | trailing-10d drift ≤ 0 → next day's A+ LONGs −0.56% signal→close (n 45 days) vs > 0 → +0.01% (n 16). It shut the book Oct 7 (−0.02%) and Oct 8 (−0.72%) — Oct 7's A+ longs fell −5.17% | ✅ keep; it is the one gate that acted this week |
| N6 | Clockwork live picks = its validated rule | On official prints the same rule (personality.csv WILD, 30-night count, lagged a day) matches live on only 1-2 of 3 names: live counts gaps from bars_5m and the score is x/30, so 3-7 names tie at the cut and ties break ALPHABETICALLY (known, F7). 12 nights: live picks −461bp vs the whole tied group −262bp. CENX held every night since Sep 9 | 🟡 noise-sized; the pick is close to a lottery among tied names |
| N7 | Clockwork "loses almost every night" | 19 nights with official marks: negative on **14 at paper fills vs 8 at official prices**; −$1,442 vs +$33. The IBKR paper simulator's MOO fills (Sep 20) are most of the daily red | ℹ️ judge on `ref_pnl` — the dashboard headline shows fills |
| N8 | Hedge the overnight books with Russell futures | Would have recovered ~$550 of the ~$1,100 lost Wed/Thu; over 2021-26 the 1.68× hedge costs ~40% of profit, Sharpe 1.68 → 1.45 (M5) | ❌ as a standing hedge |
| N9 | Wave Rider / Contrarian earn their place in the fleet | Wave live 36 trades −$507: 12 stops −$2,211 (realized **−9.45%** avg on an 8% stop — gaps), 16 time exits +$1,432, your 2 manual closes +$320; daily MTM corr with Clockwork +0.53. Contrarian 7 own trades +$65 after RIOT −$772 (−15.4% through a 15% stop); M6/M9 already show its edge is survivorship | 🟡 both add correlated WILD beta with no demonstrated alpha — user decision |

## O. Oct 8 2026 (night) — handling gap-down weeks: react at 09:25, don't predict at 15:40
User: "don't pause — find the signal that handles weeks like this." Futures 24h 5-min bars 2021+, daily official prints
2018+, unselected stocks (A→OGG 2021-23, all US commons 2024-26), VIX/VIX3M (yfinance). Scratchpad: `gapdown.py`,
`futnight.py`, `exante.py`, `buygap.py`, `whichgap.py`, `panic.py`, `barsonly.py`.
⚠️ **Price-basis trap found:** the Night Owl daily cache is dividend/split ADJUSTED, `bars_5m` is RAW — mixing them shifts
2024-Jul 2026 comparisons by +0.5-0.7% even on flat mornings (agrees to ±0.02% from Aug 2026). A first "opening-auction
overshoot" result came from this and was withdrawn. **Never compare daily-cache prices with bars_5m prices directly.**
| # | Hypothesis | Result | Verdict |
|---|---|---|---|
| O1 | Overnight books: on gap-down mornings HOLD to the close instead of selling at the open (official prints, 12,964 CW/NO positions 2018-26) | WILD basket gap ≤ −2%: held positions +71bp open→close (t 4.3, 8/9 yrs), gap-up ≥ +1%: −29bp (selling at the open is right). Own gap ≤ −1% → hold: +6bp/position, better 9/9 yrs | ✅ the reversal is real (but see O4/O5) |
| O2 | Ex-ante version: Russell futures 15:55→09:25 (known before the MOO cutoff) | corr with the WILD basket gap 0.81. RTY ≤ −1%: held positions +85bp open→close (t 4.2), ≤ −1.5% +195bp (t 5.8); 2023 −13bp; median morning +45bp, only 55% of mornings positive | ✅ signal, ⚠️ lumpy |
| O3 | Overnight futures hedge triggered mid-night (01:00/04:00/07:00, RTY down 0.5-1%, 1.5× short to the open) | overnight futures drops do NOT continue — the rest of the night slightly recovers (+5 to +11bp); every variant lowers Sharpe (1.27 → 1.01-1.22), helped 1-2/6 yrs | ❌ |
| O4 | Which gap-downs rebound? (295 mornings RTY ≤ −0.5%, terciles, features known by 09:25) | rebound when FEAR is up and names are BEATEN DOWN: VIX/VIX3M high +66bp (5/6 yrs) vs low −31bp (1/6); VIX up yesterday +81 vs −25; WILD 5-day low +66 (6/6) vs high −33 (2/6); bigger gap +115 vs −30 | ✅ panic vs calm |
| O5 | PANIC/CALM classifier, thresholds fixed on 2021-23 (VIX/VIX3M ≥ 0.901 yesterday, WILD 5-day ≤ −1.07%), tested 2024-26 | PANIC: train +42bp (t 0.7) → test **+187bp (t 2.3)**; all 76 mornings +99bp (t 2.0), + 5/6 yrs (2021 −93, n 6). CALM: −15bp (t −0.9) — a weak continuation, not a tradeable short. **Oct 7 and Oct 8 = CALM** | 🟡 candidate (~13 mornings/yr) |
| O6 | Same-source check (bars_5m only, 2024-26): is the panic rebound just the opening print? | PANIC (30 mornings): buy at first trade → close +182bp (t 2.2), 09:35 +166 (t 2.2), 09:45 +156 (t 2.0), 10:05 +88. Hold overnight positions to the close +182bp vs selling at the open. CALM: buy −10 to −22bp; flat/gap-up mornings: selling later than the open is WORSE (−6 to −14bp, t −2.2 at 09:35) | ✅ tradeable after the open; MOO exit right on normal mornings |
| O7 | Buy the gap-down on UNSELECTED stocks (RTY ≤ −1% mornings, all WILD, official open→close) | hand-picked +87bp (t 2.8, 6/6 yrs); unselected 2021-23 +52bp (t 1.5, 3/4); all US commons 2024-26 +82bp (t 2.4, 3/3); normal mornings ≈ 0 | ✅ not a survivorship artifact |
| O8 | This week under the protocol | Oct 7 (RTY −1.01%) and Oct 8 (−0.55%) were both CALM ⇒ sell at the open (what we did), no dip buy. Holding instead would have cost −$145 (Oct 7) and −$471 (Oct 8) | ℹ️ it would not have saved this week — it avoids making it worse |
| O9 | Anything at 15:40 that warns of a calm overnight sell-off — fear gauge at entry (VIX level, VIX/VIX3M, 1-day change, vs 20d; same-day and lagged) | every quintile positive; "VIX jumped ≥5.4% today → skip tonight": train t −3.2, **test t −0.3** — failed out of sample | ❌ (adds to N1/N2: 15+ timing inputs, none predicts the next night) |
**Conclusion:** the overnight sell-off cannot be predicted at 15:40 with daily inputs (Night Owl is cross-sectional by
design — it ranks stocks against each other and is structurally blind to a market-wide gap). The information arrives by
09:25 (futures explain ~2/3 of the basket gap). The missing piece is a **morning gap-down protocol**: PANIC ⇒ hold the
overnight positions to the close and buy WILD names at the open; CALM/normal ⇒ sell at the open as today.

## P. Oct 9 2026 — "a model that calls tomorrow at >60%": 2026 weather, market timing, news, earnings, root cause
User: find the signal behind weeks like Oct 5-8, use 2026 weather, news, earnings, macro; >60% calls. Daily panel 2017-09 →
2026-10 (88 market-state measures known at the close: WILD/all/semis baskets, overnight vs intraday splits, breadth,
dispersion, trendiness, streaks, VIX/VIX3M, 10-yr yield, calendar, next-morning macro, sector earnings tonight), futures
15:40 snapshots (2021+), 2026 internal data (49k AI-tagged news items, scan_log, Field Report). Scratchpad: `weather_panel.py`,
`fut_features.py`, `y2026_features.py`, `model.py`, `dt_window.py`. Every model is walk-forward (each year predicted by a
model trained only on earlier years).
| # | Hypothesis | Result | Verdict |
|---|---|---|---|
| P1 | 2026 is a worse tape than 2024-25 | 2026 H2: sunny days 30% (vs 34-41%), stormy 37% (vs 25-28%), trendiness 0.23 (lowest), WILD **intraday −32bp/day** (worst half-year in 3 yrs) but WILD **overnight +19bp/night** (premium intact). After a STORMY day the next day's WILD intraday averages +38bp (rebound) | ✅ the daytime tape is the storm; nights are fine |
| P2 | A market-timing model calls tonight's WILD gap / our overnight rule / tomorrow's intraday | tonight's gap: 55.6-56.3% vs 57.8% "always up" (AUC 0.53-0.55); strict 15:40 info: AUC 0.50-0.51; overnight rule: AUC 0.51-0.56; tomorrow's intraday decided at 09:25 with the overnight futures gap: 49% (AUC 0.49). Big-move warning (tail): AUC 0.58-0.63 — size is predictable, direction is not | ❌ no >60% direction model |
| P3 | Skip the model's most bearish 20% of nights | EOD model: those nights ≈ 0bp vs +31 kept; Sharpe 1.47 → 1.69, better in 7/7 test years — but it leans on the yield LEVEL (5.3% is outside its history) and has called nearly every night "skip" since mid-Sep; would have skipped Oct 6 (−235bp) and held Oct 7 (−154). Change-only version: Sharpe 1.47 → 1.63, maxDD −67% → −47%, better 5/7 yrs but WORSE in 2024 and 2026 | 🟡 weak bear filter, overlaps the Basket Tide — not shipped |
| P4 | "Flip with the weather": trade the intraday WILD basket / day-trader pool in the direction of its recent drift (5-60 days) | −11 to −17bp/day, negative in 8-9 of 9 years; pool version −11 to −18bp/day. Daily intraday autocorr −0.06 | ❌ daytime weather does not persist |
| P5 | Days like this behave a known way next day (k-NN analogs on 16 state measures, causal scaling) | 20 nearest analogs of Oct 6 / Oct 7 → next night +4 / +7bp (up 55-65%) — history did not foresee −2.35% / −1.54%; analog vote accuracy 53.4% vs 57.6% base; next-day intraday 49.7% | ❌ |
| P6 | News drives tomorrow (stock-level, May-Oct 2026, 24,645 stock-days, demeaned vs peers) | news is priced into the GAP (bearish −13bp, bullish +9bp vs peers) and predicts nothing after it: day t −0.6/+0.1, night after t −0.2/−0.5; month signs flip. Our engine ingests most news in a 04:00 batch — the edge in news is speed we do not have | ❌ |
| P7 | Sector earnings tonight drive our overnight names (2018-26, 120k WILD name-nights, own report nights excluded) | 2+ same-sector peers reporting: −5 to −7bp vs basket (t −4.0 / −2.0), negative 7/9 yrs, no extra tail; semis alone slightly positive | 🟡 small tilt for the overnight pickers |
| P8 | Diversify the overnight books (top-3 → 5/10/20 names, combined CW+NO rank, tiered fees, $20k) | 3: +23.9bp Sharpe 1.72 · 5: +20.3 / 1.58 · 10: +16.5 / 1.36 · 20: +9.8 / 0.87; worst night −14.9% → −13.8% only — the risk is the market factor | ❌ concentration is right |
| P9 | A >60% stock-level call (Night Owl walk-forward, by score percentile) | top 5%: gap up 55.3% (by year 45-60%), beats basket 52%, but +33bp vs +5bp for the bottom 10% — the edge is SIZE, not hit rate; with Clockwork consistency ≥ 65% too: 58.2% up, +33bp | ℹ️ >60% is the wrong target; expected value is |
| P10 | ROOT CAUSE — split every 2026 live trade into market (WILD basket, same window) and picking parts | Day trader (same minutes, bars only): P&L −$1,596 = market **+$1,391** + picks **−$2,987**; −0.22%/trade (t −1.5), beats basket 38%; first 15 min after entry **−0.64%** vs basket (n 221), May +0.95%, Jun-Oct negative. Clockwork (official prices) picks −$269 (t −0.8), Night Owl −$213 (9 trades), Wave −$747 (t −0.15): noise-level each. This week: market −$918, picks −$1,177 | ✅ 2026's loss is SELECTION (mostly the day trader buying local tops), not weather |
| P11 | The day trader's TIMING has value even if its picks don't (WILD basket after each 2026 entry vs random times the same day, bars only) | +30m +0.014%, +60m +0.031%, to close −0.015% vs random; day-clustered t −0.34 / +0.13 / −0.75 (84 days) | ❌ no timing skill either |
| P12 | ⭐ FEAR STORM → next-day rebound. Stormy close (all names < −0.5%, breadth < 40%) split by whether VIX jumped ≥ 5% that day (known at the close) | 2018-26 official prints: after FEAR storm next-day WILD open→close **+32.7bp (t 2.35, 8/9 yrs, 2026 +69bp)**, n 351 (~40/yr); after CALM storm −5bp (3/9); other days −2bp. Bars only 2024-26: buy 09:35 **+70bp (t 2.5)**, 09:45 **+76bp (t 2.9)**, 10:05 +49bp, n 106 — NOT an opening-print artifact. 2026: 30 days, 09:45→close +0.80%/day, up 60%, ≈ +$2,100 on $10k after fees (live day trader 2026: −$1,596). Oct 7 / Oct 8 were CALM storms (VIX +0.5% / +2.2%) | ✅ strongest daytime edge found — a "Fear Rebound" mode for the day trader |
| P13 | Overnight books stand aside on FEAR-storm nights | those nights: CW+NO −3.7bp (t −0.3, 4/9 yrs, sd 238bp) vs +30.1bp other nights; standing aside: Sharpe 1.29 → 1.64, 111% of the return kept; on top of the Basket Tide 1.72 → 2.07 (6/8 yrs better) — but **2026 worse (−0.92 Sharpe)** | 🟡 historically strong, cost this year — shadow first |
| P14 | Robustness of P12/P13 to the thresholds (market −0.3/−0.5/−0.75/−1%, breadth 35/40/45%, VIX jump 3/5/8%) | all 36 cells: overnight Sharpe 1.41-1.67 (base 1.29), rebound +24 to +46bp (t 1.8-2.6) — a plateau, not a tuned point. Backtest reads the 16:00 close / VIX close. **Checked Oct 9:** the 15:45 reading (bars_5m universe + yfinance 5-min VIX) gave the SAME call as the close on 49/49 sessions (Aug 4-Oct 8; 15 storm days, 6 FEAR) — the overnight stand-aside can be decided live at 15:45; the next-day rebound uses the full close anyway. Recent 6 FEAR storms' next days: 3 up / 3 down (−0.2% avg) — 2026's +0.80% came mostly from Feb-Mar and Jun-Jul | ✅ plateau; 15:45 reading verified |
**Synthesis (Oct 9):** daily market DIRECTION is not predictable at >60% from any data we hold (P2-P6, plus N1/N2/O9) — the
pros' edge is not direction calls. What IS measurable: (1) 2026's loss is selection, chiefly the day trader buying local tops
(P10); (2) the market's fear state at the close decides where tomorrow's edge sits — after a FEAR storm the overnight premium
vanishes and the next day rebounds; after a CALM storm (Oct 7/8) neither happens (P12-P14, O4-O6 at 09:25). One classifier —
the close-of-day fear state plus the 09:25 futures gap — drives all three actions: overnight stand-aside, next-day Fear
Rebound buy, and the morning hold-to-close on PANIC gap-downs. None of it would have saved Oct 7-8 (calm storms, surprise
gaps); it targets the other kind of bad week, and replaces the day trader's no-edge days with its one edge day.

## Q. Oct 9 2026 (pm) — follow-ups: which basket, traits, the bear side, repeat tickers
| # | Hypothesis | Result | Verdict |
|---|---|---|---|
| Q1 | The FEAR-storm rebound (P12) holds outside our hand-picked universe (official prints, next-day open→close, volatile third = top third by 60d vol, ≥$5, ≥$20M ADV) | OUR 240 names: +31.6bp (t 2.3), + 7/9 yrs. UNSELECTED A→OGG 2018-Jan 2024: **+6bp (t 0.5)** (2024 −56). ALL US commons 2024-26: **+55bp (t 2.6)** (2025 +112, 2026 +52). Calm third +10, middle +33 in 2024-26 | 🟡 market-wide only since 2025; earlier years flattered by our hindsight-picked list |
| Q2 | Which names rebound most after a FEAR storm | everywhere: the biggest losers on the storm day (our Q0 +38 vs Q4 +10bp; all-US +54 vs +20) and the biggest gap-downs that morning (our +43 vs +4; all-US +64 vs +13); more volatile > less | ✅ traits for a rebound basket |
| Q3 | A predictable DOWN cell to short — state (FEAR/calm/sunny/cloudy) × regime (Basket Tide ON/OFF) × window (WILD night/next day, RTY futures night/next day), 2018-26 | no cell beyond t −1.6; most negative: tide-OFF FEAR night RTY −22bp (t −1.5), WILD −17 (t −0.8); tide-ON calm-storm WILD night −3bp (7/8 yrs neg, tiny). Every strong cell is LONG | ❌ no equity/futures short edge in our data |
| Q4 | A stormy MORNING continues (WILD basket open→10:35 vs 10:35→close, bars 2024-26, 674 sessions) | corr 0.00 (10:05: 0.05); worst-morning quintile rest of day −2bp (t −0.1); "morning storm" (< −1%, < 30% up) +4bp (t 0.3); best mornings +16 (t 0.8) | ❌ no intraday short / momentum signal |
| Q5 | Old equity bear book (269 live trades May-Aug 2026, +$134, 28% WR) | +$496 on calm-storm days, +$63 FEAR days, −$371 up days, −$54 mildly-down days — but day type is the day's OWN close (look-ahead); combined with Q3/Q4 there is no way to know early | ❌ retirement stands |
| Q6 | Clockwork / Night Owl repeat the same tickers — bug or design? | Clockwork keeps 2.2 of 3 names night to night live vs 2.08 in its 9-yr replay (by design: 30-day signal). BUT on **14/14 live nights an alphabetical tie decided a pick**; 62% of picks start A-C vs 29% of its WILD list (why CENX is held every night). Tie-break test 2018-26 on the live list: alphabetical +33.5bp Sharpe 2.73 · random +35.3 / 2.98 · Night Owl score +35.8 / 2.97 · 10-day consistency +35.1 / 2.90; all ≈ +19bp in 2026. Night Owl: continuous score, no ties; keeps 1.5 of 3 live vs 0.7-0.9 in backtest (MRVL 5/5 nights, semis run) — genuine model preference | ✅ Clockwork tie-break is a design flaw (concentration, slightly worse Sharpe) — fix = break ties by Night Owl score; Night Owl OK |

## R. Oct 9 2026 (evening) — thresholds derived, new feeds checked, day-trader execution, builds
| # | Hypothesis | Result | Verdict |
|---|---|---|---|
| R1 | Are the FEAR thresholds (market −0.5%, breadth 40%, VIX +5%) data-driven? | They were chosen a priori (round numbers). Checked: (a) one condition at a time the rebound grows smoothly as each tightens, no spike; (b) ablation — VIX jump ≥5% alone +27bp (t 2.2), + market down +32bp (t 2.3), + breadth +33bp (t 2.4): the VIX jump carries it, breadth adds nothing; storm without the jump −5bp; (c) thresholds grid-searched on 2018-21 did WORSE on 2022-26 (+14bp t 1.0) than the round rule (+43bp t 2.0) — corr(train t, test t) across 150 cells −0.68, yet 100% of cells positive in the test and 81% with t > 1.5; (d) a linear model with no thresholds fails (top decile +12bp t 0.5) — it is a state effect | ✅ sound; simplified to **VIX up ≥5% AND universe down ≥0.5%**; do not tune |
| R2 | New feed: sector / cross-asset ETFs (SPY QQQ IWM SMH SOXX XLK XLE XLF XBI ARKK HYG TLT UUP GLD BTC VIX9D) | overnight model AUC 0.557 → 0.550 with them (no help). On FEAR days the **dollar** splits it: dollar down that day → next day +97bp (t 2.8, 8/10 yrs); flat +38bp; dollar up −4bp (4/10). Post-hoc (1 of 24 splits) | 🟡 recorded daily (`uup_ret`), not gated |
| R3 | New feed: closing-auction imbalance (IBKR generic tick 225) | ib_async exposes auctionVolume/Price/Imbalance/regulatoryImbalance; no history exists to backtest (exchange-sold); published from 15:50 — after our 15:41-15:49 MOC entries, and the NYSE MOC cutoff is 15:50 (Nasdaq 15:55), so even a useful signal is only partly usable | ⏳ collecting from Mon Oct 12 15:52 (`auction_imbalance`, separate read-only connection, clientId 77) |
| R4 | Day trader: is the picking loss really execution? (entries since May 29 — real fills; before that the fill was recorded = signal price) | fill vs signal price +0.09% avg (+0.10..+0.25% every month since Jun). Edge vs basket by entry situation: **chased (>+0.25% above signal) −1.40%/trade (n 62, t −3.2, every month)**; price already below signal −0.82% (n 38, t −2.5); at signal −0.39% (n 24); 0..+0.25% +0.04% (n 76). Booked P&L: kept by a −0.05%..+0.25% band +$271 (100 trades) vs skipped −$2,039 (100). Every band tried beats no band (−$1,283..+$271 vs −$1,768); skipped trades negative 6/6 months, kept positive 4/6. The +$271 cell is the grid's best — expect less | ✅ SHIPPED Oct 9 (live Mon Oct 12): `ENTRY_PRICE_BAND` — re-quote the ask, buy only within −0.05%..+0.25% of the signal with a LIMIT capped at the band top; every check logged to `entry_band_log`. Stops most of the bleed; does NOT create an edge |
| R5 | Clockwork's alphabetical tie-break | FIXED: ties broken by Night Owl's score, then a date-seeded shuffle (`rank_candidates`, tests `factory/tests/test_clockwork_tiebreak.py`). Tonight's 3rd slot: APLD (alphabetical) → PI (Night Owl best in a 7-way tie at 63%) | ✅ live from Mon Oct 12 15:40 |
**Built Oct 9:** `market_state.py` + launchd `com.sushil.trading.market_state` (09:26 / 15:52 / 18:15), backfilled 2018 (2,204
sessions) — reproduces the research: FEAR next day +32bp (t 2.3), 10 hardest-hit names +39bp (t 2.2, 8/9 yrs), PANIC mornings
+105bp, CALM gap-down mornings −3bp. Dashboard service row + glossary rows added.
