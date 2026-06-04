"""
futures/news_macro.py — Macro news direction classifier for MNQ futures.

Repurposes the existing Groq/Llama infrastructure from options/news_engine.py.
Classifies macro headlines for NASDAQ futures directional bias.

Key insight (Jun 4 2026 backtest):
  NFP/CPI days have 61.1% WR vs 47.4% normal — our BEST days.
  The goal is not to avoid them but to trade them with the right direction.

Use cases:
  1. After NFP/CPI release: classify surprise → LONG or SHORT daily bias.
  2. Direction boost on HIGH_IMPACT days: allow max_trades=3 (larger moves expected).
  3. Tariff/trade news: classify → RISK_ON or RISK_OFF (relevant 2026 regime).

Integration points:
  - futures_trader.py morning_scan(): call get_macro_bias() before first entry.
  - futures_trader.py check_can_trade(): check macro_bias against proposed direction.

Usage:
    from futures.news_macro import get_macro_bias, MacroBias

    bias = get_macro_bias()  # queries news + classifies
    if bias.direction in ('LONG', 'SHORT'):
        # Override or weight the daily_bias from EMA5/EMA20
        pass
"""

import os
import re
from datetime import datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / '.env')

ET = ZoneInfo('America/New_York')

# Reuse Groq — same free API as options/news_engine.py (llama-3.3-70b-versatile)
try:
    from groq import Groq
    _GROQ_KEY = os.getenv('GROQ_API_KEY')
    _GROQ_CLIENT = Groq(api_key=_GROQ_KEY) if _GROQ_KEY else None
except ImportError:
    _GROQ_CLIENT = None


@dataclass
class MacroBias:
    direction:   str    # 'LONG' | 'SHORT' | 'NEUTRAL' | 'UNKNOWN'
    confidence:  str    # 'HIGH' | 'MEDIUM' | 'LOW'
    event_type:  str    # 'NFP' | 'CPI' | 'FOMC' | 'OTHER' | ''
    headline:    str    # the headline we classified
    reasoning:   str    # one-line reason
    timestamp:   str    # when this was computed

    @property
    def is_actionable(self) -> bool:
        return self.direction in ('LONG', 'SHORT') and self.confidence in ('HIGH', 'MEDIUM')


_GROQ_SYSTEM = """You are a financial analyst classifying macro economic news for NASDAQ futures (MNQ/NQ) directional trading.

Classify news as:
RISK_ON  = positive for NASDAQ/tech stocks (Fed dovish, jobs beat, inflation lower, trade deal, strong GDP)
RISK_OFF = negative for NASDAQ/tech stocks (Fed hawkish, jobs miss, inflation higher, tariff/war escalation, recession)
NEUTRAL  = unclear or mixed signals

Rules:
- Focus only on impact on MNQ/NQ futures direction for the current trading session
- Jobs beat = RISK_ON, miss = RISK_OFF
- CPI below expected = RISK_ON (less Fed tightening), above = RISK_OFF
- Fed rate cut = RISK_ON, hike/hold-hawkish = RISK_OFF
- Tariff escalation = RISK_OFF, deal/pause = RISK_ON
- GDP beat = RISK_ON, miss = RISK_OFF

Respond with exactly this format (one line each):
DIRECTION: RISK_ON or RISK_OFF or NEUTRAL
CONFIDENCE: HIGH or MEDIUM or LOW
REASON: <one sentence>"""


def classify_headline(headline: str, event_type: str = '') -> MacroBias:
    """
    Classify a macro headline for MNQ directional bias using Groq/Llama.
    Returns MacroBias with LONG/SHORT/NEUTRAL direction.
    """
    if not _GROQ_CLIENT:
        return MacroBias('UNKNOWN', 'LOW', event_type, headline,
                         'Groq not available', datetime.now(ET).isoformat())

    prompt = (f'Event type: {event_type}\nHeadline: {headline}\n\n'
              f'Classify the directional impact on MNQ futures.')
    try:
        resp = _GROQ_CLIENT.chat.completions.create(
            model='llama-3.3-70b-versatile',
            messages=[
                {'role': 'system', 'content': _GROQ_SYSTEM},
                {'role': 'user',   'content': prompt},
            ],
            temperature=0.1,
            max_tokens=150,
        )
        text = resp.choices[0].message.content.strip()
    except Exception as e:
        return MacroBias('UNKNOWN', 'LOW', event_type, headline,
                         f'Groq error: {e}', datetime.now(ET).isoformat())

    # Parse response
    direction_map = {'RISK_ON': 'LONG', 'RISK_OFF': 'SHORT', 'NEUTRAL': 'NEUTRAL'}
    direction = 'NEUTRAL'
    confidence = 'LOW'
    reason = text[:100]

    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('DIRECTION:'):
            raw = line.split(':', 1)[1].strip()
            direction = direction_map.get(raw, 'NEUTRAL')
        elif line.startswith('CONFIDENCE:'):
            confidence = line.split(':', 1)[1].strip()
        elif line.startswith('REASON:'):
            reason = line.split(':', 1)[1].strip()

    return MacroBias(
        direction=direction, confidence=confidence, event_type=event_type,
        headline=headline, reasoning=reason,
        timestamp=datetime.now(ET).isoformat(),
    )


