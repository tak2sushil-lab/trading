"""
options/engine.py — Quantitative core for the Evidence-Based Verdict System.

Provides: stock data (HV30, momentum, 200MA), Expected Move, Volatility Edge,
          Entry Gate scoring, Net Greeks, Theta Velocity, Monte Carlo EV.
All functions are pure computation — no Telegram, no DB, no side effects.
"""

import math
import numpy as np
import yfinance as yf
from scipy.stats import norm as _norm
from typing import Optional


# ── Stock data (single yfinance call) ───────────────────────────────────────

def get_stock_data(symbol: str) -> dict:
    """
    Fetch 1-year daily closes once and compute HV30, HV10, 5-day momentum,
    and 200-day MA status. Returns all None/False on failure.
    """
    try:
        hist   = yf.Ticker(symbol).history(period="1y")
        closes = hist['Close'].values
        n      = len(closes)

        hv30 = None
        if n >= 32:
            c   = closes[-(31):]
            lr  = np.log(c[1:] / c[:-1])
            hv30 = round(float(np.std(lr, ddof=1) * math.sqrt(252)) * 100, 2)

        hv10 = None
        if n >= 12:
            c   = closes[-(11):]
            lr  = np.log(c[1:] / c[:-1])
            hv10 = round(float(np.std(lr, ddof=1) * math.sqrt(252)) * 100, 2)

        momentum_5d = None
        if n >= 6:
            momentum_5d = round(float((closes[-1] - closes[-6]) / closes[-6] * 100), 2)

        above_200 = False
        if n >= 200:
            above_200 = float(closes[-1]) > float(np.mean(closes[-200:]))

        # Prior close = last completed daily bar (yfinance excludes today's intraday)
        prior_close = round(float(closes[-1]), 4) if n >= 1 else None

        return {
            'hv30':        hv30,
            'hv10':        hv10,
            'momentum_5d': momentum_5d,
            'above_200':   above_200,
            'prior_close': prior_close,
        }
    except Exception:
        return {'hv30': None, 'hv10': None, 'momentum_5d': None,
                'above_200': False, 'prior_close': None}


def compute_hv(symbol: str, window: int = 30) -> Optional[float]:
    """Standalone HV computation. Use get_stock_data() when you need multiple metrics."""
    try:
        hist   = yf.Ticker(symbol).history(period="90d")
        closes = hist['Close'].values
        if len(closes) < window + 2:
            return None
        c  = closes[-(window + 1):]
        lr = np.log(c[1:] / c[:-1])
        return round(float(np.std(lr, ddof=1) * math.sqrt(252)) * 100, 2)
    except Exception:
        return None


# ── Expected Move ────────────────────────────────────────────────────────────

def compute_expected_move(price: float, iv_pct: float, dte: int) -> float:
    """
    1-SD expected move in dollars.
    iv_pct: IV as percent (e.g. 47.0, not 0.47).
    Formula: price × (iv/100) × sqrt(dte/252) — Black-Scholes 1-SD.
    """
    return round(price * (iv_pct / 100) * math.sqrt(dte / 252), 2)


# ── Volatility Edge ──────────────────────────────────────────────────────────

def assess_volatility_edge(current_iv: float, hv30: float) -> dict:
    """
    VRP check: IV vs HV30. edge_pts > 0 = options cheap (IV < HV30).
    Gate fails only if IV > HV30 + 10% (clearly expensive).
    """
    edge_pts = round(hv30 - current_iv, 1)
    gap      = current_iv - hv30
    if gap <= 0:
        verdict, gate_pass = "CHEAP", True
    elif gap <= 5:
        verdict, gate_pass = "FAIR", True
    elif gap <= 10:
        verdict, gate_pass = "WARN", True
    else:
        verdict, gate_pass = "EXPENSIVE", False
    return {
        'edge_pts':  edge_pts,
        'verdict':   verdict,
        'gate_pass': gate_pass,
        'iv':        current_iv,
        'hv30':      hv30,
    }


# ── Entry Gate Scoring ───────────────────────────────────────────────────────

