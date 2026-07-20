#!/usr/bin/env python3
"""
market_context.py — Market Context Engine (Phase 0: INSTRUMENT-ONLY, gates nothing).

Canonical name: "Field Report" (GLOSSARY.md). Two layers, one row per day in
trades.db:market_brief, written PRE-MARKET (launchd 13:15 UTC weekdays) so the
stance is immutable before the open — that's what makes it scoreable.

Layer 1 (mechanical, no LLM): SPY/QQQ multi-timeframe trend state (yfinance
daily — bars_5m's SPY feed died Jun 1 2026, daily data is the reliable path),
MNQ trend + S/R levels from futures_bars_5m, realized vol, macro-event
calendar (FOMC/CPI/NFP), upcoming earnings from catalyst_calendar.

Layer 2 (LLM): one claude-opus-4-8 call/day (~$0.03) synthesizing Layer 1 +
last-48h HIGH/MEDIUM headlines from options_news into a structured stance
(RISK_ON/NEUTRAL/RISK_OFF + themes + event risk). API failure → stance
UNAVAILABLE, mechanical layer still recorded; the day is never lost.

NO trader reads this table for decisions (Constitution: instrument-first).
Scoring after ~4 weeks: join market_brief.date vs scan_log / futures_trades
outcomes by date. `--score` prints the join once data accumulates.

Usage:
  venv/bin/python market_context.py                # full run (DB + telegram)
  venv/bin/python market_context.py --no-telegram  # write DB only
  venv/bin/python market_context.py --no-llm       # mechanical layer only
  venv/bin/python market_context.py --score        # stance-vs-outcome report
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

ET = ZoneInfo('America/New_York')
TRADES_DB = os.path.join(BASE_DIR, 'trades.db')
MARKET_DB = os.path.join(BASE_DIR, 'market_data.db')

# Keep in sync with dashboard/app.py MACRO_EVENTS (dashboard renders the full
# list; this copy only needs dates — update both when adding 2027 dates).
MACRO_DATES = {
    '2026-07-29': 'FOMC Rate Decision',
    '2026-09-16': 'FOMC Rate Decision',
    '2026-11-04': 'FOMC Rate Decision',
    '2026-12-09': 'FOMC Rate Decision',
    '2026-08-12': 'CPI Release (Jul data)',
    '2026-09-10': 'CPI Release (Aug data)',
    '2026-08-07': 'Non-Farm Payrolls (Jul data)',
    '2026-09-04': 'Non-Farm Payrolls (Aug data)',
    '2026-07-31': 'PCE Price Index (Jun data)',
}

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "stance": {"type": "string", "enum": ["RISK_ON", "NEUTRAL", "RISK_OFF"]},
        "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
        "event_risk_today": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
        "themes": {"type": "array", "items": {"type": "string"}},
        "sectors_favored": {"type": "array", "items": {"type": "string"}},
        "sectors_avoid": {"type": "array", "items": {"type": "string"}},
        "one_line_thesis": {"type": "string"},
        "watch_items": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["stance", "confidence", "event_risk_today", "themes",
                 "sectors_favored", "sectors_avoid", "one_line_thesis",
                 "watch_items"],
    "additionalProperties": False,
}


# ── Layer 1: mechanical ──────────────────────────────────────────────────────

def _trend_state(closes) -> dict:
    """Multi-timeframe trend snapshot from a daily close series (list, oldest first)."""
    import statistics
    if len(closes) < 55:
        return {}
    c = closes[-1]
    ma5 = sum(closes[-5:]) / 5
    ma20 = sum(closes[-20:]) / 20
    ma50 = sum(closes[-50:]) / 50
    rets = [(closes[i] / closes[i - 1] - 1) for i in range(len(closes) - 19, len(closes))]
    rvol20 = statistics.pstdev(rets) * (252 ** 0.5) * 100
    return {
        'close': round(c, 2),
        'vs_ma5_pct': round((c / ma5 - 1) * 100, 2),
        'vs_ma20_pct': round((c / ma20 - 1) * 100, 2),
        'vs_ma50_pct': round((c / ma50 - 1) * 100, 2),
        'mom_5d_pct': round((c / closes[-6] - 1) * 100, 2),
        'mom_20d_pct': round((c / closes[-21] - 1) * 100, 2),
        'realized_vol_20d': round(rvol20, 1),
        'trend': ('UP' if c > ma20 > ma50 else
                  'DOWN' if c < ma20 < ma50 else 'MIXED'),
    }


def _equity_indices() -> dict:
    """SPY/QQQ daily trend via yfinance (see module docstring for why not bars_5m)."""
    out = {}
    try:
        import yfinance as yf
        data = yf.download(['SPY', 'QQQ'], period='6mo', progress=False)['Close']
        for sym in ('SPY', 'QQQ'):
            if sym in data.columns:
                closes = data[sym].dropna().tolist()
                out[sym] = _trend_state(closes)
                ser = data[sym].dropna()
                out[sym]['prior_day_close'] = round(float(ser.iloc[-1]), 2)
    except Exception as e:
        print(f"[market_context] index fetch error: {e}")
    return out


def _mnq_state() -> dict:
    """MNQ trend + S/R levels from our own futures 5-min bars."""
    out = {}
    try:
        conn = sqlite3.connect(MARKET_DB)
        rows = conn.execute(
            """SELECT ts_utc, open, high, low, close FROM futures_bars_5m
               WHERE symbol='MNQ' AND ts_utc >= datetime('now', '-80 days')
               ORDER BY ts_utc""").fetchall()
        conn.close()
        if not rows:
            return out
        # Resample by CME trading day: session opens 6pm ET the prior calendar
        # evening, so shift +6h before taking the date. On a pre-market run the
        # current (incomplete) trading day is the overnight session — split it
        # out so "prior_day" levels always mean the last COMPLETED session.
        days: dict = {}
        for ts, o, h, l, c in rows:
            et_t = datetime.fromisoformat(ts).replace(tzinfo=ZoneInfo('UTC')) \
                .astimezone(ET)
            d = (et_t + timedelta(hours=6)).date().isoformat()
            if d not in days:
                days[d] = {'high': h, 'low': l, 'close': c}
            else:
                days[d]['high'] = max(days[d]['high'], h)
                days[d]['low'] = min(days[d]['low'], l)
                days[d]['close'] = c
        today_key = (datetime.now(ET) + timedelta(hours=6)).date().isoformat()
        overnight = days.pop(today_key, None)
        keys = sorted(days)
        if not keys:
            return out
        closes = [days[k]['close'] for k in keys]
        out = _trend_state(closes)
        prior = days[keys[-1]]
        out['levels'] = {
            'prior_day_high': round(prior['high'], 2),
            'prior_day_low': round(prior['low'], 2),
            'prior_day_close': round(prior['close'], 2),
            'week_high': round(max(days[k]['high'] for k in keys[-5:]), 2),
            'week_low': round(min(days[k]['low'] for k in keys[-5:]), 2),
        }
        if overnight:
            out['overnight'] = {
                'high': round(overnight['high'], 2),
                'low': round(overnight['low'], 2),
                'last': round(overnight['close'], 2),
                'gap_vs_prior_close_pct': round(
                    (overnight['close'] / prior['close'] - 1) * 100, 2),
            }
        out['last_bar_utc'] = rows[-1][0]
    except Exception as e:
        print(f"[market_context] MNQ state error: {e}")
    return out


def _events_today_and_upcoming() -> dict:
    today = date.today()
    out = {'today': [], 'next_3_days': []}
    for dstr, name in MACRO_DATES.items():
        d = date.fromisoformat(dstr)
        if d == today:
            out['today'].append(name)
        elif today < d <= today + timedelta(days=3):
            out['next_3_days'].append(f"{name} ({dstr})")
    # Upcoming catalysts (earnings etc.) for our own names, next 3 days
    try:
        conn = sqlite3.connect(TRADES_DB)
        rows = conn.execute(
            """SELECT symbol, name, date FROM catalyst_calendar
               WHERE date BETWEEN ? AND ? ORDER BY date LIMIT 12""",
            (today.isoformat(), (today + timedelta(days=3)).isoformat())).fetchall()
        conn.close()
        out['catalysts_next_3d'] = [f"{r[0]}: {r[1]} ({r[2]})" for r in rows]
    except Exception:
        out['catalysts_next_3d'] = []
    return out


def _recent_headlines(hours: int = 48, limit: int = 25) -> list:
    try:
        conn = sqlite3.connect(TRADES_DB)
        rows = conn.execute(
            """SELECT symbol, headline, relevance, direction, magnitude
               FROM options_news
               WHERE published_at >= datetime('now', ?)
                 AND relevance IN ('HIGH', 'MEDIUM')
               ORDER BY CASE relevance WHEN 'HIGH' THEN 0 ELSE 1 END, id DESC
               LIMIT ?""", (f'-{hours} hours', limit)).fetchall()
        conn.close()
        return [f"[{r[2]}/{r[3]}] {r[0]}: {r[1]}" for r in rows]
    except Exception as e:
        print(f"[market_context] headlines error: {e}")
        return []


def build_mechanical() -> dict:
    return {
        'indices': _equity_indices(),
        'mnq': _mnq_state(),
        'events': _events_today_and_upcoming(),
    }


# ── Layer 2: LLM brief ───────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the pre-market Field Report writer for TriVega, an
automated intraday trading system (US equities long/short, MNQ futures,
options spreads). It holds nothing overnight and trades momentum/breakout
setups during the NY session.

You receive mechanical market state (trend/vol/levels computed from data — do
not recompute or contradict them) plus recent headlines and the event
calendar. Produce today's stance:

- stance: RISK_ON (tape supports momentum longs), RISK_OFF (tape favors
  shorts/defense), NEUTRAL (mixed/chop — no strong tilt).
- event_risk_today: HIGH only if a market-moving release/event lands today.
- themes: the 2-5 macro forces actually in play (e.g. "tariffs", "oil
  supply", "Fed path", "AI capex") — only ones supported by the inputs.
- sectors_favored / sectors_avoid: sector names from the inputs, max 3 each.
- one_line_thesis: one sentence a trader reads at 9:25am.
- watch_items: concrete, checkable risks for TODAY (e.g. "FOMC 2pm",
  "MNQ sitting on last week's low 22850").

Ground every field in the supplied inputs. If inputs are thin (quiet weekend,
no headlines), say so via NEUTRAL/LOW confidence rather than inventing
narrative. This report is logged and scored against what the market actually
does — plain, falsifiable statements beat colorful ones."""


