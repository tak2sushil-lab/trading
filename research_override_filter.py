# research_override_filter.py — Aug 5 2026
#
# Answers the exact question raised: "A+" is already just score>=80 (grade_setup,
# auto_trader.py) — a flat line, nothing above 80 is distinguished. Does drawing
# a SECOND, higher line within A+ (reusing the score that's already computed,
# no new real-time factors) identify candidates worth trading even on days the
# book-wide gate says OFF? And does that need to be catalyst-only, or does it
# hold for any symbol?
#
# Walk-forward: recompute the SAME trailing book-health status used live for
# every day, then look ONLY at days it was OFF (the days this would actually
# need to fire on) — not just June, the full available history — split by
# score threshold AND by catalyst flag.
#
# Command: venv/bin/python research_override_filter.py

import sqlite3
import pandas as pd

WINDOW_DAYS, MIN_DAYS, MIN_ROWS = 10, 4, 30


def load_data():
    conn = sqlite3.connect('/Users/sushil/trading/trades.db')
    df = pd.read_sql("""
        SELECT scan_date, score, is_catalyst, actual_day_pct - intra_chg AS drift
        FROM scan_log
        WHERE grade='A+' AND direction='LONG' AND enriched=1
          AND actual_day_pct IS NOT NULL AND intra_chg IS NOT NULL
        ORDER BY scan_date
    """, conn)
    conn.close()
    return df


def trailing_health(health_df, as_of_date, days_available):
    prior = [d for d in days_available if d < as_of_date][-WINDOW_DAYS:]
    if len(prior) < MIN_DAYS:
        return None
    rows = health_df[health_df['scan_date'].isin(prior)]
    if len(rows) < MIN_ROWS:
        return None
    return rows['drift'].mean()


def main():
    df = load_data()
    all_days = sorted(df['scan_date'].unique())
    print(f"{len(all_days)} days, {all_days[0]} -> {all_days[-1]}\n")

    off_days = []
    for d in all_days:
        h = trailing_health(df, d, all_days)
        if h is not None and h <= 0:
            off_days.append(d)
    print(f"Book was OFF on {len(off_days)} of {len(all_days)} days (walk-forward, same math as live)\n")

    off = df[df['scan_date'].isin(off_days)].copy()
    print(f"{len(off)} A+ candidates existed on OFF days — these never traded live, this is what an override would touch\n")

    print("=" * 78)
    print("SCORE THRESHOLD ON OFF-DAYS ONLY — does raising the bar within A+ help?")
    print("=" * 78)
    for thresh in (80, 90, 100, 110, 120):
        sub = off[off['score'] >= thresh]
        if len(sub) == 0:
            print(f"score>={thresh:3d}: no candidates ever reached this")
            continue
        pos_rate = (sub['drift'] > 0).mean() * 100
        print(f"score>={thresh:3d}: n={len(sub):4d}  mean_drift={sub['drift'].mean():+6.3f}%  "
              f"%positive={pos_rate:5.1f}%  total={sub['drift'].sum():+8.1f} pts")

    print("\n" + "=" * 78)
    print("CATALYST vs NON-CATALYST, ON OFF-DAYS, AT score>=100")
    print("=" * 78)
    sub = off[off['score'] >= 100]
    for flag, label in [(1, 'catalyst'), (0, 'non-catalyst')]:
        s = sub[sub['is_catalyst'] == flag]
        if len(s) == 0:
            print(f"{label}: n=0")
            continue
        print(f"{label:14}: n={len(s):4d}  mean_drift={s['drift'].mean():+6.3f}%  "
              f"%positive={(s['drift']>0).mean()*100:5.1f}%  total={s['drift'].sum():+8.1f} pts")

    print("\n" + "=" * 78)
    print("REFERENCE: all A+ candidates on OFF days, no score filter at all")
    print("=" * 78)
    print(f"n={len(off)}  mean_drift={off['drift'].mean():+.3f}%  "
          f"%positive={(off['drift']>0).mean()*100:.1f}%  total={off['drift'].sum():+.1f} pts")

    off.to_csv('/Users/sushil/trading/research_override_filter_results.csv', index=False)
    print("\nFull table -> research_override_filter_results.csv")


if __name__ == '__main__':
    main()
