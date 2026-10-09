"""Market State Record — one row per session describing the market's 'weather', scored against what came next.
Built Oct 9 2026. LOG-ONLY: no book reads this table yet; it places no orders.

WHY. Every equity book is long-only and the only market-aware inputs anywhere are the Basket Tide (200-day trend, the
overnight books) and Book Health (the day trader). The research of Oct 8-9 (docs/RESEARCH_REGISTRY.md §N-§Q) found
that daily DIRECTION is not predictable from any data we hold, but the market's FEAR state at the close decides where
the next edge sits:
  • after a FEAR day (VIX closed up ≥5% AND our universe fell ≥0.5% on average) the overnight books earned ≈0
    (−4bp vs +30bp on other nights) and the next day's volatile names rebounded (+33bp open→close 2018-26, +70-76bp
    bought 09:35-09:45 on 2024-26 bars; all US stocks 2024-26 +55bp; unselected stocks 2018-23 only +6bp);
  • after a CALM storm (market down, VIX did not jump — Oct 7/8 2026) neither happened.
The thresholds were checked Oct 9: the VIX jump carries the effect (alone +27bp, with the market-down condition +32bp,
adding breadth +33bp); all 150 threshold cells were positive out of sample and thresholds OPTIMISED on 2018-21 did
worse on 2022-26 than these round numbers — so they are kept simple on purpose. Do not tune them on a few months.

WHAT IT RECORDS (trades.db `market_state`, primary key = session date):
  close (~18:15, after night_owl_prep refreshes the daily cache at 17:30)
      universe / WILD basket / semis moves, breadth, dispersion, trendiness, Basket Tide ratio, VIX, VIX/VIX3M, 10-yr,
      SPY/QQQ/IWM/SMH/ARKK/HYG/TLT/dollar/bitcoin day moves, weather label, FEAR flag,
      shadow actions for FEAR days: the 10 hardest-hit WILD names as tomorrow's Fear Rebound basket, and
      "the overnight books would stand aside tonight"
  morning (~09:26)  Russell/Nasdaq/S&P futures gap since the prior 15:55 + label PANIC / CALM gap-down / GAP-UP / NORMAL
                    (registry §O5: gap-down ≤ −0.5% with VIX/VIX3M ≥ 0.901 and the WILD basket ≤ −1.07% over 5 days)
  outcomes (filled on later runs) next night / next day for the WILD basket, the Fear Rebound basket (official open
                    →close and 09:40→close), and the overnight books' actual official-price P&L that night
  auction (~15:52)  closing-auction imbalance for the WILD names (table `auction_imbalance`) — a NEW feed with no
                    history anywhere we can reach; collected so its value can be measured forward

CLI:  venv/bin/python market_state.py --auto            launchd entry point; picks the snapshot by the clock
      venv/bin/python market_state.py --close [--date YYYY-MM-DD] [--no-telegram]
      venv/bin/python market_state.py --open | --auction
      venv/bin/python market_state.py --backfill 2018-01-01
      venv/bin/python market_state.py --status
"""
from __future__ import annotations
import argparse, datetime as dt, json, math, os, sqlite3, sys
import numpy as np
import pandas as pd
import requests

ROOT = '/Users/sushil/trading'
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, 'trades.db')
DAILY = os.path.join(ROOT, 'factory', 'cache', 'night_owl', 'daily.parquet')
PERSONALITY = os.path.join(ROOT, 'factory', 'cache', 'personality.csv')
BRIDGE = 'http://localhost:8000'
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo('America/New_York')
except Exception:                                   # pragma: no cover
    ET = None

FEAR_VIX_JUMP = 0.05          # VIX close-to-close
FEAR_MKT_DROP = -0.005        # equal-weight universe close-to-close
STORM_BREADTH, SUNNY_BREADTH = 0.40, 0.60          # descriptive weather label only
PANIC_GAP, PANIC_VIX_TERM, PANIC_WILD5 = -0.005, 0.901, -0.0107
REBOUND_N = 10
MIN_PRICE, MIN_DV = 5.0, 20e6                       # identical to the Basket Tide's eligibility
XASSET = {'vix': '^VIX', 'vix3m': '^VIX3M', 'tnx': '^TNX', 'spy': 'SPY', 'qqq': 'QQQ', 'iwm': 'IWM', 'smh': 'SMH',
          'arkk': 'ARKK', 'hyg': 'HYG', 'tlt': 'TLT', 'uup': 'UUP', 'btc': 'BTC-USD'}
