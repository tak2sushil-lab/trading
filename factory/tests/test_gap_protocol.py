"""Gap-Down Protocol tests (Oct 12 2026). Run:  venv/bin/python -m factory.tests.test_gap_protocol
Scratch databases, fake clock, fake futures/quotes/orders — nothing reaches IBKR, Yahoo or the live trades.db."""
import datetime as dt
import os
import shutil
import sqlite3
import sys
import tempfile
import types

sys.path.insert(0, '/Users/sushil/trading')
from factory.live import gap_protocol as GP          # noqa: E402
from factory.live import overnight as CW             # noqa: E402
from factory.live import night_owl as NO             # noqa: E402
from factory.live import fear_rebound as FR          # noqa: E402

FAILS = []


def check(name, ok, detail=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ''))
    if not ok:
        FAILS.append(name)


TMP = tempfile.mkdtemp()
DB, MDB = os.path.join(TMP, 'trades.db'), os.path.join(TMP, 'market_data.db')
GP.DB = CW.DB = NO.DB = FR.DB = DB
GP.MARKET_DB = MDB
ET = GP.ET
DAY, PREV = '2026-10-13', '2026-10-12'                     # Tue; previous session Mon
NOW = {'t': dt.datetime(2026, 10, 13, 9, 6, tzinfo=ET)}
for m in (GP, CW, NO, FR):
    m.now_et = lambda: NOW['t']
SILENT = lambda *a, **k: None                              # noqa: E731
CW.log = NO.log = FR.log = SILENT
TG = []
GP.send_telegram = lambda msg: TG.append(msg)
NO.send_telegram = FR.send_telegram = SILENT
RTY = {'now': 2786.0, 'prev': 2800.0}                     # -0.50% unless changed
GP.rty_now = lambda: (RTY['now'], 'test') if RTY['now'] else (None, 'no Russell price (test)')
GP.rty_prev_close = lambda prev: (RTY['prev'], 'test')
OPENS, QUOTES = {}, {}
GP.opening_print = lambda sym, day: OPENS.get(sym)
FILLS = {}                                                 # order id -> price (execution record)
GP.fill_by_order_id = lambda bridge, oid, log=None: (True, FILLS[oid], 1) if oid in FILLS else (False, None, 0)
CANCELS = []
GP.requests = types.SimpleNamespace(post=lambda url, **k: CANCELS.append(url))
GP.time = types.SimpleNamespace(sleep=lambda s: None)
ORDERS = []
MKT = {'mode': 'fill'}                                     # 'fill' | 'ambiguous' | 'rejected'


def fake_order(book):
    def _o(sym, shares, side, order_type='MARKET'):
        oid = f'{book}-{sym}-{len(ORDERS)}'
        ORDERS.append((book, sym, shares, side, order_type))
        if order_type in ('MOC', 'MOO'):
            return True, 0.0, oid
        if MKT['mode'] == 'fill':
            return True, QUOTES.get(sym, 50.0), oid
        if MKT['mode'] == 'rejected':
            return False, 0.0, None
        if MKT['mode'] == 'ok_noprice':
            return True, 0.0, oid
        return False, 0.0, oid
    return _o


CW.place_paper_order, NO.place_paper_order = fake_order('CW'), fake_order('NO')
CW.bridge_quote = NO.bridge_quote = FR.bridge_quote = lambda s: QUOTES.get(s)
CW.days_to_earnings = NO.days_to_earnings = lambda s: None
CW.tide_ok = NO.tide_ok = lambda: True


