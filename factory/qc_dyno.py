"""
The Proving Ground (QC Dyno) — the honest gate every engine must clear before it earns
capital. One function in: an engine. One card out: PASS / FAIL with the reasons.

It grades the SAILING (market-neutral alpha), never the raw return, so no engine can pass
by just riding the tide. Seven checks, each a real reason engines die:
  1. OOS alpha      — positive on sealed forward data (both windows), not just in-sample
  2. Significance   — enough trades + a real t-stat (not luck)
  3. Walk-forward   — positive across most rolling windows (stable, not one lucky split)
  4. Cost survival  — still positive after realistic round-trip friction
  5. Robustness     — neighboring hold/stop params also work (a plateau, not a spike)
  6. Uncorrelated   — adds diversification vs engines already on the Roster (corr < cap)
  7. Direction sane — in-sample edge points the right way to begin with
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
import numpy as np
import pandas as pd

from factory import data as D
from factory.contracts import Engine

# --- pass/fail thresholds (visible + tunable on purpose) ----------------------
MIN_OOS_ALPHA = 0.05      # % per trade, required in EACH sealed OOS window
MIN_N_OOS = 100           # trades needed across the OOS windows
MIN_TSTAT = 1.5           # overall alpha t-stat
MIN_WF_POS = 0.55         # fraction of walk-forward windows that must be positive
COST_DRAG = 0.10          # % round-trip friction subtracted from alpha
MAX_CORR = 0.50           # max |correlation| to any roster engine
WF_WINDOW_M = 6           # walk-forward window length (months)
WF_STEP_M = 2             # walk-forward step (months)


@dataclass
class Scorecard:
    engine: str
    nickname: str
    checks: dict = field(default_factory=dict)   # name -> (passed: bool, detail: str)
    stats: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(v[0] for v in self.checks.values())

    def render(self) -> str:
        head = f"╔═ PROVING GROUND ─ {self.nickname} [{self.engine}] " + "═" * 10
        lines = [head]
        for name, (ok, detail) in self.checks.items():
            lines.append(f"  {'✅' if ok else '❌'} {name:14} {detail}")
        verdict = "PASS ✅ — earns a spot on the Roster" if self.passed else "FAIL ❌ — back to the bench"
        lines.append(f"╚═ VERDICT: {verdict}")
        return "\n".join(lines)


def _daily_alpha(trades: pd.DataFrame) -> pd.Series:
    return trades.groupby("date")["alpha"].mean()


def evaluate(engine: Engine, events: pd.DataFrame, tide: pd.DataFrame,
             roster_alpha: dict[str, pd.Series] | None = None) -> Scorecard:
    """Run the full gauntlet. `roster_alpha` maps existing-engine-name -> its daily alpha
    series (for the correlation check); pass {} or None for the first engine."""
    roster_alpha = roster_alpha or {}
    sc = Scorecard(engine=engine.spec.name, nickname=engine.spec.nickname)
    trades = engine.run(events, tide)
    trades = trades.copy()
    trades["period"] = D.period_of(trades["date"])
    n = len(trades)
    sc.stats["n_trades"] = n
    if n < 30:
        sc.checks["sample"] = (False, f"only {n} trades — cannot evaluate")
        return sc

    # 1. OOS alpha — positive in BOTH sealed windows
    by_p = trades.groupby("period")["alpha"].agg(["mean", "size"])
    is_a = by_p.loc["IS", "mean"] if "IS" in by_p.index else float("nan")
    o1 = by_p.loc["OOS1", "mean"] if "OOS1" in by_p.index else float("nan")
    o2 = by_p.loc["OOS2", "mean"] if "OOS2" in by_p.index else float("nan")
    n_oos = int(by_p.reindex(["OOS1", "OOS2"])["size"].fillna(0).sum())
    sc.stats.update(alpha_IS=is_a, alpha_OOS1=o1, alpha_OOS2=o2, n_oos=n_oos)
    ok1 = (o1 >= MIN_OOS_ALPHA) and (o2 >= MIN_OOS_ALPHA)
    sc.checks["OOS alpha"] = (ok1, f"IS {is_a:+.3f}% | OOS1 {o1:+.3f}% | OOS2 {o2:+.3f}% "
                                   f"(need both OOS ≥ {MIN_OOS_ALPHA})")

    # 2. Significance — sample size + t-stat on overall alpha
    a = trades["alpha"].values
    tstat = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if a.std() > 0 else 0.0
    sc.stats["tstat"] = tstat
    ok2 = (n_oos >= MIN_N_OOS) and (tstat >= MIN_TSTAT)
    sc.checks["significance"] = (ok2, f"n_oos={n_oos} (≥{MIN_N_OOS}), t={tstat:.2f} (≥{MIN_TSTAT})")

    # 3. Walk-forward — rolling windows, fraction positive
    t = trades.sort_values("date")
    start, end = t["date"].min(), t["date"].max()
    wins, pos = 0, 0
    cur = start
    while cur + pd.DateOffset(months=WF_WINDOW_M) <= end + pd.DateOffset(days=1):
        w = t[(t["date"] >= cur) & (t["date"] < cur + pd.DateOffset(months=WF_WINDOW_M))]
        if len(w) >= 20:
            wins += 1
            pos += int(w["alpha"].mean() > 0)
        cur += pd.DateOffset(months=WF_STEP_M)
    wf_frac = pos / wins if wins else 0.0
    sc.stats["wf_frac"] = wf_frac
    sc.checks["walk-forward"] = (wf_frac >= MIN_WF_POS, f"{pos}/{wins} windows positive ({wf_frac:.0%}, need ≥{MIN_WF_POS:.0%})")

    # 4. Cost survival — alpha minus round-trip friction still positive
    net = a.mean() - COST_DRAG
    sc.stats["alpha_net"] = net
    sc.checks["cost survival"] = (net > 0, f"alpha {a.mean():+.3f}% − cost {COST_DRAG} = {net:+.3f}%")

    # 5. Robustness — neighboring hold/stop still positive overall (plateau, not spike)
    neigh, neigh_ok = [], True
    for dh in (-1, 0, 1):
        for ds in (-2, 0, 2):
            h = engine.spec.hold_days + dh
            s = engine.spec.stop_pct + ds
            if h < 1 or h > D.NF or s <= 0:
                continue
            alt = replace(engine.spec, hold_days=h, stop_pct=s)
            e2 = Engine(alt); e2.select = engine.select  # borrow the same selection
            am = e2.run(events, tide)["alpha"].mean()
            neigh.append(am)
            if not (dh == 0 and ds == 0) and am <= 0:
                neigh_ok = False
    frac_pos = np.mean([x > 0 for x in neigh]) if neigh else 0
    sc.stats["robust_frac"] = frac_pos
    sc.checks["robustness"] = (frac_pos >= 0.8, f"{frac_pos:.0%} of {len(neigh)} neighbor configs positive (need ≥80%)")

    # 6. Uncorrelated — vs each roster engine
    my_da = _daily_alpha(trades)
    worst_corr, worst_name = 0.0, "—"
    for nm, other in roster_alpha.items():
        j = pd.concat([my_da.rename("a"), other.rename("b")], axis=1).dropna()
        if len(j) >= 30:
            c = j["a"].corr(j["b"])
            if abs(c) > abs(worst_corr):
                worst_corr, worst_name = c, nm
    sc.stats["worst_corr"] = worst_corr
    ok6 = abs(worst_corr) < MAX_CORR
    sc.checks["uncorrelated"] = (ok6, f"max |corr| {worst_corr:+.2f} vs {worst_name} (need <{MAX_CORR})"
                                 if roster_alpha else "first engine — nothing to correlate to")

    # 7. Direction sane — in-sample alpha positive (the idea points the right way)
    sc.checks["direction"] = (is_a > 0, f"in-sample alpha {is_a:+.3f}% (must be > 0)")

    return sc


def daily_alpha_of(engine: Engine, events: pd.DataFrame, tide: pd.DataFrame) -> pd.Series:
    """Convenience for building the roster's correlation inputs."""
    return _daily_alpha(engine.run(events, tide))
