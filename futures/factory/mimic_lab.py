"""Mimic lab (Oct 1 2026): what copying the TC system's trades by hand would earn, on the
TopStep $50K STATIC combine ($149 one-time, 90 days, $4,000 target, floor fixed at $48,000,
$1,000 daily loss limit, 55% consistency, no resets).

Uses the live-parity Tide-on sim book (_tideab3_on.csv, 2021-06 .. 2026-09) and MNQ 1-minute bars.
Entry price in the book = close of the 5-min bar labelled entry_time, so the copy decision happens
at entry_time + 5 min. Four questions:

  A. LAG      — what does arriving k minutes late cost (same exit as the system)?
  B. BRACKET  — the "set R:R" button: a fixed stop/target on the system's entries, sized to the
                same dollar risk, vs letting the system's own exit run.
  C. COMBINE  — pass / blow / run-out-of-time odds on the static $50K for a perfect copy.
  D. SPLIT    — one contract on a fixed target, one riding the system's exit.

Approximation: a tighter stop or a target is resolved on 1-min bars between the decision and the
system's own exit; if neither is touched first, the trade ends exactly as the system's did. If a
stop and a target fall in the same minute, the STOP is assumed (conservative).
"""
import numpy as np, pandas as pd, sqlite3, datetime as dt

BOOK = '/Users/sushil/trading/futures/factory/_tideab3_on.csv'
FRIC = 6.0                                   # $ per contract round trip (bench convention)
book = pd.read_csv(BOOK)
con = sqlite3.connect('/Users/sushil/trading/market_data.db')
m = pd.read_sql_query("SELECT ts_utc, high, low, close FROM futures_bars_1m WHERE symbol='MNQ' "
                      "AND ts_utc >= '2021-06-01'", con)
m['ts'] = pd.to_datetime(m.ts_utc, format='mixed', utc=True).dt.tz_convert('America/New_York')
m = m.drop_duplicates('ts', keep='last').set_index('ts').sort_index()
by_day = {d: g for d, g in m.groupby(m.index.date)}

T = []
for r in book.itertuples():
    g = by_day.get(dt.date.fromisoformat(r.date))
    if g is None:
        continue
    sd = 1 if r.side == 'LONG' else -1
    h, mi = map(int, r.entry_time.split(':')); xh, xm = map(int, r.exit_time.split(':'))
    t0 = h * 60 + mi + 5; t1 = xh * 60 + xm + 4
    gm = g.index.hour * 60 + g.index.minute
    path = g[(gm >= t0) & (gm <= t1)]
    if len(path) == 0:
        continue
    T.append(dict(date=r.date, yr=r.date[:4], side=r.side, sd=sd, e=r.entry, x=r.exit,
                  sys_pts=(r.exit - r.entry) * sd, hr=h,
                  fav=((path.high - r.entry) if sd == 1 else (r.entry - path.low)).values,
                  adv=((r.entry - path.low) if sd == 1 else (path.high - r.entry)).values,
                  lagpx=[g[gm == t0 + k - 1].close.iloc[0] if (gm == t0 + k - 1).any() else np.nan
                         for k in (1, 2, 3, 5, 10)]))
T = pd.DataFrame(T)
print(f"{len(T)} system trades matched to 1-min bars ({T.date.min()} .. {T.date.max()})\n")

# ── A. lag ────────────────────────────────────────────────────────────────────
print("A. COST OF ARRIVING LATE (same exit as the system), pts per contract")
for i, k in enumerate((1, 2, 3, 5, 10)):
    lag = np.array([(lp[i] - e) * s for lp, e, s in zip(T.lagpx, T.e, T.sd)])
    print(f"   {k:>2} min late: avg {np.nanmean(lag):+5.1f} pts against you "
          f"(median {np.nanmedian(lag):+5.1f}) = ${np.nanmean(lag)*2:+.1f}/contract")
print()


def resolve(fav, adv, S, Tg, sys_pts):
    for f, a in zip(fav, adv):
        if a >= S: return -S
        if Tg and f >= Tg: return Tg
    return max(sys_pts, -S)


