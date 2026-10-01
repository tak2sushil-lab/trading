# database.py — trade database + learning engine
# Stores every trade, learns from results, updates strategy weights
# Uses SQLite — no extra setup needed, built into Python

import sqlite3
import json
import re
from datetime import datetime, date, timedelta
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
        notes           TEXT,
        max_gain_pct    REAL
    )''')
    # Add max_gain_pct to existing DBs that predate this column
    try:
        c.execute('ALTER TABLE trades ADD COLUMN max_gain_pct REAL')
    except Exception:
        pass  # column already exists
    # Add partial_exited to existing DBs — persists partial exit state across restarts
    try:
        c.execute('ALTER TABLE trades ADD COLUMN partial_exited INTEGER DEFAULT 0')
    except Exception:
        pass  # column already exists
    # Add hod_at_entry — HOD at time of entry, used for energy gate analysis
    try:
        c.execute('ALTER TABLE trades ADD COLUMN hod_at_entry REAL')
    except Exception:
        pass  # column already exists
    # Add regime_at_entry (Aug 8 2026, Fish Finder redesign) — the Weather
    # Report reading at the moment of entry. Exists specifically so Bite Check
    # (the WEAK-regime FISHFINDER_ADX_TREND trailing health gate) can
    # reconstruct its trailing window from real DB history on every check,
    # instead of an in-memory list that would silently reset on every
    # autotrader restart. Written once at entry, never updated.
    try:
        c.execute('ALTER TABLE trades ADD COLUMN regime_at_entry TEXT')
    except Exception:
        pass  # column already exists

    # ── Fish Finder gate log (Aug 8 2026) ──────────────────
    # One row per calendar day, written by auto_trader.py's _crowd_gauge_correlation()/
    # _bite_check_ok() the first time each computes that day. Exists so the dashboard
    # (a SEPARATE process — cannot see autotrader's in-memory gate caches) can show
    # today's real gate state instead of silently showing nothing/stale data.
    c.execute('''CREATE TABLE IF NOT EXISTS fishfinder_gate_log (
        date              TEXT PRIMARY KEY,
        crowd_gauge_corr  REAL,
        crowd_gauge_state TEXT,
        bite_check_state  TEXT,
        computed_at       TEXT
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

    # ── Auto-close blocklist — durable, survives watchman restarts (Jul 22 2026).
    # Replaces an in-memory set that reset on every restart, which is exactly
    # the kind of gap that let USAR's fill-detection race go unnoticed for two
    # days. Any trade_id in here is skipped entirely by watchman's per-trade
    # monitoring (not just auto-close) until a human deletes the row.
    c.execute('''CREATE TABLE IF NOT EXISTS options_auto_close_blocks (
        trade_id            INTEGER PRIMARY KEY REFERENCES options_trades(id),
        reason               TEXT,
        flagged_at           TEXT
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
        created_at          TEXT,
        magnitude           INTEGER DEFAULT 3
    )''')
    # Idempotent column add for existing DBs
    try:
        c.execute('ALTER TABLE options_news ADD COLUMN magnitude INTEGER DEFAULT 3')
        conn.commit()
    except Exception:
        pass

    c.execute('''CREATE TABLE IF NOT EXISTS ticker_conviction (
        symbol          TEXT PRIMARY KEY,
        direction       TEXT,
        tier            TEXT,
        score           REAL,
        signal_count    INTEGER DEFAULT 0,
        high_count      INTEGER DEFAULT 0,
        sources         TEXT,
        narrative       TEXT,
        iv_rank         REAL,
        last_signal_at  TEXT,
        updated_at      TEXT
    )''')
    # migrate: add iv_rank to existing tables created before this column existed
    try:
        c.execute('ALTER TABLE ticker_conviction ADD COLUMN iv_rank REAL')
    except Exception:
        pass  # column already exists

    # migrate: add target_value to options_trades for profit-target auto-close
    try:
        c.execute('ALTER TABLE options_trades ADD COLUMN target_value REAL')
    except Exception:
        pass  # column already exists

    # ── Auto-suggest decision log ─────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS opt_suggestions (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol                TEXT NOT NULL,
        suggested_at          TEXT NOT NULL,
        conviction_score      REAL,
        signal_count          INTEGER,
        calc_log_id           INTEGER REFERENCES opt_calc_log(id),
        verdict               TEXT,
        mc_ev_dollar          REAL,
        mc_win_rate           REAL,
        gates_pass            INTEGER,
        long_strike           REAL,
        short_strike          REAL,
        expiry                TEXT,
        net_debit             REAL,
        underlying_at_suggest REAL,
        status                TEXT DEFAULT 'PENDING',
        user_decision         TEXT,
        decided_at            TEXT,
        trade_id              INTEGER REFERENCES options_trades(id),
        underlying_7d         REAL,
        underlying_14d        REAL,
        whatif_pnl            REAL,
        whatif_return_pct     REAL
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_sug_status  ON opt_suggestions(status, suggested_at)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_sug_symbol  ON opt_suggestions(symbol, suggested_at)')

    # ── KB: Calculator run log — every OPT BUY evaluation ────────
    c.execute('''CREATE TABLE IF NOT EXISTS opt_calc_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_at          TEXT NOT NULL,
        symbol          TEXT NOT NULL,
        underlying      REAL,
        iv_pct          REAL,
        iv_rank         REAL,
        hv30            REAL,
        edge_pts        REAL,
        vol_verdict     TEXT,
        expected_move   REAL,
        dte             INTEGER,
        expiry          TEXT,
        long_strike     REAL,
        short_strike    REAL,
        net_debit       REAL,
        breakeven       REAL,
        breakeven_pct   REAL,
        momentum_5d     REAL,
        above_200       INTEGER,
        conviction_tier TEXT,
        conviction_dir  TEXT,
        signal_count    INTEGER,
        vol_gate        INTEGER,
        tech_gate       INTEGER,
        conviction_gate INTEGER,
        liquidity_gate  INTEGER,
        momentum_gate   INTEGER,
        gates_pass      INTEGER,
        verdict         TEXT,
        net_delta       REAL,
        net_theta       REAL,
        net_vega        REAL,
        velocity_ratio  REAL,
        mc_ev_dollar    REAL,
        mc_win_rate     REAL,
        user_action     TEXT DEFAULT 'NONE',
        trade_id        INTEGER REFERENCES options_trades(id)
    )''')
    # Spread Toll Gate metrics (Aug 3 2026): new decider + legacy per-leg
    # metric, both logged so the nightly what-if can score old vs new rule.
    try:
        c.execute('ALTER TABLE opt_calc_log ADD COLUMN liq_cost_pct REAL')
    except Exception:
        pass  # column already exists
    try:
        c.execute('ALTER TABLE opt_calc_log ADD COLUMN liq_legacy_max_rel REAL')
    except Exception:
        pass  # column already exists

    # ── Edge Budget instrumentation (Sep 20 2026) ─────────────────────────
    # Every options decision now records the structure it chose, the move that
    # structure needs, the move the signal was expected to deliver, and whether
    # the Edge Budget would have allowed it. While the gate runs in OBSERVE mode
    # it does not block, so these columns are a stored PREDICTION we can score
    # against realized P&L — which is the only way to find out whether the gate
    # is right. skip_reason was never persisted before, so "why did nothing
    # trade" could not be answered from the DB at all.
    for _col, _typ in (('be_move_pct', 'REAL'), ('signal_move_pct', 'REAL'),
                       ('edge_required_pct', 'REAL'), ('edge_budget_ok', 'INTEGER'),
                       ('structure_template', 'TEXT'), ('skip_reason', 'TEXT')):
        try:
            c.execute(f'ALTER TABLE opt_calc_log ADD COLUMN {_col} {_typ}')
        except Exception:
            pass  # column already exists

    # ── Daily option chain snapshots (Aug 3 2026) — enables new-strike/
    #    new-expiry detection and IV-rank-from-own-history, both deferred
    #    items from the Aug 3 ideation doc. v1 scope: held-position
    #    underlyings only (options/collect_chain_snapshots.py, EOD daily).
    c.execute('''CREATE TABLE IF NOT EXISTS options_chain_snapshots (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_date   TEXT NOT NULL,
        symbol          TEXT NOT NULL,
        underlying      REAL,
        expiry          TEXT NOT NULL,
        right           TEXT NOT NULL,
        strike          REAL NOT NULL,
        bid             REAL,
        ask             REAL,
        last            REAL,
        iv              REAL,
        volume          INTEGER,
        open_interest    INTEGER,
        UNIQUE(snapshot_date, symbol, expiry, right, strike)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_chain_snap_lookup '
              'ON options_chain_snapshots(symbol, expiry, right, snapshot_date)')

    # ── Book-level options Greeks snapshots (Aug 3 2026, watchman writes
    #    every full scan; dashboard reads latest row) ───────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS options_book_greeks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT NOT NULL,
        positions   INTEGER,
        total_value REAL,
        net_delta   REAL,
        net_theta   REAL,
        net_vega    REAL
    )''')

    # ── KB: Outcome log — actual P&L vs predicted MC EV ──────────
    c.execute('''CREATE TABLE IF NOT EXISTS opt_trade_outcomes (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id        INTEGER NOT NULL REFERENCES options_trades(id),
        calc_log_id     INTEGER REFERENCES opt_calc_log(id),
        predicted_ev    REAL,
        predicted_wr    REAL,
        actual_pnl      REAL,
        exit_reason     TEXT,
        days_held       INTEGER,
        accuracy_pct    REAL,
        thesis_correct  INTEGER,
        lesson          TEXT,
        recorded_at     TEXT
    )''')

    # migrate: indexes are safe to repeat
    c.execute('CREATE INDEX IF NOT EXISTS idx_calc_log_sym    ON opt_calc_log(symbol, run_at)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_calc_log_verdict ON opt_calc_log(verdict, run_at)')

    # Indexes for common queries
    c.execute('CREATE INDEX IF NOT EXISTS idx_opt_trades_status   ON options_trades(status)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_opt_trades_symbol   ON options_trades(symbol)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_opt_snapshots_trade ON options_snapshots(trade_id, snapshot_date)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_opt_news_symbol     ON options_news(symbol, relevance)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_catalyst_symbol     ON catalyst_calendar(symbol, event_date)')

    # ── Sector grades — learner writes nightly, grade_setup reads ─
    c.execute('''CREATE TABLE IF NOT EXISTS sector_grades (
        sector      TEXT PRIMARY KEY,
        wr_30d      REAL,
        trade_count INTEGER,
        grade       TEXT,
        updated_at  TEXT
    )''')

    # ── Scan log — every graded candidate, win or lose, entered or skipped ──
    c.execute('''CREATE TABLE IF NOT EXISTS scan_log (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_date        TEXT NOT NULL,
        scan_time        TEXT NOT NULL,
        symbol           TEXT NOT NULL,
        direction        TEXT NOT NULL,
        regime           TEXT,
        price            REAL,
        grade            TEXT,
        score            INTEGER,
        skip_reason      TEXT,
        vol_ratio        REAL,
        rsi              REAL,
        intra_chg        REAL,
        sector           TEXT,
        is_catalyst      INTEGER DEFAULT 0,
        entered          INTEGER DEFAULT 0,
        entry_trade_id   INTEGER,
        actual_day_pct   REAL,
        actual_day_high_pct REAL,
        actual_close     REAL,
        enriched             INTEGER DEFAULT 0,
        burst_age_min        REAL,
        consec_new_highs     INTEGER,
        today_hod            REAL,
        price_vs_hod_pct     REAL,
        actual_30m_pct       REAL,
        actual_60m_pct       REAL
    )''')
    # Migrate existing DBs — add columns if not present
    for _col, _typ in [
        ('burst_age_min',    'REAL'),
        ('consec_new_highs', 'INTEGER'),
        ('today_hod',        'REAL'),
        ('price_vs_hod_pct', 'REAL'),
        ('actual_30m_pct',   'REAL'),
        ('actual_60m_pct',   'REAL'),
        # Sep 18 2026 -- the grader's own breakdown and a CORRECT forward label.
        # score_components: JSON {component: points} from grade_setup's `reasons`. Until now
        #   only the TOTAL was stored, so none of the ~15 hand-assigned weights (ORB +30,
        #   VWAP reclaim +25, bull flag +25, HOD +20, vol +25, RSI +20 ...) could ever be
        #   scored against an outcome. 3,694 A+ candidates were logged with the breakdown
        #   thrown away every scan.
        # fwd_mfe_pct / fwd_mae_pct: best and worst excursion FROM THE SIGNAL PRICE over the
        #   next fwd_window_min minutes. These replace actual_day_high_pct as the learning
        #   target -- that column measures the stock's day from the OPEN (see enrich_scan_log
        #   below), so it mostly re-reports the MIN_TODAY_GAIN entry gate and reports ~94% of
        #   candidates as winners when the true forward rate is ~23%.
        ('score_components', 'TEXT'),
        ('fwd_mfe_pct',      'REAL'),
        ('fwd_mae_pct',      'REAL'),
        ('fwd_window_min',   'INTEGER'),
        # Sep 30 2026 day-trader review -- three measurements logged on every graded
        # candidate so they can be scored against outcomes before any of them gates a trade.
        # ret_5d: prior five-session return (multi-day freshness). atr_pct: 14-session daily
        # ATR as % of the prior close. is_thrust: up >=5% from the open on >=3x volume,
        # above VWAP.
        ('ret_5d',           'REAL'),
        ('atr_pct',          'REAL'),
        ('is_thrust',        'INTEGER'),
        # today_gain: the % change vs the prior close that the GRADER acted on (prev_chg). It
        # was frozen at first fetch for months and nobody could see it, because only outputs
        # were logged; logging the input makes a stale value visible next to the live price.
        ('today_gain',       'REAL'),
    ]:
        try:
            c.execute(f'ALTER TABLE scan_log ADD COLUMN {_col} {_typ}')
        except Exception:
            pass  # column already exists
    c.execute('CREATE INDEX IF NOT EXISTS idx_scan_log_date ON scan_log(scan_date)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_scan_log_symbol ON scan_log(symbol, scan_date)')

    # Insert default strategy weights if none exist
    c.execute('SELECT COUNT(*) FROM strategy_weights')
    if c.fetchone()[0] == 0:
        c.execute('''INSERT INTO strategy_weights
            (updated_date, rsi_weight, volume_weight, momentum_weight,
             sector_weight, earnings_weight, notes)
            VALUES (?, 1.0, 1.0, 1.0, 1.0, 1.0, 'initial defaults')''',
            (date.today().isoformat(),))

    # ── Futures trades ────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS futures_trades (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol          TEXT NOT NULL DEFAULT 'MNQ',
        contract        TEXT,
        entry_date      TEXT,
        entry_time      TEXT,
        entry_price     REAL,
        exit_date       TEXT,
        exit_time       TEXT,
        exit_price      REAL,
        contracts       INTEGER DEFAULT 1,
        side            TEXT DEFAULT 'LONG',
        target_price    REAL,
        stop_price      REAL,
        pnl             REAL,
        pnl_ticks       REAL,
        status          TEXT DEFAULT 'OPEN',
        exit_reason     TEXT,
        setup_type      TEXT,
        session         TEXT,
        order_id        TEXT,
        notes           TEXT,
        max_gain_ticks  REAL
    )''')

    # ── Prop account state — one row per eval ─────────────
    c.execute('''CREATE TABLE IF NOT EXISTS prop_account_state (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        eval_date               TEXT,
        eod_balance             REAL,
        high_water_mark         REAL,
        daily_pnl               REAL,
        total_realized_profit   REAL,
        status                  TEXT DEFAULT 'ACTIVE',
        notes                   TEXT
    )''')

    # ── Futures scan log ──────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS futures_scan_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_time       TEXT,
        scan_date       TEXT,
        symbol          TEXT DEFAULT 'MNQ',
        session         TEXT,
        regime          TEXT,
        score           INTEGER,
        grade           TEXT,
        signal          TEXT,
        entry_price     REAL,
        action          TEXT,
        notes           TEXT
    )''')

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

