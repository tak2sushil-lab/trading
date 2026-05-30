#!/usr/bin/env python3
"""
news_engine.py — Options news scanner and classifier
Runs standalone every 15 min during market hours (9:30am–4:00pm ET).

Sources:  yfinance (free, always on)
          Polygon.io (optional — set POLYGON_API_KEY in .env)
          Alpha Vantage (optional — set ALPHA_VANTAGE_KEY in .env)

Pipeline: fetch → dedup → Claude API classify → store MEDIUM/HIGH → alert HIGH
"""

import os
import sys
import time
import json
import requests
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import yfinance as yf
import anthropic
from groq import Groq
from dotenv import load_dotenv

# ── Path setup: import database from parent trading/ folder ──
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import (
    log_options_news, add_catalyst,
    headline_seen_recently, get_open_options_trades,
    upsert_catalyst_from_wsh,
    recompute_conviction, update_conviction_narrative, update_conviction_iv,
    log_suggestion, get_suggestions_today_count,
)

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

# ── Credentials + config ──────────────────────────────────
ET_TZ             = ZoneInfo('America/New_York')

US_HOLIDAYS_2026 = {
    date(2026,  1,  1), date(2026,  1, 19), date(2026,  2, 16),
    date(2026,  4,  3), date(2026,  5, 25), date(2026,  6, 19),
    date(2026,  7,  3), date(2026,  9,  7), date(2026, 11, 26),
    date(2026, 12, 25),
}
BRIDGE_URL        = os.getenv('BRIDGE_URL', 'http://127.0.0.1:8000')
TELEGRAM_TOKEN    = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID  = os.getenv('TELEGRAM_CHAT_ID')
# Options-specific bot (falls back to main bot if not configured)
OPT_TG_TOKEN     = os.getenv('OPTIONS_TELEGRAM_TOKEN') or TELEGRAM_TOKEN
OPT_TG_CHAT_ID   = os.getenv('OPTIONS_TELEGRAM_CHAT_ID') or TELEGRAM_CHAT_ID
ANTHROPIC_KEY     = os.getenv('ANTHROPIC_KEY')
GROQ_KEY          = os.getenv('GROQ_API_KEY')          # free tier — used for LLM classification
POLYGON_KEY       = os.getenv('POLYGON_API_KEY')       # optional — $29/mo
ALPHA_VANTAGE_KEY = os.getenv('ALPHA_VANTAGE_KEY')     # optional — free 25 calls/day
FINNHUB_KEY       = os.getenv('FINNHUB_API_KEY')        # optional — free 60 calls/min
TG_API            = f"https://api.telegram.org/bot{OPT_TG_TOKEN}"

SCAN_INTERVAL_MIN = 15     # minutes between scans — safe on Groq free tier (dedup keeps LLM calls <25/day)
MAX_AGE_HOURS     = 16     # fetch window: covers overnight news at 9:30am open
                           # (4pm close + ~12h gap to open = need ≥13h; 16h gives margin)
DEDUP_HOURS       = 24     # dedup window: must be >= MAX_AGE_HOURS to prevent re-classifying

# Alpha Vantage free tier: 25 calls/day.  We call once per symbol per calendar
# day (13 symbols = 13 calls).  This set resets at midnight automatically
# because process restarts or we check the date inside the guard.
_av_called_today: set = set()   # 'SYMBOL|YYYY-MM-DD' keys

# ── Options universe to monitor ──────────────────────────
# Full equity universe — same symbols as FULL_UNIVERSE in auto_trader.py.
# Gates handle quality filtering; no pre-filtering needed here.
# RDW excluded: IV 173% chronic, options market confirmed illiquid.
OPTIONS_SYMBOLS = [
    'AAPL', 'PLTR', 'COHR', 'IONQ', 'HOOD', 'JPM', 'IREN', 'NUTX',
    'LITE', 'VST', 'ITA', 'NFLX', 'ORCL', 'OKLO', 'AMZN', 'GOOGL',
    'CRM', 'QBTS', 'TOST', 'AVGO', 'NBIS', 'CLS', 'RKLB', 'CNQ',
    'AMD', 'RKT', 'NU', 'MSFT', 'META', 'GS', 'CRWV', 'SMCI', 'RBRK',
    'AI', 'RGTI', 'USAR', 'FSLR', 'CCJ', 'UUUU', 'DNN', 'LLY', 'NTLA',
    'BEAM', 'APLD', 'SOUN', 'BBAI', 'ON', 'LRCX', 'DDOG', 'MDB', 'POET',
    'EOSE', 'INDI', 'NVDA', 'INTC', 'TSLA', 'CVX', 'XOM', 'OXY', 'SLB',
    'HAL', 'DVN', 'XLE', 'UNH', 'MRNA', 'PFE', 'ABBV', 'ISRG', 'DXCM',
    'HIMS', 'XBI', 'COST', 'NKE', 'SBUX', 'CMG', 'UBER', 'BAC', 'C',
    'WFC', 'V', 'MA', 'COIN', 'RTX', 'LMT', 'NOC', 'CAT', 'DE', 'QCOM',
    'MRVL', 'KLAC', 'AMAT', 'MU', 'SMH', 'LAC', 'RIVN', 'NIO', 'CHPT',
    'FCX', 'NEM', 'MP', 'APP', 'MARA', 'ARM', 'AXON', 'SHOP', 'MSTR',
    'ONDS', 'VERI', 'JOBY', 'CLSK', 'WULF', 'HUT', 'ARRY', 'RIOT', 'EQT',
    'CIFR', 'CPNG', 'SITM', 'KTOS', 'ACLS', 'CTRA', 'CACI', 'FTNT', 'IBKR',
    'ONTO', 'SAIC', 'BWXT', 'SAIA', 'HWM', 'GDDY', 'EW', 'KKR', 'TPR',
    'GILD', 'GE', 'TXT', 'YUM', 'BSX', 'HOLX', 'TT', 'UPST', 'CELH', 'HL',
    'ZM', 'DUOL', 'RBLX', 'WFRD', 'TTD', 'TWLO', 'AG', 'DOCU', 'ZS', 'HUBS',
    'OKTA', 'DECK', 'LULU', 'PANW', 'AEM', 'AEHR', 'APD', 'HXL', 'SSYS',
    'CRDO', 'OUST', 'AXTI', 'CRWD', 'AFRM', 'SOFI',
]

