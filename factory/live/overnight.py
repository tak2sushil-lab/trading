"""
Clockwork — LIVE PAPER trader for Night Shift v2 (xsec_overnight_consist).

The overnight edge, the user's way: each day near the CLOSE, rank the WILD universe by how
CONSISTENTLY it has gapped up lately (fraction of the last 30 nights whose close→open was
positive), BUY the top ~decile, hold OVERNIGHT, and SELL at the next OPEN. Slow signal
(~20%/night turnover), long-only (carries overnight beta — a directional overnight book, not
market-neutral alpha). Backtest: net +0.17%/night in 2026 even at a 0.30% round-trip.

SAFETY / ON-RAMP (identical doctrine to wave_rider.py):
  MODE='SHADOW' (default) — records intended overnight trades + marks them to live prices,
    places NO orders. This is how we catch bugs at the Monday review with zero order risk.
  MODE='LIVE' — real orders on the IBKR *paper* account. Flip via CLOCKWORK_MODE=LIVE in the
    plist after a clean SHADOW day. One env var.

Fires every 5 min via launchd; self-gates to the entry window (near close) and exit window
(the open). Stateless between runs (state in overnight_trades). Signal is computed FRESH from
market_data.db each run (not the static factory cache), so it always sees yesterday's data.
  Dry-scan a day's picks (no writes):  python -m factory.live.overnight --dryscan
"""
from __future__ import annotations
import os, sys, sqlite3, time, json, datetime as dt
import requests
import numpy as np
import pandas as pd
sys.path.insert(0, "/Users/sushil/trading")
from collect_bars import load_bars  # noqa: E402
from factory.live._fills import (place_verified, position_qty, last_fill_price,  # noqa: E402
                                   fill_by_order_id)

# ── Earnings blackout ────────────────────────────────────────────────────────────
# Added Sep 20 2026. This book holds a name through exactly one overnight gap, which is
# precisely when an earnings release lands. Audit of 1,932 held name-nights: only 4 were
# worse than -10%, and the identifiable ones are all earnings reactions --
#     DELL -12.0% (reported 2024-11-26) · AMZN -9.4% and AAPL -9.4% (both reported
#     2024-08-01, after the close) · ABBV -10.5% · plus CEG -15.8% on the Jan-2025 AI selloff.
# Ten name-nights beyond +/-10% carry 15% of all P&L, in both directions. Clipping the tail
# at +/-5% costs 2.6bp of mean (20.56 -> 17.97) but lifts Sharpe 2.16 -> 2.26 and improves the
# worst night from -637bp to -500bp. On a 3-name book one -15.8% gap is -5.3% of the entire
# book in a single night, so the trade is worth making: this is variance reduction, NOT a
# P&L improvement, and it should not be expected to raise returns.
EARNINGS_BLACKOUT_DAYS = 1     # skip a name reporting tonight or tomorrow morning
_earn_cache: dict = {}


def days_to_earnings(sym):
    """Calendar days until the next earnings date, or None when unknown.
    Mirrors auto_trader.get_days_to_earnings. None means UNKNOWN -- the caller decides, and
    here we allow the trade rather than skip the whole book on a data outage."""
    key = (sym, dt.date.today().isoformat())
    if key in _earn_cache:
        return _earn_cache[key]
    days = None
    try:
        import yfinance as yf
        cal = yf.Ticker(sym).calendar
        vals = []
        if isinstance(cal, dict):
            for k in ("Earnings Date", "earningsDate"):
                v = cal.get(k)
                if v:
                    vals = v if isinstance(v, list) else [v]
                    break
        elif cal is not None and hasattr(cal, "columns") and len(cal.columns):
            vals = list(cal.columns)
        for v in vals:
            try:
                d = (pd.Timestamp(v).date() - dt.date.today()).days
                if d >= -1:
                    days = d
                    break
            except Exception:
                continue
    except Exception:
        days = None
    _earn_cache[key] = days
    return days


LOCK = "/tmp/clockwork.lock"
MODE = os.environ.get("CLOCKWORK_MODE", "SHADOW").upper()   # SHADOW | LIVE
BRIDGE = "http://localhost:8000"
DB = "/Users/sushil/trading/trades.db"
CACHE = "/Users/sushil/trading/factory/cache"
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

