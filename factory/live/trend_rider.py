"""
Trend Rider — LIVE PAPER trader for the multi-day Trend-Follower (design C).

The first futures engine to pass a walk-forward OOS test (see futures/factory/trend.py +
[[futures-alpha-factory-aug16]]). MNQ is long-biased by DNA and its daily noise is 200-700pts,
so tight intraday stops get shaken out — this rides the multi-day STRUCTURE instead of predicting,
holding through the noise and exiting only when the trend flips.

Design C (all-weather, 1 contract, 2021-2026: +$26k, Sharpe 0.67, MaxDD −$6.3k, green every year):
    LONG   when daily close > MA50
    SHORT  when daily close < MA200      (deep-bear defense only — e.g. 2022)
    FLAT   otherwise
Exit is the SIGNAL FLIP (MA cross) — no fixed stop (that's the whole point; the natural breadth
is ~650pts / 2.5×ATR). This is an IBKR-personal engine (multi-day hold; NOT TopStep).

MODE:
  SHADOW (default) — records what it WOULD trade in trend_trades (no orders). Flip after a
                     forward window proves the edge holds live.
  LIVE   — places the order on the IBKR *paper* account via the bridge (order path = TODO,
           mirrors wave_rider.py when we get there).

Runs ONCE per day in the evening (after the daily bar completes), backtest-faithful: the signal
is computed from COMPLETED daily closes and the position is held into the next session.

Run manually:  venv/bin/python -m factory.live.trend_rider
"""
from __future__ import annotations
import os
import sqlite3
import datetime as dt

import numpy as np
import pandas as pd
import pytz

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "trades.db")
ET = pytz.timezone("America/New_York")

MODE      = os.environ.get("TREND_RIDER_MODE", "SHADOW").upper()   # SHADOW | LIVE
CONTRACTS = int(os.environ.get("TREND_RIDER_CONTRACTS", "1"))
MA_FAST   = 50
MA_SLOW   = 200
DPP       = 2.0                     # MNQ $/pt per contract
SYMBOL    = "MNQ"


def log(msg: str):
    line = f"[{dt.datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S')}] [TREND RIDER {MODE}] {msg}"
    print(line)
    with open(os.path.join(ROOT, "logs", "trend_rider.log"), "a") as f:
        f.write(line + "\n")


def init_db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS trend_trades(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_date TEXT, entry_price REAL, side TEXT, contracts INTEGER,
        ma_fast REAL, ma_slow REAL, atr REAL, breadth_pts REAL,
        exit_date TEXT, exit_price REAL, pnl REAL, status TEXT DEFAULT 'OPEN',
        mode TEXT, note TEXT)""")
    c.commit(); c.close()


def load_daily() -> pd.DataFrame:
    dbp = os.path.join(ROOT, "market_data.db")
    if not os.path.exists(dbp):
        dbp = DB
    con = sqlite3.connect(dbp)
    d = pd.read_sql_query(
        f"SELECT ts_utc,high,low,close FROM futures_bars_1d WHERE symbol='{SYMBOL}' ORDER BY ts_utc",
        con)
    con.close()
    d["date"] = pd.to_datetime(d["ts_utc"], utc=True, format="ISO8601").dt.date
    d["ma_fast"] = d["close"].rolling(MA_FAST).mean()
    d["ma_slow"] = d["close"].rolling(MA_SLOW).mean()
    tr = pd.concat([d["high"] - d["low"], (d["high"] - d["close"].shift()).abs(),
                    (d["low"] - d["close"].shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()
    return d.dropna(subset=["ma_slow"]).reset_index(drop=True)


def signal_now(d: pd.DataFrame) -> dict:
    r = d.iloc[-1]
    if r["close"] > r["ma_fast"]:
        side = "LONG"
    elif r["close"] < r["ma_slow"]:
        side = "SHORT"
    else:
        side = "FLAT"
    return dict(side=side, date=str(r["date"]), close=float(r["close"]),
                ma_fast=float(r["ma_fast"]), ma_slow=float(r["ma_slow"]), atr=float(r["atr"]))


def open_position():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    row = c.execute("SELECT * FROM trend_trades WHERE status='OPEN' ORDER BY id DESC LIMIT 1").fetchone()
    c.close()
    return dict(row) if row else None


def record_entry(sig: dict):
    breadth = 2.5 * sig["atr"]
    c = sqlite3.connect(DB)
    c.execute("""INSERT INTO trend_trades(entry_date,entry_price,side,contracts,ma_fast,ma_slow,
        atr,breadth_pts,status,mode,note) VALUES(?,?,?,?,?,?,?,?,'OPEN',?,?)""",
        (sig["date"], sig["close"], sig["side"], CONTRACTS, sig["ma_fast"], sig["ma_slow"],
         sig["atr"], breadth, MODE, f"MA{MA_FAST}/{MA_SLOW} signal={sig['side']}"))
    c.commit(); c.close()
    log(f"ENTER {sig['side']} x{CONTRACTS} @ {sig['close']:.0f}  (MA50 {sig['ma_fast']:.0f} / "
        f"MA200 {sig['ma_slow']:.0f}, breadth ~{breadth:.0f}pts)")


def record_exit(pos: dict, sig: dict, reason: str):
    sgn = 1 if pos["side"] == "LONG" else -1
    pnl = sgn * (sig["close"] - pos["entry_price"]) * pos["contracts"] * DPP
    c = sqlite3.connect(DB)
    c.execute("UPDATE trend_trades SET status='CLOSED',exit_date=?,exit_price=?,pnl=?,note=? WHERE id=?",
              (sig["date"], sig["close"], pnl, reason, pos["id"]))
    c.commit(); c.close()
    log(f"EXIT {pos['side']} @ {sig['close']:.0f}  pnl ${pnl:+.0f}  ({reason})")


def run():
    init_db()
    if dt.datetime.now(ET).weekday() >= 5:
        log("weekend — no action"); return
    d = load_daily()
    sig = signal_now(d)
    pos = open_position()
    cur = pos["side"] if pos else "FLAT"
    log(f"signal={sig['side']} (close {sig['close']:.0f}, MA50 {sig['ma_fast']:.0f}, "
        f"MA200 {sig['ma_slow']:.0f}) | current position={cur}")
    if sig["side"] == cur:
        log("no change — holding"); return
    if pos:                                   # trend flipped → exit current
        record_exit(pos, sig, reason=f"signal flip {cur}->{sig['side']}")
    if sig["side"] != "FLAT":                 # enter new direction
        if MODE == "LIVE":
            log("LIVE order path not yet implemented — logging SHADOW-style only")
        record_entry(sig)


if __name__ == "__main__":
    run()
