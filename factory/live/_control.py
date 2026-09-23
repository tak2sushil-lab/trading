"""Dashboard close requests for the one-shot factory engines (Wave Rider, Contrarian, Clockwork).

WHY THESE THREE ARE DIFFERENT. auto_trader, options_trader and the futures traders are
long-running daemons that poll a queue on their own schedule. These three are not — launchd
runs each as a fresh one-shot every five minutes and they keep no state between runs. Waiting
up to five minutes for a close button is not acceptable, so the dashboard queues the request
and then immediately spawns the engine's own one-shot, which drains the queue as its first
action. The engine still does all the work with its own code, in its own environment, under
its own lock file; the dashboard only starts it early.

WHY THE DRAIN RUNS BEFORE THE MARKET-HOURS GUARD. run_once() returns early outside 09:30-16:00
and on non-trading days. A close request arriving then must still be ANSWERED — reporting
"market closed, nothing sent" is a real answer. Leaving it PENDING would let it fire at the
next open, hours later, against a book the user has since dealt with.

Shared rather than copied into all three engines on purpose: the two NY futures traders are
duplicated code and had silently accrued three behavioural divergences by the time anyone
AST-diffed them (Sep 2 2026). One copy, three callers.
"""
from __future__ import annotations


def drain(target: str, get_open, close_one, log, mode: str = 'LIVE'):
    """Claim and execute this engine's pending close requests.

    close_one(trade) -> (ok: bool, message: str)   — the engine's own close path.
    Anything raised inside it is recorded as a FAILED request, never swallowed.
    """
    try:
        from database import claim_control_requests, complete_control_request
        reqs = claim_control_requests(target)
    except Exception as e:
        log(f"[control] claim failed: {e}")
        return
    for r in reqs:
        try:
            rows = get_open()
            if r['action'] == 'CLOSE_ONE':
                rows = [t for t in rows
                        if t['symbol'] == (r['symbol'] or '')
                        and (r['trade_id'] is None or t['id'] == r['trade_id'])]
            log(f"[control] #{r['id']} {r['action']} "
                f"{r['symbol'] or 'ALL'} — {len(rows)} matching position(s)")
            if not rows:
                complete_control_request(r['id'], 'DONE', 'no matching open position')
                continue
            msgs, failed = [], 0
            for t in rows:
                try:
                    ok, msg = close_one(t)
                except Exception as e:
                    ok, msg = False, f"{t['symbol']}: {e}"
                msgs.append(('' if ok else '! ') + msg)
                failed += 0 if ok else 1
            outcome = f"[{mode}] " + ' | '.join(msgs)
            complete_control_request(r['id'], 'FAILED' if failed else 'DONE', outcome)
            log(f"[control] #{r['id']} -> {'FAILED' if failed else 'DONE'}: {outcome}")
        except Exception as e:
            log(f"[control] request {r['id']} failed: {e}")
            try:
                from database import complete_control_request as _c
                _c(r['id'], 'FAILED', str(e))
            except Exception:
                pass
