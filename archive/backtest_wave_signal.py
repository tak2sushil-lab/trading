"""
backtest_wave_signal.py
-----------------------
Tests whether wave state at scan time predicts forward price movement,
using ALL evaluated scan_log ticks (traded + not-traded).

Key question: does RECOVERY wave state → higher actual_30m_pct / actual_60m_pct
              INDEPENDENTLY of whether we entered the trade?

If yes, the signal is real at the market level, not just a trade-selection artifact.
Then we can decide HOW to use it (scoring, sizing, hard gate).

Data:
  scan_log (trades.db)   — Apr 15 → Jun 22 2026, forward returns pre-populated
  bars_5m (market_data.db) — intraday bars for universe symbols
"""

import sqlite3
from collections import defaultdict
from statistics import mean, stdev
from backtest_wave_state import classify_wave, get_bars, et_to_utc


TRADES_DB = 'trades.db'
BARS_DB   = 'market_data.db'


def stats(pnls: list) -> dict:
    if not pnls:
        return {}
    wins = [p for p in pnls if p > 0]
    return {
        'n':     len(pnls),
        'wr':    len(wins) / len(pnls) * 100,
        'avg':   mean(pnls),
        'std':   stdev(pnls) if len(pnls) > 1 else 0,
        'total': sum(pnls),
        'best':  max(pnls),
        'worst': min(pnls),
    }


def print_table(title: str, groups: dict, metric: str = 'avg_30m'):
    ORDER = ['RECOVERY', 'BASING', 'EXTENDED', 'EARLY', 'NO_BARS']
    print(f"\n{'─'*72}")
    print(f"  {title}")
    print(f"{'─'*72}")
    print(f"  {'State':<12} {'N':>5}  {'WR%':>6}  {'Avg 30m':>8}  {'Avg 60m':>8}  {'Avg Day':>8}  {'StdDev':>7}")
    print(f"  {'─'*64}")
    for state in ORDER:
        row = groups.get(state)
        if not row or not row['30m']:
            continue
        s30 = stats(row['30m'])
        s60 = stats(row['60m']) if row['60m'] else {}
        sd  = stats(row['day']) if row['day'] else {}
        std = f"{s30['std']:>7.2f}" if s30.get('std') else '     -'
        avg60 = f"{s60['avg']:>+8.2f}%" if s60 else '        -'
        avgd  = f"{sd['avg']:>+8.2f}%"  if sd  else '        -'
        print(f"  {state:<12} {s30['n']:>5}  {s30['wr']:>5.1f}%  {s30['avg']:>+8.2f}%  {avg60}  {avgd}  {std}")


