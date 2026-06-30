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

            # Bidirectional gates (BOTH) block all entries regardless of direction.
            # Directional pts are meaningless — mark scored without filling pts columns.
            if signal == 'BOTH':
                with _conn() as c:
                    c.execute("UPDATE gate_blocks SET scored=1 WHERE id=?", (row['id'],))
                updated += 1
                continue

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

_EPISODE_GAP_MIN = 10   # consecutive same-key rows within this gap = one episode, not N

def _episodes(rows):
    """
    Collapse consecutive rows of the same (system, gate, signal) into episodes.

    Gates that key off a persisting market state (extension, regime, RVOL) get
    re-logged every scan while the state holds — a single 2-hour extended move
    becomes 80+ rows for what is really one event. Treating each row as an
    independent trial inflates N and lets one trending afternoon dominate the
    accuracy stat. An episode is the first row after a gap > _EPISODE_GAP_MIN
    minutes from the previous row of the same key — that first row's outcome
    is what we score; the rest are the same decision repeating.
    """
    by_key = {}
    for r in rows:
        key = (r['system'], r['gate'], r['signal'])
        by_key.setdefault(key, []).append(r)

    out = {}
    for key, rs in by_key.items():
        rs = sorted(rs, key=lambda r: r['ts'])
        eps, cur, last_ts = [], [], None
        for r in rs:
            ts = datetime.fromisoformat(r['ts'])
            if last_ts is not None and (ts - last_ts) > timedelta(minutes=_EPISODE_GAP_MIN):
                eps.append(cur)
                cur = []
            cur.append(r)
            last_ts = ts
        if cur:
            eps.append(cur)
        out[key] = [e[0] for e in eps]   # first row of each episode = the decision
    return out


def gate_report(days: int = 14):
    """
    Print gate-by-gate accuracy report, deduped to one row per episode (see
    _episodes). N is episode count, not raw scan count — raw_n is shown too
    so thin episode counts behind a large raw_n are visible at a glance.

    For each gate:
      accuracy    — % of episodes where gate was correct (blocked a loser)
      avg_pts     — average pts moved in signal direction (negative = gate was right)
    """
    init_db()
    since = (datetime.now(ET) - timedelta(days=days)).isoformat()

    with _conn() as c:
        rows = c.execute(
            """SELECT gate, system, signal, ts, correct_30m, pts_30m
               FROM gate_blocks
               WHERE ts >= ? AND scored = 1 AND gate != 'SHADOW_RAW'
               ORDER BY system, gate, signal, ts""",
            (since,)
        ).fetchall()

    if not rows:
        print(f"gate_audit: no scored data in last {days} days")
        return

    episodes = _episodes(rows)
    raw_n    = {}
    for r in rows:
        key = (r['system'], r['gate'], r['signal'])
        raw_n[key] = raw_n.get(key, 0) + 1

    print(f"\n{'═'*78}")
    print(f"  GATE AUDIT REPORT — last {days} days  (outcome window: 30min, episode-deduped)")
    print(f"{'═'*78}")
    print(f"  {'Gate':<20} {'System':<8} {'Sig':<6} {'N':>4} {'raw_n':>6} {'Acc%':>6} {'AvgPts':>8}  Assessment")
    print(f"  {'-'*74}")

    def _stats(firsts):
        accs = [f['correct_30m'] for f in firsts if f['correct_30m'] is not None]
        pts  = [f['pts_30m']     for f in firsts if f['pts_30m']     is not None]
        acc  = (sum(accs) / len(accs) * 100) if accs else None
        avgp = (sum(pts) / len(pts)) if pts else None
        return acc, avgp

    both_keys = sorted(k for k in episodes if k[2] == 'BOTH' and k[1] != 'ENTER')
    dir_keys  = sorted(k for k in episodes if k[2] != 'BOTH' and k[1] != 'ENTER')

    for key in dir_keys:
        system, gate, signal = key
        firsts = episodes[key]
        n = len(firsts)
        acc, avgp = _stats(firsts)
        assess = ('—  N<5, inconclusive' if n < 5 else
                  '✅ GOOD'   if acc is not None and acc >= 60 else
                  '⚠️  WEAK'   if acc is not None and acc >= 40 else
                  '❌ REMOVE' if acc is not None else '—')
        acc_s  = f'{acc:.0f}%' if acc is not None else 'n/a'
        avgp_s = f'{avgp:+.1f}' if avgp is not None else 'n/a'
        print(f"  {gate:<20} {system:<8} {signal:<6} {n:>4} {raw_n[key]:>6} "
              f"{acc_s:>6} {avgp_s:>8}  {assess}")

    if both_keys:
        print(f"\n  {'─'*74}")
        print(f"  Bidirectional blocks (BOTH — no directional scoring):")
        for key in both_keys:
            system, gate, signal = key
            n = len(episodes[key])
            print(f"  {gate:<20} {system:<8} {'BOTH':<6} {n:>4} {raw_n[key]:>6}  (blocks both sides)")

    enter_keys = sorted(k for k in episodes if k[1] == 'ENTER')
    if enter_keys:
        print(f"\n  {'─'*74}")
        print(f"  Reference — actual entries:")
        for key in enter_keys:
            system, gate, signal = key
            firsts = episodes[key]
            n = len(firsts)
            acc, avgp = _stats(firsts)
            acc_s  = f'{acc:.0f}%' if acc is not None else 'n/a'
            avgp_s = f'{avgp:+.1f}' if avgp is not None else 'n/a'
            print(f"  {'ENTER':<20} {system:<8} {signal:<6} {n:>4} {raw_n[key]:>6} {acc_s:>6} {avgp_s:>8}")

    # Unscored count
    with _conn() as c:
        pending = c.execute(
            "SELECT COUNT(*) FROM gate_blocks WHERE scored=0 AND ts >= ?", (since,)
        ).fetchone()[0]
    if pending:
        print(f"\n  ⏳ {pending} decisions still pending outcome scoring")
    print(f"  Note: N<5 episodes is not enough to act on — treat as a watch item, not a verdict.")


