#!/usr/bin/env python3
"""
backtest_dna.py — DNA-modified strategy backtest (A/B comparison)

Loads dna_clusters.csv to identify stock personality:
  HIGH_VOL   (cluster 0, ATR >6%, gap_fill >65%):
    → Penalise raw ORB without VWAP confirmation (-15 pts)
    → Bonus for VWAP reclaim (+15 pts) — pullback confirmed
  MOMENTUM   (cluster 1, ATR 4-6%, gap_go ~70%):
    → No change — baseline scoring works fine
  INSTITUTIONAL (cluster 2, ATR <4%, gap_go ~75%):
    → Small ORB bonus (+5 pts) — gaps stick in direction more reliably
  OUTLIER    (cluster 3 / unknown): baseline scoring

Run:
  venv/bin/python backtest_dna.py                        # full universe A/B
  venv/bin/python backtest_dna.py MARA SOUN BBAI IREN    # selected symbols
"""

import os, sys
sys.path.insert(0, os.path.dirname(__file__))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date
import warnings
warnings.filterwarnings('ignore')

# ── Config (mirrors backtest_strategy.py) ──────────────────────────
START_DATE        = '2020-01-01'
END_DATE          = date.today().isoformat()
CAPITAL_PER_TRADE = 2000
ATR_PERIOD        = 14
STOP_PCT          = 5.0
ATR_TRAIL_MULT    = 1.5
MIN_RR            = 2.5
MIN_VOLUME_RATIO  = 1.3
MIN_TODAY_GAIN    = 1.5
SKIP_WEAK_DAYS    = True
FIRST_BAR_QUALITY = True

# ── Load DNA clusters ───────────────────────────────────────────────
def load_dna():
    path = os.path.join(os.path.dirname(__file__), 'dna_clusters.csv')
    if not os.path.exists(path):
        print("WARNING: dna_clusters.csv not found — run dna_analysis.py first")
        return {}
    df = pd.read_csv(path, index_col=0)
    return df['cluster_raw'].to_dict()   # {symbol: 0|1|2|3}

# Cluster 0 = HIGH_VOL (ATR 8.3%, gap_fill 70%)
# Cluster 1 = MOMENTUM (ATR 5.0%, gap_go  71%)
# Cluster 2 = INSTITUTIONAL (ATR 3.1%, gap_go 75%)
# Cluster 3 = OUTLIER  (USAR only)
CLUSTER_LABEL = {0: 'HIGH_VOL', 1: 'MOMENTUM', 2: 'INSTITUTIONAL', 3: 'OUTLIER'}

DNA_CLUSTERS = load_dna()   # populated at import time

# ── Universe (full, for default run) ───────────────────────────────
FULL_UNIVERSE = [
    'AAPL','PLTR','COHR','IONQ','HOOD','JPM','IREN','NUTX',
    'LITE','VST','ITA','NFLX','ORCL','OKLO','AMZN','GOOGL',
    'CRM','QBTS','TOST','AVGO','NBIS','CLS','RKLB','CNQ',
    'AMD','RKT','NU','MSFT','META','GS',
    'CRWV','SMCI','RBRK','AI','RGTI',
    'USAR','FSLR','CCJ','UUUU','DNN',
    'LLY','NTLA','BEAM',
    'APLD','SOUN','BBAI',
    'ON','LRCX','DDOG','MDB',
    'POET','EOSE','INDI','NVDA','INTC','TSLA',
    'CVX','XOM','OXY','SLB','HAL','DVN','XLE',
    'UNH','MRNA','PFE','ABBV','ISRG','DXCM','HIMS','XBI',
    'COST','NKE','SBUX','CMG','UBER',
    'BAC','C','WFC','V','MA','COIN',
    'RTX','LMT','NOC','CAT','DE',
    'QCOM','MRVL','KLAC','AMAT','MU','SMH',
    'LAC','RIVN','NIO','CHPT',
    'FCX','NEM','MP',
    'APP','MARA','ARM','AXON','SHOP',
    'MSTR','ONDS','RDW','VERI','JOBY',
]

SYMBOLS = sys.argv[1:] if len(sys.argv) > 1 else FULL_UNIVERSE

