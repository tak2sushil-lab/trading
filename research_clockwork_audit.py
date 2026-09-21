#!/usr/bin/env python
"""CLOCKWORK AUDIT — what is missing, what is tunable, what is the real tail.

Context from practice, not just papers: NightShares launched ETFs to harvest exactly this
premium and CLOSED them a year later, explicitly because turning the portfolio over in full
twice a day ate the return. Our position is different in one specific way -- we trade 3 names
at $3,333, so market impact is ~0 and the cost is a known $0.70 round trip -- but the warning
is the right frame: this strategy lives or dies on turnover cost and tail nights.
"""
import sys, numpy as np, pandas as pd
pd.set_option('display.width', 220)

p = pd.read_parquet('research_out/panel.parquet')
p['d'] = p['d'].astype(str)
q = p.sort_values(['symbol', 'd']).copy()
g = q.groupby('symbol')
q['fwd_on'] = g['on'].shift(-1)
q['adv'] = g.apply(lambda x: (x['close'] * x['vol']).rolling(20).mean(), include_groups=False).reset_index(level=0, drop=True)
for lb in (10, 20, 30, 40, 60):
    q[f'c{lb}'] = g['on'].transform(lambda s: (s > 0).rolling(lb).mean())
q['meanon'] = g['on'].transform(lambda s: s.rolling(30).mean())
q = q.dropna(subset=['fwd_on', 'c30', 'adv'])
q['dow'] = pd.to_datetime(q.d).dt.dayofweek
q['yr'] = pd.to_datetime(q.d).dt.year


def book(sel, n=3, minadv=0, df=None, price_lo=5, price_hi=800):
    df = q if df is None else df
    df = df[(df.close >= price_lo) & (df.close <= price_hi)]
    if minadv: df = df[df.adv >= minadv]
    rows = []
    for d, day in df.groupby('d'):
        day = day.dropna(subset=[sel])
        if len(day) < max(10, n): continue
        pick = day.nlargest(n, sel)
        rows.append(dict(d=d, r=pick.fwd_on.mean(), dow=pick.dow.iloc[0],
                         worst=pick.fwd_on.min()))
    s = pd.DataFrame(rows).set_index('d').sort_index()
    cum = (1 + s.r).cumprod()
    return s, dict(bp=s.r.mean() * 1e4, sharpe=s.r.mean() / s.r.std() * np.sqrt(252),
                   maxDD=((cum / cum.cummax()) - 1).min() * 100, days=len(s))


print('=== 1. THE TAIL — what a 3-name book actually risks in one night ===')
s, m = book('c30', 3, 2e7)
print(f"  nights {m['days']}   mean {m['bp']:+.1f}bp   Sharpe {m['sharpe']:+.2f}   maxDD {m['maxDD']:.1f}%")
print(f"  worst 5 nights (book-level): {(s.r.nsmallest(5)*1e4).round(0).tolist()} bp")
print(f"  worst single NAME in any night: {s.worst.min()*1e4:.0f}bp")
print(f"  nights worse than -300bp: {(s.r < -0.03).sum()}  |  better than +300bp: {(s.r > 0.03).sum()}")
onm = (1 + s.r).cumprod()
print(f"  share of total return from the best 5% of nights: "
      f"{(s.r.nlargest(int(len(s)*0.05)).sum() / s.r.sum() * 100):.0f}%")

print('\n=== 2. LIQUIDITY FLOOR — the validated config used $20M ADV; live has NO floor ===')
print(f"{'floor':>10}{'bp/night':>10}{'sharpe':>8}{'maxDD%':>9}{'nights':>8}")
for f in (0, 5e6, 2e7, 5e7, 1e8):
    _, mm = book('c30', 3, f)
    print(f"{('$%dM' % (f/1e6)):>10}{mm['bp']:>10.2f}{mm['sharpe']:>8.2f}{mm['maxDD']:>9.1f}{mm['days']:>8}")

print('\n=== 3. LOOKBACK — 30 is live; is it on a plateau? ($20M floor) ===')
print(f"{'lookback':>10}{'bp/night':>10}{'sharpe':>8}{'2026 bp':>10}")
for lb in (10, 20, 30, 40, 60):
    _, a = book(f'c{lb}', 3, 2e7)
    _, b = book(f'c{lb}', 3, 2e7, df=q[q.yr == 2026])
    print(f"{lb:>10}{a['bp']:>10.2f}{a['sharpe']:>8.2f}{b['bp']:>10.2f}")
_, mo = book('meanon', 3, 2e7)
print(f"{'mean-on':>10}{mo['bp']:>10.2f}{mo['sharpe']:>8.2f}      (alternative signal: mean overnight return)")

print('\n=== 4. DAY OF WEEK — Friday carries a 3-day gap ===')
s2, _ = book('c30', 3, 2e7)
s2['dow'] = pd.to_datetime(s2.index).dayofweek
names = {0: 'Mon->Tue', 1: 'Tue->Wed', 2: 'Wed->Thu', 3: 'Thu->Fri', 4: 'Fri->Mon'}
r = s2.groupby('dow').r.agg(n='size', mean='mean', sd='std')
r['bp'] = r['mean'] * 1e4; r['sharpe'] = r['mean'] / r['sd'] * np.sqrt(252)
r.index = r.index.map(names)
print('   ' + r[['n', 'bp', 'sharpe']].round(2).to_string().replace('\n', '\n   '))

print('\n=== 5. HOW MANY NAMES CLEAR THE FILTERS ON A TYPICAL DAY? ===')
elig = q[(q.adv >= 2e7) & (q.close.between(5, 800))].groupby('d').size()
print(f"  median eligible names/day: {elig.median():.0f}   min {elig.min()}   "
      f"days with fewer than 3: {(elig < 3).sum()}")
