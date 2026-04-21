# day_guardian.py — Daily P&L Guardian (The Boat Rule)
# Monitors daily P&L every 15 min during market hours
# Docks the boat when target hit + market choppy
# Lets it ride when target hit + market strong
# Hard stops all trading when daily loss limit hit
# Runs alongside trader.py
# Command: python day_guardian.py

from dotenv import load_dotenv
load_dotenv()

import os
import json
import requests
from datetime import datetime, date
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from twilio.rest import Client

from database import get_daily_pnl, get_open_trades, get_win_rate, init_db
from regime_detector import get_regime

ET        = pytz.timezone('America/New_York')
scheduler = BlockingScheduler(timezone=ET)

TWILIO_SID      = os.getenv('TWILIO_SID')
TWILIO_TOKEN    = os.getenv('TWILIO_TOKEN')
TWILIO_WHATSAPP = os.getenv('TWILIO_WHATSAPP')
MY_WHATSAPP     = os.getenv('MY_WHATSAPP')
BRIDGE          = 'http://127.0.0.1:8000'

twilio = Client(TWILIO_SID, TWILIO_TOKEN)

# ── Guardian settings (CAD equivalent targets) ───────────
DAILY_TARGET_CAD   = 80     # Daily profit target ($80 CAD to start)
DAILY_MAX_LOSS_CAD = 50     # Max loss before sitting out ($50 CAD)
RIDE_BOOST_PCT     = 50     # Raise target by 50% when market is STRONG
MAX_TRADES_DAY     = 100    # max trades per day (paper testing — data collection)

# State file — persists across restarts
STATE_FILE = os.path.join(os.path.dirname(__file__), 'guardian_state.json')

def send_whatsapp(msg):
    try:
        twilio.messages.create(from_=TWILIO_WHATSAPP, to=MY_WHATSAPP, body=msg)
        print(f"📱 {msg[:80]}")
    except Exception as e:
        print(f"❌ WhatsApp: {e}")

def load_state():
    today = date.today().isoformat()
    default = {
        'date':          today,
        'boat_docked':   False,
        'loss_stopped':  False,
        'target_raised': False,
        'active_target': DAILY_TARGET_CAD,
        'alerts_sent':   [],
        'trade_count':   0,
    }
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                state = json.load(f)
            # Reset if new day
            if state.get('date') != today:
                state = default
                save_state(state)
            return state
    except:
        pass
    return default

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def close_all_positions():
    """Emergency close all open positions"""
    try:
        open_trades = get_open_trades()
        for trade in open_trades:
            requests.post(f"{BRIDGE}/order", json={
                'symbol':     trade['symbol'],
                'qty':        trade['shares'],
                'side':       'SELL',
                'order_type': 'MARKET'
            }, timeout=10)
            print(f"  Closed {trade['symbol']}")
    except Exception as e:
        print(f"  Close error: {e}")

def block_new_trades(reason):
    """Write a block file that screener/trader checks before placing trades"""
    block_file = os.path.join(os.path.dirname(__file__), 'trading_blocked.json')
    with open(block_file, 'w') as f:
        json.dump({
            'blocked': True,
            'reason':  reason,
            'time':    datetime.now(ET).strftime('%H:%M'),
            'date':    date.today().isoformat()
        }, f)
    print(f"  🚫 Trading BLOCKED: {reason}")

def unblock_trades():
    """Remove trade block"""
    block_file = os.path.join(os.path.dirname(__file__), 'trading_blocked.json')
    if os.path.exists(block_file):
        os.remove(block_file)

def is_trading_blocked():
    block_file = os.path.join(os.path.dirname(__file__), 'trading_blocked.json')
    if not os.path.exists(block_file):
        return False, None
    try:
        with open(block_file) as f:
            data = json.load(f)
        # Auto-reset if different day
        if data.get('date') != date.today().isoformat():
            os.remove(block_file)
            return False, None
        return data.get('blocked', False), data.get('reason')
    except:
        return False, None

@scheduler.scheduled_job('cron', day_of_week='mon-fri',
                          hour='9-15', minute='*/15')
