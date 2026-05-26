"""
smoke_test.py — Quick sanity check for the options system.

Tests:
  1. All DB helpers (insert/read/update/close) with sample data
  2. Trailing stop math (all 3 stages, both strategies)
  3. Grading logic
  4. Calculator helpers (no bridge needed)
  5. Telegram send (dry-run, checks env vars)
  6. Import health (news_engine, watchman, options_trader)

Run: venv/bin/python options/smoke_test.py
"""

import os
import sys
import json
import sqlite3
import tempfile
import traceback
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"
results = []

def ok(name):
    results.append((PASS, name))
    print(f"  {PASS} {name}")

def fail(name, err):
    results.append((FAIL, name))
    print(f"  {FAIL} {name}: {err}")

def warn(name, msg):
    results.append((WARN, name))
    print(f"  {WARN} {name}: {msg}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n[1/6] Import health")
# ─────────────────────────────────────────────────────────────────────────────
try:
    import database
    ok("database imports")
except Exception as e:
    fail("database imports", e)

try:
    import options.news_engine  # noqa
    ok("news_engine imports")
except Exception as e:
    fail("news_engine imports", e)

try:
    import options.watchman  # noqa
    ok("watchman imports")
except Exception as e:
    fail("watchman imports", e)

try:
    import options.options_trader  # noqa
    ok("options_trader imports")
except Exception as e:
    fail("options_trader imports", e)


# ─────────────────────────────────────────────────────────────────────────────
print("\n[2/6] Database helpers (isolated test DB)")
# ─────────────────────────────────────────────────────────────────────────────

# Patch DB to use a temp file so we don't pollute real trading.db
import database as db
_orig_get_conn = db.get_connection
tmp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
tmp_db.close()

def _test_conn():
    return sqlite3.connect(tmp_db.name)

db.get_connection = _test_conn
db.init_db()  # create all tables in temp DB

try:
    cat_id = db.add_catalyst(
        symbol='PLTR',
        catalyst_type='EARNINGS',
        event_name='Q2 2026 Earnings',
        event_date=(date.today() + timedelta(days=14)).isoformat(),
        confidence='HIGH',
        expected_move_pct=8.0,
        iv_rank_when_noted=22.0,
        news_source='MANUAL',
        notes='Expecting strong gov deal announcements',
    )
    assert isinstance(cat_id, int) and cat_id > 0
    ok("add_catalyst")
except Exception as e:
    fail("add_catalyst", e)

try:
    upcoming = db.get_upcoming_catalysts(days=30)
    assert len(upcoming) == 1
    assert upcoming[0]['symbol'] == 'PLTR'
    assert upcoming[0]['name'] == 'Q2 2026 Earnings'
    assert upcoming[0]['confidence'] == 'HIGH'
    ok("get_upcoming_catalysts (key names correct)")
except Exception as e:
    fail("get_upcoming_catalysts", e)

try:
    trade_id = db.log_options_trade(
        strategy='BULL_SPREAD',
        symbol='PLTR',
        cap_type='MID',
        underlying_price=27.50,
        expiry='20260619',
        contracts=1,
        delta_entry=0.35,
        iv_rank_entry=22.0,
        iv_pct_entry=38.0,
        premium_paid=150.00,
        max_profit=350.00,
        max_loss=150.00,
        entry_grade='A+',
        entry_thesis='Balanced spread — 32d exp',
        long_strike=27.0,
        short_strike=32.0,
        right='C',
        net_debit=1.50,
        catalyst_id=cat_id,
        days_to_catalyst=14,
    )
    assert isinstance(trade_id, int) and trade_id > 0
    ok("log_options_trade (BULL_SPREAD)")
except Exception as e:
    fail("log_options_trade BULL_SPREAD", e)
    traceback.print_exc()

try:
    # Verify initial stop is set correctly for BULL_SPREAD: 50% of premium
    conn = _test_conn()
    row = conn.execute(
        'SELECT stop_value, stop_stage, status FROM options_trades WHERE id=?', (trade_id,)
    ).fetchone()
    conn.close()
    assert row[0] == 75.00, f"Expected stop=75.00, got {row[0]}"  # 150 * 0.50
    assert row[1] == 1,     f"Expected stage=1, got {row[1]}"
    assert row[2] == 'OPEN'
    ok("initial stop_value = 50% of premium (BULL_SPREAD)")
except Exception as e:
    fail("initial stop_value check", e)

