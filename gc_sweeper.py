"""Bound how long a leaked SQLite connection can hold its lock (Oct 1 2026).

In Python 3.11 an sqlite3.Connection is part of a reference cycle (it owns a statement
cache that refers back to it), so a connection that is never close()d is NOT freed when
the code drops it — only the cyclic garbage collector frees it, and in a long-running
process a full collection can be hours away. If that connection was mid-write when its
commit failed ("database is locked"), it keeps the write lock the whole time and every
other process's reads and writes on trades.db fail.

That is exactly what happened on Oct 1 2026: options_trader held a half-finished write from
17:04 to 18:10 (65 min); auto_trader, both futures traders, the dashboard and the evening
jobs all failed with "database is locked", and nothing alerted.

The real fix is to close every connection in a `finally` (about 130 call sites — open
follow-up). This module is the safety net until then: a daemon thread runs a full
gc.collect() every INTERVAL_SEC, which frees leaked connections and releases their locks
within about a minute. gc.collect() only frees objects nothing can reach any more, so it
cannot change what a service decides or does.

Usage (once, at service startup):
    import gc_sweeper; gc_sweeper.install()
"""
import gc
import threading
import time

INTERVAL_SEC = 60
_started = False


def _loop(interval: float) -> None:
    while True:
        time.sleep(interval)
        try:
            gc.collect()
        except Exception:
            pass


def install(interval: float = INTERVAL_SEC) -> None:
    """Start the sweeper thread once per process (later calls are no-ops)."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, args=(interval,), name='gc-sweeper', daemon=True).start()
