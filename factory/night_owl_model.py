"""Night Owl — the learned overnight ranking model (library). Built Oct 5 2026.

WHAT IT IS. A machine-learned ranking model (gradient-boosted decision trees, scikit-learn's
HistGradientBoostingRegressor). Every trading day it scores our universe on ~80 daily signals and the
live engine (factory/live/night_owl.py) buys the 3 best-scored WILD names at the close and sells them at
the next open. Research trail and every number: docs/RESEARCH_REGISTRY.md §F (F2-F11).

DECISION-TIME HONEST BY CONSTRUCTION. A row for session t uses only:
  * daily features computed through session t-1's close (every stock feature LAGGED one session), and
  * session t's official opening price (the overnight gap, `gap_today`) — known at 09:30.
So tonight's scores are fixed from the open onward; the engine scores in the morning and only places
orders near the close. Target: the overnight return close(t) → open(t+1), market-neutral (rank within
the day). The universe each day = names whose PREVIOUS close was ≥ $5 with a known dollar volume
(the research version filtered on the same day's close — a small look-ahead removed here).

ONE CODE PATH. Training and live scoring both call build_panel() → make_X() from this file, on the
same yfinance daily data (auto-adjusted). Live scoring appends a synthetic "today" row carrying only
the opening price, so today's features come out of the identical code. factory/tests/test_night_owl.py
asserts the live path reproduces the training rows for past dates (parity) and that adding future
data cannot change a past row (no look-ahead).

Data: factory/cache/night_owl/daily.parquet (seeded from research_out/yf_daily_2015.parquet, refreshed
from yfinance each run; a symbol whose history was re-adjusted for a split/dividend is re-downloaded
whole). Models: factory/cache/night_owl/model_YYYYMMDD.joblib (+ .json metadata), re-trained when the
calendar month changes.

CLI (venv/bin/python -m factory.night_owl_model ...):
  prep          refresh the daily cache; train if the latest model is from an earlier month
  train         force a re-train on everything through the latest completed session
  validate      walk-forward re-run of the research test through THIS library (regression check)
  score [DATE]  print the scores for DATE (default: today) without placing anything
"""
from __future__ import annotations
import datetime as dt
import glob
import json
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd

ROOT = '/Users/sushil/trading'
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from factory.features_daily import features, _at_constants  # noqa: E402

warnings.filterwarnings('ignore')
CACHE_DIR = os.path.join(ROOT, 'factory', 'cache', 'night_owl')
DAILY = os.path.join(CACHE_DIR, 'daily.parquet')
SEED_DAILY = os.path.join(ROOT, 'research_out', 'yf_daily_2015.parquet')
PERSONALITY = os.path.join(ROOT, 'factory', 'cache', 'personality.csv')
HISTORY_START = '2015-01-01'
TAIL_SESSIONS = 520          # history used to score one day (≥ 252-day window + EWM burn-in)
MIN_PRICE = 5.0
ADJ_TOL = 5e-4               # a cached close that moved more than this was re-adjusted → refetch symbol
INTRADAY_ONLY = ['ret_first_hour', 'ret_last_hour', 'ret_last_30m', 'ivol_intraday', 'upvol_share']
MARKET_COLS = ['mkt_r1', 'mkt_r5', 'mkt_r20', 'xs_dispersion', 'breadth', 'vix', 'dow', 'month_end']
NOT_LAGGED = ('dow', 'month_end')          # calendar facts about TODAY
NON_FEATURES = {'date', 'symbol', 'sector', 'dna', 'close'}
HGB_PARAMS = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=500,
                  l2_regularization=1.0, early_stopping=False, random_state=7)
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo('America/New_York')
except Exception:                                   # pragma: no cover
    ET = None


def _now():
    return dt.datetime.now(ET) if ET else dt.datetime.now()


# ─────────────────────────── universe ───────────────────────────
def universe():
    """(symbols, sector_map, high_vol_set, institutional_set) from auto_trader.py (read, not imported)."""
    k = _at_constants()
    etf = set(k.get('ETF_SYMBOLS', []))
    syms = [s for s in k['FULL_UNIVERSE'] if s not in etf]
    return syms, k['SECTOR_MAP'], set(k['HIGH_VOL_SYMBOLS']), set(k['INSTITUTIONAL_SYMBOLS'])


def wild_set():
    pe = pd.read_csv(PERSONALITY)
    return set(pe[pe.cluster == 'WILD'].symbol)


