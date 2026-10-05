"""Earnings research with REAL report dates and EPS surprises (Oct 4 2026 — docs/RESEARCH_REGISTRY.md).

The factory's Aug 15 "Earnings Drift" engine had no earnings data and used a ≥5% gap as a stand-in (it
failed both OOS windows). yfinance now gives actual report timestamps (16:00 = after the close,
07:00 = before the open) with EPS estimate / reported / surprise %, back to 2014 for most names.

Tests, on official daily prints (research_out/yf_daily_2015.parquet, auto-adjusted), market-neutral
(return minus the universe's mean over the same window):
  1. Post-earnings drift (PEAD): day 0 = first session that can react to the report. Entry at day-0 CLOSE
     (after the reaction is known), hold 5 / 10 / 20 sessions. Sorted by EPS surprise and by the earnings-
     announcement return (EAR: prior close → day-0 close; Brandt et al. 2008 find EAR beats SUE).
  2. Pre-earnings premium (Frazzini-Lamont 2007): buy the close 6 sessions before day 0, sell the close
     the session before day 0 (never holding through the report).
Every result per year 2015-2026 and with day-clustered t. Surprise % is clipped at ±100 (it explodes
when the estimate is near zero).

Usage: venv/bin/python research_earnings.py
"""
import os, sys, time, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, 'research_out')
EARN = os.path.join(OUT, 'earnings_dates.parquet')
DAILY = os.path.join(OUT, 'yf_daily_2015.parquet')


def fetch(symbols):
    if os.path.exists(EARN):
        return pd.read_parquet(EARN)
    import yf_cache_fix  # noqa: F401
    import yfinance as yf
    rows = []
    for i, s in enumerate(symbols):
        try:
            e = yf.Ticker(s).get_earnings_dates(limit=60)
        except Exception:
            e = None
        if e is not None and len(e):
            e = e.reset_index().rename(columns={'Earnings Date': 'ts', 'EPS Estimate': 'est',
                                                'Reported EPS': 'eps', 'Surprise(%)': 'surprise'})
            e['symbol'] = s
            rows.append(e[['symbol', 'ts', 'est', 'eps', 'surprise']])
        if i % 40 == 0:
            print(f'  earnings {i}/{len(symbols)}', flush=True)
        time.sleep(0.2)
    d = pd.concat(rows, ignore_index=True)
    d['ts'] = pd.to_datetime(d.ts, utc=True).dt.tz_convert('America/New_York')
    d.to_parquet(EARN, index=False)
    return d


def main():
    px = pd.read_parquet(DAILY)
    syms = sorted(px.symbol.unique())
    ed = fetch(syms)
    ed = ed.dropna(subset=['eps'])                                  # reported events only
    ed = ed[ed.ts.dt.tz_localize(None) < pd.Timestamp.today()]
    # price matrices
    C = px.pivot(index='date', columns='symbol', values='close').sort_index()
    days = C.index
    mkt = C.pct_change()
    def fwd(i0, i1):                                                # close[i0] → close[i1], per symbol
        return C.iloc[i1] / C.iloc[i0] - 1
    rows = []
    for r in ed.itertuples():
        t = r.ts
        d0 = t.normalize().tz_localize(None)
        # after the close (≥ 16:00) or unknown time at midnight → first session AFTER that date
        if t.hour >= 16 or (t.hour == 0 and t.minute == 0):
            pos = days.searchsorted(d0, side='right')
        else:                                                       # pre-market / during session → same day
            pos = days.searchsorted(d0, side='left')
        if pos < 7 or pos + 21 >= len(days) or r.symbol not in C.columns:
            continue
        s = r.symbol
        c = C[s].values
        if np.isnan(c[pos - 7:pos + 21]).any():
            continue
        def xs(a, b):                                               # stock minus universe mean, close[a]→close[b]
            all_ = (C.iloc[b] / C.iloc[a] - 1).dropna()
            return (c[b] / c[a] - 1) - all_.mean()
        rows.append(dict(symbol=s, day0=days[pos], year=days[pos].year,
                         surprise=np.clip(r.surprise, -100, 100) if pd.notna(r.surprise) else np.nan,
                         ear=xs(pos - 1, pos),                       # announcement reaction, market-adjusted
                         pre=xs(pos - 6, pos - 1),                   # pre-earnings window
                         d5=xs(pos, pos + 5), d10=xs(pos, pos + 10), d20=xs(pos, pos + 20)))
    ev = pd.DataFrame(rows)
    ev.to_parquet(os.path.join(OUT, 'earnings_events.parquet'), index=False)
    print(f"{len(ev):,} earnings events, {ev.symbol.nunique()} names, {ev.day0.min().date()} → {ev.day0.max().date()}\n")

    def clustered(x, by):
        g = x.groupby(by).mean(); return g.mean(), g.mean() / (g.std() / np.sqrt(len(g))) if len(g) > 2 else np.nan

    print("== 2. PRE-EARNINGS PREMIUM: close t-6 → close t-1 (never through the report), excess vs universe ==")
    m, t = clustered(ev.pre * 100, ev.day0.dt.to_period('M'))
    yr = ev.groupby('year').pre.mean() * 1e4
    print(f"  all events: {ev.pre.mean() * 1e4:+.1f}bp over 5 sessions (t by month {t:+.2f}); win {(ev.pre > 0).mean():.0%}")
    print('  by year (bp): ' + '  '.join(f"{y}:{v:+.0f}" for y, v in yr.items()))

    print("\n== 1. POST-EARNINGS DRIFT, entry at day-0 close, excess vs universe ==")
    for sig in ('ear', 'surprise'):
        e = ev.dropna(subset=[sig]).copy()
        e['q'] = e.groupby('year')[sig].transform(lambda s: pd.qcut(s.rank(method='first'), 5, labels=False))
        tab = e.groupby('q')[['d5', 'd10', 'd20']].mean() * 1e4
        print(f"\n  sorted by {sig.upper()} (quintiles within year; Q4 = best), mean excess drift in bp:")
        print(tab.round(0).to_string())
        top = e[e.q == 4]; bot = e[e.q == 0]
        for h in ('d10', 'd20'):
            m, t = clustered(top[h] * 100, top.day0.dt.to_period('M'))
            ls = (top.groupby('year')[h].mean() - bot.groupby('year')[h].mean()) * 1e4
            print(f"    top quintile {h}: {top[h].mean() * 1e4:+.0f}bp (t by month {t:+.2f}) | top−bottom by year: "
                  + ' '.join(f"{y}:{v:+.0f}" for y, v in ls.items()))


if __name__ == '__main__':
    main()
