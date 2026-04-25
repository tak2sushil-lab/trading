# screener.py — morning stock screener
# Tech-prioritized: AI/Chips + Cloud get score boost
# Supports YES ALL command to approve all picks at once
# Command: python screener.py

from dotenv import load_dotenv
load_dotenv()

import os
import json
import time
import yfinance as yf
import pandas as pd
import ta
from datetime import datetime, date
import pytz
import requests
from twilio.rest import Client
from database import get_strategy_weights, get_win_rate, init_db
from watchlist import FINAL_UNIVERSE, SECTORS, MACRO_THEMES
from catalyst_detector import run_catalyst_scan, load_catalyst_picks

# ── Credentials ───────────────────────────────────────────
TWILIO_SID      = os.getenv('TWILIO_SID')
TWILIO_TOKEN    = os.getenv('TWILIO_TOKEN')
TWILIO_WHATSAPP = os.getenv('TWILIO_WHATSAPP')
MY_WHATSAPP     = os.getenv('MY_WHATSAPP')
BRIDGE          = 'http://127.0.0.1:8000'
ET              = pytz.timezone('America/New_York')

twilio = Client(TWILIO_SID, TWILIO_TOKEN)

# ── Config ────────────────────────────────────────────────
MAX_PICKS      = 5
MIN_CONFIDENCE = 55
CAPITAL        = 1000
MAX_PER_TRADE  = 300
MIN_PRICE      = 8
MAX_PRICE      = 400

# ── Tech priority stocks ──────────────────────────────────
# These get a score boost — your preferred universe
AI_CHIPS = [
    'NVDA', 'AMD', 'CRWV', 'NBIS', 'SMCI',
    'AVGO', 'COHR', 'LITE', 'ON', 'CLS'
]

CLOUD_SOFTWARE = [
    'SNOW', 'CRM', 'ORCL', 'PANW', 'CRWD',
    'PLTR', 'RBRK', 'S', 'PATH', 'AI'
]

TECH_PRIORITY = AI_CHIPS + CLOUD_SOFTWARE
TECH_BOOST    = 12  # Add 12 points to confidence score for tech stocks

def send_whatsapp(msg):
    try:
        twilio.messages.create(
            from_=TWILIO_WHATSAPP,
            to=MY_WHATSAPP,
            body=msg
        )
        print(f"📱 Sent: {msg[:60]}...")
    except Exception as e:
        print(f"❌ WhatsApp error: {e}")

def get_stock_data(symbol, period='3mo'):
    try:
        ticker = yf.Ticker(symbol)
        df     = ticker.history(period=period)
        if df.empty or len(df) < 20:
            return None
        return df
    except Exception as e:
        print(f"  ⚠ {symbol}: {e}")
        return None

def get_earnings_days(symbol):
    try:
        ticker   = yf.Ticker(symbol)
        calendar = ticker.calendar
        if calendar is not None and not calendar.empty:
            earnings_date = calendar.iloc[0]['Earnings Date']
            if earnings_date:
                days = (pd.Timestamp(earnings_date).date() - date.today()).days
                return max(0, days)
        return 999
    except:
        return 999

