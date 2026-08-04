# research_hmm_regime.py — Aug 3 2026
#
# Does a learned (HMM) regime classifier separate "genuine trend" from "wide
# whipsaw" days better than the live IB-range threshold classifier
# (futures/hero_score.py detect_regime)? This is THE twice-documented open
# problem in futures memory:
#   - Jul 7 2026: 4 hand-tuned live chop detectors tried, none worked
#     (VWAP-crossing count, ADX-at-entry, morning character, regime-flip count)
#   - Jul 8 2026: "IB range alone can't cleanly separate real trend from wide
#     whipsaw" — the TRENDING lock-fraction gap, concrete case: Jun 18 2026,
#     a slow +0.34% grind, misclassified TRENDING purely on IB range >=200pts
#
# Research only — this does NOT touch futures_trader.py/tc_trader.py. If it
# shows a real, honest separation edge, it becomes a candidate for the
# shadow/instrument-first treatment (CONSTITUTION.md), not a live gate today.
#
# Data: MNQ 5-min bars from market_data.db, 2yr window (user directive).
# Command: venv/bin/python research_hmm_regime.py

import sys, warnings
import numpy as np
import pandas as pd
import sqlite3
from datetime import date, timedelta
from hmmlearn.hmm import GaussianHMM

warnings.filterwarnings('ignore')
sys.path.insert(0, '/Users/sushil/trading')
sys.path.insert(0, '/Users/sushil/trading/futures')
from hero_score import detect_regime, QUIET_IB_THRESH, TRENDING_IB_THRESH

START = (date.today() - timedelta(days=730)).isoformat()
ET_OFFSET_HOURS = 4   # EDT approx for the whole window — good enough for day bucketing


def load_bars():
    conn = sqlite3.connect('/Users/sushil/trading/market_data.db')
    df = pd.read_sql(
        "SELECT ts_utc, open, high, low, close, volume FROM futures_bars_5m "
        "WHERE symbol='MNQ' AND ts_utc >= ? ORDER BY ts_utc", conn, params=(START,))
    conn.close()
    df['ts'] = pd.to_datetime(df['ts_utc'], utc=True, format='ISO8601').dt.tz_convert('America/New_York')
    df = df.set_index('ts')
    return df


def daily_features(df):
    """One row per RTH trading day: IB range (9:30-10:30, for the live
    classifier), plus HMM inputs (day return, day range%, volume vol) and a
    forward-quality proxy: does the post-10:30 move CONTINUE the pre-10:30
    (IB-period) direction, or reverse? That proxy is what both classifiers
    get judged against."""
    rows = []
    for d, day in df.groupby(df.index.date):
        rth = day.between_time('09:30', '16:00')
        if len(rth) < 40:   # need most of the session present
            continue
        ib = rth.between_time('09:30', '10:29')
        post = rth.between_time('10:30', '16:00')
        if ib.empty or post.empty:
            continue

        ib_range_pts = float(ib['high'].max() - ib['low'].min())
        ib_dir       = float(ib['close'].iloc[-1] - ib['open'].iloc[0])
        post_move    = float(post['close'].iloc[-1] - post['open'].iloc[0])
        day_open     = float(rth['open'].iloc[0])
        day_close    = float(rth['close'].iloc[-1])
        day_ret_pct  = (day_close - day_open) / day_open * 100
        day_range_pct = (rth['high'].max() - rth['low'].min()) / day_open * 100
        vol_mean     = rth['volume'].mean()
        vol_std      = rth['volume'].std()
        vol_vol      = (vol_std / vol_mean) if vol_mean > 0 else 0.0

        # Forward-quality proxy: +1 if post-IB move continues the IB-period
        # direction, -1 if it reverses. This is what we're judging both
        # classifiers against — did "trend" actually mean something.
        continuation = 1 if (ib_dir * post_move > 0) else (-1 if ib_dir * post_move < 0 else 0)
        post_move_pts = abs(post_move)

        rows.append({
            'date': str(d), 'ib_range_pts': ib_range_pts, 'day_ret_pct': day_ret_pct,
            'day_range_pct': day_range_pct, 'vol_vol': vol_vol,
            'continuation': continuation, 'post_move_pts': post_move_pts,
        })
    return pd.DataFrame(rows)


