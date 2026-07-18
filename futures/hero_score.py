"""
futures/hero_score.py — 5-hero entry quality scorer for MNQ

Heroes derived from Phase 2-3 research (Jun 16-17 2026).
Thresholds fixed from 2025-2026 IS + confirmed on 2024/2023 OOS.

OOS verdict (phase4_oos.py):
  H1 ATR_SAFE    : OOS 2025 +4.5pp, 2024 +1.1pp — CONFIRMED
  H2 MTF_ALIGNED : OOS 2025 +2.0pp, 2024 +1.1pp — CONFIRMED
  H3 RSI_MOMENTUM: OOS 2025 +2.3pp, 2024 +2.7pp — CONFIRMED
  H4 POC_BREAKOUT: OOS 2025 +7.3pp, 2024 +4.4pp — CONFIRMED
  H5 FIB_FLOOR   : OOS 2024 -6.3pp — regime-conditional keeper (Jun 17 2026)
                   IS 2025-2026: WR 45.1% vs 40.6% baseline (fib_floor>=300, N=226)
                   "different weather" — high-vol trending regimes, FIB is structural
                   Used as a CONTRACT BOOST (silver→gold) not a skip gate.
  H6 GOLDEN_SCOUT: 2024 -19.2pp, 2023 -3.3pp    — DROPPED permanently

Score → contract sizing (Phase 5 regime-aware weighted score):
  weighted < skip_th : SKIP
  skip_th ≤ weighted < gold_th : 1 contract (silver)
  weighted ≥ gold_th : 2 contracts (gold)
  + H5 boost: silver → gold when fib_floor >= 300 (structural confirmation)
"""
import numpy as np
import pandas as pd

# ── Fixed thresholds (DO NOT retune mid-run) ─────────────────────────────────
ATR_DEATH_LO  = 38.0    # medium-vol death zone lower bound
ATR_DEATH_HI  = 55.0    # medium-vol death zone upper bound
RSI_DIR_FLOOR = 50.0    # directional RSI minimum
POC_SIGNED_FL = 200.0   # signed POC breakout minimum (pts)
FIB_FLOOR_MIN = 300.0   # structural floor depth (pts) — 2025-2026 IS optimal threshold


def _compute_rsi(closes, period=14):
    if len(closes) < period + 2:
        return None
    closes = np.array(closes, dtype=float)
    deltas = np.diff(closes[-(period + 1):])
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    ag = gains.mean()
    al = losses.mean()
    if al == 0:
        return 100.0
    return round(100.0 - 100.0 / (1.0 + ag / al), 2)


def _ma_bias(bars_1h, period=20):
    """Is last 1H close above the N-bar MA? 1/0/None."""
    if bars_1h is None or len(bars_1h) < period + 1:
        return None
    closes = bars_1h['close'].values
    ma = closes[-period:].mean()
    return int(float(closes[-1]) > ma)


def _compute_poc(prior_rth):
    """Prior-day volume Point of Control."""
    if prior_rth is None or prior_rth.empty:
        return None
    if 'volume' not in prior_rth.columns:
        return None
    tick = 0.25
    vol_map = {}
    for _, row in prior_rth.iterrows():
        mid   = float((row['high'] + row['low']) / 2)
        price = round(mid / tick) * tick
        vol_map[price] = vol_map.get(price, 0) + float(row.get('volume', 0))
    return max(vol_map, key=vol_map.get) if vol_map else None


