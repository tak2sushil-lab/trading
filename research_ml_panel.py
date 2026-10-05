"""Research panel for the learned multi-signal model (Oct 4 2026 programme — docs/RESEARCH_REGISTRY.md §F).

One row per (symbol, trading day). Every FEATURE uses only information available at that day's
CLOSE (the decision point for an overnight or multi-day hold). Every TARGET is prefixed `y_` and
looks forward. Built from our own 5-min bars (bars_5m via collect_bars.load_bars), Jan 2024 onward.

Feature families (≈60 signals) — textbook, practitioner and academic:
  returns & reversal ...... r1 r5 r10 r20 r60 r120, overnight/intraday decomposition + consistency
  volatility & tails ...... realised vol, ATR, downside vol, skew, MAX/MIN (lottery), beta, idio vol
  trend ................... MA distances (ATR units), MA slope, 52w-high proximity, Donchian/Turtle
  oscillators ............. RSI14, RSI2 (Connors), Williams %R, CCI, MACD hist, ADX/DI, Bollinger %B,
                            Bollinger bandwidth + its 120d percentile (squeeze), stochastic
  candles / patterns ...... NR4/NR7, inside/outside day, gap and gap-fill, consecutive up/down days,
                            hammer, engulfing, doji, close location value
  Fibonacci ............... retracement ratio of the 20d and 60d swing + 38.2-61.8% zone flags,
                            prior-day Fibonacci pivot distances (ATR units), position vs pivot P
  intraday shape .......... first hour, last hour, last 30 min, close vs VWAP, intraday realised vol,
                            up-volume share, max run-up / drawdown from the open
  volume .................. relative volume (NaN across the Aug 2026 data-source switch), OBV slope,
                            dollar volume (liquidity)
  context ................. sector-relative 5d return, market EW returns, cross-sectional dispersion,
                            breadth, VIX, day of week, month-end
Targets: y_on (close→next open), y_id (next open→next close), y_r1 y_r3 y_r5 y_r10 (close→close+k).

Known data caveats handled here:
  * bars_5m is NOT split-adjusted → splits detected from the overnight jump and back-adjusted.
  * bars_5m volume before Aug 4 2026 is a ~2-5% backfill sample (memory: bars5m_volume_two_scales)
    → load_bars output switches scale on Jun 1 2026 (20-25x jump, every symbol the same day). Volume
    RATIOS are valid within a source; rvol / OBV slope are NaN for 35 days after the switch while their
    baselines mix sources. Dollar-volume LEVELS are only compared cross-sectionally (same day, same source).
  * Survivorship: the universe is today's 241 names, partly chosen for having moved. Judge every result
    on market-neutral (cross-sectional) returns, never raw.

Usage: venv/bin/python research_ml_panel.py            → research_out/ml_panel.parquet
"""
import os, sys, warnings, time
import numpy as np, pandas as pd

warnings.filterwarnings('ignore')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from collect_bars import load_bars  # noqa: E402

OUT = os.path.join(ROOT, 'research_out', 'ml_panel.parquet')
START = '2024-01-01'
# Oct 5 2026: the feature code lives in factory/features_daily.py (single source of truth for
# research, training and live scoring); re-exported here so existing research scripts keep working.
from factory.features_daily import (VOL_SWITCH, SPLIT_KS, _at_constants, _split_adjust,  # noqa: E402,F401
                                    _rsi, features)






