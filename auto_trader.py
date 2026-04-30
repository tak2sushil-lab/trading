# auto_trader.py v2 — Consolidated single-process auto trader
# Absorbs: trader.py (monitoring, WhatsApp commands, evening summary)
#          scheduler.py (catalyst scan, voice summary, nightly learning)
# Depends on: bridge.py (IBKR gateway), tunnel.py (WhatsApp webhook)
# Command: python auto_trader.py

from dotenv import load_dotenv
load_dotenv()

import os, json, time, requests, yfinance as yf, pandas as pd, numpy as np
from datetime import datetime, date, timedelta
import pytz, pyttsx3
from apscheduler.schedulers.background import BackgroundScheduler
from database import (
    init_db, log_trade_entry, log_trade_exit,
    get_open_trades, get_daily_pnl, get_win_rate,
    update_trade_stop, get_trade_entry_date
)
from catalyst_detector import run_catalyst_scan
from learner import run_learning_cycle

ET = pytz.timezone('America/New_York')

# ── Credentials ───────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
BRIDGE           = 'http://127.0.0.1:8000'
TG_API           = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ── Settings ──────────────────────────────────────────────
SCAN_INTERVAL     = 300      # 5 min
TOTAL_CAPITAL     = 10000    # max capital deployed across all positions
MAX_OPEN_TRADES   = 5        # 5 × $2,000 = $10K fully deployed
MAX_DAILY_TRADES  = 5        # top-5 daily entry cap — only best setups by score
MAX_RISK_PCT        = 8.0    # hard ATR cap — removed 2% floor, trust the ATR fully
MIN_RR              = 2.5    # min reward:risk for entry qualification
MAX_LOSS_PER_TRADE  = 100    # dollar circuit breaker: $100 = 5% of $2,000 position
DAILY_PROFIT_TARGET = 400    # at +$400 session P&L: protect gains, no new entries
EOD_CLOSE_HOUR      = 15     # 3:45pm ET EOD close — exit unless conviction to hold overnight
EOD_CLOSE_MINUTE    = 45
MAX_RSI_5M        = 85   # skip entry if 5-min RSI >85 — intraday candle is exhausted
MIN_VOLUME_RATIO  = 1.3
MAX_PER_SECTOR    = 5        # max open positions per sector
TG_POLL_OFFSET    = 0        # tracks last processed Telegram update
ATR_PERIOD        = 14
ATR_STOP_MULT     = 2.0      # initial stop: entry - 2×ATR (swing needs breathing room)
ATR_TRAIL_MULT    = 1.5      # trail: 1.5×ATR below rolling session high
ATR_FADE_MULT     = 1.0      # momentum fade: drop > 1×ATR from session high
MAX_HOLD_DAYS     = 1        # hard max hold: exit any position after 1 business day (~24h)
NO_ENTRY_BEFORE   = 10       # wait until 10:00am — let opening range establish
NO_ENTRY_AFTER    = 15       # no new entries at/after 3:00pm ET
MIN_REGIME_SCANS  = 2        # regime must be confirmed for N consecutive scans before entry
MIN_TODAY_GAIN    = 1.5      # stock must be up ≥1.5% today before we enter
MAX_DAILY_LOSS    = 200      # stop new entries if daily P&L < -$200
LUNCH_AVOID_START = (11, 30) # no new entries from 11:30am ET (lunch chop)
LUNCH_AVOID_END   = (12, 45) # resume entries at 12:45pm ET
ORB_ENTRY_CUTOFF  = (11, 30) # ORB signal only valid before 11:30am — late breaks are just resistance

# ── Persistence ───────────────────────────────────────────
_DIR              = os.path.dirname(os.path.abspath(__file__))
TRADED_TODAY_FILE = os.path.join(_DIR, 'traded_today.json')

# ── In-memory state ───────────────────────────────────────
traded_today      = set()
open_positions    = {}       # sym → trade_id
price_history     = {}       # trade_id → [prices]
session_high      = {}       # trade_id → highest price seen (LONG trades)
session_low       = {}       # trade_id → lowest price seen (SHORT trades)
atr_cache         = {}       # sym → (date_str, atr_value)
daily_trade_count = 0
catalyst_priority  = []       # symbols from today's catalyst scan
tg_update_id       = 0        # Telegram polling offset
regime_history     = []       # last N regime readings for confirmation
spy_open_price     = None     # SPY price at market open (set on first post-open scan)
sector_strength    = {}       # ETF ticker → % change today, updated each scan
key_levels         = {}       # sym → {pm_high, pm_low, prior_close, orb_high, orb_low}
trade_entry_times  = {}       # trade_id → datetime of entry (for no-move exit)
earnings_cache     = {}       # symbol → (date_str, days_to_next_earnings)
partial_done_trades = {}      # trade_id → locked_pnl_usd — trades that had 50% sold at 1R

# ── Universe ──────────────────────────────────────────────
FULL_UNIVERSE = list(dict.fromkeys([
    # ── Tier 1+2: 62%+ win rate (v6 confirmed) ───────────
    'AAPL', 'PLTR', 'COHR', 'IONQ', 'HOOD', 'JPM', 'IREN', 'NUTX',
    'LITE', 'VST', 'ITA', 'NFLX', 'ORCL', 'OKLO', 'AMZN', 'GOOGL',
    'CRM', 'QBTS',
    # ── Tier 3: 54-59% win rate, positive avg ────────────
    'TOST', 'AVGO', 'NBIS', 'CLS', 'RKLB', 'CNQ',
    # ── Borderline: 50%, positive avg, good sample ───────
    'AMD', 'RKT', 'NU',
    # ── Mega cap (price filter may apply in backtest) ─────
    'MSFT', 'META', 'GS',
    # ── Catalyst-only (not in proven list but scan for events) ──
    'CRWV', 'SMCI', 'RBRK', 'AI', 'RGTI',
    'USAR', 'FSLR', 'CCJ', 'UUUU', 'DNN',
    'LLY', 'NTLA', 'BEAM',
    'APLD', 'SOUN', 'BBAI',
    # ── Gap-and-go confirmed (5Y backtest: 56-60% WR) ────────
    'ON',   # 59% WR gap plays, $8.40 avg — semi leader
    'LRCX', # 59% WR gap plays, $7.79 avg — semi equipment
    'DDOG', # 58% WR gap plays, $8.08 avg — cloud monitoring
    'MDB',  # 56% WR gap plays, $17.41 avg — database
    # ── Momentum / sector-leader additions ───────────────────
    'POET', # photonic semiconductors — strong gap momentum
    'EOSE', # energy storage — catalyst mover, gap plays
    'INDI', # automotive semis — catalyst mover
    'NVDA', # AI/semi leader — strong sector days (dropped from gap backtest but valid swing)
    'INTC', # Intel — earnings catalyst, large-cap semi
    'TSLA', # high beta — big moves on strong days
    # DROPPED — confirmed underperformers (gap-and-go backtest):
    # SMR(24%), MSTR(17%), SNOW(33%), CRWD(33%)
    # UBER(33%), JOBY(38%), PANW(43%), MS(43%)
    # AFRM(43%), ACHR(44%), SOFI(50% neg avg), HPE(50% neg avg)
]))

# ── Top 5 sectors + symbol map ────────────────────────────
# Consolidates watchlist SECTORS into 5 tradeable groups
SECTOR_MAP = {
    # TECH: AI chips, semis, cloud, mega-cap
    'AAPL':'TECH','MSFT':'TECH','AMZN':'TECH','GOOGL':'TECH','META':'TECH',
    'AMD':'TECH','AVGO':'TECH','NFLX':'TECH','NVDA':'TECH','INTC':'TECH','TSLA':'TECH',
    'COHR':'TECH','LITE':'TECH','CLS':'TECH','SMCI':'TECH','CRWV':'TECH',
    'PLTR':'TECH','NBIS':'TECH','AI':'TECH','CRM':'TECH',
    'ORCL':'TECH','RBRK':'TECH',
    # NUCLEAR: small modular reactors, uranium
    'OKLO':'NUCLEAR','CCJ':'NUCLEAR','UUUU':'NUCLEAR','DNN':'NUCLEAR',
    # FINTECH: neo-banks, payments, trading apps, financials
    'NU':'FINTECH','RKT':'FINTECH','JPM':'FINTECH','GS':'FINTECH',
    'HOOD':'FINTECH','TOST':'FINTECH',
    # BIOTECH: pharma, gene editing
    'LLY':'BIOTECH','NTLA':'BIOTECH','BEAM':'BIOTECH','NUTX':'BIOTECH',
    # QUANTUM_CRYPTO: quantum computing + crypto infrastructure
    'IONQ':'QUANTUM_CRYPTO','QBTS':'QUANTUM_CRYPTO','RGTI':'QUANTUM_CRYPTO',
    'IREN':'QUANTUM_CRYPTO','APLD':'QUANTUM_CRYPTO',
    # Remainder → OTHER (still tradeable, just uncapped)
}

# ── Sector ETF proxies for relative strength ─────────────────
SECTOR_ETF_MAP = {
    'TECH':          'XLK',
    'FINTECH':       'XLF',
    'ENERGY':        'XLE',
    'BIOTECH':       'XBI',
    'NUCLEAR':       'NLR',
    'DEFENCE':       'ITA',
    'QUANTUM_CRYPTO':'QQQ',
    'OTHER':         'SPY',
}

def get_symbol_sector(symbol):
    return SECTOR_MAP.get(symbol, 'OTHER')

def get_open_sector_counts():
    """Count open trades per sector from DB."""
    counts = {}
    for t in get_open_trades():
        sec = get_symbol_sector(t['symbol'])
        counts[sec] = counts.get(sec, 0) + 1
    return counts

def update_sector_strength():
    """Fetch sector ETF % changes once per scan — identifies which sectors lead today."""
    global sector_strength
    etfs = list(set(SECTOR_ETF_MAP.values()))
    try:
        raw = yf.download(etfs, period='2d', interval='1d', progress=False, auto_adjust=True)
        closes = raw['Close'] if isinstance(raw.columns, pd.MultiIndex) else raw
        strength = {}
        for etf in etfs:
            try:
                col = closes[etf].dropna() if etf in closes.columns else pd.Series()
                if len(col) >= 2:
                    strength[etf] = round((float(col.iloc[-1]) - float(col.iloc[-2])) / float(col.iloc[-2]) * 100, 2)
            except Exception:
                pass
        sector_strength = strength
    except Exception as e:
        log(f"Sector strength error: {e}")

# ─────────────────────────────────────────────────────────
# PERSISTENCE HELPERS
# ─────────────────────────────────────────────────────────
def load_traded_today():
    try:
        if os.path.exists(TRADED_TODAY_FILE):
            with open(TRADED_TODAY_FILE) as f:
                data = json.load(f)
            if data.get('date') == date.today().isoformat():
                return set(data.get('symbols', []))
    except:
        pass
    return set()

def save_traded_today():
    try:
        with open(TRADED_TODAY_FILE, 'w') as f:
            json.dump({'date': date.today().isoformat(), 'symbols': list(traded_today)}, f)
    except:
        pass

# ─────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────
def log(msg):
    print(f"[{datetime.now(ET).strftime('%H:%M:%S')}] {msg}")

def send_telegram(msg):
    try:
        requests.post(f"{TG_API}/sendMessage",
                      json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg},
                      timeout=10)
        log(f"TG: {msg[:60]}")
    except Exception as e:
        log(f"TG error: {e}")

def speak(text):
    try:
        engine = pyttsx3.init()
        engine.setProperty('rate', 165)
        engine.say(text)
        engine.runAndWait()
    except Exception as e:
        log(f"Voice error: {e}")

# ─────────────────────────────────────────────────────────
# MARKET TIMING
# ─────────────────────────────────────────────────────────
def is_market_open():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    if now.hour < 9 or (now.hour == 9 and now.minute < 31):
        return False
    if now.hour >= 16:
        return False
    return True

def is_entry_window():
    now = datetime.now(ET)
    if not is_market_open():
        return False
    if now.hour < NO_ENTRY_BEFORE:
        return False
    if now.hour >= NO_ENTRY_AFTER:
        return False
    # Avoid lunch chop — institutions step away, price action is noise
    t = (now.hour, now.minute)
    if LUNCH_AVOID_START <= t < LUNCH_AVOID_END:
        return False
    return True

# ─────────────────────────────────────────────────────────
# IB HISTORICAL DATA — bridge first, yfinance fallback
# ─────────────────────────────────────────────────────────
def get_ib_daily(symbol, duration='60 D'):
    """Fetch daily OHLCV from IB bridge → pandas DataFrame."""
    try:
        r = requests.get(
            f"{BRIDGE}/history/{symbol}",
            params={'duration': duration, 'bar_size': '1 day'},
            timeout=15
        )
        bars = r.json()
        if not bars:
            return None
        df = pd.DataFrame(bars)
        df['date']  = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        df.columns  = [c.capitalize() for c in df.columns]  # Open/High/Low/Close/Volume
        return df
    except:
        return None

