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
    update_trade_stop, update_trade_shares, get_trade_entry_date
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
MAX_OPEN_TRADES   = 50       # paper testing — collect data
MAX_DAILY_TRADES  = 100
CAPITAL_PER_TRADE = 400
MAX_RISK_PCT      = 3.5      # slightly wider for ATR-based stops
MIN_RR            = 2.5      # relaxed slightly since stops are wider
MAX_RSI_ENTRY     = 80
MIN_VOLUME_RATIO  = 1.3
MAX_PER_SECTOR    = 5        # max open positions per sector
TG_POLL_OFFSET    = 0        # tracks last processed Telegram update
ATR_PERIOD        = 14
ATR_STOP_MULT     = 1.5      # initial stop: entry - 1.5×ATR
ATR_TRAIL_MULT    = 1.5      # trail: 1.5×ATR below rolling session high
ATR_FADE_MULT     = 1.0      # momentum fade: drop > 1×ATR from session high
TIME_STOP_DAYS    = 3        # exit flat trades after 3 days
NO_ENTRY_AFTER    = 15       # no new entries at/after 3:00pm ET

# ── Persistence ───────────────────────────────────────────
_DIR              = os.path.dirname(os.path.abspath(__file__))
TRADED_TODAY_FILE = os.path.join(_DIR, 'traded_today.json')

# ── In-memory state ───────────────────────────────────────
traded_today      = set()
open_positions    = {}       # sym → trade_id
price_history     = {}       # trade_id → [prices]
session_high      = {}       # trade_id → highest price seen this session
atr_cache         = {}       # sym → (date_str, atr_value)
partial_done      = set()    # trade_ids where 50% already exited
daily_trade_count = 0
catalyst_priority = []       # symbols from today's catalyst scan
tg_update_id      = 0        # Telegram polling offset

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
    # DROPPED — confirmed underperformers:
    # NVDA(0%), SMR(24%), MSTR(17%), SNOW(33%), CRWD(33%)
    # TSLA(33%), UBER(33%), JOBY(38%), PANW(43%), MS(43%)
    # AFRM(43%), ACHR(44%), SOFI(50% neg avg), HPE(50% neg avg)
]))