def book_usd(S, Tg, risk=400.0):
    c = max(1, int(risk // (2 * S)))
    pts = np.array([resolve(f, a, S, Tg, sp) for f, a, sp in zip(T.fav, T.adv, T.sys_pts)])
    return pts, c, (pts * 2 - FRIC) * c


def stats(usd):
    s = pd.Series(usd, index=T.index)
    yr = s.groupby(T.yr).sum(); day = s.groupby(T.date).sum()
    eq = day.cumsum(); dd = (eq - eq.cummax()).min()
    return yr, day, dd

# ── B. bracket ────────────────────────────────────────────────────────────────
print("B. THE R:R BUTTON on the system's entries, every variant sized to ~$400 risk per trade")
rows = []
for S in (200, 150, 100, 75, 50, 30):
    for R in (None, 1, 2, 3):
        Tg = None if R is None else S * R
        pts, c, usd = book_usd(S, Tg)
        yr, day, dd = stats(usd)
        rows.append(dict(stop=S, target='system exit' if R is None else f'{R}R ({Tg}pt)', contracts=c,
                         win=(usd > 0).mean(), total=usd.sum(), per_trade=usd.mean(),
                         green=f"{(yr > 0).sum()}/{len(yr)}", y2026=yr.get('2026', 0),
                         worst_day=day.min(), maxdd=dd))
B = pd.DataFrame(rows)
pd.set_option('display.width', 220)
print(B.round({'win': 2, 'total': 0, 'per_trade': 1, 'y2026': 0, 'worst_day': 0, 'maxdd': 0}).to_string(index=False))
print()

# ── D. split: 1 contract fixed target, 1 contract system exit (200pt stop on both) ────
print("D. SPLIT — contract 1 takes a fixed target, contract 2 rides the system's exit (200pt stop)")
base = (np.array(T.sys_pts) * 2 - FRIC)
for Tg in (40, 60, 80, 100, 150):
    leg1 = np.array([resolve(f, a, 200, Tg, sp) for f, a, sp in zip(T.fav, T.adv, T.sys_pts)]) * 2 - FRIC
    tot = leg1 + base
    yr, day, dd = stats(tot)
    print(f"   target {Tg:>3}pt + ride: total ${tot.sum():>7,.0f}  win {np.mean(tot>0):.0%}  "
          f"green {(yr>0).sum()}/{len(yr)}  maxDD ${dd:,.0f}   (2 x ride = ${2*base.sum():,.0f})")
print()

# ── C. static $50K combine, perfect copy ──────────────────────────────────────
print("C. STATIC $50K COMBINE (one shot, 90 days) — perfect copy of the system")
trades = {}
for r in T.itertuples():
    trades.setdefault(r.date, []).append(r)
cal = pd.bdate_range(T.date.min(), T.date.max())


def combine(start_i, size_fn, S=200, Tg=None):
    bal = 50_000.; best = 0.; start = cal[start_i]
    end = start + pd.Timedelta(days=90)
    for d in cal[start_i:]:
        if d > end:
            return 'expired', bal - 50_000
        sess = 0.
        for r in trades.get(d.strftime('%Y-%m-%d'), []):
            if sess <= -1_000 + 50 or sess >= 2_000:     # DLL / soft day cap (keeps 55% rule)
                break
            want = size_fn(bal)
            c = min(want, int((1_000 - 50 + min(sess, 0)) // (2 * S + FRIC)))
            if c < 1:
                break
            pts = resolve(r.fav, r.adv, S, Tg, r.sys_pts)
            sess += (pts * 2 - FRIC) * c
            if bal + sess <= 48_000:
                return 'blown', bal + sess - 50_000
        bal += sess; best = max(best, sess); prof = bal - 50_000
        if prof >= 4_000 and best <= 0.55 * prof:
            return 'passed', prof
    return 'no data', bal - 50_000


SIZES = {
    '1 MNQ, 200pt stop': (lambda b: 1, 200, None),
    '2 MNQ, 200pt stop': (lambda b: 2, 200, None),
    'ladder 1->2 MNQ at +$1,000': (lambda b: 1 if b < 51_000 else 2, 200, None),
}
for lab, (a, b) in {'starts Jun 2021 - Jun 2026': ('2021-06-01', '2026-06-30'),
                    'starts Oct 2025 - Jun 2026': ('2025-10-01', '2026-06-30'),
                    'starts Apr 2026 - Jun 2026': ('2026-04-01', '2026-06-30')}.items():
    idx = [i for i, d in enumerate(cal) if a <= d.strftime('%Y-%m-%d') <= b]
    print(f"  {lab} ({len(idx)} start days)")
    for name, (fn, S, Tg) in SIZES.items():
        res = [combine(i, fn, S, Tg) for i in idx]
        o = pd.Series([x[0] for x in res]); p = pd.Series([x[1] for x in res])
        print(f"     {name:<30} pass {np.mean(o=='passed'):4.0%}  blown {np.mean(o=='blown'):4.0%}  "
              f"ran out of time {np.mean(o=='expired'):4.0%}  median P&L at the end ${p.median():+,.0f}")
    print()

# hours
print("WHEN THE SIGNALS COME (share of trades and P&L at 2 MNQ, system exit)")
h = pd.DataFrame(dict(hr=T.hr, usd=base * 2))
print(h.groupby('hr').agg(trades=('usd', 'size'), total=('usd', 'sum'), per_trade=('usd', 'mean')).round(0).to_string())
print(f"\nsignals per week: {len(T) / (len(cal) / 5):.2f}  ->  ~{len(T) / (len(cal) / 5) * 90 / 7:.0f} per 90 days")


# ── E. bold vs timid on a one-shot, 90-day static combine ─────────────────────
# With a deadline and a fixed floor, playing small mostly runs out the clock. Sweep the risk per
# trade (contracts = risk // full-stop cost) and the stop width, same entries, and see which
# maximises the chance of reaching $4,000 before the floor or the deadline.
def combine2(start_i, S, Tg, risk):
    c_want = max(1, int(risk // (2 * S + FRIC)))
    return combine(start_i, lambda b: c_want, S, Tg)

if __name__ == '__main__':
    print("E. BOLD vs TIMID — pass odds by stop width and risk per trade (perfect copy of entries)")
    for lab, (a, b) in {'starts Jun 2021 - Jun 2026': ('2021-06-01', '2026-06-30'),
                        'starts Oct 2025 - Jun 2026': ('2025-10-01', '2026-06-30')}.items():
        idx = [i for i, d in enumerate(cal) if a <= d.strftime('%Y-%m-%d') <= b]
        rows = []
        for S in (200, 100, 75, 50):
            for R in (None, 2):
                Tg = None if R is None else S * R
                for risk in (400, 600, 800, 950):
                    c = max(1, int(risk // (2 * S + FRIC)))
                    if 2 * S * c + FRIC * c > 1000:      # a full stop must fit the $1,000 DLL
                        continue
                    res = [combine2(i, S, Tg, risk) for i in idx]
                    o = pd.Series([x[0] for x in res])
                    rows.append(dict(stop=S, exit='system' if R is None else '2R target', mnq=c,
                                     risk=int(2 * S * c + FRIC * c), passed=np.mean(o == 'passed'),
                                     blown=np.mean(o == 'blown'), expired=np.mean(o == 'expired')))
        E = pd.DataFrame(rows).sort_values('passed', ascending=False)
        for col in ('passed', 'blown', 'expired'):
            E[col] = (E[col] * 100).round(0).astype(int).astype(str) + '%'
        print(f"  {lab}"); print(E.head(12).to_string(index=False)); print()


# ── F. a learning phase first, and only the hours you can watch ──────────────
def combine3(start_i, plan, hours=None):
    """plan(day_n, bal) -> (contracts, stop, target). hours = entry hours you are present for."""
    bal = 50_000.; best = 0.; start = cal[start_i]; end = start + pd.Timedelta(days=90)
    for n, d in enumerate(cal[start_i:]):
        if d > end:
            return 'expired'
        sess = 0.
        for r in trades.get(d.strftime('%Y-%m-%d'), []):
            if hours is not None and r.hr not in hours:
                continue
            if sess <= -950 or sess >= 2_000:
                break
            want, S, Tg = plan(n, bal)
            c = min(want, int((1_000 - 50 + min(sess, 0)) // (2 * S + FRIC)))
            if c < 1:
                break
            sess += (resolve(r.fav, r.adv, S, Tg, r.sys_pts) * 2 - FRIC) * c
            if bal + sess <= 48_000:
                return 'blown'
        bal += sess; best = max(best, sess); prof = bal - 50_000
        if prof >= 4_000 and best <= 0.55 * prof:
            return 'passed'
    return 'no data'

if __name__ == '__main__':
    BOLD = (5, 75, 150); TIMID = (1, 200, None)
    PLANS = {
        'timid all the way (1 MNQ, 200pt)':        lambda n, b: TIMID,
        'bold from day 1 (5 MNQ, 75pt/150pt)':     lambda n, b: BOLD,
        'learn 15 days timid, then bold':          lambda n, b: TIMID if n < 15 else BOLD,
        'learn 25 days timid, then bold':          lambda n, b: TIMID if n < 25 else BOLD,
    }
    HOURS = {'all hours': None, '10:30-11:00 only': {10}, '13:00-14:00 only': {13}, '10:30-11:00 + 13:00-14:00': {10, 13}}
    print("F. LEARNING PHASE and WATCH WINDOW (perfect copy of entries)")
    for lab, (a, b) in {'starts Jun 2021 - Jun 2026': ('2021-06-01', '2026-06-30'),
                        'starts Oct 2025 - Jun 2026': ('2025-10-01', '2026-06-30')}.items():
        idx = [i for i, d in enumerate(cal) if a <= d.strftime('%Y-%m-%d') <= b]
        print(f"  {lab}")
        for pn, plan in PLANS.items():
            o = pd.Series([combine3(i, plan) for i in idx])
            print(f"    {pn:<40} pass {np.mean(o=='passed'):4.0%}  blown {np.mean(o=='blown'):4.0%}  expired {np.mean(o=='expired'):4.0%}")
        for hn, hrs in HOURS.items():
            if hrs is None: continue
            o = pd.Series([combine3(i, PLANS['bold from day 1 (5 MNQ, 75pt/150pt)'], hrs) for i in idx])
            print(f"    bold, {hn:<34} pass {np.mean(o=='passed'):4.0%}  blown {np.mean(o=='blown'):4.0%}  expired {np.mean(o=='expired'):4.0%}")
        print()
