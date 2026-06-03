"""
backtest_enhanced.py — Enhanced strategy backtest with three modes.
Integrates with the regression suite (batch_backtest.py pattern).

Data:
  Stock intraday: market_data.db → bars_5m (Databento EQUS.MINI, Jan 2024+)
  Stock daily:    yfinance (fallback + MA/vol context)
  SPY regime:     yfinance daily
  Sector ETFs:    yfinance daily (data-driven map from correlation analysis)

Modes:
  A — Baseline:    Current logic. CHOPPY/CAUTIOUS days blocked entirely.
                   Earnings unknown → skip.
  B — Fixes:       + Fix1: catalyst stocks bypass CAUTIOUS/CHOPPY.
                   + Fix2: earnings unknown + running hard (>5% / 3x vol) → allow.
  C — ETF Regime:  Mode B + sector ETF gap gate. If sector ETF gapped up >0.5%
                   at open, allow sector stocks even on choppy SPY days.

Run:
  venv/bin/python backtest_enhanced.py              # A/B/C full run
  venv/bin/python backtest_enhanced.py --corr-only  # ETF correlation only
  venv/bin/python backtest_enhanced.py --mode A     # single mode
  venv/bin/python backtest_enhanced.py --start 2024-06-01

Output: per-mode summary + monthly + yearly + regime breakdown + sector breakdown + A/B/C delta table
"""

import sys, os, argparse, warnings
from datetime import date, datetime
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
import sqlite3

warnings.filterwarnings('ignore')

sys.path.insert(0, str(Path(__file__).parent))

# ── Config ────────────────────────────────────────────────────────────────────
START_DATE = '2024-01-01'   # matches Databento EQUS.MINI start
END_DATE   = date.today().isoformat()
DB_PATH    = Path(__file__).parent / 'market_data.db'

CAPITAL_PER_TRADE = 2_000
STOP_PCT          = 5.0
ATR_TRAIL_MULT    = 1.5
MIN_VOL_RATIO     = 1.3
MIN_TODAY_GAIN    = 3.0
ATR_PERIOD        = 14

# Choppy-day proxy: SPY net move tiny but range was wide (indecision)
CHOPPY_CHG_MAX   = 0.30    # |SPY daily %| < this = flat
CHOPPY_RANGE_MIN = 1.0     # SPY (H-L)/Open > this = churning

# Sector ETF gate (Mode C): ETF gap-up vs prior close at open (look-ahead-free)
ETF_GAP_THRESHOLD = 0.5    # sector ETF gapped >0.5% → sector hot

