#!/usr/bin/env python3
"""TriVega Trading Dashboard — Flask server, port 8080."""

import os, sys, sqlite3, subprocess, json, base64, functools
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from flask import Flask, render_template, jsonify, request, Response, session, redirect, url_for
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from futures.strategy_core import TICK_SIZE, TICK_VALUE  # noqa: E402

# ── Config ─────────────────────────────────────────────────────────────
BASE_DIR        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADES_DB       = os.path.join(BASE_DIR, 'trades.db')
BRIDGE_URL      = 'http://localhost:8000'
TC_BRIDGE_URL   = 'http://localhost:8002'
PROD_BRIDGE_URL = None   # set to 'http://localhost:8001' when prod bridge is live
PORT            = 8080
ET              = ZoneInfo('America/New_York')

SERVICES = [
    ('bridge',       'com.sushil.trading.bridge'),
    ('autotrader',   'com.sushil.trading.autotrader'),
    ('watchman',     'com.sushil.trading.watchman'),
    ('news_engine',  'com.sushil.trading.news_engine'),
    ('options',      'com.sushil.trading.options_trader'),
    ('collect_bars', 'com.sushil.trading.collect_bars'),
    ('graphify',     'com.sushil.trading.graphify_watch'),
    # Aug 9 2026: futures services were missing entirely from this row — a
    # stopped futures_personal or futures_trader (TC) would have shown zero
    # indication here, despite this session's entire focus being TC readiness.
    ('gateway',      'com.sushil.trading.gateway'),          # IBKR TWS/Gateway (equity+options bridge depends on this)
    ('futures_ibkr', 'com.sushil.trading.futures_personal'), # futures_trader.py — IBKR NY + London
    ('tc_gateway',   'com.sushil.trading.tc_gateway'),        # TC's IB Gateway process (DUQ640500 stand-in, for now)
    ('tc_bridge',    'com.sushil.trading.tc_bridge'),         # TC's port-8002 bridge (bridge.py/DUQ640500 until re-hooked to bridge_projectx.py)
    ('futures_tc',   'com.sushil.trading.futures_trader'),   # tc_trader.py — TC NY + London
    ('futures_bars', 'com.sushil.trading.futures_collect_bars'),
]

