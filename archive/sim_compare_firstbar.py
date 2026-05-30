"""
First-Bar Quality A/B Comparison
==================================
Runs the full universe sim_today on N recent trading days, twice per day —
once with FIRST_BAR_QUALITY=True and once with False — then compares P&L.

Usage:
    venv/bin/python sim_compare_firstbar.py
    venv/bin/python sim_compare_firstbar.py --days 5
"""

import subprocess, sys, re, os
from datetime import date
import yfinance as yf

BASE        = os.path.dirname(os.path.abspath(__file__))
SCRIPT      = os.path.join(BASE, 'sim_today.py')
AUTO_TRADER = os.path.join(BASE, 'auto_trader.py')
PYTHON      = os.path.join(BASE, 'venv', 'bin', 'python')

def load_full_universe():
    """Extract FULL_UNIVERSE symbol list from auto_trader.py without importing it."""
    with open(AUTO_TRADER) as f:
        src = f.read()
    m = re.search(r'FULL_UNIVERSE\s*=\s*list\(dict\.fromkeys\(\[(.*?)\]\)\)',
                  src, re.DOTALL)
    if not m:
        return []
    syms = re.findall(r"'([A-Z]+)'", m.group(1))
    return list(dict.fromkeys(syms))

# ── patch / restore helper ────────────────────────────────────────────────────

def set_first_bar_flag(enabled: bool):
    """Directly patch FIRST_BAR_QUALITY in sim_today.py (saves/restores)."""
    with open(SCRIPT) as f:
        src = f.read()
    patched = re.sub(
        r'^(FIRST_BAR_QUALITY\s*=\s*)(True|False)',
        f'\\g<1>{"True" if enabled else "False"}',
        src, flags=re.MULTILINE
    )
    with open(SCRIPT, 'w') as f:
        f.write(patched)

def get_current_flag():
    with open(SCRIPT) as f:
        src = f.read()
    m = re.search(r'^FIRST_BAR_QUALITY\s*=\s*(True|False)', src, re.MULTILINE)
    return m.group(1) == 'True' if m else True

# ── run one sim ───────────────────────────────────────────────────────────────

def run_sim(sim_date, symbols):
    """Run sim_today --date SYMBOLS and parse WIN/LOSS lines. Returns (pnl, trades, wins)."""
    result = subprocess.run(
        [PYTHON, SCRIPT, '--date', sim_date] + symbols,
        capture_output=True, text=True, cwd=BASE, timeout=600
    )
    output = result.stdout + result.stderr

    total_pnl = 0.0
    wins = 0; losses = 0

    for line in output.splitlines():
        # Format: "→ ✅ WIN  +2.19%  $+37.80"  or "→ ❌ LOSS  -3.45%  $-48.30"
        m = re.search(r'(WIN|LOSS)\s+[+-][\d.]+%\s+\$([+-][\d,.]+)', line)
        if m:
            try:
                usd = float(m.group(2).replace(',', ''))
                total_pnl += usd
                if m.group(1) == 'WIN': wins += 1
                else:                   losses += 1
            except ValueError:
                pass

    return total_pnl, wins + losses, wins

# ── main ──────────────────────────────────────────────────────────────────────

def get_recent_trading_days(n):
    spy  = yf.Ticker('SPY').history(period='30d', interval='1d')
    return [str(d.date()) for d in sorted(spy.index, reverse=True)[:n]]

def main():
    n_days = 10
    if '--days' in sys.argv:
        n_days = int(sys.argv[sys.argv.index('--days') + 1])

    days    = get_recent_trading_days(n_days)
    symbols = load_full_universe()
    if not symbols:
        print("ERROR: could not extract FULL_UNIVERSE from auto_trader.py")
        sys.exit(1)
    print(f"  Universe: {len(symbols)} symbols")

    original_flag = get_current_flag()   # remember to restore

    print(f"\n{'='*76}")
    print(f"  FIRST-BAR QUALITY A/B — {len(days)} trading days")
    print(f"  BASELINE: FIRST_BAR_QUALITY=False  |  NEW: FIRST_BAR_QUALITY=True")
    print(f"{'='*76}")
    print(f"  {'Date':12s} | {'Baseline':>10s} | {'     New':>10s} | {'Diff':>8s} | {'T_base':>7s} | {'T_new':>7s}")
    print(f"  {'-'*72}")

    rows = []
    try:
        for d in days:
            # Baseline
            set_first_bar_flag(False)
            pnl_b, t_b, w_b = run_sim(d, symbols)

            # New
            set_first_bar_flag(True)
            pnl_n, t_n, w_n = run_sim(d, symbols)

            rows.append((d, pnl_b, t_b, w_b, pnl_n, t_n, w_n))
    finally:
        set_first_bar_flag(original_flag)   # always restore

    total_base = total_new = 0.0
    days_better = days_worse = 0

    for d, pnl_b, t_b, w_b, pnl_n, t_n, w_n in rows:
        diff = pnl_n - pnl_b
        total_base += pnl_b
        total_new  += pnl_n
        if diff >  1.0: days_better += 1
        if diff < -1.0: days_worse  += 1
        sig = '▲' if diff > 1 else ('▼' if diff < -1 else '~')
        tb  = f"{t_b}T {w_b}W" if t_b else "—"
        tn  = f"{t_n}T {w_n}W" if t_n else "—"
        print(f"  {d:12s} | ${pnl_b:+8.2f} | ${pnl_n:+8.2f} | {diff:+7.2f} | {tb:>7s} | {tn:>7s}  {sig}")

    print(f"  {'-'*72}")
    total_diff = total_new - total_base
    avg_lift   = total_diff / len(rows) if rows else 0
    print(f"  {'TOTAL':12s} | ${total_base:+8.2f} | ${total_new:+8.2f} | {total_diff:+7.2f}")
    print(f"  Days better: {days_better}  |  Days worse: {days_worse}  |  Days same: {len(rows)-days_better-days_worse}")
    print(f"  Avg lift/day: ${avg_lift:+.2f}")
    print(f"\n  VERDICT: ", end='')
    if total_diff > 50 and days_better > days_worse:
        print(f"✅ BUILD IT  (${total_diff:+.2f} over {len(rows)} days, {days_better}/{len(rows)} days better)")
    elif total_diff > 0 and days_better >= days_worse:
        print(f"⚠️  MARGINAL  (${total_diff:+.2f}) — monitor 2 more weeks live before committing")
    else:
        print(f"❌ NO IMPROVEMENT  (${total_diff:+.2f}) — keep current system unchanged")
    print(f"{'='*76}\n")

if __name__ == '__main__':
    main()