def run_llm_brief(mech: dict, headlines: list) -> dict:
    """One structured claude-opus-4-8 call. Returns dict or {'stance':'UNAVAILABLE'}."""
    import anthropic
    api_key = os.getenv('ANTHROPIC_KEY') or os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        print("[market_context] no ANTHROPIC_KEY — skipping LLM layer")
        return {'stance': 'UNAVAILABLE', 'error': 'no api key'}

    payload = {
        'date': date.today().isoformat(),
        'mechanical': mech,
        'headlines_48h': headlines if headlines else ['(no HIGH/MEDIUM headlines in window)'],
    }
    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=120.0)
        response = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=2000,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            output_config={"format": {"type": "json_schema", "schema": BRIEF_SCHEMA}},
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
        )
        if response.stop_reason == "refusal":
            return {'stance': 'UNAVAILABLE', 'error': 'refusal'}
        text = next((b.text for b in response.content if b.type == "text"), "")
        brief = json.loads(text)
        brief['_model'] = response.model
        brief['_usage'] = {'in': response.usage.input_tokens,
                           'out': response.usage.output_tokens}
        return brief
    except anthropic.RateLimitError:
        return {'stance': 'UNAVAILABLE', 'error': 'rate limited'}
    except anthropic.APIStatusError as e:
        return {'stance': 'UNAVAILABLE', 'error': f'api {e.status_code}'}
    except anthropic.APIConnectionError:
        return {'stance': 'UNAVAILABLE', 'error': 'connection'}
    except Exception as e:
        return {'stance': 'UNAVAILABLE', 'error': str(e)[:200]}


