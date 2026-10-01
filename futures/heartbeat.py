"""Heartbeat + watchdog for the trading services.

WHY THIS EXISTS (Sep 3 2026): the Mac rebooted at 01:29 and, because no futures
service had RunAtLoad, nothing restarted until its next cron — the gateway at
02:50, the traders at 08:00. The entire London 3-8am window went untraded and
NOBODY WAS TOLD. It was found by reading logs a day later. RunAtLoad is now set,
but that only fixes the one failure mode we happened to hit; a hang, a crash
loop, a dead gateway or a wedged bridge would still be silent.

Two halves, deliberately separate:
  writer  — each trader calls beat() every scan cycle, stamping a tiny JSON file.
            Cheap, no network, no DB.
  checker — this module run as `python -m futures.heartbeat --check` from launchd,
            independent of the traders, so it still fires when they are dead.
            That independence is the whole point: a watchdog inside the process
            it watches cannot report that process dying.

Alerts are DEDUPED to once per issue per ALERT_REPEAT_MIN so a weekend outage
does not produce 400 Telegram messages (the Jul 20 2026 USAR lesson).
"""
from __future__ import annotations
import argparse, json, os, sys, time
from datetime import datetime, timedelta

import pytz, requests

ET   = pytz.timezone('America/New_York')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BEAT_DIR = os.path.join(ROOT, 'logs', 'heartbeat')
STATE    = os.path.join(BEAT_DIR, '_alert_state.json')

# A service is stale if its beat is older than this. Generous vs the 60s scan
# loop so a single slow bridge call is never an alert.
STALE_SEC = {'futures_ibkr': 300, 'futures_tc': 300, 'london': 300}

# Hours (ET) during which each service is SUPPOSED to be beating. Outside its own
# window a missing beat is normal, not a fault. Without this the watchdog would
# report 'london: NO heartbeat file' every 5 minutes from 9am to 3am and you would
# very quickly learn to ignore it — the same alert-fatigue failure as the Jul 20
# 2026 USAR retry storm. A watchdog that cries wolf is worse than none.
#   NY traders  : run_scan is an unconditional interval job, so they beat 24/5.
#   london      : APScheduler cron hour='3-8', so it only beats 03:00-08:59 ET.
ACTIVE_HOURS = {'futures_ibkr': (0, 24), 'futures_tc': (0, 24), 'london': (3, 9)}
GRACE_MIN    = 10       # allow this long after a window opens before alerting
ALERT_REPEAT_MIN = 60      # re-alert the same issue at most this often
BRIDGES = {'IBKR bridge': 'http://localhost:8000', 'TC bridge': 'http://localhost:8002'}

# trades.db is shared by every book. On Oct 1 2026 one process (options_trader) was left
# holding a half-finished write from 17:04 to 18:10: every other service's reads AND writes
# failed with "database is locked" for 65 minutes — auto_trader, both futures traders, the
# dashboard, the 17:05/17:15 jobs — and nothing alerted, because each service only logged
# its own error. A normal write holds the lock for milliseconds, so a read that cannot get
# in within DB_LOCK_WAIT_SEC means something is stuck. This check alerts at ANY hour: the
# London session (03:00) and the 23:00 learner both need the database.
DB_PATH = os.path.join(ROOT, 'trades.db')
DB_LOCK_WAIT_SEC = 15
DB_ISSUE_KEY = 'trades.db'
# script -> launchd label, so the alert can say exactly what to restart
SERVICE_LABELS = {'options_trader.py': 'options_trader', 'auto_trader.py': 'autotrader',
                  'futures_trader.py': 'futures_personal', 'tc_trader.py': 'futures_trader',
                  'app.py': 'dashboard', 'watchman.py': 'watchman', 'news_engine.py': 'news_engine'}


# ── writer half (imported by the traders) ────────────────────────────────────

def beat(name: str, extra: dict | None = None) -> None:
    """Stamp this service as alive. Must never raise into the caller."""
    try:
        os.makedirs(BEAT_DIR, exist_ok=True)
        p = os.path.join(BEAT_DIR, f'{name}.json')
        tmp = p + '.tmp'
        with open(tmp, 'w') as fh:
            json.dump({'ts': time.time(),
                       'iso': datetime.now(ET).isoformat(),
                       'pid': os.getpid(), **(extra or {})}, fh)
        os.replace(tmp, p)          # atomic — a reader never sees a half file
    except Exception:
        pass


