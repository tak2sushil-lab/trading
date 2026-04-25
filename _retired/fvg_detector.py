# fvg_detector.py — Fair Value Gap + Support/Resistance Engine
# Detects FVGs on 15min and 1hr charts
# Calculates key S/R levels for dynamic SL/Target placement
# Called by strategy_router.py
# Command: python fvg_detector.py ORCL (to test standalone)

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz

ET = pytz.timezone('America/New_York')

# ── Config ────────────────────────────────────────────────
FVG_MIN_SIZE_PCT  = 0.15   # Minimum gap size (% of price) to qualify as FVG
SR_LOOKBACK_DAYS  = 30     # Days to look back for S/R levels
SR_ZONE_TOLERANCE = 0.3    # % tolerance for S/R zone clustering
MAX_SR_LEVELS     = 5      # Max S/R levels to return


def get_ohlcv(symbol, interval='15m', period='5d'):
    """Fetch OHLCV data for given interval"""
    try:
        ticker = yf.Ticker(symbol)
        df     = ticker.history(period=period, interval=interval)
        if df.empty or len(df) < 10:
            return None
        df.index = df.index.tz_convert(ET)
        return df
    except Exception as e:
        print(f"  [FVG] {symbol} data error ({interval}): {e}")
        return None


def detect_fvg(df, symbol, timeframe='15m'):
    """
    Detect Fair Value Gaps in OHLCV data.
    A bullish FVG = candle[i-1].high < candle[i+1].low (gap up — price left a hole)
    A bearish FVG = candle[i-1].low > candle[i+1].high (gap down)

    Returns list of FVG dicts sorted by recency.
    """
    fvgs = []

    if df is None or len(df) < 3:
        return fvgs

    high  = df['High'].values
    low   = df['Low'].values
    close = df['Close'].values
    times = df.index

    current_price = close[-1]

    for i in range(1, len(df) - 1):
        # ── Bullish FVG ───────────────────────────────────
        # Gap between candle[i-1] high and candle[i+1] low
        if high[i-1] < low[i+1]:
            gap_bottom = high[i-1]
            gap_top    = low[i+1]
            gap_size   = gap_top - gap_bottom
            gap_pct    = (gap_size / close[i]) * 100

            if gap_pct >= FVG_MIN_SIZE_PCT:
                # Check if gap is still open (price hasn't filled it)
                gap_filled = any(low[j] < gap_bottom for j in range(i+1, len(df)))
                recency    = len(df) - i  # lower = more recent

                fvgs.append({
                    'type':        'BULLISH',
                    'top':         round(gap_top, 2),
                    'bottom':      round(gap_bottom, 2),
                    'midpoint':    round((gap_top + gap_bottom) / 2, 2),
                    'size_pct':    round(gap_pct, 2),
                    'time':        times[i].strftime('%Y-%m-%d %H:%M'),
                    'filled':      gap_filled,
                    'recency':     recency,
                    'timeframe':   timeframe,
                    'actionable':  not gap_filled and current_price > gap_bottom,
                })

        # ── Bearish FVG ───────────────────────────────────
        elif low[i-1] > high[i+1]:
            gap_bottom = high[i+1]
            gap_top    = low[i-1]
            gap_size   = gap_top - gap_bottom
            gap_pct    = (gap_size / close[i]) * 100

            if gap_pct >= FVG_MIN_SIZE_PCT:
                gap_filled = any(high[j] > gap_top for j in range(i+1, len(df)))
                recency    = len(df) - i

                fvgs.append({
                    'type':        'BEARISH',
                    'top':         round(gap_top, 2),
                    'bottom':      round(gap_bottom, 2),
                    'midpoint':    round((gap_top + gap_bottom) / 2, 2),
                    'size_pct':    round(gap_pct, 2),
                    'time':        times[i].strftime('%Y-%m-%d %H:%M'),
                    'filled':      gap_filled,
                    'recency':     recency,
                    'timeframe':   timeframe,
                    'actionable':  not gap_filled and current_price < gap_top,
                })

    # Sort by recency (most recent first), unfilled first
    fvgs.sort(key=lambda x: (x['filled'], x['recency']))
    return fvgs


