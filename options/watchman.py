"""
watchman.py — Options position monitor.

Runs as a standalone process (separate from auto_trader.py and news_engine.py).

Schedule:
  - Every 15 min during market hours (9:30–4:00 ET): threshold checks only,
    silent unless a trigger fires.
  - 4:00 PM ET sharp: full EOD snapshot for every open position, always sends
    daily Telegram summary regardless of activity.

Trailing stop stages (stored in DB, never placed as broker orders):
  Bull Spread:
    Stage 1 — hard stop at entry cost × 0.50 below premium_paid
    Stage 2 — breakeven lock when value ≥ premium_paid × 1.25
    Stage 3 — trail when value ≥ premium_paid + (max_profit × 0.50),
               stop = session_high − (max_profit × 0.15)

  LEAP:
    Stage 1 — hard stop at premium_paid × 0.60  (i.e., -40%)
    Stage 2 — breakeven lock when value ≥ premium_paid × 1.30
    Stage 3 — trail when value ≥ premium_paid × 1.50,
               stop = session_high − (premium_paid × 0.20)

Six alert thresholds (Telegram, intraday):
  T1 — underlying price move > 3% from prior close
  T2 — IV rank change > 8 pts from entry IV rank
  T3 — contract / spread value up > 30% from entry cost
  T4 — value fell below stop_value  → close alert
  T5 — catalyst event within 10 days
  T6 — LEAP DTE crosses below 180
"""

import os
import sys
import time
import requests
import yfinance as yf
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# ── Path setup ──────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import (
    get_open_options_trades,
    update_options_stop,
    close_options_trade,
    log_options_snapshot,
    log_trade_outcome,
    get_upcoming_catalysts,
    get_closed_options_count,
    get_options_learning_data,
    purge_old_news,
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

ET = ZoneInfo('America/New_York')

US_HOLIDAYS_2026 = {
    date(2026,  1,  1), date(2026,  1, 19), date(2026,  2, 16),
    date(2026,  4,  3), date(2026,  5, 25), date(2026,  6, 19),
    date(2026,  7,  3), date(2026,  9,  7), date(2026, 11, 26),
    date(2026, 12, 25),
}
SCAN_INTERVAL_MIN = 5

# ── Telegram ─────────────────────────────────────────────────────────────────

def send_telegram(message: str):
    if not OPT_TG_TOKEN or not OPT_TG_CHAT_ID:
        print(f"[TG] {message}")
        return
    try:
        requests.post(
            f"{TG_API}/sendMessage",
            json={'chat_id': OPT_TG_CHAT_ID, 'text': message,
                  'parse_mode': 'Markdown'},
            timeout=10,
        )
    except Exception as e:
        print(f"[TG error] {e}")


# ── Market hours helpers ─────────────────────────────────────────────────────

def now_et() -> datetime:
    return datetime.now(ET)


def is_market_hours() -> bool:
    """9:30am–8:00pm ET Mon-Fri. Extended to cover IBKR after-hours options until 8pm."""
    n = now_et()
    if n.weekday() >= 5:
        return False
    if n.date() in US_HOLIDAYS_2026:
        return False
    open_  = n.replace(hour=9, minute=30, second=0, microsecond=0)
    close_ = n.replace(hour=20, minute=0,  second=0, microsecond=0)
    return open_ <= n <= close_


def is_eod_window() -> bool:
    """True 8:00–8:20pm ET — IBKR options stop trading at 8pm."""
    n = now_et()
    if n.weekday() >= 5:
        return False
    if n.date() in US_HOLIDAYS_2026:
        return False
    eod_start = n.replace(hour=20, minute=0, second=0, microsecond=0)
    eod_end   = n.replace(hour=20, minute=20, second=0, microsecond=0)
    return eod_start <= n < eod_end


# ── Bridge helpers ────────────────────────────────────────────────────────────

