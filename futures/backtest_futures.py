"""
futures/backtest_futures.py — MNQ Strategy Backtest

Strategy: ORB + VWAP reclaim + momentum (same edge as equity)
Data:      futures_bars_5m from market_data.db (seeded by collect_bars.py)
Output:    trade log, WR, EV/trade, Sharpe, by-year, TC eval simulation

MNQ economics:
  Tick size  : 0.25 points
  Tick value : $0.50/tick/contract
  Point value: $2.00/contract
  Commission : $1.24/round-turn

Usage:
  venv/bin/python futures/backtest_futures.py
  venv/bin/python futures/backtest_futures.py --start 2025-01-01
  venv/bin/python futures/backtest_futures.py --start 2025-01-01 --mode XFA
"""

import sys
import os
import argparse
import math
from datetime import datetime, date, time, timedelta
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))
from futures.collect_bars import load_bars, filter_ny_session
from futures.prop_rules import PropRulesSimulator, COMMISSION, POINT_VALUE, TICK_SIZE

ET = pytz.timezone('America/New_York')

# ── Strategy constants ─────────────────────────────────────────────────────────
ORB_WINDOW_MIN    = 30       # opening range: first 30 minutes (9:30-10:00 ET)
MIN_ORB_RANGE_PTS = 5.0      # minimum ORB range to trade (filter choppy opens)
ATR_PERIOD        = 14       # periods for ATR
ATR_STOP_MULT     = 1.5      # stop = entry ± ATR × mult
MIN_RR            = 2.0      # minimum reward:risk ratio
TARGET_MULT       = 3.0      # target = entry ± ATR × TARGET_MULT
PCT_TRAIL_ACTIVATE = 0.015   # +1.5% from entry activates PCT trail
PCT_TRAIL_GAP      = 0.005   # trail 0.5% from session high
MAX_HOLD_BARS     = 36       # 3 hours (36 × 5-min bars) — no-move exit
EOD_CLOSE_TIME    = time(15, 10)  # 3:10 PM ET — TopStepX hard deadline
NO_ENTRY_AFTER    = time(14, 30)  # 2:30 PM ET — no new entries
LUNCH_AVOID_START = time(11, 30)  # 11:30 AM ET
LUNCH_AVOID_END   = time(12, 30)  # 12:30 PM ET (tighter than equity)

# Regime thresholds
TREND_EMA_FAST = 8
TREND_EMA_SLOW = 21


# ── Indicators ─────────────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # ATR
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low']  - df['close'].shift(1)),
        )
    )
    df['atr'] = df['tr'].rolling(ATR_PERIOD).mean()

    # VWAP (resets each session — computed per day in simulate_day)
    # EMA trend filter
    df['ema_fast'] = df['close'].ewm(span=TREND_EMA_FAST, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=TREND_EMA_SLOW, adjust=False).mean()
    return df


def compute_vwap(day_df: pd.DataFrame) -> pd.Series:
    """VWAP anchored to the session open."""
    tp  = (day_df['high'] + day_df['low'] + day_df['close']) / 3
    vol = day_df['volume'].replace(0, 1)  # avoid div-by-zero on thin bars
    cumvol  = vol.cumsum()
    cumtpvol = (tp * vol).cumsum()
    return cumtpvol / cumvol


# ── Single-day simulation ──────────────────────────────────────────────────────

