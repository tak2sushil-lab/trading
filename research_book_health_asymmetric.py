# research_book_health_asymmetric.py — Aug 4 2026
#
# Direct test of an asymmetric confirmation rule targeting the transition
# risk the user identified: require 2 CONSECUTIVE positive trailing-health
# readings before flipping OFF->ON (slow to trust green), but flip instantly
# back to OFF on a single negative reading (fast to cut). Compares against
# plain binary (flips on a single day either direction).
#
# Command: venv/bin/python research_book_health_asymmetric.py
import sqlite3
import pandas as pd

WINDOW_DAYS, MIN_DAYS, MIN_ROWS = 10, 4, 30

def load_data():
    conn = sqlite3.connect('/Users/sushil/trading/trades.db')
    df = pd.read_sql("""SELECT scan_date, actual_day_pct - intra_chg AS drift FROM scan_log
        WHERE grade='A+' AND direction='LONG' AND enriched=1
          AND actual_day_pct IS NOT NULL AND intra_chg IS NOT NULL ORDER BY scan_date""", conn)
    conn.close()
    return df

def trailing_health(df, as_of_date, days_available):
    prior = [d for d in days_available if d < as_of_date][-WINDOW_DAYS:]
    if len(prior) < MIN_DAYS: return None
    rows = df[df['scan_date'].isin(prior)]
    if len(rows) < MIN_ROWS: return None
    return rows['drift'].mean()

df = load_data()
all_days = sorted(df['scan_date'].unique())

binary_total = asym_total = 0.0
positive_streak = 0
asym_status = 'ON'  # cold start default
rows = []
for d in all_days:
    health = trailing_health(df, d, all_days)
    today = df[df['scan_date'] == d]
    raw = today['drift'].sum()

    binary_on = (health is None or health > 0)

    if health is None:
        asym_status = 'ON'
    elif health > 0:
        positive_streak += 1
        if asym_status == 'OFF' and positive_streak >= 2:
            asym_status = 'ON'
        elif asym_status == 'OFF':
            pass  # only 1 positive day so far — stay off, wait for confirmation
    else:
        positive_streak = 0
        asym_status = 'OFF'  # instant cut on any negative reading

    binary_total += raw * (1.0 if binary_on else 0.0)
    asym_total += raw * (1.0 if asym_status == 'ON' else 0.0)
    rows.append({'date': d, 'health': health, 'binary_on': binary_on,
                'asym_status': asym_status, 'raw': raw})

print(f"BINARY total:              {binary_total:+8.1f} pts")
print(f"ASYMMETRIC (2-day confirm): {asym_total:+8.1f} pts")
print()
res = pd.DataFrame(rows)
print(res.to_string(index=False))
res.to_csv('/Users/sushil/trading/research_book_health_asymmetric_results.csv', index=False)
