"""
Fear Rebound — the DAY SHIFT of the overnight pool (built Oct 9 2026). LIVE on the IBKR paper account.

THE TRADE. The morning after a FEAR day, buy the 3 volatile names that fell hardest on that day at ~09:40 and sell
them at 15:30. A FEAR day = VIX closed up ≥5% AND our universe fell ≥0.5% on average (market_state.py). Forced
selling on a fear day overshoots; the next day the hardest-hit names tend to bounce. Calm sell-offs (market down,
VIX not jumping — Oct 7/8 2026) do not rebound, so they are not traded.

THE MONEY. No new capital. Clockwork's $10,000 pool is idle in the daytime — Clockwork sells at the 09:30 opening
auction and buys again at the 15:40-15:49 closing auction — so this book borrows it for the day: 3 names × $3,333,
exactly Clockwork's slots. Room = 3 − Clockwork positions still open or pending (a morning sell that has not cleared
still holds its money) − this book's own open positions. Clockwork in turn counts this book's open positions before
it buys at 15:40 (factory/live/overnight.py: fear_rebound_open()), and this book is flat by 15:38, so the two never
use the same dollars at once and never meet the same name in the closing auction.

THE PICK (docs/RESEARCH_REGISTRY.md §P12-§P14, §Q1-§Q2, §R1, §R6). market_state's `rebound_basket` lists the 10
WILD names that fell hardest on the fear day, hardest first; the book takes the first 3 that pass its checks.
Chosen over "biggest gap-down this morning" because the entry is 09:40, not the opening print:
    3 names, the day after a fear day         official open→close (2018-26)   bought 09:40, sold 15:30 (bars 2024-26)
    hardest-hit on the fear day                 +43bp  (t 2.0, 7/9 yrs)          +93bp  (t 2.2, up 56%)
    biggest gap-down that morning               +71bp  (t 3.2, 9/9 yrs)          +46bp  (t 1.1)
    both combined                               +67bp  (t 3.2)                   +59bp  (t 1.4)
  The gap-down names rebound mostly in the first minutes after the open — gone by 09:40 (the Day Owl lesson).
  ⚠️ The rebound is market-wide only since ~2025: on unselected stocks 2018-23 it was ≈0 (+6bp, t 0.5); all US
  stocks 2024-26 +55bp. ~40 fear days a year. 2026: 30 days, +0.80%/day for the WILD basket, up 60%.

EXITS. 15:30-15:38 market sell (the research exit). Disaster stop −10% on any pass (with it: +97bp vs +93bp).
Nothing else. A position is NEVER booked closed unless the SELL confirmed a fill (the Jul 20 2026 USAR lesson); a
failed exit is retried every pass until 15:58 and alerts once if anything is still open at 15:50.

DAY CYCLE (launchd every 60s, stateless — state lives in trades.db):
  09:40-10:00  enter once: needs the previous session's market_state row with fear=1 (missing row or unknown VIX ⇒
               no trade); if Clockwork's morning sells have not cleared by 10:00 it enters only the free room
  every pass   disaster-stop check
  15:30-15:58  sell everything still open
  MODE=SHADOW records + marks at quotes (no orders); MODE=LIVE places orders on the paper account.

CLI: venv/bin/python -m factory.live.fear_rebound                    one pass (launchd)
     venv/bin/python -m factory.live.fear_rebound --dryscan 2026-09-28 what it would buy the morning after that day
     venv/bin/python -m factory.live.fear_rebound --status
"""
from __future__ import annotations
import datetime as dt
import json
import os
import sqlite3
import sys
import time

import requests

ROOT = "/Users/sushil/trading"
sys.path.insert(0, ROOT)
from factory.live._fills import place_verified  # noqa: E402
from factory.live import overnight as CW  # noqa: E402  (the pool's owner: CAPITAL, TOP_N)

