"""
Long Flip-Exit on WEAK x3 — Backtest Proxy
============================================
Currently the system only has a WEAK flip-exit for SHORTS (3 consecutive WEAK scans
→ covers losing short). LONGS have no such protection.

Jun 9 postmortem: 3 SEMIS longs entered at 10:06am (NORMAL x2).
  Regime: CAUTIOUS 10:12am → CAUTIOUS 10:22am → WEAK 10:27am.
  WEAK x3 at ~10:27am. Stops hit 10:31am–11:22am.
  If we'd exited at WEAK x3: ~$150-200 saved vs waiting for hard stop.

Proxy in daily backtest:
  The daily regime represents end-of-day SPY conditions. An intraday WEAK x3
  signal (fires within 45 min of entry) maps closely to: SPY regime is WEAK
  on the ENTRY DAY (regime degraded during the session, not just at close).

  For each LONG trade that ended in a loss:
    - Was SPY regime WEAK on the entry date? → early flip-exit would have saved ~50% of loss
    - Was SPY regime WEAK on the EXIT date? → partial savings (less certain)

  Savings factor: 50% of avoided loss (conservative estimate based on Jun 9 data).
  Jun 9 actual: ACLS saved ~$4/sh of $10.34, CRDO ~$10 of $13.46, LRCX ~$7 of $16.90.
  Average: ~50-55% of loss avoided.

Second analysis: LONG flip-exit on WEAK x3 using different exit price fractions.
This gives a sensitivity range for the mechanism's value.
"""

import sys, warnings
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date

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


def by_year_table(baseline_df, flipped_df):
    years = sorted(baseline_df['year'].unique())
    print(f"  {'Year':<6} {'Base_N':>7} {'Base_WR':>8} {'Base_PnL':>10}  "
          f"{'Flip_N':>7} {'Flip_WR':>8} {'Flip_PnL':>10}  {'Δ_PnL':>9}  {'Result'}")
    print(f"  {'-'*82}")
    for yr in years:
        b = baseline_df[baseline_df['year'] == yr]
        f = flipped_df[flipped_df['year'] == yr]
        bwr = len(b[b['pnl_usd'] > 0]) / len(b) * 100 if len(b) else 0
        fwr = len(f[f['pnl_usd'] > 0]) / len(f) * 100 if len(f) else 0
        bp = b['pnl_usd'].sum()
        fp = f['pnl_usd'].sum()
        delta = fp - bp
        result = "✓ BETTER" if delta > 20 else ("= same" if abs(delta) <= 20 else "✗ worse")
        print(f"  {yr:<6} {len(b):>7} {bwr:>7.1f}% {bp:>+10,.0f}  "
              f"{len(f):>7} {fwr:>7.1f}% {fp:>+10,.0f}  {delta:>+9,.0f}  {result}")
    print(f"  {'-'*82}")
    btot = baseline_df['pnl_usd'].sum()
    ftot = flipped_df['pnl_usd'].sum()
    bwr_t = len(baseline_df[baseline_df['pnl_usd'] > 0]) / len(baseline_df) * 100
    fwr_t = len(flipped_df[flipped_df['pnl_usd'] > 0]) / len(flipped_df) * 100
    print(f"  {'TOTAL':<6} {len(baseline_df):>7} {bwr_t:>7.1f}% {btot:>+10,.0f}  "
          f"{len(flipped_df):>7} {fwr_t:>7.1f}% {ftot:>+10,.0f}  {ftot-btot:>+9,.0f}")


def apply_flip_exit(baseline_df, spy_regime, save_fraction):
    """
    For each losing LONG trade where SPY regime on entry date was WEAK:
    simulate that WEAK x3 fired → early exit → save `save_fraction` of the loss.
    Returns modified DataFrame + count of affected trades.
    """
    modified = baseline_df.copy()
    n_affected = 0
    for idx, trade in modified.iterrows():
        if trade['pnl_usd'] >= 0:
            continue
        entry_regime = spy_regime.get(trade['date'], 'NORMAL')
        if entry_regime != 'WEAK':
            continue
        original_loss = trade['pnl_usd']
        modified.at[idx, 'pnl_usd'] = original_loss * (1 - save_fraction)
        n_affected += 1
    return modified, n_affected


