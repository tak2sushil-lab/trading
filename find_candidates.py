#!/usr/bin/env python3
"""
find_candidates.py — DNA-fingerprint universe expansion

Screens a candidate pool through 5 gates in order:
  Rule 1: data_years >= 3
  Rule 2: DNA stability drift < 8pp (train → valid consistency)
  Rule 3: catalyst_wr >= cluster floor (INSTITUTIONAL ≥50%, MOMENTUM ≥52%, HIGH_VOL ≥55%)
  Rule 4: backtest WR >= 55% (5yr full signal stack)
  Rule 5: backtest profitable in >= 3 of the covered years

Assigns cluster by projecting onto the existing 110-symbol model from dna_analysis.py.
Only runs the slow backtest on symbols that pass rules 1-3.

Output:
  find_candidates_results.csv   — full results for every tested symbol
  stdout                        — ranked shortlist of PASS symbols + drop summary
"""

import os, sys
sys.path.insert(0, os.path.dirname(__file__))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date, timedelta, datetime
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
import warnings
warnings.filterwarnings('ignore')

# ── Import DNA functions from dna_analysis.py ──────────────────────
from dna_analysis import (
    fetch_daily, fetch_hourly,
    compute_daily_dna, compute_hourly_dna,
    CLUSTER_FEATURES, TRAIN_END, VALID_START, VALID_END, DAILY_START,
)

# ── Candidate pool (not in current FULL_UNIVERSE) ──────────────────
CANDIDATES = [
    # TECH — dropped in old gap-only backtest, re-test with full DNA
    'SNOW','PANW','CRWD','TTD','INTU','ADSK','HUBS','BILL','ZS','OKTA',
    'ZM','DOCU','CFLT','FTNT','GDDY','PINS','RBLX','TWLO','NOW','WDAY',
    # SEMIS gaps
    'MPWR','SWKS','CRUS','ONTO','WOLF','ACLS','ALGM','SITM','TXN','ADI',
    # INSTITUTIONAL blue-chip gaps (AXON-profile targets)
    'NDAQ','ICE','SPGI','MCO','CME','TT','CTAS','ROK',
    'FAST','ODFL','SAIA','GWW','IDXX','TDG','HWM',
    'ETN','EMR','HON','ITW','PH','GE',
    # FINTECH / FINANCIAL
    'SCHW','IBKR','LPLA','SYF','DFS','COF','AXP','BX','KKR',
    # BIOTECH / HEALTHCARE
    'VRTX','REGN','GILD','RGEN','EXAS','GEHC','BSX','EW','HOLX','MDT','ZBH',
    # CONSUMER
    'ONON','LULU','DECK','TPR','CPNG','CAVA','WING','SFM','MCD','TXRH','YUM',
    # ENERGY
    'MPC','VLO','PSX','CTRA','AR','EQT','WFRD',
    # DEFENCE gaps
    'BWXT','KTOS','LDOS','SAIC','CACI','HII','TXT',
    # CRYPTO-adjacent (IREN/MARA profile)
    'RIOT','HUT','CLSK','WULF','CIFR',
    # CLEAN ENERGY
    'ENPH','SEDG','ARRY','BE',
    # MINING / METALS
    'GOLD','AEM','WPM','AG','HL',
    # FINTECH 2.0 / PAYMENTS
    'AFRM','UPST','SQ','PYPL','MELI','SE',
    # HEALTHCARE INSURANCE
    'ELV','HCA','CI','MOH',
    # GROWTH MISC
    'CELH','DUOL','DKNG','LVS',
    # INDUSTRIAL
    'CARR','OTIS','GEV','MMM',
]

# ── Screening rules ─────────────────────────────────────────────────
RULE1_MIN_YEARS   = 3.0
RULE2_MAX_DRIFT   = 0.08     # 8pp train→valid DNA drift
RULE3_WR_FLOOR    = {'INSTITUTIONAL': 0.50, 'MOMENTUM': 0.52, 'HIGH_VOL': 0.55}
RULE4_MIN_BT_WR   = 55.0     # backtest win rate %
RULE5_MIN_PROF_YR = 3        # profitable years

