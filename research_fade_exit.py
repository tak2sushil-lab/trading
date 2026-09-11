"""
FADE EXIT — the angle that is switched off, and whether 1-min sees it sooner.

User, Sep 11 2026: "There should be another angle we must be already covering which tells that
it is going to fade soon or already faded and no point holding it back." They are right, and
the angle already exists — auto_trader has a VWAP-cross exit (rule 3: exit a profitable long
once it closes back below session VWAP). It is gated `and not holds_to_close(tid)`, so exactly
the trades that complain about give-back — the early entries, DELL and POET — have NO fade
detection whatsoever. Their only remaining exits are the 5% hard stop, the dollar circuit
breaker, a regime flip that requires the trade to already be LOSING, and the EOD close.

This tests two things the user asked for, on the hold-to-close population only:

  A. FADE  — exit on the first bar that closes below session VWAP while up >= +0.5%
             (the same rule the 10:00+ population already gets).
  B. TRAIL — the give-back trail, for comparison. Already known to be ~neutral.

and it runs A at BOTH 5-min and 1-min granularity, because the other question was whether a
finer check catches the fade sooner. 1-min bars exist only through 2026-08-04 (the collector
stopped), so the granularity comparison runs on the overlapping window and is reported with
its own n.

WHY THIS IS NOT THE ALREADY-REJECTED IDEA. Every previous attempt here shortened the trade
based on GIVE-BACK (price retraced X% from its peak) and lost money, because give-back is the
premium paid for the right tail. A fade exit is conditioned on STRUCTURE instead — price losing
the session's own volume-weighted average — which is a statement about the move being over,
not about how much has been handed back. Those are different claims and deserve separate tests.

Per-month reporting throughout: an aggregate carried by one month is the trap this repo keeps
falling into.

Run: venv/bin/python research_fade_exit.py
"""
from __future__ import annotations

import argparse
import sqlite3

import pandas as pd

from collect_bars import load_bars

DB = 'trades.db'
HOLD_TO_CLOSE_MINS = 10 * 60
MIN_PROFIT_TO_FADE = 0.5      # mirrors auto_trader rule 3: only fires when up >= +0.5%


def load_trades(start: str) -> pd.DataFrame:
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT id, symbol, entry_date, entry_time, entry_price, exit_time, shares, side, "
        "       pnl, pnl_pct, exit_reason FROM trades "
        "WHERE status IN ('WIN','LOSS') AND setup_type != 'RECONCILED' AND side='LONG' "
        f"AND entry_date >= '{start}' ORDER BY entry_date, entry_time", con)
    con.close()
    if df.empty:
        return df
    hm = df['entry_time'].astype(str).str.slice(0, 5).str.split(':', expand=True)
    df['mins'] = pd.to_numeric(hm[0], errors='coerce') * 60 + pd.to_numeric(hm[1], errors='coerce')
    df['hold_to_close'] = df['mins'] < HOLD_TO_CLOSE_MINS
    df['month'] = df['entry_date'].str.slice(0, 7)
    return df


_CACHE: dict = {}


def day_bars(sym: str, d: str, table: str = 'bars_5m'):
    """One session of bars, ET, from market_data.db. Queried directly rather than through
    collect_bars.load_bars because that helper is hard-wired to bars_5m and the granularity
    comparison needs bars_1m as well. Applies the same mixed-format/dedup handling the
    Aug 7 2026 timestamp fix established."""
    key = (sym, d, table)
    if key in _CACHE:
        return _CACHE[key]
    out = None
    try:
        con = sqlite3.connect('market_data.db')
        q = (f"SELECT ts_utc, open, high, low, close, volume FROM {table} "
             "WHERE symbol=? AND substr(ts_utc,1,10)=? ORDER BY ts_utc")
        b = pd.read_sql_query(q, con, params=(sym, d))
        con.close()
        if len(b):
            b['ts'] = pd.to_datetime(b['ts_utc'], utc=True, format='mixed')
            b = b.drop_duplicates(subset='ts', keep='last').set_index('ts')
            b.index = b.index.tz_convert('America/New_York')
            b = b[['open', 'high', 'low', 'close', 'volume']].sort_index()
            out = b if len(b) else None
    except Exception:
        out = None
    _CACHE[key] = out
    return out


def with_vwap(b: pd.DataFrame) -> pd.DataFrame:
    """Session VWAP, accumulated from the session's first bar — not from our entry."""
    b = b.copy()
    tp = (b['high'] + b['low'] + b['close']) / 3.0
    vol = b['volume'].replace(0, pd.NA).fillna(1)
    b['vwap'] = (tp * vol).cumsum() / vol.cumsum()
    return b


