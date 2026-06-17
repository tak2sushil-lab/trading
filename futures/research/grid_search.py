#!/usr/bin/env python3
"""
futures/grid_search.py — Phase 3: Parameter Grid Search
Tests threshold combinations for confirmed features (Phase 2 blood test).
Trains on 2026 (in-sample), validates on 2025 (out-of-sample).

Scoring: WR × sqrt(N)  — rewards high WR, penalises tiny sample sizes.
"""
import sys, os, datetime as _dt, itertools
import numpy as np
import pandas as pd

sys.path.insert(0, '.')
import futures.sim_replay as sr
from futures.feature_study import compute_features, get_prior_rth


# ── Generate or load per-trade feature CSV ─────────────────────────────────

def generate_features(year, bar_start):
    csv_path = f'futures/feature_study_{year}.csv'
    if os.path.exists(csv_path):
        print(f'  Loaded cached {csv_path}')
        return pd.read_csv(csv_path)

    end_str = f'{year}-12-31' if year < 2026 else '2026-06-17'
    print(f'  Generating {year} features (bars {bar_start} → {end_str})...')
    all_bars = sr.load_bars('MNQ', start=bar_start, end=end_str)
    if all_bars is None or all_bars.empty:
        print(f'  ERROR: No bars for {year}'); return None

    rth_all    = sr.filter_ny_session(all_bars)
    start_dt   = _dt.date(year, 1, 1)
    trade_dates = sorted(d for d in set(rth_all.index.date) if d >= start_dt)
    print(f'  {year}: {len(trade_dates)} trading days')

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
                'date':       str(td),
                'entry_time': t['entry_time'],
                'side':       t['side'],
                'pnl':        t['pnl'],
                'win':        int(t['pnl'] > 0),
                **feats,
            })
        if (i + 1) % 20 == 0:
            print(f'  ...{i+1}/{len(trade_dates)} days, {len(records)} trades')

    df = pd.DataFrame(records)
    df.to_csv(csv_path, index=False)
    print(f'  Saved {len(df)} trades → {csv_path}')
    return df


# ── Direction-aware feature transforms ─────────────────────────────────────

def add_signed_features(df):
    """
    Convert directional features so that 'higher = better' for both LONG and SHORT.

    signed_poc : LONG: poc_dist (entry above POC is good)
                 SHORT: -poc_dist (entry below POC is good)
    rsi_dir    : LONG: rsi_1h    (high RSI = momentum)
                 SHORT: 100-rsi  (low RSI = bearish momentum → inverted so higher=better)
    mtf1h_ok   : 1 when 1H trend agrees with trade direction
    """
    df = df.copy()
    is_long = df['side'] == 'LONG'

    df['signed_poc'] = np.where(is_long, df['poc_dist'], -df['poc_dist'])
    df['signed_poc'] = pd.to_numeric(df['signed_poc'], errors='coerce')

    df['rsi_dir'] = np.where(is_long, df['rsi_1h'], 100.0 - df['rsi_1h'])
    df['rsi_dir'] = pd.to_numeric(df['rsi_dir'], errors='coerce')

    df['mtf1h_ok'] = np.where(is_long, df['mtf_1h_bull'], 1 - df['mtf_1h_bull'])
    df['mtf1h_ok'] = pd.to_numeric(df['mtf1h_ok'], errors='coerce')

    return df


# ── Apply one filter combo ──────────────────────────────────────────────────

def apply_filters(df, poc_min, atr_lo, atr_hi, rsi_floor, fib_min, mtf_req):
    mask = pd.Series(True, index=df.index)

    if poc_min is not None:
        # Require price committed far beyond prior-day volume center
        mask &= df['signed_poc'].fillna(-999999) >= poc_min

    if atr_lo is not None and atr_hi is not None:
        # Skip the medium-volatility death zone (Q2 from Phase 2)
        mask &= ~((df['atr'] >= atr_lo) & (df['atr'] <= atr_hi))

    if rsi_floor is not None:
        # Require minimum directional momentum on 1H
        mask &= df['rsi_dir'].fillna(0) >= rsi_floor

    if fib_min is not None:
        # Must be clear of Fibonacci pivot friction zone
        mask &= df['fib_dist'].fillna(99999) >= fib_min

    if mtf_req:
        # Require 1H timeframe agrees with trade direction
        mask &= df['mtf1h_ok'].fillna(0) == 1

    return df[mask]


# ── Score a filtered subset ─────────────────────────────────────────────────

def score_subset(df_filt, n_all):
    n = len(df_filt)
    if n < 10:
        return None
    wr       = df_filt['win'].mean()
    total    = df_filt['pnl'].sum()
    avg      = df_filt['pnl'].mean()
    skip_pct = 100.0 * (n_all - n) / n_all
    quality  = wr * (n ** 0.5)          # balance WR vs sample size
    return dict(n=n, wr=round(wr*100, 1), total=round(total, 0),
                avg=round(avg, 2), skip_pct=round(skip_pct, 1),
                quality=round(quality, 3))


