"""R&D BENCH — Clockwork v3: does 15:40 "closing strength" improve the overnight picks? (Oct 4 2026)

Lead from the indicator scan (docs/RESEARCH_REGISTRY.md D4): names that finish the day strong (close near
the day's high, above VWAP, rallying in the last 30 min) earn LESS overnight — IC −0.035, t ≈ −4.2…−4.5,
3/4 half-years. Clockwork (factory/live/overnight.py) ranks WILD names only by 30-night up-gap
consistency and never looks at how a name is closing.

Honest construction:
  * Clockwork decides at 15:40-15:49 (MOC orders before 15:50), so closing strength is measured at
    15:40 from 5-min bars: price = close of the 15:35 bar; day high/low/VWAP through 15:40; the
    15:10→15:40 move. No 15:40-16:00 information is used.
  * Entry at the 16:00 close (MOC), exit at the next session's open (MOO). Same universe as live:
    WILD names (factory personality.csv), price $5-800.
  * Splits back-adjusted (bars_5m is not split-adjusted).
  * Arms, 3 names/night like live:
      live      on_cons30 counted through YESTERDAY's open (live lag), ties alphabetical (live behaviour)
      cons      on_cons30 incl. today's gap (the factory backtest's definition)
      cons_weak top-3 by cons among names whose 15:40 strength is at/below the WILD median
      combo     rank(cons) − 0.5·rank(strength)
      weak_only lowest 15:40 strength (no consistency)
      random    mean of 20 random 3-name draws (the benchmark the Sep 20 notes asked for)
Reports raw overnight return and excess vs the WILD mean, per half-year, gross and net of 0.05%/0.10%.

Run: venv/bin/python -m factory.research.clockwork_v3
"""
from __future__ import annotations
import sys, os
import numpy as np, pandas as pd

sys.path.insert(0, '/Users/sushil/trading')
from collect_bars import load_bars  # noqa: E402
from research_ml_panel import _split_adjust  # noqa: E402

CACHE = '/Users/sushil/trading/factory/cache'
OUT = '/Users/sushil/trading/research_out/clockwork_v3_panel.parquet'


