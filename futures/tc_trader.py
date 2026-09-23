"""
futures_trader.py — MNQ Futures Trading System
Third vertical: equity → options → futures

Architecture:
  bridge_projectx.py  (port 8002)  ←→  TopStepX / ProjectX API  [to build]
  prop_rules.py                    ←   TopStepX TC/XFA safety layer
  futures_trader.py                ←   this file (strategy + execution)
  database.py (shared root)        ←   trades.db futures_trades table

MNQ constants:
  Tick size  : 0.25 points
  Tick value : $0.50
  Point value: $2.00
  Session    : 6pm–5pm ET (23h), trade NY only (9:30am–4:00pm ET / 3:00pm CT)
"""

import os
import subprocess
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
    save_state as prop_save, ACCOUNT_MODE, DLL_SOFT, TC_DAILY_CAP,
)
from portfolio_status import format_all as _portfolio_all

# ── Telegram ──────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv('FUTURES_TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('FUTURES_TELEGRAM_CHAT_ID')

# ── Bridge ────────────────────────────────────────────────
BRIDGE = os.getenv('FUTURES_BRIDGE_URL', 'http://localhost:8000')  # IBKR bridge (bridge.py)

from strategy_core import SYMBOL, EXCHANGE, POINT_VALUE, TICK_SIZE, TICK_VALUE, COMMISSION  # noqa: E402
from futures.heartbeat import beat as _beat  # noqa: E402
from futures.gate_audit import log_block, log_enter, log_shadow_signal  # noqa: E402
from futures.hero_score import (  # noqa: E402  — Trend Jury (Jul 25 2026 alignment)
    score_entry_regime, contracts_from_regime_score, detect_regime, is_gold_score,
)
from futures import thesis_check as _thesis_check  # noqa: E402  — LLM in-trade observer (Aug 9 2026)

# ── Risk constants ────────────────────────────────────────
MAX_RISK_PER_TRADE   = 100.0   # $ max risk per trade (1 contract × 50-tick stop)
MAX_DAILY_LOSS       = DLL_SOFT      # $700 TC soft DLL (hard limit $1K — stay $300 above it)
DAILY_PROFIT_TARGET  = TC_DAILY_CAP  # $1,200 — TC consistency cap (50% rule buffer)
MIN_RR               = 2.0     # minimum reward:risk ratio
MAX_OPEN_TRADES      = 1       # was 2 until Aug 24 2026. Every backtest in this
                               # program (sim_replay:1127) models ONE position at a
                               # time; live allowed two, so 78% of live entry-events
                               # were correlated pairs and every Sharpe / MaxDD /
                               # P(blow) figure understated real risk ~2x. This makes
                               # live match the book we actually validated.
MAX_DAILY_TRADES     = 5       # raised from 2 Aug 9 2026 to match IBKR — the TC_DAILY_CAP
                                # ($1,200, ≤40% of the $3K target) and check_can_trade()'s
                                # consistency check (50% of total_profit, active once
                                # total_profit>=$1,200) are both trade-count-independent
                                # $-based backstops, so this cap was never the thing actually
                                # protecting the account; it was just leaving upside on the
                                # table on winning days. User-directed, reasoned from the real
                                # gate code, not backtested against TC's exact sizing yet.
COOLDOWN_MINUTES     = 2.0     # minutes to wait after any exit before next entry
ENTRY_COOLDOWN_MINUTES = 2.0   # minutes to wait after any ENTRY before another entry.
                               # Added Aug 24 2026. COOLDOWN_MINUTES only counted from the
                               # last EXIT, so the 60s scan loop re-fired a still-valid
                               # signal on the very next scan: 57 of 73 live repeat entries
                               # were <2min apart (median gap 1min) and lost -$832, while
                               # every genuine re-entry was >=2min away. Together with
                               # MAX_OPEN_TRADES=1 this is what stops one signal becoming
                               # two correlated positions (see docs / Aug 18 duplicate-entry
                               # root cause).
MAX_PRICE_DIVERGENCE = 50.0    # pts: max allowed gap between scan price and live price at order time

# ── SHORT-SIDE RISK CAP + DAILY TIDE GATE (Sep 2 2026) ─────────────────────
SHORT_MAX_CONTRACTS = 1        # LIVE. The RVOL/IB conviction ladder in
                               # calc_contracts_dynamic() is anti-predictive on the
                               # short side ONLY (see the comment there). Shorts are
                               # profitable at 1c (+$10.1/trade) and ruinous at 2c
                               # (-$85.3/trade, negative 5 of 6 years). Removes no
                               # trades — only caps their size.

# The "Daily Tide" side gate: only take LONGs when the previous daily close was
# ABOVE its own 200-day MA, and SHORTs only when BELOW it. 5.5yr pipeline result
# (both legs run separately through _run_scenario, disjoint day sets, merged):
#   live today   949t  +$3,353  maxDD -$7,715  worstDay -$814  6 TC blows  green 2/6
#   daily tide   516t +$12,937  maxDD -$2,198  worstDay -$801  0 TC blows  green 6/6
# The short leg alone is +$3,089 (n=97) and is what finally gives the book a
# DEFENSIVE leg: 2022 goes -$1,615 -> +$2,238.
# ⚠️ LOG-ONLY for now (user decision Sep 2 2026): it blocks nothing, it only records
# what it WOULD have blocked, under gate name 'TIDE_INFO' so gate_audit scores it
# the same way OVN_SKIP was scored before it was trusted. Review after one week.
# Honest caveats being watched during the log window:
#   - the short leg is bear-market insurance: 72 of its 97 trades are in 2022, and
#     it would have taken ZERO trades in 2021 and 2024.
#   - it has already cost a real winner (Sep 1 2026 VWAP_SHORT, +$512).
#   - NY zero-trade days would rise 43% -> 68%.
#   - London shorts the same tape in the same week and is NOT gated by this.
TIDE_GATE_ENABLED  = False     # False = LOG ONLY. Flip True to actually block.
TIDE_MA_DAYS       = 200


# ── Exit stack — ALIGNED WITH futures_trader.py Jul 25 2026 ─────────────────
# User-directed alignment: TC was still running the pre-Jul-7 NY system
# (ATR 1.5x stops, BE+30/trail 20/10pt tiers tuned for a 99pt target that no
# longer exists). Now mirrors the NY IBKR stack: 200pt backstop stop, 1500pt
# backstop target (trail tiers do the real exit work), regime-aware profit
# locks, reversal-detection exit, partial scale-out. Validated via sim_replay
# under TC's own caps (--dll 700 --max-trades 2): baseline $6,017 → rev-exit
# $7,395 (2026 YTD). Prop rules (DLL_SOFT/TC_DAILY_CAP/sizing) UNCHANGED.
BASE_STOP_PTS     = 200.0   # catastrophic backstop — trail tiers cut losses first
BASE_TARGET_PTS   = 1500.0  # backstop only — essentially never fires

EXIT_PARAMS_BY_REGIME: dict[str, dict[str, float]] = {
    'CHOPPY':   {'be_pts': 90.0,  'be_frac': 0.45, 'wide_pts': 130.0, 'wide_gap': 60.0,  'tight_pts': 200.0, 'tight_gap': 35.0},
    'QUIET':    {'be_pts': 90.0,  'be_frac': 0.45, 'wide_pts': 130.0, 'wide_gap': 60.0,  'tight_pts': 200.0, 'tight_gap': 35.0},
    'TRENDING': {'be_pts': 110.0, 'be_frac': 0.20, 'wide_pts': 300.0, 'wide_gap': 180.0, 'tight_pts': 550.0, 'tight_gap': 110.0},
}

# Reversal-detection exit + partial scale-out (see futures_trader.py constants
# comment for the full validation trail — 2026 YTD + 2025 OOS, both positive).
REV_EXIT_CONFIRM_BARS  = 2      # consecutive adverse CLOSED 5-min bars
REV_EXIT_RETRACE_FRAC  = 0.30   # fraction of peak given back
REV_EXIT_PEAK_MIN_PTS  = 120.0  # only after the trade proved a real peak
PARTIAL_TAKE_PTS       = 150.0  # bank 1 contract here (2-contract trades)

# Entry-quality gates (aligned with NY Jul 25 2026)
ENTRY_MIN_RVOL    = 0.85    # session RVOL floor at entry (completed bars only)
RVOL_GRAD_FLOOR   = 0.70    # graduated band: allowed if Hero score clears GOLD
HTF_TREND_BARS    = 3       # 30-min bars for higher-timeframe agreement

# ── No-move exit (time-based — frees dead trade slots) ───
# Aligned with NY (was 25/-10 from the old 99pt-target era).
NO_MOVE_MINUTES = 90      # minutes open before checking
NO_MOVE_MAX_PTS = 60.0    # above this → trade IS progressing, let it run
NO_MOVE_MIN_PTS = -40.0   # below this → hard stop will manage it

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
EOD_START       = (15, 30)  # 3:30pm ET = 2:30pm CT — no new entries (matches TopStepX rule)
HARD_CLOSE      = (16, 0)   # 4:00pm ET = 3:00pm CT — force close (10 min before TopStepX 3:10pm CT auto-liq)

SCAN_INTERVAL   = 60        # seconds between scans (1 min, faster than equity)
MONITOR_INTERVAL = 15       # seconds between position checks

LONDON_ENABLED = True   # wired Aug 9 2026 — see main() for scheduler jobs.
                         # london_trader.py resolves BRIDGE/ACCOUNT_MODE from
                         # this process's own env (FUTURES_BRIDGE_URL=8002,
                         # FUTURES_ACCOUNT_MODE=TC) — same module, isolated by
                         # account_mode-tagged rows in the shared london_trades
                         # table. Plug/unplug: flip this + restart service.

