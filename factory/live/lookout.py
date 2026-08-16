"""
The Lookout — the crow's-nest. Watches live (or shadow) engine results against what the
backtest promised, and sounds two alarms:
  DRIFT  — live per-trade edge is materially below the backtest's expectation (band = the
           backtest's own scatter). "The map said +0.5%; we're sailing −0.2%."
  DECAY  — the most recent live window has gone negative while the backtest was positive.
           "This edge is rotting; consider retiring the engine."

Reads the live trade tables (wave_trades for Wave Rider), compares to the factory backtest.
Safe to run anytime — reports "awaiting live data" until trades exist. Reads only; changes nothing.
Run:  venv/bin/python -m factory.live.lookout
"""
from __future__ import annotations
import sqlite3
import numpy as np
import pandas as pd

from factory import data as D, engines as E

DB = "/Users/sushil/trading/trades.db"
DRIFT_BAND_Z = -2.0      # live mean this many std-errors below backtest → DRIFT alarm
DECAY_WINDOW = 20        # most-recent N live trades for the decay check


def _live_trades(table: str) -> pd.DataFrame:
    try:
        c = sqlite3.connect(DB)
        df = pd.read_sql_query(
            f"SELECT symbol, entry_date, pnl_pct, mode FROM {table} WHERE status='CLOSED'", c)
        c.close()
        return df
    except Exception:
        return pd.DataFrame(columns=["symbol", "entry_date", "pnl_pct", "mode"])


def watch(engine_name: str, live_table: str) -> dict:
    ev, td, _ = D.build_dataset()
    bt = E.get_engine(engine_name).run(ev, td)
    exp_mean = float(bt["ret"].mean())
    exp_std = float(bt["ret"].std())
    exp_win = float((bt["ret"] > 0).mean() * 100)

    live = _live_trades(live_table)
    out = {"engine": engine_name, "exp_mean": exp_mean, "exp_win": exp_win, "n_live": len(live)}
    print(f"\n╔═ LOOKOUT ─ {engine_name} " + "═" * 20)
    print(f"  Backtest expectation: {exp_mean:+.3f}%/trade, win {exp_win:.0f}%  "
          f"(inflated by survivorship — a ceiling, not a promise)")
    if len(live) == 0:
        print("  Live/shadow: none yet — awaiting first trades (shadow starts next session).")
        print("╚" + "═" * 45)
        out["status"] = "AWAITING_DATA"
        return out

    live_mean = float(live["pnl_pct"].mean())
    live_win = float((live["pnl_pct"] > 0).mean() * 100)
    se = exp_std / np.sqrt(len(live)) if len(live) else float("inf")
    z = (live_mean - exp_mean) / se if se > 0 else 0.0
    modes = live["mode"].value_counts().to_dict()
    print(f"  Live ({modes}): {live_mean:+.3f}%/trade, win {live_win:.0f}%, n={len(live)}")
    print(f"  Deviation from backtest: z = {z:+.1f}")

    drift = z <= DRIFT_BAND_Z
    recent = live.sort_values("entry_date").tail(DECAY_WINDOW)
    decay = len(recent) >= DECAY_WINDOW and recent["pnl_pct"].mean() < 0 < exp_mean
    if drift:
        print(f"  ⚠ DRIFT ALARM — live edge {DRIFT_BAND_Z:.0f}σ below backtest. Investigate.")
    if decay:
        print(f"  ⚠ DECAY ALARM — last {DECAY_WINDOW} trades net-negative while backtest is positive. Consider retiring.")
    if not (drift or decay):
        print("  ✅ Tracking within band — no alarm.")
    print("╚" + "═" * 45)
    out.update(status="ALARM" if (drift or decay) else "OK",
               live_mean=live_mean, live_win=live_win, z=z, drift=drift, decay=decay)
    return out


def main():
    # Currently one live engine (Wave Rider → wave_trades). Add rows as engines go live.
    watch("momentum_wild", "wave_trades")


if __name__ == "__main__":
    main()
