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
                                contracts_from_regime_score, is_gold_score)

# ── Constants — must match futures_trader.py ─────────────────────────────────

POINT_VALUE    = 2.00
TICK_SIZE      = 0.25
TICK_VALUE     = 0.50
COMMISSION_RT  = 1.24

# Updated Jul 7 2026 (sim/prod sync pass) to match futures_trader.py exactly.
# Overnight bias / IB-kind / Hero gate machinery below was already correct and
# untouched — this sync only fixes regime/signal detection, entry gates, and
# the exit stack, which is what actually drifted (all rebuilt same-day).
MAX_DAILY_TRADES   = 5        # was 2 — raised Jul 7 2026, see futures_trader.py comment
MAX_DAILY_LOSS     = 3_750.0  # IBKR_DLL_SOFT — 25% of $15K capital (was $1,250/$5K)
HERO_GATE_ENABLED           = True   # Phase 5 regime-aware scoring — disable via --no-hero-gate
ALLOW_LONG_ON_SHORT_BIAS    = False  # Test flag: allow LONG entries even on SHORT ovn_pos bias
MAX_RISK_PER_TRADE = 2000.0   # matches futures_trader.py — NOTE: real live sizing goes through
                               # calc_contracts_dynamic() (RVOL/IB-range tiers), not this formula.
                               # calc_contracts() below (risk-based) is an approximation used only
                               # to feed the Hero gate's contract cap, same as it always has been.

# Point-based stop/target (was ATR-multiple STOP_ATR_MULT=1.5/TARGET_ATR_MULT=3.0
# before Jul 7 2026 morning). BASE_STOP_PTS=150 is the real loss-cutter —
# matches current live futures_trader.py (reverted Jul 7 evening; see
# USE_THESIS_INVALIDATION below for the opt-in comparison mode).
BASE_STOP_PTS    = 150.0
BASE_TARGET_PTS  = 1500.0
MIN_RR           = 1.4    # was 2.0 — trivially satisfied now target is a backstop

# Entry-quality gates added Jul 7 2026 (checked in simulate_day, not grade_entry)
ENTRY_MIN_RVOL    = 0.85
HTF_BARS_MIN      = 30
HTF_TREND_BARS    = 3

# Candidate idea (Jul 8 2026) — opt-in via --graduated-rvol. Parked in
# futures_next_session_recalibration memory after a Jul 7 near-miss (0.84 vs
# 0.85 killed a Hero-approved A+ SHORT) and reinforced Jul 8 by three more
# A+ SHORT signals (150/130/130pts) blocked at 0.73/0.68/0.72 during a
# confirmed WEAK-regime downtrend. Idea: instead of a hard RVOL<0.85 skip,
# let an entry through on RVOL between RVOL_GRAD_FLOOR and ENTRY_MIN_RVOL if
# the Hero score already clears this regime's GOLD bar on its own (contracts
# capped at 1 — thin participation still means smaller size). Below
# RVOL_GRAD_FLOOR, always skip regardless of Hero score. No effect unless
# HERO_GATE_ENABLED (nothing to compensate with otherwise).
GRADUATED_RVOL    = False
RVOL_GRAD_FLOOR   = 0.60

# Profit-lock trail tiers — these predate the Jul 7 thesis-invalidation
# experiment entirely (already live before that work started) and were NOT
# part of what got reverted. Proportional lock-in (BE_LOCK_FRACTION), not
# flat +1-tick BE.
BE_ACTIVATE_PTS  = 150.0
BE_LOCK_FRACTION = 0.35
TRAIL_WIDE_PTS   = 200.0
TRAIL_WIDE_GAP   = 120.0
TRAIL_TIGHT_PTS  = 350.0
TRAIL_TIGHT_GAP  = 60.0

NO_MOVE_MINUTES  = 90
NO_MOVE_MAX_PTS  = 60.0
NO_MOVE_MIN_PTS  = -40.0

# ── Candidate ideas (Jul 7 2026 evening) — opt-in only, untested against the
# complete pipeline before now. Named directly from the Jul 7 missed-day
# diagnosis: wave 2 (a real ~350pt rally) never got an entry because
# orb_bull never fired and score capped at 75 (A, not A+); wave 3's SHORT
# re-entry was delayed ~15min by the 3-scan WEAK confirmation requirement.
SUSTAIN_A_PLUS_BONUS   = False  # --sustain-bonus: alternate A+ path for
                                 # sustained vwap_reclaim+momentum without an
                                 # ORB break (6+ consecutive bars, +15pts)
SUSTAIN_BARS           = 6
SUSTAIN_BONUS_PTS      = 15
SHORT_CONFIRM_SCANS    = 3      # --short-confirm N: consecutive WEAK scans
                                 # required before SHORT is allowed (was 3)

# Candidate idea (Jul 8 2026 pm) — opt-in via --rsi-trend-exempt. Found by
# reading the real Jul 7 production log directly: a genuine ~270pt/50min
# grinding rally (13:11-13:59, RSI 76-81, price consistently 85-95pts above
# VWAP) fired a LONG signal on every single scan but never scored above 70
# (A, not A+) — the RSI>70 -10pt penalty was exactly the gap between 70 and
# the 80 A+ threshold (50 baseline + 5 MIDDAY + 15 vwap_reclaim + 10
# momentum_bull = 80, minus the RSI penalty = 70). The RSI gate's intent is
# to catch fresh overbought spikes prone to reversal — but a sustained
# elevated RSI *with* vwap_reclaim and momentum_bull already confirming is a
# trend-continuation signature, not a fresh spike (mirrors the equity DNA
# model's own "sustained high RSI = continuation, single-bar spike =
# exhaustion" distinction). Idea: waive the RSI>70 penalty (LONG) / RSI<30
# penalty (SHORT) specifically when both trend-confirmation signals are
# already true.
RSI_TREND_EXEMPT = False