def reset(cw_mode='LIVE', no_mode='LIVE', state=(0.95, -0.03)):
    for p in (DB, MDB):
        if os.path.exists(p):
            os.remove(p)
    c = sqlite3.connect(DB)
    c.executescript("""
      CREATE TABLE market_state (date TEXT PRIMARY KEY, vix_term REAL, wild_r5 REAL, fear REAL, vix REAL, vix_chg REAL,
                                 mkt_ret REAL, rebound_basket TEXT);
      CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, status TEXT, setup_type TEXT);
      CREATE TABLE wave_trades (id INTEGER PRIMARY KEY, symbol TEXT, status TEXT, mode TEXT);
      CREATE TABLE contrarian_trades (id INTEGER PRIMARY KEY, symbol TEXT, status TEXT, mode TEXT);""")
    if state:
        c.execute("INSERT INTO market_state(date, vix_term, wild_r5) VALUES(?,?,?)", (PREV, *state))
    c.commit(); c.close()
    CW.MODE, NO.MODE = cw_mode, no_mode
    CW.init_db(); NO.init_db(); FR.init_db()
    ORDERS.clear(); TG.clear(); CANCELS.clear(); FILLS.clear(); OPENS.clear(); QUOTES.clear()
    MKT['mode'] = 'fill'
    RTY.update(now=2786.0, prev=2800.0)


def add_open(tbl, sym, shares=20, price=100.0, entry=PREV, mode='LIVE'):
    c = sqlite3.connect(DB)
    c.execute(f"INSERT INTO {tbl}(symbol, entry_date, entry_time, entry_price, shares, status, mode) "
              f"VALUES(?,?,?,?,?,?,?)", (sym, entry, '15:45:00', price, shares, 'OPEN', mode))
    c.commit(); c.close()


def rows(tbl):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    try:
        return {r['symbol']: dict(r) for r in c.execute(f"SELECT * FROM {tbl} ORDER BY id")}
    finally:
        c.close()


def at(h, m):
    NOW['t'] = dt.datetime(2026, 10, 13, h, m, tzinfo=ET)


print('── the decision ──')
reset()
at(9, 3)
check('before 09:05: no decision, engines WAIT', GP.decide(log=SILENT) is None and GP.morning_action(SILENT) == GP.WAIT)
RTY['now'] = 2770.0                                        # -1.07%
at(9, 6)
d = GP.decide(log=SILENT)
check('PANIC: gap-down + fear high + basket down -> HOLD', d['label'] == 'PANIC_GAPDOWN' and d['action'] == GP.HOLD, str(d))
check('PANIC announced once', len(TG) == 1 and 'HOLDING' in TG[0], str(TG))
RTY['now'] = 2810.0
at(9, 8)
d2 = GP.decide(log=SILENT)
check('one decision per day — a later read cannot change it', d2['label'] == 'PANIC_GAPDOWN' and len(TG) == 1)

for name, state, now_px, want in (
        ('CALM: gap-down without high fear -> sell at the open', (0.85, -0.03), 2770.0, 'CALM_GAPDOWN'),
        ('CALM: high fear but basket not beaten down -> sell', (0.95, 0.01), 2770.0, 'CALM_GAPDOWN'),
        ('NORMAL: small dip -> sell at the open', (0.95, -0.03), 2795.0, 'NORMAL'),
        ('GAPUP -> sell at the open', (0.95, -0.03), 2820.0, 'GAPUP'),
        ("gap-down but yesterday's state missing -> not PANIC", None, 2770.0, 'GAPDOWN_UNCONFIRMED')):
    reset(state=state); RTY['now'] = now_px; at(9, 6)
    d = GP.decide(log=SILENT)
    check(name, d['label'] == want and d['action'] == GP.SELL, f"{d['label']} {d['action']}")
check('boundaries: gap -0.5%, VIX/VIX3M 0.901, basket -1.07% all count as PANIC',
      GP.classify(GP.PANIC_GAP, {'vix_term': GP.PANIC_VIX_TERM, 'wild_r5': GP.PANIC_WILD5}) == 'PANIC_GAPDOWN')
check('just inside the boundaries is not PANIC',
      GP.classify(GP.PANIC_GAP + 1e-4, {'vix_term': 0.95, 'wild_r5': -0.05}) == 'NORMAL'
      and GP.classify(-0.01, {'vix_term': GP.PANIC_VIX_TERM - 1e-4, 'wild_r5': -0.05}) == 'CALM_GAPDOWN')

