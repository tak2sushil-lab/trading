# Archive — One-Time Experiment Backtests

These files answered a specific question and are no longer part of the active regression suite.
Do not delete — they are the evidence trail for every parameter decision in the live system.

| File | Question Asked | Conclusion |
|------|----------------|------------|
| `backtest_afternoon_short.py` | Should we gate afternoon entries when morning is already profitable? And why is the short side underperforming? | **Applied May 22 2026.** Afternoon gate: no new entries after 12pm if morning realized ≥$150 (LONG 44.8% WR afternoon vs 58.5% morning; SHORT 18.2% vs afternoon). Short side deep-dive confirmed bear WR drag. |
| `backtest_bear_universe.py` | Does flip-exit 3 scans (vs 2) help? Why do RIVN/EOSE/VST/CLS/CCJ keep losing short? Does adding mega-caps (AMZN/GOOGL/META) add value? | **3-scan flip applied May 22 (+$754/yr).** Short symbol issues noted in sector grade system. Universe expansion deferred until 3+ months live. |
| `backtest_calibrate_exits.py` | Parameter sweep for Gap2 (slow-bleed cut), P&L protection, and correlation exit. What's the optimal threshold? | **P&L protection calibrated to 25% drop from peak ≥$200.** Gap2 parameters (90 min, -1%, max_gain<1.5%) validated. Correlation exit tested but not applied (false positive rate too high). |
| `backtest_choppy_normal.py` | When morning is CHOPPY and resolves NORMAL after 1pm, is there a tradeable edge? | **Not built.** Edge not consistent enough — depends heavily on regime quality at transition point. |
| `backtest_dynamic_exits.py` | Evaluated 4 dynamic exit options: Gap2 slow-bleed, P&L protection floor, correlation exit, all combined. | **Gap2 and P&L protection both applied May 22 2026.** Correlation exit validated as too noisy for current trade count. |
| `backtest_early_mover.py` | Does entering at 9:35/9:45 instead of 10:00 improve results? | **REJECTED.** Early entry was -$271 worse over 2 weeks. Do not revisit without new data. Rule locked in CLAUDE.md. |
| `backtest_gap.py` | Gap-and-go strategy — 5-year backtest. Do stocks gapping 4%+ at open continue or fade? | **Informed MIN_TODAY_GAIN=3% and gap-and-crap hard gate.** Pattern continues 60%+ of the time in STRONG regime. |
| `backtest_gap_fixes.py` | Test Gap2 (slow-bleed cut at max_gain<1.5%) and Gap1 (PCT trail in the +1–2.4% dead zone). | **Both applied.** Gap2 became no-move-240min with pattern check. Gap1 became PCT trail (activates at +1.5%). |
| `backtest_sympathy.py` | When a sector leader beats/misses big, do sympathy stocks gap and hold? Is pre-market entry justified? | **Sympathy scanner built and in auto_trader. Entry deferred.** Need 30+ live sympathy trades before enabling pre-market entries. Revisit Q3 2026. |
| `backtest_velocity.py` | Does a momentum velocity filter at entry time (blocking "slow" movers) add edge? | **Not implemented.** Velocity conditions individually not strong enough to add as a gate. May revisit with scan_log data after 200+ logged candidates. |
| `backtest_weak_confirmation.py` | Post-lunch WEAK signals: is 2 scans sufficient if first scan is strong (SPY <-0.7%)? | **3 scans validated as correct.** Strong first-scan signals still reversed 30%+ of the time. Applied May 22 2026. |

| `sim_compare_firstbar.py` | Does FIRST_BAR_QUALITY gate improve results? A/B test with False vs True across 10 days. | **APPLIED.** True wins +$71 over 10 days, better 7/10 days. Feature live. No further use. |

## How to Reactivate an Archived Experiment

If you want to re-run one of these (e.g., velocity with scan_log data):
1. Copy the file back to the root trading directory
2. Update imports if database schema has changed
3. Run it — check against current live results, not 2026 paper data
4. If conclusion changes, update this README and the relevant entry in CLAUDE.md

## Active Regression Suite (in `/Users/sushil/trading/`)

The 5 files that run in the regression suite:
- `backtest_strategy.py` — bull edge, full universe
- `backtest_bear.py` — bear/short edge
- `backtest_walkforward.py` — OOS walk-forward (8 windows)
- `backtest_stress.py` — crisis periods (COVID, 2022, carry trade)
- `monte_carlo.py` — ruin risk simulation

Run all 5 at once: `./run_regression.sh`
