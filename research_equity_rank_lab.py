"""RANK LAB — turn the grader from a stamp into a ranker.

THE PROBLEM. grade_setup() sums ~15 hand-assigned components (ORB +30, VWAP reclaim +25,
bull flag +25, vol +25, HOD +20, RSI +20, gain +20 ...) against an A+ threshold of 80. The
sum has no ceiling, so 89% of accepted candidates score above 100 and 97.6% of everything not
rejected gets the same top grade. The book must choose 5 positions from ~16 A+ candidates a
day and has no way to order them.

THE LABEL. Ranking on forward MFE is a trap this session already proved: a filter that raised
MFE also raised MAE and realised WORSE under a stop-dominated exit stack. So the label here is
a SIMULATED OUTCOME under our own exit rules, applied to each candidate's own forward bars —
what this trade would actually have returned, not how far it could theoretically have run.

Step 1 validates that label against the trades we really took before anything is built on it.
"""
import sys; sys.path.insert(0, '/Users/sushil/trading')
import sqlite3, numpy as np, pandas as pd
from collect_bars import load_bars
pd.set_option('display.width', 220)

OUT = 'research_out'
# mirrors auto_trader constants
HARD_STOP_PCT   = 5.0
PCT_TRAIL_ARM   = 1.5
PCT_TRAIL_GAP   = 0.5
ATR_TRAIL_MULT  = 1.5
FADE_MULT       = 1.0
EOD             = (15, 45)
HOLD_TO_CLOSE_BEFORE = (10, 0)


def simulate_exit(bars, entry_px, entry_ts, atr):
    """Replay auto_trader's exit stack on one candidate's own 5-min bars. LONG only.
    Returns realised % (entry->exit). Deliberately a faithful SUBSET: hard stop, PCT trail,
    ATR trail, momentum fade, VWAP cross, EOD. Trails/fade are suspended for hold-to-close
    entries, exactly as holds_to_close() does live."""
    if bars is None or len(bars) == 0:
        return None
    ride = (entry_ts.hour, entry_ts.minute) < HOLD_TO_CLOSE_BEFORE
    stop = entry_px * (1 - HARD_STOP_PCT / 100)
    hi = entry_px
    tp = (bars['high'] + bars['low'] + bars['close']) / 3
    vwap = (tp * bars['volume']).cumsum() / bars['volume'].cumsum().replace(0, np.nan)
    prev_above = None
    for i, (ts, b) in enumerate(bars.iterrows()):
        # intrabar stop first — the conservative ordering
        if b['low'] <= stop:
            return (stop - entry_px) / entry_px * 100
        hi = max(hi, b['high'])
        gain = (b['close'] - entry_px) / entry_px * 100
        if not ride:
            if gain >= PCT_TRAIL_ARM:
                stop = max(stop, hi * (1 - PCT_TRAIL_GAP / 100))
            if b['close'] >= entry_px + atr:
                stop = max(stop, hi - ATR_TRAIL_MULT * atr)
            if gain > 0.3 and (hi - b['close']) > FADE_MULT * atr:
                return gain
        above = b['close'] > vwap.iloc[i] if not np.isnan(vwap.iloc[i]) else None
        if gain > 0.5 and above is False and prev_above is True:
            return gain
        prev_above = above
        if (ts.hour, ts.minute) >= EOD:
            return gain
    return (bars['close'].iloc[-1] - entry_px) / entry_px * 100