def get_macro_bias(trade_date: str | None = None) -> MacroBias:
    """
    Get macro directional bias for the current (or specified) trading day.

    Steps:
    1. Check if today has a high-impact release (macro_calendar).
    2. If yes: fetch the most recent macro headline from a free source.
    3. Classify with Groq/Llama.
    4. Return MacroBias.

    Falls back to NEUTRAL if no release day or no Groq key.
    """
    from futures.macro_calendar import get_release_info, is_release_day

    d = trade_date or datetime.now(ET).date().isoformat()
    event = get_release_info(d)

    if not event:
        return MacroBias('NEUTRAL', 'HIGH', '', '',
                         'No high-impact release today', d)

    event_type = event['type']

    # Try to fetch a relevant headline
    headline = _fetch_latest_headline(event_type)
    if not headline:
        return MacroBias('NEUTRAL', 'LOW', event_type, '',
                         f'{event_type} release day but no headline found', d)

    return classify_headline(headline, event_type)


def _fetch_latest_headline(event_type: str) -> str:
    """
    Fetch the most recent relevant headline for a macro event.
    Uses free sources: RSS feeds from BLS (NFP/CPI), Fed (FOMC), BEA (GDP).
    Returns headline string or empty string if unavailable.
    """
    # Try each source; return first working result
    sources = {
        'NFP':  _fetch_bls_latest,
        'CPI':  _fetch_bls_latest,
        'FOMC': _fetch_fed_latest,
        'GDP':  _fetch_bea_latest,
        'PPI':  _fetch_bls_latest,
    }
    fetcher = sources.get(event_type)
    if fetcher:
        try:
            return fetcher(event_type)
        except Exception:
            pass

    # Generic fallback: news search
    return _fetch_news_search(event_type)


def _fetch_bls_latest(event_type: str) -> str:
    """Fetch latest BLS headline (NFP/CPI/PPI) from BLS RSS."""
    import requests
    # BLS publishes press releases here
    url = 'https://www.bls.gov/bls/newrelease.htm'
    resp = requests.get(url, timeout=10,
                        headers={'User-Agent': 'MNQ-bot/1.0 (research)'})
    if resp.status_code != 200:
        return ''
    # Very basic scrape — just grab the first relevant line
    text = resp.text
    keywords = {'NFP': 'nonfarm', 'CPI': 'consumer price', 'PPI': 'producer price'}
    kw = keywords.get(event_type, '').lower()
    for line in text.split('\n'):
        if kw in line.lower() and len(line.strip()) > 20:
            return line.strip()[:300]
    return ''


def _fetch_fed_latest(event_type: str) -> str:
    """Stub — Fed press release content requires a more targeted scrape."""
    return ''


def _fetch_bea_latest(event_type: str) -> str:
    """Stub — BEA GDP content requires a more targeted scrape."""
    return ''


def _fetch_news_search(event_type: str) -> str:
    """Generic fallback — search for event headline in free financial news."""
    try:
        import requests
        query_map = {
            'NFP': 'US nonfarm payrolls jobs report today',
            'CPI': 'US inflation CPI report today',
            'FOMC': 'Federal Reserve interest rate decision today',
            'GDP': 'US GDP report today',
            'PPI': 'US producer price index today',
        }
        query = query_map.get(event_type, f'{event_type} economic report')
        # Use DuckDuckGo instant answer API (free, no key required)
        resp = requests.get(
            'https://api.duckduckgo.com/',
            params={'q': query, 'format': 'json', 'no_html': 1},
            timeout=10,
            headers={'User-Agent': 'MNQ-bot/1.0'},
        )
        if resp.status_code == 200:
            data = resp.json()
            abstract = data.get('AbstractText') or data.get('Answer', '')
            if abstract:
                return abstract[:300]
    except Exception:
        pass
    return ''


# ── Caching (avoid repeated Groq calls within the same session) ───────────────

_bias_cache: dict[str, MacroBias] = {}

def get_macro_bias_cached(trade_date: str | None = None) -> MacroBias:
    """
    Cached version — returns the same result within a trading session.
    Call once at startup, reuse throughout the day.
    """
    d = trade_date or datetime.now(ET).date().isoformat()
    if d not in _bias_cache:
        _bias_cache[d] = get_macro_bias(d)
    return _bias_cache[d]


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    from futures.macro_calendar import get_release_info, days_until_next_release
    from datetime import date

    today = date.today().isoformat()
    event = get_release_info(today)

    print(f'\n=== Macro Bias — {today} ===')

    if not event:
        days, d_str, evt = days_until_next_release()
        print(f'  No high-impact release today.')
        print(f'  Next: {evt} on {d_str} ({days} days away)')
    else:
        print(f'  Release today: {event["type"]} at {event["time_et"].strftime("%H:%M")} ET')
        print(f'  Fetching headline and classifying...')
        bias = get_macro_bias(today)
        print(f'\n  Direction:  {bias.direction}')
        print(f'  Confidence: {bias.confidence}')
        print(f'  Event:      {bias.event_type}')
        print(f'  Headline:   {bias.headline[:80] if bias.headline else "(not found)"}')
        print(f'  Reasoning:  {bias.reasoning}')
        print(f'\n  Actionable: {bias.is_actionable}')
        if bias.is_actionable:
            print(f'  → Trade {bias.direction} setups with extra conviction today.')
    print()
