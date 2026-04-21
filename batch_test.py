# batch_test.py — full universe scanner through strategy router
# Scans FINAL_UNIVERSE + CATALYST_UNIVERSE (76+ stocks)
# Uses previous day data before 11am, today's data after 11am

import yfinance as yf
import sys
import os
sys.path.insert(0, '/home/claude')

# ── Full universe — FINAL + CATALYST combined ─────────────
FINAL_UNIVERSE = [
    'AAPL', 'MSFT', 'AMZN', 'GOOGL', 'META', 'TSLA', 'NVDA', 'AMD',
    'JPM', 'GS', 'MS', 'NFLX', 'UBER', 'AVGO',
    'COHR', 'NUTX', 'HPE', 'TOST', 'HOOD', 'LITE', 'ORCL', 'CLS',
    'RKLB', 'VST', 'PLTR', 'NBIS',
]

CATALYST_UNIVERSE = [
    'NVDA', 'AMD', 'MSFT', 'AAPL', 'AVGO', 'ORCL',
    'PLTR', 'NBIS', 'CRWV', 'SMCI', 'CLS', 'COHR',
    'LITE', 'PANW', 'CRWD', 'SNOW', 'CRM', 'RBRK',
    'PATH', 'AI', 'IONQ', 'QBTS', 'RGTI',
    'HOOD', 'SOFI', 'AFRM', 'TOST', 'NU', 'RKT',
    'ITA', 'RKLB', 'USAR',
    'VST', 'FSLR', 'NEE',
    'SMR', 'OKLO', 'CCJ', 'UUUU', 'DNN',
    'LLY', 'NTLA', 'BEAM', 'NUTX',
    'MSTR', 'IREN', 'APLD', 'HIVE',
    'AMZN', 'TSLA', 'META', 'GOOGL', 'NFLX', 'UBER',
    'JPM', 'GS', 'MS',
    'HPE', 'PONY', 'SOUN', 'BBAI',
    'ACHR', 'JOBY',
]

# Combined + deduplicated
FULL_UNIVERSE = list(dict.fromkeys(FINAL_UNIVERSE + CATALYST_UNIVERSE))

# ── Minimal regime (no live IBKR needed) ──────────────────
def get_simple_regime():
    try:
        spy = yf.Ticker('SPY').history(period='2d')
        vix = yf.Ticker('^VIX').history(period='2d')
        spy_chg = (spy['Close'].iloc[-1] - spy['Close'].iloc[-2]) / spy['Close'].iloc[-2] * 100
        vix_val = vix['Close'].iloc[-1]

        if spy_chg < -0.5 or vix_val > 25:
            regime = 'WEAK'
        elif abs(spy_chg) < 0.3:
            regime = 'CHOPPY'
        elif spy_chg >= 0.5 and vix_val < 20:
            regime = 'STRONG'
        elif spy_chg >= 0 and vix_val < 22:
            regime = 'NORMAL'
        else:
            regime = 'CAUTIOUS'

        return {
            'regime': regime,
            'spy_change': round(spy_chg, 2),
            'vix': round(vix_val, 2),
            'trade_bias': 'SHORT' if regime == 'WEAK' else ('NONE' if regime == 'CHOPPY' else 'LONG'),
            'max_trades': 0 if regime == 'CHOPPY' else (3 if regime == 'STRONG' else 2),
            'confidence_boost': 10 if regime == 'STRONG' else (-20 if regime in ['CHOPPY','WEAK'] else 0),
        }
    except Exception as e:
        print(f"Regime error: {e}")
        return {'regime': 'NORMAL', 'spy_change': 0, 'vix': 18,
                'trade_bias': 'LONG', 'max_trades': 2, 'confidence_boost': 0}

# ── Minimal FVG check ─────────────────────────────────────
def quick_fvg_check(symbol):
    try:
        df = yf.Ticker(symbol).history(period='5d', interval='15m')
        if df is None or len(df) < 3:
            return 0
        high  = df['High'].values
        low   = df['Low'].values
        close = df['Close'].values
        count = 0
        for i in range(1, len(df)-1):
            if high[i-1] < low[i+1]:
                gap_pct = (low[i+1] - high[i-1]) / close[i] * 100
                if gap_pct >= 0.15:
                    count += 1
        return count
    except:
        return 0

# ── Quick S/R and SL/Target ───────────────────────────────
def quick_sl_target(symbol, price):
    try:
        df   = yf.Ticker(symbol).history(period='30d')
        high = df['High'].values
        low  = df['Low'].values

        supports    = sorted([l for l in low if l < price], reverse=True)[:3]
        resistances = sorted([h for h in high if h > price])[:3]

        nearest_sup = supports[0] if supports else price * 0.97
        raw_risk    = (price - nearest_sup * 0.998) / price * 100
        risk_pct    = min(raw_risk, 3.0)
        sl          = round(price * (1 - risk_pct / 100), 2)
        reward_pct  = min(risk_pct * 3, 9.0)
        target      = round(price * (1 + reward_pct / 100), 2)
        rr          = round(reward_pct / risk_pct, 1) if risk_pct > 0 else 0

        return sl, target, round(risk_pct, 2), round(reward_pct, 2), rr
    except:
        sl     = round(price * 0.97, 2)
        target = round(price * 1.09, 2)
        return sl, target, 3.0, 9.0, 3.0

