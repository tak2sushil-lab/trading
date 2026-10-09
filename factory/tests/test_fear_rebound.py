"""Fear Rebound tests (Oct 9 2026). Run:  venv/bin/python -m factory.tests.test_fear_rebound
Everything runs on a SCRATCH database with fake quotes and fake orders — nothing reaches IBKR or the live trades.db."""
import datetime as dt, json, os, shutil, sqlite3, sys, tempfile
sys.path.insert(0, '/Users/sushil/trading')
from factory.live import fear_rebound as FR
from factory.live import overnight as CW
from factory.live import night_owl as NO

FAILS = []


def check(name, ok, detail=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ''))
    if not ok:
        FAILS.append(name)


TMP = tempfile.mkdtemp()
DB = os.path.join(TMP, 'trades.db')
FR.DB = CW.DB = NO.DB = DB
FR.log = lambda *a, **k: None
FR.send_telegram = lambda *a, **k: None
ORDERS = []
QUOTES = {}
FILL_OK = {'v': True}


def fake_order(sym, shares, side):
    """FILL_OK: True = filled; False = AMBIGUOUS (order reached IBKR, fill not provable); 'rejected' = no order created."""
    ORDERS.append((sym, shares, side))
    v = FILL_OK['v']
    if v is True:
        return True, QUOTES.get(sym, 50.0), 'oid-' + sym
    if v == 'rejected':
        return False, 0.0, None
    return False, 0.0, 'oid-' + sym


FR.place_paper_order = fake_order
FR.bridge_quote = lambda s: QUOTES.get(s)
NOW = {'t': dt.datetime(2026, 9, 29, 9, 45, tzinfo=FR.ET)}
FR.now_et = lambda: NOW['t']


def reset(mode='LIVE'):
    if os.path.exists(DB):
        os.remove(DB)
    c = sqlite3.connect(DB)
    c.executescript("""
      CREATE TABLE market_state (date TEXT PRIMARY KEY, fear REAL, vix REAL, vix_chg REAL, mkt_ret REAL, rebound_basket TEXT);
      CREATE TABLE overnight_trades (id INTEGER PRIMARY KEY, symbol TEXT, status TEXT, mode TEXT, shares INT, entry_price REAL);
      CREATE TABLE night_owl_trades (id INTEGER PRIMARY KEY, symbol TEXT, status TEXT, mode TEXT);
      CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, status TEXT, setup_type TEXT);
      CREATE TABLE wave_trades (id INTEGER PRIMARY KEY, symbol TEXT, status TEXT, mode TEXT);
      CREATE TABLE contrarian_trades (id INTEGER PRIMARY KEY, symbol TEXT, status TEXT, mode TEXT);""")
    c.commit(); c.close()
    FR.MODE = mode
    FR.init_db()
    ORDERS.clear(); QUOTES.clear(); FILL_OK['v'] = True
    QUOTES.update({'RBLX': 100.0, 'UTI': 40.0, 'ARM': 250.0, 'CRDO': 150.0, 'CDE': 20.0})


def fear_row(date='2026-09-28', fear=1, vix_chg=0.081, basket=('RBLX', 'UTI', 'ARM', 'CRDO', 'CDE')):
    c = sqlite3.connect(DB)
    c.execute("INSERT OR REPLACE INTO market_state VALUES (?,?,?,?,?,?)",
              (date, fear, 16.07, vix_chg, -0.0195, json.dumps(list(basket)) if basket else None))
    c.commit(); c.close()


def q(sql, p=()):
    c = sqlite3.connect(DB)
    try:
        return c.execute(sql, p).fetchall()
    finally:
        c.close()


print('── signal ──')
reset()
check('no market_state row -> no trade', not FR.fear_signal(dt.date(2026, 9, 29))['ok'])
fear_row(vix_chg=None)
check('unknown VIX -> no trade', not FR.fear_signal(dt.date(2026, 9, 29))['ok'])
fear_row(fear=0)
check('calm day -> no trade', not FR.fear_signal(dt.date(2026, 9, 29))['ok'])
fear_row()
s = FR.fear_signal(dt.date(2026, 9, 29))
check('fear day -> basket in hardest-first order', s['ok'] and s['basket'][:3] == ['RBLX', 'UTI', 'ARM'], str(s['basket']))
check('Monday looks back to Friday', FR.previous_session(dt.date(2026, 10, 12)) == dt.date(2026, 10, 9))

print('── entry ──')
reset(); fear_row()
NOW['t'] = dt.datetime(2026, 9, 29, 9, 45, tzinfo=FR.ET)
FR.scan_and_enter()
rows = q("SELECT symbol, shares, entry_price, stop_price, pick_rank, mode FROM fear_rebound_trades ORDER BY id")
check('buys the 3 hardest-hit', [r[0] for r in rows] == ['RBLX', 'UTI', 'ARM'], str(rows))
check('~$3,333 a name', all(abs(r[1] * r[2] - 3333) < max(r[2], 1) for r in rows), str([(r[0], r[1] * r[2]) for r in rows]))
check('-10% disaster stop', all(abs(r[3] - round(r[2] * 0.9, 2)) < 0.011 for r in rows))
check('orders are MARKET buys through the verified path', [o[2] for o in ORDERS] == ['BUY'] * 3)
FR.scan_and_enter()
check('second pass the same day does not re-enter', len(q("SELECT * FROM fear_rebound_trades")) == 3)

reset(); fear_row()
c = sqlite3.connect(DB); c.execute("INSERT INTO trades (symbol,status,setup_type) VALUES ('UTI','OPEN','MOMENTUM')"); c.commit(); c.close()
FR.scan_and_enter()
check('skips a name another book holds, takes the next', [r[0] for r in q("SELECT symbol FROM fear_rebound_trades ORDER BY id")] == ['RBLX', 'ARM', 'CRDO'])

