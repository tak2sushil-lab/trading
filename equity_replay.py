#!/usr/bin/env python
"""
equity_replay.py — bar-level equity replay that calls LIVE auto_trader functions.
Built Jul 18 2026 (weekend redesign, user-approved). Replaces stale sim_today.py as
the equity validator (Constitution Art. 4: sim must match the machine).

How it works (same pattern as the futures FakeDatetime replays):
  - Freezes auto_trader's clock per 5-min bar (FakeDatetime/FakeDate monkey-patch)
  - Serves stored bars (market_data.db bars_5m + a yfinance daily/SPY cache) through
    auto_trader's own fetch points (yf.Ticker, get_ib_daily, _bridge_df, get_live_price)
  - Decision chain is 100%% LIVE CODE: get_regime → get_intraday_signals → grade_setup
    → _check_layer2_fitness → book_is_on → get_position_capital
  - Exit engine replays the live stack: 5%% stop, -$150 breaker, L3 T+5 probation
    (hard-fail/flat/confirm — mirrors monitor_open_trades), partial at +5%%, BE +2.5%%,
    VWAP cross, no-move timer (240/300 DNA), ATR trail (1.0×/1.5× DNA), PCT trail,
    5m-bar trail at +3%%, EOD 15:45.

v1 known gaps (documented, not hidden): no catalyst flag (is_catalyst=False — catalyst
override/sympathy/pre-market modules not replayed), earnings distance stubbed to 999
(live had real calendar), sector_strength/key_levels empty (neutral scoring), afternoon/
recycled-slot gates not enforced, momentum-fade + regime-flip exits skipped.
Use --parity DATE to quantify decision divergence vs that day's live scan_log.

Usage:
  venv/bin/python equity_replay.py --start 2026-07-06 --end 2026-07-17
  venv/bin/python equity_replay.py --parity 2026-07-17
  venv/bin/python equity_replay.py --start ... --end ... --no-book-health   # A/B
"""
import argparse, os, sqlite3, sys, warnings
import datetime as _dt
import numpy as np
import pandas as pd
import pytz
import yfinance as _real_yf

warnings.filterwarnings('ignore')
ET   = pytz.timezone('America/New_York')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import auto_trader as at
from collect_bars import load_bars

# ── Frozen clock ─────────────────────────────────────────────────────────────
class FakeDatetime(_dt.datetime):
    _now = None
    @classmethod
    def now(cls, tz=None):
        n = cls._now
        return n if tz else n.replace(tzinfo=None)

class FakeDate(_dt.date):
    @classmethod
    def today(cls):
        return FakeDatetime._now.date()

def set_now(ts):
    FakeDatetime._now = ts if ts.tzinfo else ET.localize(ts)

at.datetime = FakeDatetime
at.date     = FakeDate

# ── Data layer ───────────────────────────────────────────────────────────────
_bars5, _daily = {}, {}

def preload(symbols, start, end):
    """5-min bars from market_data.db; daily bars via one yfinance batch call."""
    pad = (pd.Timestamp(start) - pd.Timedelta(days=10)).strftime('%Y-%m-%d')
    for s in symbols:
        try:
            df = load_bars(s, start=pad, end=end)
            if df is not None and len(df):
                df = df.rename(columns={c: c.capitalize() for c in df.columns})
                _bars5[s] = df
        except Exception:
            pass
    need_daily = list(_bars5) + ['SPY', 'QQQ']
    dl = _real_yf.download(need_daily, period='6mo', interval='1d',
                           group_by='ticker', auto_adjust=False,
                           threads=True, progress=False)
    for s in need_daily:
        try:
            d = dl[s].dropna()
            if len(d):
                _daily[s] = d
        except Exception:
            pass
    # SPY/QQQ 5-min via yfinance (only 60d available — fine for recent windows)
    for s in ('SPY', 'QQQ'):
        try:
            d = _real_yf.Ticker(s).history(period='60d', interval='5m')
            if len(d):
                d.index = d.index.tz_convert(ET)
                _bars5[s] = d
        except Exception:
            pass

