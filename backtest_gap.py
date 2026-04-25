# backtest_gap.py — Gap-and-go catalyst strategy, 5-year backtest
# Tests: stocks gapping 4%+ at open, how often they continue vs fade
# Also: regime distribution — how many tradeable days per year
# Command: venv/bin/python backtest_gap.py

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date

START_DATE   = '2021-01-01'
END_DATE     = date.today().isoformat()
GAP_PCT      = 4.0    # minimum gap up at open to qualify
STOP_PCT     = 3.0    # stop if stock drops this % from open
CAPITAL      = 1500   # capital per trade (new dynamic sizing)

# Broad universe — fixed watchlist + stocks we want to discover dynamically
UNIVERSE = list(dict.fromkeys([
    # Current watchlist
    'AAPL','PLTR','COHR','IONQ','HOOD','IREN','NUTX','LITE','VST','ORCL',
    'OKLO','AMZN','GOOGL','CRM','QBTS','TOST','AMD','RKT','AVGO','CLS',
    'RKLB','MSFT','META','NFLX','JPM','APLD','SOUN','BBAI','RBRK','AI',
    'UUUU','CCJ','DNN','NTLA','BEAM','LLY','GS',
    # Stocks we missed — should find dynamically
    'ON','NVDA','MU','MRVL','AMAT','LRCX','TSM',   # semis
    'SMR','NNE',                                     # nuclear
    'RGTI','QUBT',                                   # quantum
    'COIN','MSTR','RIOT','MARA',                     # crypto
    'CRWD','PANW','ZS','FTNT',                       # cyber
    'SNOW','DDOG','MDB','NET',                       # cloud
    'ROKU','SPOT','UBER','LYFT',                     # consumer tech
]))

def get_regime(spy_chg_pct):
    if spy_chg_pct >= 0.5:   return 'STRONG'
    if spy_chg_pct <= -0.5:  return 'WEAK'
    return 'NORMAL'

