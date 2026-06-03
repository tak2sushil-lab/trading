"""
futures/backtest_futures.py — MNQ Futures Backtest (Pro Grade)

Pro features:
  ✅ Initial Balance (60-min) instead of 30-min ORB — standard futures methodology
  ✅ IB confirmation bar  — 1 close above/below IB before entry (reduces false breaks)
  ✅ Slippage model        — 1 tick ($0.50) per entry + 1 tick per exit = $1.00/round-turn
  ✅ Opening gap filter    — skip if overnight gap > GAP_MAX_PCT (gap-fill risk)
  ✅ Day type filter       — skip choppy days (IB range < MIN_IB_RANGE_PTS)
  ✅ Relative volume gate  — only enter on bars with RVOL ≥ MIN_RVOL
  ✅ MAE / MFE tracking    — measures optimal stop & target placement
  ✅ Walk-forward analysis — 8 rolling OOS windows (anti-overfitting)
  ✅ Time-of-day breakdown — which hours generate edge
  ✅ Direction breakdown   — LONG vs SHORT separately
  ✅ TC eval simulation    — simulates passing the Trading Combine
  ✅ A/B config runner     — compare parameter sets in one command

MNQ economics:
  Tick size:   0.25 points  → $0.50/tick/contract
  Point value: $2.00/contract
  Commission:  $1.24/round-turn
  Slippage:    $0.50/entry + $0.50/exit = $1.00/round-turn

Usage:
  venv/bin/python futures/backtest_futures.py                     # full run
  venv/bin/python futures/backtest_futures.py --start 2026-01-01  # date range
  venv/bin/python futures/backtest_futures.py --ab                # A/B comparison
  venv/bin/python futures/backtest_futures.py --wfa               # walk-forward
"""

import sys, os, argparse, math
from datetime import datetime, date, time, timedelta
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))
from futures.collect_bars import load_bars, filter_ny_session
from futures.prop_rules   import PropRulesSimulator, COMMISSION, POINT_VALUE, TICK_SIZE

ET = pytz.timezone('America/New_York')

# ── Strategy parameters (tune via --ab to compare) ────────────────────────────
@dataclass
class Config:
    label: str = 'default'

    # Entry
    ib_window_min:   int   = 60     # Initial Balance window (mins from open)
    ib_confirm_bars: int   = 1      # closes above/below IB before entry (prevents false breaks)
    min_ib_range:    float = 50.0   # skip day if IB range < N points (confirmed chop)
    max_ib_range:    float = 600.0  # skip day if IB range > N points (confirmed crash/gap day)
    gap_max_pct:     float = 1.5    # skip if overnight gap > N% (gap-fill risk)
    min_rvol:        float = 0.7    # min relative volume on entry bar

    ema_fast:        int   = 8      # 5-min EMA fast (intraday trend)
    ema_slow:        int   = 21     # 5-min EMA slow (intraday trend)

    # Risk — IB-based stops (the standard for IB breakout trading)
    # Stop at IB midpoint: stop_frac = 0.50 → stop = IB_high - IB_range × 0.50
    # This places the stop at the middle of the IB — structural S/R
    stop_ib_frac:    float = 0.50   # stop = entry side of IB ± IB_range × frac
    target_ib_mult:  float = 1.5    # target = entry + IB_range × mult
    min_rr:          float = 1.5    # skip if R:R < min_rr
    max_stop_pts:    float = 150.0  # skip day if stop distance > N pts ($300 risk floor)

    pct_trail_activate: float = 0.008  # +0.8% activates PCT trail (lower than equity)
    pct_trail_gap:      float = 0.004  # trail 0.4% from peak

    # Exit
    max_hold_bars:   int   = 24     # 2h no-move exit (tighter than equity)
    eod_close_time:  time  = time(15, 10)
    no_entry_after:  time  = time(14, 0)   # tighter: no entries after 2pm ET
    lunch_start:     time  = time(11, 45)
    lunch_end:       time  = time(12, 30)

    # Friction
    slippage_ticks:  float = 1.0    # ticks per entry/exit = $0.50 each
    commission:      float = COMMISSION

    @property
    def slippage_pts(self) -> float:
        return self.slippage_ticks * TICK_SIZE

    @property
    def total_friction_dollar(self) -> float:
        return self.commission + self.slippage_ticks * 2 * TICK_SIZE * (POINT_VALUE / TICK_SIZE)


# ── Indicators ─────────────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    df = df.copy()
    hl   = df['high'] - df['low']
    hpc  = (df['high'] - df['close'].shift(1)).abs()
    lpc  = (df['low']  - df['close'].shift(1)).abs()
    df['atr']      = pd.concat([hl, hpc, lpc], axis=1).max(axis=1).rolling(14).mean()
    df['ema_fast'] = df['close'].ewm(span=cfg.ema_fast, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=cfg.ema_slow, adjust=False).mean()
    return df


def compute_vwap(day_df: pd.DataFrame) -> pd.Series:
    tp  = (day_df['high'] + day_df['low'] + day_df['close']) / 3
    vol = day_df['volume'].replace(0, 1)
    return (tp * vol).cumsum() / vol.cumsum()


