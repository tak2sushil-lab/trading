# scheduler.py — master scheduler (v2)
# Runs ALL automated tasks:
#   8:15am  → catalyst scan
#   8:30am  → morning screen + regime check + WhatsApp picks
#   9:00am  → morning portfolio summary (voice)
#   Every 15m → trade monitoring (via trader.py)
#   Every 30m → stop loss check
#   4:30pm  → evening summary
#   11:00pm → learning cycle
# Command: python scheduler.py

from dotenv import load_dotenv
load_dotenv()

import os
import requests
import pyttsx3
from apscheduler.schedulers.blocking import BlockingScheduler
from datetime import datetime, date
import pytz
from database import init_db, get_win_rate, get_daily_pnl
from screener import run_morning_screen
from catalyst_detector import run_catalyst_scan
from learner import run_learning_cycle
from regime_detector import get_regime, regime_summary
from twilio.rest import Client

ET              = pytz.timezone('America/New_York')
scheduler       = BlockingScheduler(timezone=ET)
BRIDGE          = 'http://127.0.0.1:8000'

TWILIO_SID      = os.getenv('TWILIO_SID')
TWILIO_TOKEN    = os.getenv('TWILIO_TOKEN')
TWILIO_WHATSAPP = os.getenv('TWILIO_WHATSAPP')
MY_WHATSAPP     = os.getenv('MY_WHATSAPP')

RULE3_STOP_LOSS = float(os.getenv('RULE3_STOP_LOSS_PCT', '5.0'))
RULE3_ENABLED   = os.getenv('RULE3_ENABLED', 'true').lower() == 'true'

twilio = Client(TWILIO_SID, TWILIO_TOKEN)
engine = pyttsx3.init()
engine.setProperty('rate', 165)

def speak(text):
    print(f"[SPEAK] {text}")
    engine.say(text)
    engine.runAndWait()

def log(msg):
    print(f"[{datetime.now(ET).strftime('%H:%M:%S')}] {msg}")

def send_whatsapp(msg):
    try:
        twilio.messages.create(from_=TWILIO_WHATSAPP, to=MY_WHATSAPP, body=msg)
    except Exception as e:
        log(f"WhatsApp error: {e}")

def get_quote(symbol):
    try:
        r = requests.get(f"{BRIDGE}/quote/{symbol}", timeout=10)
        return r.json()
    except:
        return None

def get_portfolio():
    try:
        r = requests.get(f"{BRIDGE}/portfolio", timeout=10)
        return r.json()
    except:
        return []

def get_account():
    try:
        r = requests.get(f"{BRIDGE}/account", timeout=10)
        return r.json()
    except:
        return {}

