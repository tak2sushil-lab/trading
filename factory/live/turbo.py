"""
Turbo — the Factory→Options execution layer (SHADOW-first).

Options are NOT a strategy here. Turbo takes a *Roster-validated* engine's already-proven
directional signal (first client: Wave Rider — momentum_wild) and expresses it as a leveraged,
defined-risk OPTIONS structure instead of shares — but ONLY when the structure's expected value,
measured over the engine's OWN real 3-day outcome distribution, beats its carry. If no structure
clears that bar, Turbo trades nothing. Refusing is a valid, intended outcome.

Design: docs/OPTIONS_FACTORY_BRIDGE_DESIGN_2026-08-16.md.  Analogy: a turbocharger multiplies a
running, healthy engine's power but blows the gasket on a weak one — which is exactly what the old
equity-echo options book did (turbocharged a zero-edge signal, bled -$4,502). See the
options-edge-dive-aug16 memory.

THE EDGE-BUDGET GATE (upgraded from the design doc's "median move clears breakeven"):
  Options are convex, so the honest test is not the typical move but the EXPECTED VALUE of the
  structure's payoff across the engine's real 3-day terminal moves. We reprice the exact structure
  (Black-Scholes, at day-3 with remaining time) at each of Wave Rider's ~6,481 historical WILD
  outcomes (with its 8% stop modeled), average, net the entry cost → EV per premium. Gate: that
  EV/risk must clear EDGE_MIN and IV rank must be >= 25.  This is idealized (no vol-crush, no real
  bid-ask beyond what the live chain already prices) — which is exactly what the live-shadow marks
  measure. Nothing here places an order.

SAFETY / ON-RAMP (mirrors wave_rider.py):
  MODE='SHADOW' (default) — records the structure it WOULD trade to options_shadow, marks it to
    the live option chain daily, computes leverage-premium-vs-shares. Places NO orders.
  MODE='LIVE' — reserved; not implemented in v1 (options stays instrument-first until the shadow
    book shows a positive leverage premium net of carry). Flip only after the shadow verdict.

Stateless between runs; all state in the DB. Run one pass:  python -m factory.live.turbo
Score a single symbol offline:  python -m factory.live.turbo --plan NVDA
"""
from __future__ import annotations
import os, sys, sqlite3, json, datetime as dt
import numpy as np
import pandas as pd
sys.path.insert(0, "/Users/sushil/trading")
sys.path.insert(0, "/Users/sushil/trading/options")

MODE   = os.environ.get("TURBO_MODE", "SHADOW").upper()   # SHADOW | LIVE(reserved)
DB     = "/Users/sushil/trading/trades.db"
CACHE  = "/Users/sushil/trading/factory/cache"
ENGINE = "momentum_wild"          # first (only) client — Wave Rider
HOLD_DAYS = 3
STOP_PCT  = 8.0

# ── Turbo config (all provisional — tuned once shadow data lands) ─────────────
EDGE_MIN      = 0.10     # required idealized EV as a fraction of risk (+10%)
IV_FLOOR      = 25       # the one data-validated exclusion (IV rank < 25 = quiet name, skip)
IV_CREDIT_HI  = 65       # IV rank > this → sell premium (credit) instead of buy (debit)
US_HOLIDAYS   = {"2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"}

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None


