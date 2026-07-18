"""
Backtest: Layer 2 (pre-entry fitness) + Layer 3 (5-min confirmation)
Compares baseline vs L2-only vs L3-only vs L2+L3 combined.
Uses scan_log entries cross-joined with actual 5-min bar data (bars_5m).

Layer 2 gates (pre-entry, all hard-skip or half-size):
  HOD×3+  — price tested current HOD ≥3 times in last 6 bars → SKIP
  RUN×4+  — 4+ consecutive directional bars before entry → SKIP
  VWAP>2.5× ATR — too extended from VWAP → SKIP
  HOD×2   — 2 tests of HOD → HALF position size

Layer 3 action (T+5 bar after entry):
  HARD_FAIL (<-2%)  → exit at T+5 close price (limit damage)
  FLAT     (±0.5%)  → tighten stop to break-even (simulate: cap loss at $0)
  CONFIRM  (>+0.5%) → hold, keep actual P&L unchanged

Run:  venv/bin/python backtest_layer23.py
"""

import sqlite3
from datetime import datetime, timedelta

TRADES_DB = 'trades.db'
MARKET_DB = 'market_data.db'

# ── Thresholds ─────────────────────────────────────────────────────────────
HOD_TEST_BARS   = 6      # look-back window for HOD test count
HOD_TEST_THRESH = 0.005  # within 0.5% of HOD = "tested"
HOD_SKIP        = 3      # ≥3 tests → SKIP
HOD_HALF        = 2      # 2 tests → HALF size
RUN_SKIP        = 4      # ≥4 consecutive directional bars → SKIP
VWAP_SKIP       = 2.5    # VWAP extension > 2.5× ATR → SKIP

CONF_HARD_FAIL  = -2.0   # T+5 bar: < -2% → immediate exit
CONF_CONFIRM    =  0.5   # T+5 bar: > +0.5% → full hold

# ── Helpers ────────────────────────────────────────────────────────────────

def upper_wick(b):
    r = b['high'] - b['low']
    return (b['high'] - b['close']) / r if r > 0 else 0


def layer2_signal(pre_bars, direction, entry_price):
    """
    Returns ('GO', None) | ('HALF', reason) | ('SKIP', reason)
    pre_bars: bars BEFORE entry (last bar is the trigger bar)
    """
    if len(pre_bars) < 3:
        return ('GO', None)

    is_long = direction == 'LONG'
    window  = pre_bars[-HOD_TEST_BARS:]

    # HOD test count
    if is_long:
        hod = max(b['high'] for b in window)
        hod_tests = sum(1 for b in window if (hod - b['high']) / hod < HOD_TEST_THRESH)
    else:
        lod = min(b['low'] for b in window)
        hod_tests = sum(1 for b in window if (b['low'] - lod) / max(lod, 0.01) < HOD_TEST_THRESH)

    if hod_tests >= HOD_SKIP:
        return ('SKIP', f'HOD×{hod_tests}')

    # Consecutive directional bars
    consec = 0
    for b in reversed(window):
        if is_long and b['close'] > b['open']:
            consec += 1
        elif not is_long and b['close'] < b['open']:
            consec += 1
        else:
            break
    if consec >= RUN_SKIP:
        return ('SKIP', f'RUN×{consec}')

    # VWAP extension
    cum_tv = sum((b['high'] + b['low'] + b['close']) / 3 * b['volume'] for b in pre_bars)
    cum_v  = sum(b['volume'] for b in pre_bars)
    vwap   = cum_tv / cum_v if cum_v > 0 else entry_price
    atr_window = window[-5:]
    atr = sum(b['high'] - b['low'] for b in atr_window) / len(atr_window) if atr_window else 0.01
    if atr > 0:
        ext = (entry_price - vwap) / atr if is_long else (vwap - entry_price) / atr
        if ext > VWAP_SKIP:
            return ('SKIP', f'VWAP {ext:.1f}×ATR')

    if hod_tests == HOD_HALF:
        return ('HALF', f'HOD×2')

    return ('GO', None)