# ── Global state ──────────────────────────────────────────
_active_contract_month = ''   # resolved daily; keeps get_bars() on same contract as live quote
_last_regime          = 'NORMAL'
_confirmed_scans      = 0
_regime_scan_counts   = {'STRONG': 0, 'NORMAL': 0, 'WEAK': 0}
_session_high         = {}   # trade_id → session high
_session_low          = {}   # trade_id → session low
_price_history        = {}   # trade_id → [prices]
_partial_done         = {}   # trade_id → locked_pnl
_rev_state            = {}   # trade_id → {'last_ts', 'adv_streak', 'prev_close'} (reversal exit)
_avg_vol_by_time: dict = {}  # ported from futures_trader.py Aug 9 2026 — RVOL denominator for calc_contracts_dynamic
_streak_regime        = None # F1: regime of the current consecutive-bar streak
_streak_bar_ts        = None # F1: last completed 5-min bar that advanced the streak
_day_regime           = None # 'TRENDING' | 'CHOPPY' | 'QUIET' — set at 10:30 IB formation
_ib_kind              = None # 'BEAR_DIRECTIONAL' | 'BULL_DIRECTIONAL' | 'ROTATIONAL'
_ib_kind_set          = False
_orb_high             = None # opening range high (first 15 min)
_orb_low              = None # opening range low
_orb_set              = False
_pm_high              = None # pre-market IB high (8:30–9:30am ET) — Cylinder 4
_pm_low               = None # pre-market IB low
_pm_ib_set            = False
_pm_ib_set_time       = None  # datetime when pm_ib was first captured this session
_daily_macro_bias     = 'BOTH'  # 'LONG' | 'SHORT' | 'BOTH' — set via Telegram or Groq
_overnight_bias       = 'BOTH'  # from overnight_position classifier (aligned with futures_trader.py Aug 9 2026)
_overnight_skip_day   = False   # True if overnight_position in bad zone [0.20, 0.40)
_overnight_position   = None    # float — 0=opened at overnight low, 1=overnight high
_overnight_computed   = False   # True after first RTH-bar computation this session
_daily_pnl            = 0.0
_peak_daily_pnl       = 0.0
_trading_paused       = False
_last_exit_time       = None   # datetime of most recent trade exit — cooldown gate
_last_entry_time      = None   # datetime of most recent trade ENTRY — duplicate-entry gate
_tg_offset            = 0      # Telegram getUpdates offset — marks messages as read
_scheduler            = None
_cached_df5           = pd.DataFrame()  # bars cached by run_scan(), reused by run_monitor()
_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_DIR, '..', 'trades.db')
MKT_DB_PATH = os.path.join(_DIR, '..', 'market_data.db')

# CME holiday calendar — NOT the same as NYSE. CME stays open on Juneteenth,
# MLK Day, and Presidents Day.
CME_HOLIDAYS_2026 = {
    date(2026, 1,  1),   # New Year's Day
    date(2026, 4,  3),   # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 7,  3),   # Independence Day (observed, Sat→Fri)
    date(2026, 9,  7),   # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
}


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
    lines.append(f"Day P&L: ${s.get('session_pnl', 0):+,.2f}  |  Cap left: ${s.get('daily_cap_left', 0):,.0f}")
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
    # Weekend guard — RESTORED Sep 2 2026. The Jul 18 2026 fix was recorded as
    # applied to "both traders" but only ever landed in futures_trader.py; an
    # AST diff of the two files found TC still unguarded. get_session() is
    # time-of-day only, so Saturday 09:30-16:00 reads as MIDDAY/AFTERNOON and
    # the whole scan pipeline runs against frozen Friday bars — junk
    # GRADE/REGIME rows in gate_blocks at a stale price, plus theoretical
    # resting-order risk into a closed market.
    if datetime.now(ET).weekday() >= 5:
        return False
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


def _get_ibkr_qty() -> float | None:
    """Current net MNQ position size at the broker (signed). None if bridge unavailable."""
    pos = _bridge_get('/futures/position')
    if not isinstance(pos, list):
        return None
    for p in pos:
        if p.get('symbol') == SYMBOL:
            return float(p.get('qty', 0))
    return 0.0


def _get_fill_price(order_id, fallback: float) -> float:
    """Real average fill price for the entry we just placed; scan price on failure.

    Added Sep 3 2026. place_trade() verified fill SIZE (_actual_filled) but never
    the fill PRICE, so entry_price in futures_trades was the SCAN price. Measured
    live the same day on one signal taken by BOTH accounts:
        IBKR  scan 29378.25  actual 29378.06  ->  0.19pt error
        TC    scan 29378.25  actual 29392.93  -> 14.68pt error = $58.72 on 2c
    Every stop / target / trail level / P&L keyed off a price we did not pay. It
    bites hardest on TC, whose separate gateway session simulates its own fills,
    and it is a direct contributor to the live/sim divergence.

    PRIMARY SOURCE is the portfolio avg_cost, NOT /order/{id}/status. The order
    endpoint is unreliable — verified Sep 3 2026 on a real filled entry it still
    returned {status: PendingSubmit, filled: 0.0, avgFillPrice: 0.0}, the same
    class of staleness behind the Jul 20 2026 USAR auto-close incident. Because
    MAX_OPEN_TRADES == 1 every entry is opened from FLAT, so the position's
    avg_cost IS this entry's fill price with no blending.

    Guarded: a value further than MAX_FILL_DRIFT_PTS from the scan price is
    rejected as stale/blended rather than trusted, and any failure falls back to
    the scan price so an entry is never lost.
    """
    MAX_FILL_DRIFT_PTS = 60.0
    for src, getter in (
        ('avg_cost', lambda: next(
            (float(p['avg_cost']) for p in (_bridge_get('/futures/position') or [])
             if isinstance(p, dict) and p.get('symbol') == SYMBOL
             and float(p.get('qty', 0)) != 0 and float(p.get('avg_cost', 0)) > 0),
            0.0)),
        ('order_status', lambda: float(
            (_bridge_get(f'/order/{order_id}/status') or {}).get('avgFillPrice') or 0)
            if order_id else 0.0),
    ):
        try:
            px = getter()
        except Exception as e:
            log(f"  fill-price via {src} failed ({e})")
            continue
        if px <= 0:
            continue
        drift = px - fallback
        if abs(drift) > MAX_FILL_DRIFT_PTS:
            log(f"  fill-price via {src} = {px} is {drift:+.1f}pts from scan "
                f"{fallback} (> {MAX_FILL_DRIFT_PTS:.0f}) — REJECTED as stale")
            continue
        if abs(drift) > 0.01:
            log(f"  fill {px} vs scan {fallback} ({drift:+.2f}pts, via {src}) — using real fill")
        return px
    log(f"  fill price unavailable — using scan price {fallback}")
    return fallback


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


# ── Overnight position classifier (ported from futures_trader.py Aug 9 2026) ──
# Thresholds match tc_champion v3.2 (data-validated, WFA 8/9 windows):
#   [0.20, 0.40) = bad zone → skip day (18–36% WR, -$3,679 in 5yr backtest)
#   >= 0.85      = TRENDING_UP  → LONG bias
#   <= 0.20      = TRENDING_DOWN → SHORT bias
# Whole-day veto REMOVED Jul 18 2026 (aligned with futures_trader.py) — the skip
# zone is now log-only (OVN_SKIP gate_blocks row), it no longer blocks entries.
# _daily_macro_bias auto-set from a clear overnight lean (>=0.85 / <=0.20) still
# stands — that's a real directional gate, not the removed veto.

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
            # Pre-market already showing bullish structure (mirrors futures_trader.py).
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


# ── Entry-gate helpers (ported from futures_trader.py, Jul 25 2026 alignment) ──

def calc_session_rvol(df5: pd.DataFrame) -> float:
    """Last COMPLETED RTH bar volume / avg of today's RTH bars. Mirrors
    futures_trader.py exactly (incl. the Jul 17 partial-bar fix — the forming
    bar's partial volume made the gate ~2x stricter than calibrated)."""
    if df5.empty:
        return 1.0
    now     = datetime.now(ET)
    today   = now.date()
    rth_start = ET.localize(datetime(today.year, today.month, today.day, 9, 30))
    today_bars = df5[df5.index >= rth_start]
    if len(today_bars) and (now - today_bars.index[-1]).total_seconds() < 300:
        today_bars = today_bars.iloc[:-1]
    if len(today_bars) < 2:
        return 1.0
    avg_v = today_bars['volume'].mean()
    if not avg_v:
        return 1.0
    return float(today_bars['volume'].iloc[-1]) / float(avg_v)


def calc_htf_trend(df5: pd.DataFrame) -> int:
    """30-min trend direction over last HTF_TREND_BARS completed bars.
    1 up / -1 down / 0 flat. Mirrors futures_trader.py (incl. F2 forming-bar
    drop, Jul 18)."""
    if df5.empty:
        return 0
    bars = df5
    if len(bars) and (datetime.now(ET) - bars.index[-1]).total_seconds() < 300:
        bars = bars.iloc[:-1]
    if bars.empty:
        return 0
    htf = bars['close'].resample('30min').last().dropna()
    if len(htf) < HTF_TREND_BARS:
        return 0
    last_n = htf.iloc[-HTF_TREND_BARS:]
    if last_n.iloc[-1] > last_n.iloc[0]:
        return 1
    if last_n.iloc[-1] < last_n.iloc[0]:
        return -1
    return 0


def calc_ib_range_today(df5: pd.DataFrame) -> float:
    """Today's H-L range from 9:30am to now. Mirrors futures_trader.py."""
    if df5.empty:
        return 0.0
    today    = datetime.now(ET).date()
    ib_start = ET.localize(datetime(today.year, today.month, today.day, 9, 30))
    bars     = df5[df5.index >= ib_start]
    if len(bars) < 2:
        return 0.0
    return float(bars['high'].max() - bars['low'].min())


# ── Regime detection (NQ-native) ─────────────────────────

