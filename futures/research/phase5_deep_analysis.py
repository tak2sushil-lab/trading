#!/usr/bin/env python3
"""
futures/phase5_deep_analysis.py — Two questions before building Phase 5 into live

Q1: Is 10:30am the right IB gate, or can we detect TRENDING regime earlier?
    For each 2025-2026 trade day, compute IB range at 9:45, 10:00, 10:15, 10:30.
    Find when TRENDING days become identifiable.

Q2: Do Fibonacci (H5/H6), Hurst, and OVN_POS have regime-specific value in 2025-2026?
    We dropped them as all-weather heroes but they may belong in specific regimes.

Data: avengers_2025_2026.csv only. No 2021-2024.
"""
import sys, os
import numpy as np
import pandas as pd
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from futures.collect_bars import load_bars

# ── Fixed thresholds (mirrors phase5_regime.py champion) ─────────────────────
QUIET_IB     = 100.0
TRENDING_IB  = 200.0

NY = 'America/New_York'


def compute_ib_snapshots(all_bars, dates):
    """
    For each date, compute IB range at 9:45, 10:00, 10:15, 10:30 ET.
    Returns DataFrame with date + ib_945, ib_1000, ib_1015, ib_1030.
    """
    rows = []
    for d in dates:
        base = pd.Timestamp(d).tz_localize(NY)
        snaps = {
            '9:45':  base.replace(hour=9,  minute=45),
            '10:00': base.replace(hour=10, minute=0),
            '10:15': base.replace(hour=10, minute=15),
            '10:30': base.replace(hour=10, minute=30),
        }
        open_t = base.replace(hour=9, minute=30)
        row = {'date': str(d)}
        for label, snap_ts in snaps.items():
            window = all_bars[(all_bars.index >= open_t) & (all_bars.index <= snap_ts)]
            row[f'ib_{label.replace(":", "")}'] = (
                float(window['high'].max() - window['low'].min())
                if len(window) >= 2 else np.nan
            )
        rows.append(row)
    return pd.DataFrame(rows)


def regime_label(ib):
    if pd.isna(ib) or ib <= 0:
        return 'UNKNOWN'
    if ib < QUIET_IB:
        return 'QUIET'
    if ib >= TRENDING_IB:
        return 'TRENDING'
    return 'CHOPPY'


def wr_lift(df, col, threshold=None, below=True):
    """
    WR when col passes threshold vs fails.
    If threshold is None, treats col as boolean (1=passes, 0=fails).
    """
    if threshold is not None:
        passes = df[df[col] >= threshold] if not below else df[df[col] < threshold]
        fails  = df[df[col] < threshold]  if not below else df[df[col] >= threshold]
    else:
        passes = df[df[col] == 1]
        fails  = df[df[col] == 0]

    if len(passes) < 5 or len(fails) < 5:
        return None, None, None, len(passes), len(fails)
    wr_p = passes['win'].mean() * 100
    wr_f = fails['win'].mean()  * 100
    return round(wr_p, 1), round(wr_f, 1), round(wr_p - wr_f, 1), len(passes), len(fails)


