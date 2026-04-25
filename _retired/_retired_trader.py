# trader.py — trade monitor + WhatsApp approval handler
# Supports: YES, NO, YES ALL commands
# Monitors trades every 15 min with smart momentum exits
# Command: python trader.py

from dotenv import load_dotenv
load_dotenv()

import os
import json
import time
import yfinance as yf
import requests
from datetime import datetime, date
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from twilio.rest import Client
from database import (
    init_db, log_trade_entry, log_trade_exit,
    get_open_trades, get_daily_pnl, get_win_rate
)

# ── Credentials ───────────────────────────────────────────
TWILIO_SID      = os.getenv('TWILIO_SID')
TWILIO_TOKEN    = os.getenv('TWILIO_TOKEN')
TWILIO_WHATSAPP = os.getenv('TWILIO_WHATSAPP')
MY_WHATSAPP     = os.getenv('MY_WHATSAPP')
BRIDGE          = 'http://127.0.0.1:8000'
ET              = pytz.timezone('America/New_York')

twilio    = Client(TWILIO_SID, TWILIO_TOKEN)
scheduler = BlockingScheduler(timezone=ET)

PENDING_FILE   = os.path.join(os.path.dirname(__file__), 'pending_picks.json')
PROCESSED_FILE = os.path.join(os.path.dirname(__file__), 'processed_msgs.json')

# Track which picks have been approved/rejected
approved_picks  = set()
rejected_picks  = set()
pick_index      = 0   # which pick we're waiting reply for

def send_whatsapp(msg):
    try:
        twilio.messages.create(
            from_=TWILIO_WHATSAPP,
            to=MY_WHATSAPP,
            body=msg
        )
        print(f"📱 {msg[:80]}...")
    except Exception as e:
        print(f"❌ WhatsApp: {e}")

def load_pending_picks():
    """Load today's picks from screener"""
    if not os.path.exists(PENDING_FILE):
        return []
    try:
        with open(PENDING_FILE) as f:
            return json.load(f)
    except:
        return []

def load_processed_msgs():
    """Track already-processed message SIDs"""
    if not os.path.exists(PROCESSED_FILE):
        return set()
    try:
        with open(PROCESSED_FILE) as f:
            return set(json.load(f))
    except:
        return set()

def save_processed_msgs(processed):
    with open(PROCESSED_FILE, 'w') as f:
        json.dump(list(processed), f)

def get_current_price(symbol):
    """Get live price — bridge first, Yahoo Finance fallback"""
    try:
        r = requests.get(f"{BRIDGE}/quote/{symbol}", timeout=8)
        d = r.json()
        if d.get('best_price'):
            return d['best_price']
    except:
        pass
    try:
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(period='1d', interval='5m')
        if not hist.empty:
            return round(hist['Close'].iloc[-1], 2)
    except:
        pass
    return None

def place_trade(pick, current_price):
    """Place a paper trade for an approved pick"""
    symbol       = pick['symbol']
    shares       = pick['shares']
    target_price = pick['target_price']
    stop_price   = pick['stop_price']

    try:
        r = requests.post(f"{BRIDGE}/order", json={
            "symbol":     symbol,
            "qty":        shares,
            "side":       "BUY",
            "order_type": "MARKET"
        }, timeout=10)
        d        = r.json()
        order_id = str(d.get('orderId', ''))

        trade_id = log_trade_entry(
            symbol        = symbol,
            entry_price   = current_price,
            shares        = shares,
            target_price  = target_price,
            stop_price    = stop_price,
            setup_type    = pick.get('setup_type', 'MOMENTUM'),
            rsi           = pick.get('rsi', 50),
            volume_ratio  = pick.get('volume_ratio', 1),
            sector        = pick.get('sector', 'OTHER'),
            earnings_days = pick.get('earnings_days', 999),
            confidence    = pick.get('score', 60),
            order_id      = order_id
        )

        pct_to_target = round((target_price - current_price) / current_price * 100, 1)
        print(f"✅ Placed: {symbol} x{shares} @ ${current_price} (ID:{trade_id})")

        send_whatsapp(
            f"Trade placed!\n"
            f"{symbol} x{shares} shares @ ${current_price}\n"
            f"Target: ${target_price} (+{pct_to_target}%)\n"
            f"Stop: ${stop_price}\n"
            f"Monitoring every 15 min."
        )
        return trade_id

    except Exception as e:
        print(f"❌ Trade failed for {symbol}: {e}")
        send_whatsapp(f"Trade failed for {symbol}. Error: {str(e)[:50]}")
        return None

