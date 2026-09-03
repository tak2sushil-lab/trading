"""
Calibration — measure the MECHANICAL gap between the sim and live so the bench's
5.5-year edge estimate can be haircut to reality.

Doctrine (per the Aug-16 design session): we do NOT calibrate *edge* to a few weeks
of live data. We calibrate two mechanical quantities that are stable and measurable
even from ~140 trades, then apply them to the 5.5-year sim:

  (A) EXECUTION HAIRCUT — on trades the sim AND live both took (same date/side/setup,
      entry within a few minutes), how much worse is live's per-contract P&L?
      Isolates fill/slippage/exit-timing realism from trade-selection differences.

  (B) DAY-AVAILABILITY — the sim trades every eligible day; live stands down (gateway
      outages, regime halts, DLL stops). What fraction of sim days did live actually
      trade?

Matched pairs exclude the user's manual closes (no sim analog) and PM_SHORT (disabled).

Run:  venv/bin/python -m futures.factory.calibrate --start 2026-06-01 --end 2026-08-14
"""
from __future__ import annotations
import argparse
import datetime as _dt
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, 'trades.db')

from futures.factory.bench import run_ny, DOLLARS_PER_PT_MNQ   # noqa: E402


def _hhmm_to_min(s) -> int | None:
    try:
        h, m = str(s)[:5].split(':')[:2]
        return int(h) * 60 + int(m)
    except Exception:
        return None


def load_live(start: str, end: str) -> pd.DataFrame:
    """Live AUTOMATED NY trades, both accounts, excl manual / RECONCILED / partials / PM_SHORT."""
    con = sqlite3.connect(DB)
    q = """
      SELECT account_mode, entry_date AS date, entry_time, side, setup_type AS setup,
             entry_price, exit_price, contracts, pnl, exit_reason
      FROM futures_trades
      WHERE status='CLOSED' AND entry_date>=? AND entry_date<=?
        AND (setup_type IS NULL OR setup_type NOT IN ('RECONCILED','PM_SHORT'))
        AND (notes IS NULL OR notes NOT LIKE 'partial of %')
        AND exit_reason NOT LIKE '%FUT CLOSE%' AND exit_reason NOT LIKE '%Manual%'
        AND exit_reason NOT LIKE '%user-directed%'
    """
    df = pd.read_sql_query(q, con, params=[start, end])
    con.close()
    df['emin'] = df['entry_time'].map(_hhmm_to_min)
    df['pc']   = df['pnl'] / df['contracts'].clip(lower=1)     # per-contract P&L
    return df


def match(sim: pd.DataFrame, live: pd.DataFrame, tol_min: int = 12) -> pd.DataFrame:
    """Join sim↔live on (date, side, setup) with entry within tol_min minutes."""
    sim = sim.copy()
    sim['date'] = sim['date'].astype(str)
    sim['emin'] = sim['entry_time'].map(_hhmm_to_min)
    sim['pc']   = sim['pnl'] / sim['contracts'].clip(lower=1)
    pairs = []
    for _, lv in live.iterrows():
        cand = sim[(sim['date'] == str(lv['date'])) & (sim['side'] == lv['side'])
                   & (sim['setup'] == lv['setup'])]
        if lv['emin'] is not None and len(cand):
            cand = cand.assign(dt=(cand['emin'] - lv['emin']).abs())
            cand = cand[cand['dt'] <= tol_min].sort_values('dt')
        if len(cand):
            s = cand.iloc[0]
            pairs.append(dict(
                date=str(lv['date']), setup=lv['setup'], side=lv['side'],
                acct=lv['account_mode'],
                sim_entry=float(s['entry']), live_entry=float(lv['entry_price']),
                sim_pc=float(s['pc']), live_pc=float(lv['pc']),
                sim_exit_reason=str(s['exit_reason']), live_exit_reason=str(lv['exit_reason'])))
    return pd.DataFrame(pairs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2026-06-01')
    ap.add_argument('--end',   default='2026-08-14')
    args = ap.parse_args()

    print(f'\n Calibration  |  overlap window {args.start} → {args.end}')
    print(' Running sim over the live window…')
    sim = run_ny(args.start, args.end)
    live = load_live(args.start, args.end)

    # ── (B) DAY-AVAILABILITY ─────────────────────────────────────────────────
    sim_days  = set(sim['date'].astype(str)) if len(sim) else set()
    live_days = set(live['date'].astype(str)) if len(live) else set()
    print(f'\n{"="*72}\n  (B) DAY-AVAILABILITY\n{"="*72}')
    print(f'  Sim traded on {len(sim_days)} days; live traded on {len(live_days)} days.')
    both = sim_days & live_days
    only_sim = sim_days - live_days
    avail = len(both) / len(sim_days) if sim_days else 0
    print(f'  Days BOTH traded: {len(both)}   |   sim-only (live stood down): {len(only_sim)}')
    print(f'  →  DAY-AVAILABILITY FACTOR ≈ {avail:.2f}  '
          f'(live is live ~{avail*100:.0f}% of the days the sim assumes)')

    # ── (A) EXECUTION HAIRCUT ────────────────────────────────────────────────
    m = match(sim, live)
    print(f'\n{"="*72}\n  (A) EXECUTION HAIRCUT  (matched same-day/same-setup trades)\n{"="*72}')
    if len(m) < 5:
        print(f'  Only {len(m)} matched pairs — too few for a stable haircut. '
              f'Widen the window.')
    else:
        slip = np.where(m['side'] == 'LONG', m['live_entry'] - m['sim_entry'],
                        m['sim_entry'] - m['live_entry'])   # +ve = live filled worse
        sim_pc, live_pc = m['sim_pc'].mean(), m['live_pc'].mean()
        ratio = (live_pc / sim_pc) if sim_pc != 0 else float('nan')
        print(f'  Matched pairs: {len(m)}')
        print(f'  Mean entry slippage (live vs sim): {slip.mean():+.2f} pts  '
              f'(${slip.mean()*DOLLARS_PER_PT_MNQ:+.2f}/contract)')
        print(f'  Per-contract P&L:  sim ${sim_pc:+.1f}   live ${live_pc:+.1f}   '
              f'→  EXECUTION RATIO {ratio:.2f}')
        print(f'  Median per-contract:  sim ${m["sim_pc"].median():+.1f}   '
              f'live ${m["live_pc"].median():+.1f}')
        # per-account
        for acct, g in m.groupby('acct'):
            if len(g) >= 3:
                print(f'    [{acct}] n={len(g)}  sim ${g["sim_pc"].mean():+.1f}  '
                      f'live ${g["live_pc"].mean():+.1f}')
        print(f'\n  →  Apply to bench:  calibrated ≈ raw × EXEC_RATIO × DAY_AVAIL')
        if not np.isnan(ratio):
            print(f'      ≈ raw × {ratio:.2f} × {avail:.2f} = raw × {ratio*avail:.2f}')
    print()


if __name__ == '__main__':
    main()