# ── Main grid search ────────────────────────────────────────────────────────

def run_grid(df_is, df_oos=None, top_n=25):
    df_is  = add_signed_features(df_is)
    n_all  = len(df_is)
    base_wr_is = df_is['win'].mean() * 100

    if df_oos is not None:
        df_oos    = add_signed_features(df_oos)
        n_oos     = len(df_oos)
        base_wr_oos = df_oos['win'].mean() * 100
    else:
        n_oos = 0; base_wr_oos = 0.0

    print(f'\nBaseline  IS 2026: N={n_all:>3}, WR={base_wr_is:.1f}%')
    if df_oos is not None:
        print(f'Baseline OOS 2025: N={n_oos:>3}, WR={base_wr_oos:.1f}%')

    # ── Parameter grid (ranges informed by Phase 2 quartile data) ────────────
    poc_mins   = [None, 200, 300, 360, 500]      # signed_poc threshold
    atr_bands  = [None, (38,55), (40,60), (35,60), (38,62)]  # band to skip
    rsi_floors = [None, 38, 42, 45, 50]          # directional momentum floor
    fib_mins   = [None, 10, 13, 15, 20]          # min dist from nearest pivot
    mtf_opts   = [False, True]                   # require 1H alignment

    total = (len(poc_mins) * len(atr_bands) * len(rsi_floors) *
             len(fib_mins) * len(mtf_opts))
    print(f'\nTesting {total} combinations...')

    results = []
    for poc_min, atr_band, rsi_floor, fib_min, mtf_req in itertools.product(
            poc_mins, atr_bands, rsi_floors, fib_mins, mtf_opts):

        atr_lo = atr_band[0] if atr_band else None
        atr_hi = atr_band[1] if atr_band else None

        filt = apply_filters(df_is, poc_min, atr_lo, atr_hi,
                              rsi_floor, fib_min, mtf_req)
        sc = score_subset(filt, n_all)
        if sc is None:
            continue

        results.append({
            'poc_min':   poc_min,
            'atr_lo':    atr_lo,
            'atr_hi':    atr_hi,
            'rsi_floor': rsi_floor,
            'fib_min':   fib_min,
            'mtf_req':   mtf_req,
            **{f'is_{k}': v for k, v in sc.items()},
        })

    results.sort(key=lambda r: -r['is_quality'])
    print(f'  Done. {len(results)} valid combos (N≥10).')

    # ── OOS validation for top N ──────────────────────────────────────────────
    print(f'Validating top {top_n} on 2025 OOS...')
    for r in results[:top_n]:
        if df_oos is None:
            r.update({'oos_n': None, 'oos_wr': None, 'oos_avg': None})
            continue
        filt_oos = apply_filters(df_oos, r['poc_min'],
                                 r['atr_lo'], r['atr_hi'],
                                 r['rsi_floor'], r['fib_min'], r['mtf_req'])
        sc_oos = score_subset(filt_oos, n_oos)
        if sc_oos:
            r.update({'oos_n': sc_oos['n'], 'oos_wr': sc_oos['wr'],
                      'oos_avg': sc_oos['avg']})
        else:
            r.update({'oos_n': 0, 'oos_wr': None, 'oos_avg': None})

    # ── Print table ───────────────────────────────────────────────────────────
    W = 118
    print('\n' + '='*W)
    print('  GRID SEARCH — MNQ FEATURE FILTER (ranked by IS WR×√N)')
    print('  Filters: poc≥ = signed POC distance  |  ATR-skip = medium-vol death zone')
    print('           RSI≥ = directional momentum  |  Fib≥ = min pivot clearance')
    print('           MTF  = require 1H alignment')
    print('='*W)
    print(f'  {"#":>3}  {"poc≥":>5}  {"ATR-skip":>9}  {"RSI≥":>5}  {"Fib≥":>5}  '
          f'{"M":>1}  |  {"IS-N":>4}  {"IS-WR%":>6}  {"IS-avg":>7}  {"skip%":>5}  '
          f'|  {"OOS-N":>5}  {"OOS-WR%":>7}  {"OOS-avg":>8}')
    print('-'*W)

    for i, r in enumerate(results[:top_n], 1):
        poc_s  = str(r['poc_min']) if r['poc_min'] else ' none'
        atr_s  = f'{r["atr_lo"]}-{r["atr_hi"]}' if r['atr_lo'] else '     none'
        rsi_s  = str(r['rsi_floor']) if r['rsi_floor'] else ' none'
        fib_s  = str(r['fib_min']) if r['fib_min'] else 'none'
        mtf_c  = 'Y' if r['mtf_req'] else 'N'

        oos_wr  = f'{r["oos_wr"]:>6.1f}%' if r.get('oos_wr') is not None else '    n/a'
        oos_n   = f'{r["oos_n"]:>5}'       if r.get('oos_n')  is not None else '    -'
        oos_avg = f'${r["oos_avg"]:>+7.2f}' if r.get('oos_avg') is not None else '      n/a'
        delta_wr = ''
        if r.get('oos_wr') is not None:
            d = r['oos_wr'] - base_wr_oos
            delta_wr = f'({d:+.1f}pp)'

        print(f'  {i:>3}  {poc_s:>5}  {atr_s:>9}  {rsi_s:>5}  {fib_s:>4}  '
              f'{mtf_c:>1}  |  {r["is_n"]:>4}  {r["is_wr"]:>6.1f}%  '
              f'${r["is_avg"]:>+6.2f}  {r["is_skip_pct"]:>5.1f}%  '
              f'|  {oos_n}  {oos_wr}  {oos_avg}  {delta_wr}')

    print('-'*W)
    oos_base_s = f'{base_wr_oos:.1f}%' if df_oos is not None else 'n/a'
    print(f'  Baseline (no filter):  IS N={n_all} WR={base_wr_is:.1f}%  '
          f'|  OOS N={n_oos} WR={oos_base_s}')

    # ── Summary: which individual filters add the most WR lift ───────────────
    print('\n' + '='*W)
    print('  SINGLE-FILTER IMPACT (vs baseline — each filter tested alone)')
    print('='*W)
    single_params = [
        ('poc≥200',   dict(poc_min=200,  atr_lo=None, atr_hi=None, rsi_floor=None, fib_min=None, mtf_req=False)),
        ('poc≥360',   dict(poc_min=360,  atr_lo=None, atr_hi=None, rsi_floor=None, fib_min=None, mtf_req=False)),
        ('ATR-skip38-55', dict(poc_min=None, atr_lo=38, atr_hi=55, rsi_floor=None, fib_min=None, mtf_req=False)),
        ('ATR-skip40-60', dict(poc_min=None, atr_lo=40, atr_hi=60, rsi_floor=None, fib_min=None, mtf_req=False)),
        ('RSI≥42',    dict(poc_min=None,  atr_lo=None, atr_hi=None, rsi_floor=42, fib_min=None, mtf_req=False)),
        ('RSI≥45',    dict(poc_min=None,  atr_lo=None, atr_hi=None, rsi_floor=45, fib_min=None, mtf_req=False)),
        ('Fib≥13',    dict(poc_min=None,  atr_lo=None, atr_hi=None, rsi_floor=None, fib_min=13, mtf_req=False)),
        ('Fib≥15',    dict(poc_min=None,  atr_lo=None, atr_hi=None, rsi_floor=None, fib_min=15, mtf_req=False)),
        ('MTF-1H',    dict(poc_min=None,  atr_lo=None, atr_hi=None, rsi_floor=None, fib_min=None, mtf_req=True)),
    ]

    print(f'  {"Filter":<16}  {"IS-N":>4}  {"IS-WR%":>7}  {"IS-avg":>8}  '
          f'{"skip%":>6}  |  {"OOS-N":>5}  {"OOS-WR%":>7}  {"OOS-avg":>8}')
    print('-'*W)
    for lbl, p in single_params:
        filt_is = apply_filters(df_is, p['poc_min'], p['atr_lo'], p['atr_hi'],
                                p['rsi_floor'], p['fib_min'], p['mtf_req'])
        sc_is = score_subset(filt_is, n_all)
        if sc_is is None:
            continue
        oos_parts = ''
        if df_oos is not None:
            filt_oo = apply_filters(df_oos, p['poc_min'], p['atr_lo'], p['atr_hi'],
                                    p['rsi_floor'], p['fib_min'], p['mtf_req'])
            sc_oo = score_subset(filt_oo, n_oos)
            if sc_oo:
                d = sc_oo['wr'] - base_wr_oos
                oos_parts = (f'{sc_oo["n"]:>5}  {sc_oo["wr"]:>6.1f}%  '
                             f'${sc_oo["avg"]:>+7.2f}  ({d:+.1f}pp)')
            else:
                oos_parts = '    -     n/a       n/a'

        d_is = sc_is['wr'] - base_wr_is
        print(f'  {lbl:<16}  {sc_is["n"]:>4}  {sc_is["wr"]:>6.1f}% ({d_is:+.1f}pp)  '
              f'${sc_is["avg"]:>+7.2f}  {sc_is["skip_pct"]:>5.1f}%  |  {oos_parts}')

    return results[:top_n]


# ── Entry point ─────────────────────────────────────────────────────────────

def main():
    print('='*60)
    print('  Phase 3 Grid Search — MNQ Feature Filter Optimization')
    print('  In-sample: 2026  |  Out-of-sample: 2025')
    print('='*60)

    print('\n[1/2] In-sample data (2026):')
    df_2026 = generate_features(2026, '2025-06-01')
    if df_2026 is None or df_2026.empty:
        print('ERROR: No 2026 data.'); return

    print('\n[2/2] OOS data (2025):')
    df_2025 = generate_features(2025, '2024-06-01')

    run_grid(df_2026, df_2025, top_n=25)


if __name__ == '__main__':
    main()
