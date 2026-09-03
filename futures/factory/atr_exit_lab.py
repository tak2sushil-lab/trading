"""
ATR EXIT LAB (Aug 24 2026) — "the tape moves in ATR, our rules are in points."

FINDING THIS TESTS (from thesis_lab.py):
  median MFE is a near-constant ~2.4-2.75 ATR in EVERY year (2021..2026), but the trail-arm
  threshold is a fixed 120 POINTS = 7.21 ATR in 2021 and 3.03 ATR in 2026. So whether a trade
  can ever arm its trail is decided by the year's volatility, not by the setup. Same units
  problem as the 200pt stop decaying from 1.04 to 0.44 daily-ATR.

THE EXPERIMENT (deliberately zero new free parameters):
  Replay the REAL exit stack bar-by-bar on the cached entry set, twice:
    POINTS  — exactly the live constants.
    ATR     — the SAME geometry, every threshold multiplied by (ATR_at_entry / A0), where A0
              is the full-sample median ATR. At median volatility the two are identical; the
              ATR version simply breathes with the tape.
  Then sweep one global scalar to check the result sits on a plateau, not a spike.

HONESTY RAILS
  * CALIBRATION GATE: the POINTS replay must reproduce the cached P&L. If it does not, the
    comparison is meaningless and the lab says so and stops.
  * ATR is snapshotted AT ENTRY from completed bars only, then frozen for the trade's life.
  * Decisions on CLOSED bars; intrabar we assume ADVERSE-FIRST (stop before target) in BOTH
    modes, so the comparison is fair and pessimistic.
  * Trail levels are CLAMPED to the market (a stop can never rest beyond the current price) --
    the look-ahead that inflated the patient-engine lab 10x.
  * Entry set is FIXED, so this measures exits only. Contracts come from the cache.

Run: venv/bin/python -m futures.factory.atr_exit_lab
"""
from __future__ import annotations
import os, sqlite3, sys
import numpy as np
import pandas as pd

ROOT = '/Users/sushil/trading'
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
FAC = os.path.join(ROOT, 'futures', 'factory')
ET, DPP = 'America/New_York', 2.0
YRS = ['2021', '2022', '2023', '2024', '2025', '2026']

# ── live constants, verbatim ────────────────────────────────────────────────
BASE_STOP_PTS, BASE_TARGET_PTS = 200.0, 1500.0
NO_MOVE_MINUTES, NO_MOVE_MAX_PTS, NO_MOVE_MIN_PTS = 90, 60.0, -40.0
REV_CONFIRM, REV_FRAC, REV_PEAK_MIN = 2, 0.30, 120.0
EXIT_PARAMS = {
    'CHOPPY':   dict(be_pts=90.0,  be_frac=0.45, wide_pts=130.0, wide_gap=60.0,  tight_pts=200.0, tight_gap=35.0),
    'QUIET':    dict(be_pts=90.0,  be_frac=0.45, wide_pts=130.0, wide_gap=60.0,  tight_pts=200.0, tight_gap=35.0),
    'TRENDING': dict(be_pts=110.0, be_frac=0.20, wide_pts=300.0, wide_gap=180.0, tight_pts=550.0, tight_gap=110.0),
}
QUIET_IB, TRENDING_IB, MIN_IB = 100.0, 200.0, 50.0
FRICTION = 6.0
ADVERSE_FIRST = True   # intrabar ordering assumption; flipped by the robustness run


def day_regime(ib_range: float) -> str:
    if ib_range < MIN_IB:
        return 'CHOPPY'
    if ib_range < QUIET_IB:
        return 'QUIET'
    return 'TRENDING' if ib_range >= TRENDING_IB else 'CHOPPY'


def load_bars() -> pd.DataFrame:
    con = sqlite3.connect(os.path.join(ROOT, 'market_data.db'))
    b = pd.read_sql_query("SELECT ts_utc,open,high,low,close,volume FROM futures_bars_5m "
                          "WHERE symbol='MNQ' ORDER BY ts_utc", con)
    b['ts'] = pd.to_datetime(b.ts_utc, format='mixed', utc=True).dt.tz_convert(ET)
    return b.drop_duplicates('ts').set_index('ts').sort_index()