def detect_support_resistance(symbol):
    """
    Calculate key S/R levels using:
    - Recent highs and lows (swing points)
    - High-volume price nodes
    - Round number levels
    """
    try:
        ticker = yf.Ticker(symbol)
        df     = ticker.history(period=f'{SR_LOOKBACK_DAYS}d', interval='1d')

        if df.empty or len(df) < 5:
            return [], []

        high  = df['High'].values
        low   = df['Low'].values
        close = df['Close'].values
        vol   = df['Volume'].values

        current_price = close[-1]
        levels = []

        # ── Swing highs ───────────────────────────────────
        for i in range(2, len(df) - 2):
            if high[i] > high[i-1] and high[i] > high[i-2] and \
               high[i] > high[i+1] and high[i] > high[i+2]:
                levels.append({
                    'price':    round(high[i], 2),
                    'type':     'RESISTANCE',
                    'strength': min(5, int(vol[i] / np.mean(vol) * 2)),
                })

        # ── Swing lows ────────────────────────────────────
        for i in range(2, len(df) - 2):
            if low[i] < low[i-1] and low[i] < low[i-2] and \
               low[i] < low[i+1] and low[i] < low[i+2]:
                levels.append({
                    'price':    round(low[i], 2),
                    'type':     'SUPPORT',
                    'strength': min(5, int(vol[i] / np.mean(vol) * 2)),
                })

        # ── Cluster nearby levels ─────────────────────────
        clustered = []
        used = set()
        levels.sort(key=lambda x: x['price'])

        for i, level in enumerate(levels):
            if i in used:
                continue
            cluster = [level]
            for j, other in enumerate(levels):
                if j != i and j not in used:
                    pct_diff = abs(level['price'] - other['price']) / level['price'] * 100
                    if pct_diff <= SR_ZONE_TOLERANCE:
                        cluster.append(other)
                        used.add(j)
            avg_price  = round(np.mean([c['price'] for c in cluster]), 2)
            avg_str    = round(np.mean([c['strength'] for c in cluster]), 1)
            level_type = cluster[0]['type']
            clustered.append({
                'price':    avg_price,
                'type':     level_type,
                'strength': avg_str,
                'count':    len(cluster),
            })
            used.add(i)

        # ── Separate supports and resistances ─────────────
        supports    = sorted(
            [l for l in clustered if l['price'] < current_price],
            key=lambda x: -x['price']   # closest support first
        )[:MAX_SR_LEVELS]

        resistances = sorted(
            [l for l in clustered if l['price'] > current_price],
            key=lambda x: x['price']    # closest resistance first
        )[:MAX_SR_LEVELS]

        return supports, resistances

    except Exception as e:
        print(f"  [S/R] {symbol} error: {e}")
        return [], []


