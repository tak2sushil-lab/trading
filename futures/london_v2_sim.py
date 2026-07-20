#!/usr/bin/env python
"""
London v2 — ground-up rebuild sim (Jul 18 2026).

Why v1 failed live (validated findings, see CLAUDE.md Jul 17):
  1. BE=0.10×ATR armed ~1 min after entry on the 15-sec live monitor → constant
     breakeven scratches. v1's 5-min-bar sim never modeled that churn.
  2. Signal A enters on a single IB touch — no volume or acceptance
     confirmation → near-instant fakeout stop-outs (the confirmed cold streak).
  3. Overnight-bias "skip_day" vetoes the whole session at 3am (missed the
     Jul 8 427pt move).

v2 design (this sim):
  - Simulates on 1-MINUTE bars (futures_bars_1m) so BE/stop/trail behavior
    matches live monitor granularity. Signals still evaluated on 5-min closes
    (live scan cadence).
  - Toggles: --vol-confirm (breakout 5m bar vol >= 1.2x prior 12-bar avg),
    --acceptance (2 consecutive 5m closes beyond IB), --skip-day (v1's
    overnight veto, default OFF in v2).
  - BE trigger is a grid parameter — v1's 0.10 is tested honestly at 1m
    granularity alongside larger values and none.

Usage:
  venv/bin/python futures/london_v2_sim.py --start 2025-01-01 --grid
  venv/bin/python futures/london_v2_sim.py --start 2025-01-01 \
      --vol-confirm --acceptance --be-mult 1.0
"""
import argparse, os, sqlite3
from datetime import datetime, time as dtime

import numpy as np
import pandas as pd
import pytz

ET = pytz.timezone('America/New_York')
MARKET_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'market_data.db')

STOP_ATR = 2.0      # v1 champion — kept
TGT_ATR  = 6.0      # v1 champion — kept (trail does the work)
TRAIL_WIDE_ATR  = 1.00   # profit (ATR) at which wide trail (1.0 ATR gap) arms
TRAIL_TIGHT_ATR = 1.50   # profit (ATR) at which tight trail (0.5 ATR gap) arms
MAX_TRADES_DAY  = 2
DOLLARS_PER_PT  = 2.0    # 1 MNQ contract
VOL_CONFIRM_MULT = 1.2
OVN_SKIP_LO, OVN_SKIP_HI = 0.20, 0.40


def load_days(start, end):
    con = sqlite3.connect(MARKET_DB)
    b = pd.read_sql(
        "select ts_utc, open, high, low, close, volume from futures_bars_1m "
        "where symbol='MNQ' and ts_utc>=? and ts_utc<=?", con,
        params=(start, end + 'T23:59:59'))
    con.close()
    b['ts'] = pd.to_datetime(b['ts_utc'], format='ISO8601', utc=True).dt.tz_convert(ET)
    b = b.sort_values('ts').set_index('ts')
    b['d'] = b.index.date.astype(str)
    return b


def day_session(day_bars):
    """Slice 3:00–9:00 ET; return (ib_hi, ib_lo, entry_bars_1m, bars5, ovn_pos) or None."""
    s = day_bars.between_time(dtime(3, 0), dtime(8, 59))
    if len(s) < 120:
        return None
    ib = s.between_time(dtime(3, 0), dtime(3, 59))
    if len(ib) < 30:
        return None
    ib_hi, ib_lo = ib['high'].max(), ib['low'].min()
    # overnight position for the optional v1 skip gate: 7pm prev — 3am window
    ovn = day_bars.between_time(dtime(0, 0), dtime(2, 59))
    ovn_pos = None
    if len(ovn) > 30:
        lo, hi = ovn['low'].min(), ovn['high'].max()
        if hi > lo:
            ovn_pos = (ib['open'].iloc[0] - lo) / (hi - lo)
    bars5 = s.resample('5min').agg({'open': 'first', 'high': 'max', 'low': 'min',
                                    'close': 'last', 'volume': 'sum'}).dropna()
    atr5 = (bars5['high'] - bars5['low']).rolling(14).mean()
    return ib_hi, ib_lo, s, bars5, atr5, ovn_pos