# ── Persistence + telegram ───────────────────────────────────────────────────

def ensure_table():
    conn = sqlite3.connect(TRADES_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS market_brief (
        brief_date TEXT PRIMARY KEY,
        created_at TEXT,
        stance TEXT,
        confidence TEXT,
        event_risk TEXT,
        themes TEXT,
        one_line TEXT,
        mechanical_json TEXT,
        llm_json TEXT
    )""")
    conn.commit()
    conn.close()


def save_brief(mech: dict, brief: dict):
    ensure_table()
    conn = sqlite3.connect(TRADES_DB)
    conn.execute(
        """INSERT OR REPLACE INTO market_brief
           (brief_date, created_at, stance, confidence, event_risk, themes,
            one_line, mechanical_json, llm_json)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (date.today().isoformat(),
         datetime.now(ET).isoformat(),
         brief.get('stance'),
         brief.get('confidence'),
         brief.get('event_risk_today'),
         json.dumps(brief.get('themes', [])),
         brief.get('one_line_thesis'),
         json.dumps(mech, default=str),
         json.dumps(brief, default=str)))
    conn.commit()
    conn.close()


def send_telegram(mech: dict, brief: dict):
    token = os.getenv('TELEGRAM_TOKEN')
    chat = os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat:
        print("[market_context] no telegram config — skipping send")
        return
    spy = mech.get('indices', {}).get('SPY', {})
    mnq = mech.get('mnq', {})
    lv = mnq.get('levels', {})
    ev = mech.get('events', {})
    lines = [
        f"🌅 Field Report — {date.today().isoformat()}",
        f"Stance: {brief.get('stance', '?')} ({brief.get('confidence', '?')}) | "
        f"Event risk: {brief.get('event_risk_today', '?')}",
    ]
    if brief.get('one_line_thesis'):
        lines.append(brief['one_line_thesis'])
    if brief.get('themes'):
        lines.append("Themes: " + ", ".join(brief['themes'][:5]))
    if spy:
        lines.append(f"SPY {spy.get('trend')} (5d {spy.get('mom_5d_pct'):+.1f}%, "
                     f"vs 20MA {spy.get('vs_ma20_pct'):+.1f}%)")
    if mnq.get('trend'):
        lines.append(f"MNQ {mnq.get('trend')} | pdH {lv.get('prior_day_high')} "
                     f"pdL {lv.get('prior_day_low')} | wkL {lv.get('week_low')}")
    ovn = mnq.get('overnight')
    if ovn:
        lines.append(f"Overnight: {ovn.get('gap_vs_prior_close_pct'):+.2f}% "
                     f"(H {ovn.get('high')} / L {ovn.get('low')})")
    if ev.get('today'):
        lines.append("⚠️ TODAY: " + ", ".join(ev['today']))
    if brief.get('watch_items'):
        lines.append("Watch: " + "; ".join(brief['watch_items'][:3]))
    lines.append("_Log-only: no gate reads this. Scored nightly after ~4 wks._")
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={'chat_id': chat, 'text': "\n".join(lines)},
                      timeout=10)
    except Exception as e:
        print(f"[market_context] telegram error: {e}")


