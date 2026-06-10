"""
futures/collect_bars.py — Multi-symbol Futures Historical Data Collector

Symbols collected:
  MNQ  (Micro E-mini NASDAQ)  — what we trade at TopStepX
  ES   (E-mini S&P 500)       — regime benchmark, trades 23h/day
  RTY  (E-mini Russell 2000)  — risk-on/risk-off leading indicator

Sources (priority order):
  1. Databento GLBX.MDP3 — 1-min OHLCV → resampled to 5-min.
                            5yr history (2021→). Cost: ~$0/symbol.
  2. yfinance             — 5-min (60d), 1-hour (2yr), daily (10yr).
                            NQ=F proxy for MNQ, ES=F for ES, RTY=F for RTY.

Storage (market_data.db):
  futures_bars_1m  — 1-min bars from Databento (precise entry analysis)
  futures_bars_5m  — 5-min bars (primary backtest source)
  futures_bars_1h  — 1-hour bars (yfinance context)
  futures_bars_1d  — daily bars (yfinance 10yr regime)

Usage:
  venv/bin/python futures/collect_bars.py --bootstrap          # seed all 3 symbols
  venv/bin/python futures/collect_bars.py --bootstrap MNQ      # single symbol
  venv/bin/python futures/collect_bars.py --update             # append last 3 days
  venv/bin/python futures/collect_bars.py --summary            # show coverage
  venv/bin/python futures/collect_bars.py --cost               # estimate cost (no download)
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
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH    = Path(__file__).parent.parent / 'market_data.db'
BRIDGE_URL = os.getenv('BRIDGE_URL', 'http://localhost:8000')
ET         = pytz.timezone('America/New_York')

DATABENTO_DATASET = 'GLBX.MDP3'
DATABENTO_START   = '2021-01-01'   # 5yr history — covers 2022 bear market

# symbol → (databento_continuous, yfinance_ticker)
FUTURES_SYMBOLS = {
    'MNQ': ('MNQ.c.0', 'NQ=F'),
    'ES':  ('ES.c.0',  'ES=F'),
    'RTY': ('RTY.c.0', 'RTY=F'),
}

SESSION_START = (9, 30)   # ET
SESSION_END   = (15, 10)  # 3:10 PM ET (TopStepX hard close)


# ── Database ──────────────────────────────────────────────────────────────────

def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    for table in ('futures_bars_1m', 'futures_bars_5m', 'futures_bars_1h', 'futures_bars_1d'):
        default_src = 'databento' if table == 'futures_bars_1m' else 'yfinance'
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS {table} (
                symbol  TEXT    NOT NULL,
                ts_utc  TEXT    NOT NULL,
                open    REAL,
                high    REAL,
                low     REAL,
                close   REAL,
                volume  INTEGER,
                source  TEXT    DEFAULT "{default_src}",
                PRIMARY KEY (symbol, ts_utc)
            )
        ''')
    conn.commit()
    return conn


def store_bars(conn: sqlite3.Connection, rows: list[dict], table: str) -> int:
    if not rows:
        return 0
    before = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    conn.executemany(
        f'INSERT OR IGNORE INTO {table} '
        f'(symbol, ts_utc, open, high, low, close, volume, source) '
        f'VALUES (?,?,?,?,?,?,?,?)',
        [(r['symbol'], r['ts_utc'], r['open'], r['high'],
          r['low'], r['close'], r.get('volume', 0), r['source']) for r in rows]
    )
    conn.commit()
    return conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] - before


# ── yfinance helpers ──────────────────────────────────────────────────────────

def _yf_rows(df: pd.DataFrame, symbol: str, source: str = 'yfinance') -> list[dict]:
    rows = []
    for ts, row in df.iterrows():
        try:
            ts_utc = ts.tz_convert('UTC') if ts.tzinfo else ts
            rows.append({
                'symbol': symbol,
                'ts_utc': ts_utc.strftime('%Y-%m-%dT%H:%M:%S'),
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


def fetch_yf(symbol: str, yf_sym: str, interval: str, period: str) -> list[dict]:
    print(f'[yfinance] {symbol} {interval} {period}...')
    try:
        df = yf.Ticker(yf_sym).history(period=period, interval=interval)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        rows = _yf_rows(df, symbol)
        print(f'[yfinance] {symbol} {interval}: {len(rows):,} bars')
        return rows
    except Exception as e:
        print(f'[yfinance] {symbol} {interval} error: {e}')
        return []


# ── Databento ─────────────────────────────────────────────────────────────────

def estimate_cost(symbols: list[str] | None = None, start: str = DATABENTO_START) -> None:
    key = os.getenv('DATABENTO_API_KEY')
    if not key:
        print('❌ DATABENTO_API_KEY not set in .env')
        return
    import databento as db
    client = db.Historical(key=key)
    targets = symbols or list(FUTURES_SYMBOLS)
    # Databento end is exclusive — use today so yesterday's full session is included.
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    total = 0.0
    for sym in targets:
        db_sym = FUTURES_SYMBOLS[sym][0]
        cost = client.metadata.get_cost(
            dataset=DATABENTO_DATASET, symbols=[db_sym], stype_in='continuous',
            schema='ohlcv-1m', start=start, end=today,
        )
        print(f'  {sym} ({db_sym}) from {start}: ${cost:.4f}')
        total += cost
    print(f'  Total: ${total:.4f}  (balance remaining after: see Databento dashboard)')


def fetch_databento(symbol: str, start: str = DATABENTO_START,
                    end: str | None = None) -> tuple[list[dict], list[dict]]:
    """
    Fetch 1-min OHLCV for one symbol from Databento.
    Returns (rows_1m, rows_5m).
    """
    key = os.getenv('DATABENTO_API_KEY')
    if not key:
        print('[databento] DATABENTO_API_KEY not set — skipping')
        return [], []

    db_sym = FUTURES_SYMBOLS[symbol][0]
    # Databento end is exclusive — pass today to include yesterday's full session.
    end_str = end or datetime.now(timezone.utc).strftime('%Y-%m-%d')

    print(f'[databento] {symbol} ({db_sym}) {start} → {end_str}...')
    try:
        import databento as db
        client = db.Historical(key=key)
        data = client.timeseries.get_range(
            dataset=DATABENTO_DATASET, symbols=[db_sym], stype_in='continuous',
            schema='ohlcv-1m', start=start, end=end_str,
        )
        df = data.to_df()
        if df.empty:
            print(f'[databento] {symbol}: empty response')
            return [], []

        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, utc=True)
        elif df.index.tz is None:
            df.index = df.index.tz_localize('UTC')
        else:
            df.index = df.index.tz_convert('UTC')

        df = df[['open', 'high', 'low', 'close', 'volume']].copy()

        # Databento CME prices are in fixed-point (divide by 1e9 if needed)
        if df['close'].median() > 1_000_000:
            for col in ['open', 'high', 'low', 'close']:
                df[col] = df[col] / 1e9

        print(f'[databento] {symbol} 1-min: {len(df):,} bars  '
              f'({df.index[0].date()} → {df.index[-1].date()})')

        rows_1m = [{
            'symbol': symbol, 'ts_utc': ts.strftime('%Y-%m-%dT%H:%M:%S'),
            'open': float(r['open']), 'high': float(r['high']),
            'low': float(r['low']),  'close': float(r['close']),
            'volume': int(r.get('volume', 0)), 'source': 'databento',
        } for ts, r in df.iterrows()]

        df_5m = df.resample('5min').agg(
            {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
        ).dropna()

        rows_5m = [{
            'symbol': symbol, 'ts_utc': ts.strftime('%Y-%m-%dT%H:%M:%S'),
            'open': float(r['open']), 'high': float(r['high']),
            'low': float(r['low']),  'close': float(r['close']),
            'volume': int(r.get('volume', 0)), 'source': 'databento',
        } for ts, r in df_5m.iterrows()]

        print(f'[databento] {symbol} 5-min: {len(rows_5m):,} bars (resampled)')
        return rows_1m, rows_5m

    except ImportError:
        print('[databento] not installed — run: venv/bin/pip install databento')
        return [], []
    except Exception as e:
        print(f'[databento] {symbol} error: {e}')
        return [], []


# ── IBKR bridge ───────────────────────────────────────────────────────────────

def fetch_ibkr_5min(symbol: str = 'MNQ') -> list[dict]:
    """IBKR 5-min bars — last 55 days. MNQ only (TopStepX instrument)."""
    try:
        resp = requests.get(
            f'{BRIDGE_URL}/history/futures/{symbol}',
            params={'duration': '55 D', 'bar_size': '5 mins'},
            timeout=90,
        )
        if resp.status_code != 200:
            print(f'[ibkr] {symbol}: bridge returned {resp.status_code} — skipping')
            return []
        rows = []
        for b in resp.json().get('bars', []):
            try:
                rows.append({
                    'symbol': symbol, 'ts_utc': b['ts'],
                    'open': float(b['open']), 'high': float(b['high']),
                    'low': float(b['low']),   'close': float(b['close']),
                    'volume': int(b.get('volume', 0)), 'source': 'ibkr',
                })
            except Exception:
                pass
        print(f'[ibkr] {symbol} 5-min: {len(rows):,} bars')
        return rows
    except requests.exceptions.ConnectionError:
        print(f'[ibkr] bridge not reachable — skipping')
        return []
    except Exception as e:
        print(f'[ibkr] {symbol} error: {e}')
        return []


# ── Public API ────────────────────────────────────────────────────────────────

def bootstrap(symbols: list[str] | None = None, start: str = DATABENTO_START):
    """Seed maximum history for all (or specified) symbols."""
    conn = init_db()
    targets = symbols or list(FUTURES_SYMBOLS)
    print(f'=== BOOTSTRAP: {", ".join(targets)}  from {start} ===')

    for sym in targets:
        yf_sym = FUTURES_SYMBOLS[sym][1]
        print(f'\n--- {sym} ---')

        # 1. Databento 1-min → 5-min (best quality, 5yr)
        rows_1m, rows_5m = fetch_databento(sym, start=start)
        if rows_1m:
            n = store_bars(conn, rows_1m, 'futures_bars_1m')
            print(f'  stored {n:,} new 1-min bars')
        if rows_5m:
            n = store_bars(conn, rows_5m, 'futures_bars_5m')
            print(f'  stored {n:,} new 5-min bars')

        # 2. IBKR 5-min (MNQ only — what we trade)
        if sym == 'MNQ':
            rows = fetch_ibkr_5min(sym)
            if rows:
                n = store_bars(conn, rows, 'futures_bars_5m')
                print(f'  stored {n:,} new IBKR 5-min bars (deduped)')

        # 3. yfinance 5-min (last 59 days — gap fill)
        rows = fetch_yf(sym, yf_sym, '5m', '59d')
        if rows:
            n = store_bars(conn, rows, 'futures_bars_5m')
            print(f'  stored {n:,} new yfinance 5-min bars (deduped)')

        # 4. yfinance 1-hour (2yr)
        rows = fetch_yf(sym, yf_sym, '1h', '729d')
        if rows:
            n = store_bars(conn, rows, 'futures_bars_1h')
            print(f'  stored {n:,} new 1-hour bars')

        # 5. yfinance daily (10yr)
        rows = fetch_yf(sym, yf_sym, '1d', '10y')
        if rows:
            n = store_bars(conn, rows, 'futures_bars_1d')
            print(f'  stored {n:,} new daily bars')

    conn.close()
    print()
    summary()


def update():
    """Append last 3 days for all symbols — run nightly via launchd."""
    conn = init_db()
    print('=== UPDATE: fetching last 3 days ===')
    for sym, (_, yf_sym) in FUTURES_SYMBOLS.items():
        # Databento 1-min (last 3 days)
        three_days_ago = (datetime.now(timezone.utc) - timedelta(days=4)).strftime('%Y-%m-%d')
        rows_1m, rows_5m = fetch_databento(sym, start=three_days_ago)
        if rows_1m:
            store_bars(conn, rows_1m, 'futures_bars_1m')
        if rows_5m:
            store_bars(conn, rows_5m, 'futures_bars_5m')

        # yfinance 5-min gap fill
        rows = fetch_yf(sym, yf_sym, '5m', '3d')
        if rows:
            store_bars(conn, rows, 'futures_bars_5m')

        # daily (keeps 10yr window fresh)
        rows = fetch_yf(sym, yf_sym, '1d', '1y')
        if rows:
            store_bars(conn, rows, 'futures_bars_1d')

    conn.close()
    summary()


def summary():
    """Print coverage for all symbols across all tables."""
    conn = sqlite3.connect(DB_PATH)
    print()
    print('=== FUTURES DATA COVERAGE ===')
    for table in ('futures_bars_1m', 'futures_bars_5m', 'futures_bars_1h', 'futures_bars_1d'):
        print(f'  {table}:')
        for sym in FUTURES_SYMBOLS:
            try:
                row = conn.execute(
                    f'SELECT COUNT(*), MIN(ts_utc), MAX(ts_utc) FROM {table} WHERE symbol=?',
                    (sym,)
                ).fetchone()
                n, lo, hi = row
                if n:
                    print(f'    {sym:<5} {n:>8,} bars  |  {lo[:10]} → {hi[:10]}')
                else:
                    print(f'    {sym:<5}        0 bars')
            except Exception as e:
                print(f'    {sym}: {e}')
    conn.close()


def load_bars(symbol: str = 'MNQ', start: str | None = None, end: str | None = None,
              table: str = 'futures_bars_5m',
              include_premarket: bool = False) -> pd.DataFrame:
    """
    Load futures bars into a DataFrame for backtesting.
    Index: DatetimeIndex (America/New_York). Cols: open, high, low, close, volume.

    include_premarket=False (default): returns all bars (premarket + RTH + overnight).
      Caller uses filter_ny_session() or filter_premarket_session() to slice.
    include_premarket=True: alias for the default (kept for clarity in callers).

    Usage:
        from futures.collect_bars import load_bars, filter_ny_session, filter_premarket_session
        df = load_bars('MNQ', start='2024-01-01')
        rth = filter_ny_session(df)        # 9:30am–3:10pm
        pm  = filter_premarket_session(df) # 8:30am–9:29am
    """
    conn = sqlite3.connect(DB_PATH)
    q = f'SELECT ts_utc, open, high, low, close, volume FROM {table} WHERE symbol=?'
    params: list = [symbol]
    if start:
        q += ' AND ts_utc >= ?'
        params.append(start)
    if end:
        q += ' AND ts_utc <= ?'
        params.append(end + 'Z' if end and 'T' not in end else end)
    q += ' ORDER BY ts_utc'

    df = pd.read_sql_query(q, conn, params=params, index_col='ts_utc')
    conn.close()

    df.index = pd.to_datetime(df.index, utc=True, format='mixed').tz_convert(ET)
    df.columns = [c.lower() for c in df.columns]

    # Deduplicate: multiple sources (Databento + yfinance + IBKR) store the same
    # timestamp with different string formats. After UTC→ET conversion, these become
    # duplicate index entries. Keep the bar with the highest volume (best quality).
    if df.index.duplicated().any():
        df = df.sort_values('volume', ascending=False)
        df = df[~df.index.duplicated(keep='first')]
        df = df.sort_index()

    return df


def filter_ny_session(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only NY RTH session bars (9:30 AM – 3:10 PM ET)."""
    import datetime as _dt
    t = df.index.time
    return df[(t >= _dt.time(*SESSION_START)) & (t <= _dt.time(*SESSION_END))]


