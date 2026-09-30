#!/usr/bin/env python
"""
Parity Harness v1 (Jul 18 2026, redesign build ①).

Constitution Article 4: the sim must match the machine. This job replays
TODAY through sim_replay.py with production-matching flags and diffs the
sim's trade decisions against what production actually did (futures_trades,
account_mode=IBKR). Any mismatch = the sim and the live system disagree
about the same day — a divergence to investigate BEFORE trusting any backtest.

v2 scope (Jul 19 2026 — all four books covered):
  - Futures NY: full entry diff vs sim_replay (SIM_FLAGS must track live config)
  - London: entry diff vs london_v2_sim champion config (LONDON_CFG), runs one
    day lagged (Databento 1m bars arrive next evening); exits excluded by
    design (15s live monitor vs 1m sim granularity)
  - Equity: decision invariants (graded signal trace, entry window, caps) —
    full bar-level replay is equity_replay.py, run manually
  - Options: decision invariants from OPTIONS_COP_SINCE (ENTER verdict trace,
    A+ signal trace, book-ON check) — no bar-level replay possible (option
    chains aren't stored)

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
             '--regime-aware-exits', '--stop-pts', '200',
             '--no-ovn-skip',   # OVN whole-day veto removed live Jul 18 2026
             '--dll', '1250',   # $5K futures allocation risk model (Jul 18 2026)
             '--rev-exit', '2,0.30,120',  # reversal-detection exit live Jul 25 2026
             '--partial', '150',          # partial scale-out live Jul 25 2026
             '--tide',                    # Daily Tide side gate live Sep 29 2026
             '--short-max-contracts', '1']  # short size cap live Sep 2 2026 —
                              # passed explicitly (it is also the sim default) so a
                              # future change to the module default cannot silently
                              # desync sim from live. NOTE: the cop matches on
                              # time+side, not size, so it cannot detect a size
                              # divergence on its own.

TRADE_RE = re.compile(
    r'^\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(LONG|SHORT)\s+(\S+)\s+\S+\s+([\d.]+)')


def log(msg):
    """Write once. The launchd plist redirects stdout AND stderr into this same file, so
    printing as well duplicated every single line — which is why parity.log reads as if
    each run happened twice. Print only when stdout is a terminal (a manual run)."""
    line = f"[{datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    if sys.stdout.isatty():
        print(line)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


def send_telegram(msg):
    """Divergences must reach a human, not just a log file (added Jul 18 2026)."""
    import requests
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, '.env'))
    token = os.getenv('FUTURES_TELEGRAM_TOKEN') or os.getenv('TELEGRAM_TOKEN')
    chat  = os.getenv('FUTURES_TELEGRAM_CHAT_ID') or os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={'chat_id': chat, 'text': msg}, timeout=10)
    except Exception:
        pass


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
           where entry_date=? and account_mode='IBKR' and setup_type!='RECONCILED'
           and coalesce(notes,'') not like 'partial of %'""",
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


# ── London leg (Jul 19 2026) ─────────────────────────────────────────────────
# Live london_trader.py champion config, expressed in london_v2_sim terms.
# UPDATE when live London config changes (and only then). Entry-side parity
# only: exits diverge by design (15s live monitor vs 1m sim granularity).
LONDON_CFG = {'vol_confirm': False, 'acceptance': False,
              'skip_day': False,     # veto removed live Jul 18 2026
              'be_mult': 0.10}


def london_check_day(day):
    """Most recent weekday ≤ day with 1m bars. Databento --update only reaches
    YESTERDAY (availability lag), so London parity runs one day behind — a
    constant lag, still full drift detection."""
    con = sqlite3.connect(os.path.join(ROOT, 'market_data.db'))
    row = con.execute(
        """select max(substr(ts_utc,1,10)) from futures_bars_1m
           where symbol='MNQ' and substr(ts_utc,1,10) <= ?""", (day,)).fetchone()
    con.close()
    d = row[0] if row else None
    if d and datetime.strptime(d, '%Y-%m-%d').weekday() >= 5:
        return None   # latest bar day is a weekend stub — nothing to check
    return d


