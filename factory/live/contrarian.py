"""
Contrarian — LIVE PAPER trader for the cross-sectional reversal engine.

Buys the universe's biggest 3-day LOSERS and holds 5 business days, on the premise that
extreme short-term moves overshoot and revert. Bench-validated as `xsec_reversal` in
factory/xsec.py, anti-correlated to Wave Rider (2026 daily corr -0.30) — it is on the book to
make the equity side less of a one-way momentum bet, not to be the biggest earner.

⚠️ READ THIS BEFORE TRUSTING ANY BACKTEST NUMBER FOR THIS ENGINE (Sep 5 2026).
The validated form holds ~100 concurrent positions (10 long + 10 short x 5-day hold). At our
$10k that is $100 a position, where IBKR's $1 MINIMUM commission is 1% per side — the engine
is net NEGATIVE at $10k, $25k and $50k. The factory gate missed this because COST_DRAG is a
percentage-only model that never checks position size: a breadth strategy sails through a
percentage cost gate and dies on a fixed-cost floor.

So this runs a CONCENTRATED form the Proving Ground never scored: 2 slots at ~$5k, recycled as
they close. Two known biases inflate its historical record and BOTH bite hardest exactly here,
because concentration selects for the most extreme data points in the sample:
  1. daily_close.csv is built from RAW bars with no split adjustment, so a stock split reads as
     a ~-90% three-day "crash". CMG (50:1, Jun 2024), AVGO (10:1, Jul 2024), NFLX, BKNG, KLAC
     were all top picks. 12.6% of #1 picks were split artifacts. SPLIT_MAX_DROP guards this.
  2. The 241-name universe was screened in JULY 2026 partly on past performance, so "buy the
     biggest crasher and wait for the bounce" is precisely the axis survivorship bias inflates.
     Names that crashed and never came back are not in the universe at all.
Neither bias can touch a FORWARD test, which is the whole point of running it live on paper.
Treat this as a from-scratch forward experiment with no credible prior, not a validated engine.

LONG-ONLY on purpose: the short leg carried only +0.116% of the engine's +0.355% alpha while
adding all of the borrow and squeeze risk. Dropping it also drops market-neutrality, so this
book DOES carry beta — judge it on alpha vs the Tide, not on raw P&L.

MODE='SHADOW' (default) records intended trades and marks them to live prices, places NO
orders. MODE='LIVE' places real orders on the IBKR *paper* account.
  Dry-scan today's picks (no writes):  venv/bin/python -m factory.live.contrarian --dryscan
"""
from __future__ import annotations
import os, sys, sqlite3, time, json, datetime as dt
import requests
import numpy as np
import pandas as pd
sys.path.insert(0, "/Users/sushil/trading")
from collect_bars import load_bars  # noqa: E402
from factory.live._fills import place_verified  # noqa: E402

LOCK = "/tmp/contrarian.lock"
MODE = os.environ.get("CONTRARIAN_MODE", "SHADOW").upper()
BRIDGE = "http://localhost:8000"
DB = "/Users/sushil/trading/trades.db"
CACHE = "/Users/sushil/trading/factory/cache"
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

CAPITAL, SLOTS = 10_000.0, 2
PER_SLOT = CAPITAL / SLOTS          # ~$5,000 — large enough that the $1 commission floor is 0.02%
LOOKBACK = 3                        # trailing days used to rank the move
HOLD_DAYS = 5                       # business days
STOP_PCT = 15.0                     # wide: reversion needs room, and these names just fell hard
SPLIT_MAX_DROP = -35.0              # a 3-day move worse than this is treated as a split artifact
MIN_DROP = -8.0                     # must actually have fallen to be a reversion candidate
PRICE_LO, PRICE_HI = 5.0, 800.0
EARNINGS_BLOCK_DAYS = 4
ENTRY_START, ENTRY_END = dt.time(10, 0), dt.time(15, 0)
EOD_EXIT = dt.time(15, 45)
US_HOLIDAYS = {"2026-09-07", "2026-11-26", "2026-12-25"}


def now_et() -> dt.datetime:
    return dt.datetime.now(ET) if ET else dt.datetime.now()


def log(msg: str):
    print(f"[{now_et().strftime('%Y-%m-%d %H:%M:%S')}] [CONTRARIAN {MODE}] {msg}", flush=True)


def is_market_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in US_HOLIDAYS


def add_business_days(d: dt.date, n: int) -> dt.date:
    cur, added = d, 0
    while added < n:
        cur += dt.timedelta(days=1)
        if is_market_day(cur):
            added += 1
    return cur


