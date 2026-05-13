"""
options/learner_options.py — Nightly options learning cycle.

Runs independently from the equity learner (learner.py).
Responsibilities:
  1. fill_whatif_prices() — backfill 7d/14d prices + estimated P&L for
     SKIPPED/EXPIRED suggestions so what-if analysis works in OPT KB.
  2. (Future) Gate accuracy analysis — which gate patterns predict outcomes.
  3. (Future) MC EV calibration — compare predicted vs actual win rate.

Scheduled: same launchd job as equity learner, or add a separate plist.
Run manually: python3 options/learner_options.py
"""

import sys, os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import fill_whatif_prices, get_connection


def analyse_suggestion_decisions() -> None:
    """Print a summary of auto-suggest decisions so far."""
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute('''
        SELECT
            user_decision,
            COUNT(*)                                           as n,
            ROUND(AVG(mc_ev_dollar), 0)                       as avg_pred_ev,
            ROUND(AVG(whatif_pnl),   0)                       as avg_actual_pnl,
            SUM(CASE WHEN whatif_pnl > 0 THEN 1 ELSE 0 END)   as whatif_wins
        FROM opt_suggestions
        WHERE user_decision IS NOT NULL
        GROUP BY user_decision
        ORDER BY n DESC
    ''')
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("  No resolved suggestions yet.")
        return

    print(f"  {'Decision':<12} {'N':>4} {'Pred EV':>9} {'Actual PnL':>11} {'WhatIf W':>9}")
    print("  " + "-" * 50)
    for r in rows:
        decision, n, pred_ev, actual_pnl, wins = r
        pred_s   = f"${pred_ev:+.0f}"   if pred_ev   is not None else "n/a"
        actual_s = f"${actual_pnl:+.0f}" if actual_pnl is not None else "pending"
        wins_s   = f"{wins}"             if wins       is not None else "-"
        print(f"  {decision:<12} {n:>4} {pred_s:>9} {actual_s:>11} {wins_s:>9}")


def run_options_learning_cycle() -> None:
    print(f"\n{'=' * 55}")
    print(f"Options Learning Cycle — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'=' * 55}")

    print("\n--- What-If Price Fill ---")
    try:
        fill_whatif_prices()
        print("  Done.")
    except Exception as e:
        print(f"  Error: {e}")

    print("\n--- Suggestion Decision Log ---")
    try:
        analyse_suggestion_decisions()
    except Exception as e:
        print(f"  Error: {e}")

    print("\nOptions learner complete.")


if __name__ == '__main__':
    run_options_learning_cycle()
