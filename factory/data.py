"""
Data layer — builds and caches the factory's raw materials from our 2.5yr of 5-min bars.

Three products, all cached to factory/cache/ (regenerable, git-ignored):
  personality.csv : each stock's volatility class  (CALM / MID / WILD)   -> "the boat's temperament"
  tide.csv        : the market's h-day drift per day (h=1..15)            -> "the beta lift"
  events.csv      : every >=3% morning mover with 15-day forward paths     -> "the fishing spots"

An event is entered at 10:00 ET; forward columns lo{k}/cl{k} are % vs that 10:00 price for
trading day k after entry (lo = that day's low, for stop detection; cl = that day's close).
"""
from __future__ import annotations
import os, sys, datetime
import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/sushil/trading")
from collect_bars import load_bars  # noqa: E402

_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(_DIR, "cache")
NF = 15                      # forward trading days to record
MOVE_MIN = 3.0               # % move (vs prior close) to qualify as an event
PRICE_LO, PRICE_HI = 5.0, 800.0
IS_END = "2025-07-01"        # design window ends here; everything after is sealed OOS
OOS1_END = "2026-01-01"      # OOS window 1 = H2 2025;  OOS window 2 = 2026+


def _daily_frame(sym: str) -> pd.DataFrame | None:
    """Per-session-day OHLC + 10:00 price + opening-range stats for one symbol."""
    try:
        df = load_bars(sym, start="2024-01-01", end="2026-09-15")
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    d = df.between_time("09:30", "15:59").copy()
    if len(d) == 0:
        return None
    d["date"] = d.index.date
    g = d.groupby("date")
    frame = pd.DataFrame({
        "open": g["open"].first(),
        "high": g["high"].max(),
        "low": g["low"].min(),
        "close": g["close"].last(),
    })
    orb = d.between_time("09:30", "09:59").copy()
    orb["tp"] = (orb["high"] + orb["low"] + orb["close"]) / 3
    og = orb.groupby("date")
    frame["or_high"] = og["high"].max()
    frame["or_low"] = og["low"].min()
    frame["vwap10"] = og.apply(lambda x: (x["tp"] * x["volume"]).sum() / max(x["volume"].sum(), 1),
                               include_groups=False)
    a10 = d.between_time("10:00", "15:59").groupby("date")
    frame["price10"] = a10["open"].first()
    frame = frame.dropna(subset=["price10", "or_high", "or_low"])
    frame.index = pd.to_datetime(frame.index)
    return frame.sort_index()


def build_dataset(force: bool = False, progress: bool = True):
    """Build (or load) personality, tide, events. Returns (events, tide, personality)."""
    ev_p = os.path.join(CACHE, "events.csv")
    td_p = os.path.join(CACHE, "tide.csv")
    pe_p = os.path.join(CACHE, "personality.csv")
    if not force and all(os.path.exists(p) for p in (ev_p, td_p, pe_p)):
        return load_dataset()

    import sqlite3
    m = sqlite3.connect("/Users/sushil/trading/market_data.db")
    syms = [r[0] for r in m.execute("SELECT DISTINCT symbol FROM bars_5m ORDER BY symbol")]

    vol = {}
    fwd_all = []      # per symbol-day forward cl paths (for the tide)
    event_rows = []
    for i, sym in enumerate(syms):
        if progress and i % 40 == 0:
            print(f"  building {i}/{len(syms)} ... {len(event_rows)} events", flush=True)
        f = _daily_frame(sym)
        if f is None or len(f) < NF + 2:
            continue
        vol[sym] = float(((f["high"] - f["low"]) / f["close"] * 100).median())
        p10 = f["price10"]
        prior_close = f["close"].shift(1)
        day_chg_10 = (p10 - prior_close) / prior_close * 100
        gap = (f["open"] - prior_close) / prior_close * 100
        ext_vwap = (p10 - f["vwap10"]) / f["vwap10"] * 100
        rng = (f["or_high"] - f["or_low"]).replace(0, np.nan)
        or_pos = (p10 - f["or_low"]) / rng * 100
        # forward paths vs the 10:00 price
        cl = {k: (f["close"].shift(-k) - p10) / p10 * 100 for k in range(1, NF + 1)}
        lo = {k: (f["low"].shift(-k) - p10) / p10 * 100 for k in range(1, NF + 1)}
        base = pd.DataFrame({"date": f.index, "symbol": sym, **{f"cl{k}": cl[k].values for k in cl}})
        fwd_all.append(base)  # tide uses ALL symbol-days' forward closes
        # events = qualifying movers only
        mask = (day_chg_10.abs() >= MOVE_MIN) & (p10 >= PRICE_LO) & (p10 <= PRICE_HI)
        mask = mask & cl[NF].notna()   # need full forward window
        if mask.any():
            er = pd.DataFrame({
                "date": f.index[mask.values], "symbol": sym,
                "price": p10[mask].values,
                "day_chg_10": day_chg_10[mask].round(2).values,
                "gap": gap[mask].round(2).values,
                "ext_vwap": ext_vwap[mask].round(2).values,
                "or_pos": or_pos[mask].round(1).values,
                "direction": np.where(day_chg_10[mask].values > 0, "UP", "DOWN"),
            })
            for k in range(1, NF + 1):
                er[f"cl{k}"] = cl[k][mask].round(3).values
                er[f"lo{k}"] = lo[k][mask].round(3).values
            event_rows.append(er)

    # --- Tide: universe average forward return per day, per horizon -----------
    fwd = pd.concat(fwd_all, ignore_index=True)
    tide = pd.DataFrame({"date": sorted(fwd["date"].unique())})
    for k in range(1, NF + 1):
        daily_mean = fwd.groupby("date")[f"cl{k}"].mean()
        tide[f"tide{k}"] = tide["date"].map(daily_mean)
    # --- Personality: volatility terciles across the universe ----------------
    vser = pd.Series(vol)
    lo_c, hi_c = vser.quantile(0.33), vser.quantile(0.66)
    personality = pd.DataFrame({"symbol": vser.index, "daily_range": vser.values})
    personality["cluster"] = np.where(vser.values < lo_c, "CALM",
                              np.where(vser.values > hi_c, "WILD", "MID"))
    personality.attrs["lo_c"] = lo_c
    personality.attrs["hi_c"] = hi_c

    events = pd.concat(event_rows, ignore_index=True)
    events = events.merge(personality[["symbol", "cluster"]], on="symbol", how="left")

    os.makedirs(CACHE, exist_ok=True)
    events.to_csv(ev_p, index=False)
    tide.to_csv(td_p, index=False)
    personality.to_csv(pe_p, index=False)
    print(f"  cached: {len(events)} events, {len(tide)} tide-days, {len(personality)} stocks "
          f"(CALM<{lo_c:.1f}%<MID<{hi_c:.1f}%<WILD)")
    return load_dataset()


