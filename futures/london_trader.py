"""
futures/london_trader.py — London session MNQ live trader (3am–9am ET)

Mirrors london_sim.py champion config exactly. Designed to be plugged into
futures_trader.py via LONDON_ENABLED=True (same process, same scheduler).
Also runnable standalone for testing.

Plug in:  set LONDON_ENABLED=True  in futures_trader.py → restart service
Unplug:   set LONDON_ENABLED=False in futures_trader.py → restart service


Champion config (locked Jun 15 2026):
  Stop:   2.0×ATR   |  Target: 6.0×ATR (pure trail, almost never fires)
  BE:     0.10×ATR  |  Max trades: 2/day
  Signal: IB range break only (Signal A — no VWAP/bias scoring required)

Account model ($5k, 25% DLL):
  DLL: $1,250   |  Risk/trade: $250   |  Max contracts: 2

Backtest (2025-2026 IS): 467t | 42.4% WR | $10,607 | MaxDD $321

Run:
  venv/bin/python futures/london_trader.py
  launchd: com.sushil.trading.london
"""

import os
import sys
import sqlite3
import time
import logging
import threading
import requests
from datetime import datetime, date, timedelta, time as dt_time
from dotenv import load_dotenv

import pandas as pd
import pytz
from apscheduler.schedulers.background import BackgroundScheduler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
from futures.gate_audit import log_block, log_enter

# ── Instrument constants (MNQ) ────────────────────────────────────────────────

SYMBOL      = 'MNQ'
POINT_VALUE = 2.00    # $2/point
TICK_SIZE   = 0.25    # 0.25 index points
TICK_VALUE  = 0.50    # $0.50/tick
COMMISSION  = 1.24    # round-turn commission per contract

# ── Session times (all ET) ────────────────────────────────────────────────────

ET = pytz.timezone('America/New_York')

LONDON_IB_START  = (3,  0)   # IB formation begins (London equity open 8am GMT)
LONDON_IB_END    = (4,  0)   # IB formation ends / entry gate opens
LONDON_EOD       = (9,  0)   # hard close (30 min before NY RTH)
SESSION_SETUP    = (2, 45)   # startup: load state, wait for IB bars to start

# No entries in the last pre-NY hour (empirically weak, N=21 trades -$7/trade in 2025-26)
LONDON_ENTRY_CUTOFF = (8, 0)

SCAN_INTERVAL    = 60        # seconds between scans during London session
MONITOR_INTERVAL = 15        # seconds between position monitor checks

# ── Strategy champion params (DATA locked Jun 15 2026) ────────────────────────

STOP_ATR_MULT    = 2.0       # DATA: wider stop → higher WR, less fakeout noise
TARGET_ATR_MULT  = 6.0       # DATA: pure trail target — almost never fires
BE_ATR_MULT      = 0.10      # DATA: protect entry at +5pts → fakeouts exit flat
TRAIL_WIDE_ATR   = 1.00      # CALIBRATE: not yet tuned
TRAIL_WIDE_GAP   = 0.30      # CALIBRATE: gap for wide trail
TRAIL_TIGHT_ATR  = 1.50      # CALIBRATE: not yet tuned
TRAIL_TIGHT_GAP  = 0.20      # CALIBRATE: gap for tight trail

# ── Risk params ($5k account model, 25% DLL) ─────────────────────────────────

MAX_DAILY_LOSS     = 1250.0  # 25% of $5k account allocation
MAX_RISK_PER_TRADE = 250.0   # 5% of $5k account per trade
MAX_CONTRACTS      = 2
MAX_DAILY_TRADES   = 2
MIN_RR             = 2.0
MAX_STOP_PTS       = 150.0   # skip entry if ATR×2 > 150pts

# ── IB quality gates ──────────────────────────────────────────────────────────

LONDON_MIN_IB_RANGE = 20.0   # thin IB → skip day
LONDON_MIN_IB_BARS  = 6      # need at least 6 bars in the 3:00–4:00 window

# ── Overnight bias thresholds ─────────────────────────────────────────────────

OVN_COMPRESS   = 50.0        # overnight range < 50pts → BOTH directions allowed
OVN_TREND_HI   = 0.85        # pos ≥ 0.85 → overnight LONG bias
OVN_TREND_LO   = 0.20        # pos ≤ 0.20 → overnight SHORT bias
OVN_SKIP_LO    = 0.20        # (0.20, 0.40) → ambiguous zone → skip day
OVN_SKIP_HI    = 0.40

# No-move exit (fires when trade stuck in dead zone for too long)
NO_MOVE_MINUTES = 60         # London session is 5hrs — 60-min no-move limit
NO_MOVE_MAX_PTS = 20.0       # above this → trade IS moving, let it run
NO_MOVE_MIN_PTS = -10.0      # below this → stop will manage it

# ── Bridge + DB ───────────────────────────────────────────────────────────────

BRIDGE  = os.getenv('FUTURES_BRIDGE_URL', 'http://localhost:8000')
DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'trades.db')

