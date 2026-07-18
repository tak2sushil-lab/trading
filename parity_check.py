#!/usr/bin/env python
"""
Parity Harness v1 (Jul 18 2026, redesign build ①).

Constitution Article 4: the sim must match the machine. This job replays
TODAY through sim_replay.py with production-matching flags and diffs the
sim's trade decisions against what production actually did (futures_trades,
account_mode=IBKR). Any mismatch = the sim and the live system disagree
about the same day — a divergence to investigate BEFORE trusting any backtest.

v1 scope: futures NY only (sim_replay exists and claims to mirror live).
Equity: reports book-health values + live trade count as context (full
equity decision-diff is a later build).

Run after the close (manually or via launchd 22:40 ET):
    venv/bin/python parity_check.py [--date YYYY-MM-DD]
Appends to logs/parity.log. Exit code 1 on divergence (usable in alerts).
"""
import argparse, os, re, sqlite3, subprocess, sys
from datetime import datetime

import pytz

ET = pytz.timezone('America/New_York')
ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, 'logs', 'parity.log')

# Production-matching sim flags — UPDATE when live config changes (and only then)
SIM_FLAGS = ['--graduated-rvol', '--rvol-floor', '0.70',
             '--regime-aware-exits', '--stop-pts', '200']

TRADE_RE = re.compile(
    r'^\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(LONG|SHORT)\s+(\S+)\s+\S+\s+([\d.]+)')


def log(msg):
    line = f"[{datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


def sim_trades(day):
    cmd = [os.path.join(ROOT, 'venv', 'bin', 'python'),
           os.path.join(ROOT, 'futures', 'sim_replay.py'),
           '--start', day, '--end', day] + SIM_FLAGS
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=600).stdout
    trades = []
    for line in out.splitlines():
        m = TRADE_RE.match(line)
        if m and m.group(1) == day:
            trades.append({'time': m.group(2), 'side': m.group(3),
                           'setup': m.group(4), 'entry': float(m.group(5))})
    return trades


def live_trades(day):
    con = sqlite3.connect(os.path.join(ROOT, 'trades.db'))
    rows = con.execute(
        """select entry_time, side, setup_type, entry_price from futures_trades
           where entry_date=? and account_mode='IBKR' and setup_type!='RECONCILED'""",
        (day,)).fetchall()
    con.close()
    return [{'time': r[0][:5], 'side': r[1], 'setup': r[2], 'entry': r[3]}
            for r in rows]


def diff(sim, live):
    """Match by side within ±15 min; report unmatched on both sides."""
    def tmin(t):
        h, m = t.split(':'); return int(h) * 60 + int(m)
    unmatched_live = list(live)
    matched = 0
    for s in sim:
        hit = next((l for l in unmatched_live
                    if l['side'] == s['side'] and abs(tmin(l['time']) - tmin(s['time'])) <= 15), None)
        if hit:
            unmatched_live.remove(hit)
            matched += 1
    sim_only = len(sim) - matched
    return matched, sim_only, unmatched_live


def equity_context():
    try:
        con = sqlite3.connect(os.path.join(ROOT, 'trades.db'))
        day = datetime.now(ET).strftime('%Y-%m-%d')
        n = con.execute("select count(*) from trades where entry_date=? and setup_type!='RECONCILED'",
                        (day,)).fetchone()[0]
        con.close()
        return f"equity live trades today: {n}"
    except Exception as e:
        return f"equity context error: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=datetime.now(ET).strftime('%Y-%m-%d'))
    a = ap.parse_args()
    day = a.date
    if datetime.strptime(day, '%Y-%m-%d').weekday() >= 5:
        log(f"parity {day}: weekend — skip")
        return 0
    sim = sim_trades(day)
    live = live_trades(day)
    matched, sim_only, live_only = diff(sim, live)
    status = 'OK' if (sim_only == 0 and not live_only) else 'DIVERGENCE'
    log(f"parity {day} [futures NY]: sim={len(sim)} live={len(live)} matched={matched} "
        f"sim-only={sim_only} live-only={len(live_only)} → {status}")
    if sim_only:
        for s in sim:
            log(f"  sim trade: {s}")
    for l in live_only:
        log(f"  live-only trade (sim never took it): {l}")
    log(equity_context())
    return 0 if status == 'OK' else 1


if __name__ == '__main__':
    sys.exit(main())
