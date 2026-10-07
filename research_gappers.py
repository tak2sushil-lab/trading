"""Can we know TONIGHT which stocks will be tomorrow morning's top gainers? (Oct 5 2026, RESEARCH_REGISTRY §J)

User's question: XRPN, SDEV, IBRX topped this morning's pre-market list. If we had known on the previous
evening we would have had an edge. Is the gap predictable from what is visible at the close?

Data: research_out/broad_daily_2024.parquet (every currently-listed US common stock, yfinance daily,
2024-01 → today; built by research_gappers_data.py). ⚠️ Survivorship: delisted pump-and-dumps are missing.

Every evening t, tradeable set = close ≥ $2 (also reported at ≥ $5), 20-day median dollar volume ≥ $1M.
Targets (all FUTURE): gap_n = open(t+1)/close(t)−1 (buy MOC, sell MOO), id_n = close(t+1)/open(t+1)−1
(buy the morning gapper at the open, sell at the close), cc_n = close(t+1)/close(t)−1.
"Top gainer" = gap_n ≥ +10%, or among that morning's 10 largest gaps.

1. What buying the morning's top gappers at the open does (the day trader's natural move).
2. Single evening features: lift of P(tomorrow's gap ≥ +10%) and the mean gap by decile.
3. A walk-forward gradient-boosted model on ~25 evening features: precision of its top-10 picks and the
   money in buying them at the close and selling at the open (gross; break-even cost reported).
4. "Frenzy" names (up ≥ 50% in 3 days at the close — XRPN, SDEV) the next night and next day.
Usage: venv/bin/python research_gappers.py
"""
import os, sys, time, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, 'research_out')
SRC = os.path.join(OUT, 'broad_daily_2024.parquet')
PANEL = os.path.join(OUT, 'gappers_panel.parquet')
FEATS = ['r1', 'r3', 'r5', 'r20', 'gap', 'idr', 'clv', 'close_vs_high', 'rvol', 'rvol5', 'log_dvol', 'log_price',
         'vol20', 'max_r1_20', 'n_gap10_20', 'up_streak', 'hi20_prox', 'hi250_prox', 'age', 'dist_ma20', 'atr_pct',
         'gap_avg20', 'gap_cons20', 'ret_vs_mkt', 'dow']


def log(*a):
    print(*a, flush=True)


