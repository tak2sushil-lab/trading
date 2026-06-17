#!/usr/bin/env python3
"""
futures/fib_deep.py — Deep Fibonacci analysis
6 directional features: RUNWAY, FLOOR, DENSITY, POSITION, GOLDEN, STRUCT_RR
Run on 2025-2026 sim trades to find real Fibonacci signal.
"""
import sys, datetime as dt
import numpy as np, pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, '.')
import futures.sim_replay as sr
from futures.feature_study import compute_fib_pivots, get_prior_rth


def fib_features_deep(entry_price, pivots, side):
    """
    6 directional Fibonacci features.

    Old (broken): fib_dist = distance to nearest of any 12 levels (unsigned, non-directional)
    New: separate resistance from support, measure runway/floor relative to direction.
    """
    if not pivots:
        return {}

    P = pivots['P']

    # Resistance levels (above pivot point — ceilings for LONG, targets for SHORT)
    res_keys = ['R1', 'R2', 'R3', 'FR618', 'FR382', 'FR500']
    sup_keys = ['S1', 'S2', 'S3', 'FS618', 'FS382', 'FS500']

    all_res = sorted([pivots[k] for k in res_keys if pivots[k] > P])
    all_sup = sorted([pivots[k] for k in sup_keys if pivots[k] < P], reverse=True)
    all_levels = list(pivots.values())

    if side == 'LONG':
        # Runway: distance from entry to nearest resistance ABOVE (room to target)
        walls   = [r for r in all_res if r > entry_price]
        runway  = round(min(walls) - entry_price, 1) if walls else 999.0
        # Floor: nearest support BELOW entry (structural stop protection)
        floors  = [s for s in all_sup if s < entry_price]
        floor   = round(entry_price - max(floors), 1) if floors else 999.0
        # Is entry above pivot point? (bullish structural bias)
        above_P = int(entry_price > P)
        # Near Golden Ratio resistance (FR618) — blocking ceiling ahead?
        fr618   = pivots.get('FR618')
        near_golden = int(fr618 is not None and 0 < fr618 - entry_price < 25)

    else:  # SHORT
        # Runway: distance from entry to nearest support BELOW (room to fall)
        walls   = [s for s in all_sup if s < entry_price]
        runway  = round(entry_price - max(walls), 1) if walls else 999.0
        # Floor: nearest resistance ABOVE entry (ceiling protection for short)
        floors  = [r for r in all_res if r > entry_price]
        floor   = round(min(floors) - entry_price, 1) if floors else 999.0
        # Below pivot point = bearish structural bias (0 = below P = short-friendly)
        above_P = int(entry_price > P)
        # Near Golden Ratio support (FS618) — floor blocking the downside?
        fs618   = pivots.get('FS618')
        near_golden = int(fs618 is not None and 0 < entry_price - fs618 < 25)

    # Cluster density: how many pivot levels within ±40pts of entry?
    density = sum(1 for v in all_levels if abs(v - entry_price) <= 40)

    # Signed position relative to Pivot Point (direction-neutral: +ve above, -ve below)
    above_P_pts = round(entry_price - P, 1)

    # Structural R:R: how much runway vs floor? (runway > floor = favorable structure)
    struct_rr = round(runway / floor, 2) if (floor > 0 and floor < 999.0 and runway < 999.0) else None

    return {
        'fib_runway':    runway,
        'fib_floor':     floor,
        'fib_density':   density,
        'above_P':       above_P,
        'above_P_pts':   above_P_pts,
        'near_golden':   near_golden,
        'struct_rr':     struct_rr,
    }


