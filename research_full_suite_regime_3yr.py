# research_full_suite_regime_3yr.py — Aug 5 2026
#
# 3-year re-run of research_full_suite_regime.py (which stays at 2yr, unmodified,
# for side-by-side comparison — this is a separate file, not an overwrite).
# Purpose: does the regime->strategy baseline "story" from the 2yr run still
# hold with a third year of data, or was it specific to the last 2 years'
# market conditions? Same methodology throughout, only START changes.
#
# The comprehensive baseline the user asked for: full strategy suite (reversion
# + momentum + trend-strength families), BOTH long and short, tested against a
# 5-state daily regime proxy (STRONG/NORMAL/CAUTIOUS/WEAK/CHOPPY, mirroring the
# live get_regime() thresholds as closely as daily bars allow), across a 50-
# symbol systematic sample of FULL_UNIVERSE, 3yr window (extended from 2yr per
# user request Aug 5, to check whether the mapping is durable, not a 2yr fluke).
#
# Output: for every (strategy, direction, regime) cell, Sharpe/WR/n — directly
# answers "241 candidates follow what strategy on what regime."
#
# Honest simplifications, stated up front, not hidden:
#   - Exits are signal-reversal + max-hold, not a simulation of the live
#     stop-loss/trailing-stop stack. A further-refinement item if this
#     direction proceeds past the baseline stage.
#   - Regime is a DAILY-BAR proxy of get_regime() (real one needs 5-min SPY
#     intraday, which the collector hasn't had since Jun 1) — same caveat as
#     every regime-adjacent research script this week.
#   - 50 symbols, not the full 241 — a systematic (every-Nth), non-cherry-
#     picked sample, chosen for tractable runtime on a first pass.
#   - Dual Momentum (cross-asset rotation) and Triple Screen (multi-timeframe)
#     don't fit cleanly into a per-symbol daily-bar sweep — not included this
#     pass. Everything else from the video is: RSI/Zscore/Keltner Revert,
#     RSI Momentum, Donchian Breakout (=Turtle), ADX Trend, MFI Revert — each
#     tested LONG and SHORT.
#
# Command: venv/bin/python research_full_suite_regime.py

import sys, warnings
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import date, timedelta

warnings.filterwarnings('ignore')
sys.path.insert(0, '/Users/sushil/trading')

START = (date.today() - timedelta(days=1095)).isoformat()  # 3yr — extended from 730 (2yr)
END   = date.today().isoformat()
MAX_HOLD = 10