# Sep 20 2026 — resized on the overnight panel study (research_strategy_hunt.py --config).
# This book has its OWN $10,000; it is not funded out of the intraday book's capital.
# Concentration was measured, not assumed, on 673 nights with a $20M ADV floor:
#     3 x $3,000  ->  +19.09bp/night  Sharpe 2.01  maxDD -13.7%   (2026: +20.98bp)
#     5 x $2,000  ->  +14.12bp/night  Sharpe 1.80  maxDD -18.9%
#    10 x $1,000  ->  +10.22bp/night  Sharpe 1.36  maxDD -20.4%   <- the old config
# Fewer, larger positions win here because the fee is charged PER TRADE and 96% of our
# orders hit the broker's per-order minimum: at $1,000 a name the round trip costs 7.0bp of
# a ~17bp edge, at $3,000 it costs 2.3bp. The gross edge is nearly flat from 3 to 50 names
# (21.4 -> 16.9bp), so concentration buys the fee saving almost for free — and the worst
# single night was actually BETTER at 3 names (-637bp) than at 10 (-784bp).
CAPITAL = 10_000.0         # this strategy's own budget, separate from the intraday book
TOP_N = 3                  # was 10 — see the table above
PER_NAME = CAPITAL / TOP_N # $3,333
LOOKBACK = 30              # up-night consistency window (validated plateau 20-40)
PRICE_LO, PRICE_HI = 5.0, 800.0
# Auction windows (Sep 5 2026). The backtest prices entries at the daily CLOSE and exits at
# the daily OPEN, so live uses MOC/MOO auction orders to fill at those exact prints instead of
# paying the spread with a MARKET order 13 min early. Auction orders must be SUBMITTED before
# the exchange cutoff (MOC ~15:50, MOO ~09:28) and only FILL later, so each side is two phases:
# submit, then confirm the fill once the auction has run.
ENTRY_START, ENTRY_END = dt.time(15, 40), dt.time(15, 49)   # submit MOC BUY
ENTRY_CONFIRM_START, ENTRY_CONFIRM_END = dt.time(16, 0), dt.time(16, 40)
EXIT_START, EXIT_END = dt.time(9, 0), dt.time(9, 27)        # submit MOO SELL
EXIT_CONFIRM_START, EXIT_CONFIRM_END = dt.time(9, 31), dt.time(9, 59)
EXIT_FALLBACK_AFTER = dt.time(9, 45)   # MOO never filled -> cross with a MARKET order
US_HOLIDAYS = {"2026-09-07", "2026-11-26", "2026-12-25"}


def now_et() -> dt.datetime:
    return dt.datetime.now(ET) if ET else dt.datetime.now()


def log(msg: str):
    print(f"[{now_et().strftime('%Y-%m-%d %H:%M:%S')}] [CLOCKWORK {MODE}] {msg}", flush=True)


def is_market_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in US_HOLIDAYS


