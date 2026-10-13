"""
Gap-Down Protocol — the overnight books' morning exit rule (built Oct 12 2026). LIVE on the IBKR paper account.

THE RULE. Clockwork and Night Owl buy at the close and normally sell in the next opening auction (MOO). Each morning
at ~09:05 this module labels the morning ONCE, before any MOO is submitted:
    PANIC gap-down  -> HOLD last night's positions through the open and sell them at 15:30 (−15% disaster stop
                       below the opening print). Both books, every position.
    anything else   -> sell at the open exactly as before (calm gap-down, normal, gap-up, or no decision).
A PANIC gap-down = Russell 2000 futures at least 0.5% below yesterday's 16:00 price, AND yesterday closed with fear
high (VIX/VIX3M ≥ 0.901) AND our volatile basket already down ≥1.07% over 5 days. The thresholds live in
market_state.py (PANIC_GAP / PANIC_VIX_TERM / PANIC_WILD5) — fixed on 2021-23, tested on 2024-26 — and are imported
here, so the record and the trading rule can never drift apart. Do not tune them.

WHY (docs/RESEARCH_REGISTRY.md §O1-§O8, §S). Gap-down mornings come in two kinds. When fear is up and the names are
already beaten down, forced selling overshoots at the open and the names rebound during the day; when the sell-off is
calm (Oct 7-8 2026) they do not. Measured on the book this rule actually holds — last night's Clockwork + Night Owl
picks — official prints 2021-26, label read at 09:05:
      PANIC mornings (79):  open->close +107bp, t 2.0, up 59%, positive 5 of 6 years (2021 −153bp, 6 mornings);
                            without its single best morning +89bp; median +79bp
      CALM mornings (238):  −2bp — selling at the open is right there, so nothing changes
  bars 2024-26, 32 PANIC mornings: open->15:30 +163bp vs open->close +165bp (15:30 costs nothing and clears the book
  before the 15:40 entry auction); a −15% stop below the open costs ~3bp (−10% cost 8bp: too tight on these days).
  Decision time: a 09:05 read agrees with the research's 09:25 label on 96.7% of mornings with the same outcome, and
  leaves the MOO window (09:05-09:27) almost as wide as before for Clockwork's 5-minute passes.
  ⚠️ Lumpy: ~13 PANIC mornings a year; 2025-04-09 alone was +13.9%. Judge it on `hold_gain` (holding vs selling at the
  official open, written nightly by backfill_overnight_reference.py) over many PANIC mornings, never on one.

SAFETY. One decision per day, stored in trades.db `gap_protocol` (INSERT OR IGNORE, so both engines act on the SAME
answer). Any failure — no futures price, no prior market_state row, a crash — falls back to selling at the open, the
behaviour this rule replaced. A held row stays status OPEN (the day trader's reconcile, the other books' symbol checks
and the dashboard close button all keep working); `gap_hold_date` marks it. The 15:30 sell is booked only on a
confirmed fill; its order id is kept and checked against IBKR's execution record before any retry, so a sell that
quietly filled can never be sent twice (the Sep 11 2026 oversell lesson).

CLI:  venv/bin/python -m factory.live.gap_protocol --status        recent decisions + how the held positions did
      venv/bin/python -m factory.live.gap_protocol --preview       what the NEXT morning reads right now (no writes)
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
from market_state import PANIC_GAP, PANIC_VIX_TERM, PANIC_WILD5, is_market_day  # noqa: E402
from factory.live._fills import fill_by_order_id  # noqa: E402

DB = os.path.join(ROOT, "trades.db")
MARKET_DB = os.path.join(ROOT, "market_data.db")
BRIDGE = "http://localhost:8000"
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:                                                   # pragma: no cover
    ET = None
UTC = dt.timezone.utc

DECIDE_FROM = dt.time(9, 5)          # first read of the futures gap; no MOO is submitted before a decision exists
GIVE_UP_AT = dt.time(9, 15)          # still no futures price by now -> decide "sell at the open" (the old behaviour)
LAST_CALL = dt.time(9, 26)           # a decision first attempted after this is too late to keep any MOO in time
HOLD_SINCE = dt.time(9, 31)          # held rows are watched from the open on
EXIT_AT = dt.time(15, 30)            # sell what is held
EXIT_UNTIL = dt.time(15, 58)         # keep retrying an unconfirmed sell until here
ALERT_STILL_OPEN_AT = dt.time(15, 50)
STOP_BELOW_OPEN = 0.15               # disaster stop, % below the opening print
MAX_SANE_GAP = 0.15                  # a futures "gap" beyond ±15% is a data error, not a market
HOLD, SELL, WAIT = "HOLD_TO_1530", "SELL_AT_OPEN", "WAIT"
BOOKS = {"overnight_trades": ("Clockwork", 1), "night_owl_trades": ("Night Owl", 2)}   # table -> (name, alert bit)
HOLD_COLS = (("gap_hold_date", "TEXT"), ("gap_hold_open", "REAL"), ("gap_hold_stop", "REAL"),
             ("gap_sell_oid", "TEXT"), ("ref_open", "REAL"), ("hold_gain", "REAL"))
REASON_EXIT = "Gap-Down Protocol — held through a PANIC gap-down, sold 15:30"


def now_et() -> dt.datetime:
    return dt.datetime.now(ET) if ET else dt.datetime.now()


def _print_log(msg: str):
    print(f"[{now_et().strftime('%Y-%m-%d %H:%M:%S')}] [GAP PROTOCOL] {msg}", flush=True)


def send_telegram(msg: str):
    """Equity bot. Best-effort: a Telegram outage must never block a decision or a sell."""
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
        token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
        if token and chat:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": msg}, timeout=10)
    except Exception as e:
        _print_log(f"telegram send failed: {e}")


# ─────────────────────────── DB ───────────────────────────
def _q(sql, params=(), as_dict=False, db=None):
    c = sqlite3.connect(db or DB, timeout=30)
    try:
        if as_dict:
            c.row_factory = sqlite3.Row
        return [dict(r) if as_dict else r for r in c.execute(sql, params)]
    finally:
        c.close()


def _x(sql, params=(), db=None) -> int:
    c = sqlite3.connect(db or DB, timeout=30)
    try:
        cur = c.execute(sql, params)
        c.commit()
        return cur.rowcount
    finally:
        c.close()


def init_db(db=None):
    """The decision table, plus the hold columns on both overnight books' tables. Each engine calls this from its own
    init_db() AFTER creating its table, so the columns exist before any pass reads them."""
    c = sqlite3.connect(db or DB, timeout=30)
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS gap_protocol(
            date TEXT PRIMARY KEY, decided_at TEXT, label TEXT, action TEXT,
            rty_gap REAL, rty_now REAL, rty_now_src TEXT, rty_prev REAL, rty_prev_src TEXT,
            state_date TEXT, vix_term REAL, wild_r5 REAL, reason TEXT, alerted_open INTEGER DEFAULT 0)""")
        for tbl in BOOKS:
            have = {r[1] for r in c.execute(f"PRAGMA table_info({tbl})")}
            if not have:
                continue                               # that book's table does not exist yet
            for col, typ in HOLD_COLS:
                if col not in have:                    # only ever ALTER when a column is genuinely missing
                    c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typ}")
        c.commit()
    finally:
        c.close()