def calculate_dynamic_sl_target(symbol, entry_price, side='LONG',
                                  supports=None, resistances=None,
                                  fvgs=None, min_rr=3.0):
    """
    Calculate structure-based SL and Target using S/R + FVG levels.
    Ensures R:R >= min_rr (default 1:3).
    Caps: max risk 3% | max target 7% (realistic swing trade bounds)
    """
    MAX_RISK_PCT   = 3.0   # Never risk more than 3% on any trade
    MAX_TARGET_PCT = 9.0   # Allow up to 9% target (supports 1:3 RR at max risk)

    if supports is None:
        supports, resistances = detect_support_resistance(symbol)
    if fvgs is None:
        fvgs_15m = detect_fvg(get_ohlcv(symbol, '15m', '5d'), symbol, '15m')
        fvgs = fvgs_15m

    current = entry_price

    if side == 'LONG':
        # ── Stop Loss: just below nearest support, capped at 3% ──
        sl_price  = None
        sl_reason = 'default 2% SL'

        if supports:
            nearest_sup = supports[0]['price']
            proposed_sl = round(nearest_sup * 0.998, 2)
            raw_risk    = (current - proposed_sl) / current * 100
            if raw_risk <= MAX_RISK_PCT:
                sl_price  = proposed_sl
                sl_reason = f'Below support ${nearest_sup}'
            else:
                # Support too far — use max risk cap instead
                sl_price  = round(current * (1 - MAX_RISK_PCT / 100), 2)
                sl_reason = f'3% cap (support ${nearest_sup} too far)'
        else:
            sl_price  = round(current * 0.97, 2)
            sl_reason = 'Default 3% SL (no support found)'

        # ── Target: nearest FVG top or resistance, capped at 7% ──
        target_price  = None
        target_reason = 'default target'

        # Check FVGs above price
        fvg_targets = [
            f for f in fvgs
            if f['type'] == 'BULLISH' and not f['filled']
            and f['top'] > current
        ]
        if fvg_targets:
            proposed = fvg_targets[0]['top']
            if (proposed - current) / current * 100 <= MAX_TARGET_PCT:
                target_price  = proposed
                target_reason = f'FVG fill ${target_price}'

        # Check resistances
        if resistances:
            proposed_r = round(resistances[0]['price'] * 0.998, 2)
            r_pct      = (proposed_r - current) / current * 100
            if r_pct <= MAX_TARGET_PCT and (target_price is None or proposed_r < target_price):
                target_price  = proposed_r
                target_reason = f'Resistance ${resistances[0]["price"]}'

        risk_pct = round((current - sl_price) / current * 100, 2)

        # Apply 1:3 R:R minimum
        min_target_pct = risk_pct * min_rr
        if target_price is None or (target_price - current) / current * 100 < min_target_pct:
            proposed_target_pct = min(min_target_pct, MAX_TARGET_PCT)
            target_price  = round(current * (1 + proposed_target_pct / 100), 2)
            target_reason = f'1:{min_rr} R:R target (capped at {proposed_target_pct:.1f}%)'

        reward_pct = round((target_price - current) / current * 100, 2)
        rr_ratio   = round(reward_pct / risk_pct, 2) if risk_pct > 0 else 0

        return {
            'stop_loss':     sl_price,
            'target':        target_price,
            'risk_pct':      risk_pct,
            'reward_pct':    reward_pct,
            'rr_ratio':      rr_ratio,
            'sl_reason':     sl_reason,
            'target_reason': target_reason,
            'valid':         rr_ratio >= min_rr and risk_pct > 0 and risk_pct <= MAX_RISK_PCT
        }

    else:  # SHORT
        # SL above nearest resistance, capped at 3%
        if resistances:
            proposed_sl = round(resistances[0]['price'] * 1.002, 2)
            raw_risk    = (proposed_sl - current) / current * 100
            if raw_risk <= MAX_RISK_PCT:
                sl_price  = proposed_sl
                sl_reason = f'Above resistance ${resistances[0]["price"]}'
            else:
                sl_price  = round(current * (1 + MAX_RISK_PCT / 100), 2)
                sl_reason = f'3% cap (resistance too far)'
        else:
            sl_price  = round(current * 1.03, 2)
            sl_reason = 'Default 3% SL'

        # Target: nearest support, capped at 7%
        if supports:
            proposed_t = supports[0]['price']
            t_pct      = (current - proposed_t) / current * 100
            target_price  = proposed_t if t_pct <= MAX_TARGET_PCT else round(current * (1 - MAX_TARGET_PCT/100), 2)
            target_reason = f'Support ${supports[0]["price"]}'
        else:
            target_reason = 'Default target'
            target_price  = round(current * 0.955, 2)

        risk_pct   = round((sl_price - current) / current * 100, 2)
        reward_pct = round((current - target_price) / current * 100, 2)

        min_target_pct = risk_pct * min_rr
        if reward_pct < min_target_pct:
            proposed_target_pct = min(min_target_pct, MAX_TARGET_PCT)
            target_price  = round(current * (1 - proposed_target_pct / 100), 2)
            target_reason = f'1:{min_rr} R:R target'
            reward_pct    = proposed_target_pct

        rr_ratio = round(reward_pct / risk_pct, 2) if risk_pct > 0 else 0

        return {
            'stop_loss':     sl_price,
            'target':        target_price,
            'risk_pct':      risk_pct,
            'reward_pct':    reward_pct,
            'rr_ratio':      rr_ratio,
            'sl_reason':     sl_reason,
            'target_reason': target_reason,
            'valid':         rr_ratio >= min_rr and risk_pct > 0 and risk_pct <= MAX_RISK_PCT
        }


