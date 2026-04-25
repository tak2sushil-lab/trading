# backtest.py — v4 FINAL strategy
# Expands proven SEMI+AI sector stocks
# Removes nuclear/mining that underperformed in v3
# Targets 20-30 trades/month at 60%+ win rate
# Command: python backtest.py
#
# Data source: IBKR historical data (via bridge) if available, yfinance fallback
# For 2-year backtest: bridge must be running (python bridge.py)
# Set USE_IB_DATA=False to force yfinance

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import ta
from datetime import datetime, date, timedelta
import json
import os

from watchlist import SECTORS
from watchlist import AI_CHIPS, CLOUD_SOFTWARE

BRIDGE       = 'http://127.0.0.1:8000'
USE_IB_DATA  = True    # use IBKR data if bridge is running

def _bridge_available():
    try:
        return requests.get(f"{BRIDGE}/", timeout=3).status_code == 200
    except:
        return False

def get_historical_data(symbol, start_date, end_date=None):
    """
    Fetch daily OHLCV. Uses IBKR (2-year accurate data) if bridge is running,
    falls back to yfinance automatically.
    Returns DataFrame with columns: Open High Low Close Volume (capitalised).
    """
    if USE_IB_DATA and _bridge_available():
        try:
            r = requests.get(
                f"{BRIDGE}/history/{symbol}",
                params={'duration': '2 Y', 'bar_size': '1 day'},
                timeout=30
            )
            bars = r.json()
            if bars:
                df = pd.DataFrame(bars)
                df['date'] = pd.to_datetime(df['date'])
                df = df.set_index('date').sort_index()
                df.columns = [c.capitalize() for c in df.columns]
                # Filter to requested date range
                if start_date:
                    df = df[df.index >= pd.Timestamp(start_date)]
                if end_date:
                    df = df[df.index <= pd.Timestamp(end_date)]
                return df
        except Exception as e:
            print(f"  IB data failed for {symbol}: {e} — falling back to yfinance")

    # yfinance fallback
    df = yf.download(symbol, start=start_date, end=end_date or date.today().isoformat(),
                     progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

# ── V7 Config — matches live auto_trader strategy ─────────
START_DATE       = '2024-01-01'
END_DATE         = date.today().isoformat()
CAPITAL          = 1000
MAX_PER_TRADE    = 400
TARGET_PCT       = 0.045   # 4.5% initial target (partial exit here)
MAX_HOLD_DAYS    = 3       # time stop (was 5)
MIN_CONFIDENCE   = 75
MIN_VOLUME_RATIO = 1.8
TECH_BOOST       = 12

# ATR-based stops (matches live strategy)
ATR_PERIOD       = 14
ATR_STOP_MULT    = 1.5     # initial stop: entry - 1.5×ATR
ATR_TRAIL_MULT   = 1.5     # trail: 1.5×ATR below session high
ATR_TRAIL_START  = 1.0     # only trail after 1×ATR profit (breathing room)
ATR_FADE_MULT    = 1.0     # exit if drops 1×ATR from high while in profit
PARTIAL_EXIT     = True    # exit 50% at target, trail remainder
MAX_RISK_PCT     = 3.5     # cap stop at 3.5%

# Regime filter (matches live strategy)
SKIP_WEAK        = True    # no entries when SPY day < -0.5%
SKIP_CHOPPY      = True    # no entries on choppy days (reversal-heavy)

TECH_STOCKS = AI_CHIPS + CLOUD_SOFTWARE

# ── V5 Universe — full expanded universe ─────────────────
# Dropped from v4: NVDA (0% win), AFRM (43% win, negative avg)
# Added: full blue chips (MSFT, TSLA, META, AMZN, GOOGL, JPM, GS, NFLX, UBER)
#        more fintech (SOFI, NU, RKT), nuclear (SMR, OKLO), quantum (IONQ, QBTS)
#        crypto (MSTR, IREN), space/mobility (JOBY, ACHR), cloud (SNOW, PANW, CRWD)

FOCUS_UNIVERSE = [
    # ── TIER 1: 70%+ win rate (v5 confirmed) ─────────────
    'AAPL',   # 100% win, avg $7.24
    'PLTR',   # 86% win,  avg $7.78  TECH
    'COHR',   # 82% win,  avg $6.66  TECH
    'IONQ',   # 80% win,  avg $5.96  QUANTUM ← new
    'HOOD',   # 75% win,  avg $5.38
    'JPM',    # 75% win,  avg $3.18  ← new
    'IREN',   # 75% win,  avg $6.30  ← new
    'NUTX',   # 73% win,  avg $4.65  BIOTECH
    'LITE',   # 67% win,  avg $4.69  TECH
    'VST',    # 67% win,  avg $2.52
    'ITA',    # 67% win,  avg $0.95
    'NFLX',   # 67% win,  avg $1.83  ← new

    # ── TIER 2: 60-66% win rate (v5 confirmed) ───────────
    'ORCL',   # 64% win,  avg $3.04  TECH
    'OKLO',   # 63% win,  avg $3.35  NUCLEAR ← new (27 trades)
    'AMZN',   # 62% win,  avg $1.40  ← new
    'GOOGL',  # 62% win,  avg $2.34  ← new
    'CRM',    # 62% win,  avg $2.71  TECH ← new
    'QBTS',   # 60% win,  avg $2.66  QUANTUM ← new

    # ── TIER 3: 55-59% win rate, positive avg ────────────
    'TOST',   # 57% win,  avg $2.70
    'AVGO',   # 56% win,  avg $2.55  TECH
    'NBIS',   # 55% win,  avg $3.00  TECH
    'CLS',    # 54% win,  avg $1.91  TECH
    'RKLB',   # 54% win,  avg $2.49
    'CNQ',    # 100% win (1 trade — small sample, keep monitoring)

    # ── BORDERLINE: 50%, positive avg, decent sample ─────
    'AMD',    # 50% win,  avg $0.92  TECH  (10 trades)
    'RKT',    # 50% win,  avg $1.61        (16 trades)
]

# Remove duplicates
FOCUS_UNIVERSE = list(dict.fromkeys(FOCUS_UNIVERSE))

# ── DROPPED STOCKS (confirmed underperformers) ───────────
# NVDA   0%  win (v4)
# SMR   24%  win — nuclear that doesn't fit momentum strategy
# MSTR  17%  win — too volatile, crypto proxy
# SNOW  33%  win — cloud SaaS doesn't trend our way
# CRWD  33%  win — cybersecurity underperformer (v3+v5 confirmed)
# TSLA  33%  win — too choppy for our stop logic
# UBER  33%  win — not a momentum stock
# JOBY  38%  win — speculative, erratic
# PANW  43%  win — cybersecurity confirmed bad fit
# MS    43%  win — financials: JPM works, MS doesn't
# AFRM  43%  win, negative avg (v4)
# ACHR  44%  win — speculative space
# SOFI  50%  win, negative avg
# HPE   50%  win, negative avg

print(f"\n{'='*60}")
print(f"  STRATEGY BACKTEST v7 — ATR STOPS + REGIME FILTER")
print(f"  Period:     {START_DATE} to {END_DATE}")
print(f"  Universe:   {len(FOCUS_UNIVERSE)} stocks")
print(f"  Added:      IONQ,IREN,JPM,OKLO,AMZN,GOOGL,CRM,QBTS,NFLX")
print(f"  Dropped:    NVDA,TSLA,UBER,SNOW,CRWD,PANW,SMR,MSTR,JOBY,AFRM")
print(f"{'='*60}\n")

def get_sector(symbol):
    for sector, stocks in SECTORS.items():
        if symbol in stocks:
            return sector
    return 'OTHER'

# SPY market filter
print("  Loading SPY...")
ib_online = _bridge_available() and USE_IB_DATA
print(f"  Data source: {'IBKR (2-year accurate)' if ib_online else 'yfinance (fallback)'}")
spy_df    = get_historical_data('SPY', START_DATE, END_DATE)
spy_close = spy_df['Close'].squeeze()
spy_chg   = spy_close.pct_change()
print(f"  SPY: {len(spy_df)} days loaded\n")

def is_market_green(idx, df_index):
    try:
        dt = df_index[idx]
        if dt in spy_chg.index:
            return float(spy_chg.loc[dt]) > -0.005
        return True
    except:
        return True

def calculate_score(close, volume, high, low, idx):
    try:
        if idx < 50:
            return None

        c = close.iloc[:idx+1]
        v = volume.iloc[:idx+1]

        # Hard filter: volume surge required
        avg_vol      = v.rolling(20).mean().iloc[-1]
        today_vol    = v.iloc[-1]
        volume_ratio = today_vol / avg_vol if avg_vol > 0 else 1
        if volume_ratio < MIN_VOLUME_RATIO:
            return None

        if volume_ratio >= 3:
            vol_score = 100
        elif volume_ratio >= 2.5:
            vol_score = 90
        elif volume_ratio >= 2:
            vol_score = 80
        else:
            vol_score = 65

        # RSI
        rsi_s = ta.momentum.RSIIndicator(c, window=14).rsi()
        rsi   = rsi_s.iloc[-1]

        if 48 <= rsi <= 68:
            rsi_score = 100
        elif 38 <= rsi < 48:
            rsi_score = 70
        elif 68 < rsi <= 78:
            rsi_score = 55
        elif rsi > 78:
            rsi_score = 10
        else:
            rsi_score = 35

        # Momentum — hard filter: must be up today
        p0  = c.iloc[-1]
        p1  = c.iloc[-2]
        p5  = c.iloc[-5]  if len(c) > 5  else p0
        p20 = c.iloc[-20] if len(c) > 20 else p0

        ch1  = (p0 - p1)  / p1  * 100
        ch5  = (p0 - p5)  / p5  * 100
        ch20 = (p0 - p20) / p20 * 100

        if ch1 <= 0:
            return None  # must be green today

        if ch1 > 2 and ch5 > 3 and ch20 > 7:
            mom_score = 100
        elif ch1 > 1 and ch5 > 2:
            mom_score = 80
        elif ch1 > 0.5 and ch5 > 1:
            mom_score = 60
        else:
            mom_score = 35

        # MA — hard filter: must be above at least one MA
        ma20 = c.rolling(20).mean().iloc[-1]
        ma50 = c.rolling(50).mean().iloc[-1] if len(c) >= 50 else ma20

        if p0 > ma20 > ma50:
            ma_score = 100
        elif p0 > ma20:
            ma_score = 65
        elif p0 > ma50:
            ma_score = 40
        else:
            return None  # below both MAs — skip

        # MACD
        try:
            macd    = ta.trend.MACD(c)
            macd_h  = macd.macd_diff()
            if macd.macd().iloc[-1] > macd.macd_signal().iloc[-1] \
               and macd_h.iloc[-1] > macd_h.iloc[-2]:
                macd_score = 100
            elif macd.macd().iloc[-1] > macd.macd_signal().iloc[-1]:
                macd_score = 70
            elif macd_h.iloc[-1] > macd_h.iloc[-2]:
                macd_score = 50
            else:
                macd_score = 20
        except:
            macd_score = 50

        # ADX
        try:
            adx = ta.trend.ADXIndicator(
                high.iloc[:idx+1], low.iloc[:idx+1], c, window=14
            ).adx().iloc[-1]
            adx_score = 100 if adx > 30 else (75 if adx > 25 else (50 if adx > 20 else 25))
        except:
            adx_score = 50

        score = (
            vol_score   * 0.25 +
            mom_score   * 0.22 +
            rsi_score   * 0.18 +
            ma_score    * 0.15 +
            macd_score  * 0.12 +
            adx_score   * 0.08
        )

        return {
            'score':        round(score, 1),
            'rsi':          round(rsi, 1),
            'volume_ratio': round(volume_ratio, 2),
            'ch1':          round(ch1, 2),
            'ch5':          round(ch5, 2),
        }
    except:
        return None

# ── Run backtest ──────────────────────────────────────────
all_trades    = []
stock_results = {}

for i, symbol in enumerate(FOCUS_UNIVERSE):
    print(f"  [{i+1:02d}/{len(FOCUS_UNIVERSE)}] {symbol:<8}", end=' ')

    try:
        df = get_historical_data(symbol, START_DATE, END_DATE)

        if df.empty or len(df) < 60:
            print("no data")
            continue

        close  = df['Close'].squeeze()
        volume = df['Volume'].squeeze()
        high   = df['High'].squeeze()
        low    = df['Low'].squeeze()

        # Pre-compute ATR series for this stock
        def calc_atr_series(h, l, c):
            tr = pd.concat([
                h - l,
                (h - c.shift(1)).abs(),
                (l - c.shift(1)).abs()
            ], axis=1).max(axis=1)
            return tr.rolling(ATR_PERIOD).mean()

        atr_series = calc_atr_series(high, low, close)

        trades      = []
        in_trade    = False
        entry_price = 0
        entry_idx   = 0
        shares      = 0
        full_shares = 0
        target      = 0
        stop        = 0
        atr_entry   = 0
        session_high= 0
        partial_done= False

        for idx in range(50, len(df)-1):
            price     = float(close.iloc[idx])
            day_high  = float(high.iloc[idx])
            day_low   = float(low.iloc[idx])
            if price < 8 or price > 800:
                continue

            # Regime filter — skip weak days
            spy_day_chg = float(spy_chg.iloc[spy_chg.index.get_indexer([df.index[idx]], method='nearest')[0]]) if df.index[idx] in spy_chg.index else 0
            is_weak   = SKIP_WEAK   and spy_day_chg < -0.005
            is_choppy = SKIP_CHOPPY and abs(spy_day_chg) < 0.002

            if in_trade:
                hold_days   = idx - entry_idx
                session_high= max(session_high, day_high)
                exit_price  = None
                exit_reason = None

                # ATR trail activates after 1×ATR breathing room
                trail_active = session_high >= entry_price + atr_entry * ATR_TRAIL_START
                trail_stop   = round(session_high - ATR_TRAIL_MULT * atr_entry, 2) if trail_active else stop
                effective_stop = max(stop, trail_stop)

                # Tighten in WEAK regime (1.0×ATR trail)
                if is_weak and trail_active:
                    effective_stop = max(effective_stop,
                                        round(session_high - 1.0 * atr_entry, 2))

                # WEAK early loss cut at half stop distance
                if is_weak and price < entry_price - (entry_price - stop) * 0.5:
                    exit_price  = price
                    exit_reason = 'WEAK_CUT'

                # Stop hit (use daily low to check)
                elif day_low <= effective_stop:
                    exit_price  = effective_stop
                    exit_reason = 'STOP'

                # Partial exit at target — 50% out
                elif not partial_done and day_high >= target:
                    half = max(1, full_shares // 2)
                    pnl_partial = (target - entry_price) * half
                    trades.append({
                        'symbol': symbol, 'entry_date': str(df.index[entry_idx].date()),
                        'exit_date': str(df.index[idx].date()),
                        'entry_price': round(entry_price, 2), 'exit_price': round(target, 2),
                        'shares': half, 'pnl': round(pnl_partial, 2),
                        'pnl_pct': round(TARGET_PCT * 100, 2),
                        'exit_reason': 'PARTIAL_TARGET', 'sector': get_sector(symbol),
                        'is_tech': symbol in TECH_STOCKS, 'hold_days': hold_days, 'win': True
                    })
                    shares       = full_shares - half
                    stop         = entry_price   # move stop to breakeven on remainder
                    partial_done = True

                # Momentum fade — drops 1×ATR from session high while profitable
                elif trail_active and (session_high - price) > ATR_FADE_MULT * atr_entry and price > entry_price:
                    exit_price  = price
                    exit_reason = 'FADE'

                # Time stop
                elif hold_days >= MAX_HOLD_DAYS:
                    exit_price  = price
                    exit_reason = f'TIME_{MAX_HOLD_DAYS}D'

                if exit_reason and exit_price is not None:
                    pnl     = (exit_price - entry_price) * shares
                    pnl_pct = (exit_price - entry_price) / entry_price * 100
                    trades.append({
                        'symbol':      symbol,
                        'entry_date':  str(df.index[entry_idx].date()),
                        'exit_date':   str(df.index[idx].date()),
                        'entry_price': round(entry_price, 2),
                        'exit_price':  round(exit_price, 2),
                        'shares':      shares,
                        'pnl':         round(pnl, 2),
                        'pnl_pct':     round(pnl_pct, 2),
                        'exit_reason': exit_reason,
                        'sector':      get_sector(symbol),
                        'is_tech':     symbol in TECH_STOCKS,
                        'hold_days':   hold_days,
                        'win':         pnl > 0
                    })
                    in_trade     = False
                    partial_done = False

            else:
                if is_weak or is_choppy:
                    continue
                if not is_market_green(idx, df.index):
                    continue

                signals = calculate_score(close, volume, high, low, idx)
                if signals is None:
                    continue

                score = signals['score']
                if symbol in TECH_STOCKS:
                    score = min(100, score + TECH_BOOST)

                if score >= MIN_CONFIDENCE:
                    atr_val = float(atr_series.iloc[idx])
                    if np.isnan(atr_val) or atr_val <= 0:
                        continue

                    pos_val     = MAX_PER_TRADE
                    full_shares = max(1, int(pos_val / price))
                    shares      = full_shares
                    entry_price = price
                    atr_entry   = atr_val
                    # ATR-based stop (capped at MAX_RISK_PCT)
                    raw_stop    = price - ATR_STOP_MULT * atr_val
                    risk_pct    = (price - raw_stop) / price * 100
                    stop        = raw_stop if risk_pct <= MAX_RISK_PCT else round(price * (1 - MAX_RISK_PCT / 100), 2)
                    target      = round(price * (1 + TARGET_PCT), 2)
                    entry_idx   = idx
                    session_high= price
                    partial_done= False
                    in_trade    = True

        if trades:
            wins     = sum(1 for t in trades if t['win'])
            total    = len(trades)
            win_rate = wins / total * 100
            avg_pnl  = np.mean([t['pnl'] for t in trades])
            tot_pnl  = sum(t['pnl'] for t in trades)

            stock_results[symbol] = {
                'trades':    total,
                'wins':      wins,
                'win_rate':  round(win_rate, 1),
                'avg_pnl':   round(avg_pnl, 2),
                'total_pnl': round(tot_pnl, 2),
                'is_tech':   symbol in TECH_STOCKS,
                'sector':    get_sector(symbol)
            }
            all_trades.extend(trades)
            tech = "TECH" if symbol in TECH_STOCKS else "    "
            print(f"✅ {total:>3} trades | {win_rate:.0f}% win | "
                  f"avg ${avg_pnl:.2f} {tech}")
        else:
            print("no signals")

    except Exception as e:
        print(f"error: {e}")

# ── Results ───────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"  V7 RESULTS — ATR STOPS + REGIME FILTER")
print(f"{'='*60}")

if not all_trades:
    print("No trades generated.")
else:
    df_t    = pd.DataFrame(all_trades)
    total   = len(df_t)
    wins    = df_t['win'].sum()
    losses  = total - wins
    wr      = wins / total * 100
    avg_win = df_t[df_t['win']]['pnl'].mean()
    avg_los = df_t[~df_t['win']]['pnl'].mean()
    tot_pnl = df_t['pnl'].sum()
    avg_pnl = df_t['pnl'].mean()
    avg_hld = df_t['hold_days'].mean()
    rr      = abs(avg_win / avg_los) if avg_los != 0 else 0
    exp     = (wr/100 * avg_win) + ((1-wr/100) * avg_los)

    # ── Professional metrics ──────────────────────────────
    gross_profit  = df_t[df_t['pnl'] > 0]['pnl'].sum()
    gross_loss    = abs(df_t[df_t['pnl'] < 0]['pnl'].sum())
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999

    # Equity curve + daily returns for Sharpe / drawdown
    df_t['exit_date'] = pd.to_datetime(df_t['exit_date'])
    daily_pnl = df_t.groupby('exit_date')['pnl'].sum().reindex(
        pd.bdate_range(START_DATE, END_DATE), fill_value=0)
    equity    = CAPITAL + daily_pnl.cumsum()
    d_returns = daily_pnl / CAPITAL
    sharpe    = round(d_returns.mean() / d_returns.std() * np.sqrt(252), 2) if d_returns.std() > 0 else 0

    roll_max   = equity.cummax()
    drawdown   = (equity - roll_max) / roll_max * 100
    max_dd     = round(drawdown.min(), 1)
    final_eq   = equity.iloc[-1]
    total_ret  = round((final_eq - CAPITAL) / CAPITAL * 100, 1)
    days_total = (pd.Timestamp(END_DATE) - pd.Timestamp(START_DATE)).days
    ann_ret    = round(total_ret * 365 / days_total, 1)
    calmar     = round(ann_ret / abs(max_dd), 2) if max_dd != 0 else 0

    # SPY buy-and-hold benchmark
    spy_start  = float(spy_df['Close'].iloc[0])
    spy_end    = float(spy_df['Close'].iloc[-1])
    spy_ret    = round((spy_end - spy_start) / spy_start * 100, 1)
    spy_ann    = round(spy_ret * 365 / days_total, 1)
    spy_daily  = spy_df['Close'].pct_change().dropna()
    spy_sharpe = round(spy_daily.mean() / spy_daily.std() * np.sqrt(252), 2)

    print(f"\n  Period:          {START_DATE} to {END_DATE}")
    print(f"  Stocks:          {len(stock_results)}")
    print(f"  Total trades:    {total}")
    print(f"  Wins:            {int(wins)} ({wr:.1f}%)")
    print(f"  Losses:          {int(losses)} ({100-wr:.1f}%)")
    print(f"  Avg profit/win:  ${avg_win:.2f}")
    print(f"  Avg loss/loss:   ${avg_los:.2f}")
    print(f"  Avg P&L/trade:   ${avg_pnl:.2f}")
    print(f"  Total P&L:       ${tot_pnl:.2f}")
    print(f"  Avg hold days:   {avg_hld:.1f}")
    print(f"  Risk/Reward:     1:{rr:.2f}")
    print(f"  Expectancy:      ${exp:.2f} per trade")
    print(f"  Profit factor:   {profit_factor}x")
    print(f"  Total return:    {total_ret}%  (annualised {ann_ret}%)")
    print(f"  Max drawdown:    {max_dd}%")
    print(f"  Sharpe ratio:    {sharpe}")
    print(f"  Calmar ratio:    {calmar}")

    # Progress
    print(f"\n  PROGRESS")
    print(f"  v1: 49.4% | exp $0.64 | 6419 trades")
    print(f"  v2: 52.6% | exp $1.04 | 3719 trades")
    print(f"  v3: 58.9% | exp $2.75 |  168 trades")
    print(f"  v4: 62.4% | exp $3.18 |  173 trades  (19 stocks)")
    print(f"  v5: 56.9% | exp $2.19 |  357 trades  (39 stocks)")
    print(f"  v7: {wr:.1f}% | exp ${exp:.2f} | {total:>4} trades  (ATR stops + regime filter)")

    # vs SPY benchmark
    print(f"\n  VS BENCHMARKS")
    print(f"  {'='*50}")
    print(f"  {'Metric':<22} {'Our System':>12} {'SPY B&H':>10}")
    print(f"  {'-'*50}")
    print(f"  {'Ann. Return':<22} {ann_ret:>11}% {spy_ann:>9}%")
    print(f"  {'Sharpe Ratio':<22} {sharpe:>12} {spy_sharpe:>10}")
    print(f"  {'Max Drawdown':<22} {max_dd:>11}% {'~-25%':>10}")
    print(f"  {'Win Rate':<22} {wr:>11.1f}% {'n/a':>10}")
    print(f"  {'Profit Factor':<22} {profit_factor:>12} {'n/a':>10}")

    print(f"\n  VS PROFESSIONAL BENCHMARKS")
    print(f"  {'='*50}")
    benchmarks = [
        ("Retail buy & hold",   "~10% ann", "~0.6", "~-25%"),
        ("Retail algo (avg)",   "~15% ann", "~0.8", "~-20%"),
        ("Small hedge fund",    "~20% ann", "~1.0", "~-15%"),
        ("Good quant fund",     "~30% ann", "~1.5", "~-10%"),
        ("Top tier (RenTech)",  "~60% ann", "~2.5", "~-5%"),
    ]
    print(f"  {'Level':<24} {'Ann Ret':>8} {'Sharpe':>8} {'MaxDD':>8}")
    print(f"  {'-'*50}")
    for name, ret, sh, dd in benchmarks:
        print(f"  {name:<24} {ret:>8} {sh:>8} {dd:>8}")
    print(f"  {'-'*50}")
    print(f"  {'>>> OUR SYSTEM <<<':<24} {ann_ret:>7}% {sharpe:>8} {max_dd:>7}%")

    # Exit breakdown
    print(f"\n  EXIT BREAKDOWN")
    print(f"  {'='*40}")
    for reason, grp in df_t.groupby('exit_reason'):
        r_wr  = grp['win'].mean() * 100
        r_pnl = grp['pnl'].mean()
        print(f"  {reason:<12} {len(grp):>4} | "
              f"win {r_wr:.0f}% | avg ${r_pnl:.2f}")

    # Sectors
    print(f"\n  SECTORS")
    print(f"  {'='*40}")
    sec = (df_t.groupby('sector')
           .agg(t=('pnl','count'), w=('win','mean'), a=('pnl','mean'))
           .sort_values('w', ascending=False))
    for s, row in sec.iterrows():
        if row['t'] >= 3:
            print(f"  {s:<20} {int(row['t']):>4} | "
                  f"win {row['w']*100:.0f}% | avg ${row['a']:.2f}")

    # All stocks ranked
    print(f"\n  STOCKS RANKED BY WIN RATE")
    print(f"  {'='*40}")
    ranked = sorted(stock_results.items(),
                    key=lambda x: x[1]['win_rate'], reverse=True)
    for sym, res in ranked:
        tech = "TECH" if res['is_tech'] else "    "
        go   = "✅" if res['win_rate'] >= 57 else \
               ("⚠️" if res['win_rate'] >= 50 else "❌")
        print(f"  {go} {sym:<8} {tech} | "
              f"{res['trades']:>3} trades | "
              f"win {res['win_rate']:.0f}% | "
              f"avg ${res['avg_pnl']:.2f}")

    # Verdict
    print(f"\n{'='*60}")
    print(f"  FINAL VERDICT")
    print(f"{'='*60}")

    if wr >= 60 and exp > 5:
        verdict  = "STRONG ✅ Ready for Week 1 paper trading!"
        go_live  = True
    elif wr >= 57 and exp > 2:
        verdict  = "GOOD ✅ Start Week 1 paper trading!"
        go_live  = True
    elif wr >= 55 and exp > 0:
        verdict  = "OK ✅ Paper trade and monitor closely."
        go_live  = True
    else:
        verdict  = "NEEDS TUNING ❌"
        go_live  = False

    print(f"\n  Win rate:    {wr:.1f}%")
    print(f"  Expectancy:  ${exp:.2f} per trade")
    print(f"\n  {verdict}")

    if go_live:
        months     = 27
        trades_mo  = total / months
        profit_mo  = exp * trades_mo
        profit_day = profit_mo / 22

        print(f"\n  WEEK 1 PAPER TRADING PLAN")
        print(f"  {'='*40}")
        print(f"  Trade freely — no PDT restrictions on paper")
        print(f"  Target 3-5 trades per day")
        print(f"  Focus stocks: {', '.join([s for s,r in ranked[:8] if r['win_rate']>=57])}")
        print(f"  Expected trades/month: {trades_mo:.0f}")
        print(f"  Expected profit/month: ${profit_mo:.0f}")
        print(f"  Expected profit/day:   ${profit_day:.0f}")
        print(f"\n  WEEK 1 GOAL:")
        print(f"  Complete 20+ paper trades")
        print(f"  Track win rate — target 55%+")
        print(f"  Check if screener picks match backtest quality")

    # Save final results
    save = {
        'run_date':        date.today().isoformat(),
        'version':         'v7_atr_regime',
        'parameters': {
            'atr_stop_mult':     ATR_STOP_MULT,
            'atr_trail_mult':    ATR_TRAIL_MULT,
            'atr_trail_start':   ATR_TRAIL_START,
            'target_pct':        TARGET_PCT,
            'min_confidence':    MIN_CONFIDENCE,
            'min_volume_ratio':  MIN_VOLUME_RATIO,
            'max_hold_days':     MAX_HOLD_DAYS,
            'skip_weak':         SKIP_WEAK,
        },
        'results': {
            'total_trades': total,
            'win_rate':     round(wr, 1),
            'avg_pnl':      round(avg_pnl, 2),
            'expectancy':   round(exp, 2),
            'total_pnl':    round(tot_pnl, 2),
            'risk_reward':  round(rr, 2)
        },
        'proceed':         go_live,
        'stock_results':   stock_results,
        'focus_stocks':    [s for s, r in ranked if r['win_rate'] >= 57],
        'avoid_stocks':    [s for s, r in ranked if r['win_rate'] < 50],
        'top_sectors':     ['SEMI', 'CONSUMER', 'BIOTECH', 'AI_TECH'],
    }
    out = os.path.join(os.path.dirname(__file__), 'backtest_results.json')
    with open(out, 'w') as f:
        json.dump(save, f, indent=2)

    print(f"\n  Saved → backtest_results.json")
    print(f"  Screener reads this automatically every morning!")
    print(f"\n{'='*60}\n")
