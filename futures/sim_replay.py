"""
sim_replay.py — Bar-by-bar futures strategy replay on historical data.

Mirrors futures_trader.py signal logic exactly:
  - PM IB (8:30–9:29 ET) — Cylinder 4
  - ORB (9:30–9:44 ET, 3 bars) — Cylinder 1
  - VWAP reclaim / rejection — Cylinder 2
  - Momentum (3 consecutive bars) — Cylinder 3
  - open_play_bull / open_play_bear — intraday open direction
  - grade_entry() scoring identical to live (incl. RSI gates)
  - Exit stack: stop, DLL, target, VWAP cross, no-move, EOD
  - Overnight bias via compute_overnight_bias_5m() (mirrors live)
  - ATR/RSI from 2-day history (matches live's 2-day bar window)
  - get_regime() uses yesterday's last close for day_chg_pct
  - SHORT requires WEAK regime + >=3 consecutive WEAK scans (or bias=SHORT)
  - No-entry after 14:00 ET (matches live's after-2pm gate)
  - IB range gate: H-L from 9:30 to now >= 50pts required
  - RVOL gate: current bar volume >= historical slot average

Usage:
    venv/bin/python futures/sim_replay.py
    venv/bin/python futures/sim_replay.py --start 2026-06-01 --end 2026-06-13
"""

import argparse
import sys
import os
import sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datetime as _dt
import pandas as pd
import pytz

from futures.collect_bars import load_bars, filter_ny_session, filter_premarket_session
from futures.hero_score import (score_entry, contracts_from_score,
                                detect_regime, score_entry_regime,
                                contracts_from_regime_score)

# ── Constants — must match futures_trader.py ─────────────────────────────────

POINT_VALUE    = 2.00
TICK_SIZE      = 0.25
TICK_VALUE     = 0.50
COMMISSION_RT  = 1.24

MAX_DAILY_TRADES   = 2
MAX_DAILY_LOSS     = 1_250.0  # IBKR_DLL_SOFT — 25% of $5K capital (updated Jun 16 2026)
HERO_GATE_ENABLED           = True   # Phase 5 regime-aware scoring — disable via --no-hero-gate
ALLOW_LONG_ON_SHORT_BIAS    = False  # Test flag: allow LONG entries even on SHORT ovn_pos bias
MAX_RISK_PER_TRADE = 100.0

STOP_ATR_MULT   = 1.5
TARGET_ATR_MULT = 3.0
MIN_RR          = 2.0

BE_ACTIVATE_PTS  = 30.0
TRAIL_WIDE_PTS   = 60.0
TRAIL_WIDE_GAP   = 20.0
TRAIL_TIGHT_PTS  = 85.0
TRAIL_TIGHT_GAP  = 10.0

NO_MOVE_MINUTES  = 90
NO_MOVE_MAX_PTS  = 25.0
NO_MOVE_MIN_PTS  = -10.0

# Session boundaries (ET) — match futures_trader.py
NY_OPEN_END   = _dt.time(10, 30)
MIDDAY_END    = _dt.time(12, 0)
LUNCH_END     = _dt.time(13, 0)
AFTERNOON_END = _dt.time(15, 30)
EOD_CLOSE     = _dt.time(15, 10)   # filter_ny_session ceiling

# NYSE holidays 2021-2026 (observed dates when holiday falls on weekend)
US_HOLIDAYS = {
    # 2021
    _dt.date(2021, 1,  1), _dt.date(2021, 1, 18), _dt.date(2021, 2, 15),
    _dt.date(2021, 4,  2), _dt.date(2021, 5, 31), _dt.date(2021, 7,  5),
    _dt.date(2021, 9,  6), _dt.date(2021, 11, 25), _dt.date(2021, 12, 24),
    # 2022
    _dt.date(2022, 1, 17), _dt.date(2022, 2, 21), _dt.date(2022, 4, 15),
    _dt.date(2022, 5, 30), _dt.date(2022, 6, 19), _dt.date(2022, 7,  4),
    _dt.date(2022, 9,  5), _dt.date(2022, 11, 24), _dt.date(2022, 12, 26),
    # 2023
    _dt.date(2023, 1,  2), _dt.date(2023, 1, 16), _dt.date(2023, 2, 20),
    _dt.date(2023, 4,  7), _dt.date(2023, 5, 29), _dt.date(2023, 6, 19),
    _dt.date(2023, 7,  4), _dt.date(2023, 9,  4), _dt.date(2023, 11, 23),
    _dt.date(2023, 12, 25),
    # 2024
    _dt.date(2024, 1,  1), _dt.date(2024, 1, 15), _dt.date(2024, 2, 19),
    _dt.date(2024, 3, 29), _dt.date(2024, 5, 27), _dt.date(2024, 6, 19),
    _dt.date(2024, 7,  4), _dt.date(2024, 9,  2), _dt.date(2024, 11, 28),
    _dt.date(2024, 12, 25),
    # 2025
    _dt.date(2025, 1,  1), _dt.date(2025, 1,  9), _dt.date(2025, 1, 20),
    _dt.date(2025, 2, 17), _dt.date(2025, 4, 18), _dt.date(2025, 5, 26),
    _dt.date(2025, 6, 19), _dt.date(2025, 7,  4), _dt.date(2025, 9,  1),
    _dt.date(2025, 11, 27), _dt.date(2025, 12, 25),
    # 2026
    _dt.date(2026, 1,  1), _dt.date(2026, 1, 19), _dt.date(2026, 2, 16),
    _dt.date(2026, 4,  3), _dt.date(2026, 5, 25), _dt.date(2026, 6, 19),
    _dt.date(2026, 7,  3), _dt.date(2026, 9,  7), _dt.date(2026, 11, 26),
    _dt.date(2026, 12, 25),
}
US_HOLIDAYS_2026 = US_HOLIDAYS  # alias — futures_trader.py still references this name

# Overnight bias constants — match futures_trader.py
_OVN_COMPRESS = 50.0
_OVN_SKIP_LO  = 0.20
_OVN_SKIP_HI  = 0.40
_OVN_TREND_HI = 0.85
_OVN_TREND_LO = 0.20

ET = pytz.timezone('America/New_York')

# Entry cutoff — matches live's "no-entry-after 14:00 gate"
ENTRY_CUTOFF = _dt.time(14, 0)

# IB window — matches live's 10:30 gate: no entries until Initial Balance is set (60 min)
IB_READY_TIME = _dt.time(10, 30)

# IB range minimum (pts) — live gate: thin IB (<50pts) → skip
MIN_IB_RANGE = 50.0

# ── Historical per-slot volume averages (RVOL) ────────────────────────────────
# Loaded once from market_data.db to mirror live's _avg_vol_by_time dict.
_avg_vol_by_time: dict = {}

def _load_avg_volumes() -> dict:
    """Load per-slot avg volume from market_data.db (mirrors live load_avg_volumes)."""
    try:
        _dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db   = os.path.join(_dir, 'market_data.db')
        conn = sqlite3.connect(db)
        cutoff = (_dt.datetime.now() - _dt.timedelta(days=550)).strftime('%Y-%m-%d')
        df = pd.read_sql(
            "SELECT ts_utc, volume FROM futures_bars_5m WHERE symbol='MNQ' AND ts_utc >= ?",
            conn, params=[cutoff],
        )
        conn.close()
        if df.empty:
            return {}
        df['ts'] = pd.to_datetime(df['ts_utc'], utc=True, format='ISO8601').dt.tz_convert(ET)
        df['slot'] = df['ts'].dt.strftime('%H:%M')
        return df.groupby('slot')['volume'].mean().to_dict()
    except Exception:
        return {}

