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

    # ── Catalyst calendar — options intelligence layer ────────
    c.execute('''CREATE TABLE IF NOT EXISTS catalyst_calendar (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol              TEXT NOT NULL,
        catalyst_type       TEXT NOT NULL,
        event_name          TEXT,
        event_date          TEXT,
        your_confidence     TEXT,
        expected_move_pct   REAL,
        iv_rank_when_noted  REAL,
        news_source         TEXT,
        notes               TEXT,
        created_date        TEXT
    )''')

    # ── Options trades — full lifecycle ───────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS options_trades (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy            TEXT NOT NULL,
        symbol              TEXT NOT NULL,
        cap_type            TEXT,
        underlying_price    REAL,
        strike              REAL,
        long_strike         REAL,
        short_strike        REAL,
        expiry              TEXT,
        right               TEXT,
        contracts           INTEGER DEFAULT 1,
        delta_entry         REAL,
        iv_rank_entry       REAL,
        iv_pct_entry        REAL,
        premium_paid        REAL,
        max_profit          REAL,
        max_loss            REAL,
        net_debit           REAL,
        stop_value          REAL,
        stop_stage          INTEGER DEFAULT 1,
        catalyst_id         INTEGER REFERENCES catalyst_calendar(id),
        days_to_catalyst    INTEGER,
        entry_grade         TEXT,
        entry_thesis        TEXT,
        entry_date          TEXT,
        exit_date           TEXT,
        exit_value          REAL,
        exit_reason         TEXT,
        return_pct          REAL,
        return_on_risk      REAL,
        thesis_correct      TEXT,
        lesson              TEXT,
        status              TEXT DEFAULT 'OPEN'
    )''')

    # ── Options snapshots — 15-min + EOD watchman writes ─────
    c.execute('''CREATE TABLE IF NOT EXISTS options_snapshots (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id            INTEGER NOT NULL REFERENCES options_trades(id),
        snapshot_date       TEXT,
        snapshot_time       TEXT,
        underlying_price    REAL,
        contract_value      REAL,
        pnl_unrealized      REAL,
        delta               REAL,
        iv_rank             REAL,
        iv_pct              REAL,
        days_to_expiry      INTEGER,
        stop_value          REAL,
        notes               TEXT
    )''')

    # ── Options news — classified by news_engine ──────────────
    c.execute('''CREATE TABLE IF NOT EXISTS options_news (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol              TEXT NOT NULL,
        headline            TEXT,
        source              TEXT,
        source_first        INTEGER DEFAULT 0,
        published_at        TEXT,
        relevance           TEXT,
        news_type           TEXT,
        direction           TEXT,
        time_horizon        TEXT,
        already_priced_in   TEXT,
        creates_future_event INTEGER DEFAULT 0,
        catalyst_id         INTEGER REFERENCES catalyst_calendar(id),
        one_line_reason     TEXT,
        linked_trade_id     INTEGER REFERENCES options_trades(id),
        created_at          TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS ticker_conviction (
        symbol          TEXT PRIMARY KEY,
        direction       TEXT,
        tier            TEXT,
        score           REAL,
        signal_count    INTEGER DEFAULT 0,
        high_count      INTEGER DEFAULT 0,
        sources         TEXT,
        narrative       TEXT,
        last_signal_at  TEXT,
        updated_at      TEXT
    )''')

    # Indexes for common queries
    c.execute('CREATE INDEX IF NOT EXISTS idx_opt_trades_status   ON options_trades(status)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_opt_trades_symbol   ON options_trades(symbol)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_opt_snapshots_trade ON options_snapshots(trade_id, snapshot_date)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_opt_news_symbol     ON options_news(symbol, relevance)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_catalyst_symbol     ON catalyst_calendar(symbol, event_date)')

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
                    sector, earnings_days, confidence, order_id='', side='LONG'):
    conn = get_connection()
    c    = conn.cursor()
    now  = datetime.now()
    c.execute('''INSERT INTO trades
        (symbol, entry_date, entry_time, entry_price, shares,
         target_price, stop_price, setup_type, rsi_at_entry,
         volume_ratio, sector, earnings_days, confidence, order_id, status, side)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?)''',
        (symbol, now.strftime('%Y-%m-%d'), now.strftime('%H:%M:%S'),
         entry_price, shares, target_price, stop_price, setup_type,
         rsi, volume_ratio, sector, earnings_days, confidence, order_id, side))
    trade_id = c.lastrowid
    conn.commit()
    conn.close()
    return trade_id

def log_trade_exit(trade_id, exit_price, exit_reason):
    conn = get_connection()
    c    = conn.cursor()
    now  = datetime.now()

    # Get entry details including side for correct PnL direction
    c.execute('SELECT entry_price, shares, side FROM trades WHERE id=?', (trade_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None

    entry_price, shares, side = row[0], row[1], (row[2] or 'LONG')
    if side == 'SHORT':
        pnl     = (entry_price - exit_price) * shares
        pnl_pct = ((entry_price - exit_price) / entry_price) * 100
    else:
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
                        stop_price, setup_type, confidence, side
                 FROM trades WHERE status='OPEN' ''')
    rows   = c.fetchall()
    conn.close()
    trades = []
    for r in rows:
        trades.append({
            'id': r[0], 'symbol': r[1], 'entry_price': r[2],
            'shares': r[3], 'target_price': r[4], 'stop_price': r[5],
            'setup_type': r[6], 'confidence': r[7],
            'side': r[8] or 'LONG'
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

def get_today_entry_counts():
    """Return today's bull/bear entry counts across all statuses (for restart restore)."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''SELECT side, COUNT(*) FROM trades
                 WHERE entry_date=date('now') AND status IN ('OPEN','WIN','LOSS')
                 GROUP BY side''')
    rows = c.fetchall()
    conn.close()
    counts = {'bull': 0, 'bear': 0}
    for side, n in rows:
        if (side or 'LONG') == 'LONG':
            counts['bull'] = n
        else:
            counts['bear'] = n
    return counts

def get_today_trades():
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''SELECT symbol, entry_price, exit_price, shares, pnl, status, exit_reason, side
                 FROM trades
                 WHERE entry_date=date('now') AND status IN ('WIN','LOSS')
                 ORDER BY id''')
    rows = c.fetchall()
    conn.close()
    return [{'symbol': r[0], 'entry': r[1], 'exit': r[2], 'shares': r[3],
             'pnl': r[4] or 0, 'status': r[5], 'reason': r[6], 'side': r[7] or 'LONG'}
            for r in rows]

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

# ── Options DB helpers ────────────────────────────────────────

def add_catalyst(symbol, catalyst_type, event_name, event_date,
                 confidence, expected_move_pct=None, iv_rank_when_noted=None,
                 news_source=None, notes=None):
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''INSERT INTO catalyst_calendar
        (symbol, catalyst_type, event_name, event_date, your_confidence,
         expected_move_pct, iv_rank_when_noted, news_source, notes, created_date)
        VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (symbol.upper(), catalyst_type, event_name, event_date, confidence,
         expected_move_pct, iv_rank_when_noted, news_source, notes,
         date.today().isoformat()))
    catalyst_id = c.lastrowid
    conn.commit()
    conn.close()
    return catalyst_id

def upsert_catalyst_from_wsh(symbol, catalyst_type, event_name, event_date,
                              confidence='MEDIUM'):
    """
    Insert a catalyst from Wall Street Horizon if one doesn't already exist
    for this symbol + date + type.  Returns (catalyst_id, created: bool).
    """
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """SELECT id FROM catalyst_calendar
           WHERE symbol=? AND event_date=? AND catalyst_type=?""",
        (symbol.upper(), event_date, catalyst_type)
    )
    row = c.fetchone()
    if row:
        conn.close()
        return row[0], False          # already exists
    c.execute(
        """INSERT INTO catalyst_calendar
           (symbol, catalyst_type, event_name, event_date, your_confidence,
            news_source, created_date)
           VALUES (?,?,?,?,?,'IBKR_WSH',?)""",
        (symbol.upper(), catalyst_type, event_name, event_date,
         confidence, date.today().isoformat())
    )
    cat_id = c.lastrowid
    conn.commit()
    conn.close()
    return cat_id, True


def get_upcoming_catalysts(days=30):
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''SELECT id, symbol, catalyst_type, event_name, event_date,
                        your_confidence, expected_move_pct, iv_rank_when_noted, notes
                 FROM catalyst_calendar
                 WHERE event_date >= date('now')
                   AND event_date <= date('now', ?)
                 ORDER BY event_date ASC''', (f'+{days} days',))
    rows = c.fetchall()
    conn.close()
    return [{'id': r[0], 'symbol': r[1], 'type': r[2], 'name': r[3],
             'date': r[4], 'confidence': r[5], 'expected_move': r[6],
             'iv_rank_noted': r[7], 'notes': r[8]} for r in rows]

def log_options_trade(strategy, symbol, cap_type, underlying_price,
                      expiry, contracts, delta_entry, iv_rank_entry, iv_pct_entry,
                      premium_paid, max_profit, max_loss, entry_grade, entry_thesis,
                      strike=None, long_strike=None, short_strike=None,
                      right='C', net_debit=None, catalyst_id=None, days_to_catalyst=None):
    conn       = get_connection()
    c          = conn.cursor()
    # Initial stop = value at which to close: spread -50%, LEAP -40% (keep 60%)
    if strategy == 'BULL_SPREAD':
        stop_value = round(premium_paid * 0.50, 2)
    else:
        stop_value = round(premium_paid * 0.60, 2)
    c.execute('''INSERT INTO options_trades
        (strategy, symbol, cap_type, underlying_price, strike, long_strike,
         short_strike, expiry, right, contracts, delta_entry, iv_rank_entry,
         iv_pct_entry, premium_paid, max_profit, max_loss, net_debit,
         stop_value, stop_stage, catalyst_id, days_to_catalyst,
         entry_grade, entry_thesis, entry_date, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,\'OPEN\')''',
        (strategy, symbol.upper(), cap_type, underlying_price, strike, long_strike,
         short_strike, expiry, right, contracts, delta_entry, iv_rank_entry,
         iv_pct_entry, premium_paid, max_profit, max_loss, net_debit,
         stop_value, catalyst_id, days_to_catalyst,
         entry_grade, entry_thesis, date.today().isoformat()))
    trade_id = c.lastrowid
    conn.commit()
    conn.close()
    return trade_id

def get_open_options_trades():
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''SELECT id, strategy, symbol, cap_type, underlying_price,
                        strike, long_strike, short_strike, expiry, right,
                        contracts, delta_entry, iv_rank_entry, premium_paid,
                        max_profit, max_loss, net_debit, stop_value, stop_stage,
                        catalyst_id, entry_grade, entry_date
                 FROM options_trades WHERE status=\'OPEN\'
                 ORDER BY entry_date ASC''')
    rows = c.fetchall()
    conn.close()
    keys = ['id','strategy','symbol','cap_type','underlying_price',
            'strike','long_strike','short_strike','expiry','right',
            'contracts','delta_entry','iv_rank_entry','premium_paid',
            'max_profit','max_loss','net_debit','stop_value','stop_stage',
            'catalyst_id','entry_grade','entry_date']
    return [dict(zip(keys, r)) for r in rows]

def update_options_stop(trade_id, new_stop_value, new_stage):
    conn = get_connection()
    c    = conn.cursor()
    c.execute('UPDATE options_trades SET stop_value=?, stop_stage=? WHERE id=?',
              (new_stop_value, new_stage, trade_id))
    conn.commit()
    conn.close()

def close_options_trade(trade_id, exit_value, exit_reason,
                        thesis_correct=None, lesson=None):
    conn       = get_connection()
    c          = conn.cursor()
    c.execute('SELECT premium_paid, max_loss FROM options_trades WHERE id=?',
              (trade_id,))
    row        = c.fetchone()
    if not row:
        conn.close()
        return None
    premium_paid, max_loss = row
    return_pct     = round((exit_value - premium_paid) / premium_paid * 100, 2) if premium_paid else 0
    return_on_risk = round((exit_value - premium_paid) / abs(max_loss) * 100, 2) if max_loss else 0
    c.execute('''UPDATE options_trades SET
        exit_date=?, exit_value=?, exit_reason=?, return_pct=?,
        return_on_risk=?, thesis_correct=?, lesson=?, status=\'CLOSED\'
        WHERE id=?''',
        (date.today().isoformat(), exit_value, exit_reason,
         return_pct, return_on_risk, thesis_correct, lesson, trade_id))
    conn.commit()
    conn.close()
    return return_pct

def log_options_snapshot(trade_id, underlying_price, contract_value,
                         delta=None, iv_rank=None, iv_pct=None,
                         days_to_expiry=None, stop_value=None, notes=None):
    conn = get_connection()
    c    = conn.cursor()
    now  = datetime.now()
    pnl  = None
    c.execute('SELECT premium_paid FROM options_trades WHERE id=?', (trade_id,))
    row  = c.fetchone()
    if row and row[0]:
        pnl = round(contract_value - row[0], 2)
    c.execute('''INSERT INTO options_snapshots
        (trade_id, snapshot_date, snapshot_time, underlying_price, contract_value,
         pnl_unrealized, delta, iv_rank, iv_pct, days_to_expiry, stop_value, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
        (trade_id, now.strftime('%Y-%m-%d'), now.strftime('%H:%M:%S'),
         underlying_price, contract_value, pnl, delta, iv_rank, iv_pct,
         days_to_expiry, stop_value, notes))
    conn.commit()
    conn.close()

def log_options_news(symbol, headline, source, published_at, relevance,
                     news_type, direction, time_horizon, already_priced_in,
                     creates_future_event, one_line_reason,
                     source_first=0, catalyst_id=None, linked_trade_id=None):
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''INSERT INTO options_news
        (symbol, headline, source, source_first, published_at, relevance,
         news_type, direction, time_horizon, already_priced_in,
         creates_future_event, catalyst_id, one_line_reason,
         linked_trade_id, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (symbol.upper(), headline, source, source_first, published_at,
         relevance, news_type, direction, time_horizon, already_priced_in,
         creates_future_event, catalyst_id, one_line_reason,
         linked_trade_id, datetime.now().isoformat()))
    news_id = c.lastrowid
    conn.commit()
    conn.close()
    return news_id

def get_closed_options_count():
    conn = get_connection()
    c    = conn.cursor()
    c.execute("SELECT COUNT(*) FROM options_trades WHERE status='CLOSED'")
    n    = c.fetchone()[0]
    conn.close()
    return n

def get_options_learning_data():
    """Return all closed trades with fields needed for learning analysis."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''SELECT strategy, symbol, cap_type, iv_rank_entry,
                        entry_grade, return_pct, return_on_risk, exit_reason,
                        thesis_correct, catalyst_id, entry_date
                 FROM options_trades WHERE status=\'CLOSED\'
                 ORDER BY entry_date ASC''')
    rows = c.fetchall()
    conn.close()
    keys = ['strategy','symbol','cap_type','iv_rank_entry','entry_grade',
            'return_pct','return_on_risk','exit_reason','thesis_correct',
            'catalyst_id','entry_date']
    return [dict(zip(keys, r)) for r in rows]

def purge_old_news(keep_signal_days=None, keep_noise_days=7):
    """
    Delete stale news rows to keep the table lean.

    NOISE + LOW rows older than keep_noise_days are deleted — they exist only
    for dedup and the dedup window is 2 hours, so 7 days is far more than enough.

    MEDIUM + HIGH rows are kept forever by default (signal history).
    Pass keep_signal_days=90 to also prune old signal rows if desired.
    """
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """DELETE FROM options_news
           WHERE relevance IN ('NOISE','LOW')
             AND created_at < datetime('now', ?)""",
        (f'-{keep_noise_days} days',)
    )
    noise_deleted = c.rowcount
    signal_deleted = 0
    if keep_signal_days:
        c.execute(
            """DELETE FROM options_news
               WHERE relevance IN ('MEDIUM','HIGH')
                 AND created_at < datetime('now', ?)""",
            (f'-{keep_signal_days} days',)
        )
        signal_deleted = c.rowcount
    conn.commit()
    conn.close()
    return noise_deleted, signal_deleted


def get_recent_news(symbol=None, limit=20, min_relevance=None):
    """
    Return recent options_news rows for display / audit.

    symbol        — filter to one ticker; None = all symbols
    limit         — max rows returned (ordered newest-first)
    min_relevance — 'HIGH', 'MEDIUM', or None (all)
    """
    conn  = get_connection()
    c     = conn.cursor()
    where = []
    args  = []
    if symbol:
        where.append('symbol=?')
        args.append(symbol.upper())
    if min_relevance == 'HIGH':
        where.append("relevance='HIGH'")
    elif min_relevance == 'MEDIUM':
        where.append("relevance IN ('HIGH','MEDIUM')")
    clause = ('WHERE ' + ' AND '.join(where)) if where else ''
    c.execute(
        f"""SELECT id, symbol, relevance, direction, news_type,
                   headline, source, published_at, one_line_reason, created_at
            FROM options_news
            {clause}
            ORDER BY created_at DESC
            LIMIT ?""",
        args + [limit]
    )
    rows = c.fetchall()
    conn.close()
    keys = ['id','symbol','relevance','direction','news_type',
            'headline','source','published_at','one_line_reason','created_at']
    return [dict(zip(keys, r)) for r in rows]


def get_news_quality_stats():
    """Return per-symbol signal quality counts for the last 30 days."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """SELECT symbol,
                  COUNT(*) as total,
                  SUM(CASE WHEN relevance='HIGH'   THEN 1 ELSE 0 END) as high,
                  SUM(CASE WHEN relevance='MEDIUM' THEN 1 ELSE 0 END) as medium,
                  SUM(CASE WHEN relevance='LOW'    THEN 1 ELSE 0 END) as low,
                  SUM(CASE WHEN relevance='NOISE'  THEN 1 ELSE 0 END) as noise
           FROM options_news
           WHERE created_at >= datetime('now', '-30 days')
           GROUP BY symbol
           ORDER BY high DESC, medium DESC"""
    )
    rows = c.fetchall()
    conn.close()
    return [{'symbol': r[0], 'total': r[1], 'high': r[2],
             'medium': r[3], 'low': r[4], 'noise': r[5]} for r in rows]


def headline_seen_recently(symbol, headline_text, hours=2):
    """Dedup: has this headline been stored for this symbol in the last N hours?"""
    conn    = get_connection()
    c       = conn.cursor()
    snippet = headline_text[:150].strip()
    c.execute('''SELECT COUNT(*) FROM options_news
                 WHERE symbol=? AND headline=?
                 AND created_at >= datetime('now', ?)''',
              (symbol.upper(), snippet, f'-{hours} hours'))
    n    = c.fetchone()[0]
    conn.close()
    return n > 0

def recompute_conviction(symbol: str) -> dict:
    """
    Recompute conviction score for a symbol from last 5 days of MEDIUM/HIGH signals.
    Upserts into ticker_conviction. Returns dict with score, tier, direction,
    signal_count, high_count, sources, prev_tier, tier_changed.
    """
    RELEVANCE_WEIGHT = {'HIGH': 1.0, 'MEDIUM': 0.35}
    conn = get_connection()
    c    = conn.cursor()

    c.execute("""
        SELECT relevance, direction, source, created_at
        FROM options_news
        WHERE symbol=? AND relevance IN ('HIGH','MEDIUM')
          AND created_at >= datetime('now', '-5 days')
        ORDER BY created_at ASC
    """, (symbol.upper(),))
    rows = c.fetchall()

    if not rows:
        conn.close()
        return {'score': 0.0, 'tier': 'LOW', 'direction': 'NEUTRAL',
                'signal_count': 0, 'high_count': 0, 'sources': '',
                'prev_tier': None, 'tier_changed': False}

    now          = datetime.now()
    seen_sources = set()
    total_score  = 0.0
    bull = bear  = 0
    high_count   = 0
    unique_srcs  = []
    last_at      = ''

    for relevance, direction, source, created_at_str in rows:
        try:
            created_at = datetime.fromisoformat(created_at_str)
        except Exception:
            created_at = now
        hours_old  = max(0, (now - created_at).total_seconds() / 3600)
        weight     = RELEVANCE_WEIGHT.get(relevance, 0)
        recency    = 0.5 ** (hours_old / 48)
        diversity  = 1.2 if source not in seen_sources else 1.0
        seen_sources.add(source)
        total_score += weight * recency * diversity
        if direction == 'BULLISH':
            bull += 1
        elif direction == 'BEARISH':
            bear += 1
        if relevance == 'HIGH':
            high_count += 1
        if source not in unique_srcs:
            unique_srcs.append(source)
        last_at = created_at_str

    score     = min(1.0, total_score)
    tier      = 'HIGH' if score >= 0.60 else ('MEDIUM' if score >= 0.30 else 'LOW')
    direction = 'BULL' if bull > bear else ('BEAR' if bear > bull else 'MIXED')
    sources   = ', '.join(unique_srcs[:5])
    now_str   = now.isoformat()

    c.execute("SELECT tier FROM ticker_conviction WHERE symbol=?", (symbol.upper(),))
    prev_row  = c.fetchone()
    prev_tier = prev_row[0] if prev_row else None

    c.execute("""
        INSERT INTO ticker_conviction
            (symbol, direction, tier, score, signal_count, high_count,
             sources, last_signal_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(symbol) DO UPDATE SET
            direction=excluded.direction, tier=excluded.tier, score=excluded.score,
            signal_count=excluded.signal_count, high_count=excluded.high_count,
            sources=excluded.sources, last_signal_at=excluded.last_signal_at,
            updated_at=excluded.updated_at
    """, (symbol.upper(), direction, tier, round(score, 3),
          len(rows), high_count, sources, last_at, now_str))
    conn.commit()
    conn.close()

    tier_order   = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}
    tier_changed = tier_order.get(tier, 0) > tier_order.get(prev_tier or 'LOW', 0)
    return {'score': score, 'tier': tier, 'direction': direction,
            'signal_count': len(rows), 'high_count': high_count,
            'sources': sources, 'prev_tier': prev_tier, 'tier_changed': tier_changed}


def update_conviction_narrative(symbol: str, narrative: str):
    conn = get_connection()
    c    = conn.cursor()
    c.execute("UPDATE ticker_conviction SET narrative=? WHERE symbol=?",
              (narrative, symbol.upper()))
    conn.commit()
    conn.close()


def get_conviction_leaderboard() -> list:
    """Return all tickers with conviction > 0, ranked by score desc."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute("""
        SELECT symbol, direction, tier, score, signal_count, high_count,
               sources, narrative, last_signal_at, updated_at
        FROM ticker_conviction
        WHERE score > 0
        ORDER BY score DESC, high_count DESC, last_signal_at DESC
    """)
    rows = c.fetchall()
    conn.close()
    keys = ['symbol','direction','tier','score','signal_count','high_count',
            'sources','narrative','last_signal_at','updated_at']
    return [dict(zip(keys, r)) for r in rows]


def get_conviction_detail(symbol: str) -> dict:
    """Return conviction row + recent MEDIUM/HIGH signals for one ticker."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute("""
        SELECT symbol, direction, tier, score, signal_count, high_count,
               sources, narrative, last_signal_at, updated_at
        FROM ticker_conviction WHERE symbol=?
    """, (symbol.upper(),))
    row = c.fetchone()
    if not row:
        conn.close()
        return {}
    keys = ['symbol','direction','tier','score','signal_count','high_count',
            'sources','narrative','last_signal_at','updated_at']
    result = dict(zip(keys, row))

    c.execute("""
        SELECT relevance, direction, news_type, headline, source,
               one_line_reason, created_at
        FROM options_news
        WHERE symbol=? AND relevance IN ('HIGH','MEDIUM')
          AND created_at >= datetime('now', '-5 days')
        ORDER BY created_at DESC LIMIT 10
    """, (symbol.upper(),))
    sig_rows = c.fetchall()
    conn.close()
    sig_keys = ['relevance','direction','news_type','headline','source',
                'one_line_reason','created_at']
    result['signals'] = [dict(zip(sig_keys, r)) for r in sig_rows]
    return result


if __name__ == '__main__':
    init_db()
    print("Database ready at:", DB_PATH)
