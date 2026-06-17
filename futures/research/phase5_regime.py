#!/usr/bin/env python3
"""
futures/phase5_regime.py — Phase 5 blood test: regime-aware hero weighting

Question: does IB range predict which heroes dominate?
  - High IB range (trending day)  → expect H2+H3 (MTF/RSI) to outperform
  - Low-mid IB range (choppy day) → expect H4+H1 (POC/ATR) to outperform

Method:
  1. Load avengers_2025_2026.csv (478 IS trades — already validated)
  2. Annotate each trade with its day's IB range (9:30–10:30 H-L from bars DB)
  3. Bin by IB range (every 20pts) → per-hero WR lift table
  4. Grid search (lo, hi) splits → find thresholds where hero dominance diverges most

No retune of hero thresholds. Only regime boundary discovery.
"""

import sys, os
import numpy as np
import pandas as pd
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from futures.collect_bars import load_bars, filter_ny_session
import futures.sim_replay as sr

# ── Hero thresholds — FIXED from IS (mirror phase4_oos.py) ───────────────────
ATR_DEATH_LO  = 38.0
ATR_DEATH_HI  = 55.0
RSI_DIR_FLOOR = 50.0
POC_SIGNED_FL = 200.0


def compute_ib_range_per_day(all_bars, dates):
    """
    For each date, compute IB range = max(high) - min(low) of 9:30–10:30 ET bars.
    Returns dict {date -> ib_range_pts}.
    """
    ny = 'America/New_York'
    result = {}
    for d in dates:
        lo = pd.Timestamp(d).tz_localize(ny).replace(hour=9, minute=30, second=0)
        hi = pd.Timestamp(d).tz_localize(ny).replace(hour=10, minute=30, second=0)
        day_bars = all_bars[(all_bars.index >= lo) & (all_bars.index <= hi)]
        if len(day_bars) < 2:
            result[str(d)] = np.nan
        else:
            result[str(d)] = float(day_bars['high'].max() - day_bars['low'].min())
    return result


def add_heroes(df):
    """Recompute 4 confirmed hero flags from CSV columns."""
    df = df.copy()
    is_long = df['side'] == 'LONG'
    df['rsi_dir']    = np.where(is_long, df['rsi_1h'],   100.0 - df['rsi_1h'])
    df['signed_poc'] = np.where(is_long, df['poc_dist'],  -df['poc_dist'])
    df['mtf1h_ok']   = np.where(is_long, df['mtf_1h_bull'], 1 - df['mtf_1h_bull'])

    df['H1'] = ((df['atr'] < ATR_DEATH_LO) | (df['atr'] > ATR_DEATH_HI)).astype(int)
    df['H2'] = (df['mtf1h_ok'].fillna(0) >= 1).astype(int)
    df['H3'] = (df['rsi_dir'].fillna(0) >= RSI_DIR_FLOOR).astype(int)
    df['H4'] = (df['signed_poc'].fillna(0) >= POC_SIGNED_FL).astype(int)

    df['score'] = df[['H1','H2','H3','H4']].sum(axis=1)
    return df


def hero_wr_lift(df, hero_col):
    """WR when hero=1 minus WR when hero=0. Returns (wr1, wr0, lift, n1, n0)."""
    on  = df[df[hero_col] == 1]
    off = df[df[hero_col] == 0]
    if len(on) == 0 or len(off) == 0:
        return None, None, None, 0, 0
    wr1 = on['win'].mean() * 100
    wr0 = off['win'].mean() * 100
    return round(wr1, 1), round(wr0, 1), round(wr1 - wr0, 1), len(on), len(off)


