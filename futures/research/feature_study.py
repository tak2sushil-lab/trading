#!/usr/bin/env python3
"""
futures/feature_study.py — Phase 1: Blood Test
Compute 8 technical features for every 2026 sim trade.
No gates, no changes to live system — observe and correlate only.

Features:
  1. range_consumed   — % of daily ATR used before entry (head vs tail)
  2. fib_dist         — distance to nearest Fibonacci pivot level (pts)
  3. fib_zone         — is entry inside the .382-.618 Golden Zone? (bool)
  4. rsi_1h           — RSI(14) on 1-hour bars at entry time
  5. hurst            — lag-1 autocorrelation proxy (trending vs mean-rev)
  6. poc_above        — entry above prior-day volume POC? (bool)
  7. mtf_1h_bull      — 1H close > 20-bar MA at entry (bool)
  8. mtf_4h_bull      — 4H close > 10-bar MA at entry (bool)
  + atr, side, setup, grade, bias, pnl, win for slicing
"""
import sys, datetime as _dt
import numpy as np
import pandas as pd

sys.path.insert(0, '.')
import futures.sim_replay as sr


# ── Technical helpers ─────────────────────────────────────────────────────────

def compute_rsi(closes, period=14):
    if len(closes) < period + 2:
        return None
    closes = np.array(closes, dtype=float)
    deltas = np.diff(closes[-(period + 1):])
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    ag = gains.mean()
    al = losses.mean()
    if al == 0:
        return 100.0
    return round(100.0 - 100.0 / (1.0 + ag / al), 2)


def compute_fib_pivots(H, L, C):
    """Full Fibonacci pivot grid from prior day H/L/C."""
    P   = (H + L + C) / 3.0
    rng = H - L
    return {
        'P':     P,
        'R1':    2*P - L,
        'R2':    P + rng,
        'R3':    H + 2*(P - L),
        'S1':    2*P - H,
        'S2':    P - rng,
        'S3':    L - 2*(H - P),
        'FR618': P + 0.618 * rng,   # Golden Ratio resistance
        'FS618': P - 0.618 * rng,   # Golden Ratio support
        'FR382': P + 0.382 * rng,   # Silver ratio resistance
        'FS382': P - 0.382 * rng,   # Silver ratio support
        'FR500': P + 0.500 * rng,   # Midpoint resistance
        'FS500': P - 0.500 * rng,   # Midpoint support
    }


def nearest_pivot_dist(price, pivots):
    """Distance in points from entry to nearest pivot level."""
    if not pivots:
        return None
    return round(min(abs(price - v) for v in pivots.values()), 2)


def in_golden_zone(price, pivots, side):
    """Is entry inside the .382-.618 Golden Zone (retracement target for counter-moves)?"""
    if not pivots:
        return None
    # Golden Zone: between FS382 and FS618 for LONG (support zone)
    #              between FR382 and FR618 for SHORT (resistance zone)
    if side == 'LONG':
        lo = min(pivots['FS618'], pivots['FS382'])
        hi = max(pivots['FS618'], pivots['FS382'])
    else:
        lo = min(pivots['FR382'], pivots['FR618'])
        hi = max(pivots['FR382'], pivots['FR618'])
    return int(lo <= price <= hi)


def hurst_proxy(prices):
    """
    Lag-1 autocorrelation of log-returns as Hurst proxy.
    Positive → trending (momentum works).
    Negative → mean-reverting (momentum fails).
    ~0       → random walk (no edge).
    """
    prices = np.array(prices, dtype=float)
    if len(prices) < 20:
        return None
    rets = np.diff(np.log(prices[-61:]))
    if len(rets) < 10:
        return None
    c = np.corrcoef(rets[:-1], rets[1:])[0, 1]
    return round(float(c), 4) if np.isfinite(c) else None


def compute_poc(bars):
    """
    Prior-day volume Point of Control — price with highest total volume.
    Institutional gravitational center; price repeatedly revisits it.
    """
    if bars is None or bars.empty:
        return None
    if 'volume' not in bars.columns:
        return None
    tick = 0.25
    vol_map = {}
    for _, row in bars.iterrows():
        mid   = float((row['high'] + row['low']) / 2)
        price = round(mid / tick) * tick
        vol_map[price] = vol_map.get(price, 0) + float(row.get('volume', 0))
    if not vol_map:
        return None
    return max(vol_map, key=vol_map.get)


