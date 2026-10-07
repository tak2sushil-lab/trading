"""Short the morning gappers — the PROPER test: real entry times, minute-by-minute stops, walk-forward model, 2018-2026.
(Oct 5-6 2026, RESEARCH_REGISTRY §K)

research_gapper_short.py found +110bp/day shorting each morning's top-10 gappers at the official open, but a 5-min
spot check showed the fade is front-loaded into the opening auction. Here every gapper day gets its own 1-MINUTE bars
(DataBento XNAS.ITCH, May 2018 →) so a short can be entered at a price we could really get after the open:
  entries  decide on bars through 09:31 · 09:35 · 09:45 · 10:00, FILL at the NEXT minute's close (one-bar gap)
           (also the official open, for reference only — auction fills unproven)
  exit     the last minute bar's close before 16:00, or a STOP: first minute whose high ≥ entry×(1+stop); if that
           minute OPENED above the stop (a jump / halt re-open) the fill is that minute's open — never better.
  costs    spread+commission charged per round trip; borrow is NOT known historically (IBKR snapshot is tonight
           only) and is charged as a flat daily scenario.
Population each morning: listed US common stocks, previous close ≥ $2, 20-day median $vol ≥ $1M, official gap ≥ +5%
(daily data: research_out/broad_daily_2018.parquet + broad_daily_2024.parquet, yfinance — survivorship: delisted
names missing, which for SHORTS probably understates the fade).
Model: gradient boosting on inputs known AT THE ENTRY TIME (daily features lagged one session + today's gap + what the
first minutes did), walk-forward, re-fit every 6 months on all earlier data, out of sample 2019-07 → 2026-10.

Stages (each cached, resumable):  events → cost → pull → outcomes → model
Usage: venv/bin/python research_gapper_intraday.py quote|pull|run
"""
import os, sys, time, warnings, concurrent.futures as cf
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
import research_gapper_short as S
OUT = os.path.join(ROOT, 'research_out')
MIN_DIR = os.path.join(OUT, 'gapper_1m')                      # one parquet per trading day
EVENTS = os.path.join(OUT, 'gapper_events_2018.parquet')
PANEL18 = os.path.join(OUT, 'gapper_short_panel_2018.parquet')
OUTCOMES = os.path.join(OUT, 'gapper_outcomes.parquet')
DATASET = 'XNAS.ITCH'
FIRST_DAY = pd.Timestamp('2018-05-02')
ENTRIES = {'0931': 1, '0935': 5, '0945': 15, '1000': 30}     # minutes after 09:30 (entry = close of the minute ending then)
STOPS = (None, .10, .15, .20, .30)
SPEND_CAP = 25.0                                               # dollars — refuse to pull above this


def client():
    from databento_keys import historical    # primary key first, reserve account when it is refused/expired
    return historical()


def events():
    if os.path.exists(EVENTS):
        return pd.read_parquet(EVENTS)
    a = pd.read_parquet(os.path.join(OUT, 'broad_daily_2018.parquet'))
    b = pd.read_parquet(os.path.join(OUT, 'broad_daily_2024.parquet'))
    d = pd.concat([a[a.date < b.date.min()], b], ignore_index=True)
    p = S.build_lean(d, PANEL18)            # one stock at a time — the full 8-year panel does not fit in memory
    q = S.tradeable(p, 2.0)
    e = q[(q.gap >= 0.05) & (q.date >= FIRST_DAY)].copy()
    e.to_parquet(EVENTS, index=False)
    return e


def window(day):
    d = pd.Timestamp(day).tz_localize('America/New_York')
    return (d + pd.Timedelta(hours=9, minutes=30)).tz_convert('UTC'), (d + pd.Timedelta(hours=16)).tz_convert('UTC')


def quote(ev, c, sample=40):
    days = sorted(ev.date.unique())
    rng = np.random.default_rng(5)
    pick = sorted(rng.choice(days, min(sample, len(days)), replace=False))
    cost, n = 0.0, 0
    for day in pick:
        syms = sorted(ev[ev.date == day].symbol)
        s, e = window(day)
        try:
            cost += c.metadata.get_cost(dataset=DATASET, symbols=syms, schema='ohlcv-1m', start=s.isoformat(), end=e.isoformat(), stype_in='raw_symbol')
        except Exception as ex:
            if 'could be resolved' not in str(ex):
                raise
            continue                       # no symbol existed under that ticker on that date — nothing billed
        n += len(syms)
    per = cost / n
    total = per * len(ev)
    print(f'quote: {len(pick)} sample days, {n} symbol-days → ${cost:.4f} (${per:.6f}/symbol-day) → all {len(ev):,} symbol-days ≈ ${total:.2f}')
    return total


