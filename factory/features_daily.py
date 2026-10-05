"""Daily feature library — the single source of truth for every Night Owl feature (Oct 5 2026).

Moved here from research_ml_panel.py so research, training and LIVE scoring call the same code and
cannot drift apart (research_ml_panel.py now imports these names from here). Nothing in the bodies
changed in the move; `tests` assert research_ml_panel and this module produce identical output.

features(d) takes one symbol's DAILY frame (index = session date; columns open/high/low/close/volume/
vwap plus the intraday-shape columns, NaN when unavailable) and returns one row per session: every
FEATURE uses only that session's information or earlier; every column starting `y_` looks FORWARD
and is a TARGET, never an input.
"""
import ast, os
import numpy as np, pandas as pd

ROOT = '/Users/sushil/trading'
VOL_SWITCH = pd.Timestamp('2026-06-01')   # bars_5m (load_bars) volume jumps 20-25x here — see research_ml_panel docstring
SPLIT_KS = (2, 3, 4, 5, 8, 10, 15, 20, 25, 30, 40, 50)


def _at_constants():
    """Read FULL_UNIVERSE / SECTOR_MAP / DNA clusters from auto_trader.py without importing it."""
    tree = ast.parse(open(os.path.join(ROOT, 'auto_trader.py')).read())
    want = {'FULL_UNIVERSE', 'SECTOR_MAP', 'HIGH_VOL_SYMBOLS', 'INSTITUTIONAL_SYMBOLS', 'ETF_SYMBOLS'}
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if getattr(t, 'id', None) in want:
                    if t.id == 'SECTOR_MAP':
                        out[t.id] = ast.literal_eval(node.value)
                    else:
                        out[t.id] = sorted({n.value for n in ast.walk(node.value)
                                            if isinstance(n, ast.Constant) and isinstance(n.value, str)})
    return out


def _split_adjust(d):
    """Back-adjust OHLC/volume for splits detected as an integer-ratio overnight jump."""
    pc = d['close'].shift(1)
    ratio = pc / d['open']
    for i in np.where((ratio > 1.6) | (ratio < 0.62))[0]:
        r = float(ratio.iloc[i])
        k = min(SPLIT_KS, key=lambda k: min(abs(r - k) / k, abs(1 / r - k) / k))
        if abs(r - k) / k < 0.04:
            f = 1.0 / k           # forward split k:1 → earlier prices / k
        elif abs(1 / r - k) / k < 0.04:
            f = float(k)          # reverse split 1:k → earlier prices × k
        else:
            continue
        idx = d.index[:i]
        d.loc[idx, ['open', 'high', 'low', 'close', 'vwap']] *= f
        d.loc[idx, 'volume'] /= f
    return d


