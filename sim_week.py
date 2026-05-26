# sim_week.py — simulate the most recent completed trading week across key momentum stocks
# Command: venv/bin/python sim_week.py
#          venv/bin/python sim_week.py NVDA PLTR MSFT AAPL

import sys
import io
import re
import contextlib
from datetime import date

import yfinance as yf
import sim_today

# ── Week to simulate (last 5 trading days with market data) ──────────────────

def _last_5_trading_days():
    spy = yf.Ticker('SPY').history(period='30d', interval='1d')
    return sorted(d.date() for d in spy.index)[-5:]

WEEK_DAYS = _last_5_trading_days()

DEFAULT_SYMBOLS = [
    'NVDA', 'PLTR', 'MSFT', 'AAPL', 'TSLA',
    'IONQ', 'HOOD', 'AMD',
    'AVGO', 'META', 'AMZN', 'SOUN', 'RKLB',
    'OKLO', 'SMCI', 'CRM',
]

SYMBOLS = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_SYMBOLS


# ── Parse captured output ─────────────────────────────────────────────────────

def parse_output(text, symbol, sim_date):
    result = {
        'symbol':      symbol,
        'date':        sim_date,
        'entered':     False,
        'grade':       None,
        'catalyst':    False,
        'entry_px':    None,
        'entry_time':  None,
        'capital':     None,
        'outcome':     'SKIP',
        'pnl_pct':     None,
        'pnl_usd':     None,
        'exit_reason': None,
    }

    # Entry: ▶ HH:MM  $123.45  ENTER  Grade A+ ⚡CATALYST  score=95  capital=$1,500
    m = re.search(r'▶\s+(\d+:\d+)\s+\$([0-9.]+)\s+ENTER\s+Grade\s+(\S+)(.*?)capital=\$([0-9,]+)', text)
    if m:
        result['entered']    = True
        result['entry_time'] = m.group(1)
        result['entry_px']   = float(m.group(2))
        result['grade']      = m.group(3)
        result['catalyst']   = '⚡' in m.group(4)
        result['capital']    = int(m.group(5).replace(',', ''))

    # Exit P&L: → ✅ WIN  +2.05%  $+246.00  or  → ❌ LOSS  -1.50%  $-180.00
    m = re.search(r'→\s+(✅ WIN|❌ LOSS)\s+([+-][0-9.]+)%\s+\$([+-][0-9.]+)', text)
    if m:
        result['outcome']  = 'WIN' if '✅' in m.group(1) else 'LOSS'
        result['pnl_pct']  = float(m.group(2))
        result['pnl_usd']  = float(m.group(3))

    # Exit reason: ■ HH:MM  $price  EXIT  <reason>
    m = re.search(r'■\s+\d+:\d+\s+\$[0-9.]+\s+EXIT\s+(.+)', text)
    if m:
        result['exit_reason'] = m.group(1).strip()

    # Still open: ⏳ STILL OPEN … $+pnl
    if '⏳ STILL OPEN' in text:
        result['outcome'] = 'OPEN'
        after = text.split('STILL OPEN', 1)[-1]
        m = re.search(r'([+-][0-9.]+)%', after)
        if m:
            result['pnl_pct'] = float(m.group(1))
        m = re.search(r'\$([+-][0-9.]+)', after)
        if m:
            result['pnl_usd'] = float(m.group(1))

    # Propagate entered state to outcome
    if result['entered'] and result['outcome'] == 'SKIP':
        result['outcome'] = 'OPEN'   # entry but no exit = still open

    return result