def layer3_action(entry_price, post_close, direction):
    """
    Returns 'HARD_FAIL' | 'FLAT' | 'CONFIRM'
    """
    if direction == 'LONG':
        conf_pct = (post_close - entry_price) / entry_price * 100
    else:
        conf_pct = (entry_price - post_close) / entry_price * 100

    if conf_pct < CONF_HARD_FAIL:
        return 'HARD_FAIL', conf_pct
    elif conf_pct < CONF_CONFIRM:
        return 'FLAT', conf_pct
    else:
        return 'CONFIRM', conf_pct


def simulate_pnl(actual_pnl, shares, entry_price, post_close, l3, l2_signal, direction):
    """Compute what P&L would have been under each scenario."""
    is_long = direction == 'LONG'

    # HARD_FAIL: exit at T+5 close
    if l3 == 'HARD_FAIL' and post_close:
        if is_long:
            hard_fail_pnl = (post_close - entry_price) * shares
        else:
            hard_fail_pnl = (entry_price - post_close) * shares
    else:
        hard_fail_pnl = actual_pnl  # not triggered

    # FLAT: tighten to break-even — cap loss at $0, keep wins
    be_pnl = max(0, actual_pnl) if l3 == 'FLAT' else actual_pnl

    # HALF size: same outcome direction but half shares
    half_factor = 0.5 if l2_signal == 'HALF' else 1.0

    return hard_fail_pnl, be_pnl, half_factor


# ── Main ───────────────────────────────────────────────────────────────────

