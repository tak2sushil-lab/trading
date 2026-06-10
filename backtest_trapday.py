"""
Trap Day Gate Validation
========================
Tests the hypothesis: on "pre-HIGH_IMPACT-macro days" with a wide sector gap sweep,
blocking LONG entries would reduce losses without meaningfully hurting overall WR/PnL.

Three gate variants tested:
  A) Pre-event day + stock gapped >7% at open → skip LONG
  B) Pre-event day + stock gapped >5% at open → skip LONG
  C) Pre-event day only (any gap) → skip LONG  [too aggressive, control]

Runs the existing backtest logic, post-processes trades, compares:
  - Baseline (no gate)
  - Gate A / B / C applied
  - Shows by-year and by-month breakdown
"""

import sys, warnings
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date, timedelta
from collections import defaultdict

warnings.filterwarnings('ignore')
sys.path.insert(0, '/Users/sushil/trading')

# ── Reuse existing backtest machinery ────────────────────────────────────
from backtest_strategy import (
    build_spy_regime, backtest_symbol,
    START_DATE, END_DATE, CAPITAL_PER_TRADE
)
# Import full universe directly from auto_trader, not the arg-parsed SYMBOLS list
try:
    from auto_trader import SECTOR_MAP as _SM
    SYMBOLS = sorted(_SM.keys())
except Exception:
    from backtest_strategy import SYMBOLS
from futures.macro_calendar import classify_date

# ── Build macro event lookup: date_str → next_bday classification ────────
def build_pre_event_set(start='2020-01-01', end=None):
    """Returns set of date strings that are the day BEFORE a HIGH_IMPACT event."""
    if end is None:
        end = date.today().isoformat()
    pre_event = set()
    dt = date.fromisoformat(start)
    end_dt = date.fromisoformat(end)
    while dt <= end_dt:
        if dt.weekday() < 5:  # weekday
            next_bday = dt + timedelta(days=1)
            while next_bday.weekday() >= 5:
                next_bday += timedelta(days=1)
            if classify_date(next_bday) == 'HIGH_IMPACT':
                pre_event.add(dt.isoformat())
        dt += timedelta(days=1)
    return pre_event


def compute_stats(trades_df, label):
    if trades_df.empty:
        return {'label': label, 'n': 0, 'wr': 0, 'total_pnl': 0, 'avg_pnl': 0,
                'avg_win': 0, 'avg_loss': 0, 'sharpe': 0, 'max_dd': 0}

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


def by_year_table(baseline_df, gated_df, gate_label):
    years = sorted(baseline_df['year'].unique())
    print(f"\n  {'Year':<6} {'Base_N':>7} {'Base_WR':>8} {'Base_PnL':>10}  "
          f"{'Gate_N':>7} {'Gate_WR':>8} {'Gate_PnL':>10}  {'Δ_PnL':>9}  {'Saved?'}")
    print(f"  {'-'*80}")
    for yr in years:
        b = baseline_df[baseline_df['year'] == yr]
        g = gated_df[gated_df['year'] == yr]
        bwr = len(b[b['pnl_usd'] > 0]) / len(b) * 100 if len(b) else 0
        gwr = len(g[g['pnl_usd'] > 0]) / len(g) * 100 if len(g) else 0
        bp = b['pnl_usd'].sum()
        gp = g['pnl_usd'].sum()
        delta = gp - bp
        saved = "✓ BETTER" if delta > 0 else ("= same" if abs(delta) < 10 else "✗ worse")
        print(f"  {yr:<6} {len(b):>7} {bwr:>7.1f}% {bp:>+10,.0f}  "
              f"{len(g):>7} {gwr:>7.1f}% {gp:>+10,.0f}  {delta:>+9,.0f}  {saved}")
    print(f"  {'-'*80}")
    btot = baseline_df['pnl_usd'].sum()
    gtot = gated_df['pnl_usd'].sum()
    bwr_tot = len(baseline_df[baseline_df['pnl_usd']>0]) / len(baseline_df) * 100
    gwr_tot = len(gated_df[gated_df['pnl_usd']>0]) / len(gated_df) * 100 if len(gated_df) else 0
    print(f"  {'TOTAL':<6} {len(baseline_df):>7} {bwr_tot:>7.1f}% {btot:>+10,.0f}  "
          f"{len(gated_df):>7} {gwr_tot:>7.1f}% {gtot:>+10,.0f}  {gtot-btot:>+9,.0f}")


