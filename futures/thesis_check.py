"""
futures/thesis_check.py — LLM in-trade trend-continuation / reversal-risk observer.

Built Aug 9 2026, motivated directly by a real 2-week review of IBKR trades
(user request). Manual FUT CLOSE decisions in that window were right about
half the time by count but wrong by magnitude: 4 of 7 pulls cut genuine trend
days short (128-322pt of further favorable move left on the table, Aug 3/4),
while 3 of 7 correctly caught a real reversal day (Aug 6, 73-129pt of give-back
avoided). The instinct isn't bad — it just isn't reliably telling trend days
from reversal days in the moment, which is the same trend/chop classification
problem this codebase has already flagged twice as unsolved by every
mechanical attempt tried (RVOL, ADX, IB-range, VWAP-cross-count — see
CLAUDE.md Jul 7/Jul 8 sections). This is a new angle at that same problem:
ask an LLM to read the live chart on an OPEN, PROFITABLE position and
classify CONTINUE vs REVERSAL_RISK.

LOG MODE ONLY — same instrument-first doctrine as equity's Thesis Check and
Chart Gate (auto_trader.py, shipped Aug 8 2026). Does NOT touch orders or
sizing. Logs to futures_thesis_check (trades.db) for scoring; graduating to
an actual gate only happens after real data shows it beats what Reversal
Exit's fixed thresholds already do — not before.

Shared by both futures_trader.py (IBKR) and tc_trader.py (TC) — one prompt,
one throttle dict, one scoring path, account-isolated by account_mode (the
throttle dict is process-local, so IBKR and TC never share a trade_id
namespace collision — trade_id is only ever looked up scoped by its own
process's open positions).
"""
import os
import io
import base64
import sqlite3
import threading
from datetime import datetime, timedelta

import pandas as pd
import pytz
import anthropic
import mplfinance as mpf
from dotenv import load_dotenv

_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_DIR, '..', '.env'))

ET      = pytz.timezone('America/New_York')
DB_PATH = os.path.join(_DIR, '..', 'trades.db')

ANTHROPIC_KEY = os.getenv('ANTHROPIC_KEY')
_ai = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# Throttle: check at most once every N minutes per trade, and only once the
# position has a real peak to protect — MIN_PEAK_PTS matches Reversal Exit's
# own REV_EXIT_PEAK_MIN_PTS floor so both mechanisms are judged on the same
# population of trades (an apples-to-apples comparison in the weekly review).
MIN_INTERVAL_MIN = 15
MIN_PEAK_PTS     = 100.0