# ── SPY regime ──────────────────────────────────────────────────────
def build_spy_regime(start, end):
    spy = yf.download('SPY', start=start, end=end, progress=False, auto_adjust=True)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)
    spy['spy_chg'] = spy['Close'].pct_change() * 100
    spy['regime']  = spy['spy_chg'].apply(
        lambda c: 'STRONG' if c >= 0.5 else ('WEAK' if c <= -0.5 else 'NORMAL')
    )
    return spy[['spy_chg', 'regime']]

def add_atr(df):
    h, l, c = df['High'], df['Low'], df['Close']
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(ATR_PERIOD).mean()
    return df

# ── Grade a day's setup ─────────────────────────────────────────────
def grade_day(i, df, spy_chg, regime, symbol, use_dna=True):
    row      = df.iloc[i]
    prev_row = df.iloc[i - 1]
    df_upto  = df.iloc[:i + 1]

    price = row['Open']
    atr   = row['atr']
    if pd.isna(atr) or atr <= 0 or pd.isna(price) or price < 5:
        return 'SKIP', 0, [], False, 'UNKNOWN'

    if SKIP_WEAK_DAYS and regime == 'WEAK':
        return 'SKIP', 0, [], False, 'UNKNOWN'

    ma20 = df_upto['Close'].rolling(20).mean().iloc[-1]
    if pd.isna(ma20) or price < ma20:
        return 'SKIP', 0, [], False, 'UNKNOWN'

    today_gain = (row['Close'] - prev_row['Close']) / prev_row['Close'] * 100
    if today_gain < MIN_TODAY_GAIN:
        return 'SKIP', 0, [], False, 'UNKNOWN'

    avg_vol   = df_upto['Volume'].rolling(20).mean().iloc[-2] if len(df_upto) >= 21 else None
    vol_ratio = float(row['Volume'] / avg_vol) if avg_vol and avg_vol > 0 else 1.0
    if vol_ratio < MIN_VOLUME_RATIO:
        return 'SKIP', 0, [], False, 'UNKNOWN'

    # ── Patterns ────────────────────────────────────────────────────
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

    strong_momo = today_gain >= 5.0
    rs_vs_spy   = today_gain - spy_chg
    rs_leader   = rs_vs_spy >= 3.0 and today_gain >= 2.0

    has_pattern = orb_proxy or vwap_reclaim or bull_flag or hod_break or strong_momo or rs_leader
    if not has_pattern:
        return 'SKIP', 0, [], False, 'UNKNOWN'

    # ── Baseline scoring ─────────────────────────────────────────────
    score   = 0
    reasons = []

    if orb_proxy:    score += 30; reasons.append('ORB')
    if vwap_reclaim: score += 25; reasons.append('VWAP reclaim')
    if bull_flag:    score += 25; reasons.append('Bull flag')
    if hod_break:    score += 20; reasons.append('HOD break')
    if rs_leader:    score += 20; reasons.append(f'RS +{rs_vs_spy:.1f}%')

    if vol_ratio >= 2.0:   score += 25; reasons.append(f'{vol_ratio:.1f}x vol')
    elif vol_ratio >= 1.5: score += 15; reasons.append(f'{vol_ratio:.1f}x vol')
    else:                  score += 5;  reasons.append(f'{vol_ratio:.1f}x vol')

    ema8  = float(df_upto['Close'].ewm(span=8).mean().iloc[-1])
    ema21 = float(df_upto['Close'].ewm(span=21).mean().iloc[-1])
    if price > ema8 > ema21: score += 20; reasons.append('EMA uptrend')
    elif price > ema21:      score += 10; reasons.append('Above EMA21')

    d = df_upto['Close'].diff()
    g = d.clip(lower=0).rolling(14).mean().iloc[-1]
    l = (-d.clip(upper=0)).rolling(14).mean().iloc[-1]
    rsi = round(float(100 - (100 / (1 + g / l))), 1) if l and l > 0 else 50
    if 45 <= rsi <= 65:    score += 20; reasons.append(f'RSI {rsi:.0f}')
    elif 65 < rsi <= 75:   pass
    elif 75 < rsi <= 80:   score += 5;  reasons.append(f'RSI {rsi:.0f} strong')
    else:                  score += 5

    if today_gain >= 5.0:   score += 30; reasons.append(f'+{today_gain:.1f}%')
    elif today_gain >= 3.0: score += 20; reasons.append(f'+{today_gain:.1f}%')
    elif today_gain >= 1.5: score += 10; reasons.append(f'+{today_gain:.1f}%')

    if regime == 'STRONG': score += 15; reasons.append('Strong mkt')
    elif regime == 'NORMAL': score += 5

    score += 5  # R:R gate always passes

    # ── DNA modifier (the new layer) ─────────────────────────────────
    raw_cluster = DNA_CLUSTERS.get(symbol)
    dna_label   = CLUSTER_LABEL.get(raw_cluster, 'UNKNOWN')

    if use_dna and raw_cluster is not None:
        if dna_label == 'HIGH_VOL':
            # Gap fills 70% intraday — ORB fires before the pullback.
            # Penalise naked ORB; reward VWAP reclaim (implies pullback + recovery).
            if orb_proxy and not vwap_reclaim:
                score -= 15
                reasons.append('⚠ HIGH_VOL: ORB-15 (pullback expected)')
            if vwap_reclaim:
                score += 15
                reasons.append('✓ HIGH_VOL: VWAP+15 (pullback confirmed)')

        elif dna_label == 'INSTITUTIONAL':
            # Gaps stick 75% of the time — ORB is more reliable here.
            if orb_proxy:
                score += 5
                reasons.append('✓ INST: ORB+5 (gap sticks 75%)')

        # MOMENTUM: no change — standard scoring works

    score = max(score, 0)  # floor at 0
    grade = 'A+' if score >= 80 else 'A' if score >= 65 else 'B' if score >= 50 else 'C'

    if grade in ('B', 'C'):
        return 'SKIP', score, reasons, False, dna_label
    if spy_chg < 0 and grade != 'A+':
        return 'SKIP', score, reasons, False, dna_label

    vol_avg20       = df_upto['Volume'].rolling(20).mean().iloc[-2]
    first_bar_strong = (
        FIRST_BAR_QUALITY
        and gap_pct > 1.0
        and not pd.isna(vol_avg20) and vol_avg20 > 0
        and row['Volume'] > vol_avg20 * 1.3
    )

    return grade, score, reasons, first_bar_strong, dna_label