def replay_trade(t, post, reg, k, k_stop=None, k_trail=None, k_nm=None):
    """k = scale applied to EVERY point threshold. k=1.0 reproduces live."""
    sgn = 1 if t.side == 'LONG' else -1
    entry = float(t.entry)
    P = EXIT_PARAMS[reg]
    ks = k if k_stop  is None else k_stop
    kt = k if k_trail is None else k_trail
    kn = k if k_nm    is None else k_nm
    stop_d   = BASE_STOP_PTS * ks
    tgt_d    = BASE_TARGET_PTS * ks
    be_pts   = P['be_pts'] * kt;    wide_pts  = P['wide_pts'] * kt
    wide_gap = P['wide_gap'] * kt;  tight_pts = P['tight_pts'] * kt
    tight_gap= P['tight_gap'] * kt; be_frac   = P['be_frac']
    nm_hi, nm_lo = NO_MOVE_MAX_PTS * kn, NO_MOVE_MIN_PTS * kn
    rev_peak = REV_PEAK_MIN * kt

    sl   = entry - sgn * stop_d
    tgt  = entry + sgn * tgt_d
    peak = 0.0; adv = 0; prev_c = None
    for ts, bar in post.iterrows():
        hi, lo, cl = float(bar.high), float(bar.low), float(bar.close)
        # ---- intrabar ordering (assumption, flagged)
        hit_stop = sgn * (lo if sgn > 0 else hi) <= sgn * sl
        hit_tgt  = sgn * (hi if sgn > 0 else lo) >= sgn * tgt
        if ADVERSE_FIRST:
            if hit_stop: return (sgn * (sl - entry), 'stop')
            if hit_tgt:  return (sgn * (tgt - entry), 'target')
        else:
            if hit_tgt:  return (sgn * (tgt - entry), 'target')
            if hit_stop: return (sgn * (sl - entry), 'stop')
        fav = sgn * ((hi - entry) if sgn > 0 else (entry - lo))
        peak = max(peak, fav)
        pnl_pts = sgn * (cl - entry)
        # ---- trail tiers (ratcheted AND clamped to the market)
        peak_px = entry + sgn * peak
        if pnl_pts >= be_pts:
            cand = entry + sgn * max(be_frac * peak, 0.25)
            if sgn * (cand - sl) > 0: sl = sgn * min(sgn * cand, sgn * cl)
        if pnl_pts >= wide_pts:
            cand = peak_px - sgn * wide_gap
            if sgn * (cand - sl) > 0: sl = sgn * min(sgn * cand, sgn * cl)
        if pnl_pts >= tight_pts:
            cand = peak_px - sgn * tight_gap
            if sgn * (cand - sl) > 0: sl = sgn * min(sgn * cand, sgn * cl)
        # ---- reversal exit
        if prev_c is not None:
            adv = adv + 1 if sgn * (cl - prev_c) < 0 else 0
        prev_c = cl
        if peak >= rev_peak and adv >= REV_CONFIRM and (peak - pnl_pts) >= REV_FRAC * peak:
            return (pnl_pts, 'rev_exit')
        # ---- no-move
        age = (ts - t.ent).total_seconds() / 60.0
        if age >= NO_MOVE_MINUTES and nm_lo <= pnl_pts <= nm_hi:
            return (pnl_pts, 'no_move')
    return (sgn * (float(post.close.iloc[-1]) - entry), 'eod')


def build_ctx(trades, bars):
    """Precompute per-trade context ONCE: day RTH frame, entry ATR, day regime."""
    ctx = []
    bars = bars.copy(); bars['d'] = bars.index.date
    by_day = {d: g for d, g in bars.groupby('d')}
    for day, td in trades.groupby('date'):
        g = by_day.get(pd.Timestamp(day).date())
        if g is None:
            continue
        rth = g.between_time('09:30', '15:55')
        if len(rth) < 12:
            continue
        tr = pd.concat([rth.high - rth.low, (rth.high - rth.close.shift()).abs(),
                        (rth.low - rth.close.shift()).abs()], axis=1).max(axis=1)
        atr_s = tr.rolling(14, min_periods=5).mean()
        ib = rth.between_time('09:30', '10:30')
        reg = day_regime(float(ib.high.max() - ib.low.min()) if len(ib) >= 2 else 0.0)
        for _, t in td.iterrows():
            pre = atr_s[atr_s.index <= t.ent].dropna()
            post = rth[(rth.index > t.ent) & (rth.index.time <= pd.Timestamp('15:10').time())]
            if not len(pre) or len(post) < 1:
                continue
            ctx.append((t, rth, post, float(pre.iloc[-1]), reg))
    return ctx


def run(ctx, mode, k_global=1.0, A0=None, parts=('stop', 'trail', 'nm')):
    """parts: which threshold families get ATR-scaled; the rest stay in POINTS."""
    rows = []
    for t, rth, post, atr_e, reg in ctx:
        kk = k_global * (atr_e / A0) if mode == 'atr' else k_global
        r = replay_trade(t, post, reg, k_global,
                         k_stop = kk if 'stop'  in parts else k_global,
                         k_trail= kk if 'trail' in parts else k_global,
                         k_nm   = kk if 'nm'    in parts else k_global)
        if r is None:
            continue
        pts, why = r
        rows.append(dict(date=t.date, year=t.year, side=t.side, cached=t.pnl,
                         pnl=pts * DPP * t.contracts - FRICTION * max(1, t.contracts),
                         why=why, reg=reg, atr=atr_e, k=kk))
    return pd.DataFrame(rows)


