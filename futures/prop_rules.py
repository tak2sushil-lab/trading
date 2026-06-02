"""
prop_rules.py — TopStepX rule engine (soft safety layer)

Fires BEFORE TopStepX hard limits so we never get auto-closed.
Covers both Trading Combine (TC) eval mode and Express Funded Account (XFA).

TopStepX $50K Standard path:
  TC eval:   profit target $3,000 | MLL $2,000 | DLL $1,000 | consistency ≤50%/day
  XFA:       balance starts $0    | MLL starts -$2,000, locks at $0 after first $2K

MNQ contract: $2/point, $0.50/tick, $1.24 commission/round-turn
"""

import json
import os
from datetime import datetime, date, time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / '.env')

import requests

FUTURES_TELEGRAM_TOKEN   = os.getenv('FUTURES_TELEGRAM_TOKEN')
FUTURES_TELEGRAM_CHAT_ID = os.getenv('FUTURES_TELEGRAM_CHAT_ID')

# ── Account mode ──────────────────────────────────────────────────────────────
# 'TC'  = Trading Combine (eval, chasing $3K target)
# 'XFA' = Express Funded Account (funded, chasing payouts)
ACCOUNT_MODE = os.getenv('FUTURES_ACCOUNT_MODE', 'TC')

# ── TopStepX $50K TC constants ────────────────────────────────────────────────
TC_PROFIT_TARGET    = 3_000.0   # pass condition
TC_MLL_AMOUNT       = 2_000.0   # trailing max loss limit
TC_DLL_AMOUNT       = 1_000.0   # daily loss limit
TC_CONSISTENCY_MAX  = 0.50      # best day ≤ 50% of total profit
TC_DAILY_CAP        = 1_200.0   # our soft daily cap ($300 buffer under $1,500 ceiling)
TC_MAX_CONTRACTS    = 50        # IBKR platform limit for $50K account (micro)

# ── TopStepX XFA constants ────────────────────────────────────────────────────
XFA_STARTING_BALANCE = 0.0
XFA_INITIAL_MLL      = -2_000.0  # floor until balance reaches $2K
XFA_MLL_LOCK_AT      = 0.0       # floor locks here once balance ≥ $2K
XFA_MIN_PAYOUT       = 125.0
XFA_INACTIVITY_DAYS  = 25        # alert before 30-day closure

# ── Soft stops (always fire before TopStepX hard limits) ─────────────────────
SOFT_STOP_BUFFER     = 300.0    # stay $300 above MLL floor (slippage guard)
DLL_SOFT             = 700.0    # our DLL soft stop (below $700 → halt; hard limit is $1K)

# ── Session times (CT — TopStepX operates on Chicago time) ───────────────────
# DLL resets at 5 PM CT (new trading day start)
# All positions auto-liquidated at 3:10 PM CT
EOD_CLOSE_CT         = time(15, 10)   # 3:10 PM CT — TopStepX hard deadline
NO_NEW_ENTRIES_CT    = time(14, 30)   # 2:30 PM CT — our soft cutoff
DLL_RESET_CT         = time(17, 0)    # 5 PM CT — new trading day

# ── MNQ economics ─────────────────────────────────────────────────────────────
TICK_SIZE    = 0.25
TICK_VALUE   = 0.50
POINT_VALUE  = 2.00
COMMISSION   = 1.24   # per round-turn

STATE_FILE = Path(__file__).parent / 'prop_state.json'


# ── State persistence ─────────────────────────────────────────────────────────

def _default_state() -> dict:
    return {
        'mode':               ACCOUNT_MODE,
        'balance':            0.0 if ACCOUNT_MODE == 'XFA' else 50_000.0,
        'high_water_mark':    0.0 if ACCOUNT_MODE == 'XFA' else 50_000.0,
        'total_profit':       0.0,
        'session_pnl':        0.0,
        'session_date':       date.today().isoformat(),
        'best_day_profit':    0.0,
        'qualifying_days':    0,   # XFA payout: need 5 days ≥ $150
        'payout_count':       0,
    }


def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return _default_state()


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── MLL floor calculation ─────────────────────────────────────────────────────

def effective_floor(state: dict) -> float:
    """
    TC:  trailing floor = high_water_mark - TC_MLL_AMOUNT
         Locks at starting_balance once balance reaches starting_balance.
    XFA: floor starts at XFA_INITIAL_MLL (-$2,000).
         Locks at $0 once balance >= $2,000. Resets to $0 after each payout.
    """
    mode = state.get('mode', ACCOUNT_MODE)
    if mode == 'TC':
        hwm   = state['high_water_mark']
        floor = hwm - TC_MLL_AMOUNT
        # Lock at starting balance (50,000) once we've crossed it
        return max(floor, 50_000.0 - TC_MLL_AMOUNT)
    else:  # XFA
        balance = state['balance']
        if balance >= 2_000.0:
            return XFA_MLL_LOCK_AT   # locked at $0
        return XFA_INITIAL_MLL       # -$2,000 trailing