TELEGRAM_TOKEN   = os.getenv('FUTURES_TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('FUTURES_TELEGRAM_CHAT_ID')

# ── Logging — named logger so we don't pollute futures_trader's root logger ───

_logger = logging.getLogger('london')
if not _logger.handlers:
    _logger.setLevel(logging.INFO)
    _fmt = logging.Formatter('%(asctime)s [LON] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    _sh  = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_fmt)
    _logger.addHandler(_sh)
    _fh  = logging.FileHandler(
        os.path.join(os.path.dirname(__file__), '..', 'logs', 'london_trader.log'),
        encoding='utf-8',
    )
    _fh.setFormatter(_fmt)
    _logger.addHandler(_fh)
    _logger.propagate = False   # don't bubble up to root logger

def log(msg: str):
    _logger.info(msg)


# ── Global state ──────────────────────────────────────────────────────────────

_session_date:   date | None     = None
_ib_high:        float            = 0.0
_ib_low:         float            = 0.0
_ib_formed:      bool             = False
_ib_close_pos:   float            = 0.5
_ovn_bias:       str              = 'BOTH'
_ovn_skip:       bool             = False
_atr:            float            = 10.0

_position:       dict | None      = None
_trade_count:    int              = 0
_daily_pnl:      float            = 0.0
_last_exit_time: datetime | None  = None
_ovn_pos:        float            = -1.0   # overnight close position (set by run_scan)

# monitor_position() is called from two independent scheduler jobs (the per-minute
# london_scan cron job and the 15s london_monitor interval job). Without this lock,
# both can see the same open _position concurrently and both submit a real closing
# order — the second one fires on an already-flat account and flips it into a fresh
# unintended position. Non-blocking: if monitor is already running, skip and let the
# next tick (≤15s later) retry — never worth blocking one job on the other.
_monitor_lock = threading.Lock()

_cached_df:  pd.DataFrame         = pd.DataFrame()

_active_contract_month: str       = ''   # resolved at session reset; passed to get_bars
_stale_block_count:     int       = 0    # consecutive stale-price blocks this session
_ib_pending_sync:       bool      = False  # IB formed but live quote stale — waiting to align


# ── Helpers: tick rounding ────────────────────────────────────────────────────

def _tick(v: float) -> float:
    return round(round(v / TICK_SIZE) * TICK_SIZE, 2)


# ── Bridge helpers ────────────────────────────────────────────────────────────

def _bridge_get(path: str, timeout: int = 10) -> dict:
    try:
        r = requests.get(f'{BRIDGE}{path}', timeout=timeout)
        return r.json() if r.ok else {}
    except Exception as e:
        log(f'Bridge GET error {path}: {e}')
        return {}


def _bridge_post(path: str, payload: dict, timeout: int = 10) -> dict:
    try:
        r = requests.post(f'{BRIDGE}{path}', json=payload, timeout=timeout)
        return r.json() if r.ok else {}
    except Exception as e:
        log(f'Bridge POST error {path}: {e}')
        return {}


def get_bridge_connected() -> bool:
    return _bridge_get('/').get('connected', False)


# ── Data / bar functions ──────────────────────────────────────────────────────

def get_active_contract_month() -> str:
    """Return the contract month string the bridge is subscribed to (e.g. '20260918')."""
    resp = _bridge_get(f'/futures/quote/{SYMBOL}')
    return str(resp.get('contract_month', '') or '').strip()


def get_bars(days: int = 2, contract_month: str = '') -> pd.DataFrame:
    """Fetch MNQ 5-min bars from IBKR bridge (rth=false → includes London window).
    Pass contract_month to ensure bars and live quote use the same contract."""
    cm   = f'&contract_month={contract_month}' if contract_month else ''
    path = f'/history/futures/{SYMBOL}?duration={days}+D&bar_size=5+mins&rth=false{cm}'
    resp = _bridge_get(path, timeout=20)
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


def get_live_price() -> float | None:
    """Get current MNQ mid price from bridge."""
    resp = _bridge_get(f'/futures/quote/{SYMBOL}')
    if not resp:
        return None
    bid = resp.get('bid', 0) or 0
    ask = resp.get('ask', 0) or 0
    if bid > 0 and ask > 0:
        return round((float(bid) + float(ask)) / 2, 2)
    best = resp.get('best_price') or resp.get('last')
    return float(best) if best else None


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """14-period ATR from historical bars."""
    if len(df) < 2:
        return 10.0
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low']  - df['close'].shift()).abs(),
    ], axis=1).max(axis=1)
    v = tr.rolling(period, min_periods=2).mean().iloc[-1]
    return float(v) if pd.notna(v) and v > 0 else 10.0


# ── Overnight bias ────────────────────────────────────────────────────────────

