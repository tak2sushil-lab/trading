"""
backtest_scalp.py — OPT_SCALP Mode A backtest

Uses scan_log A+ LONG entries + market_data.db 5-min OHLCV bars to simulate
ATM call scalp outcomes. Stock % move is used as a proxy for call P&L via a
simplified delta-scaling model (ATM call ≈ 0.50 delta, +1 contract = $100 × delta × stock_move).

Exit rules (match live system):
  - Target: +80% gain on premium (SCALP_PROFIT_MULT = 1.80)
  - Stop:   -50% loss on premium (SCALP_STOP_MULT   = 0.50)
  - Time:   3 calendar days (SCALP_MAX_DAYS = 3)

Premium proxy: 1.5% × underlying price (ATM ~DTE-10 rough estimate).
Delta proxy:   0.50 (ATM).

Run:
    venv/bin/python backtest_scalp.py
"""

import sqlite3
import os
from datetime import date, datetime, timedelta

DB_PATH      = os.getenv('DB_PATH',      'trades.db')
MARKET_DB    = os.getenv('MARKET_DB',    'market_data.db')

SCALP_UNIVERSE = {
    'IONQ', 'MARA', 'WULF', 'RIOT', 'SOUN', 'RKLB', 'HIMS', 'AFRM',
    'CELH', 'UPST', 'RIVN', 'RDW', 'JOBY', 'HOOD', 'NOK',
}
SCALP_PROFIT_MULT = 1.80
SCALP_STOP_MULT   = 0.50
SCALP_MAX_DAYS    = 3
PREMIUM_PCT       = 0.015   # ATM call premium ≈ 1.5% of stock price
DELTA             = 0.50


def load_scan_log_aplus(conn: sqlite3.Connection) -> list[dict]:
    c = conn.cursor()
    c.execute("""
        SELECT symbol, scan_date, scan_time, price, score
        FROM scan_log
        WHERE grade='A+' AND direction='LONG' AND symbol IN ({})
        ORDER BY scan_date, scan_time
    """.format(','.join('?' * len(SCALP_UNIVERSE))), list(SCALP_UNIVERSE))
    rows = c.fetchall()
    return [
        {'symbol': r[0], 'scan_date': r[1], 'scan_time': r[2],
         'price': r[3], 'score': r[4]}
        for r in rows
    ]


def load_bars(mconn: sqlite3.Connection, symbol: str,
              start: str, end: str) -> list[tuple]:
    """Return (ts_utc, open, high, low, close, volume) rows."""
    c = mconn.cursor()
    c.execute("""
        SELECT ts_utc, open, high, low, close, volume
        FROM bars_5m
        WHERE symbol=? AND ts_utc>=? AND ts_utc<?
        ORDER BY ts_utc
    """, (symbol, start, end))
    return c.fetchall()


def simulate_trade(entry_price: float, entry_date_str: str,
                   entry_time_str: str, symbol: str,
                   mconn: sqlite3.Connection) -> dict:
    """
    Simulate an ATM call scalp. Returns outcome dict with exit_reason and return_pct.
    """
    premium_per_share = entry_price * PREMIUM_PCT
    premium_total     = round(premium_per_share * 100, 2)   # 1 contract = 100 shares

    target_px  = entry_price * (1 + premium_per_share * SCALP_PROFIT_MULT / entry_price)
    stop_px    = entry_price * (1 - premium_per_share * (1 - SCALP_STOP_MULT) / entry_price)

    # 3-day window of bars
    start_dt = datetime.strptime(f"{entry_date_str} {entry_time_str}", '%Y-%m-%d %H:%M:%S')
    end_dt   = start_dt + timedelta(days=SCALP_MAX_DAYS + 1)

    bars = load_bars(mconn, symbol, start_dt.isoformat(), end_dt.isoformat())

    # Skip bars before entry time
    entry_passed = False
    for bar in bars:
        ts_str = bar[0]
        ts     = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        # Convert to ET by subtracting 4 or 5 hours (approximate — bars stored in UTC)
        ts_et  = ts.replace(tzinfo=None) - timedelta(hours=4)

        bar_date = ts_et.date()
        bar_dt   = ts_et

        if not entry_passed:
            if bar_date < date.fromisoformat(entry_date_str):
                continue
            if bar_date == date.fromisoformat(entry_date_str):
                bar_time_str = bar_dt.strftime('%H:%M:%S')
                if bar_time_str < entry_time_str:
                    continue
            entry_passed = True

        # Check 3-day time limit
        days_elapsed = (bar_date - date.fromisoformat(entry_date_str)).days
        if days_elapsed >= SCALP_MAX_DAYS:
            exit_price = bar[4]   # close of last valid bar
            # Delta-scaled P&L proxy; theta decay ≈ 30% of premium over 3 days on short DTE
            pnl = round((exit_price - entry_price) * DELTA * 100 - premium_total * 0.30, 2)
            return_pct = max(round(pnl / premium_total * 100, 1),
                             round((SCALP_STOP_MULT - 1) * 100, 1))  # floor at stop level
            return {'exit_reason': 'TIME_STOP', 'return_pct': return_pct, 'exit_price': exit_price,
                    'premium': premium_total}

        high = bar[2]
        low  = bar[3]

        # Target: stock moved up enough that call gained 80%
        # Proxy: call value ≈ premium × PROFIT_MULT when stock at target
        # ATM call moves ≈ delta × stock_move → gain = delta × (high - entry) × 100
        call_gain_if_high = (high - entry_price) * DELTA * 100
        if call_gain_if_high >= premium_total * (SCALP_PROFIT_MULT - 1):
            return {'exit_reason': 'AUTO_TARGET', 'return_pct': round((SCALP_PROFIT_MULT - 1) * 100, 1),
                    'exit_price': high, 'premium': premium_total}

        # Stop: stock moved down enough that call lost 50%
        call_loss_if_low = (entry_price - low) * DELTA * 100
        if call_loss_if_low >= premium_total * (1 - SCALP_STOP_MULT):
            return {'exit_reason': 'AUTO_STOP', 'return_pct': round((SCALP_STOP_MULT - 1) * 100, 1),
                    'exit_price': low, 'premium': premium_total}

    # No bar hit stop/target — time-stopped at end of window
    if bars:
        exit_price = bars[-1][4]
        pnl = round((exit_price - entry_price) * DELTA * 100 - premium_total * 0.30, 2)
        return_pct = max(round(pnl / premium_total * 100, 1), round((SCALP_STOP_MULT - 1) * 100, 1))
    else:
        return_pct = round((SCALP_STOP_MULT - 1) * 100, 1)
        exit_price = entry_price

    return {'exit_reason': 'TIME_STOP', 'return_pct': return_pct,
            'exit_price': exit_price if bars else entry_price, 'premium': premium_total}


