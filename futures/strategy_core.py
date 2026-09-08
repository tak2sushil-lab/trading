"""
strategy_core.py — Instrument + strategy loader for TriVega Futures.

Eliminates hardcoded MNQ constants across tc_trader.py, futures_trader.py,
and prop_rules.py. Adding a new instrument (e.g. MES) = create instruments/MES.json,
zero code changes here.

Usage:
    from strategy_core import SYMBOL, POINT_VALUE, TICK_SIZE, TICK_VALUE, COMMISSION
    from strategy_core import load_strategy

Env vars:
    FUTURES_INSTRUMENT  — defaults to 'MNQ'
    FUTURES_STRATEGY    — defaults to 'tc/standard'
"""

import json
import os
from pathlib import Path

_DIR = Path(__file__).parent


def load_instrument(symbol: str | None = None) -> dict:
    symbol = symbol or os.getenv('FUTURES_INSTRUMENT', 'MNQ')
    path = _DIR / 'instruments' / f'{symbol}.json'
    if not path.exists():
        raise FileNotFoundError(f'Instrument spec not found: {path}')
    with open(path) as f:
        return json.load(f)


def load_strategy(path: str) -> dict:
    """Load strategy JSON by path relative to strategies/ (e.g. 'tc/standard')."""
    full = _DIR / 'strategies' / f'{path}.json'
    if not full.exists():
        raise FileNotFoundError(f'Strategy not found: {full}')
    with open(full) as f:
        return json.load(f)


# ── Module-level constants — loaded once at import ────────────────────────────
_inst = load_instrument()

SYMBOL      : str   = _inst['symbol']
EXCHANGE    : str   = _inst['exchange']
POINT_VALUE : float = _inst['point_value']
TICK_SIZE   : float = _inst['tick_size']
TICK_VALUE  : float = _inst['tick_value']
# Commission is ACCOUNT-AWARE (Sep 7 2026). IBKR and TopStep do not charge the same round
# turn — IBKR is broker commission + CME exchange + regulatory, TopStep bundles its own fee
# schedule — and TC is heading for a funded subscription where this number feeds the DLL/MLL
# gates, not just reporting. One shared constant would quietly mis-state one of the two.
#   ⚠️ commission_rt_tc is currently set EQUAL to the IBKR rate as a placeholder, so today's
#   behaviour is unchanged. It is UNVERIFIED — confirm it against TopStep's published fee
#   schedule before the subscription starts, and update MNQ.json only (no code change).
_MODE = os.getenv('FUTURES_ACCOUNT_MODE', 'TC')
COMMISSION  : float = float(
    _inst.get('commission_rt_tc', _inst['commission_rt']) if _MODE == 'TC'
    else _inst['commission_rt'])
