#!/usr/bin/env python3
"""
backtest_scanlog.py — Options backtest using REAL equity scan signals.

Uses actual A+ grades from scan_log as entry triggers (not simulated entry dates).
Simulates 2-day call/put spread P&L with Black-Scholes pricing.

Why this is better than the symbol-universe backtest:
  - Real signals from the live equity scanner
  - intra_chg captures actual momentum at scan time (matches live gate)
  - Includes catalyst stocks AND symbols outside standard options universe
  - actual_close already stored = no yfinance needed for day-1 exit
  - Answers: "Do our A+ signals generate profitable options setups?"

Run:
  venv/bin/python options/backtest_scanlog.py
  venv/bin/python options/backtest_scanlog.py --days 60 --min-intra 2.0
  venv/bin/python options/backtest_scanlog.py --catalyst-only
  venv/bin/python options/backtest_scanlog.py --direction SHORT
  venv/bin/python options/backtest_scanlog.py --no-intra-filter   # all A+, no momentum gate
"""

import os
import sys
import math
import sqlite3
import argparse
from datetime import datetime, timedelta, date

import pandas as pd
import yfinance as yf
from scipy.stats import norm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'trades.db')

# Our standard 159-symbol equity universe (for in-universe vs catalyst comparison)
EQUITY_UNIVERSE = {
    'AAPL','PLTR','IONQ','HOOD','JPM','VST','NFLX','ORCL','OKLO','AMZN','GOOGL',
    'CRM','QBTS','AVGO','CLS','RKLB','AMD','MSFT','META','GS','SMCI','AI','RGTI',
    'FSLR','CCJ','UUUU','APLD','SOUN','ON','LRCX','DDOG','MDB','NVDA','INTC',
    'TSLA','CVX','XOM','UNH','MRNA','HIMS','COST','NKE','UBER','BAC','V','MA',
    'COIN','RTX','LMT','QCOM','MRVL','AMAT','MU','RIVN','NIO','APP','MARA','ARM',
    'AXON','SHOP','MSTR','JOBY','WULF','RIOT','FTNT','IBKR','KKR','GILD','GE',
    'BSX','TT','UPST','CELH','DUOL','RBLX','TTD','TWLO','DOCU','ZS','OKTA','LULU',
    'PANW','CRWD','AFRM','SOFI',
    'AEHR','APD','HXL','SSYS',
}


# ── Black-Scholes ─────────────────────────────────────────────────────────────

def bs_call(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, S - K)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)

def bs_put(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, K - S)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)

def call_spread_val(S, K_long, K_short, T, r, sigma):
    return max(0.0, bs_call(S, K_long, T, r, sigma) - bs_call(S, K_short, T, r, sigma))

def put_spread_val(S, K_long, K_short, T, r, sigma):
    return max(0.0, bs_put(S, K_long, T, r, sigma) - bs_put(S, K_short, T, r, sigma))

def rfr(date_str):
    return 0.043


# ── IV proxy ──────────────────────────────────────────────────────────────────

_iv_cache: dict[str, float] = {}

def get_iv_proxy(symbol: str) -> float | None:
    """HV30 × 1.20 proxy — same method as backtester_options. Cached per symbol."""
    if symbol in _iv_cache:
        return _iv_cache[symbol]
    try:
        hist   = yf.Ticker(symbol).history(period='90d', interval='1d')
        closes = hist['Close'].values
        if len(closes) < 32:
            return None
        c   = closes[-31:]
        lr  = [math.log(c[i+1] / c[i]) for i in range(len(c)-1)]
        n   = len(lr)
        mu  = sum(lr) / n
        hv30 = math.sqrt(sum((x - mu)**2 for x in lr) / (n - 1)) * math.sqrt(252)
        iv  = round(hv30 * 1.20, 4)   # VRP: IV typically 20% above HV
        _iv_cache[symbol] = iv
        return iv
    except Exception:
        return None


# ── Fetch next trading day close ──────────────────────────────────────────────

_price_cache: dict[str, pd.DataFrame] = {}