def log(msg: str):
    ts = (dt.datetime.now(ET) if ET else dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [TURBO {MODE}] {msg}", flush=True)


# ─────────────────────── engine outcome distribution ───────────────────────
_SCENARIOS: np.ndarray | None = None

def wave_rider_scenarios() -> np.ndarray:
    """The engine's REAL 3-day terminal moves (fraction), with its 8% stop modeled — the
    honest distribution to price a structure against. Cached from the Factory event table."""
    global _SCENARIOS
    if _SCENARIOS is not None:
        return _SCENARIOS
    e = pd.read_csv(os.path.join(CACHE, "events.csv"))
    wr = e[(e.cluster == "WILD") & (e.day_chg_10 >= 3.0) & (e.ext_vwap >= 0)].copy()
    def eff(r):
        for k in (1, 2, 3):                    # 8% stop hit intraday → exit at -8%
            lo = r.get(f"lo{k}")
            if pd.notna(lo) and lo <= -STOP_PCT:
                return -STOP_PCT
        return r.get("cl3")
    moves = wr.apply(eff, axis=1).dropna().values / 100.0
    _SCENARIOS = moves
    return moves


# ─────────────────────── Black-Scholes structure EV ───────────────────────
def structure_ev(kind: str, entry_px: float, lo_strike: float, hi_strike: float,
                 cost_or_credit: float, iv_pct: float, dte: int) -> dict:
    """Expected value of the structure at the day-3 exit, priced across the engine's real
    outcome distribution. `kind` = 'DEBIT_CALL' or 'CREDIT_PUT'. cost_or_credit is per-share
    debit paid (DEBIT) or credit received (CREDIT). Returns EV/risk ('roi') + P(win)."""
    from engine import _bs_spread_vals, _bs_put_spread_vals
    moves = wave_rider_scenarios()
    S3    = (entry_px * (1.0 + moves)).reshape(-1, 1)
    Trem  = np.array([[max(dte - HOLD_DAYS, 1) / 252.0]])
    sigma = max(iv_pct / 100.0, 0.05)
    width = abs(hi_strike - lo_strike)
    if kind == "DEBIT_CALL":                                   # long lo call / short hi call
        val   = _bs_spread_vals(S3, lo_strike, hi_strike, sigma, Trem)[:, 0]
        pnl   = val - cost_or_credit                           # per share
        risk  = cost_or_credit
    else:                                                      # CREDIT_PUT: short hi put / long lo put
        val   = _bs_put_spread_vals(S3, hi_strike, lo_strike, sigma, Trem)[:, 0]  # cost to close
        pnl   = cost_or_credit - val
        risk  = max(width - cost_or_credit, 0.01)
    ev = float(pnl.mean())
    return {"ev_per_share": ev, "roi": ev / risk if risk > 0 else 0.0,
            "p_win": float((pnl > 0).mean()), "risk_per_share": risk,
            "n_scenarios": len(moves)}


# ─────────────────────── plan a structure for a ticket ───────────────────────
def plan_for_symbol(symbol: str) -> dict:
    """Route by IV, build a real-chain structure via the existing calculators, then apply the
    convex Edge-Budget gate. Returns a plan dict (gate PASS/SKIP + full diagnostics)."""
    import options_trader as ot
    iv_d   = ot.get_iv_rank(symbol)
    iv_rnk = (iv_d.get("iv_rank") if iv_d else None) or 0

    plan = {"symbol": symbol, "iv_rank": iv_rnk, "gate": "SKIP", "gate_reason": "",
            "strategy": None}

    if iv_rnk < IV_FLOOR:
        plan["gate_reason"] = f"IV rank {iv_rnk:.0f} < {IV_FLOOR} (quiet name — data-validated skip)"
        return plan

    if iv_rnk > IV_CREDIT_HI:
        calc = ot.run_bull_put_credit_calc(symbol, 1); kind = "CREDIT_PUT"
        strat = "BULL_PUT_CREDIT"
    else:
        calc = ot.run_calculator(symbol, 1); kind = "DEBIT_CALL"
        strat = "DEBIT_CALL_SPREAD"

    if not calc or "error" in calc:
        plan["gate_reason"] = f"no structure: {calc.get('error') if calc else 'calc failed'}"
        return plan

    liq = (calc.get("liquidity") or {}).get("cost_pct")
    if not (calc.get("entry_gates") or {}).get("verdict") == "ENTER":
        plan.update({"gate_reason": f"illiquid (spread cost {liq}% of width)", "strategy": strat})
        return plan

    lo_k, hi_k = min(calc["long_strike"], calc["short_strike"]), max(calc["long_strike"], calc["short_strike"])
    # KEY INCONSISTENCY (validated Aug 16): debit calcs set max_loss_$/max_profit_$; credit calcs
    # set max_loss/max_profit (no _$ suffix). Read both so the credit route isn't silently zeroed.
    max_loss_d   = calc.get("max_loss_$")   or calc.get("max_loss")   or 0
    max_profit_d = calc.get("max_profit_$") or calc.get("max_profit") or 0
    entry_px     = calc["underlying"]
    if kind == "DEBIT_CALL":
        cost_or_credit = max_loss_d / 100.0           # debit paid per share
    else:
        cost_or_credit = max_profit_d / 100.0         # credit received per share

    ev = structure_ev(kind, entry_px, lo_k, hi_k, cost_or_credit, calc.get("current_iv") or 0, calc["dte"])

    plan.update({
        "strategy": strat, "kind": kind,
        "underlying_entry": entry_px, "expiry": calc["expiry"], "dte": calc["dte"],
        "long_strike": calc["long_strike"], "short_strike": calc["short_strike"],
        "net_debit": calc.get("net_debit"), "max_loss_$": max_loss_d, "max_profit_$": max_profit_d,
        "breakeven": calc.get("breakeven"), "breakeven_pct": calc.get("breakeven_pct"),
        "liq_cost_pct": liq, "ev_roi": round(ev["roi"], 4), "ev_p_win": round(ev["p_win"], 3),
        "ev_per_share": round(ev["ev_per_share"], 4),
    })
    if ev["roi"] >= EDGE_MIN:
        plan["gate"] = "PASS"
        plan["gate_reason"] = f"EV/risk {ev['roi']*100:+.0f}% ≥ {EDGE_MIN*100:.0f}% (P(win) {ev['p_win']*100:.0f}%)"
    else:
        plan["gate_reason"] = f"EV/risk {ev['roi']*100:+.0f}% < {EDGE_MIN*100:.0f}% — carry not paid"
    return plan


# ─────────────────────── DB ───────────────────────
def init_db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS options_shadow(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wave_trade_id INTEGER UNIQUE, symbol TEXT, engine TEXT,
        planned_at TEXT, entry_date TEXT, exit_on_date TEXT,
        strategy TEXT, kind TEXT, iv_rank REAL, dte INTEGER, expiry TEXT,
        long_strike REAL, short_strike REAL, net_debit REAL,
        max_loss REAL, max_profit REAL, breakeven REAL, breakeven_pct REAL, liq_cost_pct REAL,
        ev_roi REAL, ev_p_win REAL, gate TEXT, gate_reason TEXT,
        underlying_entry REAL, shares_entry_price REAL,
        status TEXT DEFAULT 'PLANNED',
        mark_date TEXT, mark_value REAL, mark_unreal REAL,
        exit_date TEXT, exit_value REAL, struct_ret_pct REAL,
        shares_ret_pct REAL, leverage_premium REAL,
        mode TEXT, raw TEXT)""")
    c.commit(); c.close()


def already_planned(wave_trade_id: int) -> bool:
    c = sqlite3.connect(DB)
    hit = c.execute("SELECT 1 FROM options_shadow WHERE wave_trade_id=?", (wave_trade_id,)).fetchone()
    c.close(); return hit is not None


def record_plan(wt: dict, plan: dict):
    now = (dt.datetime.now(ET) if ET else dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    status = "OPEN" if plan["gate"] == "PASS" else "SKIPPED"
    c = sqlite3.connect(DB)
    c.execute("""INSERT OR IGNORE INTO options_shadow(
        wave_trade_id,symbol,engine,planned_at,entry_date,exit_on_date,strategy,kind,iv_rank,dte,
        expiry,long_strike,short_strike,net_debit,max_loss,max_profit,breakeven,breakeven_pct,
        liq_cost_pct,ev_roi,ev_p_win,gate,gate_reason,underlying_entry,shares_entry_price,status,
        mode,raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (wt["id"], plan["symbol"], ENGINE, now, wt.get("entry_date"), wt.get("exit_on_date"),
         plan.get("strategy"), plan.get("kind"), plan.get("iv_rank"), plan.get("dte"),
         plan.get("expiry"), plan.get("long_strike"), plan.get("short_strike"), plan.get("net_debit"),
         plan.get("max_loss_$"), plan.get("max_profit_$"), plan.get("breakeven"),
         plan.get("breakeven_pct"), plan.get("liq_cost_pct"), plan.get("ev_roi"), plan.get("ev_p_win"),
         plan["gate"], plan["gate_reason"], plan.get("underlying_entry"), wt.get("entry_price"),
         status, MODE, json.dumps(plan)))
    c.commit(); c.close()
    log(f"{plan['gate']:4} {plan['symbol']:<6} {plan.get('strategy') or '-':<18} {plan['gate_reason']}")


# ─────────────────────── live chain mark (SHADOW) ───────────────────────
def _leg_mid(symbol: str, expiry: str, right: str, strike: float):
    import options_trader as ot
    try:
        df = ot._yf_option_chain(symbol, expiry, right)
        if df is None or len(df) == 0:
            return None
        row = df.iloc[(df["strike"] - strike).abs().argmin()]
        bid, ask = float(row.get("bid") or 0), float(row.get("ask") or 0)
        if ask <= 0:
            return None
        return (bid + ask) / 2.0
    except Exception:
        return None


def mark_open():
    """Mark each OPEN shadow structure to the live option chain; close when its Wave Rider
    parent has closed (mirror the engine's exit). SHADOW only."""
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    opens = [dict(r) for r in c.execute("SELECT * FROM options_shadow WHERE status='OPEN'")]
    parents = {r["id"]: dict(r) for r in
               c.execute("SELECT * FROM wave_trades WHERE id IN (%s)" %
                         ",".join(str(o["wave_trade_id"]) for o in opens))} if opens else {}
    c.close()
    today = (dt.datetime.now(ET) if ET else dt.datetime.now()).strftime("%Y-%m-%d")
    for o in opens:
        right = "P" if o["kind"] == "CREDIT_PUT" else "C"
        long_mid  = _leg_mid(o["symbol"], o["expiry"], right, o["long_strike"])
        short_mid = _leg_mid(o["symbol"], o["expiry"], right, o["short_strike"])
        if long_mid is None or short_mid is None:
            continue
        if o["kind"] == "DEBIT_CALL":
            spread_val = long_mid - short_mid                 # what we could sell the spread for now
            entry_cost = (o["max_loss"] or 0) / 100.0         # debit paid per share
            unreal_ps  = spread_val - entry_cost
            ret_pct    = unreal_ps / entry_cost * 100 if entry_cost else 0
        else:                                                 # CREDIT_PUT: cost to close = hi put - lo put
            hi_put = _leg_mid(o["symbol"], o["expiry"], "P", max(o["long_strike"], o["short_strike"]))
            lo_put = _leg_mid(o["symbol"], o["expiry"], "P", min(o["long_strike"], o["short_strike"]))
            if hi_put is None or lo_put is None:
                continue
            close_val  = max(hi_put - lo_put, 0.0)
            credit     = (o["max_profit"] or 0) / 100.0       # credit received per share
            risk       = max(abs(o["short_strike"] - o["long_strike"]) - credit, 0.01)
            unreal_ps  = credit - close_val
            ret_pct    = unreal_ps / risk * 100
        parent = parents.get(o["wave_trade_id"], {})
        parent_closed = parent.get("status") == "CLOSED"
        c = sqlite3.connect(DB)
        if parent_closed:
            shares_ret = parent.get("pnl_pct")
            # ⚠ ROUGH PROXY (refine before trusting): option %-return is on premium (a small base),
            # shares %-return is on full notional — different denominators, so this pp gap OVERSTATES
            # options' benefit (leverage inflates the %). The honest "should options exist" number is
            # equal-RISK dollar P&L (size the option so max_loss = the shares' 8% risk, compare $).
            # v2 refinement — for now this is a directional shadow signal only, not a verdict.
            lev_prem = (ret_pct - shares_ret) if shares_ret is not None else None
            c.execute("""UPDATE options_shadow SET status='CLOSED',exit_date=?,exit_value=?,
                struct_ret_pct=?,shares_ret_pct=?,leverage_premium=?,mark_date=?,mark_unreal=? WHERE id=?""",
                (today, round((long_mid - short_mid), 3) if o["kind"] == "DEBIT_CALL" else None,
                 round(ret_pct, 2), shares_ret, round(lev_prem, 2) if lev_prem is not None else None,
                 today, round(unreal_ps * 100, 2), o["id"]))
            log(f"CLOSE {o['symbol']} struct {ret_pct:+.0f}% vs shares {shares_ret}% → lev-prem "
                f"{('%+.0f' % lev_prem) if lev_prem is not None else 'n/a'}pp")
        else:
            c.execute("UPDATE options_shadow SET mark_date=?,mark_value=?,mark_unreal=? WHERE id=?",
                      (today, round(unreal_ps * 100, 2) + (o["max_loss"] or 0), round(unreal_ps * 100, 2), o["id"]))
        c.commit(); c.close()


# ─────────────────────── main pass ───────────────────────
def new_wave_tickets() -> list[dict]:
    """Wave Rider tickets not yet turbo-processed (any status — we plan at entry time)."""
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM wave_trades WHERE id NOT IN (SELECT wave_trade_id FROM options_shadow "
        "WHERE wave_trade_id IS NOT NULL)")]
    c.close(); return rows


def run_once():
    init_db()
    n = dt.datetime.now(ET) if ET else dt.datetime.now()
    is_mkt = n.weekday() < 5 and n.strftime("%Y-%m-%d") not in US_HOLIDAYS
    # Plan any brand-new Wave Rider tickets (shadow-on-shadow)
    planned = 0
    for wt in new_wave_tickets():
        try:
            plan = plan_for_symbol(wt["symbol"])
            record_plan(wt, plan); planned += 1
        except Exception as e:
            log(f"plan error {wt.get('symbol')}: {e}")
    # Mark open structures (needs live chain — only during/after market)
    if is_mkt and 9 <= n.hour <= 20:
        try:
            mark_open()
        except Exception as e:
            log(f"mark error: {e}")
    log(f"pass complete — {planned} new ticket(s) planned")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--plan":
        init_db()
        p = plan_for_symbol(sys.argv[2])
        print(json.dumps(p, indent=2, default=str))
    else:
        run_once()
