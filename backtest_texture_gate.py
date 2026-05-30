# backtest_texture_gate.py
# Portfolio-level simulation: does the texture gate (tight open → max 3 positions)
# improve results vs the baseline (max 5 positions per day)?
#
# Gate condition (daily proxy — 5-min not available for 5+ years):
#   SPY daily range < 0.55% AND abs(SPY net change) < 0.35%
# Known limitation: proxy has ~20% error rate vs exact 5-min ORB rule.
# Use as directional evidence only.
#
# Run: venv/bin/python backtest_texture_gate.py

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date
import sys
import warnings
warnings.filterwarnings('ignore')

# ── Config ────────────────────────────────────────────────────────────────
START_DATE       = '2020-01-01'
END_DATE         = date.today().isoformat()
CAPITAL_PER_TRADE= 2000
STOP_PCT         = 5.0
ATR_TRAIL_MULT   = 1.5
ATR_PERIOD       = 14
MIN_VOLUME_RATIO = 1.3
MIN_TODAY_GAIN   = 3.0
MAX_POSITIONS_NORMAL = 5     # baseline
MAX_POSITIONS_GATE   = 3     # on texture-gate days

# Representative cross-cluster universe (40 symbols covering all DNA clusters)
SYMBOLS = [
    # HIGH_VOL cluster
    'PLTR','MARA','RIOT','SOUN','RKLB','IONQ','JOBY','CHPT','ONDS','RIVN',
    # INSTITUTIONAL cluster
    'NVDA','MSFT','AAPL','GOOGL','AVGO','AMAT','AXON','LMT','MA','JPM',
    'KLAC','LRCX','ON','QCOM','VST','GS','CAT','COST','ISRG','UNH',
    # MOMENTUM cluster
    'TSLA','AMD','SOFI','COIN','HOOD','CELH','UPST','HIMS','APP','CRWD',
]

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

# ── SPY data: regime + texture ────────────────────────────────────────────
def build_spy_data(start, end):
    spy = yf.download('SPY', start=start, end=end, progress=False, auto_adjust=True)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)
    spy['spy_chg']    = spy['Close'].pct_change() * 100
    spy['regime']     = spy['spy_chg'].apply(
        lambda c: 'STRONG' if c >= 0.5 else ('WEAK' if c <= -0.5 else 'NORMAL'))
    # Texture gate proxy: low-energy day (daily range tight + net move flat)
    spy['range_pct']  = (spy['High'] - spy['Low']) / spy['Open'] * 100
    spy['atr14']      = (spy['High'] - spy['Low']).rolling(14).mean()
    # Gate fires when daily range < 0.55% AND abs net change < 0.35%
    spy['texture_gate'] = (spy['range_pct'] < 0.55) & (spy['spy_chg'].abs() < 0.35)
    return spy[['spy_chg', 'regime', 'range_pct', 'texture_gate']]

# ── ATR ───────────────────────────────────────────────────────────────────
def add_atr(df):
    h, l, c = df['High'], df['Low'], df['Close']
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(ATR_PERIOD).mean()
    return df

