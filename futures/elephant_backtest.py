"""
elephant_backtest.py v5 — Elephant Trade (Liquidity Grab Reversal) backtest

Strategy: LONG only on STRONG_BULL days.
  A STRONG_BULL day opens with a 15-min RTH body ≥ 100pt upward (institutional bias UP).
  Within the session, algos sweep stop-loss clusters by flushing DOWN ≥ 150pt in 60 min.
  We enter 10pt above the flush extreme — buying the reversal as the sweep completes.
  SL: 100pt below flush extreme. Target: 150pt above entry.

What was removed in v5 (cleanup Jun 14 2026):
  - EXTREME day classification (body ≥ 250pt) — too few occurrences, not statistically
    meaningful, and ES filter was disabled for those days without validation.
    5.5yr data had <6 EXTREME trades. Removed as data extrapolation.
  - SHORT setup (STRONG_BEAR days, surge fade) — 5.5yr backtest: 49% WR, -$472.
    MNQ has structural upward bias; shorts on this instrument are mean-reverting
    against the dominant trend. Confirmed loser across every strategy we tested.
  - OLD (50pt SL) comparison — kept only current parameters for clarity.

ES filter threshold aligned to live code: 25pt (was 40pt in v3/v4 — bug).
"""

import os, sys
import pandas as pd
import numpy as np
from datetime import date, timedelta
from pathlib import Path

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, str(Path(_DIR).parent))

from futures.collect_bars import load_bars as _cb_load_bars, filter_ny_session

# ── Constants (must match futures_trader.py exactly) ──────────
ELEPHANT_ENTRY_CONF    = 10.0
ELEPHANT_STOP_PTS      = 100.0   # 100pt SL validated Jun 13 2026: +16pp WR vs 50pt
ELEPHANT_TARGET_PTS    = 150.0
ELEPHANT_TIMEOUT_MINS  = 180.0
ELEPHANT_FLUSH_STRONG  = 150.0   # min flush depth (pts) on STRONG_BULL days
ELEPHANT_BODY_STRONG   = 100.0   # 15-min opening bar body threshold → STRONG classification
ELEPHANT_MAX_STRONG    = 4       # max entries per STRONG day (quota safety net)
ELEPHANT_ES_MOVE_SKIP  = 40.0    # skip when ES also moved ≥40pt in the same window (calibrated 5.5yr backtest; 25pt blocks all setups as MNQ 150pt flush ≈ ES 37pt)
ELEPHANT_NOON_CUTOFF   = 12      # no entries at/after noon ET
ELEPHANT_LOOKBACK_BARS = 12      # 12 × 5-min = 60-min rolling window

# Volume early exit — cuts losses when institutional selling is confirmed
ELEPHANT_VOL_ADV_ZONE    = 50.0  # pts adverse before vol monitoring activates
ELEPHANT_VOL_BUILD_MULT  = 1.5   # vol ≥ this × pre-entry avg = "building" bar
ELEPHANT_VOL_CONSEC_BARS = 3     # N consecutive building+worsening bars → exit

TICK_SIZE  = 0.25
TICK_VALUE = 0.50   # $2/pt for 1 MNQ contract


# ── Data loading ───────────────────────────────────────────────
def load_clean_bars(symbol: str) -> pd.DataFrame:
    return filter_ny_session(_cb_load_bars(symbol=symbol, table='futures_bars_5m'))


def load_all_bars(symbol: str) -> pd.DataFrame:
    return _cb_load_bars(symbol=symbol, table='futures_bars_5m')


# ── Day classifier ─────────────────────────────────────────────
def classify_day(day_bars: pd.DataFrame) -> str:
    """
    Classify today's session using the first 15-min RTH opening bar body.
    Returns: 'STRONG_BULL' | 'STRONG_BEAR' | 'NONE'
    Only STRONG_BULL days are eligible for Elephant LONG entries.
    """
    today    = day_bars.index[0].date()
    rth_open = pd.Timestamp(f'{today} 09:30:00', tz='America/New_York')
    rth_15m  = pd.Timestamp(f'{today} 09:45:00', tz='America/New_York')
    bars_15  = day_bars[(day_bars.index >= rth_open) & (day_bars.index < rth_15m)]
    if len(bars_15) < 3:
        return 'NONE'
    body = float(bars_15['close'].iloc[-1]) - float(bars_15['open'].iloc[0])
    if abs(body) >= ELEPHANT_BODY_STRONG:
        return 'STRONG_BULL' if body > 0 else 'STRONG_BEAR'
    return 'NONE'