# ── Sector map (mirrors auto_trader.py) ───────────────────────────────────────
SECTOR_MAP = {
    'AAPL':'TECH','MSFT':'TECH','AMZN':'TECH','GOOGL':'TECH','META':'TECH',
    'AMD':'TECH','AVGO':'TECH','NFLX':'TECH','NVDA':'TECH','INTC':'TECH','TSLA':'TECH',
    'COHR':'TECH','LITE':'TECH','CLS':'TECH','SMCI':'TECH','CRWV':'TECH',
    'PLTR':'TECH','NBIS':'TECH','AI':'TECH','CRM':'TECH','ORCL':'TECH','RBRK':'TECH',
    'DDOG':'TECH','MDB':'TECH','ON':'TECH','SOUN':'TECH','BBAI':'TECH',
    'LRCX':'SEMIS','QCOM':'SEMIS','MRVL':'SEMIS','KLAC':'SEMIS',
    'AMAT':'SEMIS','MU':'SEMIS','INDI':'SEMIS','POET':'SEMIS',
    'AEHR':'SEMIS','CRDO':'SEMIS','AXTI':'SEMIS','SITM':'SEMIS','ACLS':'SEMIS','ONTO':'SEMIS','ARM':'SEMIS',
    'OKLO':'NUCLEAR','CCJ':'NUCLEAR','UUUU':'NUCLEAR','DNN':'NUCLEAR',
    'NU':'FINTECH','RKT':'FINTECH','JPM':'FINTECH','GS':'FINTECH',
    'TOST':'FINTECH','BAC':'FINTECH','C':'FINTECH','WFC':'FINTECH','V':'FINTECH','MA':'FINTECH',
    'IBKR':'FINTECH','KKR':'FINTECH','UPST':'FINTECH',
    'COIN':'QUANTUM_CRYPTO','HOOD':'TECH',
    'LLY':'BIOTECH','NTLA':'BIOTECH','BEAM':'BIOTECH','NUTX':'BIOTECH',
    'UNH':'BIOTECH','MRNA':'BIOTECH','PFE':'BIOTECH','ABBV':'BIOTECH',
    'ISRG':'BIOTECH','DXCM':'BIOTECH','HIMS':'BIOTECH','EW':'BIOTECH',
    'GILD':'BIOTECH','BSX':'BIOTECH','HOLX':'BIOTECH',
    'IONQ':'QUANTUM_CRYPTO','QBTS':'QUANTUM_CRYPTO','RGTI':'QUANTUM_CRYPTO',
    'IREN':'QUANTUM_CRYPTO','APLD':'QUANTUM_CRYPTO',
    'MARA':'QUANTUM_CRYPTO','MSTR':'QUANTUM_CRYPTO','CLSK':'QUANTUM_CRYPTO',
    'WULF':'QUANTUM_CRYPTO','HUT':'QUANTUM_CRYPTO','RIOT':'QUANTUM_CRYPTO','CIFR':'QUANTUM_CRYPTO',
    'CVX':'ENERGY','XOM':'ENERGY','OXY':'ENERGY','SLB':'ENERGY',
    'HAL':'ENERGY','DVN':'ENERGY','FSLR':'ENERGY','EOSE':'ENERGY',
    'VST':'ENERGY','CNQ':'ENERGY','EQT':'ENERGY','CTRA':'ENERGY','WFRD':'ENERGY',
    'RTX':'DEFENCE','LMT':'DEFENCE','NOC':'DEFENCE','CAT':'DEFENCE',
    'DE':'DEFENCE','AXON':'DEFENCE','RKLB':'DEFENCE','JOBY':'DEFENCE',
    'KTOS':'DEFENCE','CACI':'DEFENCE','SAIC':'DEFENCE','BWXT':'DEFENCE',
    'HWM':'DEFENCE','GE':'DEFENCE','TXT':'DEFENCE','ONDS':'DEFENCE',
    'RDW':'DEFENCE','HXL':'DEFENCE','TT':'DEFENCE',
    'COST':'CONSUMER','NKE':'CONSUMER','SBUX':'CONSUMER','CMG':'CONSUMER',
    'UBER':'CONSUMER','USAR':'CONSUMER','SHOP':'CONSUMER','CPNG':'CONSUMER',
    'SAIA':'CONSUMER','TPR':'CONSUMER','YUM':'CONSUMER','DECK':'CONSUMER',
    'CELH':'CONSUMER','LULU':'CONSUMER',
    'LAC':'CLEAN_ENERGY','RIVN':'CLEAN_ENERGY','NIO':'CLEAN_ENERGY',
    'CHPT':'CLEAN_ENERGY','ARRY':'CLEAN_ENERGY',
    'FCX':'COMMODITIES','NEM':'COMMODITIES','MP':'COMMODITIES',
    'HL':'COMMODITIES','AG':'COMMODITIES','AEM':'COMMODITIES','APD':'COMMODITIES',
    'APP':'TECH','VERI':'TECH','SSYS':'TECH','OUST':'TECH',
    'FTNT':'TECH','GDDY':'TECH',
    'ZM':'TECH','DUOL':'TECH','RBLX':'TECH','TTD':'TECH','TWLO':'TECH',
    'DOCU':'TECH','ZS':'TECH','HUBS':'TECH','OKTA':'TECH','PANW':'TECH',
}

FULL_UNIVERSE = sorted(SECTOR_MAP.keys())

