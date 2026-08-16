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

    def run(self, events=None, tide=None, close=None) -> pd.DataFrame:
        # `close` can be injected (e.g. a 2022-23 bear matrix from bear_test.py); default =
        # the factory's 2024-26 cache. Everything downstream is regime-agnostic.
        if close is None:
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


class ShortTermReversal(CrossSectionalEngine):
    """Whiplash — DISCARDED Aug 16 2026 (unregistered; kept for reference/revertibility).
    Dead in this universe (IS alpha −0.05%, t=−0.25) and negative in every bear-test regime.
    Classic 1-day cross-sectional reversal (Jegadeesh 1990): yesterday's biggest
    losers bounce, yesterday's biggest winners give back — over a SINGLE day. Different horizon
    from Contrarian (which ranks a 3-day move and holds 5d), so it may diversify even within the
    reversal family. High turnover → the cost check is the one to watch."""
    lookback = 1
    long_end = "BOTTOM"   # long yesterday's losers, short yesterday's winners

    def __init__(self):
        super().__init__(EngineSpec(
            name="xsec_st_reversal", nickname="Whiplash",
            hypothesis="Single-day extreme moves overshoot and snap back the very next day.",
            side="LONG", hold_days=1, stop_pct=0.0, personality="ANY", direction="XS", sleeve=True,
        ))

    def factor(self, close):
        return (close / close.shift(self.lookback) - 1.0) * 100.0   # yesterday's return


class OvernightDrift(CrossSectionalEngine):
    """Night Shift. Cross-sectional overnight momentum (Lou-Polk-Skouras 'A Tug of War', 2019):
    overnight (close->open) returns PERSIST — names with strong recent overnight drift keep
    earning it — while the intraday session tends to reverse. Enter at today's close, exit at
    tomorrow's open (a single overnight hold), long the strong-overnight names / short the weak.
    Needs the open matrix as well as close; its own run() computes close->open returns."""
    lookback = 10
    decile = 0.1
    long_end = "TOP"      # long the persistent overnight-winners, short the overnight-losers

    def __init__(self):
        super().__init__(EngineSpec(
            name="xsec_overnight", nickname="Night Shift",
            hypothesis="Overnight (close-to-open) returns persist cross-sectionally while the intraday leg reverses.",
            side="LONG", hold_days=1, stop_pct=0.0, personality="ANY", direction="XS", sleeve=True,
        ))

    def factor(self, close):  # unused (overnight run computes its own factor); kept for interface
        return close * np.nan

    def _signal(self, overnight):
        """The picking signal from the overnight-return matrix. Base = trailing MEAN overnight
        drift (magnitude). Subclasses override (e.g. consistency = how OFTEN it gaps up)."""
        return overnight.rolling(self.lookback).mean()

    def run(self, events=None, tide=None, close=None, opens=None) -> pd.DataFrame:
        if close is None:
            close = D.load_daily_close()
        if opens is None:
            opens = D.load_daily_open()
        # align matrices on common dates/symbols
        opens = opens.reindex(index=close.index, columns=close.columns)
        overnight = (opens / close.shift(1) - 1.0) * 100.0     # close[t-1] -> open[t], known at close[t]
        fac = self._signal(overnight)                          # picking signal, as-of close[t]
        fwd = overnight.shift(-1)                              # next overnight = enter close[t], exit open[t+1]
        mkt = fwd.mean(axis=1)                                 # overnight Tide per day
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
            longs = top if self.long_end == "TOP" else bottom
            shorts = bottom if self.long_end == "TOP" else top
            t = float(mkt.iloc[i]); day = idx[i]
            for s in longs:
                rows.append((day, s, "LONG", float(f[s]), float(r[s]), t, float(r[s]) - t))
            for s in shorts:
                rows.append((day, s, "SHORT", float(f[s]), -float(r[s]), t, t - float(r[s])))
        df = pd.DataFrame(rows, columns=["date", "symbol", "side", "day_chg", "ret", "tide", "alpha"])
        df["engine"] = self.spec.name
        df["cluster"] = "ANY"
        df["hold_days"] = 1
        df["stopped"] = 0
        return df[TRADE_COLUMNS].dropna(subset=["ret", "tide", "alpha"])

    def neighbors(self):
        out = []
        for lb in self._nbr_lookbacks:
            for dec in (0.1, 0.2):
                e = type(self)()          # preserve subclass (e.g. OvernightConsistency)
                e.lookback = lb
                e.decile = dec
                out.append(e)
        return out

    _nbr_lookbacks = (5, 10, 20)


class OvernightConsistency(OvernightDrift):
    """Clockwork — Night Shift v2. The user's refinement: don't chase the single BIGGEST overnight
    gap, back the names that gap up like CLOCKWORK — rank by how OFTEN (fraction of recent nights)
    a wild stock's close->open was positive, long the most-reliable repeat-gappers. Beats the raw
    magnitude signal in 2026 (~2x) and fixes the 2022 bear where magnitude went negative. This is
    the recognized 'consistency / hit-rate as momentum quality' refinement, on the overnight leg."""
    lookback = 30                # robust mid-plateau (20-40 all work); ~6 trading weeks
    decile = 0.1
    long_end = "TOP"
    _nbr_lookbacks = (20, 30, 40)   # the validated plateau

    def __init__(self):
        # bypass OvernightDrift.__init__ (which hard-codes the magnitude spec); set our own
        Engine.__init__(self, EngineSpec(
            name="xsec_overnight_consist", nickname="Clockwork",
            hypothesis="Wild stocks that gap up CONSISTENTLY (high recent up-night frequency) keep gapping up overnight.",
            side="LONG", hold_days=1, stop_pct=0.0, personality="ANY", direction="XS", sleeve=True,
        ))

    def _signal(self, overnight):
        return (overnight > 0).rolling(self.lookback).mean()   # fraction of recent nights that gapped up