# ─────────────────────────── inputs ───────────────────────────
def previous_session(d: dt.date) -> dt.date:
    p = d - dt.timedelta(days=1)
    while not is_market_day(p):
        p -= dt.timedelta(days=1)
    return p


def prev_state(day: dt.date) -> dict | None:
    """Yesterday's close state from market_state (written 18:15). None if that session's row is missing — PANIC then
    cannot be confirmed, and the rule falls back to selling at the open."""
    prev = previous_session(day).isoformat()
    try:
        r = _q("SELECT date, vix_term, wild_r5 FROM market_state WHERE date=?", (prev,), True)
    except sqlite3.Error:
        return None
    return r[0] if r else None


def rty_prev_close(prev: dt.date) -> tuple[float | None, str]:
    """Russell futures at the previous session's 16:00 (the close of its 15:55 5-min bar) — the same reference the
    research and market_state use. Our own futures_bars_5m first (filled 21:30 every weekday), Yahoo second."""
    ts = dt.datetime.combine(prev, dt.time(15, 55), ET).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        r = _q("SELECT close FROM futures_bars_5m WHERE symbol='RTY' AND ts_utc=?", (ts,), db=MARKET_DB)
        if r and r[0][0]:
            return float(r[0][0]), "futures_bars_5m"
    except sqlite3.Error:
        pass
    try:
        import yfinance as yf
        h = yf.Ticker("RTY=F").history(period="5d", interval="5m")
        h.index = h.index.tz_convert("America/New_York")
        b = h[(h.index.date == prev) & (h.index.strftime("%H:%M") == "15:55")]
        if len(b):
            return float(b["Close"].iloc[-1]), "yfinance"
    except Exception as e:
        return None, f"no 16:00 Russell price for {prev} ({type(e).__name__})"
    return None, f"no 16:00 Russell price for {prev}"


