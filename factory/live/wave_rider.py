"""
Wave Rider — LIVE PAPER trader for the one Roster engine (momentum_wild).

Buys WILD stocks that popped ≥3% and are holding above their opening VWAP, ~10:00 ET,
holds 3 business days with an 8% stop, blocks around earnings. This is the swing-momentum
edge the Proving Ground validated (OOS alpha +0.5%, walk-forward 13/13).

SAFETY / ON-RAMP:
  MODE='SHADOW' (default) — records what it WOULD trade in the wave_trades table and marks
    them to live prices, but places NO orders. Zero account impact. This is how every
    component in this system goes live (instrument-first).
  MODE='LIVE' — places real orders on the IBKR *paper* account via the bridge.
  Flip only after eyeballing a day or two of SHADOW picks. One constant below.

Stateless between runs — all state lives in the DB, so launchd can fire this every 5 min
and a restart never loses a position. Run one pass:  python -m factory.live.wave_rider
Dry-scan a past day (no DB writes):  python -m factory.live.wave_rider --dryscan 2026-08-14
"""
from __future__ import annotations
import os, sys, sqlite3, time, datetime as dt
import requests
import pandas as pd
sys.path.insert(0, "/Users/sushil/trading")
from factory.live._fills import place_verified  # noqa: E402

LOCK = "/tmp/wave_rider.lock"   # prevents overlapping launchd runs (the scan can exceed 5 min)

# ─────────────────────────── config ───────────────────────────
MODE = os.environ.get("WAVE_RIDER_MODE", "SHADOW").upper()   # SHADOW | LIVE
BRIDGE = "http://localhost:8000"
DB = "/Users/sushil/trading/trades.db"
CACHE = "/Users/sushil/trading/factory/cache"
ET = None
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    pass

CAPITAL, SLOTS = 10_000.0, 5
PER_SLOT = CAPITAL / SLOTS
HOLD_DAYS = 3            # business days
STOP_PCT = 8.0
MOVE_MIN = 3.0          # % up vs prior close by scan time
PRICE_LO, PRICE_HI = 5.0, 800.0
EARNINGS_BLOCK_DAYS = 4  # skip if earnings within this many days
ENTRY_START = dt.time(10, 0)
ENTRY_END = dt.time(15, 0)
EOD_EXIT = dt.time(15, 45)
US_HOLIDAYS = {"2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"}


def log(msg: str):
    ts = dt.datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S") if ET else dt.datetime.now().isoformat()
    print(f"[{ts}] [WAVE RIDER {MODE}] {msg}", flush=True)


