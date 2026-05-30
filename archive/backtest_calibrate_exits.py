#!/usr/bin/env python3
"""
backtest_calibrate_exits.py
Data-driven parameter sweep to find the right calibration for dynamic exits.
Goal: zero false positives on actual winners, maximum improvement on losers.

Sweeps:
  Gap2: mins=[60,90,120,150] x loss_pct=[0.5,0.75,1.0,1.5] → 16 combos
  Protection: gap_pct=[0.25,0.35,0.45] of peak
  Correlation: mins=[60,90,120] x n_losers=[2,3] x loss_pct=[0.3,0.5]
"""

import sqlite3, yfinance as yf, pandas as pd, numpy as np
from datetime import datetime, date, timedelta
import warnings; warnings.filterwarnings('ignore')

DB = 'trades.db'

# ── Load data ─────────────────────────────────────────────────────────────────

def load_trades():
    conn = sqlite3.connect(DB)
    df = pd.read_sql("""
        SELECT id, symbol, side, entry_date, entry_time, exit_time,
               CAST(entry_price AS FLOAT) as entry_px,
               CAST(exit_price AS FLOAT) as exit_px,
               CAST(shares AS INT) as qty,
               CAST(pnl AS FLOAT) as actual_pnl,
               max_gain_pct, exit_reason
        FROM trades WHERE entry_date >= '2026-05-01' AND exit_time IS NOT NULL
        ORDER BY entry_date, entry_time
    """, conn)
    conn.close()
    return df

def pull_intraday(symbol, trade_date):
    try:
        t = yf.Ticker(symbol)
        end = trade_date + timedelta(days=1)
        df = t.history(start=trade_date, end=end, interval='5m', auto_adjust=True)
        if df.empty: return None
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize('America/New_York')
        else:
            df.index = df.index.tz_convert('America/New_York')
        return df.between_time('09:30', '15:55')
    except:
        return None

def parse_dt(date_str, time_str, tz='America/New_York'):
    raw = pd.Timestamp(f"{date_str} {time_str[:8]}")
    return raw.tz_localize(tz)

def get_price_at(bars, dt):
    if bars is None or bars.empty: return None
    m = bars[bars.index <= dt]
    return float(m.iloc[-1]['Close']) if not m.empty else float(bars.iloc[0]['Close'])

# ── Per-trade Gap2 simulation (parametric) ────────────────────────────────────

def sim_gap2(trade, bars, mins_thresh, loss_thresh, peak_thresh=1.5):
    entry_px = float(trade['entry_px'])
    qty      = int(trade['qty']) if trade['qty'] else 0
    side     = trade['side']
    actual   = float(trade['actual_pnl'])

    if bars is None or qty == 0 or bars.empty:
        return actual, False, None

    try:
        entry_dt = parse_dt(trade['entry_date'], str(trade['entry_time']))
    except:
        return actual, False, None

    sess_ext = entry_px
    for bar_time, bar in bars[bars.index >= entry_dt].iterrows():
        mins = (bar_time - entry_dt).total_seconds() / 60
        px   = float(bar['Close'])

        if side == 'LONG':
            sess_ext = max(sess_ext, float(bar['High']))
            max_gain = (sess_ext - entry_px) / entry_px * 100
            cur_loss = (entry_px - px) / entry_px * 100
            new_pnl  = (px - entry_px) * qty
        else:
            sess_ext = min(sess_ext, float(bar['Low']))
            max_gain = (entry_px - sess_ext) / entry_px * 100
            cur_loss = (px - entry_px) / entry_px * 100
            new_pnl  = (entry_px - px) * qty

        if mins >= mins_thresh and max_gain < peak_thresh and cur_loss > loss_thresh:
            return round(new_pnl, 2), True, bar_time

    return actual, False, None

# ── Day-level Protection simulation (percentage-based) ────────────────────────

