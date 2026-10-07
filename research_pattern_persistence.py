"""Do some stocks reliably RESPECT textbook patterns? (Oct 6 2026, RESEARCH_REGISTRY §L)

User's thesis: many stocks "follow textbook trends, respect indicators, align with FVG / order blocks / breakouts /
Fibonacci" — learn which ones, and trade the patterns only there. That is testable as PERSISTENCE: if respecting a
pattern is a trait of the stock, the stocks that respected it in one period should respect it in the next.

Data: research_out/broad_daily_2018.parquet + broad_daily_2024.parquet (yfinance daily, split/dividend adjusted),
the ~3,200 listed US common stocks with tickers A→OGG that have continuous history 2018-01 → 2026-10.
Tradeable day: close ≥ $5 and 20-day median dollar volume ≥ $5M.

12 patterns, each decided from data up to and including day t's close:
  continuation : BRK20 (first close above the prior 20-day high) · BRK20_VOL (same, volume ≥ 2× normal) ·
                 HI52 (first close above the prior 52-week high) · NR7_BRK (close above a narrow-range-7 day's high) ·
                 GAP_GO (gap up ≥ 3%, closes in the top 30% of the day's range)
  support      : MA50_TOUCH / MA200_TOUCH (low tags a RISING average, close holds above it, was clearly above before) ·
                 FIB_GOLDEN (close in the 50-65% retracement of a ≥3-ATR 60-day up-swing, bullish candle) ·
                 FVG_RETEST (price dips into a recent bullish fair-value gap and closes above its bottom; first retest) ·
                 OB_RETEST (price returns into the last down candle before a ≥2-ATR 3-day impulse; first retest,
                 the block only becomes known when the impulse completes)
  reversion    : RSI2_DIP (RSI(2) < 10 above the 200-day average) · BB_LOW_UP (close below the lower Bollinger band,
                 above the 200-day average)
Repeat signals of the same pattern within 5 days are dropped.

Outcome: buy at the NEXT open, sell at the close 5 sessions later (and 2 sessions, secondary), minus the average of all
tradeable stocks over the same dates (market-neutral). Never the close of the signal day (shared-print trap).
PATTERN ALPHA of a stock in a period = its mean pattern excess return − its mean excess return over ALL its tradeable
days in that period — so a stock that simply rose is not credited with "respecting" a pattern.

A. pooled: does each pattern work at all (8 years, ~3,200 stocks)?
B. persistence: alpha in 2018-01 → 2022-06 vs 2022-07 → 2026-10, across stocks — correlation, and the top quintile of
   "respecters" in period 1 vs the bottom quintile, measured in period 2.
C. the user's strategy, walk-forward: each year, pick the stocks with the best pattern alpha over the previous 2 years
   and trade the pattern only on them; compare with trading it on every stock.
Usage: venv/bin/python research_pattern_persistence.py
"""
import os, sys, time, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, 'research_out')
EVENTS = os.path.join(OUT, 'pattern_events.parquet')
BASE = os.path.join(OUT, 'pattern_base.parquet')
SPLIT = pd.Timestamp('2022-07-01')
PATTERNS = ['BRK20', 'BRK20_VOL', 'HI52', 'NR7_BRK', 'GAP_GO', 'MA50_TOUCH', 'MA200_TOUCH', 'FIB_GOLDEN',
            'FVG_RETEST', 'OB_RETEST', 'RSI2_DIP', 'BB_LOW_UP']