def report_by_bin(df, bin_col='ib_bin'):
    """Print per-hero WR lift for each IB range bin."""
    heroes = ['H1', 'H2', 'H3', 'H4']
    labels = {'H1': 'ATR_SAFE', 'H2': 'MTF_ALIGN', 'H3': 'RSI_MOMO', 'H4': 'POC_BREAK'}

    bins = sorted(df[bin_col].dropna().unique())
    print(f"\n{'IB BIN':>10}  {'N':>4}  {'BASE_WR':>7}  "
          f"{'H1(ATR)':>8}  {'H2(MTF)':>8}  {'H3(RSI)':>8}  {'H4(POC)':>8}  "
          f"{'TREND_HEROES':>12}  {'STRUCT_HEROES':>13}")
    print("-" * 105)

    for b in bins:
        sub = df[df[bin_col] == b]
        n   = len(sub)
        if n < 5:
            continue
        base_wr = sub['win'].mean() * 100

        lifts = {}
        for h in heroes:
            _, _, lift, n1, _ = hero_wr_lift(sub, h)
            lifts[h] = lift if lift is not None else 0.0

        # TRENDING heroes = H2+H3 avg lift; STRUCTURAL heroes = H1+H4 avg lift
        trend  = (lifts['H2'] + lifts['H3']) / 2
        struct = (lifts['H1'] + lifts['H4']) / 2
        dom    = 'TREND>>' if trend > struct + 2 else ('STRUCT>>' if struct > trend + 2 else 'EQUAL')

        h1s = f"{lifts['H1']:+.1f}"
        h2s = f"{lifts['H2']:+.1f}"
        h3s = f"{lifts['H3']:+.1f}"
        h4s = f"{lifts['H4']:+.1f}"
        print(f"{b:>10}  {n:>4}  {base_wr:>6.1f}%  "
              f"{h1s:>8}  {h2s:>8}  {h3s:>8}  {h4s:>8}  "
              f"{trend:>+11.1f}  {struct:>+12.1f}  {dom}")


def grid_search_thresholds(df, min_bucket=25):
    """
    Grid search over (lo_thresh, hi_thresh) pairs to find the IB range split where
    hero dominance is most clearly regime-specific.

    Objective: maximize (trend_cross in HIGH bucket) + (struct_cross in LOW bucket)
    where:
      trend_cross  = (H2_lift + H3_lift) - (H1_lift + H4_lift) in IB >= hi_thresh
      struct_cross = (H1_lift + H4_lift) - (H2_lift + H3_lift) in IB < lo_thresh
    """
    lo_range = list(range(80, 220, 10))
    hi_range = list(range(150, 380, 10))

    best_score = -999
    best_lo    = None
    best_hi    = None
    results    = []

    for lo in lo_range:
        for hi in hi_range:
            if hi <= lo + 15:
                continue

            quiet   = df[df['ib_range'] < lo]
            choppy  = df[(df['ib_range'] >= lo) & (df['ib_range'] < hi)]
            trend   = df[df['ib_range'] >= hi]

            if len(quiet) < min_bucket or len(trend) < min_bucket:
                continue

            def hero_avg_lift(sub, heroes):
                lifts = []
                for h in heroes:
                    on  = sub[sub[h] == 1]
                    off = sub[sub[h] == 0]
                    if len(on) < 3 or len(off) < 3:
                        return 0.0
                    lifts.append(on['win'].mean() * 100 - off['win'].mean() * 100)
                return sum(lifts) / len(lifts)

            t_trend  = hero_avg_lift(trend,  ['H2', 'H3'])
            s_trend  = hero_avg_lift(trend,  ['H1', 'H4'])
            t_quiet  = hero_avg_lift(quiet,  ['H2', 'H3'])
            s_quiet  = hero_avg_lift(quiet,  ['H1', 'H4'])

            trend_cross  = t_trend  - s_trend   # want >0 in trending (H2+H3 dominate)
            struct_cross = s_quiet  - t_quiet   # want >0 in quiet (H1+H4 dominate)
            total_score  = trend_cross + struct_cross

            results.append({
                'lo': lo, 'hi': hi,
                'n_quiet': len(quiet), 'n_choppy': len(choppy), 'n_trend': len(trend),
                'trend_cross': round(trend_cross, 2),
                'struct_cross': round(struct_cross, 2),
                'total': round(total_score, 2),
            })

            if total_score > best_score:
                best_score = total_score
                best_lo    = lo
                best_hi    = hi

    if not results:
        print("  No valid (lo, hi) pairs found with enough trades in each bucket.")
        return None, None
    df_res = pd.DataFrame(results).sort_values('total', ascending=False)

    print("\n── Grid Search: Top 15 (lo_thresh, hi_thresh) splits ──")
    print(f"{'LO':>4}  {'HI':>4}  {'N_QUIET':>7}  {'N_CHOP':>6}  {'N_TREND':>7}  "
          f"{'TREND_CROSS':>11}  {'STRUCT_CROSS':>12}  {'TOTAL':>6}")
    print("-" * 80)
    for _, r in df_res.head(15).iterrows():
        print(f"{int(r['lo']):>4}  {int(r['hi']):>4}  {int(r['n_quiet']):>7}  "
              f"{int(r['n_choppy']):>6}  {int(r['n_trend']):>7}  "
              f"{r['trend_cross']:>+11.2f}  {r['struct_cross']:>+12.2f}  {r['total']:>+6.2f}")

    return best_lo, best_hi