def compute_overnight_bias(df: pd.DataFrame, trade_date: date) -> tuple[str, bool, float]:
    """
    Compute overnight directional bias for London session.
    Range: prev calendar day 7pm ET → today 3am ET.
    Returns (bias, skip_day, ovn_pos).
    """
    prev_cal = trade_date - timedelta(days=1)
    night_mask = (
        ((df.index.date == prev_cal) & (df.index.time >= dt_time(19, 0))) |
        ((df.index.date == trade_date) & (df.index.time < dt_time(3, 0)))
    )
    night_bars = df[night_mask]

    if len(night_bars) < 4:
        return 'BOTH', False, -1.0

    ovn_high  = float(night_bars['high'].max())
    ovn_low   = float(night_bars['low'].min())
    ovn_range = ovn_high - ovn_low

    if ovn_range < OVN_COMPRESS:
        return 'BOTH', False, -1.0

    lon_open_bars = df[
        (df.index.date == trade_date) &
        (df.index.time >= dt_time(3, 0)) &
        (df.index.time <  dt_time(3, 10))
    ]
    if lon_open_bars.empty:
        return 'BOTH', False, -1.0

    lon_open = float(lon_open_bars['open'].iloc[0])
    pos = max(0.0, min(1.0, (lon_open - ovn_low) / ovn_range))

    if pos >= OVN_TREND_HI:
        return 'LONG', False, round(pos, 3)
    if pos <= OVN_TREND_LO:
        return 'SHORT', False, round(pos, 3)
    if OVN_SKIP_LO < pos < OVN_SKIP_HI:
        return 'BOTH', True, round(pos, 3)
    return 'BOTH', False, round(pos, 3)


# ── IB formation ──────────────────────────────────────────────────────────────

def update_london_ib(df: pd.DataFrame, trade_date: date) -> bool:
    """
    Compute London IB from 3am–4am ET bars. Sets global _ib_high/_ib_low.
    Call once after 4am when IB window is complete.
    Returns True if IB is valid and formed.
    """
    global _ib_high, _ib_low, _ib_formed, _ib_close_pos, _ib_pending_sync

    ib_mask = (
        (df.index.date == trade_date) &
        (df.index.time >= dt_time(3, 0)) &
        (df.index.time <  dt_time(4, 0))
    )
    ib_bars = df[ib_mask]

    if len(ib_bars) < LONDON_MIN_IB_BARS:
        log(f'IB thin: only {len(ib_bars)} bars (need {LONDON_MIN_IB_BARS})')
        return False

    ib_hi = float(ib_bars['high'].max())
    ib_lo = float(ib_bars['low'].min())
    ib_rng = ib_hi - ib_lo

    if ib_rng < LONDON_MIN_IB_RANGE:
        log(f'IB too thin: {ib_rng:.1f}pts (min {LONDON_MIN_IB_RANGE}pts) — skip day')
        return False

    ib_cp = (float(ib_bars['close'].iloc[-1]) - ib_lo) / ib_rng if ib_rng > 0 else 0.5

    _ib_high      = ib_hi
    _ib_low       = ib_lo
    _ib_close_pos = ib_cp
    _ib_formed    = True

    # Coherence check: on quarterly rollover day IBKR streaming for the new
    # front-month contract may not be active yet, causing a stale live quote.
    # The IB range itself (from historical bars) is always correct. Rather than
    # skip the day, raise _ib_pending_sync so run_scan() holds off on entries
    # until live price aligns with the most-recent bar close (within 50pts).
    live_chk = get_live_price()
    ib_mid   = (ib_hi + ib_lo) / 2
    if live_chk and abs(live_chk - ib_mid) > 75.0:
        _ib_pending_sync = True
        msg = (f'⚠️ LONDON IB — live price out of sync\n'
               f'IB H={ib_hi:.0f}  L={ib_lo:.0f}  mid={ib_mid:.0f}\n'
               f'live={live_chk:.0f}  gap={abs(live_chk - ib_mid):.0f}pts\n'
               f'IB valid — waiting for live quote to align before trading.')
        log(msg)
        send_telegram(msg)
    else:
        _ib_pending_sync = False

    log(f'IB formed: H={ib_hi:.2f}  L={ib_lo:.2f}  Range={ib_rng:.1f}pts  '
        f'ClosePos={ib_cp:.2f}  Bias={_ovn_bias}  OvnPos={_ovn_pos:.2f}')
    if not _ib_pending_sync:
        send_telegram(
            f'🌅 LONDON IB FORMED\n'
            f'H={ib_hi:.2f}  L={ib_lo:.2f}  Range={ib_rng:.1f}pts\n'
            f'Close@{ib_cp:.0%}  Overnight bias: {_ovn_bias}\n'
            f'Watching for IB breaks...'
        )
    return True


# ── Position sizing ───────────────────────────────────────────────────────────

def calc_contracts(price: float, sl: float) -> int:
    stop_pts = abs(price - sl)
    if stop_pts <= 0:
        return 1
    risk_per_c = (stop_pts / TICK_SIZE) * TICK_VALUE
    return max(1, min(int(MAX_RISK_PER_TRADE / risk_per_c), MAX_CONTRACTS))


def calc_sl_target(price: float, atr: float, side: str) -> tuple[float, float]:
    if side == 'LONG':
        sl     = _tick(price - atr * STOP_ATR_MULT)
        target = _tick(price + atr * TARGET_ATR_MULT)
    else:
        sl     = _tick(price + atr * STOP_ATR_MULT)
        target = _tick(price - atr * TARGET_ATR_MULT)
    return sl, target


