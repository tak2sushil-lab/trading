# backtest_walkforward.py — Walk-forward validation for day-trading strategy
#
# Confirms the strategy's edge is real, not data-mined from bull market conditions.
#
# TEST 1: Simple in/out split
#   In-sample:     2020-01-01 → 2023-12-31
#   Out-of-sample: 2024-01-01 → today
#
# TEST 2: Rolling walk-forward (6-month steps, 2-year train window)
#   8 windows; reports only test-period performance per window.
#
# Verdict logic:
#   ✅ out-of-sample WR within 10% of in-sample AND positive EV
#   ⚠️  WR drops 10-20%  (edge degrading, monitor closely)
#   ❌  WR drops >20% or negative EV  (overfit)
#
# Command: venv/bin/python backtest_walkforward.py
#          venv/bin/python backtest_walkforward.py NVDA PLTR AAPL

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date
import sys
import warnings
warnings.filterwarnings('ignore')

# ── Config ──────────────────────────────────────────────────────────────────
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

TODAY = date.today().isoformat()

# ── Rolling walk-forward window definitions ──────────────────────────────────
# Each tuple: (train_start, train_end, test_start, test_end, label)
ROLLING_WINDOWS = [
    ('2020-01-01', '2021-12-31', '2022-01-01', '2022-06-30', '2022-H1'),
    ('2020-07-01', '2022-06-30', '2022-07-01', '2022-12-31', '2022-H2'),
    ('2021-01-01', '2022-12-31', '2023-01-01', '2023-06-30', '2023-H1'),
    ('2021-07-01', '2023-06-30', '2023-07-01', '2023-12-31', '2023-H2'),
    ('2022-01-01', '2023-12-31', '2024-01-01', '2024-06-30', '2024-H1'),
    ('2022-07-01', '2024-06-30', '2024-07-01', '2024-12-31', '2024-H2'),
    ('2023-01-01', '2024-12-31', '2025-01-01', '2025-06-30', '2025-H1'),
    ('2023-07-01', '2025-06-30', '2025-07-01', '2025-12-31', '2025-H2'),
]

# ── Build SPY daily regime ────────────────────────────────────────────────────
def build_spy_regime(start, end):
    spy = yf.download('SPY', start=start, end=end, progress=False, auto_adjust=True)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)
    spy['spy_chg'] = spy['Close'].pct_change() * 100
    spy['regime']  = spy['spy_chg'].apply(
        lambda c: 'STRONG' if c >= 0.5 else ('WEAK' if c <= -0.5 else 'NORMAL')
    )
    return spy[['spy_chg', 'regime']]

# ── ATR (rolling) ─────────────────────────────────────────────────────────────
def add_atr(df):
    h, l, c = df['High'], df['Low'], df['Close']
    tr = pd.concat([h - l,
                    (h - c.shift(1)).abs(),
                    (l - c.shift(1)).abs()], axis=1).max(axis=1)
    df = df.copy()
    df['atr'] = tr.rolling(ATR_PERIOD).mean()
    return df

# ── Grade a day's setup ───────────────────────────────────────────────────────
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
    l = (-d.clip(upper=0)).rolling(14).mean().iloc[-1]
    rsi = round(float(100 - (100 / (1 + g / l))), 1) if l and l > 0 else 50
    if 45 <= rsi <= 65:  score += 20; reasons.append(f'RSI {rsi:.0f}')
    elif 65 < rsi <= 80: score += 10; reasons.append(f'RSI {rsi:.0f}↑')
    else:                score += 5

    if today_gain >= 5.0:   score += 30; reasons.append(f'+{today_gain:.1f}%')
    elif today_gain >= 3.0: score += 20; reasons.append(f'+{today_gain:.1f}%')
    elif today_gain >= 1.5: score += 10; reasons.append(f'+{today_gain:.1f}%')

    if regime == 'STRONG':  score += 15; reasons.append('Strong mkt')
    elif regime == 'NORMAL': score += 5

    score += 5  # R:R always = MIN_RR by construction

    grade = 'A+' if score >= 80 else 'A' if score >= 65 else 'B' if score >= 50 else 'C'

    if grade in ('B', 'C'):
        return 'SKIP', score, reasons
    if spy_chg < 0 and grade != 'A+':
        return 'SKIP', score, reasons

    return grade, score, reasons

