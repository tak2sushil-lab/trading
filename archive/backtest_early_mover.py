"""
EARLY MOVER BACKTEST — 3 LAYERS
=================================
Tests the hypothesis: "Stocks showing genuine momentum in the first
15-30 min (9:30-10:00) produce better outcomes than waiting for 10:00."

Also tests partial-exit structure (60% at target, 40% with wider trail)
vs current all-in/all-out approach.

Layers:
  A  5-min / 6 months   (IBKR cache)    — exact 9:45 vs 10:00 comparison
  B  1-hour / 5 years   (IBKR cache)    — first-bar strength, 5-year regime coverage
  C  Daily / 5 years    (yfinance)       — gap+volume proxy, max statistical power

Usage:
    venv/bin/python backtest_early_mover.py
    venv/bin/python backtest_early_mover.py --layer A
    venv/bin/python backtest_early_mover.py --layer B
    venv/bin/python backtest_early_mover.py --layer C
"""

import sqlite3
import pandas as pd
import numpy as np
import yfinance as yf
import pytz
import sys
import os
from datetime import datetime, timedelta, date

ET       = pytz.timezone('America/New_York')
DB_PATH  = os.path.join(os.path.dirname(__file__), 'velocity_data.db')
STOP_PCT = 0.05   # 5% stop loss (matches live system)

UNIVERSE = [
    'NVDA','AMD','PLTR','SMCI','MARA','COIN','TSLA','META','GOOGL','MSFT',
    'AAPL','AMZN','APP','AXON','ARM','DDOG','CRWD','SHOP','MSTR','IONQ',
    'HOOD','SOFI','UPST','RKLB','JOBY','OKLO','SMR','QBTS','RGTI','SOUN',
    'ORCL','CRM','NOW','SNOW','NET','PANW','FTNT','ZS','ANET','MRVL',
    'LRCX','KLAC','AMAT','QCOM','INTC','MU','ON','WOLF','MPWR','ENPH',
    'CCJ','UEC','NNE','SMH','XLK','EOSE','IREN','GTLB','BILL','MNDY',
    'CELH','HIMS','PENN','DKNG','LLY','ABBV','MRNA','RXRX','NVAX','CRSP',
    'XOM','CVX','SLB','OXY','FANG','MPC','VLO','NEM','GDX','GDXJ',
    'JPM','BAC','GS','MS','SCHW','MA','V','PYPL','AFRM',
    'NFLX','DIS','SPOT','RBLX','U','EA','TTWO','MTCH','BMBL',
]
UNIVERSE = list(dict.fromkeys(UNIVERSE))

# ── Shared stats helper ────────────────────────────────────────────────────────
def stats(returns):
    """Given list of % returns (with stop already applied), return summary dict."""
    if not returns:
        return dict(n=0, wr=0, avg=0, avg_w=0, avg_l=0, med=0, best=0, worst=0)
    arr   = np.array(returns)
    wins  = arr[arr > 0]
    loss  = arr[arr <= 0]
    return dict(
        n     = len(arr),
        wr    = round(len(wins) / len(arr) * 100, 1),
        avg   = round(float(np.mean(arr)), 3),
        avg_w = round(float(np.mean(wins)),  3) if len(wins) else 0,
        avg_l = round(float(np.mean(loss)),  3) if len(loss) else 0,
        med   = round(float(np.median(arr)), 3),
        best  = round(float(np.max(arr)),    2),
        worst = round(float(np.min(arr)),    2),
    )

def print_stats(label, s, indent=2):
    pad = ' ' * indent
    if s['n'] == 0:
        print(f"{pad}{label:45s}  n=0")
        return
    print(f"{pad}{label:45s}  n={s['n']:4d}  WR={s['wr']:4.1f}%  "
          f"avg={s['avg']:+.3f}%  W:{s['avg_w']:+.3f}% / L:{s['avg_l']:+.3f}%")

