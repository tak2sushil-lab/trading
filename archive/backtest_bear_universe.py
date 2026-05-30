#!/usr/bin/env python3
"""
backtest_bear_universe.py
Three analyses:
  1. Flip-exit scan change: if flip required 3 scans (not 2), would we have fared better?
     Uses 5-min intraday — look at what happened AFTER each flip-cover exit.
  2. Short symbol investigation: why RIVN/EOSE/VST/CLS/CCJ keep losing.
     Check: volatility, trend alignment, sector behaviour on loss days.
  3. Universe expansion: do AMZN/GOOGL/META/MSFT/AAPL + other high-caps
     generate enough 3%+ setups? How do they compare to our existing universe?
"""

import sqlite3, yfinance as yf, pandas as pd, numpy as np
from datetime import datetime, date, timedelta
import warnings; warnings.filterwarnings('ignore')

DB = 'trades.db'
TZ = 'America/New_York'

# ── Data helpers ───────────────────────────────────────────────────────────────

def load_trades():
    conn = sqlite3.connect(DB)
    df = pd.read_sql("""
        SELECT id, symbol, side, entry_date, entry_time, exit_time,
               CAST(entry_price AS FLOAT) as entry_px,
               CAST(exit_price AS FLOAT) as exit_px,
               CAST(shares AS INT) as qty,
               CAST(pnl AS FLOAT) as pnl,
               max_gain_pct, exit_reason
        FROM trades WHERE exit_time IS NOT NULL
        ORDER BY entry_date, entry_time
    """, conn)
    conn.close()
    df['flip_victim'] = df['exit_reason'].fillna('').str.lower().str.contains('regime|flip|cover')
    return df

def pull_intraday(symbol, trade_date):
    try:
        t   = yf.Ticker(symbol)
        end = trade_date + timedelta(days=1)
        df  = t.history(start=trade_date, end=end, interval='5m', auto_adjust=True)
        if df.empty: return None
        df.index = (df.index.tz_convert(TZ)
                    if df.index.tzinfo else df.index.tz_localize(TZ))
        return df.between_time('09:30', '15:55')
    except:
        return None

def parse_dt(date_str, time_str):
    return pd.Timestamp(f"{date_str} {time_str[:8]}").tz_localize(TZ)

def get_price_at(bars, dt):
    if bars is None or bars.empty: return None
    m = bars[bars.index <= dt]
    return float(m.iloc[-1]['Close']) if not m.empty else float(bars.iloc[0]['Close'])

def sep(title=''):
    print(f"\n{'='*70}")
    if title: print(f"  {title}\n{'='*70}")

# ── ANALYSIS 1: Flip-exit scan change (2→3) ───────────────────────────────────