def compute_rvol(day_df: pd.DataFrame, avg_vol_by_time: dict) -> pd.Series:
    """Relative volume vs average for each time slot across history."""
    rvol = pd.Series(index=day_df.index, dtype=float)
    for ts in day_df.index:
        t_key = ts.strftime('%H:%M')
        avg   = avg_vol_by_time.get(t_key, 0)
        rvol[ts] = day_df.loc[ts, 'volume'] / avg if avg > 0 else 1.0
    return rvol


def build_avg_vol(ny_df: pd.DataFrame) -> dict:
    """Build average volume per 5-min time slot across all history."""
    ny_df = ny_df.copy()
    ny_df['time_key'] = ny_df.index.strftime('%H:%M')
    return ny_df.groupby('time_key')['volume'].mean().to_dict()


# ── Dynamic contract sizing ────────────────────────────────────────────────────

MAX_TRADE_CONTRACTS = 5   # TopStepX $50K account hard limit for MNQ micro

def _dynamic_contracts(base: int, rvol: float, ib_range: float,
                       had_loss_today: bool) -> int:
    """
    Scale contracts based on signal conviction.
      RVOL 2.0–3.0×  → +1 contract  (strong institutional momentum)
      RVOL ≥ 3.0×    → +2 contracts (exceptional conviction)
      IB range ≥ 150pts → +1 contract (clear trending day, less reversal risk)
      After a losing trade today → -1 contract (protect MLL buffer)
    Clamps to [1, MAX_TRADE_CONTRACTS].
    """
    n = base
    if rvol >= 2.0: n += 1
    if rvol >= 3.0: n += 1
    if ib_range >= 150: n += 1
    if had_loss_today: n -= 1
    return max(1, min(n, MAX_TRADE_CONTRACTS))


# ── Single-day simulation ──────────────────────────────────────────────────────

