# research_book_health_graded.py — Aug 4 2026
#
# Direct response to the "weather analogy" pushback on Book Health: the
# current design is a hard ON/OFF switch on a trailing 10-day drift average.
# The critique: this can sit fully OFF for days/weeks waiting for the trailing
# average to cross zero, even on days that individually look fine — and it's
# symmetric, so it also delays turning back ON for a while after the tape
# genuinely improves. Proposed alternative: a GRADED response (full size when
# healthy, half size when ambiguous, fully off only when genuinely bad) —
# "take an umbrella" instead of "stay home" during the uncertain middle.
#
# This walks forward day-by-day through the FULL scan_log history (May 27 -
# Aug 3, before AND after the Jul 22 reset — this is a design-choice test,
# not a re-validation of whether book health works at all, so more breadth
# across both good and bad stretches is the right call here, not the 2yr
# equity-research window rule) and compares:
#   BINARY:  weight = 1.0 if trailing health > 0  else 0.0   (today's live rule)
#   GRADED:  weight = 1.0 if health > +0.10%
#            weight = 0.5 if -0.25% <= health <= +0.10%   ("umbrella" tier)
#            weight = 0.0 if health < -0.25%              (genuinely bad)
# "Captured" per signal = (actual_day_pct - intra_chg) * weight — same drift
# math book health already uses, just weighted instead of gated to 0/1.
#
# Command: venv/bin/python research_book_health_graded.py

import sqlite3
import pandas as pd
import numpy as np
from datetime import timedelta

WINDOW_DAYS = 10
MIN_DAYS    = 4
MIN_ROWS    = 30
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


def trailing_health(df, as_of_date, days_available):
    """Same windowing logic as compute_book_health: most recent up to
    WINDOW_DAYS distinct days strictly before as_of_date."""
    prior_days = [d for d in days_available if d < as_of_date][-WINDOW_DAYS:]
    if len(prior_days) < MIN_DAYS:
        return None, len(prior_days), 0
    rows = df[df['scan_date'].isin(prior_days)]
    if len(rows) < MIN_ROWS:
        return None, len(prior_days), len(rows)
    return rows['drift'].mean(), len(prior_days), len(rows)


def main():
    df = load_data()
    all_days = sorted(df['scan_date'].unique())
    print(f"{len(all_days)} trading days with enriched A+ LONG data: {all_days[0]} -> {all_days[-1]}\n")

    results = []
    for d in all_days:
        health, n_days, n_rows = trailing_health(df, d, all_days)
        today_rows = df[df['scan_date'] == d]
        if health is None:
            # cold start: current live system defaults ON (full size) here
            binary_w, graded_w, status = 1.0, 1.0, 'COLD_START'
        else:
            binary_w = 1.0 if health > 0 else 0.0
            if health > FULL_THRESH:
                graded_w, status = 1.0, 'FULL'
            elif health >= OFF_THRESH:
                graded_w, status = 0.5, 'UMBRELLA'
            else:
                graded_w, status = 0.0, 'OFF'
        actual_drift_today = today_rows['drift'].sum()
        n_today = len(today_rows)
        results.append({
            'date': d, 'health': health, 'n_days': n_days, 'n_rows': n_rows,
            'status': status, 'n_signals_today': n_today,
            'binary_captured': actual_drift_today * binary_w,
            'graded_captured': actual_drift_today * graded_w,
            'raw_sum_today': actual_drift_today,
        })

    res = pd.DataFrame(results)
    print("=" * 78)
    print("TOTALS (sum of drift-pts captured across the whole history, unweighted per-signal)")
    print("=" * 78)
    print(f"BINARY (today's live rule):  {res['binary_captured'].sum():+9.1f} pts")
    print(f"GRADED (umbrella tier):      {res['graded_captured'].sum():+9.1f} pts")
    print(f"UNGATED (always full size):  {res['raw_sum_today'].sum():+9.1f} pts  (reference — no filter at all)")

    print("\n" + "=" * 78)
    print("TIME SPENT PER STATUS (trading days)")
    print("=" * 78)
    print(res['status'].value_counts().to_string())

    print("\n" + "=" * 78)
    print("DID GRADED ACTUALLY HELP ON THE 'UMBRELLA' DAYS SPECIFICALLY?")
    print("=" * 78)
    umb = res[res['status'] == 'UMBRELLA']
    print(f"{len(umb)} umbrella (half-size) days, {umb['n_signals_today'].sum()} signals total")
    print(f"  sum of raw (unweighted) drift on those days: {umb['raw_sum_today'].sum():+.1f} pts")
    print(f"  binary would have captured (0x, since binary treats these as OFF): 0.0 pts")
    print(f"  graded captured (0.5x): {umb['graded_captured'].sum():+.1f} pts")
    print(f"  -> was the umbrella tier's raw sign actually favorable more often than not?")
    print(f"     positive days: {(umb['raw_sum_today'] > 0).sum()} / {len(umb)} "
          f"({(umb['raw_sum_today'] > 0).mean()*100:.0f}%)")

    print("\n" + "=" * 78)
    print("LONGEST CONSECUTIVE OFF STREAK (binary vs graded-fully-off)")
    print("=" * 78)
    def longest_streak(mask):
        best = cur = 0
        for v in mask:
            cur = cur + 1 if v else 0
            best = max(best, cur)
        return best
    print(f"BINARY OFF streak (max consecutive days weight=0):   "
          f"{longest_streak(res['status'].isin(['OFF']) & (res['health'] <= 0))} days")
    print(f"  (note: BINARY off = health<=0, includes what GRADED calls UMBRELLA)")
    binary_off_mask = res['health'].apply(lambda h: h is not None and h <= 0)
    print(f"BINARY off (health<=0) longest streak: {longest_streak(binary_off_mask)} days")
    graded_off_mask = res['status'] == 'OFF'
    print(f"GRADED fully-off (health<{OFF_THRESH}%) longest streak: {longest_streak(graded_off_mask)} days")

    res.to_csv('/Users/sushil/trading/research_book_health_graded_results.csv', index=False)
    print("\nFull day-by-day table -> research_book_health_graded_results.csv")


if __name__ == '__main__':
    main()
