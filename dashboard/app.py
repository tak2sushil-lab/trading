#!/usr/bin/env python3
"""TriVega Trading Dashboard — Flask server, port 8080."""

import os, sys, sqlite3, subprocess, json, base64, functools, time, hmac
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from flask import Flask, render_template, jsonify, request, Response, session, redirect, url_for
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from futures.strategy_core import TICK_SIZE, TICK_VALUE  # noqa: E402
from totp import totp_code  # noqa: E402  (dashboard/ is sys.path[0])

# ── Config ─────────────────────────────────────────────────────────────
BASE_DIR        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADES_DB       = os.path.join(BASE_DIR, 'trades.db')
BRIDGE_URL      = 'http://localhost:8000'
TC_BRIDGE_URL   = 'http://localhost:8002'
PROD_BRIDGE_URL = None   # set to 'http://localhost:8001' when prod bridge is live
PORT            = 8080
ET              = ZoneInfo('America/New_York')

# (name, launchd label, kind, meaning of a non-zero exit, role, log file, max age hrs, what)
#   kind 'daemon'    — must hold a PID; no PID is a real alarm
#   kind 'scheduled' — idle between runs is NORMAL; judge it by its last exit code
#   what             — one line of plain English: what this thing IS and whether it can
#                      place an order. Added Sep 22 2026 because the pills answered
#                      "how is it doing" while the recurring question is "what is it" —
#                      turbo, ref_prices and heartbeat in particular. It is the same
#                      question /glossary exists for, answered where it gets asked.
#
# Sep 22 2026: this listed 13 of 36 loaded jobs and was missing, among others, all
# THREE factory engines whose positions the dashboard now shows. Wave Rider,
# Contrarian or Clockwork could have died and nothing on the page would have said
# so — while their open positions carried on being displayed as if live. The
# watchdog itself (heartbeat) and the scoring job were invisible too.
SERVICES = [
    # ── brokers and bridges: everything else is dead without these ──
    ('gateway',      'com.sushil.trading.gateway',        'daemon',    '', 'Brokers', None, None, 'IB Gateway for the IBKR paper account (DU9952463). Logs itself out nightly at 23:45 and is restarted by launchd — everything downstream is dead without it.'),
    ('bridge',       'com.sushil.trading.bridge',         'daemon',    '', 'Brokers', None, None, 'FastAPI translator between our code and IBKR on port 8000. Every order, quote and portfolio read goes through it.'),
    ('tc_gateway',   'com.sushil.trading.tc_gateway',     'daemon',    '', 'Brokers', None, None, 'IB Gateway for the TC account (DUQ640500). Its 02:50 start is what finally gave TC London its full 04:00-08:00 window.'),
    ('tc_bridge',    'com.sushil.trading.tc_bridge',      'daemon',    '', 'Brokers', None, None, 'The same bridge, port 8002, pointed at the TC account.'),
    # ── the books that place orders ──
    ('autotrader',   'com.sushil.trading.autotrader',     'daemon',    '', 'Books', None, None, 'The Day Trader book — intraday catalyst and momentum names, in after 09:30 and out by 15:45. PLACES REAL ORDERS.'),
    ('wave_rider',   'com.sushil.trading.wave_rider',     'scheduled', 'last scan errored', 'Books', 'wave_rider.log', 24, 'Swing book — buys names already moving hard, holds 3 days, 8% stop. PLACES REAL ORDERS.'),
    ('contrarian',   'com.sushil.trading.contrarian',     'scheduled', 'last scan errored', 'Books', 'contrarian.log', 24, 'Mean-reversion book — buys the biggest 3-day fallers, holds 5 days, 15% stop. Long-only, so judge it on alpha vs the tide, not raw P&L. PLACES REAL ORDERS.'),
    ('clockwork',    'com.sushil.trading.clockwork',      'scheduled', 'last scan errored', 'Books', 'clockwork.log', 24, "Overnight book — buys at the closing auction, sells at the next opening auction, 3 names. This is the book that trades the overnight window where 91% of the universe's return actually accrues. PLACES REAL ORDERS."),
    ('options',      'com.sushil.trading.options_trader', 'daemon',    '', 'Books', None, None, 'The options book — spread entries, the calculator and the OPT commands. PLACES REAL ORDERS.'),
    ('watchman',     'com.sushil.trading.watchman',       'daemon',    '', 'Books', None, None, 'Monitors open options positions every 15 min and runs their exits (targets, stops, two-leg closes). PLACES REAL ORDERS.'),
    ('futures_ibkr', 'com.sushil.trading.futures_personal', 'daemon',  '', 'Books', None, None, 'MNQ futures on IBKR — the NY session plus London threaded inside it. PLACES REAL ORDERS.'),
    ('futures_tc',   'com.sushil.trading.futures_trader', 'daemon',    '', 'Books', None, None, 'The same futures code on the TC account, which is the one heading for a TopStep evaluation. PLACES REAL ORDERS.'),
    # ── data the books depend on ──
    ('collect_bars', 'com.sushil.trading.collect_bars',   'scheduled', 'last collection failed', 'Data', 'collect_bars.log', 96, "Pulls the day's 5-min equity bars into market_data.db after the close. Book Health, every signal and every backtest read these — if it stops, the system degrades quietly rather than failing loudly."),
    ('futures_bars', 'com.sushil.trading.futures_collect_bars', 'scheduled', 'last collection failed', 'Data', 'futures_collect_bars.log', 96, 'The same collector for MNQ/ES/RTY futures bars.'),
    ('news_engine',  'com.sushil.trading.news_engine',    'daemon',    '', 'Data', None, None, 'Scans headlines every 30 min via Groq/Llama and writes the catalyst calendar. Log-only for trading since Jul 19 2026 — it feeds the Ghost Ledger, never an entry.'),
    ('field_report', 'com.sushil.trading.market_context', 'scheduled', 'pre-market brief failed', 'Data', 'market_context.log', 96, 'Pre-market market-context brief at 09:15 — trend, S/R levels, macro dates, plus one Claude call for a stance. LOG-ONLY: no trader reads it.'),
    # ── instrumentation: silent failure here costs evidence, not money ──
    ('scoring',      'com.sushil.trading.scan_forward_label', 'scheduled', 'forward label not written', 'Instruments', 'scan_forward_label.log', 96, 'Writes the forward outcome label (what each graded signal actually went on to do) onto scan_log each evening. This is the answer key for the grader — without it we record the guesses and never the answers. Places no orders.'),
    ('ref_prices',   'com.sushil.trading.overnight_reference', 'scheduled', 'reference marks not written', 'Instruments', 'overnight_reference.log', 96, "Marks Clockwork twice a night: once at the broker's fill, once at the session's OFFICIAL close and open from our own bars. IBKR paper fabricates auction fills, so ref_pnl is the honest read on the strategy and pnl is the read on the plumbing. Places no orders."),
    ('turbo',        'com.sushil.trading.turbo',          'scheduled', 'shadow pass errored', 'Instruments', 'turbo.log', 24, 'Asks, for each Wave Rider pick, whether an options structure would beat simply holding the shares. So far the answer is almost always no (5 passes in 45, and those lost 51pp vs shares). SHADOW ONLY — places no orders.'),
    # ── watchdogs. parity_check exits 1 when it FINDS a divergence — that is its
    #    designed signal, not a crash, so amber here means "read the report". ──
    ('heartbeat',    'com.sushil.trading.heartbeat',      'scheduled', 'watchdog check errored', 'Watchdogs', 'heartbeat.log', 24, 'The watchdog. Each trader stamps a file every scan; this checks staleness and both bridges from OUTSIDE the traders, because a watchdog inside a process cannot report that process dying. Built after a reboot silently cost a whole London session. Places no orders.'),
    ('trade_cop',    'com.sushil.trading.parity_check',   'scheduled',
     'found a divergence — by design, read logs/parity.log', 'Watchdogs', 'parity.log', 96,
     'Replays the day through the simulator and diffs it against what live actually did, '
     'across all four books. Amber means it FOUND something — read logs/parity.log. '
     'It only reports; it never corrects anything itself.'),
    ('graphify',     'com.sushil.trading.graphify_watch', 'daemon',    '', 'Watchdogs', None, None, 'Keeps the codebase knowledge graph in sync on every .py save. Nothing to do with trading.'),
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
# Session-signing key. Its own secret since Sep 28 2026 (was the password itself);
# falls back to the password so an .env without it still starts.
app.secret_key = (os.getenv('DASHBOARD_SECRET_KEY')
                  or os.getenv('DASHBOARD_PASSWORD', 'trivega-dev-key'))
app.permanent_session_lifetime = timedelta(days=30)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# ── Auth: password + authenticator code (TOTP), 30-day cookie ─────────────
# Sep 28 2026: moving to a public Tailscale Funnel URL, which has no auth of its own,
# so the login is the only wall. Adds a 6-digit authenticator code (RFC 6238, same as
# Google Authenticator) and lockouts. Setup / rotate: venv/bin/python dashboard/totp_setup.py
_DASH_PASSWORD = os.getenv('DASHBOARD_PASSWORD', '')
_TOTP_SECRET   = os.getenv('DASHBOARD_TOTP_SECRET', '').replace(' ', '').upper()
_AUTH_VERSION  = 2      # bump to sign every browser out (old cookies lack it)

_OPEN_PATHS = {'/login', '/static/apple-touch-icon.png', '/favicon.ico'}

LOGIN_MAX_FAILS_IP     = 5      # per client, per window
LOGIN_MAX_FAILS_GLOBAL = 20     # all clients — X-Forwarded-For can be spoofed
LOGIN_WINDOW_S         = 15 * 60
_fails_by_ip  = {}
_fails_global = []
_last_totp_counter = [0]        # a code is accepted once, never replayed


def _totp_ok(code):
    code = (code or '').strip().replace(' ', '')
    if not (_TOTP_SECRET and code.isdigit() and len(code) == 6):
        return False
    now = int(time.time()) // 30
    for c in (now - 1, now, now + 1):       # ±30 s for clock drift
        if c > _last_totp_counter[0] and hmac.compare_digest(totp_code(_TOTP_SECRET, c), code):
            _last_totp_counter[0] = c
            return True
    return False


def _client_ip():
    # Behind a tunnel every request arrives from 127.0.0.1; the tunnel sets XFF.
    xff = request.headers.get('X-Forwarded-For', '')
    return (xff.split(',')[0].strip() if xff else '') or request.remote_addr or '?'


def _lockout_left(ip):
    cut = time.time() - LOGIN_WINDOW_S
    _fails_global[:] = [t for t in _fails_global if t > cut]
    mine = _fails_by_ip[ip] = [t for t in _fails_by_ip.get(ip, []) if t > cut]
    for fails, cap in ((mine, LOGIN_MAX_FAILS_IP), (_fails_global, LOGIN_MAX_FAILS_GLOBAL)):
        if len(fails) >= cap:
            return int(fails[-cap] + LOGIN_WINDOW_S - time.time()) + 1
    return 0


@app.before_request
def _check_auth():
    if not _DASH_PASSWORD:
        return
    if request.path in _OPEN_PATHS or request.path.startswith('/static/'):
        return
    if not (session.get('authenticated') and session.get('auth_v') == _AUTH_VERSION):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'not signed in'}), 401
        return redirect(url_for('login', next=request.path))


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = ''
    if request.method == 'POST':
        ip = _client_ip()
        wait = _lockout_left(ip)
        if wait:
            error = f'Too many attempts. Try again in {wait // 60 + 1} min.'
        else:
            pw_ok   = hmac.compare_digest(request.form.get('password', ''), _DASH_PASSWORD)
            # Only after the password passes: checking burns the code (replay guard).
            code_ok = pw_ok and (_totp_ok(request.form.get('code')) if _TOTP_SECRET else True)
            if pw_ok and code_ok:
                _fails_by_ip.pop(ip, None)
                session.clear()
                session.permanent = True
                session['authenticated'] = True
                session['auth_v'] = _AUTH_VERSION
                nxt = request.args.get('next') or '/'
                if not nxt.startswith('/') or nxt.startswith('//'):
                    nxt = '/'                   # never redirect off-site
                return redirect(nxt)
            now = time.time()
            _fails_by_ip.setdefault(ip, []).append(now)
            _fails_global.append(now)
            print(f'[login] failed from {ip}', flush=True)
            error = 'Wrong password or code'
    return render_template('login.html', error=error, need_code=bool(_TOTP_SECRET))


