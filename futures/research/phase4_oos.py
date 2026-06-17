#!/usr/bin/env python3
"""
futures/phase4_oos.py — Phase 4: OOS validation on 2023 + 2024

Tests 6 Avengers heroes + OVN_POS champion on true out-of-sample windows.

Market 'weather' context — each year has a different climate:
  2023: Strong recovery (QQQ +54%) — momentum-driven trending year
  2024: Continued rally (QQQ +26%) — election year, mid-year chop, Q4 surge

Hero thresholds are FIXED from 2025-2026 IS. Not retuned here.
If a hero survives both years despite different regimes, it is structural.
If it only works in one year, it is regime-specific (note but don't discard yet).
"""
import sys, os, datetime as dt
import numpy as np, pandas as pd

sys.path.insert(0, '.')
import futures.sim_replay as sr
from futures.feature_study import compute_features, get_prior_rth, compute_fib_pivots
from futures.fib_deep import fib_features_deep

# ── Hero thresholds — FIXED from 2025-2026 IS (do NOT retune) ────────────────
ATR_DEATH_LO  = 38.0    # medium-vol death zone — below is safe, above is also safe
ATR_DEATH_HI  = 55.0    # skip trades inside this range
RSI_DIR_FLOOR = 50.0    # directional RSI floor (rsi_1h for LONG, 100-rsi for SHORT)
POC_SIGNED_FL = 200.0   # signed POC breakout min (pts)
FIB_FLOOR_MIN = 446.0   # deep structural support (Q4 threshold from quartile)
GOLDEN_DIST   = 25.0    # within 25pts of .618 level

# ── OVN_POS champion (derived from grid — no retune) ─────────────────────────
CHAMP_TREND_HI = 0.70   # was 0.85 — expanded long zone
CHAMP_TREND_LO = 0.0    # was 0.20 — no short filter
CHAMP_SKIP_LO  = 0.0    # no skip zone
CHAMP_SKIP_HI  = 0.0    # no skip zone


def generate_year_data(year, bar_start):
    """Single-pass: simulate + all features + fib in one loop. Cached."""
    csv_path = f'futures/avengers_{year}.csv'
    if os.path.exists(csv_path):
        print(f'  Loaded cached {csv_path}')
        return pd.read_csv(csv_path)

    end_str = f'{year}-12-31'
    print(f'  Generating {year} features (bars {bar_start} → {end_str})...')
    all_bars = sr.load_bars('MNQ', start=bar_start, end=end_str)
    if all_bars is None or all_bars.empty:
        print(f'  ERROR: no bars for {year}'); return None

    rth_all     = sr.filter_ny_session(all_bars)
    trade_dates = sorted(d for d in set(rth_all.index.date)
                         if d >= dt.date(year, 1, 1))
    print(f'  {year}: {len(trade_dates)} trading days')

    records = []
    for i, td in enumerate(trade_dates):
        trades, _, _ = sr.simulate_day(all_bars, td)
        if not trades:
            continue
        prior_rth = get_prior_rth(all_bars, td)
        today_rth = sr.filter_ny_session(all_bars[all_bars.index.date == td])

        pivots = None
        if prior_rth is not None and not prior_rth.empty:
            pdH = float(prior_rth['high'].max())
            pdL = float(prior_rth['low'].min())
            pdC = float(prior_rth['close'].iloc[-1])
            pivots = compute_fib_pivots(pdH, pdL, pdC)

        for t in trades:
            feats     = compute_features(all_bars, td, t, prior_rth, today_rth)
            fib_feats = fib_features_deep(float(t['entry']), pivots, t['side']) if pivots else {}
            records.append({
                'date':      str(td),
                'side':      t['side'],
                'pnl':       round(t['pnl'], 2),
                'win':       int(t['pnl'] > 0),
                **feats,
                **fib_feats,
            })
        if (i + 1) % 30 == 0:
            print(f'    ...{i+1}/{len(trade_dates)} days, {len(records)} trades')

    df = pd.DataFrame(records)
    df.to_csv(csv_path, index=False)
    print(f'  Saved {len(df)} trades → {csv_path}')
    return df