def line(g, label):
    y = g.groupby('year').pnl.sum().reindex(YRS).fillna(0)
    dd = g.groupby('date').pnl.sum().sort_index(); eq = dd.cumsum()
    print(f"  {label:30s} n={len(g):4d} ${g.pnl.sum():+8,.0f} DD={(eq-eq.cummax()).min():8,.0f} "
          f"worst={dd.min():7,.0f} green={(y>0).sum()}/6  " + " ".join(f"{a[2:]}:{v:+6.0f}" for a, v in y.items()))


def main():
    t = pd.read_csv(os.path.join(FAC, '_mom_2021-06-01_2026-08-14.csv'))
    t['date'] = t.date.astype(str); t['year'] = t.date.str[:4]
    t['ent'] = pd.to_datetime(t.date + ' ' + t.entry_time.astype(str)).dt.tz_localize(ET)
    bars = load_bars()
    ctx = build_ctx(t, bars)
    base = run(ctx, 'points', 1.0)
    A0 = float(base.atr.median())
    print(f"replayed {len(base)} of {len(t)} trades | reference ATR A0 = {A0:.1f} pts "
          f"(5-min ATR14 at entry)\n")
    print("=" * 104)
    print("CALIBRATION GATE — does the POINTS replay reproduce the cached book?")
    print("=" * 104)
    print(f"  cached total  ${base.cached.sum():+9,.0f}")
    print(f"  replay total  ${base.pnl.sum():+9,.0f}   corr(per-trade) = {base.cached.corr(base.pnl):+.3f}")
    print(f"  exit mix: {base.why.value_counts().to_dict()}")
    ok = base.cached.corr(base.pnl) > 0.85
    print(f"  VERDICT: {'PASS — comparison is meaningful' if ok else 'FAIL — replay does not match live; treat results as indicative only'}")
    print("\n" + "=" * 104)
    print("THE TEST — same geometry, points vs ATR-scaled")
    print("=" * 104)
    line(base, 'POINTS (live, k=1.0)')
    atr1 = run(ctx, 'atr', 1.0, A0)
    line(atr1, 'ATR-scaled (k=1.0)')
    print("\n  plateau check — one global scalar on top:")
    for k in (0.6, 0.8, 1.0, 1.25, 1.5, 2.0):
        line(run(ctx, 'atr', k, A0), f'ATR-scaled x{k}')
    print("\n  the same scalar applied to the POINTS version (control — is it just 'wider'?):")
    for k in (0.6, 0.8, 1.25, 1.5, 2.0):
        line(run(ctx, 'points', k), f'POINTS x{k}')
    print("\n" + "=" * 104)
    print("WHICH THRESHOLD CARRIES THE ATR EFFECT?  (scale only one family at a time)")
    print("=" * 104)
    line(base, 'none (all POINTS)')
    for parts, lab in ((('stop',), 'ATR: STOP only'), (('trail',), 'ATR: TRAIL only'),
                       (('nm',), 'ATR: NO-MOVE band only'),
                       (('stop','trail'), 'ATR: stop+trail'), (('stop','trail','nm'), 'ATR: all three')):
        line(run(ctx, 'atr', 1.0, A0, parts), lab)

    print("\n" + "=" * 104)
    print("ROBUSTNESS — does the verdict survive the opposite intrabar assumption?")
    print("=" * 104)
    import futures.factory.atr_exit_lab as _self
    for af in (True, False):
        _self.ADVERSE_FIRST = af
        tag = 'adverse-first (pessimistic)' if af else 'favourable-first (optimistic)'
        b2 = run(ctx, 'points', 1.0); a2 = run(ctx, 'atr', 1.0, A0)
        print(f"  -- {tag}")
        line(b2, '   POINTS (live)'); line(a2, '   ATR-scaled')
        print(f"     swing ATR-POINTS = ${a2.pnl.sum()-b2.pnl.sum():+,.0f}   "
              f"(cached book for reference: ${b2.cached.sum():+,.0f})")
    _self.ADVERSE_FIRST = True
    print("\n  exit mix, POINTS vs ATR:")
    print(f"    points : {base.why.value_counts().to_dict()}")
    print(f"    atr    : {atr1.why.value_counts().to_dict()}")


if __name__ == '__main__':
    main()
