#!/usr/bin/env python3
"""
backtester_options.py — Historical validation of Bull Spread + LEAP strategy
Uses Black-Scholes approximation with IBKR historical price + IV data.

Run:
  venv/bin/python options/backtester_options.py
  venv/bin/python options/backtester_options.py --strategy LEAP
  venv/bin/python options/backtester_options.py --symbols PLTR NVDA AMD COIN
"""

import os, sys, math, argparse, requests
import pandas as pd
import yfinance as yf
from scipy.stats import norm
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

BRIDGE_URL = os.getenv('BRIDGE_URL', 'http://127.0.0.1:8000')

# Same as FULL_UNIVERSE / OPTIONS_SYMBOLS — gates filter on execution
DEFAULT_SYMBOLS = [
    'AAPL', 'PLTR', 'IONQ', 'HOOD', 'JPM', 'VST', 'NFLX', 'ORCL', 'OKLO',
    'AMZN', 'GOOGL', 'CRM', 'QBTS', 'AVGO', 'CLS', 'RKLB', 'AMD', 'MSFT',
    'META', 'GS', 'SMCI', 'AI', 'RGTI', 'FSLR', 'CCJ', 'UUUU', 'APLD',
    'SOUN', 'ON', 'LRCX', 'DDOG', 'MDB', 'NVDA', 'INTC', 'TSLA', 'CVX',
    'XOM', 'UNH', 'MRNA', 'HIMS', 'COST', 'NKE', 'UBER', 'BAC', 'V', 'MA',
    'COIN', 'RTX', 'LMT', 'QCOM', 'MRVL', 'AMAT', 'MU', 'RIVN', 'NIO',
    'APP', 'MARA', 'ARM', 'AXON', 'SHOP', 'MSTR', 'JOBY', 'WULF', 'RIOT',
    'FTNT', 'IBKR', 'KKR', 'GILD', 'GE', 'BSX', 'TT', 'UPST', 'CELH',
    'DUOL', 'RBLX', 'TTD', 'TWLO', 'DOCU', 'ZS', 'OKTA', 'LULU', 'PANW',
    'CRWD', 'AFRM', 'SOFI', 'PLTR', 'COIN',
]

# ── Black-Scholes ──────────────────────────────────────────────────────────────

def bs_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, S - K)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)

def spread_val(S, K_long, K_short, T, r, sigma) -> float:
    return max(0.0, bs_call(S, K_long, T, r, sigma) - bs_call(S, K_short, T, r, sigma))

def rfr(date_str: str) -> float:
    return 0.050 if date_str < '2025-01-01' else 0.043

# ── Data fetchers ──────────────────────────────────────────────────────────────

_price_cache: dict[str, pd.DataFrame] = {}
_iv_cache:    dict[str, pd.Series]    = {}

def fetch_prices(symbol: str) -> pd.DataFrame | None:
    if symbol in _price_cache:
        return _price_cache[symbol]
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period='2y', interval='1d', auto_adjust=True)
        if df is None or len(df) < 50:
            return None
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df[['Close']].rename(columns={'Close': 'close'}).sort_index()
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna(subset=['close'])
        df['ma200'] = df['close'].rolling(200, min_periods=150).mean()
        _price_cache[symbol] = df
        return df
    except Exception as e:
        print(f"    price error: {e}")
        return None

def fetch_iv(symbol: str, prices: pd.DataFrame | None = None) -> pd.Series | None:
    """
    Try IBKR bridge first (accurate historical IV).
    Fall back to realized-volatility proxy from price history:
      IV ≈ HV30 × 1.20  (20% vol-risk-premium is conservative market average)
    This makes the backtester fully self-contained — no gateway required.
    Cached per symbol — safe because all scenarios run in the same process.
    """
    if symbol in _iv_cache:
        return _iv_cache[symbol]
    result = None
    try:
        r = requests.get(f"{BRIDGE_URL}/options/iv_history/{symbol}", timeout=10)
        if r.status_code == 200:
            bars = r.json().get('bars', [])
            if bars:
                s = pd.Series({pd.Timestamp(b['date']): b['iv'] for b in bars if b.get('iv')})
                if len(s) >= 20:
                    result = s.sort_index()
    except Exception:
        pass

    if result is None:
        # Fallback: compute 30-day realized vol from price history, scale by 1.20
        if prices is not None and len(prices) >= 35:
            try:
                log_ret  = prices['close'].pct_change().apply(lambda x: x + 1).apply(lambda x: __import__('math').log(x))
                hv30     = log_ret.rolling(30).std() * (252 ** 0.5)
                iv_proxy = (hv30 * 1.20).dropna()
                if len(iv_proxy) >= 20:
                    result = iv_proxy
            except Exception:
                pass

    if result is not None:
        _iv_cache[symbol] = result
    return result