# ─────────────────────────── DB ───────────────────────────
def init_db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS overnight_trades(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT, entry_date TEXT, entry_time TEXT, entry_price REAL, shares INTEGER,
        status TEXT DEFAULT 'OPEN', exit_date TEXT, exit_time TEXT, exit_price REAL,
        pnl REAL, pnl_pct REAL, consistency REAL, mode TEXT, order_id TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS overnight_scan_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT, scan_ts TEXT, kind TEXT,
        symbol TEXT, consistency REAL, verdict TEXT, detail TEXT)""")
    c.commit(); c.close()


def get_open() -> list[dict]:
    """Only rows belonging to the CURRENT mode. Without this, flipping SHADOW->LIVE would make
    exit_at_open() place real SELL orders against positions that were never actually bought —
    i.e. open naked shorts. Found before the first LIVE session, Sep 5 2026."""
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM overnight_trades WHERE status='OPEN' AND mode=?", (MODE,))]
    c.close(); return rows


def entered_today() -> bool:
    today = now_et().date().isoformat()
    c = sqlite3.connect(DB)
    n = c.execute("SELECT COUNT(*) FROM overnight_trades WHERE entry_date=?", (today,)).fetchone()[0]
    c.close(); return n > 0


def record_entry(sym, price, shares, consistency, order_id=None, status="OPEN"):
    now = now_et()
    c = sqlite3.connect(DB)
    c.execute("""INSERT INTO overnight_trades(symbol,entry_date,entry_time,entry_price,shares,
        status,consistency,mode,order_id) VALUES(?,?,?,?,?,?,?,?,?)""",
        (sym, now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), price, shares,
         status, round(consistency, 3), MODE, order_id))
    c.commit(); c.close()


def rows_with_status(st: str) -> list[dict]:
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM overnight_trades WHERE status=? AND mode=?", (st, MODE))]
    c.close(); return rows


def set_status(tid, st, **cols):
    sets = ",".join([f"{k}=?" for k in cols] + ["status=?"])
    c = sqlite3.connect(DB)
    c.execute(f"UPDATE overnight_trades SET {sets} WHERE id=?", (*cols.values(), st, tid))
    c.commit(); c.close()


def order_fill(oid):
    """(filled, avg_price) for THIS order id.

    Checks the execution records FIRST (ground truth, identifies our specific order) and only
    falls back to the status endpoint, which lags. Never infers anything from the net position
    — in a shared account that is the sum of all four books, and reading it as ours is what
    caused the Sep 11 2026 oversell."""
    filled, px, _sh = fill_by_order_id(BRIDGE, oid, log)
    if filled and px:
        return True, px
    try:
        d = requests.get(f"{BRIDGE}/order/{oid}/status", timeout=5).json()
        if d.get("status") == "Filled" and d.get("avgFillPrice"):
            return True, float(d["avgFillPrice"])
        if float(d.get("filled") or 0) > 0 and d.get("avgFillPrice"):
            return True, float(d["avgFillPrice"])
    except Exception as e:
        log(f"fill check failed for order {oid}: {e}")
    return False, 0.0


def real_position(sym):
    """Ground truth from IBKR's own portfolio: (qty, avg_cost), or (0, None) if genuinely
    flat, or (None, None) if the check itself failed — that last case must NEVER be treated
    as confirmed-flat, exactly the distinction order_fill's own docstring already asks for
    but that confirm_fills() didn't actually apply (Sep 2026 root cause: 10 real Clockwork
    MOC fills got deleted from tracking on a lagged order-status read with no portfolio
    cross-check, then sat as real, untracked IBKR positions for two days until
    auto_trader.py's reconcile rediscovered them as "orphans")."""
    try:
        r = requests.get(f"{BRIDGE}/portfolio", timeout=10).json()
        for p in r:
            if p.get("symbol") == sym:
                return int(p.get("qty", 0) or 0), (float(p["avgCost"]) if p.get("avgCost") else None)
        return 0, None
    except Exception as e:
        log(f"portfolio check failed for {sym}: {e}")
        return None, None


def record_exit(tid, price):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    t = dict(c.execute("SELECT * FROM overnight_trades WHERE id=?", (tid,)).fetchone())
    # net of the real IBKR round trip. At this book's ~$968 positions that is ~0.207%,
    # which is LARGER than the strategy's whole measured edge -- was gross until Sep 3 2026.
    from database import equity_commission
    pnl = (price - t["entry_price"]) * t["shares"] - equity_commission(t["shares"])
    pct = (price - t["entry_price"]) / t["entry_price"] * 100
    now = now_et()
    c.execute("""UPDATE overnight_trades SET status='CLOSED',exit_date=?,exit_time=?,exit_price=?,
        pnl=?,pnl_pct=? WHERE id=?""",
        (now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), price, pnl, pct, tid))
    c.commit(); c.close()
    log(f"EXIT #{tid} {t['symbol']} @ ${price:.2f} (open) | PnL ${pnl:+.2f} ({pct:+.1f}%)")


def record_scan(funnel: dict, picks: list):
    ts = now_et().strftime("%Y-%m-%d %H:%M:%S")
    c = sqlite3.connect(DB)
    c.execute("INSERT INTO overnight_scan_log(scan_ts,kind,verdict,detail) VALUES(?,?,?,?)",
              (ts, "SUMMARY", "SCAN", json.dumps(funnel)))
    for sym, cons, verdict in picks:
        c.execute("INSERT INTO overnight_scan_log(scan_ts,kind,symbol,consistency,verdict) VALUES(?,?,?,?,?)",
                  (ts, "CANDIDATE", sym, round(cons, 3), verdict))
    c.execute("DELETE FROM overnight_scan_log WHERE id < (SELECT MAX(id)-2000 FROM overnight_scan_log)")
    c.commit(); c.close()