# ── Simulate one trade ──────────────────────────────────────────────
def simulate_trade(row, atr, first_bar_strong=False, _always_partial=False):
    capital    = CAPITAL_PER_TRADE * 1.15 if (FIRST_BAR_QUALITY and first_bar_strong) else CAPITAL_PER_TRADE
    do_partial = _always_partial or (not FIRST_BAR_QUALITY) or first_bar_strong

    entry  = row['Open']
    sl     = round(entry * (1 - STOP_PCT / 100), 2)
    one_r  = entry * (1 + STOP_PCT / 100)
    hit_1r = row['High'] >= one_r

    half_cap       = capital / 2
    partial_locked = (STOP_PCT / 100 * half_cap) if (hit_1r and do_partial) else 0.0
    rem_cap        = (half_cap if (hit_1r and do_partial) else capital)

    if row['Low'] <= sl:
        if hit_1r and do_partial:
            rest_loss = -STOP_PCT / 100 * rem_cap
            total_pnl = round(partial_locked + rest_loss, 2)
            return 'PARTIAL_STOP', round(total_pnl / capital * 100, 2), total_pnl, sl
        return 'STOP', round(-STOP_PCT, 2), round(-STOP_PCT * capital / 100, 2), sl

    trail_stop = row['High'] - ATR_TRAIL_MULT * atr
    if row['Close'] < trail_stop:
        rest_pnl  = (trail_stop - entry) / entry * rem_cap
        total_pnl = round(partial_locked + rest_pnl, 2)
        result    = 'WIN' if total_pnl > 0 else 'FADE'
        return result, round(total_pnl / capital * 100, 2), total_pnl, trail_stop

    rest_pnl  = (row['Close'] - entry) / entry * rem_cap
    total_pnl = round(partial_locked + rest_pnl, 2)
    result    = 'WIN' if total_pnl > 0 else 'LOSS'
    return result, round(total_pnl / capital * 100, 2), total_pnl, row['Close']