def sample_universe(n=50):
    from auto_trader import FULL_UNIVERSE
    step = max(1, len(FULL_UNIVERSE) // n)
    return FULL_UNIVERSE[::step][:n]


def fetch(symbol):
    df = yf.Ticker(symbol).history(start=START, end=END, auto_adjust=True)
    if df.empty or len(df) < 60:
        return None
    return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()


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
    return pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(period).mean()


def adx(df, period=14):
    up_move = df['High'].diff()
    down_move = -df['Low'].diff()
    plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = atr(df, period)
    plus_di  = 100 * pd.Series(plus_dm, index=df.index).rolling(period).mean() / tr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(period).mean() / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.rolling(period).mean()


def mfi(df, period=14):
    tp = (df['High'] + df['Low'] + df['Close']) / 3
    mf = tp * df['Volume']
    pos_mf = mf.where(tp > tp.shift(), 0.0).rolling(period).sum()
    neg_mf = mf.where(tp < tp.shift(), 0.0).rolling(period).sum()
    mfr = pos_mf / neg_mf.replace(0, np.nan)
    return 100 - (100 / (1 + mfr))


def run_positions(entry_sig, exit_sig, max_hold, short=False):
    pos = pd.Series(0, index=entry_sig.index)
    in_pos, bars_held = False, 0
    for i in range(1, len(entry_sig)):
        if in_pos:
            bars_held += 1
            pos.iloc[i] = -1 if short else 1
            if exit_sig.iloc[i] or bars_held >= max_hold:
                in_pos = False
        elif entry_sig.iloc[i - 1]:
            in_pos, bars_held = True, 0
            pos.iloc[i] = -1 if short else 1
    return pos


def spy_regime_daily():
    """5-state daily-bar proxy of get_regime() (STRONG/NORMAL/CAUTIOUS/WEAK/CHOPPY).
    Real classifier needs 5-min intraday (VWAP position, VIX direction) we
    don't have for the full window — this mirrors its spirit on daily bars."""
    spy = yf.Ticker('SPY').history(start=START, end=END, auto_adjust=True)
    vix = yf.Ticker('^VIX').history(start=START, end=END, auto_adjust=True)['Close']
    # yfinance returns SPY in America/New_York and ^VIX in America/Chicago tz —
    # same calendar dates, different tz labels, so a raw join 100% NaNs out.
    # Normalize both to tz-naive dates before joining.
    spy.index = pd.to_datetime(spy.index).tz_localize(None)
    vix.index = pd.to_datetime(vix.index).tz_localize(None)
    spy['ret_pct'] = spy['Close'].pct_change() * 100
    diffs = spy['Close'].diff()
    flip = (diffs * diffs.shift()).lt(0).rolling(5).sum()  # reversal count, 5-day proxy for chop
    spy['choppy'] = (flip >= 3) & (spy['ret_pct'].abs() < 0.5)
    spy = spy.join(vix.rename('vix'), how='left').ffill()

    def label(r):
        if r['choppy']:
            return 'CHOPPY'
        if r['ret_pct'] < -0.5 or r['vix'] > 28:
            return 'WEAK'
        if r['ret_pct'] >= 0.5 and r['vix'] < 22:
            return 'STRONG'
        if r['ret_pct'] >= 0 and r['vix'] < 25:
            return 'NORMAL'
        return 'CAUTIOUS'
    spy['regime'] = spy.apply(label, axis=1)
    return spy['regime'].copy()


STRATEGIES = {
    'RSI Revert':     lambda df: (rsi(df['Close']) < 30, rsi(df['Close']) > 50),
    'Zscore Revert':  lambda df: (
        (df['Close'] - df['Close'].rolling(20).mean()) / df['Close'].rolling(20).std() < -2.0,
        (df['Close'] - df['Close'].rolling(20).mean()) / df['Close'].rolling(20).std() >= 0.0),
    'Keltner Revert': lambda df: (
        df['Close'] < df['Close'].ewm(span=20).mean() - 2 * atr(df),
        df['Close'] >= df['Close'].ewm(span=20).mean()),
    'MFI Revert':     lambda df: (mfi(df) < 20, mfi(df) > 50),
    'RSI Momentum':   lambda df: (rsi(df['Close']) > 70, rsi(df['Close']) < 50),
    'Donchian/Turtle':lambda df: (
        df['Close'] > df['High'].rolling(20).max().shift(1),
        df['Close'] < df['Low'].rolling(10).min().shift(1)),
    'ADX Trend':      lambda df: (
        (df['Close'] > df['Close'].shift(5)) & (adx(df) > 25),
        (df['Close'] < df['Close'].shift(5)) | (adx(df) < 20)),
}
# SHORT versions mirror the LONG entry logic on the opposite side
STRATEGIES_SHORT = {
    'RSI Revert':     lambda df: (rsi(df['Close']) > 70, rsi(df['Close']) < 50),
    'Zscore Revert':  lambda df: (
        (df['Close'] - df['Close'].rolling(20).mean()) / df['Close'].rolling(20).std() > 2.0,
        (df['Close'] - df['Close'].rolling(20).mean()) / df['Close'].rolling(20).std() <= 0.0),
    'Keltner Revert': lambda df: (
        df['Close'] > df['Close'].ewm(span=20).mean() + 2 * atr(df),
        df['Close'] <= df['Close'].ewm(span=20).mean()),
    'MFI Revert':     lambda df: (mfi(df) > 80, mfi(df) < 50),
    'RSI Momentum':   lambda df: (rsi(df['Close']) < 30, rsi(df['Close']) > 50),
    'Donchian/Turtle':lambda df: (
        df['Close'] < df['Low'].rolling(20).min().shift(1),
        df['Close'] > df['High'].rolling(10).max().shift(1)),
    'ADX Trend':      lambda df: (
        (df['Close'] < df['Close'].shift(5)) & (adx(df) > 25),
        (df['Close'] > df['Close'].shift(5)) | (adx(df) < 20)),
}
FAMILY = {'RSI Revert': 'Reversion', 'Zscore Revert': 'Reversion', 'Keltner Revert': 'Reversion',
          'MFI Revert': 'Reversion', 'RSI Momentum': 'Momentum', 'Donchian/Turtle': 'Momentum',
          'ADX Trend': 'Momentum'}


def sharpe(daily_ret):
    active = daily_ret[daily_ret != 0]
    if len(active) < 15 or daily_ret.std() == 0:
        return None
    return round(daily_ret.mean() / daily_ret.std() * np.sqrt(252), 3)


def main():
    universe = sample_universe(50)
    print(f"Universe sample ({len(universe)}): {universe}\n")
    regime = spy_regime_daily()
    print("Regime day-counts:", regime.value_counts().to_dict(), "\n")

    results = []
    for sym in universe:
        df = fetch(sym)
        if df is None:
            continue
        df.index = pd.to_datetime(df.index).tz_localize(None)
        daily_pct = df['Close'].pct_change().fillna(0)
        sym_regime = regime.reindex(df.index).ffill()

        for name, fn in STRATEGIES.items():
            for direction, spec in [('LONG', STRATEGIES), ('SHORT', STRATEGIES_SHORT)]:
                entry_sig, exit_sig = spec[name](df)
                short = (direction == 'SHORT')
                pos = run_positions(entry_sig.fillna(False), exit_sig.fillna(False), MAX_HOLD, short=short)
                # pos is +1 while long-in-position, -1 while short-in-position, 0 flat.
                # P&L = position sign x next-bar price return (short profits when price falls).
                strat_ret = pos.shift(1).fillna(0) * daily_pct

                for reg_name, reg_mask in [('ALL', pd.Series(True, index=df.index))] + \
                                          [(r, sym_regime == r) for r in ['STRONG','NORMAL','CAUTIOUS','WEAK','CHOPPY']]:
                    masked_ret = strat_ret.where(reg_mask.reindex(strat_ret.index).fillna(False), 0.0)
                    sh = sharpe(masked_ret)
                    if sh is not None:
                        active_days = int((masked_ret != 0).sum())
                        wr = (masked_ret[masked_ret != 0] > 0).mean() * 100
                        results.append({'strategy': name, 'family': FAMILY[name], 'direction': direction,
                                        'symbol': sym, 'regime': reg_name, 'sharpe': sh,
                                        'wr': round(wr, 1), 'active_days': active_days})

    res = pd.DataFrame(results)
    res = res[res['active_days'] >= 15]
    res.to_csv('/Users/sushil/trading/research_full_suite_regime_3yr_results.csv', index=False)

    print("=" * 90)
    print("BASELINE MAP — mean Sharpe by (strategy, direction) x regime, ALL = unconditional")
    print("=" * 90)
    pivot = res.pivot_table(index=['family', 'strategy', 'direction'], columns='regime',
                            values='sharpe', aggfunc='mean')
    cols = [c for c in ['ALL', 'STRONG', 'NORMAL', 'CAUTIOUS', 'WEAK', 'CHOPPY'] if c in pivot.columns]
    print(pivot[cols].round(2).to_string())

    print("\n" + "=" * 90)
    print("SAMPLE SIZE (n symbol-runs) per (strategy, direction) x regime — trust nothing under ~15")
    print("=" * 90)
    pivot_n = res.pivot_table(index=['family', 'strategy', 'direction'], columns='regime',
                              values='sharpe', aggfunc='count')
    print(pivot_n[cols].to_string())

    print("\n" + "=" * 90)
    print("BEST (strategy, direction) PER REGIME (by mean Sharpe, min n=15)")
    print("=" * 90)
    for r in ['STRONG', 'NORMAL', 'CAUTIOUS', 'WEAK', 'CHOPPY']:
        sub = res[res['regime'] == r].groupby(['family', 'strategy', 'direction']).agg(
            mean_sharpe=('sharpe', 'mean'), n=('sharpe', 'count')).reset_index()
        sub = sub[sub['n'] >= 15].sort_values('mean_sharpe', ascending=False)
        if len(sub):
            top = sub.iloc[0]
            print(f"{r:10} -> {top['family']:10} {top['strategy']:16} {top['direction']:5}  "
                  f"Sharpe={top['mean_sharpe']:+.2f}  n={top['n']}")
        else:
            print(f"{r:10} -> insufficient sample")

    print("\nFull results -> research_full_suite_regime_3yr_results.csv")


if __name__ == '__main__':
    main()