# ─────────────────────────── DB ───────────────────────────
def init_db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS wave_trades(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT, entry_date TEXT, entry_time TEXT, entry_price REAL,
        shares INTEGER, stop_price REAL, exit_on_date TEXT,
        status TEXT DEFAULT 'OPEN', exit_date TEXT, exit_time TEXT, exit_price REAL,
        pnl REAL, pnl_pct REAL, exit_reason TEXT, mode TEXT, day_chg REAL, order_id TEXT)""")
    # scan-visibility: one SUMMARY row (the funnel) + one CANDIDATE row per qualifier, per cycle
    c.execute("""CREATE TABLE IF NOT EXISTS wave_scan_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT, scan_ts TEXT, kind TEXT,
        symbol TEXT, day_chg REAL, ext_vwap REAL, verdict TEXT, detail TEXT)""")
    c.commit(); c.close()


def record_scan(funnel: dict, candidates: list):
    """Persist the scan funnel + each qualifying candidate's verdict, for dashboard visibility."""
    import json
    ts = (now_et() if ET else dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    c = sqlite3.connect(DB)
    c.execute("INSERT INTO wave_scan_log(scan_ts,kind,symbol,verdict,detail) VALUES(?,?,?,?,?)",
              (ts, "SUMMARY", None, "SCAN", json.dumps(funnel)))
    for sym, sig, verdict in candidates:
        c.execute("INSERT INTO wave_scan_log(scan_ts,kind,symbol,day_chg,ext_vwap,verdict,detail) VALUES(?,?,?,?,?,?,?)",
                  (ts, "CANDIDATE", sym, round(sig["day_chg"], 2), round(sig["ext_vwap"], 2), verdict, None))
    # keep the log bounded (last ~3000 rows)
    c.execute("DELETE FROM wave_scan_log WHERE id < (SELECT MAX(id)-3000 FROM wave_scan_log)")
    c.commit(); c.close()


def get_open() -> list[dict]:
    """Only rows belonging to the CURRENT mode. Without this, flipping SHADOW->LIVE makes
    monitor() place real SELL orders against positions that were never actually bought — i.e.
    open naked shorts. Same guard added to overnight.py/contrarian.py on Sep 5 2026."""
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM wave_trades WHERE status='OPEN' AND mode=?", (MODE,))]
    c.close(); return rows


def open_symbols() -> set:
    return {r["symbol"] for r in get_open()}


def record_entry(sym, price, shares, stop, exit_on, day_chg, order_id=None):
    now = dt.datetime.now(ET) if ET else dt.datetime.now()
    c = sqlite3.connect(DB)
    c.execute("""INSERT INTO wave_trades(symbol,entry_date,entry_time,entry_price,shares,
        stop_price,exit_on_date,status,mode,day_chg,order_id) VALUES(?,?,?,?,?,?,?,'OPEN',?,?,?)""",
        (sym, now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), price, shares,
         stop, exit_on, MODE, day_chg, order_id))
    c.commit(); c.close()


def record_exit(tid, price, reason):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    t = dict(c.execute("SELECT * FROM wave_trades WHERE id=?", (tid,)).fetchone())
    # net of the real IBKR round trip ($0.005/share, $1.00 order minimum) -- was gross
    # until Sep 3 2026, which flattered every shadow result by ~15-20bps a trade.
    from database import equity_commission
    pnl = (price - t["entry_price"]) * t["shares"] - equity_commission(t["shares"])
    pnl_pct = (price - t["entry_price"]) / t["entry_price"] * 100
    now = dt.datetime.now(ET) if ET else dt.datetime.now()
    c.execute("""UPDATE wave_trades SET status='CLOSED',exit_date=?,exit_time=?,exit_price=?,
        pnl=?,pnl_pct=?,exit_reason=? WHERE id=?""",
        (now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), price, pnl, pnl_pct, reason, tid))
    c.commit(); c.close()
    log(f"EXIT #{tid} {t['symbol']} @ ${price:.2f} — {reason} | PnL ${pnl:+.2f} ({pnl_pct:+.1f}%)")
    return pnl


# ─────────────────────────── market helpers ───────────────────────────
def now_et() -> dt.datetime:
    return dt.datetime.now(ET) if ET else dt.datetime.now()


def is_market_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in US_HOLIDAYS


def wild_universe() -> list[str]:
    pe = pd.read_csv(os.path.join(CACHE, "personality.csv"))
    return pe[pe["cluster"] == "WILD"]["symbol"].tolist()


def add_business_days(d: dt.date, n: int) -> dt.date:
    cur = d; added = 0
    while added < n:
        cur += dt.timedelta(days=1)
        if is_market_day(cur):
            added += 1
    return cur


def bridge_quote(sym: str):
    try:
        r = requests.get(f"{BRIDGE}/quote/{sym}", timeout=5)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def days_to_earnings(sym: str):
    try:
        import yfinance as yf
        cal = yf.Ticker(sym).calendar
        if isinstance(cal, dict):
            for k in ("Earnings Date", "earningsDate"):
                v = cal.get(k)
                if v:
                    ed = pd.Timestamp(v[0] if isinstance(v, list) else v).date()
                    return (ed - dt.date.today()).days
    except Exception:
        pass
    return None


