"""
VELOCITY GATE BACKTEST
======================
Tests whether a momentum velocity filter at entry time adds edge.

Hypothesis (from May 8 2026 session):
  Current system enters all A+ setups at 10:00 together.
  A velocity check at the moment of entry could:
    1. Block "slow" entries (coasting near HOD, volume fading) → fewer bad trades
    2. Allow early entries at 9:45-9:59 for genuinely fast movers → better fill price

Velocity = NOT SLOW.  A stock is SLOW if ANY condition fails:
  - Current 5m bar is red (close < open)
  - Current bar close < prior bar close  (losing momentum)
  - Current bar volume < 80% of prior bar (fading interest)
  - Price within 0.3% of VWAP           (coasting, no direction)
  - Stock up < 0.5% from session open   (barely moved)

Output:
  - Velocity-eligible vs velocity-blocked entry comparison (win rate, avg P&L)
  - Early window (9:45-9:59) velocity-eligible entries vs 10:00 baseline
  - Per-condition breakdown: which slow condition is most predictive
  - Top 10 symbols with best velocity filter lift

Usage: venv/bin/python backtest_velocity.py
       venv/bin/python backtest_velocity.py --days 30
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
import pytz
import sys
import os
import time

ET = pytz.timezone('America/New_York')

# ── Config ────────────────────────────────────────────────────────────────────
LOOKBACK_DAYS   = 59      # yfinance 5-min limit
ENTRY_WINDOW    = (10, 0) # current system entry gate
EARLY_WINDOW    = (9, 45) # proposed early velocity entry
MEASURE_MINS    = [30, 60, 120]  # outcomes measured at these minutes after entry
MIN_TODAY_GAIN  = 1.0     # minimum % gain from open to even consider (lower than live to capture more data)
STOP_PCT        = 0.05    # 5% stop

UNIVERSE = [
    'NVDA','AMD','PLTR','SMCI','MARA','COIN','TSLA','META','GOOGL','MSFT',
    'AAPL','AMZN','APP','AXON','ARM','DDOG','CRWD','SHOP','MSTR','IONQ',
    'HOOD','SOFI','UPST','RKLB','JOBY','OKLO','SMR','QBTS','RGTI','SOUN',
    'ORCL','CRM','NOW','SNOW','NET','PANW','FTNT','ZS','ANET','MRVL',
    'LRCX','KLAC','AMAT','QCOM','INTC','MU','ON','WOLF','MPWR','ENPH',
    'CCJ','UEC','NNE','SMH','XLK','EOSE','IREN','GTLB','BILL','MNDY',
    'CELH','HIMS','PENN','DKNG','LLY','ABBV','MRNA','RXRX','NVAX','CRSP',
    'XOM','CVX','SLB','OXY','FANG','MPC','VLO','NEM','GDX','GDXJ',
    'JPM','BAC','GS','MS','SCHW','COIN','MA','V','PYPL','AFRM',
    'NFLX','DIS','PARA','SPOT','RBLX','U','EA','TTWO','MTCH','BMBL',
]
UNIVERSE = list(dict.fromkeys(UNIVERSE))  # dedup, preserve order

# ── Velocity computation ───────────────────────────────────────────────────────
def compute_velocity(df5_bars):
    if len(df5_bars) < 2:
        return None
    curr = df5_bars.iloc[-1]
    prev = df5_bars.iloc[-2]
    curr_close = float(curr['Close'])
    curr_open  = float(curr['Open'])
    curr_vol   = float(curr['Volume'])
    prev_close_bar = float(prev['Close'])
    prev_vol   = float(prev['Volume']) if float(prev['Volume']) > 0 else 1

    tp   = (df5_bars['High'] + df5_bars['Low'] + df5_bars['Close']) / 3
    vwap = float((tp * df5_bars['Volume']).cumsum().iloc[-1] /
                  df5_bars['Volume'].cumsum().iloc[-1])
    session_open    = float(df5_bars['Open'].iloc[0])
    move_from_open  = (curr_close - session_open) / session_open * 100
    vwap_dist_pct   = (curr_close - vwap) / vwap * 100 if vwap else 0

    v_green    = curr_close > curr_open
    v_momentum = curr_close > prev_close_bar
    v_volume   = curr_vol >= prev_vol * 0.80
    v_vwap     = abs(vwap_dist_pct) >= 0.30
    v_moved    = move_from_open >= 0.50

    conditions = {'v_green': v_green, 'v_momentum': v_momentum,
                  'v_volume': v_volume, 'v_vwap': v_vwap, 'v_moved': v_moved}
    slow_reasons = [k for k, v in conditions.items() if not v]

    return {
        'eligible': len(slow_reasons) == 0,
        'slow_reasons': slow_reasons,
        'move_from_open': round(move_from_open, 2),
        'vwap_dist': round(vwap_dist_pct, 2),
        **conditions,
    }


# ── Measure outcome N minutes after entry bar ─────────────────────────────────
def measure_outcome(rth_bars, entry_ts, entry_price, measure_mins):
    results = {}
    for mins in measure_mins:
        target_ts = entry_ts + pd.Timedelta(minutes=mins)
        future = rth_bars[rth_bars.index > entry_ts]
        future_window = future[future.index <= target_ts]
        if future_window.empty:
            results[f'pct_{mins}m'] = None
            results[f'stopped_{mins}m'] = None
            continue
        # Did stop fire?
        stop_price = entry_price * (1 - STOP_PCT)
        stopped = any(float(b['Low']) <= stop_price for _, b in future_window.iterrows())
        if stopped:
            results[f'pct_{mins}m'] = round(-STOP_PCT * 100, 2)
            results[f'stopped_{mins}m'] = True
        else:
            exit_price = float(future_window['Close'].iloc[-1])
            results[f'pct_{mins}m'] = round((exit_price - entry_price) / entry_price * 100, 2)
            results[f'stopped_{mins}m'] = False
    return results


# ── Process one symbol ─────────────────────────────────────────────────────────
def analyse_symbol(sym, all_bars_5m):
    """
    For each trading day, find the 9:45 and 10:00 bar and compute:
    - velocity check at that bar
    - outcome at 30/60/120 min
    Returns list of row dicts.
    """
    rows = []
    trading_days = sorted(set(all_bars_5m.index.date))

    for day in trading_days:
        day_bars = all_bars_5m[all_bars_5m.index.date == day]
        rth = day_bars.between_time('09:30', '16:00')
        if len(rth) < 6:
            continue

        prev_day_bars = all_bars_5m[all_bars_5m.index.date < day].between_time('09:30', '16:00')
        if prev_day_bars.empty:
            continue
        prev_close = float(prev_day_bars['Close'].iloc[-1])
        session_open = float(rth['Open'].iloc[0])

        # Check candidate entry bars: 9:45, 9:50, 9:55, 10:00, 10:05, 10:10
        candidate_times = [(9,45),(9,50),(9,55),(10,0),(10,5),(10,10)]

        for hr, mn in candidate_times:
            # find the bar at or just after this time
            bar_ts_target = pd.Timestamp(datetime(day.year, day.month, day.day, hr, mn),
                                         tz=ET)
            bars_up_to = rth[rth.index <= bar_ts_target]
            if len(bars_up_to) < 2:
                continue

            entry_ts    = bars_up_to.index[-1]
            entry_bar   = bars_up_to.iloc[-1]
            entry_price = float(entry_bar['Close'])

            # Basic filter: must have moved from open
            move_pct = (entry_price - session_open) / session_open * 100
            if move_pct < MIN_TODAY_GAIN:
                continue

            vel = compute_velocity(bars_up_to)
            if vel is None:
                continue

            outcomes = measure_outcome(rth, entry_ts, entry_price, MEASURE_MINS)

            is_current_gate = (hr == ENTRY_WINDOW[0] and mn == ENTRY_WINDOW[1])
            is_early_gate   = (hr == EARLY_WINDOW[0] and mn == EARLY_WINDOW[1])
            time_cat = 'EARLY' if (hr, mn) < ENTRY_WINDOW else 'CURRENT' if is_current_gate else 'LATER'

            rows.append({
                'symbol': sym, 'date': day.isoformat(),
                'bar_time': f'{hr:02d}:{mn:02d}',
                'time_cat': time_cat,
                'is_current_gate': is_current_gate,
                'is_early_gate': is_early_gate,
                'entry_price': round(entry_price, 2),
                'move_from_open': vel['move_from_open'],
                'vwap_dist': vel['vwap_dist'],
                'velocity_eligible': vel['eligible'],
                'slow_reasons': '|'.join(vel['slow_reasons']),
                'v_green': vel['v_green'], 'v_momentum': vel['v_momentum'],
                'v_volume': vel['v_volume'], 'v_vwap': vel['v_vwap'],
                'v_moved': vel['v_moved'],
                **outcomes,
            })

    return rows


# ── Summary stats ─────────────────────────────────────────────────────────────
def summarise(rows, label, metric='pct_30m'):
    valid = [r for r in rows if r.get(metric) is not None]
    if not valid:
        return f'  {label}: no data'
    wins   = [r for r in valid if r[metric] > 0]
    losses = [r for r in valid if r[metric] <= 0]
    wr     = len(wins) / len(valid) * 100
    avg    = np.mean([r[metric] for r in valid])
    avg_w  = np.mean([r[metric] for r in wins])  if wins   else 0
    avg_l  = np.mean([r[metric] for r in losses]) if losses else 0
    return (f'  {label:<40} n={len(valid):>4}  WR={wr:>5.1f}%  '
            f'avg={avg:>+6.2f}%  W:{avg_w:>+5.2f}% / L:{avg_l:>+5.2f}%')


# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    print('='*72)
    print('  VELOCITY GATE BACKTEST')
    print(f'  Universe: {len(UNIVERSE)} symbols | Lookback: {LOOKBACK_DAYS} days')
    print(f'  Entry gate: {ENTRY_WINDOW[0]:02d}:{ENTRY_WINDOW[1]:02d} (current) | '
          f'Early gate: {EARLY_WINDOW[0]:02d}:{EARLY_WINDOW[1]:02d} (proposed)')
    print('='*72)

    all_rows = []
    failed   = []

    for i, sym in enumerate(UNIVERSE):
        print(f'  [{i+1:>3}/{len(UNIVERSE)}] {sym:<8}', end='', flush=True)
        try:
            raw = yf.Ticker(sym).history(period=f'{LOOKBACK_DAYS}d', interval='5m')
            if raw.empty or len(raw) < 50:
                print(' — no data')
                failed.append(sym)
                continue
            raw.index = raw.index.tz_convert(ET)
            rows = analyse_symbol(sym, raw)
            all_rows.extend(rows)
            print(f' → {len(rows)} bars')
        except Exception as e:
            print(f' — error: {e}')
            failed.append(sym)
        time.sleep(0.2)

    if not all_rows:
        print('No data collected.')
        return

    # ── Save raw data ──────────────────────────────────────────────────────────
    log_dir = os.path.join(os.path.dirname(__file__), 'velocity_logs')
    os.makedirs(log_dir, exist_ok=True)
    raw_path = os.path.join(log_dir, 'velocity_backtest_raw.csv')
    pd.DataFrame(all_rows).to_csv(raw_path, index=False)
    print(f'\n  Raw data saved → {raw_path}  ({len(all_rows)} rows)\n')

    # ── Analysis ──────────────────────────────────────────────────────────────
    current_gate = [r for r in all_rows if r['is_current_gate']]
    vel_yes  = [r for r in current_gate if r['velocity_eligible']]
    vel_no   = [r for r in current_gate if not r['velocity_eligible']]
    early    = [r for r in all_rows if r['is_early_gate'] and r['velocity_eligible']]

    print('='*72)
    print('  RESULT 1: Current 10:00 gate — velocity eligible vs blocked')
    print('='*72)
    for m in ['pct_30m','pct_60m','pct_120m']:
        label = m.replace('pct_','').replace('m',' min outcome')
        print(f'\n  {label}:')
        print(summarise(vel_yes, 'Velocity ELIGIBLE (not slow)', m))
        print(summarise(vel_no,  'Velocity BLOCKED  (slow)',     m))
        if vel_yes and vel_no:
            yes_avg = np.mean([r[m] for r in vel_yes if r.get(m) is not None])
            no_avg  = np.mean([r[m] for r in vel_no  if r.get(m) is not None])
            lift = yes_avg - no_avg
            print(f'  {"Edge from velocity filter":<40} lift={lift:>+6.2f}% per entry')

    print('\n' + '='*72)
    print('  RESULT 2: Early 9:45 velocity-eligible vs current 10:00 baseline')
    print('='*72)
    for m in ['pct_30m','pct_60m','pct_120m']:
        label = m.replace('pct_','').replace('m',' min outcome')
        print(f'\n  {label}:')
        print(summarise(early,        '9:45 velocity-eligible (early)', m))
        print(summarise(current_gate, '10:00 all entries (current)',    m))

    print('\n' + '='*72)
    print('  RESULT 3: Which slow condition is most predictive of bad entries?')
    print('='*72)
    conditions = ['v_green','v_momentum','v_volume','v_vwap','v_moved']
    m = 'pct_30m'
    print(f'\n  At 10:00 gate, 30-min outcome by each failed condition:')
    for cond in conditions:
        failed_cond = [r for r in current_gate if not r.get(cond)]
        passed_cond = [r for r in current_gate if r.get(cond)]
        if not failed_cond: continue
        f_avg = np.mean([r[m] for r in failed_cond if r.get(m) is not None])
        p_avg = np.mean([r[m] for r in passed_cond if r.get(m) is not None])
        print(f'  {cond:<15} fail_avg={f_avg:>+5.2f}%  pass_avg={p_avg:>+5.2f}%  '
              f'delta={f_avg-p_avg:>+5.2f}%  n_fail={len(failed_cond)}')

    print('\n' + '='*72)
    print('  RESULT 4: Per-symbol velocity filter lift (top 10)')
    print('='*72)
    sym_lifts = []
    for sym in set(r['symbol'] for r in all_rows):
        sym_cur  = [r for r in current_gate if r['symbol'] == sym]
        sym_yes  = [r for r in sym_cur if r['velocity_eligible']]
        sym_no   = [r for r in sym_cur if not r['velocity_eligible']]
        if len(sym_yes) < 3 or len(sym_no) < 3: continue
        yes_avg = np.mean([r['pct_30m'] for r in sym_yes if r.get('pct_30m') is not None])
        no_avg  = np.mean([r['pct_30m'] for r in sym_no  if r.get('pct_30m') is not None])
        sym_lifts.append((sym, yes_avg - no_avg, len(sym_yes), len(sym_no)))
    sym_lifts.sort(key=lambda x: -x[1])
    print(f'\n  {"Symbol":<8} {"Lift":>8}  {"#eligible":>10}  {"#blocked":>8}')
    for sym, lift, n_yes, n_no in sym_lifts[:10]:
        print(f'  {sym:<8} {lift:>+7.2f}%  {n_yes:>10}  {n_no:>8}')

    print('\n' + '='*72)
    print('  VERDICT')
    print('='*72)
    if vel_yes and vel_no:
        yes_avg30 = np.mean([r['pct_30m'] for r in vel_yes if r.get('pct_30m') is not None])
        no_avg30  = np.mean([r['pct_30m'] for r in vel_no  if r.get('pct_30m') is not None])
        lift30 = yes_avg30 - no_avg30
        if lift30 >= 0.30:
            verdict = f'✅ VELOCITY GATE ADDS EDGE (+{lift30:.2f}% lift) — BUILD IT'
        elif lift30 >= 0.10:
            verdict = f'⚠️  MARGINAL EDGE (+{lift30:.2f}%) — collect more data'
        else:
            verdict = f'❌ NO MEANINGFUL EDGE ({lift30:+.2f}%) — current system fine'
        print(f'\n  {verdict}')
        if early:
            e_avg = np.mean([r['pct_30m'] for r in early if r.get('pct_30m') is not None])
            c_avg = np.mean([r['pct_30m'] for r in current_gate if r.get('pct_30m') is not None])
            print(f'  Early 9:45 velocity entries avg: {e_avg:+.2f}% vs 10:00 avg: {c_avg:+.2f}%')
            if e_avg > c_avg + 0.20:
                print(f'  ✅ EARLY ENTRY ADDS EDGE — 9:45 velocity gate worth testing')
    print('='*72)
    if failed:
        print(f'\n  Failed symbols ({len(failed)}): {", ".join(failed)}')


if __name__ == '__main__':
    if '--days' in sys.argv:
        idx = sys.argv.index('--days')
        LOOKBACK_DAYS = int(sys.argv[idx + 1])
    run()
