"""Day Owl — can Night Owl's learned-model recipe predict the INTRADAY leg the day trader trades? (Oct 5 2026)

RESEARCH_REGISTRY §I. Same production pipeline as Night Owl (factory/night_owl_model.build_panel → make_X:
~90 daily features, every stock input LAGGED one session, plus today's official opening gap), but the target
is TODAY's open→close return instead of tonight's close→open. Decision at the 09:30 open (MOO), exit at the
close (MOC) — the cleanest daily-data version of "what the day trader is trying to do".

Data: factory/cache/night_owl/daily.parquet (yfinance official prints, 2015 → today, our ~237 names).
Walk-forward: YEARLY re-fit on every earlier row, purged 2 sessions, out of sample 2018 → today.

Three questions, one script:
  A. POOLED model (all names) — IC and top/bottom-k intraday excess per year, gross and net of cost;
     also inside the WILD set and inside "gappers" (today's gap ≥ +3%: the names the day trader chases).
  B. "Train it on a few stocks" — for 20 long-history names: a model per stock (own history only),
     a model on just those 20, and the pooled model, all scored on the same 20 names and days.
  C. The same per-stock vs pooled comparison on the OVERNIGHT target, where pooled is known to work
     (registry F10/F12) — if per-stock cannot find even that edge, the approach is the problem.

⚠️ Survivorship: the universe is TODAY's names. Market-neutral (within-day) scoring limits it.
Usage: venv/bin/python research_ml_dayowl.py            (writes research_out/dayowl_*.parquet/.txt)
"""
import os, sys, time, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
from factory import night_owl_model as NO

OUT = os.path.join(ROOT, 'research_out')
PANEL_CACHE = os.path.join(OUT, 'dayowl_X.parquet')
TEST_YEARS = list(range(2018, 2027))
FEW = 20


def log(*a):
    print(*a, flush=True)


