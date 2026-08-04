"""collect_chain_snapshots.py — daily option-chain snapshot collector.

Aug 3 2026. Wires the storage the Aug 3 ideation doc flagged as missing:
new-strike/new-expiry detection and (eventually) IV-rank-from-own-history
both need a dated history of the chain, which nothing wrote before this.

v1 scope, deliberately narrow: snapshots the HELD expiry's full chain (both
rights, every listed strike) for every symbol with an OPEN options_trades
row. Not a broader watchlist yet — that's a real cost/API-load decision to
make once this proves useful, not a default to reach for now.

Idempotent: re-running the same day is a no-op (UNIQUE constraint, INSERT OR
IGNORE). Safe to re-run after a partial failure.

Schedule: EOD daily via launchd (com.sushil.trading.collect_chain_snapshots),
matching collect_bars.py's pattern. Skips weekends/holidays.

Command: venv/bin/python options/collect_chain_snapshots.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_open_options_trades, save_chain_snapshot, init_db

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from options_trader import (
    _yf_option_chain, get_underlying_price,
    US_HOLIDAYS_2026 as _HOLIDAYS,
)


def _rows_from_df(df) -> list[dict]:
    out = []
    for _, r in df.iterrows():
        try:
            out.append({
                'strike': float(r['strike']),
                'bid':    float(r['bid'])  if r.get('bid')  == r.get('bid')  else None,
                'ask':    float(r['ask'])  if r.get('ask')  == r.get('ask')  else None,
                'last':   float(r['lastPrice']) if r.get('lastPrice') == r.get('lastPrice') else None,
                'iv':     float(r['impliedVolatility']) if r.get('impliedVolatility') == r.get('impliedVolatility') else None,
                'volume': int(r['volume'])        if r.get('volume')        == r.get('volume')        else None,
                'oi':     int(r['openInterest'])  if r.get('openInterest')  == r.get('openInterest')  else None,
            })
        except Exception:
            continue
    return out


def main():
    today = date.today()
    if today.weekday() >= 5 or today in _HOLIDAYS:
        print("[collect_chain_snapshots] weekend/holiday — skip")
        return

    init_db()
    trades = get_open_options_trades()
    if not trades:
        print("[collect_chain_snapshots] no open options positions — nothing to snapshot")
        return

    seen = set()   # (symbol, expiry) de-dup across trades sharing a name+expiry
    snap_date = today.isoformat()
    total_rows = 0
    for t in trades:
        sym    = t['symbol']
        expiry = t.get('expiry')
        if not expiry or (sym, expiry) in seen:
            continue
        seen.add((sym, expiry))

        underlying = get_underlying_price(sym)
        for right in ('C', 'P'):
            try:
                df = _yf_option_chain(sym, expiry, right)
                rows = _rows_from_df(df)
                n = save_chain_snapshot(snap_date, sym, underlying, expiry, right, rows)
                total_rows += n
                print(f"[collect_chain_snapshots] {sym} {expiry} {right}: {len(rows)} strikes, {n} new rows")
            except Exception as e:
                print(f"[collect_chain_snapshots] {sym} {expiry} {right} error: {e}")

    print(f"[collect_chain_snapshots] done — {total_rows} new rows across {len(seen)} symbol/expiry pairs")


if __name__ == '__main__':
    main()
