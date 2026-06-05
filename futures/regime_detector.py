"""
futures/regime_detector.py — Market regime classification for strategy selection.

Reads recent MNQ bar data and classifies the current market regime.
Each regime maps to a recommended strategy preset from futures/strategies/.

Regimes:
  TRENDING      → strong directional momentum, IB breakouts following through
  RETEST_DOM    → large IB, initial breaks failing, retests outperforming
  EXTREME_VOL   → ATR 1.5×+ above median, skip or use xfa_conservative
  MEAN_REVERT   → IB breakouts reversing quickly, low WR (2023-type)
  QUIET         → narrow IB, no clear structure, sit out

Strategy mapping:
  TRENDING    → tc_aggressive (Ferrari — maximize the clean momentum)
  RETEST_DOM  → tc_champion   (Daily Driver — retests handle it, base=2 safe)
  EXTREME_VOL → xfa_conservative (SUV — ATR filter on, reduce exposure)
  MEAN_REVERT → tc_champion   (default safe, maybe sit some days out)
  QUIET       → tc_champion   (fewer trades, use champion to avoid chasing)

Usage:
  python futures/regime_detector.py                  # classify + recommend now
  python futures/regime_detector.py --days 20        # use last 20 trading days
  python futures/regime_detector.py --verbose        # show all metrics
"""

import sys, argparse
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from futures.collect_bars import load_bars, filter_ny_session
from futures.backtest_futures import add_indicators, Config