# ── Backtest config (mirrors backtest_strategy.py) ──────────────────
BT_START          = '2020-01-01'
BT_END            = date.today().isoformat()
CAPITAL           = 2000
STOP_PCT          = 5.0
ATR_TRAIL_MULT    = 1.5
ATR_PERIOD        = 14
MIN_VOLUME_RATIO  = 1.3
MIN_TODAY_GAIN    = 1.5
CLUSTER_LABEL     = {0: 'HIGH_VOL', 1: 'MOMENTUM', 2: 'INSTITUTIONAL', 3: 'OUTLIER'}


# ── Load existing DNA model ─────────────────────────────────────────
def load_existing_model():
    path = os.path.join(os.path.dirname(__file__), 'dna_results.csv')
    if not os.path.exists(path):
        print("ERROR: dna_results.csv not found — run dna_analysis.py first")
        sys.exit(1)
    existing = pd.read_csv(path, index_col=0)
    feat_df  = existing[CLUSTER_FEATURES].fillna(existing[CLUSTER_FEATURES].median())
    scaler   = StandardScaler()
    X        = scaler.fit_transform(feat_df)
    km       = KMeans(n_clusters=4, random_state=42, n_init=20)
    km.fit(X)
    return scaler, km, existing


def assign_cluster(dna_dict, scaler, km):
    row = pd.DataFrame([dna_dict])[CLUSTER_FEATURES].fillna(0.5)
    X   = scaler.transform(row)
    raw = int(km.predict(X)[0])
    # Distance to assigned centroid (confidence — lower = tighter fit)
    dist = float(np.linalg.norm(X - km.cluster_centers_[raw]))
    return CLUSTER_LABEL.get(raw, 'UNKNOWN'), raw, dist


# ── DNA analysis for one candidate ─────────────────────────────────
def get_dna(sym):
    daily = fetch_daily(sym)
    if daily is None:
        return None, 'no data'

    train = daily[daily.index <= TRAIN_END]
    valid = daily[daily.index >= VALID_START]
    if len(daily) < 80:
        return None, f'only {len(daily)} days total'

    if len(train) < 80:
        train = daily   # short-history: use all

    dna = {'symbol': sym}
    dna.update(compute_daily_dna(train))

    if len(valid) >= 40:
        v = compute_daily_dna(valid)
        dna['val_gap_fill']  = v['gap_fill_rate']
        dna['val_gap_go']    = v['gap_go_rate']
        dna['val_trend_1d']  = v['trend_1d']
        dna['drift']         = abs(dna['gap_fill_rate'] - v['gap_fill_rate']) / 2 + \
                               abs(dna['gap_go_rate']   - v['gap_go_rate'])   / 2
    else:
        dna['val_gap_fill'] = dna['gap_fill_rate']
        dna['val_gap_go']   = dna['gap_go_rate']
        dna['val_trend_1d'] = dna['trend_1d']
        dna['drift']        = 0.0

    hourly = fetch_hourly(sym)
    dna.update(compute_hourly_dna(hourly))

    df_full = daily.copy()
    df_full['prev_close'] = df_full['Close'].shift(1)
    df_full['gap_pct']    = (df_full['Open'] - df_full['prev_close']) / df_full['prev_close'] * 100
    df_full['ret']        = df_full['Close'].pct_change() * 100
    df_full['vol_z']      = (df_full['Volume'] - df_full['Volume'].rolling(20).mean()) / \
                            (df_full['Volume'].rolling(20).std() + 1)
    cat = df_full[(df_full['gap_pct'].abs() > 0.5) & (df_full['vol_z'] > 1.0)]
    if len(cat) >= 8:
        dna['catalyst_wr']  = (cat['ret'] > 0).mean()
        dna['catalyst_avg'] = cat['ret'].mean()
    else:
        dna['catalyst_wr']  = 0.50
        dna['catalyst_avg'] = 0.0

    dna['data_years'] = round(len(daily) / 252, 1)
    return dna, daily