US_HOLIDAYS = {'2026-11-26', '2026-12-25', '2027-01-01', '2027-01-18', '2027-02-15', '2027-03-26', '2027-05-31',
               '2027-06-18', '2027-07-05', '2027-09-06', '2027-11-25', '2027-12-24'}

CLOSE_COLS = ['n_elig', 'n_wild', 'mkt_ret', 'breadth_up', 'wild_ret', 'wild_on', 'wild_id', 'semi_ret', 'wild_r5',
              'wild_r20', 'tide_ratio', 'eff10', 'disp', 'vix', 'vix_chg', 'vix_term', 'tnx', 'tnx_chg', 'spy_ret',
              'qqq_ret', 'iwm_ret', 'smh_ret', 'arkk_ret', 'hyg_ret', 'tlt_ret', 'uup_ret', 'btc_ret', 'weather',
              'fear', 'rebound_basket', 'stand_aside_shadow']
AM_COLS = ['am_ts', 'am_es_gap', 'am_nq_gap', 'am_rty_gap', 'am_label']
OUT_COLS = ['next_on_wild', 'next_id_wild', 'rebound_official', 'rebound_0940', 'books_ref_pnl']


def now_et() -> dt.datetime:
    return dt.datetime.now(ET) if ET else dt.datetime.now()


def log(msg: str):
    print(f"[{now_et().strftime('%Y-%m-%d %H:%M:%S')}] [MARKET STATE] {msg}", flush=True)


def is_market_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in US_HOLIDAYS