def get_regime(df5: pd.DataFrame | None = None) -> str:
    """
    Determine market regime from NQ/MNQ bars directly.
    No SPY proxy needed — NQ IS the market for this instrument.
    Returns: STRONG | NORMAL | WEAK
    Accepts pre-fetched df5 from run_scan() to avoid a redundant bridge call.

    PORTED from futures_trader.py Jul 25 2026 (TC alignment).
    Rebuilt Jul 7 2026 after backtesting the original formula against 3 months
    of real bars (3,721 scans) and finding STRONG and NORMAL were statistically
    indistinguishable (both ~+2.8pt mean 30-min forward move) — the STRONG
    label wasn't adding real signal, and WEAK's median forward move was ~0.
    Root causes found and fixed:
      1. No volume/participation filter — added an RVOL>=0.65 gate (matches
         the decoder's independently-derived "high" tercile boundary). Alone,
         this roughly tripled STRONG/WEAK separation from NORMAL.
      2. day_chg_pct (vs YESTERDAY's close) can't detect a strong intraday
         reversal on a gap day — if today opened down and is recovering hard,
         day_chg vs yesterday can stay negative the whole session even during
         a genuine intraday uptrend, so STRONG could structurally never fire.
         Fixed with a hybrid: STRONG uses change vs TODAY's own open
         (session_chg) which captures the reversal; WEAK keeps day_chg vs
         yesterday's close, which tested better there (session-relative
         WEAK's median forward move flipped positive — false negatives).
      3. 3-bar trend was too noise-sensitive — a single reversal bar broke a
         real multi-bar trend and reset the 3-scan confirmation counter.
         Widened to 5 bars.
    Backtested result (RVOL gate + hybrid + 5-bar trend): STRONG n=253
    mean +11.14/median +12.75 (vs old +2.62/+6.62); WEAK n=205 mean -14.46/
    median -11.25 (vs old -4.75/-0.25, which barely cleared zero). Replayed
    against Jul 7 2026 itself: old formula produced only 2 total WEAK scans
    all session (never confirmed); new formula confirms WEAK at 10:00am
    (3 consecutive scans from 9:50), ~45min earlier than the actual 10:46
    entry and at a meaningfully better SHORT price (29,443 vs 29,270-29,280).
    """
    global _last_regime
    try:
        if df5 is None or df5.empty:
            df5 = get_bars(bar_size_min=5, days=2)
        if df5.empty or len(df5) < 6:
            return _last_regime

        # ── Completed bars only (fixed Aug 24 2026) ───────────────────────
        # get_bars() returns the currently-FORMING 5-min bar. Reading it made
        # price / RSI / the 5-bar trend flicker *within* a single bar, which
        # reset the consecutive-same-regime confirmation streak to 1 and made
        # live confirmation lag the backtest. sim_replay.get_regime() is fed
        # closed bars by construction, so this was a pure live/sim divergence.
        # Third and final instance of this bug class, after calc_session_rvol
        # (Jul 17 2026) and calc_htf_trend (Jul 18 2026).
        # NOTE: calc_session_rvol() performs its OWN forming-bar trim, so it
        # must keep receiving the untrimmed frame — passing df5c would drop
        # two bars and understate RVOL.
        df5c = df5
        if (datetime.now(ET) - df5.index[-1]).total_seconds() < 300:
            df5c = df5.iloc[:-1]
        if len(df5c) < 6:
            return _last_regime

        price      = float(df5c['close'].iloc[-1])
        vwap       = calc_vwap(df5c)
        rsi        = calc_rsi(df5c['close'])
        rvol       = calc_session_rvol(df5)

        # Today's bars only
        today      = datetime.now(ET).date()
        df_today   = df5c[df5c.index.date == today]

        # Price vs VWAP
        above_vwap = price > vwap if vwap else True

        # Short-term trend: last 5 bars (was 3 — too noise-sensitive)
        if len(df_today) >= 5:
            trend = df_today['close'].iloc[-5:]
            trending_up   = trend.iloc[-1] > trend.iloc[0]
            trending_down = trend.iloc[-1] < trend.iloc[0]
        else:
            trending_up = trending_down = False

        # Day change vs prev close (used for WEAK — tested better there)
        if len(df5c) >= 2:
            prev_close = float(df5c['close'].iloc[-2]) if len(df_today) < 2 else float(df5c[df5c.index.date < today]['close'].iloc[-1]) if len(df5c[df5c.index.date < today]) > 0 else float(df5c['close'].iloc[-2])
            day_chg_pct = (price - prev_close) / prev_close * 100 if prev_close else 0
        else:
            day_chg_pct = 0

        # Change vs TODAY's own open (used for STRONG — catches intraday
        # reversals that day_chg vs yesterday's close would miss on gap days)
        if len(df_today) >= 1:
            session_open = float(df_today['open'].iloc[0])
            session_chg_pct = (price - session_open) / session_open * 100 if session_open else 0
        else:
            session_chg_pct = 0

        # Choppiness: >40% bar reversals, measured against session_chg (today's
        # own range) rather than day_chg — a big gap can make day_chg large
        # even on a genuinely flat/choppy today.
        if len(df_today) >= 6:
            diffs  = df_today['close'].diff().dropna()
            flips  = sum(1 for i in range(1, len(diffs)) if diffs.iloc[i] * diffs.iloc[i-1] < 0)
            choppy = (flips / max(len(diffs), 1)) > 0.4 and abs(session_chg_pct) < 0.15
        else:
            choppy = False

        # ── Classify regime ───────────────────────────────
        if choppy:
            regime = 'NORMAL'
        elif rvol < 0.65:
            regime = 'NORMAL'   # not enough participation to trust the direction
        elif (above_vwap and trending_up and session_chg_pct > 0.15
              and rsi < 80):
            regime = 'STRONG'
        elif (not above_vwap and trending_down and day_chg_pct < -0.3
              and rsi > 20):
            regime = 'WEAK'
        else:
            regime = 'NORMAL'

        # Diagnostic detail — added Jul 7 2026. Without this, a misclassification
        # is unprovable after the fact: on Jul 7, hand-replaying this exact
        # formula against (later-settled) bars showed WEAK continuously from
        # 9:45-10:15am while the live log said STRONG/NORMAL almost the whole
        # window, blocking a SHORT entry during the session's real move. Could
        # not pin down whether that was a genuine bug or live/streaming bars
        # not yet settled vs. the final historical values, because the inputs
        # were never logged. Log them every scan so next time it's provable.
        vwap_str = f"{vwap:.2f}" if vwap is not None else "None"
        log(f"  regime_detail: day_chg%={day_chg_pct:+.2f} sess_chg%={session_chg_pct:+.2f} "
            f"vwap={vwap_str} above_vwap={above_vwap} trend_up={trending_up} trend_dn={trending_down} "
            f"rsi={rsi:.1f} rvol={rvol:.2f} choppy={choppy} contract={_active_contract_month or 'unset'}")

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

    # ── Bull/bear signal patterns — ALIGNED with futures_trader.py (Jul 25
    # 2026, originally redesigned there Jul 7). Old TC definitions had the
    # same two flaws NY fixed: orb signals required session=='NY_OPEN' (so
    # orb_bear could never qualify a SHORT after 10:30), and vwap/momentum
    # were single-bar-sensitive. See futures_trader.py get_signals comment
    # for the empirical validation trail.

    # ORB break — session restriction removed (was NY_OPEN-only)
    sig['orb_bull'] = (_orb_set and price > _orb_high)
    sig['orb_bear'] = (_orb_set and price < _orb_low)

    # VWAP position — sustained 3-bar streak instead of single-bar crossing
    if len(df5) >= 3:
        vwap3 = df5.index[-3:]
        sig['vwap_reclaim']   = bool((df5.loc[vwap3, 'close'] > vwap).all())
        sig['vwap_rejection'] = bool((df5.loc[vwap3, 'close'] < vwap).all())
    else:
        sig['vwap_reclaim'] = sig['vwap_rejection'] = False

    # Momentum: majority (3-of-4) same-direction transitions over last 5 bars
    if len(df5) >= 5:
        last5 = df5['close'].iloc[-5:]
        up_count   = sum(1 for i in range(len(last5)-1) if last5.iloc[i] < last5.iloc[i+1])
        down_count = sum(1 for i in range(len(last5)-1) if last5.iloc[i] > last5.iloc[i+1])
        sig['momentum_bull'] = (up_count >= 3) and price > vwap
        sig['momentum_bear'] = (down_count >= 3) and price < vwap
    else:
        sig['momentum_bull'] = sig['momentum_bear'] = False

    # Session open play (first bar direction after 9:30)
    today      = datetime.now(ET).date()
    df_today   = df5[df5.index.date == today]
    if len(df_today) >= 2:
        open_bar = float(df_today['close'].iloc[0])
        sig['open_play_bull'] = price > open_bar and price > vwap
        sig['open_play_bear'] = price < open_bar and price < vwap
    else:
        sig['open_play_bull'] = sig['open_play_bear'] = False

    # ── Pre-market IB break (Cylinder 4) — macro event capture ───────────
    # On NFP/CPI/FOMC days: the 8:30am release creates a structural range.
    # Breaking pm_high/pm_low during RTH = continuation of macro move.
    sig['pm_bull'] = (_pm_ib_set and _pm_high is not None and price > _pm_high)
    # PM_SHORT disabled (aligned with futures_trader.py Jul 25 2026): 0-for-7
    # lifetime / -$1,544 on the IBKR side — it shorts the exhaustion LOW of a
    # decline (stop-hunt through the pre-market low), not a real breakdown.
    sig['pm_bear'] = False
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

def calc_sl_target(price: float, atr: float, side: str) -> tuple[float, float]:
    """
    Calculate stop-loss and target in price terms.
    Point-based (aligned with futures_trader.py Jul 25 2026) — MNQ's real
    daily range (~447pt avg) is an order of magnitude bigger than its 5-min
    ATR (~39pt), so ATR-multiple stops chase the wrong scale. The 200pt stop
    is a catastrophic backstop; the regime-aware trail tiers + reversal exit
    in monitor_open_trades() do the real exit work. `atr` param kept for
    signature compat.
    """
    tick = TICK_SIZE
    raw_stop   = BASE_STOP_PTS
    raw_target = BASE_TARGET_PTS

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
    Risk-based contract sizing. LEGACY / UNUSED as of Aug 9 2026 — place_trade()
    now calls calc_contracts_dynamic() instead (RVOL/IB-range tiers, matches
    futures_trader.py). MAX_RISK_PER_TRADE is stale (written for a 50-tick stop;
    BASE_STOP_PTS is 200pts/800 ticks now) — kept only for documentation
    consistency, same as futures_trader.py's own dead MAX_RISK_PER_TRADE.
    """
    stop_pts   = abs(price - sl)
    stop_ticks = stop_pts / TICK_SIZE
    risk_per_c = stop_ticks * TICK_VALUE

    if risk_per_c <= 0:
        return 1

    contracts = int(MAX_RISK_PER_TRADE / risk_per_c)
    contracts = max(1, min(contracts, get_max_contracts()))
    return contracts


def had_loss_today() -> bool:
    """True if any futures trade closed at a loss today (ET date). Ported from futures_trader.py Aug 9 2026."""
    today = str(datetime.now(ET).date())
    conn  = sqlite3.connect(DB_PATH)
    count = conn.execute(
        "SELECT COUNT(*) FROM futures_trades "
        "WHERE entry_date=? AND pnl < 0 AND status='CLOSED' AND account_mode=?",
        (today, ACCOUNT_MODE),
    ).fetchone()[0]
    conn.close()
    return count > 0


def load_avg_volumes():
    """Load per-time-slot average volume from market_data.db at startup (once).
    Builds _avg_vol_by_time dict keyed by ET time string '%H:%M'.
    Ported from futures_trader.py Aug 9 2026 — shared MNQ market data, same
    baseline as IBKR's own instance (separate in-memory copy per process).
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
    Returns 1.0 (neutral) if data unavailable. Ported from futures_trader.py Aug 9 2026.
    """
    if df5.empty or not _avg_vol_by_time:
        return 1.0
    slot = df5.index[-1].strftime('%H:%M')
    avg  = _avg_vol_by_time.get(slot, 0)
    if avg <= 0:
        return 1.0
    return float(df5['volume'].iloc[-1]) / avg


def calc_contracts_dynamic(price: float, sl: float,
                            rvol: float, ib_range: float,
                            side: str = 'LONG') -> int:
    """
    RVOL-based contract scaling. Ported verbatim from futures_trader.py Aug 9
    2026, hard cap swapped to TC's own get_max_contracts() (TC_TRADING_MAX_CONTRACTS=2).
    Base = 1.

    Tiers (additive):
      rvol ≥ 2×  → +1  (elevated participation)
      rvol ≥ 3×  → +1  (strong institutional interest)
      rvol ≥ 4×  → +1  (exceptional — capped by prop_rules anyway)
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
    n = max(1, min(n, get_max_contracts(n)))

    # ── SHORT SIZE CAP (Sep 2 2026) — mirrors futures_trader.py ────────────
    # The conviction ladder above is anti-predictive on the SHORT side only.
    # 5.5yr sim book: SHORT 1c n=297 +$3,012 (+$10.1/t) vs SHORT 2c n=59
    # -$5,035 (-$85.3/t, negative in 5 of 6 years). LONG 2c is +$26.7/t and
    # green 6/6, so the ladder is kept for longs. Mechanism: it scales on high
    # ATR + large gap; for shorts that is a violent gap-down (median -0.36 ATR,
    # ATR-rank 0.80) — panic read as conviction, doubled into the snapback.
    # Removes ZERO trades. Worth +$2,517 over 5.5yr, TC blow-ups 6 -> 4.
    if side == 'SHORT':
        n = min(n, SHORT_MAX_CONTRACTS)
    return n