def main():
    print("=" * 60)
    print("OPT_SCALP Mode A Backtest")
    print("Using scan_log A+ LONG entries + market_data.db 5-min bars")
    print("=" * 60)

    conn  = sqlite3.connect(DB_PATH)
    mconn = sqlite3.connect(MARKET_DB)

    entries = load_scan_log_aplus(conn)
    conn.close()

    if not entries:
        print("No A+ LONG scan_log entries found for SCALP_UNIVERSE symbols.")
        print(f"Symbols: {', '.join(sorted(SCALP_UNIVERSE))}")
        return

    print(f"\nFound {len(entries)} A+ LONG scan entries across {len(set(e['symbol'] for e in entries))} symbols\n")

    results = []
    skipped = 0
    for e in entries:
        sym   = e['symbol']
        price = e['price']
        if not price or price <= 0:
            skipped += 1
            continue
        outcome = simulate_trade(price, e['scan_date'], e['scan_time'], sym, mconn)
        outcome['symbol']    = sym
        outcome['scan_date'] = e['scan_date']
        outcome['score']     = e['score']
        results.append(outcome)

    mconn.close()

    if not results:
        print(f"No results (skipped {skipped} entries with missing price).")
        return

    # Aggregate stats
    wins       = [r for r in results if r['return_pct'] > 0]
    losses     = [r for r in results if r['return_pct'] <= 0]
    wr         = round(len(wins) / len(results) * 100, 1)
    avg_win    = round(sum(r['return_pct'] for r in wins)   / len(wins),   1) if wins   else 0
    avg_loss   = round(sum(r['return_pct'] for r in losses) / len(losses), 1) if losses else 0
    avg_r      = round(sum(r['return_pct'] for r in results) / len(results), 1)
    expectancy = round((wr/100 * avg_win) + ((1 - wr/100) * avg_loss), 1)

    # Exit reason breakdown
    exit_counts: dict[str, int] = {}
    for r in results:
        k = r['exit_reason']
        exit_counts[k] = exit_counts.get(k, 0) + 1

    # By symbol
    sym_stats: dict[str, list] = {}
    for r in results:
        sym_stats.setdefault(r['symbol'], []).append(r['return_pct'])

    print(f"{'─'*60}")
    print(f"  Total trades     : {len(results)}")
    print(f"  Win rate         : {wr}%")
    print(f"  Avg win          : +{avg_win}%")
    print(f"  Avg loss         : {avg_loss}%")
    print(f"  Avg return       : {avg_r:+.1f}%")
    print(f"  Expectancy       : {expectancy:+.1f}%")
    print(f"  Skipped (no px)  : {skipped}")
    print(f"{'─'*60}")
    print()
    print("Exit reason breakdown:")
    for k, v in sorted(exit_counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<20} {v:>4} trades ({v/len(results)*100:.0f}%)")

    print()
    print("By symbol (>= 3 trades):")
    for sym in sorted(sym_stats, key=lambda s: -len(sym_stats[s])):
        rs  = sym_stats[sym]
        if len(rs) < 3:
            continue
        sw  = round(sum(1 for r in rs if r > 0) / len(rs) * 100, 0)
        sa  = round(sum(rs) / len(rs), 1)
        print(f"  {sym:<6} {len(rs):>3} trades  WR {sw:.0f}%  avg {sa:+.1f}%")

    # Verdict
    print()
    if wr >= 55 and expectancy >= 10:
        print("VERDICT: ✅ Strategy shows edge — deploy with current parameters")
    elif wr >= 45 and expectancy >= 0:
        print("VERDICT: ⚠️  Marginal edge — monitor closely, consider tighter gates")
    else:
        print("VERDICT: ❌ No clear edge — review entry criteria before deploying")
    print()


if __name__ == '__main__':
    main()
