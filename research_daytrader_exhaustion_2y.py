"""Day-trader exhaustion lab, last 2 years (Sep 30 2026).

Builds every day-trader-style candidate from stored 5-min bars, Sep 2024 -> Sep 2026:
the FIRST 5-min bar close in the entry window (09:35-11:25, 12:45-12:55) where a
universe stock is
    truly up >= 3% vs the prior close (live price, not a frozen snapshot)
    above today's VWAP and above its prior 20-day average close
    on time-of-day relative volume >= 1.3 (cum volume vs same clock time, prior 20 days)
    priced $5-$800
and records point-in-time exhaustion features plus forward outcomes from that bar's close.

Also flags "override-like" candidates (up >= 5% from the OPEN, rvol >= 3, above VWAP) --
the entry rule of _scan_catalyst_override Path B.

Output: research_out/exhaustion_2y.csv
Usage:  venv/bin/python research_daytrader_exhaustion_2y.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_bars import load_bars  # noqa: E402

START, END = '2024-08-01', '2026-10-01'
KEEP_FROM = pd.Timestamp('2024-09-30')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'research_out', 'exhaustion_2y.csv')


def universe():
    import ast
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'auto_trader.py')).read()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == 'FULL_UNIVERSE' for t in node.targets):
            return sorted({n.value for n in ast.walk(node.value)
                           if isinstance(n, ast.Constant) and isinstance(n.value, str)})
    return []


def rsi(series, n=14):
    d = series.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))


def process(sym):
    b = load_bars(sym, start=START, end=END)
    if b.empty or len(b) < 5000:
        return None
    b = b.between_time('09:30', '15:55').copy()
    b.index.name = 'ts'
    b['date'] = b.index.normalize().tz_localize(None)
    b['tod'] = b.index.strftime('%H:%M')

    # ── daily context (known before the session starts) ──
    g = b.groupby('date')
    daily = pd.DataFrame({'open': g['open'].first(), 'high': g['high'].max(), 'low': g['low'].min(),
                          'close': g['close'].last(), 'volume': g['volume'].sum()})
    tr = pd.concat([daily['high'] - daily['low'], (daily['high'] - daily['close'].shift()).abs(),
                    (daily['low'] - daily['close'].shift()).abs()], axis=1).max(axis=1)
    ctx = pd.DataFrame(index=daily.index)
    ctx['prev_close'] = daily['close'].shift(1)
    ctx['atr'] = tr.rolling(14).mean().shift(1)
    ctx['ma20'] = daily['close'].rolling(20).mean().shift(1)
    ctx['prior_day_ret'] = (daily['close'].shift(1) / daily['close'].shift(2) - 1) * 100
    ctx['ret_5d'] = (daily['close'].shift(1) / daily['close'].shift(6) - 1) * 100
    ctx['high_20d'] = daily['high'].rolling(20).max().shift(1)
    ctx['next_open'] = daily['open'].shift(-1)
    ctx['day_close'] = daily['close']
    b = b.join(ctx, on='date')

    # ── intraday running values ──
    b['cumvol'] = g['volume'].cumsum()
    tp = (b['high'] + b['low'] + b['close']) / 3
    b['cum_tpv'] = (tp * b['volume']).groupby(b['date']).cumsum()
    b['vwap'] = b['cum_tpv'] / b['cumvol'].replace(0, np.nan)
    b['day_open'] = g['open'].transform('first')
    b['hod'] = g['high'].cummax()
    b['lod'] = g['low'].cummin()
    # time of the running high (bar start) -> minutes since HOD
    is_new_hod = b['high'] >= b['hod']
    b['hod_ts'] = pd.Series(np.where(is_new_hod, b.index.view('int64'), np.nan), index=b.index)
    b['hod_ts'] = b.groupby('date')['hod_ts'].ffill()
    b['hod_age'] = (b.index.view('int64') - b['hod_ts']) / 6e10
    # run over the last 30 min (6 bars) within the day
    b['close_6ago'] = g['close'].shift(6)
    b['close_6ago'] = b['close_6ago'].fillna(b['day_open'])
    # up-volume ratio of the last 6 bars
    upv = (b['volume'] * (b['close'] > b['open'])).groupby(b['date']).rolling(6, min_periods=1).sum().reset_index(level=0, drop=True)
    totv = b['volume'].groupby(b['date']).rolling(6, min_periods=1).sum().reset_index(level=0, drop=True)
    b['upvol6'] = upv / totv.replace(0, np.nan)
    b['rsi5m'] = rsi(b['close'])
    b['bar_n'] = g.cumcount() + 1

    # time-of-day relative cumulative volume vs the prior 20 sessions
    piv = b.pivot_table(index='date', columns='tod', values='cumvol', aggfunc='last')
    avg = piv.rolling(20, min_periods=10).mean().shift(1)
    avg_long = avg.stack().rename('avg_cumvol').reset_index()
    b = b.reset_index().merge(avg_long, on=['date', 'tod'], how='left').set_index('ts')
    b['rvol_tod'] = b['cumvol'] / b['avg_cumvol']

    # ── candidate condition at bar close ──
    t = b.index
    hm = t.hour * 100 + t.minute   # bar START time; the bar closes 5 min later
    in_window = ((hm >= 930) & (hm <= 1120)) | ((hm >= 1240) & (hm <= 1250))
    b['day_chg'] = (b['close'] / b['prev_close'] - 1) * 100
    cond = (in_window & (b['day_chg'] >= 3.0) & (b['close'] > b['vwap']) & (b['close'] > b['ma20'])
            & (b['rvol_tod'] >= 1.3) & (b['close'].between(5, 800)) & b['atr'].notna())
    cand = b[cond]
    cand = cand[~cand['date'].duplicated()]          # first qualifying bar per day
    cand = cand[cand['date'] >= KEEP_FROM]
    if cand.empty:
        return None

    rows = []
    for ts, r in cand.iterrows():
        day = b[b['date'] == r['date']]
        after = day[day.index > ts]
        entry = float(r['close'])
        atr = float(r['atr'])
        f = {'sym': sym, 'date': r['date'], 'ts': ts, 'entry': entry,
             'entry_min': (ts.hour - 9) * 60 + ts.minute - 30 + 5,
             'atr_pct': atr / r['prev_close'] * 100,
             'day_chg': r['day_chg'],
             'gap_pct': (r['day_open'] / r['prev_close'] - 1) * 100,
             'intra_chg': (entry / r['day_open'] - 1) * 100,
             'day_ext_atr': (entry - r['prev_close']) / atr,
             'intra_ext_atr': (entry - r['day_open']) / atr,
             'pvh': (entry / r['hod'] - 1) * 100,
             'retrace': ((r['hod'] - entry) / (r['hod'] - r['day_open'])) if r['hod'] > r['day_open'] else np.nan,
             'hod_age': r['hod_age'],
             'range_atr': (r['hod'] - r['lod']) / atr,
             'vwap_ext_atr': (entry - r['vwap']) / atr,
             'run_30m': (entry / r['close_6ago'] - 1) * 100,
             'upvol6': r['upvol6'], 'rsi5m': r['rsi5m'], 'rvol_tod': r['rvol_tod'],
             'prior_day_ret': r['prior_day_ret'], 'ret_5d': r['ret_5d'],
             'dist_20d_high_atr': (entry - r['high_20d']) / atr,
             'dist_ma20_atr': (entry - r['ma20']) / atr}
        f['override_like'] = ((entry / r['day_open'] - 1) * 100 >= 5.0) and r['rvol_tod'] >= 3.0
        if after.empty:
            continue
        for mins in (30, 60):
            w = after[after.index < ts + pd.Timedelta(minutes=mins)]
            f[f'ret_{mins}'] = (float(w['close'].iloc[-1]) / entry - 1) * 100 if not w.empty else np.nan
        f['mfe'] = (float(after['high'].max()) / entry - 1) * 100
        f['mae'] = (float(after['low'].min()) / entry - 1) * 100
        f['ret_close'] = (float(after['close'].iloc[-1]) / entry - 1) * 100
        f['ret_next_open'] = (float(r['next_open']) / entry - 1) * 100 if pd.notna(r['next_open']) else np.nan
        # the live hard stop: 5% below entry, else hold to the close
        hit = after[after['low'] <= entry * 0.95]
        if not hit.empty:
            first = hit.iloc[0]
            fill = min(float(first['open']), entry * 0.95)   # gap through -> fill at the open
            f['stop5_ret'] = (fill / entry - 1) * 100
            f['stop5_hit'] = True
        else:
            f['stop5_ret'] = f['ret_close']
            f['stop5_hit'] = False
        rows.append(f)
    return pd.DataFrame(rows)


def main():
    syms = universe()
    out = []
    for i, s in enumerate(syms):
        try:
            d = process(s)
            if d is not None:
                out.append(d)
        except Exception as e:  # keep going; report at the end
            print(f'{s}: {e}')
        if (i + 1) % 40 == 0:
            print(f'{i + 1}/{len(syms)} symbols', flush=True)
    df = pd.concat(out, ignore_index=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f'{len(df)} candidate-days from {df.sym.nunique()} symbols -> {OUT}')


if __name__ == '__main__':
    main()