def run():
    trades_conn = sqlite3.connect(TRADES_DB)
    trades_conn.row_factory = sqlite3.Row
    bars_conn   = sqlite3.connect(BARS_DB)
    bars_conn.row_factory = sqlite3.Row

    # Only scored opportunities (A+/A/B) with forward data
    # Direction=LONG; include both entered=1 and entered=0
    rows = trades_conn.execute("""
        SELECT scan_date, scan_time, symbol, direction, grade, score,
               is_catalyst, entered, actual_30m_pct, actual_60m_pct,
               actual_day_pct, rsi, intra_chg, vol_ratio, skip_reason
        FROM scan_log
        WHERE direction = 'LONG'
          AND grade IN ('A+', 'A', 'B')
          AND actual_30m_pct IS NOT NULL
        ORDER BY scan_date, scan_time
    """).fetchall()

    print(f"Loaded {len(rows)} LONG A+/A/B scan_log rows with forward data")
    print(f"Date range: {rows[0]['scan_date']} → {rows[-1]['scan_date']}")

    # Classify wave state for each tick
    # groups[slice][state] = {'30m': [...], '60m': [...], 'day': [...]}
    def make_group():
        return defaultdict(lambda: {'30m': [], '60m': [], 'day': []})

    all_ticks   = make_group()
    traded      = make_group()
    not_traded  = make_group()
    catalyst    = make_group()
    no_catalyst = make_group()
    by_grade    = {'A+': make_group(), 'A': make_group(), 'B': make_group()}

    no_bars_syms = set()
    classified   = 0
    detail       = []

    for r in rows:
        sym  = r['symbol']
        p30  = r['actual_30m_pct']
        p60  = r['actual_60m_pct']
        pday = r['actual_day_pct']

        try:
            # scan_time is 'HH:MM', pad seconds
            entry_utc = et_to_utc(r['scan_date'], r['scan_time'] + ':00')
        except Exception:
            continue

        bars = get_bars(bars_conn, sym, entry_utc)
        if not bars:
            no_bars_syms.add(sym)
            state = 'NO_BARS'
        else:
            state, _ = classify_wave(bars)
        classified += 1

        def push(grp):
            grp[state]['30m'].append(p30)
            if p60  is not None: grp[state]['60m'].append(p60)
            if pday is not None: grp[state]['day'].append(pday)

        push(all_ticks)
        push(traded    if r['entered'] else not_traded)
        push(catalyst  if r['is_catalyst'] else no_catalyst)
        if r['grade'] in by_grade:
            push(by_grade[r['grade']])

        detail.append({
            'date': r['scan_date'], 'sym': sym, 'state': state,
            'entered': r['entered'], 'is_cat': r['is_catalyst'],
            'grade': r['grade'], 'score': r['score'],
            'rsi': r['rsi'], 'intra': r['intra_chg'],
            'p30': p30, 'p60': p60, 'pday': pday,
        })

    trades_conn.close()
    bars_conn.close()

    print(f"Classified: {classified}  No bars: {len([d for d in detail if d['state']=='NO_BARS'])}")

    # ── Main results ─────────────────────────────────────────────────────────
    print_table("ALL EVALUATED TICKS (traded + not-traded)", all_ticks)
    print_table("TRADED ticks only (entered=1)", traded)
    print_table("NOT-TRADED ticks (entered=0, capacity/gate blocked)", not_traded)
    print_table("CATALYST ticks", catalyst)
    print_table("NON-CATALYST ticks", no_catalyst)
    print_table("A+ grade only", by_grade['A+'])

    # ── Key signal test ───────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print("  SIGNAL TEST: Does RECOVERY predict better 30m outcome than EXTENDED?")
    print(f"{'='*72}")
    for label, grp in [
        ('ALL', all_ticks), ('CATALYST', catalyst), ('NON-CATALYST', no_catalyst)
    ]:
        rec  = stats(grp['RECOVERY']['30m'])
        ext  = stats(grp['EXTENDED']['30m'])
        bas  = stats(grp['BASING']['30m'])
        if not rec or not ext:
            continue
        delta_re = rec['avg'] - ext['avg']
        delta_rb = rec['avg'] - bas['avg']
        print(f"\n  {label}:")
        print(f"    RECOVERY  N={rec['n']:>4}  avg30m={rec['avg']:>+6.2f}%  WR={rec['wr']:>5.1f}%")
        print(f"    BASING    N={bas['n']:>4}  avg30m={bas['avg']:>+6.2f}%  WR={bas['wr']:>5.1f}%")
        print(f"    EXTENDED  N={ext['n']:>4}  avg30m={ext['avg']:>+6.2f}%  WR={ext['wr']:>5.1f}%")
        print(f"    RECOVERY vs EXTENDED: {delta_re:>+.2f}% per tick")
        print(f"    RECOVERY vs BASING:   {delta_rb:>+.2f}% per tick")

    # ── Forward return distribution by state ─────────────────────────────────
    print(f"\n{'='*72}")
    print("  FORWARD RETURN DISTRIBUTION — did the signal hold 30m, 60m, all day?")
    print(f"{'='*72}")
    ORDER = ['RECOVERY', 'BASING', 'EXTENDED']
    for state in ORDER:
        s30  = stats(all_ticks[state]['30m'])
        s60  = stats(all_ticks[state]['60m'])
        sday = stats(all_ticks[state]['day'])
        if not s30:
            continue
        pct_pos30 = sum(1 for p in all_ticks[state]['30m'] if p >= 1.0) / s30['n'] * 100
        pct_pos60 = sum(1 for p in all_ticks[state]['60m'] if p >= 1.0) / s60['n'] * 100 if s60 else 0
        print(f"\n  {state} (N={s30['n']}):")
        print(f"    30m:  avg={s30['avg']:>+5.2f}%  WR={s30['wr']:.1f}%  ≥1%: {pct_pos30:.1f}%")
        if s60:
            print(f"    60m:  avg={s60['avg']:>+5.2f}%  WR={s60['wr']:.1f}%  ≥1%: {pct_pos60:.1f}%")
        if sday:
            print(f"    Day:  avg={sday['avg']:>+5.2f}%  WR={sday['wr']:.1f}%")

    # ── Percentile view ───────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print("  PERCENTILE VIEW — top-quartile 30m returns by wave state")
    print(f"{'='*72}")
    for state in ORDER:
        vals = sorted(all_ticks[state]['30m'])
        if len(vals) < 4:
            continue
        q25 = vals[len(vals)//4]
        q50 = vals[len(vals)//2]
        q75 = vals[3*len(vals)//4]
        print(f"  {state:<12} p25={q25:>+6.2f}%  median={q50:>+6.2f}%  p75={q75:>+6.2f}%  max={max(vals):>+6.2f}%")

    # ── Sizing insight ────────────────────────────────────────────────────────
    # If we sized 1.5x RECOVERY and 0.5x EXTENDED, what's the P&L delta?
    print(f"\n{'='*72}")
    print("  SIZING SIMULATION — what if we sized by wave state?")
    print("  (assumes each tick = 1 unit of P&L, proportional to actual_30m_pct)")
    print(f"{'='*72}")
    base_total    = sum(all_ticks[s]['30m'][i]
                        for s in ['RECOVERY','BASING','EXTENDED']
                        for i in range(len(all_ticks[s]['30m'])))
    sized_total   = (
        sum(p * 1.5 for p in all_ticks['RECOVERY']['30m'])
      + sum(p * 1.0 for p in all_ticks['BASING']['30m'])
      + sum(p * 0.5 for p in all_ticks['EXTENDED']['30m'])
    )
    n_all = sum(len(all_ticks[s]['30m']) for s in ['RECOVERY','BASING','EXTENDED'])
    print(f"  Baseline (equal size):   total={base_total:>+.1f}%  avg={base_total/n_all:>+.3f}%/tick")
    print(f"  Sized (R×1.5 E×0.5):    total={sized_total:>+.1f}%  avg={sized_total/n_all:>+.3f}%/tick")
    print(f"  Delta:                   {sized_total-base_total:>+.1f}%  ({(sized_total-base_total)/n_all:>+.3f}%/tick)")

    if no_bars_syms:
        print(f"\n  No bars_5m coverage ({len(no_bars_syms)} symbols): {sorted(no_bars_syms)}")


if __name__ == '__main__':
    run()