reset(); RTY['now'] = None; at(9, 7)
check('no futures price at 09:07 -> no decision yet (retry), nothing stored',
      GP.decide(log=SILENT) is None and GP.today_decision(DAY) is None and GP.morning_action(SILENT) == GP.WAIT)
at(9, 16)
d = GP.decide(log=SILENT)
check('still no futures price at 09:16 -> UNKNOWN, sell at the open, warned', d['label'] == 'UNKNOWN'
      and d['action'] == GP.SELL and any('could not judge' in m for m in TG))
reset(); RTY['now'] = 1500.0; at(9, 6)
check('a -46% "gap" is a data error, not a market (retried, not acted on)', GP.decide(log=SILENT) is None)
reset(); at(9, 27)
d = GP.decide(log=SILENT)
check('first asked after the 09:26 last call -> NO_DECISION, sell', d['label'] == 'NO_DECISION' and d['action'] == GP.SELL)
reset(); NOW['t'] = dt.datetime(2026, 10, 17, 9, 6, tzinfo=ET)
check('weekend: no decision', GP.decide(log=SILENT) is None)

print('── Clockwork on a PANIC morning ──')
reset(); RTY['now'] = 2770.0
for s in ('AXTI', 'CENX', 'AG'):
    add_open('overnight_trades', s)
add_open('night_owl_trades', 'MXL'); add_open('night_owl_trades', 'SMTC')
at(9, 3); CW.exit_at_open()
check('09:03: nothing sold before the decision', not ORDERS)
at(9, 6); CW.exit_at_open(); NO.exit_at_open()
r = rows('overnight_trades')
check('PANIC: no MOO sells sent by either book', not ORDERS, str(ORDERS))
check('all overnight positions marked held for today', all(x['gap_hold_date'] == DAY and x['status'] == 'OPEN' for x in r.values())
      and all(x['gap_hold_date'] == DAY for x in rows('night_owl_trades').values()))
check('the announcement lists both books\' held names', TG and 'AXTI' in TG[0] and 'MXL' in TG[0], TG[0] if TG else '')
at(9, 11); CW.exit_at_open()
check('a second morning pass sends nothing either', not ORDERS)
at(9, 20); GP.monitor_holds(CW, 'overnight_trades')
check('before 09:31 the holds are left alone', all(x['gap_hold_open'] is None for x in rows('overnight_trades').values()))
OPENS.update(AXTI=60.0, CENX=30.0, AG=10.0)
QUOTES.update(AXTI=61.0, CENX=29.0, AG=10.5)
at(9, 35); GP.monitor_holds(CW, 'overnight_trades')
r = rows('overnight_trades')
check('stop set 15% below the opening print', abs(r['AXTI']['gap_hold_stop'] - 51.0) < 1e-9 and abs(r['CENX']['gap_hold_stop'] - 25.5) < 1e-9)
check('no sells mid-morning above the stop', not ORDERS)
QUOTES['CENX'] = 25.0
at(11, 0); GP.monitor_holds(CW, 'overnight_trades')
r = rows('overnight_trades')
check('disaster stop sells only the faller (MARKET)', r['CENX']['status'] == 'CLOSED' and 'disaster stop' in r['CENX']['exit_reason']
      and ORDERS == [('CW', 'CENX', 20, 'SELL', 'MARKET')], str(ORDERS))
check('the others are still held', r['AXTI']['status'] == 'OPEN' and r['AG']['status'] == 'OPEN')
TG.clear()
at(15, 31); GP.monitor_holds(CW, 'overnight_trades')
r = rows('overnight_trades')
check('15:30: everything left is sold', all(x['status'] == 'CLOSED' for x in r.values()))
check('booked at the fill with the protocol reason', r['AXTI']['exit_price'] == 61.0 and r['AXTI']['exit_reason'] == GP.REASON_EXIT)
check('the sale is announced', any('Clockwork sold' in m for m in TG), str(TG))