# ── Broker friction ────────────────────────────────────────────────────────────
# ⚠️ THIS IS A MODEL, NOT A BILL. Verified Sep 18 2026 against GET /executions on a live
# trading session: IBKR charges the PAPER account $0.00 on every fill (33 of 33, with the
# commissionReport actually present -- a real zero, not a missing value). So every dollar
# of commission in trades.db was invented here. That is the correct thing to do -- paper
# P&L should forecast what a funded account will pay -- but it means the plan below has to
# match the plan the REAL account is enrolled in, and nothing in the API reports that.
#
# IBKR Pro offers two US-equity schedules. At this book's median of 14 shares per trade the
# PER-SHARE RATE IS IRRELEVANT -- the minimum binds on 96% of trades -- so the choice of
# plan is a straight 2.6x on total friction:
#     FIXED   $0.005/share, $1.00 min/order  ->  $2.00 round trip  ->  $1,641 over 810 trades
#     TIERED  $0.0035/share, $0.35 min/order ->  $0.77 round trip  ->  $  621 over 810 trades
# (Tiered also passes through exchange/regulatory fees, a few cents at these sizes, and caps
#  at 0.5% of trade value; Fixed caps at 1%.)
#
# ⚠️ WHICH PLAN THIS ACCOUNT IS ON IS UNVERIFIED. It cannot be read from the API and the
# live account (trading-prod/trades.db) has never placed an order, so there is no fill to
# measure. Confirm it in IBKR Client Portal, then set IBKR_COMMISSION_PLAN accordingly --
# that is the only edit needed.
#
# NOTE this figure is NOT purely cosmetic: realized P&L feeds the daily-loss brake
# (MAX_DAILY_LOSS) and peak_session_pnl, so changing the plan shifts those thresholds by
# roughly $1.20 per trade. Small, but it is a live behaviour change -- make it deliberately.
# Added Sep 3 2026; made plan-aware and documented Sep 18 2026.
# Sep 20 2026 — set to TIERED, user-directed. At our median 14 shares/trade the per-share
# rate is irrelevant and only the per-order MINIMUM binds, so the plan is a straight 2.6x on
# all friction: FIXED $2.00 round trip vs TIERED $0.77. On the overnight book it is the
# difference between a dead strategy and a live one (10 x $1,000 FIXED = -2.88bp/night net
# against a +17.12bp gross edge).
# ⚠️ THIS IS A MODELLING CHOICE THAT ASSUMES THE REAL ACCOUNT IS ENROLLED IN TIERED.
# IBKR does not report the plan through the API and the live account has never traded, so it
# cannot be verified from here. Set it in IBKR Client Portal before funding anything, or this
# model understates real costs by ~$1.30 per trade.
IBKR_COMMISSION_PLAN = 'TIERED'         # 'FIXED' | 'TIERED' -- see the warning above
_IBKR_SCHEDULE = {
    'FIXED':  (0.005,  1.00),
    'TIERED': (0.0035, 0.35),
}
IBKR_PER_SHARE, IBKR_MIN_ORDER = _IBKR_SCHEDULE[IBKR_COMMISSION_PLAN]


def equity_commission(shares: float, round_trip: bool = True) -> float:
    """Commission in dollars for `shares` (one leg, or a full round trip)."""
    try:
        one = max(IBKR_MIN_ORDER, IBKR_PER_SHARE * abs(float(shares or 0)))
    except (TypeError, ValueError):
        return 0.0
    return round(one * (2 if round_trip else 1), 2)


