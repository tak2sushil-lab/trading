"""
tests_entry_band.py — Oct 9 2026

Pins the ENTRY PRICE BAND (the "ordering gap", RESEARCH_REGISTRY §R4) and the cancel-unfilled-limit fix in
place_trade(). Run after ANY change to the entry loop, place_trade() or the band constants:

    venv/bin/python tests_entry_band.py

Everything is faked (quotes, orders, positions, DB path) — nothing reaches IBKR or the live trades.db.
"""
import os, sys, sqlite3, tempfile, time as _realtime, types
sys.path.insert(0, '/Users/sushil/trading')

import auto_trader as at

fails = []


def check(name, cond, detail=''):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ''))
    if not cond:
        fails.append(name)


class _Resp:
    def __init__(self, body, status=200):
        self._b, self.status_code = body, status
        self.text = 'ok'
    def json(self):
        return self._b


def quotes(table, raise_on_quote=False):
    def _get(url, *a, **kw):
        if '/quote/' in url:
            if raise_on_quote:
                raise ConnectionError('no bridge')
            return _Resp(table[url.rsplit('/', 1)[-1]])
        raise ConnectionError('unexpected GET ' + url)
    at.requests = types.SimpleNamespace(get=_get, post=lambda *a, **k: _Resp({}))


at.log = lambda *a, **k: None
at.send_telegram = lambda *a, **k: None
TMP = tempfile.mkdtemp()
at._DIR = TMP                                    # entry_band_log goes to a scratch trades.db

print("── _entry_band_check ──")
SIG = 100.00
cases = [('in band +0.10%', 100.10, True, 'PASS'), ('top edge +0.25%', 100.25, True, 'PASS'),
         ('just past the top +0.26%', 100.26, False, 'CHASE_SKIP'), ('chased +0.60%', 100.60, False, 'CHASE_SKIP'),
         ('bottom edge -0.05%', 99.95, True, 'PASS'), ('falling -0.20%', 99.80, False, 'FALLING_SKIP')]
for name, ask, ok_exp, verdict in cases:
    quotes({'XYZ': {'bid': ask - 0.02, 'ask': ask, 'last': ask - 0.01, 'best_price': ask - 0.01}})
    ok, lim, info = at._entry_band_check('XYZ', SIG)
    check(name, ok == ok_exp and info['verdict'] == verdict, f"ok={ok} verdict={info['verdict']} dev={info.get('dev_pct')}")
    if ok:
        check(f'{name}: limit is a LIMIT at the band top and >= the ask', lim == 100.25 and lim >= ask, f'limit={lim}')

quotes({'XYZ': {'bid': None, 'ask': None, 'last': None, 'best_price': None}})
at.get_live_price = lambda s: None
ok, lim, info = at._entry_band_check('XYZ', SIG)
check('no quote anywhere -> skip (never order blind)', ok is False and info['verdict'] == 'NO_QUOTE', str(info['verdict']))

quotes({}, raise_on_quote=True)
at.get_live_price = lambda s: SIG                # equity_replay patches get_live_price to the stored bar
ok, lim, info = at._entry_band_check('XYZ', SIG)
check('bridge down -> get_live_price fallback (replay path) passes at the signal price',
      ok and info['source'] == 'fallback' and lim == 100.25, f"{info['verdict']} {info['source']} {lim}")

import random
random.seed(1)
bad = 0
for _ in range(2000):
    sig = round(random.uniform(5, 800), 2)
    ask = round(sig * (1 + random.uniform(-0.0005, 0.0025)), 2)
    quotes({'R': {'ask': ask, 'bid': ask - 0.01, 'last': ask, 'best_price': ask}})
    ok, lim, _ = at._entry_band_check('R', sig)
    if ok and lim < ask:
        bad += 1
check('limit never below the ask (2,000 random prices)', bad == 0, f'{bad} violations')

print("── entry_band_log ──")
rid = at._log_entry_band('XYZ', {'signal_px': 100, 'ask': 100.1, 'ref_px': 100.1, 'source': 'ask', 'dev_pct': 0.1,
                                 'verdict': 'PASS', 'limit_px': 100.25})
at._log_entry_band('XYZ', {'verdict': 'FILLED'}, trade_id=42, row_id=rid)
c = sqlite3.connect(os.path.join(TMP, 'trades.db'))
row = c.execute("SELECT symbol, verdict, trade_id, limit_px FROM entry_band_log WHERE id=?", (rid,)).fetchone()
c.close()
check('row written then finished with the trade id', row == ('XYZ', 'FILLED', 42, 100.25), str(row))
at._DIR = '/nonexistent/dir'
check('a logging failure never raises', at._log_entry_band('XYZ', {'verdict': 'PASS'}) is None)
at._DIR = TMP

print("── place_trade: unfilled LIMIT is cancelled, a last-second fill is still recorded ──")
at.time = types.SimpleNamespace(sleep=lambda s: None, time=_realtime.time)
at.log_trade_entry = lambda **kw: 999
at.get_ibkr_positions = lambda: {}


def run_place(status_after_cancel, positions_after=None):
    posts, state = [], {'cancelled': False}

    def _post(url, json=None, timeout=None, **kw):
        posts.append(url)
        if url.endswith('/order'):
            return _Resp({'orderId': 123})
        if url.endswith('/cancel'):
            state['cancelled'] = True
        return _Resp({})

    def _get(url, *a, **kw):
        if url.endswith('/account'):
            return _Resp({'BuyingPower': 1e7})
        if '/status' in url:
            return _Resp(status_after_cancel if state['cancelled'] else {'status': 'Submitted', 'filled': 0})
        raise ConnectionError(url)
    at.requests = types.SimpleNamespace(get=_get, post=_post)
    at.get_ibkr_positions = (lambda: positions_after) if (positions_after and state) else (lambda: {})
    tid = at.place_trade('XYZ', 100.0, 10, 95.0, 110.0, 'MOMENTUM', 'A+', limit_price=100.25)
    return tid, posts


tid, posts = run_place({'status': 'Cancelled', 'filled': 0})
check('never filled -> cancel sent, no trade recorded', tid is None and any(p.endswith('/order/123/cancel') for p in posts),
      f'tid={tid} posts={posts}')
tid, posts = run_place({'status': 'Filled', 'filled': 10, 'avgFillPrice': 100.20})
check('filled just before the cancel -> recorded, not dropped', tid == 999, f'tid={tid}')
print("── market orders untouched ──")
tid, posts = (None, [])
calls = {'n': 0}


def _get_mkt(url, *a, **kw):
    if url.endswith('/account'):
        return _Resp({'BuyingPower': 1e7})
    calls['n'] += 1
    return _Resp({'status': 'Filled', 'filled': 10, 'avgFillPrice': 100.0})


at.requests = types.SimpleNamespace(get=_get_mkt, post=lambda url, json=None, timeout=None, **k: (posts.append(url), _Resp({'orderId': 7}))[1])
tid = at.place_trade('XYZ', 100.0, 10, 95.0, 110.0, 'MOMENTUM', 'A+')
check('market order path still fills and never cancels', tid == 999 and not any('cancel' in p for p in posts), str(posts))

print(f"\n{'ALL PASS' if not fails else 'FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