def send_telegram(msg: str):
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, '.env'))
        token, chat = os.getenv('TELEGRAM_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
        if token and chat:
            requests.post(f'https://api.telegram.org/bot{token}/sendMessage', json={'chat_id': chat, 'text': msg}, timeout=10)
    except Exception as e:
        log(f'telegram send failed: {e}')


# ─────────────────────────── DB ───────────────────────────
def init_db():
    c = sqlite3.connect(DB)
    try:
        cols = ', '.join(f'{k} {"TEXT" if k in ("weather", "rebound_basket", "am_ts", "am_label") else "REAL"}'
                         for k in CLOSE_COLS + AM_COLS + OUT_COLS)
        c.execute(f'CREATE TABLE IF NOT EXISTS market_state (date TEXT PRIMARY KEY, created_at TEXT, {cols})')
        have = {r[1] for r in c.execute('PRAGMA table_info(market_state)')}
        for k in CLOSE_COLS + AM_COLS + OUT_COLS:                     # idempotent: new columns over time
            if k not in have:
                c.execute(f'ALTER TABLE market_state ADD COLUMN {k} {"TEXT" if k in ("weather", "rebound_basket", "am_ts", "am_label") else "REAL"}')
        c.execute("""CREATE TABLE IF NOT EXISTS auction_imbalance (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT,
                     ts TEXT, symbol TEXT, auction_volume REAL, auction_price REAL, auction_imbalance REAL,
                     regulatory_imbalance REAL, last REAL, prev_close REAL)""")
        c.execute('CREATE INDEX IF NOT EXISTS ix_ai_date ON auction_imbalance(date)')
        c.commit()
    finally:
        c.close()


def _clean(v):
    if v is None:
        return None
    if isinstance(v, (np.floating, float)) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    return v


def upsert(date: str, values: dict):
    """Insert or update ONLY the given columns — a close run never wipes the morning snapshot or outcomes."""
    if not values:
        return
    keys = list(values)
    c = sqlite3.connect(DB, timeout=30)
    try:
        c.execute('INSERT OR IGNORE INTO market_state (date, created_at) VALUES (?, ?)',
                  (date, now_et().strftime('%Y-%m-%d %H:%M:%S')))
        c.execute(f'UPDATE market_state SET {", ".join(k + "=?" for k in keys)} WHERE date=?',
                  [_clean(values[k]) for k in keys] + [date])
        c.commit()
    finally:
        c.close()


def rows(where: str = '1=1', params=()) -> pd.DataFrame:
    c = sqlite3.connect(DB)
    try:
        return pd.read_sql(f'SELECT * FROM market_state WHERE {where} ORDER BY date', c, params=params)
    finally:
        c.close()


# ─────────────────────────── computation ───────────────────────────
def sector_map() -> dict:
    from factory.features_daily import _at_constants
    return _at_constants()['SECTOR_MAP']


def panel(daily: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    """Per session: universe/WILD/semis state from the Night Owl daily cache (Basket Tide definitions).
    Returns (frame, extras) where extras holds per-date WILD returns for the rebound basket."""
    d = pd.read_parquet(DAILY) if daily is None else daily
    d = d[(d['open'] > 0) & (d['close'] > 0)].copy()
    d['date'] = pd.to_datetime(d['date'])
    O = d.pivot(index='date', columns='symbol', values='open').sort_index()
    C = d.pivot(index='date', columns='symbol', values='close').reindex(O.index)
    V = d.pivot(index='date', columns='symbol', values='volume').reindex(O.index)
    pc = C.shift(1)
    cc, on, idr = C / pc - 1, O / pc - 1, C / O - 1
    bad = (cc.abs() > 0.6) | (on.abs() > 0.5) | (idr.abs() > 0.5)          # split / data artefacts
    cc, on, idr = cc.mask(bad), on.mask(bad), idr.mask(bad)
    vol60 = cc.rolling(60, min_periods=40).std().shift(1)
    dv20 = (C * V).rolling(20, min_periods=10).median().shift(1)
    elig = (pc >= MIN_PRICE) & (dv20 >= MIN_DV) & cc.notna()
    wild = elig & (vol60.where(elig).rank(axis=1, pct=True) >= 2 / 3)
    sm = sector_map()
    semi = elig & pd.DataFrame(np.broadcast_to([sm.get(s) == 'SEMIS' for s in C.columns], C.shape), index=C.index, columns=C.columns)
    f = pd.DataFrame(index=C.index)
    f['n_elig'], f['n_wild'] = elig.sum(axis=1), wild.sum(axis=1)
    f['mkt_ret'] = cc.where(elig).mean(axis=1)
    f['breadth_up'] = (cc > 0).where(elig).mean(axis=1)
    f['wild_ret'], f['wild_on'], f['wild_id'] = cc.where(wild).mean(axis=1), on.where(wild).mean(axis=1), idr.where(wild).mean(axis=1)
    f['semi_ret'] = cc.where(semi).mean(axis=1)
    ix = (1 + f['wild_ret'].fillna(0)).cumprod()
    f['wild_r5'], f['wild_r20'] = ix / ix.shift(5) - 1, ix / ix.shift(20) - 1
    try:                                       # the LIVE Basket Tide's own series, so the record always matches it
        from factory.live import basket_tide
        bt = basket_tide.series(d)
        f['tide_ratio'] = (bt['index'] / bt['ma'] - 1).reindex(f.index)
    except Exception:
        f['tide_ratio'] = ix / ix.rolling(200, min_periods=200).mean() - 1
    m = f['mkt_ret']
    f['eff10'] = m.rolling(10).sum().abs() / m.abs().rolling(10).sum()
    f['disp'] = cc.where(elig).std(axis=1)
    # next-session outcomes (NaN until the next session exists)
    f['next_on_wild'] = f['wild_on'].shift(-1)
    f['next_id_wild'] = f['wild_id'].shift(-1)
    return f, {'cc': cc, 'idr': idr, 'wild': wild}


def xasset(start: str) -> pd.DataFrame:
    """Daily closes for VIX/VIX3M/10-yr and the sector/cross-asset feeds — ONE batched request."""
    import yfinance as yf
    x = yf.download(list(XASSET.values()), start=start, progress=False, auto_adjust=True, threads=True)['Close']
    x = x.rename(columns={v: k for k, v in XASSET.items()})
    x.index = pd.to_datetime(x.index).tz_localize(None).normalize()
    return x


def close_values(day: pd.Timestamp, f: pd.DataFrame, ex: dict, xa: pd.DataFrame) -> dict:
    r = f.loc[day]
    v = {k: r[k] for k in ['n_elig', 'n_wild', 'mkt_ret', 'breadth_up', 'wild_ret', 'wild_on', 'wild_id', 'semi_ret',
                           'wild_r5', 'wild_r20', 'tide_ratio', 'eff10', 'disp']}
    # market days only — bitcoin trades on weekends, which would make Monday's "previous close" a Sunday (no VIX)
    xa = xa[(xa.index <= day)].dropna(subset=['vix'])
    if len(xa) >= 2 and xa.index[-1] == day:
        last, prev = xa.iloc[-1], xa.iloc[-2]
        v['vix'], v['vix_chg'] = last.get('vix'), last.get('vix') / prev.get('vix') - 1
        v['vix_term'] = last.get('vix') / last.get('vix3m')
        v['tnx'], v['tnx_chg'] = last.get('tnx'), last.get('tnx') - prev.get('tnx')
        for k in ('spy', 'qqq', 'iwm', 'smh', 'arkk', 'hyg', 'tlt', 'uup', 'btc'):
            v[f'{k}_ret'] = last.get(k) / prev.get(k) - 1
    mkt, br, vj = v['mkt_ret'], v['breadth_up'], v.get('vix_chg')
    v['weather'] = ('STORMY' if (mkt <= FEAR_MKT_DROP and br < STORM_BREADTH) else
                    'SUNNY' if (mkt >= -FEAR_MKT_DROP and br > SUNNY_BREADTH) else 'CLOUDY')
    fear = vj is not None and not pd.isna(vj) and vj >= FEAR_VIX_JUMP and mkt <= FEAR_MKT_DROP
    v['fear'] = 1 if fear else 0
    v['stand_aside_shadow'] = 1 if fear else 0
    if fear:
        cc = ex['cc'].loc[day][ex['wild'].loc[day]].dropna()
        v['rebound_basket'] = json.dumps(list(cc.nsmallest(REBOUND_N).index))
    return v


def _bridge_0940_to_close(symbols: list[str], day: str) -> float | None:
    """Equal-weight 09:40 → 16:00 return from bridge 5-min bars (raw, one source for both ends)."""
    rets = []
    for s in symbols:
        try:
            r = requests.get(f'{BRIDGE}/history/{s}', params={'duration': '5 D', 'bar_size': '5 mins', 'rth': 'true'}, timeout=10)
            b = pd.DataFrame(r.json())
            b['t'] = pd.to_datetime(b['date'] if 'date' in b else b['time'])
            b = b[b['t'].dt.strftime('%Y-%m-%d') == day]
            hm = b['t'].dt.strftime('%H:%M')
            p0, p1 = b.loc[hm == '09:35', 'close'], b.loc[hm == '15:55', 'close']   # 09:35 bar close = 09:40 price
            if len(p0) and len(p1):
                rets.append(float(p1.iloc[-1]) / float(p0.iloc[-1]) - 1)
        except Exception:
            continue
    return float(np.mean(rets)) if len(rets) >= max(3, len(symbols) // 2) else None


def fill_outcomes(f: pd.DataFrame, ex: dict, use_bridge: bool = True):
    """Fill next-session outcomes for rows that can now be scored."""
    st = rows('next_on_wild IS NULL OR next_id_wild IS NULL OR (fear=1 AND (rebound_official IS NULL OR rebound_0940 IS NULL)) '
              'OR (fear=1 AND books_ref_pnl IS NULL)')
    if st.empty:
        return
    dates = list(f.index)
    con = sqlite3.connect(DB)
    try:
        for _, r in st.iterrows():
            t = pd.Timestamp(r['date'])
            if t not in f.index:
                continue
            i = dates.index(t)
            if i + 1 >= len(dates):
                continue
            n = dates[i + 1]
            vals = {'next_on_wild': f.at[t, 'next_on_wild'], 'next_id_wild': f.at[t, 'next_id_wild']}
            if r.get('fear') == 1 and r.get('rebound_basket'):
                basket = json.loads(r['rebound_basket'])
                vals['rebound_official'] = ex['idr'].loc[n, [s for s in basket if s in ex['idr'].columns]].mean()
                if use_bridge and pd.isna(r.get('rebound_0940')) and (now_et().date() - n.date()).days <= 3:
                    vals['rebound_0940'] = _bridge_0940_to_close(basket, n.strftime('%Y-%m-%d'))
            if r.get('fear') == 1:
                q = con.execute("""SELECT SUM(ref_pnl), COUNT(*), SUM(ref_pnl IS NULL) FROM (
                       SELECT ref_pnl FROM overnight_trades WHERE mode='LIVE' AND entry_date=?
                       UNION ALL SELECT ref_pnl FROM night_owl_trades WHERE mode='LIVE' AND entry_date=?)""",
                                (r['date'], r['date'])).fetchone()
                if q and q[1] and not q[2]:
                    vals['books_ref_pnl'] = q[0]
            upsert(r['date'], {k: v for k, v in vals.items() if v is not None and not (isinstance(v, float) and math.isnan(v))})
    finally:
        con.close()


# ─────────────────────────── snapshots ───────────────────────────
def close_snapshot(day: dt.date | None = None, telegram: bool = True) -> bool:
    day = day or now_et().date()
    f, ex = panel()
    t = pd.Timestamp(day)
    if t not in f.index or pd.isna(f.at[t, 'mkt_ret']):
        log(f'no completed daily bar for {day} in the cache yet — nothing recorded (night_owl_prep refreshes at 17:30)')
        return False
    xa = xasset((t - pd.Timedelta(days=15)).strftime('%Y-%m-%d'))
    v = close_values(t, f, ex, xa)
    upsert(day.isoformat(), v)
    fill_outcomes(f, ex)
    log(f"{day} {v['weather']} universe {v['mkt_ret']*100:+.2f}% breadth {v['breadth_up']:.0%} WILD {v['wild_ret']*100:+.2f}% "
        f"VIX {v.get('vix', float('nan')):.2f} ({(v.get('vix_chg') or float('nan'))*100:+.1f}%) FEAR={v['fear']}")
    if telegram:
        vix = v.get('vix'); vc = v.get('vix_chg')
        msg = (f"🌦 Market state {day:%a %b %d}: {v['weather']} — universe {v['mkt_ret']*100:+.1f}%, "
               f"{v['breadth_up']:.0%} of names up, volatile basket {v['wild_ret']*100:+.1f}%"
               + (f" | VIX {vix:.1f} ({vc*100:+.1f}%)" if vix is not None and vc is not None else '')
               + f" | Basket Tide {v['tide_ratio']*100:+.1f}% vs 200d")
        if v['fear']:
            msg += (f"\n⚡ FEAR day (VIX up ≥{FEAR_VIX_JUMP:.0%}, universe down ≥{-FEAR_MKT_DROP:.1%}). Shadow only — no orders: "
                    f"tomorrow's Fear Rebound basket {', '.join(json.loads(v['rebound_basket']))}; "
                    f"the overnight books would stand aside tonight.")
            if v.get('uup_ret') is not None:
                msg += f" Dollar {v['uup_ret']*100:+.2f}% (fear with a falling dollar rebounded best; being tracked)."
        send_telegram(msg)
    return True


def _futures_gap() -> dict:
    """Overnight move of ES/NQ/RTY futures from the prior session's 15:55 bar close to the latest price."""
    import yfinance as yf
    out = {}
    for k, tk in (('es', 'ES=F'), ('nq', 'NQ=F'), ('rty', 'RTY=F')):
        try:
            h = yf.Ticker(tk).history(period='3d', interval='5m')
            h.index = h.index.tz_convert('America/New_York')
            today = now_et().date()
            prev = h[(h.index.date < today) & (h.index.strftime('%H:%M') == '15:55')]
            if len(prev) and len(h):
                out[k] = float(h['Close'].iloc[-1]) / float(prev['Close'].iloc[-1]) - 1
        except Exception as e:
            log(f'futures gap {tk} failed: {e}')
    return out


def morning_snapshot(telegram: bool = True):
    day = now_et().date()
    g = _futures_gap()
    if 'rty' not in g:
        log('no Russell futures quote — morning snapshot skipped'); return
    prev = rows('date < ?', (day.isoformat(),)).tail(1)
    term = prev['vix_term'].iloc[0] if len(prev) else None
    w5 = prev['wild_r5'].iloc[0] if len(prev) else None
    rty = g['rty']
    if rty <= PANIC_GAP:
        panic = term is not None and w5 is not None and term >= PANIC_VIX_TERM and w5 <= PANIC_WILD5
        label = 'PANIC_GAPDOWN' if panic else 'CALM_GAPDOWN'
    elif rty >= -PANIC_GAP:
        label = 'GAPUP'
    else:
        label = 'NORMAL'
    upsert(day.isoformat(), {'am_ts': now_et().strftime('%H:%M:%S'), 'am_es_gap': g.get('es'), 'am_nq_gap': g.get('nq'),
                             'am_rty_gap': rty, 'am_label': label})
    log(f'morning {label}: RTY {rty*100:+.2f}% NQ {g.get("nq", float("nan"))*100:+.2f}% ES {g.get("es", float("nan"))*100:+.2f}%')
    if telegram and label.endswith('GAPDOWN'):
        what = ('PANIC gap-down — in the backtests the overnight positions did better held to the close (+1.8%); '
                'shadow only, the books still sell at the open' if label == 'PANIC_GAPDOWN' else
                'CALM gap-down — selling at the open is the better choice here (no rebound expected)')
        send_telegram(f"🌅 {day:%a %b %d} 09:26 — Russell futures {rty*100:+.1f}% since yesterday's close: {what}.")


def auction_probe():
    """Closing-auction imbalance for the WILD names via a SEPARATE read-only IBKR connection (clientId 77 — the bridge
    uses 10-19), so the shared bridge is never touched. Logs what arrives; records nothing if the account has no feed."""
    from ib_async import IB, Stock
    day = now_et().date().isoformat()
    pe = pd.read_csv(PERSONALITY)
    syms = sorted(set(pe[pe['cluster'] == 'WILD']['symbol']))
    c = sqlite3.connect(DB)
    try:
        for t in ('overnight_trades', 'night_owl_trades'):
            syms += [r[0] for r in c.execute(f"SELECT symbol FROM {t} WHERE entry_date=?", (day,))]
    finally:
        c.close()
    syms = sorted(set(syms))
    ib = IB()
    try:
        ib.connect('127.0.0.1', 4002, clientId=77, timeout=15, readonly=True)
    except Exception as e:
        log(f'auction probe: cannot connect to the gateway ({e})'); return
    got, out = 0, []
    try:
        ib.reqMarketDataType(1)
        cons = [Stock(s, 'SMART', 'USD') for s in syms]
        cons = [k for k in ib.qualifyContracts(*cons) if k.conId]
        for i in range(0, len(cons), 35):
            batch = cons[i:i + 35]
            tks = [ib.reqMktData(k, genericTickList='225', snapshot=False) for k in batch]
            ib.sleep(6)
            ts = now_et().strftime('%H:%M:%S')
            for k, tk in zip(batch, tks):
                vals = [_clean(x) for x in (tk.auctionVolume, tk.auctionPrice, tk.auctionImbalance, tk.regulatoryImbalance, tk.last, tk.close)]
                if any(x is not None for x in vals[:4]):
                    got += 1
                out.append((day, ts, k.symbol, *vals))
            for k in batch:
                ib.cancelMktData(k)
    finally:
        ib.disconnect()
    c = sqlite3.connect(DB, timeout=30)
    try:
        c.executemany("""INSERT INTO auction_imbalance(date, ts, symbol, auction_volume, auction_price, auction_imbalance,
                         regulatory_imbalance, last, prev_close) VALUES (?,?,?,?,?,?,?,?,?)""", out)
        c.commit()
    finally:
        c.close()
    log(f'auction probe: {len(out)} names queried, {got} returned auction fields'
        + ('' if got else ' — this account may not receive closing-auction data (check the IBKR market-data subscription)'))


# ─────────────────────────── backfill ───────────────────────────
def _futures_gaps_history() -> pd.DataFrame:
    """09:20-bar close vs the prior session's 15:55 close for ES/MNQ/RTY (futures_bars_5m, 2021+)."""
    con = sqlite3.connect(os.path.join(ROOT, 'market_data.db'))
    out = {}
    try:
        for sym, k in (('ES', 'es'), ('MNQ', 'nq'), ('RTY', 'rty')):
            x = pd.read_sql("SELECT ts_utc, close FROM futures_bars_5m WHERE symbol=?", con, params=(sym,))
            x['ts'] = pd.to_datetime(x.ts_utc.str.replace('T', ' ').str[:19]).dt.tz_localize('UTC').dt.tz_convert('America/New_York').dt.tz_localize(None)
            x = x.drop_duplicates('ts', keep='last').set_index('ts').sort_index()
            hm = x.index.strftime('%H:%M'); day = x.index.normalize()
            c1555 = x['close'][hm == '15:55'].groupby(day[hm == '15:55']).last()
            c0920 = x['close'][hm == '09:20'].groupby(day[hm == '09:20']).last()
            out[k] = c0920 / c1555.shift(1).reindex(c0920.index, method='ffill') - 1
    finally:
        con.close()
    return pd.DataFrame(out)


def backfill(start: str):
    init_db()
    f, ex = panel()
    xa = xasset((pd.Timestamp(start) - pd.Timedelta(days=30)).strftime('%Y-%m-%d'))
    fg = _futures_gaps_history()
    days = [t for t in f.index if t >= pd.Timestamp(start) and not pd.isna(f.at[t, 'mkt_ret'])]
    prev_term = prev_w5 = None
    for t in days:
        v = close_values(t, f, ex, xa)
        if t in fg.index and not pd.isna(fg.at[t, 'rty']):
            rty = fg.at[t, 'rty']
            if rty <= PANIC_GAP:
                lab = 'PANIC_GAPDOWN' if (prev_term is not None and prev_w5 is not None and prev_term >= PANIC_VIX_TERM and prev_w5 <= PANIC_WILD5) else 'CALM_GAPDOWN'
            else:
                lab = 'GAPUP' if rty >= -PANIC_GAP else 'NORMAL'
            v.update({'am_ts': 'backfill', 'am_es_gap': fg.at[t, 'es'], 'am_nq_gap': fg.at[t, 'nq'], 'am_rty_gap': rty, 'am_label': lab})
        prev_term, prev_w5 = v.get('vix_term'), v.get('wild_r5')
        upsert(t.strftime('%Y-%m-%d'), v)
    fill_outcomes(f, ex, use_bridge=False)
    log(f'backfilled {len(days)} sessions from {start}')


def status(n: int = 12):
    r = rows().tail(n)
    show = ['date', 'weather', 'fear', 'mkt_ret', 'breadth_up', 'wild_ret', 'vix', 'vix_chg', 'tide_ratio', 'am_label',
            'next_on_wild', 'next_id_wild', 'rebound_official', 'rebound_0940', 'books_ref_pnl']
    with pd.option_context('display.width', 220, 'display.max_columns', 30):
        print(r[[c for c in show if c in r]].round(4).to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--auto', action='store_true'); ap.add_argument('--close', action='store_true')
    ap.add_argument('--open', action='store_true'); ap.add_argument('--auction', action='store_true')
    ap.add_argument('--backfill'); ap.add_argument('--status', action='store_true')
    ap.add_argument('--date'); ap.add_argument('--no-telegram', action='store_true')
    a = ap.parse_args()
    init_db()
    tg = not a.no_telegram
    if a.backfill:
        backfill(a.backfill); return
    if a.status:
        status(); return
    n = now_et()
    if not is_market_day(n.date()) and not a.date:
        log('market closed today — nothing to record'); return
    if a.close or a.date:
        close_snapshot(dt.date.fromisoformat(a.date) if a.date else None, telegram=tg); return
    if a.open:
        morning_snapshot(telegram=tg); return
    if a.auction:
        auction_probe(); return
    if a.auto:
        t = n.time()
        if dt.time(9, 15) <= t <= dt.time(9, 29):
            morning_snapshot(telegram=tg)
        elif dt.time(15, 50) <= t <= dt.time(15, 58):
            auction_probe()
        elif t >= dt.time(17, 50):
            close_snapshot(telegram=tg)
        else:
            log(f'--auto at {t:%H:%M}: outside the 09:15-09:29 / 15:50-15:58 / 17:50+ windows — nothing to do')


if __name__ == '__main__':
    main()