def calculate_signals(symbol, df, weights):
    try:
        close  = df['Close']
        volume = df['Volume']
        high   = df['High']
        low    = df['Low']

        # ── RSI ───────────────────────────────────────────
        rsi         = ta.momentum.RSIIndicator(close, window=14).rsi()
        current_rsi = rsi.iloc[-1]

        if 45 <= current_rsi <= 65:
            rsi_score = 100
        elif 35 <= current_rsi < 45:
            rsi_score = 70
        elif 65 < current_rsi <= 75:
            rsi_score = 60
        elif current_rsi > 75:
            rsi_score = 20
        else:
            rsi_score = 40

        # ── Volume surge ──────────────────────────────────
        avg_volume   = volume.rolling(20).mean().iloc[-1]
        today_volume = volume.iloc[-1]
        volume_ratio = today_volume / avg_volume if avg_volume > 0 else 1

        if volume_ratio >= 3:
            volume_score = 100
        elif volume_ratio >= 2:
            volume_score = 85
        elif volume_ratio >= 1.5:
            volume_score = 65
        else:
            volume_score = 30

        # ── Price momentum ────────────────────────────────
        price_today   = close.iloc[-1]
        price_1d_ago  = close.iloc[-2] if len(close) > 1 else price_today
        price_5d_ago  = close.iloc[-5] if len(close) > 5 else price_today
        price_20d_ago = close.iloc[-20] if len(close) > 20 else price_today

        change_1d  = ((price_today - price_1d_ago) / price_1d_ago) * 100
        change_5d  = ((price_today - price_5d_ago) / price_5d_ago) * 100
        change_20d = ((price_today - price_20d_ago) / price_20d_ago) * 100

        if change_1d > 1 and change_5d > 2 and change_20d > 5:
            momentum_score = 100
        elif change_1d > 0.5 and change_5d > 1:
            momentum_score = 75
        elif change_1d > 0 and change_5d > 0:
            momentum_score = 55
        elif change_1d < -3:
            momentum_score = 10
        else:
            momentum_score = 35

        # ── Moving average alignment ───────────────────────
        ma20 = close.rolling(20).mean().iloc[-1]
        ma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else ma20

        if price_today > ma20 > ma50:
            ma_score = 100
        elif price_today > ma20:
            ma_score = 70
        elif price_today > ma50:
            ma_score = 50
        else:
            ma_score = 20

        # ── MACD ──────────────────────────────────────────
        try:
            macd_ind  = ta.trend.MACD(close)
            macd_line = macd_ind.macd().iloc[-1]
            macd_sig  = macd_ind.macd_signal().iloc[-1]
            macd_hist = macd_ind.macd_diff().iloc[-1]
            prev_hist = macd_ind.macd_diff().iloc[-2] if len(close) > 2 else 0

            if macd_line > macd_sig and macd_hist > 0 and macd_hist > prev_hist:
                macd_score = 100
            elif macd_line > macd_sig:
                macd_score = 70
            elif macd_hist > prev_hist:
                macd_score = 50
            else:
                macd_score = 25
        except:
            macd_score = 50

        # ── Bull flag pattern ─────────────────────────────
        if len(df) >= 10:
            recent_high = high.iloc[-10:].max()
            recent_low  = low.iloc[-5:].min()
            flag_range  = (recent_high - recent_low) / recent_high * 100

            if flag_range < 5:
                flag_score = 90
            elif flag_range < 8:
                flag_score = 60
            else:
                flag_score = 30
        else:
            flag_score = 50

        # ── Weighted final score ───────────────────────────
        raw_score = (
            rsi_score      * weights['rsi']      * 0.20 +
            volume_score   * weights['volume']   * 0.25 +
            momentum_score * weights['momentum'] * 0.20 +
            ma_score       * 0.15 +
            macd_score     * 0.10 +
            flag_score     * 0.10
        )

        weight_sum = (weights['rsi'] * 0.20 + weights['volume'] * 0.25 +
                      weights['momentum'] * 0.20 + 0.15 + 0.10 + 0.10)
        score = min(100, raw_score / weight_sum)

        # ── Tech boost ────────────────────────────────────
        is_tech = symbol in TECH_PRIORITY
        if is_tech:
            score = min(100, score + TECH_BOOST)

        return {
            'score':          round(score, 1),
            'rsi':            round(current_rsi, 1),
            'volume_ratio':   round(volume_ratio, 2),
            'change_1d':      round(change_1d, 2),
            'change_5d':      round(change_5d, 2),
            'price':          round(price_today, 2),
            'is_tech':        is_tech,
            'rsi_score':      rsi_score,
            'volume_score':   volume_score,
            'momentum_score': momentum_score,
            'ma_score':       ma_score,
        }

    except Exception as e:
        print(f"  ⚠ Signal error for {symbol}: {e}")
        return None

def calculate_trade_plan(symbol, price, score):
    """Position sizing based on confidence"""
    if score >= 80:
        position_value = min(MAX_PER_TRADE, CAPITAL * 0.30)
    elif score >= 70:
        position_value = min(MAX_PER_TRADE, CAPITAL * 0.20)
    else:
        position_value = min(MAX_PER_TRADE, CAPITAL * 0.15)

    shares        = max(1, int(position_value / price))
    actual_cost   = shares * price
    target_pct    = 0.025 if score >= 75 else 0.02
    target_price  = round(price * (1 + target_pct), 2)
    target_profit = round((target_price - price) * shares, 2)
    stop_price    = round(price * 0.985, 2)
    max_loss      = round((price - stop_price) * shares, 2)

    return {
        'shares':        shares,
        'cost':          round(actual_cost, 2),
        'target_price':  target_price,
        'target_profit': target_profit,
        'stop_price':    stop_price,
        'max_loss':      max_loss,
        'risk_reward':   round(target_profit / max_loss, 2) if max_loss > 0 else 0
    }

