# strategy_router.py — Per-Stock Strategy Assignment Engine
# Decides which strategy to apply to each stock each day
# Strategies: FVG_FILL, TREND_PULLBACK, SBS_BREAKOUT, GAP_GO, GAP_FILL, SHORT_FADE
# Called by screener.py before sending WhatsApp picks
# Command: python strategy_router.py ORCL (to test standalone)

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import pytz
import sqlite3
import os

from regime_detector import get_regime
from fvg_detector import analyse_symbol, calculate_dynamic_sl_target

ET      = pytz.timezone('America/New_York')
DB_PATH = os.path.join(os.path.dirname(__file__), 'trades.db')

# ── Strategy definitions ──────────────────────────────────
STRATEGIES = {
    'FVG_FILL': {
        'name':        'Fair Value Gap Fill',
        'description': 'Price left a gap — waiting to fill it. High probability.',
        'bias':        'LONG',
        'best_on':     ['ORCL', 'MSFT', 'AAPL', 'AMZN', 'JPM', 'GS'],
        'requires':    ['fvg', 'volume'],
        'min_regime':  ['STRONG', 'NORMAL'],
    },
    'TREND_PULLBACK': {
        'name':        'Trend Pullback Entry',
        'description': 'Strong trend with pullback to EMA — momentum resumes.',
        'bias':        'LONG',
        'best_on':     ['NVDA', 'AMD', 'META', 'TSLA', 'PLTR', 'HOOD'],
        'requires':    ['trend', 'ema_touch', 'volume'],
        'min_regime':  ['STRONG', 'NORMAL'],
    },
    'SBS_BREAKOUT': {
        'name':        'Support/Breakout Buy',
        'description': 'Stock coiling at key level, breakout imminent.',
        'bias':        'LONG',
        'best_on':     ['TOST', 'RKLB', 'NBIS', 'COHR', 'LITE', 'CLS'],
        'requires':    ['sr_level', 'volume', 'tight_range'],
        'min_regime':  ['STRONG', 'NORMAL', 'CAUTIOUS'],
    },
    'GAP_GO': {
        'name':        'Gap and Go',
        'description': 'Catalyst gap up with volume — ride the momentum.',
        'bias':        'LONG',
        'best_on':     ['RKLB', 'MSTR', 'HOOD', 'SOFI', 'NUTX'],
        'requires':    ['catalyst', 'gap_up', 'volume'],
        'min_regime':  ['STRONG', 'NORMAL'],
    },
    'GAP_FILL': {
        'name':        'Gap Fill (Fade)',
        'description': 'Overextended gap up on weak market — fade back to close.',
        'bias':        'SHORT',
        'best_on':     ['TSLA', 'NVDA', 'AMD', 'MSTR'],
        'requires':    ['gap_up', 'weak_market', 'overbought'],
        'min_regime':  ['CAUTIOUS', 'WEAK'],
    },
    'SHORT_FADE': {
        'name':        'Short / Fade the Rip',
        'description': 'Weak market day, stock overbought — short for mean reversion.',
        'bias':        'SHORT',
        'best_on':     ['TSLA', 'NVDA', 'AMD', 'SMCI', 'MSTR'],
        'requires':    ['weak_market', 'overbought', 'volume'],
        'min_regime':  ['WEAK'],
    },
    'MOMENTUM': {
        'name':        'Momentum Long (Standard)',
        'description': 'Strong RSI + volume + trend alignment.',
        'bias':        'LONG',
        'best_on':     [],   # fallback for any stock
        'requires':    ['volume', 'momentum'],
        'min_regime':  ['STRONG', 'NORMAL'],
    }
}