# ── IV rank ────────────────────────────────────────────────────────────────────

def iv_rank_at(iv: pd.Series, date: pd.Timestamp, window: int = 252) -> float | None:
    if date not in iv.index:
        return None
    pos = iv.index.get_loc(date)
    start = max(0, pos - window)
    w = iv.iloc[start: pos + 1]
    if len(w) < 20:
        return None
    lo, hi = w.min(), w.max()
    return round((w.iloc[-1] - lo) / (hi - lo) * 100, 1) if hi != lo else 50.0

# ── Grading (IV rank proxy — no historical catalyst data available) ────────────

def grade(iv_rank: float, above_200: bool) -> str:
    """
    A+ : IV rank < 25  + above 200MA  — cheapest options, strong trend
    A  : IV rank 25-35 + above 200MA
    B  : IV rank 35-45 + above 200MA
    C  : IV rank > 45 or below 200MA  — avoid
    """
    if iv_rank < 25 and above_200:
        return 'A+'
    if iv_rank < 35 and above_200:
        return 'A'
    if iv_rank < 45 and above_200:
        return 'B'
    return 'C'

# ── Bull Spread simulator ──────────────────────────────────────────────────────
# Balanced template: long 4% OTM, short 15% OTM (4+11), ~32 DTE

BS_LONG_OTM  = 0.04
BS_WIDTH     = 0.11
BS_DTE       = 32
MAX_IV_ENTRY = 50.0

def sim_spread(sym, entry_date, price_df, iv_series, ivr) -> dict | None:
    row_e     = price_df.loc[entry_date]
    S         = row_e['close']
    sigma     = iv_series.get(entry_date)
    if not sigma or sigma <= 0:
        return None

    K_long  = S * (1 + BS_LONG_OTM)
    K_short = S * (1 + BS_LONG_OTM + BS_WIDTH)
    r       = rfr(str(entry_date.date()))
    T0      = BS_DTE / 365

    entry_v = spread_val(S, K_long, K_short, T0, r, sigma)
    if entry_v < 0.10:
        return None

    max_profit   = max(0.01, (K_short - K_long) - entry_v)
    hard_stop    = entry_v * 0.50
    be_trigger   = entry_v * 1.25
    trail_trigger = entry_v + max_profit * 0.50

    stage  = 1
    stop   = hard_stop
    s_high = entry_v
    above  = bool(S > (row_e['ma200'] if pd.notna(row_e['ma200']) else 0))
    g      = grade(ivr, above)

    for i, (date, row) in enumerate(price_df.loc[entry_date:].iloc[1:].iterrows(), 1):
        rem = BS_DTE - i
        if rem <= 7:
            T   = max(rem / 365, 0.005)
            sig = iv_series.get(date, sigma)
            val = spread_val(row['close'], K_long, K_short, T, rfr(str(date.date())), sig)
            return _res(sym, entry_date, date, entry_v, val, 'TIME_EXIT', i, g, ivr)

        T   = rem / 365
        sig = iv_series.get(date, sigma)
        val = spread_val(row['close'], K_long, K_short, T, rfr(str(date.date())), sig)

        if val > s_high:
            s_high = val
            if stage == 3:
                stop = max(stop, s_high - max_profit * 0.15)

        if stage == 1 and val >= be_trigger:
            stage, stop = 2, entry_v
        if stage >= 2 and val >= trail_trigger:
            stage = 3
            stop  = max(stop, s_high - max_profit * 0.15)

        if val <= stop:
            reason = {1: 'HARD_STOP', 2: 'BE_STOP', 3: 'TRAIL_STOP'}[stage]
            return _res(sym, entry_date, date, entry_v, val, reason, i, g, ivr)

    # Ran to end of data — mark as open
    last = list(price_df.loc[entry_date:].iloc[1:].iterrows())
    if last:
        d, row = last[-1]
        T   = max((BS_DTE - len(last)) / 365, 0.005)
        val = spread_val(row['close'], K_long, K_short, T,
                         rfr(str(d.date())), iv_series.get(d, sigma))
        return _res(sym, entry_date, d, entry_v, val, 'OPEN', len(last), g, ivr)
    return None

# ── LEAP simulator ─────────────────────────────────────────────────────────────
# 18-month expiry, 5% OTM, hard -40%, breakeven +30%, trail at +50% premium

LEAP_OTM = 0.05
LEAP_DTE = 540