# ─────────────────────────── signal (fresh from market_data.db) ───────────────────────────
def wild_universe() -> list[str]:
    pe = pd.read_csv(os.path.join(CACHE, "personality.csv"))
    return pe[pe["cluster"] == "WILD"]["symbol"].tolist()


def consistency_signal() -> dict[str, float]:
    """{symbol: fraction of last LOOKBACK nights that gapped up}, from fresh daily bars.
    Computed as-of the latest COMPLETED session (causal — no lookahead into today's close)."""
    start = (now_et().date() - dt.timedelta(days=LOOKBACK * 2 + 20)).isoformat()
    end = now_et().date().isoformat()
    out = {}
    for sym in wild_universe():
        try:
            df = load_bars(sym, start=start, end=end)
        except Exception:
            continue
        if df is None or len(df) < 40:
            continue
        d = df.between_time("09:30", "15:59").copy()
        d["date"] = d.index.date
        g = d.groupby("date")
        daily = pd.DataFrame({"open": g["open"].first(), "close": g["close"].last()}).sort_index()
        overnight = daily["open"] / daily["close"].shift(1) - 1.0
        recent = overnight.dropna().tail(LOOKBACK)
        if len(recent) >= LOOKBACK - 5:
            out[sym] = float((recent > 0).mean())
    return out


def bridge_quote(sym: str):
    try:
        r = requests.get(f"{BRIDGE}/quote/{sym}", timeout=5)
        if r.status_code == 200:
            q = r.json()
            return q.get("last") or q.get("price") or q.get("close")
    except Exception:
        pass
    return None


def place_paper_order(sym, shares, side, order_type="MARKET"):
    """Returns (accepted, fill_price, order_id). For MOC/MOO the auction has not run yet, so
    `accepted` means the broker took the order and fill_price is 0.0 — the fill is picked up
    later by confirm_fills(). Confirmation logic lives in _fills.place_verified: it decides on
    the POSITION DELTA, which is correct for sells as well as buys. The Sep-10 version of this
    function checked `abs(qty) >= shares`, which on a SELL reads "the position is still here"
    as "the sell filled" — exactly backwards, and it booked avgCost (an entry basis) as the
    exit price."""
    return place_verified(BRIDGE, sym, shares, side, order_type=order_type, log=log)


# ─────────────────────────── entry / exit ───────────────────────────
def scan_and_enter():
    if entered_today():
        log("already entered today — skipping"); return
    sig = consistency_signal()
    ranked = sorted(sig.items(), key=lambda x: -x[1])
    funnel = {"wild_scanned": len(sig), "top_n": TOP_N, "entered": 0, "mode": MODE}
    picks_log = []
    entered = 0
    # Never exceed TOP_N positions IN TOTAL. scan_and_enter used to check only
    # entered_today(), so if the morning MOO sells had failed (gateway down at 09:00, say)
    # the book would hold yesterday's names AND buy a full new set on top -- committing well
    # over its $10,000 budget with no cap anywhere in the path. Found in the Sep 20 audit.
    already = len(get_open())
    if already >= TOP_N:
        log(f"{already} position(s) still open (>= TOP_N {TOP_N}) — no new entries; "
            f"yesterday's exits have not cleared")
        record_scan({**funnel, "blocked": "positions_still_open"}, picks_log); return
    room = TOP_N - already
    if already:
        log(f"{already} position(s) still open — entering only {room} to stay within TOP_N")

    for sym, cons in ranked:
        if entered >= room:
            picks_log.append((sym, cons, "RANKED_BELOW_CUT")); break
        dte = days_to_earnings(sym)
        if dte is not None and 0 <= dte <= EARNINGS_BLACKOUT_DAYS:
            log(f"SKIP {sym} — earnings in {dte}d, not holding it through the print")
            picks_log.append((sym, cons, "EARNINGS_BLACKOUT")); continue
        price = bridge_quote(sym)
        if price is None or not (PRICE_LO <= float(price) <= PRICE_HI):
            picks_log.append((sym, cons, "NO_PRICE")); continue
        price = float(price)
        shares = max(1, int(PER_NAME / price))
        if MODE == "LIVE":
            ok, fill, oid = place_paper_order(sym, shares, "BUY", order_type="MOC")
            if not ok:
                picks_log.append((sym, cons, "ORDER_FAILED")); continue
            # MOC fills in the 16:00 auction — park it and pick up the real print later.
            record_entry(sym, price, shares, cons, oid, status="PENDING_ENTRY")
        else:
            record_entry(sym, price, shares, cons)
        entered += 1
        picks_log.append((sym, cons, "ENTERED"))
        log(f"ENTER {sym} x{shares} @ ${price:.2f}  consistency {cons:.0%} (holds overnight)")
    funnel["entered"] = entered
    record_scan(funnel, picks_log)
    log(f"overnight scan: {len(sig)} WILD ranked → {entered} entered @ close")