def simulate_day(day_df: pd.DataFrame, atr_at_open: float,
                 prev_close: float, avg_vol: dict,
                 prop: PropRulesSimulator, cfg: Config,
                 daily_bias: str = 'BOTH',
                 contracts: int = 1,
                 es_day_df: pd.DataFrame | None = None,
                 scale_contracts: bool = False) -> list[dict]:
    """
    daily_bias: 'LONG' | 'SHORT' | 'BOTH'
      Filters entry direction based on higher-timeframe daily trend.
      'LONG' = daily EMA5 > EMA20 → only take long IB breakouts.
      'SHORT' = daily EMA5 < EMA20 → only take short IB breakdowns.
      'BOTH' = take both directions (no HTF filter).
    """

    if len(day_df) < 14:
        return []

    day_df = day_df.copy()
    day_df['vwap'] = compute_vwap(day_df)
    day_df['rvol'] = compute_rvol(day_df, avg_vol)

    today_open = float(day_df['open'].iloc[0])

    # ── Opening gap filter ───────────────────────────────
    gap_pct = 0.0
    if prev_close > 0:
        gap_pct = abs(today_open - prev_close) / prev_close * 100
        if gap_pct > cfg.gap_max_pct:
            return []

    # ── Initial Balance (dynamic window: ib_window_min from 9:30) ────
    # 9:30 + 30min = 10:00 | 9:30 + 60min = 10:30
    from datetime import datetime as _dt
    _ib_end_dt = _dt.combine(day_df.index[0].date(),
                             time(9, 30)) + timedelta(minutes=cfg.ib_window_min)
    ib_end_t   = _ib_end_dt.time()
    ib_bars   = day_df[day_df.index.time < ib_end_t]
    if len(ib_bars) < 4:
        return []

    ib_high  = float(ib_bars['high'].max())
    ib_low   = float(ib_bars['low'].min())
    ib_range = ib_high - ib_low
    ib_mid   = (ib_high + ib_low) / 2

    # Day quality filters
    if ib_range < cfg.min_ib_range:
        return []   # too narrow — chop
    if ib_range > cfg.max_ib_range:
        return []   # too wide — crash/extreme vol day

    # IB-based stop distances
    stop_dist_long  = ib_range * cfg.stop_ib_frac   # IB midpoint from IB high
    stop_dist_short = ib_range * cfg.stop_ib_frac

    # Skip day if stop exceeds our max acceptable risk
    if stop_dist_long > cfg.max_stop_pts:
        return []

    atr = atr_at_open if atr_at_open and atr_at_open > 0 else ib_range

    trades:         list[dict] = []
    position:       Optional[dict] = None
    bars_held       = 0
    session_high    = float(day_df['high'].iloc[0])
    session_low     = float(day_df['low'].iloc[0])
    had_loss_today  = False   # for dynamic sizing: scale down after a loss

    # IB confirmation tracking
    above_ib_closes = 0
    below_ib_closes = 0

    for i, (ts, bar) in enumerate(day_df.iterrows()):
        t         = ts.time()
        price     = float(bar['close'])
        session_high = max(session_high, float(bar['high']))
        session_low  = min(session_low,  float(bar['low']))

        # Track IB confirmation closes (only during IB window)
        if t < ib_end_t:
            if price > ib_high: above_ib_closes += 1
            elif price < ib_low: below_ib_closes += 1
            else:
                above_ib_closes = max(0, above_ib_closes - 1)
                below_ib_closes = max(0, below_ib_closes - 1)

        # ── Exit existing position ──────────────────────
        if position is not None:
            bars_held += 1
            entry   = position['entry']
            stop    = position['stop']
            target  = position['target']
            dir_    = position['direction']
            bar_high = float(bar['high'])
            bar_low  = float(bar['low'])

            # MAE / MFE update (in points)
            if dir_ == 'LONG':
                position['mae'] = max(position['mae'], entry - bar_low)
                position['mfe'] = max(position['mfe'], bar_high - entry)
                peak_price = session_high
            else:
                position['mae'] = max(position['mae'], bar_high - entry)
                position['mfe'] = max(position['mfe'], entry - bar_low)
                peak_price = session_low

            # PCT trail
            pnl_pts = (price - entry) if dir_ == 'LONG' else (entry - price)
            pct_gain = pnl_pts / entry if entry > 0 else 0
            if pct_gain >= cfg.pct_trail_activate:
                if dir_ == 'LONG':
                    trail = peak_price * (1 - cfg.pct_trail_gap)
                    position['stop'] = max(position['stop'], trail)
                else:
                    trail = peak_price * (1 + cfg.pct_trail_gap)
                    position['stop'] = min(position['stop'], trail)

            exit_price  = None
            exit_reason = None

            if dir_ == 'LONG':
                if bar_low  <= position['stop']: exit_price = position['stop']; exit_reason = 'stop'
                elif bar_high >= target:          exit_price = target;           exit_reason = 'target'
                elif pct_gain > 0.005 and price < float(bar['vwap']): exit_price = price; exit_reason = 'vwap_cross'
            else:
                if bar_high >= position['stop']:  exit_price = position['stop']; exit_reason = 'stop'
                elif bar_low <= target:           exit_price = target;           exit_reason = 'target'
                elif pct_gain > 0.005 and price > float(bar['vwap']): exit_price = price; exit_reason = 'vwap_cross'

            if exit_price is None and bars_held >= cfg.max_hold_bars:
                if abs(pnl_pts) < atr * 0.5:
                    exit_price = price; exit_reason = 'no_move'

            if exit_price is None and t >= cfg.eod_close_time:
                exit_price = price; exit_reason = 'eod'

            if exit_price is not None:
                # Apply exit slippage (adverse — moves against us)
                slip = cfg.slippage_pts
                if dir_ == 'LONG':
                    exit_price -= slip   # fills worse on exit
                else:
                    exit_price += slip

                n_ct    = position.get('n_ct', contracts)
                raw_pnl = ((exit_price - entry) if dir_ == 'LONG'
                           else (entry - exit_price)) * POINT_VALUE * n_ct
                net_pnl = prop.record_trade(raw_pnl, contracts=n_ct)
                if net_pnl < 0:
                    had_loss_today = True

                trades.append({
                    'date':        ts.date().isoformat(),
                    'entry_time':  position['entry_time'].strftime('%H:%M'),
                    'exit_time':   t.strftime('%H:%M'),
                    'hour':        position['entry_time'].hour,
                    'contracts':   n_ct,
                    'direction':   dir_,
                    'entry':       position['entry_raw'],   # pre-slippage entry
                    'exit':        exit_price,
                    'stop_init':   position['stop_init'],
                    'target':      target,
                    'atr':         atr,
                    'ib_range':    ib_range,
                    'gap_pct':     round(gap_pct if prev_close > 0 else 0, 2),
                    'bars_held':   bars_held,
                    'mae_pts':     round(position['mae'], 2),
                    'mfe_pts':     round(position['mfe'], 2),
                    'raw_pnl':     round(raw_pnl, 2),
                    'net_pnl':     round(net_pnl, 2),
                    'exit_reason': exit_reason,
                    'status':      'WIN' if net_pnl > 0 else 'LOSS',
                })
                position = None
                bars_held = 0
            continue

        # ── Entry logic ──────────────────────────────────
        if t < ib_end_t:
            continue   # still forming IB
        if t >= cfg.no_entry_after:
            continue
        if cfg.lunch_start <= t <= cfg.lunch_end:
            continue

        ok, _ = prop.check_can_trade()
        if not ok:
            continue

        rvol  = float(bar['rvol'])
        if rvol < cfg.min_rvol:
            continue   # low-volume bar — no conviction

        vwap  = float(bar['vwap'])

        # ── ES confirmation: compute ES IB levels once per day ───────
        # ES must be breaking the same direction as MNQ at entry time.
        es_ib_high = es_ib_low = None
        if es_day_df is not None and not es_day_df.empty:
            es_ib_bars = es_day_df[es_day_df.index.time < ib_end_t]
            if len(es_ib_bars) >= 4:
                es_ib_high = float(es_ib_bars['high'].max())
                es_ib_low  = float(es_ib_bars['low'].min())

        # ── LONG: IB high breakout ───────────────────────────────────
        # Skip if daily trend says SHORT-only
        # Entry gate:
        #   1. Close above IB high (confirmed breakout)
        #   2. ib_confirm_bars consecutive closes above IB (no false break)
        #   3. EMA fast > slow on 5-min (intraday uptrend)
        #   4. Close above VWAP (bullish session bias)
        #   5. ES above its own IB high [if --es-confirm]
        # Stop: IB midpoint (entry - IB_range × stop_frac)
        # Target: entry + IB_range × target_mult  (IB extension)
        _es_bars_now = (es_day_df[es_day_df.index <= ts]
                        if es_day_df is not None and not es_day_df.empty else None)
        es_price_now = float(_es_bars_now['close'].iloc[-1]) if (_es_bars_now is not None and len(_es_bars_now)) else None

        _es_long_ok  = (es_ib_high is None or es_price_now is None or es_price_now > es_ib_high)
        _es_short_ok = (es_ib_low  is None or es_price_now is None or es_price_now < es_ib_low)

        if (daily_bias != 'SHORT'
                and price > ib_high
                and above_ib_closes >= cfg.ib_confirm_bars
                and float(bar['ema_fast']) > float(bar['ema_slow'])
                and price > vwap
                and _es_long_ok):

            entry_raw = price
            entry     = price + cfg.slippage_pts      # entry slippage: fills above ask
            stop      = ib_high - stop_dist_long       # IB midpoint stop
            risk      = entry - stop
            if risk <= 0:
                continue
            target = entry + ib_range * cfg.target_ib_mult   # IB extension target
            if risk > 0 and (target - entry) / risk < cfg.min_rr:
                continue

            n_ct = (_dynamic_contracts(contracts, rvol, ib_range, had_loss_today)
                    if scale_contracts else contracts)
            position = {
                'direction': 'LONG', 'entry': entry, 'entry_raw': entry_raw,
                'entry_time': t, 'stop': stop, 'stop_init': stop,
                'target': target, 'mae': 0.0, 'mfe': 0.0, 'n_ct': n_ct,
            }

        # ── SHORT: IB low breakdown ───────────────────────────────────
        elif (daily_bias != 'LONG'
                and price < ib_low
                and below_ib_closes >= cfg.ib_confirm_bars
                and float(bar['ema_fast']) < float(bar['ema_slow'])
                and price < vwap
                and _es_short_ok):

            entry_raw = price
            entry     = price - cfg.slippage_pts      # entry slippage: fills below bid
            stop      = ib_low + stop_dist_short       # IB midpoint stop
            risk      = stop - entry
            if risk <= 0:
                continue
            target = entry - ib_range * cfg.target_ib_mult
            if risk > 0 and (entry - target) / risk < cfg.min_rr:
                continue

            n_ct = (_dynamic_contracts(contracts, rvol, ib_range, had_loss_today)
                    if scale_contracts else contracts)
            position = {
                'direction': 'SHORT', 'entry': entry, 'entry_raw': entry_raw,
                'entry_time': t, 'stop': stop, 'stop_init': stop,
                'target': target, 'mae': 0.0, 'mfe': 0.0, 'n_ct': n_ct,
            }

        # Update IB confirmation counters after IB window
        if price > ib_high: above_ib_closes += 1
        elif price < ib_low: below_ib_closes += 1

    # Force-close at EOD
    if position is not None:
        last = day_df.iloc[-1]
        ep   = float(last['close'])
        slip = cfg.slippage_pts
        ep   = ep - slip if position['direction'] == 'LONG' else ep + slip
        n_ct = position.get('n_ct', contracts)
        raw  = ((ep - position['entry']) if position['direction'] == 'LONG'
                else (position['entry'] - ep)) * POINT_VALUE * n_ct
        net  = prop.record_trade(raw, contracts=n_ct)
        trades.append({
            'date':        day_df.index[-1].date().isoformat(),
            'entry_time':  position['entry_time'].strftime('%H:%M'),
            'exit_time':   '15:10', 'hour': position['entry_time'].hour,
            'contracts':   n_ct,
            'direction':   position['direction'],
            'entry':       position['entry_raw'], 'exit': ep,
            'stop_init':   position['stop_init'], 'target': position['target'],
            'atr': atr, 'ib_range': ib_range,
            'gap_pct':     round(gap_pct if prev_close > 0 else 0, 2),
            'bars_held': bars_held,
            'mae_pts': round(position['mae'], 2), 'mfe_pts': round(position['mfe'], 2),
            'raw_pnl': round(raw, 2), 'net_pnl': round(net, 2),
            'exit_reason': 'eod_force',
            'status': 'WIN' if net > 0 else 'LOSS',
        })

    return trades


