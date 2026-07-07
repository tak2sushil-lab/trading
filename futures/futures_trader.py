"""
futures_trader.py — MNQ Futures Trading System (IBKR Personal Account)
Third vertical: equity → options → futures

Architecture:
  bridge.py           (port 8000)  ←→  IBKR TWS / DU9952463 paper
  prop_rules.py                    ←   IBKR mode safety layer ($2K floor, $150 DLL soft)
  futures_trader.py                ←   this file (strategy + execution)
  database.py (shared root)        ←   trades.db futures_trades table

Config (set by launch_futures_personal.sh):
  FUTURES_BRIDGE_URL   = http://localhost:8000
  FUTURES_ACCOUNT_MODE = IBKR
  FUTURES_STATE_FILE   = futures/ibkr_state.json
  FUTURES_TELEGRAM_TOKEN / CHAT_ID = TriVegaFutures bot + Futures · MNQ · Paper group
"""

import os
import sys
import json
import time
import sqlite3
import requests
import numpy as np
import pandas as pd
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
import pytz
import threading
from apscheduler.schedulers.background import BackgroundScheduler

# ── root path so shared modules resolve ──────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from prop_rules import (
    check_can_trade, get_max_contracts, record_trade_pnl,
    update_eod_balance, get_status as prop_status, load_state as prop_load,
    save_state as prop_save, ACCOUNT_MODE, IBKR_DAILY_CAP, IBKR_DLL_SOFT, IBKR_FLOOR,
)
from portfolio_status import format_all as _portfolio_all
from futures.hero_score import (
    score_entry_regime, contracts_from_regime_score, detect_regime,
)

# ── Telegram ──────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv('FUTURES_TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('FUTURES_TELEGRAM_CHAT_ID')

# ── Bridge ────────────────────────────────────────────────
BRIDGE = os.getenv('FUTURES_BRIDGE_URL', 'http://localhost:8000')  # IBKR bridge (bridge.py)

from strategy_core import SYMBOL, EXCHANGE, POINT_VALUE, TICK_SIZE, TICK_VALUE, COMMISSION  # noqa: E402
from futures.gate_audit import log_block, log_enter, log_shadow_signal  # noqa: E402

# ── Risk constants ────────────────────────────────────────
# Jul 6 2026 — widened for $15K account. Gate-audit + 66-day bar backtest showed
# the old 1.5xATR (~45-58pt) stop was catching routine noise, not real reversals:
# 52% of losers were near-zero "breakeven scratch" exits, and losers recovered
# +135pts on average shortly after the system's own exit. RTH daily range is
# 447pts avg (66-day sample) — a stop an order of magnitude smaller than that
# can't survive real MNQ movement. New sizing grid-searched against real bars;
# $15K/280pt-stop was the return-maximizing point (61.5% WR, beyond which wider
# stops stop paying for the extra risk). See conversation Jul 6 2026 for the full analysis.
MAX_RISK_PER_TRADE   = 560.0          # $ max risk per trade (1 contract x 280pt stop x $2/pt)
MAX_DAILY_LOSS       = IBKR_DLL_SOFT  # $3,750 (25% of $15K) — mirrors prop_rules IBKR soft stop
DAILY_PROFIT_TARGET  = IBKR_DAILY_CAP # $1,200 — mirrors prop_rules IBKR daily cap
MIN_RR               = 1.4     # minimum reward:risk ratio — trivially satisfied now that
                                # target is a 1500pt backstop (RR~5.4); kept as a floor
                                # in case target is ever tightened back down
MAX_OPEN_TRADES      = 2       # max simultaneous MNQ positions
MAX_DAILY_TRADES     = 2       # total trade entries per day (matches tc_champion.json)
COOLDOWN_MINUTES     = 2.0     # minutes to wait after any exit before next entry
MAX_PRICE_DIVERGENCE = 100.0   # pts: max allowed gap between scan price and live price at order time
# A_EXT gate REMOVED Jul 6 2026 — gate_audit scored it 33% accuracy / "REMOVE"
# verdict on live IBKR SHORT (N=6) after being wired off a small N=11/64% sample
# Jun 24. Also structurally conflicts with the new wide-stop philosophy: blocking
# entries at 70pts extension makes no sense when the target itself is 400+pts.

# ── Base stop/target (point-based, RVOL-adaptive) — MNQ-calibrated ──────────
# Replaces the old ATR-multiple calc (1.5x/3.0x ATR ~ 45-58pt stop, 90-116pt
# target) which was sized for a ~30-40pt instrument, not one with a 447pt avg
# daily range. Grid-searched against 66 days of real 5-min bars; RVOL tercile
# adaptation (0.484 / 0.651 boundaries) came from the decoder's own outcome data:
# high-RVOL scans preceded ~1.8x bigger subsequent moves than low-RVOL scans.
BASE_STOP_PTS     = 280.0   # base hard stop (was ~45-58pts)
# Backstop only, not a real profit-take level — verified via backtest (bug sweep
# Jul 6): a literal 420pt hard target clipped winners at $5,812 total; letting
# the trail tiers (below) manage the exit instead reached $8,378 on the same 65
# trades, same win rate. 1500 essentially never binds — it exists only to cap a
# truly unprecedented single-direction move the trail somehow didn't catch.
BASE_TARGET_PTS   = 1500.0
RVOL_HIGH_THRESH  = 0.65    # >= this -> widen (elevated participation, bigger move likely)
RVOL_LOW_THRESH   = 0.48    # <= this -> tighten (quiet/chop, smaller move likely)
RVOL_WIDE_MULT    = 1.3
RVOL_TIGHT_MULT   = 0.8

# ── Profit protection (point-based — MNQ-calibrated, widened Jul 6 2026) ────
# Old tiers (BE at +30, trail at +60/+85) were ~1x ATR — pure noise triggered
# breakeven immediately, then any wiggle stopped the trade for a few dollars.
# New tiers require a real, meaningful move before protecting/trailing.
BE_ACTIVATE_PTS  = 150.0   # +150pts → move stop to entry (real move required first)
TRAIL_WIDE_PTS   = 250.0   # +250pts → trail 120pts behind session peak — let it run
TRAIL_TIGHT_PTS  = 400.0   # +400pts → tighten trail to 60pts (past base target)
TRAIL_WIDE_GAP   = 120.0   # trail distance in wide mode
TRAIL_TIGHT_GAP  = 60.0    # trail distance in tight mode

# ── No-move exit (time-based — frees dead trade slots) ───
# Widened Jul 6 2026 to a 45min decision checkpoint (was 90min) with a dead-zone
# band scaled to the new 280pt stop, per the "watch it 45min-1hr, then decide"
# design: if a trade hasn't shown a real move by 45min, cut with minimum loss
# rather than let it keep drifting toward the full stop.
NO_MOVE_MINUTES = 45      # minutes open before checking
NO_MOVE_MAX_PTS = 60.0    # above this → trade IS progressing, let it run
NO_MOVE_MIN_PTS = -40.0   # below this → hard stop will manage it

# ── ELEPHANT TRADE (Liquidity Grab Reversal) ─────────────
# Algos sweep stop-loss clusters then reverse — we enter at the sweep extreme.
# All parameters derived from 5.5yr MNQ backtest (Jun 12 2026 research session).
ELEPHANT_ENABLED         = True    # master kill-switch
ELEPHANT_ENTRY_CONF      = 10.0    # pts above flush extreme for entry confirmation
ELEPHANT_STOP_PTS        = 100.0   # pts from flush extreme to hard stop (widened from 50: +16pp WR Jun 13 2026)
ELEPHANT_TARGET_PTS      = 150.0   # pts from entry to profit target  (R:R = 150/110 = 1.36)
ELEPHANT_TIMEOUT_MINS    = 180.0   # no-move timeout (3 hr) — wider than regular 90 min
ELEPHANT_FLUSH_STRONG    = 150.0   # min flush depth (pts) on STRONG_BULL days
ELEPHANT_BODY_STRONG     = 100.0   # 15-min opening bar body threshold → STRONG classification
ELEPHANT_MAX_STRONG      = 4       # max entries allowed per STRONG_BULL day
ELEPHANT_ES_MOVE_SKIP    = 40.0    # skip flush when ES also moved ≥40pt in same window (calibrated: MNQ 150pt flush ≈ ES 37pt; 25pt was too tight — blocked all setups)
ELEPHANT_NOON_CUTOFF_ET  = 12      # no elephant entries at/after noon ET
ELEPHANT_LOOKBACK_BARS   = 12      # 60-min rolling window for flush detection (12 × 5-min)

# Volume early exit — cuts losses when institutional conviction selling is confirmed
# Mirrors elephant_backtest.py constants exactly (live ↔ backtest parity).
ELEPHANT_VOL_ADV_ZONE    = 50.0    # pts adverse before vol monitoring activates
ELEPHANT_VOL_BUILD_MULT  = 1.5     # bar vol ≥ this × pre-entry baseline = "building"
ELEPHANT_VOL_CONSEC_BARS = 3       # N consecutive building+worsening 5-min bars → early exit

# ── Session constants (ET) ────────────────────────────────
ET = pytz.timezone('America/New_York')

# Session windows
NY_OPEN_START   = (9, 30)   # best entries
NY_OPEN_END     = (10, 30)
MIDDAY_START    = (10, 30)
MIDDAY_END      = (12, 0)
LUNCH_START     = (12, 0)
LUNCH_END       = (13, 0)
AFTERNOON_START = (13, 0)
AFTERNOON_END   = (15, 30)
EOD_START       = (15, 30)  # 3:30pm ET — no new entries (8:30pm London BST)
HARD_CLOSE      = (16, 0)   # 4:00pm ET — force close all positions (9pm London BST)

SCAN_INTERVAL   = 60        # seconds between scans (1 min, faster than equity)
MONITOR_INTERVAL = 15       # seconds between position checks

# ── London session module (plug / unplug here) ────────────────────────────────
# Flip to True + restart service → London session activates 3am–9am ET
# Flip to False + restart service → London disabled, zero impact on NY session
LONDON_ENABLED = True

# ── Global state ──────────────────────────────────────────
_active_contract_month = ''   # resolved on first live-price fetch; keeps get_bars() aligned
_last_regime          = 'NORMAL'
_confirmed_scans      = 0
_regime_scan_counts   = {'STRONG': 0, 'NORMAL': 0, 'WEAK': 0}
_session_high         = {}   # trade_id → session high
_session_low          = {}   # trade_id → session low
_price_history        = {}   # trade_id → [prices]
_partial_done         = {}   # trade_id → locked_pnl
_orb_high             = None # opening range high (first 15 min)
_orb_low              = None # opening range low
_orb_set              = False
_pm_high              = None # pre-market IB high (8:30–9:30am ET) — Cylinder 4
_pm_low               = None # pre-market IB low
_pm_ib_set            = False
_pm_ib_set_time       = None  # datetime when pm_ib was first captured this session
_daily_macro_bias     = 'BOTH'  # 'LONG' | 'SHORT' | 'BOTH' — set via Telegram or Groq
_overnight_bias       = 'BOTH'  # from overnight_position classifier (tc_champion v3.2)
_overnight_skip_day   = False   # True if overnight_position in bad zone [0.20, 0.40)
_overnight_position   = None    # float — 0=opened at overnight low, 1=overnight high
_overnight_computed   = False   # True after first RTH-bar computation this session

# ── IB classification state (set once at 10:30, sticky for the day) ──────────
_ib_kind              = None    # 'BEAR_DIRECTIONAL' | 'BULL_DIRECTIONAL' | 'ROTATIONAL'
_ib_kind_set          = False   # True once ib_mid computed at IB formation
_day_regime           = None    # 'TRENDING' | 'CHOPPY' | 'QUIET' — for hero weighting
_large_ib_delayed     = False   # True when IB range > 200pts — delay first entry to 10:45
_daily_pnl            = 0.0
_peak_daily_pnl       = 0.0
_trading_paused       = False
_last_exit_time       = None   # datetime of most recent trade exit — cooldown gate
_tg_offset            = 0      # Telegram getUpdates offset — marks messages as read
_scheduler            = None
_cached_df5           = pd.DataFrame()  # bars cached by run_scan(), reused by run_monitor()

# ── Elephant Trade state (reset daily) ───────────────────
_elephant_day_type    = 'NONE'  # NONE | STRONG_BULL
_elephant_trades_today = 0      # count of elephant entries today
_elephant_flush_ids   = set()   # timestamps of flush extremes already acted on (dedup)

_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_DIR, '..', 'trades.db')
MKT_DB_PATH = os.path.join(_DIR, '..', 'market_data.db')

# CME holiday calendar — NOT the same as NYSE. CME stays open on Juneteenth,
# MLK Day, and Presidents Day. Only close on: New Year's, Good Friday,
# Memorial Day, Independence Day, Labor Day, Thanksgiving, Christmas.
CME_HOLIDAYS_2026 = {
    date(2026, 1,  1),   # New Year's Day
    date(2026, 4,  3),   # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 7,  3),   # Independence Day (observed, Sat→Fri)
    date(2026, 9,  7),   # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
}

# RVOL scaling: {"%H:%M" ET slot → avg volume across history} — loaded once at startup.
# Empty dict = RVOL unavailable → dynamic sizing falls back to 1 contract safely.
_avg_vol_by_time: dict = {}


# ── Logging + Telegram ────────────────────────────────────

def log(msg: str):
    ts = datetime.now(ET).strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)


def format_prop_status() -> str:
    """Format prop_rules status dict as a clean Telegram-ready string."""
    s = prop_status()   # calls get_status() from prop_rules
    mode  = s.get('mode', 'TC')
    lines = [f"Mode: {mode}  |  Balance: ${s.get('balance', 0):,.0f}"]
    if mode == 'TC':
        lines.append(f"Target left: ${s.get('tc_target_left', 0):,.0f}  |  MLL buffer: ${s.get('buffer_to_mll', 0):,.0f}")
    lines.append(f"Day P&L: ${s.get('session_pnl', 0):+,.2f}")
    return '\n'.join(lines)


