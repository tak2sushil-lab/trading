#!/usr/bin/env python
"""
equity_replay.py — bar-level equity replay that calls LIVE auto_trader functions.
Built Jul 18 2026. Rebuilt Aug 5 2026 (v2): v1 hand-reimplemented a subset of the
entry/exit logic and only hit ~88% decision parity, with zero coverage of
_scan_regime_adaptive() (shipped Aug 5) at all. v2 instead mirrors run_scan()'s own
routing (auto_trader.py:3215-3427) and calls the REAL orchestration functions
directly under a frozen clock — _scan_regime_adaptive, _scan_and_enter,
_scan_and_enter_bear, _scan_catalyst_override, monitor_open_trades — so the decision
chain is 100% live code end to end, not an approximation of it.

How it works:
  - Freezes auto_trader's (and database.py's, separately) clock per 5-min bar
    (FakeDatetime/FakeDate monkey-patch)
  - Serves stored bars (market_data.db bars_5m, DataBento-backed 2024-01-02+ for
    234/241 universe symbols + SPY/QQQ/IWM/MDY/sector ETFs; VIX has no path to real
    intraday history on any dataset this account can reach, so it's a flat-within-
    day proxy from real yfinance daily closes — vix_val threshold checks work,
    vix_rising never fires, stated permanent v1 gap) through auto_trader's own
    fetch points (yf.Ticker/yf.download, get_ib_daily, _bridge_df, get_live_price)
  - place_trade() replaced by FillSimulator (instant 100% fill, real DB write via
    the real log_trade_entry — no bridge order, no polling)
  - monitor_open_trades()'s bridge calls neutralized (requests shim, empty
    get_ibkr_positions — routes every exit through the real DB-only-close branch)
  - get_daily_pnl() re-implemented against the replay DB with the SIMULATED date
    substituted in (SQLite's date('now') can't be monkeypatched from Python)
  - A dedicated _replay_trades.db (never the real trades.db) via a process-wide
    sqlite3.connect guard — required because several auto_trader.py write sites do
    `import sqlite3` INSIDE the function body, which an attribute-patch can't reach

Known, accepted v1 gaps (documented, not hidden): no catalyst/sympathy/pre-market
scanning (catalyst_priority stays empty all replay — is_catalyst is correctly False
throughout, matching that gap), _check_layer2_fitness fails open (SKIP/HALF-only
gate, makes replay slightly more permissive than live, not a comparison bias),
book_is_on() reads the REAL production scan_log (correct — but BOOK_HEALTH_RESET_DATE
2026-07-22 means it's unconditionally True/cold-start for virtually this whole
2024-2026 window), session_pnl is realized-only (no live unrealized P&L feed).
Use --parity DATE to quantify decision divergence vs that day's live scan_log.

Usage:
  venv/bin/python equity_replay.py --start 2024-01-02 --end 2026-08-04
  venv/bin/python equity_replay.py --parity 2026-08-04
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

# Regime/breadth inputs get_regime() needs that live outside FULL_UNIVERSE — must be
# preloaded regardless of which symbol list the caller passes in.
_REGIME_INPUT_SYMS = ['SPY', 'QQQ'] + sorted(set(at.SECTOR_ETF_MAP.values()))
_VIX_SYM = '^VIX'
# IWM/MDY feed get_regime()'s breadth check (auto_trader.py _bridge_df('IWM'/'MDY', ...)).
_BREADTH_SYMS = ['IWM', 'MDY']

def _synth_5min_from_daily(sym):
    """No real intraday history available (VIX: not on any DataBento dataset this
    account can reach — confirmed via metadata.list_datasets(), no CBOE index feed).
    Flat-within-day proxy from free, unlimited yfinance daily closes: keeps absolute-
    level threshold checks (vix_val > 28 etc) meaningful, but vix_rising (30-min
    trend) can never fire — stated v1 fidelity gap, not hidden."""
    try:
        d = _real_yf.Ticker(sym).history(period='max', interval='1d', auto_adjust=False)
        if not len(d):
            return
        if d.index.tz is None:
            d.index = d.index.tz_localize(ET)
        else:
            d.index = d.index.tz_convert(ET)
        _daily[sym] = d
        rows = []
        for day, close in d['Close'].items():
            day = day.date()
            for t in pd.date_range(f'{day} 09:30', f'{day} 16:00', freq='5min', tz=ET):
                rows.append({'ts': t, 'Open': close, 'High': close, 'Low': close,
                             'Close': close, 'Volume': 0})
        if rows:
            df = pd.DataFrame(rows).set_index('ts')
            _bars5[sym] = df
    except Exception:
        pass

def preload(symbols, start, end):
    """5-min bars from market_data.db; daily bars via one yfinance batch call."""
    pad = (pd.Timestamp(start) - pd.Timedelta(days=10)).strftime('%Y-%m-%d')
    all_syms = list(dict.fromkeys(list(symbols) + _REGIME_INPUT_SYMS + _BREADTH_SYMS))
    for s in all_syms:
        try:
            df = load_bars(s, start=pad, end=end)
            if df is not None and len(df):
                df = df.rename(columns={c: c.capitalize() for c in df.columns})
                _bars5[s] = df
        except Exception:
            pass
    # get_intraday_signals() requires >=20 daily bars BEFORE the simulated date
    # (auto_trader.py:997: `len(df1d) < 20 -> return None`). yfinance's `period=`
    # is relative to the REAL wall clock, not the replay window — for any replay
    # date more than a few months in the past, a period='6mo' fetch silently
    # returns bars entirely AFTER the simulated date, so every symbol's daily
    # history looks empty and get_intraday_signals returns None for everyone,
    # every day (confirmed: this is why the Aug 5 2026 H1-2025 smoke test showed
    # zero trades across all 125 days — not a quiet market, a broken data fetch).
    # Anchor to the actual replay window instead, with a ~45-calendar-day pad
    # before `start` (>=20 trading days needs ~28-30 calendar days; 45 is safe
    # margin for holidays/weekends) so the >=20-bar requirement is met even on
    # day 1 of the replay.
    daily_pad_start = (pd.Timestamp(start) - pd.Timedelta(days=45)).strftime('%Y-%m-%d')
    daily_end       = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    need_daily = list(_bars5) + ['SPY', 'QQQ']
    dl = _real_yf.download(need_daily, start=daily_pad_start, end=daily_end, interval='1d',
                           group_by='ticker', auto_adjust=False,
                           threads=True, progress=False)
    for s in need_daily:
        try:
            d = dl[s].dropna()
            if len(d):
                _daily[s] = d
        except Exception:
            pass
    # SPY/QQQ 5-min: merge (not overwrite) a yfinance 60-day tail onto the DataBento
    # DB history — market_data.db's SPY series currently stops ~2mo short of "now",
    # and QQQ needs to exist in _bars5 at all before this tail can extend it.
    for s in ('SPY', 'QQQ'):
        try:
            d = _real_yf.Ticker(s).history(period='60d', interval='5m')
            if len(d):
                d.index = d.index.tz_convert(ET)
                existing = _bars5.get(s)
                if existing is not None and len(existing):
                    merged = pd.concat([existing, d])
                    merged = merged[~merged.index.duplicated(keep='last')].sort_index()
                    _bars5[s] = merged
                else:
                    _bars5[s] = d
        except Exception:
            pass
    _synth_5min_from_daily(_VIX_SYM)

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
def _parse_period_days(period):
    period = (period or '').strip()
    if period.endswith('d'):
        try:
            return int(period[:-1])
        except ValueError:
            return None
    if period.endswith('mo'):
        try:
            return int(period[:-2]) * 30
        except ValueError:
            return None
    if period == 'max':
        return None
    return None

class _FakeTicker:
    def __init__(self, sym): self.sym = sym
    def history(self, period='5d', interval='5m', **kw):
        if interval.endswith('m'):
            days = _parse_period_days(period) or 5
            b = bars5_upto(self.sym, days=days)
            if interval == '15m' and len(b):
                # get_intraday_signals's MTF alignment check (auto_trader.py:1221)
                # requests real 15-min bars — the 5-min cache mislabeled as 15m
                # would silently feed the wrong bar size into that comparison.
                b = b.resample('15min').agg({'Open': 'first', 'High': 'max',
                                             'Low': 'min', 'Close': 'last',
                                             'Volume': 'sum'}).dropna()
            return b
        d = daily_upto(self.sym)
        days = _parse_period_days(period)
        if days is not None and len(d):
            cutoff = FakeDatetime._now.date() - pd.Timedelta(days=days)
            d = d[d.index.date >= cutoff]
        return d

class _FakeYF:
    Ticker = _FakeTicker

    @staticmethod
    def download(tickers, period='2d', interval='1d', group_by='column', **kw):
        """Mirrors yf.download(tickers, period=, interval='1d', ...)'s default
        (group_by='column') shape: 2-level column MultiIndex (field, ticker).
        Only used by update_sector_strength(), which only reads ['Close'] —
        other fields are populated for shape-compatibility, not accuracy."""
        if isinstance(tickers, str):
            tickers = tickers.split()
        days = _parse_period_days(period) or 2
        cutoff = FakeDatetime._now.date() - pd.Timedelta(days=days)
        cols = {}
        for t in tickers:
            d = daily_upto(t)
            if len(d):
                d = d[d.index.date >= cutoff]
            for field in ('Open', 'High', 'Low', 'Close', 'Volume'):
                cols[(field, t)] = d[field] if (len(d) and field in d.columns) else pd.Series(dtype=float)
        if not cols:
            return pd.DataFrame()
        out = pd.concat(cols, axis=1)
        out.columns = pd.MultiIndex.from_tuples(out.columns)
        return out

at.yf             = _FakeYF
at.get_ib_daily   = lambda symbol, duration='60 D': daily_upto(symbol)
at.get_ib_intraday = lambda symbol, duration='5 D', bar_size='5 mins': bars5_upto(symbol)
def _fake_bridge_df(symbol, duration='1 D', bar_size='5 mins'):
    # get_regime()'s breadth check asks for '1 day' bars specifically to get
    # yesterday's close (auto_trader.py:832-833: iwm_daily/mdy_daily) — routing
    # that to intraday bars (as this used to do, ignoring bar_size entirely)
    # made iwm_prev/mdy_prev resolve to TODAY's own latest price, so
    # breadth_weak/broad_advance computed a near-zero delta every time and
    # never contributed to any regime read. Route daily requests to daily_upto.
    if 'day' in bar_size:
        return daily_upto(symbol)
    return bars5_upto(symbol, days=1)

at._bridge_df     = _fake_bridge_df
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

# ── Process isolation ────────────────────────────────────────────────────────
# database.py has its own `from datetime import datetime, date` (a separate binding
# from auto_trader's) — log_trade_entry/log_trade_exit would stamp real wall-clock
# dates onto replay rows unless patched too.
import database as _database
_database.datetime = FakeDatetime
_database.date     = FakeDate
at.save_traded_today = lambda: None   # writes the real traded_today.json otherwise —
                                       # the live bot's daily-dedup state, not ours to touch
at.load_traded_today = lambda: set()

# Suffix (env-configurable) so two replay processes never collide on the same
# DB file — e.g. running run_old_design/run_new_design as separate parallel
# processes (research_fish_finder_weather_advisory.py) would otherwise both
# delete-and-recreate the SAME _replay_trades.db, corrupting whichever run
# loses the race. Each process picks its own suffix via REPLAY_DB_SUFFIX.
REPLAY_DB_PATH = os.path.join(ROOT, f'_replay_trades{os.environ.get("REPLAY_DB_SUFFIX", "")}.db')
_REAL_CONNECT = sqlite3.connect

def _install_sqlite_guard():
    """auto_trader.py has 8 call sites that do `import sqlite3 as _sq...` INSIDE the
    function body (lines 2025, 2226, 3764, 3801, 4181, 4456, 4995, 5050) — these
    re-resolve against sys.modules['sqlite3'] at call time, so an `at.sqlite3 = shim`
    attribute patch would not intercept them. Patching sqlite3.connect itself (the
    shared module every one of those imports resolves to) is the only fix that
    reaches all of them, plus database.get_connection()'s DB_PATH-based connect.
    Redirects any 'trades.db' path to a replay-local DB; 'market_data.db' (the real
    historical source) passes through unchanged. The --parity mode's read against
    the REAL production trades.db must use _REAL_CONNECT directly, not this guard."""
    def _guarded_connect(path, *a, **kw):
        if os.path.basename(str(path)) == 'trades.db':
            path = REPLAY_DB_PATH
        return _REAL_CONNECT(path, *a, **kw)
    sqlite3.connect = _guarded_connect
    _database.DB_PATH = REPLAY_DB_PATH

def _remove_sqlite_guard():
    sqlite3.connect = _REAL_CONNECT

class sqlite_guard:
    """Context manager so the process-wide patch can't leak into a longer-lived
    process later (e.g. if this ever gets embedded in an automated job) — for a
    plain `python equity_replay.py` run this is moot (process exits either way),
    but costs nothing to build defensively from the start."""
    def __enter__(self):
        if os.path.exists(REPLAY_DB_PATH):
            os.remove(REPLAY_DB_PATH)
        _install_sqlite_guard()
        _database.init_db()
        return self
    def __exit__(self, *exc):
        _remove_sqlite_guard()
        return False

# ── FillSimulator: replaces place_trade() ───────────────────────────────────
# place_trade() (auto_trader.py:1913-2045) unconditionally does a real bridge order
# POST, polls order status up to 8x with time.sleep(2) (16+ sec/entry — intractable
# over a multi-year replay), and writes DB rows. Keep its CONTRACT real (every caller
# branches on `if trade_id:`) while replacing its IMPLEMENTATION with an instant,
# 100%-fill simulator — same "monkeypatch, don't reimplement" pattern already used
# for get_ib_daily/_bridge_df elsewhere in this file.
class FillSimulator:
    def fill(self, symbol, price, shares, sl, target, strategy, grade,
              rsi=0, vol_ratio=0, confidence=75, sector='OTHER', side='LONG',
              limit_price=None, outside_rth=False):
        trade_id = at.log_trade_entry(
            symbol=symbol, entry_price=price, shares=shares,
            target_price=target, stop_price=sl, setup_type=strategy,
            rsi=rsi, volume_ratio=vol_ratio, sector=sector,
            earnings_days=999, confidence=confidence, order_id='REPLAY',
            side=side,
        )
        if trade_id:
            at.trade_entry_times[trade_id] = at.datetime.now(ET)
        return trade_id

at.place_trade = FillSimulator().fill

# ── Neutralize monitor_open_trades()'s live side effects ────────────────────
# Otherwise this runs FULLY REAL — its stop/trail/no-move/momentum-fade/EOD/L3
# stack is exactly what the old hand-rolled replay loop was a lossy approximation
# of. Two live-effect points need neutralizing:
class _FakeRequests:
    """.post() no-ops for bridge order submission (partial-exit-at-1R,
    auto_trader.py:2218-2221; full-close, :2371-2374). .get() returns a fake
    empty portfolio for /portfolio checks; anything else raises so the many
    existing try/except wrappers around these calls (e.g. 1928, 2239, 2382)
    degrade gracefully instead of silently doing the wrong thing."""
    class _Resp:
        def __init__(self, json_body): self._json = json_body
        def json(self): return self._json
        status_code = 200
    @staticmethod
    def post(url, *a, **kw):
        return _FakeRequests._Resp({})
    @staticmethod
    def get(url, *a, **kw):
        if url.rstrip('/').endswith('/portfolio'):
            return _FakeRequests._Resp([])
        raise ConnectionError('replay: no live bridge')

at.requests = _FakeRequests
at.get_ibkr_positions = lambda: {}   # real exit code already has a DB-only-close
                                      # branch when ibkr_qty<=0 (auto_trader.py:2354-2360)
                                      # — an empty dict routes every exit through it correctly

# ── get_daily_pnl(): SQLite's date('now') can't be monkeypatched from Python —
# it always reads the real wall clock, silently no-opping the daily-loss-brake and
# profit-target gates during replay. Re-query the (now correctly redirected +
# date-patched) replay DB with the SIMULATED date substituted in explicitly.
def _replay_get_daily_pnl():
    today_str = at.date.today().isoformat()
    conn = _REAL_CONNECT(_database.DB_PATH)
    row = conn.execute(
        """SELECT SUM(pnl), COUNT(*), SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END)
           FROM trades WHERE entry_date=? AND status IN ('WIN','LOSS')
           AND setup_type != 'RECONCILED'""", (today_str,)).fetchone()
    conn.close()
    return {'pnl': round(row[0] or 0, 2), 'trades': row[1] or 0, 'wins': row[2] or 0}

at.get_daily_pnl = _replay_get_daily_pnl

# _scan_and_enter/_scan_and_enter_bear call time.sleep(2) after each successful
# entry (paced pacing for live order submission / the background chart-check
# thread — already stubbed instant above). Harmless live, but 2s x possibly
# thousands of entries over a multi-year replay adds up. Shim only auto_trader's
# own `time` reference (not the real global time module) so nothing else in the
# process is affected.
import time as _real_time
class _FakeTime:
    def __getattr__(self, name):
        return getattr(_real_time, name)
    @staticmethod
    def sleep(secs): pass
at.time = _FakeTime()

# ── Replay engine ────────────────────────────────────────────────────────────
# (spy_chg is now sourced directly from at.get_regime()'s own return tuple, passed
# straight into _scan_and_enter/_scan_and_enter_bear like live does — no separate
# helper needed.)

def _trading_days(start, end):
    """Ground truth of 'market was open' — real SPY 5-min bar dates present in the
    preload cache. NOT US_HOLIDAYS_2026 (that list is 2026-only, silently wrong for
    2024/2025 — harmless there only because no bar data exists on those days either,
    so nothing would fire regardless)."""
    df = _bars5.get('SPY')
    if df is None or not len(df):
        return []
    dates = sorted(set(df.index.date))
    return [d for d in dates if str(start) <= str(d) <= str(end)]

def replay_run(start, end):
    """Replaces replay_day(). Mirrors run_scan()'s routing (auto_trader.py:3215-3427)
    — every branch body is a call to a REAL at.* function, not a reimplementation.
    The gateway/reconcile block at the top of run_scan() is deliberately skipped
    (network-only, N/A in replay — _entries_allowed is implicitly always True here).
    Positions persist across simulated days via at.get_open_trades(), exactly as
    live — no force-flatten between days; monitor_open_trades()'s own EOD/
    MAX_HOLD_DAYS logic closes them for real, when it should."""
    days = _trading_days(start, end)
    if not days:
        print('No trading days (no SPY bars) in range.')
        return []

    daily_summaries = []
    for day in days:
        set_now(ET.localize(_dt.datetime.combine(day, _dt.time(0, 1))))
        at.reset_daily_state()

        times = pd.date_range(f'{day} 09:35', f'{day} 15:55', freq='5min', tz=ET)
        for ts in times:
            set_now(ts.to_pydatetime())

            at.update_sector_strength()
            regime, spy_chg, vix, extra = at.get_regime()

            at.regime_history.append(regime)
            if len(at.regime_history) > 6:
                at.regime_history.pop(0)
            confirmed_scans = 0
            for _r in reversed(at.regime_history):
                if _r == regime:
                    confirmed_scans += 1
                else:
                    break

            if at.is_market_open() and at.spy_open_price is None:
                spy_bar = bars5_upto('SPY', 1)
                spy_bar = spy_bar[spy_bar.index.date == day]
                if len(spy_bar):
                    at.spy_open_price = round(float(spy_bar['Open'].iloc[0]), 2)

            spy_above_open = True
            if at.spy_open_price:
                spy_now_bar = bars5_upto('SPY', 1)
                if len(spy_now_bar):
                    spy_above_open = float(spy_now_bar['Close'].iloc[-1]) >= at.spy_open_price * 0.998

            # session_pnl: realized (from the now-correctly-redirected replay DB) +
            # unrealized (always 0 here — _FakeRequests./portfolio returns [], so
            # this is a stated realized-only approximation, applies identically
            # regardless of which design is under test, not a comparison bias).
            daily = at.get_daily_pnl()
            portfolio_snap = at.requests.get(f"{at.BRIDGE}/portfolio").json()
            unrealized_now = sum(p.get('unrealizedPnL', 0) or 0 for p in portfolio_snap
                                 if (p.get('qty') or 0) != 0)
            session_pnl = daily['pnl'] + unrealized_now

            open_trades = at.get_open_trades()
            # Fish Finder is retired live (FISH_FINDER_ENABLED=False); mirror that.
            if getattr(at, 'FISH_FINDER_ENABLED', False) and at.is_entry_window() \
                    and not at.is_trading_blocked()[0]:
                at._scan_regime_adaptive(regime, open_trades)
                open_trades = at.get_open_trades()

            # ── ROUTER — must mirror auto_trader.run_scan() branch for branch ──────
            # Re-synced Sep 5 2026. It had drifted badly: it still called the RETIRED
            # bear book (_scan_and_enter_bear) and Fish Finder, and it predated the
            # observe_only mode entirely, so CHOPPY/WEAK/unconfirmed/SPY-below-open all
            # monitored instead of grading. A replay of a system we no longer run cannot
            # validate anything -- CONSTITUTION.md requires sim to match the live path.
            #
            # REGIME_AS_MODIFIER: when True, the market-wide label stops being a router
            # and only the per-trade risk gates remain. This is the A/B switch for the
            # question "does the regime router earn the ~70% of cycles it stands down?"
            _rm = globals().get('REGIME_AS_MODIFIER', False)

            if not at.is_entry_window():
                at.monitor_open_trades(regime, confirmed_scans)
            elif at.is_trading_blocked()[0]:
                at.monitor_open_trades(regime, confirmed_scans)
            elif len(open_trades) >= at.MAX_OPEN_TRADES:
                at.monitor_open_trades(regime, confirmed_scans)
            elif at.daily_bull_count >= at.MAX_DAILY_BULL_TRADES:
                at.monitor_open_trades(regime, confirmed_scans)
            elif session_pnl >= at.DAILY_PROFIT_TARGET:
                at.monitor_open_trades(regime, confirmed_scans)
            elif _rm:
                # regime demoted: grade and trade regardless of the market label
                at._scan_catalyst_override(open_trades)
                at._scan_and_enter(regime, spy_chg, open_trades, confirmed_scans)
            elif regime == 'CHOPPY':
                at._scan_catalyst_override(open_trades)
                at._scan_and_enter(regime, spy_chg, open_trades, confirmed_scans,
                                   observe_only=True)
            elif regime == 'WEAK':
                at._scan_catalyst_override(open_trades)
                at._scan_and_enter(regime, spy_chg, open_trades, confirmed_scans,
                                   observe_only=True)
            elif not spy_above_open:
                at._scan_and_enter(regime, spy_chg, open_trades, confirmed_scans,
                                   observe_only=True)
            elif confirmed_scans < at.MIN_REGIME_SCANS:
                at._scan_and_enter(regime, spy_chg, open_trades, confirmed_scans,
                                   observe_only=True)
            else:
                at._scan_and_enter(regime, spy_chg, open_trades, confirmed_scans)

        day_pnl = at.get_daily_pnl()
        n_open = len(at.get_open_trades())
        print(f"{day}  bull={at.daily_bull_count} bear={at.daily_bear_count}  "
              f"day_pnl=${day_pnl['pnl']:+,.0f} ({day_pnl['trades']}t, {day_pnl['wins']}w)  open_at_eod={n_open}")
        daily_summaries.append({'date': str(day), **day_pnl, 'open_at_eod': n_open})

    return daily_summaries

REGIME_AS_MODIFIER = False   # set by --regime-as-modifier

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start'); ap.add_argument('--end')
    ap.add_argument('--parity', help='decision-parity check vs scan_log for one date')
    ap.add_argument('--no-book-health', action='store_true')
    ap.add_argument('--regime-as-modifier', action='store_true',
                    help='A/B: demote the market regime from router to modifier')
    a = ap.parse_args()

    start = a.parity or a.start
    end   = a.parity or a.end

    set_now(ET.localize(_dt.datetime.combine(_dt.date.fromisoformat(start), _dt.time(9, 35))))
    print(f'preloading bars for {len(at.FULL_UNIVERSE)} symbols…')
    preload(at.FULL_UNIVERSE, start, str(pd.Timestamp(end) + pd.Timedelta(days=1)))
    print(f'  {len(_bars5)} symbols with 5-min bars, {len(_daily)} with daily')

    _orig_book_is_on = at.book_is_on
    if a.no_book_health:
        at.book_is_on = lambda direction: True

    global REGIME_AS_MODIFIER
    REGIME_AS_MODIFIER = a.regime_as_modifier
    if REGIME_AS_MODIFIER:
        print('  ⚙  REGIME AS MODIFIER — market label does not route entries')

    try:
        with sqlite_guard():
            summaries = replay_run(start, end)

            n     = sum(s['trades'] for s in summaries)
            wins  = sum(s['wins'] for s in summaries)
            total = sum(s['pnl'] for s in summaries)
            print('─' * 60)
            print(f'Trades: {n} | WR: {wins}/{n} = {wins / n * 100:.1f}%' if n else 'Trades: 0')
            print(f'Total P&L: ${total:+,.0f}')

            if a.parity:
                sim_con = _REAL_CONNECT(REPLAY_DB_PATH)
                sim = sim_con.execute(
                    "select symbol, grade, count(*) from scan_log where scan_date=? "
                    "and direction='LONG' group by 1,2", (a.parity,)).fetchall()
                sim_con.close()
                live_con = _REAL_CONNECT(os.path.join(ROOT, 'trades.db'))
                live = live_con.execute(
                    "select symbol, grade, count(*) from scan_log where scan_date=? "
                    "and direction='LONG' group by 1,2", (a.parity,)).fetchall()
                live_con.close()
                live_syms = {(r[0], r[1]) for r in live}
                sim_syms  = {(r[0], r[1]) for r in sim}
                both = live_syms & sim_syms
                print(f'\nPARITY vs scan_log {a.parity} (LONG, symbol+grade pairs):')
                print(f'  live pairs={len(live_syms)}  sim pairs={len(sim_syms)}  overlap={len(both)}')
                denom = len(live_syms | sim_syms)
                print(f'  jaccard overlap = {len(both)}/{denom} = {len(both)/denom*100:.1f}%' if denom else '')
                print(f'  sim-only: {sorted(sim_syms - live_syms)[:10]}')
                print(f'  live-only: {sorted(live_syms - sim_syms)[:10]}')
    finally:
        at.book_is_on = _orig_book_is_on

if __name__ == '__main__':
    main()
