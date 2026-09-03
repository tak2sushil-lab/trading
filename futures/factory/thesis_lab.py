"""
THESIS LAB (Aug 24 2026) — "did the move we predicted ever actually happen?"

User's reframe: stop tuning exits; look at the trades where the thesis NEVER held, ask why
the system said a move was coming, and whether anything knowable AT ENTRY separates them.
Key structural fact from the exit ledger: the trail's first tier cannot arm until the trade
is meaningfully green, and Reversal Exit needs peak >= 120pts. So a trade whose MFE never
reaches ~120 has NO adaptive exit and is pure noise absorption. Call those ORPHANS.

Questions, in order:
  Q1  What share of trades are orphans, and what do they cost? (per year)
  Q2  Are we "catching the tail"? -- how far had price ALREADY run before we entered,
      orphans vs movers. (user's hypothesis)
  Q3  Does anything knowable AT ENTRY predict reaching +120? (RVOL, ATR, prior run,
      distance from session extreme, room-to-VWAP, time of day, grade, regime, flip_age)
  Q4  Is the SHORT side broken everywhere, or only in some entry conditions?

METHOD: excursions are measured from real 5-min bars between entry and 15:10 (not the
recorded exit) so the result describes the TAPE, not our exit stack. All entry features use
bars strictly BEFORE the entry bar. Per-year reporting throughout.

Run: venv/bin/python -m futures.factory.thesis_lab
"""
from __future__ import annotations
import os, sqlite3, sys
import numpy as np
import pandas as pd

ROOT = '/Users/sushil/trading'
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
FAC = os.path.join(ROOT, 'futures', 'factory')
ET = 'America/New_York'
DPP = 2.0
YRS = ['2021', '2022', '2023', '2024', '2025', '2026']
TRAIL_ARM = 120.0        # REV_EXIT_PEAK_MIN_PTS — the floor for any adaptive exit


def load_bars() -> pd.DataFrame:
    con = sqlite3.connect(os.path.join(ROOT, 'market_data.db'))
    b = pd.read_sql_query("SELECT ts_utc,open,high,low,close,volume FROM futures_bars_5m "
                          "WHERE symbol='MNQ' ORDER BY ts_utc", con)
    b['ts'] = pd.to_datetime(b.ts_utc, format='mixed', utc=True).dt.tz_convert(ET)
    return b.drop_duplicates('ts').set_index('ts').sort_index()