@app.after_request
def _gzip(resp):
    """Sep 28 2026: ngrok's free 1 GB/month ran out because /api/data (69 KB) went
    uncompressed every 30 s — ~8 MB/hour per open tab. gzip cuts it ~6x (69 KB -> 12 KB).
    No new dependency; skips small, already-encoded and streamed responses."""
    import gzip as _gz
    if ('gzip' not in request.headers.get('Accept-Encoding', '').lower()
            or resp.status_code < 200 or resp.status_code >= 300
            or 'Content-Encoding' in resp.headers
            or (resp.is_streamed and not resp.direct_passthrough)):
        return resp
    resp.direct_passthrough = False      # static files are sent passthrough; read them in
    data = resp.get_data()
    if len(data) < 1024:
        return resp
    resp.set_data(_gz.compress(data, compresslevel=6))
    resp.headers['Content-Encoding'] = 'gzip'
    resp.headers['Content-Length'] = str(len(resp.get_data()))
    resp.headers.add('Vary', 'Accept-Encoding')
    return resp


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


# Which services are long-running daemons (must hold a PID) and which are scheduled
# jobs that are SUPPOSED to be idle between runs. Judging them the same way is what
# made this row meaningless — see get_services().
# (kind now lives in the SERVICES table itself)

# Newest 5-min bar, cached — see the comment at its use site for why this
# must never be queried per request.
_BARS_CACHE = {'at': 0.0, 'last': ''}


