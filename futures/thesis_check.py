"""
futures/thesis_check.py — LLM in-trade trend-continuation / reversal-risk observer
("Crest Watch" — see GLOSSARY.md).

Built Aug 9 2026 (chart-vision version). Redesigned same night after a design
review with the user, before any of the first version's data had accumulated
(0 rows logged at redesign time — no migration cost, no history lost).

Why redesigned: the original version showed Claude a candlestick PNG and asked
for a binary CONTINUE/REVERSAL_RISK label. Three problems with that shape:
(1) a vision model reading pixels is strictly worse at the numeric judgments
(momentum, distance from a level, volume regime) than the indicators this
codebase already computes precisely every cycle — RVOL, RSI, VWAP distance,
regime. (2) a binary label can't drive a graded response later (trail tighter
vs cut now vs give it room) — only ever a blunt on/off switch, the same
failure mode this codebase has already rejected for every mechanical exit
rule it kept (Reversal Exit's proportional lock-in, the regime-aware trail
tiers). (3) nothing was archived (only the last sentence of the reply) and no
model version was logged, so a prompt/model change next month couldn't be
judged against what was actually said before.

This version: text-only structured state in, a 0-100 graded risk score out,
full response + model version archived, and a per-trade streak counter —
mirrors the "require 2 consecutive confirmations before acting" pattern this
codebase already leans on everywhere else (Reversal Exit's confirm-bars,
the original thesis-invalidation's 2-of-4-votes-sustained-2-bars). Also folds
in the day's Field Report macro stance (market_context.py) as its own logged
field — NOT blended into the score — since this codebase already learned once
that a raw sentiment/news signal can run backwards (options' HIGH-BULL-
conviction was found to be a fade signal, Jul 18 audit); a new qualitative
ingredient earns trust from scored data, it isn't assumed to have it.

LOG MODE ONLY — same instrument-first doctrine as equity's Thesis Check and
Chart Gate, and as the first version of this file. Does NOT touch orders or
sizing. Logs to futures_thesis_check (trades.db) for scoring; graduating to
an actual gate only happens after real data shows it beats what Reversal
Exit's fixed thresholds already do — not before.

Shared by all four books that can hold an open MNQ position: futures_trader.py
(IBKR NY), tc_trader.py (TC NY), and london_trader.py under either account
(IBKR-London / TC-London, resolved via prop_rules.ACCOUNT_MODE at import
time in whichever process it's threaded into). One prompt, one scoring path,
account+session-isolated in the DB (account_mode + session columns) — NY
trade_ids come from futures_trades, London trade_ids come from london_trades,
two separate id spaces, never joined without the session column to pick the
right table.

NOT wired into any backtest/sim harness (sim_replay.py, london_v2_sim.py,
equity_replay.py) — deliberate, not an oversight. Two reasons: (1) it makes
zero trading decisions, so CONSTITUTION.md's "sim must match live" parity
requirement — which exists to keep decision-affecting logic honest — doesn't
apply. (2) a live model call can't be backtested the way a quant signal can:
it isn't free, isn't instant, and isn't reproducible (model behavior a year
from now won't match today's), so replaying it against 2024-2026 bars would
manufacture false precision rather than real validation. Same treatment
equity's Chart Gate / Thesis Check already got — live-only observer, scored
against real outcomes as they happen, not backtested.
"""
import os
import json
import sqlite3
import threading
from datetime import datetime, timedelta, date

import pandas as pd
import pytz
import anthropic
from dotenv import load_dotenv

_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_DIR, '..', '.env'))

ET      = pytz.timezone('America/New_York')
DB_PATH = os.path.join(_DIR, '..', 'trades.db')

ANTHROPIC_KEY = os.getenv('ANTHROPIC_KEY') or os.getenv('ANTHROPIC_API_KEY')
_ai = anthropic.Anthropic(api_key=ANTHROPIC_KEY) if ANTHROPIC_KEY else None

# Throttle: check at most once every N minutes per trade, and only once the
# position has a real peak to protect — MIN_PEAK_PTS matches Reversal Exit's
# own REV_EXIT_PEAK_MIN_PTS floor so both mechanisms are judged on the same
# population of trades (an apples-to-apples comparison in the weekly review).
MIN_INTERVAL_MIN = 15
MIN_PEAK_PTS     = 100.0

