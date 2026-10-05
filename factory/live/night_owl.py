"""
Night Owl — LIVE PAPER trader for the learned overnight model (built Oct 5 2026).

THE TRADE (same window as Clockwork): buy a few WILD names in the closing auction, sell them in the next
morning's opening auction. Our universe earns most of its return while the market is closed.
THE PICK (what is new): a machine-learned ranking model (factory/night_owl_model.py — gradient-boosted
trees on ~80 daily signals, re-trained monthly) scores every name each morning; the 3 best-scored WILD
names are bought at the close. Clockwork reads one clue (how often a stock gapped up lately); Night Owl
learned its own blend of ~80, where that clue carries ~11%. Evidence: docs/RESEARCH_REGISTRY.md F2-F11
(out of sample 2018-2026: top-3 +18.7bp/night above the WILD average, positive every year; it passed the
factory Proving Ground 8/8). ⚠️ Tail risk: ~-60% summed drawdown in the 2021-23 speculative-stock bear.

DAY CYCLE (launchd every 60s, self-gating; stateless between runs — state lives in trades.db):
  09:00-09:27  submit MOO SELL for last night's positions        (exact Clockwork exit path)
  09:31-09:59  confirm the MOO fills; MARKET fallback after 09:45
  09:35-15:49  SCORE once: refresh daily data, read today's opening prices, score every name, store the
               ranking in night_owl_scores and post tonight's picks to Telegram. Features use only
               yesterday's close and earlier plus today's open, so the scores are final from the open on.
  15:41-15:49  ENTER: wait for Clockwork's picks (until 15:46), skip any name another book holds or is
               buying tonight, skip earnings tonight/tomorrow, submit MOC BUY for the top 3 WILD names
  16:00-16:40  confirm the closing-auction fills
Fills are confirmed by ORDER ID from IBKR's execution records — never inferred from the shared net
position (the Sep 10-11 2026 oversell). Judge the strategy on ref_pnl (official prints), not pnl: the
IBKR paper account fabricates auction fills (CLAUDE.md, Sep 20 2026).

  MODE=SHADOW records intended trades only; MODE=LIVE places real orders on the IBKR PAPER account.
  Dry-run the ranking for a day (no writes):  python -m factory.live.night_owl --dryscan [YYYY-MM-DD]
"""
from __future__ import annotations
import os, sys, sqlite3, time, json, datetime as dt
import requests
import pandas as pd
sys.path.insert(0, "/Users/sushil/trading")
from factory.live._fills import place_verified, fill_by_order_id  # noqa: E402

EARNINGS_BLACKOUT_DAYS = 1     # skip a name reporting tonight or tomorrow morning (Clockwork's rule)
_earn_cache: dict = {}


def days_to_earnings(sym):
    """Calendar days to the next earnings date, or None when unknown (then allowed — Clockwork's rule)."""
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


LOCK = "/tmp/night_owl.lock"
MODE = os.environ.get("NIGHT_OWL_MODE", "SHADOW").upper()   # SHADOW | LIVE
BRIDGE = "http://localhost:8000"
DB = "/Users/sushil/trading/trades.db"
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

CAPITAL = 10_000.0         # this book's own budget (paper), separate from every other book
TOP_N = 3                  # matches Clockwork and the research test (3 x $3,333)
PER_NAME = CAPITAL / TOP_N
PRICE_LO, PRICE_HI = 5.0, 800.0
SCORE_START = dt.time(9, 35)                                  # opening prints are in by now
ENTRY_START, ENTRY_END = dt.time(15, 41), dt.time(15, 49)    # submit MOC BUY (cutoff ~15:50)
WAIT_FOR_CLOCKWORK_UNTIL = dt.time(15, 46)                    # let Clockwork pick first, then avoid its names
ENTRY_CONFIRM_START, ENTRY_CONFIRM_END = dt.time(16, 0), dt.time(16, 40)
EXIT_START, EXIT_END = dt.time(9, 0), dt.time(9, 27)          # submit MOO SELL
EXIT_CONFIRM_START, EXIT_CONFIRM_END = dt.time(9, 31), dt.time(9, 59)
EXIT_FALLBACK_AFTER = dt.time(9, 45)
SCORE_RETRY_S = 900        # a failed scoring attempt is retried at most every 15 min
US_HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
               "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"}


def now_et() -> dt.datetime:
    return dt.datetime.now(ET) if ET else dt.datetime.now()