def resample_bars(bars, freq):
    """Resample 5-min bars to a coarser frequency."""
    if bars is None or bars.empty:
        return pd.DataFrame()
    try:
        r = bars.resample(freq).agg(
            {'open': 'first', 'high': 'max', 'low': 'min',
             'close': 'last', 'volume': 'sum'}
        ).dropna(subset=['close'])
        return r
    except Exception:
        return pd.DataFrame()


def ma_bias(bars_resampled, period):
    """Is last close above the N-bar simple MA? Returns 1/0/None."""
    if bars_resampled is None or len(bars_resampled) < period + 1:
        return None
    closes = bars_resampled['close'].values
    ma = closes[-period:].mean()
    return int(float(closes[-1]) > ma)


# ── Prior RTH day lookup ──────────────────────────────────────────────────────

def get_prior_rth(all_bars, trade_date):
    """Return prior trading day's RTH bars (skipping weekends & holidays)."""
    prev = trade_date - _dt.timedelta(days=1)
    for _ in range(7):
        if prev.weekday() < 5 and prev not in sr.US_HOLIDAYS_2026:
            day_bars = all_bars[all_bars.index.date == prev]
            rth = sr.filter_ny_session(day_bars)
            if not rth.empty:
                return rth
        prev -= _dt.timedelta(days=1)
    return None


# ── Bars-to-entry slice ───────────────────────────────────────────────────────

def bars_up_to(all_bars, trade_date, entry_time_str):
    """All bars (including history) up to and including entry bar.
    bars index is America/New_York — compare directly."""
    h = int(entry_time_str[:2])
    m = int(entry_time_str[3:5])
    entry_et = pd.Timestamp(year=trade_date.year, month=trade_date.month,
                             day=trade_date.day, hour=h, minute=m,
                             tz='America/New_York')
    return all_bars[all_bars.index <= entry_et]


# ── Main feature computation ──────────────────────────────────────────────────

def compute_features(all_bars, trade_date, trade, prior_rth, today_rth):
    atr     = abs(trade['entry'] - trade['stop_init']) / sr.STOP_ATR_MULT
    entry_p = float(trade['entry'])
    side    = trade['side']

    # ── 1. Range consumed ────────────────────────────────────────────────────
    if not today_rth.empty and atr > 0:
        day_open = float(today_rth['open'].iloc[0])
        rng_consumed = round(abs(entry_p - day_open) / atr, 3)
    else:
        rng_consumed = None

    # ── 2 & 3. Fibonacci pivots ───────────────────────────────────────────────
    pivots = None
    if prior_rth is not None and not prior_rth.empty:
        pdH = float(prior_rth['high'].max())
        pdL = float(prior_rth['low'].min())
        pdC = float(prior_rth['close'].iloc[-1])
        pivots = compute_fib_pivots(pdH, pdL, pdC)
    fib_dist    = nearest_pivot_dist(entry_p, pivots)
    golden_zone = in_golden_zone(entry_p, pivots, side)

    # ── 4. 1-Hour RSI ─────────────────────────────────────────────────────────
    b_up = bars_up_to(all_bars, trade_date, trade['entry_time'])
    bars_1h = resample_bars(b_up, '1h')
    rsi_1h = compute_rsi(bars_1h['close'].values, 14) if len(bars_1h) >= 16 else None

    # ── 5. Hurst proxy ────────────────────────────────────────────────────────
    recent_5m = b_up.tail(80)
    hurst     = hurst_proxy(recent_5m['close'].values)

    # ── 6. POC position ───────────────────────────────────────────────────────
    poc = compute_poc(prior_rth)
    poc_above = int(entry_p > poc) if poc is not None else None
    poc_dist  = round(entry_p - poc, 1) if poc is not None else None

    # ── 7 & 8. Multi-timeframe MA bias ───────────────────────────────────────
    mtf_1h = ma_bias(bars_1h, 20)
    bars_4h = resample_bars(b_up, '4h')
    mtf_4h  = ma_bias(bars_4h, 10)

    # ── MTF alignment (both timeframes agree with trade direction) ────────────
    expected = 1 if side == 'LONG' else 0
    if mtf_1h is not None and mtf_4h is not None:
        mtf_aligned = int(mtf_1h == expected and mtf_4h == expected)
    else:
        mtf_aligned = None

    return {
        'range_consumed': rng_consumed,
        'fib_dist':       fib_dist,
        'golden_zone':    golden_zone,
        'rsi_1h':         rsi_1h,
        'hurst':          hurst,
        'poc_above':      poc_above,
        'poc_dist':       poc_dist,
        'mtf_1h_bull':    mtf_1h,
        'mtf_4h_bull':    mtf_4h,
        'mtf_aligned':    mtf_aligned,
        'atr':            round(atr, 1),
    }