# Legacy CONTINUE/REVERSAL_RISK label, derived from risk_score for readable
# logs and for the weekly review's confirmed/not-confirmed scoring. A single
# number is the source of truth; this threshold is PROVISIONAL — nothing has
# been tuned against real outcomes yet, revisit once a few weeks of scored
# data exist.
RISK_SCORE_ALERT_THRESHOLD = 55

_last_run: dict = {}   # trade_id -> datetime of last check (process-local; throttle
                        # only, never the source of truth for streaks — see below)

THESIS_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        # NOTE: 'minimum'/'maximum' are not supported for integer fields by
        # this API's json_schema output format (confirmed via a live 400 at
        # build time) — the 0-100 range is enforced by the prompt text only.
        # _run_check clamps defensively in case the model ignores it.
        "risk_score": {"type": "integer"},
        "reasoning":  {"type": "string"},
    },
    "required": ["risk_score", "reasoning"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are a technical trading analyst reviewing an OPEN,
currently PROFITABLE futures position for an automated trading system. You
are given the exact structured state the system's own exit logic already
computed this cycle — not a chart. Do not invent price action you were not
given.

Score 0-100 how much REVERSAL RISK is building right now:
  0   = trend firmly intact, worth holding for more
  100 = reversal imminent, worth banking the gain now
Ground the score only in the fields provided. One sentence of reasoning
referencing the specific fields that drove the score."""


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS futures_thesis_check (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id      INTEGER,
            account_mode  TEXT,
            session       TEXT,
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
    # Idempotent ALTERs for the redesigned columns — matches this codebase's
    # own migration convention (see database.py). The table already existed
    # from tonight's first version but had zero rows, so this is a free,
    # lossless upgrade, not a real migration.
    for col, decl in [
        ('session',              'TEXT'),
        ('risk_score',           'INTEGER'),
        ('raw_response',         'TEXT'),
        ('model_version',        'TEXT'),
        ('streak',               'INTEGER'),
        ('context_json',         'TEXT'),
        ('field_report_stance',  'TEXT'),
        ('field_report_thesis',  'TEXT'),
        ('error',                'TEXT'),
    ]:
        try:
            conn.execute(f'ALTER TABLE futures_thesis_check ADD COLUMN {col} {decl}')
        except sqlite3.OperationalError:
            pass   # column already exists
    conn.commit()
    conn.close()


def _get_field_report():
    """Today's Field Report stance (market_context.py), if it has run yet.
    Soft-fail — a missing/late brief must never block a check."""
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT stance, one_line FROM market_brief WHERE brief_date=?",
            (datetime.now(ET).date().isoformat(),)
        ).fetchone()
        conn.close()
        if row:
            return row[0], row[1]
    except Exception:
        pass
    return None, None


def _get_streak(trade_id, session, risk_score):
    """DB-backed (not in-memory) per-trade streak of consecutive same-direction
    verdicts — restart-safe, mirrors Bite Check's DB-backed history (Aug 8
    2026) after that same in-memory-vs-restart lesson was already learned
    once in this codebase. Direction = above/below RISK_SCORE_ALERT_THRESHOLD."""
    direction = risk_score >= RISK_SCORE_ALERT_THRESHOLD
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            """SELECT risk_score, streak FROM futures_thesis_check
               WHERE trade_id=? AND session=? AND risk_score IS NOT NULL
               ORDER BY checked_at DESC LIMIT 1""",
            (trade_id, session)
        ).fetchone()
        conn.close()
        if row and row[0] is not None:
            prev_direction = row[0] >= RISK_SCORE_ALERT_THRESHOLD
            prev_streak    = row[1] or 1
            return (prev_streak + 1) if prev_direction == direction else 1
    except Exception:
        pass
    return 1


def _ask_claude(prompt_text: str):
    """Returns (risk_score, reasoning, raw_json_text, model, error) — error is
    None on success. Every failure path returns a specific reason; nothing is
    swallowed silently (the first version of this file had that bug)."""
    if _ai is None:
        return None, None, None, None, 'no ANTHROPIC_KEY configured'
    try:
        resp = _ai.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=400,
            system=SYSTEM_PROMPT,
            output_config={"format": {"type": "json_schema", "schema": THESIS_CHECK_SCHEMA}},
            messages=[{'role': 'user', 'content': prompt_text}],
        )
        if resp.stop_reason == 'refusal':
            return None, None, None, resp.model, 'model refusal'
        text = next((b.text for b in resp.content if b.type == 'text'), '')
        data = json.loads(text)
        score = max(0, min(100, int(data['risk_score'])))   # schema can't enforce range — clamp defensively
        return score, data['reasoning'], text, resp.model, None
    except anthropic.RateLimitError:
        return None, None, None, None, 'rate limited'
    except anthropic.APIStatusError as e:
        return None, None, None, None, f'api {e.status_code}'
    except anthropic.APIConnectionError:
        return None, None, None, None, 'connection error'
    except (KeyError, json.JSONDecodeError) as e:
        return None, None, None, None, f'malformed response: {e}'
    except Exception as e:
        return None, None, None, None, str(e)[:200]


def _build_prompt(symbol, side, account_mode, session, entry_price, current_price,
                   pnl_pts, peak_pts, stop_distance_pts, trail_tier, context: dict,
                   fr_stance, fr_thesis):
    lines = [
        f"{symbol} {side} position — {session} session, {account_mode} account.",
        f"Entry: {entry_price:.2f}  Current: {current_price:.2f}  "
        f"Unrealized: {pnl_pts:+.0f}pts  Peak favorable move: {peak_pts:+.0f}pts",
        f"Current stop: {stop_distance_pts:.0f}pts away  |  Active trail tier: {trail_tier}",
        "",
        "Signal state this cycle:",
    ]
    for k, v in context.items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append(
        f"Today's Field Report macro stance: {fr_stance or 'unavailable'}"
        + (f" — {fr_thesis}" if fr_thesis else "")
    )
    return '\n'.join(lines)


def _run_check(trade_id, account_mode, session, symbol, side, entry_price, current_price,
               pnl_pts, peak_pts, stop_distance_pts, trail_tier, context, log_fn):
    tag = f"{symbol} #{trade_id} ({account_mode}/{session})"
    try:
        fr_stance, fr_thesis = _get_field_report()
        prompt = _build_prompt(symbol, side, account_mode, session, entry_price,
                                current_price, pnl_pts, peak_pts, stop_distance_pts,
                                trail_tier, context, fr_stance, fr_thesis)
        risk_score, reasoning, raw, model, error = _ask_claude(prompt)

        if error:
            log_fn(f"  [CREST WATCH] {tag} SKIPPED — {error}")
            conn = sqlite3.connect(DB_PATH)
            conn.execute('''
                INSERT INTO futures_thesis_check
                    (trade_id, account_mode, session, symbol, side, checked_at,
                     entry_price, current_price, pnl_pts, peak_pts, context_json,
                     field_report_stance, field_report_thesis, error)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''', (trade_id, account_mode, session, symbol, side, datetime.now(ET).isoformat(),
                  entry_price, current_price, pnl_pts, peak_pts, json.dumps(context),
                  fr_stance, fr_thesis, error))
            conn.commit()
            conn.close()
            return

        streak  = _get_streak(trade_id, session, risk_score)
        verdict = 'REVERSAL_RISK' if risk_score >= RISK_SCORE_ALERT_THRESHOLD else 'CONTINUE'

        conn = sqlite3.connect(DB_PATH)
        conn.execute('''
            INSERT INTO futures_thesis_check
                (trade_id, account_mode, session, symbol, side, checked_at, entry_price,
                 current_price, pnl_pts, peak_pts, risk_score, verdict, reasoning,
                 raw_response, model_version, streak, context_json,
                 field_report_stance, field_report_thesis)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (trade_id, account_mode, session, symbol, side, datetime.now(ET).isoformat(),
              entry_price, current_price, pnl_pts, peak_pts, risk_score, verdict, reasoning,
              raw, model, streak, json.dumps(context), fr_stance, fr_thesis))
        conn.commit()
        conn.close()

        icon = '⚠️' if verdict == 'REVERSAL_RISK' else '📈'
        streak_note = f' (streak {streak})' if streak > 1 else ''
        log_fn(f"  [CREST WATCH] {tag} | {icon} risk={risk_score}{streak_note} | "
               f"+{pnl_pts:.0f}pts (peak +{peak_pts:.0f}) | {reasoning}")
    except Exception as e:
        log_fn(f"  [CREST WATCH] {tag} SKIPPED — unexpected error: {e}")


def maybe_check(trade_id, account_mode, session, symbol, side, entry_price, current_price,
                 pnl_pts, peak_pts, stop_distance_pts, trail_tier, context: dict, log_fn):
    """Throttle gate — call every monitor cycle, cheap no-op most of the time.
    Only fires on OPEN, PROFITABLE positions past MIN_PEAK_PTS, at most once
    every MIN_INTERVAL_MIN minutes per trade. Runs in a background thread —
    zero latency impact on the monitor loop.

    context: dict of already-computed signal-state fields specific to the
    caller (NY passes regime/RVOL/RSI/VWAP-side; London passes ATR/overnight
    bias/BE state) — rendered as text lines in the prompt and archived
    verbatim as context_json. No chart image is generated or sent."""
    if peak_pts < MIN_PEAK_PTS or pnl_pts <= 0:
        return
    now  = datetime.now(ET)
    last = _last_run.get((session, trade_id))
    if last and (now - last).total_seconds() < MIN_INTERVAL_MIN * 60:
        return
    _last_run[(session, trade_id)] = now
    threading.Thread(
        target=_run_check,
        args=(trade_id, account_mode, session, symbol, side, entry_price, current_price,
              pnl_pts, peak_pts, stop_distance_pts, trail_tier, context, log_fn),
        daemon=True,
    ).start()


def weekly_review(send_telegram_fn, log_fn, account_mode: str):
    """Cross-ref verdicts against real trade outcomes for this account, split
    by session (NY vs London use different trade tables AND different signal
    profiles — futures_trades for NY, london_trades for London — blending
    them would hide which one the signal actually works for, if either)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cutoff = (datetime.now(ET) - timedelta(days=7)).isoformat()
        rows = conn.execute(
            "SELECT * FROM futures_thesis_check WHERE checked_at >= ? AND account_mode=?",
            (cutoff, account_mode)
        ).fetchall()

        if not rows:
            send_telegram_fn(f"🧠 Crest Watch Weekly Review ({account_mode})\n"
                              f"No checks logged this week.")
            conn.close()
            return

        scored  = [r for r in rows if r['risk_score'] is not None]
        errored = [r for r in rows if r['error']]
        lines = [f"🧠 Crest Watch Weekly Review ({account_mode}) | {datetime.now(ET).strftime('%b %d')}",
                 f"Total checks: {len(rows)}  ({len(scored)} scored, {len(errored)} failed)"]

        for session, table in (('NY', 'futures_trades'), ('LONDON', 'london_trades')):
            srows = [r for r in scored if r['session'] == session]
            if not srows:
                continue
            risk_n     = sum(1 for r in srows if r['verdict'] == 'REVERSAL_RISK')
            streak2_n  = sum(1 for r in srows if r['verdict'] == 'REVERSAL_RISK' and (r['streak'] or 1) >= 2)
            confirmed, confirmed_streak2, n_scored = 0, 0, 0
            for r in srows:
                trow = conn.execute(
                    f"SELECT exit_price, status FROM {table} WHERE id=?", (r['trade_id'],)
                ).fetchone()
                if not trow or trow['status'] != 'CLOSED' or trow['exit_price'] is None:
                    continue
                is_short = r['side'] == 'SHORT'
                gave_back = (trow['exit_price'] < r['current_price']) if not is_short \
                            else (trow['exit_price'] > r['current_price'])
                is_risk_call = r['verdict'] == 'REVERSAL_RISK'
                right = gave_back if is_risk_call else (not gave_back)
                n_scored += 1
                confirmed += 1 if right else 0
                if is_risk_call and (r['streak'] or 1) >= 2:
                    confirmed_streak2 += 1 if right else 0

            lines.append(f"— {session}: {len(srows)} checks, {risk_n} REVERSAL_RISK "
                         f"({streak2_n} at streak≥2)")
            if n_scored:
                lines.append(f"   accuracy vs real outcome: {confirmed}/{n_scored}")
            if streak2_n:
                lines.append(f"   streak≥2 REVERSAL_RISK confirmed: {confirmed_streak2}/{streak2_n}")

        send_telegram_fn('\n'.join(lines))
        conn.close()
    except Exception as e:
        log_fn(f"Crest Watch weekly review error: {e}")
