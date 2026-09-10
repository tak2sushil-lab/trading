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
from factory.live._fills import place_verified, position_qty, last_fill_price  # noqa: E402

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

CAPITAL = 10_000.0
TOP_N = 10                 # top decile of the 100 WILD names, equal-weighted
PER_NAME = CAPITAL / TOP_N
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
    """(filled, avg_price). `/order/{id}/status` can lag (the Jul-20 USAR / Sep-3 futures
    lesson), so a 'not filled' answer here is never treated as proof — the caller retries on
    the next 5-min pass and falls back to a MARKET cross before the window closes."""
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
    for sym, cons in ranked:
        if entered >= TOP_N:
            picks_log.append((sym, cons, "RANKED_BELOW_CUT")); break
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
            # order_fill's own docstring: "not filled" is never proof by itself — cross-check
            # the real portfolio before deleting our only record of this position.
            qty, avg_cost = real_position(t["symbol"])
            if qty is None:
                log(f"STALE ENTRY {t['symbol']} — order status AND portfolio check both "
                    f"failed, leaving row pending, will retry next pass")
            elif qty > 0:
                px = avg_cost or t["entry_price"]
                set_status(t["id"], "OPEN", entry_price=px)
                log(f"STALE ENTRY adopted {t['symbol']} @ ${px:.2f} qty={qty} via portfolio "
                    f"check (order status lagged, real position confirms the fill)")
            else:
                c = sqlite3.connect(DB)
                c.execute("DELETE FROM overnight_trades WHERE id=?", (t["id"],)); c.commit(); c.close()
                log(f"STALE ENTRY never filled {t['symbol']} — confirmed zero in portfolio, "
                    f"row removed")
    for t in rows_with_status("PENDING_EXIT"):
        if t["entry_date"] < today and not (EXIT_CONFIRM_START <= n.time() <= EXIT_CONFIRM_END):
            filled, px = order_fill(t["order_id"])
            if filled:
                record_exit(t["id"], px)
                log(f"STALE EXIT confirmed {t['symbol']} @ ${px:.2f}")
                continue
            # Sep 10 2026 — THE OVERSELL BUG. This used to fall straight through to
            # set_status(OPEN), which hands the row back to exit_at_open(), which submits
            # ANOTHER MOO SELL next pass. When the first sell HAD actually filled (the status
            # endpoint just lagged), every pass sold again: CC reached -396 shares, CLF -492,
            # UUUU -408 before reconcile bought them back. "Not filled" is not proof — the
            # position itself is. Only return to OPEN if we verifiably still hold the shares.
            qty = position_qty(BRIDGE, t["symbol"], log)
            if qty is None:
                log(f"STALE EXIT {t['symbol']} — cannot verify position, leaving PENDING_EXIT "
                    f"(will retry; never re-sends a sell on an unverified read)")
            elif qty <= 0:
                px2 = last_fill_price(BRIDGE, t["symbol"], "SLD", log) or bridge_quote(t["symbol"])
                if px2:
                    record_exit(t["id"], float(px2))
                    log(f"STALE EXIT confirmed {t['symbol']} @ ${float(px2):.2f} — position is "
                        f"flat, the sell had filled (order status lagged)")
                else:
                    log(f"STALE EXIT {t['symbol']} flat in portfolio but no fill price yet — "
                        f"leaving PENDING_EXIT rather than booking a made-up price")
            else:                            # genuinely still holding it — back to the exit path
                log(f"STALE EXIT unfilled {t['symbol']} — {qty} shares still held, returned to OPEN")
                set_status(t["id"], "OPEN")

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
def run_once():
    init_db()
    n = now_et()
    if not is_market_day(n.date()):
        log("market closed — no action"); return
    if os.path.exists(LOCK) and (time.time() - os.path.getmtime(LOCK)) < 600:
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
    else:
        run_once()