def log_trade_exit(trade_id, exit_price, exit_reason, max_gain_pct=None):
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
        gross = (entry_price - exit_price) * shares
    else:
        gross = (exit_price - entry_price) * shares
    # Charge the real round-trip commission. Was missing entirely until Sep 3 2026, so every
    # historical pnl in this table is GROSS -- see equity_commission() above.
    comm    = equity_commission(shares)
    pnl     = gross - comm
    pnl_pct = (pnl / (entry_price * shares) * 100) if (entry_price and shares) else 0.0

    c.execute('''UPDATE trades SET
        exit_date=?, exit_time=?, exit_price=?,
        pnl=?, pnl_pct=?, status=?, exit_reason=?, max_gain_pct=?
        WHERE id=?''',
        (now.strftime('%Y-%m-%d'), now.strftime('%H:%M:%S'),
         exit_price, round(pnl, 2), round(pnl_pct, 2),
         'WIN' if pnl > 0 else 'LOSS', exit_reason,
         round(max_gain_pct, 2) if max_gain_pct is not None else None,
         trade_id))
    conn.commit()
    conn.close()
    return round(pnl, 2)

def get_open_trades():
    conn   = get_connection()
    c      = conn.cursor()
    c.execute('''SELECT id, symbol, entry_price, shares, target_price,
                        stop_price, setup_type, confidence, side
                 FROM trades WHERE status='OPEN'
                   AND setup_type != 'RECONCILED' ''')
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
                     AND setup_type != 'RECONCILED'
                     AND entry_date >= date('now', ?)''',
                  (symbol, f'-{days} days'))
    else:
        c.execute('''SELECT COUNT(*), SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END)
                     FROM trades WHERE status IN ('WIN','LOSS')
                     AND setup_type != 'RECONCILED'
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
                 AND setup_type != 'RECONCILED'
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
                 AND setup_type != 'RECONCILED'
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
                 AND status IN ('WIN','LOSS')
                 AND setup_type != 'RECONCILED'
                 ''')
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

def set_regime_at_entry(trade_id, regime):
    """Tags a trade with the Weather Report reading at entry. Written once
    right after place_trade() succeeds — see regime_at_entry column comment
    in init_db(). Durable (DB write, not in-memory) so Bite Check survives
    autotrader restarts."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('UPDATE trades SET regime_at_entry=? WHERE id=?', (regime, trade_id))
    conn.commit()
    conn.close()

def log_fishfinder_crowd_gauge(date_str, corr, state):
    """Persists today's Crowd Gauge reading — dashboard-visible source of
    truth, since the dashboard is a separate process from autotrader and
    can't see its in-memory cache. Upserts (called at most once/day live,
    but idempotent if called again)."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''INSERT INTO fishfinder_gate_log (date, crowd_gauge_corr, crowd_gauge_state, computed_at)
        VALUES (?,?,?,datetime('now'))
        ON CONFLICT(date) DO UPDATE SET
            crowd_gauge_corr=excluded.crowd_gauge_corr,
            crowd_gauge_state=excluded.crowd_gauge_state,
            computed_at=excluded.computed_at''',
        (date_str, corr, state))
    conn.commit()
    conn.close()

def log_fishfinder_bite_check(date_str, state):
    """Persists today's Bite Check state — same rationale as
    log_fishfinder_crowd_gauge()."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''INSERT INTO fishfinder_gate_log (date, bite_check_state, computed_at)
        VALUES (?,?,datetime('now'))
        ON CONFLICT(date) DO UPDATE SET
            bite_check_state=excluded.bite_check_state,
            computed_at=excluded.computed_at''',
        (date_str, state))
    conn.commit()
    conn.close()

def get_weak_regime_adx_history(before_date):
    """All CLOSED FISHFINDER_ADX_TREND trades entered while regime was WEAK,
    with exit_date strictly before `before_date` (no lookahead) — the raw
    material Bite Check's trailing-window health check sums over. Returns
    list of (exit_date, pnl) tuples."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "SELECT exit_date, pnl FROM trades WHERE setup_type='FISHFINDER_ADX_TREND' "
        "AND regime_at_entry='WEAK' AND status IN ('WIN','LOSS') AND exit_date < ? "
        "ORDER BY exit_date", (before_date,)
    )
    rows = c.fetchall()
    conn.close()
    return rows

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

def get_sector_grade(sector):
    """Return 'STRONG', 'NEUTRAL', 'WEAK' or None if no data yet."""
    conn = get_connection()
    row  = conn.execute(
        'SELECT grade FROM sector_grades WHERE sector = ?', (sector,)
    ).fetchone()
    conn.close()
    return row[0] if row else None