def load_dataset():
    ev = pd.read_csv(os.path.join(CACHE, "events.csv"), parse_dates=["date"])
    td = pd.read_csv(os.path.join(CACHE, "tide.csv"), parse_dates=["date"])
    pe = pd.read_csv(os.path.join(CACHE, "personality.csv"))
    return ev, td, pe


def period_of(dates: pd.Series) -> pd.Series:
    """Label each date IS / OOS1 / OOS2 for honest in-sample vs sealed-forward reporting."""
    return np.where(dates < IS_END, "IS", np.where(dates < OOS1_END, "OOS1", "OOS2"))


def _daily_price_matrix(which: str, force: bool = False) -> pd.DataFrame:
    """Wide (date x symbol) daily RTH matrix for `which` in {'close','open'} — the raw material
    for cross-sectional (whole-universe ranking) engines. Cached per field."""
    p = os.path.join(CACHE, f"daily_{which}.csv")
    if os.path.exists(p) and not force:
        return pd.read_csv(p, parse_dates=["date"]).set_index("date")
    import sqlite3
    m = sqlite3.connect("/Users/sushil/trading/market_data.db")
    syms = [r[0] for r in m.execute("SELECT DISTINCT symbol FROM bars_5m ORDER BY symbol")]
    agg = "last" if which == "close" else "first"
    cols = {}
    for i, s in enumerate(syms):
        if i % 60 == 0:
            print(f"  daily-{which} {i}/{len(syms)}", flush=True)
        try:
            df = load_bars(s, start="2024-01-01", end="2026-09-15")
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue
        d = df.between_time("09:30", "15:59").copy()
        d["date"] = d.index.date
        cols[s] = getattr(d.groupby("date")[which], agg)()
    mat = pd.DataFrame(cols)
    mat.index = pd.to_datetime(mat.index); mat = mat.sort_index(); mat.index.name = "date"
    os.makedirs(CACHE, exist_ok=True)
    mat.to_csv(p)
    return mat


def load_daily_close(force: bool = False) -> pd.DataFrame:
    """Wide (date x symbol) daily RTH-close matrix. Cached to factory/cache/daily_close.csv."""
    return _daily_price_matrix("close", force=force)


def load_daily_open(force: bool = False) -> pd.DataFrame:
    """Wide (date x symbol) daily RTH-open matrix (first 5-min bar of the session).
    Raw material for the overnight (close->open) cross-sectional engine."""
    return _daily_price_matrix("open", force=force)


if __name__ == "__main__":
    force = "--force" in sys.argv
    build_dataset(force=force)