def rty_now() -> tuple[float | None, str]:
    """Russell futures right now. IBKR's live quote through the bridge (real-time on this account); Yahoo's ~10-min
    delayed 1-minute bars as the fallback. Never yesterday's settlement (`close`) — that would read as no gap at all."""
    try:
        q = requests.get(f"{BRIDGE}/futures/quote/RTY", timeout=25).json()
        bid, ask = q.get("bid"), q.get("ask")
        px = q.get("last") or ((bid + ask) / 2 if bid and ask else None)
        if px:
            src = "ibkr_live" if q.get("source") == "ibkr_live" else f"bridge/{q.get('source')} (delayed)"
            return float(px), src
    except Exception:
        pass
    try:
        import yfinance as yf
        h = yf.Ticker("RTY=F").history(period="1d", interval="1m")
        if len(h):
            last_ts = h.index[-1].tz_convert("America/New_York")
            if last_ts.date() == now_et().date() and last_ts.time() >= dt.time(6, 0):
                return float(h["Close"].iloc[-1]), f"yfinance {last_ts:%H:%M} (~10-min delay)"
    except Exception:
        pass
    return None, "no Russell futures price (bridge and Yahoo both failed)"


def classify(gap: float | None, state: dict | None) -> str:
    if gap is None:
        return "UNKNOWN"
    if gap <= PANIC_GAP:
        if not state or state.get("vix_term") is None or state.get("wild_r5") is None:
            return "GAPDOWN_UNCONFIRMED"           # gap-down, but yesterday's fear state is unknown -> not PANIC
        if state["vix_term"] >= PANIC_VIX_TERM and state["wild_r5"] <= PANIC_WILD5:
            return "PANIC_GAPDOWN"
        return "CALM_GAPDOWN"
    return "GAPUP" if gap >= -PANIC_GAP else "NORMAL"


def read_inputs(day: dt.date) -> dict:
    """Everything the label needs, for the morning of `day`. Raises nothing; missing pieces are reported in `error`."""
    prev = previous_session(day)
    st = prev_state(day)
    p0, s0 = rty_prev_close(prev)
    p1, s1 = rty_now()
    out = {"state_date": st["date"] if st else None, "vix_term": st.get("vix_term") if st else None,
           "wild_r5": st.get("wild_r5") if st else None, "rty_prev": p0, "rty_prev_src": s0,
           "rty_now": p1, "rty_now_src": s1, "rty_gap": None, "error": None}
    if p0 is None or p1 is None:
        out["error"] = s0 if p0 is None else s1
        return out
    g = p1 / p0 - 1
    if abs(g) > MAX_SANE_GAP:
        out["error"] = f"Russell gap {g * 100:+.1f}% is not believable (prev {p0} [{s0}], now {p1} [{s1}])"
        return out
    out["rty_gap"] = g
    return out