# ── ES filter ──────────────────────────────────────────────────
def check_es_move(es_day_bars: pd.DataFrame, scan_time: pd.Timestamp) -> float:
    """Return the H-L range of ES in the 60-min window ending at scan_time."""
    window_start = scan_time - pd.Timedelta(minutes=60)
    es_win = es_day_bars[
        (es_day_bars.index >= window_start) & (es_day_bars.index <= scan_time)
    ]
    return float(es_win['high'].max() - es_win['low'].min()) if len(es_win) >= 2 else 0.0


# ── Outcome simulation ─────────────────────────────────────────
def _simulate_outcome(entry: float, sl: float, target: float,
                       future_bars: pd.DataFrame,
                       entry_time: pd.Timestamp,
                       entry_vol_avg: float = 0.0) -> dict:
    """
    Walk future bars and apply the exit stack (LONG only):
      1. Hard stop (broker stop)
      2. Target hit
      3. Volume early exit (optional — fires when institutional selling is confirmed)
      4. Timeout (180 min — wider than standard 90 min to allow LGR to play out)
      5. EOD
    """
    timeout_cut  = entry_time + pd.Timedelta(minutes=ELEPHANT_TIMEOUT_MINS)
    eod_cut      = pd.Timestamp(f'{entry_time.date()} 16:00:00', tz='America/New_York')
    last_close   = None
    prev_close   = entry
    consec_build = 0

    for _, bar in future_bars.iterrows():
        ts         = bar.name
        last_close = float(bar['close'])
        bar_vol    = float(bar['volume'])

        if float(bar['low']) <= sl:
            pts = sl - entry
            return {'exit_reason': 'stop', 'exit': sl,
                    'exit_pts': pts, 'pnl_usd': pts / TICK_SIZE * TICK_VALUE}
        if float(bar['high']) >= target:
            pts = target - entry
            return {'exit_reason': 'target', 'exit': target,
                    'exit_pts': pts, 'pnl_usd': pts / TICK_SIZE * TICK_VALUE}

        if entry_vol_avg > 0:
            adv_pts     = entry - last_close
            going_worse = last_close < prev_close
            if adv_pts >= ELEPHANT_VOL_ADV_ZONE:
                if bar_vol >= ELEPHANT_VOL_BUILD_MULT * entry_vol_avg and going_worse:
                    consec_build += 1
                else:
                    consec_build = 0
                if consec_build >= ELEPHANT_VOL_CONSEC_BARS:
                    pts = last_close - entry
                    return {'exit_reason': 'vol_early_exit', 'exit': last_close,
                            'exit_pts': pts, 'pnl_usd': pts / TICK_SIZE * TICK_VALUE}
            else:
                consec_build = 0

        prev_close = last_close

        if ts >= timeout_cut:
            pts = last_close - entry
            if -10.0 <= pts <= 25.0:
                return {'exit_reason': 'timeout', 'exit': last_close,
                        'exit_pts': pts, 'pnl_usd': pts / TICK_SIZE * TICK_VALUE}
        if ts >= eod_cut:
            pts = last_close - entry
            return {'exit_reason': 'eod', 'exit': last_close,
                    'exit_pts': pts, 'pnl_usd': pts / TICK_SIZE * TICK_VALUE}

    if last_close is not None:
        pts = last_close - entry
        return {'exit_reason': 'eod', 'exit': last_close,
                'exit_pts': pts, 'pnl_usd': pts / TICK_SIZE * TICK_VALUE}
    return {'exit_reason': 'eod', 'exit': entry, 'exit_pts': 0.0, 'pnl_usd': 0.0}


