#!/usr/bin/env python3
"""Read the regime router A/B replay result.  venv/bin/python read_regime_ab.py

Launched Sep 5 2026 to settle one question: does the market-wide regime router earn the
~70% of scan cycles it stands the equity book down for, or should the regime be demoted
to a score modifier (which is what the Alpha Factory decided, and what the code never
became after Fish Finder was retired Aug 15)?

  arm A  _replay_trades_armA.db   current router  (CHOPPY/WEAK/unconfirmed block entries)
  arm B  _replay_trades_armB.db   regime as modifier (only per-trade risk gates remain)

Both arms: equity_replay.py --start 2026-06-01 --end 2026-09-04, identical in every other
respect, run from the router that was re-synced to live in commit 3fd0f42.

READ THE CAVEATS BEFORE QUOTING A NUMBER:
  * The harness has documented stubs -- no catalyst/sympathy flags, empty key_levels and
    sector_strength -- so it UNDER-REPRESENTS the CATALYST_OVERRIDE path that produced
    most recent live trades. Directional evidence, not proof.
  * Day-cluster before believing any difference. Per-trade counts overstate significance
    because trades cluster heavily on the same days. This program has been fooled by that
    twice (see [[equity-premarket-research-sep3]]).
"""
import sqlite3, sys, os
import pandas as pd, numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))

def load(tag):
    p = os.path.join(ROOT, f'_replay_trades_arm{tag}.db')
    if not os.path.exists(p):
        return None
    con = sqlite3.connect(p)
    try:
        return pd.read_sql_query(
            "SELECT entry_date, symbol, side, setup_type, pnl, pnl_pct "
            "FROM trades WHERE setup_type != 'RECONCILED'", con)
    finally:
        con.close()

def summarise(tag, label, d):
    if d is None or d.empty:
        print(f"  {label:26s} no data"); return None
    pnl = d.pnl.fillna(0)
    day = pnl.groupby(d.entry_date).sum()
    t = day.mean() / (day.std() / np.sqrt(len(day))) if len(day) > 2 and day.std() else float('nan')
    print(f"  {label:26s} n={len(d):4d}  P&L ${pnl.sum():+9.2f}  "
          f"WR {100*(pnl>0).mean():4.1f}%  days={len(day):3d}  "
          f"day-t={t:+5.2f}  pos-days {100*(day>0).mean():3.0f}%")
    return day

a, b = load('A'), load('B')
print(f"\n=== REGIME ROUTER A/B — {a.entry_date.min() if a is not None and len(a) else '?'} "
      f"-> {a.entry_date.max() if a is not None and len(a) else '?'} ===\n")
da = summarise('A', 'A: current router', a)
db = summarise('B', 'B: regime as modifier', b)

if da is not None and db is not None:
    j = pd.concat([da.rename('A'), db.rename('B')], axis=1).fillna(0)
    sp = j.B - j.A
    t = sp.mean() / (sp.std() / np.sqrt(len(sp))) if len(sp) > 2 and sp.std() else float('nan')
    print(f"\n  MATCHED-DAY SPREAD (B - A): ${sp.sum():+.2f} total, ${sp.mean():+.2f}/day, "
          f"t={t:+.2f} over {len(sp)} days")
    print(f"  B better on {100*(sp>0).mean():.0f}% of days")
    print("\n  |t| > 2 = a real difference. Below that, the router neither earns nor")
    print("  loses its keep on this evidence, and the honest answer is 'still unproven'.")
    print("\n  Also check concentration before acting:")
    for k in (1, 3, 5):
        print(f"    drop B's best {k} day(s): spread ${sp.drop(sp.nlargest(k).index).sum():+.2f}")