def sim_day_protection_pct(date_str, day_trades, day_intraday,
                           gap_pct, min_peak=120.0, cut_min_loss=0.5,
                           cut_max_peak=1.5):
    tz = 'America/New_York'
    states = []
    for _, tr in day_trades.iterrows():
        try:
            entry_dt = parse_dt(date_str, str(tr['entry_time']), tz)
        except:
            continue
        exit_t = str(tr['exit_time'])[:8]
        try:
            exit_hour = int(exit_t[:2])
            if exit_hour < 9:
                next_day = (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
                exit_dt = parse_dt(next_day, exit_t, tz)
            else:
                exit_dt = parse_dt(date_str, exit_t, tz)
        except:
            exit_dt = parse_dt(date_str, '15:52:00', tz)

        states.append({
            'id': tr['id'], 'symbol': tr['symbol'], 'side': tr['side'],
            'entry_px': float(tr['entry_px']), 'qty': int(tr['qty']) if tr['qty'] else 0,
            'actual_pnl': float(tr['actual_pnl']),
            'actual_exit': exit_dt, 'entry_dt': entry_dt,
            'bars': day_intraday.get(tr['symbol']),
            'sess_ext': float(tr['entry_px']), 'max_gain': 0.0,
            'new_pnl': float(tr['actual_pnl']), 'fired': False, 'new_exit': None,
        })

    if not states: return {}

    start_ts = pd.Timestamp(f"{date_str} 10:00:00", tz=tz)
    end_ts   = pd.Timestamp(f"{date_str} 15:52:00", tz=tz)
    timeline = pd.date_range(start_ts, end_ts, freq='5min')
    peak_pnl = 0.0

    for t in timeline:
        realized = sum(s['new_pnl'] for s in states if
                       (s['new_exit'] is not None and t >= s['new_exit']) or
                       (s['new_exit'] is None and t >= s['actual_exit']))
        unrealized = 0.0
        open_losers = []

        for s in states:
            if t < s['entry_dt']: continue
            exited = (s['new_exit'] is not None and t >= s['new_exit']) or \
                     (s['new_exit'] is None and t >= s['actual_exit'])
            if exited: continue

            px  = get_price_at(s['bars'], t) or s['entry_px']
            px  = float(px)
            mins = (t - s['entry_dt']).total_seconds() / 60

            if s['side'] == 'LONG':
                s['sess_ext'] = max(s['sess_ext'], px)
                s['max_gain'] = (s['sess_ext'] - s['entry_px']) / s['entry_px'] * 100
                cur_pct = (px - s['entry_px']) / s['entry_px'] * 100
                unreal  = (px - s['entry_px']) * s['qty']
            else:
                s['sess_ext'] = min(s['sess_ext'], px)
                s['max_gain'] = (s['entry_px'] - s['sess_ext']) / s['entry_px'] * 100
                cur_pct = (s['entry_px'] - px) / s['entry_px'] * 100
                unreal  = (s['entry_px'] - px) * s['qty']

            unrealized += unreal
            if (cur_pct < -cut_min_loss and s['max_gain'] < cut_max_peak
                    and mins >= 30 and not s['fired']):
                open_losers.append((s, px, cur_pct))

        portfolio = realized + unrealized
        if portfolio > peak_pnl:
            peak_pnl = portfolio

        # Fire protection if peak was significant AND portfolio dropped by gap_pct
        if peak_pnl >= min_peak and portfolio < peak_pnl * (1 - gap_pct):
            for s, px, cur_pct in open_losers:
                if not s['fired']:
                    if s['side'] == 'LONG':
                        new_pnl = (px - s['entry_px']) * s['qty']
                    else:
                        new_pnl = (s['entry_px'] - px) * s['qty']
                    s['new_pnl'] = round(new_pnl, 2)
                    s['new_exit'] = t
                    s['fired'] = True

    return {s['id']: {'actual_pnl': s['actual_pnl'], 'new_pnl': s['new_pnl'],
                      'fired': s['fired'], 'symbol': s['symbol']} for s in states}

# ── Correlation exit simulation ────────────────────────────────────────────────

def sim_day_correlation(date_str, day_trades, day_intraday,
                        corr_mins, n_losers, loss_pct, max_peak=1.5,
                        no_cut_symbols=None):
    no_cut = no_cut_symbols or set()
    tz = 'America/New_York'
    states = []
    for _, tr in day_trades.iterrows():
        try:
            entry_dt = parse_dt(date_str, str(tr['entry_time']), tz)
        except:
            continue
        exit_t = str(tr['exit_time'])[:8]
        try:
            exit_hour = int(exit_t[:2])
            if exit_hour < 9:
                next_day = (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
                exit_dt = parse_dt(next_day, exit_t, tz)
            else:
                exit_dt = parse_dt(date_str, exit_t, tz)
        except:
            exit_dt = parse_dt(date_str, '15:52:00', tz)

        states.append({
            'id': tr['id'], 'symbol': tr['symbol'], 'side': tr['side'],
            'entry_px': float(tr['entry_px']), 'qty': int(tr['qty']) if tr['qty'] else 0,
            'actual_pnl': float(tr['actual_pnl']),
            'actual_exit': exit_dt, 'entry_dt': entry_dt,
            'bars': day_intraday.get(tr['symbol']),
            'sess_ext': float(tr['entry_px']), 'max_gain': 0.0,
            'new_pnl': float(tr['actual_pnl']), 'fired': False, 'new_exit': None,
        })

    if not states: return {}

    start_ts = pd.Timestamp(f"{date_str} 10:00:00", tz=tz)
    end_ts   = pd.Timestamp(f"{date_str} 15:52:00", tz=tz)
    timeline = pd.date_range(start_ts, end_ts, freq='5min')

    for t in timeline:
        losers = []
        for s in states:
            if t < s['entry_dt']: continue
            exited = (s['new_exit'] is not None and t >= s['new_exit']) or \
                     (s['new_exit'] is None and t >= s['actual_exit'])
            if exited or s['fired']: continue
            if s['symbol'] in no_cut: continue

            px   = get_price_at(s['bars'], t) or s['entry_px']
            px   = float(px)
            mins = (t - s['entry_dt']).total_seconds() / 60

            if s['side'] == 'LONG':
                s['sess_ext'] = max(s['sess_ext'], px)
                s['max_gain'] = (s['sess_ext'] - s['entry_px']) / s['entry_px'] * 100
                cur_pct = (px - s['entry_px']) / s['entry_px'] * 100
                new_pnl = (px - s['entry_px']) * s['qty']
            else:
                s['sess_ext'] = min(s['sess_ext'], px)
                s['max_gain'] = (s['entry_px'] - s['sess_ext']) / s['entry_px'] * 100
                cur_pct = (s['entry_px'] - px) / s['entry_px'] * 100
                new_pnl = (s['entry_px'] - px) * s['qty']

            if mins >= corr_mins and cur_pct < -loss_pct and s['max_gain'] < max_peak:
                losers.append((s, px, cur_pct, new_pnl))

        if len(losers) >= n_losers:
            # Cut the weakest (most negative)
            losers.sort(key=lambda x: x[2])
            worst = losers[0]
            s, px, _, new_pnl = worst
            s['new_pnl'] = round(new_pnl, 2)
            s['new_exit'] = t
            s['fired'] = True

    return {s['id']: {'actual_pnl': s['actual_pnl'], 'new_pnl': s['new_pnl'],
                      'fired': s['fired'], 'symbol': s['symbol']} for s in states}

# ── Build intraday cache ───────────────────────────────────────────────────────

def build_cache(trades):
    print("Pulling 5-min intraday data (cached)...")
    cache = {}
    for _, t in trades[['entry_date','symbol']].drop_duplicates().iterrows():
        key = f"{t['entry_date']}_{t['symbol']}"
        if key not in cache:
            d = datetime.strptime(t['entry_date'], '%Y-%m-%d').date()
            cache[key] = pull_intraday(t['symbol'], d)
    ok = sum(1 for v in cache.values() if v is not None)
    print(f"  {ok}/{len(cache)} symbol-days available\n")
    return cache

# ── Helpers ────────────────────────────────────────────────────────────────────

def count_fp(results_by_id, trades):
    """Count false positives: fired=True but actual_pnl > 0"""
    fp = 0
    for tid, res in results_by_id.items():
        if res.get('fired') and float(res['actual_pnl']) > 0:
            fp += 1
    return fp

def net_delta(results_by_id, trades):
    new_total  = sum(float(r['new_pnl'])    for r in results_by_id.values())
    act_total  = sum(float(r['actual_pnl']) for r in results_by_id.values())
    return new_total - act_total, new_total

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("DATA-DRIVEN CALIBRATION — DYNAMIC EXIT OPTIONS")
    print("Goal: zero false positives on winners, maximum improvement on losers")
    print("=" * 70)

    trades = load_trades()
    cache  = build_cache(trades)

    actual_total = trades['actual_pnl'].sum()
    winners = trades[trades['actual_pnl'] > 0]
    losers  = trades[trades['actual_pnl'] < 0]
    print(f"May baseline: ${actual_total:.2f} | {len(winners)} winners | {len(losers)} losers")
    print(f"Loser pool: ${losers['actual_pnl'].sum():.2f} — max recoverable if all cut perfectly")

    # ── SWEEP A: Gap2 ─────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("SWEEP A: Gap2 Intraday — mins x loss_pct")
    print(f"{'mins':>6} {'loss%':>6} {'FP':>4} {'TP':>4} {'Fired':>6} {'Net Δ':>8} {'New Total':>10}  verdict")
    print("-" * 70)

    best_gap2 = None
    best_gap2_score = float('-inf')

    for mins in [60, 90, 120, 150]:
        for loss in [0.5, 0.75, 1.0, 1.5]:
            results = {}
            for _, tr in trades.iterrows():
                key  = f"{tr['entry_date']}_{tr['symbol']}"
                bars = cache.get(key)
                new_pnl, fired, _ = sim_gap2(tr, bars, mins, loss)
                results[tr['id']] = {
                    'actual_pnl': tr['actual_pnl'],
                    'new_pnl': new_pnl,
                    'fired': fired,
                    'symbol': tr['symbol'],
                }

            fp   = count_fp(results, trades)
            tp   = sum(1 for r in results.values()
                       if r['fired'] and float(r['actual_pnl']) <= 0
                       and float(r['new_pnl']) > float(r['actual_pnl']))
            fired_n = sum(1 for r in results.values() if r['fired'])
            delta, new_tot = net_delta(results, trades)

            verdict = '✅ CANDIDATE' if fp == 0 and delta > 0 else \
                      '⚠️  FP>0' if fp > 0 else '❌ worse'

            score = delta - fp * 200
            if score > best_gap2_score:
                best_gap2_score = score
                best_gap2 = (mins, loss, fp, tp, fired_n, delta, new_tot)

            print(f"{mins:>6} {loss:>6.2f} {fp:>4} {tp:>4} {fired_n:>6} "
                  f"{delta:>+8.2f} ${new_tot:>9.2f}  {verdict}")

    bm, bl, bfp, btp, bfired, bdelta, bnewtot = best_gap2
    print(f"\n★ Best Gap2: mins={bm}, loss={bl}% | FP={bfp} | TP={btp} | "
          f"Δ={bdelta:+.2f} | May total=${bnewtot:.2f}")

    # ── SWEEP B: P&L Protection (percentage-based) ───────────────────────────
    print("\n" + "=" * 70)
    print("SWEEP B: P&L Protection Floor — percentage drop from peak")
    print(f"{'gap%':>6} {'min_peak':>9} {'FP':>4} {'Fired':>6} {'Net Δ':>8} {'New Total':>10}  verdict")
    print("-" * 70)

    best_prot = None
    best_prot_score = float('-inf')

    for gap_pct in [0.20, 0.25, 0.30, 0.35, 0.40, 0.45]:
        for min_peak in [100, 120, 150]:
            all_res = {}
            for date_str, day_grp in trades.groupby('entry_date'):
                day_intraday = {sym: cache.get(f"{date_str}_{sym}")
                                for sym in day_grp['symbol'].unique()}
                day_res = sim_day_protection_pct(
                    date_str, day_grp, day_intraday,
                    gap_pct=gap_pct, min_peak=min_peak)
                all_res.update(day_res)

            if not all_res: continue
            fp     = sum(1 for r in all_res.values()
                         if r['fired'] and float(r['actual_pnl']) > 0)
            fired_n = sum(1 for r in all_res.values() if r['fired'])
            new_tot = sum(float(r['new_pnl']) for r in all_res.values())
            delta   = new_tot - actual_total

            verdict = '✅ CANDIDATE' if fp == 0 and delta > 0 else \
                      '⚠️  FP>0' if fp > 0 else '❌ worse'

            score = delta - fp * 200
            if score > best_prot_score:
                best_prot_score = score
                best_prot = (gap_pct, min_peak, fp, fired_n, delta, new_tot)

            print(f"{gap_pct:>6.0%} {min_peak:>9} {fp:>4} {fired_n:>6} "
                  f"{delta:>+8.2f} ${new_tot:>9.2f}  {verdict}")

    if best_prot:
        bgp, bmp, bfp2, bfired2, bdelta2, bnewtot2 = best_prot
        print(f"\n★ Best Protection: gap={bgp:.0%} of peak, min_peak=${bmp} | "
              f"FP={bfp2} | Δ={bdelta2:+.2f} | May total=${bnewtot2:.2f}")

    # ── SWEEP C: Correlation Exit ─────────────────────────────────────────────
    # High-WR textbook stocks excluded from correlation cut
    NO_CUT = {'AXON', 'MRVL', 'SMCI', 'QBTS', 'RKLB', 'CCJ', 'UUUU', 'CLS',
              'NVDA', 'ARM', 'IONQ', 'AMD', 'NBIS'}

    print("\n" + "=" * 70)
    print("SWEEP C: Correlation Exit — mins x n_losers x loss_pct (textbook stocks protected)")
    print(f"{'mins':>6} {'n_los':>6} {'loss%':>6} {'FP':>4} {'Fired':>6} {'Net Δ':>8} {'New Total':>10}  verdict")
    print("-" * 70)

    best_corr = None
    best_corr_score = float('-inf')

    for c_mins in [60, 90, 120]:
        for n_l in [2, 3]:
            for c_loss in [0.3, 0.5, 0.75]:
                all_res = {}
                for date_str, day_grp in trades.groupby('entry_date'):
                    day_intraday = {sym: cache.get(f"{date_str}_{sym}")
                                    for sym in day_grp['symbol'].unique()}
                    day_res = sim_day_correlation(
                        date_str, day_grp, day_intraday,
                        corr_mins=c_mins, n_losers=n_l,
                        loss_pct=c_loss, no_cut_symbols=NO_CUT)
                    all_res.update(day_res)

                if not all_res: continue
                fp     = sum(1 for r in all_res.values()
                             if r['fired'] and float(r['actual_pnl']) > 0)
                fired_n = sum(1 for r in all_res.values() if r['fired'])
                new_tot = sum(float(r['new_pnl']) for r in all_res.values())
                delta   = new_tot - actual_total

                verdict = '✅ CANDIDATE' if fp == 0 and delta > 0 else \
                          '⚠️  FP>0' if fp > 0 else '❌ worse'

                score = delta - fp * 200
                if score > best_corr_score:
                    best_corr_score = score
                    best_corr = (c_mins, n_l, c_loss, fp, fired_n, delta, new_tot)

                print(f"{c_mins:>6} {n_l:>6} {c_loss:>6.2f} {fp:>4} {fired_n:>6} "
                      f"{delta:>+8.2f} ${new_tot:>9.2f}  {verdict}")

    if best_corr:
        bcm, bcn, bcl, bcfp, bcf, bcd, bcnt = best_corr
        print(f"\n★ Best Correlation: mins={bcm}, n_losers={bcn}, loss={bcl}% | "
              f"FP={bcfp} | Δ={bcd:+.2f} | May total=${bcnt:.2f}")

    # ── OPTIMAL COMBO run ────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("OPTIMAL COMBO — best params from each sweep, applied together")
    print("=" * 70)

    # Use best Gap2 params
    g2_mins, g2_loss = bm, bl
    # Use best Protection params
    if best_prot:
        p_gap, p_peak = bgp, bmp
    else:
        p_gap, p_peak = 0.35, 120
    # Use best Correlation params
    if best_corr:
        c_mins_b, c_n_b, c_loss_b = bcm, bcn, bcl
    else:
        c_mins_b, c_n_b, c_loss_b = 120, 2, 0.5

    # Run Gap2 with best params
    gap2_res = {}
    for _, tr in trades.iterrows():
        key  = f"{tr['entry_date']}_{tr['symbol']}"
        bars = cache.get(key)
        new_pnl, fired, exit_t = sim_gap2(tr, bars, g2_mins, g2_loss)
        gap2_res[tr['id']] = {'new_pnl': new_pnl, 'fired': fired,
                               'actual_pnl': float(tr['actual_pnl']),
                               'symbol': tr['symbol']}

    # Run Protection
    prot_res = {}
    for date_str, day_grp in trades.groupby('entry_date'):
        day_intraday = {sym: cache.get(f"{date_str}_{sym}")
                        for sym in day_grp['symbol'].unique()}
        dr = sim_day_protection_pct(date_str, day_grp, day_intraday,
                                     gap_pct=p_gap, min_peak=p_peak)
        prot_res.update(dr)

    # Run Correlation
    corr_res = {}
    for date_str, day_grp in trades.groupby('entry_date'):
        day_intraday = {sym: cache.get(f"{date_str}_{sym}")
                        for sym in day_grp['symbol'].unique()}
        dr = sim_day_correlation(date_str, day_grp, day_intraday,
                                  corr_mins=c_mins_b, n_losers=c_n_b,
                                  loss_pct=c_loss_b, no_cut_symbols=NO_CUT)
        corr_res.update(dr)

    # Combo: for each trade, take the most profitable exit from any option
    # In production these fire sequentially (first trigger wins), so combo upper bound
    # Also show realistic combo (first-to-fire = Gap2 priority, then Protection)
    combo_pnl = 0.0
    realistic_pnl = 0.0
    fp_combo = 0
    print(f"\n{'Date':<12}{'Actual':>8}{'Gap2':>8}{'Prot':>8}{'Corr':>8}{'Combo':>8}{'Real':>8}  note")
    print("-" * 75)

    for date_str, day_grp in trades.groupby('entry_date'):
        day_act    = day_grp['actual_pnl'].sum()
        day_g2     = sum(gap2_res.get(tid, {}).get('new_pnl', pnl)
                         for tid, pnl in zip(day_grp['id'], day_grp['actual_pnl']))
        day_prot   = sum(float(prot_res.get(tid, {}).get('new_pnl', pnl))
                         for tid, pnl in zip(day_grp['id'], day_grp['actual_pnl']))
        day_corr   = sum(float(corr_res.get(tid, {}).get('new_pnl', pnl))
                         for tid, pnl in zip(day_grp['id'], day_grp['actual_pnl']))

        # Best (oracle) combo per trade
        day_combo  = 0.0
        day_real   = 0.0
        for tid, pnl in zip(day_grp['id'], day_grp['actual_pnl']):
            g2v   = gap2_res.get(tid, {}).get('new_pnl', pnl)
            pv    = float(prot_res.get(tid, {}).get('new_pnl', pnl))
            cv    = float(corr_res.get(tid, {}).get('new_pnl', pnl))
            day_combo += max(g2v, pv, cv)
            # Realistic: Gap2 fires first (individual trade), then Protection (portfolio)
            g2_fired = gap2_res.get(tid, {}).get('fired', False)
            p_fired  = prot_res.get(tid, {}).get('fired', False)
            if g2_fired:
                day_real += g2v
            elif p_fired:
                day_real += pv
            else:
                day_real += pnl

        combo_pnl    += day_combo
        realistic_pnl += day_real

        note = '← mixed momentum' if abs(day_act) < abs(day_combo) * 0.6 else ''
        print(f"{date_str:<12}{day_act:>8.2f}{day_g2:>8.2f}{day_prot:>8.2f}"
              f"{day_corr:>8.2f}{day_combo:>8.2f}{day_real:>8.2f}  {note}")

    print("-" * 75)
    print(f"{'MAY TOTAL':<12}{actual_total:>8.2f}"
          f"{sum(gap2_res[tid]['new_pnl'] for tid in gap2_res):>8.2f}"
          f"{sum(float(v['new_pnl']) for v in prot_res.values()):>8.2f}"
          f"{sum(float(v['new_pnl']) for v in corr_res.values()):>8.2f}"
          f"{combo_pnl:>8.2f}{realistic_pnl:>8.2f}")

    print(f"\n{'Config':<35} {'May P&L':>10} {'vs Actual':>10}")
    print("-" * 57)
    print(f"{'Actual (baseline):':<35} ${actual_total:>9.2f}")
    g2t = sum(gap2_res[tid]['new_pnl'] for tid in gap2_res)
    pt  = sum(float(v['new_pnl']) for v in prot_res.values())
    ct  = sum(float(v['new_pnl']) for v in corr_res.values())
    print(f"{f'Gap2 (mins={g2_mins}, loss={g2_loss}%):':<35} ${g2t:>9.2f}  ({g2t-actual_total:>+.2f})")
    print(f"{f'Protection ({p_gap:.0%} of peak):':<35} ${pt:>9.2f}  ({pt-actual_total:>+.2f})")
    print(f"{f'Correlation (mins={c_mins_b}, n={c_n_b}, {c_loss_b}%):':<35} ${ct:>9.2f}  ({ct-actual_total:>+.2f})")
    print(f"{'Combo — oracle (best per trade):':<35} ${combo_pnl:>9.2f}  ({combo_pnl-actual_total:>+.2f})")
    print(f"{'Combo — realistic (G2 first):':<35} ${realistic_pnl:>9.2f}  ({realistic_pnl-actual_total:>+.2f})")

    # ── Annualized extrapolation ────────────────────────────────────────────────
    trading_days_may = trades['entry_date'].nunique()
    print(f"\n{'─'*57}")
    print(f"Based on {trading_days_may} May trading days:")
    improvements = {
        'Gap2': g2t - actual_total,
        'Protection': pt - actual_total,
        'Combo-realistic': realistic_pnl - actual_total,
    }
    for name, delta in improvements.items():
        annualized = delta / trading_days_may * 252
        print(f"  {name:<25} May: {delta:>+.2f}  →  annualized: ${annualized:>+,.0f}/yr")

    print("\n✓ Calibration sweep complete")

if __name__ == '__main__':
    main()
