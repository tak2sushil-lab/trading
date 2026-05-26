# batch_backtest.py — Full validation suite for 49 DNA-screened candidates
#
# Tests: bull edge (5yr full), walk-forward IS/OOS split, rolling OOS (3 windows),
#        stress periods (4 crises). Combines into RECOMMEND_ADD / WATCH / SKIP.
#
# Command:
#   venv/bin/python batch_backtest.py              # all 49 from find_candidates_results.csv
#   venv/bin/python batch_backtest.py CLSK UPST    # spot-check specific symbols

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date
import sys
import warnings
warnings.filterwarnings('ignore')

# ── Config ──────────────────────────────────────────────────────────────────
CAPITAL_PER_TRADE = 2000
STOP_PCT          = 5.0
ATR_TRAIL_MULT    = 1.5
ATR_FADE_MULT     = 1.0
ATR_PERIOD        = 14
MIN_VOLUME_RATIO  = 1.3
MIN_TODAY_GAIN    = 3.0         # matches live auto_trader
SKIP_WEAK_DAYS    = True

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
GLOBAL_START      = '2019-10-01'   # 3m warmup before earliest window
BULL_START        = '2020-01-01'
TODAY             = date.today().isoformat()

STRESS_PERIODS = [
    ('COVID Crash',      '2020-02-19', '2020-03-31'),
    ('Rate Hike Cycle',  '2022-01-01', '2022-10-31'),
    ('Japan Carry',      '2024-07-31', '2024-08-16'),
    ('2022 Full Bear',   '2022-01-01', '2022-12-31'),
]

# IS/OOS split + 3 recent rolling windows
IS_START   = '2020-01-01';  IS_END   = '2023-12-31'
OOS_START  = '2024-01-01';  OOS_END  = TODAY
ROLL_WINDOWS = [
    ('2022-01-01', '2023-12-31', '2024-01-01', '2024-06-30', '2024-H1'),
    ('2022-07-01', '2024-06-30', '2024-07-01', '2024-12-31', '2024-H2'),
    ('2023-01-01', '2024-12-31', '2025-01-01', '2025-06-30', '2025-H1'),
]

# Verdict thresholds
THRESH_ADD  = dict(wr=58.0, oos_wr=50.0, oos_ev=0.0)
THRESH_WATCH = dict(wr=53.0, oos_ev=0.0)

# ── Core functions (mirrors backtest_strategy.py — do not change thresholds) ──

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
    df = df.copy()
    h, l, c = df['High'], df['Low'], df['Close']
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(ATR_PERIOD).mean()
    return df