def apply_stop(entry, future_prices, stop_pct=STOP_PCT):
    """Walk future prices; return % return at stop or end."""
    stop = entry * (1 - stop_pct)
    for p in future_prices:
        if p <= stop:
            return (stop - entry) / entry * 100
    if future_prices:
        return (future_prices[-1] - entry) / entry * 100
    return 0.0

def apply_partial_exit(entry, future_prices, target_pct=0.025,
                       partial_frac=0.60, trail_pct=0.04, stop_pct=STOP_PCT):
    """
    Simulate partial exit:
      - Exit partial_frac of position at +target_pct
      - Remaining (1-partial_frac) trails with trail_pct stop from HOD
    Returns blended % return.
    """
    stop      = entry * (1 - stop_pct)
    target    = entry * (1 + target_pct)
    partial_fired = False
    partial_ret   = 0.0
    hod           = entry
    trail_stop    = entry * (1 - stop_pct)   # initially same as hard stop

    for p in future_prices:
        hod = max(hod, p)
        trail_stop = max(trail_stop, hod * (1 - trail_pct))

        if not partial_fired and p >= target:
            partial_ret   = target_pct * 100
            partial_fired = True

        if partial_fired:
            if p <= trail_stop:
                rest_ret = (trail_stop - entry) / entry * 100
                return partial_frac * partial_ret + (1 - partial_frac) * rest_ret
        else:
            if p <= stop:
                return (stop - entry) / entry * 100

    # EOD
    eod = future_prices[-1] if future_prices else entry
    if partial_fired:
        rest_ret = (eod - entry) / entry * 100
        return partial_frac * partial_ret + (1 - partial_frac) * rest_ret
    return (eod - entry) / entry * 100

