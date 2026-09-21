#!/usr/bin/env python
"""FIT THE RANKER — replace 15 hand-guessed weights with fitted ones.

Population: A+/A LONG candidates carrying BOTH a reconstructed component set
(backfill_score_components.py, calibration-gated: intra_chg reproduces live exactly, corr
1.000) and the corrected forward label (backfill_scan_forward.py).

TARGET. The decision is "which 5 of today's ~16", so everything is demeaned WITHIN THE DAY --
a cross-day correlation is mostly the market's mood, which cannot be acted on. Two targets are
carried and a feature must survive BOTH, because this session has already shown that ranking
on raw forward MFE picks livelier stocks that realise worse:
    edge     = fwd_mfe + fwd_mae   (favourable move net of the adverse swing)
    sim_net  = the exit stack replayed on the candidate's own bars, net of commission
               (research_equity_rank_lab.py; corr +0.668 to real trade outcomes)

VALIDATION. Walk-forward by month: fit on everything before month k, score month k. No month
is ever used to predict itself. Reported on the decision that matters -- pick the top 5 per
day and compare against what the current score and the live batting order would have picked.
"""
import sys, os, json, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
from sklearn.linear_model import Ridge
pd.set_option('display.width', 230)

FEATS = ['orb_break', 'vwap_reclaim', 'above_vwap', 'hod_break', 'is_bull_flag', 'is_tight',
         'uptrend', 'ema_touch', 'above_ma', 'fvg_count', 'rsi', 'rsi_5m', 'range_pct',
         'today_gain', 'rs_vs_spy', 'intra_chg']


def load():
    con = sqlite3.connect('trades.db')
    d = pd.read_sql_query("""
        SELECT id, scan_date, scan_time, symbol, price, score, vol_ratio, entered, entry_trade_id,
               fwd_mfe_pct, fwd_mae_pct, score_components
        FROM scan_log WHERE score_components LIKE '%backfill%' AND fwd_mfe_pct IS NOT NULL""", con)
    con.close()
    comp = pd.DataFrame([json.loads(x) for x in d.score_components])
    d = pd.concat([d.drop(columns=['score_components']).reset_index(drop=True),
                   comp.reset_index(drop=True)], axis=1)
    for f in FEATS:
        if f in d:
            d[f] = pd.to_numeric(d[f].replace({True: 1, False: 0, None: np.nan}), errors='coerce')
    d['edge'] = d.fwd_mfe_pct + d.fwd_mae_pct
    try:
        rk = pd.read_csv('research_out/rank.csv')[['scan_date', 'symbol', 'scan_time', 'sim_net']]
        d = d.merge(rk, on=['scan_date', 'symbol', 'scan_time'], how='left')
    except Exception:
        d['sim_net'] = np.nan
    d['m'] = pd.to_datetime(d.scan_date).dt.to_period('M')
    d['vol_ratio'] = pd.to_numeric(d.vol_ratio, errors='coerce')
    return d


def demean(df, col):
    return df[col] - df.groupby('scan_date')[col].transform('mean')


def main():
    d = load()
    use = [f for f in FEATS if f in d and d[f].notna().mean() > 0.6] + ['vol_ratio']
    d = d.dropna(subset=use + ['edge'])
    print(f'candidates with components + label: {len(d):,}   features: {len(use)}')
    print(f'months: {sorted(d.m.astype(str).unique())}\n')

    for target in ['edge', 'sim_net']:
        if d[target].notna().sum() < 500:
            print(f'--- target {target}: too few rows, skipped\n'); continue
        x = d.dropna(subset=[target]).copy()
        x['y'] = demean(x, target)
        months = sorted(x.m.unique())
        print(f'=== TARGET: {target}  (n={len(x):,}) ===')
        print(f"{'test month':<12}{'train n':>9}{'test n':>8}{'fitted rho':>12}{'current score rho':>19}")
        preds = []
        for i in range(2, len(months)):
            tr = x[x.m < months[i]]; te = x[x.m == months[i]].copy()
            if len(tr) < 200 or len(te) < 40: continue
            mu, sd = tr[use].mean(), tr[use].std().replace(0, 1)
            mdl = Ridge(alpha=5.0).fit((tr[use] - mu) / sd, tr.y)
            te['pred'] = mdl.predict((te[use] - mu) / sd)
            r1 = te.pred.corr(te.y, method='spearman')
            r2 = te.score.corr(te.y, method='spearman')
            print(f'{str(months[i]):<12}{len(tr):>9}{len(te):>8}{r1:>+12.3f}{r2:>+19.3f}')
            preds.append(te)
        if not preds: print(); continue
        p = pd.concat(preds)
        print(f"{'ALL OOS':<12}{'':>9}{len(p):>8}{p.pred.corr(p.y,method='spearman'):>+12.3f}"
              f"{p.score.corr(p.y,method='spearman'):>+19.3f}")

        print('\n  THE DECISION: pick top 5 per day, out-of-sample')
        rows = []
        for dt, g in p.groupby('scan_date'):
            if len(g) < 6: continue
            rows.append(dict(d=dt, n=len(g),
                             fitted=g.nlargest(5, 'pred')[target].mean(),
                             cur=g.nlargest(5, 'score')[target].mean(),
                             allcand=g[target].mean(),
                             rnd=g.sample(min(5, len(g)), random_state=7)[target].mean()))
        s = pd.DataFrame(rows)
        print(f'    days={len(s)}   fitted {s.fitted.mean():+.3f} | current score {s.cur.mean():+.3f} '
              f'| random-5 {s.rnd.mean():+.3f} | all candidates {s.allcand.mean():+.3f}')
        print(f'    fitted beats current score on {(s.fitted>s.cur).mean()*100:.0f}% of days')
        s['m'] = pd.to_datetime(s.d).dt.to_period('M')
        print('   ' + s.groupby('m')[['fitted', 'cur', 'allcand']].mean().round(3).to_string().replace('\n', '\n   '))

        mu, sd = x[use].mean(), x[use].std().replace(0, 1)
        full = Ridge(alpha=5.0).fit((x[use] - mu) / sd, x.y)
        w = pd.Series(full.coef_, index=use).sort_values()
        print(f'\n  fitted weights (full sample, standardised — sign is what matters):')
        print('   ' + w.round(4).to_string().replace('\n', '\n   '))
        print()


if __name__ == '__main__':
    main()
