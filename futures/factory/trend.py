"""
Multi-day Trend-Follower — the first futures engine to PASS a walk-forward OOS test.

Doctrine / why this exists (Aug 17 2026 research, see [[futures-alpha-factory-aug16]]):
  Intraday DIRECTION is efficient — proven dead every way (rules, decoder features, ML AUC
  0.48 OOS). The edge on MNQ is STRUCTURAL, not predictive:
    • MNQ is long-biased by DNA (it's a growth index).
    • Its daily NOISE is 200-700pts (median 286) — a tight intraday stop is inside the noise
      76% of days, so it gets shaken out (that's what killed the intraday book, e.g. Aug 17).
  Trend-following REACTS to the established multi-day structure instead of predicting, holds
  through the daily noise, and exits only when the trend itself flips (MA cross). Validated
  2021-2026: long-only Sharpe ~0.77-0.85 across MA 50-150 (robust plateau); walk-forward
  MA150 IS Sharpe 0.80 -> OOS 0.84. NOT for TopStep (multi-day hold; TopStep is flat-by-3:10)
  — this is an IBKR-personal-account engine.

Signal:  long when daily close > MA(N); optionally short when below (long/short). Long-only has
  the best Sharpe (index drift makes the short side a drag except in sustained bears like 2022);
  long/short is more all-weather but lower Sharpe. Default long-only, MA=50.

Run:  venv/bin/python -m futures.factory.trend --ma 50
      venv/bin/python -m futures.factory.trend --ma 150 --long-short --walk-forward
"""
from __future__ import annotations
import argparse
import os
import sqlite3

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOLLARS_PER_PT = 2.0   # MNQ, per contract


def load_daily(start="2021-01-01") -> pd.DataFrame:
    dbp = os.path.join(ROOT, "market_data.db")
    if not os.path.exists(dbp):
        dbp = os.path.join(ROOT, "trades.db")
    con = sqlite3.connect(dbp)
    d = pd.read_sql_query(
        "SELECT ts_utc,open,high,low,close FROM futures_bars_1d WHERE symbol='MNQ' ORDER BY ts_utc",
        con)
    con.close()
    d["date"] = pd.to_datetime(d["ts_utc"], utc=True, format="ISO8601").dt.date
    d = d[pd.to_datetime(d["date"]) >= start].reset_index(drop=True)
    d["ret"] = d["close"].diff()
    d["year"] = pd.to_datetime(d["date"]).dt.year
    return d


def signal(d: pd.DataFrame, ma_n: int, long_short: bool) -> pd.Series:
    ma = d["close"].rolling(ma_n).mean()
    if long_short:
        return np.sign(d["close"] - ma)          # +1 long / -1 short
    return (d["close"] > ma).astype(float)       # +1 long / 0 flat


def backtest(d: pd.DataFrame, pos: pd.Series, contracts: int = 2) -> dict:
    dpp = DOLLARS_PER_PT * contracts
    pnl = (pos.shift(1) * d["ret"] * dpp).fillna(0)   # shift(1): decide at close, hold next day
    sharpe = pnl.mean() / pnl.std() * np.sqrt(252) if pnl.std() > 0 else 0.0
    cum = pnl.cumsum()
    dd = float((cum - cum.cummax()).min())
    per_year = pnl.groupby(d["year"]).sum()
    return dict(total=float(pnl.sum()), sharpe=float(sharpe), maxdd=dd,
                per_year=per_year, pnl=pnl)


def report(d, ma_n, long_short, contracts):
    pos = signal(d, ma_n, long_short)
    r = backtest(d, pos, contracts)
    tag = "long/short" if long_short else "long-only"
    print(f"\n  Trend-Follower  MA{ma_n} {tag}  ({contracts} contracts, 2021-2026)")
    print(f"    Total ${r['total']:,.0f}   Sharpe {r['sharpe']:.2f}   MaxDD ${r['maxdd']:,.0f}")
    print("    per-year: " + "  ".join(f"{y}:${v:+,.0f}" for y, v in r["per_year"].items()))
    # current state
    ma = d["close"].rolling(ma_n).mean()
    up = d["close"].iloc[-1] > ma.iloc[-1]
    print(f"    CURRENT: close {d['close'].iloc[-1]:.0f} vs MA{ma_n} {ma.iloc[-1]:.0f} "
          f"-> {'LONG (hold)' if up else 'FLAT/SHORT'}")
    return r


def walk_forward(d, long_short, contracts):
    print("\n  WALK-FORWARD (pick best MA on 2021-2023, test OOS on 2024-2026):")
    train = d[d["year"] <= 2023].index
    test = d[d["year"] >= 2024].index
    best = None
    for n in [20, 30, 50, 75, 100, 125, 150]:
        r = backtest(d, signal(d, n, long_short), contracts)
        p = r["pnl"]
        s = p[train].mean() / p[train].std() * np.sqrt(252) if p[train].std() > 0 else 0
        if best is None or s > best[1]:
            best = (n, s)
    n = best[0]
    r = backtest(d, signal(d, n, long_short), contracts)
    p = r["pnl"]
    s_oos = p[test].mean() / p[test].std() * np.sqrt(252) if p[test].std() > 0 else 0
    print(f"    best MA in-sample = MA{n} (Sharpe {best[1]:.2f})  ->  OOS Sharpe {s_oos:.2f}, "
          f"total ${p[test].sum():,.0f}   {'PASS ✓' if s_oos > 0.4 else 'FAIL'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ma", type=int, default=50)
    ap.add_argument("--long-short", action="store_true")
    ap.add_argument("--contracts", type=int, default=2)
    ap.add_argument("--walk-forward", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()

    d = load_daily()
    if args.sweep:
        print("  MA sweep (Sharpe):  MA | long-only | long/short")
        for n in [20, 30, 50, 75, 100, 125, 150]:
            lo = backtest(d, signal(d, n, False), args.contracts)["sharpe"]
            ls = backtest(d, signal(d, n, True), args.contracts)["sharpe"]
            print(f"    {n:>4} |  {lo:>5.2f}   |   {ls:>5.2f}")
        return
    report(d, args.ma, args.long_short, args.contracts)
    if args.walk_forward:
        walk_forward(d, args.long_short, args.contracts)


if __name__ == "__main__":
    main()