# ─────────────────────────── DB ───────────────────────────
def init_db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS contrarian_trades(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT, entry_date TEXT, entry_time TEXT, entry_price REAL,
        shares INTEGER, stop_price REAL, exit_on_date TEXT,
        status TEXT DEFAULT 'OPEN', exit_date TEXT, exit_time TEXT, exit_price REAL,
        pnl REAL, pnl_pct REAL, exit_reason TEXT, mode TEXT, trail3d REAL, order_id TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS contrarian_scan_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT, scan_ts TEXT, kind TEXT,
        symbol TEXT, trail3d REAL, verdict TEXT, detail TEXT)""")
    c.commit(); c.close()


def get_open() -> list[dict]:
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM contrarian_trades WHERE status='OPEN' AND mode=?", (MODE,))]
    c.close(); return rows


def record_scan(funnel: dict, picks: list):
    ts = now_et().strftime("%Y-%m-%d %H:%M:%S")
    c = sqlite3.connect(DB)
    c.execute("INSERT INTO contrarian_scan_log(scan_ts,kind,verdict,detail) VALUES(?,?,?,?)",
              (ts, "SUMMARY", "SCAN", json.dumps(funnel)))
    for sym, tr, verdict in picks:
        c.execute("INSERT INTO contrarian_scan_log(scan_ts,kind,symbol,trail3d,verdict) VALUES(?,?,?,?,?)",
                  (ts, "CANDIDATE", sym, round(tr, 2), verdict))
    c.execute("DELETE FROM contrarian_scan_log WHERE id < (SELECT MAX(id)-3000 FROM contrarian_scan_log)")
    c.commit(); c.close()


def record_entry(sym, price, shares, stop, exit_on, trail3d, order_id=None):
    now = now_et()
    c = sqlite3.connect(DB)
    c.execute("""INSERT INTO contrarian_trades(symbol,entry_date,entry_time,entry_price,shares,
        stop_price,exit_on_date,status,mode,trail3d,order_id) VALUES(?,?,?,?,?,?,?,'OPEN',?,?,?)""",
        (sym, now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), price, shares,
         stop, exit_on, MODE, round(trail3d, 2), order_id))
    c.commit(); c.close()


def record_exit(tid, price, reason):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    t = dict(c.execute("SELECT * FROM contrarian_trades WHERE id=?", (tid,)).fetchone())
    from database import equity_commission
    pnl = (price - t["entry_price"]) * t["shares"] - equity_commission(t["shares"])
    pct = (price - t["entry_price"]) / t["entry_price"] * 100
    now = now_et()
    c.execute("""UPDATE contrarian_trades SET status='CLOSED',exit_date=?,exit_time=?,exit_price=?,
        pnl=?,pnl_pct=?,exit_reason=? WHERE id=?""",
        (now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), price, pnl, pct, reason, tid))
    c.commit(); c.close()
    log(f"EXIT #{tid} {t['symbol']} @ ${price:.2f} — {reason} | PnL ${pnl:+.2f} ({pct:+.1f}%)")


# ─────────────────────────── signal ───────────────────────────
def universe() -> list[str]:
    pe = pd.read_csv(os.path.join(CACHE, "personality.csv"))
    return pe["symbol"].tolist()


def trailing_moves() -> dict:
    """Trailing LOOKBACK-day % move per symbol, from our own 5-min bars (same source the
    backtest used, so live and sim rank on the identical quantity). Computed fresh each run."""
    out = {}
    start = (now_et().date() - dt.timedelta(days=20)).isoformat()
    for sym in universe():
        try:
            df = load_bars(sym, start=start)
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue
        d = df.between_time("09:30", "15:59")
        if len(d) == 0:
            continue
        closes = d.groupby(d.index.date)["close"].last()
        if len(closes) < LOOKBACK + 1:
            continue
        now_px, then_px = float(closes.iloc[-1]), float(closes.iloc[-1 - LOOKBACK])
        if then_px <= 0:
            continue
        out[sym] = ((now_px / then_px) - 1.0) * 100.0
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


def place_paper_order(sym, shares, side):
    """Confirmation lives in _fills.place_verified, which decides on the POSITION DELTA —
    correct for the SELL exits below as well as for BUY entries. See _fills.py for why the
    order-status field is never trusted on its own."""
    return place_verified(BRIDGE, sym, shares, side, log=log)


# ─────────────────────────── scan + enter ───────────────────────────
def scan_and_enter():
    held = {t["symbol"] for t in get_open()}
    free = SLOTS - len(held)
    funnel = {"scanned": 0, "held": len(held), "split_artifact": 0, "not_fallen": 0,
              "no_price": 0, "earnings": 0, "qualified": 0, "entered": 0,
              "slots_free": free, "mode": MODE}
    if free <= 0:
        record_scan(funnel, []); log("slots full — no entries"); return
    moves = trailing_moves()
    funnel["scanned"] = len(moves)
    cands = []
    for sym, tr in sorted(moves.items(), key=lambda x: x[1]):     # biggest loser first
        if sym in held:
            continue
        if tr < SPLIT_MAX_DROP:
            funnel["split_artifact"] += 1
            cands.append((sym, tr, "SPLIT_ARTIFACT")); continue
        if tr > MIN_DROP:
            funnel["not_fallen"] += 1; break        # sorted ascending — nothing below qualifies
        cands.append((sym, tr, "QUALIFIED"))
    picks_log = [c for c in cands if c[2] == "SPLIT_ARTIFACT"]
    entered = 0
    for sym, tr, _ in [c for c in cands if c[2] == "QUALIFIED"]:
        if entered >= free:
            picks_log.append((sym, tr, "QUALIFIED_NO_SLOT")); continue
        price = bridge_quote(sym)
        if price is None or not (PRICE_LO <= float(price) <= PRICE_HI):
            funnel["no_price"] += 1
            picks_log.append((sym, tr, "NO_PRICE")); continue
        price = float(price)
        dte = days_to_earnings(sym)
        if dte is not None and 0 <= dte <= EARNINGS_BLOCK_DAYS:
            funnel["earnings"] += 1
            picks_log.append((sym, tr, "EARNINGS")); continue
        funnel["qualified"] += 1
        shares = max(1, int(PER_SLOT / price))
        stop = round(price * (1 - STOP_PCT / 100), 2)
        exit_on = add_business_days(now_et().date(), HOLD_DAYS).isoformat()
        if MODE == "LIVE":
            ok, fill, oid = place_paper_order(sym, shares, "BUY")
            if not ok:
                picks_log.append((sym, tr, "ORDER_FAILED")); continue
            price = fill or price
            stop = round(price * (1 - STOP_PCT / 100), 2)
            record_entry(sym, price, shares, stop, exit_on, tr, oid)
        else:
            record_entry(sym, price, shares, stop, exit_on, tr)
        entered += 1
        picks_log.append((sym, tr, "ENTERED"))
        log(f"ENTER {sym} x{shares} @ ${price:.2f} (3d {tr:+.1f}%) stop ${stop} exit_on {exit_on}")
    funnel["entered"] = entered
    record_scan(funnel, picks_log)
    log(f"scan: {funnel['scanned']} ranked → {funnel['qualified']} qualified → {entered} entered "
        f"({funnel['split_artifact']} split artifacts filtered)")


# ─────────────────────────── monitor + exit ───────────────────────────
def monitor():
    today = now_et().date().isoformat()
    tnow = now_et().time()
    for t in get_open():
        price = bridge_quote(t["symbol"])
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
            if not ok:   # never mark CLOSED without a confirmed fill (the USAR lesson)
                log(f"exit for {t['symbol']} NOT confirmed — leaving OPEN, retry next fire")
                continue
            if fill:
                price = fill
        record_exit(t["id"], price, reason)


def run_once():
    init_db()
    n = now_et()
    if not is_market_day(n.date()):
        log("market closed — no action"); return
    if n.time() < dt.time(9, 30) or n.time() > dt.time(16, 0):
        log("outside market hours — no action"); return
    if os.path.exists(LOCK) and (time.time() - os.path.getmtime(LOCK)) < 900:
        log("previous pass still active — skipping"); return
    open(LOCK, "w").close()
    try:
        monitor()
        if ENTRY_START <= n.time() <= ENTRY_END:
            scan_and_enter()
        log(f"pass complete — {len(get_open())} open position(s)")
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


def dryscan():
    init_db()
    moves = trailing_moves()
    ranked = sorted(moves.items(), key=lambda x: x[1])
    print(f"\nContrarian dry-scan — {len(moves)} names ranked by trailing {LOOKBACK}d move")
    print(f"(artifact filter < {SPLIT_MAX_DROP}%, must have fallen < {MIN_DROP}%, {SLOTS} slots @ ${PER_SLOT:,.0f})")
    shown = 0
    for sym, tr in ranked:
        if tr > MIN_DROP or shown >= 14:
            break
        tag = "SPLIT ARTIFACT — skipped" if tr < SPLIT_MAX_DROP else ("<-- WOULD ENTER" if shown < SLOTS else "")
        print(f"  {sym:6} {tr:+7.2f}%   {tag}")
        shown += 1
    if shown == 0:
        print("  no candidate has fallen enough today")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--dryscan":
        dryscan()
    else:
        run_once()
