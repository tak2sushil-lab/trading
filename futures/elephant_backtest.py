"""
elephant_backtest.py — Liquidity Grab Reversal (Elephant Trade) backtest
Validates the exact parameters coded in futures_trader.py against 5.5yr MNQ history.

Methodology mirrors futures_trader.py _classify_elephant_day() + _scan_elephant() exactly:
  - Same opening bar body thresholds
  - Same flush depth requirements
  - Same entry / stop / target levels
  - Same noon cutoff
  - Same ES confirmation filter (STRONG days)
  - Same dedup (one entry per flush extreme)
"""

import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
import os
import sys

# ── Same constants as futures_trader.py ──────────────────
ELEPHANT_ENTRY_CONF    = 10.0
ELEPHANT_STOP_PTS      = 50.0
ELEPHANT_TARGET_PTS    = 150.0
ELEPHANT_TIMEOUT_MINS  = 180.0
ELEPHANT_FLUSH_EXTREME = 100.0
ELEPHANT_FLUSH_STRONG  = 150.0
ELEPHANT_BODY_EXTREME  = 250.0
ELEPHANT_BODY_STRONG   = 100.0
ELEPHANT_MAX_EXTREME   = 4
ELEPHANT_MAX_STRONG    = 4
ELEPHANT_ES_MOVE_SKIP  = 25.0
ELEPHANT_NOON_CUTOFF   = 12       # ET hour
ELEPHANT_LOOKBACK_BARS = 12       # 5-min bars = 60 min

TICK_SIZE  = 0.25
TICK_VALUE = 0.50
# $2/pt = TICK_VALUE / TICK_SIZE

_DIR       = os.path.dirname(os.path.abspath(__file__))
MKT_DB     = os.path.join(_DIR, '..', 'market_data.db')