def analysis_flip_exit(trades, cache):
    sep("ANALYSIS 1: FLIP EXIT 2 SCANS → 3 SCANS")
    print("  For each flip-covered short, check price 5 min after the cover.")
    print("  If price is HIGHER after 5 min → regime kept going up → cover was correct.")
    print("  If price is LOWER  after 5 min → stock resumed down  → cover was premature.\n")

    flips = trades[(trades.side == 'SHORT') & (trades.flip_victim)].copy()
    print(f"  Total flip-covered shorts: {len(flips)}")
    print(f"  Total P&L of flips: ${flips['pnl'].sum():.2f}\n")

    print(f"  {'date':<12} {'sym':<6} {'actual $':>9} {'cover_px':>9} "
          f"{'px+5m':>8} {'px+10m':>8} {'direction':>12}  verdict")
    print(f"  {'-'*80}")

    correct    = 0   # cover was right (stock kept going up after)
    premature  = 0   # cover was wrong (stock resumed down)
    neutral    = 0   # barely moved

    pnl_if_3scan  = 0.0  # what P&L would have been with 3-scan delay
    pnl_if_2scan  = flips['pnl'].sum()

    for _, tr in flips.iterrows():
        key = f"{tr['entry_date']}_{tr['symbol']}"
        bars = cache.get(key)

        entry_px  = float(tr['entry_px'])
        cover_px  = float(tr['exit_px'])
        actual_pnl = float(tr['pnl'])
        qty        = int(tr['qty']) if tr['qty'] else 0

        if bars is None or bars.empty or qty == 0:
            pnl_if_3scan += actual_pnl
            print(f"  {tr['entry_date']:<12} {tr['symbol']:<6} {actual_pnl:>+9.2f}  [no intraday data]")
            continue

        try:
            exit_t = str(tr['exit_time'])[:8]
            exit_hour = int(exit_t[:2])
            if exit_hour < 9:
                nd = (datetime.strptime(tr['entry_date'],'%Y-%m-%d')+timedelta(days=1)).strftime('%Y-%m-%d')
                exit_dt = parse_dt(nd, exit_t)
            else:
                exit_dt = parse_dt(tr['entry_date'], exit_t)
        except:
            pnl_if_3scan += actual_pnl
            continue

        # Price at exit, +5min, +10min
        px_at_exit = get_price_at(bars, exit_dt) or cover_px
        px_plus5   = get_price_at(bars, exit_dt + pd.Timedelta(minutes=5))
        px_plus10  = get_price_at(bars, exit_dt + pd.Timedelta(minutes=10))

        if px_plus5 is None:
            pnl_if_3scan += actual_pnl
            print(f"  {tr['entry_date']:<12} {tr['symbol']:<6} {actual_pnl:>+9.2f}  [no +5m data]")
            continue

        move_5m  = (px_plus5 - px_at_exit) / px_at_exit * 100
        move_10m = (px_plus10 - px_at_exit) / px_at_exit * 100 if px_plus10 else move_5m

        # For a short: stock going UP after cover = cover was correct (thesis invalidated)
        #              stock going DOWN after cover = cover was premature (should have held)
        if move_5m > 0.15:    # stock kept going up > 0.15%
            verdict  = 'CORRECT ✅'
            correct += 1
            new_pnl  = actual_pnl   # would have been same or worse if waited
            pnl_if_3scan += actual_pnl
        elif move_5m < -0.15:  # stock dropped > 0.15% (resumed down)
            verdict   = 'PREMATURE ❌'
            premature += 1
            # With 3 scans: we'd exit 5 min later at px_plus5
            new_pnl   = round((entry_px - px_plus5) * qty, 2)
            pnl_if_3scan += new_pnl
        else:
            verdict  = 'NEUTRAL  —'
            neutral += 1
            new_pnl  = actual_pnl
            pnl_if_3scan += actual_pnl

        p5_str  = f"${px_plus5:.2f}" if px_plus5 else "—"
        p10_str = f"${px_plus10:.2f}" if px_plus10 else "—"
        print(f"  {tr['entry_date']:<12} {tr['symbol']:<6} {actual_pnl:>+9.2f}  "
              f"${cover_px:>8.2f} {p5_str:>8} {p10_str:>8} "
              f"{move_5m:>+10.2f}%  {verdict}  (3-scan: {new_pnl:+.2f})")

    print(f"\n  {'─'*50}")
    total = correct + premature + neutral
    print(f"  Correct covers (stock kept rising):   {correct:>3}/{total} ({correct/max(total,1)*100:.0f}%)")
    print(f"  Premature covers (stock resumed down):{premature:>3}/{total} ({premature/max(total,1)*100:.0f}%)")
    print(f"  Neutral (< 0.15% move):               {neutral:>3}/{total}")
    print(f"\n  P&L with 2-scan flip rule: ${pnl_if_2scan:.2f}")
    print(f"  P&L with 3-scan flip rule: ${pnl_if_3scan:.2f}")
    print(f"  Improvement from 3-scan:   ${pnl_if_3scan - pnl_if_2scan:+.2f}")
    ann = (pnl_if_3scan - pnl_if_2scan) / len(trades['entry_date'].unique()) * 252
    print(f"  Annualised estimate:       ${ann:+,.0f}/yr")

# ── ANALYSIS 2: Short symbol investigation ────────────────────────────────────

