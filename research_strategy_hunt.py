#!/usr/bin/env python
"""STRATEGY HUNT — day-1 posture. Build the daily panel once, then test evidence-backed
strategy families we do NOT have, against the only benchmark that means anything here.

THE BENCHMARK PROBLEM, established first. Our 241-name universe was screened in Jul 2026,
and 113 of those names were selected on `bt_wr` (backtest win rates of 88-93%) computed over
2024-2026 -- the same window any backtest here would use. Measured: over 2024-01 to 2026-09
the median universe name returned +95% (added) / +87% (incumbents) against SPY's +60%. So a
long-only result on this universe inherits a tailwind that is partly a selection artifact.
=> Every strategy below is scored against the universe's own equal-weight buy-and-hold,
   not against zero and not against SPY.

THE EVIDENCE BASE (and its critics):
  Lou, Polk & Skouras (JFE 2019) "A Tug of War": across 30 years of US intraday data, ALL of
    momentum's abnormal return accrues OVERNIGHT (3-factor alpha 0.95%/mo, t=3.65) while the
    intraday component is 0.11% and insignificant. Short-term reversal is the mirror: sorting
    on past INTRADAY returns earns 2.19%/mo intraday (t=6.72) and gives back -1.81% overnight.
  Hou, Xue & Zhang (RFS 2020) "Replicating Anomalies": 65% of 452 published anomalies fail
    t>1.96 once microcaps are controlled and returns value-weighted; 82% fail at t>2.78.
    Treat every published effect as guilty until it survives on our own tape and our own costs.
  Alpha Architect / recent work: trading costs wipe out the overnight anomaly in many
    implementations. Our own cost is a FLAT $2 round trip (96% of trades hit IBKR's $1
    minimum), which on a $1,400 slot is 0.143% -- larger than most daily-frequency edges.

Panel is built from bars_5m: per symbol per session, the 09:30 open and the 15:55 close, so
    overnight = open[t]/close[t-1] - 1     intraday = close[t]/open[t] - 1
"""
from __future__ import annotations
import os, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
pd.set_option('display.width', 220)

CACHE = 'research_out/panel.parquet'
SPLIT_ABS = 0.35        # |overnight| beyond this is treated as a corporate action, not a return


def build_panel(force=False) -> pd.DataFrame:
    if os.path.exists(CACHE) and not force:
        return pd.read_parquet(CACHE)
    con = sqlite3.connect('market_data.db')
    df = pd.read_sql_query(
        "SELECT symbol, ts_utc, open, high, low, close, volume FROM bars_5m "
        "WHERE ts_utc >= '2024-01-02' ORDER BY symbol, ts_utc", con)
    con.close()
    ts = pd.to_datetime(df.ts_utc, utc=True, format='mixed').dt.tz_convert('America/New_York')
    df['d'] = ts.dt.date
    df['t'] = ts.dt.strftime('%H:%M')
    rth = df[(df.t >= '09:30') & (df.t <= '15:55')]
    g = rth.groupby(['symbol', 'd'])
    p = pd.DataFrame({'open': g['open'].first(), 'close': g['close'].last(),
                      'high': g['high'].max(), 'low': g['low'].min(),
                      'vol': g['volume'].sum(), 'bars': g['close'].count()}).reset_index()
    p = p[p['bars'] >= 60]                       # drop half-days and broken sessions
    p = p.sort_values(['symbol', 'd'])
    p['prev_close'] = p.groupby('symbol')['close'].shift(1)
    p['on'] = p['open'] / p['prev_close'] - 1          # overnight (close -> next open)
    p['id'] = p['close'] / p['open'] - 1                # intraday (open -> close)
    p['tot'] = (1 + p['on']) * (1 + p['id']) - 1
    bad = p['on'].abs() > SPLIT_ABS
    print(f'  corporate-action filter: dropping {int(bad.sum()):,} symbol-days with |overnight| > {SPLIT_ABS:.0%}')
    p.loc[bad, ['on', 'id', 'tot']] = np.nan
    p = p.dropna(subset=['on', 'id'])
    os.makedirs('research_out', exist_ok=True)
    p.to_parquet(CACHE)
    return p