# 2026 key macro dates — update annually
MACRO_EVENTS = [
    # FOMC
    {'date': '2026-07-29', 'event': 'FOMC Rate Decision',              'type': 'HIGH',    'category': 'FOMC',    'link': 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'},
    {'date': '2026-09-16', 'event': 'FOMC Rate Decision',              'type': 'HIGH',    'category': 'FOMC',    'link': 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'},
    {'date': '2026-11-04', 'event': 'FOMC Rate Decision',              'type': 'HIGH',    'category': 'FOMC',    'link': 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'},
    {'date': '2026-12-09', 'event': 'FOMC Rate Decision',              'type': 'HIGH',    'category': 'FOMC',    'link': 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'},
    # CPI
    {'date': '2026-07-15', 'event': 'CPI Release (Jun data)',          'type': 'HIGH',    'category': 'CPI',     'link': 'https://www.bls.gov/schedule/news_release/cpi.htm'},
    {'date': '2026-08-12', 'event': 'CPI Release (Jul data)',          'type': 'HIGH',    'category': 'CPI',     'link': 'https://www.bls.gov/schedule/news_release/cpi.htm'},
    {'date': '2026-09-10', 'event': 'CPI Release (Aug data)',          'type': 'HIGH',    'category': 'CPI',     'link': 'https://www.bls.gov/schedule/news_release/cpi.htm'},
    # NFP
    {'date': '2026-07-10', 'event': 'Non-Farm Payrolls (Jun data)',    'type': 'HIGH',    'category': 'NFP',     'link': 'https://www.bls.gov/schedule/news_release/empsit.htm'},
    {'date': '2026-08-07', 'event': 'Non-Farm Payrolls (Jul data)',    'type': 'HIGH',    'category': 'NFP',     'link': 'https://www.bls.gov/schedule/news_release/empsit.htm'},
    {'date': '2026-09-04', 'event': 'Non-Farm Payrolls (Aug data)',    'type': 'HIGH',    'category': 'NFP',     'link': 'https://www.bls.gov/schedule/news_release/empsit.htm'},
    # PCE
    {'date': '2026-06-26', 'event': 'PCE Price Index (May data)',      'type': 'MEDIUM',  'category': 'PCE',     'link': 'https://www.bea.gov/'},
    {'date': '2026-07-31', 'event': 'PCE Price Index (Jun data)',      'type': 'MEDIUM',  'category': 'PCE',     'link': 'https://www.bea.gov/'},
    # Market holidays
    {'date': '2026-07-03', 'event': 'Independence Day — MARKET CLOSED','type': 'HOLIDAY', 'category': 'HOLIDAY', 'link': None},
    {'date': '2026-09-07', 'event': 'Labor Day — MARKET CLOSED',       'type': 'HOLIDAY', 'category': 'HOLIDAY', 'link': None},
    {'date': '2026-11-26', 'event': 'Thanksgiving — MARKET CLOSED',    'type': 'HOLIDAY', 'category': 'HOLIDAY', 'link': None},
    {'date': '2026-12-25', 'event': 'Christmas — MARKET CLOSED',       'type': 'HOLIDAY', 'category': 'HOLIDAY', 'link': None},
]

# Go-live checklist for production tab
GOLIVE_CHECKLIST = [
    {'item': 'Gateway reconnect simulation test',    'done': False},
    {'item': 'PROD_EQUITY_ENABLED flag test',        'done': False},
    {'item': 'Prod .env credentials audit',          'done': False},
    {'item': 'Prod gateway launchd bootstrap',       'done': False},
    {'item': 'Partial fill handling in place_trade', 'done': True},
    {'item': 'Buying power pre-check',               'done': True},
    {'item': 'Bridge streaming subscriptions',       'done': True},
    {'item': 'Float gate for scanner stocks',        'done': True},
]

app = Flask(__name__)
app.secret_key = os.getenv('DASHBOARD_PASSWORD', 'trivega-dev-key')  # signs the session cookie

# ── Auth (cookie-based session — enter once, persists 30 days) ───────────
_DASH_PASSWORD = os.getenv('DASHBOARD_PASSWORD', '')

_OPEN_PATHS = {'/login', '/static/apple-touch-icon.png', '/favicon.ico'}

@app.before_request
def _check_auth():
    if not _DASH_PASSWORD:
        return
    if request.path in _OPEN_PATHS or request.path.startswith('/static/'):
        return
    if not session.get('authenticated'):
        return redirect(url_for('login', next=request.path))

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = ''
    if request.method == 'POST':
        if request.form.get('password') == _DASH_PASSWORD:
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=30)
            session['authenticated'] = True
            return redirect(request.args.get('next') or '/')
        error = 'Wrong password'
    return render_template('login.html', error=error)


# ── DB helper ──────────────────────────────────────────────────────────

def _db():
    conn = sqlite3.connect(TRADES_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_london_account_mode_column():
    """Aug 9 2026: the dashboard now queries london_trades.account_mode (IBKR/TC
    split). That column is added by futures/london_trader.py's init_db() on the
    trader's next restart — but the dashboard is a separate process that may run
    before that restart happens. Idempotent ALTER, same pattern as database.py,
    so the dashboard doesn't 500 on a stale schema regardless of restart order."""
    try:
        conn = sqlite3.connect(TRADES_DB)
        conn.execute("ALTER TABLE london_trades ADD COLUMN account_mode TEXT DEFAULT 'IBKR'")
        conn.commit()
        conn.close()
    except Exception:
        pass  # column already exists, or table doesn't exist yet


_ensure_london_account_mode_column()


# ── Bridge helpers ─────────────────────────────────────────────────────

def _bridge(path, url=None, timeout=4):
    try:
        r = requests.get((url or BRIDGE_URL) + path, timeout=timeout)
        return r.json()
    except Exception:
        return None


# ── Data functions ─────────────────────────────────────────────────────

def get_bridge_info(url=None):
    data = _bridge('/', url=url)
    if not data:
        return {'status': 'DOWN', 'connected': False, 'mode': 'UNKNOWN', 'account': '—'}
    data['status'] = 'UP'
    return data


def get_services():
    try:
        out = subprocess.run(['launchctl', 'list'], capture_output=True, text=True, timeout=5).stdout
        return {name: (label in out) for name, label in SERVICES}
    except Exception:
        return {name: False for name, _ in SERVICES}


def get_regime():
    today = datetime.now(tz=ET).strftime('%Y-%m-%d')
    # Primary: scan_log (only written during entry window, 10am+)
    try:
        with _db() as c:
            row = c.execute(
                'SELECT regime, scan_date, scan_time FROM scan_log ORDER BY id DESC LIMIT 1'
            ).fetchone()
            if row and row['scan_date'] == today:
                return {'label': row['regime'], 'at': f"{row['scan_date']} {row['scan_time']}"}
    except Exception:
        pass
    # Fallback: parse the latest SCAN line from auto_trader.log
    # Format: [HH:MM:SS] SCAN | Regime: NORMAL (x1) | ...
    import re
    log_path = os.path.join(BASE_DIR, 'logs', 'auto_trader.log')
    label, at = None, None
    try:
        with open(log_path, 'r', errors='replace') as f:
            # Read last 4KB — enough to find the latest SCAN line without reading the whole file
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4096))
            chunk = f.read()
        for line in reversed(chunk.splitlines()):
            m = re.search(r'SCAN \| Regime: (\w+)', line)
            if m:
                label = m.group(1)
                ts_m = re.match(r'\[(\d{2}:\d{2}:\d{2})\]', line)
                at = f"{today} {ts_m.group(1)}" if ts_m else today
                break
    except Exception:
        pass
    if label:
        return {'label': label, 'at': at, 'source': 'log'}
    # Last resort: return whatever scan_log has (may be stale)
    try:
        with _db() as c:
            row = c.execute(
                'SELECT regime, scan_date, scan_time FROM scan_log ORDER BY id DESC LIMIT 1'
            ).fetchone()
            if row:
                return {'label': row['regime'], 'at': f"{row['scan_date']} {row['scan_time']} (stale)"}
    except Exception:
        pass
    return {'label': 'UNKNOWN', 'at': None}


def get_equity_positions():
    rows = []
    try:
        with _db() as c:
            rows = c.execute("""
                SELECT symbol, entry_date, entry_time, entry_price, shares, side,
                       target_price, stop_price, setup_type, sector, confidence,
                       max_gain_pct, hod_at_entry
                FROM trades
                WHERE status = 'OPEN' AND setup_type != 'RECONCILED'
                ORDER BY entry_time DESC
            """).fetchall()
    except Exception:
        pass

    live_map = {}
    live_raw = _bridge('/portfolio')
    bridge_connected = live_raw is not None  # None = bridge down/reconnecting; [] = connected but no positions
    for p in (live_raw or []):
        live_map[p.get('symbol', '')] = p

    result = []
    for row in rows:
        sym = row['symbol']
        lp  = live_map.get(sym, {})
        ep  = row['entry_price'] or 0
        shares = row['shares'] or 0

        # Only use bridge price when bridge is connected; show None when reconnecting
        # so the dashboard displays "---" instead of misleading $0 unrealized P&L
        if bridge_connected and lp:
            cp = lp.get('marketPrice') or ep
            # Sep 22 2026: this used to report the BROKER's unrealizedPnL, which is
            # blended across every book that happens to hold the symbol, while the
            # % below is computed from THIS book's own entry. When two engines own
            # the same name the two numbers describe different positions — VICR
            # showed "+0.95%" next to "+$300" because Wave Rider held 9 shares from
            # $212.88 and auto_trader 8 from $244.00 (IBKR: 17 @ $227.64). Always
            # derive the dollar figure from this row's own shares and entry so $ and
            # % agree and both describe the position this book actually owns.
            unreal_pnl = (cp - ep) * shares if ep else None
        else:
            cp = ep
            unreal_pnl = None  # will render as "---" in frontend

        unreal_pct = ((cp - ep) / ep * 100) if (ep and bridge_connected and lp) else None

        stop = row['stop_price'] or 0
        if stop and ep:
            buf = (cp - stop) / ep * 100
            status = 'REVIEW' if buf < 0.5 else ('WARN' if buf < 2.0 else 'OK')
        else:
            status = 'OK'

        result.append({
            'symbol':        sym,
            'entry_date':    row['entry_date'],
            'entry_time':    row['entry_time'],
            'entry_price':   ep,
            'current_price': cp,
            'shares':        shares,
            'side':          row['side'],
            'target_price':  row['target_price'],
            'stop_price':    stop,
            'setup_type':    row['setup_type'],
            'sector':        row['sector'],
            'confidence':    row['confidence'],
            'unreal_pnl':    round(unreal_pnl, 2) if unreal_pnl is not None else None,
            'unreal_pct':    round(unreal_pct, 2),
            'status':        status,
        })
    return result


def get_options_positions():
    rows = []
    try:
        with _db() as c:
            rows = c.execute("""
                SELECT id, symbol, strategy, expiry, long_strike, short_strike,
                       right, contracts, premium_paid, target_value, stop_value,
                       entry_date, delta_entry, NULL as net_theta, max_profit, max_loss,
                       net_debit, entry_grade
                FROM options_trades
                WHERE status NOT IN ('CLOSED','EXPIRED','CANCELLED')
                ORDER BY entry_date DESC
            """).fetchall()
    except Exception:
        pass

    live_map = {}
    for p in (_bridge('/portfolio/options') or []):
        sym = p.get('symbol', '')
        agg = live_map.setdefault(sym, {'marketValue': 0.0, 'unrealizedPnL': 0.0})
        agg['marketValue'] += p.get('marketValue') or 0.0
        agg['unrealizedPnL'] += p.get('unrealizedPnL') or 0.0

    now_et = datetime.now(tz=ET)

    result = []
    for row in rows:
        sym = row['symbol']
        lp  = live_map.get(sym, {})
        cv  = lp.get('marketValue')
        upnl = lp.get('unrealizedPnL')
        paid = abs(row['premium_paid'] or row['net_debit'] or 0)

        pnl_pct = None
        if paid and upnl is not None:
            pnl_pct = upnl / paid * 100

        # DTE
        dte = None
        if row['expiry']:
            try:
                exp = datetime.strptime(str(row['expiry'])[:8], '%Y%m%d').date()
                dte = (exp - now_et.date()).days
            except Exception:
                pass

        # Earnings days
        earnings_days = None
        try:
            with _db() as c:
                ec = c.execute(
                    'SELECT earnings_date FROM earnings_calendar WHERE symbol=? '
                    'AND earnings_date >= ? ORDER BY earnings_date ASC LIMIT 1',
                    (sym, now_et.strftime('%Y-%m-%d'))
                ).fetchone()
                if ec:
                    ed = datetime.strptime(ec['earnings_date'], '%Y-%m-%d').date()
                    earnings_days = (ed - now_et.date()).days
        except Exception:
            pass

        maxloss = abs(row['max_loss'] or paid or 1)
        if upnl is not None and maxloss:
            loss_pct = abs(min(upnl, 0)) / maxloss * 100
            status = 'REVIEW' if loss_pct > 80 else (
                     'WARN'   if (loss_pct > 50 or (earnings_days is not None and 0 < earnings_days <= 5))
                               else 'OK')
        else:
            status = 'OK'

        result.append({
            'id':            row['id'],
            'symbol':        sym,
            'strategy':      row['strategy'],
            'expiry':        row['expiry'],
            'dte':           dte,
            'long_strike':   row['long_strike'],
            'short_strike':  row['short_strike'],
            'right':         row['right'],
            'contracts':     row['contracts'],
            'premium_paid':  paid,
            'current_value': cv,
            'unreal_pnl':    round(upnl, 2) if upnl is not None else None,
            'pnl_pct':       round(pnl_pct, 1) if pnl_pct is not None else None,
            'target_value':  row['target_value'],
            'stop_value':    row['stop_value'],
            'delta':         row['delta_entry'],
            'theta_daily':   row['net_theta'],
            'entry_date':    row['entry_date'],
            'earnings_days': earnings_days,
            'max_profit':    row['max_profit'],
            'max_loss':      row['max_loss'],
            'grade':         row['entry_grade'],
            'status':        status,
        })
    return result


def get_futures_positions():
    """
    Per-trade rows from our own DB, not a proxy of IBKR's broker-consolidated
    position. IBKR nets same-symbol fills into one line (e.g. two separate
    2-contract shorts show up there as a single qty=-4 position) — that's
    normal broker accounting, but it hides that each trade can carry its own
    stop/target and would only partially exit at a given price level. Fixed
    Jul 7 2026 to match the equity/options views, which already do this.

    Aug 9 2026 (Crest Watch dashboard sweep): two fixes bundled in.
    (1) This previously queried futures_trades only — any OPEN london_trades
    row was invisible here (it only ever showed up in the recent-activity
    feed, never in the open-positions table). Now unions both, each row
    stamped with its OWN session ('NY' / 'LONDON') rather than every row
    getting relabeled with whatever the CURRENT wall-clock hour happens to
    be — that was harmless before (only NY rows existed here, and the
    dashboard is mostly glanced at during NY hours anyway) but was never
    actually correct. (2) Added the latest Crest Watch reading per position
    (risk_score/streak/reasoning) — most rows will show "not yet checked"
    since it only fires past a 100pt peak, that's expected, not a bug.
    """
    now_et  = datetime.now(tz=ET)
    h       = now_et.hour
    session = 'LONDON' if 3 <= h < 9 else ('NY' if 9 <= h < 16 else 'OFF')

    ny_rows, london_rows, crest = [], [], {}
    try:
        with _db() as c:
            ny_rows = c.execute("""
                SELECT id, symbol, contract, entry_date, entry_time, entry_price,
                       contracts, side, target_price, stop_price, setup_type,
                       account_mode
                FROM futures_trades
                WHERE status = 'OPEN'
                ORDER BY entry_time DESC
            """).fetchall()
            london_rows = c.execute("""
                SELECT id, entry_date, entry_time, entry, contracts, side,
                       target, sl_current, setup, account_mode
                FROM london_trades
                WHERE status = 'OPEN'
                ORDER BY entry_time DESC
            """).fetchall()
            # Latest scored (risk_score IS NOT NULL — skip failed-check rows)
            # Crest Watch reading per (session, trade_id). One query, not N+1.
            crows = c.execute("""
                SELECT trade_id, session, risk_score, streak, verdict, reasoning, checked_at
                FROM futures_thesis_check
                WHERE risk_score IS NOT NULL AND id IN (
                    SELECT MAX(id) FROM futures_thesis_check
                    WHERE risk_score IS NOT NULL GROUP BY trade_id, session
                )
            """).fetchall()
            crest = {(r['session'], r['trade_id']): r for r in crows}
    except Exception:
        pass

    live_ibkr = _bridge('/futures/position') or []
    live_tc   = _bridge('/futures/position', url=TC_BRIDGE_URL) or []
    price_maps = {
        'IBKR': {p.get('symbol'): p.get('market_price') for p in live_ibkr},
        'TC':   {p.get('symbol'): p.get('market_price') for p in live_tc},
    }

    def _crest_watch(sess, trade_id):
        r = crest.get((sess, trade_id))
        if not r:
            return None
        return {'risk_score': r['risk_score'], 'streak': r['streak'] or 1,
                'verdict': r['verdict'], 'reasoning': r['reasoning'],
                'checked_at': r['checked_at']}


    # ── Exit Map (Sep 3 2026) ────────────────────────────────────────────────
    # `target_price` on these rows is BASE_TARGET_PTS = 1500pts away. It has
    # fired ZERO times in 951 trades over 5.5yr and the best trade this book has
    # ever produced ran 378pts — it exists only as a disaster cap and as the
    # numerator of the MIN_RR gate. Showing it as "the target" tells you nothing
    # about how close a position is to actually being closed.
    # The traders publish the REAL distances (trail stop, reversal exit, partial,
    # no-move, EOD) each monitor cycle. We read the file rather than recompute,
    # because the reversal-exit level depends on the running peak which only
    # exists in the trader's memory.
    def _exit_map(account_mode, trade_id):
        try:
            p = os.path.join(BASE_DIR, 'logs', 'heartbeat', f'exitmap_{account_mode}.json')
            with open(p) as fh:
                d = json.load(fh)
            age = (datetime.now(ET) - datetime.fromisoformat(d['ts'])).total_seconds()
            if age > 180:            # stale -> say so rather than show old distances
                return {'stale': True, 'age_s': int(age), 'exits': []}
            for pos in d.get('positions', []):
                if pos.get('trade_id') == trade_id:
                    return {'stale': False, 'age_s': int(age), 'exits': pos.get('exits', [])}
        except Exception:
            pass
        return None

    def _unreal(ep, mp, qty, is_short):
        if mp is None or not ep:
            return None  # bridge down/reconnecting — render "---", not misleading $0
        pnl_pts = (ep - mp) if is_short else (mp - ep)
        return round(pnl_pts / TICK_SIZE * TICK_VALUE * qty, 2)

    result = []
    for row in ny_rows:
        sym, ep, qty = row['symbol'], row['entry_price'] or 0, row['contracts'] or 1
        is_short = row['side'] == 'SHORT'
        mp = price_maps.get(row['account_mode'], {}).get(sym)
        result.append({
            'id':             row['id'],
            'symbol':         sym,
            'contract_month': row['contract'],
            'entry_date':     row['entry_date'],
            'entry_time':     row['entry_time'],
            'side':           row['side'],
            'qty':            qty,
            'entry_price':    ep,
            'market_price':   mp,
            'target_price':   row['target_price'],
            'stop_price':     row['stop_price'],
            'setup_type':     row['setup_type'],
            'unreal_pnl':     _unreal(ep, mp, qty, is_short),
            'session':        'NY',
            'account_mode':   row['account_mode'],
            'status':         'OK',
            'crest_watch':    _crest_watch('NY', row['id']),
            'exit_map':       _exit_map(row['account_mode'], row['id']),
        })
    for row in london_rows:
        sym, ep, qty = 'MNQ', row['entry'] or 0, row['contracts'] or 1
        is_short = row['side'] == 'SHORT'
        mp = price_maps.get(row['account_mode'], {}).get(sym)
        result.append({
            'id':             row['id'],
            'symbol':         sym,
            'contract_month': None,
            'entry_date':     row['entry_date'],
            'entry_time':     row['entry_time'],
            'side':           row['side'],
            'qty':            qty,
            'entry_price':    ep,
            'market_price':   mp,
            'target_price':   row['target'],
            'stop_price':     row['sl_current'],
            'setup_type':     row['setup'],
            'unreal_pnl':     _unreal(ep, mp, qty, is_short),
            'session':        'LONDON',
            'account_mode':   row['account_mode'],
            'status':         'OK',
            'crest_watch':    _crest_watch('LONDON', row['id']),
        })

    result.sort(key=lambda r: (r['entry_date'] or '', r['entry_time'] or ''), reverse=True)
    return result, session


def get_today_summary():
    """Aug 9 2026: futures split into fut_ibkr / fut_tc — two real, separate
    accounts with different prop rules (DLL/MLL/consistency), each combining
    its own NY + London P&L. Previously blended IBKR+TC together silently."""
    today = datetime.now(tz=ET).strftime('%Y-%m-%d')
    eq      = {'pnl': 0, 'trades': 0, 'wins': 0, 'open': 0, 'wr': None}
    opt     = {'pnl': 0, 'trades': 0, 'open': 0, 'theta': 0, 'delta': 0}
    fut_ibkr = {'pnl': 0, 'trades': 0, 'wins': 0, 'wr': None}
    fut_tc   = {'pnl': 0, 'trades': 0, 'wins': 0, 'wr': None}

    try:
        with _db() as c:
            rows = c.execute(
                "SELECT pnl FROM trades WHERE exit_date=? AND setup_type!='RECONCILED'",
                (today,)
            ).fetchall()
            eq['pnl']    = round(sum(r['pnl'] or 0 for r in rows), 2)
            eq['trades'] = len(rows)
            eq['wins']   = sum(1 for r in rows if (r['pnl'] or 0) > 0)
            eq['wr']     = round(eq['wins'] / eq['trades'] * 100, 1) if eq['trades'] else None

            eq['open'] = c.execute(
                "SELECT COUNT(*) as n FROM trades WHERE status='OPEN' AND setup_type!='RECONCILED'"
            ).fetchone()['n']
            eq['books'] = [{'name': 'Day Trader', 'pnl': eq['pnl'], 'trades': eq['trades'],
                            'open': eq['open']}]

            # Sep 22 2026: this card read the `trades` table ONLY, so it reported
            # the Day Trader book as if it were the whole equity side. Wave Rider,
            # Contrarian and Clockwork trade the SAME real account through their own
            # tables and were invisible here — on the day this was found the card
            # showed +$70.57 while the true equity figure was +$21.52, because
            # Clockwork's -$49.05 simply was not counted. The per-engine breakdown
            # has existed since Sep 11 in the ENGINES scoreboard; the headline never
            # caught up. `books` carries the composition so a book can never again go
            # missing from this number without it being visible on the card itself.
            for _name, _tbl in (('Wave Rider', 'wave_trades'),
                                ('Contrarian', 'contrarian_trades'),
                                ('Clockwork',  'overnight_trades')):
                try:
                    _cols = {r[1] for r in c.execute(f'PRAGMA table_info({_tbl})')}
                    if not _cols:
                        continue          # table not created yet — engine never ran
                    _mode = " AND mode='LIVE'" if 'mode' in _cols else ''
                    _cl = c.execute(
                        f"SELECT pnl FROM {_tbl} WHERE exit_date=? AND status='CLOSED'{_mode}",
                        (today,)).fetchall()
                    _op = c.execute(
                        f"SELECT COUNT(*) AS n FROM {_tbl} WHERE status IN "
                        f"('OPEN','PENDING_ENTRY','PENDING_EXIT'){_mode}").fetchone()['n']
                    _pnl = round(sum(r['pnl'] or 0 for r in _cl), 2)
                    eq['pnl']    = round(eq['pnl'] + _pnl, 2)
                    eq['trades'] += len(_cl)
                    eq['wins']   += sum(1 for r in _cl if (r['pnl'] or 0) > 0)
                    eq['open']   += _op
                    eq['books'].append({'name': _name, 'pnl': _pnl,
                                        'trades': len(_cl), 'open': _op})
                except Exception:
                    continue              # one bad table must not blank the whole card
            eq['wr'] = round(eq['wins'] / eq['trades'] * 100, 1) if eq['trades'] else None

            opt_open = c.execute(
                "SELECT delta_entry FROM options_trades "
                "WHERE status NOT IN ('CLOSED','EXPIRED','CANCELLED')"
            ).fetchall()
            opt['open']  = len(opt_open)
            opt['theta'] = 0
            opt['delta'] = round(sum(r['delta_entry'] or 0 for r in opt_open), 3)

            opt_closed = c.execute(
                "SELECT exit_value - premium_paid as pnl FROM options_trades "
                "WHERE exit_date=? AND exit_value IS NOT NULL", (today,)
            ).fetchall()
            opt['pnl']    = round(sum(r['pnl'] or 0 for r in opt_closed), 2)
            opt['trades'] = len(opt_closed)

            for mode, bucket in (('IBKR', fut_ibkr), ('TC', fut_tc)):
                ny_rows = c.execute(
                    "SELECT pnl FROM futures_trades WHERE exit_date=? "
                    "AND setup_type != 'RECONCILED' AND account_mode=?",
                    (today, mode)
                ).fetchall()
                lon_rows = c.execute(
                    "SELECT pnl FROM london_trades WHERE exit_date=? AND account_mode=?",
                    (today, mode)
                ).fetchall()
                all_fut = list(ny_rows) + list(lon_rows)
                bucket['pnl']    = round(sum(r['pnl'] or 0 for r in all_fut), 2)
                bucket['trades'] = len(all_fut)
                bucket['wins']   = sum(1 for r in all_fut if (r['pnl'] or 0) > 0)
                bucket['wr']     = round(bucket['wins'] / bucket['trades'] * 100, 1) if bucket['trades'] else None
    except Exception:
        pass

    return eq, opt, fut_ibkr, fut_tc


def get_pnl_by_book(sessions=15):
    """Daily closed P&L per vertical (equity / options / futures incl London)
    for the last `sessions` dates that had any closed trade."""
    cutoff = (datetime.now(tz=ET) - timedelta(days=sessions + 14)).strftime('%Y-%m-%d')
    daily = {}   # date -> {book: pnl}

    def add(rows, book):
        for d, p in rows:
            if d:
                bucket = daily.setdefault(d, {})
                bucket[book] = round(bucket.get(book, 0) + (p or 0), 2)

    try:
        with _db() as c:
            add(c.execute(
                "SELECT exit_date, SUM(pnl) FROM trades "
                "WHERE exit_date>=? AND setup_type!='RECONCILED' GROUP BY exit_date",
                (cutoff,)).fetchall(), 'equity')
            # Sep 22 2026: the 'equity' bar was the Day Trader table alone. Wave
            # Rider, Contrarian and Clockwork trade the same real account and were
            # missing from this chart entirely — the same omission found in the
            # Today card. Folded into 'equity' rather than given their own series:
            # this chart is P&L per VERTICAL, and per-engine detail already lives
            # in the ENGINES scoreboard.
            for _tbl in ('wave_trades', 'contrarian_trades', 'overnight_trades'):
                try:
                    _cols = {r[1] for r in c.execute(f'PRAGMA table_info({_tbl})')}
                    if not _cols:
                        continue
                    _mode = " AND mode='LIVE'" if 'mode' in _cols else ''
                    add(c.execute(
                        f"SELECT exit_date, SUM(pnl) FROM {_tbl} "
                        f"WHERE exit_date>=? AND status='CLOSED'{_mode} GROUP BY exit_date",
                        (cutoff,)).fetchall(), 'equity')
                except Exception:
                    continue
            add(c.execute(
                "SELECT exit_date, SUM(exit_value - premium_paid) FROM options_trades "
                "WHERE exit_date>=? AND exit_value IS NOT NULL GROUP BY exit_date",
                (cutoff,)).fetchall(), 'options')
            # Aug 9 2026: 'futures' split into futures_ibkr / futures_tc — each
            # combines its own account's NY + London leg. Previously London had
            # no account_mode filter and silently blended into the IBKR-only bar.
            add(c.execute(
                "SELECT exit_date, SUM(pnl) FROM futures_trades "
                "WHERE exit_date>=? AND setup_type!='RECONCILED' AND account_mode='IBKR' "
                "GROUP BY exit_date",
                (cutoff,)).fetchall(), 'futures_ibkr')
            add(c.execute(
                "SELECT exit_date, SUM(pnl) FROM london_trades "
                "WHERE exit_date>=? AND account_mode='IBKR' GROUP BY exit_date",
                (cutoff,)).fetchall(), 'futures_ibkr')
            add(c.execute(
                "SELECT exit_date, SUM(pnl) FROM futures_trades "
                "WHERE exit_date>=? AND setup_type!='RECONCILED' AND account_mode='TC' "
                "GROUP BY exit_date",
                (cutoff,)).fetchall(), 'futures_tc')
            add(c.execute(
                "SELECT exit_date, SUM(pnl) FROM london_trades "
                "WHERE exit_date>=? AND account_mode='TC' GROUP BY exit_date",
                (cutoff,)).fetchall(), 'futures_tc')
    except Exception:
        return []

    dates = sorted(daily.keys())[-sessions:]
    return [{
        'date':         d,
        'equity':       daily[d].get('equity'),
        'options':      daily[d].get('options'),
        'futures_ibkr': daily[d].get('futures_ibkr'),
        'futures_tc':   daily[d].get('futures_tc'),
        'total':        round(sum(v for v in daily[d].values() if v is not None), 2),
    } for d in dates]


def get_scorecard(since_date=None, days=21):
    """Per-book aggregates over the chart window: trades, WR, P&L, avg,
    best/worst day. Futures NY and London reported separately (different
    strategies, different sessions) AND by account (Aug 9 2026 — IBKR and TC
    are two real, separate accounts with different prop rules; London now
    trades on both, so it needs the same account split NY already had)."""
    cutoff = since_date or (datetime.now(tz=ET) - timedelta(days=days)).strftime('%Y-%m-%d')
    books = [
        # Sep 22 2026: 'Equity' was the Day Trader table alone, so three live books
        # that trade the same real account had no row here at all. Renamed to match
        # the ENGINES scoreboard, and the others given their own rows — this table
        # exists to COMPARE books, so folding them together would defeat it.
        ('Day Trader',    "SELECT exit_date, pnl FROM trades "
                          "WHERE exit_date>=? AND setup_type!='RECONCILED' AND pnl IS NOT NULL"),
        ('Wave Rider',    "SELECT exit_date, pnl FROM wave_trades "
                          "WHERE exit_date>=? AND status='CLOSED' AND mode='LIVE' "
                          "AND pnl IS NOT NULL"),
        ('Contrarian',    "SELECT exit_date, pnl FROM contrarian_trades "
                          "WHERE exit_date>=? AND status='CLOSED' AND mode='LIVE' "
                          "AND pnl IS NOT NULL"),
        ('Clockwork',     "SELECT exit_date, pnl FROM overnight_trades "
                          "WHERE exit_date>=? AND status='CLOSED' AND mode='LIVE' "
                          "AND pnl IS NOT NULL"),
        ('Options',       "SELECT exit_date, exit_value - premium_paid FROM options_trades "
                          "WHERE exit_date>=? AND exit_value IS NOT NULL"),
        ('IBKR NY',       "SELECT exit_date, pnl FROM futures_trades "
                          "WHERE exit_date>=? AND setup_type!='RECONCILED' AND pnl IS NOT NULL "
                          "AND account_mode='IBKR'"),
        ('IBKR London',   "SELECT exit_date, pnl FROM london_trades "
                          "WHERE exit_date>=? AND pnl IS NOT NULL AND account_mode='IBKR'"),
        ('TC NY',         "SELECT exit_date, pnl FROM futures_trades "
                          "WHERE exit_date>=? AND setup_type!='RECONCILED' AND pnl IS NOT NULL "
                          "AND account_mode='TC'"),
        ('TC London',     "SELECT exit_date, pnl FROM london_trades "
                          "WHERE exit_date>=? AND pnl IS NOT NULL AND account_mode='TC'"),
    ]
    out = []
    try:
        with _db() as c:
            for name, sql in books:
                try:
                    rows = c.execute(sql, (cutoff,)).fetchall()
                except Exception:
                    out.append({'book': name, 'n': 0})   # table absent — engine never ran
                    continue
                pnls = [r[1] or 0 for r in rows]
                if not pnls:
                    out.append({'book': name, 'n': 0})
                    continue
                by_day = {}
                for d, p in rows:
                    by_day[d] = round(by_day.get(d, 0) + (p or 0), 2)
                best  = max(by_day.items(), key=lambda kv: kv[1])
                worst = min(by_day.items(), key=lambda kv: kv[1])
                wins  = sum(1 for p in pnls if p > 0)
                out.append({
                    'book': name, 'n': len(pnls),
                    'wr':   round(wins / len(pnls) * 100),
                    'pnl':  round(sum(pnls), 2),
                    'avg':  round(sum(pnls) / len(pnls), 2),
                    'best':  {'date': best[0],  'pnl': best[1]},
                    'worst': {'date': worst[0], 'pnl': worst[1]},
                })
    except Exception:
        pass
    return out


def get_alerts(eq_pos, opt_pos):
    alerts = []
    now_et = datetime.now(tz=ET)

    for p in eq_pos:
        if p['status'] == 'REVIEW':
            alerts.append({
                'level': 'HIGH', 'type': 'NEAR_STOP', 'vertical': 'EQUITY',
                'symbol': p['symbol'], 'time': now_et.strftime('%H:%M'),
                'message': f"{p['symbol']} critically close to stop "
                           f"(${p['current_price']:.2f} vs stop ${p['stop_price']:.2f})"
            })
        elif p['status'] == 'WARN':
            alerts.append({
                'level': 'WARN', 'type': 'NEAR_STOP', 'vertical': 'EQUITY',
                'symbol': p['symbol'], 'time': now_et.strftime('%H:%M'),
                'message': f"{p['symbol']} approaching stop — monitor closely"
            })

    for p in opt_pos:
        if p['status'] == 'REVIEW':
            alerts.append({
                'level': 'HIGH', 'type': 'OPT_LOSS', 'vertical': 'OPTIONS',
                'symbol': p['symbol'], 'time': now_et.strftime('%H:%M'),
                'message': f"{p['symbol']} {p['strategy']} at {p['pnl_pct']:.0f}% loss — circuit breaker zone"
            })
        elif p['earnings_days'] is not None and 0 < p['earnings_days'] <= 5:
            alerts.append({
                'level': 'WARN', 'type': 'EARNINGS_RISK', 'vertical': 'OPTIONS',
                'symbol': p['symbol'], 'time': now_et.strftime('%H:%M'),
                'message': f"{p['symbol']} earnings in {p['earnings_days']}d — position exposed"
            })

    try:
        today = now_et.strftime('%Y-%m-%d')
        with _db() as c:
            regimes = c.execute(
                'SELECT DISTINCT regime, scan_time FROM scan_log '
                'WHERE scan_date=? ORDER BY id DESC LIMIT 4', (today,)
            ).fetchall()
            if len(regimes) >= 2 and regimes[0]['regime'] != regimes[1]['regime']:
                alerts.append({
                    'level': 'INFO', 'type': 'REGIME_CHANGE', 'vertical': 'MARKET',
                    'symbol': 'MARKET', 'time': regimes[0]['scan_time'],
                    'message': f"Regime changed: {regimes[1]['regime']} → {regimes[0]['regime']}"
                })
    except Exception:
        pass

    order = {'HIGH': 0, 'WARN': 1, 'INFO': 2}
    alerts.sort(key=lambda x: order.get(x['level'], 3))
    return alerts


def get_activity(sessions=5):
    cutoff = (datetime.now(tz=ET) - timedelta(days=sessions + 2)).strftime('%Y-%m-%d')
    result = []
    try:
        with _db() as c:
            # Equity
            rows = c.execute("""
                SELECT 'ENTRY' as ev, 'EQUITY' as vert, symbol, entry_date as dt,
                       entry_time as tm, entry_price as price, setup_type as setup,
                       sector, side, shares, NULL as pnl, NULL as reason
                FROM trades WHERE entry_date >= ? AND setup_type != 'RECONCILED'
                UNION ALL
                SELECT 'EXIT', 'EQUITY', symbol, exit_date, exit_time, exit_price,
                       setup_type, sector, side, shares, pnl, exit_reason
                FROM trades WHERE exit_date >= ? AND exit_date IS NOT NULL
                  AND setup_type != 'RECONCILED'
            """, (cutoff, cutoff)).fetchall()
            result.extend([dict(r) for r in rows])

            # Options
            rows = c.execute("""
                SELECT 'ENTRY' as ev, 'OPTIONS' as vert, symbol, entry_date as dt,
                       NULL as tm, net_debit as price, strategy as setup,
                       NULL as sector, 'LONG' as side, contracts as shares,
                       NULL as pnl, NULL as reason
                FROM options_trades WHERE entry_date >= ?
                UNION ALL
                SELECT 'EXIT', 'OPTIONS', symbol, exit_date, NULL, exit_value,
                       strategy, NULL, 'LONG', contracts,
                       exit_value - premium_paid, exit_reason
                FROM options_trades WHERE exit_date >= ? AND exit_date IS NOT NULL
            """, (cutoff, cutoff)).fetchall()
            result.extend([dict(r) for r in rows])

            # Futures (NY/TC sessions)
            rows = c.execute("""
                SELECT 'ENTRY' as ev, 'FUTURES' as vert, symbol, entry_date as dt,
                       entry_time as tm, entry_price as price, setup_type as setup,
                       session as sector, side, contracts as shares, NULL as pnl, NULL as reason
                FROM futures_trades WHERE entry_date >= ?
                UNION ALL
                SELECT 'EXIT', 'FUTURES', symbol, exit_date, exit_time, exit_price,
                       setup_type, session, side, contracts, pnl, exit_reason
                FROM futures_trades WHERE exit_date >= ? AND exit_date IS NOT NULL
            """, (cutoff, cutoff)).fetchall()
            result.extend([dict(r) for r in rows])

            # London session futures
            rows = c.execute("""
                SELECT 'ENTRY' as ev, 'FUTURES' as vert, 'MNQ' as symbol, entry_date as dt,
                       entry_time as tm, entry as price, setup as setup,
                       'LONDON' as sector, side, contracts as shares, NULL as pnl, NULL as reason
                FROM london_trades WHERE entry_date >= ?
                UNION ALL
                SELECT 'EXIT', 'FUTURES', 'MNQ', exit_date, exit_time, exit_price,
                       setup, 'LONDON', side, contracts, pnl, exit_reason
                FROM london_trades WHERE exit_date >= ? AND exit_date IS NOT NULL
            """, (cutoff, cutoff)).fetchall()
            result.extend([dict(r) for r in rows])

    except Exception:
        pass

    result.sort(key=lambda x: (x.get('dt') or '', x.get('tm') or ''), reverse=True)
    return result[:100]


def get_calendar(opt_pos, eq_pos):
    now_et = datetime.now(tz=ET)
    today  = now_et.date()
    cutoff = today + timedelta(days=30)

    earnings = []
    eq_syms  = {p['symbol'] for p in eq_pos}
    opt_syms = {p['symbol'] for p in opt_pos}
    all_syms = eq_syms | opt_syms

    try:
        with _db() as c:
            if all_syms:
                ph = ','.join('?' * len(all_syms))
                rows = c.execute(
                    f"SELECT symbol, earnings_date FROM earnings_calendar "
                    f"WHERE symbol IN ({ph}) AND earnings_date >= ? "
                    f"ORDER BY earnings_date ASC",
                    list(all_syms) + [today.strftime('%Y-%m-%d')]
                ).fetchall()
                for r in rows:
                    try:
                        ed = datetime.strptime(r['earnings_date'], '%Y-%m-%d').date()
                        if ed <= cutoff:
                            verts = []
                            if r['symbol'] in eq_syms:  verts.append('EQ')
                            if r['symbol'] in opt_syms: verts.append('OPT')
                            days_to = (ed - today).days
                            earnings.append({
                                'symbol':   r['symbol'],
                                'date':     r['earnings_date'],
                                'days_to':  days_to,
                                'verticals': ' + '.join(verts),
                                'urgency':  'HIGH' if days_to <= 7 else ('WARN' if days_to <= 14 else 'INFO'),
                            })
                    except Exception:
                        pass
    except Exception:
        pass

    macro = []
    for evt in MACRO_EVENTS:
        try:
            ed = datetime.strptime(evt['date'], '%Y-%m-%d').date()
            if today <= ed <= cutoff:
                macro.append({**evt, 'days_to': (ed - today).days})
        except Exception:
            pass
    macro.sort(key=lambda x: x['date'])

    return earnings, macro


def get_sector_grades():
    try:
        with _db() as c:
            rows = c.execute(
                'SELECT sector, grade, wr_30d, trade_count, updated_at '
                'FROM sector_grades ORDER BY '
                "CASE grade WHEN 'STRONG' THEN 0 WHEN 'NEUTRAL' THEN 1 ELSE 2 END, wr_30d DESC"
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


# Gate code → (Glossary name, plain-English tooltip). GLOSSARY.md is the authority;
# raw codes stay in the DB/logs, the dashboard just translates for reading.
GATE_INFO = {
    'REGIME':     ('Weather',        'Weather Report (trend regime) not confirmed for this side — needs 3 consecutive 5-min bars'),
    'GRADE':      ('Setup Grade',    'Signal scored below the A+ entry bar'),
    'RVOL_ENTRY': ('Volume Pulse',   'Session volume too thin vs 20-day norm (hard floor 0.70, full pass 0.85)'),
    'HERO':       ('Trend Jury',     'Trend Jury vote below the entry threshold for today\'s weather'),
    'HTF':        ('Higher-TF',      '30-min higher-timeframe trend disagrees with the entry direction'),
    'OVN_SKIP':   ('Overnight Veto', 'Ambiguous overnight positioning — INFO-ONLY since Jul 18, no longer blocks'),
    'RVOL':       ('Dead Tape',      'Scan skipped — market volume below the dead-tape floor'),
    'A_EXT':      ('Extension',      'Price too extended from VWAP at signal time'),
    'DLL':        ('Loss Halt',      'Daily loss limit reached — entries halted for the day'),
}


def get_system_health():
    """System-health panel (Jul 18 2026): Book Health, signal funnel, Trade Cop,
    Mirror Book. The 'is the machine healthy and what is it seeing' view."""
    out = {'books': {}, 'funnel': {}, 'parity': {}, 'shadow': {}, 'universe': None}
    today = datetime.now(tz=ET).strftime('%Y-%m-%d')
    # Jul 22 2026: must mirror auto_trader.py's BOOK_HEALTH_RESET_DATE exactly, or this
    # panel keeps averaging the pre-reset (poisoned) window forever — see auto_trader.py
    # comment near BOOK_HEALTH_RESET_DATE for the full story. Found drifted same day the
    # options_trader.py twin was found drifted; same root cause (duplicated query, no
    # shared source of truth).
    BOOK_HEALTH_RESET_DATE = '2026-07-22'
    try:
        with _db() as c:
            # Book Health — same trailing-10-day drift formula as auto_trader
            for d in ('LONG', 'SHORT'):
                days = [r[0] for r in c.execute(
                    """SELECT DISTINCT scan_date FROM scan_log
                       WHERE grade='A+' AND direction=? AND scan_date<? AND scan_date>=?
                         AND enriched=1
                         AND actual_day_pct IS NOT NULL AND intra_chg IS NOT NULL
                       ORDER BY scan_date DESC LIMIT 10""",
                    (d, today, BOOK_HEALTH_RESET_DATE)).fetchall()]
                if len(days) < 4:
                    out['books'][d] = {'state': 'COLD START', 'drift': None}
                    continue
                q = ','.join('?' * len(days))
                rows = c.execute(
                    f"""SELECT actual_day_pct - intra_chg FROM scan_log
                        WHERE grade='A+' AND direction=? AND enriched=1
                          AND actual_day_pct IS NOT NULL AND intra_chg IS NOT NULL
                          AND scan_date IN ({q})""", (d, *days)).fetchall()
                if len(rows) < 30:
                    out['books'][d] = {'state': 'COLD START', 'drift': None}
                    continue
                drifts = [(-r[0] if d == 'SHORT' else r[0]) for r in rows]
                h = sum(drifts) / len(drifts)
                out['books'][d] = {
                    'state': 'ON' if h > 0 else 'OFF',
                    'drift': round(h, 2), 'n': len(rows),
                    'desc': (f"Own A+ {d} signals {'gained' if h > 0 else 'faded'} "
                             f"{h:+.2f}% per signal after firing, over the last 10 sessions "
                             f"({len(rows)} signals). Book trades only while this is positive."),
                }
            # Signal funnel — equity A+ counts + futures entries/blocks today
            eq = dict(c.execute(
                """SELECT direction, COUNT(*) FROM scan_log
                   WHERE scan_date=? AND grade='A+' GROUP BY direction""",
                (today,)).fetchall())
            fut = c.execute(
                """SELECT gate, COUNT(*) FROM gate_blocks
                   WHERE date(ts)=? AND system='IBKR'
                     AND gate NOT IN ('SHADOW_RAW', 'ENTER')
                   GROUP BY gate ORDER BY 2 DESC LIMIT 6""", (today,)).fetchall()
            entered = c.execute(
                """SELECT COUNT(*) FROM gate_blocks
                   WHERE date(ts)=? AND system='IBKR' AND gate='ENTER'""",
                (today,)).fetchone()[0]
            out['funnel'] = {'eq_aplus_long': eq.get('LONG', 0),
                             'eq_aplus_short': eq.get('SHORT', 0),
                             'fut_entered': entered,
                             'fut_gates': [
                                 [g, n,
                                  GATE_INFO.get(g, (g, ''))[0],
                                  GATE_INFO.get(g, (g, 'Unmapped gate — see GLOSSARY.md'))[1]]
                                 for g, n in fut]}
            # Mirror Book (shadow fish-net) — cumulative + last 14 days
            row = c.execute(
                """SELECT COUNT(*), ROUND(SUM(pnl_pts),1),
                          ROUND(SUM(CASE WHEN date(entry_ts) >= date('now','-14 day')
                                    THEN pnl_pts ELSE 0 END),1)
                   FROM shadow_fishnet""").fetchone()
            out['shadow'] = {'n': row[0] or 0, 'pts_total': row[1] or 0,
                             'pts_14d': row[2] or 0}
            # Options (Jul 18 2026 redesign) — book-gated funnel + what-if ledger
            opt = {}
            opt['open'] = [
                {'symbol': r[0], 'strategy': r[1], 'premium': r[2],
                 'entry_date': r[3]}
                for r in c.execute(
                    """SELECT symbol, strategy, premium_paid, entry_date
                       FROM options_trades WHERE status='OPEN'""").fetchall()]
            r = c.execute(
                """SELECT COUNT(*), SUM(verdict='ENTER'), SUM(verdict='SKIP')
                   FROM opt_calc_log WHERE substr(run_at,1,10)=?""",
                (today,)).fetchone()
            opt['calcs_today'] = {'total': r[0] or 0, 'enter': r[1] or 0,
                                  'skip': r[2] or 0}
            # What the skipped/logged suggestions would have done (last 14d)
            r = c.execute(
                """SELECT COUNT(*), ROUND(SUM(whatif_pnl),0), SUM(whatif_pnl > 0)
                   FROM opt_suggestions
                   WHERE whatif_pnl IS NOT NULL
                     AND date(suggested_at) >= date('now','-14 day')""").fetchone()
            opt['whatif_14d'] = {'n': r[0] or 0, 'pnl': r[1] or 0,
                                 'wins': r[2] or 0}
            r = c.execute(
                """SELECT COUNT(*), ROUND(SUM(exit_value - premium_paid),0)
                   FROM options_trades WHERE status='CLOSED'
                     AND exit_date >= date('now','-14 day')""").fetchone()
            opt['closed_14d'] = {'n': r[0] or 0, 'pnl': r[1] or 0}
            # Book-level Greeks — latest watchman snapshot (Aug 3 2026).
            # Only shown when fresh (today) so a stale row can't masquerade
            # as live exposure.
            try:
                r = c.execute(
                    """SELECT ts, positions, total_value, net_delta, net_theta, net_vega
                       FROM options_book_greeks ORDER BY id DESC LIMIT 1""").fetchone()
                if r and r[0][:10] == today:
                    opt['book_greeks'] = {
                        'ts': r[0][11:16], 'positions': r[1], 'total_value': r[2],
                        'net_delta': r[3], 'net_theta': r[4], 'net_vega': r[5]}
            except Exception:
                pass
            # Concentration: % of deployed options premium per symbol
            try:
                rows_c = c.execute(
                    """SELECT symbol, SUM(premium_paid) FROM options_trades
                       WHERE status='OPEN' GROUP BY symbol""").fetchall()
                tot_prem = sum(x[1] or 0 for x in rows_c)
                if tot_prem > 0:
                    opt['concentration'] = [
                        {'symbol': x[0], 'pct': round((x[1] or 0) / tot_prem * 100)}
                        for x in sorted(rows_c, key=lambda y: -(y[1] or 0))]
            except Exception:
                pass
            out['options'] = opt
            # Fish Finder DECOMMISSIONED Aug 15 2026 (failed the Alpha Factory tests). Its
            # System Health block was removed to stop showing stale/dead data. Its validated
            # replacement, Wave Rider, has its own live view on the /factory page.
            # Field Report (market_context.py) — log-only pre-market brief
            try:
                r = c.execute(
                    """SELECT brief_date, stance, confidence, event_risk,
                              themes, one_line
                       FROM market_brief ORDER BY brief_date DESC LIMIT 1""").fetchone()
                if r:
                    out['field_report'] = {
                        'date': r[0], 'stance': r[1], 'confidence': r[2],
                        'event_risk': r[3],
                        'themes': json.loads(r[4] or '[]'),
                        'one_line': r[5],
                    }
            except Exception:
                pass
    except Exception:
        pass
    # Trade Cop — last parity verdict, decoded into a readable sentence
    try:
        import re as _re
        with open(os.path.join(BASE_DIR, 'logs', 'parity.log')) as f:
            lines = [l.strip() for l in f if 'parity ' in l and '→' in l]
        if lines:
            last = lines[-1]
            status = 'DIVERGENCE' if 'DIVERGENCE' in last else 'OK'
            detail = last.split('] ')[-1]
            m = _re.search(r'parity (\S+) .*?sim=(\d+) live=(\d+) matched=(\d+)', last)
            if m:
                day, sim, live, matched = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
                if status == 'OK':
                    if sim == 0 and live == 0:
                        friendly = f"{day}: replay and live both took 0 trades — in agreement"
                    else:
                        friendly = f"{day}: replay matched all live trades ({matched}/{live})"
                else:
                    friendly = (f"{day}: replay and live DISAGREE — sim {sim} vs live {live} "
                                f"trades, only {matched} matched. Check logs/parity.log before "
                                f"trusting any backtest.")
            else:
                friendly = detail
            out['parity'] = {'status': status, 'detail': detail, 'friendly': friendly}
    except Exception:
        pass
    try:
        from auto_trader import FULL_UNIVERSE
        out['universe'] = len(FULL_UNIVERSE)
    except Exception:
        pass
    return out


# ── Routes ──────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/logo-preview')
def logo_preview():
    return render_template('logo_preview.html')


@app.route('/glossary')
def glossary():
    """Aug 9 2026 — user kept re-asking what Trade Cop/Mirror Book/sector-grade
    refresh cadence/etc actually mean. Rather than re-explain each time, a
    standing reference page sourced from the same terms GLOSSARY.md already
    documents, reorganized by where they appear on the dashboard."""
    return render_template('glossary.html')


def _turbo_ladder(pick):
    """Live strike ladder around a Turbo structure's strikes (best-effort, yfinance).
    Returns rows near the held legs so the /factory page shows exactly what Turbo picked."""
    try:
        import sys as _sys
        _op = os.path.join(BASE_DIR, 'options')
        if _op not in _sys.path:
            _sys.path.insert(0, _op)
        from options_trader import _yf_option_chain
        right = 'P' if pick.get('kind') == 'CREDIT_PUT' else 'C'
        df = _yf_option_chain(pick['symbol'], pick['expiry'], right)
        if df is None or len(df) == 0:
            return []
        held = {float(pick['long_strike']), float(pick['short_strike'])}
        lo, hi = min(held) * 0.90, max(held) * 1.10
        sub = df[(df['strike'] >= lo) & (df['strike'] <= hi)].sort_values('strike')
        rows = []
        for _, r in sub.iterrows():
            k = float(r['strike'])
            rows.append({
                'strike': k,
                'bid':  round(float(r.get('bid') or 0), 2),
                'ask':  round(float(r.get('ask') or 0), 2),
                'last': round(float(r.get('lastPrice') or 0), 2),
                'iv':   round(float(r.get('impliedVolatility') or 0) * 100),
                'vol':  int(r.get('volume') or 0),
                'oi':   int(r.get('openInterest') or 0),
                'held': k in held,
            })
        return rows
    except Exception:
        return []


# Display names + a plain-English analogy shown INLINE in the table, so the scoreboard is
# readable without a trip to /glossary. These are DISPLAY labels only — code identifiers, DB
# tables and launchd names are never renamed (GLOSSARY.md rule). Analogies for Wave Rider and
# Contrarian are taken from GLOSSARY.md §8 verbatim; Day Trader and Clockwork had none, so
# they are named here in the same style.
ENGINE_SPECS = [
    # display name,  analogy (shown inline),                 table,               mode col?, detail (tooltip)
    ('Day Trader',   'in by morning, out by the close',       'trades',            False,
     'The original intraday book: catalyst and momentum names, closed the same day at 15:45.'),
    ('Wave Rider',   'rides a stock already moving',          'wave_trades',       True,
     'Momentum swing — buys WILD names that popped, holds 3 days, 8% stop.'),
    ('Contrarian',   'buys what just fell hardest',           'contrarian_trades', True,
     'Mean reversion — buys the biggest 3-day fallers, holds 5 days, 15% stop. Long-only, so '
     'judge it on alpha vs the tide rather than raw P&L.'),
    ('Clockwork',    'buys the close, sells the open',        'overnight_trades',  True,
     'Overnight gap book — ranks names by how consistently they gap up, buys the top 10 at the '
     'closing auction, sells at the next opening auction.'),
]


def get_engine_scoreboard():
    """One row per live equity book — the thing you actually watch to evaluate them.

    Added Sep 11 2026. Before this, Contrarian appeared NOWHERE in the dashboard, Wave Rider and
    Clockwork lived only on /factory, and those panels mixed SHADOW history in with LIVE trades,
    so there was no way to see what was real. Every number here is LIVE mode only.

    `self_exits` is the column that matters most right now: a trade closed by the engine's own
    rule counts, one closed by reconcile or by hand does not. Until that ratio is ~1.0 the P&L
    beside it is describing the plumbing, not the strategy."""
    out = []
    today = datetime.now(tz=ET).strftime('%Y-%m-%d')
    week = (datetime.now(tz=ET) - timedelta(days=7)).strftime('%Y-%m-%d')
    try:
        conn = sqlite3.connect(TRADES_DB)
        conn.row_factory = sqlite3.Row
        for label, analogy, tbl, has_mode, desc in ENGINE_SPECS:
            row = {'engine': label, 'analogy': analogy, 'desc': desc, 'table': tbl, 'mode': 'LIVE',
                   'open': 0, 'today_n': 0, 'today_pnl': 0.0, 'wk_n': 0, 'wk_pnl': 0.0,
                   'wk_win': None, 'self_exits': None, 'last': None, 'err': None}
            try:
                cols = {r[1] for r in conn.execute(f'PRAGMA table_info({tbl})')}
                mode_f = " AND mode='LIVE'" if has_mode and 'mode' in cols else ''
                closed = "('WIN','LOSS','CLOSED')" if tbl == 'trades' else "('CLOSED')"
                exit_d = 'exit_date' if 'exit_date' in cols else 'entry_date'
                extra = " AND setup_type!='RECONCILED'" if tbl == 'trades' else ''

                row['open'] = conn.execute(
                    f"SELECT COUNT(*) FROM {tbl} WHERE status IN "
                    f"('OPEN','PENDING_ENTRY','PENDING_EXIT'){mode_f}{extra}").fetchone()[0]
                r1 = conn.execute(
                    f"SELECT COUNT(*), COALESCE(SUM(pnl),0) FROM {tbl} "
                    f"WHERE status IN {closed} AND {exit_d}=?{mode_f}{extra}", (today,)).fetchone()
                row['today_n'], row['today_pnl'] = r1[0], round(r1[1] or 0, 2)
                r2 = conn.execute(
                    f"SELECT COUNT(*), COALESCE(SUM(pnl),0), "
                    f"       COALESCE(SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0) FROM {tbl} "
                    f"WHERE status IN {closed} AND {exit_d}>=?{mode_f}{extra}", (week,)).fetchone()
                row['wk_n'], row['wk_pnl'] = r2[0], round(r2[1] or 0, 2)
                row['wk_win'] = round(100 * r2[2] / r2[0]) if r2[0] else None

                # did the engine close it, or did reconcile / a human?
                if 'exit_reason' in cols and r2[0]:
                    bad = conn.execute(
                        f"SELECT COUNT(*) FROM {tbl} WHERE status IN {closed} AND {exit_d}>=? "
                        f"{mode_f}{extra} AND (exit_reason LIKE '%RECONCIL%' "
                        f"OR exit_reason LIKE '%FORCED%' OR exit_reason LIKE '%MANUAL%')",
                        (week,)).fetchone()[0]
                    row['self_exits'] = f'{r2[0] - bad}/{r2[0]}'
                last = conn.execute(
                    f"SELECT MAX({exit_d}) FROM {tbl} WHERE 1=1{mode_f}{extra}").fetchone()[0]
                row['last'] = last
            except Exception as e:
                row['err'] = str(e)[:60]
            out.append(row)
        conn.close()
    except Exception:
        pass
    return out


def get_factory_state():
    """Alpha Factory visibility (Aug 15 2026): the snapshot (roster/fleet/scorecards from
    factory/cache/factory_snapshot.json) + live Wave Rider state (wave_trades) + the scan funnel
    (wave_scan_log) so the /factory page shows what the factory IS and what it's DOING."""
    import json
    state = {"snapshot": None, "open": [], "closed": [], "closed_summary": None,
             "scan": None, "candidates": [], "mode": "SHADOW"}
    try:
        with open(os.path.join(BASE_DIR, 'factory', 'cache', 'factory_snapshot.json')) as fh:
            state["snapshot"] = json.load(fh)
    except Exception:
        pass
    try:
        conn = sqlite3.connect(TRADES_DB); conn.row_factory = sqlite3.Row
        state["open"] = [dict(r) for r in conn.execute(
            "SELECT * FROM wave_trades WHERE status='OPEN' ORDER BY entry_date DESC")]
        state["closed"] = [dict(r) for r in conn.execute(
            "SELECT * FROM wave_trades WHERE status='CLOSED' ORDER BY exit_date DESC, id DESC LIMIT 15")]
        row = conn.execute("SELECT scan_ts, detail FROM wave_scan_log WHERE kind='SUMMARY' "
                           "ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            state["scan"] = {"ts": row["scan_ts"], "funnel": json.loads(row["detail"])}
            state["candidates"] = [dict(r) for r in conn.execute(
                "SELECT symbol, day_chg, ext_vwap, verdict FROM wave_scan_log "
                "WHERE kind='CANDIDATE' AND scan_ts=? ORDER BY day_chg DESC", (row["scan_ts"],))]
        m = conn.execute("SELECT mode FROM wave_trades ORDER BY id DESC LIMIT 1").fetchone()
        if m:
            state["mode"] = m["mode"]
        conn.close()
    except Exception:
        pass
    if state["closed"]:
        pnls = [c["pnl"] or 0 for c in state["closed"]]
        state["closed_summary"] = {"n": len(pnls), "pnl": round(sum(pnls), 2),
                                   "win": round(100 * sum(1 for p in pnls if p > 0) / len(pnls))}
    # Clockwork overnight book (Night Shift v2) — the consistency-ranked overnight trader
    state["ovn"] = {"open": [], "closed": [], "scan": None, "candidates": [],
                    "mode": "SHADOW", "summary": None}
    try:
        conn = sqlite3.connect(TRADES_DB); conn.row_factory = sqlite3.Row
        state["ovn"]["open"] = [dict(r) for r in conn.execute(
            "SELECT * FROM overnight_trades WHERE status='OPEN' ORDER BY entry_date DESC")]
        state["ovn"]["closed"] = [dict(r) for r in conn.execute(
            "SELECT * FROM overnight_trades WHERE status='CLOSED' ORDER BY exit_date DESC, id DESC LIMIT 15")]
        row = conn.execute("SELECT scan_ts, detail FROM overnight_scan_log WHERE kind='SUMMARY' "
                           "ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            state["ovn"]["scan"] = {"ts": row["scan_ts"], "funnel": json.loads(row["detail"])}
            state["ovn"]["candidates"] = [dict(r) for r in conn.execute(
                "SELECT symbol, consistency, verdict FROM overnight_scan_log "
                "WHERE kind='CANDIDATE' AND scan_ts=? ORDER BY consistency DESC", (row["scan_ts"],))]
        m = conn.execute("SELECT mode FROM overnight_trades ORDER BY id DESC LIMIT 1").fetchone()
        if m:
            state["ovn"]["mode"] = m["mode"]
        conn.close()
        cl = state["ovn"]["closed"]
        if cl:
            pnls = [c["pnl"] or 0 for c in cl]
            state["ovn"]["summary"] = {"n": len(pnls), "pnl": round(sum(pnls), 2),
                                       "win": round(100 * sum(1 for p in pnls if p > 0) / len(pnls))}
    except Exception:
        pass
    # Contrarian — the mean-reversion sleeve (buys the biggest 3-day fallers). Was missing from
    # this page entirely until Sep 11 2026 despite trading live since Sep 9, so its activity was
    # invisible anywhere in the dashboard.
    state["contra"] = {"open": [], "closed": [], "scan": None, "candidates": [],
                        "mode": "SHADOW", "summary": None}
    try:
        conn = sqlite3.connect(TRADES_DB); conn.row_factory = sqlite3.Row
        state["contra"]["open"] = [dict(r) for r in conn.execute(
            "SELECT * FROM contrarian_trades WHERE status='OPEN' ORDER BY entry_date DESC")]
        state["contra"]["closed"] = [dict(r) for r in conn.execute(
            "SELECT * FROM contrarian_trades WHERE status='CLOSED' "
            "ORDER BY exit_date DESC, id DESC LIMIT 15")]
        row = conn.execute("SELECT scan_ts, detail FROM contrarian_scan_log WHERE kind='SUMMARY' "
                           "ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            state["contra"]["scan"] = {"ts": row["scan_ts"], "funnel": json.loads(row["detail"])}
            state["contra"]["candidates"] = [dict(r) for r in conn.execute(
                "SELECT * FROM contrarian_scan_log WHERE kind='CANDIDATE' AND scan_ts=? "
                "ORDER BY id LIMIT 12", (row["scan_ts"],))]
        m = conn.execute("SELECT mode FROM contrarian_trades ORDER BY id DESC LIMIT 1").fetchone()
        if m:
            state["contra"]["mode"] = m["mode"]
        conn.close()
        cl = state["contra"]["closed"]
        if cl:
            pnls = [c["pnl"] or 0 for c in cl]
            state["contra"]["summary"] = {"n": len(pnls), "pnl": round(sum(pnls), 2),
                                           "win": round(100 * sum(1 for p in pnls if p > 0) / len(pnls))}
    except Exception:
        pass
    # Turbo — the options execution layer on Wave Rider (options_shadow). Shows what Turbo
    # WOULD trade (structure + strike ladder) and what it rejected + why (the Edge-Budget gate).
    state["turbo"] = {"open": [], "skipped": [], "closed": [], "mode": "SHADOW", "summary": None}
    try:
        conn = sqlite3.connect(TRADES_DB); conn.row_factory = sqlite3.Row
        state["turbo"]["open"] = [dict(r) for r in conn.execute(
            "SELECT * FROM options_shadow WHERE status='OPEN' ORDER BY planned_at DESC")]
        state["turbo"]["skipped"] = [dict(r) for r in conn.execute(
            "SELECT symbol, strategy, iv_rank, ev_roi, gate_reason, planned_at FROM options_shadow "
            "WHERE status='SKIPPED' ORDER BY id DESC LIMIT 8")]
        state["turbo"]["closed"] = [dict(r) for r in conn.execute(
            "SELECT * FROM options_shadow WHERE status='CLOSED' ORDER BY exit_date DESC, id DESC LIMIT 10")]
        m = conn.execute("SELECT mode FROM options_shadow ORDER BY id DESC LIMIT 1").fetchone()
        if m:
            state["turbo"]["mode"] = m["mode"]
        conn.close()
        cl = state["turbo"]["closed"]
        if cl:
            lps = [c["leverage_premium"] for c in cl if c.get("leverage_premium") is not None]
            state["turbo"]["summary"] = {"n": len(cl),
                "avg_lev": round(sum(lps) / len(lps), 1) if lps else None}
        for pick in state["turbo"]["open"]:
            pick["ladder"] = _turbo_ladder(pick)
    except Exception:
        pass
    return state


@app.route('/factory')
def factory():
    """The Alpha Factory — architecture diagram + live Roster/fleet/Wave-Rider state.
    See docs/ALPHA_FACTORY_DESIGN.md."""
    return render_template('factory.html', f=get_factory_state())


@app.route('/api/data')
def api_data():
    bridge     = get_bridge_info()
    services   = get_services()
    regime     = get_regime()
    eq_pos     = get_equity_positions()
    opt_pos    = get_options_positions()
    fut_pos, session = get_futures_positions()
    eq_sum, opt_sum, fut_ibkr_sum, fut_tc_sum = get_today_summary()
    pnl_books  = get_pnl_by_book(15)
    scorecard  = get_scorecard(since_date=pnl_books[0]['date'] if pnl_books else None)
    alerts     = get_alerts(eq_pos, opt_pos)
    activity   = get_activity(5)
    earnings, macro = get_calendar(opt_pos, eq_pos)
    sectors    = get_sector_grades()
    health     = get_system_health()
    engines    = get_engine_scoreboard()

    prod_avail = PROD_BRIDGE_URL is not None
    prod_bridge = get_bridge_info(PROD_BRIDGE_URL) if prod_avail else None

    return jsonify({
        'ts':           datetime.now(tz=ET).strftime('%Y-%m-%d %H:%M:%S ET'),
        'mode':         bridge.get('mode', 'UNKNOWN'),
        'bridge':       bridge,
        'prod_available': prod_avail,
        'prod_bridge':  prod_bridge,
        'golive_checklist': GOLIVE_CHECKLIST,
        'services':     services,
        'regime':       regime,
        'futures_session': session,
        'equity_positions':  eq_pos,
        'options_positions': opt_pos,
        'futures_positions': fut_pos,
        'eq_summary':   eq_sum,
        'opt_summary':  opt_sum,
        'fut_ibkr_summary': fut_ibkr_sum,
        'fut_tc_summary':   fut_tc_sum,
        'pnl_by_book':  pnl_books,
        'scorecard':    scorecard,
        'alerts':       alerts,
        'activity':     activity,
        'earnings_calendar': earnings,
        'macro_calendar':    macro,
        'sector_grades': sectors,
        'system_health': health,
        'engines':      engines,
    })


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=PORT, debug=False)
