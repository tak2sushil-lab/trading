#!/usr/bin/env python3
"""Compare the commission we MODEL against the commission IBKR actually charged.

    venv/bin/python commission_check.py

Every P&L in this system is recomputed from the price difference and then has a modelled
commission subtracted — `database.equity_commission()` for stock, `strategy_core.COMMISSION`
for futures. Nothing reads the broker's own figure, so nothing is double-counted, but until
Sep 7 2026 nothing verified the model either. This closes that loop.

IBKR serves executions for the CURRENT trading session only, so run it intraday or shortly
after the close. On a day with no fills it will correctly say so rather than invent a number.

A commission of None means IBKR has not yet delivered the commissionReport for that fill (it
arrives a moment after the execution) — that is NOT the same as zero, and rows without it are
excluded from the averages rather than counted as free.
"""
import sys, collections
import requests

sys.path.insert(0, "/Users/sushil/trading")
sys.path.insert(0, "/Users/sushil/trading/futures")

BRIDGES = {"IBKR": "http://localhost:8000", "TC": "http://localhost:8002"}


def model_equity(shares: float) -> float:
    from database import equity_commission
    return equity_commission(shares, round_trip=False)      # one leg — a fill is one leg


def model_futures(contracts: float, account: str) -> float:
    import json, os
    spec = json.load(open("/Users/sushil/trading/futures/instruments/MNQ.json"))
    rt = spec.get("commission_rt_tc", spec["commission_rt"]) if account == "TC" else spec["commission_rt"]
    return rt / 2.0 * contracts                             # half a round turn per fill


def main():
    any_fills = False
    for acct, url in BRIDGES.items():
        print(f"\n{'='*66}\n{acct}  ({url})\n{'='*66}")
        try:
            r = requests.get(f"{url}/executions", params={"days": 1}, timeout=15)
            data = r.json()
        except Exception as e:
            print(f"  bridge unreachable: {type(e).__name__}: {e}")
            continue
        if data.get("error"):
            print(f"  {data['error']}")
            continue
        fills = data.get("fills", [])
        if not fills:
            print("  no fills this session (IBKR only serves the current trading day)")
            continue
        any_fills = True
        buckets = collections.defaultdict(list)
        missing = 0
        for f in fills:
            if f["commission"] is None:
                missing += 1
                continue
            buckets[(f["secType"], f["symbol"])].append(f)

        if missing:
            print(f"  ⚠️  {missing} fill(s) have no commissionReport yet — excluded, not counted as $0\n")

        print(f"  {'instrument':16s} {'fills':>5} {'size':>8} {'IBKR actual':>12} {'our model':>11} {'diff':>10}")
        for (sec, sym), rows in sorted(buckets.items()):
            qty = sum(x["shares"] for x in rows)
            actual = sum(x["commission"] for x in rows)
            model = (sum(model_futures(x["shares"], acct) for x in rows) if sec == "FUT"
                     else sum(model_equity(x["shares"]) for x in rows))
            diff = actual - model
            flag = "  <-- MODEL IS OFF" if model and abs(diff) > max(0.05, 0.10 * model) else ""
            print(f"  {sec+' '+sym:16s} {len(rows):>5} {qty:>8.0f} "
                  f"{actual:>12.2f} {model:>11.2f} {diff:>+10.2f}{flag}")
            if sec == "FUT" and qty:
                print(f"  {'':16s} {'':>5} {'':>8} per-contract round turn: "
                      f"IBKR ${actual/qty*2:.4f}  vs  model ${model/qty*2:.4f}")

    if not any_fills:
        print("\nNothing to compare yet. Run this after a session with real fills —"
              "\nthe first one will be the first time these constants are checked against reality.")


if __name__ == "__main__":
    main()
