#!/usr/bin/env python
"""backfill_scan_forward.py — write the CORRECT forward label onto scan_log.

WHY THIS EXISTS. scan_log's learning target has been `actual_day_high_pct`, which
enrich_scan_log computes as (day_high - day_OPEN)/day_open (database.py). That is the
stock's whole day, not our forward opportunity from the signal -- so it largely re-reports
the MIN_TODAY_GAIN 3% entry gate. Measured Sep 18 2026: it reports 94% of A+/A LONG
candidates as reaching +2%, while the true forward rate from the signal price is 23%. Every
conclusion drawn from that column has been scored against the wrong outcome.

WHAT THIS WRITES, per row, from market_data.db 5-min bars:
    fwd_mfe_pct    best excursion from the SIGNAL price over the next fwd_window_min minutes
    fwd_mae_pct    worst excursion over the same window
    fwd_window_min the window used
Both are signed for the row's own direction, so a SHORT's favourable move is positive.

Backfillable over all history -- no waiting for new data.

Usage:
  venv/bin/python backfill_scan_forward.py                  # all unlabelled rows
  venv/bin/python backfill_scan_forward.py --window 60      # different horizon
  venv/bin/python backfill_scan_forward.py --since 2026-09-01 --rebuild
"""
from __future__ import annotations
import argparse, os, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
from collect_bars import load_bars

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trades.db')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--window', type=int, default=120, help='forward window in minutes')
    ap.add_argument('--since', default=None, help='only rows on/after this scan_date')
    ap.add_argument('--rebuild', action='store_true', help='recompute rows that already have a label')
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()

    con = sqlite3.connect(DB)
    where = ['price IS NOT NULL', 'scan_time IS NOT NULL']
    if not a.rebuild:
        where.append('fwd_mfe_pct IS NULL')
    if a.since:
        where.append(f"scan_date >= '{a.since}'")
    sql = (f"SELECT id, scan_date, scan_time, symbol, direction, price FROM scan_log "
           f"WHERE {' AND '.join(where)} ORDER BY scan_date, symbol")
    if a.limit:
        sql += f' LIMIT {a.limit}'
    rows = pd.read_sql_query(sql, con)
    print(f'rows to label: {len(rows):,}  (window {a.window} min)')
    if rows.empty:
        con.close(); return

    cache: dict = {}
    def day(sym, ds):
        k = (sym, ds)
        if k not in cache:
            if len(cache) > 400:
                cache.clear()
            try:
                nxt = (pd.Timestamp(ds) + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
                b = load_bars(sym, start=ds, end=nxt)
                cache[k] = b if b is not None and len(b) else None
            except Exception:
                cache[k] = None
        return cache[k]

    out, miss = [], 0
    for i, x in enumerate(rows.itertuples(index=False), 1):
        b = day(x.symbol, x.scan_date)
        if b is None:
            miss += 1; continue
        try:
            t0 = pd.Timestamp(f'{x.scan_date} {x.scan_time}', tz='America/New_York')
        except Exception:
            miss += 1; continue
        w = b[(b.index >= t0) & (b.index <= t0 + pd.Timedelta(minutes=a.window))]
        if len(w) == 0:
            miss += 1; continue
        hi, lo, px = float(w.high.max()), float(w.low.min()), float(x.price)
        if px <= 0:
            miss += 1; continue
        if str(x.direction).upper() == 'SHORT':
            mfe, mae = (px - lo) / px * 100, (px - hi) / px * 100
        else:
            mfe, mae = (hi - px) / px * 100, (lo - px) / px * 100
        out.append((round(mfe, 4), round(mae, 4), a.window, x.id))
        if i % 20000 == 0:
            print(f'  ...{i:,} scanned, {len(out):,} labelled')

    con.executemany('UPDATE scan_log SET fwd_mfe_pct=?, fwd_mae_pct=?, fwd_window_min=? WHERE id=?', out)
    con.commit()
    n = con.execute('SELECT COUNT(*) FROM scan_log WHERE fwd_mfe_pct IS NOT NULL').fetchone()[0]
    print(f'labelled {len(out):,}   no bars for {miss:,}   scan_log now carries {n:,} labelled rows')

    q = pd.read_sql_query(
        "SELECT direction, COUNT(*) n, ROUND(AVG(fwd_mfe_pct),3) mfe, ROUND(AVG(fwd_mae_pct),3) mae, "
        "ROUND(AVG(CASE WHEN fwd_mfe_pct>=2 THEN 1.0 ELSE 0 END)*100,1) mover_pct "
        "FROM scan_log WHERE fwd_mfe_pct IS NOT NULL GROUP BY direction", con)
    print(q.to_string(index=False))
    con.close()


if __name__ == '__main__':
    main()