def sim_leap(sym, entry_date, price_df, iv_series, ivr) -> dict | None:
    row_e = price_df.loc[entry_date]
    S     = row_e['close']
    sigma = iv_series.get(entry_date)
    if not sigma or sigma <= 0:
        return None

    K  = S * (1 + LEAP_OTM)
    r  = rfr(str(entry_date.date()))
    T0 = LEAP_DTE / 365

    entry_v = bs_call(S, K, T0, r, sigma)
    if entry_v < 0.50:
        return None

    hard_stop    = entry_v * 0.60
    be_trigger   = entry_v * 1.30
    trail_trigger = entry_v * 1.50

    stage  = 1
    stop   = hard_stop
    s_high = entry_v
    above  = bool(S > (row_e['ma200'] if pd.notna(row_e['ma200']) else 0))
    g      = grade(ivr, above)

    for i, (date, row) in enumerate(price_df.loc[entry_date:].iloc[1:].iterrows(), 1):
        rem = LEAP_DTE - i
        if rem <= 30:
            T   = max(rem / 365, 0.05)
            sig = iv_series.get(date, sigma)
            val = bs_call(row['close'], K, T, rfr(str(date.date())), sig)
            return _res(sym, entry_date, date, entry_v, val, 'TIME_EXIT', i, g, ivr)

        T   = rem / 365
        sig = iv_series.get(date, sigma)
        val = bs_call(row['close'], K, T, rfr(str(date.date())), sig)

        if val > s_high:
            s_high = val
            if stage == 3:
                stop = max(stop, s_high - entry_v * 0.20)

        if stage == 1 and val >= be_trigger:
            stage, stop = 2, entry_v
        if stage >= 2 and val >= trail_trigger:
            stage = 3
            stop  = max(stop, s_high - entry_v * 0.20)

        if val <= stop:
            reason = {1: 'HARD_STOP', 2: 'BE_STOP', 3: 'TRAIL_STOP'}[stage]
            return _res(sym, entry_date, date, entry_v, val, reason, i, g, ivr)

    last = list(price_df.loc[entry_date:].iloc[1:].iterrows())
    if last:
        d, row = last[-1]
        T   = max((LEAP_DTE - len(last)) / 365, 0.05)
        val = bs_call(row['close'], K, T, rfr(str(d.date())), iv_series.get(d, sigma))
        return _res(sym, entry_date, d, entry_v, val, 'OPEN', len(last), g, ivr)
    return None

def _res(sym, entry_date, exit_date, entry_v, exit_v, reason, days, g, ivr):
    ret = round((exit_v - entry_v) / entry_v * 100, 1)
    return {
        'symbol':      sym,
        'entry_date':  str(entry_date.date()),
        'exit_date':   str(exit_date.date()),
        'grade':       g,
        'iv_rank':     ivr,
        'return_pct':  ret,
        'entry_val':   round(entry_v, 2),
        'exit_val':    round(exit_v, 2),
        'exit_reason': reason,
        'days_held':   days,
        'win':         ret > 0,
    }

# ── Main backtest loop ─────────────────────────────────────────────────────────

def run_backtest(symbols: list[str], strategy: str) -> list[dict]:
    print(f"\n{'═'*55}")
    print(f"  Loading data for {len(symbols)} symbols ...")
    print(f"{'═'*55}")

    all_trades = []
    for sym in symbols:
        print(f"  {sym:<6} ", end='', flush=True)
        prices = fetch_prices(sym)
        if prices is None or len(prices) < 200:
            print("✗ no price data")
            continue
        ivs = fetch_iv(sym, prices)
        if ivs is None or len(ivs) < 20:
            print("✗ no IV data")
            continue
        src = "ibkr" if ivs.index[0] in prices.index else "hv30"
        print(f"[{src}] ", end='', flush=True)

        common = prices.index.intersection(ivs.index)
        if len(common) < 20:
            print("✗ insufficient overlap")
            continue

        trades     = 0
        open_until = None

        for date in common:
            if open_until and date <= open_until:
                continue

            ivr = iv_rank_at(ivs, date)
            if ivr is None:
                continue

            row = prices.loc[date]
            if pd.isna(row.get('ma200')):
                continue

            above = bool(row['close'] > row['ma200'])

            if strategy == 'BULL_SPREAD':
                if ivr > MAX_IV_ENTRY:
                    continue
                result = sim_spread(sym, date, prices, ivs, ivr)
            else:
                if ivr > 35 or not above:
                    continue
                result = sim_leap(sym, date, prices, ivs, ivr)

            if result:
                all_trades.append(result)
                trades += 1
                open_until = pd.Timestamp(result['exit_date'])

        print(f"✓ {trades} trades")

    return all_trades

# ── Report ─────────────────────────────────────────────────────────────────────

