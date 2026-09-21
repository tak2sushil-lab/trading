"""ADD-ON LAB — the opposite of every give-back fix tried so far.
Five tests have rejected exiting earlier. Nobody has tested exiting LATER with MORE SIZE.
At T+N a trade's own mark is a strong classifier (up >1% at T+30 -> 88% eventual win).
So: keep the exit stack exactly as it is, and put a second unit on the trades that have
already proved themselves. Measures the P&L of the ADD-ON leg only, from the T+N price to
the trade's real exit price - i.e. nothing about the existing book changes."""
import sys; sys.path.insert(0,'/Users/sushil/trading')
import pandas as pd, numpy as np
pd.set_option('display.width',200)
r=pd.read_csv('research_out/stall.csv')
r['m']=pd.to_datetime(r.date).dt.to_period('M')
# reconstruct exit % and the price at each mark
r['exit_pct']=r.rpct
print(f'base book (replayed): n={len(r)}  ${r.pnl.sum():,.0f}')

for M in [15,30,45,60]:
    print(f'\n{"="*78}\nADD-ON AT T+{M}')
    for TH in [0.5,1.0,1.5]:
        sub=r[(r[f'alive{M}']==1)&(r[f'mk{M}']>=TH)].copy()
        if len(sub)<8: continue
        # add-on leg: entered at the T+M mark price, exits with the trade
        # return of the add-on = (exit% - mark%) measured off the mark price
        sub['addon_pct']=(1+sub.exit_pct/100)/(1+sub[f'mk{M}']/100)-1
        sub['addon_pct']*=100
        notional=sub.ep*sub.sh                     # same size as the original unit
        sub['addon$']=sub.addon_pct/100*notional
        bym=sub.groupby('m')['addon$'].agg(['size','sum']).round(0)
        pos=(sub['addon$']>0).mean()*100
        print(f"  thresh +{TH}%  n={len(sub):3d}  add-on P&L ${sub['addon$'].sum():>8,.0f}  "
              f"avg ${sub['addon$'].mean():6.2f}  win {pos:3.0f}%  "
              f"months +ve {int((bym['sum']>0).sum())}/{len(bym)}")
        if TH==1.0 and M==30:
            print('    by month:'); print('     '+bym.to_string().replace('\n','\n     '))
            print(f"    the same trades' ORIGINAL legs: ${sub.pnl.sum():,.0f}")

print(f'\n{"="*78}\nCONTROL — add-on on trades that have NOT proved themselves (mark < 0 at T+30)')
sub=r[(r.alive30==1)&(r.mk30<0)].copy()
sub['addon_pct']=((1+sub.exit_pct/100)/(1+sub.mk30/100)-1)*100
sub['addon$']=sub.addon_pct/100*sub.ep*sub.sh
bym=sub.groupby('m')['addon$'].sum()
print(f"  n={len(sub)}  add-on P&L ${sub['addon$'].sum():,.0f}  months +ve {int((bym>0).sum())}/{len(bym)}")
print('  => confirms the classifier, not a rising tape' if sub['addon$'].sum()<0 else '  => WARNING: tape effect, not selection')
