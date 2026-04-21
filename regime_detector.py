# regime_detector.py — Market Regime Detection Engine
# Detects: STRONG / NORMAL / CAUTIOUS / CHOPPY / WEAK
# Uses SPY price action + VIX + breadth indicators
# Called by screener.py, trader.py, strategy_router.py
# Command: python regime_detector.py (to test standalone)

import yfinance as yf
import pandas as pd
from datetime import datetime
import pytz

ET = pytz.timezone('America/New_York')

# ── Regime thresholds ─────────────────────────────────────
VIX_CALM      = 15   # Below this = calm market
VIX_NORMAL    = 20   # Below this = normal
VIX_CAUTIOUS  = 25   # Below this = cautious
VIX_FEAR      = 30   # Above this = fear/weak

SPY_STRONG    =  0.5   # SPY up 0.5%+ = strong
SPY_WEAK      = -0.5   # SPY down 0.5%+ = weak
SPY_CHOPPY    =  0.3   # SPY within ±0.3% = choppy range

def get_spy_data():
    """Get SPY intraday and daily data"""
    try:
        spy = yf.Ticker('SPY')
        # Intraday for today's move
        intraday = spy.history(period='1d', interval='5m')
        # Daily for trend
        daily    = spy.history(period='10d')
        return intraday, daily
    except Exception as e:
        print(f"[Regime] SPY data error: {e}")
        return None, None

def get_vix():
    """Get current VIX level"""
    try:
        vix    = yf.Ticker('^VIX')
        data   = vix.history(period='2d')
        if data.empty:
            return 18.0  # default neutral
        current = data['Close'].iloc[-1]
        prev    = data['Close'].iloc[-2] if len(data) > 1 else current
        return round(current, 2), round(prev, 2)
    except Exception as e:
        print(f"[Regime] VIX error: {e}")
        return 18.0, 18.0

def get_spy_change():
    """Get SPY change % today vs previous close"""
    try:
        spy    = yf.Ticker('SPY')
        data   = spy.history(period='2d')
        if len(data) < 2:
            return 0.0
        today  = data['Close'].iloc[-1]
        prev   = data['Close'].iloc[-2]
        return round(((today - prev) / prev) * 100, 2)
    except:
        return 0.0

def get_spy_trend(daily):
    """Is SPY trending up, down, or sideways over past 5 days?"""
    if daily is None or len(daily) < 5:
        return 'NEUTRAL'
    close  = daily['Close']
    ma5    = close.rolling(5).mean().iloc[-1]
    ma10   = close.rolling(10).mean().iloc[-1] if len(close) >= 10 else ma5
    current = close.iloc[-1]

    if current > ma5 > ma10:
        return 'UPTREND'
    elif current < ma5 < ma10:
        return 'DOWNTREND'
    else:
        return 'SIDEWAYS'

def detect_choppy(intraday):
    """Detect if market is choppy — whipsawing without direction"""
    if intraday is None or len(intraday) < 6:
        return False
    close  = intraday['Close']
    # Count direction changes
    diffs  = close.diff().dropna()
    changes = 0
    for i in range(1, len(diffs)):
        if diffs.iloc[i] * diffs.iloc[i-1] < 0:  # Sign change
            changes += 1
    # If direction changes more than 40% of bars = choppy
    chop_ratio = changes / len(diffs)
    net_move   = abs((close.iloc[-1] - close.iloc[0]) / close.iloc[0] * 100)
    return chop_ratio > 0.4 and net_move < 0.3