# PM_SHORT — disabled live Jul 8 2026 (0-for-7, see futures_jul8_gap_hunt
# memory). Default here matches live; set True only to reproduce pre-Jul-8
# behavior for an old-vs-new comparison.
PM_SHORT_ENABLED = False

# Candidate idea (Jul 8 2026, user-directed) — opt-in via --regime-aware-exits.
# Reuses the SAME IB-range day classification (detect_regime, CHOPPY/TRENDING/
# QUIET) already computed and validated for Hero-gate weighting — not a new
# real-time chop detector (those were tried and rejected, see
# futures_exit_stack_jul7 memory). Idea: don't protect profit at the same
# fixed distance regardless of day character. On CHOPPY/QUIET days, lock in
# fast and take less — real moves are smaller and more likely to round-trip.
# On TRENDING days, give it more room before locking and a wider trail gap —
# the whole point is not to get shaken out of a real, sustained move.
REGIME_AWARE_EXITS = False
# Combine width-based regime with IB directional commitment (ib_kind) for the
# exit-stack lookup — opt-in via --trending-requires-directional. See the
# rationale comment at the point of use in simulate_day().
TRENDING_REQUIRES_DIRECTIONAL = False

# Candidate idea (Jul 9 2026) — opt-in via --long-allows-a-grade. See rationale
# comment at point of use (grade gate, just before Hero gate).
LONG_ALLOWS_A_GRADE = False

# Candidate idea (Jul 9 2026) — opt-in via --hero-trending-requires-directional.
# See rationale at point of use (Hero gate regime input).
HERO_TRENDING_REQUIRES_DIRECTIONAL = False
# v3 — SHIPPED to futures_trader.py Jul 8 2026 (full 2026 YTD backtest: N=53,
# WR=69.8%, $+3,095, MaxDD=-$1,415 vs flat-150 baseline's N=54/64.8%/$+1,991/
# -$2,347 — beats baseline on every metric). Known limitation: TRENDING's low
# lock-fraction (0.20) under-protects modestly-trending misclassified days
# (e.g. Jun 18 2026) — a v4 attempt raising it to 0.40 fixed that but hurt
# genuinely-big trends more ($2,583 total, worse than v3). Not yet fixed —
# needs a smoothly graduated lock-fraction, not a flat number.
EXIT_PARAMS_BY_REGIME: dict[str, dict[str, float]] = {
    'CHOPPY':   {'be_pts': 90.0,  'be_frac': 0.45, 'wide_pts': 130.0, 'wide_gap': 60.0,  'tight_pts': 200.0, 'tight_gap': 35.0},
    'QUIET':    {'be_pts': 90.0,  'be_frac': 0.45, 'wide_pts': 130.0, 'wide_gap': 60.0,  'tight_pts': 200.0, 'tight_gap': 35.0},
    'TRENDING': {'be_pts': 110.0, 'be_frac': 0.20, 'wide_pts': 300.0, 'wide_gap': 180.0, 'tight_pts': 550.0, 'tight_gap': 110.0},
}

# H6 candidate (Jul 8 2026 pm) — opt-in, research-only. See hero_score.py's
# score_h6_intraday_trend for the rationale (H2/H3 are contaminated by
# multi-day 1H lookback; H6 is same-day-only RSI+VWAP). Not calibrated —
# H6_WEIGHT=0 and TRENDING_SKIP_OVERRIDE=None reproduce current live exactly.
H6_WEIGHT = 0
TRENDING_SKIP_OVERRIDE: 'int | None' = None

# ── Thesis invalidation — REVERTED Jul 7 2026, opt-in only via --thesis-invalidation ──
# Tried widening BASE_STOP_PTS to 500 (rare backstop) + this signal-based exit
# as the real loss-cutter. Once validated against the COMPLETE entry pipeline
# (Hero gate + overnight bias + IB kind + 14:00 cutoff — all missing from the
# quick backtests that first looked promising), it was net NEGATIVE vs the
# flat 150pt stop: $3,770 vs $4,172 full 2026 YTD, 52.5% vs 60.0% WR. Reverted
# same day. Kept here as an opt-in flag for anyone re-testing a variant of the
# idea — default OFF, matching live production.
USE_THESIS_INVALIDATION = False
THESIS_STOP_PTS          = 500.0   # backstop width when this mode is enabled
THESIS_FAIL_MIN_VOTES    = 2
THESIS_FAIL_CONFIRM_BARS = 2

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

