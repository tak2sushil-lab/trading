"""
tests_market_state.py — Oct 9 2026

Pins market_state.py (the Market State Record). Uses a SCRATCH database — never the live trades.db.

    venv/bin/python tests_market_state.py
"""
import os, sys, shutil, sqlite3, tempfile, types
import numpy as np, pandas as pd
sys.path.insert(0, '/Users/sushil/trading')
import market_state as M

fails = []


def check(name, cond, detail=''):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ''))
    if not cond:
        fails.append(name)


TMP = tempfile.mkdtemp()
M.DB = os.path.join(TMP, 'trades.db')
M.send_telegram = lambda msg: None
M.log = lambda msg: None
M.init_db()

print("── close values ──")
f, ex = M.panel()
day = pd.Timestamp('2026-09-28')                       # a known FEAR day (VIX +8.1%, universe -1.95%)
xa_dates = pd.to_datetime(['2026-09-25', '2026-09-26', '2026-09-27', '2026-09-28'])
xa = pd.DataFrame({'vix': [14.87, np.nan, np.nan, 16.07], 'vix3m': [17.5, np.nan, np.nan, 18.2], 'tnx': [5.18, np.nan, np.nan, 5.24],
                   'uup': [29.0, np.nan, np.nan, 28.9], 'btc': [80000, 81000, 82000, 79000]}, index=xa_dates)
v = M.close_values(day, f, ex, xa)
check('weekend rows (bitcoin) do not blank the VIX change', v.get('vix_chg') is not None and abs(v['vix_chg'] - (16.07 / 14.87 - 1)) < 1e-9,
      f"vix_chg={v.get('vix_chg')}")
check('FEAR flag on a VIX +8% / universe -1.9% day', v['fear'] == 1 and v['stand_aside_shadow'] == 1, f"fear={v['fear']}")
check('Fear Rebound basket = 10 hardest-hit WILD names', len(eval(v['rebound_basket'].replace('null', 'None'))) == 10)
v2 = M.close_values(day, f, ex, pd.DataFrame())
check('no cross-asset data -> still records the market, FEAR=0', v2['fear'] == 0 and v2.get('mkt_ret') is not None, str(v2.get('weather')))
calm = xa.copy(); calm.loc['2026-09-28', 'vix'] = 15.0         # VIX +0.9% -> calm storm
check('market down without a VIX jump is NOT fear', M.close_values(day, f, ex, calm)['fear'] == 0)
check('tide_ratio equals the live Basket Tide series',
      abs(f.at[pd.Timestamp('2026-10-08'), 'tide_ratio'] - (lambda b: b['index'].iloc[-1] / b['ma'].iloc[-1] - 1)(
          __import__('factory.live.basket_tide', fromlist=['x']).series().loc[:'2026-10-08'])) < 1e-12)

print("── upsert isolation ──")
M.upsert('2026-09-28', {'am_label': 'PANIC_GAPDOWN', 'am_rty_gap': -0.012})
M.upsert('2026-09-28', {k: v[k] for k in ('weather', 'fear', 'mkt_ret')})
r = M.rows('date=?', ('2026-09-28',)).iloc[0]
check('a close write keeps the morning columns', r['am_label'] == 'PANIC_GAPDOWN' and r['fear'] == 1, f"{r['am_label']} {r['fear']}")
M.upsert('2026-09-28', {'am_label': 'CALM_GAPDOWN'})
r = M.rows('date=?', ('2026-09-28',)).iloc[0]
check('a morning write keeps the close columns', r['weather'] == v['weather'] and r['am_label'] == 'CALM_GAPDOWN')

print("── morning labels ──")
today = M.now_et().date().isoformat()
prev = (pd.Timestamp(today) - pd.offsets.BDay(1)).strftime('%Y-%m-%d')
for name, gap, term, w5, want in [('panic gap-down', -0.012, 0.95, -0.03, 'PANIC_GAPDOWN'),
                                  ('calm gap-down (no fear)', -0.012, 0.85, -0.03, 'CALM_GAPDOWN'),
                                  ('calm gap-down (not beaten down)', -0.012, 0.95, 0.02, 'CALM_GAPDOWN'),
                                  ('gap up', 0.008, 0.95, -0.03, 'GAPUP'), ('normal', -0.002, 0.95, -0.03, 'NORMAL')]:
    M.upsert(prev, {'vix_term': term, 'wild_r5': w5})
    M._futures_gap = lambda g=gap: {'rty': g, 'nq': g, 'es': g / 2}
    M.morning_snapshot(telegram=False)
    got = M.rows('date=?', (today,)).iloc[0]['am_label']
    check(name, got == want, f'got {got}')

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'ALL PASS' if not fails else 'FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
