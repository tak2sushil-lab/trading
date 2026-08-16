"""
The Fill Desk (Execution) — runs the whole fleet as ONE realistic portfolio and reports
what a business actually cares about: the equity curve, its steadiness (Sharpe), and its
worst fall (drawdown).

Realistic on purpose:
  - discrete slots (fixed $/slot), so capital is genuinely tied up while a trade is held
  - the Captain's weights become per-engine slot caps (priority to higher-conviction engines)
  - the Captain's throttle shrinks the active slot count when the tide is out
  - round-trip costs on every fill
P&L is booked on the EXIT day (realised-equity curve) — transient intra-hold marks are not
counted, which is the honest basis for a business's drawdown.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
import pandas as pd

from factory.risk_brain import RiskBrain


@dataclass
class PortfolioResult:
    equity: pd.Series
    trades: int
    win_rate: float
    per_engine: dict
    stats: dict
    weights: dict

    def render(self) -> str:
        s = self.stats
        lines = [
            "╔═ THE FLEET — portfolio result " + "═" * 20,
            f"  Capital {s['capital0']:,.0f} → {s['capital1']:,.0f}   "
            f"({s['total_return']:+.1f}% over {s['years']:.1f}y, CAGR {s['cagr']:+.1f}%)",
            f"  Sharpe {s['sharpe']:.2f}   Sortino {s['sortino']:.2f}   "
            f"MaxDD {s['maxdd']:.1f}%   Calmar {s['calmar']:.2f}",
            f"  Trades {self.trades}   Win% {self.win_rate:.0f}   "
            f"Avg $/trade {s['avg_trade']:+.1f}",
            "  Allocation + P&L by engine:",
        ]
        for nm, pe in self.per_engine.items():
            lines.append(f"    {nm:16} weight {self.weights.get(nm,0):.0%}  "
                         f"trades {pe['n']:>4}  P&L ${pe['pnl']:>8,.0f}  win {pe['win']:.0f}%")
        lines.append("╚" + "═" * 50)
        return "\n".join(lines)


def _trading_days(tide: pd.DataFrame) -> list:
    # return pd.Timestamp objects so all downstream date lookups are the same type
    return [pd.Timestamp(d) for d in sorted(pd.to_datetime(tide["date"]).unique())]


def run_fleet(roster_trades: dict[str, pd.DataFrame], tide: pd.DataFrame,
              brain: RiskBrain | None = None, capital: float = 10_000.0,
              slots: int = 5, cost: float = 2.0, basis: str = "ret") -> PortfolioResult:
    """basis='ret' = raw P&L (includes the market tide). basis='alpha' = skill-only,
    market-neutral P&L (strips the bull-market lift — the honest, repeatable number)."""
    brain = brain or RiskBrain()
    weights = brain.weights(roster_trades)
    throttle = brain.throttle(tide)
    cal = _trading_days(tide)
    cal_idx = {d: i for i, d in enumerate(cal)}
    per_slot = capital / slots

    # combine all engines' trades, indexed by entry date
    allt = []
    for nm, tr in roster_trades.items():
        x = tr.copy(); x["engine"] = nm
        allt.append(x)
    allt = pd.concat(allt, ignore_index=True)
    allt["date"] = pd.to_datetime(allt["date"])
    by_date = {d: g for d, g in allt.groupby("date")}

    open_pos = []          # list of dicts: exit_i, pnl, engine, win
    booked = {d: 0.0 for d in cal}     # realised P&L per exit day
    per_engine = {nm: {"n": 0, "pnl": 0.0, "wins": 0} for nm in roster_trades}
    n_trades = 0

    for i, day in enumerate(cal):
        # 1. free slots whose holds have expired (exit today) — book their P&L
        still = []
        for p in open_pos:
            if p["exit_i"] <= i:
                booked[day] += p["pnl"]
            else:
                still.append(p)
        open_pos = still
        # 2. how many slots are live today (throttle)
        thr = float(throttle.get(day, 1.0)) if day in throttle.index else 1.0
        eff_slots = max(1, int(round(slots * thr)))
        open_by_eng = {}
        for p in open_pos:
            open_by_eng[p["engine"]] = open_by_eng.get(p["engine"], 0) + 1
        free = eff_slots - len(open_pos)
        if free <= 0 or day not in by_date:
            continue
        # 3. per-engine caps from weights; fill free slots, higher-weight engines first
        caps = {nm: max(1, int(round(eff_slots * weights.get(nm, 0)))) for nm in roster_trades}
        cands = by_date[day]
        # priority: engine weight desc, then bigger move first
        order = sorted(roster_trades.keys(), key=lambda n: -weights.get(n, 0))
        for nm in order:
            if free <= 0:
                break
            room = caps.get(nm, 0) - open_by_eng.get(nm, 0)
            if room <= 0:
                continue
            eng_c = cands[cands["engine"] == nm]
            # Prioritise by the ENTRY-TIME move size (causal), never by realised outcome.
            eng_c = eng_c.reindex(eng_c["day_chg"].abs().sort_values(ascending=False).index)
            for _, row in eng_c.iterrows():
                if free <= 0 or room <= 0:
                    break
                pnl = row[basis] / 100.0 * per_slot - cost
                exit_i = min(i + int(row["hold_days"]), len(cal) - 1)
                open_pos.append({"exit_i": exit_i, "pnl": pnl, "engine": nm, "win": pnl > 0})
                per_engine[nm]["n"] += 1
                per_engine[nm]["pnl"] += pnl
                per_engine[nm]["wins"] += int(pnl > 0)
                n_trades += 1
                free -= 1
                room -= 1
    # book any still-open at the end
    for p in open_pos:
        booked[cal[-1]] += p["pnl"]

    daily_pnl = pd.Series([booked[d] for d in cal], index=pd.to_datetime(cal))
    equity = capital + daily_pnl.cumsum()
    rets = equity.pct_change().dropna()
    years = max((cal[-1] - cal[0]).days / 365.25, 0.1)
    dd = (equity / equity.cummax() - 1.0)
    downside = rets[rets < 0]
    stats = {
        "capital0": capital, "capital1": float(equity.iloc[-1]),
        "total_return": float(equity.iloc[-1] / capital - 1) * 100,
        "years": years,
        "cagr": (float(equity.iloc[-1] / capital) ** (1 / years) - 1) * 100,
        "sharpe": float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0,
        "sortino": float(rets.mean() / downside.std() * np.sqrt(252)) if len(downside) > 1 and downside.std() > 0 else 0.0,
        "maxdd": float(dd.min()) * 100,
        "avg_trade": float(daily_pnl.sum() / n_trades) if n_trades else 0.0,
    }
    stats["calmar"] = stats["cagr"] / abs(stats["maxdd"]) if stats["maxdd"] != 0 else 0.0
    pe_out = {nm: {"n": v["n"], "pnl": v["pnl"],
                   "win": 100 * v["wins"] / v["n"] if v["n"] else 0} for nm, v in per_engine.items()}
    win_rate = 100 * sum(v["wins"] for v in per_engine.values()) / n_trades if n_trades else 0
    return PortfolioResult(equity=equity, trades=n_trades, win_rate=win_rate,
                           per_engine=pe_out, stats=stats, weights=weights)
