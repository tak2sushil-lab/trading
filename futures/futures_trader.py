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
  Session    : 6pm–5pm ET (23h), trade NY only (9:30am–3:10pm ET)
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
    update_eod_balance, get_status as prop_status, load_state as prop_load
)

# ── Telegram ──────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv('FUTURES_TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('FUTURES_TELEGRAM_CHAT_ID')

# ── Bridge ────────────────────────────────────────────────
BRIDGE = os.getenv('FUTURES_BRIDGE_URL', 'http://localhost:8000')  # IBKR bridge (bridge.py)

# ── MNQ constants ─────────────────────────────────────────
SYMBOL       = 'MNQ'
EXCHANGE     = 'CME'
TICK_SIZE    = 0.25          # minimum price increment
TICK_VALUE   = 0.50          # $ per tick per contract
POINT_VALUE  = 2.00          # $ per point per contract  (4 ticks × $0.50)

# ── Risk constants ────────────────────────────────────────
MAX_RISK_PER_TRADE   = 100.0   # $ max risk per trade (1 contract × 50-tick stop)
MAX_DAILY_LOSS       = 350.0   # prop_rules.py hard gates at this level
DAILY_PROFIT_TARGET  = 400.0   # stop new entries after hitting this
MIN_RR               = 2.0     # minimum reward:risk ratio
MAX_OPEN_TRADES      = 2       # max simultaneous MNQ positions

# ── Profit protection (point-based — MNQ-calibrated) ─────
# PCT-based thresholds (e.g. 1.5%) translate to 450pts on MNQ ≈ never fires.
# Use absolute points instead. Typical trade: entry ~30,000, target ~99pts.
BE_ACTIVATE_PTS  = 30.0   # +30pts → move stop to entry (scratch worst case)
TRAIL_WIDE_PTS   = 60.0   # +60pts → trail 20pts behind session peak
TRAIL_TIGHT_PTS  = 85.0   # +85pts → tighten trail to 10pts (near 99pt target)
TRAIL_WIDE_GAP   = 20.0   # trail distance in wide mode
TRAIL_TIGHT_GAP  = 10.0   # trail distance in tight mode

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
AFTERNOON_END   = (15, 0)
EOD_START       = (15, 0)   # close only, no new entries
HARD_CLOSE      = (15, 15)  # force close all positions

SCAN_INTERVAL   = 60        # seconds between scans (1 min, faster than equity)
MONITOR_INTERVAL = 15       # seconds between position checks

# ── Global state ──────────────────────────────────────────
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
_daily_pnl            = 0.0
_peak_daily_pnl       = 0.0
_trading_paused       = False
_scheduler            = None
_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_DIR, '..', 'trades.db')


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
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'},
            timeout=5,
        )
    except Exception:
        pass


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
    q = _bridge_get(f'/futures/quote/{SYMBOL}')
    return q.get('best_price') or q.get('last') or q.get('close')


def get_bars(bar_size_min: int = 5, days: int = 2) -> pd.DataFrame:
    """
    Fetch historical bars for MNQ from the IBKR bridge.
    Bridge endpoint: GET /history/futures/MNQ?duration=2+D&bar_size=5+mins&rth=false
    Response: {'symbol': 'MNQ', 'bars': [{ts, open, high, low, close, volume}, ...]}
    """
    bar_str  = f'{bar_size_min}+mins'
    dur_str  = f'{days}+D'
    path     = f'/history/futures/{SYMBOL}?duration={dur_str}&bar_size={bar_str}&rth=false'
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

