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

Oct 12 2026 — the Gap-Down Protocol (factory/live/gap_protocol.py) holds positions through a PANIC gap-down open and
sells them at 15:30. For those rows (gap_hold_date set):
    ref_exit    the official price at the time we actually exited that day (close of the last finished 5-min bar)
    ref_open    that morning's official opening print — what the normal MOO sale would have got
    hold_gain   (ref_exit - ref_open) x shares: what holding earned versus selling at the open. THIS is the
                protocol's scorecard; judge it over many PANIC mornings, never one.

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
COLS = [('ref_entry', 'REAL'), ('ref_exit', 'REAL'), ('ref_pnl', 'REAL'), ('exec_drag', 'REAL'),
        ('ref_open', 'REAL'), ('hold_gain', 'REAL')]


# Oct 5 2026: Night Owl (factory/live/night_owl.py) is the second overnight book; the same honest-price
# marking applies, so both tables are processed. Its table is created with these columns already.
TABLES = ('overnight_trades', 'night_owl_trades')


def ensure_cols(con, table='overnight_trades'):
    for c, t in COLS:
        try:
            con.execute(f'ALTER TABLE {table} ADD COLUMN {c} {t}')
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


def price_at(symbol: str, day: str, hhmmss: str):
    """Official price at `hhmmss` on `day`: the close of the last 5-min bar that had FINISHED by then."""
    con = sqlite3.connect(MARKET)
    try:
        b = pd.read_sql_query("SELECT ts_utc, close FROM bars_5m WHERE symbol=? AND ts_utc LIKE ?", con,
                              params=(symbol, day + '%'))
    finally:
        con.close()
    if b.empty:
        return None
    b['ts'] = pd.to_datetime(b.ts_utc, utc=True, format='mixed').dt.tz_convert('America/New_York')
    b = b.sort_values('ts').drop_duplicates('ts', keep='last')
    cut = pd.Timestamp(f'{day} {hhmmss}', tz='America/New_York')
    ok = b[(b.ts + pd.Timedelta(minutes=5) <= cut) & (b.ts.dt.strftime('%H:%M') >= '09:30')]
    return float(ok.close.iloc[-1]) if len(ok) else None


def main():
    for table in TABLES:
        mark(table)


def mark(table):
    con = sqlite3.connect(TRADES)
    try:
        con.execute(f'SELECT 1 FROM {table} LIMIT 1')
    except Exception:
        print(f'{table}: not present yet'); con.close(); return
    ensure_cols(con, table)
    have = {r[1] for r in con.execute(f'PRAGMA table_info({table})')}
    extra = ', gap_hold_date, exit_time' if {'gap_hold_date', 'exit_time'} <= have else ', NULL AS gap_hold_date, NULL AS exit_time'
    df = pd.read_sql_query(
        "SELECT id, symbol, entry_date, exit_date, shares, pnl, mode" + extra + " "
        f"FROM {table} WHERE status='CLOSED' AND exit_date IS NOT NULL "
        "AND (ref_pnl IS NULL)", con)
    if df.empty:
        print(f'{table}: nothing to backfill'); con.close(); return
    op, cl = prints(list(df.entry_date) + list(df.exit_date))
    m = (df.merge(cl.rename(columns={'d': 'entry_date', 'c': 'ref_entry'}),
                  on=['symbol', 'entry_date'], how='left')
           .merge(op.rename(columns={'d': 'exit_date', 'o': 'ref_exit'}),
                  on=['symbol', 'exit_date'], how='left'))
    # Gap-Down Protocol rows: the exit was not the opening auction. ref_open = the official open on the hold day (the
    # sale the protocol skipped); ref_exit = the official price when we really got out (15:30 sell or the stop).
    m['ref_open'], m['hold_gain'] = None, None
    opens = {(r.symbol, r.d): r.o for r in op.itertuples()}
    for i, r in m[m.gap_hold_date.notna()].iterrows():
        o = opens.get((r.symbol, r.gap_hold_date))
        if r.exit_date == r.gap_hold_date and r.exit_time:
            x = price_at(r.symbol, r.exit_date, r.exit_time)
        else:                                   # the 15:30 sell never confirmed; it went at a later open
            x = r.ref_exit
        m.at[i, 'ref_exit'] = x
        m.at[i, 'ref_open'] = o
        m.at[i, 'hold_gain'] = (x - o) * r.shares if (x is not None and o is not None) else None
    m = m[~(m.gap_hold_date.notna() & (m.ref_open.isna() | m.ref_exit.isna()))]   # retried tomorrow night
    m = m.dropna(subset=['ref_entry', 'ref_exit'])
    m['ref_pnl'] = (m.ref_exit.astype(float) - m.ref_entry) * m.shares - m.shares.apply(equity_commission)
    m['exec_drag'] = m.ref_pnl - m.pnl
    m = m.astype(object).where(m.notna(), None)
    con.executemany(f'UPDATE {table} SET ref_entry=?, ref_exit=?, ref_pnl=?, exec_drag=?, ref_open=?, hold_gain=? '
                    'WHERE id=?',
                    m[['ref_entry', 'ref_exit', 'ref_pnl', 'exec_drag', 'ref_open', 'hold_gain', 'id']].values.tolist())
    con.commit()
    print(f'{table}: backfilled {len(m)} of {len(df)} rows')

    full = pd.read_sql_query(
        "SELECT mode, COUNT(*) n, ROUND(SUM(pnl),2) actual, ROUND(SUM(ref_pnl),2) strategy, "
        f"ROUND(SUM(exec_drag),2) exec_drag FROM {table} "
        "WHERE ref_pnl IS NOT NULL GROUP BY mode", con)
    con.close()
    print(f'\n=== strategy vs execution, all recorded {table} ===')
    print(full.to_string(index=False))
    print('\n  actual    = what the broker filled us at')
    print('  strategy  = the same trades at the official close and open, net of the modelled fee')
    print('  exec_drag = strategy - actual. On the IBKR paper account this is largely simulator')
    print('              noise, NOT a real cost — see this file\'s docstring before acting on it.')


if __name__ == '__main__':
    main()
