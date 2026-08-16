"""
Factory snapshot — runs the whole factory once and writes a JSON the dashboard reads, so
the /factory page loads instantly instead of re-running a 30s backtest per web request.

Captures: data stats, every engine's Proving-Ground verdict + scorecard, the fleet result.
Run manually after any engine change, or on a schedule:  python -m factory.snapshot
"""
from __future__ import annotations
import os, json, datetime as dt
from factory import data as D, engines as E, qc_dyno as QC
from factory.risk_brain import RiskBrain
from factory.execution import run_fleet

OUT = os.path.join(os.path.dirname(__file__), "cache", "factory_snapshot.json")


def build() -> dict:
    events, tide, pers = D.build_dataset()
    snap = {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "data": {
            "events": int(len(events)),
            "universe": int(pers["symbol"].nunique()),
            "start": str(events["date"].min().date()),
            "end": str(events["date"].max().date()),
            "clusters": {k: int(v) for k, v in pers["cluster"].value_counts().items()},
        },
        "engines": [],
    }
    roster_alpha = {}
    slot_trades = {}
    sleeves = []
    for eng in E.all_engines():
        sc = QC.evaluate(eng, events, tide, roster_alpha=roster_alpha)
        roster_alpha[eng.spec.name] = QC.daily_alpha_of(eng, events, tide)
        snap["engines"].append({
            "name": eng.spec.name,
            "nickname": eng.spec.nickname,
            "hypothesis": eng.spec.hypothesis,
            "type": "sleeve" if eng.spec.sleeve else "slot",
            "verdict": "PASS" if sc.passed else "FAIL",
            "roster": bool(sc.passed),
            "hold_days": eng.spec.hold_days,
            "checks": {k: {"ok": bool(v[0]), "detail": v[1]} for k, v in sc.checks.items()},
            "stats": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in sc.stats.items()},
        })
        if sc.passed:
            if eng.spec.sleeve:
                tr = eng.run(events, tide)
                a = tr["alpha"]
                sleeves.append({"name": eng.spec.name, "nickname": eng.spec.nickname,
                                "alpha": round(float(a.mean()), 3),
                                "tstat": round(float(a.mean() / (a.std() / len(a) ** 0.5)), 2),
                                "legs": int(len(tr))})
            else:
                slot_trades[eng.spec.name] = eng.run(events, tide)

    fleet = {"slot": None, "sleeves": sleeves}
    if slot_trades:
        brain = RiskBrain()
        raw = run_fleet(slot_trades, tide, brain=brain, basis="ret")
        skill = run_fleet(slot_trades, tide, brain=brain, basis="alpha")
        fleet["slot"] = {
            "weights": brain.weights(slot_trades),
            "raw": {k: round(raw.stats[k], 2) for k in ("cagr", "sharpe", "maxdd", "total_return")},
            "skill": {k: round(skill.stats[k], 2) for k in ("cagr", "sharpe", "maxdd", "total_return")},
            "trades": raw.trades, "win_rate": round(raw.win_rate, 0),
        }
    snap["fleet"] = fleet
    snap["roster"] = [e["nickname"] for e in snap["engines"] if e["roster"]]
    return snap


def main():
    snap = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(snap, f, indent=2)
    print(f"wrote {OUT}: {len(snap['engines'])} engines, roster={snap['roster']}")


if __name__ == "__main__":
    main()
