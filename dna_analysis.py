#!/usr/bin/env python3
"""
dna_analysis.py — Per-symbol signal DNA characterization

Derives stock "fingerprints" from 5Y daily + 2Y hourly data.
Train: 2020-2023 | Validate: 2024-2025

Characteristics computed:
  Daily (5Y): gap behavior, trend persistence, reversal tendency,
              volume character, volatility profile, intraday reversion
  Hourly (2Y): FVG density/fill, ORB breakout/hold, VWAP reclaim,
               morning momentum continuation

Output:
  dna_results.csv    — full feature table per symbol
  dna_clusters.csv   — cluster assignment + defining traits
  stdout             — ranked cluster summary table
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
import warnings
warnings.filterwarnings('ignore')

# ── Universe ────────────────────────────────────────────────────
UNIVERSE = [
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

TRAIN_END   = '2023-12-31'
VALID_START = '2024-01-01'
VALID_END   = '2025-12-31'
DAILY_START = '2020-01-01'

# Known high-performers for cluster anchor reference
HIGH_PERF = {'AXON','APP','SHOP','ORCL','PLTR','NFLX','AAPL','MSFT','NVDA'}


# ── Data fetching ───────────────────────────────────────────────

def fetch_daily(sym):
    try:
        df = yf.download(sym, start=DAILY_START, end=VALID_END,
                         interval='1d', progress=False, auto_adjust=True)
        if len(df) < 50:
            return None
        # Flatten multi-index columns yfinance sometimes returns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index)
        return df
    except Exception as e:
        print(f"  {sym}: daily fetch error — {e}")
        return None


def fetch_hourly(sym):
    try:
        end = datetime.now()
        start = end - timedelta(days=729)
        df = yf.download(sym,
                         start=start.strftime('%Y-%m-%d'),
                         end=end.strftime('%Y-%m-%d'),
                         interval='1h', progress=False, auto_adjust=True)
        if len(df) < 100:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index)
        return df
    except Exception as e:
        print(f"  {sym}: hourly fetch error — {e}")
        return None


# ── Daily DNA ───────────────────────────────────────────────────

def compute_daily_dna(df):
    r = {}
    df = df.copy()
    df['prev_close'] = df['Close'].shift(1)
    df['gap_pct']    = (df['Open'] - df['prev_close']) / df['prev_close'] * 100
    df['ret']        = df['Close'].pct_change() * 100
    df['range_pct']  = (df['High'] - df['Low']) / df['prev_close'].replace(0, np.nan) * 100
    df['vol_z']      = (df['Volume'] - df['Volume'].rolling(20).mean()) / \
                       (df['Volume'].rolling(20).std() + 1)

    # ATR
    tr = pd.concat([
        df['High'] - df['Low'],
        (df['High'] - df['prev_close']).abs(),
        (df['Low']  - df['prev_close']).abs(),
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    df['atr_pct'] = df['atr'] / df['Close'].replace(0, np.nan) * 100

    # ── Gap behavior ──────────────────────────────────────────
    gd = df[df['gap_pct'].abs() > 0.5].copy()
    if len(gd) >= 15:
        gd['filled'] = np.where(
            gd['gap_pct'] > 0,
            gd['Low']  <= gd['prev_close'],
            gd['High'] >= gd['prev_close'],
        )
        gd['held'] = np.where(
            gd['gap_pct'] > 0,
            gd['Close'] > gd['prev_close'],
            gd['Close'] < gd['prev_close'],
        )
        r['gap_fill_rate'] = gd['filled'].mean()
        r['gap_go_rate']   = gd['held'].mean()
    else:
        r['gap_fill_rate'] = 0.50
        r['gap_go_rate']   = 0.50

    # ── Trend continuation after strong move ──────────────────
    strong = df['ret'] > 3.0
    if strong.sum() >= 8:
        fwd1 = df['Close'].shift(-1) / df['Close'] - 1
        fwd3 = df['Close'].shift(-3) / df['Close'] - 1
        r['trend_1d'] = (fwd1[strong] > 0).mean()
        r['trend_3d'] = (fwd3[strong] > 0).mean()
    else:
        r['trend_1d'] = 0.50
        r['trend_3d'] = 0.50

    # ── Reversal tendency after consecutive moves ─────────────
    streaks, streak = [], 0
    for ret in df['ret']:
        if pd.isna(ret):   streak = 0
        elif ret > 0:      streak = max(streak + 1,  1)
        else:              streak = min(streak - 1, -1)
        streaks.append(streak)
    df['streak'] = streaks

    next_ret = df['ret'].shift(-1)
    bull_rev = df['streak'] >= 2
    bear_rev = df['streak'] <= -2
    if bull_rev.sum() >= 8 and bear_rev.sum() >= 8:
        r['reversal_tendency'] = (
            (next_ret[bull_rev] < 0).mean() +
            (next_ret[bear_rev] > 0).mean()
        ) / 2
    else:
        r['reversal_tendency'] = 0.50

    # ── Volume character ──────────────────────────────────────
    v = df.dropna(subset=['vol_z', 'ret'])
    if len(v) >= 40:
        r['vol_move_corr'] = v['vol_z'].corr(v['ret'].abs())
        up_vol = v[v['ret'] > 0]['Volume'].mean()
        dn_vol = v[v['ret'] < 0]['Volume'].mean()
        r['up_vol_ratio']  = up_vol / dn_vol if dn_vol > 0 else 1.0
    else:
        r['vol_move_corr'] = 0.30
        r['up_vol_ratio']  = 1.00

    # ── Volatility ───────────────────────────────────────────
    r['avg_atr_pct']   = df['atr_pct'].dropna().mean()
    r['avg_range_pct'] = df['range_pct'].dropna().mean()

    cr = df['ret'].dropna()
    r['return_skew']    = float(cr.skew()) if len(cr) > 30 else 0.0
    r['vol_clustering'] = float(cr.abs().autocorr(lag=1)) if len(cr) > 30 else 0.0

    # ── Intraday reversion proxy (daily) ─────────────────────
    df['intraday_revert'] = np.where(
        df['gap_pct'] > 0,
        df['Close'] < df['Open'],
        df['Close'] > df['Open'],
    )
    r['intraday_revert_rate'] = df['intraday_revert'].mean()

    return r


# ── Hourly DNA ──────────────────────────────────────────────────

def compute_hourly_dna(df_h):
    blank = dict(fvg_density=0.0, fvg_fill_rate=0.50,
                 orb_breakout_rate=0.50, orb_hold_1h=0.50,
                 vwap_reclaim_rate=0.50, morning_continuation=0.0)
    if df_h is None or len(df_h) < 100:
        return blank

    r = {}
    df = df_h.copy()
    df.index = pd.to_datetime(df.index)

    # ── FVG density & fill ────────────────────────────────────
    H, L = df['High'].values, df['Low'].values
    n = len(H)
    bull_fvgs, bear_fvgs = [], []
    for i in range(1, n - 1):
        if H[i-1] < L[i+1]:
            bull_fvgs.append((i, H[i-1], L[i+1]))
        elif L[i-1] > H[i+1]:
            bear_fvgs.append((i, H[i+1], L[i-1]))

    total_fvgs = len(bull_fvgs) + len(bear_fvgs)
    r['fvg_density'] = total_fvgs / (n / 100)

    if total_fvgs > 0:
        fills = 0
        for idx, g_lo, g_hi in bull_fvgs:
            mid = g_lo + (g_hi - g_lo) * 0.5
            future_lows = df['Low'].values[idx+2:idx+12]
            if len(future_lows) > 0 and (future_lows <= mid).any():
                fills += 1
        for idx, g_lo, g_hi in bear_fvgs:
            mid = g_lo + (g_hi - g_lo) * 0.5
            future_highs = df['High'].values[idx+2:idx+12]
            if len(future_highs) > 0 and (future_highs >= mid).any():
                fills += 1
        r['fvg_fill_rate'] = fills / total_fvgs
    else:
        r['fvg_fill_rate'] = 0.50

    # ── ORB: first-hour breakout analysis ─────────────────────
    df['date'] = df.index.date
    orb_breakouts, orb_holds = [], []

    for _date, day_df in df.groupby('date'):
        day_df = day_df.sort_index()
        if len(day_df) < 3:
            continue
        orb_high = day_df.iloc[0]['High']
        rest = day_df.iloc[1:]
        bo_bars = rest[rest['High'] > orb_high]
        if len(bo_bars) > 0:
            orb_breakouts.append(1)
            after = day_df[day_df.index > bo_bars.index[0]]
            if len(after) >= 2:
                orb_holds.append(int(after.iloc[1]['Close'] > orb_high))
        else:
            orb_breakouts.append(0)

    r['orb_breakout_rate'] = float(np.mean(orb_breakouts)) if orb_breakouts else 0.50
    r['orb_hold_1h']       = float(np.mean(orb_holds))       if orb_holds       else 0.50

    # ── VWAP reclaim rate ─────────────────────────────────────
    vwap_reclaims = []
    for _date, day_df in df.groupby('date'):
        day_df = day_df.sort_index()
        if len(day_df) < 3:
            continue
        typical  = (day_df['High'] + day_df['Low'] + day_df['Close']) / 3
        vol_safe = day_df['Volume'].replace(0, np.nan).fillna(1)
        vwap     = (typical * vol_safe).cumsum() / vol_safe.cumsum()
        was_below, reclaimed = False, False
        for close, vw in zip(day_df['Close'].values, vwap.values):
            if not was_below and close < vw:
                was_below = True
            elif was_below and close > vw:
                reclaimed = True
                break
        vwap_reclaims.append(int(reclaimed))

    r['vwap_reclaim_rate'] = float(np.mean(vwap_reclaims)) if vwap_reclaims else 0.50

    # ── Morning vs afternoon momentum ─────────────────────────
    df['hour'] = df.index.hour
    ma_pairs = []
    for _date, day_df in df.groupby('date'):
        day_df = day_df.sort_index()
        morning   = day_df[day_df['hour'] <= 11]
        afternoon = day_df[day_df['hour'] >= 13]
        if len(morning) == 0 or len(afternoon) == 0:
            continue
        m_ret = morning.iloc[-1]['Close'] / morning.iloc[0]['Open'] - 1
        a_ret = afternoon.iloc[-1]['Close'] / afternoon.iloc[0]['Open'] - 1
        ma_pairs.append(1 if m_ret * a_ret > 0 else -1)

    r['morning_continuation'] = float(np.mean(ma_pairs)) if ma_pairs else 0.0

    return r


# ── Per-symbol analysis ─────────────────────────────────────────

def analyze_symbol(sym):
    daily = fetch_daily(sym)
    if daily is None:
        return None

    train = daily[daily.index <= TRAIN_END]
    valid = daily[daily.index >= VALID_START]

    if len(train) < 80:
        # Symbol may not have 4+ years; use whatever we have
        if len(daily) < 80:
            return None
        train = daily  # short-history stocks: use all data

    dna = {'symbol': sym}

    # Train-period daily DNA (basis for clustering)
    dna.update(compute_daily_dna(train))

    # Validation-period cross-check
    if len(valid) >= 40:
        v = compute_daily_dna(valid)
        dna['val_gap_fill']  = v['gap_fill_rate']
        dna['val_gap_go']    = v['gap_go_rate']
        dna['val_trend_1d']  = v['trend_1d']
        dna['consistency']   = 1 - abs(dna['gap_fill_rate'] - v['gap_fill_rate'])
    else:
        dna['val_gap_fill'] = dna['gap_fill_rate']
        dna['val_gap_go']   = dna['gap_go_rate']
        dna['val_trend_1d'] = dna['trend_1d']
        dna['consistency']  = 0.50

    # Hourly DNA (intraday patterns)
    hourly = fetch_hourly(sym)
    dna.update(compute_hourly_dna(hourly))

    # Catalyst win rate: gap >0.5% AND high volume
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
    dna['high_perf']  = sym in HIGH_PERF

    return dna


# ── Clustering ──────────────────────────────────────────────────

CLUSTER_FEATURES = [
    'gap_fill_rate', 'gap_go_rate',
    'trend_1d', 'trend_3d',
    'reversal_tendency',
    'vol_move_corr', 'up_vol_ratio',
    'avg_atr_pct',
    'return_skew', 'vol_clustering',
    'intraday_revert_rate',
    'fvg_density', 'fvg_fill_rate',
    'orb_breakout_rate', 'orb_hold_1h',
    'vwap_reclaim_rate',
    'morning_continuation',
]

CLUSTER_NAMES = {
    0: 'INSTITUTIONAL_ACCUMULATION',
    1: 'MOMENTUM_TREND',
    2: 'VOLATILE_NEWS_DRIVEN',
    3: 'RANGE_BOUND_REVERSAL',
}


def assign_clusters(df_dna, n_clusters=4):
    feat_df = df_dna[CLUSTER_FEATURES].fillna(df_dna[CLUSTER_FEATURES].median())
    scaler  = StandardScaler()
    X       = scaler.fit_transform(feat_df)

    km = KMeans(n_clusters=n_clusters, random_state=42, n_init=20)
    labels = km.fit_predict(X)

    # PCA for 2D view
    pca  = PCA(n_components=2)
    X_2d = pca.fit_transform(X)

    df_dna = df_dna.copy()
    df_dna['cluster_raw']  = labels
    df_dna['pca1']         = X_2d[:, 0]
    df_dna['pca2']         = X_2d[:, 1]

    # Inverse-transform centers so they display in original feature units
    centers_raw = scaler.inverse_transform(km.cluster_centers_)
    centers = pd.DataFrame(centers_raw, columns=CLUSTER_FEATURES)
    cluster_map = {}
    for c in range(n_clusters):
        atr   = centers.loc[c, 'avg_atr_pct']
        gap_go = centers.loc[c, 'gap_go_rate']
        rev   = centers.loc[c, 'reversal_tendency']
        trend = centers.loc[c, 'trend_1d']
        if trend > 0.58:
            cluster_map[c] = 'MOMENTUM_TREND'
        elif rev > 0.58:
            cluster_map[c] = 'RANGE_BOUND_REVERSAL'
        elif atr > centers['avg_atr_pct'].mean() + 0.5:
            cluster_map[c] = 'VOLATILE_NEWS_DRIVEN'
        else:
            cluster_map[c] = 'INSTITUTIONAL_ACCUMULATION'

    df_dna['cluster'] = df_dna['cluster_raw'].map(cluster_map)
    return df_dna, centers, scaler, cluster_map


# ── Reporting ───────────────────────────────────────────────────

def print_cluster_report(df_dna, centers, cluster_map):
    print("\n" + "="*80)
    print("  DNA CLUSTER ANALYSIS — Signal Fingerprints")
    print("  Train: 2020-2023 | Validate: 2024-2025")
    print("="*80)

    for raw_id, name in cluster_map.items():
        members = df_dna[df_dna['cluster_raw'] == raw_id].sort_values('catalyst_wr', ascending=False)
        if len(members) == 0:
            continue

        c = centers.loc[raw_id]
        print(f"\n{'─'*70}")
        print(f"  Cluster {raw_id}: {name}  ({len(members)} symbols)")
        print(f"{'─'*70}")
        print(f"  Defining traits:")
        print(f"    ATR%:          {c['avg_atr_pct']:.2f}%  (volatility)")
        print(f"    Gap-and-go:    {c['gap_go_rate']:.1%}  (gap holds direction)")
        print(f"    Gap fill:      {c['gap_fill_rate']:.1%}  (gap gets filled intraday)")
        print(f"    Trend 1d:      {c['trend_1d']:.1%}  (next-day follow-through after big move)")
        print(f"    Trend 3d:      {c['trend_3d']:.1%}")
        print(f"    Reversal:      {c['reversal_tendency']:.1%}  (mean-reverts after streak)")
        print(f"    Vol-move corr: {c['vol_move_corr']:.2f}  (vol predicts move size)")
        print(f"    ORB hold:      {c['orb_hold_1h']:.1%}  (ORB breakout holds 1h)")
        print(f"    VWAP reclaim:  {c['vwap_reclaim_rate']:.1%}  (VWAP magnet intraday)")
        print(f"    FVG fill:      {c['fvg_fill_rate']:.1%}  (fair value gaps fill within 10 bars)")

        print(f"\n  Symbols (sorted by catalyst WR):")
        row = []
        for _, s in members.iterrows():
            hp = "★" if s['high_perf'] else " "
            row.append(f"    {hp}{s['symbol']:6s}  WR={s['catalyst_wr']:.0%}  "
                       f"ATR={s['avg_atr_pct']:.1f}%  "
                       f"data={s['data_years']:.1f}yr  "
                       f"trend={s['trend_1d']:.0%}/{s['trend_3d']:.0%}d")
        for line in row:
            print(line)

    # Validation consistency check
    print(f"\n{'─'*70}")
    print("  VALIDATION: Train→Valid consistency (how stable is each symbol's DNA?)")
    print(f"{'─'*70}")
    df_dna['dna_drift'] = (
        (df_dna['gap_fill_rate'] - df_dna['val_gap_fill']).abs() +
        (df_dna['gap_go_rate']   - df_dna['val_gap_go']).abs()
    ) / 2
    stable   = df_dna[df_dna['dna_drift'] < 0.08].sort_values('catalyst_wr', ascending=False)
    unstable = df_dna[df_dna['dna_drift'] >= 0.08].sort_values('dna_drift', ascending=False)

    print(f"\n  STABLE DNA (drift < 8pp) — {len(stable)} symbols:")
    for _, s in stable.head(20).iterrows():
        hp = "★" if s['high_perf'] else " "
        print(f"    {hp}{s['symbol']:6s}  drift={s['dna_drift']:.1%}  cluster={s['cluster']}")

    if len(unstable) > 0:
        print(f"\n  UNSTABLE DNA (drift ≥ 8pp) — {len(unstable)} symbols — treat with caution:")
        for _, s in unstable.iterrows():
            print(f"    {s['symbol']:6s}  drift={s['dna_drift']:.1%}  cluster={s['cluster']}")


def print_strategy_implications(df_dna, cluster_map):
    print(f"\n{'='*80}")
    print("  STRATEGY IMPLICATIONS — How each cluster should affect scoring")
    print(f"{'='*80}")

    implications = {
        'MOMENTUM_TREND': (
            "Entry: raise A+ threshold slightly (only take the best setups)\n"
            "  Scoring: bonus points for strong ORB, early VWAP reclaim\n"
            "  Exit: MORE patience — trend persists, don't cut early\n"
            "  Best for: bull flag, HOD break, strong momo ≥5%"
        ),
        'INSTITUTIONAL_ACCUMULATION': (
            "Entry: standard A/A+ threshold — reliable but not flashy\n"
            "  Scoring: weight volume signal heavily (institutional = volume-confirmed)\n"
            "  Exit: standard exits — these don't tend to rip OR collapse\n"
            "  Best for: VWAP reclaim, steady breakout from consolidation"
        ),
        'VOLATILE_NEWS_DRIVEN': (
            "Entry: RAISE threshold to A+ only — false positives are expensive\n"
            "  Scoring: penalty if no clear catalyst; bonus for news + volume spike\n"
            "  Exit: LESS patience — volatile = can reverse fast\n"
            "  Best for: catalyst day only (earnings reaction, FDA, sector news)"
        ),
        'RANGE_BOUND_REVERSAL': (
            "Entry: standard threshold but favour reversal setups (not breakout)\n"
            "  Scoring: bonus for VWAP reclaim after dip (not HOD break)\n"
            "  Exit: faster exits — don't chase breakouts that often fail\n"
            "  Best for: VWAP reclaim, ORB false-break fade (short side)"
        ),
    }

    for name, impl in implications.items():
        members = df_dna[df_dna['cluster'] == name]['symbol'].tolist()
        if not members:
            continue
        print(f"\n  {name} ({len(members)} symbols)")
        print(f"  Symbols: {', '.join(members)}")
        print(f"  {impl}")


# ── Main ─────────────────────────────────────────────────────────

def main():
    symbols = [s for s in UNIVERSE if s]
    print(f"DNA Analysis: {len(symbols)} symbols | "
          f"Train 2020-2023 | Validate 2024-2025")
    print("Fetching data...")

    results = []
    failed  = []

    for i, sym in enumerate(symbols, 1):
        print(f"  [{i:3d}/{len(symbols)}] {sym:6s}", end='', flush=True)
        dna = analyze_symbol(sym)
        if dna:
            results.append(dna)
            print(f"  ✓  atr={dna['avg_atr_pct']:.1f}%  "
                  f"gap_go={dna['gap_go_rate']:.0%}  "
                  f"cat_wr={dna['catalyst_wr']:.0%}  "
                  f"data={dna['data_years']}yr")
        else:
            failed.append(sym)
            print("  ✗  (insufficient data)")

    if not results:
        print("No data — check network connection")
        return

    df = pd.DataFrame(results)
    df = df.set_index('symbol')

    # Save raw results
    out_path = os.path.join(os.path.dirname(__file__), 'dna_results.csv')
    df.to_csv(out_path)
    print(f"\nRaw DNA saved → {out_path}  ({len(df)} symbols)")

    # Cluster
    if len(df) >= 4:
        n_clusters = min(4, len(df) // 3)
        df_c, centers, scaler, cluster_map = assign_clusters(df, n_clusters=n_clusters)

        cluster_path = os.path.join(os.path.dirname(__file__), 'dna_clusters.csv')
        df_c[['cluster','cluster_raw','pca1','pca2'] + CLUSTER_FEATURES].to_csv(cluster_path)
        print(f"Clusters saved  → {cluster_path}")

        print_cluster_report(df_c.reset_index(), centers, cluster_map)
        print_strategy_implications(df_c.reset_index(), cluster_map)
    else:
        print("Too few symbols to cluster — check failures above")

    if failed:
        print(f"\nFailed symbols ({len(failed)}): {', '.join(failed)}")

    print("\nDone.")


if __name__ == '__main__':
    main()
