"""
futures_learner.py — Nightly learning engine for MNQ futures

Replaces equity learner's sector/RSI/volume analysis with:
  - Session performance  (NY_OPEN / MIDDAY / AFTERNOON)
  - Setup type          (ORB_LONG / VWAP_LONG / MOMENTUM_LONG etc.)
  - Day of week         (Mon–Fri edge)
  - Time of day         (which hours are best)
  - Regime alignment    (how often STRONG → WIN, WEAK → SHORT WIN)

Runs nightly via APScheduler in futures_trader.py.
Min 20 trades to run — too few trades = noise, not signal.
"""

import os
import sys
import sqlite3
from datetime import datetime, date, timedelta
import pytz

# ── root path ─────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

import requests

ET       = pytz.timezone('America/New_York')
_DIR     = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(_DIR, '..', 'trades.db')

FUTURES_TELEGRAM_TOKEN   = os.getenv('FUTURES_TELEGRAM_TOKEN')
FUTURES_TELEGRAM_CHAT_ID = os.getenv('FUTURES_TELEGRAM_CHAT_ID')

MIN_TRADES = 20   # minimum trades before learning runs


# ── DB helpers ────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _query(sql, params=()):
    conn = _conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _count_closed() -> int:
    conn = _conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM futures_trades WHERE status='CLOSED'"
    ).fetchone()[0]
    conn.close()
    return n


# ── Analysis functions ────────────────────────────────────

def analyse_session_performance() -> list:
    """Which session (NY_OPEN / MIDDAY / AFTERNOON) has best WR?"""
    return _query('''
        SELECT session,
               COUNT(*) as total,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
               AVG(pnl)        as avg_pnl,
               AVG(pnl_ticks)  as avg_ticks
        FROM futures_trades
        WHERE status='CLOSED' AND session IS NOT NULL AND setup_type != 'RECONCILED'
        GROUP BY session
        HAVING total >= 3
        ORDER BY wins * 1.0 / total DESC
    ''')


def analyse_setup_performance() -> list:
    """Which setups (ORB_LONG, VWAP_LONG etc.) have best WR?"""
    return _query('''
        SELECT setup_type,
               COUNT(*) as total,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
               AVG(pnl)       as avg_pnl,
               AVG(pnl_ticks) as avg_ticks
        FROM futures_trades
        WHERE status='CLOSED' AND setup_type IS NOT NULL AND setup_type != 'RECONCILED'
        GROUP BY setup_type
        HAVING total >= 3
        ORDER BY wins * 1.0 / total DESC
    ''')


def analyse_day_of_week() -> list:
    """Which days of the week are most profitable?"""
    return _query('''
        SELECT
            CASE CAST(strftime('%w', exit_date) AS INTEGER)
                WHEN 1 THEN 'Monday'
                WHEN 2 THEN 'Tuesday'
                WHEN 3 THEN 'Wednesday'
                WHEN 4 THEN 'Thursday'
                WHEN 5 THEN 'Friday'
                ELSE 'Weekend'
            END as day_name,
            COUNT(*) as total,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
            AVG(pnl) as avg_pnl
        FROM futures_trades
        WHERE status='CLOSED' AND setup_type != 'RECONCILED'
        GROUP BY day_name
        HAVING total >= 3
        ORDER BY wins * 1.0 / total DESC
    ''')


def analyse_time_of_day() -> list:
    """Which entry hours are most profitable?"""
    return _query('''
        SELECT
            CASE
                WHEN entry_time < '10:00' THEN '09:30-10:00 open'
                WHEN entry_time < '10:30' THEN '10:00-10:30 NY open'
                WHEN entry_time < '11:30' THEN '10:30-11:30 mid morning'
                WHEN entry_time < '12:00' THEN '11:30-12:00 pre-lunch'
                WHEN entry_time < '13:00' THEN '12:00-13:00 lunch'
                WHEN entry_time < '14:00' THEN '13:00-14:00 early afternoon'
                ELSE '14:00+ late afternoon'
            END as time_bucket,
            COUNT(*) as total,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
            AVG(pnl) as avg_pnl
        FROM futures_trades
        WHERE status='CLOSED' AND setup_type != 'RECONCILED'
        GROUP BY time_bucket
        HAVING total >= 3
        ORDER BY wins * 1.0 / total DESC
    ''')


