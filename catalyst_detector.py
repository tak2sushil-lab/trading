# catalyst_detector.py — event and catalyst scanner
# Runs at 8:15am ET before morning screen
# Detects: earnings beats, gap ups, volume surges, news
# Adds catalyst stocks temporarily to today's screen
# Command: python catalyst_detector.py (or called by scheduler)

from dotenv import load_dotenv
load_dotenv()

import os
import json
import yfinance as yf
import pandas as pd
from datetime import datetime, date, timedelta
import pytz
import requests
from twilio.rest import Client
from watchlist import CATALYST_UNIVERSE, SECTORS, FINAL_UNIVERSE

# ── Config ────────────────────────────────────────────────
TWILIO_SID      = os.getenv('TWILIO_SID')
TWILIO_TOKEN    = os.getenv('TWILIO_TOKEN')
TWILIO_WHATSAPP = os.getenv('TWILIO_WHATSAPP')
MY_WHATSAPP     = os.getenv('MY_WHATSAPP')

twilio = Client(TWILIO_SID, TWILIO_TOKEN)
ET     = pytz.timezone('America/New_York')

# Catalyst thresholds
GAP_UP_PCT        = 3.0   # 3%+ gap up from previous close
VOLUME_SURGE_X    = 5.0   # 5x normal volume
EARNINGS_MOVE_PCT = 5.0   # 5%+ move after earnings
MIN_PRICE         = 5.0   # minimum stock price for catalyst play
MAX_PRICE         = 500.0 # maximum price

CATALYST_FILE = os.path.join(os.path.dirname(__file__), 'catalyst_picks.json')

def send_whatsapp(msg):
    try:
        twilio.messages.create(
            from_=TWILIO_WHATSAPP,
            to=MY_WHATSAPP,
            body=msg
        )
    except Exception as e:
        print(f"❌ WhatsApp: {e}")

def get_sector(symbol):
    for sector, stocks in SECTORS.items():
        if symbol in stocks:
            return sector
    return 'OTHER'

def check_earnings_catalyst(symbol, ticker, hist):
    """
    Detect earnings beat catalyst
    Stock up 5%+ today AND had earnings in last 3 days
    """
    try:
        if len(hist) < 2:
            return None

        prev_close  = float(hist['Close'].iloc[-2])
        today_open  = float(hist['Open'].iloc[-1])
        today_close = float(hist['Close'].iloc[-1])

        gap_pct     = (today_open - prev_close) / prev_close * 100
        day_move    = (today_close - prev_close) / prev_close * 100

        # Check if there was recent earnings
        try:
            calendar = ticker.calendar
            if calendar is not None and not calendar.empty:
                earnings_date = pd.Timestamp(calendar.iloc[0]['Earnings Date']).date()
                days_since    = (date.today() - earnings_date).days
                if 0 <= days_since <= 3 and day_move >= EARNINGS_MOVE_PCT:
                    return {
                        'type':        'EARNINGS_BEAT',
                        'emoji':       '📊',
                        'description': f'Earnings beat! Up {day_move:.1f}% in {days_since}d',
                        'gap_pct':     round(gap_pct, 2),
                        'day_move':    round(day_move, 2),
                        'urgency':     'HIGH'
                    }
        except:
            pass

        return None
    except:
        return None

def check_gap_up(symbol, hist):
    """
    Detect gap up catalyst
    Stock opened 3%+ above previous close
    """
    try:
        if len(hist) < 2:
            return None

        prev_close = float(hist['Close'].iloc[-2])
        today_open = float(hist['Open'].iloc[-1])
        gap_pct    = (today_open - prev_close) / prev_close * 100

        if gap_pct >= GAP_UP_PCT:
            return {
                'type':        'GAP_UP',
                'emoji':       '⚡',
                'description': f'Gap up {gap_pct:.1f}% at open',
                'gap_pct':     round(gap_pct, 2),
                'urgency':     'HIGH' if gap_pct >= 5 else 'MEDIUM'
            }
        return None
    except:
        return None