def filter_london_session(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only London RTH bars (3:00 AM – 12:00 PM ET = 8:00am–5:00pm BST/GMT)."""
    import datetime as _dt
    t = df.index.time
    return df[(t >= _dt.time(3, 0)) & (t <= _dt.time(12, 0))]


def filter_premarket_session(df: pd.DataFrame,
                              start_time: tuple = (8, 30),
                              end_time:   tuple = (9, 29)) -> pd.DataFrame:
    """
    Keep only pre-market bars.
    Default: 8:30 AM – 9:29 AM ET (the hour before RTH open).
    On NFP/CPI days this window contains the data-reaction bars.
    Returns empty DataFrame if no pre-market data available for a given day.
    """
    import datetime as _dt
    t = df.index.time
    return df[(t >= _dt.time(*start_time)) & (t <= _dt.time(*end_time))]


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Futures bar collector (MNQ + ES + RTY)')
    parser.add_argument('--bootstrap', action='store_true', help='Seed all history')
    parser.add_argument('--update',    action='store_true', help='Append last 3 days')
    parser.add_argument('--summary',   action='store_true', help='Show coverage')
    parser.add_argument('--cost',      action='store_true', help='Estimate Databento cost')
    parser.add_argument('--start',     default=DATABENTO_START, help='Databento start date')
    parser.add_argument('symbols', nargs='*', metavar='SYM',
                        help='Symbols to bootstrap: MNQ ES RTY (default: all)')
    args = parser.parse_args()

    target_syms = [s for s in args.symbols if s] or None

    if args.cost:
        estimate_cost(symbols=target_syms, start=args.start)
    elif args.bootstrap:
        bootstrap(symbols=target_syms, start=args.start)
    elif args.update:
        update()
    elif args.summary:
        summary()
    else:
        parser.print_help()