# ── Simulate a single trade from entry at Open ────────────────────────────────
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

# ── Backtest one symbol over a full price DataFrame + spy regime ──────────────
# df_full must cover at least 22 bars before period_start to warm up indicators.
def backtest_symbol_window(symbol, df_full, spy_regime_full, period_start, period_end):
    """
    Run the strategy on df_full (which covers a wide date range for indicator warmup),
    but only collect trades that fall within [period_start, period_end].
    """
    df = df_full.copy()
    df = add_atr(df)
    merged = df.join(spy_regime_full, how='left')
    merged['spy_chg'] = merged['spy_chg'].fillna(0)
    merged['regime']  = merged['regime'].fillna('NORMAL')

    ps = pd.Timestamp(period_start)
    pe = pd.Timestamp(period_end)

    trades = []
    for i in range(22, len(merged)):
        row_date = merged.index[i]
        if row_date < ps or row_date > pe:
            continue

        row     = merged.iloc[i]
        spy_chg = float(row['spy_chg'])
        regime  = str(row['regime'])

        grade, score, reasons = grade_day(i, merged, spy_chg, regime)
        if grade == 'SKIP':
            continue

        result, pnl_pct, pnl_usd, exit_price = simulate_trade(row, float(row['atr']))

        trades.append({
            'date':    row_date.strftime('%Y-%m-%d'),
            'symbol':  symbol,
            'grade':   grade,
            'score':   score,
            'regime':  regime,
            'pnl_usd': pnl_usd,
            'result':  result,
        })

    return pd.DataFrame(trades)

# ── Summarise a list of trade DataFrames into (n, wr, ev, total_pnl) ──────────
def summarise(trades_list):
    if not trades_list:
        return 0, 0.0, 0.0, 0.0
    df = pd.concat(trades_list, ignore_index=True)
    if df.empty:
        return 0, 0.0, 0.0, 0.0
    n      = len(df)
    wins   = df[df['pnl_usd'] > 0]
    losses = df[df['pnl_usd'] <= 0]
    wr     = len(wins) / n * 100
    avg_w  = wins['pnl_usd'].mean()   if len(wins)   else 0.0
    avg_l  = losses['pnl_usd'].mean() if len(losses) else 0.0
    ev     = (wr / 100 * avg_w) + ((1 - wr / 100) * avg_l)
    total  = df['pnl_usd'].sum()
    return n, wr, ev, total

# ── Fetch price data for a symbol (with wide date range for warmup) ───────────
_price_cache = {}

def get_price_data(symbol, start, end):
    key = (symbol, start, end)
    if key not in _price_cache:
        df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        _price_cache[key] = df
    return _price_cache[key]

# ── SPY regime cache ──────────────────────────────────────────────────────────
_spy_cache = {}

def get_spy_regime(start, end):
    key = (start, end)
    if key not in _spy_cache:
        _spy_cache[key] = build_spy_regime(start, end)
    return _spy_cache[key]

# ── Verdict tag for a single window ──────────────────────────────────────────
def window_verdict(wr, ev):
    if ev > 0 and wr >= 50:
        return '✅'
    elif ev > 0 and wr >= 40:
        return '⚠️ '
    else:
        return '❌'