# ── checker half (run from launchd, outside the traders) ─────────────────────

def _telegram(msg: str) -> None:
    tok = os.getenv('TELEGRAM_TOKEN'); chat = os.getenv('TELEGRAM_CHAT_ID')
    if not tok or not chat:
        print('  (no telegram creds — alert not sent)'); return
    try:
        requests.post(f'https://api.telegram.org/bot{tok}/sendMessage',
                      json={'chat_id': chat, 'text': msg}, timeout=10)
    except Exception as e:
        print(f'  telegram send failed: {e}')


def _load(p, default):
    try:
        with open(p) as fh: return json.load(fh)
    except Exception: return default


def _should_alert(key: str, state: dict) -> bool:
    last = state.get(key)
    if last is None: return True
    return (time.time() - last) > ALERT_REPEAT_MIN * 60


def _db_lock_holder(path: str):
    """PID holding SQLite's write locks on `path`, or None.

    SQLite's unix VFS locks fixed bytes of the file (PENDING at 0x40000000, RESERVED +1,
    the SHARED range +2..+511), and F_GETLK reports the PID of a conflicting holder. The
    struct flock layout below is macOS's, so other platforms return None.
    """
    if sys.platform != 'darwin':
        return None
    import fcntl, struct
    pending = 0x40000000
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return None
    try:
        for start, length in ((pending, 1), (pending + 1, 1), (pending + 2, 510)):
            req = struct.pack('qqihh', start, length, 0, fcntl.F_WRLCK, os.SEEK_SET)
            _, _, pid, ltype, _ = struct.unpack('qqihh', fcntl.fcntl(fd, fcntl.F_GETLK, req))
            if ltype != fcntl.F_UNLCK:
                return pid
    except OSError:
        return None
    finally:
        os.close(fd)
    return None


