"""
options_trader.py — OPT Telegram command handler, Evidence-Based Verdict
                    spread calculator, and order execution.

Run as a standalone process. Long-polls Telegram for OPT* messages.
Zero interaction with equity auto_trader.py.

Standing commands (any time):
  OPT STATUS                         — portfolio overview + all-time stats
  OPT POSITIONS                      — per-position: Greeks, stop stage, DTE
  OPT CALENDAR                       — upcoming catalysts (30-day window)
  OPT PAUSE                          — suspend scanning
  OPT RESUME                         — re-enable scanning
  OPT CLOSE <sym>                    — prompt to close open position on <sym>
  OPT ADD <sym> <YYYY-MM-DD> <note> [HIGH|MEDIUM|LOW]  — add DOMAIN_INSIGHT catalyst
  OPT BUY <sym> [qty]                — Evidence-Based Verdict: HV30 edge, EM strikes,
                                       5-gate entry, net Greeks, Monte Carlo EV

After OPT BUY (30-min timeout):
  CONFIRM = place the recommended spread trade
  SKIP    = cancel

Close confirmation:
  YES = execute, NO = cancel
"""

import os
import sys
import time
import json
import threading
import traceback
import requests
import yfinance as yf
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine   # options/engine.py — same directory
from database import (
    get_open_options_trades,
    get_upcoming_catalysts,
    add_catalyst,
    log_options_trade,
    close_options_trade,
    get_closed_options_count,
    get_options_learning_data,
    get_options_total_pnl,
    get_options_deployed_capital,
    get_recent_news,
    get_news_quality_stats,
    get_conviction_leaderboard,
    get_conviction_detail,
    log_calc_run,
    update_calc_action,
    log_trade_outcome,
    get_pending_suggestions,
    update_suggestion_calc,
    update_suggestion_status,
    update_suggestion_decision,
)

load_dotenv()

# ── Config ───────────────────────────────────────────────────────────────────
BRIDGE_URL       = os.getenv('BRIDGE_URL', 'http://127.0.0.1:8000')
TELEGRAM_TOKEN   = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
# Options-specific bot (falls back to main bot if not configured)
OPT_TG_TOKEN     = os.getenv('OPTIONS_TELEGRAM_TOKEN') or TELEGRAM_TOKEN
OPT_TG_CHAT_ID   = os.getenv('OPTIONS_TELEGRAM_CHAT_ID') or TELEGRAM_CHAT_ID
TG_API           = f"https://api.telegram.org/bot{OPT_TG_TOKEN}"
# Dedicated options capital allocation ($5K start; 30% max deployed = $1.5K cap)
OPTIONS_ACCOUNT_SIZE    = float(os.getenv('OPTIONS_ACCOUNT_SIZE', '5000'))
OPTIONS_CIRCUIT_BREAKER = float(os.getenv('OPTIONS_CIRCUIT_BREAKER', '2000'))  # max realized loss
OPTIONS_TOTAL_CAPITAL   = float(os.getenv('OPTIONS_TOTAL_CAPITAL',   '5000'))  # total pool
MAX_OPTIONS_POSITIONS   = int(os.getenv('MAX_OPTIONS_POSITIONS',     '4'))     # max concurrent

ET               = ZoneInfo('America/New_York')
COMMISSION_RT    = 3.60    # round-trip per spread (2 legs × open + close)
MAX_SLIPPAGE     = 0.15    # max $ above mid before cancel
FILL_WAIT_SEC    = 45      # seconds between fill checks
MAX_FILL_TRIES   = 4       # attempts: mid, mid+.05, mid+.10, mid+.15

LARGE_CAP_UNIVERSE = {
    # Mega/large cap >$100B — deep options liquidity, expensive contracts, qty=1
    'NVDA', 'META', 'AMZN', 'MSFT', 'AAPL', 'TSLA', 'AMD', 'GOOGL',
    'ORCL', 'PLTR', 'APP',  'CRWD', 'ARM',  'SHOP',
}
MID_CAP_UNIVERSE = {
    # Mid cap $2B-$100B — cheaper contracts, auto-qty targets $1,200/position
    'COIN', 'AXON', 'HOOD', 'SMCI', 'RKLB',
    'IONQ', 'CELH', 'AFRM', 'SOFI', 'HIMS', 'MARA',
}
AUTO_QTY_TARGET = 1200   # target dollars per options position (auto-qty scales to this)

# ── Global state ──────────────────────────────────────────────────────────────
_paused          = False
_pending: dict   = {}       # chat_id → {action, symbol, qty, templates, leap, expires_at}
_last_update_id  = 0

def _is_paper() -> bool:
    """Return True if connected to IBKR paper account (port 4002)."""
    info = bridge_get('/')
    return (info or {}).get('mode') == 'paper'


# ── Telegram helpers ─────────────────────────────────────────────────────────

def send_telegram(message: str, chat_id: str | None = None):
    cid = chat_id or OPT_TG_CHAT_ID
    if not OPT_TG_TOKEN or not cid:
        print(f"[TG] {message}")
        return
    try:
        r = requests.post(
            f"{TG_API}/sendMessage",
            json={'chat_id': cid, 'text': message, 'parse_mode': 'Markdown'},
            timeout=10,
        )
        if not r.ok:
            # Markdown parse error — retry as plain text
            print(f"[TG markdown error] {r.status_code} {r.json().get('description')}")
            requests.post(
                f"{TG_API}/sendMessage",
                json={'chat_id': cid, 'text': message},
                timeout=10,
            )
    except Exception as e:
        print(f"[TG error] {e}")


def poll_telegram(timeout: int = 0) -> list[dict]:
    """Fetch updates without advancing offset — caller decides what to consume."""
    try:
        r = requests.get(
            f"{TG_API}/getUpdates",
            params={'offset': _last_update_id + 1, 'timeout': timeout},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        return r.json().get('result', [])
    except Exception as e:
        print(f"[poll error] {e}")
        return []


# ── Bridge helpers ────────────────────────────────────────────────────────────

def bridge_get(path: str) -> dict | None:
    try:
        r = requests.get(f"{BRIDGE_URL}{path}", timeout=12)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[bridge GET] {path} — {e}")
    return None


def bridge_post(path: str, payload: dict) -> dict | None:
    try:
        r = requests.post(f"{BRIDGE_URL}{path}", json=payload, timeout=12)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[bridge POST] {path} — {e}")
    return None


def get_iv_rank(symbol: str) -> dict | None:
    return bridge_get(f"/options/iv_rank/{symbol}")


def get_chain(symbol: str) -> dict | None:
    # Try IBKR bridge first
    data = bridge_get(f"/options/chain/{symbol}")
    if data and data.get('chain'):
        return data
    # Fallback: yfinance expiry list
    try:
        tk       = yf.Ticker(symbol)
        expiries = tk.options   # tuple of 'YYYY-MM-DD' strings
        chain    = [{'expiry': e.replace('-', ''), 'right': 'C', 'strikes': []}
                    for e in expiries]
        return {'symbol': symbol, 'chain': chain}
    except Exception:
        return None


_yf_chain_cache: dict = {}   # (symbol, expiry, right) → DataFrame, TTL per session


def _yf_option_chain(symbol: str, expiry: str, right: str):
    """Fetch and cache yfinance options chain per (symbol, expiry, right)."""
    key = (symbol.upper(), expiry, right.upper())
    if key not in _yf_chain_cache:
        exp_fmt = f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:]}"
        chain   = yf.Ticker(symbol).option_chain(exp_fmt)
        _yf_chain_cache[key] = chain.calls if right.upper() == 'C' else chain.puts
    return _yf_chain_cache[key]


def get_quote(symbol: str, expiry: str, strike: float, right: str = 'C') -> dict | None:
    # Try IBKR bridge first — uses 15-min delayed data, returns real delta + IV
    try:
        data = bridge_get(f"/options/quote/{symbol}/{expiry}/{strike}/{right}")
        if data and data.get('bid') and data.get('ask'):
            return {
                'bid':   data['bid'],
                'ask':   data['ask'],
                'mid':   data.get('mid'),
                'delta': data.get('delta'),
                'theta': data.get('theta'),
                'vega':  data.get('vega'),
                'iv':    data.get('iv'),
            }
    except Exception:
        pass
    # Fallback: yfinance (no delta, but reliable chain data)
    try:
        df  = _yf_option_chain(symbol, expiry, right)
        row = df[df['strike'] == strike]
        if row.empty:
            row = df.iloc[(df['strike'] - strike).abs().argsort()[:1]]
        if not row.empty:
            r   = row.iloc[0]
            bid = float(r.get('bid', 0) or 0)
            ask = float(r.get('ask', 0) or 0)
            mid = round((bid + ask) / 2, 4) if bid and ask else None
            return {
                'bid':   bid or None,
                'ask':   ask or None,
                'mid':   mid,
                'delta': None,
                'iv':    float(r.get('impliedVolatility', 0) or 0) or None,
            }
    except Exception:
        pass
    return None


def get_underlying_price(symbol: str) -> float | None:
    # Try bridge first (live IBKR stream)
    data = bridge_get(f"/quote/{symbol}")
    if data:
        price = data.get('last') or data.get('mid') or data.get('bid')
        if price:
            return price
    # Fallback: yfinance (covers symbols not in equity universe)
    try:
        ticker = yf.Ticker(symbol)
        info   = ticker.fast_info
        price  = getattr(info, 'last_price', None) or getattr(info, 'regular_market_price', None)
        if price:
            return float(price)
    except Exception:
        pass
    return None


def get_order_status(order_id: int) -> dict | None:
    return bridge_get(f"/order/{order_id}/status")


def cancel_all_orders():
    bridge_post("/cancel_all", {})


def get_portfolio_options() -> list[dict]:
    data = bridge_get("/portfolio/options")
    return data if isinstance(data, list) else []


def get_account_nav() -> float | None:
    """Return account net liquidation value from IBKR bridge."""
    data = bridge_get("/account")
    if data and data.get('NetLiquidation'):
        return float(data['NetLiquidation'])
    return None


def get_deployed_capital() -> float:
    """Sum of premium_paid for all open options positions."""
    trades = get_open_options_trades()
    return sum(t.get('premium_paid') or 0 for t in trades)


def check_capital_cap(new_premium: float, chat_id: str) -> bool:
    """
    Return True if adding new_premium stays within the 30% capital deployment cap.
    Uses OPTIONS_ACCOUNT_SIZE (dedicated options allocation), not full IBKR NAV.
    """
    deployed  = get_deployed_capital()
    cap_limit = OPTIONS_ACCOUNT_SIZE * 0.30
    if deployed + new_premium > cap_limit:
        send_telegram(
            f"🚫 *Capital cap reached*\n"
            f"Deployed: ${deployed:,.0f} + new ${new_premium:,.0f} = ${deployed+new_premium:,.0f}\n"
            f"30% cap on ${OPTIONS_ACCOUNT_SIZE:,.0f} options allocation = ${cap_limit:,.0f}\n"
            f"Close an existing position or wait for more capital.",
            chat_id,
        )
        return False
    remaining = cap_limit - deployed - new_premium
    send_telegram(
        f"✅ Cap check passed — ${remaining:,.0f} remaining headroom after this trade.",
        chat_id,
    )
    return True


# ── Circuit breaker ───────────────────────────────────────────────────────────

