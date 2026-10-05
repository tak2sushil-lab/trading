"""R&D BENCH — Fibonacci retracement entries through the Proving Ground (Oct 4 2026).

Hypothesis (practitioner, never tested here — docs/RESEARCH_REGISTRY.md D2): after a genuine up-swing,
a pullback into the 38.2-61.8% retracement zone is a buyable dip; the trend resumes over the next days.

Rule: on day t, the stock's last-W-day swing is an UPSWING (the W-day high came after the W-day low), the
swing is at least MIN_SWING daily ATRs, and today's CLOSE sits 38.2-61.8% of the way back down it.
Buy at t's close, hold H trading days. Scored by the factory's own qc_dyno.evaluate on market-neutral
alpha (return minus the universe's mean return over the same H days) with its 7 checks + spendable return.

Data: research_out/ml_panel.parquet (split-adjusted daily bars from bars_5m; research_ml_panel.py) — the
factory's own daily_close.csv is NOT split-adjusted, so it is not used here.
Neighbours for the robustness check: W ∈ {20, 60} × MIN_SWING ∈ {2, 3, 4} × H ∈ {3, 5, 10}.

Run: venv/bin/python -m factory.research.fib_retracement
"""
from __future__ import annotations
import os
from dataclasses import replace
import numpy as np
import pandas as pd

from factory.contracts import Engine, EngineSpec, TRADE_COLUMNS
from factory import qc_dyno as QC, data as D, engines as E

PANEL = '/Users/sushil/trading/research_out/ml_panel.parquet'
_PANEL = None


def panel():
    global _PANEL
    if _PANEL is None:
        p = pd.read_parquet(PANEL)
        p = p[p.close >= 5].copy()
        for h in (3, 5, 10):                      # the tide: universe mean forward return per day
            p[f'tide_r{h}'] = p.groupby('date')[f'y_r{h}'].transform('mean')
        _PANEL = p
    return _PANEL


class FibRetracement(Engine):
    window = 20
    min_swing = 3.0

    def __init__(self, window=20, min_swing=3.0, hold=5):
        self.window, self.min_swing = window, min_swing
        super().__init__(EngineSpec(
            name=f"fib_retr_w{window}_s{min_swing:g}", nickname="Golden Pullback",
            hypothesis="After a real up-swing, a 38.2-61.8% pullback is a buyable dip; the trend resumes.",
            side="LONG", hold_days=hold, stop_pct=0.0, personality="ANY", direction="UP"))

    def run(self, events=None, tide=None) -> pd.DataFrame:
        p = panel(); h = self.spec.hold_days; w = self.window
        sel = p[(p[f'fib_zone{w}_up'] == 1) & (p[f'swing{w}_size_atr'] >= self.min_swing)]
        sel = sel.dropna(subset=[f'y_r{h}', f'tide_r{h}'])
        ret = sel[f'y_r{h}'] * 100
        tide_v = sel[f'tide_r{h}'] * 100
        df = pd.DataFrame({'engine': self.spec.name, 'date': sel['date'].values, 'symbol': sel['symbol'].values,
                           'side': 'LONG', 'cluster': sel['dna'].values, 'day_chg': (sel['r1'] * 100).values,
                           'hold_days': h, 'ret': ret.values, 'tide': tide_v.values,
                           'alpha': (ret - tide_v).values, 'stopped': 0})
        return df[TRADE_COLUMNS]

    def neighbors(self):
        return [FibRetracement(w, s, h) for w in (20, 60) for s in (2.0, 3.0, 4.0) for h in (3, 5, 10)]


def main():
    events, tide, _ = D.load_dataset()
    roster = {}
    for nm in ('momentum_wild', 'xsec_reversal'):
        try:
            roster[nm] = QC.daily_alpha_of(E.get_engine(nm), events, tide)
        except Exception as e:
            print(f'  roster {nm} unavailable: {e}')
    print(f'roster for the correlation check: {list(roster)}')
    for w in (20, 60):
        for s in (2.0, 3.0):
            for h in (3, 5, 10):
                eng = FibRetracement(w, s, h)
                sc = QC.evaluate(eng, events, tide, roster)
                st = sc.stats
                print(f"\n{eng.spec.name} hold {h}d — n={st.get('n_trades')}  "
                      f"alpha IS {st.get('alpha_IS', np.nan):+.3f} OOS1 {st.get('alpha_OOS1', np.nan):+.3f} "
                      f"OOS2 {st.get('alpha_OOS2', np.nan):+.3f}  t={st.get('tstat', np.nan):.2f}")
                print(sc.render())


if __name__ == '__main__':
    main()