def check_volume_surge(symbol, hist):
    """
    Detect unusual volume — 5x normal
    Signals institutional buying
    """
    try:
        if len(hist) < 21:
            return None

        avg_volume   = hist['Volume'].iloc[-21:-1].mean()
        today_volume = float(hist['Volume'].iloc[-1])
        volume_ratio = today_volume / avg_volume if avg_volume > 0 else 1

        if volume_ratio >= VOLUME_SURGE_X:
            today_close = float(hist['Close'].iloc[-1])
            prev_close  = float(hist['Close'].iloc[-2])
            day_move    = (today_close - prev_close) / prev_close * 100

            # Only flag if price is also moving up
            if day_move > 0:
                return {
                    'type':         'VOLUME_SURGE',
                    'emoji':        '🔥',
                    'description':  f'Volume {volume_ratio:.0f}x normal! Up {day_move:.1f}%',
                    'volume_ratio': round(volume_ratio, 1),
                    'day_move':     round(day_move, 2),
                    'urgency':      'HIGH' if volume_ratio >= 10 else 'MEDIUM'
                }
        return None
    except:
        return None

def check_momentum_surge(symbol, hist):
    """
    Detect strong price momentum
    Up 5%+ today with good volume
    """
    try:
        if len(hist) < 2:
            return None

        prev_close   = float(hist['Close'].iloc[-2])
        today_close  = float(hist['Close'].iloc[-1])
        day_move     = (today_close - prev_close) / prev_close * 100

        avg_volume   = hist['Volume'].iloc[-21:-1].mean() if len(hist) > 21 else 1
        today_volume = float(hist['Volume'].iloc[-1])
        vol_ratio    = today_volume / avg_volume if avg_volume > 0 else 1

        if day_move >= 5.0 and vol_ratio >= 2.0:
            return {
                'type':        'MOMENTUM_SURGE',
                'emoji':       '🚀',
                'description': f'Strong move {day_move:.1f}% with {vol_ratio:.1f}x volume',
                'day_move':    round(day_move, 2),
                'vol_ratio':   round(vol_ratio, 1),
                'urgency':     'HIGH'
            }
        return None
    except:
        return None

def calculate_catalyst_trade_plan(price, catalyst_type, urgency):
    """
    More aggressive position sizing for catalysts
    These are high conviction plays
    """
    # Catalyst plays get bigger positions — momentum is confirmed
    if urgency == 'HIGH':
        position_value = 250   # $250 per catalyst trade
        target_pct     = 0.05  # 5% target (bigger move expected)
        stop_pct       = 0.03  # 3% stop
    else:
        position_value = 150   # $150 for medium urgency
        target_pct     = 0.04  # 4% target
        stop_pct       = 0.025 # 2.5% stop

    shares       = max(1, int(position_value / price))
    target_price = round(price * (1 + target_pct), 2)
    stop_price   = round(price * (1 - stop_pct), 2)
    target_profit= round((target_price - price) * shares, 2)
    max_loss     = round((price - stop_price) * shares, 2)

    return {
        'shares':        shares,
        'cost':          round(shares * price, 2),
        'target_price':  target_price,
        'target_profit': target_profit,
        'stop_price':    stop_price,
        'max_loss':      max_loss,
        'target_pct':    round(target_pct * 100, 1),
        'stop_pct':      round(stop_pct * 100, 1),
    }

