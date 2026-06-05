"""
futures/macro_calendar.py — Economic calendar for MNQ futures.

Tracks high-impact macro events that move NASDAQ futures ±200pts in minutes.
Used by Cylinder 5 to:
  1. Block new entries 30 min before a release (pre-event blackout)
  2. Classify post-release sentiment (RISK_ON / RISK_OFF / NEUTRAL)
  3. Set daily directional bias for futures_trader.py

Events covered (all times Eastern):
  NFP   — Non-Farm Payrolls: 1st Friday of month, 8:30am ET
  CPI   — Consumer Price Index: 2nd-3rd week, Wed/Thu, 8:30am ET
  FOMC  — Fed rate decision: 8 meetings/year, 2:00pm ET
  GDP   — Gross Domestic Product: quarterly, 8:30am ET
  PPI   — Producer Price Index: monthly, 8:30am ET

Usage:
    from futures.macro_calendar import is_release_day, is_blackout_window, get_release_info

    # In futures_trader.py before each entry:
    if is_blackout_window(datetime.now(ET)):
        return  # skip entry

    # Daily bias (post-release):
    from futures.macro_calendar import get_daily_macro_bias
    bias = get_daily_macro_bias('2026-06-06')  # → 'LONG' | 'SHORT' | 'NEUTRAL' | 'SKIP'
"""

from datetime import datetime, time, date, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')

# ── 2026 High-Impact Calendar ─────────────────────────────────────────────────
# Format: 'YYYY-MM-DD': {'type': str, 'time_et': time, 'note': str}
# time_et = scheduled release time ET (entries scheduled 30 min before are blocked)

MACRO_EVENTS_2026: dict[str, dict] = {
    # NFP (Non-Farm Payrolls) — first Friday of each month
    '2026-01-09': {'type': 'NFP',  'time_et': time(8, 30), 'note': 'Dec 2025 jobs'},
    '2026-02-06': {'type': 'NFP',  'time_et': time(8, 30), 'note': 'Jan 2026 jobs'},
    '2026-03-06': {'type': 'NFP',  'time_et': time(8, 30), 'note': 'Feb 2026 jobs'},
    '2026-04-03': {'type': 'NFP',  'time_et': time(8, 30), 'note': 'Mar 2026 jobs'},
    '2026-05-01': {'type': 'NFP',  'time_et': time(8, 30), 'note': 'Apr 2026 jobs'},
    '2026-06-05': {'type': 'NFP',  'time_et': time(8, 30), 'note': 'May 2026 jobs'},
    '2026-07-10': {'type': 'NFP',  'time_et': time(8, 30), 'note': 'Jun 2026 jobs'},
    '2026-08-07': {'type': 'NFP',  'time_et': time(8, 30), 'note': 'Jul 2026 jobs'},
    '2026-09-04': {'type': 'NFP',  'time_et': time(8, 30), 'note': 'Aug 2026 jobs'},
    '2026-10-02': {'type': 'NFP',  'time_et': time(8, 30), 'note': 'Sep 2026 jobs'},
    '2026-11-06': {'type': 'NFP',  'time_et': time(8, 30), 'note': 'Oct 2026 jobs'},
    '2026-12-04': {'type': 'NFP',  'time_et': time(8, 30), 'note': 'Nov 2026 jobs'},

    # CPI (Consumer Price Index) — monthly, Wed/Thu
    '2026-01-14': {'type': 'CPI',  'time_et': time(8, 30), 'note': 'Dec 2025 CPI'},
    '2026-02-11': {'type': 'CPI',  'time_et': time(8, 30), 'note': 'Jan 2026 CPI'},
    '2026-03-11': {'type': 'CPI',  'time_et': time(8, 30), 'note': 'Feb 2026 CPI'},
    '2026-04-10': {'type': 'CPI',  'time_et': time(8, 30), 'note': 'Mar 2026 CPI'},
    '2026-05-13': {'type': 'CPI',  'time_et': time(8, 30), 'note': 'Apr 2026 CPI'},
    '2026-06-10': {'type': 'CPI',  'time_et': time(8, 30), 'note': 'May 2026 CPI'},
    '2026-07-15': {'type': 'CPI',  'time_et': time(8, 30), 'note': 'Jun 2026 CPI'},
    '2026-08-12': {'type': 'CPI',  'time_et': time(8, 30), 'note': 'Jul 2026 CPI'},
    '2026-09-09': {'type': 'CPI',  'time_et': time(8, 30), 'note': 'Aug 2026 CPI'},
    '2026-10-14': {'type': 'CPI',  'time_et': time(8, 30), 'note': 'Sep 2026 CPI'},
    '2026-11-12': {'type': 'CPI',  'time_et': time(8, 30), 'note': 'Oct 2026 CPI'},
    '2026-12-09': {'type': 'CPI',  'time_et': time(8, 30), 'note': 'Nov 2026 CPI'},

    # FOMC (Fed rate decisions) — 8 meetings in 2026
    '2026-01-29': {'type': 'FOMC', 'time_et': time(14,  0), 'note': 'Jan FOMC'},
    '2026-03-19': {'type': 'FOMC', 'time_et': time(14,  0), 'note': 'Mar FOMC'},
    '2026-05-07': {'type': 'FOMC', 'time_et': time(14,  0), 'note': 'May FOMC'},
    '2026-06-18': {'type': 'FOMC', 'time_et': time(14,  0), 'note': 'Jun FOMC'},
    '2026-07-30': {'type': 'FOMC', 'time_et': time(14,  0), 'note': 'Jul FOMC'},
    '2026-09-17': {'type': 'FOMC', 'time_et': time(14,  0), 'note': 'Sep FOMC'},
    '2026-11-05': {'type': 'FOMC', 'time_et': time(14,  0), 'note': 'Nov FOMC'},
    '2026-12-17': {'type': 'FOMC', 'time_et': time(14,  0), 'note': 'Dec FOMC'},

    # GDP (quarterly — advance estimate)
    # Note: 2026-01-29 and 2026-07-30 also have FOMC — FOMC takes priority (already in dict above).
    # GDP on same-day as FOMC is captured by the FOMC entry; FOMC is higher impact.
    # '2026-01-29' FOMC overrides GDP (FOMC entry kept above, GDP dropped to avoid key collision).
    # '2026-07-30' FOMC overrides GDP (same reason).
    '2026-04-30': {'type': 'GDP',  'time_et': time(8, 30), 'note': 'Q1 2026 GDP advance'},
    '2026-10-29': {'type': 'GDP',  'time_et': time(8, 30), 'note': 'Q3 2026 GDP advance'},

    # PPI (Producer Price Index)
    '2026-01-15': {'type': 'PPI',  'time_et': time(8, 30), 'note': 'Dec 2025 PPI'},
    '2026-02-12': {'type': 'PPI',  'time_et': time(8, 30), 'note': 'Jan 2026 PPI'},
    '2026-03-12': {'type': 'PPI',  'time_et': time(8, 30), 'note': 'Feb 2026 PPI'},
    '2026-04-11': {'type': 'PPI',  'time_et': time(8, 30), 'note': 'Mar 2026 PPI'},
    '2026-05-14': {'type': 'PPI',  'time_et': time(8, 30), 'note': 'Apr 2026 PPI'},
    '2026-06-11': {'type': 'PPI',  'time_et': time(8, 30), 'note': 'May 2026 PPI'},
}