def get_ib_intraday(symbol, duration='5 D', bar_size='5 mins'):
    """Fetch intraday OHLCV from IB bridge → pandas DataFrame."""
    try:
        r = requests.get(
            f"{BRIDGE}/history/{symbol}",
            params={'duration': duration, 'bar_size': bar_size},
            timeout=15
        )
        bars = r.json()
        if not bars:
            return None
        df = pd.DataFrame(bars)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        df.columns = [c.capitalize() for c in df.columns]
        return df
    except:
        return None

# ─────────────────────────────────────────────────────────
# ATR — IB daily data, cached per symbol per day
# ─────────────────────────────────────────────────────────
def get_atr(symbol):
    today = date.today().isoformat()
    if symbol in atr_cache and atr_cache[symbol][0] == today:
        return atr_cache[symbol][1]
    try:
        # Try IB first (accurate exchange data)
        df = get_ib_daily(symbol, duration=f'{ATR_PERIOD + 5} D')
        if df is None or len(df) < ATR_PERIOD:
            df = yf.Ticker(symbol).history(period=f'{ATR_PERIOD + 5}d')
        high = df['High']
        low  = df['Low']
        prev = df['Close'].shift(1)
        tr   = pd.concat([high - low,
                          (high - prev).abs(),
                          (low  - prev).abs()], axis=1).max(axis=1)
        atr  = round(float(tr.rolling(ATR_PERIOD).mean().iloc[-1]), 4)
        atr_cache[symbol] = (today, atr)
        return atr
    except:
        return None

# ─────────────────────────────────────────────────────────
# REGIME — VWAP + VIX direction + QQQ breadth
# ─────────────────────────────────────────────────────────
def _bridge_df(symbol, duration='1 D', bar_size='5 mins'):
    """Fetch IBKR bars from bridge → DataFrame with DatetimeIndex."""
    r = requests.get(f"{BRIDGE}/history/{symbol}",
                     params={'duration': duration, 'bar_size': bar_size},
                     timeout=15)
    r.raise_for_status()
    bars = r.json()
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    df.rename(columns={c: c.capitalize() for c in df.columns}, inplace=True)
    if bar_size == '1 day':
        df['Date'] = pd.to_datetime(df['Date'])
    else:
        df['Date'] = pd.to_datetime(df['Date'], utc=True).dt.tz_convert(ET)
    return df.set_index('Date').sort_index()


def get_regime():
    try:
        # ── Primary signals — real-time IBKR data via bridge ─────────────────
        spy_intra = _bridge_df('SPY', '1 D', '5 mins')
        qqq_intra = _bridge_df('QQQ', '1 D', '5 mins')
        spy_daily = _bridge_df('SPY', '5 D', '1 day')

        # VIX — bridge (Index contract) with yfinance fallback if no CBOE subscription
        try:
            vix_intra = _bridge_df('VIX', '1 D', '5 mins')
            if vix_intra.empty:
                raise ValueError('empty')
        except Exception:
            vix_raw = yf.Ticker('^VIX').history(period='1d', interval='5m')
            vix_raw.index = vix_raw.index.tz_convert(ET)
            vix_intra = vix_raw

        spy_price = float(spy_intra['Close'].iloc[-1])

        # Prev close: IBKR daily bars exclude the incomplete current-day bar
        spy_prev = float(spy_daily['Close'].iloc[-1]) if not spy_daily.empty else float(spy_intra['Open'].iloc[0])
        spy_chg  = (spy_price - spy_prev) / spy_prev * 100
        vix_val  = float(vix_intra['Close'].iloc[-1])

        # SPY VWAP
        tp   = (spy_intra['High'] + spy_intra['Low'] + spy_intra['Close']) / 3
        vwap = float((tp * spy_intra['Volume']).cumsum().iloc[-1] /
                     spy_intra['Volume'].cumsum().iloc[-1])
        spy_above_vwap = spy_price > vwap

        # VIX direction (last 30 min = 6 bars)
        vix_rising = (len(vix_intra) >= 6 and
                      float(vix_intra['Close'].iloc[-1]) > float(vix_intra['Close'].iloc[-6]))

        # QQQ vs SPY — tech leading or lagging
        qqq_leading = True
        if not qqq_intra.empty and len(qqq_intra) >= 2:
            qqq_chg       = (float(qqq_intra['Close'].iloc[-1]) - float(qqq_intra['Open'].iloc[0])) / float(qqq_intra['Open'].iloc[0]) * 100
            spy_intra_chg = (spy_price - float(spy_intra['Open'].iloc[0])) / float(spy_intra['Open'].iloc[0]) * 100
            qqq_leading   = qqq_chg >= spy_intra_chg - 0.3

        # Choppiness — >40% bar reversals on a flat tape
        chop = False
        if len(spy_intra) >= 6:
            diffs   = spy_intra['Close'].diff().dropna()
            changes = sum(1 for i in range(1, len(diffs)) if diffs.iloc[i] * diffs.iloc[i-1] < 0)
            chop    = changes / len(diffs) > 0.4 and abs(spy_chg) < 0.3

        # Base regime
        if chop:
            regime = 'CHOPPY'
        elif spy_chg < -0.5 or vix_val > 28:
            regime = 'WEAK'
        elif spy_chg >= 0.5 and vix_val < 22:
            regime = 'STRONG'
        elif spy_chg >= 0 and vix_val < 25:
            regime = 'NORMAL'
        else:
            regime = 'CAUTIOUS'

        # Downgrade one level per negative signal
        order = ['STRONG', 'NORMAL', 'CAUTIOUS', 'WEAK']
        if regime not in ('CHOPPY', 'WEAK'):
            if not spy_above_vwap:
                regime = order[min(order.index(regime) + 1, len(order) - 1)]
            if vix_rising:
                regime = order[min(order.index(regime) + 1, len(order) - 1)]
            if not qqq_leading and regime == 'STRONG':
                regime = 'NORMAL'

        # ── ES/NQ futures — informational only, yfinance ok (not a regime gate) ──
        es_chg = nq_chg = 0.0
        try:
            today_d = datetime.now(ET).date()
            es_raw  = yf.Ticker('ES=F').history(period='2d', interval='5m')
            nq_raw  = yf.Ticker('NQ=F').history(period='2d', interval='5m')
            if not es_raw.empty:
                es_raw.index = es_raw.index.tz_convert(ET)
                es_prev = es_raw[es_raw.index.date < today_d]['Close']
                if not es_prev.empty:
                    es_chg = round((float(es_raw['Close'].iloc[-1]) - float(es_prev.iloc[-1])) / float(es_prev.iloc[-1]) * 100, 2)
            if not nq_raw.empty:
                nq_raw.index = nq_raw.index.tz_convert(ET)
                nq_prev = nq_raw[nq_raw.index.date < today_d]['Close']
                if not nq_prev.empty:
                    nq_chg = round((float(nq_raw['Close'].iloc[-1]) - float(nq_prev.iloc[-1])) / float(nq_prev.iloc[-1]) * 100, 2)
        except Exception:
            pass

        # ── Market breadth — IWM + MDY via bridge ─────────────────────────────
        broad_advance = True
        breadth_weak  = False
        try:
            iwm_5m    = _bridge_df('IWM', '1 D', '5 mins')
            mdy_5m    = _bridge_df('MDY', '1 D', '5 mins')
            iwm_daily = _bridge_df('IWM', '3 D', '1 day')
            mdy_daily = _bridge_df('MDY', '3 D', '1 day')
            iwm_now   = float(iwm_5m['Close'].iloc[-1])
            mdy_now   = float(mdy_5m['Close'].iloc[-1])
            iwm_prev  = float(iwm_daily['Close'].iloc[-1])
            mdy_prev  = float(mdy_daily['Close'].iloc[-1])
            iwm_chg   = (iwm_now - iwm_prev) / iwm_prev * 100
            mdy_chg   = (mdy_now - mdy_prev) / mdy_prev * 100
            broad_advance = iwm_chg > 0 and mdy_chg > 0
            breadth_weak  = iwm_chg < -0.5 and mdy_chg < -0.5
            if breadth_weak and regime not in ('CHOPPY', 'WEAK'):
                regime = order[min(order.index(regime) + 1, len(order) - 1)]
        except Exception:
            pass

        extra = {
            'vwap': round(vwap, 2), 'spy_above_vwap': spy_above_vwap,
            'vix_rising': vix_rising, 'qqq_leading': qqq_leading,
            'es_chg': es_chg, 'nq_chg': nq_chg,
            'broad_advance': broad_advance, 'breadth_weak': breadth_weak,
        }
        return regime, round(spy_chg, 2), round(vix_val, 2), extra

    except Exception as e:
        log(f"Regime error: {e}")
        return 'NORMAL', 0, 18, {}

# ─────────────────────────────────────────────────────────
# IBKR RECONCILIATION — sync DB with real positions
# ─────────────────────────────────────────────────────────
def get_ibkr_positions():
    try:
        r = requests.get(f"{BRIDGE}/portfolio", timeout=10)
        return {p['symbol']: p for p in r.json()}
    except:
        return {}

def reconcile_with_ibkr():
    ibkr = get_ibkr_positions()
    if not ibkr:
        return  # bridge unreachable — skip, don't corrupt DB

    db_trades  = get_open_trades()
    db_symbols = {t['symbol']: t for t in db_trades}

    # IBKR has position, DB doesn't → orphaned, create minimal record
    for sym, pos in ibkr.items():
        if sym not in db_symbols and pos['qty'] > 0:
            log(f"Reconcile: {sym} in IBKR but missing from DB → creating record")
            avg = pos['avgCost']
            log_trade_entry(
                symbol=sym, entry_price=avg,
                shares=int(pos['qty']),
                target_price=round(avg * 1.075, 2),
                stop_price=round(avg * 0.965, 2),
                setup_type='RECONCILED', rsi=0, volume_ratio=0,
                sector='OTHER', earnings_days=999, confidence=50,
                order_id='reconciled'
            )

    # DB has open trade, IBKR doesn't → closed externally
    for sym, trade in db_symbols.items():
        if sym not in ibkr:
            price = get_live_price(sym) or trade['entry_price']
            log(f"Reconcile: {sym} in DB but gone from IBKR → marking closed")
            log_trade_exit(trade['id'], price, 'Position closed outside auto_trader')
            if sym in open_positions:
                del open_positions[sym]

# ─────────────────────────────────────────────────────────
# LIVE PRICE
# ─────────────────────────────────────────────────────────
def get_live_price(symbol):
    try:
        r = requests.get(f"{BRIDGE}/quote/{symbol}", timeout=5)
        d = r.json()
        if d.get('best_price'):
            return d['best_price']
    except:
        pass
    try:
        df = yf.Ticker(symbol).history(period='1d', interval='1m')
        if not df.empty:
            return round(float(df['Close'].iloc[-1]), 2)
    except:
        pass
    return None

