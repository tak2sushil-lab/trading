#!/usr/bin/env python
"""CLOCKWORK REPLAY — what did the STRATEGY earn, versus what the EXECUTION delivered?

Takes Clockwork's ACTUAL live picks (same symbols, same nights, no re-selection, no
hindsight) and re-prices them three ways:
  A  as actually filled                      -- what really happened
  B  at the official prints (15:55 close -> 09:30 open) -- what the strategy was worth
  C  B, at $3,000 per name and the TIERED fee -- the proposed configuration

Anything B earns over A is execution, not strategy. Anything C earns over B is sizing and fees.
"""
import sqlite3, sys
import numpy as np, pandas as pd
pd.set_option('display.width', 210)

FEE = {'FIXED': (0.005, 1.00), 'TIERED': (0.0035, 0.35)}


def fee(shares, plan):
    ps, mn = FEE[plan]
    return max(shares * ps, mn) * 2


def main():
    c = sqlite3.connect('market_data.db')
    b = pd.read_sql_query("select symbol,ts_utc,open,close from bars_5m where ts_utc>='2026-09-01'", c)
    c.close()
    ts = pd.to_datetime(b.ts_utc, utc=True, format='mixed').dt.tz_convert('America/New_York')
    b['d'] = ts.dt.date.astype(str); b['t'] = ts.dt.strftime('%H:%M')
    op = b[b.t == '09:30'][['symbol', 'd', 'open']].rename(columns={'open': 'o930'})
    cl = b[b.t == '15:55'][['symbol', 'd', 'close']].rename(columns={'close': 'c1600'})

    t = sqlite3.connect('trades.db')
    x = pd.read_sql_query(
        "select symbol,entry_date,exit_date,entry_price,exit_price,shares,pnl "
        "from overnight_trades where mode='LIVE' and status='CLOSED'", t)
    t.close()
    m = (x.merge(cl.rename(columns={'d': 'entry_date'}), on=['symbol', 'entry_date'], how='left')
           .merge(op.rename(columns={'d': 'exit_date'}), on=['symbol', 'exit_date'], how='left')
           .dropna(subset=['c1600', 'o930']))
    print(f'live Clockwork nights replayed: {len(m)} trades, {m.entry_date.nunique()} sessions')

    # A — as filled (pnl already net of whatever the engine booked)
    A = m.pnl.sum()
    a_notional = (m.entry_price * m.shares).sum()

    # B — official prints, same shares, same fee plan the book actually used (FIXED)
    m['b_gross'] = (m.o930 - m.c1600) * m.shares
    m['b_fee'] = m.shares.apply(lambda s: fee(s, 'FIXED'))
    B = (m.b_gross - m.b_fee).sum()

    # C — official prints, $3,000 per name, TIERED
    m['c_sh'] = (3000 / m.c1600).apply(np.floor).clip(lower=1)
    m['c_gross'] = (m.o930 - m.c1600) * m.c_sh
    m['c_fee'] = m.c_sh.apply(lambda s: fee(s, 'TIERED'))
    C = (m.c_gross - m.c_fee).sum()

    print(f"\n{'scenario':<52}{'P&L':>10}{'per night':>12}{'per trade bp':>14}")
    rows = [
        ('A  as actually filled (live)', A, (m.entry_price * m.shares).sum()),
        ('B  official prints, same size, FIXED fee', B, (m.c1600 * m.shares).sum()),
        ('C  official prints, $3,000/name, TIERED fee', C, (m.c1600 * m.c_sh).sum()),
    ]
    for lab, pnl, notl in rows:
        print(f'{lab:<52}{pnl:>+10,.0f}{pnl/m.entry_date.nunique():>+12,.1f}'
              f'{pnl/notl*1e4:>+14.1f}')

    print(f"\n  execution cost  (B - A) : ${B - A:+,.0f}")
    print(f"  sizing + fee gain (C - B): ${C - B:+,.0f}")
    print(f"  total gap        (C - A) : ${C - A:+,.0f}")

    print('\n  gross of ALL fees, at the official prints:')
    print(f"    strategy gross            : ${m.b_gross.sum():+,.0f}  ({m.b_gross.sum()/(m.c1600*m.shares).sum()*1e4:+.1f}bp/night)")
    print(f"    fee at FIXED,  live size  : ${-m.b_fee.sum():+,.0f}")
    print(f"    fee at TIERED, $3,000     : ${-m.c_fee.sum():+,.0f}")

    print('\n  per session (official prints, $3,000, TIERED):')
    s = m.groupby('entry_date').apply(lambda g: (g.c_gross - g.c_fee).sum(), include_groups=False)
    live = m.groupby('entry_date').pnl.sum()
    print('   ' + pd.DataFrame({'as filled': live.round(0), 'proposed config': s.round(0)}).to_string().replace('\n', '\n   '))


if __name__ == '__main__':
    main()