def log(msg: str):
    print(f"[{now_et().strftime('%Y-%m-%d %H:%M:%S')}] [NIGHT OWL {MODE}] {msg}", flush=True)


def is_market_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in US_HOLIDAYS


def send_telegram(msg: str):
    """Equity bot. Best-effort: a Telegram outage must never block a trade or a fill check."""
    try:
        from dotenv import load_dotenv
        load_dotenv("/Users/sushil/trading/.env")
        token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
        if token and chat:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": msg}, timeout=10)
    except Exception as e:
        log(f"telegram send failed: {e}")


# ─────────────────────────── DB ───────────────────────────
def init_db():
    c = sqlite3.connect(DB)
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS night_owl_trades(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, entry_date TEXT, entry_time TEXT, entry_price REAL, shares INTEGER,
            status TEXT DEFAULT 'OPEN', exit_date TEXT, exit_time TEXT, exit_price REAL,
            pnl REAL, pnl_pct REAL, score REAL, model_rank INTEGER, model_version TEXT,
            mode TEXT, order_id TEXT, exit_reason TEXT,
            ref_entry REAL, ref_exit REAL, ref_pnl REAL, exec_drag REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS night_owl_scores(
            id INTEGER PRIMARY KEY AUTOINCREMENT, score_date TEXT, symbol TEXT, score REAL,
            rank INTEGER, wild INTEGER, wild_rank INTEGER, gap REAL, prev_close REAL,
            model_version TEXT, created_at TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_nos_date ON night_owl_scores(score_date)")
        c.execute("""CREATE TABLE IF NOT EXISTS night_owl_scan_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT, scan_ts TEXT, kind TEXT,
            symbol TEXT, score REAL, verdict TEXT, detail TEXT)""")
        c.commit()
    finally:
        c.close()


def _q(sql, params=(), row_factory=False):
    c = sqlite3.connect(DB)
    try:
        if row_factory:
            c.row_factory = sqlite3.Row
        return [dict(r) if row_factory else r for r in c.execute(sql, params)]
    finally:
        c.close()


def _x(sql, params=()):
    c = sqlite3.connect(DB)
    try:
        c.execute(sql, params)
        c.commit()
    finally:
        c.close()


def get_open() -> list[dict]:
    """Only rows of the CURRENT mode — a SHADOW row must never produce a real SELL (Clockwork, Sep 5)."""
    return _q("SELECT * FROM night_owl_trades WHERE status='OPEN' AND mode=?", (MODE,), True)


def entered_today() -> bool:
    today = now_et().date().isoformat()
    return _q("SELECT COUNT(*) FROM night_owl_trades WHERE entry_date=? AND mode=?", (today, MODE))[0][0] > 0


def record_entry(sym, price, shares, score, rank, version, order_id=None, status="OPEN"):
    now = now_et()
    _x("""INSERT INTO night_owl_trades(symbol,entry_date,entry_time,entry_price,shares,status,
          score,model_rank,model_version,mode,order_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
       (sym, now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), price, shares, status,
        round(float(score), 6), int(rank), version, MODE, order_id))


def rows_with_status(st: str) -> list[dict]:
    return _q("SELECT * FROM night_owl_trades WHERE status=? AND mode=?", (st, MODE), True)


def set_status(tid, st, **cols):
    sets = ",".join([f"{k}=?" for k in cols] + ["status=?"])
    _x(f"UPDATE night_owl_trades SET {sets} WHERE id=?", (*cols.values(), st, tid))


def order_fill(oid):
    """(filled, avg_price) for THIS order id — execution records first, status endpoint second,
    never the shared net position (Clockwork's Sep 11 2026 lesson)."""
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
    """(qty, avg_cost) from IBKR, (0, None) if flat, (None, None) if the check failed — never 'flat'."""
    try:
        r = requests.get(f"{BRIDGE}/portfolio", timeout=10).json()
        for p in r:
            if p.get("symbol") == sym:
                return int(p.get("qty", 0) or 0), (float(p["avgCost"]) if p.get("avgCost") else None)
        return 0, None
    except Exception as e:
        log(f"portfolio check failed for {sym}: {e}")
        return None, None


def record_exit(tid, price, reason="Sold at the open (MOO)"):
    t = _q("SELECT * FROM night_owl_trades WHERE id=?", (tid,), True)[0]
    from database import equity_commission
    pnl = (price - t["entry_price"]) * t["shares"] - equity_commission(t["shares"])
    pct = (price - t["entry_price"]) / t["entry_price"] * 100
    now = now_et()
    _x("""UPDATE night_owl_trades SET status='CLOSED',exit_date=?,exit_time=?,exit_price=?,
          pnl=?,pnl_pct=?,exit_reason=? WHERE id=?""",
       (now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), price, pnl, pct, reason, tid))
    log(f"EXIT #{tid} {t['symbol']} @ ${price:.2f} — {reason} | PnL ${pnl:+.2f} ({pct:+.1f}%)")


def record_scan(funnel: dict, picks: list):
    ts = now_et().strftime("%Y-%m-%d %H:%M:%S")
    c = sqlite3.connect(DB)
    try:
        c.execute("INSERT INTO night_owl_scan_log(scan_ts,kind,verdict,detail) VALUES(?,?,?,?)",
                  (ts, "SUMMARY", "SCAN", json.dumps(funnel)))
        for sym, score, verdict in picks:
            c.execute("INSERT INTO night_owl_scan_log(scan_ts,kind,symbol,score,verdict) VALUES(?,?,?,?,?)",
                      (ts, "CANDIDATE", sym, round(float(score), 6), verdict))
        c.execute("DELETE FROM night_owl_scan_log WHERE id < (SELECT MAX(id)-3000 FROM night_owl_scan_log)")
        c.commit()
    finally:
        c.close()


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
    return place_verified(BRIDGE, sym, shares, side, order_type=order_type, log=log)


# ─────────────────────────── scoring (morning) ───────────────────────────
def scores_for(day: str) -> list[dict]:
    return _q("SELECT * FROM night_owl_scores WHERE score_date=? ORDER BY rank", (day,), True)


def _last_scan_ts(kind: str, day: str):
    r = _q("SELECT MAX(scan_ts) FROM night_owl_scan_log WHERE kind=? AND scan_ts LIKE ?", (kind, day + '%'))
    return r[0][0] if r and r[0][0] else None


def score_today(force=False) -> bool:
    """Score every name for tonight and store it. Idempotent per day. Returns True when scores exist."""
    today = now_et().date().isoformat()
    if scores_for(today) and not force:
        return True
    last_fail = _last_scan_ts("SCORE_FAIL", today)
    if last_fail and not force:
        age = (now_et().replace(tzinfo=None) - dt.datetime.fromisoformat(last_fail)).total_seconds()
        if age < SCORE_RETRY_S:
            return False
    from factory import night_owl_model as M
    try:
        cache, fresh = M.refresh_daily(log=log)
        day = pd.Timestamp(today)
        prior = cache[cache["date"] < day]["date"].max()
        prev_mkt = day - pd.offsets.BDay(1)
        while not is_market_day(prev_mkt.date()):
            prev_mkt -= pd.offsets.BDay(1)
        if prior < prev_mkt:
            raise RuntimeError(f"daily data is stale: last completed session {prior.date()}, expected {prev_mkt.date()}")
        today_rows = fresh[fresh["date"] == day]
        opens = today_rows.set_index("symbol")["open"].dropna().to_dict()
        if len(opens) < 100:
            raise RuntimeError(f"only {len(opens)} opening prices for {today} — data not in yet")
        art = M.load_model()
        if not M.model_is_current(art, day):
            log(f"⚠️ model {art['version']} is from an earlier month — the 17:30 prep job should have "
                f"re-trained it; scoring with it anyway")
        s = M.score_day(day, cache, opens, art)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        log(f"SCORING FAILED — {msg}")
        first_today = _last_scan_ts("SCORE_FAIL", today) is None
        _x("INSERT INTO night_owl_scan_log(scan_ts,kind,verdict,detail) VALUES(?,?,?,?)",
           (now_et().strftime("%Y-%m-%d %H:%M:%S"), "SCORE_FAIL", "ERROR", msg[:300]))
        if first_today:
            send_telegram(f"🦉 Night Owl could not score today's picks: {msg[:200]}\n"
                          f"It retries every 15 min until 15:49; no trade tonight if it keeps failing.")
        return False
    created = now_et().isoformat(timespec="seconds")
    c = sqlite3.connect(DB)
    try:
        c.execute("DELETE FROM night_owl_scores WHERE score_date=?", (today,))
        c.executemany("""INSERT INTO night_owl_scores(score_date,symbol,score,rank,wild,wild_rank,gap,prev_close,
                         model_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                      [(today, r.symbol, float(r.score), int(r.rank), int(bool(r.wild)), int(r.wild_rank),
                        None if pd.isna(r.gap) else float(r.gap), None if pd.isna(r.prev_close) else float(r.prev_close),
                        r.model_version, created) for r in s.itertuples()])
        c.commit()
    finally:
        c.close()
    top = s[s.wild].head(TOP_N + 2)
    log(f"scored {len(s)} names with {art['version']} (trained through {art['trained_through']}); "
        f"top WILD: " + ", ".join(f"{r.symbol} {r.score:+.4f}" for r in top.itertuples()))
    send_telegram("🦉 Night Owl — tonight's picks (buys at the close, sells at tomorrow's open)\n" +
                  "\n".join(f"{i + 1}. {r.symbol}  gap {r.gap * 100:+.1f}%" for i, r in enumerate(s[s.wild].head(TOP_N).itertuples())) +
                  f"\nnext in line: {', '.join(s[s.wild].iloc[TOP_N:TOP_N + 3].symbol)}"
                  f"\nmodel {art['version']} · names another book holds tonight are skipped at 15:46")
    return True


# ─────────────────────────── entry ───────────────────────────
def other_book_symbols(today: str) -> set:
    """Names any other live book holds right now or is buying tonight — Night Owl never doubles up."""
    syms = set()
    queries = (
        ("SELECT symbol FROM trades WHERE status='OPEN' AND setup_type!='RECONCILED'", ()),
        ("SELECT symbol FROM wave_trades WHERE mode='LIVE' AND status IN ('OPEN','PENDING_ENTRY','PENDING_EXIT')", ()),
        ("SELECT symbol FROM contrarian_trades WHERE mode='LIVE' AND status IN ('OPEN','PENDING_ENTRY','PENDING_EXIT')", ()),
        ("SELECT symbol FROM overnight_trades WHERE mode='LIVE' AND (status IN ('OPEN','PENDING_ENTRY','PENDING_EXIT') OR entry_date=?)", (today,)),
    )
    for sql, params in queries:
        try:
            syms.update(r[0] for r in _q(sql, params))
        except Exception as e:
            log(f"other-book check failed ({sql.split()[3]}): {e}")
    return syms


def clockwork_has_entered(today: str) -> bool:
    try:
        return _q("SELECT COUNT(*) FROM overnight_trades WHERE mode='LIVE' AND entry_date=?", (today,))[0][0] > 0
    except Exception:
        return True       # no Clockwork table → nothing to wait for


def scan_and_enter():
    today = now_et().date().isoformat()
    if entered_today():
        log("already entered today — skipping"); return
    # OPEN + both pending states: a morning sell that has not confirmed still holds capital, and buying a
    # fresh 3 on top of it would take this book past its own budget (Clockwork's Sep 20 cap, extended)
    already = len(get_open()) + len(rows_with_status("PENDING_ENTRY")) + len(rows_with_status("PENDING_EXIT"))
    if already >= TOP_N:
        log(f"{already} position(s) still open/pending (>= TOP_N {TOP_N}) — no new entries; "
            f"last night's exits have not cleared")
        record_scan({"blocked": "positions_still_open", "mode": MODE}, []); return
    if now_et().time() < WAIT_FOR_CLOCKWORK_UNTIL and not clockwork_has_entered(today):
        log("waiting for Clockwork's picks (until 15:46) so the two books never buy the same name")
        return
    if not score_today():
        log("no scores for today — cannot enter (see SCORE_FAIL above)"); return
    ranked = [r for r in scores_for(today) if r["wild"]]
    avoid = other_book_symbols(today)
    room = TOP_N - already
    entered, picks = 0, []
    for r in ranked:
        sym = r["symbol"]
        if entered >= room:
            picks.append((sym, r["score"], "RANKED_BELOW_CUT")); break
        if sym in avoid:
            log(f"SKIP {sym} (rank {r['wild_rank']}) — held or being bought tonight by another book")
            picks.append((sym, r["score"], "OTHER_BOOK")); continue
        dte = days_to_earnings(sym)
        if dte is not None and 0 <= dte <= EARNINGS_BLACKOUT_DAYS:
            log(f"SKIP {sym} — earnings in {dte}d, not holding it through the print")
            picks.append((sym, r["score"], "EARNINGS_BLACKOUT")); continue
        price = bridge_quote(sym)
        if price is None or not (PRICE_LO <= float(price) <= PRICE_HI):
            picks.append((sym, r["score"], "NO_PRICE")); continue
        price = float(price)
        shares = max(1, int(PER_NAME / price))
        if MODE == "LIVE":
            ok, _fill, oid = place_paper_order(sym, shares, "BUY", order_type="MOC")
            if not ok:
                picks.append((sym, r["score"], "ORDER_FAILED")); continue
            record_entry(sym, price, shares, r["score"], r["wild_rank"], r["model_version"], oid, status="PENDING_ENTRY")
        else:
            record_entry(sym, price, shares, r["score"], r["wild_rank"], r["model_version"])
        entered += 1
        picks.append((sym, r["score"], "ENTERED"))
        log(f"ENTER {sym} x{shares} @ ~${price:.2f}  model rank {r['wild_rank']} score {r['score']:+.4f} (holds overnight)")
    record_scan({"wild_ranked": len(ranked), "avoided": sorted(avoid), "top_n": TOP_N,
                 "entered": entered, "mode": MODE}, picks)
    log(f"entry scan: {len(ranked)} WILD ranked → {entered} entered @ close")
    if entered:
        names = [p[0] for p in picks if p[2] == "ENTERED"]
        skipped = [f"{p[0]} ({p[2].lower()})" for p in picks if p[2] in ("OTHER_BOOK", "EARNINGS_BLACKOUT", "NO_PRICE", "ORDER_FAILED")]
        send_telegram(f"🦉 Night Owl {'MOC orders sent' if MODE == 'LIVE' else 'shadow entries'}: {', '.join(names)}"
                      + (f"\nskipped: {', '.join(skipped)}" if skipped else ""))


# ─────────────────────────── exit + fills (Clockwork's path, unchanged) ───────────────────────────
def exit_at_open():
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
            record_exit(t["id"], float(px), "Marked out at the open (SHADOW)")


def confirm_fills():
    """Clockwork's confirm_fills(), table renamed. Never books a price we did not get; a stale pending row
    is resolved by THIS order id's execution record, never by the shared net position."""
    n = now_et()
    if MODE != "LIVE":
        return
    today = n.date().isoformat()
    for t in rows_with_status("PENDING_ENTRY"):
        if t["entry_date"] < today:
            filled, px = order_fill(t["order_id"])
            if filled:
                set_status(t["id"], "OPEN", entry_price=px)
                log(f"STALE ENTRY adopted {t['symbol']} @ ${px:.2f} (filled, confirm was missed)")
                continue
            qty, _avg = real_position(t["symbol"])
            if qty is None:
                log(f"STALE ENTRY {t['symbol']} — fill unknown and portfolio check failed; leaving pending")
            elif qty == 0:
                _x("DELETE FROM night_owl_trades WHERE id=?", (t["id"],))
                log(f"STALE ENTRY never filled {t['symbol']} — no fill on order {t['order_id']} and the account "
                    f"is flat in it, row removed")
            else:
                log(f"STALE ENTRY {t['symbol']} — order {t['order_id']} shows no fill, but the account holds {qty} "
                    f"(another book's). NOT adopting it; leaving pending")
    for t in rows_with_status("PENDING_EXIT"):
        if t["entry_date"] < today and not (EXIT_CONFIRM_START <= n.time() <= EXIT_CONFIRM_END):
            filled, px = order_fill(t["order_id"])
            if filled:
                record_exit(t["id"], px, "Sold at the open (MOO, confirmed late)")
                log(f"STALE EXIT confirmed {t['symbol']} @ ${px:.2f}")
                continue
            log(f"STALE EXIT {t['symbol']} — order {t['order_id']} shows no fill yet; holding PENDING_EXIT "
                f"rather than re-sending a sell (never infer from the shared net position)")
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
                record_exit(t["id"], px, "Sold at the open (MOO)")
            elif n.time() >= EXIT_FALLBACK_AFTER:
                ok, fill, _ = place_paper_order(t["symbol"], t["shares"], "SELL")
                if ok and fill:
                    log(f"MOO did not fill {t['symbol']} — crossed with MARKET @ ${fill:.2f}")
                    record_exit(t["id"], fill, "MOO did not fill — crossed with MARKET")
                else:
                    log(f"fallback SELL not confirmed for {t['symbol']} — stays PENDING_EXIT")


# ─────────────────────────── dashboard close + main ───────────────────────────
def _manual_close(t):
    """Close ONE position on a dashboard instruction (identical to the other engines'). LIVE books the exit
    only if the SELL confirmed a fill; an unconfirmed sell leaves the row OPEN."""
    sym = t["symbol"]
    px = bridge_quote(sym)
    if isinstance(px, dict):
        px = px.get("last") or px.get("price") or px.get("close")
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
    _control.drain("night_owl", get_open, _manual_close, log, MODE)


LOCK_TTL = 600


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
    """Serve dashboard close requests and NOTHING else — pressing close must never open anything."""
    init_db()
    waited = 0.0
    while os.path.exists(LOCK) and (time.time() - os.path.getmtime(LOCK)) < LOCK_TTL:
        if waited >= 20:
            log("a scheduled pass is holding the lock — close request left pending for it")
            return
        time.sleep(1.0); waited += 1.0
    open(LOCK, "w").close()
    try:
        _drain_control_queue()
    finally:
        _release_lock()


def _touch_log():
    """Each pass is silent outside its windows, so the log's mtime would go a whole weekend without
    changing and the dashboard's services row (which reads "last ran" from it) would flag a healthy
    engine as stale. Touch it instead of writing a line every minute. A pass that never starts —
    the job unloaded, Python broken — stops touching it, which is exactly what that row should show."""
    try:
        path = "/Users/sushil/trading/logs/night_owl.log"
        if os.path.exists(path):
            os.utime(path, None)
    except Exception:
        pass


def run_once():
    _touch_log()
    init_db()
    _drain_control_queue()
    n = now_et()
    if not is_market_day(n.date()):
        return                                   # quiet on weekends/holidays (runs every minute)
    t = n.time()
    active = (EXIT_START <= t <= EXIT_CONFIRM_END) or (SCORE_START <= t <= ENTRY_END) or \
             (ENTRY_CONFIRM_START <= t <= ENTRY_CONFIRM_END) or rows_with_status("PENDING_ENTRY") or \
             rows_with_status("PENDING_EXIT")
    if not active:
        return                                   # nothing to do outside the windows — stay silent
    if not _take_lock():
        log("previous pass still active — skipping"); return
    try:
        confirm_fills()
        if (MODE == "LIVE" and EXIT_START <= t <= EXIT_END) or \
           (MODE != "LIVE" and EXIT_CONFIRM_START <= t <= EXIT_CONFIRM_END):
            exit_at_open()
        if SCORE_START <= t <= ENTRY_END:
            score_today()
        if ENTRY_START <= t <= ENTRY_END:
            scan_and_enter()
        if t.minute % 15 == 0 or ENTRY_START <= t <= ENTRY_CONFIRM_END or EXIT_START <= t <= EXIT_CONFIRM_END:
            log(f"pass complete — {len(get_open())} overnight position(s) held")
    finally:
        _release_lock()


def dryscan(day=None):
    """Print the ranking for a day without writing anything. A past day is scored from the cache (that
    day's opens from the cache); today refreshes the data first."""
    from factory import night_owl_model as M
    d = pd.Timestamp(day or now_et().date())
    cache = M.load_daily()
    if cache is None or not (cache["date"] == d).any():
        cache, fresh = M.refresh_daily(log=log)
        opens = fresh[fresh["date"] == d].set_index("symbol")["open"].dropna().to_dict()
    else:
        opens = cache[cache["date"] == d].set_index("symbol")["open"].to_dict()
    s = M.score_day(d, cache, opens)
    w = s[s.wild]
    print(f"\nNight Owl dry-scan {d.date()} — model {s.model_version.iloc[0]}, {len(s)} names scored, {len(w)} WILD")
    for i, r in enumerate(w.head(TOP_N + 5).itertuples()):
        print(f"  {i + 1:2}. {r.symbol:6} score {r.score:+.4f}  gap {r.gap * 100:+.2f}%" + ("  <-- WOULD ENTER" if i < TOP_N else ""))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--dryscan":
        dryscan(sys.argv[2] if len(sys.argv) > 2 else None)
    elif len(sys.argv) > 1 and sys.argv[1] == "--drain-only":
        drain_only()
    else:
        run_once()
