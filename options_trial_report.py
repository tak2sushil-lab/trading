"""
options_trial_report.py — Sep 20 2026

Scores the paper trial of the corrected options structure.

The trial exists because options is the one book here that cannot be
backtested: there is no historical chain data, so every structure number we
have is Black-Scholes approximation rather than a real quote. This report turns
the resulting live fills into the answers that analysis could not reach.

FOUR QUESTIONS, in the order they can be answered:

  1. DOES IT FILL?            — do delta-anchored (near/ITM) spreads get filled
                                at all, and does the Spread Toll Gate pass them?
  2. WHAT DOES IT REALLY COST? — realized debit vs the mid we calculated on.
  3. IS THE EDGE BUDGET RIGHT? — the graduation test. The gate runs in OBSERVE
                                mode, so it records a PASS/FAIL prediction on
                                every trade without blocking it. If PASS trades
                                beat FAIL trades, flip EDGE_BUDGET_MODE to
                                ENFORCE. If they do not, the gate is wrong and
                                should be removed. Both are real outcomes.
  4. DID OPTIONS BEAT SHARES? — the question the Edge Budget explicitly cannot
                                answer. Needs the underlying's move over the
                                same hold; computed here from bars_5m.

⚠️ Read P&L with the signal in mind. Our A+/A LONG signals measure -0.79%
terminal move at 7d against a universe that did +0.81%, so the SIGNAL is
expected to lose. A negative total is not a verdict on the structure work —
compare structures and gate-buckets against each other, not against zero.

Usage:  venv/bin/python options_trial_report.py [--since YYYY-MM-DD]
"""
import argparse
import os
import sqlite3
import sys
from datetime import date

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trades.db')
TRIAL_START = '2026-09-21'          # first session of the corrected-structure trial


def _rows(conn, sql, *a):
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, a).fetchall()]
    except sqlite3.OperationalError as e:
        print(f"  (query unavailable: {e})")
        return []