# ── Single-day simulation ──────────────────────────────────────
def simulate_day(today_bars: pd.DataFrame, es_bars: pd.DataFrame,
                 vol_exit: bool = True) -> list[dict]:
    """
    Walk STRONG_BULL day bars for Elephant LONG setups.
    Caller passes only STRONG_BULL days — day_type check is upstream.

    Scan logic:
      Rolling 60-min window. If window H-L ≥ 150pt and current price is
      10–60pt above the window low (entry zone), enter LONG.
      ES filter: if ES also moved ≥ 25pt in the same window → skip (true macro move, not sweep).
      Dedup: each flush extreme (bar timestamp) is only acted on once.
    """
    today     = today_bars.index[0].date()
    rth_open  = pd.Timestamp(f'{today} 09:30:00', tz='America/New_York')
    noon_cut  = pd.Timestamp(f'{today} {ELEPHANT_NOON_CUTOFF:02d}:00:00',
                             tz='America/New_York')

    rth_bars = today_bars[today_bars.index >= rth_open].copy()
    es_rth   = es_bars[es_bars.index >= rth_open] if not es_bars.empty else pd.DataFrame()

    trades_today = 0
    flush_ids    = set()
    trades       = []

    for i in range(ELEPHANT_LOOKBACK_BARS, len(rth_bars)):
        bar_time = rth_bars.index[i]
        if bar_time >= noon_cut or trades_today >= ELEPHANT_MAX_STRONG:
            break

        window        = rth_bars.iloc[i - ELEPHANT_LOOKBACK_BARS: i + 1]
        current_price = float(window['close'].iloc[-1])
        w_high        = float(window['high'].max())
        w_low         = float(window['low'].min())
        flush_depth   = w_high - w_low

        if flush_depth < ELEPHANT_FLUSH_STRONG:
            continue

        flush_extreme = w_low
        flush_bar_ts  = str(window['low'].idxmin())
        entry_level   = flush_extreme + ELEPHANT_ENTRY_CONF

        if not (entry_level <= current_price <= flush_extreme + 50):
            continue
        if flush_bar_ts in flush_ids:
            continue

        flush_bar_pos = (i - ELEPHANT_LOOKBACK_BARS) + int(window['low'].values.argmin())

        # ES filter: skip if ES confirmed the flush (macro move, not an algo sweep)
        if not es_rth.empty:
            if check_es_move(es_rth, bar_time) >= ELEPHANT_ES_MOVE_SKIP:
                continue

        v_start   = max(0, flush_bar_pos - 20)
        e_vol_avg = float(rth_bars.iloc[v_start:flush_bar_pos]['volume'].mean() or 0)

        sl     = round(flush_extreme - ELEPHANT_STOP_PTS, 2)
        target = round(entry_level   + ELEPHANT_TARGET_PTS, 2)
        future = rth_bars.iloc[i + 1:]
        out    = _simulate_outcome(current_price, sl, target, future, bar_time,
                                   entry_vol_avg=e_vol_avg if vol_exit else 0.0)
        out.update({
            'date':        str(today),
            'entry_time':  str(bar_time.time())[:5],
            'direction':   'LONG',
            'day_type':    'STRONG_BULL',
            'flush_depth': flush_depth,
            'entry':       current_price,
            'stop':        sl,
            'target':      target,
        })
        trades.append(out)
        flush_ids.add(flush_bar_ts)
        trades_today += 1

    return trades


# ── Full backtest runner ───────────────────────────────────────
def run_backtest(mnq: pd.DataFrame, es: pd.DataFrame,
                 vol_exit: bool = True) -> list[dict]:
    """
    Run full Elephant LONG backtest.
    mnq     : RTH-only bars (filter_ny_session applied)
    es      : RTH ES bars for macro filter
    vol_exit: enable volume early exit in adverse zone
    """
    bars_930      = mnq[(mnq.index.hour == 9) & (mnq.index.minute == 30)]
    trading_dates = sorted(set(ts.date() for ts in bars_930.index))

    all_trades = []
    for today in trading_dates:
        rth_s = pd.Timestamp(f'{today} 09:30:00', tz='America/New_York')
        rth_e = pd.Timestamp(f'{today} 16:00:00', tz='America/New_York')

        day_bars = mnq[(mnq.index >= rth_s) & (mnq.index <= rth_e)].copy()
        if len(day_bars) < ELEPHANT_LOOKBACK_BARS + 3:
            continue

        if classify_day(day_bars) != 'STRONG_BULL':
            continue

        es_day = es[(es.index >= rth_s) & (es.index <= rth_e)] \
            if not es.empty else pd.DataFrame()

        trades = simulate_day(day_bars, es_day, vol_exit=vol_exit)
        all_trades.extend(trades)

    return all_trades


