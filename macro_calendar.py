"""Macro calendar — one source of truth for scheduled US economic events (Oct 6 2026).

Replaces the hand-typed lists in dashboard/app.py (MACRO_EVENTS) and market_context.py (MACRO_DATES), which had
4 future dates and the WRONG next FOMC decision (Nov 4 instead of Oct 28 2026).

Sources
  NASDAQ  api.nasdaq.com/api/calendar/economicevents — every US release with time (ET), actual / consensus / previous.
          ⚠️ The feed is shifted by one day: asking for day D returns day D−1's events (verified against the
          2022-06-10 CPI, the 2026-10-02 payrolls and the 2026-10-28 FOMC decision). fetch_day() corrects it.
  FED     federalreserve.gov FOMC schedule — the authoritative rate-decision dates (two-day meetings end on the
          decision day; cross-month meetings are labelled "Jan/Feb", "Apr/May", "Oct/Nov").
  BLS (CPI, payrolls, PPI) blocks automated requests (403), so those come from NASDAQ.

Storage: trades.db table macro_calendar (event_date, time_et, event, category, importance, actual, consensus,
previous, source, fetched_at). Only US events. importance: HIGH (moves the whole market), MEDIUM, LOW.

Why it matters (registry §M7): scheduled-release nights are the BEST nights for the overnight books — in 2021-26
21% of nights carried 78% of the universe's overnight return; the night before an FOMC decision replicated out of
sample (2018-20). The calendar exists so no book ever skips — or is surprised by — one of these nights.

CLI (venv/bin/python macro_calendar.py ...):
  --update                 refresh the last 7 days (actuals) + the next 60 days + the Fed schedule   (daily job)
  --backfill START END     history, throttled, resumable (skips days already fetched)
  --upcoming [N]           print HIGH/MEDIUM events in the next N days (default 14)
  --night YYYY-MM-DD       what an overnight hold from that session's close to the next open is exposed to
"""
from __future__ import annotations
import datetime as dt
import html
import os
import re
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, 'trades.db')
H = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) '
                   'Version/17.5 Safari/605.1.15',
     'Accept': 'application/json, text/plain, */*', 'Origin': 'https://www.nasdaq.com', 'Referer': 'https://www.nasdaq.com/'}
THROTTLE_S = 0.4

# (pattern, category, importance) — first match wins; matched against the event name as Nasdaq prints it
RULES = [
    (r'^(Fed Interest Rate Decision|FOMC Rate Decision|Fed Funds Rate|FOMC Statement|FOMC Economic Projections|FOMC Press Conference)', 'FED', 'HIGH'),
    (r'^(Core )?CPI$', 'INFLATION', 'HIGH'),
    (r'^Nonfarm Payrolls$|^Unemployment Rate$', 'JOBS', 'HIGH'),
    (r'^(Core )?PCE Price Index', 'INFLATION', 'HIGH'),
    (r'^(Core )?PPI$', 'INFLATION', 'HIGH'),
    (r'^(Core )?Retail Sales', 'CONSUMER', 'HIGH'),
    (r'^GDP( \(QoQ\))?$|^GDP Price Index', 'GROWTH', 'HIGH'),
    (r'^Fed Chair Powell Speaks|^Fed Chair .* Speaks|^FOMC Meeting Minutes', 'FED', 'MEDIUM'),
    (r'^JOLTs Job Openings|^ADP Nonfarm Employment|^Average Hourly Earnings|^Initial Jobless Claims$', 'JOBS', 'MEDIUM'),
    (r'^ISM (Manufacturing|Non-Manufacturing|Services) PMI$', 'ACTIVITY', 'MEDIUM'),
    (r'^Michigan Consumer Sentiment$|^CB Consumer Confidence$|^Michigan .*Inflation Expectations', 'CONSUMER', 'MEDIUM'),
    (r'^Core Durable Goods Orders$|^Durable Goods Orders$', 'ACTIVITY', 'MEDIUM'),
    (r'^Treasury Refunding|^10-Year Note Auction$|^30-Year Bond Auction$', 'RATES', 'MEDIUM'),
]
MONTHS = {m: i for i, m in enumerate(['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
                                      'September', 'October', 'November', 'December'], 1)}
ABBR = {m[:3]: i for m, i in MONTHS.items()}


