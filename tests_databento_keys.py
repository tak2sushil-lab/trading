"""Tests for databento_keys.py (Oct 6 2026). Run:  venv/bin/python tests_databento_keys.py
Uses only FREE DataBento calls (metadata.get_cost / list_datasets) — nothing is bought. State and Telegram are
redirected so the live state file is never touched and no message is sent."""
import datetime as dt
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import databento_keys as K

FAILS = []
QUOTE = dict(dataset='GLBX.MDP3', symbols=['MNQ.c.0'], stype_in='continuous', schema='ohlcv-1m',
             start='2026-10-01', end='2026-10-02')


def check(name, ok, detail=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ''))
    if not ok:
        FAILS.append(name)


def main():
    tmp = tempfile.mkdtemp()
    saved = (K.STATE, K._telegram, os.environ.get('DATABENTO_API_KEY'))
    sent = []
    try:
        K.STATE = os.path.join(tmp, 'state.json')
        K._telegram = sent.append

        # 1. normal day: the primary pays
        c = K.historical()
        cost = c.metadata.get_cost(**QUOTE)
        check('normal: primary key serves the call', c.account == 'DATABENTO_API_KEY', f'{c.account}, quote ${cost:.4f}')

        # 2. dates: primary stops after 2026-12-31, reserve after 2027-04-27
        names = lambda d: [n for n, _, _ in K.usable_keys(d)]
        check('dates: both usable on 2026-12-31', names(dt.date(2026, 12, 31)) == ['DATABENTO_API_KEY', 'DATABENTO_API_KEY_2'])
        check('dates: only the reserve on 2027-01-01', names(dt.date(2027, 1, 1)) == ['DATABENTO_API_KEY_2'])
        check('dates: reserve still usable on 2027-04-27', names(dt.date(2027, 4, 27)) == ['DATABENTO_API_KEY_2'])
        check('dates: nothing on 2027-04-28', names(dt.date(2027, 4, 28)) == [])

        # 3. a request that is wrong in itself must NOT move to the reserve
        try:
            K.historical().metadata.get_cost(**{**QUOTE, 'symbols': ['NO_SUCH_SYMBOL_XYZ'], 'stype_in': 'raw_symbol'})
            raised = None
        except Exception as e:
            raised = e
        check('bad request is raised, not retried on the reserve',
              raised is not None and not K._is_account_refusal(raised) and not os.path.exists(K.STATE),
              f'{type(raised).__name__} {getattr(raised, "http_status", "")}' if raised else 'no error raised')

        # 4. primary refused for an account reason (a broken key → 401) → the reserve serves it, one Telegram
        os.environ['DATABENTO_API_KEY'] = 'db-' + 'X' * 29
        c = K.historical()
        cost = c.metadata.get_cost(**QUOTE)
        st = K._load_state().get('DATABENTO_API_KEY', {})
        check('refused primary → reserve serves the call', c.account == 'DATABENTO_API_KEY_2',
              f'{c.account}, primary refused with {st.get("status")}')
        check('refusal recorded with its status and message', st.get('status') in (401, 403) and st.get('message'))
        check('one Telegram on the switch', len(sent) == 1, sent[0][:90] if sent else 'none')

        # 5. within 24h the refused key is skipped outright (no repeat failure, no repeat Telegram)
        c = K.historical()
        c.metadata.get_cost(**QUOTE)
        check('next call goes straight to the reserve', c.account == 'DATABENTO_API_KEY_2'
              and [n for n, _, _ in K.usable_keys()] == ['DATABENTO_API_KEY_2'])
        check('no second Telegram the same day', len(sent) == 1, f'{len(sent)} messages')

        # 6. after 24h the primary is tried again (e.g. topped up) — restore the real key and age the refusal
        if saved[2] is None:
            os.environ.pop('DATABENTO_API_KEY', None)     # back to the real key from .env
        else:
            os.environ['DATABENTO_API_KEY'] = saved[2]
        st = K._load_state()
        st['DATABENTO_API_KEY']['refused_at'] = (dt.datetime.now() - dt.timedelta(hours=25)).isoformat(timespec='seconds')
        K._save_state(st)
        c = K.historical()
        c.metadata.get_cost(**QUOTE)
        check('after 24h the primary is tried again and pays', c.account == 'DATABENTO_API_KEY')
    finally:
        K.STATE, K._telegram = saved[0], saved[1]
        if saved[2] is None:
            os.environ.pop('DATABENTO_API_KEY', None)
        else:
            os.environ['DATABENTO_API_KEY'] = saved[2]
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
