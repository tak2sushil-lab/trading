"""
IBKR Historical Data Downloader + Cache
=========================================
Downloads historical bars for the full universe + regime/sector/VIX symbols
from the IBKR bridge and stores to SQLite for fast reuse.

Tables:
  bars_5m        — 5-min RTH,  6 months  (universe + regime/sector)
  bars_1h        — 1-hour RTH, 5 years   (universe + regime/sector)
  bars_premarket — 30-min extended hours, 6 months (gap quality analysis)

Run once to populate cache, re-run to top-up new/missing symbols.

Usage:
    venv/bin/python download_ibkr_cache.py             # all passes
    venv/bin/python download_ibkr_cache.py --only 5m
    venv/bin/python download_ibkr_cache.py --only 1h
    venv/bin/python download_ibkr_cache.py --only pre
    venv/bin/python download_ibkr_cache.py --refresh NVDA  # force re-download one symbol
"""

import requests
import sqlite3
import time
import sys
import os
from datetime import datetime

BRIDGE     = "http://127.0.0.1:8000"
DB_PATH    = os.path.join(os.path.dirname(__file__), "velocity_data.db")
PACING_SEC = 11   # IBKR: 60 req / 10 min → 1 per ~10s; use 11 for safety

# ── Symbol groups ─────────────────────────────────────────────────────────────

UNIVERSE = [
    'NVDA','AMD','PLTR','SMCI','MARA','COIN','TSLA','META','GOOGL','MSFT',
    'AAPL','AMZN','APP','AXON','ARM','DDOG','CRWD','SHOP','MSTR','IONQ',
    'HOOD','SOFI','UPST','RKLB','JOBY','OKLO','SMR','QBTS','RGTI','SOUN',
    'ORCL','CRM','NOW','SNOW','NET','PANW','FTNT','ZS','ANET','MRVL',
    'LRCX','KLAC','AMAT','QCOM','INTC','MU','ON','WOLF','MPWR','ENPH',
    'CCJ','UEC','NNE','SMH','EOSE','IREN','GTLB','BILL','MNDY',
    'CELH','HIMS','PENN','DKNG','LLY','ABBV','MRNA','RXRX','NVAX','CRSP',
    'XOM','CVX','SLB','OXY','FANG','MPC','VLO','NEM','GDX','GDXJ',
    'JPM','BAC','GS','MS','SCHW','MA','V','PYPL','AFRM',
    'NFLX','DIS','SPOT','RBLX','U','EA','TTWO','MTCH','BMBL',
]

# Regime + sector signals — downloaded alongside universe
REGIME_SYMBOLS = ['SPY', 'QQQ', 'VIX']

SECTOR_ETFS = ['XLK', 'XLE', 'XLF', 'XLY', 'XLI', 'XLU', 'XLC', 'XLV', 'XLB', 'XLRE']

# Pre-market: top movers + highest-volume stocks where gap quality matters most
PREMARKET_SYMBOLS = [
    'NVDA','AMD','TSLA','META','AAPL','MSFT','AMZN','GOOGL',  # mega-cap
    'PLTR','MARA','COIN','MSTR','RKLB','IONQ','HOOD','SOFI',   # high-beta universe
    'SMCI','ARM','APP','AXON','DDOG','CRWD','SNOW','NET',       # tech momentum
    'INTC','MU','QCOM','AMAT','LRCX','KLAC',                   # semis
    'SPY','QQQ',                                                # regime context
]

UNIVERSE       = list(dict.fromkeys(UNIVERSE))
ALL_RTH        = list(dict.fromkeys(UNIVERSE + REGIME_SYMBOLS + SECTOR_ETFS))
PREMARKET_SYMS = list(dict.fromkeys(PREMARKET_SYMBOLS))

# ── Bar configs ───────────────────────────────────────────────────────────────

BAR_CONFIGS = [
    # (key, table,           bar_size,  duration, rth,   label)
    ('5m',  'bars_5m',       '5 mins',  '6 M',    True,  '5-min RTH / 6 months'),
    ('1h',  'bars_1h',       '1 hour',  '5 Y',    True,  '1-hour RTH / 5 years'),
    ('pre', 'bars_premarket','30 mins', '6 M',    False, '30-min extended hours / 6 months'),
]

# ── DB helpers ────────────────────────────────────────────────────────────────