def decompose(p):
    print('\n=== FACT 1: where does our universe\'s return actually accrue? ===')
    print('   (Lou/Polk/Skouras predict the premium is overnight; reversal lives intraday)')
    rows = []
    for lab, sub in [('ALL 2024-01 -> 2026-09', p)] + \
                    [(str(y), p[pd.to_datetime(p.d).dt.year == y]) for y in (2024, 2025, 2026)]:
        if sub.empty: continue
        rows.append(dict(period=lab, sym_days=len(sub),
                         overnight_bp=sub['on'].mean() * 1e4,
                         intraday_bp=sub['id'].mean() * 1e4,
                         total_bp=sub['tot'].mean() * 1e4,
                         on_win=(sub['on'] > 0).mean() * 100,
                         id_win=(sub['id'] > 0).mean() * 100))
    r = pd.DataFrame(rows)
    print(r.round(2).to_string(index=False))
    print('\n   mean return per symbol-day, in basis points. If the overnight column carries the')
    print('   total and the intraday column is flat or negative, our book trades the dead half.')
    ann = lambda x: ((1 + x) ** 252 - 1) * 100
    print(f"\n   compounded over a year at these averages:"
          f"   overnight {ann(p['on'].mean()):+.0f}%   intraday {ann(p['id'].mean()):+.0f}%")


if __name__ == '__main__':
    print('building daily panel from 16M 5-min bars...')
    p = build_panel(force='--rebuild' in sys.argv)
    print(f'  panel: {len(p):,} symbol-days | {p.symbol.nunique()} symbols | {p.d.min()} -> {p.d.max()}')
    decompose(p)


def control_and_risk(p):
    """The universe is performance-selected, so the split above could be an artifact.
    ETFs in the same panel were NOT selected on performance -- they are the control."""
    print('\n=== CONTROL: the same split on instruments we did NOT select on performance ===')
    etfs = ['SPY', 'QQQ', 'IWM', 'XLK', 'SOXX', 'XLE', 'XLF', 'GDX', 'URA', 'BITQ', 'SMH']
    rows = []
    for s in etfs:
        q = p[p.symbol == s]
        if len(q) < 300: continue
        rows.append(dict(sym=s, days=len(q), overnight_bp=q['on'].mean() * 1e4,
                         intraday_bp=q['id'].mean() * 1e4,
                         on_share=q['on'].mean() / (q['on'].mean() + q['id'].mean()) * 100
                         if (q['on'].mean() + q['id'].mean()) != 0 else np.nan))
    c = pd.DataFrame(rows).sort_values('overnight_bp', ascending=False)
    print(c.round(2).to_string(index=False))
    uni = p[~p.symbol.isin(etfs)]
    print(f"\n  selected universe : overnight {uni['on'].mean()*1e4:+.2f}bp  intraday {uni['id'].mean()*1e4:+.2f}bp")

    print('\n=== RISK: is the overnight premium worth its volatility? ===')
    daily = p.groupby('d')[['on', 'id', 'tot']].mean()          # equal-weight universe each day
    for lab in ['on', 'id', 'tot']:
        x = daily[lab]
        sh = x.mean() / x.std() * np.sqrt(252)
        dd = (1 + x).cumprod()
        mdd = ((dd / dd.cummax()) - 1).min() * 100
        print(f'  {lab:4s} mean {x.mean()*1e4:+7.2f}bp  sd {x.std()*1e4:7.1f}bp  '
              f'Sharpe {sh:+5.2f}  maxDD {mdd:7.1f}%  total {(dd.iloc[-1]-1)*100:+8.1f}%')
    print('  (equal-weight across the whole universe each day — no selection at all)')

    print('\n=== COST: does a daily round trip survive our own fee? ===')
    for nom in (1400, 2000, 5000):
        edge = daily['on'].mean() * nom
        for plan, fee in (('FIXED $1 min', 2.00), ('TIERED $0.35 min', 0.70)):
            print(f'  ${nom:,} position: overnight edge ${edge:.2f}/day vs {plan} fee ${fee:.2f} '
                  f'-> net ${edge-fee:+.2f}/day  ({(edge-fee)/nom*1e4:+.1f}bp)')


if __name__ == '__main__' and '--control' in sys.argv:
    p = build_panel()
    control_and_risk(p)