def check_circuit_breaker(chat_id: str) -> bool:
    """
    Block new entries if cumulative realized options losses exceed OPTIONS_CIRCUIT_BREAKER.
    Returns True if OK to trade, False if breaker is tripped.
    """
    total_pnl = get_options_total_pnl()
    if total_pnl < -OPTIONS_CIRCUIT_BREAKER:
        send_telegram(
            f"🛑 *Options circuit breaker tripped*\n"
            f"Cumulative realized loss: ${abs(total_pnl):,.0f} "
            f"exceeds ${OPTIONS_CIRCUIT_BREAKER:,.0f} limit\n"
            f"No new entries until losses recover. Review open positions.",
            chat_id,
        )
        return False
    return True


def capital_status() -> dict:
    """Return deployed, available, and open slot count. Central fact source."""
    open_trades = get_open_options_trades()
    deployed    = sum(t['premium_paid'] or 0 for t in open_trades)
    available   = max(0.0, OPTIONS_TOTAL_CAPITAL - deployed)
    slots_used  = len(open_trades)
    slots_free  = max(0, MAX_OPTIONS_POSITIONS - slots_used)
    return {
        'deployed':   round(deployed, 2),
        'available':  round(available, 2),
        'slots_used': slots_used,
        'slots_free': slots_free,
        'total':      OPTIONS_TOTAL_CAPITAL,
    }


def can_open_position(estimated_cost: float = 0.0, chat_id: str | None = None) -> bool:
    """
    Returns True if a new position can be opened.
    Checks: open slot count, available capital.
    Sends a message to chat_id if blocked (when chat_id is provided).
    """
    cs = capital_status()
    if cs['slots_free'] <= 0:
        if chat_id:
            send_telegram(
                f"⛔ All {MAX_OPTIONS_POSITIONS} position slots full "
                f"({cs['slots_used']} open). Capital re-deploys when a trade closes.",
                chat_id,
            )
        return False
    if estimated_cost > 0 and estimated_cost > cs['available']:
        if chat_id:
            send_telegram(
                f"⛔ Insufficient capital: ${estimated_cost:.0f} needed, "
                f"${cs['available']:.0f} available "
                f"(${cs['deployed']:.0f} deployed of ${cs['total']:.0f}).",
                chat_id,
            )
        return False
    return True


# ── Extended-hours awareness ──────────────────────────────────────────────────

def is_options_session() -> bool:
    """Options can be traded 9:15am–8:00pm ET Mon-Fri."""
    n = datetime.now(ET)
    if n.weekday() >= 5:
        return False
    open_  = n.replace(hour=9,  minute=15, second=0, microsecond=0)
    close_ = n.replace(hour=20, minute=0,  second=0, microsecond=0)
    return open_ <= n <= close_


def session_liquidity_note() -> str:
    """Return a warning if we're outside regular market hours."""
    n = datetime.now(ET)
    h, m = n.hour, n.minute
    if h < 9 or (h == 9 and m < 30):
        return " ⚠️ Pre-market — spreads wider than normal"
    if h >= 16:
        return " ⚠️ Post-market — spreads wider, fills slower"
    return ""


# ── Market helpers ────────────────────────────────────────────────────────────

def is_above_200ma(symbol: str) -> bool:
    try:
        hist = yf.Ticker(symbol).history(period='1y')
        if len(hist) < 200:
            return False
        ma200   = hist['Close'].rolling(200).mean().iloc[-1]
        current = hist['Close'].iloc[-1]
        return float(current) > float(ma200)
    except Exception:
        return False


def days_to_expiry(expiry_str: str) -> int:
    try:
        expiry_str = expiry_str.replace('-', '')
        expiry     = datetime.strptime(expiry_str, '%Y%m%d').date()
        return (expiry - date.today()).days
    except Exception:
        return 9999


def cap_type(symbol: str) -> str:
    s = symbol.upper()
    if s in LARGE_CAP_UNIVERSE:
        return 'LARGE'
    return 'MID'


# ── Grading ───────────────────────────────────────────────────────────────────

def grade_spread(iv_rank: float, catalyst_days: int | None,
                 liquidity_ok: bool) -> str:
    has_catalyst = catalyst_days is not None and catalyst_days <= 21
    if iv_rank < 35 and has_catalyst and liquidity_ok:
        return 'A+'
    if iv_rank < 40 and liquidity_ok:
        return 'A'
    if iv_rank < 45 and liquidity_ok:
        return 'B'
    return 'C'


def grade_leap(iv_rank: float, catalyst_days: int | None,
               above_200ma: bool, domain_insight: bool) -> str:
    has_catalyst = catalyst_days is not None
    in_window    = has_catalyst and 28 <= (catalyst_days or 0) <= 56
    if iv_rank < 25 and in_window and above_200ma and domain_insight:
        return 'A+'
    if iv_rank < 30 and has_catalyst and above_200ma:
        return 'A'
    if iv_rank < 35 and above_200ma:
        return 'B'
    return 'C'


# ── Calculator internals ──────────────────────────────────────────────────────

def _find_expiry(expiries: list[str], dte_min: int, dte_max: int) -> str | None:
    """Return the expiry closest to the middle of [dte_min, dte_max]."""
    target = (dte_min + dte_max) // 2
    valid  = [(e, abs(days_to_expiry(e) - target)) for e in expiries
              if dte_min <= days_to_expiry(e) <= dte_max]
    return min(valid, key=lambda x: x[1])[0] if valid else None


def _find_nearest_strike(available: list[float], target: float) -> float | None:
    if not available:
        return None
    return min(available, key=lambda s: abs(s - target))


def _actual_strikes(symbol: str, expiry: str) -> list[float]:
    """
    Return the strikes actually listed for this expiry (from yfinance).
    These are the strikes IBKR will accept for order qualification.
    Falls back to [] if yfinance can't fetch — caller falls back to theoretical list.
    """
    try:
        df = _yf_option_chain(symbol, expiry, 'C')
        return sorted(df['strike'].tolist())
    except Exception:
        return []


def _calc_template(symbol: str, underlying: float, expiry: str,
                   long_strike: float, short_strike: float,
                   iv_rank: float, catalyst_days: int | None,
                   template_name: str) -> dict | None:
    """Fetch quotes, compute EV, liquidity, grade for one spread template."""
    lq = get_quote(symbol, expiry, long_strike)
    sq = get_quote(symbol, expiry, short_strike)
    if not lq or not sq:
        return None
    if any(v is None for v in (lq.get('bid'), lq.get('ask'), sq.get('bid'), sq.get('ask'))):
        return None

    long_bid  = float(lq['bid'])
    long_ask  = float(lq['ask'])
    short_bid = float(sq['bid'])
    short_ask = float(sq['ask'])
    long_mid  = (long_bid  + long_ask)  / 2
    short_mid = (short_bid + short_ask) / 2
    net_debit = round(long_mid - short_mid, 2)

    # Per-contract dollar values (×100 multiplier)
    net_debit_dollar   = round(net_debit * 100, 2)
    spread_width       = short_strike - long_strike
    max_profit_dollar  = round((spread_width - net_debit) * 100, 2)
    max_loss_dollar    = net_debit_dollar

    # Liquidity flags
    long_spread_ok  = (long_ask  - long_bid)  <= 0.30
    short_spread_ok = (short_ask - short_bid) <= 0.25
    liquidity_ok    = long_spread_ok and short_spread_ok
    min_debit_ok    = net_debit >= 0.80

    # EV using delta as probability proxy (per-contract)
    prob_profit = sq.get('delta') or 0.30    # prob short finishes ITM = max profit
    prob_loss   = 1.0 - (lq.get('delta') or 0.40)  # prob long finishes OTM = max loss
    ev_gross    = prob_profit * max_profit_dollar - prob_loss * max_loss_dollar
    ev_net      = round(ev_gross - COMMISSION_RT, 2)
    rr_net      = round(max_profit_dollar / max_loss_dollar, 2) if max_loss_dollar > 0 else 0

    dte         = days_to_expiry(expiry)
    grd         = grade_spread(iv_rank, catalyst_days, liquidity_ok)

    return {
        'template':    template_name,
        'expiry':      expiry,
        'dte':         dte,
        'long_strike': long_strike,
        'short_strike': short_strike,
        'net_debit':   net_debit,
        'net_debit_$': net_debit_dollar,
        'max_profit_$': max_profit_dollar,
        'max_loss_$':  max_loss_dollar,
        'ev_net':      ev_net,
        'rr_net':      rr_net,
        'prob_profit': round(prob_profit * 100, 0),
        'prob_loss':   round(prob_loss   * 100, 0),
        'delta_long':  lq.get('delta'),
        'delta_short': sq.get('delta'),
        'long_ba':     round(long_ask  - long_bid,  2),
        'short_ba':    round(short_ask - short_bid, 2),
        'liquidity_ok': liquidity_ok,
        'min_debit_ok': min_debit_ok,
        'grade':       grd,
    }


def _calc_leap(symbol: str, underlying: float, expiry: str, strike: float,
               iv_rank: float, catalyst_days: int | None,
               above_200ma: bool, domain_insight: bool) -> dict | None:
    """Fetch quote, grade LEAP."""
    q = get_quote(symbol, expiry, strike)
    if not q or q.get('bid') is None:
        return None

    bid = q['bid']
    ask = q['ask']
    mid = round((bid + ask) / 2, 2)
    premium_dollar = round(mid * 100, 2)
    ba_spread      = round(ask - bid, 2)
    delta          = q.get('delta')
    dte            = days_to_expiry(expiry)

    # Rough EV: prob profit ≈ delta, target +50% gain
    prob_profit  = delta or 0.65
    target_gain  = round(premium_dollar * 0.50, 2)
    ev_net       = round(prob_profit * target_gain - (1 - prob_profit) * premium_dollar * 0.40 - COMMISSION_RT, 2)
    grd          = grade_leap(iv_rank, catalyst_days, above_200ma, domain_insight)

    return {
        'expiry':         expiry,
        'dte':            dte,
        'strike':         strike,
        'mid':            mid,
        'premium_$':      premium_dollar,
        'bid_ask_spread': ba_spread,
        'delta':          delta,
        'theta':          q.get('theta'),
        'vega':           q.get('vega'),
        'ev_net':         ev_net,
        'grade':          grd,
        'above_200ma':    above_200ma,
    }


# ── Full LEAP calculator (mirrors run_calculator for bull spreads) ────────────

