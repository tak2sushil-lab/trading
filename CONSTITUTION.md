# TriVega Constitution
**The rules that keep the system simple, honest, and alive. Every change must pass this page.**
Adopted Jul 18 2026 (post-redesign). Amend only with explicit user approval.

---

## Article 1 — Live expectancy is the only truth
Backtests *nominate*; only live or shadow results *confirm*. No setup, gate, or parameter is
called "working" until it has live/shadow N and positive expectancy. Anything that can't show
its number gets frozen, not debated.

## Article 2 — Every rule ships with a contract
A new gate, setup, or exit may go live only with all three:
1. **Hypothesis** — one sentence: what it improves and why (written before the backtest).
2. **Auto-scoring** — it must log its decisions somewhere a nightly job scores against reality
   (gate_blocks / scan_log pattern). If its correctness can't be measured, it doesn't ship.
3. **Sunset date** — a review date (≤60 days out). At sunset: scored ≥55% correct or
   demonstrably +EV → stays; otherwise it dies. No rule lives on tenure.

## Article 3 — Complexity is capped
- Max **3 boolean entry gates** per system live at once (scored advisors/sizers are exempt —
  they inform, they don't veto).
- No **whole-day vetoes** without an intraday reconsideration path (London skip-day lesson).
- A new gate that overlaps an existing gate's job replaces it, never stacks on it.
- One bad day is never grounds for a new rule. (Two independent episodes + a mechanism is the
  minimum bar — see PM_SHORT for the standard done right.)

## Article 4 — The sim must match the machine
- A backtest validates a change only if the sim executes the **same code path at the same
  granularity** the live system runs (partial-bar RVOL and London 15-sec BE churn are the
  canonical failures).
- Any discovered live/sim divergence is a **P0 bug** — fix the divergence before tuning
  anything it touches.
- Every sim run states its date range explicitly (sim_replay `--end` defaults to 2026-06-16 —
  never rely on defaults).

## Article 5 — Books trade only when they're working
Each book (equity LONG, equity SHORT, futures NY, London) runs under a measured health signal
or an explicit user decision — never by momentum of habit. Flat is a position. A book that
hasn't earned in its trailing window sits down without shame and stands back up on data.

## Article 6 — Protect the right tail
Exits are judged by **capture efficiency** (% of max favorable excursion kept) and the win/loss
size ratio — not by win rate. Any proposed exit change must show it doesn't amputate winners:
report the counterfactual P&L of the 5 largest winners under the new rule before shipping.

## Article 7 — Instrument first, gate later
New context sources (decoder, news engine, breadth, anything "aware") enter as **logged
features** scored offline against forward returns. They may become sizers after scoring well
for ≥30 days, and vetoes only after ≥60 days — never straight to a gate.

## Article 8 — Ops hygiene is part of the strategy
- Live code is committed the same day it ships. An uncommitted live system is an incident.
- After any change: restart the right service (check the service-name trap table in CLAUDE.md)
  and run the required validation (`sim_today.py` for equity; explicit-range sim_replay for
  futures).
- CLAUDE.md is updated in the same session as the change it describes.

---

*Checklist for any proposed change:*
`[ ] hypothesis written  [ ] auto-scored  [ ] sunset date  [ ] sim==live path  [ ] gate cap respected  [ ] right-tail counterfactual  [ ] commit + restart + validate + document`