def selection_test(p):
    """Clockwork trades the overnight window and LOSES, while the window itself is the best
    thing in the panel. So the question is not the window -- it is whether SELECTING inside it
    adds anything. Tests equal-weight-everything against every sort we can compute from price."""
    print('\n=== DOES PICKING BEAT NOT PICKING, OVERNIGHT? ===')
    q = p.sort_values(['symbol', 'd']).copy()
    g = q.groupby('symbol')
    q['id_1'] = g['id'].shift(0)            # today's intraday, known at the close
    q['on_1'] = g['on'].shift(0)
    q['mom5'] = g['tot'].transform(lambda s: s.rolling(5).sum()).shift(0)
    q['mom20'] = g['tot'].transform(lambda s: s.rolling(20).sum()).shift(0)
    q['on20'] = g['on'].transform(lambda s: s.rolling(20).mean()).shift(0)
    q['gapcons'] = g['on'].transform(lambda s: (s > 0).rolling(30).mean()).shift(0)
    q['vol20'] = g['tot'].transform(lambda s: s.rolling(20).std()).shift(0)
    q['dollar'] = (q['close'] * q['vol']).groupby(q.symbol).transform(lambda s: s.rolling(20).mean())
    # target = the NEXT overnight return, i.e. tonight, decided at today's close
    q['fwd_on'] = g['on'].shift(-1)
    q = q.dropna(subset=['fwd_on'])

    def book(sel, lab, n=10):
        rows = []
        for d, day in q.groupby('d'):
            day = day.dropna(subset=[sel]) if sel else day
            if len(day) < 20: continue
            pick = day.nlargest(n, sel) if sel else day
            rows.append(dict(d=d, r=pick.fwd_on.mean(), n=len(pick)))
        s = pd.DataFrame(rows).set_index('d').sort_index()
        sh = s.r.mean() / s.r.std() * np.sqrt(252)
        cum = (1 + s.r).cumprod()
        return dict(strategy=lab, days=len(s), bp=s.r.mean() * 1e4, sharpe=sh,
                    total=(cum.iloc[-1] - 1) * 100,
                    maxDD=((cum / cum.cummax()) - 1).min() * 100)

    out = [book(None, 'EQUAL-WEIGHT everything (no pick)')]
    for sel, lab in [('gapcons', "Clockwork's sort: 30d gap consistency"),
                     ('mom20', '20-day momentum (LPS: momentum is overnight)'),
                     ('mom5', '5-day momentum'),
                     ('on20', '20-day mean overnight return'),
                     ('id_1', "today's intraday WINNERS"),
                     ('vol20', 'highest 20-day volatility'),
                     ('dollar', 'largest dollar volume')]:
        out.append(book(sel, lab))
    for sel, lab in [('id_1', "today's intraday LOSERS (LPS reversal)")]:
        q['_neg'] = -q[sel]
        out.append(book('_neg', lab))
    r = pd.DataFrame(out)
    print(r.round(2).to_string(index=False))
    print('\n  top-10 names each night, held close -> next open, gross of fees.')
    print('  Anything below the equal-weight row means the SELECTION is destroying value.')


if __name__ == '__main__' and '--select' in sys.argv:
    selection_test(build_panel())