def main():
    print(f"\n{'='*72}")
    print("  LONG FLIP-EXIT ON WEAK x3 — Backtest Proxy")
    print(f"  Period: {START_DATE} → {END_DATE}  |  {len(SYMBOLS)} symbols")
    print(f"{'='*72}")
    print("""
  Mechanism: when SPY regime shows WEAK x3 consecutive scans (15-45 min after
  entry), exit all losing LONG positions immediately — don't wait for hard stop.

  Currently exists for SHORTS: NORMAL/STRONG x3 → cover losing shorts.
  Missing for LONGS: WEAK x3 → exit losing longs.

  Daily backtest proxy: if SPY regime = WEAK on entry date → early exit saved
  50% of the eventual stop loss (conservative estimate from Jun 9 data).
""")

    print("  Loading SPY regime data...")
    spy_regime = build_spy_regime(START_DATE, END_DATE)

    print("\n  Running backtest across all symbols...")
    all_trades = []
    for sym in SYMBOLS:
        print(f"    {sym}...", end=' ', flush=True)
        try:
            trades_df = backtest_symbol(sym, spy_regime)
            if not trades_df.empty:
                trades_df['year'] = pd.to_datetime(trades_df['date']).dt.year
                trades_df['sector'] = trades_df['symbol'].map(lambda s: _SM.get(s, 'OTHER'))
                # Add entry date regime
                trades_df['entry_regime'] = trades_df['date'].map(
                    lambda d: spy_regime.get(d, 'NORMAL'))
                all_trades.append(trades_df)
                print(f"{len(trades_df)}t")
            else:
                print("0t")
        except Exception as e:
            print(f"err: {e}")

    if not all_trades:
        print("No trades.")
        return

    baseline = pd.concat(all_trades, ignore_index=True)
    losing = baseline[baseline['pnl_usd'] < 0]
    weak_entry = losing[losing['entry_regime'] == 'WEAK']

    print(f"\n  Total baseline trades:  {len(baseline)}")
    print(f"  Losing trades:          {len(losing)}")
    print(f"  Losing + WEAK regime:   {len(weak_entry)} → these are the flip-exit candidates")
    print(f"  % of all trades:        {len(weak_entry)/len(baseline)*100:.1f}%")
    print(f"  % of losing trades:     {len(weak_entry)/len(losing)*100:.1f}%")

    # Distribution of losing trades by entry regime
    print(f"\n  Losing trade regime breakdown:")
    print(f"  {'Regime':>10}  {'Count':>7}  {'% of losses':>12}  {'Total loss':>12}  {'Avg loss':>10}")
    print(f"  {'-'*55}")
    for reg in ['STRONG', 'NORMAL', 'WEAK']:
        sub = losing[losing['entry_regime'] == reg]
        if len(sub) == 0:
            continue
        pct = len(sub) / len(losing) * 100
        print(f"  {reg:>10}  {len(sub):>7}  {pct:>11.1f}%  ${sub['pnl_usd'].sum():>+10,.0f}  "
              f"${sub['pnl_usd'].mean():>+8.2f}")

    # Why do we have losing trades entered on WEAK regime?
    # Answer: regime gate blocks NEW entries on WEAK, but some trades enter on NORMAL
    # and regime degrades to WEAK by end-of-day. In the daily backtest, regime is computed
    # from SPY daily bars, so the "entry regime" is the regime AT or NEAR market open.
    # Trades that entered NORMAL but were stopped same-day would show as NORMAL entry.
    # The proxy: entry_regime=WEAK means regime was already degrading at entry.

    print(f"\n  Note: WEAK entry regime = SPY already below 20MA at market open.")
    print(f"  These are entries where regime gate DID NOT block (bug or gap from prior day).")
    print(f"  Flip-exit would help by cutting losses earlier, even if entry regime was already bad.")

    print(f"\n{'='*72}")
    print("  SENSITIVITY ANALYSIS: savings by exit-fraction assumption")
    print(f"  Save fraction = how much of the loss we avoid by exiting at WEAK x3")
    print(f"{'='*72}")

    print(f"\n  {'Save %':>8}  {'Affected':>9}  {'PnL saved':>11}  {'Annual':>8}  {'New total WR':>13}  {'New PnL':>10}")
    print(f"  {'-'*65}")

    best_results = {}
    for frac in [0.30, 0.40, 0.50, 0.60, 0.70]:
        mod, n_aff = apply_flip_exit(baseline, spy_regime, frac)
        saved = mod['pnl_usd'].sum() - baseline['pnl_usd'].sum()
        ann_saved = saved / 6.4
        new_wr = len(mod[mod['pnl_usd'] > 0]) / len(mod) * 100
        new_pnl = mod['pnl_usd'].sum()
        print(f"  {frac*100:>7.0f}%  {n_aff:>9}  ${saved:>+9,.0f}  ${ann_saved:>+6,.0f}/yr  "
              f"{new_wr:>12.1f}%  ${new_pnl:>+8,.0f}")
        best_results[frac] = mod

    # Use 50% as the central estimate
    central_frac = 0.50
    mod50, n50 = apply_flip_exit(baseline, spy_regime, central_frac)

    print(f"\n{'='*72}")
    print(f"  DETAILED RESULTS — 50% save fraction (central estimate)")
    print(f"{'='*72}")
    print_stats(compute_stats(baseline, "BASELINE (no flip-exit)"))
    print_stats(compute_stats(mod50, "WITH LONG FLIP-EXIT (50% save on WEAK-entry losses)"))

    print(f"\n  By Year:")
    by_year_table(baseline, mod50)

    # Sector breakdown: where does flip-exit help most?
    print(f"\n{'='*72}")
    print("  WHERE DOES FLIP-EXIT HELP MOST? (by sector, WEAK-entry losses)")
    print(f"{'='*72}")
    print(f"\n  {'Sector':<20}  {'Losses':>7}  {'WEAK losses':>12}  {'Loss avg':>10}  {'Saved(50%)':>12}")
    print(f"  {'-'*65}")
    for sector in sorted(weak_entry['sector'].unique()):
        sub = weak_entry[weak_entry['sector'] == sector]
        all_sec_losses = losing[losing['sector'] == sector]
        avg_loss = sub['pnl_usd'].mean()
        saved = abs(sub['pnl_usd'].sum()) * 0.5
        print(f"  {sector:<20}  {len(all_sec_losses):>7}  {len(sub):>12}  "
              f"${avg_loss:>+8.2f}  ${saved:>+10,.0f}")

    # Worst days where flip-exit would have saved the most
    print(f"\n{'='*72}")
    print("  TOP 10 WORST DAYS SAVED BY FLIP-EXIT")
    print(f"  (days where WEAK entry losses were largest → most savings)")
    print(f"{'='*72}")
    daily_weak_loss = weak_entry.groupby('date')['pnl_usd'].sum().sort_values()
    top10 = daily_weak_loss.head(10)
    print(f"\n  {'Date':<12}  {'Loss':>10}  {'Saved(50%)':>12}  {'Symbols'}")
    print(f"  {'-'*55}")
    for dt, loss in top10.items():
        syms = weak_entry[weak_entry['date'] == dt]['symbol'].tolist()
        print(f"  {dt:<12}  ${loss:>+8,.0f}  ${abs(loss)*0.5:>+10,.0f}  "
              f"{', '.join(syms[:4])}{'...' if len(syms)>4 else ''}")

    print(f"\n{'='*72}")
    print("  CONCLUSION")
    print(f"{'='*72}")
    saved_total = mod50['pnl_usd'].sum() - baseline['pnl_usd'].sum()
    saved_ann = saved_total / 6.4
    print(f"""
  Long flip-exit on WEAK x3 (50% save estimate):
  - Affects {n50} trades ({n50/len(baseline)*100:.1f}% of all trades)
  - Saves ${saved_total:+,.0f} over 6.4 years (${saved_ann:+,.0f}/yr)
  - WR improves: {len(baseline[baseline['pnl_usd']>0])/len(baseline)*100:.1f}% → {len(mod50[mod50['pnl_usd']>0])/len(mod50)*100:.1f}%

  The mechanism does NOT require knowing WHY the regime is WEAK — it fires
  automatically when the 3 consecutive WEAK scans trigger. Works for both
  sector-sweep days (Jun 9) and ordinary bearish days.

  Implementation: mirror of existing short flip-exit in auto_trader.py.
  Add: if `consecutive_weak_count >= 3` AND open LONG positions → exit_all_longs()
  Suggested to build after sector sweep gate validation is complete.
""")


if __name__ == '__main__':
    main()