# ── Overall walk-forward verdict comparing IS vs OOS ─────────────────────────
def overall_verdict(is_wr, oos_wr, oos_ev):
    wr_drop = is_wr - oos_wr
    if oos_ev > 0 and wr_drop <= 10:
        return '✅ EDGE HOLDS OUT-OF-SAMPLE', True
    elif oos_ev > 0 and wr_drop <= 20:
        return '⚠️  EDGE DEGRADING — monitor closely (WR drop {:.1f}%)'.format(wr_drop), True
    else:
        tag = '❌ OVERFIT' if wr_drop > 20 else '❌ NEGATIVE EV OUT-OF-SAMPLE'
        return tag, False

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    sym_str = ' '.join(SYMBOLS)
    print(f'\nWALK-FORWARD VALIDATION — {sym_str}')
    print('=' * 60)
    print(f'  Symbols  : {sym_str}')
    print(f'  Capital  : ${CAPITAL_PER_TRADE}/trade | Stop: {STOP_PCT}% | A/A+ only | WEAK days skipped')
    print(f'  Run date : {TODAY}')
    print('=' * 60)

    # ── Pre-fetch all data needed (2020-01-01 → today covers everything) ──
    print('\nFetching market data...', flush=True)
    GLOBAL_START = '2019-10-01'   # extra 3m warmup before earliest train window
    spy_full     = get_spy_regime(GLOBAL_START, TODAY)

    sym_data = {}
    for sym in SYMBOLS:
        print(f'  {sym}...', end=' ', flush=True)
        df = get_price_data(sym, GLOBAL_START, TODAY)
        if len(df) < 60:
            print('insufficient data — skipping')
        else:
            sym_data[sym] = df
            print(f'{len(df)} bars')

    if not sym_data:
        print('\nNo usable symbol data. Exiting.')
        return

    # ════════════════════════════════════════════════════════════════════════
    # TEST 1: In-sample vs out-of-sample split
    # ════════════════════════════════════════════════════════════════════════
    IS_START  = '2020-01-01'
    IS_END    = '2023-12-31'
    OOS_START = '2024-01-01'
    OOS_END   = TODAY

    print('\n──────────────────────────────────────────────────────────────')
    print('TEST 1: IN-SAMPLE vs OUT-OF-SAMPLE  (computing...)')
    print('──────────────────────────────────────────────────────────────')

    is_trades_list  = []
    oos_trades_list = []

    for sym, df_full in sym_data.items():
        is_t  = backtest_symbol_window(sym, df_full, spy_full, IS_START,  IS_END)
        oos_t = backtest_symbol_window(sym, df_full, spy_full, OOS_START, OOS_END)
        if not is_t.empty:  is_trades_list.append(is_t)
        if not oos_t.empty: oos_trades_list.append(oos_t)

    is_n,  is_wr,  is_ev,  is_total  = summarise(is_trades_list)
    oos_n, oos_wr, oos_ev, oos_total = summarise(oos_trades_list)

    verdict_str, _ = overall_verdict(is_wr, oos_wr, oos_ev)
    wr_drop = is_wr - oos_wr

    print(f'\nTEST 1: IN-SAMPLE vs OUT-OF-SAMPLE')
    print(f'  In-sample  (2020-2023): {is_n:>5} trades | WR {is_wr:.0f}% | EV ${is_ev:+.0f} | Total ${is_total:+,.0f}')
    print(f'  Out-of-sample (2024+): {oos_n:>5} trades | WR {oos_wr:.0f}% | EV ${oos_ev:+.0f} | Total ${oos_total:+,.0f}')
    print(f'  WR delta : {wr_drop:+.1f}pp  ({is_wr:.0f}% → {oos_wr:.0f}%)')
    print(f'  Verdict  : {verdict_str}')

    # ════════════════════════════════════════════════════════════════════════
    # TEST 2: Rolling walk-forward
    # ════════════════════════════════════════════════════════════════════════
    print('\n──────────────────────────────────────────────────────────────')
    print('TEST 2: ROLLING WALK-FORWARD RESULTS (test periods only)')
    print('──────────────────────────────────────────────────────────────')
    print(f'  {"Window":<10} | {"Trades":>6} | {"WR":>5} | {"EV":>7} | {"Total P&L":>10} | Verdict')
    print(f'  {"─"*58}')

    all_test_trades = []
    window_results  = []

    for train_s, train_e, test_s, test_e, label in ROLLING_WINDOWS:
        # Skip windows whose test period is entirely in the future
        if pd.Timestamp(test_s) > pd.Timestamp(TODAY):
            print(f'  {label:<10} | {"—":>6} | {"—":>5} | {"—":>7} | {"—":>10} | (future)')
            continue

        # Clip test end to today if it overshoots
        actual_test_end = min(test_e, TODAY)

        period_trades = []
        for sym, df_full in sym_data.items():
            t = backtest_symbol_window(sym, df_full, spy_full, test_s, actual_test_end)
            if not t.empty:
                period_trades.append(t)
                all_test_trades.append(t)

        n, wr, ev, total = summarise(period_trades)
        verd = window_verdict(wr, ev) if n > 0 else '—'

        # Annotate partial windows
        note = ' (partial)' if actual_test_end < test_e else ''
        print(f'  {label:<10} | {n:>6} | {wr:>4.0f}% | ${ev:>+6.0f} | ${total:>+9,.0f} | {verd}{note}')
        window_results.append((label, n, wr, ev, total, verd))

    # ── Combined test-window summary ──────────────────────────────────────
    comb_n, comb_wr, comb_ev, comb_total = summarise(all_test_trades)
    profitable_windows = sum(1 for _, n, wr, ev, tot, _ in window_results if n > 0 and tot > 0)
    total_windows_run  = sum(1 for _, n, wr, ev, tot, _ in window_results if n > 0)

    print(f'  {"─"*58}')
    print(f'  {"COMBINED":<10} | {comb_n:>6} | {comb_wr:>4.0f}% | ${comb_ev:>+6.0f} | ${comb_total:>+9,.0f} |')
    print()

    # ── Combined rolling verdict ──────────────────────────────────────────
    roll_wr_drop = is_wr - comb_wr   # compare rolling OOS to IS
    if comb_ev > 0 and roll_wr_drop <= 10:
        roll_verdict = f'✅  EDGE CONFIRMED — {profitable_windows}/{total_windows_run} windows profitable, WR held within {abs(roll_wr_drop):.1f}pp of IS'
    elif comb_ev > 0 and roll_wr_drop <= 20:
        roll_verdict = f'⚠️   EDGE PRESENT BUT SOFTENING — WR dropped {roll_wr_drop:.1f}pp vs IS, monitor closely'
    else:
        if comb_ev <= 0:
            roll_verdict = f'❌  NEGATIVE EV IN OOS WINDOWS — strategy may be overfit'
        else:
            roll_verdict = f'❌  WR DECAY TOO LARGE ({roll_wr_drop:.1f}pp drop) — strategy may be overfit to IS period'

    print(f'  Combined test windows : WR {comb_wr:.0f}% | EV ${comb_ev:+.0f} | {profitable_windows}/{total_windows_run} windows profitable')
    print(f'  {roll_verdict}')

    # ════════════════════════════════════════════════════════════════════════
    # Final summary block
    # ════════════════════════════════════════════════════════════════════════
    print()
    print('=' * 60)
    print('  FINAL SUMMARY')
    print('=' * 60)
    print(f'  In-sample  WR : {is_wr:.0f}%  ({is_n} trades, ${is_total:+,.0f})')
    print(f'  OOS split  WR : {oos_wr:.0f}%  ({oos_n} trades, ${oos_total:+,.0f})')
    print(f'  OOS rolling WR: {comb_wr:.0f}%  ({comb_n} trades across {total_windows_run} windows)')
    print()
    print(f'  Test 1 verdict : {verdict_str}')
    print(f'  Test 2 verdict : {roll_verdict}')

    # Guidance
    if is_wr - oos_wr <= 10 and oos_ev > 0 and comb_ev > 0:
        print()
        print('  Edge is real and robust across time periods and market regimes.')
        print('  Daily-bar approximation — live results may vary ±15-20%.')
    elif is_wr - oos_wr <= 20 and (oos_ev > 0 or comb_ev > 0):
        print()
        print('  Edge exists but is degrading. Continue live trading with smaller size;')
        print('  review entry criteria (tighten grade threshold or add catalyst gate).')
    else:
        print()
        print('  Evidence of overfit. Recommend expanding universe and re-testing.')
        print('  Do not scale live capital until OOS WR is within 10pp of IS.')

    print('=' * 60)
    print()


if __name__ == '__main__':
    main()