def build():
    pe = pd.read_csv(os.path.join(CACHE, 'personality.csv'))
    wild = pe[pe.cluster == 'WILD'].symbol.tolist()
    end = (pd.Timestamp.today() + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    rows = []
    for s in wild:
        b = load_bars(s, start='2024-01-01', end=end)
        if b is None or len(b) < 3000:
            continue
        b = b.between_time('09:30', '15:55').copy()
        b['d'] = b.index.normalize().tz_localize(None)
        b['tpv'] = (b.high + b.low + b.close) / 3 * b.volume
        mins = b.index.hour * 60 + b.index.minute
        g = b.groupby('d')
        d = pd.DataFrame({'open': g.open.first(), 'high': g.high.max(), 'low': g.low.min(),
                          'close': g.close.last(), 'volume': g.volume.sum().astype(float), 'n': g.close.size()})
        d = d[d.n >= 70]
        d['vwap'] = g.tpv.sum() / g.volume.sum().replace(0, np.nan)
        e = b[mins <= 935]                                   # bars that START at or before 15:35
        ge = e.groupby('d')
        d['px1540'] = ge.close.last()
        d['hi1540'] = ge.high.max(); d['lo1540'] = ge.low.min()
        d['vwap1540'] = ge.tpv.sum() / ge.volume.sum().replace(0, np.nan)
        d['px1510'] = b[mins == 905].groupby('d').close.last()  # close of the 15:05 bar = price at 15:10
        d = _split_adjust(d.drop(columns='n'))
        d['on'] = d.open / d.close.shift(1) - 1
        d['y_on'] = d.open.shift(-1) / d.close - 1               # enter 16:00 close, exit next open
        d['cons_live'] = (d.on.shift(1) > 0).rolling(30).mean()  # through YESTERDAY's open (live lag)
        d['cons_bt'] = (d.on > 0).rolling(30).mean()             # through TODAY's open (backtest)
        rng = (d.hi1540 - d.lo1540).replace(0, np.nan)
        d['clv1540'] = (d.px1540 - d.lo1540) / rng
        d['vwapdist1540'] = d.px1540 / d.vwap1540 - 1
        d['ret_1510_1540'] = d.px1540 / d.px1510 - 1
        d['symbol'] = s
        rows.append(d.reset_index().rename(columns={'d': 'date'}))
    p = pd.concat(rows, ignore_index=True)
    p = p[(p.px1540 >= 5) & (p.px1540 <= 800)]
    g = p.groupby('date')
    p['strength'] = (g.clv1540.rank(pct=True) + g.vwapdist1540.rank(pct=True) + g.ret_1510_1540.rank(pct=True)) / 3
    p['strength_rk'] = p.groupby('date').strength.rank(pct=True)
    p.to_parquet(OUT, index=False)
    return p


def pick(day, key, n=3, ascending=False, mask=None, tiebreak_alpha=False):
    x = day if mask is None else day[mask]
    x = x.dropna(subset=[key, 'y_on'])
    if tiebreak_alpha:
        x = x.sort_values([key, 'symbol'], ascending=[ascending, True])
    else:
        x = x.sort_values(key, ascending=ascending)
    return x.head(n)


def evaluate(p):
    p = p.dropna(subset=['y_on', 'cons_bt', 'strength'])
    p = p[p.date >= '2024-03-01']
    p['combo'] = p.groupby('date').cons_bt.rank(pct=True) - 0.5 * p.strength_rk
    wild_mean = p.groupby('date').y_on.mean()
    arms = {}
    rng = np.random.default_rng(7)
    for day, dd in p.groupby('date'):
        if len(dd) < 15:
            continue
        sel = {
            'live': pick(dd, 'cons_live', tiebreak_alpha=True),
            'cons': pick(dd, 'cons_bt', tiebreak_alpha=True),
            'cons_weak': pick(dd, 'cons_bt', mask=dd.strength_rk <= 0.5, tiebreak_alpha=True),
            'combo': pick(dd, 'combo'),
            'weak_only': pick(dd, 'strength', ascending=True),
        }
        for k, v in sel.items():
            arms.setdefault(k, []).append((day, v.y_on.mean(), len(v)))
        rnd = np.mean([dd.y_on.iloc[rng.choice(len(dd), 3, replace=False)].mean() for _ in range(20)])
        arms.setdefault('random', []).append((day, rnd, 3))
    print(f"WILD overnight panel: {p.symbol.nunique()} names, {p.date.nunique()} nights "
          f"{p.date.min().date()} → {p.date.max().date()}\n")
    res = []
    for k, v in arms.items():
        s = pd.Series({d: r for d, r, _ in v}) * 100
        exc = s - wild_mean.reindex(s.index) * 100
        half = s.groupby(lambda d: f"{d.year}{'H1' if d.month <= 6 else 'H2'}").mean()
        hex_ = exc.groupby(lambda d: f"{d.year}{'H1' if d.month <= 6 else 'H2'}").mean()
        res.append(dict(arm=k, nights=len(s), raw_bp=s.mean() * 100, excess_bp=exc.mean() * 100,
                        t_excess=exc.mean() / (exc.std() / np.sqrt(len(exc))),
                        net05_bp=(s.mean() - 0.05) * 100, net10_bp=(s.mean() - 0.10) * 100,
                        sharpe_net05=(s - 0.05).mean() / (s - 0.05).std() * np.sqrt(252),
                        worst_night_pct=s.min(),
                        halves_raw=' '.join(f'{x * 100:+.0f}' for x in half.values),
                        halves_excess=' '.join(f'{x * 100:+.0f}' for x in hex_.values)))
    r = pd.DataFrame(res)
    pd.set_option('display.width', 250)
    print(r.round(2).to_string(index=False))
    print("\nhalves = 2024H1(from Mar) 2024H2 2025H1 2025H2 2026H1 2026H2(to date), bp per night")
    return r


if __name__ == '__main__':
    p = pd.read_parquet(OUT) if ('--cached' in sys.argv and os.path.exists(OUT)) else build()
    evaluate(p)