def fmt_pnl(usd):
    if usd is None:
        return '     n/a'
    sign = '+' if usd >= 0 else ''
    return f'{sign}${usd:,.2f}'


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_week():
    all_trades  = []
    day_summary = []

    week_label = f"{WEEK_DAYS[0].strftime('%b %d')}–{WEEK_DAYS[-1].strftime('%b %d, %Y')}"
    print(f"\n{'='*70}")
    print(f"  WEEKLY SIMULATION  —  {week_label}")
    print(f"  Universe ({len(SYMBOLS)}): {', '.join(SYMBOLS)}")
    print(f"{'='*70}")

    for sim_date in WEEK_DAYS:
        print(f"\n\n{'━'*70}")
        print(f"  {sim_date.strftime('%A %b %d, %Y')}")
        print(f"{'━'*70}")

        sim_today.SIM_DATE = sim_date

        try:
            print(f"  Fetching regime for {sim_date}...", end=' ', flush=True)
            regime, spy_chg, vix = sim_today.get_regime()
            print(f"done")
        except Exception as e:
            print(f"\n  ⚠️  Regime fetch failed: {e}")
            regime, spy_chg, vix = 'NORMAL', 0.0, 18.0

        print(f"  Regime: {regime} | SPY {spy_chg:+.1f}% | VIX {vix:.1f}")
        if regime in ('CHOPPY', 'WEAK'):
            print(f"  ⚠️  {regime} — live system skips new longs (catalyst override not modelled in sim)")
        print()

        day_trades = []
        for sym in SYMBOLS:
            print(f"  Scanning {sym}...", end='\r', flush=True)
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    sim_today.simulate(sym, regime, spy_chg)
            except Exception as e:
                buf.write(f"  ERROR: {e}\n")

            res = parse_output(buf.getvalue(), sym, sim_date)
            all_trades.append(res)

            if res['entered']:
                day_trades.append(res)
                if res['outcome'] == 'WIN':
                    tag = '✅'
                elif res['outcome'] == 'LOSS':
                    tag = '❌'
                else:
                    tag = '⏳'

                cat_str  = ' ⚡' if res['catalyst'] else '   '
                pnl_str  = fmt_pnl(res['pnl_usd'])
                pct_str  = f"{res['pnl_pct']:+.1f}%" if res['pnl_pct'] is not None else '?%'
                print(f"  {tag} {sym:<6}{cat_str} Grade {res['grade']:<3}  "
                      f"@{res['entry_time']} ${res['entry_px']:.2f}  "
                      f"Cap ${res['capital']:,}  →  {pnl_str:>9}  {pct_str:<7}  {res['outcome']}")
                if res['exit_reason']:
                    print(f"              Exit: {res['exit_reason']}")

        # Clear the last scanning line
        print(' ' * 60, end='\r')

        # Day totals
        wins    = [t for t in day_trades if t['outcome'] == 'WIN']
        losses  = [t for t in day_trades if t['outcome'] == 'LOSS']
        opens   = [t for t in day_trades if t['outcome'] == 'OPEN']
        day_pnl = sum(t['pnl_usd'] for t in day_trades if t['pnl_usd'] is not None)

        day_summary.append({
            'date':    sim_date,
            'regime':  regime,
            'spy_chg': spy_chg,
            'entered': len(day_trades),
            'wins':    len(wins),
            'losses':  len(losses),
            'opens':   len(opens),
            'pnl':     day_pnl,
        })

        if len(day_trades) == 0:
            print(f"  — No qualifying entries today")
        else:
            open_note = f"  {len(opens)} still open" if opens else ''
            print(f"\n  Day total: {len(day_trades)} entries | "
                  f"{len(wins)}W {len(losses)}L{open_note}  |  P&L {fmt_pnl(day_pnl)}")

    # ── Weekly summary table ─────────────────────────────────────────────────
    total_entered = sum(d['entered'] for d in day_summary)
    total_wins    = sum(d['wins']    for d in day_summary)
    total_losses  = sum(d['losses']  for d in day_summary)
    total_opens   = sum(d['opens']   for d in day_summary)
    total_pnl     = sum(d['pnl']     for d in day_summary)
    closed        = total_wins + total_losses
    wr            = total_wins / closed * 100 if closed > 0 else 0.0

    print(f"\n\n{'='*70}")
    print(f"  WEEKLY SUMMARY")
    print(f"{'='*70}")
    print(f"  {'Day':<14} {'Regime':<8} {'SPY':>6}  {'In':>4}  {'W/L':>7}  {'P&L':>10}")
    print(f"  {'-'*60}")
    for d in day_summary:
        open_tag = f"+{d['opens']}⏳" if d['opens'] else ''
        wl_str   = f"{d['wins']}W/{d['losses']}L {open_tag}"
        print(f"  {d['date'].strftime('%a %b %d'):<14} {d['regime']:<8} "
              f"{d['spy_chg']:>+5.1f}%  {d['entered']:>4}  {wl_str:<9}  {fmt_pnl(d['pnl']):>10}")
    print(f"  {'-'*60}")
    open_note = f" +{total_opens}⏳" if total_opens else ''
    wl_total  = f"{total_wins}W/{total_losses}L{open_note}"
    print(f"  {'TOTAL':<14} {'':>14}  {total_entered:>4}  {wl_total:<9}  {fmt_pnl(total_pnl):>10}")
    print()
    print(f"  Win rate  : {wr:.0f}%  ({total_wins}W / {closed} closed trades)")
    print(f"  Week P&L  : {fmt_pnl(total_pnl)}")
    print(f"  Avg/trade : {fmt_pnl(total_pnl / max(total_entered, 1))}")
    print(f"{'='*70}\n")


if __name__ == '__main__':
    run_week()