def _bridge_post(path: str, payload: dict) -> dict | None:
    try:
        r = requests.post(f"{BRIDGE_URL}{path}", json=payload, timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[bridge POST] {path} — {e}")
    return None


def _bridge_get(path: str) -> dict | None:
    try:
        r = requests.get(f"{BRIDGE_URL}{path}", timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[bridge GET] {path} — {e}")
    return None


def _is_paper() -> bool:
    info = _bridge_get('/')
    return (info or {}).get('mode') == 'paper'


def _auto_close_position(trade: dict, current_value: float, exit_reason: str = 'AUTO_STOP') -> bool:
    """
    Place a market-limit sell order to close the position when stop is hit.
    Records outcome to DB. Returns True if close order was successfully placed.
    """
    sym   = trade['symbol']
    strat = trade['strategy']
    tid   = trade['id']
    qty   = trade.get('contracts', 1)

    if strat in ('BULL_PUT_CREDIT', 'BEAR_CALL_CREDIT'):
        # Close credit spread: BUY BACK the spread.
        # long_strike = what we sold (higher-value leg); short_strike = what we bought.
        lq = get_quote(sym, trade['expiry'], trade['long_strike'],  trade['right'])
        sq = get_quote(sym, trade['expiry'], trade['short_strike'], trade['right'])
        if not lq or not sq:
            return False
        long_mid   = ((lq.get('bid') or 0) + (lq.get('ask') or 0)) / 2
        short_mid  = ((sq.get('bid') or 0) + (sq.get('ask') or 0)) / 2
        natural_debit = round(
            (lq.get('ask') or long_mid) - (sq.get('bid') or short_mid), 2)
        limit_debit  = max(0.01, natural_debit)
        exit_dollar  = round((long_mid - short_mid) * 100 * qty, 2)
        payload = {
            'symbol':       sym,
            'expiry':       trade['expiry'],
            'strike':       trade['long_strike'],
            'right':        trade['right'],
            'qty':          qty,
            'action':       'BUY',
            'order_type':   'LIMIT',
            'limit_price':  limit_debit,
            'short_strike': trade['short_strike'],
            'net_debit':    limit_debit,
        }
    elif strat in ('BULL_SPREAD', 'BEAR_PUT_SPREAD'):
        lq = get_quote(sym, trade['expiry'], trade['long_strike'],  trade['right'])
        sq = get_quote(sym, trade['expiry'], trade['short_strike'], trade['right'])
        if not lq or not sq:
            return False
        long_mid   = ((lq.get('bid') or 0) + (lq.get('ask') or 0)) / 2
        short_mid  = ((sq.get('bid') or 0) + (sq.get('ask') or 0)) / 2
        spread_mid = round(long_mid - short_mid, 2)
        # Natural credit: sell long at bid, buy back short at ask.
        # IBKR paper BAG fill requires limit ≤ natural credit to execute SELL.
        natural_credit = round(
            (lq.get('bid') or long_mid) - (sq.get('ask') or short_mid), 2)
        limit_credit = max(0.01, natural_credit)
        payload = {
            'symbol':       sym,
            'expiry':       trade['expiry'],
            'strike':       trade['long_strike'],
            'right':        trade['right'],
            'qty':          qty,
            'action':       'SELL',
            'order_type':   'LIMIT',
            'limit_price':  limit_credit,
            'short_strike': trade['short_strike'],
            'net_debit':    round(-limit_credit, 2),
        }
        exit_dollar = round(spread_mid * 100 * qty, 2)
    elif strat == 'OPT_SCALP':
        q = get_quote(sym, trade['expiry'], trade['long_strike'], trade['right'])
        if not q:
            return False
        mid = round(((q.get('bid') or 0) + (q.get('ask') or 0)) / 2, 2)
        payload = {
            'symbol':      sym,
            'expiry':      trade['expiry'],
            'strike':      trade['long_strike'],
            'right':       trade['right'],
            'qty':         qty,
            'action':      'SELL',
            'order_type':  'LIMIT',
            'limit_price': round(mid - 0.05, 2),
        }
        exit_dollar = round(mid * 100 * qty, 2)
    else:  # LEAP
        q = get_quote(sym, trade['expiry'], trade['strike'], trade['right'])
        if not q:
            return False
        mid = round(((q.get('bid') or 0) + (q.get('ask') or 0)) / 2, 2)
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
        exit_dollar = round(mid * 100 * qty, 2)

    MAX_CLOSE_TRIES = 3
    filled   = False
    order_id = None
    st       = 'Unknown'
    exit_dollar_final = exit_dollar

    for attempt in range(MAX_CLOSE_TRIES):
        # On retry: re-fetch live quotes to get fresh price (spread value can move)
        if attempt > 0:
            time.sleep(30)
            try:
                if strat in ('BULL_SPREAD', 'BEAR_PUT_SPREAD'):
                    lq2 = get_quote(sym, trade['expiry'], trade['long_strike'],  trade['right'])
                    sq2 = get_quote(sym, trade['expiry'], trade['short_strike'], trade['right'])
                    if lq2 and sq2:
                        nat2 = round((lq2.get('bid') or 0) - (sq2.get('ask') or 0), 2)
                        payload['limit_price'] = max(0.01, nat2)
                        payload['net_debit']   = round(-max(0.01, nat2), 2)
                elif strat in ('BULL_PUT_CREDIT', 'BEAR_CALL_CREDIT'):
                    # Two-leg re-quote for credit close: BUY long_strike + SELL short_strike
                    right2 = 'P' if strat == 'BULL_PUT_CREDIT' else 'C'
                    lq2 = get_quote(sym, trade['expiry'], trade['long_strike'],  right2)
                    sq2 = get_quote(sym, trade['expiry'], trade['short_strike'], right2)
                    if lq2 and sq2:
                        nat2 = round((lq2.get('ask') or 0) - (sq2.get('bid') or 0), 2)
                        payload['limit_price'] = max(0.01, nat2)
                        payload['net_debit']   = max(0.01, nat2)
                else:
                    q2 = get_quote(sym, trade['expiry'],
                                   trade.get('long_strike') or trade.get('strike'),
                                   trade['right'])
                    if q2:
                        mid2 = round(((q2.get('bid') or 0) + (q2.get('ask') or 0)) / 2, 2)
                        payload['limit_price'] = max(0.01, round(mid2 - 0.05, 2))
            except Exception:
                pass
            send_telegram(
                f"⏳ *{sym} auto-close retry {attempt+1}/{MAX_CLOSE_TRIES}* (trade #{tid})\n"
                f"Previous attempt {order_id} status: {st}"
            )

        resp = _bridge_post('/options/order', payload)
        if not resp or 'orderId' not in resp:
            continue

        order_id = resp['orderId']
        time.sleep(30)
        status = _bridge_get(f'/order/{order_id}/status')
        st     = (status or {}).get('status', 'Unknown')
        filled = bool(status and status.get('filled', 0) >= qty or st == 'Filled')

        # Portfolio fallback: verify position is gone from IBKR
        if not filled and st in ('Submitted', 'PreSubmitted', 'Cancelled', 'Unknown'):
            try:
                r     = requests.get(f"{BRIDGE_URL}/portfolio/options", timeout=10)
                opts  = r.json() if r.ok else []
                long_stk = float(trade.get('long_strike', 0))
                exp      = trade.get('expiry', '')
                still_open = any(
                    p.get('symbol') == sym
                    and p.get('expiry') == exp
                    and abs(float(p.get('strike', 0)) - long_stk) < 0.01
                    and float(p.get('qty', 0)) > 0
                    for p in opts
                )
                if not still_open:
                    filled = True
            except Exception:
                pass

        if filled:
            # Use avgFillPrice from IBKR if available
            avg_fp = float((status or {}).get('avgFillPrice') or 0)
            if avg_fp > 0 and strat in ('BULL_SPREAD', 'BEAR_PUT_SPREAD',
                                              'BULL_PUT_CREDIT', 'BEAR_CALL_CREDIT'):
                exit_dollar_final = round(avg_fp * 100 * qty, 2)
            break

        # Cancel this specific order before retry
        if order_id:
            _bridge_post(f'/order/{order_id}/cancel', {})

    if not filled:
        send_telegram(
            f"⚠️ *{sym} auto-close FAILED after {MAX_CLOSE_TRIES} attempts* (trade #{tid})\n"
            f"Last order {order_id} status: {st}\n"
            f"DB NOT updated — use `OPT CLOSE {sym}` to retry manually."
        )
        return False

    exit_dollar = exit_dollar_final

    return_pct = close_options_trade(tid, exit_dollar, exit_reason=exit_reason)

    # Log outcome for learning loop
    try:
        import sqlite3 as _sq
        from database import DB_PATH as _dbp
        _conn = _sq.connect(_dbp)
        _cur  = _conn.cursor()
        _cur.execute(
            'SELECT id, mc_ev_dollar, mc_win_rate FROM opt_calc_log WHERE trade_id=? LIMIT 1',
            (tid,))
        _row = _cur.fetchone()
        _cur.execute('SELECT entry_date, premium_paid FROM options_trades WHERE id=?', (tid,))
        _erow = _cur.fetchone()
        _conn.close()
        if _row and _erow:
            _days    = ((date.today() - date.fromisoformat(_erow[0])).days
                        if _erow[0] else 0)
            _premium = _erow[1] or 0
            # Credit spreads: P&L = credit_received - buyback_cost; debit: P&L = exit - entry
            _is_credit = strat in ('BULL_PUT_CREDIT', 'BEAR_CALL_CREDIT')
            _pnl = round((_premium - exit_dollar) if _is_credit else (exit_dollar - _premium), 2)
            log_trade_outcome(
                trade_id=tid, calc_log_id=_row[0],
                predicted_ev=_row[1], predicted_wr=_row[2],
                actual_pnl=_pnl,
                exit_reason=exit_reason,
                days_held=_days,
            )
    except Exception:
        pass

    print(f"[watchman] auto-closed {sym} trade #{tid} — exit ${exit_dollar:.0f} ({return_pct:+.1f}%)")
    if strat == 'OPT_SCALP':
        _check_scalp_milestone()
    return True


def _check_scalp_milestone():
    try:
        import sqlite3
        from database import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        c    = conn.cursor()
        c.execute("SELECT COUNT(*) FROM options_trades WHERE strategy='OPT_SCALP' AND status!='OPEN'")
        cnt = c.fetchone()[0]
        conn.close()
        if cnt > 0 and cnt % 10 == 0:
            _send_scalp_report(cnt)
    except Exception:
        pass


def _send_scalp_report(cnt: int):
    try:
        import sqlite3
        from database import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        c    = conn.cursor()
        c.execute(
            "SELECT exit_reason, return_pct FROM options_trades "
            "WHERE strategy='OPT_SCALP' AND status!='OPEN'",
        )
        rows = c.fetchall()
        conn.close()
        if not rows:
            return
        wins  = sum(1 for r in rows if (r[1] or 0) > 0)
        wr    = round(wins / len(rows) * 100, 1)
        avg_r = round(sum(r[1] or 0 for r in rows) / len(rows), 1)
        exit_reasons: dict[str, int] = {}
        for r in rows:
            k = r[0] or 'UNKNOWN'
            exit_reasons[k] = exit_reasons.get(k, 0) + 1
        lines = [
            f"⚡ *Scalp Report — {cnt} closed trades*",
            f"WR: {wr}% | Avg: {avg_r:+.1f}%",
            "",
            "*Exit reasons:*",
        ] + [f"   {k}: {v}" for k, v in sorted(exit_reasons.items(), key=lambda x: -x[1])]
        if cnt < 30:
            lines.append("\n⚠️ Advisory only — small sample (<30 trades)")
        send_telegram("\n".join(lines))
    except Exception as e:
        print(f"[watchman] scalp report error: {e}")


def _yf_option_quote(symbol: str, expiry: str, strike: float, right: str) -> dict | None:
    """yfinance fallback for option quotes when IBKR/OPRA returns null bid/ask."""
    try:
        exp_fmt = f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:]}"
        chain = yf.Ticker(symbol).option_chain(exp_fmt)
        df = chain.calls if right.upper() == 'C' else chain.puts
        # Exact strike match first, then nearest
        row = df[df['strike'] == float(strike)]
        if row.empty:
            row = df.iloc[(df['strike'] - float(strike)).abs().argsort()[:1]]
        if row.empty:
            return None
        r   = row.iloc[0]
        bid = float(r['bid'])  if r['bid']       > 0 else None
        ask = float(r['ask'])  if r['ask']       > 0 else None
        last= float(r['lastPrice']) if r['lastPrice'] > 0 else None
        # Far-OTM options often have 0 bid/ask — use last price as proxy
        if bid is None and last:
            bid = round(last * 0.90, 2)
            ask = round(last * 1.10, 2)
        return {
            'bid': bid, 'ask': ask, 'last': last,
            'iv':  float(r['impliedVolatility']) if r.get('impliedVolatility') else None,
            'source': 'yfinance_fallback',
        }
    except Exception as e:
        print(f"[yf_option_quote] {symbol} {expiry} {strike}{right}: {e}")
        return None