print('── an unconfirmed protocol sell is never sent twice ──')
reset(); RTY['now'] = 2770.0
add_open('overnight_trades', 'AXTI'); at(9, 6); CW.exit_at_open()
OPENS['AXTI'] = 60.0; QUOTES['AXTI'] = 62.0
MKT['mode'] = 'ambiguous'
at(15, 31); GP.monitor_holds(CW, 'overnight_trades')
r = rows('overnight_trades')['AXTI']
check('ambiguous sell: row stays OPEN, its order id is kept', r['status'] == 'OPEN' and r['gap_sell_oid'], str(r['gap_sell_oid']))
oid = r['gap_sell_oid']; FILLS[oid] = 61.75; n0 = len(ORDERS)
at(15, 36); GP.monitor_holds(CW, 'overnight_trades')
r = rows('overnight_trades')['AXTI']
check('next pass: the execution record shows it filled -> booked at that fill, NO second order',
      r['status'] == 'CLOSED' and r['exit_price'] == 61.75 and len(ORDERS) == n0, f"{r['status']} orders {len(ORDERS) - n0}")
reset(); RTY['now'] = 2770.0
add_open('overnight_trades', 'AXTI'); at(9, 6); CW.exit_at_open()
OPENS['AXTI'] = 60.0; QUOTES['AXTI'] = 62.0; MKT['mode'] = 'ambiguous'
at(15, 31); GP.monitor_holds(CW, 'overnight_trades')
MKT['mode'] = 'fill'; n0 = len(ORDERS)
at(15, 36); GP.monitor_holds(CW, 'overnight_trades')
r = rows('overnight_trades')['AXTI']
check('next pass, still unfilled: the earlier order is CANCELLED first, then one fresh sell',
      CANCELS and len(ORDERS) == n0 + 1 and r['status'] == 'CLOSED', f"cancels {CANCELS} orders {len(ORDERS) - n0}")
reset(); RTY['now'] = 2770.0
add_open('overnight_trades', 'AXTI'); at(9, 6); CW.exit_at_open()
OPENS['AXTI'] = 60.0; QUOTES['AXTI'] = 50.0; MKT['mode'] = 'ambiguous'
at(10, 0); GP.monitor_holds(CW, 'overnight_trades')
n0 = len(ORDERS)
at(10, 5); GP.monitor_holds(CW, 'overnight_trades')
check('a doubtful STOP sell before 15:30 is not stacked with another order', len(ORDERS) == n0 and not CANCELS)

reset(); RTY['now'] = 2770.0
add_open('overnight_trades', 'AXTI'); at(9, 6); CW.exit_at_open()
OPENS['AXTI'] = 60.0; QUOTES['AXTI'] = 62.0; MKT['mode'] = 'ok_noprice'
at(15, 31); GP.monitor_holds(CW, 'overnight_trades')
r = rows('overnight_trades')['AXTI']
check('a sell reported without a price is NOT booked at some other fill — left OPEN with its order id',
      r['status'] == 'OPEN' and r['gap_sell_oid'])
FILLS[r['gap_sell_oid']] = 61.4; n0 = len(ORDERS)
at(15, 36); GP.monitor_holds(CW, 'overnight_trades')
r = rows('overnight_trades')['AXTI']
check('next pass books it at THAT order\'s own fill, no new order', r['status'] == 'CLOSED' and r['exit_price'] == 61.4
      and len(ORDERS) == n0)

print('── normal mornings and leftovers ──')
reset(); RTY['now'] = 2795.0                              # NORMAL
for s in ('AXTI', 'CENX'):
    add_open('overnight_trades', s)
at(9, 6); CW.exit_at_open()
r = rows('overnight_trades')
check('NORMAL morning: MOO sells exactly as before', [o[4] for o in ORDERS] == ['MOO', 'MOO']
      and all(x['status'] == 'PENDING_EXIT' and x['gap_hold_date'] is None for x in r.values()))
