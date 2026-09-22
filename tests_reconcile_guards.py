"""
tests_reconcile_guards.py — Sep 22 2026

Pins the two guards added after the META oversell. Run after ANY change to
reconcile_with_ibkr() or the exit paths:

    venv/bin/python tests_reconcile_guards.py

The incident being pinned: META's stop fired 09:23 PRE-MARKET. The DB row was
written CLOSED before the order went out, so the still-real position looked like
an orphan. Reconcile fired six more closes across three attempts; all seven
landed at the 09:30 open, turning a 2-share long into a 12-share short.
"""
import sys, types, time
sys.path.insert(0, '/Users/sushil/trading')

import auto_trader as at

orders = []
fails = []


def check(name, cond, detail=''):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ''))
    if not cond:
        fails.append(name)


class _Resp:
    status_code = 200
    text = 'ok'
    def json(self): return {'status': 'submitted'}


def _fake_post(url, json=None, timeout=None, **kw):
    orders.append(json)
    return _Resp()


def setup(market_open, ibkr_positions, db_open=(), other=()):
    orders.clear()
    at._orphan_first_seen.clear()
    at._orphan_close_state.clear()
    at._orphan_closed_market_logged.clear()
    at._exit_in_flight.clear()
    at.is_market_open = lambda: market_open
    at.get_ibkr_positions = lambda: ibkr_positions
    at.get_open_trades = lambda: [{'symbol': s} for s in db_open]
    at._other_book_symbols = lambda: set(other)
    at.requests = types.SimpleNamespace(post=_fake_post, get=lambda *a, **k: _Resp())
    at.send_telegram = lambda *a, **k: None
    at.log = lambda *a, **k: None
    at.get_live_price = lambda s: 100.0


print("── GUARD 2: market closed ──")
setup(market_open=False, ibkr_positions={'META': {'qty': 2}})
at.reconcile_with_ibkr()
at._orphan_first_seen['META'] = time.time() - 999      # push past the grace window
at.reconcile_with_ibkr()
check("no orders placed pre-market on an unaccounted position", len(orders) == 0,
      f"{len(orders)} orders")
check("logged once, not per cycle", 'META' in at._orphan_closed_market_logged)

print("\n── the real orphan safety net still works during RTH ──")
setup(market_open=True, ibkr_positions={'ZZZ': {'qty': 5}})
at._orphan_first_seen['ZZZ'] = time.time() - 999
at.reconcile_with_ibkr()
check("genuine orphan IS closed during market hours", len(orders) >= 1,
      f"{len(orders)} orders, first={orders[0] if orders else None}")
check("EXACTLY ONE order per cycle (was market+limit = 2)", len(orders) == 1,
      f"{len(orders)} orders")

print("\n── GUARD 1: a strategy exit is already working ──")
setup(market_open=True, ibkr_positions={'META': {'qty': 2}})
at._orphan_first_seen['META'] = time.time() - 999
at._mark_exit_in_flight('META')
at.reconcile_with_ibkr()
check("reconcile does NOT race a working exit order", len(orders) == 0, f"{len(orders)} orders")

print("\n── the guard ages out during RTH so a truly stuck order is still rescued ──")
setup(market_open=True, ibkr_positions={'META': {'qty': 2}})
at._orphan_first_seen['META'] = time.time() - 999
at._exit_in_flight['META'] = time.time() - (at.EXIT_IN_FLIGHT_S + 60)
at.reconcile_with_ibkr()
check("stale in-flight flag stops protecting after EXIT_IN_FLIGHT_S", len(orders) == 1,
      f"{len(orders)} orders")

print("\n── the guard NEVER ages out while the market is closed ──")
setup(market_open=False, ibkr_positions={'META': {'qty': 2}})
at._exit_in_flight['META'] = time.time() - 99999
check("a resting order is still 'in flight' out of hours", at._exit_in_flight_active('META'))

print("\n── flat position clears the flag ──")
setup(market_open=True, ibkr_positions={'META': {'qty': 0}})
at._mark_exit_in_flight('META')
at.reconcile_with_ibkr()
check("in-flight flag cleared once IBKR reports flat", 'META' not in at._exit_in_flight)

print("\n── a position owned by another book is never touched ──")
setup(market_open=True, ibkr_positions={'VICR': {'qty': 17}}, other=('VICR',))
at._orphan_first_seen['VICR'] = time.time() - 999
at.reconcile_with_ibkr()
check("Wave Rider / Contrarian / Clockwork holdings are left alone", len(orders) == 0)

print("\n── full META replay: pre-market stop, then the open ──")
setup(market_open=False, ibkr_positions={'META': {'qty': 2}})
at._mark_exit_in_flight('META')                      # strategy submits its sell 09:23
for _ in range(12):                                  # 09:23 -> 09:29, reconcile every 30s
    at._orphan_first_seen['META'] = time.time() - 999
    at.reconcile_with_ibkr()
pre = len(orders)
at.is_market_open = lambda: True                     # 09:30 — the sell fills
at.get_ibkr_positions = lambda: {'META': {'qty': 0}}
at.reconcile_with_ibkr()
check("ZERO extra sell orders across the whole pre-market window", pre == 0, f"{pre} orders")
check("no oversell: position ends flat, flag cleared", 'META' not in at._exit_in_flight)
print(f"    (before the fix this produced 6 extra orders and a -12 share short)")

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}  ({len(fails)} failed)")
sys.exit(1 if fails else 0)