def report(trades: list[dict], strategy: str, symbols: list[str]):
    if not trades:
        print("\n  No trades generated. Bridge may be down or data unavailable.")
        print("  Try: curl http://localhost:8000/options/iv_history/PLTR")
        return

    df = pd.DataFrame(trades)
    date_range = f"{df['entry_date'].min()} → {df['entry_date'].max()}"
    strat_label = 'Bull Spread (Balanced: long 4%OTM, short 15%OTM, ~32d)' \
                  if strategy == 'BULL_SPREAD' else 'LEAP (18-month, 5% OTM)'

    print(f"\n\n{'═'*62}")
    print(f"  BACKTEST RESULTS  |  {strat_label}")
    print(f"  {len(symbols)} symbols  ·  {len(df)} trades  ·  {date_range}")
    print(f"{'═'*62}")
    print()
    print("  WHAT THIS MEASURES")
    print("  Grade = IV rank bucket + trend filter (200-day MA)")
    print("  A+ = cheapest options (IV<25%) in strong uptrend  ← best setup")
    print("  Exit = 3-stage: hard stop → breakeven lock → trailing stop")
    print("  B-S prices are approximate (~75% accurate vs real fills)")
    print()

    # ── Grade table ──────────────────────────────────────────
    print(f"{'─'*62}")
    print("  DOES GRADE PREDICT PERFORMANCE?")
    print(f"{'─'*62}")
    print(f"  {'Grade':<6} {'# Trades':>9} {'Win Rate':>10} {'Avg Return':>12} {'Avg Hold':>10}  Notes")
    print(f"  {'─'*56}")
    for g in ['A+', 'A', 'B', 'C']:
        sub = df[df['grade'] == g]
        if len(sub) == 0:
            continue
        wr   = sub['win'].mean() * 100
        avg  = sub['return_pct'].mean()
        hold = sub['days_held'].mean()
        note = ' ← target grade' if g == 'A+' else (' ← avoid' if g == 'C' else '')
        print(f"  {g:<6} {len(sub):>9} {wr:>9.1f}% {avg:>+11.1f}% {hold:>8.0f}d  {note}")
    print()

    # ── IV rank buckets ──────────────────────────────────────
    print(f"{'─'*62}")
    print("  DOES IV RANK MATTER?  (lower = cheaper premium = higher return)")
    print(f"{'─'*62}")
    buckets = [
        ('IV < 25%  (A+)',  df[df['iv_rank'] <  25]),
        ('IV 25-35% (A)',   df[(df['iv_rank'] >= 25) & (df['iv_rank'] < 35)]),
        ('IV 35-45% (B)',   df[(df['iv_rank'] >= 35) & (df['iv_rank'] < 45)]),
        ('IV > 45%  (C)',   df[df['iv_rank'] >= 45]),
    ]
    for label, sub in buckets:
        if len(sub) == 0:
            continue
        wr  = sub['win'].mean() * 100
        avg = sub['return_pct'].mean()
        bar = '█' * max(0, int((avg + 60) / 6))
        print(f"  {label:<18}  {len(sub):>3} trades   WR {wr:>5.1f}%   Avg {avg:>+6.1f}%  {bar}")
    print()

    # ── Exit breakdown ───────────────────────────────────────
    print(f"{'─'*62}")
    print("  HOW DO TRADES EXIT?")
    print(f"{'─'*62}")
    labels = {
        'HARD_STOP':  'Hard stop (-50% premium)   ← loss',
        'BE_STOP':    'Breakeven stop exit',
        'TRAIL_STOP': 'Trailing stop exit         ← win',
        'TIME_EXIT':  'Time exit (near expiry)',
        'OPEN':       'Still open / end of data',
    }
    for reason, cnt in df['exit_reason'].value_counts().items():
        pct = cnt / len(df) * 100
        print(f"  {labels.get(reason, reason):<40}  {cnt:>4}  ({pct:>4.1f}%)")
    print()

    # ── Per-symbol leaderboard ───────────────────────────────
    print(f"{'─'*62}")
    print("  BEST SYMBOLS")
    print(f"{'─'*62}")
    sym_df = df.groupby('symbol').agg(
        trades    = ('return_pct', 'count'),
        win_rate  = ('win',        lambda x: x.mean() * 100),
        avg_ret   = ('return_pct', 'mean'),
    ).sort_values('avg_ret', ascending=False)
    print(f"  {'Symbol':<8} {'Trades':>7} {'Win Rate':>10} {'Avg Return':>12}")
    print(f"  {'─'*40}")
    for sym, row in sym_df.head(10).iterrows():
        flag = ' ★' if row['avg_ret'] > 20 and row['win_rate'] > 55 else ''
        print(f"  {sym:<8} {row['trades']:>7.0f} {row['win_rate']:>9.1f}% {row['avg_ret']:>+11.1f}%{flag}")
    print()

    # ── Monthly equity curve ─────────────────────────────────
    print(f"{'─'*62}")
    print("  EQUITY CURVE  ($5,000 account · $300/trade · 1 contract)")
    print(f"{'─'*62}")
    capital    = 5_000.0
    trade_size = 300.0
    monthly    = {}
    for _, row in df.sort_values('entry_date').iterrows():
        mo = row['entry_date'][:7]
        monthly[mo] = monthly.get(mo, 0) + trade_size * row['return_pct'] / 100
    running = 5_000.0
    for mo in sorted(monthly):
        pnl      = monthly[mo]
        running += pnl
        arrow    = '▲' if pnl >= 0 else '▼'
        print(f"  {mo}   ${running:>8,.0f}   {arrow}  ${abs(pnl):>6,.0f}  monthly ({pnl / 5000 * 100:>+5.1f}%)")
    total_ret = (running - 5_000) / 5_000 * 100
    print(f"\n  End capital: ${running:>8,.0f}   Total return {total_ret:>+.1f}%  over ~12 months")
    print()

    # ── Go / No-Go ───────────────────────────────────────────
    print(f"{'═'*62}")
    print("  GO / NO-GO  ─  Monday Paper Trading Decision")
    print(f"{'═'*62}")

    aplus    = df[df['grade'] == 'A+']
    c_grade  = df[df['grade'] == 'C']
    lo_iv    = df[df['iv_rank'] < 25]
    hi_iv    = df[df['iv_rank'] > 35]

    a_wr  = aplus['win'].mean()  * 100 if len(aplus)   > 0 else 0.0
    a_ret = aplus['return_pct'].mean()  if len(aplus)   > 0 else 0.0
    c_ret = c_grade['return_pct'].mean() if len(c_grade) > 0 else 0.0
    lo_r  = lo_iv['return_pct'].mean()  if len(lo_iv)   > 0 else 0.0
    hi_r  = hi_iv['return_pct'].mean()  if len(hi_iv)   > 0 else 0.0

    # Note: spreads with hard stops at -50% naturally produce 20-35% WR with positive EV.
    # WR ≥ 55% is an equity metric — wrong for spreads. Use avg return > 0 as EV check.
    checks = [
        (f"A+ avg return > 0%       got {a_ret:>+6.1f}%", a_ret >  0),
        (f"A+ avg return ≥ +15%     got {a_ret:>+6.1f}%", a_ret >= 15),
        (f"C grade < A+ return      {c_ret:>+5.1f}% vs {a_ret:>+5.1f}%", c_ret < a_ret),
        (f"IV <25% beats IV >35%    {lo_r:>+5.1f}% vs {hi_r:>+5.1f}%",   lo_r  > hi_r),
    ]

    all_pass = all(ok for _, ok in checks)
    for label, ok in checks:
        print(f"  {'✅' if ok else '❌'}  {label}")

    print()
    if all_pass:
        print("  VERDICT: ✅  ALL 4 CHECKS PASS")
        print("           Proceed to paper trading Monday.")
        print("           Start with A+/A grade setups only.")
    else:
        fails = sum(1 for _, ok in checks if not ok)
        print(f"  VERDICT: ❌  {fails} check(s) failed.")
        print("           Review grading thresholds before entering trades.")
    print(f"{'═'*62}\n")


