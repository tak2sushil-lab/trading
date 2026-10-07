"""Macro nights on TRUE release dates (Oct 6 2026, RESEARCH_REGISTRY §M7b).

§M7 tagged release mornings from 08:30 S&P-futures volume spikes, which only work from 2021 and missed most pre-2021 CPI
mornings (CPI did not move markets then). This re-tests with the real calendar now stored in trades.db macro_calendar
(Nasdaq economic calendar + Fed FOMC schedule, 2018 → today, macro_calendar.py --backfill).

A night = hold from session D's close to the next session's open. Tags (first match): FOMC decision on the NEXT session
(pre-FOMC) · CPI / payrolls / PPI / retail sales / GDP / PCE released before the next open · FOMC decision on D
(post-FOMC) · normal. Scored as the night's equal-weight overnight return of (a) every name and the WILD basket in our
237-name universe 2018-2026 (research_out/portfolio_lab_nights.parquet) and (b) every WILD name in the broad universe
2020-2026 (research_out/nightowl_broad_preds.parquet). 2018-2020 is reported separately: the M7 finding was discovered
on 2021-26, so earlier years are the out-of-sample check.
Usage: venv/bin/python research_macro_nights.py
"""
import os, sqlite3, sys
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'research_out')
ORDER = ['pre-FOMC', 'CPI', 'payrolls', 'PPI', 'retail sales', 'GDP', 'PCE', 'post-FOMC', 'normal']


def calendar():
    c = sqlite3.connect(os.path.join(ROOT, 'trades.db'))
    e = pd.read_sql("SELECT event_date, time_et, event, category, source FROM macro_calendar WHERE importance='HIGH'", c)
    c.close()
    e['event_date'] = pd.to_datetime(e.event_date)
    def kind(r):
        n = r.event
        if r.category == 'FED' and ('Rate Decision' in n or 'Interest Rate' in n): return 'FOMC'
        if n in ('CPI', 'Core CPI'): return 'CPI'
        if n.startswith('Nonfarm') or n == 'Unemployment Rate': return 'payrolls'
        if n in ('PPI', 'Core PPI'): return 'PPI'
        if 'Retail Sales' in n: return 'retail sales'
        if n.startswith('GDP'): return 'GDP'
        if 'PCE' in n: return 'PCE'
        return None
    e['kind'] = e.apply(kind, axis=1)
    return e.dropna(subset=['kind'])


def tag_nights(sessions: pd.DatetimeIndex, cal: pd.DataFrame) -> pd.Series:
    s = list(sessions); tags = pd.Series('normal', index=sessions)
    fomc = set(cal.loc[cal.kind == 'FOMC', 'event_date'])
    morning = cal[(cal.kind != 'FOMC') & (cal.time_et < '09:30')]
    by_day = morning.groupby('event_date').kind.apply(set).to_dict()
    for i in range(len(s) - 1):
        d, n = s[i], s[i + 1]
        if n in fomc:
            tags[d] = 'pre-FOMC'; continue
        ks = by_day.get(n, set())
        for k in ['CPI', 'payrolls', 'PPI', 'retail sales', 'GDP', 'PCE']:
            if k in ks:
                tags[d] = k; break
        else:
            if d in fomc:
                tags[d] = 'post-FOMC'
    return tags


def report(series: pd.Series, tags: pd.Series, label: str):
    x = pd.DataFrame({'r': series * 1e4, 't': tags.reindex(series.index)}).dropna()
    print(f'\n{label} ({x.index.min().date()} → {x.index.max().date()}, {len(x)} nights), bp per night')
    norm = x[x.t == 'normal'].r
    for period, m in [('ALL', slice(None)), ('2018-2020 (out of sample for M7)', x.index < '2021-01-01'), ('2021-2026', x.index >= '2021-01-01')]:
        y = x[m] if not isinstance(m, slice) else x
        if len(y) == 0:
            continue
        nn = y[y.t == 'normal'].r
        row = []
        for k in ORDER[:-1]:
            s = y[y.t == k].r
            if len(s) < 5:
                continue
            d = s.mean() - nn.mean(); se = np.sqrt(s.var() / len(s) + nn.var() / len(nn))
            row.append(f'{k} {s.mean():+.0f} (n {len(s)}, t {d / se:+.1f})')
        ev = y[y.t != 'normal']
        share = ev.r.sum() / y.r.sum() if y.r.sum() != 0 else np.nan
        print(f'  {period:34s} normal {nn.mean():+.1f} · ' + ' · '.join(row) +
              f' | release nights = {len(ev) / len(y):.0%} of nights, {share:.0%} of the return')


def main():
    cal = calendar()
    print(f'calendar: {len(cal):,} HIGH events, {cal.event_date.min().date()} → {cal.event_date.max().date()}; '
          f'FOMC {cal[cal.kind == "FOMC"].event_date.nunique()}, CPI {cal[cal.kind == "CPI"].event_date.nunique()}, '
          f'payrolls {cal[cal.kind == "payrolls"].event_date.nunique()} days')
    N = pd.read_parquet(os.path.join(OUT, 'portfolio_lab_nights.parquet'))
    tags = tag_nights(N.index, cal)
    report(N.ALL_EW_ON, tags, 'OUR 237 NAMES — every name overnight')
    report(N.WILD_EW, tags, 'OUR 237 NAMES — WILD basket overnight')
    report((N.CW + N.NO) / 2, tags, 'OUR 237 NAMES — Clockwork + Night Owl picks')
    B = pd.read_parquet(os.path.join(OUT, 'nightowl_broad_preds.parquet'))
    B['date'] = pd.to_datetime(B.date)
    w = B[B.wild].groupby('date').y_on.mean()
    report(w, tag_nights(w.index, cal), 'BROAD UNIVERSE — WILD basket overnight')
    tags.to_frame('tag').to_parquet(os.path.join(OUT, 'macro_night_tags.parquet'))


if __name__ == '__main__':
    main()
