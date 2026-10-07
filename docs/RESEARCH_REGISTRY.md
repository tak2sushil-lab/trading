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
