#!/usr/bin/env python
"""backfill_score_components.py — reconstruct grade_setup()'s inputs at signal time.

WHY. grade_setup sums ~15 hand-assigned components and `log_scan_candidate` stored only the
TOTAL, so no weight has ever been scored against an outcome (3,694 A+ candidates, zero
breakdowns). Live logging of the breakdown started Sep 18 2026 — this recovers the history so
the weights can be FITTED now instead of after weeks of waiting.

POINT-IN-TIME DISCIPLINE. Every value is built only from bars at or before the signal
timestamp, mirroring get_intraday_signals exactly:
  df5  = 5-min bars, trailing 5 sessions, TRUNCATED at the signal bar  (auto_trader.py:1375)
  df1d = daily OHLCV aggregated from those 5-min bars: completed sessions before the signal
         day, plus the signal day as a PARTIAL bar up to the signal — which is what the live
         code sees when it reads close.iloc[-1] intraday.

NOT RECONSTRUCTED, and why — these are point-in-time STATE, not price, so deriving them today
would leak the future:
  is_catalyst        IBKR scanner output, never persisted
  pre-market high    bars_5m starts 09:30 ET; no pre-market stored
  sector grade       learner state as of that morning (30-day WR), changes nightly
  strategy weights   same
  sympathy triggers  computed live from mega-cap earnings moves
  VIX intraday       no dataset this account can reach (documented permanent gap)

Writes into scan_log.score_components as JSON, tagged {"_src":"backfill"} so reconstructed
rows are never silently mixed with live-logged ones.
"""
from __future__ import annotations
import argparse, json, os, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
from collect_bars import load_bars

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trades.db')
ORB_CUTOFF = (11, 30)


def daily_from_5m(b: pd.DataFrame) -> pd.DataFrame:
    g = b.groupby(b.index.date)
    return pd.DataFrame({'open': g['open'].first(), 'high': g['high'].max(),
                         'low': g['low'].min(), 'close': g['close'].last(),
                         'volume': g['volume'].sum()}).sort_index()


def rsi(series: pd.Series, n: int = 14):
    d = series.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = (-d.clip(upper=0)).rolling(n).mean()
    if len(l) == 0 or pd.isna(l.iloc[-1]):
        return None
    return 50.0 if l.iloc[-1] == 0 else round(float(100 - 100 / (1 + g.iloc[-1] / l.iloc[-1])), 1)


