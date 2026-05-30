#!/usr/bin/env python3
"""
backtest_dynamic_exits.py
Evaluate 4 dynamic exit options on actual May 2026 trades (intraday replay)
and 2-year historical daily proxy backtest.

Options:
  A - Gap2 intraday: max_gain<1.5% AND pnl<-0.5% AND mins>=60 → exit
  B - P&L Protection floor: portfolio drops $80 from peak → cut underperformers
  C - Correlation exit: 2+ positions simultaneously losing >0.3% after 45 min → cut weakest
  D - All combined (A+B+C)
"""

import sqlite3, yfinance as yf, pandas as pd, numpy as np
from datetime import datetime, date, timedelta
import warnings; warnings.filterwarnings('ignore')

DB = 'trades.db'

# ── Parameters ─────────────────────────────────────────────────────────────────
GAP2_MAX_PEAK    = 1.5    # % — if peak gain never exceeded this
GAP2_LOSS_PCT    = 0.5    # % — currently losing this much
GAP2_MINS        = 60     # minutes before Gap2 check applies

PROTECT_GAP      = 80     # $ — drop from intraday P&L peak triggers cut
PROTECT_MIN_LOSS = 0.3    # % — position must be at least this negative to be cut
PROTECT_MIN_PEAK = 100    # $ — only activate protection if today's peak was above this

CORR_N_LOSERS    = 2      # positions losing simultaneously
CORR_LOSS_PCT    = 0.3    # % each must be losing
CORR_MINS        = 45     # min held before correlation check
CORR_MAX_PEAK    = 1.5    # % — position's peak must be below this

# ── Data Loading ───────────────────────────────────────────────────────────────

def load_may_trades():
    conn = sqlite3.connect(DB)
    df = pd.read_sql("""
        SELECT id, symbol, side, entry_date, entry_time, exit_time,
               CAST(entry_price AS FLOAT) as entry_px,
               CAST(exit_price AS FLOAT) as exit_px,
               CAST(shares AS INT) as qty,
               CAST(pnl AS FLOAT) as actual_pnl,
               CAST(pnl_pct AS FLOAT) as actual_pnl_pct,
               max_gain_pct, exit_reason
        FROM trades WHERE entry_date >= '2026-05-01' AND exit_time IS NOT NULL
        ORDER BY entry_date, entry_time
    """, conn)
    conn.close()
    return df

def pull_intraday(symbol, trade_date):
    """Pull 5-min RTH data for symbol on trade_date"""
    try:
        t = yf.Ticker(symbol)
        end = trade_date + timedelta(days=1)
        df = t.history(start=trade_date, end=end, interval='5m', auto_adjust=True)
        if df.empty:
            return None
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize('America/New_York')
        else:
            df.index = df.index.tz_convert('America/New_York')
        df = df.between_time('09:30', '15:55')
        return df
    except Exception as e:
        return None

def parse_dt(date_str, time_str, tz='America/New_York'):
    """Parse entry/exit datetime with timezone"""
    raw = pd.Timestamp(f"{date_str} {time_str[:8]}")
    return raw.tz_localize(tz)

def get_price_at(bars, dt):
    """Get close price at or just before dt"""
    if bars is None or bars.empty:
        return None
    matching = bars[bars.index <= dt]
    if matching.empty:
        return bars.iloc[0]['Close']
    return float(matching.iloc[-1]['Close'])

# ── Per-Trade Gap2 Simulation ──────────────────────────────────────────────────