# ── Top 5 sectors + symbol map ────────────────────────────
# Consolidates watchlist SECTORS into 5 tradeable groups
SECTOR_MAP = {
    # TECH: AI chips, semis, cloud, mega-cap
    'AAPL':'TECH','MSFT':'TECH','AMZN':'TECH','GOOGL':'TECH','META':'TECH',
    'AMD':'TECH','AVGO':'TECH','NFLX':'TECH',
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

def get_symbol_sector(symbol):
    return SECTOR_MAP.get(symbol, 'OTHER')

def get_open_sector_counts():
    """Count open trades per sector from DB."""
    counts = {}
    for t in get_open_trades():
        sec = get_symbol_sector(t['symbol'])
        counts[sec] = counts.get(sec, 0) + 1
    return counts

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
    return is_market_open() and now.hour < NO_ENTRY_AFTER

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
def get_regime():
    try:
        spy_intra = yf.Ticker('SPY').history(period='1d', interval='5m')
        qqq_intra = yf.Ticker('QQQ').history(period='1d', interval='5m')
        vix_intra = yf.Ticker('^VIX').history(period='1d', interval='5m')
        spy_daily = yf.Ticker('SPY').history(period='2d')

        spy_price = float(spy_intra['Close'].iloc[-1])
        spy_prev  = float(spy_daily['Close'].iloc[-2])
        spy_chg   = (spy_price - spy_prev) / spy_prev * 100
        vix_val   = float(vix_intra['Close'].iloc[-1])

        # SPY VWAP
        tp   = (spy_intra['High'] + spy_intra['Low'] + spy_intra['Close']) / 3
        vwap = float((tp * spy_intra['Volume']).cumsum().iloc[-1] /
                     spy_intra['Volume'].cumsum().iloc[-1])
        spy_above_vwap = spy_price > vwap

        # VIX direction (30 min)
        vix_rising = (len(vix_intra) >= 6 and
                      float(vix_intra['Close'].iloc[-1]) > float(vix_intra['Close'].iloc[-6]))

        # QQQ vs SPY breadth
        qqq_leading = True
        if not qqq_intra.empty and len(qqq_intra) >= 2:
            qqq_chg       = (float(qqq_intra['Close'].iloc[-1]) - float(qqq_intra['Open'].iloc[0])) / float(qqq_intra['Open'].iloc[0]) * 100
            spy_intra_chg = (spy_price - float(spy_intra['Open'].iloc[0])) / float(spy_intra['Open'].iloc[0]) * 100
            qqq_leading   = qqq_chg >= spy_intra_chg - 0.3

        # Choppiness
        chop = False
        if len(spy_intra) >= 6:
            diffs   = spy_intra['Close'].diff().dropna()
            changes = sum(1 for i in range(1, len(diffs)) if diffs.iloc[i] * diffs.iloc[i-1] < 0)
            chop    = changes / len(diffs) > 0.4 and abs(spy_chg) < 0.3

        # Base regime — thresholds calibrated to post-2022 VIX baseline (18-22 is now normal)
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

        # Downgrade one level per negative institutional signal
        order = ['STRONG', 'NORMAL', 'CAUTIOUS', 'WEAK']
        if regime not in ('CHOPPY', 'WEAK'):
            if not spy_above_vwap:
                regime = order[min(order.index(regime) + 1, len(order) - 1)]
            if vix_rising:
                regime = order[min(order.index(regime) + 1, len(order) - 1)]
            if not qqq_leading and regime == 'STRONG':
                regime = 'NORMAL'

        extra = {
            'vwap': round(vwap, 2), 'spy_above_vwap': spy_above_vwap,
            'vix_rising': vix_rising, 'qqq_leading': qqq_leading,
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
def get_intraday_signals(symbol):
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

        return {
            'price': round(price, 2), 'intra_chg': round(intra_chg, 2),
            'prev_chg': round(prev_chg, 2), 'vol_ratio': round(vol_ratio, 2),
            'above_ma': above_ma, 'uptrend': uptrend, 'ema_touch': ema_touch,
            'rsi': rsi, 'fvg_count': fvg_count,
            'is_tight': is_tight, 'range_pct': round(range_pct, 2),
        }
    except:
        return None

# ─────────────────────────────────────────────────────────
# STOP / TARGET — ATR-based, adapts to each stock's volatility
# ─────────────────────────────────────────────────────────
def calc_sl_target(symbol, price, side='LONG'):
    try:
        df  = get_ib_daily(symbol, duration='30 D') or yf.Ticker(symbol).history(period='30d')
        atr = get_atr(symbol) or price * 0.02

        if side == 'LONG':
            # Support-based stop
            supports     = sorted([l for l in df['Low'].values if l < price], reverse=True)
            support_stop = supports[0] * 0.998 if supports else price * 0.97
            # ATR-based stop — wider of the two, up to MAX_RISK_PCT
            atr_stop     = price - (ATR_STOP_MULT * atr)
            sl           = round(max(support_stop, atr_stop), 2)
            risk_pct     = (price - sl) / price * 100
            if risk_pct > MAX_RISK_PCT:
                sl       = round(price * (1 - MAX_RISK_PCT / 100), 2)
                risk_pct = MAX_RISK_PCT
            reward       = min(risk_pct * MIN_RR, 10.0)
            target       = round(price * (1 + reward / 100), 2)
        else:
            resists  = sorted([h for h in df['High'].values if h > price])
            resist   = resists[0] * 1.002 if resists else price * 1.03
            atr_stop = price + (ATR_STOP_MULT * atr)
            sl       = round(min(resist, atr_stop), 2)
            risk_pct = (sl - price) / price * 100
            if risk_pct > MAX_RISK_PCT:
                sl       = round(price * (1 + MAX_RISK_PCT / 100), 2)
                risk_pct = MAX_RISK_PCT
            reward   = min(risk_pct * MIN_RR, 10.0)
            target   = round(price * (1 - reward / 100), 2)

        rr = round(reward / risk_pct, 1) if risk_pct > 0 else 0
        return sl, target, round(risk_pct, 2), round(reward, 2), rr

    except:
        if side == 'LONG':
            return round(price * 0.965, 2), round(price * 1.088, 2), 3.5, 8.75, 2.5
        else:
            return round(price * 1.035, 2), round(price * 0.912, 2), 3.5, 8.75, 2.5

# ─────────────────────────────────────────────────────────
# GRADE SETUP
# ─────────────────────────────────────────────────────────
def grade_setup(sig, regime, sl, target, price, rr):
    score   = 0
    reasons = []

    if not sig['above_ma']:
        return 'SKIP', ['Below MA'], 0
    if sig['rsi'] > MAX_RSI_ENTRY:
        return 'SKIP', [f'RSI {sig["rsi"]} too high'], 0
    if sig['vol_ratio'] < MIN_VOLUME_RATIO:
        return 'SKIP', [f'Volume {sig["vol_ratio"]:.1f}x too low'], 0
    if rr < MIN_RR:
        return 'SKIP', [f'R:R 1:{rr} below min 1:{MIN_RR}'], 0
    if regime == 'CHOPPY':
        return 'SKIP', ['Choppy — no trades'], 0

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

    if 45 <= sig['rsi'] <= 65:
        score += 20; reasons.append(f'RSI {sig["rsi"]} ideal')
    elif 65 < sig['rsi'] <= 75:
        score += 10; reasons.append(f'RSI {sig["rsi"]} elevated')
    else:
        score += 5;  reasons.append(f'RSI {sig["rsi"]}')

    if sig['is_tight']:
        score += 10; reasons.append(f'Tight range {sig["range_pct"]:.1f}%')

    if regime == 'STRONG':
        score += 15; reasons.append('Strong market')
    elif regime == 'NORMAL':
        score += 5

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
                rsi=0, vol_ratio=0, confidence=75, sector='OTHER'):
    try:
        r = requests.post(f"{BRIDGE}/order", json={
            'symbol': symbol, 'qty': shares,
            'side': 'BUY', 'order_type': 'MARKET'
        }, timeout=10)
        order_id = str(r.json().get('orderId', ''))

        trade_id = log_trade_entry(
            symbol=symbol, entry_price=price, shares=shares,
            target_price=target, stop_price=sl, setup_type=strategy,
            rsi=rsi, volume_ratio=vol_ratio, sector=sector,
            earnings_days=999, confidence=confidence, order_id=order_id
        )
        return trade_id
    except Exception as e:
        log(f"Place trade error {symbol}: {e}")
        return None

# ─────────────────────────────────────────────────────────
# MONITOR OPEN TRADES — ATR trailing, partial exit, time stop
# ─────────────────────────────────────────────────────────
def monitor_open_trades():
    trades = get_open_trades()
    if not trades:
        return []

    exits = []

    for trade in trades:
        tid    = trade['id']
        sym    = trade['symbol']
        shares = trade['shares']
        entry  = trade['entry_price']
        sl     = trade['stop_price']
        target = trade['target_price']

        price = get_live_price(sym)
        if not price:
            continue

        # Price + session-high history
        if tid not in price_history:
            price_history[tid] = []
        price_history[tid].append(price)
        if len(price_history[tid]) > 20:
            price_history[tid].pop(0)

        session_high[tid] = max(session_high.get(tid, price), price)

        pnl_pct  = (price - entry) / entry * 100
        hist     = price_history[tid]
        atr      = get_atr(sym) or (entry * 0.02)

        # ── ATR trailing stop ──────────────────────────────
        # Trail 1.5×ATR below session high (only moves up, never down)
        if price > entry:
            atr_trail = round(session_high[tid] - ATR_TRAIL_MULT * atr, 2)
            if atr_trail > sl:
                sl = atr_trail
                update_trade_stop(tid, sl)
                log(f"  {sym}: ATR trail stop → ${sl} ({pnl_pct:+.1f}%)")

        # ── Exit decisions ─────────────────────────────────
        exit_reason = None
        now = datetime.now(ET)

        # 1. Hard stop
        if price <= sl:
            exit_reason = f'Stop loss ${sl} hit ({pnl_pct:+.1f}%)'

        # 2. Partial exit at target (50%) — first time only
        elif price >= target and tid not in partial_done:
            half = max(1, shares // 2)
            try:
                requests.post(f"{BRIDGE}/order", json={
                    'symbol': sym, 'qty': half,
                    'side': 'SELL', 'order_type': 'MARKET'
                }, timeout=10)
                partial_pnl = round((price - entry) * half, 2)
                partial_done.add(tid)
                remaining = shares - half
                update_trade_shares(tid, remaining)
                # Move stop to entry (lock in breakeven on remainder)
                update_trade_stop(tid, entry)
                sl = entry
                log(f"  {sym}: 50% exit at target ${target} | P&L ${partial_pnl:+.2f} | {remaining} shares remain")
                exits.append({
                    'sym': sym, 'price': price, 'entry': entry,
                    'pnl': partial_pnl, 'pnl_pct': pnl_pct,
                    'reason': f'Partial exit 50% at target ({pnl_pct:+.1f}%)'
                })
                continue
            except Exception as e:
                log(f"  {sym}: partial exit error: {e}")

        # 3. Full exit when remaining half hits new ATR-based target or stop
        elif price >= target and tid in partial_done:
            exit_reason = f'Final exit — target extended ({pnl_pct:+.1f}%)'

        # 4. Momentum fade — drop > 1×ATR from session high while in profit
        elif price < session_high.get(tid, price):
            drop = session_high[tid] - price
            if drop > ATR_FADE_MULT * atr and pnl_pct > 0.3:
                exit_reason = f'Momentum fade >{ATR_FADE_MULT}×ATR from high ({pnl_pct:+.1f}%)'

        # 5. Time stop — exit flat trade after TIME_STOP_DAYS
        if not exit_reason:
            entry_date = get_trade_entry_date(tid)
            if entry_date:
                days_held = (date.today() - date.fromisoformat(entry_date)).days
                if days_held >= TIME_STOP_DAYS and abs(pnl_pct) < 1.0:
                    exit_reason = f'Time stop — {days_held}d held, flat ({pnl_pct:+.1f}%)'

        # 6. EOD close
        if not exit_reason and now.hour == 15 and now.minute >= 45:
            exit_reason = f'EOD close ({pnl_pct:+.1f}%)'

        log(f"  {sym}: ${price} ({pnl_pct:+.1f}%) SL=${sl} TGT=${target} ATR=${atr:.2f} "
            f"→ {'EXIT' if exit_reason else 'HOLD'}")

        if exit_reason:
            try:
                requests.post(f"{BRIDGE}/order", json={
                    'symbol': sym, 'qty': shares,
                    'side': 'SELL', 'order_type': 'MARKET'
                }, timeout=10)
                pnl = log_trade_exit(tid, price, exit_reason)
                exits.append({
                    'sym': sym, 'price': price, 'entry': entry,
                    'pnl': pnl, 'pnl_pct': pnl_pct, 'reason': exit_reason
                })
                for d in (price_history, session_high, open_positions):
                    d.pop(tid if isinstance(d, dict) and tid in d else sym, None)
                partial_done.discard(tid)
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
    global daily_trade_count, traded_today

    # Reconcile DB with IBKR first
    reconcile_with_ibkr()

    regime, spy_chg, vix, extra = get_regime()
    open_trades = get_open_trades()
    daily       = get_daily_pnl()

    vwap_str = f"VWAP {'↑' if extra.get('spy_above_vwap') else '↓'}"
    vix_str  = f"VIX {vix:.1f}{'↑' if extra.get('vix_rising') else '↓'}"
    qqq_str  = f"QQQ {'lead' if extra.get('qqq_leading') else 'lag'}"
    log(f"\n{'='*55}")
    log(f"SCAN | Regime: {regime} | SPY {spy_chg:+.1f}% | {vwap_str} | {vix_str} | {qqq_str}")
    log(f"Open: {len(open_trades)} | Trades today: {daily_trade_count} | P&L: ${daily['pnl']:+.2f}")

    # Always monitor open trades
    exits = []
    if not is_entry_window():
        log("After 3pm — monitoring only")
        exits = monitor_open_trades()
    elif is_trading_blocked()[0]:
        log(f"Trading blocked — monitoring only")
        exits = monitor_open_trades()
    elif regime == 'CHOPPY':
        log("CHOPPY — monitoring only")
        exits = monitor_open_trades()
    elif len(open_trades) >= MAX_OPEN_TRADES:
        log(f"Max open trades ({MAX_OPEN_TRADES}) — monitoring only")
        exits = monitor_open_trades()
    elif daily_trade_count >= MAX_DAILY_TRADES:
        log(f"Max daily trades ({MAX_DAILY_TRADES}) reached")
        exits = monitor_open_trades()
    else:
        exits = _scan_and_enter(regime, spy_chg, open_trades)

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

def _scan_and_enter(regime, spy_chg, open_trades):
    global daily_trade_count, traded_today

    # Build scan order: catalyst picks first, then rest of universe
    scan_order = catalyst_priority + [s for s in FULL_UNIVERSE if s not in catalyst_priority]
    candidates = []

    for symbol in scan_order:
        if symbol in traded_today:
            continue
        if any(t['symbol'] == symbol for t in open_trades):
            continue

        sig = get_intraday_signals(symbol)
        if sig is None:
            continue

        price = sig['price']
        if price < 5 or price > 800:
            continue

        side = 'LONG'  # SHORT selling deferred to Phase 2 — monitor assumes LONG
        if side == 'LONG' and sig['intra_chg'] < -5:
            continue

        sl, target, risk_pct, reward_pct, rr = calc_sl_target(symbol, price, side)
        grade, reasons, score = grade_setup(sig, regime, sl, target, price, rr)

        if grade in ('SKIP', 'C'):
            continue

        is_catalyst = symbol in catalyst_priority
        candidates.append({
            'symbol': symbol, 'price': price, 'grade': grade, 'score': score,
            'side': side, 'sl': sl, 'target': target, 'risk_pct': risk_pct, 'rr': rr,
            'reasons': reasons, 'fvg_count': sig['fvg_count'],
            'vol_ratio': sig['vol_ratio'], 'rsi': sig['rsi'],
            'intra_chg': sig['intra_chg'], 'is_catalyst': is_catalyst,
        })

    # Sort: catalyst A+ first, then by grade + volume
    grade_order = {'A+': 0, 'A': 1, 'B': 2}
    candidates.sort(key=lambda x: (
        0 if x['is_catalyst'] and x['grade'] in ('A+', 'A') else 1,
        grade_order.get(x['grade'], 3),
        -x['vol_ratio']
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
        shares   = max(1, int(CAPITAL_PER_TRADE / price))
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
    exits = monitor_open_trades()

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
# SCHEDULED TASKS
# ─────────────────────────────────────────────────────────
def morning_catalyst_scan():
    global catalyst_priority
    log("CATALYST SCAN — scanning for events...")
    try:
        picks = run_catalyst_scan()
        catalyst_priority = [p['symbol'] for p in picks if isinstance(p, dict) and 'symbol' in p]
        log(f"Catalyst scan done: {len(catalyst_priority)} priority symbols → {catalyst_priority[:10]}")
        if catalyst_priority:
            send_telegram(f"⚡ Catalyst signals: {', '.join(catalyst_priority[:8])}\nAuto-trading A-grade setups first.")
    except Exception as e:
        log(f"Catalyst scan error: {e}")

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
    daily  = get_daily_pnl()
    wr_30  = get_win_rate(days=30)
    wr_day = (daily['wins'] / daily['trades'] * 100) if daily['trades'] > 0 else 0
    emoji  = '✅' if daily['pnl'] > 0 else '❌'
    send_telegram(
        f"{emoji} EOD Summary\n"
        f"Trades: {daily['trades']} | Wins: {daily['wins']} | WR: {wr_day:.0f}%\n"
        f"P&L: ${daily['pnl']:+.2f}\n"
        f"30d win rate: {wr_30:.0f}%"
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
    global traded_today, daily_trade_count, atr_cache
    traded_today      = set()
    daily_trade_count = 0
    atr_cache         = {}
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
    print(f"Capital/trade:    ${CAPITAL_PER_TRADE}")
    print(f"Stop method:      ATR × {ATR_STOP_MULT} (adaptive)")
    print(f"Trailing stop:    ATR × {ATR_TRAIL_MULT} below session high")
    print(f"Momentum fade:    ATR × {ATR_FADE_MULT} drop from high")
    print(f"Partial exit:     50% at target, trail remainder")
    print(f"Time stop:        {TIME_STOP_DAYS} days if flat")
    print(f"Entry cutoff:     {NO_ENTRY_AFTER}:00 ET")
    print(f"Min R:R:          1:{MIN_RR}")
    print("=" * 55)
    print("Scheduled: catalyst 8:15am | voice 9am | EOD 4:30pm | learning 11pm | reset midnight")
    print("Telegram:  STATUS | CANCEL | RESUME")
    print("Press CTRL+C to stop\n")

    # Background scheduler for timed tasks
    sched = BackgroundScheduler(timezone=ET)
    sched.add_job(morning_catalyst_scan,  'cron',     day_of_week='mon-fri', hour=8,  minute=15)
    sched.add_job(morning_voice_summary,  'cron',     day_of_week='mon-fri', hour=9,  minute=0)
    sched.add_job(evening_summary,        'cron',     day_of_week='mon-fri', hour=16, minute=30)
    sched.add_job(nightly_learning,       'cron',     day_of_week='mon-fri', hour=23, minute=0)
    sched.add_job(reset_daily_state,      'cron',                            hour=0,  minute=1)
    sched.add_job(poll_telegram_commands, 'interval', seconds=15)
    sched.start()

    send_telegram(f"🤖 Auto Trader v2 ON | ATR stops | {len(FULL_UNIVERSE)} stocks | ${CAPITAL_PER_TRADE}/trade")

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