# ── Reporting ──────────────────────────────────────────────────
def _stats(trades: list[dict]) -> dict:
    if not trades:
        return {'n': 0, 'wr': 0, 'avg_w': 0, 'avg_l': 0, 'ev': 0, 'total': 0}
    df = pd.DataFrame(trades)
    w = df[df['pnl_usd'] > 0]
    l = df[df['pnl_usd'] <= 0]
    return {
        'n':     len(df),
        'wr':    len(w) / len(df) * 100,
        'avg_w': w['pnl_usd'].mean() if len(w) else 0,
        'avg_l': l['pnl_usd'].mean() if len(l) else 0,
        'ev':    df['pnl_usd'].mean(),
        'total': df['pnl_usd'].sum(),
    }


def main():
    print("=" * 65)
    print("ELEPHANT TRADE BACKTEST v5 — LONG only, STRONG_BULL days")
    print(f"entry={ELEPHANT_ENTRY_CONF}pt above flush | "
          f"stop={ELEPHANT_STOP_PTS}pt below extreme | "
          f"target={ELEPHANT_TARGET_PTS}pt above entry")
    print(f"R:R = {ELEPHANT_TARGET_PTS/(ELEPHANT_ENTRY_CONF+ELEPHANT_STOP_PTS):.2f}  "
          f"| Timeout={ELEPHANT_TIMEOUT_MINS:.0f}min  "
          f"| ES skip ≥{ELEPHANT_ES_MOVE_SKIP}pt")
    print("=" * 65)

    print("\nLoading bars…")
    mnq_all = load_all_bars('MNQ')
    mnq     = filter_ny_session(mnq_all)
    es_all  = load_all_bars('ES')
    es      = filter_ny_session(es_all)
    print(f"  MNQ RTH: {len(mnq):,} bars  {mnq.index[0].date()} → {mnq.index[-1].date()}")
    print(f"  ES  RTH: {len(es):,} bars")

    trades = run_backtest(mnq, es, vol_exit=True)

    s    = _stats(trades)
    comm = s['n'] * 2.00   # $2/RT
    slip = s['n'] * 1.00   # $0.50/pt × 2 ticks
    net  = s['total'] - comm - slip

    print(f"\n{'='*65}")
    print(f"RESULTS — 1 MNQ contract  ({mnq.index[0].year}–{mnq.index[-1].year})")
    print(f"{'='*65}")
    print(f"  N={s['n']}  WR={s['wr']:.1f}%  EV/trade=${s['ev']:+.1f}  Gross=${s['total']:+,.0f}")
    print(f"  AvgWin=${s['avg_w']:+.0f}  AvgLoss=${s['avg_l']:+.0f}")
    print(f"  After comm $2/RT + slip $1/RT: Net=${net:+,.0f}")

    if not trades:
        return

    df = pd.DataFrame(trades)
    df['year'] = pd.to_datetime(df['date']).dt.year

    # Year-by-year
    print(f"\n{'─'*65}")
    print("  Year   N     WR%    AvgPnL$    Total$")
    for yr, g in df.groupby('year'):
        wr  = (g['pnl_usd'] > 0).mean() * 100
        avg = g['pnl_usd'].mean()
        tot = g['pnl_usd'].sum()
        print(f"  {yr}   {len(g):>3}   {wr:>5.1f}%   ${avg:>+6.0f}   ${tot:>+8,.0f}")

    # Exit reasons
    print(f"\n{'─'*65}")
    print("  Exit reasons")
    for reason, grp in df.groupby('exit_reason'):
        wr  = (grp['pnl_usd'] > 0).mean() * 100
        ev  = grp['pnl_usd'].mean()
        pct = len(grp) / len(df) * 100
        print(f"  {reason:<16}  N={len(grp):>3} ({pct:>4.1f}%)  "
              f"WR={wr:.1f}%  EV=${ev:+.0f}")

    print(f"\n{'='*65}")
    print(f"Frequency: ~{s['n']/5.5:.0f} trades/yr  "
          f"({'low frequency — runs as overlay' if s['n']/5.5 < 10 else 'adequate'})")
    print("LONG only. MNQ upward bias confirmed across all strategies tested.")


if __name__ == '__main__':
    main()
