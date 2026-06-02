"""
futures/collect_bars.py — MNQ Historical Data Collector

Sources (in order of preference):
  1. Databento     — 1-min OHLCV → resampled to 5-min. 2+ years. Best quality.
                     Requires DATABENTO_API_KEY in .env. Dataset: GLBX.MDP3
  2. IBKR bridge   — 5-min bars, last 55 days. ContFuture limit (no endDateTime).
  3. yfinance      — NQ=F proxy:
                     - 5-min:  last 60 days
                     - 1-hour: last 730 days (2 years)
                     - 1-day:  10+ years

Storage:
  market_data.db → futures_bars_5m  (5-min bars — primary backtest source)
                   futures_bars_1m  (1-min bars — from Databento, for precise entry analysis)
                   futures_bars_1h  (1-hour bars — yfinance 2yr context)
                   futures_bars_1d  (daily bars  — yfinance 10yr regime)

Usage:
  venv/bin/python futures/collect_bars.py --bootstrap   # seed all history (Databento first)
  venv/bin/python futures/collect_bars.py --update      # append last 3 days
  venv/bin/python futures/collect_bars.py --summary     # show coverage
  venv/bin/python futures/collect_bars.py --cost        # estimate Databento cost before pulling
"""

import os
import sys
import sqlite3
import argparse
import requests
from datetime import datetime, timedelta, timezone, date
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
        CREATE TABLE IF NOT EXISTS futures_bars_1m (
            symbol  TEXT    NOT NULL,
            ts_utc  TEXT    NOT NULL,
            open    REAL,
            high    REAL,
            low     REAL,
            close   REAL,
            volume  INTEGER,
            source  TEXT    DEFAULT "databento",
            PRIMARY KEY (symbol, ts_utc)
        )
    ''')
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


# ── Databento ────────────────────────────────────────────────────────────────
# Instrument: MNQ.c.0 = Micro E-mini Nasdaq front-month continuous contract
# Dataset:    GLBX.MDP3 = CME Globex MDP 3.0 (the authoritative CME futures feed)
# Schema:     ohlcv-1m = 1-minute OHLCV bars (cheapest/smallest download)
# We resample 1-min → 5-min in pandas for the backtest.

DATABENTO_SYMBOL  = 'MNQ.c.0'   # continuous front-month MNQ
DATABENTO_DATASET = 'GLBX.MDP3'


def estimate_databento_cost(start: str = '2024-01-01') -> None:
    """
    Print a cost estimate for the data range before you download.
    Call this first — Databento deducts credits on download, not on estimate.
    """
    key = os.getenv('DATABENTO_API_KEY')
    if not key:
        print('❌ DATABENTO_API_KEY not set in .env')
        return
    try:
        import databento as db
        client = db.Historical(key=key)
        cost   = client.metadata.get_cost(
            dataset  = DATABENTO_DATASET,
            symbols  = [DATABENTO_SYMBOL],
            schema   = 'ohlcv-1m',
            start    = start,
        )
        print(f'[databento] Cost estimate for 1-min OHLCV {DATABENTO_SYMBOL}'
              f' from {start}: ${cost:.4f}')
        bal = client.metadata.get_billing_info()
        print(f'[databento] Account credits available: ${bal.get("balance_usd", "?"):.2f}')
    except Exception as e:
        print(f'[databento] Cost estimate error: {e}')


def fetch_databento(start: str = '2024-01-01', end: str | None = None) -> tuple[list[dict], list[dict]]:
    """
    Fetch 1-min OHLCV from Databento for MNQ continuous contract.
    Returns (rows_1m, rows_5m) — both ready to store.
    rows_1m  → store in futures_bars_1m
    rows_5m  → store in futures_bars_5m (resampled from 1-min)

    Cost: ~$0.10–0.50 for 2 years of 1-min OHLCV (tiny dataset).
    Databento deducts from your credit balance automatically.
    """
    key = os.getenv('DATABENTO_API_KEY')
    if not key:
        print('[databento] DATABENTO_API_KEY not set — skipping')
        return [], []

    end_str = end or datetime.now(timezone.utc).strftime('%Y-%m-%d')

    print(f'[databento] fetching 1-min OHLCV {DATABENTO_SYMBOL} {start} → {end_str}...')

    try:
        import databento as db
        client = db.Historical(key=key)

        data = client.timeseries.get_range(
            dataset  = DATABENTO_DATASET,
            symbols  = [DATABENTO_SYMBOL],
            schema   = 'ohlcv-1m',
            start    = start,
            end      = end_str,
        )

        # Convert to DataFrame
        df = data.to_df()
        if df.empty:
            print('[databento] empty response')
            return [], []

        # Databento uses nanosecond timestamps in 'ts_event' column
        # Index may already be a DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, utc=True)
        else:
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC')
            else:
                df.index = df.index.tz_convert('UTC')

        # Rename Databento OHLCV columns to our standard
        col_map = {'open': 'open', 'high': 'high', 'low': 'low',
                   'close': 'close', 'volume': 'volume'}
        df = df.rename(columns=col_map)
        df = df[['open', 'high', 'low', 'close', 'volume']].copy()

        # Scale: Databento delivers prices in fixed-point (×1e-9 for CME)
        # Check if prices look like futures prices (~20,000) or need scaling
        if df['close'].median() > 1_000_000:
            for col in ['open', 'high', 'low', 'close']:
                df[col] = df[col] / 1e9

        print(f'[databento] 1-min: {len(df):,} bars  '
              f'({df.index[0].date()} → {df.index[-1].date()})')

        # ── Build 1-min rows ──────────────────────────────────────────
        rows_1m = []
        for ts, row in df.iterrows():
            rows_1m.append({
                'symbol': SYMBOL,
                'ts_utc': ts.strftime('%Y-%m-%dT%H:%M:%S'),
                'open':   float(row['open']),
                'high':   float(row['high']),
                'low':    float(row['low']),
                'close':  float(row['close']),
                'volume': int(row.get('volume', 0)),
                'source': 'databento',
            })

        # ── Resample 1-min → 5-min ────────────────────────────────────
        df_5m = df.resample('5min').agg({
            'open':   'first',
            'high':   'max',
            'low':    'min',
            'close':  'last',
            'volume': 'sum',
        }).dropna()

        rows_5m = []
        for ts, row in df_5m.iterrows():
            rows_5m.append({
                'symbol': SYMBOL,
                'ts_utc': ts.strftime('%Y-%m-%dT%H:%M:%S'),
                'open':   float(row['open']),
                'high':   float(row['high']),
                'low':    float(row['low']),
                'close':  float(row['close']),
                'volume': int(row.get('volume', 0)),
                'source': 'databento',
            })

        print(f'[databento] 5-min: {len(rows_5m):,} bars (resampled)')
        return rows_1m, rows_5m

    except ImportError:
        print('[databento] library not installed — run: venv/bin/pip install databento')
        return [], []
    except Exception as e:
        print(f'[databento] error: {e}')
        return [], []


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

def bootstrap(databento_start: str = '2024-01-01'):
    """
    Seed maximum history from all available sources.
    Databento (if key set) runs first — gives 2+ years of clean 5-min data.
    IBKR + yfinance fill gaps / supplement where Databento isn't available.
    """
    conn = init_db()
    print('=== BOOTSTRAP: seeding MNQ historical data ===')
    print()

    # 1. Databento — best quality, 2+ years (requires DATABENTO_API_KEY in .env)
    if os.getenv('DATABENTO_API_KEY'):
        rows_1m, rows_5m = fetch_databento(start=databento_start)
        if rows_1m:
            store_bars(conn, rows_1m, 'futures_bars_1m')
            print(f'  stored {len(rows_1m):,} Databento 1-min bars')
        if rows_5m:
            store_bars(conn, rows_5m, 'futures_bars_5m')
            print(f'  stored {len(rows_5m):,} Databento 5-min bars')
    else:
        print('  [databento] DATABENTO_API_KEY not set — skipping (add to .env for 2yr data)')

    # 2. IBKR 5-min (last 55 days — fills recent gap if Databento ends yesterday)
    rows = fetch_ibkr_5min()
    if rows:
        store_bars(conn, rows, 'futures_bars_5m')
        print(f'  stored {len(rows):,} IBKR 5-min bars (deduped)')

    # 3. yfinance 5-min (fills most recent 60 days — good redundancy)
    rows = fetch_yf_5min(days_back=59)
    if rows:
        store_bars(conn, rows, 'futures_bars_5m')
        print(f'  stored {len(rows):,} yfinance 5-min bars (deduped)')

    # 4. yfinance 1-hour (2 years)
    rows = fetch_yf_1h(days_back=729)
    if rows:
        store_bars(conn, rows, 'futures_bars_1h')
        print(f'  stored {len(rows):,} yfinance 1-hour bars')

    # 5. yfinance daily (10 years — regime context)
    rows = fetch_yf_daily(years_back=10)
    if rows:
        store_bars(conn, rows, 'futures_bars_1d')
        print(f'  stored {len(rows):,} yfinance daily bars')

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

    df = pd.read_sql_query(q, conn, params=params, index_col='ts_utc')
    conn.close()

    # Mixed formats: yfinance stores plain UTC strings, IBKR stores tz-aware ISO strings
    # utc=True handles both by assuming naive strings are UTC
    df.index = pd.to_datetime(df.index, utc=True, format='mixed').tz_convert(ET)
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
    parser.add_argument('--cost',      action='store_true', help='Estimate Databento cost (no download)')
    parser.add_argument('--start',     default='2024-01-01', help='Databento start date (bootstrap only)')
    args = parser.parse_args()

    if args.cost:
        estimate_databento_cost(start=args.start)
    elif args.bootstrap:
        bootstrap(databento_start=args.start)
    elif args.update:
        update()
    elif args.summary:
        summary()
    else:
        parser.print_help()