def run_leap_calculator(symbol: str, qty: int = 1) -> dict:
    """
    Full LEAP analysis: same 5-gate scoring, MC EV via engine (single-leg GBM).
    Targets 18-month DTE, 5% OTM strike.
    """
    sym = symbol.upper()

    iv_data    = get_iv_rank(sym)
    iv_rank    = iv_data.get('iv_rank',    50.0) if iv_data else 50.0
    current_iv = iv_data.get('current_iv')       if iv_data else None

    underlying = get_underlying_price(sym)
    if not underlying:
        return {'error': f'Cannot fetch price for {sym}'}

    chain_data = get_chain(sym)
    if not chain_data or 'chain' not in chain_data:
        return {'error': f'Cannot fetch option chain for {sym}'}

    expiries = [item['expiry'] for item in chain_data['chain']]
    stock_data  = engine.get_stock_data(sym)
    hv30        = stock_data['hv30']
    above_200   = stock_data['above_200']
    momentum_5d = stock_data['momentum_5d']

    iv_for_calc  = current_iv or iv_rank or 40.0
    vol_analysis = (engine.assess_volatility_edge(iv_for_calc, hv30)
                    if hv30 else {'edge_pts': None, 'verdict': 'UNKNOWN',
                                  'gate_pass': True, 'iv': iv_for_calc, 'hv30': None})

    upcoming      = get_upcoming_catalysts(days=90)
    sym_cats      = [c for c in upcoming if c['symbol'] == sym]
    catalyst_days = None
    catalyst_id   = None
    if sym_cats:
        cat_date      = datetime.strptime(sym_cats[0]['date'], '%Y-%m-%d').date()
        catalyst_days = (cat_date - date.today()).days
        catalyst_id   = sym_cats[0]['id']

    conviction    = get_conviction_detail(sym)
    tier          = conviction.get('tier', 'LOW')
    direction     = conviction.get('direction', 'MIXED')
    signal_count  = conviction.get('signal_count', 0)
    narrative     = conviction.get('narrative', '')
    conviction_gate = (tier in ('HIGH', 'MEDIUM')) and (direction == 'BULLISH')

    # LEAP: 15-24 month expiry (450–730 DTE)
    expiry = _find_expiry(expiries, 450, 730)
    if not expiry:
        expiry = _find_expiry(expiries, 300, 730)
    if not expiry:
        return {'error': f'No LEAP-eligible expiry found for {sym}'}

    dte = days_to_expiry(expiry)

    # Strike: 5% OTM on actual IBKR strikes
    target_strike = round(underlying * 1.05, 0)
    avail = _actual_strikes(sym, expiry)
    strike = _find_nearest_strike(avail, target_strike) if avail else target_strike

    q = get_quote(sym, expiry, strike)
    if not q or q.get('bid') is None or q.get('ask') is None:
        return {'error': f'Cannot fetch LEAP quote for {sym} {strike}'}

    bid = float(q['bid'] or 0)
    ask = float(q['ask'] or 0)
    if bid <= 0 or ask <= 0:
        return {'error': f'No valid LEAP bid/ask for {sym}'}

    mid           = round((bid + ask) / 2, 2)
    ba_spread     = round(ask - bid, 2)
    net_debit     = mid
    net_debit_dol = round(mid * 100 * qty, 2)
    breakeven     = round(strike + mid, 2)
    breakeven_pct = round((breakeven - underlying) / underlying * 100, 1)

    # 5-gate scoring — same framework, LEAP-specific thresholds
    liquidity_gate  = ba_spread <= 0.60        # LEAP spreads wider — allow up to $0.60
    momentum_gate   = (momentum_5d is not None and momentum_5d >= 1.0) or \
                      (catalyst_days is not None and catalyst_days <= 3)

    entry_gates = engine.score_entry_gates(
        vol_gate=vol_analysis.get('gate_pass', True),
        tech_gate=above_200,
        conviction_gate=conviction_gate,
        liquidity_gate=liquidity_gate,
        momentum_gate=momentum_gate,
    )

    # Greeks from IBKR quote
    net_greeks = {
        'net_delta': q.get('delta'),
        'net_theta': round((q.get('theta') or 0) * 100, 2),
        'net_vega':  round((q.get('vega')  or 0) * 100, 2),
    }

    # Theta velocity (same function — single-leg uses same daily move math)
    hv30_for_vel = hv30 or iv_for_calc
    velocity = engine.compute_theta_velocity(breakeven, underlying, dte, hv30_for_vel)

    # MC EV: single-leg call — model as spread with very high short strike (→ 0 short value)
    mc_ev = engine.run_monte_carlo_ev(
        price=underlying,
        iv_pct=iv_for_calc,
        dte=dte,
        long_strike=strike,
        short_strike=underlying * 10,   # far OTM → short leg ≈ 0, simulates single call
        net_debit=net_debit,
        hv30=hv30,
    )

    grd = grade_leap(iv_rank, catalyst_days, above_200, bool(sym_cats))

    return {
        'strategy':     'LEAP',
        'symbol':       sym,
        'underlying':   underlying,
        'expiry':       expiry,
        'dte':          dte,
        'strike':       strike,
        'iv_rank':      iv_rank,
        'current_iv':   current_iv,
        'hv30':         hv30,
        'vol_analysis': vol_analysis,
        'net_debit':    net_debit,
        'net_debit_$':  net_debit_dol,
        'breakeven':    breakeven,
        'breakeven_pct': breakeven_pct,
        'long_ba':      ba_spread,
        'entry_gates':  entry_gates,
        'net_greeks':   net_greeks,
        'velocity':     velocity,
        'mc_ev':        mc_ev,
        'momentum_5d':  momentum_5d,
        'above_200':    above_200,
        'conviction':   {'tier': tier, 'direction': direction,
                         'signals': signal_count, 'narrative': narrative},
        'catalyst_days': catalyst_days,
        'catalyst_id':   catalyst_id,
        'qty':           qty,
        'grade':         grd,
        'trade': {
            'qty':         qty,
            'net_debit':   net_debit,
            'expiry':      expiry,
            'dte':         dte,
            'strike':      strike,
            'underlying':  underlying,
            'iv_rank':     iv_rank,
            'iv_pct':      current_iv,
            'grade':       grd,
            'template':    'LEAP-5pct-OTM',
            'delta_long':  q.get('delta'),
            'catalyst_id': catalyst_id,
            'catalyst_days': catalyst_days,
        },
    }


# ── Auto-qty: scale contracts to target $1,200/position ──────────────────────

def _auto_qty_calc(calc: dict) -> dict:
    """
    Scale qty so total position cost targets AUTO_QTY_TARGET.
    Only applies when the original calc was run at qty=1 (prevents double-scaling).
    Modifies calc in place and returns it.
    """
    if calc.get('qty', 1) != 1:
        return calc
    cost_per = calc.get('net_debit_$') or 0
    if cost_per <= 0:
        return calc
    auto_qty = max(1, min(5, int(AUTO_QTY_TARGET / cost_per)))
    if auto_qty == 1:
        return calc

    calc['qty'] = auto_qty
    calc['net_debit_$']  = round(calc['net_debit'] * 100 * auto_qty, 2)
    if calc.get('max_profit_$') is not None:
        calc['max_profit_$'] = round((calc.get('max_profit') or 0) * 100 * auto_qty, 2)
    if calc.get('max_loss_$') is not None:
        calc['max_loss_$']   = round(calc['net_debit'] * 100 * auto_qty, 2)
    if isinstance(calc.get('trade'), dict):
        calc['trade']['qty'] = auto_qty
    total_ev = (calc.get('mc_ev') or {}).get('ev_dollar')
    total_ev_str = f"MC EV ${total_ev * auto_qty:+.0f} total" if total_ev is not None else ""
    calc['_auto_qty_note'] = (
        f"Auto {auto_qty}× contracts (${cost_per:.0f}/contract → "
        f"${calc['net_debit_$']:.0f} total{', ' + total_ev_str if total_ev_str else ''})"
    )
    return calc


# ── Strategy comparison: pick BULL_SPREAD vs LEAP based on MC EV ─────────────

def run_strategy_comparison(symbol: str, qty: int = 1) -> dict:
    """
    Run both BULL_SPREAD and LEAP calculators. Return the one with higher MC EV
    (or better gate score if EV is tied/unavailable). Attaches comparison summary.
    """
    sym = symbol.upper()
    spread_calc = run_calculator(sym, qty)
    leap_calc   = run_leap_calculator(sym, qty)

    spread_err = 'error' in spread_calc
    leap_err   = 'error' in leap_calc

    if spread_err and leap_err:
        return spread_calc   # both failed, return spread error

    if spread_err:
        leap_calc['_comparison'] = 'LEAP only (spread failed)'
        return leap_calc

    if leap_err:
        spread_calc['_comparison'] = 'SPREAD only (LEAP failed)'
        return spread_calc

    # Both succeeded — compare MC EV
    spread_ev  = (spread_calc.get('mc_ev') or {}).get('ev_dollar') or 0
    leap_ev    = (leap_calc.get('mc_ev')   or {}).get('ev_dollar') or 0
    spread_gates = (spread_calc.get('entry_gates') or {}).get('gates_pass', 0)
    leap_gates   = (leap_calc.get('entry_gates')   or {}).get('gates_pass', 0)

    # Prefer higher MC EV; gate score breaks ties
    if spread_ev >= leap_ev and spread_gates >= leap_gates:
        winner = spread_calc
        loser_label  = f"LEAP: ${leap_ev:+.0f} EV, {leap_gates}/5 gates"
        winner_label = f"SPREAD: ${spread_ev:+.0f} EV, {spread_gates}/5 gates"
    elif leap_ev > spread_ev or leap_gates > spread_gates:
        winner = leap_calc
        loser_label  = f"SPREAD: ${spread_ev:+.0f} EV, {spread_gates}/5 gates"
        winner_label = f"LEAP: ${leap_ev:+.0f} EV, {leap_gates}/5 gates"
    else:
        winner = spread_calc
        loser_label  = f"LEAP: ${leap_ev:+.0f} EV, {leap_gates}/5 gates"
        winner_label = f"SPREAD: ${spread_ev:+.0f} EV, {spread_gates}/5 gates"

    winner['_comparison'] = f"✅ {winner_label} beats {loser_label}"
    return winner


# ── Evidence-Based Verdict Calculator ────────────────────────────────────────

