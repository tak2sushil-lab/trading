# backtest_bear.py — 5-year bear strategy backtest on daily OHLCV
# Tests short entries on WEAK market days (SPY daily return <= -0.5%)
#
# APPROXIMATIONS (daily bar limits):
#   Entry        = today's Open  (short entered after ORB confirms weakness ~10am)
#   Stop         = Open × 1.05  — 5% above entry (squeeze risk = max loss)
#   Stop hit     = day High >= SL  (if high touched stop, we covered at loss)
#   Trail exit   = day Close > (day Low + ATR_TRAIL_MULT×ATR)  (bounced from LOD)
#   EOD exit     = cover at day Close  (no overnight shorts — squeeze risk)
#   VWAP proxy   = (O+H+L+C)/4  — day's "typical price"
#   orb_break_dn = opened down ≥1.5% AND closed ≤ open  (gap-and-continue down)
#   bear_flag    = prior 3d fell ≥5% AND today tight range (<3%) AND close < open
#   lod_break    = close within 1% of day's low
#
# Command: venv/bin/python backtest_bear.py
#          venv/bin/python backtest_bear.py TSLA AMD NVDA PLTR

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date
import sys
import warnings
warnings.filterwarnings('ignore')

# ── Config ─────────────────────────────────────────────────────────────
START_DATE        = '2020-01-01'
END_DATE          = date.today().isoformat()
SYMBOLS           = sys.argv[1:] if len(sys.argv) > 1 else [
    # Original validated universe
    'NVDA', 'PLTR', 'TSLA', 'AMD', 'HOOD', 'SMCI', 'IONQ', 'META', 'AMZN', 'GOOGL',
    # Live short symbols May 2026 — added after short-side diagnosis
    'UUUU', 'OKLO', 'HIMS', 'CHPT', 'MP', 'USAR', 'MSTR', 'AXON', 'COHR',
    'AI',   'TOST', 'NIO',  'RIVN', 'EOSE', 'CCJ',  'VST',  'CLS',
    'QBTS', 'APLD', 'DXCM', 'NTLA', 'ONDS',
]
CAPITAL_PER_TRADE = 2000
ATR_PERIOD        = 14
STOP_PCT          = 5.0           # 5% above entry = SL
ATR_TRAIL_MULT    = 1.5
ATR_FADE_MULT     = 1.0
MIN_RR            = 2.5
MIN_VOLUME_RATIO  = 1.3
MIN_TODAY_DECLINE = 3.0           # matches live auto_trader — stock must be down ≥3% today

# ── DNA Cluster Sets (mirrors auto_trader.py — update when dna_analysis.py re-runs) ──
HIGH_VOL_SYMBOLS = frozenset([
    'AI','APLD','APP','BBAI','BEAM','CHPT','DNN','EOSE','INDI','IONQ',
    'IREN','JOBY','LAC','MARA','NTLA','NU','NUTX','ONDS','POET','QBTS',
    'RDW','RGTI','RIVN','RKLB','RKT','SOUN','TOST','VERI',
    'ARRY','CIFR','CLSK','EQT','HUT','RIOT','WULF',
])
INSTITUTIONAL_SYMBOLS = frozenset([
    'AAPL','ABBV','AMAT','AVGO','AXON','BAC','C','CAT','CNQ','COST',
    'CVX','DE','DVN','GOOGL','GS','HAL','HOOD','INTC','ISRG','ITA',
    'JPM','KLAC','LMT','LRCX','MA','MSFT','NKE','NOC','OKLO','ON',
    'OXY','PFE','QCOM','RTX','SBUX','SLB','SMH','UNH','V','VST',
    'WFC','XBI','XLE','XOM',
    'ACLS','BSX','BWXT','CACI','CPNG','CTRA','EW','FTNT','GE','GDDY',
    'GILD','HOLX','HWM','IBKR','KKR','KTOS','ONTO','SAIC','SAIA','SITM',
    'TPR','TT','TXT','YUM',
])