def simulate_day(day_df: pd.DataFrame, atr_at_open: float,
                 prop: PropRulesSimulator) -> list[dict]:
    """
    Simulate one trading day. Returns list of closed trade dicts.
    day_df: NY session bars for one day (already filtered 9:30–3:10 PM ET).
    atr_at_open: ATR value at start of this day (from prior close).
    """
    if len(day_df) < 8:   # need at least 8 bars (40 min) to trade
        return []

    trades = []
    day_df = day_df.copy()
    day_df['vwap'] = compute_vwap(day_df)

    # ── Opening Range (first 30 min = 6 bars of 5-min) ──
    orb_bars = day_df[day_df.index.time < time(10, 0)]
    if len(orb_bars) < 4:
        return []

    orb_high = orb_bars['high'].max()
    orb_low  = orb_bars['low'].min()
    orb_range = orb_high - orb_low

    if orb_range < MIN_ORB_RANGE_PTS:
        return []   # choppy open — skip day

    atr = atr_at_open if atr_at_open and atr_at_open > 0 else orb_range

    # Track open position
    position = None
    session_high = day_df['high'].iloc[0]
    session_low  = day_df['low'].iloc[0]
    bars_held    = 0

    for i, (ts, bar) in enumerate(day_df.iterrows()):
        t = ts.time()
        session_high = max(session_high, bar['high'])
        session_low  = min(session_low,  bar['low'])

        # ── Exit existing position ──────────────────────
        if position is not None:
            bars_held += 1
            entry     = position['entry']
            stop      = position['stop']
            target    = position['target']
            direction = position['direction']
            pnl_pts   = (bar['close'] - entry) if direction == 'LONG' else (entry - bar['close'])

            # Track trail
            if direction == 'LONG':
                position['peak'] = max(position['peak'], bar['high'])
            else:
                position['peak'] = min(position['peak'], bar['low'])

            # PCT trail: activates at +1.5%
            pct_gain = pnl_pts / entry
            if pct_gain >= PCT_TRAIL_ACTIVATE:
                if direction == 'LONG':
                    trail_stop = position['peak'] * (1 - PCT_TRAIL_GAP)
                    position['stop'] = max(position['stop'], trail_stop)
                else:
                    trail_stop = position['peak'] * (1 + PCT_TRAIL_GAP)
                    position['stop'] = min(position['stop'], trail_stop)

            exit_price  = None
            exit_reason = None

            # Hard stop
            if direction == 'LONG' and bar['low'] <= position['stop']:
                exit_price  = position['stop']
                exit_reason = 'stop'
            elif direction == 'SHORT' and bar['high'] >= position['stop']:
                exit_price  = position['stop']
                exit_reason = 'stop'
            # Target
            elif direction == 'LONG' and bar['high'] >= target:
                exit_price  = target
                exit_reason = 'target'
            elif direction == 'SHORT' and bar['low'] <= target:
                exit_price  = target
                exit_reason = 'target'
            # VWAP cross (if profitable > 0.5%)
            elif pct_gain > 0.005:
                if direction == 'LONG' and bar['close'] < bar['vwap']:
                    exit_price  = bar['close']
                    exit_reason = 'vwap_cross'
                elif direction == 'SHORT' and bar['close'] > bar['vwap']:
                    exit_price  = bar['close']
                    exit_reason = 'vwap_cross'
            # No-move exit (3 hours flat)
            if exit_price is None and bars_held >= MAX_HOLD_BARS:
                if abs(pnl_pts) < atr * 0.5:  # barely moved
                    exit_price  = bar['close']
                    exit_reason = 'no_move'
            # EOD close
            if exit_price is None and t >= EOD_CLOSE_TIME:
                exit_price  = bar['close']
                exit_reason = 'eod'

            if exit_price is not None:
                raw_pnl  = (exit_price - entry) * POINT_VALUE if direction == 'LONG' \
                           else (entry - exit_price) * POINT_VALUE
                net_pnl  = prop.record_trade(raw_pnl)   # commission deducted inside
                trades.append({
                    'date':        ts.date().isoformat(),
                    'entry_time':  position['entry_time'].strftime('%H:%M'),
                    'exit_time':   t.strftime('%H:%M'),
                    'direction':   direction,
                    'entry':       entry,
                    'exit':        exit_price,
                    'stop_init':   position['stop_init'],
                    'target':      target,
                    'atr':         atr,
                    'bars_held':   bars_held,
                    'raw_pnl':     round(raw_pnl, 2),
                    'net_pnl':     round(net_pnl, 2),
                    'exit_reason': exit_reason,
                    'status':      'WIN' if net_pnl > 0 else 'LOSS',
                })
                position = None
                bars_held = 0
            continue  # done with exit logic for this bar

        # ── Entry logic ─────────────────────────────────
        if t < time(10, 0):  # still in ORB window
            continue
        if t >= NO_ENTRY_AFTER:
            continue
        if time(LUNCH_AVOID_START.hour, LUNCH_AVOID_START.minute) <= t \
                <= time(LUNCH_AVOID_END.hour, LUNCH_AVOID_END.minute):
            continue

        ok, reason = prop.check_can_trade()
        if not ok:
            continue

        vwap = bar['vwap']
        close = bar['close']

        # ── LONG setup ──────────────────────────────────
        #  1. ORB breakout above orb_high
        #  2. Close above VWAP (bullish bias)
        #  3. EMA fast > slow (uptrend)
        if (close > orb_high
                and bar['ema_fast'] > bar['ema_slow']
                and close > vwap):

            entry  = close
            stop   = entry - atr * ATR_STOP_MULT
            risk   = entry - stop
            if risk <= 0:
                continue
            target = entry + risk * TARGET_MULT
            if (target - entry) / entry < MIN_RR * risk / entry:
                continue

            position = {
                'direction':  'LONG',
                'entry':      entry,
                'entry_time': t,
                'stop':       stop,
                'stop_init':  stop,
                'target':     target,
                'peak':       entry,
            }

        # ── SHORT setup ─────────────────────────────────
        #  1. ORB breakdown below orb_low
        #  2. Close below VWAP (bearish bias)
        #  3. EMA fast < slow (downtrend)
        elif (close < orb_low
                and bar['ema_fast'] < bar['ema_slow']
                and close < vwap):

            entry  = close
            stop   = entry + atr * ATR_STOP_MULT
            risk   = stop - entry
            if risk <= 0:
                continue
            target = entry - risk * TARGET_MULT
            if (entry - target) / entry < MIN_RR * risk / entry:
                continue

            position = {
                'direction':  'SHORT',
                'entry':      entry,
                'entry_time': t,
                'stop':       stop,
                'stop_init':  stop,
                'target':     target,
                'peak':       entry,
            }

    # Force-close at EOD if position still open
    if position is not None:
        last  = day_df.iloc[-1]
        entry = position['entry']
        dir_  = position['direction']
        raw   = (last['close'] - entry) * POINT_VALUE if dir_ == 'LONG' \
                else (entry - last['close']) * POINT_VALUE
        net   = prop.record_trade(raw)
        trades.append({
            'date':        day_df.index[-1].date().isoformat(),
            'entry_time':  position['entry_time'].strftime('%H:%M'),
            'exit_time':   '15:10',
            'direction':   dir_,
            'entry':       entry,
            'exit':        last['close'],
            'stop_init':   position['stop_init'],
            'target':      position['target'],
            'atr':         atr,
            'bars_held':   bars_held,
            'raw_pnl':     round(raw, 2),
            'net_pnl':     round(net, 2),
            'exit_reason': 'eod_force',
            'status':      'WIN' if net > 0 else 'LOSS',
        })

    return trades