def _window(r, b):
    try:
        t0 = pd.Timestamp(f"{r['entry_date']} {str(r['entry_time'])[:8]}").tz_localize(b.index.tz)
    except Exception:
        return None
    t1 = None
    if r['exit_time']:
        try:
            t1 = pd.Timestamp(f"{r['entry_date']} {str(r['exit_time'])[:8]}").tz_localize(b.index.tz)
        except Exception:
            t1 = None
    w = b[b.index >= t0]
    if t1 is not None:
        w = w[w.index <= t1]
    return w if len(w) >= 2 else None


def fade_exit(r, table='bars_5m'):
    """% result if we exit on the first bar closing below session VWAP while up >= +0.5%."""
    b = day_bars(r['symbol'], r['entry_date'], table)
    if b is None:
        return None
    b = with_vwap(b)
    w = _window(r, b)
    if w is None:
        return None
    entry = float(r['entry_price'])
    for _, bar in w.iterrows():
        c = float(bar['close'])
        gain = (c - entry) / entry * 100
        if gain >= MIN_PROFIT_TO_FADE and pd.notna(bar['vwap']) and c < float(bar['vwap']):
            return gain
    return float(r['pnl_pct'])


def trail_exit(r, arm=3.0, gap=0.5, table='bars_5m'):
    b = day_bars(r['symbol'], r['entry_date'], table)
    if b is None:
        return None
    w = _window(r, b)
    if w is None:
        return None
    entry = float(r['entry_price']); high = entry
    for _, bar in w.iterrows():
        high = max(high, float(bar['high']))
        if (high - entry) / entry * 100 >= arm:
            stop = high * (1 - gap / 100)
            if float(bar['low']) <= stop:
                return (stop - entry) / entry * 100
    return float(r['pnl_pct'])


def score(df, fn, months, **kw):
    tot, n, per = 0.0, 0, {m: 0.0 for m in months}
    for _, r in df.iterrows():
        cf = fn(r, **kw)
        if cf is None:
            continue
        d = (cf - r['pnl_pct']) / 100 * float(r['entry_price']) * int(r['shares'])
        if abs(d) > 1e-9:
            n += 1
        tot += d
        per[r['month']] += d
    return tot, n, per


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2026-04-01')
    a = ap.parse_args()
    df = load_trades(a.start)
    df = df[df['entry_price'].notna() & df['shares'].notna()]
    htc = df[df.hold_to_close]
    months = sorted(df['month'].unique())
    print(f'  {len(df)} closed LONG trades; {len(htc)} of them hold-to-close (entry < 10:00)')

    def row(lbl, tot, n, per):
        line = '  {:<34}{:>7}{:>+10.0f}'.format(lbl, n, tot)
        for m in months:
            line += f'{per[m]:>+9.0f}'
        greens = sum(1 for m in months if per[m] > 0)
        return line + f'   {greens}/{len(months)}'

    print(f'\n{"="*112}')
    print('  HOLD-TO-CLOSE population — $ delta vs what actually happened')
    print(f'{"="*112}')
    hdr = '  {:<34}{:>7}{:>10}'.format('variant', 'n fired', 'total $')
    for m in months:
        hdr += f'{m[5:]:>9}'
    print(hdr + '   green')
    print('  ' + '-' * (53 + 9 * len(months)))
    print(row('A. FADE (VWAP cross, 5-min)', *score(htc, fade_exit, months)))
    print(row('B. TRAIL arm3%/gap0.5% (5-min)', *score(htc, trail_exit, months)))

    print(f'\n{"="*112}')
    print('  Same two variants on the TRAILED population (10:00+), which already has the')
    print('  VWAP-cross exit live — a sanity check that the replay agrees with reality')
    print(f'{"="*112}')
    tr = df[~df.hold_to_close]
    print(row('A. FADE (VWAP cross, 5-min)', *score(tr, fade_exit, months)))

    # ── granularity: 1-min vs 5-min, on the window where 1-min data exists ──
    print(f'\n{"="*112}')
    print('  GRANULARITY — does a 1-min check catch the fade sooner? (1-min bars end 2026-08-04)')
    print(f'{"="*112}')
    sub = htc[htc['entry_date'] <= '2026-08-04']
    if sub.empty:
        print('  no hold-to-close trades inside the 1-min window')
    else:
        m5 = score(sub, fade_exit, months, table='bars_5m')
        m1 = score(sub, fade_exit, months, table='bars_1m')
        print(f'  overlapping hold-to-close trades: {len(sub)}')
        print(f'    5-min fade : fired {m5[1]:>3}   total ${m5[0]:+.0f}')
        print(f'    1-min fade : fired {m1[1]:>3}   total ${m1[0]:+.0f}')
        print(f'    difference from finer granularity: ${m1[0]-m5[0]:+.0f}')

    print('\n  ⚠️  Judge on the dollar column AND the green count. A variant that wins in total')
    print('      but is green in 2/6 months is a coin flip dressed up as an edge.\n')


if __name__ == '__main__':
    main()