def analyse_symbol(symbol):
    """Full FVG + S/R analysis for one symbol"""
    print(f"\n=== {symbol} Analysis ===")

    # FVGs on 15min and 1hr
    df_15m  = get_ohlcv(symbol, '15m', '5d')
    df_1hr  = get_ohlcv(symbol, '60m', '30d')

    fvgs_15m = detect_fvg(df_15m, symbol, '15m')
    fvgs_1hr = detect_fvg(df_1hr, symbol, '1hr')

    # S/R levels
    supports, resistances = detect_support_resistance(symbol)

    # Current price
    current = df_15m['Close'].iloc[-1] if df_15m is not None else 0

    # Summary
    open_fvgs_15m = [f for f in fvgs_15m if not f['filled'] and f['actionable']]
    open_fvgs_1hr = [f for f in fvgs_1hr if not f['filled'] and f['actionable']]

    return {
        'symbol':         symbol,
        'current_price':  round(current, 2),
        'fvgs_15m':       fvgs_15m,
        'fvgs_1hr':       fvgs_1hr,
        'open_fvgs_15m':  open_fvgs_15m,
        'open_fvgs_1hr':  open_fvgs_1hr,
        'supports':       supports,
        'resistances':    resistances,
        'has_fvg':        len(open_fvgs_15m) > 0 or len(open_fvgs_1hr) > 0,
        'fvg_count':      len(open_fvgs_15m) + len(open_fvgs_1hr),
    }


if __name__ == '__main__':
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'ORCL'

    result = analyse_symbol(symbol)
    price  = result['current_price']

    print(f"\nCurrent Price: ${price}")

    print(f"\nFVGs (15min) — {len(result['open_fvgs_15m'])} open:")
    for f in result['open_fvgs_15m'][:3]:
        print(f"  {f['type']:8s} ${f['bottom']} → ${f['top']} "
              f"({f['size_pct']:.2f}%) @ {f['time']}")

    print(f"\nFVGs (1hr) — {len(result['open_fvgs_1hr'])} open:")
    for f in result['open_fvgs_1hr'][:3]:
        print(f"  {f['type']:8s} ${f['bottom']} → ${f['top']} "
              f"({f['size_pct']:.2f}%) @ {f['time']}")

    print(f"\nSupport levels:")
    for s in result['supports'][:3]:
        print(f"  ${s['price']} (strength: {s['strength']:.1f}, count: {s['count']})")

    print(f"\nResistance levels:")
    for r in result['resistances'][:3]:
        print(f"  ${r['price']} (strength: {r['strength']:.1f}, count: {r['count']})")

    print(f"\nDynamic SL/Target (LONG from ${price}):")
    plan = calculate_dynamic_sl_target(
        symbol, price, 'LONG',
        result['supports'], result['resistances'],
        result['open_fvgs_15m']
    )
    print(f"  Stop Loss:  ${plan['stop_loss']} ({plan['sl_reason']})")
    print(f"  Target:     ${plan['target']} ({plan['target_reason']})")
    print(f"  Risk:       {plan['risk_pct']:.2f}%")
    print(f"  Reward:     {plan['reward_pct']:.2f}%")
    print(f"  R:R ratio:  1:{plan['rr_ratio']:.1f}")
    print(f"  Valid:      {'✅ YES' if plan['valid'] else '❌ NO (skip)'}")