LOCK = "/tmp/fear_rebound.lock"
LOCK_TTL = 600
MODE = os.environ.get("FEAR_REBOUND_MODE", "SHADOW").upper()      # SHADOW | LIVE
BRIDGE = "http://localhost:8000"
DB = os.path.join(ROOT, "trades.db")
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:                                                  # pragma: no cover
    ET = None

POOL = "Clockwork"
CAPITAL = CW.CAPITAL               # $10,000 — Clockwork's own, borrowed for the day (never added to)
TOP_N = CW.TOP_N                   # 3 slots, same as Clockwork
PER_NAME = CAPITAL / TOP_N         # $3,333
STOP_PCT = 10.0                    # disaster stop only
PRICE_LO, PRICE_HI = 5.0, 800.0
ENTRY_START, ENTRY_END = dt.time(9, 40), dt.time(10, 0)
EXIT_START, EXIT_END = dt.time(15, 30), dt.time(15, 58)
ALERT_STILL_OPEN_AT = dt.time(15, 50)
US_HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
               "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"}


def now_et() -> dt.datetime:
    return dt.datetime.now(ET) if ET else dt.datetime.now()


def log(msg: str):
    print(f"[{now_et().strftime('%Y-%m-%d %H:%M:%S')}] [FEAR REBOUND {MODE}] {msg}", flush=True)


def is_market_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in US_HOLIDAYS


def previous_session(d: dt.date) -> dt.date:
    p = d - dt.timedelta(days=1)
    while not is_market_day(p):
        p -= dt.timedelta(days=1)
    return p


def send_telegram(msg: str):
    """Equity bot. Best-effort: a Telegram outage must never block a trade or a fill check."""
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
        token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
        if token and chat:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": msg}, timeout=10)
    except Exception as e:
        log(f"telegram send failed: {e}")


# ─────────────────────────── DB ───────────────────────────
def init_db():
    c = sqlite3.connect(DB, timeout=30)
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS fear_rebound_trades(
            id INTEGER PRIMARY KEY AUTOINCREMENT, signal_date TEXT, symbol TEXT,
            entry_date TEXT, entry_time TEXT, entry_price REAL, shares INTEGER, stop_price REAL,
            status TEXT DEFAULT 'OPEN', exit_date TEXT, exit_time TEXT, exit_price REAL,
            pnl REAL, pnl_pct REAL, exit_reason TEXT, mode TEXT, order_id TEXT, pick_rank INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS fear_rebound_scan_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT, scan_ts TEXT, kind TEXT, symbol TEXT,
            verdict TEXT, detail TEXT)""")
        c.commit()
    finally:
        c.close()


def _q(sql, params=(), rows_as_dict=False):
    c = sqlite3.connect(DB, timeout=30)
    try:
        if rows_as_dict:
            c.row_factory = sqlite3.Row
        return [dict(r) if rows_as_dict else r for r in c.execute(sql, params)]
    finally:
        c.close()


def _x(sql, params=()):
    c = sqlite3.connect(DB, timeout=30)
    try:
        c.execute(sql, params)
        c.commit()
    finally:
        c.close()


def get_open() -> list[dict]:
    """Only rows of the CURRENT mode — a SHADOW row must never produce a real SELL (Clockwork, Sep 5 2026)."""
    return _q("SELECT * FROM fear_rebound_trades WHERE status='OPEN' AND mode=?", (MODE,), True)


def entered_today() -> bool:
    return _q("SELECT COUNT(*) FROM fear_rebound_trades WHERE entry_date=? AND mode=?",
              (now_et().date().isoformat(), MODE))[0][0] > 0


def scanned_today(kind: str) -> bool:
    return _q("SELECT COUNT(*) FROM fear_rebound_scan_log WHERE kind=? AND scan_ts LIKE ?",
              (kind, now_et().date().isoformat() + "%"))[0][0] > 0


def record_scan(kind: str, funnel: dict, picks: list):
    ts = now_et().strftime("%Y-%m-%d %H:%M:%S")
    c = sqlite3.connect(DB, timeout=30)
    try:
        c.execute("INSERT INTO fear_rebound_scan_log(scan_ts,kind,verdict,detail) VALUES(?,?,?,?)",
                  (ts, kind, "SCAN", json.dumps(funnel)))
        for sym, verdict in picks:
            c.execute("INSERT INTO fear_rebound_scan_log(scan_ts,kind,symbol,verdict) VALUES(?,?,?,?)",
                      (ts, "CANDIDATE", sym, verdict))
        c.execute("DELETE FROM fear_rebound_scan_log WHERE id < (SELECT MAX(id)-3000 FROM fear_rebound_scan_log)")
        c.commit()
    finally:
        c.close()


def record_entry(signal_date, sym, price, shares, rank, order_id=None):
    n = now_et()
    _x("""INSERT INTO fear_rebound_trades(signal_date,symbol,entry_date,entry_time,entry_price,shares,stop_price,
          status,mode,order_id,pick_rank) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
       (signal_date, sym, n.strftime("%Y-%m-%d"), n.strftime("%H:%M:%S"), price, shares,
        round(price * (1 - STOP_PCT / 100), 2), "OPEN", MODE, order_id, rank))