# ── Helpers ──────────────────────────────────────────────────────────────────

def get_session(t: _dt.time) -> str:
    if t < _dt.time(9, 30):   return 'GLOBEX'
    if t < NY_OPEN_END:       return 'NY_OPEN'
    if t < MIDDAY_END:        return 'MIDDAY'
    if t < LUNCH_END:         return 'LUNCH'
    if t < AFTERNOON_END:     return 'AFTERNOON'
    return 'EOD'


def is_entry_allowed(t: _dt.time) -> bool:
    # Mirror live: no new entries at/after 14:00 ET
    if t >= ENTRY_CUTOFF:
        return False
    return get_session(t) in ('NY_OPEN', 'MIDDAY', 'AFTERNOON')


def compute_vwap(df: pd.DataFrame) -> float | None:
    """VWAP from today's bars (matches live calc_vwap which filters to today)."""
    if df.empty or len(df) < 2:
        return None
    tp  = (df['high'] + df['low'] + df['close']) / 3
    vol = df['volume'].cumsum()
    if vol.iloc[-1] == 0:
        return None
    return float((tp * df['volume']).cumsum().iloc[-1] / vol.iloc[-1])


def compute_rsi(close: pd.Series, period: int = 14) -> float:
    """Matches live calc_rsi exactly (replace 0 with 1e-9, not NaN)."""
    if len(close) < period + 1:
        return 50.0
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, 1e-9)
    v     = (100 - (100 / (1 + rs))).iloc[-1]
    return round(float(v), 1) if pd.notna(v) else 50.0


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """ATR from df. Fallback 10.0 matches live calc_atr."""
    if df.empty or len(df) < 2:
        return 10.0
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low']  - df['close'].shift()).abs(),
    ], axis=1).max(axis=1)
    v = tr.rolling(period).mean().iloc[-1]
    return float(v) if pd.notna(v) else 10.0


def calc_sl_target(price: float, atr: float, side: str) -> tuple[float, float]:
    def rt(v):
        return round(round(v / TICK_SIZE) * TICK_SIZE, 2)
    raw_sl  = atr * STOP_ATR_MULT
    raw_tgt = atr * TARGET_ATR_MULT
    if side == 'LONG':
        return rt(price - raw_sl), rt(price + raw_tgt)
    else:
        return rt(price + raw_sl), rt(price - raw_tgt)


def calc_contracts(price: float, sl: float) -> int:
    stop_pts   = abs(price - sl)
    stop_ticks = stop_pts / TICK_SIZE
    risk_per_c = stop_ticks * TICK_VALUE
    if risk_per_c <= 0:
        return 1
    return max(1, min(int(MAX_RISK_PER_TRADE / risk_per_c), 2))


def pnl_dollars(entry: float, exit_p: float, side: str, contracts: int) -> float:
    pts = (exit_p - entry) if side == 'LONG' else (entry - exit_p)
    return round(pts * POINT_VALUE * contracts - COMMISSION_RT, 2)


# ── Signal detection ─────────────────────────────────────────────────────────

def get_signals(bars_today: pd.DataFrame, bars_hist: pd.DataFrame,
                orb_high: float, orb_low: float, orb_set: bool,
                pm_high: float, pm_low: float, pm_set: bool) -> dict:
    """
    Generate entry signals. Matches live get_signals() exactly.
    bars_today = today's RTH bars so far (for VWAP, open_play)
    bars_hist  = 2-day history (for RSI)
    """
    if len(bars_today) < 4:
        return {}

    price   = float(bars_today['close'].iloc[-1])
    vwap    = compute_vwap(bars_today)
    rsi     = compute_rsi(bars_hist['close'])
    t       = bars_today.index[-1].time()
    session = get_session(t)

    if not vwap:
        return {}

    sig = {
        'price':   price,
        'vwap':    vwap,
        'rsi':     rsi,
        'session': session,
    }

    prev_bar_close = float(bars_today['close'].iloc[-2])

    # ── Bull signals ──────────────────────────────────────────────────────────

    sig['orb_bull']       = orb_set and price > orb_high and session == 'NY_OPEN'
    sig['vwap_reclaim']   = prev_bar_close < vwap and price > vwap
    if len(bars_today) >= 4:
        last3 = bars_today['close'].iloc[-4:-1]
        sig['momentum_bull'] = (
            all(last3.iloc[i] < last3.iloc[i+1] for i in range(2)) and price > vwap
        )
    else:
        sig['momentum_bull'] = False

    # Session open play: price moved away from first RTH close in direction of VWAP
    if len(bars_today) >= 2:
        open_bar = float(bars_today['close'].iloc[0])
        sig['open_play_bull'] = price > open_bar and price > vwap
        sig['open_play_bear'] = price < open_bar and price < vwap
    else:
        sig['open_play_bull'] = sig['open_play_bear'] = False

    # PM IB break
    sig['pm_bull'] = pm_set and pm_high > 0 and price > pm_high
    sig['pm_bear'] = pm_set and pm_low  > 0 and price < pm_low

    # ── Bear signals ──────────────────────────────────────────────────────────

    sig['orb_bear']       = orb_set and price < orb_low and session == 'NY_OPEN'
    sig['vwap_rejection'] = prev_bar_close > vwap and price < vwap
    if len(bars_today) >= 4:
        last3 = bars_today['close'].iloc[-4:-1]
        sig['momentum_bear'] = (
            all(last3.iloc[i] > last3.iloc[i+1] for i in range(2)) and price < vwap
        )
    else:
        sig['momentum_bear'] = False

    return sig


# ── Regime detection ─────────────────────────────────────────────────────────

def get_regime(bars_today: pd.DataFrame, bars_hist: pd.DataFrame,
               prev_close: float) -> str:
    """
    Matches live get_regime() exactly.
    bars_today = today's RTH bars (for trend, choppiness, VWAP)
    bars_hist  = 2-day history (for RSI)
    prev_close = yesterday's last close (for day_chg_pct)
    """
    if len(bars_today) < 3:
        return 'NORMAL'

    price = float(bars_today['close'].iloc[-1])
    vwap  = compute_vwap(bars_today)
    rsi   = compute_rsi(bars_hist['close'])

    above_vwap = price > vwap if vwap else True

    # Short-term trend: last 3 bars of today
    trend = bars_today['close'].iloc[-3:]
    trending_up   = float(trend.iloc[-1]) > float(trend.iloc[0])
    trending_down = float(trend.iloc[-1]) < float(trend.iloc[0])

    # Day change vs YESTERDAY's close (matches live — not vs first RTH bar)
    day_chg_pct = (price - prev_close) / prev_close * 100 if prev_close else 0

    # Choppiness (only meaningful with >= 6 bars today)
    choppy = False
    if len(bars_today) >= 6:
        diffs = bars_today['close'].diff().dropna()
        flips = sum(1 for i in range(1, len(diffs)) if diffs.iloc[i] * diffs.iloc[i-1] < 0)
        choppy = (flips / max(len(diffs), 1)) > 0.4 and abs(day_chg_pct) < 0.2

    if choppy:
        return 'NORMAL'
    if above_vwap and trending_up and day_chg_pct > 0.3 and rsi < 80:
        return 'STRONG'
    if not above_vwap and trending_down and day_chg_pct < -0.3 and rsi > 20:
        return 'WEAK'
    return 'NORMAL'


# ── Entry scoring ─────────────────────────────────────────────────────────────