def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'},
            timeout=5,
        )
        if not r.ok:
            log(f"[TG error] {r.status_code}: {r.text[:120]}")
    except Exception as e:
        log(f"[TG error] {e}")


# ── Session detection ─────────────────────────────────────

def get_session() -> str:
    """Returns current session: GLOBEX | NY_OPEN | MIDDAY | LUNCH | AFTERNOON | EOD | CLOSED"""
    now  = datetime.now(ET)
    h, m = now.hour, now.minute
    t    = h * 60 + m   # minutes since midnight

    ny_open_start   = NY_OPEN_START[0]   * 60 + NY_OPEN_START[1]
    ny_open_end     = NY_OPEN_END[0]     * 60 + NY_OPEN_END[1]
    midday_end      = MIDDAY_END[0]      * 60 + MIDDAY_END[1]
    lunch_end       = LUNCH_END[0]       * 60 + LUNCH_END[1]
    afternoon_end   = AFTERNOON_END[0]   * 60 + AFTERNOON_END[1]
    eod_start       = EOD_START[0]       * 60 + EOD_START[1]

    if t < ny_open_start:
        return 'GLOBEX'
    if t < ny_open_end:
        return 'NY_OPEN'
    if t < midday_end:
        return 'MIDDAY'
    if t < lunch_end:
        return 'LUNCH'
    if t < afternoon_end:
        return 'AFTERNOON'
    if t < eod_start + 15:
        return 'EOD'
    return 'CLOSED'


def is_entry_allowed() -> bool:
    """True only during sessions where new entries make sense."""
    return get_session() in ('NY_OPEN', 'MIDDAY', 'AFTERNOON')


def is_market_open() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:   # Saturday/Sunday
        return False
    session = get_session()
    return session not in ('GLOBEX', 'CLOSED')


# ── Bridge helpers ────────────────────────────────────────

def _bridge_get(path: str, timeout: int = 5) -> dict:
    try:
        r = requests.get(f"{BRIDGE}{path}", timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"Bridge GET {path} error: {e}")
        return {}


def _bridge_post(path: str, payload: dict, timeout: int = 10) -> dict:
    try:
        r = requests.post(f"{BRIDGE}{path}", json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"Bridge POST {path} error: {e}")
        return {}


def get_live_price() -> float | None:
    """Get current MNQ best price via bridge (yfinance primary, IBKR live fallback)."""
    global _active_contract_month
    q = _bridge_get(f'/futures/quote/{SYMBOL}')
    # Cache contract month so get_bars() stays on the same contract as the live quote
    cm = str(q.get('contract_month', '') or '').strip()
    if cm:
        _active_contract_month = cm
    return q.get('best_price') or q.get('last') or q.get('close')