# Candidate idea (Jul 8 2026, user-directed) — opt-in via --ib-ready. Test
# pulling the entry window forward (e.g. 9:55, 25min IB instead of 60min) to
# see if the extra window catches real moves the 10:30 wait misses — same
# category of question as the Jul 7 "Large IB gate delayed entry to 10:45"
# finding, but testing the OPPOSITE direction (earlier, not later).
IB_READY_OVERRIDE: '_dt.time | None' = None

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
    """
    Point-based (was ATR-multiple before Jul 7 2026 morning). BASE_STOP_PTS
    (150) is the real loss-cutter, matching live. If USE_THESIS_INVALIDATION
    is on (opt-in comparison mode, off by default), the stop widens to
    THESIS_STOP_PTS (500) and becomes a rare backstop instead.
    `atr` param kept for signature compat with existing call sites.
    """
    def rt(v):
        return round(round(v / TICK_SIZE) * TICK_SIZE, 2)
    stop_pts = THESIS_STOP_PTS if USE_THESIS_INVALIDATION else BASE_STOP_PTS
    if side == 'LONG':
        return rt(price - stop_pts), rt(price + BASE_TARGET_PTS)
    else:
        return rt(price + stop_pts), rt(price - BASE_TARGET_PTS)


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

    # ── Bull/bear signal patterns — redesigned Jul 7 2026 ──────────────────
    # Old patterns had two problems: (1) orb_bull/orb_bear required
    # session=='NY_OPEN' — orb_bear fired ZERO times in a 3-month sample
    # because of this; (2) vwap_rejection/momentum_bear were single-bar-
    # sensitive (any one noisy tick reset them). Fixed: ORB restriction
    # removed; VWAP position is now a sustained 3-bar streak instead of a
    # single crossing event; momentum is majority (3-of-4) over 5 bars
    # instead of strict 3-of-3.

    # ORB break — session restriction removed (was NY_OPEN-only)
    sig['orb_bull'] = orb_set and price > orb_high
    sig['orb_bear'] = orb_set and price < orb_low

    # VWAP position — sustained 3-bar streak instead of a single-bar crossing
    if len(bars_today) >= 3:
        last3v = bars_today['close'].iloc[-3:]
        sig['vwap_reclaim']   = bool((last3v > vwap).all())
        sig['vwap_rejection'] = bool((last3v < vwap).all())
    else:
        sig['vwap_reclaim'] = sig['vwap_rejection'] = False

    # Momentum: majority (3-of-4) same-direction transitions over the last 5
    # bars + on the right side of VWAP — was strict 3-of-3 over 4 bars.
    if len(bars_today) >= 5:
        last5 = bars_today['close'].iloc[-5:]
        up_count   = sum(1 for i in range(len(last5)-1) if last5.iloc[i] < last5.iloc[i+1])
        down_count = sum(1 for i in range(len(last5)-1) if last5.iloc[i] > last5.iloc[i+1])
        sig['momentum_bull'] = (up_count >= 3) and price > vwap
        sig['momentum_bear'] = (down_count >= 3) and price < vwap
    else:
        sig['momentum_bull'] = sig['momentum_bear'] = False

    # Session open play: price moved away from first RTH close in direction of VWAP
    if len(bars_today) >= 2:
        open_bar = float(bars_today['close'].iloc[0])
        sig['open_play_bull'] = price > open_bar and price > vwap
        sig['open_play_bear'] = price < open_bar and price < vwap
    else:
        sig['open_play_bull'] = sig['open_play_bear'] = False

    # PM IB break — PM_SHORT DISABLED Jul 8 2026, mirrors futures_trader.py
    # (0-for-7 lifetime, shorts exhaustion lows every time, see
    # futures_jul8_gap_hunt memory). PM_LONG kept, still live. PM_SHORT_ENABLED
    # exists only so old-vs-new comparisons can still be run in this file.
    sig['pm_bull'] = pm_set and pm_high > 0 and price > pm_high
    sig['pm_bear'] = PM_SHORT_ENABLED and pm_set and pm_low > 0 and price < pm_low

    return sig


# ── Regime detection ─────────────────────────────────────────────────────────

def calc_session_rvol(bars_today: pd.DataFrame) -> float:
    """
    Matches live calc_session_rvol() exactly: current bar volume / average
    volume of today's RTH bars so far. Added Jul 7 2026 as a hard entry gate
    (ENTRY_MIN_RVOL) and as an input to get_regime()'s participation filter.
    """
    if bars_today.empty or len(bars_today) < 2:
        return 1.0
    avg_v = bars_today['volume'].mean()
    if not avg_v:
        return 1.0
    return float(bars_today['volume'].iloc[-1]) / float(avg_v)


def calc_htf_trend(all_bars: pd.DataFrame, current_ts) -> int:
    """
    Matches live calc_htf_trend() exactly: net direction of the last
    HTF_TREND_BARS completed HTF_BARS_MIN-minute bars. Returns 1/-1/0.
    Added Jul 7 2026 as a hard entry gate (30-min trend must agree with side).
    """
    hist = all_bars[all_bars.index <= current_ts]
    if hist.empty:
        return 0
    htf = hist['close'].resample(f'{HTF_BARS_MIN}min').last().dropna()
    if len(htf) < HTF_TREND_BARS:
        return 0
    last_n = htf.iloc[-HTF_TREND_BARS:]
    if last_n.iloc[-1] > last_n.iloc[0]:
        return 1
    if last_n.iloc[-1] < last_n.iloc[0]:
        return -1
    return 0