# ─────────────────────────── the daily decision ───────────────────────────
def today_decision(day: str | None = None) -> dict | None:
    day = day or now_et().date().isoformat()
    try:
        r = _q("SELECT * FROM gap_protocol WHERE date=?", (day,), True)
    except sqlite3.Error:
        return None
    return r[0] if r else None


def holding_today(day: str | None = None) -> bool:
    d = today_decision(day)
    return bool(d and d["action"] == HOLD)


def _overnight_positions(day: str) -> dict:
    """{book name: [symbols]} — last night's LIVE positions still open this morning, for the announcement."""
    out = {}
    for tbl, (name, _bit) in BOOKS.items():
        try:
            out[name] = [r[0] for r in _q(f"SELECT symbol FROM {tbl} WHERE status='OPEN' AND mode='LIVE' "
                                          f"AND entry_date < ? ORDER BY id", (day,))]
        except sqlite3.Error:
            out[name] = []
    return out


def _persist(day: str, label: str, action: str, info: dict, reason: str, log) -> tuple[dict, bool]:
    n = _x("""INSERT OR IGNORE INTO gap_protocol(date, decided_at, label, action, rty_gap, rty_now, rty_now_src,
              rty_prev, rty_prev_src, state_date, vix_term, wild_r5, reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
           (day, now_et().strftime("%H:%M:%S"), label, action, info.get("rty_gap"), info.get("rty_now"),
            info.get("rty_now_src"), info.get("rty_prev"), info.get("rty_prev_src"), info.get("state_date"),
            info.get("vix_term"), info.get("wild_r5"), reason))
    row = today_decision(day)
    if n == 1:
        log(f"decision for {day}: {label} -> {action} — {reason}")
    return row, n == 1


def _announce(row: dict, day: str):
    g = row.get("rty_gap")
    gtxt = f"Russell futures {g * 100:+.2f}% since yesterday's 16:00" if g is not None else "no futures read"
    if row["action"] == HOLD:
        pos = _overnight_positions(day)
        held = "; ".join(f"{b}: {', '.join(s)}" for b, s in pos.items() if s) or "nothing was held overnight"
        send_telegram(f"🌅 Gap-Down Protocol — PANIC gap-down ({gtxt}; yesterday VIX/VIX3M {row['vix_term']:.3f}, "
                      f"volatile basket 5-day {row['wild_r5'] * 100:+.1f}%).\n"
                      f"HOLDING last night's positions through the open, selling at 15:30 "
                      f"(−{STOP_BELOW_OPEN * 100:.0f}% stop below the open) instead of selling in the opening auction.\n"
                      f"{held}")
    elif row["label"] in ("UNKNOWN", "NO_DECISION", "GAPDOWN_UNCONFIRMED"):
        send_telegram(f"⚠️ Gap-Down Protocol could not judge this morning ({row['reason']}) — the overnight books "
                      f"sell at the open as usual.")


def decide(log=None, now: dt.datetime | None = None, telegram: bool = True) -> dict | None:
    """Today's decision row, deciding it if needed. None = not decided YET (caller waits; nothing may be sold).

    Before 09:05: None. 09:05-09:26: read the futures gap and decide; if the inputs cannot be read, retry on the next
    pass until 09:15, then decide "sell at the open". First called after 09:26: too late to change anything at the
    open — recorded as NO_DECISION (sell). The first writer wins (INSERT OR IGNORE): every engine acts on one answer."""
    log = log or _print_log
    n = now or now_et()
    day = n.date().isoformat()
    if not is_market_day(n.date()):
        return None
    row = today_decision(day)
    if row:
        return row
    if n.time() < DECIDE_FROM:
        return None
    init_db()
    if n.time() > LAST_CALL:
        row, new = _persist(day, "NO_DECISION", SELL, {}, f"first asked at {n:%H:%M}, after the {LAST_CALL:%H:%M} "
                                                                f"last call", log)
        if new and telegram:
            _announce(row, day)
        return row
    info = read_inputs(n.date())
    if info["error"]:
        if n.time() < GIVE_UP_AT:
            log(f"cannot decide yet — {info['error']}; retrying next pass (gives up at {GIVE_UP_AT:%H:%M})")
            return None
        row, new = _persist(day, "UNKNOWN", SELL, info, info["error"], log)
        if new and telegram:
            _announce(row, day)
        return row
    label = classify(info["rty_gap"], {"vix_term": info["vix_term"], "wild_r5": info["wild_r5"]}
                     if info["state_date"] else None)
    action = HOLD if label == "PANIC_GAPDOWN" else SELL
    why = (f"Russell {info['rty_gap'] * 100:+.2f}% ({info['rty_now_src']}); yesterday "
           + (f"VIX/VIX3M {info['vix_term']:.3f}, basket 5-day {info['wild_r5'] * 100:+.2f}%"
              if info["vix_term"] is not None and info["wild_r5"] is not None
              else f"state missing for {previous_session(n.date())}"))
    row, new = _persist(day, label, action, info, why, log)
    if new and telegram:
        _announce(row, day)
    return row


def morning_action(log=None, now: dt.datetime | None = None) -> str:
    """WAIT / HOLD / SELL for an engine's exit_at_open(). A crash in the decision itself falls back to SELL once the
    give-up time has passed (never later than the MOO cutoff), and to WAIT before it."""
    log = log or _print_log
    n = now or now_et()
    try:
        row = decide(log=log, now=n)
    except Exception as e:                                          # pragma: no cover — defensive
        log(f"Gap-Down Protocol decision crashed ({type(e).__name__}: {e})")
        return SELL if n.time() >= GIVE_UP_AT else WAIT
    if row is None:
        return SELL if n.time() > LAST_CALL else WAIT
    return row["action"]


# ─────────────────────────── holding ───────────────────────────
def mark_held(table: str, row: dict, day: str, log=None, db=None) -> bool:
    """Mark one position held for today. False (and logged) if the write failed — the row is then neither held nor
    sold this pass, and the next pass tries again; it never falls through to a MOO sale on a PANIC morning."""
    log = log or _print_log
    try:
        _x(f"UPDATE {table} SET gap_hold_date=? WHERE id=? AND status='OPEN'", (day, row["id"]), db=db)
    except sqlite3.Error as e:
        log(f"could not mark {row['symbol']} held ({e}) — retrying next pass")
        return False
    log(f"HOLD {row['symbol']} x{row['shares']} through the open — PANIC gap-down; sells 15:30")
    return True


def unsold_holds(day: str | None = None) -> set:
    """Symbols either overnight book is still holding under the protocol today (LIVE). An engine must not send a
    closing-auction BUY for a name it — or the other book — may still be selling with a market order."""
    day = day or now_et().date().isoformat()
    syms = set()
    for tbl in BOOKS:
        try:
            syms.update(r[0] for r in _q(f"SELECT symbol FROM {tbl} WHERE status='OPEN' AND mode='LIVE' "
                                         f"AND gap_hold_date=?", (day,)))
        except sqlite3.Error:
            pass
    return syms


def has_holds(table: str, mode: str, day: str | None = None) -> bool:
    day = day or now_et().date().isoformat()
    try:
        return _q(f"SELECT COUNT(*) FROM {table} WHERE status='OPEN' AND mode=? AND gap_hold_date=?",
                  (mode, day))[0][0] > 0
    except sqlite3.Error:
        return False


def opening_print(sym: str, day: str) -> float | None:
    """The stock's opening print today — the open of its 09:30 5-min bar from the bridge."""
    try:
        bars = requests.get(f"{BRIDGE}/history/{sym}", params={"bar_size": "5 mins", "duration": "1 D"},
                            timeout=20).json()
        for b in bars if isinstance(bars, list) else []:
            if str(b.get("date", "")).startswith(f"{day}T09:30") and b.get("open"):
                return float(b["open"])
    except Exception:
        pass
    return None


def monitor_holds(eng, table: str, now: dt.datetime | None = None):
    """Never raises: a failure here must not stop the engine's own exits and closing-auction entries in the same
    pass. See _monitor_holds for the work."""
    try:
        _monitor_holds(eng, table, now)
    except Exception as e:
        try:
            eng.log(f"Gap-Down Protocol monitor failed ({type(e).__name__}: {e}) — retried next pass")
        except Exception:
            pass


def _monitor_holds(eng, table: str, now: dt.datetime | None = None):
    """Every pass, for the rows this book is holding today: set the stop from the opening print, sell on the
    disaster stop, sell everything at 15:30. `eng` is the engine module (Clockwork / Night Owl) — its own get_open,
    bridge_quote, place_paper_order, record_exit, MODE, BRIDGE, DB and log, so fills are booked exactly as the
    engine books every other exit."""
    n = now or now_et()
    t, day = n.time(), n.date().isoformat()
    if t < HOLD_SINCE or t > EXIT_UNTIL:
        return
    held = [r for r in eng.get_open() if r.get("gap_hold_date") == day]
    if not held:
        return
    closed = []
    for r in held:
        sym = r["symbol"]
        # 1. A sell sent on an earlier pass may have filled after we stopped waiting. Ask IBKR about THAT order id
        #    before anything else — never send a second sell on an ambiguous read (Sep 11 2026).
        if eng.MODE == "LIVE" and r.get("gap_sell_oid"):
            oid0 = r["gap_sell_oid"]
            ok, fpx, _sh = fill_by_order_id(eng.BRIDGE, oid0, eng.log)
            if ok and fpx:
                eng.record_exit(r["id"], fpx, REASON_EXIT + " (fill confirmed late)")
                closed.append(f"{sym} @ ${fpx:.2f}")
                continue
            if t < EXIT_AT:
                # a stop sell is in doubt and we are not yet at the day exit — do not stack another one
                eng.log(f"{sym}: earlier protocol sell {oid0} not confirmed yet — waiting, no new order")
                continue
            # Day exit with an earlier sell still unconfirmed: CANCEL it before sending a fresh one (a halted stock
            # would otherwise end up with two working sells), then ask the execution record once more.
            try:
                requests.post(f"{eng.BRIDGE}/order/{oid0}/cancel", json={}, timeout=5)
            except Exception:
                pass
            time.sleep(2)
            ok, fpx, _sh = fill_by_order_id(eng.BRIDGE, oid0, eng.log)
            if ok and fpx:
                eng.record_exit(r["id"], fpx, REASON_EXIT + " (fill confirmed late)")
                closed.append(f"{sym} @ ${fpx:.2f}")
                continue
            eng.log(f"{sym}: earlier protocol sell {oid0} cancelled unfilled — sending a fresh 15:30 sell")
        # 2. The stop reference: today's opening print (once).
        if r.get("gap_hold_open") is None:
            op = opening_print(sym, day)
            src = "opening print"
            if op is None and t >= dt.time(9, 40):
                q = eng.bridge_quote(sym)
                op, src = (float(q), "first quote seen (no 09:30 bar)") if q else (None, src)
            if op:
                stop = round(op * (1 - STOP_BELOW_OPEN), 2)
                _x(f"UPDATE {table} SET gap_hold_open=?, gap_hold_stop=? WHERE id=?", (op, stop, r["id"]), db=eng.DB)
                r["gap_hold_open"], r["gap_hold_stop"] = op, stop
                eng.log(f"{sym}: held from the {src} ${op:.2f}; disaster stop ${stop:.2f}")
        q = eng.bridge_quote(sym)
        px = float(q) if q is not None else None
        reason = None
        if px is not None and r.get("gap_hold_stop") and px <= r["gap_hold_stop"]:
            reason = f"Gap-Down Protocol disaster stop ${r['gap_hold_stop']:.2f} (−{STOP_BELOW_OPEN * 100:.0f}% below the open)"
        elif t >= EXIT_AT:
            reason = REASON_EXIT
        if not reason:
            continue
        if eng.MODE == "LIVE":
            ok, fill, oid = eng.place_paper_order(sym, r["shares"], "SELL")      # MARKET
            if oid:
                _x(f"UPDATE {table} SET gap_sell_oid=? WHERE id=?", (str(oid), r["id"]), db=eng.DB)
            if not ok:
                eng.log(f"{sym}: protocol SELL not confirmed filled — left OPEN; order {oid} is checked before any retry")
                continue
            if not fill:
                # Never price it from "the last SLD fill in this symbol" — in a shared account that can be another
                # book's sale. The order id is stored; the next pass reads THIS order's own execution record.
                eng.log(f"{sym}: protocol SELL reported without a fill price — left OPEN; order {oid} is checked next pass")
                continue
            price = float(fill)
        else:
            if px is None:
                continue
            price = px
        eng.record_exit(r["id"], price, reason)
        closed.append(f"{sym} @ ${price:.2f}")
    name, bit = BOOKS.get(table, (table, 0))
    if closed:
        send_telegram(f"🌅 Gap-Down Protocol — {name} sold {', '.join(closed)}")
    if t >= ALERT_STILL_OPEN_AT and bit:
        still = [r["symbol"] for r in eng.get_open() if r.get("gap_hold_date") == day]
        dec = today_decision(day) or {}
        if still and not (int(dec.get("alerted_open") or 0) & bit):
            _x("UPDATE gap_protocol SET alerted_open = COALESCE(alerted_open,0) | ? WHERE date=?", (bit, day))
            msg = (f"⚠️ Gap-Down Protocol: {name} still holds {', '.join(still)} at {t:%H:%M} — sells keep retrying "
                   f"until {EXIT_UNTIL:%H:%M}; anything left is sold at tomorrow's open")
            eng.log(msg)
            send_telegram(msg)


# ─────────────────────────── CLI ───────────────────────────
def status(n: int = 15):
    init_db()
    rows = _q("SELECT * FROM gap_protocol ORDER BY date DESC LIMIT ?", (n,), True)
    if not rows:
        print("no Gap-Down Protocol decisions yet")
    for r in rows:
        g = f"{r['rty_gap'] * 100:+.2f}%" if r["rty_gap"] is not None else "   —  "
        print(f"{r['date']} {r['decided_at']}  {r['label']:20s} {r['action']:13s} RTY {g}  {r['reason']}")
    for tbl, (name, _b) in BOOKS.items():
        try:
            h = _q(f"SELECT gap_hold_date, symbol, shares, entry_price, exit_price, pnl, hold_gain, exit_reason "
                   f"FROM {tbl} WHERE gap_hold_date IS NOT NULL ORDER BY id DESC LIMIT 30", (), True)
        except sqlite3.Error:
            continue
        if h:
            print(f"\n{name} positions held by the protocol (hold_gain = held vs selling at the official open):")
            for r in h:
                hg = f"{r['hold_gain']:+.2f}" if r["hold_gain"] is not None else "pending"
                pnl = f"{r['pnl']:+.2f}" if r["pnl"] is not None else "open"
                print(f"  {r['gap_hold_date']} {r['symbol']:6s} x{r['shares']:<4} pnl {pnl:>9}  hold_gain {hg:>9}  "
                      f"{r['exit_reason'] or ''}")


def preview():
    """What the NEXT morning reads right now — no writes, no Telegram."""
    n = now_et()
    day = n.date()
    if n.time() >= dt.time(16, 0) or not is_market_day(day):
        day += dt.timedelta(days=1)
        while not is_market_day(day):
            day += dt.timedelta(days=1)
    info = read_inputs(day)
    st = {"vix_term": info["vix_term"], "wild_r5": info["wild_r5"]} if info["state_date"] else None
    print(f"morning of {day} (reads {previous_session(day)}'s close state):")
    print(json.dumps({k: (round(v, 5) if isinstance(v, float) else v) for k, v in info.items()}, indent=1))
    print("label now:", classify(info["rty_gap"], st) if not info["error"] else f"UNKNOWN ({info['error']})")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--status":
        status()
    elif len(sys.argv) > 1 and sys.argv[1] == "--preview":
        preview()
    else:
        print(__doc__)