def tstat(x):
    x = pd.Series(x).dropna()
    return x.mean() / (x.std() / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def _rsi(c, n):
    d = np.diff(c, prepend=np.nan)
    up = pd.Series(np.where(d > 0, d, 0.0)).ewm(alpha=1 / n, adjust=False).mean().values
    dn = pd.Series(np.where(d < 0, -d, 0.0)).ewm(alpha=1 / n, adjust=False).mean().values
    with np.errstate(divide='ignore', invalid='ignore'):
        return 100 - 100 / (1 + up / dn)


def _cooldown(sig, k=5):
    out = np.zeros_like(sig)
    last = -10 ** 9
    for i in np.flatnonzero(sig):
        if i - last > k:
            out[i] = True
            last = i
    return out


def _fvg_retest(o, h, l, c, atr):
    n = len(c); ev = np.zeros(n, bool); zones = []          # (top, bottom, created)
    for t in range(2, n):
        keep = []
        for top, bot, k in zones:
            if t - k > 20 or c[t] < bot:                    # expired or broken through
                continue
            if t >= k + 2 and l[t] <= top and c[t] >= bot:
                ev[t] = True                                # first retest: zone is used up
                continue
            keep.append((top, bot, k))
        zones = keep[-5:]
        if l[t] > h[t - 2] and (l[t] - h[t - 2]) >= 0.3 * atr[t]:
            zones.append((l[t], h[t - 2], t))
    return ev


def _ob_retest(o, h, l, c, atr_pct):
    n = len(c); ev = np.zeros(n, bool); zones = []          # (top, bottom, active_from)
    for t in range(3, n):
        keep = []
        for top, bot, a in zones:
            if t - a > 27 or c[t] < bot:
                continue
            if t >= a and l[t] <= top and c[t] >= bot:
                ev[t] = True
                continue
            keep.append((top, bot, a))
        zones = keep[-5:]
        k = t - 3                                           # the impulse that ends today confirms the block at k
        if c[k] < o[k] and np.isfinite(atr_pct[k]) and c[t] / c[k] - 1 >= 2 * atr_pct[k]:
            zones.append((h[k], l[k], t + 1))
    return ev


def build():
    if os.path.exists(EVENTS) and os.path.exists(BASE):
        return pd.read_parquet(EVENTS), pd.read_parquet(BASE)
    a = pd.read_parquet(os.path.join(OUT, 'broad_daily_2018.parquet'))
    b = pd.read_parquet(os.path.join(OUT, 'broad_daily_2024.parquet'))
    syms = set(a.symbol)
    d = pd.concat([a[a.date < b.date.min()], b[b.symbol.isin(syms)]], ignore_index=True).sort_values(['symbol', 'date'])
    d = d[(d.open > 0) & (d.close > 0) & (d.high >= d.low)]
    ev_parts, all_parts = [], []
    t0 = time.time()
    for i, (s, x) in enumerate(d.groupby('symbol', sort=False)):
        if len(x) < 260:
            continue
        dates = x.date.values
        o, h, l, c, v = (x[k].values.astype(float) for k in ('open', 'high', 'low', 'close', 'volume'))
        C = pd.Series(c); H = pd.Series(h); L = pd.Series(l); V = pd.Series(v)
        pc = C.shift(1).values
        tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
        atr = pd.Series(tr).rolling(14).mean().values
        atr_pct = atr / c
        ma20, sd20 = C.rolling(20).mean().values, C.rolling(20).std().values
        ma50, ma200 = C.rolling(50).mean().values, C.rolling(200).mean().values
        ma50_10, ma200_20 = pd.Series(ma50).shift(10).values, pd.Series(ma200).shift(20).values
        hh20 = H.rolling(20).max().shift(1).values
        hh252 = H.rolling(252, min_periods=200).max().shift(1).values
        mv = V.shift(1).rolling(20, min_periods=10).median().values
        rvol = v / mv
        dv = (C * V).rolling(20, min_periods=10).median().values
        rng = h - l
        nr7 = rng <= pd.Series(rng).rolling(7).min().values
        rsi2 = _rsi(c, 2)
        hi60, lo60 = H.rolling(60).max().values, L.rolling(60).min().values
        hi_age = H.rolling(60).apply(lambda z: len(z) - 1 - np.argmax(z), raw=True).values
        lo_age = L.rolling(60).apply(lambda z: len(z) - 1 - np.argmin(z), raw=True).values
        with np.errstate(divide='ignore', invalid='ignore'):
            retr = (hi60 - c) / (hi60 - lo60)
            swing_atr = (hi60 - lo60) / atr
        sh = lambda z, k=1: pd.Series(z).shift(k).values
        sig = {
            'BRK20': (c > hh20) & ~(sh(c) > sh(hh20)),
            'HI52': (c > hh252) & ~(sh(c) > sh(hh252)),
            'NR7_BRK': sh(nr7).astype(bool) & (c > sh(h)),
            'GAP_GO': (o / pc - 1 >= 0.03) & ((c - l) / np.where(rng > 0, rng, np.nan) >= 0.7),
            'MA50_TOUCH': (ma50 > ma50_10) & (l <= ma50 * 1.005) & (c > ma50) & (sh(c, 5) > sh(ma50, 5) * 1.02),
            'MA200_TOUCH': (ma200 > ma200_20) & (l <= ma200 * 1.01) & (c > ma200) & (sh(c, 10) > sh(ma200, 10) * 1.03),
            'FIB_GOLDEN': (hi_age < lo_age) & (swing_atr >= 3) & (retr >= 0.5) & (retr <= 0.65) & (c > o),
            'RSI2_DIP': (rsi2 < 10) & (c > ma200),
            'BB_LOW_UP': (c < ma20 - 2 * sd20) & (c > ma200),
        }
        sig['BRK20_VOL'] = sig['BRK20'] & (rvol >= 2)
        sig['FVG_RETEST'] = _fvg_retest(o, h, l, c, atr)
        sig['OB_RETEST'] = _ob_retest(o, h, l, c, atr_pct)
        # outcome: next open → close k sessions later
        o1 = sh(o, -1)
        f5 = np.clip(sh(c, -5) / o1 - 1, -0.5, 0.5)
        f2 = np.clip(sh(c, -2) / o1 - 1, -0.5, 0.5)
        ok = (c >= 5) & (dv >= 5e6) & np.isfinite(f5)
        all_parts.append(pd.DataFrame({'date': dates[ok], 'symbol': s, 'f5': f5[ok], 'f2': f2[ok]}))
        for p in PATTERNS:
            m = _cooldown(np.nan_to_num(sig[p]).astype(bool)) & ok
            if m.any():
                ev_parts.append(pd.DataFrame({'date': dates[m], 'symbol': s, 'pattern': p, 'f5': f5[m], 'f2': f2[m]}))
        if i % 500 == 0:
            print(f'  {i} symbols {time.time() - t0:.0f}s', flush=True)
    allx = pd.concat(all_parts, ignore_index=True)
    mkt = allx.groupby('date')[['f5', 'f2']].mean().rename(columns={'f5': 'm5', 'f2': 'm2'})
    allx = allx.join(mkt, on='date')
    allx['x5'], allx['x2'] = allx.f5 - allx.m5, allx.f2 - allx.m2
    allx['period'] = np.where(allx.date < SPLIT, 'A', 'B')
    allx['year'] = allx.date.dt.year
    E = pd.concat(ev_parts, ignore_index=True).join(mkt, on='date')
    E['x5'], E['x2'] = E.f5 - E.m5, E.f2 - E.m2
    E['period'] = np.where(E.date < SPLIT, 'A', 'B')
    E['year'] = E.date.dt.year
    base = pd.concat([allx.groupby(['symbol', 'period'])[['x5', 'x2']].mean().assign(kind='period').reset_index(),
                      allx.groupby(['symbol', 'year'])[['x5', 'x2']].mean().assign(kind='year').reset_index()], ignore_index=True)
    E.to_parquet(EVENTS, index=False); base.to_parquet(BASE, index=False)
    print(f'built {len(E):,} pattern events on {allx.symbol.nunique():,} stocks, {allx.date.min().date()} → {allx.date.max().date()} '
          f'({len(allx):,} tradeable stock-days) in {time.time() - t0:.0f}s', flush=True)
    return E, base


def main():
    E, base = build()
    L = []
    # ── A. pooled ──
    L.append('A. DOES EACH PATTERN WORK AT ALL? next open → close 5 sessions later, minus the average stock (bp); '
             't by date; ~20bp is a realistic round-trip cost')
    for p in PATTERNS:
        e = E[E.pattern == p]
        by_d = e.groupby('date').x5.mean() * 1e4
        yr = e.groupby('year').x5.mean() * 1e4
        L.append(f'  {p:12s} n={len(e):7,d} 5d {e.x5.mean() * 1e4:+6.1f}bp (t {tstat(by_d):+.2f}) · 2d {e.x2.mean() * 1e4:+6.1f}bp · '
                 f'years+ {int((yr > 0).sum())}/{len(yr)} · ' + ' '.join(f'{k % 100:02d}:{v:+.0f}' for k, v in yr.items()))
    # ── B. persistence ──
    bp = base[base.kind == 'period'].set_index(['symbol', 'period'])
    L.append('\nB. IS "RESPECTING" A PATTERN A TRAIT? pattern alpha per stock (pattern return − the stock\'s own normal return), '
             '2018-01→2022-06 (A) vs 2022-07→2026-10 (B); stocks with ≥8 events in both')
    persist = {}
    for p in PATTERNS + ['ALL_SUPPORT', 'ALL_CONTINUATION']:
        if p == 'ALL_SUPPORT':
            e = E[E.pattern.isin(['MA50_TOUCH', 'MA200_TOUCH', 'FIB_GOLDEN', 'FVG_RETEST', 'OB_RETEST', 'RSI2_DIP', 'BB_LOW_UP'])]
        elif p == 'ALL_CONTINUATION':
            e = E[E.pattern.isin(['BRK20', 'BRK20_VOL', 'HI52', 'NR7_BRK', 'GAP_GO'])]
        else:
            e = E[E.pattern == p]
        g = e.groupby(['symbol', 'period']).x5.agg(['mean', 'count', 'std'])
        g = g.join(bp.x5.rename('base'))
        g['alpha'] = g['mean'] - g['base']
        g['tA'] = g['alpha'] / (g['std'] / np.sqrt(g['count']))
        w = g.unstack('period')
        w = w[(w[('count', 'A')] >= 8) & (w[('count', 'B')] >= 8)]
        if len(w) < 50:
            L.append(f'  {p:16s} too few stocks ({len(w)})'); continue
        aA, aB, tA = w[('alpha', 'A')], w[('alpha', 'B')], w[('tA', 'A')]
        rho = aA.rank().corr(aB.rank())
        q = pd.qcut(tA.rank(method='first'), 5, labels=False)
        top, bot = aB[q == 4], aB[q == 0]
        diff = top.mean() - bot.mean()
        se = np.sqrt(top.var() / len(top) + bot.var() / len(bot))
        persist[p] = (rho, diff * 1e4, diff / se)
        L.append(f'  {p:16s} stocks={len(w):5d} | rank corr A→B {rho:+.3f} | period-B alpha: top-quintile respecters '
                 f'{top.mean() * 1e4:+6.1f}bp vs bottom {bot.mean() * 1e4:+6.1f}bp → spread {diff * 1e4:+6.1f}bp (t {diff / se:+.2f}) '
                 f'| all stocks in B {aB.mean() * 1e4:+6.1f}bp')
    # ── C. the user's strategy, walk-forward ──
    by_year = base[base.kind == 'year'].rename(columns={'year': 'yr'})
    by_year['yr'] = by_year['yr'].astype(int)
    L.append('\nC. THE STRATEGY, WALK-FORWARD: each year trade the pattern only on stocks whose pattern alpha over the previous '
             '2 years was in the top quintile (≥6 events) vs on every stock. 5-day excess per event, bp')
    for p in PATTERNS + ['ALL']:
        e = E if p == 'ALL' else E[E.pattern == p]
        e = e.merge(by_year[['symbol', 'yr', 'x5']].rename(columns={'yr': 'year', 'x5': 'base'}), on=['symbol', 'year'], how='left')
        e['alpha'] = e.x5 - e.base
        rows = []
        for Y in range(2020, 2027):
            hist = e[(e.year >= Y - 2) & (e.year < Y)]
            sc = hist.groupby('symbol').alpha.agg(['mean', 'count'])
            sc = sc[sc['count'] >= 6]
            if len(sc) < 25:
                continue
            picks = set(sc[sc['mean'] >= sc['mean'].quantile(0.8)].index)
            cur = e[e.year == Y]
            sel, rest = cur[cur.symbol.isin(picks)], cur[~cur.symbol.isin(picks)]
            rows.append((Y, sel.x5.mean() * 1e4, cur.x5.mean() * 1e4, len(sel), sel.alpha.mean() * 1e4, rest.alpha.mean() * 1e4))
        if not rows:
            continue
        R = pd.DataFrame(rows, columns=['Y', 'sel', 'all', 'n', 'sel_alpha', 'rest_alpha'])
        edge = R.sel - R['all']
        L.append(f'  {p:12s} picked respecters {R.sel.mean():+6.1f}bp vs every stock {R["all"].mean():+6.1f}bp → edge {edge.mean():+6.1f}bp, '
                 f'better in {int((edge > 0).sum())}/{len(R)} years · by year ' + ' '.join(f'{int(y) % 100:02d}:{d:+.0f}' for y, d in zip(R.Y, edge)))
    txt = '\n'.join(L)
    open(os.path.join(OUT, 'pattern_persistence_report.txt'), 'w').write(txt)
    print(txt)


if __name__ == '__main__':
    main()