def _fmt(v, nd=2, suffix=''):
    return '—' if v is None else f"{v:,.{nd}f}{suffix}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default=TRIAL_START)
    args = ap.parse_args()
    since = args.since
    conn = sqlite3.connect(DB)

    print(f"\n{'='*74}\nOPTIONS PAPER TRIAL — corrected structure, since {since}\n{'='*74}")

    # ── 1. funnel ────────────────────────────────────────────────────────────
    print("\n① FUNNEL — what the calculators decided")
    f = _rows(conn, """SELECT verdict, COUNT(*) n FROM opt_calc_log
                       WHERE run_at >= ? GROUP BY 1 ORDER BY n DESC""", since)
    if not f:
        print("  no calculator runs yet — nothing has scanned since the trial start")
    for r in f:
        print(f"  {str(r['verdict']):14} {r['n']:>5}")

    sk = _rows(conn, """SELECT verdict, skip_reason, COUNT(*) n FROM opt_calc_log
                        WHERE run_at >= ? AND verdict IN ('SKIP','REJECT')
                          AND skip_reason IS NOT NULL
                        GROUP BY 1,2 ORDER BY n DESC LIMIT 14""", since)
    if sk:
        print("\n  why candidates did not trade"
              "  (REJECT = died inside a calculator before a verdict):")
        for r in sk:
            print(f"    {r['n']:>4}  [{r['verdict']:6}] {r['skip_reason'][:78]}")

    # ── 2. structure + fill ──────────────────────────────────────────────────
    print("\n② STRUCTURE — is it picking what we intended, and does it clear the toll gate?")
    st = _rows(conn, """SELECT structure_template tmpl, COUNT(*) n,
                               AVG(be_move_pct) be, AVG(liq_cost_pct) liq,
                               SUM(CASE WHEN verdict='ENTER' THEN 1 ELSE 0 END) entered
                        FROM opt_calc_log WHERE run_at >= ? AND structure_template IS NOT NULL
                        GROUP BY 1 ORDER BY n DESC""", since)
    if not st:
        print("  no structured calc rows yet")
    for r in st:
        print(f"  {str(r['tmpl']):22} n={r['n']:>4}  entered={r['entered']:>3}  "
              f"avg breakeven {_fmt(r['be'],2,'%'):>8}  avg toll {_fmt(r['liq'],1,'%'):>7}")
    print("  (EM-Anchored rows = the delta ladder could not express the structure — "
          "expect these on low-priced names with coarse strikes)")

    # ── 3. the graduation test ───────────────────────────────────────────────
    print("\n③ EDGE BUDGET — the graduation test (gate is in OBSERVE, so it only predicts)")
    eb = _rows(conn, """SELECT c.edge_budget_ok ok, COUNT(*) n,
                               AVG(t.return_pct) avg_ret, SUM(t.return_pct) tot_ret,
                               SUM(CASE WHEN t.return_pct > 0 THEN 1 ELSE 0 END) wins
                        FROM opt_calc_log c JOIN options_trades t ON t.id = c.trade_id
                        WHERE c.run_at >= ? AND t.status='CLOSED' AND c.edge_budget_ok IS NOT NULL
                        GROUP BY 1""", since)
    if not eb:
        print("  no CLOSED trades linked to a calc row yet — this is the number that decides")
        print("  whether the gate lives or dies. Needs ~20-30 closed trades.")
    else:
        for r in eb:
            lbl = 'PASS (budget said yes)' if r['ok'] else 'FAIL (budget said no)'
            print(f"  {lbl:26} n={r['n']:>3}  win {r['wins']}/{r['n']}  "
                  f"avg return {_fmt(r['avg_ret'],1,'%'):>8}")
        if len(eb) == 2:
            a = next((x for x in eb if x['ok']), None)
            b = next((x for x in eb if not x['ok']), None)
            if a and b and a['avg_ret'] is not None and b['avg_ret'] is not None:
                d = a['avg_ret'] - b['avg_ret']
                print(f"\n  >>> PASS minus FAIL: {d:+.1f}pp per trade")
                print(f"  >>> {'gate looks predictive — consider EDGE_BUDGET_MODE=ENFORCE' if d > 0 else 'gate is NOT predictive — do not enforce it; consider removing it'}")
                print(f"  >>> (n={a['n']}+{b['n']}; below ~20-30 closed trades this is noise, not a verdict)")

    # ── 4. options vs shares ─────────────────────────────────────────────────
    print("\n④ DID OPTIONS BEAT SHARES? (the question the Edge Budget cannot answer)")
    tr = _rows(conn, """SELECT symbol, strategy, entry_date, exit_date, underlying_price,
                               return_pct, exit_reason
                        FROM options_trades
                        WHERE status='CLOSED' AND entry_date >= ? ORDER BY entry_date""", since)
    if not tr:
        print("  no closed trades yet")
    else:
        try:
            import pandas as pd
            md = sqlite3.connect(os.path.join(os.path.dirname(DB), 'market_data.db'))
            px = pd.read_sql_query(
                "select symbol, substr(replace(ts_utc,'T',' '),1,10) d, close, "
                "replace(ts_utc,'T',' ') ts from bars_5m where ts_utc >= ?", md, params=(since,))
            daily = px.sort_values('ts').groupby(['symbol', 'd'], as_index=False).last()
            look = {(r.symbol, r.d): r.close for r in daily.itertuples()}
            print(f"  {'sym':6} {'strategy':16} {'opt ret':>9} {'shares ret':>11} {'leverage prem':>14}")
            prem = []
            for t in tr:
                a = look.get((t['symbol'], t['entry_date']))
                b = look.get((t['symbol'], t['exit_date']))
                sr = ((b / a - 1) * 100) if (a and b) else None
                if t['strategy'] in ('BEAR_PUT_SPREAD', 'BEAR_CALL_CREDIT') and sr is not None:
                    sr = -sr
                lp = (t['return_pct'] - sr) if (sr is not None and t['return_pct'] is not None) else None
                if lp is not None:
                    prem.append(lp)
                print(f"  {t['symbol']:6} {str(t['strategy'])[:16]:16} "
                      f"{_fmt(t['return_pct'],1,'%'):>9} {_fmt(sr,1,'%'):>11} {_fmt(lp,1,'pp'):>14}")
            if prem:
                print(f"\n  >>> mean leverage premium: {sum(prem)/len(prem):+.1f}pp over {len(prem)} trades")
                print(f"  >>> {'options ADDED value vs holding the shares' if sum(prem)/len(prem) > 0 else 'options DESTROYED value vs holding the shares — the Turbo result, repeated'}")
        except Exception as e:
            print(f"  (share comparison unavailable: {e})")

    print(f"\n{'='*74}")
    print("Reminder: the SIGNAL feeding this is measured at -0.79% over 7d. Judge the")
    print("structure work on ①②④ and the gate on ③ — not on the P&L total.\n")
    conn.close()


if __name__ == '__main__':
    main()