def guardian_check():
    """Main guardian loop — runs every 15 min during market hours"""
    now = datetime.now(ET)
    if now.hour == 9 and now.minute < 31:
        return
    if now.hour >= 16:
        return

    state  = load_state()
    daily  = get_daily_pnl()
    pnl    = daily['pnl']
    trades = daily['trades']

    print(f"\n[Guardian {now.strftime('%H:%M')}] P&L: ${pnl:+.2f} | "
          f"Trades: {trades} | Target: ${state['active_target']} | "
          f"Blocked: {state['boat_docked'] or state['loss_stopped']}")

    # ── Already blocked — just monitor ───────────────────
    if state['boat_docked'] or state['loss_stopped']:
        return

    # ── Get market regime ─────────────────────────────────
    try:
        regime = get_regime()
        regime_name = regime['regime']
    except:
        regime_name = 'NORMAL'

    alert_key = f"{now.strftime('%H:%M')}_{pnl:.0f}"

    # ── MAX TRADES LIMIT ──────────────────────────────────
    if trades >= MAX_TRADES_DAY and f'max_trades' not in state['alerts_sent']:
        block_new_trades(f'Max {MAX_TRADES_DAY} trades reached for today')
        state['boat_docked'] = True
        state['alerts_sent'].append('max_trades')
        save_state(state)
        send_whatsapp(
            f"🚢 Max trades reached!\n"
            f"Trades today: {trades}/{MAX_TRADES_DAY}\n"
            f"P&L: ${pnl:+.2f}\n"
            f"No more trades today. Monitoring open positions."
        )
        return

    # ── LOSS LIMIT HIT ────────────────────────────────────
    if pnl <= -DAILY_MAX_LOSS_CAD and 'loss_limit' not in state['alerts_sent']:
        state['loss_stopped'] = True
        state['alerts_sent'].append('loss_limit')
        save_state(state)
        block_new_trades(f'Daily loss limit -${DAILY_MAX_LOSS_CAD} hit')
        send_whatsapp(
            f"🛑 LOSS LIMIT HIT\n"
            f"Daily P&L: ${pnl:+.2f}\n"
            f"Loss limit: -${DAILY_MAX_LOSS_CAD}\n"
            f"All trading stopped for today.\n"
            f"Open positions still monitored for exit."
        )
        return

    # ── TARGET HIT — decide: dock or ride? ───────────────
    if pnl >= state['active_target'] and f'target_{state["active_target"]}' not in state['alerts_sent']:

        if regime_name == 'STRONG' and not state['target_raised']:
            # Market is strong — raise target and ride!
            new_target = round(state['active_target'] * (1 + RIDE_BOOST_PCT / 100))
            state['active_target'] = new_target
            state['target_raised'] = True
            state['alerts_sent'].append(f'target_{pnl:.0f}')
            save_state(state)
            send_whatsapp(
                f"🌊 Target hit + Market STRONG — Riding the wave!\n"
                f"P&L: ${pnl:+.2f} ✅\n"
                f"Market: {regime_name} | SPY {regime.get('spy_change', 0):+.1f}%\n"
                f"Raising target to ${new_target}\n"
                f"Trailing stops protecting profits."
            )

        elif regime_name in ['CHOPPY', 'WEAK', 'CAUTIOUS']:
            # Dock the boat
            state['boat_docked'] = True
            state['alerts_sent'].append(f'target_{pnl:.0f}')
            save_state(state)
            block_new_trades(f'Daily target ${state["active_target"]} hit + market {regime_name}')
            send_whatsapp(
                f"🚢 Boat docked — Target reached!\n"
                f"P&L: ${pnl:+.2f} ✅\n"
                f"Market: {regime_name} — Not worth the risk\n"
                f"No new trades today.\n"
                f"Open positions still managed to exit."
            )

        else:
            # NORMAL market — dock boat but let open trades run
            state['boat_docked'] = True
            state['alerts_sent'].append(f'target_{pnl:.0f}')
            save_state(state)
            block_new_trades(f'Daily target ${state["active_target"]} reached')
            send_whatsapp(
                f"✅ Daily target reached!\n"
                f"P&L: ${pnl:+.2f}\n"
                f"Market: {regime_name}\n"
                f"No new trades. Locking in gains.\n"
                f"Reply STATUS to see open positions."
            )
        return

    # ── CHOPPY market with no P&L — sit out ──────────────
    if regime_name == 'CHOPPY' and abs(pnl) < 10 and 'choppy_pause' not in state['alerts_sent']:
        if trades == 0:  # Only pause if no trades yet
            state['alerts_sent'].append('choppy_pause')
            save_state(state)
            block_new_trades('Choppy market — no clear direction')
            send_whatsapp(
                f"🟠 Choppy market detected\n"
                f"SPY flat, VIX unstable\n"
                f"Pausing new entries today.\n"
                f"Checking again tomorrow.\n"
                f"Reply RESUME to override."
            )
        return

    # ── Progress update at 50% of target ──────────────────
    half_target = state['active_target'] * 0.5
    if pnl >= half_target and f'halfway' not in state['alerts_sent'] and trades > 0:
        state['alerts_sent'].append('halfway')
        save_state(state)
        send_whatsapp(
            f"📊 Halfway to target!\n"
            f"P&L: ${pnl:+.2f} / target ${state['active_target']}\n"
            f"Trades: {trades} | Market: {regime_name}\n"
            f"Keep going! 🎯"
        )


@scheduler.scheduled_job('cron', day_of_week='mon-fri', hour=9, minute=30)
def morning_reset():
    """Reset guardian state at market open"""
    unblock_trades()
    state = {
        'date':          date.today().isoformat(),
        'boat_docked':   False,
        'loss_stopped':  False,
        'target_raised': False,
        'active_target': DAILY_TARGET_CAD,
        'alerts_sent':   [],
        'trade_count':   0,
    }
    save_state(state)
    print(f"[Guardian] Reset for new day. Target: ${DAILY_TARGET_CAD}")

    regime = get_regime()
    rn     = regime['regime']
    icons  = {'STRONG': '🟢', 'NORMAL': '🔵', 'CAUTIOUS': '🟡',
              'CHOPPY': '🟠', 'WEAK': '🔴'}
    icon   = icons.get(rn, '⚪')

    send_whatsapp(
        f"☀️ Good morning! Guardian active.\n"
        f"{icon} Market regime: {rn}\n"
        f"SPY: {regime['spy_change']:+.1f}% | VIX: {regime['vix']:.1f}\n"
        f"Daily target: ${DAILY_TARGET_CAD}\n"
        f"Loss limit: -${DAILY_MAX_LOSS_CAD}\n"
        f"Max trades: {MAX_TRADES_DAY}\n"
        f"Bias: {regime['trade_bias']}\n"
        f"\n{regime['description']}"
    )


if __name__ == '__main__':
    init_db()
    print("\n🛡️  Day Guardian Started")
    print("=" * 50)
    print(f"Daily target:   ${DAILY_TARGET_CAD}")
    print(f"Loss limit:     -${DAILY_MAX_LOSS_CAD}")
    print(f"Max trades/day: {MAX_TRADES_DAY}")
    print(f"Ride boost:     +{RIDE_BOOST_PCT}% when STRONG")
    print("=" * 50)
    print("Checking every 15 min during market hours")
    print("Press CTRL+C to stop\n")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("\nGuardian stopped.")
