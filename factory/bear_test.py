"""
Bear stress-test (FREE — yfinance daily, no Databento spend).

The factory's whole 2.5yr backend is one regime: a 2024-26 high-beta momentum bull. Every
engine — the 2 that passed and the 5 that failed — was judged inside that single regime. The
one thing that can't be manufactured from that tape is a real BEAR. 2022 (S&P -19%, the rate
selloff) + 2023 (recovery) gives us that, for free, at DAILY resolution via yfinance.

What this can and cannot test:
  ✓ the DAILY cross-sectional engines (Contrarian, Steady Hand, Whiplash, Night Shift) — their
    whole logic is daily close/open ranking, so a 2022-23 daily matrix tests them faithfully.
  ~ Wave Rider — its live edge needs 5-min intraday (10:00 VWAP-hold). Here it gets a DAILY
    PROXY (buy WILD stocks up >=3% close-to-close, hold 3d, 8% stop). Weaker than the real thing
    (no intraday filter, entry at close not 10:00) but a fair directional read on whether the
    momentum edge survives a bear. If the proxy dies, THAT is the trigger to consider a small
    (~$5-15) Databento 5-min 2022 pull for the real intraday test.

Everything is scored on market-neutral ALPHA (return - universe mean), same basis as the gate.
Run:  venv/bin/python -m factory.bear_test
Cache: factory/cache/bear_*.csv (download once; delete to refresh).
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/sushil/trading")
_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(_DIR, "cache")

DL_START = "2017-06-01"   # buffer before 2018 for lookbacks
DL_END   = "2024-01-01"
PERIODS = {                # (label, start, end) — inclusive start, exclusive end
    "2018 Q4 SELLOFF": ("2018-10-01", "2019-01-01"),   # −19.8% S&P peak-to-trough
    "2020 COVID CRASH": ("2020-02-15", "2020-04-16"),  # −34% crash + snap-back
    "2020 FULL":       ("2020-01-01", "2021-01-01"),
    "2022 BEAR":       ("2022-01-01", "2023-01-01"),   # rate selloff, −19%
    "2023 RECOVERY":   ("2023-01-01", "2024-01-01"),
}


def _universe() -> list[str]:
    import sqlite3
    m = sqlite3.connect("/Users/sushil/trading/market_data.db")
    return [r[0] for r in m.execute("SELECT DISTINCT symbol FROM bars_5m ORDER BY symbol")]


def download(force: bool = False):
    """Download 2021-24 daily OHLC for the universe → cached close/open/high/low matrices.
    Names that didn't exist in 2022 simply come back short/empty and are dropped (this is the
    honest bear universe — smaller than today's, no way around survivorship without paid PIT data)."""
    paths = {f: os.path.join(CACHE, f"bear_{f}.csv") for f in ("close", "open", "high", "low")}
    if not force and all(os.path.exists(p) for p in paths.values()):
        return {f: pd.read_csv(p, parse_dates=["date"]).set_index("date") for f, p in paths.items()}
    import yfinance as yf
    syms = _universe()
    fields = {"close": {}, "open": {}, "high": {}, "low": {}}
    CH = 60
    for i in range(0, len(syms), CH):
        chunk = syms[i:i + CH]
        print(f"  yfinance {i}/{len(syms)} ...", flush=True)
        try:
            df = yf.download(chunk, start=DL_START, end=DL_END, auto_adjust=True,
                             progress=False, threads=True, group_by="column")
        except Exception as e:
            print(f"    chunk failed: {e}"); continue
        if df is None or len(df) == 0:
            continue
        for fld, dst in (("Close", "close"), ("Open", "open"), ("High", "high"), ("Low", "low")):
            if fld not in df.columns.get_level_values(0):
                continue
            sub = df[fld]
            for s in sub.columns:
                col = sub[s].dropna()
                if len(col) > 200:            # need a real 2022+ history
                    fields[dst][s] = col
    os.makedirs(CACHE, exist_ok=True)
    out = {}
    for fld, cols in fields.items():
        mat = pd.DataFrame(cols).sort_index()
        mat.index.name = "date"
        mat.to_csv(paths[fld])
        out[fld] = mat
    print(f"  cached bear matrices: {out['close'].shape[1]} names, "
          f"{out['close'].index.min().date()} -> {out['close'].index.max().date()}")
    return out


# ---------------------------------------------------------------------------
def _stats(alpha: pd.Series) -> dict:
    a = alpha.dropna().values
    if len(a) == 0:
        return dict(n=0, mean=np.nan, t=np.nan, win=np.nan)
    t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if a.std() > 0 else 0.0
    return dict(n=len(a), mean=a.mean(), t=t, win=(a > 0).mean())