def exit_trade_position(trade_id, symbol, shares, exit_price, reason):
    """Exit a trade and notify"""
    try:
        requests.post(f"{BRIDGE}/order", json={
            "symbol":     symbol,
            "qty":        shares,
            "side":       "SELL",
            "order_type": "MARKET"
        }, timeout=10)
        pnl   = log_trade_exit(trade_id, exit_price, reason)
        emoji = "✅" if pnl and pnl > 0 else "❌"
        print(f"{emoji} Exited {symbol}: ${exit_price} | P&L: ${pnl} | {reason}")
        return pnl
    except Exception as e:
        print(f"❌ Exit failed: {e}")
        return None

def handle_reply(reply_text):
    """
    Process WhatsApp reply:
    YES      → approve current pending pick
    NO       → skip current pending pick
    YES ALL  → approve all remaining picks
    STATUS   → show open trades + daily P&L
    CANCEL   → cancel all pending picks
    """
    global pick_index, approved_picks, rejected_picks
    reply = reply_text.strip().upper()
    picks = load_pending_picks()

    print(f"📱 Reply received: {reply}")

    # ── STATUS command ────────────────────────────────────
    if reply == 'STATUS':
        open_trades = get_open_trades()
        daily       = get_daily_pnl()
        wr          = get_win_rate(days=30)
        msg = (
            f"Status update:\n"
            f"Open trades: {len(open_trades)}\n"
            f"Today P&L: ${daily['pnl']:+.2f}\n"
            f"Today trades: {daily['trades']}\n"
            f"Today wins: {daily['wins']}\n"
            f"30d win rate: {wr:.0f}%"
        )
        if open_trades:
            msg += "\n\nOpen positions:"
            for t in open_trades:
                current = get_current_price(t['symbol'])
                if current:
                    pnl_pct = ((current - t['entry_price']) / t['entry_price']) * 100
                    msg += f"\n  {t['symbol']}: ${current} ({pnl_pct:+.1f}%)"
        send_whatsapp(msg)
        return

    # ── CANCEL command ────────────────────────────────────
    if reply in ['CANCEL', 'STOP', 'CLEAR']:
        if os.path.exists(PENDING_FILE):
            os.remove(PENDING_FILE)
        approved_picks = set()
        rejected_picks = set()
        pick_index     = 0
        send_whatsapp("All pending picks cancelled. No trades will be placed.")
        return

    if not picks:
        send_whatsapp("No pending picks right now. Wait for tomorrow's morning screen at 8:30am ET.")
        return

    # ── YES ALL command ───────────────────────────────────
    if reply in ['YES ALL', 'YESALL', 'ALL', 'YES ALL!']:
        remaining = [p for p in picks
                     if p['symbol'] not in approved_picks
                     and p['symbol'] not in rejected_picks]

        if not remaining:
            send_whatsapp("All picks already processed!")
            return

        send_whatsapp(f"Approving all {len(remaining)} remaining picks now...")

        for pick in remaining:
            current_price = get_current_price(pick['symbol'])
            if not current_price:
                send_whatsapp(f"Could not get price for {pick['symbol']} — skipped.")
                rejected_picks.add(pick['symbol'])
                continue

            place_trade(pick, current_price)
            approved_picks.add(pick['symbol'])
            time.sleep(2)

        # Clean up
        if os.path.exists(PENDING_FILE):
            os.remove(PENDING_FILE)
        return

    # ── YES command ───────────────────────────────────────
    if reply in ['YES', 'Y', 'YEP', 'GO', 'BUY', 'YEAH']:
        # Find next unprocessed pick
        pending = [p for p in picks
                   if p['symbol'] not in approved_picks
                   and p['symbol'] not in rejected_picks]

        if not pending:
            send_whatsapp("All picks already processed!")
            return

        pick = pending[0]
        symbol = pick['symbol']

        current_price = get_current_price(symbol)
        if not current_price:
            send_whatsapp(f"Could not get current price for {symbol}. Skipped.")
            rejected_picks.add(symbol)
        else:
            place_trade(pick, current_price)
            approved_picks.add(symbol)

        # Tell user about next pick if any
        remaining = [p for p in picks
                     if p['symbol'] not in approved_picks
                     and p['symbol'] not in rejected_picks]
        if remaining:
            next_pick = remaining[0]
            send_whatsapp(
                f"Next pick: {next_pick['symbol']} "
                f"(confidence {next_pick['score']:.0f}/100)\n"
                f"Reply YES, NO, or YES ALL"
            )
        else:
            send_whatsapp("All picks processed! Monitoring trades every 15 min.")
            if os.path.exists(PENDING_FILE):
                os.remove(PENDING_FILE)
        return

    # ── NO command ────────────────────────────────────────
    if reply in ['NO', 'N', 'SKIP', 'PASS', 'NOPE']:
        pending = [p for p in picks
                   if p['symbol'] not in approved_picks
                   and p['symbol'] not in rejected_picks]

        if not pending:
            send_whatsapp("All picks already processed!")
            return

        pick   = pending[0]
        symbol = pick['symbol']
        rejected_picks.add(symbol)

        send_whatsapp(f"Skipped {symbol}.")

        # Next pick
        remaining = [p for p in picks
                     if p['symbol'] not in approved_picks
                     and p['symbol'] not in rejected_picks]
        if remaining:
            next_pick = remaining[0]
            send_whatsapp(
                f"Next: {next_pick['symbol']} "
                f"(confidence {next_pick['score']:.0f}/100)\n"
                f"Reply YES, NO, or YES ALL"
            )
        else:
            send_whatsapp("All picks processed!")
            if os.path.exists(PENDING_FILE):
                os.remove(PENDING_FILE)
        return

