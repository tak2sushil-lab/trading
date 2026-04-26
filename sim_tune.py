# sim_tune.py — test parameter tweaks across 3 consecutive weeks
# Shows Baseline vs Optimized vs Optimized+Cap
# Update WEEKS below to choose which weeks to analyze.
#
# Command: venv/bin/python sim_tune.py
#          venv/bin/python sim_tune.py NVDA PLTR AMD   (custom symbols)

# ── Cache yfinance HTTP — configs 2+ are near-instant ────────────────────────
import requests_cache
requests_cache.install_cache('sim_tune_cache', expire_after=86400)

import sys, io, re, contextlib
from datetime import date
import sim_today

# ── Universe ──────────────────────────────────────────────────────────────────

DEFAULT_SYMBOLS = [
    'NVDA', 'PLTR', 'MSFT', 'AAPL', 'TSLA',
    'POET', 'EOSE', 'IONQ', 'HOOD', 'AMD',
    'AVGO', 'META', 'AMZN', 'SOUN', 'RKLB',
    'OKLO', 'SMCI', 'CRM',  'CRWV', 'BBAI',
]
SYMBOLS = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_SYMBOLS

# ── 3 weeks to test ───────────────────────────────────────────────────────────

WEEKS = [
    {
        'label': 'Week 1  Apr 14–18  (tariff bounce)',
        'days':  [date(2026, 4, 14), date(2026, 4, 15), date(2026, 4, 16),
                  date(2026, 4, 17), date(2026, 4, 18)],
    },
    {
        'label': 'Week 2  Apr 20–24  (tariff chaos)',
        'days':  [date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22),
                  date(2026, 4, 23), date(2026, 4, 24)],
    },
]

# ── Configs to compare ────────────────────────────────────────────────────────
# _MAX_DAILY: not a sim_today constant — handled post-processing (top-N per day)

CONFIGS = [
    {
        'name':                 'Current  (240min no-move)',
        'BLOCK_CAUTIOUS':       True,
        'NO_MOVE_MINUTES':      240,
        'NO_MOVE_UPPER_PCT':    2.0,
        'BE_TRIGGER_PCT':       2.5,
        'PARTIAL_EXIT':         True,
        'EARLY_ENTRY_ENABLED':  False,
        '_MAX_DAILY':           5,
    },
    {
        'name':                 'Test next param here',
        'BLOCK_CAUTIOUS':       True,
        'NO_MOVE_MINUTES':      240,
        'NO_MOVE_UPPER_PCT':    2.0,
        'BE_TRIGGER_PCT':       2.5,
        'PARTIAL_EXIT':         True,
        'EARLY_ENTRY_ENABLED':  False,
        '_MAX_DAILY':           5,
    },
]

# ── Output parser ─────────────────────────────────────────────────────────────

def parse_result(text, symbol, sim_date):
    r = {
        'symbol':  symbol,
        'date':    sim_date,
        'entered': False,
        'score':   0,
        'capital': 0,
        'outcome': 'SKIP',
        'pnl_usd': None,
        'exit_reason': None,
    }
    m = re.search(r'▶.*?score=(\d+).*?capital=\$([0-9,]+)', text)
    if m:
        r['entered'] = True
        r['score']   = int(m.group(1))
        r['capital'] = int(m.group(2).replace(',', ''))

    m = re.search(r'→\s+(✅ WIN|❌ LOSS)\s+[+-][0-9.]+%\s+\$([+-][0-9.]+)', text)
    if m:
        r['outcome']  = 'WIN' if '✅' in m.group(1) else 'LOSS'
        r['pnl_usd']  = float(m.group(2))

    m = re.search(r'■\s+\d+:\d+\s+\$[0-9.]+\s+EXIT\s+(.+)', text)
    if m:
        r['exit_reason'] = m.group(1).strip()

    if '⏳ STILL OPEN' in text:
        r['outcome'] = 'OPEN'
        after = text.split('STILL OPEN', 1)[-1]
        m = re.search(r'\$([+-][0-9.]+)', after)
        if m: r['pnl_usd'] = float(m.group(1))

    if r['entered'] and r['outcome'] == 'SKIP':
        r['outcome'] = 'OPEN'
    return r


# ── Run one config for one week ───────────────────────────────────────────────

