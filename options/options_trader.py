"""
options_trader.py — OPT Telegram command handler, bull spread calculator,
                    LEAP evaluator, and order execution.

Run as a standalone process. Long-polls Telegram for OPT* messages and
numbered replies. Zero interaction with equity auto_trader.py.

Standing commands (any time):
  OPT STATUS                         — portfolio overview + all-time stats
  OPT POSITIONS                      — per-position: Greeks, stop stage, DTE
  OPT CALENDAR                       — upcoming catalysts (30-day window)
  OPT PAUSE                          — suspend scanning flags (inform watchman/news)
  OPT RESUME                         — re-enable scanning
  OPT CLOSE <sym>                    — prompt to close open position on <sym>
  OPT ADD <sym> <YYYY-MM-DD> <note> [HIGH|MEDIUM|LOW]  — add DOMAIN_INSIGHT catalyst
  OPT BUY <sym> [qty]                — run calculator, show 4 entry options

Numbered replies after OPT BUY (30-min timeout):
  1 = enter Conservative spread
  2 = enter Balanced spread
  3 = enter Aggressive spread
  4 = enter as LEAP
  5 = skip

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
from database import (
    get_open_options_trades,
    get_upcoming_catalysts,
    add_catalyst,
    log_options_trade,
    close_options_trade,
    get_closed_options_count,
    get_options_learning_data,
    get_recent_news,
    get_news_quality_stats,
    get_conviction_leaderboard,
    get_conviction_detail,
)

load_dotenv()

# ── Config ───────────────────────────────────────────────────────────────────
BRIDGE_URL       = os.getenv('BRIDGE_URL', 'http://127.0.0.1:8000')
TELEGRAM_TOKEN   = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
TG_API           = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

ET               = ZoneInfo('America/New_York')
COMMISSION_RT    = 3.60    # round-trip per spread (2 legs × open + close)
MAX_SLIPPAGE     = 0.15    # max $ above mid before cancel
FILL_WAIT_SEC    = 45      # seconds between fill checks
MAX_FILL_TRIES   = 4       # attempts: mid, mid+.05, mid+.10, mid+.15

MID_CAP_UNIVERSE = {'PLTR', 'RKLB', 'APP', 'HOOD', 'IONQ'}
LARGE_CAP_UNIVERSE = {'NVDA', 'META', 'AMZN', 'MSFT', 'AAPL', 'TSLA', 'AMD', 'GOOGL'}

# ── Global state ──────────────────────────────────────────────────────────────
_paused          = False
_pending: dict   = {}       # chat_id → {action, symbol, qty, templates, leap, expires_at}
_last_update_id  = 0


# ── Telegram helpers ─────────────────────────────────────────────────────────

def send_telegram(message: str, chat_id: str | None = None):
    cid = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_TOKEN or not cid:
        print(f"[TG] {message}")
        return
    try:
        requests.post(
            f"{TG_API}/sendMessage",
            json={'chat_id': cid, 'text': message, 'parse_mode': 'Markdown'},
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


# ── Full calculator ───────────────────────────────────────────────────────────

def run_calculator(symbol: str, qty: int = 1) -> dict:
    """
    For a symbol, fetch chain + IV rank, compute 3 spread templates + 1 LEAP.
    Returns {'iv': {...}, 'templates': [...], 'leap': {...}, 'underlying': float}
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

    # All available expiry dates and strikes
    expiries = [item['expiry'] for item in chain_data['chain']]
    all_strikes = chain_data['chain'][0]['strikes'] if chain_data['chain'] else []

    # Upcoming catalyst for this symbol (for grading + DTE info)
    upcoming     = get_upcoming_catalysts(days=60)
    sym_cats     = [c for c in upcoming if c['symbol'] == sym]
    catalyst_days = None
    catalyst_id   = None
    if sym_cats:
        cat_date      = datetime.strptime(sym_cats[0]['date'], '%Y-%m-%d').date()
        catalyst_days = (cat_date - date.today()).days
        catalyst_id   = sym_cats[0]['id']

    # Domain insight: if we have any DOMAIN_INSIGHT catalyst for this symbol
    domain_insight = any(c.get('type') == 'DOMAIN_INSIGHT' for c in sym_cats)
    above_200      = is_above_200ma(sym)

    # ── 3 Spread Templates ─────────────────────────────────────────────────
    templates = []
    spread_configs = [
        # (name, long_otm_pct, width_pct, dte_min, dte_max)
        ('Conservative', 0.00, 0.07, 21, 28),
        ('Balanced',     0.04, 0.11, 28, 35),
        ('Aggressive',   0.06, 0.17, 35, 45),
    ]

    for name, long_otm, width_pct, dte_min, dte_max in spread_configs:
        expiry = _find_expiry(expiries, dte_min, dte_max)
        if not expiry:
            continue

        target_long  = underlying * (1 + long_otm)
        target_short = underlying * (1 + long_otm + width_pct)
        long_strike  = _find_nearest_strike(all_strikes, target_long)
        short_strike = _find_nearest_strike(all_strikes, target_short)
        if not long_strike or not short_strike or long_strike >= short_strike:
            continue

        tmpl = _calc_template(sym, underlying, expiry, long_strike, short_strike,
                               iv_rank, catalyst_days, name)
        if tmpl:
            tmpl['qty']         = qty
            tmpl['catalyst_id'] = catalyst_id
            tmpl['catalyst_days'] = catalyst_days
            templates.append(tmpl)

    # ── LEAP Template ──────────────────────────────────────────────────────
    leap_data = None
    # LEAP: 18-24 month expiry, delta ~0.65-0.75 (10% OTM or ATM)
    leap_expiry = _find_expiry(expiries, 540, 720)
    if not leap_expiry:
        leap_expiry = _find_expiry(expiries, 360, 540)  # fallback 12-18m

    if leap_expiry:
        target_leap_strike = underlying * 1.05  # slightly OTM
        leap_strike = _find_nearest_strike(all_strikes, target_leap_strike)
        if leap_strike:
            leap_data = _calc_leap(sym, underlying, leap_expiry, leap_strike,
                                   iv_rank, catalyst_days, above_200, domain_insight)
            if leap_data:
                leap_data['qty']          = qty
                leap_data['catalyst_id']  = catalyst_id
                leap_data['catalyst_days'] = catalyst_days

    return {
        'symbol':      sym,
        'underlying':  underlying,
        'iv':          iv_data,
        'templates':   templates,
        'leap':        leap_data,
        'above_200ma': above_200,
    }


