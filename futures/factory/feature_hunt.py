"""
FEATURE HUNT (Aug 24 2026) — what separates a MOVER from an ORPHAN at entry?

THE TARGET THIS ATTACKS. thesis_lab.py split the 949-trade sim book by whether MFE ever
reached +120pts: ORPHANS 733 (77%) = -$34,268, green 0/6 · MOVERS 216 (23%) = +$37,621,
green 6/6. If anything knowable AT ENTRY separates them, that is where the money is —
selection, not exits (9 independent confirmations that reaction fails).

⚠️ METHOD FIX vs thesis_lab. That split used a FIXED 120pt threshold, which is itself
ATR-contaminated (120pt = 7.2 ATR in 2021, 3.0 ATR in 2026), so ATR trivially "won" the
predictor race and crowded everything else out. Here the target is ATR-NORMALISED:
    MOVER := MFE >= MOVER_ATR (default 2.5x the entry ATR)
so ATR is removed from the label and other signals can show through. A second target,
raw profitability, is reported alongside because reach != profit.

ANTI-FOOLERY RAILS (this session already produced a 12x frame overstatement and two
look-ahead bugs):
  * every feature uses bars STRICTLY up to and including the entry bar;
  * every feature is reported TRAIN (2021-23) vs TEST (2024-26) and only counts if the
    sign holds with |corr| > 0.05 in BOTH;
  * a PERMUTATION BASELINE is printed — with ~20 features on 949 trades, several will look
    "stable" by chance, and the baseline shows how many.

Run: venv/bin/python -m futures.factory.feature_hunt
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
MOVER_ATR = 2.5


def _rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(50.0)


def build(trades, bars):
    bars = bars.copy(); bars['d'] = bars.index.date
    by_day = {d: g for d, g in bars.groupby('d')}
    days = sorted(by_day)
    prev_hl = {}
    for i, d in enumerate(days):
        if i:
            p = by_day[days[i-1]].between_time('09:30', '15:55')
            if len(p): prev_hl[d] = (float(p.high.max()), float(p.low.min()))
    # causal daily ATR ratio
    datr = {}
    for d in days:
        r = by_day[d].between_time('09:30', '15:55')
        if len(r) > 3:
            tr = pd.concat([r.high - r.low, (r.high - r.close.shift()).abs(),
                            (r.low - r.close.shift()).abs()], axis=1).max(axis=1)
            datr[d] = float(tr.mean())
    ds = pd.Series(datr).sort_index()
    dratio = (ds.shift(1) / ds.shift(1).rolling(100, min_periods=30).median()).to_dict()

    rows = []
    for day, td in trades.groupby('date'):
        dd = pd.Timestamp(day).date()
        g = by_day.get(dd)
        if g is None: continue
        rth = g.between_time('09:30', '15:55')
        if len(rth) < 14: continue
        tp = (rth.high + rth.low + rth.close) / 3.0
        vwap = (tp * rth.volume).cumsum() / rth.volume.cumsum().replace(0, np.nan)
        tr = pd.concat([rth.high - rth.low, (rth.high - rth.close.shift()).abs(),
                        (rth.low - rth.close.shift()).abs()], axis=1).max(axis=1)
        atr_s = tr.rolling(14, min_periods=5).mean()
        rsi_s = _rsi(rth.close)
        cum_hi, cum_lo = rth.high.cummax(), rth.low.cummin()
        avgv = rth.volume.expanding().mean()
        ib = rth.between_time('09:30', '10:30')
        ib_hi, ib_lo = (float(ib.high.max()), float(ib.low.min())) if len(ib) >= 2 else (np.nan, np.nan)
        sess_open = float(rth.open.iloc[0])
        pdh, pdl = prev_hl.get(dd, (np.nan, np.nan))
        on = g.between_time('18:00', '09:29')
        for _, t in td.iterrows():
            pre = rth[rth.index <= t.ent]
            post = rth[(rth.index > t.ent) & (rth.index.time <= pd.Timestamp('15:10').time())]
            if len(pre) < 8 or len(post) < 2: continue
            i = pre.index[-1]
            atr = float(atr_s.loc[i]) if pd.notna(atr_s.loc[i]) else np.nan
            if not atr or not np.isfinite(atr) or atr <= 0: continue
            sgn = 1 if t.side == 'LONG' else -1
            e = float(t.entry)
            fav = (post.high - e) if sgn > 0 else (e - post.low)
            adv = (e - post.low) if sgn > 0 else (post.high - e)
            b = pre.iloc[-1]
            rng = float(b.high - b.low) or 1e-9
            last5, last3, prior10 = pre.iloc[-5:], pre.iloc[-3:], pre.iloc[-13:-3]
            ext = float(cum_hi.loc[i]) if sgn > 0 else float(cum_lo.loc[i])
            pd_lvl = pdh if sgn > 0 else pdl
            vw = float(vwap.loc[i])
            vw6 = float(vwap.iloc[max(0, len(pre) - 7)])
            c, c3, c6 = float(b.close), float(pre.close.iloc[-4]), float(pre.close.iloc[-7])
            rows.append(dict(
                date=t.date, year=t.year, side=t.side, setup=t.setup, pnl=t.pnl,
                mfe_atr=float(fav.max()) / atr, mae_atr=float(adv.max()) / atr, atr=atr,
                # ── candidate features, all causal ──
                atr_ratio=dratio.get(dd, np.nan),
                bar_body=abs(float(b.close - b.open)) / rng,
                bar_dir=sgn * float(b.close - b.open) / rng,
                bar_range_atr=rng / atr,
                run_closes=float(sum(1 for x in range(1, 6)
                                     if sgn * (float(pre.close.iloc[-x]) - float(pre.close.iloc[-x-1])) > 0)),
                vol_trend=float(last3.volume.mean() / max(prior10.volume.mean(), 1e-9)),
                rvol_entry=float(b.volume / avgv.loc[i]) if avgv.loc[i] else np.nan,
                mins_open=float((t.ent - t.ent.normalize() - pd.Timedelta(hours=9, minutes=30)).total_seconds() / 60),
                ib_range_atr=(ib_hi - ib_lo) / atr if np.isfinite(ib_hi) else np.nan,
                beyond_ib=float(sgn * (e - (ib_hi if sgn > 0 else ib_lo)) > 0) if np.isfinite(ib_hi) else np.nan,
                day_range_used=float(cum_hi.loc[i] - cum_lo.loc[i]) / atr,
                dist_prevday_atr=(sgn * (pd_lvl - e) / atr) if np.isfinite(pd_lvl) else np.nan,
                prior_run_atr=sgn * (e - sess_open) / atr,
                dist_ext_atr=sgn * (e - ext) / atr,
                vwap_ext_atr=sgn * (e - vw) / atr,
                vwap_slope_atr=sgn * (vw - vw6) / atr,
                rsi=float(rsi_s.loc[i]),
                accel_atr=(sgn * ((c - c3) - (c3 - c6))) / atr,
                on_range_atr=float(on.high.max() - on.low.min()) / atr if len(on) > 3 else np.nan,
            ))
    d = pd.DataFrame(rows)
    d['mover'] = d.mfe_atr >= MOVER_ATR
    d['win'] = d.pnl > 0
    return d


FEATS = ['atr_ratio', 'bar_body', 'bar_dir', 'bar_range_atr', 'run_closes', 'vol_trend',
         'rvol_entry', 'mins_open', 'ib_range_atr', 'beyond_ib', 'day_range_used',
         'dist_prevday_atr', 'prior_run_atr', 'dist_ext_atr', 'vwap_ext_atr',
         'vwap_slope_atr', 'rsi', 'accel_atr', 'on_range_atr']


def stability(d, target):
    tr, te = d[d.year <= '2023'], d[d.year >= '2024']
    out = []
    for f in FEATS:
        s = d[d[f].notna()]
        if len(s) < 100: continue
        a = s[f].corr(s[target].astype(float))
        c1 = tr[tr[f].notna()][f].corr(tr[tr[f].notna()][target].astype(float))
        c2 = te[te[f].notna()][f].corr(te[te[f].notna()][target].astype(float))
        ok = (np.sign(c1) == np.sign(c2)) and min(abs(c1), abs(c2)) > 0.05
        out.append(dict(feature=f, all=round(a, 3), train=round(c1, 3), test=round(c2, 3),
                        stable='YES' if ok else ''))
    return pd.DataFrame(out).sort_values('all', key=abs, ascending=False)


def main():
    con = sqlite3.connect(os.path.join(ROOT, 'market_data.db'))
    b = pd.read_sql_query("SELECT ts_utc,open,high,low,close,volume FROM futures_bars_5m "
                          "WHERE symbol='MNQ' ORDER BY ts_utc", con)
    b['ts'] = pd.to_datetime(b.ts_utc, format='mixed', utc=True).dt.tz_convert(ET)
    bars = b.drop_duplicates('ts').set_index('ts').sort_index()
    t = pd.read_csv(os.path.join(FAC, '_mom_2021-06-01_2026-08-14.csv'))
    t['date'] = t.date.astype(str); t['year'] = t.date.str[:4]
    t['ent'] = pd.to_datetime(t.date + ' ' + t.entry_time.astype(str)).dt.tz_localize(ET)
    d = build(t, bars)
    d.to_csv(os.path.join(FAC, '_feature_table.csv'), index=False)
    print(f"{len(d)} trades with full feature vectors | MOVER := MFE >= {MOVER_ATR} ATR")
    print(f"  movers {d.mover.mean()*100:.0f}%  |  movers P&L ${d[d.mover].pnl.sum():+,.0f} "
          f"(green {(d[d.mover].groupby('year').pnl.sum()>0).sum()}/6)  |  orphans "
          f"${d[~d.mover].pnl.sum():+,.0f} (green {(d[~d.mover].groupby('year').pnl.sum()>0).sum()}/6)\n")
    for tgt in ('mover', 'win'):
        print("=" * 92)
        print(f"TARGET = {tgt.upper()}   (walk-forward: train 2021-23 vs test 2024-26)")
        print("=" * 92)
        s = stability(d, tgt)
        print(s.to_string(index=False))
        n_stable = (s.stable == 'YES').sum()
        # permutation baseline: how many pass by chance?
        rng = np.random.default_rng(7); hits = []
        for _ in range(200):
            dd = d.copy(); dd[tgt] = rng.permutation(dd[tgt].values)
            hits.append((stability(dd, tgt).stable == 'YES').sum())
        print(f"\n  stable features: {n_stable}   |   permutation baseline: "
              f"{np.mean(hits):.1f} +/- {np.std(hits):.1f} pass by CHANCE (200 shuffles)")
        print(f"  ==> {'SIGNAL' if n_stable > np.mean(hits) + 2*np.std(hits) else 'INDISTINGUISHABLE FROM NOISE'}\n")


if __name__ == '__main__':
    main()
