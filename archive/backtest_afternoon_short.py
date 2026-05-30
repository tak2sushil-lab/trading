#!/usr/bin/env python3
"""
backtest_afternoon_short.py
60-day deep analysis on two questions:
  1. Afternoon gate: when morning is already profitable, should we gate
     afternoon recycled entries tighter?
  2. Short side deep dive: what patterns are killing the bear edge?

Uses all available DB data (April 15 – May 22) + yfinance 5-min intraday.
"""

import sqlite3, yfinance as yf, pandas as pd, numpy as np
from datetime import datetime, date, timedelta
import warnings; warnings.filterwarnings('ignore')

DB = 'trades.db'

# ── Load & enrich trades ───────────────────────────────────────────────────────

def load_all_trades():
    conn = sqlite3.connect(DB)
    df = pd.read_sql("""
        SELECT id, symbol, side, entry_date, entry_time, exit_time,
               CAST(entry_price AS FLOAT) as entry_px,
               CAST(exit_price AS FLOAT) as exit_px,
               CAST(shares AS INT) as qty,
               CAST(pnl AS FLOAT) as pnl,
               max_gain_pct, exit_reason
        FROM trades WHERE exit_time IS NOT NULL
        ORDER BY entry_date, entry_time
    """, conn)
    conn.close()
    df['entry_hour']   = df['entry_time'].str[:2].astype(int)
    df['entry_min_i']  = df['entry_time'].str[:5]   # HH:MM
    df['is_morning']   = df['entry_hour'] < 12
    df['is_afternoon'] = df['entry_hour'] >= 12
    df['is_long']      = df['side'] == 'LONG'
    df['is_short']     = df['side'] == 'SHORT'
    df['winner']       = df['pnl'] > 0
    df['is_may']       = df['entry_date'] >= '2026-05-01'
    # Flag regime-flip victims (shorts covered by regime change)
    df['flip_victim'] = df['exit_reason'].fillna('').str.lower().str.contains('regime|flip|cover')
    return df

def pull_intraday(symbol, trade_date):
    try:
        t   = yf.Ticker(symbol)
        end = trade_date + timedelta(days=1)
        df  = t.history(start=trade_date, end=end, interval='5m', auto_adjust=True)
        if df.empty: return None
        df.index = (df.index.tz_convert('America/New_York')
                    if df.index.tzinfo else df.index.tz_localize('America/New_York'))
        return df.between_time('09:30', '15:55')
    except:
        return None

def parse_dt(date_str, time_str, tz='America/New_York'):
    return pd.Timestamp(f"{date_str} {time_str[:8]}").tz_localize(tz)

def get_price_at(bars, dt):
    if bars is None or bars.empty: return None
    m = bars[bars.index <= dt]
    return float(m.iloc[-1]['Close']) if not m.empty else float(bars.iloc[0]['Close'])

# ── Build intraday cache (60-day window) ──────────────────────────────────────

def build_cache(trades):
    print("Pulling 5-min intraday data for all symbol-days...")
    cache = {}
    pairs = trades[['entry_date','symbol']].drop_duplicates()
    for _, r in pairs.iterrows():
        key = f"{r['entry_date']}_{r['symbol']}"
        if key not in cache:
            d = datetime.strptime(r['entry_date'], '%Y-%m-%d').date()
            cache[key] = pull_intraday(r['symbol'], d)
    ok = sum(1 for v in cache.values() if v is not None)
    print(f"  {ok}/{len(cache)} symbol-days available\n")
    return cache

# ── Section helpers ────────────────────────────────────────────────────────────

def print_stats(label, subset):
    if len(subset) == 0:
        print(f"  {label:<40} — no data")
        return
    wr  = subset['winner'].mean() * 100
    avg = subset['pnl'].mean()
    tot = subset['pnl'].sum()
    print(f"  {label:<40} n={len(subset):>3}  WR={wr:>5.1f}%  avg=${avg:>+7.2f}  total=${tot:>+8.2f}")

def sep(title=''):
    if title:
        print(f"\n{'='*70}")
        print(f"  {title}")
        print('='*70)
    else:
        print('-'*70)