# ─────────────────────────── signal (live via bridge) ───────────────────────────
def live_signal(sym: str):
    """Returns dict(price, day_chg, ext_vwap) from live bridge bars, or None."""
    try:
        r = requests.get(f"{BRIDGE}/history/{sym}", params={"duration": "2 D", "bar_size": "5 mins", "rth": "true"}, timeout=8)
        if r.status_code != 200:
            return None
        bars = r.json()
        if not isinstance(bars, list) or len(bars) < 10:
            return None
        df = pd.DataFrame(bars)
        df["date"] = pd.to_datetime(df["date"] if "date" in df else df["time"])
        df["d"] = df["date"].dt.date
        days = sorted(df["d"].unique())
        today = days[-1]
        prior = df[df["d"] == days[-2]]
        tdf = df[df["d"] == today]
        if len(prior) == 0 or len(tdf) == 0:
            return None
        prior_close = float(prior["close"].iloc[-1])
        price = float(tdf["close"].iloc[-1])
        orb = tdf.head(6)  # first 30 min
        tp = (orb["high"] + orb["low"] + orb["close"]) / 3
        vwap = float((tp * orb["volume"]).sum() / max(orb["volume"].sum(), 1))
        return {"price": price, "day_chg": (price - prior_close) / prior_close * 100,
                "ext_vwap": (price - vwap) / vwap * 100}
    except Exception as e:
        log(f"signal error {sym}: {e}")
        return None


def qualifies(sig) -> bool:
    return (sig is not None and sig["day_chg"] >= MOVE_MIN and sig["ext_vwap"] >= 0
            and PRICE_LO <= sig["price"] <= PRICE_HI)


# ─────────────────────────── order path (LIVE only) ───────────────────────────
def place_paper_order(sym, shares, side) -> tuple[bool, float, str | None]:
    """Returns (filled, fill_price, order_id). Confirmation lives in _fills.place_verified,
    which decides on the POSITION DELTA. The Sep-9 version of this function checked
    `abs(qty) >= shares` against the portfolio, which is right for a BUY but exactly backwards
    for the SELL exits below — it would read "the position is still here" as "the sell filled"
    and book avgCost (an entry basis) as the exit price. Fixed Sep 10 before it ever fired
    here; it had already driven Clockwork's positions to -396 shares."""
    return place_verified(BRIDGE, sym, shares, side, log=log)


# ─────────────────────────── scan + enter ───────────────────────────
def scan_and_enter():
    held = open_symbols()
    free = SLOTS - len(held)
    funnel = {"scanned": 0, "held_already": 0, "no_signal": 0, "below_move": 0,
              "below_vwap": 0, "earnings": 0, "qualified": 0, "entered": 0,
              "slots_free": free, "mode": MODE}
    if free <= 0:
        record_scan(funnel, [])
        log("slots full — no entries this cycle"); return
    candidates = []
    for sym in wild_universe():
        if sym in held:
            funnel["held_already"] += 1; continue
        funnel["scanned"] += 1
        sig = live_signal(sym)
        if sig is None:
            funnel["no_signal"] += 1; continue
        if sig["day_chg"] < MOVE_MIN or not (PRICE_LO <= sig["price"] <= PRICE_HI):
            funnel["below_move"] += 1; continue
        if sig["ext_vwap"] < 0:                       # not holding above opening VWAP
            funnel["below_vwap"] += 1; continue
        dte = days_to_earnings(sym)
        if dte is not None and 0 <= dte <= EARNINGS_BLOCK_DAYS:
            funnel["earnings"] += 1; continue
        funnel["qualified"] += 1
        candidates.append((sym, sig))
    candidates.sort(key=lambda x: -x[1]["day_chg"])   # biggest mover first (entry-time, causal)
    logged = []
    for i, (sym, sig) in enumerate(candidates):
        if i >= free:
            logged.append((sym, sig, "QUALIFIED_NO_SLOT")); continue
        price = sig["price"]
        shares = max(1, int(PER_SLOT / price))
        stop = round(price * (1 - STOP_PCT / 100), 2)
        exit_on = add_business_days(now_et().date(), HOLD_DAYS).isoformat()
        if MODE == "LIVE":
            ok, fill, oid = place_paper_order(sym, shares, "BUY")
            if not ok:
                logged.append((sym, sig, "ORDER_FAILED")); continue
            price = fill or price
            record_entry(sym, price, shares, round(price * (1 - STOP_PCT / 100), 2), exit_on, sig["day_chg"], oid)
        else:  # SHADOW — no order, just record + mark to live prices
            record_entry(sym, price, shares, stop, exit_on, sig["day_chg"])
        funnel["entered"] += 1
        logged.append((sym, sig, "ENTERED"))
        log(f"ENTER {sym} x{shares} @ ${price:.2f} (+{sig['day_chg']:.1f}%) stop ${stop} exit_on {exit_on}")
    record_scan(funnel, logged)
    log(f"scan funnel: {funnel['scanned']} scanned → {funnel['qualified']} qualified → {funnel['entered']} entered")


