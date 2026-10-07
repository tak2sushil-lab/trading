"""Portfolio lab — the three live equity books replayed TOGETHER, then the bear-market plan, macro and earnings blind
spots, and two engine upgrades (Oct 6 2026, RESEARCH_REGISTRY §M).

Books (rules as live, data causal, official prints — factory/cache/night_owl/daily.parquet, 237 names, 2018 → today):
  CW  Clockwork  — each session at the close, the 3 WILD names with the highest share of up-gaps over the last 30 nights
                   (count lagged one night, as live; random ties), held to the next open. $10,000 (3 × $3,333).
  NO  Night Owl  — same window, top-3 by the overnight model (research_out/no_preds_all.parquet: yearly-refit
                   walk-forward of the production pipeline), skipping Clockwork's names (as live). $10,000.
  CT  Contrarian — 2 slots × $5,000: buy the biggest 3-day fallers (−8%…−35%) at the close, hold 5 sessions,
                   15% stop (checked at closes), no entry if earnings fall in the next 4 sessions. $10,000.
WILD = top third of the eligible names by trailing 60-day volatility, measured through YESTERDAY (causal — the live
engines use a hindsight list, personality.csv). Eligible = previous close ≥ $5 and 20-day median $vol ≥ $20M.
Earnings blackout (CW, NO) = skip a name whose report lands in tonight's hold (after-close today or before-open
tomorrow), from research_out/earnings_dates.parquet timestamps. Costs: CW/NO 2.1bp a night (TIERED, $3,333 names);
CT 10bp per round trip.

⚠️ Survivorship: the 237 names are today's universe, chosen in Jul 2026 partly on past performance. Read every level
as an upper bound and judge on differences between variants run on the same data.
Usage: venv/bin/python research_portfolio_lab.py
"""
import os, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, 'research_out')
BEAR = (pd.Timestamp('2021-11-01'), pd.Timestamp('2023-06-30'))
CW_COST, CT_COST = 2.1e-4, 10e-4
L = []


def say(s=''):
    print(s, flush=True); L.append(s)


def stats(day, cap=10_000.0, label=''):
    day = day.dropna()
    cum = day.cumsum(); dd = (cum - cum.cummax()).min()
    mo = day.groupby(day.index.to_period('M')).sum()
    yr = day.groupby(day.index.year).sum()
    bear = day[(day.index >= BEAR[0]) & (day.index <= BEAR[1])].sum()
    sh = day.mean() / day.std() * np.sqrt(252) if day.std() > 0 else np.nan
    return (f'  {label:44s} ${day.sum():+9,.0f} ({day.sum() / cap / (len(day) / 252) * 100:+5.1f}%/yr) Sharpe {sh:+.2f} | '
            f'maxDD ${dd:+8,.0f} ({dd / cap * 100:+.0f}%) | worst day ${day.min():+6,.0f} | months+ {(mo > 0).mean():.0%} | '
            f'2021-11→2023-06 ${bear:+7,.0f} | yrs ' + ' '.join(f'{y % 100:02d}:{v / cap * 100:+.0f}%' for y, v in yr.items()))


def load():
    d = pd.read_parquet(os.path.join(ROOT, 'factory/cache/night_owl/daily.parquet')).sort_values(['symbol', 'date'])
    d = d[(d.open > 0) & (d.close > 0)]
    o = d.pivot(index='date', columns='symbol', values='open')
    c = d.pivot(index='date', columns='symbol', values='close')
    v = d.pivot(index='date', columns='symbol', values='volume')
    return o, c, v


def earnings_nights(dates):
    """(symbol, session) whose overnight hold contains a report; and next-report session index for CT."""
    e = pd.read_parquet(os.path.join(OUT, 'earnings_dates.parquet'))
    e['ts'] = pd.to_datetime(e['ts'], utc=True).dt.tz_convert('America/New_York')
    e['day'] = e.ts.dt.tz_localize(None).dt.normalize()
    e['hr'] = e.ts.dt.hour + e.ts.dt.minute / 60
    di = pd.DatetimeIndex(dates)
    pos = di.searchsorted(e['day'])                       # index of the report day (or the next session)
    exposed = set()
    rep_idx = {}
    for s, p, hr, day in zip(e.symbol, pos, e.hr, e.day):
        if p >= len(di):
            continue
        if hr < 9.5:                                      # before the open → the night ending this morning
            sess = p - 1 if di[p] == day else p - 1
        elif hr >= 16:                                    # after the close → tonight
            sess = p if di[p] == day else p - 1
        else:
            continue
        if sess >= 0:
            exposed.add((s, di[sess]))
            rep_idx.setdefault(s, []).append(sess)
    return exposed, {k: np.array(sorted(v)) for k, v in rep_idx.items()}