# ─────────────────────────── daily data ───────────────────────────
def _yf_daily(symbols, **kw):
    """yfinance daily bars, auto-adjusted, long format (date, symbol, open, high, low, close, volume)."""
    import yf_cache_fix  # noqa: F401  (in-memory yfinance caches — the Sep 30 file-handle leak fix)
    import yfinance as yf
    frames = []
    for i in range(0, len(symbols), 40):
        chunk = symbols[i:i + 40]
        df = yf.download(chunk, auto_adjust=True, progress=False, group_by='ticker', threads=True, **kw)
        for s in chunk:
            try:
                x = df[s].dropna(how='all') if len(chunk) > 1 or isinstance(df.columns, pd.MultiIndex) else df.dropna(how='all')
            except KeyError:
                continue
            if x.empty:
                continue
            x = x.rename(columns=str.lower)
            if not {'open', 'high', 'low', 'close', 'volume'} <= set(x.columns):
                continue
            x = x[['open', 'high', 'low', 'close', 'volume']].copy()
            x['symbol'] = s
            x.index.name = 'date'
            frames.append(x.reset_index())
    if not frames:
        return pd.DataFrame(columns=['date', 'symbol', 'open', 'high', 'low', 'close', 'volume'])
    d = pd.concat(frames, ignore_index=True)
    d['date'] = pd.to_datetime(d['date'])
    if getattr(d['date'].dt, 'tz', None) is not None:
        d['date'] = d['date'].dt.tz_localize(None)
    return d


def _session_complete(day: pd.Timestamp) -> bool:
    n = _now()
    today = pd.Timestamp(n.date())
    return day < today or (day == today and (n.hour, n.minute) >= (16, 30))


def load_daily():
    if not os.path.exists(DAILY):
        return None
    d = pd.read_parquet(DAILY)
    d['date'] = pd.to_datetime(d['date'])
    return d


def refresh_daily(symbols=None, log=print):
    """Bring the daily cache up to the latest COMPLETED session and return (cache, fresh_recent).
    fresh_recent = the just-downloaded last ~month, INCLUDING today's partial row (the opening prices)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    if symbols is None:
        symbols = universe()[0]
    cache = load_daily()
    if cache is None:
        if os.path.exists(SEED_DAILY):
            cache = pd.read_parquet(SEED_DAILY)
            cache['date'] = pd.to_datetime(cache['date'])
            log(f'seeded daily cache from {SEED_DAILY} ({len(cache):,} rows)')
        else:
            log('no cache and no seed — full download from ' + HISTORY_START)
            cache = _yf_daily(symbols, start=HISTORY_START)
    fresh = _yf_daily(symbols, period='1mo')
    if fresh.empty:
        raise RuntimeError('yfinance returned no recent daily data')
    complete = fresh[fresh['date'].map(_session_complete)]
    # a symbol whose overlapping closes moved was re-adjusted (split/dividend) → refetch its whole history
    ov = complete.merge(cache[['date', 'symbol', 'close']], on=['date', 'symbol'], suffixes=('', '_old'))
    moved = ov[(ov.close / ov.close_old - 1).abs() > ADJ_TOL].symbol.unique().tolist()
    missing = [s for s in symbols if s not in set(cache.symbol)]
    redo = sorted(set(moved) | set(missing))
    if redo:
        log(f'refetching full history for {len(redo)} symbol(s) (re-adjusted or new): {redo[:12]}{"…" if len(redo) > 12 else ""}')
        full = _yf_daily(redo, start=HISTORY_START)
        full = full[full['date'].map(_session_complete)]
        cache = pd.concat([cache[~cache.symbol.isin(redo)], full], ignore_index=True)
    cache = pd.concat([cache, complete], ignore_index=True)
    cache = cache.drop_duplicates(['symbol', 'date'], keep='last').sort_values(['symbol', 'date'])
    cache = cache[cache.symbol.isin(symbols)].reset_index(drop=True)
    cache.to_parquet(DAILY, index=False)
    return cache, fresh


# ─────────────────────────── panel + model inputs ───────────────────────────
def build_panel(daily: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol features (factory.features_daily.features) + cross-sectional context, one row per
    (symbol, session). `daily` = long frame date/symbol/open/high/low/close/volume."""
    syms, sector, hv, inst = universe()
    parts = []
    for s, d in daily.groupby('symbol'):
        d = d.set_index('date').sort_index()
        d = d[(d['open'] > 0)].copy()
        if len(d) < 30:
            continue
        d['vwap'] = (d['high'] + d['low'] + d['close']) / 3
        for c in INTRADAY_ONLY:
            d[c] = np.nan
        d['runup_open'] = d['high'] / d['open'] - 1
        d['drawdown_open'] = d['low'] / d['open'] - 1
        f = features(d).drop(columns=INTRADAY_ONLY)
        f['symbol'] = s
        f['sector'] = sector.get(s, 'OTHER')
        f['dna'] = 'HIGH_VOL' if s in hv else 'INSTITUTIONAL' if s in inst else 'MOMENTUM'
        parts.append(f.reset_index().rename(columns={'index': 'date'}))
    p = pd.concat(parts, ignore_index=True)
    p['date'] = pd.to_datetime(p['date'])
    g = p.groupby('date')
    p['mkt_r1'] = g['ret_1d_for_beta'].transform('mean')
    p['mkt_r5'] = g['r5'].transform('mean')
    p['mkt_r20'] = g['r20'].transform('mean')
    p['xs_dispersion'] = g['ret_1d_for_beta'].transform('std')
    p['breadth'] = g['ret_1d_for_beta'].transform(lambda x: (x > 0).mean())
    p['sector_rel_r5'] = p['r5'] - p.groupby(['date', 'sector'])['r5'].transform('median')
    p = p.sort_values(['symbol', 'date'])
    gs = p.groupby('symbol')
    m_r = gs['ret_1d_for_beta'].transform(lambda x: x.rolling(60).mean())
    m_m = gs['mkt_r1'].transform(lambda x: x.rolling(60).mean())
    cov = (p['ret_1d_for_beta'] * p['mkt_r1']).groupby(p['symbol']).transform(lambda x: x.rolling(60).mean()) - m_r * m_m
    var = gs['mkt_r1'].transform(lambda x: x.rolling(60).var(ddof=0))
    p['beta60'] = cov / var.replace(0, np.nan)
    resid = p['ret_1d_for_beta'] - p['beta60'] * p['mkt_r1']
    p['idio_vol60'] = resid.groupby(p['symbol']).transform(lambda x: x.rolling(60).std())
    p['vix'] = np.nan
    return p.drop(columns=['ret_1d_for_beta']).reset_index(drop=True)