def build():
    con = sqlite3.connect('trades.db')
    d = pd.read_sql_query("""
      SELECT scan_date, scan_time, symbol, regime, price, grade, score, vol_ratio, rsi,
             intra_chg, sector, is_catalyst, entered, entry_trade_id, burst_age_min,
             consec_new_highs, price_vs_hod_pct
      FROM scan_log WHERE direction='LONG' AND grade IN ('A+','A') AND price IS NOT NULL""", con)
    real = pd.read_sql_query("""
      SELECT id AS entry_trade_id, pnl_pct AS real_pct, pnl AS real_pnl
      FROM trades WHERE status IN ('WIN','LOSS') AND setup_type!='RECONCILED'""", con)
    con.close()
    print(f'A+/A LONG candidates: {len(d):,}')

    cache = {}
    def day(sym, ds):
        k = (sym, ds)
        if k not in cache:
            try:
                b = load_bars(sym, start=ds, end=(pd.Timestamp(ds) + pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
                cache[k] = b if b is not None and len(b) else None
            except Exception:
                cache[k] = None
        return cache[k]

    rows = []
    for _, x in d.iterrows():
        b = day(x.symbol, x.scan_date)
        if b is None:
            continue
        try:
            t0 = pd.Timestamp(f'{x.scan_date} {x.scan_time}', tz='America/New_York')
        except Exception:
            continue
        fwd = b[b.index >= t0]
        if len(fwd) < 2:
            continue
        rng = (b['high'] - b['low'])
        atr = float(rng.tail(14).mean()) if len(rng) >= 5 else x.price * 0.02
        sim = simulate_exit(fwd, x.price, t0, atr)
        if sim is None:
            continue
        r = x.to_dict()
        r['sim_pct'] = sim
        r['mfe'] = (fwd['high'].max() - x.price) / x.price * 100
        r['mae'] = (fwd['low'].min() - x.price) / x.price * 100
        r['atr_pct'] = atr / x.price * 100
        rows.append(r)
    r = pd.DataFrame(rows).merge(real, on='entry_trade_id', how='left')
    # real_pct in the trades table is NET of commission (database.py:610), so the label must
    # be charged too or it is not comparable. Nominal $1,400 slot on the IBKR FIXED plan.
    _NOM = 1400.0
    r['comm_pct'] = np.maximum(_NOM / r.price * 0.005, 1.0) * 2 / _NOM * 100
    r['sim_net'] = r.sim_pct - r.comm_pct
    r['m'] = pd.to_datetime(r.scan_date).dt.to_period('M')
    r['hr'] = pd.to_numeric(r.scan_time.str.slice(0, 2), errors='coerce')
    r.to_csv(f'{OUT}/rank.csv', index=False)
    return r


if __name__ == '__main__' and '--hunt' not in sys.argv:
    r = build()
    print(f'labelled candidates: {len(r):,}   {r.scan_date.min()} -> {r.scan_date.max()}')
    print(f'\nsimulated outcome: mean {r.sim_pct.mean():+.3f}%  median {r.sim_pct.median():+.3f}%  '
          f'win {(r.sim_pct > 0).mean()*100:.1f}%')

    print('\n=== STEP 1: IS THE LABEL TRUSTWORTHY? (only trades we really took) ===')
    v = r.dropna(subset=['real_pct'])
    print(f'  matched real trades: {len(v)}')
    print(f'  correlation sim vs real      : {v.sim_pct.corr(v.real_pct):+.3f}')
    print(f'  mean simulated  {v.sim_pct.mean():+.3f}%   mean real  {v.real_pct.mean():+.3f}%')
    print(f'  win rate simulated {(v.sim_pct>0).mean()*100:.1f}%   real {(v.real_pct>0).mean()*100:.1f}%')
    print('  by month:')
    print('   ' + v.groupby('m').apply(lambda x: pd.Series({
        'n': len(x), 'sim%': x.sim_pct.mean(), 'real%': x.real_pct.mean(),
        'corr': x.sim_pct.corr(x.real_pct)}), include_groups=False).round(3).to_string().replace('\n', '\n   '))

    print('\n=== STEP 2: DOES THE CURRENT SCORE RANK ANYTHING? ===')
    a = r[r.score >= 80].copy()
    a['b'] = pd.cut(a.score, [79, 100, 115, 130, 150, 400])
    g = a.groupby('b', observed=True).apply(lambda x: pd.Series({
        'n': len(x), 'sim%': x.sim_pct.mean(), 'win%': (x.sim_pct > 0).mean()*100,
        'mfe': x.mfe.mean(), 'mae': x.mae.mean()}), include_groups=False)
    piv = a.pivot_table(index='b', columns='m', values='sim_pct', aggfunc='mean', observed=True)
    print(g.join(piv).round(3).to_string())
    print(f'\n  rank correlation score vs simulated outcome: '
          f'{a.score.corr(a.sim_pct, method="spearman"):+.4f}  (0 = the score carries no ranking information)')


# ── FEATURE HUNT ──────────────────────────────────────────────────────────────
# The decision being made is "which 5 of today's ~16 A+ candidates", so the target that
# matters is the WITHIN-DAY rank, not the global one: a cross-day correlation is mostly the
# market's mood, which we cannot act on. Every feature is scored both ways and is only
# reported as usable if its sign holds in at least 5 of the 6 months.
def feature_hunt():
    r = pd.read_csv(f'{OUT}/rank.csv')
    r['m'] = pd.to_datetime(r.scan_date).dt.to_period('M')
    r['hr'] = pd.to_numeric(r.scan_time.str.slice(0, 2), errors='coerce')
    r['fresh_burst'] = ((r.burst_age_min >= 30) & (r.burst_age_min <= 90)).astype(int)
    r['no_burst'] = (r.burst_age_min >= 999).astype(int)
    r['below_hod'] = -r.price_vs_hod_pct
    # within-day demeaned target and features
    r['y'] = r.groupby('scan_date').sim_net.transform(lambda s: s - s.mean())

    feats = ['score', 'vol_ratio', 'rsi', 'intra_chg', 'burst_age_min', 'consec_new_highs',
             'below_hod', 'hr', 'price', 'atr_pct', 'fresh_burst', 'no_burst', 'is_catalyst']
    print('\n=== FEATURE HUNT — Spearman vs simulated NET outcome ===')
    print(f"{'feature':<18}{'n':>6}{'global':>9}{'within-day':>12}   monthly within-day sign")
    keep = []
    for f in feats:
        x = r.dropna(subset=[f, 'sim_net'])
        if len(x) < 300:
            continue
        g = x[f].corr(x.sim_net, method='spearman')
        w = x[f].corr(x.y, method='spearman')
        bym = x.groupby('m').apply(lambda z: z[f].corr(z.y, method='spearman'), include_groups=False)
        signs = ''.join('+' if v > 0 else '-' if v < 0 else '.' for v in bym.fillna(0))
        agree = max((bym > 0).sum(), (bym < 0).sum())
        flag = ' <<<' if agree >= 5 and abs(w) >= 0.05 else ''
        print(f'{f:<18}{len(x):>6}{g:>+9.3f}{w:>+12.3f}   {signs}  {agree}/{len(bym)}{flag}')
        if agree >= 5 and abs(w) >= 0.05:
            keep.append((f, w))
    print('\n  <<< = sign holds in >=5 of 6 months AND |rho| >= 0.05  (the bar for usable)')
    return r, keep


if __name__ == '__main__' and '--hunt' in sys.argv:
    feature_hunt()