# ── Database ──────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS london_trades (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_date    TEXT,
            entry_time    TEXT,
            exit_date     TEXT,
            exit_time     TEXT,
            side          TEXT,
            setup         TEXT,
            entry         REAL,
            exit_price    REAL,
            sl_init       REAL,
            sl_current    REAL,
            target        REAL,
            contracts     INTEGER,
            pnl           REAL,
            exit_reason   TEXT,
            ib_range      REAL,
            ib_close_pos  REAL,
            atr           REAL,
            ovn_pos       REAL,
            status        TEXT DEFAULT 'OPEN',
            stop_order_id TEXT
        )
    ''')
    conn.commit()
    conn.close()


def _log_entry(side: str, entry: float, sl: float, target: float,
               contracts: int, atr: float) -> int:
    now_et  = datetime.now(ET)
    ib_range = _ib_high - _ib_low
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute('''
        INSERT INTO london_trades
          (entry_date, entry_time, side, setup, entry, sl_init, sl_current,
           target, contracts, ib_range, ib_close_pos, atr, ovn_pos, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN')
    ''', (
        str(now_et.date()), now_et.strftime('%H:%M'),
        side, f'LON_{side[0]}_IB',
        entry, sl, sl, target, contracts,
        round(ib_range, 2), round(_ib_close_pos, 3),
        round(atr, 2), round(_ovn_pos, 3),
    ))
    trade_id = cur.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def _log_exit(trade_id: int, exit_price: float, reason: str, pnl: float):
    now_et = datetime.now(ET)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        UPDATE london_trades
           SET exit_date=?, exit_time=?, exit_price=?, pnl=?, exit_reason=?, status='CLOSED'
         WHERE id=?
    ''', (str(now_et.date()), now_et.strftime('%H:%M'), exit_price, round(pnl, 2), reason, trade_id))
    conn.commit()
    conn.close()


def _update_db_stop(trade_id: int, new_sl: float, stop_order_id: str = ''):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        'UPDATE london_trades SET sl_current=?, stop_order_id=? WHERE id=?',
        (new_sl, stop_order_id, trade_id)
    )
    conn.commit()
    conn.close()


def get_london_daily_pnl() -> float:
    today = str(datetime.now(ET).date())
    conn  = sqlite3.connect(DB_PATH)
    row   = conn.execute(
        "SELECT SUM(pnl) FROM london_trades WHERE exit_date=? AND status='CLOSED'",
        (today,)
    ).fetchone()
    conn.close()
    return round(float(row[0] or 0), 2)


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg},
            timeout=5,
        )
    except Exception:
        pass


# ── Backup stop helpers ───────────────────────────────────────────────────────

def _cancel_backup_stop(trade: dict):
    sid = trade.get('stop_order_id')
    if sid:
        _bridge_post(f'/futures/cancel/{sid}', {})
        trade['stop_order_id'] = None


def _update_backup_stop(trade: dict, new_sl: float):
    """Cancel old IBKR backup stop, place a new one at new_sl."""
    is_short  = trade['side'] == 'SHORT'
    contracts = trade['contracts']

    _cancel_backup_stop(trade)

    stop_side = 'BUY' if is_short else 'SELL'
    result = _bridge_post('/futures/order', {
        'symbol':     SYMBOL,
        'qty':        contracts,
        'side':       stop_side,
        'order_type': 'STOP_MARKET',
        'stop_price': new_sl,
    })
    new_oid = (result or {}).get('order_id', '') or ''
    if not new_oid:
        alert = f'⚠️ LONDON BACKUP STOP FAILED (trade {trade["id"]}) — manual stop at {new_sl} needed!'
        log(alert)
        send_telegram(alert)
        return
    trade['stop_order_id'] = new_oid
    _update_db_stop(trade['id'], new_sl, new_oid)
    log(f'  Backup stop updated → {new_sl} (order {new_oid})')


# ── Entry execution ───────────────────────────────────────────────────────────