reset(); RTY['now'] = 2795.0
add_open('overnight_trades', 'AXTI')
c = sqlite3.connect(DB); c.execute("UPDATE overnight_trades SET gap_hold_date=?", (PREV,)); c.commit(); c.close()
at(9, 6); CW.exit_at_open()
check("yesterday's unsold hold goes out in today's MOO on a normal morning", ORDERS == [('CW', 'AXTI', 20, 'SELL', 'MOO')], str(ORDERS))
reset(); RTY['now'] = None
add_open('overnight_trades', 'AXTI')
at(9, 7); CW.exit_at_open()
check('no futures price yet: Clockwork waits (no MOO)', not ORDERS)
at(9, 16); CW.exit_at_open()
check('give-up time: sells at the open after all', ORDERS == [('CW', 'AXTI', 20, 'SELL', 'MOO')], str(ORDERS))

print('── entries at the close ──')
reset(); RTY['now'] = 2770.0
add_open('overnight_trades', 'AXTI'); add_open('night_owl_trades', 'MXL')
at(9, 6); CW.exit_at_open(); NO.exit_at_open()
MKT['mode'] = 'ambiguous'; OPENS.update(AXTI=60.0, MXL=20.0); QUOTES.update(AXTI=61.0, MXL=20.0, CENX=30.0, AG=10.0, PI=90.0)
at(15, 31); GP.monitor_holds(CW, 'overnight_trades'); GP.monitor_holds(NO, 'night_owl_trades')
check('both holds still unsold (sells unconfirmed)', GP.unsold_holds(DAY) == {'AXTI', 'MXL'}, str(GP.unsold_holds(DAY)))
CW.consistency_signal = lambda: {'MXL': 0.9, 'AXTI': 0.8, 'CENX': 0.7, 'AG': 0.6, 'PI': 0.5}
MKT['mode'] = 'fill'; ORDERS.clear()
at(15, 41); CW.scan_and_enter()
bought = [o[1] for o in ORDERS if o[3] == 'BUY']
check("Clockwork never buys a name either book is still selling", 'AXTI' not in bought and 'MXL' not in bought, str(bought))
check('its unsold hold still counts against TOP_N (2 buys, not 3)', len(bought) == 2, str(bought))
c = sqlite3.connect(DB)
for i, s in enumerate(('AXTI', 'MXL', 'SITM', 'UUUU')):
    c.execute("INSERT INTO night_owl_scores(score_date,symbol,score,rank,wild,wild_rank,model_version) VALUES(?,?,?,?,1,?,'t')",
              (DAY, s, 1.0 - i / 10, i + 1, i + 1))
c.commit(); c.close()
QUOTES.update(SITM=40.0, UUUU=12.0)
NO.score_today = lambda force=False: True
ORDERS.clear()
at(15, 47); NO.scan_and_enter()
bought = [o[1] for o in ORDERS if o[3] == 'BUY']
check('Night Owl skips names still being sold and keeps its own hold in the cap', bought == ['SITM', 'UUUU'], str(bought))
check('Night Owl stays active while it holds', GP.has_holds('night_owl_trades', 'LIVE', DAY))

print('── Night Owl + SHADOW ──')
reset(cw_mode='SHADOW', no_mode='LIVE'); RTY['now'] = 2770.0
add_open('night_owl_trades', 'MXL'); add_open('overnight_trades', 'AXTI', mode='SHADOW')
at(9, 6); NO.exit_at_open()
check('Night Owl holds on PANIC', rows('night_owl_trades')['MXL']['gap_hold_date'] == DAY and not ORDERS)
at(9, 40); CW.exit_at_open()                               # SHADOW marks out after the open
check('SHADOW Clockwork follows the same decision (held, not marked out)',
      rows('overnight_trades')['AXTI']['gap_hold_date'] == DAY and rows('overnight_trades')['AXTI']['status'] == 'OPEN')
OPENS.update(MXL=20.0, AXTI=60.0); QUOTES.update(MXL=21.0, AXTI=63.0)
at(15, 31); GP.monitor_holds(NO, 'night_owl_trades'); GP.monitor_holds(CW, 'overnight_trades')
check('15:30: Night Owl sold (MARKET), SHADOW Clockwork marked out with no order',
      rows('night_owl_trades')['MXL']['status'] == 'CLOSED' and rows('overnight_trades')['AXTI']['exit_price'] == 63.0
      and [o[1] for o in ORDERS] == ['MXL'], str(ORDERS))

