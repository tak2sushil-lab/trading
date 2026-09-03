"""
REGIME-FLIP EXIT LAB  (Aug 24 2026)

THE GAP THIS ATTACKS (measured, not assumed).
Live exit ledger, 148 automated trades Jun 5 - Aug 21 2026:
    every adaptive exit is PROFITABLE   trail +$5,901 · rev_exit +$1,922 (100% WR)
                                        no_move +$519 · target/vwap +$366
    the entire loss sits in trades that never earned one:
                                        real stop -$5,988 · circuit breaker -$5,458
`monitor_open_trades(regime)` receives the live regime label and uses it ONLY to look up
trail parameters from the sticky 10:30 `_day_regime`. The live label never triggers an exit.
The one "it turned against me" exit (Reversal Exit) requires peak >= REV_EXIT_PEAK_MIN_PTS
(120pts) FIRST -- so a trade that never works has nothing watching it at all.

WHAT THIS LAB DOES
  1. Rebuilds the live `get_regime()` label per 5-min bar, causally (bars up to and
     including that bar only), from futures_bars_5m -- the SAME formula as
     futures_trader.get_regime / sim_replay.get_regime.
  2. Replays every cached sim trade against that series and asks: if we exit when the
     regime flips AGAINST the position and stays flipped for N closed bars, what happens?
  3. Reports PER YEAR, and separately for the "orphan" population (peak < 120pts) that
     currently has no adaptive exit at all.

METHOD HONESTY
  * This is a FRAME counterfactual: it re-exits existing trades, it does not re-run the
    entry pipeline, so freed slots are never refilled. Per this program's own rule, frame
    results are an UPPER BOUND (historically ~8-15% optimistic) and must be pipeline
    confirmed before shipping.
  * Exit price is the CLOSE of the confirming bar (no look-ahead: the decision is made on
    bars already closed, the fill is at the price available then).
  * A prior attempt at this idea ("thesis invalidation", Jul 7 2026) was shipped and
    REVERTED the same day. It voted on a regime read that was computed off the FORMING
    5-min bar; that bug was only fixed Aug 24 2026. This lab tests the idea on the
    corrected sensor.

Run: venv/bin/python -m futures.factory.regime_exit_lab
"""
from __future__ import annotations
import os, sqlite3, sys
import numpy as np
import pandas as pd

ROOT = '/Users/sushil/trading'
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
FAC = os.path.join(ROOT, 'futures', 'factory')
ET = 'America/New_York'
DOLLARS_PER_PT = 2.0
YRS = ['2021', '2022', '2023', '2024', '2025', '2026']


