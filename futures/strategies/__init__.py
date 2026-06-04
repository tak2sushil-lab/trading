"""
futures/strategies/ — Named, versioned strategy presets for the three-cylinder engine.

Each preset is a JSON file capturing the exact Config + run parameters that produced
a known-good backtest result. Loading a named strategy guarantees reproducibility —
the same flags, the same result, every time.

Usage:
    from futures.strategies import load_strategy, list_strategies, apply_to_config

    cfg, run_params = load_strategy('tc_champion')
    # cfg is a Config dataclass with all parameters filled
    # run_params has: contracts, es_confirm, scale_contracts, mode

Design principle:
    --strategy NAME   → loads the baseline preset
    + any CLI flags   → OVERRIDES on top of the baseline
    This lets you patch a single parameter without losing the rest of the config.
"""

import json
from pathlib import Path

STRATEGIES_DIR = Path(__file__).parent
KNOWN_STRATEGIES = ['tc_champion', 'tc_aggressive', 'xfa_conservative']


def list_strategies() -> list[dict]:
    """Return metadata for all known strategies."""
    result = []
    for name in KNOWN_STRATEGIES:
        path = STRATEGIES_DIR / f'{name}.json'
        if path.exists():
            data = json.loads(path.read_text())
            meta = data.get('_meta', {})
            perf = data.get('performance_snapshot', {})
            result.append({
                'name': name,
                'label': meta.get('label', name),
                'weather': meta.get('weather', '?'),
                'tc_pass_pct': perf.get('tc_pass_rate_pct', '?'),
                'total_pnl': perf.get('total_pnl', '?'),
                'max_dd': perf.get('max_drawdown', '?'),
                'blow_pct': perf.get('blow_rate_pct', '?'),
            })
    return result


def load_strategy(name: str) -> tuple[dict, dict]:
    """
    Load a named strategy preset.
    Returns (config_dict, run_params_dict).

    config_dict:   maps 1:1 to Config dataclass fields.
    run_params:    {contracts, es_confirm, scale_contracts, mode}.

    Caller converts config_dict to a Config() instance.
    """
    path = STRATEGIES_DIR / f'{name}.json'
    if not path.exists():
        available = ', '.join(KNOWN_STRATEGIES)
        raise ValueError(f'Unknown strategy: {name!r}. Available: {available}')

    data = json.loads(path.read_text())
    return data['config'], data['run_params']


def get_meta(name: str) -> dict:
    """Return the _meta block for a strategy (description, use_when, risk_rules)."""
    path = STRATEGIES_DIR / f'{name}.json'
    if not path.exists():
        raise ValueError(f'Unknown strategy: {name!r}')
    return json.loads(path.read_text()).get('_meta', {})


def print_strategy_summary(name: str):
    """Print a human-readable summary of a strategy."""
    path = STRATEGIES_DIR / f'{name}.json'
    if not path.exists():
        print(f'Strategy {name!r} not found.')
        return
    data = json.loads(path.read_text())
    meta = data['_meta']
    perf = data.get('performance_snapshot', {})
    risk = meta.get('risk_rules', {})

    print(f'\n{"="*60}')
    print(f'  {meta["label"]}')
    print(f'{"="*60}')
    print(f'  Version:      {meta["version"]}  (locked {meta["locked_date"]})')
    print(f'  Weather:      {meta["weather"]}')
    print(f'  Use when:     {meta["use_when"]}')
    print(f'  Avoid when:   {meta.get("do_not_use_when", "—")}')
    print()
    print(f'  Performance (5yr backtest):')
    print(f'    Trades:     {perf.get("trades", "?")}  |  WR: {perf.get("win_rate_pct", "?")}%')
    print(f'    Total P&L:  ${perf.get("total_pnl", 0):,.0f}')
    print(f'    Sharpe:     {perf.get("sharpe_annualized", "?")}')
    print(f'    MaxDD:      ${perf.get("max_drawdown", 0):,.0f}')
    print(f'    TC pass:    {perf.get("tc_pass_rate_pct", "?")}%  ({perf.get("tc_passes","?")}/{perf.get("tc_attempts","?")})')
    print(f'    Blow rate:  {perf.get("blow_rate_pct", "?")}%')
    print()
    if risk:
        print(f'  Risk rules:')
        print(f'    Stop after session loss > ${risk.get("stop_after_session_loss_usd", "?")}')
        if risk.get('note'):
            print(f'    Note: {risk["note"]}')
    print(f'\n  CLI: {data.get("cli_command", "")}')
    print()