def grade_day(i, df, spy_chg, regime, symbol=None):
    row      = df.iloc[i]
    prev_row = df.iloc[i - 1]
    df_upto  = df.iloc[:i + 1]
    price    = row['Open']
    atr      = row['atr']

    if pd.isna(atr) or atr <= 0 or pd.isna(price) or price < 5:
        return 'SKIP', 0
    if SKIP_WEAK_DAYS and regime == 'WEAK':
        return 'SKIP', 0
    ma20 = df_upto['Close'].rolling(20).mean().iloc[-1]
    if pd.isna(ma20) or price < ma20:
        return 'SKIP', 0
    today_gain = (row['Close'] - prev_row['Close']) / prev_row['Close'] * 100
    if today_gain < MIN_TODAY_GAIN:
        return 'SKIP', 0
    avg_vol   = df_upto['Volume'].rolling(20).mean().iloc[-2] if len(df_upto) >= 21 else None
    vol_ratio = float(row['Volume'] / avg_vol) if avg_vol and avg_vol > 0 else 1.0
    if vol_ratio < MIN_VOLUME_RATIO:
        return 'SKIP', 0

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
    rs_leader    = rs_vs_spy >= 3.0 and today_gain >= 2.0

    if not (orb_proxy or vwap_reclaim or bull_flag or hod_break or strong_momo or rs_leader):
        return 'SKIP', 0

    score = 0
    if orb_proxy:    score += 30
    if vwap_reclaim: score += 25
    if bull_flag:    score += 25
    if hod_break:    score += 20
    if rs_leader:    score += 20
    if vol_ratio >= 2.0:    score += 25
    elif vol_ratio >= 1.5:  score += 15
    else:                   score += 5

    ema8  = float(df_upto['Close'].ewm(span=8).mean().iloc[-1])
    ema21 = float(df_upto['Close'].ewm(span=21).mean().iloc[-1])
    if price > ema8 > ema21: score += 20
    elif price > ema21:      score += 10

    d = df_upto['Close'].diff()
    g = d.clip(lower=0).rolling(14).mean().iloc[-1]
    lv = (-d.clip(upper=0)).rolling(14).mean().iloc[-1]
    rsi = round(float(100 - (100 / (1 + g / lv))), 1) if lv and lv > 0 else 50
    if 45 <= rsi <= 65:    score += 20
    elif 75 < rsi <= 80:   score += 5
    else:                  score += 5

    if today_gain >= 5.0:   score += 30
    elif today_gain >= 3.0: score += 20
    elif today_gain >= 1.5: score += 10

    if regime == 'STRONG': score += 15
    elif regime == 'NORMAL': score += 5
    score += 5   # R:R always passes

    # ── DNA cluster modifier (mirrors auto_trader L1 entry) ─────────
    if symbol in HIGH_VOL_SYMBOLS:
        if orb_proxy and not vwap_reclaim:
            score -= 15
        if vwap_reclaim:
            score += 15
    elif symbol in INSTITUTIONAL_SYMBOLS:
        if orb_proxy:
            score += 5

    grade = 'A+' if score >= 80 else 'A' if score >= 65 else 'B' if score >= 50 else 'C'
    if grade in ('B', 'C'):
        return 'SKIP', score
    if spy_chg < 0 and grade != 'A+':
        return 'SKIP', score
    return grade, score

def simulate_trade(row, atr, symbol=None):
    entry  = row['Open']
    sl     = entry * (1 - STOP_PCT / 100)
    one_r  = entry * (1 + STOP_PCT / 100)
    hit_1r = row['High'] >= one_r

    half_cap       = CAPITAL_PER_TRADE / 2
    partial_locked = STOP_PCT / 100 * half_cap if hit_1r else 0.0
    rem_cap        = half_cap if hit_1r else CAPITAL_PER_TRADE

    if row['Low'] <= sl:
        if hit_1r:
            total_pnl = round(partial_locked - STOP_PCT / 100 * rem_cap, 2)
            return total_pnl
        return round(-STOP_PCT * CAPITAL_PER_TRADE / 100, 2)

    _trail_mult = 1.0 if symbol in HIGH_VOL_SYMBOLS else ATR_TRAIL_MULT
    trail_stop = row['High'] - _trail_mult * atr
    if row['Close'] < trail_stop:
        rest_pnl  = (trail_stop - entry) / entry * rem_cap
        return round(partial_locked + rest_pnl, 2)

    fade_stop = row['High'] - ATR_FADE_MULT * atr
    if row['Close'] < fade_stop:
        rest_pnl  = (row['Close'] - entry) / entry * rem_cap
        total_pnl = partial_locked + rest_pnl
        if total_pnl > 0:
            return round(total_pnl, 2)

    rest_pnl = (row['Close'] - entry) / entry * rem_cap
    return round(partial_locked + rest_pnl, 2)

# ── Run strategy on a date slice of a merged DataFrame ────────────────────