def pull_day(c, day, syms):
    path = os.path.join(MIN_DIR, pd.Timestamp(day).strftime('%Y%m%d') + '.parquet')
    if os.path.exists(path):
        return 'cached'
    s, e = window(day)
    for attempt in range(3):
        try:
            data = c.timeseries.get_range(dataset=DATASET, symbols=syms, schema='ohlcv-1m', start=s.isoformat(),
                                          end=e.isoformat(), stype_in='raw_symbol')
            df = data.to_df()
            if df.empty:
                pd.DataFrame(columns=['ts', 'symbol', 'open', 'high', 'low', 'close', 'volume']).to_parquet(path, index=False)
                return 'empty'
            df = df.reset_index()
            df['ts'] = pd.to_datetime(df['ts_event']).dt.tz_convert('America/New_York').dt.tz_localize(None)
            df[['ts', 'symbol', 'open', 'high', 'low', 'close', 'volume']].to_parquet(path, index=False)
            return 'ok'
        except Exception as ex:
            msg = str(ex)
            if 'could be resolved' in msg:
                pd.DataFrame(columns=['ts', 'symbol', 'open', 'high', 'low', 'close', 'volume']).to_parquet(path, index=False)
                return 'unresolved'
            if 'symbol' in msg.lower() and attempt == 0 and len(syms) > 1:
                # one bad ticker can fail the whole request → keep only the ones that resolve
                good = []
                for sym in syms:
                    try:
                        c.metadata.get_cost(dataset=DATASET, symbols=[sym], schema='ohlcv-1m', start=s.isoformat(), end=e.isoformat(), stype_in='raw_symbol')
                        good.append(sym)
                    except Exception:
                        pass
                syms = good or syms
                continue
            time.sleep(3 * (attempt + 1))
    return 'fail: ' + msg[:120]


def pull(ev):
    os.makedirs(MIN_DIR, exist_ok=True)
    c = client()
    total = quote(ev, c)
    if total > SPEND_CAP:
        raise SystemExit(f'quote ${total:.2f} exceeds the ${SPEND_CAP} cap — not pulling')
    days = sorted(ev.date.unique())
    t0, res = time.time(), {}
    with cf.ThreadPoolExecutor(4) as ex:
        futs = {ex.submit(pull_day, c, day, sorted(ev[ev.date == day].symbol)): day for day in days}
        for i, f in enumerate(cf.as_completed(futs)):
            r = f.result(); res[r.split(':')[0]] = res.get(r.split(':')[0], 0) + 1
            if i % 100 == 0:
                print(f'  pulled {i}/{len(days)} days {res} {time.time() - t0:.0f}s', flush=True)
    print(f'pull done: {res} in {time.time() - t0:.0f}s')


def outcomes(ev):
    """Per event: entry prices, early-session features, and the short result to the close for every stop level."""
    if os.path.exists(OUTCOMES):
        return pd.read_parquet(OUTCOMES)
    rows = []
    for day, g in ev.groupby('date'):
        path = os.path.join(MIN_DIR, pd.Timestamp(day).strftime('%Y%m%d') + '.parquet')
        if not os.path.exists(path):
            continue
        m = pd.read_parquet(path)
        if m.empty:
            continue
        m = m[(m.ts.dt.time >= pd.Timestamp('09:30').time()) & (m.ts.dt.time < pd.Timestamp('16:00').time())]
        for sym, x in m.groupby('symbol'):
            x = x.sort_values('ts')
            mins = ((x.ts - x.ts.dt.normalize()) / pd.Timedelta(minutes=1)).values - 570   # minutes after 09:30
            if len(x) < 30 or mins.max() < 300:
                continue
            o, h, l, c, v = (x[k].values.astype(float) for k in ('open', 'high', 'low', 'close', 'volume'))
            r = {'date': day, 'symbol': sym, 'first_open': o[0], 'first_min': mins[0], 'exit': c[-1], 'n_min': len(x)}
            for name, k in ENTRIES.items():
                # decide on bars that END by 09:30+k; FILL on the next minute's close (one-bar gap — the same print
                # must never be both an input and the entry price: that is how bid-ask bounce fakes a reversal)
                dec = np.where(mins <= k - 1)[0]
                fil = np.where((mins >= k) & (mins <= k + 2))[0]
                if len(dec) == 0 or len(fil) == 0:
                    continue
                i, j = dec[-1], fil[0]
                px = c[j]
                r['px_' + name] = px
                after = slice(j + 1, None)
                ho, hh = o[after], h[after]
                r[f'mae_{name}'] = (hh.max() / px - 1) if len(hh) else 0.0
                for st in STOPS:
                    if st is None:
                        r[f'sh_{name}_none'] = 1 - c[-1] / px
                        continue
                    lvl = px * (1 + st)
                    hit = np.where(hh >= lvl)[0]
                    if len(hit):
                        fill = max(lvl, ho[hit[0]])              # jumped through the stop → fill at that minute's open
                        r[f'sh_{name}_{int(st * 100)}'] = 1 - fill / px
                    else:
                        r[f'sh_{name}_{int(st * 100)}'] = 1 - c[-1] / px
                # what the first minutes showed — bars through the decision minute only
                r[f'ret_open_{name}'] = c[i] / o[0] - 1
                r[f'hi_open_{name}'] = h[:i + 1].max() / o[0] - 1
                r[f'lo_open_{name}'] = l[:i + 1].min() / o[0] - 1
                r[f'dvol_{name}'] = float((c[:i + 1] * v[:i + 1]).sum())
            rows.append(r)
    out = pd.DataFrame(rows)
    out = out.merge(ev, on=['date', 'symbol'], how='inner')
    # Same company? Yahoo files a renamed company's history under TODAY's ticker; DataBento resolves the ticker
    # as it was on that date, which can be a different company. Compare the day's open→close move from both
    # sources (a ratio, so splits cancel); a gap beyond 3 points means the bars are not the same stock.
    out['db_oc'] = out['exit'] / out['first_open'] - 1
    out['yf_oc'] = -out['short']
    out['src_diff'] = (out['db_oc'] - out['yf_oc']).abs()
    n0 = len(out)
    out = out[out['src_diff'] <= 0.03]
    print(f'ticker/source match check: kept {len(out):,} of {n0:,} ({len(out) / max(n0, 1):.1%}); '
          f'dropped {n0 - len(out):,} whose DataBento and Yahoo day moves disagree by > 3 points', flush=True)
    out['sh_open_none'] = out['short']                                # official open → official close (daily data; auction)
    out.to_parquet(OUTCOMES, index=False)
    return out