def score_entry_gates(
    vol_gate:        bool,
    tech_gate:       bool,
    conviction_gate: bool,
    liquidity_gate:  bool,
    momentum_gate:   bool,
) -> dict:
    """
    5-gate entry scoring system.
    5/5 = ENTER full size, 4/5 = ENTER_REDUCED (half), ≤3/5 = SKIP.
    """
    gates  = [vol_gate, tech_gate, conviction_gate, liquidity_gate, momentum_gate]
    n_pass = sum(gates)
    if n_pass == 5:
        verdict, size_adj = "ENTER", 1.0
    elif n_pass == 4:
        verdict, size_adj = "ENTER_REDUCED", 0.5
    else:
        verdict, size_adj = "SKIP", 0.0
    return {
        'gates_pass':    n_pass,
        'verdict':       verdict,
        'size_adj':      size_adj,
        'vol':           vol_gate,
        'tech':          tech_gate,
        'conviction':    conviction_gate,
        'liquidity':     liquidity_gate,
        'momentum':      momentum_gate,
    }


# ── Net Position Greeks ───────────────────────────────────────────────────────

def compute_net_greeks(long_q: dict, short_q: dict) -> dict:
    """
    Net Greeks for a 1-lot bull call spread (long 1 / short 1).
    theta and vega multiplied ×100 for per-contract dollar values.
    delta is per-share (not scaled).
    """
    def g(q, key):
        v = q.get(key)
        return float(v) if v is not None else None

    dl, ds = g(long_q, 'delta'), g(short_q, 'delta')
    tl, ts = g(long_q, 'theta'), g(short_q, 'theta')
    vl, vs = g(long_q, 'vega'),  g(short_q, 'vega')

    net_delta = round(dl - ds, 3) if dl is not None and ds is not None else None
    net_theta = round((tl - ts) * 100, 2) if tl is not None and ts is not None else None
    net_vega  = round((vl - vs) * 100, 2) if vl is not None and vs is not None else None

    return {'net_delta': net_delta, 'net_theta': net_theta, 'net_vega': net_vega}


# ── Theta Velocity ────────────────────────────────────────────────────────────

def compute_theta_velocity(
    breakeven:     float,
    current_price: float,
    dte:           int,
    hv30:          float,
) -> dict:
    """
    How far the stock needs to move per day to reach breakeven by expiry,
    expressed as % of price. Compared to HV30's average daily move.
    velocity_ratio < 1.0 means the stock's normal volatility achieves breakeven.
    """
    if dte <= 0 or current_price <= 0 or hv30 <= 0:
        return {'required_pct_day': None, 'hv30_daily': None, 'velocity_ratio': None, 'achievable': False}
    required_pct_day = (breakeven - current_price) / (dte * current_price) * 100
    hv30_daily_pct   = hv30 / math.sqrt(252)
    ratio = required_pct_day / hv30_daily_pct if hv30_daily_pct > 0 else None
    return {
        'required_pct_day': round(required_pct_day, 3),
        'hv30_daily':       round(hv30_daily_pct, 3),
        'velocity_ratio':   round(ratio, 2) if ratio is not None else None,
        'achievable':       ratio is not None and ratio < 1.0,
    }


# ── Monte Carlo EV ────────────────────────────────────────────────────────────

def _bs_spread_vals(S_matrix: np.ndarray, K1: float, K2: float,
                    sigma: float, T_remaining: np.ndarray) -> np.ndarray:
    """
    Vectorized Black-Scholes bull call spread value.
    S_matrix: (n_paths, dte), T_remaining: (1, dte) years remaining at each step.
    """
    spread_width = K2 - K1
    safe_T   = np.maximum(T_remaining, 1 / 252)
    sqrt_T   = np.sqrt(safe_T)

    with np.errstate(divide='ignore', invalid='ignore'):
        d1_l = (np.log(S_matrix / K1) + 0.5 * sigma**2 * safe_T) / (sigma * sqrt_T)
        d2_l = d1_l - sigma * sqrt_T
        d1_s = (np.log(S_matrix / K2) + 0.5 * sigma**2 * safe_T) / (sigma * sqrt_T)
        d2_s = d1_s - sigma * sqrt_T

    call_long  = S_matrix * _norm.cdf(d1_l) - K1 * _norm.cdf(d2_l)
    call_short = S_matrix * _norm.cdf(d1_s) - K2 * _norm.cdf(d2_s)
    return np.clip(call_long - call_short, 0.0, spread_width)