def sim_gap2_trade(trade, bars):
    """
    Replay a single trade with Gap2 exit logic.
    Returns dict with gap2_pnl, gap2_fired, gap2_exit_time.
    """
    entry_px = float(trade['entry_px'])
    qty      = int(trade['qty']) if trade['qty'] else 0
    side     = trade['side']
    actual   = float(trade['actual_pnl'])

    result = {'actual_pnl': actual, 'gap2_pnl': actual,
              'gap2_fired': False, 'gap2_exit_time': None, 'gap2_exit_px': None}

    if bars is None or qty == 0 or bars.empty:
        return result

    try:
        entry_dt = parse_dt(trade['entry_date'], str(trade['entry_time']))
    except:
        return result

    session_extreme = entry_px
    future = bars[bars.index >= entry_dt]
    if future.empty:
        return result

    for bar_time, bar in future.iterrows():
        mins = (bar_time - entry_dt).total_seconds() / 60
        px   = float(bar['Close'])

        if side == 'LONG':
            session_extreme = max(session_extreme, float(bar['High']))
            max_gain = (session_extreme - entry_px) / entry_px * 100
            cur_loss = (entry_px - px) / entry_px * 100  # positive = losing
        else:
            session_extreme = min(session_extreme, float(bar['Low']))
            max_gain = (entry_px - session_extreme) / entry_px * 100
            cur_loss = (px - entry_px) / entry_px * 100

        if (not result['gap2_fired'] and mins >= GAP2_MINS
                and max_gain < GAP2_MAX_PEAK and cur_loss > GAP2_LOSS_PCT):
            if side == 'LONG':
                new_pnl = round((px - entry_px) * qty, 2)
            else:
                new_pnl = round((entry_px - px) * qty, 2)
            result.update({'gap2_fired': True, 'gap2_exit_time': bar_time,
                           'gap2_exit_px': round(px, 2), 'gap2_pnl': new_pnl})
            break

    return result

# ── Day-Level Portfolio Simulation ────────────────────────────────────────────

