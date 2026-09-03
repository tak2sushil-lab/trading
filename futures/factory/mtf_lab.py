"""
MTF LAB — "would another timeframe have told us to flip short, or to wait for the pullback?"

Motivation (user, Aug 18 2026): the book loses a few trades BADLY (62 trades worse than
−$400 carry 52% of all loss). The ask is for a lever, available AT ENTRY, that separates
"go long now" from "flip short" or "wait for a pullback first".

The book already checks ONE higher timeframe (30-min HTF agreement). This lab reconstructs,
for every trade in the 5.5yr sim, the state of a LADDER of timeframes plus the entry's
micro-structure, and asks of each candidate lever the only two questions that matter:

  1. does it separate the BIG losers from everything else?
  2. does it survive as a FILTER on the whole book, PER YEAR?

(2) is the graveyard of this codebase: A_EXT, GRADE-for-LONG, H6-intraday-hero, four chop
detectors and the 11am block all looked right in isolation and died in the full pipeline.
A lever only counts here if it is positive in a majority of years INCLUDING a bad one.

Levers tested
  HTF-up   : weekly (5d/10d) trend, daily MA20+MA50 double-confirm, daily MA slope
  HTF-down : 30-min already live; 15-min alignment at entry
  MICRO    : extension at entry (distance from session VWAP / from the 5m EMA, in ATR)
             — this is the literal "did we buy the extension instead of the pullback"
  FLIP     : does any lever actually predict the OPPOSITE direction was right?

Run: venv/bin/python -m futures.factory.mtf_lab
"""
from __future__ import annotations

import datetime as _dt
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from futures.collect_bars import load_bars           # noqa: E402
from futures.factory.conditions import _sess         # noqa: E402

TRADES = os.path.join(ROOT, 'futures', 'factory', '_mom_2021-06-01_2026-08-14.csv')
DAYTBL = os.path.join(ROOT, 'futures', 'factory', '_day_table.csv')
BIG_LOSS = -400.0


def build():
    """Reconstruct entry-time multi-timeframe state for every trade."""
    d = pd.read_csv(TRADES)
    d['dts'] = d['date'].astype(str)
    bars = load_bars('MNQ', start='2021-01-01', end='2026-08-15')
    rth = _sess(bars, _dt.time(9, 30), _dt.time(15, 10))

    # daily frame (own bars) for the HTF ladder
    daily = rth.groupby(rth.index.date).agg(h=('high', 'max'), l=('low', 'min'),
                                            c=('close', 'last'))
    daily.index = pd.to_datetime(list(daily.index))
    for n in (10, 20, 50, 200):
        daily[f'ma{n}'] = daily['c'].rolling(n, min_periods=n).mean()
    daily['ma50_slope'] = daily['ma50'] - daily['ma50'].shift(5)
    daily['ma20_slope'] = daily['ma20'] - daily['ma20'].shift(3)
    daily['wk_ret'] = daily['c'] / daily['c'].shift(5) - 1.0        # weekly trend
    daily['wk2_ret'] = daily['c'] / daily['c'].shift(10) - 1.0
    # shift ALL daily state by one session — only completed history is knowable at entry
    dprev = daily.shift(1)

    day_map = {str(k): v.sort_index() for k, v in rth.groupby(rth.index.date)}
    dtbl = pd.read_csv(DAYTBL).set_index('date')

    rows = []
    for _, t in d.iterrows():
        day = day_map.get(t['dts'])
        if day is None:
            continue
        try:
            hh, mm = map(int, str(t['entry_time'])[:5].split(':'))
        except Exception:
            continue
        et = _dt.time(hh, mm)
        upto = day[day.index.time <= et]
        fwd = day[day.index.time > et]
        if len(upto) < 6 or len(fwd) < 2:
            continue
        ts = pd.Timestamp(t['dts'])
        if ts not in dprev.index:
            continue
        pv = dprev.loc[ts]
        atr = float(dtbl.loc[t['dts'], 'atr']) if t['dts'] in dtbl.index else np.nan
        if not np.isfinite(atr) or atr <= 0:
            continue

        e = float(t['entry'])
        sgn = 1 if t['side'] == 'LONG' else -1
        tp = (upto['high'] + upto['low'] + upto['close']) / 3.0
        vwap = float((tp * upto['volume']).cumsum().iloc[-1] /
                     max(upto['volume'].cumsum().iloc[-1], 1))
        ema9 = float(upto['close'].ewm(span=9, adjust=False).mean().iloc[-1])
        # 15-min alignment: last three 5-min closes trending our way
        c3 = upto['close'].tail(4).to_numpy()
        m15 = float(c3[-1] - c3[0]) if len(c3) >= 4 else np.nan
        # session position: where in TODAY's range are we entering?
        rng = max(float(upto['high'].max() - upto['low'].min()), 1.0)
        loc = (e - float(upto['low'].min())) / rng

        rows.append(dict(
            date=t['dts'], y=ts.year, side=t['side'], setup=t['setup'],
            contracts=int(t['contracts']), pnl=float(t['pnl']),
            reason=t['exit_reason'], entry_time=str(t['entry_time'])[:5],
            # ── HTF ladder (all from COMPLETED prior sessions) ──
            d_ma20=(float(pv['c']) - float(pv['ma20'])) / atr,
            d_ma50=(float(pv['c']) - float(pv['ma50'])) / atr,
            d_ma200=(float(pv['c']) - float(pv['ma200'])) / atr,
            ma50_slope=float(pv['ma50_slope']) / atr,
            ma20_slope=float(pv['ma20_slope']) / atr,
            wk_ret=float(pv['wk_ret']), wk2_ret=float(pv['wk2_ret']),
            # ── entry micro-structure ──
            ext_vwap=sgn * (e - vwap) / atr,      # >0 = entered EXTENDED past VWAP
            ext_ema9=sgn * (e - ema9) / atr,
            m15=sgn * m15 / atr,                  # >0 = last 15min moving our way
            sess_loc=loc if sgn > 0 else 1 - loc,  # 1.0 = entering at the extreme
            atr=atr,
        ))
    return pd.DataFrame(rows)


