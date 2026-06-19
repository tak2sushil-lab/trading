"""
Gate Audit — decision logger and outcome scorer for all TriVega futures systems.

Every gate that blocks an entry, and every actual entry, is written to
trades.db:gate_blocks. A nightly scorer fills in what price did 15/30/60 min
later. A report shows gate-by-gate accuracy so we know which rules are working.

Usage (production systems):
    from futures.gate_audit import log_block, log_enter
    try:
        log_block('IBKR', 'MNQ', 'LONG', 'RVOL', f'{rvol:.2f}x', price, session)
    except Exception:
        pass   # audit must never affect trading

Usage (standalone):
    venv/bin/python futures/gate_audit.py --score    # score pending outcomes
    venv/bin/python futures/gate_audit.py --report   # print accuracy report
    venv/bin/python futures/gate_audit.py --report --days 30
"""

import os
import sqlite3
import argparse
from datetime import datetime, timedelta, date
from pathlib import Path

import pytz

ET      = pytz.timezone('America/New_York')
_DIR    = Path(__file__).parent
DB_PATH     = str(_DIR.parent / 'trades.db')
MKT_DB_PATH = str(_DIR.parent / 'market_data.db')


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS gate_blocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,          -- ISO timestamp of decision (ET)
    system      TEXT    NOT NULL,          -- IBKR | TC | LONDON | DECODER
    symbol      TEXT    NOT NULL DEFAULT 'MNQ',
    signal      TEXT    NOT NULL,          -- LONG | SHORT
    gate        TEXT    NOT NULL,          -- RVOL | IB_RANGE | OVN_SKIP | HERO |
                                           --   REGIME | GRADE | IB_WINDOW | PM_HOLD |
                                           --   EXT | DIST | STALE | ENTER
    gate_value  TEXT,                      -- human-readable value: "0.12x" / "QUIET" etc.
    price       REAL,
    session     TEXT,                      -- NY_OPEN | MIDDAY | AFTERNOON | LONDON | etc.
    -- outcome columns filled by score_outcomes()
    price_15m   REAL,
    price_30m   REAL,
    price_60m   REAL,
    pts_15m     REAL,                      -- signed: positive = moved in signal direction
    pts_30m     REAL,
    pts_60m     REAL,
    correct_15m INTEGER,                   -- 1 = gate decision was right, 0 = wrong, NULL = pending
    correct_30m INTEGER,
    correct_60m INTEGER,
    scored      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
)
"""

_IDX = """
CREATE INDEX IF NOT EXISTS gate_blocks_ts    ON gate_blocks(ts);
CREATE INDEX IF NOT EXISTS gate_blocks_gate  ON gate_blocks(gate);
CREATE INDEX IF NOT EXISTS gate_blocks_sys   ON gate_blocks(system);
CREATE INDEX IF NOT EXISTS gate_blocks_scored ON gate_blocks(scored);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    """Create gate_blocks table and indexes if not yet present."""
    with _conn() as c:
        c.execute(_DDL)
        for stmt in _IDX.strip().splitlines():
            stmt = stmt.strip()
            if stmt:
                c.execute(stmt)


# ── Writers (called from production systems) ──────────────────────────────────

def log_block(system: str, symbol: str, signal: str,
              gate: str, gate_value, price: float, session: str):
    """
    Log a gate block — a decision where a gate prevented an entry.
    All args are passed through; exceptions are suppressed by the caller.
    """
    init_db()
    ts = datetime.now(ET).isoformat()
    with _conn() as c:
        c.execute(
            """INSERT INTO gate_blocks
               (ts, system, symbol, signal, gate, gate_value, price, session)
               VALUES (?,?,?,?,?,?,?,?)""",
            (ts, system, symbol, signal, gate, str(gate_value), float(price or 0), session)
        )


def log_enter(system: str, symbol: str, signal: str,
              gate_value: str, price: float, session: str):
    """
    Log an actual entry (all gates passed).
    gate='ENTER' distinguishes it from blocks in analysis.
    """
    log_block(system, symbol, signal, 'ENTER', gate_value, price, session)


# ── Outcome scorer (run nightly) ──────────────────────────────────────────────

