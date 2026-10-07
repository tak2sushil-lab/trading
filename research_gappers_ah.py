"""After-hours → next morning: can the EVENING (16:00-20:00) tell us tomorrow's top gainers, and is there money
left after it does? (Oct 5 2026, RESEARCH_REGISTRY §J)

SDEV's Monday gap started in Friday's after-hours (+9…+23% by 16:00-17:00); XRPN's only appeared in Monday's
pre-market. This measures, over two years and every liquid US common stock:
  1. how much of tomorrow's opening gap is already visible in the 20:00 after-hours price (and at 09:00 pre-market);
  2. what buying at the after-hours price (≈20:00) or the 09:00 pre-market price earns into the open, and then
     into the close — i.e. is anything LEFT after the news is out?
Data: yfinance 60-minute bars with extended hours (2 years max), every name with close ≥ $2 and median dollar
volume ≥ $1M in research_out/broad_daily_2024.parquet. Prices only — yfinance reports no extended-hours volume,
so an after-hours bar's close can be a stale trade. All prices within one source (hourly), so adjustments cancel.
Points used: C = 15:30-bar close (≈16:00 close) · AH = 19:00-bar close (last trade by 20:00) · PM = 09:00-bar
close (≈09:30 pre-market) · O = 09:30-bar open (first regular trade) · C1 = next 15:30-bar close.
⚠️ Survivorship (listed today) and stale extended-hours prints both flatter results; spreads after hours are wide
(often 1-5% on small caps) and are NOT charged here — a result must clear them to matter.
Usage: venv/bin/python research_gappers_ah.py
"""
import os, sys, time, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, 'research_out')
RAW = os.path.join(OUT, 'broad_hourly_ext.parquet')
PTS = os.path.join(OUT, 'ah_points.parquet')