def place_order(symbol, qty, side, order_type='MARKET'):
    try:
        r = requests.post(f"{BRIDGE}/order", json={
            "symbol": symbol, "qty": qty,
            "side": side, "order_type": order_type
        }, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

# ─────────────────────────────────────────────────────────
# CATALYST SCAN — 8:15am ET
# ─────────────────────────────────────────────────────────
@scheduler.scheduled_job('cron', day_of_week='mon-fri', hour=8, minute=15)
def catalyst_scan():
    log("CATALYST SCAN — checking for events and news...")
    try:
        picks = run_catalyst_scan()
        log(f"Catalyst scan done. {len(picks)} events found.")
    except Exception as e:
        log(f"Catalyst scan error: {e}")

# ─────────────────────────────────────────────────────────
# MORNING SCREEN — 8:30am ET
# ─────────────────────────────────────────────────────────
@scheduler.scheduled_job('cron', day_of_week='mon-fri', hour=8, minute=30)
def morning_screen():
    log("MORNING SCREEN — checking regime + scanning watchlist...")
    try:
        # Get market regime first — send to WhatsApp before picks
        regime = get_regime()
        summary = regime_summary(regime)

        if regime['regime'] == 'CHOPPY':
            send_whatsapp(
                f"{summary}\n\n"
                f"No picks today — market too choppy.\n"
                f"Sit tight. Tomorrow will be better."
            )
            log("CHOPPY market — skipping screen.")
            return []

        send_whatsapp(f"Morning regime check:\n{summary}")

        picks = run_morning_screen()
        log(f"Screen done. {len(picks)} picks sent to WhatsApp.")
        return picks
    except Exception as e:
        log(f"Screen error: {e}")
        send_whatsapp(f"Morning screen error: {e}\nCheck logs.")
        return []

# ─────────────────────────────────────────────────────────
# MORNING PORTFOLIO SUMMARY — 9:00am ET (voice)
# ─────────────────────────────────────────────────────────
@scheduler.scheduled_job('cron', day_of_week='mon-fri', hour=9, minute=0)
def morning_summary():
    log("Morning summary")
    account   = get_account()
    positions = get_portfolio()
    pnl       = account.get('UnrealizedPnL', 0) or 0
    net_liq   = account.get('NetLiquidation', 0) or 0
    buying    = account.get('BuyingPower', 0) or 0
    wr_30d    = get_win_rate(days=30)

    pnl_word = "up" if pnl >= 0 else "down"
    pos_text = f"Holding {len(positions)} positions. " if positions else ""

    speak(
        f"Good morning! Portfolio summary. "
        f"Account value {net_liq:,.0f} dollars. "
        f"You are {pnl_word} {abs(pnl):,.0f} dollars. "
        f"Buying power {buying:,.0f} dollars. "
        f"{pos_text}"
        f"30 day win rate is {wr_30d:.0f} percent. "
        f"Have a great trading day!"
    )

# ─────────────────────────────────────────────────────────
# STOP LOSS CHECK — every 30 min during market hours
# ─────────────────────────────────────────────────────────
@scheduler.scheduled_job('cron', day_of_week='mon-fri',
                          hour='9-15', minute='1,31')
def stop_loss_check():
    if not RULE3_ENABLED:
        return
    now = datetime.now(ET)
    if now.hour == 9 and now.minute < 31:
        return
    if now.hour == 15 and now.minute > 55:
        return

    log("Stop loss check")
    positions = get_portfolio()

    for pos in positions:
        symbol   = pos['symbol']
        qty      = pos['qty']
        avg_cost = pos['avgCost']
        if not qty or not avg_cost:
            continue

        quote = get_quote(symbol)
        if not quote or not quote.get('best_price'):
            continue

        current = quote['best_price']
        pnl_pct = ((current - avg_cost) / avg_cost) * 100
        log(f"  {symbol}: {pnl_pct:+.1f}%")

        if pnl_pct <= -RULE3_STOP_LOSS:
            result = place_order(symbol, qty, 'SELL')
            if 'error' not in result:
                speak(f"Stop loss! Sold {symbol}. Down {abs(pnl_pct):.0f} percent.")
                send_whatsapp(
                    f"Stop loss triggered!\n"
                    f"{symbol} x{qty} sold at ${current}\n"
                    f"Down {abs(pnl_pct):.1f}% from avg ${avg_cost}"
                )

# ─────────────────────────────────────────────────────────
# NIGHTLY LEARNING — 11:00pm
# ─────────────────────────────────────────────────────────
@scheduler.scheduled_job('cron', day_of_week='mon-fri', hour=23, minute=0)
def nightly_learning():
    log("LEARNING CYCLE — analysing today's trades...")
    try:
        run_learning_cycle()
        log("Learning complete. Strategy updated for tomorrow.")
    except Exception as e:
        log(f"Learning error: {e}")

# ─────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────
if __name__ == '__main__':
    init_db()

    print("\n⏰ Master Scheduler v2 Started")
    print("=" * 55)
    print("8:15am ET  → Catalyst scan")
    print("8:30am ET  → Regime check + Morning screen")
    print("9:00am ET  → Portfolio summary (voice)")
    print("Every 15m  → Trade monitoring (run trader.py)")
    print("Every 30m  → Stop loss check")
    print("Every 15m  → Day Guardian (run day_guardian.py)")
    print("11:00pm    → Nightly learning cycle")
    print("=" * 55)
    print(f"Stop loss (-{RULE3_STOP_LOSS}%): {'ON' if RULE3_ENABLED else 'OFF'}")
    print("\nPress CTRL+C to stop\n")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("\nScheduler stopped.")