# ── Daily Tide helper (Sep 2 2026) ─────────────────────────────────────────
_tide_cache = {}     # {date: (above_ma, prev_close, prev_ma)} — one read per day


def get_daily_tide():
    """PREVIOUS daily close vs its PREVIOUS 200-day MA. Returns (above, close, ma)
    or (None, None, None) if there is not enough daily history.

    Causal by construction — both sides of the comparison are shifted one session
    back, so nothing about today is used (same anchoring as
    futures/factory/conditions.py:209-211, which produced the backtest).
    Cached per calendar day: this is a daily-frequency signal, there is no reason
    to re-read it on a 60s scan loop.
    """
    today = datetime.now(ET).date()
    if today in _tide_cache:
        return _tide_cache[today]
    out = (None, None, None)
    try:
        import pandas as _pd
        _c = sqlite3.connect(MKT_DB_PATH)
        _d = _pd.read_sql_query(
            "SELECT ts_utc, close FROM futures_bars_1d WHERE symbol=? ORDER BY ts_utc",
            _c, params=(SYMBOL,))
        _c.close()
        if len(_d) > TIDE_MA_DAYS + 1:
            _d['d'] = _pd.to_datetime(_d.ts_utc, utc=True, format='ISO8601').dt.date
            _d = _d[_d.d < today]                     # never read today's own bar
            ma = _d.close.rolling(TIDE_MA_DAYS).mean()
            pc, pm = float(_d.close.iloc[-1]), float(ma.iloc[-1])
            if pm == pm:                              # not NaN
                out = (pc > pm, pc, pm)
    except Exception as e:
        log(f"  [TIDE] unavailable: {e}")
    _tide_cache[today] = out
    return out


def tide_agrees(side: str) -> bool:
    """True when the daily tide agrees with the trade direction (or is unknown —
    fails OPEN, so a missing daily bar can never silently halt trading)."""
    above, _, _ = get_daily_tide()
    if above is None:
        return True
    return above if side == 'LONG' else (not above)

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


def _log_partial_close(trade: dict, exit_price: float, pnl: float, pnl_ticks: float):
    """Record a partial scale-out as its own CLOSED row (1 contract) and
    decrement the open trade's contract count. Mirrors futures_trader.py."""
    now  = datetime.now(ET)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        INSERT INTO futures_trades
            (symbol, contract, entry_date, entry_time, entry_price,
             exit_date, exit_time, exit_price, contracts, side,
             target_price, stop_price, pnl, pnl_ticks, status,
             exit_reason, setup_type, session, instrument, account_mode, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (trade.get('symbol', SYMBOL), trade.get('contract', ''),
          trade['entry_date'], trade['entry_time'], trade['entry_price'],
          str(now.date()), now.strftime('%H:%M:%S'), exit_price, 1,
          trade.get('side', 'LONG'), trade.get('target_price'),
          trade.get('stop_price'), pnl, pnl_ticks, 'CLOSED',
          f'Partial scale-out +{PARTIAL_TAKE_PTS:.0f}pts (1 of {trade.get("contracts", 2)})',
          trade.get('setup_type', ''), trade.get('session', ''),
          SYMBOL, ACCOUNT_MODE,
          f'partial of trade #{trade["id"]}'))
    conn.execute('UPDATE futures_trades SET contracts=? WHERE id=?',
                 (max(1, trade.get('contracts', 2) - 1), trade['id']))
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

    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE futures_trades SET stop_price=?, stop_order_id=? WHERE id=?',
                 (new_sl, new_oid or None, trade['id']))
    conn.commit()
    conn.close()

    trade['stop_order_id'] = new_oid   # keep trade dict in sync for this cycle


def get_futures_daily_pnl() -> float:
    today = str(datetime.now(ET).date())   # use ET date, consistent with log_futures_entry
    conn  = sqlite3.connect(DB_PATH)
    row   = conn.execute(
        # RECONCILED rows are manual broker-side cleanups of bot bugs, not strategy
        # P&L. Excluded on the IBKR side since May 2026; TC never was — and on TC
        # this figure feeds check_can_trade()'s DLL gate and the daily circuit
        # breaker, so a phantom row could halt a live prop account. Fixed Sep 2 2026.
        "SELECT SUM(pnl) FROM futures_trades WHERE exit_date=? AND status='CLOSED' "
        "AND account_mode=? AND setup_type != 'RECONCILED'",
        (today, ACCOUNT_MODE)
    ).fetchone()
    conn.close()
    return round(float(row[0] or 0), 2)


def _get_all_time_futures_pnl() -> float:
    """Total realized P&L across all futures trades — used to reconcile prop_state balance."""
    conn = sqlite3.connect(DB_PATH)
    row  = conn.execute(
        "SELECT SUM(pnl) FROM futures_trades WHERE status='CLOSED' "
        "AND account_mode=? AND setup_type != 'RECONCILED'",   # see get_futures_daily_pnl
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
    global _trading_paused, _last_entry_time

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
        "SELECT COUNT(*) FROM futures_trades WHERE entry_date=? AND account_mode=? AND status != 'CANCELLED' AND COALESCE(notes,'') NOT LIKE 'partial of %'",   # partial scale-out rows are not new entries
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

    # 6b. Cooldown after any ENTRY — blocks the same still-valid signal from being
    # taken twice by consecutive 60s scans (the live duplicate-cluster mechanism).
    if _last_entry_time is not None:
        _e2 = (datetime.now(ET) - _last_entry_time).total_seconds() / 60
        if _e2 < ENTRY_COOLDOWN_MINUTES:
            log(f"  BLOCKED: entry cooldown {_e2:.1f}min "
                f"(need {ENTRY_COOLDOWN_MINUTES:.0f}min after last entry)")
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

    df5        = get_bars()
    atr        = calc_atr(df5) if not df5.empty else 10.0
    sl, target = calc_sl_target(price, atr, side)
    rvol       = calc_rvol_current(df5)

    # Stop-sanity ceiling — PORTED from futures_trader.py Sep 2 2026 (TC never had
    # it). Rejects an entry whose stop is absurdly far, which only happens on
    # broken/extreme bar data. Scales off BASE_STOP_PTS rather than a literal so it
    # cannot silently desync from the stop width (the exact bug the IBKR side hit
    # when BASE_STOP_PTS was raised and the ceiling stayed at the 150pt-era value).
    stop_pts = abs(price - sl)
    stop_sanity_ceiling = BASE_STOP_PTS + 100.0
    if stop_pts > stop_sanity_ceiling:
        log(f"  SKIP: stop {stop_pts:.0f}pts > {stop_sanity_ceiling:.0f} max (broken/extreme data)")
        return False
    # ── Daily Tide side gate (Sep 2 2026) — LOG ONLY while TIDE_GATE_ENABLED is False.
    # Records what it WOULD have blocked so it can be scored before it is trusted.
    _tide_above, _tide_c, _tide_m = get_daily_tide()
    if _tide_above is not None and not tide_agrees(side):
        _dist = (_tide_c / _tide_m - 1) * 100
        _verdict = 'BLOCKED' if TIDE_GATE_ENABLED else 'would block (LOG ONLY)'
        log(f"  [TIDE] {_verdict}: {side} while prev close {_tide_c:,.0f} is "
            f"{'above' if _tide_above else 'below'} the {TIDE_MA_DAYS}d MA "
            f"{_tide_m:,.0f} ({_dist:+.1f}%)")
        try:
            # get_session() rather than `session`: that local is not assigned until ~55
            # lines BELOW this point, so this call raised UnboundLocalError every time and
            # the bare except swallowed it. Net effect — the Daily Tide log-only trial
            # wrote ZERO rows to gate_blocks between Sep 2 and Sep 22 while printing
            # "would block" to the trader log, so the review due ~Sep 9 had nothing to
            # review. Third instance of this exact shape: log_block's own OVN_SKIP call
            # (TypeError on None, Aug 9) and the dead evening funnel line (NameError,
            # Sep 22) were the first two. A bare `except: pass` around instrumentation
            # turns a crash into a silent, permanent hole in the evidence.
            log_block(ACCOUNT_MODE, SYMBOL, side,
                      'TIDE' if TIDE_GATE_ENABLED else 'TIDE_INFO',
                      f'prev_close={_tide_c:.0f} ma{TIDE_MA_DAYS}={_tide_m:.0f} '
                      f'dist={_dist:+.1f}%', price, get_session())
        except Exception as _te:
            log(f"  [TIDE] could not record the block: {_te}")
        if TIDE_GATE_ENABLED:
            return False

    ib_range   = calc_ib_range_today(df5)
    contracts  = calc_contracts_dynamic(price, sl, rvol, ib_range, side)

    rr = abs(target - price) / abs(price - sl) if abs(price - sl) > 0 else 0
    # Use small tolerance to avoid floating-point false rejects at exactly MIN_RR
    if rr < MIN_RR - 0.01:
        log(f"  SKIP: R:R {rr:.2f} < min {MIN_RR}")
        return False

    # ── Submit order ──────────────────────────────────────
    order_side = 'BUY' if side == 'LONG' else 'SELL'
    _pre_qty = _get_ibkr_qty()   # snapshot before the order — verify the real fill below
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

    # Verify the actual filled quantity against the broker rather than trusting
    # the requested size (ported from the futures_trader.py/IBKR fix, Jul 23 2026
    # incident: an unverified `contracts` value caused a stop-close to over-buy
    # and open a phantom position once the real fill and the DB disagreed).
    time.sleep(3)
    _post_qty = _get_ibkr_qty()
    if _pre_qty is None or _post_qty is None:
        log(f"  WARNING: could not verify fill (bridge unavailable) — trusting requested size {contracts}")
    else:
        _actual_filled = round(abs(_post_qty - _pre_qty))
        if _actual_filled == 0:
            log(f"  Order submitted but position unchanged after 3s — entry not confirmed, aborting")
            send_telegram(f"⚠️ FUTURES(TC) entry order submitted but no fill detected (order {order_id}) — no DB entry written")
            return False
        if _actual_filled != contracts:
            log(f"  WARNING: requested {contracts} contracts, broker shows {_actual_filled} filled — using real fill size")
            send_telegram(f"⚠️ FUTURES(TC) entry partial/over fill: requested {contracts}, got {_actual_filled} (order {order_id})")
            contracts = _actual_filled

    # Real fill price (Sep 3 2026) — stops/targets/trail/P&L must key off what we
    # actually paid, not the scan price. See _get_fill_price().
    price = _get_fill_price(order_id, price)
    sl, target = calc_sl_target(price, atr, side)

    session  = get_session()
    # Setup label: priority order matches grade_entry's bonus hierarchy.
    # ALIGNED with futures_trader.py Sep 3 2026 — TC previously used a two-way
    # stub (`ORB_{side}` if orb else `VWAP_{side}`) so it could NEVER emit
    # PM_/MOM_/OPEN_. Measured live Sep 3: both accounts took the SAME trade with
    # pm_bull and orb_bull both true; IBKR labelled it PM_LONG by priority, TC
    # labelled it ORB_LONG by its stub. That single line is the whole "TC fires a
    # different setup mix" mystery — every cross-account per-setup P&L table was
    # comparing a 5-name vocabulary against a 2-name one.
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

    _last_entry_time = datetime.now(ET)   # arms the entry-cooldown gate

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
        f"Contracts: {contracts} × MNQ\n"
        f"Setup:     {setup} | Grade: {grade} ({score}pts)\n"
        f"Session:   {session} | Regime: {regime}\n"
        f"R:R:       {rr:.1f}\n"
        f"{backup_line}"
    )
    log(msg)
    send_telegram(msg)
    return True