def update_sector_grades(grades):
    """grades = {sector: {'wr': float, 'count': int, 'grade': str}}"""
    conn = get_connection()
    now  = datetime.now().isoformat()
    for sector, data in grades.items():
        conn.execute('''
            INSERT OR REPLACE INTO sector_grades (sector, wr_30d, trade_count, grade, updated_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (sector, data['wr'], data['count'], data['grade'], now))
    conn.commit()
    conn.close()

def get_recent_trades(limit=20):
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''SELECT symbol, entry_date, entry_price, exit_price,
                        pnl, pnl_pct, status, exit_reason, setup_type
                 FROM trades WHERE status IN ('WIN','LOSS')
                 AND setup_type != 'RECONCILED'
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
                 AND setup_type != 'RECONCILED'
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
    # Stop and target: strategy-specific
    if strategy in ('BULL_SPREAD', 'BEAR_PUT_SPREAD'):
        stop_value   = round(premium_paid * 0.50, 2)
        target_value = round(premium_paid + (max_profit or 0) * 0.50, 2)
    elif strategy in ('BULL_PUT_CREDIT', 'BEAR_CALL_CREDIT'):
        # Credit: premium_paid = max profit (credit received × 100 × qty).
        # stop_value  = spread buyback cost that triggers stop (3× credit = net loss 2×).
        # target_value = spread buyback cost at 50% profit (spread fell to 0.5× credit).
        stop_value   = round(premium_paid * 3.0, 2)
        target_value = round(premium_paid * 0.5, 2)
    elif strategy == 'OPT_SCALP':
        stop_value   = round(premium_paid * 0.50, 2)   # -50% exit
        target_value = round(premium_paid * 1.80, 2)   # +80% exit
    else:  # LEAP
        stop_value   = round(premium_paid * 0.60, 2)
        target_value = round(premium_paid * 2.0, 2)
    c.execute('''INSERT INTO options_trades
        (strategy, symbol, cap_type, underlying_price, strike, long_strike,
         short_strike, expiry, right, contracts, delta_entry, iv_rank_entry,
         iv_pct_entry, premium_paid, max_profit, max_loss, net_debit,
         stop_value, stop_stage, target_value, catalyst_id, days_to_catalyst,
         entry_grade, entry_thesis, entry_date, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,\'OPEN\')''',
        (strategy, symbol.upper(), cap_type, underlying_price, strike, long_strike,
         short_strike, expiry, right, contracts, delta_entry, iv_rank_entry,
         iv_pct_entry, premium_paid, max_profit, max_loss, net_debit,
         stop_value, target_value, catalyst_id, days_to_catalyst,
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
                        target_value, catalyst_id, entry_grade, entry_date
                 FROM options_trades WHERE status=\'OPEN\'
                 ORDER BY entry_date ASC''')
    rows = c.fetchall()
    conn.close()
    keys = ['id','strategy','symbol','cap_type','underlying_price',
            'strike','long_strike','short_strike','expiry','right',
            'contracts','delta_entry','iv_rank_entry','premium_paid',
            'max_profit','max_loss','net_debit','stop_value','stop_stage',
            'target_value','catalyst_id','entry_grade','entry_date']
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
    c.execute('SELECT premium_paid, max_loss, strategy FROM options_trades WHERE id=?',
              (trade_id,))
    row        = c.fetchone()
    if not row:
        conn.close()
        return None
    premium_paid, max_loss, strategy = row
    if strategy in ('BULL_PUT_CREDIT', 'BEAR_CALL_CREDIT'):
        # For credit spreads: exit_value = buyback cost. P&L = credit_received - buyback.
        # premium_paid = credit_received (max profit). return_pct = P&L / credit × 100.
        pnl            = round(premium_paid - exit_value, 2)
        return_pct     = round(pnl / premium_paid * 100, 2) if premium_paid else 0
        return_on_risk = round(pnl / abs(max_loss) * 100, 2) if max_loss else 0
    else:
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

def block_auto_close(trade_id, reason):
    """Durably block watchman's per-trade monitoring for one options trade —
    survives restarts, unlike an in-memory set (Jul 22 2026). Idempotent:
    calling again just refreshes the reason/timestamp."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''INSERT INTO options_auto_close_blocks (trade_id, reason, flagged_at)
                 VALUES (?, ?, ?)
                 ON CONFLICT(trade_id) DO UPDATE SET reason=excluded.reason,
                     flagged_at=excluded.flagged_at''',
              (trade_id, reason, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def unblock_auto_close(trade_id):
    """Manually lift a block once a trade has been reconciled by hand."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('DELETE FROM options_auto_close_blocks WHERE trade_id=?', (trade_id,))
    conn.commit()
    conn.close()

def is_auto_close_blocked(trade_id):
    conn = get_connection()
    c    = conn.cursor()
    c.execute('SELECT 1 FROM options_auto_close_blocks WHERE trade_id=?', (trade_id,))
    row  = c.fetchone()
    conn.close()
    return row is not None

def get_auto_close_blocks():
    """All currently-blocked trades, for inspection (e.g. from a Telegram command)."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('SELECT trade_id, reason, flagged_at FROM options_auto_close_blocks '
              'ORDER BY flagged_at DESC')
    rows = c.fetchall()
    conn.close()
    return [{'trade_id': r[0], 'reason': r[1], 'flagged_at': r[2]} for r in rows]

def log_options_snapshot(trade_id, underlying_price, contract_value,
                         delta=None, iv_rank=None, iv_pct=None,
                         days_to_expiry=None, stop_value=None, notes=None):
    conn = get_connection()
    c    = conn.cursor()
    now  = datetime.now()
    pnl  = None
    c.execute('SELECT premium_paid, strategy FROM options_trades WHERE id=?', (trade_id,))
    row  = c.fetchone()
    if row and row[0]:
        is_credit = row[1] in ('BULL_PUT_CREDIT', 'BEAR_CALL_CREDIT')
        pnl = round((row[0] - contract_value) if is_credit else (contract_value - row[0]), 2)
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
                     source_first=0, catalyst_id=None, linked_trade_id=None,
                     magnitude=3):
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''INSERT INTO options_news
        (symbol, headline, source, source_first, published_at, relevance,
         news_type, direction, time_horizon, already_priced_in,
         creates_future_event, catalyst_id, one_line_reason,
         linked_trade_id, created_at, magnitude)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (symbol.upper(), headline, source, source_first, published_at,
         relevance, news_type, direction, time_horizon, already_priced_in,
         creates_future_event, catalyst_id, one_line_reason,
         linked_trade_id, datetime.now().isoformat(), magnitude or 3))
    news_id = c.lastrowid
    conn.commit()
    conn.close()
    return news_id

def get_options_total_pnl(since: str = None) -> float:
    """Net P&L across CLOSED options trades. Used by the options circuit breaker.
    Credit spreads: P&L = premium_paid - exit_value (credit received minus buyback cost).
    Debit spreads/LEAPs/scalps: P&L = exit_value - premium_paid.

    `since` (YYYY-MM-DD, on exit_date) scopes the total to trades closed on or after that
    date. Added Sep 22 2026 so the circuit breaker can measure the CURRENT system rather
    than lifetime damage — see options_trader.OPTIONS_CB_SINCE. Passing None keeps the
    lifetime figure, which is still what the dashboard and OPT STATUS report.
    """
    conn = get_connection()
    c    = conn.cursor()
    sql = """SELECT COALESCE(SUM(
        CASE WHEN strategy IN ('BULL_PUT_CREDIT','BEAR_CALL_CREDIT')
             THEN premium_paid - exit_value
             ELSE exit_value - premium_paid END
    ), 0.0) FROM options_trades WHERE status='CLOSED'"""
    args = ()
    if since:
        sql += " AND exit_date >= ?"
        args = (since,)
    c.execute(sql, args)
    val = c.fetchone()[0]
    conn.close()
    return round(float(val), 2)

def get_options_deployed_capital() -> float:
    """Capital currently deployed: premium paid for debit/LEAP/scalp, margin held for credits.
    Credit margin = max_loss (spread_width - credit) × 100 × qty — what IBKR actually holds."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute("""SELECT COALESCE(SUM(
        CASE WHEN strategy IN ('BULL_PUT_CREDIT','BEAR_CALL_CREDIT')
             THEN ABS(max_loss)
             ELSE premium_paid END
    ), 0.0) FROM options_trades WHERE status='OPEN'""")
    val = c.fetchone()[0]
    conn.close()
    return round(float(val), 2)


def get_closed_options_count():
    conn = get_connection()
    c    = conn.cursor()
    c.execute("SELECT COUNT(*) FROM options_trades WHERE status='CLOSED'")
    n    = c.fetchone()[0]
    conn.close()
    return n


def get_options_today_closed() -> list:
    """Trades closed today — for session P&L display in OPT STATUS."""
    from datetime import date
    today = date.today().isoformat()
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    c    = conn.cursor()
    c.execute('''SELECT id, symbol, strategy, premium_paid, exit_value, return_pct, exit_reason
                 FROM options_trades
                 WHERE status = 'CLOSED' AND exit_date = ?
                 ORDER BY id''', (today,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    # Compute P&L per row (credit sign aware)
    for r in rows:
        paid = r['premium_paid'] or 0
        exv  = r['exit_value']  or 0
        is_credit = r['strategy'] in ('BULL_PUT_CREDIT', 'BEAR_CALL_CREDIT')
        r['pnl'] = round((paid - exv) if is_credit else (exv - paid), 2)
    return rows

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
        SELECT relevance, direction, source, created_at, already_priced_in,
               COALESCE(magnitude, 3)
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
    bull_score = bear_score = 0.0
    high_count   = 0
    bull_high = bear_high = 0
    unique_srcs  = []
    last_at      = ''
    counted      = 0   # rows actually contributing to score (excludes already-priced-in)

    for relevance, direction, source, created_at_str, already_priced_in, magnitude in rows:
        # Skip signals already priced in — they no longer move IV
        if already_priced_in == 'YES':
            continue
        counted += 1
        try:
            created_at = datetime.fromisoformat(created_at_str)
        except Exception:
            created_at = now
        hours_old  = max(0, (now - created_at).total_seconds() / 3600)
        weight     = RELEVANCE_WEIGHT.get(relevance, 0)
        recency    = 0.5 ** (hours_old / 48)
        diversity  = 1.2 if source not in seen_sources else 1.0
        mag_mult   = (magnitude or 3) / 3.0   # magnitude 1-5; 3=baseline, 5=+67%, 1=-67%
        seen_sources.add(source)
        signal_contribution = weight * recency * diversity * mag_mult
        total_score += signal_contribution
        if direction == 'BULLISH':
            bull_score += signal_contribution
        elif direction == 'BEARISH':
            bear_score += signal_contribution
        if relevance == 'HIGH':
            high_count += 1
            if direction == 'BULLISH':
                bull_high += 1
            elif direction == 'BEARISH':
                bear_high += 1
        if source not in unique_srcs:
            unique_srcs.append(source)
        last_at = created_at_str

    score     = min(1.0, total_score)
    lead_dir  = 'BULL' if bull_score > bear_score else ('BEAR' if bear_score > bull_score else 'MIXED')
    net_high  = (bull_high - bear_high) if lead_dir == 'BULL' else (bear_high - bull_high)
    # HIGH requires: score threshold + genuine HIGH signal + net ≥2 high signals in winning direction
    tier      = ('HIGH'   if score >= 0.60 and high_count >= 1 and net_high >= 2
            else 'MEDIUM' if score >= 0.30
            else 'LOW')
    direction = lead_dir
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
          counted, high_count, sources, last_at, now_str))
    conn.commit()
    conn.close()

    tier_order   = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}
    tier_changed = tier_order.get(tier, 0) > tier_order.get(prev_tier or 'LOW', 0)
    return {'score': score, 'tier': tier, 'direction': direction,
            'signal_count': counted, 'high_count': high_count,
            'sources': sources, 'prev_tier': prev_tier, 'tier_changed': tier_changed}


def update_conviction_narrative(symbol: str, narrative: str):
    conn = get_connection()
    c    = conn.cursor()
    c.execute("UPDATE ticker_conviction SET narrative=? WHERE symbol=?",
              (narrative, symbol.upper()))
    conn.commit()
    conn.close()


def update_conviction_iv(symbol: str, iv_rank: float):
    conn = get_connection()
    c    = conn.cursor()
    c.execute("UPDATE ticker_conviction SET iv_rank=? WHERE symbol=?",
              (iv_rank, symbol.upper()))
    conn.commit()
    conn.close()


def get_conviction_leaderboard() -> list:
    """Return all tickers with conviction > 0, ranked by score desc."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute("""
        SELECT symbol, direction, tier, score, signal_count, high_count,
               sources, narrative, iv_rank, last_signal_at, updated_at
        FROM ticker_conviction
        WHERE score > 0
        ORDER BY score DESC, high_count DESC, last_signal_at DESC
    """)
    rows = c.fetchall()
    conn.close()
    keys = ['symbol','direction','tier','score','signal_count','high_count',
            'sources','narrative','iv_rank','last_signal_at','updated_at']
    return [dict(zip(keys, r)) for r in rows]


def get_conviction_detail(symbol: str) -> dict:
    """Return conviction row + recent MEDIUM/HIGH signals for one ticker."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute("""
        SELECT symbol, direction, tier, score, signal_count, high_count,
               sources, narrative, iv_rank, last_signal_at, updated_at
        FROM ticker_conviction WHERE symbol=?
    """, (symbol.upper(),))
    row = c.fetchone()
    if not row:
        conn.close()
        return {}
    keys = ['symbol','direction','tier','score','signal_count','high_count',
            'sources','narrative','iv_rank','last_signal_at','updated_at']
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


# ── KB: Calculator run logging ────────────────────────────────────────────────

def log_calc_run(calc: dict) -> int:
    """Persist every run_calculator() result to opt_calc_log. Returns row id."""
    conn = get_connection()
    c    = conn.cursor()
    now  = datetime.now().isoformat(timespec='seconds')

    eg = calc.get('entry_gates', {})
    ng = calc.get('net_greeks',  {})
    vl = calc.get('velocity',    {})
    mc = calc.get('mc_ev',       {})
    va = calc.get('vol_analysis', {})
    cv = calc.get('conviction',   {})
    lq = calc.get('liquidity',    {})
    eb = calc.get('edge_budget',  {}) or {}

    c.execute('''INSERT INTO opt_calc_log (
        run_at, symbol, underlying, iv_pct, iv_rank, hv30,
        edge_pts, vol_verdict, expected_move, dte, expiry,
        long_strike, short_strike, net_debit, breakeven, breakeven_pct,
        momentum_5d, above_200, conviction_tier, conviction_dir, signal_count,
        vol_gate, tech_gate, conviction_gate, liquidity_gate, momentum_gate,
        gates_pass, verdict, net_delta, net_theta, net_vega,
        velocity_ratio, mc_ev_dollar, mc_win_rate, liq_cost_pct, liq_legacy_max_rel,
        be_move_pct, signal_move_pct, edge_required_pct, edge_budget_ok,
        structure_template, skip_reason
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
    (
        now, calc.get('symbol'), calc.get('underlying'),
        calc.get('current_iv'), calc.get('iv_rank'), calc.get('hv30'),
        va.get('edge_pts'), va.get('verdict'),
        calc.get('em'), calc.get('dte'), calc.get('expiry'),
        calc.get('long_strike'), calc.get('short_strike'),
        calc.get('net_debit'), calc.get('breakeven'), calc.get('breakeven_pct'),
        calc.get('momentum_5d'), 1 if calc.get('above_200') else 0,
        cv.get('tier'), cv.get('direction'),
        (len(cv['signals']) if isinstance(cv.get('signals'), list) else cv.get('signals', 0)),
        1 if eg.get('vol')        else 0,
        1 if eg.get('tech')       else 0,
        1 if eg.get('conviction') else 0,
        1 if eg.get('liquidity')  else 0,
        1 if eg.get('momentum')   else 0,
        eg.get('gates_pass'), eg.get('verdict'),
        ng.get('net_delta'), ng.get('net_theta'), ng.get('net_vega'),
        vl.get('velocity_ratio'),
        mc.get('ev_dollar'), mc.get('win_rate'),
        lq.get('cost_pct'), lq.get('legacy_max_rel'),
        eb.get('breakeven_pct'), eb.get('signal_move_pct'), eb.get('required_pct'),
        (1 if eb.get('ok') else 0) if eb else None,
        calc.get('template'), eg.get('skip_reason'),
    ))
    row_id = c.lastrowid
    conn.commit()
    conn.close()
    return row_id


def save_chain_snapshot(snapshot_date: str, symbol: str, underlying: float | None,
                        expiry: str, right: str, rows: list[dict]):
    """Bulk-insert one expiry's chain (all strikes) for one day.
    rows: [{'strike','bid','ask','last','iv','volume','oi'}, ...].
    INSERT OR IGNORE — re-running the same day/symbol/expiry is a no-op,
    not a duplicate (idempotent, safe to re-run after a partial failure)."""
    if not rows:
        return 0
    conn = get_connection()
    c = conn.cursor()
    c.executemany(
        '''INSERT OR IGNORE INTO options_chain_snapshots
           (snapshot_date, symbol, underlying, expiry, right, strike,
            bid, ask, last, iv, volume, open_interest)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
        [(snapshot_date, symbol, underlying, expiry, right,
          r['strike'], r.get('bid'), r.get('ask'), r.get('last'),
          r.get('iv'), r.get('volume'), r.get('oi'))
         for r in rows])
    n = c.rowcount
    conn.commit()
    conn.close()
    return n


def get_new_strikes(symbol: str, expiry: str, right: str,
                    as_of: str, band_pct: float = 30.0,
                    held_strike: float | None = None) -> list[float]:
    """Strikes present in today's (as_of) snapshot but absent from the prior
    snapshot for the same symbol/expiry/right — 'newly listed since last
    time we looked'. Optionally restricted to a +/-band_pct window around
    the held strike (matches the video's 'nearby strikes' framing)."""
    conn = get_connection()
    c = conn.cursor()
    prior = c.execute(
        '''SELECT MAX(snapshot_date) FROM options_chain_snapshots
           WHERE symbol=? AND expiry=? AND right=? AND snapshot_date<?''',
        (symbol, expiry, right, as_of)).fetchone()[0]
    if not prior:
        conn.close()
        return []   # no prior snapshot to diff against yet
    today_strikes = {r[0] for r in c.execute(
        'SELECT DISTINCT strike FROM options_chain_snapshots '
        'WHERE symbol=? AND expiry=? AND right=? AND snapshot_date=?',
        (symbol, expiry, right, as_of))}
    prior_strikes = {r[0] for r in c.execute(
        'SELECT DISTINCT strike FROM options_chain_snapshots '
        'WHERE symbol=? AND expiry=? AND right=? AND snapshot_date=?',
        (symbol, expiry, right, prior))}
    conn.close()
    new = today_strikes - prior_strikes
    if held_strike:
        lo, hi = held_strike * (1 - band_pct / 100), held_strike * (1 + band_pct / 100)
        new = {s for s in new if lo <= s <= hi}
    return sorted(new)


def log_book_greeks(positions: int, total_value: float | None,
                    net_delta: float | None, net_theta: float | None,
                    net_vega: float | None):
    """Persist one book-level options Greeks snapshot (watchman, each full scan)."""
    conn = get_connection()
    conn.execute(
        'INSERT INTO options_book_greeks (ts, positions, total_value, net_delta, net_theta, net_vega) '
        'VALUES (?,?,?,?,?,?)',
        (datetime.now().isoformat(timespec='seconds'), positions, total_value,
         net_delta, net_theta, net_vega))
    conn.commit()
    conn.close()


def update_calc_action(calc_log_id: int, user_action: str, trade_id: int | None = None):
    """Record user CONFIRM/SKIP and link to options_trade row if executed."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('UPDATE opt_calc_log SET user_action=?, trade_id=? WHERE id=?',
              (user_action, trade_id, calc_log_id))
    conn.commit()
    conn.close()


def log_trade_outcome(trade_id: int, calc_log_id: int | None,
                      predicted_ev: float | None, predicted_wr: float | None,
                      actual_pnl: float, exit_reason: str,
                      days_held: int, thesis_correct: bool | None = None,
                      lesson: str = ''):
    """Called when an options trade closes — captures actual vs predicted EV."""
    conn = get_connection()
    c    = conn.cursor()
    accuracy = None
    if predicted_ev is not None and predicted_ev != 0:
        accuracy = round(abs((actual_pnl - predicted_ev) / abs(predicted_ev)) * 100, 1)
    c.execute('''INSERT INTO opt_trade_outcomes
        (trade_id, calc_log_id, predicted_ev, predicted_wr, actual_pnl,
         exit_reason, days_held, accuracy_pct,
         thesis_correct, lesson, recorded_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
    (
        trade_id, calc_log_id, predicted_ev, predicted_wr, actual_pnl,
        exit_reason, days_held, accuracy,
        1 if thesis_correct else 0 if thesis_correct is not None else None,
        lesson, datetime.now().isoformat(timespec='seconds'),
    ))
    conn.commit()
    conn.close()


def get_kb_summary() -> dict:
    """
    Pull everything needed for a session catch-up brief.
    Returns: open_positions, recent_closed, gate_stats, recent_runs,
             top_conviction, model_accuracy.
    """
    conn = get_connection()
    c    = conn.cursor()

    # Open options positions
    c.execute('''
        SELECT id, symbol, strategy, long_strike, short_strike, expiry,
               net_debit, contracts, entry_date, entry_thesis
        FROM options_trades WHERE status='OPEN'
        ORDER BY entry_date DESC
    ''')
    open_pos = [dict(zip(
        ['id','symbol','strategy','long_strike','short_strike','expiry',
         'net_debit','contracts','entry_date','entry_thesis'], r))
        for r in c.fetchall()]

    # Last 20 closed options trades
    c.execute('''
        SELECT symbol, strategy, long_strike, short_strike, expiry,
               net_debit, return_pct, return_on_risk, exit_reason,
               entry_date, exit_date, lesson
        FROM options_trades WHERE status IN ('WIN','LOSS','EXPIRED','CLOSED')
        ORDER BY exit_date DESC LIMIT 20
    ''')
    recent_closed = [dict(zip(
        ['symbol','strategy','long_strike','short_strike','expiry',
         'net_debit','return_pct','ror','exit_reason',
         'entry_date','exit_date','lesson'], r))
        for r in c.fetchall()]

    # Gate failure stats (last 30 days)
    c.execute('''
        SELECT
            SUM(CASE WHEN vol_gate=0        THEN 1 ELSE 0 END) as vol_fail,
            SUM(CASE WHEN tech_gate=0       THEN 1 ELSE 0 END) as tech_fail,
            SUM(CASE WHEN conviction_gate=0 THEN 1 ELSE 0 END) as conv_fail,
            SUM(CASE WHEN liquidity_gate=0  THEN 1 ELSE 0 END) as liq_fail,
            SUM(CASE WHEN momentum_gate=0   THEN 1 ELSE 0 END) as mom_fail,
            COUNT(*)                                             as total_runs,
            SUM(CASE WHEN verdict='ENTER' OR verdict='ENTER_REDUCED' THEN 1 ELSE 0 END) as enters,
            SUM(CASE WHEN verdict='SKIP'  THEN 1 ELSE 0 END)    as skips
        FROM opt_calc_log
        WHERE run_at >= datetime('now', '-30 days')
    ''')
    row = c.fetchone()
    gate_stats = dict(zip(
        ['vol_fail','tech_fail','conv_fail','liq_fail','mom_fail',
         'total_runs','enters','skips'], row)) if row else {}

    # Recent calc runs (last 14 days)
    c.execute('''
        SELECT run_at, symbol, underlying, iv_pct, hv30, edge_pts, vol_verdict,
               gates_pass, verdict, mc_ev_dollar, mc_win_rate, user_action
        FROM opt_calc_log
        WHERE run_at >= datetime('now', '-14 days')
        ORDER BY run_at DESC LIMIT 30
    ''')
    recent_runs = [dict(zip(
        ['run_at','symbol','underlying','iv_pct','hv30','edge_pts','vol_verdict',
         'gates_pass','verdict','mc_ev','mc_wr','user_action'], r))
        for r in c.fetchall()]

    # Top conviction (HIGH tier BULLISH)
    c.execute('''
        SELECT symbol, tier, score, signal_count, high_count,
               iv_rank, narrative, last_signal_at
        FROM ticker_conviction
        WHERE tier IN ('HIGH','MEDIUM') AND direction='BULL'
        ORDER BY score DESC LIMIT 10
    ''')
    top_conviction = [dict(zip(
        ['symbol','tier','score','signals','highs','ivr','narrative','last_at'], r))
        for r in c.fetchall()]

    # Model accuracy (closed trades with outcomes)
    c.execute('''
        SELECT COUNT(*) as n,
               AVG(actual_pnl)    as avg_actual,
               AVG(predicted_ev)  as avg_predicted,
               AVG(accuracy_pct)  as avg_accuracy,
               SUM(CASE WHEN actual_pnl > 0 THEN 1 ELSE 0 END) as wins,
               AVG(predicted_wr)  as avg_predicted_wr
        FROM opt_trade_outcomes
    ''')
    row = c.fetchone()
    model_accuracy = dict(zip(
        ['n','avg_actual','avg_predicted','avg_accuracy','wins','avg_predicted_wr'],
        row)) if row else {}

    conn.close()
    return {
        'open_positions':  open_pos,
        'recent_closed':   recent_closed,
        'gate_stats':      gate_stats,
        'recent_runs':     recent_runs,
        'top_conviction':  top_conviction,
        'model_accuracy':  model_accuracy,
    }


# ── Auto-suggest helpers ──────────────────────────────────────────────────────

def log_suggestion(symbol: str, conviction_score: float,
                   signal_count: int) -> int:
    """Called by news_engine when a BULLISH HIGH alert fires. Returns row id."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''INSERT INTO opt_suggestions
        (symbol, suggested_at, conviction_score, signal_count, status)
        VALUES (?, ?, ?, ?, 'PENDING')''',
        (symbol.upper(), datetime.now().isoformat(timespec='seconds'),
         conviction_score, signal_count))
    row_id = c.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_suggestions_today_count() -> int:
    """How many auto-suggestions have been queued today."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''SELECT COUNT(*) FROM opt_suggestions
                 WHERE date(suggested_at) = date('now')''')
    n = c.fetchone()[0]
    conn.close()
    return n


def get_pending_suggestions() -> list:
    """Return PENDING suggestions not yet processed by options_trader."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''SELECT id, symbol, conviction_score, signal_count
                 FROM opt_suggestions
                 WHERE status = 'PENDING'
                 ORDER BY suggested_at ASC''')
    rows = c.fetchall()
    conn.close()
    return [{'id': r[0], 'symbol': r[1],
             'conviction_score': r[2], 'signal_count': r[3]}
            for r in rows]


def update_suggestion_calc(sug_id: int, calc: dict, calc_log_id: int | None):
    """Store calculator result on a suggestion row."""
    conn = get_connection()
    c    = conn.cursor()
    eg   = calc.get('entry_gates', {})
    mc   = calc.get('mc_ev', {})
    c.execute('''UPDATE opt_suggestions SET
        calc_log_id=?, verdict=?, mc_ev_dollar=?, mc_win_rate=?,
        gates_pass=?, long_strike=?, short_strike=?, expiry=?,
        net_debit=?, underlying_at_suggest=?
        WHERE id=?''',
    (
        calc_log_id, eg.get('verdict'),
        mc.get('ev_dollar'), mc.get('win_rate'),
        eg.get('gates_pass'),
        calc.get('long_strike'), calc.get('short_strike'),
        calc.get('expiry'), calc.get('net_debit'),
        calc.get('underlying'), sug_id,
    ))
    conn.commit()
    conn.close()


def update_suggestion_status(sug_id: int, status: str):
    """Transition status: PENDING → SENT / NO_TRADE / ERROR."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('UPDATE opt_suggestions SET status=? WHERE id=?', (status, sug_id))
    conn.commit()
    conn.close()


def update_suggestion_decision(sug_id: int, decision: str,
                                trade_id: int | None = None):
    """Record user CONFIRMED / SKIPPED / EXPIRED."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''UPDATE opt_suggestions SET
        user_decision=?, decided_at=?, trade_id=?, status=?
        WHERE id=?''',
        (decision, datetime.now().isoformat(timespec='seconds'),
         trade_id, decision, sug_id))
    conn.commit()
    conn.close()


def fill_whatif_prices():
    """
    Nightly: for SKIPped/EXPIRED suggestions, fetch current price and compute
    what the P&L would have been had the user confirmed the trade.
    Fills underlying_7d (if 7+ days old) and underlying_14d (if 14+ days old).

    Spread type detection from stored fields (no strategy column needed):
      net_debit < 0            → credit spread (credit received = -net_debit)
      net_debit > 0, ls < ss   → BULL_SPREAD (long lower call / short higher call)
      net_debit > 0, ls > ss   → BEAR_PUT_SPREAD (long higher put / short lower put)
    """
    import yfinance as yf
    conn = get_connection()
    c    = conn.cursor()

    c.execute('''SELECT id, symbol, suggested_at, long_strike, short_strike,
                        net_debit, underlying_at_suggest,
                        underlying_7d, underlying_14d
                 FROM opt_suggestions
                 WHERE (status IN ('NO_TRADE','EXPIRED')
                        OR user_decision IN ('SKIPPED','EXPIRED','NO_TRADE'))
                   AND trade_id IS NULL
                   AND long_strike IS NOT NULL
                   AND (underlying_7d IS NULL OR underlying_14d IS NULL)''')
    rows = c.fetchall()

    today = date.today()
    _hist_cache: dict = {}

    def _close_at(sym: str, target: date):
        """Close on the first trading day at/after target (None if not yet reached)."""
        if target > today:
            return None
        if sym not in _hist_cache:
            try:
                _hist_cache[sym] = yf.Ticker(sym).history(period='4mo')['Close']
            except Exception:
                _hist_cache[sym] = None
        ser = _hist_cache[sym]
        if ser is None or len(ser) == 0:
            return None
        after = ser[ser.index.date >= target]
        return float(after.iloc[0]) if len(after) else None

    for row in rows:
        sid, sym, sug_at, ls, ss, nd, entry_price, u7, u14 = row
        try:
            sug_date = date.fromisoformat(sug_at[:10])

            if u7 is None:
                u7 = _close_at(sym, sug_date + timedelta(days=7))
                if u7 is not None:
                    c.execute('UPDATE opt_suggestions SET underlying_7d=? WHERE id=?',
                              (u7, sid))
            if u14 is None:
                u14 = _close_at(sym, sug_date + timedelta(days=14))
                if u14 is not None:
                    c.execute('UPDATE opt_suggestions SET underlying_14d=? WHERE id=?',
                              (u14, sid))

            current = u14 if u14 is not None else u7
            if current is None:
                continue

            if ls and ss and nd is not None and entry_price and current:
                if nd < 0:
                    # Credit spread: P&L = credit_received - buyback_cost (spread intrinsic)
                    net_credit = -nd
                    if ls > ss:
                        # BULL_PUT_CREDIT: sold higher put (ls), bought lower put (ss)
                        width      = ls - ss
                        spread_val = max(0.0, min(ls - current, width))
                    else:
                        # BEAR_CALL_CREDIT: sold lower call (ls), bought higher call (ss)
                        width      = ss - ls
                        spread_val = max(0.0, min(current - ls, width))
                    whatif_pnl = round((net_credit - spread_val) * 100, 2)
                    whatif_pct = round(whatif_pnl / (net_credit * 100) * 100, 1) if net_credit else 0
                elif ls < ss:
                    # BULL_SPREAD: long lower call (ls), short higher call (ss)
                    width      = ss - ls
                    spread_val = max(0.0, min(current - ls, width))
                    whatif_pnl = round((spread_val - nd) * 100, 2)
                    whatif_pct = round((spread_val - nd) / nd * 100, 1) if nd else 0
                else:
                    # BEAR_PUT_SPREAD: long higher put (ls), short lower put (ss)
                    width      = ls - ss
                    spread_val = max(0.0, min(ls - current, width))
                    whatif_pnl = round((spread_val - nd) * 100, 2)
                    whatif_pct = round((spread_val - nd) / nd * 100, 1) if nd else 0

                c.execute('''UPDATE opt_suggestions
                             SET whatif_pnl=?, whatif_return_pct=? WHERE id=?''',
                          (whatif_pnl, whatif_pct, sid))
        except Exception:
            continue

    conn.commit()
    conn.close()


# ── Scan log operations ───────────────────────────────────────────────────────

_COMPONENT_PTS = re.compile(r'([+-])(\d+)(?=[,)\s]|$)')
# Strip every varying value out of a reason string so the same component always yields the
# same key: numbers, %, x-multiples, $ prices, ticker-ish ALLCAPS prefixes and the ✓ marker.
_COMPONENT_STRIP = re.compile(r'[-+]?\d+(?:\.\d+)?\s*(?:%|x|m\b|min\b)?|\$|✓|→|:')


def _canon_component(txt):
    """'2.6x vol' -> 'vol' | '+5.2% today' -> 'today' | 'RS +4.1% vs SPY' -> 'RS vs SPY'.

    Prose-parsing is fragile by nature; it is used here because grade_setup() returns its
    explanation as free text and changing that signature would touch every caller. If anyone
    reworks grade_setup, have it emit a {component: points} dict directly and delete this.
    """
    t = txt.split('(')[0]
    t = _COMPONENT_STRIP.sub(' ', t)
    t = ' '.join(t.replace('—', ' ').split())
    return t.strip(' -')[:40]


def _components_json(reasons):
    """grade_setup()'s reason strings -> compact JSON {canonical component: points or None}.

    Keeps the explicit point value where the reason states one, and the canonical label
    otherwise -- enough to regress each component against the forward outcome later. Never
    raises: a logging helper must not be able to kill a scan.
    """
    if not reasons:
        return None
    try:
        out = {}
        for i, raw in enumerate(reasons):
            txt = str(raw)
            key = _canon_component(txt) or f'r{i}'
            m = _COMPONENT_PTS.search(txt)
            out[key] = int(m.group(1) + m.group(2)) if m else None
        return json.dumps(out, separators=(',', ':'))[:4000]
    except Exception:
        return None


def log_scan_candidate(scan_date, scan_time, symbol, direction, regime,
                       price, grade, score, skip_reason,
                       vol_ratio, rsi, intra_chg, sector,
                       is_catalyst=False, entered=False, entry_trade_id=None,
                       burst_age_min=None, consec_new_highs=None,
                       today_hod=None, price_vs_hod_pct=None, reasons=None,
                       ret_5d=None, atr_pct=None, is_thrust=None, today_gain=None):
    """Log every candidate — entered, benched, or skipped — with full energy context.

    `reasons` is grade_setup()'s own explanation list. It is parsed into a
    {component: points} JSON blob so each hand-assigned weight can later be scored against
    the forward outcome. Without it the score is a single opaque number and the 15 weights
    inside it are untunable -- which is the state this book was in until Sep 18 2026.
    """
    conn = get_connection()
    c    = conn.cursor()
    c.execute('''INSERT INTO scan_log
        (scan_date, scan_time, symbol, direction, regime, price,
         grade, score, skip_reason, vol_ratio, rsi, intra_chg, sector,
         is_catalyst, entered, entry_trade_id,
         burst_age_min, consec_new_highs, today_hod, price_vs_hod_pct, score_components,
         ret_5d, atr_pct, is_thrust, today_gain)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (scan_date, scan_time, symbol, direction, regime, price,
         grade, score, skip_reason, vol_ratio, rsi, intra_chg, sector,
         int(is_catalyst), int(entered), entry_trade_id,
         burst_age_min if burst_age_min != 999 else None,
         consec_new_highs, today_hod, price_vs_hod_pct,
         _components_json(reasons),
         ret_5d, atr_pct, None if is_thrust is None else int(is_thrust), today_gain))
    row_id = c.lastrowid
    conn.commit()
    conn.close()
    return row_id


def enrich_scan_log():
    """
    Nightly job: enrich scan_log rows with actual outcomes.
    Per row: actual_day_pct, actual_day_high_pct (full day), actual_30m_pct,
    actual_60m_pct (forward returns from scan time using bars_5m where available).
    """
    import yfinance as yf, sqlite3 as _sq3
    import os as _os

    conn = get_connection()
    c    = conn.cursor()
    # LIMIT raised 2000→6000 Jul 25 2026: at the 241-symbol universe a single
    # day generates ~2,400 rows; with newest-first ordering the old cap left
    # ~400 rows/day permanently unenriched (new days always outranked the
    # backlog), silently biasing the Book Health Selector's sample.
    c.execute('''SELECT id, symbol, scan_date, scan_time FROM scan_log
                 WHERE enriched = 0
                 ORDER BY scan_date DESC LIMIT 6000''')
    rows = c.fetchall()
    conn.close()

    if not rows:
        return 0

    # Open bars_5m for forward return lookup
    _bars_db_path = _os.path.join(_os.path.dirname(__file__), 'market_data.db')
    try:
        _mconn = _sq3.connect(_bars_db_path)
    except Exception:
        _mconn = None

    from collections import defaultdict
    groups = defaultdict(list)
    for row_id, symbol, scan_date, scan_time in rows:
        groups[(symbol, scan_date)].append((row_id, scan_time))

    updated = 0
    for (symbol, scan_date), id_times in groups.items():
        try:
            end_date = (date.fromisoformat(scan_date) + timedelta(days=1)).isoformat()
            hist = yf.Ticker(symbol).history(start=scan_date, end=end_date, interval='1d')
            if hist.empty:
                conn = get_connection()
                conn.execute('UPDATE scan_log SET enriched=1 WHERE id IN (%s)'
                             % ','.join('?'*len(id_times)), [r for r,_ in id_times])
                conn.commit(); conn.close(); continue

            open_p  = float(hist['Open'].iloc[0])
            close_p = float(hist['Close'].iloc[0])
            high_p  = float(hist['High'].iloc[0])
            if open_p <= 0: continue

            day_pct      = (close_p - open_p) / open_p * 100
            day_high_pct = (high_p  - open_p) / open_p * 100

            conn = get_connection()
            for row_id, scan_time in id_times:
                r30 = r60 = None
                # Forward returns from bars_5m if available
                if _mconn and scan_time:
                    try:
                        from datetime import datetime as _dt, timedelta as _td
                        h, m = int(scan_time[:2]), int(scan_time[3:5])
                        # Correct ET→UTC: EDT (UTC-4) Apr-Oct, EST (UTC-5) Nov-Mar
                        utc_off = 4 if 4 <= int(scan_date[5:7]) <= 10 else 5
                        base = _dt.strptime(f"{scan_date} {h:02d}:{m:02d}", "%Y-%m-%d %H:%M")
                        scan_ts = (base + _td(hours=utc_off)).strftime("%Y-%m-%d %H:%M")
                        ts_30   = (base + _td(hours=utc_off, minutes=30)).strftime("%Y-%m-%d %H:%M")
                        ts_60   = (base + _td(hours=utc_off, minutes=60)).strftime("%Y-%m-%d %H:%M")
                        p_scan = _mconn.execute(
                            "SELECT close FROM bars_5m WHERE symbol=? AND ts_utc>=? LIMIT 1",
                            (symbol, scan_ts)).fetchone()
                        p_30 = _mconn.execute(
                            "SELECT close FROM bars_5m WHERE symbol=? AND ts_utc>=? LIMIT 1",
                            (symbol, ts_30)).fetchone()
                        p_60 = _mconn.execute(
                            "SELECT close FROM bars_5m WHERE symbol=? AND ts_utc>=? LIMIT 1",
                            (symbol, ts_60)).fetchone()
                        if p_scan and p_scan[0] and p_30 and p_30[0]:
                            r30 = round((float(p_30[0])/float(p_scan[0])-1)*100, 3)
                        if p_scan and p_scan[0] and p_60 and p_60[0]:
                            r60 = round((float(p_60[0])/float(p_scan[0])-1)*100, 3)
                    except Exception:
                        pass

                conn.execute('''UPDATE scan_log
                                SET actual_day_pct=?, actual_day_high_pct=?,
                                    actual_close=?, actual_30m_pct=?,
                                    actual_60m_pct=?, enriched=1
                                WHERE id=?''',
                             (day_pct, day_high_pct, close_p, r30, r60, row_id))

            conn.commit(); conn.close()
            updated += len(id_times)

        except Exception:
            try: conn.close()
            except Exception: pass
            continue

    if _mconn:
        try: _mconn.close()
        except: pass

    return updated


def backfill_energy_signals():
    """
    Backfill consec_new_highs, today_hod, price_vs_hod_pct, actual_30m_pct,
    actual_60m_pct for all historical scan_log rows that are missing them.
    Uses market_data.db bars_5m — covers the 62 trading days already seeded.
    Safe to call multiple times (skips rows where consec_new_highs IS NOT NULL).
    """
    import sqlite3 as _sq3, os as _os
    from datetime import datetime as _dt, timedelta as _td

    bars_path = _os.path.join(_os.path.dirname(__file__), 'market_data.db')
    try:
        mconn = _sq3.connect(bars_path)
    except Exception as e:
        return 0, f"Cannot open market_data.db: {e}"

    conn = get_connection()
    rows = conn.execute("""
        SELECT id, symbol, scan_date, scan_time, price
        FROM scan_log
        WHERE consec_new_highs IS NULL AND scan_date >= '2026-03-01'
        ORDER BY scan_date DESC LIMIT 5000
    """).fetchall()

    updated = 0
    for row_id, symbol, scan_date, scan_time, price in rows:
        try:
            if not scan_time: continue
            h, m = int(scan_time[:2]), int(scan_time[3:5])
            # Correct ET→UTC: EDT (UTC-4) Apr-Oct, EST (UTC-5) Nov-Mar
            utc_off   = 4 if 4 <= int(scan_date[5:7]) <= 10 else 5
            base      = _dt.strptime(f"{scan_date} {h:02d}:{m:02d}", "%Y-%m-%d %H:%M")
            rth_start = f"{scan_date} {9 + utc_off:02d}:30"   # 9:30 ET → UTC
            scan_ts   = (base + _td(hours=utc_off)).strftime("%Y-%m-%d %H:%M")

            # Bars from 9:30 to scan time
            pre_bars = mconn.execute(
                "SELECT high, close, volume FROM bars_5m "
                "WHERE symbol=? AND ts_utc>=? AND ts_utc<=? ORDER BY ts_utc",
                (symbol, rth_start, scan_ts)).fetchall()

            if len(pre_bars) < 2: continue

            hod    = max(float(r[0]) for r in pre_bars)
            p_at_scan = float(pre_bars[-1][1])
            # pct below HOD at scan time (negative = below, 0 = at HOD)
            pvh    = round((p_at_scan - hod) / hod * 100, 2) if hod else None

            # consec_new_highs: consecutive new HODs in last 5 bars
            # baseline = HOD of all bars before the last 5 (not 0.0)
            last5 = pre_bars[-5:]
            prior = pre_bars[:-5]
            rmax  = max(float(r[0]) for r in prior) if prior else 0.0
            consec = 0
            for r in last5:
                if float(r[0]) > rmax: rmax = float(r[0]); consec += 1
                else: consec = 0

            # Forward returns — use timedelta to correctly advance hour boundary
            ts_30 = (base + _td(hours=utc_off, minutes=30)).strftime("%Y-%m-%d %H:%M")
            ts_60 = (base + _td(hours=utc_off, minutes=60)).strftime("%Y-%m-%d %H:%M")
            p30 = mconn.execute(
                "SELECT close FROM bars_5m WHERE symbol=? AND ts_utc>? ORDER BY ts_utc LIMIT 1",
                (symbol, ts_30)).fetchone()
            p60 = mconn.execute(
                "SELECT close FROM bars_5m WHERE symbol=? AND ts_utc>? ORDER BY ts_utc LIMIT 1",
                (symbol, ts_60)).fetchone()
            r30 = round((float(p30[0])/p_at_scan-1)*100,3) if p30 and p_at_scan else None
            r60 = round((float(p60[0])/p_at_scan-1)*100,3) if p60 and p_at_scan else None

            conn.execute("""
                UPDATE scan_log
                SET consec_new_highs=?, today_hod=?, price_vs_hod_pct=?,
                    actual_30m_pct=?, actual_60m_pct=?
                WHERE id=?
            """, (consec, round(hod,2), pvh, r30, r60, row_id))

            updated += 1
            if updated % 500 == 0:
                conn.commit()

        except Exception:
            continue

    conn.commit(); conn.close(); mconn.close()
    return updated, "OK"


def backfill_scan_log_from_trades():
    """
    One-time backfill: seed scan_log with all historical trades from the trades table.
    These are entered=1 rows — we know we traded them, we just don't have the SKIP rows.
    Safe to call multiple times (skips already-backfilled rows via scan_date+symbol dedup).
    """
    conn = get_connection()
    c    = conn.cursor()

    c.execute('''SELECT id, symbol, entry_date, entry_time, side, entry_price,
                        volume_ratio, rsi_at_entry, sector, confidence, setup_type
                 FROM trades
                 WHERE status IN ('WIN', 'LOSS', 'CLOSED', 'OPEN')
                 AND setup_type != 'RECONCILED'
                 ORDER BY entry_date, entry_time''')
    trades = c.fetchall()

    inserted = 0
    for (tid, symbol, edate, etime, side, price,
         vol_ratio, rsi, sector, confidence, setup_type) in trades:
        # Skip if already in scan_log
        c.execute('SELECT 1 FROM scan_log WHERE entry_trade_id=?', (tid,))
        if c.fetchone():
            continue

        direction = side if side in ('LONG', 'SHORT') else 'LONG'
        grade = 'A+' if (confidence or 0) >= 80 else ('A' if (confidence or 0) >= 65 else 'B')

        c.execute('''INSERT INTO scan_log
            (scan_date, scan_time, symbol, direction, regime, price,
             grade, score, skip_reason, vol_ratio, rsi, intra_chg, sector,
             is_catalyst, entered, entry_trade_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (edate, etime or '10:30', symbol, direction, 'UNKNOWN', price,
             grade, int(confidence or 0), None, vol_ratio, rsi, None, sector,
             0, 1, tid))
        inserted += 1

    conn.commit()
    conn.close()
    return inserted



# ══════════════════════════════════════════════════════════════════════════════
# CONTROL QUEUE — how the dashboard asks a trading process to close something
# ══════════════════════════════════════════════════════════════════════════════
#
# WHY A QUEUE AND NOT A DIRECT ORDER (Sep 22 2026).
#
# The dashboard is a separate Flask process. It can reach the bridge, so it COULD
# place a closing order itself. It must not, and the reason is written all over
# this codebase's incident history — four separate incidents, one shape:
#
#   Jul 20-21 2026  options/watchman.py  a retry loop raced its own fill      -> 15 phantom shorts
#   Aug  3 2026     manual FUT CLOSE     ran outside the launch env           -> closed IBKR, booked TC
#   Sep 10-11 2026  factory/live/*.py    re-sent a sell on an ambiguous read  -> -396 shares
#   Sep 22 2026     auto_trader reconcile raced a pre-market exit             -> META 2 long -> 12 short
#
# Every one is a SECOND ACTOR acting on a position a FIRST ACTOR believes it owns.
# A dashboard that posts orders directly is that second actor, and it is worse than
# the others, because the owning processes hold state the dashboard cannot see or
# update: auto_trader's open_positions/session_high, the futures traders' in-memory
# peak (_session_high, on which the Reversal Exit depends), and — worst — london_trader's
# entire position, which lives ONLY in a module global. Flattening London at the broker
# from outside would leave its 15s monitor managing a phantom and closing it again.
#
# So the dashboard WRITES A REQUEST and the owning process EXECUTES IT, through the
# same close path its Telegram command already uses. The dashboard never places an order.
#
# TWO PROPERTIES THIS TABLE MUST HAVE, both learned the hard way:
#
#  1. CLAIMING IS ATOMIC. The IBKR and TC futures traders poll the SAME queue, 10s
#     apart, in two processes. `claim_control_requests` claims by UPDATE ... WHERE
#     status='PENDING' and trusts rowcount, so exactly one process can win a row.
#     Futures rows additionally carry account_mode so TC can never execute IBKR's request.
#
#  2. REQUESTS EXPIRE. A request that has sat unclaimed longer than REQUEST_TTL_SEC is
#     dead, not pending. Without this, a close queued while a trader is down would fire
#     whenever it next started — possibly hours later, into a different market, against a
#     position the user has long since dealt with. Same reasoning as ORPHAN_GRACE_S in
#     auto_trader: time-since-request is information, and ignoring it is how stale
#     intentions become live orders.

CONTROL_TARGETS = {
    'equity',        # auto_trader's own book ('trades')  — drained by auto_trader
    'wave_rider',    # factory/live/wave_rider.py         — drained by its own one-shot
    'contrarian',    # factory/live/contrarian.py         — drained by its own one-shot
    'clockwork',     # factory/live/overnight.py          — drained by its own one-shot
    'options',       # options/options_trader.py          — drained by options_trader
    'futures_ny',    # futures/{futures,tc}_trader.py     — drained per account_mode
    'london',        # futures/london_trader.py           — drained by run_monitor
}
CONTROL_ACTIONS = {'CLOSE_ONE', 'CLOSE_ALL'}

# How long a request stays actionable. Five minutes is long enough to survive a slow
# poll, a service restart or a brief bridge outage, and short enough that nothing the
# user asked for in one market can execute in another.
REQUEST_TTL_SEC = 300


def init_control_queue(conn=None):
    """Idempotent — safe to call from every process at startup, like the ALTERs above."""
    own = conn is None
    conn = conn or get_connection()
    conn.execute('''CREATE TABLE IF NOT EXISTS control_queue (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at    TEXT NOT NULL,
        target        TEXT NOT NULL,
        account_mode  TEXT,
        action        TEXT NOT NULL,
        book          TEXT,
        symbol        TEXT,
        trade_id      INTEGER,
        source        TEXT,
        requested_by  TEXT,
        status        TEXT NOT NULL DEFAULT 'PENDING',
        claimed_at    TEXT,
        completed_at  TEXT,
        result        TEXT
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_control_pending '
                 'ON control_queue(status, target)')
    if own:
        conn.commit()
        conn.close()


def queue_control_request(target, action, symbol=None, trade_id=None, book=None,
                          account_mode=None, source='dashboard', requested_by=None):
    """Record an intent to close. Returns the request id. Places no order."""
    if target not in CONTROL_TARGETS:
        raise ValueError(f'unknown control target: {target}')
    if action not in CONTROL_ACTIONS:
        raise ValueError(f'unknown control action: {action}')
    init_control_queue()
    conn = get_connection()
    cur = conn.execute(
        'INSERT INTO control_queue (created_at, target, account_mode, action, book, '
        'symbol, trade_id, source, requested_by, status) '
        "VALUES (?,?,?,?,?,?,?,?,?,'PENDING')",
        (datetime.now().isoformat(timespec='seconds'), target, account_mode, action,
         book, (symbol or '').upper() or None, trade_id, source, requested_by))
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def claim_control_requests(target, account_mode=None):
    """Atomically take ownership of every actionable request for this target.

    Returns claimed rows as dicts. Anything past REQUEST_TTL_SEC is marked EXPIRED
    instead of returned — see the note at the top of this section for why a stale
    request must never execute.

    The claim is `UPDATE ... WHERE id=? AND status='PENDING'` with a rowcount check,
    which is atomic under SQLite's write lock. That matters because the IBKR and TC
    futures traders poll this same table from two processes.
    """
    init_control_queue()
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    now = datetime.now()
    claimed = []
    try:
        sql = "SELECT * FROM control_queue WHERE status='PENDING' AND target=?"
        args = [target]
        if account_mode is not None:
            # A NULL account_mode means "whoever owns this target"; a set one is scoped.
            sql += ' AND (account_mode IS NULL OR account_mode=?)'
            args.append(account_mode)
        for row in conn.execute(sql + ' ORDER BY id', args).fetchall():
            row = dict(row)
            try:
                age = (now - datetime.fromisoformat(row['created_at'])).total_seconds()
            except Exception:
                age = REQUEST_TTL_SEC + 1      # unparseable timestamp: treat as stale
            if age > REQUEST_TTL_SEC:
                conn.execute(
                    "UPDATE control_queue SET status='EXPIRED', completed_at=?, "
                    "result=? WHERE id=? AND status='PENDING'",
                    (now.isoformat(timespec='seconds'),
                     f'expired unclaimed after {int(age)}s (limit {REQUEST_TTL_SEC}s)',
                     row['id']))
                continue
            cur = conn.execute(
                "UPDATE control_queue SET status='CLAIMED', claimed_at=? "
                "WHERE id=? AND status='PENDING'",
                (now.isoformat(timespec='seconds'), row['id']))
            if cur.rowcount == 1:          # we won it; another poller cannot
                claimed.append(row)
        conn.commit()
    finally:
        conn.close()
    return claimed


def complete_control_request(request_id, status, result=''):
    """Close out a claimed request. status: 'DONE' | 'FAILED'."""
    conn = get_connection()
    conn.execute(
        'UPDATE control_queue SET status=?, completed_at=?, result=? WHERE id=?',
        (status, datetime.now().isoformat(timespec='seconds'), str(result)[:500], request_id))
    conn.commit()
    conn.close()


def get_control_requests(ids=None, limit=25):
    """Read back outcomes — used by the dashboard to report what actually happened."""
    init_control_queue()
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    try:
        if ids:
            marks = ','.join('?' * len(ids))
            rows = conn.execute(
                f'SELECT * FROM control_queue WHERE id IN ({marks}) ORDER BY id',
                list(ids)).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM control_queue ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == '__main__':
    init_db()
    print("Database ready at:", DB_PATH)