def feature_columns(panel: pd.DataFrame):
    feats = [c for c in panel.columns if c not in NON_FEATURES and not c.startswith('y_')]
    stock = [c for c in feats if c not in MARKET_COLS]
    return feats, stock


def make_X(panel: pd.DataFrame, sector_cats=None, dna_cats=None):
    """Decision-time model inputs. Returns (X, meta, sector_cats, dna_cats); X row i ↔ meta row i.
    Universe per day = names whose PREVIOUS close ≥ $5 and with a known previous dollar volume."""
    p = panel.sort_values(['symbol', 'date']).reset_index(drop=True)
    _, stock = feature_columns(p)
    lag = stock + [c for c in MARKET_COLS if c not in NOT_LAGGED]
    # 1. lag every raw value exactly ONE session on the full history (filtering first would let a symbol
    #    that dropped out for a day inherit features from an older session)
    raw = p[lag].groupby(p['symbol']).shift(1)
    raw['gap_today'] = p['on']                       # today's overnight gap — known at the open
    for c in NOT_LAGGED:
        raw[c] = p[c]
    # 2. tonight's universe: previous close ≥ $5 and a known previous dollar volume
    keep = (p.groupby('symbol')['close'].shift(1) >= MIN_PRICE) & raw['log_dollar_vol'].notna()
    p, raw = p[keep].reset_index(drop=True), raw[keep].reset_index(drop=True)
    # 3. rank stock-level values across tonight's universe; market context stays raw
    g = raw.groupby(p['date'])
    X = pd.DataFrame(index=p.index)
    for c in stock + ['gap_today']:
        X[c] = g[c].rank(pct=True) - 0.5
    for c in MARKET_COLS:
        X[c] = raw[c]
    sector_cats = sector_cats or sorted(p['sector'].unique())
    dna_cats = dna_cats or sorted(p['dna'].unique())
    X['sector_code'] = pd.Categorical(p['sector'], categories=sector_cats).codes
    X['dna_code'] = pd.Categorical(p['dna'], categories=dna_cats).codes
    meta = p[['date', 'symbol', 'close']].copy()
    if 'y_on' in p:
        meta['y_on'] = p['y_on']
        gm = meta.groupby('date')['y_on']
        meta['y_on_xs'] = meta['y_on'] - gm.transform('mean')
        meta['y_on_rk'] = gm.rank(pct=True) - 0.5
    return X, meta, sector_cats, dna_cats


# ─────────────────────────── model ───────────────────────────
def _fit(X, y):
    from sklearn.ensemble import HistGradientBoostingRegressor
    cols = list(X.columns)
    m = HistGradientBoostingRegressor(categorical_features=[cols.index('sector_code'), cols.index('dna_code')],
                                      **HGB_PARAMS)
    m.fit(X, y)
    return m


