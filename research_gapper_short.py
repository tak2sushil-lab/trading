"""SHORT the morning's top gappers? (Oct 5 2026, RESEARCH_REGISTRY §K)

User's idea: buying the morning's top gainers at the open loses −108bp/day (registry J1), so sell them short and
train a model to pick the best shorts. This measures what a short book would actually face:
  1. the raw result of shorting each morning's top gappers at the open and covering at the close — per stock,
     per day, per year — and the SQUEEZE tail (how far above the open they trade: daily high / open);
  2. stops (pessimistic: if the day's high reached the stop we assume we were stopped there, plus slippage,
     because daily bars cannot say whether the low came first);
  3. costs — spread/commission and BORROW: IBKR's public shortable-stock file (ftp2.interactivebrokers.com,
     usa.txt) says which names can be borrowed tonight and at what fee (a snapshot, not history);
  4. a learned model trained ONLY on gappers (gap ≥ +5%), inputs known at 09:30, quarterly walk-forward,
     picking the k most-likely-to-fade shorts each morning;
  5. the opening-print check on our own 237 names (bars_5m): short at the official open vs 09:35 / 10:00.
Data: research_out/broad_daily_2024.parquet (5,637 listed US common stocks, yfinance daily 2024-01 → 2026-10).
⚠️ Survivorship: names delisted since are missing — for SHORTS this probably UNDERSTATES the edge (the missing
names mostly collapsed). Daily bars cannot model intraday stop order, halts, SSR or locate failures.
Usage: venv/bin/python research_gapper_short.py
"""
import os, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, 'research_out')
PANEL = os.path.join(OUT, 'gapper_short_panel.parquet')
SHORTFILE = '/private/tmp/claude-501/-Users-sushil-trading/7358fd15-a4e8-480d-bc4e-46bd3ee743c2/scratchpad/ibkr_usa_short.txt'
FEATS = ['gap', 'gap_rank', 'log_price_l', 'log_dvol_l', 'r1_l', 'r3_l', 'r5_l', 'r20_l', 'idr_l', 'clv_l', 'rvol_l',
         'vol20_l', 'atr_pct_l', 'max_r1_20_l', 'n_gap10_20_l', 'hi20_prox_l', 'hi250_prox_l', 'dist_ma20_l', 'age',
         'gap_vs_atr', 'mkt_gap', 'n_gappers', 'dow', 'prev_gap']