def tstat(x):
    x = pd.Series(x).dropna()
    return x.mean() / (x.std() / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


COST_BP = 30          # spread + commission per round trip (small caps, market orders after the open)
BORROW_BP = 10        # stress scenario: ~36%/yr average borrow fee — the real rate on the gap day is unknown


def daily(picks, col, cost=COST_BP, borrow=0):
    """Equal-weight book per day, bp. On a $10,000 short book (e.g. 5 x $2,000), bp/day == $/day."""
    return (picks[col] - (cost + borrow) / 1e4).groupby(picks.date).mean() * 1e4


def halves(day):
    return day.groupby(day.index.year.astype(str) + 'H' + ((day.index.month > 6) + 1).astype(str)).mean()


def summarise(day, label):
    dd = (day / 1e4).cumsum()
    yr = day.groupby(day.index.year).mean()
    hv = halves(day)
    return (f'  {label:50s} days={len(day):4d} {day.mean():+7.1f}bp/day (t {tstat(day):+.2f}) days+ {(day > 0).mean():.0%} '
            f'worst {day.min():+6.0f} | sum-DD {(dd - dd.cummax()).min() * 100:+5.0f}% | halves+ {int((hv > 0).sum())}/{len(hv)} | yrs '
            + ' '.join(f'{k}:{v:+.0f}' for k, v in yr.items()))


def walk_forward(D, feats, target):
    from sklearn.ensemble import HistGradientBoostingRegressor
    X = D[feats].copy()
    for c_ in feats:
        if c_ not in ('log_price_l', 'log_dvol_l', 'age', 'dow', 'mkt_gap', 'n_gappers', 'gap_rank'):
            X[c_] = D.groupby('date')[c_].rank(pct=True) - 0.5
    y = D[target].clip(-0.5, 0.5)
    pred = pd.Series(np.nan, index=D.index)
    cuts = list(pd.date_range('2019-07-01', D.date.max(), freq='6MS'))
    cuts.append(D.date.max() + pd.Timedelta(days=1))          # the last, partial half-year is tested too
    for a, b in zip(cuts[:-1], cuts[1:]):
        te = (D.date >= a) & (D.date < b); tr = D.date < a - pd.Timedelta(days=3)
        if te.sum() == 0 or tr.sum() < 3000:
            continue
        mdl = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=200,
                                            l2_regularization=1.0, loss='absolute_error', random_state=7).fit(X[tr], y[tr])
        pred[te] = mdl.predict(X[te])
    return pred