def report_regime_detail(df, lo, hi):
    """Show hero WR lift for QUIET / CHOPPY / TRENDING under the chosen thresholds."""
    buckets = {
        f'QUIET    (ib < {lo}pts)':        df[df['ib_range'] < lo],
        f'CHOPPY   ({lo}–{hi}pts)':        df[(df['ib_range'] >= lo) & (df['ib_range'] < hi)],
        f'TRENDING (ib >= {hi}pts)':        df[df['ib_range'] >= hi],
    }

    print(f"\n── Champion split: QUIET < {lo}pts | CHOPPY {lo}–{hi}pts | TRENDING >= {hi}pts ──")
    print(f"{'REGIME':30}  {'N':>4}  {'WR%':>5}  "
          f"{'H1(ATR)':>8}  {'H2(MTF)':>8}  {'H3(RSI)':>8}  {'H4(POC)':>8}  "
          f"{'TREND_H':>8}  {'STRUCT_H':>9}  VERDICT")
    print("-" * 120)

    for name, sub in buckets.items():
        if len(sub) < 5:
            print(f"  {name:30}  N={len(sub)} too small")
            continue
        wr = sub['win'].mean() * 100
        heroes = ['H1','H2','H3','H4']
        lifts  = {}
        for h in heroes:
            on  = sub[sub[h] == 1]
            off = sub[sub[h] == 0]
            if len(on) < 3 or len(off) < 3:
                lifts[h] = 0.0
            else:
                lifts[h] = round(on['win'].mean() * 100 - off['win'].mean() * 100, 1)

        trend_avg  = (lifts['H2'] + lifts['H3']) / 2
        struct_avg = (lifts['H1'] + lifts['H4']) / 2
        verdict    = 'TREND>>' if trend_avg > struct_avg + 1 else (
                     'STRUCT>>' if struct_avg > trend_avg + 1 else 'MIXED')

        print(f"  {name:30}  {len(sub):>4}  {wr:>4.1f}%  "
              f"{lifts['H1']:>+8.1f}  {lifts['H2']:>+8.1f}  "
              f"{lifts['H3']:>+8.1f}  {lifts['H4']:>+8.1f}  "
              f"{trend_avg:>+7.1f}  {struct_avg:>+8.1f}  {verdict}")

    # Also show: with gate active (score>=3), how does each regime do?
    print(f"\n── Composite gate (score>=3) performance by regime ──")
    print(f"{'REGIME':30}  {'ALL_N':>5}  {'ALL_WR':>6}  "
          f"{'GATE_N':>6}  {'GATE_WR':>7}  {'SKIP_N':>6}  {'SKIP_WR':>7}  {'WR_LIFT':>8}")
    print("-" * 90)
    for name, sub in buckets.items():
        if len(sub) < 5:
            continue
        gated = sub[sub['score'] >= 3]
        skip  = sub[sub['score'] < 3]
        all_wr  = sub['win'].mean() * 100
        gate_wr = gated['win'].mean() * 100 if len(gated) > 0 else 0.0
        skip_wr = skip['win'].mean() * 100  if len(skip)  > 0 else 0.0
        lift    = gate_wr - all_wr
        print(f"  {name:30}  {len(sub):>5}  {all_wr:>5.1f}%  "
              f"{len(gated):>6}  {gate_wr:>6.1f}%  "
              f"{len(skip):>6}  {skip_wr:>6.1f}%  "
              f"{lift:>+7.1f}pp")