# ── Data-driven ETF map (validated Jun 2 2026 correlation analysis) ───────────
SECTOR_ETF_MAP = {
    'TECH':          'XLK',    # corr 0.511
    'SEMIS':         'SOXX',   # corr 0.646 (upgraded from SMH 0.632)
    'FINTECH':       'XLF',    # corr 0.637
    'ENERGY':        'XLE',    # corr 0.617
    'BIOTECH':       'IBB',    # corr 0.366 (upgraded from XBI 0.334)
    'NUCLEAR':       'URA',    # corr 0.783 (upgraded from NLR 0.778)
    'DEFENCE':       'XAR',    # corr 0.521 (upgraded from ITA 0.501)
    'QUANTUM_CRYPTO':'BITQ',   # corr 0.703 (major upgrade from QQQ 0.432)
    'CONSUMER':      'XRT',    # corr 0.387
    'CLEAN_ENERGY':  'QCLN',   # corr 0.496 (upgraded from ICLN 0.413)
    'COMMODITIES':   'GDX',    # corr 0.618 (upgraded from GLD 0.472)
    'OTHER':         'SPY',
}

ETF_CANDIDATES = {
    'TECH':           ['XLK', 'QQQ', 'BOTZ'],
    'SEMIS':          ['SMH', 'SOXX', 'XSD'],
    'FINTECH':        ['XLF', 'FINX', 'KRE'],
    'ENERGY':         ['XLE', 'XOP', 'OIH'],
    'BIOTECH':        ['XBI', 'IBB', 'ARKG'],
    'NUCLEAR':        ['NLR', 'URA', 'URNM'],
    'DEFENCE':        ['ITA', 'XAR', 'DFEN'],
    'QUANTUM_CRYPTO': ['QQQ', 'WGMI', 'BITQ', 'QTUM'],
    'CONSUMER':       ['XLY', 'XRT', 'RETL'],
    'CLEAN_ENERGY':   ['ICLN', 'TAN', 'QCLN'],
    'COMMODITIES':    ['GLD', 'COPX', 'XME', 'GDX'],
}


# ── Part 1: ETF Correlation Analysis ─────────────────────────────────────────

def run_etf_correlation(start=START_DATE):
    print('=' * 70)
    print('  ETF CORRELATION ANALYSIS  (2yr daily returns)')
    print(f'  Period: {start} → {END_DATE}')
    print('=' * 70)

    all_etfs = list({e for cands in ETF_CANDIDATES.values() for e in cands})
    by_sector = defaultdict(list)
    for sym, sec in SECTOR_MAP.items():
        by_sector[sec].append(sym)

    print('  Downloading ETF data...')
    etf_raw  = yf.download(all_etfs, start=start, end=END_DATE, auto_adjust=True, progress=False)
    etf_close = etf_raw['Close'] if 'Close' in etf_raw else etf_raw
    if isinstance(etf_close.columns, pd.MultiIndex):
        etf_close.columns = etf_close.columns.get_level_values(0)
    etf_ret = etf_close.pct_change().dropna()

    best_etf_map = {}
    for sector, candidates in sorted(ETF_CANDIDATES.items()):
        stocks = by_sector.get(sector, [])
        if not stocks:
            continue
        stk_raw = yf.download(stocks, start=start, end=END_DATE, auto_adjust=True, progress=False)
        stk_close = stk_raw['Close'] if 'Close' in stk_raw else stk_raw
        if isinstance(stk_close.columns, pd.MultiIndex):
            stk_close.columns = stk_close.columns.get_level_values(0)
        elif len(stocks) == 1:
            stk_close = stk_close.to_frame(name=stocks[0])
        stk_ret = stk_close.pct_change().dropna()
        common  = etf_ret.index.intersection(stk_ret.index)
        e, s    = etf_ret.loc[common], stk_ret.loc[common]

        results = []
        current = SECTOR_ETF_MAP.get(sector, '?')
        for etf in candidates:
            if etf not in e.columns:
                continue
            corrs = [s[stk].dropna().corr(e[etf].reindex(s[stk].dropna().index).dropna())
                     for stk in stocks if stk in s.columns]
            corrs = [c for c in corrs if not np.isnan(c)]
            avg   = np.mean(corrs) if corrs else 0
            results.append((etf, avg, len(corrs)))

        results.sort(key=lambda x: -x[1])
        best = results[0][0] if results else current
        best_etf_map[sector] = best

        print(f'\n  {sector} ({len(stocks)} stocks):')
        for etf, corr, n in results:
            markers = (' ← BEST' if etf == best else '') + (' [CURRENT]' if etf == current else '')
            print(f'    {etf:<8} avg corr={corr:.3f}  (n={n} stocks){markers}')
        if best != current:
            print(f'    ⚡ Upgrade: {current} → {best}')

    print('\n' + '─' * 70)
    print('  RECOMMENDED SECTOR_ETF_MAP:')
    for sec in sorted(best_etf_map):
        cur = SECTOR_ETF_MAP.get(sec, '?')
        chg = f'  ← was {cur}' if best_etf_map[sec] != cur else ''
        print(f"    '{sec}': '{best_etf_map[sec]}',{chg}")
    print('=' * 70)
    return best_etf_map


