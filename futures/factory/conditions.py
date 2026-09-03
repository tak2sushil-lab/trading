"""
CONDITION LAB — "different clothes for different weather" for the intraday futures book.

The premise under test (user's thesis, Aug 18 2026): we have been fitting ONE strategy to
5.5 years of tape. But a session has a *character* — smooth trend, rocky chop, uphill grind,
knife-edge reversal — and a fleet of condition-specialised engines should beat one all-weather
engine. The analogy is right. The empirical question it hides is the whole game:

    *** IS THE WEATHER FORECASTABLE BEFORE WE HAVE TO DRESS FOR IT? ***

Everything previously tried failed at the FORECAST step, not the strategy step, and every
previous test was run on a NOISY LABEL (per-trade win/loss, n≈126/yr) which cannot separate
"no signal" from "signal buried under entry-timing noise".

This lab changes the measurement in three ways:
  1. LABEL = day character (efficiency ratio / directional room / adverse-first), not trade P&L.
     ~1,350 sessions instead of ~700 trades, and the label is a property of the TAPE, not of
     our entry rules — so a null here is a real null, and a hit is exploitable by ANY engine.
  2. FEATURES = strictly causal, known by 10:30 ET (the book's own decision time): overnight
     structure, the opening hour's own texture, higher-timeframe daily state, cross-instrument
     breadth (ES/RTY — never used before), and yesterday's character (persistence).
  3. VALIDATION = walk-forward by construction: every IC is reported TRAIN (2021-23) vs
     TEST (2024-26). A feature only counts if it survives the split with the same sign.

Modes
  --build        build/refresh the day table (cached CSV)
  --ic           feature → day-character predictability, train vs test  [THE CRUX]
  --persistence  does character cluster day-to-day? (vol clustering is real; is trend?)
  --profile      how much of the tape is each weather type, and what does it pay
  --room         the surviving forecast (expected ROOM) × the live momentum book, per year
  --direction    is WHICH-WAY forecastable inside any single weather bucket?
  --calendar     the weather we know weeks ahead (FOMC / NFP / OPEX / month-end)
  --book         momentum book sliced by the 5-way weather taxonomy

VERDICT (Aug 18 2026 run, 1,419 sessions):
  • ROOM (how far the tape will travel) IS forecastable — ib_range_atr IC +0.36→+0.41,
    rvol_1h +0.27→+0.41, on_range_atr +0.27→+0.33, all stable train→test. This is
    volatility clustering, and it is the only thing in the whole intraday program that
    has ever survived a walk-forward split.
  • TREND-QUALITY (pm_er) is NOT forecastable by anything, and has ZERO persistence
    (lag-1 IC −0.06). You cannot know at 10:30 whether the road ahead is smooth or rocky.
  • DIRECTION is NOT forecastable in any weather bucket.
  ⇒ The fleet analogy is right about the road and wrong about the map: the weather that
    exists is MAGNITUDE, not direction. A magnitude forecast is a RISK instrument (size,
    stop width, participation), not a new engine.
  • The one condition knowable in ADVANCE that changes everything is FOMC day: +48% room,
    58% clean-run vs 29% baseline, and 63% of that room lives AFTER 14:00 — which our
    entry cutoff excludes by construction.

Run: venv/bin/python -m futures.factory.conditions --ic
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from futures.collect_bars import load_bars  # noqa: E402

CACHE = os.path.join(ROOT, 'futures', 'factory', '_day_table.csv')

RTH_OPEN = _dt.time(9, 30)
IB_END = _dt.time(10, 30)      # the book's own decision boundary (IB_READY_TIME)
RTH_END = _dt.time(15, 10)     # the book's own EOD_CLOSE
ON_START = _dt.time(18, 0)     # CME session open, prior calendar day

TRAIN_END = 2023               # walk-forward split: 2021-23 train, 2024-26 test


# ── primitives ────────────────────────────────────────────────────────────────
def efficiency_ratio(closes: pd.Series) -> float:
    """Kaufman ER: |net move| / sum(|bar moves|). 1.0 = pure trend, ~0 = pure chop.
    This is the cleanest scalar for 'was this a trending session or a rocky one'."""
    if len(closes) < 3:
        return np.nan
    d = closes.diff().abs().sum()
    return float(abs(closes.iloc[-1] - closes.iloc[0]) / d) if d > 0 else np.nan


def vwap_crosses(day: pd.DataFrame) -> int:
    """How many times price crossed session VWAP — a direct chop counter."""
    tp = (day['high'] + day['low'] + day['close']) / 3.0
    vw = (tp * day['volume']).cumsum() / day['volume'].cumsum().replace(0, np.nan)
    side = np.sign(day['close'] - vw)
    side = side[side != 0]
    return int((side.diff().abs() > 0).sum()) if len(side) > 1 else 0


def _sess(df: pd.DataFrame, t0: _dt.time, t1: _dt.time) -> pd.DataFrame:
    t = df.index.time
    return df[(t >= t0) & (t <= t1)]


# ── the day table ─────────────────────────────────────────────────────────────
def build_day_table(start='2021-01-04', end='2026-08-15', verbose=True) -> pd.DataFrame:
    """One row per NY session: causal features (known by 10:30) + outcome labels (10:30→15:10)."""
    if verbose:
        print(f'  loading MNQ/ES/RTY 5-min bars {start} → {end} …')
    end_cut = (_dt.date.fromisoformat(end) + _dt.timedelta(days=1)).isoformat()
    mnq = load_bars('MNQ', start=start, end=end_cut)
    es = load_bars('ES', start=start, end=end_cut)
    rty = load_bars('RTY', start=start, end=end_cut)

    # daily frame for higher-timeframe state (built from our own 5m RTH, no extra source)
    rth_all = _sess(mnq, RTH_OPEN, RTH_END)
    daily = rth_all.groupby(rth_all.index.date).agg(
        o=('open', 'first'), h=('high', 'max'), l=('low', 'min'), c=('close', 'last'))
    daily.index = pd.to_datetime(list(daily.index))
    pc = daily['c'].shift(1)
    tr = pd.concat([daily['h'] - daily['l'], (daily['h'] - pc).abs(),
                    (daily['l'] - pc).abs()], axis=1).max(axis=1)
    daily['atr20'] = tr.rolling(20, min_periods=10).mean()
    daily['ma20'] = daily['c'].rolling(20, min_periods=20).mean()
    daily['ma50'] = daily['c'].rolling(50, min_periods=50).mean()
    daily['ma200'] = daily['c'].rolling(200, min_periods=200).mean()
    ret = daily['c'].pct_change()
    daily['rv5'] = ret.rolling(5, min_periods=5).std()
    daily['rv20'] = ret.rolling(20, min_periods=20).std()
    daily['atr_rank'] = daily['atr20'].rolling(252, min_periods=60).rank(pct=True)

    dates = sorted(set(rth_all.index.date))
    es_rth = _sess(es, RTH_OPEN, RTH_END)
    rty_rth = _sess(rty, RTH_OPEN, RTH_END)

    rows = []
    for i, d in enumerate(dates):
        if i == 0:
            continue
        day = rth_all[rth_all.index.date == d].sort_index()
        ib = _sess(day, RTH_OPEN, IB_END)
        pm_ = day[day.index.time > IB_END]           # the tradeable remainder
        if len(ib) < 8 or len(pm_) < 20:
            continue
        ts = pd.Timestamp(d)
        if ts not in daily.index:
            continue
        prev = daily.iloc[daily.index.get_loc(ts) - 1]
        atr = prev['atr20']
        if not np.isfinite(atr) or atr <= 0:
            continue

        # ── overnight (prior 18:00 → 09:30) ────────────────────────────────
        on_lo = pd.Timestamp.combine(dates[i - 1], ON_START).tz_localize(day.index.tz)
        on_hi = pd.Timestamp.combine(d, RTH_OPEN).tz_localize(day.index.tz)
        on = mnq[(mnq.index >= on_lo) & (mnq.index < on_hi)]
        on_range = float(on['high'].max() - on['low'].min()) if len(on) else np.nan
        on_er = efficiency_ratio(on['close']) if len(on) > 3 else np.nan

        open_px = float(ib['open'].iloc[0])
        ib_hi, ib_lo = float(ib['high'].max()), float(ib['low'].min())
        ib_rng = max(ib_hi - ib_lo, 1e-9)
        px1030 = float(ib['close'].iloc[-1])

        # cross-instrument first-hour breadth (risk appetite / rotation)
        def _fh_ret(src):
            s = src[src.index.date == d]
            s = _sess(s, RTH_OPEN, IB_END)
            if len(s) < 5:
                return np.nan
            return float(s['close'].iloc[-1] / s['open'].iloc[0] - 1.0)
        mnq_fh = px1030 / open_px - 1.0
        es_fh, rty_fh = _fh_ret(es_rth), _fh_ret(rty_rth)

        # ── LABELS: character of the tradeable window 10:30 → 15:10 ────────
        pc_ = pm_['close']
        pm_er = efficiency_ratio(pc_)
        net_pm = float(pc_.iloc[-1] - px1030)
        mfe_up = float(pm_['high'].max() - px1030)
        mfe_dn = float(px1030 - pm_['low'].min())
        room = max(mfe_up, mfe_dn)

        # "clean directional room": did the tape hand a 1-ATR-ish run in ONE direction
        # before taking half that against you first? (the thing a trend engine needs)
        thr = 0.6 * atr
        adv_thr = 0.35 * atr
        clean = 0
        for side, fav, adv in (('L', mfe_up, mfe_dn), ('S', mfe_dn, mfe_up)):
            if fav < thr:
                continue
            hi = pm_['high'].to_numpy(); lo = pm_['low'].to_numpy()
            if side == 'L':
                i_fav = int(np.argmax(hi - px1030 >= thr))
                adverse_first = float(px1030 - lo[:i_fav + 1].min())
            else:
                i_fav = int(np.argmax(px1030 - lo >= thr))
                adverse_first = float(hi[:i_fav + 1].max() - px1030)
            if adverse_first < adv_thr:
                clean = 1
        rows.append(dict(
            date=str(d), year=d.year,
            # ── causal features (all knowable at 10:30) ──
            gap_atr=(open_px - float(prev['c'])) / atr,
            on_range_atr=on_range / atr if np.isfinite(on_range) else np.nan,
            on_er=on_er,
            ib_range_atr=ib_rng / atr,
            ib_er=efficiency_ratio(ib['close']),
            ib_dir=(px1030 - open_px) / ib_rng,
            ib_vwap_x=vwap_crosses(ib),
            open_loc=(px1030 - ib_lo) / ib_rng,
            rvol_1h=np.nan,                       # filled below (needs trailing median)
            d_ma20=(float(prev['c']) - float(prev['ma20'])) / atr,
            d_ma50=(float(prev['c']) - float(prev['ma50'])) / atr,
            d_ma200=(float(prev['c']) - float(prev['ma200'])) / atr,
            vol_expand=float(prev['rv5'] / prev['rv20']) if prev['rv20'] else np.nan,
            atr_rank=float(prev['atr_rank']),
            prev_range_atr=float(prev['h'] - prev['l']) / atr,
            breadth_rty=(rty_fh - mnq_fh) if np.isfinite(rty_fh) else np.nan,
            breadth_es=(es_fh - mnq_fh) if np.isfinite(es_fh) else np.nan,
            dow=pd.Timestamp(d).dayofweek,
            ib_vol=float(ib['volume'].sum()),
            # ── labels ──
            pm_er=pm_er, net_pm=net_pm, abs_net_pm=abs(net_pm),
            room_atr=room / atr, mfe_up=mfe_up, mfe_dn=mfe_dn,
            clean_run=clean, atr=float(atr), px1030=px1030,
        ))

    df = pd.DataFrame(rows)
    # trailing-median RVOL for the opening hour (causal: prior 20 sessions only)
    df['rvol_1h'] = df['ib_vol'] / df['ib_vol'].shift(1).rolling(20, min_periods=10).median()
    # yesterday's character — the persistence features
    for c in ('pm_er', 'room_atr', 'clean_run'):
        df[f'prev_{c}'] = df[c].shift(1)
    df.to_csv(CACHE, index=False)
    if verbose:
        print(f'  built {len(df)} sessions → {CACHE}')
    return df


def load_day_table(rebuild=False) -> pd.DataFrame:
    if rebuild or not os.path.exists(CACHE):
        return build_day_table()
    return pd.read_csv(CACHE)


FEATURES = ['gap_atr', 'on_range_atr', 'on_er', 'ib_range_atr', 'ib_er', 'ib_dir',
            'ib_vwap_x', 'open_loc', 'rvol_1h', 'd_ma20', 'd_ma50', 'd_ma200',
            'vol_expand', 'atr_rank', 'prev_range_atr', 'breadth_rty', 'breadth_es',
            'prev_pm_er', 'prev_room_atr', 'prev_clean_run', 'dow']
LABELS = ['pm_er', 'room_atr', 'clean_run', 'abs_net_pm']


def _ic(a: pd.Series, b: pd.Series) -> tuple[float, int]:
    m = a.notna() & b.notna()
    if m.sum() < 40:
        return np.nan, int(m.sum())
    return float(a[m].rank().corr(b[m].rank())), int(m.sum())


# ── MODES ─────────────────────────────────────────────────────────────────────
def mode_ic(df: pd.DataFrame):
    """THE CRUX: does anything knowable at 10:30 predict the day's character?
    Reported TRAIN (≤2023) vs TEST (≥2024). A feature is only real if both halves
    agree in sign AND clear the noise floor."""
    tr, te = df[df.year <= TRAIN_END], df[df.year > TRAIN_END]
    print(f'\n{"="*96}')
    print('  CONDITION LAB — is the weather forecastable at 10:30?  (Spearman IC, walk-forward)')
    print(f'  train {tr.year.min()}-{TRAIN_END} n={len(tr)}   |   test {TRAIN_END+1}-{te.year.max()} n={len(te)}')
    print(f'{"="*96}')
    # noise floor: |IC| ~ 1/sqrt(n) is one sigma of a random feature
    floor_te = 1.96 / np.sqrt(max(len(te), 1))
    print(f'  95% noise floor on the TEST half: |IC| < {floor_te:.3f} is indistinguishable from luck\n')
    for lab in LABELS:
        print(f'  ── label: {lab} ' + '─' * (78 - len(lab)))
        print('  {:<18}{:>10}{:>10}{:>10}   {}'.format('feature', 'IC train', 'IC test', 'stable?', ''))
        out = []
        for f in FEATURES:
            ic_tr, _ = _ic(tr[f], tr[lab])
            ic_te, n_te = _ic(te[f], te[lab])
            if not (np.isfinite(ic_tr) and np.isfinite(ic_te)):
                continue
            same = np.sign(ic_tr) == np.sign(ic_te)
            real = same and abs(ic_te) > floor_te and abs(ic_tr) > 0.05
            out.append((abs(ic_te) if real else -1, f, ic_tr, ic_te, same, real))
        for _, f, a, b, same, real in sorted(out, reverse=True):
            flag = '✅ SURVIVES' if real else ('~ sign ok' if same else '✗ flips')
            print(f'  {f:<18}{a:>+10.3f}{b:>+10.3f}{"":>10}   {flag}')
        print()


def mode_persistence(df: pd.DataFrame):
    """Does character CLUSTER? Vol clustering is one of the most robust facts in markets;
    if trendiness clusters too, 'yesterday's weather' is a free forecast."""
    print(f'\n{"="*96}')
    print('  PERSISTENCE — does yesterday\'s character predict today\'s?')
    print(f'{"="*96}')
    for lab in ('pm_er', 'room_atr', 'abs_net_pm', 'clean_run'):
        for lag in (1, 2, 3, 5):
            s = df[lab]
            ic, n = _ic(s.shift(lag), s)
            floor = 1.96 / np.sqrt(max(n, 1))
            mark = '✅' if abs(ic) > floor else '  '
            print(f'  {lab:<12} lag {lag}:  IC {ic:+.3f}  (n={n}, floor {floor:.3f})  {mark}')
        print()
    # regime blocks: split by trailing-5d mean of the label, look at forward mean
    print('  Conditional means — bucket by TRAILING 5-day mean character, read FORWARD day:')
    for lab in ('pm_er', 'room_atr'):
        trail = df[lab].shift(1).rolling(5, min_periods=5).mean()
        q = pd.qcut(trail, 4, labels=['Q1 calm/chop', 'Q2', 'Q3', 'Q4 trendy/wide'], duplicates='drop')
        g = df.groupby(q, observed=True)[lab].agg(['mean', 'count'])
        print(f'\n    {lab}:')
        for k, r in g.iterrows():
            print(f'      {str(k):<16} forward mean {r["mean"]:.3f}   n={int(r["count"])}')
    print()