def get_regime():
    """
    Main function — returns full regime dict.

    Returns:
        {
            'regime':      'STRONG' | 'NORMAL' | 'CAUTIOUS' | 'CHOPPY' | 'WEAK',
            'spy_change':  float (% today),
            'vix':         float,
            'vix_prev':    float,
            'vix_rising':  bool,
            'spy_trend':   'UPTREND' | 'DOWNTREND' | 'SIDEWAYS',
            'is_choppy':   bool,
            'trade_bias':  'LONG' | 'SHORT' | 'NONE',
            'max_trades':  int,
            'confidence_boost': int,   # add to confidence scores
            'description': str
        }
    """
    print("[Regime] Fetching market data...")

    spy_change          = get_spy_change()
    vix, vix_prev       = get_vix() if isinstance(get_vix(), tuple) else (get_vix(), get_vix())
    intraday, daily     = get_spy_data()
    spy_trend           = get_spy_trend(daily)
    is_choppy           = detect_choppy(intraday)
    vix_rising          = vix > vix_prev * 1.05  # VIX up 5%+ from yesterday

    # ── Regime classification ─────────────────────────────
    regime = 'NORMAL'  # default

    if is_choppy and abs(spy_change) < SPY_CHOPPY:
        regime = 'CHOPPY'

    elif vix > VIX_FEAR or spy_change < -1.5:
        regime = 'WEAK'

    elif spy_change >= SPY_STRONG and vix < VIX_NORMAL and not vix_rising:
        if spy_trend == 'UPTREND':
            regime = 'STRONG'
        else:
            regime = 'NORMAL'

    elif spy_change < SPY_WEAK or vix > VIX_CAUTIOUS or vix_rising:
        regime = 'CAUTIOUS'

    elif spy_change >= 0 and vix < VIX_NORMAL:
        regime = 'NORMAL'

    # ── Regime properties ─────────────────────────────────
    properties = {
        'STRONG': {
            'trade_bias':       'LONG',
            'max_trades':       3,
            'confidence_boost': 10,
            'description':      f'Strong bull day. SPY +{spy_change:.1f}%, VIX {vix:.1f} calm. Ride momentum!'
        },
        'NORMAL': {
            'trade_bias':       'LONG',
            'max_trades':       2,
            'confidence_boost': 0,
            'description':      f'Normal market. SPY {spy_change:+.1f}%, VIX {vix:.1f}. Trade selectively.'
        },
        'CAUTIOUS': {
            'trade_bias':       'LONG',
            'max_trades':       1,
            'confidence_boost': -10,
            'description':      f'Cautious day. SPY {spy_change:+.1f}%, VIX {vix:.1f} elevated. One trade max.'
        },
        'CHOPPY': {
            'trade_bias':       'NONE',
            'max_trades':       0,
            'confidence_boost': -20,
            'description':      f'Choppy market. No clear direction. Dock the boat. Wait tomorrow.'
        },
        'WEAK': {
            'trade_bias':       'SHORT',
            'max_trades':       1,
            'confidence_boost': -20,
            'description':      f'Weak/bear day. SPY {spy_change:+.1f}%, VIX {vix:.1f} fear. Short bias or sit out.'
        }
    }

    result = {
        'regime':           regime,
        'spy_change':       spy_change,
        'vix':              vix,
        'vix_prev':         vix_prev,
        'vix_rising':       vix_rising,
        'spy_trend':        spy_trend,
        'is_choppy':        is_choppy,
        **properties[regime]
    }

    print(f"[Regime] {regime} | SPY {spy_change:+.1f}% | VIX {vix:.1f} "
          f"({'rising' if vix_rising else 'stable'}) | Trend: {spy_trend} | "
          f"Choppy: {is_choppy} | Bias: {result['trade_bias']}")

    return result


def regime_summary(r):
    """One-line summary for WhatsApp messages"""
    icons = {
        'STRONG':   '🟢',
        'NORMAL':   '🔵',
        'CAUTIOUS': '🟡',
        'CHOPPY':   '🟠',
        'WEAK':     '🔴'
    }
    icon = icons.get(r['regime'], '⚪')
    return (
        f"{icon} Market: {r['regime']}\n"
        f"SPY: {r['spy_change']:+.1f}% | VIX: {r['vix']:.1f} | Trend: {r['spy_trend']}\n"
        f"{r['description']}"
    )


if __name__ == '__main__':
    print("\n=== Market Regime Detector ===")
    regime = get_regime()
    print(f"\n{'='*40}")
    print(f"REGIME:    {regime['regime']}")
    print(f"SPY:       {regime['spy_change']:+.1f}%")
    print(f"VIX:       {regime['vix']:.1f} (prev: {regime['vix_prev']:.1f})")
    print(f"VIX trend: {'RISING ⚠️' if regime['vix_rising'] else 'Stable ✅'}")
    print(f"SPY trend: {regime['spy_trend']}")
    print(f"Choppy:    {regime['is_choppy']}")
    print(f"Bias:      {regime['trade_bias']}")
    print(f"Max trades:{regime['max_trades']}")
    print(f"Conf boost:{regime['confidence_boost']:+d}")
    print(f"\n{regime['description']}")