def run_window(merged, start, end, symbol=None):
    """Returns (n, wr, ev, total_pnl, ann_pnl, by_year)."""
    ps = pd.Timestamp(start)
    pe = pd.Timestamp(end)
    if merged.index.tz is not None:
        ps = ps.tz_localize(merged.index.tz)
        pe = pe.tz_localize(merged.index.tz)

    pnl_list = []
    years    = []
    for i in range(22, len(merged)):
        bar_date = merged.index[i]
        if bar_date < ps or bar_date > pe:
            continue
        row     = merged.iloc[i]
        spy_chg = float(row['spy_chg'])
        regime  = str(row['regime'])
        grade, score = grade_day(i, merged, spy_chg, regime, symbol)
        if grade == 'SKIP':
            continue
        pnl  = simulate_trade(row, float(row['atr']), symbol)
        pnl_list.append(pnl)
        years.append(bar_date.year)

    if not pnl_list:
        return dict(n=0, wr=0.0, ev=0.0, total=0.0, ann=0.0, prof_yr=0, n_yr=0)

    arr  = np.array(pnl_list)
    n    = len(arr)
    wins = (arr > 0).sum()
    wr   = wins / n * 100
    avg_w = arr[arr > 0].mean() if wins > 0 else 0.0
    avg_l = arr[arr <= 0].mean() if (arr <= 0).sum() > 0 else 0.0
    ev    = (wr / 100 * avg_w) + ((1 - wr / 100) * avg_l)
    total = arr.sum()

    n_years = max((pd.Timestamp(end) - pd.Timestamp(start)).days / 365, 0.5)
    ann     = total / n_years

    yr_ser   = pd.Series(arr, index=years)
    by_yr    = yr_ser.groupby(yr_ser.index).sum()
    prof_yr  = (by_yr > 0).sum()
    n_yr     = len(by_yr)

    return dict(n=n, wr=round(wr, 1), ev=round(ev, 2), total=round(total, 2),
                ann=round(ann, 2), prof_yr=int(prof_yr), n_yr=int(n_yr))

# ── Stress period summary ──────────────────────────────────────────────────

def run_stress_summary(merged, symbol=None):
    """Returns dict: period_name -> (n, wr, ev)."""
    results = {}
    for name, s, e in STRESS_PERIODS:
        st = run_window(merged, s, e, symbol)
        results[name] = st
    return results

# ── Build merged DataFrame once per symbol ────────────────────────────────

