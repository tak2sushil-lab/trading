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


# Registry — the factory floor. Add a class here and it flows through the whole pipeline.
REGISTRY = {
    "momentum_wild": MomentumWild,
    "meanrev_mid": MeanRevMid,
}


def get_engine(name: str) -> Engine:
    if name not in REGISTRY:
        raise KeyError(f"unknown engine '{name}'. Known: {list(REGISTRY)}")
    return REGISTRY[name]()


def all_engines():
    return [cls() for cls in REGISTRY.values()]