def score_entry(price: float, atr: float, side: str,
                bars_up_to_entry: pd.DataFrame,
                prior_rth: pd.DataFrame) -> tuple[int, dict]:
    """
    Score an entry candidate on 5 heroes (H1-H4 OOS-validated, H5 IS 2025-2026).

    Args:
        price           : entry price
        atr             : current ATR (pts, pre-computed)
        side            : 'LONG' or 'SHORT'
        bars_up_to_entry: all_bars sliced to [start, entry_bar] inclusive
        prior_rth       : prior day RTH bars (for POC + FIB pivots)

    Returns:
        (score, flags_dict) where score is 0-5 and flags_dict has per-hero bool.
    """
    is_long = (side == 'LONG')
    flags   = {}

    # ── H1: ATR_SAFE — not in the medium-vol death zone ──────────────────────
    flags['H1_ATR_SAFE'] = (atr < ATR_DEATH_LO or atr > ATR_DEATH_HI)

    # ── H2: MTF_ALIGNED — 1H trend agrees with direction ─────────────────────
    try:
        bars_1h = bars_up_to_entry.resample('1h').agg(
            {'open': 'first', 'high': 'max', 'low': 'min',
             'close': 'last', 'volume': 'sum'}
        ).dropna(subset=['close'])
    except Exception:
        bars_1h = pd.DataFrame()

    mtf = _ma_bias(bars_1h)
    expected_mtf = 1 if is_long else 0
    flags['H2_MTF_ALIGNED'] = (mtf is not None and mtf == expected_mtf)

    # ── H3: RSI_MOMENTUM — directional RSI ≥ 50 ──────────────────────────────
    rsi = _compute_rsi(bars_1h['close'].values, 14) if len(bars_1h) >= 16 else None
    if rsi is not None:
        rsi_dir = rsi if is_long else (100.0 - rsi)
        flags['H3_RSI_MOMENTUM'] = (rsi_dir >= RSI_DIR_FLOOR)
    else:
        flags['H3_RSI_MOMENTUM'] = False

    # ── H4: POC_BREAKOUT — committed ≥ 200pts past volume center ─────────────
    poc = _compute_poc(prior_rth)
    if poc is not None:
        poc_dist   = price - poc
        signed_poc = poc_dist if is_long else -poc_dist
        flags['H4_POC_BREAKOUT'] = (signed_poc >= POC_SIGNED_FL)
    else:
        flags['H4_POC_BREAKOUT'] = False

    # ── H5: FIB_FLOOR — structural support depth ≥ 300pts ────────────────────
    # Distance from entry to nearest blocking fib pivot in the opposite direction.
    # High value = no pivot walls nearby = room to run.
    # IS 2025-2026: threshold=300 → WR 45.1% vs 40.6% baseline (N=226).
    # Not OOS-validated (2024 failed) — used as contract boost only, not skip gate.
    try:
        from futures.feature_study import compute_fib_pivots
        from futures.fib_deep import fib_features_deep
        if prior_rth is not None and not prior_rth.empty:
            pdH = float(prior_rth['high'].max())
            pdL = float(prior_rth['low'].min())
            pdC = float(prior_rth['close'].iloc[-1])
            pivots    = compute_fib_pivots(pdH, pdL, pdC)
            fib_feats = fib_features_deep(price, pivots, side)
            flags['H5_FIB_FLOOR'] = fib_feats.get('fib_floor', 0) >= FIB_FLOOR_MIN
        else:
            flags['H5_FIB_FLOOR'] = False
    except Exception:
        flags['H5_FIB_FLOOR'] = False

    score = sum(int(v) for v in flags.values())
    return score, flags


def contracts_from_score(score: int, calc_contracts_result: int) -> int:
    """
    Map hero score to contract count.

    0-2: skip  → score 1-2 trades avg -$40/trade in 2025-2026 data (confirmed bad)
    3  : silver → 1 contract
    4  : gold   → use calc_contracts (1 or 2 based on risk)
    """
    if score <= 2:
        return 0   # signal to skip
    if score <= 3:
        return 1
    return min(2, calc_contracts_result)  # gold: respect risk cap


# ── Phase 5: Regime-aware hero weighting ─────────────────────────────────────
# Thresholds from phase5_regime.py grid search on 2025-2026 IS data (Jun 17 2026).
# IB range = 9:30–10:30 H-L. Data-driven: lo=100, hi=200.
#
# Hero dominance by regime (from data):
#   QUIET    (<100pts):  H4=+25.3pp, H1=+16.7pp. MTF=-10.1pp (!)
#   CHOPPY  (100-200):   H1=+15.6pp, H4=+4.9pp. Structure leads.
#   TRENDING (>=200pts): H2=+26.8pp, H3=+18.7pp. Momentum dominates.

QUIET_IB_THRESH    = 100.0   # IB range below this → QUIET day
TRENDING_IB_THRESH = 200.0   # IB range at/above this → TRENDING day

# Each hero's vote weight per regime (H1-H4 map to flag keys)
_REGIME_WEIGHTS: dict[str, dict[str, int]] = {
    'TRENDING': {'H1_ATR_SAFE': 1, 'H2_MTF_ALIGNED': 2, 'H3_RSI_MOMENTUM': 2, 'H4_POC_BREAKOUT': 1},
    'CHOPPY':   {'H1_ATR_SAFE': 2, 'H2_MTF_ALIGNED': 1, 'H3_RSI_MOMENTUM': 1, 'H4_POC_BREAKOUT': 2},
    'QUIET':    {'H1_ATR_SAFE': 2, 'H2_MTF_ALIGNED': 0, 'H3_RSI_MOMENTUM': 1, 'H4_POC_BREAKOUT': 2},
}
# (skip_below, gold_at_or_above) thresholds for weighted score
_REGIME_THRESHOLDS: dict[str, tuple[int, int]] = {
    'TRENDING': (4, 5),   # max 6: need both momentum heroes to fire
    'CHOPPY':   (4, 5),   # max 6: need both structure heroes to fire
    'QUIET':    (3, 4),   # max 5: H4 required + H1 or H3
}


