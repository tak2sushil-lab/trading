"""
options/kb_report.py — Options Knowledge Base session brief.

Run: python3 options/kb_report.py
  OR: python3 options/kb_report.py --telegram   (sends to Telegram)
  OR: python3 options/kb_report.py --json        (machine-readable)

Produces a ~1-page catch-up covering:
  1. Open positions + unrealised P&L
  2. Recent closed trades (win rate, avg return)
  3. MC model accuracy (predicted vs actual EV)
  4. Gate analysis — which gates block most trades
  5. Recent calculator runs (last 14 days)
  6. Top conviction names right now
"""

import sys, os, json
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_kb_summary, get_connection, DB_PATH


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pct(val, denom):
    return round(val / denom * 100, 1) if denom else 0.0


def _age(ts_str: str) -> str:
    """'2h ago', '3d ago' from ISO timestamp."""
    try:
        ts = datetime.fromisoformat(ts_str)
        delta = datetime.now() - ts
        h = int(delta.total_seconds() / 3600)
        if h < 24:
            return f"{h}h ago"
        return f"{h // 24}d ago"
    except Exception:
        return ts_str or "?"


def _gate_bar(fail: int, total: int) -> str:
    pct = _pct(fail, total)
    bar = "#" * int(pct / 5)
    return f"{bar:<20} {fail}/{total} ({pct:.0f}%)"


# ── Section builders ──────────────────────────────────────────────────────────

def _section_open(open_pos: list) -> str:
    if not open_pos:
        return "OPEN POSITIONS\n  None\n"

    lines = ["OPEN POSITIONS"]
    for t in open_pos:
        sym     = t['symbol']
        strat   = t['strategy']
        exp     = t['expiry']
        ls, ss  = t.get('long_strike'), t.get('short_strike')
        debit   = t.get('net_debit', 0)
        qty     = t.get('contracts', 1)
        edate   = t.get('entry_date', '?')
        thesis  = (t.get('entry_thesis') or '')[:60]

        strikes = f"${ls:.0f}/${ss:.0f}" if ls and ss else f"${ls or ss or '?'}"
        lines.append(f"  [{t['id']}] {sym} {strat}  {strikes}  exp {exp}")
        lines.append(f"       Debit: ${debit:.2f}/ct × {qty} | Entered: {edate}")
        if thesis:
            lines.append(f"       Thesis: {thesis}")
    return "\n".join(lines) + "\n"


def _section_closed(recent: list) -> str:
    if not recent:
        return "RECENT CLOSED TRADES\n  None\n"

    wins   = sum(1 for t in recent if (t.get('return_pct') or 0) > 0)
    losses = sum(1 for t in recent if (t.get('return_pct') or 0) <= 0)
    avg_r  = round(sum(t.get('return_pct') or 0 for t in recent) / len(recent), 1)

    lines  = [f"RECENT CLOSED TRADES  ({wins}W/{losses}L  avg {avg_r:+.1f}%)"]
    for t in recent[:10]:
        sym  = t['symbol']
        r    = t.get('return_pct') or 0
        rsn  = (t.get('exit_reason') or '?')[:12]
        dxit = t.get('exit_date', '?')
        sign = "+" if r >= 0 else ""
        lsn  = (t.get('lesson') or '')[:55]
        ls   = t.get('long_strike')
        ss   = t.get('short_strike')
        sk   = f"${ls:.0f}/${ss:.0f}" if ls and ss else ""
        lines.append(f"  {sym:<6} {sk:<12} {sign}{r:.1f}%  [{rsn}]  {dxit}")
        if lsn:
            lines.append(f"       Lesson: {lsn}")
    return "\n".join(lines) + "\n"