def _rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def build_regime_series(cache=os.path.join(FAC, '_regime_bars.csv')) -> pd.DataFrame:
    """Per-5-min-bar regime label over RTH, causal. Mirrors live get_regime()."""
    if os.path.exists(cache):
        d = pd.read_csv(cache, parse_dates=['ts'])
        d['ts'] = d.ts.dt.tz_localize(ET, ambiguous='NaT', nonexistent='NaT') \
            if d.ts.dt.tz is None else d.ts.dt.tz_convert(ET)
        return d
    con = sqlite3.connect(os.path.join(ROOT, 'market_data.db'))
    b = pd.read_sql_query(
        "SELECT ts_utc,open,high,low,close,volume FROM futures_bars_5m "
        "WHERE symbol='MNQ' ORDER BY ts_utc", con)
    b['ts'] = pd.to_datetime(b.ts_utc, format='mixed', utc=True).dt.tz_convert(ET)
    b = b.drop_duplicates('ts').set_index('ts').sort_index()
    b['day'] = b.index.date
    prev_close = b.groupby('day').close.last().shift(1)

    out = []
    for day, g in b.groupby('day'):
        rth = g.between_time('09:30', '15:55')
        if len(rth) < 6:
            continue
        pc = prev_close.get(day, np.nan)
        if not np.isfinite(pc):
            continue
        c = rth.close
        # cumulative session VWAP (same as live calc_vwap over the session)
        tp = (rth.high + rth.low + rth.close) / 3.0
        vwap = (tp * rth.volume).cumsum() / rth.volume.cumsum().replace(0, np.nan)
        rsi = _rsi(c)
        # session RVOL = this bar's volume / mean volume of today's bars so far
        rvol = rth.volume / rth.volume.expanding().mean().replace(0, np.nan)
        sess_open = float(rth.open.iloc[0])
        sess_chg = (c - sess_open) / sess_open * 100.0
        day_chg = (c - pc) / pc * 100.0
        trend_up = c > c.shift(4)          # last 5 bars: close[-1] vs close[-5]
        trend_dn = c < c.shift(4)
        # choppy: >40% sign flips in bar-to-bar change AND |session_chg| < 0.15
        dif = c.diff()
        flip = ((dif * dif.shift(1)) < 0).astype(float)
        flip_rate = flip.expanding().mean()
        choppy = (flip_rate > 0.4) & (sess_chg.abs() < 0.15)
        above_vwap = c > vwap

        reg = np.full(len(rth), 'NORMAL', dtype=object)
        strong = (above_vwap & trend_up & (sess_chg > 0.15) & (rsi < 80)).values
        weak = ((~above_vwap) & trend_dn & (day_chg < -0.3) & (rsi > 20)).values
        gate = (rvol.fillna(0) >= 0.65).values & (~choppy.fillna(False).values)
        reg[gate & strong] = 'STRONG'
        reg[gate & weak & ~strong] = 'WEAK'
        # 30-min HTF trend from COMPLETED 30-min bars (mirrors calc_htf_trend)
        # LOOK-AHEAD GUARD (Aug 24 2026): resample() labels a window by its START, so
        # the value stamped 10:30 is computed from data through 10:59. Shift the index
        # forward one full period so a 5-min bar can only ever see 30-min bars that have
        # already CLOSED. Same bug class that inflated the patient-engine lab 10x.
        h = rth.close.resample('30min').last().dropna()
        hdir = np.sign(h.diff(2))
        hdir.index = hdir.index + pd.Timedelta(minutes=30)
        hdir = hdir.reindex(rth.index, method='ffill').fillna(0.0)
        out.append(pd.DataFrame({'ts': rth.index, 'close': c.values, 'regime': reg,
                                 'vwap': vwap.values, 'htf': hdir.values}))
    d = pd.concat(out, ignore_index=True)
    d.to_csv(cache, index=False)
    return d


def load_trades(fname='_mom_2021-06-01_2026-08-14.csv') -> pd.DataFrame:
    t = pd.read_csv(os.path.join(FAC, fname))
    t['date'] = t.date.astype(str)
    t['year'] = t.date.str[:4]
    t['ent'] = pd.to_datetime(t.date + ' ' + t.entry_time.astype(str)).dt.tz_localize(ET)
    t['ext'] = pd.to_datetime(t.date + ' ' + t.exit_time.astype(str)).dt.tz_localize(ET)
    return t


def replay(trades: pd.DataFrame, reg: pd.DataFrame, confirm: int,
           only_orphans: bool = False, peak_floor: float = 120.0) -> pd.DataFrame:
    """Exit when regime is against the position for `confirm` consecutive closed bars."""
    r = reg.set_index('ts')
    rows = []
    for _, t in trades.iterrows():
        w = r.loc[(r.index > t.ent) & (r.index <= t.ext)]
        new_pnl, why = t.pnl, 'unchanged'
        if len(w):
            sgn = 1 if t.side == 'LONG' else -1
            peak = float((sgn * (w.close - t.entry)).max())
            if only_orphans and peak >= peak_floor:
                rows.append(dict(t, new_pnl=t.pnl, why='not-orphan', peak=peak)); continue
            against = 'WEAK' if t.side == 'LONG' else 'STRONG'
            streak = 0
            for ts, row in w.iterrows():
                streak = streak + 1 if row.regime == against else 0
                if streak >= confirm:
                    new_pnl = sgn * (float(row.close) - t.entry) * DOLLARS_PER_PT * t.contracts - 6 * max(1, t.contracts)
                    why = 'regime_flip'
                    break
            rows.append(dict(t, new_pnl=new_pnl, why=why, peak=peak))
        else:
            rows.append(dict(t, new_pnl=t.pnl, why='no-bars', peak=np.nan))
    return pd.DataFrame(rows)


