#!/usr/bin/env python3
"""
futures/oracle_today.py — The Oracle: read today's cards

Shows everything the system "sees" for a given date:
  1. Overnight positioning (OVN_POS) — where did we come from?
  2. IB range and regime — what kind of day is it?
  3. Hero scores at each entry window — which Avengers are activated?
  4. Trades the system took (or would take) — the treasure map
  5. Actual P&L outcome (for completed bars)

This is the test: do the signs lead to the treasure?
"""
import sys, os, sqlite3
import datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import futures.sim_replay as sr
from futures.collect_bars import load_bars, filter_ny_session
from futures.hero_score import (score_entry, detect_regime,
                                 score_entry_regime, contracts_from_regime_score,
                                 QUIET_IB_THRESH, TRENDING_IB_THRESH)
from futures.feature_study import get_prior_rth
from futures.rotational_sim import get_premarket, classify_ib

NY = 'America/New_York'


def compute_ovn_pos(all_bars, trade_date):
    """OVN_POS = (rth_open - ovn_low) / ovn_range  [same as compute_overnight_bias_5m]"""
    prev_date = trade_date - dt.timedelta(days=1)
    while prev_date.weekday() >= 5:
        prev_date -= dt.timedelta(days=1)

    # Overnight = prev RTH close (4pm ET) → today RTH open (9:30am ET)
    ovn_start = pd.Timestamp(prev_date).tz_localize(NY).replace(hour=16, minute=0)
    ovn_end   = pd.Timestamp(trade_date).tz_localize(NY).replace(hour=9,  minute=30)

    ovn = all_bars[(all_bars.index > ovn_start) & (all_bars.index <= ovn_end)]
    if ovn.empty:
        return None, None, None

    ovn_lo    = float(ovn['low'].min())
    ovn_hi    = float(ovn['high'].max())
    ovn_range = ovn_hi - ovn_lo

    # RTH open = first 9:30am bar
    rth_bars = filter_ny_session(all_bars[all_bars.index.date == trade_date])
    if rth_bars.empty:
        return None, None, None
    rth_open = float(rth_bars['open'].iloc[0])

    if ovn_range < 1:
        return 0.5, ovn_range, rth_open

    ovn_pos = (rth_open - ovn_lo) / ovn_range
    return round(ovn_pos, 3), round(ovn_range, 1), rth_open


def ib_snapshots(rth_today):
    """IB range at 9:45, 10:00, 10:15, 10:30."""
    snaps = {}
    for hh, mm in [(9,45),(10,0),(10,15),(10,30)]:
        snap_t = pd.Timestamp(f'2000-01-01 {hh:02d}:{mm:02d}:00').time()
        window = rth_today[rth_today.index.time <= snap_t]
        if len(window) >= 2:
            snaps[f'{hh}:{mm:02d}'] = round(float(window['high'].max() - window['low'].min()), 1)
        else:
            snaps[f'{hh}:{mm:02d}'] = None
    return snaps