def _portfolio_option_quote(symbol: str, expiry: str, strike: float, right: str) -> dict | None:
    """Last-resort fallback: use bridge /portfolio/options marketPrice for positions we hold."""
    try:
        r = requests.get(f"{BRIDGE_URL}/portfolio/options", timeout=10)
        if r.status_code != 200:
            return None
        for leg in r.json():
            if (leg.get('symbol') == symbol
                    and leg.get('expiry') == expiry
                    and leg.get('right') == right
                    and abs((leg.get('strike') or 0) - float(strike)) < 0.01):
                mkt = leg.get('marketPrice')
                if mkt and mkt > 0:
                    return {'bid': round(mkt * 0.97, 2), 'ask': round(mkt * 1.03, 2),
                            'last': mkt, 'source': 'portfolio_fallback'}
    except Exception:
        pass
    return None


def get_quote(symbol: str, expiry: str, strike: float, right: str) -> dict | None:
    # Try IBKR bridge first
    try:
        url = f"{BRIDGE_URL}/options/quote/{symbol}/{expiry}/{strike}/{right}"
        r   = requests.get(url, timeout=20)
        if r.status_code == 200:
            d = r.json()
            # Only trust bridge if bid/ask are real — otherwise fall through to yfinance
            if d.get('bid') is not None and d.get('ask') is not None:
                return d
    except Exception as e:
        print(f"[quote error] {symbol} {e}")
    # IBKR returned null bid/ask — fall back to yfinance
    yf_q = _yf_option_quote(symbol, expiry, strike, right)
    if yf_q and yf_q.get('bid') is not None:
        return yf_q
    # yfinance also failed — use portfolio/options marketPrice (last resort for held positions)
    return _portfolio_option_quote(symbol, expiry, strike, right)