# ── Domain knowledge primes ───────────────────────────────
# Sushil's IT sector knowledge seeded as LLM context.
# These are NOT insider info — public knowledge framed as trading context.
SYMBOL_CONTEXT = {
    'NVDA': (
        "China export revenue ~25% of data center segment — any export restriction is HIGH. "
        "GTC conference (March) is a tier-1 event. H100/Blackwell chip demand from "
        "hyperscalers (AWS, Azure, Google) is the core growth driver. "
        "Competitor GPU launches from AMD/Intel matter as sector context."
    ),
    'META': (
        "Llama model releases are tier-1 events. Any competitor LLM news (OpenAI GPT, "
        "Google Gemini) is relevant sector context. Reality Labs losses are noise. "
        "AI infra capex guidance and ad revenue growth drive the stock."
    ),
    'AMZN': (
        "AWS capacity expansions (H100 orders, new data center regions) are leading "
        "indicators for cloud revenue. Bedrock/AI services announcements matter. "
        "Retail segment is largely noise for options plays on AMZN."
    ),
    'MSFT': (
        "Azure AI capacity tied to OpenAI partnership is the key driver. "
        "Enterprise AI contract wins (Fortune 500 Copilot deployments) matter. "
        "Gaming and LinkedIn segments are noise for momentum plays."
    ),
    'AAPL': (
        "iPhone supercycle upgrades and services revenue growth are key drivers. "
        "AI features (Apple Intelligence) adoption and Siri improvements matter. "
        "China iPhone sales data and regulatory App Store news are HIGH impact. "
        "Supply chain disruptions (TSMC, Foxconn) are relevant risk signals."
    ),
    'GOOGL': (
        "Search market share vs AI alternatives (ChatGPT, Perplexity) is the core risk. "
        "Gemini model releases and YouTube AI monetization are positive catalysts. "
        "DOJ antitrust proceedings are a structural overhang — any ruling is HIGH. "
        "Cloud (GCP) AI wins vs AWS/Azure matter for momentum."
    ),
    'TSLA': (
        "Quarterly delivery numbers and margin guidance are key. "
        "FSD/autonomous driving news moves IV significantly. "
        "China sales data (weekly registration figures) matters. "
        "Elon Musk non-Tesla news (DOGE, xAI) is largely noise for TSLA options."
    ),
    'AMD': (
        "Data center GPU market share vs NVDA is the core story — MI300X/MI400 adoption. "
        "Hyperscaler (AWS, Azure, Google) AI chip orders matter. "
        "PC and gaming segment performance is noise for momentum options plays."
    ),
    'ORCL': (
        "Cloud infrastructure (OCI) growth vs AWS/Azure is the key metric. "
        "AI database contracts and partnerships (Nvidia, Microsoft, OpenAI) are HIGH. "
        "Database migration wins from on-prem to OCI drive recurring revenue. "
        "Government cloud contracts are tier-1 catalysts."
    ),
    'COIN': (
        "Bitcoin price is a leading indicator — COIN revenue is ~70% crypto-correlated. "
        "Regulatory news (SEC, CFTC, stablecoin legislation) is HIGH impact. "
        "New product launches (futures, international, Base L2) expand revenue base. "
        "Institutional crypto adoption news drives sustained volume."
    ),
    'PLTR': (
        "Government contract wins (DoD, DHS, NATO allies, intelligence agencies) are HIGH. "
        "AIP (AI Platform) enterprise deals are key growth metric. "
        "CEO Alex Karp statements on AI are more substantive than most tech CEOs. "
        "SPAC/secondary offerings can be BEARISH near-term."
    ),
    'APP': (
        "AI advertising platform — efficiency improvements in AXON AI engine matter. "
        "Big Tech ad spend commentary (Meta/Google earnings) is relevant sector context. "
        "E-commerce and gaming client wins are positive signals."
    ),
    'CRWD': (
        "Endpoint security market share gains vs MSFT Defender and SentinelOne matter. "
        "Enterprise cybersecurity spending guidance from CIOs is sector context. "
        "Any platform outage news (ref: July 2024 incident) is HIGH negative. "
        "Government security contracts and FedRAMP authorizations are positive catalysts."
    ),
    'AXON': (
        "Law enforcement agency contract wins (police, federal) drive revenue. "
        "TASER and body camera fleet renewals are recurring revenue signals. "
        "Drone and AI evidence platform (Evidence.com) expansion is the growth story. "
        "Regulatory news on police technology funding matters."
    ),
    'ARM': (
        "AI chip licensing deals — Nvidia, Apple, Qualcomm royalty streams are key. "
        "Data center CPU adoption (AWS Graviton, Ampere) is the secular growth story. "
        "Smartphone market share (iOS/Android SoC design wins) matters. "
        "China revenue risk (~20% of sales) is a structural concern — any news HIGH."
    ),
    'HIMS': (
        "GLP-1 weight loss drug access and compounding pharmacy regulatory news is HIGH. "
        "FDA decisions on telehealth prescribing rules directly impact revenue model. "
        "Subscriber growth and ARPU guidance drive momentum. "
        "Competitive entry from Amazon/CVS into telehealth is a key risk signal."
    ),
    'HOOD': (
        "PFOF regulatory news is HIGH impact — could structurally change revenue. "
        "Crypto trading volumes are leading indicators for quarter revenue. "
        "New product launches (options flow, futures, international) matter. "
        "Fed rate moves affect cash sweep revenue meaningfully."
    ),
    'SMCI': (
        "AI server rack demand from hyperscalers is the core growth driver. "
        "Nvidia GPU allocation for AI server builds directly impacts SMCI revenue. "
        "Accounting/audit news is HIGH risk — history of restatement concerns. "
        "Liquid cooling technology wins for next-gen AI data centers matter."
    ),
    'MARA': (
        "Bitcoin price is the primary driver — MARA is a leveraged BTC proxy. "
        "Mining hash rate efficiency and energy cost news matter for margins. "
        "BTC halving events affect mining economics over multi-quarter horizon. "
        "Institutional BTC ETF flow news is positive sector context."
    ),
    'SHOP': (
        "GMV (gross merchandise volume) growth and merchant adds drive revenue. "
        "AI commerce features (Shopify Magic, Sidekick) are the next growth layer. "
        "Enterprise merchant wins (moving upmarket from SMB) are positive signals. "
        "Take rate improvements and fintech (Shopify Balance, Payments) expansion matter."
    ),
    'RKLB': (
        "Launch manifest updates and backlog announcements are key. "
        "NASA contracts and DoD payload wins matter. "
        "Competitor news (SpaceX Starship issues or delays) is BULLISH for RKLB. "
        "Neutron rocket development milestones are long-horizon catalysts."
    ),
    'IONQ': (
        "Quantum computing milestones and government funding are key. "
        "IBM/Google quantum news provides relevant sector context. "
        "Pure-play quantum — very news-sensitive, even small milestones can move IV. "
        "NSA/DoD quantum contracts are HIGH impact."
    ),
    'CELH': (
        "International expansion (Europe, Asia distribution deals) is the growth story. "
        "PepsiCo distribution partnership execution and shelf space wins matter. "
        "Energy drink market share vs Monster/Red Bull in Nielsen data matters. "
        "FTC/regulatory scrutiny on energy drink marketing is a risk signal."
    ),
    'AFRM': (
        "Buy-now-pay-later volume growth and merchant partner adds drive revenue. "
        "Interest rate direction is critical — higher rates compress AFRM margins. "
        "Apple Pay Later / Klarna competitive announcements are relevant sector context. "
        "Delinquency rate and credit quality guidance are key risk signals."
    ),
    'SOFI': (
        "Student loan volume and refinancing activity are key revenue drivers. "
        "Banking charter allows deposits — rate environment directly impacts NIM. "
        "Loan origination growth and credit quality (charge-off rates) matter. "
        "Fintech banking regulation and OCC guidance are HIGH impact."
    ),
}