# How long before release to stop new entries
BLACKOUT_MINUTES_BEFORE = 30

# How long after release to wait before resuming entries
# (market needs time to absorb the shock)
RESUME_MINUTES_AFTER = 30


def _first_friday(year: int, month: int) -> date:
    """Return the first Friday of a given month."""
    d = date(year, month, 1)
    # weekday(): Monday=0, Friday=4
    offset = (4 - d.weekday()) % 7
    return d + timedelta(days=offset)


def _build_nfp_dates(start_year: int = 2021, end_year: int = 2027) -> dict[str, dict]:
    """
    Generate NFP release dates (first Friday of each month) for a range of years.
    NFP is the most consistently-scheduled high-impact event.
    """
    events = {}
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            nfp_date = _first_friday(year, month)
            if nfp_date.year == year:  # guard month overflow
                d_str = nfp_date.isoformat()
                events[d_str] = {
                    'type': 'NFP',
                    'time_et': time(8, 30),
                    'note': f'NFP {nfp_date.strftime("%b %Y")} (auto-detected)',
                }
    return events


# 2021–2025 only: 2026 is fully covered by MACRO_EVENTS_2026 (exact dates).
# Including 2026 in _build_nfp_dates causes false positives for holiday-shifted months
# (e.g. Jan 2 = New Year's week, Jul 3 = Independence Day observed — both wrong).
_NFP_ALL_YEARS = _build_nfp_dates(2021, 2025)
# Hardcoded events override auto-detected (more accurate dates/notes)
MACRO_EVENTS_ALL: dict[str, dict] = {**_NFP_ALL_YEARS, **MACRO_EVENTS_2026}