reset(); fear_row(fear=0)
for m in (41, 42, 43):
    NOW['t'] = dt.datetime(2026, 9, 29, 9, m, tzinfo=FR.ET); FR.scan_and_enter()
check('calm day: no entries, ONE summary row for the day',
      not q("SELECT * FROM fear_rebound_trades") and q("SELECT COUNT(*) FROM fear_rebound_scan_log WHERE kind='SUMMARY'")[0][0] == 1)

reset(); fear_row()
FILL_OK['v'] = False
NOW['t'] = dt.datetime(2026, 9, 29, 9, 45, tzinfo=FR.ET); FR.scan_and_enter()
n1 = len([o for o in ORDERS if o[2] == 'BUY'])
FILL_OK['v'] = True
NOW['t'] = dt.datetime(2026, 9, 29, 9, 46, tzinfo=FR.ET); FR.scan_and_enter()
bought = [r[0] for r in q("SELECT symbol FROM fear_rebound_trades ORDER BY id")]
check('ambiguous BUYs use up their slots: 3 attempts, never 5', n1 == 3, f'{n1} BUY attempts')
check('next pass: no retry and no new names — the 3 unconfirmed slots stay reserved (no double position)',
      len(ORDERS) == 3 and bought == [], f'orders={ORDERS} bought={bought}')
reset(); fear_row(); FILL_OK['v'] = 'rejected'
NOW['t'] = dt.datetime(2026, 9, 29, 9, 45, tzinfo=FR.ET); FR.scan_and_enter()
check('outright rejections free the slot — it moves on to the next name', len(ORDERS) == 5, f'{len(ORDERS)} attempts')
FILL_OK['v'] = True

print('── shared pool (Clockwork) ──')
reset(); fear_row()
c = sqlite3.connect(DB); c.execute("INSERT INTO overnight_trades (symbol,status,mode) VALUES ('CENX','PENDING_EXIT','LIVE')"); c.commit(); c.close()
NOW['t'] = dt.datetime(2026, 9, 29, 9, 45, tzinfo=FR.ET); FR.scan_and_enter()
check('Clockwork sell not cleared at 09:45 -> waits', not q("SELECT * FROM fear_rebound_trades"))
NOW['t'] = dt.datetime(2026, 9, 29, 9, 56, tzinfo=FR.ET); FR.scan_and_enter()
check('still not cleared at 09:56 -> enters only the 2 free slots', len(q("SELECT * FROM fear_rebound_trades")) == 2)
check('Clockwork counts the day shift before buying at 15:40', CW.fear_rebound_open() == 2)
check('Night Owl avoids the day shift\'s names', {'RBLX', 'UTI'} <= NO.other_book_symbols('2026-09-29'))

print('── exits ──')
reset(); fear_row(); NOW['t'] = dt.datetime(2026, 9, 29, 9, 45, tzinfo=FR.ET); FR.scan_and_enter()
QUOTES['UTI'] = 35.0                                      # -12.5% -> stop
NOW['t'] = dt.datetime(2026, 9, 29, 11, 0, tzinfo=FR.ET); FR.monitor()
st = dict(q("SELECT symbol, status FROM fear_rebound_trades"))
check('disaster stop sells only the faller', st == {'RBLX': 'OPEN', 'UTI': 'CLOSED', 'ARM': 'OPEN'}, str(st))
FILL_OK['v'] = False
NOW['t'] = dt.datetime(2026, 9, 29, 15, 31, tzinfo=FR.ET); FR.monitor()
check('a sell that does not confirm leaves the row OPEN', dict(q("SELECT symbol, status FROM fear_rebound_trades"))['RBLX'] == 'OPEN')
FILL_OK['v'] = True
FR.monitor()
check('15:30 exit sells everything left', all(r[0] == 'CLOSED' for r in q("SELECT status FROM fear_rebound_trades")))
pnl = q("SELECT symbol, pnl, exit_reason FROM fear_rebound_trades WHERE symbol='UTI'")[0]
check('stop booked with commission', pnl[1] < -(5.0 * 83) + 1 and 'Disaster stop' in pnl[2], str(pnl))

reset(); fear_row(); NOW['t'] = dt.datetime(2026, 9, 29, 9, 45, tzinfo=FR.ET); FR.scan_and_enter()
NOW['t'] = dt.datetime(2026, 9, 30, 9, 31, tzinfo=FR.ET); FR.monitor()
check('a position left over from yesterday sells the next morning',
      all(r[0] == 'CLOSED' for r in q("SELECT status FROM fear_rebound_trades")) and
      'Overdue' in q("SELECT exit_reason FROM fear_rebound_trades")[0][0])

print('── SHADOW ──')
reset('SHADOW'); fear_row(); NOW['t'] = dt.datetime(2026, 9, 29, 9, 45, tzinfo=FR.ET); FR.scan_and_enter()
check('SHADOW places no orders', not ORDERS and len(q("SELECT * FROM fear_rebound_trades WHERE mode='SHADOW'")) == 3)
check('SHADOW rows are invisible to Clockwork\'s pool count', CW.fear_rebound_open() == 0)

print('── reconcile ownership (auto_trader) ──')
reset(); fear_row(); NOW['t'] = dt.datetime(2026, 9, 29, 9, 45, tzinfo=FR.ET); FR.scan_and_enter()
import auto_trader as at
at._DIR = TMP
at.log = lambda *a, **k: None
check('the day trader\'s reconcile treats the day shift\'s names as owned', {'RBLX', 'UTI', 'ARM'} <= at._other_book_symbols())

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