def build(trades: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for day, t_day in trades.groupby('date'):
        g = b[b.index.date == pd.Timestamp(day).date()]
        rth = g.between_time('09:30', '15:55')
        if len(rth) < 12:
            continue
        tp = (rth.high + rth.low + rth.close) / 3.0
        vwap = (tp * rth.volume).cumsum() / rth.volume.cumsum().replace(0, np.nan)
        cum_hi, cum_lo = rth.high.cummax(), rth.low.cummin()
        avg_v = rth.volume.expanding().mean()
        sess_open = float(rth.open.iloc[0])
        atr = float((rth.high - rth.low).rolling(14, min_periods=4).mean().median())
        for _, t in t_day.iterrows():
            pre = rth[rth.index <= t.ent]
            post = rth[(rth.index > t.ent) & (rth.index.time <= pd.Timestamp('15:10').time())]
            if len(pre) < 2 or len(post) < 2:
                continue
            sgn = 1 if t.side == 'LONG' else -1
            fav = (post.high - t.entry) if sgn > 0 else (t.entry - post.low)
            adv = (t.entry - post.low) if sgn > 0 else (post.high - t.entry)
            i = pre.index[-1]
            # --- features knowable at entry (bars up to & incl. the entry bar)
            prior_run = sgn * (t.entry - sess_open)                       # how far it already ran our way
            ext = float(cum_hi.loc[i]) if sgn > 0 else float(cum_lo.loc[i])
            dist_ext = sgn * (t.entry - ext)                              # 0 = entering AT the extreme
            vw = float(vwap.loc[i])
            vwap_ext = sgn * (t.entry - vw)                               # stretch above/below VWAP
            rvol = float(pre.volume.iloc[-1] / avg_v.loc[i]) if avg_v.loc[i] else np.nan
            rows.append(dict(
                date=t.date, year=t.year, side=t.side, setup=t.setup, grade=t.grade,
                flip_age=t.flip_age, pnl=t.pnl, contracts=t.contracts,
                entry_hhmm=str(t.entry_time),
                mfe=float(fav.max()), mae=float(adv.max()),
                eod_pts=float(sgn * (post.close.iloc[-1] - t.entry)),
                armed=bool(fav.max() >= TRAIL_ARM),
                prior_run=float(prior_run), dist_ext=float(dist_ext),
                vwap_ext=float(vwap_ext), rvol=rvol, atr=atr,
                prior_run_atr=float(prior_run / atr) if atr else np.nan,
                vwap_ext_atr=float(vwap_ext / atr) if atr else np.nan,
            ))
    return pd.DataFrame(rows)


def yr(df, col='pnl'):
    y = df.groupby('year')[col].sum().reindex(YRS).fillna(0)
    return " ".join(f"{a[2:]}:{v:+6.0f}" for a, v in y.items()) + f"  green={(y>0).sum()}/6"


def main():
    t = pd.read_csv(os.path.join(FAC, '_mom_2021-06-01_2026-08-14.csv'))
    t['date'] = t.date.astype(str); t['year'] = t.date.str[:4]
    t['ent'] = pd.to_datetime(t.date + ' ' + t.entry_time.astype(str)).dt.tz_localize(ET)
    d = build(t, load_bars())
    d.to_csv(os.path.join(FAC, '_thesis_table.csv'), index=False)
    print(f"built {len(d)} of {len(t)} trades (need >=2 bars either side of entry)\n")

    print("=" * 100)
    print("Q1  ORPHANS — trades whose MFE never reached +120pts (no adaptive exit can arm)")
    print("=" * 100)
    o, m = d[~d.armed], d[d.armed]
    print(f"  ORPHANS  n={len(o):3d} ({len(o)/len(d)*100:.0f}%)  P&L ${o.pnl.sum():8,.0f}  avg ${o.pnl.mean():+7.1f}  median MFE {o.mfe.median():5.0f}pts  median MAE {o.mae.median():5.0f}pts")
    print(f"  MOVERS   n={len(m):3d} ({len(m)/len(d)*100:.0f}%)  P&L ${m.pnl.sum():8,.0f}  avg ${m.pnl.mean():+7.1f}  median MFE {m.mfe.median():5.0f}pts  median MAE {m.mae.median():5.0f}pts")
    print(f"  orphans per year: {yr(o)}")
    print(f"  movers  per year: {yr(m)}")
    print(f"\n  >>> the whole book = orphans ${o.pnl.sum():,.0f} + movers ${m.pnl.sum():,.0f}")
    print(f"  orphan share by side:  " + str({s: f"{(~g.armed).mean()*100:.0f}%" for s, g in d.groupby('side')}))

    print("\n" + "=" * 100)
    print("Q2  ARE WE CATCHING THE TAIL? (how far price already ran our way BEFORE entry)")
    print("=" * 100)
    print(f"  {'bucket':>26s} {'n':>5s} {'orphan%':>8s} {'avg $':>9s} {'medMFE':>7s} {'reach120%':>10s}")
    d2 = d[d.prior_run_atr.notna()].copy()
    d2['q'] = pd.qcut(d2.prior_run_atr, 5, labels=['Q1 least run', 'Q2', 'Q3', 'Q4', 'Q5 most run'])
    for q, g in d2.groupby('q', observed=True):
        print(f"  {str(q):>26s} {len(g):5d} {(~g.armed).mean()*100:7.0f}% {g.pnl.mean():+9.1f} {g.mfe.median():7.0f} {g.armed.mean()*100:9.0f}%")
    print("\n  same cut, distance from the session extreme at entry (0 = buying the very high):")
    d2['qe'] = pd.qcut(d2.dist_ext, 5, labels=['Q1 at extreme', 'Q2', 'Q3', 'Q4', 'Q5 far from it'])
    for q, g in d2.groupby('qe', observed=True):
        print(f"  {str(q):>26s} {len(g):5d} {(~g.armed).mean()*100:7.0f}% {g.pnl.mean():+9.1f} {g.mfe.median():7.0f} {g.armed.mean()*100:9.0f}%")

    print("\n" + "=" * 100)
    print("Q3  WHAT, KNOWABLE AT ENTRY, PREDICTS REACHING +120? (point-biserial vs `armed`)")
    print("=" * 100)
    feats = ['rvol', 'prior_run_atr', 'dist_ext', 'vwap_ext_atr', 'atr', 'flip_age']
    print(f"  {'feature':>16s} {'ALL':>8s} {'2021-23':>9s} {'2024-26':>9s}  stable?")
    for f in feats:
        s = d[d[f].notna()]
        a = s[f].corr(s.armed.astype(float))
        tr = s[s.year <= '2023']; te = s[s.year >= '2024']
        c1 = tr[f].corr(tr.armed.astype(float)); c2 = te[f].corr(te.armed.astype(float))
        ok = 'YES' if (np.sign(c1) == np.sign(c2) and min(abs(c1), abs(c2)) > 0.05) else 'no'
        print(f"  {f:>16s} {a:+8.3f} {c1:+9.3f} {c2:+9.3f}  {ok}")

    print("\n" + "=" * 100)
    print("Q4  THE SHORT SIDE — is it broken everywhere, or only in some conditions?")
    print("=" * 100)
    for s, g in d.groupby('side'):
        print(f"  {s}: n={len(g)} ${g.pnl.sum():+8,.0f} avg ${g.pnl.mean():+6.1f} reach120 {g.armed.mean()*100:3.0f}% | {yr(g)}")
    sh = d[d.side == 'SHORT'].copy()
    print("\n  SHORT sliced by prior-run quintile (is there ANY pocket that works?):")
    sh2 = sh[sh.prior_run_atr.notna()].copy()
    sh2['q'] = pd.qcut(sh2.prior_run_atr, 4, labels=['least run', 'Q2', 'Q3', 'most run'])
    for q, g in sh2.groupby('q', observed=True):
        print(f"    {str(q):>12s} n={len(g):3d} ${g.pnl.sum():+8,.0f} avg ${g.pnl.mean():+6.1f} | {yr(g)}")


if __name__ == '__main__':
    main()