def analysis_short_symbols(trades, cache):
    sep("ANALYSIS 2: SHORT SYMBOL INVESTIGATION")
    print("  Why do RIVN, EOSE, VST, CLS, CCJ keep losing on shorts?\n")

    targets = ['RIVN','EOSE','VST','CLS','CCJ','QBTS','HIMS','OKLO']
    shorts  = trades[trades.side == 'SHORT']

    # Pull 2-year daily data for each
    end_d   = date(2026, 5, 22)
    start_d = end_d - timedelta(days=730)

    print(f"  {'symbol':<6} {'beta':>6} {'avg_daily_%':>12} {'days>3%':>8} "
          f"{'days<-3%':>9} {'trend':>8} {'short WR':>9} {'verdict'}")
    print(f"  {'─'*80}")

    for sym in targets:
        try:
            t  = yf.Ticker(sym)
            dh = t.history(start=start_d, end=end_d, interval='1d', auto_adjust=True)
            if len(dh) < 50:
                print(f"  {sym:<6}  [insufficient data]")
                continue

            dh.index = dh.index.tz_localize(None) if dh.index.tzinfo else dh.index
            dh['ret'] = dh['Close'].pct_change() * 100
            dh['MA50'] = dh['Close'].rolling(50).mean()

            avg_ret   = dh['ret'].mean()
            days_up3  = (dh['ret'] > 3).sum()
            days_dn3  = (dh['ret'] < -3).sum()
            last_trend = 'UPTREND' if dh['Close'].iloc[-1] > dh['MA50'].iloc[-1] else 'DOWNTREND'

            # 2-year cumulative return
            cum_ret = (dh['Close'].iloc[-1] / dh['Close'].iloc[0] - 1) * 100

            # Short WR from our trades
            sym_shorts = shorts[shorts.symbol == sym]
            wr = sym_shorts['pnl'].apply(lambda x: x > 0).mean() * 100 if len(sym_shorts) else float('nan')
            wr_str = f"{wr:.0f}%" if not np.isnan(wr) else "n/a"

            # Beta (vs SPY - approximate)
            spy = yf.Ticker('SPY').history(start=start_d, end=end_d, interval='1d',
                                            auto_adjust=True)
            spy.index = spy.index.tz_localize(None) if spy.index.tzinfo else spy.index
            spy_ret = spy['Close'].pct_change().dropna()
            sym_ret_aligned = dh['ret'].reindex(spy_ret.index).dropna() / 100
            spy_ret_aligned = spy_ret.reindex(sym_ret_aligned.index)
            if len(sym_ret_aligned) > 50:
                cov   = np.cov(sym_ret_aligned, spy_ret_aligned)[0][1]
                var_s = np.var(spy_ret_aligned)
                beta  = round(cov / var_s, 2) if var_s > 0 else float('nan')
            else:
                beta = float('nan')

            # Is this a short-squeeze risk stock?
            # High short interest proxy: high beta + uptrend = bad to short
            # Low beta + downtrend = good to short
            if last_trend == 'UPTREND' and (beta > 1.5 if not np.isnan(beta) else False):
                verdict = '⚠️ UPTREND+HIGH-BETA — avoid short'
            elif last_trend == 'UPTREND':
                verdict = '⚠️ UPTREND — short risky'
            elif days_dn3 < days_up3:
                verdict = '⚠️ more up-days than down-days'
            else:
                verdict = '✅ OK to short'

            beta_str = f"{beta:.2f}" if not np.isnan(beta) else "n/a"
            print(f"  {sym:<6} {beta_str:>6} {avg_ret:>+11.2f}% {days_up3:>8} "
                  f"{days_dn3:>9} {last_trend:>8} {wr_str:>9}  {verdict}")

            # Why did our specific short trades lose?
            sym_losing = sym_shorts[sym_shorts['pnl'] < 0]
            if len(sym_losing) > 0:
                print(f"         ↳ {len(sym_losing)} losing trades:")
                for _, tr in sym_losing.iterrows():
                    key  = f"{tr['entry_date']}_{sym}"
                    bars = cache.get(key)
                    reason = str(tr['exit_reason'])[:50]
                    if bars is not None and not bars.empty:
                        try:
                            entry_dt = parse_dt(tr['entry_date'], str(tr['entry_time']))
                            exit_t   = str(tr['exit_time'])[:8]
                            exit_hour = int(exit_t[:2])
                            if exit_hour < 9:
                                nd = (datetime.strptime(tr['entry_date'],'%Y-%m-%d')+timedelta(days=1)).strftime('%Y-%m-%d')
                                exit_dt = parse_dt(nd, exit_t)
                            else:
                                exit_dt = parse_dt(tr['entry_date'], exit_t)

                            # Was stock already in uptrend at entry?
                            entry_bar = bars[bars.index <= entry_dt]
                            exit_bar  = bars[bars.index <= exit_dt]
                            if not entry_bar.empty and not exit_bar.empty:
                                d_open  = float(bars.iloc[0]['Open'])
                                d_entry = float(entry_bar.iloc[-1]['Close'])
                                d_exit  = float(exit_bar.iloc[-1]['Close'])
                                pct_from_open = (d_entry - d_open) / d_open * 100
                                trend_at_entry = 'UP from open' if pct_from_open > 0 else 'DOWN from open'
                                print(f"           {tr['entry_date']} pnl=${tr['pnl']:+.2f}  "
                                      f"at_entry: {pct_from_open:+.1f}% from open ({trend_at_entry})  "
                                      f"reason: {reason}")
                        except Exception as e:
                            print(f"           {tr['entry_date']} pnl=${tr['pnl']:+.2f}  reason: {reason}")
                    else:
                        print(f"           {tr['entry_date']} pnl=${tr['pnl']:+.2f}  reason: {reason}")

        except Exception as e:
            print(f"  {sym:<6}  [error: {e}]")