# ─────────────────────────────────────────────────────────
# INTRADAY SIGNALS
# ─────────────────────────────────────────────────────────
def get_intraday_signals(symbol, spy_chg=0):
    try:
        # 5-min intraday: yfinance (IB rate limits prevent scanning 60+ stocks every 5 min)
        df5 = yf.Ticker(symbol).history(period='5d', interval='5m')

        # Daily bars: IB first (accurate, cached 24h) → yfinance fallback
        df1d = get_ib_daily(symbol, duration='60 D')
        if df1d is None or len(df1d) < 20:
            df1d = yf.Ticker(symbol).history(period='60d', interval='1d')

        if df5.empty or df1d.empty or len(df1d) < 20:
            return None

        price     = float(df5['Close'].iloc[-1])
        open_p    = float(df5['Open'].iloc[0])
        intra_chg = (price - open_p) / open_p * 100

        avg_vol   = df1d['Volume'].rolling(20).mean().iloc[-2]
        now       = datetime.now(ET)
        mins_open = max(1, (now.hour - 9) * 60 + now.minute - 30)
        vol_ratio = (df5['Volume'].sum() * (390 / mins_open)) / avg_vol if avg_vol > 0 else 1

        close    = df1d['Close']
        ma20     = float(close.rolling(20).mean().iloc[-1])
        ema8     = float(close.ewm(span=8).mean().iloc[-1])
        ema21    = float(close.ewm(span=21).mean().iloc[-1])
        above_ma = price > ma20
        uptrend  = price > ema8 > ema21
        ema_touch = abs(price - ema21) / price * 100 < 2.5

        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rsi   = round(float(100 - (100 / (1 + gain.iloc[-1] / loss.iloc[-1]))), 1)

        # 5-min RSI — intraday exhaustion check (resets each session, much more sensitive)
        d5    = df5['Close'].diff()
        g5    = d5.clip(lower=0).rolling(14).mean()
        l5    = (-d5.clip(upper=0)).rolling(14).mean()
        rsi_5m = round(float(100 - (100 / (1 + g5.iloc[-1] / l5.iloc[-1]))), 1) if l5.iloc[-1] != 0 else 50.0

        fvg_count = 0
        h = df5['High'].values
        l = df5['Low'].values
        c = df5['Close'].values
        for i in range(1, len(df5) - 1):
            if h[i-1] < l[i+1]:
                if (l[i+1] - h[i-1]) / c[i] * 100 >= 0.15:
                    fvg_count += 1

        r_high    = float(df1d['High'].iloc[-3:].max())
        r_low     = float(df1d['Low'].iloc[-3:].min())
        range_pct = (r_high - r_low) / r_low * 100
        is_tight  = range_pct < 5
        prev_chg  = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100

        # ── VWAP (intraday, resets each session) ─────────────
        df5['typical'] = (df5['High'] + df5['Low'] + df5['Close']) / 3
        df5['vwap']    = (df5['typical'] * df5['Volume']).cumsum() / df5['Volume'].cumsum()
        vwap           = round(float(df5['vwap'].iloc[-1]), 2)
        above_vwap     = price > vwap
        # VWAP reclaim: last bar crossed above VWAP from below
        vwap_reclaim   = (len(df5) >= 2 and
                          float(df5['Close'].iloc[-1]) > float(df5['vwap'].iloc[-1]) and
                          float(df5['Close'].iloc[-2]) <= float(df5['vwap'].iloc[-2]))

        # ── Bull flag: surge → tight consolidation → breakout ─
        is_bull_flag = False
        if len(df5) >= 15:
            pole  = df5.iloc[-14:-5]   # flagpole bars
            base  = df5.iloc[-5:]      # consolidation bars
            pole_move    = (float(pole['High'].max()) - float(pole['Open'].iloc[0])) \
                           / max(float(pole['Open'].iloc[0]), 0.01) * 100
            base_high    = float(base['High'].max())
            base_low     = float(base['Low'].min())
            base_range   = (base_high - base_low) / max(float(base['Close'].mean()), 0.01) * 100
            # Pole ≥2%, base tight <2%, price near/breaking base high
            is_bull_flag = (pole_move >= 2.0 and base_range < 2.0
                            and price >= base_high * 0.998)

        # ── High of day break ─────────────────────────────────
        hod        = float(df5['High'].max())
        prior_hod  = float(df5['High'].iloc[:-2].max()) if len(df5) > 2 else hod
        hod_break  = price >= prior_hod * 0.999 and price >= hod * 0.995

        # ── Opening Range Breakout (ORB) ──────────────────────
        # Opening range = first 15 minutes (9:30–9:44am ET)
        # After 10am we check if price has broken above that range
        orb_high = orb_low = None
        orb_break = False
        try:
            df5_tz = df5.copy()
            df5_tz.index = df5_tz.index.tz_convert(ET)
            today_bars = df5_tz[df5_tz.index.date == datetime.now(ET).date()]
            orb_bars   = today_bars[
                (today_bars.index.hour == 9) & (today_bars.index.minute >= 30) |
                (today_bars.index.hour == 9) & (today_bars.index.minute <= 44)
            ]
            # Use first 15-min window: 9:30–9:44
            orb_window = today_bars[
                (today_bars.index.hour == 9) &
                (today_bars.index.minute >= 30) &
                (today_bars.index.minute < 45)
            ]
            if len(orb_window) >= 2:
                orb_high  = round(float(orb_window['High'].max()), 2)
                orb_low   = round(float(orb_window['Low'].min()), 2)
                # ORB signal only valid before 11:30am — after that it's just resistance
                now_t     = (datetime.now(ET).hour, datetime.now(ET).minute)
                orb_break = (price > orb_high and price >= orb_high * 0.998
                             and now_t < ORB_ENTRY_CUTOFF)
                # Store in global key_levels
                if symbol not in key_levels:
                    key_levels[symbol] = {}
                key_levels[symbol].update({'orb_high': orb_high, 'orb_low': orb_low})
        except Exception:
            pass

        # ── Relative strength vs SPY ──────────────────────────
        rs_vs_spy  = round(prev_chg - spy_chg, 2)   # positive = beating SPY today

        # ── Last 5m candle quality ────────────────────────────
        last_o = float(df5['Open'].iloc[-1])
        last_c = float(df5['Close'].iloc[-1])
        last_h = float(df5['High'].iloc[-1])
        last_l = float(df5['Low'].iloc[-1])
        candle_range  = last_h - last_l
        candle_body   = abs(last_c - last_o)
        body_ratio    = candle_body / candle_range if candle_range > 0 else 0
        is_bullish_candle = last_c > last_o and body_ratio >= 0.6
        is_doji           = body_ratio < 0.2
        is_hammer         = (last_c > last_o and candle_range > 0 and
                             (last_o - last_l) > candle_body * 2 and
                             (last_h - last_c) < candle_body * 0.5)

        # ── Low of day break (bear) ───────────────────────────
        lod           = float(df5['Low'].min())
        prior_lod     = float(df5['Low'].iloc[:-2].min()) if len(df5) > 2 else lod
        lod_break     = price <= prior_lod * 1.001 and price <= lod * 1.005

        # ── ORB break downward (bear) ─────────────────────────
        orb_break_down = False
        if orb_low:
            now_t2 = (datetime.now(ET).hour, datetime.now(ET).minute)
            orb_break_down = (price < orb_low and price >= orb_low * 0.998
                              and now_t2 < ORB_ENTRY_CUTOFF)

        # ── VWAP rejection (bear): rallied to VWAP then failed ─
        vwap_rejection = (len(df5) >= 2 and
                          float(df5['Close'].iloc[-1]) < float(df5['vwap'].iloc[-1]) and
                          float(df5['Close'].iloc[-2]) >= float(df5['vwap'].iloc[-2]))

        # ── Bear flag: pole down → tight consolidation → breakdown
        is_bear_flag = False
        if len(df5) >= 15:
            bpole     = df5.iloc[-14:-5]
            bbase     = df5.iloc[-5:]
            pole_drop = (float(bpole['Open'].iloc[0]) - float(bpole['Low'].min())) \
                        / max(float(bpole['Open'].iloc[0]), 0.01) * 100
            bbase_hi  = float(bbase['High'].max())
            bbase_lo  = float(bbase['Low'].min())
            bbase_rng = (bbase_hi - bbase_lo) / max(float(bbase['Close'].mean()), 0.01) * 100
            is_bear_flag = (pole_drop >= 2.0 and bbase_rng < 2.0
                            and price <= bbase_lo * 1.002)

        # ── Bearish candle ────────────────────────────────────
        is_bearish_candle = last_c < last_o and body_ratio >= 0.6

        # ── 15-min timeframe alignment ────────────────────────
        # All three must agree before entering on 5m signal
        aligned_15m      = True   # default True — don't penalise if data unavailable
        aligned_15m_bear = True
        try:
            df15 = yf.Ticker(symbol).history(period='5d', interval='15m')
            if not df15.empty:
                df15.index = df15.index.tz_convert(ET)
                df15_today = df15[df15.index.date == datetime.now(ET).date()].copy()
                if len(df15_today) >= 3:
                    df15_today['tp']   = (df15_today['High'] + df15_today['Low'] + df15_today['Close']) / 3
                    df15_today['vwap'] = (df15_today['tp'] * df15_today['Volume']).cumsum() / df15_today['Volume'].cumsum()
                    p15    = float(df15_today['Close'].iloc[-1])
                    v15    = float(df15_today['vwap'].iloc[-1])
                    e20_15 = float(df15_today['Close'].ewm(span=20).mean().iloc[-1])
                    aligned_15m      = p15 > v15 and p15 > e20_15
                    aligned_15m_bear = p15 < v15 and p15 < e20_15
        except Exception:
            pass

        return {
            'price': round(price, 2), 'intra_chg': round(intra_chg, 2),
            'prev_chg': round(prev_chg, 2), 'vol_ratio': round(vol_ratio, 2),
            'above_ma': above_ma, 'uptrend': uptrend, 'ema_touch': ema_touch,
            'rsi': rsi, 'rsi_5m': rsi_5m, 'fvg_count': fvg_count,
            'is_tight': is_tight, 'range_pct': round(range_pct, 2),
            'vwap': vwap, 'above_vwap': above_vwap, 'vwap_reclaim': vwap_reclaim,
            'is_bull_flag': is_bull_flag, 'hod_break': hod_break,
            'orb_break': orb_break, 'orb_high': orb_high, 'orb_low': orb_low,
            'lod_break': lod_break, 'orb_break_down': orb_break_down,
            'vwap_rejection': vwap_rejection, 'is_bear_flag': is_bear_flag,
            'rs_vs_spy': rs_vs_spy,
            'is_bullish_candle': is_bullish_candle, 'is_hammer': is_hammer,
            'is_bearish_candle': is_bearish_candle,
            'is_doji': is_doji, 'aligned_15m': aligned_15m,
            'aligned_15m_bear': aligned_15m_bear,
        }
    except:
        return None

# ─────────────────────────────────────────────────────────
# STOP / TARGET — ATR-based, adapts to each stock's volatility
# ─────────────────────────────────────────────────────────
def calc_sl_target(symbol, price, side='LONG'):
    risk_pct = 5.0
    reward   = risk_pct * MIN_RR   # 12.5% display target — no profit cap, strategy rides trail
    if side == 'LONG':
        sl     = round(price * 0.95, 2)
        target = round(price * (1 + reward / 100), 2)
    else:
        sl     = round(price * 1.05, 2)
        target = round(price * (1 - reward / 100), 2)
    return sl, target, risk_pct, round(reward, 2), MIN_RR

# ─────────────────────────────────────────────────────────
# EARNINGS HELPER
# ─────────────────────────────────────────────────────────
def get_days_to_earnings(symbol):
    today_str = date.today().isoformat()
    if symbol in earnings_cache and earnings_cache[symbol][0] == today_str:
        return earnings_cache[symbol][1]
    days = None
    try:
        cal = yf.Ticker(symbol).calendar
        if isinstance(cal, pd.DataFrame) and not cal.empty:
            for col in cal.columns:
                try:
                    ed = pd.Timestamp(col).date()
                    d  = (ed - date.today()).days
                    if d >= -1:
                        days = d
                        break
                except Exception:
                    continue
        elif isinstance(cal, dict):
            for key in ('Earnings Date', 'earningsDate'):
                val = cal.get(key)
                if val:
                    try:
                        ed = pd.Timestamp(val[0] if isinstance(val, list) else val).date()
                        d  = (ed - date.today()).days
                        if d >= -1:
                            days = d
                    except Exception:
                        pass
                    break
    except Exception:
        pass
    earnings_cache[symbol] = (today_str, days)
    return days