def run():
    ev = events()
    O = outcomes(ev)
    print(f'events {len(ev):,} ({ev.date.min().date()} → {ev.date.max().date()}); usable with minute data {len(O):,} '
          f'({len(O) / len(ev):.0%}); per year: ' + ' '.join(f'{y}:{n}' for y, n in O.groupby(O.date.dt.year).size().items()))
    L = ['\nA. NO MODEL — short the 10 biggest official gaps each morning, equal weight, cover at the close']
    for ent in ['open', '0931', '0935', '0945', '1000']:
        for st in ([None, .20] if ent != 'open' else [None]):
            col = f'sh_{ent}_{"none" if st is None else int(st * 100)}'
            t = O[O.gap_rank <= 10].dropna(subset=[col])
            for cost in (0, COST_BP):
                L.append(summarise(daily(t, col, cost), f'entry {ent}{" (auction, reference)" if ent == "open" else ""}, '
                                                        f'stop {"none" if st is None else f"+{st:.0%}"}, cost {cost}bp'))
    print('\n'.join(L), flush=True); L = []

    base = list(S.FEATS)
    for ent in ['0931', '0935', '1000']:
        early = [f'ret_open_{ent}', f'hi_open_{ent}', f'lo_open_{ent}', f'dvol_{ent}']
        D = O.dropna(subset=[f'px_{ent}', f'sh_{ent}_none']).copy()
        D[f'dvol_{ent}'] = D[f'dvol_{ent}'] / D['dvol20_l']
        D['pred'] = walk_forward(D, base + early, f'sh_{ent}_none')
        R = D.dropna(subset=['pred'])
        tgt, st20 = f'sh_{ent}_none', f'sh_{ent}_20'
        ic = R.groupby('date').apply(lambda d: d.pred.rank().corr(d[tgt].rank()) if len(d) >= 5 else np.nan).dropna()
        L.append(f'\nB. MODEL decides on data through {ent[:2]}:{ent[2:]}, fills one minute later — OOS {R.date.min().date()} → '
                 f'{R.date.max().date()} ({R.date.nunique()} days, ~{len(R) / R.date.nunique():.0f} gappers/day)')
        L.append(f'  within-day IC {ic.mean():+.4f} (t {tstat(ic):+.2f}); by year ' +
                 ' '.join(f'{k}:{v:+.3f}' for k, v in ic.groupby(ic.index.year).mean().items()))
        L.append('  -- all lines below: +20% stop (checked minute by minute), 30bp cost, out-of-sample days only --')
        mk = lambda k: R.sort_values('pred', ascending=False).groupby('date').head(k)
        rule = lambda col, k: R.sort_values(col, ascending=False).groupby('date').head(k)
        for k in (3, 5, 10):
            L.append(summarise(daily(mk(k), st20), f'MODEL top-{k}'))
        L.append(summarise(daily(mk(5), tgt), 'MODEL top-5, NO stop'))
        L.append(summarise(daily(mk(5), st20, borrow=BORROW_BP), f'MODEL top-5, + {BORROW_BP}bp/day borrow stress'))
        L.append(summarise(daily(rule('gap', 5), st20), 'RULE top-5 by gap size'))
        L.append(summarise(daily(rule('vol20_l', 5), st20), 'RULE top-5 most volatile gappers (20d vol)'))
        L.append(summarise(daily(rule(f'ret_open_{ent}', 5), st20), 'RULE top-5 that rose most since the open'))
        L.append(summarise(daily(R, st20), 'EVERY gapper ≥ +5%'))
        rnd = pd.concat([daily(R.sample(frac=1, random_state=i).groupby('date').head(5), st20)
                         for i in range(20)], axis=1).mean(axis=1)
        L.append(summarise(rnd, 'CONTROL random 5 gappers (avg of 20 draws)'))
        p5 = mk(5)
        L.append(f'  model top-5 profile: median price ${p5.price_l.median():.1f} (under $5: {(p5.price_l < 5).mean():.0%}), '
                 f'median gap {p5.gap.median():+.1%}, median $vol ${p5.dvol20_l.median() / 1e6:.1f}M, '
                 f'hit the +20% stop {(p5[f"mae_{ent}"] >= .2).mean():.1%}, worst single short {p5[st20].min() * 100:+.1f}%')
        top = sorted(base + early, key=lambda f: -abs(R.groupby('date').apply(
            lambda d: d[f].rank().corr(d[tgt].rank()) if len(d) >= 5 else np.nan).mean()))[:8]
        L.append('  strongest single inputs (within-day IC with the short result): ' + ', '.join(
            f'{f} {R.groupby("date").apply(lambda d: d[f].rank().corr(d[tgt].rank()) if len(d) >= 5 else np.nan).mean():+.3f}' for f in top))
        print('\n'.join(L), flush=True); L = []
        R.to_parquet(os.path.join(OUT, f'gapper_intraday_preds_{ent}.parquet'), index=False)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'run'
    ev = events()
    if cmd == 'quote':
        print(f'{len(ev):,} gapper symbol-days, {ev.date.nunique()} days, {ev.symbol.nunique()} symbols')
        quote(ev, client())
    elif cmd == 'pull':
        pull(ev)
    else:
        run()