def get_release_info(trade_date: str | date) -> dict | None:
    """
    Return event info for a given trading date (YYYY-MM-DD), or None.
    Checks hardcoded 2026 calendar first, then programmatic NFP detection.
    Priority: FOMC > NFP > CPI > GDP > PPI
    """
    if isinstance(trade_date, date):
        trade_date = trade_date.isoformat()
    # Hardcoded 2026 events (exact dates, includes CPI/FOMC/GDP/PPI)
    event = MACRO_EVENTS_2026.get(trade_date)
    if event:
        return event
    # Programmatic NFP detection (all years, first Friday of month)
    return _NFP_ALL_YEARS.get(trade_date)


def is_release_day(trade_date: str | date) -> bool:
    """True if today has a high-impact release."""
    return get_release_info(trade_date) is not None


def is_blackout_window(now_et: datetime) -> tuple[bool, str]:
    """
    Returns (is_blocked: bool, reason: str).
    Blocked if within BLACKOUT_MINUTES_BEFORE of a release
    OR within RESUME_MINUTES_AFTER of a release.

    Pass a timezone-aware datetime in ET.
    Usage in futures_trader.py:
        blocked, reason = is_blackout_window(datetime.now(ET))
        if blocked:
            return None, reason
    """
    d_str = now_et.date().isoformat()
    event = get_release_info(d_str)   # uses full fallback (hardcoded + auto-NFP)
    if not event:
        return False, ''

    release_dt = datetime.combine(now_et.date(), event['time_et'], tzinfo=ET)
    blackout_start = release_dt - timedelta(minutes=BLACKOUT_MINUTES_BEFORE)
    resume_time    = release_dt + timedelta(minutes=RESUME_MINUTES_AFTER)

    if blackout_start <= now_et < resume_time:
        event_type = event['type']
        if now_et < release_dt:
            mins_left = int((release_dt - now_et).seconds / 60)
            return True, f'{event_type} in {mins_left}min — blackout until {release_dt.strftime("%H:%M")} ET'
        else:
            mins_since = int((now_et - release_dt).seconds / 60)
            return True, f'{event_type} released {mins_since}min ago — waiting for dust to settle'

    return False, ''


def get_event_type(trade_date: str | date) -> str | None:
    """Return event type string for date, or None."""
    info = get_release_info(trade_date)
    return info['type'] if info else None


def get_release_time_et(trade_date: str | date) -> time | None:
    """Return scheduled release time ET for date, or None."""
    info = get_release_info(trade_date)
    return info['time_et'] if info else None


def days_until_next_release(from_date: date | None = None) -> tuple[int, str, str]:
    """
    Returns (days_away, date_str, event_type) for the next scheduled release.
    Useful for futures_trader.py startup log.
    """
    today = from_date or date.today()
    for d_str in sorted(MACRO_EVENTS_2026.keys()):
        event_date = date.fromisoformat(d_str)
        if event_date >= today:
            return (event_date - today).days, d_str, MACRO_EVENTS_2026[d_str]['type']
    return 999, '', ''


# ── Release-day WR analysis (backtest support) ────────────────────────────────

def is_high_impact(trade_date: str | date) -> bool:
    """True for NFP, CPI, FOMC (the three highest-impact events for NASDAQ)."""
    info = get_release_info(trade_date)
    return info is not None and info['type'] in ('NFP', 'CPI', 'FOMC')


def classify_date(trade_date: str | date) -> str:
    """
    Classify a trading date for backtest analysis:
    'HIGH_IMPACT' — NFP/CPI/FOMC
    'MEDIUM_IMPACT' — GDP/PPI
    'NORMAL' — no scheduled release
    """
    info = get_release_info(trade_date)
    if not info:
        return 'NORMAL'
    if info['type'] in ('NFP', 'CPI', 'FOMC'):
        return 'HIGH_IMPACT'
    return 'MEDIUM_IMPACT'


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    today = date.today()
    print(f'\n=== Macro Calendar — {today} ===')

    # Today
    info = get_release_info(today)
    if info:
        blocked, reason = is_blackout_window(datetime.now(ET))
        status = f'  ⚠️  ACTIVE: {info["type"]} at {info["time_et"].strftime("%H:%M")} ET — {info["note"]}'
        if blocked:
            status += f'\n  🚫 BLACKOUT NOW: {reason}'
        print(status)
    else:
        print('  ✅ No high-impact release today')

    # Next release
    days, d_str, evt = days_until_next_release(today)
    if d_str:
        print(f'\n  Next: {evt} on {d_str} ({days} days away)')

    # This week
    print('\n  This week:')
    for i in range(7):
        d = today + timedelta(days=i)
        info = get_release_info(d)
        if info:
            print(f'    {d}  {info["type"]:<6}  {info["time_et"].strftime("%H:%M")} ET  {info["note"]}')

    print()