# ── Format calculator output for Telegram ────────────────────────────────────

def format_calc_message(calc: dict) -> str:
    sym        = calc['symbol']
    under      = calc['underlying']
    iv_data    = calc.get('iv') or {}
    iv_rank    = iv_data.get('iv_rank',    '?')
    current_iv = iv_data.get('current_iv', '?')

    # IV routing header
    if isinstance(iv_rank, float) and iv_rank > 45:
        routing = "🔴 IV Rank > 45% — *all templates at edge of viable range*"
    elif isinstance(iv_rank, float) and iv_rank < 30:
        routing = "🟢 IV Rank < 30% — LEAP preferred"
    else:
        routing = "🟡 IV Rank 30-45% — Spread preferred"

    lines = [
        f"📊 *{sym} Options Calculator*",
        f"Price: ${under:.2f} | IV Rank: {iv_rank}% | IV: {current_iv}%",
        routing,
        "",
    ]

    for i, t in enumerate(calc['templates'], 1):
        liq  = "✅ liquid" if t['liquidity_ok'] and t['min_debit_ok'] else "⚠️ illiquid"
        warn = "" if t['min_debit_ok'] else " *(debit < $0.80 — skip)*"
        ev_t    = t['ev_net']    if t['ev_net']    is not None else 0
        rr_t    = t['rr_net']   if t['rr_net']    is not None else 0
        pp_t    = t['prob_profit'] if t['prob_profit'] is not None else 0
        pl_t    = t['prob_loss']   if t['prob_loss']   is not None else 0
        lines += [
            f"*{i}. {t['template']} [{t['grade']}]* {warn}",
            f"   {t['dte']}d exp | ${t['long_strike']}/${t['short_strike']} call spread",
            f"   Cost: ${t['net_debit_$']} | Max profit: ${t['max_profit_$']} | Max loss: ${t['max_loss_$']}",
            f"   EV (post-comm): ${ev_t:+.0f} | R:R {rr_t:.1f}x | {liq}",
            f"   Prob profit: {pp_t:.0f}% | Prob loss: {pl_t:.0f}%",
            "",
        ]

    lp = calc.get('leap')
    if lp:
        liq_leap = "✅" if (lp.get('bid_ask_spread') or 1.0) <= 0.40 else "⚠️"
        delta_str = f"{lp['delta']:.2f}" if lp.get('delta') else 'n/a'
        ev_lp     = lp['ev_net'] if lp.get('ev_net') is not None else 0
        lines += [
            f"*4. LEAP [{lp['grade']}]* {liq_leap}",
            f"   {lp['dte']}d exp | ${lp['strike']} call",
            f"   Premium: ${lp['premium_$']} | Delta: {delta_str}",
            f"   EV (est, post-comm): ${ev_lp:+.0f} | 200MA: {'✅' if lp['above_200ma'] else '❌'}",
            "",
        ]
    else:
        lines.append("*4. LEAP* — no 12-24m expiry available")
        lines.append("")

    lines += [
        "Reply: *1/2/3/4* to enter, *5* to skip",
        f"_(timeout in 30 min)_",
    ]
    return "\n".join(lines)