def detect_regime(lookback_days: int = 20, verbose: bool = False) -> dict:
    """
    Classify the current MNQ market regime using recent bar data.

    Returns dict with keys:
      regime         : str (TRENDING, RETEST_DOM, EXTREME_VOL, MEAN_REVERT, QUIET)
      recommended    : str (strategy name)
      confidence     : str (HIGH, MEDIUM, LOW)
      metrics        : dict of supporting measurements
      reason         : str (human-readable explanation)
    """
    end_date = date.today().isoformat()
    start_date = (date.today() - timedelta(days=lookback_days * 2)).isoformat()  # extra buffer

    df_5m = load_bars(start=start_date, end=end_date, table='futures_bars_5m')
    if df_5m.empty:
        return {'regime': 'UNKNOWN', 'recommended': 'tc_champion',
                'confidence': 'LOW', 'reason': 'No data available.', 'metrics': {}}

    cfg = Config()
    df_5m = add_indicators(df_5m, cfg)
    ny_df = filter_ny_session(df_5m)

    trading_days = sorted(ny_df.index.normalize().unique())
    if len(trading_days) < 5:
        return {'regime': 'UNKNOWN', 'recommended': 'tc_champion',
                'confidence': 'LOW', 'reason': 'Too few trading days.', 'metrics': {}}

    recent_days = trading_days[-lookback_days:]

    # ── Metric 1: Rolling ATR vs 60-day median ───────────────────────────────
    all_days = sorted(ny_df.index.normalize().unique())
    atr_hist = []
    day_atrs = {}
    for d in all_days:
        prior = df_5m[df_5m.index.date < d.date()].tail(20)
        atr_v = float(prior['atr'].iloc[-1]) if not prior.empty else None
        if atr_v:
            atr_hist.append(atr_v)
            if len(atr_hist) >= 20:
                day_atrs[d.date().isoformat()] = {
                    'atr': atr_v,
                    'median_60': float(np.median(atr_hist[-60:])),
                }

    recent_atr_ratios = []
    for d in recent_days:
        d_str = d.date().isoformat()
        if d_str in day_atrs:
            info = day_atrs[d_str]
            recent_atr_ratios.append(info['atr'] / info['median_60'])

    avg_atr_ratio = float(np.mean(recent_atr_ratios)) if recent_atr_ratios else 1.0
    latest_atr_ratio = recent_atr_ratios[-1] if recent_atr_ratios else 1.0

    # ── Metric 2: IB range trend ─────────────────────────────────────────────
    ib_ranges = []
    for d in recent_days:
        day_bars = ny_df[ny_df.index.date == d.date()]
        if len(day_bars) < 4:
            continue
        ib_end = pd.Timestamp(d.date()) + pd.Timedelta(hours=9, minutes=30) + pd.Timedelta(hours=1)
        ib_bars = day_bars[day_bars.index < ib_end.tz_localize(ny_df.index.tz
                           if ny_df.index.tz else 'America/New_York')]
        if len(ib_bars) >= 4:
            ib_ranges.append(float(ib_bars['high'].max()) - float(ib_bars['low'].min()))

    avg_ib = float(np.mean(ib_ranges)) if ib_ranges else 130.0
    # Historical 5yr median is ~132pts. >175 = expanded; <90 = narrow
    ib_expanded = avg_ib > 175
    ib_narrow   = avg_ib < 90

    # ── Metric 3: Recent IB breakout extension rate ───────────────────────────
    # For each day in lookback: did the IB break, and did it extend ≥50pts?
    # Extension rate < 35% = mean-reversion; > 55% = trending
    breakout_days = 0
    extension_days = 0
    for d in recent_days[-10:]:  # last 10 trade days
        day_bars = ny_df[ny_df.index.date == d.date()]
        if len(day_bars) < 8:
            continue
        # Time-based IB: 9:30–10:30 AM ET (60-min IB, consistent with backtest)
        from datetime import time as _time
        ib_bars  = day_bars[day_bars.index.time < _time(10, 30)]
        post_bars = day_bars[day_bars.index.time >= _time(10, 30)]
        if post_bars.empty:
            continue
        ib_h = float(ib_bars['high'].max())
        ib_l = float(ib_bars['low'].min())
        ib_r = ib_h - ib_l
        if ib_r < 50:
            continue
        # Did price break IB?
        max_above = float(post_bars['high'].max()) - ib_h
        max_below = ib_l - float(post_bars['low'].min())
        broke = max_above > 10 or max_below > 10
        if broke:
            breakout_days += 1
            # Did it extend ≥50pts from IB?
            extended = max(max_above, max_below) >= 50
            if extended:
                extension_days += 1

    extension_rate = extension_days / breakout_days if breakout_days > 0 else 0.5

    # ── Metric 4: Daily trend consistency (EMA5 > EMA20) ─────────────────────
    daily_df = load_bars(start=start_date, end=end_date, table='futures_bars_1d')
    consistent_trend = False
    if not daily_df.empty:
        daily_df['ema5']  = daily_df['close'].ewm(span=5,  adjust=False).mean()
        daily_df['ema20'] = daily_df['close'].ewm(span=20, adjust=False).mean()
        recent_daily = daily_df.tail(lookback_days)
        bull_days = (recent_daily['ema5'] > recent_daily['ema20']).sum()
        bear_days = (recent_daily['ema5'] < recent_daily['ema20']).sum()
        consistent_trend = bull_days >= lookback_days * 0.75 or bear_days >= lookback_days * 0.75

    # ── Classify regime ───────────────────────────────────────────────────────
    metrics = {
        'avg_atr_ratio':    round(avg_atr_ratio, 2),
        'latest_atr_ratio': round(latest_atr_ratio, 2),
        'avg_ib_range':     round(avg_ib, 0),
        'ib_expanded':      ib_expanded,
        'ib_narrow':        ib_narrow,
        'extension_rate':   round(extension_rate, 2),
        'breakout_days_of_last10': breakout_days,
        'extension_days_of_last10': extension_days,
        'consistent_daily_trend': consistent_trend,
        'lookback_days': lookback_days,
    }

    if latest_atr_ratio > 1.5:
        regime = 'EXTREME_VOL'
        recommended = 'xfa_conservative'
        confidence = 'HIGH'
        reason = (f'ATR is {latest_atr_ratio:.1f}× the 60-day median. '
                  f'IB targets become unreachable (avg IB {avg_ib:.0f}pts, '
                  f'targets ~{avg_ib*0.75:.0f}pts vs ~80pt actual extension). '
                  f'Use ATR filter config to skip these days.')

    elif ib_expanded and extension_rate < 0.40:
        regime = 'RETEST_DOM'
        recommended = 'tc_champion'
        confidence = 'MEDIUM' if extension_rate < 0.30 else 'HIGH'
        reason = (f'Large IB ({avg_ib:.0f}pts avg) with low extension rate '
                  f'({extension_rate:.0%} of IB breaks extend ≥50pts). '
                  f'Retests are the dominant edge. Champion handles this. '
                  f'Consider tc_aggressive for 3rd trade opportunity on hat-trick retest days.')

    elif extension_rate < 0.30 and not ib_expanded and not consistent_trend:
        regime = 'MEAN_REVERT'
        recommended = 'tc_champion'
        confidence = 'LOW'
        reason = (f'Low extension rate ({extension_rate:.0%}) with narrow IB ({avg_ib:.0f}pts). '
                  f'Mean-reversion regime (2023-type). IB breakouts reversing quickly. '
                  f'Trade fewer days — wait for RVOL ≥1.5× setups only.')

    elif extension_rate > 0.55 and consistent_trend:
        regime = 'TRENDING'
        recommended = 'tc_aggressive'
        confidence = 'HIGH'
        reason = (f'Strong extension rate ({extension_rate:.0%}) with consistent daily trend. '
                  f'IB breakouts following through. Ferrari time. '
                  f'Use tc_aggressive for 3 trades/day at base=3.')

    elif ib_narrow:
        regime = 'QUIET'
        recommended = 'tc_champion'
        confidence = 'MEDIUM'
        reason = (f'Narrow IB ({avg_ib:.0f}pts avg) — low volatility, few qualifying setups. '
                  f'Champion will fire rarely. That is correct. Do not force trades.')

    else:
        regime = 'TRENDING'
        recommended = 'tc_aggressive' if extension_rate > 0.45 and not ib_expanded else 'tc_champion'
        confidence = 'MEDIUM'
        reason = (f'Normal market. Extension rate {extension_rate:.0%}, IB {avg_ib:.0f}pts. '
                  f'{"Use tc_aggressive for faster TC pass." if recommended == "tc_aggressive" else "tc_champion is the safe default."}')

    return {
        'regime':       regime,
        'recommended':  recommended,
        'confidence':   confidence,
        'reason':       reason,
        'metrics':      metrics,
    }


