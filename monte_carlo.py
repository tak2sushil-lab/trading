# monte_carlo.py — Monte Carlo simulation for day-trading strategy risk analysis
#
# Purpose: Run the full backtest (2020–today) to collect real trade P&Ls, then
#          randomly reorder those trades 1,000 times to stress-test worst-case
#          drawdown sequences we could realistically face.
#
# Usage:
#   venv/bin/python monte_carlo.py
#   venv/bin/python monte_carlo.py NVDA PLTR AMD TSLA
#
# Core strategy logic copied directly from backtest_strategy.py (no import).

import yfinance as yf
import pandas as pd
import numpy as np
import itertools
import sys
from datetime import date

import warnings
warnings.filterwarnings('ignore')

# ── Config ─────────────────────────────────────────────────────────────────
START_DATE        = '2020-01-01'
END_DATE          = date.today().isoformat()
SYMBOLS           = sys.argv[1:] if len(sys.argv) > 1 else ['NVDA', 'PLTR', 'MSFT']
CAPITAL_PER_TRADE = 2000
ATR_PERIOD        = 14
STOP_PCT          = 5.0
ATR_TRAIL_MULT    = 1.5
ATR_FADE_MULT     = 1.0
MIN_RR            = 2.5
MIN_VOLUME_RATIO  = 1.3
MIN_TODAY_GAIN    = 1.5
SKIP_WEAK_DAYS    = True

N_SIMS            = 1000

# Risk-of-ruin thresholds (dollar losses from zero starting equity)
ROR_THRESHOLDS    = [-500, -1000, -2000]


# ── Copied from backtest_strategy.py ───────────────────────────────────────

def build_spy_regime(start, end):
    spy = yf.download('SPY', start=start, end=end, progress=False, auto_adjust=True)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)
    spy['spy_chg'] = spy['Close'].pct_change() * 100
    spy['regime']  = spy['spy_chg'].apply(
        lambda c: 'STRONG' if c >= 0.5 else ('WEAK' if c <= -0.5 else 'NORMAL')
    )
    return spy[['spy_chg', 'regime']]