def build_merged(sym, spy_regime):
    df = yf.download(sym, start=GLOBAL_START, end=TODAY, progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if len(df) < 60:
        return None
    df = add_atr(df)
    merged = df.join(spy_regime, how='left')
    merged['spy_chg'] = merged['spy_chg'].fillna(0)
    merged['regime']  = merged['regime'].fillna('NORMAL')
    return merged

# ── Per-symbol verdict ────────────────────────────────────────────────────

def compute_verdict(full, oos, stress):
    n_stress_positive = sum(1 for v in stress.values() if v['n'] == 0 or v['ev'] > 0)
    n_stress_with_trades = sum(1 for v in stress.values() if v['n'] > 0)
    stress_ok = (n_stress_positive >= n_stress_with_trades) or (n_stress_with_trades == 0)

    if (full['wr'] >= THRESH_ADD['wr']
            and oos['wr'] >= THRESH_ADD['oos_wr']
            and oos['ev'] > THRESH_ADD['oos_ev']
            and stress_ok):
        return 'RECOMMEND_ADD'
    elif (full['wr'] >= THRESH_WATCH['wr']
            and oos['ev'] > THRESH_WATCH['oos_ev']):
        return 'WATCH'
    else:
        return 'SKIP'

# ── Main ──────────────────────────────────────────────────────────────────

def main():
    # Load candidate list
    override_syms = sys.argv[1:]
    if override_syms:
        candidates = pd.DataFrame({'symbol': override_syms, 'cluster': ['?'] * len(override_syms),
                                   'catalyst_wr': [0.0] * len(override_syms)})
    else:
        cands_path = 'find_candidates_results.csv'
        try:
            all_cands = pd.read_csv(cands_path)
            candidates = all_cands[all_cands['passed'] == True][['symbol', 'cluster', 'catalyst_wr']].copy()
        except FileNotFoundError:
            print(f"ERROR: {cands_path} not found. Run find_candidates.py first.")
            return

    symbols = candidates['symbol'].tolist()
    cluster_map = dict(zip(candidates['symbol'], candidates['cluster']))
    catwr_map   = dict(zip(candidates['symbol'], candidates['catalyst_wr']))

    print(f"\nBATCH BACKTEST — {len(symbols)} DNA-screened candidates")
    print(f"{'='*72}")
    print(f"  Capital   : ${CAPITAL_PER_TRADE}/trade | Stop: {STOP_PCT}% | A/A+ only")
    print(f"  Full edge : {BULL_START} → {TODAY}")
    print(f"  IS/OOS    : 2020-2023 in-sample | 2024+ out-of-sample")
    print(f"  Stress    : COVID, Rate Hike, Japan Carry, 2022 Bear")
    print(f"{'='*72}\n")

    print("Downloading SPY regime...", flush=True)
    spy_regime = build_spy_regime(GLOBAL_START, TODAY)

    rows = []
    for i, sym in enumerate(symbols, 1):
        print(f"[{i:>2}/{len(symbols)}] {sym:<6} ", end='', flush=True)

        merged = build_merged(sym, spy_regime)
        if merged is None:
            print("SKIP — insufficient data")
            continue

        # Full edge (2020→today)
        full = run_window(merged, BULL_START, TODAY, sym)
        # IS period (2020-2023)
        is_  = run_window(merged, IS_START, IS_END, sym)
        # OOS period (2024+)
        oos  = run_window(merged, OOS_START, OOS_END, sym)
        # Rolling windows
        roll_wins = []
        for ts, te, ps, pe, lbl in ROLL_WINDOWS:
            actual_pe = min(pe, TODAY)
            if pd.Timestamp(ps) > pd.Timestamp(TODAY):
                continue
            ww = run_window(merged, ps, actual_pe, sym)
            roll_wins.append((lbl, ww))
        # Stress
        stress = run_stress_summary(merged, sym)

        # Rolling OOS summary
        roll_n     = sum(w['n'] for _, w in roll_wins)
        roll_total = sum(w['total'] for _, w in roll_wins)
        roll_wr    = (sum(w['wr'] * w['n'] for _, w in roll_wins) / roll_n) if roll_n > 0 else 0.0
        roll_ev    = (sum(w['ev'] * w['n'] for _, w in roll_wins) / roll_n) if roll_n > 0 else 0.0

        # Stress summary (% of periods with trades that were positive)
        stress_pos  = sum(1 for v in stress.values() if v['n'] > 0 and v['ev'] > 0)
        stress_tot  = sum(1 for v in stress.values() if v['n'] > 0)
        stress_str  = f"{stress_pos}/{stress_tot}" if stress_tot > 0 else "0/0"

        verdict = compute_verdict(full, oos, stress)

        tag = {'RECOMMEND_ADD': '✅', 'WATCH': '⚠️ ', 'SKIP': '❌'}[verdict]
        print(f"N={full['n']:>3}  WR={full['wr']:>5.1f}%  OOS_WR={oos['wr']:>5.1f}%  "
              f"OOS_EV=${oos['ev']:>+7.2f}  Stress={stress_str}  {tag} {verdict}")

        # Year breakdown string
        is_by_yr_raw = {}
        for yr in range(2020, 2026):
            yw = run_window(merged, f'{yr}-01-01', f'{yr}-12-31', sym)
            is_by_yr_raw[yr] = yw['total']

        rows.append({
            'symbol':     sym,
            'cluster':    cluster_map.get(sym, '?'),
            'cat_wr':     round(catwr_map.get(sym, 0), 3),

            # Full edge
            'full_n':     full['n'],
            'full_wr':    full['wr'],
            'full_ev':    full['ev'],
            'full_ann':   full['ann'],
            'full_total': full['total'],
            'prof_yr':    full['prof_yr'],
            'n_yr':       full['n_yr'],

            # IS / OOS split
            'is_n':       is_['n'],
            'is_wr':      is_['wr'],
            'is_ev':      is_['ev'],
            'oos_n':      oos['n'],
            'oos_wr':     oos['wr'],
            'oos_ev':     oos['ev'],
            'oos_ann':    oos['ann'],
            'wr_drop':    round(is_['wr'] - oos['wr'], 1),

            # Rolling OOS
            'roll_n':     roll_n,
            'roll_wr':    round(roll_wr, 1),
            'roll_ev':    round(roll_ev, 2),

            # Stress
            'stress_pos': stress_pos,
            'stress_tot': stress_tot,
            'stress_str': stress_str,

            # By year
            '2020_pnl': round(is_by_yr_raw.get(2020, 0), 0),
            '2021_pnl': round(is_by_yr_raw.get(2021, 0), 0),
            '2022_pnl': round(is_by_yr_raw.get(2022, 0), 0),
            '2023_pnl': round(is_by_yr_raw.get(2023, 0), 0),
            '2024_pnl': round(is_by_yr_raw.get(2024, 0), 0),
            '2025_pnl': round(is_by_yr_raw.get(2025, 0), 0),

            'verdict':    verdict,
        })

    if not rows:
        print("\nNo results — check candidate list and data availability.")
        return

    df = pd.DataFrame(rows)

    # ── Summary tables ────────────────────────────────────────────────────

    print(f"\n{'='*100}")
    print(f"  FULL RESULTS — ranked by full_wr")
    print(f"{'='*100}")
    print(f"  {'Sym':<6} {'Cls':<12} {'N':>4} {'WR':>6} {'Ann$':>8} {'OOS_WR':>7} {'OOS_EV':>8} "
          f"{'WR_drop':>8} {'Stress':>7}  Verdict")
    print(f"  {'─'*92}")

    df_sorted = df.sort_values(['verdict', 'full_wr'], ascending=[True, False])
    for _, r in df_sorted.iterrows():
        tag = {'RECOMMEND_ADD': '✅', 'WATCH': '⚠️ ', 'SKIP': '❌'}[r['verdict']]
        print(f"  {r['symbol']:<6} {r['cluster']:<12} {r['full_n']:>4} {r['full_wr']:>5.1f}% "
              f"{r['full_ann']:>+8,.0f} {r['oos_wr']:>6.1f}% {r['oos_ev']:>+8.2f} "
              f"{r['wr_drop']:>+7.1f}pp {r['stress_str']:>7}  {tag} {r['verdict']}")

    # ── Summary by cluster ────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print(f"  SUMMARY BY CLUSTER")
    print(f"{'='*72}")
    print(f"  {'Cluster':<14} {'N_syms':>7} {'Rec':>5} {'Watch':>7} {'Skip':>6} {'Avg_WR':>8} {'Avg_Ann$':>10}")
    print(f"  {'─'*60}")
    for cls in ['HIGH_VOL', 'MOMENTUM', 'INSTITUTIONAL']:
        sub = df[df['cluster'] == cls]
        if sub.empty:
            continue
        rec   = (sub['verdict'] == 'RECOMMEND_ADD').sum()
        watch = (sub['verdict'] == 'WATCH').sum()
        skip  = (sub['verdict'] == 'SKIP').sum()
        print(f"  {cls:<14} {len(sub):>7} {rec:>5} {watch:>7} {skip:>6} "
              f"{sub['full_wr'].mean():>7.1f}% {sub['full_ann'].mean():>+10,.0f}")

    # ── RECOMMEND_ADD list ────────────────────────────────────────────────
    rec_df = df[df['verdict'] == 'RECOMMEND_ADD'].sort_values('full_wr', ascending=False)
    watch_df = df[df['verdict'] == 'WATCH'].sort_values('full_wr', ascending=False)

    print(f"\n{'='*72}")
    print(f"  RECOMMEND_ADD ({len(rec_df)} symbols)")
    print(f"  Add these to FULL_UNIVERSE after this review")
    print(f"{'='*72}")
    if rec_df.empty:
        print("  None met all thresholds (WR≥58%, OOS_WR≥50%, OOS_EV>0, stress OK)")
    else:
        for _, r in rec_df.iterrows():
            print(f"  {r['symbol']:<6}  {r['cluster']:<12}  WR={r['full_wr']:.1f}%  "
                  f"Ann=${r['full_ann']:+,.0f}  OOS_WR={r['oos_wr']:.1f}%  "
                  f"OOS_EV=${r['oos_ev']:+.2f}  Stress={r['stress_str']}")

    print(f"\n{'='*72}")
    print(f"  WATCH ({len(watch_df)} symbols)")
    print(f"  Add to a watchlist — backtest is positive but OOS/stress incomplete")
    print(f"{'='*72}")
    if watch_df.empty:
        print("  None in WATCH tier")
    else:
        for _, r in watch_df.iterrows():
            wr_drop_note = f"OOS drop {r['wr_drop']:+.1f}pp" if r['wr_drop'] > 10 else "OOS stable"
            print(f"  {r['symbol']:<6}  {r['cluster']:<12}  WR={r['full_wr']:.1f}%  "
                  f"Ann=${r['full_ann']:+,.0f}  OOS_WR={r['oos_wr']:.1f}%  {wr_drop_note}  "
                  f"Stress={r['stress_str']}")

    # ── By-year P&L heatmap ───────────────────────────────────────────────
    rec_syms = rec_df['symbol'].tolist()
    if rec_syms:
        print(f"\n{'='*90}")
        print(f"  BY-YEAR P&L — RECOMMEND_ADD symbols (${CAPITAL_PER_TRADE}/trade)")
        print(f"{'='*90}")
        print(f"  {'Symbol':<7} {'2020':>8} {'2021':>8} {'2022':>8} {'2023':>8} "
              f"{'2024':>8} {'2025':>8}  Prof/Total")
        print(f"  {'─'*80}")
        for _, r in rec_df.iterrows():
            def yr(y): return f"{r[f'{y}_pnl']:>+8,.0f}"
            pyr = f"{r['prof_yr']}/{r['n_yr']}"
            print(f"  {r['symbol']:<7}{yr(2020)}{yr(2021)}{yr(2022)}{yr(2023)}{yr(2024)}{yr(2025)}  {pyr}")

    # ── Save CSV ──────────────────────────────────────────────────────────
    out = 'batch_backtest_results.csv'
    df.to_csv(out, index=False)
    print(f"\n  Results saved → {out}")

    # ── Final counts ──────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print(f"  FINAL TALLY")
    print(f"{'='*72}")
    print(f"  Tested         : {len(df)}")
    print(f"  RECOMMEND_ADD  : {len(rec_df)}   (WR≥{THRESH_ADD['wr']}%, OOS_WR≥{THRESH_ADD['oos_wr']}%, EV>0, stress OK)")
    print(f"  WATCH          : {len(watch_df)}   (WR≥{THRESH_WATCH['wr']}%, OOS EV>0)")
    print(f"  SKIP           : {(df['verdict']=='SKIP').sum()}")
    print()
    print(f"  Thresholds: RECOMMEND_ADD = WR≥{THRESH_ADD['wr']}% AND OOS_WR≥{THRESH_ADD['oos_wr']}% "
          f"AND OOS_EV>0 AND stress positive")
    print(f"              WATCH         = WR≥{THRESH_WATCH['wr']}% AND OOS_EV>0")
    print()
    print(f"  Note: Daily-bar approximation — live results vary ±15-20%")
    print(f"{'='*72}\n")


if __name__ == '__main__':
    main()
