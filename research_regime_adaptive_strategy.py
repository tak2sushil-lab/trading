# research_regime_adaptive_strategy.py — Aug 3 2026
#
# Direct answer to: "does switching strategy (reversion vs momentum) as the
# market regime changes reduce whipsaw vs running one strategy everywhere?"
# Not blocked by live data — reuses the same 2yr universe sample and signal
# logic as research_strategy_family_sweep.py, adds a day-level market regime
# label and compares 3 portfolios' equity curves.
#
# SIMPLIFICATION, stated plainly: this uses a DAILY-BAR proxy for market
# regime (SPY day return% + a simple flip-count chop measure), not the live
# get_regime() function (which needs 5-min intraday SPY bars — our own
# collector's SPY feed has been dead since Jun 1 2026, see CLAUDE.md). This
# is a reasonable stand-in for a first-pass answer, not a claim that it
# reproduces the live classifier exactly.
#
# Whipsaw metrics reported: max drawdown, Sharpe, and a "reversal day" count
# (a losing portfolio day immediately following 2+ winning days — a concrete,
# interpretable proxy for "won it then gave it back").
#
# Command: venv/bin/python research_regime_adaptive_strategy.py

import sys, warnings
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import date, timedelta

warnings.filterwarnings('ignore')
sys.path.insert(0, '/Users/sushil/trading')

from research_strategy_family_sweep import (
    sample_universe, fetch, run_positions,
    strat_rsi_revert, strat_zscore_revert, strat_keltner_revert,
    strat_rsi_momentum, strat_donchian_breakout, START, END,
)


def spy_regime_daily():
    """Daily-bar proxy regime label per day: CHOPPY / TRENDING / NORMAL.
    CHOPPY: small net move with high directional flip-flop (proxied here by
    a small |return| combined with a small realized range, since we don't
    have intraday flip-count on daily bars). TRENDING: large net move.
    NORMAL: everything else. Thresholds are illustrative, not fit."""
    spy = yf.Ticker('SPY').history(start=START, end=END, auto_adjust=True)
    spy['ret_pct'] = spy['Close'].pct_change() * 100
    spy['range_pct'] = (spy['High'] - spy['Low']) / spy['Open'] * 100

    def label(row):
        if abs(row['ret_pct']) < 0.25 and row['range_pct'] < 0.8:
            return 'CHOPPY'
        if abs(row['ret_pct']) >= 1.0:
            return 'TRENDING'
        return 'NORMAL'

    spy['regime'] = spy.apply(label, axis=1)
    return spy['regime']


def portfolio_returns(symbol_positions: dict, daily_rets: dict, mask: pd.Series = None) -> pd.Series:
    """Equal-weight combine per-symbol strategy returns into one portfolio
    daily-return series. mask, if given, zeroes out days outside the regime
    this approach is 'switched on' for (used by the ADAPTIVE portfolio)."""
    all_days = sorted(set().union(*[r.index for r in daily_rets.values()]))
    port = pd.Series(0.0, index=all_days)
    counts = pd.Series(0, index=all_days)
    for sym, ret in daily_rets.items():
        r = ret.reindex(all_days).fillna(0.0)
        if mask is not None:
            m = mask.reindex(all_days).fillna(False)
            r = r.where(m, 0.0)
        active = symbol_positions[sym].reindex(all_days).fillna(0) > 0
        if mask is not None:
            active = active & mask.reindex(all_days).fillna(False)
        port += r
        counts += active.astype(int)
    counts = counts.replace(0, np.nan)
    return (port / counts).fillna(0.0)


def metrics(daily_ret: pd.Series, label: str):
    cum = (1 + daily_ret).cumprod()
    peak = cum.cummax()
    dd = (cum / peak - 1)
    max_dd = dd.min() * 100
    sh = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    # Reversal-day whipsaw proxy: losing day immediately after >=2 winning days
    wins = (daily_ret > 0).astype(int)
    win_streak = wins.groupby((wins != wins.shift()).cumsum()).cumsum()
    reversal_days = int(((win_streak.shift(1) >= 2) & (daily_ret < 0)).sum())
    total_active_days = int((daily_ret != 0).sum())
    print(f"{label:22} Sharpe={sh:6.2f}  MaxDD={max_dd:7.2f}%  "
          f"reversal_days={reversal_days:4d}  active_days={total_active_days:4d}  "
          f"reversal_rate={reversal_days/max(total_active_days,1)*100:5.1f}%")
    return sh, max_dd, reversal_days


