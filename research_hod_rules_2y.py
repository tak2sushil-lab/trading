"""Which belief about the day's high is right? (Oct 1 2026)

The batting order ranks candidates AT their day's high first ("strong"); Layer 2 rejects candidates
that touched the high 3+ times in the last 6 bars ("stuck"). This lab computes both rules' inputs on
every 2-year day-trader candidate (research_out/exhaustion_2y.csv) and splits Layer 2's "HOD tests"
into the two situations it cannot tell apart:
    failed tests  — bars that came within 0.5% of the high but did NOT make a new session high (stall)
    new highs     — bars that made a fresh session high (grind / breakout)
All features use only the candidate bar and the bars before it (the decision is made at its close).
HOD tests are price-based, so the bars_5m volume-scale issue (pre-Aug-2026) does not affect them;
volume-based features (up-volume ratio) are still computed but flagged.

Output: research_out/hod_rules_2y.csv
Usage:  venv/bin/python research_hod_rules_2y.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_bars import load_bars  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))


def l2_features(day, ts, entry):
    """Layer 2's inputs on the 6 bars ending with the candidate bar, plus stall/grind split."""
    upto = day[day.index <= ts]
    if len(upto) < 3:
        return None
    w = upto.iloc[-6:]
    hod6 = float(w['high'].max())
    near = (hod6 - w['high']) / hod6 < 0.005
    # a bar is a "new high" when its high exceeds every earlier high of the session
    prior_max = upto['high'].cummax().shift(1)
    is_new = (upto['high'] > prior_max).iloc[-6:]
    is_new.iloc[:] = is_new.fillna(True).values
    tests = int(near.sum())
    new_high_tests = int((near & is_new).sum())
    consec = 0
    for _, b in w.iloc[::-1].iterrows():
        if b['close'] > b['open']:
            consec += 1
        else:
            break
    tp = (upto['high'] + upto['low'] + upto['close']) / 3
    vwap = float((tp * upto['volume']).sum() / max(upto['volume'].sum(), 1))
    bar_atr = float((w['high'] - w['low']).iloc[-5:].mean())
    ext = (entry - vwap) / bar_atr if bar_atr > 0 else 0.0
    tot = float(w['volume'].sum())
    upvol = float(w[w['close'] > w['open']]['volume'].sum()) / tot if tot > 0 else np.nan
    sess_hod = float(upto['high'].max())
    return {
        'tests6': tests, 'new_high_tests6': new_high_tests, 'failed_tests6': tests - new_high_tests,
        'consec_green': consec, 'vwap_ext_bar_atr': ext, 'l2_upvol6': upvol,
        'pvh_sess': (entry / sess_hod - 1) * 100,
        'l2_verdict': ('SKIP_HOD' if tests >= 3 else 'SKIP_RUN' if consec >= 4 else
                       'SKIP_VWAP' if ext > 2.5 else 'HALF_HOD2' if tests == 2 else 'GO'),
    }


def main():
    cands = pd.read_csv(os.path.join(ROOT, 'research_out', 'exhaustion_2y.csv'), parse_dates=['date'])
    cands['ts'] = pd.to_datetime(cands['ts'], utc=True).dt.tz_convert('America/New_York')
    stops = pd.read_csv(os.path.join(ROOT, 'research_out', 'stops_2y.csv'), parse_dates=['date'])
    rows = []
    for sym, g in cands.groupby('sym'):
        b = load_bars(sym, start='2024-09-25', end='2026-10-01').between_time('09:30', '15:55')
        dates = b.index.date
        days = {d: b[dates == d] for d in set(g['date'].dt.date) if (dates == d).any()}
        for i, r in g.iterrows():
            day = days.get(r['date'].date())
            if day is None:
                continue
            f = l2_features(day, r['ts'], float(r['entry']))
            if f:
                rows.append({'idx': i, **f})
    feats = pd.DataFrame(rows).set_index('idx')
    out = cands.join(feats, how='inner').merge(
        stops[['sym', 'date', 'fixed5_R', 'atr1.0_R', 'atr1.0_ret']], on=['sym', 'date'], how='left')
    path = os.path.join(ROOT, 'research_out', 'hod_rules_2y.csv')
    out.to_csv(path, index=False)
    print(f'{len(out)} candidates with Layer 2 features -> {path}')


if __name__ == '__main__':
    main()