def evaluate_exit(trade, current_price, price_history):
    """Smart momentum-based exit logic"""
    entry_price  = trade['entry_price']
    target_price = trade['target_price']
    stop_price   = trade['stop_price']
    pnl_pct      = ((current_price - entry_price) / entry_price) * 100

    # Hard stop loss
    if current_price <= stop_price:
        return 'EXIT', f'Stop loss hit ({pnl_pct:.1f}%)'

    # Target hit — check if momentum still strong
    if current_price >= target_price:
        if len(price_history) >= 3:
            recent_trend = price_history[-1] - price_history[-3]
            if recent_trend > 0 and pnl_pct < 5:
                # Update trailing stop to protect profits
                return 'HOLD', f'Target hit, momentum strong — letting run ({pnl_pct:+.1f}%)'
        return 'EXIT', f'Target reached ({pnl_pct:+.1f}%)'

    # Momentum fading — drop 2%+ from intraday high but still profitable
    if len(price_history) >= 4:
        intraday_high  = max(price_history)
        drop_from_high = ((intraday_high - current_price) / intraday_high) * 100
        if drop_from_high > 2 and pnl_pct > 0.5:
            return 'EXIT', f'Momentum fading, locking profits ({pnl_pct:+.1f}%)'

    # End of day — always close
    now = datetime.now(ET)
    if now.hour == 15 and now.minute >= 45:
        return 'EXIT', f'End of day close ({pnl_pct:+.1f}%)'

    return 'HOLD', f'Holding ({pnl_pct:+.1f}%)'

# Price history per trade
price_history = {}