def exit_at_open():
    """SHADOW: mark out at the open price. LIVE: submit MOO before the 09:28 cutoff."""
    for t in get_open():
        if MODE == "LIVE":
            ok, _, oid = place_paper_order(t["symbol"], t["shares"], "SELL", order_type="MOO")
            if not ok:
                log(f"MOO submit FAILED {t['symbol']} — stays OPEN, retried next pass"); continue
            set_status(t["id"], "PENDING_EXIT", order_id=oid)
            log(f"MOO SELL submitted {t['symbol']} x{t['shares']} (fills at the open)")
        else:
            px = bridge_quote(t["symbol"])
            if px is None:
                log(f"no open price for {t['symbol']} — will retry next fire"); continue
            record_exit(t["id"], float(px))


def confirm_fills():
    """Promote auction orders once the auction has actually run. LIVE only.

    A position is NEVER marked OPEN or CLOSED on a price we did not get filled at — that is
    the Jul-20 USAR lesson. An entry that never filled is deleted (we own nothing); an exit
    that never filled is crossed with a MARKET order before the window shuts, because holding
    an unsold overnight position into the day is risk this engine never signed up for."""
    n = now_et()
    if MODE != "LIVE":
        return
    today = n.date().isoformat()

    # ── Stale sweep, runs on EVERY pass ───────────────────────────────────────────────
    # A PENDING_* row is invisible to get_open() (which wants status='OPEN'), so anything
    # left pending from a PRIOR day is a zombie: never exited, never counted, silently wrong.
    # The original same-day cleanup could only fire in the single instant t == 16:40:00, which
    # a 5-min cadence essentially never hits — so it never ran. Found in the Sep 6 bug sweep.
    for t in rows_with_status("PENDING_ENTRY"):
        if t["entry_date"] < today:
            filled, px = order_fill(t["order_id"])
            if filled:                       # it DID fill, we just never saw it — adopt it
                set_status(t["id"], "OPEN", entry_price=px)
                log(f"STALE ENTRY adopted {t['symbol']} @ ${px:.2f} (filled, confirm was missed)")
                continue
            # order_fill() already consulted the execution record for THIS order id, which is
            # the only uncontaminated answer. The previous version fell back to the net
            # portfolio position here — the mirror image of the exit-side oversell: if Wave
            # Rider happened to hold the same ticker, Clockwork would "adopt" ITS shares and
            # then sell them at the next open. Only delete the row when the account is
            # genuinely flat in that symbol AND no book claims it; otherwise hold and retry.
            qty, _avg = real_position(t["symbol"])
            if qty is None:
                log(f"STALE ENTRY {t['symbol']} — order status, execution record and portfolio "
                    f"all inconclusive; leaving row pending, will retry next pass")
            elif qty == 0:
                c = sqlite3.connect(DB)
                c.execute("DELETE FROM overnight_trades WHERE id=?", (t["id"],)); c.commit(); c.close()
                log(f"STALE ENTRY never filled {t['symbol']} — no fill on order {t['order_id']} "
                    f"and the account is flat in it, row removed")
            else:
                log(f"STALE ENTRY {t['symbol']} — order {t['order_id']} shows no fill, but the "
                    f"account holds {qty} (another book's). NOT adopting it; leaving pending")
    for t in rows_with_status("PENDING_EXIT"):
        if t["entry_date"] < today and not (EXIT_CONFIRM_START <= n.time() <= EXIT_CONFIRM_END):
            filled, px = order_fill(t["order_id"])
            if filled:
                record_exit(t["id"], px)
                log(f"STALE EXIT confirmed {t['symbol']} @ ${px:.2f}")
                continue
            # Sep 10-11 2026 — THE OVERSELL BUG, twice.
            # v1 fell straight through to set_status(OPEN), which hands the row back to
            # exit_at_open() and submits ANOTHER MOO SELL next pass. v2 tried to guard that
            # with the NET POSITION — but the net is the sum of all four books sharing this
            # account, so when Clockwork's sell filled and Wave Rider still held the same
            # ticker, the net stayed positive, v2 read "still held" and sold AGAIN. Ten
            # symbols went short and Wave Rider's positions were consumed in the process.
            # order_fill() now identifies OUR order by id via the execution records, so a
            # "did it fill" answer can no longer be contaminated by another book. If that
            # is still inconclusive we hold the row PENDING_EXIT and retry — we never
            # re-send a sell on an ambiguous read.
            log(f"STALE EXIT {t['symbol']} — order {t['order_id']} shows no fill in the "
                f"execution record yet; holding PENDING_EXIT for the next pass rather than "
                f"re-sending a sell (never infer from the shared net position)")

    if ENTRY_CONFIRM_START <= n.time() <= ENTRY_CONFIRM_END:
        for t in rows_with_status("PENDING_ENTRY"):
            filled, px = order_fill(t["order_id"])
            if filled:
                set_status(t["id"], "OPEN", entry_price=px)
                log(f"ENTRY FILLED {t['symbol']} x{t['shares']} @ ${px:.2f} (closing auction)")
    if EXIT_CONFIRM_START <= n.time() <= EXIT_CONFIRM_END:
        for t in rows_with_status("PENDING_EXIT"):
            filled, px = order_fill(t["order_id"])
            if filled:
                record_exit(t["id"], px)
            elif n.time() >= EXIT_FALLBACK_AFTER:
                ok, fill, _ = place_paper_order(t["symbol"], t["shares"], "SELL")
                if ok and fill:
                    log(f"MOO did not fill {t['symbol']} — crossed with MARKET @ ${fill:.2f}")
                    record_exit(t["id"], fill)
                else:
                    log(f"fallback SELL not confirmed for {t['symbol']} — stays PENDING_EXIT")


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
    if px is None and "live_signal" in globals():
        sig = live_signal(sym)
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
    record_exit(t["id"], px)
    return True, f"{sym} x{t['shares']} @ ${px:.2f}"