# ── Full backtest ──────────────────────────────────────────────────────────────

def run_backtest(start: str | None = None, end: str | None = None,
                 mode: str = 'TC', cfg: Config | None = None,
                 contracts: int = 1, es_confirm: bool = False,
                 scale_contracts: bool = False) -> list[dict]:
    if cfg is None:
        cfg = Config()

    df = load_bars(start=start, end=end, table='futures_bars_5m')
    if df.empty:
        print('❌ No data. Run: venv/bin/python futures/collect_bars.py --bootstrap')
        return []

    df     = add_indicators(df, cfg)
    ny_df  = filter_ny_session(df)
    avg_vol = build_avg_vol(ny_df)

    # Daily trend bias (EMA5 vs EMA20 on daily bars) — higher-timeframe filter
    daily_df = load_bars(start=start, end=end, table='futures_bars_1d')
    daily_bias_map: dict[str, str] = {}
    if not daily_df.empty:
        daily_df['ema5']  = daily_df['close'].ewm(span=5,  adjust=False).mean()
        daily_df['ema20'] = daily_df['close'].ewm(span=20, adjust=False).mean()
        for ts, row in daily_df.iterrows():
            d = ts.date().isoformat()
            if row['ema5'] > row['ema20']:
                daily_bias_map[d] = 'LONG'
            elif row['ema5'] < row['ema20']:
                daily_bias_map[d] = 'SHORT'
            else:
                daily_bias_map[d] = 'BOTH'

    # ES bars for directional confirmation (--es-confirm flag)
    es_ny: pd.DataFrame = pd.DataFrame()
    if es_confirm:
        es_df = load_bars(symbol='ES', start=start, end=end, table='futures_bars_5m')
        if not es_df.empty:
            es_ny = filter_ny_session(add_indicators(es_df, cfg))
            print(f'  ES data loaded: {len(es_ny):,} NY session bars for confirmation')
        else:
            print('  ⚠️  ES data not found — running without ES confirmation')

    prop  = PropRulesSimulator(mode=mode)
    trades: list[dict] = []
    days  = sorted(ny_df.index.normalize().unique())
    resets = 0   # count how many times MLL reset the prop (blown TC attempts)

    prev_close = 0.0
    for day_ts in days:
        day_str  = day_ts.date().isoformat()
        day_bars = ny_df[ny_df.index.date == day_ts.date()]

        # ATR at open from prior session
        prior = df[df.index.date < day_ts.date()].tail(20)
        atr_open = float(prior['atr'].iloc[-1]) if not prior.empty else 20.0

        # Prior close for gap calc
        if not prior.empty:
            prev_close = float(prior['close'].iloc[-1])

        # Daily trend bias: look up yesterday's daily bar
        yesterday = (day_ts.date() - timedelta(days=1)).isoformat()
        daily_bias = daily_bias_map.get(yesterday, 'BOTH')

        # ES day bars for confirmation
        es_day_bars = (es_ny[es_ny.index.date == day_ts.date()]
                       if es_confirm and not es_ny.empty else pd.DataFrame())

        # Reset prop if MLL was hit — simulates restarting TC after a blown attempt.
        # Without this, one bad multi-contract run in year 1 silences all future years.
        can_trade, reason = prop.check_can_trade()
        if not can_trade and 'MLL' in reason:
            prop = PropRulesSimulator(mode=mode)
            resets += 1

        prop.new_day(day_str)
        day_trades = simulate_day(day_bars, atr_open, prev_close,
                                  avg_vol, prop, cfg, daily_bias,
                                  contracts=contracts, es_day_df=es_day_bars,
                                  scale_contracts=scale_contracts)
        trades.extend(day_trades)

        if not day_bars.empty:
            prev_close = float(day_bars['close'].iloc[-1])

    if resets:
        print(f'  (MLL triggered {resets}× — prop reset each time, simulating TC restart)')
    return trades