def _db_issue(path: str = DB_PATH):
    """None if the database is readable within DB_LOCK_WAIT_SEC, else a one-line issue."""
    import sqlite3, subprocess
    try:
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=DB_LOCK_WAIT_SEC)
        try:
            con.execute('SELECT 1 FROM sqlite_master LIMIT 1').fetchone()
        finally:
            con.close()
        return None
    except sqlite3.OperationalError as e:
        if 'locked' not in str(e).lower():
            return f'{DB_ISSUE_KEY}: UNREADABLE ({e})'
    pid = _db_lock_holder(path)
    who, fix = 'an unknown process', ''
    if pid:
        try:
            cmd = subprocess.run(['ps', '-o', 'command=', '-p', str(pid)],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            script = next((os.path.basename(t) for t in cmd.split() if t.endswith('.py')), '')
            who = f'{script or cmd[:60]} (pid {pid})'
            if script in SERVICE_LABELS:
                fix = (f' — fix: launchctl kickstart -k gui/$(id -u)/'
                       f'com.sushil.trading.{SERVICE_LABELS[script]}')
        except Exception:
            who = f'pid {pid}'
    return (f'{DB_ISSUE_KEY}: LOCKED by {who} for >{DB_LOCK_WAIT_SEC}s — every service\'s '
            f'database reads and writes are failing{fix}')


def _is_trading_window() -> bool:
    """London IB starts 3am ET; NY closes 4pm. Outside that, silence is normal."""
    now = datetime.now(ET)
    return now.weekday() < 5 and 3 <= now.hour < 16


def check(force: bool = False) -> int:
    issues: list[str] = []
    state = _load(STATE, {})

    now = datetime.now(ET)
    for name, limit in STALE_SEC.items():
        lo, hi = ACTIVE_HOURS.get(name, (0, 24))
        weekday = now.weekday() < 5
        in_own_window = weekday and lo <= now.hour < hi
        if not in_own_window:
            continue                      # not due to be beating — silence is correct
        # grace period just after the window opens (service may still be spinning up)
        if now.hour == lo and now.minute < GRACE_MIN:
            continue
        p = os.path.join(BEAT_DIR, f'{name}.json')
        b = _load(p, None)
        if b is None:
            issues.append(f'{name}: NO heartbeat file (should be running {lo:02d}:00-{hi:02d}:00 ET)')
            continue
        age = time.time() - float(b.get('ts', 0))
        if age > limit:
            issues.append(f'{name}: SILENT for {age/60:.0f}min '
                          f'(last {b.get("iso","?")[11:19]}, pid {b.get("pid","?")})')

    for label, url in BRIDGES.items():
        try:
            r = requests.get(url + '/', timeout=8).json()
            if not r.get('connected'):
                issues.append(f'{label}: reachable but IB NOT CONNECTED '
                              f'(gateway down? account={r.get("account")})')
        except Exception as e:
            issues.append(f'{label}: UNREACHABLE ({type(e).__name__})')

    db = _db_issue()
    if db:
        issues.append(db)

    stamp = datetime.now(ET).strftime('%H:%M:%S')
    if not issues:
        print(f'[{stamp}] heartbeat OK — all services alive, both bridges connected')
        if state.get('_was_down') and _should_alert('_recovered', state):
            _telegram(f'✅ TRADING SERVICES RECOVERED ({stamp} ET) — all alive, bridges connected')
            state['_recovered'] = time.time()
        state['_was_down'] = False
        _save_state(state)
        return 0

    in_window = _is_trading_window()
    print(f'[{stamp}] heartbeat PROBLEM ({len(issues)} issue(s), '
          f'trading window={in_window}):')
    for i in issues: print('   - ' + i)

    due = [i for i in issues if force or _should_alert(i.split(':')[0], state)]
    # a stuck database alerts at any hour; everything else waits for the trading window
    to_alert = due if (in_window or force) else [i for i in due if i.startswith(DB_ISSUE_KEY)]
    if len(to_alert) < len(due):
        print('   (outside 03:00-16:00 ET trading window — logged, not alerted)')
    if to_alert:
        # Say WHAT IS STILL WORKING, not just what broke. The old text ended with a blanket
        # "Nothing is trading until this is fixed", which on Sep 7 2026 was simply false: only
        # the TC gateway was down, IBKR traded the whole London session normally. An alert that
        # overstates its own scope is the one you learn to ignore — the same alert-fatigue
        # failure as the Jul 20 USAR retry storm.
        broken = {i.split(':')[0] for i in issues}
        healthy = [k for k in list(STALE_SEC) + list(BRIDGES) if k not in broken]
        tail = ('\n\nStill healthy: ' + ', '.join(healthy)) if healthy else \
               '\n\nNothing is trading until this is fixed.'
        _telegram('🚨 TRADING WATCHDOG — ' + stamp + ' ET\n' + '\n'.join('• ' + i for i in to_alert)
                  + tail)
        for i in to_alert: state[i.split(':')[0]] = time.time()
    state['_was_down'] = True
    _save_state(state)
    return 1


def _save_state(state: dict) -> None:
    try:
        os.makedirs(BEAT_DIR, exist_ok=True)
        with open(STATE, 'w') as fh: json.dump(state, fh)
    except Exception: pass


def status() -> None:
    print(f'heartbeat dir: {BEAT_DIR}')
    for name in STALE_SEC:
        b = _load(os.path.join(BEAT_DIR, f'{name}.json'), None)
        lo, hi = ACTIVE_HOURS.get(name, (0, 24))
        now = datetime.now(ET)
        due = now.weekday() < 5 and lo <= now.hour < hi
        if b is None:
            print(f'  {name:<14} NO FILE' + ('' if due else f'  (not due — runs {lo:02d}-{hi:02d} ET)'))
            continue
        age = time.time() - float(b.get('ts', 0))
        flag = 'OK  ' if age <= STALE_SEC[name] else 'STALE'
        print(f'  {name:<14} {flag} last beat {age:>6.0f}s ago  pid {b.get("pid")}  {b.get("iso","")[11:19]}')
    for label, url in BRIDGES.items():
        try:
            r = requests.get(url + '/', timeout=8).json()
            print(f'  {label:<14} connected={r.get("connected")} account={r.get("account")}')
        except Exception as e:
            print(f'  {label:<14} UNREACHABLE {type(e).__name__}')
    print(f'  {DB_ISSUE_KEY:<14} {_db_issue() or "readable"}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--check',  action='store_true', help='run the watchdog (launchd entry point)')
    ap.add_argument('--status', action='store_true', help='print current heartbeat ages')
    ap.add_argument('--force',  action='store_true', help='alert even outside the trading window')
    a = ap.parse_args()
    if a.status: status(); sys.exit(0)
    if a.check:  sys.exit(check(force=a.force))
    status()
