#!/usr/bin/env python
"""backfill_overnight_reference.py — separate Clockwork's STRATEGY from its EXECUTION.

WHY THIS EXISTS. Clockwork's live P&L is not a measurement of its strategy, because the
IBKR PAPER account does not simulate the opening auction faithfully. Measured on Sep 18 2026:
of ten MOO sells filled at 09:30:01, four printed at whole-dollar prices and THREE of those
were outside the stock's actual traded range for that bar --

    PI    filled 179.00   the 09:30 bar was open = high = low = 182.29
    ACLS  filled 105.00   bar low 105.61
    VST   filled 143.00   bar low 143.20

The stock never traded there. Our order path is correct (MKT + tif='OPG', submitted before the
09:28 cutoff, filled at 09:30:01) -- the simulator is inventing the price. So paper fills must
never be read as evidence about the strategy, and the strategy's own prints must never be read
as evidence about execution. This records BOTH, every night, so the two can never be confused.

    ref_entry   the session's official closing print (last 5-min bar close)
    ref_exit    the next session's official opening print (first 5-min bar open)
    ref_pnl     what the strategy earned at those prints, net of the modelled fee
    exec_drag   ref_pnl - actual pnl, i.e. everything execution cost or gave

Run after the 5-min collector has written the session (it fires ~16:30 ET):
    venv/bin/python backfill_overnight_reference.py
"""
from __future__ import annotations
import os, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
from database import equity_commission

ROOT = os.path.dirname(os.path.abspath(__file__))
TRADES, MARKET = os.path.join(ROOT, 'trades.db'), os.path.join(ROOT, 'market_data.db')
COLS = [('ref_entry', 'REAL'), ('ref_exit', 'REAL'), ('ref_pnl', 'REAL'), ('exec_drag', 'REAL')]


def ensure_cols(con):
    for c, t in COLS:
        try:
            con.execute(f'ALTER TABLE overnight_trades ADD COLUMN {c} {t}')
        except Exception:
            pass
    con.commit()


def prints(dates):
    """Official close and open per symbol-session, from our own 5-min bars."""
    con = sqlite3.connect(MARKET)
    lo = min(dates)
    b = pd.read_sql_query(
        "SELECT symbol, ts_utc, open, close FROM bars_5m WHERE ts_utc >= ?", con, params=(lo,))
    con.close()
    ts = pd.to_datetime(b.ts_utc, utc=True, format='mixed').dt.tz_convert('America/New_York')
    b['d'] = ts.dt.date.astype(str)
    b['t'] = ts.dt.strftime('%H:%M')
    op = b[b.t == '09:30'].groupby(['symbol', 'd'])['open'].first().rename('o').reset_index()
    cl = b.sort_values('t').groupby(['symbol', 'd'])['close'].last().rename('c').reset_index()
    return op, cl


def main():
    con = sqlite3.connect(TRADES)
    ensure_cols(con)
    df = pd.read_sql_query(
        "SELECT id, symbol, entry_date, exit_date, shares, pnl, mode "
        "FROM overnight_trades WHERE status='CLOSED' AND exit_date IS NOT NULL "
        "AND (ref_pnl IS NULL)", con)
    if df.empty:
        print('nothing to backfill'); con.close(); return
    op, cl = prints(list(df.entry_date) + list(df.exit_date))
    m = (df.merge(cl.rename(columns={'d': 'entry_date', 'c': 'ref_entry'}),
                  on=['symbol', 'entry_date'], how='left')
           .merge(op.rename(columns={'d': 'exit_date', 'o': 'ref_exit'}),
                  on=['symbol', 'exit_date'], how='left'))
    m = m.dropna(subset=['ref_entry', 'ref_exit'])
    m['ref_pnl'] = (m.ref_exit - m.ref_entry) * m.shares - m.shares.apply(equity_commission)
    m['exec_drag'] = m.ref_pnl - m.pnl
    con.executemany('UPDATE overnight_trades SET ref_entry=?, ref_exit=?, ref_pnl=?, exec_drag=? WHERE id=?',
                    m[['ref_entry', 'ref_exit', 'ref_pnl', 'exec_drag', 'id']].values.tolist())
    con.commit()
    print(f'backfilled {len(m)} of {len(df)} rows')

    full = pd.read_sql_query(
        "SELECT mode, COUNT(*) n, ROUND(SUM(pnl),2) actual, ROUND(SUM(ref_pnl),2) strategy, "
        "ROUND(SUM(exec_drag),2) exec_drag FROM overnight_trades "
        "WHERE ref_pnl IS NOT NULL GROUP BY mode", con)
    con.close()
    print('\n=== strategy vs execution, all recorded Clockwork trades ===')
    print(full.to_string(index=False))
    print('\n  actual    = what the broker filled us at')
    print('  strategy  = the same trades at the official close and open, net of the modelled fee')
    print('  exec_drag = strategy - actual. On the IBKR paper account this is largely simulator')
    print('              noise, NOT a real cost — see this file\'s docstring before acting on it.')


if __name__ == '__main__':
    main()