DEFAULT_CONTEXT = (
    "US large/mid cap tech stock. Focus on: earnings guidance changes, "
    "major partnerships, regulatory news, product launches, and management changes. "
    "Ignore generic market commentary and analyst reiterations."
)

# ── LLM client: Groq (free) preferred, Claude fallback ───
# Groq runs Llama 3 70B — same classification quality, zero cost.
# Claude is used only if GROQ_KEY is absent and ANTHROPIC_KEY is set.
if GROQ_KEY:
    _groq_client = Groq(api_key=GROQ_KEY)
    ai = None   # Claude client not needed
else:
    _groq_client = None
    ai = anthropic.Anthropic(api_key=ANTHROPIC_KEY) if ANTHROPIC_KEY else None

_groq_fallback_active = False  # flips True when 70B daily cap is hit

def _llm_call(prompt: str, max_tokens: int) -> str:
    """Single LLM call — Groq 70B → 8B fallback on daily cap → Claude fallback."""
    global _groq_fallback_active
    if _groq_client:
        models = (
            ['llama-3.1-8b-instant'] if _groq_fallback_active
            else ['llama-3.3-70b-versatile', 'llama-3.1-8b-instant']
        )
        for model in models:
            try:
                resp = _groq_client.chat.completions.create(
                    model    = model,
                    messages = [{'role': 'user', 'content': prompt}],
                    max_tokens      = max_tokens,
                    temperature     = 0,
                    response_format = {'type': 'json_object'},
                )
                if model != 'llama-3.3-70b-versatile' and not _groq_fallback_active:
                    pass  # already tried 70B, this is the fallback
                return resp.choices[0].message.content.strip()
            except Exception as e:
                err = str(e)
                if '429' in err and 'tokens per day' in err:
                    if model == 'llama-3.3-70b-versatile':
                        print(f'[LLM] 70B daily cap hit — switching to 8B for rest of day')
                        _groq_fallback_active = True
                        continue  # retry with next model
                raise  # non-rate-limit error — propagate
    if ai:
        resp = ai.messages.create(
            model    = 'claude-haiku-4-5-20251001',
            max_tokens = max_tokens,
            messages = [{'role': 'user', 'content': prompt}],
        )
        return resp.content[0].text.strip()
    raise RuntimeError('No LLM configured — set GROQ_API_KEY or ANTHROPIC_KEY')


# ── Telegram ──────────────────────────────────────────────
def send_telegram(message: str):
    if not OPT_TG_TOKEN or not OPT_TG_CHAT_ID:
        print(f"[TG] {message}")
        return
    try:
        requests.post(
            f"{TG_API}/sendMessage",
            json={'chat_id': OPT_TG_CHAT_ID, 'text': message,
                  'parse_mode': 'HTML'},
            timeout=10,
        )
    except Exception as e:
        print(f"[TG ERROR] {e}")


