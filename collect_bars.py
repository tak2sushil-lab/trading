"""
collect_bars.py — passive 5-min OHLCV data collector for all 159 universe symbols.

Usage:
    venv/bin/python collect_bars.py              # daily mode: fetch last 3 days
    venv/bin/python collect_bars.py --bootstrap  # one-time: fetch 60 days history
    venv/bin/python collect_bars.py --symbols AAPL TSLA  # specific symbols only

Query API (import into any backtest):
    from collect_bars import load_bars, load_multi
    df = load_bars('AAPL', start='2026-04-01', end='2026-05-01')
    dfs = load_multi(['AAPL', 'TSLA'], start='2026-04-01')

Launchd: com.sushil.trading.collect_bars — 4:30pm ET Mon-Fri
"""

import argparse
import logging
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

# ── paths ──────────────────────────────────────────────────────────────────────
TRADING_DIR = Path(__file__).parent
DB_PATH     = TRADING_DIR / "market_data.db"
LOG_PATH    = TRADING_DIR / "logs" / "collect_bars.log"

# ── timezone ───────────────────────────────────────────────────────────────────
ET = ZoneInfo("America/New_York")

# ── holidays (skip collection on closed days) ──────────────────────────────────
US_HOLIDAYS_2026 = {
    date(2026,  1,  1), date(2026,  1, 19), date(2026,  2, 16),
    date(2026,  4,  3), date(2026,  5, 25), date(2026,  6, 19),
    date(2026,  7,  3), date(2026,  9,  7), date(2026, 11, 26),
    date(2026, 12, 25),
}

# ── rate limiting ──────────────────────────────────────────────────────────────
BATCH_SIZE  = 10
BATCH_SLEEP = 1.5   # seconds between batches

# ── logging setup ──────────────────────────────────────────────────────────────
def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler(sys.stdout),
        ],
    )

log = logging.getLogger(__name__)

# ── schema ─────────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS bars_5m (
    symbol   TEXT    NOT NULL,
    ts_utc   TEXT    NOT NULL,   -- ISO-8601, UTC, e.g. "2026-05-20 14:30:00+00:00"
    open     REAL    NOT NULL,
    high     REAL    NOT NULL,
    low      REAL    NOT NULL,
    close    REAL    NOT NULL,
    volume   INTEGER NOT NULL,
    PRIMARY KEY (symbol, ts_utc)
);

CREATE TABLE IF NOT EXISTS collection_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_ts      TEXT    NOT NULL,   -- UTC ISO-8601 when this run started
    mode        TEXT    NOT NULL,   -- 'bootstrap' | 'daily'
    symbols_ok  INTEGER NOT NULL,
    symbols_err INTEGER NOT NULL,
    rows_added  INTEGER NOT NULL,
    elapsed_s   REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bars_symbol_ts ON bars_5m (symbol, ts_utc);