def tstat(x):
    x = pd.Series(x).dropna()
    return x.mean() / (x.std() / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def pick_symbols():
    d = pd.read_parquet(os.path.join(OUT, 'broad_daily_2024.parquet'))
    d = d[d.date >= d.date.max() - pd.Timedelta(days=730)]
    d['dv'] = d.close * d.volume
    s = d.groupby('symbol').agg(px=('close', 'median'), dv=('dv', 'median'))
    return sorted(s[(s.px >= 2) & (s.dv >= 1e6)].index)


def download(syms):
    if os.path.exists(RAW):
        return pd.read_parquet(RAW)
    import yf_cache_fix  # noqa: F401
    import yfinance as yf
    frames, t0 = [], time.time()
    for i in range(0, len(syms), 60):
        chunk = syms[i:i + 60]
        for attempt in range(3):
            try:
                h = yf.download(chunk, period='730d', interval='60m', prepost=True, progress=False, group_by='ticker', threads=True)
                break
            except Exception as e:
                print('retry', e, flush=True); time.sleep(15)
        for s in chunk:
            try:
                x = h[s].dropna(how='all')
            except Exception:
                continue
            if x.empty:
                continue
            x = x.rename(columns=str.lower)[['open', 'close']]
            x.index = x.index.tz_convert('America/New_York').tz_localize(None)
            x['symbol'] = s; x.index.name = 'ts'
            frames.append(x.reset_index())
        print(f'  {min(i + 60, len(syms))}/{len(syms)} {time.time() - t0:.0f}s', flush=True)
    r = pd.concat(frames, ignore_index=True)
    r.to_parquet(RAW, index=False)
    return r


def points(r):
    if os.path.exists(PTS):
        return pd.read_parquet(PTS)
    r['date'] = r.ts.dt.normalize(); r['hm'] = r.ts.dt.strftime('%H:%M')
    piv = {}
    for k, (hm, col) in {'C': ('15:30', 'close'), 'AH': ('19:00', 'close'), 'AH16': ('16:00', 'close'),
                         'PM': ('09:00', 'close'), 'PM4': ('04:00', 'open'), 'O': ('09:30', 'open')}.items():
        x = r[r.hm == hm].drop_duplicates(['symbol', 'date'], keep='last').set_index(['symbol', 'date'])[col]
        piv[k] = x
    p = pd.DataFrame(piv).reset_index().sort_values(['symbol', 'date'])
    g = p.groupby('symbol')
    for k in ('PM', 'PM4', 'O', 'C'):
        p[k + '_n'] = g[k].shift(-1)
    p['next_date'] = g['date'].shift(-1)
    p = p[(p.next_date - p.date).dt.days <= 4]
    p.to_parquet(PTS, index=False)
    return p


def main():
    t0 = time.time()
    syms = pick_symbols()
    print(f'{len(syms)} liquid names', flush=True)
    p = points(download(syms))
    p = p.dropna(subset=['C', 'AH', 'O_n', 'C_n'])
    p['ah'] = p.AH / p.C - 1                 # after-hours move by 20:00
    p['gap'] = p.O_n / p.C - 1               # tomorrow's opening gap
    p['ah_to_open'] = p.O_n / p.AH - 1       # buy at the after-hours price, sell at the open
    p['pm_to_open'] = p.O_n / p.PM_n - 1     # buy at the 09:00 pre-market price, sell at the open
    p['open_to_close'] = p.C_n / p.O_n - 1
    p['ah_to_close'] = p.C_n / p.AH - 1
    p = p[(p.gap.abs() < 2) & (p.ah.abs() < 2)]
    p['grank'] = p.groupby('next_date')['gap'].rank(ascending=False, method='first')
    print(f'{len(p):,} stock-nights, {p.symbol.nunique()} names, {p.date.min().date()} → {p.date.max().date()}  ({time.time() - t0:.0f}s)\n')

    print('1. HOW MUCH OF TOMORROW\'S GAP IS ALREADY VISIBLE?')
    print(f'   corr(after-hours move by 20:00, next gap) {p.ah.corr(p.gap):.3f}   corr(pre-market 09:00 move, next gap) '
          f'{(p.PM_n / p.C - 1).corr(p.gap):.3f}')
    top = p[p.grank <= 10]
    big = p[p.gap >= 0.10]
    for lab, s in [('morning top-10 gappers', top), ('all gaps ≥ +10%', big)]:
        print(f'   {lab}: n={len(s):,} | share already ≥ +5% after hours: {(s.ah >= .05).mean():.0%} | ≥ +2%: {(s.ah >= .02).mean():.0%} | '
              f'flat or down after hours (gap came later): {(s.ah <= .005).mean():.0%} | median AH move {s.ah.median():+.1%} vs median gap {s.gap.median():+.1%}')
    print('\n2. BUYING AFTER THE NEWS IS OUT — what is left (gross, no spread charged)')
    print(f'   {"set":44s} {"n":>6s} | {"AH→open":>9s} {"%up":>5s} {"t(night)":>8s} | {"PM→open":>8s} | {"open→close":>10s} {"median":>7s} | {"AH→close":>9s}')
    sets = [('after-hours ≥ +5%', p[p.ah >= .05]), ('after-hours ≥ +10%', p[p.ah >= .10]), ('after-hours ≥ +20%', p[p.ah >= .20]),
            ('after-hours +2-5%', p[(p.ah >= .02) & (p.ah < .05)]), ('after-hours ≤ −5%', p[p.ah <= -.05]),
            ('all stock-nights', p), ('morning top-10 gappers (hindsight)', top)]
    for lab, s in sets:
        night = s.groupby('date')['ah_to_open'].mean()
        print(f'   {lab:44s} {len(s):6,d} | {s.ah_to_open.mean() * 1e4:+8.0f}bp {(s.ah_to_open > 0).mean():5.0%} {tstat(night):+8.2f} | '
              f'{s.pm_to_open.mean() * 1e4:+7.0f}bp | {s.open_to_close.mean() * 1e4:+9.0f}bp {s.open_to_close.median() * 1e4:+6.0f}bp | '
              f'{s.ah_to_close.mean() * 1e4:+8.0f}bp')
    s = p[p.ah >= .05]
    print('\n   after-hours ≥ +5%, by half-year (AH→open bp / open→close bp / n):')
    hy = s.groupby(s.date.dt.year.astype(str) + 'H' + ((s.date.dt.month > 6) + 1).astype(str))
    print('   ' + ' '.join(f'{k}:{g.ah_to_open.mean() * 1e4:+.0f}/{g.open_to_close.mean() * 1e4:+.0f}/{len(g)}' for k, g in hy))
    by_px = s.assign(b=pd.cut(s.C, [0, 5, 20, 100, 1e9], labels=['<$5', '$5-20', '$20-100', '$100+']))
    print('   by price: ' + ' | '.join(f'{k}: AH→open {g.ah_to_open.mean() * 1e4:+.0f}bp o→c {g.open_to_close.mean() * 1e4:+.0f}bp n={len(g)}'
                                       for k, g in by_px.groupby('b')))
    print('\n3. THE GAP THAT FORMS OVERNIGHT (flat after hours, then gaps ≥ +10% — XRPN Monday): what the evening showed')
    late = p[(p.ah.abs() <= .02) & (p.gap >= .10)]
    print(f'   n={len(late):,} ({len(late) / max(len(big), 1):.0%} of all ≥10% gaps) | weekend nights {(late.next_date.dt.dayofweek == 0).mean():.0%} '
          f'(all nights {(p.next_date.dt.dayofweek == 0).mean():.0%}) | then open→close {late.open_to_close.mean() * 1e4:+.0f}bp median '
          f'{late.open_to_close.median() * 1e4:+.0f}bp')
    p.to_parquet(os.path.join(OUT, 'ah_study.parquet'), index=False)
    print(f'\ndone {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