# ── Market hours check ────────────────────────────────────
def is_trading_hours() -> bool:
    """
    4:00am–8:00pm ET, Mon-Fri.
    Covers pre-market (4-9:30), regular (9:30-4), and after-hours (4-8pm).
    Pre-market catches FDA decisions, guidance, international news.
    After-hours catches earnings releases (typically 4-5pm) while IBKR
    still allows options trading until 8pm.
    """
    now = datetime.now(ET_TZ)
    if now.weekday() >= 5:
        return False
    if now.date() in US_HOLIDAYS_2026:
        return False
    open_  = now.replace(hour=4,  minute=0, second=0, microsecond=0)
    close_ = now.replace(hour=20, minute=0, second=0, microsecond=0)
    return open_ <= now <= close_


# ── IV rank from bridge ───────────────────────────────────
def get_iv_rank(symbol: str) -> dict | None:
    try:
        r = requests.get(f"{BRIDGE_URL}/options/iv_rank/{symbol}", timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


# ── News fetchers ─────────────────────────────────────────
def fetch_yfinance_news(symbol: str) -> list[dict]:
    """
    yfinance changed its news schema (May 2025+): each item is now
    {'id': ..., 'content': {'title': ..., 'pubDate': 'ISO-Z', ...}}
    Fall back to the old flat schema if 'content' is absent.
    """
    try:
        cutoff = time.time() - (MAX_AGE_HOURS * 3600)
        ticker = yf.Ticker(symbol)
        news   = ticker.news or []
        result = []
        for item in news:
            content = item.get('content') or item   # new schema vs old
            title   = content.get('title', '').strip()
            if not title:
                continue
            # pubDate is ISO "2026-05-09T18:36:38Z"; old schema is unix int
            raw_ts = content.get('pubDate') or content.get('displayTime')
            if raw_ts and isinstance(raw_ts, str):
                try:
                    dt = datetime.fromisoformat(raw_ts.replace('Z', '+00:00'))
                    ts = dt.timestamp()
                except Exception:
                    ts = 0
            else:
                ts = content.get('providerPublishTime', 0) or item.get('providerPublishTime', 0)
            if ts < cutoff:
                continue
            result.append({
                'title':        title,
                'published_at': datetime.fromtimestamp(ts).isoformat(),
                'source':       'yfinance',
            })
        return result
    except Exception as e:
        print(f"[yfinance] {symbol}: {e}")
        return []


def fetch_polygon_news(symbol: str) -> list[dict]:
    if not POLYGON_KEY:
        return []
    try:
        since = (datetime.utcnow() - timedelta(hours=MAX_AGE_HOURS)).strftime('%Y-%m-%dT%H:%M:%SZ')
        r = requests.get(
            'https://api.polygon.io/v2/reference/news',
            params={'ticker': symbol, 'limit': 20,
                    'apiKey': POLYGON_KEY, 'published_utc.gte': since},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        return [{'title':        i.get('title', '').strip(),
                 'published_at': i.get('published_utc', ''),
                 'source':       'polygon'}
                for i in r.json().get('results', []) if i.get('title')]
    except Exception as e:
        print(f"[polygon] {symbol}: {e}")
        return []


def fetch_alpha_vantage_news(symbol: str) -> list[dict]:
    """
    Alpha Vantage free tier = 25 calls/day.  Guard: one call per symbol per
    calendar day so 13 symbols = 13 calls, well within the limit.
    """
    if not ALPHA_VANTAGE_KEY:
        return []
    today_key = f"{symbol}|{datetime.now().strftime('%Y-%m-%d')}"
    if today_key in _av_called_today:
        return []
    _av_called_today.add(today_key)
    try:
        r = requests.get(
            'https://www.alphavantage.co/query',
            params={'function': 'NEWS_SENTIMENT', 'tickers': symbol,
                    'apikey': ALPHA_VANTAGE_KEY, 'limit': 20},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        cutoff  = time.time() - (MAX_AGE_HOURS * 3600)
        results = []
        for item in r.json().get('feed', []):
            try:
                ts = datetime.strptime(
                    item.get('time_published', ''), '%Y%m%dT%H%M%S').timestamp()
            except Exception:
                continue
            if ts < cutoff:
                continue
            results.append({
                'title':        item.get('title', '').strip(),
                'published_at': datetime.fromtimestamp(ts).isoformat(),
                'source':       'alpha_vantage',
                'av_sentiment': item.get('overall_sentiment_label', ''),
            })
        return results
    except Exception as e:
        print(f"[alpha_vantage] {symbol}: {e}")
        return []


def fetch_finnhub_news(symbol: str) -> list[dict]:
    """
    Finnhub company-news endpoint. Free tier = 60 calls/min — fine for our scan.
    Separate from yfinance: different aggregation, often faster on US equities.
    Set FINNHUB_API_KEY in .env (free at finnhub.io, no card required).
    """
    if not FINNHUB_KEY:
        return []
    try:
        today  = datetime.now().strftime('%Y-%m-%d')
        since  = (datetime.now() - timedelta(hours=MAX_AGE_HOURS)).strftime('%Y-%m-%d')
        r = requests.get(
            'https://finnhub.io/api/v1/company-news',
            params={'symbol': symbol, 'from': since, 'to': today, 'token': FINNHUB_KEY},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        cutoff  = time.time() - (MAX_AGE_HOURS * 3600)
        results = []
        for item in r.json():
            ts = item.get('datetime', 0)
            if ts < cutoff:
                continue
            title = item.get('headline', '').strip()
            if not title:
                continue
            results.append({
                'title':        title,
                'published_at': datetime.fromtimestamp(ts).isoformat(),
                'source':       'finnhub',
            })
        return results
    except Exception as e:
        print(f"[finnhub] {symbol}: {e}")
        return []


def fetch_ibkr_news(symbol: str) -> list[dict]:
    """
    Fetch news via the IBKR bridge endpoint. Uses all providers subscribed
    on the account. Paper accounts typically get Globe Newswire + PR Newswire
    free; Briefing.com ($10/mo) and Dow Jones ($25/mo) activate automatically
    once subscribed in IBKR Account Management.
    """
    try:
        r = requests.get(
            f"{BRIDGE_URL}/options/news/{symbol}",
            params={'hours': MAX_AGE_HOURS, 'limit': 20},
            timeout=15,    # IBKR news calls can be slow
        )
        if r.status_code != 200:
            return []
        data = r.json()
        if data.get('error') or not data.get('news'):
            return []
        return [
            {
                'title':        item['headline'].strip(),
                'published_at': item['time'],
                'source':       f"ibkr_{item['provider'].lower()}",
            }
            for item in data['news']
            if item.get('headline')
        ]
    except Exception as e:
        print(f"[ibkr_news] {symbol}: {e}")
        return []


# ── LLM classifier ────────────────────────────────────────
_CLASSIFY_PROMPT = """You are a financial news classifier for an options trader who buys LEAP calls and bull call spreads.

Stock: {symbol}
Context: {context}{av_hint}

Headline: "{headline}"

Return ONLY valid JSON — no extra text, no markdown:
{{
  "relevance": "HIGH|MEDIUM|LOW|NOISE",
  "news_type": "PARTNERSHIP|REGULATORY|EARNINGS_SIGNAL|PRODUCT|LAYOFF|MACRO|ANALYST|CEO_COMMENT|LEGAL|SECTOR",
  "direction": "BULLISH|BEARISH|NEUTRAL",
  "time_horizon": "IMMEDIATE|SHORT|LONG",
  "already_priced_in": "YES|NO|UNCLEAR",
  "creates_future_event": false,
  "future_event_date": null,
  "future_event_name": null,
  "one_line_reason": "one sentence"
}}

Relevance rules:
  NOISE  — generic AI commentary, index moves, analyst reiterations with no change
  LOW    — minor, unlikely to move IV materially
  MEDIUM — noteworthy, worth tracking, moderate IV impact possible
  HIGH   — likely to move IV or price materially within days to weeks
  creates_future_event = true only if headline mentions a specific future dated event
  future_event_date in YYYY-MM-DD format if creates_future_event is true else null"""


def classify_headline(symbol: str, headline: str,
                      av_sentiment: str | None = None) -> dict | None:
    if not _groq_client and not ai:
        return None
    context  = SYMBOL_CONTEXT.get(symbol, DEFAULT_CONTEXT)
    av_hint  = f"\nAlpha Vantage pre-label: {av_sentiment}" if av_sentiment else ''
    prompt   = _CLASSIFY_PROMPT.format(
        symbol=symbol, context=context, av_hint=av_hint, headline=headline
    )
    try:
        text = _llm_call(prompt, max_tokens=300)
        if '```' in text:
            parts = text.split('```')
            text  = parts[1].lstrip('json').strip() if len(parts) > 1 else text
        return json.loads(text)
    except Exception as e:
        print(f"[LLM] {symbol} classify error: {e}")
        return None


# ── Noise pre-filter (no LLM cost) ───────────────────────
import re as _re
_NOISE_RE = _re.compile(
    r'\b(reiterates?|reaffirms?|maintains?)\b.{0,40}'
    r'\b(buy|hold|sell|neutral|overweight|underweight|outperform|underperform|market\s*perform)\b',
    _re.I
)

def _is_obvious_noise(headline: str) -> bool:
    """Return True for analyst reiterations that never need LLM classification."""
    return bool(_NOISE_RE.search(headline))


# ── Batch classifier — one LLM call per symbol per scan ──
_BATCH_CLASSIFY_PROMPT = (
    "You are a financial news classifier for an options trader who buys LEAP calls "
    "and bull call spreads.\n\n"
    "Stock: {symbol}\nContext: {context}\n\n"
    "Classify each numbered headline. Return ONLY a valid JSON array — no extra text, "
    "no markdown. Array length must equal the number of headlines.\n"
    "Each element schema:\n"
    '{{\"relevance\":\"HIGH|MEDIUM|LOW|NOISE\",\"news_type\":\"PARTNERSHIP|REGULATORY|'
    'EARNINGS_SIGNAL|PRODUCT|LAYOFF|MACRO|ANALYST|CEO_COMMENT|LEGAL|SECTOR\",'
    '\"direction\":\"BULLISH|BEARISH|NEUTRAL\",\"time_horizon\":\"IMMEDIATE|SHORT|LONG\",'
    '\"already_priced_in\":\"YES|NO|UNCLEAR\",\"creates_future_event\":false,'
    '\"future_event_date\":null,\"future_event_name\":null,\"one_line_reason\":\"one sentence\"}}\n\n'
    "Headlines:\n{headlines}"
)


def classify_headlines_batch(symbol: str,
                              items: list[dict]) -> list[dict | None]:
    """
    Classify a batch of headlines for one symbol in a single LLM call.
    items: list of {'title': str, 'av_sentiment': str|None}
    Returns list of clf dicts (or None on error) — same length as items.
    """
    if not _groq_client and not ai:
        return [None] * len(items)
    if not items:
        return []

    headlines = items
    context   = SYMBOL_CONTEXT.get(symbol, DEFAULT_CONTEXT)
    numbered  = '\n'.join(f'{i+1}. "{it["title"]}"' for i, it in enumerate(headlines))
    prompt    = _BATCH_CLASSIFY_PROMPT.format(
        symbol=symbol, context=context, headlines=numbered
    )
    try:
        text = _llm_call(prompt, max_tokens=200 * len(headlines))
        if '```' in text:
            parts = text.split('```')
            text  = parts[1].lstrip('json').strip() if len(parts) > 1 else text
        results = json.loads(text)
        if isinstance(results, list) and len(results) == len(headlines):
            return results
        print(f"[LLM] {symbol} batch length mismatch ({len(results)} vs {len(headlines)}) — retrying individually")
    except Exception as e:
        print(f"[LLM] {symbol} batch classify error: {e} — retrying individually")

    # Fallback: classify each headline individually so a token-limit truncation
    # doesn't zero out the entire batch.
    return [classify_headline(symbol, it['title'], it.get('av_sentiment')) for it in headlines]


# ── Per-ticker daily alert dedup ──────────────────────────────────────────────
_alerted_today:     set[str] = set()   # symbols already alerted this calendar day
_alerted_date:      str      = ''      # date string when set was last reset
_alerts_sent_today: int      = 0       # daily cap: max 5 tier-change alerts
_ALERTED_FILE = os.path.join(os.path.dirname(__file__), '.alerted_today.json')


def _load_alerted_file(today: str) -> set[str]:
    """Reload _alerted_today from disk so restarts don't lose dedup state."""
    try:
        with open(_ALERTED_FILE) as f:
            data = json.load(f)
        if data.get('date') == today:
            return set(data.get('symbols', []))
    except Exception:
        pass
    return set()


def _save_alerted_file():
    try:
        with open(_ALERTED_FILE, 'w') as f:
            json.dump({'date': _alerted_date, 'symbols': list(_alerted_today)}, f)
    except Exception:
        pass


def _check_reset_daily():
    global _alerted_today, _alerted_date, _alerts_sent_today
    today = datetime.now(ET_TZ).strftime('%Y-%m-%d')
    if _alerted_date != today:
        _alerted_today     = _load_alerted_file(today)
        _alerted_date      = today
        _alerts_sent_today = 0


# ── Consolidated per-ticker alert ─────────────────────────────────────────────
def _generate_narrative(symbol: str, signals: list[dict]) -> str | None:
    """
    One LLM call: synthesise a 2-sentence bull/bear thesis from signal reasons.
    Called only when a ticker first reaches HIGH tier — not on every scan.
    """
    if not _groq_client and not ai:
        return None
    if not signals:
        return None
    reasons = '\n'.join(
        f"- [{s['clf'].get('direction','')}] {s['clf'].get('one_line_reason', s['headline'][:80])}"
        for s in signals
    )
    try:
        return _llm_call(
            f"Stock: {symbol}\nSignals:\n{reasons}\n\n"
            "Write exactly 2 sentences: a bull/bear thesis summary an options trader "
            "would find useful. Mention IV if signals suggest event risk. Be specific, no filler.",
            max_tokens=120,
        )
    except Exception:
        return None


def _send_consolidated_alert(symbol: str, high_signals: list[dict],
                              linked_trade: dict | None,
                              conviction: dict | None = None,
                              narrative: str | None = None) -> None:
    """
    Send ONE Telegram message per ticker per day summarising all HIGH signals.
    Determines net direction (BULLISH / BEARISH / MIXED) and routes accordingly.
    """
    global _alerts_sent_today
    if symbol in _alerted_today:
        return   # safety guard — should_alert already checked, but be safe
    _alerted_today.add(symbol)
    _alerts_sent_today += 1
    _save_alerted_file()

    bull  = sum(1 for s in high_signals if s['clf'].get('direction') == 'BULLISH')
    bear  = sum(1 for s in high_signals if s['clf'].get('direction') == 'BEARISH')
    total = len(high_signals)

    if bull > bear:
        net_dir   = 'BULLISH'
        net_emoji = '🟢'
    elif bear > bull:
        net_dir   = 'BEARISH'
        net_emoji = '🔴'
    else:
        net_dir   = 'MIXED'
        net_emoji = '⚠️'

    # IV route (use first available)
    iv_data = get_iv_rank(symbol)
    if iv_data and iv_data.get('iv_rank') is not None:
        iv_rank = iv_data['iv_rank']
        if iv_rank < 30:
            iv_line = f"IV Rank: {iv_rank}% — cheap ✅"
            route   = "LEAP candidate" if net_dir == 'BULLISH' else "SKIP (bear signal)"
        elif iv_rank < 45:
            iv_line = f"IV Rank: {iv_rank}% — moderate"
            route   = "Bull Spread candidate" if net_dir == 'BULLISH' else "SKIP (bear signal)"
        else:
            iv_line = f"IV Rank: {iv_rank}% — expensive ⚠️"
            route   = "SKIP — premium too expensive"
    else:
        iv_line = "IV Rank: unavailable"
        route   = "Check IV manually"

    if net_dir == 'MIXED':
        route = "⚠️ MIXED signals — observe only, no entry"

    # Header
    count_label = f"{total} HIGH signal{'s' if total > 1 else ''}"
    if linked_trade:
        header = (f"⚡ <b>{symbol}</b> — {count_label} | OPEN POSITION\n"
                  f"Open: {linked_trade['strategy']} "
                  f"(grade {linked_trade.get('entry_grade', '?')})")
    else:
        header = f"📡 <b>{symbol}</b> — {count_label} | Net: {net_emoji} {net_dir}"

    # Signal lines (one per article)
    dir_emoji = {'BULLISH': '🟢', 'BEARISH': '🔴', 'NEUTRAL': '⚪️'}
    lines = []
    for s in high_signals:
        clf  = s['clf']
        d    = clf.get('direction', 'NEUTRAL')
        nt   = clf.get('news_type', '')
        rsn  = clf.get('one_line_reason', '')
        hdl  = s['headline'][:90]
        lines.append(f"{dir_emoji.get(d,'⚪️')} {nt}: {hdl}\n   ↳ {rsn}")

    signals_block = '\n'.join(lines)

    # Conviction tier line
    if conviction:
        tier_emoji = {'HIGH': '🔥', 'MEDIUM': '⚡', 'LOW': '💤'}.get(conviction['tier'], '')
        tier_line  = (f"Conviction: {tier_emoji} {conviction['tier']} "
                      f"| Score {conviction['score']:.2f} "
                      f"| {conviction['signal_count']} signals ({conviction['high_count']} HIGH)")
    else:
        tier_line = ''

    narrative_line = f"Thesis: {narrative}" if narrative else ''

    parts = [
        header,
        '─' * 28,
        signals_block,
        '─' * 28,
        iv_line,
        f"Route: {route}",
    ]
    if tier_line:
        parts.append(tier_line)
    if narrative_line:
        parts.append(narrative_line)
    # Queue auto-suggest: options_trader will run the calculator and send CONFIRM/SKIP
    if net_dir == 'BULLISH' and get_suggestions_today_count() < 5:
        try:
            score = conviction['score'] if conviction else 0.0
            n_sig = conviction['signal_count'] if conviction else len(high_signals)
            log_suggestion(symbol, score, n_sig)
            parts.append(f"<i>Auto-analysis queued — you will receive a trade suggestion shortly</i>")
        except Exception:
            parts.append(f"Reply <code>OPT BUY {symbol}</code> to see trade options")
    else:
        parts.append(f"Reply <code>OPT BUY {symbol}</code> to see trade options")

    send_telegram('\n'.join(parts))


# ── Per-symbol processor ──────────────────────────────────
def process_symbol(symbol: str, open_trades: list[dict]) -> int:
    """
    Fetch, dedup, classify, store, and alert for one symbol.
    Returns number of HIGH signals found.
    """
    # Collect from all sources (each returns [] if not configured / rate-limited)
    all_items: list[dict] = []
    all_items.extend(fetch_yfinance_news(symbol))
    all_items.extend(fetch_finnhub_news(symbol))
    all_items.extend(fetch_ibkr_news(symbol))
    all_items.extend(fetch_polygon_news(symbol))
    all_items.extend(fetch_alpha_vantage_news(symbol))

    if not all_items:
        return 0

    # Dedup within this batch by title (first 150 chars) + against DB
    seen_titles: set[str] = set()
    unique: list[dict]    = []
    for item in all_items:
        title   = item['title']
        snippet = title[:150].strip().lower()
        if snippet in seen_titles:
            continue
        if headline_seen_recently(symbol, title, hours=DEDUP_HOURS):
            continue
        seen_titles.add(snippet)
        unique.append(item)

    if not unique:
        return 0

    # Sort newest-first so the cap keeps the most recent articles
    unique.sort(key=lambda x: x.get('published_at', ''), reverse=True)

    open_sym_map = {t['symbol']: t for t in open_trades}
    high_count   = 0
    high_signals: list[dict] = []

    # ── Pre-filter obvious noise (no LLM cost) ───────────────
    to_classify: list[dict] = []
    for item in unique:
        if _is_obvious_noise(item['title']):
            log_options_news(
                symbol=symbol, headline=item['title'][:150], source=item['source'],
                published_at=item.get('published_at', ''), relevance='NOISE',
                news_type='ANALYST', direction='', time_horizon='',
                already_priced_in='', creates_future_event=0, one_line_reason='analyst reiteration',
            )
            print(f"[NEWS] {symbol} NOISE  pre-filter            | {item['title'][:70]}")
        else:
            to_classify.append(item)

    # Cap at 3 most recent per batch — prevents first-scan token burst.
    # Older articles will be picked up in the next scan cycle (they stay new
    # in the dedup window until classified and stored in DB).
    to_classify = to_classify[:3]

    if not to_classify:
        pass  # fall through to conviction recompute
    else:
        # ── Batch classify up to 3 headlines in ONE LLM call ──
        clfs = classify_headlines_batch(symbol, to_classify)

        for item, clf in zip(to_classify, clfs):
            headline = item['title']
            source   = item['source']
            pub_at   = item.get('published_at', '')

            if clf is None:
                continue

            relevance = clf.get('relevance', 'LOW')
            if relevance in ('LOW', 'NOISE'):
                log_options_news(
                    symbol=symbol, headline=headline[:150], source=source,
                    published_at=pub_at, relevance='NOISE', news_type='',
                    direction='', time_horizon='', already_priced_in='',
                    creates_future_event=0, one_line_reason='',
                )
                continue

            # Auto-create catalyst if future event detected
            catalyst_id = None
            if clf.get('creates_future_event') and clf.get('future_event_date'):
                catalyst_id = add_catalyst(
                    symbol              = symbol,
                    catalyst_type       = clf.get('news_type', 'SECTOR'),
                    event_name          = clf.get('future_event_name') or headline[:80],
                    event_date          = clf.get('future_event_date'),
                    confidence          = 'MEDIUM',
                    iv_rank_when_noted  = None,
                    news_source         = source,
                    notes               = clf.get('one_line_reason'),
                )
                print(f"[CATALYST] Auto-created: {symbol} "
                      f"{clf.get('future_event_date')} — "
                      f"{clf.get('future_event_name') or headline[:60]}")

            linked_trade    = open_sym_map.get(symbol)
            linked_trade_id = linked_trade['id'] if linked_trade else None

            log_options_news(
                symbol               = symbol,
                headline             = headline[:150],
                source               = source,
                published_at         = pub_at,
                relevance            = relevance,
                news_type            = clf.get('news_type', ''),
                direction            = clf.get('direction', ''),
                time_horizon         = clf.get('time_horizon', ''),
                already_priced_in    = clf.get('already_priced_in', ''),
                creates_future_event = 1 if clf.get('creates_future_event') else 0,
                one_line_reason      = clf.get('one_line_reason', ''),
                catalyst_id          = catalyst_id,
                linked_trade_id      = linked_trade_id,
            )

            print(f"[NEWS] {symbol} {relevance:6s} {clf.get('news_type',''):20s} "
                  f"{clf.get('direction',''):7s} | {headline[:70]}")

            if relevance == 'HIGH':
                high_count += 1
                high_signals.append({'headline': headline, 'clf': clf})

    # Recompute conviction score from all signals in last 5 days
    conviction = recompute_conviction(symbol) if (high_signals or high_count == 0) else None
    if not conviction:
        conviction = recompute_conviction(symbol)

    # Fetch and store IV rank (bridge call — fails silently if bridge is down)
    iv_data = get_iv_rank(symbol)
    if iv_data and iv_data.get('iv_rank') is not None:
        update_conviction_iv(symbol, round(iv_data['iv_rank'], 1))
        print(f"[IV] {symbol} iv_rank={iv_data['iv_rank']:.1f}%")

    # Alert only on tier upgrades (LOW→MEDIUM or MEDIUM→HIGH), cap at 5/day
    tier         = conviction['tier']
    tier_changed = conviction['tier_changed']
    should_alert = (
        tier_changed
        and tier in ('MEDIUM', 'HIGH')
        and _alerts_sent_today < 5
    )

    if should_alert:
        narrative = None
        if tier == 'HIGH' and high_signals:
            narrative = _generate_narrative(symbol, high_signals)
            if narrative:
                update_conviction_narrative(symbol, narrative)
        _send_consolidated_alert(symbol, high_signals, linked_trade, conviction, narrative)
        print(f"[CONVICTION] {symbol} → {tier} (score {conviction['score']:.2f}) | alert sent ({_alerts_sent_today}/5 today)")
    else:
        print(f"[CONVICTION] {symbol} → {tier} (score {conviction['score']:.2f}) | silent update")

    return high_count


# ── Wall Street Horizon catalyst sync ────────────────────
def sync_wsh_catalysts(days: int = 60) -> tuple[int, int]:
    """
    Pull upcoming corporate events from IBKR Wall Street Horizon for all
    OPTIONS_SYMBOLS and upsert into catalyst_calendar.
    Returns (added, skipped) counts.
    Safe to call repeatedly — upsert prevents duplicates.
    """
    added = skipped = 0
    importance_map = {'high': 'HIGH', 'medium': 'MEDIUM', 'low': 'LOW'}
    for sym in OPTIONS_SYMBOLS:
        try:
            r = requests.get(
                f"{BRIDGE_URL}/options/wsh_events/{sym}",
                params={'days': days},
                timeout=15,
            )
            if r.status_code != 200:
                continue
            data = r.json()
            for ev in data.get('events', []):
                confidence = importance_map.get(
                    str(ev.get('importance', '')).lower(), 'MEDIUM'
                )
                _, created = upsert_catalyst_from_wsh(
                    symbol        = sym,
                    catalyst_type = ev['event_type'],
                    event_name    = ev['event_name'],
                    event_date    = ev['event_date'],
                    confidence    = confidence,
                )
                if created:
                    added += 1
                else:
                    skipped += 1
        except Exception as e:
            print(f"[wsh_sync] {sym}: {e}")
        time.sleep(0.3)   # be gentle with bridge
    return added, skipped


# ── Main scan ─────────────────────────────────────────────
def run_scan():
    _check_reset_daily()   # reset _alerted_today and counter at day boundary
    now = datetime.now(ET_TZ)
    print(f"\n[SCAN] {now.strftime('%Y-%m-%d %H:%M ET')} | "
          f"{len(OPTIONS_SYMBOLS)} symbols")

    open_trades = get_open_options_trades()
    total_high  = 0

    for symbol in OPTIONS_SYMBOLS:
        high = process_symbol(symbol, open_trades)
        total_high += high
        time.sleep(0.5)   # gentle rate-limit between symbols

    # Recompute conviction for symbols with no new articles so recency decay applies
    for symbol in OPTIONS_SYMBOLS:
        recompute_conviction(symbol)

    print(f"[SCAN] Done | HIGH signals: {total_high}")
    return total_high


# ── Entry point ───────────────────────────────────────────
def main():
    if os.getenv('TRADING_MODE', 'paper') == 'live':
        if os.getenv('PROD_OPTIONS_ENABLED', 'false').lower() != 'true':
            print("PROD_OPTIONS_ENABLED is not 'true' in .env — exiting.")
            sys.exit(0)

    sources = 'yfinance + Finnhub + IBKR'
    if POLYGON_KEY:
        sources += ' + Polygon'
    if ALPHA_VANTAGE_KEY:
        sources += ' + Alpha Vantage'

    print('=' * 52)
    print('📡 news_engine.py starting')
    print(f'   Symbols  : {len(OPTIONS_SYMBOLS)}')
    print(f'   Interval : {SCAN_INTERVAL_MIN} min')
    print(f'   Sources  : {sources}')
    llm_label = ('groq/llama-3.3-70b (free)' if _groq_client
                 else 'claude-haiku (fallback)' if ai
                 else 'DISABLED — set GROK_API_KEY or ANTHROPIC_KEY')
    print(f'   LLM      : {llm_label}')
    print('=' * 52)

    send_telegram(
        f'📡 <b>Options news engine started</b>\n'
        f'Monitoring {len(OPTIONS_SYMBOLS)} symbols every {SCAN_INTERVAL_MIN} min\n'
        f'Sources: {sources}'
    )

    # Sync Wall Street Horizon corporate events into catalyst_calendar
    print('[WSH] Syncing corporate events from Wall Street Horizon ...')
    try:
        added, skipped = sync_wsh_catalysts(days=60)
        print(f'[WSH] Done — {added} new catalysts added, {skipped} already existed')
    except Exception as exc:
        print(f'[WSH] Sync failed (bridge may be down): {exc}')

    # Run immediately on startup if within trading hours
    if is_trading_hours():
        run_scan()
    else:
        print('[SCAN] Outside trading hours — waiting for next open')

    while True:
        time.sleep(SCAN_INTERVAL_MIN * 60)
        if is_trading_hours():
            run_scan()
        else:
            print(f'[SCAN] {datetime.now(ET_TZ).strftime("%H:%M ET")} '
                  f'— outside trading hours, sleeping {SCAN_INTERVAL_MIN} min')


if __name__ == '__main__':
    main()
