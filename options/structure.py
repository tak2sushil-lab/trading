"""
options/structure.py — strike selection and the Edge Budget admission gate.

Sep 20 2026. Pure computation: no Telegram, no DB, no network. Same contract as
engine.py so it is unit-testable and safe to import anywhere.

WHY THIS EXISTS
---------------
Every debit spread this book has ever traded was anchored at 0.33/0.67 of the
expected move, which put the long leg 8-12% OTM and made the *breakeven* a
median +8.1% move. Measured on 194k symbol-days of our own universe, only 15.6%
of 7-day windows and 7.7% of 3-day windows clear +8.1%. The book lost on 17 of
22 closed trades — almost exactly the rate the structure dictated. It was never
a signal problem; it was arithmetic.

Two corrections live here:

1. DELTA-ANCHORED STRIKES. Strikes are chosen by option delta, not by a multiple
   of the expected move. A 0.70-delta long leg sits near or inside the money, so
   the breakeven is ~1-2% instead of ~8%, and the structure stops needing a
   1-in-7 event just to return the premium.

2. THE EDGE BUDGET. A structure may only be traded when the signal's own
   expected move over its intended holding period clears the structure's
   breakeven with margin. This is the permanent admission test: options convert
   edge into leverage, they never create it, so a structure whose breakeven the
   signal cannot reach is a losing trade no matter how good the signal is.

Structure evidence (research_options_structure_lab.py, 2024-2026, detrended so
the bull tide is removed, friction from our own options_chain_snapshots):

    structure              BE move   detrended exp. return (21d hold)
    debit spr .40/.20       +5.94%      -20.32%   <- what we traded
    debit spr .60/.35       +2.03%      -10.22%
    debit spr .75/.50       -0.55%       -5.89%
    bull put cr .30/.15     -3.56%       -2.22%   <- least bad

No structure is positive once the market's drift is removed, which is why the
Edge Budget gate is a gate and not a suggestion.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

try:
    from scipy.stats import norm as _norm
    _N = _norm.cdf
except Exception:                                    # scipy always present, but never fail closed
    def _N(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

RISK_FREE = 0.04
DAYS_PER_YEAR = 365.0        # expiries are CALENDAR days — see expected_move()


# ── Black-Scholes ────────────────────────────────────────────────────────────

def _d1(S: float, K: float, T: float, sig: float, r: float = RISK_FREE) -> float:
    return (math.log(S / K) + (r + sig * sig / 2) * T) / (sig * math.sqrt(T))


def bs_call(S: float, K: float, T: float, sig: float, r: float = RISK_FREE) -> float:
    if T <= 0 or sig <= 0:
        return max(S - K, 0.0)
    d1 = _d1(S, K, T, sig, r)
    return S * _N(d1) - K * math.exp(-r * T) * _N(d1 - sig * math.sqrt(T))


def bs_put(S: float, K: float, T: float, sig: float, r: float = RISK_FREE) -> float:
    if T <= 0 or sig <= 0:
        return max(K - S, 0.0)
    d1 = _d1(S, K, T, sig, r)
    return K * math.exp(-r * T) * _N(-(d1 - sig * math.sqrt(T))) - S * _N(-d1)


def call_delta(S: float, K: float, T: float, sig: float, r: float = RISK_FREE) -> float:
    if T <= 0 or sig <= 0:
        return 1.0 if S > K else 0.0
    return _N(_d1(S, K, T, sig, r))


def put_delta(S: float, K: float, T: float, sig: float, r: float = RISK_FREE) -> float:
    """Returned as a negative number, the way a broker reports it."""
    return call_delta(S, K, T, sig, r) - 1.0


# ── Expected move ────────────────────────────────────────────────────────────

def expected_move(price: float, iv_pct: float, dte_calendar: int) -> float:
    """
    1-SD expected move in dollars over `dte_calendar` CALENDAR days.

    ⚠️ The bug this replaces: engine.compute_expected_move used
    sqrt(dte/252) while days_to_expiry() returns calendar days. Mixing a
    calendar numerator with a trading-day denominator overstates every expected
    move by sqrt(365/252) = 1.204x, which pushed every strike ~20% further OTM
    than the template intended, on all four calculators, since they were written.
    Calendar days belong over 365.
    """
    if not price or not iv_pct or dte_calendar is None or dte_calendar <= 0:
        return 0.0
    return round(price * (iv_pct / 100.0) * math.sqrt(dte_calendar / DAYS_PER_YEAR), 2)


# ── Delta-anchored strike selection ──────────────────────────────────────────

def strike_for_call_delta(S: float, T: float, sig: float, target_delta: float) -> float:
    """Strike whose call delta equals target_delta. Monotone, so bisection is exact."""
    lo, hi = S * 0.20, S * 4.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if call_delta(S, mid, T, sig) > target_delta:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def strike_for_put_delta(S: float, T: float, sig: float, target_delta: float) -> float:
    """`target_delta` is the magnitude, e.g. 0.30 for a -0.30-delta put."""
    lo, hi = S * 0.20, S * 4.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if abs(put_delta(S, mid, T, sig)) < target_delta:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def nearest_strike(strikes: Sequence[float], target: float) -> Optional[float]:
    valid = [float(s) for s in (strikes or []) if s]
    return min(valid, key=lambda s: abs(s - target)) if valid else None


def pick_delta_strikes(strikes: Sequence[float], underlying: float, iv_pct: float,
                       dte: int, long_delta: float, short_delta: float,
                       right: str = 'C') -> tuple[Optional[float], Optional[float]]:
    """
    Choose (long, short) strikes by delta from the real listed strike ladder.

    Calls  → long is the LOWER strike (higher delta), short the HIGHER strike.
    Puts   → long is the HIGHER strike (higher |delta|), short the LOWER strike.

    Returns (None, None) when the ladder cannot express the structure (the two
    targets collapse onto the same listed strike, which happens on coarse
    ladders for low-priced names).
    """
    if not underlying or not iv_pct or not dte or dte <= 0:
        return None, None
    T   = dte / DAYS_PER_YEAR
    sig = iv_pct / 100.0
    if right.upper().startswith('P'):
        t_long  = strike_for_put_delta(underlying, T, sig, long_delta)
        t_short = strike_for_put_delta(underlying, T, sig, short_delta)
    else:
        t_long  = strike_for_call_delta(underlying, T, sig, long_delta)
        t_short = strike_for_call_delta(underlying, T, sig, short_delta)

    k_long  = nearest_strike(strikes, t_long)
    k_short = nearest_strike(strikes, t_short)
    if k_long is None or k_short is None or k_long == k_short:
        return None, None
    # enforce the correct ordering for the right
    if right.upper().startswith('P'):
        if k_long <= k_short:
            return None, None
    else:
        if k_long >= k_short:
            return None, None
    return k_long, k_short


# ── Breakeven and the Edge Budget ────────────────────────────────────────────

def breakeven_move_pct(underlying: float, long_strike: float, short_strike: Optional[float],
                       net_debit: float, right: str = 'C') -> Optional[float]:
    """
    Underlying move, in percent, needed at EXPIRY for the structure to return the
    premium paid. Signed in the direction of the trade: a bull structure needing
    the stock 2% higher returns +2.0; a bear put spread needing it 2% lower also
    returns +2.0 (i.e. "2% in our favour"), so one number is comparable across
    both books.
    """
    if not underlying or not long_strike or net_debit is None:
        return None
    if right.upper().startswith('P'):
        be = long_strike - net_debit
        return round((underlying - be) / underlying * 100.0, 2)
    be = long_strike + net_debit
    return round((be - underlying) / underlying * 100.0, 2)


def edge_budget(breakeven_pct: Optional[float], signal_move_pct: Optional[float],
                margin: float = 1.25) -> dict:
    """
    THE ADMISSION TEST.

    `signal_move_pct` is the move the SIGNAL is expected to deliver, in our
    favour, over the holding period we actually intend to hold for — not the
    move the stock could theoretically make by expiry. It must clear the
    structure's breakeven by `margin`.

    Rationale: options convert edge into leverage; they do not create it. If the
    signal's own expected move cannot reach the structure's breakeven, the trade
    loses in expectation regardless of how good the signal is. This is the gate
    that the EM-anchored template never had — which is why it bought structures
    needing +8.1% from signals worth ~+1%.

    ⚠️ KNOWN LIMITATION, stated so nobody mistakes this gate for a full test.
    It asks only "can the signal reach this structure's breakeven?" It does NOT
    ask "does this structure beat simply holding the shares?" A structure can
    clear its breakeven and still be the worse trade, because leverage costs
    extrinsic and spread that shares do not pay. On our own universe, detrended,
    every structure loses to shares — so a PASS here is a necessary condition,
    never a sufficient one. The leverage-premium comparison that answers the
    second question already exists in factory/live/turbo.py; wire it in before
    treating a PASS as a reason to trade.

    Returns {'ok', 'reason', 'breakeven_pct', 'signal_move_pct', 'required_pct'}.
    """
    if breakeven_pct is None or signal_move_pct is None:
        return {'ok': False, 'reason': 'edge budget: breakeven or signal move unknown',
                'breakeven_pct': breakeven_pct, 'signal_move_pct': signal_move_pct,
                'required_pct': None}
    # Buffer is always applied in the CONSERVATIVE direction. A plain
    # `breakeven * margin` would loosen the gate whenever the breakeven is
    # negative (a theta-positive structure), which is exactly backwards.
    required = round(breakeven_pct + abs(breakeven_pct) * (margin - 1.0), 2)
    ok = signal_move_pct >= required
    if ok:
        reason = (f"edge budget OK: signal {signal_move_pct:.2f}% ≥ {required:.2f}% "
                  f"(breakeven {breakeven_pct:.2f}% + {margin - 1:.0%} buffer)")
    else:
        reason = (f"edge budget: signal move {signal_move_pct:.2f}% < {required:.2f}% needed "
                  f"(breakeven {breakeven_pct:.2f}% + {margin - 1:.0%} buffer)")
    return {'ok': ok, 'reason': reason, 'breakeven_pct': breakeven_pct,
            'signal_move_pct': signal_move_pct, 'required_pct': required}
