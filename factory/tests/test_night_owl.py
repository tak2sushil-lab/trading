"""
Night Owl test suite (Oct 5 2026). Run:  venv/bin/python -m factory.tests.test_night_owl
Uses the real daily cache + trained model (factory/cache/night_owl) and a SCRATCH copy of trades.db
for the engine tests — the live database is never written.

  parity       live scoring (history before the day + that day's opens only) reproduces the scores the
               training pipeline produces for the same day from the FULL history → no train/serve skew
  no-lookahead appending future sessions cannot change a past day's model inputs
  lag/filter   every stock input is exactly one session old; the universe uses YESTERDAY's close
  engine       other-book exclusion, the 3-name cap (incl. unconfirmed morning sells), wait-for-Clockwork
  artifact     the saved model's feature list matches what the code builds today
"""
from __future__ import annotations
import os, shutil, sqlite3, sys, tempfile
import numpy as np
import pandas as pd

from factory import night_owl_model as M

PASS, FAIL = "  ✅", "  ❌"
_fails = []


def check(name, cond, detail=""):
    print((PASS if cond else FAIL) + f" {name}  {detail}")
    if not cond:
        _fails.append(name)


def _full_rows(daily, art, day):
    X, meta, _, _ = M.make_X(M.build_panel(daily[daily['date'] <= day]), art['sector_cats'], art['dna_cats'])
    sel = (meta['date'] == day).values
    out = meta.loc[sel, ['symbol']].copy()
    out['score_full'] = art['model'].predict(X[sel])
    return out, X[sel].reset_index(drop=True), meta[sel].reset_index(drop=True)


def test_parity_and_lookahead(daily, art):
    days = sorted(daily['date'].unique())
    for day in (days[-1], days[-40], days[-250]):
        day = pd.Timestamp(day)
        opens = daily[daily['date'] == day].set_index('symbol')['open'].to_dict()
        live = M.score_day(day, daily, opens, art)
        full, _, _ = _full_rows(daily, art, day)
        j = live.merge(full, on='symbol')
        diff = (j['score'] - j['score_full']).abs().max()
        same_top = list(live[live.wild].symbol.head(5)) == list(
            j[j.wild].sort_values('score_full', ascending=False).symbol.head(5))
        check(f"parity {day.date()}: live vs training scores", len(j) == len(live) == len(full) and diff < 1e-6 and same_top,
              f"n={len(j)} max|Δscore|={diff:.2e} top-5 WILD identical={same_top}")
    # no look-ahead: the same past day computed with and without ~6 months of later data
    day = pd.Timestamp(days[-130])
    a, Xa, _ = _full_rows(daily, art, day)
    later = daily                                            # includes everything after `day`
    X2, meta2, _, _ = M.make_X(M.build_panel(later), art['sector_cats'], art['dna_cats'])
    sel = (meta2['date'] == day).values
    Xb = X2[sel].reset_index(drop=True)
    same = Xa.shape == Xb.shape and np.allclose(Xa.fillna(-9).values, Xb.fillna(-9).values, atol=1e-9)
    check(f"no look-ahead {day.date()}: later data leaves the day's inputs unchanged", same,
          f"{Xa.shape} vs {Xb.shape}")


def test_lag_and_filter(daily, art):
    p = M.build_panel(daily[daily['date'] >= daily['date'].max() - pd.Timedelta(days=500)])
    X, meta, _, _ = M.make_X(p, art['sector_cats'], art['dna_cats'])
    # (a) universe uses YESTERDAY's close: every kept row's previous close is >= $5
    pp = p.sort_values(['symbol', 'date'])
    prev = pp.groupby('symbol')['close'].shift(1)
    prev_map = pd.Series(prev.values, index=pd.MultiIndex.from_frame(pp[['symbol', 'date']]))
    kept_prev = prev_map.reindex(pd.MultiIndex.from_frame(meta[['symbol', 'date']])).values
    check("universe filter uses the previous close", np.nanmin(kept_prev) >= M.MIN_PRICE,
          f"min previous close of a kept row ${np.nanmin(kept_prev):.2f}")
    # (b) exactly one session of lag: X's r1 for (sym, t) ranks the raw r1 of sym's PREVIOUS session
    sym = meta['symbol'].value_counts().index[0]
    s = pp[pp['symbol'] == sym].set_index('date')
    m = meta[meta['symbol'] == sym].reset_index()
    t = m['date'].iloc[-1]
    prev_date = s.index[s.index.get_loc(t) - 1]
    raw_prev = s.loc[prev_date, 'r1']
    day_rows = meta['date'] == t
    raw_day = pd.Series(p.set_index(['symbol', 'date'])['r1']).groupby(level=0).shift(1)
    vals = raw_day.reindex(pd.MultiIndex.from_frame(meta.loc[day_rows, ['symbol', 'date']]))
    expect = vals.rank(pct=True).values - 0.5
    got = X.loc[day_rows, 'r1'].values
    check(f"one-session lag ({sym} {t.date()} uses {prev_date.date()})",
          np.allclose(np.nan_to_num(expect, nan=-9), np.nan_to_num(got, nan=-9)) and not np.isnan(raw_prev),
          f"raw r1 of the previous session {raw_prev:+.4f}")