def add_atr(df):
    h, l, c = df['High'], df['Low'], df['Close']
    tr = pd.concat([h - l,
                    (h - c.shift(1)).abs(),
                    (l - c.shift(1)).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(ATR_PERIOD).mean()
    return df


def grade_day(i, df, spy_chg, regime):
    row      = df.iloc[i]
    prev_row = df.iloc[i - 1]
    df_upto  = df.iloc[:i + 1]

    price = row['Open']
    atr   = row['atr']
    if pd.isna(atr) or atr <= 0 or pd.isna(price) or price < 5:
        return 'SKIP', 0, []

    if SKIP_WEAK_DAYS and regime == 'WEAK':
        return 'SKIP', 0, []

    ma20 = df_upto['Close'].rolling(20).mean().iloc[-1]
    if pd.isna(ma20) or price < ma20:
        return 'SKIP', 0, []

    today_gain = (row['Close'] - prev_row['Close']) / prev_row['Close'] * 100
    if today_gain < MIN_TODAY_GAIN:
        return 'SKIP', 0, []

    avg_vol   = df_upto['Volume'].rolling(20).mean().iloc[-2] if len(df_upto) >= 21 else None
    vol_ratio = float(row['Volume'] / avg_vol) if avg_vol and avg_vol > 0 else 1.0
    if vol_ratio < MIN_VOLUME_RATIO:
        return 'SKIP', 0, []

    gap_pct      = (row['Open'] - prev_row['Close']) / prev_row['Close'] * 100
    vwap_est     = (row['Open'] + row['High'] + row['Low'] + row['Close']) / 4
    vwap_reclaim = row['Open'] < vwap_est and row['Close'] > vwap_est
    orb_proxy    = gap_pct >= 1.5 and row['Close'] >= row['Open'] * 0.998
    hod_break    = row['Close'] >= row['High'] * 0.990

    bull_flag = False
    if len(df_upto) >= 6:
        prior3_chg  = (float(df_upto['Close'].iloc[-2]) - float(df_upto['Close'].iloc[-5])) \
                      / max(float(df_upto['Close'].iloc[-5]), 0.01) * 100
        today_range = (row['High'] - row['Low']) / max(row['Open'], 0.01) * 100
        bull_flag   = prior3_chg >= 5.0 and today_range < 3.0 and row['Close'] > row['Open']

    strong_momo = today_gain >= 5.0
    rs_vs_spy   = today_gain - spy_chg
    rs_leader   = rs_vs_spy >= 3.0 and today_gain >= 2.0

    has_pattern = orb_proxy or vwap_reclaim or bull_flag or hod_break or strong_momo or rs_leader
    if not has_pattern:
        return 'SKIP', 0, []

    score   = 0
    reasons = []

    if orb_proxy:    score += 30; reasons.append('ORB')
    if vwap_reclaim: score += 25; reasons.append('VWAP reclaim')
    if bull_flag:    score += 25; reasons.append('Bull flag')
    if hod_break:    score += 20; reasons.append('HOD break')
    if rs_leader:    score += 20; reasons.append(f'RS +{rs_vs_spy:.1f}% vs SPY')

    if vol_ratio >= 2.0:   score += 25; reasons.append(f'{vol_ratio:.1f}x vol')
    elif vol_ratio >= 1.5: score += 15; reasons.append(f'{vol_ratio:.1f}x vol')
    else:                  score += 5;  reasons.append(f'{vol_ratio:.1f}x vol')

    ema8  = float(df_upto['Close'].ewm(span=8).mean().iloc[-1])
    ema21 = float(df_upto['Close'].ewm(span=21).mean().iloc[-1])
    if price > ema8 > ema21: score += 20; reasons.append('EMA uptrend')
    elif price > ema21:      score += 10; reasons.append('Above EMA21')

    d = df_upto['Close'].diff()
    g = d.clip(lower=0).rolling(14).mean().iloc[-1]
    l_ = (-d.clip(upper=0)).rolling(14).mean().iloc[-1]
    rsi = round(float(100 - (100 / (1 + g / l_))), 1) if l_ and l_ > 0 else 50
    if 45 <= rsi <= 65:  score += 20; reasons.append(f'RSI {rsi:.0f}')
    elif 65 < rsi <= 80: score += 10; reasons.append(f'RSI {rsi:.0f}↑')
    else:                score += 5

    if today_gain >= 5.0:   score += 30; reasons.append(f'+{today_gain:.1f}%')
    elif today_gain >= 3.0: score += 20; reasons.append(f'+{today_gain:.1f}%')
    elif today_gain >= 1.5: score += 10; reasons.append(f'+{today_gain:.1f}%')

    if regime == 'STRONG':  score += 15; reasons.append('Strong mkt')
    elif regime == 'NORMAL': score += 5

    score += 5  # R:R always passes by construction

    grade = 'A+' if score >= 80 else 'A' if score >= 65 else 'B' if score >= 50 else 'C'

    if grade in ('B', 'C'):
        return 'SKIP', score, reasons
    if spy_chg < 0 and grade != 'A+':
        return 'SKIP', score, reasons

    return grade, score, reasons


def simulate_trade(row, atr):
    entry  = row['Open']
    sl     = round(entry * (1 - STOP_PCT / 100), 2)
    one_r  = entry * (1 + STOP_PCT / 100)
    hit_1r = row['High'] >= one_r

    half_cap       = CAPITAL_PER_TRADE / 2
    partial_locked = STOP_PCT / 100 * half_cap if hit_1r else 0.0
    rem_cap        = half_cap if hit_1r else CAPITAL_PER_TRADE

    if row['Low'] <= sl:
        if hit_1r:
            rest_loss = -STOP_PCT / 100 * rem_cap
            total_pnl = round(partial_locked + rest_loss, 2)
            return 'PARTIAL_STOP', round(total_pnl / CAPITAL_PER_TRADE * 100, 2), total_pnl, sl
        return 'STOP', round(-STOP_PCT, 2), round(-STOP_PCT * CAPITAL_PER_TRADE / 100, 2), sl

    trail_stop = row['High'] - ATR_TRAIL_MULT * atr
    if row['Close'] < trail_stop:
        rest_pnl  = (trail_stop - entry) / entry * rem_cap
        total_pnl = round(partial_locked + rest_pnl, 2)
        result    = 'WIN' if total_pnl > 0 else 'FADE'
        return result, round(total_pnl / CAPITAL_PER_TRADE * 100, 2), total_pnl, trail_stop

    fade_stop = row['High'] - ATR_FADE_MULT * atr
    if row['Close'] < fade_stop:
        rest_pnl  = (row['Close'] - entry) / entry * rem_cap
        total_pnl = partial_locked + rest_pnl
        if total_pnl > 0:
            return 'FADE_EXIT', round(total_pnl / CAPITAL_PER_TRADE * 100, 2), round(total_pnl, 2), row['Close']

    rest_pnl  = (row['Close'] - entry) / entry * rem_cap
    total_pnl = round(partial_locked + rest_pnl, 2)
    result    = 'WIN' if total_pnl > 0 else 'LOSS'
    return result, round(total_pnl / CAPITAL_PER_TRADE * 100, 2), total_pnl, row['Close']


def backtest_symbol(symbol, spy_regime):
    df = yf.download(symbol, start=START_DATE, end=END_DATE,
                     progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if len(df) < 60:
        print(f"  {symbol}: insufficient data"); return pd.DataFrame()

    df     = add_atr(df)
    merged = df.join(spy_regime, how='left')
    merged['spy_chg'] = merged['spy_chg'].fillna(0)
    merged['regime']  = merged['regime'].fillna('NORMAL')

    trades = []
    for i in range(22, len(merged)):
        row     = merged.iloc[i]
        spy_chg = float(row['spy_chg'])
        regime  = str(row['regime'])

        grade, score, reasons = grade_day(i, merged, spy_chg, regime)
        if grade == 'SKIP':
            continue

        result, pnl_pct, pnl_usd, exit_price = simulate_trade(row, float(row['atr']))

        trades.append({
            'date':    merged.index[i].strftime('%Y-%m-%d'),
            'symbol':  symbol,
            'pnl_usd': pnl_usd,
            'result':  result,
        })

    return pd.DataFrame(trades)


# ── Monte Carlo helpers ─────────────────────────────────────────────────────

def max_consecutive_losses(pnls):
    """Return the longest consecutive loss streak in a P&L array."""
    losses = (pnls < 0).astype(int)
    if losses.sum() == 0:
        return 0
    max_run = max(
        (sum(1 for _ in group) for val, group in itertools.groupby(losses) if val == 1),
        default=0
    )
    return max_run


def calc_drawdown(pnls):
    """Return (max_drawdown_usd, max_drawdown_pct, recovery_bars).

    equity is the cumulative P&L curve anchored at STARTING_CAPITAL so that
    percentage drawdowns are stable and intuitive regardless of where in the
    trade sequence a loss cluster falls.
    """
    # Anchor to a starting capital so early small peaks don't distort pct
    starting_equity = CAPITAL_PER_TRADE * 5          # $10K reference portfolio
    equity   = starting_equity + np.cumsum(np.insert(pnls, 0, 0))  # n+1 points
    peak     = np.maximum.accumulate(equity)
    drawdown = equity - peak                          # <= 0 everywhere

    max_dd_usd = float(drawdown.min())

    # Percentage: drawdown / peak-at-trough (always > 0 since starting_equity > 0)
    worst_idx   = int(np.argmin(drawdown))
    peak_at_low = float(peak[worst_idx])
    max_dd_pct  = max_dd_usd / peak_at_low * 100

    # Recovery bars: from the trough index back to a new equity high
    recovery_bars = 0
    if max_dd_usd < 0:
        trough_val = equity[worst_idx]
        found_new_high = False
        for k in range(worst_idx + 1, len(equity)):
            recovery_bars += 1
            if equity[k] >= peak[worst_idx]:
                found_new_high = True
                break
        if not found_new_high:
            recovery_bars = len(equity) - worst_idx - 1  # still in drawdown at end

    return max_dd_usd, max_dd_pct, recovery_bars


def run_monte_carlo(pnls_raw):
    """Run N_SIMS shuffles of pnls_raw. Return dict of result arrays."""
    n = len(pnls_raw)
    rng = np.random.default_rng(seed=42)

    final_pnls     = np.zeros(N_SIMS)
    max_dd_usd_arr = np.zeros(N_SIMS)
    max_dd_pct_arr = np.zeros(N_SIMS)
    max_consec_arr = np.zeros(N_SIMS, dtype=int)
    recovery_arr   = np.zeros(N_SIMS, dtype=int)

    pnls = pnls_raw.copy()

    for s in range(N_SIMS):
        if (s + 1) % 100 == 0:
            print('.', end='', flush=True)

        rng.shuffle(pnls)

        final_pnls[s]     = pnls.sum()
        dd_usd, dd_pct, rec = calc_drawdown(pnls)
        max_dd_usd_arr[s]  = dd_usd
        max_dd_pct_arr[s]  = dd_pct
        max_consec_arr[s]  = max_consecutive_losses(pnls)
        recovery_arr[s]    = rec

    print()  # newline after progress dots

    return {
        'final_pnl':    final_pnls,
        'max_dd_usd':   max_dd_usd_arr,
        'max_dd_pct':   max_dd_pct_arr,
        'max_consec':   max_consec_arr,
        'recovery':     recovery_arr,
    }


def percentiles(arr, qs=(5, 25, 50, 75, 95)):
    return {q: float(np.percentile(arr, q)) for q in qs}


def fmt_dollar(v):
    sign = '+' if v >= 0 else '-'
    return f"${sign}{abs(v):,.0f}"


def fmt_pct(v, decimals=1):
    sign = '+' if v >= 0 else ''
    return f"{sign}{v:.{decimals}f}%"


def print_row(label, vals, fmt_fn, width=9):
    cells = '  '.join(f"{fmt_fn(vals[q]):>{width}}" for q in (5, 25, 50, 75, 95))
    print(f"  {label:<20}{cells}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\nLoading SPY regime {START_DATE} → {END_DATE}...")
    spy_regime = build_spy_regime(START_DATE, END_DATE)

    all_trades = []
    for sym in SYMBOLS:
        print(f"Backtesting {sym}...", end=' ', flush=True)
        df_sym = backtest_symbol(sym, spy_regime)
        if not df_sym.empty:
            all_trades.append(df_sym)
            wr_sym = len(df_sym[df_sym['pnl_usd'] > 0]) / len(df_sym) * 100
            print(f"{len(df_sym)} trades | WR {wr_sym:.0f}% | P&L ${df_sym['pnl_usd'].sum():+,.0f}")
        else:
            print("no qualifying trades")

    if not all_trades:
        print("\nNo trades found. Check MIN_TODAY_GAIN or MIN_VOLUME_RATIO thresholds.")
        return

    df_all  = pd.concat(all_trades, ignore_index=True).sort_values('date').reset_index(drop=True)
    pnls    = df_all['pnl_usd'].values.copy().astype(float)

    n_trades  = len(pnls)
    wins      = pnls[pnls > 0]
    losses    = pnls[pnls <= 0]
    total_pnl = pnls.sum()
    wr        = len(wins) / n_trades * 100 if n_trades else 0
    avg_win   = wins.mean()   if len(wins)   else 0.0
    avg_loss  = losses.mean() if len(losses) else 0.0
    ev        = (wr / 100 * avg_win) + ((1 - wr / 100) * avg_loss) if len(losses) else avg_win

    # ── Run sims ─────────────────────────────────────────────────────────
    print(f"\nRunning {N_SIMS:,} Monte Carlo simulations (dot = 100 sims)...")
    results = run_monte_carlo(pnls)

    # ── Percentile tables ─────────────────────────────────────────────────
    fp_pct   = percentiles(results['final_pnl'])
    dd_pct_p = percentiles(results['max_dd_pct'])   # note: dd is negative
    dd_usd_p = percentiles(results['max_dd_usd'])
    mc_pct   = percentiles(results['max_consec'])
    rec_pct  = percentiles(results['recovery'])

    # ── Risk of ruin ──────────────────────────────────────────────────────
    ror = {}
    for thresh in ROR_THRESHOLDS:
        # equity curve floor: any sim where cumulative P&L dropped below thresh
        count = 0
        pnls_copy = pnls.copy()
        rng2 = np.random.default_rng(seed=42)
        for _ in range(N_SIMS):
            rng2.shuffle(pnls_copy)
            equity = np.cumsum(np.insert(pnls_copy, 0, 0))
            if equity.min() <= thresh:
                count += 1
        ror[thresh] = count / N_SIMS * 100

    # ── Circuit breaker suggestion ─────────────────────────────────────────
    p5_consec = int(np.percentile(results['max_consec'], 5))   # worst-case streak

    # ── Print report ──────────────────────────────────────────────────────
    bar = '=' * 68
    thin = '-' * 68

    sym_str = ' '.join(SYMBOLS)
    print(f"\n\nMONTE CARLO SIMULATION — {sym_str}")
    print(f"{N_SIMS:,} simulations | {n_trades} trades resampled per run | ${CAPITAL_PER_TRADE:,}/trade")
    print(bar)

    print(f"\nINPUT TRADES (actual backtest results)")
    print(f"  Trades: {n_trades} | WR: {wr:.0f}% | EV: {fmt_dollar(ev)}/trade | Total P&L: {fmt_dollar(total_pnl)}")
    print(f"  Best trade: {fmt_dollar(pnls.max())} | Worst trade: {fmt_dollar(pnls.min())} "
          f"| Avg win: {fmt_dollar(avg_win)} | Avg loss: {fmt_dollar(avg_loss)}")

    print(f"\nDISTRIBUTION OF OUTCOMES ({N_SIMS:,} simulations)")
    hdr = f"  {'':20}{'P5':>9}  {'P25':>9}  {'Median':>9}  {'P75':>9}  {'P95':>9}"
    print(hdr)
    print(f"  {thin[:66]}")

    # Final P&L row
    fp_vals  = {q: fp_pct[q] for q in (5, 25, 50, 75, 95)}
    print_row("Final P&L:", fp_vals, fmt_dollar, width=9)

    # Max Drawdown % row — for display, P5 is worst (most negative), P95 is least bad
    dd_disp = {q: dd_pct_p[q] for q in (5, 25, 50, 75, 95)}
    print_row("Max Drawdown:", dd_disp, lambda v: f"{v:+.1f}%", width=9)

    # Max Consecutive Losses — P5 is highest (worst); flip percentiles for natural reading
    mc_disp = {q: int(round(mc_pct[q])) for q in (5, 25, 50, 75, 95)}
    print_row("Max Consec L:", mc_disp, lambda v: str(v), width=9)

    # Recovery (bars from trough to new high) — P5 = longest wait
    rec_disp = {q: int(round(rec_pct[q])) for q in (5, 25, 50, 75, 95)}
    print_row("Recovery (bars):", rec_disp, lambda v: str(v), width=9)

    # ── Worst-case analysis ────────────────────────────────────────────────
    p5_pnl    = fp_pct[5]
    p5_dd_pct = dd_pct_p[5]
    p5_dd_usd = dd_usd_p[5]
    p5_consec = mc_pct[5]
    p5_rec    = rec_pct[5]

    still_profitable = "still profitable" if p5_pnl > 0 else "at a loss"

    print(f"\nWORST-CASE ANALYSIS (5th percentile — 1-in-20 bad run)")
    print(f"  Final P&L:     {fmt_dollar(p5_pnl)} ({still_profitable})")
    print(f"  Max drawdown:  {p5_dd_pct:+.1f}%  ({fmt_dollar(p5_dd_usd)} worst single run)")
    print(f"  Max consec L:  {int(round(p5_consec)):>4}   ({int(round(p5_consec))} losses in a row)")
    print(f"  Recovery time: {int(round(p5_rec)):>4} bars  (from trough to new equity high)")

    # ── Risk of ruin table ──────────────────────────────────────────────────
    print(f"\nRISK OF RUIN  (% of {N_SIMS:,} sims that hit each floor)")
    for thresh in ROR_THRESHOLDS:
        pct = ror[thresh]
        flag = " <-- danger" if pct > 5 else (" <-- caution" if pct > 1 else "")
        print(f"  Equity < {fmt_dollar(thresh):>8}:  {pct:5.1f}% of sims{flag}")

    # ── Rules recommendation ───────────────────────────────────────────────
    # Worst daily estimate: assume worst 5% streak all hits on 1 day
    p5_dd_abs = abs(p5_dd_usd)
    portfolio_10k = 10_000
    dd_on_10k_pct = abs(p5_dd_pct)

    circuit_breaker = max(3, int(round(p5_consec)) - 1)

    print(f"\nTRADING RULES RECOMMENDATION")
    print(f"  Current: MAX_DAILY_LOSS $200, MAX_OPEN_TRADES 5")
    print(f"  Based on P5 drawdown ({p5_dd_pct:.1f}% on ${portfolio_10k:,} = {fmt_dollar(-dd_on_10k_pct/100*portfolio_10k)}):")
    print(f"  {'OK' if p5_dd_abs < 500 else 'WARN':>4} $200/day loss limit "
          f"({'covers' if p5_dd_abs < 2000 else 'may not cover'} worst daily scenario)")
    print(f"  {'OK':>4} 5-trade cap limits correlated drawdown")
    print(f"  Suggested circuit breaker: pause after {circuit_breaker} consecutive losses")

    print(f"\n{bar}\n")


if __name__ == '__main__':
    main()
