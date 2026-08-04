# research_strategy_family_sweep.py — Aug 3 2026
#
# Ideation follow-up: does the "mean reversion generalizes better than momentum
# across a diverse asset universe" finding (from an external video review, see
# docs/IDEATION_2026-08-03_strategy_and_options_dashboard_review.md) replicate
# on OUR equity universe? Research only — not wired into any trader.
#
# Scope, deliberately: EQUITY only. HMM is a separate, futures-scoped idea
# (targets the twice-documented chop/trend-separation problem — see
# futures_exit_stack_jul7 / futures_jul8_gap_hunt memory) and is not run here.
#
# Data: 2 years of daily bars via yfinance (user directive Aug 3 2026 — no 5yr
# runs for research going forward). 30-symbol sample pulled systematically
# (every 8th name) from FULL_UNIVERSE, not cherry-picked.
#
# Methodology: all parameters are textbook defaults (RSI 14/30/70, Zscore
# 20-day/±2, Keltner 20-EMA/2xATR, Donchian 20/10) — NOT fit to our data, so
# there is no separate in-sample/out-of-sample split; the whole 2yr window is
# the evaluation window and "OOS" in the sense of "not curve-fit," not in the
# sense of a held-out period. That's a real methodological difference from a
# proper walk-forward test — flagged, not glossed over.
#
# Command: venv/bin/python research_strategy_family_sweep.py

import sys, warnings
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import date, timedelta

warnings.filterwarnings('ignore')
sys.path.insert(0, '/Users/sushil/trading')

START = (date.today() - timedelta(days=730)).isoformat()
END   = date.today().isoformat()
MAX_HOLD = 10   # trading days, applies to all mean-reversion/RSI-momentum exits