def record_exit(tid, price, reason):
    t = _q("SELECT * FROM fear_rebound_trades WHERE id=?", (tid,), True)[0]
    from database import equity_commission
    pnl = (price - t["entry_price"]) * t["shares"] - equity_commission(t["shares"])
    pct = (price - t["entry_price"]) / t["entry_price"] * 100
    n = now_et()
    _x("""UPDATE fear_rebound_trades SET status='CLOSED',exit_date=?,exit_time=?,exit_price=?,pnl=?,pnl_pct=?,
          exit_reason=? WHERE id=?""",
       (n.strftime("%Y-%m-%d"), n.strftime("%H:%M:%S"), price, pnl, pct, reason, tid))
    log(f"EXIT #{tid} {t['symbol']} @ ${price:.2f} — {reason} | PnL ${pnl:+.2f} ({pct:+.1f}%)")
    return pnl


# ─────────────────────────── signal + pool ───────────────────────────
def fear_signal(today: dt.date) -> dict:
    """The previous session's market_state verdict. {'ok': bool, 'reason': str, 'date': str, 'basket': [...]}.
    Missing row, unknown VIX or no basket ⇒ ok=False (never trade on a guess)."""
    prev = previous_session(today).isoformat()
    try:
        r = _q("SELECT fear, vix, vix_chg, mkt_ret, rebound_basket FROM market_state WHERE date=?", (prev,), True)
    except sqlite3.Error as e:
        return {"ok": False, "reason": f"market_state unreadable ({e})", "date": prev, "basket": []}
    if not r:
        return {"ok": False, "reason": f"no market_state row for {prev} (the 18:15 snapshot did not run)",
                "date": prev, "basket": []}
    r = r[0]
    if r["vix_chg"] is None:
        return {"ok": False, "reason": f"VIX change unknown for {prev} — fear cannot be judged", "date": prev, "basket": []}
    if not r["fear"]:
        return {"ok": False, "reason": f"{prev} was not a fear day (VIX {r['vix_chg'] * 100:+.1f}%, universe "
                                       f"{(r['mkt_ret'] or 0) * 100:+.2f}%)", "date": prev, "basket": []}
    basket = json.loads(r["rebound_basket"]) if r["rebound_basket"] else []
    if not basket:
        return {"ok": False, "reason": f"{prev} was a fear day but no rebound basket was recorded", "date": prev, "basket": []}
    return {"ok": True, "reason": f"{prev} was a FEAR day (VIX {r['vix_chg'] * 100:+.1f}%, universe "
                                  f"{(r['mkt_ret'] or 0) * 100:+.2f}%)", "date": prev, "basket": basket}


