"""HORIZON LAB — how long does this book's edge actually last?
The add-on lab found forward return AFTER T+30 is negative for winners and positive for
losers: i.e. everything mean-reverts once the first half hour is done. This tests the direct
implication - a flat exit-at-T+N horizon for every trade - and, because that contradicts the
hold-to-close measurement shipped Sep 4, splits it by entry hour."""
import sys; sys.path.insert(0,'/Users/sushil/trading')
import pandas as pd, numpy as np
pd.set_option('display.width',200)
r=pd.read_csv('/private/tmp/claude-501/-Users-sushil-trading/6359faf3-30ae-4922-aae4-879c2b075f53/scratchpad/stall.csv'.replace('4922','4992'))
r['m']=pd.to_datetime(r.date).dt.to_period('M')
r['hr']=pd.to_numeric(r.date.astype(str).str.slice(0,0)+'0',errors='coerce')  # placeholder
import sqlite3
c=sqlite3.connect('trades.db'); et=pd.read_sql_query('select id,entry_time from trades',c); c.close()
r=r.merge(et,on='id',how='left')
r['hr']=pd.to_numeric(r.entry_time.str.slice(0,2),errors='coerce')
r['notional']=r.ep*r.sh
base=r.pnl.sum()
print(f'replayed book: n={len(r)}  ${base:,.0f}\n')

print(f"{'horizon':>9} {'n moved':>8} {'new book $':>11} {'delta $':>9} {'months +ve':>11}")
for M in [15,30,45,60,90]:
    x=r.copy()
    live=x[f'alive{M}']==1
    x['new']=np.where(live, x[f'mk{M}']/100*x.notional, x.pnl)
    bym=x.groupby('m').apply(lambda g: g['new'].sum()-g['pnl'].sum(), include_groups=False)
    print(f"{'T+'+str(M):>9} {int(live.sum()):>8} {x['new'].sum():>11,.0f} "
          f"{x['new'].sum()-base:>+9,.0f} {int((bym>0).sum()):>6}/{len(bym)}")

print('\n=== same test, split by entry hour (T+30) ===')
x=r.copy(); live=x.alive30==1
x['new']=np.where(live,x.mk30/100*x.notional,x.pnl)
g=x.groupby(pd.cut(x.hr,[8,9,10,11,12,16])).apply(lambda z: pd.Series({
    'n':len(z),'n_moved':int((z.alive30==1).sum()),'real$':z.pnl.sum(),
    'atT30$':z['new'].sum(),'delta$':z['new'].sum()-z.pnl.sum()}),include_groups=False)
print(g.round(0).to_string())

print('\n=== peak-vs-hold: where does a trade sit at T+30 relative to its whole life? ===')
a=r[r.alive30==1]
print(f"  median mark at T+30 : {a.mk30.median():+.2f}%")
print(f"  median peak by T+30 : {a.pk30.median():+.2f}%")
print(f"  median final        : {a.rpct.median():+.2f}%")
print(f"  median lifetime MFE : {a.mfe.median():+.2f}%")
print(f"  share of lifetime peak already reached by T+30: {(a.pk30/a.mfe.replace(0,np.nan)).median()*100:.0f}%")
print(f"  median hold length  : {a.held_min.median():.0f} min")