def get_price_series(symbol: str) -> pd.DataFrame | None:
    if symbol in _price_cache:
        return _price_cache[symbol]
    try:
        df = yf.Ticker(symbol).history(period='6mo', interval='1d', auto_adjust=True)
        if df is None or len(df) < 5:
            return None
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df[['Close']].rename(columns={'Close': 'close'})
        _price_cache[symbol] = df
        return df
    except Exception:
        return None

def get_next_day_close(symbol: str, scan_date: str, offset: int = 1) -> float | None:
    """Return the close `offset` trading days after scan_date."""
    df = get_price_series(symbol)
    if df is None:
        return None
    try:
        dt = pd.Timestamp(scan_date)
        future = df[df.index > dt]
        if len(future) < offset:
            return None
        return float(future.iloc[offset - 1]['close'])
    except Exception:
        return None


# ── Simulate one spread ───────────────────────────────────────────────────────

DTE   = 17     # midpoint of live 14-21 DTE window
LONG_SD  = 0.33
SHORT_SD = 0.67

def simulate_signal(row: dict, bearish: bool) -> dict | None:
    """
    Simulate a 2-day spread trade on one scan_log signal.
    Returns trade result dict or None if not tradeable.
    """
    sym         = row['symbol']
    scan_date   = row['scan_date']
    entry_price = row['price']
    actual_close = row['actual_close']   # day-1 close (always available)
    intra_chg   = row['intra_chg'] or 0.0

    sigma = get_iv_proxy(sym)
    if not sigma or sigma <= 0:
        return None
    if entry_price <= 0:
        return None

    r   = rfr(scan_date)
    em  = entry_price * sigma * math.sqrt(DTE / 252)
    T0  = DTE / 365
    T1  = (DTE - 1) / 365
    T2  = max((DTE - 2) / 365, 1/365)

    if bearish:
        K_long  = entry_price - em * LONG_SD
        K_short = entry_price - em * SHORT_SD
        entry_v = put_spread_val(entry_price, K_long, K_short, T0, r, sigma)
    else:
        K_long  = entry_price + em * LONG_SD
        K_short = entry_price + em * SHORT_SD
        entry_v = call_spread_val(entry_price, K_long, K_short, T0, r, sigma)

    if entry_v < 0.05:
        return None

    max_profit   = max(0.01, abs(K_short - K_long) - entry_v)
    target_val   = entry_v + max_profit * 0.50
    stop_val     = entry_v * 0.50

    # Day 1: use actual_close from scan_log (already captured by live system)
    sig1 = sigma   # assume IV stable over 2 days (reasonable for short DTE)
    if bearish:
        val1 = put_spread_val(actual_close, K_long, K_short, T1, r, sig1)
    else:
        val1 = call_spread_val(actual_close, K_long, K_short, T1, r, sig1)

    # Check stop/target after day 1
    if val1 >= target_val:
        exit_val, exit_day, exit_why = val1, 1, 'TARGET'
    elif val1 <= stop_val:
        exit_val, exit_day, exit_why = val1, 1, 'STOP'
    else:
        # Day 2: fetch from yfinance
        close2 = get_next_day_close(sym, scan_date, offset=2)
        if close2 is None:
            # Fall back to day-1 exit if day-2 not available
            exit_val, exit_day, exit_why = val1, 1, 'TIME_D1'
        else:
            if bearish:
                val2 = put_spread_val(close2, K_long, K_short, T2, r, sig1)
            else:
                val2 = call_spread_val(close2, K_long, K_short, T2, r, sig1)

            if val2 >= target_val:
                exit_val, exit_day, exit_why = val2, 2, 'TARGET'
            elif val2 <= stop_val:
                exit_val, exit_day, exit_why = val2, 2, 'STOP'
            else:
                exit_val, exit_day, exit_why = val2, 2, 'TIME'

    ret_pct = round((exit_val - entry_v) / entry_v * 100, 1)
    win     = ret_pct > 0

    return {
        'symbol':     sym,
        'scan_date':  scan_date,
        'direction':  'SHORT' if bearish else 'LONG',
        'entry_price': round(entry_price, 2),
        'intra_chg':  round(intra_chg, 2),
        'is_catalyst': row['is_catalyst'],
        'sector':     row['sector'],
        'in_universe': sym in EQUITY_UNIVERSE,
        'entry_v':    round(entry_v, 2),
        'exit_v':     round(exit_val, 2),
        'max_profit': round(max_profit, 2),
        'return_pct': ret_pct,
        'win':        win,
        'exit_day':   exit_day,
        'exit_why':   exit_why,
        'day1_pct':   round((actual_close - entry_price) / entry_price * 100, 2),
    }


