"""
Futures Proving Ground — the "test bench" every futures engine must pass before
it is trusted with real (TopStep) money.

Doctrine (shared with the equity factory/, NOT its code):
  1. OUT-OF-SAMPLE  — grade per calendar year; an engine must earn its keep in
                      years it was not tuned on, not just in aggregate.
  2. STRIP THE NOISE — we score the PURE-AUTOMATED sim output (sim_replay /
                      london_v2_sim). There is no manual hand in the sim, so the
                      user's discretionary closes are excluded for free.
  3. RIGHT RULER    — expectancy + Sharpe + per-year consistency + give-back,
                      NOT peak-day P&L.
  4. PROP-REALISTIC — the whole automated stream is walked through the real
                      TopStep $50k rules (DLL / trailing MLL / consistency) and we
                      report P(pass eval) and P(blow) via day-bootstrap.

This file drives the EXISTING, validated sims — it does not re-implement strategy
logic. The NY config below is pinned to live parity (parity_check.SIM_FLAGS).

Run:
  venv/bin/python -m futures.factory.bench --start 2021-01-04 --end 2026-08-14
  venv/bin/python -m futures.factory.bench --start 2026-01-01 --end 2026-08-14   # fast
"""
from __future__ import annotations
import argparse
import datetime as _dt
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── TopStep $50k combine rules (mirror prop_rules.py; the bench applies them as a
#    post-processor because the sim itself does not know about them) ─────────────
TC_PROFIT_TARGET = 3_000.0
TC_MLL           = 2_000.0   # trailing max loss limit; trail LOCKS at start balance
TC_DLL           = 1_000.0   # daily loss limit
TC_CONSISTENCY   = 0.50      # best day must be <= 50% of total profit at pass time

DOLLARS_PER_PT_MNQ = 2.0     # informational; sim already returns dollar pnl


# ══════════════════════════════════════════════════════════════════════════════
#  ENGINE ADAPTERS — run the real sims, return a tagged trade frame
# ══════════════════════════════════════════════════════════════════════════════
# Conservative, model-based per-contract round-trip friction (commission + spread +
# a slippage assumption). Empirical execution calibration needs more matched live
# pairs (only 15 available Aug 2026 — too noisy); until then we haircut mechanically.
FRICTION_PER_CONTRACT = 6.0


def run_ny(start: str, end: str, rev_exit=(2, 0.30, 120.0), partial=150.0,
           max_trades=5, friction=FRICTION_PER_CONTRACT) -> pd.DataFrame:
    """Drive sim_replay with the exact live-parity config and return the trade
    frame (cols incl. date, entry_time, side, setup, contracts, pnl, exit_reason).
    rev_exit/partial/max_trades are exposed so the Proving Ground can sweep exits.
    `friction` $/contract round-trip is subtracted from every trade's P&L."""
    import futures.sim_replay as srp

    # Pin to parity_check.SIM_FLAGS (the definition of "matches live"):
    #   --graduated-rvol --rvol-floor 0.70 --regime-aware-exits --stop-pts 200
    #   --no-ovn-skip --rev-exit 2,0.30,120 --partial 150   (max-trades 5, hero ON)
    srp.GRADUATED_RVOL   = True
    srp.RVOL_GRAD_FLOOR  = 0.70
    srp.REGIME_AWARE_EXITS = True
    srp.BASE_STOP_PTS    = 200.0
    srp.NO_OVN_SKIP      = True
    srp.REV_EXIT         = tuple(rev_exit) if rev_exit else None
    srp.PARTIAL_TAKE_PTS = partial
    srp.MAX_DAILY_TRADES = max_trades
    srp.HERO_GATE_ENABLED = True

    start_dt   = _dt.date.fromisoformat(start)
    load_start = (start_dt - _dt.timedelta(days=110)).isoformat()   # 50d-MA warmup
    end_cutoff = (_dt.date.fromisoformat(end) + _dt.timedelta(days=1)).isoformat()
    all_bars   = srp.load_bars('MNQ', start=load_start, end=end_cutoff)
    rth_all    = srp.filter_ny_session(all_bars)
    trade_dates = sorted(d for d in set(rth_all.index.date) if d >= start_dt)

    r = srp._run_scenario(all_bars, trade_dates, False, False, 'NY', verbose=False)
    df = r['df']
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    if friction:
        df['pnl'] = df['pnl'] - friction * df['contracts'].clip(lower=1)   # execution haircut
    df['session'] = 'NY'
    df['engine']  = df['setup']
    df['dt']      = pd.to_datetime(df['date'].astype(str) + ' ' + df['entry_time'].astype(str),
                                   errors='coerce')
    return df


def run_london(start: str, end: str) -> pd.DataFrame:
    """Drive london_v2_sim (1-min) with champion config; return tagged trade frame."""
    try:
        import futures.london_v2_sim as lv2
    except Exception as e:
        print(f'  [london] adapter unavailable: {e}')
        return pd.DataFrame()
    cfg = {'be_mult': 0.10, 'vol_confirm': False, 'acceptance': False,
           'skip_day': False, 'rev_exit': None}
    try:
        bars = lv2.load_1m(start, end) if hasattr(lv2, 'load_1m') else None
        df = lv2.simulate(bars, cfg, start) if bars is not None else pd.DataFrame()
    except Exception as e:
        print(f'  [london] sim call failed ({e}); London leg skipped this run')
        return pd.DataFrame()
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df = df.copy()
    df['session'] = 'LONDON'
    df['engine']  = 'LONDON_' + df['side'].astype(str)
    df['date']    = pd.to_datetime(df.get('date', pd.NaT), errors='coerce')
    return df


# ══════════════════════════════════════════════════════════════════════════════
#  METRICS
# ══════════════════════════════════════════════════════════════════════════════
def _sharpe_daily(day_pnls: np.ndarray) -> float:
    if len(day_pnls) < 5 or day_pnls.std(ddof=1) == 0:
        return 0.0
    return float(day_pnls.mean() / day_pnls.std(ddof=1) * np.sqrt(252))


def _max_dd(pnls: np.ndarray) -> float:
    cum = np.cumsum(pnls)
    return float((cum - np.maximum.accumulate(cum)).min()) if len(cum) else 0.0


def engine_stats(df: pd.DataFrame) -> dict:
    n = len(df)
    if n == 0:
        return dict(n=0, wr=0, total=0, avg=0, dd=0, sharpe=0, years={})
    pnl = df['pnl'].to_numpy()
    total = float(pnl.sum())
    wr = float((pnl > 0).mean() * 100)
    # daily aggregation for sharpe
    day = df.groupby(df['date'].astype(str))['pnl'].sum().to_numpy()
    years = {}
    yr = pd.to_datetime(df['date'].astype(str), errors='coerce').dt.year
    for y in sorted(set(yr.dropna())):
        yp = pnl[(yr == y).to_numpy()]
        years[int(y)] = (len(yp), float(yp.sum()), float(yp.mean()))
    return dict(n=n, wr=wr, total=total, avg=total / n,
                dd=_max_dd(pnl), sharpe=_sharpe_daily(day), years=years)


def verdict(st: dict) -> str:
    """PASS / WATCH / FAIL per the doctrine."""
    if st['n'] < 20:
        return 'THIN'
    yrs = st['years']
    if not yrs:
        return 'THIN'
    pos_years = sum(1 for (_, tot, _) in yrs.values() if tot > 0)
    last_year = max(yrs)
    last_avg  = yrs[last_year][2]
    frac_pos  = pos_years / len(yrs)
    if st['avg'] <= 0 or last_avg <= 0 or frac_pos < 0.5:
        return 'FAIL'
    if st['avg'] > 0 and frac_pos >= 0.6 and st['sharpe'] > 0.5:
        return 'PASS'
    return 'WATCH'


# ══════════════════════════════════════════════════════════════════════════════
#  THE TOPSTEP GAUNTLET (prop-constraint simulator)
# ══════════════════════════════════════════════════════════════════════════════
def daily_pnls_with_dll(df: pd.DataFrame, dll: float = TC_DLL) -> list[tuple[str, float]]:
    """Collapse trades to per-day P&L, applying the DLL halt the sim didn't model:
    once a day's cumulative loss <= -dll, the trader is halted — later trades that
    day are dropped."""
    out = []
    for date_str, g in df.sort_values(['date', 'entry_time']).groupby(df['date'].astype(str)):
        cum = 0.0
        halted = False
        for p in g['pnl']:
            if halted:
                continue
            cum += p
            if cum <= -dll:
                cum = -dll          # halted at the limit
                halted = True
        out.append((date_str, cum))
    return out


def walk_eval(day_pnls: list[float],
              target=TC_PROFIT_TARGET, mll=TC_MLL, consistency=TC_CONSISTENCY) -> dict:
    """Walk a sequence of daily P&Ls through the trailing-MLL eval. Returns outcome."""
    cum = 0.0
    hwm = 0.0
    best_day = -1e9
    for i, dp in enumerate(day_pnls):
        cum += dp
        best_day = max(best_day, dp)
        hwm = max(hwm, cum)
        floor = min(hwm - mll, 0.0)      # trailing MLL locks at start balance (pnl=0)
        if cum <= floor:
            return dict(outcome='BLOWN_MLL', day=i + 1, pnl=cum)
        if cum >= target:
            # consistency: best single day must be <= 50% of total profit
            if best_day > consistency * cum:
                return dict(outcome='CONSISTENCY_FAIL', day=i + 1, pnl=cum,
                            best_share=best_day / cum)
            return dict(outcome='PASSED', day=i + 1, pnl=cum)
    return dict(outcome='RUNNING', day=len(day_pnls), pnl=cum)


def monte_carlo_eval(day_pnls: list[float], horizon=60, n=3000, seed=7) -> dict:
    """Bootstrap synthetic evals by resampling whole days (preserves DLL-shaped
    daily distribution). Report P(pass), P(blow), P(still running at horizon)."""
    if len(day_pnls) < 10:
        return dict(n=0)
    rng = np.random.default_rng(seed)
    arr = np.array(day_pnls)
    outc = defaultdict(int)
    days_to_pass = []
    for _ in range(n):
        seq = rng.choice(arr, size=horizon, replace=True)
        res = walk_eval(list(seq))
        outc[res['outcome']] += 1
        if res['outcome'] == 'PASSED':
            days_to_pass.append(res['day'])
    return dict(n=n, horizon=horizon,
                p_pass=outc['PASSED'] / n,
                p_blow=outc['BLOWN_MLL'] / n,
                p_consist=outc['CONSISTENCY_FAIL'] / n,
                p_running=outc['RUNNING'] / n,
                med_days_to_pass=(int(np.median(days_to_pass)) if days_to_pass else None))


# ══════════════════════════════════════════════════════════════════════════════
#  SCORECARD
# ══════════════════════════════════════════════════════════════════════════════
def scorecard(df: pd.DataFrame, label: str):
    print(f'\n{"="*84}')
    print(f'  PROVING GROUND SCORECARD — {label}')
    print(f'{"="*84}')
    if len(df) == 0:
        print('  (no trades)')
        return

    all_st = engine_stats(df)
    print('  Window trades: {}   WR {:.0f}%   Total ${:+,.0f}   Expectancy ${:+.1f}/trade'.format(
        all_st['n'], all_st['wr'], all_st['total'], all_st['avg']))
    print('  Account Sharpe (daily, annualized): {:.2f}   MaxDD ${:+,.0f}'.format(
        all_st['sharpe'], all_st['dd']))

    # per-year account line
    print(f'\n  {"By year":<10}', end='')
    for y, (nn, tot, avg) in sorted(all_st['years'].items()):
        print(f'  {y}:${tot:+,.0f}({nn}t,${avg:+.0f})', end='')
    print()

    # per-engine table
    print(f'\n  {"ENGINE":<16}{"n":>6}{"WR":>6}{"Total":>11}{"Exp/t":>9}{"Sharpe":>8}{"MaxDD":>10}   verdict')
    print(f'  {"-"*82}')
    rows = []
    for eng, g in df.groupby('engine'):
        st = engine_stats(g)
        rows.append((eng, st))
    for eng, st in sorted(rows, key=lambda r: -r[1]['total']):
        v = verdict(st)
        print(f'  {eng:<16}{st["n"]:>6}{st["wr"]:>5.0f}%{st["total"]:>+11,.0f}'
              f'{st["avg"]:>+9.1f}{st["sharpe"]:>8.2f}{st["dd"]:>+10,.0f}   {v}')

    # per-engine per-year (the OOS honesty check)
    print("\n  PER-ENGINE PER-YEAR expectancy $/trade "
          "(OOS honesty — must hold up in years it wasn't tuned on):")
    years_all = sorted({y for _, st in rows for y in st['years']})
    hdr = '  ' + f'{"engine":<16}' + ''.join(f'{y:>10}' for y in years_all)
    print(hdr)
    for eng, st in sorted(rows, key=lambda r: -r[1]['total']):
        line = '  ' + f'{eng:<16}'
        for y in years_all:
            if y in st['years']:
                _, tot, avg = st['years'][y]
                line += f'{avg:>+10.1f}'
            else:
                line += f'{"-":>10}'
        print(line)


def giveback_report(df: pd.DataFrame):
    """Reconstruct max-favorable-excursion (MFE) per trade at the sim's own 5-min
    granularity, and quantify give-back. Directly tests the belief 'if we stop
    giving back we make more' by separating:
      • ROUND-TRIPS  — reached a real peak (>=100pts) then ended <=0  (capturable pain)
      • RUNNER PULLBACKS — reached a peak, gave most back, but still won (NOT capturable
                           without killing the runner — this is the right-tail premium)
    """
    import futures.sim_replay as srp
    if len(df) == 0:
        print('  (no trades)'); return
    dmin = pd.to_datetime(df['date'].astype(str)).min().date()
    dmax = pd.to_datetime(df['date'].astype(str)).max().date()
    bars = srp.filter_ny_session(srp.load_bars(
        'MNQ', start=(dmin - _dt.timedelta(days=3)).isoformat(),
        end=(dmax + _dt.timedelta(days=1)).isoformat()))

    recs = []
    for _, t in df.iterrows():
        try:
            d = pd.to_datetime(str(t['date'])).date()
            e_h, e_m = map(int, str(t['entry_time']).split(':'))
            x_h, x_m = map(int, str(t['exit_time']).split(':'))
        except Exception:
            continue
        win = bars[(bars.index.date == d)
                   & (bars.index.time >= _dt.time(e_h, e_m))
                   & (bars.index.time <= _dt.time(x_h, x_m))]
        if len(win) == 0:
            continue
        entry = float(t['entry'])
        if str(t['side']) == 'LONG':
            mfe = float(win['high'].max()) - entry
            realized = float(t['exit']) - entry
        else:
            mfe = entry - float(win['low'].min())
            realized = entry - float(t['exit'])
        recs.append(dict(engine=t['engine'], mfe=mfe, realized=realized,
                         giveback=max(0.0, mfe - realized), pnl=float(t['pnl'])))
    if not recs:
        print('  (no MFE reconstructable)'); return
    r = pd.DataFrame(recs)

    print(f'\n{"="*84}')
    print('  GIVE-BACK / PEAK-EXIT ANALYSIS  (points per contract, 5-min granularity)')
    print(f'{"="*84}')
    print('  {:<14}{:>6}{:>9}{:>10}{:>10}{:>9}'.format(
        'ENGINE', 'n', 'avgMFE', 'avgKept', 'capture%', 'giveBk'))
    print('  ' + '-' * 72)
    for eng, g in sorted(r.groupby('engine'), key=lambda kv: -kv[1]['giveback'].sum()):
        pos = g[g['mfe'] > 0]
        cap = (pos['realized'].sum() / pos['mfe'].sum() * 100) if len(pos) and pos['mfe'].sum() > 0 else 0
        print('  {:<14}{:>6}{:>9.0f}{:>10.0f}{:>9.0f}%{:>9.0f}'.format(
            eng, len(g), g['mfe'].mean(), g['realized'].mean(), cap, g['giveback'].mean()))

    # The belief test: how much give-back is capturable (round-trips) vs runner premium?
    big = r[r['mfe'] >= 100]                       # reached a genuine peak
    roundtrip = big[big['pnl'] <= 0]               # ...and threw it all away → LOSS
    runner_pb = big[(big['pnl'] > 0) & (big['realized'] < 0.4 * big['mfe'])]  # kept but <40%
    clean_win = big[(big['pnl'] > 0) & (big['realized'] >= 0.4 * big['mfe'])]
    print(f'\n  Of {len(big)} trades that reached a real +100pt peak:')
    print('    • ROUND-TRIPPED to a LOSS : {:>3}  ({:.0f}% of peaks)  ← capturable pain, ${:,.0f} of peak thrown away'.format(
        len(roundtrip), 100*len(roundtrip)/len(big) if len(big) else 0,
        roundtrip['mfe'].sum() * DOLLARS_PER_PT_MNQ))
    print('    • RUNNER PULLBACK (won <40% of peak): {:>3}  ← the right-tail premium, NOT free to cut'.format(len(runner_pb)))
    print('    • CLEAN CAPTURE (kept >=40%): {:>3}'.format(len(clean_win)))
    print('\n  READ: cutting give-back only helps if ROUND-TRIPS dominate. If runner-pullbacks')
    print('        dominate, a faster exit kills winners (matches the Jul-25 finding).')


def topstep_report(df: pd.DataFrame):
    print(f'\n{"="*84}')
    print(f'  THE TOPSTEP $50k GAUNTLET (automated stream only — no manual hand)')
    print(f'{"="*84}')
    dp = daily_pnls_with_dll(df)
    vals = [v for _, v in dp]
    if not vals:
        print('  (no days)'); return
    arr = np.array(vals)
    print('  Trading days: {}   Green days: {:.0f}%   Mean/day ${:+.0f}   Worst ${:+.0f}   Best ${:+.0f}'.format(
        len(vals), (arr > 0).mean() * 100, arr.mean(), arr.min(), arr.max()))
    dll_days = int((arr <= -TC_DLL + 0.01).sum())
    print('  Days that hit the $1k DLL halt: {}   Days > $1.2k soft cap: {}'.format(
        dll_days, int((arr >= 1200).sum())))
    # historical straight-through walk
    hist = walk_eval(vals)
    print('\n  Straight historical walk: {}  (day {}, cum ${:+,.0f})'.format(
        hist['outcome'], hist['day'], hist['pnl']))
    # bootstrap
    mc = monte_carlo_eval(vals)
    if mc.get('n'):
        print('\n  BOOTSTRAP over a {}-day eval ({} sims):'.format(mc['horizon'], mc['n']))
        print('     P(PASS $3k target)      : {:5.1f}%   (median {} days when it passes)'.format(
            mc['p_pass'] * 100, mc['med_days_to_pass']))
        print('     P(BLOW trailing $2k MLL): {:5.1f}%'.format(mc['p_blow'] * 100))
        print('     P(consistency fail)     : {:5.1f}%'.format(mc['p_consist'] * 100))
        print('     P(still running @ {}d) : {:5.1f}%'.format(mc['horizon'], mc['p_running'] * 100))
        ok = mc['p_pass'] > 2 * mc['p_blow'] and mc['p_pass'] > 0.4
        print('\n  READ: a fundable engine wants P(pass) well above P(blow).  {}'.format(
            '✅' if ok else '⚠️  NOT THERE YET'))


# ══════════════════════════════════════════════════════════════════════════════
def rev_exit_sweep(start: str, end: str):
    """Test the peak-exit belief the RIGHT way: sweep Reversal-Exit params and measure
    whether ANY setting raises capture without cutting the right tail. Baseline is live
    (2,0.30,120). Lower peak_min / retrace = exit earlier (protect peak); we watch what
    it does to total, Sharpe, MaxDD across the whole window."""
    print(f'\n{"="*94}')
    print(f'  REVERSAL-EXIT SWEEP  {start}→{end}   (does cutting give-back help? — measured, not believed)')
    print(f'{"="*94}')
    configs = [
        ('OFF (ride to stop/EOD)', None),
        ('live  2,0.30,120',       (2, 0.30, 120)),
        ('earlier 2,0.30,90',      (2, 0.30, 90)),
        ('tighter 2,0.25,120',     (2, 0.25, 120)),
        ('faster  1,0.30,120',     (1, 0.30, 120)),
        ('aggressive 1,0.20,80',   (1, 0.20, 80)),
        ('patient 2,0.40,150',     (2, 0.40, 150)),
    ]
    print('  {:<24}{:>7}{:>7}{:>11}{:>9}{:>8}{:>11}'.format(
        'rev-exit config', 'n', 'WR', 'Total', 'Exp/t', 'Sharpe', 'MaxDD'))
    print('  ' + '-' * 84)
    for label, rc in configs:
        df = run_ny(start, end, rev_exit=rc)
        st = engine_stats(df)
        print('  {:<24}{:>7}{:>6.0f}%{:>+11,.0f}{:>+9.1f}{:>8.2f}{:>+11,.0f}'.format(
            label, st['n'], st['wr'], st['total'], st['avg'], st['sharpe'], st['dd']))
    print('\n  READ: if "OFF" or "patient" ≈ best total/Sharpe, give-back is the tail premium')
    print('        (cutting it loses). If an "earlier/tighter" row wins, there IS capturable juice.')


def green_light_analysis(start: str, end: str):
    """Validate the Green-Light Extender BEFORE any live wiring: does allowing more
    trades/day pay, and specifically are the later trades (#3+) still +EV — and even
    more so AFTER 2 prior wins that day (the conditional the user proposed)? Answered
    on the sim's own multi-year output, per year (OOS), NOT on the manual-contaminated
    live data."""
    print(f'\n{"="*90}')
    print(f'  GREEN-LIGHT EXTENDER — bench validation  {start}→{end}')
    print(f'{"="*90}')

    # (1) max-trades/day sweep — is there aggregate juice in more trades?
    print('\n  (1) Max-trades/day sweep (friction-adjusted):')
    print('  {:<16}{:>7}{:>11}{:>9}{:>8}{:>11}'.format('cap', 'n', 'Total', 'Exp/t', 'Sharpe', 'MaxDD'))
    print('  ' + '-' * 62)
    df8 = None
    for mt in [2, 3, 5, 8]:
        df = run_ny(start, end, max_trades=mt)
        if mt == 8:
            df8 = df
        st = engine_stats(df)
        print('  {:<16}{:>7}{:>+11,.0f}{:>+9.1f}{:>8.2f}{:>+11,.0f}'.format(
            f'{mt}/day', st['n'], st['total'], st['avg'], st['sharpe'], st['dd']))

    if df8 is None or len(df8) == 0:
        return
    # (2) within-day RANK P&L (does the Nth trade of the day still earn?)
    df8 = df8.sort_values(['date', 'entry_time']).copy()
    df8['rank'] = df8.groupby('date').cumcount() + 1
    print('\n  (2) P&L by within-day trade RANK (cap=8, the honest OOS version):')
    print('  {:<10}{:>7}{:>7}{:>11}{:>9}'.format('rank', 'n', 'WR', 'Total', 'Exp/t'))
    print('  ' + '-' * 44)
    for rk, g in df8.groupby('rank'):
        wr = (g['pnl'] > 0).mean() * 100
        print('  #{:<9}{:>7}{:>6.0f}%{:>+11,.0f}{:>+9.1f}'.format(
            rk, len(g), wr, g['pnl'].sum(), g['pnl'].mean()))

    # (3) the CONDITIONAL rule: trade #3+ AFTER 2 prior wins that day, per year
    print('\n  (3) Conditional Green-Light: trade #3+ split by prior-2-that-day, PER YEAR (OOS):')
    rows_after_win, rows_after_notwin = [], []
    for _, g in df8.groupby('date'):
        pnls = g['pnl'].tolist()
        for i in range(2, len(pnls)):
            after_2_green = pnls[i - 1] > 0 and pnls[i - 2] > 0
            (rows_after_win if after_2_green else rows_after_notwin).append(
                (str(g['date'].iloc[0])[:4], pnls[i]))
    def _summ(rows, label):
        if not rows:
            print(f'    {label}: (none)'); return
        d = defaultdict(list)
        for y, p in rows:
            d[y].append(p)
        tot = sum(p for _, p in rows)
        line = f'    {label:<26} overall n={len(rows)} ${tot:+,.0f}  |  '
        line += '  '.join(f'{y}:${sum(v):+,.0f}(n{len(v)})' for y, v in sorted(d.items()))
        print(line)
    _summ(rows_after_win,    'trade#3+ AFTER 2 wins')
    _summ(rows_after_notwin, 'trade#3+ after mixed/loss')
    print('\n  READ: Green-Light is worth wiring (as a live shadow) only if "AFTER 2 wins"')
    print('        is +EV and holds up across years — not just in aggregate.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2024-01-02')
    ap.add_argument('--end',   default='2026-08-14')
    ap.add_argument('--london', action='store_true', help='include London leg')
    ap.add_argument('--ny-only', action='store_true')
    ap.add_argument('--giveback', action='store_true', help='run peak-exit / give-back analysis')
    ap.add_argument('--rev-sweep', action='store_true', help='sweep Reversal-Exit params')
    ap.add_argument('--green-light', action='store_true', help='validate Green-Light Extender')
    args = ap.parse_args()

    if args.rev_sweep:
        rev_exit_sweep(args.start, args.end)
        return
    if args.green_light:
        green_light_analysis(args.start, args.end)
        return

    print(f'\n Futures Proving Ground  |  {args.start} → {args.end}')
    print(' Loading NY engine (sim_replay, live-parity config)…')
    ny = run_ny(args.start, args.end)
    scorecard(ny, f'NY engines  {args.start}→{args.end}')
    topstep_report(ny)
    if args.giveback:
        giveback_report(ny)

    if args.london and not args.ny_only:
        print('\n Loading London engine…')
        ldn = run_london(args.start, args.end)
        scorecard(ldn, f'London engines  {args.start}→{args.end}')

    print()


if __name__ == '__main__':
    main()