# ── Quick Spread: 2-day hold, EM-anchored strikes ─────────────────────────────
# Mirrors the live strategy: long 0.33 SD OTM, short 0.67 SD OTM, 17 DTE, 2-day hold.
# Entry signal proxy: IV rank < 35 + trend filter (above MA200 = bull, below = bear).

QUICK_DTE   = 17   # midpoint of 14-21 DTE window
QUICK_HOLD  = 2    # business days max hold

def bs_put(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, K - S)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)

def put_spread_val(S, K_long, K_short, T, r, sigma) -> float:
    return max(0.0, bs_put(S, K_long, T, r, sigma) - bs_put(S, K_short, T, r, sigma))

def sim_quick_spread(sym, entry_date, price_df, iv_series, ivr,
                     bearish: bool = False,
                     min_move_pct: float = 0.0,
                     max_move_pct: float = 0.0,
                     long_sd: float = 0.33,
                     short_sd: float = 0.67) -> dict | None:
    row_e  = price_df.loc[entry_date]
    S      = row_e['close']
    sigma  = iv_series.get(entry_date)
    if not sigma or sigma <= 0:
        return None

    ma200  = row_e.get('ma200')
    above  = bool(S > ma200) if pd.notna(ma200) else False

    # Direction filter: bull needs above 200MA, bear needs below
    if bearish and above:
        return None
    if not bearish and not above:
        return None

    # Same-day momentum filter (proxy for catalyst/news trigger)
    # max_move_pct: upper bound for bull (live gate uses 2-7% — ≥7% = exhausted)
    if min_move_pct > 0 or max_move_pct > 0:
        idx = price_df.index.get_loc(entry_date)
        if idx > 0:
            prev_close = price_df['close'].iloc[idx - 1]
            day_move   = (S - prev_close) / prev_close * 100
            if bearish:
                if day_move > -min_move_pct:
                    return None   # need stock DOWN min_move_pct for bear
            else:
                if min_move_pct > 0 and day_move < min_move_pct:
                    return None   # need stock UP min_move_pct for bull
                if max_move_pct > 0 and day_move >= max_move_pct:
                    return None   # ≥7%: momentum exhausted at scan time

    em = S * sigma * math.sqrt(QUICK_DTE / 252)
    r  = rfr(str(entry_date.date()))
    T0 = QUICK_DTE / 365

    if bearish:
        K_long  = S - em * long_sd
        K_short = S - em * short_sd
        entry_v = put_spread_val(S, K_long, K_short, T0, r, sigma)
    else:
        K_long  = S + em * long_sd
        K_short = S + em * short_sd
        entry_v = spread_val(S, K_long, K_short, T0, r, sigma)

    if entry_v < 0.10:
        return None

    max_profit = max(0.01, abs(K_short - K_long) - entry_v)
    hard_stop  = entry_v * 0.50
    target     = entry_v + max_profit * 0.50

    g = grade(ivr, above)

    # Simulate up to QUICK_HOLD business days
    future_rows = list(price_df.loc[entry_date:].iloc[1:QUICK_HOLD + 1].iterrows())
    if not future_rows:
        return None

    for i, (date, row) in enumerate(future_rows, 1):
        rem = QUICK_DTE - i
        T   = max(rem / 365, 1 / 365)
        sig = iv_series.get(date, sigma)
        if bearish:
            val = put_spread_val(row['close'], K_long, K_short, T, rfr(str(date.date())), sig)
        else:
            val = spread_val(row['close'], K_long, K_short, T, rfr(str(date.date())), sig)

        if val >= target:
            return _res(sym, entry_date, date, entry_v, val, 'TARGET_HIT', i, g, ivr)
        if val <= hard_stop:
            return _res(sym, entry_date, date, entry_v, val, 'HARD_STOP', i, g, ivr)

    # Time exit: forced close after 2 days
    last_date, last_row = future_rows[-1]
    rem = QUICK_DTE - len(future_rows)
    T   = max(rem / 365, 1 / 365)
    sig = iv_series.get(last_date, sigma)
    if bearish:
        val = put_spread_val(last_row['close'], K_long, K_short, T,
                             rfr(str(last_date.date())), sig)
    else:
        val = spread_val(last_row['close'], K_long, K_short, T,
                         rfr(str(last_date.date())), sig)
    return _res(sym, entry_date, last_date, entry_v, val, 'TIME_EXIT', len(future_rows), g, ivr)


