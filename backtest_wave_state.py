"""
backtest_wave_state.py
----------------------
Tests whether "wave reset" (RECOVERY) entries outperform BASING/EXTENDED entries.

Wave state is classified from the 6 five-minute bars immediately before each LONG entry:

  RECOVERY      — stock ran to a peak, pulled back ≥0.4% with volume contraction,
                   then last bar is recovering above the pullback low.
                   Hypothesis: highest-probability entry (fresh wave forming).

  BASING        — stock near its recent high with no meaningful pullback.
                   Grinding. Wave may continue or stall.

  EXTENDED      — 3+ consecutive higher-close bars leading into entry.
                   Already overheated (L2 RUN×4 catches the extreme version).

  EARLY/UNKNOWN — fewer than 4 bars available (entry within first 20 min of session,
                   or no bars_5m data for this symbol).

Data: trades.db (LONG trades, May 24+) × market_data.db (bars_5m).
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from zoneinfo import ZoneInfo

TRADES_DB     = 'trades.db'
BARS_DB       = 'market_data.db'
BARS_LOOKBACK = 6        # bars to load before entry
PULLBACK_PCT  = 0.004    # 0.4% from peak = meaningful rest
VOL_CONTRACT  = 0.80     # pullback bar volume ≤ 80% of peak bar volume
CONSEC_EXTEND = 3        # consecutive higher closes = EXTENDED


# ── helpers ────────────────────────────────────────────────────────────────────

_ET_ZONE  = ZoneInfo('America/New_York')
_UTC_ZONE = ZoneInfo('UTC')

def et_to_utc(date_str: str, time_str: str) -> datetime:
    """Convert 'YYYY-MM-DD' + 'HH:MM:SS' Eastern time (auto DST) to UTC datetime."""
    dt_et = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
    return dt_et.replace(tzinfo=_ET_ZONE).astimezone(_UTC_ZONE)


def floor_to_5min(dt: datetime) -> datetime:
    """Round down to the nearest 5-min boundary."""
    return dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)


def get_bars(conn, symbol: str, entry_utc: datetime, n: int = BARS_LOOKBACK):
    """
    Return up to n completed 5-min bars immediately before entry_utc.
    'Completed' means the bar's ts_utc < floor_to_5min(entry_utc).
    Returns list of dicts sorted oldest→newest.
    """
    cutoff = floor_to_5min(entry_utc).isoformat()
    rows = conn.execute(
        """SELECT ts_utc, open, high, low, close, volume
           FROM bars_5m
           WHERE symbol = ? AND ts_utc < ?
           ORDER BY ts_utc DESC LIMIT ?""",
        (symbol, cutoff, n)
    ).fetchall()
    return list(reversed([dict(r) for r in rows]))


# ── wave classifier ─────────────────────────────────────────────────────────────

def classify_wave(bars: list) -> tuple[str, dict]:
    """
    Classify the wave state from a list of bar dicts (oldest→newest).
    Returns (state, debug_dict).
    """
    if len(bars) < 4:
        return 'EARLY', {'bars': len(bars)}

    closes  = [b['close']  for b in bars]
    highs   = [b['high']   for b in bars]
    lows    = [b['low']    for b in bars]
    volumes = [b['volume'] for b in bars]

    # ── EXTENDED: 3+ consecutive higher closes into entry ──────────────────
    consec = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] > closes[i - 1]:
            consec += 1
        else:
            break
    if consec >= CONSEC_EXTEND:
        return 'EXTENDED', {'consec_up': consec}

    # ── find the peak bar ──────────────────────────────────────────────────
    peak_idx  = highs.index(max(highs))
    peak_high = highs[peak_idx]
    peak_vol  = volumes[peak_idx]

    # ── look for pullback AFTER the peak ──────────────────────────────────
    post_peak_bars = list(zip(
        lows[peak_idx:], closes[peak_idx:], volumes[peak_idx:]
    ))

    pulled_back     = False
    vol_contracted  = False
    pullback_low    = peak_high

    for low, close, vol in post_peak_bars[1:]:   # skip the peak bar itself
        depth = (peak_high - low) / peak_high
        if depth >= PULLBACK_PCT:
            pulled_back = True
            if low < pullback_low:
                pullback_low = low
            if vol <= peak_vol * VOL_CONTRACT:
                vol_contracted = True

    if pulled_back:
        # Is the last bar recovering above the pullback low?
        recovering = closes[-1] > pullback_low * 1.001
        state = 'RECOVERY' if (recovering and vol_contracted) else 'BASING'
        return state, {
            'peak_high': round(peak_high, 2),
            'pullback_low': round(pullback_low, 2),
            'depth_pct': round((peak_high - pullback_low) / peak_high * 100, 2),
            'vol_contracted': vol_contracted,
            'recovering': recovering,
        }

    # ── no meaningful pullback — stock basing near the high ───────────────
    return 'BASING', {
        'peak_high': round(peak_high, 2),
        'last_close': round(closes[-1], 2),
        'near_high_pct': round((peak_high - closes[-1]) / peak_high * 100, 2),
    }


# ── main backtest ───────────────────────────────────────────────────────────────

def run():
    trades_conn = sqlite3.connect(TRADES_DB)
    trades_conn.row_factory = sqlite3.Row
    bars_conn   = sqlite3.connect(BARS_DB)
    bars_conn.row_factory = sqlite3.Row

    trades = trades_conn.execute("""
        SELECT symbol, entry_date, entry_time, pnl, setup_type,
               rsi_at_entry, volume_ratio, exit_reason
        FROM trades
        WHERE side = 'LONG'
          AND entry_date >= '2026-05-24'
          AND setup_type != 'RECONCILED'
          AND pnl IS NOT NULL
        ORDER BY entry_date, entry_time
    """).fetchall()

    print(f"Classifying {len(trades)} LONG trades (May 24 – present)...\n")

    # group results
    by_state  = defaultdict(list)   # state → [pnl, ...]
    no_bars   = []
    detail    = []

    for t in trades:
        sym   = t['symbol']
        pnl   = t['pnl']
        try:
            entry_utc = et_to_utc(t['entry_date'], t['entry_time'])
        except Exception:
            continue

        bars = get_bars(bars_conn, sym, entry_utc)
        if not bars:
            no_bars.append(sym)
            by_state['NO_BARS'].append(pnl)
            continue

        state, dbg = classify_wave(bars)
        by_state[state].append(pnl)
        detail.append({
            'date':  t['entry_date'],
            'sym':   sym,
            'time':  t['entry_time'],
            'state': state,
            'pnl':   pnl,
            'setup': t['setup_type'],
            'rsi':   t['rsi_at_entry'],
            'bars':  len(bars),
            'dbg':   dbg,
        })

    trades_conn.close()
    bars_conn.close()

    # ── results ──────────────────────────────────────────────────────────────
    ORDER = ['RECOVERY', 'BASING', 'EXTENDED', 'EARLY', 'NO_BARS']

    print("=" * 70)
    print("WAVE STATE BACKTEST — LONG entries (May 24 2026 – present)")
    print("=" * 70)
    print(f"{'State':<12} {'N':>4}  {'WR%':>6}  {'Avg $':>7}  {'Total $':>8}  {'Best':>7}  {'Worst':>7}")
    print("-" * 70)

    for state in ORDER:
        pnls = by_state.get(state, [])
        if not pnls:
            continue
        n      = len(pnls)
        wins   = sum(1 for p in pnls if p > 0)
        wr     = wins / n * 100
        avg    = sum(pnls) / n
        total  = sum(pnls)
        best   = max(pnls)
        worst  = min(pnls)
        print(f"{state:<12} {n:>4}  {wr:>5.1f}%  {avg:>+7.2f}  {total:>+8.2f}  {best:>+7.2f}  {worst:>+7.2f}")

    print()

    # ── per-trade detail for RECOVERY ────────────────────────────────────────
    recovery = [d for d in detail if d['state'] == 'RECOVERY']
    if recovery:
        print(f"RECOVERY trades ({len(recovery)}):")
        print(f"  {'Date':<12} {'Sym':<6} {'Time':<9} {'PnL':>7}  {'RSI':>5}  {'Setup':<12} {'Depth%':>7}  {'VolCont'}")
        for d in recovery:
            dbg = d['dbg']
            depth = dbg.get('depth_pct', '-')
            vc    = dbg.get('vol_contracted', '-')
            print(f"  {d['date']:<12} {d['sym']:<6} {d['time']:<9} {d['pnl']:>+7.2f}  {str(d['rsi'] or '-'):>5}  {d['setup']:<12} {str(depth):>7}  {vc}")

    print()

    # ── BASING breakdown: clean vs marginal ──────────────────────────────────
    basing = [d for d in detail if d['state'] == 'BASING']
    if basing:
        # split: near HOD (<0.5% from peak) vs further
        near  = [d for d in basing if d['dbg'].get('near_high_pct', 99) < 0.5]
        other = [d for d in basing if d['dbg'].get('near_high_pct', 99) >= 0.5]
        print(f"BASING breakdown:")
        for label, subset in [('tight (<0.5% from HOD)', near), ('loose (≥0.5% from HOD)', other)]:
            if subset:
                pnls = [d['pnl'] for d in subset]
                wins = sum(1 for p in pnls if p > 0)
                print(f"  {label}: N={len(pnls)}  WR={wins/len(pnls)*100:.1f}%  avg={sum(pnls)/len(pnls):+.2f}")

    print()

    # ── key question: if we scored RECOVERY +10pts, how many more A+ entries? ─
    print("KEY SIGNAL (RECOVERY vs rest):")
    rec_pnls   = by_state.get('RECOVERY', [])
    other_pnls = [p for s, ps in by_state.items() for p in ps if s not in ('RECOVERY', 'NO_BARS')]
    if rec_pnls and other_pnls:
        rec_avg   = sum(rec_pnls) / len(rec_pnls)
        other_avg = sum(other_pnls) / len(other_pnls)
        rec_wr    = sum(1 for p in rec_pnls if p > 0) / len(rec_pnls) * 100
        other_wr  = sum(1 for p in other_pnls if p > 0) / len(other_pnls) * 100
        print(f"  RECOVERY  : avg={rec_avg:+.2f}  WR={rec_wr:.1f}%  N={len(rec_pnls)}")
        print(f"  Everything: avg={other_avg:+.2f}  WR={other_wr:.1f}%  N={len(other_pnls)}")
        delta = rec_avg - other_avg
        print(f"  Edge delta: {delta:+.2f} per trade")
        if len(rec_pnls) >= 5:
            annualised = delta * len(other_pnls) * (252 / 30)
            print(f"  Implied annual lift (if signal holds): ~${annualised:+,.0f}")
        else:
            print(f"  (N too small for annual projection — need more data)")

    if no_bars:
        print(f"\nNo bars_5m data: {sorted(set(no_bars))} ({len(no_bars)} trades skipped)")


if __name__ == '__main__':
    run()