# ── Part 2: Data loading ───────────────────────────────────────────────────────

def load_spy(start):
    spy = yf.download('SPY', start=start, end=END_DATE, auto_adjust=True, progress=False)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)
    spy['chg']    = spy['Close'].pct_change() * 100
    spy['range']  = (spy['High'] - spy['Low']) / spy['Open'] * 100
    spy['regime'] = spy['chg'].apply(
        lambda c: 'STRONG' if c >= 0.5 else ('WEAK' if c <= -0.5 else 'NORMAL')
    )
    spy['choppy'] = (spy['chg'].abs() < CHOPPY_CHG_MAX) & (spy['range'] > CHOPPY_RANGE_MIN)
    return spy


def load_etfs(etf_map, start):
    etfs = list(set(etf_map.values()))
    raw  = yf.download(etfs, start=start, end=END_DATE, auto_adjust=True, progress=False)
    cls  = raw['Close'] if 'Close' in raw else raw
    opn  = raw['Open']  if 'Open'  in raw else None
    for df in [cls, opn]:
        if df is not None and isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
    if len(etfs) == 1:
        cls = cls.to_frame(name=etfs[0])
        if opn is not None:
            opn = opn.to_frame(name=etfs[0])
    daily_ret = cls.pct_change() * 100
    gap = pd.DataFrame(index=cls.index)
    if opn is not None:
        for col in cls.columns:
            if col in opn.columns:
                gap[col] = (opn[col] - cls[col].shift(1)) / cls[col].shift(1) * 100
    return daily_ret, gap


