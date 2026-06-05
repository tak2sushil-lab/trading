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
from typing import Optional, Union

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

    # Fixed absolute stop/target (override IB-fraction scaling).
    # Use when IB range varies wildly (e.g. 2026 tariff volatility doubled IB size
    # but actual post-break extension stayed ~70-90pts — making IB-scaled targets unreachable).
    # stop_pts: measured from entry price (not from IB level).
    # target_pts: measured from entry price.
    # When set, min_rr check uses these values; max_stop_pts is ignored.
    stop_pts:        Optional[float] = None  # e.g. 40.0 → fixed 40pt stop from entry
    target_pts:      Optional[float] = None  # e.g. 60.0 → fixed 60pt target from entry

    # Multi-trade: allow up to N entries per day with a cooldown between them.
    # Enables re-entry after target/stop hits (retest setups, continuation on trend days).
    # cooldown_bars=2 means wait 2 × 5-min bars (10 min) after exit before next entry.
    max_daily_trades: int  = 5    # 5 = effectively uncapped (one-trade behaviour by default)
    cooldown_bars:    int  = 0    # bars to wait after exit before next entry

    # Pullback retest entry — second mount on the same fence.
    # After price has run ≥ retest_min_ext pts past IB (the fence is proven), a pullback
    # back near the IB level is a high R:R entry with stop just below the fence.
    # retest_zone_pts: entry window above IB (e.g. ib_high to ib_high+20pts).
    # retest_stop_pts: buffer BELOW IB for the stop (0=stop at IB itself, 15=stop 15pts below).
    #   → Stop at IB itself (0pts) is too tight — MNQ oscillates 20-30pts normally, so
    #     normal bar wiggle tags the stop even on valid retests. Use 10-15pt buffer.
    # retest_min_ext: min pts price must have extended past IB (confirms the run happened).
    retest_zone_pts:   float = 0.0   # 0=off; 20=allow retest entry within 20pts of IB level
    retest_stop_pts:   float = 15.0  # pts below IB level for the retest stop (default 15)
    retest_min_ext:    float = 50.0  # min IB extension before retest is valid
    retest_target_pts: float = 80.0  # retest target: fixed pts FROM IB LEVEL (not IB-scaled).
                                     # Horse runs ~80pts from the fence regardless of IB size.
                                     # Initial entries keep IB-scaled target (captures big trend days).

    # ATR regime filter — "know when to walk away from the pen."
    # Skip days where today's ATR is outside [min_atr_ratio, max_atr_ratio] × 60d rolling median.
    # max_atr_ratio=1.5: skip extreme-vol days (2026 tariff shock — IB doubled, targets unreachable).
    # min_atr_ratio=0.8: skip low-vol grind days (2023 — IB breakouts reverse repeatedly, <30% WR).
    # Band filter (both set): only trade in the "sweet spot" — normal vol regime.
    max_atr_ratio:    float = 0.0   # 0=off; 1.5=skip if today's ATR > 1.5× 60d median
    min_atr_ratio:    float = 0.0   # 0=off; 0.8=skip if today's ATR < 0.8× 60d median (low vol)

    # Retest bounce confirmation: require the retest bar to show a bounce from its low.
    # A close at IB+3 on a falling bar is NOT a bounce — price is still dropping through IB.
    # A close at IB+15 with bar_low at IB+2 IS a bounce — price touched IB area and held.
    # retest_bounce_pts=10: bar_low must be ≤ ib_high+zone_pts AND close-low ≥ 10pts.
    # This filters "falling through" entries while keeping genuine IB-hold entries.
    retest_bounce_pts: float = 0.0  # 0=off; try 10 → require 10pt bounce from bar low

    # Cylinder 4 — Pre-market IB (8:30–9:30 AM ET) breakout.
    # The overnight session (8:30–9:30am) forms a structural high/low.
    # When the RTH session consolidates below the PM high, then breaks above it,
    # that is a "double-fence" confirmation: the RTH IB AND the overnight fence are both broken.
    # Strategy: fire when pm_high > ib_high + premarket_min_ext (level is meaningfully outside IB).
    # Entry: price > pm_high (long) or price < pm_low (short) during RTH.
    # Stop: pm_level - premarket_stop_pts (structural — below the overnight level).
    # Target: pm_level + premarket_target_pts (fixed 80pts — same calibration as retest).
    premarket_ib:          bool  = False # enable Cylinder 4
    premarket_min_ext:     float = 15.0  # min pts PM must be outside RTH IB to activate
    premarket_stop_pts:    float = 20.0  # stop pts below PM level (wider than retest — PM is noisier)
    premarket_target_pts:  float = 80.0  # fixed target from PM level

    # Cylinder 5 — Macro news blackout (DO NOT USE — hurts performance).
    # Testing confirmed: NFP/CPI days = 61.1% WR, +$203/trade (3× normal).
    # Blocking them drops TC 67%→40% and removes $3,660 of P&L.
    # Kept as a flag only for future research.
    macro_blackout:       bool  = False
    macro_blackout_level: str   = 'HIGH'   # 'HIGH' or 'ALL'

    # Cylinder 5b — Macro both-sides override (USE THIS INSTEAD).
    # On HIGH_IMPACT days (NFP/CPI/FOMC), override the EMA daily_bias to 'BOTH'.
    # Unlocks SHORT on bad-news days even when market was uptrending the day before.
    # Data: SHORT on bad NFP = 71.4% WR, +$338/trade avg (7 trades, 5yr).
    # Without this flag, those shorts are blocked when EMA5 > EMA20 prior day.
    # For live trading: Groq classifier sets direction; this flag enables both-sides in backtest.
    macro_both_sides:     bool  = False

    # Win protection: conservative sizing after a good day.
    # After daily P&L ≥ win_protect_pnl, scale down base contracts by 1.
    # Protects TC consistency rule (best_day ≤ 50% of total profit) and locks gains.
    win_protect_pnl:  float = 0.0   # 0=off; e.g. 500 = scale down after $500 daily gain

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
        # 2 sides (entry+exit) × tick_value ($0.50/tick) per slippage tick
        return self.commission + self.slippage_ticks * 2 * TICK_SIZE * POINT_VALUE


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
                       had_loss_today: bool,
                       session_pnl: float = 0.0,
                       win_protect_pnl: float = 0.0) -> int:
    """
    Scale contracts based on signal conviction.
      A   setup (RVOL 2.0–3.0×)  → +1 contract  (strong institutional momentum)
      A+  setup (RVOL 3.0–4.0×)  → +2 contracts (high conviction breakout)
      A++ setup (RVOL ≥ 4.0×)    → +3 contracts (exceptional — max the position)
      IB range ≥ 150pts → +1 contract (clear trending day, less reversal risk)
      After a losing trade today  → -1 contract (protect MLL buffer)
      After win_protect_pnl gain  → -1 contract (conservative after good day)
    Clamps to [1, MAX_TRADE_CONTRACTS].
    """
    n = base
    if rvol >= 2.0: n += 1
    if rvol >= 3.0: n += 1
    if rvol >= 4.0: n += 1          # A++ — max conviction, max size
    if ib_range >= 150: n += 1      # large IB = structural trending day signal (complements RVOL)
    if had_loss_today: n -= 1
    if win_protect_pnl > 0 and session_pnl >= win_protect_pnl:
        n -= 1                       # protect the day's gains
    return max(1, min(n, MAX_TRADE_CONTRACTS))