def check_can_trade(unrealized_pnl: float = 0.0) -> tuple[bool, str]:
    """
    Called before every order.
    Returns (allowed: bool, reason: str).
    """
    state = load_state()
    mode  = state.get('mode', ACCOUNT_MODE)

    balance       = state['balance']
    session_pnl   = state['session_pnl']
    total_profit  = state['total_profit']
    floor         = effective_floor(state)

    # ── MLL soft stop ─────────────────────────────────────
    running_balance = balance + unrealized_pnl
    if running_balance < floor + SOFT_STOP_BUFFER:
        return False, f'Approaching MLL floor (balance ${running_balance:.0f} < floor ${floor:.0f} + buffer ${SOFT_STOP_BUFFER:.0f})'

    # ── DLL soft stop ──────────────────────────────────────
    if session_pnl <= -DLL_SOFT:
        return False, f'Daily loss soft stop hit (session P&L ${session_pnl:.0f})'

    # ── TC: daily profit cap (consistency protection) ─────
    if mode == 'TC' and session_pnl >= TC_DAILY_CAP:
        return False, f'TC daily cap reached (${session_pnl:.0f} ≥ ${TC_DAILY_CAP:.0f}) — protecting consistency rule'

    # ── TC: consistency check warning ─────────────────────
    if mode == 'TC' and total_profit > 0:
        if session_pnl > 0 and (session_pnl / total_profit) > TC_CONSISTENCY_MAX:
            return False, f'TC consistency: today ${session_pnl:.0f} = {session_pnl/total_profit:.0%} of total ${total_profit:.0f} — cap hit'

    return True, 'ok'


def get_max_contracts(base_contracts: int = 1) -> int:
    """
    Returns max contracts allowed for this order.
    XFA: scales with balance level.
    TC: always 1 during eval (conservative).
    """
    state = load_state()
    mode  = state.get('mode', ACCOUNT_MODE)

    if mode == 'TC':
        return min(base_contracts, 1)   # conservative during eval

    # XFA scaling by balance
    balance = state['balance']
    if balance < 500:
        return min(base_contracts, 1)
    elif balance < 1_000:
        return min(base_contracts, 2)
    elif balance < 2_000:
        return min(base_contracts, 3)
    else:
        return min(base_contracts, 5)


def record_trade_pnl(pnl: float):
    """Call after each trade closes with realized P&L (after commission)."""
    state = load_state()
    state['session_pnl']   = round(state['session_pnl'] + pnl, 2)
    state['total_profit']  = round(state['total_profit'] + pnl, 2)
    state['balance']       = round(state['balance'] + pnl, 2)

    # TC: update high-water mark EOD only (not intraday)
    if state.get('mode', ACCOUNT_MODE) == 'XFA':
        state['high_water_mark'] = max(state['high_water_mark'], state['balance'])

    # XFA qualifying day check ($150+ net)
    if pnl > 0 and state['session_pnl'] >= 150.0:
        # Will be confirmed at EOD — flag for now
        pass

    save_state(state)
    return state


def update_eod_balance(eod_pnl: float):
    """
    Call at end of each trading day (5 PM CT reset).
    Updates TC trailing floor + XFA qualifying day count.
    """
    state = load_state()
    mode  = state.get('mode', ACCOUNT_MODE)

    if mode == 'TC':
        # TC: high-water mark trails EOD balance
        state['high_water_mark'] = max(state['high_water_mark'], state['balance'])
        # TC pass check
        if state['total_profit'] >= TC_PROFIT_TARGET:
            _send_telegram('🏆 *TC PASS* — profit target reached! Apply for XFA.')
    else:
        # XFA qualifying day
        if eod_pnl >= 150.0:
            state['qualifying_days'] = state.get('qualifying_days', 0) + 1
            days = state['qualifying_days']
            if days >= 5:
                _send_telegram(f'💰 *Payout eligible* — {days} qualifying days. Request up to 50% of balance.')

    # Track best day for consistency
    state['best_day_profit'] = max(state.get('best_day_profit', 0), eod_pnl)

    # Reset session P&L for next day
    state['session_pnl']  = 0.0
    state['session_date'] = date.today().isoformat()

    save_state(state)


def record_payout(payout_amount: float):
    """Call after a payout is approved and received."""
    state = load_state()
    state['balance']           = round(state['balance'] - payout_amount, 2)
    state['high_water_mark']   = state['balance']   # resets after payout
    state['qualifying_days']   = 0                  # 5-day count restarts
    state['payout_count']      = state.get('payout_count', 0) + 1
    save_state(state)
    _send_telegram(f'💸 Payout ${payout_amount:,.0f} recorded. Balance: ${state["balance"]:,.0f}. MLL resets to $0.')