# ══════════════════════════════════════════════════════════════════════════════
# LAYER A  —  5-min / 6 months  (IBKR cache)
# ══════════════════════════════════════════════════════════════════════════════
def run_layer_a():
    print("\n" + "="*70)
    print("  LAYER A — 5-min / 6 months  (IBKR data)")
    print("  Question: Does velocity-eligible early entry (9:45) beat 10:00?")
    print("="*70)

    if not os.path.exists(DB_PATH):
        print("  ❌ velocity_data.db not found. Run download_ibkr_cache.py first.")
        return

    conn  = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(DISTINCT symbol) FROM bars_5m").fetchone()[0]
    if count == 0:
        print("  ❌ bars_5m table is empty. Run download_ibkr_cache.py --only 5m first.")
        conn.close()
        return
    print(f"  Cache: {count} symbols with 5-min data")

    rows_9_45_eligible  = []
    rows_9_45_blocked   = []
    rows_10_00_eligible = []
    rows_10_00_blocked  = []

    # partial exit comparison
    rows_10_00_current  = []   # all-in/all-out (current system)
    rows_10_00_partial  = []   # partial exit simulation

    second_leg_count  = 0
    second_leg_total  = 0

    for sym in UNIVERSE:
        df = pd.read_sql(
            "SELECT * FROM bars_5m WHERE symbol=? ORDER BY date",
            conn, params=(sym,)
        )
        if df.empty:
            continue

        df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert(ET)
        df = df.set_index('date').sort_index()
        df = df.between_time('09:30', '16:00')

        for day, grp in df.groupby(df.index.date):
            grp = grp.copy()
            if len(grp) < 20:
                continue

            session_open = float(grp['open'].iloc[0])
            if session_open <= 0:
                continue

            # Must be up at least 1% from open at time of check (selectivity filter)
            def check_velocity(grp_slice):
                if len(grp_slice) < 2:
                    return None
                curr = grp_slice.iloc[-1]
                prev = grp_slice.iloc[-2]
                c_close = float(curr['close']); c_open = float(curr['open'])
                c_vol   = float(curr['volume'])
                p_close = float(prev['close'])
                p_vol   = float(prev['volume']) if float(prev['volume']) > 0 else 1

                tp   = (grp_slice['high'] + grp_slice['low'] + grp_slice['close']) / 3
                vwap = float((tp * grp_slice['volume']).sum() /
                              grp_slice['volume'].sum()) if grp_slice['volume'].sum() > 0 else c_close

                move_from_open = (c_close - session_open) / session_open * 100
                vwap_dist      = (c_close - vwap) / vwap * 100 if vwap else 0

                slow = (
                    c_close < c_open or        # red bar
                    c_close < p_close or        # losing momentum
                    c_vol < p_vol * 0.80 or     # volume fading
                    abs(vwap_dist) < 0.30 or    # at VWAP
                    move_from_open < 0.50        # barely moved
                )
                return not slow, move_from_open

            # Get bars up to 9:45 and 10:00
            grp_0945 = grp.between_time('09:30', '09:44')
            grp_1000 = grp.between_time('09:30', '09:59')
            future_after_0945 = grp.between_time('09:45', '16:00')
            future_after_1000 = grp.between_time('10:00', '16:00')

            if len(grp_0945) < 2 or len(future_after_0945) < 5:
                continue
            if len(grp_1000) < 2 or len(future_after_1000) < 5:
                continue

            # --- 9:45 velocity check ---
            vel_0945 = check_velocity(grp_0945)
            if vel_0945:
                eligible_0945, move_0945 = vel_0945
                entry_0945 = float(grp_0945.iloc[-1]['close'])
                future_0945 = future_after_0945['close'].tolist()
                ret_0945 = apply_stop(entry_0945, future_0945)
                if eligible_0945:
                    rows_9_45_eligible.append(ret_0945)
                else:
                    rows_9_45_blocked.append(ret_0945)

            # --- 10:00 velocity check ---
            vel_1000 = check_velocity(grp_1000)
            if vel_1000:
                eligible_1000, move_1000 = vel_1000
                entry_1000 = float(grp_1000.iloc[-1]['close'])
                future_1000 = future_after_1000['close'].tolist()
                ret_current  = apply_stop(entry_1000, future_1000)
                ret_partial  = apply_partial_exit(entry_1000, future_1000)
                rows_10_00_current.append(ret_current)
                rows_10_00_partial.append(ret_partial)
                if eligible_1000:
                    rows_10_00_eligible.append(ret_current)
                else:
                    rows_10_00_blocked.append(ret_current)

            # --- Second-leg analysis (RKLB question) ---
            # Does a stock pulled back after initial 10:00 run continue for a second leg?
            if len(future_after_1000) >= 20:
                entry_1000 = float(grp_1000.iloc[-1]['close']) if len(grp_1000) else session_open
                future_prices = future_after_1000['close'].tolist()
                if len(future_prices) >= 20:
                    # First peak (HOD in first 30 min)
                    first_30 = future_prices[:6]
                    rest      = future_prices[6:]
                    if first_30 and rest:
                        first_peak = max(first_30)
                        pullback   = min(rest[:6]) if len(rest) >= 6 else rest[0]
                        second_peak = max(rest) if rest else pullback
                        if first_peak > entry_1000 * 1.01:  # had a meaningful first peak
                            second_leg_total += 1
                            if second_peak > first_peak:     # continued higher after pullback
                                second_leg_count += 1

    conn.close()

    s_9_45_el  = stats(rows_9_45_eligible)
    s_9_45_bl  = stats(rows_9_45_blocked)
    s_10_el    = stats(rows_10_00_eligible)
    s_10_bl    = stats(rows_10_00_blocked)
    s_current  = stats(rows_10_00_current)
    s_partial  = stats(rows_10_00_partial)

    print("\n  9:45 entry — velocity eligible vs blocked:")
    print_stats("  9:45 ELIGIBLE (velocity fast)",   s_9_45_el)
    print_stats("  9:45 BLOCKED  (velocity slow)",   s_9_45_bl)
    lift_early = s_9_45_el['avg'] - s_9_45_bl['avg']
    print(f"    → Early velocity lift: {lift_early:+.3f}%")

    print("\n  10:00 entry — velocity eligible vs blocked:")
    print_stats("  10:00 ELIGIBLE (velocity fast)",  s_10_el)
    print_stats("  10:00 BLOCKED  (velocity slow)",  s_10_bl)
    lift_gate = s_10_el['avg'] - s_10_bl['avg']
    print(f"    → 10:00 velocity gate lift: {lift_gate:+.3f}%")

    print("\n  Early (9:45 eligible) vs current (10:00 all):")
    print_stats("  9:45 velocity-eligible",          s_9_45_el)
    print_stats("  10:00 all entries (current)",     s_current)
    lift_vs_current = s_9_45_el['avg'] - s_current['avg']
    print(f"    → Early entry advantage: {lift_vs_current:+.3f}%")

    print("\n  Partial exit simulation (10:00 entries):")
    print_stats("  Current  (all-in → trail stop)",        s_current)
    print_stats("  Partial  (60% at +2.5%, 40% trail)",    s_partial)
    partial_lift = s_partial['avg'] - s_current['avg']
    print(f"    → Partial exit lift: {partial_lift:+.3f}% per trade")

    if second_leg_total > 0:
        second_leg_pct = second_leg_count / second_leg_total * 100
        print(f"\n  Second-leg analysis (after initial run + pullback):")
        print(f"    {second_leg_count}/{second_leg_total} ({second_leg_pct:.1f}%) of first-peak days "
              f"continued to make new highs after pullback")
        print(f"    → {'Partial exit worth it' if second_leg_pct > 40 else 'Second legs are rare — current exit is fine'}")

    print("\n  LAYER A VERDICT:")
    if lift_vs_current >= 0.20:
        print(f"  ✅ EARLY ENTRY HAS EDGE ({lift_vs_current:+.3f}%) — build velocity gate")
    elif lift_vs_current >= 0.08:
        print(f"  ⚠️  MARGINAL EDGE ({lift_vs_current:+.3f}%) — collect more data first")
    else:
        print(f"  ❌ NO EDGE ({lift_vs_current:+.3f}%) — 10:00 entry is fine")

    if partial_lift >= 0.15:
        print(f"  ✅ PARTIAL EXIT HAS EDGE ({partial_lift:+.3f}%) — build partial exit logic")
    elif partial_lift >= 0.05:
        print(f"  ⚠️  PARTIAL EXIT MARGINAL ({partial_lift:+.3f}%)")
    else:
        print(f"  ❌ PARTIAL EXIT NO EDGE ({partial_lift:+.3f}%) — keep current exit")