@scheduler.scheduled_job('cron', day_of_week='mon-fri',
                          hour='9-15', minute='*/15')
def monitor_and_check_replies():
    """Every 15 min: check WhatsApp replies + monitor open trades"""
    now = datetime.now(ET)
    if now.hour == 9 and now.minute < 31:
        return
    if now.hour >= 16:
        return

    print(f"\n[{now.strftime('%H:%M')}] Checking replies + monitoring trades...")

    # ── Check WhatsApp for new replies ───────────────────
    processed = load_processed_msgs()
    try:
        messages = twilio.messages.list(
            to=TWILIO_WHATSAPP,
            limit=5
        )
        for msg in messages:
            if msg.sid not in processed:
                processed.add(msg.sid)
                body = msg.body.strip()
                print(f"  New message: {body}")
                handle_reply(body)
        save_processed_msgs(processed)
    except Exception as e:
        print(f"  Reply check error: {e}")

    # ── Monitor open trades ───────────────────────────────
    open_trades = get_open_trades()
    if not open_trades:
        print("  No open trades")
        return

    print(f"  Monitoring {len(open_trades)} trade(s)")

    for trade in open_trades:
        trade_id = trade['id']
        symbol   = trade['symbol']
        shares   = trade['shares']

        current_price = get_current_price(symbol)
        if not current_price:
            print(f"  {symbol}: no price")
            continue

        if trade_id not in price_history:
            price_history[trade_id] = []
        price_history[trade_id].append(current_price)
        if len(price_history[trade_id]) > 8:
            price_history[trade_id].pop(0)

        decision, reason = evaluate_exit(
            trade, current_price, price_history[trade_id]
        )

        pnl_pct = ((current_price - trade['entry_price']) / trade['entry_price']) * 100
        print(f"  {symbol}: ${current_price} ({pnl_pct:+.1f}%) → {decision}")

        if decision == 'EXIT':
            pnl   = exit_trade_position(trade_id, symbol, shares, current_price, reason)
            emoji = "✅" if pnl and pnl > 0 else "❌"
            send_whatsapp(
                f"{emoji} {symbol} CLOSED\n"
                f"Exit: ${current_price}\n"
                f"Entry: ${trade['entry_price']}\n"
                f"P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
                f"Reason: {reason}"
            )
            if trade_id in price_history:
                del price_history[trade_id]

@scheduler.scheduled_job('cron', day_of_week='mon-fri', hour=16, minute=30)
def evening_summary():
    """4:30pm daily summary"""
    daily = get_daily_pnl()
    wr_30 = get_win_rate(days=30)

    if daily['trades'] == 0:
        send_whatsapp(
            f"End of day summary\n"
            f"No trades today.\n"
            f"30d win rate: {wr_30:.0f}%\n"
            f"Tomorrow screen: 8:30am ET"
        )
        return

    win_rate = (daily['wins'] / daily['trades'] * 100) if daily['trades'] > 0 else 0
    emoji    = "✅" if daily['pnl'] > 0 else "❌"

    send_whatsapp(
        f"{emoji} End of day\n"
        f"Trades: {daily['trades']} | Wins: {daily['wins']}\n"
        f"Win rate today: {win_rate:.0f}%\n"
        f"P&L today: ${daily['pnl']:+.2f}\n"
        f"30d win rate: {wr_30:.0f}%\n"
        f"Tomorrow screen: 8:30am ET"
    )

if __name__ == '__main__':
    init_db()
    print("✅ Trader started")
    print("📱 Monitoring WhatsApp for YES/NO/YES ALL replies")
    print("📊 Checking open trades every 15 minutes")
    print("\nCommands you can send via WhatsApp:")
    print("  YES      → approve next pick")
    print("  NO       → skip next pick")
    print("  YES ALL  → approve all picks at once")
    print("  STATUS   → see open trades + P&L")
    print("  CANCEL   → cancel all pending picks")
    print("\nPress CTRL+C to stop\n")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("\nTrader stopped.")
