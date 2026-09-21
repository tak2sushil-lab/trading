"""STALL LAB — does a trade that hasn't worked by T+N minutes ever work?
Replays each closed equity trade's own 5-min bars and records the mark at T+15/30/45/60,
then tests 'cut if not up X% by T+N' against what actually happened."""
import sys; sys.path.insert(0,'/Users/sushil/trading')
import sqlite3, pandas as pd, numpy as np
from collect_bars import load_bars

con = sqlite3.connect('trades.db')
df = pd.read_sql_query("""
 SELECT id,symbol,entry_date,entry_time,entry_price,exit_date,exit_time,exit_price,shares,side,
        pnl,pnl_pct,exit_reason,setup_type
 FROM trades WHERE status IN ('WIN','LOSS') AND setup_type!='RECONCILED' ORDER BY entry_date,entry_time""", con)
con.close()

cache={}
def bars(sym,d1,d2):
    k=(sym,d1,d2)
    if k not in cache:
        try: cache[k]=load_bars(sym,start=d1,end=d2)
        except Exception: cache[k]=None
    return cache[k]

MARKS=[15,30,45,60,90]
rows=[]
for _,t in df.iterrows():
    ed=t.entry_date; xd=t.exit_date or ed
    b=bars(t.symbol,(pd.Timestamp(ed)-pd.Timedelta(days=2)).strftime('%Y-%m-%d'),
                    (pd.Timestamp(xd)+pd.Timedelta(days=2)).strftime('%Y-%m-%d'))
    if b is None or len(b)==0: continue
    try:
        e=pd.Timestamp(f"{ed} {t.entry_time}",tz='America/New_York')
        x=pd.Timestamp(f"{xd} {t.exit_time}",tz='America/New_York') if t.exit_time else None
    except Exception: continue
    w=b[(b.index>=e)&((b.index<=x) if x is not None else True)]
    if len(w)==0: continue
    sgn = -1 if t.side=='SHORT' else 1
    ep=t.entry_price
    d=dict(id=t.id,sym=t.symbol,date=ed,side=t.side,setup=t.setup_type,ep=ep,sh=t.shares,
           pnl=t.pnl,rpct=t.pnl_pct,reason=t.exit_reason,
           mfe=sgn*(w.high.max()-ep)/ep*100 if sgn>0 else (ep-w.low.min())/ep*100,
           held_min=(w.index[-1]-e).total_seconds()/60)
    for m in MARKS:
        cut=e+pd.Timedelta(minutes=m)
        seg=w[w.index<=cut]
        # mark = close of last bar at/ before the mark; None if trade already over
        d[f'mk{m}']= (sgn*(float(seg.close.iloc[-1])-ep)/ep*100) if len(seg) else np.nan
        # best seen up to the mark
        d[f'pk{m}']= ((float(seg.high.max())-ep)/ep*100 if sgn>0 else (ep-float(seg.low.min()))/ep*100) if len(seg) else np.nan
        d[f'alive{m}']= 1 if (x is None or cut < x) else 0
    rows.append(d)
r=pd.DataFrame(rows)
r.to_csv('research_out/stall.csv',index=False)
print('trades replayed:',len(r), '| still alive at T+30:',int(r.alive30.sum()))
print(f'baseline realized total: ${r.pnl.sum():,.0f}  ({(r.rpct>0).mean()*100:.0f}% win)')

print('\n=== WHAT HAPPENS TO A TRADE BY ITS MARK AT T+30 (still-alive trades only) ===')
a=r[r.alive30==1].copy()
a['b30']=pd.cut(a.mk30,[-99,-1,-0.5,0,0.5,1,99],labels=['<-1','-1..-0.5','-0.5..0','0..0.5','0.5..1','>1'])
print(a.groupby('b30',observed=True).apply(lambda x: pd.Series({
  'n':len(x),'final$':x.pnl.sum(),'final_avg%':x.rpct.mean(),
  'eventual_win%':(x.rpct>0).mean()*100,'avg_mfe%':x.mfe.mean()}),include_groups=False).round(2))

print('\n=== CUT-IF-STALLED: cut at T+N when mark < THRESH (exit at that bar close) ===')
print(f"{'T+N':>5} {'thresh':>7} {'n cut':>6} {'their real $':>13} {'cut-at-mark $':>14} {'delta $':>10} {'book after':>11}")
base=r.pnl.sum()
for m in MARKS:
    for th in [0.0,0.25,0.5]:
        sub=r[(r[f'alive{m}']==1)&(r[f'mk{m}']<th)].copy()
        if len(sub)==0: continue
        cut_pnl=(sub[f'mk{m}']/100*sub.ep*sub.sh).sum()
        real=sub.pnl.sum()
        print(f"{m:>5} {th:>7.2f} {len(sub):>6} {real:>13,.0f} {cut_pnl:>14,.0f} {cut_pnl-real:>+10,.0f} {base-real+cut_pnl:>+11,.0f}")
