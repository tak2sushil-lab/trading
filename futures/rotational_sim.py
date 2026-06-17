#!/usr/bin/env python3
"""
futures/rotational_sim.py — ROTATIONAL mode research (NOT yet live)

When IB closes in the middle of its range (0.25–0.75), the day is ROTATIONAL
(not directional). The current trend-following system bleeds on these days.
This module implements range-fade logic for ROTATIONAL days and is used for
research/backtesting only until conviction is established.

Detection signal (at 10:30 IB formation):
    ib_mid_pct = (ib_close - ib_lo) / (ib_hi - ib_lo)
    0.25–0.75  → ROTATIONAL
    <0.25      → BEARISH DIRECTIONAL
    >0.75      → BULLISH DIRECTIONAL

Trade logic (post-IB, post-crash range):
    SHORT when price at top 22% of post-crash range + RR>=2 + hero confirms
    LONG  when price at bot 22% of post-crash range + RR>=2 + hero confirms
    Stop:   25pts (tight, just outside range boundary)
    Target: range 40%/60% level (or VWAP if deeper)
    Max 4 trades/day, 80-min time stop

Pre-market bias (Cards 0 — see oracle_today.py):
    DIST_TOP  → RTH opened near PM ceiling → allow SHORT only
    DIST_BOT  → RTH opened near PM floor   → allow LONG only
    MID       → both directions allowed

Status: Research only. Needs 60+ ROTATIONAL days to build conviction.
        Results so far (22 days): 19 trades, 36.8% WR, +$506 total.
"""
import datetime as dt
import sqlite3
import pandas as pd
import numpy as np

from futures.collect_bars import load_bars, filter_ny_session
import futures.sim_replay as sr
from futures.hero_score import score_entry_regime, contracts_from_regime_score
from futures.feature_study import get_prior_rth

NY = 'America/New_York'

IB_ROTATIONAL_LO = 0.25
IB_ROTATIONAL_HI = 0.75
STOP_PTS         = 25
MIN_RR           = 2.0
RANGE_ENTRY_TOP  = 0.78   # SHORT when price >= top 22% of range
RANGE_ENTRY_BOT  = 0.22   # LONG  when price <= bot 22% of range
RANGE_TARGET_HI  = 0.60   # LONG  target = 60% level
RANGE_TARGET_LO  = 0.40   # SHORT target = 40% level
MIN_RANGE_PTS    = 60      # range must be at least 60pts to trade
MAX_TRADES       = 4
MAX_HOLD_BARS    = 16      # 80 min


def get_premarket(conn_or_path, trade_date):
    """
    Read pre-market bars (7am–9:30 ET) and classify where RTH opened
    relative to the overnight range.

    Returns dict:
        bias     : 'DIST_TOP' | 'DIST_BOT' | 'MID' | 'UNKNOWN'
        pm_hi    : float
        pm_lo    : float
        open_pct : float  (0=PM low, 1=PM high)
        slope    : float  (last-30min slope pts/bar — positive=rising)
    """
    if isinstance(conn_or_path, str):
        conn = sqlite3.connect(conn_or_path)
        own  = True
    else:
        conn = conn_or_path
        own  = False
    try:
        df = pd.read_sql(
            f"SELECT ts_utc,open,high,low,close FROM futures_bars_5m "
            f"WHERE symbol='MNQ' AND date(ts_utc)='{trade_date}' ORDER BY ts_utc", conn)
    finally:
        if own: conn.close()

    if df.empty:
        return {'bias': 'UNKNOWN', 'pm_hi': 0, 'pm_lo': 0, 'open_pct': 0.5, 'slope': 0}

    df['ts'] = pd.to_datetime(df['ts_utc'], utc=True, format='ISO8601').dt.tz_convert(NY)
    pm  = df[((df['ts'].dt.hour >= 7) & (df['ts'].dt.hour < 9)) |
             ((df['ts'].dt.hour == 9) & (df['ts'].dt.minute < 30))]
    rth = df[(df['ts'].dt.hour == 9) & (df['ts'].dt.minute >= 30)]

    if pm.empty or rth.empty:
        return {'bias': 'UNKNOWN', 'pm_hi': 0, 'pm_lo': 0, 'open_pct': 0.5, 'slope': 0}

    pm_hi  = float(pm['high'].max())
    pm_lo  = float(pm['low'].min())
    rth_op = float(rth['open'].iloc[0])
    rng    = pm_hi - pm_lo
    op_pct = (rth_op - pm_lo) / rng if rng > 0 else 0.5
    tail   = pm.tail(6)
    slope  = (float(tail['close'].iloc[-1]) - float(tail['close'].iloc[0])) / max(len(tail), 1)

    bias = 'DIST_TOP' if op_pct >= 0.80 else ('DIST_BOT' if op_pct <= 0.20 else 'MID')
    return {'bias': bias, 'pm_hi': pm_hi, 'pm_lo': pm_lo, 'open_pct': op_pct, 'slope': slope}