def bars5_upto(sym, days=5):
    df = _bars5.get(sym)
    if df is None:
        return pd.DataFrame()
    now = FakeDatetime._now
    df = df[df.index <= now]
    cutoff = now - pd.Timedelta(days=days)
    return df[df.index >= cutoff]

def daily_upto(sym):
    """Daily bars up to sim date, with a synthetic today-so-far row from 5m bars
    (mirrors IB daily, whose last row is today's partial bar)."""
    d = _daily.get(sym)
    if d is None:
        return pd.DataFrame()
    today = FakeDatetime._now.date()
    hist = d[d.index.date < today]
    intra = bars5_upto(sym, days=1)
    intra = intra[intra.index.date == today]
    if len(intra):
        row = pd.DataFrame({'Open': [float(intra['Open'].iloc[0])],
                            'High': [float(intra['High'].max())],
                            'Low':  [float(intra['Low'].min())],
                            'Close': [float(intra['Close'].iloc[-1])],
                            'Volume': [float(intra['Volume'].sum())]},
                           index=[pd.Timestamp(today)])
        hist = pd.concat([hist, row])
    return hist

# ── Patch auto_trader's fetch points ─────────────────────────────────────────
class _FakeTicker:
    def __init__(self, sym): self.sym = sym
    def history(self, period='5d', interval='5m', **kw):
        if 'm' in interval:
            return bars5_upto(self.sym, days=int(period.rstrip('d') or 5))
        return daily_upto(self.sym)

class _FakeYF:
    Ticker = _FakeTicker

at.yf             = _FakeYF
at.get_ib_daily   = lambda symbol, duration='60 D': daily_upto(symbol)
at.get_ib_intraday = lambda symbol, duration='5 D', bar_size='5 mins': bars5_upto(symbol)
at._bridge_df     = lambda symbol, duration='1 D', bar_size='5 mins': bars5_upto(symbol, days=1)
at.get_live_price = lambda symbol: (float(bars5_upto(symbol, 1)['Close'].iloc[-1])
                                    if len(bars5_upto(symbol, 1)) else None)
at.get_days_to_earnings = lambda symbol: 999           # v1 stub — see header
at.send_telegram  = lambda *a, **k: None
at.send_telegram_to = lambda *a, **k: None
at.speak          = lambda *a, **k: None
at._chart_alignment_check = lambda *a, **k: (True, 'replay')   # never call the LLM
_quiet = [True]
_orig_log = at.log
at.log = lambda m: (None if _quiet[0] else _orig_log(m))

# ── Replay engine ────────────────────────────────────────────────────────────
def spy_chg_now():
    d = _daily.get('SPY')
    intra = bars5_upto('SPY', 1)
    if d is None or not len(intra):
        return 0.0
    today = FakeDatetime._now.date()
    prev = d[d.index.date < today]['Close']
    if not len(prev):
        return 0.0
    return (float(intra['Close'].iloc[-1]) - float(prev.iloc[-1])) / float(prev.iloc[-1]) * 100