def main():
    o, c, v = load()
    dates = c.index
    pc = c.shift(1)
    on = (o.shift(-1) / c - 1)                             # hold from session t's close to t+1's open
    cc = c / pc - 1
    gap = o / pc - 1
    cons30 = (gap > 0).astype(float).where(gap.notna()).rolling(30, min_periods=25).mean().shift(1)
    vol60 = cc.rolling(60, min_periods=40).std().shift(1)
    dv20 = (c * v).rolling(20, min_periods=10).median().shift(1)
    elig = (pc >= 5) & (dv20 >= 20e6) & on.notna()
    wild = elig & (vol60.where(elig).rank(axis=1, pct=True) >= 2 / 3)
    exposed, rep_idx = earnings_nights(dates)
    expo = pd.DataFrame(False, index=dates, columns=c.columns)
    for s, d in exposed:
        if s in expo.columns:
            expo.at[d, s] = True
    no = pd.read_parquet(os.path.join(OUT, 'no_preds_all.parquet')).pivot(index='date', columns='symbol', values='model').reindex(index=dates, columns=c.columns)
    rng = np.random.default_rng(42)
    start = pd.Timestamp('2018-03-01')
    days = [d for d in dates if d >= start and d < dates[-1]]

    def pick(score, mask, k, exclude=()):
        s = score.where(mask).dropna()
        s = s[~s.index.isin(exclude)]
        if s.empty:
            return []
        s = s + rng.uniform(0, 1e-9, len(s))              # random tie-break
        return list(s.nlargest(k).index)

    rows = []
    for d in days:
        w = wild.loc[d] & ~expo.loc[d]
        w_noblk = wild.loc[d]
        cw = pick(cons30.loc[d], w, 3)
        nw = pick(no.loc[d], w, 3, exclude=cw)
        no_alone = pick(no.loc[d], w, 3)
        cw_nb = pick(cons30.loc[d], w_noblk, 3)
        # merged ranker: average percentile of both signals, one 6-name book
        rk = (cons30.loc[d].where(w).rank(pct=True) + no.loc[d].where(w).rank(pct=True)) / 2
        mg = pick(rk, w, 6)
        r = on.loc[d]
        rows.append({'date': d, 'CW': r[cw].mean() if cw else np.nan, 'NO': r[nw].mean() if nw else np.nan,
                     'NO_alone': r[no_alone].mean() if no_alone else np.nan, 'CW_noblackout': r[cw_nb].mean() if cw_nb else np.nan,
                     'MERGED6': r[mg].mean() if mg else np.nan, 'WILD_EW': r[w_noblk].mean(), 'ALL_EW_ON': r[elig.loc[d]].mean(),
                     'MKT_CC': cc.loc[d][elig.loc[d]].mean(), 'WILD_CC': cc.loc[d][wild.loc[d]].mean(),
                     'cw_names': ','.join(cw), 'no_names': ','.join(nw), 'n_wild': int(w_noblk.sum())})
    N = pd.DataFrame(rows).set_index('date')
    # ── Contrarian slot simulation ──
    r3 = c / c.shift(3) - 1
    eligct = (pc >= 5) & ((c * v).rolling(20, min_periods=10).median() >= 5e6)
    di = {d: i for i, d in enumerate(dates)}
    slots, ct_pnl, ct_strict_pnl = [], {}, {}

    def run_ct(strict):
        held, pnl, trades = [], {}, []
        for d in days:
            i = di[d]
            day_p = 0.0
            still = []
            for pos in held:
                sym, ei, px0, sh = pos
                px_prev, px = c.iat[i - 1, c.columns.get_loc(sym)], c.at[d, sym]
                if np.isnan(px):
                    still.append(pos); continue
                day_p += sh * (px - px_prev)
                if i - ei >= 5 or px <= px0 * 0.85:
                    day_p -= 5000 * CT_COST
                    trades.append((sym, dates[ei], d, px / px0 - 1))
                else:
                    still.append(pos)
            held = still
            free = 2 - len(held)
            if free > 0:
                cand = r3.loc[d].where(eligct.loc[d]).dropna()
                cand = cand[(cand <= -0.08) & (cand > -0.35)].sort_values()
                taken = {p[0] for p in held}
                for sym in cand.index:
                    if free == 0:
                        break
                    if sym in taken:
                        continue
                    ri = rep_idx.get(sym)
                    if ri is not None:
                        if ((ri >= i) & (ri <= i + 4)).any():          # live rule: report within the next 4 sessions
                            continue
                        if strict and ((ri >= i - 3) & (ri < i)).any():  # upgrade: the fall came from a report
                            continue
                    held.append((sym, i, c.at[d, sym], 5000 / c.at[d, sym])); free -= 1
            pnl[d] = day_p
        return pd.Series(pnl), pd.DataFrame(trades, columns=['symbol', 'entry', 'exit', 'ret'])

    ct, ct_trades = run_ct(False)
    ct_s, ct_s_trades = run_ct(True)
    book = pd.DataFrame({'CW': (N.CW - CW_COST) * 10_000, 'NO': (N.NO - CW_COST) * 10_000, 'CT': ct}).fillna(0.0)
    book['FLEET'] = book.sum(axis=1)

    say(f'1. THE FLEET AS IT RUNS TODAY (rules replayed {days[0].date()} → {days[-1].date()}, {len(days)} sessions, $10k per book)')
    for k in ['CW', 'NO', 'CT']:
        say(stats(book[k], 10_000, k))
    say(stats(book.FLEET, 30_000, 'FLEET (all three, $30k)'))
    say('  daily correlation:  CW-NO {:+.2f} · CW-CT {:+.2f} · NO-CT {:+.2f} · CW-WILD overnight {:+.2f} · CT-market {:+.2f}'.format(
        book.CW.corr(book.NO), book.CW.corr(book.CT), book.NO.corr(book.CT), book.CW.corr(N.WILD_EW), book.CT.corr(N.MKT_CC)))
    say(stats((N.WILD_EW - CW_COST) * 10_000, 10_000, 'reference: every WILD name overnight'))
    say(stats(N.MKT_CC * 10_000, 10_000, 'reference: every name, buy and hold'))
    tr = ct_trades
    say(f'  CT: {len(tr)} trades, mean {tr.ret.mean() * 100:+.2f}%, win {(tr.ret > 0).mean():.0%}, stopped {(tr.ret <= -0.15).mean():.0%}')

    say('\n2. CONSOLIDATION UPGRADES (same nights)')
    say(stats((N.NO_alone - CW_COST) * 10_000, 10_000, 'NO without skipping Clockwork\'s names'))
    say(stats((N.MERGED6 - CW_COST) * 20_000, 20_000, 'ONE merged ranker, top-6, $20k'))
    say(stats(book.CW + book.NO, 20_000, 'today: CW top-3 + NO top-3, $20k'))
    say(stats((N.CW_noblackout - CW_COST) * 10_000, 10_000, 'CW WITHOUT the earnings blackout'))
    say(stats(ct_s, 10_000, 'CT, also skipping fallers whose drop came from a report'))
    t2 = ct_s_trades
    say(f'  CT strict: {len(t2)} trades, mean {t2.ret.mean() * 100:+.2f}%, win {(t2.ret > 0).mean():.0%}')

    # ── bear-market overlays (rules fixed before looking) ──
    say('\n3. BEAR-MARKET PLANS on the two overnight books (CW+NO, $20k) — rules fixed before running')
    base = book.CW + book.NO
    idx = (1 + N.WILD_CC.fillna(0)).cumprod()
    trend_ok = (idx > idx.rolling(200, min_periods=150).mean()).shift(1).fillna(True)
    say(stats(base, 20_000, 'base'))
    say(stats(base * np.where(trend_ok, 1.0, 0.5), 20_000, 'TREND: half size when WILD basket < its 200d avg'))
    say(stats(base * np.where(trend_ok, 1.0, 0.0), 20_000, 'TREND: flat when WILD basket < its 200d avg'))
    rv = base.rolling(20, min_periods=15).std().shift(1)
    tgt = rv.expanding(min_periods=120).median()
    say(stats(base * (tgt / rv).clip(upper=1.0).fillna(1.0), 20_000, 'VOL TARGET: shrink when 20d vol > its long-run median'))
    eq, hwm, scale, out = 0.0, 0.0, 1.0, []
    for x in base.values:
        out.append(x * scale); eq += x * scale; hwm = max(hwm, eq)
        if eq < hwm - 0.15 * 20_000: scale = 0.5
        elif eq > hwm - 0.05 * 20_000: scale = 1.0
    say(stats(pd.Series(out, index=base.index), 20_000, 'DD BRAKE: half size >15% below the high, back at 5%'))
    rty = rty_overnight()
    if rty is not None:
        b = (book.CW + book.NO) / 20_000
        j = pd.concat([b.rename('b'), rty.rename('r')], axis=1).dropna()
        cov = (j.b * j.r).rolling(120, min_periods=60).mean() - j.b.rolling(120, min_periods=60).mean() * j.r.rolling(120, min_periods=60).mean()
        beta = (cov / j.r.rolling(120, min_periods=60).var(ddof=0)).shift(1).clip(0, 3)
        hedged = (j.b - beta * j.r - 1.5e-4 * beta.notna()) * 20_000
        say(stats(j.b * 20_000, 20_000, 'unhedged, same nights (2021+)'))
        say(stats(hedged.dropna(), 20_000, 'HEDGE: short Russell 2000 futures overnight × rolling beta'))
        say(f'  average hedge beta {beta.mean():.2f}; Russell 2000 overnight itself {j.r.mean() * 1e4:+.1f}bp/night')

    # ── macro nights ──
    say('\n4. MACRO NIGHTS — mean / std of the night\'s return (bp) for the overnight books and the WILD basket')
    me = pd.read_parquet(os.path.join(OUT, 'macro_events.parquet'))
    dl = list(dates)
    tag = pd.Series('normal', index=N.index)
    for d, ev in zip(me.date, me.event):
        if d not in di:
            continue
        i = di[d]
        if ev == 'FOMC':
            if i - 1 >= 0 and dates[i - 1] in tag.index: tag[dates[i - 1]] = 'night BEFORE FOMC day'
            if d in tag.index: tag[d] = 'night AFTER FOMC decision'
        else:
            if i - 1 >= 0 and dates[i - 1] in tag.index and tag[dates[i - 1]] == 'normal':
                tag[dates[i - 1]] = {'NFP': 'jobs-report morning', 'CPI_MID': 'mid-month 08:30 (CPI/PPI/retail)'}.get(ev, 'other 08:30 release')
    two = N.index >= pd.Timestamp('2021-01-04')
    for t in ['normal', 'night BEFORE FOMC day', 'night AFTER FOMC decision', 'jobs-report morning', 'mid-month 08:30 (CPI/PPI/retail)', 'other 08:30 release']:
        m = (tag == t) & (two if '08:30' in t or 'jobs' in t or t == 'normal' else True)
        x = N[m]
        cwnet = (x.CW + x.NO) / 2 * 1e4
        say(f'  {t:34s} n={m.sum():4d} | CW+NO {cwnet.mean():+6.1f}bp (sd {cwnet.std():5.0f}) | WILD basket {x.WILD_EW.mean() * 1e4:+6.1f}bp '
            f'(sd {x.WILD_EW.std() * 1e4:4.0f}) | every name {x.ALL_EW_ON.mean() * 1e4:+5.1f}bp')

    # ── earnings nights ──
    say('\n5. EARNINGS NIGHTS — WILD names on their own report night vs ordinary nights (the blackout\'s job)')
    ws = on.where(wild)
    rep = ws.where(expo).stack(); nrm = ws.where(~expo).stack()
    for lab, s in [('report night', rep), ('ordinary night', nrm)]:
        say(f'  {lab:16s} n={len(s):7,d} mean {s.mean() * 1e4:+6.1f}bp sd {s.std() * 1e4:5.0f}bp | worse than −10%: {(s < -0.10).mean():.2%} '
            f'| better than +10%: {(s > 0.10).mean():.2%}')
    N.assign(tag=tag).to_parquet(os.path.join(OUT, 'portfolio_lab_nights.parquet'))
    book.to_parquet(os.path.join(OUT, 'portfolio_lab_books.parquet'))
    open(os.path.join(OUT, 'portfolio_lab_report.txt'), 'w').write('\n'.join(L))