def get_regime(bars_today: pd.DataFrame, bars_hist: pd.DataFrame,
               prev_close: float) -> str:
    """
    Rebuilt Jul 7 2026 — matches live get_regime() exactly. Old formula (STRONG
    vs NORMAL indistinguishable in backtest) replaced with: RVOL>=0.65
    participation gate, hybrid day/session change reference (STRONG uses
    change vs today's own open, WEAK keeps change vs yesterday's close),
    5-bar trend (was 3 — too noise-sensitive), choppy measured against
    session_chg (today's own range) not day_chg.
    bars_today = today's RTH bars (for trend, choppiness, VWAP, RVOL)
    bars_hist  = 2-day history (for RSI)
    prev_close = yesterday's last close (for day_chg_pct)
    """
    if len(bars_today) < 6:
        return 'NORMAL'

    price = float(bars_today['close'].iloc[-1])
    vwap  = compute_vwap(bars_today)
    rsi   = compute_rsi(bars_hist['close'])
    rvol  = calc_session_rvol(bars_today)

    above_vwap = price > vwap if vwap else True

    # Short-term trend: last 5 bars (was 3)
    trend = bars_today['close'].iloc[-5:]
    trending_up   = float(trend.iloc[-1]) > float(trend.iloc[0])
    trending_down = float(trend.iloc[-1]) < float(trend.iloc[0])

    # Day change vs YESTERDAY's close (used for WEAK)
    day_chg_pct = (price - prev_close) / prev_close * 100 if prev_close else 0

    # Change vs TODAY's own open (used for STRONG — catches intraday reversals)
    session_open = float(bars_today['open'].iloc[0])
    session_chg_pct = (price - session_open) / session_open * 100 if session_open else 0

    # Choppiness: >40% bar reversals, measured against session_chg
    diffs  = bars_today['close'].diff().dropna()
    flips  = sum(1 for i in range(1, len(diffs)) if diffs.iloc[i] * diffs.iloc[i-1] < 0)
    choppy = (flips / max(len(diffs), 1)) > 0.4 and abs(session_chg_pct) < 0.15

    if choppy:
        return 'NORMAL'
    if rvol < 0.65:
        return 'NORMAL'   # not enough participation to trust the direction
    if above_vwap and trending_up and session_chg_pct > 0.15 and rsi < 80:
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
        if rsi > 70:
            trend_confirmed = sig.get('vwap_reclaim') and sig.get('momentum_bull')
            if not (RSI_TREND_EXEMPT and trend_confirmed):
                score -= 10
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
        if rsi < 30:
            trend_confirmed = sig.get('vwap_rejection') and sig.get('momentum_bear')
            if not (RSI_TREND_EXEMPT and trend_confirmed):
                score -= 10
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
    elif IB_READY_OVERRIDE is not None:
        ib_ready_time = IB_READY_OVERRIDE
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

    # Sustained vwap_reclaim/vwap_rejection streak (candidate idea, opt-in via
    # SUSTAIN_A_PLUS_BONUS) — consecutive bars the signal has held true.
    reclaim_streak   = 0
    rejection_streak = 0

    # Phase 5: day regime detected once at IB formation (10:30am)
    day_regime: str | None = None

    # IB directional classification — computed once at IB gate
    # BEAR_DIRECTIONAL (ib_mid<0.25) unlocks SHORT without requiring WEAK×3
    ib_kind: str | None = None

    # Large IB gate: when IB range > large_ib_gate_pts, delay first entry to 10:45
    # Prevents entering right into the tail of IB volatility
    large_ib_delayed: bool = False

    # Thesis-invalidation state (added Jul 7 2026) — per-position fail streak,
    # tracked across bars (position dict carries 'fail_streak', reset on new entry).

    for i in range(3, len(rth)):
        bar         = rth.iloc[i]
        t           = rth.index[i].time()
        bars_today  = rth.iloc[:i+1]           # today's RTH bars for VWAP/signals/regime

        # 2-day history ending at current bar (for ATR, RSI — matches live 2-day bar window)
        current_ts = rth.index[i]
        bars_hist  = all_bars[all_bars.index <= current_ts].iloc[-100:]

        # ── Compute signals/regime/HTF unconditionally, every bar ──────────────
        # (Jul 7 2026 sync: previously only computed when scanning for a new
        # entry — but thesis-invalidation needs these for OPEN positions too,
        # matching live monitor_open_trades() which computes them once per
        # cycle regardless of position state.)
        sig    = get_signals(bars_today, bars_hist, orb_high, orb_low, orb_set,
                             pm_high, pm_low, pm_set)
        regime = get_regime(bars_today, bars_hist, prev_close)
        htf    = calc_htf_trend(all_bars, current_ts)

        if SUSTAIN_A_PLUS_BONUS and sig:
            reclaim_streak   = reclaim_streak + 1 if sig.get('vwap_reclaim') else 0
            rejection_streak = rejection_streak + 1 if sig.get('vwap_rejection') else 0

        # Track consecutive regime scans (mirrors live _confirmed_scans)
        if regime == last_regime:
            consec_count += 1
        else:
            consec_count = 1
            last_regime  = regime
        confirmed_scans = consec_count

        # IB range / day regime / IB-kind classification (mirrors live — happens
        # every scan until classified, regardless of position state)
        ib_range = float(bars_today['high'].max() - bars_today['low'].min()) if len(bars_today) >= 2 else 0.0
        if day_regime is None and ib_range >= MIN_IB_RANGE:
            day_regime = detect_regime(ib_range)
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
            if large_ib_gate_pts > 0 and ib_range > large_ib_gate_pts:
                large_ib_delayed = True

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

            # 1. Hard stop — the primary loss-cutter (flat 150pt), UNLESS
            # USE_THESIS_INVALIDATION opt-in mode is on, in which case it's a
            # rare catastrophic backstop (500pt) instead. SL from PREVIOUS
            # bar — no same-bar trail race.
            stop_hit_label = 'stop' if not USE_THESIS_INVALIDATION else ('backstop' if position['sl'] == position['stop_init'] else 'trail_lock')
            if not is_short and float(bar['low']) <= sl:
                exit_price, exit_reason = sl, stop_hit_label
            elif is_short and float(bar['high']) >= sl:
                exit_price, exit_reason = sl, stop_hit_label

            # 2. Daily circuit breaker (realized + unrealized, checked before target)
            if not exit_reason:
                cur_pnl_pts = (entry - float(bar['close'])) if is_short else (float(bar['close']) - entry)
                cur_usd = cur_pnl_pts * POINT_VALUE * contracts - COMMISSION_RT
                if daily_pnl + cur_usd <= -MAX_DAILY_LOSS:
                    exit_price, exit_reason = float(bar['close']), 'dll_circuit'

            # 3. Target hit (1500pt backstop — essentially never fires; trail does the work)
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

            # 4b. Thesis invalidation — opt-in only (USE_THESIS_INVALIDATION,
            # off by default; reverted Jul 7 2026, see constants comment
            # above for why). Exit on evidence the setup failed instead of
            # waiting for price to reach the backstop. 2-of-4 votes (regime
            # flip, HTF flip, opposing momentum, opposing VWAP cross/reclaim),
            # sustained 2 consecutive closed bars.
            if not exit_reason and USE_THESIS_INVALIDATION and sig:
                regime_against = (regime == 'WEAK') if not is_short else (regime in ('STRONG', 'NORMAL'))
                htf_against    = (htf == -1) if not is_short else (htf == 1)
                opp_signal     = sig.get('momentum_bear') if not is_short else sig.get('momentum_bull')
                opp_vwap       = sig.get('vwap_rejection') if not is_short else sig.get('vwap_reclaim')
                fail_votes     = sum([bool(regime_against), bool(htf_against), bool(opp_signal), bool(opp_vwap)])
                position['fail_streak'] = position.get('fail_streak', 0) + 1 if fail_votes >= 1 else 0
                if fail_votes >= THESIS_FAIL_MIN_VOTES and position['fail_streak'] >= THESIS_FAIL_CONFIRM_BARS:
                    exit_price, exit_reason = float(bar['close']), 'thesis_fail'

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

                # Proportional lock-in (protects BE_LOCK_FRACTION of peak
                # favorable move) — predates the thesis-invalidation
                # experiment, unconditional, matches live exactly.
                if REGIME_AWARE_EXITS:
                    # Combine width-based regime with IB directional commitment
                    # (ib_kind) — Jul 9 2026. A wide IB range alone doesn't mean
                    # a real trend: Jun 18/23 2026 both had wide (250-430pt)
                    # ranges but closed near the MIDDLE of that range (ROTATIONAL,
                    # ib_mid 0.62/0.75) — swung both ways without committing.
                    # Jul 7 2026 (a day that validated well) closed at ib_mid=0.05
                    # (BEAR_DIRECTIONAL) — genuine directional commitment. The
                    # classic pit-trader "trend day" distinction: width AND
                    # closing near an extreme, not width alone.
                    _exit_regime = day_regime or 'CHOPPY'
                    if (TRENDING_REQUIRES_DIRECTIONAL and _exit_regime == 'TRENDING'
                            and ib_kind == 'ROTATIONAL'):
                        _exit_regime = 'CHOPPY'   # wide range, no directional commitment — treat as chop
                    _p = EXIT_PARAMS_BY_REGIME.get(_exit_regime, EXIT_PARAMS_BY_REGIME['CHOPPY'])
                    _be_pts = _p['be_pts']
                    _wide_pts, _wide_gap = _p['wide_pts'], _p['wide_gap']
                    _tight_pts, _tight_gap = _p['tight_pts'], _p['tight_gap']
                    # Graduated lock-fraction (opt-in via 'be_frac_near'/'be_frac_far'
                    # in the regime's param dict) — Jul 9 2026, fixes the v3 known
                    # limitation: a flat low fraction (tuned for genuinely huge
                    # trends) under-protects a modest peak on a day merely
                    # misclassified as TRENDING by IB range. Interpolates linearly
                    # from frac_near (protective, at be_pts) to frac_far (loose,
                    # at tight_pts) based on how far peak actually got — a modest
                    # peak gets near-full protection, a peak that grows into
                    # tight_pts territory earns the loose fraction because it has
                    # now proven itself a real move. Regimes without these keys
                    # (CHOPPY/QUIET) keep the flat 'be_frac' behavior unchanged.
                    if 'be_frac_near' in _p:
                        near, far = _p['be_frac_near'], _p['be_frac_far']
                        ceiling = _p.get('be_frac_ceiling', _tight_pts)  # interpolation ceiling — separate from the tight-trail trigger
                        if pnl_pts_peak <= _be_pts:
                            _be_frac = near
                        elif pnl_pts_peak >= ceiling:
                            _be_frac = far
                        else:
                            frac_t = (pnl_pts_peak - _be_pts) / (ceiling - _be_pts)
                            _be_frac = near + frac_t * (far - near)
                    else:
                        _be_frac = _p['be_frac']
                else:
                    _be_pts, _be_frac = BE_ACTIVATE_PTS, BE_LOCK_FRACTION
                    _wide_pts, _wide_gap = TRAIL_WIDE_PTS, TRAIL_WIDE_GAP
                    _tight_pts, _tight_gap = TRAIL_TIGHT_PTS, TRAIL_TIGHT_GAP

                if pnl_pts_peak >= _be_pts:
                    locked = round(pnl_pts_peak * _be_frac, 2)
                    be_sl  = round(entry + max(locked, TICK_SIZE), 2) if not is_short else round(entry - max(locked, TICK_SIZE), 2)
                    if (not is_short and be_sl > sl) or (is_short and be_sl < sl):
                        sl = be_sl

                if pnl_pts_peak >= _wide_pts:
                    w_sl = round(peak - _wide_gap, 2) if not is_short else round(peak + _wide_gap, 2)
                    if (not is_short and w_sl > sl) or (is_short and w_sl < sl):
                        sl = w_sl

                if pnl_pts_peak >= _tight_pts:
                    t_sl = round(peak - _tight_gap, 2) if not is_short else round(peak + _tight_gap, 2)
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

            # IB range gate: H-L from 9:30 to now must be >= 50pts (mirrors live)
            if 0 < ib_range < MIN_IB_RANGE:
                continue

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
                (regime == 'WEAK' and confirmed_scans >= SHORT_CONFIRM_SCANS) or
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

                # A+ only (was 'A' or 'A+') — backtested Jul 7: A-grade entries
                # diluted WR from 56% to 41% over a 9-day sample; single biggest
                # lever in the whole redesign. Matches live exactly.
                score, grade = grade_entry(sig, regime, side)

                # Candidate idea (opt-in, --sustain-bonus): upgrade a sub-A+ grade
                # to A+ if vwap_reclaim/vwap_rejection has held SUSTAIN_BARS+
                # consecutive bars — catches sustained grinding moves (like the
                # Jul 7 rally) that never get an ORB break and cap out at A.
                if SUSTAIN_A_PLUS_BONUS and grade != 'A+' and grade != 'SKIP':
                    streak = reclaim_streak if side == 'LONG' else rejection_streak
                    if streak >= SUSTAIN_BARS and score + SUSTAIN_BONUS_PTS >= 80:
                        grade = 'A+'

                # Candidate idea (Jul 9 2026) — opt-in via --long-allows-a-grade.
                # gate_audit.py --report showed the A+-only GRADE cutoff is
                # net-harmful specifically for LONG on IBKR (N=24, only 38% of
                # blocks justified, avg +13.7pts if taken — i.e. 62% of blocked
                # LONGs would have won) while Hero gate is already doing the
                # real, validated filtering work for LONG separately (N=11, 82%
                # accurate blocks, avg -40.9pts). SHORT's GRADE blocks were
                #100% justified (N=7) — leave SHORT at A+-only, only loosen LONG.
                if LONG_ALLOWS_A_GRADE and side == 'LONG' and grade == 'A':
                    pass  # let it through to Hero gate instead of skipping here
                elif grade != 'A+':
                    continue

                # NOTE: the old "PM signals must align with ORB direction" gate
                # (orb_dir vs ib_kind) was removed during the Jul 7 sync pass —
                # confirmed it has no equivalent anywhere in current
                # futures_trader.py's run_scan(); it was legacy from an earlier
                # strategy generation and kept sim artificially more restrictive
                # than live.
                setup = setup_name(sig, side)

                price      = float(bar['close'])
                atr        = compute_atr(bars_hist)   # 2-day ATR (kept for hero_score signature compat)
                sl, target = calc_sl_target(price, atr, side)

                rr = abs(target - price) / abs(price - sl) if abs(price - sl) > 0 else 0
                if rr < MIN_RR - 0.01:
                    continue

                # Sanity ceiling — catches broken/extreme data, not the design's
                # own stop width. BUG FIXED live Jul 7 2026 (was a bare 250pt
                # literal left over from the 150pt-era stop — would have
                # silently blocked every trade once BASE_STOP_PTS became 500).
                # Scales off the active stop width so it can't desync again.
                _active_stop = THESIS_STOP_PTS if USE_THESIS_INVALIDATION else BASE_STOP_PTS
                stop_pts = abs(price - sl)
                if stop_pts > _active_stop + 100.0:
                    continue

                # Hero quality gate — Phase 5 regime-aware scoring + H5 FIB boost
                # (matches live gate ORDER: A+ grade -> Hero gate -> RVOL -> HTF)
                cc = calc_contracts(price, sl)
                if HERO_GATE_ENABLED:
                    bars_up  = all_bars[all_bars.index <= current_ts]
                    hero_regime = day_regime or 'CHOPPY'   # fallback if IB data missing
                    # Candidate idea (Jul 9 2026) — opt-in via
                    # --hero-trending-requires-directional. Same pit-trader
                    # "trend day" distinction tested on the exit side: a wide
                    # IB range alone isn't a real trend if price closed near
                    # the middle of that range (ROTATIONAL) rather than near
                    # an extreme. Downgrades Hero's regime INPUT (not just the
                    # exit stack) so entry scoring uses CHOPPY's structure-
                    # weighted heroes instead of TRENDING's momentum-weighted
                    # ones on a day that's wide but hasn't actually committed.
                    if (HERO_TRENDING_REQUIRES_DIRECTIONAL and hero_regime == 'TRENDING'
                            and ib_kind == 'ROTATIONAL'):
                        hero_regime = 'CHOPPY'
                    h_score, h_flags = score_entry_regime(price, atr, side, bars_up, prev_rth,
                                                           hero_regime, h6_weight=H6_WEIGHT)
                    h5_fib   = h_flags.get('H5_FIB_FLOOR', False)
                    skip_th  = TRENDING_SKIP_OVERRIDE if (hero_regime == 'TRENDING'
                                                           and TRENDING_SKIP_OVERRIDE is not None) else None
                    if skip_th is not None:
                        contracts = 0 if h_score < skip_th else cc
                    else:
                        contracts = contracts_from_regime_score(h_score, hero_regime, cc, h5_fib=h5_fib)
                    if contracts == 0:
                        continue   # below quality threshold — skip this setup
                else:
                    contracts = cc

                # RVOL + HTF gates — added Jul 7 2026, checked AFTER Hero gate
                # (matches live run_scan() exact order).
                entry_rvol = calc_session_rvol(bars_today)
                if GRADUATED_RVOL and HERO_GATE_ENABLED:
                    if entry_rvol < RVOL_GRAD_FLOOR:
                        continue
                    if entry_rvol < ENTRY_MIN_RVOL:
                        if not is_gold_score(h_score, hero_regime):
                            continue
                        contracts = min(contracts, 1)
                elif entry_rvol < ENTRY_MIN_RVOL:
                    continue
                # (htf already computed unconditionally at top of loop — reuse it)
                if (side == 'LONG' and htf != 1) or (side == 'SHORT' and htf != -1):
                    continue

                position  = {
                    'entry':       price,
                    'sl':          sl,
                    'stop_init':   sl,
                    'target':      target,
                    'side':        side,
                    'contracts':   contracts,
                    'entry_time':  t.strftime('%H:%M'),
                    'entry_idx':   i,
                    'peak':        price,
                    'setup':       setup,
                    'grade':       grade,
                    'fail_streak': 0,
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
    ap.add_argument('--stop-pts',    type=float, default=None, help='Override BASE_STOP_PTS (default 150)')
    ap.add_argument('--target-pts',  type=float, default=None, help='Override BASE_TARGET_PTS (default 1500)')
    ap.add_argument('--dll',         type=float, default=None, help='Override MAX_DAILY_LOSS DLL (default 3750)')
    ap.add_argument('--max-trades',  type=int,   default=None, help='Override MAX_DAILY_TRADES (default 5)')
    ap.add_argument('--be-pts',      type=float, default=None, help='Override BE_ACTIVATE_PTS (default 150)')
    ap.add_argument('--be-lock-frac', type=float, default=None, dest='be_lock_frac',
                    help='Override BE_LOCK_FRACTION (default 0.35)')
    ap.add_argument('--trail-wide-pts', type=float, default=None, dest='trail_wide_pts',
                    help='Override TRAIL_WIDE_PTS (default 200)')
    ap.add_argument('--trail-wide-gap', type=float, default=None, dest='trail_wide_gap',
                    help='Override TRAIL_WIDE_GAP (default 120)')
    ap.add_argument('--trail-tight-pts', type=float, default=None, dest='trail_tight_pts',
                    help='Override TRAIL_TIGHT_PTS (default 350)')
    ap.add_argument('--trail-tight-gap', type=float, default=None, dest='trail_tight_gap',
                    help='Override TRAIL_TIGHT_GAP (default 60)')
    ap.add_argument('--regime-aware-exits', action='store_true', dest='regime_aware_exits',
                    help='Candidate idea: exit BE/trail params vary by day regime '
                         '(CHOPPY/QUIET lock in fast+small, TRENDING gets more room) '
                         'instead of one fixed set of numbers — see EXIT_PARAMS_BY_REGIME')
    ap.add_argument('--trending-requires-directional', action='store_true',
                    dest='trending_requires_directional',
                    help='Candidate idea: a wide-IB-range day only gets TRENDING exit '
                         'treatment if ib_kind also shows directional commitment '
                         '(not ROTATIONAL) — width alone is not enough')
    ap.add_argument('--long-allows-a-grade', action='store_true', dest='long_allows_a_grade',
                    help='Candidate idea: let grade=A (not just A+) reach Hero gate for '
                         'LONG only — gate_audit.py showed A+-only GRADE cutoff blocks '
                         'LONG setups that would have won 62%% of the time (N=24)')
    ap.add_argument('--hero-trending-requires-directional', action='store_true',
                    dest='hero_trending_requires_directional',
                    help='Candidate idea: same as --trending-requires-directional but '
                         'for Hero gate\'s regime INPUT (entry scoring), not the exit stack')
    ap.add_argument('--thesis-invalidation', action='store_true', dest='thesis_invalidation',
                    help='Opt in to the REVERTED wide-backstop + signal-based exit experiment '
                         '(500pt backstop, cut on regime/HTF/momentum/VWAP turning against the '
                         'position) instead of the current live flat-150pt-stop default — for '
                         're-testing that idea only, not for production use')
    ap.add_argument('--no-2pm-cutoff', action='store_true', dest='no_2pm_cutoff',
                    help='Disable the 14:00 ET no-new-entry gate (extends to normal AFTERNOON close)')
    ap.add_argument('--sustain-bonus', action='store_true', dest='sustain_bonus',
                    help='Candidate idea: alternate A+ path for sustained vwap_reclaim/rejection '
                         '(6+ bars) without requiring an ORB break — targets the Jul 7 missed-rally case')
    ap.add_argument('--short-confirm', type=int, default=None, dest='short_confirm',
                    help='Candidate idea: override consecutive WEAK-scan requirement for SHORT (default 3)')
    ap.add_argument('--graduated-rvol', action='store_true', dest='graduated_rvol',
                    help='Candidate idea: fold RVOL into Hero score as a graduated factor '
                         '(floor-0.85 band allowed through if Hero score clears GOLD, capped at '
                         '1 contract) instead of a hard 0.85 cliff — targets Jul 7/Jul 8 near-misses')
    ap.add_argument('--rvol-floor', type=float, default=None, dest='rvol_floor',
                    help='Override RVOL_GRAD_FLOOR for --graduated-rvol (default 0.60)')
    ap.add_argument('--rsi-trend-exempt', action='store_true', dest='rsi_trend_exempt',
                    help='Candidate idea: waive the RSI>70(LONG)/RSI<30(SHORT) overbought/'
                         'oversold penalty when vwap_reclaim+momentum already confirm the '
                         'same-direction trend — targets grinding rallies capped at A instead '
                         'of A+ purely by the RSI penalty (found via Jul 7 log)')
    # --compare-stops REMOVED Jul 7 2026 sync pass — it compared ATR-multiple
    # combos, a paradigm that no longer exists now that stop sizing is
    # point-based. Use --stop-pts / --target-pts with separate runs instead
    # if a similar comparison is needed.
    args = ap.parse_args()

    # Apply overrides to module-level constants so all functions pick them up
    global BASE_STOP_PTS, BASE_TARGET_PTS, MAX_DAILY_LOSS, MAX_DAILY_TRADES, BE_ACTIVATE_PTS, HERO_GATE_ENABLED, USE_THESIS_INVALIDATION, ENTRY_CUTOFF, SUSTAIN_A_PLUS_BONUS, SHORT_CONFIRM_SCANS, GRADUATED_RVOL, RVOL_GRAD_FLOOR, RSI_TREND_EXEMPT, BE_LOCK_FRACTION, TRAIL_WIDE_PTS, TRAIL_WIDE_GAP, TRAIL_TIGHT_PTS, TRAIL_TIGHT_GAP, REGIME_AWARE_EXITS, TRENDING_REQUIRES_DIRECTIONAL, LONG_ALLOWS_A_GRADE, HERO_TRENDING_REQUIRES_DIRECTIONAL
    if args.stop_pts    is not None: BASE_STOP_PTS    = args.stop_pts
    elif args.regime_aware_exits:    BASE_STOP_PTS    = 200.0  # v3 shipped with stop=200; override with --stop-pts if needed
    if args.target_pts  is not None: BASE_TARGET_PTS  = args.target_pts
    if args.dll         is not None: MAX_DAILY_LOSS   = args.dll
    if args.max_trades  is not None: MAX_DAILY_TRADES = args.max_trades
    if args.be_pts      is not None: BE_ACTIVATE_PTS  = args.be_pts
    if args.be_lock_frac is not None: BE_LOCK_FRACTION = args.be_lock_frac
    if args.trail_wide_pts is not None: TRAIL_WIDE_PTS = args.trail_wide_pts
    if args.trail_wide_gap is not None: TRAIL_WIDE_GAP = args.trail_wide_gap
    if args.trail_tight_pts is not None: TRAIL_TIGHT_PTS = args.trail_tight_pts
    if args.trail_tight_gap is not None: TRAIL_TIGHT_GAP = args.trail_tight_gap
    if args.regime_aware_exits:      REGIME_AWARE_EXITS = True
    if args.trending_requires_directional: TRENDING_REQUIRES_DIRECTIONAL = True
    if args.long_allows_a_grade:      LONG_ALLOWS_A_GRADE = True
    if args.hero_trending_requires_directional: HERO_TRENDING_REQUIRES_DIRECTIONAL = True
    if args.no_hero_gate:            HERO_GATE_ENABLED = False
    if args.thesis_invalidation:     USE_THESIS_INVALIDATION = True
    if args.no_2pm_cutoff:           ENTRY_CUTOFF      = AFTERNOON_END
    if args.sustain_bonus:           SUSTAIN_A_PLUS_BONUS = True
    if args.short_confirm is not None: SHORT_CONFIRM_SCANS = args.short_confirm
    if args.graduated_rvol:          GRADUATED_RVOL   = True
    if args.rvol_floor is not None:  RVOL_GRAD_FLOOR  = args.rvol_floor
    if args.rsi_trend_exempt:       RSI_TREND_EXEMPT = True

    print(f'\n=== Futures Strategy Replay: {args.start} → {args.end} ===')
    _stop_desc = f'THESIS_STOP_PTS={THESIS_STOP_PTS} (backstop, opt-in mode)' if USE_THESIS_INVALIDATION else f'BASE_STOP_PTS={BASE_STOP_PTS} (real stop)'
    print(f'{_stop_desc}  BASE_TARGET_PTS={BASE_TARGET_PTS}  BE_ACTIVATE={BE_ACTIVATE_PTS}pts  MAX_TRADES={MAX_DAILY_TRADES}  DLL=${MAX_DAILY_LOSS:.0f}')
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
