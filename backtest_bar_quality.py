"""
backtest_bar_quality.py
-----------------------
Validates the bar quality exhaustion gate on:

  SECTION A — 135 actual live LONG trades (May 1–Jun 22 2026)
              Applies gate retrospectively: would these trades have been blocked?

  SECTION B — Full 2025-2026 historical backtest via backtest_strategy.py
              bars_5m available from 2024-01-02; gate applied at simulated entry time.

Gate logic (from signal validation analysis Jun 22 2026):
  EXHAUSTION  : up_vol_ratio > 0.90 AND non-catalyst → SKIP (production threshold)
                (90%+ of bar volume on bullish bars = buying spent, reversal imminent)
                Analysis was done at 0.80 to see full picture; 0.90 chosen for production
                to reduce false positives (catches NUTX -$93, SMCI -$67 with fewer blocks)
                Evidence at >0.80: WR30=35%, WR$=20%, avg$=-$43  [N=34 evaluated, N=5 traded]

  QUALITY     : up_vol_ratio 0.55–0.80 AND last_close_pos ≥ 0.50 → GREEN (batting order)
                Evidence: 80% WR30, +0.399% avg 30m when combined with AT_HOD [N=20]

Run:
  venv/bin/python backtest_bar_quality.py           # Section A only (fast)
  venv/bin/python backtest_bar_quality.py --full    # Section A + B (runs full backtest ~2-4 min)
  venv/bin/python backtest_bar_quality.py --sym NVDA TSLA  # specific symbols for Section B
"""

import sqlite3
import sys
import warnings
from datetime import datetime, timedelta, timezone
from statistics import mean, stdev
from zoneinfo import ZoneInfo

warnings.filterwarnings('ignore')

BARS_DB    = 'market_data.db'
TRADES_DB  = 'trades.db'

EXHAUSTION_THRESHOLD = 0.90   # up_vol_ratio above this = buyers spent (matches auto_trader.py production)
N_BARS               = 6      # bars to inspect before entry
ASSUMED_ENTRY_ET     = '10:10:00'  # simulated backtest entry time (before 10:15 open)

ET_ZONE  = ZoneInfo('America/New_York')
UTC_ZONE = ZoneInfo('UTC')


# ── Bar quality helpers ──────────────────────────────────────────────────────

def et_to_utc(date_str: str, time_str: str) -> datetime:
    """Convert date+time in US/Eastern (auto-DST) to UTC-aware datetime."""
    dt_et = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
    return dt_et.replace(tzinfo=ET_ZONE).astimezone(UTC_ZONE)


def floor_5min(dt: datetime) -> datetime:
    return dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)


def get_bars(conn_bars, symbol: str, entry_utc: datetime, n: int = N_BARS):
    """Fetch n completed 5-min bars before entry_utc from bars_5m."""
    cutoff = floor_5min(entry_utc).isoformat()
    cur = conn_bars.execute(
        """SELECT ts_utc, open, high, low, close, volume
           FROM bars_5m
           WHERE symbol = ? AND ts_utc < ?
           ORDER BY ts_utc DESC LIMIT ?""",
        (symbol, cutoff, n)
    )
    cols = [d[0] for d in cur.description]
    return list(reversed([dict(zip(cols, row)) for row in cur.fetchall()]))