def clockwork_busy() -> int:
    """Clockwork positions still open or in flight — their money is not free for the day shift."""
    try:
        return _q("SELECT COUNT(*) FROM overnight_trades WHERE mode='LIVE' "
                  "AND status IN ('OPEN','PENDING_ENTRY','PENDING_EXIT')")[0][0]
    except sqlite3.Error:
        return TOP_N                                   # unknown ⇒ assume the pool is busy (never over-commit)


def pool_room() -> int:
    return max(0, TOP_N - clockwork_busy() - len(get_open()))


def other_book_symbols() -> set:
    """Names any other equity book holds right now — not doubled up here."""
    syms = set()
    for sql in ("SELECT symbol FROM trades WHERE status='OPEN' AND setup_type!='RECONCILED'",
                "SELECT symbol FROM wave_trades WHERE mode='LIVE' AND status='OPEN'",
                "SELECT symbol FROM contrarian_trades WHERE mode='LIVE' AND status='OPEN'",
                "SELECT symbol FROM overnight_trades WHERE mode='LIVE' AND status IN ('OPEN','PENDING_ENTRY','PENDING_EXIT')",
                "SELECT symbol FROM night_owl_trades WHERE mode='LIVE' AND status IN ('OPEN','PENDING_ENTRY','PENDING_EXIT')"):
        try:
            syms.update(r[0] for r in _q(sql))
        except sqlite3.Error as e:
            log(f"other-book check failed ({sql.split()[3]}): {e}")
    return syms


def bridge_quote(sym: str):
    try:
        r = requests.get(f"{BRIDGE}/quote/{sym}", timeout=8)
        if r.status_code == 200:
            q = r.json()
            return q.get("last") or q.get("ask") or q.get("bid") or q.get("close")
    except Exception:
        pass
    return None


def unconfirmed_today() -> set:
    """Names whose BUY came back unconfirmed earlier today — never retried the same day."""
    return {r[0] for r in _q("SELECT symbol FROM fear_rebound_scan_log WHERE kind='CANDIDATE' AND verdict='ORDER_FAILED' "
                            "AND scan_ts LIKE ?", (now_et().date().isoformat() + "%",))}


def place_paper_order(sym, shares, side):
    return place_verified(BRIDGE, sym, shares, side, order_type="MARKET", log=log)