# ── Backtest one symbol (both modes) ───────────────────────────────
def backtest_symbol(symbol, spy_regime):
    df = yf.download(symbol, start=START_DATE, end=END_DATE,
                     progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if len(df) < 60:
        return pd.DataFrame(), pd.DataFrame()

    df     = add_atr(df)
    merged = df.join(spy_regime, how='left')
    merged['spy_chg'] = merged['spy_chg'].fillna(0)
    merged['regime']  = merged['regime'].fillna('NORMAL')

    trades_base, trades_dna = [], []

    for i in range(22, len(merged)):
        row     = merged.iloc[i]
        spy_chg = float(row['spy_chg'])
        regime  = str(row['regime'])
        atr_val = float(row['atr'])

        # Baseline (DNA off)
        grade_b, score_b, reasons_b, fbs_b, dna_lbl = grade_day(
            i, merged, spy_chg, regime, symbol, use_dna=False)

        # DNA-modified (DNA on)
        grade_d, score_d, reasons_d, fbs_d, _ = grade_day(
            i, merged, spy_chg, regime, symbol, use_dna=True)

        if grade_b != 'SKIP':
            res, pnl_pct, pnl_usd, ep = simulate_trade(row, atr_val, fbs_b)
            trades_base.append({
                'date': merged.index[i].strftime('%Y-%m-%d'),
                'year': merged.index[i].year,
                'symbol': symbol,
                'grade': grade_b, 'score': score_b,
                'regime': regime, 'spy_chg': round(spy_chg, 2),
                'entry': round(row['Open'], 2),
                'pnl_usd': pnl_usd, 'pnl_pct': pnl_pct,
                'result': res,
                'dna_cluster': dna_lbl,
                'patterns': ' | '.join(reasons_b[:5]),
            })

        if grade_d != 'SKIP':
            res, pnl_pct, pnl_usd, ep = simulate_trade(row, atr_val, fbs_d)
            trades_dna.append({
                'date': merged.index[i].strftime('%Y-%m-%d'),
                'year': merged.index[i].year,
                'symbol': symbol,
                'grade': grade_d, 'score': score_d,
                'regime': regime, 'spy_chg': round(spy_chg, 2),
                'entry': round(row['Open'], 2),
                'pnl_usd': pnl_usd, 'pnl_pct': pnl_pct,
                'result': res,
                'dna_cluster': dna_lbl,
                'patterns': ' | '.join(reasons_d[:5]),
            })

    return pd.DataFrame(trades_base), pd.DataFrame(trades_dna)


# ── Summary stats ───────────────────────────────────────────────────
def stats(df):
    if df.empty:
        return dict(n=0, wr=0, avg_w=0, avg_l=0, total=0, ann=0, ev=0)
    n   = len(df)
    w   = df[df['pnl_usd'] > 0]
    l   = df[df['pnl_usd'] <= 0]
    tot = df['pnl_usd'].sum()
    wr  = len(w) / n * 100
    aw  = w['pnl_usd'].mean() if len(w) else 0
    al  = l['pnl_usd'].mean() if len(l) else 0
    n_yr = max(1, (pd.to_datetime(df['date'].max()) -
                   pd.to_datetime(df['date'].min())).days / 365)
    ev  = (wr/100 * aw) + ((1-wr/100) * al) if al else aw
    return dict(n=n, wr=wr, avg_w=aw, avg_l=al, total=tot,
                ann=tot/n_yr, ev=ev, n_yr=n_yr)


# ── Print comparison ────────────────────────────────────────────────
def print_comparison(all_base, all_dna):
    b = stats(all_base)
    d = stats(all_dna)

    print(f"\n{'='*72}")
    print(f"  DNA BACKTEST COMPARISON  {START_DATE} → {END_DATE}")
    print(f"  {len(SYMBOLS)} symbols | ${CAPITAL_PER_TRADE}/trade | 5% stop")
    print(f"{'='*72}")

    print(f"\n  {'Metric':<28} {'BASELINE':>12} {'DNA-MOD':>12} {'DELTA':>12}")
    print(f"  {'─'*64}")

    def row(label, bv, dv, fmt='+,.0f', suffix=''):
        delta = dv - bv
        sign  = '+' if delta >= 0 else ''
        bfmt  = f'{bv:{fmt}}{suffix}'
        dfmt  = f'{dv:{fmt}}{suffix}'
        efmt  = f'{sign}{delta:{fmt.lstrip("+")}}{suffix}'
        print(f"  {label:<28} {bfmt:>12} {dfmt:>12} {efmt:>12}")

    row('Trades taken',       b['n'],    d['n'],    '.0f')
    row('Win rate',           b['wr'],   d['wr'],   '.1f', '%')
    row('Avg winner ($)',     b['avg_w'],d['avg_w'],'+.2f')
    row('Avg loser ($)',      b['avg_l'],d['avg_l'],'+.2f')
    row('Expected value ($)', b['ev'],   d['ev'],   '+.2f')
    row('Total P&L ($)',      b['total'],d['total'],'+,.0f')
    row('Annual return ($)',  b['ann'],  d['ann'],  '+,.0f')

    ev_delta  = d['ev']  - b['ev']
    ann_delta = d['ann'] - b['ann']
    print(f"\n  EV lift    : {ev_delta:+.2f}/trade  ({ev_delta/b['ev']*100:+.1f}% vs baseline)" if b['ev'] else "")
    print(f"  Annual lift: {ann_delta:+,.0f}/yr")

    # ── By DNA cluster ──────────────────────────────────────────────
    print(f"\n  {'─'*72}")
    print(f"  BY DNA CLUSTER")
    print(f"  {'─'*72}")
    print(f"  {'Cluster':<20} {'BASE N':>7} {'BASE WR':>8} {'BASE EV':>9} "
          f"{'DNA N':>7} {'DNA WR':>8} {'DNA EV':>9} {'EV DELTA':>10}")
    print(f"  {'─'*72}")

    all_clusters = sorted(set(
        list(all_base['dna_cluster'].unique()) +
        list(all_dna['dna_cluster'].unique())
    ))
    for cl in all_clusters:
        sb = all_base[all_base['dna_cluster'] == cl]
        sd = all_dna[all_dna['dna_cluster'] == cl]
        if sb.empty and sd.empty:
            continue
        bb = stats(sb)
        dd = stats(sd)
        ev_d = dd['ev'] - bb['ev']
        print(f"  {cl:<20} {bb['n']:>7} {bb['wr']:>7.1f}% {bb['ev']:>+9.2f} "
              f"{dd['n']:>7} {dd['wr']:>7.1f}% {dd['ev']:>+9.2f} {ev_d:>+10.2f}")

    # ── By year ─────────────────────────────────────────────────────
    print(f"\n  {'─'*72}")
    print(f"  YEAR-BY-YEAR  (total P&L across all symbols)")
    print(f"  {'─'*72}")
    print(f"  {'Year':<6} {'BASE P&L':>12} {'DNA P&L':>12} {'DELTA':>12}")
    print(f"  {'─'*44}")
    all_years = sorted(set(list(all_base['year'].unique()) + list(all_dna['year'].unique())))
    for yr in all_years:
        bp = all_base[all_base['year'] == yr]['pnl_usd'].sum()
        dp = all_dna[all_dna['year'] == yr]['pnl_usd'].sum()
        tag = '✅' if dp > 0 else '❌'
        base_tag = '' if bp > 0 else '❌'
        print(f"  {yr:<6} {bp:>+12,.0f}{base_tag}  {dp:>+11,.0f} {tag}  {dp-bp:>+10,.0f}")

    # ── HIGH_VOL cluster detail (key focus) ─────────────────────────
    hv_b = all_base[all_base['dna_cluster'] == 'HIGH_VOL']
    hv_d = all_dna[all_dna['dna_cluster'] == 'HIGH_VOL']

    if not hv_b.empty:
        print(f"\n  {'─'*72}")
        print(f"  HIGH_VOL CLUSTER DETAIL  (gap-fill stocks — pullback logic)")
        print(f"  {'─'*72}")
        # ORB-only trades removed by DNA (base had ORB, DNA dropped them)
        orb_base = hv_b[hv_b['patterns'].str.contains('ORB')]
        print(f"  Base ORB trades from HIGH_VOL: {len(orb_base)}  "
              f"WR={len(orb_base[orb_base['pnl_usd']>0])/max(len(orb_base),1)*100:.0f}%  "
              f"avg=${orb_base['pnl_usd'].mean():+.2f}")
        vwap_base = hv_b[hv_b['patterns'].str.contains('VWAP')]
        print(f"  Base VWAP trades from HIGH_VOL: {len(vwap_base)}  "
              f"WR={len(vwap_base[vwap_base['pnl_usd']>0])/max(len(vwap_base),1)*100:.0f}%  "
              f"avg=${vwap_base['pnl_usd'].mean():+.2f}")
        vwap_dna = hv_d[hv_d['patterns'].str.contains('VWAP')]
        print(f"  DNA  VWAP trades from HIGH_VOL: {len(vwap_dna)}  "
              f"WR={len(vwap_dna[vwap_dna['pnl_usd']>0])/max(len(vwap_dna),1)*100:.0f}%  "
              f"avg=${vwap_dna['pnl_usd'].mean():+.2f}")

    # ── HIGH_VOL per-symbol drill-down ──────────────────────────────
    if not hv_b.empty:
        syms_hv = sorted(hv_b['symbol'].unique())
        print(f"\n  HIGH_VOL per-symbol (base vs DNA):")
        print(f"  {'Sym':<6} {'BASE N':>7} {'BASE WR':>8} {'BASE EV':>9} "
              f"{'DNA N':>7} {'DNA WR':>8} {'DNA EV':>9} {'EV DELTA':>10}")
        print(f"  {'─'*66}")
        for sym in syms_hv:
            sb = hv_b[hv_b['symbol'] == sym]
            sd = hv_d[hv_d['symbol'] == sym]
            bb = stats(sb); dd = stats(sd)
            ev_d = dd['ev'] - bb['ev']
            flag = ' ✅' if ev_d > 0 else (' ❌' if ev_d < -1 else '')
            print(f"  {sym:<6} {bb['n']:>7} {bb['wr']:>7.1f}% {bb['ev']:>+9.2f} "
                  f"{dd['n']:>7} {dd['wr']:>7.1f}% {dd['ev']:>+9.2f} {ev_d:>+10.2f}{flag}")

    print(f"\n{'='*72}")
    verdict = '✅ DNA MODIFIERS IMPROVE EV' if d['ev'] > b['ev'] else '❌ DNA MODIFIERS HURT EV — DO NOT DEPLOY'
    print(f"  {verdict}")
    if d['ann'] > b['ann']:
        print(f"  Annual gain:  +${d['ann']-b['ann']:,.0f}/yr on full universe")
    print(f"{'='*72}\n")


# ── Main ─────────────────────────────────────────────────────────────
def main():
    print(f"DNA Backtest: {len(SYMBOLS)} symbols | {START_DATE} → {END_DATE}")
    print(f"DNA clusters loaded: {len(DNA_CLUSTERS)} symbols mapped")
    if not DNA_CLUSTERS:
        print("ERROR: No DNA data. Run dna_analysis.py first."); return

    spy = build_spy_regime(START_DATE, END_DATE)

    all_base_frames, all_dna_frames = [], []

    for i, sym in enumerate(SYMBOLS, 1):
        cl = CLUSTER_LABEL.get(DNA_CLUSTERS.get(sym), 'UNKNOWN')
        print(f"  [{i:3d}/{len(SYMBOLS)}] {sym:<6}  cluster={cl}", end='', flush=True)
        base, dna = backtest_symbol(sym, spy)
        if not base.empty:
            all_base_frames.append(base)
            all_dna_frames.append(dna)
            bb = stats(base); dd = stats(dna)
            ev_delta = dd['ev'] - bb['ev']
            print(f"  base={bb['n']}t {bb['wr']:.0f}%WR ${bb['ev']:+.2f}ev  "
                  f"dna={dd['n']}t {dd['wr']:.0f}%WR ${dd['ev']:+.2f}ev  "
                  f"Δ={ev_delta:+.2f}")
        else:
            print("  (no trades)")

    if not all_base_frames:
        print("No trades."); return

    all_base = pd.concat(all_base_frames, ignore_index=True)
    all_dna  = pd.concat(all_dna_frames,  ignore_index=True)

    # Save for further analysis
    all_dna.to_csv(os.path.join(os.path.dirname(__file__), 'backtest_dna_results.csv'), index=False)

    print_comparison(all_base, all_dna)


if __name__ == '__main__':
    main()
