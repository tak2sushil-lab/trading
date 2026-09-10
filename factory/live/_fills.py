"""
Verified order placement — shared by every live equity engine (Wave Rider, Contrarian, Clockwork).

WHY THIS MODULE EXISTS. The same defect has now been found FOUR separate times in four
different files in this codebase:

  Jul 20 2026  options/watchman.py   — retry loop never read the cancel result -> 15 phantom shorts
  Sep  3 2026  futures/*_trader.py   — DB stored the scan price, not the fill price
  Sep  9 2026  auto_trader.py        — reconcile "verified" a close against a hardcoded status
  Sep 10 2026  factory/live/*.py     — exits re-sent every pass -> real positions at -396/-492 shares

It is always the same mistake: treating the order-status endpoint as ground truth.

  * bridge.py's `POST /order` hardcodes `"status": "submitted"` regardless of outcome, so that
    field can NEVER signal success or failure.
  * `GET /order/{id}/status` LAGS — a genuinely filled order routinely still reports
    PendingSubmit with filled=0.0 and avgFillPrice=0.0 (verified live, Sep 3 and Sep 10).

Believing it is harmful in BOTH directions, asymmetrically:
  - on a BUY  it silently discards real fills   -> engine thinks it never entered
  - on a SELL it re-sends the same exit order   -> OVERSELLS, position goes short and compounds

The Sep 10 incident is the sell case: Clockwork's stale-exit handler read "not filled", returned
the row to OPEN, and the next pass submitted another MOO SELL. Each pass sold again.

GROUND TRUTH, in order of preference:
  1. the position DELTA measured across the order — unambiguous for buys AND sells
  2. the real execution record from /executions   — the only source of a true fill PRICE
Never the status field on its own.
"""
from __future__ import annotations

import time

import requests


def position_qty(bridge: str, sym: str, log=None):
    """Signed share count IBKR actually reports, 0 if flat, or None if the check itself
    failed. None must NEVER be collapsed into 0 — 'I could not look' is not 'we hold nothing'."""
    try:
        for p in requests.get(f"{bridge}/portfolio", timeout=10).json():
            if p.get("symbol") == sym:
                return int(float(p.get("qty", 0) or 0))
        return 0
    except Exception as e:
        if log:
            log(f"portfolio check failed for {sym}: {e}")
        return None


def last_fill_price(bridge: str, sym: str, side_code: str, log=None):
    """Most recent real execution price for this symbol/side ('BOT' or 'SLD') in the current
    session. This is the only place a TRUE fill price can come from when the order-status
    endpoint is lagging — avgCost from the portfolio is the position's basis, not an exit
    price, and using it to close a trade books a fabricated P&L."""
    try:
        d = requests.get(f"{bridge}/executions?days=1", timeout=10).json()
        fills = d.get("fills", d) if isinstance(d, dict) else d
        best = None
        for f in fills:
            if f.get("symbol") != sym or f.get("side") != side_code:
                continue
            if best is None or str(f.get("time", "")) > str(best.get("time", "")):
                best = f
        if best and best.get("price"):
            return float(best["price"])
    except Exception as e:
        if log:
            log(f"execution lookup failed for {sym}: {e}")
    return None


def place_verified(bridge: str, sym: str, shares: int, side: str,
                   order_type: str = "MARKET", log=None, polls: int = 4, wait: float = 2.0):
    """Place an order and confirm it against reality. Returns (ok, fill_price, order_id).

    For MOC/MOO the auction has not run yet, so `ok` only means the broker ACCEPTED the order
    and fill_price is 0.0 — the caller confirms the fill later (see confirm_fills()).

    For MARKET/LIMIT: poll the status endpoint briefly, and if it is still ambiguous, fall back
    to the position delta measured across the order. A delta proves the order did something,
    in either direction, without ever trusting the status field.
    """
    _log = log or (lambda *_a, **_k: None)
    side_u = side.upper()
    before = position_qty(bridge, sym, _log)      # snapshot BEFORE, so a delta is measurable
    try:
        r = requests.post(f"{bridge}/order",
                          json={"symbol": sym, "qty": shares, "side": side_u,
                                "order_type": order_type}, timeout=10)
        if r.status_code != 200 or not r.text.strip():
            _log(f"order rejected {sym}: {r.status_code}")
            return False, 0.0, None
        oid = r.json().get("orderId")
        if not oid:
            return False, 0.0, None
        if order_type in ("MOC", "MOO"):
            return True, 0.0, str(oid)

        for _ in range(polls):
            time.sleep(wait)
            try:
                d = requests.get(f"{bridge}/order/{oid}/status", timeout=5).json()
            except Exception:
                continue
            if d.get("status") == "Filled" and d.get("avgFillPrice"):
                return True, float(d["avgFillPrice"]), str(oid)

        # Status stayed ambiguous. Decide on the position delta, never on the status field.
        after = position_qty(bridge, sym, _log)
        if before is None or after is None:
            _log(f"{sym}: status ambiguous AND portfolio unreadable — reporting NOT filled "
                 f"(safe default: caller retries rather than assuming)")
            return False, 0.0, str(oid)
        if after != before:
            px = last_fill_price(bridge, sym, "BOT" if side_u == "BUY" else "SLD", _log)
            _log(f"{sym}: order status lagged, position moved {before} -> {after} "
                 f"({side_u} {shares}) — treating as FILLED"
                 + (f" @ ${px:.2f}" if px else " (no execution price available)"))
            return (True, px, str(oid)) if px else (False, 0.0, str(oid))
        _log(f"{sym}: {side_u} {shares} did not move the position ({before}) — NOT filled")
        return False, 0.0, str(oid)
    except Exception as e:
        _log(f"order error {sym}: {e}")
        return False, 0.0, None