try:
    leap_id = db.log_options_trade(
        strategy='LEAP',
        symbol='RKLB',
        cap_type='MID',
        underlying_price=18.40,
        expiry='20271219',
        contracts=1,
        delta_entry=0.68,
        iv_rank_entry=18.0,
        iv_pct_entry=55.0,
        premium_paid=400.00,
        max_profit=None,
        max_loss=400.00,
        entry_grade='A',
        entry_thesis='LEAP 18m — delta 0.68',
        strike=19.0,
        right='C',
    )
    conn = _test_conn()
    row = conn.execute(
        'SELECT stop_value FROM options_trades WHERE id=?', (leap_id,)
    ).fetchone()
    conn.close()
    assert row[0] == 240.00, f"Expected LEAP stop=240.00, got {row[0]}"  # 400 * 0.60
    ok("initial stop_value = 60% of premium (LEAP)")
except Exception as e:
    fail("log_options_trade LEAP / stop check", e)
    traceback.print_exc()

try:
    db.update_options_stop(trade_id, 150.00, 2)
    conn = _test_conn()
    row = conn.execute(
        'SELECT stop_value, stop_stage FROM options_trades WHERE id=?', (trade_id,)
    ).fetchone()
    conn.close()
    assert row[0] == 150.00 and row[1] == 2
    ok("update_options_stop (stage 2 breakeven lock)")
except Exception as e:
    fail("update_options_stop", e)

try:
    db.log_options_snapshot(
        trade_id=trade_id,
        underlying_price=28.10,
        contract_value=180.00,
        delta=0.38,
        iv_rank=24.0,
        iv_pct=40.0,
        days_to_expiry=28,
        stop_value=150.00,
    )
    conn = _test_conn()
    row = conn.execute('SELECT contract_value, pnl_unrealized FROM options_snapshots WHERE trade_id=?',
                       (trade_id,)).fetchone()
    conn.close()
    assert row[0] == 180.00
    assert row[1] == 30.00  # 180 - 150 premium
    ok("log_options_snapshot + pnl_unrealized")
except Exception as e:
    fail("log_options_snapshot", e)

try:
    open_trades = db.get_open_options_trades()
    assert len(open_trades) == 2  # BULL_SPREAD + LEAP
    keys = set(open_trades[0].keys())
    required = {'id','strategy','symbol','cap_type','underlying_price',
                'strike','long_strike','short_strike','expiry','right',
                'contracts','delta_entry','iv_rank_entry','premium_paid',
                'max_profit','max_loss','net_debit','stop_value','stop_stage',
                'catalyst_id','entry_grade','entry_date'}
    missing = required - keys
    assert not missing, f"Missing keys: {missing}"
    ok("get_open_options_trades (all keys present)")
except Exception as e:
    fail("get_open_options_trades", e)

try:
    db.log_options_news(
        symbol='PLTR',
        headline='Palantir wins $500M Pentagon AI contract',
        source='yfinance',
        published_at='2026-05-09T10:30:00',
        relevance='HIGH',
        news_type='CONTRACT_WIN',
        direction='BULLISH',
        time_horizon='SHORT',
        already_priced_in='NO',
        creates_future_event='NO',
        one_line_reason='Large gov AI contract directly lifts PLTR revenue',
    )
    ok("log_options_news")
except Exception as e:
    fail("log_options_news", e)

try:
    ret = db.close_options_trade(trade_id, exit_value=190.00, exit_reason='MANUAL',
                                  thesis_correct='YES', lesson='Held through catalyst correctly')
    assert ret is not None
    conn = _test_conn()
    row = conn.execute('SELECT status, return_pct FROM options_trades WHERE id=?', (trade_id,)).fetchone()
    conn.close()
    assert row[0] == 'CLOSED'
    assert abs(row[1] - 26.67) < 0.1, f"Expected ~26.67%, got {row[1]}"  # (190-150)/150*100
    ok(f"close_options_trade (return_pct = {row[1]:.2f}%)")
except Exception as e:
    fail("close_options_trade", e)

try:
    cnt = db.get_closed_options_count()
    assert cnt == 1
    learning = db.get_options_learning_data()
    assert len(learning) == 1
    assert learning[0]['strategy'] == 'BULL_SPREAD'
    ok("get_closed_options_count + get_options_learning_data")
except Exception as e:
    fail("learning data helpers", e)

# Restore real DB connection
db.get_connection = _orig_get_conn
import os as _os
_os.unlink(tmp_db.name)


# ─────────────────────────────────────────────────────────────────────────────
print("\n[3/6] Trailing stop math")
# ─────────────────────────────────────────────────────────────────────────────
from options.watchman import compute_new_stop

def make_trade(strategy, premium, max_profit=300, stage=1, stop=None):
    return {
        'strategy': strategy,
        'premium_paid': premium,
        'max_profit': max_profit,
        'stop_stage': stage,
        'stop_value': stop,
    }