def analyse_hold_time() -> list:
    """How long do winning vs losing trades last?"""
    return _query('''
        SELECT
            CASE WHEN pnl > 0 THEN 'WIN' ELSE 'LOSS' END as outcome,
            COUNT(*) as total,
            AVG(
                (strftime('%s', exit_date || ' ' || exit_time) -
                 strftime('%s', entry_date || ' ' || entry_time)) / 60.0
            ) as avg_hold_minutes,
            AVG(pnl) as avg_pnl
        FROM futures_trades
        WHERE status='CLOSED' AND setup_type != 'RECONCILED'
          AND exit_date IS NOT NULL AND exit_time IS NOT NULL
        GROUP BY outcome
    ''')


def analyse_long_vs_short() -> list:
    """Long vs Short edge comparison."""
    return _query('''
        SELECT side,
               COUNT(*) as total,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
               AVG(pnl)       as avg_pnl,
               AVG(pnl_ticks) as avg_ticks
        FROM futures_trades
        WHERE status='CLOSED' AND setup_type != 'RECONCILED'
        GROUP BY side
    ''')


def calculate_session_scores(session_data: list) -> dict:
    """
    Convert session WR data into score adjustments for futures_trader.py.
    Returns dict: session → score_bonus (positive or negative int)
    """
    if not session_data:
        return {}

    # Find overall average WR
    total_t = sum(r['total'] for r in session_data)
    total_w = sum(r['wins']  for r in session_data)
    avg_wr  = total_w / total_t if total_t > 0 else 0.5

    scores = {}
    for row in session_data:
        if row['total'] < 3:
            continue
        wr   = row['wins'] / row['total']
        diff = wr - avg_wr
        # Scale: +/-15pts per 10% WR difference from average
        bonus = int(round(diff * 150))
        bonus = max(-15, min(+15, bonus))   # cap at ±15
        scores[row['session']] = bonus

    return scores


# ── Main learning cycle ───────────────────────────────────