def get_stock_profile(symbol):
    """
    Get historical strategy performance for this stock from DB.
    Returns dict of {strategy_name: win_rate}
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c    = conn.cursor()
        c.execute('''
            SELECT setup_type, COUNT(*) as total,
                   SUM(CASE WHEN status="WIN" THEN 1 ELSE 0 END) as wins,
                   AVG(pnl) as avg_pnl
            FROM trades
            WHERE symbol=? AND status IN ("WIN","LOSS")
            GROUP BY setup_type
            HAVING total >= 2
        ''', (symbol,))
        rows = c.fetchall()
        conn.close()
        return {row[0]: {'win_rate': row[2]/row[1]*100, 'trades': row[1], 'avg_pnl': row[3]}
                for row in rows}
    except:
        return {}


def check_ema_alignment(symbol):
    """Check if stock is in uptrend with EMA alignment"""
    try:
        ticker = yf.Ticker(symbol)
        df     = ticker.history(period='3mo', interval='1d')
        if df.empty or len(df) < 50:
            return False, False, 0

        close = df['Close']
        ema8  = close.ewm(span=8).mean().iloc[-1]
        ema21 = close.ewm(span=21).mean().iloc[-1]
        ema50 = close.ewm(span=50).mean().iloc[-1]
        current = close.iloc[-1]

        # Strong uptrend: price > ema8 > ema21 > ema50
        is_uptrend  = current > ema8 > ema21 > ema50
        # Pullback to ema8 or ema21
        near_ema8   = abs(current - ema8) / current * 100 < 1.5
        near_ema21  = abs(current - ema21) / current * 100 < 2.0
        ema_touch   = near_ema8 or near_ema21

        rsi_ind = None
        try:
            import ta
            rsi_series = ta.momentum.RSIIndicator(close, window=14).rsi()
            rsi_ind    = rsi_series.iloc[-1]
        except:
            pass

        return is_uptrend, ema_touch, rsi_ind or 50
    except:
        return False, False, 50


def check_tight_range(symbol):
    """Check if stock is coiling in a tight range (SBS setup)"""
    try:
        ticker = yf.Ticker(symbol)
        df     = ticker.history(period='5d', interval='1d')
        if df.empty or len(df) < 3:
            return False, 0

        # Range of last 3 days
        recent_high = df['High'].iloc[-3:].max()
        recent_low  = df['Low'].iloc[-3:].min()
        range_pct   = (recent_high - recent_low) / recent_low * 100
        is_tight    = range_pct < 5  # less than 5% range = coiling
        return is_tight, round(range_pct, 2)
    except:
        return False, 0


def check_gap_up(symbol):
    """Check if stock gapped up today"""
    try:
        ticker = yf.Ticker(symbol)
        df     = ticker.history(period='2d', interval='1d')
        if len(df) < 2:
            return False, 0
        prev_close = df['Close'].iloc[-2]
        today_open = df['Open'].iloc[-1]
        gap_pct    = (today_open - prev_close) / prev_close * 100
        return gap_pct >= 2.0, round(gap_pct, 2)
    except:
        return False, 0


def check_overbought(symbol):
    """Check if stock is overbought (short candidate)"""
    try:
        import ta
        ticker = yf.Ticker(symbol)
        df     = ticker.history(period='3mo', interval='1d')
        if df.empty or len(df) < 14:
            return False, 50

        close = df['Close']
        rsi   = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]
        change_1d = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100
        is_overbought = rsi > 70 or (rsi > 65 and change_1d > 3)
        return is_overbought, round(rsi, 1)
    except:
        return False, 50


def route_strategy(symbol, regime, fvg_analysis=None,
                   catalyst=None, volume_ratio=1.0):
    """
    Main routing function — assigns best strategy for this stock today.

    Returns:
        {
            'strategy':    str (strategy key),
            'name':        str,
            'description': str,
            'bias':        'LONG' | 'SHORT',
            'confidence':  int (0-100),
            'signals':     dict,
            'reason':      str
        }
    """
    # Get historical performance on this stock
    profile = get_stock_profile(symbol)

    # Get technical data
    is_uptrend, ema_touch, rsi = check_ema_alignment(symbol)
    is_tight, range_pct        = check_tight_range(symbol)
    is_gap_up, gap_pct         = check_gap_up(symbol)
    is_overbought, rsi_val     = check_overbought(symbol)

    has_fvg     = fvg_analysis and fvg_analysis.get('has_fvg', False)
    has_catalyst = catalyst is not None and catalyst.get('type') is not None

    regime_name = regime.get('regime', 'NORMAL')
    is_weak     = regime_name in ['WEAK', 'CHOPPY']
    is_strong   = regime_name in ['STRONG', 'NORMAL']

    signals = {
        'is_uptrend':    is_uptrend,
        'ema_touch':     ema_touch,
        'rsi':           rsi_val,
        'is_tight':      is_tight,
        'range_pct':     range_pct,
        'is_gap_up':     is_gap_up,
        'gap_pct':       gap_pct,
        'is_overbought': is_overbought,
        'has_fvg':       has_fvg,
        'has_catalyst':  has_catalyst,
        'volume_ratio':  volume_ratio,
        'regime':        regime_name,
    }

    scored = []

    # ── Score each strategy ───────────────────────────────
    fvg_count = fvg_analysis.get('fvg_count', 0) if fvg_analysis else 0

    # 1. FVG_FILL — lower volume threshold, works even on CHOPPY for next-day setups
    if has_fvg and fvg_count >= 1 and volume_ratio >= 1.0:
        score = 85
        if fvg_count >= 5:
            score += 8   # many gaps = strong magnet
        if symbol in STRATEGIES['FVG_FILL']['best_on']:
            score += 10
        if profile.get('FVG_FILL', {}).get('win_rate', 0) > 60:
            score += 5
        # Reduce score on choppy days (setup valid but don't trade yet)
        if regime_name == 'CHOPPY':
            score -= 20
        scored.append(('FVG_FILL', min(100, score), f'Unfilled FVGs: {fvg_count} gaps below price'))

    # 2. TREND_PULLBACK
    if is_uptrend and ema_touch and is_strong and volume_ratio >= 1.3:
        score = 80
        if symbol in STRATEGIES['TREND_PULLBACK']['best_on']:
            score += 10
        if profile.get('TREND_PULLBACK', {}).get('win_rate', 0) > 60:
            score += 5
        scored.append(('TREND_PULLBACK', min(100, score), f'Uptrend pullback to EMA, RSI {rsi_val:.0f}'))

    # 3. GAP_GO (catalyst gap up on strong market)
    if has_catalyst and is_gap_up and is_strong and volume_ratio >= 2.0:
        score = 88
        if symbol in STRATEGIES['GAP_GO']['best_on']:
            score += 7
        scored.append(('GAP_GO', min(100, score), f'Catalyst gap up {gap_pct:.1f}% with {volume_ratio:.1f}x volume'))

    # 4. SBS_BREAKOUT (tight coil near S/R)
    if is_tight and volume_ratio >= 1.5 and not is_weak:
        score = 75
        if symbol in STRATEGIES['SBS_BREAKOUT']['best_on']:
            score += 10
        if profile.get('SBS_BREAKOUT', {}).get('win_rate', 0) > 60:
            score += 5
        scored.append(('SBS_BREAKOUT', min(100, score), f'Tight range {range_pct:.1f}% — breakout building'))

    # 5. SHORT_FADE / GAP_FILL (weak market)
    if is_weak and is_overbought and volume_ratio >= 1.5:
        score = 78
        strategy = 'GAP_FILL' if is_gap_up else 'SHORT_FADE'
        reason   = f'Weak market + overbought RSI {rsi_val:.0f}'
        if symbol in STRATEGIES[strategy]['best_on']:
            score += 10
        scored.append((strategy, min(100, score), reason))

    # 6. Fallback: MOMENTUM
    if not scored:
        score = 60
        scored.append(('MOMENTUM', score, 'Standard momentum long setup'))

    # ── Sort by score, prefer historically proven strategy ──
    scored.sort(key=lambda x: -x[1])

    # If top strategy has bad history on this stock, try second
    best_strategy, best_score, best_reason = scored[0]
    if len(scored) > 1:
        hist = profile.get(best_strategy, {})
        if hist.get('trades', 0) >= 3 and hist.get('win_rate', 100) < 40:
            # This strategy has been losing on this stock — try next
            best_strategy, best_score, best_reason = scored[1]
            best_reason += ' (switched: history poor on top pick)'

    strategy_info = STRATEGIES[best_strategy]

    return {
        'strategy':    best_strategy,
        'name':        strategy_info['name'],
        'description': strategy_info['description'],
        'bias':        strategy_info['bias'],
        'confidence':  best_score,
        'signals':     signals,
        'reason':      best_reason,
        'all_scored':  scored,
        'history':     profile.get(best_strategy, {}),
    }


def get_full_trade_setup(symbol, regime=None, catalyst=None, volume_ratio=1.0):
    """
    Full pipeline: regime → FVG analysis → strategy route → dynamic SL/target.
    One call gives you everything needed to place a trade.
    """
    if regime is None:
        regime = get_regime()

    # FVG + S/R analysis
    fvg_data = analyse_symbol(symbol)

    # Strategy routing
    routing  = route_strategy(symbol, regime, fvg_data, catalyst, volume_ratio)

    # Current price
    try:
        ticker  = yf.Ticker(symbol)
        df      = ticker.history(period='1d', interval='5m')
        current = round(df['Close'].iloc[-1], 2) if not df.empty else 0
    except:
        current = 0

    # Dynamic SL + target
    side = routing['bias']
    plan = calculate_dynamic_sl_target(
        symbol, current, side,
        fvg_data['supports'],
        fvg_data['resistances'],
        fvg_data.get('open_fvgs_15m', [])
    )

    return {
        'symbol':        symbol,
        'current_price': current,
        'regime':        regime['regime'],
        'strategy':      routing['strategy'],
        'strategy_name': routing['name'],
        'bias':          routing['bias'],
        'confidence':    routing['confidence'],
        'reason':        routing['reason'],
        'stop_loss':     plan['stop_loss'],
        'target':        plan['target'],
        'risk_pct':      plan['risk_pct'],
        'reward_pct':    plan['reward_pct'],
        'rr_ratio':      plan['rr_ratio'],
        'sl_reason':     plan['sl_reason'],
        'target_reason': plan['target_reason'],
        'trade_valid':   plan['valid'],
        'signals':       routing['signals'],
        'history':       routing['history'],
        'fvg_count':     fvg_data['fvg_count'],
        'supports':      fvg_data['supports'][:2],
        'resistances':   fvg_data['resistances'][:2],
    }


if __name__ == '__main__':
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'ORCL'

    print(f"\n=== Strategy Router: {symbol} ===")
    regime = get_regime()
    print(f"\nMarket Regime: {regime['regime']} | SPY {regime['spy_change']:+.1f}%")

    setup = get_full_trade_setup(symbol, regime)

    print(f"\n{'='*45}")
    print(f"STRATEGY:    {setup['strategy']} — {setup['strategy_name']}")
    print(f"BIAS:        {setup['bias']}")
    print(f"CONFIDENCE:  {setup['confidence']}/100")
    print(f"REASON:      {setup['reason']}")
    print(f"\nPrice:       ${setup['current_price']}")
    print(f"Stop Loss:   ${setup['stop_loss']} ({setup['sl_reason']})")
    print(f"Target:      ${setup['target']} ({setup['target_reason']})")
    print(f"Risk:        {setup['risk_pct']:.2f}%")
    print(f"Reward:      {setup['reward_pct']:.2f}%")
    print(f"R:R:         1:{setup['rr_ratio']:.1f}")
    print(f"Valid:       {'✅' if setup['trade_valid'] else '❌'}")
    if setup['history']:
        h = setup['history']
        print(f"\nHistory on this stock+strategy:")
        print(f"  Win rate: {h.get('win_rate', 0):.0f}% | Trades: {h.get('trades', 0)}")