# ── Monitor positions (exit stack) ───────────────────────

def exit_map(trade: dict, price: float) -> list[str]:
    """How far is this position from every exit that can ACTUALLY fire?

    Added Sep 3 2026. `target_price` in the DB is BASE_TARGET_PTS = 1500pts away —
    it has fired ZERO times in 951 trades over 5.5yr, and the best trade this book
    has ever produced ran 378pts. It exists only as (a) a disaster cap and (b) the
    numerator of the MIN_RR gate. Showing it as "the target" is actively
    misleading: it tells you nothing about how close you are to being out.

    This lists the exits that DO fire, nearest first, so that if you are deciding
    whether to pull the plug by hand you can see whether the system was about to
    do it for you or was nowhere near.
    """
    out, is_short = [], trade['side'] == 'SHORT'
    entry = float(trade['entry_price'])
    ct    = int(trade['contracts'])
    sgn   = -1 if is_short else 1
    pnl_pts = (entry - price) if is_short else (price - entry)

    def money(pts):  return pts / TICK_SIZE * TICK_VALUE * ct
    def away(level): return abs(price - level), (abs(price - level) / price * 100)

    # 1. trail stop — the real floor
    stop = trade.get('stop_price')
    if stop:
        d, pct = away(float(stop))
        locked = (float(stop) - entry) * sgn
        out.append((d, f"stop {float(stop):,.2f}  {d:>6.1f}pt ({pct:.2f}%)  "
                      f"→ locks ${money(locked):+,.0f}"))

    # 2. reversal exit — only armed once peak cleared the floor
    tid  = trade['id']
    s_pk = _session_high.get(tid) if not is_short else _session_low.get(tid)
    if s_pk is not None:
        peak = (entry - s_pk) if is_short else (s_pk - entry)
        if peak >= REV_EXIT_PEAK_MIN_PTS:
            trig_pts = peak * (1 - REV_EXIT_RETRACE_FRAC)
            lvl = entry + sgn * trig_pts
            d, pct = away(lvl)
            streak = _rev_state.get(tid, {}).get('adv_streak', 0)
            out.append((d, f"rev-exit {lvl:,.2f}  {d:>6.1f}pt ({pct:.2f}%)  "
                          f"→ ARMED (peak +{peak:.0f}, {streak}/{REV_EXIT_CONFIRM_BARS} adverse bars)"))
        else:
            out.append((9e9, f"rev-exit  not armed — peak +{peak:.0f}pt of "
                             f"{REV_EXIT_PEAK_MIN_PTS:.0f} needed"))

    # 3. partial scale-out — the only "target" that ever fires
    if ct >= 2 and not _partial_done.get(tid):
        lvl = entry + sgn * PARTIAL_TAKE_PTS
        d, pct = away(lvl)
        out.append((d, f"partial  {lvl:,.2f}  {d:>6.1f}pt ({pct:.2f}%)  "
                      f"→ banks 1ct ≈ ${PARTIAL_TAKE_PTS/TICK_SIZE*TICK_VALUE:+,.0f}"))

    # 4. no-move — a TIME exit, not a price one
    try:
        et_now = datetime.now(ET)
        opened = ET.localize(datetime.strptime(
            f"{trade['entry_date']} {trade['entry_time']}", '%Y-%m-%d %H:%M:%S'))
        mins = (et_now - opened).total_seconds() / 60
        left = NO_MOVE_MINUTES - mins
        in_band = NO_MOVE_MIN_PTS <= pnl_pts <= NO_MOVE_MAX_PTS
        if left > 0:
            out.append((9e9, f"no-move  in {left:>5.0f}min  "
                             f"→ {'WILL fire' if in_band else 'will NOT fire'} "
                             f"(needs {NO_MOVE_MIN_PTS:+.0f}..{NO_MOVE_MAX_PTS:+.0f}pt, now {pnl_pts:+.0f})"))
        elif in_band:
            out.append((0, f"no-move  DUE NOW ({pnl_pts:+.0f}pt is inside the band)"))
    except Exception:
        pass

    # 5. EOD flatten
    try:
        et_now = datetime.now(ET)
        eod = et_now.replace(hour=HARD_CLOSE[0], minute=HARD_CLOSE[1], second=0, microsecond=0)
        mins = (eod - et_now).total_seconds() / 60
        if mins > 0:
            out.append((9e9, f"EOD      in {mins:>5.0f}min ({HARD_CLOSE[0]:02d}:{HARD_CLOSE[1]:02d} ET) → flattens regardless"))
    except Exception:
        pass

    out.sort(key=lambda x: x[0])
    return [t for _, t in out]


def _publish_exit_map(open_trades: list) -> None:
    """Write the live exit map so the DASHBOARD can show it without importing the
    trader. The rev-exit level depends on `_session_high`, which only exists in
    this process's memory — the dashboard has no way to derive it from the DB.
    Cheap (a few dict entries, atomic replace, once per monitor cycle) and never
    raises into the monitor. Added Sep 3 2026."""
    try:
        import json as _json
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'logs', 'heartbeat')
        os.makedirs(d, exist_ok=True)
        if not open_trades:                     # flat: publish empty, no bridge call
            price = 0.0
        else:
            price = get_live_price() or 0.0
        payload = {'ts': datetime.now(ET).isoformat(), 'price': price, 'positions': []}
        for t in open_trades:
            payload['positions'].append({
                'trade_id': t['id'], 'side': t['side'], 'contracts': t['contracts'],
                'entry': t['entry_price'], 'exits': exit_map(t, price),
            })
        p = os.path.join(d, f'exitmap_{ACCOUNT_MODE}.json')
        tmp = p + '.tmp'
        with open(tmp, 'w') as fh:
            _json.dump(payload, fh)
        os.replace(tmp, p)
    except Exception:
        pass



def _net_usd(gross_usd: float, contracts: int) -> float:
    """Gross P&L minus the real round-turn commission for the contracts actually closed.

    Added Sep 7 2026. Until then NY exits recorded GROSS while london_trader.py recorded NET,
    so the two books were not comparable and every NY live figure was overstated by
    COMMISSION x contracts per trade. It matters more than the size suggests: this number is
    what `record_trade_pnl()` feeds to prop_rules, and on TC that drives the DLL / trailing-MLL
    gates — a gross figure makes the account look further from its loss limit than it is.

    Applied ONLY at the recording boundary (DB write, prop-rule tracker, Telegram). The live
    `pnl_usd` used for trail tiers and the dollar circuit breaker is deliberately left GROSS,
    because charging commission there would shift real exit thresholds — a strategy change,
    which the Sep 3 evidence freeze rules out. Books get accurate; decisions do not move.
    """
    return round(gross_usd - COMMISSION * max(1, int(contracts)), 2)