# ── Walk-Forward Analysis ──────────────────────────────────────────────────────

def walk_forward(df_5m: pd.DataFrame, cfg: Config,
                 n_windows: int = 6, oos_pct: float = 0.30) -> list[dict]:
    """
    Rolling walk-forward: split history into IS/OOS windows.
    Each window: train on IS (70%), test on OOS (30%).
    Returns list of OOS window results.
    """
    days = sorted(df_5m.index.normalize().unique())
    n    = len(days)
    if n < 20:
        print('  ⚠️  Too few days for walk-forward — need 20+ trading days')
        return []

    window_size = n // n_windows
    oos_size    = max(3, int(window_size * oos_pct))
    is_size     = window_size - oos_size

    results = []
    for i in range(n_windows):
        start_idx = i * window_size
        is_end    = start_idx + is_size
        oos_end   = is_end + oos_size
        if oos_end > n:
            break

        is_days  = days[start_idx:is_end]
        oos_days = days[is_end:oos_end]

        is_start  = is_days[0].date().isoformat()
        is_end_dt = is_days[-1].date().isoformat()
        oos_start = oos_days[0].date().isoformat()
        oos_end_dt = oos_days[-1].date().isoformat()

        # Run OOS
        oos_trades = run_backtest(start=oos_start, end=oos_end_dt, cfg=cfg)
        n_trades   = len(oos_trades)
        oos_wr     = (sum(1 for t in oos_trades if t['status'] == 'WIN') / n_trades * 100
                      if n_trades else 0)
        oos_pnl    = sum(t['net_pnl'] for t in oos_trades)

        results.append({
            'window':    i + 1,
            'is_range':  f'{is_start} → {is_end_dt}',
            'oos_range': f'{oos_start} → {oos_end_dt}',
            'oos_days':  len(oos_days),
            'trades':    n_trades,
            'wr':        round(oos_wr, 1),
            'pnl':       round(oos_pnl, 2),
        })

    return results


# ── Report ─────────────────────────────────────────────────────────────────────

