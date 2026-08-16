"""
Factory runner (CLI).

  venv/bin/python -m factory.run build-data [--force]   # build/refresh the 2.5yr cache
  venv/bin/python -m factory.run validate [engine]      # run engine(s) through the Proving Ground
  venv/bin/python -m factory.run fleet [--no-throttle]  # sail the whole fleet as one portfolio
  venv/bin/python -m factory.run all                    # validate all + fleet

Places no orders. Pure research.
"""
from __future__ import annotations
import sys
from factory import data as D
from factory import engines as E
from factory import qc_dyno as QC
from factory.risk_brain import RiskBrain
from factory.execution import run_fleet


def cmd_build_data(argv):
    D.build_dataset(force="--force" in argv)


def cmd_validate(argv):
    events, tide, pers = D.build_dataset()
    names = [a for a in argv if not a.startswith("-")]
    engines = [E.get_engine(n) for n in names] if names else E.all_engines()
    roster_alpha = {}   # accumulate so each engine is correlation-checked vs the prior ones
    passed = []
    for eng in engines:
        sc = QC.evaluate(eng, events, tide, roster_alpha=roster_alpha)
        print("\n" + sc.render())
        if sc.passed:
            passed.append(eng.spec.name)
        # add to roster-alpha regardless, so correlation reflects the real candidate set
        roster_alpha[eng.spec.name] = QC.daily_alpha_of(eng, events, tide)
    print(f"\n>>> {len(passed)}/{len(engines)} engines PASS the Proving Ground: {passed}")
    return passed


def cmd_fleet(argv):
    events, tide, pers = D.build_dataset()
    # only sail engines that actually pass the gate
    passed = []
    roster_alpha = {}
    for eng in E.all_engines():
        sc = QC.evaluate(eng, events, tide, roster_alpha=roster_alpha)
        roster_alpha[eng.spec.name] = QC.daily_alpha_of(eng, events, tide)
        if sc.passed:
            passed.append(eng)
    if not passed:
        print("No engines pass the Proving Ground — nothing to sail.")
        return
    roster_trades = {e.spec.name: e.run(events, tide) for e in passed}
    # Throttle OFF by default — it failed validation on our (bull-only) data. --throttle to test.
    floor = 0.5 if "--throttle" in argv else 1.0
    brain = RiskBrain(throttle_floor=floor)
    print("\n" + brain.describe(roster_trades))
    print("\n--- RAW (includes the 2024-26 bull-market tide — NOT repeatable) ---")
    raw = run_fleet(roster_trades, tide, brain=brain, basis="ret")
    print(raw.render())
    print("\n--- SKILL-ONLY (market-neutral alpha — the honest, repeatable number) ---")
    skill = run_fleet(roster_trades, tide, brain=brain, basis="alpha")
    print(skill.render())
    print("\n⚠ Both curves are still inflated by universe survivorship (these 293 names were")
    print("  partly chosen for having moved) + idealised stop fills. Live forward = the real test.")
    return raw, skill


def main():
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "all"
    rest = argv[1:]
    if cmd == "build-data":
        cmd_build_data(rest)
    elif cmd == "validate":
        cmd_validate(rest)
    elif cmd == "fleet":
        cmd_fleet(rest)
    elif cmd == "all":
        cmd_validate([])
        cmd_fleet([])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