def monitor_open_trades(regime: str = 'NORMAL'):
    """
    Check all open futures positions and apply exit stack.
    Runs every MONITOR_INTERVAL seconds.
    """
    trades = get_open_futures_trades()
    _publish_exit_map(trades)   # dashboard visibility (Sep 3 2026) — no bridge call when flat
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

        # ── Regime-aware profit locks (aligned with futures_trader.py Jul 25 2026) ──
        # Three tiers, each only tightens — never loosens the stop. Params vary
        # by day type (set at 10:30 IB formation; CHOPPY default before that).
        s_peak    = _session_low.get(tid, price) if is_short else _session_high.get(tid, price)
        peak_pts  = (entry - s_peak) if is_short else (s_peak - entry)

        _rp = EXIT_PARAMS_BY_REGIME.get(_day_regime or 'CHOPPY', EXIT_PARAMS_BY_REGIME['CHOPPY'])
        _be_pts, _be_frac      = _rp['be_pts'], _rp['be_frac']
        _wide_pts, _wide_gap   = _rp['wide_pts'], _rp['wide_gap']
        _tight_pts, _tight_gap = _rp['tight_pts'], _rp['tight_gap']

        # Crest Watch — LLM in-trade reversal-risk observer (log-only, Aug 9
        # 2026, redesigned same night — see futures/thesis_check.py). Reads
        # the same structured state the exit logic above already computed
        # this cycle; no chart image involved.
        try:
            # Cheap eligibility pre-check before doing any RVOL/RSI work — most
            # cycles won't qualify (not yet profitable, or under the peak floor,
            # or throttled), no reason to recompute indicators for those.
            if pnl_pts > 0 and peak_pts >= _thesis_check.MIN_PEAK_PTS:
                if pnl_pts >= _tight_pts:
                    _trail_tier = 'tight_trail'
                elif pnl_pts >= _wide_pts:
                    _trail_tier = 'wide_trail'
                elif pnl_pts >= _be_pts:
                    _trail_tier = 'be_lock'
                else:
                    _trail_tier = 'none'
                _stop_dist = abs(price - sl)
                _tc_context = {
                    'regime':        _day_regime or 'unknown',
                    'rvol':          round(calc_session_rvol(df5), 2) if df5 is not None and not df5.empty else None,
                    'rsi':           round(calc_rsi(df5['close']), 1) if df5 is not None and not df5.empty else None,
                    'price_vs_vwap': ('above' if vwap_now and price > vwap_now else 'below') if vwap_now else 'unknown',
                }
                _thesis_check.maybe_check(tid, ACCOUNT_MODE, 'NY', SYMBOL, side, entry, price,
                                           pnl_pts, peak_pts, _stop_dist, _trail_tier, _tc_context, log)
        except Exception as _e:
            log(f"  Crest Watch context error (trade {tid}): {_e}")

        # Tier 1: proportional lock-in — protect _be_frac of the peak move
        if pnl_pts >= _be_pts:
            locked = round(peak_pts * _be_frac, 2)
            be = round(entry + max(locked, TICK_SIZE), 2) if not is_short else round(entry - max(locked, TICK_SIZE), 2)
            if (not is_short and be > sl) or (is_short and be < sl):
                sl = be
                _update_backup_stop(trade, sl)
                log(f"  {SYMBOL}{'SHORT' if is_short else ''}: BE lock({_be_frac:.0%} of {peak_pts:.0f}pk, {_day_regime}) → {sl} (+{pnl_pts:.0f}pts)")

        # Tier 2: trail _wide_gap behind session peak — let it run
        if pnl_pts >= _wide_pts:
            trail = round(s_peak - _wide_gap, 2) if not is_short else round(s_peak + _wide_gap, 2)
            if (not is_short and trail > sl) or (is_short and trail < sl):
                sl = trail
                _update_backup_stop(trade, sl)
                log(f"  {SYMBOL}{'SHORT' if is_short else ''}: trail({_wide_gap:.0f}, {_day_regime}) → {sl} (+{pnl_pts:.0f}pts)")

        # Tier 3: tighten to _tight_gap behind peak — lock in most of the gain
        if pnl_pts >= _tight_pts:
            trail = round(s_peak - _tight_gap, 2) if not is_short else round(s_peak + _tight_gap, 2)
            if (not is_short and trail > sl) or (is_short and trail < sl):
                sl = trail
                _update_backup_stop(trade, sl)
                log(f"  {SYMBOL}{'SHORT' if is_short else ''}: trail({_tight_gap:.0f}, {_day_regime}) → {sl} (+{pnl_pts:.0f}pts)")

        # ── Partial scale-out (aligned Jul 25 2026 — was dormant while TC
        # sizing was hardcoded to 1 contract; reactivated Aug 9 2026 by the
        # calc_contracts_dynamic fix, now fires on genuine 2-contract trades) ──
        if (contracts >= 2 and not _partial_done.get(tid)
                and pnl_pts >= PARTIAL_TAKE_PTS):
            _pp = _bridge_get('/futures/position')
            if isinstance(_pp, list):
                _pq = 0.0
                for _p in _pp:
                    if _p.get('symbol') == SYMBOL:
                        _pq = float(_p.get('qty', 0))
                        break
                _held = (_pq <= -contracts) if is_short else (_pq >= contracts)
                if _held:
                    _pside = 'BUY' if is_short else 'SELL'
                    _pr = _bridge_post('/futures/order', {
                        'symbol': SYMBOL, 'qty': 1, 'side': _pside,
                        'order_type': 'MARKET',
                    })
                    if _pr.get('status') == 'submitted':
                        _partial_done[tid] = True
                        _ppts = pnl_pts
                        _pusd = _ppts / TICK_SIZE * TICK_VALUE * 1
                        _pnet = _net_usd(_pusd, 1)     # one contract's round turn
                        _log_partial_close(trade, price, _pnet,
                                           round(_ppts / TICK_SIZE, 1))
                        record_trade_pnl(_pnet)
                        contracts -= 1
                        trade['contracts'] = contracts
                        pnl_usd = pnl_ticks * TICK_VALUE * contracts
                        _be_px = round(entry + (TICK_SIZE if not is_short else -TICK_SIZE), 2)
                        if (not is_short and _be_px > sl) or (is_short and _be_px < sl):
                            sl = _be_px
                        _update_backup_stop(trade, sl)
                        msg = (
                            f"🟡 PARTIAL SCALE-OUT (TC)\n"
                            f"{SYMBOL} {side}: banked 1 of {contracts + 1} @ {price} "
                            f"(+{_ppts:.0f}pts / ${_pusd:+.0f})\n"
                            f"Runner: 1 contract, stop → breakeven ({sl})"
                        )
                        log(msg)
                        send_telegram(msg)
                    else:
                        log(f"  partial scale-out order failed ({_pr}) — retry next cycle")

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

        # 4b. Reversal-detection exit (aligned with futures_trader.py Jul 25 2026).
        # After the trade proved a real peak (≥REV_EXIT_PEAK_MIN_PTS), exit at
        # market when REV_EXIT_CONFIRM_BARS consecutive CLOSED 5-min bars go
        # against the position AND ≥REV_EXIT_RETRACE_FRAC of the peak is gone.
        if not exit_reason and df5 is not None and not df5.empty and len(df5) >= 3:
            try:
                _completed = df5
                if (datetime.now(ET) - df5.index[-1]).total_seconds() < 300:
                    _completed = df5.iloc[:-1]      # drop the forming bar only
                _last_ts   = _completed.index[-1]
                _st = _rev_state.setdefault(tid, {'last_ts': None, 'adv_streak': 0, 'prev_close': None})
                if _st['last_ts'] is None or _last_ts > _st['last_ts']:
                    _c = float(_completed['close'].iloc[-1])
                    if _st['prev_close'] is not None:
                        _adverse = (_c < _st['prev_close']) if not is_short else (_c > _st['prev_close'])
                        _st['adv_streak'] = _st['adv_streak'] + 1 if _adverse else 0
                    _st['prev_close'] = _c
                    _st['last_ts']    = _last_ts
                if (peak_pts >= REV_EXIT_PEAK_MIN_PTS
                        and _st['adv_streak'] >= REV_EXIT_CONFIRM_BARS
                        and (peak_pts - pnl_pts) >= REV_EXIT_RETRACE_FRAC * peak_pts):
                    exit_reason = (
                        f'Reversal exit: peak +{peak_pts:.0f}pts, gave back '
                        f'{peak_pts - pnl_pts:.0f} over {_st["adv_streak"]} closed bars '
                        f'({pnl_pct:+.1f}% / ${pnl_usd:+.0f})'
                    )
            except Exception as _e:
                log(f"  rev-exit check error (trade {tid}): {_e}")

        # 5. No-move exit (time-based — dead trade, free the slot)
        # Only during active session; EOD close handles anything still open at 4pm.
        if not exit_reason and get_session() not in ('EOD', 'CLOSED'):
            try:
                entry_dt = ET.localize(datetime.strptime(
                    f"{trade['entry_date']} {trade['entry_time']}", '%Y-%m-%d %H:%M:%S'
                ))
                age_min = (now - entry_dt).total_seconds() / 60
                if (age_min >= NO_MOVE_MINUTES
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

            global _last_exit_time
            _last_exit_time = datetime.now(ET)
            _ibkr_qty = 0.0
            for _p in _ibkr_pos:
                if _p.get('symbol') == SYMBOL:
                    _ibkr_qty = float(_p.get('qty', 0))
                    break
            _position_held = (_ibkr_qty > 0) if not is_short else (_ibkr_qty < 0)

            if not _position_held:
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
                pnl_net = _net_usd(pnl_usd, contracts)
                log_futures_exit(tid, price, f"[backup-stop] IBKR stop @ {backup_stop_px} filled",
                                 pnl_net, round(pnl_ticks, 1))
                record_trade_pnl(pnl_net)
                pnl_usd = pnl_net          # Telegram below reports what was actually booked
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
                exits.append({'tid': tid, 'pnl': pnl_usd})
                for d in (_session_high, _session_low, _price_history, _partial_done, _rev_state):
                    d.pop(tid, None)
                continue

            # Position confirmed on IBKR — cancel backup stop then place software exit
            _cancel_backup_stop(trade)
            cover_side = 'BUY' if is_short else 'SELL'
            result = _bridge_post('/futures/order', {
                'symbol':     SYMBOL,
                'qty':        contracts,
                'side':       cover_side,
                'order_type': 'MARKET',
            })

            pnl_net = _net_usd(pnl_usd, contracts)
            log_futures_exit(tid, price, exit_reason, pnl_net, round(pnl_ticks, 1))
            record_trade_pnl(pnl_net)
            pnl_usd = pnl_net              # Telegram below reports what was actually booked

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
            for d in (_session_high, _session_low, _price_history, _partial_done, _rev_state):
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


# ── Main scan loop ────────────────────────────────────────

def run_scan():
    """5-min scan: check regime, signals, enter if qualified."""
    _beat('futures_tc')   # watchdog liveness stamp (Sep 3 2026)
    global _confirmed_scans, _regime_scan_counts, _cached_df5

    if date.today() in CME_HOLIDAYS_2026:
        log("CME holiday — scan skipped")
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
    _regime_scan_counts[regime] = _regime_scan_counts.get(regime, 0) + 1   # daily tally (logging only)
    # F1 fix (ported from futures_trader.py Jul 25 2026): _confirmed_scans must
    # be CONSECUTIVE same-regime reads, advanced at most once per completed
    # 5-min bar — the old cumulative per-day tally at 60s cadence could unlock
    # SHORT from 3 scattered WEAK minutes.
    global _streak_regime, _streak_bar_ts
    _streak_bar = None
    if not df5.empty:
        _bar_age = (datetime.now(ET) - df5.index[-1]).total_seconds()
        if _bar_age >= 300:
            _streak_bar = df5.index[-1]
        elif len(df5) >= 2:
            _streak_bar = df5.index[-2]
    if regime != _streak_regime:
        _streak_regime  = regime
        _streak_bar_ts  = _streak_bar
        _confirmed_scans = 1
    elif _streak_bar is not None and _streak_bar != _streak_bar_ts:
        _streak_bar_ts   = _streak_bar
        _confirmed_scans += 1
    log(f"Regime: {regime} (×{_confirmed_scans} consec bars) | Daily P&L: ${get_futures_daily_pnl():+.0f}")

    if not is_entry_allowed():
        log(f"Session {session} — no new entries")
        return

    # ── IB classification (computed once at 10:30, sticky all day) ───────────
    # Ported from futures_trader.py Jul 25 2026 — sets _day_regime (exit-stack
    # weather) + _ib_kind (Day Shape, SHORT-gate bypass) + hero_regime input.
    global _ib_kind, _ib_kind_set, _day_regime
    if not _ib_kind_set and not df5.empty:
        _ibr = calc_ib_range_today(df5)
        _now_c = datetime.now(ET)
        if _ibr >= 50.0 and (_now_c.hour > 10 or (_now_c.hour == 10 and _now_c.minute >= 30)):
            _df_today = df5[df5.index.date == _now_c.date()]
            if not _df_today.empty:
                _ib_hi  = float(_df_today['high'].max())
                _ib_lo  = float(_df_today['low'].min())
                _ib_cl  = float(_df_today['close'].iloc[-1])
                _ib_rng = _ib_hi - _ib_lo
                _ib_mid = 0.5
                if _ib_rng > 0:
                    _ib_mid = (_ib_cl - _ib_lo) / _ib_rng
                    if _ib_mid < 0.25:
                        _ib_kind = 'BEAR_DIRECTIONAL'
                    elif _ib_mid > 0.75:
                        _ib_kind = 'BULL_DIRECTIONAL'
                    else:
                        _ib_kind = 'ROTATIONAL'
                _day_regime  = detect_regime(_ibr)
                _ib_kind_set = True
                log(f"IB classified: {_ib_kind} | regime={_day_regime} | range={_ibr:.0f}pts | mid={_ib_mid:.2f}")

    # Get signals
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
        log_shadow_signal('TC', 'MNQ', df5['close'].tail(3).tolist(), vwap, price, session)
    except Exception:
        pass

    # Open trades count
    open_trades = get_open_futures_trades()
    if len(open_trades) >= MAX_OPEN_TRADES:
        log(f"Max trades open ({len(open_trades)}) — skip")
        return

    # ── IB window gate: no entries until Initial Balance is fully established ──
    # Backtest finding (Jun 1 2026): 10:xx entries (right after IB forms at 10:30am)
    # have 52.7% WR vs 9:xx entries which are noise. The backtest enforces this
    # implicitly — live trading must mirror it. IB window = 60min from 9:30am = 10:30am.
    now_et = datetime.now(ET)
    today_et = now_et.date()
    ib_ready_at = ET.localize(datetime(today_et.year, today_et.month, today_et.day, 10, 30))
    if now_et < ib_ready_at:
        mins_left = int((ib_ready_at - now_et).total_seconds() / 60) + 1
        log(f"IB window not complete — waiting for 10:30am ET ({mins_left}min remaining)")
        return

    # ── pm_ib hold: give user 5 min to send FUT BIAS after macro range is set ──
    # Only applies in the morning window (before 11am ET). After 11am the pre-market
    # IB data is stale and restarts should not re-trigger the hold — entries would
    # be blocked for 5 min after every mid-day restart, which is unacceptable.
    if (_pm_ib_set and _pm_ib_set_time is not None
            and datetime.now(ET).hour < 11):
        elapsed = (datetime.now(ET) - _pm_ib_set_time).total_seconds()
        if elapsed < 300:
            remaining = int(300 - elapsed)
            log(f"pm_ib hold — {remaining}s remaining. Send FUT BIAS LONG/SHORT if needed.")
            return

    # ── Overnight skip zone gate (log-only, aligned with futures_trader.py) ──
    # Whole-day veto REMOVED Jul 18 2026 on the IBKR side — mirrored here, never
    # blocks entries; kept under the 'OVN_SKIP' gate name so gate_audit scores it.
    if _overnight_skip_day and _daily_macro_bias == 'BOTH':
        log(f"Overnight skip zone (pos={_overnight_position}) — INFO ONLY (veto removed), trading continues")
        try: log_block('TC', 'MNQ', 'BOTH', 'OVN_SKIP', f'pos={_overnight_position}', price, session)
        except Exception: pass

    # ── Entry gates ALIGNED with futures_trader.py (Jul 25 2026) ─────────────
    # A+-only + Trend Jury (hero gate) + Volume Pulse (RVOL, graduated floor
    # with Hero-GOLD compensation) + 30-min HTF agreement. Prop rules
    # (DLL/consistency/sizing) unchanged — this only upgrades signal quality.
    hero_regime = _day_regime if _day_regime else 'CHOPPY'
    try:
        bars_hist = get_bars(bar_size_min=5, days=2)
        prev_rth  = bars_hist[bars_hist.index.date < datetime.now(ET).date()] if not bars_hist.empty else pd.DataFrame()
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
        if grade != 'A+':   # A+-only (aligned Jul 25 2026 — A-grade diluted WR 56%→41%)
            try: log_block('TC', 'MNQ', 'LONG', 'GRADE', f'{grade}({score})', price, session)
            except Exception: pass
        else:
            h_score, h_flags = score_entry_regime(price_now, atr_now, 'LONG',
                                                   bars_hist, prev_rth, hero_regime)
            contracts_hero = contracts_from_regime_score(h_score, hero_regime, 2)
            if contracts_hero == 0:
                log(f"LONG signal: {grade} ({score}pts) — HERO SKIP "
                    f"(weighted={h_score}, regime={hero_regime}, flags={h_flags})")
                try: log_block('TC', 'MNQ', 'LONG', 'HERO', f'score={h_score}/{hero_regime}', price_now, session)
                except Exception: pass
            else:
                entry_rvol = calc_session_rvol(df5)
                rvol_ok = entry_rvol >= ENTRY_MIN_RVOL
                rvol_compensated = (
                    not rvol_ok and entry_rvol >= RVOL_GRAD_FLOOR
                    and is_gold_score(h_score, hero_regime)
                )
                if not rvol_ok and not rvol_compensated:
                    log(f"LONG signal: {grade} ({score}pts) — RVOL SKIP "
                        f"({entry_rvol:.2f} < {ENTRY_MIN_RVOL})")
                    try: log_block('TC', 'MNQ', 'LONG', 'RVOL_ENTRY', f'{entry_rvol:.2f}', price_now, session)
                    except Exception: pass
                elif calc_htf_trend(df5) != 1:
                    log(f"LONG signal: {grade} ({score}pts) — HTF SKIP (30min trend not up)")
                    try: log_block('TC', 'MNQ', 'LONG', 'HTF', 'not_up', price_now, session)
                    except Exception: pass
                else:
                    _rvol_note = ' (RVOL-compensated by Hero GOLD)' if rvol_compensated else ''
                    log(f"LONG signal: {grade} ({score}pts) | heroes={h_score}/{hero_regime} "
                        f"rvol={entry_rvol:.2f} htf=up — entering{_rvol_note}")
                    if place_trade('LONG', sig, regime, score, grade):
                        try: log_enter('TC', 'MNQ', 'LONG', f'{grade}({score})', price_now, session)
                        except Exception: pass
    else:
        try: log_block('TC', 'MNQ', 'LONG', 'REGIME', regime, price, session)
        except Exception: pass

    # ── Try SHORT entry ────────────────────────────────────
    # short_allowed (aligned Jul 25 2026):
    #   1. WEAK regime confirmed ≥3 consecutive 5-min bars
    #   2. Overnight macro bias is SHORT (NFP/CPI directional day)
    #   3. BEAR_DIRECTIONAL IB (day closed near IB low — already showed its hand)
    short_allowed = (
        (regime == 'WEAK' and _confirmed_scans >= 3) or
        (_daily_macro_bias == 'SHORT') or
        (_ib_kind == 'BEAR_DIRECTIONAL')
    )
    if short_allowed:
        score, grade = grade_entry(sig, regime, 'SHORT')
        if grade != 'A+':   # A+-only — see LONG side comment
            try: log_block('TC', 'MNQ', 'SHORT', 'GRADE', f'{grade}({score})', price, session)
            except Exception: pass
        else:
            h_score, h_flags = score_entry_regime(price_now, atr_now, 'SHORT',
                                                   bars_hist, prev_rth, hero_regime)
            contracts_hero = contracts_from_regime_score(h_score, hero_regime, 2)
            if contracts_hero == 0:
                log(f"SHORT signal: {grade} ({score}pts) — HERO SKIP "
                    f"(weighted={h_score}, regime={hero_regime}, flags={h_flags})")
                try: log_block('TC', 'MNQ', 'SHORT', 'HERO', f'score={h_score}/{hero_regime}', price_now, session)
                except Exception: pass
            else:
                entry_rvol = calc_session_rvol(df5)
                rvol_ok = entry_rvol >= ENTRY_MIN_RVOL
                rvol_compensated = (
                    not rvol_ok and entry_rvol >= RVOL_GRAD_FLOOR
                    and is_gold_score(h_score, hero_regime)
                )
                if not rvol_ok and not rvol_compensated:
                    log(f"SHORT signal: {grade} ({score}pts) — RVOL SKIP "
                        f"({entry_rvol:.2f} < {ENTRY_MIN_RVOL})")
                    try: log_block('TC', 'MNQ', 'SHORT', 'RVOL_ENTRY', f'{entry_rvol:.2f}', price_now, session)
                    except Exception: pass
                elif calc_htf_trend(df5) != -1:
                    log(f"SHORT signal: {grade} ({score}pts) — HTF SKIP (30min trend not down)")
                    try: log_block('TC', 'MNQ', 'SHORT', 'HTF', 'not_down', price_now, session)
                    except Exception: pass
                else:
                    _rvol_note = ' (RVOL-compensated by Hero GOLD)' if rvol_compensated else ''
                    log(f"SHORT signal: {grade} ({score}pts) | heroes={h_score}/{hero_regime} "
                        f"ib={_ib_kind} rvol={entry_rvol:.2f} htf=down — entering{_rvol_note}")
                    if place_trade('SHORT', sig, regime, score, grade):
                        try: log_enter('TC', 'MNQ', 'SHORT', f'{grade}({score})', price_now, session)
                        except Exception: pass
    else:
        try: log_block('TC', 'MNQ', 'SHORT', 'REGIME', regime, price, session)
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
        log("reset_daily_state: CME holiday — skipping")
        return

    global _orb_high, _orb_low, _orb_set, _confirmed_scans
    global _regime_scan_counts, _session_high, _session_low
    global _price_history, _partial_done, _peak_daily_pnl, _daily_pnl
    global _pm_high, _pm_low, _pm_ib_set, _pm_ib_set_time, _daily_macro_bias
    global _overnight_bias, _overnight_skip_day, _overnight_position, _overnight_computed

    _orb_high = _orb_low = None
    _orb_set  = False
    _pm_high = _pm_low = None
    _pm_ib_set      = False
    _pm_ib_set_time = None
    _daily_macro_bias = 'BOTH'
    _overnight_bias     = 'BOTH'
    _overnight_skip_day = False
    _overnight_position = None
    _overnight_computed = False
    _confirmed_scans  = 0
    _regime_scan_counts = {'STRONG': 0, 'NORMAL': 0, 'WEAK': 0}
    _session_high = {}
    _session_low  = {}
    _price_history = {}
    _partial_done  = {}
    global _rev_state, _streak_regime, _streak_bar_ts, _ib_kind, _ib_kind_set, _day_regime
    _rev_state     = {}
    _streak_regime = None
    _streak_bar_ts = None
    _ib_kind       = None
    _ib_kind_set   = False
    _day_regime    = None
    _daily_pnl     = 0.0
    _peak_daily_pnl = 0.0

    prop_load()   # refresh prop rules state for new day
    log("Daily state reset")
    send_telegram(
        f"🌅 FUTURES day started\n"
        f"{format_prop_status()}"
    )


def morning_gateway_health_check():
    """
    Called at 8:15am ET. Verifies the TC bridge (BRIDGE) is connected to IBKR.
    If not, restarts com.sushil.trading.tc_gateway and waits up to 60s for reconnect.
    Must run before the 8:30am pre-market IB capture window.
    """
    def _connected():
        try:
            r = requests.get(BRIDGE, timeout=5)
            return r.status_code == 200 and r.json().get('connected') is True
        except Exception:
            return False

    if _connected():
        log("Gateway health: OK (connected)")
        return

    log("Gateway health: not connected — restarting tc_gateway...")
    send_telegram("⚠️ TC gateway down at pre-flight — auto-restarting now...")
    try:
        subprocess.run(
            ['launchctl', 'kickstart', '-k', f'gui/{os.getuid()}/com.sushil.trading.tc_gateway'],
            timeout=15, check=True
        )
    except Exception as e:
        log(f"Gateway restart failed: {e}")
        send_telegram(f"❌ TC gateway restart failed: {e} — restart manually")
        return

    for _ in range(6):
        time.sleep(10)
        if _connected():
            log("TC gateway restarted successfully")
            send_telegram("✅ TC gateway restarted and connected.")
            return

    log("TC gateway still not connected after restart")
    send_telegram("⚠️ TC gateway still down after restart — TC trading offline today. Check IBC.")


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
        f"🌙 FUTURES EOD\n"
        f"Day P&L:      ${daily:+.2f}\n"
        f"Balance:      ${s.get('balance', 0):,.0f}\n"
        f"TC Progress:  ${s.get('total_profit', 0):,.0f} / $3,000 "
        f"(${s.get('tc_target_left', 0):,.0f} left)\n"
        f"MLL buffer:   ${s.get('buffer_to_mll', 0):,.0f}\n"
        f"Resets tomorrow at 9:28am ET"
    )


