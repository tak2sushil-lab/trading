# database.py — trade database + learning engine
# Stores every trade, learns from results, updates strategy weights
# Uses SQLite — no extra setup needed, built into Python

import sqlite3
import json
from datetime import datetime, date
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'trades.db')

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_db():
    """Create all tables if they don't exist"""
    conn = get_connection()
    c    = conn.cursor()

    # ── Trades table ──────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS trades (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol          TEXT NOT NULL,
        entry_date      TEXT,
        entry_time      TEXT,
        entry_price     REAL,
        exit_date       TEXT,
        exit_time       TEXT,
        exit_price      REAL,
        shares          INTEGER,
        side            TEXT DEFAULT 'LONG',
        target_price    REAL,
        stop_price      REAL,
        pnl             REAL,
        pnl_pct         REAL,
        status          TEXT DEFAULT 'OPEN',
        exit_reason     TEXT,
        setup_type      TEXT,
        rsi_at_entry    REAL,
        volume_ratio    REAL,
        sector          TEXT,
        earnings_days   INTEGER,
        confidence      REAL,
        order_id        TEXT,
        notes           TEXT
    )''')

    # ── Strategy weights — updated by learner ─────────────
    c.execute('''CREATE TABLE IF NOT EXISTS strategy_weights (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        updated_date    TEXT,
        rsi_weight      REAL DEFAULT 1.0,
        volume_weight   REAL DEFAULT 1.0,
        momentum_weight REAL DEFAULT 1.0,
        sector_weight   REAL DEFAULT 1.0,
        earnings_weight REAL DEFAULT 1.0,
        notes           TEXT
    )''')

    # ── Daily performance log ─────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS daily_performance (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date      TEXT UNIQUE,
        total_trades    INTEGER DEFAULT 0,
        wins            INTEGER DEFAULT 0,
        losses          INTEGER DEFAULT 0,
        total_pnl       REAL DEFAULT 0,
        win_rate        REAL DEFAULT 0,
        best_trade      TEXT,
        worst_trade     TEXT,
        market_condition TEXT,
        notes           TEXT
    )''')

    # ── Sector performance ────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS sector_performance (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date      TEXT,
        sector          TEXT,
        avg_return      REAL,
        trade_count     INTEGER
    )''')

    # ── Earnings calendar ─────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS earnings_calendar (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol          TEXT,
        earnings_date   TEXT,
        updated_date    TEXT
    )''')

    # Insert default strategy weights if none exist
    c.execute('SELECT COUNT(*) FROM strategy_weights')
    if c.fetchone()[0] == 0:
        c.execute('''INSERT INTO strategy_weights
            (updated_date, rsi_weight, volume_weight, momentum_weight,
             sector_weight, earnings_weight, notes)
            VALUES (?, 1.0, 1.0, 1.0, 1.0, 1.0, 'initial defaults')''',
            (date.today().isoformat(),))

    conn.commit()
    conn.close()
    print("✅ Database initialized")

# ── Trade operations ──────────────────────────────────────
def log_trade_entry(symbol, entry_price, shares, target_price,
                    stop_price, setup_type, rsi, volume_ratio,
                    sector, earnings_days, confidence, order_id=''):
    conn = get_connection()
    c    = conn.cursor()
    now  = datetime.now()
    c.execute('''INSERT INTO trades
        (symbol, entry_date, entry_time, entry_price, shares,
         target_price, stop_price, setup_type, rsi_at_entry,
         volume_ratio, sector, earnings_days, confidence, order_id, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN')''',
        (symbol, now.strftime('%Y-%m-%d'), now.strftime('%H:%M:%S'),
         entry_price, shares, target_price, stop_price, setup_type,
         rsi, volume_ratio, sector, earnings_days, confidence, order_id))
    trade_id = c.lastrowid
    conn.commit()
    conn.close()
    return trade_id