"""


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ── fetch ──────────────────────────────────────────────────────────────────────
_REGULAR_HOURS_START = 9 * 60 + 30   # 09:30 ET in minutes-since-midnight
_REGULAR_HOURS_END   = 16 * 60       # 16:00 ET


def fetch_bars(symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
    """
    Download `days` of 5-min bars from yfinance for each symbol.
    Returns dict {symbol: DataFrame} with columns [open, high, low, close, volume].
    DataFrame index is UTC-aware timestamps; only regular-hours bars (09:30-16:00 ET) kept.
    Empty DataFrame returned for any symbol that fails.
    """
    result: dict[str, pd.DataFrame] = {}
    period = f"{min(days, 59)}d"   # yfinance 5m max is ~60 days

    for i in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[i : i + BATCH_SIZE]
        batch_str = " ".join(batch)

        try:
            raw = yf.download(
                tickers=batch_str,
                period=period,
                interval="5m",
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
        except Exception as exc:
            log.warning("yfinance batch %s failed: %s", batch, exc)
            for sym in batch:
                result[sym] = pd.DataFrame()
            if i + BATCH_SIZE < len(symbols):
                time.sleep(BATCH_SLEEP)
            continue

        for sym in batch:
            try:
                if len(batch) == 1:
                    df = raw.copy()
                else:
                    if sym not in raw.columns.get_level_values(0):
                        result[sym] = pd.DataFrame()
                        continue
                    df = raw[sym].copy()

                if df.empty:
                    result[sym] = pd.DataFrame()
                    continue

                # normalise column names
                df.columns = [c.lower() for c in df.columns]
                df = df[["open", "high", "low", "close", "volume"]].dropna()

                # ensure UTC-aware index
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                else:
                    df.index = df.index.tz_convert("UTC")

                # filter to regular hours in ET
                et_index = df.index.tz_convert(ET)
                minutes  = et_index.hour * 60 + et_index.minute
                mask     = (minutes >= _REGULAR_HOURS_START) & (minutes < _REGULAR_HOURS_END)
                df       = df[mask]
                df["volume"] = df["volume"].astype(int)

                result[sym] = df
                log.debug("  %s: %d bars", sym, len(df))

            except Exception as exc:
                log.warning("  parse error for %s: %s", sym, exc)
                result[sym] = pd.DataFrame()

        if i + BATCH_SIZE < len(symbols):
            time.sleep(BATCH_SLEEP)

    return result


# ── upsert ─────────────────────────────────────────────────────────────────────
def upsert(conn: sqlite3.Connection, symbol: str, df: pd.DataFrame) -> int:
    """Insert rows, skip duplicates. Returns number of new rows inserted."""
    if df.empty:
        return 0

    rows = [
        (symbol, str(ts), row.open, row.high, row.low, row.close, row.volume)
        for ts, row in df.iterrows()
    ]
    before = conn.execute("SELECT COUNT(*) FROM bars_5m WHERE symbol=?", (symbol,)).fetchone()[0]
    conn.executemany(
        "INSERT OR IGNORE INTO bars_5m (symbol,ts_utc,open,high,low,close,volume) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM bars_5m WHERE symbol=?", (symbol,)).fetchone()[0]
    return after - before


# ── orchestrate ────────────────────────────────────────────────────────────────
def collect(symbols: list[str], days: int, mode: str) -> None:
    t0 = time.time()
    conn = init_db()
    log.info("=== collect_bars %s | %d symbols | %d-day lookback ===", mode, len(symbols), days)

    bars = fetch_bars(symbols, days)

    ok, err, total_added = 0, 0, 0
    for sym in symbols:
        df = bars.get(sym, pd.DataFrame())
        if df.empty:
            log.warning("  SKIP %s — no data returned", sym)
            err += 1
        else:
            added = upsert(conn, sym, df)
            total_added += added
            ok += 1
            if added:
                log.info("  %-8s  +%d rows (total bars: %d)", sym, added,
                         conn.execute("SELECT COUNT(*) FROM bars_5m WHERE symbol=?", (sym,)).fetchone()[0])

    elapsed = round(time.time() - t0, 1)
    conn.execute(
        "INSERT INTO collection_log (run_ts,mode,symbols_ok,symbols_err,rows_added,elapsed_s) "
        "VALUES (?,?,?,?,?,?)",
        (datetime.utcnow().isoformat(), mode, ok, err, total_added, elapsed),
    )
    conn.commit()
    conn.close()

    log.info("=== done: %d ok / %d err | +%d rows | %.1fs ===", ok, err, total_added, elapsed)


# ── public query API ───────────────────────────────────────────────────────────
def load_bars(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    """
    Load 5-min bars for one symbol from the local database.

    Parameters
    ----------
    symbol : str
        Ticker symbol, e.g. 'AAPL'
    start  : str | None
        ISO date or datetime string, e.g. '2026-04-01'. Inclusive.
    end    : str | None
        ISO date or datetime string. Exclusive (bars strictly before this ts).

    Returns
    -------
    pd.DataFrame
        Columns: open, high, low, close, volume
        Index  : DatetimeIndex, timezone-aware (America/New_York)
        Sorted ascending by timestamp.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)

    clauses = ["symbol = ?"]
    params: list = [symbol]
    if start:
        clauses.append("ts_utc >= ?")
        params.append(_to_utc_str(start))
    if end:
        clauses.append("ts_utc < ?")
        params.append(_to_utc_str(end))

    where = " AND ".join(clauses)
    sql   = f"SELECT ts_utc,open,high,low,close,volume FROM bars_5m WHERE {where} ORDER BY ts_utc"
    df = pd.read_sql_query(sql, conn, params=params, parse_dates=["ts_utc"])
    conn.close()

    if df.empty:
        return df

    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    df = df.set_index("ts_utc")
    df.index = df.index.tz_convert(ET)
    df.index.name = "datetime_et"
    return df


