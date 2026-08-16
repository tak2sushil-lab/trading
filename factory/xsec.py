"""
Cross-sectional engines — the market-neutral boats.

Instead of "did THIS stock move," rank the WHOLE universe each day on a factor and go LONG
one end / SHORT the other. Long+short cancels the Tide, so these pay in bull OR bear — the
diversifier a directional engine can't be. They are SLEEVES (a slice of capital run as a
long-short book), not discrete slots — the Fill Desk treats them as a return stream.

They plug into the SAME framework: `run()` returns the standard Trade table (per-leg, with
market-neutral alpha), so The Proving Ground scores them identically. Their `neighbors()`
vary lookback/hold (their own knobs) for the robustness check.
"""
from __future__ import annotations
from dataclasses import replace
import numpy as np
import pandas as pd

from factory.contracts import Engine, EngineSpec, TRADE_COLUMNS
from factory import data as D


class CrossSectionalEngine(Engine):
    """Base for whole-universe long-short engines. Subclass sets `factor()` + `long_end`."""
    lookback: int = 5
    decile: float = 0.1
    long_end: str = "BOTTOM"   # go LONG the low end of the factor, SHORT the high end

    def factor(self, close: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    def run(self, events=None, tide=None) -> pd.DataFrame:
        close = D.load_daily_close()
        hold = self.spec.hold_days
        fac = self.factor(close)
        fwd = (close.shift(-hold) / close - 1.0) * 100.0     # forward hold-day return
        mkt = fwd.mean(axis=1)                                # close-anchored Tide per day
        rows = []
        idx = close.index
        for i in range(len(idx)):
            f = fac.iloc[i]; r = fwd.iloc[i]
            ok = f.notna() & r.notna()
            if ok.sum() < 30:
                continue
            f = f[ok]; r = r[ok]
            n = max(int(len(f) * self.decile), 5)
            order = f.sort_values()
            bottom, top = order.index[:n], order.index[-n:]
            longs = bottom if self.long_end == "BOTTOM" else top
            shorts = top if self.long_end == "BOTTOM" else bottom
            t = float(mkt.iloc[i]); day = idx[i]
            for s in longs:   # LONG leg: earn r; alpha = r - tide
                rows.append((day, s, "LONG", float(f[s]), float(r[s]), t, float(r[s]) - t))
            for s in shorts:  # SHORT leg: earn -r; alpha = tide - r
                rows.append((day, s, "SHORT", float(f[s]), -float(r[s]), t, t - float(r[s])))
        df = pd.DataFrame(rows, columns=["date", "symbol", "side", "day_chg", "ret", "tide", "alpha"])
        df["engine"] = self.spec.name
        df["cluster"] = "ANY"
        df["hold_days"] = hold
        df["stopped"] = 0
        return df[TRADE_COLUMNS].dropna(subset=["ret", "tide", "alpha"])

    def neighbors(self):
        out = []
        for lb in (self.lookback - 2, self.lookback, self.lookback + 2):
            for h in (self.spec.hold_days - 2, self.spec.hold_days, self.spec.hold_days + 2):
                if lb < 1 or h < 1 or h > 15:
                    continue
                e = self.__class__()
                e.lookback = lb
                e.spec = replace(self.spec, hold_days=h)
                out.append(e)
        return out


class XSectionalReversal(CrossSectionalEngine):
    """Back the laggards vs the leaders. Long the biggest recent losers, short the biggest
    recent winners — short-term mean reversion. Bench-validated PASS; uncorrelated to Wave Rider."""
    lookback = 3
    long_end = "BOTTOM"   # long the losers

    def __init__(self):
        super().__init__(EngineSpec(
            name="xsec_reversal", nickname="Contrarian",
            hypothesis="Recent extreme moves overshoot; laggards outperform leaders over the next days.",
            side="LONG", hold_days=5, stop_pct=0.0, personality="ANY", direction="XS", sleeve=True,
        ))

    def factor(self, close):
        return (close / close.shift(self.lookback) - 1.0) * 100.0   # trailing return


class XSectionalLowVol(CrossSectionalEngine):
    """Betting-against-beta. Long the calmest names, short the wildest — the low-volatility
    anomaly (calm stocks outperform risk-adjusted). Engine #3 candidate."""
    lookback = 20
    long_end = "BOTTOM"   # long the low-vol names

    def __init__(self):
        super().__init__(EngineSpec(
            name="xsec_lowvol", nickname="Steady Hand",
            hypothesis="Low-volatility stocks outperform high-volatility ones on a risk-adjusted basis.",
            side="LONG", hold_days=5, stop_pct=0.0, personality="ANY", direction="XS", sleeve=True,
        ))

    def factor(self, close):
        ret = close.pct_change()
        return ret.rolling(self.lookback).std() * 100.0   # trailing realised volatility
