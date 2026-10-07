"""Basket Tide — the bear-market switch for the overnight books (Clockwork + Night Owl). Built Oct 6 2026.

RULE. ON when the WILD basket of our universe closed ABOVE its 200-session average at the last completed close;
OFF otherwise. While OFF, Clockwork and Night Owl take no new entries. Exits are never touched — a position bought
before the switch still sells at the next open as usual.

WHY (docs/RESEARCH_REGISTRY.md §M4, §M10). Long-only overnight books in volatile names lose most of their history in
small-cap bear markets (Nov 2021 → mid 2023). Replayed 2018 → Oct 2026: fleet max drawdown −83% → −23%, Sharpe
1.87 → 2.06, for ~5% of total return; on ~1,800 unselected stocks both overnight books together: maxDD −36% → −22%,
Sharpe 1.39 → 1.54. 150-250 sessions all work (a plateau), 100 is worse. It costs money in V-shaped recoveries
(2020: −$14.9k on the $30k fleet) and saves it in grinding bears (2022: +$19.5k). Off 19% of sessions since 2015
(all of 2022; short stretches in late 2018, Mar-Apr 2020, Mar-Apr 2025).

DEFINITION (identical to research_portfolio_lab.py; tested for parity in factory/tests/test_basket_tide.py):
  eligible  previous close ≥ $5 and previous 20-day median dollar volume ≥ $20M
  WILD      the eligible names in the top third by trailing 60-day volatility (measured through the previous day)
  basket    equal-weight close-to-close return of the WILD names, compounded into an index
  ON        index > its 200-session simple average, at the last completed close (never today's close)
Data: factory/cache/night_owl/daily.parquet (yfinance official prints; refreshed 17:30 by night_owl_prep and at Night
Owl's 09:35 scoring). FAILS OPEN — if the cache is missing or more than 3 sessions stale the books trade as before and
the reason is logged — the same choice as the futures Daily Tide: a data outage must never be mistaken for a bear.

CLI: venv/bin/python -m factory.live.basket_tide            today's status
     venv/bin/python -m factory.live.basket_tide --history  every OFF stretch since 2015
"""
from __future__ import annotations
import datetime as dt
import json
import os
import sys

import pandas as pd

ROOT = '/Users/sushil/trading'
DAILY = os.path.join(ROOT, 'factory', 'cache', 'night_owl', 'daily.parquet')
STATE = os.path.join(ROOT, 'factory', 'cache', 'basket_tide.json')
MA_SESSIONS = 200
MAX_STALE_SESSIONS = 3
MIN_PRICE, MIN_DV = 5.0, 20e6