# ── Build SPY daily regime ────────────────────────────────────────────
def build_spy_regime(start, end):
    spy = yf.download('SPY', start=start, end=end, progress=False, auto_adjust=True)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)
    spy['spy_chg'] = spy['Close'].pct_change() * 100
    spy['regime']  = spy['spy_chg'].apply(
        lambda c: 'STRONG' if c >= 0.5 else ('WEAK' if c <= -0.5 else 'NORMAL')
    )
    return spy[['spy_chg', 'regime']]

# ── ATR (rolling) ──────────────────────────────────────────────────────
def add_atr(df):
    h, l, c = df['High'], df['Low'], df['Close']
    tr = pd.concat([h - l,
                    (h - c.shift(1)).abs(),
                    (l - c.shift(1)).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(ATR_PERIOD).mean()
    return df

# ── Grade a day's short setup ─────────────────────────────────────────
def grade_bear_day(i, df, spy_chg, regime, symbol=None):
    row      = df.iloc[i]
    prev_row = df.iloc[i - 1]
    df_upto  = df.iloc[:i + 1]

    price = row['Open']
    atr   = row['atr']
    if pd.isna(atr) or atr <= 0 or pd.isna(price) or price < 5:
        return 'SKIP', 0, []

    # Hard gate 1: only trade on WEAK days
    if regime != 'WEAK':
        return 'SKIP', 0, []

    # Hard gate 2: stock must be BELOW MA20 (not a strong stock being sold temporarily)
    ma20 = df_upto['Close'].rolling(20).mean().iloc[-1]
    if pd.isna(ma20) or price > ma20:
        return 'SKIP', 0, []

    # Hard gate 3: stock must be declining today (use close as look-ahead proxy)
    today_chg = (row['Close'] - prev_row['Close']) / prev_row['Close'] * 100
    if today_chg > -MIN_TODAY_DECLINE:
        return 'SKIP', 0, []

    # Volume check
    avg_vol   = df_upto['Volume'].rolling(20).mean().iloc[-2] if len(df_upto) >= 21 else None
    vol_ratio = float(row['Volume'] / avg_vol) if avg_vol and avg_vol > 0 else 1.0
    if vol_ratio < MIN_VOLUME_RATIO:
        return 'SKIP', 0, []

    # ── Bear pattern detection ─────────────────────────────────────────
    gap_pct      = (row['Open'] - prev_row['Close']) / prev_row['Close'] * 100
    vwap_est     = (row['Open'] + row['High'] + row['Low'] + row['Close']) / 4
    vwap_below   = row['Close'] < vwap_est
    orb_break_dn = gap_pct <= -1.5 and row['Close'] <= row['Open'] * 1.002
    lod_break    = row['Close'] <= row['Low'] * 1.010

    bear_flag = False
    if len(df_upto) >= 6:
        prior3_chg  = (float(df_upto['Close'].iloc[-2]) - float(df_upto['Close'].iloc[-5])) \
                      / max(float(df_upto['Close'].iloc[-5]), 0.01) * 100
        today_range = (row['High'] - row['Low']) / max(row['Open'], 0.01) * 100
        bear_flag   = prior3_chg <= -5.0 and today_range < 3.0 and row['Close'] < row['Open']

    rs_vs_spy   = today_chg - spy_chg     # negative = stock weaker than SPY = good short
    rs_weak     = rs_vs_spy <= -2.0 and today_chg < -1.0

    has_bear_pattern = orb_break_dn or vwap_below or lod_break or bear_flag or rs_weak
    if not has_bear_pattern:
        return 'SKIP', 0, []

    # ── Bear scoring ───────────────────────────────────────────────────
    score   = 0
    reasons = []

    if orb_break_dn: score += 25; reasons.append(f'Gap-dn {gap_pct:.1f}%')
    if vwap_below:   score += 15; reasons.append('Below VWAP')
    if lod_break:    score += 20; reasons.append('LOD break')
    if bear_flag:    score += 25; reasons.append('Bear flag')
    if rs_weak:      score += 15; reasons.append(f'RS {rs_vs_spy:.1f}% weak')

    if vol_ratio >= 2.0:   score += 25; reasons.append(f'{vol_ratio:.1f}x vol')
    elif vol_ratio >= 1.5: score += 15; reasons.append(f'{vol_ratio:.1f}x vol')
    else:                  score += 5;  reasons.append(f'{vol_ratio:.1f}x vol')

    if today_chg <= -5.0:  score += 20; reasons.append(f'{today_chg:.1f}%')
    elif today_chg <= -3.0: score += 12; reasons.append(f'{today_chg:.1f}%')
    elif today_chg <= -1.5: score += 6;  reasons.append(f'{today_chg:.1f}%')

    d = df_upto['Close'].diff()
    g = d.clip(lower=0).rolling(14).mean().iloc[-1]
    l = (-d.clip(upper=0)).rolling(14).mean().iloc[-1]
    rsi = round(float(100 - (100 / (1 + g / l))), 1) if l and l > 0 else 50
    if rsi < 30:   score += 15; reasons.append(f'RSI {rsi:.0f} oversold')
    elif rsi < 45: score += 10; reasons.append(f'RSI {rsi:.0f} weak')
    elif rsi > 60: score -= 15; reasons.append(f'RSI {rsi:.0f} strong (risk)')

    score += 15; reasons.append('WEAK regime')
    score += 5;  reasons.append(f'R:R 1:{MIN_RR}')

    # ── DNA cluster modifier — short side (mirrors auto_trader) ─────
    vwap_rejection_proxy = row['High'] > vwap_est and row['Close'] < vwap_est
    if symbol in HIGH_VOL_SYMBOLS:
        if orb_break_dn and not vwap_rejection_proxy:
            score -= 15; reasons.append('HIGH_VOL: ORB↓-15 (bounce risk)')
        if vwap_rejection_proxy:
            score += 15; reasons.append('HIGH_VOL: VWAP reject+15')
    elif symbol in INSTITUTIONAL_SYMBOLS:
        if orb_break_dn:
            score += 5; reasons.append('INST: ORB↓+5')

    grade = 'A+' if score >= 80 else 'A' if score >= 65 else 'B' if score >= 50 else 'C'

    if grade in ('B', 'C'):
        return 'SKIP', score, reasons
    # If SPY also bouncing today, only take A+ shorts
    if spy_chg > -1.0 and grade != 'A+':
        return 'SKIP', score, reasons

    return grade, score, reasons

# ── Simulate a single short trade ──────────────────────────────────────
def simulate_bear_trade(row, atr, symbol=None):
    entry  = row['Open']
    sl     = round(entry * 1.05, 2)        # 5% above entry = stop
    one_r  = entry * 0.95                  # 5% below = 1R gain target
    hit_1r = row['Low'] <= one_r           # price fell 5% intraday

    half_cap       = CAPITAL_PER_TRADE / 2
    partial_locked = STOP_PCT / 100 * half_cap if hit_1r else 0.0
    rem_cap        = half_cap if hit_1r else CAPITAL_PER_TRADE

    # Hard stop: price squeezed up above SL
    if row['High'] >= sl:
        if hit_1r:
            rest_loss = -STOP_PCT / 100 * rem_cap
            total_pnl = round(partial_locked + rest_loss, 2)
            return 'PARTIAL_STOP', round(total_pnl / CAPITAL_PER_TRADE * 100, 2), total_pnl, sl
        return 'STOP', round(-STOP_PCT, 2), round(-STOP_PCT * CAPITAL_PER_TRADE / 100, 2), sl

    # ATR trail hit — price bounced from LOD (1.0× for HIGH_VOL, 1.5× others)
    _trail_mult = 1.0 if symbol in HIGH_VOL_SYMBOLS else ATR_TRAIL_MULT
    trail_stop = row['Low'] + _trail_mult * atr
    if row['Close'] > trail_stop:
        rest_pnl  = (entry - trail_stop) / entry * rem_cap
        total_pnl = round(partial_locked + rest_pnl, 2)
        result    = 'WIN' if total_pnl > 0 else 'FADE'
        return result, round(total_pnl / CAPITAL_PER_TRADE * 100, 2), total_pnl, trail_stop

    # ATR fade — bounce >1×ATR from LOD while in profit
    fade_stop = row['Low'] + ATR_FADE_MULT * atr
    if row['Close'] > fade_stop:
        rest_pnl  = (entry - row['Close']) / entry * rem_cap
        total_pnl = partial_locked + rest_pnl
        if total_pnl > 0:
            return 'FADE_EXIT', round(total_pnl / CAPITAL_PER_TRADE * 100, 2), round(total_pnl, 2), row['Close']

    # EOD: cover short at close
    rest_pnl  = (entry - row['Close']) / entry * rem_cap
    total_pnl = round(partial_locked + rest_pnl, 2)
    result    = 'WIN' if total_pnl > 0 else 'LOSS'
    return result, round(total_pnl / CAPITAL_PER_TRADE * 100, 2), total_pnl, row['Close']

# ── Backtest one symbol ────────────────────────────────────────────────
def backtest_symbol(symbol, spy_regime):
    df = yf.download(symbol, start=START_DATE, end=END_DATE,
                     progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if len(df) < 60:
        print(f"  {symbol}: insufficient data"); return pd.DataFrame()

    df     = add_atr(df)
    merged = df.join(spy_regime, how='left')
    merged['spy_chg'] = merged['spy_chg'].fillna(0)
    merged['regime']  = merged['regime'].fillna('NORMAL')

    trades = []
    for i in range(22, len(merged)):
        row     = merged.iloc[i]
        spy_chg = float(row['spy_chg'])
        regime  = str(row['regime'])

        grade, score, reasons = grade_bear_day(i, merged, spy_chg, regime, symbol)
        if grade == 'SKIP':
            continue

        result, pnl_pct, pnl_usd, exit_price = simulate_bear_trade(row, float(row['atr']), symbol)

        trades.append({
            'date':     merged.index[i].strftime('%Y-%m-%d'),
            'year':     merged.index[i].year,
            'symbol':   symbol,
            'grade':    grade,
            'score':    score,
            'regime':   regime,
            'spy_chg':  round(spy_chg, 2),
            'entry':    round(row['Open'], 2),
            'high':     round(row['High'], 2),
            'low':      round(row['Low'], 2),
            'exit':     round(exit_price, 2),
            'atr':      round(float(row['atr']), 2),
            'pnl_pct':  pnl_pct,
            'pnl_usd':  pnl_usd,
            'result':   result,
            'patterns': ' | '.join(reasons[:5]),
        })

    return pd.DataFrame(trades)

# ── Print results ──────────────────────────────────────────────────────
def print_results(df_all):
    if df_all.empty:
        print("No trades found."); return

    n       = len(df_all)
    wins    = (df_all['pnl_usd'] > 0).sum()
    losses  = (df_all['pnl_usd'] <= 0).sum()
    wr      = wins / n * 100
    total   = df_all['pnl_usd'].sum()
    avg_win = df_all[df_all['pnl_usd'] > 0]['pnl_usd'].mean() or 0
    avg_los = df_all[df_all['pnl_usd'] <= 0]['pnl_usd'].mean() or 0
    ev      = (wr / 100 * avg_win) + ((1 - wr / 100) * avg_los)

    print(f"\n{'='*62}")
    print(f"  ↓ BEAR BACKTEST RESULTS — {START_DATE} → {END_DATE}")
    print(f"  Symbols: {', '.join(SYMBOLS)}")
    print(f"{'='*62}")
    print(f"  Trades   : {n}  ({wins}W / {losses}L)   WR: {wr:.1f}%")
    print(f"  Total P&L: ${total:,.2f}")
    print(f"  Avg win  : ${avg_win:,.2f}  |  Avg loss: ${avg_los:,.2f}")
    print(f"  EV/trade : ${ev:,.2f}")

    print(f"\n── By Year {'─'*45}")
    by_year = df_all.groupby('year').agg(
        trades=('pnl_usd', 'count'),
        wins  =('pnl_usd', lambda x: (x > 0).sum()),
        pnl   =('pnl_usd', 'sum'),
    ).assign(wr=lambda d: d['wins'] / d['trades'] * 100)
    for yr, row in by_year.iterrows():
        print(f"  {yr}: {int(row['trades']):>3} trades  WR {row['wr']:.0f}%  P&L ${row['pnl']:,.0f}")

    print(f"\n── By Symbol {'─'*43}")
    by_sym = df_all.groupby('symbol').agg(
        trades=('pnl_usd', 'count'),
        wins  =('pnl_usd', lambda x: (x > 0).sum()),
        pnl   =('pnl_usd', 'sum'),
        avg   =('pnl_usd', 'mean'),
    ).assign(wr=lambda d: d['wins'] / d['trades'] * 100)
    by_sym = by_sym.sort_values('pnl', ascending=False)
    for sym, row in by_sym.iterrows():
        tag = '✅' if row['pnl'] > 0 else '❌'
        print(f"  {tag} {sym:<6} {int(row['trades']):>3}tr  WR {row['wr']:.0f}%  "
              f"P&L ${row['pnl']:,.0f}  avg ${row['avg']:,.0f}")

    print(f"\n── By Grade {'─'*44}")
    for g in ['A+', 'A']:
        sub = df_all[df_all['grade'] == g]
        if sub.empty: continue
        gwr = (sub['pnl_usd'] > 0).sum() / len(sub) * 100
        print(f"  Grade {g}: {len(sub)} trades  WR {gwr:.0f}%  P&L ${sub['pnl_usd'].sum():,.0f}")

    print(f"\n── Exit reasons {'─'*40}")
    by_result = df_all['result'].value_counts()
    for res, cnt in by_result.items():
        sub = df_all[df_all['result'] == res]
        wr_r = (sub['pnl_usd'] > 0).sum() / len(sub) * 100
        print(f"  {res:<15}: {cnt:>4}  WR {wr_r:.0f}%  avg ${sub['pnl_usd'].mean():,.0f}")

    print()


if __name__ == '__main__':
    print(f"\nLoading SPY regime {START_DATE} → {END_DATE}...")
    spy_regime = build_spy_regime(START_DATE, END_DATE)
    spy_reg_cnt = spy_regime['regime'].value_counts()
    weak_days = spy_reg_cnt.get('WEAK', 0)
    total_days = len(spy_regime)
    print(f"WEAK days: {weak_days}/{total_days} ({weak_days/total_days*100:.0f}%) — bear engine active on these")
    print(f"Symbols  : {', '.join(SYMBOLS)}\n")

    all_trades = []
    for sym in SYMBOLS:
        print(f"  Backtesting {sym}...")
        df_sym = backtest_symbol(sym, spy_regime)
        if not df_sym.empty:
            n   = len(df_sym)
            wr  = (df_sym['pnl_usd'] > 0).sum() / n * 100
            pnl = df_sym['pnl_usd'].sum()
            print(f"    → {n} trades  WR {wr:.0f}%  P&L ${pnl:,.0f}")
            all_trades.append(df_sym)

    if all_trades:
        df_all = pd.concat(all_trades, ignore_index=True)
        print_results(df_all)
    else:
        print("No trades across all symbols.")
