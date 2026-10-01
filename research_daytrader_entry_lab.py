"""Day-trader entry lab (Sep 30 2026).

Joins every auto_trader LONG trade to its own 5-minute bars and computes, strictly
point-in-time (only bars that had CLOSED before the entry timestamp, plus the fill
price itself), how extended / faded / stale the stock already was when we bought it.
Also attaches bar-based forward outcomes that do not depend on our exit stack.

Output: research_out/daytrader_entries.csv (one row per trade).

Usage:  venv/bin/python research_daytrader_entry_lab.py
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_bars import load_bars  # noqa: E402

ET = 'America/New_York'
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'research_out')


def daily_from_5m(df):
    """RTH daily bars aggregated from 5-min bars (index = session date)."""
    d = df.copy()
    d['date'] = d.index.date
    g = d.groupby('date')
    daily = pd.DataFrame({
        'open': g['open'].first(), 'high': g['high'].max(), 'low': g['low'].min(),
        'close': g['close'].last(), 'volume': g['volume'].sum(),
    })
    daily.index = pd.to_datetime(daily.index)
    return daily


def rsi(series, n=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def features_for_trade(bars, daily, entry_ts, entry_px):
    """All features use bars whose 5-min interval ended at or before entry_ts."""
    day = entry_ts.normalize().tz_localize(None)
    prior = daily[daily.index < day]
    if len(prior) < 21:
        return None
    today = bars[bars.index.normalize().tz_localize(None) == day]
    closed = today[today.index + pd.Timedelta(minutes=5) <= entry_ts]
    if today.empty:
        return None

    prev_close = float(prior['close'].iloc[-1])
    tr = pd.concat([prior['high'] - prior['low'],
                    (prior['high'] - prior['close'].shift()).abs(),
                    (prior['low'] - prior['close'].shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    atr_pct = atr / prev_close * 100
    open_px = float(today['open'].iloc[0])

    f = {
        'prev_close': prev_close, 'open': open_px, 'atr_pct': atr_pct,
        'gap_pct': (open_px / prev_close - 1) * 100,
        'day_chg': (entry_px / prev_close - 1) * 100,
        'intra_chg': (entry_px / open_px - 1) * 100,
        'prior_day_ret': (prev_close / float(prior['close'].iloc[-2]) - 1) * 100,
        'ret_5d': (prev_close / float(prior['close'].iloc[-6]) - 1) * 100,
        'dist_20d_high': (entry_px / float(prior['high'].iloc[-20:].max()) - 1) * 100,
        'mins_from_open': (entry_ts - today.index[0]).total_seconds() / 60,
        'entry_hour': entry_ts.hour + entry_ts.minute / 60,
    }
    f['day_ext_atr'] = (entry_px - prev_close) / atr if atr > 0 else np.nan
    f['intra_ext_atr'] = (entry_px - open_px) / atr if atr > 0 else np.nan

    # Daily RSI with the live price standing in for today's close (what live SHOULD compute)
    closes = pd.concat([prior['close'], pd.Series([entry_px], index=[day])])
    f['rsi_daily'] = float(rsi(closes).iloc[-1])
    f['above_ma20'] = entry_px > float(prior['close'].iloc[-20:].mean())

    if closed.empty:
        # Entry inside the first 5-min bar: no intraday structure yet
        f.update({k: np.nan for k in ('hod', 'pvh', 'hod_age_min', 'retrace', 'range_atr',
                                      'dist_vwap_pct', 'dist_vwap_atr', 'run_15m', 'run_30m',
                                      'pos_in_range', 'rsi5m', 'rvol_tod', 'upvol6',
                                      'consec_green', 'bars_closed')})
        f['bars_closed'] = 0
        return f

    hod = max(float(closed['high'].max()), entry_px)
    lod = min(float(closed['low'].min()), entry_px)
    hod_bar_ts = closed['high'].idxmax()
    f['hod'] = hod
    f['pvh'] = (entry_px / float(closed['high'].max()) - 1) * 100   # vs HOD of closed bars
    f['hod_age_min'] = (entry_ts - hod_bar_ts).total_seconds() / 60
    run = float(closed['high'].max()) - open_px
    f['retrace'] = ((float(closed['high'].max()) - entry_px) / run) if run > 0 else np.nan
    f['range_atr'] = (hod - lod) / atr if atr > 0 else np.nan
    tp = (closed['high'] + closed['low'] + closed['close']) / 3
    vwap = float((tp * closed['volume']).sum() / max(closed['volume'].sum(), 1))
    f['dist_vwap_pct'] = (entry_px / vwap - 1) * 100
    f['dist_vwap_atr'] = (entry_px - vwap) / atr if atr > 0 else np.nan

    def px_ago(mins):
        ref = closed[closed.index + pd.Timedelta(minutes=5) <= entry_ts - pd.Timedelta(minutes=mins)]
        return float(ref['close'].iloc[-1]) if not ref.empty else open_px
    f['run_15m'] = (entry_px / px_ago(15) - 1) * 100
    f['run_30m'] = (entry_px / px_ago(30) - 1) * 100
    f['pos_in_range'] = (entry_px - lod) / (hod - lod) if hod > lod else np.nan

    hist5 = bars[bars.index + pd.Timedelta(minutes=5) <= entry_ts]['close'].iloc[-60:]
    f['rsi5m'] = float(rsi(hist5).iloc[-1]) if len(hist5) > 15 else np.nan

    # Time-of-day relative volume: today's cum volume vs the same clock window, prior 20 sessions
    cutoff = closed.index[-1].time()
    cum_today = float(closed['volume'].sum())
    prior_days = sorted(set(bars.index.normalize()) - {entry_ts.normalize()})
    prior_days = [d for d in prior_days if d < entry_ts.normalize()][-20:]
    cums = []
    for d in prior_days:
        dd = bars[(bars.index.normalize() == d)]
        dd = dd[dd.index.time <= cutoff]
        if not dd.empty:
            cums.append(float(dd['volume'].sum()))
    f['rvol_tod'] = cum_today / np.mean(cums) if cums and np.mean(cums) > 0 else np.nan

    last6 = closed.iloc[-6:]
    tot = float(last6['volume'].sum())
    f['upvol6'] = float(last6[last6['close'] > last6['open']]['volume'].sum()) / tot if tot > 0 else np.nan
    cg = 0
    for _, b in closed.iloc[::-1].iterrows():
        if b['close'] > b['open']:
            cg += 1
        else:
            break
    f['consec_green'] = cg
    f['bars_closed'] = len(closed)
    return f


def forward_outcomes(bars, entry_ts, entry_px):
    day = entry_ts.normalize()
    after = bars[(bars.index.normalize() == day) & (bars.index + pd.Timedelta(minutes=5) > entry_ts)]
    out = {}
    for mins in (30, 60, 120):
        w = after[after.index < entry_ts + pd.Timedelta(minutes=mins)]
        if w.empty:
            out[f'mfe_{mins}'] = out[f'mae_{mins}'] = out[f'ret_{mins}'] = np.nan
            continue
        out[f'mfe_{mins}'] = (float(w['high'].max()) / entry_px - 1) * 100
        out[f'mae_{mins}'] = (float(w['low'].min()) / entry_px - 1) * 100
        out[f'ret_{mins}'] = (float(w['close'].iloc[-1]) / entry_px - 1) * 100
    if not after.empty:
        out['mfe_day'] = (float(after['high'].max()) / entry_px - 1) * 100
        out['mae_day'] = (float(after['low'].min()) / entry_px - 1) * 100
        out['ret_close'] = (float(after['close'].iloc[-1]) / entry_px - 1) * 100
    return out


def main():
    con = sqlite3.connect('trades.db')
    trades = pd.read_sql_query(
        "SELECT id, symbol, entry_date, entry_time, entry_price, exit_date, exit_time, exit_price, "
        "shares, pnl, pnl_pct, exit_reason, setup_type, rsi_at_entry, volume_ratio, sector, "
        "confidence, max_gain_pct, regime_at_entry FROM trades "
        "WHERE side='LONG' AND status!='OPEN' AND setup_type NOT IN ('RECONCILED','MANUAL') "
        "AND entry_price > 0 ORDER BY entry_date, entry_time", con)
    con.close()
    trades['entry_ts'] = pd.to_datetime(trades['entry_date'] + ' ' + trades['entry_time']).dt.tz_localize(ET)

    rows, missing = [], []
    for sym, grp in trades.groupby('symbol'):
        start = (grp['entry_ts'].min() - timedelta(days=60)).strftime('%Y-%m-%d')
        end = (grp['entry_ts'].max() + timedelta(days=2)).strftime('%Y-%m-%d')
        bars = load_bars(sym, start=start, end=end)
        if bars.empty:
            missing.extend(grp['id'].tolist())
            continue
        bars = bars.between_time('09:30', '15:55')
        daily = daily_from_5m(bars)
        for _, t in grp.iterrows():
            f = features_for_trade(bars, daily, t['entry_ts'], float(t['entry_price']))
            if f is None:
                missing.append(t['id'])
                continue
            f.update(forward_outcomes(bars, t['entry_ts'], float(t['entry_price'])))
            rows.append({**t.to_dict(), **f})

    df = pd.DataFrame(rows).sort_values('entry_ts')
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, 'daytrader_entries.csv')
    df.to_csv(path, index=False)
    print(f"{len(df)} trades with features -> {path}; {len(missing)} without bars "
          f"(ids {missing[:15]}{'...' if len(missing) > 15 else ''})")


if __name__ == '__main__':
    main()