def _conn():
    c = sqlite3.connect(DB, timeout=30)
    c.execute("""CREATE TABLE IF NOT EXISTS macro_calendar (
        event_date TEXT NOT NULL, time_et TEXT NOT NULL, event TEXT NOT NULL, category TEXT, importance TEXT,
        actual TEXT, consensus TEXT, previous TEXT, source TEXT, fetched_at TEXT,
        PRIMARY KEY (event_date, time_et, event, source))""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_macro_cal_imp ON macro_calendar(importance, event_date)")
    c.execute("""CREATE TABLE IF NOT EXISTS macro_calendar_fetch (event_date TEXT PRIMARY KEY, n_rows INTEGER,
        fetched_at TEXT)""")
    return c


def classify(name: str):
    for pat, cat, imp in RULES:
        if re.search(pat, name, re.I):
            return cat, imp
    return 'OTHER', 'LOW'


def fetch_day(day: dt.date) -> list[dict] | None:
    """US events released ON `day` (the feed is one day behind, so ask for day+1). None on a fetch failure."""
    import requests
    q = (day + dt.timedelta(days=1)).isoformat()
    try:
        r = requests.get(f'https://api.nasdaq.com/api/calendar/economicevents?date={q}', headers=H, timeout=30)
        if r.status_code != 200:
            return None
        rows = (r.json().get('data') or {}).get('rows') or []
    except Exception:
        return None
    out, seen = [], set()
    for x in rows:
        if x.get('country') != 'United States':
            continue
        name = html.unescape(x.get('eventName') or '').strip()
        t = (x.get('gmt') or '').strip()
        t = t if re.match(r'^\d{2}:\d{2}$', t) else 'TBD'
        key = (t, name)
        if not name or key in seen:              # m/m and y/y versions share a name — keep the first
            continue
        seen.add(key)
        cat, imp = classify(name)
        clean = lambda s: html.unescape(s or '').replace('\xa0', ' ').strip()
        out.append({'event_date': day.isoformat(), 'time_et': t, 'event': name, 'category': cat, 'importance': imp,
                    'actual': clean(x.get('actual')), 'consensus': clean(x.get('consensus')),
                    'previous': clean(x.get('previous')), 'source': 'NASDAQ'})
    return out


def fed_schedule() -> list[dict]:
    """FOMC rate-decision dates from the Fed's calendar page (current and future years)."""
    import requests
    try:
        t = requests.get('https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm', headers={'User-Agent': H['User-Agent']},
                         timeout=30).text
    except Exception:
        return []
    out = []
    for m in re.finditer(r'(\d{4}) FOMC Meetings(.*?)(?=\d{4} FOMC Meetings|$)', t, re.S):
        yr, body = int(m.group(1)), m.group(2)
        for mm in re.finditer(r'fomc-meeting__month[^>]*>\s*<strong>([A-Za-z]+)(?:/([A-Za-z]+))?</strong>.*?'
                              r'fomc-meeting__date[^>]*>\s*([\d\-]+)(\*?)([^<]*)', body, re.S):
            if 'notation' in mm.group(5).lower():          # written "notation votes" are not rate decisions
                continue
            mon = (mm.group(2) or mm.group(1))
            mi = MONTHS.get(mon) or ABBR.get(mon[:3])
            if not mi:
                continue
            day = int(mm.group(3).split('-')[-1])
            try:
                d = dt.date(yr, mi, day)
            except ValueError:
                continue
            out.append({'event_date': d.isoformat(), 'time_et': '14:00',
                        'event': 'FOMC Rate Decision' + (' + projections' if mm.group(4) else ''),
                        'category': 'FED', 'importance': 'HIGH', 'actual': '', 'consensus': '', 'previous': '',
                        'source': 'FED'})
    return out


def nfp_rule_dates(start: dt.date, months: int = 6) -> list[dt.date]:
    """Employment Situation (payrolls) — BLS releases it on the third Friday after the end of the reference week (the
    Sun-Sat week containing the 12th). Matches 2026-10-02; gives 2026-11-06. Holidays/shutdowns can move it, so these
    rows are marked source='RULE' and are replaced as soon as the feed lists the real date."""
    out, y, m = [], start.year, start.month
    for _ in range(months):
        twelfth = dt.date(y, m, 12)
        sat = twelfth + dt.timedelta(days=(5 - twelfth.weekday()) % 7)        # Saturday ending the reference week
        fri = sat + dt.timedelta(days=6)                                       # first Friday after it
        rel = fri + dt.timedelta(days=14)                                      # the third Friday after it
        if (rel.month, rel.day) in {(1, 1), (7, 3), (7, 4), (12, 25)}:         # federal holiday → BLS moves it a week
            rel += dt.timedelta(days=7)
        out.append(rel)
        m = m + 1 if m < 12 else 1; y = y + (m == 1)
    return [d for d in out if d >= start]


def _store(c, rows):
    now = dt.datetime.now().isoformat(timespec='seconds')
    c.executemany("""INSERT OR REPLACE INTO macro_calendar(event_date,time_et,event,category,importance,actual,consensus,
                     previous,source,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  [(r['event_date'], r['time_et'], r['event'], r['category'], r['importance'], r['actual'],
                    r['consensus'], r['previous'], r['source'], now) for r in rows])


def _weekdays(a: dt.date, b: dt.date):
    d = a
    while d <= b:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


def update(back=7, ahead=60, log=print):
    c = _conn(); today = dt.date.today(); n = fails = 0
    for d in _weekdays(today - dt.timedelta(days=back), today + dt.timedelta(days=ahead)):
        rows = fetch_day(d)
        if rows is None:
            fails += 1
            if fails >= 5:
                log('stopping: 5 failed requests in a row'); break
            continue
        fails = 0
        c.execute("DELETE FROM macro_calendar WHERE event_date=? AND source='NASDAQ'", (d.isoformat(),))
        _store(c, rows); n += len(rows)              # committed below, before the next network call
        c.execute("INSERT OR REPLACE INTO macro_calendar_fetch VALUES(?,?,?)", (d.isoformat(), len(rows), dt.datetime.now().isoformat(timespec='seconds')))
        c.commit(); time.sleep(THROTTLE_S)
    fed = fed_schedule()
    if fed:
        c.execute("DELETE FROM macro_calendar WHERE source='FED'"); _store(c, fed); c.commit()
    # expected payrolls beyond the feed's ~3-week horizon (replaced once the feed has the real date)
    c.execute("DELETE FROM macro_calendar WHERE source='RULE'")
    feed_nfp = [dt.date.fromisoformat(r[0]) for r in c.execute(
        "SELECT event_date FROM macro_calendar WHERE source='NASDAQ' AND event='Nonfarm Payrolls'")]
    rule = [d for d in nfp_rule_dates(today, 7) if not any(abs((d - f).days) <= 10 for f in feed_nfp)]
    _store(c, [{'event_date': d.isoformat(), 'time_et': '08:30', 'event': 'Nonfarm Payrolls (expected)', 'category': 'JOBS',
                'importance': 'HIGH', 'actual': '', 'consensus': '', 'previous': '', 'source': 'RULE'} for d in rule])
    c.commit()
    check_fomc(c, log)
    log(f'macro calendar updated: {n} US events ({today - dt.timedelta(days=back)} → {today + dt.timedelta(days=ahead)}), '
        f'{len(fed)} FOMC dates from the Fed')
    c.close()


def check_fomc(c, log=print):
    """Every Fed-schedule decision in the Nasdaq window should appear in the Nasdaq feed too — report mismatches."""
    fed = {r[0] for r in c.execute("SELECT event_date FROM macro_calendar WHERE source='FED'")}
    nas = {r[0] for r in c.execute("SELECT DISTINCT event_date FROM macro_calendar WHERE source='NASDAQ' "
                                   "AND (event LIKE 'Fed Interest Rate Decision%' OR event LIKE 'FOMC Rate Decision%')")}
    have = {r[0] for r in c.execute("SELECT event_date FROM macro_calendar_fetch")}
    miss = sorted(d for d in fed if d in have and d not in nas)
    extra = sorted(d for d in nas if d not in fed and d >= min(fed, default='9999'))
    if miss or extra:
        log(f'⚠️ FOMC cross-check: Fed dates absent from Nasdaq {miss} · Nasdaq dates absent from the Fed schedule {extra}')


def backfill(start: str, end: str, log=print, workers=4):
    """History, resumable. Fetches run in a small thread pool (each request takes seconds); every result is written
    by THIS thread in its own short transaction — trades.db is the live database, so the write lock is never held
    across a network call and never by more than one writer here."""
    import concurrent.futures as cf
    c = _conn()
    done = {r[0] for r in c.execute("SELECT event_date FROM macro_calendar_fetch")}
    todo = [d for d in _weekdays(dt.date.fromisoformat(start), dt.date.fromisoformat(end)) if d.isoformat() not in done]
    log(f'backfill: {len(todo)} weekdays to fetch with {workers} workers'); t0 = time.time(); fails = 0

    def job(d):
        time.sleep(THROTTLE_S)
        return d, fetch_day(d)
    with cf.ThreadPoolExecutor(workers) as ex:
        for i, (d, rows) in enumerate(ex.map(job, todo)):
            if rows is None:
                fails += 1
                if fails >= 8:
                    log(f'stopping at {d}: repeated failures (rate limit?) — rerun later, it resumes'); break
                continue
            fails = 0
            _store(c, rows)
            c.execute("INSERT OR REPLACE INTO macro_calendar_fetch VALUES(?,?,?)", (d.isoformat(), len(rows), dt.datetime.now().isoformat(timespec='seconds')))
            c.commit()
            if i % 50 == 0:
                log(f'  {i}/{len(todo)} {d} {time.time() - t0:.0f}s')
    c.close()


# ─────────────────────────── read API (dashboard, Field Report, engines) ───────────────────────────
def events(start: str, end: str, min_importance='MEDIUM'):
    """Events with event_date in [start, end], at or above min_importance; FED source wins for FOMC days."""
    order = {'HIGH': 2, 'MEDIUM': 1, 'LOW': 0}
    try:
        c = sqlite3.connect(DB, timeout=10)
        rows = c.execute("""SELECT event_date, time_et, event, category, importance, actual, consensus, previous, source
                            FROM macro_calendar WHERE event_date BETWEEN ? AND ? ORDER BY event_date, time_et""",
                         (start, end)).fetchall()
        c.close()
    except Exception:
        return []
    keys = ['event_date', 'time_et', 'event', 'category', 'importance', 'actual', 'consensus', 'previous', 'source']
    out = [dict(zip(keys, r)) for r in rows if order.get(r[4], 0) >= order[min_importance]]
    fed_days = {e['event_date'] for e in out if e['source'] == 'FED'}
    return [e for e in out if not (e['source'] == 'NASDAQ' and e['category'] == 'FED' and e['event'].startswith('Fed Interest')
                                   and e['event_date'] in fed_days)]


def upcoming(days=14, min_importance='MEDIUM'):
    t = dt.date.today()
    return events(t.isoformat(), (t + dt.timedelta(days=days)).isoformat(), min_importance)


def night_exposure(session: str, next_session: str | None = None):
    """HIGH events an overnight hold (session close 16:00 → next session open 09:30) is exposed to, plus whether the
    next session is an FOMC decision day (the 'pre-FOMC' night, registry §M7)."""
    d = dt.date.fromisoformat(session)
    n = dt.date.fromisoformat(next_session) if next_session else d + dt.timedelta(days=3 if d.weekday() == 4 else 1)
    ev = events(d.isoformat(), n.isoformat(), 'HIGH')
    inside = [e for e in ev if (e['event_date'] == d.isoformat() and e['time_et'] >= '16:00') or
              (d.isoformat() < e['event_date'] < n.isoformat()) or
              (e['event_date'] == n.isoformat() and e['time_et'] < '09:30')]
    fomc_next = any(e['event_date'] == n.isoformat() and e['category'] == 'FED' for e in ev)
    return {'session': d.isoformat(), 'next_session': n.isoformat(), 'releases_before_open': inside, 'pre_fomc': fomc_next}


def _cli():
    a = sys.argv[1:]
    if not a or a[0] == '--update':
        update()
    elif a[0] == '--backfill':
        backfill(a[1], a[2])
    elif a[0] == '--upcoming':
        for e in upcoming(int(a[1]) if len(a) > 1 else 14):
            print(f"{e['event_date']} {e['time_et']:>5}  {e['importance']:6s} {e['category']:9s} {e['event']}"
                  + (f"  (cons {e['consensus']})" if e['consensus'] else ''))
    elif a[0] == '--night':
        print(night_exposure(a[1]))
    else:
        print(__doc__)


if __name__ == '__main__':
    _cli()