# ═══════════════════════════════════════════════════════════════════════════════
def main():
    print("Phase 5 Deep Analysis — 2025-2026 data only")
    print("=" * 72)

    csv_path = 'futures/avengers_2025_2026.csv'
    df = pd.read_csv(csv_path)
    df['date_parsed'] = pd.to_datetime(df['date'])
    is_long = df['side'] == 'LONG'
    df['rsi_dir']    = np.where(is_long, df['rsi_1h'],   100.0 - df['rsi_1h'])
    df['signed_poc'] = np.where(is_long, df['poc_dist'], -df['poc_dist'])

    dates = sorted(df['date_parsed'].dt.date.unique())
    print(f"Loaded {len(df)} trades across {len(dates)} trading days (2025-2026)\n")

    # ── Load bars ─────────────────────────────────────────────────────────────
    print("Loading bars...")
    all_bars = load_bars('MNQ', start='2024-12-01', end='2026-12-31')
    print(f"Bars loaded: {len(all_bars):,} rows\n")

    # ─────────────────────────────────────────────────────────────────────────
    # Q1: IB range stabilization — when can we detect TRENDING days?
    # ─────────────────────────────────────────────────────────────────────────
    print("═" * 72)
    print("Q1: IB RANGE STABILIZATION — when do TRENDING days become visible?")
    print("═" * 72)

    snaps = compute_ib_snapshots(all_bars, dates)
    snaps['regime_1030'] = snaps['ib_1030'].apply(regime_label)

    print(f"\nFinal 10:30 regime distribution ({len(snaps)} days):")
    for r in ['QUIET', 'CHOPPY', 'TRENDING']:
        n = (snaps['regime_1030'] == r).sum()
        print(f"  {r:10}: {n:3d} days ({n/len(snaps)*100:.0f}%)")

    # For TRENDING days: what % were already classifiable at earlier snapshots?
    trending_days = snaps[snaps['regime_1030'] == 'TRENDING']
    print(f"\nFor {len(trending_days)} TRENDING days (IB≥200 at 10:30):")
    print(f"  {'Snapshot':>8}  {'Median IB':>9}  {'p25 IB':>7}  {'≥200pts':>8}  {'≥150pts':>8}  {'≥120pts':>8}")
    print(f"  {'-'*8}  {'-'*9}  {'-'*7}  {'-'*8}  {'-'*8}  {'-'*8}")
    for col, label in [('ib_945','9:45'), ('ib_1000','10:00'), ('ib_1015','10:15'), ('ib_1030','10:30')]:
        vals = trending_days[col].dropna()
        med  = vals.median()
        p25  = vals.quantile(0.25)
        pct200 = (vals >= 200).mean() * 100
        pct150 = (vals >= 150).mean() * 100
        pct120 = (vals >= 120).mean() * 100
        print(f"  {label:>8}  {med:>9.0f}  {p25:>7.0f}  {pct200:>7.0f}%  {pct150:>7.0f}%  {pct120:>7.0f}%")

    # For NON-TRENDING days: what % would false-positive as trending at earlier snapshots?
    non_trending = snaps[snaps['regime_1030'] != 'TRENDING']
    print(f"\nFor {len(non_trending)} non-TRENDING days (QUIET+CHOPPY at 10:30):")
    print(f"  False-positive rate (non-trending day that LOOKS trending at each snapshot):")
    for col, label in [('ib_945','9:45'), ('ib_1000','10:00'), ('ib_1015','10:15'), ('ib_1030','10:30')]:
        vals = non_trending[col].dropna()
        fp200 = (vals >= 200).mean() * 100
        fp150 = (vals >= 150).mean() * 100
        print(f"  {label:>8}:  fp(≥200)={fp200:.0f}%   fp(≥150)={fp150:.0f}%")

    # Also: what % of QUIET days are correctly identified at 10:00?
    quiet_days = snaps[snaps['regime_1030'] == 'QUIET']
    if len(quiet_days) > 0:
        print(f"\nFor {len(quiet_days)} QUIET days (IB<100 at 10:30):")
        for col, label in [('ib_945','9:45'), ('ib_1000','10:00'), ('ib_1015','10:15'), ('ib_1030','10:30')]:
            vals = quiet_days[col].dropna()
            pct_correct = (vals < 100).mean() * 100 if len(vals) > 0 else 0
            print(f"  {label:>8}: correctly quiet (IB<100): {pct_correct:.0f}%")

    # Entry time distribution of actual trades
    print(f"\nActual entry time distribution (all 478 trades):")
    df['entry_hour_min'] = df['entry_time'].str[:5]
    time_dist = df.groupby('entry_hour_min').agg(
        n=('win','count'), wr=('win','mean')
    ).reset_index()
    time_dist['wr'] = (time_dist['wr'] * 100).round(1)
    print(f"  {'Time':>6}  {'N':>4}  {'WR%':>6}")
    for _, row in time_dist.iterrows():
        bar = '█' * int(row['n'] / 3)
        print(f"  {row['entry_hour_min']:>6}  {int(row['n']):>4}  {row['wr']:>5.1f}%  {bar}")

    # ─────────────────────────────────────────────────────────────────────────
    # Q2: Fibonacci, Hurst, OVN_POS — regime-specific value in 2025-2026
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "═" * 72)
    print("Q2: FEATURE RE-EVALUATION — by regime in 2025-2026")
    print("═" * 72)

    # Merge IB range into trades
    ib_map = dict(zip(snaps['date'], snaps['ib_1030']))
    df['ib_range'] = df['date'].map(ib_map)
    df['regime']   = df['ib_range'].apply(regime_label)

    print(f"\nTrade regime distribution:")
    for r in ['QUIET', 'CHOPPY', 'TRENDING']:
        sub = df[df['regime'] == r]
        print(f"  {r:10}: {len(sub):3d} trades, base WR={sub['win'].mean()*100:.1f}%")

    # ── H5: fib_floor ─────────────────────────────────────────────────────────
    print(f"\n── H5: fib_floor (structural Fibonacci support depth) ──")
    print(f"  Hypothesis: most valuable on QUIET/STRUCTURAL days, not TRENDING")
    # Look at it as a continuous variable by regime
    for r in ['QUIET', 'CHOPPY', 'TRENDING']:
        sub = df[df['regime'] == r].dropna(subset=['fib_floor'])
        if len(sub) < 5:
            print(f"  {r:10}: N={len(sub)} (too small)")
            continue
        # Best threshold for this regime?
        threshs = [300, 400, 446, 500, 600]
        print(f"  {r:10} (N={len(sub)}, base WR={sub['win'].mean()*100:.1f}%):")
        for th in threshs:
            hi = sub[sub['fib_floor'] >= th]
            lo = sub[sub['fib_floor'] <  th]
            if len(hi) < 5:
                continue
            lift = hi['win'].mean()*100 - lo['win'].mean()*100
            print(f"    fib_floor≥{th}: N={len(hi):3d}, WR={hi['win'].mean()*100:.1f}%, lift={lift:+.1f}pp")

    # ── H6: near_golden ───────────────────────────────────────────────────────
    print(f"\n── H6: near_golden (within 25pts of 0.618 Fibonacci level) ──")
    print(f"  Hypothesis: Fibonacci retracement support most powerful on QUIET days")
    for r in ['QUIET', 'CHOPPY', 'TRENDING']:
        sub = df[df['regime'] == r]
        if len(sub) < 5:
            continue
        on  = sub[sub['near_golden'] == 1]
        off = sub[sub['near_golden'] == 0]
        if len(on) < 3 or len(off) < 3:
            print(f"  {r:10}: N={len(sub)}, near_golden N={len(on)} (too small)")
            continue
        lift = on['win'].mean()*100 - off['win'].mean()*100
        print(f"  {r:10}: N_golden={len(on):3d}, WR={on['win'].mean()*100:.1f}%  "
              f"| N_other={len(off):3d}, WR={off['win'].mean()*100:.1f}%  | lift={lift:+.1f}pp")

    # ── Hurst exponent ────────────────────────────────────────────────────────
    print(f"\n── Hurst exponent — regime detector or trade-level hero? ──")
    print(f"  H>0.5 = trending (persistent), H<0.5 = mean-reverting, H≈0.5 = random")

    # Distribution of Hurst by day regime
    df_h = df.dropna(subset=['hurst'])
    print(f"\n  Hurst distribution by regime:")
    for r in ['QUIET', 'CHOPPY', 'TRENDING']:
        sub = df_h[df_h['regime'] == r]
        if len(sub) < 5:
            continue
        print(f"  {r:10}: median={sub['hurst'].median():.3f}  "
              f"p25={sub['hurst'].quantile(0.25):.3f}  "
              f"p75={sub['hurst'].quantile(0.75):.3f}  N={len(sub)}")

    # Hurst as trade-level filter — does high Hurst within each regime add value?
    print(f"\n  Hurst as trade-level hero (threshold ≥0.55):")
    for r in ['QUIET', 'CHOPPY', 'TRENDING', 'ALL']:
        sub = df_h if r == 'ALL' else df_h[df_h['regime'] == r]
        hi  = sub[sub['hurst'] >= 0.55]
        lo  = sub[sub['hurst'] <  0.55]
        if len(hi) < 5 or len(lo) < 5:
            continue
        lift = hi['win'].mean()*100 - lo['win'].mean()*100
        print(f"  {r:10}: hurst≥0.55 N={len(hi):3d} WR={hi['win'].mean()*100:.1f}%  "
              f"| hurst<0.55 N={len(lo):3d} WR={lo['win'].mean()*100:.1f}%  | lift={lift:+.1f}pp")

    # Hurst as regime DETECTOR — does Hurst at entry predict the IB regime?
    print(f"\n  Hurst vs IB-regime agreement:")
    df_h['hurst_regime'] = np.where(df_h['hurst'] >= 0.55, 'TRENDING',
                           np.where(df_h['hurst'] <= 0.45, 'QUIET', 'CHOPPY'))
    agree = (df_h['hurst_regime'] == df_h['regime']).mean() * 100
    print(f"  Agreement (hurst_regime vs IB regime): {agree:.1f}%")
    for r in ['QUIET', 'CHOPPY', 'TRENDING']:
        sub = df_h[df_h['regime'] == r]
        if len(sub) < 5:
            continue
        pct = (sub['hurst_regime'] == r).mean() * 100
        med_h = sub['hurst'].median()
        print(f"    IB={r:10}: Hurst agrees {pct:.0f}% of the time  (median Hurst={med_h:.3f})")

    # ── OVN_POS by regime ─────────────────────────────────────────────────────
    print(f"\n── OVN_POS — regime-specific predictive power? ──")
    print(f"  Hypothesis: overnight positioning matters most on QUIET days")
    df_o = df.dropna(subset=['ovn_pos'])

    print(f"\n  OVN_POS distribution by regime:")
    for r in ['QUIET', 'CHOPPY', 'TRENDING']:
        sub = df_o[df_o['regime'] == r]
        if len(sub) < 5:
            continue
        print(f"  {r:10}: median={sub['ovn_pos'].median():.2f}  "
              f"p25={sub['ovn_pos'].quantile(0.25):.2f}  "
              f"p75={sub['ovn_pos'].quantile(0.75):.2f}  N={len(sub)}")

    # LONG trades: does high OVN_POS (≥0.70) predict better outcomes by regime?
    print(f"\n  LONG trades: OVN_POS≥0.70 (bullish overnight) WR lift by regime:")
    longs = df_o[df_o['side'] == 'LONG']
    for r in ['QUIET', 'CHOPPY', 'TRENDING', 'ALL']:
        sub = longs if r == 'ALL' else longs[longs['regime'] == r]
        hi  = sub[sub['ovn_pos'] >= 0.70]
        lo  = sub[sub['ovn_pos'] <  0.70]
        if len(hi) < 5 or len(lo) < 5:
            continue
        lift = hi['win'].mean()*100 - lo['win'].mean()*100
        print(f"  {r:10}: ovn≥0.70 N={len(hi):3d} WR={hi['win'].mean()*100:.1f}%  "
              f"| ovn<0.70 N={len(lo):3d} WR={lo['win'].mean()*100:.1f}%  | lift={lift:+.1f}pp")

    print(f"\n  SHORT trades: OVN_POS≤0.20 (bearish overnight) WR lift by regime:")
    shorts = df_o[df_o['side'] == 'SHORT']
    for r in ['QUIET', 'CHOPPY', 'TRENDING', 'ALL']:
        sub = shorts if r == 'ALL' else shorts[shorts['regime'] == r]
        lo  = sub[sub['ovn_pos'] <= 0.20]
        hi  = sub[sub['ovn_pos'] >  0.20]
        if len(lo) < 3 or len(hi) < 3:
            continue
        lift = lo['win'].mean()*100 - hi['win'].mean()*100
        print(f"  {r:10}: ovn≤0.20 N={len(lo):3d} WR={lo['win'].mean()*100:.1f}%  "
              f"| ovn>0.20 N={len(hi):3d} WR={hi['win'].mean()*100:.1f}%  | lift={lift:+.1f}pp")

    # ── 2026 subslice — most recent weather ───────────────────────────────────
    print("\n" + "═" * 72)
    print("2026 SUBSLICE — most recent weather (Jan-Jun 2026)")
    print("═" * 72)
    df26 = df[df['date_parsed'] >= pd.Timestamp('2026-01-01')]
    print(f"\n{len(df26)} trades in 2026 YTD")
    if len(df26) >= 20:
        print(f"  Base WR: {df26['win'].mean()*100:.1f}%")
        for r in ['QUIET', 'CHOPPY', 'TRENDING']:
            sub = df26[df26['regime'] == r]
            print(f"  {r:10}: {len(sub)} trades, WR={sub['win'].mean()*100:.1f}%")

        print(f"\n  H5 fib_floor in 2026 by regime:")
        for r in ['CHOPPY', 'TRENDING']:
            sub = df26[df26['regime'] == r].dropna(subset=['fib_floor'])
            if len(sub) < 5:
                print(f"    {r}: N={len(sub)} too small")
                continue
            hi = sub[sub['fib_floor'] >= 446]
            lo = sub[sub['fib_floor'] <  446]
            if len(hi) >= 3 and len(lo) >= 3:
                lift = hi['win'].mean()*100 - lo['win'].mean()*100
                print(f"    {r:10}: fib≥446 N={len(hi)} WR={hi['win'].mean()*100:.1f}%  "
                      f"fib<446 N={len(lo)} WR={lo['win'].mean()*100:.1f}%  lift={lift:+.1f}pp")

        print(f"\n  H6 near_golden in 2026:")
        for r in ['QUIET', 'CHOPPY', 'TRENDING']:
            sub = df26[df26['regime'] == r]
            on  = sub[sub['near_golden'] == 1]
            off = sub[sub['near_golden'] == 0]
            if len(on) < 3 or len(off) < 3:
                continue
            lift = on['win'].mean()*100 - off['win'].mean()*100
            print(f"    {r:10}: golden N={len(on)} WR={on['win'].mean()*100:.1f}%  "
                  f"other N={len(off)} WR={off['win'].mean()*100:.1f}%  lift={lift:+.1f}pp")

        print(f"\n  OVN_POS LONG in 2026 by regime:")
        l26 = df26[df26['side'] == 'LONG'].dropna(subset=['ovn_pos'])
        for r in ['QUIET', 'CHOPPY', 'TRENDING', 'ALL']:
            sub = l26 if r == 'ALL' else l26[l26['regime'] == r]
            hi  = sub[sub['ovn_pos'] >= 0.70]
            lo  = sub[sub['ovn_pos'] <  0.70]
            if len(hi) < 3 or len(lo) < 3:
                continue
            lift = hi['win'].mean()*100 - lo['win'].mean()*100
            print(f"    {r:10}: ovn≥0.70 N={len(hi)} WR={hi['win'].mean()*100:.1f}%  "
                  f"| ovn<0.70 N={len(lo)} WR={lo['win'].mean()*100:.1f}%  lift={lift:+.1f}pp")

    print("\n" + "=" * 72)
    print("Analysis complete.")


if __name__ == '__main__':
    main()
