"""
research_options_structure_lab.py — Sep 20 2026

THE QUESTION nobody had asked with numbers: for each option structure, how big a
move does the underlying have to make, over OUR holding period, before the
structure returns a single dollar net of theta and bid-ask?

Then: how often does our universe actually make that move?

Black-Scholes at the universe's REAL median IV, bid-ask from our OWN
options_chain_snapshots (not an assumption), forward-return distribution from
market_data.db bars. Pure research — imports nothing live, writes nothing.
"""
import sqlite3, math, numpy as np, pandas as pd
from scipy.stats import norm

R = 0.04

def bs_call(S, K, T, sig, r=R):
    """Vectorised over S (array or scalar)."""
    S = np.asarray(S, dtype=float)
    if T <= 0: return np.maximum(S - K, 0.0)
    d1 = (np.log(S/K) + (r + sig*sig/2)*T) / (sig*math.sqrt(T))
    d2 = d1 - sig*math.sqrt(T)
    out = S*norm.cdf(d1) - K*math.exp(-r*T)*norm.cdf(d2)
    return out if out.ndim else float(out)

def bs_put(S, K, T, sig, r=R):
    S = np.asarray(S, dtype=float)
    if T <= 0: return np.maximum(K - S, 0.0)
    d1 = (np.log(S/K) + (r + sig*sig/2)*T) / (sig*math.sqrt(T))
    d2 = d1 - sig*math.sqrt(T)
    out = K*math.exp(-r*T)*norm.cdf(-d2) - S*norm.cdf(-d1)
    return out if out.ndim else float(out)

def bs_put_delta(S, K, T, sig, r=R):
    if T <= 0: return -1.0 if S < K else 0.0
    d1 = (math.log(S/K) + (r + sig*sig/2)*T) / (sig*math.sqrt(T))
    return norm.cdf(d1) - 1.0

def strike_for_put_delta(S, T, sig, target):
    """target is positive magnitude, e.g. 0.35 -> strike where put delta = -0.35"""
    lo, hi = S*0.2, S*3.0
    for _ in range(80):
        mid = (lo+hi)/2
        if abs(bs_put_delta(S, mid, T, sig)) < target: lo = mid
        else: hi = mid
    return (lo+hi)/2

def bs_delta(S, K, T, sig, r=R):
    if T <= 0: return 1.0 if S > K else 0.0
    d1 = (math.log(S/K) + (r + sig*sig/2)*T) / (sig*math.sqrt(T))
    return norm.cdf(d1)

def strike_for_delta(S, T, sig, target):
    """Invert delta -> strike."""
    lo, hi = S*0.3, S*3.0
    for _ in range(80):
        mid = (lo+hi)/2
        if bs_delta(S, mid, T, sig) > target: lo = mid
        else: hi = mid
    return (lo+hi)/2

_PIV = {}

def _panel(start):
    if start in _PIV: return _PIV[start]
    mc = sqlite3.connect('market_data.db')
    df = pd.read_sql_query(
        f"select symbol, substr(replace(ts_utc,'T',' '),1,10) d, close, replace(ts_utc,'T',' ') ts "
        f"from bars_5m where ts_utc>='{start}'", mc)
    daily = df.sort_values('ts').groupby(['symbol','d'], as_index=False).last()
    _PIV[start] = daily.pivot(index='d', columns='symbol', values='close').sort_index()
    return _PIV[start]


def forward_dist(H, start='2024-01-01'):
    piv = _panel(start)
    fwd = (piv.shift(-H)/piv - 1.0).values.ravel()
    return fwd[~np.isnan(fwd)]

# ── structures ───────────────────────────────────────────────────────────────
# spread_pct_of_mid: measured from our own chain snapshots (see table in session)
STRUCTS = [
    # name,                 kind,    long_delta, short_delta, dte, spread_pct_of_mid
    ('shares',              'stock',  None, None,  0,   0.0),
    ('deep ITM call d.85',  'call',   0.85, None, 45,   3.2),
    ('ITM call d.75',       'call',   0.75, None, 45,   4.2),
    ('ATM call d.55',       'call',   0.55, None, 45,   3.1),
    ('OTM call d.35',       'call',   0.35, None, 45,   5.1),
    ('debit spr .40/.20',   'spread', 0.40, 0.20, 36,   8.0),   # ~= what we traded
    ('debit spr .60/.35',   'spread', 0.60, 0.35, 45,   6.0),
    ('debit spr .75/.50',   'spread', 0.75, 0.50, 45,   5.0),
    # CREDIT — short premium, defined risk. Never traded by this system.
    ('bull put cr .35/.20', 'putcr',  0.35, 0.20, 45,   6.0),
    ('bull put cr .50/.30', 'putcr',  0.50, 0.30, 45,   5.0),
    ('bull put cr .30/.15', 'putcr',  0.30, 0.15, 45,   7.0),
]

