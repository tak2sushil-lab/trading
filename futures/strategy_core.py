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
COMMISSION  : float = _inst['commission_rt']
