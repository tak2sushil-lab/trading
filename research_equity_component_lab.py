#!/usr/bin/env python
"""COMPONENT LAB — are grade_setup()'s four biggest weights earning them?

grade_setup sums ~15 hand-assigned components. The four largest price-action weights are
ORB breakout +30, VWAP reclaim +25, bull flag +25, HOD break +20 -- and NONE of them has
ever been recorded, so none has ever been checked against an outcome (scan_log stores only
the score TOTAL; the reasons list is discarded every scan). database.log_scan_candidate now
persists the breakdown, but that only helps from the next restart onward.

This reconstructs three of them from bars_5m at each candidate's own signal time, using the
SAME definitions as get_intraday_signals (auto_trader.py:1556, 1579, 1617), and scores them
against the corrected forward label (scan_log.fwd_mfe_pct / fwd_mae_pct -- excursions from
the SIGNAL price, not actual_day_high_pct which measures the day from the OPEN).

Bull flag is deliberately not reconstructed: its definition is a multi-condition surge ->
consolidation -> breakout shape whose thresholds live in several places, and an approximation
of it would be a different signal wearing its name.
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sqlite3
import numpy as np, pandas as pd
from collect_bars import load_bars
pd.set_option('display.width', 220)

ORB_CUTOFF = (11, 30)


def reconstruct(b, t0, price):
    """orb_break / vwap_reclaim / above_vwap / hod_break as of t0, from today's bars."""
    day = b[b.index.date == t0.date()]
    past = day[day.index <= t0]
    if len(past) < 2:
        return None
    tp = (past['high'] + past['low'] + past['close']) / 3
    vw = (tp * past['volume']).cumsum() / past['volume'].cumsum().replace(0, np.nan)
    above_vwap = bool(past['close'].iloc[-1] > vw.iloc[-1]) if pd.notna(vw.iloc[-1]) else None
    reclaim = None
    if len(past) >= 2 and pd.notna(vw.iloc[-1]) and pd.notna(vw.iloc[-2]):
        reclaim = bool(past['close'].iloc[-1] > vw.iloc[-1] and past['close'].iloc[-2] <= vw.iloc[-2])
    hod = float(past['high'].max())
    prior_hod = float(past['high'].iloc[:-2].max()) if len(past) > 2 else hod
    hod_break = bool(price >= prior_hod * 0.999 and price >= hod * 0.995)
    orb = day.between_time('09:30', '09:44')
    orb_break = None
    if len(orb) >= 2:
        oh = round(float(orb['high'].max()), 2)
        orb_break = bool(price > oh and price >= oh * 0.998 and (t0.hour, t0.minute) < ORB_CUTOFF)
    return dict(orb_break=orb_break, vwap_reclaim=reclaim, above_vwap=above_vwap, hod_break=hod_break)


def main():
    con = sqlite3.connect('trades.db')
    d = pd.read_sql_query("""
        SELECT id, scan_date, scan_time, symbol, price, score, fwd_mfe_pct, fwd_mae_pct
        FROM scan_log
        WHERE direction='LONG' AND grade IN ('A+','A')
          AND fwd_mfe_pct IS NOT NULL AND price IS NOT NULL""", con)
    con.close()
    print(f'labelled A+/A LONG candidates: {len(d):,}')

    cache = {}
    def day(sym, ds):
        k = (sym, ds)
        if k not in cache:
            if len(cache) > 400: cache.clear()
            try:
                nxt = (pd.Timestamp(ds) + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
                b = load_bars(sym, start=ds, end=nxt)
                cache[k] = b if b is not None and len(b) else None
            except Exception:
                cache[k] = None
        return cache[k]

    rows = []
    for x in d.itertuples(index=False):
        b = day(x.symbol, x.scan_date)
        if b is None: continue
        try: t0 = pd.Timestamp(f'{x.scan_date} {x.scan_time}', tz='America/New_York')
        except Exception: continue
        r = reconstruct(b, t0, float(x.price))
        if r is None: continue
        r.update(id=x.id, m=pd.Period(x.scan_date[:7], 'M'), score=x.score,
                 mfe=x.fwd_mfe_pct, mae=x.fwd_mae_pct, scan_date=x.scan_date,
                 edge=x.fwd_mfe_pct + x.fwd_mae_pct)   # net of the adverse swing
        rows.append(r)
    r = pd.DataFrame(rows)
    r.to_csv('research_out/components.csv', index=False)
    print(f'reconstructed: {len(r):,}\n')

    print('=== DOES EACH BIG WEIGHT EARN IT? ===')
    print('edge = fwd_mfe + fwd_mae (favourable move NET of the adverse swing) — the quantity')
    print('a stop-dominated book actually lives on. mover = fwd_mfe >= 2%.\n')
    print(f"{'component':<16}{'weight':>7}{'n on':>7}{'n off':>7}{'edge ON':>9}{'edge OFF':>10}"
          f"{'delta':>8}{'mover ON':>10}{'mover OFF':>11}   months better")
    for f, w in [('orb_break', '+30'), ('vwap_reclaim', '+25'), ('above_vwap', '+10'), ('hod_break', '+20')]:
        x = r.dropna(subset=[f])
        on, off = x[x[f] == 1], x[x[f] == 0]
        if len(on) < 40 or len(off) < 40:
            print(f'{f:<16}{w:>7}  too few'); continue
        bym = x.groupby('m').apply(
            lambda z: (z[z[f] == 1].edge.mean() - z[z[f] == 0].edge.mean())
            if (z[f] == 1).sum() > 4 and (z[f] == 0).sum() > 4 else np.nan, include_groups=False)
        good = int((bym > 0).sum()); tot = int(bym.notna().sum())
        print(f'{f:<16}{w:>7}{len(on):>7}{len(off):>7}{on.edge.mean():>+9.3f}{off.edge.mean():>+10.3f}'
              f'{on.edge.mean()-off.edge.mean():>+8.3f}{(on.mfe>=2).mean()*100:>9.1f}%{(off.mfe>=2).mean()*100:>10.1f}%'
              f'   {good}/{tot}' + ('  <<<' if good >= max(4, tot - 1) else ''))
    print('\n  <<< = the component is better in at least all-but-one month')


if __name__ == '__main__':
    main()
