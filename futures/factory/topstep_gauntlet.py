import pandas as pd, numpy as np, gauntlet2 as G
STOP_1C=406.0   # 200pt stop x $2 + $6 friction
d=G.load('/Users/sushil/trading/futures/factory/_tideab3_on.csv'); trades={k:g for k,g in d.groupby('date')}
cal=pd.bdate_range(d.date.min(), d.date.max()).strftime('%Y-%m-%d').tolist()
def journey(s0, size, rule, horizon):
    resets=0; bal=50000.; hwm=50000.; best=0.
    for di in range(s0, min(s0+horizon,len(cal))):
        floor=max(hwm-2000,48000); room=bal-floor
        dead = (room < 300) if rule=='buffer' else (room < STOP_1C)
        if dead:                                   # can't trade -> pay a reset, start again tomorrow
            resets+=1; bal=50000.; hwm=50000.; best=0.; continue
        g=trades.get(cal[di]); sess=0.; blown=False
        if g is not None:
            for t in g.itertuples():
                r=bal+sess-floor
                if sess<=-700 or sess>=1200: break
                if rule=='buffer':
                    if r < 300: break
                    c = t.contracts if size=='sim' else (1 if t.side=='SHORT' else size)
                else:   # step-down: never risk more than the room
                    want = t.contracts if size=='sim' else (1 if t.side=='SHORT' else size)
                    c = min(want, int(r // STOP_1C))
                    if c < 1: break
                sess += t.pc*c - 6.0*c
                if bal+sess <= floor: blown=True; break
                if sess <= -1000: break
        if blown:
            resets+=1; bal=50000.; hwm=50000.; best=0.; continue
        bal+=sess; hwm=max(hwm,bal); best=max(best,sess); prof=bal-50000
        if prof>=3000 and best<=0.55*prof: return True, di-s0+1, resets
    return False, min(horizon,len(cal)-s0), resets
rows=[]
for lab,(a,b,h) in {'ALL HISTORY, 12mo':('2021-06-01','2025-09-30',252),'RECENT (Oct25-Mar26), 6mo':('2025-10-01','2026-03-31',126)}.items():
    starts=[i for i,c in enumerate(cal) if a<=c<=b]
    for rule in ('buffer','stepdown'):
        for size in ('sim',2,3):
            res=[journey(s,size,rule,h) for s in starts]
            ok=np.array([x[0] for x in res]); days=np.array([x[1] for x in res]); rs=np.array([x[2] for x in res])
            months=np.ceil(days/21); cost=85*months+85*np.maximum(0,rs-months)
            rows.append(dict(window=lab,rule=rule,size=size,
                pass_3mo=f"{(ok&(days<=63)).mean():.0%}",pass_6mo=f"{(ok&(days<=126)).mean():.0%}",pass_12mo=f"{ok.mean():.0%}" if h==252 else '-',
                median_months=round(np.median(days[ok])/21,1) if ok.any() else None,resets=round(rs.mean(),2),
                cost_if_pass=int(cost[ok].mean()) if ok.any() else None))
pd.set_option('display.width',220); print(pd.DataFrame(rows).to_string(index=False))
