"""STACK LAB — does filtering candidates on the month-consistent separators concentrate the
forward edge, and does it hold in EVERY month? Judged on median MFE/|MAE| (the signal's own
edge ratio) and mover rate, not on a fitted P&L."""
import sys; sys.path.insert(0,'/Users/sushil/trading')
import pandas as pd, numpy as np
pd.set_option('display.width',210)
r=pd.read_csv('research_out/sel.csv')
r['m']=pd.to_datetime(r.scan_date).dt.to_period('M')
r['hr']=pd.to_numeric(r.scan_time.str.slice(0,2),errors='coerce')

def score(x,label):
    if len(x)<40: print(f'{label:<46} n={len(x):<5} TOO FEW'); return
    ratio=x.mfe_f120.median()/abs(x.mae_f120.median())
    bym=x.groupby('m').apply(lambda g: pd.Series({
        'n':len(g),'ratio':g.mfe_f120.median()/abs(g.mae_f120.median()) if g.mae_f120.median() else np.nan,
        'mover':(g.mfe_f120>=2).mean()*100}),include_groups=False)
    good=int((bym.ratio>1.19).sum())          # beats the unfiltered baseline
    print(f'{label:<46} n={len(x):<5} mover {x.mover.mean()*100:4.1f}%  MFE {x.mfe_f120.median():.2f}%  '
          f'MAE {x.mae_f120.median():.2f}%  ratio {ratio:.2f}  months beating baseline {good}/{len(bym)}  '
          f'| ' + ' '.join(f'{v:.2f}' for v in bym.ratio))

print('baseline ratio = 1.19 (all A+/A LONG candidates)\n')
score(r,'ALL CANDIDATES (baseline)')
print()
score(r[r.burst_age_min.between(30,90)],'fresh burst 30-90min')
score(r[r.vol_ratio>=3],'volume >= 3x')
score(r[r.hr<=9],'signalled before 10:00')
score(r[r.price_vs_hod_pct<-1],'not pinned at day high (>1% below)')
print()
score(r[(r.burst_age_min.between(30,90))&(r.vol_ratio>=3)],'fresh + vol>=3x')
score(r[(r.burst_age_min.between(30,90))&(r.hr<=9)],'fresh + before 10:00')
score(r[(r.vol_ratio>=3)&(r.hr<=9)],'vol>=3x + before 10:00')
score(r[(r.burst_age_min.between(30,90))&(r.vol_ratio>=3)&(r.hr<=9)],'fresh + vol>=3x + before 10:00')
score(r[(r.vol_ratio>=3)&(r.price_vs_hod_pct<-1)],'vol>=3x + not pinned at high')
print()
print('--- the mirror image: the orphan pocket we should stop buying ---')
score(r[(r.burst_age_min>150)|(r.vol_ratio<1.5)],'stale burst OR thin volume')
score(r[(r.burst_age_min>150)&(r.vol_ratio<1.5)],'stale AND thin')
score(r[r.hr>=12],'signalled at/after 12:00')