def run_gap_backtest():
    print("=" * 65)
    print("  GAP-AND-GO CATALYST BACKTEST — 5 YEARS")
    print(f"  Period : {START_DATE} → {END_DATE}")
    print(f"  Capital : ${CAPITAL}/trade | Stop: {STOP_PCT}% from open")
    print(f"  Universe: {len(UNIVERSE)} stocks")
    print("=" * 65)

    # ── Regime distribution ───────────────────────────────
    spy = yf.download('SPY', start=START_DATE, end=END_DATE,
                      progress=False, auto_adjust=True)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)
    spy['chg'] = spy['Close'].pct_change() * 100

    total   = len(spy['chg'].dropna())
    strong  = (spy['chg'] >= 0.5).sum()
    weak    = (spy['chg'] <= -0.5).sum()
    normal  = total - strong - weak

    print(f"\nREGIME DISTRIBUTION ({total} trading days, ~{total//252} years)")
    print(f"  STRONG (SPY ≥+0.5%) : {strong:>4}d  {strong/total*100:.0f}%")
    print(f"  NORMAL (SPY -0.5 to +0.5%): {normal:>4}d  {normal/total*100:.0f}%")
    print(f"  WEAK   (SPY ≤-0.5%) : {weak:>4}d  {weak/total*100:.0f}%")
    print(f"  Tradeable (NORMAL+STRONG): {strong+normal}d  {(strong+normal)/total*100:.0f}%")
    print(f"  Expected per year: ~{(strong+normal)//5} tradeable days")

    # ── Gap-and-go scan ───────────────────────────────────
    print(f"\nScanning {len(UNIVERSE)} stocks for 4%+ gap events...\n")
    events = []

    for sym in UNIVERSE:
        try:
            df = yf.download(sym, start=START_DATE, end=END_DATE,
                             progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if len(df) < 30:
                continue

            avg_vol = df['Volume'].rolling(21).mean().shift(1)

            for i in range(1, len(df)):
                prev_close  = float(df['Close'].iloc[i-1])
                today_open  = float(df['Open'].iloc[i])
                today_close = float(df['Close'].iloc[i])
                today_low   = float(df['Low'].iloc[i])
                today_high  = float(df['High'].iloc[i])
                today_vol   = float(df['Volume'].iloc[i])
                avg_v       = float(avg_vol.iloc[i]) if pd.notna(avg_vol.iloc[i]) else 0

                if prev_close <= 0 or today_open <= 5:
                    continue

                gap_pct = (today_open - prev_close) / prev_close * 100
                if gap_pct < GAP_PCT:
                    continue

                vol_ratio   = today_vol / avg_v if avg_v > 0 else 1
                from_open   = (today_close - today_open) / today_open * 100
                high_from_open = (today_high - today_open) / today_open * 100
                stop_price  = today_open * (1 - STOP_PCT / 100)

                # Get SPY regime that day
                dt = df.index[i]
                spy_idx = spy.index.get_indexer([dt], method='nearest')[0]
                spy_chg_day = float(spy['chg'].iloc[spy_idx]) if spy_idx > 0 else 0
                regime = get_regime(spy_chg_day)

                # Simulate: enter at open, stop at open-STOP_PCT
                if today_low <= stop_price:
                    result  = 'STOP'
                    pnl_pct = -STOP_PCT
                elif today_close >= today_open:
                    result  = 'WIN'
                    pnl_pct = from_open
                else:
                    result  = 'FADE'
                    pnl_pct = from_open

                events.append({
                    'sym':       sym,
                    'date':      dt,
                    'gap_pct':   round(gap_pct, 1),
                    'from_open': round(from_open, 1),
                    'high_open': round(high_from_open, 1),
                    'vol_ratio': round(vol_ratio, 1),
                    'result':    result,
                    'pnl_pct':   round(pnl_pct, 2),
                    'pnl':       round(CAPITAL * pnl_pct / 100, 2),
                    'regime':    regime,
                    'spy_chg':   round(spy_chg_day, 2),
                    'price':     round(today_open, 2),
                })
        except Exception:
            pass

    df_e = pd.DataFrame(events)
    if df_e.empty:
        print("No gap events found.")
        return

    n = len(df_e)
    wins  = df_e[df_e['result'] == 'WIN']
    fades = df_e[df_e['result'] == 'FADE']
    stops = df_e[df_e['result'] == 'STOP']

    # ── Overall stats ─────────────────────────────────────
    print(f"FOUND {n} gap events across {df_e['sym'].nunique()} stocks\n")
    print(f"OVERALL (enter at open, stop {STOP_PCT}% below open)")
    print(f"  Win  (closed above open): {len(wins):>4}  ({len(wins)/n*100:.0f}%)")
    print(f"  Fade (closed below open): {len(fades):>4}  ({len(fades)/n*100:.0f}%)")
    print(f"  Stop hit               : {len(stops):>4}  ({len(stops)/n*100:.0f}%)")
    print(f"  Avg gain  on wins      : {wins['from_open'].mean():+.1f}%")
    print(f"  Avg loss  on fades     : {fades['from_open'].mean():+.1f}%")
    print(f"  Avg P&L per trade      : ${df_e['pnl'].mean():+.2f}")
    print(f"  Total P&L (all)        : ${df_e['pnl'].sum():+,.0f}")
    print(f"  Avg events/year        : {n // 5}/year → {n // 5 // 252:.1f}/day")

    # ── By regime ─────────────────────────────────────────
    print(f"\nBY MARKET REGIME:")
    print(f"  {'Regime':<10} {'Count':>6} {'WR':>6} {'AvgPnL':>9} {'TotalPnL':>11}")
    print(f"  {'-'*46}")
    for regime in ['STRONG', 'NORMAL', 'WEAK']:
        sub = df_e[df_e['regime'] == regime]
        if len(sub) == 0: continue
        wr = len(sub[sub['result'] == 'WIN']) / len(sub) * 100
        print(f"  {regime:<10} {len(sub):>6} {wr:>5.0f}% {sub['pnl'].mean():>+9.2f} {sub['pnl'].sum():>+11,.0f}")

    # ── By gap size ───────────────────────────────────────
    print(f"\nBY GAP SIZE:")
    print(f"  {'Gap Range':<14} {'Count':>6} {'WR':>6} {'AvgGain':>9} {'AvgPnL':>9}")
    print(f"  {'-'*46}")
    for lo, hi in [(4,6),(6,10),(10,15),(15,100)]:
        sub = df_e[(df_e['gap_pct'] >= lo) & (df_e['gap_pct'] < hi)]
        if len(sub) == 0: continue
        wr = len(sub[sub['result']=='WIN']) / len(sub) * 100
        print(f"  {lo}-{hi}%{' '*8} {len(sub):>6} {wr:>5.0f}% {sub['from_open'].mean():>+9.1f}% {sub['pnl'].mean():>+9.2f}")

    # ── Volume filter impact ──────────────────────────────
    print(f"\nVOLUME FILTER IMPACT (gap events with 2x+ volume):")
    high_vol = df_e[df_e['vol_ratio'] >= 2.0]
    low_vol  = df_e[df_e['vol_ratio'] <  2.0]
    if len(high_vol):
        wr_hv = len(high_vol[high_vol['result']=='WIN']) / len(high_vol) * 100
        wr_lv = len(low_vol[low_vol['result']=='WIN']) / max(1,len(low_vol)) * 100
        print(f"  2x+ volume ({len(high_vol)} events): WR {wr_hv:.0f}% | avg ${high_vol['pnl'].mean():+.2f}")
        print(f"  <2x  volume ({len(low_vol)} events): WR {wr_lv:.0f}% | avg ${low_vol['pnl'].mean():+.2f}")

    # ── Top stocks ────────────────────────────────────────
    print(f"\nTOP STOCKS FOR GAP-AND-GO (min 3 events):")
    print(f"  {'Symbol':<8} {'Events':>7} {'WR':>6} {'AvgPnL':>9} {'TotalPnL':>11}")
    print(f"  {'-'*44}")
    by_sym = (df_e.groupby('sym')
              .agg(count=('pnl','count'),
                   wins=('result', lambda x:(x=='WIN').sum()),
                   avg_pnl=('pnl','mean'),
                   total=('pnl','sum'))
              .reset_index())
    by_sym['wr'] = by_sym['wins'] / by_sym['count'] * 100
    by_sym = by_sym[by_sym['count'] >= 3].sort_values('wr', ascending=False)
    for _, r in by_sym.head(15).iterrows():
        tag = ' ← not in watchlist' if r['sym'] not in [
            'AAPL','PLTR','COHR','IONQ','HOOD','IREN','NUTX','LITE','VST',
            'ORCL','OKLO','AMZN','GOOGL','CRM','QBTS','TOST','AMD','RKT'] else ''
        print(f"  {r['sym']:<8} {r['count']:>7} {r['wr']:>5.0f}% {r['avg_pnl']:>+9.2f} {r['total']:>+11,.0f}{tag}")

    # ── Key finding: WEAK day gap plays ───────────────────
    weak_e  = df_e[df_e['regime'] == 'WEAK']
    ns_e    = df_e[df_e['regime'].isin(['NORMAL','STRONG'])]
    weak_wr = len(weak_e[weak_e['result']=='WIN']) / max(1,len(weak_e)) * 100
    ns_wr   = len(ns_e[ns_e['result']=='WIN']) / max(1,len(ns_e)) * 100

    print(f"\n{'='*65}")
    print(f"  KEY FINDINGS")
    print(f"{'='*65}")
    print(f"  Gap-and-go on NORMAL/STRONG days : {ns_wr:.0f}% win rate | avg ${ns_e['pnl'].mean():+.2f}")
    print(f"  Gap-and-go on WEAK days          : {weak_wr:.0f}% win rate | avg ${weak_e['pnl'].mean():+.2f}")
    print(f"  Weak-day gaps as % of all gaps   : {len(weak_e)/n*100:.0f}%")

    if weak_wr >= 50:
        print(f"\n  ✅ WEAK-day gap plays still profitable ({weak_wr:.0f}% WR)")
        print(f"     → Catalyst override (allow entries on WEAK days) is VALIDATED")
    else:
        print(f"\n  ⚠️  WEAK-day gap plays underperform ({weak_wr:.0f}% vs {ns_wr:.0f}%)")
        print(f"     → Regime gate should stay — only override for 6%+ gaps with 3x+ volume")

    exp = df_e['pnl'].mean()
    if exp > 0:
        days_per_year = n // 5
        print(f"\n  📈 At ${CAPITAL}/trade × {days_per_year} gap events/year × ${exp:.2f} avg = ${days_per_year*exp:,.0f}/year potential")
    print(f"{'='*65}\n")

if __name__ == '__main__':
    run_gap_backtest()