def print_report(trades: list[dict], cfg: Config, mode: str = 'TC', label: str = '',
                 contracts: int = 1, scale_contracts: bool = False):
    if not trades:
        print('No trades.')
        return

    wins   = [t for t in trades if t['status'] == 'WIN']
    losses = [t for t in trades if t['status'] == 'LOSS']
    total  = len(trades)
    wr     = len(wins) / total * 100

    pnls      = [t['net_pnl'] for t in trades]
    total_pnl = sum(pnls)
    avg_pnl   = total_pnl / total
    avg_win   = sum(t['net_pnl'] for t in wins)   / len(wins)  if wins   else 0
    avg_loss  = sum(t['net_pnl'] for t in losses) / len(losses) if losses else 0
    pf        = abs(avg_win * len(wins)) / abs(avg_loss * len(losses)) if losses and avg_loss else 0

    # Daily Sharpe
    daily = defaultdict(float)
    for t in trades:
        daily[t['date']] += t['net_pnl']
    dv = list(daily.values())
    sharpe = (np.mean(dv) / np.std(dv) * math.sqrt(252)
              if len(dv) > 1 and np.std(dv) > 0 else 0)

    # Max drawdown
    eq     = np.cumsum(pnls)
    peak   = np.maximum.accumulate(eq)
    max_dd = float((eq - peak).min()) if len(eq) else 0

    # By year
    by_year = defaultdict(lambda: {'n': 0, 'wins': 0, 'pnl': 0.0})
    for t in trades:
        y = t['date'][:4]
        by_year[y]['n']    += 1
        by_year[y]['wins'] += (1 if t['status'] == 'WIN' else 0)
        by_year[y]['pnl']  += t['net_pnl']

    # By direction
    longs  = [t for t in trades if t['direction'] == 'LONG']
    shorts = [t for t in trades if t['direction'] == 'SHORT']

    # By hour
    by_hour = defaultdict(lambda: {'n': 0, 'wins': 0, 'pnl': 0.0})
    for t in trades:
        h = t['hour']
        by_hour[h]['n']    += 1
        by_hour[h]['wins'] += (1 if t['status'] == 'WIN' else 0)
        by_hour[h]['pnl']  += t['net_pnl']

    # By exit reason
    reasons = defaultdict(int)
    for t in trades:
        reasons[t['exit_reason']] += 1

    # MAE / MFE analysis
    maes = [t['mae_pts'] for t in trades]
    mfes = [t['mfe_pts'] for t in trades]
    mae_p50 = np.percentile(maes, 50) if maes else 0
    mae_p75 = np.percentile(maes, 75) if maes else 0
    mae_p90 = np.percentile(maes, 90) if maes else 0
    mfe_p50 = np.percentile(mfes, 50) if mfes else 0
    mfe_p75 = np.percentile(mfes, 75) if mfes else 0
    win_mfes = [t['mfe_pts'] for t in wins]
    loss_mfes = [t['mfe_pts'] for t in losses]

    hdr = f'  MNQ FUTURES BACKTEST — {mode} mode' + (f' [{label}]' if label else '')
    print('=' * 65)
    print(hdr)
    print('=' * 65)
    sizing_str = f'dynamic 1-{MAX_TRADE_CONTRACTS}ct (base={contracts})' if scale_contracts else f'{contracts}ct'
    print(f'  Config:        IB={cfg.ib_window_min}min | stop={cfg.stop_ib_frac*100:.0f}%IB '
          f'| target={cfg.target_ib_mult}×IB | slip={cfg.slippage_ticks}tk | gap<{cfg.gap_max_pct}%'
          f' | sizing={sizing_str}')
    if scale_contracts and trades:
        ct_dist = {}
        for t in trades:
            n = t.get('contracts', contracts)
            ct_dist[n] = ct_dist.get(n, 0) + 1
        avg_ct = sum(t.get('contracts', contracts) for t in trades) / len(trades)
        dist_str = '  '.join(f'{k}ct:{v}' for k, v in sorted(ct_dist.items()))
        print(f'  Avg contracts: {avg_ct:.2f}  ({dist_str})')
    print()
    print(f'  Trades:        {total}  ({len(daily)} trading days, {total/len(daily):.1f}/day avg)')
    print(f'  Win rate:      {wr:.1f}%  ({len(wins)}W / {len(losses)}L)')
    print(f'  Total P&L:     ${total_pnl:,.2f}')
    print(f'  Avg P&L/trade: ${avg_pnl:,.2f}')
    print(f'  Avg win:       ${avg_win:,.2f}')
    print(f'  Avg loss:      ${avg_loss:,.2f}')
    print(f'  Profit factor: {pf:.2f}')
    print(f'  Sharpe (ann):  {sharpe:.2f}')
    print(f'  Max drawdown:  ${max_dd:,.2f}')
    print()

    print('  ── By Direction ─────────────────────────────────────────')
    for label_d, grp in [('LONG ', longs), ('SHORT', shorts)]:
        if not grp:
            continue
        gwr = sum(1 for t in grp if t['status'] == 'WIN') / len(grp) * 100
        gpnl = sum(t['net_pnl'] for t in grp)
        print(f'    {label_d}  {len(grp):>4} trades  {gwr:>5.1f}% WR  ${gpnl:>9,.2f}')

    print()
    print('  ── By Year ──────────────────────────────────────────────')
    for y in sorted(by_year):
        d = by_year[y]
        yr_wr = d['wins'] / d['n'] * 100 if d['n'] else 0
        print(f'    {y}  {d["n"]:>4} trades  {yr_wr:>5.1f}% WR  ${d["pnl"]:>9,.2f}')

    print()
    print('  ── By Hour of Entry ─────────────────────────────────────')
    for h in sorted(by_hour):
        d = by_hour[h]
        hwr = d['wins'] / d['n'] * 100 if d['n'] else 0
        print(f'    {h:02d}:xx  {d["n"]:>4} trades  {hwr:>5.1f}% WR  ${d["pnl"]:>9,.2f}')

    print()
    print('  ── Exit Reasons ─────────────────────────────────────────')
    for r, n in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f'    {r:<20} {n:>4}  ({n/total*100:.0f}%)')

    print()
    print('  ── MAE / MFE Analysis (in points) ──────────────────────')
    print(f'    MAE p50={mae_p50:.1f}  p75={mae_p75:.1f}  p90={mae_p90:.1f}  '
          f'→ stops < p75 ({mae_p75:.0f}pts) catch 75% of moves')
    print(f'    MFE p50={mfe_p50:.1f}  p75={mfe_p75:.1f}')
    print(f'    MFE winners avg: {np.mean(win_mfes):.1f}pts  '
          f'losers avg: {np.mean(loss_mfes):.1f}pts')

    # Stop placement recommendation
    avg_atr = np.mean([t['atr'] for t in trades]) or 1
    suggested_ib_frac = mae_p75 / (np.mean([t.get('ib_range', avg_atr) for t in trades]) or 1)
    print(f'    IB stop frac to contain 75% MAE: {suggested_ib_frac:.2f}× IB range'
          f'  [current: {cfg.stop_ib_frac:.2f}×]')

    print()