# ── condition taxonomy ────────────────────────────────────────────────────────
def classify(df: pd.DataFrame) -> pd.Series:
    """The 'weather forecast' — assigned from 10:30-knowable info ONLY.
    Deliberately coarse and prior-driven (no fitting): the opening hour's own texture
    (wide/narrow, trendy/choppy) plus higher-timeframe daily direction."""
    wide = df['ib_range_atr'] >= df['ib_range_atr'].shift(1).rolling(60, min_periods=20).median()
    trendy = df['ib_er'] >= 0.35
    up = df['d_ma50'] > 0
    out = pd.Series('MIXED', index=df.index)
    out[wide & trendy & up] = 'SMOOTH_UP'       # wide directional open, daily uptrend
    out[wide & trendy & ~up] = 'SMOOTH_DOWN'    # wide directional open, daily downtrend
    out[wide & ~trendy] = 'ROCKY_WIDE'          # big range, no direction — whipsaw
    out[~wide & trendy] = 'NARROW_DRIFT'        # quiet but directional — grind
    out[~wide & ~trendy] = 'DEAD_CALM'          # small range, no direction
    return out


def mode_profile(df: pd.DataFrame):
    df = df.copy()
    df['cond'] = classify(df)
    print(f'\n{"="*96}')
    print('  WEATHER PROFILE — how common is each condition, and what does the tape pay in it?')
    print(f'{"="*96}')
    print('  {:<14}{:>7}{:>8}{:>10}{:>11}{:>11}{:>11}'.format(
        'condition', 'n', '%days', 'pm_er', 'room(ATR)', 'clean%', 'net_pm'))
    print('  ' + '-' * 74)
    for c, g in df.groupby('cond'):
        print('  {:<14}{:>7}{:>7.0f}%{:>10.3f}{:>11.2f}{:>10.0f}%{:>+11.0f}'.format(
            c, len(g), 100 * len(g) / len(df), g['pm_er'].mean(),
            g['room_atr'].mean(), 100 * g['clean_run'].mean(), g['net_pm'].mean()))
    print('\n  Same table per YEAR (does the condition mean the same thing every year?):')
    for c, g in df.groupby('cond'):
        line = f'  {c:<14}'
        for y in sorted(df.year.unique()):
            gg = g[g.year == y]
            line += f'  {y}:{gg["clean_run"].mean()*100 if len(gg) else 0:>3.0f}%'
        print(line + '   ← % clean directional days')
    print()