def run_day(sess, cfg):
    ib_hi, ib_lo, s1, bars5, atr5, ovn_pos = sess
    if cfg['skip_day'] and ovn_pos is not None and OVN_SKIP_LO <= ovn_pos < OVN_SKIP_HI:
        return []
    trades = []
    entry_from = dtime(4, 0)
    i = 0
    b5 = bars5.reset_index()
    while i < len(b5) and len(trades) < MAX_TRADES_DAY:
        row = b5.iloc[i]
        t5 = row['ts'].time() if 'ts' in row else b5['index'].iloc[i].time()
        tcol = 'ts' if 'ts' in b5.columns else 'index'
        t5 = b5[tcol].iloc[i].time()
        if t5 < entry_from or t5 >= dtime(8, 0):
            i += 1; continue
        atr = atr5.iloc[i] if i < len(atr5) else np.nan
        if pd.isna(atr) or atr <= 0:
            i += 1; continue
        side = None
        if row['close'] > ib_hi: side = 'LONG'
        elif row['close'] < ib_lo: side = 'SHORT'
        if side is None:
            i += 1; continue
        # volume confirmation on the breakout 5m bar
        if cfg['vol_confirm']:
            prior = b5['volume'].iloc[max(0, i - 12):i]
            if len(prior) < 6 or row['volume'] < VOL_CONFIRM_MULT * prior.mean():
                i += 1; continue
        # acceptance: next 5m close also beyond the level
        sig_i = i
        if cfg['acceptance']:
            if i + 1 >= len(b5):
                break
            nxt = b5.iloc[i + 1]
            ok = nxt['close'] > ib_hi if side == 'LONG' else nxt['close'] < ib_lo
            if not ok:
                i += 1; continue
            sig_i = i + 1
        # entry at first 1m bar after the signal 5m bar completes
        sig_end = b5[tcol].iloc[sig_i] + pd.Timedelta(minutes=5)
        fut1 = s1[s1.index >= sig_end]
        if len(fut1) < 3:
            break
        e = float(fut1.iloc[0]['open'])
        sign = 1 if side == 'LONG' else -1
        stop = e - sign * STOP_ATR * atr
        tgt  = e + sign * TGT_ATR * atr
        be_armed = False
        exit_p, reason = None, 'eod'
        for ts_, bar in fut1.iterrows():
            lo_, hi_ = bar['low'], bar['high']
            fav = (hi_ - e) if side == 'LONG' else (e - lo_)
            # stop first (conservative)
            if (side == 'LONG' and lo_ <= stop) or (side == 'SHORT' and hi_ >= stop):
                exit_p, reason = stop, ('be' if be_armed and abs(stop - e) < 0.3 * atr else 'stop')
                break
            if (side == 'LONG' and hi_ >= tgt) or (side == 'SHORT' and lo_ <= tgt):
                exit_p, reason = tgt, 'target'
                break
            # trail management on closed 1m bar
            c = bar['close']
            prof = sign * (c - e)
            if cfg['be_mult'] is not None and not be_armed and prof >= cfg['be_mult'] * atr:
                stop = e + sign * 0.05 * atr   # entry + tick-ish
                be_armed = True
            if prof >= TRAIL_TIGHT_ATR * atr:
                ns = c - sign * 0.5 * atr
            elif prof >= TRAIL_WIDE_ATR * atr:
                ns = c - sign * 1.0 * atr
            else:
                ns = None
            if ns is not None and sign * (ns - stop) > 0:
                stop = ns
        if exit_p is None:
            exit_p = float(fut1.iloc[-1]['close'])
        pnl = sign * (exit_p - e) * DOLLARS_PER_PT
        trades.append({'side': side, 'entry': e, 'exit': exit_p,
                       'pnl': pnl, 'reason': reason,
                       'time': fut1.index[0].strftime('%H:%M')})
        # resume scanning after exit — find 5m index past exit time
        i = sig_i + 1
        # (simplification: continue from next 5m bar; MAX_TRADES_DAY caps churn)
    return trades