# ── Full backtest ──────────────────────────────────────────────────────────────

def run_backtest(start: str | None = None, end: str | None = None,
                 mode: str = 'TC') -> list[dict]:
    print(f'Loading MNQ 5-min bars (start={start or "all"}, end={end or "today"})...')
    df = load_bars(start=start, end=end, table='futures_bars_5m')
    if df.empty:
        print('❌ No data. Run: venv/bin/python futures/collect_bars.py --bootstrap')
        return []

    df = add_indicators(df)
    ny_df = filter_ny_session(df)
    print(f'  {len(df):,} total bars → {len(ny_df):,} NY session bars')

    prop   = PropRulesSimulator(mode=mode)
    trades = []

    days = sorted(ny_df.index.normalize().unique())
    print(f'  Simulating {len(days)} trading days...\n')

    for day_ts in days:
        day_str  = day_ts.date().isoformat()
        day_bars = ny_df[ny_df.index.date == day_ts.date()]

        # ATR at open: use last bar from previous session
        prior_bars = df[df.index.date < day_ts.date()].tail(ATR_PERIOD + 5)
        atr_at_open = float(prior_bars['atr'].iloc[-1]) if not prior_bars.empty and 'atr' in prior_bars else 15.0

        prop.new_day(day_str)
        day_trades = simulate_day(day_bars, atr_at_open, prop)
        trades.extend(day_trades)

        # Check TC pass
        if prop.tc_passed():
            print(f'  ✅ TC PASS on {day_str} — total profit ${prop.total_profit:,.0f}')

    return trades


# ── Report ─────────────────────────────────────────────────────────────────────