def grade_entry(sig: dict, regime: str, side: str) -> tuple[int, str]:
    """Matches live grade_entry() exactly, including RSI gates."""
    if not sig:
        return 0, 'SKIP'

    score   = 50
    session = sig.get('session', 'NY_OPEN')
    rsi     = sig.get('rsi', 50)

    score += {'NY_OPEN': +15, 'MIDDAY': +5, 'AFTERNOON': 0, 'LUNCH': -20}.get(session, 0)

    if side == 'LONG':
        if regime == 'STRONG':  score += 15
        elif regime == 'WEAK':  score -= 30
        if regime == 'WEAK':    return 0, 'SKIP'

        bull_sigs = [sig.get('orb_bull'), sig.get('vwap_reclaim'),
                     sig.get('momentum_bull'), sig.get('open_play_bull'),
                     sig.get('pm_bull')]
        if not any(bull_sigs):
            return 0, 'SKIP'

        if sig.get('orb_bull'):         score += 20
        if sig.get('vwap_reclaim'):     score += 15
        if sig.get('momentum_bull'):    score += 10
        if sig.get('open_play_bull'):   score += 10
        if sig.get('pm_bull'):          score += 25

        # RSI gates — match live
        if rsi > 80:  return 0, 'SKIP'
        if rsi > 70:  score -= 10
        if rsi < 45:  score += 5

    else:  # SHORT
        if regime == 'WEAK':    score += 15
        elif regime == 'STRONG': score -= 30
        if regime == 'STRONG':  return 0, 'SKIP'

        bear_sigs = [sig.get('orb_bear'), sig.get('vwap_rejection'),
                     sig.get('momentum_bear'), sig.get('open_play_bear'),
                     sig.get('pm_bear')]
        if not any(bear_sigs):
            return 0, 'SKIP'

        if sig.get('orb_bear'):         score += 20
        if sig.get('vwap_rejection'):   score += 15
        if sig.get('momentum_bear'):    score += 10
        if sig.get('open_play_bear'):   score += 10
        if sig.get('pm_bear'):          score += 25

        # RSI gates — match live
        if rsi < 20:  return 0, 'SKIP'
        if rsi < 30:  score -= 10
        if rsi > 55:  score += 5

    if score >= 80:  return score, 'A+'
    if score >= 65:  return score, 'A'
    if score >= 50:  return score, 'B'
    return score, 'SKIP'


def setup_name(sig: dict, side: str) -> str:
    if side == 'LONG':
        if sig.get('pm_bull'):          return 'PM_LONG'
        if sig.get('orb_bull'):         return 'ORB_LONG'
        if sig.get('vwap_reclaim'):     return 'VWAP_LONG'
        if sig.get('momentum_bull'):    return 'MOM_LONG'
        if sig.get('open_play_bull'):   return 'OPEN_LONG'
    else:
        if sig.get('pm_bear'):          return 'PM_SHORT'
        if sig.get('orb_bear'):         return 'ORB_SHORT'
        if sig.get('vwap_rejection'):   return 'VWAP_SHORT'
        if sig.get('momentum_bear'):    return 'MOM_SHORT'
        if sig.get('open_play_bear'):   return 'OPEN_SHORT'
    return 'SIGNAL'


# ── Overnight bias (mirrors futures_trader.py compute_overnight_bias) ────────

def compute_overnight_bias_5m(all_bars: pd.DataFrame, trade_date: _dt.date) -> tuple[str, bool, float]:
    """
    Replicate live compute_overnight_bias() using 5-min bars.
    Returns (bias, skip_day, ovn_pos).
    """
    prev_day = trade_date - _dt.timedelta(days=1)
    while prev_day.weekday() >= 5 or prev_day in US_HOLIDAYS:
        prev_day -= _dt.timedelta(days=1)

    prev_4pm  = ET.localize(_dt.datetime(prev_day.year, prev_day.month, prev_day.day, 16, 0))
    today_930 = ET.localize(_dt.datetime(trade_date.year, trade_date.month, trade_date.day, 9, 30))

    night_bars = all_bars[(all_bars.index >= prev_4pm) & (all_bars.index < today_930)]

    if len(night_bars) < 4:
        return 'BOTH', False, -1.0

    overnight_high  = float(night_bars['high'].max())
    overnight_low   = float(night_bars['low'].min())
    overnight_range = overnight_high - overnight_low

    if overnight_range < _OVN_COMPRESS:
        return 'BOTH', True, -1.0

    rth_mask  = (all_bars.index.date == trade_date) & (all_bars.index.time >= _dt.time(9, 30))
    rth_today = all_bars[rth_mask]
    if rth_today.empty:
        return 'BOTH', False, -1.0

    rth_open = float(rth_today['open'].iloc[0])
    pos      = max(0.0, min(1.0, (rth_open - overnight_low) / overnight_range))

    if _OVN_SKIP_LO <= pos < _OVN_SKIP_HI:
        return 'BOTH', True, round(pos, 3)
    elif pos >= _OVN_TREND_HI:
        return 'LONG', False, round(pos, 3)
    elif pos <= _OVN_TREND_LO:
        return 'SHORT', False, round(pos, 3)
    else:
        return 'BOTH', False, round(pos, 3)


# ── Day simulation ───────────────────────────────────────────────────────────