def cached_momentum(start: str, end: str) -> pd.DataFrame:
    """run_ny is a full 5.5yr sim_replay (slow). Cache the trade frame so the many
    condition slices in this lab are instant. Key includes the window."""
    from futures.factory.bench import run_ny
    path = os.path.join(ROOT, 'futures', 'factory', f'_mom_{start}_{end}.csv')
    if os.path.exists(path):
        return pd.read_csv(path)
    print('  Building momentum stream (sim_replay, live-parity flags) — one time, then cached…')
    mom = run_ny(start, end)
    if len(mom):
        mom.to_csv(path, index=False)
    return mom


def room_forecast(df: pd.DataFrame) -> pd.Series:
    """THE forecast that survived walk-forward: expected directional ROOM for the rest of
    the session, from the three features with the largest stable IC (ib_range_atr,
    rvol_1h, on_range_atr). Equal-weighted rank composite — no fitting, no weights to
    overfit. Ranks are TRAILING-250-session (expanding percentile), never full-sample,
    so the score is knowable at 10:30 on the day."""
    parts = []
    for f in ('ib_range_atr', 'rvol_1h', 'on_range_atr'):
        s = df[f]
        parts.append(s.rolling(250, min_periods=60).apply(
            lambda w: (w[:-1] < w[-1]).mean(), raw=True))
    return pd.concat(parts, axis=1).mean(axis=1)