# ── Single-day simulation ──────────────────────────────────────────────────────

def simulate_day(day_df: pd.DataFrame, atr_at_open: float,
                 prev_close: float, avg_vol: dict,
                 prop: PropRulesSimulator, cfg: Config,
                 daily_bias: str = 'BOTH',
                 contracts: int = 1,
                 es_day_df: pd.DataFrame | None = None,
                 scale_contracts: bool = False,
                 pm_high: float = 0.0,
                 pm_low: float = 0.0) -> list[dict]:
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

    # Skip day if stop exceeds our max acceptable risk.
    # Only applies to IB-fraction stops; when cfg.stop_pts is set (fixed stop mode)
    # the actual risk is stop_pts, not stop_dist_long, so skip this filter.
    if cfg.stop_pts is None and stop_dist_long > cfg.max_stop_pts:
        return []

    atr = atr_at_open if atr_at_open and atr_at_open > 0 else ib_range

    trades:            list[dict] = []
    position:          Optional[dict] = None
    bars_held          = 0
    session_high       = float(day_df['high'].iloc[0])
    session_low        = float(day_df['low'].iloc[0])
    had_loss_today     = False   # for dynamic sizing: scale down after a loss
    session_pnl        = 0.0    # realized P&L so far today (win protection)
    daily_trade_count  = 0       # entries taken today (multi-trade cap)
    cooldown_remaining = 0       # bars to wait before next entry after exit

    # IB confirmation tracking
    above_ib_closes = 0
    below_ib_closes = 0

    # Retest tracking: how far has price extended beyond IB after the initial break?
    # A meaningful extension (≥ retest_min_ext) proves the first run happened — then
    # a pullback back into the retest zone is a structural bounce entry.
    max_above_ib = 0.0   # max pts price reached above IB high (post-IB window)
    max_below_ib = 0.0   # max pts price reached below IB low  (post-IB window)

    # Cylinder 4 PM break tracking — fire at most once per direction per day.
    # IB-window version (9:30-10:30am) has priority; post-IB fallback only fires if
    # PM level was never touched during IB formation.
    pm_long_taken  = False
    pm_short_taken = False

    for i, (ts, bar) in enumerate(day_df.iterrows()):
        t         = ts.time()
        price     = float(bar['close'])
        bar_high  = float(bar['high'])
        bar_low   = float(bar['low'])
        session_high = max(session_high, bar_high)
        session_low  = min(session_low,  bar_low)
        if cooldown_remaining > 0:
            cooldown_remaining -= 1

        # Track max IB extension EVERY bar (even while in a position).
        # This captures the full range of how far price has run past the IB fence —
        # essential for the retest entry which requires a prior meaningful extension.
        if t >= ib_end_t:
            if bar_high > ib_high:
                max_above_ib = max(max_above_ib, bar_high - ib_high)
            if bar_low < ib_low:
                max_below_ib = max(max_below_ib, ib_low - bar_low)

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
                    'entry_type':  position.get('entry_type', 'breakout'),
                    'status':      'WIN' if net_pnl > 0 else 'LOSS',
                })
                session_pnl += net_pnl
                position = None
                bars_held = 0
                daily_trade_count += 1
                cooldown_remaining = cfg.cooldown_bars
            continue

        # ── Entry logic ──────────────────────────────────

        # ── CYLINDER 4 (IB-window): PM level break 9:30–10:30am ──────────────
        # Fire when RTH price first breaks the pre-market high/low DURING IB formation.
        # This is the redesigned version: fires BEFORE the RTH IB is established so it
        # never competes with Cylinders 1/2. The pre-market session (8:30-9:30am) forms
        # a structural high/low; when RTH open immediately breaks that fence = highest
        # conviction signal ("double fence" + fresh institutional flow).
        # No RVOL/EMA gates: opening volume is always elevated; EMAs meaningless at 9:30.
        if (position is None
                and cfg.premarket_ib
                and t < ib_end_t
                and daily_trade_count < cfg.max_daily_trades):
            _ok_pm, _ = prop.check_can_trade()
            if _ok_pm:
                _pm_rvol = float(bar['rvol'])
                # PM level must be structurally OUTSIDE the final RTH IB to be meaningful.
                # If pm_high <= ib_high + min_ext, the IB will form at or above the PM level
                # and the PM fence has no structural value as a breakout signal.
                # (ib_high here is the pre-computed final IB value — a look-ahead used to
                # select only high-quality days; live system would proxy with opening range.)
                if (not pm_long_taken
                        and pm_high > ib_high + cfg.premarket_min_ext
                        and daily_bias != 'SHORT'
                        and price > pm_high):
                    entry_raw = price
                    entry     = price + cfg.slippage_pts
                    stop      = pm_high - cfg.premarket_stop_pts
                    risk      = entry - stop
                    if risk > 0:
                        target = pm_high + cfg.premarket_target_pts
                        if (target - entry) / risk >= cfg.min_rr:
                            n_ct = (_dynamic_contracts(contracts, _pm_rvol, ib_range,
                                                       had_loss_today, session_pnl,
                                                       cfg.win_protect_pnl)
                                    if scale_contracts else contracts)
                            position = {
                                'direction': 'LONG', 'entry': entry, 'entry_raw': entry_raw,
                                'entry_time': t, 'stop': stop, 'stop_init': stop,
                                'target': target, 'mae': 0.0, 'mfe': 0.0, 'n_ct': n_ct,
                                'entry_type': 'pm_break',
                            }
                            pm_long_taken = True
                            # Same-bar exit check: if this bar's low already tagged the stop
                            # (entry bar during IB window — exit block hasn't run yet for this bar).
                            if bar_low <= stop:
                                _ep = stop - cfg.slippage_pts
                                _raw = (_ep - entry) * POINT_VALUE * n_ct
                                _net = prop.record_trade(_raw, contracts=n_ct)
                                if _net < 0: had_loss_today = True
                                trades.append({'date': ts.date().isoformat(),
                                    'entry_time': t.strftime('%H:%M'), 'exit_time': t.strftime('%H:%M'),
                                    'hour': t.hour, 'contracts': n_ct, 'direction': 'LONG',
                                    'entry': entry_raw, 'exit': _ep, 'stop_init': stop, 'target': target,
                                    'atr': atr, 'ib_range': ib_range, 'gap_pct': round(gap_pct if prev_close > 0 else 0, 2),
                                    'bars_held': 0, 'mae_pts': entry - bar_low, 'mfe_pts': 0.0,
                                    'raw_pnl': round(_raw, 2), 'net_pnl': round(_net, 2),
                                    'exit_reason': 'stop', 'entry_type': 'pm_break',
                                    'status': 'WIN' if _net > 0 else 'LOSS'})
                                session_pnl += _net; position = None
                                daily_trade_count += 1; cooldown_remaining = cfg.cooldown_bars

                elif (not pm_short_taken
                        and pm_low < ib_low - cfg.premarket_min_ext
                        and daily_bias != 'LONG'
                        and price < pm_low):
                    entry_raw = price
                    entry     = price - cfg.slippage_pts
                    stop      = pm_low + cfg.premarket_stop_pts
                    risk      = stop - entry
                    if risk > 0:
                        target = pm_low - cfg.premarket_target_pts
                        if (entry - target) / risk >= cfg.min_rr:
                            n_ct = (_dynamic_contracts(contracts, _pm_rvol, ib_range,
                                                       had_loss_today, session_pnl,
                                                       cfg.win_protect_pnl)
                                    if scale_contracts else contracts)
                            position = {
                                'direction': 'SHORT', 'entry': entry, 'entry_raw': entry_raw,
                                'entry_time': t, 'stop': stop, 'stop_init': stop,
                                'target': target, 'mae': 0.0, 'mfe': 0.0, 'n_ct': n_ct,
                                'entry_type': 'pm_break',
                            }
                            pm_short_taken = True
                            # Same-bar exit check for short
                            if bar_high >= stop:
                                _ep = stop + cfg.slippage_pts
                                _raw = (entry - _ep) * POINT_VALUE * n_ct
                                _net = prop.record_trade(_raw, contracts=n_ct)
                                if _net < 0: had_loss_today = True
                                trades.append({'date': ts.date().isoformat(),
                                    'entry_time': t.strftime('%H:%M'), 'exit_time': t.strftime('%H:%M'),
                                    'hour': t.hour, 'contracts': n_ct, 'direction': 'SHORT',
                                    'entry': entry_raw, 'exit': _ep, 'stop_init': stop, 'target': target,
                                    'atr': atr, 'ib_range': ib_range, 'gap_pct': round(gap_pct if prev_close > 0 else 0, 2),
                                    'bars_held': 0, 'mae_pts': bar_high - entry, 'mfe_pts': 0.0,
                                    'raw_pnl': round(_raw, 2), 'net_pnl': round(_net, 2),
                                    'exit_reason': 'stop', 'entry_type': 'pm_break',
                                    'status': 'WIN' if _net > 0 else 'LOSS'})
                                session_pnl += _net; position = None
                                daily_trade_count += 1; cooldown_remaining = cfg.cooldown_bars

        if t < ib_end_t:
            continue   # still forming IB — other cylinders wait
        if daily_trade_count >= cfg.max_daily_trades:
            continue
        if cooldown_remaining > 0:
            continue

        ok, _ = prop.check_can_trade()
        if not ok:
            continue

        rvol  = float(bar['rvol'])
        vwap  = float(bar['vwap'])

        # ES IB levels and current price (used by both initial and retest checks)
        es_ib_high = es_ib_low = None
        if es_day_df is not None and not es_day_df.empty:
            es_ib_bars = es_day_df[es_day_df.index.time < ib_end_t]
            if len(es_ib_bars) >= 4:
                es_ib_high = float(es_ib_bars['high'].max())
                es_ib_low  = float(es_ib_bars['low'].min())
        _es_bars_now = (es_day_df[es_day_df.index <= ts]
                        if es_day_df is not None and not es_day_df.empty else None)
        es_price_now = (float(_es_bars_now['close'].iloc[-1])
                        if _es_bars_now is not None and len(_es_bars_now) else None)
        _es_long_ok  = (es_ib_high is None or es_price_now is None or es_price_now > es_ib_high)
        _es_short_ok = (es_ib_low  is None or es_price_now is None or es_price_now < es_ib_low)

        # ── PULLBACK RETEST (extended 1pm deadline, no ES/RVOL gate) ─────
        # Checked BEFORE the noon no-entry-after cutoff so retests can fire until 1pm.
        # No ES confirmation: on a pullback to IB level ES has usually also pulled back.
        #   The structural fence (IB high/low holding as S/R) IS the signal.
        # No RVOL gate: pullbacks naturally have lower volume — that's a GOOD sign.
        # Condition: price extended ≥ retest_min_ext past IB at any point today
        #   (level proved itself) AND price is now back in the retest zone.
        if (cfg.retest_zone_pts > 0
                and max_above_ib >= cfg.retest_min_ext   # LONG: IB high proved as resistance→support
                and not (cfg.lunch_start <= t <= cfg.lunch_end)
                and t < cfg.no_entry_after):             # same deadline as initial entries

            # Bounce confirmation: the bar must have touched the IB zone (low ≤ ib_high+zone)
            # AND closed at least retest_bounce_pts above its low. Filters "falling through" bars.
            _long_bounce_ok = (cfg.retest_bounce_pts <= 0
                               or (bar_low <= ib_high + cfg.retest_zone_pts
                                   and price - bar_low >= cfg.retest_bounce_pts))

            if (daily_bias != 'SHORT'
                    and ib_high < price <= ib_high + cfg.retest_zone_pts
                    and _long_bounce_ok):
                entry_raw = price
                entry     = price + cfg.slippage_pts
                stop      = ib_high - cfg.retest_stop_pts  # buffer below fence: avoids normal bar wiggle
                risk      = entry - stop
                if risk > 0:
                    # Fixed target from IB level — ~80pts regardless of IB size.
                    # This is the key fix: initial entries use IB-scaled target (big trend days),
                    # but retests use a fixed realistic extension from the fence.
                    target = ib_high + cfg.retest_target_pts
                    if (target - entry) / risk >= cfg.min_rr:
                        n_ct = (_dynamic_contracts(contracts, rvol, ib_range, had_loss_today,
                                                   session_pnl, cfg.win_protect_pnl)
                                if scale_contracts else contracts)
                        position = {
                            'direction': 'LONG', 'entry': entry, 'entry_raw': entry_raw,
                            'entry_time': t, 'stop': stop, 'stop_init': stop,
                            'target': target, 'mae': 0.0, 'mfe': 0.0, 'n_ct': n_ct,
                            'entry_type': 'retest',
                        }

        if (position is None
                and cfg.retest_zone_pts > 0
                and max_below_ib >= cfg.retest_min_ext   # SHORT: IB low proved as support→resistance
                and not (cfg.lunch_start <= t <= cfg.lunch_end)
                and t < cfg.no_entry_after):

            _short_bounce_ok = (cfg.retest_bounce_pts <= 0
                                or (bar_high >= ib_low - cfg.retest_zone_pts
                                    and bar_high - price >= cfg.retest_bounce_pts))

            if (daily_bias != 'LONG'
                    and ib_low - cfg.retest_zone_pts <= price < ib_low
                    and _short_bounce_ok):
                entry_raw = price
                entry     = price - cfg.slippage_pts
                stop      = ib_low + cfg.retest_stop_pts  # buffer above fence
                risk      = stop - entry
                if risk > 0:
                    target = ib_low - cfg.retest_target_pts  # fixed from IB level
                    if (entry - target) / risk >= cfg.min_rr:
                        n_ct = (_dynamic_contracts(contracts, rvol, ib_range, had_loss_today,
                                                   session_pnl, cfg.win_protect_pnl)
                                if scale_contracts else contracts)
                        position = {
                            'direction': 'SHORT', 'entry': entry, 'entry_raw': entry_raw,
                            'entry_time': t, 'stop': stop, 'stop_init': stop,
                            'target': target, 'mae': 0.0, 'mfe': 0.0, 'n_ct': n_ct,
                            'entry_type': 'retest',
                        }

        # ── CYLINDER 4 (post-IB fallback): PM level outside RTH IB ──────────
        # Fires post-IB ONLY when the PM level was NOT broken during the IB window.
        # Use case: PM high set early pre-market, RTH IB consolidates below it all morning,
        # then breaks above pm_high after 10:30am — still a valid double-fence signal.
        # Has full quality gates (RVOL + EMA) since this fires later in the session.
        if (position is None
                and cfg.premarket_ib
                and t >= ib_end_t
                and not (cfg.lunch_start <= t <= cfg.lunch_end)
                and t < cfg.no_entry_after):

            # LONG: PM high structurally above RTH IB, not yet taken today
            if (not pm_long_taken
                    and daily_bias != 'SHORT'
                    and pm_high > ib_high + cfg.premarket_min_ext
                    and price > pm_high
                    and rvol >= cfg.min_rvol
                    and float(bar['ema_fast']) > float(bar['ema_slow'])):
                entry_raw = price
                entry     = price + cfg.slippage_pts
                stop      = pm_high - cfg.premarket_stop_pts
                risk      = entry - stop
                if risk > 0:
                    target = pm_high + cfg.premarket_target_pts
                    if (target - entry) / risk >= cfg.min_rr:
                        n_ct = (_dynamic_contracts(contracts, rvol, ib_range, had_loss_today,
                                                   session_pnl, cfg.win_protect_pnl)
                                if scale_contracts else contracts)
                        position = {
                            'direction': 'LONG', 'entry': entry, 'entry_raw': entry_raw,
                            'entry_time': t, 'stop': stop, 'stop_init': stop,
                            'target': target, 'mae': 0.0, 'mfe': 0.0, 'n_ct': n_ct,
                            'entry_type': 'pm_break',
                        }
                        pm_long_taken = True

            # SHORT: PM low structurally below RTH IB, not yet taken today
            elif (position is None
                    and not pm_short_taken
                    and daily_bias != 'LONG'
                    and pm_low > 0
                    and pm_low < ib_low - cfg.premarket_min_ext
                    and price < pm_low
                    and rvol >= cfg.min_rvol
                    and float(bar['ema_fast']) < float(bar['ema_slow'])):
                entry_raw = price
                entry     = price - cfg.slippage_pts
                stop      = pm_low + cfg.premarket_stop_pts
                risk      = stop - entry
                if risk > 0:
                    target = pm_low - cfg.premarket_target_pts
                    if (entry - target) / risk >= cfg.min_rr:
                        n_ct = (_dynamic_contracts(contracts, rvol, ib_range, had_loss_today,
                                                   session_pnl, cfg.win_protect_pnl)
                                if scale_contracts else contracts)
                        position = {
                            'direction': 'SHORT', 'entry': entry, 'entry_raw': entry_raw,
                            'entry_time': t, 'stop': stop, 'stop_init': stop,
                            'target': target, 'mae': 0.0, 'mfe': 0.0, 'n_ct': n_ct,
                            'entry_type': 'pm_break',
                        }
                        pm_short_taken = True

        # ── INITIAL BREAKOUT (noon cutoff, full RVOL/ES/VWAP/EMA quality gate) ──
        # Only fires if retest did not already set a position this bar.
        if position is None:
            if t < cfg.no_entry_after and not (cfg.lunch_start <= t <= cfg.lunch_end):
                if rvol >= cfg.min_rvol:

                    # LONG: IB high breakout
                    if (daily_bias != 'SHORT'
                            and price > ib_high
                            and above_ib_closes >= cfg.ib_confirm_bars
                            and float(bar['ema_fast']) > float(bar['ema_slow'])
                            and price > vwap
                            and _es_long_ok):

                        entry_raw = price
                        entry     = price + cfg.slippage_pts
                        if cfg.stop_pts is not None:
                            stop = entry - cfg.stop_pts
                            risk = cfg.stop_pts
                        else:
                            stop = ib_high - stop_dist_long
                            risk = entry - stop
                        if risk > 0:
                            target = entry + (cfg.target_pts if cfg.target_pts is not None
                                              else ib_range * cfg.target_ib_mult)
                            if (target - entry) / risk >= cfg.min_rr:
                                n_ct = (_dynamic_contracts(contracts, rvol, ib_range, had_loss_today,
                                                           session_pnl, cfg.win_protect_pnl)
                                        if scale_contracts else contracts)
                                position = {
                                    'direction': 'LONG', 'entry': entry, 'entry_raw': entry_raw,
                                    'entry_time': t, 'stop': stop, 'stop_init': stop,
                                    'target': target, 'mae': 0.0, 'mfe': 0.0, 'n_ct': n_ct,
                                    'entry_type': 'breakout',
                                }

                    # SHORT: IB low breakdown
                    elif (daily_bias != 'LONG'
                            and price < ib_low
                            and below_ib_closes >= cfg.ib_confirm_bars
                            and float(bar['ema_fast']) < float(bar['ema_slow'])
                            and price < vwap
                            and _es_short_ok):

                        entry_raw = price
                        entry     = price - cfg.slippage_pts
                        if cfg.stop_pts is not None:
                            stop = entry + cfg.stop_pts
                            risk = cfg.stop_pts
                        else:
                            stop = ib_low + stop_dist_short
                            risk = stop - entry
                        if risk > 0:
                            target = entry - (cfg.target_pts if cfg.target_pts is not None
                                              else ib_range * cfg.target_ib_mult)
                            if (entry - target) / risk >= cfg.min_rr:
                                n_ct = (_dynamic_contracts(contracts, rvol, ib_range, had_loss_today,
                                                           session_pnl, cfg.win_protect_pnl)
                                        if scale_contracts else contracts)
                                position = {
                                    'direction': 'SHORT', 'entry': entry, 'entry_raw': entry_raw,
                                    'entry_time': t, 'stop': stop, 'stop_init': stop,
                                    'target': target, 'mae': 0.0, 'mfe': 0.0, 'n_ct': n_ct,
                                    'entry_type': 'breakout',
                                }

        # Update IB confirmation counters (post-IB window)
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
            'entry_type':  position.get('entry_type', 'breakout'),
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

    # Cylinder 4: pre-market IB levels (8:30–9:30 AM ET)
    pm_levels: dict[str, tuple[float, float]] = {}   # date → (pm_high, pm_low)
    if cfg.premarket_ib:
        from futures.collect_bars import filter_premarket_session
        pm_df = filter_premarket_session(df)
        for d_ts in sorted(pm_df.index.normalize().unique()):
            d_bars = pm_df[pm_df.index.date == d_ts.date()]
            if len(d_bars) >= 2:
                pm_levels[d_ts.date().isoformat()] = (
                    float(d_bars['high'].max()),
                    float(d_bars['low'].min()),
                )
        print(f'  Cylinder 4 (pre-market IB): {len(pm_levels)} days with PM data')

    prop  = PropRulesSimulator(mode=mode)
    trades: list[dict] = []
    days  = sorted(ny_df.index.normalize().unique())
    resets = 0   # count how many times MLL reset the prop (blown TC attempts)

    # ATR regime filter: build rolling 60-day median ATR per day.
    # On days where the current ATR is abnormally high (broken regime), skip trading.
    # This preserves the IB-extension strategy for regime-appropriate days only.
    rolling_atr_median: dict[str, float] = {}
    if cfg.max_atr_ratio > 0 or cfg.min_atr_ratio > 0:
        atr_hist: list[float] = []
        for d_ts in days:
            d_str  = d_ts.date().isoformat()
            prior_ = df[df.index.date < d_ts.date()].tail(20)
            atr_v  = float(prior_['atr'].iloc[-1]) if not prior_.empty else None
            if atr_v and len(atr_hist) >= 20:
                rolling_atr_median[d_str] = float(np.median(atr_hist[-60:]))
            if atr_v:
                atr_hist.append(atr_v)
        if rolling_atr_median:
            if cfg.max_atr_ratio > 0:
                skipped_hi = sum(1 for d_ts in days
                                 if (m := rolling_atr_median.get(d_ts.date().isoformat()))
                                 and (float(df[df.index.date < d_ts.date()].tail(20)['atr'].iloc[-1])
                                      if not df[df.index.date < d_ts.date()].empty else 0) > cfg.max_atr_ratio * m)
                print(f'  ATR regime filter (<{cfg.max_atr_ratio}×): ~{skipped_hi} extreme-vol days skipped')
            if cfg.min_atr_ratio > 0:
                skipped_lo = sum(1 for d_ts in days
                                 if (m := rolling_atr_median.get(d_ts.date().isoformat()))
                                 and (float(df[df.index.date < d_ts.date()].tail(20)['atr'].iloc[-1])
                                      if not df[df.index.date < d_ts.date()].empty else 0) < cfg.min_atr_ratio * m)
                print(f'  ATR regime filter (>{cfg.min_atr_ratio}×): ~{skipped_lo} low-vol days skipped')

    prev_close = 0.0
    for day_ts in days:
        day_str  = day_ts.date().isoformat()
        day_bars = ny_df[ny_df.index.date == day_ts.date()]

        # ATR at open from prior session
        prior = df[df.index.date < day_ts.date()].tail(20)
        atr_open = float(prior['atr'].iloc[-1]) if not prior.empty else 20.0

        # ATR regime filter: skip abnormally volatile OR abnormally quiet days
        if day_str in rolling_atr_median:
            median_atr = rolling_atr_median[day_str]
            if cfg.max_atr_ratio > 0 and atr_open > cfg.max_atr_ratio * median_atr:
                if not prior.empty:
                    prev_close = float(prior['close'].iloc[-1])
                continue
            if cfg.min_atr_ratio > 0 and atr_open < cfg.min_atr_ratio * median_atr:
                if not prior.empty:
                    prev_close = float(prior['close'].iloc[-1])
                continue

        # Prior close for gap calc
        if not prior.empty:
            prev_close = float(prior['close'].iloc[-1])

        # Daily trend bias: look up yesterday's daily bar
        yesterday = (day_ts.date() - timedelta(days=1)).isoformat()
        daily_bias = daily_bias_map.get(yesterday, 'BOTH')

        # Macro both-sides override: on HIGH_IMPACT days (NFP/CPI/FOMC),
        # ignore EMA trend and trade both directions. Unlocks SHORT on bad-news
        # days even when market was uptrending. In live trading this is replaced
        # by Groq classification (SHORT/LONG/NEUTRAL from actual headline).
        if cfg.macro_both_sides:
            from futures.macro_calendar import classify_date as _classify
            if _classify(day_str) == 'HIGH_IMPACT':
                daily_bias = 'BOTH'

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

        # Cylinder 5: macro blackout — skip entire day if high-impact release
        if cfg.macro_blackout:
            from futures.macro_calendar import classify_date, is_high_impact
            day_class = classify_date(day_str)
            skip_day = (day_class == 'HIGH_IMPACT' or
                        (cfg.macro_blackout_level == 'ALL' and day_class == 'MEDIUM_IMPACT'))
            if skip_day:
                if not day_bars.empty:
                    prev_close = float(day_bars['close'].iloc[-1])
                continue

        _pm_high, _pm_low = pm_levels.get(day_str, (0.0, 0.0))
        day_trades = simulate_day(day_bars, atr_open, prev_close,
                                  avg_vol, prop, cfg, daily_bias,
                                  contracts=contracts, es_day_df=es_day_bars,
                                  scale_contracts=scale_contracts,
                                  pm_high=_pm_high, pm_low=_pm_low)

        # Tag each trade with its macro classification (for analysis)
        if day_trades:
            from futures.macro_calendar import classify_date
            macro_class = classify_date(day_str)
            for t in day_trades:
                t['macro_class'] = macro_class

        trades.extend(day_trades)

        if not day_bars.empty:
            prev_close = float(day_bars['close'].iloc[-1])

    if resets:
        print(f'  (MLL triggered {resets}× — prop reset each time, simulating TC restart)')
    return trades