def simulate_day(
    all_bars:            pd.DataFrame,
    trade_date:          _dt.date,
    gate1:               bool = False,   # Bias-regime conflict: SHORT bias + NORMAL/STRONG RTH → treat as BOTH
    gate2:               bool = False,   # Opening volatility: 09:30 bar range >120pts → skip first 2 scans
    daily_closes:        'pd.Series | None' = None,  # precomputed daily close series for 50-day MA
    large_ib_gate_pts:   float = 0.0,   # IB range gate: if IB range > this at 10:30 → delay entry to 10:45
    early_ib_pts:        float = 0.0,   # Early entry gate: if IB range >= this by 10:00, allow entry at 10:00
                                        # (0=disabled). Early TRENDING only — late TRENDING still gets 10:45 delay.
) -> tuple[list[dict], str, float]:
    """Simulate one trading day. Returns (trades, daily_bias, ovn_pos)."""
    date_str = trade_date.isoformat()

    # Skip US market holidays (mirrors live run_scan() US_HOLIDAYS_2026 check)
    if trade_date in US_HOLIDAYS_2026:
        return [], 'HOLIDAY', -1.0

    # Overnight bias (mirrors live _daily_macro_bias)
    daily_bias, skip_day, ovn_pos = compute_overnight_bias_5m(all_bars, trade_date)
    if skip_day:
        return [], daily_bias, ovn_pos

    # OVN_POS Option A: overnight closed near its low → exhausted bears → allow LONGs.
    # Even on a SHORT bias day, ovn_pos ≤ 0.13 means overnight sellers ran out of fuel.
    # Pre-market was already showing bullish structure (26/27 new trades were PM_LONG).
    # Data (2025-2026 IS): ovn_pos 0–0.08 → 66.7% WR +$64 avg; 0.08–0.13 → 57.1% WR +$63 avg.
    # Net +$773 (27 trades). The 0.14–0.21 bucket is 50/50 with ~$0 avg — not worth including.
    if daily_bias == 'SHORT' and 0.0 <= ovn_pos <= 0.13:
        daily_bias = 'BOTH'

    # Yesterday's last RTH close (for day_chg_pct in regime detection)
    # Robust: skip weekends, known holidays, AND any day with no actual RTH bars
    # (catches early-close days and gaps in data for multi-year backtest)
    prev_day = trade_date - _dt.timedelta(days=1)
    while prev_day.weekday() >= 5 or prev_day in US_HOLIDAYS:
        prev_day -= _dt.timedelta(days=1)
    prev_rth = filter_ny_session(all_bars[all_bars.index.date == prev_day])
    lookback = 0
    while prev_rth.empty and lookback < 7:
        prev_day -= _dt.timedelta(days=1)
        while prev_day.weekday() >= 5 or prev_day in US_HOLIDAYS:
            prev_day -= _dt.timedelta(days=1)
        prev_rth = filter_ny_session(all_bars[all_bars.index.date == prev_day])
        lookback += 1
    prev_close = float(prev_rth['close'].iloc[-1]) if not prev_rth.empty else 0.0

    # Gate1 macro guard: is MNQ in a macro uptrend? (prev_close >= 50-day daily MA)
    # Only override overnight SHORT bias when the market is structurally bullish.
    # In bear markets, SHORT bias + intraday bounce = trap. Don't override.
    if gate1 and daily_closes is not None:
        closes_before = daily_closes[daily_closes.index < trade_date]
        if len(closes_before) >= 20:
            ma50 = float(closes_before.tail(50).mean())
            gate1_macro_ok = prev_close >= ma50   # uptrend confirmed
        else:
            gate1_macro_ok = True   # insufficient history — allow gate
    else:
        gate1_macro_ok = True   # no guard when daily_closes not provided

    # Today's full day bars (ET)
    day_bars = all_bars[all_bars.index.date == trade_date]
    if day_bars.empty:
        return [], daily_bias, ovn_pos

    # PM IB: 8:30–9:29 ET
    pm_bars = filter_premarket_session(day_bars)
    pm_set  = len(pm_bars) >= 2
    pm_high = float(pm_bars['high'].max()) if pm_set else 0.0
    pm_low  = float(pm_bars['low'].min())  if pm_set else 0.0

    # RTH: 9:30–15:10 ET
    rth = filter_ny_session(day_bars)
    if len(rth) < 14:
        return [], daily_bias, ovn_pos

    # ORB: first 3 RTH bars (9:30–9:44)
    orb_bars = rth[rth.index.time < _dt.time(9, 45)]
    orb_set  = len(orb_bars) >= 3
    orb_high = float(orb_bars['high'].max()) if orb_set else 0.0
    orb_low  = float(orb_bars['low'].min())  if orb_set else 0.0

    # ORB direction — used as BOTH-mode confirmation gate for PM signals
    if orb_set:
        orb_dir = 'UP' if float(orb_bars['close'].iloc[-1]) >= float(orb_bars['open'].iloc[0]) else 'DOWN'
    else:
        orb_dir = 'BOTH'

    # Gate 2: Opening volatility — if 09:30 bar range > 120pts, extend IB window 10:30 → 10:45
    # Rationale: extreme opening bar = chaotic price discovery; even after IB forms at 10:30,
    # the first entry scan on a wild-open day is still noisy. Wait one extra scan (15 min).
    # Mirrors what live would do: IB_READY_TIME is 10:30 normally, 10:45 on extreme opens.
    if len(rth) > 0:
        first_bar = rth.iloc[0]
        opening_range = float(first_bar['high']) - float(first_bar['low'])
    else:
        opening_range = 0.0

    if gate2 and opening_range > 120.0:
        ib_ready_time = _dt.time(10, 45)   # extend by one scan on extreme-open days
    else:
        ib_ready_time = IB_READY_TIME       # standard 10:30 IB window

    # Early TRENDING gate: if IB range already ≥ early_ib_pts by 10:00, advance entry to 10:00.
    # Data: 65% of TRENDING days show ≥200pts by 10:00 with 0% false positives (Jun 17 research).
    # Only fires when range is confirmed BEFORE 10:30 — late TRENDING days (200pts only at 10:30)
    # still get the large_ib_gate_pts delay to 10:45 (different data point, different problem).
    if early_ib_pts > 0:
        bars_at_1000 = rth[rth.index.time <= _dt.time(10, 0)]
        if len(bars_at_1000) >= 2:
            range_at_1000 = float(bars_at_1000['high'].max()) - float(bars_at_1000['low'].min())
            if range_at_1000 >= early_ib_pts:
                ib_ready_time = _dt.time(10, 0)

    trades:         list[dict] = []
    position:       dict | None = None
    trade_count     = 0
    daily_pnl       = 0.0
    bias_overridden = False  # Gate1 sticky: once RTH invalidates overnight bias, stays overridden

    # Track consecutive regime scans — SHORT requires WEAK x3 (mirrors live _confirmed_scans)
    last_regime    = 'NORMAL'
    consec_count   = 0   # how many consecutive scans in `last_regime`

    # Phase 5: day regime detected once at IB formation (10:30am)
    day_regime: str | None = None

    # IB directional classification — computed once at IB gate
    # BEAR_DIRECTIONAL (ib_mid<0.25) unlocks SHORT without requiring WEAK×3
    ib_kind: str | None = None

    # Large IB gate: when IB range > large_ib_gate_pts, delay first entry to 10:45
    # Prevents entering right into the tail of IB volatility
    large_ib_delayed: bool = False

    for i in range(3, len(rth)):
        bar         = rth.iloc[i]
        t           = rth.index[i].time()
        bars_today  = rth.iloc[:i+1]           # today's RTH bars for VWAP/signals/regime

        # 2-day history ending at current bar (for ATR, RSI — matches live 2-day bar window)
        current_ts = rth.index[i]
        bars_hist  = all_bars[all_bars.index <= current_ts].iloc[-100:]

        # ── Manage open position ──────────────────────────────────────────────
        if position:
            entry     = position['entry']
            sl        = position['sl']          # SL from previous bar's trail
            target    = position['target']
            side      = position['side']
            is_short  = (side == 'SHORT')
            contracts = position['contracts']
            peak      = position['peak']
            entry_idx = position['entry_idx']

            exit_price  = None
            exit_reason = None

            # 1. Hard stop (SL from PREVIOUS bar — no same-bar trail race)
            if not is_short and float(bar['low']) <= sl:
                exit_price, exit_reason = sl, 'stop'
            elif is_short and float(bar['high']) >= sl:
                exit_price, exit_reason = sl, 'stop'

            # 2. Daily circuit breaker (realized + unrealized, checked before target)
            if not exit_reason:
                cur_pnl_pts = (entry - float(bar['close'])) if is_short else (float(bar['close']) - entry)
                cur_usd = cur_pnl_pts * POINT_VALUE * contracts - COMMISSION_RT
                if daily_pnl + cur_usd <= -MAX_DAILY_LOSS:
                    exit_price, exit_reason = float(bar['close']), 'dll_circuit'

            # 3. Target hit
            if not exit_reason:
                if not is_short and float(bar['high']) >= target:
                    exit_price, exit_reason = target, 'target'
                elif is_short and float(bar['low']) <= target:
                    exit_price, exit_reason = target, 'target'

            # 4. VWAP cross (only exit if profitable)
            if not exit_reason:
                vwap_now    = compute_vwap(bars_today)
                cur_pnl_pts = (entry - float(bar['close'])) if is_short else (float(bar['close']) - entry)
                if vwap_now and cur_pnl_pts > 0:
                    if not is_short and float(bar['close']) < vwap_now:
                        exit_price, exit_reason = float(bar['close']), 'vwap_cross'
                    elif is_short and float(bar['close']) > vwap_now:
                        exit_price, exit_reason = float(bar['close']), 'vwap_cross'

            # 5. No-move exit (90 min stuck in dead zone)
            if not exit_reason:
                mins_open   = (i - entry_idx) * 5
                cur_pnl_pts = (entry - float(bar['close'])) if is_short else (float(bar['close']) - entry)
                if (mins_open >= NO_MOVE_MINUTES and
                        NO_MOVE_MIN_PTS <= cur_pnl_pts <= NO_MOVE_MAX_PTS):
                    exit_price, exit_reason = float(bar['close']), 'no_move'

            # 6. EOD
            if not exit_reason and t >= EOD_CLOSE:
                exit_price, exit_reason = float(bar['close']), 'eod'

            # Update trail AFTER exit checks (applies to NEXT bar — prevents same-bar race)
            if exit_price is None:
                if is_short:
                    peak = min(peak, float(bar['low']))
                else:
                    peak = max(peak, float(bar['high']))
                position['peak'] = peak

                # Trail thresholds use PEAK P&L (live uses current pnl but tracks s_peak)
                pnl_pts_peak = (entry - peak) if is_short else (peak - entry)

                if pnl_pts_peak >= BE_ACTIVATE_PTS:
                    be_sl = round(entry + TICK_SIZE, 2) if not is_short else round(entry - TICK_SIZE, 2)
                    if (not is_short and be_sl > sl) or (is_short and be_sl < sl):
                        sl = be_sl

                if pnl_pts_peak >= TRAIL_WIDE_PTS:
                    w_sl = round(peak - TRAIL_WIDE_GAP, 2) if not is_short else round(peak + TRAIL_WIDE_GAP, 2)
                    if (not is_short and w_sl > sl) or (is_short and w_sl < sl):
                        sl = w_sl

                if pnl_pts_peak >= TRAIL_TIGHT_PTS:
                    t_sl = round(peak - TRAIL_TIGHT_GAP, 2) if not is_short else round(peak + TRAIL_TIGHT_GAP, 2)
                    if (not is_short and t_sl > sl) or (is_short and t_sl < sl):
                        sl = t_sl

                position['sl'] = sl

            if exit_price is not None:
                net = pnl_dollars(entry, exit_price, side, contracts)
                daily_pnl += net
                trades.append({
                    'date':        date_str,
                    'entry_time':  position['entry_time'],
                    'exit_time':   t.strftime('%H:%M'),
                    'side':        side,
                    'setup':       position['setup'],
                    'entry':       entry,
                    'exit':        exit_price,
                    'stop_init':   position['stop_init'],
                    'target':      position['target'],
                    'contracts':   contracts,
                    'pnl':         net,
                    'exit_reason': exit_reason,
                    'grade':       position['grade'],
                })
                position = None
                continue

        # ── Look for new entry ────────────────────────────────────────────────
        if position is None and trade_count < MAX_DAILY_TRADES and is_entry_allowed(t):
            # IB window gate — no entries before 10:30 ET (mirrors live); Gate2 extends to 10:45
            if t < ib_ready_time:
                continue

            sig    = get_signals(bars_today, bars_hist, orb_high, orb_low, orb_set,
                                 pm_high, pm_low, pm_set)
            regime = get_regime(bars_today, bars_hist, prev_close)

            # Track consecutive regime scans (mirrors live _confirmed_scans)
            if regime == last_regime:
                consec_count += 1
            else:
                consec_count = 1
                last_regime  = regime
            confirmed_scans = consec_count

            # IB range gate: H-L from 9:30 to now must be >= 50pts (mirrors live)
            ib_range = float(bars_today['high'].max() - bars_today['low'].min()) if len(bars_today) >= 2 else 0.0
            if 0 < ib_range < MIN_IB_RANGE:
                continue

            # Phase 5: detect day regime once at IB formation (first bar past IB gate)
            if day_regime is None:
                day_regime = detect_regime(ib_range)

                # IB directional classification (classify once at IB formation)
                ib_hi = float(bars_today['high'].max())
                ib_lo = float(bars_today['low'].min())
                ib_cl = float(bars_today['close'].iloc[-1])
                ib_rng = ib_hi - ib_lo
                if ib_rng > 0:
                    ib_mid = (ib_cl - ib_lo) / ib_rng
                    if ib_mid < 0.25:
                        ib_kind = 'BEAR_DIRECTIONAL'
                    elif ib_mid > 0.75:
                        ib_kind = 'BULL_DIRECTIONAL'
                    else:
                        ib_kind = 'ROTATIONAL'

                # Large IB gate: IB range > threshold at 10:30 → delay first entry to 10:45
                if large_ib_gate_pts > 0 and ib_range > large_ib_gate_pts:
                    large_ib_delayed = True

            # Large IB delay: skip scans until 10:45 (one extra scan to let volatility settle)
            if large_ib_delayed:
                if t < _dt.time(10, 45):
                    continue
                large_ib_delayed = False

            # Gate 1: Bias-regime conflict (sticky) — overnight says SHORT but RTH clearly bullish.
            # Fires once when: price >0.3% above prev_close + NORMAL/STRONG for 2+ scans.
            # Once fired, stays for rest of day (prevents on-bar flip that adds bad trades).
            # 0.3% threshold: separates genuine reversals (May 14 +0.34-0.48%) from noise
            # (Jun 1 peak was only +0.23% — never triggered, correct SHORT day preserved).
            effective_bias = daily_bias
            if gate1 and daily_bias == 'SHORT':
                if not bias_overridden and gate1_macro_ok:
                    # Only override overnight SHORT bias when:
                    # 1. MNQ is above its 50-day MA (macro uptrend — not a bear bounce)
                    # 2. RTH opened bullish: price >0.3% above prev_close in NORMAL/STRONG
                    pct_above = (float(bar['close']) - prev_close) / prev_close if prev_close else 0.0
                    if (regime in ('NORMAL', 'STRONG') and
                            confirmed_scans >= 1 and
                            pct_above > 0.003):
                        bias_overridden = True
                if bias_overridden:
                    effective_bias = 'BOTH'

            # SHORT allowed: WEAK regime confirmed for >=3 consecutive scans (mirrors live)
            # Exception 1: daily_bias=='SHORT' (overnight classifier already set direction)
            # Exception 2: IB closed near its low (BEAR_DIRECTIONAL) — day structure is bearish
            #   even if day_chg_pct hasn't gone negative yet (market opened up, then crashed).
            #   Hero gate still applies — only A/A+ setups pass through.
            short_allowed = (
                (regime == 'WEAK' and confirmed_scans >= 3) or
                (effective_bias == 'SHORT') or
                (ib_kind == 'BEAR_DIRECTIONAL')
            )

            for side in ('LONG', 'SHORT'):
                # Overnight macro bias gate (mirrors live _daily_macro_bias)
                if effective_bias == 'LONG'  and side == 'SHORT': continue
                if effective_bias == 'SHORT' and side == 'LONG' and not ALLOW_LONG_ON_SHORT_BIAS: continue

                # SHORT regime gate — mirrors live: short_allowed = (WEAK+3scans) or bias=SHORT
                if side == 'SHORT' and not short_allowed:
                    continue

                # LONG gate: regime must be STRONG or NORMAL (mirrors live)
                if side == 'LONG' and regime == 'WEAK':
                    continue

                score, grade = grade_entry(sig, regime, side)
                if grade not in ('A', 'A+'):
                    continue

                # BOTH days: PM signals must align with ORB direction
                # Exception: IB formation overrides ORB (IB=60min vs ORB=15min).
                # BEAR_DIRECTIONAL IB (mid<0.25) lets PM_SHORT through even on UP ORB.
                # BULL_DIRECTIONAL IB lets PM_LONG through even on DOWN ORB.
                setup = setup_name(sig, side)
                if daily_bias == 'BOTH' and setup.startswith('PM'):
                    if side == 'LONG'  and orb_dir != 'UP'   and ib_kind != 'BULL_DIRECTIONAL': continue
                    if side == 'SHORT' and orb_dir != 'DOWN' and ib_kind != 'BEAR_DIRECTIONAL': continue

                price      = float(bar['close'])
                atr        = compute_atr(bars_hist)   # 2-day ATR (matches live)
                sl, target = calc_sl_target(price, atr, side)

                rr = abs(target - price) / abs(price - sl) if abs(price - sl) > 0 else 0
                if rr < MIN_RR - 0.01:
                    continue

                # Max stop gate: skip when ATR stop > 150pts (mirrors live max_stop_pts=150)
                stop_pts = abs(price - sl)
                if stop_pts > 150.0:
                    continue

                # Hero quality gate — Phase 5 regime-aware scoring + H5 FIB boost
                cc = calc_contracts(price, sl)
                if HERO_GATE_ENABLED:
                    bars_up  = all_bars[all_bars.index <= current_ts]
                    regime   = day_regime or 'CHOPPY'   # fallback if IB data missing
                    h_score, h_flags = score_entry_regime(price, atr, side, bars_up, prev_rth, regime)
                    h5_fib   = h_flags.get('H5_FIB_FLOOR', False)
                    contracts = contracts_from_regime_score(h_score, regime, cc, h5_fib=h5_fib)
                    if contracts == 0:
                        continue   # below quality threshold — skip this setup
                else:
                    contracts = cc

                position  = {
                    'entry':      price,
                    'sl':         sl,
                    'stop_init':  sl,
                    'target':     target,
                    'side':       side,
                    'contracts':  contracts,
                    'entry_time': t.strftime('%H:%M'),
                    'entry_idx':  i,
                    'peak':       price,
                    'setup':      setup,
                    'grade':      grade,
                }
                trade_count += 1
                break

    # Force-close any open position at EOD
    if position:
        last = rth.iloc[-1]
        ep   = float(last['close'])
        net  = pnl_dollars(position['entry'], ep, position['side'], position['contracts'])
        daily_pnl += net
        trades.append({
            'date':        date_str,
            'entry_time':  position['entry_time'],
            'exit_time':   rth.index[-1].time().strftime('%H:%M'),
            'side':        position['side'],
            'setup':       position['setup'],
            'entry':       position['entry'],
            'exit':        ep,
            'stop_init':   position['stop_init'],
            'target':      position['target'],
            'contracts':   position['contracts'],
            'pnl':         net,
            'exit_reason': 'eod_force',
            'grade':       position['grade'],
        })

    return trades, daily_bias, ovn_pos