# ── PART 1: Morning vs Afternoon performance ───────────────────────────────────

def part1_morning_vs_afternoon(df):
    sep("PART 1: MORNING vs AFTERNOON PERFORMANCE")

    # Overall split
    print("\n[ All trades — all dates ]")
    print_stats("Morning LONG  (before 12pm)", df[df.is_morning  & df.is_long])
    print_stats("Afternoon LONG (12pm+)",      df[df.is_afternoon & df.is_long])
    print_stats("Morning SHORT (before 12pm)", df[df.is_morning  & df.is_short])
    print_stats("Afternoon SHORT (12pm+)",     df[df.is_afternoon & df.is_short])

    print("\n[ May only — v2 system ]")
    may = df[df.is_may]
    print_stats("Morning LONG",   may[may.is_morning  & may.is_long])
    print_stats("Afternoon LONG", may[may.is_afternoon & may.is_long])
    print_stats("Morning SHORT",  may[may.is_morning  & may.is_short])
    print_stats("Afternoon SHORT",may[may.is_afternoon & may.is_short])

    # Afternoon entries on days where morning was already profitable
    print("\n[ Afternoon entries grouped by MORNING P&L context ]")
    print(f"  {'morning context':<30}  {'n':>4}  {'WR':>6}  {'avg $':>8}  {'total $':>9}")
    sep()
    for lock in [0, 50, 100, 150, 200]:
        for cside in ['LONG', 'SHORT']:
            # For each date, compute morning realized P&L for the given side
            rows = []
            for dt, grp in df.groupby('entry_date'):
                morning_pnl = grp[grp.is_morning & (grp.side == cside)]['pnl'].sum()
                afternoon   = grp[grp.is_afternoon & (grp.side == cside)]
                if morning_pnl >= lock and len(afternoon) > 0:
                    rows.append(afternoon)
            if not rows: continue
            merged = pd.concat(rows)
            wr  = merged['winner'].mean() * 100
            avg = merged['pnl'].mean()
            tot = merged['pnl'].sum()
            label = f"{cside} afternoon when morning ≥${lock}"
            print(f"  {label:<40}  n={len(merged):>3}  WR={wr:>5.1f}%  avg=${avg:>+7.2f}  total=${tot:>+8.2f}")

# ── PART 2: Afternoon Gate Simulation ─────────────────────────────────────────