def _report(name: str, trades: pd.DataFrame):
    """trades: columns date, alpha (per-leg / per-trade). Print per-period alpha stats."""
    print(f"\n── {name} " + "─" * (52 - len(name)))
    if trades is None or len(trades) == 0:
        print("   no trades in window"); return
    tr = trades.dropna(subset=["alpha"]).copy()
    tr["date"] = pd.to_datetime(tr["date"])
    for lbl, (s, e) in PERIODS.items():
        w = tr[(tr["date"] >= s) & (tr["date"] < e)]
        st = _stats(w["alpha"])
        flag = "" if st["n"] == 0 else ("  ✅" if (st["mean"] > 0 and st["t"] >= 1.5) else
                                        ("  ⚠" if st["mean"] > 0 else "  ❌"))
        m = f"{st['mean']:+.3f}%" if st["n"] else "  —  "
        t = f"{st['t']:+.2f}" if st["n"] else " — "
        wn = f"{st['win']:.0%}" if st["n"] else " — "
        print(f"   {lbl:14} alpha/leg {m}  t={t}  win={wn}  n={st['n']}{flag}")


def wave_rider_proxy(mats: dict) -> pd.DataFrame:
    """Daily proxy for Wave Rider: WILD stocks up >=3% close-to-close, hold 3d, 8% close-stop,
    market-neutral alpha vs the universe's mean 3-day forward return."""
    close, high, low = mats["close"], mats["high"], mats["low"]
    HOLD, STOP, MOVE = 3, 8.0, 3.0
    # personality = WILD tercile by median daily range over the window
    rng = ((high - low) / close * 100.0).median()
    wild_cut = rng.quantile(0.66)
    wild = set(rng[rng > wild_cut].index)
    ret1 = close.pct_change() * 100.0                      # today's close-to-close move
    fwd_close = (close.shift(-HOLD) / close - 1.0) * 100.0  # 3-day forward close return
    tide = fwd_close.mean(axis=1)                          # universe mean 3d forward = the bear tide
    # stop: worst forward low within the hold, vs entry close
    worst_lo = None
    for k in range(1, HOLD + 1):
        lk = (low.shift(-k) / close - 1.0) * 100.0
        worst_lo = lk if worst_lo is None else np.minimum(worst_lo, lk)
    rows = []
    idx = close.index
    for i in range(len(idx)):
        day = idx[i]
        movers = ret1.iloc[i]
        cand = movers[(movers >= MOVE) & movers.index.isin(wild)].index
        for s in cand:
            f = fwd_close.iloc[i][s]
            if pd.isna(f):
                continue
            wl = worst_lo.iloc[i][s]
            r = -STOP if (pd.notna(wl) and wl <= -STOP) else f
            rows.append((day, s, r - tide.iloc[i]))
    return pd.DataFrame(rows, columns=["date", "symbol", "alpha"])


def main(force=False):
    print("Downloading (or loading cached) 2021-24 daily OHLC — FREE via yfinance ...")
    mats = download(force=force)
    close, opens = mats["close"], mats["open"]
    n_2022 = int((close.loc["2022-01-01":"2022-12-31"].notna().any()).sum())
    print(f"\nBear universe: {close.shape[1]} names with data; {n_2022} trading in 2022 "
          f"(survivorship — newer listings absent).")

    from factory.xsec import XSectionalReversal, XSectionalLowVol, OvernightDrift
    # per-period universe sizes (survivorship worsens going back — newer listings absent)
    sizes = {lbl: int((close.loc[s:e].notna().any()).sum()) for lbl, (s, e) in PERIODS.items()}
    print("Names with data per period: " + "  ".join(f"{k}={v}" for k, v in sizes.items()))

    print("\n" + "=" * 66)
    print("CROSS-SECTIONAL ENGINES — crisis-regime market-neutral alpha")
    print("=" * 66)
    for eng in (XSectionalReversal(), OvernightDrift(), XSectionalLowVol()):
        if isinstance(eng, OvernightDrift):
            tr = eng.run(close=close, opens=opens)
        else:
            tr = eng.run(close=close)
        _report(f"{eng.spec.nickname} [{eng.spec.name}]", tr)

    print("\n" + "=" * 66)
    print("WAVE RIDER — DAILY PROXY (real edge needs 5-min intraday; see module docstring)")
    print("=" * 66)
    _report("Wave Rider proxy [momentum_wild]", wave_rider_proxy(mats))

    print("\nLegend: ✅ mean>0 & t>=1.5   ⚠ positive but weak/insignificant   ❌ negative")
    print("Alpha is market-neutral (return - universe mean) — survives bull OR bear by construction.")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