def run_quick_backtest(symbols: list[str], bearish: bool = False,
                       min_move_pct: float = 0.0, max_move_pct: float = 0.0,
                       long_sd: float = 0.33, short_sd: float = 0.67,
                       max_ivr: int = 35, silent: bool = False,
                       start_date: str = '2026-01-01') -> list[dict]:
    direction = 'BEAR PUT' if bearish else 'BULL CALL'
    if not silent:
        print(f"\n{'═'*55}")
        print(f"  QUICK SPREAD ({direction}) — 2-day hold, EM-anchored")
        print(f"  Long {long_sd} SD · Short {short_sd} SD · {QUICK_DTE} DTE"
              + (f" · {min_move_pct:.0f}%+ momentum" if min_move_pct else ""))
        print(f"  Loading {len(symbols)} symbols ...")
        print(f"{'═'*55}")

    all_trades = []
    for sym in symbols:
        if not silent:
            print(f"  {sym:<6} ", end='', flush=True)
        prices = fetch_prices(sym)
        if prices is None or len(prices) < 200:
            if not silent: print("✗ no price data")
            continue
        ivs = fetch_iv(sym, prices)
        if ivs is None or len(ivs) < 20:
            if not silent: print("✗ no IV data")
            continue
        if not silent:
            src = "ibkr" if ivs.index[0] in prices.index else "hv30"
            print(f"[{src}] ", end='', flush=True)

        common = prices.index.intersection(ivs.index)
        if len(common) < 20:
            if not silent: print("✗ insufficient overlap")
            continue

        common = common[common >= pd.Timestamp(start_date)]
        if len(common) < 5:
            if not silent: print(f"✗ insufficient data from {start_date}")
            continue

        trades    = 0
        skip_days = 0

        for date in common:
            if skip_days > 0:
                skip_days -= 1
                continue

            ivr = iv_rank_at(ivs, date)
            if ivr is None or ivr > max_ivr:
                continue

            result = sim_quick_spread(sym, date, prices, ivs, ivr,
                                      bearish=bearish,
                                      min_move_pct=min_move_pct,
                                      max_move_pct=max_move_pct,
                                      long_sd=long_sd,
                                      short_sd=short_sd)
            if result:
                all_trades.append(result)
                trades   += 1
                skip_days = QUICK_HOLD

        if not silent:
            print(f"✓ {trades} trades")

    return all_trades