def get_services():
    """Real run-state per service, not just "is the plist loaded".

    Sep 22 2026: this used to be `label in launchctl_list_output`, which is True for
    every LOADED job whether or not it is running, has ever run, or exited non-zero.
    A crashed daemon showed green; a scheduled job that is correctly idle showed green;
    parity_check sitting on exit code 1 showed green. The row could not report a
    failure of any kind.

    `launchctl list` prints three columns: PID, last exit status, label.
      - daemon with a PID      -> up
      - daemon without a PID   -> DOWN (the real alarm)
      - scheduled job, exit 0  -> idle (normal between runs)
      - scheduled job, exit !=0-> failing (its last run errored)
    A daemon's exit status is ignored when it holds a PID: -15 is just SIGTERM from
    the last `launchctl kickstart -k` restart, which is routine here.
    """
    states = {}
    try:
        out = subprocess.run(['launchctl', 'list'], capture_output=True,
                             text=True, timeout=5).stdout
        table = {}
        for line in out.splitlines():
            parts = line.split('\t')
            if len(parts) >= 3:
                table[parts[2].strip()] = (parts[0].strip(), parts[1].strip())
        for name, label, kind, note, role, logf, max_h, what in SERVICES:
            if label not in table:
                states[name] = {'state': 'missing', 'ok': False, 'role': role,
                                'what': what, 'detail': 'not loaded in launchd'}
                continue
            pid, rc = table[label]
            running = pid not in ('-', '')
            scheduled = (kind == 'scheduled')
            if running:
                states[name] = {'state': 'up', 'ok': True, 'pid': pid, 'role': role,
                                'what': what, 'detail': f'running (pid {pid})'}
            elif scheduled:
                bad = rc not in ('0', '')
                # Sep 22 2026: a bare "idle" chip reads as "off", which is alarming
                # for a trading engine that is simply between scheduled runs. Show
                # WHEN it last ran instead — a scheduled job that ran 5 minutes ago
                # is healthy; one that has not run in days is broken, and only the
                # age distinguishes them.
                age_s, ago = None, None
                if logf:
                    try:
                        age_s = int(time.time() - os.path.getmtime(
                            os.path.join(BASE_DIR, 'logs', logf)))
                        ago = (f'{age_s}s' if age_s < 120 else
                               f'{age_s // 60}m' if age_s < 7200 else
                               f'{age_s // 3600}h' if age_s < 172800 else
                               f'{age_s // 86400}d')
                    except Exception:
                        pass
                overdue = (age_s is not None and max_h and age_s > max_h * 3600)
                states[name] = {
                    'state': 'failing' if bad else ('stale' if overdue else 'idle'),
                    'ok': not (bad or overdue), 'rc': rc, 'role': role,
                    'what': what, 'ago': ago, 'age_s': age_s,
                    'detail': ((note or f'last run exited {rc}') if bad
                               else (f'has not run for {ago} — expected at least every '
                                     f'{max_h}h' if overdue
                                     else f'ran {ago} ago; waiting for its next scheduled run'
                                          if ago else 'idle between scheduled runs (normal)'))}
            else:
                states[name] = {'state': 'down', 'ok': False, 'rc': rc, 'role': role,
                                'what': what, 'detail': f'NOT RUNNING — last exit {rc}'}
    except Exception as e:
        for row in SERVICES:
            states[row[0]] = {'state': 'unknown', 'ok': False, 'role': row[4],
                              'what': row[7], 'detail': str(e)[:60]}
    # Return ORDERED GROUPS, not a dict. Flask's jsonify sorts dict keys, which
    # silently alphabetised this row and destroyed the role grouping entirely.
    ROLE_WHAT = {
        'Brokers':     'The connection to the broker. Everything below is dead without these.',
        'Books':       'The things that place real orders. A red pill here means a book is not trading.',
        'Data':        'The bars and headlines every book reads. These fail quietly — a book keeps '
                       'running on stale data rather than stopping, so watch the ages.',
        'Instruments': 'Measurement only. Nothing here can place an order; silent failure here '
                       'costs EVIDENCE, not money — which is why it is easy to miss.',
        'Watchdogs':   'They watch the rest and report. None of them corrects anything on its own; '
                       'amber means read the log, not that something was fixed.',
    }
    groups, seen = [], {}
    for row in SERVICES:
        role = row[4]
        if role not in seen:
            seen[role] = {'role': role, 'what': ROLE_WHAT.get(role, ''), 'items': []}
            groups.append(seen[role])
        seen[role]['items'].append(dict(states[row[0]], name=row[0]))
    return groups


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
                SELECT id, symbol, entry_date, entry_time, entry_price, shares, side,
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
            # id + book identify the row a close button targets. Symbol alone is not
            # enough: four books share this account and two can hold the same name.
            'id':            row['id'],
            'row_status':    'OPEN',
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
            'book':          'Day Trader',
            'kind':          'equity',
            'target':        'equity',
            'exit_plan':     'closes today 15:45 (or holds overnight if >+1.5% and above VWAP)',
        })

    # Sep 22 2026: this table listed the Day Trader book only, so the other three
    # live equity books — which hold real shares in the SAME account — were visible
    # nowhere except /factory. VICR made that concrete: Wave Rider held 9 shares of
    # it while this table showed only auto_trader's 8. Each row now says which book
    # owns it and when that book intends to get out, because "when does this close"
    # is the question the table was failing to answer.
    _ENGINES = (
        ('Wave Rider', 'wave_trades',        'momentum swing · 3-day hold · 8% stop'),
        ('Contrarian', 'contrarian_trades',  'mean reversion · 5-day hold · 15% stop'),
        ('Clockwork',  'overnight_trades',   'overnight gap · sells at the next open'),
    )
    try:
        with _db() as c:
            for _book, _tbl, _desc in _ENGINES:
                try:
                    cols = {r[1] for r in c.execute(f'PRAGMA table_info({_tbl})')}
                    if not cols:
                        continue
                    mode_f = " AND mode='LIVE'" if 'mode' in cols else ''
                    for row in c.execute(
                        f"SELECT * FROM {_tbl} WHERE status IN "
                        f"('OPEN','PENDING_ENTRY','PENDING_EXIT'){mode_f} "
                        f"ORDER BY entry_date DESC").fetchall():
                        sym = row['symbol']
                        ep  = row['entry_price'] or 0
                        sh  = row['shares'] or 0
                        lp  = live_map.get(sym, {})
                        cp  = (lp.get('marketPrice') or ep) if (bridge_connected and lp) else ep
                        # Own shares and own entry only — never the broker's blended
                        # figure, which merges every book holding the same symbol.
                        upnl = (cp - ep) * sh if (bridge_connected and lp and ep) else None
                        upct = ((cp - ep) / ep * 100) if (ep and bridge_connected and lp) else None
                        exit_on = row['exit_on_date'] if 'exit_on_date' in cols else None
                        plan = (f"{_desc} · exits {exit_on[5:].replace('-', '/')}"
                                if exit_on else _desc)
                        stop = row['stop_price'] if 'stop_price' in cols else None
                        st = 'OK'
                        if stop and ep and cp:
                            buf = (cp - stop) / ep * 100
                            st = 'REVIEW' if buf < 0.5 else ('WARN' if buf < 2.0 else 'OK')
                        result.append({
                            'id': row['id'], 'row_status': row['status'],
                            'symbol': sym, 'entry_date': row['entry_date'],
                            'entry_time': row['entry_time'] if 'entry_time' in cols else None,
                            'entry_price': ep, 'current_price': cp, 'shares': sh,
                            'side': 'LONG', 'target_price': None, 'stop_price': stop,
                            'setup_type': None, 'sector': None, 'confidence': None,
                            'unreal_pnl': round(upnl, 2) if upnl is not None else None,
                            'unreal_pct': round(upct, 2) if upct is not None else None,
                            'status': st, 'book': _book, 'exit_plan': plan,
                            'kind': 'equity', 'target': _BOOK_TARGET.get(_book),
                        })
                except Exception:
                    continue      # one engine's table must not blank the whole panel
    except Exception:
        pass
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
            'kind':          'options',
            'target':        'options',
            'row_status':    'OPEN',
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
            'kind':           'futures',
            'target':         'futures_ny',
            'row_status':     'OPEN',
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
            # London is its own book with its own owner — FUT CLOSE does not cover it,
            # which is exactly why it needs a per-row button.
            'kind':           'futures',
            'target':         'london',
            'row_status':     'OPEN',
        })

    result.sort(key=lambda r: (r['entry_date'] or '', r['entry_time'] or ''), reverse=True)
    return result, session