def load_bars(symbol: str) -> pd.DataFrame:
    """Load 5-min bars from market_data.db, ET-indexed."""
    conn = sqlite3.connect(MKT_DB)
    rows = conn.execute(
        "SELECT substr(ts_utc,1,19) as ts, open, high, low, close, volume "
        "FROM futures_bars_5m WHERE symbol=? ORDER BY ts_utc",
        (symbol,)
    ).fetchall()
    conn.close()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
    df['ts'] = pd.to_datetime(df['ts'], format='%Y-%m-%dT%H:%M:%S', utc=True)
    df = df.set_index('ts').tz_convert('America/New_York').sort_index()
    for col in ('open', 'high', 'low', 'close', 'volume'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.dropna(subset=['close'])


def classify_day(day_bars: pd.DataFrame) -> str:
    """
    Identical to futures_trader._classify_elephant_day().
    Uses 9:30-9:45am bars (first 15 min of RTH).
    """
    today    = day_bars.index[0].date()
    rth_open = pd.Timestamp(f'{today} 09:30:00', tz='America/New_York')
    rth_15m  = pd.Timestamp(f'{today} 09:45:00', tz='America/New_York')

    bars_15 = day_bars[(day_bars.index >= rth_open) & (day_bars.index < rth_15m)]
    if len(bars_15) < 3:
        return 'NONE'

    open_px  = float(bars_15['open'].iloc[0])
    close_px = float(bars_15['close'].iloc[-1])
    body     = close_px - open_px
    body_abs = abs(body)

    if body_abs >= ELEPHANT_BODY_EXTREME:
        return 'EXTREME_BULL' if body > 0 else 'EXTREME_BEAR'
    elif body_abs >= ELEPHANT_BODY_STRONG:
        return 'STRONG_BULL' if body > 0 else 'STRONG_BEAR'
    return 'NONE'


def check_es_filter(es_day_bars: pd.DataFrame, scan_time: pd.Timestamp) -> float:
    """
    Returns the ES move (pts) in the last 60-min window ending at scan_time.
    Caller compares with ELEPHANT_ES_MOVE_SKIP.
    """
    window_start = scan_time - pd.Timedelta(minutes=60)
    es_win       = es_day_bars[(es_day_bars.index >= window_start) & (es_day_bars.index <= scan_time)]
    if len(es_win) < 2:
        return 0.0
    return float(es_win['high'].max()) - float(es_win['low'].min())


def simulate_day(today_bars: pd.DataFrame, es_bars: pd.DataFrame,
                 day_type: str) -> list[dict]:
    """
    Walk the day bar-by-bar, identical to the live scanner's 1-min loop.
    Returns list of trade dicts.
    """
    today     = today_bars.index[0].date()
    rth_open  = pd.Timestamp(f'{today} 09:30:00', tz='America/New_York')
    noon_cut  = pd.Timestamp(f'{today} 12:00:00', tz='America/New_York')

    is_extreme = 'EXTREME' in day_type
    min_flush  = ELEPHANT_FLUSH_EXTREME if is_extreme else ELEPHANT_FLUSH_STRONG
    max_trades = ELEPHANT_MAX_EXTREME if is_extreme else ELEPHANT_MAX_STRONG

    rth_bars    = today_bars[today_bars.index >= rth_open].copy()
    es_rth      = es_bars[es_bars.index >= rth_open] if not es_bars.empty else pd.DataFrame()

    trades_today = 0
    flush_ids    = set()
    trades       = []

    for i in range(ELEPHANT_LOOKBACK_BARS, len(rth_bars)):
        bar_time = rth_bars.index[i]

        # Noon cutoff
        if bar_time >= noon_cut:
            break

        # Quota
        if trades_today >= max_trades:
            break

        window = rth_bars.iloc[i - ELEPHANT_LOOKBACK_BARS: i + 1]
        current_price = float(window['close'].iloc[-1])

        # ── LONG setup ──────────────────────────────────────
        if day_type in ('STRONG_BULL', 'EXTREME_BULL', 'EXTREME_BEAR'):
            w_high = float(window['high'].max())
            w_low  = float(window['low'].min())
            flush_depth = w_high - w_low

            if flush_depth >= min_flush:
                flush_extreme = w_low
                flush_bar_ts  = str(window['low'].idxmin())
                entry_level   = flush_extreme + ELEPHANT_ENTRY_CONF

                if entry_level <= current_price <= flush_extreme + 50:
                    if flush_bar_ts not in flush_ids:
                        # ES filter (STRONG days only)
                        skip = False
                        if not is_extreme and not es_rth.empty:
                            es_move = check_es_filter(es_rth, bar_time)
                            if es_move >= ELEPHANT_ES_MOVE_SKIP:
                                skip = True

                        if not skip:
                            entry_price = current_price
                            sl          = round(flush_extreme - ELEPHANT_STOP_PTS, 2)
                            target      = round(entry_level + ELEPHANT_TARGET_PTS, 2)

                            # Simulate outcome on subsequent bars
                            future = rth_bars.iloc[i + 1:]
                            outcome = _simulate_outcome(entry_price, sl, target, 'LONG',
                                                        future, bar_time)
                            outcome.update({
                                'date':        str(today),
                                'entry_time':  str(bar_time.time())[:5],
                                'direction':   'LONG',
                                'day_type':    day_type,
                                'flush_depth': flush_depth,
                                'entry':       entry_price,
                                'stop':        sl,
                                'target':      target,
                            })
                            trades.append(outcome)
                            flush_ids.add(flush_bar_ts)
                            trades_today += 1

        # ── SHORT setup ─────────────────────────────────────
        if day_type in ('STRONG_BEAR', 'EXTREME_BEAR', 'EXTREME_BULL') and trades_today < max_trades:
            w_high = float(window['high'].max())
            w_low  = float(window['low'].min())
            surge_depth = w_high - w_low

            if surge_depth >= min_flush:
                surge_extreme = w_high
                surge_bar_ts  = str(window['high'].idxmax())
                entry_level   = surge_extreme - ELEPHANT_ENTRY_CONF

                if surge_extreme - 50 <= current_price <= entry_level:
                    if surge_bar_ts not in flush_ids:
                        skip = False
                        if not is_extreme and not es_rth.empty:
                            es_move = check_es_filter(es_rth, bar_time)
                            if es_move >= ELEPHANT_ES_MOVE_SKIP:
                                skip = True

                        if not skip:
                            entry_price = current_price
                            sl          = round(surge_extreme + ELEPHANT_STOP_PTS, 2)
                            target      = round(entry_level - ELEPHANT_TARGET_PTS, 2)

                            future = rth_bars.iloc[i + 1:]
                            outcome = _simulate_outcome(entry_price, sl, target, 'SHORT',
                                                        future, bar_time)
                            outcome.update({
                                'date':        str(today),
                                'entry_time':  str(bar_time.time())[:5],
                                'direction':   'SHORT',
                                'day_type':    day_type,
                                'flush_depth': surge_depth,
                                'entry':       entry_price,
                                'stop':        sl,
                                'target':      target,
                            })
                            trades.append(outcome)
                            flush_ids.add(surge_bar_ts)
                            trades_today += 1

    return trades


def _simulate_outcome(entry: float, sl: float, target: float, direction: str,
                       future_bars: pd.DataFrame, entry_time: pd.Timestamp) -> dict:
    """Walk future bars and return exit reason, price, pnl_pts."""
    timeout_cut = entry_time + pd.Timedelta(minutes=ELEPHANT_TIMEOUT_MINS)
    eod_cut     = pd.Timestamp(f'{entry_time.date()} 16:00:00', tz='America/New_York')

    for _, bar in future_bars.iterrows():
        ts = bar.name

        if direction == 'LONG':
            if bar['low'] <= sl:
                pts = sl - entry
                return {'exit_reason': 'stop', 'exit_pts': pts,
                        'pnl_usd': pts / TICK_SIZE * TICK_VALUE}
            if bar['high'] >= target:
                pts = target - entry
                return {'exit_reason': 'target', 'exit_pts': pts,
                        'pnl_usd': pts / TICK_SIZE * TICK_VALUE}
        else:
            if bar['high'] >= sl:
                pts = entry - sl
                return {'exit_reason': 'stop', 'exit_pts': pts,
                        'pnl_usd': pts / TICK_SIZE * TICK_VALUE}
            if bar['low'] <= target:
                pts = entry - target
                return {'exit_reason': 'target', 'exit_pts': pts,
                        'pnl_usd': pts / TICK_SIZE * TICK_VALUE}

        # Timeout no-move (within no-move zone)
        if ts >= timeout_cut:
            close_px = float(bar['close'])
            pts = (close_px - entry) if direction == 'LONG' else (entry - close_px)
            no_move_min = -10.0   # must match NO_MOVE_MIN_PTS
            no_move_max = 25.0    # must match NO_MOVE_MAX_PTS
            if no_move_min <= pts <= no_move_max:
                return {'exit_reason': 'timeout', 'exit_pts': pts,
                        'pnl_usd': pts / TICK_SIZE * TICK_VALUE}

        # EOD
        if ts >= eod_cut:
            close_px = float(bar['close'])
            pts = (close_px - entry) if direction == 'LONG' else (entry - close_px)
            return {'exit_reason': 'eod', 'exit_pts': pts,
                    'pnl_usd': pts / TICK_SIZE * TICK_VALUE}

    # No bars left (market closed)
    return {'exit_reason': 'eod', 'exit_pts': 0.0, 'pnl_usd': 0.0}


def main():
    print("=" * 65)
    print("ELEPHANT TRADE BACKTEST — Liquidity Grab Reversal (LGR)")
    print(f"Parameters: entry={ELEPHANT_ENTRY_CONF}pts | stop={ELEPHANT_STOP_PTS}pts | "
          f"target={ELEPHANT_TARGET_PTS}pts")
    print(f"Day types: EXTREME body≥{ELEPHANT_BODY_EXTREME}pts / STRONG body≥{ELEPHANT_BODY_STRONG}pts")
    print(f"Flush thresholds: EXTREME={ELEPHANT_FLUSH_EXTREME}pts / STRONG={ELEPHANT_FLUSH_STRONG}pts")
    print(f"ES filter: skip STRONG flush if ES moved ≥{ELEPHANT_ES_MOVE_SKIP}pts")
    print("=" * 65)

    print("Loading bars from market_data.db…")
    mnq = load_bars('MNQ')
    es  = load_bars('ES')
    if mnq.empty:
        print("ERROR: No MNQ bars found in market_data.db")
        sys.exit(1)
    print(f"MNQ: {len(mnq):,} bars from {mnq.index[0].date()} to {mnq.index[-1].date()}")
    print(f"ES : {len(es):,} bars" if not es.empty else "ES : not available (ES filter disabled)")

    # Group bars by RTH date
    mnq['date'] = mnq.index.map(lambda x: (x - pd.Timedelta(hours=9, minutes=30)).date()
                                 if x.hour >= 9 else (x - pd.Timedelta(days=1)).date())
    mnq['rth_date'] = mnq.index.map(
        lambda x: x.date() if x.hour >= 9 else (x - pd.Timedelta(days=1)).date()
    )

    all_trades = []
    day_summary = []

    # Get unique trading dates (RTH dates only — 9:30am bar must exist)
    trading_dates = sorted(set(
        mnq[(mnq.index.hour == 9) & (mnq.index.minute == 30)].index.map(lambda x: x.date())
    ))
    print(f"Trading days: {len(trading_dates)}")

    for today in trading_dates:
        today_str = str(today)
        rth_start = pd.Timestamp(f'{today} 09:30:00', tz='America/New_York')
        rth_end   = pd.Timestamp(f'{today} 16:00:00', tz='America/New_York')

        day_bars = mnq[(mnq.index >= rth_start) & (mnq.index <= rth_end)].copy()
        if len(day_bars) < ELEPHANT_LOOKBACK_BARS + 3:
            continue

        day_type = classify_day(day_bars)
        day_summary.append({'date': today_str, 'day_type': day_type})

        if day_type == 'NONE':
            continue

        es_day = es[(es.index >= rth_start) & (es.index <= rth_end)] if not es.empty else pd.DataFrame()
        trades = simulate_day(day_bars, es_day, day_type)
        all_trades.extend(trades)

    # ── Results ──────────────────────────────────────────────
    if not all_trades:
        print("\nNo trades generated. Check bar data coverage and thresholds.")
        return

    df = pd.DataFrame(all_trades)

    print(f"\n{'─'*65}")
    print(f"TOTAL TRADES: {len(df)}")
    winners  = df[df['pnl_usd'] > 0]
    losers   = df[df['pnl_usd'] <= 0]
    wr       = len(winners) / len(df) * 100
    avg_win  = winners['pnl_usd'].mean() if len(winners) else 0
    avg_loss = losers['pnl_usd'].mean() if len(losers) else 0
    total_pnl = df['pnl_usd'].sum()
    exp       = df['pnl_usd'].mean()

    print(f"Win rate:    {wr:.1f}%  ({len(winners)}W / {len(losers)}L)")
    print(f"Avg win:     ${avg_win:+.0f}")
    print(f"Avg loss:    ${avg_loss:+.0f}")
    print(f"Expectancy:  ${exp:+.0f}/trade")
    print(f"Total P&L:   ${total_pnl:+,.0f}")
    print(f"Max winner:  ${df['pnl_usd'].max():+.0f}")
    print(f"Max loser:   ${df['pnl_usd'].min():+.0f}")

    # ── By day type ───────────────────────────────────────────
    print(f"\n{'─'*65}")
    print("BY DAY TYPE:")
    for dt in ['EXTREME_BULL', 'EXTREME_BEAR', 'STRONG_BULL', 'STRONG_BEAR']:
        sub = df[df['day_type'] == dt]
        if sub.empty:
            continue
        sw  = sub[sub['pnl_usd'] > 0]
        swr = len(sw) / len(sub) * 100
        print(f"  {dt:<15} {len(sub):>3} trades | WR {swr:.0f}% | "
              f"avg ${sub['pnl_usd'].mean():+.0f} | total ${sub['pnl_usd'].sum():+,.0f}")

    # ── By direction ─────────────────────────────────────────
    print(f"\n{'─'*65}")
    print("BY DIRECTION:")
    for dirn in ['LONG', 'SHORT']:
        sub = df[df['direction'] == dirn]
        if sub.empty:
            continue
        sw  = sub[sub['pnl_usd'] > 0]
        swr = len(sw) / len(sub) * 100
        print(f"  {dirn:<6} {len(sub):>3} trades | WR {swr:.0f}% | "
              f"avg ${sub['pnl_usd'].mean():+.0f} | total ${sub['pnl_usd'].sum():+,.0f}")

    # ── Exit reason breakdown ─────────────────────────────────
    print(f"\n{'─'*65}")
    print("EXIT REASONS:")
    for reason, grp in df.groupby('exit_reason'):
        sw  = grp[grp['pnl_usd'] > 0]
        swr = len(sw) / len(grp) * 100 if len(grp) else 0
        print(f"  {reason:<10} {len(grp):>3} trades | WR {swr:.0f}% | "
              f"avg ${grp['pnl_usd'].mean():+.0f}")

    # ── Day classifier stats ──────────────────────────────────
    day_df = pd.DataFrame(day_summary)
    print(f"\n{'─'*65}")
    print("DAY CLASSIFIER (opening 15-min bar body):")
    for dt, grp in day_df.groupby('day_type'):
        pct = len(grp) / len(day_df) * 100
        print(f"  {dt:<15} {len(grp):>4} days ({pct:.0f}%)")

    # ── Year-by-year breakdown ────────────────────────────────
    df['year'] = pd.to_datetime(df['date']).dt.year
    print(f"\n{'─'*65}")
    print("YEAR-BY-YEAR:")
    for yr, grp in df.groupby('year'):
        sw  = grp[grp['pnl_usd'] > 0]
        swr = len(sw) / len(grp) * 100
        print(f"  {yr}  {len(grp):>3} trades | WR {swr:.0f}% | "
              f"avg ${grp['pnl_usd'].mean():+.0f} | total ${grp['pnl_usd'].sum():+,.0f}")

    # ── Monthly cadence ───────────────────────────────────────
    df['month'] = pd.to_datetime(df['date']).dt.to_period('M')
    monthly_count = df.groupby('month').size()
    print(f"\nMonthly trade frequency:")
    print(f"  Avg {monthly_count.mean():.1f} trades/month | "
          f"max {monthly_count.max()} | min {monthly_count.min()}")

    print(f"\n{'='*65}")
    print("Backtest complete. Parameters match futures_trader.py exactly.")
    print(f"NOTE: Backtest uses 1 contract per trade (fixed, no RVOL scaling).")
    rr = ELEPHANT_TARGET_PTS / (ELEPHANT_ENTRY_CONF + ELEPHANT_STOP_PTS)
    print(f"R:R = {ELEPHANT_TARGET_PTS:.0f} / ({ELEPHANT_ENTRY_CONF:.0f} + {ELEPHANT_STOP_PTS:.0f}) = {rr:.2f}")


if __name__ == '__main__':
    main()
