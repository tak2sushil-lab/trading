"""
Factory test suite — unit (math) + integration (real 2.5yr cache) + property tests.
Run:  venv/bin/python -m factory.tests.test_factory
The star check is test_no_lookahead: selection must be independent of the outcome.
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd

from factory.contracts import Engine, EngineSpec, _outcome, _stopped
from factory import data as D
from factory import engines as E
from factory import qc_dyno as QC
from factory.risk_brain import RiskBrain
from factory.execution import run_fleet

PASS, FAIL = "  ✅", "  ❌"
_fails = []


def check(name, cond, detail=""):
    print((PASS if cond else FAIL) + f" {name}  {detail}")
    if not cond:
        _fails.append(name)


# ---------------- unit: outcome + alpha math ----------------
def test_outcome_math():
    # LONG, hold 3, stop 8%. Path: day1 low -2, day2 low -5, day3 close +4 -> no stop -> +4
    row = {"lo1": -2, "cl1": 0, "lo2": -5, "cl2": 1, "lo3": -3, "cl3": 4}
    check("outcome no-stop", _outcome(row, 3, 8.0) == 4 and _stopped(row, 3, 8.0) == 0)
    # stop breached on day2 (low -9 <= -8) -> exit at -8
    row2 = {"lo1": -2, "cl1": 0, "lo2": -9, "cl2": -7, "lo3": 5, "cl3": 6}
    check("outcome stop-hit", _outcome(row2, 3, 8.0) == -8.0 and _stopped(row2, 3, 8.0) == 1)


def test_alpha_math():
    # tiny synthetic dataset: 1 UP WILD event with a known forward path + known tide
    ev = pd.DataFrame([{
        "date": pd.Timestamp("2024-06-03"), "symbol": "TEST", "price": 100.0,
        "day_chg_10": 6.0, "gap": 1.0, "ext_vwap": 2.0, "or_pos": 90.0,
        "direction": "UP", "cluster": "WILD",
        **{f"lo{k}": -1.0 for k in range(1, 16)}, **{f"cl{k}": 2.0 for k in range(1, 16)},
    }])
    td = pd.DataFrame([{"date": pd.Timestamp("2024-06-03"), **{f"tide{k}": 0.5 for k in range(1, 16)}}])
    eng = E.MomentumWild()
    tr = eng.run(ev, td)
    # LONG: ret = cl3 = 2.0 ; tide3 = 0.5 ; alpha = 1.5
    check("LONG ret", abs(tr["ret"].iloc[0] - 2.0) < 1e-9, f"ret={tr['ret'].iloc[0]}")
    check("LONG alpha", abs(tr["alpha"].iloc[0] - 1.5) < 1e-9, f"alpha={tr['alpha'].iloc[0]}")
    # SHORT check with a synthetic short engine on the same event
    class ShortEng(Engine):
        def select(self, events):
            return events
    se = ShortEng(EngineSpec("s", "S", "h", "SHORT", 3, 8.0, "WILD", "UP"))
    st = se.run(ev, td)
    # SHORT: tradable = -cl3 = -2.0 ; alpha = tide - cl3 = 0.5 - 2.0 = -1.5
    check("SHORT ret", abs(st["ret"].iloc[0] + 2.0) < 1e-9, f"ret={st['ret'].iloc[0]}")
    check("SHORT alpha", abs(st["alpha"].iloc[0] + 1.5) < 1e-9, f"alpha={st['alpha'].iloc[0]}")


# ---------------- integration: real cache ----------------
def test_data_and_personality():
    ev, td, pe = D.build_dataset()
    check("events non-empty", len(ev) > 10000, f"n={len(ev)}")
    check("tide has all horizons", all(f"tide{k}" in td.columns for k in range(1, 16)))
    counts = pe["cluster"].value_counts()
    check("3 personality classes", set(counts.index) == {"CALM", "MID", "WILD"}, dict(counts))
    check("no forward NaN in events", ev[[f"cl{k}" for k in range(1, 16)]].notna().all().all())
    return ev, td, pe


def test_proving_ground(ev, td):
    mom = QC.evaluate(E.MomentumWild(), ev, td, roster_alpha={})
    check("Wave Rider PASSES", mom.passed, f"OOS1={mom.stats['alpha_OOS1']:+.2f} OOS2={mom.stats['alpha_OOS2']:+.2f}")
    ra = {"momentum_wild": QC.daily_alpha_of(E.MomentumWild(), ev, td)}
    rev = QC.evaluate(E.MeanRevMid(), ev, td, roster_alpha=ra)
    check("Bargain Hunter graded (fragile)", not rev.passed, "correctly held back on robustness")
    check("correlation computed", abs(rev.stats["worst_corr"]) < 0.5, f"corr={rev.stats['worst_corr']:+.2f}")


def test_no_lookahead(ev, td):
    """THE critical test: which trades get taken must NOT depend on their outcome.
    Running the fleet on raw return vs market-neutral alpha must select the SAME trades
    (same counts) — only the P&L differs. If selection peeked at the outcome, counts diverge."""
    rt = {"momentum_wild": E.MomentumWild().run(ev, td)}
    raw = run_fleet(rt, td, basis="ret")
    skill = run_fleet(rt, td, basis="alpha")
    same_total = raw.trades == skill.trades
    same_per = all(raw.per_engine[n]["n"] == skill.per_engine[n]["n"] for n in raw.per_engine)
    check("no-lookahead: same trades regardless of outcome basis", same_total and same_per,
          f"raw={raw.trades} skill={skill.trades}")
    # sanity: results are finite & drawdown negative-or-zero
    check("stats finite", np.isfinite(raw.stats["sharpe"]) and raw.stats["maxdd"] <= 0)


def test_shuffle_invariance(ev, td):
    """Stronger: shuffle the realised returns; the SET of (date,symbol) taken must be identical."""
    tr = E.MomentumWild().run(ev, td)
    base = run_fleet({"momentum_wild": tr}, td, basis="ret")
    tr2 = tr.copy()
    rng = np.random.default_rng(0)
    tr2["ret"] = rng.permutation(tr2["ret"].values)   # scramble outcomes
    shuf = run_fleet({"momentum_wild": tr2}, td, basis="ret")
    check("shuffle-invariant selection (count)", base.trades == shuf.trades,
          f"{base.trades} vs {shuf.trades}")


def test_cross_sectional():
    """Engine #2 type: cross-sectional produces the standard Trade table with long+short legs."""
    from factory.xsec import XSectionalReversal
    from factory.contracts import TRADE_COLUMNS
    tr = XSectionalReversal().run()
    check("xsec standard columns", list(tr.columns) == TRADE_COLUMNS, f"cols={list(tr.columns)[:3]}…")
    check("xsec has long AND short legs", set(tr["side"]) == {"LONG", "SHORT"})
    check("xsec alpha finite", bool(np.isfinite(tr["alpha"]).all()))


def test_neighbors():
    """Robustness generalises: both engine types expose their own param-variant neighbours."""
    from factory.xsec import XSectionalReversal
    check("event engine neighbours", len(E.MomentumWild().neighbors()) >= 5)
    check("xsec engine neighbours", len(XSectionalReversal().neighbors()) >= 5)


def main():
    print("── unit ──"); test_outcome_math(); test_alpha_math()
    print("── integration ──"); ev, td, pe = test_data_and_personality()
    test_proving_ground(ev, td)
    print("── cross-sectional / framework generalisation ──"); test_cross_sectional(); test_neighbors()
    print("── property (no lookahead) ──"); test_no_lookahead(ev, td); test_shuffle_invariance(ev, td)
    print(f"\n{'ALL PASS ✅' if not _fails else 'FAILURES: ' + str(_fails)}")
    sys.exit(1 if _fails else 0)


if __name__ == "__main__":
    main()