# ══════════════════════════════════════════════════════════════════════════════
# LAYER B  —  1-hour / 5 years  (IBKR cache)
# ══════════════════════════════════════════════════════════════════════════════
def run_layer_b():
    print("\n" + "="*70)
    print("  LAYER B — 1-hour / 5 years  (IBKR data)")
    print("  Question: Does a strong first hourly bar predict rest-of-day gain?")
    print("  Covers: 2021 bull, 2022 crash, 2023 recovery, 2024-25 AI bull")
    print("="*70)

    if not os.path.exists(DB_PATH):
        print("  ❌ velocity_data.db not found. Run download_ibkr_cache.py first.")
        return

    conn  = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(DISTINCT symbol) FROM bars_1h").fetchone()[0]
    if count == 0:
        print("  ❌ bars_1h table is empty. Run download_ibkr_cache.py --only 1h first.")
        conn.close()
        return
    print(f"  Cache: {count} symbols with 1-hour data")

    strong_first_bar  = []   # first bar strong (>1% green, high vol) → rest of day
    weak_first_bar    = []   # first bar weak or red → rest of day
    strong_hod_ratios = []   # HOD as % of entry on strong days
    weak_hod_ratios   = []

    # By year — to see regime consistency
    by_year = {}

    vol_avg_window = 20  # bars for rolling avg volume

    for sym in UNIVERSE:
        df = pd.read_sql(
            "SELECT * FROM bars_1h WHERE symbol=? ORDER BY date",
            conn, params=(sym,)
        )
        if len(df) < 50:
            continue

        df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert(ET)
        df = df.set_index('date').sort_index()
        df = df.between_time('09:30', '16:00')

        # Rolling avg volume
        df['vol_avg'] = df['volume'].rolling(vol_avg_window, min_periods=5).mean().shift(1)

        for day, grp in df.groupby(df.index.date):
            grp = grp.copy()
            if len(grp) < 4:   # need at least 4 hourly bars (9:30, 10:30, 11:30, 12:30+)
                continue

            first_bar = grp.iloc[0]
            rest_bars = grp.iloc[1:]

            fb_open  = float(first_bar['open'])
            fb_close = float(first_bar['close'])
            fb_high  = float(first_bar['high'])
            fb_vol   = float(first_bar['volume'])
            fb_avg_v = float(first_bar['vol_avg']) if not pd.isna(first_bar['vol_avg']) else fb_vol

            if fb_open <= 0:
                continue

            fb_move_pct  = (fb_close - fb_open) / fb_open * 100
            fb_vol_ratio = fb_vol / fb_avg_v if fb_avg_v > 0 else 1.0

            # Strong first bar: green + moved >1% + volume elevated
            is_strong = (fb_close > fb_open and
                         fb_move_pct > 1.0 and
                         fb_vol_ratio > 1.3)

            # Rest-of-day return (from end of first bar)
            entry  = fb_close
            eod    = float(rest_bars.iloc[-1]['close'])
            hod    = float(rest_bars['high'].max())
            ret    = (eod - entry) / entry * 100
            hod_r  = (hod - entry) / entry * 100

            # Apply partial exit simulation on hourly granularity
            future_closes = rest_bars['close'].tolist()
            ret_with_stop = apply_stop(entry, future_closes)

            yr = str(day.year)
            if yr not in by_year:
                by_year[yr] = {'strong': [], 'weak': []}

            if is_strong:
                strong_first_bar.append(ret_with_stop)
                strong_hod_ratios.append(hod_r)
                by_year[yr]['strong'].append(ret_with_stop)
            else:
                weak_first_bar.append(ret_with_stop)
                weak_hod_ratios.append(hod_r)
                by_year[yr]['weak'].append(ret_with_stop)

    conn.close()

    s_strong = stats(strong_first_bar)
    s_weak   = stats(weak_first_bar)
    lift     = s_strong['avg'] - s_weak['avg']

    print(f"\n  Strong first bar vs weak first bar (rest-of-day outcome):")
    print_stats("  STRONG first bar (green >1%, vol >1.3x)", s_strong)
    print_stats("  WEAK / red first bar",                     s_weak)
    print(f"    → Strong first bar lift: {lift:+.3f}%")

    if strong_hod_ratios and weak_hod_ratios:
        avg_strong_hod = np.mean(strong_hod_ratios)
        avg_weak_hod   = np.mean(weak_hod_ratios)
        print(f"\n  HOD capture (from end of first bar):")
        print(f"    Strong first bar days: avg HOD +{avg_strong_hod:.2f}% above entry")
        print(f"    Weak first bar days:   avg HOD +{avg_weak_hod:.2f}% above entry")
        print(f"    → Strong days have {avg_strong_hod - avg_weak_hod:.2f}% more HOD room")

    print(f"\n  By year — is the signal consistent across regimes?")
    for yr in sorted(by_year.keys()):
        s  = stats(by_year[yr]['strong'])
        w  = stats(by_year[yr]['weak'])
        lft = s['avg'] - w['avg']
        regime_label = {'2021': 'bull', '2022': 'crash', '2023': 'recovery',
                        '2024': 'AI bull', '2025': 'AI bull', '2026': '2026'}.get(yr, '')
        sig = '✅' if lft > 0.10 else ('⚠️ ' if lft > 0 else '❌')
        print(f"    {yr} ({regime_label:10s}): strong n={s['n']:4d} avg={s['avg']:+.3f}%  "
              f"weak n={w['n']:4d} avg={w['avg']:+.3f}%  lift={lft:+.3f}% {sig}")

    print(f"\n  LAYER B VERDICT:")
    consistent_years = sum(1 for yr in by_year.values()
                           if stats(yr['strong'])['avg'] > stats(yr['weak'])['avg'])
    total_years = len(by_year)
    if lift >= 0.20 and consistent_years >= total_years * 0.7:
        print(f"  ✅ SIGNAL IS REAL AND CONSISTENT ({lift:+.3f}% lift, "
              f"{consistent_years}/{total_years} years positive)")
    elif lift >= 0.08:
        print(f"  ⚠️  MARGINAL ({lift:+.3f}% lift, {consistent_years}/{total_years} years positive)")
    else:
        print(f"  ❌ NO CONSISTENT EDGE ({lift:+.3f}% lift)")