def run_calculator(symbol: str, qty: int = 1) -> dict:
    """
    Evidence-Based Verdict System. Returns a single recommended trade (or SKIP)
    with: HV30 volatility edge, EM-anchored strikes, net Greeks, theta velocity,
    Monte Carlo EV, and 5-gate entry scoring.
    """
    sym = symbol.upper()

    # ── Market data ───────────────────────────────────────────────────────
    iv_data    = get_iv_rank(sym)
    iv_rank    = iv_data.get('iv_rank',    50.0) if iv_data else 50.0
    current_iv = iv_data.get('current_iv')       if iv_data else None

    underlying = get_underlying_price(sym)
    if not underlying:
        return {'error': f'Cannot fetch price for {sym}'}

    chain_data = get_chain(sym)
    if not chain_data or 'chain' not in chain_data:
        return {'error': f'Cannot fetch option chain for {sym}'}

    expiries     = [item['expiry'] for item in chain_data['chain']]
    theo_strikes = chain_data['chain'][0]['strikes'] if chain_data['chain'] else []

    # ── Stock analytics (single yfinance call) ────────────────────────────
    stock_data  = engine.get_stock_data(sym)
    hv30        = stock_data['hv30']
    above_200   = stock_data['above_200']
    momentum_5d = stock_data['momentum_5d']

    # ── Volatility edge (Gate 1) ──────────────────────────────────────────
    iv_for_calc = current_iv or iv_rank or 40.0
    if hv30 is not None:
        vol_analysis = engine.assess_volatility_edge(iv_for_calc, hv30)
    else:
        vol_analysis = {'edge_pts': None, 'verdict': 'UNKNOWN', 'gate_pass': True,
                        'iv': iv_for_calc, 'hv30': None}

    # ── Upcoming catalyst ─────────────────────────────────────────────────
    upcoming      = get_upcoming_catalysts(days=90)
    sym_cats      = [c for c in upcoming if c['symbol'] == sym]
    catalyst_days = None
    catalyst_id   = None
    if sym_cats:
        cat_date      = datetime.strptime(sym_cats[0]['date'], '%Y-%m-%d').date()
        catalyst_days = (cat_date - date.today()).days
        catalyst_id   = sym_cats[0]['id']

    # ── Conviction (Gate 3) ───────────────────────────────────────────────
    conviction    = get_conviction_detail(sym)
    tier          = conviction.get('tier', 'LOW')
    direction     = conviction.get('direction', 'MIXED')
    signal_count  = conviction.get('signal_count', 0)
    narrative     = conviction.get('narrative', '')
    conviction_gate = (tier in ('HIGH', 'MEDIUM')) and (direction == 'BULLISH')

    # ── Expiry selection (catalyst-driven or balanced DTE) ────────────────
    if catalyst_days and 21 <= catalyst_days <= 75:
        target_dte = catalyst_days + 14
        valid_exp  = [e for e in expiries if days_to_expiry(e) >= 21]
        expiry     = min(valid_exp, key=lambda e: abs(days_to_expiry(e) - target_dte), default=None)
    else:
        expiry = _find_expiry(expiries, 28, 45)

    if not expiry:
        return {'error': f'No suitable expiry for {sym} (no options 28-45 DTE)'}

    dte = days_to_expiry(expiry)

    # ── Expected Move and EM-anchored strike selection ────────────────────
    em = engine.compute_expected_move(underlying, iv_for_calc, dte)

    strikes = _actual_strikes(sym, expiry) or theo_strikes
    if not strikes:
        return {'error': f'Cannot fetch strike list for {sym} {expiry}'}

    target_long  = underlying + em * 0.33   # ~33% of 1-SD move — moderate OTM entry
    target_short = underlying + em * 1.00   # at the 1-SD level — natural cap
    long_strike  = _find_nearest_strike(strikes, target_long)
    short_strike = _find_nearest_strike(strikes, target_short)

    if not long_strike or not short_strike or long_strike >= short_strike:
        return {'error': f'Cannot determine valid strikes for {sym} (EM=${em}, strikes near ${target_long:.0f}/${target_short:.0f})'}

    # ── Quotes with full Greeks ───────────────────────────────────────────
    lq = get_quote(sym, expiry, long_strike)
    sq = get_quote(sym, expiry, short_strike)

    if not lq or not sq:
        return {'error': f'Cannot fetch option quotes for {sym}'}

    long_bid  = float(lq.get('bid') or 0)
    long_ask  = float(lq.get('ask') or 0)
    short_bid = float(sq.get('bid') or 0)
    short_ask = float(sq.get('ask') or 0)

    if not (long_bid and long_ask and short_bid and short_ask):
        return {'error': f'Incomplete bid/ask for {sym} options (check market hours or IBKR connection)'}

    long_mid  = (long_bid  + long_ask)  / 2
    short_mid = (short_bid + short_ask) / 2
    net_debit = round(long_mid - short_mid, 2)

    spread_width      = short_strike - long_strike
    net_debit_dollar  = round(net_debit * 100, 2)
    max_profit_dollar = round((spread_width - net_debit) * 100, 2)
    max_loss_dollar   = net_debit_dollar
    breakeven         = round(long_strike + net_debit, 2)
    breakeven_pct     = round((breakeven - underlying) / underlying * 100, 1)

    # ── Gate evaluation ───────────────────────────────────────────────────
    long_ba  = round(long_ask  - long_bid,  2)
    short_ba = round(short_ask - short_bid, 2)
    liquidity_gate = long_ba <= 0.30 and short_ba <= 0.30

    momentum_gate = (momentum_5d is not None and momentum_5d >= 1.0) or \
                    (catalyst_days is not None and catalyst_days <= 3)

    entry_gates = engine.score_entry_gates(
        vol_gate=vol_analysis.get('gate_pass', True),
        tech_gate=above_200,
        conviction_gate=conviction_gate,
        liquidity_gate=liquidity_gate,
        momentum_gate=momentum_gate,
    )

    # ── Net Greeks ────────────────────────────────────────────────────────
    net_greeks = engine.compute_net_greeks(lq, sq)

    # ── Theta velocity ────────────────────────────────────────────────────
    hv30_for_vel = hv30 or iv_for_calc
    velocity = engine.compute_theta_velocity(breakeven, underlying, dte, hv30_for_vel)

    # ── Monte Carlo EV ────────────────────────────────────────────────────
    mc_ev = engine.run_monte_carlo_ev(
        price=underlying,
        iv_pct=iv_for_calc,
        dte=dte,
        long_strike=long_strike,
        short_strike=short_strike,
        net_debit=net_debit,
        hv30=hv30,   # two-sigma: paths on HV30, pricing on IV
    )

    # ── Trade dict for execution (consumed by _execute_spread_bg) ─────────
    verdict     = entry_gates['verdict']
    grade_label = {'ENTER': 'ENTER', 'ENTER_REDUCED': 'ENTER(R)', 'SKIP': 'SKIP'}.get(verdict, verdict)
    trade = {
        'qty':           qty,
        'net_debit':     net_debit,
        'expiry':        expiry,
        'dte':           dte,
        'long_strike':   long_strike,
        'short_strike':  short_strike,
        'underlying':    underlying,
        'iv_rank':       iv_rank,
        'iv_pct':        current_iv,
        'grade':         grade_label,
        'template':      'EM-Anchored',
        'delta_long':    lq.get('delta'),
        'catalyst_id':   catalyst_id,
        'catalyst_days': catalyst_days,
    }

    result = {
        'strategy':     'BULL_SPREAD',
        'symbol':       sym,
        'underlying':   underlying,
        'expiry':       expiry,
        'dte':          dte,
        'iv_rank':      iv_rank,
        'current_iv':   current_iv,
        'hv30':         hv30,
        'vol_analysis': vol_analysis,
        'em':           em,
        'long_strike':  long_strike,
        'short_strike': short_strike,
        'net_debit':    net_debit,
        'net_debit_$':  net_debit_dollar,
        'max_profit_$': max_profit_dollar,
        'max_loss_$':   max_loss_dollar,
        'breakeven':    breakeven,
        'breakeven_pct': breakeven_pct,
        'long_ba':      long_ba,
        'short_ba':     short_ba,
        'entry_gates':  entry_gates,
        'net_greeks':   net_greeks,
        'velocity':     velocity,
        'mc_ev':        mc_ev,
        'momentum_5d':  momentum_5d,
        'above_200':    above_200,
        'conviction':   {'tier': tier, 'direction': direction, 'signals': signal_count, 'narrative': narrative},
        'catalyst_days': catalyst_days,
        'catalyst_id':  catalyst_id,
        'qty':          qty,
        'trade':        trade,
    }

    # Persist to KB (non-blocking — errors should never kill the calculator)
    try:
        calc_log_id = log_calc_run(result)
        result['calc_log_id'] = calc_log_id
    except Exception:
        pass

    return result


# ── Format calculator output for Telegram ────────────────────────────────────

def format_calc_message(calc: dict) -> str:
    sym      = calc['symbol']
    und      = calc['underlying']
    strategy = calc.get('strategy', 'BULL_SPREAD')
    iv   = calc.get('current_iv') or calc.get('iv_rank') or 0
    hv30 = calc.get('hv30')
    vol  = calc.get('vol_analysis') or {}
    em   = calc.get('em')
    gs   = calc.get('entry_gates') or {}
    gr   = calc.get('net_greeks')  or {}
    vel  = calc.get('velocity')    or {}
    mc   = calc.get('mc_ev')       or {}
    con  = calc.get('conviction')  or {}
    dte  = calc.get('dte', 0)

    verdict    = gs.get('verdict', 'SKIP')
    gates_pass = gs.get('gates_pass', 0)
    ls         = calc.get('long_strike')
    ss         = calc.get('short_strike')
    nd         = calc.get('net_debit', 0)
    nd_dol     = calc.get('net_debit_$', 0)
    mp_dol     = calc.get('max_profit_$', 0)
    be         = calc.get('breakeven', 0)
    be_pct     = calc.get('breakeven_pct', 0)
    qty        = calc.get('qty', 1)

    # ── Expiry label ──────────────────────────────────────────────────────
    try:
        exp_fmt = f"{calc['expiry'][4:6]}/{calc['expiry'][6:]}"   # YYYYMMDD → MM/DD
    except Exception:
        exp_fmt = calc.get('expiry', '?')

    # ── Volatility edge block ─────────────────────────────────────────────
    edge_pts = vol.get('edge_pts')
    vol_vtd  = vol.get('verdict', 'UNKNOWN')
    vol_icon = '✅' if vol.get('gate_pass') else '❌'
    iv_s    = f"{iv:.1f}%"   if iv   is not None else "n/a"
    hv30_s  = f"{hv30:.1f}%" if hv30 is not None else "n/a"
    edge_s  = f"{edge_pts:+.1f}pts " if edge_pts is not None else ""
    edge_str = f"IV: {iv_s} | HV30: {hv30_s} | {edge_s}{vol_vtd} {vol_icon}"

    if em and und:
        em_lo = round(und - em, 2)
        em_hi = round(und + em, 2)
        em_str = f"1-SD move ({dte}d): +/-${em:.2f} → ${em_lo}–${em_hi}"
    else:
        em_str = ""

    # ── Conviction block ──────────────────────────────────────────────────
    tier      = con.get('tier', '?')
    direction = con.get('direction', '?')
    sig_count = con.get('signals', 0)
    narrative = con.get('narrative', '')
    tier_icon = '🔥' if tier == 'HIGH' else ('⚠️' if tier == 'MEDIUM' else '❄️')
    con_str   = f"{tier_icon} {tier} · {sig_count} signals · {direction}"
    if narrative:
        con_str += f" — {narrative[:40]}"

    tech_str     = "✅ Above 200MA" if calc.get('above_200') else "❌ Below 200MA"
    mom_5d       = calc.get('momentum_5d')
    mom_str      = (f"✅ +{mom_5d:.1f}% (5d)" if (mom_5d or 0) >= 1.0
                    else (f"❌ {mom_5d:.1f}% (5d)" if mom_5d is not None else "❌ n/a"))

    # ── Gate summary ──────────────────────────────────────────────────────
    if verdict == 'ENTER':
        gate_verdict = f"*{gates_pass}/5 → ENTER full size*"
    elif verdict == 'ENTER_REDUCED':
        gate_verdict = f"*{gates_pass}/5 → PROCEED (half size)*"
    else:
        gate_verdict = f"*{gates_pass}/5 → SKIP*"
        failed = []
        if not gs.get('vol'):        failed.append("Volatility")
        if not gs.get('tech'):       failed.append("Technical")
        if not gs.get('conviction'): failed.append("Conviction")
        if not gs.get('liquidity'):  failed.append("Liquidity")
        if not gs.get('momentum'):   failed.append("Momentum")

    # ── Build message ─────────────────────────────────────────────────────
    strat_label  = "LEAP Analysis" if strategy == 'LEAP' else "Bull Spread Analysis"
    comparison   = calc.get('_comparison', '')
    auto_qty_note = calc.get('_auto_qty_note', '')
    lines = [
        f"📊 *{sym} — {strat_label}*",
    ]
    if comparison:
        lines.append(f"_{comparison}_")
    if auto_qty_note:
        lines.append(f"📦 _{auto_qty_note}_")
    lines += [
        "VOLATILITY EDGE",
        edge_str,
    ]
    if em_str:
        lines.append(em_str)

    lines += [
        "",
        "CONVICTION",
        con_str,
        f"Tech: {tech_str}",
        f"Momentum: {mom_str}",
        "",
        f"ENTRY GATE: {gate_verdict}",
    ]

    if verdict == 'SKIP':
        if failed:
            lines.append(f"Fails: {', '.join(failed)}")
        lines += ["", "No trade recommended — keep watching."]
        return "\n".join(lines)

    # ── Trade details (ENTER / ENTER_REDUCED) ─────────────────────────────
    stop_dol = round(nd_dol * 0.50, 0)
    nd_str  = f"Δ {gr['net_delta']:+.3f}" if gr.get('net_delta') is not None else "Δ n/a"
    th_str  = f"θ ${gr['net_theta']:.2f}/day" if gr.get('net_theta') is not None else "θ n/a"
    vg_str  = f"ν ${gr['net_vega']:.0f}/IV pt" if gr.get('net_vega') is not None else "ν n/a"

    if strategy == 'LEAP':
        strike   = calc.get('strike', 0)
        liq_warn = "" if calc.get('long_ba', 1) <= 0.60 else " ⚠️ wide spread"
        tgt_dol  = round(nd_dol * 1.00, 0)    # target: 100% gain (double debit)
        lines += [
            "",
            "━━━━━━━━━━━━━━━━━━━",
            "*RECOMMENDED TRADE*",
            f"${strike} LEAP Call · {exp_fmt} ({dte}d){liq_warn}",
            f"Cost: ${nd:.2f}/sh · ${nd_dol:.0f}/contract",
            f"Breakeven: ${be:.2f} (+{be_pct:.1f}% from now)",
            "",
            "GREEKS",
        ]
    else:
        liq_warn = "" if (calc.get('long_ba', 1) <= 0.30 and calc.get('short_ba', 1) <= 0.30) \
                   else " ⚠️ wide spread"
        tgt_dol  = round(mp_dol * 0.50, 0)
        lines += [
            "",
            "━━━━━━━━━━━━━━━━━━━",
            "*RECOMMENDED TRADE*",
            f"${ls}/{ss} Call Spread · {exp_fmt} ({dte}d){liq_warn}",
            f"Debit: ${nd:.2f}/sh · ${nd_dol:.0f}/contract",
            f"Breakeven: ${be:.2f} (+{be_pct:.1f}%)",
            "",
            "GREEKS (net spread)",
        ]

    lines.append(f"{nd_str} · {th_str} · {vg_str}")

    if vel.get('required_pct_day') is not None and vel.get('hv30_daily') is not None:
        ach_icon = "✅" if vel.get('achievable') else "⚠️"
        lines.append(
            f"Velocity: {vel['required_pct_day']:.2f}%/d needed "
            f"| HV30 avg {vel['hv30_daily']:.2f}%/d {ach_icon}"
        )

    if mc.get('ev_dollar') is not None:
        ev_sign = "+" if mc['ev_dollar'] >= 0 else ""
        win_rate = mc.get('win_rate') or 0
        lines += [
            "",
            "MONTE CARLO EV (10K paths, managed)",
            f"EV: {ev_sign}${mc['ev_dollar']:.0f}/contract · Win: {win_rate:.0f}%",
        ]

    time_rule = "18-month hold or 50% gain" if strategy == 'LEAP' else "21 DTE"
    lines += [
        "",
        "EXIT RULES",
        f"Target: ${tgt_dol:.0f} ({'100% gain' if strategy == 'LEAP' else '50% max profit'})",
        f"Stop:   ${stop_dol:.0f} (50% of debit)",
        f"Time:   {time_rule}",
        "",
        "━━━━━━━━━━━━━━━━━━━",
        "Reply *CONFIRM* to place · *SKIP* to cancel",
        "_(timeout in 30 min)_",
    ]
    return "\n".join(lines)


