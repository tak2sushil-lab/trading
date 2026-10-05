"""R&D BENCH — the learned overnight model as a factory engine, through the Proving Ground (Oct 4 2026).

Predictions come from research_ml_overnight_1540.py (walk-forward, re-fit monthly, decision-time honest:
features known by 15:40, entry at the 16:00 close, exit at the next open). Two forms:
  * "Night Owl" long-only: the top-N WILD names each night (N=5; neighbours N ∈ {3, 5, 8, 10})
  * "Night Owl sleeve": long the top decile / short the bottom decile of all names (neighbours 10%/20%)
Scored on market-neutral alpha (return minus the night's universe mean) with the factory's own 7 checks.
Note: every prediction is out of sample, so the gate's "IS" window (before 2025-07) is 2025-H1 out-of-
sample predictions, not an in-sample fit.

Run: venv/bin/python -m factory.research.ml_overnight_engine
"""
from __future__ import annotations
import numpy as np, pandas as pd
from factory.contracts import Engine, EngineSpec, TRADE_COLUMNS
from factory import qc_dyno as QC, data as D, engines as E

PREDS = '/Users/sushil/trading/research_out/ml_preds_overnight_1540.parquet'
_P = None


def preds():
    global _P
    if _P is None:
        p = pd.read_parquet(PREDS).dropna(subset=['model_1540_honest', 'y_on'])
        pe = pd.read_csv('/Users/sushil/trading/factory/cache/personality.csv')
        p = p.merge(pe[['symbol', 'cluster']], on='symbol', how='left')
        p['tide'] = p.groupby('date').y_on.transform('mean') * 100
        _P = p
    return _P


class NightOwl(Engine):
    def __init__(self, n=5, sleeve=False, decile=0.1):
        self.n, self.decile = n, decile
        super().__init__(EngineSpec(
            name=f"ml_overnight_{'sleeve' if sleeve else 'top' + str(n)}", nickname="Night Owl",
            hypothesis="A model of ~90 causal signals ranks tonight's overnight returns better than any one signal.",
            side="LONG", hold_days=1, stop_pct=0.0, personality="WILD" if not sleeve else "ANY",
            direction="XS", sleeve=sleeve))

    def run(self, events=None, tide=None) -> pd.DataFrame:
        p = preds(); rows = []
        if self.spec.sleeve:
            for d, g in p.groupby('date'):
                if len(g) < 30:
                    continue
                n = max(int(len(g) * self.decile), 5)
                o = g.sort_values('model_1540_honest')
                for _, r in o.tail(n).iterrows():
                    rows.append((d, r.symbol, 'LONG', r.y_on * 100, r.tide, r.y_on * 100 - r.tide))
                for _, r in o.head(n).iterrows():
                    rows.append((d, r.symbol, 'SHORT', -r.y_on * 100, r.tide, r.tide - r.y_on * 100))
        else:
            w = p[p.cluster == 'WILD']
            top = w.sort_values('model_1540_honest', ascending=False).groupby('date').head(self.n)
            rows = [(r.date, r.symbol, 'LONG', r.y_on * 100, r.tide, r.y_on * 100 - r.tide) for r in top.itertuples()]
        df = pd.DataFrame(rows, columns=['date', 'symbol', 'side', 'ret', 'tide', 'alpha'])
        df['engine'] = self.spec.name; df['cluster'] = 'ANY'; df['day_chg'] = 0.0
        df['hold_days'] = 1; df['stopped'] = 0
        return df[TRADE_COLUMNS]

    def neighbors(self):
        if self.spec.sleeve:
            return [NightOwl(sleeve=True, decile=d) for d in (0.1, 0.2)]
        return [NightOwl(n) for n in (3, 5, 8, 10)]


def main():
    events, tide, _ = D.load_dataset()
    roster = {}
    for nm in ('momentum_wild', 'xsec_reversal', 'xsec_overnight_consist'):
        try:
            roster[nm] = QC.daily_alpha_of(E.get_engine(nm), events, tide)
        except Exception as e:
            print(f'  roster {nm}: {e}')
    for eng in (NightOwl(5), NightOwl(3), NightOwl(sleeve=True)):
        sc = QC.evaluate(eng, events, tide, roster)
        print(f"\n{eng.spec.name}: n={sc.stats.get('n_trades')}  raw ret {sc.stats.get('ret_mean', float('nan')):+.3f}%/night")
        print(sc.render())


if __name__ == '__main__':
    main()