# ══════════════════════════════════════════════════════════════════════════════
# LAYER C  —  Daily / 5 years  (yfinance)
# ══════════════════════════════════════════════════════════════════════════════
def run_layer_c():
    print("\n" + "="*70)
    print("  LAYER C — Daily OHLCV / 5 years  (yfinance)")
    print("  Question: Do gap+momentum days (proxy for early velocity) outperform?")
    print("  ~124K stock-days — highest statistical power")
    print("="*70)

    gap_momentum_days = []   # gap >1% AND open=day low OR strong trend day
    flat_open_days    = []   # gap <0.5% or reversal open
    second_leg_rate_by_gap = {}   # gap bucket → second leg rate

    # Partial exit on daily data: exit half at HOD proxy (open + 60% of day range), hold rest
    partial_daily_returns = []
    current_daily_returns = []

    downloaded = 0
    failed     = 0

    for i, sym in enumerate(UNIVERSE, 1):
        try:
            raw = yf.Ticker(sym).history(period='5y', interval='1d', auto_adjust=True)
        except Exception:
            failed += 1
            continue
        if len(raw) < 60:
            failed += 1
            continue

        raw.index = pd.to_datetime(raw.index)
        if hasattr(raw.index, 'tz') and raw.index.tz is not None:
            raw.index = raw.index.tz_localize(None)

        raw = raw.sort_index()
        raw['prev_close'] = raw['Close'].shift(1)
        raw['avg_vol']    = raw['Volume'].rolling(20, min_periods=5).mean().shift(1)
        raw = raw.dropna(subset=['prev_close', 'avg_vol'])

        print(f"  [{i:3d}/{len(UNIVERSE)}] {sym:8s} → {len(raw)} days", end='\r')
        downloaded += 1

        for idx, row in raw.iterrows():
            op   = float(row['Open'])
            hi   = float(row['High'])
            lo   = float(row['Low'])
            cl   = float(row['Close'])
            vol  = float(row['Volume'])
            pc   = float(row['prev_close'])
            avgv = float(row['avg_vol'])

            if pc <= 0 or op <= 0 or avgv <= 0:
                continue

            gap_pct    = (op - pc) / pc * 100
            day_ret    = (cl - op)  / op  * 100
            hod_ret    = (hi - op)  / op  * 100
            vol_ratio  = vol / avgv

            # Signal: gap >1% AND elevated volume (both observable at/before open).
            # Do NOT include close>open — that is the outcome, not the predictor.
            is_velocity = (gap_pct > 1.0 and vol_ratio > 1.3)

            # Stop: if day low goes below open * 0.95, cap loss at -5%
            stopped_ret = max(day_ret, -STOP_PCT * 100) if (lo < op * (1 - STOP_PCT)) else day_ret

            # Partial exit proxy: exit 60% at 60% of the day's high-to-open range (rough HOD proxy),
            # hold 40% to close. This approximates exiting early + letting rest run.
            partial_ret = 0.60 * (hod_ret * 0.60) + 0.40 * stopped_ret

            if is_velocity:
                gap_momentum_days.append(stopped_ret)
                partial_daily_returns.append(partial_ret)
                current_daily_returns.append(stopped_ret)

                # Gap bucket
                gap_bucket = f"{int(gap_pct)}%+" if gap_pct < 10 else "10%+"
                if gap_bucket not in second_leg_rate_by_gap:
                    second_leg_rate_by_gap[gap_bucket] = {
                        'total': 0, 'held': 0, 'reversed': 0
                    }
                second_leg_rate_by_gap[gap_bucket]['total'] += 1
                # "Held" = close within 85% of HOD (trend intact at close)
                # "Reversed" = gap-and-crap (close < open)
                if cl >= hi * 0.85:
                    second_leg_rate_by_gap[gap_bucket]['held'] += 1
                if cl < op:
                    second_leg_rate_by_gap[gap_bucket]['reversed'] += 1
            else:
                flat_open_days.append(stopped_ret)

    print()  # clear \r line

    s_vel  = stats(gap_momentum_days)
    s_flat = stats(flat_open_days)
    s_curr = stats(current_daily_returns)
    s_part = stats(partial_daily_returns)
    lift   = s_vel['avg'] - s_flat['avg']

    print(f"\n  Velocity days (gap>1%, vol>1.3x) vs flat/no-gap days:")
    print_stats("  VELOCITY days (gap>1% + vol spike)",   s_vel)
    print_stats("  FLAT / no-gap days",                    s_flat)
    print(f"    → Velocity day lift: {lift:+.3f}%  "
          f"(best: {s_vel['best']:+.1f}%  worst: {s_vel['worst']:+.1f}%  "
          f"WR: {s_vel['wr']:.0f}%)")

    print(f"\n  Partial exit simulation on velocity days:")
    print_stats("  Current  (all-in → close)",                    s_curr)
    print_stats("  Partial  (60% at ~60%×HOD proxy, 40% close)", s_part)
    partial_lift = s_part['avg'] - s_curr['avg']
    print(f"    → Partial exit lift: {partial_lift:+.3f}%")

    # Second leg by gap bucket
    print(f"\n  Gap day outcome by gap size:")
    print(f"    {'Gap':6s}  {'Held%':6s}  {'Rev%':6s}  {'n':>5s}")
    for bucket in sorted(second_leg_rate_by_gap.keys()):
        d    = second_leg_rate_by_gap[bucket]
        held = d['held']     / d['total'] * 100 if d['total'] else 0
        rev  = d['reversed'] / d['total'] * 100 if d['total'] else 0
        print(f"    {bucket:6s}  {held:5.0f}%  {rev:5.0f}%  {d['total']:5d}")
    print("    Held% = close within 85% of HOD (trend intact).  Rev% = gap-and-crap.")

    print(f"\n  LAYER C VERDICT:")
    if lift >= 0.30:
        print(f"  ✅ STRONG SIGNAL ({lift:+.3f}%) — velocity/gap days clearly outperform")
    elif lift >= 0.10:
        print(f"  ⚠️  MODERATE SIGNAL ({lift:+.3f}%)")
    else:
        print(f"  ❌ WEAK SIGNAL ({lift:+.3f}%)")

    if partial_lift >= 0.10:
        print(f"  ✅ PARTIAL EXIT ADDS VALUE ({partial_lift:+.3f}% per trade on velocity days)")
    else:
        print(f"  ➡️  Partial exit marginal on daily scale ({partial_lift:+.3f}%)")

    return downloaded, failed

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    layer = None
    if '--layer' in sys.argv:
        idx   = sys.argv.index('--layer')
        layer = sys.argv[idx + 1].upper()

    print("\n" + "█"*70)
    print("  EARLY MOVER BACKTEST — 3-LAYER ANALYSIS")
    print(f"  Universe: {len(UNIVERSE)} symbols")
    print(f"  Date: {date.today()}")
    print("█"*70)

    if layer is None or layer == 'A':
        run_layer_a()
    if layer is None or layer == 'B':
        run_layer_b()
    if layer is None or layer == 'C':
        run_layer_c()

    print("\n" + "█"*70)
    print("  ALL LAYERS COMPLETE")
    print("  Next steps:")
    print("    - If A+B show consistent lift → build velocity gate in sim_today.py")
    print("    - If partial exit shows lift  → build partial_exit flag per trade")
    print("    - Run again in 4 weeks with accumulated sim_today velocity logs")
    print("█"*70)

if __name__ == '__main__':
    main()