def tstat(x):
    x = pd.Series(x).dropna()
    return x.mean() / (x.std() / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def _features_one(s, x):
    """All per-symbol columns for one stock's daily frame (shared by build and build_lean)."""
    x = x.set_index('date')
    o, h, l, c, v = x.open, x.high, x.low, x.close, x.volume
    pc = c.shift(1)
    f = pd.DataFrame(index=x.index)
    f['open'], f['high'], f['low'], f['close'], f['pc'] = o, h, l, c, pc
    f['gap'] = o / pc - 1
    f['short'] = 1 - c / o                         # short at the open, cover at the close
    f['mae'] = h / o - 1                           # how far above the open it traded (adverse to a short)
    f['mfe'] = 1 - l / o
    r1 = c / pc - 1
    lag = lambda z: z.shift(1)
    f['r1_l'] = lag(r1); f['r3_l'] = lag(c / c.shift(3) - 1); f['r5_l'] = lag(c / c.shift(5) - 1); f['r20_l'] = lag(c / c.shift(20) - 1)
    f['idr_l'] = lag(c / o - 1); f['prev_gap'] = lag(o / pc - 1)
    f['clv_l'] = lag((c - l) / (h - l).replace(0, np.nan))
    mv = v.shift(1).rolling(20, min_periods=10).median().replace(0, np.nan)
    f['rvol_l'] = lag(v / mv)
    dv = (c * v).rolling(20, min_periods=10).median()
    f['dvol20_l'] = lag(dv); f['log_dvol_l'] = np.log(f['dvol20_l'].replace(0, np.nan)); f['price_l'] = pc; f['log_price_l'] = np.log(pc)
    f['vol20_l'] = lag(r1.rolling(20, min_periods=10).std()); f['max_r1_20_l'] = lag(r1.rolling(20, min_periods=10).max())
    g0 = o / pc - 1
    f['n_gap10_20_l'] = lag((g0.abs() >= 0.10).rolling(20, min_periods=1).sum())
    f['hi20_prox_l'] = lag(c / h.rolling(20, min_periods=5).max()); f['hi250_prox_l'] = lag(c / h.rolling(250, min_periods=20).max())
    f['dist_ma20_l'] = lag(c / c.rolling(20, min_periods=10).mean() - 1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    f['atr_pct_l'] = lag(tr.rolling(14, min_periods=7).mean() / c)
    f['age'] = np.arange(len(x)).clip(max=300)
    f['symbol'] = s
    f['prev_date'] = pd.Series(x.index, index=x.index).shift(1)
    return f.reset_index()


def build(src=None, panel=PANEL):
    """src: a long daily frame (date/symbol/OHLCV) or None for broad_daily_2024.parquet."""
    if os.path.exists(panel):
        return pd.read_parquet(panel)
    d = (pd.read_parquet(os.path.join(OUT, 'broad_daily_2024.parquet')) if src is None else src).sort_values(['symbol', 'date'])
    d = d[(d.open > 0) & (d.close > 0) & (d.high >= d.low)]
    parts = [_features_one(sym, x) for sym, x in d.groupby('symbol', sort=False)]
    p = pd.concat(parts, ignore_index=True)
    p = p[(p.date - p.prev_date).dt.days <= 7]                                    # no halts / data holes
    p = p[(p.gap.abs() < 3) & (p.open <= p.high * 1.001) & (p.open >= p.low * 0.999)]
    p['gap_vs_atr'] = p.gap / p.atr_pct_l
    p['mkt_gap'] = p.groupby('date')['gap'].transform('median')
    p['n_gappers'] = p.groupby('date')['gap'].transform(lambda g: (g >= 0.10).sum())
    p['dow'] = p.date.dt.dayofweek
    p.to_parquet(panel, index=False)
    return p


def _clean(p):
    p = p[(p.date - p.prev_date).dt.days <= 7]                                    # no halts / data holes
    return p[(p.gap.abs() < 3) & (p.open <= p.high * 1.001) & (p.open >= p.low * 0.999)]


def build_lean(src, panel, min_gap=0.05):
    """Same columns as build(), but keeps only rows with gap >= min_gap (plus the market context computed
    over EVERY row). Built one symbol at a time so 8 years x 5,600 stocks fit in memory. gap_rank among
    tradeable names is unchanged for kept rows: every name ranked above a gapper is itself a gapper."""
    if os.path.exists(panel):
        return pd.read_parquet(panel)
    d = src.sort_values(['symbol', 'date'])
    d = d[(d.open > 0) & (d.close > 0) & (d.high >= d.low)]
    keep, slim = [], []
    for sym, x in d.groupby('symbol', sort=False):
        f = _clean(_features_one(sym, x))
        slim.append(f[['date', 'gap']])
        k = f[f.gap >= min_gap]
        if len(k):
            keep.append(k)
    del d
    m = pd.concat(slim, ignore_index=True)
    ctx = m.groupby('date')['gap'].agg(mkt_gap='median', n_gappers=lambda g: int((g >= 0.10).sum())).reset_index()
    del m, slim
    p = pd.concat(keep, ignore_index=True).merge(ctx, on='date', how='left')
    p['gap_vs_atr'] = p.gap / p.atr_pct_l
    p['dow'] = p.date.dt.dayofweek
    p.to_parquet(panel, index=False)
    return p


def tradeable(p, mp=2.0):
    q = p[(p.price_l >= mp) & (p.dvol20_l >= 1e6) & p.short.notna()].copy()
    q['gap_rank'] = q.groupby('date')['gap'].rank(ascending=False, method='first')
    return q


def book(picks, label, cost_bp=0.0, stop=None, slip=0.01):
    """Equal-weight short book per day. Returns a one-line summary + the daily series (bp)."""
    r = picks['short'].copy()
    if stop is not None:
        hit = picks['mae'] >= stop
        r[hit] = -(stop + slip)
    r = r - cost_bp / 1e4
    day = r.groupby(picks['date']).mean() * 1e4
    dd = (day / 1e4).cumsum()
    yr = day.groupby(day.index.year).mean()
    line = (f'  {label:46s} n={len(picks):6,d} days={len(day):4d} | per stock mean {r.mean() * 1e4:+7.1f}bp median {r.median() * 1e4:+6.0f} '
            f'win {(r > 0).mean():.0%} | per day {day.mean():+6.1f}bp (t {tstat(day):+.2f}) days+ {(day > 0).mean():.0%} '
            f'worst {day.min():+.0f} | sum-DD {(dd - dd.cummax()).min() * 100:+.0f}% | yrs ' + ' '.join(f'{k}:{v:+.0f}' for k, v in yr.items()))
    return line, day


def main():
    p = build()
    L = []
    for mp in (2.0, 5.0):
        q = tradeable(p, mp)
        L.append(f'\n================ SHORT AT THE OPEN, COVER AT THE CLOSE — price ≥ ${mp:.0f}, $vol ≥ $1M ({q.date.min().date()} → {q.date.max().date()}) ================')
        top10 = q[(q.gap_rank <= 10) & (q.gap >= 0.05)]
        L.append('1. RAW (no costs, no stop)')
        for lab, s in [('top-10 gappers (gap ≥ +5%)', top10), ('top-3 gappers', q[(q.gap_rank <= 3) & (q.gap >= .05)]),
                       ('all gaps ≥ +10%', q[q.gap >= .10]), ('gaps +5-10%', q[(q.gap >= .05) & (q.gap < .10)]),
                       ('gaps +10-20%', q[(q.gap >= .10) & (q.gap < .20)]), ('gaps +20-50%', q[(q.gap >= .20) & (q.gap < .50)]),
                       ('gaps +50%+', q[q.gap >= .50]), ('top-10, price $2-5', top10[top10.price_l < 5]),
                       ('top-10, price $5-20', top10[(top10.price_l >= 5) & (top10.price_l < 20)]),
                       ('top-10, price $20+', top10[top10.price_l >= 20]),
                       ('top-10, ALSO up ≥20% yesterday (day-2 runner)', top10[top10.r1_l >= .20]),
                       ('top-10, NOT up yesterday (fresh gap)', top10[top10.r1_l < .05])]:
            L.append(book(s, lab)[0])
        m = top10.mae
        L.append(f'   squeeze tail, top-10 gappers: traded ≥10% above the open {(m >= .10).mean():.0%} | ≥20% {(m >= .20).mean():.1%} | '
                 f'≥50% {(m >= .50).mean():.1%} | ≥100% {(m >= 1.0).mean():.2%} | worst close vs open +{(-top10.short).max() * 100:.0f}% | '
                 f'worst single short −{(-top10.short).quantile(.99) * 100:.0f}% at the 1st percentile')
        L.append('2. STOPS (pessimistic: high touched the stop ⇒ stopped there + 1% slippage) and COSTS (spread+commission round trip)')
        for stop in (None, .10, .15, .20, .30):
            for cost in (0, 30, 60):
                if stop is None and cost == 0:
                    continue
                L.append(book(top10, f'top-10, stop {"none" if stop is None else f"+{stop:.0%}"}, cost {cost}bp', cost, stop)[0])
        if mp == 2.0:
            # 3. borrow — tonight's snapshot applied to the last 20 sessions' top-10 gappers
            sh = pd.read_csv(SHORTFILE, sep='|', skiprows=1, dtype=str)
            sh.columns = [c.strip('#') for c in sh.columns]
            sh = sh[sh.CUR == 'USD'].drop_duplicates('SYM').set_index('SYM')
            recent = top10[top10.date >= top10.date.max() - pd.Timedelta(days=30)]
            have = recent.symbol.isin(sh.index)
            fee = pd.to_numeric(sh.reindex(recent.symbol)['FEERATE'], errors='coerce').values
            avail = sh.reindex(recent.symbol)['AVAILABLE'].fillna('0').str.replace('>', '').astype(float).values
            L.append(f'3. BORROW (IBKR snapshot {open(SHORTFILE).readline().strip()}), last 30 days of top-10 gappers: n={len(recent)}')
            L.append(f'   on the shortable list at all: {have.mean():.0%} | ≥1,000 shares available: {(avail >= 1000).mean():.0%} | '
                     f'fee ≥ 10%/yr: {np.nanmean(fee >= 10):.0%} | ≥ 50%/yr: {np.nanmean(fee >= 50):.0%} | median fee of the borrowable '
                     f'{np.nanmedian(fee):.1f}%/yr (= {np.nanmedian(fee) / 360 * 100:.1f}bp/day) | 90th pct {np.nanpercentile(fee[~np.isnan(fee)], 90):.0f}%/yr')
            ok = recent[have & (avail >= 1000)]
            nok = recent[~(have & (avail >= 1000))]
            L.append(f'   short result of the BORROWABLE ones {ok.short.mean() * 1e4:+.0f}bp (n={len(ok)}) vs NOT borrowable {nok.short.mean() * 1e4:+.0f}bp (n={len(nok)})')
            # 4. model on gappers only
            from sklearn.ensemble import HistGradientBoostingRegressor
            G = q[q.gap >= .05].copy()
            X = G[FEATS].copy()
            for c in FEATS:
                if c not in ('log_price_l', 'log_dvol_l', 'age', 'dow', 'mkt_gap', 'n_gappers', 'gap_rank'):
                    X[c] = G.groupby('date')[c].rank(pct=True) - 0.5
            y = G['short'].clip(-0.5, 0.5)
            pred = pd.Series(np.nan, index=G.index)
            qs = pd.date_range('2025-01-01', G.date.max() + pd.offsets.QuarterBegin(1), freq='QS')
            for a, b in zip(qs[:-1], qs[1:]):
                te = (G.date >= a) & (G.date < b); tr = G.date < a - pd.Timedelta(days=3)
                if te.sum() == 0:
                    continue
                mdl = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=200,
                                                    l2_regularization=1.0, loss='absolute_error', random_state=7).fit(X[tr], y[tr])
                pred[te] = mdl.predict(X[te])
            R = G.assign(pred=pred).dropna(subset=['pred'])
            ic = R.groupby('date').apply(lambda d: d.pred.rank().corr(d.short.rank()) if len(d) >= 5 else np.nan).dropna()
            L.append(f'4. MODEL trained only on gappers (gap ≥ +5%), OOS {R.date.min().date()} → {R.date.max().date()} '
                     f'({R.date.nunique()} days, ~{len(R) / R.date.nunique():.0f} gappers/day): within-day IC {ic.mean():+.4f} (t {tstat(ic):+.2f})')
            base10 = R[R.gap_rank <= 10]
            L.append(book(base10, 'baseline: top-10 by gap (same OOS days)')[0])
            L.append(book(R, 'baseline: every gapper ≥ +5%')[0])
            for k in (3, 5, 10):
                pk = R.sort_values('pred', ascending=False).groupby('date').head(k)
                L.append(book(pk, f'MODEL top-{k} shorts')[0])
                L.append(book(pk, f'MODEL top-{k}, stop +20%, cost 30bp', 30, .20)[0])
                if k == 5:
                    L.append(f'      model top-5: squeeze ≥20% {(pk.mae >= .2).mean():.1%} vs baseline top-10 {(base10.mae >= .2).mean():.1%} | '
                             f'median gap {pk.gap.median():+.1%} | median price ${pk.price_l.median():.1f}')
            rk = R.sort_values('pred').groupby('date').head(5)
            L.append(book(rk, 'MODEL bottom-5 (predicted NOT to fade) — control')[0])
            R.to_parquet(os.path.join(OUT, 'gapper_short_preds.parquet'), index=False)
    # 5. opening-print check on our own names
    pr = pd.read_parquet(os.path.join(OUT, 'dayowl_preds.parquet')).rename(columns={'m_date': 'date', 'm_symbol': 'symbol'})
    px = pd.read_parquet(os.path.join(OUT, 'dayowl_intraday_prices.parquet'))
    z = pr[pr.date >= '2024-01-02'].merge(px, on=['date', 'symbol'])
    z = z[(z.n >= 60) & (z.last_t == '15:55')]
    L.append('\n5. OPENING-PRINT CHECK on our 237 names (bars_5m, 2024-26): short at the official open vs later entries (cover at 15:55 close)')
    for lo, hi in [(.05, .10), (.10, 9)]:
        s = z[(z.m_gap_raw >= lo) & (z.m_gap_raw < hi)]
        parts = [f'official open {-s.y_id_today.mean() * 1e4:+.0f}bp']
        for k, lab in [('o0930', 'first trade'), ('p0935', '09:35'), ('p0945', '09:45'), ('p1000', '10:00'), ('p1030', '10:30')]:
            parts.append(f'{lab} {(1 - s["last"] / s[k]).mean() * 1e4:+.0f}bp')
        L.append(f'   gap {lo:.0%}-{"∞" if hi > 1 else f"{hi:.0%}"} (n={len(s)}): ' + ' | '.join(parts))
    txt = '\n'.join(L)
    open(os.path.join(OUT, 'gapper_short_report.txt'), 'w').write(txt)
    print(txt)


if __name__ == '__main__':
    main()
