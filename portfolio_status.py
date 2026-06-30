"""
portfolio_status.py — Unified cross-vertical portfolio summary.

Called from any Telegram bot when user sends "STATUS ALL".
Reads trades.db directly — no bridge calls needed.

Usage:
    from portfolio_status import format_all
    send_telegram(format_all())

    # standalone test:
    python portfolio_status.py
"""

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytz

_DIR   = Path(__file__).parent
DB     = _DIR / 'trades.db'
ET     = pytz.timezone('America/New_York')

TC_STATE_FILE   = _DIR / 'futures' / 'prop_state.json'
IBKR_STATE_FILE = _DIR / 'futures' / 'ibkr_state.json'

OPTIONS_TOTAL_CAPITAL = 5_000.0   # $4K spreads + $1K scalps


# ── helpers ───────────────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def _d(v: float) -> str:
    """Format dollar amount with sign: +$1,234 or -$1,234"""
    return f"+${v:,.0f}" if v >= 0 else f"-${abs(v):,.0f}"


# ── Equity section ────────────────────────────────────────────────────────────

def _equity_section() -> str:
    today = str(date.today())
    conn  = _conn()

    closed = conn.execute(
        "SELECT pnl, status FROM trades "
        "WHERE entry_date=? AND status IN ('WIN','LOSS') AND setup_type!='RECONCILED'",
        (today,)
    ).fetchall()

    open_t = conn.execute(
        "SELECT symbol, side, entry_price, shares FROM trades "
        "WHERE status='OPEN' AND setup_type!='RECONCILED' ORDER BY symbol",
    ).fetchall()

    wr30_rows = conn.execute(
        "SELECT status FROM trades "
        "WHERE entry_date >= date('now','-30 days') "
        "AND status IN ('WIN','LOSS') AND setup_type!='RECONCILED'"
    ).fetchall()

    conn.close()

    realized = sum(r['pnl'] or 0 for r in closed)
    n        = len(closed)
    wins     = sum(1 for r in closed if r['status'] == 'WIN')
    wr30     = (sum(1 for r in wr30_rows if r['status'] == 'WIN') / max(len(wr30_rows), 1)) * 100
    n_open   = len(open_t)

    line1 = (f"📈 EQUITY  {_d(realized)} today · "
             f"{n}t {wins}W/{n-wins}L · WR{wr30:.0f}% · {n_open} open")
    lines = [line1]
    for t in open_t:
        side = '(S)' if t['side'] == 'SHORT' else ''
        lines.append(f"   • {t['symbol']}{side} ×{t['shares']} @ ${t['entry_price']:.2f}")
    return '\n'.join(lines)


# ── Options section ───────────────────────────────────────────────────────────

def _options_section() -> str:
    try:
        conn = _conn()

        open_t = conn.execute(
            "SELECT symbol, strategy, entry_grade, premium_paid, stop_value, expiry "
            "FROM options_trades WHERE status='OPEN'"
        ).fetchall()

        closed_count = conn.execute(
            "SELECT COUNT(*) FROM options_trades WHERE status='CLOSED'"
        ).fetchone()[0]

        net_pnl = conn.execute(
            "SELECT COALESCE(SUM(exit_value - premium_paid), 0) "
            "FROM options_trades WHERE status='CLOSED'"
        ).fetchone()[0] or 0.0

        conn.close()

        deployed  = sum(t['premium_paid'] or 0 for t in open_t)
        available = OPTIONS_TOTAL_CAPITAL - deployed
        n_open    = len(open_t)

        cap_k = int(OPTIONS_TOTAL_CAPITAL / 1000)
        line1 = (f"⚡ OPTIONS  ${deployed:,.0f}/${cap_k}K deployed · "
                 f"Net {_d(net_pnl)} · {n_open} open / {closed_count} closed")
        lines = [line1]
        for t in open_t:
            prem = t['premium_paid'] or 0
            lines.append(
                f"   • {t['symbol']} [{t['strategy']}] {t['entry_grade'] or '?'}"
                f"  ${prem:.0f}  exp {t['expiry']}"
            )
        return '\n'.join(lines)

    except Exception as exc:
        return f"⚡ OPTIONS  (unavailable: {exc})"


# ── Futures section ───────────────────────────────────────────────────────────

def _futures_section(state_file: Path, label: str) -> str:
    emoji  = '🔷' if label == 'TC' else '💎'
    header = f"{emoji} FUTURES {label}"
    try:
        if not state_file.exists():
            return f"{header}  Not started yet."

        with open(state_file) as f:
            s = json.load(f)

        balance = s.get('balance', 0)
        session = s.get('session_pnl', 0)
        total   = s.get('total_profit', 0)
        mode    = s.get('mode', label)

        today = str(date.today())
        conn  = _conn()
        trades = conn.execute(
            "SELECT pnl, status FROM futures_trades "
            "WHERE entry_date=? AND account_mode=? AND status!='ORPHANED' "
            "AND setup_type != 'RECONCILED'",
            (today, label)
        ).fetchall()
        open_futures = conn.execute(
            "SELECT symbol, side, entry_price, contracts FROM futures_trades "
            "WHERE entry_date=? AND account_mode=? AND status='OPEN'",
            (today, label)
        ).fetchall()
        conn.close()

        n      = len(trades)
        wins   = sum(1 for t in trades if t['status'] == 'CLOSED' and (t['pnl'] or 0) > 0)
        n_open = len(open_futures)

        line1 = (f"{header}  Bal ${balance:,.0f} · Day {_d(session)} · "
                 f"All {_d(total)} · {n}t({wins}W) · {n_open} open")
        lines = [line1]

        if mode == 'TC':
            hwm   = s.get('high_water_mark', balance)
            buf   = balance - (hwm - 3000)
            left  = 3000.0 - total
            lines.append(f"   Cap ${left:,.0f} left · MLL buf ${buf:,.0f}")

        for t in open_futures:
            lines.append(f"   • MNQ {t['side']} ×{t['contracts']} @ {t['entry_price']:.2f}")

        return '\n'.join(lines)

    except Exception as exc:
        return f"{header}  (unavailable: {exc})"


# ── Combined ──────────────────────────────────────────────────────────────────

def format_all() -> str:
    now = datetime.now(ET).strftime('%H:%M ET')
    parts = [
        f"📊 TriVega · {now}",
        _equity_section(),
        _options_section(),
        _futures_section(TC_STATE_FILE, 'TC'),
        _futures_section(IBKR_STATE_FILE, 'IBKR'),
    ]
    return '\n'.join(parts)


if __name__ == '__main__':
    print(format_all())