# ── Walk-Forward Analysis ──────────────────────────────────────────────────────

def walk_forward(df_5m: pd.DataFrame, cfg: Config,
                 n_windows: int = 6, oos_pct: float = 0.30,
                 mode: str = 'TC', contracts: int = 1,
                 es_confirm: bool = False, scale_contracts: bool = False) -> list[dict]:
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

        # Run OOS with the same config/sizing as the main backtest
        oos_trades = run_backtest(start=oos_start, end=oos_end_dt, cfg=cfg,
                                  mode=mode, contracts=contracts,
                                  es_confirm=es_confirm, scale_contracts=scale_contracts)
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
    stop_str   = f'{cfg.stop_pts:.0f}pts fixed' if cfg.stop_pts   is not None else f'{cfg.stop_ib_frac*100:.0f}%IB'
    tgt_str    = f'{cfg.target_pts:.0f}pts fixed' if cfg.target_pts is not None else f'{cfg.target_ib_mult}×IB'
    multi_str  = (f' | max_trades={cfg.max_daily_trades}/day cooldown={cfg.cooldown_bars}bars'
                  if cfg.max_daily_trades < 5 or cfg.cooldown_bars > 0 else '')
    print(f'  Config:        IB={cfg.ib_window_min}min | stop={stop_str} '
          f'| target={tgt_str} | slip={cfg.slippage_ticks}tk | gap<{cfg.gap_max_pct}%'
          f' | sizing={sizing_str}{multi_str}')
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

    # Entry type breakdown (breakout vs retest) — only shown if both types present
    by_etype = defaultdict(lambda: {'n': 0, 'wins': 0, 'pnl': 0.0})
    for t in trades:
        et = t.get('entry_type', 'breakout')
        by_etype[et]['n']    += 1
        by_etype[et]['wins'] += (1 if t['status'] == 'WIN' else 0)
        by_etype[et]['pnl']  += t['net_pnl']
    if len(by_etype) > 1:
        print()
        print('  ── By Entry Type ─────────────────────────────────────')
        for et, d in sorted(by_etype.items()):
            ewr = d['wins'] / d['n'] * 100 if d['n'] else 0
            print(f'    {et:<10} {d["n"]:>4} trades  {ewr:>5.1f}% WR  ${d["pnl"]:>9,.2f}')

    print()
    print('  ── Exit Reasons ─────────────────────────────────────────')
    for r, n in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f'    {r:<20} {n:>4}  ({n/total*100:.0f}%)')

    # Macro release day breakdown (always shown if macro_class tag exists)
    by_macro = {}
    for t in trades:
        mc = t.get('macro_class', 'NORMAL')
        if mc not in by_macro:
            by_macro[mc] = {'n': 0, 'wins': 0, 'pnl': 0.0}
        by_macro[mc]['n']    += 1
        by_macro[mc]['wins'] += (1 if t['status'] == 'WIN' else 0)
        by_macro[mc]['pnl']  += t['net_pnl']
    if len(by_macro) > 1 or 'HIGH_IMPACT' in by_macro:
        print()
        print('  ── By Macro Release Day ─────────────────────────────────')
        for mc in ['HIGH_IMPACT', 'MEDIUM_IMPACT', 'NORMAL']:
            if mc not in by_macro:
                continue
            d = by_macro[mc]
            mwr = d['wins'] / d['n'] * 100 if d['n'] else 0
            flag = ' ← skip?' if (mc == 'HIGH_IMPACT' and mwr < 40) else ''
            print(f'    {mc:<15}  {d["n"]:>4} trades  {mwr:>5.1f}% WR  ${d["pnl"]:>9,.2f}{flag}')

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
    parser = argparse.ArgumentParser(
        description='MNQ futures backtest (pro)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Named strategies (--strategy):
  tc_champion    Daily Driver — safe TC eval, 67% pass, all regimes
  tc_aggressive  Ferrari — fast TC pass, base=3, favorable regimes only
  xfa_conservative  Family SUV — live XFA, ATR filter, 0%% blow rate

  --strategy loads a preset. Any additional flags OVERRIDE specific params.
  Example: --strategy tc_champion --start 2026-01-01  (run champion on 2026 only)

  To list all strategies:  --list-strategies
        """)
    parser.add_argument('--start',  default=None,  help='Start date YYYY-MM-DD')
    parser.add_argument('--end',    default=None,  help='End date YYYY-MM-DD')
    parser.add_argument('--mode',   default='TC',  choices=['TC', 'XFA'])
    parser.add_argument('--tc-sim', action='store_true', help='TC eval simulation')
    parser.add_argument('--wfa',    action='store_true', help='Walk-forward analysis')
    parser.add_argument('--ab',     action='store_true', help='A/B parameter comparison')

    # Named strategy preset — loads JSON config from futures/strategies/
    parser.add_argument('--strategy', default=None,
                        help='Load named strategy preset (tc_champion, tc_aggressive, xfa_conservative). Individual flags override.')
    parser.add_argument('--list-strategies', action='store_true',
                        help='List all available named strategies and their performance')

    # Config overrides
    parser.add_argument('--ib',              type=int,   default=None,   help='IB window minutes')
    parser.add_argument('--ib-confirm',      type=int,   default=None,   help='Consecutive closes above/below IB before entry')
    parser.add_argument('--stop-frac',       type=float, default=None,   help='Stop as fraction of IB range')
    parser.add_argument('--tgt-mult',        type=float, default=None,   help='Target = IB_range × mult')
    parser.add_argument('--slip',            type=float, default=None,   help='Slippage ticks per side')
    parser.add_argument('--min-ib',          type=float, default=None,   help='Min IB range (pts)')
    parser.add_argument('--max-ib',          type=float, default=None,   help='Max IB range (pts)')
    parser.add_argument('--gap',             type=float, default=None,   help='Max gap% to trade')
    parser.add_argument('--no-entry-after',  type=int,   default=None,   help='No new entries after this hour ET')
    parser.add_argument('--min-rvol',        type=float, default=None,   help='Min relative volume on entry bar')
    # Contract sizing — scales raw P&L and commission.
    # Practical max for TC $50K: DLL_SOFT ($700) / risk_per_contract.
    # At stop=30% IB, typical IB=130pts → stop=39pts × $2/pt = $78/contract → max ~9 contracts.
    # Platform hard cap (TC_MAX_CONTRACTS) = 50 MNQ per TopStepX $50K rules.
    parser.add_argument('--contracts',       type=int,   default=None,  help='MNQ contracts per trade')
    # ES confirmation: require ES to be breaking the same IB direction as MNQ at entry time.
    # Filters out MNQ-only moves that ES doesn't confirm — typically false breaks.
    parser.add_argument('--es-confirm',       action='store_true',      help='Require ES to confirm MNQ IB break direction at entry')
    # Dynamic contract sizing: scale up on high-RVOL / large-IB days, down after losses.
    # Base = --contracts N. Scale: RVOL≥2.0 +1ct, RVOL≥3.0 +2ct, IB≥150pts +1ct,
    # after loss -1ct. Clamps to [1, MAX_TRADE_CONTRACTS=5].
    parser.add_argument('--scale-contracts',  action='store_true',      help='Dynamic sizing: scale contracts [1-5] based on RVOL/IB/loss')

    # Fixed stop/target (override IB-fraction scaling).
    # MNQ post-break extension is ~70-90pts regardless of IB size — IB-scaled targets
    # become unreachable in high-vol years (2026: IB=213pts → 0.75×IB=160pts vs actual 80pts).
    # Use --tgt-pts 60 --stop-pts 40 for a 1.5 R:R that works in any vol regime.
    parser.add_argument('--tgt-pts',    type=float, default=None, help='Fixed target in points from entry (e.g. 60). Overrides --tgt-mult.')
    parser.add_argument('--stop-pts',   type=float, default=None, help='Fixed stop in points from entry (e.g. 40). Overrides --stop-frac.')

    # Multi-trade per day.
    # --max-trades 3: take up to 3 entries per day (retest / continuation setups).
    # --cooldown 2: wait 2×5min bars after exit before next entry (prevents chasing).
    parser.add_argument('--max-trades', type=int,   default=None, help='Max entries per day.')
    parser.add_argument('--cooldown',   type=int,   default=None, help='Bars to wait after exit before next entry.')

    # Pullback retest entry — second mount at the structural fence.
    # After initial IB break run (price extended ≥ --retest-min-ext pts), a pullback
    # near the IB level is a high-R:R entry (stop AT IB level, same IB extension target).
    # --retest-zone 20: entry zone is IB level to IB+20pts above (for LONG).
    # --retest-min-ext 50: require price to have moved ≥50pts past IB before retest counts.
    parser.add_argument('--retest-zone',       type=float, default=None, help='Enable pullback retest: entry zone width above IB level (0=off, try 20).')
    parser.add_argument('--retest-stop-pts',   type=float, default=None, help='Retest stop buffer below IB level (default 15pts).')
    parser.add_argument('--retest-min-ext',    type=float, default=None, help='Min pts price must extend past IB before retest is valid (default 50).')
    parser.add_argument('--retest-target-pts', type=float, default=None, help='Retest target: fixed pts from IB level (default 80).')

    # ATR regime band filter — trade only in the "sweet spot" of normal volatility.
    # --max-atr-ratio 1.5: skip extreme-vol days (2026 tariff shock — IB doubled, targets unreachable).
    # --min-atr-ratio 0.8: skip low-vol grind days (2023 — IB breakouts reverse repeatedly, ~28% WR).
    # Both together: only trade when ATR is 0.8× to 1.5× the 60-day rolling median.
    parser.add_argument('--max-atr-ratio', type=float, default=None, help='Skip day if ATR > N×60d median (0=off, try 1.5 — extreme vol filter).')
    parser.add_argument('--min-atr-ratio', type=float, default=None, help='Skip day if ATR < N×60d median (0=off, try 0.8 — low-vol grind filter).')

    # Win protection — conservative sizing after a good day.
    # After daily P&L ≥ N, base contracts reduce by 1 (lock in gains, protect TC consistency rule).
    # Try --win-protect 500.
    parser.add_argument('--win-protect',       type=float, default=None, help='Scale down after daily P&L ≥ N (0=off, try 500).')
    parser.add_argument('--retest-bounce-pts', type=float, default=None, help='Retest entry: require bar to bounce N pts from its low (0=off, try 10).')
    # Cylinder 4 — Pre-market IB breakout.
    # Fire when the overnight 8:30–9:30am high/low is outside the RTH IB by ≥ premarket-min-ext pts.
    # Most powerful on NFP/CPI/FOMC days where pre-market reacts, then RTH consolidates.
    # Cylinder 5 — Macro news blackout.
    # Skip entries on high-impact release days (NFP/CPI/FOMC).
    # Use --macro-blackout to measure the impact on backtest WR.
    # If release days have much lower WR: the blackout is valuable.
    parser.add_argument('--macro-blackout',    action='store_true', help='Skip HIGH-impact days (DO NOT USE — hurts performance, testing only).')
    parser.add_argument('--macro-both-sides', action='store_true', help='On HIGH_IMPACT days (NFP/CPI), trade BOTH directions regardless of EMA trend.')
    parser.add_argument('--macro-blackout-all', action='store_true', help='Skip ALL release days including GDP/PPI.')
    parser.add_argument('--macro-analysis', action='store_true', help='Show WR breakdown by macro release type (no blackout applied).')
    parser.add_argument('--premarket-ib',       action='store_true', help='Enable Cylinder 4: pre-market IB breakout.')
    parser.add_argument('--premarket-min-ext',  type=float, default=None, help='Min pts PM must be outside RTH IB to activate (default 15).')
    parser.add_argument('--premarket-stop-pts', type=float, default=None, help='Stop pts below/above PM level (default 20).')
    parser.add_argument('--premarket-tgt-pts',  type=float, default=None, help='Target pts from PM level (default 80).')

    args = parser.parse_args()

    # ── Strategy preset handling ──────────────────────────────────────────────
    # List strategies
    if args.list_strategies:
        from futures.strategies import list_strategies, print_strategy_summary
        strategies = list_strategies()
        print(f'\n{"="*70}')
        print('  AVAILABLE STRATEGIES')
        print(f'{"="*70}')
        print(f'  {"Name":<22} {"TC%":>4} {"P&L":>9} {"MaxDD":>9} {"Blow%":>6}')
        print(f'  {"-"*22} {"-"*4} {"-"*9} {"-"*9} {"-"*6}')
        for s in strategies:
            print(f'  {s["name"]:<22} {str(s["tc_pass_pct"]):>4} ${s["total_pnl"]:>8,.0f} ${s["max_dd"]:>8,.0f} {str(s["blow_pct"]):>5}%')
            print(f'    └─ {s["label"]}')
        print()
        import sys; sys.exit(0)

    # Load strategy preset (sets defaults; CLI flags still override)
    preset_config: dict = {}
    preset_run: dict = {}
    if args.strategy:
        from futures.strategies import load_strategy, print_strategy_summary
        print_strategy_summary(args.strategy)
        preset_config, preset_run = load_strategy(args.strategy)
        print(f'  Loaded strategy: {args.strategy!r}  (any CLI flag overrides the preset)')
        print()

    if args.ab:
        run_ab(args.start, args.end)
        sys.exit(0)

    # Helper: CLI arg (non-None) > preset > hardcoded default
    def _v(cli_val, preset_key, default):
        if cli_val is not None:
            return cli_val
        if preset_key in preset_config:
            return preset_config[preset_key]
        return default

    no_entry_h = _v(args.no_entry_after, 'no_entry_after_hour', 14)
    cfg = Config(
        ib_window_min    = _v(args.ib,          'ib_window_min',    60),
        ib_confirm_bars  = _v(args.ib_confirm,  'ib_confirm_bars',  1),
        stop_ib_frac     = _v(args.stop_frac,   'stop_ib_frac',     0.50),
        target_ib_mult   = _v(args.tgt_mult,    'target_ib_mult',   1.5),
        slippage_ticks   = _v(args.slip,        'slippage_ticks',   1.0),
        min_ib_range     = _v(args.min_ib,      'min_ib_range',     50.0),
        max_ib_range     = _v(args.max_ib,      'max_ib_range',     600.0),
        gap_max_pct      = _v(args.gap,         'gap_max_pct',      1.5),
        no_entry_after   = time(no_entry_h, 0),
        min_rvol         = _v(args.min_rvol,    'min_rvol',         0.7),
        stop_pts         = _v(args.stop_pts,    'stop_pts',         None),
        target_pts       = _v(args.tgt_pts,     'target_pts',       None),
        max_daily_trades = _v(args.max_trades,  'max_daily_trades', 5),
        cooldown_bars    = _v(args.cooldown,    'cooldown_bars',    0),
        retest_zone_pts   = _v(args.retest_zone,       'retest_zone_pts',   0.0),
        retest_stop_pts   = _v(args.retest_stop_pts,   'retest_stop_pts',   15.0),
        retest_min_ext    = _v(args.retest_min_ext,    'retest_min_ext',    50.0),
        retest_target_pts = _v(args.retest_target_pts, 'retest_target_pts', 80.0),
        retest_bounce_pts = _v(args.retest_bounce_pts, 'retest_bounce_pts', 0.0),
        max_atr_ratio    = _v(args.max_atr_ratio, 'max_atr_ratio',  0.0),
        min_atr_ratio    = _v(args.min_atr_ratio, 'min_atr_ratio',  0.0),
        win_protect_pnl  = _v(args.win_protect,   'win_protect_pnl', 0.0),
        macro_blackout         = args.macro_blackout or preset_config.get('macro_blackout', False),
        macro_blackout_level   = 'ALL' if args.macro_blackout_all else preset_config.get('macro_blackout_level', 'HIGH'),
        macro_both_sides       = args.macro_both_sides or preset_config.get('macro_both_sides', False),
        premarket_ib           = args.premarket_ib or preset_config.get('premarket_ib', False),
        premarket_min_ext      = _v(args.premarket_min_ext,  'premarket_min_ext',  15.0),
        premarket_stop_pts     = _v(args.premarket_stop_pts, 'premarket_stop_pts', 20.0),
        premarket_target_pts   = _v(args.premarket_tgt_pts,  'premarket_target_pts', 80.0),
    )

    # Run params: CLI args override preset, preset overrides defaults
    _contracts       = _v(args.contracts,      None, preset_run.get('contracts', 1))
    _es_confirm      = args.es_confirm or preset_run.get('es_confirm', False)
    _scale_contracts = args.scale_contracts or preset_run.get('scale_contracts', False)

    print(f'Loading MNQ 5-min bars (start={args.start or "all"})...')
    df = load_bars(start=args.start, end=args.end, table='futures_bars_5m')
    if df.empty:
        print('❌ No data. Run bootstrap first.')
        sys.exit(1)
    df_ind = add_indicators(df, cfg)
    ny_df  = filter_ny_session(df_ind)
    print(f'  {len(df):,} total bars → {len(ny_df):,} NY session bars')
    print(f'  {len(sorted(ny_df.index.normalize().unique()))} trading days\n')

    _mode = args.mode if args.mode != 'TC' else preset_run.get('mode', args.mode)
    trades = run_backtest(start=args.start, end=args.end, mode=_mode, cfg=cfg,
                          contracts=_contracts, es_confirm=_es_confirm,
                          scale_contracts=_scale_contracts)
    print_report(trades, cfg, mode=args.mode, contracts=_contracts,
                 scale_contracts=_scale_contracts)

    # Macro analysis: show WR on release days vs normal days
    if getattr(args, 'macro_analysis', False) and trades:
        from futures.macro_calendar import classify_date
        for t in trades:
            if 'macro_class' not in t:
                t['macro_class'] = classify_date(t['date'])
        print('  ── Macro Release Day Analysis ───────────────────────────')
        from collections import defaultdict as _dd
        by_mc = _dd(lambda: {'n': 0, 'wins': 0, 'pnl': 0.0, 'days': set()})
        for t in trades:
            mc = t.get('macro_class', 'NORMAL')
            by_mc[mc]['n']    += 1
            by_mc[mc]['wins'] += (1 if t['status'] == 'WIN' else 0)
            by_mc[mc]['pnl']  += t['net_pnl']
            by_mc[mc]['days'].add(t['date'])
        for mc in ['HIGH_IMPACT', 'MEDIUM_IMPACT', 'NORMAL']:
            if mc not in by_mc: continue
            d = by_mc[mc]
            mwr = d['wins'] / d['n'] * 100 if d['n'] else 0
            avg = d['pnl'] / d['n'] if d['n'] else 0
            recommendation = 'SKIP' if (mc == 'HIGH_IMPACT' and mwr < 42) else 'TRADE'
            print(f'  {mc:<15}  {d["n"]:>4}t  {mwr:>5.1f}%WR  ${d["pnl"]:>9,.0f}  '
                  f'${avg:>7,.0f}/t  {len(d["days"])} days  → {recommendation}')
        print()

    if args.tc_sim and trades:
        simulate_tc_eval(trades)

    if args.wfa:
        print('Running walk-forward analysis...')
        wfa_results = walk_forward(df_ind, cfg, mode=_mode,
                                   contracts=_contracts, es_confirm=_es_confirm,
                                   scale_contracts=_scale_contracts)
        print_wfa_report(wfa_results)