def main():
    print("Loading MNQ 5-min bars...")
    bars = load_bars()
    feat = daily_features(bars)
    feat = feat.dropna()
    print(f"{len(feat)} trading days, {feat['date'].min()} -> {feat['date'].max()}\n")

    # ── Live classifier (unchanged, imported directly from hero_score.py) ──
    feat['live_regime'] = feat['ib_range_pts'].apply(detect_regime)

    # ── HMM classifier: 3 states on (day_ret_pct, day_range_pct, vol_vol), z-scored ──
    X = feat[['day_ret_pct', 'day_range_pct', 'vol_vol']].values
    Xz = (X - X.mean(axis=0)) / X.std(axis=0)
    model = GaussianHMM(n_components=3, covariance_type='diag', n_iter=200, random_state=42)
    model.fit(Xz)
    states = model.predict(Xz)
    feat['hmm_state'] = states

    # Auto-label states by mean day_range_pct (matches the video's "auto-identify
    # bull-run/bear-crash by highest/lowest return" pattern, adapted: here we care
    # about RANGE magnitude, since the question is trend-vs-chop, not direction).
    state_range = feat.groupby('hmm_state')['day_range_pct'].mean().sort_values()
    label_map = {state_range.index[0]: 'HMM_QUIET',
                 state_range.index[1]: 'HMM_CHOPPY',
                 state_range.index[2]: 'HMM_TRENDING'}
    feat['hmm_regime'] = feat['hmm_state'].map(label_map)

    print("=" * 70)
    print("HMM state characteristics (auto-labeled by mean day range%)")
    print("=" * 70)
    print(feat.groupby('hmm_regime')[['day_ret_pct', 'day_range_pct', 'vol_vol']].mean().to_string())

    # ── The actual test: on days each classifier calls "TRENDING", how often
    # does the post-IB move actually continue the IB-period direction (vs.
    # reverse)? And what's the average forward move size? Higher continuation
    # rate = "trending" label is catching something real, not just a wide IB.
    print("\n" + "=" * 70)
    print("TRENDING-label quality: continuation rate + avg forward move")
    print("=" * 70)
    for label, col in [('LIVE (IB-range threshold)', 'live_regime'),
                        ('HMM (learned)', 'hmm_regime')]:
        trending_val = 'TRENDING' if col == 'live_regime' else 'HMM_TRENDING'
        sub = feat[feat[col] == trending_val]
        if len(sub) == 0:
            print(f"{label}: no days labeled TRENDING")
            continue
        cont_rate = (sub['continuation'] > 0).mean() * 100
        avg_move = sub['post_move_pts'].mean()
        print(f"{label}: n={len(sub)}  continuation_rate={cont_rate:.1f}%  "
              f"avg_post_IB_move={avg_move:.0f}pts")

    # ── Cross-tab: where do the two classifiers disagree, and who's right
    # more often on those disagreement days (per the continuation proxy)? ──
    print("\n" + "=" * 70)
    print("CROSS-TAB: live_regime vs hmm_regime")
    print("=" * 70)
    print(pd.crosstab(feat['live_regime'], feat['hmm_regime']).to_string())

    disagree = feat[(feat['live_regime'] == 'TRENDING') & (feat['hmm_regime'] != 'HMM_TRENDING')]
    print(f"\nDays LIVE says TRENDING but HMM disagrees: {len(disagree)}")
    if len(disagree):
        cont_rate_disagree = (disagree['continuation'] > 0).mean() * 100
        print(f"  their continuation rate: {cont_rate_disagree:.1f}% "
              f"(vs {(feat[feat['live_regime']=='TRENDING']['continuation']>0).mean()*100:.1f}% for all LIVE-TRENDING days)")
        print("  (if this subset's continuation rate is much LOWER, HMM is catching exactly")
        print("   the 'wide IB but not a real trend' failure case documented Jul 8 2026)")

    # ── Specific case study: Jun 18 2026, the exact day flagged as
    # misclassified TRENDING (slow +0.34% grind) ──
    print("\n" + "=" * 70)
    print("CASE STUDY: Jun 18 2026 (documented misclassification)")
    print("=" * 70)
    case = feat[feat['date'] == '2026-06-18']
    if len(case):
        print(case[['date', 'ib_range_pts', 'day_ret_pct', 'live_regime',
                    'hmm_regime', 'continuation', 'post_move_pts']].to_string(index=False))
    else:
        print("2026-06-18 not in the 2yr window or bar data missing that day")

    feat.to_csv('/Users/sushil/trading/research_hmm_regime_results.csv', index=False)
    print("\nFull results -> research_hmm_regime_results.csv")


if __name__ == '__main__':
    main()