# ── H6 candidate (Jul 8 2026 pm) — opt-in, NOT in _REGIME_WEIGHTS by default ──
# Root cause: H2_MTF_ALIGNED and H3_RSI_MOMENTUM are computed on 1H-resampled
# bars spanning ~14-20 hours (multiple trading days). On Jul 7, a genuine
# ~270pt/50min same-day rally (13:11-13:59, 5-min RSI 72-81, price 85-95pts
# above session VWAP) still read: 1H RSI=41.67 (dominated by that morning's
# earlier crash, still inside the same 14-period 1H window) and price BELOW
# the 20-period 1H MA (29644.76 vs 29534.5, anchored to price levels from
# days earlier) — both heroes read bearish during an obvious live uptrend.
# H6 is a same-day-only substitute: RSI(14) and VWAP computed strictly from
# TODAY's bars, so it can't be contaminated by yesterday or the pre-dawn
# hours. Weight/threshold NOT yet calibrated — grid-search before shipping.
H6_INTRADAY_RSI_PERIOD = 14


def _same_day_bars(bars_up_to_entry: pd.DataFrame) -> pd.DataFrame:
    if bars_up_to_entry is None or bars_up_to_entry.empty:
        return pd.DataFrame()
    today = bars_up_to_entry.index[-1].date()
    return bars_up_to_entry[bars_up_to_entry.index.date == today]


def score_h6_intraday_trend(price: float, side: str,
                            bars_up_to_entry: pd.DataFrame) -> bool:
    """
    Same-day-only substitute for H2/H3: RSI(14) computed from today's own
    5-min bars only (direction-aware) AND price on the correct side of
    today's own session VWAP (volume-weighted, today's bars only).
    """
    is_long = (side == 'LONG')
    bars_today = _same_day_bars(bars_up_to_entry)
    if len(bars_today) < H6_INTRADAY_RSI_PERIOD + 2:
        return False

    rsi = _compute_rsi(bars_today['close'].values, H6_INTRADAY_RSI_PERIOD)
    if rsi is None:
        return False
    rsi_dir = rsi if is_long else (100.0 - rsi)
    if rsi_dir < RSI_DIR_FLOOR:
        return False

    if 'volume' not in bars_today.columns or bars_today['volume'].sum() <= 0:
        return False
    vwap = (bars_today['close'] * bars_today['volume']).sum() / bars_today['volume'].sum()
    return (price > vwap) if is_long else (price < vwap)


def is_gold_score(wscore: int, regime: str) -> bool:
    """True if wscore meets or exceeds this regime's GOLD threshold (2-contract tier)."""
    _, gold_th = _REGIME_THRESHOLDS.get(regime, (4, 5))
    return wscore >= gold_th


def detect_regime(ib_range_pts: float) -> str:
    """Classify day type from IB range (9:30–10:30 H-L in MNQ points)."""
    if ib_range_pts < QUIET_IB_THRESH:
        return 'QUIET'
    if ib_range_pts >= TRENDING_IB_THRESH:
        return 'TRENDING'
    return 'CHOPPY'


def score_entry_regime(price: float, atr: float, side: str,
                       bars_up_to_entry: pd.DataFrame,
                       prior_rth: pd.DataFrame,
                       regime: str,
                       h6_weight: int = 0) -> tuple[int, dict]:
    """
    Regime-aware entry scoring. Computes raw hero flags then applies
    regime-specific weights to produce a weighted score.

    h6_weight: opt-in, 0 by default (H6 not part of the validated live score
    yet — see score_h6_intraday_trend). Pass >0 only for backtesting the
    candidate; NOT calibrated for live use.

    Returns (weighted_score, flags_dict).
    Flags dict includes per-hero bools AND 'regime' key.
    """
    _, flags = score_entry(price, atr, side, bars_up_to_entry, prior_rth)
    weights  = _REGIME_WEIGHTS.get(regime, _REGIME_WEIGHTS['CHOPPY'])
    wscore   = sum(weights[h] * int(flags.get(h, False)) for h in weights)
    if h6_weight > 0:
        flags['H6_INTRADAY_TREND'] = score_h6_intraday_trend(price, side, bars_up_to_entry)
        wscore += h6_weight * int(flags['H6_INTRADAY_TREND'])
    flags['regime'] = regime
    flags['weighted_score'] = wscore
    return wscore, flags


def contracts_from_regime_score(wscore: int, regime: str,
                                calc_contracts_result: int,
                                h5_fib: bool = False) -> int:
    """
    Map regime-weighted score to contract count.

    Skip/silver/gold thresholds differ by regime — see _REGIME_THRESHOLDS.
    H5 FIB boost: silver → gold when fib_floor >= 300 (structural confirmation).
    H5 cannot override a skip — quality gate (H1-H4) must still pass.
    """
    skip_th, gold_th = _REGIME_THRESHOLDS.get(regime, (4, 5))
    if wscore < skip_th:
        return 0
    if wscore < gold_th:
        return 1   # silver
    return min(2, calc_contracts_result)  # gold: respect risk cap
    # Note: h5_fib is tracked in flags for analysis but not used to gate contracts.
    # Current gates (IB kind + large_ib + Phase 5) already filter bad silver trades.
    # Re-evaluate H5 as a gate after 3+ months of live data accumulates.