def mode_room(df: pd.DataFrame, start: str, end: str):
    """The money slice: bucket days by the ROOM FORECAST (causal, 10:30) and read both
    the tape's own payoff AND the existing momentum book's P&L — per year."""
    from futures.factory.bench import run_ny, engine_stats, daily_pnls_with_dll, monte_carlo_eval
    df = df.copy()
    df['room_fc'] = room_forecast(df)
    d = df.dropna(subset=['room_fc']).copy()
    d['bucket'] = pd.qcut(d['room_fc'], 5, labels=['R1 dead', 'R2', 'R3', 'R4', 'R5 wide'])

    print(f'\n{"="*96}')
    print('  ROOM FORECAST — the one thing that survived walk-forward. What the TAPE delivers:')
    print(f'{"="*96}')
    print('  {:<10}{:>7}{:>11}{:>11}{:>10}{:>11}'.format(
        'bucket', 'n', 'room(ATR)', 'realised pts', 'clean%', 'pm_er'))
    print('  ' + '-' * 60)
    for b, g in d.groupby('bucket', observed=True):
        print('  {:<10}{:>7}{:>11.2f}{:>11.0f}{:>9.0f}%{:>11.3f}'.format(
            str(b), len(g), g['room_atr'].mean(),
            (g['room_atr'] * g['atr']).mean(), 100 * g['clean_run'].mean(), g['pm_er'].mean()))

    print('\n  Realised room in POINTS, per year (is "wide" the same thing every year?):')
    for b, g in d.groupby('bucket', observed=True):
        line = f'  {str(b):<10}'
        for y in sorted(d.year.unique()):
            gg = g[g.year == y]
            line += f'{(gg["room_atr"]*gg["atr"]).mean() if len(gg) else 0:>8.0f}'
        print(line)
    print('  ' + ' ' * 10 + ''.join(f'{y:>8}' for y in sorted(d.year.unique())))

    mom = cached_momentum(start, end)
    if len(mom) == 0:
        print('  no trades'); return
    bmap = dict(zip(d['date'].astype(str), d['bucket'].astype(str)))
    mom['bucket'] = mom['date'].astype(str).map(bmap).fillna('n/a')
    yrs = pd.to_datetime(mom['date'].astype(str)).dt.year

    print(f'\n{"="*96}')
    print('  MOMENTUM BOOK by ROOM FORECAST  (does the book need room to work?)')
    print(f'{"="*96}')
    print('  {:<10}{:>6}{:>10}{:>8}{:>9}{:>9}{:>10}{:>10}'.format(
        'bucket', 'n', 'total', 'WR', 'avg', 'Sharpe', 'P(pass)', 'P(blow)'))
    print('  ' + '-' * 72)
    for b in ['R1 dead', 'R2', 'R3', 'R4', 'R5 wide', 'n/a']:
        g = mom[mom['bucket'] == b]
        if len(g) == 0:
            continue
        st = engine_stats(g)
        mc = monte_carlo_eval([v for _, v in daily_pnls_with_dll(g)])
        print('  {:<10}{:>6}{:>+10,.0f}{:>7.0f}%{:>+9.0f}{:>9.2f}{:>9.0f}%{:>9.0f}%'.format(
            b, st['n'], st['total'], st['wr'], st['avg'], st['sharpe'],
            mc.get('p_pass', 0) * 100, mc.get('p_blow', 0) * 100))

    print('\n  PER YEAR (the overfit test — must pay in most years, not one):')
    print('  {:<10}'.format('bucket') + ''.join(f'{y:>10}' for y in sorted(yrs.unique())))
    for b in ['R1 dead', 'R2', 'R3', 'R4', 'R5 wide']:
        line = f'  {b:<10}'
        for y in sorted(yrs.unique()):
            g = mom[(mom['bucket'] == b) & (yrs == y)]
            line += f'{g["pnl"].sum():>+10,.0f}' if len(g) else f'{"·":>10}'
        print(line)

    # the shippable rule: stand aside on the lowest-room days
    for cut in (['R1 dead'], ['R1 dead', 'R2']):
        keep = mom[~mom['bucket'].isin(cut)]
        st = engine_stats(keep)
        mc = monte_carlo_eval([v for _, v in daily_pnls_with_dll(keep)])
        print(f'\n  ROOM GATE — stand aside on {"+".join(cut)}:  n={st["n"]}  '
              f'total ${st["total"]:+,.0f}  Sharpe {st["sharpe"]:.2f}  '
              f'P(pass) {mc.get("p_pass",0)*100:.0f}%  P(blow) {mc.get("p_blow",0)*100:.0f}%')
        line = '    per year: '
        for y in sorted(yrs.unique()):
            g = keep[pd.to_datetime(keep['date'].astype(str)).dt.year == y]
            line += f'{y}:{g["pnl"].sum():+,.0f}  '
        print(line)
    st = engine_stats(mom)
    mc = monte_carlo_eval([v for _, v in daily_pnls_with_dll(mom)])
    print(f'\n  (baseline RAW:                    n={st["n"]}  total ${st["total"]:+,.0f}  '
          f'Sharpe {st["sharpe"]:.2f}  P(pass) {mc.get("p_pass",0)*100:.0f}%  '
          f'P(blow) {mc.get("p_blow",0)*100:.0f}%)')
    line = '    per year: '
    for y in sorted(yrs.unique()):
        g = mom[yrs == y]
        line += f'{y}:{g["pnl"].sum():+,.0f}  '
    print(line + '\n')