def part2_afternoon_gate(df):
    sep("PART 2: AFTERNOON GATE SIMULATION")
    print("  Block afternoon entries on days where morning P&L exceeds threshold.")
    print("  P&L of blocked trades assumed $0 (slot stays empty, no new trade).\n")

    total_actual = df['pnl'].sum()
    print(f"  Baseline (all trades): ${total_actual:.2f}\n")
    print(f"  {'lock $':>7}  {'cutoff':>7}  {'side':>6}  {'blocked':>8}  "
          f"{'blocked WR':>10}  {'blocked P&L':>12}  {'new total':>10}  {'delta':>8}")
    sep()

    best_result = None
    best_delta  = float('-inf')

    for lock in [50, 100, 150, 200]:
        for cutoff_h in [12, 13, 14]:
            for side in ['LONG', 'SHORT', 'BOTH']:
                blocked_pnl = 0.0
                blocked_wins = 0
                blocked_n   = 0

                for dt, grp in df.groupby('entry_date'):
                    # Morning P&L for the same direction
                    if side == 'BOTH':
                        morning_pnl = grp[grp.is_morning]['pnl'].sum()
                        aft_mask    = grp['entry_hour'] >= cutoff_h
                    elif side == 'LONG':
                        morning_pnl = grp[grp.is_morning & grp.is_long]['pnl'].sum()
                        aft_mask    = (grp['entry_hour'] >= cutoff_h) & grp.is_long
                    else:
                        morning_pnl = grp[grp.is_morning & grp.is_short]['pnl'].sum()
                        aft_mask    = (grp['entry_hour'] >= cutoff_h) & grp.is_short

                    if morning_pnl < lock:
                        continue   # morning not profitable enough, gate doesn't fire

                    afternoon_trades = grp[aft_mask]
                    blocked_pnl  += afternoon_trades['pnl'].sum()
                    blocked_wins += afternoon_trades['winner'].sum()
                    blocked_n    += len(afternoon_trades)

                if blocked_n == 0:
                    continue

                blocked_wr  = blocked_wins / blocked_n * 100
                new_total   = total_actual - blocked_pnl   # removing their P&L (not taken)
                delta       = new_total - total_actual      # positive = improvement

                marker = '  ← BEST' if delta > best_delta else ''
                if delta > best_delta:
                    best_delta  = delta
                    best_result = (lock, cutoff_h, side, blocked_n, blocked_wr,
                                   blocked_pnl, new_total, delta)

                print(f"  ${lock:>6}  {cutoff_h:>5}pm  {side:>6}  {blocked_n:>8}  "
                      f"{blocked_wr:>9.1f}%  ${blocked_pnl:>+11.2f}  "
                      f"${new_total:>9.2f}  {delta:>+8.2f}{marker}")

    if best_result:
        lock, ch, side, bn, bwr, bpnl, bnt, bd = best_result
        print(f"\n  ★ Best gate: morning_lock=${lock}, cutoff={ch}pm, side={side}")
        print(f"    Blocks {bn} trades (WR={bwr:.1f}%, total P&L={bpnl:+.2f})")
        print(f"    New total: ${bnt:.2f}  ({bd:+.2f} improvement)")

    # Day-by-day for best gate
    if best_result:
        lock, cutoff_h, side, *_ = best_result
        print(f"\n  DAY-BY-DAY: gate lock=${lock}, cutoff={cutoff_h}pm, side={side}")
        print(f"  {'date':<12} {'actual':>8} {'gated':>8} {'blocked trades':<30}")
        sep()
        for dt, grp in df.groupby('entry_date'):
            if side == 'LONG':
                morning_pnl = grp[grp.is_morning & grp.is_long]['pnl'].sum()
                aft = grp[(grp['entry_hour'] >= cutoff_h) & grp.is_long]
            elif side == 'SHORT':
                morning_pnl = grp[grp.is_morning & grp.is_short]['pnl'].sum()
                aft = grp[(grp['entry_hour'] >= cutoff_h) & grp.is_short]
            else:
                morning_pnl = grp[grp.is_morning]['pnl'].sum()
                aft = grp[grp['entry_hour'] >= cutoff_h]

            day_pnl    = grp['pnl'].sum()
            if morning_pnl >= lock and len(aft) > 0:
                gated_pnl  = day_pnl - aft['pnl'].sum()
                trade_list = ', '.join(
                    f"{r['symbol']}({r['pnl']:+.0f})" for _, r in aft.iterrows())
                print(f"  {dt:<12} ${day_pnl:>7.2f} ${gated_pnl:>7.2f}  blocked: {trade_list}")

# ── PART 3: Short Side Deep Dive ───────────────────────────────────────────────