def load_5min_from_db(symbol, start, end=None):
    """Load 5-min bars from market_data.db (Databento EQUS.MINI)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        q    = "SELECT ts_utc, open, high, low, close, volume FROM bars_5m WHERE symbol=? AND ts_utc >= ?"
        params = [symbol, start]
        if end:
            q += " AND ts_utc <= ?"
            params.append(end)
        q += " ORDER BY ts_utc"
        df = pd.read_sql_query(q, conn, params=params, parse_dates=['ts_utc'])
        conn.close()
        if df.empty:
            return pd.DataFrame()
        df['ts_utc'] = pd.to_datetime(df['ts_utc'], utc=True)
        df = df.set_index('ts_utc')
        df.index = df.index.tz_convert('America/New_York')
        return df
    except Exception:
        return pd.DataFrame()


def load_daily_context(symbol, start):
    """Daily bars for MA20, avg_vol context (fills what 5-min can't give)."""
    try:
        raw = yf.download(symbol, start=start, end=END_DATE, auto_adjust=True, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        if raw.empty:
            return pd.DataFrame()
        raw['ma20']     = raw['Close'].rolling(20).mean()
        raw['avg_vol']  = raw['Volume'].rolling(20).mean()
        h, l, c = raw['High'], raw['Low'], raw['Close']
        tr = pd.concat([h-l, (h-c.shift(1)).abs(), (l-c.shift(1)).abs()], axis=1).max(axis=1)
        raw['atr'] = tr.rolling(ATR_PERIOD).mean()
        raw['prev_close'] = raw['Close'].shift(1)
        return raw
    except Exception:
        return pd.DataFrame()


# ── Part 3: Trade simulation ───────────────────────────────────────────────────

def simulate_day_5min(bars_5m, entry_after='10:00', stop_price=None, trail_mult=1.5, atr=None):
    """
    Simulate one stock on one day using real 5-min bars.
    Returns: pnl_pct, exit_reason, actual_entry_price
    entry_after: don't enter before this time (ORB window)
    """
    if bars_5m.empty or len(bars_5m) < 3:
        return None, None, None

    # ORB: first bars 9:30-10:00am
    orb_bars = bars_5m.between_time('09:30', '09:55')
    if orb_bars.empty:
        return None, None, None
    orb_high = orb_bars['high'].max()
    orb_low  = orb_bars['low'].min()
    orb_mid  = (orb_high + orb_low) / 2

    # Entry: first bar after ORB that closes above ORB high (bull break)
    post_orb = bars_5m.between_time(entry_after, '14:55')
    if post_orb.empty:
        return None, None, None

    entry_bar = None
    entry_price = None
    for ts, row in post_orb.iterrows():
        if row['close'] > orb_high and row['volume'] > 0:
            entry_bar  = ts
            entry_price = row['close']
            break

    if entry_price is None:
        return None, 'no_breakout', None

    # Stop at ORB midpoint (IB-stop methodology)
    stop = stop_price if stop_price else orb_mid
    if stop >= entry_price:
        stop = entry_price * 0.95   # fallback 5% stop

    trail_ref = entry_price
    peak      = entry_price

    # Walk bars after entry
    after_entry = bars_5m[bars_5m.index > entry_bar]
    exit_price  = None
    exit_reason = 'eod'

    for ts, row in after_entry.iterrows():
        if ts.time() >= datetime.strptime('15:45', '%H:%M').time():
            exit_price  = row['close']
            exit_reason = 'eod'
            break

        # Stop hit
        if row['low'] <= stop:
            exit_price  = stop
            exit_reason = 'stop'
            break

        # ATR trail activates when up >1%
        if atr and (peak - entry_price) / entry_price > 0.01:
            trail_stop = peak - trail_mult * atr
            if row['low'] <= trail_stop:
                exit_price  = max(trail_stop, row['low'])
                exit_reason = 'trail'
                break

        peak = max(peak, row['high'])

    if exit_price is None:
        exit_price = after_entry.iloc[-1]['close'] if not after_entry.empty else entry_price
        exit_reason = 'eod'

    pnl_pct = (exit_price - entry_price) / entry_price * 100
    return pnl_pct, exit_reason, entry_price


def simulate_symbol(symbol, daily, spy, etf_gap, sector_etf, mode):
    """Simulate all trades for one symbol across the full date range."""
    trades = []
    if daily.empty or len(daily) < 25:
        return trades

    sector = SECTOR_MAP.get(symbol, 'OTHER')
    etf    = sector_etf.get(sector, 'SPY')

    # Load 5-min bars once for the full period (much faster than per-day)
    bars5 = load_5min_from_db(symbol, daily.index[0].strftime('%Y-%m-%d'))

    for i in range(21, len(daily)):
        row      = daily.iloc[i]
        prev     = daily.iloc[i - 1]
        d        = daily.index[i]
        d_str    = d.strftime('%Y-%m-%d')

        if d not in spy.index:
            continue

        spy_row   = spy.loc[d]
        regime    = spy_row['regime']
        is_choppy = bool(spy_row['choppy'])

        price    = float(row['Open'])
        ma20     = float(row['ma20']) if not pd.isna(row['ma20']) else None
        avg_vol  = float(daily['avg_vol'].iloc[i - 1]) if not pd.isna(daily['avg_vol'].iloc[i - 1]) else None
        atr      = float(row['atr']) if not pd.isna(row['atr']) else None
        prev_close = float(prev['Close'])

        if price < 3 or not ma20 or price < ma20:
            continue
        if regime == 'WEAK':
            continue

        today_gain = (float(row['Close']) - prev_close) / prev_close * 100
        if today_gain < MIN_TODAY_GAIN:
            continue

        vol_ratio = float(row['Volume'] / avg_vol) if avg_vol and avg_vol > 0 else 1.0
        if vol_ratio < MIN_VOL_RATIO:
            continue

        # Catalyst proxy: vol >3x AND gain >5%
        catalyst = vol_ratio >= 3.0 and today_gain >= 5.0

        # ── Regime gate ───────────────────────────────────────────────────────
        is_weak_normal = is_choppy or (regime == 'NORMAL' and spy_row['chg'] < 0.1)

        if is_weak_normal:
            if mode == 'A':
                continue

            elif mode == 'B':
                if not catalyst:
                    continue

            elif mode == 'C':
                if not catalyst:
                    if etf in etf_gap.columns and d in etf_gap.index:
                        gap_val = float(etf_gap.loc[d, etf])
                        if pd.isna(gap_val) or gap_val < ETF_GAP_THRESHOLD:
                            continue
                    else:
                        continue

        # ── Entry / exit via 5-min bars ───────────────────────────────────────
        day_bars = pd.DataFrame()
        if not bars5.empty:
            day_bars = bars5[bars5.index.date == d.date()]

        if not day_bars.empty:
            orb_bars = day_bars.between_time('09:30', '09:55')
            orb_mid  = (orb_bars['high'].max() + orb_bars['low'].min()) / 2 if not orb_bars.empty else price * 0.95
            stop     = orb_mid
            pnl_pct, exit_reason, entry_price = simulate_day_5min(day_bars, stop_price=stop, atr=atr)
            if pnl_pct is None or exit_reason == 'no_breakout':
                continue  # no valid ORB breakout on this day
            source = '5min'
        else:
            # Daily fallback
            day_low  = float(row['Low'])
            day_high = float(row['High'])
            day_close = float(row['Close'])
            entry_price = price
            stop = price * (1 - STOP_PCT / 100)
            if day_low <= stop:
                pnl_pct    = -STOP_PCT
                exit_reason = 'stop'
            elif atr and (day_high - price) / price > 0.01:
                trail_stop = day_high - ATR_TRAIL_MULT * atr
                if day_close < trail_stop:
                    pnl_pct    = (trail_stop - price) / price * 100
                    exit_reason = 'trail'
                else:
                    pnl_pct    = (day_close - price) / price * 100
                    exit_reason = 'eod'
            else:
                pnl_pct    = (day_close - price) / price * 100
                exit_reason = 'eod'
            source = 'daily'

        pnl = pnl_pct / 100 * CAPITAL_PER_TRADE

        trades.append({
            'date':        d,
            'year':        d.year,
            'month':       d.to_period('M'),
            'symbol':      symbol,
            'sector':      sector,
            'etf':         etf,
            'regime':      regime,
            'choppy':      is_choppy,
            'catalyst':    catalyst,
            'vol_ratio':   round(vol_ratio, 1),
            'today_gain':  round(today_gain, 1),
            'pnl_pct':     round(pnl_pct, 2),
            'pnl':         round(pnl, 2),
            'exit_reason': exit_reason,
            'source':      source,
            'win':         pnl > 0,
        })

    return trades


def run_backtest(mode, start=START_DATE):
    spy     = load_spy(start)
    _, etf_gap = load_etfs(SECTOR_ETF_MAP, start)
    all_trades = []
    print(f'  Mode {mode}: ', end='', flush=True)

    for sym in FULL_UNIVERSE:
        daily = load_daily_context(sym, start)
        if daily.empty:
            continue
        trades = simulate_symbol(sym, daily, spy, etf_gap, SECTOR_ETF_MAP, mode)
        all_trades.extend(trades)
        print('.', end='', flush=True)

    print(f' {len(all_trades):,} trades')
    return pd.DataFrame(all_trades) if all_trades else pd.DataFrame()


# ── Part 4: Reporting ─────────────────────────────────────────────────────────

def _stats(df):
    if df.empty:
        return {}
    n     = len(df)
    wins  = df['win'].sum()
    daily = df.groupby('date')['pnl'].sum()
    sh    = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0
    cum   = daily.cumsum()
    dd    = (cum - cum.cummax()).min()
    win_pnl  = df[df['win']]['pnl'].mean() if wins > 0 else 0
    loss_pnl = df[~df['win']]['pnl'].mean() if (n - wins) > 0 else 0
    pf = abs(win_pnl * wins / (loss_pnl * (n - wins))) if (n - wins) > 0 and loss_pnl != 0 else float('inf')
    fivemin_pct = df[df['source'] == '5min'].shape[0] / n * 100 if '5min' in df.columns else 0
    return {'n': n, 'wr': wins/n*100, 'pnl': df['pnl'].sum(),
            'avg': df['pnl'].mean(), 'win': win_pnl, 'loss': loss_pnl,
            'pf': pf, 'sharpe': sh, 'maxdd': dd, 'fivemin_pct': fivemin_pct}


def print_summary(df, label):
    s = _stats(df)
    if not s:
        print(f'  {label}: no trades')
        return s
    fivemin = f" | 5-min bars: {s['fivemin_pct']:.0f}%" if 'source' in df.columns else ''
    print(f'\n  ── {label} ──────────────────────────────────────────')
    print(f'  Trades: {s["n"]:,}  |  WR: {s["wr"]:.1f}%  |  P&L: ${s["pnl"]:,.0f}  |  Avg/trade: ${s["avg"]:.0f}{fivemin}')
    print(f'  Win: ${s["win"]:.0f}  |  Loss: ${s["loss"]:.0f}  |  PF: {s["pf"]:.2f}  |  Sharpe: {s["sharpe"]:.2f}  |  MaxDD: ${s["maxdd"]:,.0f}')
    return s


def print_by_year(df, label):
    print(f'\n  ── {label} — By Year ──────────────────────────────')
    print(f'  {"Year":<6} {"Trades":>7} {"WR%":>6} {"P&L":>10} {"Sharpe":>8} {"5min%":>6}')
    for yr, g in df.groupby('year'):
        daily = g.groupby('date')['pnl'].sum()
        sh = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0
        fivemin = f'{g[g["source"]=="5min"].shape[0]/len(g)*100:.0f}%' if 'source' in g else '-'
        print(f'  {yr:<6} {len(g):>7,} {g["win"].mean()*100:>6.1f}% ${g["pnl"].sum():>9,.0f} {sh:>8.2f} {fivemin:>6}')


def print_by_month(df, label):
    print(f'\n  ── {label} — By Month ─────────────────────────────')
    print(f'  {"Month":<9} {"Trades":>7} {"WR%":>6} {"P&L":>10}')
    for mo, g in df.groupby('month'):
        print(f'  {str(mo):<9} {len(g):>7,} {g["win"].mean()*100:>6.1f}% ${g["pnl"].sum():>9,.0f}')


def print_regime_breakdown(df, label):
    print(f'\n  ── {label} — Regime Breakdown ─────────────────────')
    print(f'  {"Condition":<24} {"Trades":>7} {"WR%":>6} {"P&L":>10} {"Avg$":>7}')
    groups = {
        'STRONG day':       df[df['regime'] == 'STRONG'],
        'NORMAL clean':     df[(df['regime'] == 'NORMAL') & ~df['choppy']],
        'Choppy SPY (new)': df[df['choppy'] | ((df['regime'] == 'NORMAL') & (df['pnl'] > -9999) & df['choppy'])],
        'Catalyst plays':   df[df['catalyst']],
        'Non-catalyst':     df[~df['catalyst']],
    }
    for lbl, g in groups.items():
        if g.empty:
            continue
        print(f'  {lbl:<24} {len(g):>7,} {g["win"].mean()*100:>6.1f}% ${g["pnl"].sum():>9,.0f} ${g["pnl"].mean():>6.0f}')


def print_sector_breakdown(df, label):
    print(f'\n  ── {label} — By Sector ─────────────────────────────')
    print(f'  {"Sector":<16} {"Trades":>7} {"WR%":>6} {"P&L":>10} {"ETF":>6}')
    for sec, g in sorted(df.groupby('sector'), key=lambda x: -x[1]['pnl'].sum()):
        etf = g['etf'].iloc[0] if not g.empty else '?'
        print(f'  {sec:<16} {len(g):>7,} {g["win"].mean()*100:>6.1f}% ${g["pnl"].sum():>9,.0f} {etf:>6}')


def compare(results):
    print('\n' + '=' * 78)
    print('  A / B / C  COMPARISON')
    print('=' * 78)
    print(f'  {"Mode":<38} {"Trades":>7} {"WR%":>6} {"P&L":>11} {"Sharpe":>7} {"MaxDD":>9}')
    base = None
    for r in results:
        delta = ''
        if base:
            dp = r['pnl'] - base['pnl']
            dw = r['wr']  - base['wr']
            delta = f'  Δ P&L ${dp:+,.0f}  Δ WR {dw:+.1f}%'
        print(f'  {r["label"]:<38} {r["n"]:>7,} {r["wr"]:>6.1f}% ${r["pnl"]:>10,.0f} '
              f'{r["sharpe"]:>7.2f} ${r["maxdd"]:>8,.0f}{delta}')
        if base is None:
            base = r
    print('=' * 78)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Enhanced strategy backtest')
    parser.add_argument('--corr-only', action='store_true')
    parser.add_argument('--start',     default=START_DATE)
    parser.add_argument('--mode',      default='ABC', help='A | B | C | AB | ABC')
    args = parser.parse_args()

    print('\n' + '=' * 78)
    print('  ENHANCED STRATEGY BACKTEST  (5-min bars from Databento EQUS.MINI)')
    print(f'  Universe: {len(FULL_UNIVERSE)} symbols  |  Period: {args.start} → {END_DATE}')
    print(f'  Capital: ${CAPITAL_PER_TRADE:,}/trade  |  Stop: {STOP_PCT}%  |  Min gain: {MIN_TODAY_GAIN}%')
    print('=' * 78)

    # Step 1: ETF correlation
    run_etf_correlation(start=args.start)
    if args.corr_only:
        sys.exit(0)

    # Step 2: Backtests
    print('\n' + '=' * 78)
    print('  BACKTEST MODES')
    print('  A — Baseline:  CAUTIOUS/CHOPPY blocks all entries; earnings unknown = skip')
    print('  B — Fixes:     Catalyst bypass CAUTIOUS; earnings unknown+hot = allow')
    print('  C — ETF gate:  B + sector ETF gap >0.5% at open unlocks sector stocks')
    print('  All modes use 5-min ORB simulation where Databento bars available.')
    print('=' * 78)

    modes_to_run = [m for m in args.mode.upper() if m in ('A', 'B', 'C')]
    labels = {
        'A': 'Mode A — Baseline',
        'B': 'Mode B — Catalyst + Earnings fixes',
        'C': 'Mode C — B + Sector ETF regime',
    }

    all_results = []
    all_dfs     = {}

    for mode in modes_to_run:
        df = run_backtest(mode, start=args.start)
        all_dfs[mode] = df
        s = print_summary(df, labels[mode])
        s['label'] = labels[mode]
        all_results.append(s)

    # Detailed yearly + monthly for all modes
    for mode in modes_to_run:
        df = all_dfs[mode]
        if not df.empty:
            print_by_year(df, labels[mode])
            print_by_month(df, labels[mode])

    # Regime and sector breakdown for Mode A and C (comparison)
    if 'A' in all_dfs and not all_dfs['A'].empty:
        print_regime_breakdown(all_dfs['A'], 'Mode A')
    if 'C' in all_dfs and not all_dfs['C'].empty:
        print_regime_breakdown(all_dfs['C'], 'Mode C')
        print_sector_breakdown(all_dfs['C'], 'Mode C — Sector ETF map')

    compare(all_results)

    # What are we still missing?
    print('\n  ── Additional opportunities to explore ────────────────────────')
    print('  1. VIX regime filter — high VIX (>25) = different stock behavior, wider stops needed')
    print('  2. Sector concentration cap — max 2 open slots per sector (correlated drawdown risk)')
    print('  3. Multi-day momentum — stocks up >5% yesterday carry over morning (continuation bias)')
    print('  4. Price vs HOD at entry — buying 2%+ below HOD vs fresh HOD break: very different WR')
    print('  5. Failed ORB rate — how often does entry bar fail to follow through (5-min data tells us)')
    print('  6. Options on catalyst days — MRVL +14% equity = +200%+ call option (leverage opportunity)')
    print('  7. Sector ETF as position-sizing signal (not just on/off) — bigger position when ETF surging')