def value(kind, S, K1, K2, T, sig):
    """For credit structures returns the LIABILITY (what it costs to close)."""
    if kind == 'stock':  return S
    if kind == 'call':   return bs_call(S, K1, T, sig)
    if kind == 'putcr':  return bs_put(S, K1, T, sig) - bs_put(S, K2, T, sig)
    return bs_call(S, K1, T, sig) - bs_call(S, K2, T, sig)

def analyse(H_days, sig, S=100.0, label='', detrend=False):
    fwd = forward_dist(H_days)
    if detrend: fwd = fwd - fwd.mean()
    T_hold = H_days/365.0
    out = []
    for name, kind, d1, d2, dte, sprpct in STRUCTS:
        T0 = dte/365.0 if dte else T_hold
        if kind == 'putcr':
            K1 = strike_for_put_delta(S, T0, sig, d1)   # short leg (nearer the money)
            K2 = strike_for_put_delta(S, T0, sig, d2)   # long leg (further OTM)
        else:
            K1 = strike_for_delta(S, T0, sig, d1) if d1 else None
            K2 = strike_for_delta(S, T0, sig, d2) if d2 else None
        v0 = float(value(kind, S, K1, K2, T0, sig))
        if v0 <= 0: continue
        T1 = max(T0 - T_hold, 1e-6)
        fric = v0 * (sprpct/100.0) if kind != 'stock' else v0*0.0002

        if kind == 'putcr':
            risk = (K1 - K2) - v0            # capital at risk = width - credit
            if risk <= 0: continue
            # breakeven: liability back to credit received, net of friction
            lo, hi = -0.9, 1.0
            for _ in range(100):
                mid = (lo+hi)/2
                if float(value(kind, S*(1+mid), K1, K2, T1, sig)) > v0 - fric: lo = mid
                else: hi = mid
            be = (lo+hi)/2
        else:
            risk = v0
            lo, hi = -0.5, 3.0
            for _ in range(100):
                mid = (lo+hi)/2
                if float(value(kind, S*(1+mid), K1, K2, T1, sig)) < v0 + fric: lo = mid
                else: hi = mid
            be = (lo+hi)/2

        # expected structure return over the REAL forward distribution
        Sf = S*(1+fwd)
        vf = value(kind, Sf, K1, K2, T1, sig)
        if kind == 'putcr':
            ret = (v0 - vf - fric)/risk      # profit = credit kept, per $ at risk
        else:
            ret = (vf - v0 - fric)/risk
        pclear = float((fwd>=be).mean()*100) if kind!='putcr' else float((fwd>=be).mean()*100)
        out.append(dict(structure=name, risk_pct_spot=round(risk/S*100,2),
                        lev=round(abs(bs_put_delta(S,K1,T0,sig)-bs_put_delta(S,K2,T0,sig))*S/risk,1) if kind=='putcr' else (round((bs_delta(S,K1,T0,sig)-(bs_delta(S,K2,T0,sig) if K2 else 0))*S/v0,1) if kind!='stock' else 1.0),
                        BE_move_pct=round(be*100,2),
                        P_clear_BE=round(pclear,1),
                        exp_ret_pct=round(float(ret.mean()*100),2),
                        median_ret_pct=round(float(np.median(ret)*100),2),
                        P_total_loss=round(float((ret<=-0.95).mean()*100),1))) 
    df = pd.DataFrame(out)
    print(f"\n=== HOLD {H_days} trading days · IV {sig*100:.0f}% · {label} ===")
    print(f"(universe forward {H_days}d: median {np.median(fwd)*100:+.2f}%  mean {fwd.mean()*100:+.2f}%)")
    print(df.to_string(index=False))
    return df

if __name__ == '__main__':
    import sys
    IV = float(sys.argv[1]) if len(sys.argv)>1 else 0.74   # our universe's median IV
    for H in (1, 3, 7, 21):
        analyse(H, IV, label='AS-TRADED (universe drift included)')
    print("\n\n############ DETRENDED CONTROL — universe mean move removed ############")
    print("############ (isolates the STRUCTURE from the 2024-26 bull tide)   ############")
    for H in (7, 21):
        analyse(H, IV, label='DETRENDED', detrend=True)