def stress(p):
    """The candidate: buy today's biggest intraday gainers at the close, sell at the open.
    Before believing it -- year splits, costs, concentration, and whether it is just volatility."""
    q = p.sort_values(['symbol', 'd']).copy()
    g = q.groupby('symbol')
    q['vol20'] = g['tot'].transform(lambda s: s.rolling(20).std())
    q['fwd_on'] = g['on'].shift(-1)
    q['dollar'] = (q['close'] * q['vol'])
    q = q.dropna(subset=['fwd_on'])
    q['yr'] = pd.to_datetime(q.d).dt.year

    def run(df, sel, n, fee_bp=0.0, minliq=0):
        rows = []
        for d, day in df.groupby('d'):
            day = day[day.dollar >= minliq] if minliq else day
            day = day.dropna(subset=[sel])
            if len(day) < 20: continue
            pick = day.nlargest(n, sel)
            rows.append(dict(d=d, r=pick.fwd_on.mean() - fee_bp / 1e4))
        if not rows: return None
        s = pd.DataFrame(rows).set_index('d').sort_index()
        cum = (1 + s.r).cumprod()
        return dict(days=len(s), bp=s.r.mean() * 1e4, sharpe=s.r.mean() / s.r.std() * np.sqrt(252),
                    total=(cum.iloc[-1] - 1) * 100, maxDD=((cum / cum.cummax()) - 1).min() * 100,
                    win=(s.r > 0).mean() * 100, series=s)

    print('\n=== CANDIDATE: top-10 intraday gainers, bought at close, sold at open ===')
    print('\n  a) YEAR BY YEAR (gross)')
    print(f"{'year':>6}{'days':>6}{'bp/night':>10}{'sharpe':>8}{'total%':>9}{'maxDD%':>9}{'win%':>7}")
    for y in (2024, 2025, 2026):
        r = run(q[q.yr == y], 'id', 10)
        if r: print(f"{y:>6}{r['days']:>6}{r['bp']:>10.2f}{r['sharpe']:>8.2f}{r['total']:>9.1f}{r['maxDD']:>9.1f}{r['win']:>7.1f}")
    base = run(q, 'id', 10)
    print(f"{'ALL':>6}{base['days']:>6}{base['bp']:>10.2f}{base['sharpe']:>8.2f}{base['total']:>9.1f}{base['maxDD']:>9.1f}{base['win']:>7.1f}")

    print('\n  b) AFTER OUR REAL FEE (flat $2 round trip; 96% of our trades hit the $1 minimum)')
    for nom, plan, fee in [(1400, 'FIXED', 2.00), (2000, 'FIXED', 2.00), (2000, 'TIERED', 0.70),
                           (5000, 'FIXED', 2.00), (5000, 'TIERED', 0.70)]:
        fb = fee / nom * 1e4
        r = run(q, 'id', 10, fee_bp=fb)
        print(f"    ${nom:>5,} {plan:<7} fee {fb:5.1f}bp -> {r['bp']:+6.2f}bp/night  "
              f"Sharpe {r['sharpe']:+5.2f}  total {r['total']:+8.1f}%  maxDD {r['maxDD']:6.1f}%")

    print('\n  c) CONCENTRATION — drop the best days and see what is left (gross)')
    s = base['series'].r.sort_values(ascending=False)
    for k in (0, 1, 5, 10, 25):
        keep = base['series'].r.drop(s.index[:k]) if k else base['series'].r
        cum = (1 + keep).cumprod()
        print(f"    drop best {k:>2} nights of {len(s)}: {keep.mean()*1e4:+6.2f}bp  total {(cum.iloc[-1]-1)*100:+8.1f}%")

    print('\n  d) IS IT JUST VOLATILITY? sort on vol20 instead, and on intraday-gain WITHIN vol terciles')
    rv = run(q, 'vol20', 10)
    print(f"    pure vol sort            : {rv['bp']:+6.2f}bp  Sharpe {rv['sharpe']:+5.2f}")
    q['vt'] = q.groupby('d')['vol20'].transform(lambda s: pd.qcut(s, 3, labels=False, duplicates='drop'))
    for t in (0, 1, 2):
        r = run(q[q.vt == t], 'id', 5)
        if r: print(f"    intraday-gain sort, vol tercile {t}: {r['bp']:+6.2f}bp  Sharpe {r['sharpe']:+5.2f}  days {r['days']}")

    print('\n  e) LIQUIDITY FLOOR — does it survive if we only touch names we can actually fill?')
    for liq in (0, 5e6, 2e7, 5e7):
        r = run(q, 'id', 10, minliq=liq)
        if r: print(f"    min ${liq/1e6:>4.0f}M daily dollar volume: {r['bp']:+6.2f}bp  Sharpe {r['sharpe']:+5.2f}  days {r['days']}")


if __name__ == '__main__' and '--stress' in sys.argv:
    stress(build_panel())


def verdict(p):
    """The honest number: 2026 only, tradeable liquidity, our real fee, our real size.
    Everything above flatters the strategy by averaging in 2024 and by trading names we
    could not fill."""
    q = p.sort_values(['symbol', 'd']).copy()
    g = q.groupby('symbol')
    q['fwd_on'] = g['on'].shift(-1)
    q['gapcons'] = g['on'].transform(lambda s: (s > 0).rolling(30).mean())
    q['dollar'] = q['close'] * q['vol']
    q['adv'] = g.apply(lambda x: (x['close'] * x['vol']).rolling(20).mean(), include_groups=False).reset_index(level=0, drop=True)
    q = q.dropna(subset=['fwd_on'])
    q['yr'] = pd.to_datetime(q.d).dt.year

    def run(df, sel, n, fee_bp, minadv):
        rows = []
        for d, day in df.groupby('d'):
            day = day[day.adv >= minadv].dropna(subset=[sel])
            if len(day) < 15: continue
            rows.append(dict(d=d, r=day.nlargest(n, sel).fwd_on.mean() - fee_bp / 1e4))
        if len(rows) < 30: return None
        s = pd.DataFrame(rows).set_index('d').sort_index()
        cum = (1 + s.r).cumprod()
        return dict(bp=s.r.mean() * 1e4, sharpe=s.r.mean() / s.r.std() * np.sqrt(252),
                    total=(cum.iloc[-1] - 1) * 100, maxDD=((cum / cum.cummax()) - 1).min() * 100,
                    days=len(s))

    print('\n=== THE HONEST NUMBER: 2026 only, $20M ADV floor, our real fee ===')
    print(f"{'signal':<26}{'size':>8}{'plan':>8}{'fee bp':>8}{'net bp':>9}{'sharpe':>8}{'2026 %':>9}{'maxDD':>8}")
    sub = q[q.yr == 2026]
    for sel, lab in [('id', "today's intraday gainers"), ('gapcons', 'gap consistency (Clockwork)')]:
        for nom, plan, fee in [(1400, 'FIXED', 2.00), (2000, 'FIXED', 2.00),
                               (2000, 'TIERED', 0.70), (5000, 'TIERED', 0.70)]:
            fb = fee / nom * 1e4
            r = run(sub, sel, 10, fb, 2e7)
            if r:
                print(f"{lab:<26}{nom:>8,}{plan:>8}{fb:>8.1f}{r['bp']:>+9.2f}{r['sharpe']:>+8.2f}"
                      f"{r['total']:>+9.1f}{r['maxDD']:>8.1f}")

    print('\n=== and the same signals across all three years, $20M floor, gross ===')
    print(f"{'signal':<26}{'2024':>10}{'2025':>10}{'2026':>10}")
    for sel, lab in [('id', "today's intraday gainers"), ('gapcons', 'gap consistency (Clockwork)')]:
        cells = []
        for y in (2024, 2025, 2026):
            r = run(q[q.yr == y], sel, 10, 0.0, 2e7)
            cells.append(f"{r['bp']:+.1f}bp" if r else '  n/a')
        print(f"{lab:<26}" + ''.join(f'{c:>10}' for c in cells))


