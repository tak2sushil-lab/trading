"""yfinance cache fix: stop the per-thread SQLite handle leak.

yfinance 1.x keeps three small caches (ticker -> timezone, the Yahoo cookie,
ISIN -> ticker) in peewee SqliteDatabase objects that use thread-local
connections. Every new thread that touches a cache opens its own connection
(plus -wal/-shm handles, since the files are in WAL mode) and the handles are
not released when the thread exits. yf.download(threads=True) starts fresh
threads on every call, so a long-running process leaks file descriptors until
it reaches the per-process limit (launchd soft limit: 256). After that every
file or socket open fails: "unable to open database file",
"[Errno 24] Too many open files".

Measured Sep 30 2026: auto_trader (running since Sep 22) held 179 cache
handles; that day 75 of its scans failed, 3 exits failed (XRPN sat 8 minutes
past its stop) and the bridge could not be reached. bridge.py held 58 cache
handles after 5 days.

install() replaces the three caches with in-memory, lock-protected dicts. The
timezone map is seeded once from the on-disk cache (one connection, closed at
once), so no extra Yahoo requests are made; the cookie is fetched once per
process and kept in memory. Call it right after `import yfinance`.
"""
import datetime as _dt
import os
import sqlite3
import threading

_installed = False


class _MemKV:
    """Timezone / ISIN cache: same lookup/store contract as yfinance's."""

    def __init__(self, seed=None):
        self._d = dict(seed or {})
        self._lock = threading.Lock()

    def lookup(self, key):
        with self._lock:
            return self._d.get(key)

    def store(self, key, value):
        with self._lock:
            if value is None:
                self._d.pop(key, None)
            else:
                self._d[key] = value

    @property
    def tz_db(self):
        return None


class _MemCookie:
    """Cookie cache: lookup returns {'cookie': ..., 'age': timedelta} like yfinance's."""

    def __init__(self):
        self._d = {}
        self._lock = threading.Lock()

    def lookup(self, strategy):
        with self._lock:
            hit = self._d.get(strategy)
        if hit is None:
            return None
        cookie, fetched = hit
        return {'cookie': cookie, 'age': _dt.datetime.now() - fetched}

    def store(self, strategy, cookie):
        with self._lock:
            if cookie is None:
                self._d.pop(strategy, None)
            else:
                self._d[strategy] = (cookie, _dt.datetime.now())


def _seed_timezones(cache_mod):
    """Read the on-disk ticker->timezone map once. Any failure -> empty seed
    (yfinance then fetches each ticker's timezone once per process)."""
    try:
        path = os.path.join(cache_mod._TzDBManager.get_location(), 'tkr-tz.db')
        if not os.path.isfile(path):
            return {}
        con = sqlite3.connect(path, timeout=2)
        try:
            rows = con.execute('SELECT key, value FROM _tz_kv').fetchall()
        finally:
            con.close()
        return {k: v for k, v in rows if k and v}
    except Exception:
        return {}


def install():
    """Swap yfinance's SQLite-backed caches for in-memory ones. Idempotent."""
    global _installed
    if _installed:
        return
    from yfinance import cache as c
    c._TzCacheManager._tz_cache = _MemKV(_seed_timezones(c))
    c._CookieCacheManager._Cookie_cache = _MemCookie()
    c._ISINCacheManager._isin_cache = _MemKV()
    for mgr in (c._TzDBManager, c._CookieDBManager, c._ISINDBManager):
        try:
            mgr.close_db()
        except Exception:
            pass
    _installed = True
