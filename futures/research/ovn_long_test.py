#!/usr/bin/env python3
"""
futures/ovn_long_test.py — Test: allow LONG entries on SHORT OVN_POS bias days

Question: When OVN_POS ≤ 0.20 (overnight closed near low, SHORT bias),
          are there good LONG setups being blocked?

Hypothesis A: Overnight bears exhausted → RTH bounce → LONGs do well.
Hypothesis B: SHORT bias = bearish day → LONGs are correctly blocked.

Method:
  1. Run 2025-2026 sim in BASELINE mode (current gate)
  2. Run again with ALLOW_LONG_ON_SHORT_BIAS = True
  3. Isolate the ADDED trades (LONG on SHORT bias days)
  4. Report their WR, P&L, time distribution, regime breakdown

2025-2026 data only per user instruction.
"""
import sys, os
import datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import futures.sim_replay as sr
from futures.collect_bars import load_bars, filter_ny_session

START = dt.date(2025, 1, 1)
END   = dt.date(2026, 6, 16)


def run_scenario(all_bars, trade_dates, allow_long_on_short: bool, label: str):
    """Run all trade dates, return list of trade dicts tagged with daily_bias + ovn_pos."""
    sr.ALLOW_LONG_ON_SHORT_BIAS = allow_long_on_short
    all_trades = []
    for td in trade_dates:
        trades, daily_bias, ovn_pos = sr.simulate_day(all_bars, td)
        for t in trades:
            t['daily_bias'] = daily_bias
            t['ovn_pos']    = ovn_pos
        all_trades.extend(trades)
    sr.ALLOW_LONG_ON_SHORT_BIAS = False   # always reset
    return all_trades


def stats(trades):
    if not trades:
        return {'n': 0, 'wr': 0, 'avg': 0, 'total': 0}
    pnls = [t['pnl'] for t in trades]
    wins = [1 for t in trades if t['pnl'] > 0]
    return {
        'n':     len(trades),
        'wr':    len(wins) / len(trades) * 100,
        'avg':   np.mean(pnls),
        'total': sum(pnls),
    }


def print_stats(label, s):
    if s['n'] == 0:
        print(f"  {label:<30}: no trades")
        return
    print(f"  {label:<30}: N={s['n']:3d}  WR={s['wr']:>5.1f}%  avg=${s['avg']:>+8.2f}  total=${s['total']:>+9.2f}")