def simulate(bars, cfg, start):
    all_tr = []
    for d, day in bars.groupby('d'):
        if d < start:
            continue
        if pd.Timestamp(d).weekday() >= 5:
            continue
        sess = day_session(day)
        if sess is None:
            continue
        for t in run_day(sess, cfg):
            t['d'] = d
            all_tr.append(t)
    return pd.DataFrame(all_tr)


def report(name, tr):
    if tr.empty:
        print(f"{name:<46} no trades"); return
    daily = tr.groupby('d')['pnl'].sum()
    eq = daily.cumsum()
    dd = (eq - eq.cummax()).min()
    yrs = tr.assign(y=tr['d'].str[:4]).groupby('y')['pnl'].agg(['count', 'sum'])
    ystr = '  '.join(f"{y}:{int(r['count'])}t ${r['sum']:+.0f}" for y, r in yrs.iterrows())
    print(f"{name:<46} n={len(tr):<4} WR={(tr['pnl']>0).mean():.0%} "
          f"P&L=${tr['pnl'].sum():+8.0f} MaxDD=${dd:+.0f}  [{ystr}]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2025-01-01')
    ap.add_argument('--end',   default=datetime.now(ET).strftime('%Y-%m-%d'))
    ap.add_argument('--vol-confirm', action='store_true')
    ap.add_argument('--acceptance',  action='store_true')
    ap.add_argument('--skip-day',    action='store_true', help='v1 overnight whole-day veto')
    ap.add_argument('--be-mult', type=float, default=None,
                    help='BE arm profit in ATR (omit for no BE stop)')
    ap.add_argument('--grid', action='store_true')
    a = ap.parse_args()

    print(f"Loading 1m bars {a.start} → {a.end} ...")
    bars = load_days(a.start, a.end)
    print(f"bars: {len(bars):,}")

    if not a.grid:
        cfg = {'vol_confirm': a.vol_confirm, 'acceptance': a.acceptance,
               'skip_day': a.skip_day, 'be_mult': a.be_mult}
        tr = simulate(bars, cfg, a.start)
        report(str(cfg), tr)
        if not tr.empty:
            print("\nby exit reason:")
            print(tr.groupby('reason')['pnl'].agg(['count', 'sum']).round(0).to_string())
        return

    grid = [
        ('v1-emulated (BE 0.10, skip-day, no confirms)',
         {'vol_confirm': False, 'acceptance': False, 'skip_day': True,  'be_mult': 0.10}),
        ('v1 no-skip (BE 0.10)',
         {'vol_confirm': False, 'acceptance': False, 'skip_day': False, 'be_mult': 0.10}),
        ('v2: confirms only (BE 0.10)',
         {'vol_confirm': True,  'acceptance': True,  'skip_day': False, 'be_mult': 0.10}),
        ('v2: confirms + BE 0.50',
         {'vol_confirm': True,  'acceptance': True,  'skip_day': False, 'be_mult': 0.50}),
        ('v2: confirms + BE 1.00',
         {'vol_confirm': True,  'acceptance': True,  'skip_day': False, 'be_mult': 1.00}),
        ('v2: confirms + no BE',
         {'vol_confirm': True,  'acceptance': True,  'skip_day': False, 'be_mult': None}),
        ('v2: vol-confirm only + BE 1.00',
         {'vol_confirm': True,  'acceptance': False, 'skip_day': False, 'be_mult': 1.00}),
        ('v2: acceptance only + BE 1.00',
         {'vol_confirm': False, 'acceptance': True,  'skip_day': False, 'be_mult': 1.00}),
    ]
    for name, cfg in grid:
        tr = simulate(bars, cfg, a.start)
        report(name, tr)


if __name__ == '__main__':
    main()