def tstat(x):
    x = pd.Series(x).dropna()
    return x.mean() / (x.std() / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def load_X():
    if os.path.exists(PANEL_CACHE):
        Z = pd.read_parquet(PANEL_CACHE)
        feats = [c for c in Z.columns if not c.startswith(('m_', 'y_'))]
        return Z, feats
    t0 = time.time()
    daily = NO.load_daily()
    panel = NO.build_panel(daily)
    X, meta, _, _ = NO.make_X(panel)
    feats = list(X.columns)
    tgt = panel[['date', 'symbol', 'id', 'on']].rename(columns={'id': 'y_id_today', 'on': 'gap_raw'})
    meta = meta.merge(tgt, on=['date', 'symbol'], how='left')
    Z = X.copy()
    for c in ['date', 'symbol', 'close', 'y_on', 'y_on_xs', 'y_on_rk', 'y_id_today', 'gap_raw']:
        Z['m_' + c if not c.startswith('y_') else c] = meta[c].values
    gd = Z.groupby('m_date')['y_id_today']
    Z['y_id_xs'] = Z['y_id_today'] - gd.transform('mean')
    Z['y_id_rk'] = gd.rank(pct=True) - 0.5
    Z.to_parquet(PANEL_CACHE, index=False)
    log(f'panel built: {len(Z):,} rows, {Z.m_symbol.nunique()} symbols in {time.time() - t0:.0f}s')
    return Z, feats


def walk_forward(Z, feats, target, rows=None, params=None, min_train=2000):
    """Yearly re-fit, purged 2 sessions. Returns predictions aligned to Z.index (NaN where not tested)."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    rows = Z.index if rows is None else rows
    sub = Z.loc[rows]
    pred = pd.Series(np.nan, index=sub.index)
    dates = np.sort(sub['m_date'].unique())
    cols = list(feats)
    kw = dict(NO.HGB_PARAMS); kw.update(params or {})
    for y in TEST_YEARS:
        test = (sub['m_date'].dt.year == y)
        if not test.any():
            continue
        prior = dates[dates < np.datetime64(f'{y}-01-01')]
        if len(prior) < 3:
            continue
        tr = (sub['m_date'] < prior[-2]) & sub[target].notna()
        if tr.sum() < min_train:
            continue
        m = HistGradientBoostingRegressor(categorical_features=[cols.index('sector_code'), cols.index('dna_code')], **kw)
        m.fit(sub.loc[tr, cols], sub.loc[tr, target])
        pred[test] = m.predict(sub.loc[test, cols])
    return pred


def per_day_ic(df, p, y, min_n=15):
    g = df.groupby('m_date')
    return g.apply(lambda d: d[p].rank().corr(d[y].rank()) if len(d) >= min_n else np.nan).dropna()


def topk(df, p, y_raw, k, side='top'):
    """mean over days of (top-k mean − all mean) and raw top-k mean, in bp."""
    d = df.dropna(subset=[p, y_raw])
    allm = d.groupby('m_date')[y_raw].mean()
    pick = d.sort_values(p, ascending=(side == 'bottom')).groupby('m_date').head(k)
    km = pick.groupby('m_date')[y_raw].mean()
    return (km - allm).dropna() * 1e4, km * 1e4


def report_pooled(Z, pred, lines):
    Z = Z.assign(pred=pred)
    q = Z[Z.pred.notna() & Z.y_id_today.notna()]
    wild = NO.wild_set()
    lines.append('A. POOLED intraday model (open→close), OOS 2018 → ' + str(q.m_date.max().date()))
    for name, sub in [('ALL', q), ('WILD', q[q.m_symbol.isin(wild)])]:
        ic = per_day_ic(sub, 'pred', 'y_id_xs')
        lines.append(f'  {name:5s} IC {ic.mean():+.4f} (t {tstat(ic):+.2f}, {len(ic)} days)  per year: ' +
                     ' '.join(f'{yy}:{v:+.3f}' for yy, v in ic.groupby(ic.index.year).mean().items()))
        for k in (3, 5, 10):
            ex, raw = topk(sub, 'pred', 'y_id_today', k, 'top')
            exb, rawb = topk(sub, 'pred', 'y_id_today', k, 'bottom')
            ls = (raw - rawb).dropna()
            yrs = ex.groupby(ex.index.year).mean()
            lines.append(f'    top-{k:<2d} excess {ex.mean():+6.1f}bp (t {tstat(ex):+.2f}) raw {raw.mean():+6.1f}bp | '
                         f'bottom-{k} raw {rawb.mean():+6.1f}bp | long-short {ls.mean():+6.1f}bp (t {tstat(ls):+.2f}) | '
                         f'top excess +yrs {int((yrs > 0).sum())}/{len(yrs)}')
    # gappers: the names the day trader chases (gap ≥ +3%)
    g = q[q.m_gap_raw >= 0.03]
    lines.append(f'  GAPPERS (gap ≥ +3%): {len(g):,} stock-days, avg open→close {g.y_id_today.mean() * 1e4:+.1f}bp '
                 f'(universe same days {q[q.m_date.isin(g.m_date)].y_id_today.mean() * 1e4:+.1f}bp)')
    for lo, hi in [(0.03, 0.05), (0.05, 0.08), (0.08, 0.12), (0.12, 9)]:
        b = q[(q.m_gap_raw >= lo) & (q.m_gap_raw < hi)]
        lines.append(f'    gap {lo:.0%}-{hi if hi < 9 else 9:.0%}: n={len(b):5d}  open→close mean {b.y_id_today.mean() * 1e4:+7.1f}bp '
                     f'median {b.y_id_today.median() * 1e4:+7.1f}bp  vs day avg {b.y_id_xs.mean() * 1e4:+7.1f}bp')
    # within gappers: does the model separate continuers from faders?
    gd = g.groupby('m_date')
    multi = g[gd['m_symbol'].transform('count') >= 2]
    best = multi.sort_values('pred', ascending=False).groupby('m_date').head(1)
    worst = multi.sort_values('pred').groupby('m_date').head(1)
    diff = (best.set_index('m_date').y_id_today - worst.set_index('m_date').y_id_today).dropna() * 1e4
    lines.append(f'    days with ≥2 gappers: {len(diff)}; model-best gapper minus model-worst gapper '
                 f'{diff.mean():+.1f}bp (t {tstat(diff):+.2f})')
    rs = q.pred.rank(pct=True)
    g2 = q.assign(rs=rs)[q.m_gap_raw >= 0.03]
    for lo, hi in [(0, .33), (.33, .67), (.67, 1.01)]:
        b = g2[(g2.rs >= lo) & (g2.rs < hi)]
        lines.append(f'    gappers in model tercile {lo:.2f}-{min(hi, 1):.2f}: n={len(b):5d} open→close {b.y_id_today.mean() * 1e4:+7.1f}bp')
    return q


def few_names(Z):
    first = Z.groupby('m_symbol')['m_date'].min()
    n = Z.groupby('m_symbol').size()
    wild = NO.wild_set()
    cand = [s for s in first.index if first[s] <= pd.Timestamp('2015-06-01') and n[s] > 2500 and s in wild]
    if len(cand) < FEW:
        cand += [s for s in first.index if first[s] <= pd.Timestamp('2015-06-01') and n[s] > 2500 and s not in cand]
    rng = np.random.default_rng(11)
    return sorted(rng.choice(sorted(cand), FEW, replace=False).tolist())


def compare_few(Z, feats, pooled_pred, target_rk, target_xs, target_raw, label, lines):
    names = few_names(Z)
    rows = Z.index[Z.m_symbol.isin(names)]
    small = walk_forward(Z, feats, target_rk, rows=rows, params=dict(min_samples_leaf=100), min_train=2000)
    per = pd.Series(np.nan, index=rows)
    per_lin = pd.Series(np.nan, index=rows)
    from sklearn.linear_model import Ridge
    num = [c for c in feats if c not in ('sector_code', 'dna_code')]
    for s in names:
        r = Z.index[Z.m_symbol == s]
        per.loc[r] = walk_forward(Z, feats, target_xs, rows=r, params=dict(min_samples_leaf=40, max_iter=150), min_train=500)
        sub = Z.loc[r]
        for y in TEST_YEARS:
            te = sub.m_date.dt.year == y
            tr = (sub.m_date < pd.Timestamp(f'{y}-01-01') - pd.Timedelta(days=4)) & sub[target_xs].notna()
            if tr.sum() < 500 or not te.any():
                continue
            Xt = sub.loc[tr, num].fillna(0); Xs = sub.loc[te, num].fillna(0)
            per_lin.loc[sub.index[te]] = Ridge(alpha=50.0).fit(Xt, sub.loc[tr, target_xs]).predict(Xs)
    F = Z.loc[rows].assign(pooled=pooled_pred.loc[rows], small=small, per=per, per_lin=per_lin)
    F = F[F[['pooled', 'small', 'per', 'per_lin']].notna().all(axis=1) & F[target_raw].notna()]
    lines.append(f'\n{label}: {FEW} long-history names {names}')
    lines.append(f'  test rows {len(F):,} ({F.m_date.min().date()} → {F.m_date.max().date()})')
    for m in ['pooled', 'small', 'per', 'per_lin']:
        ts = F.groupby('m_symbol').apply(lambda d: d[m].corr(d[target_xs], method='spearman'))
        ic = per_day_ic(F, m, target_xs, min_n=8)
        ex, raw = topk(F, m, target_raw, 3, 'top')
        hit = F.groupby('m_symbol').apply(lambda d: ((d[m] > d[m].median()) == (d[target_xs] > 0)).mean())
        yrs = ex.groupby(ex.index.year).mean()
        lines.append(f'  {m:8s} time-series IC (per stock, avg) {ts.mean():+.4f} [{int((ts > 0).sum())}/{len(ts)} stocks +] | '
                     f'cross-sec IC {ic.mean():+.4f} (t {tstat(ic):+.2f}) | top-3-of-{FEW} excess {ex.mean():+6.1f}bp '
                     f'(t {tstat(ex):+.2f}, +yrs {int((yrs > 0).sum())}/{len(yrs)}) | hit {hit.mean():.3f}')


def main():
    t0 = time.time()
    Z, feats = load_X()
    log(f'{len(Z):,} rows, {len(feats)} inputs, {Z.m_date.min().date()} → {Z.m_date.max().date()}')
    lines = []
    pred_id = walk_forward(Z, feats, 'y_id_rk')
    log(f'pooled intraday walk-forward done in {time.time() - t0:.0f}s')
    q = report_pooled(Z, pred_id, lines)
    q[['m_date', 'm_symbol', 'pred', 'y_id_today', 'y_id_xs', 'm_gap_raw']].to_parquet(os.path.join(OUT, 'dayowl_preds.parquet'), index=False)
    log('\n'.join(lines))
    compare_few(Z, feats, pred_id, 'y_id_rk', 'y_id_xs', 'y_id_today', 'B. INTRADAY target — per-stock vs small-group vs pooled', lines)
    log('\n'.join(lines[-8:]))
    pred_on = walk_forward(Z, feats, 'y_on_rk')
    log(f'pooled overnight walk-forward done ({time.time() - t0:.0f}s)')
    compare_few(Z, feats, pred_on, 'y_on_rk', 'y_on_xs', 'y_on', 'C. OVERNIGHT target — per-stock vs small-group vs pooled', lines)
    txt = '\n'.join(lines)
    open(os.path.join(OUT, 'dayowl_report.txt'), 'w').write(txt)
    log('\n' + txt + f'\n\ndone in {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