def test_artifact(art, daily):
    X, _, _, _ = M.make_X(M.build_panel(daily[daily['date'] >= daily['date'].max() - pd.Timedelta(days=400)]),
                          art['sector_cats'], art['dna_cats'])
    check("model feature list matches the code", list(X.columns) == art['features'], f"{len(art['features'])} inputs")
    check("model trained on enough history", art['n_rows'] > 300_000 and art['first_date'] < '2016-01-01',
          f"{art['n_rows']:,} rows from {art['first_date']}")
    a = {'created': '2026-10-05T00:09:26-04:00', 'trained_through': '2026-10-01'}
    rule = [M.model_is_current(a, d) for d in ('2026-10-30', '2026-11-02', '2026-12-01')]
    grace = [M.model_is_current(a, d, grace_months=1) for d in ('2026-11-02', '2026-12-01')]
    check("re-trains every calendar month (prep): current Oct, stale Nov 2", rule == [True, False, False], str(rule))
    check("scorer grace: last month's model OK on the 1st, warns a month later", grace == [True, False], str(grace))


def test_engine_rules():
    """Run the real entry logic against a scratch DB, with the bridge/earnings/scoring stubbed."""
    import factory.live.night_owl as E
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, 'trades.db')
    src = sqlite3.connect('/Users/sushil/trading/trades.db')
    dst = sqlite3.connect(db)
    src.backup(dst); src.close(); dst.close()
    saved = dict(DB=E.DB, MODE=E.MODE, bridge_quote=E.bridge_quote, days_to_earnings=E.days_to_earnings,
                 score_today=E.score_today, now_et=E.now_et, send_telegram=E.send_telegram, tide_ok=E.tide_ok)
    try:
        E.DB, E.MODE = db, 'SHADOW'
        E.init_db()
        today = '2026-10-05'
        c = sqlite3.connect(db)
        for tbl in ('night_owl_trades', 'night_owl_scores'):
            c.execute(f"DELETE FROM {tbl}")
        c.execute("DELETE FROM overnight_trades WHERE entry_date=?", (today,))
        c.execute("UPDATE overnight_trades SET status='CLOSED' WHERE status IN ('OPEN','PENDING_ENTRY','PENDING_EXIT')")
        c.execute("UPDATE wave_trades SET status='CLOSED' WHERE status IN ('OPEN','PENDING_ENTRY','PENDING_EXIT')")
        c.execute("UPDATE contrarian_trades SET status='CLOSED' WHERE status IN ('OPEN','PENDING_ENTRY','PENDING_EXIT')")
        c.execute("UPDATE trades SET status='LOSS' WHERE status='OPEN'")
        names = ['AAA', 'BBB', 'CCC', 'DDD', 'EEE', 'FFF']
        for i, s in enumerate(names):
            c.execute("INSERT INTO night_owl_scores(score_date,symbol,score,rank,wild,wild_rank,gap,prev_close,model_version,created_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)", (today, s, 1 - i * 0.1, i + 1, 1, i + 1, 0.01, 50.0, 'test', today))
        c.execute("INSERT INTO overnight_trades(symbol,entry_date,entry_time,entry_price,shares,status,mode) "
                  "VALUES('BBB',?, '15:42:00', 50, 60, 'PENDING_ENTRY', 'LIVE')", (today,))       # Clockwork picked BBB
        c.execute("INSERT INTO wave_trades(symbol, status, mode) VALUES('DDD','OPEN','LIVE')")    # Wave Rider holds DDD
        c.commit(); c.close()
        import datetime as _dt
        E.now_et = lambda: _dt.datetime(2026, 10, 5, 15, 43, tzinfo=E.ET)
        E.bridge_quote = lambda s: 50.0
        E.days_to_earnings = lambda s: 0 if s == 'CCC' else 30                                  # CCC reports tonight
        E.score_today = lambda force=False: True
        E.send_telegram = lambda m: None
        E.tide_ok = lambda: False                                                               # bear switch OFF
        E.scan_and_enter()
        n = E._q("SELECT COUNT(*) FROM night_owl_trades WHERE entry_date=?", (today,))[0][0]
        check("Basket Tide OFF blocks every new entry", n == 0, f"{n} rows")
        E.tide_ok = lambda: True
        E.scan_and_enter()
        got = [r['symbol'] for r in E._q("SELECT symbol FROM night_owl_trades WHERE entry_date=? ORDER BY id", (today,), True)]
        check("engine shares Clockwork's name, skips Wave Rider's + earnings", got == ['AAA', 'BBB', 'EEE'],
              f"entered {got} (expected AAA, BBB, EEE: BBB is Clockwork's (allowed), CCC earnings, DDD Wave Rider)")
        sh = E._q("SELECT shares FROM night_owl_trades WHERE symbol='AAA'")[0][0]
        check("sizing = $3,333 a name", sh == int(E.PER_NAME / 50.0), f"{sh} shares at $50")
        sh = E._q("SELECT shares FROM night_owl_trades WHERE symbol='BBB'")[0][0]
        check("shared name: full slot when Clockwork has $3,000 in it (cap $6,667)", sh == int(E.PER_NAME / 50.0), f"{sh} shares")
        # the combined cap binds when Clockwork carries more than one slot (e.g. a failed morning sell + tonight)
        c = sqlite3.connect(db)
        c.execute("DELETE FROM night_owl_trades WHERE entry_date=?", (today,))
        c.execute("INSERT INTO overnight_trades(symbol,entry_date,entry_time,entry_price,shares,status,mode) "
                  "VALUES('AAA','2026-10-02','15:42:00',50,100,'OPEN','LIVE')")                    # $5,000 in AAA
        c.execute("INSERT INTO overnight_trades(symbol,entry_date,entry_time,entry_price,shares,status,mode) "
                  "VALUES('EEE',?,'15:42:00',50,134,'PENDING_ENTRY','LIVE')", (today,))           # $6,700 in EEE
        c.commit(); c.close()
        E.scan_and_enter()
        rows = dict(E._q("SELECT symbol, shares FROM night_owl_trades WHERE entry_date=?", (today,)))
        check("cap: $5,000 Clockwork → Night Owl gets the remaining $1,667", rows.get('AAA') == int((E.MAX_NAME_USD - 5000) / 50.0),
              f"AAA {rows.get('AAA')} shares")
        check("cap: a name already at the cap is skipped, next best taken", 'EEE' not in rows and 'FFF' in rows, f"{sorted(rows)}")
        c = sqlite3.connect(db)
        c.execute("DELETE FROM overnight_trades WHERE symbol IN ('AAA','EEE') AND mode='LIVE' AND shares IN (100,134)")
        c.execute("DELETE FROM night_owl_trades WHERE entry_date=?", (today,))
        c.commit(); c.close()
        E.scan_and_enter()
        E.scan_and_enter()
        n = E._q("SELECT COUNT(*) FROM night_owl_trades WHERE entry_date=?", (today,))[0][0]
        check("second pass does not enter again", n == 3, f"{n} rows")
        # cap: an unconfirmed morning sell blocks new entries
        c = sqlite3.connect(db)
        c.execute("DELETE FROM night_owl_trades")
        c.execute("INSERT INTO night_owl_trades(symbol,entry_date,shares,entry_price,status,mode) VALUES('ZZZ','2026-10-02',10,50,'PENDING_EXIT','SHADOW')")
        for s in ('Y1', 'Y2'):
            c.execute("INSERT INTO night_owl_trades(symbol,entry_date,shares,entry_price,status,mode) VALUES(?,'2026-10-02',10,50,'OPEN','SHADOW')", (s,))
        c.commit(); c.close()
        E.scan_and_enter()
        n = E._q("SELECT COUNT(*) FROM night_owl_trades WHERE entry_date=?", (today,))[0][0]
        check("3 open/pending positions block new entries", n == 0, f"{n} new rows")
        # wait for Clockwork before 15:46 when it has not entered yet
        c = sqlite3.connect(db)
        c.execute("DELETE FROM night_owl_trades"); c.execute("DELETE FROM overnight_trades WHERE entry_date=?", (today,))
        c.commit(); c.close()
        E.now_et = lambda: _dt.datetime(2026, 10, 5, 15, 42, tzinfo=E.ET)
        E.scan_and_enter()
        n = E._q("SELECT COUNT(*) FROM night_owl_trades WHERE entry_date=?", (today,))[0][0]
        check("waits for Clockwork before 15:46", n == 0, f"{n} rows at 15:42 with no Clockwork entry")
        E.now_et = lambda: _dt.datetime(2026, 10, 5, 15, 47, tzinfo=E.ET)
        E.scan_and_enter()
        n = E._q("SELECT COUNT(*) FROM night_owl_trades WHERE entry_date=?", (today,))[0][0]
        check("enters alone after 15:46 if Clockwork never did", n == 3, f"{n} rows at 15:47")
    finally:
        for k, v in saved.items():
            setattr(E, k, v)
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    daily = M.load_daily()
    art = M.load_model()
    print(f"cache {len(daily):,} rows through {daily['date'].max().date()}; model {art['version']}")
    print("── artifact ──"); test_artifact(art, daily)
    print("── lag + universe filter ──"); test_lag_and_filter(daily, art)
    print("── engine rules (scratch DB) ──"); test_engine_rules()
    print("── parity + no look-ahead (slow: full-history panels) ──"); test_parity_and_lookahead(daily, art)
    print(f"\n{'ALL PASS ✅' if not _fails else 'FAILURES: ' + str(_fails)}")
    sys.exit(1 if _fails else 0)


if __name__ == "__main__":
    main()