def tstat(x):
    x = pd.Series(x).dropna()
    return x.mean() / (x.std() / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def build():
    if os.path.exists(PANEL):
        return pd.read_parquet(PANEL)
    d = pd.read_parquet(SRC).sort_values(['symbol', 'date'])
    d = d[(d.open > 0) & (d.close > 0) & (d.high >= d.low)]
    parts = []
    for s, x in d.groupby('symbol', sort=False):
        x = x.set_index('date')
        o, h, l, c, v = x.open, x.high, x.low, x.close, x.volume
        pc = c.shift(1)
        f = pd.DataFrame(index=x.index)
        r1 = c / pc - 1
        f['r1'] = r1; f['r3'] = c / c.shift(3) - 1; f['r5'] = c / c.shift(5) - 1; f['r20'] = c / c.shift(20) - 1
        gap = o / pc - 1
        f['gap'] = gap; f['idr'] = c / o - 1
        rng = (h - l).replace(0, np.nan)
        f['clv'] = (c - l) / rng; f['close_vs_high'] = c / h - 1
        mv = v.shift(1).rolling(20, min_periods=10).median().replace(0, np.nan)
        f['rvol'] = v / mv; f['rvol5'] = v.rolling(5).mean() / mv
        dv = (c * v).rolling(20, min_periods=10).median()
        f['dvol20'] = dv; f['log_dvol'] = np.log(dv.replace(0, np.nan)); f['log_price'] = np.log(c); f['price'] = c
        f['vol20'] = r1.rolling(20, min_periods=10).std(); f['max_r1_20'] = r1.rolling(20, min_periods=10).max()
        f['n_gap10_20'] = (gap.abs() >= 0.10).rolling(20, min_periods=1).sum()
        up = (r1 > 0).astype(int)
        f['up_streak'] = up.groupby((up != up.shift()).cumsum()).cumsum() * up
        f['hi20_prox'] = c / h.rolling(20, min_periods=5).max()
        f['hi250_prox'] = c / h.rolling(250, min_periods=20).max()
        f['age'] = np.arange(len(x)).clip(max=300)
        f['dist_ma20'] = c / c.rolling(20, min_periods=10).mean() - 1
        tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
        f['atr_pct'] = tr.rolling(14, min_periods=7).mean() / c
        f['gap_avg20'] = gap.rolling(20, min_periods=10).mean(); f['gap_cons20'] = (gap > 0).rolling(20, min_periods=10).mean()
        f['gap_n'] = o.shift(-1) / c - 1
        f['id_n'] = c.shift(-1) / o.shift(-1) - 1
        f['cc_n'] = c.shift(-1) / c - 1
        f['next_date'] = pd.Series(x.index, index=x.index).shift(-1)
        f['symbol'] = s
        parts.append(f.reset_index())
    p = pd.concat(parts, ignore_index=True)
    p['dow'] = p['date'].dt.dayofweek
    # sanity: drop rows with absurd moves (bad prints / unadjusted reverse splits); a next session > 7 days away (halts)
    p = p[(p.gap_n.abs() < 3) & (p.r1.abs() < 5) & (p.gap.abs() < 3)]
    p = p[(p.next_date - p.date).dt.days <= 7]
    p['ret_vs_mkt'] = p['r1'] - p.groupby('date')['r1'].transform('median')
    p.to_parquet(PANEL, index=False)
    return p


def tradeable(p, min_price=2.0):
    return p[(p.price >= min_price) & (p.dvol20 >= 1e6) & p.gap_n.notna() & p.id_n.notna()]


def section1(q, L):
    L.append('1. BUY THE MORNING\'S TOP GAPPERS AT THE OPEN, SELL AT THE CLOSE (day trader\'s natural move)')
    q = q.copy()
    q['grank'] = q.groupby('date')['gap_n'].rank(ascending=False, method='first')
    for lab, sub in [('top-10 gappers each morning', q[q.grank <= 10]), ('all gaps ≥ +10%', q[q.gap_n >= 0.10]),
                     ('gaps +5-10%', q[(q.gap_n >= 0.05) & (q.gap_n < 0.10)]), ('gaps +20%+', q[q.gap_n >= 0.20]),
                     ('everything', q)]:
        yr = sub.groupby(sub.next_date.dt.year)['id_n'].mean() * 1e4
        L.append(f'  {lab:28s} n={len(sub):7,d} gap mean {sub.gap_n.mean() * 100:+6.2f}% | open→close mean {sub.id_n.mean() * 1e4:+7.1f}bp '
                 f'median {sub.id_n.median() * 1e4:+7.1f}bp  %up {(sub.id_n > 0).mean():.1%} | by year ' +
                 ' '.join(f'{k}:{v:+.0f}' for k, v in yr.items()))


def section2(q, L):
    L.append('\n2. ONE EVENING FEATURE AT A TIME → tomorrow\'s gap (lift = P(gap ≥ +10%) in the decile ÷ overall)')
    base = (q.gap_n >= 0.10).mean()
    L.append(f'  base rate P(gap ≥ +10%) = {base:.3%} of stock-nights; mean gap {q.gap_n.mean() * 1e4:+.1f}bp')
    for f in ['r1', 'r3', 'r5', 'clv', 'close_vs_high', 'rvol', 'log_price', 'vol20', 'n_gap10_20', 'gap_avg20', 'gap_cons20', 'age', 'idr']:
        dec = q.groupby('date')[f].rank(pct=True)
        b = pd.cut(dec, [0, .1, .5, .9, .99, 1.0], labels=['bot10', '10-50', '50-90', '90-99', 'top1'])
        t = q.groupby(b).agg(n=('gap_n', 'size'), p10=('gap_n', lambda x: (x >= .10).mean()),
                             gap=('gap_n', 'mean'), idn=('id_n', 'mean'), ccn=('cc_n', 'mean'))
        L.append(f'  {f:13s} ' + ' | '.join(f'{k}: lift {r.p10 / base:4.1f}× gap {r.gap * 1e4:+5.0f}bp then o→c {r.idn * 1e4:+5.0f}bp'
                                              for k, r in t.iterrows()))


def section3(q, L):
    from sklearn.ensemble import HistGradientBoostingRegressor
    L.append('\n3. LEARNED MODEL on 25 evening features → tomorrow\'s gap rank (walk-forward, quarterly re-fit)')
    X = q[FEATS].copy()
    for c in FEATS:
        if c not in ('log_price', 'log_dvol', 'age', 'dow'):
            X[c] = q.groupby('date')[c].rank(pct=True) - 0.5
    y = q.groupby('date')['gap_n'].rank(pct=True) - 0.5
    pred = pd.Series(np.nan, index=q.index)
    qs = pd.date_range('2025-01-01', q.date.max() + pd.offsets.QuarterBegin(1), freq='QS')
    for a, b in zip(qs[:-1], qs[1:]):
        te = (q.date >= a) & (q.date < b)
        tr = q.next_date < a
        if te.sum() == 0 or tr.sum() < 50000:
            continue
        m = HistGradientBoostingRegressor(max_iter=250, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=400,
                                          l2_regularization=1.0, random_state=7)
        m.fit(X[tr], y[tr])
        pred[te] = m.predict(X[te])
    r = q.assign(pred=pred).dropna(subset=['pred'])
    ic = r.groupby('date').apply(lambda d: d.pred.rank().corr(d.gap_n.rank())).dropna()
    L.append(f'  OOS {r.date.min().date()} → {r.date.max().date()} ({r.date.nunique()} nights, ~{len(r) / r.date.nunique():.0f} names/night)')
    L.append(f'  IC with tomorrow\'s gap {ic.mean():+.4f} (t {tstat(ic):+.2f}); by quarter ' +
             ' '.join(f'{k}:{v:+.3f}' for k, v in ic.groupby(ic.index.to_period("Q")).mean().items()))
    base = (r.gap_n >= .10).mean()
    for k in (5, 10, 25):
        top = r.sort_values('pred', ascending=False).groupby('date').head(k)
        avg = r.groupby('date')['gap_n'].mean()
        ex = (top.groupby('date')['gap_n'].mean() - avg).dropna() * 1e4
        g = top.groupby('date')
        on = g['gap_n'].mean() * 1e4; idn = g['id_n'].mean() * 1e4; ccn = g['cc_n'].mean() * 1e4
        hit = (top.gap_n >= .10).mean()
        rk = r.groupby('date')['gap_n'].rank(ascending=False)
        intop10 = (rk.loc[top.index] <= 10).mean()
        L.append(f'  top-{k:<2d}: P(gap ≥10%) {hit:.2%} vs base {base:.2%} ({hit / base:.1f}×) | in morning top-10 {intop10:.1%} '
                 f'(random {10 / (len(r) / r.date.nunique()):.1%}) | overnight {on.mean():+6.1f}bp (excess {ex.mean():+6.1f}, t {tstat(ex):+.2f}, '
                 f'median {top.gap_n.median() * 1e4:+.0f}) | then open→close {idn.mean():+6.1f}bp | close→close {ccn.mean():+6.1f}bp')
        if k == 10:
            yr = on.groupby(on.index.to_period('Q')).mean()
            L.append('        overnight by quarter: ' + ' '.join(f'{a}:{b:+.0f}' for a, b in yr.items()))
            dd = (on / 1e4).cumsum()
            L.append(f'        summed-overnight max drawdown {(dd - dd.cummax()).min() * 100:.1f}% | worst night {on.min():+.0f}bp | '
                     f'best 5% of nights carry {on[on >= on.quantile(.95)].sum() / on.sum():.0%} of the total')
    return r


def section4(q, L):
    L.append('\n4. FRENZY NAMES at the close (XRPN / SDEV pattern) — the next night and the next day')
    for lab, sub in [('up ≥ 50% in 3 days', q[q.r3 >= .5]), ('up ≥ 100% in 3 days', q[q.r3 >= 1.0]),
                     ('up ≥ 30% today', q[q.r1 >= .3]), ('up ≥ 30% today, closed in top 10% of range', q[(q.r1 >= .3) & (q.clv >= .9)]),
                     ('day\'s top-10 gainers at the close', q[q.groupby('date')['r1'].rank(ascending=False, method='first') <= 10])]:
        yr = sub.groupby(sub.date.dt.year)['gap_n'].mean() * 1e4
        L.append(f'  {lab:44s} n={len(sub):6,d} | next gap mean {sub.gap_n.mean() * 1e4:+6.0f}bp median {sub.gap_n.median() * 1e4:+5.0f}bp '
                 f'%up {(sub.gap_n > 0).mean():.0%} P(≥10%) {(sub.gap_n >= .1).mean():.1%} | then open→close {sub.id_n.mean() * 1e4:+6.0f}bp '
                 f'median {sub.id_n.median() * 1e4:+5.0f} | by yr ' + ' '.join(f'{k}:{v:+.0f}' for k, v in yr.items()))


def main():
    t0 = time.time()
    p = build()
    log(f'panel {len(p):,} rows, {p.symbol.nunique()} symbols, {p.date.min().date()} → {p.date.max().date()} ({time.time() - t0:.0f}s)')
    L = []
    for mp in (2.0, 5.0):
        q = tradeable(p, mp)
        L.append(f'\n================ price ≥ ${mp:.0f}, $vol ≥ $1M: {len(q):,} stock-nights, ~{len(q) / q.date.nunique():.0f} names/night ================')
        section1(q, L); section2(q, L)
        r = section3(q, L)
        if mp == 2.0:
            r[['date', 'symbol', 'pred', 'gap_n', 'id_n', 'cc_n', 'price', 'dvol20']].to_parquet(os.path.join(OUT, 'gappers_preds.parquet'), index=False)
        section4(q, L)
        log('\n'.join(L[-60:]))
    open(os.path.join(OUT, 'gappers_report.txt'), 'w').write('\n'.join(L))
    log(f'done {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
