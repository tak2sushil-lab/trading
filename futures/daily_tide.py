"""Daily Tide — the one shared implementation (Sep 29 2026).

LONG only when the PREVIOUS session's close is above its PREVIOUS 200-day MA,
SHORT only when below. Used by futures_trader.py, tc_trader.py and sim_replay.py
so live and sim can never read different closes again.

WHY THIS MODULE EXISTS — the traders used to read `futures_bars_1d` (yfinance) directly.
That table is written nightly at 21:30 ET with INSERT OR IGNORE, and at that hour yfinance's
newest daily bar is the NEXT session, 3.5 hours old, labelled with the date it started.
The half-built bar was frozen permanently: 63 of 2026's 184 rows were evening snapshots, not
closes (Sep 28's stored "close" 30,442 was the 21:25 price; the real close was ~30,566).
It flipped the Tide's decision on one day in 5.3 years (2026-03-20) — small so far, but MNQ
came within 0.03% of its MA in 2026, where a 100pt error decides the side.

THE SERIES — a session's close is the close of its 15:55 ET 5-minute bar (the 4pm RTH close)
from `futures_bars_5m`, our own Databento/IBKR feed. Dates with no usable RTH bars fall back
to the stored `futures_bars_1d` close (clean full bars for 2021-2025, measured median 3pt from
the RTH close), which also supplies the warm-up history before the 5-min data begins.
"""
from __future__ import annotations

import bisect
import datetime as _dt
import os
import sqlite3

import pandas as pd

MA_DAYS = 200
MIN_RTH_BARS = 60          # a full RTH session has 78 five-minute bars
_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'market_data.db')


def daily_closes(symbol: str = 'MNQ', db_path: str = _DB) -> pd.Series:
    """date -> session close, oldest first. See module docstring for the source rule."""
    conn = sqlite3.connect(db_path)
    try:
        d1 = pd.read_sql_query(
            "SELECT ts_utc, close FROM futures_bars_1d WHERE symbol=? ORDER BY ts_utc",
            conn, params=(symbol,))
        b5 = pd.read_sql_query(
            "SELECT ts_utc, close FROM futures_bars_5m WHERE symbol=? ORDER BY ts_utc",
            conn, params=(symbol,))
    finally:
        conn.close()
    d1['d'] = pd.to_datetime(d1.ts_utc, utc=True, format='ISO8601').dt.date
    fallback = d1.groupby('d').close.last()

    ts = pd.to_datetime(b5.ts_utc, utc=True, format='ISO8601').dt.tz_convert('America/New_York')
    b5 = b5.assign(ts=ts)
    m = ts.dt.hour * 60 + ts.dt.minute
    rth = b5[(m >= 570) & (m <= 955)]                    # 09:30 .. 15:55 bar starts
    g = rth.groupby(rth.ts.dt.date)
    n = g.size()
    last_bar = g.ts.max()
    rth_close = g.close.last()
    ok = (n >= MIN_RTH_BARS) & (last_bar.dt.hour * 60 + last_bar.dt.minute == 955)
    own = rth_close[ok]

    # our own close wins wherever it exists. combine_first, not .loc assignment: our bars
    # often include a session the daily table does not have yet (the collector deliberately
    # skips today's in-progress daily bar), and .loc on a missing date raises — which the
    # traders catch, making the Tide fail OPEN (caught Sep 29 2026 before it went live).
    s = own.combine_first(fallback)
    # a fallback row only counts on a weekday; yfinance occasionally emits weekend stubs
    s = s[[d.weekday() < 5 for d in s.index]]
    return s.sort_index()


class Tide:
    """Precomputed verdicts. `.verdict(day)` -> (above, prev_close, prev_ma, prev_date) using
    only sessions strictly before `day`; (None, None, None, None) without enough history."""

    def __init__(self, closes: pd.Series, ma_days: int = MA_DAYS):
        ma = closes.rolling(ma_days).mean()
        self._d = list(closes.index)
        self._c = list(closes.values)
        self._m = list(ma.values)

    def verdict(self, day: _dt.date):
        i = bisect.bisect_left(self._d, day) - 1
        if i < 0 or self._m[i] != self._m[i]:           # no history / NaN MA
            return None, None, None, None
        return self._c[i] > self._m[i], self._c[i], self._m[i], self._d[i]

    def agrees(self, day: _dt.date, side: str) -> bool:
        """Fails OPEN: unknown tide never blocks, so missing data cannot halt trading."""
        above = self.verdict(day)[0]
        if above is None:
            return True
        return above if side == 'LONG' else (not above)


def previous_weekday(day: _dt.date) -> _dt.date:
    d = day - _dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= _dt.timedelta(days=1)
    return d