def log_trade_exit(trade_id, exit_price, exit_reason):
    conn = get_connection()
    c    = conn.cursor()
    now  = datetime.now()

    # Get entry details
    c.execute('SELECT entry_price, shares FROM trades WHERE id=?', (trade_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None

    entry_price, shares = row
    pnl     = (exit_price - entry_price) * shares
    pnl_pct = ((exit_price - entry_price) / entry_price) * 100

    c.execute('''UPDATE trades SET
        exit_date=?, exit_time=?, exit_price=?,
        pnl=?, pnl_pct=?, status=?, exit_reason=?
        WHERE id=?''',
        (now.strftime('%Y-%m-%d'), now.strftime('%H:%M:%S'),
         exit_price, round(pnl, 2), round(pnl_pct, 2),
         'WIN' if pnl > 0 else 'LOSS', exit_reason, trade_id))
    conn.commit()
    conn.close()
    return round(pnl, 2)

def get_open_trades():
    conn   = get_connection()
    c      = conn.cursor()
    c.execute('''SELECT id, symbol, entry_price, shares, target_price,
                        stop_price, setup_type, confidence
                 FROM trades WHERE status='OPEN' ''')
    rows   = c.fetchall()
    conn.close()
    trades = []
    for r in rows:
        trades.append({
            'id': r[0], 'symbol': r[1], 'entry_price': r[2],
            'shares': r[3], 'target_price': r[4], 'stop_price': r[5],
            'setup_type': r[6], 'confidence': r[7]
        })
    return trades

def get_strategy_weights():
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''SELECT rsi_weight, volume_weight, momentum_weight,
                        sector_weight, earnings_weight
                 FROM strategy_weights ORDER BY id DESC LIMIT 1''')
    row  = c.fetchone()
    conn.close()
    if row:
        return {
            'rsi':      row[0],
            'volume':   row[1],
            'momentum': row[2],
            'sector':   row[3],
            'earnings': row[4]
        }
    return {'rsi': 1.0, 'volume': 1.0, 'momentum': 1.0,
            'sector': 1.0, 'earnings': 1.0}

def get_win_rate(symbol=None, days=30):
    conn = get_connection()
    c    = conn.cursor()
    if symbol:
        c.execute('''SELECT COUNT(*), SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END)
                     FROM trades WHERE symbol=? AND status IN ('WIN','LOSS')
                     AND entry_date >= date('now', ?)''',
                  (symbol, f'-{days} days'))
    else:
        c.execute('''SELECT COUNT(*), SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END)
                     FROM trades WHERE status IN ('WIN','LOSS')
                     AND entry_date >= date('now', ?)''',
                  (f'-{days} days',))
    row   = c.fetchone()
    conn.close()
    total = row[0] or 0
    wins  = row[1] or 0
    return (wins / total * 100) if total > 0 else 0

def get_daily_pnl():
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''SELECT SUM(pnl), COUNT(*),
                        SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END)
                 FROM trades
                 WHERE entry_date=date('now')
                 AND status IN ('WIN','LOSS')''')
    row  = c.fetchone()
    conn.close()
    return {
        'pnl':    round(row[0] or 0, 2),
        'trades': row[1] or 0,
        'wins':   row[2] or 0
    }

def update_trade_stop(trade_id, new_stop):
    conn = get_connection()
    c    = conn.cursor()
    c.execute('UPDATE trades SET stop_price=? WHERE id=?', (new_stop, trade_id))
    conn.commit()
    conn.close()

def update_trade_shares(trade_id, new_shares):
    conn = get_connection()
    c    = conn.cursor()
    c.execute('UPDATE trades SET shares=? WHERE id=?', (new_shares, trade_id))
    conn.commit()
    conn.close()

def get_trade_entry_date(trade_id):
    conn = get_connection()
    c    = conn.cursor()
    c.execute('SELECT entry_date FROM trades WHERE id=?', (trade_id,))
    row  = c.fetchone()
    conn.close()
    return row[0] if row else None

def update_strategy_weights(new_weights, notes=''):
    """Learner calls this to update weights based on results"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''INSERT INTO strategy_weights
        (updated_date, rsi_weight, volume_weight, momentum_weight,
         sector_weight, earnings_weight, notes)
        VALUES (?,?,?,?,?,?,?)''',
        (date.today().isoformat(),
         new_weights.get('rsi', 1.0),
         new_weights.get('volume', 1.0),
         new_weights.get('momentum', 1.0),
         new_weights.get('sector', 1.0),
         new_weights.get('earnings', 1.0),
         notes))
    conn.commit()
    conn.close()

def get_recent_trades(limit=20):
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''SELECT symbol, entry_date, entry_price, exit_price,
                        pnl, pnl_pct, status, exit_reason, setup_type
                 FROM trades WHERE status IN ('WIN','LOSS')
                 ORDER BY id DESC LIMIT ?''', (limit,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_best_setups(min_trades=5):
    """Which setup types have highest win rate"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''SELECT setup_type,
                        COUNT(*) as total,
                        SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END) as wins,
                        AVG(pnl) as avg_pnl
                 FROM trades WHERE status IN ('WIN','LOSS')
                 GROUP BY setup_type
                 HAVING total >= ?
                 ORDER BY wins*1.0/total DESC''', (min_trades,))
    rows = c.fetchall()
    conn.close()
    return rows

if __name__ == '__main__':
    init_db()
    print("Database ready at:", DB_PATH)