# ── Grade a single day ────────────────────────────────────────────────────
def grade_day(i, df, spy_chg, regime, symbol):
    row      = df.iloc[i]
    prev_row = df.iloc[i - 1]
    df_upto  = df.iloc[:i + 1]

    price = row['Open']
    atr   = row['atr']
    if pd.isna(atr) or atr <= 0 or pd.isna(price) or price < 5:
        return None

    if regime == 'WEAK':
        return None

    ma20 = df_upto['Close'].rolling(20).mean().iloc[-1]
    if pd.isna(ma20) or price < ma20:
        return None

    today_gain = (row['Close'] - prev_row['Close']) / prev_row['Close'] * 100
    if today_gain < MIN_TODAY_GAIN:
        return None

    avg_vol   = df_upto['Volume'].rolling(20).mean().iloc[-2] if len(df_upto) >= 21 else None
    vol_ratio = float(row['Volume'] / avg_vol) if avg_vol and avg_vol > 0 else 1.0
    if vol_ratio < MIN_VOLUME_RATIO:
        return None

    gap_pct      = (row['Open'] - prev_row['Close']) / prev_row['Close'] * 100
    vwap_est     = (row['Open'] + row['High'] + row['Low'] + row['Close']) / 4
    vwap_reclaim = row['Open'] < vwap_est and row['Close'] > vwap_est
    orb_proxy    = gap_pct >= 1.5 and row['Close'] >= row['Open'] * 0.998
    hod_break    = row['Close'] >= row['High'] * 0.990

    bull_flag = False
    if len(df_upto) >= 6:
        prior3_chg  = (float(df_upto['Close'].iloc[-2]) - float(df_upto['Close'].iloc[-5])) / \
                      max(float(df_upto['Close'].iloc[-5]), 0.01) * 100
        today_range = (row['High'] - row['Low']) / max(row['Open'], 0.01) * 100
        bull_flag   = prior3_chg >= 5.0 and today_range < 3.0 and row['Close'] > row['Open']

    strong_momo = today_gain >= 5.0
    rs_vs_spy   = today_gain - spy_chg
    rs_leader   = rs_vs_spy >= 3.0 and today_gain >= 2.0

    if not (orb_proxy or vwap_reclaim or bull_flag or hod_break or strong_momo or rs_leader):
        return None

    score = 0
    if orb_proxy:    score += 30
    if vwap_reclaim: score += 25
    if bull_flag:    score += 25
    if hod_break:    score += 20
    if rs_leader:    score += 20
    if vol_ratio >= 2.0:   score += 25
    elif vol_ratio >= 1.5: score += 15
    else:                  score += 5
    ema8  = float(df_upto['Close'].ewm(span=8).mean().iloc[-1])
    ema21 = float(df_upto['Close'].ewm(span=21).mean().iloc[-1])
    if price > ema8 > ema21: score += 20
    elif price > ema21:      score += 10
    d = df_upto['Close'].diff()
    g = d.clip(lower=0).rolling(14).mean().iloc[-1]
    lo = (-d.clip(upper=0)).rolling(14).mean().iloc[-1]
    rsi = round(float(100 - (100 / (1 + g / lo))), 1) if lo and lo > 0 else 50
    if 45 <= rsi <= 65:   score += 20
    elif 75 < rsi <= 80:  score += 5
    else:                 score += 5
    if today_gain >= 5.0:  score += 30
    elif today_gain >= 3.0: score += 20
    elif today_gain >= 1.5: score += 10
    if regime == 'STRONG': score += 15
    elif regime == 'NORMAL': score += 5
    score += 5

    if symbol in HIGH_VOL_SYMBOLS:
        if orb_proxy and not vwap_reclaim: score -= 15
        if vwap_reclaim: score += 15
    elif symbol in INSTITUTIONAL_SYMBOLS:
        if orb_proxy: score += 5

    grade = 'A+' if score >= 80 else 'A' if score >= 65 else None
    if grade is None:
        return None
    if spy_chg < 0 and grade != 'A+':
        return None

    return {'score': score, 'grade': grade}

# ── Simulate a single trade ───────────────────────────────────────────────
def simulate_trade(row, atr, symbol):
    capital = CAPITAL_PER_TRADE
    entry   = row['Open']
    sl      = entry * (1 - STOP_PCT / 100)
    one_r   = entry * (1 + STOP_PCT / 100)
    hit_1r  = row['High'] >= one_r

    partial_locked = (STOP_PCT / 100 * capital / 2) if hit_1r else 0.0
    rem_cap        = (capital / 2) if hit_1r else capital

    if row['Low'] <= sl:
        rest_loss = -STOP_PCT / 100 * rem_cap
        return round(partial_locked + rest_loss, 2)

    _trail = 1.0 if symbol in HIGH_VOL_SYMBOLS else ATR_TRAIL_MULT
    trail_stop = row['High'] - _trail * atr
    if row['Close'] < trail_stop:
        rest_pnl = (trail_stop - entry) / entry * rem_cap
        return round(partial_locked + rest_pnl, 2)

    fade_stop = row['High'] - 1.0 * atr
    if row['Close'] < fade_stop and (row['Close'] - entry) / entry * rem_cap > 0:
        rest_pnl = (row['Close'] - entry) / entry * rem_cap
        return round(partial_locked + rest_pnl, 2)

    rest_pnl = (row['Close'] - entry) / entry * rem_cap
    return round(partial_locked + rest_pnl, 2)