# ── Correlation + quartile analysis ──────────────────────────────────────────

def analyze(df):
    numeric_feats = ['range_consumed', 'fib_dist', 'rsi_1h', 'hurst', 'poc_dist', 'atr']
    bool_feats    = ['golden_zone', 'poc_above', 'mtf_1h_bull', 'mtf_4h_bull', 'mtf_aligned']

    print('\n' + '='*72)
    print('BLOOD TEST RESULTS — 2026 MNQ Trades')
    print('='*72)
    print(f'\nTotal trades: {len(df)}   WR={df["win"].mean()*100:.1f}%   '
          f'P&L=${df["pnl"].sum():+.2f}   avg=${df["pnl"].mean():+.2f}')

    # ── Spearman correlation ─────────────────────────────────────────────────
    print('\n--- FEATURE CORRELATION WITH WIN (Spearman rank) ---')
    print(f'{"Feature":<18} {"Corr":>7} {"Direction":>28} {"N":>5}')
    print('-'*62)
    correlations = []
    for feat in numeric_feats + bool_feats:
        sub = df[df[feat].notna()].copy()
        if len(sub) < 15:
            continue
        x = sub[feat].astype(float).values
        y = sub['win'].astype(float).values
        # Spearman via rank
        from scipy.stats import spearmanr as _sp
        try:
            corr, pval = _sp(x, y)
        except Exception:
            corr, pval = 0.0, 1.0
        sig = '***' if pval < 0.01 else '** ' if pval < 0.05 else '*  ' if pval < 0.1 else '   '
        direction = ('↑ HIGH = better WR' if corr > 0.05
                     else '↓ LOW  = better WR' if corr < -0.05
                     else '≈ weak signal     ')
        correlations.append((abs(corr), feat, corr, pval, len(sub), direction, sig))
    correlations.sort(reverse=True)
    for _, feat, corr, pval, n, direction, sig in correlations:
        print(f'{feat:<18} {corr:>+7.3f} {sig}  {direction}   N={n}')

    # ── WR by quartile ──────────────────────────────────────────────────────
    print('\n--- WR BY QUARTILE (Q1=bottom 25%, Q4=top 25%) ---')
    for feat in numeric_feats:
        sub = df[df[feat].notna()].copy()
        if len(sub) < 20:
            continue
        try:
            sub['_q'] = pd.qcut(sub[feat], 4, labels=['Q1', 'Q2', 'Q3', 'Q4'],
                                 duplicates='drop')
        except Exception:
            continue
        print(f'\n  {feat}:')
        for q in ['Q1', 'Q2', 'Q3', 'Q4']:
            qdf = sub[sub['_q'] == q]
            if len(qdf) == 0:
                continue
            wr  = qdf['win'].mean() * 100
            avg = qdf['pnl'].mean()
            lo  = qdf[feat].min()
            hi  = qdf[feat].max()
            bar = '█' * int(wr / 5)
            print(f'    {q} [{lo:>6.1f}–{hi:>6.1f}]  N={len(qdf):>3}  '
                  f'WR={wr:>5.1f}%  avg=${avg:>+7.2f}  {bar}')

    # ── Boolean features ─────────────────────────────────────────────────────
    print('\n--- BINARY FEATURES ---')
    for feat in bool_feats:
        sub = df[df[feat].notna()].copy()
        if len(sub) < 10:
            continue
        print(f'\n  {feat}:')
        for val, lbl in [(1, 'YES/BULL'), (0, 'NO/BEAR')]:
            vdf = sub[sub[feat] == val]
            if len(vdf) == 0:
                continue
            wr  = vdf['win'].mean() * 100
            avg = vdf['pnl'].mean()
            print(f'    {lbl:<10}  N={len(vdf):>3}  WR={wr:>5.1f}%  avg=${avg:>+7.2f}')

    # ── Side-specific MTF alignment ──────────────────────────────────────────
    print('\n--- MTF ALIGNMENT: does both-timeframe agreement predict WR? ---')
    sub = df[df['mtf_aligned'].notna()].copy()
    for side in ['LONG', 'SHORT']:
        sdf = sub[sub['side'] == side]
        if len(sdf) == 0:
            continue
        for val, lbl in [(1, 'ALIGNED  '), (0, 'FIGHTING ')]:
            vdf = sdf[sdf['mtf_aligned'] == val]
            if len(vdf) == 0:
                continue
            wr  = vdf['win'].mean() * 100
            avg = vdf['pnl'].mean()
            print(f'  {side} {lbl}  N={len(vdf):>3}  WR={wr:>5.1f}%  avg=${avg:>+7.2f}')

    # ── Composite score: how many features fire? ─────────────────────────────
    print('\n--- COMPOSITE SCORE: how many quality checks pass simultaneously? ---')
    print('  (favorable = range_consumed<0.5, fib_dist<15, rsi_1h 40-68, hurst>0)')
    df2 = df.copy()
    df2['q_range'] = (df2['range_consumed'] < 0.5).astype(float)
    df2['q_fib']   = (df2['fib_dist'] < 15).astype(float)
    df2['q_rsi']   = ((df2['rsi_1h'] >= 40) & (df2['rsi_1h'] <= 68)).astype(float)
    df2['q_hurst'] = (df2['hurst'] > 0).astype(float)
    df2['q_mtf']   = df2['mtf_aligned'].astype(float)
    score_cols = ['q_range', 'q_fib', 'q_rsi', 'q_hurst', 'q_mtf']
    df2['n_pass'] = df2[score_cols].sum(axis=1, min_count=1)

    for n in range(6):
        ndf = df2[df2['n_pass'] == n]
        if len(ndf) == 0:
            continue
        wr  = ndf['win'].mean() * 100
        avg = ndf['pnl'].mean()
        print(f'  {n}/5 checks pass  N={len(ndf):>3}  WR={wr:>5.1f}%  avg=${avg:>+7.2f}')


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print('Loading bars (2025-06-01 → 2026-06-17, includes history for RSI/MTF)...')
    all_bars = sr.load_bars('MNQ', start='2025-06-01', end='2026-06-17')
    if all_bars is None or all_bars.empty:
        print('ERROR: No bars loaded.')
        return

    rth_all = sr.filter_ny_session(all_bars)
    start_dt = _dt.date(2026, 1, 1)
    trade_dates = sorted(d for d in set(rth_all.index.date) if d >= start_dt)
    print(f'2026 trading days: {len(trade_dates)}')

    records = []
    for i, td in enumerate(trade_dates):
        trades, bias, pos = sr.simulate_day(all_bars, td)
        if not trades:
            continue

        prior_rth = get_prior_rth(all_bars, td)
        today_rth = sr.filter_ny_session(all_bars[all_bars.index.date == td])

        for t in trades:
            feats = compute_features(all_bars, td, t, prior_rth, today_rth)
            records.append({
                'date':        str(td),
                'entry_time':  t['entry_time'],
                'side':        t['side'],
                'setup':       t['setup'],
                'grade':       t['grade'],
                'daily_bias':  bias,
                'ovn_pos':     round(pos, 3),
                'exit_reason': t['exit_reason'],
                'pnl':         t['pnl'],
                'win':         int(t['pnl'] > 0),
                **feats,
            })

        if (i + 1) % 20 == 0:
            print(f'  ...{i+1}/{len(trade_dates)} days done, {len(records)} trades so far')

    if not records:
        print('No trades found.')
        return

    df = pd.DataFrame(records)
    csv_path = 'futures/feature_study_2026.csv'
    df.to_csv(csv_path, index=False)
    print(f'\nSaved {len(df)} trades → {csv_path}')

    analyze(df)


if __name__ == '__main__':
    main()