# ── Quick signals ─────────────────────────────────────────
def quick_signals(symbol):
    try:
        df     = yf.Ticker(symbol).history(period='3mo')
        if df.empty or len(df) < 20:
            return None
        close  = df['Close']
        volume = df['Volume']
        from datetime import datetime
        import pytz
        ET      = pytz.timezone('America/New_York')
        now_et  = datetime.now(ET)
        # Before 11am ET today's bar is incomplete — use previous day
        use_idx = -2 if now_et.hour < 11 else -1

        price     = close.iloc[use_idx]
        change_1d = (close.iloc[use_idx] - close.iloc[use_idx-1]) / close.iloc[use_idx-1] * 100
        avg_vol   = volume.rolling(20).mean().iloc[use_idx]
        vol_ratio = volume.iloc[use_idx] / avg_vol if avg_vol > 0 else 1
        ma20         = close.rolling(20).mean().iloc[-1]
        ma50         = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else ma20
        ema8         = close.ewm(span=8).mean().iloc[-1]
        ema21        = close.ewm(span=21).mean().iloc[-1]

        above_ma     = price > ma20
        uptrend      = price > ema8 > ema21
        ema_touch    = abs(price - ema21) / price * 100 < 2.5

        # Gap up today
        today_open   = df['Open'].iloc[-1]
        prev_close   = close.iloc[-2]
        gap_pct      = (today_open - prev_close) / prev_close * 100
        is_gap_up    = gap_pct >= 2.0

        # Tight range
        r_high = df['High'].iloc[-3:].max()
        r_low  = df['Low'].iloc[-3:].min()
        range_pct = (r_high - r_low) / r_low * 100
        is_tight  = range_pct < 5

        # RSI approx
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss
        rsi   = 100 - (100 / (1 + rs.iloc[-1]))

        return {
            'price':      round(price, 2),
            'change_1d':  round(change_1d, 2),
            'vol_ratio':  round(vol_ratio, 2),
            'above_ma':   above_ma,
            'uptrend':    uptrend,
            'ema_touch':  ema_touch,
            'is_gap_up':  is_gap_up,
            'gap_pct':    round(gap_pct, 2),
            'is_tight':   is_tight,
            'range_pct':  round(range_pct, 2),
            'rsi':        round(rsi, 1),
        }
    except Exception as e:
        return None

# ── Strategy assignment ───────────────────────────────────
def assign_strategy(symbol, sig, fvg_count, regime):
    rn       = regime['regime']
    is_weak  = rn in ['WEAK', 'CHOPPY']
    is_strong = rn in ['STRONG', 'NORMAL']
    vol      = sig['vol_ratio']

    candidates = []

    # FVG_FILL
    if fvg_count >= 1:
        score = 80 + min(fvg_count * 1.5, 15)
        if rn == 'CHOPPY': score -= 20
        candidates.append(('FVG_FILL', round(score), f'{fvg_count} unfilled FVGs'))

    # TREND_PULLBACK
    if sig['uptrend'] and sig['ema_touch'] and is_strong and vol >= 1.3:
        candidates.append(('TREND_PULLBACK', 80, f'EMA touch in uptrend, RSI {sig["rsi"]:.0f}'))

    # GAP_GO
    if sig['is_gap_up'] and is_strong and vol >= 2.0:
        candidates.append(('GAP_GO', 88, f'Gap up {sig["gap_pct"]:.1f}% with {vol:.1f}x vol'))

    # SBS_BREAKOUT
    if sig['is_tight'] and vol >= 1.5 and not is_weak:
        candidates.append(('SBS_BREAKOUT', 75, f'Tight range {sig["range_pct"]:.1f}%'))

    # SHORT_FADE — significantly overbought on weak/choppy day (RSI 75+ only)
    if rn in ['WEAK', 'CHOPPY'] and sig['rsi'] > 75 and vol >= 1.0:
        candidates.append(('SHORT_FADE', 78, f'Weak/choppy + RSI {sig["rsi"]:.0f} overbought'))

    # FVG_FILL — big move up + many FVGs = pullback bounce setup (not short)
    if sig['change_1d'] > 5 and fvg_count >= 5 and vol >= 1.3:
        candidates.append(('FVG_FILL', 82, f'Big move +{sig["change_1d"]:.1f}% left {fvg_count} FVGs — pullback entry'))

    # BOUNCE candidate — red stock on choppy day with FVGs
    if rn in ['CHOPPY', 'WEAK'] and sig['change_1d'] < 0 and fvg_count >= 3:
        candidates.append(('FVG_FILL', 72, f'Pullback into FVG zone ({fvg_count} gaps) — bounce tomorrow'))

    # MOMENTUM fallback
    if not candidates:
        candidates.append(('MOMENTUM', 60, 'Standard momentum'))

    candidates.sort(key=lambda x: -x[1])
    return candidates[0]