def replay_vote(trades: pd.DataFrame, reg: pd.DataFrame, need_votes: int, confirm: int,
                only_orphans: bool = True, peak_floor: float = 120.0) -> pd.DataFrame:
    """Jul-7-style 'thesis invalidation': exit when >=need_votes of four signals are
    against the position for `confirm` consecutive CLOSED bars.
    Votes: (1) regime against  (2) price the wrong side of session VWAP
           (3) 30-min HTF trend against  (4) 2 consecutive adverse bar closes.
    Now tested on the CORRECTED regime sensor (forming-bar bug fixed Aug 24 2026)."""
    r = reg.set_index('ts')
    rows = []
    for _, t in trades.iterrows():
        w = r.loc[(r.index > t.ent) & (r.index <= t.ext)]
        new_pnl, why = t.pnl, 'unchanged'
        if len(w):
            sgn = 1 if t.side == 'LONG' else -1
            peak = float((sgn * (w.close - t.entry)).max())
            if only_orphans and peak >= peak_floor:
                rows.append(dict(t, new_pnl=t.pnl, why='not-orphan', peak=peak)); continue
            against_reg = 'WEAK' if t.side == 'LONG' else 'STRONG'
            streak = 0; adv = 0; prev_c = None
            for ts, row in w.iterrows():
                c = float(row.close)
                if prev_c is not None:
                    adv = adv + 1 if (sgn * (c - prev_c)) < 0 else 0
                prev_c = c
                v = 0
                v += 1 if row.regime == against_reg else 0
                v += 1 if (sgn * (c - float(row.vwap))) < 0 else 0
                v += 1 if (sgn * float(row.htf)) < 0 else 0
                v += 1 if adv >= 2 else 0
                streak = streak + 1 if v >= need_votes else 0
                if streak >= confirm:
                    new_pnl = sgn * (c - t.entry) * DOLLARS_PER_PT * t.contracts - 6 * max(1, t.contracts)
                    why = 'vote_exit'
                    break
            rows.append(dict(t, new_pnl=new_pnl, why=why, peak=peak))
        else:
            rows.append(dict(t, new_pnl=t.pnl, why=why, peak=np.nan))
    return pd.DataFrame(rows)


def summarise(df: pd.DataFrame, col: str, label: str):
    y = df.groupby('year')[col].sum().reindex(YRS).fillna(0)
    dd = df.groupby('date')[col].sum().sort_index()
    eq = dd.cumsum(); mdd = (eq - eq.cummax()).min()
    print(f"{label:42s} ${df[col].sum():8,.0f} DD={mdd:8,.0f} worst={dd.min():7,.0f} "
          f"green={(y > 0).sum()}/6  " + " ".join(f"{a[2:]}:{v:+6.0f}" for a, v in y.items()))


def main():
    reg = build_regime_series()
    print(f"regime series: {len(reg):,} RTH bars  "
          f"{reg.ts.min().date()} -> {reg.ts.max().date()}")
    print("  label mix: " + str((reg.regime.value_counts(normalize=True) * 100).round(1).to_dict()) + " %\n")
    t = load_trades()
    print(f"trades: {len(t)}  (live-config cache, friction already applied)\n")
    summarise(t.assign(base=t.pnl), 'base', 'LIVE EXIT STACK (baseline)')
    print()
    for confirm in (1, 2, 3, 4):
        out = replay(t, reg, confirm)
        n = int((out.why == 'regime_flip').sum())
        summarise(out, 'new_pnl', f'regime-flip exit, confirm={confirm} (fired {n:3d})')
    print("\n--- MULTI-SIGNAL VOTE (the Jul 7 'thesis invalidation' design, fixed sensor) ---")
    print("    votes: regime against | wrong side of VWAP | 30m HTF against | 2 adverse closes")
    for orph in (True, False):
        tag = 'ORPHANS only' if orph else 'ALL trades  '
        for need in (2, 3, 4):
            for confirm in (1, 2):
                out = replay_vote(t, reg, need, confirm, only_orphans=orph)
                n = int((out.why == 'vote_exit').sum())
                summarise(out, 'new_pnl', f'{tag} {need}-of-4 votes x{confirm}bar (fired {n:3d})')
        print()


if __name__ == '__main__':
    main()