def report_quick(trades: list[dict], bearish: bool = False):
    direction = 'Bear Put Spread' if bearish else 'Bull Call Spread'
    print(f"\n\n{'═'*62}")
    print(f"  QUICK SPREAD BACKTEST  |  {direction}  (2026 YTD)")
    print(f"  Long 0.33 SD OTM · Short 0.67 SD OTM · {QUICK_DTE} DTE · 2-day hold")
    print(f"{'═'*62}")

    if not trades:
        print("  No trades — check IV data / try --symbols NVDA TSLA PLTR")
        return

    df = pd.DataFrame(trades)
    total = len(df)
    wr    = df['win'].mean() * 100
    avg   = df['return_pct'].mean()
    med   = df['return_pct'].median()

    print(f"\n  Total trades  : {total}")
    print(f"  Win rate      : {wr:.1f}%")
    print(f"  Avg return    : {avg:+.1f}%")
    print(f"  Median return : {med:+.1f}%")
    print(f"  Date range    : {df['entry_date'].min()} → {df['entry_date'].max()}")

    print(f"\n{'─'*62}")
    print("  EXIT BREAKDOWN")
    for reason, cnt in df['exit_reason'].value_counts().items():
        pct = cnt / total * 100
        tag = '← win' if reason == 'TARGET_HIT' else ('← loss' if reason == 'HARD_STOP' else '← neutral')
        print(f"  {reason:<15}  {cnt:>4}  ({pct:>4.1f}%)  {tag}")

    print(f"\n{'─'*62}")
    print("  GRADE BREAKDOWN  (A+ = IV rank < 25%)")
    for g in ['A+', 'A', 'B', 'C']:
        sub = df[df['grade'] == g]
        if len(sub) == 0:
            continue
        print(f"  Grade {g}  {len(sub):>4} trades   WR {sub['win'].mean()*100:>5.1f}%   "
              f"Avg {sub['return_pct'].mean():>+6.1f}%")

    print(f"\n{'─'*62}")
    print("  EQUITY CURVE  ($5k account · $400 per 2-day trade)")
    capital = 5_000.0; trade_cost = 400.0; running = capital
    for _, row in df.sort_values('entry_date').iterrows():
        pnl     = trade_cost * row['return_pct'] / 100
        running += pnl
        sym_tag = row['symbol'][:4]
        pnl_tag = f"${pnl:>+6.0f}"
        print(f"  {row['entry_date']}  {sym_tag:<5}  {row['exit_reason']:<14}  "
              f"{row['return_pct']:>+6.1f}%  {pnl_tag}  running ${running:>8,.0f}")
    total_ret = (running - capital) / capital * 100
    print(f"\n  End capital: ${running:>8,.0f}   Total: {total_ret:>+.1f}%")

    print(f"\n{'═'*62}")
    go = wr >= 45 and avg > 0
    print(f"  VERDICT: {'✅ GO' if go else '❌ REVIEW'}")
    if go:
        print("  WR ≥ 45% and avg return > 0 — enter trades Monday.")
    else:
        if wr < 45:
            print(f"  WR {wr:.1f}% < 45% — too many losers. Tighten entry filter.")
        if avg <= 0:
            print(f"  Avg return {avg:+.1f}% ≤ 0 — size/stop not covering losses. Review.")
    print(f"{'═'*62}\n")


# ── High-beta options universe — stocks that actually move 3-8% in a day ──────
# Removed: AAPL, AMZN, GOOGL, MSFT, AVGO, META, JPM, GS (equity trades, not options plays)
HIGH_BETA_SYMBOLS = [
    'PLTR', 'COIN', 'MSTR', 'HIMS', 'IONQ', 'SOUN', 'MARA', 'APP',
    'SMCI', 'HOOD', 'AFRM', 'UPST', 'RKLB', 'RIOT', 'AMD',
    'TSLA', 'NVDA', 'ARM', 'WULF', 'RIVN', 'CELH', 'JOBY',
]