# ── ANALYSIS 3: Universe expansion ────────────────────────────────────────────

def analysis_universe(trades):
    sep("ANALYSIS 3: UNIVERSE EXPANSION CHECK")
    print("  How often do mega-caps and other candidates generate 3%+ setups?")
    print("  Comparing signal frequency to our best-performing current symbols.\n")

    # Candidates to evaluate
    mega_caps = ['AMZN','GOOGL','META','MSFT','AAPL','NFLX','TSLA']
    our_best  = ['NVDA','AMD','QBTS','IONQ','RKLB','MRVL','SMCI','ARM',
                 'AXON','CCJ','UUUU','CLS']
    check_all = mega_caps + ['PLTR','HOOD','COIN','CRWD','SNOW','MDB']

    end_d   = date(2026, 5, 22)
    start_d = end_d - timedelta(days=365)  # 1 year

    results = []
    print(f"  Pulling 1-year daily data for {len(check_all) + len(our_best)} symbols...")

    for sym in check_all + our_best:
        try:
            t  = yf.Ticker(sym)
            dh = t.history(start=start_d, end=end_d, interval='1d', auto_adjust=True)
            if len(dh) < 100: continue
            dh.index = dh.index.tz_localize(None) if dh.index.tzinfo else dh.index
            dh['MA20']    = dh['Close'].rolling(20).mean()
            dh['gap_pct'] = (dh['Open'] - dh['Close'].shift(1)) / dh['Close'].shift(1) * 100
            dh['today_gain'] = (dh['Close'] - dh['Open']) / dh['Open'] * 100
            dh['intraday'] = dh['today_gain']   # proxy for MIN_TODAY_GAIN

            # Entry signal proxy: above MA20 + today up 3%+
            valid = dh.dropna(subset=['MA20'])
            setups = valid[(valid['intraday'] >= 3.0) & (valid['Open'] > valid['MA20'])]

            # Win rate: did the entry day close above open? (very rough)
            # Better: did it continue up from open to high by ≥1%?
            setup_wins = setups[setups['High'] >= setups['Open'] * 1.01]

            # Avg position size given our $1,600 alloc
            avg_price  = dh['Close'].mean()
            shares_est = int(1600 / avg_price) if avg_price > 0 else 0

            # Price tier
            if avg_price < 20:    tier = 'penny'
            elif avg_price < 100: tier = 'small'
            elif avg_price < 300: tier = 'mid'
            elif avg_price < 600: tier = 'large'
            else:                 tier = 'mega'

            in_our_universe = sym in our_best

            results.append({
                'symbol':   sym,
                'setups_yr': len(setups),
                'setup_wr':  len(setup_wins) / max(len(setups), 1) * 100,
                'avg_price': round(avg_price, 0),
                'shares':    shares_est,
                'tier':      tier,
                'in_ours':   in_our_universe,
                'mega_cap':  sym in mega_caps,
            })
        except Exception as e:
            pass

    df = pd.DataFrame(results).sort_values('setups_yr', ascending=False)

    print(f"\n  {'symbol':<7} {'setups/yr':>10} {'setup WR':>9} {'avg $':>7} "
          f"{'shares':>7} {'tier':>6} {'in ours':>8}")
    print(f"  {'─'*65}")
    for _, r in df.iterrows():
        marker = '  ✅ OURS'  if r['in_ours'] else \
                 ('  ← CANDIDATE' if r['setups_yr'] >= 15 and r['tier'] in ('mid','large','mega') else '')
        print(f"  {r['symbol']:<7} {r['setups_yr']:>10} {r['setup_wr']:>8.0f}% "
              f"${r['avg_price']:>6.0f} {r['shares']:>7} {r['tier']:>6}  "
              f"{'YES' if r['in_ours'] else 'NO':>6}{marker}")

    # Summary
    candidates = df[(~df.in_ours) & (df.setups_yr >= 15) &
                    (df.tier.isin(['mid','large','mega'])) & (df.setup_wr >= 55)]
    print(f"\n  New candidates (≥15 setups/yr, ≥55% setup WR, mid/large cap, not in our universe):")
    if len(candidates) > 0:
        for _, r in candidates.iterrows():
            print(f"  → {r['symbol']}: {r['setups_yr']} setups/yr, {r['setup_wr']:.0f}% WR, "
                  f"avg ${r['avg_price']:.0f} ({r['shares']} shares @ $1600)")
    else:
        print("  None found meeting all criteria — current universe is competitive.")

    # Why aren't mega-caps in our universe?
    print(f"\n  Mega-cap analysis (AMZN/GOOGL/META/MSFT/AAPL/NFLX/TSLA):")
    mega_df = df[df.mega_cap]
    for _, r in mega_df.iterrows():
        note = ''
        if r['setups_yr'] < 10:
            note = '← too few setups (stable price, low volatility)'
        elif r['shares'] < 3:
            note = '← too few shares (too expensive for $1600 lot)'
        elif r['setup_wr'] < 50:
            note = '← setup WR below 50% on 3% days'
        print(f"  {r['symbol']:<6}: {r['setups_yr']:>3} setups/yr  {r['setup_wr']:>4.0f}% WR  "
              f"${r['avg_price']:>6.0f}/share  {r['shares']} shares  {note}")

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("="*70)
    print("BEAR EDGE + UNIVERSE EXPANSION ANALYSIS")
    print("="*70)

    trades = load_trades()

    # Build cache only for shorts (flip analysis needs their intraday data)
    print("\nPulling intraday data for short trades...")
    cache = {}
    short_pairs = trades[trades.side=='SHORT'][['entry_date','symbol']].drop_duplicates()
    for _, r in short_pairs.iterrows():
        key = f"{r['entry_date']}_{r['symbol']}"
        if key not in cache:
            d = datetime.strptime(r['entry_date'], '%Y-%m-%d').date()
            cache[key] = pull_intraday(r['symbol'], d)
    ok = sum(1 for v in cache.values() if v is not None)
    print(f"  {ok}/{len(cache)} short symbol-days available\n")

    analysis_flip_exit(trades, cache)
    analysis_short_symbols(trades, cache)
    analysis_universe(trades)

    print("\n✓ Analysis complete")

if __name__ == '__main__':
    main()