# ─────────────────────────────────────────────────────────
# GRADE SETUP
# ─────────────────────────────────────────────────────────
def grade_setup(sig, regime, sl, target, price, rr, symbol=None):
    score   = 0
    reasons = []

    # Earnings gate — hard skip within 3 days: IV crush, gap risk, binary event
    if symbol:
        dte = get_days_to_earnings(symbol)
        if dte is not None and 0 <= dte <= 3:
            return 'SKIP', [f'Earnings in {dte}d — skip binary event'], 0

    if not sig['above_ma']:
        return 'SKIP', ['Below MA'], 0
    # 5m RSI — scoring only, not a hard gate (same principle as daily RSI)
    # High 5m RSI = late to the party but can still run; penalise rather than block
    # Large-caps (>$100) are liquid even at 1x volume — lower threshold
    vol_threshold = 1.0 if sig.get('price', 0) > 100 else MIN_VOLUME_RATIO
    if sig['vol_ratio'] < vol_threshold:
        return 'SKIP', [f'Volume {sig["vol_ratio"]:.1f}x too low'], 0
    if rr < MIN_RR:
        return 'SKIP', [f'R:R 1:{rr} below min 1:{MIN_RR}'], 0
    if regime in ('CHOPPY', 'CAUTIOUS'):
        return 'SKIP', [f'{regime} — no trades'], 0
    # Must be moving today — no entering flat or declining stocks
    today_gain = sig.get('prev_chg', 0)
    if today_gain < MIN_TODAY_GAIN:
        return 'SKIP', [f'Only +{today_gain:.1f}% today (need ≥{MIN_TODAY_GAIN}%)'], 0

    if sig['fvg_count'] >= 10:
        score += 30; reasons.append(f'{sig["fvg_count"]} FVGs')
    elif sig['fvg_count'] >= 5:
        score += 20; reasons.append(f'{sig["fvg_count"]} FVGs')
    elif sig['fvg_count'] >= 1:
        score += 10; reasons.append(f'{sig["fvg_count"]} FVGs')

    if sig['vol_ratio'] >= 2.0:
        score += 25; reasons.append(f'{sig["vol_ratio"]:.1f}x vol')
    elif sig['vol_ratio'] >= 1.5:
        score += 15; reasons.append(f'{sig["vol_ratio"]:.1f}x vol')
    else:
        score += 5;  reasons.append(f'{sig["vol_ratio"]:.1f}x vol')

    if sig['uptrend'] and sig['ema_touch']:
        score += 20; reasons.append('EMA pullback in uptrend')
    elif sig['uptrend']:
        score += 10; reasons.append('Uptrend')

    # Daily RSI — scoring only, no longer a hard gate (high RSI = momentum, not overbought)
    if 45 <= sig['rsi'] <= 65:
        score += 20; reasons.append(f'RSI {sig["rsi"]} ideal')
    elif 65 < sig['rsi'] <= 80:
        score += 10; reasons.append(f'RSI {sig["rsi"]} elevated (momentum)')
    else:
        score += 5;  reasons.append(f'RSI {sig["rsi"]} (trending)')

    # 5m RSI — scoring only, penalise exhaustion but don't block
    rsi5m = sig.get('rsi_5m', 50)
    if rsi5m > MAX_RSI_5M:
        score -= 20; reasons.append(f'5m RSI {rsi5m} exhausted (-20)')
    elif rsi5m > 75:
        score -= 10; reasons.append(f'5m RSI {rsi5m} elevated (-10)')

    if sig['is_tight']:
        score += 10; reasons.append(f'Tight range {sig["range_pct"]:.1f}%')

    # Reward stocks already moving strongly today
    if today_gain >= 5.0:
        score += 30; reasons.append(f'+{today_gain:.1f}% today')
    elif today_gain >= 3.0:
        score += 20; reasons.append(f'+{today_gain:.1f}% today')
    elif today_gain >= 1.5:
        score += 10; reasons.append(f'+{today_gain:.1f}% today')

    # ── Price action gate — must have at least one pro pattern ──
    # Exception: strong momentum (up 5%+ AND beating SPY by 3%+) is itself the signal
    rs          = sig.get('rs_vs_spy', 0)
    strong_momo = today_gain >= 5.0 and rs >= 3.0
    has_pattern = (sig.get('vwap_reclaim') or sig.get('is_bull_flag')
                   or sig.get('hod_break') or sig.get('orb_break') or strong_momo)
    if not has_pattern:
        return 'SKIP', ['No pattern — wait for ORB/VWAP reclaim/bull flag/HOD break'], 0

    # ── Score the pattern ──────────────────────────────────────
    if sig.get('orb_break'):
        score += 30; reasons.append('ORB breakout ✓')   # most reliable — scored highest

    if sig.get('vwap_reclaim'):
        score += 25; reasons.append('VWAP reclaim ✓')
    elif sig.get('above_vwap'):
        score += 10; reasons.append('Above VWAP')

    if sig.get('is_bull_flag'):
        score += 25; reasons.append('Bull flag ✓')

    if sig.get('hod_break'):
        score += 20; reasons.append('HOD break ✓')

    # ── Relative strength vs SPY ──────────────────────────────
    rs = sig.get('rs_vs_spy', 0)
    if rs >= 5:
        score += 20; reasons.append(f'RS +{rs:.1f}% vs SPY')
    elif rs >= 2:
        score += 10; reasons.append(f'RS +{rs:.1f}% vs SPY')
    elif rs < 0:
        score -= 10; reasons.append(f'RS {rs:.1f}% vs SPY (lagging)')

    # ── Pre-market high — cleared = overnight resistance gone, strong confirmation ──
    if symbol and symbol in key_levels:
        pm_high = key_levels[symbol].get('pm_high')
        if pm_high:
            if price >= pm_high * 1.001:
                score += 15; reasons.append(f'Above PM high ${pm_high} ✓')
            elif price >= pm_high * 0.998:
                score += 5;  reasons.append(f'Testing PM high ${pm_high}')
            else:
                score -= 5;  reasons.append(f'Below PM high ${pm_high} (resistance)')

    # ── Sector ETF strength ───────────────────────────────────
    if symbol and sector_strength:
        sec = get_symbol_sector(symbol)
        etf = SECTOR_ETF_MAP.get(sec, 'SPY')
        etf_chg = sector_strength.get(etf, 0)
        if etf_chg >= 1.5:
            score += 15; reasons.append(f'{etf} +{etf_chg:.1f}% leading')
        elif etf_chg >= 0.5:
            score += 5;  reasons.append(f'{etf} +{etf_chg:.1f}%')
        elif etf_chg <= -1.0:
            score -= 10; reasons.append(f'{etf} {etf_chg:.1f}% weak sector')

    if regime == 'STRONG':
        score += 15; reasons.append('Strong market')
    elif regime == 'NORMAL':
        score += 5

    if rr >= 4:
        score += 10; reasons.append(f'R:R 1:{rr} excellent')
    elif rr >= MIN_RR:
        score += 5;  reasons.append(f'R:R 1:{rr}')

    # ── Candlestick quality ───────────────────────────────────
    if sig.get('is_hammer'):
        score += 15; reasons.append('Hammer candle — reversal confirmation ✓')
    elif sig.get('is_bullish_candle'):
        score += 10; reasons.append('Strong bullish candle ✓')
    elif sig.get('is_doji') and sig.get('above_vwap'):
        score -= 5;  reasons.append('Doji at key level — indecision (-5)')

    # ── 15-minute timeframe alignment ────────────────────────
    if sig.get('aligned_15m') is True:
        score += 10; reasons.append('15m aligned (price > 15m VWAP & EMA20) ✓')
    elif sig.get('aligned_15m') is False:
        score -= 15; reasons.append('15m counter-trend (-15)')

    grade = 'A+' if score >= 80 else 'A' if score >= 65 else 'B' if score >= 50 else 'C'
    return grade, reasons, score

# ─────────────────────────────────────────────────────────
# GRADE BEAR SETUP — inverted signal stack for shorts
# ─────────────────────────────────────────────────────────
def grade_bear_setup(sig, regime, sl, target, price, rr, symbol=None):
    score   = 0
    reasons = []

    # Earnings gate — same hard skip (earnings = binary event, both directions)
    if symbol:
        dte = get_days_to_earnings(symbol)
        if dte is not None and 0 <= dte <= 3:
            return 'SKIP', [f'Earnings in {dte}d — skip binary event'], 0

    # Hard gates (inverted from bull)
    if sig['above_ma']:
        return 'SKIP', ['Above MA20 — not a short candidate'], 0
    vol_threshold = 1.0 if sig.get('price', 0) > 100 else MIN_VOLUME_RATIO
    if sig['vol_ratio'] < vol_threshold:
        return 'SKIP', [f'Volume {sig["vol_ratio"]:.1f}x too low'], 0
    if rr < MIN_RR:
        return 'SKIP', [f'R:R 1:{rr} below min 1:{MIN_RR}'], 0
    if regime != 'WEAK':
        return 'SKIP', [f'{regime} — bear engine only runs on WEAK days'], 0
    today_chg = sig.get('prev_chg', 0)
    if today_chg > -MIN_TODAY_GAIN:
        return 'SKIP', [f'Only {today_chg:.1f}% today (need ≤-{MIN_TODAY_GAIN}%)'], 0

    # ── Entry signal scoring ──────────────────────────────
    if sig.get('orb_break_down'):
        score += 25; reasons.append('ORB break down')
    if sig.get('vwap_rejection'):
        score += 25; reasons.append('VWAP rejection')
    elif not sig['above_vwap']:
        score += 10; reasons.append('Below VWAP')
    if sig.get('lod_break'):
        score += 20; reasons.append('LOD break')
    if sig.get('is_bear_flag'):
        score += 20; reasons.append('Bear flag')

    # FVG count (gaps down = air pockets to fill)
    if sig['fvg_count'] >= 10:
        score += 20; reasons.append(f'{sig["fvg_count"]} FVGs (downside)')
    elif sig['fvg_count'] >= 5:
        score += 10; reasons.append(f'{sig["fvg_count"]} FVGs')

    # Daily RSI — weak RSI supports short
    rsi = sig['rsi']
    if rsi < 30:
        score += 15; reasons.append(f'RSI {rsi} (deeply oversold momentum)')
    elif rsi < 45:
        score += 10; reasons.append(f'RSI {rsi} (weak)')
    elif rsi > 60:
        score -= 15; reasons.append(f'RSI {rsi} (too strong — avoid short)')

    # 5m RSI: if < 20, bounce risk is high
    rsi_5m = sig.get('rsi_5m', 50)
    if rsi_5m < 20:
        score -= 15; reasons.append(f'5m RSI {rsi_5m} (oversold — bounce risk)')
    elif rsi_5m < 35:
        score += 5;  reasons.append(f'5m RSI {rsi_5m} (weak intraday)')

    # Volume — selling pressure confirmation
    vol = sig['vol_ratio']
    if vol >= 5:
        score += 20; reasons.append(f'Vol {vol:.1f}x surge (distribution)')
    elif vol >= 3:
        score += 12; reasons.append(f'Vol {vol:.1f}x')
    elif vol >= 2:
        score += 6;  reasons.append(f'Vol {vol:.1f}x')

    # Today's decline magnitude
    if today_chg <= -5:
        score += 20; reasons.append(f'{today_chg:.1f}% strong distribution')
    elif today_chg <= -3:
        score += 12; reasons.append(f'{today_chg:.1f}% declining')
    elif today_chg <= -1.5:
        score += 6;  reasons.append(f'{today_chg:.1f}% declining')

    # Relative weakness vs SPY (negative = weaker than market = better short)
    rs = sig.get('rs_vs_spy', 0)
    if rs <= -2:
        score += 15; reasons.append(f'RS {rs:+.1f}% (weak vs SPY)')
    elif rs <= -1:
        score += 8;  reasons.append(f'RS {rs:+.1f}%')
    elif rs >= 2:
        score -= 10; reasons.append(f'RS {rs:+.1f}% (outperforming — avoid short)')

    # Sector weakness (ETF down = tailwind for short)
    if symbol and sector_strength:
        sec     = get_symbol_sector(symbol)
        etf     = SECTOR_ETF_MAP.get(sec, 'SPY')
        etf_chg = sector_strength.get(etf, 0)
        if etf_chg <= -1.5:
            score += 15; reasons.append(f'{etf} {etf_chg:.1f}% weak sector')
        elif etf_chg <= -0.5:
            score += 5;  reasons.append(f'{etf} {etf_chg:.1f}%')
        elif etf_chg >= 1.0:
            score -= 10; reasons.append(f'{etf} +{etf_chg:.1f}% strong sector (risk)')

    # WEAK regime confirmation
    score += 15; reasons.append('WEAK regime confirmed')

    # Bearish candle
    if sig.get('is_bearish_candle'):
        score += 10; reasons.append('Bearish candle')
    elif sig.get('is_doji') and not sig.get('above_vwap'):
        score -= 5;  reasons.append('Doji — indecision')

    # 15m bear alignment (price < 15m VWAP and < EMA20)
    if sig.get('aligned_15m_bear') is True:
        score += 10; reasons.append('15m bear-aligned (below VWAP & EMA20) ✓')
    elif sig.get('aligned_15m_bear') is False:
        score -= 10; reasons.append('15m not bear-aligned')

    if rr >= 4:
        score += 10; reasons.append(f'R:R 1:{rr} excellent')
    elif rr >= MIN_RR:
        score += 5;  reasons.append(f'R:R 1:{rr}')

    grade = 'A+' if score >= 80 else 'A' if score >= 65 else 'B' if score >= 50 else 'C'
    return grade, reasons, score

# ─────────────────────────────────────────────────────────
# PLACE TRADE
# ─────────────────────────────────────────────────────────
def place_trade(symbol, price, shares, sl, target, strategy, grade,
                rsi=0, vol_ratio=0, confidence=75, sector='OTHER', side='LONG'):
    try:
        order_side = 'BUY' if side == 'LONG' else 'SELL'
        r = requests.post(f"{BRIDGE}/order", json={
            'symbol': symbol, 'qty': shares,
            'side': order_side, 'order_type': 'MARKET'
        }, timeout=10)
        order_id = r.json().get('orderId')
        if not order_id:
            log(f"  {symbol}: No orderId returned — order submission failed")
            return None

        # Poll for fill confirmation (3 attempts × 2s = 6s max)
        filled = False
        for _ in range(3):
            time.sleep(2)
            try:
                r2 = requests.get(f"{BRIDGE}/order/{order_id}/status", timeout=5)
                d  = r2.json()
                if d.get('status') == 'Filled':
                    filled = True
                    break
                if d.get('status') in ('Cancelled', 'Inactive'):
                    log(f"  {symbol}: Order {order_id} {d['status']} — not recording")
                    return None
            except Exception:
                pass
        if not filled:
            log(f"  {symbol}: Fill not confirmed after 6s — skipping DB entry")
            return None

        trade_id = log_trade_entry(
            symbol=symbol, entry_price=price, shares=shares,
            target_price=target, stop_price=sl, setup_type=strategy,
            rsi=rsi, volume_ratio=vol_ratio, sector=sector,
            earnings_days=999, confidence=confidence, order_id=str(order_id),
            side=side
        )
        if trade_id:
            trade_entry_times[trade_id] = datetime.now(ET)
        return trade_id
    except Exception as e:
        log(f"Place trade error {symbol}: {e}")
        return None