def sample_universe(n=30):
    from auto_trader import FULL_UNIVERSE
    step = max(1, len(FULL_UNIVERSE) // n)
    return FULL_UNIVERSE[::step][:n]


def fetch(symbol):
    df = yf.Ticker(symbol).history(start=START, end=END, auto_adjust=True)
    if df.empty or len(df) < 60:
        return None
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    return df


def rsi(close, period=14):
    delta = close.diff()
    up   = delta.clip(lower=0).rolling(period).mean()
    down = (-delta.clip(upper=0)).rolling(period).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df, period=14):
    hl = df['High'] - df['Low']
    hc = (df['High'] - df['Close'].shift()).abs()
    lc = (df['Low']  - df['Close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def run_positions(entry_sig: pd.Series, exit_sig: pd.Series, max_hold: int) -> pd.Series:
    """Path-dependent long-only position series: enter on entry_sig (applied
    next bar), hold until exit_sig true or max_hold bars elapse, whichever
    first. No pyramiding — one position at a time."""
    pos = pd.Series(0, index=entry_sig.index)
    in_pos, bars_held = False, 0
    for i in range(1, len(entry_sig)):
        if in_pos:
            bars_held += 1
            pos.iloc[i] = 1
            if exit_sig.iloc[i] or bars_held >= max_hold:
                in_pos = False
        elif entry_sig.iloc[i - 1]:   # signal on close[i-1] -> position starts at bar i (next open)
            in_pos, bars_held = True, 0
            pos.iloc[i] = 1
    return pos


def strat_rsi_revert(df):
    r = rsi(df['Close'])
    return run_positions(r < 30, r > 50, MAX_HOLD)

def strat_rsi_momentum(df):
    """Mirrors our own live finding (analysis_pending.md 'RSI Paradox'):
    RSI 80+ tends to continue, not revert. Test the opposite premise of
    strat_rsi_revert on the identical indicator for a clean A/B."""
    r = rsi(df['Close'])
    return run_positions(r > 70, r < 50, MAX_HOLD)

def strat_zscore_revert(df):
    m = df['Close'].rolling(20).mean()
    s = df['Close'].rolling(20).std()
    z = (df['Close'] - m) / s.replace(0, np.nan)
    return run_positions(z < -2.0, z >= 0.0, MAX_HOLD)

def strat_keltner_revert(df):
    ema = df['Close'].ewm(span=20).mean()
    a = atr(df, 14)
    lower = ema - 2 * a
    return run_positions(df['Close'] < lower, df['Close'] >= ema, MAX_HOLD)

def strat_donchian_breakout(df):
    hi20 = df['High'].rolling(20).max().shift(1)
    lo10 = df['Low'].rolling(10).min().shift(1)
    return run_positions(df['Close'] > hi20, df['Close'] < lo10, max_hold=252)  # trend rides, cap loose


STRATEGIES = {
    'RSI Revert':        ('Mean Reversion', strat_rsi_revert),
    'Zscore Revert':     ('Mean Reversion', strat_zscore_revert),
    'Keltner Revert':    ('Mean Reversion', strat_keltner_revert),
    'RSI Momentum':      ('Momentum',       strat_rsi_momentum),
    'Donchian Breakout': ('Momentum',       strat_donchian_breakout),
}


def sharpe(daily_ret: pd.Series) -> float | None:
    active = daily_ret[daily_ret != 0]
    if len(active) < 15 or daily_ret.std() == 0:
        return None
    return round(daily_ret.mean() / daily_ret.std() * np.sqrt(252), 3)


def main():
    universe = sample_universe(30)
    print(f"Universe sample ({len(universe)}): {universe}")
    print(f"Window: {START} -> {END}  |  max hold {MAX_HOLD}d (Donchian uncapped-ish)\n")

    results = []  # (strategy, category, symbol, sharpe, n_days_in_pos)
    data_cache = {}
    for sym in universe:
        df = fetch(sym)
        if df is None:
            print(f"  skip {sym}: insufficient data")
            continue
        data_cache[sym] = df

    for name, (cat, fn) in STRATEGIES.items():
        for sym, df in data_cache.items():
            try:
                pos = fn(df)
                daily_pct = df['Close'].pct_change().fillna(0)
                strat_ret = (pos.shift(1).fillna(0) * daily_pct)
                sh = sharpe(strat_ret)
                if sh is not None:
                    results.append((name, cat, sym, sh, int(pos.sum())))
            except Exception as e:
                print(f"  error {name}/{sym}: {e}")

    res = pd.DataFrame(results, columns=['strategy', 'category', 'symbol', 'sharpe', 'days_in_pos'])
    res = res[res['days_in_pos'] >= 15]   # require a minimum amount of activity to count as a real test

    print("=" * 70)
    print("CATEGORY SUMMARY (mean Sharpe, % of symbol-runs with Sharpe > 0)")
    print("=" * 70)
    cat_summary = res.groupby('category').agg(
        mean_sharpe=('sharpe', 'mean'),
        pct_positive=('sharpe', lambda s: round((s > 0).mean() * 100, 1)),
        n=('sharpe', 'count'),
    ).sort_values('mean_sharpe', ascending=False)
    print(cat_summary.to_string())

    print("\n" + "=" * 70)
    print("PER-STRATEGY SUMMARY")
    print("=" * 70)
    strat_summary = res.groupby(['category', 'strategy']).agg(
        mean_sharpe=('sharpe', 'mean'),
        pct_positive=('sharpe', lambda s: round((s > 0).mean() * 100, 1)),
        n=('sharpe', 'count'),
    ).sort_values('mean_sharpe', ascending=False)
    print(strat_summary.to_string())

    print("\n" + "=" * 70)
    print("TOP 15 INDIVIDUAL SYMBOL x STRATEGY RESULTS")
    print("=" * 70)
    print(res.sort_values('sharpe', ascending=False).head(15).to_string(index=False))

    res.to_csv('/Users/sushil/trading/research_strategy_family_sweep_results.csv', index=False)
    print("\nFull results -> research_strategy_family_sweep_results.csv")


if __name__ == '__main__':
    main()