def classify_ib(rth_bars):
    """
    Returns (ib_mid_pct, ib_hi, ib_lo, classification).
    classification: 'ROTATIONAL' | 'BULL_DIRECTIONAL' | 'BEAR_DIRECTIONAL'
    """
    ib_w   = rth_bars[rth_bars.index.time <= pd.Timestamp('2000-01-01 10:30').time()]
    if len(ib_w) < 2:
        return 0.5, 0, 0, 'UNKNOWN'
    ib_hi  = float(ib_w['high'].max())
    ib_lo  = float(ib_w['low'].min())
    ib_rng = ib_hi - ib_lo
    ib_cl  = float(ib_w['close'].iloc[-1])
    pct    = (ib_cl - ib_lo) / ib_rng if ib_rng > 0 else 0.5
    if IB_ROTATIONAL_LO <= pct <= IB_ROTATIONAL_HI:
        kind = 'ROTATIONAL'
    elif pct > IB_ROTATIONAL_HI:
        kind = 'BULL_DIRECTIONAL'
    else:
        kind = 'BEAR_DIRECTIONAL'
    return pct, ib_hi, ib_lo, kind


def simulate_day(all_bars, trade_date, prior_rth, pm, verbose=False):
    """
    Simulate rotational mode for one day.
    Returns list of trade dicts.
    """
    rth = filter_ny_session(all_bars[all_bars.index.date == trade_date]).copy()
    if len(rth) < 10:
        return []

    rth['typ']  = (rth['high'] + rth['low'] + rth['close']) / 3
    rth['vwap'] = (rth['typ'] * rth['volume']).cumsum() / rth['volume'].cumsum()

    ib_pct, ib_hi, ib_lo, ib_kind = classify_ib(rth)
    if ib_kind != 'ROTATIONAL':
        return []

    # PM bias gates which direction we'll trade
    pm_bias = pm['bias']
    allowed = (
        {'SHORT', 'LONG'} if pm_bias in ('MID', 'UNKNOWN') else
        {'SHORT'}          if pm_bias == 'DIST_TOP' else
        {'LONG'}
    )

    trades, in_trade = [], False
    t_side = t_entry = t_stop = t_target = t_time = None
    crash_t = pd.Timestamp('2000-01-01 10:45').time()
    scan    = [(h, m) for h in range(10, 16) for m in range(0, 60, 5) if (h, m) >= (10, 35)]

    for hh, mm in scan:
        snap_t  = pd.Timestamp(f'2000-01-01 {hh:02d}:{mm:02d}').time()
        bar     = rth[rth.index.time <= snap_t]
        if bar.empty:
            continue
        price   = float(bar.iloc[-1]['close'])
        vwap    = float(bar.iloc[-1]['vwap'])

        post    = rth[(rth.index.time >= crash_t) & (rth.index.time <= snap_t)]
        rlo     = float(post['low'].min())  if not post.empty else ib_lo
        rhi     = float(post['high'].max()) if not post.empty else ib_hi
        rsp     = rhi - rlo
        pct     = (price - rlo) / rsp if rsp > 1 else 0.5

        # Range-stable gate: last 3 bars must not have set a new range extreme
        stable = True
        if not post.empty and len(post) >= 3:
            l3 = post.tail(3)
            if float(l3['high'].max()) >= rhi or float(l3['low'].min()) <= rlo:
                stable = False

        # Exit
        if in_trade:
            hit_s  = (t_side == 'LONG'  and price <= t_stop)  or \
                     (t_side == 'SHORT' and price >= t_stop)
            hit_t  = (t_side == 'LONG'  and price >= t_target) or \
                     (t_side == 'SHORT' and price <= t_target)
            tsn    = pd.Timestamp(f'2000-01-01 {t_time}').time()
            held   = len(rth[(rth.index.time > tsn) & (rth.index.time <= snap_t)])
            eod    = hh >= 15 and mm >= 40
            if hit_s or hit_t or held >= MAX_HOLD_BARS or eod:
                ep   = t_stop if hit_s else (t_target if hit_t else price)
                pnl  = (ep - t_entry) * (1 if t_side == 'LONG' else -1) * 2
                rsn  = ('STOP'   if hit_s   else
                        'TARGET' if hit_t   else
                        'TIME'   if held >= MAX_HOLD_BARS else 'EOD')
                trades.append({
                    'date': str(trade_date), 'side': t_side,
                    'entry': t_entry, 'exit': ep, 'pnl': pnl,
                    'reason': rsn, 'entry_time': t_time,
                })
                if verbose:
                    print(f'    {"WIN" if pnl > 0 else "LOS"} {t_side} '
                          f'{t_entry:.0f}->{ep:.0f}  ${pnl:+.0f} [{rsn}]')
                in_trade = False

        # Entry
        if not in_trade and len(trades) < MAX_TRADES and stable:
            bu  = all_bars[all_bars.index <=
                           pd.Timestamp(trade_date).tz_localize(NY).replace(hour=hh, minute=mm)]
            atr = sr.compute_atr(bu.iloc[-100:])
            sig = None

            if 'SHORT' in allowed and pct >= RANGE_ENTRY_TOP and rsp >= MIN_RANGE_PTS:
                tgt = rlo + rsp * RANGE_TARGET_LO
                if vwap < price - 10:
                    tgt = min(tgt, vwap)
                sl  = price + STOP_PTS
                rr  = (price - tgt) / STOP_PTS
                if rr >= MIN_RR:
                    hs, _ = score_entry_regime(price, atr, 'SHORT', bu, prior_rth, 'CHOPPY')
                    if contracts_from_regime_score(hs, 'CHOPPY', 1) > 0:
                        sig = ('SHORT', sl, tgt, rr)

            if 'LONG' in allowed and pct <= RANGE_ENTRY_BOT and rsp >= MIN_RANGE_PTS and not sig:
                tgt = rlo + rsp * RANGE_TARGET_HI
                if vwap > price + 10:
                    tgt = max(tgt, vwap)
                sl  = price - STOP_PTS
                rr  = (tgt - price) / STOP_PTS
                if rr >= MIN_RR:
                    hl, _ = score_entry_regime(price, atr, 'LONG', bu, prior_rth, 'CHOPPY')
                    if contracts_from_regime_score(hl, 'CHOPPY', 1) > 0:
                        sig = ('LONG', sl, tgt, rr)

            if sig:
                side, sl, tgt, rr = sig
                if verbose:
                    print(f'  ENTER {side:5} {price:.0f}  sl={sl:.0f}  tgt={tgt:.0f}'
                          f'  RR={rr:.1f}  box={rlo:.0f}-{rhi:.0f}({rsp:.0f}pt)')
                in_trade   = True
                t_side     = side
                t_entry    = price
                t_stop     = sl
                t_target   = tgt
                t_time     = f'{hh:02d}:{mm:02d}'

    if in_trade:
        lp  = float(rth['close'].iloc[-1])
        pnl = (lp - t_entry) * (1 if t_side == 'LONG' else -1) * 2
        trades.append({
            'date': str(trade_date), 'side': t_side,
            'entry': t_entry, 'exit': lp, 'pnl': pnl,
            'reason': 'EOD', 'entry_time': t_time,
        })
        if verbose:
            print(f'    {"WIN" if pnl > 0 else "LOS"} {t_side} '
                  f'{t_entry:.0f}->{lp:.0f}  ${pnl:+.0f} [EOD]')

    return trades