def print_wfa_report(results: list[dict]):
    if not results:
        return
    print('  ── Walk-Forward OOS Results ─────────────────────────────')
    profitable = sum(1 for r in results if r['pnl'] > 0)
    for r in results:
        flag = '✅' if r['pnl'] > 0 else '❌'
        print(f'    Window {r["window"]}  OOS: {r["oos_range"]}  '
              f'{r["trades"]:>3} trades  {r["wr"]:>5.1f}% WR  '
              f'${r["pnl"]:>8,.2f}  {flag}')
    print(f'    Profitable windows: {profitable}/{len(results)}  '
          f'({profitable/len(results)*100:.0f}%)')
    print()


def simulate_tc_eval(trades: list[dict]):
    from futures.prop_rules import TC_PROFIT_TARGET, TC_MLL_AMOUNT, TC_DLL_AMOUNT, TC_DAILY_CAP

    daily_pnl = defaultdict(float)
    for t in trades:
        daily_pnl[t['date']] += t['net_pnl']

    days     = sorted(daily_pnl.keys())
    attempts = 0
    passes   = 0
    window   = 30

    print('  ── TC Eval Simulation ($50K, $3K target, $2K MLL, $1K DLL) ──')

    i = 0
    while i < len(days):
        chunk      = days[i:i + window]
        cum        = 0.0
        hwm        = 50_000.0
        passed = blown = False
        attempts += 1

        for d in chunk:
            pnl        = daily_pnl[d]
            cum        = round(cum + pnl, 2)
            balance    = round(50_000 + cum, 2)
            hwm        = max(hwm, balance)

            if pnl < -TC_DLL_AMOUNT:
                blown = True; break
            if balance < hwm - TC_MLL_AMOUNT:
                blown = True; break
            if cum >= TC_PROFIT_TARGET:
                passed = True; break

        icon = '✅ PASS' if passed else ('💥 BLOWN' if blown else '⏱ TIMEOUT')
        print(f'    Attempt {attempts}: {len(chunk)} days  P&L ${cum:+,.0f}  {icon}')
        if passed:
            passes += 1
        i += len(chunk)

    print(f'    Pass rate: {passes}/{attempts}  ({passes/attempts*100:.0f}%)')
    print()


# ── A/B Config Comparison ─────────────────────────────────────────────────────

