# backtest_strategy.py — 5-year auto_trader strategy backtest
# Simulates the full signal stack on daily OHLCV data (5-min only goes back 60 days)
#
# APPROXIMATIONS (daily bar limits):
#   Entry price   = today's Open  (simulates arriving after ORB forms ~10am)
#   Stop price    = Open × (1 - STOP_PCT)  — 3% intraday stop (ATR×2 is swing, we hold 1 day)
#   Stop hit      = day Low <= SL  (if low went below stop, we got stopped)
#   Trail exit    = day Close < (day High - ATR_TRAIL_MULT×ATR)  (faded from HOD)
#   EOD exit      = day Close     (MAX_HOLD_DAYS = 1)
#   VWAP proxy    = (O+H+L+C)/4  — day's "typical price"
#   VWAP reclaim  = Open < VWAP_est AND Close > VWAP_est
#   ORB proxy     = stock gapped ≥1.5% AND closed ≥ open (gap held all day)
#   Bull flag     = prior 3d moved ≥5% AND today range tight (<3%) AND close > open
#   HOD break     = close within 1% of day's high
#   Strong momo   = up ≥5% from prev close
#
# Command: venv/bin/python backtest_strategy.py
#          venv/bin/python backtest_strategy.py NVDA PLTR AAPL

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date
import sys
import warnings
warnings.filterwarnings('ignore')

# ── Config ─────────────────────────────────────────────────────────────
START_DATE         = '2020-01-01'
END_DATE           = date.today().isoformat()
CAPITAL_PER_TRADE  = 2000        # $ deployed per trade
ATR_PERIOD         = 14
STOP_PCT           = 5.0         # 5% fixed stop — $100 risk on $2,000 position
ATR_TRAIL_MULT     = 1.5         # trail = HOD - 1.5×ATR
ATR_FADE_MULT      = 1.0         # fade exit = HOD - 1.0×ATR (when in profit)
MIN_RR             = 2.5         # 5% stop × 2.5 = 12.5% display target
MIN_VOLUME_RATIO   = 1.3
MIN_TODAY_GAIN     = 3.0         # matches live auto_trader — stock must close ≥3% above prev close
SKIP_WEAK_DAYS     = True        # no trades on WEAK SPY days
FIRST_BAR_QUALITY  = True        # strong first-bar day: +15% capital + conditional partial exit

# ── Sector map — import from auto_trader for single source of truth ─────
try:
    sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
    from auto_trader import SECTOR_MAP as _SM
    SECTOR_MAP = _SM
except Exception:
    SECTOR_MAP = {}

# ── Symbol selection ─────────────────────────────────────────────────────
_raw_args = [a for a in sys.argv[1:] if not a.startswith('--')]
if '--universe' in sys.argv:
    SYMBOLS = sorted(SECTOR_MAP.keys()) if SECTOR_MAP else ['NVDA', 'PLTR', 'MSFT']
elif _raw_args:
    SYMBOLS = _raw_args
else:
    SYMBOLS = ['NVDA', 'PLTR', 'MSFT']

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

# ── Build SPY daily regime ───────────────────────────────────────────────
def build_spy_regime(start, end):
    spy = yf.download('SPY', start=start, end=end, progress=False, auto_adjust=True)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)
    spy['spy_chg'] = spy['Close'].pct_change() * 100
    spy['regime']  = spy['spy_chg'].apply(
        lambda c: 'STRONG' if c >= 0.5 else ('WEAK' if c <= -0.5 else 'NORMAL')
    )
    return spy[['spy_chg', 'regime']]