def place_london_trade(side: str, signal_price: float) -> bool:
    """Submit a London IB break trade. Returns True if submitted."""
    global _position, _trade_count, _daily_pnl

    if _position is not None:
        log('  BLOCKED: already in a position')
        return False
    if _trade_count >= MAX_DAILY_TRADES:
        log(f'  BLOCKED: max daily trades ({MAX_DAILY_TRADES}) reached')
        return False

    daily_pnl = get_london_daily_pnl()
    if daily_pnl <= -MAX_DAILY_LOSS:
        log(f'  BLOCKED: DLL hit (${daily_pnl:.0f})')
        return False

    if not get_bridge_connected():
        log('  BLOCKED: bridge not connected')
        return False

    # Live price check
    live_price = get_live_price()
    if not live_price:
        log('  BLOCKED: no live price')
        return False

    # Stale quote guard (same 50pt divergence as NY system)
    if abs(live_price - signal_price) > 50.0:
        global _stale_block_count
        _stale_block_count += 1
        log(f'  BLOCKED: stale price — signal={signal_price:.2f} live={live_price:.2f} '
            f'(gap={abs(live_price-signal_price):.1f}pts) [{_stale_block_count} consecutive]')
        if _stale_block_count == 5:
            send_telegram(
                f'⚠️ LONDON: {_stale_block_count} consecutive stale-price blocks\n'
                f'gap={abs(live_price-signal_price):.0f}pts — possible contract mismatch\n'
                f'contract={_active_contract_month or "unknown"}'
            )
        return False
    _stale_block_count = 0

    sl, target = calc_sl_target(live_price, _atr, side)
    stop_pts   = abs(live_price - sl)

    if stop_pts > MAX_STOP_PTS:
        log(f'  SKIP: stop {stop_pts:.0f}pts > max {MAX_STOP_PTS}pts (high ATR day)')
        return False

    rr = abs(target - live_price) / stop_pts if stop_pts > 0 else 0
    if rr < MIN_RR - 0.01:
        log(f'  SKIP: R:R {rr:.2f} < min {MIN_RR}')
        return False

    contracts = calc_contracts(live_price, sl)

    # Submit entry
    order_side = 'BUY' if side == 'LONG' else 'SELL'
    result = _bridge_post('/futures/order', {
        'symbol':     SYMBOL,
        'qty':        contracts,
        'side':       order_side,
        'order_type': 'MARKET',
    })

    if result.get('status') != 'submitted':
        log(f'  Entry order failed: {result}')
        return False

    order_id = result.get('order_id', '')

    # Backup stop order on IBKR side (hardware protection)
    stop_side = 'SELL' if side == 'LONG' else 'BUY'
    stop_result = _bridge_post('/futures/order', {
        'symbol':     SYMBOL,
        'qty':        contracts,
        'side':       stop_side,
        'order_type': 'STOP_MARKET',
        'stop_price': sl,
    })
    stop_order_id = stop_result.get('order_id', '') or ''

    # DB + state
    trade_id = _log_entry(side, live_price, sl, target, contracts, _atr)
    _position = {
        'id':            trade_id,
        'side':          side,
        'entry':         live_price,
        'sl':            sl,
        'sl_init':       sl,
        'target':        target,
        'contracts':     contracts,
        'atr':           _atr,
        'entry_time':    datetime.now(ET),
        'peak':          live_price,
        'order_id':      order_id,
        'stop_order_id': stop_order_id or None,
        'be_done':       False,
    }
    if stop_order_id:
        _update_db_stop(trade_id, sl, stop_order_id)

    _trade_count += 1

    ib_range = _ib_high - _ib_low
    msg = (
        f'🇬🇧 LONDON ENTRY\n'
        f'{SYMBOL} {side} ×{contracts}  @{live_price:.2f}\n'
        f'SL: {sl:.2f}  Target: {target:.2f}\n'
        f'ATR={_atr:.1f}  IB: {_ib_low:.2f}–{_ib_high:.2f} ({ib_range:.0f}pts)\n'
        f'R:R={rr:.2f}  Stop={stop_pts:.0f}pts\n'
        f'{"✅ IBKR backup stop placed" if stop_order_id else "⚠️ Backup stop FAILED — manual stop needed"}'
    )
    log(msg)
    send_telegram(msg)
    return True


# ── Position monitoring ───────────────────────────────────────────────────────

def _pnl_usd(entry: float, price: float, side: str, contracts: int) -> float:
    pts = (price - entry) if side == 'LONG' else (entry - price)
    return round(pts * POINT_VALUE * contracts - COMMISSION, 2)


def monitor_position(df: pd.DataFrame):
    """
    Check exits + update trailing stop for open position.
    Called every MONITOR_INTERVAL seconds — from two independent scheduler jobs
    (london_scan cron + london_monitor interval). The lock ensures only one of
    them processes a given tick; without it, both can see the same open position
    and both submit a real closing order, flipping a flat account into a fresh
    unintended position once the first order closes it.
    """
    if _position is None:
        return
    if not _monitor_lock.acquire(blocking=False):
        log('  monitor_position already running elsewhere — skip this tick')
        return
    try:
        _monitor_position_locked(df)
    finally:
        _monitor_lock.release()