def london_sim_trades(day):
    """Replay one London session through london_v2_sim's live-matching config."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'london_v2_sim', os.path.join(ROOT, 'futures', 'london_v2_sim.py'))
    lv2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lv2)
    bars = lv2.load_days(day, day)
    if bars.empty:
        return None   # no bars for the day yet — can't judge
    tr = lv2.simulate(bars, LONDON_CFG, day)
    if tr.empty:
        return []
    return [{'time': r['time'], 'side': r['side'], 'setup': 'LONDON',
             'entry': float(r['entry'])} for _, r in tr.iterrows()]


LONDON_COP_ACCOUNT = 'IBKR'   # london_v2_sim models ONE book; compare against one account

def london_live_trades(day):
    """Sep 22 2026 — this had no account_mode filter, and london_trader runs in BOTH the
    IBKR and TC processes off the same signal. Every London day therefore reported
    live = 2x sim and a permanent DIVERGENCE: Sep 17 "sim=2 live=4" was literally two
    trades times two accounts (rows 129/130 and 131/132, same minute, same price).
    The sim is a one-book model, so the honest comparison is against one account.
    Documented as a known gap on Aug 9 2026 and never fixed until now."""
    con = sqlite3.connect(os.path.join(ROOT, 'trades.db'))
    rows = con.execute(
        """select entry_time, side, entry from london_trades
           where entry_date=? and coalesce(account_mode,'IBKR')=?""",
        (day, LONDON_COP_ACCOUNT)).fetchall()
    con.close()
    return [{'time': (r[0] or '')[:5], 'side': r[1], 'setup': 'LONDON',
             'entry': r[2]} for r in rows]


# ── Options leg (Jul 19 2026) ────────────────────────────────────────────────
# No bar-level options replay exists (option-chain quotes aren't stored), so
# this is a decision-invariant cop, mirroring the equity one. Every options
# trade entered on/after the Jul 19 redesign must satisfy:
#   1. an ENTER verdict logged in opt_calc_log that day for the symbol
#   2. an A+ scan_log signal that day, same symbol, matching direction
#   3. the equity book for that direction was ON (or cold start) that day —
#      computed from scan_log exactly as _book_health_on does
OPTIONS_COP_SINCE = '2026-07-19'

_OPT_DIRECTION = {'BULL_SPREAD': 'LONG', 'LEAP': 'LONG', 'OPT_SCALP': 'LONG',
                  'BULL_PUT_CREDIT': 'LONG',
                  'BEAR_PUT_SPREAD': 'SHORT', 'BEAR_CALL_CREDIT': 'SHORT'}


def _book_on_asof(con, direction, day):
    """Trailing-10d A+ drift before `day` — mirrors options_trader._book_health_on
    (and auto_trader.compute_book_health). Returns True if ON or cold start."""
    days = [r[0] for r in con.execute(
        """select distinct scan_date from scan_log
           where grade='A+' and direction=? and scan_date<? and enriched=1
             and actual_day_pct is not null and intra_chg is not null
           order by scan_date desc limit 10""", (direction, day))]
    if len(days) < 4:
        return True
    q = ','.join('?' * len(days))
    rows = con.execute(
        f"""select actual_day_pct - intra_chg from scan_log
            where grade='A+' and direction=? and enriched=1
              and actual_day_pct is not null and intra_chg is not null
              and scan_date in ({q})""", (direction, *days)).fetchall()
    if len(rows) < 30:
        return True
    drifts = [r[0] for r in rows]
    fav = [-x for x in drifts] if direction == 'SHORT' else drifts
    return (sum(fav) / len(fav)) > 0


def options_invariants(day):
    """Returns list of violation strings (empty = clean)."""
    if day < OPTIONS_COP_SINCE:
        return []
    v = []
    try:
        con = sqlite3.connect(os.path.join(ROOT, 'trades.db'))
        trades = con.execute(
            """select symbol, strategy, entry_date from options_trades
               where entry_date=?""", (day,)).fetchall()
        for sym, strat, _ in trades:
            direction = _OPT_DIRECTION.get(strat)
            if direction is None:
                v.append(f"unknown strategy for cop: {sym} {strat}")
                continue
            n = con.execute(
                """select count(*) from opt_calc_log
                   where symbol=? and substr(run_at,1,10)=? and verdict='ENTER'""",
                (sym, day)).fetchone()[0]
            if n == 0:
                v.append(f"options trade without ENTER verdict: {sym} {strat}")
            n = con.execute(
                """select count(*) from scan_log where scan_date=? and symbol=?
                   and direction=? and grade='A+'""",
                (day, sym, direction)).fetchone()[0]
            if n == 0:
                v.append(f"options trade without A+ signal: {sym} {strat} ({direction})")
            if not _book_on_asof(con, direction, day):
                v.append(f"options trade with {direction} book OFF: {sym} {strat}")
        con.close()
    except Exception as e:
        v.append(f"options invariant check error: {e}")
    return v


def equity_context(day=None):
    try:
        con = sqlite3.connect(os.path.join(ROOT, 'trades.db'))
        day = day or datetime.now(ET).strftime('%Y-%m-%d')
        n = con.execute("select count(*) from trades where entry_date=? and setup_type!='RECONCILED'",
                        (day,)).fetchone()[0]
        con.close()
        return f"equity live trades today: {n}"
    except Exception as e:
        return f"equity context error: {e}"


def equity_invariants(day):
    """Decision-level equity cop (Jul 18 2026). Full bar-level equity replay is a
    separate build; until then, verify the invariants live decisions must satisfy:
      1. every live trade traces to a scan_log row (same symbol+day) that was
         graded A+/A — no trade without a graded signal behind it, EXCEPT on the
         scanner paths that structurally never write one (see NO_SCANLOG_SETUPS)
      2. entry times inside the legal window, READ FROM auto_trader's own constants
      3. daily per-direction caps respected
    Returns list of violation strings (empty = clean).

    Sep 22 2026 — this function had been crying wolf since Sep 4 and was simultaneously
    blind to the thing it exists to catch. Three fixes:

    (a) The window was HARDCODED 10:00-15:00. Live moved to 09:30-13:00 on Sep 4 2026.
        So every legitimate 09:30-09:59 entry was reported as a violation (six on Sep 21
        alone) — and, far worse, a genuine 13:00-15:00 entry would have passed SILENTLY.
        A cop with the wrong rule book does not merely annoy; it clears the guilty.
        The window is now imported from auto_trader, so it cannot drift again.

    (b) "no graded scan_log signal" fired on every CATALYST_OVERRIDE and PREMARKET trade
        because _scan_catalyst_override and _scan_premarket_catalyst call
        log_scan_candidate() ZERO times — grep-verified. A rule that is guaranteed to
        fail is not a test, it is noise, and noise is what makes a real alert invisible.
        Those paths are now reported once, as a known instrumentation gap, not per trade.
    """
    v = []
    try:
        # Read the live window rather than restating it. This is the whole fix for (a).
        try:
            sys.path.insert(0, ROOT)
            import auto_trader as _at
            win_lo = f"{_at.NO_ENTRY_BEFORE:02d}:{_at.NO_ENTRY_BEFORE_MIN:02d}"
            win_hi = f"{_at.NO_ENTRY_AFTER:02d}:00"
            win_src = "auto_trader"
        except Exception as e:
            return [f"equity invariant check error: cannot read the live entry window "
                    f"from auto_trader ({e}) — refusing to check against a guessed one"]

        # Paths that place trades without ever grading a candidate into scan_log. This is
        # a real gap in instrumentation (those trades have no recorded reasoning) but it
        # is a FIXED property of the code, so it is stated once rather than per trade.
        NO_SCANLOG_SETUPS = ('CATALYST_OVERRIDE', 'PREMARKET')

        con = sqlite3.connect(os.path.join(ROOT, 'trades.db'))
        trades = con.execute(
            """select symbol, entry_time, side, setup_type from trades
               where entry_date=? and setup_type!='RECONCILED'""", (day,)).fetchall()
        ungraded_by_design = []
        for sym, etime, side, setup in trades:
            t = (etime or '')[:5]
            if t and not (win_lo <= t < win_hi) and not ('09:20' <= t <= '09:29') \
                    and 'PREMARKET' not in (setup or ''):
                v.append(f"entry-window violation: {sym} {side} at {etime} ({setup}) "
                         f"— live window is {win_lo}-{win_hi} per {win_src}")
            direction = 'SHORT' if (side or '').upper() == 'SHORT' else 'LONG'
            row = con.execute(
                """select count(*) from scan_log where scan_date=? and symbol=?
                   and direction=? and grade in ('A+','A')""",
                (day, sym, direction)).fetchone()[0]
            if row == 0:
                if any(s in (setup or '') for s in NO_SCANLOG_SETUPS):
                    ungraded_by_design.append(f"{sym} ({setup})")
                else:
                    v.append(f"no graded scan_log signal behind trade: {sym} {direction} ({setup})")
        if ungraded_by_design:
            v.append("NOTE (not a divergence): "
                     f"{len(ungraded_by_design)} trade(s) from scanner paths that never "
                     f"write scan_log — {', '.join(ungraded_by_design)}. Known gap: "
                     f"_scan_catalyst_override/_scan_premarket_catalyst do not grade.")
        for d, cap in (('LONG', 20), ('SHORT', 20)):
            n = con.execute(
                """select count(*) from trades where entry_date=? and setup_type!='RECONCILED'
                   and (case when upper(side)='SHORT' then 'SHORT' else 'LONG' end)=?""",
                (day, d)).fetchone()[0]
            if n > cap:
                v.append(f"daily {d} cap exceeded: {n} > {cap}")
        con.close()
    except Exception as e:
        v.append(f"equity invariant check error: {e}")
    return v


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

    # London leg (entry-side parity; exits diverge by design at 15s vs 1m;
    # runs one day lagged — 1m bars arrive from Databento the next evening)
    lon_issues = 0
    try:
        lon_day = london_check_day(day)
        if lon_day is None:
            log(f"parity {day} [London]: no recent 1m bar day — skipped")
        else:
            lon_sim = london_sim_trades(lon_day)
            lon_live = london_live_trades(lon_day)
            if lon_sim is None:
                log(f"parity {lon_day} [London]: bars empty — skipped")
            else:
                lm, l_sim_only, l_live_only = diff(lon_sim, lon_live)
                lon_status = 'OK' if (l_sim_only == 0 and not l_live_only) else 'DIVERGENCE'
                log(f"parity {lon_day} [London]: sim={len(lon_sim)} live={len(lon_live)} "
                    f"matched={lm} sim-only={l_sim_only} live-only={len(l_live_only)} "
                    f"→ {lon_status}")
                if l_sim_only:
                    for s in lon_sim:
                        log(f"  london sim trade: {s}")
                for l in l_live_only:
                    log(f"  london live-only trade: {l}")
                if lon_status != 'OK':
                    status = 'DIVERGENCE'
                    lon_issues = l_sim_only + len(l_live_only)
    except Exception as e:
        log(f"parity {day} [London]: check error: {e}")

    log(equity_context(day))
    eq_v = equity_invariants(day)
    # A NOTE line is context, not a finding — equity_invariants emits one for the scanner
    # paths that structurally never write scan_log. Counting it as a divergence is what
    # kept this job on exit code 1 every day, which is how a real divergence would go
    # unnoticed: an alarm that is always on is not an alarm.
    eq_real = [x for x in eq_v if not x.startswith('NOTE ')]
    if eq_v:
        for line in eq_v:
            log(f"  equity invariant: {line}")
    if eq_real:
        status = 'DIVERGENCE'
    elif not eq_v:
        log("equity invariants: clean")

    opt_v = options_invariants(day)
    if opt_v:
        status = 'DIVERGENCE'
        for line in opt_v:
            log(f"  options invariant: {line}")
    else:
        log("options invariants: clean")

    if status != 'OK':
        send_telegram(
            f"🚓 Trade Cop DIVERGENCE {day}\n"
            f"Futures NY: sim={len(sim)} live={len(live)} matched={matched}\n"
            f"London entry mismatches: {lon_issues}\n"
            f"Equity invariant issues: {len(eq_real)} | Options: {len(opt_v)}\n"
            f"Details: logs/parity.log — investigate before trusting backtests."
        )
    return 0 if status == 'OK' else 1


if __name__ == '__main__':
    sys.exit(main())
