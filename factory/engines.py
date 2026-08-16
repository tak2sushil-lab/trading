"""
Engines — the boats. Each is a strategy that fishes a specific pool of events.
A new engine implements one method (`select`) and registers itself. Everything else
(outcome, market-neutral alpha, scoring) is handled identically by the base class, so no
engine can flatter itself with a special exit or a hand-picked benchmark.

Registered engines:
  Wave Rider    (momentum_wild) — buy WILD stocks that popped up; ride the multi-day drift.
  Bargain Hunter (meanrev_mid)  — buy MID stocks that fell; bet on the multi-day bounce.
"""
from __future__ import annotations
import pandas as pd
from factory.contracts import Engine, EngineSpec


class MomentumWild(Engine):
    """Wave Rider. Hypothesis: a big up-move on a high-energy (WILD) stock underreacts —
    the crowd keeps arriving for days. Proven +0.5-0.9% market-neutral alpha, all regimes."""
    def __init__(self):
        super().__init__(EngineSpec(
            name="momentum_wild", nickname="Wave Rider",
            hypothesis="Big up-moves on high-volatility stocks keep drifting up for days (underreaction).",
            side="LONG", hold_days=3, stop_pct=8.0, personality="WILD", direction="UP",
        ))

    def select(self, events: pd.DataFrame) -> pd.DataFrame:
        return events[(events["direction"] == "UP") & (events["cluster"] == "WILD")]


class MeanRevMid(Engine):
    """Bargain Hunter. Hypothesis: a sharp drop on a moderate-volatility (MID) stock
    overshoots and partially bounces over the following days. Modest but uncorrelated to
    Wave Rider — a shock absorber, not a star."""
    def __init__(self):
        super().__init__(EngineSpec(
            name="meanrev_mid", nickname="Bargain Hunter",
            hypothesis="Sharp drops on moderate-volatility stocks overshoot and bounce over days.",
            side="LONG", hold_days=3, stop_pct=8.0, personality="MID", direction="DOWN",
        ))

    def select(self, events: pd.DataFrame) -> pd.DataFrame:
        return events[(events["direction"] == "DOWN") & (events["cluster"] == "MID")]


class PEADGap(Engine):
    """Earnings Drift (gap-proxy). Hypothesis: a stock that gaps up on an information event
    (earnings/news) keeps drifting up for weeks — post-earnings-announcement drift.
    NOTE: earnings_calendar is empty, so this proxies the event with a big overnight GAP, not a
    true earnings surprise. Tested Aug 15 2026: strong in-sample, NEGATIVE both OOS windows — the
    drift reversed after 2024. Kept as a documented rejected candidate."""
    def __init__(self):
        super().__init__(EngineSpec(
            name="pead_gap", nickname="Earnings Drift",
            hypothesis="Stocks that gap up on an info event (earnings/news) drift up for weeks.",
            side="LONG", hold_days=10, stop_pct=10.0, personality="ANY", direction="UP",
        ))

    def select(self, events: pd.DataFrame) -> pd.DataFrame:
        return events[(events["direction"] == "UP") & (events["gap"] >= 5.0)]


from factory.xsec import XSectionalReversal, XSectionalLowVol   # noqa: E402

# Registry — the factory floor. Add a class here and it flows through the whole pipeline.
REGISTRY = {
    "momentum_wild": MomentumWild,      # Wave Rider    — slot-based, Roster
    "meanrev_mid": MeanRevMid,          # Bargain Hunter — slot-based, failed
    "xsec_reversal": XSectionalReversal, # Contrarian    — market-neutral sleeve, engine #2 (Roster)
    "xsec_lowvol": XSectionalLowVol,     # Steady Hand   — market-neutral sleeve, failed
    "pead_gap": PEADGap,                 # Earnings Drift — event/position-length, tested Aug 15
}


def get_engine(name: str) -> Engine:
    if name not in REGISTRY:
        raise KeyError(f"unknown engine '{name}'. Known: {list(REGISTRY)}")
    return REGISTRY[name]()


def all_engines():
    return [cls() for cls in REGISTRY.values()]