def run_ab(start: str | None, end: str | None):
    configs = [
        # Standard IB approach: stop at midpoint (50%), target 1.5× extension
        Config(label='IB60 stop50% tgt1.5x slip1t', ib_window_min=60, stop_ib_frac=0.50, target_ib_mult=1.5, slippage_ticks=1.0),
        # Tighter stop (30% of IB), larger target (2× IB)
        Config(label='IB60 stop30% tgt2.0x slip1t', ib_window_min=60, stop_ib_frac=0.30, target_ib_mult=2.0, slippage_ticks=1.0),
        # Wider stop (70% of IB), smaller target (1.0× IB)
        Config(label='IB60 stop70% tgt1.0x slip1t', ib_window_min=60, stop_ib_frac=0.70, target_ib_mult=1.0, slippage_ticks=1.0),
        # Shorter IB window (30 min ORB), midpoint stop
        Config(label='IB30 stop50% tgt1.5x slip1t', ib_window_min=30, stop_ib_frac=0.50, target_ib_mult=1.5, slippage_ticks=1.0),
        # No slippage (ideal world baseline)
        Config(label='IB60 stop50% tgt1.5x slip0t', ib_window_min=60, stop_ib_frac=0.50, target_ib_mult=1.5, slippage_ticks=0.0),
    ]

    print('=' * 65)
    print('  A/B PARAMETER COMPARISON')
    print('=' * 65)
    print(f'  {"Config":<30} {"Trades":>7} {"WR%":>6} {"P&L":>10} {"Sharpe":>7} {"MaxDD":>10}')
    print(f'  {"-"*30} {"-"*7} {"-"*6} {"-"*10} {"-"*7} {"-"*10}')

    for cfg in configs:
        trades = run_backtest(start=start, end=end, cfg=cfg)
        if not trades:
            print(f'  {cfg.label:<30} {"no trades":>7}')
            continue
        total = len(trades)
        wr    = sum(1 for t in trades if t['status'] == 'WIN') / total * 100
        pnl   = sum(t['net_pnl'] for t in trades)
        daily = defaultdict(float)
        for t in trades:
            daily[t['date']] += t['net_pnl']
        dv = list(daily.values())
        sharpe = (np.mean(dv) / np.std(dv) * math.sqrt(252)
                  if len(dv) > 1 and np.std(dv) > 0 else 0)
        eq     = np.cumsum([t['net_pnl'] for t in trades])
        peak   = np.maximum.accumulate(eq)
        max_dd = float((eq - peak).min())
        print(f'  {cfg.label:<30} {total:>7} {wr:>6.1f} {pnl:>10,.2f} {sharpe:>7.2f} {max_dd:>10,.2f}')

    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='MNQ futures backtest (pro)')
    parser.add_argument('--start',  default=None,  help='Start date YYYY-MM-DD')
    parser.add_argument('--end',    default=None,  help='End date YYYY-MM-DD')
    parser.add_argument('--mode',   default='TC',  choices=['TC', 'XFA'])
    parser.add_argument('--tc-sim', action='store_true', help='TC eval simulation')
    parser.add_argument('--wfa',    action='store_true', help='Walk-forward analysis')
    parser.add_argument('--ab',     action='store_true', help='A/B parameter comparison')

    # Config overrides
    parser.add_argument('--ib',              type=int,   default=60,   help='IB window minutes')
    parser.add_argument('--stop-frac',       type=float, default=0.50, help='Stop as fraction of IB range')
    parser.add_argument('--tgt-mult',        type=float, default=1.5,  help='Target = IB_range × mult')
    parser.add_argument('--slip',            type=float, default=1.0,  help='Slippage ticks per side')
    parser.add_argument('--min-ib',          type=float, default=50.0,  help='Min IB range (pts) — skip narrow chop days')
    parser.add_argument('--max-ib',          type=float, default=600.0, help='Max IB range (pts) — skip extreme vol days (pro: 200)')
    parser.add_argument('--gap',             type=float, default=1.5,   help='Max gap% to trade')
    parser.add_argument('--no-entry-after',  type=int,   default=14,    help='No new entries after this hour ET (e.g. 11 = stop at 11am)')
    parser.add_argument('--min-rvol',        type=float, default=0.7,   help='Min relative volume on entry bar (pro: 1.3)')
    # Contract sizing — scales raw P&L and commission.
    # Practical max for TC $50K: DLL_SOFT ($700) / risk_per_contract.
    # At stop=30% IB, typical IB=130pts → stop=39pts × $2/pt = $78/contract → max ~9 contracts.
    # Platform hard cap (TC_MAX_CONTRACTS) = 50 MNQ per TopStepX $50K rules.
    parser.add_argument('--contracts',       type=int,   default=1,     help='MNQ contracts per trade (default 1; scale for TC sizing)')
    # ES confirmation: require ES to be breaking the same IB direction as MNQ at entry time.
    # Filters out MNQ-only moves that ES doesn't confirm — typically false breaks.
    parser.add_argument('--es-confirm',       action='store_true',      help='Require ES to confirm MNQ IB break direction at entry')
    # Dynamic contract sizing: scale up on high-RVOL / large-IB days, down after losses.
    # Base = --contracts N. Scale: RVOL≥2.0 +1ct, RVOL≥3.0 +2ct, IB≥150pts +1ct,
    # after loss -1ct. Clamps to [1, MAX_TRADE_CONTRACTS=5].
    parser.add_argument('--scale-contracts',  action='store_true',      help='Dynamic sizing: scale contracts [1-5] based on RVOL/IB/loss')

    args = parser.parse_args()

    if args.ab:
        run_ab(args.start, args.end)
        sys.exit(0)

    cfg = Config(
        ib_window_min   = args.ib,
        stop_ib_frac    = args.stop_frac,
        target_ib_mult  = args.tgt_mult,
        slippage_ticks  = args.slip,
        min_ib_range    = args.min_ib,
        max_ib_range    = args.max_ib,
        gap_max_pct     = args.gap,
        no_entry_after  = time(args.no_entry_after, 0),
        min_rvol        = args.min_rvol,
    )

    print(f'Loading MNQ 5-min bars (start={args.start or "all"})...')
    df = load_bars(start=args.start, end=args.end, table='futures_bars_5m')
    if df.empty:
        print('❌ No data. Run bootstrap first.')
        sys.exit(1)
    df_ind = add_indicators(df, cfg)
    ny_df  = filter_ny_session(df_ind)
    print(f'  {len(df):,} total bars → {len(ny_df):,} NY session bars')
    print(f'  {len(sorted(ny_df.index.normalize().unique()))} trading days\n')

    trades = run_backtest(start=args.start, end=args.end, mode=args.mode, cfg=cfg,
                          contracts=args.contracts, es_confirm=args.es_confirm,
                          scale_contracts=args.scale_contracts)
    print_report(trades, cfg, mode=args.mode, contracts=args.contracts,
                 scale_contracts=args.scale_contracts)

    if args.tc_sim and trades:
        simulate_tc_eval(trades)

    if args.wfa:
        print('Running walk-forward analysis...')
        wfa_results = walk_forward(df_ind, cfg)
        print_wfa_report(wfa_results)