def rty_overnight():
    import sqlite3
    try:
        cx = sqlite3.connect(os.path.join(ROOT, 'market_data.db'))
        r = pd.read_sql("select ts_utc, open, close from futures_bars_5m where symbol='RTY'", cx)
    except Exception:
        return None
    r['ts'] = pd.to_datetime(r.ts_utc.str.slice(0, 19).str.replace('T', ' ')).dt.tz_localize('UTC').dt.tz_convert('America/New_York')
    r['d'] = r.ts.dt.tz_localize(None).dt.normalize(); r['hm'] = r.ts.dt.strftime('%H:%M')
    cl = r[r.hm == '15:55'].groupby('d').close.last()
    op = r[r.hm == '09:30'].groupby('d').open.first()
    nxt = op.reindex(cl.index, method=None)
    s = pd.Series(op.values, index=op.index)
    # night after session d: close at 16:00 of d → open 09:30 of the next session that has an open
    od = s.index
    out = {}
    for d, px in cl.items():
        k = od.searchsorted(d + pd.Timedelta(days=1))
        if k < len(od) and (od[k] - d).days <= 4:
            out[d] = s.iloc[k] / px - 1
    x = pd.Series(out)
    return x[x.abs() < 0.05]                                # drop contract-roll jumps


if __name__ == '__main__':
    main()
