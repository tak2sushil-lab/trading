"""The bar a manual trader has to clear: click in the Tide's direction at a RANDOM minute of the
system's window, with a fixed bracket, and see what that earns. Any manual timing skill has to
beat this — it is "the tape plus the Tide, and no timing at all".

MNQ 1-minute bars (Databento), 2021-2026. Entry = close of a random minute 10:30-13:59 ET
(lunch 12:00-12:59 excluded, like the system). Bracket: 2 MNQ, stop S, target T; if both are
touched in the same minute the STOP is assumed first (conservative). Otherwise out at 15:55.
Tide = previous session close vs its 200-day MA (futures/daily_tide.py, same as live).
$6/contract round trip.
"""
import sys, numpy as np, pandas as pd, sqlite3, datetime as dt
sys.path.insert(0, '/Users/sushil/trading')
from futures.daily_tide import Tide, daily_closes

S, T, C, FRIC = 50, 100, 2, 6.0
con = sqlite3.connect('/Users/sushil/trading/market_data.db')
m = pd.read_sql_query("SELECT ts_utc, high, low, close FROM futures_bars_1m WHERE symbol='MNQ' "
                      "AND ts_utc >= '2021-06-01'", con)
m['ts'] = pd.to_datetime(m.ts_utc, format='mixed', utc=True).dt.tz_convert('America/New_York')
m = m.drop_duplicates('ts', keep='last').set_index('ts').sort_index()
mins = m.index.hour * 60 + m.index.minute
m = m[(mins >= 630) & (mins <= 955)]                      # 10:30 .. 15:55
tide = Tide(daily_closes())
rng = np.random.default_rng(7)
rows = []
for day, g in m.groupby(m.index.date):
    up, *_ = tide.verdict(day)
    if up is None or len(g) < 200:
        continue
    side = 1 if up else -1
    gm = g.index.hour * 60 + g.index.minute
    ok = np.where(((gm >= 630) & (gm < 720)) | ((gm >= 780) & (gm < 840)))[0]
    hi, lo, cl = g.high.values, g.low.values, g.close.values
    for i in rng.choice(ok, size=min(6, len(ok)), replace=False):   # 6 random clicks a day
        e = cl[i]
        if side == 1:
            stop_hit = np.where(lo[i+1:] <= e - S)[0]; tgt_hit = np.where(hi[i+1:] >= e + T)[0]
        else:
            stop_hit = np.where(hi[i+1:] >= e + S)[0]; tgt_hit = np.where(lo[i+1:] <= e - T)[0]
        s0 = stop_hit[0] if len(stop_hit) else 10**9
        t0 = tgt_hit[0] if len(tgt_hit) else 10**9
        if s0 == 10**9 and t0 == 10**9:
            pts = (cl[-1] - e) * side; out = 'eod'
        elif s0 <= t0:
            pts = -S; out = 'stop'
        else:
            pts = T; out = 'target'
        rows.append(dict(date=day, yr=day.year, side='LONG' if side == 1 else 'SHORT',
                         pts=pts, out=out, usd=pts * 2 * C - FRIC * C))
r = pd.DataFrame(rows)
r['win'] = r.usd > 0
def summ(g):
    return pd.Series(dict(clicks=len(g), win_rate=g.win.mean(), avg_usd=g.usd.mean(),
                          target=(g.out == 'target').mean(), stop=(g.out == 'stop').mean(),
                          eod=(g.out == 'eod').mean()))
pd.set_option('display.width', 200)
print(f"Bracket {C} MNQ  stop {S}pt / target {T}pt  (lose ${S*2*C+FRIC*C:.0f}, win ${T*2*C-FRIC*C:.0f})\n")
print(r.groupby('yr').apply(summ, include_groups=False).round(3).to_string()); print()
print(r.groupby('side').apply(summ, include_groups=False).round(3).to_string()); print()
rec = r[r.date >= dt.date(2025, 10, 1)]
print('last 12 months (Oct 2025 - Sep 2026):'); print(summ(rec).round(3).to_string()); print()
print('ALL:'); print(summ(r).round(3).to_string())
