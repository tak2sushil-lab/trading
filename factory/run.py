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
    slot_engines = [e for e in passed if not e.spec.sleeve]
    sleeve_engines = [e for e in passed if e.spec.sleeve]

    # --- SLOT fleet: discrete-position engines (e.g. Wave Rider) ---
    if slot_engines:
        roster_trades = {e.spec.name: e.run(events, tide) for e in slot_engines}
        floor = 0.5 if "--throttle" in argv else 1.0   # throttle OFF by default (failed validation)
        brain = RiskBrain(throttle_floor=floor)
        print("\n" + brain.describe(roster_trades))
        print("\n--- SLOT FLEET — RAW (includes the 2024-26 bull tide — NOT repeatable) ---")
        print(run_fleet(roster_trades, tide, brain=brain, basis="ret").render())
        print("\n--- SLOT FLEET — SKILL-ONLY (market-neutral alpha — honest, repeatable) ---")
        print(run_fleet(roster_trades, tide, brain=brain, basis="alpha").render())

    # --- SLEEVE engines: market-neutral long-short books (e.g. Contrarian) ---
    for e in sleeve_engines:
        tr = e.run(events, tide); a = tr["alpha"]
        t = a.mean() / (a.std() / len(a) ** 0.5) if a.std() > 0 else 0
        print(f"\n--- SLEEVE — {e.spec.nickname} [{e.spec.name}] (market-neutral, pays bull OR bear) ---")
        print(f"  per-leg alpha {a.mean():+.3f}%  t={t:.1f}  win {100*(a>0).mean():.0f}%  {len(tr)} legs")
        print(f"  ↳ separate long-short book; blending its curve with the slot fleet is the next build.")

    print("\n⚠ Slot numbers inflated by survivorship + idealised fills; live forward = the real test.")


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
