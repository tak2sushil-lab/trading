"""Score a day-trader replay A/B produced by research_replay_ab.sh.

    venv/bin/python research_replay_score.py TAG BASE_ARM

Reads _replay_trades_{TAG}_{arm}_{A,B}.db for every arm found, prints per-arm totals (trades,
P&L, win rate, hard stops, max drawdown, P&L by month) and each arm vs BASE_ARM on matched
trading days, one observation per day, with a robustness check (result without its best two days).
"""
import glob
import os
import re
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))


def load(tag):
    arms = {}
    for p in sorted(glob.glob(os.path.join(ROOT, f'_replay_trades_{tag}_*_[AB].db'))):
        arm = re.match(rf'_replay_trades_{tag}_(.+)_[AB]\.db', os.path.basename(p)).group(1)
        df = pd.read_sql_query(
            "SELECT entry_date, symbol, setup_type, pnl, pnl_pct, exit_reason FROM trades "
            "WHERE pnl IS NOT NULL AND setup_type != 'RECONCILED'", sqlite3.connect(p))
        arms.setdefault(arm, []).append(df)
    return {a: pd.concat(f) for a, f in arms.items()}


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    tag, base = sys.argv[1], sys.argv[2]
    arms = load(tag)
    if base not in arms:
        sys.exit(f'no replay DBs for base arm {base!r} (found {sorted(arms)})')
    rows, daily = [], {}
    for arm, t in arms.items():
        d = t.groupby('entry_date').pnl.sum()
        daily[arm] = d
        eq = d.sort_index().cumsum()
        row = dict(arm=arm, trades=len(t), pnl=round(t.pnl.sum()), wr=round((t.pnl > 0).mean(), 3),
                   hard_stops=int((t.pnl_pct < -4).sum()), hs_pnl=round(t[t.pnl_pct < -4].pnl.sum()),
                   max_dd=round((eq - eq.cummax()).min()))
        for mon, v in t.groupby(t.entry_date.str[:7]).pnl.sum().items():
            row[mon] = round(v)
        rows.append(row)
    pd.set_option('display.width', 200)
    print(pd.DataFrame(rows).fillna(0).to_string(index=False))
    days = sorted(set().union(*[set(v.index) for v in daily.values()]))
    print(f'\nvs {base}, matched trading days ({len(days)}), one observation per day:')
    for arm in arms:
        if arm == base:
            continue
        diff = daily[arm].reindex(days, fill_value=0) - daily[base].reindex(days, fill_value=0)
        nz = diff[diff != 0]
        t_stat = diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff))) if diff.std() > 0 else float('nan')
        print(f'  {arm:8s} {diff.sum():+8.0f}  days differing {len(nz):3d}  better on {(nz > 0).mean():.0%}  '
              f't={t_stat:+.2f}  without its best 2 days {diff.sort_values().iloc[:-2].sum():+.0f}')


if __name__ == '__main__':
    main()
