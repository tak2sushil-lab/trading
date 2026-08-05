# research_book_health_transition_risk.py — Aug 4 2026
#
# Direct test of the user's transition-risk question: when the trailing
# health flips OFF->ON, is the system now trading right as conditions are
# about to turn against it (since the flip is itself lagging, built on days
# that already happened)? And once ON, does it stay ON for a while into a
# reversal before enough red days drag it back OFF?
#
# Walks forward day-by-day over the FULL available history (Apr 15 - Aug 3,
# not just the post-reset window) using the exact same trailing-health math
# as the live compute_book_health(), and specifically measures the days
# immediately following an OFF->ON transition vs. days deep into a sustained
# ON or OFF run.
#
# Command: venv/bin/python research_book_health_transition_risk.py

import sqlite3
import pandas as pd

WINDOW_DAYS = 10
MIN_DAYS = 4
MIN_ROWS = 30


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
    prior_days = [d for d in days_available if d < as_of_date][-WINDOW_DAYS:]
    if len(prior_days) < MIN_DAYS:
        return None
    rows = df[df['scan_date'].isin(prior_days)]
    if len(rows) < MIN_ROWS:
        return None
    return rows['drift'].mean()


def main():
    df = load_data()
    all_days = sorted(df['scan_date'].unique())
    print(f"{len(all_days)} days total: {all_days[0]} -> {all_days[-1]}\n")

    rows = []
    prev_status = None
    for d in all_days:
        health = trailing_health(df, d, all_days)
        status = 'ON' if (health is None or health > 0) else 'OFF'
        today = df[df['scan_date'] == d]
        raw = today['drift'].sum()
        n = len(today)
        transition = (prev_status is not None and status != prev_status)
        rows.append({'date': d, 'health': health, 'status': status,
                     'transition': transition, 'raw_today': raw, 'n_signals': n})
        prev_status = status

    res = pd.DataFrame(rows)

    print("=" * 78)
    print("FULL TIMELINE — status + transitions")
    print("=" * 78)
    print(res[['date', 'health', 'status', 'transition', 'raw_today', 'n_signals']].to_string(index=False))

    # Tag each ON day with "days since the ON streak started"
    res['on_streak_age'] = 0
    age = 0
    for i, row in res.iterrows():
        if row['status'] == 'ON':
            age = age + 1 if (i > 0 and res.loc[i-1, 'status'] == 'ON') else 1
        else:
            age = 0
        res.loc[i, 'on_streak_age'] = age

    print("\n" + "=" * 78)
    print("FRESH-ON (streak age 1-2 days) vs SUSTAINED-ON (age 3+) — forward day quality")
    print("=" * 78)
    fresh = res[(res['status'] == 'ON') & (res['on_streak_age'] <= 2)]
    sustained = res[(res['status'] == 'ON') & (res['on_streak_age'] >= 3)]
    for label, sub in [('FRESH-ON (just flipped)', fresh), ('SUSTAINED-ON', sustained)]:
        if len(sub) == 0:
            print(f"{label}: no days in this bucket")
            continue
        pos_rate = (sub['raw_today'] > 0).mean() * 100
        print(f"{label}: n={len(sub)} days, {pos_rate:.0f}% positive, "
              f"avg raw drift {sub['raw_today'].mean():+.1f} pts/day, "
              f"total {sub['raw_today'].sum():+.1f} pts")

    print("\n" + "=" * 78)
    print("ALL OFF->ON TRANSITION DAYS (the exact moment the flag flips) + the 3 days after")
    print("=" * 78)
    trans_idx = res[(res['transition']) & (res['status'] == 'ON')].index
    for idx in trans_idx:
        window = res.loc[max(0, idx):min(len(res)-1, idx+3)]
        print(f"\n--- Transition at {res.loc[idx, 'date']} (health {res.loc[idx, 'health']:.3f}) ---")
        print(window[['date', 'health', 'status', 'raw_today']].to_string(index=False))

    res.to_csv('/Users/sushil/trading/research_book_health_transition_results.csv', index=False)
    print("\nFull table -> research_book_health_transition_results.csv")


if __name__ == '__main__':
    main()
