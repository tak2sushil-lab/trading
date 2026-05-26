"""
test_news_sources.py — Live test of all 5 news sources for one symbol.

Usage:
  venv/bin/python options/test_news_sources.py [SYMBOL]   # default: PLTR

What this does:
  1. Tests each source individually and shows raw results (no LLM, no DB)
  2. Checks IBKR bridge — shows which providers are available on your account
  3. Reports headline counts, ages, and any errors
  4. Tells you which sources are active vs. missing credentials

Run with IBKR Gateway/TWS running for the IBKR news test.
"""

import os
import sys
import time
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

from options.news_engine import (
    fetch_yfinance_news,
    fetch_finnhub_news,
    fetch_ibkr_news,
    fetch_polygon_news,
    fetch_alpha_vantage_news,
    POLYGON_KEY,
    ALPHA_VANTAGE_KEY,
    FINNHUB_KEY,
    BRIDGE_URL,
)

SYMBOL = sys.argv[1].upper() if len(sys.argv) > 1 else 'PLTR'

PASS = "✅"
WARN = "⚠️ "
FAIL = "❌"

def _age(published_at: str) -> str:
    """Human-readable age from ISO timestamp."""
    try:
        ts = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
        ts = ts.replace(tzinfo=None)  # strip tz for simple diff
        diff = datetime.now() - ts
        mins = int(diff.total_seconds() / 60)
        if mins < 60:
            return f"{mins}m ago"
        return f"{mins // 60}h {mins % 60}m ago"
    except Exception:
        return published_at[:16]


def test_source(name: str, items: list[dict], key_needed: bool = False, key_set: bool = True):
    if not key_set:
        print(f"\n  {WARN} {name}: SKIPPED — key not set")
        return 0
    if not items:
        print(f"\n  {FAIL} {name}: returned 0 headlines")
        return 0
    print(f"\n  {PASS} {name}: {len(items)} headline(s)")
    for item in items[:5]:
        age = _age(item.get('published_at', ''))
        title = item['title'][:90]
        print(f"       [{age}] {title}")
    if len(items) > 5:
        print(f"       ... and {len(items) - 5} more")
    return len(items)


print(f"\n{'='*56}")
print(f"  News source test — {SYMBOL}  ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
print(f"{'='*56}")

total = 0

# ── 1. yfinance ───────────────────────────────────────────
print("\n[1/5] yfinance (free, always on)")
items = fetch_yfinance_news(SYMBOL)
total += test_source('yfinance', items)

# ── 2. Finnhub ────────────────────────────────────────────
print("\n[2/5] Finnhub (free API key — finnhub.io)")
items = fetch_finnhub_news(SYMBOL)
total += test_source('Finnhub', items, key_needed=True, key_set=bool(FINNHUB_KEY))
if not FINNHUB_KEY:
    print(f"       → Add FINNHUB_API_KEY=xxx to .env (free at finnhub.io)")

# ── 3. IBKR news feed ─────────────────────────────────────
print("\n[3/5] IBKR news feed (via bridge)")
bridge_up = False
try:
    r = requests.get(f"{BRIDGE_URL}/health", timeout=3)
    bridge_up = r.status_code == 200
except Exception:
    pass

if not bridge_up:
    print(f"  {WARN}  IBKR bridge not running at {BRIDGE_URL}")
    print(f"       → Start: venv/bin/python bridge.py")
else:
    # First show which providers are available
    try:
        pr = requests.get(f"{BRIDGE_URL}/options/news_providers", timeout=8)
        if pr.status_code == 200:
            pdata = pr.json()
            providers = pdata.get('providers', [])
            if providers:
                print(f"  {PASS} Providers on this account ({len(providers)}):")
                for p in providers:
                    print(f"       • {p['code']:8}  {p['name']}")
            else:
                print(f"  {WARN}  No news providers on this account")
                print(f"       → Free: Globe Newswire + PR Newswire available to most accounts")
                print(f"       → Paid: Briefing.com Trader ~$10/mo in IBKR Account Mgmt")
        else:
            print(f"  {WARN}  Could not fetch providers (HTTP {pr.status_code})")
    except Exception as e:
        print(f"  {FAIL} Provider fetch error: {e}")

    items = fetch_ibkr_news(SYMBOL)
    total += test_source('IBKR', items)
    if not items:
        print(f"       → No articles in last 6h, or no subscribed providers")
        print(f"       → Try subscribing to Briefing.com Trader in IBKR Account Management")

# ── 4. Polygon ────────────────────────────────────────────
print("\n[4/5] Polygon.io ($29/mo — optional)")
items = fetch_polygon_news(SYMBOL)
total += test_source('Polygon', items, key_needed=True, key_set=bool(POLYGON_KEY))
if not POLYGON_KEY:
    print(f"       → Add POLYGON_API_KEY=pk_xxx to .env")

# ── 5. Alpha Vantage ──────────────────────────────────────
print("\n[5/5] Alpha Vantage (free 25 calls/day — optional)")
items = fetch_alpha_vantage_news(SYMBOL)
total += test_source('Alpha Vantage', items, key_needed=True, key_set=bool(ALPHA_VANTAGE_KEY))
if not ALPHA_VANTAGE_KEY:
    print(f"       → Add ALPHA_VANTAGE_KEY=xxx to .env (free at alphavantage.co)")

# ── Summary ───────────────────────────────────────────────
print(f"\n{'='*56}")
print(f"  Total raw headlines from all sources: {total}")
active = sum([
    1,                                      # yfinance always on
    1 if FINNHUB_KEY else 0,
    1 if bridge_up else 0,
    1 if POLYGON_KEY else 0,
    1 if ALPHA_VANTAGE_KEY else 0,
])
print(f"  Active sources: {active}/5")
print()
if total > 0:
    print(f"  After dedup + LLM filter, expect ~{total // 3}–{total // 2} unique classified headlines.")
    print(f"  HIGH signals (actionable) typically 0–2 per scan.")
print(f"{'='*56}\n")