def _section_model_accuracy(acc: dict, outcomes: list) -> str:
    n = acc.get('n') or 0
    if n == 0:
        return "MC MODEL ACCURACY\n  No closed trades with outcome data yet\n"

    avg_act = acc.get('avg_actual') or 0
    avg_pred = acc.get('avg_predicted') or 0
    avg_err  = acc.get('avg_accuracy') or 0
    wins     = acc.get('wins') or 0
    avg_pwr  = acc.get('avg_predicted_wr') or 0
    actual_wr = _pct(wins, n)

    lines = [
        f"MC MODEL ACCURACY  ({n} trades)",
        f"  Predicted avg EV  : ${avg_pred:+.0f}   Actual avg P&L: ${avg_act:+.0f}",
        f"  Predicted win rate: {avg_pwr:.0f}%   Actual win rate: {actual_wr:.0f}%",
        f"  Avg EV error      : {avg_err:.0f}%",
    ]
    if outcomes:
        lines.append("  Last 5 outcomes:")
        for o in outcomes[:5]:
            pred = o.get('predicted_ev') or 0
            act  = o.get('actual_pnl') or 0
            sym  = o.get('symbol') or '?'
            sign = "+" if act >= 0 else ""
            lines.append(f"    {sym:<6}  pred ${pred:+.0f}  actual {sign}${act:.0f}")
    return "\n".join(lines) + "\n"


def _section_gates(gs: dict) -> str:
    n = gs.get('total_runs') or 0
    if n == 0:
        return "GATE ANALYSIS (30d)\n  No runs yet\n"

    enters = gs.get('enters') or 0
    skips  = gs.get('skips')  or 0

    lines = [
        f"GATE ANALYSIS (30d)  {n} runs → {enters} ENTER, {skips} SKIP",
        f"  Gate 1 Vol       {_gate_bar(gs.get('vol_fail',0), n)}",
        f"  Gate 2 Tech(200MA){_gate_bar(gs.get('tech_fail',0), n)}",
        f"  Gate 3 Conviction {_gate_bar(gs.get('conv_fail',0), n)}",
        f"  Gate 4 Liquidity  {_gate_bar(gs.get('liq_fail',0), n)}",
        f"  Gate 5 Momentum   {_gate_bar(gs.get('mom_fail',0), n)}",
    ]
    return "\n".join(lines) + "\n"


def _section_recent_runs(runs: list) -> str:
    if not runs:
        return "RECENT RUNS (14d)\n  None\n"

    lines = ["RECENT RUNS (14d)"]
    lines.append(f"  {'Date':<12} {'Sym':<6} {'IV%':>4} {'HV30':>5} {'Edge':>5} "
                 f"{'Gtrs':>4} {'Verdict':<12} {'MC EV':>7} {'Action':<8}")
    lines.append("  " + "-" * 75)
    for r in runs[:15]:
        dt    = (r.get('run_at') or '')[:10]
        sym   = (r.get('symbol') or '?')[:6]
        iv    = r.get('iv_pct')
        hv    = r.get('hv30')
        edge  = r.get('edge_pts')
        gates = r.get('gates_pass')
        verd  = (r.get('verdict') or '?')[:12]
        mc    = r.get('mc_ev')
        act   = (r.get('user_action') or 'NONE')[:8]
        lines.append(
            f"  {dt:<12} {sym:<6} "
            f"{iv or 0:>4.0f} {hv or 0:>5.0f} "
            f"{('+' if (edge or 0) >= 0 else '')}{edge or 0:>4.1f} "
            f"{gates or 0:>4}  {verd:<12} "
            f"{'$'+str(int(mc)) if mc is not None else '?':>7}  {act:<8}"
        )
    return "\n".join(lines) + "\n"


def _section_conviction(top: list) -> str:
    if not top:
        return "TOP CONVICTION (BULLISH)\n  None\n"

    lines = ["TOP CONVICTION (BULLISH HIGH/MEDIUM)"]
    lines.append(f"  {'Sym':<6} {'Tier':<7} {'Score':>5} {'Sigs':>5} {'IVR':>5}  {'Age':<8}  Narrative")
    lines.append("  " + "-" * 80)
    for c in top:
        sym  = (c.get('symbol') or '?')[:6]
        tier = (c.get('tier') or '?')[:6]
        sc   = c.get('score') or 0
        sigs = c.get('signals') or 0
        ivr  = c.get('ivr')
        age  = _age(c.get('last_at') or '')
        narr = (c.get('narrative') or '')[:45]
        lines.append(
            f"  {sym:<6} {tier:<7} {sc:>5.2f} {sigs:>5} "
            f"{ivr or 0:>5.0f}  {age:<8}  {narr}"
        )
    return "\n".join(lines) + "\n"


# ── Main report builder ───────────────────────────────────────────────────────