# ─────────────────────────────────────────────────────────
# MONITOR OPEN TRADES — handles both LONG and SHORT positions
# ─────────────────────────────────────────────────────────
def monitor_open_trades(regime='NORMAL'):
    trades = get_open_trades()
    if not trades:
        return []

    exits = []
    now   = datetime.now(ET)

    for trade in trades:
        tid    = trade['id']
        sym    = trade['symbol']
        shares = trade['shares']
        entry  = trade['entry_price']
        sl     = trade['stop_price']
        side   = trade.get('side', 'LONG')
        is_short = (side == 'SHORT')

        price = get_live_price(sym)
        if not price:
            continue

        if tid not in price_history:
            price_history[tid] = []
        price_history[tid].append(price)
        if len(price_history[tid]) > 20:
            price_history[tid].pop(0)

        if is_short:
            session_low[tid] = min(session_low.get(tid, price), price)
            pnl_pct = (entry - price) / entry * 100   # positive when price drops
            pnl_usd = (entry - price) * shares
        else:
            session_high[tid] = max(session_high.get(tid, price), price)
            pnl_pct = (price - entry) / entry * 100
            pnl_usd = (price - entry) * shares

        atr = get_atr(sym) or (entry * 0.02)

        # ── ATR trailing stop ─────────────────────────────────
        trail_mult = ATR_TRAIL_MULT
        if is_short:
            trail_threshold = entry - atr              # 1 ATR of profit on short
            if price <= trail_threshold:
                atr_trail = round(session_low[tid] + trail_mult * atr, 2)
                if atr_trail < sl:                     # SL moves down for shorts
                    sl = atr_trail
                    update_trade_stop(tid, sl)
                    log(f"  {sym} ↓SHORT: ATR trail → ${sl} ({pnl_pct:+.1f}%)")
        else:
            trail_threshold = entry + atr
            if price >= trail_threshold:
                atr_trail = round(session_high[tid] - trail_mult * atr, 2)
                if atr_trail > sl:
                    sl = atr_trail
                    update_trade_stop(tid, sl)
                    log(f"  {sym}: ATR trail → ${sl} ({pnl_pct:+.1f}%)")

        # ── Break-even stop: once +2.5% profit ───────────────
        if pnl_pct >= 2.5:
            if is_short and sl > entry:                # SL already above entry = ok
                risk_dist = sl - entry
                be_sl = round(entry - max(risk_dist * 0.5, 0.05), 2)
                if be_sl < sl:
                    sl = be_sl
                    update_trade_stop(tid, sl)
                    log(f"  {sym} ↓SHORT: Break-even → ${sl} ({pnl_pct:+.1f}%)")
            elif not is_short and sl < entry:
                risk_dist = entry - sl
                be_sl = round(entry + max(risk_dist * 0.5, 0.05), 2)
                if be_sl > sl:
                    sl = be_sl
                    update_trade_stop(tid, sl)
                    log(f"  {sym}: Break-even stop → ${sl} ({pnl_pct:+.1f}%)")

        # ── Fetch 5-min bars ──────────────────────────────────
        df5             = None
        vwap_val        = None
        above_vwap      = None
        prev_above_vwap = None
        if is_market_open():
            try:
                df5 = yf.Ticker(sym).history(period='1d', interval='5m')
                if len(df5) >= 3:
                    df5['typical'] = (df5['High'] + df5['Low'] + df5['Close']) / 3
                    df5['vwap']    = (df5['typical'] * df5['Volume']).cumsum() / df5['Volume'].cumsum()
                    vwap_val        = round(float(df5['vwap'].iloc[-1]), 2)
                    above_vwap      = float(df5['Close'].iloc[-1]) > float(df5['vwap'].iloc[-1])
                    prev_above_vwap = float(df5['Close'].iloc[-2]) > float(df5['vwap'].iloc[-2])
            except Exception:
                pass

        # ── 5-min trailing stop ───────────────────────────────
        if is_market_open() and pnl_pct >= 3.0 and df5 is not None and len(df5) >= 3:
            try:
                if is_short:
                    intra_trail = round(float(df5['High'].iloc[-3:-1].max()), 2)
                    if intra_trail < sl:
                        sl = intra_trail
                        update_trade_stop(tid, sl)
                        log(f"  {sym} ↓SHORT: 5m trail → ${sl} ({pnl_pct:+.1f}%)")
                else:
                    intra_trail = round(float(df5['Low'].iloc[-3:-1].min()), 2)
                    if intra_trail > sl:
                        sl = intra_trail
                        update_trade_stop(tid, sl)
                        log(f"  {sym}: 5m trail → ${sl} ({pnl_pct:+.1f}%)")
            except Exception:
                pass

        # ── Partial exit at 1R (5% gain) ─────────────────────
        if (is_market_open() and pnl_pct >= 5.0
                and tid not in partial_done_trades and shares >= 2):
            half = shares // 2
            cover_side = 'BUY' if is_short else 'SELL'
            try:
                requests.post(f"{BRIDGE}/order", json={
                    'symbol': sym, 'qty': half,
                    'side': cover_side, 'order_type': 'MARKET'
                }, timeout=10)
                locked = round(pnl_usd / shares * half, 2)
                partial_done_trades[tid] = locked
                update_trade_shares(tid, shares - half)
                tag = '↓SHORT' if is_short else ''
                log(f"  {sym} {tag}: PARTIAL EXIT {half}sh @ ${price} +${locked:.0f} locked — trailing {shares - half}sh")
                exits.append({'sym': sym, 'price': price, 'entry': entry,
                              'pnl': locked, 'pnl_pct': pnl_pct, 'pnl_usd': locked,
                              'reason': f'Partial exit 50% at 1R ({pnl_pct:+.1f}%)', 'side': side})
                shares = shares - half
            except Exception as e:
                log(f"Partial exit error {sym}: {e}")

        # ── Exit decisions ────────────────────────────────────
        exit_reason = None

        # 1. Hard stop
        if is_short:
            if price >= sl:
                exit_reason = f'Short stop ${sl} hit ({pnl_pct:+.1f}% / ${pnl_usd:+.0f})'
        else:
            if price <= sl:
                exit_reason = f'Stop ${sl} hit ({pnl_pct:+.1f}% / ${pnl_usd:+.0f})'

        # 2. Circuit breaker
        if not exit_reason and pnl_usd <= -MAX_LOSS_PER_TRADE:
            exit_reason = f'Circuit breaker: -${MAX_LOSS_PER_TRADE} hit (${pnl_usd:+.0f})'

        # 3. VWAP signal: cross above = cover short / cross below = exit long
        if not exit_reason and is_market_open() and pnl_pct > 0.5 and above_vwap is not None:
            if is_short and above_vwap and prev_above_vwap is False:
                exit_reason = f'VWAP cross above ${vwap_val} — short momentum gone ({pnl_pct:+.1f}%)'
            elif not is_short and not above_vwap and prev_above_vwap is True:
                exit_reason = f'VWAP cross below ${vwap_val} ({pnl_pct:+.1f}% / ${pnl_usd:+.0f})'

        # 4. Momentum fade (bounce for shorts, drop for longs)
        if not exit_reason and pnl_pct > 0.3:
            if is_short:
                rise = price - session_low.get(tid, price)
                if rise > ATR_FADE_MULT * atr:
                    exit_reason = f'Short fade {ATR_FADE_MULT}×ATR bounce ({pnl_pct:+.1f}%)'
            else:
                drop = session_high.get(tid, price) - price
                if drop > ATR_FADE_MULT * atr:
                    exit_reason = f'Momentum fade {ATR_FADE_MULT}×ATR from high ({pnl_pct:+.1f}%)'

        # 5. No-move exit (same 240min window both directions)
        if not exit_reason and is_market_open():
            entry_dt = trade_entry_times.get(tid)
            if entry_dt:
                mins_held = (now - entry_dt).total_seconds() / 60
                if mins_held >= 240 and -0.3 <= pnl_pct <= 2.0:
                    exit_reason = f'No-move exit: flat {mins_held:.0f}min ({pnl_pct:+.1f}%)'

        # 6. EOD close — shorts ALWAYS cover (no overnight shorts)
        if not exit_reason and is_market_open() and (now.hour, now.minute) >= (EOD_CLOSE_HOUR, EOD_CLOSE_MINUTE):
            if is_short:
                exit_reason = f'EOD: cover short — no overnight shorts ({pnl_pct:+.1f}%)'
            else:
                conviction = pnl_pct > 1.5 and above_vwap is True
                if not conviction:
                    vwap_tag   = 'above VWAP' if above_vwap else 'below VWAP'
                    exit_reason = f'EOD: no overnight conviction ({pnl_pct:+.1f}% {vwap_tag})'

        # 7. Hard time stop (longs only — shorts already covered EOD)
        if not exit_reason and not is_short:
            entry_date = get_trade_entry_date(tid)
            if entry_date:
                bdays_held = int(np.busday_count(entry_date, date.today().isoformat()))
                if bdays_held >= MAX_HOLD_DAYS:
                    exit_reason = f'Max hold {bdays_held}bd — time exit ({pnl_pct:+.1f}%)'

        direction_tag = ' ↓SHORT' if is_short else ''
        vwap_tag = f' VWAP${vwap_val}' if vwap_val else ''
        log(f"  {sym}{direction_tag}: ${price} ({pnl_pct:+.1f}% / ${pnl_usd:+.0f}) SL=${sl}{vwap_tag} ATR=${atr:.2f} "
            f"→ {'EXIT' if exit_reason else 'HOLD'}")

        if exit_reason:
            try:
                ibkr_pos = get_ibkr_positions()
                ibkr_qty = abs(ibkr_pos.get(sym, {}).get('qty', 0) or 0)
                if ibkr_qty <= 0:
                    log(f"  {sym}: SKIP exit — no IBKR position, closing DB only")
                    log_trade_exit(tid, price, 'DB cleanup — no IBKR position')
                    open_positions.pop(sym, None)
                    continue
                close_side = 'BUY' if is_short else 'SELL'
                close_qty  = min(shares, int(ibkr_qty))
                requests.post(f"{BRIDGE}/order", json={
                    'symbol': sym, 'qty': close_qty,
                    'side': close_side, 'order_type': 'MARKET'
                }, timeout=10)
                pnl = log_trade_exit(tid, price, exit_reason)
                exits.append({
                    'sym': sym, 'price': price, 'entry': entry,
                    'pnl': pnl, 'pnl_pct': pnl_pct, 'pnl_usd': round(pnl_usd, 2),
                    'reason': exit_reason, 'side': side
                })
                for d in (price_history, session_high, session_low, open_positions):
                    d.pop(tid if tid in d else sym, None)
            except Exception as e:
                log(f"Exit error {sym}: {e}")

    return exits

