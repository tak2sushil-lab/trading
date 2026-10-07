"""Day Owl leak / tradeability check — does the intraday edge survive entering AFTER the opening print? (Oct 5 2026)

research_ml_dayowl.py found a strong-looking intraday ranking (IC +0.044, top-3 +35bp/day) when the model
knows today's opening gap. Danger: the SAME opening price appears in the input (gap = open/prev close) and
in the outcome (close/open). Any noise in that one print (an off-auction first trade, a bid/ask print)
manufactures "gap-ups fade, gap-downs recover" without any tradeable edge — errors-in-variables reversal,
the daily cousin of the 30-min bid-ask bounce (registry A8).

Test: keep the model's 09:30 ranking exactly as is, but measure the outcome from LATER prices taken from an
independent source (our bars_5m), exit at the last 5-min bar's close from the same source:
  open (bars_5m 09:30 bar open) · 09:35 · 09:40 · 09:45 · 10:00 · 10:30.
If the edge is real and reachable by the day trader it survives a 5-15 minute delay; if it lives in the print,
it collapses at 09:35. Window 2024-01 → today (bars_5m prices are clean there; volume is not used).
Usage: venv/bin/python research_dayowl_delay.py
"""
import os, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
import collect_bars as CB

OUT = os.path.join(ROOT, 'research_out')
CACHE = os.path.join(OUT, 'dayowl_intraday_prices.parquet')
TIMES = {'o0930': ('09:30', 'open'), 'p0935': ('09:30', 'close'), 'p0940': ('09:35', 'close'),
         'p0945': ('09:40', 'close'), 'p1000': ('09:55', 'close'), 'p1030': ('10:25', 'close')}


def tstat(x):
    x = pd.Series(x).dropna()
    return x.mean() / (x.std() / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def prices(syms):
    if os.path.exists(CACHE):
        return pd.read_parquet(CACHE)
    out = []
    for i, s in enumerate(syms):
        b = CB.load_bars(s, start='2024-01-01', end='2026-10-07')
        if b.empty:
            continue
        b = b.between_time('09:30', '15:55')
        b['t'] = b.index.strftime('%H:%M')
        b['date'] = pd.to_datetime(b.index.date)
        g = b.groupby('date')
        row = pd.DataFrame({'last': g['close'].last(), 'last_t': g['t'].last(), 'n': g.size()})
        for k, (t, col) in TIMES.items():
            x = b[b.t == t].set_index('date')[col]
            row[k] = x[~x.index.duplicated(keep='last')]
        row['symbol'] = s
        out.append(row.reset_index())
        if i % 40 == 0:
            print(f'  bars {i}/{len(syms)}', flush=True)
    p = pd.concat(out, ignore_index=True)
    p.to_parquet(CACHE, index=False)
    return p


def main():
    pr = pd.read_parquet(os.path.join(OUT, 'dayowl_preds.parquet'))
    pr = pr[pr.m_date >= '2024-01-02'].rename(columns={'m_date': 'date', 'm_symbol': 'symbol'})
    px = prices(sorted(pr.symbol.unique()))
    q = pr.merge(px, on=['date', 'symbol'], how='inner')
    q = q[(q.n >= 60) & (q.last_t == '15:55')]           # full sessions only
    for k in TIMES:
        q['r_' + k] = (q['last'] / q[k] - 1).clip(-0.5, 0.5)
    q['first5'] = (q.p0935 / q.o0930 - 1)
    q = q.dropna(subset=['r_' + k for k in TIMES])
    print(f'{len(q):,} stock-days, {q.date.nunique()} days, {q.symbol.nunique()} symbols ({q.date.min().date()} → {q.date.max().date()})')
    d = (q.y_id_today - q.r_o0930) * 1e4
    print(f'source check — yfinance open→close minus bars_5m open→close: median {d.median():+.1f}bp, '
          f'mean |diff| {d.abs().mean():.1f}bp, 90th pct |diff| {d.abs().quantile(.9):.1f}bp, corr {q.y_id_today.corr(q.r_o0930):.3f}')
    print(f'first 5 minutes (09:30 open → 09:35): corr with model score {q.pred.corr(q.first5, method="spearman"):+.4f}')
    tgts = [('yfinance official open', 'y_id_today')] + [(f'bars {k[1:3]}:{k[3:]}' if k != 'o0930' else 'bars 09:30 open', 'r_' + k) for k in TIMES]
    print(f'\n{"entry":24s} {"IC":>8s} {"t":>6s} | {"top-3 raw":>9s} {"excess":>7s} {"t":>6s} | {"top-10 raw":>10s} {"excess":>7s} | '
          f'{"bot-3 raw":>9s} {"L-S 3":>7s} {"t":>6s} | top-3 excess by year')
    for lab, col in tgts:
        x = q.dropna(subset=[col])
        dm = x.groupby('date')[col].transform('mean')
        x = x.assign(xs=x[col] - dm)
        ic = x.groupby('date').apply(lambda g: g.pred.rank().corr(g[col].rank())).dropna()
        res = {}
        for k in (3, 10):
            top = x.sort_values('pred', ascending=False).groupby('date').head(k)
            bot = x.sort_values('pred').groupby('date').head(k)
            res[k] = (top.groupby('date')[col].mean() * 1e4, top.groupby('date')['xs'].mean() * 1e4, bot.groupby('date')[col].mean() * 1e4)
        t3, e3, b3 = res[3]; t10, e10, _ = res[10]
        ls = (t3 - b3).dropna()
        yr = e3.groupby(e3.index.year).mean()
        print(f'{lab:24s} {ic.mean():+8.4f} {tstat(ic):+6.2f} | {t3.mean():+9.1f} {e3.mean():+7.1f} {tstat(e3):+6.2f} | {t10.mean():+10.1f} '
              f'{e10.mean():+7.1f} | {b3.mean():+9.1f} {ls.mean():+7.1f} {tstat(ls):+6.2f} | ' + ' '.join(f'{a}:{b:+.0f}' for a, b in yr.items()))


if __name__ == '__main__':
    main()