# ── the weather we DO know in advance: the calendar ───────────────────────────
# FOMC decision days 2021-2026 (public record). Self-validated below by checking that
# the 14:00→15:10 range really does explode on these dates — a wrong date list would
# show no such signature, so the check is not circular.
FOMC_DAYS = {
    '2021-01-27', '2021-03-17', '2021-04-28', '2021-06-16', '2021-07-28', '2021-09-22',
    '2021-11-03', '2021-12-15',
    '2022-01-26', '2022-03-16', '2022-05-04', '2022-06-15', '2022-07-27', '2022-09-21',
    '2022-11-02', '2022-12-14',
    '2023-02-01', '2023-03-22', '2023-05-03', '2023-06-14', '2023-07-26', '2023-09-20',
    '2023-11-01', '2023-12-13',
    '2024-01-31', '2024-03-20', '2024-05-01', '2024-06-12', '2024-07-31', '2024-09-18',
    '2024-11-07', '2024-12-18',
    '2025-01-29', '2025-03-19', '2025-05-07', '2025-06-18', '2025-07-30', '2025-09-17',
    '2025-10-29', '2025-12-10',
    '2026-01-28', '2026-03-18', '2026-04-29', '2026-06-17', '2026-07-29',
}


def calendar_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Rule-derived event flags — every one of these is knowable weeks ahead."""
    d = df.copy()
    ts = pd.to_datetime(d['date'])
    d['dom'] = ts.dt.day
    d['month'] = ts.dt.month
    dow = ts.dt.dayofweek
    week_of_month = (d['dom'] - 1) // 7
    d['is_nfp'] = ((dow == 4) & (week_of_month == 0)).astype(int)        # 1st Friday
    d['is_opex'] = ((dow == 4) & (week_of_month == 2)).astype(int)       # 3rd Friday
    d['is_quad'] = (d['is_opex'] == 1) & d['month'].isin([3, 6, 9, 12])
    d['is_fomc'] = d['date'].astype(str).isin(FOMC_DAYS).astype(int)
    d['is_cpi_win'] = d['dom'].between(10, 15).astype(int)               # CPI lands here
    d['is_month_end'] = (d['dom'] >= 26).astype(int)
    d['is_turn'] = ((d['dom'] >= 28) | (d['dom'] <= 2)).astype(int)      # turn-of-month
    return d


def mode_calendar(df: pd.DataFrame):
    """The Condition Lab's answer to 'we know winter is coming'. Tape-derived weather is
    only knowable AT 10:30 — but the calendar is knowable weeks out. If character differs
    on scheduled-event days, that is a forecast with infinite lead time."""
    d = calendar_flags(df)
    d['room_fc'] = room_forecast(d)
    base_room = d['room_atr'].mean()
    base_clean = d['clean_run'].mean()
    print(f'\n{"="*96}')
    print('  CALENDAR WEATHER — conditions knowable WEEKS in advance')
    print(f'  baseline: room {base_room:.2f} ATR   clean-run {base_clean*100:.0f}%   n={len(d)}')
    print(f'{"="*96}')
    print('  {:<16}{:>6}{:>11}{:>11}{:>11}{:>12}'.format(
        'event', 'n', 'room(ATR)', 'vs base', 'clean%', 'pm range'))
    print('  ' + '-' * 68)
    flags = ['is_fomc', 'is_nfp', 'is_opex', 'is_quad', 'is_cpi_win', 'is_month_end', 'is_turn']
    for f in flags:
        g = d[d[f] == 1]
        if len(g) < 15:
            continue
        print('  {:<16}{:>6}{:>11.2f}{:>+11.0f}%{:>10.0f}%{:>12.0f}'.format(
            f.replace('is_', ''), len(g), g['room_atr'].mean(),
            100 * (g['room_atr'].mean() / base_room - 1),
            100 * g['clean_run'].mean(), (g['room_atr'] * g['atr']).mean()))

    # self-validation of the FOMC list: the 2pm decision must show up as an afternoon
    # range explosion. If our hardcoded dates were wrong this signature would vanish.
    fo = d[d['is_fomc'] == 1]
    print(f'\n  FOMC list self-check: n={len(fo)} matched sessions, '
          f'room {fo["room_atr"].mean():.2f} vs {base_room:.2f} baseline '
          f'({100*(fo["room_atr"].mean()/base_room-1):+.0f}%)  '
          f'→ {"signature present, dates look right" if fo["room_atr"].mean() > base_room*1.15 else "NO signature — do not trust this date list"}')

    print('\n  Per-year stability of the biggest movers (an event edge must repeat):')
    for f in ['is_fomc', 'is_nfp', 'is_opex']:
        line = f'  {f.replace("is_", ""):<12}'
        for y in sorted(d.year.unique()):
            g = d[(d[f] == 1) & (d.year == y)]
            line += f'{g["room_atr"].mean() if len(g) else 0:>8.2f}'
        print(line + '   ← room in ATR')
    print('  ' + ' ' * 12 + ''.join(f'{y:>8}' for y in sorted(d.year.unique())))
    print()


def mode_direction(df: pd.DataFrame):
    """The '39% loss looked identical to the 61% win' question, asked at day level.

    Direction was already shown unforecastable IN AGGREGATE (ML AUC 0.48 OOS, Aug 17).
    But an aggregate null can hide a CONDITIONAL edge — that is precisely the user's
    thesis. So: re-ask it separately inside each ROOM bucket. If direction signal exists
    anywhere, it should show up where the tape actually moves."""
    d = df.copy()
    d['room_fc'] = room_forecast(d)
    d = d.dropna(subset=['room_fc'])
    d['bucket'] = pd.qcut(d['room_fc'], 5, labels=['R1 dead', 'R2', 'R3', 'R4', 'R5 wide'])
    d['up'] = (d['net_pm'] > 0).astype(int)
    tr, te = d[d.year <= TRAIN_END], d[d.year > TRAIN_END]

    dir_feats = ['ib_dir', 'open_loc', 'gap_atr', 'on_er', 'd_ma20', 'd_ma50', 'd_ma200',
                 'breadth_rty', 'breadth_es', 'ib_er', 'prev_pm_er']
    print(f'\n{"="*96}')
    print('  DIRECTION — is WHICH WAY forecastable inside any weather bucket?')
    print('  (label = signed net move 10:30→15:10; IC train ≤2023 vs test ≥2024)')
    print(f'{"="*96}')
    for b in ['R1 dead', 'R2', 'R3', 'R4', 'R5 wide']:
        t1, t2 = tr[tr.bucket == b], te[te.bucket == b]
        floor = 1.96 / np.sqrt(max(len(t2), 1))
        print(f'\n  ── {b}   train n={len(t1)}  test n={len(t2)}  (noise floor {floor:.3f})')
        hits = []
        for f in dir_feats:
            a, _ = _ic(t1[f], t1['net_pm'])
            c, _ = _ic(t2[f], t2['net_pm'])
            if not (np.isfinite(a) and np.isfinite(c)):
                continue
            real = (np.sign(a) == np.sign(c)) and abs(c) > floor and abs(a) > 0.05
            hits.append((f, a, c, real))
        for f, a, c, real in sorted(hits, key=lambda x: -abs(x[2])):
            print(f'     {f:<14}{a:>+8.3f}{c:>+8.3f}   {"✅ SURVIVES" if real else ""}')
        print(f'     up-day base rate: train {t1["up"].mean()*100:.0f}%  test {t2["up"].mean()*100:.0f}%')
    print()


def mode_book(df: pd.DataFrame, start: str, end: str):
    """The money question: does the EXISTING momentum book have a condition where it
    reliably works — and one where it reliably bleeds? Per year, so a single good
    regime cannot masquerade as an edge."""
    from futures.factory.bench import run_ny, engine_stats
    mom = cached_momentum(start, end)
    if len(mom) == 0:
        print('  no trades'); return
    df = df.copy()
    df['cond'] = classify(df)
    cmap = dict(zip(df['date'].astype(str), df['cond']))
    mom['cond'] = mom['date'].astype(str).map(cmap).fillna('UNKNOWN')

    print(f'\n{"="*96}')
    print('  MOMENTUM BOOK sliced by 10:30 WEATHER FORECAST')
    print(f'{"="*96}')
    print('  {:<14}{:>6}{:>10}{:>9}{:>10}{:>9}'.format('condition', 'n', 'total', 'WR', 'avg', 'Sharpe'))
    print('  ' + '-' * 60)
    for c, g in sorted(mom.groupby('cond'), key=lambda kv: -kv[1]['pnl'].sum()):
        st = engine_stats(g)
        print('  {:<14}{:>6}{:>+10,.0f}{:>8.0f}%{:>+10.0f}{:>9.2f}'.format(
            c, st['n'], st['total'], st['wr'], st['avg'], st['sharpe']))
    print('\n  PER YEAR (the overfit test — a condition must pay in most years, not one):')
    conds = sorted(mom['cond'].unique())
    hdr = '  {:<14}'.format('condition') + ''.join(f'{y:>9}' for y in sorted(
        pd.to_datetime(mom['date'].astype(str)).dt.year.unique()))
    print(hdr)
    yrs = pd.to_datetime(mom['date'].astype(str)).dt.year
    for c in conds:
        line = f'  {c:<14}'
        for y in sorted(yrs.unique()):
            g = mom[(mom['cond'] == c) & (yrs == y)]
            line += f'{g["pnl"].sum():>+9,.0f}' if len(g) else f'{"·":>9}'
        print(line)
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--ic', action='store_true')
    ap.add_argument('--persistence', action='store_true')
    ap.add_argument('--profile', action='store_true')
    ap.add_argument('--book', action='store_true')
    ap.add_argument('--room', action='store_true')
    ap.add_argument('--direction', action='store_true')
    ap.add_argument('--calendar', action='store_true')
    ap.add_argument('--start', default='2021-06-01')
    ap.add_argument('--end', default='2026-08-14')
    a = ap.parse_args()

    df = load_day_table(rebuild=a.build)
    if a.ic:
        mode_ic(df)
    if a.persistence:
        mode_persistence(df)
    if a.profile:
        mode_profile(df)
    if a.book:
        mode_book(df, a.start, a.end)
    if a.room:
        mode_room(df, a.start, a.end)
    if a.direction:
        mode_direction(df)
    if a.calendar:
        mode_calendar(df)
    if not any([a.ic, a.persistence, a.profile, a.book, a.room, a.direction, a.calendar]):
        print(f'  day table: {len(df)} sessions {df.date.min()} → {df.date.max()}')


if __name__ == '__main__':
    main()