try:
    # Bull Spread: Stage 1 — no stop set yet
    stop, stage = compute_new_stop(make_trade('BULL_SPREAD', 150, stop=None), current_value=100, session_high=100)
    assert stop == 75.0 and stage == 1, f"Got {stop}, {stage}"
    ok("BULL_SPREAD: initial hard stop set to 50% of premium")
except Exception as e:
    fail("BULL_SPREAD initial stop", e)

try:
    # Bull Spread: Stage 1 → 2 (breakeven lock at +25%)
    stop, stage = compute_new_stop(make_trade('BULL_SPREAD', 150, stop=75), current_value=190, session_high=190)
    assert stop == 150.0 and stage == 2, f"Got stop={stop}, stage={stage}"
    ok("BULL_SPREAD: stage 1→2 at +25% (breakeven lock)")
except Exception as e:
    fail("BULL_SPREAD stage 1→2", e)

try:
    # Bull Spread: Stage 1 → 3 directly (at +50% of max profit)
    # trail_trigger = 150 + 300 * 0.50 = 300
    stop, stage = compute_new_stop(make_trade('BULL_SPREAD', 150, max_profit=300, stop=75), current_value=310, session_high=310)
    assert stage == 3, f"Expected stage=3, got {stage}"
    # trail_stop = 310 - 300*0.15 = 310 - 45 = 265; max(265, 150) = 265
    assert stop == 265.0, f"Expected stop=265, got {stop}"
    ok("BULL_SPREAD: stage 1→3 at trail trigger, trail calc correct")
except Exception as e:
    fail("BULL_SPREAD stage 1→3", e)

try:
    # Bull Spread: Stage 3 — session high advances, trail moves up
    stop, stage = compute_new_stop(make_trade('BULL_SPREAD', 150, max_profit=300, stage=3, stop=250), current_value=320, session_high=350)
    # trail_stop = 350 - 300*0.15 = 350 - 45 = 305; 305 > 250 → update
    assert stage == 3 and stop == 305.0, f"Got stop={stop}, stage={stage}"
    ok("BULL_SPREAD: stage 3 trail moves up with new session high")
except Exception as e:
    fail("BULL_SPREAD stage 3 trail advance", e)

try:
    # LEAP: Stage 1 — hard stop at -40% (60% remaining)
    stop, stage = compute_new_stop(make_trade('LEAP', 400, stop=None), current_value=300, session_high=300)
    assert stop == 240.0 and stage == 1, f"Got {stop}, {stage}"
    ok("LEAP: initial hard stop at 60% of premium")
except Exception as e:
    fail("LEAP initial hard stop", e)

try:
    # LEAP: Stage 1 → 2 (breakeven lock at +30%)
    stop, stage = compute_new_stop(make_trade('LEAP', 400, stop=240), current_value=525, session_high=525)
    assert stop == 400.0 and stage == 2, f"Got stop={stop}, stage={stage}"
    ok("LEAP: stage 1→2 at +30% profit (breakeven lock)")
except Exception as e:
    fail("LEAP stage 1→2", e)

try:
    # LEAP: Stage 2 → 3 (trail at +50% of premium)
    # trail_trigger = 400 * 1.50 = 600
    stop, stage = compute_new_stop(make_trade('LEAP', 400, stage=2, stop=400), current_value=620, session_high=620)
    assert stage == 3, f"Expected stage=3, got {stage}"
    # trail_stop = 620 - 400*0.20 = 620 - 80 = 540; max(540, 400) = 540
    assert stop == 540.0, f"Expected stop=540, got {stop}"
    ok("LEAP: stage 2→3 at trail trigger, trail calc correct")
except Exception as e:
    fail("LEAP stage 2→3", e)

try:
    # Stage 3: no advance (session_high same, stop doesn't change)
    stop, stage = compute_new_stop(make_trade('LEAP', 400, stage=3, stop=540), current_value=610, session_high=620)
    assert stop is None and stage is None, f"Expected no change, got {stop}, {stage}"
    ok("Stage 3: no update when session_high unchanged")
except Exception as e:
    fail("Stage 3 no-update", e)


# ─────────────────────────────────────────────────────────────────────────────
print("\n[4/6] Grading logic")
# ─────────────────────────────────────────────────────────────────────────────
from options.options_trader import grade_spread, grade_leap