def _rsi(c, n):
    dl = c.diff()
    up = dl.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-dl.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def features(d):
    o, h, l, c, v = d.open, d.high, d.low, d.close, d.volume
    f = pd.DataFrame(index=d.index)
    r1 = c.pct_change()
    pc = c.shift(1)
    on = o / pc - 1
    idr = c / o - 1
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    # returns & reversal
    for k in (1, 5, 10, 20, 60, 120):
        f[f'r{k}'] = c / c.shift(k) - 1
    f['r20_skip5'] = c.shift(5) / c.shift(20) - 1                     # momentum skipping the last week
    f['on'] = on; f['id'] = idr
    for k in (5, 20, 60):
        f[f'on_avg{k}'] = on.rolling(k).mean()
    for k in (5, 20):
        f[f'id_avg{k}'] = idr.rolling(k).mean()
    f['on_cons30'] = (on > 0).rolling(30).mean()                        # Clockwork's signal
    f['id_cons20'] = (idr > 0).rolling(20).mean()
    # volatility & tails
    f['vol20'] = r1.rolling(20).std(); f['vol60'] = r1.rolling(60).std()
    f['vol_ratio'] = f.vol20 / f.vol60
    f['atr_pct'] = atr / c
    f['downvol20'] = r1.where(r1 < 0).rolling(20, min_periods=5).std()
    f['skew20'] = r1.rolling(20).skew()
    f['max20'] = r1.rolling(20).max(); f['min20'] = r1.rolling(20).min()
    # trend
    for k in (20, 50, 200):
        ma = c.rolling(k).mean()
        f[f'dist_ma{k}'] = (c - ma) / atr
    ma50 = c.rolling(50).mean()
    f['ma50_slope'] = ma50 / ma50.shift(20) - 1
    f['hi52_prox'] = c / h.rolling(252, min_periods=120).max()
    for k in (20, 55):
        hh, ll = h.rolling(k).max(), l.rolling(k).min()
        f[f'donch{k}_pos'] = (c - ll) / (hh - ll).replace(0, np.nan)
        f[f'donch{k}_break'] = (c > h.shift(1).rolling(k).max()).astype(float)
    # oscillators
    f['rsi14'] = _rsi(c, 14); f['rsi2'] = _rsi(c, 2)
    hh14, ll14 = h.rolling(14).max(), l.rolling(14).min()
    f['willr14'] = (hh14 - c) / (hh14 - ll14).replace(0, np.nan)
    tp = (h + l + c) / 3
    f['cci20'] = (tp - tp.rolling(20).mean()) / (0.015 * tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True))
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    f['macd_hist'] = (macd - macd.ewm(span=9, adjust=False).mean()) / c
    up_m, dn_m = h.diff(), -l.diff()
    pdm = up_m.where((up_m > dn_m) & (up_m > 0), 0.0); ndm = dn_m.where((dn_m > up_m) & (dn_m > 0), 0.0)
    atr_w = tr.ewm(alpha=1 / 14, adjust=False).mean()
    pdi = 100 * pdm.ewm(alpha=1 / 14, adjust=False).mean() / atr_w
    ndi = 100 * ndm.ewm(alpha=1 / 14, adjust=False).mean() / atr_w
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    f['adx14'] = dx.ewm(alpha=1 / 14, adjust=False).mean(); f['di_diff'] = pdi - ndi
    m20, s20 = c.rolling(20).mean(), c.rolling(20).std()
    f['boll_pctb'] = (c - (m20 - 2 * s20)) / (4 * s20).replace(0, np.nan)
    bw = 4 * s20 / m20
    f['boll_bw'] = bw
    f['boll_bw_pct120'] = bw.rolling(120, min_periods=60).rank(pct=True)          # squeeze
    f['stoch14'] = (c - ll14) / (hh14 - ll14).replace(0, np.nan)
    # candles / patterns
    rng = h - l
    f['nr7'] = (rng <= rng.rolling(7).min()).astype(float)
    f['nr4'] = (rng <= rng.rolling(4).min()).astype(float)
    f['inside_day'] = ((h < h.shift(1)) & (l > l.shift(1))).astype(float)
    f['outside_day'] = ((h > h.shift(1)) & (l < l.shift(1))).astype(float)
    f['gap'] = on
    f['gap_filled'] = np.where(on > 0, (l <= pc).astype(float), np.where(on < 0, (h >= pc).astype(float), np.nan))
    upd = (r1 > 0).astype(int)
    f['up_streak'] = upd.groupby((upd != upd.shift()).cumsum()).cumsum() * upd
    dnd = (r1 < 0).astype(int)
    f['down_streak'] = dnd.groupby((dnd != dnd.shift()).cumsum()).cumsum() * dnd
    body = (c - o).abs()
    lower_sh = pd.concat([o, c], axis=1).min(axis=1) - l
    f['hammer'] = ((lower_sh > 2 * body) & ((c - l) / rng.replace(0, np.nan) > 0.66)).astype(float)
    f['bull_engulf'] = ((c > o) & (c.shift(1) < o.shift(1)) & (c > o.shift(1)) & (o < c.shift(1))).astype(float)
    f['doji'] = (body < 0.1 * rng).astype(float)
    f['clv'] = (c - l) / rng.replace(0, np.nan)
    # Fibonacci retracement of the recent swing (20d and 60d): where the close sits inside the move
    for k in (20, 60):
        hh, ll = h.rolling(k).max(), l.rolling(k).min()
        hi_age = h.rolling(k).apply(lambda x: len(x) - 1 - np.argmax(x), raw=True)
        lo_age = l.rolling(k).apply(lambda x: len(x) - 1 - np.argmin(x), raw=True)
        upswing = hi_age < lo_age                                  # the high came after the low
        retr = np.where(upswing, (hh - c) / (hh - ll).replace(0, np.nan), (c - ll) / (hh - ll).replace(0, np.nan))
        f[f'fib_retr{k}'] = retr
        f[f'fib_upswing{k}'] = upswing.astype(float)
        f[f'fib_zone{k}_up'] = (upswing & (retr >= 0.382) & (retr <= 0.618)).astype(float)
        f[f'fib_zone{k}_dn'] = ((~upswing) & (retr >= 0.382) & (retr <= 0.618)).astype(float)
        f[f'swing{k}_size_atr'] = (hh - ll) / atr
    # prior-day Fibonacci pivots (the futures H5 construction), distances in ATR units
    P = (h.shift(1) + l.shift(1) + c.shift(1)) / 3; R = h.shift(1) - l.shift(1)
    piv = pd.concat({'P': P, 'R1': 2 * P - l.shift(1), 'S1': 2 * P - h.shift(1), 'FR382': P + .382 * R,
                     'FS382': P - .382 * R, 'FR618': P + .618 * R, 'FS618': P - .618 * R}, axis=1)
    f['piv_pos'] = (c - P) / atr
    above = piv.where(piv.gt(c, axis=0)); below = piv.where(piv.lt(c, axis=0))
    f['piv_runway_atr'] = (above.min(axis=1) - c) / atr
    f['piv_floor_atr'] = (c - below.max(axis=1)) / atr
    # intraday shape (already daily aggregates)
    for col in ('ret_first_hour', 'ret_last_hour', 'ret_last_30m', 'ivol_intraday', 'upvol_share',
                'runup_open', 'drawdown_open'):
        f[col] = d[col]
    f['close_vs_vwap'] = (c - d.vwap) / atr
    # volume
    rv = v / v.shift(1).rolling(20).median()
    sw = (d.index >= VOL_SWITCH) & (d.index < VOL_SWITCH + pd.Timedelta(days=35))
    rv[sw] = np.nan
    f['rvol'] = rv
    obv = (np.sign(c.diff()) * v).fillna(0).cumsum()
    f['obv_slope20'] = np.sign(obv - obv.shift(20))
    f.loc[sw, 'obv_slope20'] = np.nan
    f['log_dollar_vol'] = np.log((c * v).rolling(20).median().replace(0, np.nan))
    f['log_price'] = np.log(c)
    # calendar
    f['dow'] = d.index.dayofweek
    f['month_end'] = (d.index.to_series().dt.day >= 26).astype(float).values
    # targets (FUTURE — never used as features)
    f['y_on'] = o.shift(-1) / c - 1
    f['y_id'] = c.shift(-1) / o.shift(-1) - 1
    for k in (1, 3, 5, 10):
        f[f'y_r{k}'] = c.shift(-k) / c - 1
    f['close'] = c; f['ret_1d_for_beta'] = r1
    return f