def run_week_config(cfg, week_days, regimes):
    for k, v in cfg.items():
        if not k.startswith('_'):
            setattr(sim_today, k, v)

    max_daily = cfg.get('_MAX_DAILY', None)
    all_results = []

    for sim_date in week_days:
        sim_today.SIM_DATE = sim_date
        regime, spy_chg, _ = regimes[sim_date]

        day_results = []
        for sym in SYMBOLS:
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    sim_today.simulate(sym, regime, spy_chg)
            except Exception as e:
                buf.write(f"ERROR {e}")
            day_results.append(parse_result(buf.getvalue(), sym, sim_date))

        if max_daily:
            entered = sorted(
                [r for r in day_results if r['entered']],
                key=lambda r: r['score'], reverse=True
            )
            keep = {id(r) for r in entered[:max_daily]}
            for r in day_results:
                if r['entered'] and id(r) not in keep:
                    r['capped'] = True

        all_results.extend(day_results)

    active  = [r for r in all_results if r['entered'] and not r.get('capped')]
    wins    = [r for r in active if r['outcome'] == 'WIN']
    losses  = [r for r in active if r['outcome'] == 'LOSS']
    pnl     = sum(r['pnl_usd'] for r in active if r['pnl_usd'] is not None)
    closed  = len(wins) + len(losses)

    day_pnl = {}
    for d in week_days:
        day_active = [r for r in active if r['date'] == d]
        day_pnl[d] = sum(r['pnl_usd'] for r in day_active if r['pnl_usd'] is not None)

    return {
        'trades':   len(active),
        'wins':     len(wins),
        'losses':   len(losses),
        'pnl':      pnl,
        'wr':       wins.__len__() / closed * 100 if closed else 0.0,
        'avg_win':  sum(r['pnl_usd'] for r in wins   if r['pnl_usd']) / max(len(wins), 1),
        'avg_loss': sum(r['pnl_usd'] for r in losses if r['pnl_usd']) / max(len(losses), 1),
        'day_pnl':  day_pnl,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def fp(v, w=8):
    if v is None: return ' ' * w
    s = '+' if v >= 0 else ''
    return f'{s}${v:,.0f}'.rjust(w)

def fp2(v, w=9):
    if v is None: return ' ' * w
    s = '+' if v >= 0 else ''
    return f'{s}${v:,.2f}'.rjust(w)

def bar(v, scale=1.5):
    """Tiny ASCII bar for P&L — one char per $scale."""
    if v is None: return ''
    n = int(abs(v) / scale)
    n = min(n, 30)
    return ('▓' * n) if v >= 0 else ('░' * n)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*80}")
    print(f"  {len(WEEKS)}-WEEK SIMULATION  |  A/B parameter test  |  5% SL / $2K capital / Partial exits / 240min no-move")
    print(f"  Universe ({len(SYMBOLS)}): {', '.join(SYMBOLS)}")
    print(f"{'='*80}\n")

    # ── Pre-fetch all regimes across all 3 weeks ──────────────────────────────
    all_days = [d for w in WEEKS for d in w['days']]
    regimes  = {}

    print(f"Pre-fetching regimes for all {len(all_days)} trading days...")
    for d in all_days:
        sim_today.SIM_DATE = d
        try:
            regime, spy_chg, vix = sim_today.get_regime()
        except Exception:
            regime, spy_chg, vix = 'NORMAL', 0.0, 18.0
        regimes[d] = (regime, spy_chg, vix)
        print(f"  {d.strftime('%a %b %d')}  {regime:8}  SPY {spy_chg:+.1f}%  VIX {vix:.1f}")
    print()

    # ── Run all configs × all weeks ───────────────────────────────────────────
    week_results = []   # list of {week_label, cfg_name, result_dict}
    w_pnls_base  = [0.0] * len(WEEKS)   # baseline P&L per week for delta comparison
    for week in WEEKS:
        for cfg in CONFIGS:
            print(f"  {week['label']}  |  {cfg['name']}...", end=' ', flush=True)
            r = run_week_config(cfg, week['days'], regimes)
            week_results.append({'week': week['label'], 'cfg': cfg['name'], 'r': r})
            print(f"{r['trades']} trades  {r['wins']}W/{r['losses']}L  {fp2(r['pnl'])}")

    # ── Per-week detail tables ────────────────────────────────────────────────
    for week in WEEKS:
        days = week['days']
        print(f"\n\n{'━'*80}")
        print(f"  {week['label']}  — Day-by-Day Breakdown")
        print(f"{'━'*80}")

        # Regime header row
        regime_row = '  ' + f"{'Config':<36}"
        for d in days:
            regime_row += f"  {d.strftime('%a'):>5}"
        regime_row += f"  {'Total':>9}  {'Tr':>4}  {'WR':>5}  {'AvgW':>6}  {'AvgL':>6}"
        print(regime_row)

        # Regime line
        r_line = '  ' + ' ' * 36
        for d in days:
            reg, spy, _ = regimes[d]
            r_line += f"  {(reg[:3]+str(round(spy,1))):>5}"
        print(r_line)
        print(f"  {'-'*76}")

        cfg_names = [c['name'] for c in CONFIGS]
        for cfg_name in cfg_names:
            wr = next(x for x in week_results if x['week'] == week['label'] and x['cfg'] == cfg_name)
            r  = wr['r']
            row = f"  {cfg_name:<36}"
            for d in days:
                v = r['day_pnl'].get(d, 0)
                row += f"  {fp(v, 5)}"
            wr_pct = f"{r['wr']:.0f}%"
            row += (f"  {fp2(r['pnl'], 9)}"
                    f"  {r['trades']:>4}"
                    f"  {wr_pct:>5}"
                    f"  {fp(r['avg_win'], 6)}"
                    f"  {fp(r['avg_loss'], 6)}")
            print(row)

        # delta rows
        base_r = next(x['r'] for x in week_results
                      if x['week'] == week['label'] and x['cfg'] == CONFIGS[0]['name'])
        for cfg in CONFIGS[1:]:
            opt_r = next(x['r'] for x in week_results
                         if x['week'] == week['label'] and x['cfg'] == cfg['name'])
            delta = opt_r['pnl'] - base_r['pnl']
            delta_str = f"  Δ {cfg['name']}: {'+' if delta >= 0 else ''}{delta:,.2f}"
            print(delta_str)

    # ── 3-week aggregate summary ──────────────────────────────────────────────
    wk_hdrs = '  '.join(f"{'W'+str(i+1):>9}" for i in range(len(WEEKS)))
    total_lbl = f"{len(WEEKS)}-Week"
    print(f"\n\n{'='*80}")
    print(f"  {len(WEEKS)}-WEEK AGGREGATE SUMMARY")
    print(f"{'='*80}")
    print(f"  {'Config':<36}  {wk_hdrs}  {total_lbl:>10}  {'Tr':>4}  {'WR':>5}")
    print(f"  {'-'*74}")

    for cfg in CONFIGS:
        w_pnls = []
        t_trades = t_wins = t_losses = 0
        for week in WEEKS:
            wr = next(x for x in week_results
                      if x['week'] == week['label'] and x['cfg'] == cfg['name'])
            r = wr['r']
            w_pnls.append(r['pnl'])
            t_trades  += r['trades']
            t_wins    += r['wins']
            t_losses  += r['losses']
        total_pnl = sum(w_pnls)
        wr_pct = t_wins / max(t_wins + t_losses, 1) * 100
        week_cols = '  '.join(fp2(p, 9) for p in w_pnls)
        print(f"  {cfg['name']:<36}  {week_cols}  {fp2(total_pnl, 10)}  {t_trades:>4}  {wr_pct:>4.0f}%")

        if cfg['name'] == CONFIGS[0]['name']:
            w_pnls_base = w_pnls[:]

    # Delta row vs first config
    n_weeks = len(WEEKS)
    for cfg in CONFIGS[1:]:
        deltas = []
        for week in WEEKS:
            base_pnl = next(x['r']['pnl'] for x in week_results
                            if x['week'] == week['label'] and x['cfg'] == CONFIGS[0]['name'])
            opt_pnl  = next(x['r']['pnl'] for x in week_results
                            if x['week'] == week['label'] and x['cfg'] == cfg['name'])
            deltas.append(opt_pnl - base_pnl)
        total_delta = sum(deltas)
        sign = '+' if total_delta >= 0 else ''
        delta_cols = '  '.join(f"{'+' if d>=0 else ''}{d:,.0f}".rjust(9) for d in deltas)
        print(f"\n  Δ {cfg['name']}: {delta_cols}  {sign}{total_delta:,.2f}".rjust(12) + f" vs {CONFIGS[0]['name']}")

        consistent = all(d >= 0 for d in deltas)
        n_pos = sum(1 for d in deltas if d >= 0)
        if consistent:
            print(f"    ✅ Consistent improvement every week — safe to commit")
        elif n_pos >= n_weeks - 1:
            print(f"    ⚠️  Improvement in {n_pos}/{n_weeks} weeks — review losing week")
        else:
            print(f"    ❌ Inconsistent — do not commit yet")

    print(f"\n{'='*80}\n")


if __name__ == '__main__':
    main()