def _drain_control_queue():
    from factory.live import _control
    _control.drain("clockwork", get_open, _manual_close, log, MODE)


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
        log("market closed — no action"); return
    if os.path.exists(LOCK) and (time.time() - os.path.getmtime(LOCK)) < LOCK_TTL:
        log("previous pass still active — skipping"); return
    open(LOCK, "w").close()
    try:
        confirm_fills()                            # promote yesterday's auction orders first
        # LIVE submits the MOO before the 09:28 cutoff; SHADOW has no order to place, so it
        # marks out AFTER the open instead (marking at 09:00 would be a pre-market price).
        if (MODE == "LIVE" and EXIT_START <= n.time() <= EXIT_END) or \
           (MODE != "LIVE" and EXIT_CONFIRM_START <= n.time() <= EXIT_CONFIRM_END):
            exit_at_open()
        if ENTRY_START <= n.time() <= ENTRY_END:
            scan_and_enter()                       # submit MOC before the 15:50 cutoff
        log(f"pass complete — {len(get_open())} overnight position(s) held")
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


def dryscan():
    init_db()
    sig = consistency_signal()
    ranked = sorted(sig.items(), key=lambda x: -x[1])
    print(f"\nClockwork dry-scan — {len(sig)} WILD names ranked by {LOOKBACK}d up-night consistency")
    print(f"Top {TOP_N} (would BUY at close, sell at next open):")
    for i, (sym, cons) in enumerate(ranked[:TOP_N + 5]):
        flag = "  <-- WOULD ENTER" if i < TOP_N else ""
        print(f"  {sym:6} consistency {cons:.0%}{flag}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--dryscan":
        dryscan()
    elif len(sys.argv) > 1 and sys.argv[1] == "--drain-only":
        drain_only()
    else:
        run_once()