def load_multi(
    symbols: list[str],
    start: str | None = None,
    end: str | None = None,
    db_path: Path = DB_PATH,
) -> dict[str, pd.DataFrame]:
    """Load bars for multiple symbols. Returns {symbol: DataFrame}."""
    return {sym: load_bars(sym, start=start, end=end, db_path=db_path) for sym in symbols}


def _to_utc_str(dt_str: str) -> str:
    """Convert a loose date/datetime string to a UTC ISO string for DB comparison."""
    try:
        dt = pd.Timestamp(dt_str)
        if dt.tzinfo is None:
            dt = dt.tz_localize(ET)
        return dt.tz_convert("UTC").isoformat()
    except Exception:
        return dt_str


# ── diagnostics ────────────────────────────────────────────────────────────────
def print_summary(db_path: Path = DB_PATH) -> None:
    """Print per-symbol row counts and date ranges. Useful after bootstrap."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT symbol, COUNT(*) as n, MIN(ts_utc), MAX(ts_utc) "
        "FROM bars_5m GROUP BY symbol ORDER BY symbol"
    ).fetchall()
    conn.close()

    total = sum(r[1] for r in rows)
    print(f"\n{'Symbol':<10} {'Rows':>6}  {'First bar':<22}  {'Last bar'}")
    print("-" * 70)
    for sym, n, first, last in rows:
        print(f"{sym:<10} {n:>6}  {first:<22}  {last}")
    print(f"\nTotal: {len(rows)} symbols, {total:,} rows in {db_path}")


# ── main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    _setup_logging()

    parser = argparse.ArgumentParser(description="Collect 5-min OHLCV bars for trading universe")
    parser.add_argument("--bootstrap", action="store_true",
                        help="Fetch 60 days of history (one-time setup)")
    parser.add_argument("--summary", action="store_true",
                        help="Print DB summary and exit")
    parser.add_argument("--symbols", nargs="+", metavar="SYM",
                        help="Override symbol list (default: full 159-symbol universe)")
    args = parser.parse_args()

    if args.summary:
        print_summary()
        return

    # import universe from live system
    try:
        from auto_trader import FULL_UNIVERSE
        universe = sorted(FULL_UNIVERSE)
    except ImportError:
        log.error("Cannot import FULL_UNIVERSE from auto_trader.py — aborting")
        sys.exit(1)

    symbols = args.symbols if args.symbols else universe

    if args.bootstrap:
        mode, days = "bootstrap", 60
    else:
        # daily mode: skip weekends and holidays
        today = date.today()
        if today.weekday() >= 5 or today in US_HOLIDAYS_2026:
            log.info("Market closed today (%s) — nothing to collect", today)
            return
        mode, days = "daily", 3   # 3-day overlap prevents gaps if launchd misses a day

    collect(symbols, days=days, mode=mode)

    if args.bootstrap:
        print_summary()


if __name__ == "__main__":
    main()
