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

    to_alert = [i for i in issues if force or _should_alert(i.split(':')[0], state)]
    if to_alert and (in_window or force):
        _telegram('🚨 TRADING WATCHDOG — ' + stamp + ' ET\n' + '\n'.join('• ' + i for i in to_alert)
                  + '\n\nNothing is trading until this is fixed.')
        for i in to_alert: state[i.split(':')[0]] = time.time()
    elif to_alert:
        print('   (outside 03:00-16:00 ET trading window — logged, not alerted)')
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


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--check',  action='store_true', help='run the watchdog (launchd entry point)')
    ap.add_argument('--status', action='store_true', help='print current heartbeat ages')
    ap.add_argument('--force',  action='store_true', help='alert even outside the trading window')
    a = ap.parse_args()
    if a.status: status(); sys.exit(0)
    if a.check:  sys.exit(check(force=a.force))
    status()
