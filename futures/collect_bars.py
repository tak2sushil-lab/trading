"""
futures/collect_bars.py — MNQ Historical Data Collector

Sources (in order of preference):
  1. IBKR bridge  — 5-min bars, up to 1 year, best quality
  2. yfinance     — NQ=F proxy:
                    - 5-min:  last 60 days
                    - 1-hour: last 730 days (2 years)
                    - 1-day:  10+ years
Storage:
  market_data.db → futures_bars_5m (symbol, ts_utc, open, high, low, close, volume, source)

Usage:
  venv/bin/python futures/collect_bars.py --bootstrap   # seed all history
  venv/bin/python futures/collect_bars.py --update      # append last 3 days
  venv/bin/python futures/collect_bars.py --summary     # show coverage
"""

import os
import sys
import sqlite3
import argparse
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf
import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH    = Path(__file__).parent.parent / 'market_data.db'
BRIDGE_URL = os.getenv('BRIDGE_URL', 'http://localhost:8000')
ET         = pytz.timezone('America/New_York')

# yfinance symbol for continuous NQ futures (good MNQ proxy, same price action)
YF_SYMBOL  = 'NQ=F'
# Canonical symbol stored in DB
SYMBOL     = 'MNQ'

# NY session in ET (TopStepX trading window)
SESSION_START = (9, 30)
SESSION_END   = (15, 10)  # 3:10 PM CT = 4:10 PM ET


# ── Database ──────────────────────────────────────────────────────────────────