def train(daily=None, through=None, log=print):
    """Fit on every row with a known target up to `through` (default: latest), save, return artifact."""
    import joblib
    import sklearn
    daily = load_daily() if daily is None else daily
    t0 = time.time()
    panel = build_panel(daily)
    X, meta, scats, dcats = make_X(panel)
    ok = meta['y_on_rk'].notna()
    if through is not None:
        ok &= meta['date'] <= pd.Timestamp(through)
    if ok.sum() < 100_000:
        raise RuntimeError(f'refusing to train on only {int(ok.sum())} rows')
    model = _fit(X[ok], meta.loc[ok, 'y_on_rk'])
    last = meta.loc[ok, 'date'].max()
    version = 'nightowl-' + pd.Timestamp(last).strftime('%Y%m%d')
    art = dict(model=model, features=list(X.columns), sector_cats=scats, dna_cats=dcats,
               trained_through=str(pd.Timestamp(last).date()), n_rows=int(ok.sum()),
               n_symbols=int(meta.loc[ok, 'symbol'].nunique()), first_date=str(meta.loc[ok, 'date'].min().date()),
               created=_now().isoformat(timespec='seconds'), sklearn_version=sklearn.__version__,
               params=HGB_PARAMS, version=version)
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f'model_{pd.Timestamp(last).strftime("%Y%m%d")}.joblib')
    joblib.dump(art, path)
    json.dump({k: v for k, v in art.items() if k != 'model'}, open(path.replace('.joblib', '.json'), 'w'), indent=1)
    log(f'trained {version}: {art["n_rows"]:,} rows, {art["n_symbols"]} symbols, '
        f'{art["first_date"]} → {art["trained_through"]} in {time.time() - t0:.0f}s → {path}')
    return art


def latest_model_path():
    paths = sorted(glob.glob(os.path.join(CACHE_DIR, 'model_*.joblib')))
    return paths[-1] if paths else None


def load_model():
    import joblib
    import sklearn
    path = latest_model_path()
    if path is None:
        raise FileNotFoundError('no Night Owl model trained yet (run: python -m factory.night_owl_model train)')
    art = joblib.load(path)
    if art.get('sklearn_version') != sklearn.__version__:
        raise RuntimeError(f"model was trained with scikit-learn {art.get('sklearn_version')}, running "
                           f"{sklearn.__version__} — re-train before trusting its predictions")
    return art


def model_is_current(art, today=None, grace_months=0) -> bool:
    """Re-train once per calendar month: a model is current during the month it was TRAINED in.

    Oct 6 2026 fix: the old rule ("current through the month after the one it was trained through") skipped every
    other month — prep trains on the first weekday of a month, so `trained_through` already falls IN the new month and
    the model stayed "current" for two months (the Oct 5 model would not have re-trained until Dec 1). The research
    walk-forward that validated Night Owl re-fit monthly. `grace_months=1` lets the 09:35 scorer use last month's
    model on the first morning of a month without a false warning; the 17:30 prep re-trains that evening."""
    today = pd.Timestamp(today or _now().date()).to_period('M')
    made = pd.Timestamp(art.get('created') or art['trained_through'])
    made = made.tz_localize(None) if made.tzinfo else made
    return made.to_period('M') >= today - grace_months


# ─────────────────────────── scoring ───────────────────────────
def score_day(day, daily, opens: dict, art=None) -> pd.DataFrame:
    """Score session `day` from history strictly BEFORE it plus that session's opening prices.

    daily  : cache rows (any range — rows on/after `day` are ignored, so a full history is safe)
    opens  : {symbol: official open of `day`}
    Returns symbol, score, rank (1 = best), wild, gap, prev_close — every scored name, best first."""
    art = art or load_model()
    day = pd.Timestamp(day).normalize()
    hist = daily[daily['date'] < day]
    keep_dates = np.sort(hist['date'].unique())[-TAIL_SESSIONS:]
    hist = hist[hist['date'].isin(keep_dates)]
    syn = pd.DataFrame([{'date': day, 'symbol': s, 'open': float(o), 'high': np.nan, 'low': np.nan,
                         'close': np.nan, 'volume': np.nan} for s, o in opens.items() if o and o > 0])
    if syn.empty:
        raise RuntimeError(f'no opening prices for {day.date()}')
    panel = build_panel(pd.concat([hist, syn], ignore_index=True))
    X, meta, _, _ = make_X(panel, art['sector_cats'], art['dna_cats'])
    if list(X.columns) != art['features']:
        raise RuntimeError('feature list differs from the trained model — re-train (code changed?)')
    today = (meta['date'] == day).values
    if today.sum() == 0:
        raise RuntimeError(f'no scorable rows for {day.date()}')
    out = meta.loc[today, ['symbol']].copy()
    out['score'] = art['model'].predict(X[today])
    prev = hist.sort_values('date').groupby('symbol')['close'].last()
    out['prev_close'] = out['symbol'].map(prev)
    out['gap'] = out['symbol'].map(lambda s: opens.get(s, np.nan)) / out['prev_close'] - 1
    w = wild_set()
    out['wild'] = out['symbol'].isin(w)
    out = out.sort_values('score', ascending=False).reset_index(drop=True)
    out['rank'] = np.arange(1, len(out) + 1)
    out['wild_rank'] = np.where(out['wild'], out['wild'].cumsum(), 0)
    out['model_version'] = art['version']
    return out