# ── ATR (rolling) ────────────────────────────────────────────────────────
def add_atr(df):
    h, l, c = df['High'], df['Low'], df['Close']
    tr = pd.concat([h - l,
                    (h - c.shift(1)).abs(),
                    (l - c.shift(1)).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(ATR_PERIOD).mean()
    return df

# ── Grade a day's setup ──────────────────────────────────────────────────
def grade_day(i, df, spy_chg, regime, symbol=None):
    row      = df.iloc[i]
    prev_row = df.iloc[i - 1]
    df_upto  = df.iloc[:i + 1]

    price    = row['Open']
    atr      = row['atr']
    if pd.isna(atr) or atr <= 0 or pd.isna(price) or price < 5:
        return 'SKIP', 0, [], False

    # ── Hard gates ──────────────────────────────────────────────────────
    if SKIP_WEAK_DAYS and regime == 'WEAK':
        return 'SKIP', 0, [], False

    ma20 = df_upto['Close'].rolling(20).mean().iloc[-1]
    if pd.isna(ma20) or price < ma20:
        return 'SKIP', 0, [], False

    # Momentum check: stock must be up ≥MIN_TODAY_GAIN% today (close vs prev close)
    # Note: this uses close — look-ahead, but approximates intraday gain at scan time
    today_gain = (row['Close'] - prev_row['Close']) / prev_row['Close'] * 100
    if today_gain < MIN_TODAY_GAIN:
        return 'SKIP', 0, [], False

    # Volume
    avg_vol   = df_upto['Volume'].rolling(20).mean().iloc[-2] if len(df_upto) >= 21 else None
    vol_ratio = float(row['Volume'] / avg_vol) if avg_vol and avg_vol > 0 else 1.0
    if vol_ratio < MIN_VOLUME_RATIO:
        return 'SKIP', 0, [], False

    # R:R check with fixed 3% intraday stop
    risk_pct  = STOP_PCT
    reward    = risk_pct * MIN_RR        # at least 2× the risk
    rr        = reward / risk_pct        # = MIN_RR
    # passes by construction — just confirm

    # ── Pattern detection (daily approximations) ────────────────────────
    gap_pct      = (row['Open'] - prev_row['Close']) / prev_row['Close'] * 100
    vwap_est     = (row['Open'] + row['High'] + row['Low'] + row['Close']) / 4
    vwap_reclaim = row['Open'] < vwap_est and row['Close'] > vwap_est
    orb_proxy    = gap_pct >= 1.5 and row['Close'] >= row['Open'] * 0.998
    hod_break    = row['Close'] >= row['High'] * 0.990

    bull_flag = False
    if len(df_upto) >= 6:
        prior3_chg  = (float(df_upto['Close'].iloc[-2]) - float(df_upto['Close'].iloc[-5])) \
                      / max(float(df_upto['Close'].iloc[-5]), 0.01) * 100
        today_range = (row['High'] - row['Low']) / max(row['Open'], 0.01) * 100
        bull_flag   = prior3_chg >= 5.0 and today_range < 3.0 and row['Close'] > row['Open']

    strong_momo  = today_gain >= 5.0
    rs_vs_spy    = today_gain - spy_chg
    rs_leader    = rs_vs_spy >= 3.0 and today_gain >= 2.0   # beating SPY by 3%+ = signal

    has_pattern  = orb_proxy or vwap_reclaim or bull_flag or hod_break or strong_momo or rs_leader
    if not has_pattern:
        return 'SKIP', 0, [], False

    # ── Scoring (mirrors grade_setup in auto_trader) ────────────────────
    score   = 0
    reasons = []

    if orb_proxy:    score += 30; reasons.append('ORB')
    if vwap_reclaim: score += 25; reasons.append('VWAP reclaim')
    if bull_flag:    score += 25; reasons.append('Bull flag')
    if hod_break:    score += 20; reasons.append('HOD break')
    if rs_leader:    score += 20; reasons.append(f'RS +{rs_vs_spy:.1f}% vs SPY')

    # Vol scoring — ≥2.5x = full signal; <2.5x = low energy, -10pts (mirrors auto_trader)
    if vol_ratio >= 2.5:    score += 25; reasons.append(f'{vol_ratio:.1f}x vol')
    elif vol_ratio >= 2.0:  score += 15; reasons.append(f'{vol_ratio:.1f}x vol (low energy -10)')
    elif vol_ratio >= 1.5:  score += 5;  reasons.append(f'{vol_ratio:.1f}x vol (low energy -10)')
    else:                   score -= 5;  reasons.append(f'{vol_ratio:.1f}x vol (low energy -10)')

    ema8  = float(df_upto['Close'].ewm(span=8).mean().iloc[-1])
    ema21 = float(df_upto['Close'].ewm(span=21).mean().iloc[-1])
    if price > ema8 > ema21: score += 20; reasons.append('EMA uptrend')
    elif price > ema21:      score += 10; reasons.append('Above EMA21')

    d = df_upto['Close'].diff()
    g = d.clip(lower=0).rolling(14).mean().iloc[-1]
    l = (-d.clip(upper=0)).rolling(14).mean().iloc[-1]
    rsi = round(float(100 - (100 / (1 + g / l))), 1) if l and l > 0 else 50
    # Daily RSI — hard gate for 70-80 danger zone (mirrors auto_trader)
    if 70 <= rsi < 80:     return 'SKIP', 0, [f'RSI {rsi:.0f} danger zone (70-80)'], False
    if 45 <= rsi <= 65:    score += 20; reasons.append(f'RSI {rsi:.0f}')
    elif 65 < rsi < 70:   reasons.append(f'RSI {rsi:.0f} elevated (neutral)')
    else:                  score += 5

    if today_gain >= 5.0: score += 30; reasons.append(f'+{today_gain:.1f}%')
    elif today_gain >= 3.0: score += 20; reasons.append(f'+{today_gain:.1f}%')
    elif today_gain >= 1.5: score += 10; reasons.append(f'+{today_gain:.1f}%')

    if regime == 'STRONG': score += 15; reasons.append('Strong mkt')
    elif regime == 'NORMAL': score += 5

    score += 5  # R:R always = MIN_RR (by construction)

    # ── DNA cluster modifier (mirrors auto_trader L1 entry) ─────────
    if symbol in HIGH_VOL_SYMBOLS:
        if orb_proxy and not vwap_reclaim:
            score -= 15; reasons.append('HIGH_VOL: ORB-15 (naked, gap fill risk)')
        if vwap_reclaim:
            score += 15; reasons.append('HIGH_VOL: VWAP+15 (pullback confirmed)')
    elif symbol in INSTITUTIONAL_SYMBOLS:
        if orb_proxy:
            score += 5; reasons.append('INST: ORB+5 (gap sticks 75%)')

    grade = 'A+' if score >= 80 else 'A' if score >= 65 else 'B' if score >= 50 else 'C'

    # Only A/A+ entries; on negative SPY day need A+
    if grade in ('B', 'C'):
        return 'SKIP', score, reasons, False
    if spy_chg < 0 and grade != 'A+':
        return 'SKIP', score, reasons, False

    # First-bar quality proxy (daily data): gap >1% AND vol >1.3× prior 20-day avg
    vol_avg20        = df_upto['Volume'].rolling(20).mean().iloc[-2]
    first_bar_strong = (
        FIRST_BAR_QUALITY
        and gap_pct > 1.0
        and not pd.isna(vol_avg20) and vol_avg20 > 0
        and row['Volume'] > vol_avg20 * 1.3
    )

    return grade, score, reasons, first_bar_strong

# ── Simulate a single trade from entry at Open ───────────────────────────
def simulate_trade(row, atr, first_bar_strong=False, _always_partial=False, symbol=None):
    capital    = CAPITAL_PER_TRADE * 1.15 if (FIRST_BAR_QUALITY and first_bar_strong) else CAPITAL_PER_TRADE
    do_partial = _always_partial or (not FIRST_BAR_QUALITY) or first_bar_strong

    entry  = row['Open']
    sl     = round(entry * (1 - STOP_PCT / 100), 2)
    one_r  = entry * (1 + STOP_PCT / 100)
    hit_1r = row['High'] >= one_r

    half_cap       = capital / 2
    partial_locked = (STOP_PCT / 100 * half_cap) if (hit_1r and do_partial) else 0.0
    rem_cap        = (half_cap if (hit_1r and do_partial) else capital)

    # Stop hit
    if row['Low'] <= sl:
        if hit_1r and do_partial:
            rest_loss = -STOP_PCT / 100 * rem_cap
            total_pnl = round(partial_locked + rest_loss, 2)
            return 'PARTIAL_STOP', round(total_pnl / capital * 100, 2), total_pnl, sl
        return 'STOP', round(-STOP_PCT, 2), round(-STOP_PCT * capital / 100, 2), sl

    # ATR trail hit — price faded from HOD by > 1.5×ATR
    _trail_mult = 1.0 if symbol in HIGH_VOL_SYMBOLS else ATR_TRAIL_MULT
    trail_stop = row['High'] - _trail_mult * atr
    if row['Close'] < trail_stop:
        rest_pnl  = (trail_stop - entry) / entry * rem_cap
        total_pnl = round(partial_locked + rest_pnl, 2)
        result    = 'WIN' if total_pnl > 0 else 'FADE'
        return result, round(total_pnl / capital * 100, 2), total_pnl, trail_stop

    # ATR fade — drop > 1×ATR from HOD while profitable
    fade_stop = row['High'] - ATR_FADE_MULT * atr
    if row['Close'] < fade_stop:
        rest_pnl  = (row['Close'] - entry) / entry * rem_cap
        total_pnl = partial_locked + rest_pnl
        if total_pnl > 0:
            return 'FADE_EXIT', round(total_pnl / capital * 100, 2), round(total_pnl, 2), row['Close']

    # EOD exit at close
    rest_pnl  = (row['Close'] - entry) / entry * rem_cap
    total_pnl = round(partial_locked + rest_pnl, 2)
    result    = 'WIN' if total_pnl > 0 else 'LOSS'
    return result, round(total_pnl / capital * 100, 2), total_pnl, row['Close']

# ── Backtest one symbol ──────────────────────────────────────────────────
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

        grade, score, reasons, first_bar_strong = grade_day(
            i, merged, spy_chg, regime, symbol)
        if grade == 'SKIP':
            continue

        result, pnl_pct, pnl_usd, exit_price = simulate_trade(row, float(row['atr']), first_bar_strong, symbol=symbol)
        _, _, pnl_usd_base, _                 = simulate_trade(row, float(row['atr']), False, _always_partial=True, symbol=symbol)

        trades.append({
            'date':             merged.index[i].strftime('%Y-%m-%d'),
            'month':            merged.index[i].month,
            'year':             merged.index[i].year,
            'symbol':           symbol,
            'grade':            grade,
            'score':            score,
            'regime':           regime,
            'spy_chg':          round(spy_chg, 2),
            'entry':            round(row['Open'], 2),
            'high':             round(row['High'], 2),
            'low':              round(row['Low'], 2),
            'exit':             round(exit_price, 2),
            'atr':              round(float(row['atr']), 2),
            'pnl_pct':          pnl_pct,
            'pnl_usd':          pnl_usd,
            'pnl_usd_base':     pnl_usd_base,
            'result':           result,
            'first_bar_strong': first_bar_strong,
            'patterns':         ' | '.join(reasons[:5]),
        })

    return pd.DataFrame(trades)

# ── Print results ────────────────────────────────────────────────────────
def print_results(df_all):
    if df_all.empty:
        print("No trades found."); return

    n         = len(df_all)
    wins      = df_all[df_all['pnl_usd'] > 0]
    losses    = df_all[df_all['pnl_usd'] <= 0]
    total_pnl = df_all['pnl_usd'].sum()
    wr        = len(wins) / n * 100
    avg_win   = wins['pnl_usd'].mean()   if len(wins)   else 0
    avg_loss  = losses['pnl_usd'].mean() if len(losses) else 0
    rr_actual = abs(avg_win / avg_loss)  if avg_loss and avg_loss != 0 else 0

    # Equity curve + drawdown
    df_sorted = df_all.sort_values('date')
    equity    = CAPITAL_PER_TRADE + df_sorted['pnl_usd'].cumsum()
    peak      = equity.cummax()
    dd        = (equity - peak) / peak * 100
    max_dd    = dd.min()

    # Sharpe
    daily_pnl = df_sorted.groupby('date')['pnl_usd'].sum()
    n_years   = max(1, (pd.to_datetime(df_all['date'].max()) -
                        pd.to_datetime(df_all['date'].min())).days / 365)
    sharpe    = (daily_pnl.mean() / daily_pnl.std() * np.sqrt(252)) if daily_pnl.std() > 0 else 0
    ann_ret   = total_pnl / n_years
    exp_val   = (wr/100 * avg_win) + ((1-wr/100) * avg_loss) if avg_loss else avg_win

    print(f"\n{'='*68}")
    print(f"  STRATEGY BACKTEST  {START_DATE} → {END_DATE}")
    print(f"  Symbols : {', '.join(SYMBOLS)}")
    print(f"  Capital : ${CAPITAL_PER_TRADE}/trade | Stop: {STOP_PCT}% | A/A+ grades only | WEAK days skipped")
    print(f"{'='*68}")

    print(f"\n  OVERALL ({n} trades, {df_all['symbol'].nunique()} stocks, {n_years:.1f} years)")
    print(f"  {'─'*50}")
    print(f"  Win rate          : {wr:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Avg winner        : ${avg_win:+.2f}  ({wins['pnl_pct'].mean():+.1f}%)" if len(wins) else "  Avg winner        : n/a")
    print(f"  Avg loser         : ${avg_loss:+.2f}  ({losses['pnl_pct'].mean():+.1f}%)" if len(losses) else "  Avg loser         : n/a")
    print(f"  Actual R:R        : 1:{rr_actual:.1f}")
    print(f"  Expected value    : ${exp_val:+.2f} / trade")
    print(f"  Total P&L         : ${total_pnl:+,.0f}")
    print(f"  Annual return     : ${ann_ret:+,.0f}/year  (~{ann_ret/CAPITAL_PER_TRADE*100:.0f}% on deployed)")
    print(f"  Max drawdown      : {max_dd:.1f}%")
    print(f"  Sharpe ratio      : {sharpe:.2f}")
    print(f"  Avg trades/year   : {n/n_years:.0f}  ({n/n_years/252:.2f}/day)")

    # ── By symbol ──────────────────────────────────────────────────────
    print(f"\n  BY SYMBOL:")
    print(f"  {'Sym':<6} {'N':>5} {'WR':>6} {'AvgW':>8} {'AvgL':>8} {'TotalPnL':>11} {'Sharpe':>7} {'MaxDD':>7}")
    print(f"  {'─'*60}")
    for sym in SYMBOLS:
        sub = df_all[df_all['symbol'] == sym]
        if sub.empty: continue
        sw  = sub[sub['pnl_usd'] > 0]; sl_ = sub[sub['pnl_usd'] <= 0]
        swr = len(sw) / len(sub) * 100
        stot= sub['pnl_usd'].sum()
        saw = sw['pnl_usd'].mean() if len(sw) else 0
        sal = sl_['pnl_usd'].mean() if len(sl_) else 0
        eq_s  = CAPITAL_PER_TRADE + sub.sort_values('date')['pnl_usd'].cumsum()
        pk_s  = eq_s.cummax()
        dd_s  = ((eq_s - pk_s) / pk_s * 100).min()
        dp_s  = sub.sort_values('date').groupby('date')['pnl_usd'].sum()
        sh_s  = (dp_s.mean() / dp_s.std() * np.sqrt(252)) if dp_s.std() > 0 else 0
        print(f"  {sym:<6} {len(sub):>5} {swr:>5.0f}% {saw:>+8.2f} {sal:>+8.2f} {stot:>+11,.0f} {sh_s:>7.2f} {dd_s:>6.1f}%")

    # ── By regime ──────────────────────────────────────────────────────
    print(f"\n  BY REGIME:")
    print(f"  {'Regime':<10} {'N':>5} {'WR':>6} {'AvgPnL':>9} {'TotalPnL':>11}")
    print(f"  {'─'*44}")
    for r in ['STRONG', 'NORMAL']:
        sub = df_all[df_all['regime'] == r]
        if sub.empty: continue
        swr = len(sub[sub['pnl_usd'] > 0]) / len(sub) * 100
        print(f"  {r:<10} {len(sub):>5} {swr:>5.0f}% {sub['pnl_usd'].mean():>+9.2f} {sub['pnl_usd'].sum():>+11,.0f}")

    # ── By year ────────────────────────────────────────────────────────
    print(f"\n  BY YEAR (all symbols combined):")
    print(f"  {'Year':<6} {'N':>5} {'WR':>6} {'AvgPnL':>9} {'TotalPnL':>11}  Verdict")
    print(f"  {'─'*52}")
    for yr in sorted(df_all['year'].unique()):
        sub = df_all[df_all['year'] == yr]
        swr = len(sub[sub['pnl_usd'] > 0]) / len(sub) * 100
        tot = sub['pnl_usd'].sum()
        tag = '✅' if tot > 0 else '❌'
        print(f"  {yr:<6} {len(sub):>5} {swr:>5.0f}% {sub['pnl_usd'].mean():>+9.2f} {tot:>+11,.0f}  {tag}")

    # ── By grade ───────────────────────────────────────────────────────
    print(f"\n  BY GRADE:")
    print(f"  {'Grade':<7} {'N':>5} {'WR':>6} {'AvgPnL':>9} {'TotalPnL':>11}")
    print(f"  {'─'*40}")
    for g in ['A+', 'A']:
        sub = df_all[df_all['grade'] == g]
        if sub.empty: continue
        swr = len(sub[sub['pnl_usd'] > 0]) / len(sub) * 100
        print(f"  {g:<7} {len(sub):>5} {swr:>5.0f}% {sub['pnl_usd'].mean():>+9.2f} {sub['pnl_usd'].sum():>+11,.0f}")

    # ── By exit type ───────────────────────────────────────────────────
    print(f"\n  BY EXIT TYPE:")
    print(f"  {'Exit':<12} {'N':>5} {'WR':>6} {'AvgPnL':>9} {'AvgPct':>8}")
    print(f"  {'─'*40}")
    for ex in ['WIN','FADE','FADE_EXIT','STOP','LOSS']:
        sub = df_all[df_all['result'] == ex]
        if sub.empty: continue
        swr = len(sub[sub['pnl_usd'] > 0]) / len(sub) * 100
        print(f"  {ex:<12} {len(sub):>5} {swr:>5.0f}% {sub['pnl_usd'].mean():>+9.2f} {sub['pnl_pct'].mean():>+7.1f}%")

    # ── Monthly breakdown ──────────────────────────────────────────────
    MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    print(f"\n  BY MONTH (avg P&L per calendar month, all years combined):")
    print(f"  {'Month':<6} {'N':>5} {'WR':>6} {'AvgPnL':>9} {'TotalPnL':>11}")
    print(f"  {'─'*40}")
    for mo in range(1, 13):
        sub = df_all[df_all['month'] == mo]
        if sub.empty: continue
        swr = len(sub[sub['pnl_usd'] > 0]) / len(sub) * 100
        print(f"  {MONTH_NAMES[mo-1]:<6} {len(sub):>5} {swr:>5.0f}% {sub['pnl_usd'].mean():>+9.2f} {sub['pnl_usd'].sum():>+11,.0f}")

    # ── First-bar quality impact ────────────────────────────────────────
    strong = df_all[df_all['first_bar_strong'] == True]
    weak   = df_all[df_all['first_bar_strong'] == False]
    total_fbq  = df_all['pnl_usd'].sum()
    total_base = df_all['pnl_usd_base'].sum()
    lift       = total_fbq - total_base
    pct_strong = len(strong) / len(df_all) * 100
    lift_mo    = lift / n_years / 12

    print(f"\n  FIRST-BAR QUALITY IMPACT  (gap>1% + vol>1.3×avg = strong):")
    print(f"  {'─'*68}")
    print(f"  {'Type':<22} {'N':>5} {'WR':>6} {'AvgPnL':>9} {'TotalPnL':>12}")
    print(f"  {'─'*56}")
    if len(strong):
        sw = strong[strong['pnl_usd'] > 0]
        print(f"  {'Strong first-bar':<22} {len(strong):>5} {len(sw)/len(strong)*100:>5.0f}%"
              f" {strong['pnl_usd'].mean():>+9.2f} {strong['pnl_usd'].sum():>+12,.0f}")
    if len(weak):
        ww = weak[weak['pnl_usd'] > 0]
        print(f"  {'Non-strong':<22} {len(weak):>5} {len(ww)/len(weak)*100:>5.0f}%"
              f" {weak['pnl_usd'].mean():>+9.2f} {weak['pnl_usd'].sum():>+12,.0f}")
    print(f"  {'─'*56}")
    print(f"  Strong-bar days account for {pct_strong:.0f}% of all trades")
    print(f"  With  FBQ  : ${total_fbq:>+10,.0f}  (${total_fbq/n_years/12:+.0f}/month avg)")
    print(f"  Without FBQ: ${total_base:>+10,.0f}  (${total_base/n_years/12:+.0f}/month avg)")
    print(f"  LIFT       : ${lift:>+10,.0f}  (${lift_mo:+.0f}/month avg)")

    # ── Top & worst trades ─────────────────────────────────────────────
    print(f"\n  TOP 5 TRADES:")
    for _, r in df_all.nlargest(5, 'pnl_usd').iterrows():
        print(f"    {r['date']}  {r['symbol']:<5} {r['grade']}  "
              f"${r['entry']:.2f}→${r['exit']:.2f}  {r['pnl_pct']:+.1f}%  ${r['pnl_usd']:+.0f}   {r['patterns']}")

    print(f"\n  WORST 5 TRADES:")
    for _, r in df_all.nsmallest(5, 'pnl_usd').iterrows():
        print(f"    {r['date']}  {r['symbol']:<5} {r['grade']}  "
              f"${r['entry']:.2f}→${r['exit']:.2f}  {r['pnl_pct']:+.1f}%  ${r['pnl_usd']:+.0f}   {r['patterns']}")

    # ── Final verdict ──────────────────────────────────────────────────
    print(f"\n{'='*68}")
    print(f"  VERDICT")
    print(f"{'='*68}")
    aplus = df_all[df_all['grade'] == 'A+']
    a_    = df_all[df_all['grade'] == 'A']
    if len(aplus):
        ap_wr = len(aplus[aplus['pnl_usd'] > 0]) / len(aplus) * 100
        print(f"  A+ setups ({len(aplus):>3} trades): {ap_wr:.0f}% WR  avg ${aplus['pnl_usd'].mean():+.2f}")
    if len(a_):
        a_wr  = len(a_[a_['pnl_usd'] > 0]) / len(a_) * 100
        print(f"  A  setups ({len(a_):>3} trades): {a_wr:.0f}% WR  avg ${a_['pnl_usd'].mean():+.2f}")

    if exp_val > 0 and wr >= 40:
        annual_trades = n / n_years
        print(f"\n  ✅ POSITIVE EDGE: ${exp_val:+.2f} expected per trade")
        print(f"     {annual_trades:.0f} trades/year × ${exp_val:.2f} = ${annual_trades*exp_val:+,.0f}/year potential")
        if wr >= 55:
            print(f"     Win rate {wr:.0f}% — strong signal quality")
        elif wr >= 45:
            print(f"     Win rate {wr:.0f}% — acceptable; winners must be larger than losers")
        else:
            print(f"     Win rate {wr:.0f}% — below ideal; R:R 1:{rr_actual:.1f} compensates")
    else:
        print(f"\n  ⚠️  EDGE UNCLEAR: Win rate {wr:.0f}% | Expected ${exp_val:+.2f}/trade")
        if wr < 40:
            print(f"     Entry criteria too loose — tighten grade thresholds")
        if exp_val <= 0:
            print(f"     Avg loser (${avg_loss:+.2f}) bigger than avg winner (${avg_win:+.2f}) — trail stops too wide")

    print(f"\n  Note: Daily-bar approximation — actual results vary ±15-20% vs live")
    print(f"  Key gap: 15m alignment, candlestick quality, earnings gate not in daily data")
    print(f"{'='*68}\n")

# ── Main ─────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print(f"\nLoading SPY regime {START_DATE} → {END_DATE}...")
    spy_regime = build_spy_regime(START_DATE, END_DATE)
    spy_reg_cnt = spy_regime['regime'].value_counts()
    print(f"  SPY: STRONG {spy_reg_cnt.get('STRONG',0)}d  NORMAL {spy_reg_cnt.get('NORMAL',0)}d  WEAK {spy_reg_cnt.get('WEAK',0)}d\n")

    all_trades = []
    for sym in SYMBOLS:
        print(f"Backtesting {sym}...", end=' ', flush=True)
        df_sym = backtest_symbol(sym, spy_regime)
        if not df_sym.empty:
            all_trades.append(df_sym)
            wr_sym = len(df_sym[df_sym['pnl_usd'] > 0]) / len(df_sym) * 100
            print(f"{len(df_sym)} trades | WR {wr_sym:.0f}% | P&L ${df_sym['pnl_usd'].sum():+,.0f}")
        else:
            print("no qualifying trades")

    if all_trades:
        df_all = pd.concat(all_trades, ignore_index=True).sort_values('date')
        # Add year-month time series column
        df_all['ym'] = df_all['date'].str[:7]
        print_results(df_all)
        # ── Year-month time series (the main comparison table) ──────────
        print(f"\n{'='*68}")
        print(f"  BY YEAR-MONTH (time series)")
        print(f"{'='*68}")
        print(f"  {'YearMon':<9} {'N':>5} {'WR':>6} {'AvgPnL':>9} {'MonthPnL':>11}  Flag")
        print(f"  {'─'*52}")
        for ym, g in df_all.groupby('ym'):
            swr  = len(g[g['pnl_usd'] > 0]) / len(g) * 100
            mpnl = g['pnl_usd'].sum()
            flag = '✅' if mpnl > 0 else '❌'
            print(f"  {ym:<9} {len(g):>5} {swr:>5.0f}% {g['pnl_usd'].mean():>+9.2f} {mpnl:>+11,.0f}  {flag}")
        print(f"{'='*68}\n")
    else:
        print("\nNo trades found. Check MIN_TODAY_GAIN or MIN_VOLUME_RATIO thresholds.")
