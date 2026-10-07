"""DataBento API keys — which account pays, and the switch to the next one (Oct 6 2026).

Two accounts, both in .env:
  DATABENTO_API_KEY    primary   credits usable through 2026-12-31 (expires Jan 1 2027)
  DATABENTO_API_KEY_2  reserve   $125 added Oct 6 2026, usable through 2027-04-27

RULE: spend the primary first. Move to the reserve when
  (a) the primary is past its date, or
  (b) DataBento refuses the primary for an ACCOUNT reason: 401 (bad key), 402 (payment), or a 403 whose
      message is about credits / balance / payment / billing / funds / expiry.
A refused key is skipped for 24 hours (state in logs/databento_keys.json), then tried again — so a topped-up
account comes back by itself. The first refusal per key per day sends one Telegram.
NOT retried on the other key: a request that is wrong in itself (400/422 bad symbol or date range, or a 403 for a
dataset/date the license does not cover) and DataBento server errors (5xx). Switching accounts cannot fix those, and
retrying would only spend the reserve on the same failure. Every refusal's full message is logged, so the first real
out-of-credit error shows exactly what DataBento sends.

Usage (same API as databento.Historical):
    from databento_keys import historical
    client = historical()
    client.timeseries.get_range(...); client.metadata.get_cost(...); client.symbology.resolve(...)
    client.account        → which key served the last call ('DATABENTO_API_KEY' / 'DATABENTO_API_KEY_2')
CLI:  venv/bin/python databento_keys.py     validate every key with free calls and show which pays next
"""
from __future__ import annotations
import datetime as dt
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, 'logs', 'databento_keys.json')
KEYS = (  # (env name, last usable day, label) — in spending order
    ('DATABENTO_API_KEY', '2026-12-31', 'primary'),
    ('DATABENTO_API_KEY_2', '2027-04-27', 'reserve ($125, added Oct 6 2026)'),
)
REFUSAL_SKIP_HOURS = 24
_ACCOUNT_403 = re.compile(r'credit|balance|payment|billing|fund|expire|insufficient|quota|limit reached', re.I)


class NoUsableKey(RuntimeError):
    pass


def _env() -> dict:
    vals = {}
    try:
        from dotenv import dotenv_values
        vals = dotenv_values(os.path.join(ROOT, '.env'))
    except Exception:
        pass
    return {name: os.getenv(name) or vals.get(name) for name, _, _ in KEYS}


def _load_state() -> dict:
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = f'{STATE}.{os.getpid()}.tmp'   # per-process: two books can write at once
        json.dump(st, open(tmp, 'w'), indent=1)
        os.replace(tmp, STATE)
    except Exception:
        pass


def usable_keys(today: dt.date | None = None) -> list[tuple[str, str, str]]:
    """[(env name, key, label)] in spending order: present, within its date, not refused in the last 24h."""
    today = today or dt.date.today()
    env, st, now = _env(), _load_state(), dt.datetime.now()
    out = []
    for name, last_day, label in KEYS:
        key = env.get(name)
        if not key or today > dt.date.fromisoformat(last_day):
            continue
        refused = st.get(name, {}).get('refused_at')
        if refused and now - dt.datetime.fromisoformat(refused) < dt.timedelta(hours=REFUSAL_SKIP_HOURS):
            continue
        out.append((name, key, label))
    return out


def _is_account_refusal(e: Exception) -> bool:
    status = getattr(e, 'http_status', None)
    if status in (401, 402):
        return True
    return status == 403 and bool(_ACCOUNT_403.search(str(e)))


def _telegram(msg: str) -> None:
    try:
        import requests
        env = {}
        from dotenv import dotenv_values
        env = dotenv_values(os.path.join(ROOT, '.env'))
        token, chat = env.get('TELEGRAM_TOKEN'), env.get('TELEGRAM_CHAT_ID')
        if token and chat:
            requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                          json={'chat_id': chat, 'text': msg}, timeout=10)
    except Exception:
        pass