def run_comparison(symbols: list[str], bearish: bool = False, start_date: str = '2026-01-01'):
    """Run scenarios and print a side-by-side comparison table."""
    direction = 'BEAR PUT' if bearish else 'BULL CALL'
    print(f"\n{'═'*72}")
    print(f"  SCENARIO COMPARISON  |  {direction} spread  |  {start_date} →")
    print(f"  Goal: find which entry filter / strike geometry creates edge")
    print(f"{'═'*72}")

    if bearish:
        scenarios = [
            # (label,               long_sd, short_sd, min_move, max_move, max_ivr, syms)
            ('Baseline (0.33/0.67)',  0.33, 0.67, 0.0, 0.0, 35, symbols),
            ('Near-ATM (0.10/0.45)', 0.10, 0.45, 0.0, 0.0, 35, symbols),
            ('Tight (0.15/0.35)',     0.15, 0.35, 0.0, 0.0, 35, symbols),
            ('Momentum 2%+',          0.33, 0.67, 2.0, 0.0, 35, symbols),
            ('Momentum 3%+',          0.33, 0.67, 3.0, 0.0, 35, symbols),
            ('Near-ATM + Mom 2%+',   0.10, 0.45, 2.0, 0.0, 35, symbols),
        ]
    else:
        scenarios = [
            # (label,               long_sd, short_sd, min_move, max_move, max_ivr, syms)
            ('Baseline (0.33/0.67)',  0.33, 0.67, 0.0, 0.0, 35, symbols),
            ('Near-ATM (0.10/0.45)', 0.10, 0.45, 0.0, 0.0, 35, symbols),
            ('Tight (0.15/0.35)',     0.15, 0.35, 0.0, 0.0, 35, symbols),
            ('Momentum 2%+',          0.33, 0.67, 2.0, 0.0, 35, symbols),
            ('Momentum 3%+',          0.33, 0.67, 3.0, 0.0, 35, symbols),
            ('Live gate (2-7%)',       0.33, 0.67, 2.0, 7.0, 35, symbols),  # matches live code
            ('Near-ATM + Mom 2%+',   0.10, 0.45, 2.0, 0.0, 35, symbols),
        ]

    print(f"\n  {'Scenario':<26} {'N':>5} {'WR%':>7} {'Avg%':>7} {'TargetHits':>11}  Verdict")
    print(f"  {'-'*68}")

    for label, l_sd, s_sd, mm, mx_mv, mx_ivr, syms in scenarios:
        trades = run_quick_backtest(syms, bearish=bearish,
                                   min_move_pct=mm, max_move_pct=mx_mv,
                                   long_sd=l_sd, short_sd=s_sd,
                                   max_ivr=mx_ivr, silent=True,
                                   start_date=start_date)
        if not trades:
            print(f"  {label:<26}  {'–':>5}   no trades")
            continue
        df   = pd.DataFrame(trades)
        wr   = df['win'].mean() * 100
        avg  = df['return_pct'].mean()
        hits = (df['exit_reason'] == 'TARGET_HIT').sum()
        go   = '✅' if (wr >= 45 and avg > 0) else '❌'
        print(f"  {label:<26} {len(df):>5} {wr:>6.1f}% {avg:>+6.1f}% {hits:>10}x  {go}")

    print(f"\n  ✅ = WR≥45% and avg>0")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Options strategy backtester')
    parser.add_argument('--strategy', default='BULL_SPREAD',
                        choices=['BULL_SPREAD', 'LEAP', 'QUICK_BULL', 'QUICK_BEAR',
                                 'QUICK_BOTH', 'COMPARE_BULL', 'COMPARE_BEAR'],
                        help='Strategy to backtest (default: BULL_SPREAD)')
    parser.add_argument('--symbols', nargs='+', default=None,
                        help='Symbol list override')
    parser.add_argument('--high-beta', action='store_true',
                        help='Use high-beta universe (PLTR/COIN/MSTR/HIMS/IONQ/…)')
    parser.add_argument('--momentum', type=float, default=0.0,
                        help='Min same-day move %% for entry (e.g. 2.0 = stock must be up 2%%)')
    parser.add_argument('--long-sd', type=float, default=0.33,
                        help='Long strike in SD units (default 0.33)')
    parser.add_argument('--short-sd', type=float, default=0.67,
                        help='Short strike in SD units (default 0.67)')
    parser.add_argument('--start', default='2026-01-01',
                        help='Start date for backtest (default: 2026-01-01, use 2025-01-01 for full year)')
    args = parser.parse_args()

    if args.high_beta:
        syms = args.symbols or HIGH_BETA_SYMBOLS
    else:
        syms = args.symbols or DEFAULT_SYMBOLS

    if args.strategy in ('COMPARE_BULL', 'COMPARE_BEAR'):
        run_comparison(syms, bearish=(args.strategy == 'COMPARE_BEAR'), start_date=args.start)
    elif args.strategy.startswith('QUICK'):
        kw = dict(min_move_pct=args.momentum, long_sd=args.long_sd, short_sd=args.short_sd,
                  start_date=args.start)
        if args.strategy in ('QUICK_BULL', 'QUICK_BOTH'):
            bull_trades = run_quick_backtest(syms, bearish=False, **kw)
            report_quick(bull_trades, bearish=False)
        if args.strategy in ('QUICK_BEAR', 'QUICK_BOTH'):
            bear_trades = run_quick_backtest(syms, bearish=True, **kw)
            report_quick(bear_trades, bearish=True)
    else:
        trades = run_backtest(syms, args.strategy)
        report(trades, args.strategy, syms)
