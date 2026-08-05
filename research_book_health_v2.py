# research_book_health_v2.py — Aug 4 2026
#
# Follow-up to research_book_health_graded.py. That test found graded sizing
# only modestly beats binary (-122.7 vs -149.7 pts) and doesn't fix "sitting
# for weeks" because health has been decisively negative, not ambiguous, for
# most of the tracked history. This tests the two follow-up questions that
# raises:
#   1. Is the 10-day trailing window itself too slow to react? (shorter
#      windows: 5-day, 7-day, tested both binary and graded)
#   2. Does a fully CONTINUOUS weight (not 3 discrete tiers) behave any
#      differently than the tiered version?
# Also dumps the exact numbers behind one specific week (Jul 28-31) for a
# worked example.
#
# Command: venv/bin/python research_book_health_v2.py

import sqlite3
import pandas as pd
import numpy as np

MIN_DAYS = 4
MIN_ROWS = 30
FULL_THRESH = 0.10
OFF_THRESH  = -0.25


def load_data():
    conn = sqlite3.connect('/Users/sushil/trading/trades.db')
    df = pd.read_sql("""
        SELECT scan_date, actual_day_pct - intra_chg AS drift
        FROM scan_log
        WHERE grade='A+' AND direction='LONG' AND enriched=1
          AND actual_day_pct IS NOT NULL AND intra_chg IS NOT NULL
        ORDER BY scan_date
    """, conn)
    conn.close()
    return df


def trailing_health(df, as_of_date, days_available, window):
    prior_days = [d for d in days_available if d < as_of_date][-window:]
    if len(prior_days) < MIN_DAYS:
        return None, len(prior_days)
    rows = df[df['scan_date'].isin(prior_days)]
    if len(rows) < MIN_ROWS:
        return None, len(prior_days)
    return rows['drift'].mean(), len(prior_days)


def continuous_weight(health, floor=-1.0, ceil=0.25):
    """Linear ramp: 0 weight at/below floor, 1.0 weight at/above ceil,
    straight line between. No discrete tiers."""
    if health is None:
        return 1.0
    if health <= floor:
        return 0.0
    if health >= ceil:
        return 1.0
    return (health - floor) / (ceil - floor)


def run_config(df, all_days, window, mode):
    """mode: 'binary', 'graded' (3-tier), or 'continuous'."""
    total = 0.0
    off_streak = cur_streak = 0
    per_day = []
    for d in all_days:
        health, n_days = trailing_health(df, d, all_days, window)
        today = df[df['scan_date'] == d]
        raw = today['drift'].sum()

        if health is None:
            w = 1.0  # cold start default ON, same as live system
        elif mode == 'binary':
            w = 1.0 if health > 0 else 0.0
        elif mode == 'graded':
            if health > FULL_THRESH: w = 1.0
            elif health >= OFF_THRESH: w = 0.5
            else: w = 0.0
        elif mode == 'continuous':
            w = continuous_weight(health)

        total += raw * w
        if w == 0.0:
            cur_streak += 1
            off_streak = max(off_streak, cur_streak)
        else:
            cur_streak = 0
        per_day.append({'date': d, 'health': health, 'weight': w, 'raw': raw})
    return total, off_streak, pd.DataFrame(per_day)


def main():
    df = load_data()
    all_days = sorted(df['scan_date'].unique())
    print(f"{len(all_days)} days, {all_days[0]} -> {all_days[-1]}\n")

    print("=" * 78)
    print("WINDOW LENGTH x WEIGHTING SCHEME — total captured drift (pts) / longest OFF streak")
    print("=" * 78)
    print(f"{'window':>8} {'binary':>18} {'graded (3-tier)':>18} {'continuous':>18}")
    for window in (5, 7, 10):
        row = [f"{window}d"]
        for mode in ('binary', 'graded', 'continuous'):
            total, streak, _ = run_config(df, all_days, window, mode)
            row.append(f"{total:+8.1f} ({streak:2d}d off)")
        print(f"{row[0]:>8} {row[1]:>18} {row[2]:>18} {row[3]:>18}")

    print("\n" + "=" * 78)
    print("WORKED EXAMPLE: the week of Jul 28 - Jul 31 (10-day window, as live today)")
    print("=" * 78)
    _, _, detail = run_config(df, all_days, 10, 'graded')
    _, _, detail_c = run_config(df, all_days, 10, 'continuous')
    detail['weight_continuous'] = detail_c['weight']
    detail['captured_binary']     = detail.apply(lambda r: r['raw'] * (1.0 if (r['health'] is None or r['health'] > 0) else 0.0), axis=1)
    detail['captured_graded']     = detail['raw'] * detail['weight']
    detail['captured_continuous'] = detail['raw'] * detail['weight_continuous']
    window_view = detail[(detail['date'] >= '2026-07-24') & (detail['date'] <= '2026-08-03')]
    print(window_view[['date', 'health', 'raw', 'weight', 'weight_continuous',
                       'captured_binary', 'captured_graded', 'captured_continuous']].to_string(index=False))

    detail.to_csv('/Users/sushil/trading/research_book_health_v2_results.csv', index=False)
    print("\nFull table -> research_book_health_v2_results.csv")


if __name__ == '__main__':
    main()