# ─────────────────────────── entry ───────────────────────────
def scan_and_enter():
    today = now_et().date()
    if entered_today():
        return
    sig = fear_signal(today)
    if not sig["ok"]:
        if not scanned_today("SUMMARY"):
            log(f"no trade today — {sig['reason']}")
            record_scan("SUMMARY", {"traded": False, "reason": sig["reason"], "mode": MODE}, [])
        return
    room = pool_room()
    if room <= 0:
        if now_et().time() < dt.time(9, 58):
            log(f"{sig['reason']} — waiting: Clockwork's pool is still busy ({clockwork_busy()} position(s) "
                f"open/pending), will retry until {ENTRY_END:%H:%M}")
            return
        log("Clockwork's pool never freed up this morning — no trade today")
        record_scan("SUMMARY", {"traded": False, "reason": "pool busy", "signal": sig["date"], "mode": MODE}, [])
        return
    if room < TOP_N and now_et().time() < dt.time(9, 55) and not unconfirmed_today():
        log(f"only {room} of {TOP_N} slots free (Clockwork exits pending) — waiting a few minutes for the rest")
        return
    unconfirmed = unconfirmed_today()
    room -= len(unconfirmed)                      # an unconfirmed BUY may be a real position — its slot stays used
    avoid = other_book_symbols() | unconfirmed
    picks, entered, uncertain, rank = [], [], 0, 0
    for sym in sig["basket"]:
        rank += 1
        if len(entered) + uncertain >= room:
            picks.append((sym, "BELOW_CUT")); continue
        if sym in avoid:
            picks.append((sym, "HELD_BY_ANOTHER_BOOK")); continue
        px = bridge_quote(sym)
        if px is None or not (PRICE_LO <= float(px) <= PRICE_HI):
            picks.append((sym, "NO_PRICE")); continue
        px = float(px)
        shares = int(PER_NAME / px)
        if shares < 1:
            picks.append((sym, "TOO_EXPENSIVE")); continue
        oid = None
        if MODE == "LIVE":
            ok, fill, oid = place_paper_order(sym, shares, "BUY")
            if not ok:
                # An unconfirmed BUY may still have filled (place_verified reports "not filled" when it cannot prove a
                # fill). Never retry that name today — a retry could double the position — and say so.
                picks.append((sym, "ORDER_FAILED"))
                if oid:
                    uncertain += 1                # the order reached IBKR — treat its slot as used
                    send_telegram(f"⚠️ Fear Rebound: BUY {sym} x{shares} (order {oid}) was not confirmed — not retrying it "
                                  f"today; check IBKR in case it filled (reconcile will flag an unowned position)")
                continue
            px = float(fill) if fill else px
        record_entry(sig["date"], sym, px, shares, rank, oid)
        entered.append(f"{sym} x{shares} @ ${px:.2f}")
        picks.append((sym, "ENTERED"))
        log(f"ENTER {sym} x{shares} @ ${px:.2f} (#{rank} hardest-hit on {sig['date']}) — sells 15:30")
    record_scan("SUMMARY", {"traded": bool(entered), "signal": sig["date"], "room": room,
                            "entered": len(entered), "mode": MODE}, picks)
    if entered:
        send_telegram(f"⚡ Fear Rebound {'BUY' if MODE == 'LIVE' else 'shadow'} — {sig['reason']}.\n"
                      f"{', '.join(entered)}\nUses Clockwork's daytime pool; sells 15:30, −{STOP_PCT:.0f}% disaster stop.")


# ─────────────────────────── exits ───────────────────────────
def _sell(t, px, reason):
    if MODE == "LIVE":
        ok, fill, _ = place_paper_order(t["symbol"], t["shares"], "SELL")
        if not ok:              # never book a close we did not get (USAR, Jul 20 2026)
            log(f"exit for {t['symbol']} NOT confirmed filled — leaving OPEN, retry next pass")
            return None
        if fill:
            px = float(fill)
    return record_exit(t["id"], px, reason)


def monitor():
    tnow = now_et().time()
    closed = []
    for t in get_open():
        px = bridge_quote(t["symbol"])
        if px is None:
            continue
        px = float(px)
        reason = None
        if t["entry_date"] < now_et().date().isoformat():
            reason = "Overdue exit — yesterday's 15:30 sell never confirmed"
        elif px <= t["stop_price"]:
            reason = f"Disaster stop ${t['stop_price']} (−{STOP_PCT:.0f}%)"
        elif tnow >= EXIT_START:
            reason = "Day exit 15:30"
        if reason:
            pnl = _sell(t, px, reason)
            if pnl is not None:
                closed.append(f"{t['symbol']} ${pnl:+.2f}")
    if closed:
        send_telegram(f"⚡ Fear Rebound closed: {', '.join(closed)}")
    still = get_open()
    if still and tnow >= ALERT_STILL_OPEN_AT and not scanned_today("ALERT_OPEN"):
        msg = f"⚠️ Fear Rebound still holds {', '.join(t['symbol'] for t in still)} at {tnow:%H:%M} — sells keep retrying until 15:58"
        log(msg); send_telegram(msg)
        record_scan("ALERT_OPEN", {"open": [t["symbol"] for t in still]}, [])