def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS futures_bars_5m (
            symbol  TEXT    NOT NULL,
            ts_utc  TEXT    NOT NULL,
            open    REAL,
            high    REAL,
            low     REAL,
            close   REAL,
            volume  INTEGER,
            source  TEXT    DEFAULT "yfinance",
            PRIMARY KEY (symbol, ts_utc)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS futures_bars_1h (
            symbol  TEXT    NOT NULL,
            ts_utc  TEXT    NOT NULL,
            open    REAL,
            high    REAL,
            low     REAL,
            close   REAL,
            volume  INTEGER,
            source  TEXT    DEFAULT "yfinance",
            PRIMARY KEY (symbol, ts_utc)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS futures_bars_1d (
            symbol  TEXT    NOT NULL,
            ts_utc  TEXT    NOT NULL,
            open    REAL,
            high    REAL,
            low     REAL,
            close   REAL,
            volume  INTEGER,
            source  TEXT    DEFAULT "yfinance",
            PRIMARY KEY (symbol, ts_utc)
        )
    ''')
    conn.commit()
    return conn


def store_bars(conn: sqlite3.Connection, rows: list[dict], table: str) -> int:
    if not rows:
        return 0
    inserted = 0
    for r in rows:
        try:
            conn.execute(
                f'INSERT OR IGNORE INTO {table} '
                f'(symbol, ts_utc, open, high, low, close, volume, source) '
                f'VALUES (?,?,?,?,?,?,?,?)',
                (r['symbol'], r['ts_utc'], r['open'], r['high'],
                 r['low'], r['close'], r.get('volume', 0), r['source'])
            )
            inserted += conn.total_changes
        except Exception:
            pass
    conn.commit()
    return inserted


# ── yfinance helpers ──────────────────────────────────────────────────────────

def _yf_to_rows(df: pd.DataFrame, source: str = 'yfinance') -> list[dict]:
    """Convert yfinance DataFrame to list of row dicts."""
    rows = []
    for ts, row in df.iterrows():
        try:
            if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
                ts_utc = ts.tz_convert('UTC').strftime('%Y-%m-%dT%H:%M:%S')
            else:
                ts_utc = ts.strftime('%Y-%m-%dT%H:%M:%S')
            rows.append({
                'symbol': SYMBOL,
                'ts_utc': ts_utc,
                'open':   float(row['Open']),
                'high':   float(row['High']),
                'low':    float(row['Low']),
                'close':  float(row['Close']),
                'volume': int(row.get('Volume', 0)),
                'source': source,
            })
        except Exception:
            pass
    return rows


def _yf_ticker() -> yf.Ticker:
    """Return a cached yfinance Ticker for NQ=F."""
    return yf.Ticker(YF_SYMBOL)


def fetch_yf_5min(days_back: int = 59) -> list[dict]:
    """yfinance 5-min — max ~59 days via Ticker.history()."""
    print(f'[yfinance] fetching 5-min NQ=F last {days_back} days...')
    try:
        df = _yf_ticker().history(period=f'{days_back}d', interval='5m')
        rows = _yf_to_rows(df)
        print(f'[yfinance] 5-min: {len(rows)} bars')
        return rows
    except Exception as e:
        print(f'[yfinance] 5-min error: {e}')
        return []


def fetch_yf_1h(days_back: int = 729) -> list[dict]:
    """yfinance 1-hour — up to ~729 days."""
    print(f'[yfinance] fetching 1-hour NQ=F last {days_back} days...')
    try:
        df = _yf_ticker().history(period=f'{days_back}d', interval='1h')
        rows = _yf_to_rows(df)
        print(f'[yfinance] 1-hour: {len(rows)} bars')
        return rows
    except Exception as e:
        print(f'[yfinance] 1-hour error: {e}')
        return []


def fetch_yf_daily(years_back: int = 10) -> list[dict]:
    """yfinance daily — 10 years of context."""
    print(f'[yfinance] fetching daily NQ=F last {years_back} years...')
    try:
        df = _yf_ticker().history(period=f'{years_back}y', interval='1d')
        rows = _yf_to_rows(df)
        print(f'[yfinance] daily: {len(rows)} bars')
        return rows
    except Exception as e:
        print(f'[yfinance] daily error: {e}')
        return []


# ── IBKR bridge helper ────────────────────────────────────────────────────────

def fetch_ibkr_5min() -> list[dict]:
    """
    Fetch MNQ 5-min bars from IBKR bridge (most recent 55 days).

    IBKR ContFuture limitation: endDateTime is not supported for continuous
    contracts — only current-window requests work. For longer history, use
    yfinance (fetch_yf_1h for 2yr context, fetch_yf_daily for 10yr).
    """
    try:
        resp = requests.get(
            f'{BRIDGE_URL}/history/futures/MNQ',
            params={'duration': '55 D', 'bar_size': '5 mins'},
            timeout=90,
        )
        if resp.status_code != 200:
            print(f'[ibkr] bridge returned {resp.status_code} — skipping')
            return []
        bars = resp.json().get('bars', [])
        rows = []
        for b in bars:
            try:
                rows.append({
                    'symbol': SYMBOL,
                    'ts_utc': b['ts'],
                    'open':   float(b['open']),
                    'high':   float(b['high']),
                    'low':    float(b['low']),
                    'close':  float(b['close']),
                    'volume': int(b.get('volume', 0)),
                    'source': 'ibkr',
                })
            except Exception:
                pass
        print(f'[ibkr] 5-min: {len(rows)} bars (55 days)')
        return rows
    except requests.exceptions.ConnectionError:
        print('[ibkr] bridge not reachable — skipping IBKR source')
        return []
    except Exception as e:
        print(f'[ibkr] error: {e}')
        return []


# ── Public API ────────────────────────────────────────────────────────────────

def bootstrap():
    """Seed maximum history from all available sources."""
    conn = init_db()
    total_5m = 0
    total_1h = 0
    total_1d = 0

    print('=== BOOTSTRAP: seeding MNQ historical data ===')
    print()

    # 1. IBKR 5-min (best quality, last 55 days — ContFuture limit)
    rows = fetch_ibkr_5min()
    if rows:
        store_bars(conn, rows, 'futures_bars_5m')
        total_5m += len(rows)
        print(f'  stored {len(rows)} IBKR 5-min bars')

    # 2. yfinance 5-min (fills most recent 60 days, fills IBKR gaps)
    rows = fetch_yf_5min(days_back=60)
    if rows:
        store_bars(conn, rows, 'futures_bars_5m')
        total_5m += len(rows)
        print(f'  stored {len(rows)} yfinance 5-min bars (deduped on insert)')

    # 3. yfinance 1-hour (2 years — extends context beyond 5-min window)
    rows = fetch_yf_1h(days_back=730)
    if rows:
        store_bars(conn, rows, 'futures_bars_1h')
        total_1h = len(rows)
        print(f'  stored {total_1h} yfinance 1-hour bars')

    # 4. yfinance daily (10 years — regime/trend context)
    rows = fetch_yf_daily(years_back=10)
    if rows:
        store_bars(conn, rows, 'futures_bars_1d')
        total_1d = len(rows)
        print(f'  stored {total_1d} yfinance daily bars')

    conn.close()
    print()
    print('=== Bootstrap complete ===')
    summary()


def update():
    """Append last 3 days — run nightly via launchd."""
    conn = init_db()
    print('=== UPDATE: fetching last 3 days ===')

    # 5-min: yfinance (bridge update handled separately if bridge available)
    rows = fetch_yf_5min(days_back=3)
    if rows:
        store_bars(conn, rows, 'futures_bars_5m')
        print(f'  stored {len(rows)} 5-min bars')

    rows = fetch_ibkr_5min()
    if rows:
        store_bars(conn, rows, 'futures_bars_5m')
        print(f'  stored {len(rows)} IBKR 5-min bars (deduped)')

    # daily
    rows = fetch_yf_daily(years_back=1)
    if rows:
        store_bars(conn, rows, 'futures_bars_1d')

    conn.close()
    summary()


def summary():
    """Print coverage stats."""
    conn = sqlite3.connect(DB_PATH)
    print()
    print('=== FUTURES DATA COVERAGE ===')
    for table in ('futures_bars_5m', 'futures_bars_1h', 'futures_bars_1d'):
        try:
            row = conn.execute(
                f'SELECT COUNT(*), MIN(ts_utc), MAX(ts_utc) FROM {table} WHERE symbol=?',
                (SYMBOL,)
            ).fetchone()
            n, lo, hi = row
            if n:
                print(f'  {table:<22} {n:>7,} bars  |  {lo[:10]} → {hi[:10]}')
            else:
                print(f'  {table:<22}       0 bars  (empty)')
        except Exception as e:
            print(f'  {table}: {e}')
    conn.close()


def load_bars(start: str | None = None, end: str | None = None,
              table: str = 'futures_bars_5m') -> pd.DataFrame:
    """
    Load bars into DataFrame for backtesting.
    Index: DatetimeIndex (America/New_York). Cols: open, high, low, close, volume.

    Usage:
        from futures.collect_bars import load_bars
        df = load_bars(start='2025-01-01')
        df = load_bars(start='2026-01-01', table='futures_bars_1h')
    """
    conn = sqlite3.connect(DB_PATH)
    q = f'SELECT ts_utc, open, high, low, close, volume FROM {table} WHERE symbol=?'
    params: list = [SYMBOL]
    if start:
        q += ' AND ts_utc >= ?'
        params.append(start)
    if end:
        q += ' AND ts_utc <= ?'
        params.append(end)
    q += ' ORDER BY ts_utc'

    df = pd.read_sql_query(q, conn, params=params, index_col='ts_utc',
                           parse_dates={'ts_utc': {'utc': True}})
    conn.close()

    df.index = df.index.tz_convert(ET)
    df.columns = [c.lower() for c in df.columns]
    return df


# ── Session filter (for backtest use) ─────────────────────────────────────────

def filter_ny_session(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only NY session bars (9:30 AM – 3:10 PM ET)."""
    t = df.index.time
    import datetime as _dt
    start = _dt.time(*SESSION_START)
    end   = _dt.time(*SESSION_END)
    return df[(t >= start) & (t <= end)]


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='MNQ bar collector')
    parser.add_argument('--bootstrap', action='store_true', help='Seed all history')
    parser.add_argument('--update',    action='store_true', help='Append last 3 days')
    parser.add_argument('--summary',   action='store_true', help='Show coverage')
    args = parser.parse_args()

    if args.bootstrap:
        bootstrap()
    elif args.update:
        update()
    elif args.summary:
        summary()
    else:
        parser.print_help()