# ── Telegram commands ─────────────────────────────────────

def poll_telegram_commands():
    """Poll for Telegram commands — PAUSE, RESUME, STATUS, CLOSE."""
    global _trading_paused, _tg_offset, _daily_macro_bias, _overnight_skip_day
    # Dashboard close requests ride this same 10s job. Drained FIRST and outside the
    # Telegram guard below: if the Telegram token is missing or the API is down, a close
    # button must still work.
    _drain_control_queue()
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
            if 'FUT HELP' in msg:
                send_telegram(
                    "📖 *Futures commands* (this chat is shared — IBKR paper "
                    "AND TC sandbox each pick up every command independently):\n"
                    "FUT HELP                 — this message\n"
                    "FUT PAUSE / FUT RESUME   — halt/resume new entries (monitor stays on)\n"
                    "FUT STATUS               — price, bias, session, open positions + uPnL\n"
                    "FUT BIAS LONG/SHORT/BOTH — manual macro bias override\n"
                    "FUT CLOSE                — flatten ALL open positions now\n"
                    "STATUS ALL               — combined portfolio across all systems\n"
                    "\n"
                    "Send a command ONCE — no need to repeat it per account."
                )
            elif 'FUT PAUSE' in msg:
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
                    # Distance to every exit that can ACTUALLY fire. The DB's
                    # target_price is the 1500pt disaster cap (0 hits in 951
                    # trades / 5.5yr) and tells you nothing about how close you
                    # are to being out — this does. Added Sep 3 2026 so a manual
                    # FUT CLOSE is an informed decision, not a blind one.
                    try:
                        for _ln in exit_map(t, live):
                            pos_lines.append('     ' + _ln)
                    except Exception as _e:
                        pos_lines.append(f'     (exit map unavailable: {_e})')
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
                    f"✅ Macro bias set: LONG (overnight skip overridden)\n"
                    f"System will only take LONG entries today.\n"
                    f"PM High: {_pm_high}  (target for pm_break signal)"
                )
            elif 'FUT BIAS SHORT' in msg:
                _daily_macro_bias   = 'SHORT'
                _overnight_skip_day = False
                send_telegram(
                    f"✅ Macro bias set: SHORT (overnight skip overridden)\n"
                    f"System will only take SHORT entries today.\n"
                    f"PM Low: {_pm_low}  (target for pm_break signal)"
                )
            elif 'FUT BIAS BOTH' in msg:
                _daily_macro_bias   = 'BOTH'
                _overnight_skip_day = False
                send_telegram("✅ Macro bias cleared — overnight filter disabled for today.")
            elif 'FUT CLOSE' in msg:
                _force_close_all()
            elif 'STATUS ALL' in msg:
                send_telegram(_portfolio_all())
    except Exception as e:
        log(f"[TG poll error] {e}")