def run_backtest():
    db  = sqlite3.connect(TRADES_DB)
    db.row_factory  = sqlite3.Row
    mdb = sqlite3.connect(MARKET_DB)
    mdb.row_factory = sqlite3.Row

    # Symbols available in bars_5m
    avail = {r['symbol'] for r in mdb.execute("SELECT DISTINCT symbol FROM bars_5m")}

    # All entered scan_log trades with outcomes
    entries = db.execute(f"""
        SELECT s.symbol, s.scan_date, s.scan_time, s.direction, s.grade, s.score,
               s.rsi, s.is_catalyst, s.regime,
               t.id AS tid, t.entry_price, t.entry_time, t.pnl,
               t.shares, t.exit_reason, t.side
        FROM scan_log s
        JOIN trades t ON t.id = s.entry_trade_id
        WHERE s.entered = 1
          AND t.pnl IS NOT NULL
          AND t.setup_type != 'RECONCILED'
          AND s.symbol IN ({','.join('?' * len(avail))})
        ORDER BY s.scan_date, s.scan_time
    """, list(avail)).fetchall()

    print(f"Loaded {len(entries)} entries with 5-min bar coverage\n")

    rows = []
    skipped_no_bars = 0

    for e in entries:
        sym         = e['symbol']
        scan_date   = e['scan_date']
        direction   = e['direction'] or e['side'] or 'LONG'
        entry_price = e['entry_price']
        actual_pnl  = e['pnl']
        shares      = e['shares']
        grade       = e['grade']

        try:
            et  = datetime.fromisoformat(f"{scan_date}T{e['entry_time']}")
            # Normalise to space format so comparison works for both T and space ts_utc rows
            utc = (et + timedelta(hours=4)).strftime('%Y-%m-%d %H:%M')
        except Exception:
            continue

        # SUBSTR approach handles both old T-format and new space-format ts_utc rows
        day_bars = mdb.execute("""
            SELECT ts_utc, open, high, low, close, volume FROM bars_5m
            WHERE symbol = ?
              AND SUBSTR(ts_utc, 1, 10) = ?
              AND SUBSTR(ts_utc, 12, 5) >= '13:00'
              AND SUBSTR(ts_utc, 12, 5) < '21:00'
            ORDER BY ts_utc
        """, (sym, scan_date)).fetchall()

        if not day_bars:
            skipped_no_bars += 1
            continue

        pre_bars, entry_bar, post_bar = [], None, None
        for b in day_bars:
            # Normalise both to space format for consistent comparison
            ts = b['ts_utc'][:16].replace('T', ' ')
            if ts < utc:
                pre_bars.append(b)
            elif entry_bar is None:
                entry_bar = b
            elif post_bar is None:
                post_bar = b

        if len(pre_bars) < 3 or not entry_bar:
            skipped_no_bars += 1
            continue

        # RSI danger-zone gate (mirrors grade_setup: RSI 70-80 → SKIP, catalyst or not)
        rsi        = e['rsi'] or 0
        is_cat     = bool(e['is_catalyst'])
        rsi_skip   = (70 <= rsi < 80)   # same rule for all entries — catalyst exemption removed Jun 22

        # Layer 2 signal
        l2_sig, l2_reason = layer2_signal(pre_bars, direction, entry_price)

        # Layer 3 action
        if post_bar:
            l3, conf_pct = layer3_action(entry_price, post_bar['close'], direction)
        else:
            l3, conf_pct = 'UNKNOWN', None

        hard_fail_pnl, be_pnl, half_factor = simulate_pnl(
            actual_pnl, shares, entry_price,
            post_bar['close'] if post_bar else None,
            l3, l2_sig, direction
        )

        rows.append({
            'sym': sym, 'date': scan_date, 'grade': grade,
            'actual_pnl': actual_pnl,
            'rsi': rsi, 'is_cat': is_cat, 'rsi_skip': rsi_skip,
            'l2_sig': l2_sig, 'l2_reason': l2_reason or '',
            'l3': l3, 'conf_pct': conf_pct,
            'hard_fail_pnl': hard_fail_pnl,
            'be_pnl': be_pnl,
            'half_factor': half_factor,
            'win': actual_pnl > 0,
            'direction': direction,
        })

    db.close(); mdb.close()

    print(f"Rows analysed: {len(rows)}  (skipped no-bars: {skipped_no_bars})\n")

    def stats(bucket, label, pnl_key='actual_pnl'):
        if not bucket:
            print(f"  {label:<52} N=   0")
            return
        n   = len(bucket)
        wr  = 100 * sum(r['win'] for r in bucket) / n
        avg = sum(r[pnl_key] for r in bucket) / n
        tot = sum(r[pnl_key] for r in bucket)
        print(f"  {label:<52} N={n:>4}  WR={wr:>5.1f}%  avg=${avg:>+7.2f}  total=${tot:>+8.2f}")

    # ── Baseline ───────────────────────────────────────────────────────────
    print("=" * 70)
    print("BASELINE — current system (no Layer 2 or 3)")
    print("=" * 70)
    stats(rows, "All trades (baseline)", 'actual_pnl')
    all_wins  = [r for r in rows if r['win']]
    all_loss  = [r for r in rows if not r['win']]
    print(f"  {'  Winners':<52} N={len(all_wins):>4}  total=${sum(r['actual_pnl'] for r in all_wins):>+8.2f}")
    print(f"  {'  Losers':<52} N={len(all_loss):>4}  total=${sum(r['actual_pnl'] for r in all_loss):>+8.2f}")
    print()

    # ── Layer 2 only ───────────────────────────────────────────────────────
    print("=" * 70)
    print("LAYER 2 ONLY — pre-entry fitness gate (HOD, run, VWAP)")
    print("=" * 70)

    l2_pass   = [r for r in rows if r['l2_sig'] == 'GO']
    l2_half   = [r for r in rows if r['l2_sig'] == 'HALF']
    l2_skip   = [r for r in rows if r['l2_sig'] == 'SKIP']

    stats(l2_pass, "✅ GO  — clean entry (full size)")
    stats(l2_half, "⚠️  HALF — HOD×2 (half size, halved P&L simulated)",
          'actual_pnl')  # will show full pnl; note below
    stats(l2_skip, "❌ SKIP — hard gate (HOD×3+, RUN×4+, VWAP>2.5×)")

    # Simulated total with L2 applied
    l2_sim_total = (
        sum(r['actual_pnl'] for r in l2_pass) +
        sum(r['actual_pnl'] * 0.5 for r in l2_half) +
        0  # skipped trades = $0
    )
    baseline_total = sum(r['actual_pnl'] for r in rows)
    print(f"\n  L2 simulated total: ${l2_sim_total:+.2f}  "
          f"(baseline: ${baseline_total:+.2f}  delta: ${l2_sim_total - baseline_total:+.2f})")
    skipped_total = sum(r['actual_pnl'] for r in l2_skip)
    print(f"  Skipped trades P&L: ${skipped_total:+.2f}  "
          f"(positive = those trades actually lost money and we correctly skipped them)")

    # skip breakdown
    print()
    skip_reasons = {}
    for r in l2_skip:
        skip_reasons[r['l2_reason']] = skip_reasons.get(r['l2_reason'], []) + [r['actual_pnl']]
    for reason, pnls in sorted(skip_reasons.items()):
        n = len(pnls); wr = 100 * sum(p > 0 for p in pnls) / n; avg = sum(pnls) / n
        print(f"    [{reason}] N={n}  WR={wr:.0f}%  avg=${avg:+.2f}")
    print()

    # ── Layer 3 only ───────────────────────────────────────────────────────
    print("=" * 70)
    print("LAYER 3 ONLY — T+5 confirmation action")
    print("=" * 70)

    l3_confirm = [r for r in rows if r['l3'] == 'CONFIRM']
    l3_flat    = [r for r in rows if r['l3'] == 'FLAT']
    l3_fail    = [r for r in rows if r['l3'] == 'HARD_FAIL']
    l3_unk     = [r for r in rows if r['l3'] == 'UNKNOWN']

    stats(l3_confirm, "✅ CONFIRM (>+0.5%) — hold normally")
    stats(l3_flat,    "⚠️  FLAT (±0.5%) — tighten to BE", 'be_pnl')
    stats(l3_fail,    "❌ HARD_FAIL (<-2%) — exit at T+5", 'hard_fail_pnl')
    if l3_unk:
        stats(l3_unk, "❓ UNKNOWN — no post-bar available")

    l3_sim_total = (
        sum(r['actual_pnl'] for r in l3_confirm) +
        sum(r['be_pnl']     for r in l3_flat) +
        sum(r['hard_fail_pnl'] for r in l3_fail) +
        sum(r['actual_pnl'] for r in l3_unk)
    )
    saved_on_fail = sum(r['actual_pnl'] - r['hard_fail_pnl'] for r in l3_fail)
    saved_on_flat = sum(r['actual_pnl'] - r['be_pnl'] for r in l3_flat)
    print(f"\n  L3 simulated total: ${l3_sim_total:+.2f}  "
          f"(baseline: ${baseline_total:+.2f}  delta: ${l3_sim_total - baseline_total:+.2f})")
    print(f"  Saved on HARD_FAIL: ${saved_on_fail:+.2f}  "
          f"(amount of loss avoided by exiting early)")
    print(f"  BE protection gain: ${saved_on_flat:+.2f}  "
          f"(net from tightening FLAT trades to break-even)")
    print()

    # ── Combined L2 + L3 ───────────────────────────────────────────────────
    print("=" * 70)
    print("COMBINED L2 + L3 — fitness gate THEN confirmation action")
    print("=" * 70)

    def combined_pnl(r):
        # RSI gate fires first (grade_setup, before L2 candle check)
        if r['rsi_skip']:
            return 0.0
        if r['l2_sig'] == 'SKIP':
            return 0.0  # L2 candle gate
        factor = 0.5 if r['l2_sig'] == 'HALF' else 1.0
        if r['l3'] == 'HARD_FAIL':
            base = r['hard_fail_pnl']
        elif r['l3'] == 'FLAT':
            base = r['be_pnl']
        else:
            base = r['actual_pnl']
        return base * factor

    for r in rows:
        r['combined_pnl'] = combined_pnl(r)

    # Buckets for combined
    c_pass_confirm = [r for r in rows if r['l2_sig'] == 'GO'   and r['l3'] == 'CONFIRM']
    c_pass_flat    = [r for r in rows if r['l2_sig'] == 'GO'   and r['l3'] == 'FLAT']
    c_pass_fail    = [r for r in rows if r['l2_sig'] == 'GO'   and r['l3'] == 'HARD_FAIL']
    c_half_confirm = [r for r in rows if r['l2_sig'] == 'HALF' and r['l3'] == 'CONFIRM']
    c_half_flat    = [r for r in rows if r['l2_sig'] == 'HALF' and r['l3'] == 'FLAT']
    c_half_fail    = [r for r in rows if r['l2_sig'] == 'HALF' and r['l3'] == 'HARD_FAIL']
    c_skip         = [r for r in rows if r['l2_sig'] == 'SKIP']

    stats(c_pass_confirm, "✅✅ L2 GO  + L3 CONFIRM   → full trade, hold")
    stats(c_pass_flat,    "✅⚠️  L2 GO  + L3 FLAT      → full trade, BE stop")
    stats(c_pass_fail,    "✅❌ L2 GO  + L3 HARD_FAIL → full trade, early exit", 'hard_fail_pnl')
    stats(c_half_confirm, "⚠️✅  L2 HALF + L3 CONFIRM  → half trade, hold")
    stats(c_half_flat,    "⚠️⚠️  L2 HALF + L3 FLAT     → half trade, BE stop")
    stats(c_half_fail,    "⚠️❌  L2 HALF + L3 HARD_FAIL→ half trade, early exit")
    stats(c_skip,         "❌   L2 SKIP               → no trade")

    combined_total = sum(r['combined_pnl'] for r in rows)
    print(f"\n  COMBINED total: ${combined_total:+.2f}  "
          f"(baseline: ${baseline_total:+.2f}  delta: ${combined_total - baseline_total:+.2f})")
    print()

    # ── Per-day comparison ─────────────────────────────────────────────────
    print("=" * 70)
    print("PER-DAY COMPARISON (baseline vs L2+L3)")
    print("=" * 70)
    from collections import defaultdict
    by_day = defaultdict(list)
    for r in rows:
        by_day[r['date']].append(r)

    better, worse, same = 0, 0, 0
    print(f"  {'Date':<12} {'Base':>8} {'L2+L3':>8} {'Delta':>8}  {'Trades':>6}")
    print(f"  {'-'*52}")
    for day in sorted(by_day):
        day_rows = by_day[day]
        base_day = sum(r['actual_pnl'] for r in day_rows)
        comb_day = sum(r['combined_pnl'] for r in day_rows)
        delta    = comb_day - base_day
        marker   = "↑" if delta > 1 else ("↓" if delta < -1 else "~")
        n_trades = len(day_rows)
        n_skip   = sum(1 for r in day_rows if r['l2_sig'] == 'SKIP')
        print(f"  {day}  ${base_day:>7.2f}  ${comb_day:>7.2f}  ${delta:>+7.2f}  {marker}  "
              f"{n_trades}t/{n_skip}skip")
        if delta > 1:   better += 1
        elif delta < -1: worse += 1
        else:            same  += 1

    print(f"\n  Days improved: {better}  |  Days worse: {worse}  |  Neutral: {same}")
    print()

    # ── Top dirty-win cuts (winners we would lose) ────────────────────────
    lost_wins = [r for r in rows if r['l2_sig'] == 'SKIP' and r['win']]
    if lost_wins:
        lost_wins.sort(key=lambda r: -r['actual_pnl'])
        print("=" * 70)
        print(f"WINNERS WE GIVE UP (L2 skip on actual winners, N={len(lost_wins)}):")
        print("=" * 70)
        for r in lost_wins[:10]:
            print(f"  {r['date']} {r['sym']:<6} +${r['actual_pnl']:>6.2f}  [{r['l2_reason']}]  "
                  f"grade={r['grade']}  L3={r['l3']}")
        avg_lost_win = sum(r['actual_pnl'] for r in lost_wins) / len(lost_wins)
        print(f"  Avg lost win: ${avg_lost_win:+.2f}")

    # ── Worst losses we avoid ─────────────────────────────────────────────
    saved_losses = [r for r in rows if r['l2_sig'] == 'SKIP' and not r['win']]
    if saved_losses:
        saved_losses.sort(key=lambda r: r['actual_pnl'])
        print()
        print(f"LOSSES WE AVOID (L2 skip on actual losers, N={len(saved_losses)}):")
        for r in saved_losses[:8]:
            print(f"  {r['date']} {r['sym']:<6}  ${r['actual_pnl']:>7.2f}  [{r['l2_reason']}]  "
                  f"grade={r['grade']}  L3={r['l3']}")

    # ── RSI gate summary ──────────────────────────────────────────────────
    rsi_blocked = [r for r in rows if r['rsi_skip']]
    if rsi_blocked:
        rsi_saves = [r for r in rsi_blocked if not r['win']]
        rsi_costs = [r for r in rsi_blocked if r['win']]
        print("=" * 70)
        print(f"RSI 70-80 GATE (catalyst exemption removed Jun 22) — {len(rsi_blocked)} blocked")
        print("=" * 70)
        print(f"  Correctly blocked (losers avoided): {len(rsi_saves)}  "
              f"saved=${-sum(r['actual_pnl'] for r in rsi_saves):+.2f}")
        print(f"  False blocks (winners given up):   {len(rsi_costs)}  "
              f"cost=${sum(r['actual_pnl'] for r in rsi_costs):+.2f}")
        for r in sorted(rsi_blocked, key=lambda x: x['actual_pnl'])[:8]:
            tag = "SAVE" if not r['win'] else "cost"
            print(f"    {r['date']} {r['sym']:<6} RSI={r['rsi']:>4.1f} "
                  f"{'CAT' if r['is_cat'] else '   '}  ${r['actual_pnl']:>+8.2f}  {tag}")

    # ── June spotlight ─────────────────────────────────────────────────────
    june_rows = [r for r in rows if r['date'] >= '2026-06-01']
    if june_rows:
        print()
        print("=" * 70)
        print("JUNE 2026 SPOTLIGHT")
        print("=" * 70)
        base_june = sum(r['actual_pnl'] for r in june_rows)
        comb_june = sum(r['combined_pnl'] for r in june_rows)
        print(f"  June baseline:       ${base_june:>+8.2f}")
        print(f"  June L2+L3+RSI gate: ${comb_june:>+8.2f}  (delta ${comb_june-base_june:>+.2f})")
        print(f"  June trades: {len(june_rows)}  skipped: "
              f"{sum(1 for r in june_rows if r['rsi_skip'] or r['l2_sig']=='SKIP')}")

    print()
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    delta_l2   = l2_sim_total  - baseline_total
    delta_l3   = l3_sim_total  - baseline_total
    delta_comb = combined_total - baseline_total
    print(f"  Layer 2 only:         ${delta_l2:>+.2f}  vs baseline")
    print(f"  Layer 3 only:         ${delta_l3:>+.2f}  vs baseline")
    print(f"  L2 + L3 + RSI gate:   ${delta_comb:>+.2f}  vs baseline")
    rsi_n = len(rsi_blocked) if rsi_blocked else 0
    print(f"  RSI gate contribution: see {rsi_n} blocked trades above")
    if delta_comb > 0:
        print(f"\n  ✅ BUILD APPROVED — all gates add ${delta_comb:+.2f} over {len(rows)} trades")
    else:
        print(f"\n  ⚠️  REVIEW — gates subtract ${abs(delta_comb):.2f} — check assumptions")


if __name__ == '__main__':
    run_backtest()