# ── Order execution (background thread) ──────────────────────────────────────

def _execute_spread_bg(sym: str, tmpl: dict, chat_id: str):
    """Place a spread combo order with incremental limit, runs in thread."""
    qty      = tmpl['qty']
    mid      = tmpl['net_debit']
    expiry   = tmpl['expiry']

    order_id = None
    filled_at = None
    for attempt in range(MAX_FILL_TRIES):
        limit_price = round(mid + attempt * 0.05, 2)
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

    send_telegram(
        f"✅ *{sym} SPREAD entered* [trade #{trade_id}]\n"
        f"Template: {tmpl['template']} [{tmpl['grade']}]\n"
        f"${tmpl['long_strike']}/${tmpl['short_strike']} call spread · {tmpl['dte']}d\n"
        f"Fill: ${filled_at:.2f}/contract · {qty} lot(s) = ${premium_dollar:.0f} deployed\n"
        f"Max profit: ${max_profit_dollar:.0f} | Stop: ${premium_dollar*0.5:.0f}",
        chat_id,
    )

    _check_learning_milestone(chat_id)


def _execute_leap_bg(sym: str, leap: dict, chat_id: str):
    """Place a LEAP single-leg order with incremental limit, runs in thread."""
    qty  = leap['qty']
    mid  = leap['mid']

    order_id  = None
    filled_at = None
    for attempt in range(MAX_FILL_TRIES):
        limit_price = round(mid + attempt * 0.05, 2)
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
    trade_id = log_options_trade(
        strategy='LEAP',
        symbol=sym,
        cap_type=cap_type(sym),
        underlying_price=None,
        expiry=leap['expiry'],
        contracts=qty,
        delta_entry=leap.get('delta'),
        iv_rank_entry=None,
        iv_pct_entry=None,
        premium_paid=premium_dollar,
        max_profit=None,
        max_loss=premium_dollar,
        entry_grade=leap['grade'],
        entry_thesis=f"LEAP {leap['dte']}d · delta {leap.get('delta', '?')}",
        strike=leap['strike'],
        right='C',
        catalyst_id=leap.get('catalyst_id'),
        days_to_catalyst=leap.get('catalyst_days'),
    )

    hard_stop = round(premium_dollar * 0.60, 2)
    send_telegram(
        f"✅ *{sym} LEAP entered* [trade #{trade_id}]\n"
        f"${leap['strike']} call · {leap['dte']}d exp · {qty} contract(s)\n"
        f"Fill: ${filled_at:.2f} = ${premium_dollar:.0f} deployed\n"
        f"Delta: {leap.get('delta', '?')} | Grade: {leap['grade']}\n"
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

    return_pct = close_options_trade(tid, exit_value, exit_reason='MANUAL')
    sign       = '+' if (return_pct or 0) >= 0 else ''
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
    send_telegram(f"⏳ Running calculator for *{sym}*...", chat_id)
    calc = run_calculator(sym, qty)
    if 'error' in calc:
        send_telegram(f"❌ {calc['error']}", chat_id)
        return

    msg = format_calc_message(calc)
    send_telegram(msg, chat_id)

    _pending[chat_id] = {
        'action':     'signal_alert',
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
    lines   = [f"📊 *Options Status — {paused}*\n"]
    lines.append(f"Open: {len(trades)} | Closed all-time: {closed}\n")

    if trades:
        for t in trades:
            prem = t['premium_paid'] or 0
            stop = t['stop_value']
            dte  = days_to_expiry(t['expiry'])
            lines.append(
                f"• *{t['symbol']}* [{t['strategy']}] {t['entry_grade']} — "
                f"entry ${prem:.0f} | stop ${stop:.0f} | {dte}d exp"
            )
    else:
        lines.append("No open positions.")

    if cats:
        lines.append(f"\n*Catalysts (30d):*")
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
        lines += [
            f"*{t['symbol']}* [{t['entry_grade']}] — {leg_str}",
            f"  Exp: {t['expiry']} ({dte}d) | Entry: ${prem:.0f}",
            f"  Stop: ${stop:.0f} ({stage_lbl}) | Δ: {t['delta_entry'] or '?'}",
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

        lines = [
            f"📡 *{sym} {dir_arrow} — {now_et}*",
            f"Tier: {tier_emoji} {detail['tier']} | Score: {detail['score']:.2f} "
            f"| {detail['signal_count']} signals ({detail['high_count']} HIGH)",
            f"Sources: {detail['sources'] or 'none'}",
        ]
        if detail.get('narrative'):
            lines += ['', f"_{detail['narrative']}_"]

        signals = detail.get('signals', [])
        if signals:
            lines += ['', '*Signals (last 5 days):*']
            rel_icon = {'HIGH': '🔴', 'MEDIUM': '🟡'}
            for s in signals:
                ts  = s['created_at'][11:16] if len(s['created_at']) > 11 else ''
                ico = rel_icon.get(s['relevance'], '⚪️')
                lines.append(
                    f"{ico} [{ts}] {s['source']} — {s['news_type']} — {s['relevance']}\n"
                    f"  {s['headline'][:85]}\n"
                    f"  ↳ _{s['one_line_reason'] or s['direction']}_"
                )

        lines += ['', f"_OPT BUY {sym} → calculator_"]
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

    lines = [f"📡 *Signal Leaderboard — {now_et}*\n"]

    if high_rows:
        lines.append('🔥 *HIGH conviction*')
        for r in high_rows:
            arrow = {'BULL': '↑', 'BEAR': '↓', 'MIXED': '↕'}.get(r['direction'], '')
            ago   = _signal_age(r['last_signal_at'])
            lines.append(
                f"  *{r['symbol']}* {arrow} | {r['score']:.2f} | "
                f"{r['signal_count']} signals ({r['high_count']} HIGH) | {ago}"
            )
            if r.get('narrative'):
                lines.append(f"  _{r['narrative']}_")
            else:
                lines.append(f"  Sources: {r['sources'] or '—'}")
        lines.append('')

    if medium_rows:
        lines.append('⚡ *MEDIUM conviction*')
        for r in medium_rows[:5]:
            arrow = {'BULL': '↑', 'BEAR': '↓', 'MIXED': '↕'}.get(r['direction'], '')
            ago   = _signal_age(r['last_signal_at'])
            lines.append(
                f"  {r['symbol']} {arrow} | {r['score']:.2f} | "
                f"{r['signal_count']} signals | {r['sources'] or '—'} | {ago}"
            )
        lines.append('')

    tracked = len(board)
    lines += [
        f"_{tracked} tickers tracked · Scores decay 48h half-life_",
        "_OPT NEWS <sym> → detail   OPT BUY <sym> → calculator_",
    ]
    send_telegram('\n'.join(lines), chat_id)


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

def handle_reply(text: str, chat_id: str):
    pending = _pending.get(chat_id)
    if not pending:
        return

    if pending['expires_at'] < datetime.now():
        del _pending[chat_id]
        send_telegram("⏰ Pending action expired (30 min timeout).", chat_id)
        return

    action = pending['action']

    if action == 'signal_alert':
        calc = pending['calc']
        sym  = pending['symbol']
        qty  = pending['qty']
        if text == '5':
            del _pending[chat_id]
            send_telegram(f"Skipped {sym}. No order placed.", chat_id)
            return
        if text in ('1', '2', '3'):
            idx = int(text) - 1
            if idx >= len(calc['templates']):
                send_telegram(f"Template {text} not available for {sym}.", chat_id)
                return
            tmpl = calc['templates'][idx]
            tmpl['underlying'] = calc.get('underlying')
            tmpl['iv_rank']    = calc.get('iv', {}).get('iv_rank')
            tmpl['iv_pct']     = calc.get('iv', {}).get('current_iv')
            del _pending[chat_id]
            send_telegram(f"⏳ Placing {tmpl['template']} spread for *{sym}*...", chat_id)
            t = threading.Thread(target=_execute_spread_bg, args=(sym, tmpl, chat_id), daemon=True)
            t.start()
            return
        if text == '4':
            leap = calc.get('leap')
            if not leap:
                send_telegram(f"No LEAP template available for {sym}.", chat_id)
                return
            del _pending[chat_id]
            send_telegram(f"⏳ Placing LEAP for *{sym}*...", chat_id)
            t = threading.Thread(target=_execute_leap_bg, args=(sym, leap, chat_id), daemon=True)
            t.start()
            return

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
        else:
            send_telegram(
                "Unknown OPT command. Available:\n"
                "`OPT STATUS` · `OPT POSITIONS` · `OPT CALENDAR`\n"
                "`OPT PAUSE` · `OPT RESUME`\n"
                "`OPT CLOSE <sym>` · `OPT BUY <sym> [qty]`\n"
                "`OPT NEWS [sym]` · `OPT ADD <sym> <date> <note> [HIGH|MEDIUM|LOW]`",
                chat_id,
            )
        return

    # Numbered or YES/NO replies to pending actions
    if text in ('1', '2', '3', '4', '5') or text.upper() in ('YES', 'NO'):
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
        "`OPT ADD <sym> <date> <note> [HIGH|MEDIUM|LOW]` — manual catalyst",
        chat_id,
    )


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    global _last_update_id
    print("[options_trader] started — polling Telegram")
    while True:
        try:
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
        time.sleep(10)  # short-poll every 10s — avoids blocking auto_trader's Telegram connection


if __name__ == '__main__':
    main()
