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
ENTRY_START, ENTRY_END = dt.time(15, 45), dt.time(15, 59)   # near the close
EXIT_START, EXIT_END = dt.time(9, 30), dt.time(9, 50)       # at the open
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
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute("SELECT * FROM overnight_trades WHERE status='OPEN'")]
    c.close(); return rows


def entered_today() -> bool:
    today = now_et().date().isoformat()
    c = sqlite3.connect(DB)
    n = c.execute("SELECT COUNT(*) FROM overnight_trades WHERE entry_date=?", (today,)).fetchone()[0]
    c.close(); return n > 0


def record_entry(sym, price, shares, consistency, order_id=None):
    now = now_et()
    c = sqlite3.connect(DB)
    c.execute("""INSERT INTO overnight_trades(symbol,entry_date,entry_time,entry_price,shares,
        status,consistency,mode,order_id) VALUES(?,?,?,?,?,'OPEN',?,?,?)""",
        (sym, now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), price, shares,
         round(consistency, 3), MODE, order_id))
    c.commit(); c.close()


def record_exit(tid, price):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    t = dict(c.execute("SELECT * FROM overnight_trades WHERE id=?", (tid,)).fetchone())
    pnl = (price - t["entry_price"]) * t["shares"]
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


def place_paper_order(sym, shares, side):
    try:
        r = requests.post(f"{BRIDGE}/order",
                          json={"symbol": sym, "qty": shares, "side": side, "order_type": "MARKET"}, timeout=10)
        if r.status_code != 200 or not r.text.strip():
            log(f"order rejected {sym}: {r.status_code}"); return False, 0.0, None
        oid = r.json().get("orderId")
        if not oid:
            return False, 0.0, None
        for _ in range(4):
            time.sleep(2)
            d = requests.get(f"{BRIDGE}/order/{oid}/status", timeout=5).json()
            if d.get("status") == "Filled":
                px = d.get("avgFillPrice")
                return True, float(px) if px else 0.0, str(oid)
        return False, 0.0, str(oid)
    except Exception as e:
        log(f"order error {sym}: {e}"); return False, 0.0, None


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
            ok, fill, oid = place_paper_order(sym, shares, "BUY")
            if not ok:
                picks_log.append((sym, cons, "ORDER_FAILED")); continue
            price = fill or price
            record_entry(sym, price, shares, cons, oid)
        else:
            record_entry(sym, price, shares, cons)
        entered += 1
        picks_log.append((sym, cons, "ENTERED"))
        log(f"ENTER {sym} x{shares} @ ${price:.2f}  consistency {cons:.0%} (holds overnight)")
    funnel["entered"] = entered
    record_scan(funnel, picks_log)
    log(f"overnight scan: {len(sig)} WILD ranked → {entered} entered @ close")


def exit_at_open():
    for t in get_open():
        px = bridge_quote(t["symbol"])
        if px is None:
            log(f"no open price for {t['symbol']} — will retry next fire"); continue
        px = float(px)
        if MODE == "LIVE":
            ok, fill, _ = place_paper_order(t["symbol"], t["shares"], "SELL")
            if not ok:   # never mark CLOSED unless the SELL actually confirmed a fill (USAR lesson)
                log(f"exit for {t['symbol']} NOT confirmed filled — leaving OPEN, retry next fire")
                continue
            if fill:
                px = fill
        record_exit(t["id"], px)


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
        if EXIT_START <= n.time() <= EXIT_END:
            exit_at_open()                         # sell overnight holds at the open
        if ENTRY_START <= n.time() <= ENTRY_END:
            scan_and_enter()                       # buy top-consistency WILD near the close
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
