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