def add_heroes(df):
    """Compute all 6 hero flags. Thresholds fixed from IS 2025-2026."""
    df = df.copy()
    is_long = df['side'] == 'LONG'

    # Direction-aware transforms
    df['rsi_dir']    = np.where(is_long, df['rsi_1h'],   100.0 - df['rsi_1h'])
    df['signed_poc'] = np.where(is_long, df['poc_dist'], -df['poc_dist'])
    df['mtf1h_ok']   = np.where(is_long, df['mtf_1h_bull'], 1 - df['mtf_1h_bull'])

    # Hero flags (1 = hero says YES, 0 = hero says NO)
    df['H1_ATR_SAFE']     = ((df['atr'] < ATR_DEATH_LO) | (df['atr'] > ATR_DEATH_HI)).astype(int)
    df['H2_MTF_ALIGNED']  = (df['mtf1h_ok'].fillna(0) >= 1).astype(int)
    df['H3_RSI_MOMENTUM'] = (df['rsi_dir'].fillna(0) >= RSI_DIR_FLOOR).astype(int)
    df['H4_POC_BREAKOUT'] = (df['signed_poc'].fillna(0) >= POC_SIGNED_FL).astype(int)
    df['H5_FIB_FLOOR']    = (df['fib_floor'].fillna(0) >= FIB_FLOOR_MIN).astype(int)
    df['H6_GOLDEN_SCOUT'] = (df['near_golden'].fillna(0) >= 1).astype(int)

    heroes = ['H1_ATR_SAFE','H2_MTF_ALIGNED','H3_RSI_MOMENTUM',
              'H4_POC_BREAKOUT','H5_FIB_FLOOR','H6_GOLDEN_SCOUT']
    df['score'] = df[heroes].sum(axis=1)
    return df, heroes


def run_ovnpos_champion(all_bars, trade_dates):
    """Run simulate_day with champion ovn_pos thresholds. Returns P&L list."""
    orig = (sr._OVN_TREND_LO, sr._OVN_TREND_HI, sr._OVN_SKIP_LO, sr._OVN_SKIP_HI)
    sr._OVN_TREND_LO = CHAMP_TREND_LO
    sr._OVN_TREND_HI = CHAMP_TREND_HI
    sr._OVN_SKIP_LO  = CHAMP_SKIP_LO
    sr._OVN_SKIP_HI  = CHAMP_SKIP_HI

    trades_all = []
    for td in trade_dates:
        trades, _, _ = sr.simulate_day(all_bars, td)
        trades_all.extend(trades)

    sr._OVN_TREND_LO, sr._OVN_TREND_HI = orig[0], orig[1]
    sr._OVN_SKIP_LO,  sr._OVN_SKIP_HI  = orig[2], orig[3]
    return trades_all