def print_report(trades: list[dict], mode: str = 'TC'):
    if not trades:
        print('No trades to report.')
        return

    wins   = [t for t in trades if t['status'] == 'WIN']
    losses = [t for t in trades if t['status'] == 'LOSS']
    total  = len(trades)
    wr     = len(wins) / total * 100 if total else 0

    pnls     = [t['net_pnl'] for t in trades]
    total_pnl = sum(pnls)
    avg_pnl  = total_pnl / total if total else 0
    avg_win  = sum(t['net_pnl'] for t in wins)  / len(wins)  if wins   else 0
    avg_loss = sum(t['net_pnl'] for t in losses) / len(losses) if losses else 0

    # Sharpe (daily)
    daily_pnl = defaultdict(float)
    for t in trades:
        daily_pnl[t['date']] += t['net_pnl']
    day_vals = list(daily_pnl.values())
    sharpe   = (np.mean(day_vals) / np.std(day_vals) * math.sqrt(252)
                if len(day_vals) > 1 and np.std(day_vals) > 0 else 0.0)

    # Max drawdown
    equity = np.cumsum(pnls)
    peak   = np.maximum.accumulate(equity)
    dd     = equity - peak
    max_dd = float(dd.min()) if len(dd) else 0.0

    # By year
    by_year = defaultdict(lambda: {'n': 0, 'wins': 0, 'pnl': 0.0})
    for t in trades:
        y = t['date'][:4]
        by_year[y]['n']    += 1
        by_year[y]['wins'] += (1 if t['status'] == 'WIN' else 0)
        by_year[y]['pnl']  += t['net_pnl']

    # Exit reasons
    reasons = defaultdict(int)
    for t in trades:
        reasons[t['exit_reason']] += 1

    print('=' * 60)
    print(f'  MNQ FUTURES BACKTEST — {mode} mode')
    print('=' * 60)
    print(f'  Trades:        {total}')
    print(f'  Win rate:      {wr:.1f}%  ({len(wins)}W / {len(losses)}L)')
    print(f'  Total P&L:     ${total_pnl:,.2f}')
    print(f'  Avg P&L/trade: ${avg_pnl:,.2f}')
    print(f'  Avg win:       ${avg_win:,.2f}')
    print(f'  Avg loss:      ${avg_loss:,.2f}')
    print(f'  Sharpe (ann):  {sharpe:.2f}')
    print(f'  Max drawdown:  ${max_dd:,.2f}')
    print()
    print('  By year:')
    for y in sorted(by_year):
        d = by_year[y]
        yr_wr = d['wins'] / d['n'] * 100 if d['n'] else 0
        print(f'    {y}  {d["n"]:>4} trades  {yr_wr:>5.1f}% WR  ${d["pnl"]:>9,.2f}')
    print()
    print('  Exit reasons:')
    for r, n in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f'    {r:<20} {n:>4}  ({n/total*100:.0f}%)')
    print()


# ── TC eval simulation ─────────────────────────────────────────────────────────

def simulate_tc_eval(trades: list[dict]):
    """Show how many days it would take to pass TC and how many attempts."""
    from futures.prop_rules import TC_PROFIT_TARGET, TC_MLL_AMOUNT, TC_DLL_AMOUNT

    print('=== TC EVAL SIMULATION ($50K, target $3K, MLL $2K, DLL $1K) ===')

    # Simulate 30-day windows
    daily_pnl = defaultdict(float)
    for t in trades:
        daily_pnl[t['date']] += t['net_pnl']

    days     = sorted(daily_pnl.keys())
    attempts = 0
    passes   = 0
    window   = 30

    i = 0
    while i < len(days):
        chunk      = days[i:i + window]
        cum_profit = 0.0
        hwm        = 50_000.0
        passed     = False
        blown      = False
        attempts  += 1

        for d in chunk:
            pnl        = daily_pnl[d]
            cum_profit = round(cum_profit + pnl, 2)
            balance    = round(50_000 + cum_profit, 2)
            hwm        = max(hwm, balance)

            # DLL check
            if pnl < -TC_DLL_AMOUNT:
                blown = True
                break
            # MLL check
            if balance < hwm - TC_MLL_AMOUNT:
                blown = True
                break
            # Pass check
            if cum_profit >= TC_PROFIT_TARGET:
                passed = True
                break

        outcome = '✅ PASS' if passed else ('💥 BLOWN' if blown else '⏱ TIMEOUT')
        print(f'  Attempt {attempts}: days {i+1}–{i+len(chunk)}  '
              f'P&L ${cum_profit:+,.0f}  {outcome}')
        if passed:
            passes += 1

        i += len(chunk)

    print()
    print(f'  Total attempts: {attempts}  |  Passes: {passes}  '
          f'|  Pass rate: {passes/attempts*100:.0f}%')
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='MNQ futures backtest')
    parser.add_argument('--start', default=None, help='Start date YYYY-MM-DD')
    parser.add_argument('--end',   default=None, help='End date YYYY-MM-DD')
    parser.add_argument('--mode',  default='TC', choices=['TC', 'XFA'],
                        help='Prop mode: TC (eval) or XFA (funded)')
    parser.add_argument('--tc-sim', action='store_true',
                        help='Run TC eval window simulation')
    args = parser.parse_args()

    trades = run_backtest(start=args.start, end=args.end, mode=args.mode)
    print_report(trades, mode=args.mode)

    if args.tc_sim and trades:
        simulate_tc_eval(trades)

    # Prop rules final state
    prop = PropRulesSimulator(mode=args.mode)
    for t in trades:
        prop.new_day(t['date'])
        # State already recorded during run_backtest — just print summary
    print()
    print(f'  Prop rules status after simulation:')
    print(f'    TC passed:    {"✅ YES" if any(sum(t["net_pnl"] for t in trades) >= 3000 for _ in [1]) else "❌ no"}')
    print(f'    Total trades: {len(trades)}')
    print(f'    Net P&L:      ${sum(t["net_pnl"] for t in trades):,.2f}')