def compute_bar_quality(bars: list) -> dict | None:
    """
    Compute bar structure quality signals from a list of bar dicts.
    Returns None if insufficient bars.
    """
    if len(bars) < 3:
        return None

    closes  = [b['close']  for b in bars]
    opens   = [b['open']   for b in bars]
    highs   = [b['high']   for b in bars]
    lows    = [b['low']    for b in bars]
    volumes = [b['volume'] for b in bars]

    # up-volume ratio — fraction of total volume on bullish bars (close > open)
    up_vol    = sum(v for c, o, v in zip(closes, opens, volumes) if c > o)
    total_vol = sum(volumes)
    up_vol_ratio = up_vol / total_vol if total_vol > 0 else 0.5

    # last bar close position within its range (1=near high, 0=near low)
    last_range = highs[-1] - lows[-1]
    last_close_pos = (closes[-1] - lows[-1]) / last_range if last_range > 0 else 0.5

    is_exhausted = up_vol_ratio > EXHAUSTION_THRESHOLD

    # composite quality score for batting order (0–1)
    # peaks at up_vol 0.65–0.75, not at maximum up_vol
    if up_vol_ratio > EXHAUSTION_THRESHOLD:
        quality = 0.0   # exhausted — worst
    elif up_vol_ratio >= 0.55:
        quality = (1.0 - abs(up_vol_ratio - 0.67) / 0.13) * 0.6 + last_close_pos * 0.4
    else:
        quality = up_vol_ratio * 0.4 + last_close_pos * 0.2

    return {
        'up_vol_ratio':   round(up_vol_ratio, 3),
        'last_close_pos': round(last_close_pos, 3),
        'is_exhausted':   is_exhausted,
        'quality':        round(max(0.0, min(1.0, quality)), 3),
        'n_bars':         len(bars),
    }


# ── Section A — actual live trades ──────────────────────────────────────────

def section_a(conn_bars):
    print("=" * 80)
    print("SECTION A — Actual Live LONG Trades (May 1 – Jun 22 2026)")
    print("           Gate applied retrospectively to all 135 trades")
    print("=" * 80)

    conn_t = sqlite3.connect(TRADES_DB)
    conn_t.row_factory = sqlite3.Row

    trades = conn_t.execute("""
        SELECT t.id, t.symbol, t.entry_date, t.entry_time, t.pnl,
               t.exit_reason, t.setup_type,
               COALESCE(sl.is_catalyst, 0) is_catalyst
        FROM trades t
        LEFT JOIN scan_log sl ON sl.entry_trade_id = t.id
        WHERE t.entry_date >= '2026-05-01'
          AND t.side = 'LONG'
          AND t.setup_type != 'RECONCILED'
          AND t.pnl IS NOT NULL
        ORDER BY t.entry_date, t.entry_time
    """).fetchall()
    conn_t.close()

    results = []
    no_bars_count = 0

    for t in trades:
        try:
            entry_utc = et_to_utc(t['entry_date'], t['entry_time'])
        except Exception:
            continue

        bars = get_bars(conn_bars, t['symbol'], entry_utc)
        if not bars:
            no_bars_count += 1
            bq = None
        else:
            bq = compute_bar_quality(bars)

        blocked = bq and bq['is_exhausted'] and not t['is_catalyst']

        results.append({
            'date':        t['entry_date'],
            'symbol':      t['symbol'],
            'pnl':         t['pnl'],
            'is_catalyst': t['is_catalyst'],
            'exit':        t['exit_reason'],
            'bq':          bq,
            'blocked':     blocked,
        })

    # ── Results ──────────────────────────────────────────────────────────────
    all_pnl     = [r['pnl'] for r in results]
    blocked     = [r for r in results if r['blocked']]
    passed      = [r for r in results if not r['blocked']]
    no_bars_r   = [r for r in results if r['bq'] is None]

    def stats(pnls, label):
        if not pnls: return f"  {label}: N=0"
        wins = sum(1 for p in pnls if p > 0)
        wr   = wins / len(pnls) * 100
        avg  = mean(pnls)
        tot  = sum(pnls)
        std  = stdev(pnls) if len(pnls) > 1 else 0
        return (f"  {label:<20} N={len(pnls):>3}  WR={wr:>5.1f}%  "
                f"avg=${avg:>+7.2f}  total=${tot:>+8.2f}  std=${std:>6.2f}")

    print(f"\n{stats([r['pnl'] for r in results], 'BASELINE (all 135)')}")
    print(f"{stats([r['pnl'] for r in passed],  'AFTER GATE (passed)')}")
    print(f"{stats([r['pnl'] for r in blocked], 'BLOCKED trades')}")
    print(f"\n  No bars (passed through): {no_bars_count}")

    base_tot = sum(all_pnl)
    pass_tot = sum(r['pnl'] for r in passed)
    delta    = pass_tot - base_tot

    print(f"\n  P&L delta from gate:  ${delta:+.2f}")
    annual_days = 50   # approx trading days in dataset
    annual_proj = delta / annual_days * 252
    print(f"  Annualised projection: ${annual_proj:+,.0f}/year  (based on {annual_days} trading days)")

    # Trade-by-trade for blocked
    if blocked:
        print(f"\n  BLOCKED TRADES ({len(blocked)}):")
        print(f"  {'Date':<12} {'Sym':<6}  {'up_vol':>6}  {'cat':>3}  {'PnL':>8}  {'Exit'}")
        for r in blocked:
            bq = r['bq']
            print(f"  {r['date']:<12} {r['symbol']:<6}  "
                  f"{bq['up_vol_ratio']:>6.3f}  {'Y' if r['is_catalyst'] else 'N':>3}  "
                  f"{r['pnl']:>+8.2f}  {(r['exit'] or '')[:30]}")

    # up_vol_ratio distribution (what does the full population look like)
    print(f"\n  UP-VOL RATIO DISTRIBUTION (all trades with bar data):")
    print(f"  {'Bucket':<25}  {'N':>4}  {'WR':>6}  {'avg PnL':>8}  {'total':>8}")
    buckets = [
        ('<40% (sellers dominate)',  lambda x: x < 0.40),
        ('40-55% (balanced)',        lambda x: 0.40 <= x < 0.55),
        ('55-70% (buyers lead)',     lambda x: 0.55 <= x < 0.70),
        ('70-80% (strong buyers)',   lambda x: 0.70 <= x < 0.80),
        ('>80% EXHAUSTED',          lambda x: x >= 0.80),
    ]
    for label, fn in buckets:
        sub = [r for r in results if r['bq'] and fn(r['bq']['up_vol_ratio'])]
        if not sub: continue
        pnls = [r['pnl'] for r in sub]
        wr   = sum(1 for p in pnls if p > 0) / len(pnls) * 100
        print(f"  {label:<25}  {len(pnls):>4}  {wr:>5.1f}%  {mean(pnls):>+8.2f}  {sum(pnls):>+8.2f}")

    return results