def run_futures_learning_cycle():
    print(f"\n{'='*55}")
    print(f"🧠 FUTURES Learning Cycle — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")

    n = _count_closed()
    print(f"Closed futures trades: {n}")

    if n < MIN_TRADES:
        print(f"Need {MIN_TRADES} trades to learn (have {n}). Collecting data...")
        return

    # ── Session performance ───────────────────────────────
    print("\n--- Session Performance ---")
    session_data = analyse_session_performance()
    for r in session_data:
        wr = r['wins'] / r['total'] * 100 if r['total'] > 0 else 0
        print(f"  {r['session']:<15} {wr:.0f}% WR | "
              f"{r['total']} trades | avg ${r['avg_pnl']:.2f} | "
              f"{r['avg_ticks']:.1f} ticks")

    session_scores = calculate_session_scores(session_data)
    if session_scores:
        print(f"  Score adjustments: {session_scores}")

    # ── Setup performance ─────────────────────────────────
    print("\n--- Setup Performance ---")
    setup_data = analyse_setup_performance()
    for r in setup_data:
        wr = r['wins'] / r['total'] * 100 if r['total'] > 0 else 0
        print(f"  {r['setup_type']:<20} {wr:.0f}% WR | "
              f"{r['total']} trades | avg ${r['avg_pnl']:.2f}")

    # Identify best and worst setups
    if setup_data:
        best  = max(setup_data, key=lambda x: x['wins']/x['total'] if x['total'] > 0 else 0)
        worst = min(setup_data, key=lambda x: x['wins']/x['total'] if x['total'] > 0 else 0)
        print(f"\n  Best setup:  {best['setup_type']} "
              f"({best['wins']/best['total']*100:.0f}% WR)")
        print(f"  Worst setup: {worst['setup_type']} "
              f"({worst['wins']/worst['total']*100:.0f}% WR)")

    # ── Day of week ───────────────────────────────────────
    print("\n--- Day of Week ---")
    dow_data = analyse_day_of_week()
    for r in dow_data:
        wr = r['wins'] / r['total'] * 100 if r['total'] > 0 else 0
        print(f"  {r['day_name']:<12} {wr:.0f}% WR | "
              f"{r['total']} trades | avg ${r['avg_pnl']:.2f}")

    # ── Time of day ───────────────────────────────────────
    print("\n--- Time of Day ---")
    tod_data = analyse_time_of_day()
    for r in tod_data:
        wr = r['wins'] / r['total'] * 100 if r['total'] > 0 else 0
        print(f"  {r['time_bucket']:<30} {wr:.0f}% WR | "
              f"{r['total']} trades | avg ${r['avg_pnl']:.2f}")

    # ── Hold time ─────────────────────────────────────────
    print("\n--- Hold Time (WIN vs LOSS) ---")
    hold_data = analyse_hold_time()
    for r in hold_data:
        print(f"  {r['outcome']:<6} avg hold: {r['avg_hold_minutes']:.0f} min | "
              f"avg P&L: ${r['avg_pnl']:.2f}")

    # ── Long vs Short ─────────────────────────────────────
    print("\n--- Long vs Short ---")
    ls_data = analyse_long_vs_short()
    for r in ls_data:
        wr = r['wins'] / r['total'] * 100 if r['total'] > 0 else 0
        print(f"  {r['side']:<6} {wr:.0f}% WR | "
              f"{r['total']} trades | avg ${r['avg_pnl']:.2f} | "
              f"{r['avg_ticks']:.1f} ticks")

    # ── Overall stats ─────────────────────────────────────
    all_closed = _query('''
        SELECT COUNT(*) as total,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
               SUM(pnl) as total_pnl,
               AVG(pnl_ticks) as avg_ticks
        FROM futures_trades WHERE status='CLOSED'
    ''')
    if all_closed:
        s  = all_closed[0]
        wr = s['wins'] / s['total'] * 100 if s['total'] > 0 else 0
        print(f"\n{'='*55}")
        print(f"Overall: {wr:.1f}% WR | {s['total']} trades | "
              f"Total P&L: ${s['total_pnl']:.2f} | "
              f"Avg: {s['avg_ticks']:.1f} ticks")
        print(f"{'='*55}")

    # ── Telegram summary ──────────────────────────────────
    _send_learning_summary(session_data, setup_data, ls_data)

    print("\n✅ Futures learning cycle complete.")


def _send_learning_summary(session_data, setup_data, ls_data):
    """Send nightly Telegram summary."""
    if not FUTURES_TELEGRAM_TOKEN or not FUTURES_TELEGRAM_CHAT_ID:
        return

    lines = [f"🧠 FUTURES NIGHTLY LEARN — {date.today()}"]

    if session_data:
        best_s = max(session_data, key=lambda x: x['wins']/x['total'] if x['total'] > 0 else 0)
        wr     = best_s['wins'] / best_s['total'] * 100
        lines.append(f"Best session: {best_s['session']} ({wr:.0f}% WR)")

    if setup_data:
        best_set = max(setup_data, key=lambda x: x['wins']/x['total'] if x['total'] > 0 else 0)
        wr       = best_set['wins'] / best_set['total'] * 100
        lines.append(f"Best setup: {best_set['setup_type']} ({wr:.0f}% WR)")

    if ls_data:
        for r in ls_data:
            wr = r['wins'] / r['total'] * 100 if r['total'] > 0 else 0
            lines.append(f"{r['side']}: {wr:.0f}% WR ({r['total']} trades)")

    try:
        requests.post(
            f"https://api.telegram.org/bot{FUTURES_TELEGRAM_TOKEN}/sendMessage",
            json={'chat_id': FUTURES_TELEGRAM_CHAT_ID, 'text': '\n'.join(lines)},
            timeout=5,
        )
    except Exception:
        pass


if __name__ == '__main__':
    run_futures_learning_cycle()