def _yr_line(g: pd.DataFrame, years) -> str:
    return ' '.join(f'{y}:{g.loc[g.y == y, "pnl"].sum():+,.0f}' for y in years)


def report(m: pd.DataFrame):
    years = sorted(m.y.unique())
    big = m[m.pnl <= BIG_LOSS]
    rest = m[m.pnl > BIG_LOSS]
    print(f'\n{"="*100}')
    print(f'  WHAT DID THE BIG LOSERS LOOK LIKE AT ENTRY?   (n={len(big)} trades ≤ ${BIG_LOSS:,.0f}, '
          f'{100*big.pnl.sum()/m.pnl[m.pnl<0].sum():.0f}% of all loss)')
    print(f'{"="*100}')
    print('  {:<14}{:>12}{:>12}{:>10}'.format('feature', 'big losers', 'everything else', 'gap'))
    print('  ' + '-' * 50)
    for f in ('d_ma20', 'd_ma50', 'd_ma200', 'ma50_slope', 'wk_ret', 'wk2_ret',
              'ext_vwap', 'ext_ema9', 'm15', 'sess_loc'):
        a, b = big[f].mean(), rest[f].mean()
        sd = m[f].std()
        gap = (a - b) / sd if sd else 0
        flag = '  ←' if abs(gap) > 0.25 else ''
        print(f'  {f:<14}{a:>12.3f}{b:>12.3f}{gap:>10.2f}{flag}')
    print('\n  (gap = difference in standard deviations. |gap| < 0.25 means the big losers were'
          '\n   indistinguishable from every other trade on that feature.)')

    print(f'\n{"="*100}')
    print('  CANDIDATE LEVERS AS PRE-ENTRY FILTERS  (must hold up PER YEAR, not just in total)')
    print(f'{"="*100}')
    base = m
    print('  {:<34}{:>6}{:>7}{:>10}{:>8}   {}'.format(
        'lever (keep only trades where…)', 'n', 'WR', 'total', 'big-L', 'per year'))
    print('  ' + '-' * 118)
    print('  {:<34}{:>6}{:>6.0f}%{:>+10,.0f}{:>8}   {}'.format(
        'BASELINE (no filter)', len(base), 100 * (base.pnl > 0).mean(),
        base.pnl.sum(), int((base.pnl <= BIG_LOSS).sum()), _yr_line(base, years)))

    levers = {
        'daily MA50 aligned':            m.d_ma50 > 0,
        'daily MA20 AND MA50 aligned':   (m.d_ma20 > 0) & (m.d_ma50 > 0),
        'daily MA50 rising (slope>0)':   m.ma50_slope > 0,
        'MA50 aligned AND rising':       (m.d_ma50 > 0) & (m.ma50_slope > 0),
        'weekly (5d) trend aligned':     m.wk_ret > 0,
        'weekly AND daily aligned':      (m.wk_ret > 0) & (m.d_ma50 > 0),
        '2-week (10d) trend aligned':    m.wk2_ret > 0,
        'last 15min moving our way':     m.m15 > 0,
        'NOT extended >1 ATR past VWAP': m.ext_vwap < 1.0,
        'NOT extended >0.5 ATR':         m.ext_vwap < 0.5,
        'pullback entry (below VWAP)':   m.ext_vwap < 0,
        'not at session extreme (<0.9)': m.sess_loc < 0.9,
        'daily+weekly+not-extended':     (m.d_ma50 > 0) & (m.wk_ret > 0) & (m.ext_vwap < 1.0),
    }
    for name, mask in levers.items():
        g = m[mask]
        if len(g) < 20:
            print(f'  {name:<34}   (too few trades: {len(g)})'); continue
        print('  {:<34}{:>6}{:>6.0f}%{:>+10,.0f}{:>8}   {}'.format(
            name, len(g), 100 * (g.pnl > 0).mean(), g.pnl.sum(),
            int((g.pnl <= BIG_LOSS).sum()), _yr_line(g, years)))

    print(f'\n{"="*100}')
    print('  THE FLIP QUESTION — when the book was wrong, would the OPPOSITE trade have paid?')
    print(f'{"="*100}')
    print('  Split every trade by whether it fought the daily trend, and score the trade')
    print('  it actually took vs the mirror-image trade in the same session:\n')
    for lbl, mask in (('WITH daily trend', m.d_ma50 > 0), ('AGAINST daily trend', m.d_ma50 <= 0)):
        g = m[mask]
        print(f'    {lbl:<22} n={len(g):4d}  taken ${g.pnl.sum():+8,.0f}  '
              f'mirrored ${-g.pnl.sum():+8,.0f}  WR {100*(g.pnl>0).mean():.0f}%')
    print('\n  ⚠️ "mirrored" is an upper bound only — flipping the side does NOT flip the stop,')
    print('     the trail, or the exit stack, so a real short book would not collect this.')
    print('     Read it as "was there money on the other side", not as a P&L estimate.\n')


def main():
    m = build()
    print(f'  reconstructed entry-time MTF state for {len(m)} trades')
    m.to_csv(os.path.join(ROOT, 'futures', 'factory', '_mtf_trades.csv'), index=False)
    report(m)


if __name__ == '__main__':
    main()