def main():
    universe = sample_universe(30)
    print(f"Universe: {len(universe)} symbols | {START} -> {END}\n")
    regime = spy_regime_daily()
    print("SPY regime day-counts:", regime.value_counts().to_dict(), "\n")

    positions = {'rev': {}, 'mom': {}}
    daily_ret = {'rev': {}, 'mom': {}}

    for sym in universe:
        df = fetch(sym)
        if df is None:
            continue
        pct = df['Close'].pct_change().fillna(0)

        # Best-of-family per symbol (mirrors the earlier sweep's "pick the winner")
        rev_candidates = {'RSI Revert': strat_rsi_revert(df), 'Zscore Revert': strat_zscore_revert(df),
                          'Keltner Revert': strat_keltner_revert(df)}
        mom_candidates = {'RSI Momentum': strat_rsi_momentum(df), 'Donchian Breakout': strat_donchian_breakout(df)}

        def best(cands):
            scored = []
            for name, pos in cands.items():
                r = pos.shift(1).fillna(0) * pct
                sh = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else -999
                scored.append((sh, name, pos))
            return max(scored, key=lambda x: x[0])

        _, _, rev_pos = best(rev_candidates)
        _, _, mom_pos = best(mom_candidates)

        rev_pos.index = df.index
        mom_pos.index = df.index
        positions['rev'][sym] = rev_pos
        positions['mom'][sym] = mom_pos
        daily_ret['rev'][sym] = (rev_pos.shift(1).fillna(0) * pct)
        daily_ret['mom'][sym] = (mom_pos.shift(1).fillna(0) * pct)

    regime_dates = regime.copy()
    regime_dates.index = pd.to_datetime(regime_dates.index).tz_localize(None)
    choppy_mask  = (regime_dates == 'CHOPPY') | (regime_dates == 'NORMAL')
    trending_mask = (regime_dates == 'TRENDING')

    def align_idx(s):
        s2 = s.copy(); s2.index = pd.to_datetime(s2.index).tz_localize(None); return s2
    positions = {k: {sym: align_idx(p) for sym, p in d.items()} for k, d in positions.items()}
    daily_ret = {k: {sym: align_idx(r) for sym, r in d.items()} for k, d in daily_ret.items()}

    print("=" * 78)
    print("PORTFOLIO COMPARISON: static momentum vs static reversion vs regime-adaptive")
    print("=" * 78)
    static_mom  = portfolio_returns(positions['mom'], daily_ret['mom'])
    static_rev  = portfolio_returns(positions['rev'], daily_ret['rev'])
    # Adaptive: reversion signals on CHOPPY/NORMAL days, momentum signals on TRENDING days
    adaptive_rev_leg = portfolio_returns(positions['rev'], daily_ret['rev'], mask=choppy_mask)
    adaptive_mom_leg = portfolio_returns(positions['mom'], daily_ret['mom'], mask=trending_mask)
    all_idx = sorted(set(adaptive_rev_leg.index) | set(adaptive_mom_leg.index))
    adaptive = (adaptive_rev_leg.reindex(all_idx).fillna(0) + adaptive_mom_leg.reindex(all_idx).fillna(0))

    metrics(static_mom, "STATIC-MOMENTUM")
    metrics(static_rev, "STATIC-REVERSION")
    metrics(adaptive,   "REGIME-ADAPTIVE")

    out = pd.DataFrame({'static_momentum': static_mom, 'static_reversion': static_rev}).join(
        pd.DataFrame({'adaptive': adaptive}), how='outer')
    out.to_csv('/Users/sushil/trading/research_regime_adaptive_results.csv')
    print("\nFull daily series -> research_regime_adaptive_results.csv")


if __name__ == '__main__':
    main()