def report_year(label, df, heroes):
    n_total  = len(df)
    base_wr  = df['win'].mean() * 100
    base_avg = df['pnl'].mean()
    base_pnl = df['pnl'].sum()

    print(f'\n{"="*78}')
    print(f'  {label}')
    print(f'{"="*78}')
    print(f'  Baseline: N={n_total}  WR={base_wr:.1f}%  avg=${base_avg:+.2f}  total=${base_pnl:+,.0f}')

    print(f'\n  INDIVIDUAL HEROES (each tested alone on this year):')
    print(f'  {"Hero":<18} {"N":>4}  {"WR%":>6}  {"Avg":>8}  {"ΔWR":>6}  {"skip%":>6}')
    print(f'  ' + '-'*56)
    for h in heroes:
        sub = df[df[h] == 1]
        if len(sub) < 5:
            print(f'  {h:<18} N<5 — skip'); continue
        wr   = sub['win'].mean() * 100
        avg  = sub['pnl'].mean()
        skip = (1 - len(sub)/n_total) * 100
        delta = wr - base_wr
        flag = '◄ HOLDS' if delta >= 1.0 else ('○ weak' if delta >= 0 else '✗ fails')
        print(f'  {h:<18} {len(sub):>4}  {wr:>5.1f}%  ${avg:>+7.2f}  {delta:>+5.1f}pp  {skip:>5.0f}%  {flag}')

    print(f'\n  COMPOSITE SCORE — does the team still score?')
    print(f'  {"Score":<8} {"N":>4}  {"WR%":>6}  {"Avg":>8}  {"ΔWR":>6}')
    print(f'  ' + '-'*42)
    for s in range(7):
        sub = df[df['score'] == s]
        if len(sub) < 3:
            continue
        wr  = sub['win'].mean() * 100
        avg = sub['pnl'].mean()
        tag = ' ← GOLD' if s >= 5 else (' ← SILVER' if s == 4 else '')
        print(f'  {s}/6      {len(sub):>4}  {wr:>5.1f}%  ${avg:>+7.2f}  {wr-base_wr:>+5.1f}pp{tag}')

    print(f'\n  CUMULATIVE GATE (score ≥ X):')
    for thresh in [3, 4, 5]:
        sub = df[df['score'] >= thresh]
        if len(sub) < 5:
            continue
        wr   = sub['win'].mean() * 100
        avg  = sub['pnl'].mean()
        pnl  = sub['pnl'].sum()
        skip = (1 - len(sub)/n_total)*100
        delta = wr - base_wr
        flag = 'HOLDS ✓' if delta >= 2.0 else 'WEAK ○' if delta >= 0 else 'FAILS ✗'
        print(f'  score≥{thresh}: N={len(sub):>3}  WR={wr:.1f}%  avg=${avg:+.2f}  '
              f'total=${pnl:+,.0f}  skip={skip:.0f}%  Δ={delta:+.1f}pp  {flag}')


def main():
    # ── Years to validate ─────────────────────────────────────────────────────
    years = [
        (2023, '2022-06-01', 'OOS WINDOW 1 — 2023 (QQQ +54%, momentum/trending year)'),
        (2024, '2023-06-01', 'OOS WINDOW 2 — 2024 (QQQ +26%, election year, mid-year chop)'),
    ]

    print('Phase 4 OOS Validation — Heroes derived from 2025-2026 IS')
    print('Thresholds fixed: ATR[38-55], RSI≥50, MTF-1H, POC≥200, FibFloor≥446, Golden±25')
    print()

    for year, bar_start, label in years:
        df = generate_year_data(year, bar_start)
        if df is None or df.empty:
            print(f'  No data for {year}, skipping'); continue

        df, heroes = add_heroes(df)
        report_year(label, df, heroes)

        # ── OVN_POS champion vs baseline for this year ────────────────────────
        print(f'\n  OVN_POS CHAMPION TEST ({year}):')
        all_bars = sr.load_bars('MNQ', start=bar_start, end=f'{year}-12-31')
        rth_all  = sr.filter_ny_session(all_bars)
        trade_dates = sorted(d for d in set(rth_all.index.date)
                             if d >= dt.date(year, 1, 1))

        champ_trades = run_ovnpos_champion(all_bars, trade_dates)
        if champ_trades:
            c_pnl = [t['pnl'] for t in champ_trades]
            c_wr  = np.mean([1 if p > 0 else 0 for p in c_pnl]) * 100
            c_avg = np.mean(c_pnl)
            base_pnl = df['pnl'].sum()
            flag = 'HOLDS ✓' if c_wr >= df['win'].mean()*100 else 'DEGRADES ✗'
            print(f'  Baseline: N={len(df):>3}  WR={df["win"].mean()*100:.1f}%  '
                  f'P&L=${base_pnl:+,.0f}')
            print(f'  Champion: N={len(c_pnl):>3}  WR={c_wr:.1f}%  P&L=${sum(c_pnl):+,.0f}  '
                  f'avg=${c_avg:+.2f}  {flag}')

    print('\n\nPhase 4 complete. Review: did each hero HOLD, WEAKEN, or FAIL across both years?')
    print('  HOLD (≥+1pp both years) = structural edge, implement')
    print('  HOLD one year + WEAK other = regime-conditional, note in CLAUDE.md')
    print('  FAIL both years = curve-fit from IS, drop from Avengers roster')


if __name__ == '__main__':
    main()