# ── Main ─────────────────────────────────────────────────────────────────────

def _precompute_daily_closes(all_bars: pd.DataFrame) -> 'pd.Series':
    """Daily RTH close series (date → close). Used for 50-day MA in Gate1 macro guard."""
    rth = filter_ny_session(all_bars)
    return rth.groupby(rth.index.date)['close'].last().sort_index()


def _run_scenario(
    all_bars:          pd.DataFrame,
    trade_dates:       list,
    gate1:             bool,
    gate2:             bool,
    label:             str,
    verbose:           bool = True,
    large_ib_gate_pts: float = 200.0,
    early_ib_pts:      float = 200.0,
) -> dict:
    """Run one scenario and return summary stats."""
    # Precompute daily closes once for Gate1 macro guard (50-day MA)
    daily_closes = _precompute_daily_closes(all_bars) if gate1 else None

    all_trades: list[dict] = []
    for trade_date in trade_dates:
        trades, bias, pos = simulate_day(all_bars, trade_date, gate1=gate1, gate2=gate2,
                                         daily_closes=daily_closes,
                                         large_ib_gate_pts=large_ib_gate_pts,
                                         early_ib_pts=early_ib_pts)
        all_trades.extend(trades)
        if verbose:
            bias_tag = f'[{bias}' + (f' {pos:.2f}]' if pos >= 0 else ' —]')
            if trades:
                day_pnl = sum(t['pnl'] for t in trades)
                wins    = sum(1 for t in trades if t['pnl'] > 0)
                parts   = [f"{t['setup']}({'+'if t['pnl']>0 else''}{t['pnl']:.0f})" for t in trades]
                print(f'  {trade_date} {bias_tag:>14}  {len(trades)}t  {wins}W  ${day_pnl:+.2f}   {" | ".join(parts)}')
            else:
                print(f'  {trade_date} {bias_tag:>14}  — no trades')

    if not all_trades:
        return {'label': label, 'n': 0, 'wins': 0, 'wr': 0.0, 'total': 0.0, 'avg': 0.0, 'dd': 0.0, 'df': None}

    df    = pd.DataFrame(all_trades)
    total = df['pnl'].sum()
    wins  = int((df['pnl'] > 0).sum())
    wr    = wins / len(df) * 100
    cum   = df['pnl'].cumsum()
    dd    = float((cum - cum.cummax()).min())
    return {'label': label, 'n': len(df), 'wins': wins, 'wr': wr,
            'total': total, 'avg': total/len(df), 'dd': dd, 'df': df}