def main():
    print("Phase 5 Regime Blood Test — IB range → hero dominance mapping")
    print("=" * 70)

    # ── Load IS data ──────────────────────────────────────────────────────────
    csv_path = 'futures/avengers_2025_2026.csv'
    if not os.path.exists(csv_path):
        print(f"ERROR: {csv_path} not found. Run phase4_oos.py first.")
        sys.exit(1)

    df = pd.read_csv(csv_path)
    df = add_heroes(df)
    print(f"Loaded {len(df)} IS trades (2025-2026)")

    # ── Load futures bars and compute IB range per day ────────────────────────
    print("Loading MNQ bars (2024-12-01 onward for IB range computation)...")
    all_bars = load_bars('MNQ', start='2024-12-01', end='2026-12-31')
    if all_bars is None or all_bars.empty:
        print("ERROR: no bars loaded")
        sys.exit(1)

    unique_dates = sorted(pd.to_datetime(df['date']).dt.date.unique())
    print(f"Computing IB range for {len(unique_dates)} unique trading days...")
    ib_map = compute_ib_range_per_day(all_bars, unique_dates)

    df['ib_range'] = df['date'].map(ib_map)
    missing = df['ib_range'].isna().sum()
    if missing > 0:
        print(f"  Warning: {missing} trades missing IB range (dropped)")
    df = df.dropna(subset=['ib_range'])

    print(f"\nIB range distribution ({len(df)} trades):")
    pcts = [10, 25, 50, 75, 90]
    for p in pcts:
        print(f"  p{p:02d}: {np.percentile(df['ib_range'], p):.1f} pts")
    print(f"  min: {df['ib_range'].min():.1f}  max: {df['ib_range'].max():.1f}")

    # ── Bin analysis — use data-driven percentile bins ───────────────────────
    # MNQ IB ranges are large (100-500pts). Use quantile-based bins.
    p25 = np.percentile(df['ib_range'], 25)
    p50 = np.percentile(df['ib_range'], 50)
    p75 = np.percentile(df['ib_range'], 75)
    # Round to nearest 10 for clean labels
    b1 = int(round(p25 / 10) * 10)
    b2 = int(round(p50 / 10) * 10)
    b3 = int(round(p75 / 10) * 10)
    bins     = [0, b1, b2, b3, 9999]
    bin_lbls = [f'<{b1}', f'{b1}-{b2}', f'{b2}-{b3}', f'>{b3}']
    df['ib_bin'] = pd.cut(df['ib_range'], bins=bins, labels=bin_lbls)
    print(f"\nBins (quartile-based): {bin_lbls}")

    print("\n── Per-hero WR lift by IB range bin ──")
    print("(lift = WR when hero=1 minus WR when hero=0; positive = hero helps)")
    report_by_bin(df)

    # ── Grid search ───────────────────────────────────────────────────────────
    print("\n")
    best_lo, best_hi = grid_search_thresholds(df)

    if best_lo and best_hi:
        report_regime_detail(df, best_lo, best_hi)

    # ── Implication summary ───────────────────────────────────────────────────
    print("\n── TAKEAWAY ──────────────────────────────────────────────────────")
    print("Use grid search output to set Phase 5 regime thresholds.")
    print("Champion (lo, hi) defines: QUIET < lo | CHOPPY lo–hi | TRENDING >= hi")
    print("")
    print("Regime weighting scheme (Phase 5 design):")
    print("  TRENDING  → H2×2 + H3×2 + H1×1 + H4×1 (max 6)  skip<4, gold>=5")
    print("  CHOPPY    → H1×2 + H4×2 + H2×1 + H3×1 (max 6)  skip<4, gold>=5")
    print("  QUIET     → H1×1 + H2×1 + H3×1 + H4×1 (max 4)  skip<2, gold>=4")
    print("")
    if best_lo and best_hi:
        print(f"Data-driven thresholds: lo={best_lo}pts, hi={best_hi}pts")
    print("=" * 70)


if __name__ == '__main__':
    main()