# ─────────────────────────── monitor + exit ───────────────────────────
def monitor():
    today = now_et().date().isoformat()
    tnow = now_et().time()
    for t in get_open():
        q = bridge_quote(t["symbol"])
        price = None
        if q:
            price = q.get("last") or q.get("price") or q.get("close")
        if price is None:
            sig = live_signal(t["symbol"])
            price = sig["price"] if sig else None
        if price is None:
            continue
        price = float(price)
        reason = None
        if price <= t["stop_price"]:
            reason = f"Stop ${t['stop_price']} hit"
        elif today >= t["exit_on_date"] and tnow >= EOD_EXIT:
            reason = f"Time exit ({HOLD_DAYS}d hold)"
        if not reason:
            continue
        if MODE == "LIVE":
            ok, fill, _ = place_paper_order(t["symbol"], t["shares"], "SELL")
            if not ok:   # never mark CLOSED unless the SELL actually confirmed a fill (USAR lesson)
                log(f"exit for {t['symbol']} NOT confirmed filled — leaving OPEN, retry next fire")
                continue
            if fill:
                price = fill
        record_exit(t["id"], price, reason)


# ─────────────────────────── main ───────────────────────────
def _manual_close(t):
    """Close ONE position on an explicit dashboard instruction. Returns (ok, message).

    SHADOW marks out at the quote and places nothing. LIVE goes through the engine's own
    verified order path and — this is the load-bearing part — only books the exit if the
    SELL actually confirmed a fill. An unconfirmed sell leaves the row OPEN so the position
    is never recorded as closed on a price we did not get (the Jul 20 2026 USAR lesson, and
    the Sep 10-11 2026 oversell that followed from getting this exact check backwards).
    """
    sym = t["symbol"]
    px = bridge_quote(sym)
    # Wave Rider's bridge_quote returns the whole quote dict (monitor() unwraps it); the other two
    # engines return a bare price. Unwrap here so this block stays identical in all three files.
    # Oct 2 2026: without this, float(dict) raised and every dashboard close of a Wave Rider
    # position failed before the SELL (AXTI #55, COHR #59) — nothing was sent.
    if isinstance(px, dict):
        px = px.get("last") or px.get("price") or px.get("close")
    if px is None:
        # Engines that compute their own signal (Wave Rider) can price from it when the
        # quote endpoint is down; the others have no such fallback. Looked up rather than
        # called by name so this block is identical in all three files without referring
        # to a function that only exists in one of them.
        _sig_fn = globals().get("live_signal")
        if _sig_fn:
            sig = _sig_fn(sym)
            px = sig["price"] if sig else None
    if px is None:
        return False, sym + ": no price available — nothing closed"
    px = float(px)
    if MODE == "LIVE":
        ok, fill, _ = place_paper_order(sym, t["shares"], "SELL")
        if not ok:
            return False, (sym + ": SELL not confirmed filled — position left OPEN "
                           "rather than booked at a price we may not have got")
        if fill:
            px = float(fill)
    record_exit(t["id"], px, "Manual close via dashboard")
    return True, f"{sym} x{t['shares']} @ ${px:.2f}"


def _drain_control_queue():
    from factory.live import _control
    _control.drain("wave_rider", get_open, _manual_close, log, MODE)