# ─────────────────────────────────────────────────────────
# TELEGRAM COMMAND HANDLER
# ─────────────────────────────────────────────────────────
def poll_telegram_commands():
    global tg_update_id
    try:
        r = requests.get(f"{TG_API}/getUpdates",
                         params={'offset': tg_update_id, 'timeout': 0},
                         timeout=5)
        updates = r.json().get('result', [])
        for update in updates:
            tg_update_id = update['update_id'] + 1
            text = update.get('message', {}).get('text', '').strip().upper()
            if not text:
                continue
            log(f"TG command: {text}")

            if text == 'STATUS':
                ibkr    = get_ibkr_positions()
                wr      = get_win_rate(days=30)
                # Live P&L: sum unrealised from IBKR + realised from DB today
                live_upnl   = sum(p.get('unrealizedPnL', 0) or 0 for p in ibkr.values())
                daily_rpnl  = get_daily_pnl()
                total_pnl   = round(live_upnl + daily_rpnl['pnl'], 2)
                total_invest = sum(
                    (p.get('avgCost', 0) or 0) * abs(p.get('qty', 0) or 0)
                    for p in ibkr.values()
                )
                lines = [f"Status | {datetime.now(ET).strftime('%H:%M ET')}",
                         f"Total P&L: ${total_pnl:+.2f} | 30d WR: {wr:.0f}%",
                         f"Realised: ${daily_rpnl['pnl']:+.2f} ({daily_rpnl['trades']} trades, {daily_rpnl['wins']}W)",
                         f"Unrealised: ${live_upnl:+.2f} across {len(ibkr)} positions",
                         f"Open: {len(ibkr)} | Invested: ${total_invest:,.0f}",
                         "---"]
                for sym, pos in sorted(ibkr.items()):
                    qty     = abs(pos.get('qty', 0) or 0)
                    avg     = pos.get('avgCost', 0) or 0
                    mkt     = pos.get('marketPrice', 0) or 0
                    invested = round(avg * qty, 0)
                    upnl    = pos.get('unrealizedPnL', 0) or 0
                    pnl_pct = ((mkt - avg) / avg * 100) if avg else 0
                    lines.append(f"  {sym}: ${mkt} ({pnl_pct:+.1f}%) "
                                 f"uPnL ${upnl:+.2f} | ${invested:,.0f} ({qty} stock)")
                send_telegram('\n'.join(lines))

            elif text in ('CANCEL', 'STOP', 'PAUSE'):
                send_telegram("New entries paused. Monitoring open trades only. Send RESUME to re-enable.")
                with open(os.path.join(_DIR, 'trading_blocked.json'), 'w') as f:
                    json.dump({'date': date.today().isoformat(), 'blocked': True,
                               'reason': 'User sent CANCEL via Telegram'}, f)

            elif text == 'RESUME':
                block_file = os.path.join(_DIR, 'trading_blocked.json')
                if os.path.exists(block_file):
                    os.remove(block_file)
                send_telegram("Trading resumed. Scanning for setups.")

    except Exception as e:
        log(f"TG poll error: {e}")

def is_trading_blocked():
    block_file = os.path.join(_DIR, 'trading_blocked.json')
    if not os.path.exists(block_file):
        return False, None
    try:
        with open(block_file) as f:
            data = json.load(f)
        if data.get('date') == date.today().isoformat():
            return data.get('blocked', False), data.get('reason')
    except:
        pass
    return False, None

# ─────────────────────────────────────────────────────────
# MAIN SCAN
# ─────────────────────────────────────────────────────────
def run_scan():
    global daily_trade_count, traded_today, regime_history, spy_open_price

    # Reconcile DB with IBKR first
    reconcile_with_ibkr()

    # Update sector ETF strengths once per scan cycle
    update_sector_strength()

    regime, spy_chg, vix, extra = get_regime()
    open_trades = get_open_trades()
    daily       = get_daily_pnl()

    # Track regime stability — rolling window of last N readings
    regime_history.append(regime)
    if len(regime_history) > 6:
        regime_history.pop(0)
    confirmed_scans = sum(1 for r in reversed(regime_history) if r == regime)

    # Track SPY price from market open — set once on first post-open scan
    now = datetime.now(ET)
    if is_market_open() and spy_open_price is None:
        try:
            spy_data = yf.Ticker('SPY').history(period='1d', interval='1m')
            if not spy_data.empty:
                first_bar_date = spy_data.index[0].date()
                if first_bar_date == now.date():
                    spy_open_price = round(float(spy_data['Open'].iloc[0]), 2)
                    log(f"SPY open price locked: ${spy_open_price}")
                else:
                    log(f"SPY open: skipped — data is from {first_bar_date}, not today")
        except Exception:
            pass

    spy_above_open = True
    if spy_open_price:
        try:
            spy_now = yf.Ticker('SPY').history(period='1d', interval='1m')['Close'].iloc[-1]
            spy_above_open = float(spy_now) >= spy_open_price * 0.998  # allow 0.2% noise
        except Exception:
            pass

    vwap_str   = f"VWAP {'↑' if extra.get('spy_above_vwap') else '↓'}"
    vix_str    = f"VIX {vix:.1f}{'↑' if extra.get('vix_rising') else '↓'}"
    qqq_str    = f"QQQ {'lead' if extra.get('qqq_leading') else 'lag'}"
    es_chg     = extra.get('es_chg', 0)
    nq_chg     = extra.get('nq_chg', 0)
    fut_str    = f"ES {es_chg:+.1f}% NQ {nq_chg:+.1f}%"
    breadth_str = 'breadth ✓' if extra.get('broad_advance') else ('breadth WEAK' if extra.get('breadth_weak') else 'breadth ~')
    log(f"\n{'='*55}")
    log(f"SCAN | Regime: {regime} (x{confirmed_scans}) | SPY {spy_chg:+.1f}% {'↑open' if spy_above_open else '↓open'} | {vwap_str} | {vix_str} | {qqq_str}")
    log(f"      Futures: {fut_str} | {breadth_str}")
    # Live session P&L = realized today + current unrealized
    try:
        portfolio_snap = requests.get(f"{BRIDGE}/portfolio", timeout=8).json()
        unrealized_now = sum(p.get('unrealizedPnL', 0) or 0 for p in portfolio_snap)
    except Exception:
        unrealized_now = 0
    session_pnl = daily['pnl'] + unrealized_now

    log(f"Open: {len(open_trades)} | Trades today: {daily_trade_count} | Realized: ${daily['pnl']:+.2f} | Session: ${session_pnl:+.2f}")

    # Always monitor open trades
    exits = []
    if not is_entry_window():
        log("Outside entry window — monitoring only")
        exits = monitor_open_trades(regime)
    elif is_trading_blocked()[0]:
        log(f"Trading blocked — monitoring only")
        exits = monitor_open_trades(regime)
    elif regime == 'CHOPPY':
        log(f"CHOPPY market — monitoring only, no new entries")
        exits = monitor_open_trades(regime)
    elif regime == 'WEAK':
        log(f"WEAK market — routing to bear strategy (short scan)")
        exits = _scan_and_enter_bear(regime, spy_chg, open_trades, confirmed_scans)
    elif not spy_above_open:
        log(f"SPY below open price (${spy_open_price}) — no new longs until market recovers")
        exits = monitor_open_trades(regime)
    elif len(open_trades) >= MAX_OPEN_TRADES:
        log(f"Max open trades ({MAX_OPEN_TRADES}) — monitoring only")
        exits = monitor_open_trades(regime)
    elif daily_trade_count >= MAX_DAILY_TRADES:
        log(f"Max daily trades ({MAX_DAILY_TRADES}) reached")
        exits = monitor_open_trades(regime)
    elif session_pnl >= DAILY_PROFIT_TARGET:
        log(f"✅ Daily target +${session_pnl:.0f} hit — protecting gains, no new entries")
        exits = monitor_open_trades(regime)
    elif confirmed_scans < MIN_REGIME_SCANS:
        log(f"Regime {regime} only confirmed {confirmed_scans}x — waiting for stability")
        exits = monitor_open_trades(regime)
    else:
        exits = _scan_and_enter(regime, spy_chg, open_trades, confirmed_scans)

    # Batched WhatsApp exit message
    if exits:
        real_exits = [x for x in exits if 'Partial' not in x['reason']]
        partials   = [x for x in exits if 'Partial' in x['reason']]
        if real_exits:
            wins      = [x for x in real_exits if x['pnl'] and x['pnl'] > 0]
            total_pnl = sum(x['pnl'] for x in real_exits if x['pnl'])
            lines     = [f"CLOSES {len(real_exits)} | {len(wins)}W/{len(real_exits)-len(wins)}L | ${total_pnl:+.2f}"]
            for x in real_exits:
                e = '✅' if x['pnl'] and x['pnl'] > 0 else '❌'
                lines.append(f"  {e} {x['sym']} ${x['entry']}→${x['price']} ${x['pnl']:+.2f} ({x['pnl_pct']:+.1f}%)")
            send_telegram('\n'.join(lines))
        if partials:
            lines = [f"PARTIAL EXITS {len(partials)} (50% locked in)"]
            for x in partials:
                lines.append(f"  ✅ {x['sym']} 50% @ ${x['price']} P&L ${x['pnl']:+.2f}")
            send_telegram('\n'.join(lines))

def get_deployed_capital():
    """Sum of capital currently locked in open positions."""
    return sum(t['shares'] * t['entry_price'] for t in get_open_trades())

def get_position_capital(grade, is_catalyst, deployed):
    """Dynamic allocation — $2,000 max per trade; 5 trades = $10K fully deployed."""
    remaining = TOTAL_CAPITAL - deployed
    if remaining < 200:
        return 0
    if is_catalyst and grade == 'A+':
        alloc = 2000   # top catalyst
    elif grade == 'A+':
        alloc = 1800   # strong momentum
    elif is_catalyst and grade == 'A':
        alloc = 1600   # catalyst, decent grade
    else:
        alloc = 1400   # solid A setup
    return round(min(alloc, remaining), 2)

def _scan_catalyst_override(open_trades):
    """
    Scan for isolated catalyst plays (earnings/news gap-and-go) on WEAK market days.
    These are market-independent: a stock up 6%+ on earnings goes up regardless of SPY.
    Rules: gap 6%+ from prev close, still above open price now, volume 2x+, A+ grade only.
    Position size is halved since market backdrop is not supportive.
    """
    global daily_trade_count, traded_today

    now = datetime.now(ET)
    # Only run in entry window and after opening range
    if not is_entry_window():
        return []

    # Build scan list: dynamic (momentum scanner) picks first, then full universe
    scan_order = catalyst_priority + [s for s in FULL_UNIVERSE if s not in catalyst_priority]
    entries = []

    for symbol in scan_order:
        if symbol in traded_today:
            continue
        if any(t['symbol'] == symbol for t in open_trades):
            continue
        if daily_trade_count >= MAX_DAILY_TRADES:
            break
        if len(open_trades) + len(entries) >= MAX_OPEN_TRADES:
            break

        try:
            # Quick check: is this stock gapping 6%+ with 2x+ volume?
            hist = yf.Ticker(symbol).history(period='2d', interval='1d')
            if len(hist) < 2:
                continue
            prev_close  = float(hist['Close'].iloc[-2])
            today_open  = float(hist['Open'].iloc[-1])
            today_vol   = float(hist['Volume'].iloc[-1])
            avg_vol     = float(yf.Ticker(symbol).history(period='30d')['Volume'].mean())
            gap_pct     = (today_open - prev_close) / prev_close * 100
            vol_ratio   = today_vol / avg_vol if avg_vol > 0 else 1

            # Must be gapping 6%+ with at least 2x volume — confirmed catalyst
            if gap_pct < 6.0 or vol_ratio < 2.0:
                continue

            sig = get_intraday_signals(symbol)
            if sig is None:
                continue

            price = sig['price']
            # Stock must still be above its open price (gap not fading)
            if price < today_open * 0.99:
                continue

            sl, target, risk_pct, reward_pct, rr = calc_sl_target(symbol, price, 'LONG')
            grade, reasons, score = grade_setup(sig, 'NORMAL', sl, target, price, rr, symbol=symbol)

            # Catalyst override requires A+ only
            if grade != 'A+':
                continue

            sector  = get_symbol_sector(symbol)
            # Half position size on WEAK market day
            deployed = get_deployed_capital()   # entries already in DB via log_trade_entry
            capital  = get_position_capital(grade, True, deployed) * 0.5
            if capital < 100:
                continue
            # ATR-normalized: size so actual stop risk ≤ MAX_LOSS_PER_TRADE (halved for WEAK day)
            risk_per_share = round(price - sl, 4)
            atr_shares     = int((MAX_LOSS_PER_TRADE * 0.5) / risk_per_share) if risk_per_share > 0 else int(capital / price)
            shares         = max(1, min(int(capital / price), atr_shares))

            log(f"  ⚡ CATALYST OVERRIDE {symbol} gap {gap_pct:+.1f}% vol {vol_ratio:.1f}x — entering despite WEAK market")

            trade_id = place_trade(
                symbol, price, shares, sl, target,
                'CATALYST_OVERRIDE', grade,
                rsi=sig['rsi'], vol_ratio=sig['vol_ratio'],
                confidence=score, sector=sector,
            )
            if trade_id:
                traded_today.add(symbol)
                save_traded_today()
                open_positions[symbol] = trade_id
                daily_trade_count += 1
                entries.append({'symbol': symbol, 'price': price, 'shares': shares,
                                'sl': sl, 'target': target, 'gap_pct': gap_pct,
                                'vol_ratio': vol_ratio})

        except Exception as e:
            log(f"  Catalyst override error {symbol}: {e}")

    if entries:
        lines = [f"⚡ CATALYST OVERRIDE — {len(entries)} isolated plays (WEAK mkt)"]
        for e in entries:
            lines.append(f"  {e['symbol']} gap {e['gap_pct']:+.1f}% | {e['shares']}sh @ ${e['price']} | SL${e['sl']} T${e['target']}")
        send_telegram('\n'.join(lines))

    return []