# ── HARD FILTERS (same as v4 strategy) ───────────────────
def passes_filters(sig, regime):
    rn = regime['regime']
    if not sig['above_ma']:
        return False, 'Below MA'
    # On choppy/weak days red stocks are normal — don't filter them
    if sig['change_1d'] < 0 and rn not in ['WEAK', 'CHOPPY']:
        return False, 'Red today'
    # Volume threshold varies by regime
    # STRONG: 1.0x (big days often start slow then explode)
    # NORMAL: 1.2x
    # CAUTIOUS/CHOPPY/WEAK: 1.0x
    min_vol = 1.0 if rn in ['STRONG', 'CAUTIOUS', 'CHOPPY', 'WEAK'] else 1.2
    if sig['vol_ratio'] < min_vol:
        return False, f'Low volume ({sig["vol_ratio"]:.1f}x)'
    if sig['price'] < 5:
        return False, 'Price too low'
    if sig['price'] > 600:
        return False, 'Price too high'
    return True, 'OK'

# ── MAIN ─────────────────────────────────────────────────
if __name__ == '__main__':
    print("\n" + "="*65)
    print("  BATCH STRATEGY TEST — TODAY'S UNIVERSE")
    print("="*65)

    regime = get_simple_regime()
    print(f"\nMarket Regime: {regime['regime']} | SPY {regime['spy_change']:+.1f}% | VIX {regime['vix']:.1f}")
    print(f"Trade Bias: {regime['trade_bias']} | Max trades: {regime['max_trades']}\n")

    print(f"\nScanning {len(FULL_UNIVERSE)} stocks (FINAL + CATALYST universe)...")
    results   = []
    skipped   = []

    for symbol in FULL_UNIVERSE:
        print(f"  Scanning {symbol}...", end=' ', flush=True)
        sig = quick_signals(symbol)
        if sig is None:
            print("no data")
            skipped.append((symbol, 'No data'))
            continue

        passed, reason = passes_filters(sig, regime)
        if not passed:
            print(f"skip ({reason})")
            skipped.append((symbol, reason))
            continue

        fvg_count = quick_fvg_check(symbol)
        strategy, confidence, strat_reason = assign_strategy(symbol, sig, fvg_count, regime)
        sl, target, risk_pct, reward_pct, rr = quick_sl_target(symbol, sig['price'])

        valid = rr >= 3.0 and risk_pct <= 3.0
        print(f"{'✅' if valid else '⚠️ '} {strategy} | conf={confidence} | RR=1:{rr}")

        results.append({
            'symbol':     symbol,
            'strategy':   strategy,
            'confidence': confidence,
            'reason':     strat_reason,
            'price':      sig['price'],
            'change_1d':  sig['change_1d'],
            'vol_ratio':  sig['vol_ratio'],
            'rsi':        sig['rsi'],
            'fvg_count':  fvg_count,
            'sl':         sl,
            'target':     target,
            'risk_pct':   risk_pct,
            'reward_pct': reward_pct,
            'rr':         rr,
            'valid':      valid,
        })

    # ── Results summary ───────────────────────────────────
    valid_trades = [r for r in results if r['valid']]
    valid_trades.sort(key=lambda x: -x['confidence'])

    print(f"\n{'='*65}")
    print(f"  RESULTS: {len(valid_trades)} valid setups from {len(FULL_UNIVERSE)} stocks")
    print(f"  Skipped: {len(skipped)} (filters)")
    print(f"{'='*65}")

    if valid_trades:
        print(f"\n{'SYMBOL':<7} {'STRATEGY':<16} {'CONF':>4} {'PRICE':>8} {'1D%':>6} "
              f"{'VOL':>5} {'RSI':>5} {'FVGs':>4} {'SL':>8} {'TGT':>8} {'RR':>5}")
        print("-"*95)
        for r in valid_trades:
            print(f"{r['symbol']:<7} {r['strategy']:<16} {r['confidence']:>4} "
                  f"${r['price']:>7.2f} {r['change_1d']:>+5.1f}% {r['vol_ratio']:>4.1f}x "
                  f"{r['rsi']:>5.1f} {r['fvg_count']:>4} "
                  f"${r['sl']:>7.2f} ${r['target']:>7.2f} 1:{r['rr']:>3.1f}")

    print(f"\n--- Skipped ---")
    for s, reason in skipped:
        print(f"  {s:<7} → {reason}")

    print(f"\n--- Strategy breakdown ---")
    from collections import Counter
    strat_counts = Counter(r['strategy'] for r in valid_trades)
    for s, c in strat_counts.most_common():
        print(f"  {s:<16} {c} picks")

    print(f"\n--- Top 3 picks for TOMORROW (if market opens green) ---")
    for r in valid_trades[:3]:
        print(f"\n  {r['symbol']} — {r['strategy']}")
        print(f"  Entry: ~${r['price']} | SL: ${r['sl']} | Target: ${r['target']}")
        print(f"  Risk: {r['risk_pct']:.1f}% | Reward: {r['reward_pct']:.1f}% | R:R 1:{r['rr']}")
        print(f"  Reason: {r['reason']}")