# ── Lead-time report (decoder raw-signal shadow vs actual entries) ────────────

def raw_momentum_signal(closes: list, vwap):
    """
    Decoder's raw 3-bar momentum-vs-VWAP signal, no quality gates applied.
    Mirrors live_rule_sim.detect_signal(): price above VWAP and rising 2 bars,
    or below VWAP and falling 2 bars. This is the earliest possible trigger
    point — used only as a shadow probe, never to place an order.
    """
    if vwap is None or len(closes) < 3:
        return None
    p2, p1, p = closes[-3], closes[-2], closes[-1]
    if p > vwap and p > p1 and p1 > p2:
        return 'LONG'
    if p < vwap and p < p1 and p1 < p2:
        return 'SHORT'
    return None


_shadow_last_signal: dict = {}

def log_shadow_signal(system: str, symbol: str, closes: list,
                       vwap, price: float, session: str):
    """
    Log the raw momentum signal as gate='SHADOW_RAW', but only on transition
    into a new signal (not every scan) — logged for lead-time comparison only,
    never acted on, never blocks or places a trade.
    """
    sig  = raw_momentum_signal(closes, vwap)
    key  = f'{system}:{symbol}'
    prev = _shadow_last_signal.get(key)
    if sig != prev:
        _shadow_last_signal[key] = sig
        if sig:
            try:
                log_block(system, symbol, sig, 'SHADOW_RAW', f'vwap={vwap}', price, session)
            except Exception:
                pass
    return sig


def leadtime_report(days: int = 30):
    """
    For each real ENTER, find the earliest same-day same-direction SHADOW_RAW
    signal from the same system and report how much earlier it appeared and
    the price difference. Answers: would entering on the raw signal have
    caught the move earlier, and at a better price?
    """
    init_db()
    since = (datetime.now(ET) - timedelta(days=days)).isoformat()
    with _conn() as c:
        shadows = c.execute(
            """SELECT system, signal, ts, price FROM gate_blocks
               WHERE gate='SHADOW_RAW' AND ts >= ? ORDER BY ts""", (since,)
        ).fetchall()
        enters = c.execute(
            """SELECT system, signal, ts, price FROM gate_blocks
               WHERE gate='ENTER' AND ts >= ? ORDER BY ts""", (since,)
        ).fetchall()

    print(f"\n{'═'*78}")
    print(f"  EARLY-SIGNAL LEAD-TIME REPORT — last {days} days")
    print(f"{'═'*78}")

    if not shadows:
        print("  No SHADOW_RAW data yet — shadow signal not wired or no signals fired.")
        print(f"{'═'*78}\n")
        return

    matched = []
    for e in enters:
        e_ts  = datetime.fromisoformat(e['ts'])
        e_day = e_ts.date()
        cands = [s for s in shadows
                 if s['system'] == e['system'] and s['signal'] == e['signal']
                 and datetime.fromisoformat(s['ts']).date() == e_day
                 and datetime.fromisoformat(s['ts']) <= e_ts]
        if not cands:
            continue
        first = min(cands, key=lambda s: s['ts'])
        lead_min = (e_ts - datetime.fromisoformat(first['ts'])).total_seconds() / 60
        price_edge = (e['price'] - first['price']) * (1 if e['signal'] == 'LONG' else -1)
        matched.append((e_day, e['system'], e['signal'], lead_min, price_edge))

    if not matched:
        print("  No matched pairs yet (need a real entry on the same day the raw")
        print("  signal also fired in the same direction). Keep collecting.")
    else:
        for d, system, signal, lead_min, price_edge in matched:
            print(f"  {d}  {system:<5} {signal:<5}  lead={lead_min:>5.0f}min  "
                  f"price_edge={price_edge:+.1f}pts")
        avg_lead = sum(m[3] for m in matched) / len(matched)
        avg_edge = sum(m[4] for m in matched) / len(matched)
        print(f"\n  N={len(matched)}  avg_lead={avg_lead:.0f}min  avg_price_edge={avg_edge:+.1f}pts")
        if len(matched) < 5:
            print("  N<5 — directional only, not enough to act on yet.")
    print(f"{'═'*78}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Gate audit tool')
    parser.add_argument('--score',    action='store_true', help='Score pending outcomes')
    parser.add_argument('--report',   action='store_true', help='Print accuracy report')
    parser.add_argument('--leadtime', action='store_true', help='Print early-signal lead-time report')
    parser.add_argument('--days',     type=int, default=14, help='Report window (default 14)')
    parser.add_argument('--init',     action='store_true', help='Init DB only')
    args = parser.parse_args()

    if args.init or (not args.score and not args.report and not args.leadtime):
        init_db()
        print(f"gate_audit: DB ready at {DB_PATH}")

    if args.score:
        score_outcomes()

    if args.report:
        gate_report(args.days)

    if args.leadtime:
        leadtime_report(args.days)