def show_trapped_trades(baseline_df, gated_df):
    """Show which trades were blocked by the gate and their P&L."""
    blocked = baseline_df[~baseline_df.index.isin(gated_df.index)].copy()
    if blocked.empty:
        print("  No trades blocked.")
        return
    print(f"\n  Blocked trades ({len(blocked)}):")
    print(f"  {'Date':<12} {'Sym':<6} {'Grade':<5} {'Gap%':>6} {'PnL':>9}  {'Regime'}")
    print(f"  {'-'*55}")
    for _, r in blocked.sort_values('date').iterrows():
        print(f"  {r['date']:<12} {r['symbol']:<6} {r['grade']:<5} "
              f"{r.get('gap_pct', 0):>+5.1f}%  {r['pnl_usd']:>+9.2f}  {r['regime']}")
    blocked_pnl = blocked['pnl_usd'].sum()
    blocked_wr = len(blocked[blocked['pnl_usd'] > 0]) / len(blocked) * 100
    print(f"  Blocked: {len(blocked)} trades | WR {blocked_wr:.1f}% | Total PnL ${blocked_pnl:+,.0f}")


def main():
    print(f"\n{'='*70}")
    print("  TRAP DAY GATE — Validation Backtest")
    print(f"  Period: {START_DATE} → {END_DATE}  |  {len(SYMBOLS)} symbols")
    print(f"{'='*70}")

    # Build macro pre-event dates
    pre_event_dates = build_pre_event_set(START_DATE, END_DATE)
    print(f"\n  Pre-HIGH_IMPACT days in period: {len(pre_event_dates)}")
    print(f"  Symbols: {len(SYMBOLS)}")

    # Build SPY regime
    print("\n  Loading SPY regime data...")
    spy_regime = build_spy_regime(START_DATE, END_DATE)

    # Run backtest for all symbols, collect all trades + prev-close for gap calc
    print("\n  Running backtest across all symbols...")
    all_trades = []
    symbol_prev_close = {}  # sym → {date_str: prev_close}

    for sym in SYMBOLS:
        print(f"    {sym}...", end=' ', flush=True)
        try:
            df = yf.download(sym, start=START_DATE, end=END_DATE,
                             progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if len(df) < 60:
                print("skip (insufficient data)")
                continue

            # Store prev_close for each date (for gap % calc)
            prev_c = {}
            for i in range(1, len(df)):
                date_str = df.index[i].strftime('%Y-%m-%d')
                prev_c[date_str] = float(df['Close'].iloc[i-1])
            symbol_prev_close[sym] = prev_c

            trades_df = backtest_symbol(sym, spy_regime)
            if not trades_df.empty:
                # Add gap_pct to each trade
                trades_df['gap_pct'] = trades_df.apply(
                    lambda r: (r['entry'] - prev_c.get(r['date'], r['entry'])) /
                              prev_c.get(r['date'], r['entry']) * 100
                    if prev_c.get(r['date'], 0) > 0 else 0, axis=1
                )
                all_trades.append(trades_df)
                print(f"{len(trades_df)} trades")
            else:
                print("0 trades")
        except Exception as e:
            print(f"error: {e}")

    if not all_trades:
        print("No trades found.")
        return

    baseline = pd.concat(all_trades, ignore_index=True)
    baseline['is_pre_event'] = baseline['date'].isin(pre_event_dates)

    print(f"\n  Total trades (baseline): {len(baseline)}")
    print(f"  Pre-event day trades:    {baseline['is_pre_event'].sum()}")

    # ── Gate A: pre-event + gap > 7% ────────────────────────────────────
    gate_a = baseline[~(baseline['is_pre_event'] & (baseline['gap_pct'] > 7.0))]
    # ── Gate B: pre-event + gap > 5% ────────────────────────────────────
    gate_b = baseline[~(baseline['is_pre_event'] & (baseline['gap_pct'] > 5.0))]
    # ── Gate C: pre-event day (all longs blocked) ────────────────────────
    gate_c = baseline[~baseline['is_pre_event']]

    print(f"\n{'='*70}")
    print("  OVERALL STATISTICS")
    print(f"{'='*70}")

    for df, label in [(baseline, "BASELINE (no gate)"),
                      (gate_a,   "GATE A: pre-event + gap>7%"),
                      (gate_b,   "GATE B: pre-event + gap>5%"),
                      (gate_c,   "GATE C: pre-event day (all)")]:
        print_stats(compute_stats(df, label))

    print(f"\n{'='*70}")
    print("  GATE A (pre-event + gap>7%) — BY YEAR vs BASELINE")
    print(f"{'='*70}")
    by_year_table(baseline, gate_a, "Gate A")

    print(f"\n{'='*70}")
    print("  GATE B (pre-event + gap>5%) — BY YEAR vs BASELINE")
    print(f"{'='*70}")
    by_year_table(baseline, gate_b, "Gate B")

    print(f"\n{'='*70}")
    print("  WHICH TRADES DOES GATE A BLOCK?")
    print(f"{'='*70}")
    show_trapped_trades(baseline, gate_a)

    print(f"\n{'='*70}")
    print("  PRE-EVENT DAY BREAKDOWN (raw data)")
    print(f"{'='*70}")
    pre_trades = baseline[baseline['is_pre_event']].copy()
    if not pre_trades.empty:
        print(f"  {len(pre_trades)} trades on {pre_trades['date'].nunique()} pre-event days")
        print(f"  WR: {len(pre_trades[pre_trades['pnl_usd']>0])/len(pre_trades)*100:.1f}%")
        print(f"  Total PnL: ${pre_trades['pnl_usd'].sum():+,.0f}")
        print(f"  Avg PnL/trade: ${pre_trades['pnl_usd'].mean():+.2f}")
        print(f"\n  By gap size on pre-event days:")
        bins = [(-999,-5),(-5,0),(0,3),(3,5),(5,7),(7,10),(10,999)]
        labels = ['gap<-5%','gap-5→0','gap0→3%','gap3→5%','gap5→7%','gap7→10%','gap>10%']
        for (lo, hi), lbl in zip(bins, labels):
            sub = pre_trades[(pre_trades['gap_pct'] >= lo) & (pre_trades['gap_pct'] < hi)]
            if len(sub) == 0:
                continue
            swr = len(sub[sub['pnl_usd']>0])/len(sub)*100
            print(f"    {lbl:<12}: {len(sub):>4} trades  {swr:>5.1f}% WR  ${sub['pnl_usd'].sum():>+9,.0f}  avg ${sub['pnl_usd'].mean():>+.2f}")

    print(f"\n{'='*70}")
    print("  CONCLUSION")
    print(f"{'='*70}")
    b_pnl = baseline['pnl_usd'].sum()
    a_pnl = gate_a['pnl_usd'].sum()
    b_wr  = len(baseline[baseline['pnl_usd']>0]) / len(baseline) * 100
    a_wr  = len(gate_a[gate_a['pnl_usd']>0]) / len(gate_a) * 100 if len(gate_a) else 0
    n_blocked_a = len(baseline) - len(gate_a)
    print(f"  Gate A blocks {n_blocked_a} trades ({n_blocked_a/len(baseline)*100:.1f}% of all trades)")
    print(f"  PnL:  ${b_pnl:+,.0f} → ${a_pnl:+,.0f}  (Δ ${a_pnl-b_pnl:+,.0f})")
    print(f"  WR:   {b_wr:.1f}% → {a_wr:.1f}%")
    if a_pnl > b_pnl:
        print(f"  ✅ Gate A HELPS: +${a_pnl-b_pnl:+,.0f} by avoiding trapped longs")
    else:
        print(f"  ❌ Gate A HURTS: ${a_pnl-b_pnl:+,.0f} — pre-event gap days often continue")


if __name__ == '__main__':
    main()