# ─────────────────────────── validation (regression vs the research run) ───────────────────────────
def walk_forward(test_start='2018-01-01', log=print):
    """Re-run the research walk-forward through THIS library: monthly re-fit on all earlier rows, purged
    2 sessions; reports WILD IC and top-3 excess per year. Must match research_ml_longrun.py closely."""
    daily = load_daily()
    panel = build_panel(daily)
    X, meta, _, _ = make_X(panel)
    dates = np.sort(meta['date'].unique())
    months = pd.date_range(test_start, meta['date'].max() + pd.offsets.MonthBegin(1), freq='MS')
    pred = pd.Series(np.nan, index=meta.index)
    for m0, m1 in zip(months[:-1], months[1:]):
        test = (meta['date'] >= m0) & (meta['date'] < m1)
        if not test.any():
            continue
        prior = dates[dates < np.datetime64(m0)]
        cut = prior[-2]
        tr = (meta['date'] < cut) & meta['y_on_rk'].notna()
        pred[test] = _fit(X[tr], meta.loc[tr, 'y_on_rk']).predict(X[test])
    q = meta.assign(model=pred)
    q = q[(q['date'] >= test_start) & q['model'].notna() & q['y_on'].notna()]
    q = q[q['symbol'].isin(wild_set())]
    ic = q.groupby('date').apply(lambda g: g['model'].rank().corr(g['y_on_xs'].rank()) if len(g) >= 15 else np.nan).dropna()
    top = q.sort_values('model', ascending=False).groupby('date').head(3)
    exc = (top.groupby('date')['y_on'].mean() - q.groupby('date')['y_on'].mean()).dropna() * 1e4
    log(f'walk-forward {q.date.min().date()} → {q.date.max().date()}: WILD IC {ic.mean():+.4f} '
        f'(t {ic.mean() / (ic.std() / np.sqrt(len(ic))):+.2f}), top-3 excess {exc.mean():+.1f}bp/night '
        f'(t {exc.mean() / (exc.std() / np.sqrt(len(exc))):+.2f})')
    yr = pd.DataFrame({'IC': ic.groupby(ic.index.year).mean(), 'top3_bp': exc.groupby(exc.index.year).mean()})
    log(yr.round(3).to_string())
    q.to_parquet(os.path.join(CACHE_DIR, 'walk_forward_preds.parquet'), index=False)
    return ic, exc


def _cli():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'prep'
    if cmd == 'prep':
        cache, _ = refresh_daily()
        print(f'daily cache: {len(cache):,} rows, {cache.symbol.nunique()} symbols, last session {cache.date.max().date()}')
        try:
            art = load_model()
            if model_is_current(art):
                print(f"model {art['version']} is current (trained through {art['trained_through']})")
                return
            print(f"model {art['version']} is from an earlier month — re-training")
        except FileNotFoundError:
            print('no model yet — training')
        train(cache)
    elif cmd == 'train':
        train()
    elif cmd == 'validate':
        walk_forward(sys.argv[2] if len(sys.argv) > 2 else '2018-01-01')
    elif cmd == 'score':
        day = pd.Timestamp(sys.argv[2]) if len(sys.argv) > 2 else pd.Timestamp(_now().date())
        daily = load_daily()
        opens = (daily[daily['date'] == day].set_index('symbol')['open'].to_dict()
                 if (daily['date'] == day).any() else refresh_daily()[1].query('date == @day').set_index('symbol')['open'].to_dict())
        s = score_day(day, daily, opens)
        print(s[s.wild].head(10).to_string(index=False))
    else:
        print(__doc__)


if __name__ == '__main__':
    _cli()