def score_outcomes():
    """
    Fill in price_15m / price_30m / price_60m for unscored rows.
    Looks up futures_bars_5m in market_data.db for the closest bar at each
    offset after the decision timestamp.

    Call after market close (4:30pm ET+) so all intraday bars are collected.
    """
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT id, ts, signal, gate, price FROM gate_blocks WHERE scored = 0"
        ).fetchall()

    if not rows:
        print("gate_audit: nothing to score")
        return

    mkt = sqlite3.connect(MKT_DB_PATH, timeout=10)
    mkt.row_factory = sqlite3.Row

    updated = 0
    for row in rows:
        try:
            ts_dt = datetime.fromisoformat(row['ts'])
            signal = row['signal']
            entry_price = row['price'] or 0.0

            results = {}
            for mins, col_p, col_pts, col_ok in [
                (15,  'price_15m',  'pts_15m',  'correct_15m'),
                (30,  'price_30m',  'pts_30m',  'correct_30m'),
                (60,  'price_60m',  'pts_60m',  'correct_60m'),
            ]:
                target_dt = ts_dt + timedelta(minutes=mins)
                # Find the closest 5-min bar at or after target time
                bar = mkt.execute(
                    """SELECT close FROM futures_bars_5m
                       WHERE symbol = 'MNQ'
                         AND ts_utc >= ?
                       ORDER BY ts_utc ASC LIMIT 1""",
                    (target_dt.astimezone(pytz.utc).isoformat(),)
                ).fetchone()

                if bar:
                    p_out = float(bar['close'])
                    pts   = (p_out - entry_price) * (1 if signal == 'LONG' else -1)
                    # Block: correct=1 if gate prevented a loser (price moved against signal)
                    # Enter: correct=1 if the entry was a winner (price moved with signal)
                    correct = (1 if pts < 0 else 0) if row['gate'] != 'ENTER' \
                              else (1 if pts > 0 else 0)
                    results[col_p]  = p_out
                    results[col_pts] = round(pts, 2)
                    results[col_ok]  = correct

            if results:
                with _conn() as c:
                    sets        = ', '.join(f'{k}=?' for k in results)
                    all_scored  = len(results) == 9
                    c.execute(
                        f"UPDATE gate_blocks SET {sets}, scored=? WHERE id=?",
                        list(results.values()) + [1 if all_scored else 0, row['id']]
                    )
                updated += 1
        except Exception as e:
            print(f"  score_outcomes: row {row['id']} failed — {e}")

    mkt.close()
    print(f"gate_audit: scored {updated}/{len(rows)} rows")


# ── Report ────────────────────────────────────────────────────────────────────

def gate_report(days: int = 14):
    """
    Print gate-by-gate accuracy report.

    For each gate:
      blocks      — how many times it fired
      accuracy    — % of blocks where gate was correct (blocked a loser)
      avg_pts     — average pts moved in signal direction (negative = gate was right)
      systems     — which systems triggered this gate
    """
    init_db()
    since = (datetime.now(ET) - timedelta(days=days)).isoformat()

    with _conn() as c:
        rows = c.execute(
            """SELECT gate, system, signal,
                      COUNT(*)                        AS n,
                      AVG(correct_30m)                AS acc,
                      AVG(pts_30m)                    AS avg_pts,
                      SUM(CASE WHEN gate='ENTER' THEN 1 ELSE 0 END) AS entries
               FROM gate_blocks
               WHERE ts >= ? AND scored = 1
               GROUP BY gate, system
               ORDER BY gate, system""",
            (since,)
        ).fetchall()

    if not rows:
        print(f"gate_audit: no scored data in last {days} days")
        return

    print(f"\n{'═'*68}")
    print(f"  GATE AUDIT REPORT — last {days} days  (outcome window: 30min)")
    print(f"{'═'*68}")
    print(f"  {'Gate':<14} {'System':<8} {'Sig':<6} {'N':>5} {'Acc%':>7} {'AvgPts':>8}  Assessment")
    print(f"  {'-'*64}")

    for r in rows:
        gate = r['gate']
        if gate == 'ENTER':
            continue
        acc  = (r['acc'] or 0) * 100
        pts  = r['avg_pts'] or 0
        assess = ('✅ GOOD' if acc >= 60 else
                  '⚠️  WEAK' if acc >= 40 else
                  '❌ REMOVE')
        print(f"  {gate:<14} {r['system']:<8} {r['signal']:<6} {r['n']:>5} "
              f"  {acc:>5.0f}%  {pts:>+7.1f}pts  {assess}")

    # Entry outcomes (reference baseline)
    enters = [r for r in rows if r['gate'] == 'ENTER']
    if enters:
        print(f"\n  {'─'*64}")
        print(f"  Reference — actual entries:")
        for r in enters:
            acc = (r['acc'] or 0) * 100
            pts = r['avg_pts'] or 0
            print(f"  {'ENTER':<14} {r['system']:<8} {r['signal']:<6} {r['n']:>5} "
                  f"  {acc:>5.0f}%  {pts:>+7.1f}pts")

    # Unscored count
    with _conn() as c:
        pending = c.execute(
            "SELECT COUNT(*) FROM gate_blocks WHERE scored=0 AND ts >= ?", (since,)
        ).fetchone()[0]
    if pending:
        print(f"\n  ⏳ {pending} decisions still pending outcome scoring")
    print(f"{'═'*68}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Gate audit tool')
    parser.add_argument('--score',  action='store_true', help='Score pending outcomes')
    parser.add_argument('--report', action='store_true', help='Print accuracy report')
    parser.add_argument('--days',   type=int, default=14, help='Report window (default 14)')
    parser.add_argument('--init',   action='store_true', help='Init DB only')
    args = parser.parse_args()

    if args.init or (not args.score and not args.report):
        init_db()
        print(f"gate_audit: DB ready at {DB_PATH}")

    if args.score:
        score_outcomes()

    if args.report:
        gate_report(args.days)
