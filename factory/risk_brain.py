"""
The Captain (Risk Brain) — turns a roster of proven engines into ONE portfolio.

Two jobs, neither of which is prediction:
  1. WEIGHTS  — how much capital each engine gets. Stronger + steadier engines (higher
     information ratio = alpha/volatility) carry more sail; weak shock-absorbers carry less.
     This is why a "pile of engines" isn't a portfolio — the Captain sizes them.
  2. THROTTLE — a slow tide gauge. When the market's been sinking (index below its long
     moving average), reef the sails (cut gross exposure). This is the bear-season answer
     we can't get from a short edge: don't predict the storm, just carry less sail in it.

Everything here is causal — the throttle on day d uses only market history through d.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


class RiskBrain:
    def __init__(self, throttle_ma: int = 40, throttle_floor: float = 1.0, min_weight: float = 0.10):
        # NOTE: throttle_floor defaults to 1.0 = OFF. The tide-throttle FAILED validation on
        # our 2024-26 data (no sustained bear to protect against → it only cut good trades,
        # costing ~20% CAGR AND worsening drawdown). Enable (floor<1.0) only with real reason
        # to expect a sustained bear; its protective value is UNPROVEN on available data.
        self.throttle_ma = throttle_ma
        self.throttle_floor = throttle_floor
        self.min_weight = min_weight           # a validated engine never gets fully starved

    # --- how much sail each boat carries -------------------------------------
    def weights(self, roster_trades: dict[str, pd.DataFrame]) -> dict[str, float]:
        ir = {}
        for name, tr in roster_trades.items():
            a = tr["alpha"].values
            if len(a) < 30 or a.std() == 0:
                ir[name] = 0.0
            else:
                ir[name] = max(a.mean() / a.std(), 0.0)   # information ratio, floored at 0
        total = sum(ir.values())
        if total <= 0:
            return {n: 1.0 / len(roster_trades) for n in roster_trades}   # equal if no edge signal
        raw = {n: v / total for n, v in ir.items()}
        # apply a floor so a proven diversifier isn't zeroed, then renormalise
        floored = {n: max(w, self.min_weight) for n, w in raw.items()}
        s = sum(floored.values())
        return {n: w / s for n, w in floored.items()}

    # --- the slow tide gauge -------------------------------------------------
    def throttle(self, tide: pd.DataFrame) -> pd.Series:
        """Return a per-date multiplier in [floor, 1]. Built from a causal market index:
        the index level on day d compounds realised market moves strictly BEFORE d."""
        t = tide.sort_values("date").copy()
        # tide1[d] = market's return from d to d+1; realised move landing ON day d is tide1[d-1].
        daily_ret = t["tide1"].shift(1).fillna(0) / 100.0
        level = (1 + daily_ret).cumprod()
        ma = level.rolling(self.throttle_ma, min_periods=self.throttle_ma // 2).mean()
        # Reef ONLY when we have an MA and the market is below it. During warm-up (no MA
        # yet) there's no signal, so carry full sail — don't reef on ignorance.
        mult = np.where(ma.notna() & (level < ma), self.throttle_floor, 1.0)
        return pd.Series(mult, index=pd.to_datetime(t["date"].values))

    def describe(self, roster_trades: dict[str, pd.DataFrame]) -> str:
        w = self.weights(roster_trades)
        parts = [f"{n} {w[n]:.0%}" for n in w]
        return "Captain's allocation: " + " | ".join(parts) + \
               f"  (throttle floor {self.throttle_floor:.0%} when tide is out)"
