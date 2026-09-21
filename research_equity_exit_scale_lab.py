#!/usr/bin/env python
"""EXIT-SCALE LAB — the exit stack is calibrated for moves this book does not make.

Measured: median lifetime peak is 0.99%, yet PCT_TRAIL arms at +1.5%, the break-even stop at
+2.5%, and the 1R PARTIAL EXIT at +5% -- which 2.1% of trades ever reach, and which has fired
5 times in 818 trades. Every previous attempt moved these DOWN as a full exit and lost money
(6 rejections: trails, conditional arms, 1-min granularity, fade segmentation, stall-cut,
horizon). This tests something different and never run on equity:

    PARTIAL scale-out -- bank HALF at a threshold scaled to the real distribution, let the
    other half run on the untouched exit stack.

That is not "exit earlier". Half the position keeps the full right tail. It is the mechanism
that WORKED on futures (Jul 25 2026: bank 1 of 2 contracts at +150pts, runner stop to BE) and
that equity has had wired but effectively dead since it was written.

Replays each real closed trade against its own 5-min bars from its real entry.
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sqlite3
import numpy as np, pandas as pd
from collect_bars import load_bars
pd.set_option('display.width', 220)

COMM_PER_LEG = lambda sh: max(sh * 0.005, 1.0)


def load():
    con = sqlite3.connect('trades.db')
    d = pd.read_sql_query("""
      SELECT id,symbol,entry_date,entry_time,entry_price,exit_date,exit_time,exit_price,
             shares,side,pnl,pnl_pct,exit_reason
      FROM trades WHERE status IN ('WIN','LOSS') AND setup_type!='RECONCILED' AND side='LONG'
      ORDER BY entry_date,entry_time""", con)
    con.close()
    cache = {}
    def bars(sym, d1, d2):
        k = (sym, d1, d2)
        if k not in cache:
            try: cache[k] = load_bars(sym, start=d1, end=d2)
            except Exception: cache[k] = None
        return cache[k]
    out = []
    for t in d.itertuples(index=False):
        xd = t.exit_date or t.entry_date
        b = bars(t.symbol, t.entry_date,
                 (pd.Timestamp(xd) + pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
        if b is None or len(b) == 0: continue
        try:
            e = pd.Timestamp(f'{t.entry_date} {t.entry_time}', tz='America/New_York')
            x = pd.Timestamp(f'{xd} {t.exit_time}', tz='America/New_York') if t.exit_time else None
        except Exception: continue
        w = b[(b.index >= e) & ((b.index <= x) if x is not None else True)]
        if len(w) == 0: continue
        out.append((t, w))
    return out


def replay(trades, partial_at, runner_to_be=True):
    """Bank half at +partial_at%, optionally move the runner's stop to break-even, and let the
    runner finish exactly where the trade REALLY finished. Nothing else about the book changes,
    so this isolates the scale-out and nothing else."""
    rows = []
    for t, w in trades:
        ep, sh = t.entry_price, t.shares
        half = sh // 2
        if half < 1 or partial_at is None:
            rows.append(dict(id=t.id, date=t.entry_date, pnl=t.pnl)); continue
        trig = ep * (1 + partial_at / 100)
        hit = w[w.high >= trig]
        if len(hit) == 0:
            rows.append(dict(id=t.id, date=t.entry_date, pnl=t.pnl)); continue
        t_hit = hit.index[0]
        banked = (trig - ep) * half - COMM_PER_LEG(half) * 2
        rest = sh - half
        # runner: BE stop armed after the partial, else it ends where the real trade ended
        rest_exit = t.exit_price
        if runner_to_be:
            after = w[w.index > t_hit]
            be = w[w.index <= t_hit].low.min() if False else ep
            pierce = after[after.low <= be]
            if len(pierce) and (t.exit_price < be):
                rest_exit = be
        pnl = banked + (rest_exit - ep) * rest - COMM_PER_LEG(rest) * 2
        rows.append(dict(id=t.id, date=t.entry_date, pnl=pnl))
    r = pd.DataFrame(rows)
    r['m'] = pd.to_datetime(r.date).dt.to_period('M')
    return r


def main():
    tr = load()
    print(f'replayable LONG trades: {len(tr)}')
    base = pd.DataFrame([dict(id=t.id, date=t.entry_date, pnl=t.pnl) for t, _ in tr])
    base['m'] = pd.to_datetime(base.date).dt.to_period('M')
    b_tot = base.pnl.sum()
    bym_b = base.groupby('m').pnl.sum()
    print(f'actual book (these trades): ${b_tot:,.2f}\n')
    print(f"{'partial at':>11}{'runner->BE':>12}{'total $':>11}{'delta':>10}{'fired':>7}   monthly deltas")
    for pa in [0.75, 1.0, 1.5, 2.0, 3.0, 5.0]:
        for be in (True, False):
            r = replay(tr, pa, runner_to_be=be)
            fired = int((r.pnl != base.set_index('id').loc[r.id].pnl.values).sum())
            bym = r.groupby('m').pnl.sum() - bym_b
            d = r.pnl.sum() - b_tot
            print(f"{pa:>10.2f}%{str(be):>12}{r.pnl.sum():>11,.0f}{d:>+10,.0f}{fired:>7}   "
                  + ' '.join(f'{v:+.0f}' for v in bym) + f"   [{int((bym>0).sum())}/{len(bym)} months]")


if __name__ == '__main__':
    main()
