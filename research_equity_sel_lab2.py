"""SELECTION LAB v2 — correct forward label.
actual_day_high_pct is the stock's day measured from the OPEN (database.py:1993), so it mostly
re-reports the entry gate (MIN_TODAY_GAIN 3%) and says nothing about our forward opportunity.
This version computes the TRUE forward MFE from the signal: max high in the next 60/120 min
relative to the signal price, from bars_5m. Label MOVER = forward MFE >= 2%."""
import sys; sys.path.insert(0,'/Users/sushil/trading')
import sqlite3, pandas as pd, numpy as np
from collect_bars import load_bars
pd.set_option('display.width',210)

c=sqlite3.connect('trades.db')
d=pd.read_sql_query("""
 SELECT scan_date,scan_time,symbol,regime,price,grade,score,vol_ratio,rsi,intra_chg,sector,
        is_catalyst,entered,actual_day_pct,actual_day_high_pct,actual_30m_pct,actual_60m_pct,
        burst_age_min,consec_new_highs,price_vs_hod_pct
 FROM scan_log WHERE direction='LONG' AND enriched=1 AND grade IN ('A+','A')
   AND price IS NOT NULL""",c)
c.close()
print('A+/A LONG enriched rows:',len(d))

cache={}
def day(sym,dt):
    k=(sym,dt)
    if k not in cache:
        try:
            b=load_bars(sym,start=(pd.Timestamp(dt)-pd.Timedelta(days=2)).strftime('%Y-%m-%d'),
                            end=(pd.Timestamp(dt)+pd.Timedelta(days=2)).strftime('%Y-%m-%d'))
            b=b[b.index.date==pd.Timestamp(dt).date()] if b is not None and len(b) else None
            cache[k]=b if b is not None and len(b) else None
        except Exception: cache[k]=None
    return cache[k]

recs=[]
for _,x in d.iterrows():
    b=day(x.symbol,x.scan_date)
    if b is None: continue
    try: t0=pd.Timestamp(f"{x.scan_date} {x.scan_time}",tz='America/New_York')
    except Exception: continue
    for win,lab in [(60,'f60'),(120,'f120'),(9999,'feod')]:
        w=b[(b.index>=t0)&(b.index<=t0+pd.Timedelta(minutes=win))]
        x[f'mfe_{lab}']=(w.high.max()-x.price)/x.price*100 if len(w) else np.nan
        x[f'mae_{lab}']=(w.low.min()-x.price)/x.price*100 if len(w) else np.nan
    recs.append(x)
r=pd.DataFrame(recs)
r=r.dropna(subset=['mfe_f120'])
r['m']=pd.to_datetime(r.scan_date).dt.to_period('M')
r['hr']=pd.to_numeric(r.scan_time.str.slice(0,2),errors='coerce')
r['mover']=(r.mfe_f120>=2.0).astype(int)
r['orphan']=(r.mfe_f120<0.5).astype(int)
r.to_csv('research_out/sel.csv',index=False)

print('\n=== TRUE forward opportunity from the signal (2h window) ===')
print(r.groupby('m').agg(n=('mover','size'),mover_pct=('mover',lambda s:s.mean()*100),
        orphan_pct=('orphan',lambda s:s.mean()*100),
        med_mfe=('mfe_f120','median'),med_mae=('mae_f120','median'),
        med_mfe60=('mfe_f60','median'),med_eod=('mfe_feod','median')).round(2).to_string())
print(f"\noverall n={len(r)}  mover {r.mover.mean()*100:.1f}%  orphan {r.orphan.mean()*100:.1f}%  "
      f"median MFE2h {r.mfe_f120.median():.2f}%  median MAE2h {r.mae_f120.median():.2f}%")
print(f"edge ratio  median MFE/|MAE| = {r.mfe_f120.median()/abs(r.mae_f120.median()):.2f}")

def sweep(col,bins,label=None):
    x=r.dropna(subset=[col]).copy()
    if len(x)<150: print(f'\n{col}: too few ({len(x)})'); return
    x['b']=pd.cut(x[col],bins)
    piv=x.pivot_table(index='b',columns='m',values='mover',aggfunc='mean',observed=True)*100
    cnt=x.groupby('b',observed=True).agg(n=('mover','size'),mover=('mover',lambda s:s.mean()*100),
        medMFE=('mfe_f120','median'),medMAE=('mae_f120','median'),
        edge=('mfe_f120',lambda s:s.median()))
    cnt['MFE/MAE']=cnt.medMFE/cnt.medMAE.abs()
    print(f'\n--- {label or col} ---')
    print(cnt.drop(columns=['edge']).join(piv.round(0)).round(2).to_string())

sweep('intra_chg',[-99,1,3,5,8,12,99],'intraday change at signal %')
sweep('vol_ratio',[0,1.5,3,5,10,999],'volume ratio')
sweep('rsi',[0,50,60,70,80,101],'daily RSI')
sweep('price_vs_hod_pct',[-99,-5,-2,-1,-0.3,0.3,99],'distance below day high %')
sweep('burst_age_min',[-1,30,60,90,150,9999],'burst age (min)')
sweep('hr',[8,9,10,11,12,13,16],'hour of signal (ET)')
sweep('price',[0,10,20,50,100,99999],'price band')
sweep('score',[0,80,90,95,100,200],'grade score')