# ── Load signals from scan_log ────────────────────────────────────────────────

def load_signals(days: int = 60,
                 direction: str = 'LONG',
                 min_intra: float = 0.0,
                 max_intra: float = 0.0,
                 catalyst_only: bool = False,
                 no_catalyst: bool = False) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    bearish = (direction == 'SHORT')

    # Direction-specific intra filter
    if bearish:
        intra_clause = f"AND intra_chg <= {-abs(min_intra)}" if min_intra else ""
        max_clause   = f"AND intra_chg >= {-abs(max_intra)}" if max_intra else ""
    else:
        intra_clause = f"AND intra_chg >= {abs(min_intra)}"  if min_intra else ""
        max_clause   = f"AND intra_chg < {abs(max_intra)}"   if max_intra else ""

    catalyst_clause    = "AND is_catalyst = 1" if catalyst_only else ""
    no_catalyst_clause = "AND is_catalyst = 0" if no_catalyst else ""

    sql = f"""
        SELECT symbol, scan_date, MIN(scan_time) as scan_time,
               price, intra_chg, actual_close, actual_day_pct,
               is_catalyst, sector
        FROM scan_log
        WHERE grade = 'A+'
          AND direction = ?
          AND scan_date >= ?
          AND actual_close IS NOT NULL
          AND price > 5
          {intra_clause}
          {max_clause}
          {catalyst_clause}
          {no_catalyst_clause}
        GROUP BY symbol, scan_date
        ORDER BY scan_date, symbol
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, (direction, cutoff)).fetchall()
    return [dict(r) for r in rows]


# ── Report ────────────────────────────────────────────────────────────────────

def report(trades: list[dict], title: str, min_intra: float, direction: str):
    if not trades:
        print(f"  No trades for: {title}")
        return

    df = pd.DataFrame(trades)
    total = len(df)
    wr    = df['win'].mean() * 100
    avg   = df['return_pct'].mean()
    med   = df['return_pct'].median()
    n_cat = df['is_catalyst'].sum()
    n_univ = df['in_universe'].sum()
    n_cat_outside = ((df['is_catalyst'] == 1) & (~df['in_universe'])).sum()

    spread_type = 'Put Spread' if direction == 'SHORT' else 'Call Spread'
    print(f"\n{'═'*65}")
    print(f"  {title}")
    print(f"  {spread_type} · 17 DTE · 0.33/0.67 SD · 2-day hold"
          + (f" · intra ≥{min_intra:.0f}%" if min_intra and direction == 'LONG'
             else (f" · intra ≤-{min_intra:.0f}%" if min_intra else "")))
    print(f"{'═'*65}")

    print(f"\n  Total signals  : {total}")
    print(f"  Win rate       : {wr:.1f}%")
    print(f"  Avg return     : {avg:+.1f}%")
    print(f"  Median return  : {med:+.1f}%")
    print(f"  Date range     : {df['scan_date'].min()} → {df['scan_date'].max()}")
    print(f"  Catalyst tickers: {n_cat} ({n_cat_outside} outside our universe)")
    print(f"  In-universe    : {n_univ} / {total}")

    # Exit breakdown
    print(f"\n{'─'*65}")
    print("  EXIT BREAKDOWN")
    for reason, cnt in df['exit_why'].value_counts().items():
        tag = '← win' if reason == 'TARGET' else ('← loss' if reason == 'STOP' else '← time')
        pct = cnt / total * 100
        sub = df[df['exit_why'] == reason]
        avg_r = sub['return_pct'].mean()
        print(f"  {reason:<12} {cnt:>4}  ({pct:>4.1f}%)  avg {avg_r:>+5.1f}%  {tag}")

    # Intraday momentum breakdown
    print(f"\n{'─'*65}")
    print("  DOES INTRADAY MOMENTUM PREDICT WIN?")
    buckets = [
        (f'intra < 2%',  df[abs(df['intra_chg']) <  2]),
        (f'intra 2-4%',  df[(abs(df['intra_chg']) >= 2) & (abs(df['intra_chg']) < 4)]),
        (f'intra 4-7%',  df[(abs(df['intra_chg']) >= 4) & (abs(df['intra_chg']) < 7)]),
        (f'intra ≥ 7%',  df[abs(df['intra_chg']) >= 7]),
    ]
    for label, sub in buckets:
        if len(sub) == 0:
            continue
        print(f"  {label:<14} {len(sub):>4} signals   WR {sub['win'].mean()*100:>5.1f}%"
              f"   Avg {sub['return_pct'].mean():>+5.1f}%")

    # Catalyst vs non-catalyst
    print(f"\n{'─'*65}")
    print("  CATALYST vs NON-CATALYST")
    for label, mask in [('Catalyst',     df['is_catalyst'] == 1),
                        ('Non-catalyst', df['is_catalyst'] == 0)]:
        sub = df[mask]
        if len(sub) == 0:
            continue
        print(f"  {label:<16} {len(sub):>4} signals   WR {sub['win'].mean()*100:>5.1f}%"
              f"   Avg {sub['return_pct'].mean():>+5.1f}%")

    # In-universe vs outside
    print(f"\n{'─'*65}")
    print("  IN-UNIVERSE vs CATALYST/OUT-OF-UNIVERSE")
    for label, mask in [('In our universe', df['in_universe']),
                        ('Outside universe', ~df['in_universe'])]:
        sub = df[mask]
        if len(sub) == 0:
            continue
        print(f"  {label:<20} {len(sub):>4} signals   WR {sub['win'].mean()*100:>5.1f}%"
              f"   Avg {sub['return_pct'].mean():>+5.1f}%")

    # Best sectors
    print(f"\n{'─'*65}")
    print("  BY SECTOR")
    sec_df = df.groupby('sector').agg(
        n     = ('return_pct', 'count'),
        wr    = ('win',        lambda x: x.mean() * 100),
        avg_r = ('return_pct', 'mean'),
    ).sort_values('avg_r', ascending=False)
    for sect, row in sec_df.iterrows():
        print(f"  {str(sect):<20} {row['n']:>3} trades   WR {row['wr']:>5.1f}%"
              f"   Avg {row['avg_r']:>+5.1f}%")

    # Top individual winners
    winners = df[df['win']].sort_values('return_pct', ascending=False).head(8)
    if len(winners):
        print(f"\n{'─'*65}")
        print("  TOP WINNERS")
        for _, r in winners.iterrows():
            cat = '★' if r['is_catalyst'] else ' '
            univ = '' if r['in_universe'] else ' ⚡OOV'
            print(f"  {cat} {r['symbol']:<6} {r['scan_date']}  "
                  f"intra {r['intra_chg']:>+5.1f}%  "
                  f"→ {r['return_pct']:>+6.1f}% ({r['exit_why']}){univ}")

    # Equity curve
    print(f"\n{'─'*65}")
    print("  EQUITY CURVE  ($5k account · $400 per trade)")
    capital = 5_000.0; trade_cost = 400.0; running = capital
    for _, r in df.sort_values('scan_date').iterrows():
        pnl = trade_cost * r['return_pct'] / 100
        running += pnl
    total_ret = (running - capital) / capital * 100
    print(f"  {total} trades  →  End capital ${running:>8,.0f}  ({total_ret:>+.1f}%)")

    verdict = wr >= 45 and avg > 0
    print(f"\n  VERDICT: {'✅ GO' if verdict else '❌ REVIEW'}")
    if not verdict:
        if wr < 45: print(f"  WR {wr:.1f}% < 45%")
        if avg <= 0: print(f"  Avg return {avg:+.1f}% ≤ 0")
    print(f"{'═'*65}\n")


# ── Weekly / daily P&L timeline ──────────────────────────────────────────────

def weekly_timeline(bull_trades: list[dict], bear_trades: list[dict],
                    trade_cost: float = 400.0, capital: float = 5_000.0):
    """Combined BULL + BEAR P&L: daily drill-down + weekly roll-up."""
    all_trades = bull_trades + bear_trades
    if not all_trades:
        print("  No trades to show.")
        return

    df = pd.DataFrame(all_trades)
    df['scan_date'] = pd.to_datetime(df['scan_date'])
    df['pnl_dollar'] = df['return_pct'] / 100 * trade_cost
    df = df.sort_values('scan_date')
    df['week'] = df['scan_date'].dt.to_period('W')

    print(f"\n{'═'*75}")
    print(f"  COMBINED BULL+BEAR  |  Daily P&L  →  Weekly Roll-up")
    print(f"  $400 per trade  ·  $5k simulated account")
    print(f"{'═'*75}")

    running = capital
    week_pnl_acc = 0.0

    prev_week = None
    for _, row in df.iterrows():
        wk = row['week']

        # Weekly header when week changes
        if prev_week is not None and wk != prev_week:
            tag = '✅' if week_pnl_acc > 0 else ('➖' if abs(week_pnl_acc) < 30 else '❌')
            sign = '+' if week_pnl_acc >= 0 else ''
            print(f"  {'─'*73}")
            print(f"  Week total {str(prev_week):<24} "
                  f"{sign}${week_pnl_acc:>6.0f}   running ${running:>8,.0f}  {tag}")
            print(f"  {'─'*73}")
            week_pnl_acc = 0.0

        prev_week = wk
        pnl = row['pnl_dollar']
        running += pnl
        week_pnl_acc += pnl

        cat  = '★' if row['is_catalyst'] else ' '
        dir_ = '📈' if row['direction'] == 'LONG' else '📉'
        sign = '+' if pnl >= 0 else ''
        result = '✅' if row['win'] else '❌'
        intra = row['intra_chg']
        print(f"  {cat}{row['scan_date'].strftime('%a %b %d')}  {dir_} {row['symbol']:<5}  "
              f"intra {intra:>+5.1f}%  {row['exit_why']:<8}  {sign}${pnl:>5.0f}  "
              f"${running:>8,.0f}  {result}")

    # Last week footer
    if week_pnl_acc != 0.0:
        tag = '✅' if week_pnl_acc > 0 else ('➖' if abs(week_pnl_acc) < 30 else '❌')
        sign = '+' if week_pnl_acc >= 0 else ''
        print(f"  {'─'*73}")
        print(f"  Week total {str(prev_week):<24} "
              f"{sign}${week_pnl_acc:>6.0f}   running ${running:>8,.0f}  {tag}")

    # ── Weekly summary table ──────────────────────────────────────────────────
    total_pnl = running - capital
    total_ret = total_pnl / capital * 100
    total_wr  = df['win'].mean() * 100

    print(f"\n{'═'*75}")
    print(f"  WEEKLY SUMMARY")
    print(f"  {'Week':<27} {'N':>4} {'WR%':>6} {'P&L $':>8} {'Running':>10}  Mix")
    print(f"  {'─'*70}")
    w_running = capital
    for week, wdf in df.groupby('week'):
        wpnl  = wdf['pnl_dollar'].sum()
        w_running += wpnl
        wwr   = wdf['win'].mean() * 100
        nb    = (wdf['direction'] == 'LONG').sum()
        ns    = (wdf['direction'] == 'SHORT').sum()
        tag   = '✅' if wpnl > 0 else ('➖' if abs(wpnl) < 30 else '❌')
        sign  = '+' if wpnl >= 0 else ''
        print(f"  {str(week):<27} {len(wdf):>4} {wwr:>5.1f}%  {sign}${wpnl:>6.0f}  "
              f"${w_running:>8,.0f}  {nb}B/{ns}S  {tag}")

    # ── Monthly summary ───────────────────────────────────────────────────────
    df['month'] = df['scan_date'].dt.to_period('M')
    print(f"\n  {'─'*70}")
    print(f"  MONTHLY")
    m_running = capital
    for month, mdf in df.groupby('month'):
        mpnl = mdf['pnl_dollar'].sum()
        m_running += mpnl
        mwr  = mdf['win'].mean() * 100
        nb   = (mdf['direction'] == 'LONG').sum()
        ns   = (mdf['direction'] == 'SHORT').sum()
        sign = '+' if mpnl >= 0 else ''
        print(f"  {str(month):<10} {len(mdf):>4} trades  WR {mwr:>5.1f}%  "
              f"{sign}${mpnl:>7.0f}  ({nb}B / {ns}S)")

    # Stats
    week_pnl_s = df.groupby('week')['pnl_dollar'].sum()
    print(f"\n  {'─'*70}")
    print(f"  TOTAL   {len(df)} trades  WR {total_wr:.1f}%  "
          f"P&L ${total_pnl:>+,.0f}  ({total_ret:>+.1f}%)")
    print(f"  Best week : {week_pnl_s.idxmax()}  ${week_pnl_s.max():>+,.0f}")
    print(f"  Worst week: {week_pnl_s.idxmin()}  ${week_pnl_s.min():>+,.0f}")
    print(f"  Profitable weeks: {(week_pnl_s > 0).sum()} / {len(week_pnl_s)}")
    print(f"{'═'*75}\n")


# ── Comparison: all A+ vs momentum-filtered ───────────────────────────────────

def run_comparison(days: int, direction: str):
    bearish = (direction == 'SHORT')
    print(f"\n{'═'*65}")
    print(f"  SCAN-LOG COMPARISON  |  {direction} → {'Put Spread' if bearish else 'Call Spread'}")
    print(f"  Using real A+ equity scan signals  ·  {days}-day window")
    print(f"{'═'*65}")
    print(f"\n  Loading IV proxies (yfinance)... ", end='', flush=True)

    # (label, min_intra, max_intra, catalyst_only, no_catalyst)
    if bearish:
        scenarios = [
            ('No filter (all A+ SHORT)',  0.0, 0.0, False, False),
            ('Intraday ≤-2%',            2.0, 0.0, False, False),
            ('Intraday ≤-2%, no cat',    2.0, 0.0, False, True),
            ('Intraday ≤-4%',            4.0, 0.0, False, False),
            ('Intraday ≤-7%',            7.0, 0.0, False, False),
        ]
    else:
        scenarios = [
            ('No filter (all A+ LONG)',  0.0, 0.0, False, False),
            ('No catalyst only',         0.0, 0.0, False, True),
            ('Intra 2%+ (wide)',         2.0, 0.0, False, True),
            ('Intra 2-4% (sweet spot)',  2.0, 4.0, False, True),
            ('Intra 2-7% (current gate)',2.0, 7.0, False, True),
            ('Catalyst only',            0.0, 0.0, True,  False),
        ]

    print("done.\n")
    print(f"  {'Scenario':<32} {'N':>5} {'WR%':>7} {'Avg%':>7} {'Total$':>8}  Verdict")
    print(f"  {'-'*63}")

    for row in scenarios:
        label, min_intra, max_intra, cat_only, no_cat = row
        signals = load_signals(days=days, direction=direction,
                               min_intra=min_intra, max_intra=max_intra,
                               catalyst_only=cat_only, no_catalyst=no_cat)
        trades = []
        for r in signals:
            t = simulate_signal(r, bearish=bearish)
            if t:
                trades.append(t)

        if not trades:
            print(f"  {label:<32} {'–':>5}   no trades")
            continue

        df  = pd.DataFrame(trades)
        wr  = df['win'].mean() * 100
        avg = df['return_pct'].mean()
        win_dollar = 400 * avg / 100 * len(df)  # total $ at $400/trade
        go  = '✅' if (wr >= 45 and avg > 0) else '❌'
        print(f"  {label:<32} {len(df):>5} {wr:>6.1f}% {avg:>+6.1f}% "
              f"  ${win_dollar:>+6.0f}  {go}")

    print()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Options backtest on real scan-log signals')
    parser.add_argument('--days',          type=int,   default=60,
                        help='Look-back window in days (default 60)')
    parser.add_argument('--min-intra',     type=float, default=2.0,
                        help='Min intraday %% move for entry (default 2.0, 0=no filter)')
    parser.add_argument('--max-intra',     type=float, default=0.0,
                        help='Max intraday %% move for entry, 0=no upper limit (default 0)')
    parser.add_argument('--direction',     default='LONG', choices=['LONG', 'SHORT', 'BOTH'],
                        help='Signal direction to test (default LONG)')
    parser.add_argument('--catalyst-only', action='store_true',
                        help='Only test catalyst-flagged signals')
    parser.add_argument('--no-catalyst',   action='store_true',
                        help='Exclude catalyst-flagged signals (is_catalyst=0)')
    parser.add_argument('--compare',       action='store_true',
                        help='Run scenario comparison table instead of full report')
    parser.add_argument('--timeline',      action='store_true',
                        help='Show combined BULL+BEAR weekly P&L timeline (BOTH direction)')
    parser.add_argument('--no-intra-filter', action='store_true',
                        help='Disable momentum filter (test all A+ signals)')
    args = parser.parse_args()

    min_intra = 0.0 if args.no_intra_filter else args.min_intra
    max_intra = args.max_intra

    if args.compare:
        dirs = ['LONG', 'SHORT'] if args.direction == 'BOTH' else [args.direction]
        for d in dirs:
            run_comparison(days=args.days, direction=d)

    elif args.timeline:
        # Load best-filter signals for both directions and show combined weekly P&L
        print(f"\nLoading IV proxies (yfinance)... ", end='', flush=True)

        bull_signals = load_signals(days=args.days, direction='LONG',
                                    min_intra=min_intra, max_intra=max_intra,
                                    no_catalyst=True)
        bear_signals = load_signals(days=args.days, direction='SHORT',
                                    min_intra=min_intra)
        all_syms = set(r['symbol'] for r in bull_signals + bear_signals)
        for sym in all_syms:
            get_iv_proxy(sym)
        print("done.")

        bull_trades = [t for r in bull_signals for t in [simulate_signal(r, bearish=False)] if t]
        bear_trades = [t for r in bear_signals for t in [simulate_signal(r, bearish=True)]  if t]

        print(f"  Bull (LONG): {len(bull_trades)} trades | Bear (SHORT): {len(bear_trades)} trades")
        weekly_timeline(bull_trades, bear_trades, trade_cost=400.0, capital=5_000.0)

    else:
        dirs = ['LONG', 'SHORT'] if args.direction == 'BOTH' else [args.direction]
        for d in dirs:
            bearish = (d == 'SHORT')
            signals = load_signals(days=args.days, direction=d,
                                   min_intra=min_intra, max_intra=max_intra,
                                   catalyst_only=args.catalyst_only,
                                   no_catalyst=args.no_catalyst)
            print(f"\nLoading IV for {len(set(r['symbol'] for r in signals))} symbols"
                  " (yfinance)... ", end='', flush=True)
            for r in signals:
                get_iv_proxy(r['symbol'])
            print("done.")

            trades = []
            for row in signals:
                t = simulate_signal(row, bearish=bearish)
                if t:
                    trades.append(t)

            title = f"A+ {d} Signals → {'Put' if bearish else 'Call'} Spread  ({args.days}d)"
            report(trades, title, min_intra, d)