# ── SPY regime ──────────────────────────────────────────────────────
_spy_cache = None
def get_spy_regime():
    global _spy_cache
    if _spy_cache is None:
        spy = yf.download('SPY', start=BT_START, end=BT_END,
                          progress=False, auto_adjust=True)
        if isinstance(spy.columns, pd.MultiIndex):
            spy.columns = spy.columns.get_level_values(0)
        spy['spy_chg'] = spy['Close'].pct_change() * 100
        spy['regime']  = spy['spy_chg'].apply(
            lambda c: 'STRONG' if c >= 0.5 else ('WEAK' if c <= -0.5 else 'NORMAL'))
        _spy_cache = spy[['spy_chg', 'regime']]
    return _spy_cache


def add_atr(df):
    h, l, c = df['High'], df['Low'], df['Close']
    tr = pd.concat([h-l, (h-c.shift(1)).abs(), (l-c.shift(1)).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(ATR_PERIOD).mean()
    return df


def grade_day_bt(i, df, spy_chg, regime):
    row, prev = df.iloc[i], df.iloc[i-1]
    df_up     = df.iloc[:i+1]
    price, atr = row['Open'], row['atr']
    if pd.isna(atr) or atr <= 0 or pd.isna(price) or price < 5:
        return False, 0, False
    if regime == 'WEAK':
        return False, 0, False
    ma20 = df_up['Close'].rolling(20).mean().iloc[-1]
    if pd.isna(ma20) or price < ma20:
        return False, 0, False
    today_gain = (row['Close'] - prev['Close']) / prev['Close'] * 100
    if today_gain < MIN_TODAY_GAIN:
        return False, 0, False
    avg_vol   = df_up['Volume'].rolling(20).mean().iloc[-2] if len(df_up) >= 21 else None
    vol_ratio = float(row['Volume'] / avg_vol) if avg_vol and avg_vol > 0 else 1.0
    if vol_ratio < MIN_VOLUME_RATIO:
        return False, 0, False

    gap_pct      = (row['Open'] - prev['Close']) / prev['Close'] * 100
    vwap_est     = (row['Open'] + row['High'] + row['Low'] + row['Close']) / 4
    vwap_reclaim = row['Open'] < vwap_est and row['Close'] > vwap_est
    orb_proxy    = gap_pct >= 1.5 and row['Close'] >= row['Open'] * 0.998
    hod_break    = row['Close'] >= row['High'] * 0.990
    bull_flag    = False
    if len(df_up) >= 6:
        p3  = (float(df_up['Close'].iloc[-2]) - float(df_up['Close'].iloc[-5])) \
              / max(float(df_up['Close'].iloc[-5]), 0.01) * 100
        rng = (row['High'] - row['Low']) / max(row['Open'], 0.01) * 100
        bull_flag = p3 >= 5.0 and rng < 3.0 and row['Close'] > row['Open']
    rs_leader = (today_gain - spy_chg) >= 3.0 and today_gain >= 2.0
    if not (orb_proxy or vwap_reclaim or bull_flag or hod_break or today_gain >= 5.0 or rs_leader):
        return False, 0, False

    score = 0
    if orb_proxy:    score += 30
    if vwap_reclaim: score += 25
    if bull_flag:    score += 25
    if hod_break:    score += 20
    if rs_leader:    score += 20
    if vol_ratio >= 2.0:    score += 25
    elif vol_ratio >= 1.5:  score += 15
    else:                   score += 5
    ema8  = float(df_up['Close'].ewm(span=8).mean().iloc[-1])
    ema21 = float(df_up['Close'].ewm(span=21).mean().iloc[-1])
    if price > ema8 > ema21: score += 20
    elif price > ema21:      score += 10
    d  = df_up['Close'].diff()
    g  = d.clip(lower=0).rolling(14).mean().iloc[-1]
    lv = (-d.clip(upper=0)).rolling(14).mean().iloc[-1]
    rsi = round(float(100 - (100/(1+g/lv))), 1) if lv and lv > 0 else 50
    if 45 <= rsi <= 65:   score += 20
    elif 75 < rsi <= 80:  score += 5
    else:                 score += 5
    if today_gain >= 5.0:   score += 30
    elif today_gain >= 3.0: score += 20
    elif today_gain >= 1.5: score += 10
    if regime == 'STRONG':  score += 15
    elif regime == 'NORMAL': score += 5
    score += 5

    grade = 'A+' if score >= 80 else 'A' if score >= 65 else 'B'
    if grade == 'B':
        return False, score, False
    if spy_chg < 0 and grade != 'A+':
        return False, score, False

    vol_avg20       = df_up['Volume'].rolling(20).mean().iloc[-2]
    first_bar_strong = (gap_pct > 1.0 and not pd.isna(vol_avg20)
                        and vol_avg20 > 0 and row['Volume'] > vol_avg20 * 1.3)
    return True, score, first_bar_strong


def sim_trade(row, atr, fbs=False):
    cap    = CAPITAL * 1.15 if fbs else CAPITAL
    entry  = row['Open']
    sl     = entry * (1 - STOP_PCT / 100)
    one_r  = entry * (1 + STOP_PCT / 100)
    hit_1r = row['High'] >= one_r
    half   = cap / 2
    locked = (STOP_PCT / 100 * half) if (hit_1r and fbs) else 0.0
    rem    = half if (hit_1r and fbs) else cap
    if row['Low'] <= sl:
        if hit_1r and fbs:
            return round(locked - STOP_PCT / 100 * rem, 2)
        return round(-STOP_PCT * cap / 100, 2)
    trail = row['High'] - ATR_TRAIL_MULT * atr
    if row['Close'] < trail:
        return round(locked + (trail - entry) / entry * rem, 2)
    return round(locked + (row['Close'] - entry) / entry * rem, 2)


def run_backtest(sym, daily_df, spy_regime):
    df     = add_atr(daily_df.copy())
    merged = df.join(spy_regime, how='left')
    merged['spy_chg'] = merged['spy_chg'].fillna(0)
    merged['regime']  = merged['regime'].fillna('NORMAL')
    pnls, years = [], []
    for i in range(22, len(merged)):
        row = merged.iloc[i]
        ok, score, fbs = grade_day_bt(i, merged, float(row['spy_chg']), str(row['regime']))
        if not ok:
            continue
        pnl = sim_trade(row, float(row['atr']), fbs)
        pnls.append({'pnl': pnl, 'year': merged.index[i].year})
    if len(pnls) < 5:
        return None
    df_t   = pd.DataFrame(pnls)
    n      = len(df_t)
    wins   = (df_t['pnl'] > 0).sum()
    total  = df_t['pnl'].sum()
    wr     = wins / n * 100
    avg_w  = df_t[df_t['pnl'] > 0]['pnl'].mean() if wins else 0
    avg_l  = df_t[df_t['pnl'] <= 0]['pnl'].mean() if (n - wins) else 0
    ev     = (wr/100 * avg_w) + ((1-wr/100) * avg_l) if avg_l else avg_w
    by_yr  = df_t.groupby('year')['pnl'].sum()
    prof_yr = (by_yr > 0).sum()
    n_yr   = max(1, (df_t['year'].max() - df_t['year'].min() + 1))
    return dict(n=n, wr=wr, total=total, ann=total/n_yr, ev=ev,
                prof_yr=int(prof_yr), n_yr=int(n_yr), avg_w=avg_w, avg_l=avg_l)


# ── Main ─────────────────────────────────────────────────────────────
def main():
    scaler, km, existing_dna = load_existing_model()
    spy = get_spy_regime()

    print(f"find_candidates.py — {len(CANDIDATES)} candidates")
    print(f"Existing model: {len(existing_dna)} symbols | 4 clusters")
    print(f"Screening rules: ≥{RULE1_MIN_YEARS}yr | drift<{RULE2_MAX_DRIFT*100:.0f}pp | "
          f"cat_wr≥cluster_floor | bt_WR≥{RULE4_MIN_BT_WR:.0f}% | "
          f"profitable≥{RULE5_MIN_PROF_YR}yr\n")

    results = []
    drop_reasons = {}

    for i, sym in enumerate(CANDIDATES, 1):
        print(f"  [{i:3d}/{len(CANDIDATES)}] {sym:<6}", end='', flush=True)

        # ── Phase 1: DNA ─────────────────────────────────────────────
        dna, daily_or_err = get_dna(sym)
        if dna is None:
            drop_reasons[sym] = f'RULE1-fail: {daily_or_err}'
            print(f"  ✗ {daily_or_err}")
            continue

        # Rule 1: data years
        if dna['data_years'] < RULE1_MIN_YEARS:
            drop_reasons[sym] = f"RULE1-fail: {dna['data_years']:.1f}yr < {RULE1_MIN_YEARS}yr"
            print(f"  ✗ R1: only {dna['data_years']:.1f}yr data")
            continue

        # Assign cluster
        cluster, cluster_raw, dist = assign_cluster(dna, scaler, km)
        dna['cluster']     = cluster
        dna['cluster_raw'] = cluster_raw
        dna['cluster_dist'] = round(dist, 3)

        # Rule 2: DNA stability
        if dna['drift'] >= RULE2_MAX_DRIFT:
            drop_reasons[sym] = f"RULE2-fail: drift={dna['drift']:.1%} ≥ {RULE2_MAX_DRIFT*100:.0f}pp"
            print(f"  ✗ R2: drift {dna['drift']:.1%} unstable  [{cluster}]")
            continue

        # Rule 3: catalyst WR floor
        wr_floor = RULE3_WR_FLOOR.get(cluster, 0.52)
        if dna['catalyst_wr'] < wr_floor:
            drop_reasons[sym] = (f"RULE3-fail: cat_wr={dna['catalyst_wr']:.0%} < "
                                 f"floor {wr_floor:.0%} for {cluster}")
            print(f"  ✗ R3: cat_wr {dna['catalyst_wr']:.0%} < {wr_floor:.0%}  [{cluster}]")
            continue

        print(f"  ✓ DNA  atr={dna['avg_atr_pct']:.1f}%  cat_wr={dna['catalyst_wr']:.0%}  "
              f"drift={dna['drift']:.1%}  [{cluster} dist={dist:.2f}]", end='', flush=True)

        # ── Phase 2: Backtest ─────────────────────────────────────────
        bt = run_backtest(sym, daily_or_err, spy)
        if bt is None:
            drop_reasons[sym] = 'RULE4-fail: <5 backtest trades'
            print(f"  ✗ R4: <5 trades")
            continue

        # Rule 4: backtest WR
        if bt['wr'] < RULE4_MIN_BT_WR:
            drop_reasons[sym] = f"RULE4-fail: bt_wr={bt['wr']:.0f}% < {RULE4_MIN_BT_WR:.0f}%"
            print(f"  ✗ R4: bt_wr {bt['wr']:.0f}%  [{bt['n']}t]")
            continue

        # Rule 5: profitable years
        if bt['prof_yr'] < RULE5_MIN_PROF_YR:
            drop_reasons[sym] = f"RULE5-fail: profitable {bt['prof_yr']}/{bt['n_yr']}yr"
            print(f"  ✗ R5: only {bt['prof_yr']}/{bt['n_yr']} profitable years")
            continue

        # ── PASSED all 5 rules ────────────────────────────────────────
        rec = {**dna,
               'bt_n': bt['n'], 'bt_wr': bt['wr'], 'bt_ev': bt['ev'],
               'bt_ann': bt['ann'], 'bt_total': bt['total'],
               'bt_prof_yr': bt['prof_yr'], 'bt_n_yr': bt['n_yr'],
               'bt_avg_w': bt['avg_w'], 'bt_avg_l': bt['avg_l'],
               'passed': True}
        results.append(rec)
        print(f"  ✅ PASS  bt_wr={bt['wr']:.0f}%  ev=${bt['ev']:+.0f}  "
              f"ann=${bt['ann']:+,.0f}  prof={bt['prof_yr']}/{bt['n_yr']}yr")

    # ── Save full results ─────────────────────────────────────────────
    if results:
        df_out = pd.DataFrame(results).sort_values('bt_ev', ascending=False)
        out_path = os.path.join(os.path.dirname(__file__), 'find_candidates_results.csv')
        df_out.to_csv(out_path, index=False)
    else:
        df_out = pd.DataFrame()

    # ── Report ────────────────────────────────────────────────────────
    total_tested = len(CANDIDATES)
    total_passed = len(results)

    # Count drop reasons by rule
    from collections import Counter
    rule_drops = Counter(v.split(':')[0] for v in drop_reasons.values())

    print(f"\n{'='*72}")
    print(f"  RESULTS: {total_passed}/{total_tested} passed all 5 screening rules")
    print(f"{'='*72}")
    print(f"  Drop summary:")
    for rule, count in sorted(rule_drops.items()):
        print(f"    {rule}: {count} symbols")

    if df_out.empty:
        print("\n  No candidates passed all rules.")
        return

    print(f"\n  SHORTLIST — ranked by backtest EV (best → worst):")
    print(f"  {'Sym':<6} {'Cluster':<16} {'Dist':>5} {'Yrs':>5} {'Cat%':>6} "
          f"{'Drft':>5} {'BT_N':>5} {'WR':>6} {'EV':>7} {'Ann$':>8} "
          f"{'P/N':>5} {'ATR':>5}")
    print(f"  {'─'*90}")

    for _, r in df_out.iterrows():
        pn = f"{int(r['bt_prof_yr'])}/{int(r['bt_n_yr'])}"
        print(f"  {r['symbol']:<6} {r['cluster']:<16} {r['cluster_dist']:>5.2f} "
              f"{r['data_years']:>5.1f} {r['catalyst_wr']:>5.0%} "
              f"{r['drift']:>5.1%} {int(r['bt_n']):>5} "
              f"{r['bt_wr']:>5.0f}% ${r['bt_ev']:>+6.0f} "
              f"${r['bt_ann']:>+7,.0f} {pn:>5}  {r['avg_atr_pct']:>4.1f}%")

    # Cluster breakdown
    print(f"\n  BY CLUSTER:")
    for cl in ['INSTITUTIONAL','MOMENTUM','HIGH_VOL']:
        sub = df_out[df_out['cluster'] == cl]
        if sub.empty:
            continue
        syms = sub['symbol'].tolist()
        print(f"  {cl} ({len(syms)}): {', '.join(syms)}")

    # Closest matches to anchor symbols
    print(f"\n  CLOSEST TO EXISTING ANCHORS (cluster_dist — lower = tighter fit):")
    top_close = df_out.nsmallest(10, 'cluster_dist')
    for _, r in top_close.iterrows():
        print(f"    {r['symbol']:<6} {r['cluster']:<16}  dist={r['cluster_dist']:.2f}  "
              f"WR={r['bt_wr']:.0f}%  EV=${r['bt_ev']:+.0f}")

    print(f"\n  Full results → find_candidates_results.csv")
    print(f"{'='*72}")


if __name__ == '__main__':
    main()