# ── Collect all signals for all symbols ──────────────────────────────────
def collect_all_signals(spy_data):
    all_signals = []
    for sym in SYMBOLS:
        print(f"  {sym}...", end=' ', flush=True)
        df = yf.download(sym, start=START_DATE, end=END_DATE,
                         progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if len(df) < 60:
            print("skip"); continue
        df     = add_atr(df)
        merged = df.join(spy_data, how='left')
        merged['spy_chg']     = merged['spy_chg'].fillna(0)
        merged['regime']      = merged['regime'].fillna('NORMAL')
        merged['texture_gate']= merged['texture_gate'].fillna(False)

        sym_count = 0
        for i in range(22, len(merged)):
            row  = merged.iloc[i]
            d    = merged.index[i]
            info = grade_day(i, merged, float(row['spy_chg']), str(row['regime']), sym)
            if info is None:
                continue
            pnl = simulate_trade(row, float(row['atr']), sym)
            all_signals.append({
                'date':         d.strftime('%Y-%m-%d'),
                'year':         d.year,
                'month':        d.month,
                'symbol':       sym,
                'score':        info['score'],
                'grade':        info['grade'],
                'pnl':          pnl,
                'texture_gate': bool(row['texture_gate']),
                'spy_range':    round(float(row['range_pct']), 3),
                'spy_chg':      round(float(row['spy_chg']), 3),
            })
            sym_count += 1
        print(f"{sym_count} signals")
    return pd.DataFrame(all_signals)

# ── Apply portfolio caps and compare baseline vs gate ────────────────────
def apply_portfolio_caps(df_signals):
    """
    For each day: take top N signals by score.
    Baseline: max MAX_POSITIONS_NORMAL per day.
    Gate:     max MAX_POSITIONS_GATE on texture-gate days, else MAX_POSITIONS_NORMAL.
    """
    df = df_signals.sort_values(['date', 'score'], ascending=[True, False])
    baseline_trades = []
    gate_trades     = []

    for day, group in df.groupby('date'):
        is_gate = group['texture_gate'].iloc[0]
        baseline_trades.append(group.head(MAX_POSITIONS_NORMAL))
        gate_max = MAX_POSITIONS_GATE if is_gate else MAX_POSITIONS_NORMAL
        gate_trades.append(group.head(gate_max))

    baseline = pd.concat(baseline_trades).reset_index(drop=True)
    gate     = pd.concat(gate_trades).reset_index(drop=True)
    return baseline, gate

# ── Summary by year/month ─────────────────────────────────────────────────
def summarise(df, label):
    n      = len(df)
    wins   = (df['pnl'] > 0).sum()
    total  = df['pnl'].sum()
    wr     = wins / n * 100 if n else 0
    avg_pnl= df['pnl'].mean()
    return {'label': label, 'n': n, 'wr': wr, 'total': total, 'avg': avg_pnl}

def print_comparison(baseline, gate, spy_data):
    MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    b = summarise(baseline, 'BASELINE (max 5)')
    g = summarise(gate,     'GATE    (max 3 on texture days)')

    print(f"\n{'='*70}")
    print(f"  TEXTURE GATE SIMULATION  {START_DATE} → {END_DATE}")
    print(f"  {len(SYMBOLS)} symbols | Gate proxy: SPY range<0.55% + |net|<0.35%")
    print(f"  Limitation: daily proxy ~20% mismatch vs exact 5-min ORB rule")
    print(f"{'='*70}")
    print(f"\n  OVERALL:")
    print(f"  {'':30s}  {'Trades':7s}  {'WR':6s}  {'AvgPnL':9s}  {'Total P&L':11s}")
    print(f"  {'─'*65}")
    for r in [b, g]:
        print(f"  {r['label']:30s}  {r['n']:7d}  {r['wr']:5.1f}%  {r['avg']:+8.2f}   {r['total']:+10,.0f}")
    delta = g['total'] - b['total']
    delta_wr = g['wr'] - b['wr']
    print(f"  {'GATE IMPROVEMENT':30s}  {g['n']-b['n']:+7d}  {delta_wr:+5.1f}%  {'':9s}   {delta:+10,.0f}")

    # Gate-day count
    gate_days_df = baseline.groupby('date').first().reset_index()
    n_gate_days  = gate_days_df['texture_gate'].sum()
    total_days   = len(gate_days_df)
    print(f"\n  Gate fired: {n_gate_days}/{total_days} trading days ({n_gate_days/total_days*100:.1f}%)")

    # Year-by-year
    print(f"\n  BY YEAR:")
    print(f"  {'Year':6s}  {'B trades':9s}  {'B WR':6s}  {'B Total':10s}  {'G trades':9s}  {'G WR':6s}  {'G Total':10s}  {'Δ':8s}  {'Δ%':6s}")
    print(f"  {'─'*85}")
    for yr in sorted(baseline['year'].unique()):
        bs = baseline[baseline['year'] == yr]
        gs = gate[gate['year'] == yr]
        b_tot = bs['pnl'].sum(); b_wr = (bs['pnl']>0).sum()/len(bs)*100
        g_tot = gs['pnl'].sum(); g_wr = (gs['pnl']>0).sum()/len(gs)*100
        d_abs = g_tot - b_tot
        d_pct = d_abs / abs(b_tot) * 100 if b_tot != 0 else 0
        tag = '✅' if d_abs >= 0 else '❌'
        print(f"  {yr}    {len(bs):9d}  {b_wr:5.1f}%  {b_tot:+9,.0f}  {len(gs):9d}  {g_wr:5.1f}%  {g_tot:+9,.0f}  {d_abs:+7,.0f}  {d_pct:+5.1f}%  {tag}")

    # Month-by-month (all years combined)
    print(f"\n  BY MONTH (all years combined):")
    print(f"  {'Month':6s}  {'B WR':6s}  {'B Total':10s}  {'G WR':6s}  {'G Total':10s}  {'Δ':8s}  Direction")
    print(f"  {'─'*65}")
    for mo in range(1, 13):
        bs = baseline[baseline['month'] == mo]
        gs = gate[gate['month'] == mo]
        if bs.empty: continue
        b_tot = bs['pnl'].sum(); b_wr = (bs['pnl']>0).sum()/len(bs)*100
        g_tot = gs['pnl'].sum(); g_wr = (gs['pnl']>0).sum()/len(gs)*100
        d_abs = g_tot - b_tot
        tag = '✅ helps' if d_abs > 50 else ('❌ hurts' if d_abs < -50 else '~neutral')
        print(f"  {MONTH_NAMES[mo-1]:6s}  {b_wr:5.1f}%  {b_tot:+9,.0f}  {g_wr:5.1f}%  {g_tot:+9,.0f}  {d_abs:+7,.0f}  {tag}")

    # Gate-day specific analysis
    print(f"\n  GATE-DAY PERFORMANCE (days where gate actually fired):")
    b_gate = baseline[baseline['texture_gate']]
    g_gate = gate[gate['texture_gate']]
    if not b_gate.empty:
        b_g_tot = b_gate['pnl'].sum(); b_g_wr = (b_gate['pnl']>0).sum()/len(b_gate)*100
        g_g_tot = g_gate['pnl'].sum(); g_g_wr = (g_gate['pnl']>0).sum()/len(g_gate)*100
        print(f"  Baseline on gate days: {len(b_gate)} trades, {b_g_wr:.1f}% WR, {b_g_tot:+,.0f}")
        print(f"  Gate    on gate days: {len(g_gate)} trades, {g_g_wr:.1f}% WR, {g_g_tot:+,.0f}")
        print(f"  Improvement on gate days: {g_g_tot-b_g_tot:+,.0f}")

    # Non-gate days (should be identical — sanity check)
    b_norm = baseline[~baseline['texture_gate']]
    g_norm = gate[~gate['texture_gate']]
    d_norm = g_norm['pnl'].sum() - b_norm['pnl'].sum()
    print(f"\n  Non-gate days: Δ = {d_norm:+.2f}  (should be ~0 — sanity check)")

    print(f"\n{'='*70}")
    print(f"  Note: proxy accuracy ~80%. Results directional, not exact.")
    print(f"  For exact results: requires 5-min intraday SPY data (unavailable >60 days)")
    print(f"{'='*70}\n")

# ── Main ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print(f"\nBuilding SPY regime + texture data {START_DATE} → {END_DATE}...")
    spy_data = build_spy_data(START_DATE, END_DATE)
    n_gate = spy_data['texture_gate'].sum()
    n_total= len(spy_data)
    print(f"  SPY texture gate would fire on {n_gate}/{n_total} days ({n_gate/n_total*100:.1f}%) over 5 years\n")

    print(f"Collecting trade signals for {len(SYMBOLS)} symbols...")
    df_signals = collect_all_signals(spy_data)
    print(f"\nTotal raw signals: {len(df_signals)}")
    print(f"  Unique days: {df_signals['date'].nunique()}")
    print(f"  Signal days with gate: {df_signals[df_signals['texture_gate']]['date'].nunique()}")

    if df_signals.empty:
        print("No signals found."); exit()

    print(f"\nApplying portfolio caps (baseline max={MAX_POSITIONS_NORMAL}, gate max={MAX_POSITIONS_GATE})...")
    baseline, gate_result = apply_portfolio_caps(df_signals)

    print_comparison(baseline, gate_result, spy_data)
