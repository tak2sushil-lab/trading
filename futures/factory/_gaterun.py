"""Pipeline replay of the overnight-range day gate (Aug 24 2026).

The gate is purely DAY-LEVEL, so it is applied the clean way — by removing non-qualifying
sessions from the date list _run_scenario replays — rather than by filtering trades after the
fact. That makes this a true pipeline run, not a frame estimate.

on_rel = overnight(18:00->09:29) range / trailing-100-session median of that range.
Causal: known before the RTH open. argv: <start> <end> <threshold|none>
"""
import sys, sqlite3, datetime as _dt
sys.path.insert(0, '/Users/sushil/trading')
import pandas as pd, numpy as np
import futures.sim_replay as srp

START, END = sys.argv[1], sys.argv[2]
THR = None if sys.argv[3].lower() == 'none' else float(sys.argv[3])

srp.GRADUATED_RVOL = True; srp.RVOL_GRAD_FLOOR = 0.70
srp.REGIME_AWARE_EXITS = True; srp.BASE_STOP_PTS = 200.0
srp.NO_OVN_SKIP = True; srp.MAX_DAILY_TRADES = 5; srp.HERO_GATE_ENABLED = True
srp.MAX_DAILY_LOSS = 1250.0
srp.REV_EXIT = (2, 0.30, 120.0); srp.PARTIAL_TAKE_PTS = 150.0
srp.ATR_EXIT_SCALE = None                      # ATR exits stay OFF (pipeline said +$633 = noise)

def on_rel_series():
    con = sqlite3.connect('/Users/sushil/trading/market_data.db')
    b = pd.read_sql_query("SELECT ts_utc,high,low FROM futures_bars_5m WHERE symbol='MNQ' "
                          "ORDER BY ts_utc", con)
    b['ts'] = pd.to_datetime(b.ts_utc, format='mixed', utc=True).dt.tz_convert('America/New_York')
    b = b.set_index('ts'); b['d'] = b.index.date
    rng = {}
    for d, g in b.groupby('d'):
        on = g.between_time('18:00', '09:29')
        if len(on) > 3:
            rng[d] = float(on.high.max() - on.low.min())
    s = pd.Series(rng).sort_index()
    return s / s.shift(1).rolling(100, min_periods=30).median()

rel = on_rel_series()
ab = srp.load_bars('MNQ', start=(pd.Timestamp(START) - pd.Timedelta(days=90)).strftime('%Y-%m-%d'), end=END)
rth = srp.filter_ny_session(ab)
dates = sorted(d for d in set(rth.index.date)
               if pd.Timestamp(START).date() <= d <= pd.Timestamp(END).date())
if THR is not None:
    keep = [d for d in dates if pd.notna(rel.get(d, np.nan)) and rel.get(d) >= THR]
    print(f"gate on_rel >= {THR}: {len(keep)} of {len(dates)} sessions kept "
          f"({len(keep)/max(1,len(dates))*100:.0f}%)", flush=True)
    dates = keep
else:
    print(f"NO GATE: replaying all {len(dates)} sessions", flush=True)

r = srp._run_scenario(ab, dates, False, False, 'NY', verbose=False)
df = r['df'].copy()
if df.empty:
    print("  no trades"); sys.exit(0)
df['net'] = df.pnl - 6.0 * df.contracts.clip(lower=1)
dd = df.groupby('date').net.sum().sort_index(); eq = dd.cumsum()
y = pd.to_datetime(df['date'].astype(str)).dt.year
print(f"  n={len(df)} net=${df.net.sum():+,.0f} maxDD=${(eq-eq.cummax()).min():+,.0f} "
      f"worstDay=${dd.min():+,.0f} DLLdays={int((dd<=-1000).sum())} tradingDays={len(dd)}", flush=True)
print("  per year: " + " ".join(f"{a}:{df.loc[y==a,'net'].sum():+,.0f}" for a in sorted(y.unique())), flush=True)
print(f"  exits: {df.exit_reason.value_counts().to_dict()}", flush=True)