# ── Scoring (run after data accumulates) ─────────────────────────────────────

def score_report():
    """Stance vs same-day SPY move — the simplest falsifiable check."""
    ensure_table()
    conn = sqlite3.connect(TRADES_DB)
    briefs = conn.execute(
        "SELECT brief_date, stance FROM market_brief ORDER BY brief_date").fetchall()
    conn.close()
    if len(briefs) < 5:
        print(f"Only {len(briefs)} briefs logged — need ~20 trading days before scoring means anything.")
        return
    import yfinance as yf
    spy = yf.download('SPY', period='4mo', progress=False)
    daily = ((spy['Close'] - spy['Open']) / spy['Open'] * 100)
    if hasattr(daily, 'columns'):
        daily = daily.iloc[:, 0]
    daily.index = [d.date().isoformat() for d in daily.index]
    print(f"{'date':12} {'stance':10} {'SPY o→c%':>9}  aligned?")
    hits, n = 0, 0
    for d, stance in briefs:
        if d not in daily.index or stance in (None, 'UNAVAILABLE'):
            continue
        move = float(daily.loc[d])
        if stance == 'NEUTRAL':
            aligned = abs(move) < 0.5
        else:
            aligned = (move > 0) == (stance == 'RISK_ON')
        hits += aligned
        n += 1
        print(f"{d:12} {stance:10} {move:>+8.2f}%  {'✓' if aligned else '✗'}")
    if n:
        print(f"\nAligned {hits}/{n} = {hits / n * 100:.0f}% "
              f"(50% = coin flip; needs ~20+ days before judging)")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-telegram', action='store_true')
    ap.add_argument('--no-llm', action='store_true')
    ap.add_argument('--score', action='store_true')
    ap.add_argument('--force', action='store_true',
                    help='run even on weekends/holidays')
    args = ap.parse_args()

    if args.score:
        score_report()
        return

    now = datetime.now(ET)
    if not args.force and now.weekday() >= 5:
        print(f"[market_context] weekend ({now:%A}) — skipping")
        return

    print(f"[market_context] building brief for {date.today().isoformat()}")
    mech = build_mechanical()
    headlines = _recent_headlines()
    print(f"[market_context] mechanical done — {len(headlines)} headlines in window")

    if args.no_llm:
        brief = {'stance': 'UNAVAILABLE', 'error': 'llm skipped (--no-llm)'}
    else:
        brief = run_llm_brief(mech, headlines)
    print(f"[market_context] stance={brief.get('stance')} "
          f"({brief.get('confidence', '-')}) err={brief.get('error', '-')}")

    save_brief(mech, brief)
    print("[market_context] saved to market_brief")

    if not args.no_telegram:
        send_telegram(mech, brief)
        print("[market_context] telegram sent")


if __name__ == '__main__':
    main()