def run_all(start='2026-01-01', end=None, verbose_date=None):
    """Run rotational sim over a date range and print summary."""
    if end is None:
        end = dt.date.today().isoformat()

    conn     = sqlite3.connect('market_data.db')
    load_s   = (dt.datetime.strptime(start, '%Y-%m-%d') - dt.timedelta(days=110)).strftime('%Y-%m-%d')
    all_bars = load_bars('MNQ', start=load_s, end=end)
    rth_all  = filter_ny_session(all_bars)
    all_d    = sorted(set(rth_all.index.date))

    rot_days = []
    for td in all_d:
        if str(td) < start: continue
        rth = filter_ny_session(all_bars[all_bars.index.date == td])
        if len(rth) < 10: continue
        _, _, _, kind = classify_ib(rth)
        if kind == 'ROTATIONAL':
            rot_days.append(td)

    print(f'ROTATIONAL SIM  ({start} → {end})')
    print(f'Rotational days: {len(rot_days)}\n')
    print(f'  {"Date":<12} {"PM":<10} {"N":<3} {"WR":<5} {"PnL":>7}   Trades')
    print('─' * 70)

    all_t = []
    for td in rot_days:
        prior  = get_prior_rth(all_bars, td)
        pm     = get_premarket(conn, td)
        verb   = (verbose_date and td == verbose_date)
        if verb: print(f'\n[{td} verbose]:')
        trades = simulate_day(all_bars, td, prior, pm, verbose=verb)
        if verb: print()
        all_t.extend(trades)
        n   = len(trades)
        pnl = sum(t['pnl'] for t in trades)
        wr  = sum(1 for t in trades if t['pnl'] > 0) / n * 100 if n > 0 else 0
        dtl = '  '.join([f'{"W" if t["pnl"]>0 else "L"}${int(t["pnl"]):+d}'
                         for t in trades]) if n else '—'
        print(f'  {td}  {pm["bias"]:<10} {n:<3} {int(wr):>3}%  ${pnl:>+6.0f}   {dtl}')

    conn.close()

    print('\n' + '=' * 70)
    n_all = len(all_t)
    if n_all > 0:
        tot  = sum(t['pnl'] for t in all_t)
        wins = sum(1 for t in all_t if t['pnl'] > 0)
        print(f'TOTAL: {n_all} trades  WR={wins/n_all*100:.1f}%  '
              f'avg=${tot/n_all:+.1f}/trade  total=${tot:+.0f}')
        from collections import Counter
        for rsn, cnt in Counter(t['reason'] for t in all_t).most_common():
            sub = [t for t in all_t if t['reason'] == rsn]
            w2  = sum(1 for t in sub if t['pnl'] > 0) / len(sub) * 100
            print(f'  {rsn:<8} N={cnt:2d}  WR={w2:.0f}%  '
                  f'avg=${np.mean([t["pnl"] for t in sub]):+.1f}')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2026-01-01')
    ap.add_argument('--end',   default=None)
    ap.add_argument('--verbose-date', default=None)
    args = ap.parse_args()
    vd = dt.date.fromisoformat(args.verbose_date) if args.verbose_date else None
    run_all(args.start, args.end, verbose_date=vd)
