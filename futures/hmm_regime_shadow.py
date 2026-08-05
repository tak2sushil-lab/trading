"""hmm_regime_shadow.py — Aug 4 2026.

Log-only, EOD daily comparison of the HMM regime read vs the live IB-range
classifier (detect_regime in hero_score.py) — the shadow-first step research
proposed but hadn't started. Places no orders, changes no live behavior.

Why this didn't need the "live feature redesign" caveat from the original
research writeup: that caveat only applies to using HMM as a REAL-TIME gate
(deciding something before the day's bars exist). A shadow comparison runs
AFTER the day closes, so day-level features (return%, range%, volume-vol)
are simply available — no redesign needed to start collecting comparison
data today. The live-usable version (IB-time-only features) is still a
separate, harder project if this ever earns a gating role.

Refits a fresh 3-state GaussianHMM on trailing 2yr MNQ bars each run (cheap,
seconds) rather than persisting a model — avoids staleness/versioning for a
log-only job. Writes one row/day to hmm_regime_shadow (trades.db).

Schedule: EOD daily via launchd (com.sushil.trading.hmm_regime_shadow),
after collect_bars so today's bars are present.

Command: venv/bin/python futures/hmm_regime_shadow.py
"""
import os
import sqlite3
import sys
import warnings
from datetime import date, timedelta

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM

warnings.filterwarnings('ignore')
_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_DIR))
sys.path.insert(0, _DIR)
from hero_score import detect_regime

DB_PATH = os.path.join(os.path.dirname(_DIR), 'trades.db')
MKT_DB  = os.path.join(os.path.dirname(_DIR), 'market_data.db')
TRAIN_WINDOW_DAYS = 730


def ensure_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS hmm_regime_shadow (
        trading_date    TEXT PRIMARY KEY,
        ib_range_pts    REAL,
        day_ret_pct     REAL,
        day_range_pct   REAL,
        vol_vol         REAL,
        live_regime     TEXT,
        hmm_regime      TEXT,
        continuation    INTEGER,
        post_move_pts   REAL,
        created_at      TEXT DEFAULT (datetime('now'))
    )''')
    conn.commit()
    conn.close()


def load_bars():
    conn = sqlite3.connect(MKT_DB)
    start = (date.today() - timedelta(days=TRAIN_WINDOW_DAYS)).isoformat()
    df = pd.read_sql(
        "SELECT ts_utc, open, high, low, close, volume FROM futures_bars_5m "
        "WHERE symbol='MNQ' AND ts_utc >= ? ORDER BY ts_utc", conn, params=(start,))
    conn.close()
    df['ts'] = pd.to_datetime(df['ts_utc'], utc=True, format='ISO8601').dt.tz_convert('America/New_York')
    return df.set_index('ts')


def daily_features(bars):
    rows = []
    for d, day in bars.groupby(bars.index.date):
        rth = day.between_time('09:30', '16:00')
        if len(rth) < 40:
            continue
        ib = rth.between_time('09:30', '10:29')
        post = rth.between_time('10:30', '16:00')
        if ib.empty or post.empty:
            continue
        ib_range_pts = float(ib['high'].max() - ib['low'].min())
        ib_dir       = float(ib['close'].iloc[-1] - ib['open'].iloc[0])
        post_move    = float(post['close'].iloc[-1] - post['open'].iloc[0])
        day_open, day_close = float(rth['open'].iloc[0]), float(rth['close'].iloc[-1])
        day_ret_pct  = (day_close - day_open) / day_open * 100
        day_range_pct = (rth['high'].max() - rth['low'].min()) / day_open * 100
        vol_mean, vol_std = rth['volume'].mean(), rth['volume'].std()
        vol_vol = (vol_std / vol_mean) if vol_mean > 0 else 0.0
        continuation = 1 if (ib_dir * post_move > 0) else (-1 if ib_dir * post_move < 0 else 0)
        rows.append({'date': str(d), 'ib_range_pts': ib_range_pts, 'day_ret_pct': day_ret_pct,
                     'day_range_pct': day_range_pct, 'vol_vol': vol_vol,
                     'continuation': continuation, 'post_move_pts': abs(post_move)})
    return pd.DataFrame(rows)


def main():
    ensure_table()
    bars = load_bars()
    feat = daily_features(bars).dropna()
    if len(feat) < 60:
        print(f"[hmm_regime_shadow] only {len(feat)} days available — too few to fit, skipping")
        return

    feat['live_regime'] = feat['ib_range_pts'].apply(detect_regime)

    X = feat[['day_ret_pct', 'day_range_pct', 'vol_vol']].values
    Xz = (X - X.mean(axis=0)) / X.std(axis=0)
    model = GaussianHMM(n_components=3, covariance_type='diag', n_iter=200, random_state=42)
    model.fit(Xz)
    feat['hmm_state'] = model.predict(Xz)
    state_range = feat.groupby('hmm_state')['day_range_pct'].mean().sort_values()
    label_map = {state_range.index[0]: 'HMM_QUIET',
                 state_range.index[1]: 'HMM_CHOPPY',
                 state_range.index[2]: 'HMM_TRENDING'}
    feat['hmm_regime'] = feat['hmm_state'].map(label_map)

    conn = sqlite3.connect(DB_PATH)
    already = {r[0] for r in conn.execute("SELECT trading_date FROM hmm_regime_shadow")}
    new_rows = feat[~feat['date'].isin(already)]
    for _, r in new_rows.iterrows():
        conn.execute(
            '''INSERT OR IGNORE INTO hmm_regime_shadow
               (trading_date, ib_range_pts, day_ret_pct, day_range_pct, vol_vol,
                live_regime, hmm_regime, continuation, post_move_pts)
               VALUES (?,?,?,?,?,?,?,?,?)''',
            (r['date'], r['ib_range_pts'], r['day_ret_pct'], r['day_range_pct'],
             r['vol_vol'], r['live_regime'], r['hmm_regime'], r['continuation'], r['post_move_pts']))
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM hmm_regime_shadow").fetchone()[0]
    conn.close()
    print(f"[hmm_regime_shadow] backfilled/updated {len(new_rows)} day(s), {total} total rows in table")


if __name__ == '__main__':
    main()