def part3_short_deep_dive(df, cache):
    sep("PART 3: SHORT SIDE DEEP DIVE")

    shorts = df[df.is_short].copy()
    if len(shorts) == 0:
        print("No short trades in DB.")
        return

    print(f"\n  Total shorts: {len(shorts)}")
    print(f"  WR: {shorts['winner'].mean()*100:.1f}%")
    print(f"  Avg P&L: ${shorts['pnl'].mean():+.2f}")
    print(f"  Total P&L: ${shorts['pnl'].sum():+.2f}")
    print(f"  Best: ${shorts['pnl'].max():+.2f}  Worst: ${shorts['pnl'].min():+.2f}")

    # Regime flip victims
    print("\n  [ Exit reason breakdown ]")
    flip = shorts[shorts['flip_victim']]
    no_flip = shorts[~shorts['flip_victim']]
    print_stats("Regime-flip victims",    flip)
    print_stats("Non-flip exits",         no_flip)
    print_stats("  ↳ natural exits won",  no_flip[no_flip.winner])
    print_stats("  ↳ natural exits lost", no_flip[~no_flip.winner])

    # By timing
    print("\n  [ Morning vs Afternoon shorts ]")
    print_stats("Morning bears (<12pm)", shorts[shorts.is_morning])
    print_stats("Afternoon bears (12+)", shorts[shorts.is_afternoon])

    # By month
    print("\n  [ By period ]")
    print_stats("April shorts", shorts[~shorts.is_may])
    print_stats("May shorts",   shorts[shorts.is_may])

    # By symbol — which symbols are killing us
    print("\n  [ Per-symbol short performance (≥2 trades) ]")
    print(f"  {'symbol':<8} {'n':>3}  {'WR':>6}  {'avg $':>8}  {'total $':>9}  {'worst':>8}")
    sep()
    for sym, grp in shorts.groupby('symbol'):
        if len(grp) < 2: continue
        wr  = grp['winner'].mean() * 100
        avg = grp['pnl'].mean()
        tot = grp['pnl'].sum()
        wst = grp['pnl'].min()
        flag = '  ← AVOID' if wr < 30 or tot < -30 else ''
        print(f"  {sym:<8} {len(grp):>3}  {wr:>5.1f}%  ${avg:>+7.2f}  ${tot:>+8.2f}  ${wst:>+7.2f}{flag}")

    # Losing shorts: max_gain pattern (May 21+ where we capture it)
    print("\n  [ Losing shorts: max_gain_pct distribution (May 21+ only) ]")
    recent_losers = shorts[shorts.is_may & ~shorts.winner & shorts['max_gain_pct'].notna()]
    if len(recent_losers) > 0:
        print(f"  Trades: {len(recent_losers)}")
        for _, r in recent_losers.iterrows():
            print(f"  {r['entry_date']} {r['symbol']:6s}  pnl=${r['pnl']:+.2f}  "
                  f"max_gain={r['max_gain_pct']:.2f}%  reason={str(r['exit_reason'])[:50]}")
    else:
        print("  No recent losing shorts with max_gain data yet.")

    # Exit reason breakdown for short losses
    print("\n  [ Losing short exit reasons ]")
    for reason, grp in shorts[~shorts.winner].groupby('exit_reason'):
        print(f"  {str(reason)[:60]:<62} n={len(grp):>2}  total=${grp['pnl'].sum():+.2f}")

    # Day-level: which days had all-short losses
    print("\n  [ Short performance by day (days with shorts) ]")
    print(f"  {'date':<12} {'n':>3}  {'WR':>6}  {'total $':>9}  {'type'}")
    sep()
    for dt, grp in shorts.groupby('entry_date'):
        wr  = grp['winner'].mean() * 100
        tot = grp['pnl'].sum()
        n   = len(grp)
        flips = grp['flip_victim'].sum()
        note = f"  {flips} flip-victims" if flips > 0 else ''
        flag = '  ← ALL LOSS' if wr == 0 else ('  ← STRONG' if wr == 100 else '')
        print(f"  {dt:<12} {n:>3}  {wr:>5.1f}%  ${tot:>+8.2f}{flag}{note}")

    # Intraday analysis: for losing shorts, when did they start losing?
    print("\n  [ Losing shorts: intraday peak pattern (using 5-min data) ]")
    print(f"  {'date':<12} {'symbol':<7} {'entry_px':>9} {'peak_gain':>10} "
          f"{'max_loss':>10} {'held_min':>10}  fate")
    sep()
    for _, tr in shorts[~shorts.winner].iterrows():
        key  = f"{tr['entry_date']}_{tr['symbol']}"
        bars = cache.get(key)
        if bars is None or bars.empty:
            continue
        try:
            entry_dt = parse_dt(tr['entry_date'], str(tr['entry_time']))
        except:
            continue
        exit_t = str(tr['exit_time'])[:8]
        try:
            exit_hour = int(exit_t[:2])
            if exit_hour < 9:
                next_d = (datetime.strptime(tr['entry_date'], '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
                exit_dt = parse_dt(next_d, exit_t)
            else:
                exit_dt = parse_dt(tr['entry_date'], exit_t)
        except:
            continue

        entry_px   = float(tr['entry_px'])
        sess_low   = entry_px
        sess_high  = entry_px
        peak_gain  = 0.0
        max_loss   = 0.0

        future = bars[bars.index >= entry_dt]
        for bar_time, bar in future.iterrows():
            if bar_time > exit_dt:
                break
            lo = float(bar['Low'])
            hi = float(bar['High'])
            sess_low  = min(sess_low,  lo)
            sess_high = max(sess_high, hi)
            pg  = (entry_px - sess_low)  / entry_px * 100   # peak short gain
            ml  = (sess_high - entry_px) / entry_px * 100   # max adverse move

        held_min = (exit_dt - entry_dt).total_seconds() / 60
        fate = "never ran" if peak_gain < 0.5 else "ran then reversed"
        print(f"  {tr['entry_date']:<12} {tr['symbol']:<7} ${entry_px:>8.2f} "
              f"  {peak_gain:>+8.2f}%  {max_loss:>+9.2f}%  {held_min:>9.0f}m  {fate}")

# ── PART 4: Short-Side Exit Options ───────────────────────────────────────────

def part4_short_exits(df, cache):
    sep("PART 4: SHORT-SIDE EXIT OPTION TESTING")
    print("  Apply the same calibrated Protection floor to short positions.")
    print("  Also test Gap2 for shorts (peak<1.5%, loss>0.5% after 120min).\n")

    shorts   = df[df.is_short].copy()
    total_sh = shorts['pnl'].sum()
    print(f"  Short baseline: ${total_sh:.2f}\n")

    # Gap2 on shorts
    print("  GAP2 SWEEP — shorts only")
    print(f"  {'mins':>6} {'loss%':>6} {'FP':>4} {'TP':>4} {'Fired':>6} {'Net Δ':>9} {'New':>10}")
    sep()
    for mins in [90, 120, 150]:
        for loss in [0.5, 1.0, 1.5]:
            new_pnl  = 0.0
            fp = tp = fired = 0
            for _, tr in shorts.iterrows():
                key  = f"{tr['entry_date']}_{tr['symbol']}"
                bars = cache.get(key)
                actual = float(tr['pnl'])

                if bars is None or not tr['qty'] or bars.empty:
                    new_pnl += actual
                    continue
                try:
                    entry_dt = parse_dt(tr['entry_date'], str(tr['entry_time']))
                except:
                    new_pnl += actual
                    continue

                entry_px = float(tr['entry_px'])
                qty      = int(tr['qty'])
                sess_ext = entry_px
                result   = actual
                was_fired = False

                for bar_time, bar in bars[bars.index >= entry_dt].iterrows():
                    m   = (bar_time - entry_dt).total_seconds() / 60
                    px  = float(bar['Close'])
                    sess_ext = min(sess_ext, float(bar['Low']))
                    mg  = (entry_px - sess_ext) / entry_px * 100
                    cur = (px - entry_px)       / entry_px * 100  # loss for short
                    if m >= mins and mg < 1.5 and cur > loss:
                        result    = round((entry_px - px) * qty, 2)
                        was_fired = True
                        break

                new_pnl += result
                if was_fired:
                    fired += 1
                    if actual > 0: fp += 1
                    elif result > actual: tp += 1

            delta = new_pnl - total_sh
            v = '✅' if fp == 0 and delta > 0 else ('⚠️' if fp > 0 else '❌')
            print(f"  {mins:>6} {loss:>6.2f} {fp:>4} {tp:>4} {fired:>6} "
                  f"{delta:>+9.2f} ${new_pnl:>9.2f}  {v}")

    # Protection floor on shorts: per-day portfolio (short-only portfolio)
    print(f"\n  PROTECTION FLOOR — short-only portfolio")
    print(f"  {'gap%':>6} {'min_peak':>9} {'FP':>4} {'Fired':>6} {'Net Δ':>9} {'New':>10}")
    sep()
    for gap_pct in [0.20, 0.25, 0.30]:
        for min_peak in [50, 100]:
            new_tot = 0.0
            fp_tot  = 0
            fired_n = 0

            for date_str, day_grp in shorts.groupby('entry_date'):
                day_intraday = {sym: cache.get(f"{date_str}_{sym}")
                                for sym in day_grp['symbol'].unique()}

                # Track short portfolio: sess_ext per trade
                tz = 'America/New_York'
                states = []
                for _, tr in day_grp.iterrows():
                    try:
                        entry_dt = parse_dt(date_str, str(tr['entry_time']), tz)
                    except:
                        continue
                    exit_t = str(tr['exit_time'])[:8]
                    try:
                        exit_hour = int(exit_t[:2])
                        if exit_hour < 9:
                            nd = (datetime.strptime(date_str,'%Y-%m-%d')+timedelta(days=1)).strftime('%Y-%m-%d')
                            exit_dt = parse_dt(nd, exit_t, tz)
                        else:
                            exit_dt = parse_dt(date_str, exit_t, tz)
                    except:
                        exit_dt = parse_dt(date_str, '15:52:00', tz)
                    states.append({
                        'id': tr['id'], 'symbol': tr['symbol'],
                        'entry_px': float(tr['entry_px']),
                        'qty': int(tr['qty']) if tr['qty'] else 0,
                        'actual_pnl': float(tr['pnl']),
                        'actual_exit': exit_dt, 'entry_dt': entry_dt,
                        'bars': day_intraday.get(tr['symbol']),
                        'sess_ext': float(tr['entry_px']), 'max_gain': 0.0,
                        'new_pnl': float(tr['pnl']), 'fired': False, 'new_exit': None,
                    })

                if not states: continue
                start_ts = pd.Timestamp(f"{date_str} 10:00:00", tz=tz)
                end_ts   = pd.Timestamp(f"{date_str} 15:52:00", tz=tz)
                peak_p   = 0.0

                for t in pd.date_range(start_ts, end_ts, freq='5min'):
                    realized = 0.0; unreal = 0.0; losers = []
                    for s in states:
                        if t < s['entry_dt']: continue
                        exited = (s['new_exit'] is not None and t >= s['new_exit']) or \
                                 (s['new_exit'] is None and t >= s['actual_exit'])
                        if exited:
                            realized += s['new_pnl']; continue
                        px = get_price_at(s['bars'], t) or s['entry_px']
                        px = float(px)
                        s['sess_ext'] = min(s['sess_ext'], px)
                        s['max_gain'] = (s['entry_px']-s['sess_ext'])/s['entry_px']*100
                        cur_pct = (px - s['entry_px']) / s['entry_px'] * 100  # loss
                        u = (s['entry_px'] - px) * s['qty']
                        unreal += u
                        if cur_pct > 0.3 and s['max_gain'] < 1.5 and not s['fired']:
                            losers.append((s, px))

                    portfolio = realized + unreal
                    if portfolio > peak_p: peak_p = portfolio
                    if peak_p >= min_peak and portfolio < peak_p * (1-gap_pct):
                        for s, px in losers:
                            if not s['fired']:
                                s['new_pnl'] = round((s['entry_px']-px)*s['qty'], 2)
                                s['new_exit'] = t; s['fired'] = True

                for s in states:
                    new_tot += s['new_pnl']
                    if s['fired']:
                        fired_n += 1
                        if s['actual_pnl'] > 0: fp_tot += 1

            delta = new_tot - total_sh
            v = '✅' if fp_tot == 0 and delta > 0 else ('⚠️' if fp_tot > 0 else '❌')
            print(f"  {gap_pct:>6.0%} {min_peak:>9} {fp_tot:>4} {fired_n:>6} "
                  f"{delta:>+9.2f} ${new_tot:>9.2f}  {v}")

# ── PART 5: Combined Summary ───────────────────────────────────────────────────

def part5_summary(df):
    sep("PART 5: SUMMARY & RECOMMENDATIONS")

    # Key metrics
    total     = df['pnl'].sum()
    longs     = df[df.is_long]
    shorts    = df[df.is_short]
    may       = df[df.is_may]

    print(f"\n  All-time ({df['entry_date'].min()} to {df['entry_date'].max()}):")
    print(f"  Total P&L: ${total:.2f}  |  {len(df)} trades  |  WR {df['winner'].mean()*100:.1f}%")
    print(f"  LONG:  ${longs['pnl'].sum():+.2f}  ({longs['winner'].mean()*100:.1f}% WR)")
    print(f"  SHORT: ${shorts['pnl'].sum():+.2f}  ({shorts['winner'].mean()*100:.1f}% WR)")

    # Morning/afternoon split
    am_long = df[df.is_morning & df.is_long]
    pm_long = df[df.is_afternoon & df.is_long]
    am_sh   = df[df.is_morning & df.is_short]
    pm_sh   = df[df.is_afternoon & df.is_short]
    print(f"\n  Morning LONG:     ${am_long['pnl'].sum():+.2f}  WR {am_long['winner'].mean()*100:.1f}%  n={len(am_long)}")
    print(f"  Afternoon LONG:   ${pm_long['pnl'].sum():+.2f}  WR {pm_long['winner'].mean()*100:.1f}%  n={len(pm_long)}")
    print(f"  Morning SHORT:    ${am_sh['pnl'].sum():+.2f}  WR {am_sh['winner'].mean()*100:.1f}%  n={len(am_sh)}")
    print(f"  Afternoon SHORT:  ${pm_sh['pnl'].sum():+.2f}  WR {pm_sh['winner'].mean()*100:.1f}%  n={len(pm_sh)}")

    # Short: flip victims
    shorts_may = shorts[shorts.is_may]
    flip   = shorts_may[shorts_may['flip_victim']]
    noflip = shorts_may[~shorts_may['flip_victim']]
    print(f"\n  May SHORT breakdown:")
    print(f"  Regime-flip victims: {len(flip):>3}  P&L ${flip['pnl'].sum():+.2f}  WR {flip['winner'].mean()*100:.1f}% (if any)")
    print(f"  Non-flip exits:      {len(noflip):>3}  P&L ${noflip['pnl'].sum():+.2f}  WR {noflip['winner'].mean()*100:.1f}%")

    # Annualised impact estimates
    td  = df['entry_date'].nunique()
    ann = 252 / td
    print(f"\n  Trading days in sample: {td} → annualisation factor: {ann:.1f}x")

    print(f"\n  KEY FINDINGS:")
    print(f"  1. Afternoon LONG WR = {pm_long['winner'].mean()*100:.1f}% vs Morning LONG WR = {am_long['winner'].mean()*100:.1f}%")
    print(f"     → {'Gate afternoon longs — lower quality' if pm_long['winner'].mean() < am_long['winner'].mean() - 0.05 else 'Afternoon longs acceptable'}")
    print(f"  2. SHORT WR = {shorts['winner'].mean()*100:.1f}%  (target: ≥60%)")
    print(f"     → {'SHORT edge is marginal — needs tighter entry or more data' if shorts['winner'].mean() < 0.55 else 'SHORT edge is present'}")
    if len(flip) > 0:
        print(f"  3. Regime-flip victims = {len(flip)} shorts, ${flip['pnl'].sum():+.2f}")
        print(f"     → These are unavoidable given 3-scan rule; regime flip exit is doing its job")

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("60-DAY AFTERNOON GATE + SHORT SIDE ANALYSIS")
    print("=" * 70)

    df    = load_all_trades()
    cache = build_cache(df)

    print(f"Total trades loaded: {len(df)} | "
          f"{df['entry_date'].min()} to {df['entry_date'].max()}")
    print(f"LONG: {df.is_long.sum()}  SHORT: {df.is_short.sum()}  "
          f"WR: {df['winner'].mean()*100:.1f}%  Total P&L: ${df['pnl'].sum():.2f}\n")

    part1_morning_vs_afternoon(df)
    part2_afternoon_gate(df)
    part3_short_deep_dive(df, cache)
    part4_short_exits(df, cache)
    part5_summary(df)

    print("\n✓ Analysis complete")

if __name__ == '__main__':
    main()