def _drain_control_queue():
    """Execute dashboard close requests for THIS account's NY futures book.

    The dashboard never places an order (see the note above control_queue in
    database.py). It records an intent; this runs on the existing 10s Telegram poll and
    closes through _force_close_all() — the only path that sizes the closing order off
    the REAL broker position rather than the sum of DB rows (Jul 23 2026 incident).

    Requests are claimed with this trader's ACCOUNT_MODE, so the IBKR and TC processes —
    which poll this same table 10s apart — can never execute each other's request.
    """
    try:
        from database import claim_control_requests, complete_control_request
        reqs = claim_control_requests('futures_ny', ACCOUNT_MODE)
    except Exception as e:
        log(f"[control] claim failed: {e}")
        return
    for r in reqs:
        try:
            trades = get_open_futures_trades()
            if not trades:
                complete_control_request(r['id'], 'DONE',
                                         f'{ACCOUNT_MODE} NY: no open position')
                continue
            if r['action'] == 'CLOSE_ONE':
                # This book runs MAX_OPEN_TRADES=1, so "close this one" and "flatten" are
                # the same action — and _force_close_all is the only close that verifies
                # against the broker. If there is somehow more than one row, refuse rather
                # than improvise a partial close: per-row sizing is exactly what over-bought
                # and opened a phantom position on Jul 23 2026.
                if len(trades) > 1:
                    complete_control_request(
                        r['id'], 'FAILED',
                        f'{len(trades)} open rows — use Close all so the order is sized '
                        f'off the real broker position')
                    continue
                if r['trade_id'] is not None and trades[0]['id'] != r['trade_id']:
                    complete_control_request(
                        r['id'], 'FAILED',
                        f"trade #{r['trade_id']} is not open on {ACCOUNT_MODE}")
                    continue
            _force_close_all()
            still = get_open_futures_trades()
            ok = not still
            complete_control_request(
                r['id'], 'DONE' if ok else 'FAILED',
                f'{ACCOUNT_MODE} NY flattened' if ok
                else f'{len(still)} row(s) still open — check the broker')
        except Exception as e:
            log(f"[control] request {r['id']} failed: {e}")
            try:
                complete_control_request(r['id'], 'FAILED', str(e))
            except Exception:
                pass


def _force_close_all():
    """Emergency: close all open futures positions.

    Sizes the closing order off the REAL broker position, not the sum of each
    DB row's (possibly stale) `contracts` field — ported from the IBKR side fix
    (Jul 23 2026 incident: looping per-trade with each row's own contracts
    caused a stop-close to over-buy and open a phantom position once the DB
    and broker disagreed on size). Sends exactly one order, sized to what the
    broker actually holds and verified afterward, then distributes P&L.
    """
    trades = get_open_futures_trades()
    if not trades:
        send_telegram("No open futures positions.")
        return

    for t in trades:
        _cancel_backup_stop(t)   # cancel backup stops before closing

    real_qty = _get_ibkr_qty()
    if real_qty is None:
        send_telegram("⚠️ FUT CLOSE: bridge unavailable — could not verify real position, no order sent. Check TC manually.")
        return

    db_total = sum(t.get('contracts', 1) for t in trades)
    if round(real_qty) != 0 and abs(round(real_qty)) != db_total:
        log(f"  NOTE: DB rows imply {db_total} contracts total, broker shows {real_qty} — closing off the real figure")

    if round(real_qty) == 0:
        for t in trades:
            log_futures_exit(t['id'], t['entry_price'],
                              'FUT CLOSE — broker already flat (orphan DB row)', 0.0, 0.0)
        send_telegram(f"🔴 FUTURES(TC): {len(trades)} DB row(s) marked closed — broker already flat, no order sent.")
        return

    close_side = 'BUY' if real_qty < 0 else 'SELL'
    close_qty  = abs(int(round(real_qty)))
    _bridge_post('/futures/order', {
        'symbol': SYMBOL, 'qty': close_qty,
        'side': close_side, 'order_type': 'MARKET',
    })
    time.sleep(3)
    post_qty = _get_ibkr_qty()
    if post_qty is None or round(post_qty) != 0:
        send_telegram(f"⚠️ FUT CLOSE: sent {close_side} {close_qty} but broker still shows qty={post_qty} — check TC manually, DB NOT updated.")
        return

    price = get_live_price() or trades[0]['entry_price']
    total_pnl = 0.0
    for t in trades:
        pnl_pts   = (t['entry_price'] - price) if t.get('side') == 'SHORT' else (price - t['entry_price'])
        pnl_ticks = pnl_pts / TICK_SIZE
        pnl_usd   = _net_usd(pnl_ticks * TICK_VALUE * t.get('contracts', 1),
                             t.get('contracts', 1))
        log_futures_exit(t['id'], price, 'FUT CLOSE command', pnl_usd, pnl_ticks)
        total_pnl += pnl_usd
    record_trade_pnl(total_pnl)
    send_telegram(f"🔴 FUTURES(TC): force-closed real position ({close_side} {close_qty}) covering {len(trades)} DB trade(s). Est P&L ${total_pnl:+.0f}.")


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

    prop_load()
    # Reconcile prop_state from DB on every startup — restarts mid-day cause drift.
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
        _state['balance']      = round(_state.get('balance', 50000) + _delta, 2)
        _changed = True
    if _changed:
        prop_save(_state)
    send_telegram(f"⚡ TriVega Futures · Online\n{format_prop_status()}")

    load_avg_volumes()   # build RVOL denominator for calc_contracts_dynamic (non-blocking; graceful if missing)
    _thesis_check.init_db()

    _scheduler = BackgroundScheduler(timezone=ET)

    # Core loops
    _scheduler.add_job(run_scan,     'interval', seconds=SCAN_INTERVAL,    id='scan')
    _scheduler.add_job(run_monitor,  'interval', seconds=MONITOR_INTERVAL, id='monitor')

    # London session (plug/unplug via LONDON_ENABLED above) — mirrors futures_trader.py
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
        send_telegram('🇬🇧 London session ENABLED (TC)')

    # Telegram command polling
    _scheduler.add_job(poll_telegram_commands, 'interval', seconds=10, id='telegram')

    # Daily routines
    _scheduler.add_job(morning_gateway_health_check, 'cron',
                       day_of_week='mon-fri', hour=8, minute=15,
                       timezone=ET, id='gateway_health')
    _scheduler.add_job(reset_daily_state, 'cron',
                       day_of_week='mon-fri', hour=9, minute=28,
                       timezone=ET, id='daily_reset')
    _scheduler.add_job(eod_snapshot, 'cron',
                       day_of_week='mon-fri', hour=16, minute=10,
                       timezone=ET, id='eod_snapshot')
    _scheduler.add_job(
        lambda: _thesis_check.weekly_review(send_telegram, log, ACCOUNT_MODE),
        'cron', day_of_week='fri', hour=16, minute=40, timezone=ET, id='thesis_check_review',
    )

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
