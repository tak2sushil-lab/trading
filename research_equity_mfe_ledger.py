"""MFE/give-back ledger for the equity main book, computed from 5-min bars.
Loads every closed trade, replays its own bars entry->exit, records true MFE/MAE."""
import sys; sys.path.insert(0,"/Users/sushil/trading")
import sqlite3, pandas as pd, numpy as np, sys
from collect_bars import load_bars

con = sqlite3.connect('trades.db')
df = pd.read_sql_query("""
 SELECT id,symbol,entry_date,entry_time,entry_price,exit_date,exit_time,exit_price,shares,side,
        pnl,pnl_pct,max_gain_pct,exit_reason,setup_type,stop_price
 FROM trades WHERE status IN ('WIN','LOSS') AND setup_type!='RECONCILED'
 ORDER BY entry_date,entry_time""", con)
con.close()
print('closed trades:', len(df), df.entry_date.min(), '->', df.entry_date.max())

cache = {}
def bars(sym, d1, d2):
    k = (sym, d1, d2)
    if k in cache: return cache[k]
    try: b = load_bars(sym, start=d1, end=d2)
    except Exception: b = None
    cache[k] = b
    return b

rows = []
for _, t in df.iterrows():
    ed = t.entry_date; xd = t.exit_date or ed
    b = bars(t.symbol, (pd.Timestamp(ed)-pd.Timedelta(days=2)).strftime("%Y-%m-%d"),
                        (pd.Timestamp(xd)+pd.Timedelta(days=2)).strftime("%Y-%m-%d"))
    if b is None or len(b) == 0: continue
    try:
        e_dt = pd.Timestamp(f"{ed} {t.entry_time}", tz='America/New_York')
        x_dt = pd.Timestamp(f"{xd} {t.exit_time}", tz='America/New_York') if t.exit_time else b.index[-1]
    except Exception: continue
    w = b[(b.index >= e_dt) & (b.index <= x_dt)]
    if len(w) == 0: continue
    ep = t.entry_price
    if t.side == 'SHORT':
        mfe = (ep - w.low.min())/ep*100; mae = (ep - w.high.max())/ep*100
    else:
        mfe = (w.high.max() - ep)/ep*100; mae = (w.low.min() - ep)/ep*100
    rows.append(dict(id=t.id, sym=t.symbol, d=ed, side=t.side, setup=t.setup_type,
                     et=t.entry_time, xt=t.exit_time, ep=ep, xp=t.exit_price, sh=t.shares,
                     pnl=t.pnl, rpct=t.pnl_pct, mfe=mfe, mae=mae, nbars=len(w),
                     logged_mg=t.max_gain_pct, reason=t.exit_reason))
r = pd.DataFrame(rows)
r.to_csv('research_out/mfe.csv', index=False)
print('replayable:', len(r))
r['m'] = pd.to_datetime(r.d).dt.to_period('M')
print('\n=== realized vs perfect-peak, by month (LONG+SHORT) ===')
g = r.groupby('m').apply(lambda x: pd.Series({
    'n': len(x), 'realized$': x.pnl.sum(),
    'perfect$': (x.mfe/100*x.ep*x.sh).sum(),
    'giveback$': (x.mfe/100*x.ep*x.sh).sum() - x.pnl.sum(),
    'avg_mfe%': x.mfe.mean(), 'avg_real%': x.rpct.mean(), 'avg_mae%': x.mae.mean(),
}), include_groups=False)
print(g.round(1))
print('\n=== MFE distribution (how big does a trade ever get?) ===')
for th in [0.5,1,1.5,2,3,5,8]:
    print(f'  peak >= {th}%: {(r.mfe>=th).mean()*100:5.1f}%  ({(r.mfe>=th).sum():4d} trades)')
print(f'  median MFE {r.mfe.median():.2f}%   median MAE {r.mae.median():.2f}%')
print('\n=== capture: realized / MFE, by MFE bucket ===')
r['buck'] = pd.cut(r.mfe, [-1,0.5,1,2,3,5,100], labels=['<0.5','0.5-1','1-2','2-3','3-5','>5'])
print(r.groupby('buck', observed=True).apply(lambda x: pd.Series({
    'n': len(x), 'pnl$': x.pnl.sum(), 'avg_mfe%': x.mfe.mean(), 'avg_real%': x.rpct.mean(),
    'capture%': x.rpct.sum()/x.mfe.sum()*100 if x.mfe.sum() else np.nan}), include_groups=False).round(1))