def _print_result(result: dict, verbose: bool = False):
    print()
    regime_icons = {
        'TRENDING':     '🟢',
        'RETEST_DOM':   '🔵',
        'EXTREME_VOL':  '🔴',
        'MEAN_REVERT':  '🟡',
        'QUIET':        '⚪',
        'UNKNOWN':      '❓',
    }
    icon = regime_icons.get(result['regime'], '?')
    print(f'  {icon} REGIME:       {result["regime"]}')
    print(f'  RECOMMENDED:  {result["recommended"]}')
    print(f'  CONFIDENCE:   {result["confidence"]}')
    print(f'  REASON:       {result["reason"]}')
    if verbose and result.get('metrics'):
        m = result['metrics']
        print()
        print('  ── Supporting Metrics ──────────────────────────────────')
        print(f'    ATR ratio (latest):   {m["latest_atr_ratio"]}× median  (> 1.5 = extreme)')
        print(f'    ATR ratio (avg {m["lookback_days"]}d):   {m["avg_atr_ratio"]}× median')
        print(f'    Avg IB range:         {m["avg_ib_range"]:.0f}pts  (median 5yr = 132pts)')
        print(f'    IB expanded (>175):   {m["ib_expanded"]}')
        print(f'    IB narrow (<90):      {m["ib_narrow"]}')
        print(f'    Extension rate:       {m["extension_rate"]:.0%} of IB breaks extend ≥50pts')
        print(f'    Breakout/extend days: {m["breakout_days_of_last10"]}/{m["extension_days_of_last10"]} of last 10')
        print(f'    Consistent trend:     {m["consistent_daily_trend"]}')
    print()
    print(f'  ── How to run the recommended strategy ─────────────────')
    print(f'    venv/bin/python futures/backtest_futures.py --strategy {result["recommended"]} --tc-sim --wfa')
    print()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='MNQ market regime detector')
    parser.add_argument('--days',    type=int, default=20, help='Lookback window (trading days)')
    parser.add_argument('--verbose', action='store_true',  help='Show all metrics')
    args = parser.parse_args()

    print(f'\nDetecting MNQ regime (last {args.days} trading days)...')
    result = detect_regime(lookback_days=args.days, verbose=args.verbose)
    _print_result(result, verbose=args.verbose)
