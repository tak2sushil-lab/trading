"""How good do MANUAL trades have to be before they help the $100K TopStep combine?

System book = the live-parity Tide-on sim (futures/factory/_tideab3_on.csv), sized as TC trades
today: longs 4 MNQ, shorts 1, 200pt stop, DLL-aware sizing (prop_rules.dll_contracts).
Manual book = a bracket trade the user clicks: C contracts, stop S pts, target T pts, win
probability p (unknown — swept). k manual trades per trading day, taken BEFORE the system's
trades that day (conservative: a morning manual loss shrinks the room the system has).

$100K rules: target $6,000 · EOD-trailing MLL $3,000 (floor >= $97,000) · DLL $2,000 (day ends) ·
55% consistency · our soft guards: $1,400 day stop, $2,400 day cap, $450 MLL buffer.
Blow OR buffer freeze = pay a reset and start over (same as topstep_gauntlet.py).
"""
import os, sys, numpy as np, pandas as pd

BOOK = '/Users/sushil/trading/futures/factory/_tideab3_on.csv'
START, TARGET, MLL, DLL = 100_000., 6_000., 3_000., 2_000.
SOFT_DLL, CAP, BUF = 1_400., 2_400., 450.
FRIC = 6.0                      # $ per contract round trip (commission + slippage), as the bench
STOP_1C = 200 * 2 + FRIC        # system full stop per contract

d = pd.read_csv(BOOK); d['pc'] = d.pnl / d.contracts
trades = {k: [(r.side == 'SHORT', r.pc) for r in g.itertuples()] for k, g in d.groupby('date')}
cal = pd.bdate_range(d.date.min(), d.date.max()).strftime('%Y-%m-%d').tolist()


def journey(s0, horizon, manual, rng):
    """manual = None or dict(k, c, s, t, p)."""
    resets = 0; bal = hwm = START; best = 0.
    for di in range(s0, min(s0 + horizon, len(cal))):
        floor = max(hwm - MLL, START - MLL)
        if bal - floor < BUF:                       # frozen: cannot trade -> reset
            resets += 1; bal = hwm = START; best = 0.; continue
        sess = 0.; blown = False

        def ok():
            return (sess > -SOFT_DLL and sess < CAP and bal + sess - floor >= BUF)

        if manual:
            for _ in range(manual['k']):
                if not ok(): break
                c = manual['c']
                # DLL-aware: a full manual stop may not carry today past the DLL
                c = min(c, int((DLL - 50 + min(sess, 0)) // (manual['s'] * 2 + FRIC)))
                if c < 1: break
                win = rng.random() < manual['p']
                sess += (manual['t'] if win else -manual['s']) * 2 * c - FRIC * c
                if bal + sess <= floor: blown = True; break
                if sess <= -DLL: break
        g = trades.get(cal[di])
        if g is not None and not blown:
            for is_short, pc in g:
                if not ok(): break
                want = 1 if is_short else 4
                c = min(want, int((DLL - 50 + min(sess, 0)) // STOP_1C))
                if c < 1: break
                sess += pc * c - FRIC * c
                if bal + sess <= floor: blown = True; break
                if sess <= -DLL: break
        if blown:
            resets += 1; bal = hwm = START; best = 0.; continue
        bal += sess; hwm = max(hwm, bal); best = max(best, sess); prof = bal - START
        if prof >= TARGET and best <= 0.55 * prof:
            return True, di - s0 + 1, resets
    return False, min(horizon, len(cal) - s0), resets


def run(window, horizon, manual, seeds):
    a, b = window
    starts = [i for i, c in enumerate(cal) if a <= c <= b]
    starts = starts[::3] if len(starts) > 300 else starts   # overlapping journeys; every 3rd start
    ok3 = ok6 = okh = 0; rs = []; n = 0
    for sd in range(seeds):
        rng = np.random.default_rng(sd)
        for s in starts:
            ok, days, r = journey(s, horizon, manual, rng)
            n += 1; rs.append(r)
            ok3 += ok and days <= 63; ok6 += ok and days <= 126; okh += ok
    return ok3 / n, ok6 / n, okh / n, float(np.mean(rs))


if __name__ == '__main__':
    WINDOWS = {
        'ALL starts Jun21-Sep25 (12mo)': (('2021-06-01', '2025-09-30'), 252),
        'RECENT starts Oct25-Mar26 (6mo)': (('2025-10-01', '2026-03-31'), 126),
        'LATEST starts Apr26-Jun26 (3mo)': (('2026-04-01', '2026-06-22'), 63),
    }
    S, T, C = 50, 100, 2           # a 2:1 bracket, 2 MNQ: lose $206, win $394
    zero_ev = (S * 2 * C + FRIC * C) / ((S + T) * 2 * C)
    print(f"Manual bracket: {C} MNQ, stop {S}pt, target {T}pt -> lose ${S*2*C+FRIC*C:.0f}, "
          f"win ${T*2*C-FRIC*C:.0f}; break-even win rate {zero_ev:.1%}\n")
    rows = []
    for lab, (w, h) in WINDOWS.items():
        p3, p6, ph, r = run(w, h, None, 1)
        rows.append(dict(window=lab, manual='none (system only)', ev_per_trade='-',
                         pass_3mo=p3, pass_6mo=p6, pass_horizon=ph, resets=r))
        for k in (1, 2):
            for p in (0.30, 0.353, 0.40, 0.45):
                ev = p * (T * 2 * C - FRIC * C) - (1 - p) * (S * 2 * C + FRIC * C)
                p3, p6, ph, r = run(w, h, dict(k=k, c=C, s=S, t=T, p=p), 3)
                rows.append(dict(window=lab, manual=f'{k}/day win {p:.0%}', ev_per_trade=f'{ev:+.0f}',
                                 pass_3mo=p3, pass_6mo=p6, pass_horizon=ph, resets=r))
    out = pd.DataFrame(rows)
    for c in ('pass_3mo', 'pass_6mo', 'pass_horizon'):
        out[c] = (out[c] * 100).round(0).astype(int).astype(str) + '%'
    out['resets'] = out['resets'].round(2)
    pd.set_option('display.width', 200)
    print(out.to_string(index=False))