def _scan_and_enter(regime, spy_chg, open_trades, confirmed_scans=1):
    global daily_trade_count, traded_today

    # ── Daily max loss brake ───────────────────────────────────
    try:
        portfolio = requests.get(f"{BRIDGE}/portfolio", timeout=8).json()
        unrealized = sum(p.get('unrealizedPnL', 0) or 0 for p in portfolio)
    except Exception:
        unrealized = 0
    realized    = get_daily_pnl()
    daily_total = realized.get('pnl', 0) + unrealized
    if daily_total <= -MAX_DAILY_LOSS:
        log(f"⛔ Daily max loss hit (${daily_total:.0f}) — protecting capital, no new entries")
        return monitor_open_trades(regime)

    # Dynamic picks = symbols in catalyst_priority but NOT in fixed FULL_UNIVERSE
    dynamic_picks = [s for s in catalyst_priority if s not in FULL_UNIVERSE]

    # Build scan order: catalyst picks first, then rest of universe
    scan_order = catalyst_priority + [s for s in FULL_UNIVERSE if s not in catalyst_priority]
    candidates = []

    for symbol in scan_order:
        if symbol in traded_today:
            continue
        if any(t['symbol'] == symbol for t in open_trades):
            continue

        # Dynamic (unknown) stocks need strong confirmation — volatile at open
        is_dynamic = symbol in dynamic_picks
        if is_dynamic and regime != 'STRONG' and not (regime == 'NORMAL' and confirmed_scans >= 3):
            continue

        sig = get_intraday_signals(symbol, spy_chg=spy_chg)
        if sig is None:
            continue

        price = sig['price']
        if price < 5 or price > 800:
            continue

        side = 'LONG'
        if side == 'LONG' and sig['intra_chg'] < -5:
            continue

        sl, target, risk_pct, reward_pct, rr = calc_sl_target(symbol, price, side)
        grade, reasons, score = grade_setup(sig, regime, sl, target, price, rr, symbol=symbol)

        if grade in ('SKIP', 'C'):
            continue
        # SPY negative intraday → only take A+ setups
        if spy_chg < 0 and grade != 'A+':
            continue

        # Dynamic (unknown) stocks require A+ regardless — too risky at lower grades
        if symbol in dynamic_picks and grade != 'A+':
            continue

        is_catalyst = symbol in catalyst_priority
        candidates.append({
            'symbol': symbol, 'price': price, 'grade': grade, 'score': score,
            'side': side, 'sl': sl, 'target': target, 'risk_pct': risk_pct, 'rr': rr,
            'reasons': reasons, 'fvg_count': sig['fvg_count'],
            'vol_ratio': sig['vol_ratio'], 'rsi': sig['rsi'],
            'intra_chg': sig['intra_chg'], 'is_catalyst': is_catalyst,
        })

    # Sort: catalyst A+ first, then by grade, then by raw score (highest score wins within grade)
    grade_order = {'A+': 0, 'A': 1, 'B': 2}
    candidates.sort(key=lambda x: (
        0 if x['is_catalyst'] and x['grade'] in ('A+', 'A') else 1,
        grade_order.get(x['grade'], 3),
        -x['score']
    ))

    log(f"Found {len(candidates)} valid setups ({sum(1 for c in candidates if c['is_catalyst'])} catalyst)")

    entries       = []
    open_count    = len(open_trades)
    sector_counts = get_open_sector_counts()

    for pick in candidates:
        if open_count + len(entries) >= MAX_OPEN_TRADES:
            break
        if daily_trade_count >= MAX_DAILY_TRADES:
            break
        if pick['grade'] not in ('A+', 'A'):
            break

        sym    = pick['symbol']
        sector = get_symbol_sector(sym)

        # Enforce sector concentration limit (OTHER bucket is uncapped)
        if sector != 'OTHER' and sector_counts.get(sector, 0) >= MAX_PER_SECTOR:
            log(f"  SKIP {sym} — {sector} sector full ({MAX_PER_SECTOR} positions)")
            continue

        price    = pick['price']
        deployed = get_deployed_capital()   # entries already in DB via log_trade_entry
        capital  = get_position_capital(pick['grade'], pick['is_catalyst'], deployed)
        if capital <= 0:
            log(f"  Capital cap reached (${deployed:,.0f}/${TOTAL_CAPITAL:,} deployed)")
            break
        # ATR-normalized: size so actual stop risk ≤ MAX_LOSS_PER_TRADE
        risk_per_share = round(price - pick['sl'], 4)
        atr_shares     = int(MAX_LOSS_PER_TRADE / risk_per_share) if risk_per_share > 0 else int(capital / price)
        shares         = max(1, min(int(capital / price), atr_shares))
        strategy = 'CATALYST' if pick['is_catalyst'] else ('FVG_FILL' if pick['fvg_count'] > 5 else 'MOMENTUM')

        log(f"  {'⚡' if pick['is_catalyst'] else '🎯'} {pick['grade']} {sym} [{sector}] ${price} | "
            f"Vol {pick['vol_ratio']:.1f}x | RSI {pick['rsi']} | R:R 1:{pick['rr']} | ATR stop")

        trade_id = place_trade(
            sym, price, shares, pick['sl'], pick['target'],
            strategy, pick['grade'],
            rsi=pick['rsi'], vol_ratio=pick['vol_ratio'],
            confidence=pick['score'], sector=sector,
        )

        if trade_id:
            traded_today.add(sym)
            save_traded_today()
            open_positions[sym] = trade_id
            daily_trade_count  += 1
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            entries.append(pick | {'shares': shares, 'sector': sector})
            time.sleep(2)

    # Monitor existing trades too
    exits = monitor_open_trades(regime)

    # Batched WhatsApp entry message
    if entries:
        lines = [f"NEW TRADES {len(entries)} | Daily {daily_trade_count}/{MAX_DAILY_TRADES}"]
        for e in entries:
            tag = '⚡' if e['is_catalyst'] else '🤖'
            lines.append(f"  {tag}{e['grade']} {e['symbol']} [{e.get('sector','?')}] x{e['shares']} @${e['price']} "
                         f"SL${e['sl']} T${e['target']} R:R 1:{e['rr']}")
        send_telegram('\n'.join(lines))

    return exits

# ─────────────────────────────────────────────────────────
# BEAR SCAN — WEAK days: scan for short setups
# ─────────────────────────────────────────────────────────
def _scan_and_enter_bear(regime, spy_chg, open_trades, confirmed_scans=1):
    global daily_trade_count, traded_today

    # ── Daily max loss brake ───────────────────────────────────
    try:
        portfolio = requests.get(f"{BRIDGE}/portfolio", timeout=8).json()
        unrealized = sum(p.get('unrealizedPnL', 0) or 0 for p in portfolio)
    except Exception:
        unrealized = 0
    realized    = get_daily_pnl()
    daily_total = realized.get('pnl', 0) + unrealized
    if daily_total <= -MAX_DAILY_LOSS:
        log(f"⛔ Daily max loss hit (${daily_total:.0f}) — protecting capital, no new entries")
        return monitor_open_trades(regime)

    scan_order = catalyst_priority + [s for s in FULL_UNIVERSE if s not in catalyst_priority]
    candidates = []

    for symbol in scan_order:
        if symbol in traded_today:
            continue
        if any(t['symbol'] == symbol for t in open_trades):
            continue

        sig = get_intraday_signals(symbol, spy_chg=spy_chg)
        if sig is None:
            continue

        price = sig['price']
        if price < 5 or price > 800:
            continue

        # Skip stocks already surging — not short candidates
        if sig['intra_chg'] > 5:
            continue

        sl, target, risk_pct, reward_pct, rr = calc_sl_target(symbol, price, 'SHORT')
        grade, reasons, score = grade_bear_setup(sig, regime, sl, target, price, rr, symbol=symbol)

        if grade in ('SKIP', 'C'):
            continue
        # SPY recovering intraday → only highest-conviction shorts
        if spy_chg > 0 and grade != 'A+':
            continue

        is_catalyst = symbol in catalyst_priority
        candidates.append({
            'symbol': symbol, 'price': price, 'grade': grade, 'score': score,
            'side': 'SHORT', 'sl': sl, 'target': target, 'risk_pct': risk_pct, 'rr': rr,
            'reasons': reasons, 'fvg_count': sig['fvg_count'],
            'vol_ratio': sig['vol_ratio'], 'rsi': sig['rsi'],
            'intra_chg': sig['intra_chg'], 'is_catalyst': is_catalyst,
        })

    grade_order = {'A+': 0, 'A': 1, 'B': 2}
    candidates.sort(key=lambda x: (grade_order.get(x['grade'], 3), -x['score']))

    log(f"Bear scan: {len(candidates)} short candidates")

    entries       = []
    open_count    = len(open_trades)
    sector_counts = get_open_sector_counts()

    for pick in candidates:
        if open_count + len(entries) >= MAX_OPEN_TRADES:
            break
        if daily_trade_count >= MAX_DAILY_TRADES:
            break
        if pick['grade'] not in ('A+', 'A'):
            break

        sym    = pick['symbol']
        sector = get_symbol_sector(sym)

        if sector != 'OTHER' and sector_counts.get(sector, 0) >= MAX_PER_SECTOR:
            log(f"  SKIP {sym} — {sector} sector full ({MAX_PER_SECTOR} positions)")
            continue

        price    = pick['price']
        deployed = get_deployed_capital()
        capital  = get_position_capital(pick['grade'], pick['is_catalyst'], deployed)
        if capital <= 0:
            log(f"  Capital cap reached (${deployed:,.0f}/${TOTAL_CAPITAL:,} deployed)")
            break
        # For shorts: SL is above entry, so risk per share = sl - price
        risk_per_share = round(pick['sl'] - price, 4)
        atr_shares     = int(MAX_LOSS_PER_TRADE / risk_per_share) if risk_per_share > 0 else int(capital / price)
        shares         = max(1, min(int(capital / price), atr_shares))

        log(f"  ↓ {pick['grade']} {sym} [{sector}] ${price} | "
            f"Vol {pick['vol_ratio']:.1f}x | RSI {pick['rsi']} | R:R 1:{pick['rr']} | "
            f"SL${pick['sl']} | {', '.join(pick['reasons'][:3])}")

        trade_id = place_trade(
            sym, price, shares, pick['sl'], pick['target'],
            'BEAR_MOMENTUM', pick['grade'],
            rsi=pick['rsi'], vol_ratio=pick['vol_ratio'],
            confidence=pick['score'], sector=sector, side='SHORT'
        )

        if trade_id:
            traded_today.add(sym)
            save_traded_today()
            open_positions[sym] = trade_id
            daily_trade_count  += 1
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            entries.append(pick | {'shares': shares, 'sector': sector})
            time.sleep(2)

    exits = monitor_open_trades(regime)

    if entries:
        lines = [f"↓ BEAR TRADES {len(entries)} | Daily {daily_trade_count}/{MAX_DAILY_TRADES}"]
        for e in entries:
            lines.append(f"  ↓{e['grade']} {e['symbol']} [{e.get('sector','?')}] x{e['shares']} @${e['price']} "
                         f"SL${e['sl']} T${e['target']} R:R 1:{e['rr']}")
        send_telegram('\n'.join(lines))

    return exits

# ─────────────────────────────────────────────────────────
# SCHEDULED TASKS
# ─────────────────────────────────────────────────────────
def get_premarket_pct(sym):
    """Returns pre-market % change vs previous close, or None if unavailable."""
    try:
        data = yf.Ticker(sym).history(period='1d', interval='1m', prepost=True)
        if data.empty:
            return None
        # tz-aware filter: only today's pre-market bars (before 9:30am ET)
        data.index = data.index.tz_convert(ET)
        today = datetime.now(ET).date()
        pm = data[(data.index.date == today) & (data.index.hour < 9) |
                  ((data.index.date == today) & (data.index.hour == 9) & (data.index.minute < 30))]
        if pm.empty:
            return None
        pm_last = float(pm['Close'].iloc[-1])
        hist = yf.Ticker(sym).history(period='5d', interval='1d')
        if len(hist) < 2:
            return None
        prev_close = float(hist['Close'].iloc[-2])
        return round((pm_last - prev_close) / prev_close * 100, 2)
    except Exception:
        return None