def _monitor_position_locked(df: pd.DataFrame):
    global _position, _daily_pnl, _last_exit_time

    if _position is None:
        return

    pos      = _position
    is_short = pos['side'] == 'SHORT'
    entry    = pos['entry']
    sl       = pos['sl']
    contracts = pos['contracts']
    p_atr    = pos['atr']
    peak     = pos['peak']

    # Get current bar close
    if df.empty:
        return
    price = float(df['close'].iloc[-1])
    now_et = datetime.now(ET)

    pnl_pts  = (price - entry) if not is_short else (entry - price)
    pnl_usd  = _pnl_usd(entry, price, pos['side'], contracts)
    daily_pnl = get_london_daily_pnl()

    exit_price  = None
    exit_reason = None

    # 1. IBKR backup stop check (check if position still held)
    ibkr_pos = _bridge_get('/futures/position')
    if isinstance(ibkr_pos, list):
        ibkr_qty = 0.0
        for p in ibkr_pos:
            if p.get('symbol') == SYMBOL:
                ibkr_qty = float(p.get('qty', 0))
                break
        position_held = (ibkr_qty > 0) if not is_short else (ibkr_qty < 0)
        if not position_held:
            # IBKR backup stop already filled
            log(f'  IBKR flat (qty={ibkr_qty:.0f}) — backup stop filled, closing London DB only')
            _cancel_backup_stop(pos)
            stop_fill = _get_stop_fill(pos.get('stop_order_id'))
            fill_px   = stop_fill or price
            realized  = _pnl_usd(entry, fill_px, pos['side'], contracts)
            _log_exit(pos['id'], fill_px, 'stop_ibkr', realized)
            _daily_pnl += realized
            emoji = '✅' if realized > 0 else '🔴'
            send_telegram(
                f'{emoji} LONDON EXIT (IBKR stop)\n'
                f'{pos["side"]} ×{contracts}  {entry:.2f}→{fill_px:.2f}\n'
                f'P&L: ${realized:+.2f}'
            )
            _position       = None
            _last_exit_time = now_et
            return

    # 2. Software stop (in case bar ticked through before monitor ran)
    if not exit_reason:
        if not is_short and price <= sl:
            exit_price, exit_reason = sl, 'stop'
        elif is_short and price >= sl:
            exit_price, exit_reason = sl, 'stop'

    # 3. DLL circuit breaker
    if not exit_reason:
        if daily_pnl + pnl_usd <= -MAX_DAILY_LOSS:
            exit_price, exit_reason = price, 'dll_circuit'

    # 4. Target hit
    if not exit_reason:
        target = pos['target']
        if not is_short and price >= target:
            exit_price, exit_reason = target, 'target'
        elif is_short and price <= target:
            exit_price, exit_reason = target, 'target'

    # 5. No-move exit
    if not exit_reason:
        age_min = (now_et - pos['entry_time']).total_seconds() / 60
        if age_min >= NO_MOVE_MINUTES and NO_MOVE_MIN_PTS <= pnl_pts <= NO_MOVE_MAX_PTS:
            exit_price, exit_reason = price, f'no_move({age_min:.0f}min)'

    # 6. EOD hard close
    if not exit_reason:
        h, m = now_et.hour, now_et.minute
        if h > LONDON_EOD[0] or (h == LONDON_EOD[0] and m >= LONDON_EOD[1]):
            exit_price, exit_reason = price, 'eod_9am'

    # ── Execute exit ──────────────────────────────────────────────────────────
    if exit_reason:
        _cancel_backup_stop(pos)
        cover_side = 'BUY' if is_short else 'SELL'
        exit_result = _bridge_post('/futures/order', {
            'symbol':     SYMBOL,
            'qty':        contracts,
            'side':       cover_side,
            'order_type': 'MARKET',
        })
        if exit_result.get('status') != 'submitted':
            log(f'  ⚠️ EXIT ORDER FAILED: {exit_result} — re-placing backup stop, will retry')
            _bridge_post('/futures/order', {
                'symbol': SYMBOL, 'qty': contracts, 'side': cover_side,
                'order_type': 'STOP_MARKET', 'stop_price': sl,
            })
            send_telegram(f'⚠️ LONDON EXIT FAILED — retrying. Check IBKR if persists.')
            return

        realized = _pnl_usd(entry, exit_price, pos['side'], contracts)
        _log_exit(pos['id'], exit_price, exit_reason, realized)
        _daily_pnl += realized
        _last_exit_time = now_et

        emoji = '✅' if realized > 0 else '🔴'
        msg = (
            f'{emoji} LONDON EXIT\n'
            f'{pos["side"]} ×{contracts}  {entry:.2f}→{exit_price:.2f}\n'
            f'P&L: ${realized:+.2f}  |  Reason: {exit_reason}'
        )
        log(msg)
        send_telegram(msg)
        _position = None
        return

    # ── Update trail (no exit — still open) ──────────────────────────────────
    new_peak = max(peak, price) if not is_short else min(peak, price)
    pos['peak'] = new_peak

    pnl_from_peak = (new_peak - entry) if not is_short else (entry - new_peak)
    new_sl = sl

    be_pts     = p_atr * BE_ATR_MULT
    wide_pts   = p_atr * TRAIL_WIDE_ATR
    wide_gap   = p_atr * TRAIL_WIDE_GAP
    tight_pts  = p_atr * TRAIL_TIGHT_ATR
    tight_gap  = p_atr * TRAIL_TIGHT_GAP

    if not pos['be_done'] and pnl_from_peak >= be_pts:
        be_sl = _tick(entry + TICK_SIZE) if not is_short else _tick(entry - TICK_SIZE)
        if (not is_short and be_sl > sl) or (is_short and be_sl < sl):
            new_sl = be_sl
            pos['be_done'] = True
            log(f'  BE triggered: sl → {new_sl:.2f}')

    if pnl_from_peak >= wide_pts:
        w_sl = _tick(new_peak - wide_gap) if not is_short else _tick(new_peak + wide_gap)
        if (not is_short and w_sl > new_sl) or (is_short and w_sl < new_sl):
            new_sl = w_sl

    if pnl_from_peak >= tight_pts:
        t_sl = _tick(new_peak - tight_gap) if not is_short else _tick(new_peak + tight_gap)
        if (not is_short and t_sl > new_sl) or (is_short and t_sl < new_sl):
            new_sl = t_sl

    if new_sl != sl:
        pos['sl'] = new_sl
        _update_backup_stop(pos, new_sl)
        log(f'  Trail update: sl {sl:.2f} → {new_sl:.2f}  pnl_pts={pnl_pts:+.1f}')