def get_bars(bar_size_min: int = 5, days: int = 2) -> pd.DataFrame:
    """
    Fetch historical bars for MNQ from the IBKR bridge.
    Passes active contract_month so bars and live quote use the same IBKR contract
    (prevents ContFuture/live-quote divergence during rollover week).
    Bridge endpoint: GET /history/futures/MNQ?duration=2+D&bar_size=5+mins&rth=false
    Response: {'symbol': 'MNQ', 'bars': [{ts, open, high, low, close, volume}, ...]}
    """
    bar_str  = f'{bar_size_min}+mins'
    dur_str  = f'{days}+D'
    cm       = f'&contract_month={_active_contract_month}' if _active_contract_month else ''
    path     = f'/history/futures/{SYMBOL}?duration={dur_str}&bar_size={bar_str}&rth=false{cm}'
    resp     = _bridge_get(path, timeout=20)
    if not resp or 'error' in resp:
        return pd.DataFrame()
    bars = resp.get('bars', [])
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    if df.empty or 'ts' not in df.columns:
        return pd.DataFrame()
    df['ts'] = pd.to_datetime(df['ts'], utc=True).dt.tz_convert(ET)
    df = df.set_index('ts').sort_index()
    for col in ('open', 'high', 'low', 'close', 'volume'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.dropna(subset=['close'])


def get_bridge_connected() -> bool:
    h = _bridge_get('/')
    return h.get('connected', False)


def _get_es_bars(bar_size_min: int = 5, days: int = 2) -> pd.DataFrame:
    """Fetch ES 5-min bars — used by elephant ES-confirmation filter."""
    path = f'/history/futures/ES?duration={days}+D&bar_size={bar_size_min}+mins&rth=false'
    resp = _bridge_get(path, timeout=20)
    if not resp or 'error' in resp:
        return pd.DataFrame()
    bars = resp.get('bars', [])
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    if df.empty or 'ts' not in df.columns:
        return pd.DataFrame()
    df['ts'] = pd.to_datetime(df['ts'], utc=True).dt.tz_convert(ET)
    df = df.set_index('ts').sort_index()
    for col in ('open', 'high', 'low', 'close', 'volume'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.dropna(subset=['close'])


# ── Opening Range Break (ORB) ─────────────────────────────

def update_orb(df5: pd.DataFrame):
    """Set ORB high/low from first 15 min of current session (3 × 5-min bars)."""
    global _orb_high, _orb_low, _orb_set
    if _orb_set:
        return
    today = datetime.now(ET).date()
    today_open = ET.localize(datetime(today.year, today.month, today.day, 9, 30))
    orb_end    = ET.localize(datetime(today.year, today.month, today.day, 9, 45))

    orb_bars = df5[(df5.index >= today_open) & (df5.index < orb_end)]
    if len(orb_bars) >= 3:
        _orb_high = float(orb_bars['high'].max())
        _orb_low  = float(orb_bars['low'].min())
        _orb_set  = True
        log(f"ORB set: H={_orb_high} L={_orb_low}")


# ── Pre-market IB (Cylinder 4) ───────────────────────────

def update_premarket_ib():
    """
    Capture the pre-market Initial Balance from 8:30–9:30am ET bars.
    On macro days (NFP/CPI/FOMC), this is the range created by the
    8:30am release. Breaking this range during RTH = highest conviction signal.
    Called once per day at ~9:20am startup (before RTH opens).
    """
    global _pm_high, _pm_low, _pm_ib_set, _pm_ib_set_time
    if _pm_ib_set:
        return
    try:
        from futures.macro_calendar import classify_date
        today_cls = classify_date(datetime.now(ET).date().isoformat())
        bars = get_bars(bar_size_min=5, days=1)
        if bars.empty:
            return
        today   = datetime.now(ET).date()
        pm_start = ET.localize(datetime(today.year, today.month, today.day, 8, 30))
        pm_end   = ET.localize(datetime(today.year, today.month, today.day, 9, 30))
        pm_bars  = bars[(bars.index >= pm_start) & (bars.index < pm_end)]
        if len(pm_bars) >= 2:
            _pm_high  = float(pm_bars['high'].max())
            _pm_low   = float(pm_bars['low'].min())
            _pm_ib_set      = True
            _pm_ib_set_time = datetime.now(ET)
            flag = '🔴 MACRO DAY' if today_cls == 'HIGH_IMPACT' else ''
            log(f"Pre-market IB: H={_pm_high}  L={_pm_low}  ({len(pm_bars)} bars) {flag}")
            # Only send Telegram if we're still in the morning window — suppress
            # on afternoon restarts (state restore from bars, not a fresh event).
            is_morning = datetime.now(ET).hour < 11
            if today_cls == 'HIGH_IMPACT' and is_morning:
                send_telegram(
                    f"📊 Pre-market IB set ({today_cls})\n"
                    f"PM High: {_pm_high}  PM Low: {_pm_low}\n"
                    f"Range: {round(_pm_high - _pm_low, 2)}pts\n"
                    f"Reply FUT BIAS LONG or FUT BIAS SHORT after 8:30am data."
                )
    except Exception as e:
        log(f"update_premarket_ib error: {e}")


# ── Overnight position classifier (tc_champion v3.2) ─────
# overnight_position = (rth_open - overnight_low) / overnight_range
# 0 = RTH opens at overnight low (bears held all night)
# 1 = RTH opens at overnight high (bulls held all night)
#
# Thresholds match tc_champion v3.2 (data-validated, WFA 8/9 windows):
#   [0.20, 0.40) = bad zone → skip day (18–36% WR, -$3,679 in 5yr backtest)
#   >= 0.85      = TRENDING_UP  → LONG bias
#   <= 0.20      = TRENDING_DOWN → SHORT bias

_OVN_SKIP_LO  = 0.20
_OVN_SKIP_HI  = 0.40
_OVN_TREND_HI = 0.85
_OVN_TREND_LO = 0.20
_OVN_COMPRESS = 50.0   # pts — thin overnight, skip

def compute_overnight_bias():
    """
    Compute overnight_position once per session (first RTH scan after 9:30am).
    Reads 1-min bars from bridge, computes position within overnight range.
    Sets _overnight_bias and _overnight_skip_day.
    """
    global _overnight_bias, _overnight_skip_day, _overnight_position
    global _overnight_computed, _daily_macro_bias

    if _overnight_computed:
        return

    try:
        bars_1m = get_bars(bar_size_min=1, days=2)
        if bars_1m.empty:
            log("overnight_bias: no 1-min bars — defaulting BOTH")
            _overnight_computed = True
            return

        today     = datetime.now(ET).date()
        today_930 = ET.localize(datetime(today.year, today.month, today.day, 9, 30))

        # Wait for first RTH bar (open price needed) — retry next scan if not yet available
        rth_bars = bars_1m[bars_1m.index >= today_930]
        if rth_bars.empty:
            return

        rth_open = float(rth_bars['open'].iloc[0])

        # Overnight window: prev 4pm ET → today 9:30am ET (skip weekends + holidays)
        prev_day = today - timedelta(days=1)
        while prev_day.weekday() >= 5 or prev_day in CME_HOLIDAYS_2026:
            prev_day -= timedelta(days=1)
        prev_4pm   = ET.localize(datetime(prev_day.year, prev_day.month, prev_day.day, 16, 0))
        night_bars = bars_1m[(bars_1m.index >= prev_4pm) & (bars_1m.index < today_930)]

        _overnight_computed = True   # set before returns — don't retry on data issues

        if len(night_bars) < 20:
            log(f"overnight_bias: sparse overnight bars ({len(night_bars)}) — defaulting BOTH")
            return

        overnight_high  = float(night_bars['high'].max())
        overnight_low   = float(night_bars['low'].min())
        overnight_range = overnight_high - overnight_low

        if overnight_range < _OVN_COMPRESS:
            _overnight_skip_day = True
            log(f"overnight_bias: COMPRESSION ({overnight_range:.0f}pt) — skip day")
            send_telegram(
                f"⚠️ Overnight COMPRESSION ({overnight_range:.0f}pt range)\n"
                f"No entries today — thin session, IB breakouts unreliable.\n"
                f"Override: FUT BIAS LONG or FUT BIAS SHORT"
            )
            return

        pos = (rth_open - overnight_low) / overnight_range
        pos = max(0.0, min(1.0, pos))
        _overnight_position = round(pos, 3)

        if _OVN_SKIP_LO <= pos < _OVN_SKIP_HI:
            _overnight_skip_day = True
            log(f"overnight_bias: bad zone pos={pos:.3f} — skip day")
            send_telegram(
                f"⚠️ Overnight bad zone (pos={pos:.2f})\n"
                f"H={overnight_high:.0f}  L={overnight_low:.0f}  Range={overnight_range:.0f}pt\n"
                f"No entries today — moderate bearish lean, low conviction IB setups.\n"
                f"Override: FUT BIAS LONG or FUT BIAS SHORT"
            )
        elif pos >= _OVN_TREND_HI:
            _overnight_bias = 'LONG'
            if _daily_macro_bias == 'BOTH':   # only set if user hasn't overridden
                _daily_macro_bias = 'LONG'
            log(f"overnight_bias: TRENDING_UP pos={pos:.3f} → LONG")
            send_telegram(
                f"📊 Overnight → <b>LONG bias</b>\n"
                f"Position: {pos:.2f} (bulls held overnight)\n"
                f"H={overnight_high:.0f}  L={overnight_low:.0f}  Range={overnight_range:.0f}pt\n"
                f"Override: FUT BIAS SHORT or FUT BIAS BOTH"
            )
        elif pos <= _OVN_TREND_LO:
            _overnight_bias = 'SHORT'
            if _daily_macro_bias == 'BOTH':
                _daily_macro_bias = 'SHORT'
            log(f"overnight_bias: TRENDING_DOWN pos={pos:.3f} → SHORT")
            send_telegram(
                f"📊 Overnight → <b>SHORT bias</b>\n"
                f"Position: {pos:.2f} (bears held overnight)\n"
                f"H={overnight_high:.0f}  L={overnight_low:.0f}  Range={overnight_range:.0f}pt\n"
                f"Override: FUT BIAS LONG or FUT BIAS BOTH"
            )
            # OVN_POS Option A: overnight closed near its low (≤13%) → exhausted bears.
            # Pre-market already showing bullish structure (26/27 trades were PM_LONG).
            # Data 2025-2026: ovn_pos 0-0.08 → 66.7% WR +$64; 0.08-0.13 → 57.1% WR +$63.
            # Allow LONGs too — do NOT override user's manual FUT BIAS SHORT if set.
            if pos <= 0.13 and _daily_macro_bias == 'SHORT':
                _daily_macro_bias = 'BOTH'
                log(f"OVN_POS Option A: pos={pos:.3f} ≤ 0.13 → exhausted bears → override to BOTH")
                send_telegram(
                    f"📊 OVN_POS Option A: bears exhausted (pos={pos:.2f})\n"
                    f"SHORT bias → BOTH — LONGs now allowed (pre-market coiled)\n"
                    f"Override: FUT BIAS SHORT to force SHORT-only"
                )
        else:
            _overnight_bias = 'BOTH'
            log(f"overnight_bias: NORMAL pos={pos:.3f} — both directions")

    except Exception as e:
        log(f"compute_overnight_bias error: {e}")
        _overnight_computed = True   # don't loop on error


# ── VWAP ──────────────────────────────────────────────────

def calc_vwap(df5: pd.DataFrame) -> float | None:
    """Calculate today's VWAP from 5-min bars."""
    if df5.empty or len(df5) < 2:
        return None
    today = datetime.now(ET).date()
    df_today = df5[df5.index.date == today]
    if df_today.empty:
        return None
    tp   = (df_today['high'] + df_today['low'] + df_today['close']) / 3
    vwap = float((tp * df_today['volume']).cumsum().iloc[-1] /
                 df_today['volume'].cumsum().iloc[-1])
    return round(vwap, 2)


# ── ATR ───────────────────────────────────────────────────

def calc_atr(df5: pd.DataFrame, period: int = 14) -> float:
    """ATR in points from recent 5-min bars."""
    if df5.empty or len(df5) < period:
        return 10.0   # default 10-point ATR for MNQ
    tr = pd.concat([
        df5['high'] - df5['low'],
        (df5['high'] - df5['close'].shift()).abs(),
        (df5['low']  - df5['close'].shift()).abs(),
    ], axis=1).max(axis=1)
    return round(float(tr.rolling(period).mean().iloc[-1]), 2)


# ── RSI ───────────────────────────────────────────────────

def calc_rsi(series: pd.Series, period: int = 14) -> float:
    if len(series) < period + 1:
        return 50.0
    delta  = series.diff()
    gain   = delta.clip(lower=0).rolling(period).mean()
    loss   = (-delta.clip(upper=0)).rolling(period).mean()
    rs     = gain / loss.replace(0, 1e-9)
    return round(float(100 - (100 / (1 + rs.iloc[-1]))), 1)


# ── Regime detection (NQ-native) ─────────────────────────

def get_regime(df5: pd.DataFrame | None = None) -> str:
    """
    Determine market regime from NQ/MNQ bars directly.
    No SPY proxy needed — NQ IS the market for this instrument.
    Returns: STRONG | NORMAL | WEAK
    Accepts pre-fetched df5 from run_scan() to avoid a redundant bridge call.
    """
    global _last_regime
    try:
        if df5 is None or df5.empty:
            df5 = get_bars(bar_size_min=5, days=2)
        if df5.empty or len(df5) < 6:
            return _last_regime

        price      = float(df5['close'].iloc[-1])
        vwap       = calc_vwap(df5)
        atr        = calc_atr(df5)
        rsi        = calc_rsi(df5['close'])

        # Today's bars only
        today      = datetime.now(ET).date()
        df_today   = df5[df5.index.date == today]

        # Price vs VWAP
        above_vwap = price > vwap if vwap else True

        # Short-term trend: last 3 bars
        if len(df_today) >= 3:
            trend = df_today['close'].iloc[-3:]
            trending_up   = trend.iloc[-1] > trend.iloc[0]
            trending_down = trend.iloc[-1] < trend.iloc[0]
        else:
            trending_up = trending_down = False

        # Day change vs prev close
        if len(df5) >= 2:
            prev_close = float(df5['close'].iloc[-2]) if len(df_today) < 2 else float(df5[df5.index.date < today]['close'].iloc[-1]) if len(df5[df5.index.date < today]) > 0 else float(df5['close'].iloc[-2])
            day_chg_pct = (price - prev_close) / prev_close * 100 if prev_close else 0
        else:
            day_chg_pct = 0

        # Choppiness: >40% bar reversals
        if len(df_today) >= 6:
            diffs  = df_today['close'].diff().dropna()
            flips  = sum(1 for i in range(1, len(diffs)) if diffs.iloc[i] * diffs.iloc[i-1] < 0)
            choppy = (flips / max(len(diffs), 1)) > 0.4 and abs(day_chg_pct) < 0.2
        else:
            choppy = False

        # ── Classify regime ───────────────────────────────
        if choppy:
            regime = 'NORMAL'
        elif (above_vwap and trending_up and day_chg_pct > 0.3
              and rsi < 80 and not choppy):
            regime = 'STRONG'
        elif (not above_vwap and trending_down and day_chg_pct < -0.3
              and rsi > 20):
            regime = 'WEAK'
        else:
            regime = 'NORMAL'

        _last_regime = regime
        return regime

    except Exception as e:
        log(f"get_regime error: {e}")
        return _last_regime


# ── Intraday signals ──────────────────────────────────────

def get_signals(df5: pd.DataFrame) -> dict:
    """
    Generate entry signals for MNQ from 5-min bars.
    Returns dict of signal flags and scores.
    """
    if df5.empty or len(df5) < 4:
        return {}

    price   = float(df5['close'].iloc[-1])
    vwap    = calc_vwap(df5)
    atr     = calc_atr(df5)
    rsi     = calc_rsi(df5['close'])
    session = get_session()

    sig = {
        'price':   price,
        'vwap':    vwap,
        'atr':     atr,
        'rsi':     rsi,
        'session': session,
    }

    if not vwap:
        return sig

    # ── Bull signals ──────────────────────────────────────

    # ORB break (above opening range high)
    sig['orb_bull'] = (_orb_set and price > _orb_high
                       and session == 'NY_OPEN')

    # VWAP reclaim (was below, now above)
    if len(df5) >= 3:
        prev_close = float(df5['close'].iloc[-2])
        sig['vwap_reclaim'] = (prev_close < vwap and price > vwap)
    else:
        sig['vwap_reclaim'] = False

    # Momentum: 3 consecutive up bars + above VWAP
    if len(df5) >= 4:
        last3 = df5['close'].iloc[-4:-1]
        sig['momentum_bull'] = (
            all(last3.iloc[i] < last3.iloc[i+1] for i in range(len(last3)-1))
            and price > vwap
        )
    else:
        sig['momentum_bull'] = False

    # Session open play (first bar direction after 9:30)
    today      = datetime.now(ET).date()
    df_today   = df5[df5.index.date == today]
    if len(df_today) >= 2:
        open_bar = float(df_today['close'].iloc[0])
        sig['open_play_bull'] = price > open_bar and price > vwap
        sig['open_play_bear'] = price < open_bar and price < vwap
    else:
        sig['open_play_bull'] = sig['open_play_bear'] = False

    # ── Bear signals ──────────────────────────────────────
    sig['orb_bear']       = (_orb_set and price < _orb_low
                              and session == 'NY_OPEN')
    sig['vwap_rejection'] = (len(df5) >= 3
                              and float(df5['close'].iloc[-2]) > vwap
                              and price < vwap)
    if len(df5) >= 4:
        last3 = df5['close'].iloc[-4:-1]
        sig['momentum_bear'] = (
            all(last3.iloc[i] > last3.iloc[i+1] for i in range(len(last3)-1))
            and price < vwap
        )
    else:
        sig['momentum_bear'] = False

    # ── Pre-market IB break (Cylinder 4) — macro event capture ───────────
    # On NFP/CPI/FOMC days: the 8:30am release creates a structural range.
    # Breaking pm_high/pm_low during RTH = continuation of macro move.
    sig['pm_bull'] = (_pm_ib_set and _pm_high is not None and price > _pm_high)
    sig['pm_bear'] = (_pm_ib_set and _pm_low  is not None and price < _pm_low)
    sig['pm_high'] = _pm_high
    sig['pm_low']  = _pm_low

    return sig


# ── Entry scoring ─────────────────────────────────────────

def grade_entry(sig: dict, regime: str, side: str) -> tuple[int, str]:
    """
    Score a futures entry. Returns (score, grade).
    A+ >= 80 | A >= 65 | B >= 50 | skip < 50
    """
    if not sig:
        return 0, 'SKIP'

    score   = 50   # baseline
    session = sig.get('session', 'NY_OPEN')
    rsi     = sig.get('rsi', 50)
    price   = sig.get('price', 0)
    vwap    = sig.get('vwap', 0)

    # ── Session bonus ─────────────────────────────────────
    session_bonus = {'NY_OPEN': +15, 'MIDDAY': +5, 'AFTERNOON': 0, 'LUNCH': -20}
    score += session_bonus.get(session, 0)

    # ── Regime alignment ──────────────────────────────────
    if side == 'LONG':
        if regime == 'STRONG':   score += 15
        elif regime == 'WEAK':   score -= 30
        # Hard gate: no longs in WEAK regime
        if regime == 'WEAK':
            return 0, 'SKIP'
    else:  # SHORT
        if regime == 'WEAK':     score += 15
        elif regime == 'STRONG': score -= 30
        if regime == 'STRONG':
            return 0, 'SKIP'

    # ── Macro bias gate ───────────────────────────────────
    # _daily_macro_bias set by Telegram (FUT BIAS LONG/SHORT) or auto-detected.
    # On macro days: only trade in the confirmed macro direction.
    if _daily_macro_bias == 'LONG'  and side == 'SHORT': return 0, 'SKIP'
    if _daily_macro_bias == 'SHORT' and side == 'LONG':  return 0, 'SKIP'

    if side == 'LONG':
        bull_signals = [sig.get('orb_bull'), sig.get('vwap_reclaim'),
                        sig.get('momentum_bull'), sig.get('open_play_bull'),
                        sig.get('pm_bull')]
        if not any(bull_signals):
            return 0, 'SKIP'   # session/RSI context alone cannot trigger an entry

        # ORB break
        if sig.get('orb_bull'):         score += 20
        # VWAP reclaim
        if sig.get('vwap_reclaim'):     score += 15
        # Momentum
        if sig.get('momentum_bull'):    score += 10
        # Session open play
        if sig.get('open_play_bull'):   score += 10
        # Pre-market IB break — strongest signal on macro days
        if sig.get('pm_bull'):          score += 25
        # RSI gate: skip if overbought
        if rsi > 80:
            return 0, 'SKIP'
        if rsi > 70:                    score -= 10
        if rsi < 45:                    score += 5

    else:  # SHORT
        bear_signals = [sig.get('orb_bear'), sig.get('vwap_rejection'),
                        sig.get('momentum_bear'), sig.get('open_play_bear'),
                        sig.get('pm_bear')]
        if not any(bear_signals):
            return 0, 'SKIP'   # session/RSI context alone cannot trigger an entry

        if sig.get('orb_bear'):         score += 20
        if sig.get('vwap_rejection'):   score += 15
        if sig.get('momentum_bear'):    score += 10
        if sig.get('open_play_bear'):   score += 10
        # Pre-market IB break (short)
        if sig.get('pm_bear'):          score += 25
        if rsi < 20:
            return 0, 'SKIP'
        if rsi < 30:                    score -= 10
        if rsi > 55:                    score += 5

    # ── Grade ─────────────────────────────────────────────
    if score >= 80:   return score, 'A+'
    if score >= 65:   return score, 'A'
    if score >= 50:   return score, 'B'
    return score, 'SKIP'


# ── Stop / target calculation (tick-based) ───────────────

def calc_session_rvol(df5: pd.DataFrame) -> float:
    """
    Current bar volume / average volume of TODAY's RTH bars so far (9:30am ET
    onward). NOT the same metric as calc_rvol_current() (preloaded time-of-day
    historical average) — this matches the decoder's (live_rule_sim.py) exact
    formula, because the RVOL_HIGH_THRESH/RVOL_LOW_THRESH tercile boundaries
    used by calc_sl_target were derived from the decoder's outcome data using
    this specific definition.
    Must filter to today's RTH bars only — get_bars() returns 2 raw days
    including illiquid overnight/Globex bars, which would drag the baseline
    average down and make every RTH bar look artificially "high volume"
    (caught during bug sweep Jul 6 2026 — do not merge with calc_rvol_current,
    and do not pass the raw unfiltered df5).
    """
    if df5.empty:
        return 1.0
    today   = datetime.now(ET).date()
    rth_start = ET.localize(datetime(today.year, today.month, today.day, 9, 30))
    today_bars = df5[df5.index >= rth_start]
    if len(today_bars) < 2:
        return 1.0
    avg_v = today_bars['volume'].mean()
    if not avg_v:
        return 1.0
    return float(today_bars['volume'].iloc[-1]) / float(avg_v)


def calc_sl_target(price: float, atr: float, side: str, rvol: float = 1.0) -> tuple[float, float]:
    """
    Calculate stop-loss and target in price terms.
    Point-based (not ATR-multiple) — MNQ's real daily range (447pt avg) is an
    order of magnitude bigger than its 5-min ATR (~39pt avg), so an ATR-multiple
    stop chases the wrong scale. RVOL-adaptive: elevated relative volume
    precedes bigger moves (confirmed via decoder outcome data — high-RVOL scans
    saw ~1.8x bigger forward swings than low-RVOL scans), so widen/tighten the
    base stop and target accordingly. `atr` param kept for signature compat /
    future use but no longer drives the base distance. `rvol` must come from
    calc_session_rvol(), not calc_rvol_current() — see that function's docstring.
    """
    tick = TICK_SIZE

    if rvol >= RVOL_HIGH_THRESH:
        mult = RVOL_WIDE_MULT
    elif rvol <= RVOL_LOW_THRESH:
        mult = RVOL_TIGHT_MULT
    else:
        mult = 1.0

    raw_stop   = BASE_STOP_PTS * mult
    raw_target = BASE_TARGET_PTS * mult

    # Round to nearest tick
    def round_tick(v):
        return round(round(v / tick) * tick, 2)

    if side == 'LONG':
        sl     = round_tick(price - raw_stop)
        target = round_tick(price + raw_target)
    else:
        sl     = round_tick(price + raw_stop)
        target = round_tick(price - raw_target)

    return sl, target


def calc_contracts(price: float, sl: float) -> int:
    """
    Risk-based contract sizing.
    contracts = floor(MAX_RISK / (stop_ticks × TICK_VALUE))
    """
    stop_pts   = abs(price - sl)
    stop_ticks = stop_pts / TICK_SIZE
    risk_per_c = stop_ticks * TICK_VALUE

    if risk_per_c <= 0:
        return 1

    contracts = int(MAX_RISK_PER_TRADE / risk_per_c)
    contracts = max(1, min(contracts, get_max_contracts()))
    return contracts


def load_avg_volumes():
    """Load per-time-slot average volume from market_data.db at startup (once).
    Builds _avg_vol_by_time dict keyed by ET time string '%H:%M'.
    """
    global _avg_vol_by_time
    try:
        conn = sqlite3.connect(MKT_DB_PATH)
        cutoff = (datetime.now() - timedelta(days=550)).strftime('%Y-%m-%d')
        df = pd.read_sql(
            "SELECT ts_utc, volume FROM futures_bars_5m WHERE symbol='MNQ' AND ts_utc >= ?",
            conn, params=[cutoff],
        )
        conn.close()
        if df.empty:
            log("RVOL: no bars found in market_data.db — RVOL scaling disabled")
            return
        df['ts'] = pd.to_datetime(df['ts_utc'], utc=True, format='ISO8601').dt.tz_convert(ET)
        df['slot'] = df['ts'].dt.strftime('%H:%M')
        _avg_vol_by_time = df.groupby('slot')['volume'].mean().to_dict()
        log(f"RVOL: loaded avg_vol for {len(_avg_vol_by_time)} slots ({len(df):,} bars)")
    except Exception as e:
        log(f"RVOL: load_avg_volumes failed — {e}. RVOL scaling disabled.")


def calc_rvol_current(df5: pd.DataFrame) -> float:
    """Current bar's volume relative to historical average for this time slot.
    Returns 1.0 (neutral) if data unavailable.
    """
    if df5.empty or not _avg_vol_by_time:
        return 1.0
    slot = df5.index[-1].strftime('%H:%M')
    avg  = _avg_vol_by_time.get(slot, 0)
    if avg <= 0:
        return 1.0
    return float(df5['volume'].iloc[-1]) / avg


def calc_ib_range_today(df5: pd.DataFrame) -> float:
    """Today's H-L range from 9:30am to now (proxy for 60-min IB at entry time)."""
    if df5.empty:
        return 0.0
    today    = datetime.now(ET).date()
    ib_start = ET.localize(datetime(today.year, today.month, today.day, 9, 30))
    bars     = df5[df5.index >= ib_start]
    if len(bars) < 2:
        return 0.0
    return float(bars['high'].max() - bars['low'].min())


def had_loss_today() -> bool:
    """True if any futures trade closed at a loss today (ET date)."""
    today = str(datetime.now(ET).date())
    conn  = sqlite3.connect(DB_PATH)
    count = conn.execute(
        "SELECT COUNT(*) FROM futures_trades "
        "WHERE entry_date=? AND pnl < 0 AND status='CLOSED' AND account_mode=?",
        (today, ACCOUNT_MODE),
    ).fetchone()[0]
    conn.close()
    return count > 0


def calc_contracts_dynamic(price: float, sl: float,
                            rvol: float, ib_range: float) -> int:
    """
    RVOL-based contract scaling (ported from backtest _dynamic_contracts()).
    Base = 1 (IBKR personal $15K account).
    Scale up on conviction; scale down after a loss; hard cap = IBKR_MAX_CONTRACTS (2).

    Tiers (additive):
      rvol ≥ 2×  → +1  (elevated participation)
      rvol ≥ 3×  → +1  (strong institutional interest)
      rvol ≥ 4×  → +1  (exceptional — capped to 2 by prop_rules anyway)
      ib_range ≥ 150pts → +1  (wide IB = structural range worth sizing into)
      had_loss_today     → -1  (capital protection after first hit)
    """
    n = 1
    if rvol >= 2.0:
        n += 1
    if rvol >= 3.0:
        n += 1
    if rvol >= 4.0:
        n += 1
    if ib_range >= 150.0:
        n += 1
    if had_loss_today():
        n -= 1
    return max(1, min(n, get_max_contracts(n)))   # hard cap: 2 for IBKR


# ── Database helpers ──────────────────────────────────────

def get_open_futures_trades() -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM futures_trades WHERE status='OPEN' AND account_mode=?",
        (ACCOUNT_MODE,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def log_futures_entry(symbol, contract, entry_price, contracts,
                      target, sl, setup_type, session, order_id,
                      side='LONG', stop_order_id=None) -> int:
    conn = sqlite3.connect(DB_PATH)
    now  = datetime.now(ET)
    cur  = conn.execute('''
        INSERT INTO futures_trades
        (symbol, contract, entry_date, entry_time, entry_price,
         contracts, side, target_price, stop_price,
         status, setup_type, session, order_id, stop_order_id,
         instrument, account_mode)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (symbol, contract, str(now.date()), now.strftime('%H:%M:%S'),
          entry_price, contracts, side, target, sl,
          'OPEN', setup_type, session, order_id, stop_order_id,
          SYMBOL, ACCOUNT_MODE))
    tid = cur.lastrowid
    conn.commit()
    conn.close()
    return tid


def _cancel_backup_stop(trade: dict):
    """Cancel the IBKR backup stop order for a trade. Call before every software exit."""
    sid = trade.get('stop_order_id')
    if not sid:
        return
    result = _bridge_post(f'/futures/cancel/{sid}', {})
    status = result.get('status', result.get('error', '?'))
    log(f"  Backup stop {sid} cancelled → {status}")


def log_futures_exit(trade_id, exit_price, exit_reason, pnl, pnl_ticks):
    conn = sqlite3.connect(DB_PATH)
    now  = datetime.now(ET)
    conn.execute('''
        UPDATE futures_trades
        SET exit_date=?, exit_time=?, exit_price=?, pnl=?,
            pnl_ticks=?, status='CLOSED', exit_reason=?
        WHERE id=?
    ''', (str(now.date()), now.strftime('%H:%M:%S'),
          exit_price, pnl, pnl_ticks, exit_reason, trade_id))
    conn.commit()
    conn.close()


def update_futures_stop(trade_id, new_stop):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE futures_trades SET stop_price=? WHERE id=?',
                 (new_stop, trade_id))
    conn.commit()
    conn.close()


def _update_backup_stop(trade: dict, new_sl: float):
    """Cancel old IBKR backup stop, place a new one at new_sl, update DB + trade dict.
    Called when BE or trail fires so the hardware stop tracks the software stop.
    Brief gap between cancel and replace is acceptable vs. having the stop
    permanently stranded at the original ATR level.
    """
    is_short  = trade.get('side') == 'SHORT'
    contracts = trade.get('contracts', 1)

    _cancel_backup_stop(trade)

    stop_side = 'BUY' if is_short else 'SELL'
    result    = _bridge_post('/futures/order', {
        'symbol':     SYMBOL,
        'qty':        contracts,
        'side':       stop_side,
        'order_type': 'STOP_MARKET',
        'stop_price': new_sl,
        'tif':        'GTC',
    })
    new_oid = (result or {}).get('order_id', '') or ''

    if not new_oid:
        alert = f"⚠️ BACKUP STOP REPLACE FAILED (trade {trade['id']}) — position unprotected! Manual stop needed at {new_sl}"
        log(alert)
        send_telegram(alert)
        return  # keep DB/dict unchanged so old (now cancelled) stop_order_id stays as a breadcrumb

    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE futures_trades SET stop_price=?, stop_order_id=? WHERE id=?',
                 (new_sl, new_oid, trade['id']))
    conn.commit()
    conn.close()

    trade['stop_order_id'] = new_oid   # keep trade dict in sync for this cycle


def get_futures_daily_pnl() -> float:
    today = str(datetime.now(ET).date())   # use ET date, consistent with log_futures_entry
    conn  = sqlite3.connect(DB_PATH)
    row   = conn.execute(
        "SELECT SUM(pnl) FROM futures_trades WHERE exit_date=? AND status='CLOSED' "
        "AND account_mode=? AND setup_type != 'RECONCILED'",
        (today, ACCOUNT_MODE)
    ).fetchone()
    conn.close()
    return round(float(row[0] or 0), 2)


def _get_all_time_futures_pnl() -> float:
    """Total realized P&L across all futures trades — used to reconcile ibkr_state balance.
    Excludes RECONCILED rows (manual broker-side fixes for bot bugs, e.g. duplicate-order
    cleanup) — those aren't strategy P&L and shouldn't count toward win-rate or balance math."""
    conn = sqlite3.connect(DB_PATH)
    row  = conn.execute(
        "SELECT SUM(pnl) FROM futures_trades WHERE status='CLOSED' "
        "AND account_mode=? AND setup_type != 'RECONCILED'",
        (ACCOUNT_MODE,)
    ).fetchone()
    conn.close()
    return round(float(row[0] or 0), 2)


# ── Place trade ───────────────────────────────────────────

def place_trade(side: str, sig: dict, regime: str,
                score: int, grade: str) -> bool:
    """
    Place a futures order via bridge. Returns True if submitted.
    Checks prop_rules before every order.
    """
    global _trading_paused

    # ── Pre-flight gates ──────────────────────────────────

    # 1. Prop rules gate (always first)
    allowed, reason = check_can_trade(unrealized_pnl=_get_open_unrealized())
    if not allowed:
        log(f"  BLOCKED by prop_rules: {reason}")
        return False

    # 2. Pause flag
    if _trading_paused:
        log("  BLOCKED: trading paused")
        return False

    # 3. Session gate
    if not is_entry_allowed():
        log(f"  BLOCKED: session={get_session()} — entries not allowed")
        return False

    # 4. Max open trades
    open_trades = get_open_futures_trades()
    if len(open_trades) >= MAX_OPEN_TRADES:
        log(f"  BLOCKED: {len(open_trades)} trades open (max {MAX_OPEN_TRADES})")
        return False

    # 5. Max daily trades (total entries today — open + closed)
    _conn = sqlite3.connect(DB_PATH)
    _daily_count = _conn.execute(
        "SELECT COUNT(*) FROM futures_trades WHERE entry_date=? AND account_mode=? AND status != 'CANCELLED'",
        (str(datetime.now(ET).date()), ACCOUNT_MODE)
    ).fetchone()[0]
    _conn.close()
    if _daily_count >= MAX_DAILY_TRADES:
        log(f"  BLOCKED: {_daily_count} trades entered today (max {MAX_DAILY_TRADES})")
        return False

    # 6. Cooldown after any exit (prevents immediate re-entry after a stop)
    if _last_exit_time is not None:
        _elapsed = (datetime.now(ET) - _last_exit_time).total_seconds() / 60
        if _elapsed < COOLDOWN_MINUTES:
            log(f"  BLOCKED: cooldown {_elapsed:.1f}min (need {COOLDOWN_MINUTES:.0f}min after last exit)")
            return False

    # 7. Daily P&L gates
    daily_pnl = get_futures_daily_pnl()
    if daily_pnl <= -MAX_DAILY_LOSS:
        log(f"  BLOCKED: daily loss ${daily_pnl:.0f}")
        return False
    if daily_pnl >= DAILY_PROFIT_TARGET:
        log(f"  SKIP: daily target ${DAILY_PROFIT_TARGET:.0f} already hit (${daily_pnl:.0f})")
        return False

    # 8. Bridge connected
    if not get_bridge_connected():
        log("  BLOCKED: futures bridge not connected")
        return False

    # ── Sizing ────────────────────────────────────────────
    price = get_live_price()
    if not price:
        log("  BLOCKED: no live price")
        return False

    # Cross-check: live price must be close to the scan's bar-close price.
    # A large gap means the bridge is serving a stale cached quote (e.g. after
    # a competing IBKR session drops market data streaming). Do not trade on
    # stale prices — the stop will be miscalculated and trigger immediately.
    scan_price = sig.get('price', 0)
    if scan_price and abs(price - scan_price) > MAX_PRICE_DIVERGENCE:
        log(f"  BLOCKED: stale price — scan={scan_price}, live={price} "
            f"(gap={abs(price-scan_price):.1f}pts > max {MAX_PRICE_DIVERGENCE}pts)")
        return False

    df5          = get_bars()
    atr          = calc_atr(df5) if not df5.empty else 10.0
    session_rvol = calc_session_rvol(df5)     # for stop/target sizing — matches decoder's rvol definition
    rvol         = calc_rvol_current(df5)     # for contract sizing — time-of-day historical baseline (unchanged)
    sl, target   = calc_sl_target(price, atr, side, session_rvol)

    # Sanity ceiling — catches broken/extreme data, not the intended 280-364pt
    # range (base 280pt x up to 1.3x RVOL-wide multiplier = 364pt max by design).
    # Raised from 150 Jul 6 2026 — that ceiling would have blocked every single
    # trade under the new wider-stop scheme.
    stop_pts = abs(price - sl)
    if stop_pts > 450.0:
        log(f"  SKIP: stop {stop_pts:.0f}pts > 450 max (broken/extreme data)")
        return False

    ib_range   = calc_ib_range_today(df5)
    contracts  = calc_contracts_dynamic(price, sl, rvol, ib_range)

    rr = abs(target - price) / abs(price - sl) if abs(price - sl) > 0 else 0
    # Use small tolerance to avoid floating-point false rejects at exactly MIN_RR
    if rr < MIN_RR - 0.01:
        log(f"  SKIP: R:R {rr:.2f} < min {MIN_RR}")
        return False

    # ── Submit order ──────────────────────────────────────
    order_side = 'BUY' if side == 'LONG' else 'SELL'
    result = _bridge_post('/futures/order', {
        'symbol':     SYMBOL,
        'qty':        contracts,
        'side':       order_side,
        'order_type': 'MARKET',
    })

    if result.get('status') != 'submitted':
        log(f"  Order failed: {result}")
        return False

    order_id = result.get('order_id', '')          # bridge returns 'order_id' not 'orderId'
    session  = get_session()
    # Setup label: priority order matches grade_entry bonus hierarchy
    if side == 'LONG':
        if sig.get('pm_bull'):          setup = 'PM_LONG'
        elif sig.get('orb_bull'):       setup = 'ORB_LONG'
        elif sig.get('vwap_reclaim'):   setup = 'VWAP_LONG'
        elif sig.get('momentum_bull'):  setup = 'MOM_LONG'
        else:                           setup = 'OPEN_LONG'
    else:
        if sig.get('pm_bear'):          setup = 'PM_SHORT'
        elif sig.get('orb_bear'):       setup = 'ORB_SHORT'
        elif sig.get('vwap_rejection'): setup = 'VWAP_SHORT'
        elif sig.get('momentum_bear'):  setup = 'MOM_SHORT'
        else:                           setup = 'OPEN_SHORT'

    # ── Backup IBKR stop order — placed immediately after entry ──────────────
    # Fixed at the initial hard stop. Never moved (trail managed in software).
    # Survives Mac sleep/crash. Cancelled by software exit before closing.
    stop_side = 'BUY' if side == 'SHORT' else 'SELL'
    stop_result = _bridge_post('/futures/order', {
        'symbol':     SYMBOL,
        'qty':        contracts,
        'side':       stop_side,
        'order_type': 'STOP_MARKET',
        'stop_price': sl,
        'tif':        'GTC',
    })
    stop_order_id = stop_result.get('order_id', '')
    if stop_order_id:
        log(f"  Backup stop placed: {stop_side} STOP_MARKET @ {sl} (order {stop_order_id})")
    else:
        log(f"  WARNING: Backup stop failed — {stop_result}. Position unprotected if service dies.")

    tid = log_futures_entry(
        symbol=SYMBOL, contract=result.get('contract_month', SYMBOL),
        entry_price=price, contracts=contracts,
        target=target, sl=sl, setup_type=setup,
        session=session, order_id=order_id, side=side,
        stop_order_id=stop_order_id or None,
    )

    risk_usd   = abs(price - sl) / TICK_SIZE * TICK_VALUE * contracts
    target_usd = abs(target - price) / TICK_SIZE * TICK_VALUE * contracts

    backup_line = (f"Backup SL: IBKR STOP @ {sl} (order {stop_order_id}) ✅"
                   if stop_order_id else "Backup SL: ⚠️ FAILED — software stop only")
    msg = (
        f"🔵 FUTURES {side} ENTRY\n"
        f"Symbol:    {SYMBOL} {result.get('contract_month', SYMBOL)}\n"
        f"Price:     {price}\n"
        f"Stop:      {sl}  (-${risk_usd:.0f})\n"
        f"Target:    {target}  (+${target_usd:.0f})\n"
        f"Contracts: {contracts} × MNQ  (RVOL={rvol:.1f}×  IB={ib_range:.0f}pts)\n"
        f"Setup:     {setup} | Grade: {grade} ({score}pts)\n"
        f"Session:   {session} | Regime: {regime}\n"
        f"R:R:       {rr:.1f}\n"
        f"{backup_line}"
    )
    log(msg)
    send_telegram(msg)
    return True


# ── Monitor positions (exit stack) ───────────────────────

def monitor_open_trades(regime: str = 'NORMAL'):
    """
    Check all open futures positions and apply exit stack.
    Runs every MONITOR_INTERVAL seconds.
    """
    trades = get_open_futures_trades()
    if not trades:
        return

    exits = []
    now   = datetime.now(ET)

    # Reuse bars cached by run_scan() (updated every 60s) — avoids a redundant
    # bridge call every 15s. VWAP changes slowly; 60s-old bars are fine for exits.
    df5      = _cached_df5
    vwap_now = calc_vwap(df5) if not df5.empty else None

    for trade in trades:
        tid       = trade['id']
        entry     = trade['entry_price']
        sl        = trade['stop_price']
        target    = trade['target_price']
        side      = trade.get('side', 'LONG')
        contracts = trade.get('contracts', 1)
        is_short  = (side == 'SHORT')

        price = get_live_price()
        if not price:
            continue

        # Track session high/low
        if is_short:
            _session_low[tid]  = min(_session_low.get(tid, price), price)
        else:
            _session_high[tid] = max(_session_high.get(tid, price), price)

        # P&L in ticks and dollars
        if is_short:
            pnl_pts  = entry - price
        else:
            pnl_pts  = price - entry
        pnl_ticks = pnl_pts / TICK_SIZE
        pnl_usd   = pnl_ticks * TICK_VALUE * contracts
        pnl_pct   = pnl_pts / entry * 100

        # ── Point-based profit protection (widened Jul 6 2026) ─────────────
        # PCT-based thresholds (1.5% = 450pts) never fire — points-based instead.
        # Target is now a 1500pt backstop; these trail tiers do the real exit work
        # so a genuine big swing isn't clipped early. Three tiers, each only
        # tightens — never loosens the stop.
        s_peak  = _session_low.get(tid, price) if is_short else _session_high.get(tid, price)

        # Tier 1 (+150pts): break-even — stop moves to entry, trade cannot lose.
        # Requires a real move first (was +30pts — pure noise triggered this
        # immediately, then any wiggle scratched the trade for a few dollars).
        if pnl_pts >= BE_ACTIVATE_PTS:
            be = round(entry + TICK_SIZE, 2) if not is_short else round(entry - TICK_SIZE, 2)
            if (not is_short and be > sl) or (is_short and be < sl):
                sl = be
                _update_backup_stop(trade, sl)   # moves IBKR hardware stop to BE level
                log(f"  {SYMBOL}{'SHORT' if is_short else ''}: BE stop → {sl} (+{pnl_pts:.0f}pts)")

        # Tier 2 (+250pts): trail 120pts behind session peak — let it run
        if pnl_pts >= TRAIL_WIDE_PTS:
            trail = round(s_peak - TRAIL_WIDE_GAP, 2) if not is_short else round(s_peak + TRAIL_WIDE_GAP, 2)
            if (not is_short and trail > sl) or (is_short and trail < sl):
                sl = trail
                _update_backup_stop(trade, sl)   # moves IBKR hardware stop to trail level
                log(f"  {SYMBOL}{'SHORT' if is_short else ''}: trail(120) → {sl} (+{pnl_pts:.0f}pts)")

        # Tier 3 (+400pts): tighten to 60pts behind peak — lock in most of the gain
        if pnl_pts >= TRAIL_TIGHT_PTS:
            trail = round(s_peak - TRAIL_TIGHT_GAP, 2) if not is_short else round(s_peak + TRAIL_TIGHT_GAP, 2)
            if (not is_short and trail > sl) or (is_short and trail < sl):
                sl = trail
                _update_backup_stop(trade, sl)   # moves IBKR hardware stop to tight trail level
                log(f"  {SYMBOL}{'SHORT' if is_short else ''}: trail(60) → {sl} (+{pnl_pts:.0f}pts)")

        # VWAP for exit decisions
        vwap = vwap_now   # pre-fetched once above the loop

        # ── Exit decisions ────────────────────────────────
        exit_reason = None

        # 1. Hard stop
        if is_short:
            if price >= sl:
                exit_reason = f'Short stop {sl} hit ({pnl_pct:+.1f}% / ${pnl_usd:+.0f})'
        else:
            if price <= sl:
                exit_reason = f'Stop {sl} hit ({pnl_pct:+.1f}% / ${pnl_usd:+.0f})'

        # 2. Circuit breaker — total daily P&L (realized + unrealized across all open trades).
        # Realized-only check lets an open position blow through the DLL undetected.
        # Using current price for all trades is correct — same instrument, same price.
        _total_unrealized = sum(
            (t['entry_price'] - price) / TICK_SIZE * TICK_VALUE * t.get('contracts', 1)
            if t.get('side') == 'SHORT' else
            (price - t['entry_price']) / TICK_SIZE * TICK_VALUE * t.get('contracts', 1)
            for t in trades
        )
        daily_pnl = get_futures_daily_pnl() + _total_unrealized
        if not exit_reason and daily_pnl <= -MAX_DAILY_LOSS:
            exit_reason = f'Daily loss circuit breaker (total): ${daily_pnl:.0f}'

        # 3. Target hit
        if not exit_reason and target:
            if is_short and price <= target:
                exit_reason = f'Target {target} hit ({pnl_pct:+.1f}% / ${pnl_usd:+.0f})'
            elif not is_short and price >= target:
                exit_reason = f'Target {target} hit ({pnl_pct:+.1f}% / ${pnl_usd:+.0f})'

        # 4. VWAP cross — exit losing longs that fall through VWAP
        if (not exit_reason and vwap and pnl_usd > 0 and is_market_open()):
            if not is_short and price < vwap:
                exit_reason = f'VWAP cross exit ({pnl_pct:+.1f}% / ${pnl_usd:+.0f})'
            elif is_short and price > vwap:
                exit_reason = f'VWAP cross exit (short) ({pnl_pct:+.1f}% / ${pnl_usd:+.0f})'

        # 5a. Elephant volume early exit — adverse zone with confirmed institutional selling
        # Logic mirrors _simulate_outcome() in elephant_backtest.py (live ↔ backtest parity):
        #   When adverse ≥ 50pt AND last 3 completed 5-min bars are BOTH high-vol
        #   (≥ 1.5× pre-entry baseline) AND each bar closes worse than the prior
        #   → exit before hard 100pt SL fires (avg saves ~$35 vs waiting for SL)
        is_elephant = trade.get('setup_type', '').startswith('ELEPHANT')
        if not exit_reason and is_elephant and df5 is not None and len(df5) >= 24:
            adv_pts = (entry - price) if not is_short else (price - entry)
            if adv_pts >= ELEPHANT_VOL_ADV_ZONE:
                # Use last 3 completed bars (skip [-1] which may be forming)
                recent = df5.iloc[-4:-1]
                # Baseline: 20 bars before the recent window
                baseline = df5.iloc[-24:-4]['volume']
                avg_vol  = float(baseline.mean()) if len(baseline) > 0 else 0.0
                if avg_vol > 0:
                    vols   = [float(b['volume']) for _, b in recent.iterrows()]
                    closes = [float(b['close'])  for _, b in recent.iterrows()]
                    all_high_vol = all(v >= ELEPHANT_VOL_BUILD_MULT * avg_vol for v in vols)
                    if not is_short:
                        all_worsening = all(closes[i] < closes[i-1] for i in range(1, len(closes)))
                    else:
                        all_worsening = all(closes[i] > closes[i-1] for i in range(1, len(closes)))
                    if all_high_vol and all_worsening:
                        exit_reason = (
                            f'Elephant vol early exit: {adv_pts:.0f}pt adverse, '
                            f'{ELEPHANT_VOL_CONSEC_BARS} high-vol bars '
                            f'(${pnl_usd:+.0f})'
                        )

        # 5. No-move exit (time-based — dead trade, free the slot)
        # Elephant trades get a wider window (ELEPHANT_TIMEOUT_MINS=180) because
        # LGR reversals sometimes take longer to develop than regular ORB/VWAP setups.
        if not exit_reason and get_session() not in ('EOD', 'CLOSED'):
            try:
                entry_dt = ET.localize(datetime.strptime(
                    f"{trade['entry_date']} {trade['entry_time']}", '%Y-%m-%d %H:%M:%S'
                ))
                age_min = (now - entry_dt).total_seconds() / 60
                is_elephant = trade.get('setup_type', '').startswith('ELEPHANT')
                timeout_min = ELEPHANT_TIMEOUT_MINS if is_elephant else NO_MOVE_MINUTES
                if (age_min >= timeout_min
                        and NO_MOVE_MIN_PTS <= pnl_pts <= NO_MOVE_MAX_PTS):
                    exit_reason = f'No-move exit ({age_min:.0f}min, {pnl_pts:+.0f}pts)'
            except Exception:
                pass

        # 6. EOD close (hard close at 4:00pm ET)
        h, m = now.hour, now.minute
        if not exit_reason and (h > HARD_CLOSE[0] or (h == HARD_CLOSE[0] and m >= HARD_CLOSE[1])):
            exit_reason = f'EOD hard close (4:00pm ET / 3:00pm CT)'

        # ── Execute exit ──────────────────────────────────
        if exit_reason:
            # Verify IBKR actually holds this position before placing an exit order.
            # If flat, the backup IBKR stop already filled — close the DB trade
            # only; sending a market order would create a ghost short/long.
            _ibkr_pos = _bridge_get('/futures/position')
            # Bridge error returns {} (not a list). If we can't verify, skip this
            # exit and retry next cycle — the IBKR backup stop protects us.
            # Do NOT set _last_exit_time on a skipped exit or the cooldown gate
            # would fire for a trade that hasn't actually closed yet.
            if not isinstance(_ibkr_pos, list):
                log(f"  trade {tid}: position check unavailable (bridge error) — skipping, retry next cycle")
                continue

            _ibkr_qty = 0.0
            for _p in _ibkr_pos:
                if _p.get('symbol') == SYMBOL:
                    _ibkr_qty = float(_p.get('qty', 0))
                    break
            _position_held = (_ibkr_qty > 0) if not is_short else (_ibkr_qty < 0)

            if not _position_held:
                # Check for race-condition orphan: backup stop AND software exit both fired
                # within the same cycle, leaving the position inverted (e.g. intended LONG
                # but qty=-2 in IBKR).  This happens when the software places a SELL market
                # order AND the IBKR backup stop also executes before the cancel reaches IBKR.
                if _ibkr_qty != 0.0:
                    orphan_side = 'BUY' if _ibkr_qty < 0 else 'SELL'
                    orphan_qty  = abs(int(_ibkr_qty))
                    log(f"  ⚠️  RACE CONDITION: IBKR qty={_ibkr_qty} — double-exit created orphan; flattening with {orphan_side} ×{orphan_qty}")
                    _r = _bridge_post('/futures/order', {'symbol': SYMBOL, 'qty': orphan_qty, 'side': orphan_side, 'order_type': 'MARKET'})
                    _alert = (
                        f"⚠️ DOUBLE-EXIT RACE CONDITION\n"
                        f"IBKR qty={_ibkr_qty} after trade {tid} stop — orphan auto-flattened\n"
                        f"Action: {orphan_side} ×{orphan_qty} {SYMBOL} MARKET"
                    )
                    log(f"  Flatten order placed: {_r}")
                    send_telegram(_alert)
                log(f"  IBKR flat (qty={_ibkr_qty}) — backup stop filled, closing DB only")
                _cancel_backup_stop(trade)   # cancel pending stop if any remains
                backup_stop_px = trade.get('stop_price', '?')
                # Try to get actual fill price from the IBKR stop order
                stop_oid = trade.get('stop_order_id')
                actual_fill = None
                if stop_oid:
                    try:
                        sr = _bridge_get(f'/order/{stop_oid}/status')
                        fp = sr.get('avgFillPrice') if sr else None
                        if fp and float(fp) > 0:
                            actual_fill = float(fp)
                    except Exception:
                        pass
                if actual_fill:
                    price = actual_fill
                    pnl_pts  = price - entry if not is_short else entry - price
                    pnl_ticks = pnl_pts / TICK_SIZE
                    pnl_usd   = pnl_ticks * TICK_VALUE * contracts
                    exit_display = str(actual_fill)
                    price_note = f"IBKR backup stop: {backup_stop_px} | Actual fill"
                else:
                    exit_display = f"~{price} (est.)"
                    price_note = f"IBKR backup stop: {backup_stop_px} | Est. from current price"
                log_futures_exit(tid, price, f"[backup-stop] IBKR stop @ {backup_stop_px} filled",
                                 round(pnl_usd, 2), round(pnl_ticks, 1))
                record_trade_pnl(pnl_usd)
                emoji = '✅' if pnl_usd > 0 else '🔴'
                msg = (
                    f"{emoji} FUTURES EXIT (IBKR stop filled)\n"
                    f"{SYMBOL} {side} × {contracts}\n"
                    f"Entry: {entry} → Exit: {exit_display}\n"
                    f"P&L: ${pnl_usd:+.2f} ({pnl_ticks:+.1f} ticks)\n"
                    f"{price_note}"
                )
                log(msg)
                send_telegram(msg)
                global _last_exit_time
                _last_exit_time = datetime.now(ET)
                exits.append({'tid': tid, 'pnl': pnl_usd})
                for d in (_session_high, _session_low, _price_history, _partial_done):
                    d.pop(tid, None)
                continue

            # Position confirmed on IBKR — cancel backup stop then place software exit
            _cancel_backup_stop(trade)
            cover_side = 'BUY' if is_short else 'SELL'
            exit_result = _bridge_post('/futures/order', {
                'symbol':     SYMBOL,
                'qty':        contracts,
                'side':       cover_side,
                'order_type': 'MARKET',
            })

            if exit_result.get('status') != 'submitted':
                # Exit order failed: re-place backup stop so position stays protected,
                # then skip closing the DB record — next monitor cycle will retry.
                log(f"  ⚠️ EXIT ORDER FAILED: {exit_result} — re-placing backup stop, will retry")
                stop_side = 'BUY' if is_short else 'SELL'
                _bridge_post('/futures/order', {
                    'symbol': SYMBOL, 'qty': contracts, 'side': stop_side,
                    'order_type': 'STOP_MARKET', 'stop_price': sl, 'tif': 'GTC',
                })
                send_telegram(f"⚠️ FUTURES EXIT FAILED (trade {tid})!\nRetrying next cycle. Check IBKR manually if persists.")
                continue

            _last_exit_time = datetime.now(ET)

            log_futures_exit(tid, price, exit_reason, round(pnl_usd, 2),
                             round(pnl_ticks, 1))
            record_trade_pnl(pnl_usd)

            emoji  = '✅' if pnl_usd > 0 else '🔴'
            msg = (
                f"{emoji} FUTURES EXIT\n"
                f"{SYMBOL} {side} × {contracts}\n"
                f"Entry: {entry} → Exit: {price}\n"
                f"P&L: ${pnl_usd:+.2f} ({pnl_ticks:+.1f} ticks)\n"
                f"Reason: {exit_reason}"
            )
            log(msg)
            send_telegram(msg)
            exits.append({'tid': tid, 'pnl': pnl_usd})

            # Cleanup state
            for d in (_session_high, _session_low, _price_history, _partial_done):
                d.pop(tid, None)

    return exits


def _get_open_unrealized() -> float:
    """Quick estimate of unrealized P&L on open positions."""
    trades = get_open_futures_trades()
    total  = 0.0
    price  = get_live_price() or 0
    for t in trades:
        c = t.get('contracts', 1)
        if t.get('side') == 'SHORT':
            total += (t['entry_price'] - price) / TICK_SIZE * TICK_VALUE * c
        else:
            total += (price - t['entry_price']) / TICK_SIZE * TICK_VALUE * c
    return round(total, 2)


# ── ELEPHANT TRADE (Liquidity Grab Reversal) ─────────────

def _classify_elephant_day(df5: pd.DataFrame) -> str:
    """
    Classify today's session using the first 15-min RTH opening bar body.
    The opening bar body predicts institutional order flow bias for the day.
    Returns: 'STRONG_BULL' | 'NONE'
    Only STRONG_BULL days are eligible for Elephant LONG entries.
    """
    today    = datetime.now(ET).date()
    rth_open = ET.localize(datetime(today.year, today.month, today.day, 9, 30))
    rth_15m  = ET.localize(datetime(today.year, today.month, today.day, 9, 45))

    bars_15 = df5[(df5.index >= rth_open) & (df5.index < rth_15m)]
    if len(bars_15) < 3:
        return 'NONE'

    body = float(bars_15['close'].iloc[-1]) - float(bars_15['open'].iloc[0])
    if body >= ELEPHANT_BODY_STRONG:
        return 'STRONG_BULL'
    return 'NONE'


def _scan_elephant(df5: pd.DataFrame) -> dict | None:
    """
    Scan for a Liquidity Grab Reversal (LGR) entry signal.
    An LGR occurs when algos sweep stop-loss clusters at the flush extreme
    then immediately reverse — we enter 10pts from the extreme to ride the reversal.

    LONG only on STRONG_BULL days:
      - Day opens with first-15min body ≥ 100pt upward (institutional bias UP)
      - Within session: price flushes DOWN ≥ 150pt in a 60-min rolling window
      - ES macro filter: if ES also moved ≥ 25pt in same window → skip (true macro, not sweep)
      - Entry: current price within 10–60pt above flush extreme
      - SL: 100pt below flush extreme | Target: 150pt above entry

    Returns signal dict or None.
    """
    global _elephant_day_type, _elephant_trades_today, _elephant_flush_ids

    if not ELEPHANT_ENABLED:
        return None

    # Classify day on first scan after 9:45am (needs 3 × 5-min bars)
    if _elephant_day_type == 'NONE':
        _elephant_day_type = _classify_elephant_day(df5)
        if _elephant_day_type == 'STRONG_BULL':
            log(f"  🐘 Elephant day: STRONG_BULL — watching for flush")

    if _elephant_day_type != 'STRONG_BULL':
        return None

    now_et = datetime.now(ET)
    if now_et.hour >= ELEPHANT_NOON_CUTOFF_ET:
        return None

    if _elephant_trades_today >= ELEPHANT_MAX_STRONG:
        return None

    today      = now_et.date()
    rth_open   = ET.localize(datetime(today.year, today.month, today.day, 9, 30))
    today_bars = df5[df5.index >= rth_open].copy()
    if len(today_bars) < ELEPHANT_LOOKBACK_BARS:
        return None

    window        = today_bars.iloc[-ELEPHANT_LOOKBACK_BARS:]
    current_price = float(today_bars['close'].iloc[-1])
    w_high        = float(window['high'].max())
    w_low         = float(window['low'].min())
    flush_depth   = w_high - w_low

    if flush_depth < ELEPHANT_FLUSH_STRONG:
        return None

    flush_extreme = w_low
    flush_bar_ts  = str(window['low'].idxmin())
    entry_level   = flush_extreme + ELEPHANT_ENTRY_CONF

    if not (entry_level <= current_price <= flush_extreme + 50):
        return None
    if flush_bar_ts in _elephant_flush_ids:
        return None

    # ES filter: skip if ES confirmed the same flush (macro move, not algo sweep)
    es_df = _get_es_bars()
    if not es_df.empty:
        es_today = es_df[es_df.index >= rth_open]
        if len(es_today) >= 2:
            es_win  = es_today.iloc[-ELEPHANT_LOOKBACK_BARS:]
            es_drop = float(es_win['high'].max()) - float(es_win['low'].min())
            if es_drop >= ELEPHANT_ES_MOVE_SKIP:
                log(f"  Elephant LONG skip: ES confirmed flush ({es_drop:.0f}pts ≥ {ELEPHANT_ES_MOVE_SKIP})")
                return None

    # Overnight level bonus (informational only — not a gate)
    bonus_note = ''
    if _pm_low is not None and abs(flush_extreme - _pm_low) <= 25:
        bonus_note = f'⚡ Flush at PM low ({_pm_low:.0f}) — overnight level confluence'

    sl     = round(flush_extreme - ELEPHANT_STOP_PTS, 2)
    target = round(entry_level + ELEPHANT_TARGET_PTS, 2)
    return {
        'direction':     'LONG',
        'flush_extreme': flush_extreme,
        'flush_bar_ts':  flush_bar_ts,
        'entry_level':   entry_level,
        'sl':            sl,
        'target':        target,
        'flush_depth':   flush_depth,
        'bonus_note':    bonus_note,
        'day_type':      _elephant_day_type,
    }


def _enter_elephant(signal: dict) -> bool:
    """
    Place a MARKET entry for an elephant (LGR) trade.
    Uses fixed 1 contract (no RVOL scaling — LGR is a structural play, not volume-driven).
    Setup type: 'ELEPHANT_LONG' in futures_trades.
    Monitored by the standard monitor_open_trades() exit stack + longer ELEPHANT_TIMEOUT_MINS.
    """
    global _elephant_trades_today, _elephant_flush_ids

    direction    = signal['direction']
    sl           = signal['sl']
    target       = signal['target']
    day_type     = signal['day_type']
    flush_depth  = signal['flush_depth']
    bonus_note   = signal.get('bonus_note', '')
    flush_bar_ts = signal['flush_bar_ts']

    # Pre-flight checks (subset of place_trade — elephants bypass RVOL/IB gates)
    allowed, reason = check_can_trade(unrealized_pnl=_get_open_unrealized())
    if not allowed:
        log(f"  🐘 Elephant BLOCKED by prop_rules: {reason}")
        return False

    if _trading_paused:
        log("  🐘 Elephant BLOCKED: trading paused")
        return False

    open_trades = get_open_futures_trades()
    if len(open_trades) >= MAX_OPEN_TRADES:
        log(f"  🐘 Elephant BLOCKED: {len(open_trades)} trades open (max {MAX_OPEN_TRADES})")
        return False

    _econn = sqlite3.connect(DB_PATH)
    _edaily = _econn.execute(
        "SELECT COUNT(*) FROM futures_trades WHERE entry_date=? AND account_mode=? AND status != 'CANCELLED'",
        (str(datetime.now(ET).date()), ACCOUNT_MODE)
    ).fetchone()[0]
    _econn.close()
    if _edaily >= MAX_DAILY_TRADES:
        log(f"  🐘 Elephant BLOCKED: {_edaily} trades entered today (max {MAX_DAILY_TRADES})")
        return False

    daily_pnl = get_futures_daily_pnl()
    if daily_pnl <= -MAX_DAILY_LOSS:
        log(f"  🐘 Elephant BLOCKED: daily loss ${daily_pnl:.0f}")
        return False

    if not get_bridge_connected():
        log("  🐘 Elephant BLOCKED: bridge not connected")
        return False

    price = get_live_price()
    if not price:
        log("  🐘 Elephant BLOCKED: no live price")
        return False

    contracts  = 1   # fixed — no RVOL scaling for structural LGR plays
    order_side = 'BUY' if direction == 'LONG' else 'SELL'

    result = _bridge_post('/futures/order', {
        'symbol':     SYMBOL,
        'qty':        contracts,
        'side':       order_side,
        'order_type': 'MARKET',
    })
    if result.get('status') != 'submitted':
        log(f"  🐘 Elephant order failed: {result}")
        return False

    order_id = result.get('order_id', '')

    # Backup IBKR hardware stop
    stop_side   = 'BUY' if direction == 'SHORT' else 'SELL'
    stop_result = _bridge_post('/futures/order', {
        'symbol':     SYMBOL,
        'qty':        contracts,
        'side':       stop_side,
        'order_type': 'STOP_MARKET',
        'stop_price': sl,
        'tif':        'GTC',
    })
    stop_order_id = stop_result.get('order_id', '')
    if stop_order_id:
        log(f"  🐘 Backup stop placed: {stop_side} STOP_MARKET @ {sl} (order {stop_order_id})")
    else:
        log(f"  🐘 WARNING: Backup stop failed — {stop_result}")

    setup_type = f'ELEPHANT_{direction}'
    tid = log_futures_entry(
        symbol=SYMBOL, contract=result.get('contract_month', SYMBOL),
        entry_price=price, contracts=contracts,
        target=target, sl=sl, setup_type=setup_type,
        session=get_session(), order_id=order_id, side=direction,
        stop_order_id=stop_order_id or None,
    )

    _elephant_trades_today += 1
    _elephant_flush_ids.add(flush_bar_ts)

    risk_usd   = (ELEPHANT_ENTRY_CONF + ELEPHANT_STOP_PTS) * (TICK_VALUE / TICK_SIZE)   # 110pts × $2/pt = $220
    target_usd = ELEPHANT_TARGET_PTS * (TICK_VALUE / TICK_SIZE)                          # 150pts × $2/pt = $300
    rr         = ELEPHANT_TARGET_PTS / (ELEPHANT_ENTRY_CONF + ELEPHANT_STOP_PTS)         # 1.36

    backup_line = (f"Backup SL: IBKR STOP @ {sl} ✅" if stop_order_id
                   else "Backup SL: ⚠️ FAILED — software stop only")
    msg = (
        f"🐘 ELEPHANT {direction} ENTRY\n"
        f"Symbol:  {SYMBOL}\n"
        f"Price:   {price}\n"
        f"Stop:    {sl}  (-${risk_usd:.0f})\n"
        f"Target:  {target}  (+${target_usd:.0f})\n"
        f"Setup:   {setup_type} | Day: {day_type}\n"
        f"Flush:   {flush_depth:.0f}pts LGR sweep → entry {ELEPHANT_ENTRY_CONF:.0f}pts in\n"
        f"R:R:     {rr:.1f}  |  Trade #{tid}\n"
        + (f"{bonus_note}\n" if bonus_note else '')
        + backup_line
    )
    log(msg)
    send_telegram(msg)
    return True


# ── Main scan loop ────────────────────────────────────────

def run_scan():
    """5-min scan: check regime, signals, enter if qualified."""
    global _confirmed_scans, _regime_scan_counts, _cached_df5
    global _ib_kind, _ib_kind_set, _day_regime, _large_ib_delayed

    if date.today() in CME_HOLIDAYS_2026:
        log("Market holiday — scan skipped")
        return

    if not get_bridge_connected():
        log("Bridge disconnected — skipping scan")
        return

    if _trading_paused:
        log("Trading paused — scan skipped")
        return

    session = get_session()
    log(f"--- SCAN | session={session} | {datetime.now(ET).strftime('%H:%M')} ---")

    # Fetch bars once; shared with run_monitor() via _cached_df5 to avoid
    # a redundant bridge call every 15s in the monitor loop.
    df5 = get_bars(bar_size_min=5, days=2)
    _cached_df5 = df5
    if not df5.empty:
        update_orb(df5)
        update_premarket_ib()   # sets pm_high/pm_low from 8:30–9:30am bars

    # Overnight position classifier — compute once after RTH opens (first scan with bars)
    compute_overnight_bias()

    # Regime — pass pre-fetched bars to avoid a second bridge call
    regime = get_regime(df5)
    _regime_scan_counts[regime] = _regime_scan_counts.get(regime, 0) + 1
    _confirmed_scans = _regime_scan_counts.get(regime, 0)
    log(f"Regime: {regime} (×{_confirmed_scans}) | Daily P&L: ${get_futures_daily_pnl():+.0f}")

    if not is_entry_allowed():
        log(f"Session {session} — no new entries")
        return

    # Compute now_et once — used by IB window, pm_ib hold, 14:00 gate, and elephant scan.
    # Must be before any of those gates so all use the same timestamp.
    now_et   = datetime.now(ET)
    today_et = now_et.date()

    # ── IB window gate: no entries until Initial Balance is fully established ──
    ib_ready_at = ET.localize(datetime(today_et.year, today_et.month, today_et.day, 10, 30))
    if now_et < ib_ready_at:
        mins_left = int((ib_ready_at - now_et).total_seconds() / 60) + 1
        log(f"IB window not complete — waiting for 10:30am ET ({mins_left}min remaining)")
        return

    # ── IB classification (computed once at 10:30, sticky all day) ───────────
    # Classifies day type for hero weighting + directional bias for SHORT gate bypass.
    if not _ib_kind_set and not df5.empty:
        ib_range = calc_ib_range_today(df5)
        if ib_range >= 50.0:   # only classify when IB has meaningful range
            today_d = today_et
            df_today = df5[df5.index.date == today_d]
            if not df_today.empty:
                ib_hi  = float(df_today['high'].max())
                ib_lo  = float(df_today['low'].min())
                ib_cl  = float(df_today['close'].iloc[-1])
                ib_rng = ib_hi - ib_lo
                ib_mid = 0.5   # default: rotational
                if ib_rng > 0:
                    ib_mid = (ib_cl - ib_lo) / ib_rng
                    if ib_mid < 0.25:
                        _ib_kind = 'BEAR_DIRECTIONAL'
                    elif ib_mid > 0.75:
                        _ib_kind = 'BULL_DIRECTIONAL'
                    else:
                        _ib_kind = 'ROTATIONAL'
                _day_regime  = detect_regime(ib_range)
                # Large IB gate: IB > 200pts at 10:30 → first entry delayed to 10:45
                # (10:30 on a freshly-formed large IB has 35.7% WR vs 50.8% at 10:45)
                if ib_range > 200.0:
                    _large_ib_delayed = True
                    log(f"Large IB gate: {ib_range:.0f}pts > 200 — delaying first entry to 10:45")
                _ib_kind_set = True
                log(f"IB classified: {_ib_kind} | regime={_day_regime} | range={ib_range:.0f}pts | mid={ib_mid:.2f}")
                send_telegram(
                    f"📊 IB formed: {_ib_kind} | {_day_regime}\n"
                    f"Range={ib_range:.0f}pts  mid={ib_mid:.2f}\n"
                    f"{'⏳ Large IB — first entry delayed to 10:45' if _large_ib_delayed else ''}"
                )

    # ── Large IB delay gate ───────────────────────────────────────────────────
    if _large_ib_delayed:
        ib_ready_1045 = ET.localize(datetime(today_et.year, today_et.month, today_et.day, 10, 45))
        if now_et < ib_ready_1045:
            log(f"Large IB delay — waiting for 10:45 ET")
            return
        _large_ib_delayed = False   # cleared after first scan past 10:45

    # ── pm_ib hold: give user 5 min to send FUT BIAS after macro range is set ──
    # Only applies in the morning window (before 11am ET). After 11am the pre-market
    # IB data is stale and restarts should not re-trigger the hold — entries would
    # be blocked for 5 min after every mid-day restart, which is unacceptable.
    if (_pm_ib_set and _pm_ib_set_time is not None and now_et.hour < 11):
        elapsed = (now_et - _pm_ib_set_time).total_seconds()
        if elapsed < 300:
            remaining = int(300 - elapsed)
            log(f"pm_ib hold — {remaining}s remaining. Send FUT BIAS LONG/SHORT if needed.")
            return

    # ── No-entry-after 14:00 gate (tc_champion v3.2 parity) ─────────────────
    # Backtest cuts off entries at 2pm ET. Afternoon 2pm-3:30pm has lower WR
    # and is not in the validated backtest window.
    if now_et.hour >= 14:
        log(f"No-entry-after gate (14:00 ET) — monitoring only")
        return

    # Open trades count — checked once, shared by elephant + regular entry.
    # Must be before get_signals() so the elephant scan doesn't get skipped
    # on days where regular signals are absent (different signal sources).
    open_trades = get_open_futures_trades()
    if len(open_trades) >= MAX_OPEN_TRADES:
        log(f"Max trades open ({len(open_trades)}) — skip")
        return

    # ── ELEPHANT TRADE scan (LGR) — before regular signal checks ──────────────
    # Elephant entries are structural (flush reversal) — they don't need an
    # ORB/VWAP signal. Running before get_signals() ensures they aren't silently
    # skipped on days where get_signals() returns None (no regular setup present).
    if ELEPHANT_ENABLED and not _trading_paused and now_et.hour < ELEPHANT_NOON_CUTOFF_ET:
        elephant_sig = _scan_elephant(df5)
        if elephant_sig:
            log(f"  🐘 Elephant signal: {elephant_sig['direction']} | "
                f"flush {elephant_sig['flush_depth']:.0f}pts | "
                f"day={elephant_sig['day_type']}")
            _enter_elephant(elephant_sig)

    # Get signals (for regular ORB/VWAP/momentum entries)
    sig = get_signals(df5)
    if not sig:
        log("No signals generated")
        return

    price = sig.get('price', 0)
    vwap  = sig.get('vwap', 0)
    log(f"Price: {price} | VWAP: {vwap} | RSI: {sig.get('rsi',0):.0f} | "
        f"ORB: {'set' if _orb_set else 'pending'}")

    # Shadow-only: log the raw 3-bar momentum-vs-VWAP signal (decoder's earliest
    # trigger, no quality gates) so we can measure lead time vs the real ENTER
    # once it qualifies. Never blocks or places a trade — logged for comparison
    # only. See futures/gate_audit.py --leadtime.
    try:
        log_shadow_signal('IBKR', 'MNQ', df5['close'].tail(3).tolist(), vwap, price, session)
    except Exception:
        pass

    # ── RVOL + IB range gates ────────────────────────────────────────────────
    # min_rvol=0.3: 550-day averages include 2021-22 high-vol era; June 2026
    # RTH slots run 0.3-0.6× that baseline — structurally lower, not thin.
    # 0.3 blocks truly dead scans (weekend/holiday test: 0.17×) without
    # filtering normal trading days. tc_champion uses 1.0 (its own backtest).
    _scan_rvol     = calc_rvol_current(df5)
    _scan_ib_range = calc_ib_range_today(df5)
    if _scan_rvol < 0.3:
        log(f"Low RVOL ({_scan_rvol:.2f}× < 0.3) — skip entry attempt")
        try: log_block('IBKR', 'MNQ', 'BOTH', 'RVOL', f'{_scan_rvol:.2f}x', price, session)
        except Exception: pass
        return
    if _scan_ib_range > 0 and _scan_ib_range < 50.0:
        log(f"Thin IB ({_scan_ib_range:.0f}pts < 50 min) — skip entry attempt")
        try: log_block('IBKR', 'MNQ', 'BOTH', 'IB_RANGE', f'{_scan_ib_range:.0f}pts', price, session)
        except Exception: pass
        return

    # ── Overnight skip zone gate ──────────────────────────────
    # overnight_position in [0.20, 0.40): moderate bearish lean — IB breakouts
    # have 18–36% WR in this zone (backtest data, tc_champion v3.2).
    # User can override by sending FUT BIAS LONG/SHORT via Telegram.
    if _overnight_skip_day and _daily_macro_bias == 'BOTH':
        log(f"Overnight skip zone (pos={_overnight_position}) — no entries today. "
            f"Override: FUT BIAS LONG or FUT BIAS SHORT")
        try: log_block('IBKR', 'MNQ', 'BOTH', 'OVN_SKIP', f'pos={_overnight_position:.3f}', price, session)
        except Exception: pass
        return

    # ── Hero gate (Phase 5 regime-aware scoring) ─────────────────────────────
    # Heroes vote on entry quality using regime-weighted scoring.
    # Regime detected once at IB formation (10:30); CHOPPY is default before that.
    hero_regime = _day_regime if _day_regime else 'CHOPPY'
    try:
        bars_hist = get_bars(bar_size_min=5, days=2)
        prev_rth  = bars_hist[bars_hist.index.date < today_et] if not bars_hist.empty else pd.DataFrame()
        atr_now   = calc_atr(df5)
        price_now = sig.get('price', 0)
    except Exception:
        bars_hist = pd.DataFrame()
        prev_rth  = pd.DataFrame()
        atr_now   = 10.0
        price_now = sig.get('price', 0)

    # ── Try LONG entry ─────────────────────────────────────
    if regime in ('STRONG', 'NORMAL'):
        score, grade = grade_entry(sig, regime, 'LONG')
        if grade not in ('A+', 'A'):
            try: log_block('IBKR', 'MNQ', 'LONG', 'GRADE', f'{grade}({score})', price_now, session)
            except Exception: pass
        else:
            # Hero gate: score entry with regime-aware weights
            h_score, h_flags = score_entry_regime(price_now, atr_now, 'LONG',
                                                   bars_hist, prev_rth, hero_regime)
            contracts_hero = contracts_from_regime_score(h_score, hero_regime, 2)
            if contracts_hero == 0:
                log(f"LONG signal: {grade} ({score}pts) — HERO SKIP "
                    f"(weighted={h_score}, regime={hero_regime}, flags={h_flags})")
                try: log_block('IBKR', 'MNQ', 'LONG', 'HERO', f'score={h_score}/{hero_regime}', price_now, session)
                except Exception: pass
            else:
                # A_ext gate REMOVED Jul 6 2026 — gate_audit scored 33% accuracy
                # / REMOVE verdict on live IBKR SHORT; conflicts with wide-stop scheme.
                log(f"LONG signal: {grade} ({score}pts) | heroes={h_score}/{hero_regime} — entering")
                if place_trade('LONG', sig, regime, score, grade):
                    try: log_enter('IBKR', 'MNQ', 'LONG', f'{grade}({score})', price_now, session)
                    except Exception: pass
    else:
        try: log_block('IBKR', 'MNQ', 'LONG', 'REGIME', regime, price_now, session)
        except Exception: pass

    # ── Try SHORT entry ────────────────────────────────────
    # short_allowed:
    #   1. WEAK regime confirmed ≥3 consecutive scans (noisy market turned bearish)
    #   2. Overnight macro bias is SHORT (NFP/CPI directional day)
    #   3. BEAR_DIRECTIONAL IB (market closed near IB low — day already showed its hand)
    short_allowed = (
        (regime == 'WEAK' and _confirmed_scans >= 3) or
        (_daily_macro_bias == 'SHORT') or
        (_ib_kind == 'BEAR_DIRECTIONAL')
    )
    if short_allowed:
        score, grade = grade_entry(sig, regime, 'SHORT')
        if grade not in ('A+', 'A'):
            try: log_block('IBKR', 'MNQ', 'SHORT', 'GRADE', f'{grade}({score})', price_now, session)
            except Exception: pass
        else:
            # Hero gate for SHORT
            h_score, h_flags = score_entry_regime(price_now, atr_now, 'SHORT',
                                                   bars_hist, prev_rth, hero_regime)
            contracts_hero = contracts_from_regime_score(h_score, hero_regime, 2)
            if contracts_hero == 0:
                log(f"SHORT signal: {grade} ({score}pts) — HERO SKIP "
                    f"(weighted={h_score}, regime={hero_regime}, flags={h_flags})")
                try: log_block('IBKR', 'MNQ', 'SHORT', 'HERO', f'score={h_score}/{hero_regime}', price_now, session)
                except Exception: pass
            else:
                # A_ext gate REMOVED Jul 6 2026 — gate_audit scored 33% accuracy
                # / REMOVE verdict on live IBKR SHORT; conflicts with wide-stop scheme.
                log(f"SHORT signal: {grade} ({score}pts) | heroes={h_score}/{hero_regime} "
                    f"ib={_ib_kind} — entering")
                if place_trade('SHORT', sig, regime, score, grade):
                    try: log_enter('IBKR', 'MNQ', 'SHORT', f'{grade}({score})', price_now, session)
                    except Exception: pass
    else:
        try: log_block('IBKR', 'MNQ', 'SHORT', 'REGIME', regime, price_now, session)
        except Exception: pass


def run_monitor():
    """Fast monitor loop — runs every MONITOR_INTERVAL seconds."""
    regime = _last_regime
    exits  = monitor_open_trades(regime)
    if exits:
        daily = get_futures_daily_pnl()
        log(f"Monitor: {len(exits)} exit(s) | Daily P&L: ${daily:+.0f}")


# ── Daily routines ────────────────────────────────────────

def reset_daily_state():
    """Called at market open each day."""
    if date.today() in CME_HOLIDAYS_2026:
        log("reset_daily_state: market holiday — skipping")
        return
    global _orb_high, _orb_low, _orb_set, _confirmed_scans
    global _regime_scan_counts, _session_high, _session_low
    global _price_history, _partial_done, _peak_daily_pnl, _daily_pnl
    global _pm_high, _pm_low, _pm_ib_set, _pm_ib_set_time, _daily_macro_bias
    global _overnight_bias, _overnight_skip_day, _overnight_position, _overnight_computed
    global _elephant_day_type, _elephant_trades_today, _elephant_flush_ids
    global _ib_kind, _ib_kind_set, _day_regime, _large_ib_delayed

    _orb_high = _orb_low = None
    _orb_set  = False
    _pm_high = _pm_low = None
    _pm_ib_set      = False
    _pm_ib_set_time = None
    _daily_macro_bias   = 'BOTH'
    _overnight_bias     = 'BOTH'
    _overnight_skip_day = False
    _overnight_position = None
    _overnight_computed = False
    _ib_kind          = None
    _ib_kind_set      = False
    _day_regime       = None
    _large_ib_delayed = False
    _confirmed_scans  = 0
    _regime_scan_counts = {'STRONG': 0, 'NORMAL': 0, 'WEAK': 0}
    _session_high = {}
    _session_low  = {}
    _price_history = {}
    _partial_done  = {}
    _daily_pnl     = 0.0
    _peak_daily_pnl = 0.0
    _elephant_day_type     = 'NONE'
    _elephant_trades_today = 0
    _elephant_flush_ids    = set()

    prop_load()   # refresh prop rules state for new day
    log("Daily state reset")
    send_telegram(
        f"🌅 FUTURES day started\n"
        f"{format_prop_status()}"
    )


def eod_snapshot():
    """Called at EOD — reconcile balance from DB, send summary, reset for tomorrow."""
    daily = get_futures_daily_pnl()
    log(f"EOD futures P&L: ${daily:+.2f}")
    # update_eod_balance reconciles balance from DB truth, then resets session_pnl=0
    update_eod_balance(daily)
    # Read state AFTER balance update but re-inject today's P&L for the EOD message
    # (format_prop_status shows session_pnl=0 post-reset, so we show daily separately)
    s = prop_status()
    send_telegram(
        f"🌙 FUTURES (IBKR) EOD\n"
        f"Day P&L:    ${daily:+.2f}\n"
        f"Balance:    ${s.get('balance', 0):,.0f}\n"
        f"All-time:   ${s.get('total_profit', 0):+,.0f}\n"
        f"Resets tomorrow at 9:28am ET (2:28pm London)"
    )


# ── Telegram commands ─────────────────────────────────────

def poll_telegram_commands():
    """Poll for Telegram commands — PAUSE, RESUME, STATUS, CLOSE."""
    global _trading_paused, _tg_offset, _daily_macro_bias, _overnight_skip_day
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={'timeout': 1, 'offset': _tg_offset, 'allowed_updates': ['message']},
            timeout=5,
        )
        updates = r.json().get('result', [])
        for u in updates:
            _tg_offset = u['update_id'] + 1   # acknowledge — won't be returned again
            msg = u.get('message', {}).get('text', '').strip().upper()
            if msg:
                log(f"[TG CMD] recv: {msg[:80]}")
            if 'FUT PAUSE' in msg:
                _trading_paused = True
                send_telegram("⏸ FUTURES trading paused.")
            elif 'FUT RESUME' in msg:
                _trading_paused = False
                send_telegram("▶️ FUTURES trading resumed.")
            elif 'FUT STATUS' in msg:
                price = get_live_price()
                open_trades = get_open_futures_trades()
                pos_lines = []
                for t in open_trades:
                    live  = price or t['entry_price']
                    pts   = (t['entry_price'] - live) if t['side'] == 'SHORT' else (live - t['entry_price'])
                    upnl  = pts / TICK_SIZE * TICK_VALUE * t['contracts']
                    risk  = abs(t['entry_price'] - (t['stop_price'] or t['entry_price'])) / TICK_SIZE * TICK_VALUE * t['contracts']
                    pos_lines.append(
                        f"  {t['side']} {t['contracts']}ct @ {t['entry_price']} | "
                        f"now {live:.2f} | uPnL ${upnl:+.0f} | risk ${risk:.0f}"
                    )
                pos_str = '\n'.join(pos_lines) if pos_lines else '  No open positions'
                ovn_str = (f"skip({_overnight_position})" if _overnight_skip_day
                           else f"pos={_overnight_position}" if _overnight_position is not None
                           else "pending")
                send_telegram(
                    f"{format_prop_status()}\n"
                    f"MNQ: {price}  Bias: {_daily_macro_bias}  Session: {get_session()}\n"
                    f"Overnight: {ovn_str}\n"
                    f"Positions ({len(open_trades)}):\n{pos_str}"
                )
            elif 'FUT BIAS LONG' in msg:
                _daily_macro_bias   = 'LONG'
                _overnight_skip_day = False   # user override — allow entries despite skip zone
                send_telegram(
                    f"✅ Bias: LONG (overnight skip overridden)\n"
                    f"System will only take LONG entries today.\n"
                    f"PM High: {_pm_high}  (target for pm_break signal)"
                )
            elif 'FUT BIAS SHORT' in msg:
                _daily_macro_bias   = 'SHORT'
                _overnight_skip_day = False
                send_telegram(
                    f"✅ Bias: SHORT (overnight skip overridden)\n"
                    f"System will only take SHORT entries today.\n"
                    f"PM Low: {_pm_low}  (target for pm_break signal)"
                )
            elif 'FUT BIAS BOTH' in msg:
                _daily_macro_bias   = 'BOTH'
                _overnight_skip_day = False
                send_telegram("✅ Bias: BOTH — overnight filter disabled for today.")
            elif 'FUT CLOSE' in msg:
                _force_close_all()
            elif 'STATUS ALL' in msg:
                send_telegram(_portfolio_all())
    except Exception as e:
        log(f"[TG poll error] {e}")


def _force_close_all():
    """Emergency: close all open futures positions."""
    trades = get_open_futures_trades()
    if not trades:
        send_telegram("No open futures positions.")
        return
    for t in trades:
        _cancel_backup_stop(t)   # cancel backup stop before closing
        side = 'BUY' if t.get('side') == 'SHORT' else 'SELL'
        _bridge_post('/futures/order', {
            'symbol': SYMBOL, 'qty': t.get('contracts', 1),
            'side': side, 'order_type': 'MARKET',
        })
        price = get_live_price() or t['entry_price']
        pnl_pts  = (t['entry_price'] - price) if t.get('side') == 'SHORT' else (price - t['entry_price'])
        pnl_ticks = pnl_pts / TICK_SIZE
        pnl_usd   = pnl_ticks * TICK_VALUE * t.get('contracts', 1)
        log_futures_exit(t['id'], price, 'FUT CLOSE command', pnl_usd, pnl_ticks)
        record_trade_pnl(pnl_usd)
    send_telegram(f"🔴 FUTURES: force-closed {len(trades)} position(s).")


# ── Scheduler + entry point ───────────────────────────────

def main():
    global _scheduler

    # ── Go-live gate (mirrors PROD_EQUITY_ENABLED pattern) ───────────────────
    # In prod: TRADING_MODE=live. Must also set PROD_FUTURES_ENABLED=true to trade.
    # Paper mode (UAT): flag not checked — always runs.
    if os.getenv('TRADING_MODE', 'paper') == 'live':
        if os.getenv('PROD_FUTURES_ENABLED', 'false').lower() != 'true':
            log("PROD_FUTURES_ENABLED is not 'true' in .env — exiting. Set it to enable live futures trading.")
            sys.exit(0)

    log("=" * 50)
    log("FUTURES TRADER starting")
    log(f"Symbol: {SYMBOL} | Bridge: {BRIDGE}")
    log("=" * 50)

    # Verify bridge
    if not get_bridge_connected():
        log("WARNING: futures bridge not connected — waiting for connection")
        send_telegram("⚠️ FUTURES: Bridge not connected at startup. Will retry on each scan.")

    # ── Startup: cancel any dangling backup stops from prior session ─────────
    # If the bot was killed mid-trade or after a manual cleanup, IBKR may still
    # have live SELL STOP orders from previous entries. Cancel them all.
    _orphan_trades = get_open_futures_trades()
    for _t in _orphan_trades:
        if _t.get('stop_order_id'):
            _r = _bridge_post(f"/futures/cancel/{_t['stop_order_id']}", {})
            log(f"  Startup: cancelled orphan backup stop {_t['stop_order_id']} → {_r.get('status','?')}")
        # Mark stale OPEN trades from a prior day as CLOSED so today starts clean
        if _t.get('entry_date') and _t['entry_date'] != str(date.today()):
            _conn = sqlite3.connect(DB_PATH)
            _conn.execute(
                "UPDATE futures_trades SET status='CLOSED', exit_reason='orphaned on restart', "
                "exit_date=?, exit_time=?, pnl=0 WHERE id=?",
                (str(date.today()), datetime.now(ET).strftime('%H:%M:%S'), _t['id'])
            )
            _conn.commit()
            _conn.close()
            log(f"  Startup: marked trade {_t['id']} ({_t.get('symbol')}) as orphaned (was OPEN from {_t['entry_date']})")

    load_avg_volumes()   # build RVOL denominator (non-blocking; graceful if missing)

    prop_load()
    # Reconcile ibkr_state from DB on every startup — restarts mid-day cause drift.
    # DB is the single source of truth for all realized P&L.
    _state     = prop_load()
    _db_today  = get_futures_daily_pnl()
    _db_total  = _get_all_time_futures_pnl()
    _saved_date = _state.get('session_date', '')
    _today_str  = str(date.today())
    _changed   = False
    if _saved_date != _today_str:
        # New calendar day — always start session_pnl fresh; never carry yesterday's DLL
        _state['session_pnl']  = 0
        _state['session_date'] = _today_str
        _changed = True
    else:
        # Same-day restart — restore session_pnl from DB truth (handles mid-day crash recovery)
        if _db_today != _state.get('session_pnl', 0):
            _state['session_pnl'] = _db_today
            _changed = True
    # Reconcile balance/total_profit by delta (handles restarts mid-day)
    _tracked = _state.get('total_profit', 0)
    _delta   = round(_db_total - _tracked, 2)
    if abs(_delta) > 0.01:
        _state['total_profit'] = _db_total
        _state['balance']      = round(_state.get('balance', IBKR_FLOOR) + _delta, 2)
        _changed = True
    if _changed:
        prop_save(_state)
    send_telegram(f"⚡ TriVega Futures · Personal · Online\n{format_prop_status()}")

    _scheduler = BackgroundScheduler(timezone=ET)

    # Core loops
    _scheduler.add_job(run_scan,     'interval', seconds=SCAN_INTERVAL,    id='scan')
    _scheduler.add_job(run_monitor,  'interval', seconds=MONITOR_INTERVAL, id='monitor')

    # London session (plug/unplug via LONDON_ENABLED above)
    if LONDON_ENABLED:
        from futures import london_trader as _lt
        _lt.init_db()
        _scheduler.add_job(
            _lt.run_scan, 'cron',
            hour='3-8', minute='*',
            id='london_scan', max_instances=1, misfire_grace_time=30,
        )
        _scheduler.add_job(
            _lt.run_monitor, 'interval',
            seconds=15,
            id='london_monitor', max_instances=1,
        )
        log('London session ENABLED — IB 3am–4am ET, entries 4am–8am ET')
        send_telegram('🇬🇧 London session ENABLED')

    # Telegram command polling
    _scheduler.add_job(poll_telegram_commands, 'interval', seconds=10, id='telegram')

    # Daily routines
    _scheduler.add_job(reset_daily_state, 'cron',
                       day_of_week='mon-fri', hour=9, minute=28,
                       timezone=ET, id='daily_reset')
    _scheduler.add_job(eod_snapshot, 'cron',
                       day_of_week='mon-fri', hour=16, minute=10,
                       timezone=ET, id='eod_snapshot')

    _scheduler.start()
    log("Scheduler started. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        log("Shutting down futures trader...")
        _scheduler.shutdown()
        send_telegram("🔴 FUTURES TRADER stopped.")


if __name__ == '__main__':
    main()