def get_regime() -> str:
    """
    Determine market regime from NQ/MNQ bars directly.
    No SPY proxy needed — NQ IS the market for this instrument.
    Returns: STRONG | NORMAL | WEAK
    """
    global _last_regime
    try:
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

    if side == 'LONG':
        # ORB break
        if sig.get('orb_bull'):         score += 20
        # VWAP reclaim
        if sig.get('vwap_reclaim'):     score += 15
        # Momentum
        if sig.get('momentum_bull'):    score += 10
        # Session open play
        if sig.get('open_play_bull'):   score += 10
        # RSI gate: skip if overbought
        if rsi > 80:
            return 0, 'SKIP'
        if rsi > 70:                    score -= 10
        if rsi < 45:                    score += 5   # still room to run

    else:  # SHORT
        if sig.get('orb_bear'):         score += 20
        if sig.get('vwap_rejection'):   score += 15
        if sig.get('momentum_bear'):    score += 10
        if sig.get('open_play_bear'):   score += 10
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
    Uses ATR-based stops rounded to tick size.
    """
    tick = TICK_SIZE
    stop_atr_mult   = 1.5
    target_atr_mult = 3.0   # 2:1 R:R minimum

    raw_stop   = atr * stop_atr_mult
    raw_target = atr * target_atr_mult

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


# ── Database helpers ──────────────────────────────────────

def get_open_futures_trades() -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM futures_trades WHERE status='OPEN'"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def log_futures_entry(symbol, contract, entry_price, contracts,
                      target, sl, setup_type, session, order_id, side='LONG') -> int:
    conn = sqlite3.connect(DB_PATH)
    now  = datetime.now(ET)
    cur  = conn.execute('''
        INSERT INTO futures_trades
        (symbol, contract, entry_date, entry_time, entry_price,
         contracts, side, target_price, stop_price,
         status, setup_type, session, order_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (symbol, contract, str(now.date()), now.strftime('%H:%M:%S'),
          entry_price, contracts, side, target, sl,
          'OPEN', setup_type, session, order_id))
    tid = cur.lastrowid
    conn.commit()
    conn.close()
    return tid


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


def get_futures_daily_pnl() -> float:
    today = str(date.today())
    conn  = sqlite3.connect(DB_PATH)
    row   = conn.execute(
        "SELECT SUM(pnl) FROM futures_trades WHERE exit_date=? AND status='CLOSED'",
        (today,)
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
    global _daily_pnl, _trading_paused

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

    # 5. Daily P&L gates
    daily_pnl = get_futures_daily_pnl()
    if daily_pnl <= -MAX_DAILY_LOSS:
        log(f"  BLOCKED: daily loss ${daily_pnl:.0f}")
        return False
    if daily_pnl >= DAILY_PROFIT_TARGET:
        log(f"  SKIP: daily target ${DAILY_PROFIT_TARGET:.0f} already hit (${daily_pnl:.0f})")
        return False

    # 6. Bridge connected
    if not get_bridge_connected():
        log("  BLOCKED: futures bridge not connected")
        return False

    # ── Sizing ────────────────────────────────────────────
    price = get_live_price()
    if not price:
        log("  BLOCKED: no live price")
        return False

    df5        = get_bars()
    atr        = calc_atr(df5) if not df5.empty else 10.0
    sl, target = calc_sl_target(price, atr, side)
    contracts  = calc_contracts(price, sl)

    rr = abs(target - price) / abs(price - sl) if abs(price - sl) > 0 else 0
    if rr < MIN_RR:
        log(f"  SKIP: R:R {rr:.1f} < min {MIN_RR}")
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
    setup    = f"ORB_{side}" if sig.get(f'orb_{"bull" if side=="LONG" else "bear"}') else f"VWAP_{side}"

    tid = log_futures_entry(
        symbol=SYMBOL, contract=result.get('contract_month', SYMBOL),
        entry_price=price, contracts=contracts,
        target=target, sl=sl, setup_type=setup,
        session=session, order_id=order_id, side=side,
    )

    risk_usd   = abs(price - sl) / TICK_SIZE * TICK_VALUE * contracts
    target_usd = abs(target - price) / TICK_SIZE * TICK_VALUE * contracts

    msg = (
        f"🔵 FUTURES {side} ENTRY\n"
        f"Symbol:    {SYMBOL} ({result.get('contract','')})\n"
        f"Price:     {price}\n"
        f"Stop:      {sl}  (-${risk_usd:.0f})\n"
        f"Target:    {target}  (+${target_usd:.0f})\n"
        f"Contracts: {contracts} × MNQ\n"
        f"Setup:     {setup} | Grade: {grade} ({score}pts)\n"
        f"Session:   {session} | Regime: {regime}\n"
        f"R:R:       {rr:.1f}"
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
    global _daily_pnl

    trades = get_open_futures_trades()
    if not trades:
        return

    exits = []
    now   = datetime.now(ET)

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

        # ── Point-based profit protection ─────────────────
        # MNQ target ~99pts. PCT-based thresholds (1.5% = 450pts) never fire.
        # Three tiers, each only tightens — never loosens the stop.
        pnl_pts = (entry - price) if is_short else (price - entry)
        s_peak  = _session_low.get(tid, price) if is_short else _session_high.get(tid, price)

        # Tier 1 (+30pts): break-even — stop moves to entry, trade cannot lose
        if pnl_pts >= BE_ACTIVATE_PTS:
            be = round(entry + TICK_SIZE, 2) if not is_short else round(entry - TICK_SIZE, 2)
            if (not is_short and be > sl) or (is_short and be < sl):
                sl = be
                update_futures_stop(tid, sl)
                log(f"  {SYMBOL}{'SHORT' if is_short else ''}: BE stop → {sl} (+{pnl_pts:.0f}pts)")

        # Tier 2 (+60pts): trail 20pts behind session peak
        if pnl_pts >= TRAIL_WIDE_PTS:
            trail = round(s_peak - TRAIL_WIDE_GAP, 2) if not is_short else round(s_peak + TRAIL_WIDE_GAP, 2)
            if (not is_short and trail > sl) or (is_short and trail < sl):
                sl = trail
                update_futures_stop(tid, sl)
                log(f"  {SYMBOL}{'SHORT' if is_short else ''}: trail(20) → {sl} (+{pnl_pts:.0f}pts)")

        # Tier 3 (+85pts, near target): tighten to 10pts — lock in most of the gain
        if pnl_pts >= TRAIL_TIGHT_PTS:
            trail = round(s_peak - TRAIL_TIGHT_GAP, 2) if not is_short else round(s_peak + TRAIL_TIGHT_GAP, 2)
            if (not is_short and trail > sl) or (is_short and trail < sl):
                sl = trail
                update_futures_stop(tid, sl)
                log(f"  {SYMBOL}{'SHORT' if is_short else ''}: trail(10) → {sl} (+{pnl_pts:.0f}pts)")

        # VWAP for exit decisions
        vwap = calc_vwap(df5) if not df5.empty else None

        # ── Exit decisions ────────────────────────────────
        exit_reason = None

        # 1. Hard stop
        if is_short:
            if price >= sl:
                exit_reason = f'Short stop {sl} hit ({pnl_pct:+.1f}% / ${pnl_usd:+.0f})'
        else:
            if price <= sl:
                exit_reason = f'Stop {sl} hit ({pnl_pct:+.1f}% / ${pnl_usd:+.0f})'

        # 2. Circuit breaker — prop rule safety
        daily_pnl = get_futures_daily_pnl()
        if not exit_reason and daily_pnl <= -MAX_DAILY_LOSS:
            exit_reason = f'Daily loss circuit breaker: ${daily_pnl:.0f}'

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

        # 5. EOD close (hard close at 3:15pm ET)
        h, m = now.hour, now.minute
        if not exit_reason and (h > HARD_CLOSE[0] or (h == HARD_CLOSE[0] and m >= HARD_CLOSE[1])):
            exit_reason = f'EOD hard close (3:15pm ET)'

        # ── Execute exit ──────────────────────────────────
        if exit_reason:
            cover_side = 'BUY' if is_short else 'SELL'
            result = _bridge_post('/futures/order', {
                'symbol':     SYMBOL,
                'qty':        contracts,
                'side':       cover_side,
                'order_type': 'MARKET',
            })

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


# ── Main scan loop ────────────────────────────────────────

def run_scan():
    """5-min scan: check regime, signals, enter if qualified."""
    global _confirmed_scans, _regime_scan_counts

    if not get_bridge_connected():
        log("Bridge disconnected — skipping scan")
        return

    if _trading_paused:
        log("Trading paused — scan skipped")
        return

    session = get_session()
    log(f"--- SCAN | session={session} | {datetime.now(ET).strftime('%H:%M')} ---")

    # Update ORB
    df5 = get_bars(bar_size_min=5, days=2)
    if not df5.empty:
        update_orb(df5)

    # Regime
    regime = get_regime()
    _regime_scan_counts[regime] = _regime_scan_counts.get(regime, 0) + 1
    _confirmed_scans = _regime_scan_counts.get(regime, 0)
    log(f"Regime: {regime} (×{_confirmed_scans}) | Daily P&L: ${get_futures_daily_pnl():+.0f}")

    if not is_entry_allowed():
        log(f"Session {session} — no new entries")
        return

    # Get signals
    sig = get_signals(df5)
    if not sig:
        log("No signals generated")
        return

    price = sig.get('price', 0)
    vwap  = sig.get('vwap', 0)
    log(f"Price: {price} | VWAP: {vwap} | RSI: {sig.get('rsi',0):.0f} | "
        f"ORB: {'set' if _orb_set else 'pending'}")

    # Open trades count
    open_trades = get_open_futures_trades()
    if len(open_trades) >= MAX_OPEN_TRADES:
        log(f"Max trades open ({len(open_trades)}) — skip")
        return

    # ── Try LONG entry ─────────────────────────────────────
    if regime in ('STRONG', 'NORMAL'):
        score, grade = grade_entry(sig, regime, 'LONG')
        if grade in ('A+', 'A'):
            log(f"LONG signal: {grade} ({score}pts) — entering")
            place_trade('LONG', sig, regime, score, grade)

    # ── Try SHORT entry (WEAK regime only) ────────────────
    if regime == 'WEAK' and _confirmed_scans >= 3:
        score, grade = grade_entry(sig, regime, 'SHORT')
        if grade in ('A+', 'A'):
            log(f"SHORT signal: {grade} ({score}pts) — entering")
            place_trade('SHORT', sig, regime, score, grade)


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
    global _orb_high, _orb_low, _orb_set, _confirmed_scans
    global _regime_scan_counts, _session_high, _session_low
    global _price_history, _partial_done, _peak_daily_pnl, _daily_pnl

    _orb_high = _orb_low = None
    _orb_set  = False
    _confirmed_scans  = 0
    _regime_scan_counts = {'STRONG': 0, 'NORMAL': 0, 'WEAK': 0}
    _session_high = {}
    _session_low  = {}
    _price_history = {}
    _partial_done  = {}
    _daily_pnl     = 0.0
    _peak_daily_pnl = 0.0

    prop_load()   # refresh prop rules state for new day
    log("Daily state reset")
    send_telegram(
        f"🌅 FUTURES day started\n"
        f"{format_prop_status()}"
    )


def eod_snapshot():
    """Called at EOD — update trailing drawdown high water mark."""
    health = _bridge_get('/account')
    bal    = health.get('NetLiquidation') or health.get('TotalCashValue')
    if bal:
        update_eod_balance(float(bal))
        log(f"EOD balance: ${bal:,.2f}")

    daily = get_futures_daily_pnl()
    send_telegram(
        f"🌙 FUTURES EOD\n"
        f"Day P&L: ${daily:+.2f}\n"
        f"{format_prop_status()}"
    )


# ── Telegram commands ─────────────────────────────────────

def poll_telegram_commands():
    """Poll for Telegram commands — PAUSE, RESUME, STATUS, CLOSE."""
    global _trading_paused
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={'timeout': 1, 'allowed_updates': ['message']},
            timeout=5,
        )
        updates = r.json().get('result', [])
        for u in updates:
            msg = u.get('message', {}).get('text', '').strip().upper()
            if 'FUT PAUSE' in msg:
                _trading_paused = True
                send_telegram("⏸ FUTURES trading paused.")
            elif 'FUT RESUME' in msg:
                _trading_paused = False
                send_telegram("▶️ FUTURES trading resumed.")
            elif 'FUT STATUS' in msg:
                send_telegram(format_prop_status())
            elif 'FUT CLOSE' in msg:
                _force_close_all()
    except Exception:
        pass


def _force_close_all():
    """Emergency: close all open futures positions."""
    trades = get_open_futures_trades()
    if not trades:
        send_telegram("No open futures positions.")
        return
    for t in trades:
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
    log("=" * 50)
    log("FUTURES TRADER starting")
    log(f"Symbol: {SYMBOL} | Bridge: {BRIDGE}")
    log("=" * 50)

    # Verify bridge
    if not get_bridge_connected():
        log("WARNING: futures bridge not connected — waiting for connection")
        send_telegram("⚠️ FUTURES: Bridge not connected at startup. Will retry on each scan.")

    prop_load()
    send_telegram(f"⚡ TriVega Futures · Online\n{format_prop_status()}")

    _scheduler = BackgroundScheduler(timezone=ET)

    # Core loops
    _scheduler.add_job(run_scan,     'interval', seconds=SCAN_INTERVAL,    id='scan')
    _scheduler.add_job(run_monitor,  'interval', seconds=MONITOR_INTERVAL, id='monitor')

    # Telegram command polling
    _scheduler.add_job(poll_telegram_commands, 'interval', seconds=10, id='telegram')

    # Daily routines
    _scheduler.add_job(reset_daily_state, 'cron',
                       day_of_week='mon-fri', hour=9, minute=28,
                       timezone=ET, id='daily_reset')
    _scheduler.add_job(eod_snapshot, 'cron',
                       day_of_week='mon-fri', hour=15, minute=20,
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