def series(daily: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per completed session: basket index, its 200-session average, and whether the basket was above it."""
    d = pd.read_parquet(DAILY) if daily is None else daily
    d = d[(d['open'] > 0) & (d['close'] > 0)]
    c = d.pivot(index='date', columns='symbol', values='close').sort_index()
    v = d.pivot(index='date', columns='symbol', values='volume').reindex(c.index)
    pc = c.shift(1)
    cc = c / pc - 1
    vol60 = cc.rolling(60, min_periods=40).std().shift(1)
    dv20 = (c * v).rolling(20, min_periods=10).median().shift(1)
    elig = (pc >= MIN_PRICE) & (dv20 >= MIN_DV) & cc.notna()
    wild = elig & (vol60.where(elig).rank(axis=1, pct=True) >= 2 / 3)
    ret = cc.where(wild).mean(axis=1).fillna(0)
    idx = (1 + ret).cumprod()
    ma = idx.rolling(MA_SESSIONS, min_periods=MA_SESSIONS).mean()
    out = pd.DataFrame({'basket_ret': ret, 'index': idx, 'ma': ma, 'n_wild': wild.sum(axis=1)})
    out['above'] = (idx > ma).where(ma.notna())
    return out


def _prev_sessions(today: dt.date, n: int) -> list[dt.date]:
    out, d = [], today
    while len(out) < n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def compute(today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    if not os.path.exists(DAILY):
        return {'date': today.isoformat(), 'on': True, 'stale': True, 'reason': 'FAIL-OPEN: no daily cache'}
    try:
        s = series()
    except Exception as e:                                         # never block trading on a computation error
        return {'date': today.isoformat(), 'on': True, 'stale': True, 'reason': f'FAIL-OPEN: {type(e).__name__}: {e}'}
    s = s[s.index < pd.Timestamp(today)].dropna(subset=['above'])  # completed sessions only — never today's close
    if s.empty:
        return {'date': today.isoformat(), 'on': True, 'stale': True, 'reason': 'FAIL-OPEN: no 200-session history'}
    last = s.index[-1].date()
    oldest_ok = _prev_sessions(today, MAX_STALE_SESSIONS)[-1]
    r = s.iloc[-1]
    ratio = float(r['index'] / r['ma'] - 1)
    if last < oldest_ok:
        return {'date': today.isoformat(), 'on': True, 'stale': True, 'asof': last.isoformat(), 'ratio': ratio,
                'reason': f'FAIL-OPEN: basket data last closes {last}, more than {MAX_STALE_SESSIONS} sessions old'}
    on = bool(r['above'])
    # how far the basket must fall from its last close to cross the average (the average itself drifts slowly)
    return {'date': today.isoformat(), 'on': on, 'stale': False, 'asof': last.isoformat(), 'ratio': ratio,
            'n_wild': int(r['n_wild']), 'fall_to_off': (1 - 1 / (1 + ratio)) if on else None,
            'reason': (f'WILD basket {ratio:+.1%} vs its {MA_SESSIONS}-session average at the {last} close'
                       + ('' if on else ' — overnight books stand aside'))}


def status(today: dt.date | None = None) -> dict:
    """Today's verdict, computed once a day (or again if the daily cache was refreshed since) and kept in STATE."""
    today = today or dt.date.today()
    try:
        st = json.load(open(STATE))
        fresh = (st.get('date') == today.isoformat() and os.path.exists(DAILY)
                 and os.path.getmtime(DAILY) <= st.get('computed_ts', 0))
        if fresh:
            return st
    except Exception:
        st = {}
    new = compute(today)
    new['computed_ts'] = dt.datetime.now().timestamp()
    if 'announced_on' in st:
        new['announced_on'] = st['announced_on']      # survives the day roll, so a flip is detectable
    try:
        tmp = f'{STATE}.{os.getpid()}.tmp'   # per-process: two books can write at once
        json.dump(new, open(tmp, 'w'), indent=1); os.replace(tmp, STATE)
    except Exception:
        pass
    return new


def send_telegram(msg: str):
    """Equity bot, best-effort — a Telegram outage must never block an entry decision."""
    try:
        import requests
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, '.env'))
        token, chat = os.getenv('TELEGRAM_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
        if token and chat:
            requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                          json={'chat_id': chat, 'text': msg}, timeout=10)
    except Exception:
        pass


def announce(log=print, send=None) -> None:
    """One Telegram when the switch FLIPS (and once when it first goes live) — never a daily repeat.
    A bear stretch can last a year (2022: 259 sessions); a message every day would be noise."""
    st = status()
    if st.get('stale') or st.get('on') == st.get('announced_on'):
        return
    first = 'announced_on' not in st
    verdict = 'ON — Clockwork and Night Owl take new overnight entries' if st['on'] else \
              'OFF — Clockwork and Night Owl take NO new overnight entries'
    msg = (f"🌊 Basket Tide {'is live: ' if first else 'turned '}{verdict}.\n{st.get('reason', '')}\n"
           f"Exits are never affected. Rule: the WILD basket vs its {MA_SESSIONS}-session average at the last close.")
    (send or send_telegram)(msg)
    log(f'Basket Tide announced: {verdict}')
    st['announced_on'] = st['on']
    try:
        tmp = f'{STATE}.{os.getpid()}.tmp'   # per-process: two books can write at once
        json.dump(st, open(tmp, 'w'), indent=1); os.replace(tmp, STATE)
    except Exception:
        pass


def _cli():
    if '--history' in sys.argv:
        s = series().dropna(subset=['above'])
        off = s['above'] == False                                   # noqa: E712
        runs = off[off].groupby((off != off.shift()).cumsum()[off])
        for _, g in runs:
            if len(g) >= 5:
                print(f'OFF {g.index[0].date()} → {g.index[-1].date()} ({len(g)} sessions)')
        print(f'off {off.mean():.0%} of {len(s)} sessions since {s.index[0].date()}')
    st = status()
    print(json.dumps({k: v for k, v in st.items() if k != 'computed_ts'}, indent=1))


if __name__ == '__main__':
    _cli()