LOCK_TTL = 600   # seconds; matches run_once()'s own stale-lock window


def drain_only():
    """Serve dashboard close requests and NOTHING else.

    Separate entry point on purpose. run_once() also scans and can ENTER a position, so
    spawning it to serve a close request could open one — pressing "close" must never open
    anything. This takes the engine's own lock first, because a concurrent scheduled pass
    could otherwise read the same OPEN row into monitor() and submit a second exit for a
    position we are already closing.
    """
    init_db()
    waited = 0.0
    while os.path.exists(LOCK) and (time.time() - os.path.getmtime(LOCK)) < LOCK_TTL:
        if waited >= 20:
            log("a scheduled pass is holding the lock — close request left pending, "
                "it will be served by that pass or the next one")
            return
        time.sleep(1.0); waited += 1.0
    open(LOCK, "w").close()
    try:
        _drain_control_queue()
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


def run_once():
    init_db()
    # Answered before the market-hours guard below — see factory/live/_control.py.
    _drain_control_queue()
    n = now_et()
    if not is_market_day(n.date()):
        log("market closed (weekend/holiday) — no action"); return
    if n.time() < dt.time(9, 30) or n.time() > dt.time(16, 0):
        log("outside market hours — no action"); return
    # prevent overlapping runs (a full 100-symbol scan can exceed the 5-min interval, and
    # two concurrent passes could both see free slots and double-enter the same picks)
    if os.path.exists(LOCK) and (time.time() - os.path.getmtime(LOCK)) < LOCK_TTL:
        log("previous pass still active — skipping this fire"); return
    open(LOCK, "w").close()
    try:
        monitor()                          # always check exits first
        if ENTRY_START <= n.time() <= ENTRY_END:
            scan_and_enter()
        log(f"pass complete — {len(get_open())} open position(s)")
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


def dryscan(date_str: str):
    """Prove the pick logic against a past day using market_data.db (no DB writes, no orders)."""
    sys.path.insert(0, "/Users/sushil/trading")
    from collect_bars import load_bars
    wilds = wild_universe()
    log(f"DRY SCAN {date_str} over {len(wilds)} WILD stocks (no writes)")
    target = pd.Timestamp(date_str).date()
    start = (pd.Timestamp(date_str) - pd.Timedelta(days=6)).strftime("%Y-%m-%d")
    picks = []
    for sym in wilds:
        try:
            df = load_bars(sym, start=start, end=date_str)   # RANGE load (single-day returns empty)
        except Exception:
            continue
        if df is None or len(df) < 8:
            continue
        pc = df.between_time("09:30", "15:59")
        days = sorted(set(pc.index.date))
        if target not in days:
            continue
        idx = days.index(target)
        if idx == 0:
            continue
        prior_close = float(pc[pc.index.date == days[idx - 1]]["close"].iloc[-1])
        d = pc[pc.index.date == target].between_time("09:30", "10:00")
        if len(d) < 6:
            continue
        price = float(d["close"].iloc[-1])
        orb = d.head(6); tp = (orb["high"] + orb["low"] + orb["close"]) / 3
        vwap = float((tp * orb["volume"]).sum() / max(orb["volume"].sum(), 1))
        sig = {"price": price, "day_chg": (price - prior_close) / prior_close * 100,
               "ext_vwap": (price - vwap) / vwap * 100}
        if qualifies(sig):
            picks.append((sym, sig))
    picks.sort(key=lambda x: -x[1]["day_chg"])
    print(f"\n{len(picks)} qualifying WILD movers on {date_str} (would take top {SLOTS}):")
    for i, (sym, s) in enumerate(picks[:15]):
        flag = "  <-- WOULD ENTER" if i < SLOTS else ""
        print(f"  {sym:6} +{s['day_chg']:5.1f}%  ext_vwap {s['ext_vwap']:+.1f}%  ${s['price']:.2f}{flag}")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--dryscan":
        dryscan(sys.argv[2])
    elif len(sys.argv) > 1 and sys.argv[1] == "--drain-only":
        drain_only()
    else:
        run_once()
