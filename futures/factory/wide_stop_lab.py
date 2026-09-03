"""
WIDE-STOP LAB — the user's design, tested on the tape.

The design (user, Aug 19 2026):
  * ONE contract at a time, always.
  * A WIDE fixed stop (1000pt) on EVERY trade, set at entry, to absorb turbulence.
  * The EXISTING entry mechanism, unchanged — no MA50 day filter. Same signals on
    IBKR NY, TC NY and London.
  * The EXIT is what we derive from data: if the tape usually delivers 150pts, take 150;
    if it delivers 200+, take that.
  * Flat same day, so a TIME rule replaces the wide stop as the real loss-cutter: if the
    target has not been hit by X minutes, book whatever is there — that day's entry was wrong.

Why the cached entries are valid for this what-if (checked, not assumed): hero_score
.contracts_from_regime_score() skips on the score alone; `calc_contracts_result` only caps
the GOLD tier at min(2, cc). Widening the stop takes cc from 2 to 1, which removes no
entries — it only makes gold trades 1 contract, which is the design anyway. So the 949
cached NY entries are the same entries a 1000pt-stop pipeline would produce.

⚠️ RISK ARITHMETIC, stated up front. MNQ is $2/point:
     1000pt = $2,000/contract = 100% of TC's $2,000 trailing MLL (breaches once the $300
     SOFT_STOP_BUFFER applies) and 200% of the $1,000 DLL. IBKR soft DLL is $1,250 = 625pt.
   Widths that actually fit: TC DLL 500pt · IBKR DLL 625pt · TC MLL-with-buffer 850pt.
   The lab therefore sweeps the whole stop family, not just 1000.

Run: venv/bin/python -m futures.factory.wide_stop_lab --describe --grid --stops --london
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAC         = os.path.join(ROOT, 'futures', 'factory')
POINT_VALUE = 2.0
COMMISSION  = 1.24
FRICTION    = 6.0
EOD         = _dt.time(15, 10)


def ny_entries() -> pd.DataFrame:
    d = pd.read_csv(os.path.join(FAC, '_mom_2021-06-01_2026-08-14.csv'))
    d['date'] = d['date'].astype(str)
    d['y'] = pd.to_datetime(d['date']).dt.year
    return d.sort_values(['date', 'entry_time']).reset_index(drop=True)


def bars_1m():
    from futures.factory.scalp_lab import load_1m
    return load_1m()


def _paths(entries: pd.DataFrame, bars) -> list:
    """For each entry, the forward 1-min path as signed points from entry (+ = our way)."""
    dm = {str(k): v.sort_index() for k, v in bars.groupby(bars.index.date)}
    out = []
    for _, t in entries.iterrows():
        dd = dm.get(str(t['date']))
        if dd is None:
            continue
        try:
            hh, mm = map(int, str(t['entry_time'])[:5].split(':'))
        except Exception:
            continue
        et = _dt.time(hh, mm)
        fwd = dd[(dd.index.time > et) & (dd.index.time <= EOD)]
        if len(fwd) < 3:
            continue
        e = float(t['entry']); sg = 1.0 if t['side'] == 'LONG' else -1.0
        fav = (fwd['high'].to_numpy() - e) * sg if sg > 0 else (e - fwd['low'].to_numpy())
        adv = (e - fwd['low'].to_numpy()) if sg > 0 else (fwd['high'].to_numpy() - e)
        out.append(dict(date=str(t['date']), y=int(t['y']), side=t['side'], setup=t['setup'],
                        entry_dt=pd.Timestamp(f"{t['date']} {hh:02d}:{mm:02d}"),
                        fav=fav, adv=adv,
                        close=(fwd['close'].to_numpy() - e) * sg,
                        ts=fwd.index.to_numpy()))
    return out


# ── the simulator ────────────────────────────────────────────────────────────
def run(paths, stop=1000.0, target=200.0, time_stop_min=None, one_at_a_time=True) -> pd.DataFrame:
    """1 contract. Stop and target are fixed point barriers checked on 1-min bars; a bar
    that touches both is resolved AGAINST us. time_stop_min exits at market that many
    minutes after entry if neither barrier has fired. Everything is flat by 15:10."""
    rows, free_until = [], None
    for p in paths:
        if one_at_a_time and free_until is not None and p['entry_dt'] < free_until:
            continue
        n = len(p['fav'])
        i_t = np.nonzero(p['fav'] >= target)[0]
        i_s = np.nonzero(p['adv'] >= stop)[0]
        t_i = i_t[0] if len(i_t) else 10 ** 9
        s_i = i_s[0] if len(i_s) else 10 ** 9
        k_i = min(int(time_stop_min), n - 1) if time_stop_min else 10 ** 9

        if s_i <= t_i and s_i < 10 ** 9 and s_i <= k_i:
            pts, why, idx = -stop, 'stop', s_i
        elif t_i < 10 ** 9 and t_i <= k_i:
            pts, why, idx = target, 'target', t_i
        elif k_i < n:
            pts, why, idx = float(p['close'][k_i]), 'time', k_i
        else:
            pts, why, idx = float(p['close'][-1]), 'eod', n - 1
        free_until = pd.Timestamp(p['ts'][idx])
        rows.append(dict(date=p['date'], y=p['y'], side=p['side'], setup=p['setup'],
                         pts=pts, why=why,
                         pnl=pts * POINT_VALUE - COMMISSION - FRICTION))
    return pd.DataFrame(rows)


def maxdd(d: pd.DataFrame) -> float:
    s = d.groupby('date')['pnl'].sum().sort_index().cumsum()
    return float((s - s.cummax()).min()) if len(s) else 0.0


def worst_day(d: pd.DataFrame) -> float:
    g = d.groupby('date')['pnl'].sum()
    return float(g.min()) if len(g) else 0.0


def line(lbl, d, years, extra=''):
    if not len(d):
        print(f'  {lbl:<34}  (no trades)'); return None
    dd = maxdd(d)
    s = d.groupby('date')['pnl'].sum()
    sh = s.mean() / s.std() * np.sqrt(252) if len(s) > 2 and s.std() else 0.0
    per = ''.join(f'{d.loc[d.y == y, "pnl"].sum():>+9,.0f}' for y in years)
    grn = sum(1 for y in years if d.loc[d.y == y, 'pnl'].sum() > 0)
    print(f'  {lbl:<34}{len(d):>5}{100*(d.pnl>0).mean():>5.0f}%{d.pnl.sum():>+9,.0f}'
          f'{dd:>+8,.0f}{(d.pnl.sum()/abs(dd) if dd else 0):>6.2f}{sh:>6.2f}'
          f'{worst_day(d):>+8,.0f}{grn:>3}/{len(years)}  {per}{extra}')
    return dict(tot=d.pnl.sum(), dd=dd, pdd=(d.pnl.sum()/abs(dd) if dd else 0), sh=sh,
                grn=grn, worst=worst_day(d), n=len(d))


def hdr(years):
    print(f'  {"config":<34}{"n":>5}{"WR":>6}{"total":>9}{"maxDD":>8}{"$/DD":>6}{"Sh":>6}'
          f'{"wrstDay":>8}{"grn":>5}  ' + ''.join(f'{y:>9}' for y in years))
    print('  ' + '-' * (34 + 5 + 6 + 9 + 8 + 6 + 6 + 8 + 5 + 2 + 9 * len(years)))


# ══════════════════════════════════════════════════════════════════════════════
# WHAT DOES THE TAPE ACTUALLY DELIVER?  (this is what the target must be set from)
# ══════════════════════════════════════════════════════════════════════════════
def mode_describe(paths=None):
    if paths is None:
        paths = _paths(ny_entries(), bars_1m())
    years = sorted({p['y'] for p in paths})
    n = len(paths)
    print(f'\n{"="*150}')
    print('  A. HOW OFTEN DOES A TRADE REACH +T BEFORE −1000, AND HOW LONG DOES IT TAKE?')
    print('     (existing entries, both sides, no day filter, 1-min path, flat 15:10)')
    print(f'{"="*150}')
    print(f'  {"target":>8}{"reached":>10}{"% of all":>10}{"med min":>10}{"p75 min":>10}'
          f'{"p90 min":>10}   per-year reach %')
    print('  ' + '-' * 118)
    for T in (50, 100, 150, 200, 250, 300, 400, 500, 750, 1000):
        hit, mins, hy = 0, [], {y: [0, 0] for y in years}
        for p in paths:
            i_t = np.nonzero(p['fav'] >= T)[0]
            i_s = np.nonzero(p['adv'] >= 1000)[0]
            ok = len(i_t) and (not len(i_s) or i_t[0] < i_s[0])
            hy[p['y']][1] += 1
            if ok:
                hit += 1; mins.append(int(i_t[0])); hy[p['y']][0] += 1
        yl = ' '.join(f'{y}:{100*hy[y][0]/max(hy[y][1],1):>3.0f}%' for y in years)
        print(f'  {T:>8}{hit:>10}{100*hit/n:>9.0f}%{np.median(mins) if mins else 0:>10.0f}'
              f'{np.percentile(mins,75) if mins else 0:>10.0f}'
              f'{np.percentile(mins,90) if mins else 0:>10.0f}   {yl}')

    mfe = np.array([p['fav'].max() for p in paths])
    mae = np.array([p['adv'].max() for p in paths])
    fin = np.array([p['close'][-1] for p in paths])
    print(f'\n{"="*150}')
    print('  B. THE EXCURSION DISTRIBUTION  (points, per trade, to 15:10)')
    print(f'{"="*150}')
    print(f'  {"":<22}{"p10":>9}{"p25":>9}{"median":>9}{"p75":>9}{"p90":>9}{"mean":>9}')
    print('  ' + '-' * 76)
    for lbl, a in (('best it ever got  (MFE)', mfe), ('worst it ever got (MAE)', mae),
                   ('where it ended    (EOD)', fin)):
        print(f'  {lbl:<22}' + ''.join(f'{np.percentile(a,q):>9.0f}'
                                       for q in (10, 25, 50, 75, 90)) + f'{a.mean():>9.0f}')
    print(f'\n  the 1000pt stop is reached by {100*(mae>=1000).mean():.1f}% of trades '
          f'({int((mae>=1000).sum())} of {n}) — 1 in {n/max((mae>=1000).sum(),1):.0f}')
    print(f'  a 625pt stop (IBKR DLL) would be reached by {100*(mae>=625).mean():.1f}%; '
          f'500pt (TC DLL) by {100*(mae>=500).mean():.1f}%; 400pt by {100*(mae>=400).mean():.1f}%')

    print(f'\n{"="*150}')
    print('  C. IS THE ENTRY "RIGHT 61% OF THE TIME"?  — it depends entirely on the bar you set')
    print(f'{"="*150}')
    for lbl, cond in (('ever green at all', mfe > 0),
                      ('reaches +50 before −1000', None), ('reaches +100 before −1000', None),
                      ('reaches +150 before −1000', None), ('reaches +200 before −1000', None),
                      ('ends the day green (EOD)', fin > 0),
                      ('MFE beats MAE (right way first)', mfe > mae)):
        if cond is None:
            T = int(lbl.split('+')[1].split(' ')[0])
            c = np.array([bool(len(np.nonzero(p['fav'] >= T)[0]) and
                               (not len(np.nonzero(p['adv'] >= 1000)[0]) or
                                np.nonzero(p['fav'] >= T)[0][0] < np.nonzero(p['adv'] >= 1000)[0][0]))
                          for p in paths])
        else:
            c = cond
        print(f'  {lbl:<36}{100*c.mean():>6.1f}%')
    print('\n  "61% right" is only true for a LOW bar. The number that matters for a target of T')
    print('  is the +T row above, and it falls fast as T rises — that is the whole trade-off.')


TARGETS = [75, 100, 125, 150, 200, 250, 300, None]
TIMES   = [30, 45, 60, 90, 120, 180, None]


def _matrix(paths, stop, metric='tot', years=None):
    print(f'\n  {metric.upper()} — rows = target (pts), cols = time-stop (minutes in trade)')
    print(f'  {"":>8}' + ''.join(f'{(str(x)+"m" if x else "none"):>10}' for x in TIMES))
    print('  ' + '-' * (8 + 10 * len(TIMES)))
    best = []
    for T in TARGETS:
        cells = ''
        for X in TIMES:
            d = run(paths, stop=stop, target=T or 10 ** 6, time_stop_min=X)
            dd = maxdd(d)
            v = d.pnl.sum() if metric == 'tot' else (d.pnl.sum() / abs(dd) if dd else 0)
            best.append((v, T, X, d))
            cells += (f'{v:>+10,.0f}' if metric == 'tot' else f'{v:>10.2f}')
        print(f'  {(str(T) if T else "none"):>8}{cells}')
    return best


def mode_grid(paths=None, stop=1000.0):
    if paths is None:
        paths = _paths(ny_entries(), bars_1m())
    years = sorted({p['y'] for p in paths})
    print(f'\n{"="*150}')
    print(f'  D. THE EXIT GRID — 1 contract, {stop:.0f}pt stop, one position at a time, flat 15:10')
    print('     "time-stop" = exit at market that many minutes after entry if the target has')
    print('     not been hit. That, not the stop, is the real loss-cutter in this design.')
    print(f'{"="*150}')
    b = _matrix(paths, stop, 'tot')
    _matrix(paths, stop, 'pdd')

    print(f'\n{"="*150}')
    print('  TOP CONFIGURATIONS BY $/DD, shown PER YEAR')
    print(f'{"="*150}')
    hdr(years)
    scored = sorted(b, key=lambda x: -(x[0] / abs(maxdd(x[3])) if maxdd(x[3]) else 0))
    seen = set()
    for v, T, X, d in scored:
        k = (T, X)
        if k in seen:
            continue
        seen.add(k)
        line(f'target {T or "none":>4} · time {X or "none":>4}', d, years)
        if len(seen) >= 8:
            break
    print('\n  reference points:')
    line('no target, no time stop (hold EOD)', run(paths, stop, 10**6, None), years)
    line('the LIVE exit stack (200pt stop)', _live_ref(), years)


def _live_ref():
    d = ny_entries().copy()
    sg = np.where(d.side == 'LONG', 1.0, -1.0)
    pts = (d['exit'] - d['entry']) * sg
    c = d['contracts'].clip(lower=1)
    # per-contract equivalent so it compares to a 1-contract book
    d['pnl'] = pts * POINT_VALUE - COMMISSION - FRICTION
    return d[['date', 'y', 'side', 'setup', 'pnl']]


def run2(paths, stop=1000.0, target=None, time_min=None, only_if_losing=False,
         losing_worse_than=0.0, clock_flat=None, one_at_a_time=True) -> pd.DataFrame:
    """Extended simulator. only_if_losing: the time rule fires ONLY when the position is
    underwater (by more than `losing_worse_than` points) at that minute — the softer reading
    of "book the loss". clock_flat: a wall-clock time to be flat by, instead of minutes."""
    T = target or 10 ** 6
    rows, free_until = [], None
    for p in paths:
        if one_at_a_time and free_until is not None and p['entry_dt'] < free_until:
            continue
        n = len(p['fav'])
        i_t = np.nonzero(p['fav'] >= T)[0]
        i_s = np.nonzero(p['adv'] >= stop)[0]
        t_i = i_t[0] if len(i_t) else 10 ** 9
        s_i = i_s[0] if len(i_s) else 10 ** 9
        k_i = 10 ** 9
        if time_min is not None:
            k = min(int(time_min), n - 1)
            if not only_if_losing or p['close'][k] < -abs(losing_worse_than):
                k_i = k
        if clock_flat is not None:
            idx = np.nonzero(pd.to_datetime(p['ts']).time >= clock_flat)[0]
            if len(idx):
                k_i = min(k_i, int(idx[0]))
        if s_i <= t_i and s_i < 10 ** 9 and s_i <= k_i:
            pts, why, idx = -stop, 'stop', s_i
        elif t_i < 10 ** 9 and t_i <= k_i:
            pts, why, idx = T, 'target', t_i
        elif k_i < n:
            pts, why, idx = float(p['close'][k_i]), 'time', k_i
        else:
            pts, why, idx = float(p['close'][-1]), 'eod', n - 1
        free_until = pd.Timestamp(p['ts'][idx])
        rows.append(dict(date=p['date'], y=p['y'], side=p['side'], setup=p['setup'],
                         pts=pts, why=why, pnl=pts * POINT_VALUE - COMMISSION - FRICTION))
    return pd.DataFrame(rows)


def mode_variants(paths=None):
    if paths is None:
        paths = _paths(ny_entries(), bars_1m())
    years = sorted({p['y'] for p in paths})
    print(f'\n{"="*150}')
    print('  E. IS THE TIME-STOP DAMAGE THE RULE ITSELF, OR THE EXTRA TRADES A FREED SLOT LETS IN?')
    print('     Control: take EVERY entry (no one-at-a-time), so the trade set is fixed at 944')
    print('     and only the exit differs.')
    print(f'{"="*150}')
    hdr(years)
    for X in (None, 30, 60, 90, 120, 180):
        line(f'ALL entries · time {X or "none":>4}', run2(paths, 1000, None, X, one_at_a_time=False), years)

    print(f'\n{"="*150}')
    print('  F. THE SOFTER READINGS OF "BOOK THE LOSS"  (1 contract, 1000pt stop, one at a time)')
    print(f'{"="*150}')
    hdr(years)
    line('hold to EOD (baseline)', run2(paths, 1000), years)
    for X in (60, 90, 120):
        line(f'cut at {X}m ONLY if underwater', run2(paths, 1000, None, X, only_if_losing=True), years)
    for X in (60, 90, 120):
        line(f'cut at {X}m only if worse than −100', run2(paths, 1000, None, X,
             only_if_losing=True, losing_worse_than=100), years)
    for ct in (_dt.time(12, 0), _dt.time(13, 0), _dt.time(14, 0)):
        line(f'flat by clock {ct.strftime("%H:%M")}', run2(paths, 1000, None, clock_flat=ct), years)

    print(f'\n{"="*150}')
    print('  G. THE STOP FAMILY — what does coming down to a COMPLIANT width cost?')
    print('     (hold to EOD, no target, no time rule — the best cell in the grid)')
    print(f'{"="*150}')
    hdr(years)
    for S, tag in ((1000, '  breaches TC MLL+DLL, IBKR DLL'), (850, '  = TC MLL less buffer'),
                   (625, '  = IBKR soft DLL'), (500, '  = TC DLL'), (400, ''), (300, ''), (200, '  = live')):
        line(f'stop {S}pt (${S*2:,.0f}/contract){tag}', run2(paths, S), years)


def mode_neighbourhood(paths=None):
    """The one cell that looked good (cut at 60m if worse than -100) must be a PLATEAU, not
    a spike. This codebase's graveyard is full of single cells that did not survive their
    own neighbours."""
    if paths is None:
        paths = _paths(ny_entries(), bars_1m())
    years = sorted({p['y'] for p in paths})
    print(f'\n{"="*150}')
    print('  H. NEIGHBOURHOOD OF "CUT AT 60m IF WORSE THAN −100"  (1c, 1000pt stop)')
    print('     A real rule has neighbours that also work. Read across AND down.')
    print(f'{"="*150}')
    THR = [50, 75, 100, 125, 150, 200, 300]
    MIN = [30, 45, 60, 75, 90, 120]
    for metric in ('total $', 'green years'):
        print(f'\n  {metric} — rows = "worse than −X pts", cols = minutes')
        print(f'  {"":>8}' + ''.join(f'{str(m)+"m":>10}' for m in MIN))
        print('  ' + '-' * (8 + 10 * len(MIN)))
        for th in THR:
            cells = ''
            for mn in MIN:
                d = run2(paths, 1000, None, mn, only_if_losing=True, losing_worse_than=th)
                if metric == 'total $':
                    cells += f'{d.pnl.sum():>+10,.0f}'
                else:
                    cells += f'{sum(1 for y in years if d.loc[d.y==y,"pnl"].sum()>0):>7}/{len(years)}'
            print(f'  {"−"+str(th):>8}{cells}')
    print('\n  If +6,378 at (−100, 60m) is surrounded by materially worse cells in both')
    print('  directions, it is a fitted spike and must not be shipped.')

    print(f'\n{"="*150}')
    print('  I. BEST COMBINATION vs THE LIVE STACK, PER YEAR')
    print(f'{"="*150}')
    hdr(years)
    line('live exit stack, 1 contract', _live_ref(), years)
    line('wide 1000 · hold EOD', run2(paths, 1000), years)
    line('625 (IBKR DLL) · hold EOD', run2(paths, 625), years)
    line('625 · cut 60m if worse than −100', run2(paths, 625, None, 60, True, 100), years)
    line('625 · cut 60m if worse than −150', run2(paths, 625, None, 60, True, 150), years)
    line('850 (TC MLL) · hold EOD', run2(paths, 850), years)
    line('500 (TC DLL) · hold EOD', run2(paths, 500), years)


# ══════════════════════════════════════════════════════════════════════════════
# LONDON — same design, its own entry mechanism, flat by 09:00 ET
# ══════════════════════════════════════════════════════════════════════════════
LON_FLAT = _dt.time(9, 0)


def london_paths():
    import futures.london_v2_sim as lv2
    tr = pd.read_csv(os.path.join(FAC, '_london_trades.csv'))
    b = lv2.load_days('2021-06-01', '2026-08-14')
    b = b[(b.index.time >= _dt.time(3, 0)) & (b.index.time <= LON_FLAT)]
    dm = {str(k): v.sort_index() for k, v in b.groupby(b['d'])}
    out = []
    for _, t in tr.sort_values(['d', 'time']).iterrows():
        dd = dm.get(str(t['d']))
        if dd is None:
            continue
        hh, mm = map(int, str(t['time'])[:5].split(':'))
        fwd = dd[(dd.index.time > _dt.time(hh, mm)) & (dd.index.time <= LON_FLAT)]
        if len(fwd) < 3:
            continue
        e = float(t['entry']); sg = 1.0 if t['side'] == 'LONG' else -1.0
        fav = (fwd['high'].to_numpy() - e) * sg if sg > 0 else (e - fwd['low'].to_numpy())
        adv = (e - fwd['low'].to_numpy()) if sg > 0 else (fwd['high'].to_numpy() - e)
        out.append(dict(date=str(t['d']), y=int(str(t['d'])[:4]), side=t['side'], setup='LONDON',
                        entry_dt=pd.Timestamp(f"{t['d']} {hh:02d}:{mm:02d}"),
                        fav=fav, adv=adv, close=(fwd['close'].to_numpy() - e) * sg,
                        ts=fwd.index.tz_localize(None).to_numpy()))
    return out


def mode_london():
    p = london_paths()
    years = sorted({x['y'] for x in p})
    mfe = np.array([x['fav'].max() for x in p]); mae = np.array([x['adv'].max() for x in p])
    print(f'\n{"="*150}')
    print(f'  J. LONDON — same design on its own entries ({len(p)} paths, flat 09:00 ET)')
    print(f'{"="*150}')
    print(f'  MFE median {np.median(mfe):.0f}pts (p75 {np.percentile(mfe,75):.0f}, '
          f'p90 {np.percentile(mfe,90):.0f})   MAE median {np.median(mae):.0f}pts')
    print(f'  reach +100 before −1000: {100*np.mean([len(np.nonzero(x["fav"]>=100)[0])>0 for x in p]):.0f}%'
          f'   +150: {100*np.mean([len(np.nonzero(x["fav"]>=150)[0])>0 for x in p]):.0f}%'
          f'   +200: {100*np.mean([len(np.nonzero(x["fav"]>=200)[0])>0 for x in p]):.0f}%')
    print(f'  the 1000pt stop is reached by {100*(mae>=1000).mean():.1f}% of London paths')
    print()
    hdr(years)
    line('wide 1000 · hold to 09:00', run2(p, 1000), years)
    line('625 (IBKR DLL) · hold to 09:00', run2(p, 625), years)
    line('500 (TC DLL) · hold to 09:00', run2(p, 500), years)
    for T in (50, 100, 150, 200):
        line(f'1000 stop · target {T}', run2(p, 1000, T), years)
    for X in (30, 60, 120):
        line(f'1000 stop · time {X}m', run2(p, 1000, None, X), years)
    line('1000 · cut 60m if worse than −100', run2(p, 1000, None, 60, True, 100), years)
    print('\n  reference — London CHAMPION as it runs live today (BE=0.10 armour, its own sizing):')
    tr = pd.read_csv(os.path.join(FAC, '_london_trades.csv'))
    tr['y'] = tr['d'].str[:4].astype(int); tr['date'] = tr['d']
    line('  london champion (live config)', tr[['date', 'y', 'side', 'pnl']], years)
    n = len(tr)
    for f, tag in ((COMMISSION + FRICTION, 'this lab\'s $6/c haircut + commission'),
                   (COMMISSION + 2.0, '1pt slippage + commission'),
                   (COMMISSION, 'commission only')):
        print(f'    london champion less {tag:<38} ${tr.pnl.sum() - f*n:>+9,.0f}  ({n} trades x ${f:.2f})')
    print('\n  ⚠️ london_v2_sim charges NO commission and NO slippage at all — `pnl = sign x (exit-entry)')
    print('     x DOLLARS_PER_PT`, verified in the source. Its 88%% WR comes from ~2,500 tiny BE')
    print('     scratches, so cost per trade is the whole question. The design rows above ARE')
    print('     haircut at $7.24/trade; the champion row is not. Compare the haircut rows.')


# ══════════════════════════════════════════════════════════════════════════════
# PER-ACCOUNT REALITY — daily caps, DLL halt, TopStep gauntlet
# ══════════════════════════════════════════════════════════════════════════════
def _cap_daily(d: pd.DataFrame, max_trades: int) -> pd.DataFrame:
    return d.groupby('date', group_keys=False).head(max_trades).reset_index(drop=True)


def mode_accounts(paths=None):
    from futures.factory.bench import daily_pnls_with_dll, monte_carlo_eval
    if paths is None:
        paths = _paths(ny_entries(), bars_1m())
    years = sorted({p['y'] for p in paths})
    cfgs = {
        'live exit stack (reference)': _live_ref(),
        'wide 1000 · hold EOD': run2(paths, 1000),
        '625 (IBKR DLL) · hold EOD': run2(paths, 625),
        '625 · cut 60m if worse −100': run2(paths, 625, None, 60, True, 100),
        '500 (TC DLL) · hold EOD': run2(paths, 500),
    }
    print(f'\n{"="*150}')
    print('  K. UNDER EACH ACCOUNT\'S REAL DAILY TRADE CAP  (IBKR 5/day, TC 2/day)')
    print(f'{"="*150}')
    for acct, cap in (('IBKR', 5), ('TC', 2)):
        print(f'\n  --- {acct} (max {cap} trades/day, 1 contract) ---')
        hdr(years)
        for lbl, d in cfgs.items():
            dd = d.copy()
            if 'entry_time' not in dd:
                dd['entry_time'] = ''
            line(lbl, _cap_daily(dd.sort_values('date'), cap), years)

    print(f'\n{"="*150}')
    print('  L. TOPSTEP $50k GAUNTLET  (DLL $1,000 halt, $2,000 trailing MLL, $3,000 target,')
    print('      60-day bootstrap over whole days — TC cap of 2 trades/day applied)')
    print(f'{"="*150}')
    print(f'  {"config":<34}{"P(pass)":>10}{"P(blow)":>10}{"worst day":>12}{"stop=$":>10}')
    print('  ' + '-' * 78)
    for lbl, d in cfgs.items():
        dd = d.copy()
        if 'entry_time' not in dd:
            dd['entry_time'] = ''
        dd = _cap_daily(dd.sort_values('date'), 2)
        days = [v for _, v in daily_pnls_with_dll(dd, dll=1000.0)]
        mc = monte_carlo_eval(days)
        risk = 2000 if '1000' in lbl else (1250 if '625' in lbl else (1000 if '500' in lbl else 400))
        print(f'  {lbl:<34}{100*mc.get("p_pass",0):>9.1f}%{100*mc.get("p_blow",0):>9.1f}%'
              f'{min(days):>+12,.0f}{risk:>10,}')
    print('\n  A single-trade risk larger than the DLL means the halt cannot protect the account:')
    print('  the loss lands in one fill, before any daily rule can intervene.')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    for f in ('describe', 'grid', 'variants', 'neighbourhood', 'accounts', 'london'):
        ap.add_argument(f'--{f}', action='store_true')
    ap.add_argument('--all', action='store_true')
    a = ap.parse_args()
    ny = ['describe', 'grid', 'variants', 'neighbourhood', 'accounts']
    paths = None
    ran = False
    for f in ny:
        if a.all or getattr(a, f):
            if paths is None:
                paths = _paths(ny_entries(), bars_1m())
            globals()[f'mode_{f}'](paths); ran = True
    if a.all or a.london:
        mode_london(); ran = True
    if not ran:
        ap.print_help()


if __name__ == '__main__':
    main()