def hero_reading(all_bars, trade_date, scan_times, prior_rth, regime):
    """Compute hero scores at multiple scan times within a day."""
    rth = filter_ny_session(all_bars[all_bars.index.date == trade_date])
    results = []
    for hh, mm in scan_times:
        snap_ts = pd.Timestamp(trade_date).tz_localize(NY).replace(hour=hh, minute=mm)
        bars_up = all_bars[all_bars.index <= snap_ts]
        if bars_up.empty:
            continue
        bar = rth[rth.index.time <= pd.Timestamp(f'2000-01-01 {hh:02d}:{mm:02d}').time()]
        if bar.empty:
            continue
        price = float(bar['close'].iloc[-1])
        # Compute ATR from last 100 bars
        hist = bars_up.iloc[-100:]
        atr = sr.compute_atr(hist)

        for side in ('LONG', 'SHORT'):
            wscore, flags = score_entry_regime(price, atr, side, bars_up, prior_rth, regime)
            sl, tgt = sr.calc_sl_target(price, atr, side)
            rr = abs(tgt - price) / abs(price - sl) if abs(price - sl) > 0 else 0
            stop_pts = abs(price - sl)
            results.append({
                'time': f'{hh}:{mm:02d}',
                'side': side,
                'price': price,
                'atr': round(atr, 1),
                'regime': regime,
                'wscore': wscore,
                'H1': flags.get('H1_ATR_SAFE', False),
                'H2': flags.get('H2_MTF_ALIGNED', False),
                'H3': flags.get('H3_RSI_MOMENTUM', False),
                'H4': flags.get('H4_POC_BREAKOUT', False),
                'rr': round(rr, 2),
                'stop_pts': round(stop_pts, 1),
                'contracts': contracts_from_regime_score(wscore, regime,
                                sr.calc_contracts(price, sl)),
            })
    return results


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=dt.date.today().isoformat(),
                    help='Date to analyse (default: today)')
    args = ap.parse_args()

    trade_date = dt.date.fromisoformat(args.date)
    load_start = (trade_date - dt.timedelta(days=110)).isoformat()

    print(f"\n{'═'*68}")
    print(f"  THE ORACLE — {trade_date.strftime('%A %b %d, %Y')}")
    print(f"{'═'*68}\n")

    all_bars = load_bars('MNQ', start=load_start, end=(trade_date + dt.timedelta(days=1)).isoformat())
    rth_today = filter_ny_session(all_bars[all_bars.index.date == trade_date])

    if rth_today.empty:
        print("No RTH bars available for this date yet.")
        return

    print(f"  RTH bars available: {len(rth_today)}  "
          f"({rth_today.index[0].strftime('%H:%M')} → {rth_today.index[-1].strftime('%H:%M')} ET)\n")

    # ── 0. Pre-market read ────────────────────────────────────────────────────
    try:
        conn = sqlite3.connect('market_data.db')
        pm   = get_premarket(conn, trade_date)
        conn.close()
        print(f"{'─'*68}")
        print(f"  CARD 0 — PRE-MARKET  (7am–9:30 ET)")
        print(f"{'─'*68}")
        if pm['bias'] == 'UNKNOWN':
            print(f"  No pre-market bars available.")
        else:
            print(f"  PM range:   {pm['pm_lo']:.0f} – {pm['pm_hi']:.0f}  ({pm['pm_hi']-pm['pm_lo']:.0f}pts)")
            print(f"  RTH open:   {pm['open_pct']*100:.0f}% of PM range  → {pm['bias']}")
            slope_lbl = 'rising' if pm['slope'] > 3 else ('falling' if pm['slope'] < -3 else 'flat')
            print(f"  Last 30min: {pm['slope']:+.1f}pts/bar  ({slope_lbl})")
            if pm['bias'] == 'DIST_TOP':
                print(f"  ⚠  Opened at PM ceiling. Overnight bulls exhausted → SHORT lean for ROTATIONAL")
            elif pm['bias'] == 'DIST_BOT':
                print(f"  ⚠  Opened at PM floor.   Overnight bears exhausted → LONG lean for ROTATIONAL")
            else:
                print(f"  ✓  Opened in PM mid-range. Both directions open.")
        print()
    except Exception as e:
        print(f"  [Pre-market read failed: {e}]\n")

    # ── 1. Overnight positioning ──────────────────────────────────────────────
    ovn_pos, ovn_range, rth_open = compute_ovn_pos(all_bars, trade_date)
    print(f"{'─'*68}")
    print(f"  CARD 1 — OVERNIGHT POSITIONING")
    print(f"{'─'*68}")
    print(f"  OVN_POS:    {ovn_pos:.3f}  (0=overnight low, 1=overnight high)")
    print(f"  OVN range:  {ovn_range:.1f} pts")
    print(f"  RTH open:   {rth_open:.2f}")

    if ovn_pos is not None:
        if ovn_pos <= 0.13:
            bias = 'SHORT → BUT allow LONG (exhausted bears, OVN≤0.13 rule)'
            bias_code = 'BOTH_SPECIAL'
        elif ovn_pos <= 0.20:
            bias = 'SHORT (blocked LONGs — OVN 0.13-0.20 zone, marginal)'
            bias_code = 'SHORT'
        elif ovn_pos >= 0.85:
            bias = 'LONG bias (careful — high OVN hurts LONGs historically)'
            bias_code = 'LONG'
        else:
            bias = 'BOTH  (neutral — full flexibility)'
            bias_code = 'BOTH'
        print(f"  Bias read:  {bias}")

    # ── 2. IB range and regime ────────────────────────────────────────────────
    print(f"\n{'─'*68}")
    print(f"  CARD 2 — INITIAL BALANCE & DAY REGIME")
    print(f"{'─'*68}")
    snaps = ib_snapshots(rth_today)
    for label, val in snaps.items():
        if val is None:
            indicator = '(no data)'
        elif val >= TRENDING_IB_THRESH:
            indicator = f'→ TRENDING  ✓'
        elif val >= QUIET_IB_THRESH:
            indicator = f'→ CHOPPY'
        else:
            indicator = f'→ QUIET'
        val_str = f'{val:.1f} pts' if val else '—'
        print(f"  IB @ {label}:  {val_str:>8}  {indicator}")

    ib_final = snaps.get('10:30')
    regime = detect_regime(ib_final) if ib_final else 'CHOPPY'
    print(f"\n  REGIME:  {regime}  (IB={ib_final:.1f}pts)")
    if regime == 'TRENDING':
        print(f"  Day type: Big energy. Thor (MTF) + Iron Man (RSI) lead the way.")
        print(f"  Early entry possible: IB crossed 200pts at 10:00 → can enter from 10:00")
    elif regime == 'CHOPPY':
        print(f"  Day type: Structural. Captain America (ATR) + Dr Strange (POC) guide.")
    else:
        print(f"  Day type: Quiet. POC gravity rules. No MTF noise.")

    # IB mid-close classification
    ib_mid_pct, _, _, ib_kind = classify_ib(rth_today)
    print(f"\n  IB close pos: {ib_mid_pct:.2f}  → {ib_kind}")
    if ib_kind == 'ROTATIONAL':
        print(f"  ★ ROTATIONAL DAY — range-fade mode applies (not trend-follow)")
        print(f"    Current system (trend mode) historically bleeds on these days.")
        if 'pm' in dir() and pm['bias'] != 'UNKNOWN':
            print(f"    PM bias {pm['bias']} → only {('SHORT' if pm['bias']=='DIST_TOP' else 'LONG' if pm['bias']=='DIST_BOT' else 'BOTH')} entries in rotational mode")
    else:
        print(f"  ✓ DIRECTIONAL — trend-follow mode is right call")

    # ── 3. Hero scores at key windows ────────────────────────────────────────
    print(f"\n{'─'*68}")
    print(f"  CARD 3 — HERO SCORES AT KEY WINDOWS")
    print(f"{'─'*68}")
    prior_rth = get_prior_rth(all_bars, trade_date)

    # Only scan times that have RTH data
    last_bar_time = rth_today.index[-1].time()
    all_scan_times = [(10,0),(10,15),(10,30),(10,45),(11,0),(11,15),(11,30),(12,0),(12,30)]
    scan_times = [(h,m) for (h,m) in all_scan_times
                  if pd.Timestamp(f'2000-01-01 {h:02d}:{m:02d}').time() <= last_bar_time]

    readings = hero_reading(all_bars, trade_date, scan_times, prior_rth, regime)

    print(f"  {'Time':>6}  {'Side':>5}  {'Price':>8}  {'ATR':>5}  "
          f"{'H1':>3}{'H2':>3}{'H3':>3}{'H4':>3}  {'Score':>5}  {'RR':>4}  {'CT':>2}  VERDICT")
    print(f"  {'─'*6}  {'─'*5}  {'─'*8}  {'─'*5}  "
          f"{'─'*3}{'─'*3}{'─'*3}{'─'*3}  {'─'*5}  {'─'*4}  {'─'*2}  {'─'*12}")

    for r in readings:
        h1 = '✓' if r['H1'] else '·'
        h2 = '✓' if r['H2'] else '·'
        h3 = '✓' if r['H3'] else '·'
        h4 = '✓' if r['H4'] else '·'
        ct_str = str(r['contracts']) if r['contracts'] > 0 else 'SKIP'
        verdict = ''
        if r['contracts'] >= 2:
            verdict = '★ GOLD (2ct)'
        elif r['contracts'] == 1:
            verdict = '◆ SILVER (1ct)'
        else:
            verdict = '✗ skip'
        if r['rr'] < 2.0:
            verdict += ' [low RR]'
        if r['stop_pts'] > 150:
            verdict += ' [stop too wide]'
        print(f"  {r['time']:>6}  {r['side']:>5}  {r['price']:>8.2f}  {r['atr']:>5.1f}  "
              f"{h1:>3}{h2:>3}{h3:>3}{h4:>3}  {r['wscore']:>5}  {r['rr']:>4.1f}  {ct_str:>4}  {verdict}")

    # ── 4. Sim trades for today ───────────────────────────────────────────────
    print(f"\n{'─'*68}")
    print(f"  CARD 4 — TRADES THE SYSTEM TOOK TODAY")
    print(f"{'─'*68}")
    trades, daily_bias_str, ovn_val = sr.simulate_day(all_bars, trade_date)

    if not trades:
        print("  No trades triggered yet for today.")
    else:
        total_pnl = sum(t['pnl'] for t in trades)
        wins = sum(1 for t in trades if t['pnl'] > 0)
        print(f"  {len(trades)} trade(s)  |  {wins} wins  |  total P&L ${total_pnl:+.2f}\n")
        for t in trades:
            outcome = 'WIN  ✓' if t['pnl'] > 0 else 'LOSS ✗'
            in_prog = ' [in progress]' if t.get('exit_reason') == 'eod_force' else ''
            print(f"  {t['entry_time']}  {t['side']:>5}  {t['setup']:<16}  {t['grade']:>2}  "
                  f"entry={t['entry']:.2f}  exit={t['exit']:.2f}  "
                  f"P&L=${t['pnl']:>+8.2f}  {t['exit_reason']:<12}  {outcome}{in_prog}")

    # ── 5. Where are we now ───────────────────────────────────────────────────
    print(f"\n{'─'*68}")
    print(f"  CARD 5 — CURRENT PRICE ACTION ({rth_today.index[-1].strftime('%H:%M ET')})")
    print(f"{'─'*68}")
    last = rth_today.iloc[-1]
    day_hi = rth_today['high'].max()
    day_lo = rth_today['low'].min()
    day_range = day_hi - day_lo

    prev_rth = filter_ny_session(all_bars[all_bars.index.date == (trade_date - dt.timedelta(days=1))])
    prev_close = float(prev_rth['close'].iloc[-1]) if not prev_rth.empty else None

    print(f"  Last price:  {float(last['close']):.2f}")
    print(f"  Day range:   {day_lo:.2f} – {day_hi:.2f}  ({day_range:.1f} pts)")
    if prev_close:
        chg = (float(last['close']) - prev_close) / prev_close * 100
        print(f"  vs prev close ({prev_close:.2f}): {chg:+.2f}%")

    # Range consumed
    if day_range > 0 and rth_open:
        from_open = float(last['close']) - rth_open
        print(f"  From open:   {from_open:+.1f} pts  ({from_open/day_range*100:+.0f}% of day range)")

    print(f"\n{'═'*68}")
    print(f"  SUMMARY")
    print(f"{'═'*68}")
    print(f"  Regime:   {regime}  (IB {ib_final:.0f}pts)")
    print(f"  OVN bias: OVN_POS={ovn_pos} → {bias_code}")
    best = [r for r in readings if r['contracts'] > 0]
    if best:
        print(f"  Best setups: {len(best)} signal(s) fired across scan windows")
        gold = [r for r in best if r['contracts'] >= 2]
        if gold:
            print(f"  ★ GOLD signals: {len(gold)}")
            for g in gold:
                print(f"    {g['time']}  {g['side']}  score={g['wscore']}  "
                      f"H1={int(g['H1'])} H2={int(g['H2'])} H3={int(g['H3'])} H4={int(g['H4'])}")
    else:
        print(f"  No signals fired yet in scan windows so far.")
    print()


if __name__ == '__main__':
    main()