def daily_from_bars(sym):
    b = load_bars(sym, start=START, end=(pd.Timestamp.today() + pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
    if b is None or len(b) < 3000:
        return None
    b = b.between_time('09:30', '15:55').copy()
    b['d'] = b.index.normalize().tz_localize(None)
    b['tp'] = (b.high + b.low + b.close) / 3
    b['tpv'] = b.tp * b.volume
    b['upv'] = np.where(b.close > b.open, b.volume, 0.0)
    b['lr'] = np.log(b.close).diff()
    b.loc[b.d != b.d.shift(), 'lr'] = np.nan          # no overnight in intraday vol
    t = b.index.hour * 60 + b.index.minute
    g = b.groupby('d')
    d = pd.DataFrame({'open': g.open.first(), 'high': g.high.max(), 'low': g.low.min(),
                      'close': g.close.last(), 'volume': g.volume.sum(), 'bars': g.close.size()})
    d = d[d.bars >= 60]
    d['volume'] = d['volume'].astype(float)          # reverse splits make volume fractional
    d['vwap'] = g.tpv.sum() / g.volume.sum().replace(0, np.nan)
    d['upvol_share'] = g.upv.sum() / g.volume.sum().replace(0, np.nan)
    d['ivol_intraday'] = g.lr.std() * np.sqrt(78)
    c1030 = b[t == 625].groupby('d').close.last()      # 10:25 bar close = price at 10:30
    c1455 = b[t == 895].groupby('d').close.last()      # 14:55 bar close = price at 15:00
    c1525 = b[t == 925].groupby('d').close.last()      # 15:25 bar close = price at 15:30
    d['ret_first_hour'] = c1030.reindex(d.index) / d.open - 1
    d['ret_last_hour'] = d.close / c1455.reindex(d.index) - 1
    d['ret_last_30m'] = d.close / c1525.reindex(d.index) - 1
    d['runup_open'] = d.high / d.open - 1
    d['drawdown_open'] = d.low / d.open - 1
    return _split_adjust(d.drop(columns='bars'))






def main():
    k = _at_constants()
    uni = [s for s in k['FULL_UNIVERSE'] if s not in set(k.get('ETF_SYMBOLS', []))]
    sector = k['SECTOR_MAP']; hv = set(k['HIGH_VOL_SYMBOLS']); inst = set(k['INSTITUTIONAL_SYMBOLS'])
    t0 = time.time(); parts = []
    for i, s in enumerate(uni):
        try:
            d = daily_from_bars(s)
        except Exception as e:
            print(f'  {s}: {e}'); continue
        if d is None or len(d) < 260:
            continue
        f = features(d); f['symbol'] = s
        f['sector'] = sector.get(s, 'OTHER')
        f['dna'] = 'HIGH_VOL' if s in hv else 'INSTITUTIONAL' if s in inst else 'MOMENTUM'
        parts.append(f.reset_index().rename(columns={'index': 'date', 'd': 'date'}))
        if i % 40 == 0:
            print(f'  {i}/{len(uni)} {s}  {time.time() - t0:.0f}s', flush=True)
    p = pd.concat(parts, ignore_index=True)
    p['date'] = pd.to_datetime(p['date'])
    # ── cross-sectional / market features (same date across symbols) ──
    g = p.groupby('date')
    p['mkt_r1'] = g.ret_1d_for_beta.transform('mean')
    p['mkt_r5'] = g.r5.transform('mean'); p['mkt_r20'] = g.r20.transform('mean')
    p['xs_dispersion'] = g.ret_1d_for_beta.transform('std')
    p['breadth'] = g.ret_1d_for_beta.transform(lambda x: (x > 0).mean())
    p['sector_rel_r5'] = p.r5 - p.groupby(['date', 'sector']).r5.transform('median')
    p = p.sort_values(['symbol', 'date'])
    cov = (p.ret_1d_for_beta * p.mkt_r1).groupby(p.symbol).transform(lambda x: x.rolling(60).mean()) - \
          p.ret_1d_for_beta.groupby(p.symbol).transform(lambda x: x.rolling(60).mean()) * \
          p.mkt_r1.groupby(p.symbol).transform(lambda x: x.rolling(60).mean())
    var = p.mkt_r1.groupby(p.symbol).transform(lambda x: x.rolling(60).var(ddof=0))
    p['beta60'] = cov / var.replace(0, np.nan)
    resid = p.ret_1d_for_beta - p.beta60 * p.mkt_r1
    p['idio_vol60'] = resid.groupby(p.symbol).transform(lambda x: x.rolling(60).std())
    try:
        import yf_cache_fix  # noqa: F401
        import yfinance as yf
        vix = yf.download('^VIX', start=START, progress=False)['Close']
        vix = vix.iloc[:, 0] if hasattr(vix, 'columns') else vix
        vix.index = pd.to_datetime(vix.index).tz_localize(None)
        p['vix'] = p.date.map(vix)
    except Exception as e:
        print('  VIX unavailable:', e); p['vix'] = np.nan
    p = p.drop(columns=['ret_1d_for_beta'])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    p.to_parquet(OUT, index=False)
    print(f'panel {p.shape}  {p.symbol.nunique()} symbols  {p.date.min().date()} → {p.date.max().date()}  '
          f'{time.time() - t0:.0f}s → {OUT}')


if __name__ == '__main__':
    main()