def _bs_put_spread_vals(S_matrix: np.ndarray, K_long: float, K_short: float,
                        sigma: float, T_remaining: np.ndarray) -> np.ndarray:
    """
    Vectorized Black-Scholes bear put spread value.
    K_long > K_short (long higher put, short lower put).
    S_matrix: (n_paths, dte), T_remaining: (1, dte) years.
    """
    spread_width = K_long - K_short
    safe_T  = np.maximum(T_remaining, 1 / 252)
    sqrt_T  = np.sqrt(safe_T)

    with np.errstate(divide='ignore', invalid='ignore'):
        d1_l = (np.log(S_matrix / K_long)  + 0.5 * sigma**2 * safe_T) / (sigma * sqrt_T)
        d2_l = d1_l - sigma * sqrt_T
        d1_s = (np.log(S_matrix / K_short) + 0.5 * sigma**2 * safe_T) / (sigma * sqrt_T)
        d2_s = d1_s - sigma * sqrt_T

    put_long  = K_long  * _norm.cdf(-d2_l) - S_matrix * _norm.cdf(-d1_l)
    put_short = K_short * _norm.cdf(-d2_s) - S_matrix * _norm.cdf(-d1_s)
    return np.clip(put_long - put_short, 0.0, spread_width)


def run_monte_carlo_ev(
    price:        float,
    iv_pct:       float,
    dte:          int,
    long_strike:  float,
    short_strike: float,
    net_debit:    float,
    hv30:         Optional[float] = None,
    n_paths:      int = 10_000,
    bearish:      bool = False,
) -> dict:
    """
    GBM Monte Carlo with Black-Scholes spread pricing at each step.
    Managed exits: 50% max profit, -50% debit stop, 21 DTE time stop.

    Uses two-sigma model when hv30 is provided:
      - Price paths use HV30 (realized vol) — reflects actual stock movement
      - BS pricing uses IV — reflects cheap/expensive options
    When HV30 > IV (options cheap), paths move more than priced → positive EV edge.
    """
    rng          = np.random.default_rng(42)
    # VRP model: price paths and spread valuation both use realized vol (HV30).
    # Entry cost (net_debit) stays at market price (IV-based quote).
    # → When HV30 > IV: entry < fair value → positive EV (options cheap for buyer)
    # → When HV30 < IV: entry > fair value → negative EV (options expensive)
    sigma        = (hv30 / 100) if hv30 is not None else (iv_pct / 100)
    price_sigma  = sigma
    path_sigma   = sigma
    dt           = 1 / 252
    # For call spread: short_strike > long_strike → positive width
    # For put spread:  long_strike  > short_strike → use abs()
    spread_width = abs(short_strike - long_strike)

    profit_target_val = net_debit + (spread_width - net_debit) * 0.50
    stop_level_val    = net_debit * 0.50

    # Price paths: (n_paths, dte)
    Z        = rng.standard_normal((n_paths, dte))
    log_rets = -0.5 * sigma**2 * dt + sigma * math.sqrt(dt) * Z
    cum_rets = np.cumsum(log_rets, axis=1)
    prices   = price * np.exp(cum_rets)

    # Remaining time at each day step
    days_idx = np.arange(dte)
    T_remain = ((dte - days_idx - 1) / 252).reshape(1, -1)   # (1, dte) years

    # BS spread values: use realized sigma so EV reflects VRP edge at entry
    if bearish:
        # Bear put spread: long_strike > short_strike (long higher put, short lower put)
        spread_vals = _bs_put_spread_vals(prices, long_strike, short_strike, price_sigma, T_remain)
    else:
        spread_vals = _bs_spread_vals(prices, long_strike, short_strike, price_sigma, T_remain)

    # Exit masks
    profit_mask = spread_vals >= profit_target_val

    # Stop: avoid triggering on first 2 days (spread still has time value)
    stop_mask = (spread_vals <= stop_level_val) & (days_idx[np.newaxis, :] >= 2)

    # Time stop at 21 DTE remaining
    time_stop_idx = dte - 22
    time_mask = np.zeros((n_paths, dte), dtype=bool)
    if 0 <= time_stop_idx < dte:
        time_mask[:, time_stop_idx] = True

    exit_mask = profit_mask | stop_mask | time_mask
    has_exit  = exit_mask.any(axis=1)
    exit_day  = np.argmax(exit_mask, axis=1)

    path_idx    = np.arange(n_paths)
    exit_spread = np.where(has_exit, spread_vals[path_idx, exit_day], spread_vals[:, -1])

    outcomes_dollar = (exit_spread - net_debit) * 100

    return {
        'ev_dollar': round(float(np.mean(outcomes_dollar)), 2),
        'win_rate':  round(float((outcomes_dollar > 0).mean() * 100), 1),
        'n_paths':   n_paths,
        'used_hv30': hv30 is not None,
    }
