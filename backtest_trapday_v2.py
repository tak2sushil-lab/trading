"""
Trap Day Gate v2 — Surgical Sector Sweep Detector
===================================================
Hypothesis: the dangerous pattern is NOT "any stock gapping big on a pre-event day."
It is specifically SECTOR SWEEP: a coordinated, same-sector move where institutions
pump an entire sector and distribute into retail FOMO.

Individual earnings catalysts → one stock gaps 20%, others in sector flat or +2-3%.
Sector sweep → 10-30 stocks from SAME sector all gap 7-15% simultaneously.

These are completely different. The blunt Gate A (pre-event + gap>7%) couldn't
distinguish them — that's why it blocked profitable earnings-driven entries on May 6.

Surgical gate: pre-event day + ≥N stocks from SAME sector gapping >7% → block longs
IN THAT SECTOR ONLY. Stocks in other sectors, or earnings catalysts, unaffected.

Tests sweep thresholds: 3, 5, 8, 10 stocks required from same sector.

Jun 9 2026 test: should be caught (30+ SEMIS stocks all gapping 10-15%)
May 6 2026 test: should NOT be caught (earnings season, individual catalysts across sectors)
"""

import sys, warnings
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date, timedelta
from collections import defaultdict

warnings.filterwarnings('ignore')
sys.path.insert(0, '/Users/sushil/trading')

from backtest_strategy import (
    build_spy_regime, backtest_symbol,
    START_DATE, END_DATE, CAPITAL_PER_TRADE
)
try:
    from auto_trader import SECTOR_MAP as _SM
    SYMBOLS = sorted(_SM.keys())
except Exception:
    from backtest_strategy import SYMBOLS
    _SM = {}
from futures.macro_calendar import classify_date

GAP_THRESH = 7.0  # stocks must gap at least this much to count in sweep
SWEEP_THRESHOLDS = [3, 5, 8, 10]  # min same-sector stocks to declare a sweep


def build_pre_event_set(start='2020-01-01', end=None):
    if end is None:
        end = date.today().isoformat()
    pre_event = set()
    dt = date.fromisoformat(start)
    end_dt = date.fromisoformat(end)
    while dt <= end_dt:
        if dt.weekday() < 5:
            next_bday = dt + timedelta(days=1)
            while next_bday.weekday() >= 5:
                next_bday += timedelta(days=1)
            if classify_date(next_bday) == 'HIGH_IMPACT':
                pre_event.add(dt.isoformat())
        dt += timedelta(days=1)
    return pre_event


def build_sector_sweep_map(daily_gaps, sector_map, symbols, pre_event_dates, sweep_thresh):
    """
    For each pre-event day: count stocks per sector gapping > GAP_THRESH.
    Returns dict: {date_str: {'sector': str, 'count': int, 'syms': list}}
    Only includes days where the max-sector count >= sweep_thresh.
    """
    sweep_days = {}
    for date_str in pre_event_dates:
        sector_counts = defaultdict(list)
        for sym in symbols:
            if sym not in daily_gaps:
                continue
            gap = daily_gaps[sym].get(date_str, 0)
            if gap >= GAP_THRESH:
                sector = sector_map.get(sym, 'OTHER')
                if sector != 'OTHER':
                    sector_counts[sector].append((sym, gap))

        if not sector_counts:
            continue
        # Find the sector with the most stocks gapping big
        top_sector = max(sector_counts, key=lambda s: len(sector_counts[s]))
        top_count = len(sector_counts[top_sector])
        if top_count >= sweep_thresh:
            sweep_days[date_str] = {
                'sector': top_sector,
                'count': top_count,
                'syms': sector_counts[top_sector],
                'all_sectors': {s: len(v) for s, v in sector_counts.items()}
            }
    return sweep_days