# ── Order execution (background thread) ──────────────────────────────────────

def _execute_spread_bg(sym: str, tmpl: dict, chat_id: str,
                       calc_log_id: int | None = None,
                       sug_id: int | None = None):
    """Place a spread combo order with incremental limit, runs in thread."""
    qty      = tmpl['qty']
    mid      = tmpl['net_debit']
    expiry   = tmpl['expiry']

    order_id = None
    filled_at = None
    for attempt in range(MAX_FILL_TRIES):
        limit_price = round(mid + attempt * 0.05, 2)
        if attempt > 0:
            send_telegram(
                f"⏳ *{sym} spread* — attempt {attempt+1}/{MAX_FILL_TRIES} at ${limit_price:.2f}",
                chat_id,
            )
        payload = {
            'symbol':       sym,
            'expiry':       expiry,
            'strike':       tmpl['long_strike'],
            'right':        'C',
            'qty':          qty,
            'action':       'BUY',
            'order_type':   'LIMIT',
            'limit_price':  limit_price,
            'short_strike': tmpl['short_strike'],
            'net_debit':    limit_price,
        }
        resp = bridge_post('/options/order', payload)
        if not resp or 'orderId' not in resp:
            err = resp.get('error', 'unknown error') if resp else 'bridge unreachable'
            send_telegram(f"❌ *{sym} order failed*: {err}", chat_id)
            return
        order_id = resp['orderId']

        time.sleep(FILL_WAIT_SEC)

        status = get_order_status(order_id)
        if status and status.get('filled', 0) >= qty:
            filled_at = limit_price
            break

        # Paper account: BAG fill simulation often stays at Submitted — treat as filled
        if status and status.get('status') in ('Submitted', 'PreSubmitted') and _is_paper():
            filled_at = limit_price   # paper fill at limit
            break

        # Not filled yet — cancel before trying next increment
        if attempt < MAX_FILL_TRIES - 1:
            cancel_all_orders()
            time.sleep(2)

    if filled_at is None:
        cancel_all_orders()
        send_telegram(
            f"❌ *{sym} SPREAD order cancelled*\n"
            f"Not filled within ${MAX_SLIPPAGE:.2f} slippage from mid (${mid:.2f})",
            chat_id,
        )
        return

    # ── Log trade to DB ──
    premium_dollar = round(filled_at * 100 * qty, 2)
    max_profit_dollar = round((tmpl['short_strike'] - tmpl['long_strike'] - filled_at) * 100 * qty, 2)
    trade_id = log_options_trade(
        strategy='BULL_SPREAD',
        symbol=sym,
        cap_type=cap_type(sym),
        underlying_price=tmpl.get('underlying'),
        expiry=expiry,
        contracts=qty,
        delta_entry=tmpl.get('delta_long'),
        iv_rank_entry=tmpl.get('iv_rank'),
        iv_pct_entry=tmpl.get('iv_pct'),
        premium_paid=premium_dollar,
        max_profit=max_profit_dollar,
        max_loss=premium_dollar,
        entry_grade=tmpl['grade'],
        entry_thesis=f"{tmpl['template']} spread — {tmpl['dte']}d exp",
        long_strike=tmpl['long_strike'],
        short_strike=tmpl['short_strike'],
        right='C',
        net_debit=filled_at,
        catalyst_id=tmpl.get('catalyst_id'),
        days_to_catalyst=tmpl.get('catalyst_days'),
    )

    # Link trade_id to KB calc log + suggestion rows
    try:
        if calc_log_id:
            update_calc_action(calc_log_id, 'CONFIRM', trade_id)
        if sug_id:
            update_suggestion_decision(sug_id, 'CONFIRMED', trade_id)
    except Exception:
        pass

    send_telegram(
        f"✅ *{sym} SPREAD entered* [trade #{trade_id}]\n"
        f"Template: {tmpl['template']} [{tmpl['grade']}]\n"
        f"${tmpl['long_strike']}/${tmpl['short_strike']} call spread · {tmpl['dte']}d\n"
        f"Fill: ${filled_at:.2f}/contract · {qty} lot(s) = ${premium_dollar:.0f} deployed\n"
        f"Max profit: ${max_profit_dollar:.0f} | Stop: ${premium_dollar*0.5:.0f}",
        chat_id,
    )

    _check_learning_milestone(chat_id)


def _execute_leap_bg(sym: str, leap: dict, chat_id: str,
                     calc_log_id: int | None = None, sug_id: int | None = None):
    """Place a LEAP single-leg order with incremental limit, runs in thread."""
    qty  = leap['qty']
    mid  = leap['net_debit']

    order_id  = None
    filled_at = None
    for attempt in range(MAX_FILL_TRIES):
        limit_price = round(mid + attempt * 0.05, 2)
        if attempt > 0:
            send_telegram(
                f"⏳ *{sym} LEAP* — attempt {attempt+1}/{MAX_FILL_TRIES} at ${limit_price:.2f}",
                chat_id,
            )
        payload = {
            'symbol':      sym,
            'expiry':      leap['expiry'],
            'strike':      leap['strike'],
            'right':       'C',
            'qty':         qty,
            'action':      'BUY',
            'order_type':  'LIMIT',
            'limit_price': limit_price,
        }
        resp = bridge_post('/options/order', payload)
        if not resp or 'orderId' not in resp:
            send_telegram(f"❌ *{sym} LEAP order failed* (attempt {attempt+1})", chat_id)
            return
        order_id = resp['orderId']

        time.sleep(FILL_WAIT_SEC)

        status = get_order_status(order_id)
        if status and status.get('filled', 0) >= qty:
            filled_at = limit_price
            break

        if attempt < MAX_FILL_TRIES - 1:
            cancel_all_orders()
            time.sleep(2)

    if filled_at is None:
        cancel_all_orders()
        send_telegram(
            f"❌ *{sym} LEAP order cancelled*\n"
            f"Not filled within ${MAX_SLIPPAGE:.2f} slippage from mid (${mid:.2f})",
            chat_id,
        )
        return

    # ── Log trade to DB ──
    premium_dollar = round(filled_at * 100 * qty, 2)
    delta_val = leap.get('delta_long') or leap.get('delta')
    trade_id = log_options_trade(
        strategy='LEAP',
        symbol=sym,
        cap_type=cap_type(sym),
        underlying_price=leap.get('underlying'),
        expiry=leap['expiry'],
        contracts=qty,
        delta_entry=delta_val,
        iv_rank_entry=leap.get('iv_rank'),
        iv_pct_entry=leap.get('iv_pct'),
        premium_paid=premium_dollar,
        max_profit=None,
        max_loss=premium_dollar,
        entry_grade=leap['grade'],
        entry_thesis=f"LEAP {leap['dte']}d · delta {delta_val or '?'}",
        strike=leap['strike'],
        right='C',
        catalyst_id=leap.get('catalyst_id'),
        days_to_catalyst=leap.get('catalyst_days'),
    )

    # Link trade_id to KB calc log + suggestion rows
    try:
        if calc_log_id:
            update_calc_action(calc_log_id, 'CONFIRM', trade_id)
        if sug_id:
            update_suggestion_decision(sug_id, 'CONFIRMED', trade_id)
    except Exception:
        pass

    hard_stop = round(premium_dollar * 0.60, 2)
    send_telegram(
        f"✅ *{sym} LEAP entered* [trade #{trade_id}]\n"
        f"${leap['strike']} call · {leap['dte']}d exp · {qty} contract(s)\n"
        f"Fill: ${filled_at:.2f} = ${premium_dollar:.0f} deployed\n"
        f"Delta: {delta_val or '?'} | Grade: {leap['grade']}\n"
        f"Hard stop: ${hard_stop:.0f} (-40%)",
        chat_id,
    )

    _check_learning_milestone(chat_id)