def get_sector(symbol):
    for sector, stocks in SECTORS.items():
        if symbol in stocks:
            return sector
    return 'OTHER'

def save_pending_picks(picks):
    """Save all picks to a single file for trader.py"""
    pending_file = os.path.join(os.path.dirname(__file__), 'pending_picks.json')
    with open(pending_file, 'w') as f:
        json.dump(picks, f, indent=2)
    print(f"✅ Saved {len(picks)} pending picks")

def run_morning_screen():
    print(f"\n{'='*55}")
    print(f"🔍 Morning Screen — {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}")
    print(f"Tech priority: AI/Chips + Cloud (+{TECH_BOOST} pts boost)")
    print(f"{'='*55}")

    weights = get_strategy_weights()
    results = []

    for symbol in FINAL_UNIVERSE:
        print(f"  Scanning {symbol}...", end=' ')

        df = get_stock_data(symbol)
        if df is None:
            print("no data")
            continue

        price = df['Close'].iloc[-1]
        if price < MIN_PRICE or price > MAX_PRICE:
            print(f"price ${price:.2f} out of range")
            continue

        signals = calculate_signals(symbol, df, weights)
        if signals is None:
            print("signal error")
            continue

        earnings_days = get_earnings_days(symbol)
        if earnings_days < 3:
            print(f"earnings in {earnings_days}d — skip")
            continue
        elif earnings_days < 14:
            signals['score'] = signals['score'] * 0.8

        tech_tag = " 🤖TECH" if signals['is_tech'] else ""
        print(f"score={signals['score']:.0f}{tech_tag}")

        if signals['score'] >= MIN_CONFIDENCE:
            sector     = get_sector(symbol)
            win_rate   = get_win_rate(symbol, days=30)
            trade_plan = calculate_trade_plan(symbol, price, signals['score'])
            results.append({
                'symbol':        symbol,
                'sector':        sector,
                'earnings_days': earnings_days,
                'win_rate':      win_rate,
                'setup_type':    'MOMENTUM_TECH' if signals['is_tech'] else 'MOMENTUM',
                **signals,
                **trade_plan
            })

    # ── Add catalyst picks ────────────────────────────────
    catalyst_picks  = load_catalyst_picks()
    catalyst_symbols = [p['symbol'] for p in catalyst_picks]

    for cp in catalyst_picks:
        if cp['symbol'] not in [r['symbol'] for r in results]:
            cp['is_tech']  = cp['symbol'] in TECH_PRIORITY
            cp['win_rate'] = get_win_rate(cp['symbol'], days=30)
            results.append(cp)
            print(f"  ⚡ Catalyst added: {cp['symbol']} — {cp['catalyst']['description']}")

    # Sort: HIGH urgency catalysts first, then tech, then score
    def sort_key(x):
        is_cat     = x['symbol'] in catalyst_symbols
        urgency    = x.get('catalyst', {}).get('urgency', 'NONE')
        urg_rank   = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2, 'NONE': 3}.get(urgency, 3)
        return (0 if is_cat else 1, urg_rank, not x['is_tech'], -x['score'])

    results.sort(key=sort_key)
    top_picks = results[:MAX_PICKS]

    print(f"\n✅ Found {len(results)} candidates, sending top {len(top_picks)}")
    print(f"   Catalyst picks: {sum(1 for p in top_picks if p['symbol'] in catalyst_symbols)}")
    print(f"   Tech picks:     {sum(1 for p in top_picks if p['is_tech'])}")
    print(f"   Other picks:    {sum(1 for p in top_picks if not p['is_tech'])}")

    if not top_picks:
        send_whatsapp(
            f"Morning screen complete.\n"
            f"No strong setups today. Market may be choppy.\n"
            f"Win rate (30d): {get_win_rate(days=30):.0f}%\n"
            f"Sit tight — wait for better setups."
        )
        return []

    # Picks will be saved after sending (with normalized keys)

    # Send overview first
    tech_count  = sum(1 for p in top_picks if p['is_tech'])
    other_count = len(top_picks) - tech_count
    send_whatsapp(
        f"Morning screen complete!\n"
        f"Found {len(top_picks)} strong setups:\n"
        f"  Tech (AI/Cloud): {tech_count} picks\n"
        f"  Other: {other_count} picks\n"
        f"Win rate (30d): {get_win_rate(days=30):.0f}%\n"
        f"Sending picks now...\n"
        f"Tip: Reply YES ALL to approve everything"
    )
    time.sleep(2)

    # Send each pick
    for i, pick in enumerate(top_picks, 1):

        # ── Normalize keys — handle both regular and catalyst picks ──
        tp            = pick.get('trade_plan', {})  # catalyst picks nest trade plan
        price         = pick.get('price', 0)
        shares        = pick.get('shares')        or tp.get('shares', 1)
        cost          = pick.get('cost')          or tp.get('cost', round(shares * price, 2))
        target_price  = pick.get('target_price')  or tp.get('target_price', round(price * 1.045, 2))
        stop_price    = pick.get('stop_price')    or tp.get('stop_price',   round(price * 0.97, 2))
        target_profit = pick.get('target_profit') or tp.get('target_profit', 0)
        max_loss      = pick.get('max_loss')      or tp.get('max_loss', 0)
        risk_reward   = pick.get('risk_reward')   or tp.get('risk_reward', 0)
        rsi           = pick.get('rsi', 50)
        volume_ratio  = pick.get('volume_ratio')  or pick.get('catalyst', {}).get('volume_ratio', 1.0)
        change_1d     = pick.get('change_1d')     or pick.get('catalyst', {}).get('day_move', 0)
        change_5d     = pick.get('change_5d', 0)
        earnings_days = pick.get('earnings_days', 999)
        win_rate      = pick.get('win_rate', 0)
        is_tech       = pick.get('is_tech', False)
        sector        = pick.get('sector', 'OTHER')
        score         = pick.get('score', 75)

        pct_to_target = round((target_price - price) / price * 100, 1) if price > 0 else 0

        # ── Badge ─────────────────────────────────────────────────────
        catalyst      = pick.get('catalyst')
        if catalyst:
            tech_badge = f"CATALYST {catalyst['emoji']}"
        elif is_tech:
            tech_badge = "AI/TECH PICK"
        else:
            tech_badge = "PICK"

        # ── Notes ─────────────────────────────────────────────────────
        catalyst_note = ""
        if catalyst:
            catalyst_note = f"Catalyst: {catalyst['description']}\n"

        earnings_note = ""
        if earnings_days < 14:
            earnings_note = f"Earnings in {earnings_days} days\n"
        elif earnings_days < 999:
            earnings_note = f"Earnings in {earnings_days} days - safe\n"

        win_note = ""
        if win_rate > 0:
            win_note = f"Your win rate on {pick['symbol']}: {win_rate:.0f}%\n"

        msg = (
            f"{tech_badge} {i}/{len(top_picks)} - {pick['symbol']}\n"
            f"Sector: {sector}\n"
            f"Price: ${price}\n"
            f"Confidence: {score:.0f}/100\n"
            f"\n"
            f"{catalyst_note}"
            f"Signals:\n"
            f"  RSI: {rsi:.0f}\n"
            f"  Volume: {volume_ratio:.1f}x normal\n"
            f"  Today: {change_1d:+.1f}%\n"
            f"  5-day: {change_5d:+.1f}%\n"
            f"\n"
            f"Trade plan:\n"
            f"  Buy: {shares} shares @ ${price}\n"
            f"  Cost: ${cost}\n"
            f"  Target: ${target_price} (+{pct_to_target}%)\n"
            f"  Profit if hit: +${target_profit}\n"
            f"  Stop loss: ${stop_price}\n"
            f"  Max loss: -${max_loss}\n"
            f"  Risk/Reward: 1:{risk_reward}\n"
            f"\n"
            f"{earnings_note}"
            f"{win_note}"
            f"Reply:\n"
            f"YES - trade this\n"
            f"NO - skip this\n"
            f"YES ALL - approve all picks"
        )

        # Also normalize the pick dict for trader.py
        pick['shares']        = shares
        pick['cost']          = cost
        pick['target_price']  = target_price
        pick['stop_price']    = stop_price
        pick['target_profit'] = target_profit
        pick['max_loss']      = max_loss
        pick['risk_reward']   = risk_reward
        pick['rsi']           = rsi
        pick['volume_ratio']  = volume_ratio
        pick['change_1d']     = change_1d
        pick['change_5d']     = change_5d

        send_whatsapp(msg)
        time.sleep(3)

    # Save all picks AFTER sending (keys are normalized in send loop)
    save_pending_picks(top_picks)
    return top_picks

if __name__ == '__main__':
    init_db()
    picks = run_morning_screen()
    print(f"\nScreen complete. {len(picks)} picks sent to WhatsApp.")
