"""
TRADERROB LAB — "15M Rob Reversal" (TS.pptx slide 14, an NQ futures scalping course) tested
on our own MNQ history.

Motivation: user handed over a course deck (TS.pptx) studied outside this project and asked
whether any of it holds up against our data. This is the one piece with a fully mechanical,
objectively-stated rule set — no discretion, no "unmitigated zone" judgment calls — so it is
cheap and honest to replicate exactly and test, the same way every other candidate in this
program gets tested: full history, walk-forward, per-year, real friction.

RULES, taken verbatim from the deck (nothing tuned, nothing added):
  - 15-minute candles. Session 8:30am-1:15pm CST = 9:30am-2:15pm ET (entries only in this
    window; a still-open trade is allowed to run to the RTH close as a backstop — the deck
    doesn't say what happens to a trade still open at 1:15pm CST, so we use this program's
    own EOD convention rather than inventing one).
  - SHORT setup: candle[i-1] is up (green), candle[i] is down (red), candle[i] pokes a HIGHER
    HIGH than candle[i-1] but does NOT undercut candle[i-1]'s low (a failed upside push).
    Entry triggers when candle[i] CLOSES BELOW the 8-EMA (computed on 15m closes).
  - LONG setup: mirror image — down candle then an up candle that makes a LOWER LOW but does
    NOT exceed the down candle's high. Entry on close ABOVE the 8-EMA.
  - Fixed 35pt SL / 35pt TP (1:1 R:R). One position at a time (matches this book's own
    MAX_OPEN_TRADES=1 convention, and the course explicitly says "trading only one setup").
  - The course's "1-tick entry offset" is an execution nicety, immaterial at this resolution —
    entry priced at the signal bar's own close, same convention as every other lab in this
    program that isn't specifically studying fill mechanics.
  - No parameter sweep. This is a literal replication test of someone else's exact rules —
    tuning 35pts, the EMA span, or the retracement definition here would just be curve-fitting
    a course we didn't design, which defeats the point of testing it as given.

Run: venv/bin/python -m futures.factory.traderrob_lab
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

from futures.collect_bars import load_bars                              # noqa: E402
from futures.factory.bench import engine_stats, verdict, FRICTION_PER_CONTRACT  # noqa: E402

POINT_VALUE = 2.0          # MNQ, futures/instruments/MNQ.json
SL_PTS = 35.0
TP_PTS = 35.0
SESSION_START = _dt.time(9, 30)
ENTRY_CUTOFF = _dt.time(14, 15)     # 1:15pm CST
SESSION_END = _dt.time(15, 10)      # this program's own RTH_END / EOD backstop, not the course's

TRAIN_END = 2023


def _resample_15m(day: pd.DataFrame) -> pd.DataFrame:
    o = day['open'].resample('15min').first()
    h = day['high'].resample('15min').max()
    l = day['low'].resample('15min').min()
    c = day['close'].resample('15min').last()
    out = pd.concat([o, h, l, c], axis=1)
    out.columns = ['open', 'high', 'low', 'close']
    return out.dropna()


def run(start: str, end: str) -> pd.DataFrame:
    bars = load_bars('MNQ', start=start, end=end)
    dates = sorted(set(bars.index.date))
    trades = []

    for d in dates:
        day = bars[bars.index.date == d]
        day = day[(day.index.time >= SESSION_START) & (day.index.time <= SESSION_END)]
        if len(day) < 20:
            continue
        m15 = _resample_15m(day)
        if len(m15) < 10:
            continue
        m15['ema8'] = m15['close'].ewm(span=8, adjust=False).mean()

        pos = None
        for i in range(1, len(m15)):
            ts = m15.index[i]
            row, prev = m15.iloc[i], m15.iloc[i - 1]

            if pos is not None:
                hi, lo, cl = row['high'], row['low'], row['close']
                exit_px, reason = None, None
                if pos['side'] == 'LONG':
                    if lo <= pos['sl']:
                        exit_px, reason = pos['sl'], 'SL'
                    elif hi >= pos['tp']:
                        exit_px, reason = pos['tp'], 'TP'
                else:
                    if hi >= pos['sl']:
                        exit_px, reason = pos['sl'], 'SL'
                    elif lo <= pos['tp']:
                        exit_px, reason = pos['tp'], 'TP'
                if exit_px is None and ts.time() >= SESSION_END:
                    exit_px, reason = cl, 'EOD'
                if exit_px is not None:
                    pts = (exit_px - pos['entry']) if pos['side'] == 'LONG' else (pos['entry'] - exit_px)
                    trades.append(dict(
                        date=str(d), entry_time=pos['entry_time'], exit_time=str(ts.time())[:5],
                        side=pos['side'], entry=pos['entry'], exit=exit_px, pnl_pts=pts,
                        pnl=pts * POINT_VALUE - FRICTION_PER_CONTRACT, reason=reason))
                    pos = None
                continue

            if not (SESSION_START <= ts.time() <= ENTRY_CUTOFF):
                continue

            c1_up, c1_dn = prev['close'] > prev['open'], prev['close'] < prev['open']
            c2_up, c2_dn = row['close'] > row['open'], row['close'] < row['open']
            short_setup = c1_up and c2_dn and row['high'] > prev['high'] and row['low'] >= prev['low']
            long_setup = c1_dn and c2_up and row['low'] < prev['low'] and row['high'] <= prev['high']

            if short_setup and row['close'] < row['ema8']:
                entry = row['close']
                pos = dict(side='SHORT', entry=entry, sl=entry + SL_PTS, tp=entry - TP_PTS,
                           entry_time=str(ts.time())[:5])
            elif long_setup and row['close'] > row['ema8']:
                entry = row['close']
                pos = dict(side='LONG', entry=entry, sl=entry - SL_PTS, tp=entry + TP_PTS,
                           entry_time=str(ts.time())[:5])

        if pos is not None:
            # ran out of bars before an exit fired — force-close at the last available price
            last = m15.iloc[-1]
            pts = (last['close'] - pos['entry']) if pos['side'] == 'LONG' else (pos['entry'] - last['close'])
            trades.append(dict(
                date=str(d), entry_time=pos['entry_time'], exit_time=str(m15.index[-1].time())[:5],
                side=pos['side'], entry=pos['entry'], exit=last['close'], pnl_pts=pts,
                pnl=pts * POINT_VALUE - FRICTION_PER_CONTRACT, reason='EOD_FORCED'))

    return pd.DataFrame(trades)


def report(df: pd.DataFrame):
    print(f'\n{"="*96}')
    print('  TRADERROB 15M REVERSAL — replicated exactly, tested on MNQ 5.5yr history')
    print(f'{"="*96}')
    if len(df) == 0:
        print('  zero trades — the setup never fired. Check the pattern logic before concluding'
              '\n  anything about the strategy itself.')
        return

    yr = pd.to_datetime(df['date']).dt.year
    st = engine_stats(df)
    print(f'  n={st["n"]}  WR={st["wr"]:.0f}%  total=${st["total"]:+,.0f}  '
          f'avg=${st["avg"]:+.1f}/trade  Sharpe(daily)={st["sharpe"]:.2f}  '
          f'MaxDD=${st["dd"]:+,.0f}  verdict={verdict(st)}')

    print(f'\n  By side:')
    for side in ('LONG', 'SHORT'):
        g = df[df['side'] == side]
        if len(g) == 0:
            continue
        s = engine_stats(g)
        print(f'    {side:<6} n={s["n"]:<5} WR={s["wr"]:>4.0f}%  total=${s["total"]:>+9,.0f}  '
              f'avg=${s["avg"]:>+6.1f}')

    print(f'\n  By exit reason:')
    for r, g in df.groupby('reason'):
        print(f'    {r:<12} n={len(g):<5} total=${g["pnl"].sum():>+9,.0f}  '
              f'avg=${g["pnl"].mean():>+6.1f}')

    print(f'\n  Per year (the overfit/robustness test — must hold up in most years, not one):')
    tr, te = df[yr <= TRAIN_END], df[yr > TRAIN_END]
    for label, g in (('TRAIN 2021-23', tr), ('TEST 2024-26', te)):
        s = engine_stats(g)
        print(f'    {label:<14} n={s["n"]:<5} WR={s["wr"]:>4.0f}%  total=${s["total"]:>+9,.0f}  '
              f'avg=${s["avg"]:>+6.1f}')
    line = '    per year:     '
    for y in sorted(yr.unique()):
        g = df[yr == y]
        line += f'{y}:{g["pnl"].sum():+,.0f}({len(g)}t)  '
    print(line)

    freq = len(df) / max(1, len(set(df['date'])))
    days_span = (pd.to_datetime(df['date']).max() - pd.to_datetime(df['date']).min()).days
    print(f'\n  Frequency: {len(df)} trades over ~{days_span/365.25:.1f} years '
          f'({len(df)/(days_span/365.25):.0f}/yr, {freq:.2f} on days it traded).')
    print(f'  Course claim was "$1000/day, 3 trades/day" on full-size NQ (10x MNQ point value).')
    print(f'  On MNQ 1-lot the realistic frequency and per-trade $ above is what our own tape')
    print(f'  actually supports — compare against that claim, not the course\'s framing.\n')


def main():
    df = run('2021-01-04', '2026-08-15')
    out = os.path.join(ROOT, 'futures', 'factory', '_traderrob_trades.csv')
    df.to_csv(out, index=False)
    print(f'  saved {len(df)} trades -> {out}')
    report(df)


if __name__ == '__main__':
    main()