def compute_stats(trades_df, label):
    if trades_df.empty:
        return {'label': label, 'n': 0, 'wr': 0.0, 'total_pnl': 0, 'avg_pnl': 0,
                'avg_win': 0, 'avg_loss': 0, 'sharpe': 0, 'max_dd': 0,
                'n_years': 1, 'ann_pnl': 0, 'wins': 0, 'losses': 0}
    n = len(trades_df)
    wins = trades_df[trades_df['pnl_usd'] > 0]
    losses = trades_df[trades_df['pnl_usd'] <= 0]
    total_pnl = trades_df['pnl_usd'].sum()
    wr = len(wins) / n * 100
    avg_win = wins['pnl_usd'].mean() if len(wins) else 0
    avg_loss = losses['pnl_usd'].mean() if len(losses) else 0
    daily = trades_df.groupby('date')['pnl_usd'].sum()
    sharpe = (daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0
    eq = CAPITAL_PER_TRADE + trades_df.sort_values('date')['pnl_usd'].cumsum()
    pk = eq.cummax()
    max_dd = ((eq - pk) / pk * 100).min()
    n_years = max(1, (pd.to_datetime(trades_df['date'].max()) -
                      pd.to_datetime(trades_df['date'].min())).days / 365)
    return {
        'label': label, 'n': n, 'wr': wr, 'total_pnl': total_pnl,
        'avg_pnl': total_pnl / n, 'avg_win': avg_win, 'avg_loss': avg_loss,
        'sharpe': sharpe, 'max_dd': max_dd,
        'n_years': n_years, 'ann_pnl': total_pnl / n_years,
        'wins': len(wins), 'losses': len(losses),
    }


def print_stats(s):
    print(f"\n  [{s['label']}]")
    print(f"    Trades : {s['n']}  ({s.get('wins',0)}W / {s.get('losses',0)}L)")
    print(f"    WR     : {s['wr']:.1f}%")
    print(f"    PnL    : ${s['total_pnl']:+,.0f}  (${s['ann_pnl']:+,.0f}/yr)")
    print(f"    AvgW   : ${s['avg_win']:+.2f}  AvgL: ${s['avg_loss']:+.2f}")
    print(f"    Sharpe : {s['sharpe']:.2f}   MaxDD: {s['max_dd']:.1f}%")


def by_year_table(baseline_df, gated_df):
    years = sorted(baseline_df['year'].unique())
    print(f"  {'Year':<6} {'Base_N':>7} {'Base_WR':>8} {'Base_PnL':>10}  "
          f"{'Gate_N':>7} {'Gate_WR':>8} {'Gate_PnL':>10}  {'Δ_PnL':>9}  {'Result'}")
    print(f"  {'-'*82}")
    for yr in years:
        b = baseline_df[baseline_df['year'] == yr]
        g = gated_df[gated_df['year'] == yr]
        bwr = len(b[b['pnl_usd'] > 0]) / len(b) * 100 if len(b) else 0
        gwr = len(g[g['pnl_usd'] > 0]) / len(g) * 100 if len(g) else 0
        bp = b['pnl_usd'].sum()
        gp = g['pnl_usd'].sum() if len(g) else 0
        delta = gp - bp
        result = "✓ BETTER" if delta > 20 else ("= same" if abs(delta) <= 20 else "✗ worse")
        print(f"  {yr:<6} {len(b):>7} {bwr:>7.1f}% {bp:>+10,.0f}  "
              f"{len(g):>7} {gwr:>7.1f}% {gp:>+10,.0f}  {delta:>+9,.0f}  {result}")
    print(f"  {'-'*82}")
    btot = baseline_df['pnl_usd'].sum()
    gtot = gated_df['pnl_usd'].sum() if len(gated_df) else 0
    bwr_t = len(baseline_df[baseline_df['pnl_usd'] > 0]) / len(baseline_df) * 100
    gwr_t = len(gated_df[gated_df['pnl_usd'] > 0]) / len(gated_df) * 100 if len(gated_df) else 0
    print(f"  {'TOTAL':<6} {len(baseline_df):>7} {bwr_t:>7.1f}% {btot:>+10,.0f}  "
          f"{len(gated_df):>7} {gwr_t:>7.1f}% {gtot:>+10,.0f}  {gtot-btot:>+9,.0f}")


def show_blocked(blocked_df):
    if blocked_df.empty:
        print("  None blocked.")
        return
    print(f"  {'Date':<12} {'Sym':<6} {'Sector':<20} {'Gap%':>6} {'PnL':>9}  {'Regime'}")
    print(f"  {'-'*70}")
    for _, r in blocked_df.sort_values('date').iterrows():
        print(f"  {r['date']:<12} {r['symbol']:<6} {r.get('sector','?'):<20} "
              f"{r.get('gap_pct', 0):>+5.1f}%  {r['pnl_usd']:>+9.2f}  {r.get('regime','?')}")
    wr = len(blocked_df[blocked_df['pnl_usd'] > 0]) / len(blocked_df) * 100
    print(f"\n  Blocked: {len(blocked_df)} trades | WR {wr:.1f}% | Total PnL ${blocked_df['pnl_usd'].sum():+,.0f}")


def main():
    print(f"\n{'='*72}")
    print("  TRAP DAY GATE v2 — Surgical Sector Sweep Detector")
    print(f"  Period: {START_DATE} → {END_DATE}  |  {len(SYMBOLS)} symbols")
    print(f"  Gate threshold: >={GAP_THRESH:.0f}% gap + same-sector concentration")
    print(f"{'='*72}")

    pre_event_dates = build_pre_event_set(START_DATE, END_DATE)
    print(f"\n  Pre-HIGH_IMPACT event days in period: {len(pre_event_dates)}")

    print("\n  Loading SPY regime data...")
    spy_regime = build_spy_regime(START_DATE, END_DATE)

    print("\n  Downloading price data for all symbols...")
    symbol_data = {}
    for sym in SYMBOLS:
        try:
            df = yf.download(sym, start=START_DATE, end=END_DATE,
                             progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if len(df) >= 60:
                symbol_data[sym] = df
        except Exception:
            pass
    print(f"  Loaded {len(symbol_data)} symbols")

    # Build daily gap% and prev_close for every symbol
    daily_gaps = {}
    symbol_prev_close = {}
    for sym, df in symbol_data.items():
        gaps = {}
        prev_c = {}
        for i in range(1, len(df)):
            ds = df.index[i].strftime('%Y-%m-%d')
            pc = float(df['Close'].iloc[i - 1])
            op = float(df['Open'].iloc[i])
            if pc > 0:
                gaps[ds] = (op - pc) / pc * 100
                prev_c[ds] = pc
        daily_gaps[sym] = gaps
        symbol_prev_close[sym] = prev_c

    # ── Show sector sweep events at each threshold level ──────────────────
    print(f"\n{'='*72}")
    print("  SECTOR SWEEP EVENTS DETECTED (by threshold)")
    print(f"  Sector sweep = ≥N stocks from same sector all gapping >{GAP_THRESH:.0f}%")
    print(f"{'='*72}")

    print(f"\n  {'Threshold':>12}  {'Sweep Days':>12}  {'Affected Pre-Event Days':>25}")
    print(f"  {'-'*55}")
    all_sweep_maps = {}
    for thresh in SWEEP_THRESHOLDS:
        sm = build_sector_sweep_map(daily_gaps, _SM, SYMBOLS, pre_event_dates, thresh)
        all_sweep_maps[thresh] = sm
        print(f"  {'≥'+str(thresh)+' stocks':>12}  {len(sm):>12}  ", end='')
        sorted_dates = sorted(sm.keys())
        print(', '.join(d[5:] for d in sorted_dates[:6]) + ('...' if len(sorted_dates) > 6 else ''))

    # Show detailed sweep events for threshold=8 (our candidate sweet spot)
    print(f"\n  Detailed sweep events (threshold ≥5 — candidate sweet spot):")
    sm5 = all_sweep_maps[5]
    if sm5:
        print(f"  {'Date':<12} {'Sector':<22} {'Count':>6}  Top stocks (with gap%)")
        print(f"  {'-'*72}")
        for ds in sorted(sm5.keys()):
            ev = sm5[ds]
            top5 = sorted(ev['syms'], key=lambda x: -x[1])[:5]
            top5_str = ', '.join(f"{s}+{g:.0f}%" for s, g in top5)
            print(f"  {ds:<12} {ev['sector']:<22} {ev['count']:>6}  {top5_str}")
    else:
        print("  (no events at this threshold)")

    print(f"\n  Detailed sweep events (threshold ≥8 — tighter):")
    sm8 = all_sweep_maps[8]
    if sm8:
        print(f"  {'Date':<12} {'Sector':<22} {'Count':>6}  Top stocks (with gap%)")
        print(f"  {'-'*72}")
        for ds in sorted(sm8.keys()):
            ev = sm8[ds]
            top5 = sorted(ev['syms'], key=lambda x: -x[1])[:5]
            top5_str = ', '.join(f"{s}+{g:.0f}%" for s, g in top5)
            print(f"  {ds:<12} {ev['sector']:<22} {ev['count']:>6}  {top5_str}")
    else:
        print("  (no events at this threshold)")

    # ── Run backtest for all symbols ──────────────────────────────────────
    print("\n  Running backtest across all symbols...")
    all_trades = []
    for sym in SYMBOLS:
        if sym not in symbol_data:
            continue
        print(f"    {sym}...", end=' ', flush=True)
        try:
            prev_c = symbol_prev_close[sym]
            trades_df = backtest_symbol(sym, spy_regime)
            if not trades_df.empty:
                trades_df['gap_pct'] = trades_df.apply(
                    lambda r: (r['entry'] - prev_c.get(r['date'], r['entry'])) /
                              prev_c.get(r['date'], r['entry']) * 100
                    if prev_c.get(r['date'], 0) > 0 else 0, axis=1
                )
                trades_df['sector'] = trades_df['symbol'].map(
                    lambda s: _SM.get(s, 'OTHER'))
                trades_df['year'] = pd.to_datetime(trades_df['date']).dt.year
                all_trades.append(trades_df)
                print(f"{len(trades_df)}t")
            else:
                print("0t")
        except Exception as e:
            print(f"err: {e}")

    if not all_trades:
        print("No trades found.")
        return

    baseline = pd.concat(all_trades, ignore_index=True)
    baseline['is_pre_event'] = baseline['date'].isin(pre_event_dates)
    print(f"\n  Total baseline trades: {len(baseline)}")
    print(f"  Pre-event day trades:  {baseline['is_pre_event'].sum()}")

    # ── Test each sweep threshold ─────────────────────────────────────────
    print(f"\n\n{'='*72}")
    print("  RESULTS BY SWEEP THRESHOLD")
    print(f"{'='*72}")

    print(f"\n  Summary: which thresholds help?")
    print(f"  {'Threshold':>12}  {'Blocked':>8}  {'Block WR':>9}  {'Block PnL':>11}  {'Gate PnL Δ':>12}  {'Result'}")
    print(f"  {'-'*72}")

    threshold_results = {}
    for thresh in SWEEP_THRESHOLDS:
        sweep_map = all_sweep_maps[thresh]
        # A trade is blocked if: it's a pre-event day AND its sector is the sweep sector that day
        def is_blocked(row, sm=sweep_map):
            if not row['is_pre_event']:
                return False
            ev = sm.get(row['date'])
            if ev is None:
                return False
            return row['sector'] == ev['sector']

        blocked_mask = baseline.apply(is_blocked, axis=1)
        gated = baseline[~blocked_mask].copy()
        blocked = baseline[blocked_mask].copy()

        block_wr = len(blocked[blocked['pnl_usd'] > 0]) / len(blocked) * 100 if len(blocked) else 0
        block_pnl = blocked['pnl_usd'].sum() if len(blocked) else 0
        gate_delta = gated['pnl_usd'].sum() - baseline['pnl_usd'].sum()

        threshold_results[thresh] = {'gated': gated, 'blocked': blocked}

        # Verdict
        if gate_delta > 100 and block_wr < 55:
            verdict = "✅ VIABLE"
        elif gate_delta > 0:
            verdict = "⚠ marginal"
        elif abs(gate_delta) < 200:
            verdict = "≈ neutral"
        else:
            verdict = "❌ hurts"

        print(f"  {'≥'+str(thresh)+' stocks':>12}  {len(blocked):>8}  {block_wr:>8.1f}%  "
              f"{block_pnl:>+10,.0f}  {gate_delta:>+11,.0f}  {verdict}")

    # ── Detailed breakdown for each threshold ─────────────────────────────
    for thresh in SWEEP_THRESHOLDS:
        res = threshold_results[thresh]
        gated = res['gated']
        blocked = res['blocked']

        print(f"\n{'='*72}")
        print(f"  GATE: Sector sweep ≥{thresh} stocks gapping >{GAP_THRESH:.0f}% (pre-event day)")
        print(f"{'='*72}")

        print_stats(compute_stats(baseline, "BASELINE"))
        print_stats(compute_stats(gated, f"Gate (sweep≥{thresh})"))

        print(f"\n  By Year:")
        by_year_table(baseline, gated)

        print(f"\n  Blocked trades ({len(blocked)}):")
        show_blocked(blocked)

    # ── The crucial test: May 6 vs Jun 9 ─────────────────────────────────
    print(f"\n{'='*72}")
    print("  CRUCIAL TEST: May 6 2026 (earnings) vs Jun 9 2026 (sector sweep)")
    print(f"{'='*72}")
    may6  = '2026-05-06'
    jun9  = '2026-06-09'

    print(f"\n  May 6 2026 (pre-FOMC, individual earnings catalysts):")
    for thresh in SWEEP_THRESHOLDS:
        sm = all_sweep_maps[thresh]
        ev = sm.get(may6)
        if ev:
            print(f"    ≥{thresh}: CAUGHT — {ev['sector']} sweep ({ev['count']} stocks). ❌ Would block May 6 entries")
        else:
            print(f"    ≥{thresh}: NOT caught — no sector sweep detected. ✓ May 6 entries allowed")

    print(f"\n  Jun 9 2026 (pre-CPI, institutional SEMIS distribution):")
    for thresh in SWEEP_THRESHOLDS:
        sm = all_sweep_maps[thresh]
        ev = sm.get(jun9)
        if ev:
            top5 = sorted(ev['syms'], key=lambda x: -x[1])[:4]
            top5_str = ', '.join(f"{s}+{g:.0f}%" for s, g in top5)
            print(f"    ≥{thresh}: CAUGHT — {ev['sector']} sweep ({ev['count']} stocks: {top5_str}...). ✓ Jun 9 blocked")
        else:
            print(f"    ≥{thresh}: NOT caught — sweep not detected at this threshold. ❌ Jun 9 would NOT be blocked")

    # ── Pre-event day WR by gap size with sector sweep context ────────────
    print(f"\n{'='*72}")
    print("  PRE-EVENT TRADES: WR by gap size (full 357 trades)")
    print(f"  Context: gap>7% WR collapses. But many of these are in NON-swept sectors.")
    print(f"{'='*72}")
    pre_trades = baseline[baseline['is_pre_event']].copy()
    pre_trades['is_sweep'] = pre_trades.apply(
        lambda r: all_sweep_maps[5].get(r['date'], {}).get('sector') == r['sector']
        if r['date'] in all_sweep_maps[5] else False, axis=1)

    bins   = [(-999, -5), (-5, 0), (0, 3), (3, 5), (5, 7), (7, 10), (10, 999)]
    labels = ['gap<-5%', 'gap-5→0', 'gap0→3%', 'gap3→5%', 'gap5→7%', 'gap7→10%', 'gap>10%']
    print(f"\n  {'Gap range':<12}  {'All pre-evt':>5}  {'WR':>6}  {'Avg PnL':>8}  "
          f"{'In sweep':>9}  {'WR':>6}  {'Not sweep':>10}  {'WR':>6}")
    print(f"  {'-'*75}")
    for (lo, hi), lbl in zip(bins, labels):
        sub = pre_trades[(pre_trades['gap_pct'] >= lo) & (pre_trades['gap_pct'] < hi)]
        if len(sub) == 0:
            continue
        swr   = len(sub[sub['pnl_usd'] > 0]) / len(sub) * 100
        savg  = sub['pnl_usd'].mean()
        sw    = sub[sub['is_sweep']]
        nsw   = sub[~sub['is_sweep']]
        sw_wr  = len(sw[sw['pnl_usd'] > 0]) / len(sw) * 100 if len(sw) else float('nan')
        nsw_wr = len(nsw[nsw['pnl_usd'] > 0]) / len(nsw) * 100 if len(nsw) else float('nan')
        sw_str  = f"{len(sw):>4}  {sw_wr:>5.1f}%" if len(sw) > 0 else "    -       -"
        nsw_str = f"{len(nsw):>5}  {nsw_wr:>5.1f}%" if len(nsw) > 0 else "     -       -"
        print(f"  {lbl:<12}  {len(sub):>5}  {swr:>5.1f}%  {savg:>+7.2f}  "
              f"  {sw_str}  {nsw_str}")

    print(f"\n{'='*72}")
    print("  CONCLUSION")
    print(f"{'='*72}")
    best_thresh = None
    best_delta = -9999
    for thresh in SWEEP_THRESHOLDS:
        res = threshold_results[thresh]
        blocked = res['blocked']
        gated = res['gated']
        gate_delta = gated['pnl_usd'].sum() - baseline['pnl_usd'].sum()
        block_wr = len(blocked[blocked['pnl_usd'] > 0]) / len(blocked) * 100 if len(blocked) else 0
        # Check May 6 / Jun 9 behavior
        may6_caught = all_sweep_maps[thresh].get(may6) is not None
        jun9_caught = all_sweep_maps[thresh].get(jun9) is not None
        print(f"\n  ≥{thresh} stocks: Δ=${gate_delta:+,.0f} | blocked WR={block_wr:.1f}% | "
              f"May6={'CAUGHT❌' if may6_caught else 'free✓'} | "
              f"Jun9={'CAUGHT✓' if jun9_caught else 'missed❌'}")
        if not may6_caught and jun9_caught and gate_delta > best_delta:
            best_delta = gate_delta
            best_thresh = thresh

    if best_thresh:
        print(f"\n  ✅ SWEET SPOT: ≥{best_thresh} stocks from same sector gapping >{GAP_THRESH:.0f}% on pre-event day")
        print(f"     — catches Jun 9 style sweeps, leaves May 6 earnings gaps alone")
        print(f"     — PnL delta: ${best_delta:+,.0f} over 6 years (${best_delta/6.4:+,.0f}/yr)")
    else:
        print("\n  No single threshold perfectly separates May 6 (good) from Jun 9 (bad).")
        print("  Consider: lowest threshold that catches Jun 9 without hurting May 6 entries.")


if __name__ == '__main__':
    main()