print('── alert at 15:50 ──')
reset(); RTY['now'] = 2770.0
add_open('overnight_trades', 'AXTI'); at(9, 6); CW.exit_at_open()
OPENS['AXTI'] = 60.0; QUOTES['AXTI'] = 61.0; MKT['mode'] = 'rejected'; TG.clear()
at(15, 51); GP.monitor_holds(CW, 'overnight_trades')
at(15, 52); GP.monitor_holds(CW, 'overnight_trades')
check('unsold at 15:50 -> one alert, not one per pass', sum('still holds' in m for m in TG) == 1, str(TG))

print('── Fear Rebound on a hold day ──')
reset(); RTY['now'] = 2770.0
c = sqlite3.connect(DB)
c.execute("UPDATE market_state SET fear=1, vix=25, vix_chg=0.08, mkt_ret=-0.02, rebound_basket=? WHERE date=?",
          ('["RBLX","UTI","ARM"]', PREV))
c.commit(); c.close()
for s in ('AXTI', 'CENX', 'AG'):
    add_open('overnight_trades', s)
at(9, 6); CW.exit_at_open()
FR.MODE = 'LIVE'; FR.place_paper_order = lambda *a, **k: (_ for _ in ()).throw(AssertionError('no order expected'))
at(9, 41); FR.scan_and_enter(); at(9, 42); FR.scan_and_enter()
c = sqlite3.connect(DB)
summ = c.execute("SELECT detail FROM fear_rebound_scan_log WHERE kind='SUMMARY'").fetchall(); c.close()
check('fear day + PANIC hold: Fear Rebound stands down at once, says why, records it once',
      len(summ) == 1 and 'Gap-Down Protocol' in summ[0][0], str(summ))

print('── reconcile ownership ──')
import auto_trader as at_mod                               # noqa: E402
at_mod._DIR = TMP
at_mod.log = SILENT
reset(); RTY['now'] = 2770.0
add_open('overnight_trades', 'AXTI'); at(9, 6); CW.exit_at_open()
check("the day trader's reconcile still owns a held position", 'AXTI' in at_mod._other_book_symbols())

print('── nightly official-price marks ──')
import backfill_overnight_reference as BR                  # noqa: E402
BR.TRADES, BR.MARKET = DB, MDB
reset()
c = sqlite3.connect(MDB)
c.execute("CREATE TABLE bars_5m (symbol TEXT, ts_utc TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)")
bars = [('AXTI', f'{PREV} 19:55:00+00:00', 99, 99, 99, 100.0), ('AXTI', f'{DAY} 13:30:00+00:00', 95.0, 96, 94, 95.5),
        ('AXTI', f'{DAY} 19:25:00+00:00', 98.0, 98.5, 97.5, 98.2), ('AXTI', f'{DAY} 19:30:00+00:00', 98.2, 99, 98, 98.9)]
c.executemany("INSERT INTO bars_5m VALUES(?,?,?,?,?,?,0)", bars); c.commit(); c.close()
c = sqlite3.connect(DB)
c.execute("""INSERT INTO overnight_trades(symbol, entry_date, entry_price, shares, status, exit_date, exit_time, exit_price,
             pnl, mode, gap_hold_date) VALUES('AXTI',?,100,10,'CLOSED',?, '15:31:05', 98.3, -17.0, 'LIVE', ?)""",
          (PREV, DAY, DAY))
c.commit(); c.close()
BR.mark('overnight_trades')
r = rows('overnight_trades')['AXTI']
check('held row: ref_open = the official open, ref_exit = the 15:30 price, hold_gain = the difference x shares',
      r['ref_open'] == 95.0 and abs(r['ref_exit'] - 98.2) < 1e-9 and abs(r['hold_gain'] - 32.0) < 1e-6, str(
          {k: r[k] for k in ('ref_open', 'ref_exit', 'hold_gain', 'ref_pnl')}))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
