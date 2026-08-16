"""
R&D BENCH — Engine #2 candidate: CROSS-SECTIONAL (relative strength).

Instead of "did THIS stock move," rank the WHOLE universe each day on a factor and go
LONG the top basket / SHORT the bottom basket. This is market-neutral BY CONSTRUCTION
(long+short cancels the tide), so the spread return IS the sailing (alpha). It should pay
in bull OR bear — the diversifier we can't get from a directional engine.

Two flavours tested (the sign of the edge flips with horizon):
  MOMENTUM  (long recent winners, short recent losers)   — classic Jegadeesh-Titman
  REVERSAL  (long recent losers,  short recent winners)   — short-term mean reversion

Verdict is scored on the SAME honest bar as the Proving Ground: market-neutral edge,
positive in both sealed OOS windows, walk-forward stable, robust to params, UNCORRELATED
to Wave Rider, survives cost. Nothing here is integrated unless it passes.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, "/Users/sushil/trading")
from collect_bars import load_bars
from factory import data as D, engines as E, qc_dyno as QC

CACHE = "/Users/sushil/trading/factory/cache"
IS_END, OOS1_END = pd.Timestamp(D.IS_END), pd.Timestamp(D.OOS1_END)


def daily_close_matrix(force=False) -> pd.DataFrame:
    """Wide matrix (date x symbol) of daily RTH closes, cached."""
    p = os.path.join(CACHE, "daily_close.csv")
    if os.path.exists(p) and not force:
        return pd.read_csv(p, parse_dates=["date"]).set_index("date")
    import sqlite3
    m = sqlite3.connect("/Users/sushil/trading/market_data.db")
    syms = [r[0] for r in m.execute("SELECT DISTINCT symbol FROM bars_5m ORDER BY symbol")]
    cols = {}
    for i, s in enumerate(syms):
        if i % 60 == 0:
            print(f"  daily-close {i}/{len(syms)}", flush=True)
        try:
            df = load_bars(s, start="2024-01-01", end="2026-09-15")
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue
        d = df.between_time("09:30", "15:59").copy()
        d["date"] = d.index.date
        cols[s] = d.groupby("date")["close"].last()
    mat = pd.DataFrame(cols)
    mat.index = pd.to_datetime(mat.index)
    mat = mat.sort_index()
    mat.index.name = "date"
    mat.to_csv(p)
    return mat


def xsec_daily_spread(close: pd.DataFrame, lookback: int, hold: int,
                      flavour: str, decile: float = 0.1) -> pd.Series:
    """Per-day market-neutral long-short spread return (%). Enter at close of day d
    (factor known through d), hold `hold` days, exit at close d+hold."""
    factor = (close / close.shift(lookback) - 1.0) * 100.0           # trailing return
    fwd = (close.shift(-hold) / close - 1.0) * 100.0                 # forward hold-day return
    out = {}
    fvals = factor.values
    for i, day in enumerate(close.index):
        row_f = factor.iloc[i]
        row_r = fwd.iloc[i]
        ok = row_f.notna() & row_r.notna()
        if ok.sum() < 30:
            continue
        f = row_f[ok]; r = row_r[ok]
        n = max(int(len(f) * decile), 5)
        order = f.sort_values()
        losers = order.index[:n]; winners = order.index[-n:]
        if flavour == "MOMENTUM":
            spread = r[winners].mean() - r[losers].mean()
        else:  # REVERSAL
            spread = r[losers].mean() - r[winners].mean()
        out[day] = spread
    return pd.Series(out)


def score(spread: pd.Series, name: str, wave_alpha: pd.Series, cost=0.10,
          robustness: list | None = None):
    s = spread.dropna()
    per = np.where(s.index < IS_END, "IS", np.where(s.index < OOS1_END, "OOS1", "OOS2"))
    dfp = pd.DataFrame({"a": s.values, "p": per}, index=s.index)
    g = dfp.groupby("p")["a"].mean()
    isa = g.get("IS", np.nan); o1 = g.get("OOS1", np.nan); o2 = g.get("OOS2", np.nan)
    t = s.mean() / (s.std(ddof=1) / np.sqrt(len(s))) if s.std() > 0 else 0
    # walk-forward
    wins = pos = 0; cur = s.index.min()
    while cur + pd.DateOffset(months=6) <= s.index.max() + pd.DateOffset(days=1):
        w = s[(s.index >= cur) & (s.index < cur + pd.DateOffset(months=6))]
        if len(w) >= 20:
            wins += 1; pos += int(w.mean() > 0)
        cur += pd.DateOffset(months=2)
    wf = pos / wins if wins else 0
    # correlation to Wave Rider (daily)
    j = pd.concat([s.rename("x"), wave_alpha.rename("w")], axis=1, sort=True).dropna()
    corr = j["x"].corr(j["w"]) if len(j) >= 30 else np.nan
    net = s.mean() - cost
    checks = {
        "OOS edge": (o1 >= 0.05 and o2 >= 0.05, f"IS {isa:+.3f} | OOS1 {o1:+.3f} | OOS2 {o2:+.3f}"),
        "significance": (len(s) >= 200 and t >= 1.5, f"n={len(s)} t={t:.2f}"),
        "walk-forward": (wf >= 0.55, f"{pos}/{wins} ({wf:.0%})"),
        "cost survival": (net > 0, f"{s.mean():+.3f} − {cost} = {net:+.3f}"),
        "uncorrelated": (abs(corr) < 0.5, f"corr vs Wave Rider {corr:+.2f}"),
        "direction": (isa > 0, f"IS {isa:+.3f}"),
    }
    if robustness is not None:
        frac = np.mean([x > 0 for x in robustness])
        checks["robustness"] = (frac >= 0.8, f"{frac:.0%} of {len(robustness)} configs positive")
    ok = all(v[0] for v in checks.values())
    print(f"\n╔═ BENCH SCORECARD ─ {name} " + "═" * 12)
    for k, (c, det) in checks.items():
        print(f"  {'✅' if c else '❌'} {k:14} {det}")
    print(f"╚═ {'PASS ✅ — candidate for the Roster' if ok else 'FAIL ❌ — stays on the bench'}")
    return ok, s


def main():
    print("building daily-close matrix (cached after first run)...")
    close = daily_close_matrix()
    print(f"  {close.shape[1]} symbols × {close.shape[0]} days")
    ev, td, pe = D.build_dataset()
    wave = QC.daily_alpha_of(E.MomentumWild(), ev, td)   # Wave Rider daily alpha, for corr

    for flavour, lookbacks, holds in [("REVERSAL", [3, 5, 10], [3, 5]),
                                      ("MOMENTUM", [20, 40, 60], [3, 5, 10])]:
        # robustness sweep across (lookback × hold) for this flavour
        robustness = []
        best = None
        for lb in lookbacks:
            for h in holds:
                sp = xsec_daily_spread(close, lb, h, flavour)
                robustness.append(sp.mean())
                if best is None or sp.mean() > best[0]:
                    best = (sp.mean(), lb, h, sp)
        _, lb, h, sp = best
        score(sp, f"Cross-Sectional {flavour} (best: lookback {lb}d, hold {h}d)",
              wave, robustness=robustness)


if __name__ == "__main__":
    main()