def components(b5: pd.DataFrame, t0: pd.Timestamp, price: float, spy_chg: float | None):
    """Everything grade_setup reads that is derivable from price history, as of t0."""
    hist = b5[b5.index <= t0]
    if len(hist) < 40:
        return None
    today = t0.date()
    tdy = hist[hist.index.date == today]
    if len(tdy) < 2:
        return None
    # df1d spans the whole loaded history (live uses 60 daily bars); last row is today's
    # PARTIAL bar, which is exactly what the live code reads intraday.
    df1d = daily_from_5m(hist)
    if len(df1d) < 21:
        return None
    # df5 mirrors live's `history(period='5d', interval='5m')`: the last 5 sessions only.
    sess = sorted(set(hist.index.date))[-5:]
    df5 = hist[pd.Series(hist.index.date, index=hist.index).isin(sess)]
    if len(df5) < 20:
        return None
    close = df1d['close']
    o = {}

    # ── daily-frame components ────────────────────────────────────────────────
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ema8 = float(close.ewm(span=8).mean().iloc[-1])
    ema21 = float(close.ewm(span=21).mean().iloc[-1])
    o['above_ma'] = bool(price > ma20)
    o['uptrend'] = bool(price > ema8 > ema21)
    o['ema_touch'] = bool(abs(price - ema21) / price * 100 < 2.5)
    o['rsi'] = rsi(close)
    r_high = float(df1d['high'].iloc[-3:].max()); r_low = float(df1d['low'].iloc[-3:].min())
    o['range_pct'] = round((r_high - r_low) / r_low * 100, 2) if r_low else None
    o['is_tight'] = bool(o['range_pct'] < 5) if o['range_pct'] is not None else None
    prev_chg = ((float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
                if len(close) > 1 else None)
    o['today_gain'] = round(prev_chg, 2) if prev_chg is not None else None
    o['rs_vs_spy'] = round(prev_chg - spy_chg, 2) if (prev_chg is not None and spy_chg is not None) else None

    # ── 5-min-frame components ────────────────────────────────────────────────
    o['rsi_5m'] = rsi(df5['close'])
    h, l, c = df5['high'].values, df5['low'].values, df5['close'].values
    fvg = 0
    for i in range(1, len(df5) - 1):
        if h[i - 1] < l[i + 1] and c[i] and (l[i + 1] - h[i - 1]) / c[i] * 100 >= 0.15:
            fvg += 1
    o['fvg_count'] = int(fvg)

    if len(df5) >= 15:
        pole, base = df5.iloc[-14:-5], df5.iloc[-5:]
        pm = (float(pole['high'].max()) - float(pole['open'].iloc[0])) / max(float(pole['open'].iloc[0]), 0.01) * 100
        bh, bl = float(base['high'].max()), float(base['low'].min())
        br = (bh - bl) / max(float(base['close'].mean()), 0.01) * 100
        o['is_bull_flag'] = bool(pm >= 2.0 and br < 2.0 and price >= bh * 0.998)
    else:
        o['is_bull_flag'] = False

    # ── intraday session components ───────────────────────────────────────────
    tp = (tdy['high'] + tdy['low'] + tdy['close']) / 3
    vw = (tp * tdy['volume']).cumsum() / tdy['volume'].cumsum().replace(0, np.nan)
    o['above_vwap'] = bool(tdy['close'].iloc[-1] > vw.iloc[-1]) if pd.notna(vw.iloc[-1]) else None
    o['vwap_reclaim'] = (bool(tdy['close'].iloc[-1] > vw.iloc[-1] and tdy['close'].iloc[-2] <= vw.iloc[-2])
                         if len(tdy) >= 2 and pd.notna(vw.iloc[-1]) and pd.notna(vw.iloc[-2]) else None)
    hod = float(tdy['high'].max())
    prior_hod = float(tdy['high'].iloc[:-2].max()) if len(tdy) > 2 else hod
    o['hod_break'] = bool(price >= prior_hod * 0.999 and price >= hod * 0.995)
    o['intra_chg'] = round((price - float(tdy['open'].iloc[0])) / float(tdy['open'].iloc[0]) * 100, 2)

    orb = tdy.between_time('09:30', '09:44')
    if len(orb) >= 2:
        oh = round(float(orb['high'].max()), 2)
        o['orb_break'] = bool(price > oh and price >= oh * 0.998 and (t0.hour, t0.minute) < ORB_CUTOFF)
    else:
        o['orb_break'] = None
    o['_src'] = 'backfill'
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--grades', default='A+,A')
    ap.add_argument('--direction', default='LONG')
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()
    grades = tuple(a.grades.split(','))

    con = sqlite3.connect(DB)
    q = (f"SELECT id, scan_date, scan_time, symbol, price FROM scan_log "
         f"WHERE direction=? AND grade IN ({','.join('?'*len(grades))}) "
         f"AND fwd_mfe_pct IS NOT NULL AND price IS NOT NULL "
         f"AND (score_components IS NULL OR score_components LIKE '%backfill%') "
         f"ORDER BY scan_date, scan_time")
    rows = pd.read_sql_query(q, con, params=(a.direction, *grades))
    if a.limit:
        rows = rows.head(a.limit)
    print(f'candidates to reconstruct: {len(rows):,}')

    cache: dict = {}
    def bars(sym, ds):
        k = (sym, ds)
        if k not in cache:
            if len(cache) > 250:
                cache.clear()
            try:
                s = (pd.Timestamp(ds) - pd.Timedelta(days=50)).strftime('%Y-%m-%d')
                e = (pd.Timestamp(ds) + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
                b = load_bars(sym, start=s, end=e)
                cache[k] = b if b is not None and len(b) else None
            except Exception:
                cache[k] = None
        return cache[k]

    spy_cache: dict = {}
    def spy_change(ds, t0):
        if ds not in spy_cache:
            b = bars('SPY', ds)
            spy_cache[ds] = b
        b = spy_cache[ds]
        if b is None: return None
        d = daily_from_5m(b[b.index <= t0])
        if len(d) < 2: return None
        return (float(d['close'].iloc[-1]) - float(d['close'].iloc[-2])) / float(d['close'].iloc[-2]) * 100

    out, miss = [], 0
    for i, x in enumerate(rows.itertuples(index=False), 1):
        b = bars(x.symbol, x.scan_date)
        if b is None: miss += 1; continue
        try: t0 = pd.Timestamp(f'{x.scan_date} {x.scan_time}', tz='America/New_York')
        except Exception: miss += 1; continue
        c = components(b, t0, float(x.price), spy_change(x.scan_date, t0))
        if c is None: miss += 1; continue
        out.append((json.dumps(c, separators=(',', ':')), x.id))
        if i % 500 == 0:
            print(f'  ...{i:,} scanned, {len(out):,} reconstructed')

    con.executemany('UPDATE scan_log SET score_components=? WHERE id=?', out)
    con.commit(); con.close()
    print(f'reconstructed {len(out):,}   insufficient history for {miss:,}')


if __name__ == '__main__':
    main()
