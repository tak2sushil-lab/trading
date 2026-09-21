#!/usr/bin/env python
"""HORIZON LAB — is this a swing universe being day-traded?

Every measurement this session used a 2-hour forward window, because that is roughly how long
we hold. The edge ratio there is 1.15 for A+ LONG candidates -- close to a coin. But the
Aug 14 2026 diagnosis ("we DAY-TRADE A SWING EDGE") and the Aug 6 observation that 54
multi-day-held trades averaged +$46 against ~breakeven for same-day trades both point the same
way, and Wave Rider's only profits are its 3-day time exits.

So: hold the SAME candidates, on the SAME signals, and just extend the horizon.
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sqlite3
import numpy as np, pandas as pd
from collect_bars import load_bars
pd.set_option('display.width', 220)

HORIZONS = [('2h', None), ('1d', 1), ('3d', 3), ('5d', 5), ('10d', 10)]


def main():
    con = sqlite3.connect('trades.db')
    d = pd.read_sql_query("""
        SELECT id, scan_date, scan_time, symbol, price, grade, fwd_mfe_pct, fwd_mae_pct
        FROM scan_log WHERE direction='LONG' AND grade='A+' AND fwd_mfe_pct IS NOT NULL
          AND price IS NOT NULL""", con)
    con.close()
    print(f'A+ LONG candidates: {len(d):,}')

    cache = {}
    def bars(sym, d0, days):
        k = (sym, d0, days)
        if k not in cache:
            if len(cache) > 300: cache.clear()
            try:
                end = (pd.Timestamp(d0) + pd.Timedelta(days=days + 6)).strftime('%Y-%m-%d')
                b = load_bars(sym, start=d0, end=end)
                cache[k] = b if b is not None and len(b) else None
            except Exception:
                cache[k] = None
        return cache[k]

    rows = []
    for x in d.itertuples(index=False):
        b = bars(x.symbol, x.scan_date, 12)
        if b is None: continue
        try: t0 = pd.Timestamp(f'{x.scan_date} {x.scan_time}', tz='America/New_York')
        except Exception: continue
        fwd = b[b.index >= t0]
        if len(fwd) < 4: continue
        px = float(x.price)
        r = dict(id=x.id, m=pd.Period(x.scan_date[:7], 'M'))
        sess = sorted(set(fwd.index.date))
        ok = True
        for lab, nd in HORIZONS:
            if nd is None:
                w = fwd[fwd.index <= t0 + pd.Timedelta(minutes=120)]
            else:
                if len(sess) < nd + 1: ok = False; break
                w = fwd[fwd.index.to_series().dt.date <= sess[nd]]
            if len(w) == 0: ok = False; break
            r[f'mfe_{lab}'] = (float(w.high.max()) - px) / px * 100
            r[f'mae_{lab}'] = (float(w.low.min()) - px) / px * 100
            r[f'ret_{lab}'] = (float(w.close.iloc[-1]) - px) / px * 100
        if ok: rows.append(r)
    r = pd.DataFrame(rows)
    print(f'candidates with full 10-day forward history: {len(r):,}\n')

    print('=== SAME CANDIDATES, LONGER HORIZON ===')
    print(f"{'horizon':>8}{'mfe':>8}{'mae':>8}{'ratio':>8}{'buy&hold ret':>14}{'win%':>7}   monthly buy&hold")
    for lab, _ in HORIZONS:
        mfe, mae, ret = r[f'mfe_{lab}'].mean(), r[f'mae_{lab}'].mean(), r[f'ret_{lab}'].mean()
        bym = r.groupby('m')[f'ret_{lab}'].mean()
        print(f'{lab:>8}{mfe:>8.3f}{mae:>8.3f}{mfe/abs(mae):>8.3f}{ret:>+14.3f}'
              f'{(r[f"ret_{lab}"]>0).mean()*100:>7.1f}   '
              + ' '.join(f'{v:+.2f}' for v in bym) + f'  [{int((bym>0).sum())}/{len(bym)}]')
    r.to_csv('research_out/horizon.csv', index=False)


if __name__ == '__main__':
    main()