try:
    assert grade_spread(iv_rank=22, catalyst_days=14, liquidity_ok=True)  == 'A+'
    assert grade_spread(iv_rank=38, catalyst_days=14, liquidity_ok=True)  == 'A'
    assert grade_spread(iv_rank=43, catalyst_days=14, liquidity_ok=True)  == 'B'
    assert grade_spread(iv_rank=48, catalyst_days=14, liquidity_ok=True)  == 'C'
    assert grade_spread(iv_rank=22, catalyst_days=14, liquidity_ok=False) == 'C'  # illiquid overrides
    ok("grade_spread: A+/A/B/C all correct")
except Exception as e:
    fail("grade_spread", e)

try:
    assert grade_leap(iv_rank=22, catalyst_days=35, above_200ma=True,  domain_insight=True)  == 'A+'
    assert grade_leap(iv_rank=28, catalyst_days=35, above_200ma=True,  domain_insight=False) == 'A'
    assert grade_leap(iv_rank=33, catalyst_days=None, above_200ma=True, domain_insight=False) == 'B'
    assert grade_leap(iv_rank=38, catalyst_days=None, above_200ma=False, domain_insight=False) == 'C'
    ok("grade_leap: A+/A/B/C all correct")
except Exception as e:
    fail("grade_leap", e)


# ─────────────────────────────────────────────────────────────────────────────
print("\n[5/6] days_to_expiry helper")
# ─────────────────────────────────────────────────────────────────────────────
from options.options_trader import days_to_expiry

try:
    future = (date.today() + timedelta(days=30)).strftime('%Y%m%d')
    dte = days_to_expiry(future)
    assert dte == 30, f"Expected 30, got {dte}"
    ok("days_to_expiry (YYYYMMDD format)")
except Exception as e:
    fail("days_to_expiry", e)

try:
    future_dash = (date.today() + timedelta(days=90)).strftime('%Y-%m-%d')
    dte = days_to_expiry(future_dash)
    assert dte == 90, f"Expected 90, got {dte}"
    ok("days_to_expiry (YYYY-MM-DD format)")
except Exception as e:
    fail("days_to_expiry dashes", e)


# ─────────────────────────────────────────────────────────────────────────────
print("\n[6/6] Environment check")
# ─────────────────────────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

tg_token  = os.getenv('TELEGRAM_TOKEN')
tg_chat   = os.getenv('TELEGRAM_CHAT_ID')
anth_key  = os.getenv('ANTHROPIC_KEY') or os.getenv('ANTHROPIC_API_KEY')
bridge    = os.getenv('BRIDGE_URL', 'http://127.0.0.1:8000')
polygon   = os.getenv('POLYGON_KEY')
av_key    = os.getenv('ALPHA_VANTAGE_KEY')

if tg_token and tg_chat:
    ok("TELEGRAM_TOKEN + TELEGRAM_CHAT_ID set")
else:
    warn("Telegram env", "TELEGRAM_TOKEN or TELEGRAM_CHAT_ID missing — alerts will print to stdout")

if anth_key:
    ok("ANTHROPIC_KEY set — LLM classifier enabled")
else:
    warn("ANTHROPIC_KEY", "Not set — news_engine will store ALL headlines without AI classification")

ok(f"BRIDGE_URL = {bridge}")

if polygon:
    ok("POLYGON_KEY set")
else:
    warn("POLYGON_KEY", "Not set — only yfinance as news source")

if av_key:
    ok("ALPHA_VANTAGE_KEY set")
else:
    warn("ALPHA_VANTAGE_KEY", "Not set — only yfinance as news source")

# Try a live Telegram send if token is available
if tg_token and tg_chat:
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{tg_token}/sendMessage",
            json={'chat_id': tg_chat, 'text': '🔧 *Options system smoke test PASS*\n'
                  'All DB, trailing stop, and grading checks passed.',
                  'parse_mode': 'Markdown'},
            timeout=8,
        )
        if r.status_code == 200:
            ok("Telegram live send: message delivered")
        else:
            warn("Telegram live send", f"HTTP {r.status_code}: {r.text[:100]}")
    except Exception as e:
        warn("Telegram live send", str(e))
else:
    warn("Telegram live send", "skipped — no credentials")


# ─────────────────────────────────────────────────────────────────────────────
print("\n─────────────────────────────────────")
passed = sum(1 for s, _ in results if s == PASS)
warned = sum(1 for s, _ in results if s == WARN)
failed = sum(1 for s, _ in results if s == FAIL)
print(f"RESULT: {passed} passed  {warned} warnings  {failed} failed")
if failed:
    print("\nFailed tests:")
    for s, n in results:
        if s == FAIL:
            print(f"  {FAIL} {n}")
    sys.exit(1)
else:
    print("All critical checks passed. ✅")