def init_db(conn):
    for table in ('bars_5m', 'bars_1h', 'bars_premarket'):
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                symbol TEXT, date TEXT, open REAL, high REAL, low REAL,
                close REAL, volume INTEGER,
                PRIMARY KEY (symbol, date)
            )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS download_log (
            symbol TEXT, bar_size TEXT, downloaded_at TEXT, bar_count INTEGER,
            PRIMARY KEY (symbol, bar_size)
        )
    """)
    conn.commit()

def already_cached(conn, symbol, bar_size_key, force_refresh=False):
    if force_refresh:
        return False
    row = conn.execute(
        "SELECT bar_count FROM download_log WHERE symbol=? AND bar_size=?",
        (symbol, bar_size_key)
    ).fetchone()
    return row is not None and row[0] > 0

def fetch_bars(symbol, bar_size, duration, rth=True):
    url = (f"{BRIDGE}/history/{symbol}"
           f"?duration={requests.utils.quote(duration)}"
           f"&bar_size={requests.utils.quote(bar_size)}"
           f"&rth={'true' if rth else 'false'}")
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def store_bars(conn, table, symbol, bars):
    rows = [(symbol, b['date'], b['open'], b['high'],
             b['low'], b['close'], b['volume']) for b in bars]
    conn.executemany(f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()

def log_result(conn, symbol, bar_size_key, bar_count):
    conn.execute(
        "INSERT OR REPLACE INTO download_log VALUES (?,?,?,?)",
        (symbol, bar_size_key, datetime.utcnow().isoformat(), bar_count)
    )
    conn.commit()

# ── Summary helper ────────────────────────────────────────────────────────────

def print_summary(conn):
    print(f"\n{'='*60}")
    print("  CACHE SUMMARY")
    print(f"{'='*60}")
    rows = conn.execute("""
        SELECT bar_size,
               COUNT(*) as symbols,
               SUM(CASE WHEN bar_count > 0 THEN 1 ELSE 0 END) as ok,
               SUM(bar_count) as total_bars
        FROM download_log GROUP BY bar_size
    """).fetchall()
    for r in rows:
        label = {'5m':'5-min/6M RTH', '1h':'1-hour/5Y RTH',
                 'pre':'30-min/6M extended'}.get(r[0], r[0])
        print(f"  {label:25s}  {r[2]}/{r[1]} symbols  {r[3]:,} bars")
    print(f"  DB: {DB_PATH}")
    print(f"{'='*60}")

# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    args = sys.argv[1:]

    only    = None
    refresh = None
    if '--only' in args:
        idx  = args.index('--only')
        only = args[idx + 1]
    if '--refresh' in args:
        idx     = args.index('--refresh')
        refresh = args[idx + 1].upper()

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    configs = BAR_CONFIGS
    if only:
        configs = [c for c in BAR_CONFIGS if c[0] == only]

    for key, table, bar_size, duration, rth, label in configs:
        # Choose which symbols to fetch for this pass
        if key == 'pre':
            symbols = PREMARKET_SYMS
        else:
            symbols = ALL_RTH   # universe + regime + sector ETFs

        print(f"\n{'='*60}")
        print(f"  {label}  ({len(symbols)} symbols)")
        new_symbols = [s for s in symbols
                       if not already_cached(conn, s, key,
                                             force_refresh=(s == refresh))]
        skip_count  = len(symbols) - len(new_symbols)
        est_min     = len(new_symbols) * PACING_SEC // 60
        print(f"  New: {len(new_symbols)}  Already cached: {skip_count}  ETA: ~{est_min}m")
        print(f"{'='*60}")

        for i, sym in enumerate(symbols, 1):
            is_new = sym in new_symbols
            if not is_new:
                print(f"  [{i:3d}/{len(symbols)}] {sym:8s} SKIP")
                continue

            bars = fetch_bars(sym, bar_size, duration, rth=rth)

            if bars and len(bars) > 0:
                # For pre-market table: keep only 04:00–09:29 bars
                if key == 'pre':
                    bars = [b for b in bars
                            if '04:' <= b['date'][11:16] < '09:30'
                            or 'T04' <= b['date'][10:13] < 'T09']
                store_bars(conn, table, sym, bars)
                log_result(conn, sym, key, len(bars))
                date_range = f"{bars[0]['date'][:10]} → {bars[-1]['date'][:10]}" if bars else ''
                print(f"  [{i:3d}/{len(symbols)}] {sym:8s} → {len(bars):5d} bars  {date_range}")
            else:
                log_result(conn, sym, key, 0)
                print(f"  [{i:3d}/{len(symbols)}] {sym:8s} ERROR / no data")

            if i < len(symbols):
                time.sleep(PACING_SEC)

    print_summary(conn)
    conn.close()

if __name__ == '__main__':
    run()