def _get_stop_fill(stop_order_id: str | None) -> float | None:
    if not stop_order_id:
        return None
    try:
        resp = _bridge_get(f'/order/{stop_order_id}/status')
        fp   = resp.get('avgFillPrice') if resp else None
        return float(fp) if fp and float(fp) > 0 else None
    except Exception:
        return None


# ── Session logic ─────────────────────────────────────────────────────────────

def reset_session():
    """Called at session start (≈3am ET). Resets all daily state."""
    global _session_date, _ib_high, _ib_low, _ib_formed, _ib_close_pos
    global _ovn_bias, _ovn_skip, _ovn_pos, _atr, _position, _trade_count
    global _daily_pnl, _last_exit_time, _cached_df
    global _active_contract_month, _stale_block_count, _ib_pending_sync

    today = datetime.now(ET).date()
    if _session_date == today:
        return  # already initialised for today

    _session_date      = today
    _ib_high           = 0.0
    _ib_low            = 0.0
    _ib_formed         = False
    _ib_close_pos      = 0.5
    _ovn_bias          = 'BOTH'
    _ovn_skip          = False
    _ovn_pos           = -1.0
    _atr               = 10.0
    _position          = None
    _trade_count       = 0
    _daily_pnl         = 0.0
    _last_exit_time    = None
    _cached_df         = pd.DataFrame()
    _stale_block_count = 0
    _ib_pending_sync   = False

    # Resolve active contract month once per session so get_bars() and
    # get_live_price() always pull from the same IBKR contract.
    _active_contract_month = get_active_contract_month()
    log(f'=== London session reset for {today} | contract={_active_contract_month or "unresolved"} ===')