def _execute_close_bg(trade: dict, chat_id: str):
    """Close an open trade (spread or LEAP) via market-aggressive limit."""
    sym   = trade['symbol']
    tid   = trade['id']
    strat = trade['strategy']
    qty   = trade['contracts']

    # Fetch current value to set initial limit
    if strat == 'BULL_SPREAD':
        lq = get_quote(sym, trade['expiry'], trade['long_strike'],  trade['right'])
        sq = get_quote(sym, trade['expiry'], trade['short_strike'], trade['right'])
        if not lq or not sq:
            send_telegram(f"❌ Cannot fetch quotes for {sym} to close", chat_id)
            return
        long_mid  = (lq['bid'] + lq['ask']) / 2
        short_mid = (sq['bid'] + sq['ask']) / 2
        spread_mid = round(long_mid - short_mid, 2)
        # Close a bull spread = sell it back
        payload = {
            'symbol':       sym,
            'expiry':       trade['expiry'],
            'strike':       trade['long_strike'],
            'right':        trade['right'],
            'qty':          qty,
            'action':       'SELL',
            'order_type':   'LIMIT',
            'limit_price':  round(spread_mid - 0.05, 2),  # slight discount to fill fast
            'short_strike': trade['short_strike'],
            'net_debit':    round(-(spread_mid - 0.05), 2),  # negative = credit
        }
        exit_value = round(spread_mid * 100 * qty, 2)
    else:  # LEAP
        q = get_quote(sym, trade['expiry'], trade['strike'], trade['right'])
        if not q:
            send_telegram(f"❌ Cannot fetch quote for {sym} LEAP to close", chat_id)
            return
        mid = round((q['bid'] + q['ask']) / 2, 2)
        payload = {
            'symbol':      sym,
            'expiry':      trade['expiry'],
            'strike':      trade['strike'],
            'right':       trade['right'],
            'qty':         qty,
            'action':      'SELL',
            'order_type':  'LIMIT',
            'limit_price': round(mid - 0.05, 2),
        }
        exit_value = round(mid * 100 * qty, 2)

    resp = bridge_post('/options/order', payload)
    if not resp or 'orderId' not in resp:
        send_telegram(f"❌ Close order failed for {sym}", chat_id)
        return

    order_id = resp['orderId']
    time.sleep(FILL_WAIT_SEC)

    status = get_order_status(order_id)
    filled = status and status.get('filled', 0) >= qty

    return_pct = close_options_trade(tid, exit_value, exit_reason='MANUAL') or 0
    sign       = '+' if return_pct >= 0 else ''

    # KB: log actual outcome vs predicted EV
    try:
        from database import get_connection as _gc, DB_PATH as _dbp
        import sqlite3 as _sq
        _conn = _sq.connect(_dbp)
        _cur  = _conn.cursor()
        _cur.execute('''SELECT id, mc_ev_dollar, mc_win_rate
                        FROM opt_calc_log WHERE trade_id=? LIMIT 1''', (tid,))
        _row = _cur.fetchone()
        _cur.execute('''SELECT entry_date FROM options_trades WHERE id=?''', (tid,))
        _erow = _cur.fetchone()
        _conn.close()
        if _row:
            from datetime import date as _date
            _entry_date = _erow[0] if _erow else None
            _days = ((_date.today() - _date.fromisoformat(_entry_date)).days
                     if _entry_date else 0)
            _actual_pnl = exit_value - (_row[1] or 0)
            log_trade_outcome(
                trade_id=tid, calc_log_id=_row[0],
                predicted_ev=_row[1], predicted_wr=_row[2],
                actual_pnl=round(exit_value, 2),
                exit_reason='MANUAL',
                days_held=_days,
            )
    except Exception:
        pass

    send_telegram(
        f"{'✅' if filled else '⚠️'} *{sym} {strat} closed* [trade #{tid}]\n"
        f"Exit value: ${exit_value:.0f} | Return: {sign}{return_pct:.1f}%\n"
        f"{'Order filled' if filled else 'Order placed — check IBKR for fill confirmation'}",
        chat_id,
    )


# ── Learning loop ─────────────────────────────────────────────────────────────

def _check_learning_milestone(chat_id: str):
    cnt = get_closed_options_count()
    if cnt < 20:
        return
    if cnt == 20 or (cnt > 20 and (cnt - 20) % 10 == 0):
        _run_learning_analysis(chat_id)


def _run_learning_analysis(chat_id: str):
    trades = get_options_learning_data()
    if len(trades) < 5:
        return

    total  = len(trades)
    wins   = sum(1 for t in trades if (t['return_pct'] or 0) > 0)
    wr     = round(wins / total * 100, 1)
    avg_r  = round(sum(t['return_pct'] or 0 for t in trades) / total, 1)

    # By grade
    grade_groups: dict[str, list] = {}
    for t in trades:
        g = t['entry_grade'] or 'C'
        grade_groups.setdefault(g, []).append(t['return_pct'] or 0)
    grade_lines = []
    for g in ['A+', 'A', 'B', 'C']:
        rs = grade_groups.get(g, [])
        if rs:
            grade_lines.append(f"   {g}: {len(rs)} trades · avg {sum(rs)/len(rs):+.1f}%")

    # By IV rank bucket
    iv_buckets = {'<25': [], '25-35': [], '>35': []}
    for t in trades:
        iv = t['iv_rank_entry'] or 0
        if iv < 25:
            iv_buckets['<25'].append(t['return_pct'] or 0)
        elif iv < 35:
            iv_buckets['25-35'].append(t['return_pct'] or 0)
        else:
            iv_buckets['>35'].append(t['return_pct'] or 0)
    iv_lines = []
    for bucket, rs in iv_buckets.items():
        if rs:
            iv_lines.append(f"   IV {bucket}%: {len(rs)} trades · avg {sum(rs)/len(rs):+.1f}%")

    # Exit reasons
    exit_reasons: dict[str, int] = {}
    for t in trades:
        er = t['exit_reason'] or 'UNKNOWN'
        exit_reasons[er] = exit_reasons.get(er, 0) + 1

    lines = [
        f"🧠 *Learning Report — {total} closed trades*",
        f"Win rate: {wr}% | Avg return: {avg_r:+.1f}%",
        "",
        "*By grade:*",
    ] + grade_lines + [
        "",
        "*By IV rank at entry:*",
    ] + iv_lines + [
        "",
        "*Exit reasons:*",
    ] + [f"   {k}: {v}" for k, v in sorted(exit_reasons.items(), key=lambda x: -x[1])]

    if total < 50:
        lines.append("\n⚠️ Advisory only — no auto-threshold changes until 50+ trades")

    send_telegram("\n".join(lines), chat_id)


# ── Command handlers ──────────────────────────────────────────────────────────

def cmd_buy(sym: str, qty: int, chat_id: str):
    # Guard: warn if an existing position for this symbol expires within 7 days
    existing = [t for t in get_open_options_trades() if t['symbol'] == sym.upper()]
    for t in existing:
        dte = days_to_expiry(t['expiry'])
        if dte <= 7:
            send_telegram(
                f"⚠️ *{sym} has an open {t['strategy']} expiring in {dte}d*\n"
                f"Use `OPT CLOSE {sym}` to exit before opening a new position.",
                chat_id,
            )
            return

    if not check_circuit_breaker(chat_id):
        return
    if not can_open_position(chat_id=chat_id):
        return

    liq_note = session_liquidity_note()
    cs = capital_status()
    send_telegram(
        f"⏳ Running SPREAD vs LEAP analysis for *{sym}*..."
        + (f"\n{liq_note}" if liq_note else "")
        + f"\n_Capital: ${cs['available']:.0f} available · {cs['slots_free']} slot(s) free_",
        chat_id,
    )
    calc = run_strategy_comparison(sym, qty)
    if 'error' in calc:
        send_telegram(f"❌ {calc['error']}", chat_id)
        return

    calc = _auto_qty_calc(calc)
    qty  = calc['qty']   # use auto-scaled qty for pending entry

    msg = format_calc_message(calc)
    send_telegram(msg, chat_id)

    # Only add to pending if verdict allows trading (ENTER or ENTER_REDUCED)
    verdict = (calc.get('entry_gates') or {}).get('verdict', 'SKIP')
    if verdict in ('ENTER', 'ENTER_REDUCED'):
        _pending[chat_id] = {
            'action':     'spread_confirm',
            'symbol':     sym,
            'qty':        qty,
            'calc':       calc,
            'expires_at': datetime.now() + timedelta(minutes=30),
        }


def cmd_status(chat_id: str):
    trades  = get_open_options_trades()
    closed  = get_closed_options_count()
    cats    = get_upcoming_catalysts(days=30)
    paused  = "PAUSED" if _paused else "ACTIVE"
    cs      = capital_status()
    total_pnl = get_options_total_pnl()

    bar_filled = round(cs['deployed'] / cs['total'] * 10) if cs['total'] else 0
    cap_bar    = '█' * bar_filled + '░' * (10 - bar_filled)

    lines = [f"📊 *Options Status — {paused}*\n"]
    lines.append(
        f"Capital: [{cap_bar}] ${cs['deployed']:.0f} deployed / ${cs['available']:.0f} free\n"
        f"Slots: {cs['slots_used']}/{MAX_OPTIONS_POSITIONS} | "
        f"Closed: {closed} | Net P&L: ${total_pnl:+.0f}"
    )

    if trades:
        lines.append("")
        for t in trades:
            prem = t['premium_paid'] or 0
            stop = t['stop_value']
            dte  = days_to_expiry(t['expiry'])
            lines.append(
                f"• *{t['symbol']}* [{t['strategy']}] {t['entry_grade']} — "
                f"${prem:.0f} in | stop {'${:.0f}'.format(stop) if stop is not None else 'n/a'} | {dte}d"
            )
    else:
        lines.append("\nNo open positions — capital ready to deploy.")

    if cats:
        lines.append(f"\n*Upcoming catalysts:*")
        for c in cats[:5]:
            lines.append(f"• {c['symbol']} — {c['name']} ({c['date']}) [{c['confidence']}]")

    send_telegram("\n".join(lines), chat_id)


def cmd_positions(chat_id: str):
    trades = get_open_options_trades()
    if not trades:
        send_telegram("No open options positions.", chat_id)
        return
    lines = [f"📋 *Positions ({len(trades)} open)*\n"]
    for t in trades:
        prem = t['premium_paid'] or 0
        stop = t['stop_value']
        dte  = days_to_expiry(t['expiry'])
        stage_map = {1: 'hard', 2: 'breakeven', 3: 'trail'}
        stage_lbl = stage_map.get(t['stop_stage'], '?')
        if t['strategy'] == 'BULL_SPREAD':
            leg_str = f"${t['long_strike']}/${t['short_strike']} spread"
        else:
            leg_str = f"${t['strike']} LEAP"
        expiry_flag = " ⚠️ EXPIRING SOON" if dte <= 7 else ""
        lines += [
            f"*{t['symbol']}* [{t['entry_grade']}] — {leg_str}{expiry_flag}",
            f"  Exp: {t['expiry']} ({dte}d) | Entry: ${prem:.0f}",
            f"  Stop: {'${:.0f}'.format(stop) if stop is not None else 'n/a'} ({stage_lbl}) | Δ: {t['delta_entry'] or '?'}",
            f"  IV@entry: {t['iv_rank_entry'] or '?'}%",
            "",
        ]
    send_telegram("\n".join(lines), chat_id)