def replay_day(day, use_book_health=True, parity_rows=None):
    trades, open_tr = [], []
    day_ts = pd.Timestamp(day)
    universe = [s for s in at.FULL_UNIVERSE if s in _bars5]
    at._book_health_cache = {'date': None, 'LONG': None, 'SHORT': None}
    daily_count = 0
    traded = set()

    times = pd.date_range(f'{day} 09:35', f'{day} 15:55', freq='5min', tz=ET)
    for ts in times:
        set_now(ts.to_pydatetime())
        t = ts.time()

        # ── monitor open trades every bar ────────────────────────────────
        for tr in list(open_tr):
            b = bars5_upto(tr['sym'], 1)
            b = b[b.index.date == day_ts.date()]
            if not len(b):
                continue
            price = float(b['Close'].iloc[-1])
            tr['peak'] = max(tr['peak'], price)
            tr['bars'] += 1
            pnl_pct = (price - tr['entry']) / tr['entry'] * 100
            pnl_usd = (price - tr['entry']) * tr['shares']
            reason = None
            # L3 probation (T+5 ≈ 1 bar after entry bar)
            if tr['bars'] == 1:
                if pnl_pct < -2.0:
                    tr['sl'] = tr['entry']; tr['l3'] = 'HARD_FAIL'
                elif pnl_pct < 0.5:
                    tr['sl'] = max(tr['sl'], tr['entry']); tr['l3'] = 'FLAT'
                else:
                    tr['l3'] = 'CONFIRM'
            if price <= tr['sl']:
                reason = 'stop'
            elif pnl_usd <= -at.MAX_LOSS_PER_TRADE:
                reason = 'circuit_breaker'
            if reason is None and not tr['partial'] and pnl_pct >= 5.0:
                pnl_half = (price - tr['entry']) * (tr['shares'] // 2 or 1)
                tr['locked'] += pnl_half
                tr['shares'] -= (tr['shares'] // 2 or 1)
                tr['partial'] = True
            if reason is None and pnl_pct >= 2.5:
                tr['sl'] = max(tr['sl'], tr['entry'])
            vwap_b = b[b['Close'].notna()]
            vwap = float((vwap_b['Close'] * vwap_b['Volume']).sum() /
                         max(vwap_b['Volume'].sum(), 1))
            if reason is None and pnl_pct > 0.5 and price < vwap:
                reason = 'vwap_cross'
            dna = at.get_dna_cluster(tr['sym'])
            if reason is None:
                nm_min = 300 if dna == 'INSTITUTIONAL' else 240
                if tr['bars'] * 5 >= nm_min and -0.3 <= pnl_pct <= 2.0:
                    reason = 'no_move'
            atr = tr['atr']
            trail_mult = 1.0 if dna == 'HIGH_VOL' else 1.5
            if reason is None and atr and (tr['peak'] - tr['entry']) >= atr:
                tsl = tr['peak'] - trail_mult * atr
                tr['sl'] = max(tr['sl'], tsl)
                if price <= tr['sl']:
                    reason = 'atr_trail'
            if reason is None and (tr['peak'] - tr['entry']) / tr['entry'] * 100 >= 1.5:
                tsl = tr['peak'] * (1 - 0.005)
                tr['sl'] = max(tr['sl'], tsl)
                if price <= tr['sl']:
                    reason = 'pct_trail'
            if reason is None and pnl_pct >= 3.0 and len(b) >= 3:
                two_low = float(b['Low'].iloc[-3:-1].min())
                tr['sl'] = max(tr['sl'], two_low)
                if price <= tr['sl']:
                    reason = 'bar_trail'
            if reason is None and t >= _dt.time(15, 45):
                reason = 'eod'
            if reason:
                pnl = (price - tr['entry']) * tr['shares'] + tr['locked']
                trades.append({**tr, 'exit': price, 'reason': reason, 'pnl': pnl,
                               'exit_time': str(t)[:5]})
                open_tr.remove(tr)

        # ── entries at live cadence, live gates ──────────────────────────
        if not (at.is_entry_window() if hasattr(at, 'is_entry_window') else True):
            continue
        if t < _dt.time(10, 0) or t >= _dt.time(15, 0):
            continue
        if len(open_tr) >= at.MAX_OPEN_TRADES or daily_count >= 20:
            continue
        regime = at.get_regime()
        if use_book_health and not at.book_is_on('LONG'):
            continue
        schg = spy_chg_now()
        for sym in universe:
            if sym in traded or len(open_tr) >= at.MAX_OPEN_TRADES:
                continue
            b = bars5_upto(sym, 1)
            if not len(b) or b.index[-1].date() != day_ts.date():
                continue
            sig = at.get_intraday_signals(sym, spy_chg=schg)
            if not sig:
                continue
            price = sig['price']
            sl, target, _risk, _reward, rr = at.calc_sl_target(sym, price, side='LONG')
            grade, reasons, score = at.grade_setup(sig, regime, sl, target, price,
                                                   rr, symbol=sym, is_catalyst=False)
            if parity_rows is not None:
                parity_rows.append({'ts': str(t)[:5], 'symbol': sym, 'grade': grade,
                                    'score': score, 'reason': reasons[0] if reasons else ''})
            if grade not in ('A+', 'A'):
                continue
            l2 = at._check_layer2_fitness(sym, 'LONG', price, is_catalyst=False)
            if isinstance(l2, tuple):
                l2_ok, l2_half = l2[0], (len(l2) > 2 and l2[2] == 'HALF')
            else:
                l2_ok, l2_half = bool(l2), False
            if not l2_ok:
                continue
            cap = at.get_position_capital(grade, False, sum(x['entry'] * x['shares'] for x in open_tr))
            if l2_half:
                cap *= 0.5
            shares = int(cap / price)
            if shares < 1:
                continue
            atr_v = None
            try:
                dl = daily_upto(sym)
                tr_ = pd.concat([dl['High'] - dl['Low'],
                                 (dl['High'] - dl['Close'].shift()).abs(),
                                 (dl['Low'] - dl['Close'].shift()).abs()], axis=1).max(axis=1)
                atr_v = float(tr_.rolling(14).mean().iloc[-1])
            except Exception:
                pass
            open_tr.append({'sym': sym, 'entry': price, 'shares': shares, 'sl': sl,
                            'grade': grade, 'score': score, 'time': str(t)[:5],
                            'peak': price, 'bars': 0, 'partial': False, 'locked': 0.0,
                            'atr': atr_v, 'l3': None})
            traded.add(sym)
            daily_count += 1

    for tr in open_tr:   # safety net
        b = bars5_upto(tr['sym'], 1)
        price = float(b['Close'].iloc[-1]) if len(b) else tr['entry']
        trades.append({**tr, 'exit': price, 'reason': 'eod_force',
                       'pnl': (price - tr['entry']) * tr['shares'] + tr['locked'],
                       'exit_time': '16:00'})
    return trades

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start'); ap.add_argument('--end')
    ap.add_argument('--parity', help='decision-parity check vs scan_log for one date')
    ap.add_argument('--no-book-health', action='store_true')
    ap.add_argument('--detail', action='store_true')
    a = ap.parse_args()

    start = a.parity or a.start
    end   = a.parity or a.end
    days  = [d.date() for d in pd.bdate_range(start, end)
             if d.date() not in at.US_HOLIDAYS_2026]
    set_now(ET.localize(_dt.datetime.combine(days[0], _dt.time(9, 35))))
    print(f'preloading bars for {len(at.FULL_UNIVERSE)} symbols…')
    preload(at.FULL_UNIVERSE, str(days[0]), str(days[-1] + _dt.timedelta(days=1)))
    print(f'  {len(_bars5)} symbols with 5-min bars, {len(_daily)} with daily')

    all_trades = []
    parity_rows = [] if a.parity else None
    for d in days:
        trs = replay_day(str(d), use_book_health=not a.no_book_health and not a.parity,
                         parity_rows=parity_rows)
        pnl = sum(t['pnl'] for t in trs)
        print(f'{d}  {len(trs)}t  ${pnl:+,.0f}' + (
            '   ' + ' | '.join(f"{t['sym']} {t['grade']} {t['time']}→{t['exit_time']} "
                               f"${t['pnl']:+.0f} {t['reason']}" for t in trs)
            if a.detail and trs else ''))
        all_trades += trs

    n = len(all_trades)
    wins = sum(1 for t in all_trades if t['pnl'] > 0)
    print('─' * 60)
    print(f'Trades: {n} | WR: {wins}/{n} = {wins / n * 100:.1f}%' if n else 'Trades: 0')
    print(f'Total P&L: ${sum(t["pnl"] for t in all_trades):+,.0f}')

    if a.parity:
        con = sqlite3.connect(os.path.join(ROOT, 'trades.db'))
        live = con.execute(
            "select symbol, grade, count(*) from scan_log where scan_date=? "
            "and direction='LONG' group by 1,2", (a.parity,)).fetchall()
        con.close()
        live_syms = {(r[0], r[1]) for r in live}
        sim_syms  = {(r['symbol'], r['grade']) for r in parity_rows}
        both = live_syms & sim_syms
        print(f'\nPARITY vs scan_log {a.parity} (LONG, symbol+grade pairs):')
        print(f'  live pairs={len(live_syms)}  sim pairs={len(sim_syms)}  overlap={len(both)}')
        print(f'  sim-only: {sorted(sim_syms - live_syms)[:10]}')
        print(f'  live-only: {sorted(live_syms - sim_syms)[:10]}')

if __name__ == '__main__':
    main()