def run_scan():
    """
    London scan — runs every SCAN_INTERVAL seconds (1 min).
    Responsibilities:
      1. Ensure session state is initialised.
      2. After 4am: form IB (once).
      3. Detect IB breaks and enter trades.
      4. Monitor open position exits / trail updates.
    """
    global _ib_formed, _ovn_bias, _ovn_skip, _ovn_pos, _atr, _cached_df
    global _ib_pending_sync

    if not get_bridge_connected():
        log('Bridge disconnected — skipping scan')
        return

    now_et   = datetime.now(ET)
    today_et = now_et.date()
    h, m     = now_et.hour, now_et.minute

    # Outside London window — skip
    if h < LONDON_IB_START[0] or (h == LONDON_EOD[0] and m >= LONDON_EOD[1]) or h >= LONDON_EOD[0]:
        return

    reset_session()

    # Fetch bars using the specific active contract (same one as live quote)
    df = get_bars(days=2, contract_month=_active_contract_month)
    if df.empty:
        log('No bars from bridge — skip')
        return
    _cached_df = df

    # ── 1. Form IB (once, after 4am) ─────────────────────────────────────────
    if not _ib_formed and (h > LONDON_IB_END[0] or (h == LONDON_IB_END[0] and m >= 0)):
        # Compute overnight bias
        bias, skip, ovn_pos_val = compute_overnight_bias(df, today_et)
        _ovn_pos  = ovn_pos_val
        _ovn_bias = bias
        _ovn_skip = skip

        if skip:
            log(f'Overnight ambiguous (pos={ovn_pos_val:.2f}) — skip day')
            send_telegram(f'🇬🇧 LONDON: overnight ambiguous (pos={ovn_pos_val:.2f}) — skip today')
            _price_now = float(df['close'].iloc[-1]) if not df.empty else 0.0
            try: log_block('LONDON', 'MNQ', 'BOTH', 'OVN_SKIP', f'pos={ovn_pos_val:.3f}', _price_now, 'LONDON')
            except Exception: pass
            return

        # Compute ATR from yesterday's bars (not today's London bars)
        yesterday = today_et - timedelta(days=1)
        atr_bars = df[(df.index.date >= yesterday - timedelta(days=2)) &
                      (df.index.date <  today_et)]
        _atr = compute_atr(atr_bars) if not atr_bars.empty else 10.0

        formed = update_london_ib(df, today_et)
        if not formed:
            log('IB not valid — skipping rest of day')
            _ovn_skip = True

    if _ovn_skip or not _ib_formed:
        return

    # ── 1b. Live-price sync gate (rollover / stale-streaming) ─────────────────
    if _ib_pending_sync:
        today_bars_sync = df[df.index.date == today_et]
        bar_close = float(today_bars_sync['close'].iloc[-1]) if not today_bars_sync.empty else None
        live_now  = get_live_price()
        if bar_close and live_now and abs(live_now - bar_close) < 50.0:
            _ib_pending_sync = False
            msg = (f'✅ LONDON: live price synced — entries open\n'
                   f'live={live_now:.0f}  bar={bar_close:.0f}  '
                   f'gap={abs(live_now - bar_close):.0f}pts\n'
                   f'IB: H={_ib_high:.0f}  L={_ib_low:.0f}')
            log(msg)
            send_telegram(msg)
            send_telegram(
                f'🌅 LONDON IB FORMED\n'
                f'H={_ib_high:.2f}  L={_ib_low:.2f}  '
                f'Range={_ib_high - _ib_low:.1f}pts\n'
                f'Close@{_ib_close_pos:.0%}  Overnight bias: {_ovn_bias}\n'
                f'Watching for IB breaks...'
            )
        else:
            gap = abs(live_now - bar_close) if (live_now and bar_close) else 999
            log(f'  Waiting for live sync: live={live_now}  bar={bar_close}  gap={gap:.0f}pts')
            return

    # ── 2. Monitor open position ──────────────────────────────────────────────
    if _position is not None:
        monitor_position(df)

    # ── 3. Look for new entry ─────────────────────────────────────────────────
    if _position is not None:
        return
    if _trade_count >= MAX_DAILY_TRADES:
        return

    # No entries after LONDON_ENTRY_CUTOFF (8am — last hour before NY open is weak)
    if h >= LONDON_ENTRY_CUTOFF[0]:
        return

    # Cooldown after last exit (2 min)
    if _last_exit_time is not None:
        elapsed = (now_et - _last_exit_time).total_seconds() / 60
        if elapsed < 2.0:
            log(f'Cooldown: {elapsed:.1f}min after last exit')
            return

    # Current bar close
    today_bars = df[df.index.date == today_et]
    if today_bars.empty:
        return
    price = float(today_bars['close'].iloc[-1])

    # Signal A: IB range break
    sig_a_bull = price > _ib_high
    sig_a_bear = price < _ib_low

    for side in ('LONG', 'SHORT'):
        # Only log gates when there is actually an IB break signal to evaluate
        sig_a = sig_a_bull if side == 'LONG' else sig_a_bear
        if not sig_a:
            continue

        # Respect overnight directional bias — IB break exists but bias opposes it
        if _ovn_bias == 'LONG'  and side == 'SHORT':
            try: log_block('LONDON', 'MNQ', side, 'BIAS', _ovn_bias, price, 'LONDON')
            except Exception: pass
            continue
        if _ovn_bias == 'SHORT' and side == 'LONG':
            try: log_block('LONDON', 'MNQ', side, 'BIAS', _ovn_bias, price, 'LONDON')
            except Exception: pass
            continue

        log(f'Signal A: {side} IB break  price={price:.2f}  '
            f'IB=({_ib_low:.2f}–{_ib_high:.2f})  bias={_ovn_bias}')
        placed = place_london_trade(side, price)
        if placed:
            try: log_enter('LONDON', 'MNQ', side, f'SignalA({_ovn_bias})', price, 'LONDON')
            except Exception: pass
        break  # one entry per scan


def run_monitor():
    """Fast monitor loop (every MONITOR_INTERVAL seconds). Manages open position only."""
    if _position is None:
        return
    if _cached_df.empty:
        return
    monitor_position(_cached_df)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log('=== London Trader starting ===')
    log(f'Config: STOP={STOP_ATR_MULT}×ATR  TARGET={TARGET_ATR_MULT}×ATR  '
        f'BE={BE_ATR_MULT}×ATR')
    log(f'Risk: ${MAX_RISK_PER_TRADE}/trade  DLL=${MAX_DAILY_LOSS}  '
        f'Max trades: {MAX_DAILY_TRADES}/day')

    init_db()

    if not get_bridge_connected():
        log('⚠️ Bridge not connected at startup — will retry each scan')
    else:
        log('Bridge connected ✅')

    send_telegram(
        f'🇬🇧 London Trader started\n'
        f'Stop={STOP_ATR_MULT}×ATR  Target={TARGET_ATR_MULT}×ATR  BE={BE_ATR_MULT}×ATR\n'
        f'Risk=${MAX_RISK_PER_TRADE}/trade  DLL=${MAX_DAILY_LOSS}'
    )

    scheduler = BackgroundScheduler(timezone=ET)

    # Scan every minute during London hours (3am–9am ET)
    scheduler.add_job(
        run_scan, 'cron',
        hour='3-9', minute='*',
        id='london_scan',
        max_instances=1,
        misfire_grace_time=30,
    )

    # Fast monitor every 15s (only meaningful when position is open)
    scheduler.add_job(
        run_monitor, 'interval',
        seconds=MONITOR_INTERVAL,
        id='london_monitor',
        max_instances=1,
    )

    scheduler.start()
    log('Scheduler started. Waiting for London session (3am ET)...')

    try:
        while True:
            time.sleep(30)
    except (KeyboardInterrupt, SystemExit):
        log('London Trader shutting down')
        scheduler.shutdown()
        send_telegram('🇬🇧 London Trader stopped')


if __name__ == '__main__':
    main()