def cmd_calendar(chat_id: str):
    cats = get_upcoming_catalysts(days=30)
    if not cats:
        send_telegram("No upcoming catalysts in the next 30 days.", chat_id)
        return
    lines = ["📅 *Catalyst Calendar (30d)*\n"]
    for c in cats:
        lines.append(
            f"• *{c['symbol']}* — {c['name']}\n"
            f"  {c['date']} | {c['type']} | {c['confidence']}"
            + (f" | move ~{c['expected_move']}%" if c['expected_move'] else "")
        )
    send_telegram("\n".join(lines), chat_id)


def cmd_add(args: list[str], chat_id: str):
    # args: [sym, date, note, confidence?]
    if len(args) < 3:
        send_telegram(
            "Usage: `OPT ADD PLTR 2026-06-15 \"event note\" [HIGH|MEDIUM|LOW]`",
            chat_id,
        )
        return
    sym        = args[0].upper()
    event_date = args[1]
    note       = args[2]
    confidence = args[3].upper() if len(args) > 3 else 'MEDIUM'
    if confidence not in ('HIGH', 'MEDIUM', 'LOW'):
        confidence = 'MEDIUM'

    # Validate date format
    try:
        datetime.strptime(event_date, '%Y-%m-%d')
    except ValueError:
        send_telegram(f"❌ Invalid date format: `{event_date}` — use YYYY-MM-DD", chat_id)
        return

    iv_data = get_iv_rank(sym)
    iv_rank = iv_data.get('iv_rank') if iv_data else None

    cat_id = add_catalyst(
        symbol=sym,
        catalyst_type='DOMAIN_INSIGHT',
        event_name=note,
        event_date=event_date,
        confidence=confidence,
        iv_rank_when_noted=iv_rank,
        news_source='MANUAL',
    )
    send_telegram(
        f"✅ Catalyst added [#{cat_id}]\n"
        f"*{sym}* — {note}\n"
        f"Date: {event_date} | Confidence: {confidence}",
        chat_id,
    )


def cmd_news(sym: str | None, chat_id: str):
    """OPT NEWS — conviction leaderboard. OPT NEWS <sym> — per-ticker detail."""
    now_et = datetime.now(ZoneInfo('America/New_York')).strftime('%H:%M ET')

    if sym:
        # Per-ticker detail
        detail = get_conviction_detail(sym)
        if not detail:
            send_telegram(
                f"No conviction data for {sym} yet.\n"
                f"news_engine scans every 15 min during market hours.", chat_id)
            return

        dir_arrow = {'BULL': '↑', 'BEAR': '↓', 'MIXED': '↕', 'NEUTRAL': '—'}.get(detail['direction'], '')
        tier_emoji = {'HIGH': '🔥', 'MEDIUM': '⚡', 'LOW': '💤'}.get(detail['tier'], '')

        ivr = detail.get('iv_rank')
        ivr_str = (f"IVR {ivr:.0f}% — " + ("cheap ✅" if ivr < 30 else "moderate" if ivr < 45 else "expensive ⚠️")) if ivr is not None else "IVR n/a"

        lines = [
            f"📡 *{sym} {dir_arrow} — {now_et}*",
            f"Tier: {tier_emoji} {detail['tier']} | {ivr_str} "
            f"| {detail['signal_count']} signals ({detail['high_count']} HIGH)",
            f"Sources: {_md(detail['sources']) or 'none'}",
        ]
        if detail.get('narrative'):
            lines += ['', _md(detail['narrative'])]

        signals = detail.get('signals', [])
        if signals:
            lines += ['', '*Signals (last 5 days):*']
            rel_icon = {'HIGH': '🔴', 'MEDIUM': '🟡'}
            for s in signals:
                ts  = s['created_at'][11:16] if len(s['created_at']) > 11 else ''
                ico = rel_icon.get(s['relevance'], '⚪️')
                lines.append(
                    f"{ico} [{ts}] {_md(s['source'])} — {_md(s['news_type'])} — {s['relevance']}\n"
                    f"  {_md(s['headline'][:85])}\n"
                    f"  ↳ {_md(s['one_line_reason'] or s['direction'])}"
                )

        lines += ['', f"OPT BUY {sym} → calculator"]
        send_telegram('\n'.join(lines), chat_id)
        return

    # Leaderboard — all tickers ranked by score
    board = get_conviction_leaderboard()
    if not board:
        send_telegram(
            "No conviction data yet — news_engine builds this over time.\n"
            "It runs every 15 min during market hours. Check back after the next scan.", chat_id)
        return

    high_rows   = [r for r in board if r['tier'] == 'HIGH']
    medium_rows = [r for r in board if r['tier'] == 'MEDIUM']
    show_high   = high_rows[:7]
    show_medium = medium_rows[:3]

    # Build monospace table inside a code block
    # Columns: Sym(5) Dir(1) Sigs(4) H(2) IVR(3) Age Rec(4)
    HDR = f"{'Sym':<5} {'':1} {'Sigs':>4} {'H':>2}  {'IVR':>3}  {'Age':<5} Rec"
    SEP = '─' * 34

    def _ivr(row):
        v = row.get('iv_rank')
        return f"{v:>3.0f}" if v is not None else ' --'

    def _rec(row):
        d  = row.get('direction', '')
        iv = row.get('iv_rank')
        h  = row.get('high_count', 0)
        if iv is None:
            return '?   '
        if d == 'MIXED':
            return 'OBS '
        if d == 'BEAR':
            return 'SKIP' if iv > 50 else 'PUT '
        # BULL
        if iv < 30:
            return 'ACT!' if h >= 4 else 'LEAP'
        if iv < 45:
            return 'SPR '
        if iv < 60:
            return 'WAIT'
        return 'SKIP'

    tbl = [HDR, SEP]
    if show_high:
        for r in show_high:
            arrow = {'BULL': '↑', 'BEAR': '↓', 'MIXED': '~'}.get(r['direction'], ' ')
            ago   = _signal_age(r['last_signal_at']).replace(' ago', '')
            tbl.append(f"{r['symbol']:<5} {arrow} {r['signal_count']:>4} {r['high_count']:>2}  {_ivr(r)}  {ago:<5} {_rec(r)}")

    if show_medium:
        tbl.append(SEP)
        for r in show_medium:
            arrow = {'BULL': '↑', 'BEAR': '↓', 'MIXED': '~'}.get(r['direction'], ' ')
            ago   = _signal_age(r['last_signal_at']).replace(' ago', '')
            tbl.append(f"{r['symbol']:<5} {arrow} {r['signal_count']:>4} {r['high_count']:>2}  {_ivr(r)}  {ago:<5} {_rec(r)}")

    table_block = '```\n' + '\n'.join(tbl) + '\n```'

    tier_label = ''
    if show_high and show_medium:
        tier_label = f"🔥 {len(show_high)} HIGH  ⚡ {len(show_medium)} MED"
    elif show_high:
        tier_label = f"🔥 {len(show_high)} HIGH"
    else:
        tier_label = f"⚡ {len(show_medium)} MED"

    tracked = len(board)
    lines = [
        f"📡 *Signal Leaderboard — {now_et}*",
        f"{tier_label}  ({tracked} tracked)",
        table_block,
        "H=HIGH sigs  IVR<30=cheap  >50=exp",
        "ACT=buy · LEAP=cheap IV · SPR=spread · WAIT=IV high · SKIP=too exp · PUT=bear",
        "OPT NEWS <sym> → detail   OPT BUY <sym> → calculator",
    ]
    send_telegram('\n'.join(lines), chat_id)


def _md(text: str | None) -> str:
    """Strip Markdown special chars from dynamic/LLM text to prevent Telegram parse errors."""
    return (text or '').replace('_', ' ').replace('*', '').replace('`', "'").replace('[', '(').replace(']', ')')


def _signal_age(ts: str | None) -> str:
    """Convert ISO timestamp to human-readable age string."""
    if not ts:
        return 'unknown'
    try:
        dt      = datetime.fromisoformat(ts)
        minutes = int((datetime.now() - dt).total_seconds() / 60)
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        return f"{hours // 24}d ago"
    except Exception:
        return 'unknown'


def cmd_close(sym: str, chat_id: str):
    trades = [t for t in get_open_options_trades() if t['symbol'] == sym.upper()]
    if not trades:
        send_telegram(f"No open options position for {sym}.", chat_id)
        return
    trade = trades[0]
    strat = trade['strategy']
    prem  = trade['premium_paid'] or 0
    dte   = days_to_expiry(trade['expiry'])
    send_telegram(
        f"⚠️ *Close {sym} {strat}?*\n"
        f"Entry: ${prem:.0f} | DTE: {dte}d\n"
        f"Reply *YES* to close or *NO* to cancel",
        chat_id,
    )
    _pending[chat_id] = {
        'action':     'close_confirm',
        'symbol':     sym.upper(),
        'trade':      trade,
        'expires_at': datetime.now() + timedelta(minutes=30),
    }


def cmd_pause(chat_id: str):
    global _paused
    _paused = True
    send_telegram("⏸ Options scanning *PAUSED*. Reply `OPT RESUME` to re-enable.", chat_id)


def cmd_resume(chat_id: str):
    global _paused
    _paused = False
    send_telegram("▶️ Options scanning *RESUMED*.", chat_id)


# ── Reply handlers ────────────────────────────────────────────────────────────

def cmd_kb(chat_id: str):
    """Send a compact KB session brief to Telegram."""
    try:
        import importlib.util, os
        spec = importlib.util.spec_from_file_location(
            "kb_report",
            os.path.join(os.path.dirname(__file__), "kb_report.py"),
        )
        kb_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kb_mod)
        from database import get_kb_summary
        kb    = get_kb_summary()
        brief = kb_mod.build_telegram_brief(kb)
        send_telegram(brief, chat_id)
    except Exception as e:
        send_telegram(f"KB error: {e}", chat_id)