def premarket_early_scan():
    """4:30am ET — first look at pre-market movers. Pros scan here."""
    global catalyst_priority
    log("PRE-MARKET SCAN (4:30am) — identifying overnight movers...")
    scan_universe = list(dict.fromkeys(FULL_UNIVERSE))
    pm_results = []
    for sym in scan_universe:
        pct = get_premarket_pct(sym)
        if pct is not None and pct >= 2.0:
            pm_results.append((sym, pct))
    pm_results.sort(key=lambda x: -x[1])

    if pm_results:
        # Seed catalyst_priority with pre-market leaders
        pm_syms = [s for s, _ in pm_results]
        catalyst_priority = pm_syms + [s for s in catalyst_priority if s not in pm_syms]
        lines = []
        for i, (s, p) in enumerate(pm_results[:8]):
            tag = '' if s in FULL_UNIVERSE else ' 🆕'
            si_str = ''
            if i < 5:
                try:
                    info = yf.Ticker(s).info
                    si = info.get('shortPercentOfFloat')
                    if si:
                        si_str = f" | SI {si*100:.0f}%"
                except Exception:
                    pass
            lines.append(f"  {s}: +{p:.1f}% pre-mkt{tag}{si_str}")
        msg = f"🌅 Pre-market watchlist ({len(pm_results)} movers):\n" + '\n'.join(lines)
        log(msg)
        send_telegram(msg)
    else:
        log("  No significant pre-market movers (all <2%)")
        send_telegram("🌅 Pre-market: quiet — no movers >2% yet")

def morning_catalyst_scan():
    global catalyst_priority
    log("CATALYST SCAN (8:15am) — refreshing watchlist before open...")

    # ── 0. Refresh pre-market (now closer to open, more accurate) ─
    premarket_lines = []
    premarket_syms  = []
    scan_universe   = list(dict.fromkeys(FULL_UNIVERSE + list(set(
        s for s in catalyst_priority if s not in FULL_UNIVERSE
    ))))
    log(f"  Pre-market refresh: checking {len(scan_universe)} stocks...")
    pm_results = []
    for sym in scan_universe:
        pct = get_premarket_pct(sym)
        if pct is not None and pct >= 2.0:
            pm_results.append((sym, pct))
    pm_results.sort(key=lambda x: -x[1])
    for sym, pct in pm_results[:10]:
        tag = '' if sym in FULL_UNIVERSE else ' 🆕'
        premarket_syms.append(sym)
        premarket_lines.append(f"  {sym}: +{pct:.1f}% pre-mkt{tag}")
    if premarket_syms:
        log(f"  Pre-market movers ({len(premarket_syms)}): {premarket_syms}")

    # ── Key levels for top 5 pre-market movers ────────────────
    key_level_lines = []
    for sym_kl, pct_kl in pm_results[:5]:
        try:
            kl_data = yf.Ticker(sym_kl).history(period='1d', interval='1m', prepost=True)
            if not kl_data.empty:
                kl_data.index = kl_data.index.tz_convert(ET)
                today_kl = datetime.now(ET).date()
                pm_bars = kl_data[
                    ((kl_data.index.date == today_kl) & (kl_data.index.hour < 9)) |
                    ((kl_data.index.date == today_kl) & (kl_data.index.hour == 9) & (kl_data.index.minute < 30))
                ]
                if not pm_bars.empty:
                    pm_high_kl = round(float(pm_bars['High'].max()), 2)
                    hist_kl = yf.Ticker(sym_kl).history(period='5d', interval='1d')
                    prior_close_kl = round(float(hist_kl['Close'].iloc[-2]), 2) if len(hist_kl) >= 2 else None
                    if sym_kl not in key_levels:
                        key_levels[sym_kl] = {}
                    key_levels[sym_kl].update({
                        'pm_high': pm_high_kl,
                        'prior_close': prior_close_kl or pm_high_kl,
                    })
                    prior_str = f" | Prev ${prior_close_kl}" if prior_close_kl else ""
                    key_level_lines.append(f"  {sym_kl}: PM high ${pm_high_kl}{prior_str} → ORB entry above ${pm_high_kl}")
        except Exception:
            pass

    # ── 1. Dynamic IBKR momentum scan ─────────────────────
    dynamic_syms  = []
    dynamic_lines = []
    try:
        r = requests.get(f"{BRIDGE}/scan/momentum", timeout=45)
        if r.status_code == 200:
            movers = r.json()
            for m in movers:
                sym = m['symbol']
                pct = m['pct_change']
                dynamic_syms.append(sym)
                tag = '' if sym in FULL_UNIVERSE else ' 🆕'
                dynamic_lines.append(f"  {sym}: +{pct}%{tag}")
            log(f"  Dynamic movers ({len(dynamic_syms)}): {dynamic_syms[:10]}")
        else:
            log(f"  Dynamic scan HTTP {r.status_code}")
    except Exception as e:
        log(f"  Dynamic scan error: {e}")

    # ── 2. Fixed catalyst scan (earnings, gap-up, volume surge) ──
    static_syms = []
    try:
        picks       = run_catalyst_scan()
        static_syms = [p['symbol'] for p in picks if isinstance(p, dict) and 'symbol' in p]
        log(f"  Static catalyst picks ({len(static_syms)}): {static_syms[:10]}")
    except Exception as e:
        log(f"  Static catalyst scan error: {e}")

    # ── 3. Merge: pre-market first → dynamic → static ────────
    # Pre-market movers get top priority (known before open = best setups)
    seen = set()
    combined = []
    for sym in premarket_syms + dynamic_syms + static_syms:
        if sym not in seen:
            seen.add(sym)
            combined.append(sym)
    catalyst_priority = combined

    log(f"Priority list ({len(catalyst_priority)}): {catalyst_priority[:12]}")

    # ── 4. Telegram alert ─────────────────────────────────
    msg_parts = []
    if premarket_syms:
        msg_parts.append(
            f"🌅 Pre-market movers ({len(premarket_syms)}):\n"
            + '\n'.join(premarket_lines[:6])
        )
    if dynamic_syms:
        new_syms = [s for s in dynamic_syms if s not in FULL_UNIVERSE]
        msg_parts.append(
            f"🔥 Momentum scan: {len(dynamic_syms)} movers"
            + (f" ({len(new_syms)} new)\n" if new_syms else "\n")
            + '\n'.join(dynamic_lines[:6])
        )
    if static_syms:
        msg_parts.append(f"⚡ Catalyst signals: {', '.join(static_syms[:6])}")
    if key_level_lines:
        msg_parts.append("📐 Key levels for top picks:\n" + '\n'.join(key_level_lines))
    if not msg_parts:
        msg_parts.append("📭 No pre-market or catalyst signals today")

    send_telegram('\n\n'.join(msg_parts))

def morning_voice_summary():
    log("Morning voice summary")
    try:
        r       = requests.get(f"{BRIDGE}/account", timeout=10)
        account = r.json()
        pnl     = account.get('UnrealizedPnL', 0) or 0
        net_liq = account.get('NetLiquidation', 0) or 0
        buying  = account.get('BuyingPower', 0) or 0
        wr_30d  = get_win_rate(days=30)
        positions = get_ibkr_positions()
        pnl_word  = "up" if pnl >= 0 else "down"
        speak(
            f"Good morning! Auto trader active. "
            f"Account value {net_liq:,.0f} dollars. "
            f"You are {pnl_word} {abs(pnl):,.0f} dollars unrealized. "
            f"Buying power {buying:,.0f} dollars. "
            f"Holding {len(positions)} positions. "
            f"30 day win rate is {wr_30d:.0f} percent. "
            f"Good luck today!"
        )
    except Exception as e:
        log(f"Voice summary error: {e}")

def evening_summary():
    log("Evening summary")
    daily      = get_daily_pnl()
    wr_30      = get_win_rate(days=30)
    wr_day     = (daily['wins'] / daily['trades'] * 100) if daily['trades'] > 0 else 0
    emoji      = '✅' if daily['pnl'] > 0 else '❌'
    open_trades = get_open_trades()
    deployed   = get_deployed_capital()
    hold_msg   = f"\n📦 {len(open_trades)} positions held overnight (${deployed:,.0f} deployed)" if open_trades else ""
    send_telegram(
        f"{emoji} EOD Summary\n"
        f"Trades: {daily['trades']} | Wins: {daily['wins']} | WR: {wr_day:.0f}%\n"
        f"P&L today: ${daily['pnl']:+.2f}\n"
        f"30d win rate: {wr_30:.0f}%"
        f"{hold_msg}"
    )

def nightly_learning():
    log("NIGHTLY LEARNING — analysing trades...")
    try:
        run_learning_cycle()
        log("Learning complete.")
    except Exception as e:
        log(f"Learning error: {e}")

def reset_daily_state():
    """Midnight reset — clears per-day counters so next session starts clean."""
    global traded_today, daily_trade_count, atr_cache, regime_history, spy_open_price
    global trade_entry_times, earnings_cache
    traded_today       = set()
    daily_trade_count  = 0
    atr_cache          = {}
    regime_history     = []
    spy_open_price     = None
    trade_entry_times   = {}
    earnings_cache      = {}
    partial_done_trades = {}
    save_traded_today()
    log("Daily state reset for new trading day")

# ─────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────
if __name__ == '__main__':
    init_db()
    traded_today = load_traded_today()
    if traded_today:
        log(f"Restored traded_today: {len(traded_today)} symbols")

    print("\n🤖 Auto Trader v2 — Consolidated")
    print("=" * 55)
    print(f"Scan interval:    Every {SCAN_INTERVAL//60} min")
    print(f"Max open trades:  {MAX_OPEN_TRADES}")
    print(f"Max daily trades: {MAX_DAILY_TRADES}")
    print(f"Total capital:    ${TOTAL_CAPITAL:,} (dynamic per position)")
    print(f"Stop method:      5% fixed | partial exit 50% at 1R (+5%) | trail rest")
    print(f"Trail method:     ATR × {ATR_TRAIL_MULT} + 5m bar low at 3%+ profit")
    print(f"Exit signals:     VWAP cross ↓ | ATR fade | EOD 3:45pm conviction | circuit breaker")
    print(f"No fixed target:  ride winners until signal fires — no capped % exits")
    print(f"EOD close:        {EOD_CLOSE_HOUR}:{EOD_CLOSE_MINUTE:02d} ET — close unless profit>1.5% AND above VWAP")
    print(f"Circuit breaker:  ${MAX_LOSS_PER_TRADE}/trade | ${MAX_DAILY_LOSS}/day loss | +${DAILY_PROFIT_TARGET}/day profit")
    print(f"Max hold:         {MAX_HOLD_DAYS} business day backstop (EOD close fires first)")
    print(f"Min today gain:   {MIN_TODAY_GAIN}% (only enter stocks moving today)")
    print(f"Lunch avoid:      {LUNCH_AVOID_START[0]}:{LUNCH_AVOID_START[1]:02d}–{LUNCH_AVOID_END[0]}:{LUNCH_AVOID_END[1]:02d} ET (no entries during chop)")
    print(f"Entry patterns:   ORB | VWAP reclaim | Bull flag | HOD break | RS vs SPY")
    print(f"Entry cutoff:     {NO_ENTRY_AFTER}:00 ET | Min R:R 1:{MIN_RR}")
    print("=" * 55)
    print("Scheduled: pre-mkt 4:30am | catalyst 8:15am | voice 9am | EOD 4:30pm | learning 11pm | reset midnight")
    print("Telegram:  STATUS | CANCEL | RESUME")
    print("Press CTRL+C to stop\n")

    # Background scheduler for timed tasks
    sched = BackgroundScheduler(timezone=ET)
    sched.add_job(premarket_early_scan,   'cron',     day_of_week='mon-fri', hour=4,  minute=30)
    sched.add_job(morning_catalyst_scan,  'cron',     day_of_week='mon-fri', hour=8,  minute=15)
    sched.add_job(morning_voice_summary,  'cron',     day_of_week='mon-fri', hour=9,  minute=0)
    sched.add_job(evening_summary,        'cron',     day_of_week='mon-fri', hour=16, minute=30)
    sched.add_job(nightly_learning,       'cron',     day_of_week='mon-fri', hour=23, minute=0)
    sched.add_job(reset_daily_state,      'cron',                            hour=0,  minute=1)
    sched.add_job(poll_telegram_commands, 'interval', seconds=15)
    sched.start()

    send_telegram(f"🤖 Auto Trader v2 ON | Swing mode | ${TOTAL_CAPITAL:,} cap | max {MAX_OPEN_TRADES} positions")

    while True:
        try:
            if is_market_open():
                run_scan()
            else:
                now = datetime.now(ET)
                if now.hour >= 16:
                    daily = get_daily_pnl()
                    log(f"Market closed. Trades: {daily_trade_count} | P&L ${daily['pnl']:+.2f} | sleeping until tomorrow...")
                elif now.hour < 9 or (now.hour == 9 and now.minute < 31):
                    log("Pre-market — waiting for open...")
            time.sleep(SCAN_INTERVAL)
        except KeyboardInterrupt:
            daily = get_daily_pnl()
            send_telegram(f"🤖 Auto Trader stopped\nTrades: {daily_trade_count} | P&L: ${daily['pnl']:+.2f}")
            sched.shutdown()
            break
        except Exception as e:
            log(f"Scan error: {e}")
            time.sleep(30)