def build_report(kb: dict) -> str:
    # Fetch outcomes for model accuracy detail (last 5)
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute('''
            SELECT ot.symbol, o.predicted_ev, o.actual_pnl
            FROM opt_trade_outcomes o
            JOIN options_trades ot ON ot.id = o.trade_id
            ORDER BY o.recorded_at DESC LIMIT 5
        ''')
        outcomes = [dict(zip(['symbol','predicted_ev','actual_pnl'], r))
                    for r in cur.fetchall()]
        conn.close()
    except Exception:
        outcomes = []

    sep = "=" * 60
    header = (
        f"{sep}\n"
        f"OPTIONS KB SESSION BRIEF — {date.today().isoformat()}\n"
        f"{sep}\n"
    )

    sections = [
        header,
        _section_open(kb.get('open_positions', [])),
        _section_closed(kb.get('recent_closed', [])),
        _section_model_accuracy(kb.get('model_accuracy', {}), outcomes),
        _section_gates(kb.get('gate_stats', {})),
        _section_recent_runs(kb.get('recent_runs', [])),
        _section_conviction(kb.get('top_conviction', [])),
    ]
    return "\n".join(sections)


def build_telegram_brief(kb: dict) -> str:
    """Compact version for Telegram (2-3 messages)."""
    gs  = kb.get('gate_stats', {})
    n   = gs.get('total_runs') or 0
    ent = gs.get('enters') or 0
    skp = gs.get('skips') or 0

    acc   = kb.get('model_accuracy', {})
    n_out = acc.get('n') or 0

    open_pos = kb.get('open_positions', [])
    closed   = kb.get('recent_closed', [])
    top_cv   = kb.get('top_conviction', [])[:5]
    runs     = kb.get('recent_runs', [])[:5]

    lines = [
        f"*OPTIONS KB — {date.today().isoformat()}*",
        "",
        "*OPEN POSITIONS*",
    ]
    if open_pos:
        for t in open_pos:
            ls, ss = t.get('long_strike'), t.get('short_strike')
            sk = f"${ls:.0f}/${ss:.0f}" if ls and ss else ""
            lines.append(f"  {t['symbol']} {sk} exp {t['expiry']} ×{t['contracts']}")
    else:
        lines.append("  None")

    wins_c = sum(1 for t in closed if (t.get('return_pct') or 0) > 0)
    avg_r  = (sum(t.get('return_pct') or 0 for t in closed) / len(closed)) if closed else 0
    lines += [
        "",
        f"*CLOSED* ({len(closed)} trades): {wins_c}W/{len(closed)-wins_c}L  avg {avg_r:+.1f}%",
    ]
    if n_out:
        avg_pred = acc.get('avg_predicted') or 0
        avg_act  = acc.get('avg_actual') or 0
        lines.append(f"*MC accuracy* ({n_out} trades): pred avg ${avg_pred:+.0f}, actual ${avg_act:+.0f}")

    lines += [
        "",
        f"*GATE ANALYSIS (30d)* — {n} runs, {ent} ENTER, {skp} SKIP",
        f"  Vol:{gs.get('vol_fail',0)}  Tech:{gs.get('tech_fail',0)}  "
        f"Conv:{gs.get('conv_fail',0)}  Liq:{gs.get('liq_fail',0)}  "
        f"Mom:{gs.get('mom_fail',0)} failed",
        "",
        "*TOP CONVICTION*",
    ]
    for c in top_cv:
        sym  = c.get('symbol','?')
        tier = c.get('tier','?')
        ivr  = c.get('ivr')
        sigs = c.get('signals',0)
        lines.append(f"  {sym} [{tier}] sigs={sigs} IVR={ivr or '?'}")

    if runs:
        lines += ["", "*RECENT RUNS*"]
        for r in runs:
            dt    = (r.get('run_at') or '')[:10]
            sym   = r.get('symbol','?')
            verd  = r.get('verdict','?')
            mc    = r.get('mc_ev')
            act   = r.get('user_action','NONE')
            lines.append(f"  {dt} {sym} → {verd}  MC ${mc or '?'}  [{act}]")

    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    args = sys.argv[1:]
    kb   = get_kb_summary()

    if '--json' in args:
        print(json.dumps(kb, indent=2, default=str))
    elif '--telegram' in args:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from options_trader import send_telegram
        brief = build_telegram_brief(kb)
        send_telegram(brief)
        print("Sent to Telegram.")
    else:
        print(build_report(kb))