# ─────────────────────────── dashboard close + run loop ───────────────────────────
def _manual_close(t):
    """Close ONE position on an explicit dashboard instruction. Returns (ok, message). Books the exit only if the
    SELL confirmed a fill — the same rule every engine follows (factory/live/_control.py)."""
    sym = t["symbol"]
    px = bridge_quote(sym)
    if px is None:
        return False, sym + ": no price available — nothing closed"
    if MODE == "LIVE":
        ok, fill, _ = place_paper_order(sym, t["shares"], "SELL")
        if not ok:
            return False, (sym + ": SELL not confirmed filled — position left OPEN "
                           "rather than booked at a price we may not have got")
        if fill:
            px = float(fill)
    record_exit(t["id"], float(px), "Manual close via dashboard")
    return True, f"{sym} x{t['shares']} @ ${float(px):.2f}"


def _drain_control_queue():
    from factory.live import _control
    _control.drain("fear_rebound", get_open, _manual_close, log, MODE)


def _take_lock() -> bool:
    if os.path.exists(LOCK) and (time.time() - os.path.getmtime(LOCK)) < LOCK_TTL:
        return False
    open(LOCK, "w").close()
    return True


def _release_lock():
    try:
        os.remove(LOCK)
    except OSError:
        pass


def drain_only():
    """Serve dashboard close requests and NOTHING else — pressing close must never open a position."""
    init_db()
    waited = 0.0
    while os.path.exists(LOCK) and (time.time() - os.path.getmtime(LOCK)) < LOCK_TTL:
        if waited >= 20:
            log("a scheduled pass is holding the lock — close request left pending for that pass")
            return
        time.sleep(1.0); waited += 1.0
    open(LOCK, "w").close()
    try:
        _drain_control_queue()
    finally:
        _release_lock()


def run_once():
    init_db()
    _drain_control_queue()                         # answered before the hours guard (factory/live/_control.py)
    n = now_et()
    if n.minute == 0:                              # one line an hour: idle most days, so the dashboard needs a pulse
        log(f"alive — {len(get_open())} open position(s)"
            + ("" if is_market_day(n.date()) else " · market closed today"))
    if not is_market_day(n.date()):
        return
    if n.time() < dt.time(9, 30) or n.time() > EXIT_END:
        return
    if not _take_lock():
        log("previous pass still active — skipping"); return
    try:
        monitor()                                  # stops and the 15:30 exit first
        if ENTRY_START <= n.time() <= ENTRY_END:
            scan_and_enter()
    finally:
        _release_lock()


def dryscan(date_str: str):
    """What the book would buy the morning AFTER `date_str` (reads market_state; no writes, no orders)."""
    d = dt.date.fromisoformat(date_str)
    nxt = d + dt.timedelta(days=1)
    while not is_market_day(nxt):
        nxt += dt.timedelta(days=1)
    sig = fear_signal(nxt)
    print(f"\nFear Rebound dry-scan — signal session {sig['date']}, would trade {nxt}")
    print(f"  {sig['reason']}")
    if sig["ok"]:
        print(f"  basket (hardest first): {', '.join(sig['basket'])}")
        print(f"  would buy the first {TOP_N} not held by another book: {', '.join(sig['basket'][:TOP_N])} "
              f"(×${PER_NAME:,.0f} from {POOL}'s pool), sell 15:30")


def status(n: int = 15):
    rows = _q("SELECT id, signal_date, symbol, entry_date, entry_time, entry_price, shares, status, exit_time, "
              "exit_price, pnl, exit_reason, mode FROM fear_rebound_trades ORDER BY id DESC LIMIT ?", (n,), True)
    if not rows:
        print("no Fear Rebound trades yet"); return
    for r in rows:
        print(r)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--dryscan":
        init_db(); dryscan(sys.argv[2] if len(sys.argv) > 2 else (now_et().date() - dt.timedelta(days=1)).isoformat())
    elif len(sys.argv) > 1 and sys.argv[1] == "--status":
        init_db(); status()
    elif len(sys.argv) > 1 and sys.argv[1] == "--drain-only":
        drain_only()
    else:
        run_once()