if __name__ == '__main__' and '--verdict' in sys.argv:
    verdict(build_panel())


def config(p):
    """Clockwork already trades this window with this signal. What configuration does the
    data actually support -- how many names, what size, and does the fee plan decide it?"""
    q = p.sort_values(['symbol', 'd']).copy()
    g = q.groupby('symbol')
    q['fwd_on'] = g['on'].shift(-1)
    q['gapcons'] = g['on'].transform(lambda s: (s > 0).rolling(30).mean())
    q['adv'] = g.apply(lambda x: (x['close'] * x['vol']).rolling(20).mean(), include_groups=False).reset_index(level=0, drop=True)
    q = q.dropna(subset=['fwd_on', 'gapcons', 'adv'])
    q = q[q.adv >= 2e7]

    def run(n, fee_bp=0.0, yrs=None):
        df = q if yrs is None else q[pd.to_datetime(q.d).dt.year.isin(yrs)]
        rows = []
        for d, day in df.groupby('d'):
            if len(day) < 15: continue
            rows.append(dict(d=d, r=day.nlargest(n, 'gapcons').fwd_on.mean() - fee_bp / 1e4))
        s = pd.DataFrame(rows).set_index('d').sort_index()
        cum = (1 + s.r).cumprod()
        return dict(bp=s.r.mean() * 1e4, sharpe=s.r.mean() / s.r.std() * np.sqrt(252),
                    maxDD=((cum / cum.cummax()) - 1).min() * 100, days=len(s))

    print('\n=== HOW MANY NAMES? (gap-consistency sort, $20M ADV floor, gross) ===')
    print(f"{'names':>7}{'bp/night':>10}{'sharpe':>8}{'maxDD%':>9}   2026 only")
    for n in (5, 10, 15, 20, 30, 50):
        a, b = run(n), run(n, yrs=[2026])
        print(f"{n:>7}{a['bp']:>10.2f}{a['sharpe']:>8.2f}{a['maxDD']:>9.1f}   {b['bp']:+.1f}bp / Sharpe {b['sharpe']:+.2f}")

    print('\n=== THE FEE DECIDES IT — gap consistency, 10 names, 2026 only ===')
    print(f"{'position $':>12}{'FIXED net':>12}{'TIERED net':>12}   verdict")
    gross = run(10, yrs=[2026])['bp']
    print(f"   gross edge {gross:+.2f}bp/night\n")
    for nom in (1000, 1400, 2000, 3000, 5000):
        f_fix, f_tier = 2.00 / nom * 1e4, 0.70 / nom * 1e4
        nf, nt = gross - f_fix, gross - f_tier
        v = 'fee eats it' if nf <= 2 else ('marginal' if nf < 8 else 'viable on either plan')
        print(f"{nom:>12,}{nf:>+12.2f}{nt:>+12.2f}   {v}")
    print("\n  Clockwork runs 10 x ~$1,000 on the FIXED model — the top row.")


if __name__ == '__main__' and '--config' in sys.argv:
    config(build_panel())