def main():
    print("OVN_POS LONG-on-SHORT-bias Test — 2025-2026 data only")
    print("=" * 70)

    # Load bars with extra history for 50-day MA gate
    load_start = (dt.datetime(START.year, START.month, START.day) - dt.timedelta(days=110)).date().isoformat()
    all_bars = load_bars('MNQ', start=load_start, end='2026-12-31')

    rth_all     = filter_ny_session(all_bars)
    trade_dates = sorted(
        d for d in set(rth_all.index.date)
        if START <= d <= END
    )
    print(f"Trade dates: {len(trade_dates)} ({START} → {END})\n")

    # ── Run both scenarios ────────────────────────────────────────────────────
    print("Running BASELINE (current gate)...")
    baseline = run_scenario(all_bars, trade_dates, allow_long_on_short=False, label='baseline')
    print(f"  {len(baseline)} trades total\n")

    print("Running TEST (allow LONG on SHORT bias)...")
    test     = run_scenario(all_bars, trade_dates, allow_long_on_short=True,  label='test')
    print(f"  {len(test)} trades total\n")

    # ── Identify added trades (LONG on SHORT bias days — these are new) ───────
    # Key: date + entry_time + side (unique trade identifier)
    def trade_key(t):
        return (t['date'], t['entry_time'], t['side'])

    baseline_keys = set(trade_key(t) for t in baseline)
    added_trades  = [t for t in test if trade_key(t) not in baseline_keys]
    # Removed = in baseline but not in test (shouldn't happen since we only added a gate)
    removed_trades = [t for t in baseline if trade_key(t) not in set(trade_key(t) for t in test)]

    print(f"Added trades  (new LONGs on SHORT bias days): {len(added_trades)}")
    print(f"Removed trades (displaced by new entries):    {len(removed_trades)}")
    print()

    # ── Core stats ───────────────────────────────────────────────────────────
    print("═" * 70)
    print("CORE COMPARISON")
    print("═" * 70)

    b_all  = stats(baseline)
    t_all  = stats(test)
    added  = stats(added_trades)
    rmvd   = stats(removed_trades)

    print_stats("Baseline total", b_all)
    print_stats("Test total", t_all)
    delta_pnl = t_all['total'] - b_all['total']
    print(f"\n  Net P&L delta: ${delta_pnl:+.2f}  (test minus baseline)")
    print()

    print_stats("Added LONG trades (new)", added)
    print_stats("Removed trades (displaced)", rmvd)
    print()

    # ── Added trades breakdown ────────────────────────────────────────────────
    if len(added_trades) == 0:
        print("No new trades added. Gate had no effect.")
        return

    print("═" * 70)
    print(f"ADDED TRADE DETAIL  ({len(added_trades)} new LONG trades on SHORT bias days)")
    print("═" * 70)

    # All added trades should be LONG on SHORT bias
    sides  = set(t['side'] for t in added_trades)
    biases = set(t['daily_bias'] for t in added_trades)
    print(f"  Sides:  {sides}  (expected: LONG only)")
    print(f"  Biases: {biases}  (expected: SHORT only)")
    print()

    # OVN_POS distribution of added trades
    ovn_vals = [t['ovn_pos'] for t in added_trades]
    print(f"  OVN_POS distribution of added trades:")
    print(f"    min={min(ovn_vals):.3f}  median={np.median(ovn_vals):.3f}  max={max(ovn_vals):.3f}")

    # Sub-bucket by OVN_POS
    print(f"\n  WR by OVN_POS bucket:")
    buckets = [(0.00, 0.08, 'very low  (0.00–0.08)'),
               (0.08, 0.14, 'low       (0.08–0.14)'),
               (0.14, 0.21, 'mid-low   (0.14–0.21)')]
    for lo, hi, lbl in buckets:
        sub = [t for t in added_trades if lo <= t['ovn_pos'] < hi]
        if sub:
            s = stats(sub)
            print(f"    {lbl}: N={s['n']:2d}  WR={s['wr']:>5.1f}%  avg=${s['avg']:>+7.2f}  total=${s['total']:>+8.2f}")

    # Entry time distribution
    print(f"\n  Entry time distribution:")
    from collections import defaultdict
    time_groups = defaultdict(list)
    for t in added_trades:
        time_groups[t['entry_time']].append(t['pnl'])
    for tm in sorted(time_groups):
        pnls = time_groups[tm]
        wr   = sum(1 for p in pnls if p > 0) / len(pnls) * 100
        avg  = np.mean(pnls)
        bar  = '█' * len(pnls)
        print(f"    {tm}  N={len(pnls):2d}  WR={wr:>5.1f}%  avg=${avg:>+7.2f}  {bar}")

    # Exit reason breakdown
    print(f"\n  Exit reasons:")
    from collections import Counter
    exits = Counter(t['exit_reason'] for t in added_trades)
    for reason, count in exits.most_common():
        sub = [t for t in added_trades if t['exit_reason'] == reason]
        s = stats(sub)
        print(f"    {reason:<20}: N={count:2d}  WR={s['wr']:>5.1f}%  avg=${s['avg']:>+7.2f}")

    # Regime breakdown (requires IB range — use avengers CSV if available)
    print(f"\n  Setup breakdown:")
    setups = Counter(t['setup'] for t in added_trades)
    for setup, count in setups.most_common():
        sub = [t for t in added_trades if t['setup'] == setup]
        s = stats(sub)
        print(f"    {setup:<20}: N={count:2d}  WR={s['wr']:>5.1f}%  avg=${s['avg']:>+7.2f}")

    # Month breakdown
    print(f"\n  Monthly breakdown of added trades:")
    from collections import defaultdict
    monthly = defaultdict(list)
    for t in added_trades:
        month = t['date'][:7]
        monthly[month].append(t)
    for month in sorted(monthly):
        sub = monthly[month]
        s = stats(sub)
        print(f"    {month}: N={s['n']:2d}  WR={s['wr']:>5.1f}%  avg=${s['avg']:>+7.2f}  total=${s['total']:>+8.2f}")

    # ── Compare SHORT trades on same days ─────────────────────────────────────
    print(f"\n{'═'*70}")
    print("SHORT TRADES ON SAME DAYS — were any displaced?")
    print("═" * 70)
    added_dates = set(t['date'] for t in added_trades)

    baseline_shorts_on_added_dates = [t for t in baseline if t['date'] in added_dates and t['side'] == 'SHORT']
    test_shorts_on_added_dates     = [t for t in test     if t['date'] in added_dates and t['side'] == 'SHORT']
    print_stats("Baseline SHORTs on these days", stats(baseline_shorts_on_added_dates))
    print_stats("Test SHORTs on these days",     stats(test_shorts_on_added_dates))
    if len(removed_trades) > 0:
        print(f"\n  Displaced trades detail:")
        for t in removed_trades[:10]:
            print(f"    {t['date']}  {t['entry_time']}  {t['side']}  {t['setup']}  ${t['pnl']:+.2f}")

    # ── Final verdict ─────────────────────────────────────────────────────────
    print(f"\n{'═'*70}")
    print("VERDICT")
    print("═" * 70)

    if len(added_trades) == 0:
        print("  No trades added — gate had no effect.")
    elif added['wr'] >= 55 and added['avg'] > 0:
        print(f"  STRONG SIGNAL: Added {added['n']} LONGs with {added['wr']:.1f}% WR, avg ${added['avg']:+.2f}")
        print(f"  → Consider allowing LONGs on SHORT bias days (exhausted bears thesis holds)")
        print(f"  → Net P&L delta: ${delta_pnl:+.2f}")
    elif added['wr'] >= 45 and added['avg'] > 0:
        print(f"  MIXED: Added {added['n']} LONGs with {added['wr']:.1f}% WR, avg ${added['avg']:+.2f}")
        print(f"  → Slight edge but may not be worth the added complexity")
        print(f"  → Net P&L delta: ${delta_pnl:+.2f}")
    else:
        print(f"  CONFIRMED BLOCK: Added {added['n']} LONGs with {added['wr']:.1f}% WR, avg ${added['avg']:+.2f}")
        print(f"  → SHORT bias day LONG entries are correctly blocked. Gate is right.")
        print(f"  → Net P&L delta: ${delta_pnl:+.2f}")

    print()


if __name__ == '__main__':
    main()