def get_status() -> dict:
    state  = load_state()
    floor  = effective_floor(state)
    mode   = state.get('mode', ACCOUNT_MODE)
    return {
        'mode':            mode,
        'balance':         state['balance'],
        'session_pnl':     state['session_pnl'],
        'total_profit':    state['total_profit'],
        'mll_floor':       floor,
        'buffer_to_mll':   round(state['balance'] - floor - SOFT_STOP_BUFFER, 2),
        'daily_cap_left':  round(TC_DAILY_CAP - state['session_pnl'], 2) if mode == 'TC' else None,
        'qualifying_days': state.get('qualifying_days', 0),
        'payout_count':    state.get('payout_count', 0),
        'tc_target_left':  round(TC_PROFIT_TARGET - state['total_profit'], 2) if mode == 'TC' else None,
    }


# ── Telegram ──────────────────────────────────────────────────────────────────

def _send_telegram(msg: str):
    if not FUTURES_TELEGRAM_TOKEN or not FUTURES_TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{FUTURES_TELEGRAM_TOKEN}/sendMessage',
            json={'chat_id': FUTURES_TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'Markdown'},
            timeout=5,
        )
    except Exception:
        pass


# ── Backtest simulation mode ──────────────────────────────────────────────────

class PropRulesSimulator:
    """
    Stateful TC/XFA simulator for use in backtest_futures.py.
    Does NOT touch the JSON state file — purely in-memory.

    Usage:
        sim = PropRulesSimulator(mode='TC')
        for trade in trades:
            ok, reason = sim.check_can_trade()
            if not ok: continue
            sim.record_trade(pnl)
        sim.print_report()
    """

    def __init__(self, mode: str = 'TC'):
        self.mode          = mode
        self.balance       = 0.0 if mode == 'XFA' else 50_000.0
        self.hwm           = self.balance
        self.total_profit  = 0.0
        self.session_pnl   = 0.0
        self.best_day      = 0.0
        self.violations    = []
        self.trades_taken  = 0
        self.trades_blocked = 0
        self.current_date  = None

    def _floor(self) -> float:
        if self.mode == 'TC':
            return max(self.hwm - TC_MLL_AMOUNT, 50_000.0 - TC_MLL_AMOUNT)
        return XFA_MLL_LOCK_AT if self.balance >= 2_000.0 else XFA_INITIAL_MLL

    def new_day(self, trade_date):
        if self.current_date and self.current_date != trade_date:
            # EOD: update HWM for TC, track best day
            if self.mode == 'TC':
                self.hwm = max(self.hwm, self.balance)
            self.best_day   = max(self.best_day, self.session_pnl)
            self.session_pnl = 0.0
        self.current_date = trade_date

    def check_can_trade(self, unrealized: float = 0.0) -> tuple[bool, str]:
        run_bal = self.balance + unrealized

        if run_bal < self._floor() + SOFT_STOP_BUFFER:
            self.trades_blocked += 1
            return False, 'MLL soft stop'

        if self.session_pnl <= -DLL_SOFT:
            self.trades_blocked += 1
            return False, 'DLL soft stop'

        if self.mode == 'TC' and self.session_pnl >= TC_DAILY_CAP:
            self.trades_blocked += 1
            return False, 'TC daily cap'

        if (self.mode == 'TC' and self.total_profit > 0
                and self.session_pnl > 0
                and self.session_pnl / self.total_profit > TC_CONSISTENCY_MAX):
            self.trades_blocked += 1
            return False, 'TC consistency'

        return True, 'ok'

    def record_trade(self, pnl: float):
        net = round(pnl - COMMISSION, 2)   # deduct commission
        self.session_pnl  = round(self.session_pnl + net, 2)
        self.total_profit = round(self.total_profit + net, 2)
        self.balance      = round(self.balance + net, 2)
        self.trades_taken += 1
        return net

    def tc_passed(self) -> bool:
        return self.mode == 'TC' and self.total_profit >= TC_PROFIT_TARGET

    def print_report(self):
        floor = self._floor()
        print(f'\n=== PropRules Simulation ({self.mode}) ===')
        print(f'  Balance:        ${self.balance:,.2f}')
        print(f'  Total profit:   ${self.total_profit:,.2f}')
        print(f'  MLL floor:      ${floor:,.2f}')
        print(f'  Buffer to MLL:  ${self.balance - floor:,.2f}')
        print(f'  Trades taken:   {self.trades_taken}')
        print(f'  Trades blocked: {self.trades_blocked}')
        if self.mode == 'TC':
            print(f'  TC target left: ${max(0, TC_PROFIT_TARGET - self.total_profit):,.2f}')
            print(f'  TC PASSED:      {"✅ YES" if self.tc_passed() else "❌ not yet"}')