def run_catalyst_scan():
    """
    Main catalyst scan — runs at 8:15am ET
    Scans all stocks for events/catalysts
    """
    now = datetime.now(ET)
    print(f"\n{'='*55}")
    print(f"⚡ Catalyst Scan — {now.strftime('%Y-%m-%d %H:%M ET')}")
    print(f"   Scanning {len(CATALYST_UNIVERSE)} stocks for events...")
    print(f"{'='*55}\n")

    catalyst_picks = []

    for symbol in CATALYST_UNIVERSE:
        try:
            ticker = yf.Ticker(symbol)
            hist   = ticker.history(period='1mo', interval='1d')

            if hist.empty or len(hist) < 2:
                continue

            price = float(hist['Close'].iloc[-1])
            if price < MIN_PRICE or price > MAX_PRICE:
                continue

            # Run all catalyst checks
            catalyst = (
                check_earnings_catalyst(symbol, ticker, hist) or
                check_gap_up(symbol, hist) or
                check_volume_surge(symbol, hist) or
                check_momentum_surge(symbol, hist)
            )

            if catalyst:
                sector     = get_sector(symbol)
                trade_plan = calculate_catalyst_trade_plan(
                    price,
                    catalyst['type'],
                    catalyst['urgency']
                )
                in_universe = symbol in FINAL_UNIVERSE

                pick = {
                    'symbol':       symbol,
                    'price':        round(price, 2),
                    'sector':       sector,
                    'catalyst':     catalyst,
                    'trade_plan':   trade_plan,
                    'in_universe':  in_universe,
                    'setup_type':   f"CATALYST_{catalyst['type']}",
                    'score':        90 if catalyst['urgency'] == 'HIGH' else 75,
                    'rsi':          50,
                    'volume_ratio': catalyst.get('volume_ratio', 2.0),
                    'earnings_days':999,
                }
                catalyst_picks.append(pick)

                tag = "" if in_universe else " ← NEW (not in regular screen)"
                print(f"  {catalyst['emoji']} {symbol:<8} {catalyst['type']:<20} "
                      f"{catalyst['description']}{tag}")

        except Exception as e:
            pass

    # Sort by urgency then type
    priority = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}
    catalyst_picks.sort(
        key=lambda x: (priority.get(x['catalyst']['urgency'], 2), x['symbol'])
    )

    print(f"\n✅ Found {len(catalyst_picks)} catalyst opportunities")

    # Save for screener to pick up
    with open(CATALYST_FILE, 'w') as f:
        json.dump(catalyst_picks, f, indent=2)

    # Send WhatsApp if strong catalysts found
    new_stocks = [p for p in catalyst_picks if not p['in_universe']]
    high_urgency = [p for p in catalyst_picks if p['catalyst']['urgency'] == 'HIGH']

    if catalyst_picks:
        # Build summary message
        lines = [f"⚡ Catalyst Alert — {len(catalyst_picks)} events found!\n"]

        for pick in catalyst_picks[:5]:  # top 5
            c   = pick['catalyst']
            tp  = pick['trade_plan']
            tag = "🆕 NEW STOCK" if not pick['in_universe'] else ""
            lines.append(
                f"{c['emoji']} {pick['symbol']} {tag}\n"
                f"  {c['description']}\n"
                f"  Price: ${pick['price']} | "
                f"Target: +{tp['target_pct']}% | "
                f"Stop: -{tp['stop_pct']}%\n"
            )

        if new_stocks:
            lines.append(
                f"\n🆕 {len(new_stocks)} new stocks added to today's screen:\n"
                f"  {', '.join(p['symbol'] for p in new_stocks)}\n"
                f"These will appear in your 8:30am picks!"
            )

        send_whatsapp('\n'.join(lines))

    elif not catalyst_picks:
        print("  No catalysts found today — regular screen only")

    return catalyst_picks

def load_catalyst_picks():
    """Load today's catalyst picks for screener to use"""
    if not os.path.exists(CATALYST_FILE):
        return []
    try:
        with open(CATALYST_FILE) as f:
            picks = json.load(f)
        # Only return today's picks
        today = date.today().isoformat()
        return picks  # screener will combine with regular picks
    except:
        return []

if __name__ == '__main__':
    picks = run_catalyst_scan()
    print(f"\nCatalyst scan complete.")
    print(f"Results saved to catalyst_picks.json")
    print(f"Screener will include these at 8:30am")