_last_run: dict = {}   # trade_id -> datetime of last check (process-local)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS futures_thesis_check (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id      INTEGER,
            account_mode  TEXT,
            symbol        TEXT,
            side          TEXT,
            checked_at    TEXT,
            entry_price   REAL,
            current_price REAL,
            pnl_pts       REAL,
            peak_pts      REAL,
            verdict       TEXT,
            reasoning     TEXT
        )
    ''')
    conn.commit()
    conn.close()


def _chart_b64(df5: pd.DataFrame, title: str):
    """df5 must have lowercase open/high/low/close/volume columns (matches
    both traders' get_bars() shape) and a DatetimeIndex."""
    try:
        d = df5[['open', 'high', 'low', 'close', 'volume']].rename(columns={
            'open': 'Open', 'high': 'High', 'low': 'Low',
            'close': 'Close', 'volume': 'Volume',
        })
        buf = io.BytesIO()
        # Same addplot=None trap fixed in equity's Chart Gate Aug 8 2026 — no
        # overlay here, so the addplot kwarg is simply never passed.
        mpf.plot(d, type='candle', style='charles', title=title, volume=True,
                  figsize=(10, 6), savefig=dict(fname=buf, format='png', dpi=100))
        buf.seek(0)
        return base64.b64encode(buf.read()).decode('utf-8')
    except Exception:
        return None


def _ask_claude(b64_image: str, prompt: str):
    try:
        resp = _ai.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=256,
            messages=[{'role': 'user', 'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': b64_image}},
                {'type': 'text', 'text': prompt},
            ]}],
        )
        return resp.content[0].text.strip()
    except Exception:
        return None


def _run_check(trade_id, account_mode, symbol, side, entry_price, current_price,
                pnl_pts, peak_pts, df5, log_fn):
    try:
        b64 = _chart_b64(df5, f'{symbol} 5m — open {side} ({account_mode})')
        if not b64:
            return
        prompt = (
            f"You are a technical trading analyst reviewing an OPEN, currently "
            f"PROFITABLE {side} futures position. Entered {symbol} at {entry_price:.2f}, "
            f"now at {current_price:.2f} ({pnl_pts:+.0f}pts unrealized), peak favorable "
            f"move so far was {peak_pts:+.0f}pts. This is the 5-minute intraday chart. "
            f"Does the trend look likely to CONTINUE (worth holding for more), or is "
            f"REVERSAL RISK rising (worth banking the gain now)? "
            f"Answer CONTINUE or REVERSAL_RISK on the first line, then one sentence of reasoning."
        )
        answer = _ask_claude(b64, prompt)
        if not answer:
            return
        verdict   = 'REVERSAL_RISK' if 'REVERSAL_RISK' in answer.upper() else 'CONTINUE'
        reasoning = answer.split('\n', 1)[1].strip() if '\n' in answer else answer

        conn = sqlite3.connect(DB_PATH)
        conn.execute('''
            INSERT INTO futures_thesis_check
                (trade_id, account_mode, symbol, side, checked_at, entry_price,
                 current_price, pnl_pts, peak_pts, verdict, reasoning)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ''', (trade_id, account_mode, symbol, side, datetime.now(ET).isoformat(),
              entry_price, current_price, pnl_pts, peak_pts, verdict, reasoning))
        conn.commit()
        conn.close()

        icon = '⚠️' if verdict == 'REVERSAL_RISK' else '📈'
        log_fn(f"  [FUTURES THESIS CHECK] {symbol} #{trade_id} ({account_mode}) | {icon} {verdict} | "
               f"+{pnl_pts:.0f}pts (peak +{peak_pts:.0f}) | {reasoning}")
    except Exception as e:
        log_fn(f"Futures thesis check error {symbol} #{trade_id}: {e}")


def maybe_check(trade_id, account_mode, symbol, side, entry_price, current_price,
                 pnl_pts, peak_pts, df5, log_fn):
    """Throttle gate — call every monitor cycle, cheap no-op most of the time.
    Only fires on OPEN, PROFITABLE positions past MIN_PEAK_PTS, at most once
    every MIN_INTERVAL_MIN minutes per trade. Runs in a background thread —
    zero latency impact on the monitor loop, matches equity's Thesis Check."""
    if peak_pts < MIN_PEAK_PTS or pnl_pts <= 0 or df5 is None or df5.empty:
        return
    now  = datetime.now(ET)
    last = _last_run.get(trade_id)
    if last and (now - last).total_seconds() < MIN_INTERVAL_MIN * 60:
        return
    _last_run[trade_id] = now
    threading.Thread(
        target=_run_check,
        args=(trade_id, account_mode, symbol, side, entry_price, current_price,
              pnl_pts, peak_pts, df5, log_fn),
        daemon=True,
    ).start()


def weekly_review(send_telegram_fn, log_fn, account_mode: str):
    """Cross-ref verdicts against real trade outcomes for this account.
    REVERSAL_RISK calls: did the trade's actual exit price end up worse than
    the price at the moment of the call (validating the warning)? CONTINUE
    calls: did the exit end up at least as good (validating the hold)?
    Reads futures_thesis_check + futures_trades directly (DB is the source of
    truth here — no log-scraping, unlike equity's text-log-based version)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cutoff = (datetime.now(ET) - timedelta(days=7)).isoformat()
        rows = conn.execute(
            "SELECT * FROM futures_thesis_check WHERE checked_at >= ? AND account_mode=?",
            (cutoff, account_mode)
        ).fetchall()

        if not rows:
            send_telegram_fn(f"🧠 Futures Thesis Check Weekly Review ({account_mode})\n"
                              f"No checks logged this week.")
            conn.close()
            return

        continue_n = sum(1 for r in rows if r['verdict'] == 'CONTINUE')
        risk_n     = sum(1 for r in rows if r['verdict'] == 'REVERSAL_RISK')

        risk_confirmed, continue_confirmed = 0, 0
        risk_scored, continue_scored = 0, 0
        detail_lines = []
        for r in rows:
            trow = conn.execute(
                "SELECT exit_price, status FROM futures_trades WHERE id=?",
                (r['trade_id'],)
            ).fetchone()
            if not trow or trow['status'] != 'CLOSED' or trow['exit_price'] is None:
                continue
            is_short = r['side'] == 'SHORT'
            gave_back = (trow['exit_price'] < r['current_price']) if not is_short \
                        else (trow['exit_price'] > r['current_price'])
            if r['verdict'] == 'REVERSAL_RISK':
                risk_scored += 1
                if gave_back:
                    risk_confirmed += 1
                    detail_lines.append(f"  ⚠️ {r['symbol']} #{r['trade_id']}: warned, price gave back — confirmed")
            else:
                continue_scored += 1
                if not gave_back:
                    continue_confirmed += 1

        lines = [
            f"🧠 Futures Thesis Check Weekly Review ({account_mode}) | {datetime.now(ET).strftime('%b %d')}",
            f"Total checks: {len(rows)} | CONTINUE: {continue_n} | REVERSAL_RISK: {risk_n}",
        ]
        if risk_scored:
            lines.append(f"REVERSAL_RISK calls confirmed by real outcome: {risk_confirmed}/{risk_scored}")
        if continue_scored:
            lines.append(f"CONTINUE calls confirmed by real outcome: {continue_confirmed}/{continue_scored}")
        lines.extend(detail_lines[:10])
        send_telegram_fn('\n'.join(lines))
        conn.close()
    except Exception as e:
        log_fn(f"Futures thesis check weekly review error: {e}")