def _mark_refused(name: str, label: str, e: Exception, nxt: str | None) -> None:
    st = _load_state()
    today = dt.date.today().isoformat()
    first_today = st.get(name, {}).get('alerted_on') != today
    st[name] = {'refused_at': dt.datetime.now().isoformat(timespec='seconds'),
                'status': getattr(e, 'http_status', None), 'message': str(e)[:500],
                'alerted_on': today}
    _save_state(st)
    reason = ' '.join(str(e).split())[:200]          # str(e) already starts with the HTTP status
    line = (f"DataBento refused the {label} key ({name}): {reason} — "
            + (f"switched to {nxt}" if nxt else "NO other key left"))
    print(f'[databento_keys] {line}', file=sys.stderr, flush=True)
    if first_today:
        _telegram(f"📡 {line}. It is skipped for {REFUSAL_SKIP_HOURS}h, then tried again.")


class _Section:
    def __init__(self, owner, name):
        self._owner, self._name = owner, name

    def __getattr__(self, method):
        def call(*a, **k):
            return self._owner._call(self._name, method, a, k)
        return call


class FailoverHistorical:
    """databento.Historical that tries each usable key in order and moves on only for account refusals."""

    def __init__(self):
        self.account = None

    def __getattr__(self, section):          # timeseries / metadata / symbology / batch
        if section.startswith('_'):
            raise AttributeError(section)
        return _Section(self, section)

    def _call(self, section, method, a, k):
        import databento as db
        keys = usable_keys()
        if not keys:
            raise NoUsableKey('no DataBento key is usable (missing, past its date, or refused in the last '
                              f'{REFUSAL_SKIP_HOURS}h) — see {STATE} and databento_keys.KEYS')
        for i, (name, key, label) in enumerate(keys):
            try:
                out = getattr(getattr(db.Historical(key), section), method)(*a, **k)
                self.account = name
                return out
            except Exception as e:
                if not _is_account_refusal(e):
                    raise
                nxt = keys[i + 1][0] if i + 1 < len(keys) else None
                _mark_refused(name, label, e, nxt)
                if nxt is None:
                    raise


def historical() -> FailoverHistorical:
    return FailoverHistorical()


def _cli():
    import databento as db
    env, st = _env(), _load_state()
    order = [n for n, _, _ in usable_keys()]
    print(f'spending order today: {" → ".join(order) or "NONE USABLE"}\n')
    for name, last_day, label in KEYS:
        key = env.get(name)
        status = 'missing from .env' if not key else ('PAST ITS DATE' if dt.date.today() > dt.date.fromisoformat(last_day)
                                                     else 'in date')
        print(f'{name}  ({label}, usable through {last_day}) — {status}')
        if st.get(name, {}).get('refused_at'):
            r = st[name]
            print(f"  last refused {r['refused_at']}: {r.get('status')} {r.get('message', '')[:120]}")
        if not key:
            continue
        try:
            c = db.Historical(key)
            ds = c.metadata.list_datasets()
            cost = c.metadata.get_cost(dataset='GLBX.MDP3', symbols=['MNQ.c.0'], stype_in='continuous',
                                       schema='ohlcv-1m', start='2026-10-01', end='2026-10-02')
            need = [d for d in ('GLBX.MDP3', 'XNAS.ITCH', 'EQUS.SUMMARY') if d not in ds]
            print(f'  key works: {len(ds)} datasets visible' + (f', MISSING {need}' if need else ', all three we use')
                  + f'; one day of MNQ 1-min quotes ${cost:.4f} (quote only, nothing bought)')
        except Exception as e:
            print(f'  key check FAILED: {type(e).__name__} {str(e)[:200]}')
    print('\nDataBento does not report remaining credit through the API — check the balance on databento.com.')


if __name__ == '__main__':
    _cli()