def handle_reply(text: str, chat_id: str):
    pending = _pending.get(chat_id)
    if not pending:
        send_telegram(
            "No active calculator session. Send `OPT BUY <sym>` to start a new one.",
            chat_id,
        )
        return

    if pending['expires_at'] < datetime.now():
        del _pending[chat_id]
        send_telegram("⏰ Pending action expired (30 min timeout).", chat_id)
        return

    action = pending['action']

    if action == 'spread_confirm':
        calc = pending['calc']
        sym  = pending['symbol']
        qty  = pending['qty']
        upper = text.upper()
        calc_log_id = calc.get('calc_log_id')
        sug_id      = pending.get('suggestion_id')
        if upper == 'SKIP':
            del _pending[chat_id]
            try:
                if calc_log_id:
                    update_calc_action(calc_log_id, 'SKIP')
                if sug_id:
                    update_suggestion_decision(sug_id, 'SKIPPED')
            except Exception:
                pass
            send_telegram(f"Skipped {sym}. No order placed.", chat_id)
            return
        if upper == 'CONFIRM':
            trade = calc.get('trade')
            if not trade:
                del _pending[chat_id]
                send_telegram(f"❌ Trade data missing for {sym}. Run `OPT BUY {sym}` again.", chat_id)
                return
            new_premium = trade.get('net_debit', 0) * 100 * trade.get('qty', 1)
            if not can_open_position(new_premium, chat_id):
                del _pending[chat_id]
                return
            strategy = calc.get('strategy', 'BULL_SPREAD')
            del _pending[chat_id]
            if strategy == 'LEAP':
                send_telegram(f"⏳ Placing LEAP call for *{sym}*...", chat_id)
                t = threading.Thread(target=_execute_leap_bg,
                                     args=(sym, trade, chat_id, calc_log_id, sug_id), daemon=True)
            else:
                send_telegram(f"⏳ Placing EM-Anchored spread for *{sym}*...", chat_id)
                t = threading.Thread(target=_execute_spread_bg,
                                     args=(sym, trade, chat_id, calc_log_id, sug_id), daemon=True)
            t.start()
            return
        # Unrecognised reply
        send_telegram("Reply *CONFIRM* to place the trade or *SKIP* to cancel.", chat_id)

    elif action == 'close_confirm':
        trade = pending['trade']
        sym   = pending['symbol']
        if text.upper() == 'YES':
            del _pending[chat_id]
            send_telegram(f"⏳ Closing *{sym}*...", chat_id)
            t = threading.Thread(target=_execute_close_bg, args=(trade, chat_id), daemon=True)
            t.start()
        elif text.upper() == 'NO':
            del _pending[chat_id]
            send_telegram(f"Close cancelled for {sym}.", chat_id)


# ── Message dispatcher ────────────────────────────────────────────────────────

def dispatch(text: str, chat_id: str):
    text = text.strip()
    upper = text.upper()

    # OPT commands
    if upper.startswith('OPT '):
        parts = text.split()
        cmd   = parts[1].upper() if len(parts) > 1 else ''

        if cmd == 'STATUS':
            cmd_status(chat_id)
        elif cmd == 'POSITIONS':
            cmd_positions(chat_id)
        elif cmd == 'CALENDAR':
            cmd_calendar(chat_id)
        elif cmd == 'PAUSE':
            cmd_pause(chat_id)
        elif cmd == 'RESUME':
            cmd_resume(chat_id)
        elif cmd == 'CLOSE' and len(parts) > 2:
            cmd_close(parts[2], chat_id)
        elif cmd == 'ADD' and len(parts) > 4:
            cmd_add(parts[2:], chat_id)
        elif cmd == 'BUY' and len(parts) > 2:
            sym = parts[2].upper()
            qty = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
            cmd_buy(sym, qty, chat_id)
        elif cmd == 'NEWS':
            sym = parts[2].upper() if len(parts) > 2 else None
            cmd_news(sym, chat_id)
        elif cmd == 'KB':
            cmd_kb(chat_id)
        else:
            send_telegram(
                "Unknown OPT command. Available:\n"
                "`OPT STATUS` · `OPT POSITIONS` · `OPT CALENDAR`\n"
                "`OPT PAUSE` · `OPT RESUME`\n"
                "`OPT CLOSE <sym>` · `OPT BUY <sym> [qty]`\n"
                "`OPT NEWS [sym]` · `OPT ADD <sym> <date> <note> [HIGH|MEDIUM|LOW]`\n"
                "`OPT KB` — session catch-up brief",
                chat_id,
            )
        return

    # CONFIRM/SKIP (spread entry) and YES/NO (close confirmation) replies
    if upper in ('CONFIRM', 'SKIP', 'YES', 'NO'):
        handle_reply(text, chat_id)
        return

    # Equity commands handled by auto_trader — silently ignore so they don't get double-responses
    EQUITY_COMMANDS = {
        'HELP', 'STATUS', 'REGIME', 'TODAY',
        'PAUSE', 'STOP', 'CANCEL', 'RESUME', 'CLOSEALL',
    }
    first_word = upper.split()[0] if upper.split() else ''
    if first_word in EQUITY_COMMANDS or first_word in ('BUY', 'SELL', 'BLOCK'):
        return

    # Truly unrecognised input — show full command list
    send_telegram(
        "*Equity commands:*\n"
        "`HELP` · `STATUS` · `REGIME` · `TODAY`\n"
        "`BUY <sym> [%]` · `SELL <sym>` · `CLOSEALL`\n"
        "`PAUSE` / `RESUME` · `BLOCK <sym>`\n"
        "\n"
        "*Options commands (OPT prefix):*\n"
        "`OPT STATUS` — portfolio overview\n"
        "`OPT POSITIONS` — per-position Greeks + stop stage\n"
        "`OPT CALENDAR` — upcoming catalysts\n"
        "`OPT NEWS [sym]` — recent HIGH/MEDIUM signals\n"
        "`OPT BUY <sym> [qty]` — spread/LEAP calculator\n"
        "`OPT CLOSE <sym>` — close a position\n"
        "`OPT PAUSE` / `OPT RESUME` — halt/resume scanning\n"
        "`OPT ADD <sym> <date> <note> [HIGH|MEDIUM|LOW]` — manual catalyst\n"
        "`OPT KB` — session catch-up brief (open, closed, MC accuracy, gates)",
        chat_id,
    )


# ── Main loop ─────────────────────────────────────────────────────────────────

def _process_pending_suggestions():
    """
    Called from main loop every iteration.
    Picks up one PENDING suggestion written by news_engine, runs the calculator,
    and sends the CONFIRM/SKIP verdict message. Processes at most one per call
    so we never queue two trades waiting at once.
    """
    OPT_CHAT = os.getenv('OPTIONS_TELEGRAM_CHAT_ID', '')
    if not OPT_CHAT:
        return

    # Don't start a new auto-suggest while one is already waiting for a reply
    if OPT_CHAT in _pending:
        return

    suggestions = get_pending_suggestions()
    if not suggestions:
        return

    sug = suggestions[0]   # oldest first
    sug_id = sug['id']
    sym    = sug['symbol']

    # Mark immediately so next loop iteration skips it
    update_suggestion_status(sug_id, 'PROCESSING')

    if not check_circuit_breaker(OPT_CHAT):
        update_suggestion_status(sug_id, 'NO_TRADE')
        return

    # Capital and slot gate — silently defer if full (will retry on next suggestion cycle)
    if not can_open_position():
        cs = capital_status()
        send_telegram(
            f"📋 *{sym}* queued — {cs['slots_used']}/{MAX_OPTIONS_POSITIONS} slots used, "
            f"${cs['available']:.0f} available. Will evaluate when a position closes.",
            OPT_CHAT,
        )
        update_suggestion_status(sug_id, 'PENDING')   # put it back so we retry
        return

    liq_note = session_liquidity_note()
    cs       = capital_status()
    send_telegram(
        f"📡 *Auto-analysis: {sym}* (HIGH conviction)\n"
        f"⏳ Running SPREAD vs LEAP comparison..."
        + (f"\n{liq_note}" if liq_note else "")
        + f"\n_Capital: ${cs['available']:.0f} available · {cs['slots_free']} slot(s) free_",
        OPT_CHAT,
    )

    calc = run_strategy_comparison(sym, 1)

    if 'error' in calc:
        send_telegram(f"❌ {sym} calculator error: {calc['error']}", OPT_CHAT)
        update_suggestion_status(sug_id, 'ERROR')
        return

    calc        = _auto_qty_calc(calc)
    calc_log_id = calc.get('calc_log_id')
    verdict     = (calc.get('entry_gates') or {}).get('verdict', 'SKIP')
    comparison  = calc.get('_comparison', '')

    # Store calc results on the suggestion row
    try:
        update_suggestion_calc(sug_id, calc, calc_log_id)
    except Exception:
        pass

    if verdict in ('ENTER', 'ENTER_REDUCED'):
        msg = format_calc_message(calc)
        send_telegram(msg, OPT_CHAT)
        _pending[OPT_CHAT] = {
            'action':        'spread_confirm',
            'symbol':        sym,
            'qty':           1,
            'calc':          calc,
            'suggestion_id': sug_id,
            'expires_at':    datetime.now() + timedelta(minutes=30),
        }
        update_suggestion_status(sug_id, 'SENT')
    else:
        # Calculator says SKIP — send a brief note, no CONFIRM prompt
        gs    = calc.get('entry_gates', {})
        mc    = calc.get('mc_ev', {})
        gates = gs.get('gates_pass', 0)
        ev    = mc.get('ev_dollar')
        ev_str = f"MC EV ${ev:+.0f}" if ev is not None else "no EV"
        cmp_note = f"\n_{comparison}_" if comparison else ""
        send_telegram(
            f"📊 *{sym} auto-analysis: {verdict}* ({gates}/5 gates, {ev_str}){cmp_note}\n"
            f"Conviction is HIGH but setup not ready. No action needed.",
            OPT_CHAT,
        )
        update_suggestion_status(sug_id, 'NO_TRADE')


def main():
    global _last_update_id
    print("[options_trader] started — polling Telegram")
    _suggestion_check_counter = 0
    while True:
        try:
            # Purge expired pending entries; mark timed-out suggestions EXPIRED
            now = datetime.now()
            for cid in [c for c, p in list(_pending.items()) if p['expires_at'] < now]:
                sug_id = _pending[cid].get('suggestion_id')
                if sug_id:
                    try:
                        update_suggestion_decision(sug_id, 'EXPIRED')
                    except Exception:
                        pass
                del _pending[cid]

            # Check for new auto-suggest requests every ~30s (3 × 10s sleep)
            _suggestion_check_counter += 1
            if _suggestion_check_counter >= 3:
                _suggestion_check_counter = 0
                try:
                    _process_pending_suggestions()
                except Exception as _se:
                    print(f"[options_trader] suggestion poller error: {_se}")

            updates = poll_telegram()
            for update in updates:
                uid = update['update_id']
                msg = update.get('message') or update.get('edited_message')
                if not msg:
                    _last_update_id = uid   # non-message update, safe to consume
                    continue
                text    = msg.get('text', '').strip()
                chat_id = str(msg['chat']['id'])
                if not text:
                    _last_update_id = uid
                    continue

                upper      = text.upper()
                first_word = upper.split()[0] if upper.split() else ''

                # Equity commands belong to auto_trader — leave them in the queue
                EQUITY_CMDS = {
                    'HELP', 'STATUS', 'REGIME', 'TODAY',
                    'PAUSE', 'STOP', 'CANCEL', 'RESUME', 'CLOSEALL',
                    'BUY', 'SELL', 'BLOCK',
                }
                if not upper.startswith('OPT ') and first_word in EQUITY_CMDS:
                    continue  # don't advance offset — auto_trader will handle this

                _last_update_id = uid   # consume: this is ours to handle
                dispatch(text, chat_id)
        except Exception as e:
            print(f"[options_trader] loop error: {e}")
            traceback.print_exc()
            import sys; sys.stdout.flush(); sys.stderr.flush()
        time.sleep(10)  # short-poll every 10s — avoids blocking auto_trader's Telegram connection


if __name__ == '__main__':
    main()