# Options realized P&L — ONE definition, same as database.get_options_total_pnl (the circuit
# breaker's). Credit spreads earn the credit and pay to buy back, so their sign is reversed;
# the dashboard used exit_value - premium_paid for every trade until Sep 29 2026 (no credit
# spread had closed yet, so no figure was wrong — the first one would have been).
OPT_PNL_SQL = ("(CASE WHEN strategy IN ('BULL_PUT_CREDIT','BEAR_CALL_CREDIT') "
               "THEN premium_paid - exit_value ELSE exit_value - premium_paid END)")


def get_totals():
    """Realized P&L TO DATE per category — closed trades only, the same definitions the
    Today card uses (equity = all four books' LIVE rows; futures = NY + London per account;
    RECONCILED rows excluded). TC is a prop-evaluation account, so it reports its combine
    progress from the ledger-derived state and is NOT added to the own-money total."""
    out = {'equity': {'total': 0.0, 'since': None, 'books': []}}
    since_all = []
    try:
        with _db() as c:
            r = c.execute("SELECT COALESCE(SUM(pnl),0), MIN(exit_date), COUNT(*) FROM trades "
                          "WHERE exit_date IS NOT NULL AND setup_type != 'RECONCILED'").fetchone()
            books = [{'name': 'Day Trader', 'total': round(r[0], 2), 'since': r[1], 'trades': r[2]}]
            for name, tbl in (('Wave Rider', 'wave_trades'), ('Contrarian', 'contrarian_trades'),
                              ('Clockwork', 'overnight_trades')):
                try:
                    cols = {x[1] for x in c.execute(f'PRAGMA table_info({tbl})')}
                    if not cols:
                        continue
                    mode = " AND mode='LIVE'" if 'mode' in cols else ''
                    r = c.execute(f"SELECT COALESCE(SUM(pnl),0), MIN(exit_date), COUNT(*) FROM {tbl} "
                                  f"WHERE status='CLOSED'{mode}").fetchone()
                    books.append({'name': name, 'total': round(r[0], 2), 'since': r[1], 'trades': r[2]})
                except Exception:
                    continue
            eq_since = min((b['since'] for b in books if b['since']), default=None)
            out['equity'] = {'total': round(sum(b['total'] for b in books), 2),
                             'since': eq_since, 'books': books}
            r = c.execute(f"SELECT COALESCE(SUM({OPT_PNL_SQL}),0), MIN(exit_date), COUNT(*) "
                          "FROM options_trades WHERE status='CLOSED' AND exit_value IS NOT NULL").fetchone()
            r2 = c.execute(f"SELECT COALESCE(SUM({OPT_PNL_SQL}),0), COUNT(*) FROM options_trades "
                           "WHERE status='CLOSED' AND exit_value IS NOT NULL AND exit_date >= '2026-09-21'").fetchone()
            out['options'] = {'total': round(r[0], 2), 'since': r[1], 'trades': r[2],
                              'since_rebuild': round(r2[0], 2), 'rebuild_trades': r2[1],
                              'rebuild_date': '2026-09-21'}
            for mode, key in (('IBKR', 'fut_ibkr'), ('TC', 'fut_tc')):
                ny = c.execute("SELECT COALESCE(SUM(pnl),0), MIN(exit_date) FROM futures_trades "
                               "WHERE status='CLOSED' AND setup_type != 'RECONCILED' AND account_mode=?",
                               (mode,)).fetchone()
                lon = c.execute("SELECT COALESCE(SUM(pnl),0), MIN(exit_date) FROM london_trades "
                                "WHERE status='CLOSED' AND account_mode=?", (mode,)).fetchone()
                out[key] = {'total': round(ny[0] + lon[0], 2), 'ny': round(ny[0], 2),
                            'london': round(lon[0], 2),
                            'since': min((x for x in (ny[1], lon[1]) if x), default=None)}
    except Exception as e:
        out['error'] = str(e)
    try:
        import json as _json
        with open(os.path.join(BASE_DIR, 'futures', 'prop_state.json')) as _pf:
            ps = _json.load(_pf)
        out.setdefault('fut_tc', {})['combine'] = {
            'plan': ps.get('account_size'), 'profit': round(float(ps.get('total_profit') or 0), 2),
            'target': ps.get('profit_target'), 'balance': ps.get('balance'),
            'room': round(float(ps.get('balance') or 0) - float(ps.get('floor') or 0)
                          - float(ps.get('buffer') or 0), 2) if ps.get('floor') else None}
    except Exception:
        pass
    own = [out.get(k, {}).get('total') for k in ('equity', 'options', 'fut_ibkr')]
    out['own_total'] = round(sum(x for x in own if x is not None), 2)
    return out


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
                "SELECT " + OPT_PNL_SQL + " as pnl FROM options_trades "
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
                "SELECT exit_date, SUM(" + OPT_PNL_SQL + ") FROM options_trades "
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
        ('Options',       "SELECT exit_date, " + OPT_PNL_SQL + " FROM options_trades "
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


def get_alerts(eq_pos, opt_pos, health=None, services=None):
    """Things worth a human look.

    Sep 22 2026: this only ever watched position stops, so with every stop far away
    it rendered empty while the Trade Cop was flagging a real divergence, SHORT book
    health was 53 days stale, and the options circuit breaker had silently blocked an
    entire paper trial. An alerts card that can only see stops is not an alerts card.
    It now also reads the system-health payload it is rendered beside.
    """
    alerts = []
    now_et = datetime.now(tz=ET)
    hhmm = now_et.strftime('%H:%M')

    def add(level, typ, vert, sym, msg):
        alerts.append({'level': level, 'type': typ, 'vertical': vert,
                       'symbol': sym, 'time': hhmm, 'message': msg})

    h = health or {}
    # a daemon that is not running, or a scheduled job whose last run errored
    for grp in (services or []):
        for it in grp.get('items', []):
            if it.get('state') == 'down':
                add('HIGH', 'SERVICE_DOWN', 'SYSTEM', it['name'],
                    f"{it['name']} is NOT RUNNING — {it.get('detail', '')}")
            elif it.get('state') in ('missing', 'failing'):
                add('WARN', 'SERVICE', 'SYSTEM', it['name'],
                    f"{it['name']}: {it.get('detail', '')}")
    # a Book Health verdict that is no longer describing the market
    for side, b in (h.get('books') or {}).items():
        if b.get('stale'):
            add('WARN', 'STALE_HEALTH', 'EQUITY', side,
                f"{side} book health is {b.get('age_days')}d old (newest signal "
                f"{b.get('last_signal')}) — it is not describing the market now")
    # options circuit breaker / entries switched off in code
    brk = (h.get('options') or {}).get('breaker') or {}
    if brk.get('tripped') is True:
        add('HIGH', 'BREAKER', 'OPTIONS', '—',
            f"Options circuit breaker TRIPPED — no entries possible "
            f"(realized ${brk.get('pnl', 0):,.0f} since {brk.get('since') or 'inception'})")
    if brk.get('frozen'):
        add('WARN', 'FROZEN', 'OPTIONS', '—',
            'Options automated entries are frozen in code (EQUITY_ECHO_FROZEN)')
    # the prop account can lock itself out — it froze by $45 once
    pr = h.get('prop') or {}
    if pr.get('room') is not None and pr['room'] < 500:
        lvl = 'HIGH' if pr['room'] < 0 else 'WARN'
        add(lvl, 'PROP_ROOM', 'FUTURES', pr.get('mode', 'TC'),
            f"TC has ${pr['room']:,.0f} before the trailing MLL floor blocks all entries")
    # the data every book reads
    fd = h.get('feed') or {}
    if (fd.get('bars_age_days') or 0) > 3:
        add('WARN', 'STALE_DATA', 'SYSTEM', 'bars_5m',
            f"5-min bars are {fd['bars_age_days']}d old (newest {fd.get('bars_last')}) — "
            f"book health, signals and backtests all read these")
    for b in fd.get('beats', []):
        if b.get('age_s', 0) > 600 and 'london' not in b.get('name', ''):
            add('HIGH', 'NO_HEARTBEAT', 'FUTURES', b['name'],
                f"{b['name']} has not written a heartbeat for {b['age_s'] // 60}m — "
                f"its scan loop has stopped")
    # Trade Cop verdict
    par = h.get('parity') or {}
    if par.get('status') and par['status'] != 'OK':
        add('WARN', 'TRADE_COP', 'SYSTEM', '—',
            f"Trade Cop: {par.get('friendly') or par.get('detail') or par['status']}")


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
    """Every leg from every book, newest first.

    Sep 24 2026 — two things were wrong here and both hid Clockwork.

    (1) `setup` was doing two jobs. For the Day Trader table it holds the SETUP TYPE
        (CATALYST_OVERRIDE, FVG_FILL...); for the three factory engines it was being
        overloaded to hold the BOOK NAME. Nothing could filter by book, and an exit row
        could not say which engine produced it. There is now a separate `book` on every
        row and `setup` means only ever what it says.

    (2) The row cap was 100 across a 5-session window. On a busy two-day stretch the
        equity books alone can fill that, so the oldest legs fell off the end silently —
        which looks exactly like a logging gap. Raised, and the window is returned with
        the payload so the UI can state what it is showing.
    """
    cutoff = (datetime.now(tz=ET) - timedelta(days=sessions + 2)).strftime('%Y-%m-%d')
    result = []
    try:
        with _db() as c:
            # ── Day Trader (auto_trader's own book) ──
            rows = c.execute("""
                SELECT 'ENTRY' as ev, 'EQUITY' as vert, 'Day Trader' as book, symbol,
                       entry_date as dt, entry_time as tm, entry_price as price,
                       setup_type as setup, sector, side, shares,
                       NULL as pnl, NULL as reason, NULL as account
                FROM trades WHERE entry_date >= ? AND setup_type != 'RECONCILED'
                UNION ALL
                SELECT 'EXIT', 'EQUITY', 'Day Trader', symbol, exit_date, exit_time,
                       exit_price, setup_type, sector, side, shares, pnl, exit_reason, NULL
                FROM trades WHERE exit_date >= ? AND exit_date IS NOT NULL
                  AND setup_type != 'RECONCILED'
            """, (cutoff, cutoff)).fetchall()
            result.extend([dict(r) for r in rows])

            # ── The three factory engines. They hold real shares in the SAME account
            # and were invisible in this feed until Sep 22 2026, which is why a +$478
            # Wave Rider exit could land in the day total with no matching line.
            for _book, _tbl in (('Wave Rider', 'wave_trades'),
                                ('Contrarian', 'contrarian_trades'),
                                ('Clockwork',  'overnight_trades')):
                try:
                    _cols = {r[1] for r in c.execute('PRAGMA table_info(' + _tbl + ')')}
                    if not _cols:
                        continue
                    _mode = " AND mode='LIVE'" if 'mode' in _cols else ''
                    _tm  = 'entry_time'  if 'entry_time'  in _cols else 'NULL'
                    _xtm = 'exit_time'   if 'exit_time'   in _cols else 'NULL'
                    _xr  = 'exit_reason' if 'exit_reason' in _cols else 'NULL'
                    _sql = (
                        "SELECT 'ENTRY' as ev, 'EQUITY' as vert, '" + _book + "' as book, "
                        "symbol, entry_date as dt, " + _tm + " as tm, entry_price as price, "
                        "NULL as setup, NULL as sector, 'LONG' as side, shares, "
                        "NULL as pnl, NULL as reason, NULL as account "
                        "FROM " + _tbl + " WHERE entry_date >= ?" + _mode +
                        " UNION ALL "
                        "SELECT 'EXIT', 'EQUITY', '" + _book + "', symbol, exit_date, "
                        + _xtm + ", exit_price, NULL, NULL, 'LONG', shares, pnl, "
                        + _xr + ", NULL "
                        "FROM " + _tbl + " WHERE exit_date >= ? AND exit_date IS NOT NULL "
                        "AND status='CLOSED'" + _mode
                    )
                    result.extend([dict(r) for r in c.execute(_sql, (cutoff, cutoff)).fetchall()])
                except Exception:
                    continue      # one engine's table must not blank the whole feed

            # ── Options ──
            rows = c.execute("""
                SELECT 'ENTRY' as ev, 'OPTIONS' as vert, 'Options' as book, symbol,
                       entry_date as dt, NULL as tm, net_debit as price,
                       strategy as setup, NULL as sector, 'LONG' as side,
                       contracts as shares, NULL as pnl, NULL as reason, NULL as account
                FROM options_trades WHERE entry_date >= ?
                UNION ALL
                SELECT 'EXIT', 'OPTIONS', 'Options', symbol, exit_date, NULL, exit_value,
                       strategy, NULL, 'LONG', contracts,
                       """ + OPT_PNL_SQL + """, exit_reason, NULL
                FROM options_trades WHERE exit_date >= ? AND exit_date IS NOT NULL
            """, (cutoff, cutoff)).fetchall()
            result.extend([dict(r) for r in rows])

            # ── Futures NY. `account` separates IBKR from TC: two real accounts with
            # different prop rules, and their fills genuinely differ (Sep 3 2026).
            rows = c.execute("""
                SELECT 'ENTRY' as ev, 'FUTURES' as vert, 'Futures NY' as book, symbol,
                       entry_date as dt, entry_time as tm, entry_price as price,
                       setup_type as setup, session as sector, side,
                       contracts as shares, NULL as pnl, NULL as reason, account_mode as account
                FROM futures_trades WHERE entry_date >= ?
                UNION ALL
                SELECT 'EXIT', 'FUTURES', 'Futures NY', symbol, exit_date, exit_time,
                       exit_price, setup_type, session, side, contracts, pnl,
                       exit_reason, account_mode
                FROM futures_trades WHERE exit_date >= ? AND exit_date IS NOT NULL
            """, (cutoff, cutoff)).fetchall()
            result.extend([dict(r) for r in rows])

            # ── London. Its own book: FUT CLOSE does not cover it, and both accounts
            # run it, so the account tag matters here too.
            rows = c.execute("""
                SELECT 'ENTRY' as ev, 'FUTURES' as vert, 'London' as book, 'MNQ' as symbol,
                       entry_date as dt, entry_time as tm, entry as price, setup as setup,
                       'LONDON' as sector, side, contracts as shares,
                       NULL as pnl, NULL as reason, account_mode as account
                FROM london_trades WHERE entry_date >= ?
                UNION ALL
                SELECT 'EXIT', 'FUTURES', 'London', 'MNQ', exit_date, exit_time, exit_price,
                       setup, 'LONDON', side, contracts, pnl, exit_reason, account_mode
                FROM london_trades WHERE exit_date >= ? AND exit_date IS NOT NULL
            """, (cutoff, cutoff)).fetchall()
            result.extend([dict(r) for r in rows])

    except Exception:
        pass

    result.sort(key=lambda x: (x.get('dt') or '', x.get('tm') or ''), reverse=True)
    return result[:400]


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
                # Sep 22 2026: this read `earnings_calendar`, which has been EMPTY
                # (0 rows) the whole time — so the card was permanently blank while
                # `catalyst_calendar` sat beside it with 2,437 rows and 915 upcoming
                # events, actively written by news_engine. Query that instead, and
                # keep earnings_calendar as a fallback in case it is ever populated.
                # catalyst_calendar holds several rows per event (one per news pass),
                # so dedupe on symbol+date and keep the highest-confidence name.
                rows = c.execute(
                    f"""SELECT symbol, event_date AS earnings_date,
                               MIN(event_name) AS event_name,
                               MIN(catalyst_type) AS catalyst_type
                        FROM catalyst_calendar
                        WHERE symbol IN ({ph}) AND event_date >= ?
                        GROUP BY symbol, event_date
                        ORDER BY event_date ASC""",
                    list(all_syms) + [today.strftime('%Y-%m-%d')]
                ).fetchall()
                if not rows:
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
                            try:
                                _ev = r['event_name']
                            except Exception:
                                _ev = None
                            earnings.append({
                                'symbol':   r['symbol'],
                                'date':     r['earnings_date'],
                                'days_to':  days_to,
                                'event':    _ev,
                                'verticals': ' + '.join(verts),
                                'urgency':  'HIGH' if days_to <= 7 else ('WARN' if days_to <= 14 else 'INFO'),
                            })
                    except Exception:
                        pass
    except Exception:
        pass

    # Sep 22 2026: this was capped at the same 30-day cutoff as earnings, and
    # MACRO_EVENTS' nearest future entry is 2026-11-04 — so the card rendered empty
    # while four real events sat in the list. Show the next few regardless of
    # distance; "nothing for six weeks" is itself worth knowing, and an empty card
    # does not say that.
    macro = []
    for evt in MACRO_EVENTS:
        try:
            ed = datetime.strptime(evt['date'], '%Y-%m-%d').date()
            if ed >= today:
                macro.append({**evt, 'days_to': (ed - today).days})
        except Exception:
            pass
    macro.sort(key=lambda x: x['date'])
    macro = macro[:4]

    # If nothing dated is pending for the symbols we actually hold, fall back to the
    # next catalysts anywhere in the universe rather than rendering a blank card.
    # catalyst_calendar's event_date is written by an LLM and is free text half the
    # time ("TBD", "ongoing", "tonight"), so only well-formed dates are usable —
    # 1,221 of 2,437 rows qualify.
    if not earnings:
        try:
            with _db() as c:
                rows = c.execute(
                    """SELECT symbol, event_date, MIN(event_name) AS event_name
                       FROM catalyst_calendar
                       WHERE event_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                         AND event_date >= ?
                       GROUP BY symbol, event_date
                       ORDER BY event_date ASC LIMIT 6""",
                    (today.strftime('%Y-%m-%d'),)).fetchall()
            for r in rows:
                ed = datetime.strptime(r['event_date'], '%Y-%m-%d').date()
                earnings.append({
                    'symbol': r['symbol'], 'date': r['event_date'],
                    'days_to': (ed - today).days, 'event': r['event_name'],
                    'verticals': 'watch', 'held': False,
                })
        except Exception:
            pass

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
                # Sep 22 2026: show how OLD this reading is. Book Health reads the last 10
                # days that produced enriched A+ signals — if a side stops generating them
                # the window silently freezes and the panel keeps reporting a months-old
                # verdict as if it were live. SHORT has produced no A+ signal since
                # 2026-07-31, so it was showing "ON +0.71%" from July data.
                _age = None
                try:
                    _age = (datetime.strptime(today, '%Y-%m-%d')
                            - datetime.strptime(max(days), '%Y-%m-%d')).days
                except Exception:
                    pass
                out['books'][d] = {
                    'state': 'ON' if h > 0 else 'OFF',
                    'drift': round(h, 2), 'n': len(rows),
                    'age_days': _age, 'last_signal': max(days) if days else None,
                    'stale': (_age is not None and _age > 10),
                    'desc': (f"Own A+ {d} signals {'gained' if h > 0 else 'faded'} "
                             f"{h:+.2f}% per signal after firing, over the last 10 sessions "
                             f"that produced signals ({len(rows)} signals, newest "
                             f"{max(days) if days else '?'}). Book trades only while this "
                             f"is positive."),
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
                "SELECT COUNT(*), ROUND(SUM(" + OPT_PNL_SQL + "),0) "
                "FROM options_trades WHERE status='CLOSED' "
                "AND exit_date >= date('now','-14 day')").fetchone()
            opt['closed_14d'] = {'n': r[0] or 0, 'pnl': r[1] or 0}
            # Options circuit breaker (Sep 22 2026). This blocked EVERY options entry
            # for the whole paper trial and was visible nowhere: lifetime realized was
            # -$5,333 against a $5,000 limit, so the echo bailed before looking at a
            # single candidate. Scoped to OPTIONS_CB_SINCE now, and surfaced here so a
            # tripped breaker can never again be mistaken for "no opportunities".
            try:
                # options/ is not on the path by default in this process — same
                # pattern as get_turbo_ladder() below. Without it the import fails,
                # the except swallows it, and the breaker chip silently disappears:
                # exactly the invisibility that let a tripped breaker block the whole
                # options trial unnoticed.
                import sys as _sys, importlib
                _op = os.path.join(BASE_DIR, 'options')
                if _op not in _sys.path:
                    _sys.path.insert(0, _op)
                _ot = importlib.import_module('options_trader')
                _since = getattr(_ot, 'OPTIONS_CB_SINCE', None)
                _lim = getattr(_ot, 'OPTIONS_CIRCUIT_BREAKER', 5000.0)
                _sql = ("SELECT COALESCE(SUM(CASE WHEN strategy IN "
                        "('BULL_PUT_CREDIT','BEAR_CALL_CREDIT') THEN premium_paid - exit_value "
                        "ELSE exit_value - premium_paid END),0.0) FROM options_trades "
                        "WHERE status='CLOSED'")
                _a = ()
                if _since:
                    _sql += " AND exit_date >= ?"; _a = (_since,)
                _scoped = round(float(c.execute(_sql, _a).fetchone()[0]), 2)
                opt['breaker'] = {
                    'tripped': _scoped < -_lim, 'pnl': _scoped, 'limit': _lim,
                    'since': _since, 'frozen': bool(getattr(_ot, 'EQUITY_ECHO_FROZEN', False)),
                    'gate_mode': getattr(_ot, 'EDGE_BUDGET_MODE', '?'),
                }
            except Exception as _be:
                opt['breaker'] = {'tripped': None, 'err': str(_be)[:80]}

            # Fleet capital — what each book has at risk vs what it was allocated.
            # Added Sep 22 2026: "how much is actually invested" had no answer on the
            # dashboard, and four books trading one account makes it non-obvious.
            fleet = []
            for _name, _tbl, _alloc, _st in (
                    ('Day Trader', 'trades', 10000.0, "status='OPEN' AND setup_type!='RECONCILED'"),
                    ('Wave Rider', 'wave_trades', 10000.0, "status='OPEN'"),
                    ('Contrarian', 'contrarian_trades', 10000.0, "status='OPEN'"),
                    ('Clockwork', 'overnight_trades', 10000.0,
                     "status IN ('OPEN','PENDING_ENTRY','PENDING_EXIT')")):
                try:
                    _cols = {r[1] for r in c.execute('PRAGMA table_info(' + _tbl + ')')}
                    if not _cols:
                        continue
                    _m = " AND mode='LIVE'" if 'mode' in _cols else ''
                    _r = c.execute("SELECT COUNT(*), COALESCE(SUM(shares*entry_price),0) "
                                   "FROM " + _tbl + " WHERE " + _st + _m).fetchone()
                    fleet.append({'name': _name, 'n': _r[0] or 0,
                                  'deployed': round(float(_r[1] or 0), 0), 'alloc': _alloc})
                except Exception:
                    continue
            out['fleet'] = {'books': fleet,
                            'deployed': round(sum(f['deployed'] for f in fleet), 0),
                            'alloc': round(sum(f['alloc'] for f in fleet), 0)}

            # Scoring loop (Sep 22 2026) — the grader's inputs and the outcome label it
            # gets scored against. Both must keep accumulating or the weight fit that
            # this instrumentation exists for can never be run. The forward label is
            # written by com.sushil.trading.scan_forward_label at 17:15 weekdays.
            try:
                _r = c.execute(
                    """SELECT COUNT(*),
                              SUM(score_components IS NOT NULL),
                              SUM(fwd_mfe_pct IS NOT NULL)
                       FROM scan_log WHERE grade IN ('A+','A') AND direction='LONG'"""
                ).fetchone()
                _lbl = c.execute(
                    "SELECT MAX(scan_date) FROM scan_log WHERE fwd_mfe_pct IS NOT NULL"
                ).fetchone()[0]
                _tr = c.execute(
                    """SELECT COUNT(*) FROM trades t JOIN scan_log s
                         ON s.symbol=t.symbol AND s.scan_date=t.entry_date
                        AND s.direction='LONG' AND s.grade IN ('A+','A')
                        AND s.score_components IS NOT NULL
                       WHERE t.status IN ('WIN','LOSS') AND t.setup_type!='RECONCILED'"""
                ).fetchone()[0]
                out['scoring'] = {'graded': _r[0] or 0, 'with_components': _r[1] or 0,
                                  'with_label': _r[2] or 0, 'last_label': _lbl,
                                  'trades_scorable': _tr or 0}
            except Exception:
                out['scoring'] = {}

            # Data feed + watchdog (Sep 22 2026). Everything downstream degrades
            # silently if the 5-min bars stop arriving or a trader's loop dies:
            # Book Health, the forward label, the swing engines' signals and every
            # backtest all read bars_5m. The heartbeat files are written by
            # futures/heartbeat.py on every scan — silence means the LOOP stopped,
            # not that no trade qualified.
            feed = {}
            # ⚠️ CACHED ON PURPOSE. market_data.db is ~12 GB and ts_utc only exists
            # inside a composite (symbol, ts_utc) index, so ANY max-over-all-symbols
            # is a full scan taking ~5-6 seconds. Running that on every dashboard
            # poll held a read lock long enough to make collect_bars fail its commit
            # with "database is locked" — it lost half of Sep 22's bars that way.
            # The value changes once a day, when the collector runs, so a 10-minute
            # cache costs nothing and removes the contention entirely.
            try:
                _now = time.time()
                if _now - _BARS_CACHE.get('at', 0) > 600:
                    _md = os.path.join(BASE_DIR, 'market_data.db')
                    _mc = sqlite3.connect(f'file:{_md}?mode=ro', uri=True, timeout=2)
                    _mc.execute('PRAGMA query_only = 1')
                    _last = _mc.execute("SELECT MAX(ts_utc) FROM bars_5m").fetchone()[0]
                    _mc.close()
                    _BARS_CACHE.update(at=_now, last=(_last or ''))
                _last = _BARS_CACHE.get('last') or ''
                feed['bars_last'] = _last.replace('T', ' ')[:16]
                if _last:
                    _d = datetime.strptime(_last[:10], '%Y-%m-%d')
                    feed['bars_age_days'] = (datetime.now() - _d).days
            except Exception as _fe:
                feed['err'] = str(_fe)[:60]
            beats = []
            try:
                _hb = os.path.join(BASE_DIR, 'logs', 'heartbeat')
                for _f in sorted(os.listdir(_hb)) if os.path.isdir(_hb) else []:
                    if not _f.endswith('.json') or _f.startswith('_') or 'exitmap' in _f:
                        continue
                    _age = int(time.time() - os.path.getmtime(os.path.join(_hb, _f)))
                    beats.append({'name': _f[:-5], 'age_s': _age})
            except Exception:
                pass
            feed['beats'] = beats
            out['feed'] = feed

            # Prop-account room (Sep 22 2026). TC runs under a trailing Max Loss
            # Limit, and on Sep 3 it froze itself out of trading by $45 with no
            # indication anywhere — check_can_trade() refuses everything once
            # balance+unrealised drops under (high_water_mark - MLL + buffer), and
            # it cannot earn its way back because it cannot trade. Surfacing the
            # remaining room is the whole warning.
            try:
                import json as _json
                with open(os.path.join(BASE_DIR, 'futures', 'prop_state.json')) as _pf:
                    _ps = _json.load(_pf)
                _hwm = float(_ps.get('high_water_mark') or 0)
                _bal = float(_ps.get('balance') or 0)
                # floor/buffer come from the plan (prop_rules.TC_ACCOUNT_SIZE), published into the
                # state file by reconcile_from_ledger; the fallback is the old $50K arithmetic.
                _floor = float(_ps.get('floor') or max(_hwm - 2000.0, 48000.0))
                _buf   = float(_ps.get('buffer') or 300.0)
                out['prop'] = {
                    'mode': _ps.get('mode'), 'balance': round(_bal, 2),
                    'hwm': round(_hwm, 2), 'floor': round(_floor, 2),
                    'room': round(_bal - _floor - _buf, 2),
                    'plan': _ps.get('account_size'),
                    'profit_target': _ps.get('profit_target'),
                    'target': round(float(_ps.get('total_profit') or 0), 2),
                }
            except Exception:
                out['prop'] = {}

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
    # Trade Cop — the verdict for the WHOLE run, not one leg of it.
    #
    # Sep 22 2026: this read only lines containing an arrow, which is emitted by the
    # futures NY and London legs alone. The equity and options invariant lines have no
    # arrow, so they were invisible here — which is why this card could report
    # "in agreement" on the very day the cop exited 1 over six equity findings. It was
    # not wrong about futures; it simply never looked at three quarters of the report.
    try:
        import re as _re
        with open(os.path.join(BASE_DIR, 'logs', 'parity.log')) as f:
            raw = [l.strip() for l in f if l.strip()]
        # Take the LAST run only. Runs are delimited by a futures-NY line, which every
        # non-weekend run emits first.
        starts = [i for i, l in enumerate(raw) if '[futures NY]' in l or 'weekend — skip' in l]
        run = raw[starts[-1]:] if starts else raw[-40:]

        legs, findings = [], []
        for l in run:
            body = l.split('] ', 1)[-1]
            if '→' in body:
                leg = _re.search(r'\[([^\]]+)\]', body)
                legs.append((leg.group(1) if leg else 'futures',
                             'DIVERGENCE' if 'DIVERGENCE' in body else 'OK'))
            elif body.startswith('equity invariant:') or body.startswith('options invariant:'):
                findings.append(body)
            elif body.startswith('NOTE'):
                pass   # informational, never a divergence

        # A NOTE line is carried by equity_invariants but is explicitly not a finding.
        real = [f for f in findings if 'NOTE (not a divergence)' not in f]
        bad_legs = [n for n, s in legs if s == 'DIVERGENCE']
        status = 'DIVERGENCE' if (bad_legs or real) else 'OK'

        day = ''
        m = _re.search(r'parity (\S+)', run[0] if run else '')
        if m:
            day = m.group(1)

        if status == 'OK':
            friendly = (f"{day}: all legs agree — "
                        f"{', '.join(n for n, _ in legs) or 'no legs ran'}")
        else:
            bits = []
            if bad_legs:
                bits.append('replay disagrees on ' + ', '.join(bad_legs))
            if real:
                bits.append(f"{len(real)} invariant finding(s)")
            friendly = (f"{day}: " + '; '.join(bits) +
                        ". Read logs/parity.log before trusting a backtest.")
        out['parity'] = {
            'status': status,
            'detail': ' | '.join(f'{n}:{s}' for n, s in legs) or 'no legs ran',
            'friendly': friendly,
            'findings': real[:8],
        }
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



# ══════════════════════════════════════════════════════════════════════════════
# CLOSE CONTROLS — the dashboard asks; the owning process acts
# ══════════════════════════════════════════════════════════════════════════════
#
# Read the long note above control_queue in database.py before changing anything here.
# The short version: this file must never place an order. Every book below is owned by a
# process that holds state the dashboard cannot see — auto_trader's open_positions and
# session_high, the futures traders' in-memory running peak (which the Reversal Exit is
# computed from), and london_trader's entire position, which exists only as a module
# global. Closing any of those from here would leave the owner managing a phantom and
# closing it again. That is the shape of all four order incidents in this codebase.
#
# So: we write an intent, the owner executes it through the same path its Telegram
# command uses, and we report back what actually happened.

# Books owned by a long-running daemon that polls control_queue on its own loop.
_DAEMON_TARGETS = {
    'equity':     ('Day Trader', 15),   # auto_trader, 15s poll
    'options':    ('Options',    10),   # options_trader, 10s loop
    'futures_ny': ('Futures NY', 10),   # futures_trader / tc_trader, 10s poll
    'london':     ('London',     15),   # london_trader, 15s monitor
}

# Books run as one-shots by launchd every 5 minutes. Queuing alone would mean waiting up
# to five minutes for a panic button, so after queuing we start the engine's own
# --drain-only entry point immediately. That entry point does NOT scan or enter.
_ENGINE_TARGETS = {
    'wave_rider': ('Wave Rider', 'com.sushil.trading.wave_rider',  'factory.live.wave_rider'),
    'contrarian': ('Contrarian', 'com.sushil.trading.contrarian',  'factory.live.contrarian'),
    'clockwork':  ('Clockwork',  'com.sushil.trading.clockwork',   'factory.live.overnight'),
}

# book label (as get_equity_positions stamps it) -> control target
_BOOK_TARGET = {
    'Day Trader': 'equity', 'Wave Rider': 'wave_rider',
    'Contrarian': 'contrarian', 'Clockwork': 'clockwork',
}


def _engine_env(label):
    """The engine's env READ FROM ITS OWN PLIST, never hardcoded here.

    This is load-bearing, not tidiness. Every engine resolves MODE from an environment
    variable and its get_open() filters on `mode=MODE`, so an engine started without it
    defaults to SHADOW and sees none of the LIVE positions — it would report "no matching
    open position" and close nothing, while the UI said the request was sent.

    Hardcoding the value instead is precisely the Aug 3 2026 incident: a manual close run
    outside the real launch environment had ACCOUNT_MODE silently default to TC while the
    bridge still pointed at IBKR. Reading the plist means this can never drift from what
    launchd actually runs.
    """
    env, logpath = dict(os.environ), None
    try:
        out = subprocess.run(
            ['plutil', '-convert', 'json', '-o', '-',
             os.path.expanduser(f'~/Library/LaunchAgents/{label}.plist')],
            capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            d = json.loads(out.stdout)
            env.update(d.get('EnvironmentVariables') or {})
            # Also take the log destination from the plist, so a close we start lands in
            # the SAME file as the scheduled runs. Sending it to DEVNULL instead leaves a
            # real order with no trace in the place anyone would look for it.
            logpath = d.get('StandardOutPath')
    except Exception:
        pass
    return env, logpath


def _csrf_token():
    """Per-session token. The dashboard is reachable on a public Tailscale Funnel URL behind one
    password and a 30-day cookie, so a close endpoint must not be triggerable by a
    cross-site form post from a page the user happens to have open."""
    tok = session.get('csrf')
    if not tok:
        tok = base64.urlsafe_b64encode(os.urandom(24)).decode()
        session['csrf'] = tok
    return tok


@app.route('/api/close', methods=['POST'])
def api_close():
    """Queue a close. Never places an order — see the note at the top of this section."""
    body = request.get_json(silent=True) or {}

    if body.get('csrf') != session.get('csrf'):
        return jsonify({'ok': False, 'error': 'stale page — reload and try again'}), 403

    action = (body.get('action') or '').upper()
    if action not in ('CLOSE_ONE', 'CLOSE_ALL'):
        return jsonify({'ok': False, 'error': 'bad action'}), 400

    # CLOSE_ALL re-authenticates. A single position is a contained mistake; flattening a
    # book from a phone left unlocked, or a session cookie lifted from a public URL, is not.
    if action == 'CLOSE_ALL' and _DASH_PASSWORD:
        if body.get('password') != _DASH_PASSWORD:
            return jsonify({'ok': False, 'error': 'password incorrect'}), 403

    target = (body.get('target') or '').strip()
    from database import (CONTROL_TARGETS, queue_control_request,
                          get_control_requests, REQUEST_TTL_SEC)
    if target not in CONTROL_TARGETS:
        return jsonify({'ok': False, 'error': f'unknown target {target}'}), 400

    who = request.headers.get('X-Forwarded-For') or request.remote_addr or '?'
    try:
        rid = queue_control_request(
            target, action,
            symbol=body.get('symbol'),
            trade_id=body.get('trade_id'),
            book=body.get('book'),
            account_mode=body.get('account_mode'),
            source='dashboard', requested_by=who[:60])
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

    # One-shot engines: start the drain now rather than wait for the 5-min tick.
    spawned = None
    if target in _ENGINE_TARGETS:
        _name, label, module = _ENGINE_TARGETS[target]
        try:
            env, logpath = _engine_env(label)
            sink = open(logpath, 'a') if logpath else subprocess.DEVNULL
            subprocess.Popen(
                [sys.executable, '-m', module, '--drain-only'],
                cwd=BASE_DIR, env=env, stdout=sink, stderr=sink)
            spawned = True
        except Exception:
            spawned = False   # still queued; the engine's next scheduled pass serves it

    owner = (_DAEMON_TARGETS.get(target, (None, None))[0]
             or _ENGINE_TARGETS.get(target, (None,))[0] or target)
    wait_s = _DAEMON_TARGETS.get(target, (None, 15))[1]
    return jsonify({
        'ok': True, 'request_id': rid, 'owner': owner, 'spawned': spawned,
        'expires_in_s': REQUEST_TTL_SEC,
        'message': (f'Sent to {owner}. It closes through its own exit path; '
                    f'this page never places orders.'
                    + ('' if spawned else f' Expect it within ~{wait_s}s.')),
        'request': get_control_requests([rid])[0],
    })


@app.route('/api/close/status')
def api_close_status():
    """Poll the outcome of queued requests so the UI reports what REALLY happened —
    not merely that the button was pressed."""
    ids = [int(i) for i in (request.args.get('ids') or '').split(',') if i.strip().isdigit()]
    if not ids:
        return jsonify({'requests': []})
    from database import get_control_requests
    return jsonify({'requests': get_control_requests(ids)})


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
    # health must be computed BEFORE alerts — get_alerts() now reads it
    health     = get_system_health()
    alerts     = get_alerts(eq_pos, opt_pos, health, services)
    activity   = get_activity(5)
    earnings, macro = get_calendar(opt_pos, eq_pos)
    sectors    = get_sector_grades()
    engines    = get_engine_scoreboard()

    prod_avail = PROD_BRIDGE_URL is not None
    prod_bridge = get_bridge_info(PROD_BRIDGE_URL) if prod_avail else None

    return jsonify({
        'csrf':         _csrf_token(),
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
        'totals':       get_totals(),
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
