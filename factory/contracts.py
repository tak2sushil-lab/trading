"""
Factory contracts — the interfaces every component talks through.

The whole point of the framework: components communicate ONLY through these small,
well-defined shapes, so each (engine, proving ground, captain, fill desk) can be built,
tested, and swapped independently. Add an engine → it just implements Engine. Nothing
downstream changes.

Analogy map (see GLOSSARY.md §8):
  Engine      = a boat (one strategy that emits trade tickets)
  Trade       = one voyage (a single position with its realised outcome)
  Tide        = beta  (the market's drift that lifts every boat)
  Sailing     = alpha (skill above the tide = Trade.alpha)
  Personality = a stock's volatility class (CALM / MID / WILD)
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from typing import Optional
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class EngineSpec:
    """Static description of an engine — its identity + fixed parameters."""
    name: str          # code name, e.g. "momentum_wild"
    nickname: str      # analogy name, e.g. "Wave Rider"
    hypothesis: str    # one sentence: why this edge should exist
    side: str          # "LONG" or "SHORT"
    hold_days: int     # trading days held
    stop_pct: float    # hard stop, positive number of %, e.g. 8.0
    personality: str   # which volatility class it fishes: CALM / MID / WILD / ANY
    direction: str     # which event pool: "UP" (movers up) or "DOWN" (movers down)
    sleeve: bool = False  # True = market-neutral long-short basket (a capital sleeve, not
                          # discrete slots). The Fill Desk treats sleeves as a return stream.

    def __str__(self) -> str:
        return (f"{self.nickname} [{self.name}] — {self.side} {self.direction}-movers, "
                f"{self.personality} stocks, hold {self.hold_days}d, stop {self.stop_pct:.0f}%")


# Column contract for a Trade table (one row per position). Engines return a DataFrame
# with at least these columns; the proving ground and fill desk rely on them.
TRADE_COLUMNS = [
    "engine",     # engine name
    "date",       # entry date (pd.Timestamp)
    "symbol",
    "side",       # LONG / SHORT
    "cluster",    # CALM / MID / WILD
    "day_chg",    # the move at entry (%). An ENTRY-TIME feature — safe to prioritise on.
    "hold_days",
    "ret",        # realised return %, net of the stop, over the hold (the raw voyage)
    "tide",       # market's same-horizon return % that day (the beta lift)
    "alpha",      # ret - tide  (the sailing — pure skill)
    "stopped",    # 1 if the stop was hit
]


def empty_trades() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADE_COLUMNS)


class Engine:
    """Base class for a strategy. A subclass defines ONLY `select()` — which events it
    trades — and the base handles the mechanical outcome + market-neutral alpha, so every
    engine is scored on exactly the same honest basis."""

    spec: EngineSpec

    def __init__(self, spec: EngineSpec):
        self.spec = spec

    # --- the one thing a subclass must implement -----------------------------
    def select(self, events: pd.DataFrame) -> pd.DataFrame:
        """Return the subset of `events` this engine trades. `events` is the unified
        event table from data.build_dataset (one row per >=3% mover with forward paths)."""
        raise NotImplementedError

    def neighbors(self) -> list["Engine"]:
        """Param-variant engines for the robustness check (a plateau, not a spike).
        Base = hold/stop neighbours borrowing the same selection. A different engine TYPE
        (e.g. cross-sectional) overrides this to vary ITS own knobs."""
        out = []
        for dh in (-1, 0, 1):
            for ds in (-2, 0, 2):
                h, s = self.spec.hold_days + dh, self.spec.stop_pct + ds
                if h < 1 or h > 15 or s <= 0:
                    continue
                e = Engine(replace(self.spec, hold_days=h, stop_pct=s))
                e.select = self.select   # borrow this engine's selection
                out.append(e)
        return out

    # --- shared, identical for every engine (this is what keeps scoring honest) ---
    def run(self, events: pd.DataFrame, tide: pd.DataFrame) -> pd.DataFrame:
        """Turn selected events into realised Trades with market-neutral alpha."""
        sel = self.select(events)
        if len(sel) == 0:
            return empty_trades()
        hold, stop, side = self.spec.hold_days, self.spec.stop_pct, self.spec.side
        # long_ret = the stock's realised return if you BOUGHT it (net of the stop)
        long_ret = sel.apply(lambda r: _outcome(r, hold, stop, side), axis=1)
        tide_map = tide.set_index("date")[f"tide{hold}"]
        tide_vals = sel["date"].map(tide_map)
        # Tradable return and market-neutral alpha, stated once, cleanly:
        #   LONG : you earn long_ret;  alpha = long_ret - tide
        #   SHORT: you earn -long_ret; alpha = tide - long_ret  (= -(long_ret - tide))
        if side == "SHORT":
            tradable = -long_ret
            alpha = tide_vals - long_ret
        else:
            tradable = long_ret
            alpha = long_ret - tide_vals
        out = pd.DataFrame({
            "engine": self.spec.name,
            "date": sel["date"].values,
            "symbol": sel["symbol"].values,
            "side": side,
            "cluster": sel["cluster"].values,
            "day_chg": sel["day_chg_10"].values,
            "hold_days": hold,
            "ret": tradable.values,
            "tide": tide_vals.values,
            "alpha": alpha.values,
            "stopped": sel.apply(lambda r: _stopped(r, hold, stop, side), axis=1).values,
        })
        return out.dropna(subset=["ret", "tide", "alpha"])[TRADE_COLUMNS]


def _outcome(row, hold: int, stop_pct: float, side: str = "LONG") -> float:
    """Realised return over `hold` days with a hard stop at `stop_pct` against the position.

    Returned in LONG terms (the caller negates for a SHORT), so the stop must be detected on
    the side that HURTS the position:
      LONG  is hurt by the day's LOW  -> stop when lo{k} <= -stop_pct, exit at -stop_pct
      SHORT is hurt by the day's HIGH -> stop when hi{k} >= +stop_pct, exit at +stop_pct
                                        (= -stop_pct once the caller negates)

    Bug fixed Sep 3 2026: this used the LOW for both sides, so a SHORT was "stopped" on its
    PROFIT side — capped wins, uncapped losses — and every short engine the Proving Ground
    ever scored was mis-modelled. Falls back to the unstopped close if the needed column is
    missing (an old cache with no hi{k}), so results degrade visibly rather than silently.
    """
    key = "hi" if side == "SHORT" else "lo"
    for k in range(1, hold + 1):
        v = row.get(f"{key}{k}", np.nan)
        if pd.isna(v):
            return row.get(f"cl{hold}", np.nan)
        if side == "SHORT":
            if v >= stop_pct:
                return stop_pct
        elif v <= -stop_pct:
            return -stop_pct
    return row.get(f"cl{hold}", np.nan)


def _stopped(row, hold: int, stop_pct: float, side: str = "LONG") -> int:
    key = "hi" if side == "SHORT" else "lo"
    for k in range(1, hold + 1):
        v = row.get(f"{key}{k}", np.nan)
        if pd.isna(v):
            return 0
        if (v >= stop_pct) if side == "SHORT" else (v <= -stop_pct):
            return 1
    return 0