def _print_scenario(r: dict) -> None:
    if r['n'] == 0:
        print('  No trades generated.')
        return
    df = r['df']
    print(f'{"─"*60}')
    print(f'  Trades: {r["n"]}  |  WR: {r["wins"]}/{r["n"]} = {r["wr"]:.1f}%')
    print(f'  Total P&L: ${r["total"]:+.2f}  |  Avg/trade: ${r["avg"]:+.2f}  |  MaxDD: ${r["dd"]:.2f}')
    print()
    print('  By setup:')
    for stype, grp in df.groupby('setup'):
        sw = (grp['pnl'] > 0).sum()
        print(f'    {stype:<12}  {len(grp)}t  {sw}/{len(grp)}W  ${grp["pnl"].sum():+.2f}')
    print()
    print('  By side:')
    for side, grp in df.groupby('side'):
        sw = (grp['pnl'] > 0).sum()
        print(f'    {side:<6}  {len(grp)}t  {sw}/{len(grp)}W  ${grp["pnl"].sum():+.2f}')
    print()
    print('  Individual trades:')
    print(f'  {"Date":<12}{"In":>6}{"Side":>6}{"Setup":<13}{"G":>3}{"Entry":>9}{"Exit":>9}{"P&L":>9}  Reason')
    for _, t in df.iterrows():
        print(f'  {t["date"]:<12}{t["entry_time"]:>6}{t["side"]:>6} {t["setup"]:<12}'
              f'{t["grade"]:>3}{t["entry"]:>9.2f}{t["exit"]:>9.2f}'
              f'${t["pnl"]:>+8.2f}  {t["exit_reason"]}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start',      default='2026-05-01')
    ap.add_argument('--end',        default='2026-06-16')
    ap.add_argument('--gate1',      action='store_true', help='Bias-regime conflict gate (+50-day MA guard)')
    ap.add_argument('--gate2',      action='store_true', help='Opening volatility gate (>120pt → skip 2 scans)')
    ap.add_argument('--all',        action='store_true', help='Run all 4 gate combinations and compare')
    ap.add_argument('--no-hero-gate', action='store_true', dest='no_hero_gate',
                    help='Disable Phase 5 hero gate (for baseline comparison)')
    ap.add_argument('--detail',     action='store_true', help='Print per-day detail in --all mode')
    ap.add_argument('--monthly',    action='store_true', help='Show monthly breakdown for all scenarios')
    ap.add_argument('--stop-mult',   type=float, default=None, help='Override STOP_ATR_MULT (default 1.5)')
    ap.add_argument('--target-mult', type=float, default=None, help='Override TARGET_ATR_MULT (default 3.0)')
    ap.add_argument('--dll',         type=float, default=None, help='Override MAX_DAILY_LOSS DLL (default 250)')
    ap.add_argument('--max-trades',  type=int,   default=None, help='Override MAX_DAILY_TRADES (default 2)')
    ap.add_argument('--be-pts',      type=float, default=None, help='Override BE_ACTIVATE_PTS (default 30)')
    ap.add_argument('--compare-stops', action='store_true', help='Run stop/target combos: 1.5/3.0 vs 2.0/4.0 vs 2.0/6.0')
    args = ap.parse_args()

    # Apply overrides to module-level constants so all functions pick them up
    global STOP_ATR_MULT, TARGET_ATR_MULT, MAX_DAILY_LOSS, MAX_DAILY_TRADES, BE_ACTIVATE_PTS, HERO_GATE_ENABLED
    if args.stop_mult   is not None: STOP_ATR_MULT    = args.stop_mult
    if args.target_mult is not None: TARGET_ATR_MULT  = args.target_mult
    if args.dll         is not None: MAX_DAILY_LOSS   = args.dll
    if args.max_trades  is not None: MAX_DAILY_TRADES = args.max_trades
    if args.be_pts      is not None: BE_ACTIVATE_PTS  = args.be_pts
    if args.no_hero_gate:            HERO_GATE_ENABLED = False

    print(f'\n=== Futures Strategy Replay: {args.start} → {args.end} ===')
    print(f'STOP_ATR_MULT={STOP_ATR_MULT}  TARGET_ATR_MULT={TARGET_ATR_MULT}  BE_ACTIVATE={BE_ACTIVATE_PTS}pts  MAX_TRADES={MAX_DAILY_TRADES}  DLL=${MAX_DAILY_LOSS:.0f}')
    print(f'Gate1: bias-regime conflict + 50-day MA macro guard')
    print(f'Gate2: opening volatility >120pt → extend IB window 10:30→10:45')
    print(f'Overnight bias: pos≥{_OVN_TREND_HI}→LONG | pos≤{_OVN_TREND_LO}→SHORT | [{_OVN_SKIP_LO},{_OVN_SKIP_HI})→skip\n')

    start_dt   = _dt.date.fromisoformat(args.start)
    # Load extra history for 50-day MA: need 70+ trading days before start
    load_start = (start_dt - _dt.timedelta(days=110)).isoformat()
    # Use next-day cutoff: stored ts_utc uses 'T' separator ('2026-06-15T...')
    # so '2026-06-15 23:59:59Z' (space+Z) compares as LESS than 'T', excluding today.
    end_cutoff = (_dt.date.fromisoformat(args.end) + _dt.timedelta(days=1)).isoformat()
    all_bars   = load_bars('MNQ', start=load_start, end=end_cutoff)

    rth_all     = filter_ny_session(all_bars)
    trade_dates = sorted(d for d in set(rth_all.index.date) if d >= start_dt)

    if args.monthly:
        # Monthly breakdown — baseline vs Gate1+Gate2
        import calendar
        scenarios = [
            (False, False, 'Base'),
            (True,  False, 'G1'),
            (False, True,  'G2'),
            (True,  True,  'G1+G2'),
        ]
        # Run all scenarios
        print('Running all scenarios for monthly breakdown...')
        results = {}
        for g1, g2, lbl in scenarios:
            results[lbl] = _run_scenario(all_bars, trade_dates, g1, g2, lbl, verbose=False)

        # Group by month
        months = sorted(set((d.year, d.month) for d in trade_dates))
        print(f'\n{"─"*80}')
        print(f'  MONTHLY BREAKDOWN  (Gate1 now includes 50-day MA macro guard)')
        print(f'{"─"*80}')
        print(f'  {"Month":<10} | {"Base":>8} {"WR":>5} | {"G1":>8} | {"G2":>8} | {"G1+G2":>8} {"WR":>5} | {"Best":>5}')
        print(f'  {"─"*10} | {"─"*8} {"─"*5} | {"─"*8} | {"─"*8} | {"─"*8} {"─"*5} | {"─"*5}')

        for yr, mo in months:
            def month_pnl(lbl):
                df = results[lbl]['df']
                if df is None: return 0.0, 0, 0
                m = df[df['date'].str[:7] == f'{yr:04d}-{mo:02d}']
                w = int((m['pnl'] > 0).sum())
                return float(m['pnl'].sum()), w, len(m)

            b_pnl, b_w, b_n = month_pnl('Base')
            g1_pnl, _,  _   = month_pnl('G1')
            g2_pnl, _,  _   = month_pnl('G2')
            g12_pnl, g12_w, g12_n = month_pnl('G1+G2')
            b_wr  = f'{b_w/b_n*100:.0f}%' if b_n else '─'
            g12_wr= f'{g12_w/g12_n*100:.0f}%' if g12_n else '─'

            best_delta = max(g1_pnl-b_pnl, g2_pnl-b_pnl, g12_pnl-b_pnl)
            best_lbl   = ('G1+G2' if g12_pnl-b_pnl==best_delta
                          else 'G1' if g1_pnl-b_pnl==best_delta else 'G2')
            month_name = f'{calendar.month_abbr[mo]} {yr}'
            marker     = ' ✓' if best_delta > 50 else (' ←' if best_delta < -20 else '')

            print(f'  {month_name:<10} | ${b_pnl:>+7.0f} {b_wr:>4} | ${g1_pnl:>+7.0f} | '
                  f'${g2_pnl:>+7.0f} | ${g12_pnl:>+7.0f} {g12_wr:>4} | {best_lbl:>5}{marker}')

        # Totals
        def total_pnl(lbl):
            df = results[lbl]['df']
            return float(df['pnl'].sum()) if df is not None else 0.0
        def total_wr(lbl):
            r = results[lbl]
            return f'{r["wr"]:.1f}%' if r['n'] else '─'

        print(f'  {"─"*80}')
        print(f'  {"TOTAL":<10} | ${total_pnl("Base"):>+7.0f} {total_wr("Base"):>4} | '
              f'${total_pnl("G1"):>+7.0f} | ${total_pnl("G2"):>+7.0f} | '
              f'${total_pnl("G1+G2"):>+7.0f} {total_wr("G1+G2"):>4}')
        print(f'\n  Delta G1+G2 vs Base: ${total_pnl("G1+G2")-total_pnl("Base"):>+.2f}')
        return

    if args.compare_stops:
        # Compare stop+target combos. Must co-scale target so R:R >= MIN_RR=2.0.
        # Baseline: stop=1.5×ATR, target=3.0×ATR → R:R=2.0 (current)
        # Wider:    stop=2.0×ATR, target=4.0×ATR → R:R=2.0 (same ratio, more room)
        # London:   stop=2.0×ATR, target=6.0×ATR → R:R=3.0 (London champion)
        print(f'Running stop/target comparison ({args.start} → {args.end}, {len(trade_dates)} trading days)...\n')
        # (smult, tmult, dll, max_t, label)
        stop_scenarios = [
            (1.5, 3.0, 250.0,  2, 'stop=1.5 tgt=3.0  RR=2.0  DLL=$250  (current live)'),
            (2.0, 4.0, 250.0,  2, 'stop=2.0 tgt=4.0  RR=2.0  DLL=$250  (wider, same RR)'),
            (2.0, 6.0, 250.0,  2, 'stop=2.0 tgt=6.0  RR=3.0  DLL=$250  (London-style)'),
            (2.0, 6.0, 1000.0, 5, 'stop=2.0 tgt=6.0  RR=3.0  DLL=$1000 ($5K account)'),
        ]
        stop_results = []
        for smult, tmult, dll_val, max_t, lbl in stop_scenarios:
            # global declared at top of main() — assignments here update module globals
            STOP_ATR_MULT    = smult
            TARGET_ATR_MULT  = tmult
            MAX_DAILY_LOSS   = dll_val
            MAX_DAILY_TRADES = max_t
            print(f'  Running: {lbl}...')
            r = _run_scenario(all_bars, trade_dates, False, False, lbl, verbose=False)
            stop_results.append(r)

        print(f'\n{"="*80}')
        print(f'  STOP / TARGET COMPARISON  ({args.start} → {args.end})')
        print(f'{"="*80}')
        print(f'  {"Scenario":<52} {"Trades":>6} {"WR%":>6} {"P&L":>10} {"Avg":>8} {"MaxDD":>9}')
        print(f'  {"─"*52} {"─"*6} {"─"*6} {"─"*10} {"─"*8} {"─"*9}')
        for r in stop_results:
            wr_str = f'{r["wr"]:.1f}%'
            print(f'  {r["label"]:<52} {r["n"]:>6} {wr_str:>6} ${r["total"]:>+9.2f} ${r["avg"]:>+7.2f} ${r["dd"]:>8.2f}')
        base = stop_results[0]
        print(f'\n  Delta vs baseline (1ct sim — multiply by ~2 for typical RVOL 2ct live):')
        for r in stop_results[1:]:
            dwr  = r['wr']    - base['wr']
            dpnl = r['total'] - base['total']
            dn   = r['n']     - base['n']
            print(f'    {r["label"]:<52}  WR {dwr:>+.1f}pp  P&L ${dpnl:>+.2f}  Trades {dn:>+d}')
            print(f'    {"":52}  → at 2ct: ~${r["total"]*2:>+.0f}  (vs baseline 2ct ~${base["total"]*2:>+.0f})')
        return

    if args.all:
        # Run all 4 combinations silently, then compare
        scenarios = [
            (False, False, 'Baseline     (no gates)'),
            (True,  False, 'Gate1 only   (bias-regime conflict)'),
            (False, True,  'Gate2 only   (open vol >120pt, IB→10:45)'),
            (True,  True,  'Gate1+Gate2  (both)'),
        ]
        results = []
        for g1, g2, label in scenarios:
            print(f'Running: {label}...')
            r = _run_scenario(all_bars, trade_dates, g1, g2, label, verbose=args.detail)
            results.append(r)

        print(f'\n{"="*72}')
        print(f'  GATE COMPARISON  ({args.start} → {args.end}, {len(trade_dates)} trading days)')
        print(f'{"="*72}')
        print(f'  {"Scenario":<38} {"Trades":>6} {"WR%":>6} {"P&L":>10} {"Avg":>8} {"MaxDD":>9}')
        print(f'  {"─"*38} {"─"*6} {"─"*6} {"─"*10} {"─"*8} {"─"*9}')
        for r in results:
            wr_str  = f'{r["wr"]:.1f}%'
            print(f'  {r["label"]:<38} {r["n"]:>6} {wr_str:>6} ${r["total"]:>+9.2f} ${r["avg"]:>+7.2f} ${r["dd"]:>8.2f}')

        print(f'\n  Delta vs baseline:')
        base = results[0]
        for r in results[1:]:
            dwr   = r['wr']   - base['wr']
            dpnl  = r['total']- base['total']
            dn    = r['n']    - base['n']
            print(f'    {r["label"]:<38}  WR {dwr:+.1f}pp  P&L ${dpnl:+.2f}  Trades {dn:+d}')

        # Detailed breakdown for gate1 (most impactful expected)
        print(f'\n  Gate1 deep dive — trades changed vs baseline:')
        base_df = base['df']
        g1_df   = results[1]['df']
        if base_df is not None and g1_df is not None:
            base_keys = set(zip(base_df['date'], base_df['entry_time'], base_df['side']))
            g1_keys   = set(zip(g1_df['date'],  g1_df['entry_time'],  g1_df['side']))
            removed   = base_keys - g1_keys
            added     = g1_keys - base_keys
            if removed:
                print(f'    Trades REMOVED by Gate1 (would have been blocked):')
                for date, et, side in sorted(removed):
                    row = base_df[(base_df['date']==date) & (base_df['entry_time']==et) & (base_df['side']==side)]
                    if not row.empty:
                        t = row.iloc[0]
                        print(f'      {date} {et} {side:>5} {t["setup"]:<12} ${t["pnl"]:>+8.2f}')
            if added:
                print(f'    Trades ADDED by Gate1 (newly allowed when bias downgraded to BOTH):')
                for date, et, side in sorted(added):
                    row = g1_df[(g1_df['date']==date) & (g1_df['entry_time']==et) & (g1_df['side']==side)]
                    if not row.empty:
                        t = row.iloc[0]
                        print(f'      {date} {et} {side:>5} {t["setup"]:<12} ${t["pnl"]:>+8.2f}')

        print(f'\n  Gate2 deep dive — trades changed vs baseline:')
        g2_df = results[2]['df']
        if base_df is not None and g2_df is not None:
            base_keys = set(zip(base_df['date'], base_df['entry_time'], base_df['side']))
            g2_keys   = set(zip(g2_df['date'],  g2_df['entry_time'],  g2_df['side']))
            removed   = base_keys - g2_keys
            added     = g2_keys - base_keys
            if removed:
                print(f'    Trades REMOVED by Gate2:')
                for date, et, side in sorted(removed):
                    row = base_df[(base_df['date']==date) & (base_df['entry_time']==et) & (base_df['side']==side)]
                    if not row.empty:
                        t = row.iloc[0]
                        print(f'      {date} {et} {side:>5} {t["setup"]:<12} ${t["pnl"]:>+8.2f}')
            if added:
                print(f'    Trades ADDED by Gate2 (later entry on volatile-open days):')
                for date, et, side in sorted(added):
                    row = g2_df[(g2_df['date']==date) & (g2_df['entry_time']==et) & (g2_df['side']==side)]
                    if not row.empty:
                        t = row.iloc[0]
                        print(f'      {date} {et} {side:>5} {t["setup"]:<12} ${t["pnl"]:>+8.2f}')

    else:
        # Single scenario
        g1   = args.gate1
        g2   = args.gate2
        label = ('Gate1+Gate2' if g1 and g2 else 'Gate1' if g1 else 'Gate2' if g2 else 'Baseline')
        print(f'  Mode: {label}\n')
        r = _run_scenario(all_bars, trade_dates, g1, g2, label, verbose=True)
        _print_scenario(r)
        print()
        print(f'  Actual live IBKR (Jun 10–12, 3 trading days): 5t  40.0% WR  $-119.00')
        print(f'  Actual live TC   (Jun  5–12, 5 trading days): 15t  60.0% WR  $+674.00')
        print(f'  Simulation  ({args.start} – {args.end}):  {r["n"]}t  {r["wr"]:.1f}% WR  ${r["total"]:+.2f}')


if __name__ == '__main__':
    main()