def main():
    print("Loading bars 2024-06-01 → 2026-06-17...")
    all_bars = sr.load_bars('MNQ', start='2024-06-01', end='2026-06-17')
    rth_all  = sr.filter_ny_session(all_bars)

    start_dt    = dt.date(2025, 1, 1)
    trade_dates = sorted(d for d in set(rth_all.index.date) if d >= start_dt)
    print(f"Trading days 2025-2026: {len(trade_dates)}")

    records = []
    for i, td in enumerate(trade_dates):
        trades, _, _ = sr.simulate_day(all_bars, td)
        if not trades:
            continue
        prior_rth = get_prior_rth(all_bars, td)
        if prior_rth is None or prior_rth.empty:
            continue

        pdH    = float(prior_rth['high'].max())
        pdL    = float(prior_rth['low'].min())
        pdC    = float(prior_rth['close'].iloc[-1])
        pivots = compute_fib_pivots(pdH, pdL, pdC)

        for t in trades:
            feats = fib_features_deep(float(t['entry']), pivots, t['side'])
            records.append({
                'date': str(td),
                'side': t['side'],
                'pnl':  round(t['pnl'], 2),
                'win':  int(t['pnl'] > 0),
                **feats,
            })
        if (i + 1) % 50 == 0:
            print(f"  ...{i+1}/{len(trade_dates)} days, {len(records)} trades")

    df = pd.DataFrame(records)
    n_total = len(df)
    base_wr = df['win'].mean() * 100
    print(f"\nTotal: {n_total} trades  WR={base_wr:.1f}%  avg=${df['pnl'].mean():+.2f}\n")

    # ── Spearman correlations ───────────────────────────────────────────────
    print("=" * 72)
    print("  DEEP FIBONACCI — Spearman rank correlation with win/loss")
    print("  (vs old fib_dist: best corr was only +0.021)")
    print("=" * 72)

    feat_num  = ['fib_runway', 'fib_floor', 'fib_density', 'above_P_pts', 'struct_rr']
    feat_bool = ['above_P', 'near_golden']
    corr_rows = []
    for f in feat_num + feat_bool:
        sub = df[df[f].notna() & (df[f] != 999.0)].copy()
        if len(sub) < 15:
            continue
        c, p = spearmanr(sub[f].astype(float), sub['win'].astype(float))
        corr_rows.append((abs(c), f, c, p, len(sub)))
    corr_rows.sort(reverse=True)

    print(f"\n  {'Feature':<18} {'Corr':>7}  {'sig':>3}  {'N':>4}  Interpretation")
    print("  " + "-"*68)
    for _, f, c, p, n in corr_rows:
        sig = '***' if p < 0.01 else '** ' if p < 0.05 else '*  ' if p < 0.1 else '   '
        interp_map = {
            'fib_runway':   ('more runway ahead = better WR' if c > 0 else 'less runway = better'),
            'fib_floor':    ('deeper floor below = better'   if c > 0 else 'thinner floor = better'),
            'fib_density':  ('more clusters near = better'   if c > 0 else 'fewer clusters = cleaner'),
            'above_P_pts':  ('further above P = better'      if c > 0 else 'below P = better'),
            'above_P':      ('above P = better WR'           if c > 0 else 'below P = better WR'),
            'near_golden':  ('near .618 = better'            if c > 0 else 'away from .618 = better'),
            'struct_rr':    ('high struct R:R = better'      if c > 0 else 'low struct R:R = better'),
        }
        interp = interp_map.get(f, '')
        print(f"  {f:<18} {c:>+7.3f} {sig}  {n:>4}  {interp}")

    # ── Quartile analysis ───────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("  QUARTILE ANALYSIS — WR by bucket (looking for the real pattern)")
    print("=" * 72)

    for feat in ['fib_runway', 'fib_floor', 'above_P_pts', 'struct_rr', 'fib_density']:
        sub = df[(df[feat].notna()) & (df[feat] != 999.0)].copy()
        if len(sub) < 20:
            continue
        try:
            sub['_q'] = pd.qcut(sub[feat], 4, labels=['Q1','Q2','Q3','Q4'],
                                 duplicates='drop')
        except Exception:
            continue
        print(f"\n  {feat}:")
        for q in ['Q1','Q2','Q3','Q4']:
            qdf = sub[sub['_q'] == q]
            if len(qdf) == 0:
                continue
            wr   = qdf['win'].mean() * 100
            avg  = qdf['pnl'].mean()
            lo   = qdf[feat].min()
            hi   = qdf[feat].max()
            bar  = '█' * int(wr / 5)
            print(f"    {q} [{lo:>7.1f}–{hi:>7.1f}]  N={len(qdf):>3}  "
                  f"WR={wr:>5.1f}%  avg=${avg:>+7.2f}  {bar}")

    # ── above_P by side ─────────────────────────────────────────────────────
    print(f"\n  POSITION vs PIVOT POINT (directional):")
    for side in ['LONG', 'SHORT']:
        sdf = df[df['side'] == side]
        for val, lbl in [(1, 'above P'), (0, 'below P')]:
            vdf = sdf[sdf['above_P'] == val]
            if len(vdf) == 0:
                continue
            wr  = vdf['win'].mean() * 100
            avg = vdf['pnl'].mean()
            print(f"    {side:5}  {lbl}  N={len(vdf):>3}  WR={wr:>5.1f}%  avg=${avg:>+7.2f}")

    # ── The structural matrix ───────────────────────────────────────────────
    print(f"\n  STRUCTURAL MATRIX — runway × floor quadrants")
    print(f"  (runway = distance to next barrier ahead;  floor = structural support behind stop)")
    df2 = df[(df['fib_runway'] != 999.0) & (df['fib_floor'] != 999.0)].copy()
    if len(df2) >= 20:
        med_run = df2['fib_runway'].median()
        med_flo = df2['fib_floor'].median()
        print(f"  Medians: runway={med_run:.1f}pts  floor={med_flo:.1f}pts\n")
        quadrants = [
            (df2['fib_runway'] >  med_run) & (df2['fib_floor'] >  med_flo),
            'Wide runway + deep floor   (open space — ideal)',
            (df2['fib_runway'] >  med_run) & (df2['fib_floor'] <= med_flo),
            'Wide runway + thin floor   (momentum, but exposed stop)',
            (df2['fib_runway'] <= med_run) & (df2['fib_floor'] >  med_flo),
            'Narrow runway + deep floor (squeezed — limited upside)',
            (df2['fib_runway'] <= med_run) & (df2['fib_floor'] <= med_flo),
            'Narrow runway + thin floor (trapped — worst zone)',
        ]
        for j in range(0, len(quadrants), 2):
            mask = quadrants[j]
            lbl  = quadrants[j+1]
            sub  = df2[mask]
            if len(sub) == 0:
                continue
            wr   = sub['win'].mean() * 100
            avg  = sub['pnl'].mean()
            print(f"    {lbl:<48}  N={len(sub):>3}  WR={wr:>5.1f}%  avg=${avg:>+7.2f}")

    # ── Runway threshold sweep: is there a minimum runway needed? ──────────
    print(f"\n  RUNWAY THRESHOLD SWEEP (min runway before entry allowed):")
    print(f"  {'Min runway':>12}  {'N':>4}  {'WR%':>6}  {'Avg':>8}  {'skip%':>6}")
    print("  " + "-"*42)
    for thresh in [0, 10, 20, 30, 40, 50, 75, 100]:
        sub = df[(df['fib_runway'] != 999.0) & (df['fib_runway'] >= thresh)]
        if len(sub) < 10:
            continue
        skip_pct = (1 - len(sub)/n_total)*100
        wr   = sub['win'].mean()*100
        avg  = sub['pnl'].mean()
        base = '' if thresh == 0 else f'  skip {skip_pct:.0f}%'
        print(f"  runway ≥ {thresh:>4}pts  {len(sub):>4}  {wr:>5.1f}%  ${avg:>+7.2f}{base}")

    df.to_csv('futures/fib_deep_2025_2026.csv', index=False)
    print(f"\nSaved → futures/fib_deep_2025_2026.csv")


if __name__ == '__main__':
    main()
