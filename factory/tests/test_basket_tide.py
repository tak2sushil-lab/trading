"""Basket Tide tests (Oct 6 2026). Run:  venv/bin/python -m factory.tests.test_basket_tide
Uses the real daily cache; the state file and Telegram are redirected to a scratch directory."""
import datetime as dt, json, os, shutil, sys, tempfile
import pandas as pd
sys.path.insert(0, '/Users/sushil/trading')
from factory.live import basket_tide as B

FAILS = []


def check(name, ok, detail=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ''))
    if not ok:
        FAILS.append(name)


def test_parity():
    """The live switch must equal the gate the research was scored on (research_portfolio_lab.py)."""
    ref = '/Users/sushil/trading/research_out/gate_200d.parquet'
    if not os.path.exists(ref):
        print('SKIP  parity — research_out/gate_200d.parquet not present'); return
    g = pd.read_parquet(ref).gate_on
    live = B.series()['above'].shift(1).reindex(g.index)      # verdict for session t = comparison at t-1
    m = live.notna()
    agree = (live[m].astype(bool) == g[m]).mean()
    check('parity with the research gate', agree == 1.0, f'{agree:.4%} of {m.sum()} sessions')


def test_causal():
    """Today's verdict may only use completed sessions — appending a fake crash today must not change it."""
    d = pd.read_parquet(B.DAILY)
    last = d.date.max()
    today = (last + pd.offsets.BDay(1)).date()
    base = B.series(d)
    base = base[base.index < pd.Timestamp(today)].dropna(subset=['above']).iloc[-1]['above']
    crash = d[d.date == last].copy(); crash['date'] = pd.Timestamp(today)
    for k in ('open', 'high', 'low', 'close'):
        crash[k] = crash[k] * 0.5
    s = B.series(pd.concat([d, crash]))
    s = s[s.index < pd.Timestamp(today)].dropna(subset=['above']).iloc[-1]['above']
    check("a crash on the decision day itself cannot flip that day's verdict", s == base)


def test_fail_open_and_announce():
    tmp = tempfile.mkdtemp()
    saved = (B.DAILY, B.STATE)
    try:
        B.STATE = os.path.join(tmp, 'state.json')
        B.DAILY = os.path.join(tmp, 'missing.parquet')
        st = B.compute(dt.date(2026, 10, 6))
        check('missing cache → fails OPEN', st['on'] and st['stale'], st['reason'])
        B.DAILY = saved[0]
        st = B.compute(dt.date(2027, 6, 1))                     # far beyond the cache → stale
        check('stale cache → fails OPEN', st['on'] and st['stale'], st['reason'])
        sent = []
        B.announce(log=lambda m: None, send=sent.append)
        B.announce(log=lambda m: None, send=sent.append)
        check('first run announces once, second run is silent', len(sent) == 1, f'{len(sent)} messages')
        st = json.load(open(B.STATE)); st['announced_on'] = not st['on']
        json.dump(st, open(B.STATE, 'w'))
        B.announce(log=lambda m: None, send=sent.append)
        check('a flip announces again', len(sent) == 2 and ('turned' in sent[-1]), sent[-1][:60])
    finally:
        B.DAILY, B.STATE = saved
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    test_parity(); test_causal(); test_fail_open_and_announce()
    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