def sim_day_portfolio(date_str, day_trades, day_intraday):
    """
    Simulate a single trading day with Options B (protection) and C (correlation).
    Walk 5-min timeline, track portfolio P&L, fire rules as needed.
    Returns: {trade_id: {actual_pnl, opt_b_pnl, opt_c_pnl, opt_b_fired, opt_c_fired}}
    """
    trade_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    tz = 'America/New_York'

    # Build state for each trade
    states = []
    for _, tr in day_trades.iterrows():
        try:
            entry_dt = parse_dt(date_str, str(tr['entry_time']), tz)
        except:
            continue

        # Exit time — handle overnight (exit date may differ)
        exit_date_str = date_str
        exit_time_str = str(tr['exit_time'])[:8]
        # If exit_time looks like HH:MM:SS and hour < 9, it's next day
        try:
            exit_hour = int(exit_time_str[:2])
            if exit_hour < 9:
                exit_dt = parse_dt(
                    (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d'),
                    exit_time_str, tz)
            else:
                exit_dt = parse_dt(date_str, exit_time_str, tz)
        except:
            exit_dt = parse_dt(date_str, '15:52:00', tz)

        bars = day_intraday.get(tr['symbol'])
        states.append({
            'id':        tr['id'],
            'symbol':    tr['symbol'],
            'side':      tr['side'],
            'entry_px':  float(tr['entry_px']),
            'qty':       int(tr['qty']) if tr['qty'] else 0,
            'actual_pnl': float(tr['actual_pnl']),
            'actual_exit': exit_dt,
            'entry_dt':  entry_dt,
            'bars':      bars,
            'sess_ext':  float(tr['entry_px']),
            'max_gain':  0.0,
            # Option B state
            'opt_b_exit_dt':  None,
            'opt_b_exit_px':  None,
            'opt_b_pnl':      float(tr['actual_pnl']),
            'opt_b_fired':    False,
            # Option C state
            'opt_c_exit_dt':  None,
            'opt_c_exit_px':  None,
            'opt_c_pnl':      float(tr['actual_pnl']),
            'opt_c_fired':    False,
        })

    if not states:
        return {}

    # 5-min timeline: 10:00 to 15:52
    start_ts = pd.Timestamp(f"{date_str} 10:00:00", tz=tz)
    end_ts   = pd.Timestamp(f"{date_str} 15:52:00", tz=tz)
    timeline  = pd.date_range(start_ts, end_ts, freq='5min')

    peak_b = 0.0  # peak portfolio P&L for Option B
    corr_consec = 0  # consecutive scans with 2+ losers (for C)

    for t in timeline:
        # Compute portfolio state for B: realized + unrealized
        real_b = 0.0
        unreal_b = 0.0
        real_c = 0.0
        unreal_c = 0.0
        losing_c = []  # (state, cur_px, cur_loss_pct) for correlation check

        for s in states:
            if t < s['entry_dt']:
                continue  # not entered yet

            px = get_price_at(s['bars'], t)
            if px is None:
                px = s['entry_px']
            px = float(px)

            mins = (t - s['entry_dt']).total_seconds() / 60

            # Update session extreme
            if s['side'] == 'LONG':
                hi = get_price_at(s['bars'], t)  # close approx
                if hi and hi > s['sess_ext']:
                    s['sess_ext'] = hi
                s['max_gain'] = (s['sess_ext'] - s['entry_px']) / s['entry_px'] * 100
                cur_pnl_pct = (px - s['entry_px']) / s['entry_px'] * 100
                unreal_px = (px - s['entry_px']) * s['qty']
            else:
                if px < s['sess_ext']:
                    s['sess_ext'] = px
                s['max_gain'] = (s['entry_px'] - s['sess_ext']) / s['entry_px'] * 100
                cur_pnl_pct = (s['entry_px'] - px) / s['entry_px'] * 100
                unreal_px = (s['entry_px'] - px) * s['qty']

            # Option B: check if already exited by protection
            b_exited = (s['opt_b_exit_dt'] is not None and t >= s['opt_b_exit_dt']) or \
                       (s['opt_b_exit_dt'] is None and t >= s['actual_exit'])
            if b_exited:
                real_b += s['opt_b_pnl']
            else:
                unreal_b += unreal_px

            # Option C: check if already exited
            c_exited = (s['opt_c_exit_dt'] is not None and t >= s['opt_c_exit_dt']) or \
                       (s['opt_c_exit_dt'] is None and t >= s['actual_exit'])
            if c_exited:
                real_c += s['opt_c_pnl']
            else:
                unreal_c += unreal_px
                # Track for correlation check (C)
                if (mins >= CORR_MINS and cur_pnl_pct < -CORR_LOSS_PCT
                        and s['max_gain'] < CORR_MAX_PEAK
                        and not s['opt_c_fired']):
                    losing_c.append((s, px, cur_pnl_pct))

        portfolio_b = real_b + unreal_b

        # Update peak for Option B
        if portfolio_b > peak_b:
            peak_b = portfolio_b

        # Option B: fire protection if portfolio dropped $PROTECT_GAP from peak
        if (peak_b >= PROTECT_MIN_PEAK and
                (peak_b - portfolio_b) >= PROTECT_GAP):
            for s in states:
                if s['opt_b_fired'] or s['opt_b_exit_dt'] is not None:
                    continue
                if t < s['entry_dt'] or t >= s['actual_exit']:
                    continue
                px = get_price_at(s['bars'], t) or s['entry_px']
                px = float(px)
                if s['side'] == 'LONG':
                    cur_pnl_pct = (px - s['entry_px']) / s['entry_px'] * 100
                    new_pnl = (px - s['entry_px']) * s['qty']
                else:
                    cur_pnl_pct = (s['entry_px'] - px) / s['entry_px'] * 100
                    new_pnl = (s['entry_px'] - px) * s['qty']
                if cur_pnl_pct < -PROTECT_MIN_LOSS and s['max_gain'] < GAP2_MAX_PEAK:
                    s['opt_b_exit_dt'] = t
                    s['opt_b_exit_px'] = round(px, 2)
                    s['opt_b_pnl']     = round(new_pnl, 2)
                    s['opt_b_fired']   = True

        # Option C: fire if 2+ correlated losers
        if len(losing_c) >= CORR_N_LOSERS:
            # Cut the weakest one (most negative P&L)
            losing_c.sort(key=lambda x: x[2])  # most negative first
            worst = losing_c[0][0]
            if not worst['opt_c_fired']:
                px = float(losing_c[0][1])
                if worst['side'] == 'LONG':
                    new_pnl = (px - worst['entry_px']) * worst['qty']
                else:
                    new_pnl = (worst['entry_px'] - px) * worst['qty']
                worst['opt_c_exit_dt'] = t
                worst['opt_c_exit_px'] = round(px, 2)
                worst['opt_c_pnl']     = round(new_pnl, 2)
                worst['opt_c_fired']   = True

    # Collect results
    results = {}
    for s in states:
        results[s['id']] = {
            'symbol':      s['symbol'],
            'actual_pnl':  s['actual_pnl'],
            'opt_b_pnl':   s['opt_b_pnl'],
            'opt_b_fired': s['opt_b_fired'],
            'opt_c_pnl':   s['opt_c_pnl'],
            'opt_c_fired': s['opt_c_fired'],
        }
    return results

# ── Historical Daily Proxy Backtest ───────────────────────────────────────────

def run_historical_backtest(symbols, years=2):
    """
    2-year daily proxy backtest for Gap2 + Protection on textbook symbols.
    Entry: stock up >3% by midday, above 20MA, not in lunch window.
    Simulates intraday with OHLC daily proxy.
    """
    end_date   = date(2026, 5, 22)
    start_date = end_date - timedelta(days=years * 365 + 30)

    print(f"\nPulling {years}-year daily data for {len(symbols)} symbols...")

    all_results = {'baseline': [], 'gap2': [], 'combo': []}

    for sym in symbols:
        try:
            t = yf.Ticker(sym)
            df = t.history(start=start_date, end=end_date, interval='1d', auto_adjust=True)
            if df.empty or len(df) < 60:
                print(f"  {sym}: insufficient data, skipping")
                continue
            df.index = df.index.tz_localize(None)
            df['MA20'] = df['Close'].rolling(20).mean()
            df['ATR']  = (df['High'] - df['Low']).rolling(14).mean()
        except Exception as e:
            print(f"  {sym}: error {e}")
            continue

        entries = 0
        for i in range(21, len(df)):
            row  = df.iloc[i]
            prev = df.iloc[i-1]
            if pd.isna(row['MA20']) or pd.isna(row['ATR']):
                continue

            # Entry signal: gap up >3% open vs prev close, above MA20
            gap_pct   = (row['Open'] - prev['Close']) / prev['Close'] * 100
            above_ma  = row['Open'] > row['MA20']
            atr       = float(row['ATR'])
            entry_px  = float(row['Open'])

            if gap_pct < 3.0 or not above_ma or atr <= 0:
                continue

            # Position sizing: $1,600 / price, capped at $150 risk
            stop_dist = atr * 2.0
            qty_size  = int(min(1600 / entry_px, 150 / max(stop_dist, 0.01)))
            if qty_size <= 0:
                continue

            entries += 1

            # Baseline exit: use actual OHLC
            high    = float(row['High'])
            low     = float(row['Low'])
            close   = float(row['Close'])
            stop_px = round(entry_px * (1 - 0.05), 2)  # 5% hard stop

            # Baseline: hit stop or close at EOD
            if low <= stop_px:
                base_exit = stop_px
            else:
                # Apply PCT trail proxy: if high > entry*1.015, trail 0.5% below high
                if high >= entry_px * 1.015:
                    pct_trail = round(high * 0.995, 2)
                    if pct_trail > stop_px:
                        stop_px = pct_trail
                if close < stop_px:
                    base_exit = stop_px
                else:
                    base_exit = close

            base_pnl = round((base_exit - entry_px) * qty_size, 2)

            # Gap2 exit proxy: if high < entry*1.015 and low hits entry*0.995
            gap2_exit  = base_exit
            gap2_fired = False
            if high < entry_px * (1 + GAP2_MAX_PEAK/100):
                if low <= entry_px * (1 - GAP2_LOSS_PCT/100):
                    # Cut at -GAP2_LOSS_PCT (proxy for "at the 60-min mark it was losing")
                    gap2_exit  = round(entry_px * (1 - GAP2_LOSS_PCT/100 - 0.002), 2)
                    gap2_fired = True

            gap2_pnl = round((gap2_exit - entry_px) * qty_size, 2)

            # Combo: Gap2 + Protection (protection harder to proxy in daily, use Gap2)
            combo_pnl = gap2_pnl

            all_results['baseline'].append({'pnl': base_pnl, 'symbol': sym,
                                            'date': df.index[i].date()})
            all_results['gap2'].append({'pnl': gap2_pnl, 'gap2_fired': gap2_fired,
                                        'symbol': sym, 'date': df.index[i].date()})
            all_results['combo'].append({'pnl': combo_pnl, 'symbol': sym,
                                         'date': df.index[i].date()})

        print(f"  {sym}: {entries} entries in {years} years")

    return all_results

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("DYNAMIC EXIT OPTIONS — FULL SIMULATION")
    print("=" * 65)

    trades = load_may_trades()
    print(f"Loaded {len(trades)} May trades")

    # ── Pull intraday cache ────────────────────────────────────────────────────
    print("\nPulling 5-min intraday data for all May symbol-days...")
    intraday_cache = {}
    unique_pairs = trades[['entry_date', 'symbol']].drop_duplicates()
    for _, row in unique_pairs.iterrows():
        key = f"{row['entry_date']}_{row['symbol']}"
        if key not in intraday_cache:
            d = datetime.strptime(row['entry_date'], '%Y-%m-%d').date()
            data = pull_intraday(row['symbol'], d)
            intraday_cache[key] = data

    n_ok = sum(1 for v in intraday_cache.values() if v is not None and not v.empty)
    print(f"Data available: {n_ok}/{len(intraday_cache)} symbol-days")

    # ── OPTION A: Gap2 per-trade simulation ────────────────────────────────────
    print("\n" + "=" * 65)
    print(f"OPTION A — Gap2 Intraday (peak<{GAP2_MAX_PEAK}% AND pnl<-{GAP2_LOSS_PCT}% after {GAP2_MINS}min)")
    print("=" * 65)

    gap2_rows = []
    for _, tr in trades.iterrows():
        key  = f"{tr['entry_date']}_{tr['symbol']}"
        bars = intraday_cache.get(key)
        res  = sim_gap2_trade(tr, bars)
        res['symbol'] = tr['symbol']
        res['date']   = tr['entry_date']
        res['side']   = tr['side']
        gap2_rows.append(res)

    gap2_df = pd.DataFrame(gap2_rows)
    fired   = gap2_df[gap2_df['gap2_fired']]
    not_f   = gap2_df[~gap2_df['gap2_fired']]

    print(f"\nGap2 fired on {len(fired)} trades:")
    for _, r in fired.iterrows():
        delta = r['gap2_pnl'] - r['actual_pnl']
        fp    = ' ← FALSE POS (actual was +)' if r['actual_pnl'] > 0 else ''
        t_str = str(r['gap2_exit_time'])[-14:-6] if r['gap2_exit_time'] is not None else '?'
        print(f"  {r['date']} {r['symbol']:6s} {r['side']:5s} | actual={r['actual_pnl']:+7.2f} | "
              f"gap2={r['gap2_pnl']:+7.2f} | Δ={delta:+7.2f} | exit@{t_str}{fp}")

    total_actual = gap2_df['actual_pnl'].sum()
    total_gap2   = gap2_df['gap2_pnl'].sum()
    fp_count     = len(fired[fired['actual_pnl'] > 0])
    print(f"\nFalse positives: {fp_count}/{len(fired)} ({fp_count/max(len(fired),1)*100:.0f}%)")
    print(f"Total actual: ${total_actual:.2f}  →  Gap2: ${total_gap2:.2f}  ({total_gap2-total_actual:+.2f})")

    # ── OPTIONS B & C: Day-level portfolio simulation ──────────────────────────
    print("\n" + "=" * 65)
    print(f"OPTIONS B+C — P&L Protection (${PROTECT_GAP} drop) + Correlation Exit")
    print("=" * 65)

    all_port = {}
    for date_str, day_grp in trades.groupby('entry_date'):
        day_intraday = {sym: intraday_cache.get(f"{date_str}_{sym}")
                        for sym in day_grp['symbol'].unique()}
        day_res = sim_day_portfolio(date_str, day_grp, day_intraday)
        all_port.update(day_res)

    port_df = pd.DataFrame(all_port).T.reset_index(drop=True)
    port_df['actual_pnl'] = pd.to_numeric(port_df['actual_pnl'])
    port_df['opt_b_pnl']  = pd.to_numeric(port_df['opt_b_pnl'])
    port_df['opt_c_pnl']  = pd.to_numeric(port_df['opt_c_pnl'])

    b_fired = port_df[port_df['opt_b_fired'] == True]
    c_fired = port_df[port_df['opt_c_fired'] == True]

    print(f"\nOption B (Protection) fired on {len(b_fired)} trades:")
    for _, r in b_fired.iterrows():
        delta = float(r['opt_b_pnl']) - float(r['actual_pnl'])
        fp    = ' ← FALSE POS' if float(r['actual_pnl']) > 0 else ''
        print(f"  {r['symbol']:6s} | actual={float(r['actual_pnl']):+7.2f} | "
              f"opt_b={float(r['opt_b_pnl']):+7.2f} | Δ={delta:+7.2f}{fp}")

    print(f"\nOption C (Correlation) fired on {len(c_fired)} trades:")
    for _, r in c_fired.iterrows():
        delta = float(r['opt_c_pnl']) - float(r['actual_pnl'])
        fp    = ' ← FALSE POS' if float(r['actual_pnl']) > 0 else ''
        print(f"  {r['symbol']:6s} | actual={float(r['actual_pnl']):+7.2f} | "
              f"opt_c={float(r['opt_c_pnl']):+7.2f} | Δ={delta:+7.2f}{fp}")

    total_b = port_df['opt_b_pnl'].sum()
    total_c = port_df['opt_c_pnl'].sum()

    # ── Combo: all options ─────────────────────────────────────────────────────
    # For each trade, take the best available exit (gap2 or portfolio)
    combo_pnl = 0.0
    for _, tr in trades.iterrows():
        tid       = tr['id']
        actual    = float(tr['actual_pnl'])
        g2        = gap2_df[gap2_df['symbol'] == tr['symbol']]['gap2_pnl'].values
        g2_val    = float(g2[0]) if len(g2) > 0 else actual
        b_val     = float(all_port.get(tid, {}).get('opt_b_pnl', actual))
        c_val     = float(all_port.get(tid, {}).get('opt_c_pnl', actual))
        combo_pnl += max(g2_val, b_val, c_val)  # best protection per trade

    # ── DAY-BY-DAY SUMMARY ─────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print("DAY-BY-DAY SUMMARY")
    print("=" * 65)
    print(f"{'Date':<12}{'Actual':>8}{'Opt-A':>8}{'Opt-B':>8}{'Opt-C':>8}{'Combo':>8}  Best")
    print("-" * 65)

    total_a = total_actual  # gap2 total
    total_a_new = total_gap2

    for date_str, day_grp in trades.groupby('entry_date'):
        day_act = day_grp['actual_pnl'].sum()

        # Gap2 for this day
        day_g2 = gap2_df[gap2_df['date'] == date_str]['gap2_pnl'].sum()

        # Opt B, C for this day
        day_b_vals = [float(all_port.get(tid, {}).get('opt_b_pnl', pnl))
                      for tid, pnl in zip(day_grp['id'], day_grp['actual_pnl'])]
        day_c_vals = [float(all_port.get(tid, {}).get('opt_c_pnl', pnl))
                      for tid, pnl in zip(day_grp['id'], day_grp['actual_pnl'])]
        day_b  = sum(day_b_vals)
        day_c  = sum(day_c_vals)

        # Combo: per trade best
        day_combo = 0.0
        for tid, pnl in zip(day_grp['id'], day_grp['actual_pnl']):
            g2v = gap2_df[gap2_df['symbol'].isin(
                day_grp[day_grp['id']==tid]['symbol'])]['gap2_pnl'].values
            g2v = float(g2v[0]) if len(g2v) else float(pnl)
            bv  = float(all_port.get(tid, {}).get('opt_b_pnl', pnl))
            cv  = float(all_port.get(tid, {}).get('opt_c_pnl', pnl))
            day_combo += max(g2v, bv, cv)

        best = max(day_act, day_g2, day_b, day_c)
        best_lbl = ('ACT' if best == day_act else
                    'A'   if best == day_g2  else
                    'B'   if best == day_b   else 'C')
        print(f"{date_str:<12}{day_act:>8.2f}{day_g2:>8.2f}{day_b:>8.2f}{day_c:>8.2f}"
              f"{day_combo:>8.2f}  {best_lbl}")

    print("-" * 65)
    print(f"{'MAY TOTAL':<12}{total_actual:>8.2f}{total_gap2:>8.2f}{total_b:>8.2f}"
          f"{total_c:>8.2f}{combo_pnl:>8.2f}")

    # ── FINAL COMPARISON ───────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print("FINAL COMPARISON — MAY 2026")
    print("=" * 65)
    print(f"  Baseline (actual):          ${total_actual:>8.2f}")
    print(f"  Option A — Gap2 only:       ${total_gap2:>8.2f}  ({total_gap2-total_actual:+.2f})")
    print(f"  Option B — P&L Protection:  ${total_b:>8.2f}  ({total_b-total_actual:+.2f})")
    print(f"  Option C — Correlation:     ${total_c:>8.2f}  ({total_c-total_actual:+.2f})")
    print(f"  Option D — All Combined:    ${combo_pnl:>8.2f}  ({combo_pnl-total_actual:+.2f})")

    # ── HISTORICAL BACKTEST ────────────────────────────────────────────────────
    textbook = ['QBTS', 'IONQ', 'RKLB', 'ARM', 'MRVL', 'SMCI',
                'NVDA', 'AMD', 'AXON', 'CLS', 'UUUU', 'CCJ']
    hist = run_historical_backtest(textbook, years=2)

    if hist['baseline']:
        bdf = pd.DataFrame(hist['baseline'])
        gdf = pd.DataFrame(hist['gap2'])
        cdf = pd.DataFrame(hist['combo'])

        bdf['year'] = bdf['date'].apply(lambda d: d.year)
        gdf['year'] = gdf['date'].apply(lambda d: d.year)

        print("\n" + "=" * 65)
        print("HISTORICAL DAILY PROXY — 2-YEAR BACKTEST")
        print("=" * 65)
        print(f"  Symbols: {', '.join(textbook)}")
        print(f"  Total entries: {len(bdf)}")
        print(f"\n  {'Year':<8} {'Baseline':>10} {'Gap2':>10} {'Δ':>8} {'Gap2-fired':>12}")
        for yr in sorted(bdf['year'].unique()):
            ybdf = bdf[bdf['year']==yr]
            ygdf = gdf[gdf['year']==yr]
            fired_yr = ygdf[ygdf['gap2_fired']==True] if 'gap2_fired' in ygdf.columns else pd.DataFrame()
            print(f"  {yr:<8} ${ybdf['pnl'].sum():>9.2f} ${ygdf['pnl'].sum():>9.2f} "
                  f"{ygdf['pnl'].sum()-ybdf['pnl'].sum():>+8.2f}  "
                  f"{len(fired_yr)}/{len(ygdf)} ({len(fired_yr)/max(len(ygdf),1)*100:.0f}%)")

        print(f"\n  2-year total baseline: ${bdf['pnl'].sum():.2f}")
        print(f"  2-year total gap2:     ${gdf['pnl'].sum():.2f}  ({gdf['pnl'].sum()-bdf['pnl'].sum():+.2f})")

        # False positive rate on historical
        fired_h = gdf[gdf['gap2_fired']==True] if 'gap2_fired' in gdf.columns else pd.DataFrame()
        base_fired = bdf.loc[fired_h.index] if len(fired_h) > 0 else pd.DataFrame()
        if len(fired_h) > 0 and len(base_fired) > 0:
            fp_h = sum(1 for bpnl in base_fired['pnl'] if bpnl > 0)
            print(f"  Gap2 false positives (would-be winners cut): "
                  f"{fp_h}/{len(fired_h)} ({fp_h/len(fired_h)*100:.0f}%)")

    print("\n✓ Simulation complete")

if __name__ == '__main__':
    main()
