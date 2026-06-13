"""
prop_rules.py — Prop/risk rule engine for all futures modes.

Modes:
  TC   = TopStepX Trading Combine eval  ($50K, $3K target, $700 DLL soft)
  XFA  = TopStepX Express Funded        ($0 start, $2K MLL, $1,200 daily cap)
  IBKR = Personal IBKR capital          ($2K floor, $150 DLL soft, no trailing MLL)

TopStepX $50K Standard path:
  TC eval:   profit target $3,000 | MLL $2,000 | DLL $1,000 | consistency ≤50%/day
  XFA:       balance starts $0    | MLL starts -$2,000, locks at $0 after first $2K

IBKR personal ($2K own capital):
  No prop firm rules. Soft DLL $150. No trailing MLL. Daily cap $400.
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
# 'TC'   = Trading Combine eval  (chasing $3K target on TopStepX)
# 'XFA'  = Express Funded Account (funded, chasing payouts)
# 'IBKR' = Personal IBKR capital ($2K floor, soft DLL only, no trailing MLL)
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
DLL_SOFT             = 700.0    # TC/XFA DLL soft stop (below $700 → halt; hard limit is $1K)

# ── IBKR personal mode ($2K own capital) ─────────────────────────────────────
IBKR_FLOOR           = 2_000.0  # starting capital; no trailing MLL
IBKR_DLL_SOFT        = 250.0    # 12.5% of $2K capital → halt for the day
IBKR_DAILY_CAP       = 400.0    # soft daily profit cap (don't skew performance tracking)
IBKR_MAX_CONTRACTS   = 2        # conservative: personal $2K capital

# ── Session times (CT — TopStepX operates on Chicago time) ───────────────────
# DLL resets at 5 PM CT (new trading day start)
# All positions auto-liquidated at 3:10 PM CT
EOD_CLOSE_CT         = time(15, 10)   # 3:10 PM CT — TopStepX hard deadline
NO_NEW_ENTRIES_CT    = time(14, 30)   # 2:30 PM CT — our soft cutoff
DLL_RESET_CT         = time(17, 0)    # 5 PM CT — new trading day

# ── MNQ economics (sourced from strategy_core to avoid duplication) ───────────
from strategy_core import TICK_SIZE, TICK_VALUE, POINT_VALUE, COMMISSION

# ── State file — configurable so tc_trader and futures_trader use separate files
_default_state_file = str(Path(__file__).parent / 'prop_state.json')
STATE_FILE = Path(os.getenv('FUTURES_STATE_FILE', _default_state_file))


# ── State persistence ─────────────────────────────────────────────────────────

def _default_state() -> dict:
    if ACCOUNT_MODE == 'XFA':
        starting = 0.0
    elif ACCOUNT_MODE == 'IBKR':
        starting = IBKR_FLOOR
    else:  # TC
        starting = 50_000.0
    return {
        'mode':               ACCOUNT_MODE,
        'balance':            starting,
        'high_water_mark':    starting,
        'total_profit':       0.0,
        'session_pnl':        0.0,
        'session_date':       date.today().isoformat(),
        'best_day_profit':    0.0,
        'qualifying_days':    0,
        'payout_count':       0,
    }


def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            saved   = json.loads(STATE_FILE.read_text())
            merged  = _default_state()   # start with all keys present
            merged.update(saved)         # saved values override defaults
            return merged                # missing keys get default values
    except Exception:
        pass
    return _default_state()


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── MLL floor calculation ─────────────────────────────────────────────────────

def effective_floor(state: dict) -> float:
    """
    TC:   trailing floor = high_water_mark - TC_MLL_AMOUNT (locks at $48K)
    XFA:  floor starts at -$2,000, locks at $0 after first $2K profit
    IBKR: fixed floor = $0 (no trailing MLL, just don't lose all $2K starting capital)
    """
    mode = state.get('mode', ACCOUNT_MODE)
    if mode == 'TC':
        hwm   = state['high_water_mark']
        floor = hwm - TC_MLL_AMOUNT
        return max(floor, 50_000.0 - TC_MLL_AMOUNT)
    elif mode == 'IBKR':
        return 0.0   # no trailing MLL — just don't go to zero
    else:  # XFA
        balance = state['balance']
        if balance >= 2_000.0:
            return XFA_MLL_LOCK_AT
        return XFA_INITIAL_MLL


def check_can_trade(unrealized_pnl: float = 0.0) -> tuple[bool, str]:
    """
    Called before every order.
    Returns (allowed: bool, reason: str).
    """
    state = load_state()
    mode  = state.get('mode', ACCOUNT_MODE)

    balance       = state['balance']
    total_profit  = state['total_profit']
    # Ignore stale session_pnl from a prior day (service runs overnight continuously)
    session_pnl   = state['session_pnl'] if state.get('session_date', '') == date.today().isoformat() else 0.0
    floor         = effective_floor(state)

    # ── MLL soft stop ─────────────────────────────────────
    running_balance = balance + unrealized_pnl
    if running_balance < floor + SOFT_STOP_BUFFER:
        return False, f'Approaching MLL floor (balance ${running_balance:.0f} < floor ${floor:.0f} + buffer ${SOFT_STOP_BUFFER:.0f})'

    # ── DLL soft stop ──────────────────────────────────────
    dll = IBKR_DLL_SOFT if mode == 'IBKR' else DLL_SOFT
    if session_pnl <= -dll:
        return False, f'Daily loss soft stop hit (session P&L ${session_pnl:.0f})'

    # ── Daily profit cap ───────────────────────────────────
    daily_cap = IBKR_DAILY_CAP if mode == 'IBKR' else TC_DAILY_CAP
    if mode in ('TC', 'IBKR') and session_pnl >= daily_cap:
        return False, f'{mode} daily cap reached (${session_pnl:.0f} ≥ ${daily_cap:.0f})'

    # ── TC: consistency check (ratio only meaningful once enough profit is accumulated) ──
    # The $1,200 daily cap already guarantees ≤40% per day on a $3K target.
    # Only run the ratio check when total_profit >= TC_DAILY_CAP — otherwise
    # day-1 profits (total=$173, today=$173 → 100%) falsely block all entries.
    if mode == 'TC' and total_profit >= TC_DAILY_CAP:
        if session_pnl > 0 and (session_pnl / total_profit) > TC_CONSISTENCY_MAX:
            return False, f'TC consistency: today ${session_pnl:.0f} = {session_pnl/total_profit:.0%} of total ${total_profit:.0f} — cap hit'

    return True, 'ok'


def get_max_contracts(base_contracts: int = 1) -> int:
    state = load_state()
    mode  = state.get('mode', ACCOUNT_MODE)

    if mode == 'IBKR':
        return min(base_contracts, IBKR_MAX_CONTRACTS)

    if mode == 'TC':
        return min(base_contracts, 1)

    # XFA: scale with balance
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
    # Reset session_pnl if date has rolled over since last trade
    if state.get('session_date', '') != date.today().isoformat():
        state['session_pnl']  = 0.0
        state['session_date'] = date.today().isoformat()
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
    Updates balance + total_profit from DB truth, updates TC floor, resets session.
    """
    state = load_state()
    mode  = state.get('mode', ACCOUNT_MODE)

    # Reconcile balance using DB as truth — intraday restarts cause prop_state
    # to drift from actual closed P&L. eod_pnl (from DB) is authoritative.
    pnl_delta = round(eod_pnl - state.get('session_pnl', 0.0), 2)
    state['balance']      = round(state['balance'] + pnl_delta, 2)
    state['total_profit'] = round(state['total_profit'] + pnl_delta, 2)

    if mode == 'TC':
        state['high_water_mark'] = max(state['high_water_mark'], state['balance'])
        if state['total_profit'] >= TC_PROFIT_TARGET:
            _send_telegram('🏆 *TC PASS* — profit target reached! Apply for XFA.')
    else:
        if eod_pnl >= 150.0:
            state['qualifying_days'] = state.get('qualifying_days', 0) + 1
            days = state['qualifying_days']
            if days >= 5:
                _send_telegram(f'💰 *Payout eligible* — {days} qualifying days. Request up to 50% of balance.')

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
    daily_cap = IBKR_DAILY_CAP if mode == 'IBKR' else TC_DAILY_CAP
    return {
        'mode':            mode,
        'balance':         state['balance'],
        'session_pnl':     state['session_pnl'],
        'total_profit':    state['total_profit'],
        'mll_floor':       floor,
        'buffer_to_mll':   round(state['balance'] - floor - SOFT_STOP_BUFFER, 2),
        'daily_cap_left':  round(daily_cap - state['session_pnl'], 2),
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

        if (self.mode == 'TC' and self.total_profit >= TC_DAILY_CAP
                and self.session_pnl > 0
                and self.session_pnl / self.total_profit > TC_CONSISTENCY_MAX):
            self.trades_blocked += 1
            return False, 'TC consistency'

        return True, 'ok'

    def record_trade(self, pnl: float, contracts: int = 1):
        """pnl = raw gross P&L for N contracts (no commission yet)."""
        net = round(pnl - COMMISSION * contracts, 2)   # commission scales with contracts
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