def get_iv_rank(symbol: str) -> dict | None:
    try:
        r = requests.get(f"{BRIDGE_URL}/options/iv_rank/{symbol}", timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[iv_rank error] {symbol} {e}")
    return None


def get_underlying_price(symbol: str) -> float | None:
    """Fetch current underlying price via bridge stock quote endpoint."""
    try:
        r = requests.get(f"{BRIDGE_URL}/quote/{symbol}", timeout=20)
        if r.status_code == 200:
            d = r.json()
            return d.get('last') or d.get('mid') or d.get('bid')
    except Exception as e:
        print(f"[price error] {symbol} {e}")
    return None


def get_prior_close(symbol: str) -> float | None:
    """Fetch prior day close via bridge endpoint."""
    try:
        r = requests.get(f"{BRIDGE_URL}/quote/{symbol}", timeout=20)
        if r.status_code == 200:
            return r.json().get('close')
    except Exception as e:
        print(f"[prior_close error] {symbol} {e}")
    return None


# ── Spread value: mid of long leg − mid of short leg ─────────────────────────

def get_spread_value(trade: dict) -> float | None:
    """Return current mid-price of a bull spread."""
    long_q  = get_quote(trade['symbol'], trade['expiry'],
                        trade['long_strike'],  trade['right'])
    short_q = get_quote(trade['symbol'], trade['expiry'],
                        trade['short_strike'], trade['right'])
    if not long_q or not short_q:
        return None
    long_mid  = ((long_q.get('bid')  or 0) + (long_q.get('ask')  or 0)) / 2
    short_mid = ((short_q.get('bid') or 0) + (short_q.get('ask') or 0)) / 2
    value = round((long_mid - short_mid) * 100, 2)  # per-contract dollar value
    return value if value > 0 else None


def get_leap_value(trade: dict) -> float | None:
    """Return current mid-price of a LEAP contract."""
    q = get_quote(trade['symbol'], trade['expiry'],
                  trade['strike'], trade['right'])
    if not q:
        return None
    mid = ((q.get('bid') or 0) + (q.get('ask') or 0)) / 2
    return round(mid * 100, 2)  # per-contract dollar value


def get_scalp_value(trade: dict) -> float | None:
    """Return current mid-price of an OPT_SCALP single ATM call."""
    q = get_quote(trade['symbol'], trade['expiry'],
                  trade['long_strike'], trade['right'])
    if not q:
        return None
    mid = ((q.get('bid') or 0) + (q.get('ask') or 0)) / 2
    return round(mid * 100, 2)  # per-contract dollar value


def get_contract_value(trade: dict) -> float | None:
    """Return TOTAL current value of the position (all contracts combined).
    premium_paid and stop_value in DB are also totals, so comparisons are consistent.
    For credit spreads: 'value' = cost to buy back = what we monitor vs stop/target."""
    contracts = trade.get('contracts', 1) or 1
    strat     = trade.get('strategy', '')
    if strat in ('BULL_SPREAD', 'BEAR_PUT_SPREAD', 'BULL_PUT_CREDIT', 'BEAR_CALL_CREDIT'):
        val = get_spread_value(trade)
    elif strat == 'OPT_SCALP':
        val = get_scalp_value(trade)
    else:
        val = get_leap_value(trade)
    if val is None:
        return None
    return round(val * contracts, 2)


# ── DTE helper ───────────────────────────────────────────────────────────────

def days_to_expiry(expiry_str: str) -> int:
    """Return calendar days from today to expiry (YYYYMMDD or YYYY-MM-DD)."""
    try:
        expiry_str = expiry_str.replace('-', '')
        expiry     = datetime.strptime(expiry_str, '%Y%m%d').date()
        return (expiry - date.today()).days
    except Exception:
        return 9999


# ── Trailing stop logic ───────────────────────────────────────────────────────

def compute_new_stop(trade: dict, current_value: float,
                     session_high: float) -> tuple[float | None, int | None]:
    """
    Evaluate whether stop should be updated. Returns (new_stop, new_stage)
    or (None, None) if no change needed.

    stage 1 → 2: promote when profit milestone hit
    stage 2 → 3: promote when next milestone hit
    stage 3: update trail if session_high advanced

    Returns the stop that should be written to DB, along with the stage.
    """
    # OPT_SCALP + credit spreads: fixed thresholds, no trailing
    if trade.get('strategy') in ('OPT_SCALP', 'BULL_PUT_CREDIT', 'BEAR_CALL_CREDIT'):
        return None, None

    premium   = trade['premium_paid']    # true entry cost incl. commissions
    max_profit = trade['max_profit'] or 0
    cur_stage  = trade['stop_stage'] or 1
    cur_stop   = trade['stop_value']

    if trade['strategy'] in ('BULL_SPREAD', 'BEAR_PUT_SPREAD'):
        hard_stop      = round(premium * 0.50, 2)      # -50%
        be_trigger     = round(premium * 1.25, 2)      # +25% profit
        trail_trigger  = round(premium + max_profit * 0.50, 2)  # +50% of max
        trail_stop     = round(session_high - max_profit * 0.15, 2)
    else:  # LEAP
        hard_stop      = round(premium * 0.60, 2)      # -40% (keep 60%)
        be_trigger     = round(premium * 1.30, 2)      # +30% profit
        trail_trigger  = round(premium * 1.50, 2)      # +50% of premium
        trail_stop     = round(session_high - premium * 0.20, 2)

    if cur_stage == 1:
        if cur_stop is None:
            return hard_stop, 1
        if current_value >= trail_trigger:
            return max(trail_stop, premium), 3
        if current_value >= be_trigger:
            return premium, 2
        return None, None

    if cur_stage == 2:
        if current_value >= trail_trigger:
            return max(trail_stop, premium), 3
        return None, None

    if cur_stage == 3:
        new_trail = round(session_high - (
            max_profit * 0.15 if trade['strategy'] in ('BULL_SPREAD', 'BEAR_PUT_SPREAD')
            else premium * 0.20
        ), 2)
        if new_trail > (cur_stop or 0):
            return new_trail, 3
        return None, None

    return None, None


# ── Per-trade threshold evaluation ───────────────────────────────────────────

# Track session highs across intraday scans (reset at EOD)
_session_highs: dict[int, float] = {}
# Track which threshold alerts already fired per trade today (reset at EOD)
_alerted_thresholds: dict[int, set] = {}


def _check_trade(trade: dict, is_eod: bool) -> list[str]:
    """
    Evaluate one open trade. Returns list of alert messages (empty if silent).
    Side effect: may update stop_value in DB, update session high.
    """
    alerts: list[str] = []
    tid    = trade['id']
    sym    = trade['symbol']
    strat  = trade['strategy']
    prem   = trade['premium_paid']
    stage  = trade['stop_stage'] or 1
    stop   = trade['stop_value']
    fired  = _alerted_thresholds.setdefault(tid, set())

    # ── Fetch current market data ──
    current_value = get_contract_value(trade)
    if current_value is None:
        print(f"[watchman] no value for {sym} trade {tid}, skipping")
        return alerts

    underlying = get_underlying_price(sym)
    prior_close = get_prior_close(sym)
    iv_data    = get_iv_rank(sym)
    iv_rank    = iv_data.get('iv_rank') if iv_data else None
    dte        = days_to_expiry(trade['expiry'])

    # Get greeks from quote for snapshot
    if strat == 'LEAP':
        q = get_quote(sym, trade['expiry'], trade['strike'], trade['right'])
    else:
        q = get_quote(sym, trade['expiry'], trade['long_strike'], trade['right'])
    delta = q.get('delta') if q else None
    iv_pct = q.get('iv') if q else None

    # ── Track session high ──
    prev_high = _session_highs.get(tid, current_value)
    session_high = max(prev_high, current_value)
    _session_highs[tid] = session_high

    # ── Trailing stop update ──
    new_stop, new_stage = compute_new_stop(trade, current_value, session_high)
    if new_stop is not None and new_stage is not None:
        if new_stop != stop or new_stage != stage:
            update_options_stop(tid, new_stop, new_stage)
            trade['stop_value'] = new_stop
            trade['stop_stage'] = new_stage
            stop  = new_stop
            stage = new_stage

    is_credit = strat in ('BULL_PUT_CREDIT', 'BEAR_CALL_CREDIT')

    # ── T3b: hit profit target — AUTO-CLOSE ──
    target = trade.get('target_value')
    # Credit: profit when spread VALUE FALLS below target (spread depreciated).
    # Debit: profit when spread VALUE RISES above target.
    t3b_hit = (target is not None and (
        (is_credit and current_value <= target) or
        (not is_credit and current_value >= target)
    )) and 'T3b' not in fired

    if t3b_hit:
        fired.add('T3b')
        if is_credit:
            profit_dollar = round(prem - current_value, 2)
            gain_pct = round(profit_dollar / prem * 100, 1) if prem else 0
            tgt_label = '50% credit profit'
        else:
            gain_pct = round((current_value - prem) / prem * 100, 1) if prem else 0
            tgt_label = '50% max profit' if strat in ('BULL_SPREAD', 'BEAR_PUT_SPREAD') else '100% gain'
        closed = _auto_close_position(trade, current_value, exit_reason='AUTO_TARGET')
        value_line = (
            f"Buyback value: ${current_value:.2f} ≤ target ${target:.2f}" if is_credit else
            f"Value: ${current_value:.2f} ≥ target ${target:.2f}"
        )
        if closed:
            alerts.append(
                f"🎯 *AUTO-CLOSED: {sym} {strat}* (trade #{tid})\n"
                f"Profit target reached ({tgt_label}) · +{gain_pct}%\n"
                f"{value_line}"
            )
        else:
            alerts.append(
                f"🎯 *TARGET HIT — {sym} {strat}* (trade #{tid})\n"
                f"{tgt_label} reached · +{gain_pct}%\n"
                f"{value_line}\n"
                f"⚠️ Auto-close failed — act now: `OPT CLOSE {sym}`"
            )
        return alerts

    # ── T4: stop hit — AUTO-CLOSE ──
    # Credit: stop when spread VALUE RISES above stop_value (spread inflated = losing).
    # Debit: stop when spread VALUE FALLS below stop_value.
    t4_hit = (stop is not None and (
        (is_credit and current_value >= stop) or
        (not is_credit and current_value <= stop)
    )) and 'T4' not in fired

    if t4_hit:
        fired.add('T4')
        if is_credit:
            loss_dollar = round(current_value - prem, 2)
            stop_label  = f'spread tripled — buyback ${current_value:.2f} ≥ stop ${stop:.2f}'
        else:
            stage_label = {1: 'hard stop (-50%)', 2: 'breakeven stop', 3: 'trail stop'}.get(stage, '')
            stop_label  = f'{stage_label} — value ${current_value:.2f} ≤ stop ${stop:.2f}'
        closed = _auto_close_position(trade, current_value)
        if closed:
            alerts.append(
                f"🚨 *AUTO-CLOSED: {sym} {strat}* (trade #{tid})\n"
                f"Stop hit ({stop_label}) | Entry credit: ${prem:.2f}"
            )
            return alerts
        else:
            alerts.append(
                f"🚨 *STOP HIT — {sym} {strat}* (trade #{tid})\n"
                f"{stop_label}\n"
                f"⚠️ Auto-close failed — act now: `OPT CLOSE {sym}`"
            )
            return alerts

    # ── OPT_SCALP: 2-day time stop (checked intraday + EOD) ──
    if strat == 'OPT_SCALP' and 'SCALP_TIME' not in fired:
        entry_date_str = trade.get('entry_date')
        if entry_date_str:
            days_held = (date.today() - date.fromisoformat(entry_date_str)).days
            if days_held >= 2:
                fired.add('SCALP_TIME')
                closed = _auto_close_position(trade, current_value, exit_reason='SCALP_TIME')
                if closed:
                    alerts.append(
                        f"⏰ *AUTO-CLOSED: {sym} SCALP* (trade #{tid})\n"
                        f"2-day hold limit reached (Day {days_held})\n"
                        f"Value: ${current_value:.2f} | Entry cost: ${prem:.2f}"
                    )
                else:
                    alerts.append(
                        f"⏰ *SCALP TIME STOP — {sym}* (trade #{tid})\n"
                        f"3-day limit hit (Day {days_held}) — close now: `OPT CLOSE {sym}`"
                    )
                return alerts

    # ── T5: catalyst within 10 days (once per session) ──
    if trade.get('catalyst_id') and 'T5' not in fired:
        catalysts = get_upcoming_catalysts(days=10)
        for cat in catalysts:
            if cat.get('id') == trade['catalyst_id']:
                days_left = (datetime.strptime(cat['date'], '%Y-%m-%d').date() - date.today()).days
                iv_str = f"\nIV rank: {iv_rank:.0f}%" if iv_rank else ""
                fired.add('T5')
                alerts.append(
                    f"📅 *{sym} catalyst in {days_left}d*: {cat['name']}\n"
                    f"Type: {cat['type']} | Confidence: {cat['confidence']}{iv_str}"
                )

    # ── T6: LEAP DTE below 180 (once per session) ──
    if strat == 'LEAP' and dte < 180 and 'T6' not in fired:
        fired.add('T6')
        alerts.append(
            f"⏰ *{sym} LEAP: {dte} DTE* — below 180-day threshold\n"
            f"Time decay accelerating. Roll or close.\n"
            f"→ Use `OPT CLOSE {sym}` to exit"
        )

    # ── Quick play: 2-day time exit (14-21 DTE at entry — new pro strategy) ──
    if strat in ('BULL_SPREAD', 'BEAR_PUT_SPREAD') and 'QUICK_TIME' not in fired:
        try:
            entry_d = date.fromisoformat(trade.get('entry_date', '2000-01-01'))
            exp_d   = datetime.strptime(trade.get('expiry', '20000101').replace('-', ''), '%Y%m%d').date()
            dte_at_entry = (exp_d - entry_d).days
            if dte_at_entry <= 23:   # quick play entered with 14-21 DTE
                days_held = (date.today() - entry_d).days
                if days_held >= 2:
                    fired.add('QUICK_TIME')
                    pnl_pct = (current_value - prem) / prem * 100 if prem else 0
                    closed  = _auto_close_position(trade, current_value, exit_reason='QUICK_TIME')
                    if closed:
                        alerts.append(
                            f"⏰ *AUTO-CLOSED: {sym} {strat}* — 2-day time exit\n"
                            f"Held {days_held}d | P&L: ${current_value - prem:+.2f} ({pnl_pct:+.1f}%)\n"
                            f"Quick play rule: exit after 2 days regardless of P&L"
                        )
                    else:
                        alerts.append(
                            f"⏰ *{sym} {strat}: 2-day time exit* — manual close needed\n"
                            f"Held {days_held}d | Value: ${current_value:.2f}\n"
                            f"→ `OPT CLOSE {sym}`"
                        )
                    return alerts   # position closed (or close failed) — skip further checks
        except Exception:
            pass

    # ── Standard spread / credit spread: 21 DTE exit alert ──
    if strat in ('BULL_SPREAD', 'BEAR_PUT_SPREAD', 'BULL_PUT_CREDIT', 'BEAR_CALL_CREDIT') and dte <= 21 and 'BULL_DTE' not in fired:
        try:
            entry_d = date.fromisoformat(trade.get('entry_date', '2000-01-01'))
            exp_d   = datetime.strptime(trade.get('expiry', '20000101').replace('-', ''), '%Y%m%d').date()
            dte_at_entry = (exp_d - entry_d).days
            is_standard = dte_at_entry > 23   # entered with ≥24 DTE — old 28-45 DTE strategy
        except Exception:
            is_standard = True
        if is_standard:
            fired.add('BULL_DTE')
            alerts.append(
                f"⏰ *{sym} {strat}: {dte} DTE* — 21-DTE time exit rule\n"
                f"P&L: ${current_value - prem:+.2f} | Value: ${current_value:.2f}\n"
                f"→ Close via: `OPT CLOSE {sym}`"
            )

    # ── EOD: write snapshot ──
    if is_eod:
        log_options_snapshot(
            trade_id=tid,
            underlying_price=underlying,
            contract_value=current_value,
            delta=delta,
            iv_rank=iv_rank,
            iv_pct=iv_pct,
            days_to_expiry=dte,
            stop_value=stop,
        )

    return alerts


# ── EOD summary builder ───────────────────────────────────────────────────────

def _build_eod_summary(trades: list[dict]) -> str:
    today_str  = date.today().strftime('%b %d')
    catalysts  = get_upcoming_catalysts(days=14)
    closed_cnt = get_closed_options_count()

    lines = [f"📊 *Options EOD — {today_str}*\n"]
    lines.append(f"Open positions: {len(trades)} | Closed all-time: {closed_cnt}\n")

    if not trades:
        lines.append("No open positions.\n")
    else:
        for t in trades:
            sym   = t['symbol']
            strat = t['strategy']
            prem  = t['premium_paid'] or 0
            cv        = get_contract_value(t) or 0  # already total (×contracts)
            is_cred   = strat in ('BULL_PUT_CREDIT', 'BEAR_CALL_CREDIT')
            pnl       = round((prem - cv) if is_cred else (cv - prem), 2)
            pct       = round(pnl / prem * 100, 1) if prem else 0
            dte   = days_to_expiry(t['expiry'])
            stage = t['stop_stage'] or 1
            stop  = t['stop_value']
            grade = t.get('entry_grade', '?')
            sign  = "+" if pnl >= 0 else ""
            lines.append(
                f"• *{sym}* [{strat} {grade}] — P&L: {sign}${pnl:.0f} ({sign}{pct:.1f}%)\n"
                f"  Stop: ${stop:.2f} (Stage {stage}) | DTE: {dte}"
            )

    # Upcoming catalysts in next 14 days
    if catalysts:
        lines.append("\n📅 *Upcoming catalysts (14d):*")
        for cat in catalysts[:5]:
            lines.append(f"• {cat['symbol']} — {cat['name']} ({cat['date']}) [{cat['confidence']}]")

    # Learning loop check (milestone-based)
    if closed_cnt >= 20:
        closed_mod = (closed_cnt - 20) % 10
        if closed_mod == 0:
            lines.append(f"\n🧠 *Learning milestone reached* ({closed_cnt} closed trades)")
            lines.append("Run learning analysis: `OPT STATUS` for advisory report")

    return "\n".join(lines)


# ── Intraday scan ─────────────────────────────────────────────────────────────

def run_intraday_scan(scalp_only: bool = False):
    trades = get_open_options_trades()
    if not trades:
        return

    if scalp_only:
        trades = [t for t in trades if t.get('strategy') == 'OPT_SCALP']
        if not trades:
            return

    all_alerts: list[str] = []
    for trade in trades:
        try:
            alerts = _check_trade(trade, is_eod=False)
            all_alerts.extend(alerts)
        except Exception as e:
            print(f"[watchman] error checking trade {trade['id']}: {e}")

    for alert in all_alerts:
        send_telegram(alert)

    if all_alerts:
        label = 'scalp-only' if scalp_only else 'intraday'
        print(f"[watchman] {label} scan: {len(all_alerts)} alert(s) sent")
    elif not scalp_only:
        print(f"[watchman] intraday scan: {len(trades)} position(s) checked, all quiet")


# ── EOD run ───────────────────────────────────────────────────────────────────

def run_eod():
    trades = get_open_options_trades()
    print(f"[watchman] EOD: {len(trades)} open position(s)")

    all_alerts: list[str] = []
    for trade in trades:
        try:
            alerts = _check_trade(trade, is_eod=True)
            all_alerts.extend(alerts)
        except Exception as e:
            print(f"[watchman] EOD error trade {trade['id']}: {e}")

    # Threshold alerts first, then summary
    for alert in all_alerts:
        send_telegram(alert)

    # Reset session highs and intraday threshold alerts after EOD
    _session_highs.clear()
    _alerted_thresholds.clear()

    # Always send EOD summary
    summary = _build_eod_summary(trades)
    send_telegram(summary)
    print(f"[watchman] EOD summary sent")

    # Purge stale dedup rows — keeps options_news table lean
    noise_del, _ = purge_old_news(keep_noise_days=7)
    if noise_del:
        print(f"[watchman] purged {noise_del} stale NOISE/LOW news rows")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    print("[watchman] started")
    # Note: _session_highs is empty on startup — seeds from current price on first
    # check per trade. Trailing stops are seeded from current price (not true intraday
    # high) after a mid-day restart. Hard stop (-50%) still protects against large losses.
    eod_sent_today: date | None = None
    last_full_scan = 0.0   # epoch seconds

    while True:
        try:
            n = now_et()
            today = n.date()

            if is_eod_window() and eod_sent_today != today:
                run_eod()
                eod_sent_today = today
                last_full_scan = time.time()
            elif is_market_hours():
                # 1-min fast loop for OPT_SCALP (can hit -50% in <5 min at 7-12 DTE)
                run_intraday_scan(scalp_only=True)
                # Full scan every SCAN_INTERVAL_MIN (5 min)
                if time.time() - last_full_scan >= SCAN_INTERVAL_MIN * 60:
                    run_intraday_scan(scalp_only=False)
                    last_full_scan = time.time()

        except Exception as e:
            print(f"[watchman] loop error: {e}")

        time.sleep(60)   # base tick: 1 min (scalps checked every tick)


if __name__ == '__main__':
    main()