# ── Section B — full historical backtest ────────────────────────────────────

def section_b(conn_bars, symbols=None):
    print("\n" + "=" * 80)
    print("SECTION B — Historical Backtest 2024-2026 with Exhaustion Gate")
    print("           bars_5m coverage: 2024-01-02 → today")
    print("=" * 80)

    # Import backtest_strategy functions
    import importlib.util, os, types
    spec = importlib.util.spec_from_file_location(
        'backtest_strategy', os.path.join(os.path.dirname(__file__), 'backtest_strategy.py')
    )
    bs = types.ModuleType('backtest_strategy')
    bs.__spec__ = spec
    spec.loader.exec_module(bs)

    import yfinance as yf
    import pandas as pd

    # Run SPY regime for 2024-2026
    spy_regime = bs.build_spy_regime('2024-01-01', bs.END_DATE)

    # Use passed symbols or default universe subset for speed
    if symbols is None:
        # Use auto_trader SECTOR_MAP keys if available, else full backtest universe
        try:
            from auto_trader import SECTOR_MAP
            universe = sorted(SECTOR_MAP.keys())
        except Exception:
            universe = ['NVDA', 'PLTR', 'MSFT', 'AAPL', 'TSLA', 'MARA', 'IONQ',
                        'SOUN', 'RKLB', 'ACLS', 'LRCX', 'AMAT', 'ARM', 'SMCI']
    else:
        universe = symbols

    print(f"\n  Running on {len(universe)} symbols for 2024-01-01 → {bs.END_DATE}...")
    print("  (fetching daily bars + applying bar quality gate at each entry)\n")

    all_trades_baseline = []
    all_trades_gated    = []
    bar_cache = {}   # (symbol, date) → quality dict

    def get_bq_for_entry(symbol, date_str):
        key = (symbol, date_str)
        if key in bar_cache:
            return bar_cache[key]
        try:
            entry_utc = et_to_utc(date_str, ASSUMED_ENTRY_ET)
            bars = get_bars(conn_bars, symbol, entry_utc)
            result = compute_bar_quality(bars) if bars else None
        except Exception:
            result = None
        bar_cache[key] = result
        return result

    n_blocked = 0
    n_no_bars = 0

    for sym in universe:
        try:
            df_sym = bs.backtest_symbol(sym, spy_regime)
        except Exception as e:
            print(f"  {sym}: error — {e}")
            continue
        if df_sym is None or len(df_sym) == 0:
            continue

        for _, row in df_sym.iterrows():
            trade = row.to_dict()
            all_trades_baseline.append(trade)

            bq = get_bq_for_entry(sym, trade['date'])

            if bq is None:
                n_no_bars += 1
                all_trades_gated.append(trade)   # no data = pass through
            elif bq['is_exhausted']:
                n_blocked += 1
                # blocked = skip this trade (don't add to gated list)
            else:
                all_trades_gated.append(trade)

    if not all_trades_baseline:
        print("  No trades generated. Check symbol list or data.")
        return

    df_base  = pd.DataFrame(all_trades_baseline)
    df_gated = pd.DataFrame(all_trades_gated)

    def report(df, label):
        if len(df) == 0:
            print(f"\n  {label}: no trades")
            return
        wins = df[df['pnl_usd'] > 0]
        loss = df[df['pnl_usd'] <= 0]
        wr   = len(wins) / len(df) * 100
        tot  = df['pnl_usd'].sum()
        avg  = df['pnl_usd'].mean()
        n_yr = max(1, (pd.to_datetime(df['date'].max()) -
                       pd.to_datetime(df['date'].min())).days / 365)
        ann  = tot / n_yr
        print(f"\n  {label}")
        print(f"    Trades: {len(df):>5}  WR: {wr:>5.1f}%  Avg: ${avg:>+7.2f}  "
              f"Total: ${tot:>+10,.0f}  Annual: ${ann:>+9,.0f}/yr")
        print(f"    Avg winner: ${wins['pnl_usd'].mean():>+7.2f}  "
              f"Avg loser: ${loss['pnl_usd'].mean():>+7.2f}" if len(wins) and len(loss) else "")

        # by year
        for yr in sorted(df['year'].unique()):
            sub  = df[df['year'] == yr]
            sw   = sub[sub['pnl_usd'] > 0]
            swr  = len(sw) / len(sub) * 100 if len(sub) else 0
            print(f"      {yr}  N={len(sub):>4}  WR={swr:>5.1f}%  "
                  f"avg=${sub['pnl_usd'].mean():>+7.2f}  total=${sub['pnl_usd'].sum():>+9,.0f}")

    report(df_base,  "BASELINE (no gate)")
    report(df_gated, "AFTER EXHAUSTION GATE")

    # delta
    delta_pnl = df_gated['pnl_usd'].sum() - df_base['pnl_usd'].sum()
    n_yr = max(1, (pd.to_datetime(df_base['date'].max()) -
                   pd.to_datetime(df_base['date'].min())).days / 365)
    print(f"\n  GATE IMPACT:")
    print(f"    Trades blocked:    {n_blocked:>5}  (no-bars pass-through: {n_no_bars})")
    print(f"    P&L delta:         ${delta_pnl:>+10,.0f}")
    print(f"    Annual P&L delta:  ${delta_pnl/n_yr:>+9,.0f}/year")

    # show blocked trade stats (what we skipped)
    blocked_pnl = [r['pnl_usd'] for r in all_trades_baseline
                   if r not in [t for t in all_trades_gated]]
    if blocked_pnl:
        bl_wr  = sum(1 for p in blocked_pnl if p > 0) / len(blocked_pnl) * 100
        bl_avg = mean(blocked_pnl)
        print(f"    Blocked trades avg: ${bl_avg:>+7.2f}  WR={bl_wr:.1f}%")


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    run_full = '--full' in sys.argv
    sym_args = []
    for i, arg in enumerate(sys.argv[1:]):
        if arg == '--sym':
            sym_args = sys.argv[i+2:]
            break

    conn_bars = sqlite3.connect(BARS_DB)

    results_a = section_a(conn_bars)

    if run_full or sym_args:
        section_b(conn_bars, symbols=sym_args if sym_args else None)
    else:
        print("\n  [Run with --full to execute the 2024-2026 historical backtest]")
        print("  [Run with --sym NVDA TSLA ... to test specific symbols]")

    conn_bars.close()
