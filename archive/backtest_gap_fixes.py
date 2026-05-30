# backtest_gap_fixes.py — Test two structural gap fixes before applying to auto_trader
#
# GAP 2: Slow-bleed loser cut
#   Real rule: pnl < -1% AND max_gain_since_entry < 1.5% AND mins_held >= 90 → exit
#   Daily proxy: if High < entry*1.015 AND Low <= entry*0.99 → exit at entry*0.988
#   Logic: stock never showed momentum (High<+1.5%) AND went negative → cut early
#
# GAP 1: Profit dead-zone protection (+1% to +2.4% peak, no current mechanism)
#   Real rule: if pnl_pct >= 1.5% → pct_trail = session_high * 0.995; exit if price < pct_trail
#   Daily proxy: if High >= entry*1.015 → pct_trail = High * 0.995; if Close < pct_trail AND
#                pct_trail > entry → exit at pct_trail (fires before ATR trail in dead zone)
#
# Run: venv/bin/python backtest_gap_fixes.py
#      venv/bin/python backtest_gap_fixes.py CLS UUUU CCJ AXON

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date
import sys
import warnings
warnings.filterwarnings('ignore')

# ── Config ─────────────────────────────────────────────────────────────────
START_DATE        = '2020-01-01'
END_DATE          = date.today().isoformat()
SYMBOLS           = sys.argv[1:] if len(sys.argv) > 1 else [
    'CLS','UUUU','CCJ','AXON','MRVL','SHOP','APP','RKLB','NIO'
]
CAPITAL_PER_TRADE = 2000
ATR_PERIOD        = 14
STOP_PCT          = 5.0
ATR_TRAIL_MULT    = 1.5
ATR_FADE_MULT     = 1.0
MIN_RR            = 2.5
MIN_VOLUME_RATIO  = 1.3
MIN_TODAY_GAIN    = 1.5
SKIP_WEAK_DAYS    = True
FIRST_BAR_QUALITY = True

# Gap fix thresholds
GAP2_MAX_GAIN_PCT = 1.5   # stock must have peaked below this to trigger cut
GAP2_LOSS_PCT     = 1.0   # must be at least -1% to trigger cut
GAP2_EXIT_PCT     = 1.2   # exit at -1.2% (90-min slow bleed approximation)
GAP1_ACTIVATE_PCT = 1.5   # % trail activates when stock hits +1.5%
GAP1_TRAIL_PCT    = 0.5   # trail is 0.5% below session high


# ── Infrastructure (same as backtest_strategy.py) ───────────────────────────
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
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(ATR_PERIOD).mean()
    return df


def grade_day(i, df, spy_chg, regime):
    row      = df.iloc[i]
    prev_row = df.iloc[i - 1]
    df_upto  = df.iloc[:i + 1]

    price = row['Open']
    atr   = row['atr']
    if pd.isna(atr) or atr <= 0 or pd.isna(price) or price < 5:
        return 'SKIP', 0, [], False

    if SKIP_WEAK_DAYS and regime == 'WEAK':
        return 'SKIP', 0, [], False

    ma20 = df_upto['Close'].rolling(20).mean().iloc[-1]
    if pd.isna(ma20) or price < ma20:
        return 'SKIP', 0, [], False

    today_gain = (row['Close'] - prev_row['Close']) / prev_row['Close'] * 100
    if today_gain < MIN_TODAY_GAIN:
        return 'SKIP', 0, [], False

    avg_vol   = df_upto['Volume'].rolling(20).mean().iloc[-2] if len(df_upto) >= 21 else None
    vol_ratio = float(row['Volume'] / avg_vol) if avg_vol and avg_vol > 0 else 1.0
    if vol_ratio < MIN_VOLUME_RATIO:
        return 'SKIP', 0, [], False

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
        return 'SKIP', 0, [], False

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
    if 45 <= rsi <= 65:   score += 20; reasons.append(f'RSI {rsi:.0f}')
    elif 65 < rsi <= 75:  reasons.append(f'RSI {rsi:.0f} elevated')
    elif 75 < rsi <= 80:  score += 5;  reasons.append(f'RSI {rsi:.0f} strong')
    else:                 score += 5

    if today_gain >= 5.0:    score += 30; reasons.append(f'+{today_gain:.1f}%')
    elif today_gain >= 3.0:  score += 20; reasons.append(f'+{today_gain:.1f}%')
    elif today_gain >= 1.5:  score += 10; reasons.append(f'+{today_gain:.1f}%')

    if regime == 'STRONG':   score += 15; reasons.append('Strong mkt')
    elif regime == 'NORMAL': score += 5

    score += 5
    grade = 'A+' if score >= 80 else 'A' if score >= 65 else 'B' if score >= 50 else 'C'

    if grade in ('B', 'C'):
        return 'SKIP', score, reasons, False
    if spy_chg < 0 and grade != 'A+':
        return 'SKIP', score, reasons, False

    vol_avg20        = df_upto['Volume'].rolling(20).mean().iloc[-2]
    first_bar_strong = (
        FIRST_BAR_QUALITY
        and gap_pct > 1.0
        and not pd.isna(vol_avg20) and vol_avg20 > 0
        and row['Volume'] > vol_avg20 * 1.3
    )
    return grade, score, reasons, first_bar_strong


# ── Three variants of simulate_trade ─────────────────────────────────────────

def simulate_baseline(row, atr, first_bar_strong=False):
    """Exact replica of current backtest_strategy.py logic."""
    capital    = CAPITAL_PER_TRADE * 1.15 if (FIRST_BAR_QUALITY and first_bar_strong) else CAPITAL_PER_TRADE
    do_partial = (not FIRST_BAR_QUALITY) or first_bar_strong

    entry  = row['Open']
    sl     = round(entry * (1 - STOP_PCT / 100), 2)
    one_r  = entry * (1 + STOP_PCT / 100)
    hit_1r = row['High'] >= one_r

    half_cap       = capital / 2
    partial_locked = (STOP_PCT / 100 * half_cap) if (hit_1r and do_partial) else 0.0
    rem_cap        = (half_cap if (hit_1r and do_partial) else capital)

    if row['Low'] <= sl:
        if hit_1r and do_partial:
            rest_loss = -STOP_PCT / 100 * rem_cap
            total_pnl = round(partial_locked + rest_loss, 2)
            return 'PARTIAL_STOP', round(total_pnl / capital * 100, 2), total_pnl, sl
        return 'STOP', round(-STOP_PCT, 2), round(-STOP_PCT * capital / 100, 2), sl

    trail_stop = row['High'] - ATR_TRAIL_MULT * atr
    if row['Close'] < trail_stop:
        rest_pnl  = (trail_stop - entry) / entry * rem_cap
        total_pnl = round(partial_locked + rest_pnl, 2)
        result    = 'WIN' if total_pnl > 0 else 'FADE'
        return result, round(total_pnl / capital * 100, 2), total_pnl, trail_stop

    fade_stop = row['High'] - ATR_FADE_MULT * atr
    if row['Close'] < fade_stop:
        rest_pnl  = (row['Close'] - entry) / entry * rem_cap
        total_pnl = partial_locked + rest_pnl
        if total_pnl > 0:
            return 'FADE_EXIT', round(total_pnl / capital * 100, 2), round(total_pnl, 2), row['Close']

    rest_pnl  = (row['Close'] - entry) / entry * rem_cap
    total_pnl = round(partial_locked + rest_pnl, 2)
    result    = 'WIN' if total_pnl > 0 else 'LOSS'
    return result, round(total_pnl / capital * 100, 2), total_pnl, row['Close']


def simulate_gap2(row, atr, first_bar_strong=False):
    """
    Gap 2 fix: slow-bleed loser cut.
    If stock never showed momentum (High < entry+1.5%) AND went negative (Low <= entry-1%)
    → exit early at entry-1.2% instead of holding to EOD.
    Daily proxy conservatively pegs exit at -1.2% (vs EOD which often goes to -3 to -5%).
    """
    capital    = CAPITAL_PER_TRADE * 1.15 if (FIRST_BAR_QUALITY and first_bar_strong) else CAPITAL_PER_TRADE
    do_partial = (not FIRST_BAR_QUALITY) or first_bar_strong

    entry  = row['Open']
    sl     = round(entry * (1 - STOP_PCT / 100), 2)
    one_r  = entry * (1 + STOP_PCT / 100)
    hit_1r = row['High'] >= one_r

    half_cap       = capital / 2
    partial_locked = (STOP_PCT / 100 * half_cap) if (hit_1r and do_partial) else 0.0
    rem_cap        = (half_cap if (hit_1r and do_partial) else capital)

    if row['Low'] <= sl:
        if hit_1r and do_partial:
            rest_loss = -STOP_PCT / 100 * rem_cap
            total_pnl = round(partial_locked + rest_loss, 2)
            return 'PARTIAL_STOP', round(total_pnl / capital * 100, 2), total_pnl, sl
        return 'STOP', round(-STOP_PCT, 2), round(-STOP_PCT * capital / 100, 2), sl

    # ── Gap 2: cut slow-bleed losers ──────────────────────────────────────
    no_momentum = row['High'] < entry * (1 + GAP2_MAX_GAIN_PCT / 100)
    went_neg    = row['Low']  <= entry * (1 - GAP2_LOSS_PCT / 100)
    if no_momentum and went_neg:
        early_exit = entry * (1 - GAP2_EXIT_PCT / 100)
        rest_pnl   = (early_exit - entry) / entry * rem_cap
        total_pnl  = round(partial_locked + rest_pnl, 2)
        return 'GAP2_CUT', round(total_pnl / capital * 100, 2), total_pnl, early_exit

    trail_stop = row['High'] - ATR_TRAIL_MULT * atr
    if row['Close'] < trail_stop:
        rest_pnl  = (trail_stop - entry) / entry * rem_cap
        total_pnl = round(partial_locked + rest_pnl, 2)
        result    = 'WIN' if total_pnl > 0 else 'FADE'
        return result, round(total_pnl / capital * 100, 2), total_pnl, trail_stop

    fade_stop = row['High'] - ATR_FADE_MULT * atr
    if row['Close'] < fade_stop:
        rest_pnl  = (row['Close'] - entry) / entry * rem_cap
        total_pnl = partial_locked + rest_pnl
        if total_pnl > 0:
            return 'FADE_EXIT', round(total_pnl / capital * 100, 2), round(total_pnl, 2), row['Close']

    rest_pnl  = (row['Close'] - entry) / entry * rem_cap
    total_pnl = round(partial_locked + rest_pnl, 2)
    result    = 'WIN' if total_pnl > 0 else 'LOSS'
    return result, round(total_pnl / capital * 100, 2), total_pnl, row['Close']


def simulate_gap1(row, atr, first_bar_strong=False):
    """
    Gap 1 fix: % trail protecting +1% to +2.4% peak range (dead zone).
    If stock reaches +1.5%, trail activates at session_high * 0.995.
    This fires BEFORE ATR trail in the dead zone (ATR trail needs +5-8%).
    Winners that peak at +6%+ are unaffected — ATR trail fires first.
    """
    capital    = CAPITAL_PER_TRADE * 1.15 if (FIRST_BAR_QUALITY and first_bar_strong) else CAPITAL_PER_TRADE
    do_partial = (not FIRST_BAR_QUALITY) or first_bar_strong

    entry  = row['Open']
    sl     = round(entry * (1 - STOP_PCT / 100), 2)
    one_r  = entry * (1 + STOP_PCT / 100)
    hit_1r = row['High'] >= one_r

    half_cap       = capital / 2
    partial_locked = (STOP_PCT / 100 * half_cap) if (hit_1r and do_partial) else 0.0
    rem_cap        = (half_cap if (hit_1r and do_partial) else capital)

    if row['Low'] <= sl:
        if hit_1r and do_partial:
            rest_loss = -STOP_PCT / 100 * rem_cap
            total_pnl = round(partial_locked + rest_loss, 2)
            return 'PARTIAL_STOP', round(total_pnl / capital * 100, 2), total_pnl, sl
        return 'STOP', round(-STOP_PCT, 2), round(-STOP_PCT * capital / 100, 2), sl

    # ── Gap 1: % trail in the dead zone ───────────────────────────────────
    pct_activate = entry * (1 + GAP1_ACTIVATE_PCT / 100)
    if row['High'] >= pct_activate:
        pct_trail = row['High'] * (1 - GAP1_TRAIL_PCT / 100)
        # Only fire if trail is above entry (locks in a gain, not a loss)
        if pct_trail > entry and row['Close'] < pct_trail:
            # Check if ATR trail would fire at a HIGHER price (take the better exit)
            atr_trail = row['High'] - ATR_TRAIL_MULT * atr
            exit_price = max(pct_trail, atr_trail) if row['Close'] < atr_trail else pct_trail
            rest_pnl  = (exit_price - entry) / entry * rem_cap
            total_pnl = round(partial_locked + rest_pnl, 2)
            result    = 'GAP1_TRAIL' if exit_price == pct_trail else 'WIN'
            return result, round(total_pnl / capital * 100, 2), total_pnl, exit_price

    trail_stop = row['High'] - ATR_TRAIL_MULT * atr
    if row['Close'] < trail_stop:
        rest_pnl  = (trail_stop - entry) / entry * rem_cap
        total_pnl = round(partial_locked + rest_pnl, 2)
        result    = 'WIN' if total_pnl > 0 else 'FADE'
        return result, round(total_pnl / capital * 100, 2), total_pnl, trail_stop

    fade_stop = row['High'] - ATR_FADE_MULT * atr
    if row['Close'] < fade_stop:
        rest_pnl  = (row['Close'] - entry) / entry * rem_cap
        total_pnl = partial_locked + rest_pnl
        if total_pnl > 0:
            return 'FADE_EXIT', round(total_pnl / capital * 100, 2), round(total_pnl, 2), row['Close']

    rest_pnl  = (row['Close'] - entry) / entry * rem_cap
    total_pnl = round(partial_locked + rest_pnl, 2)
    result    = 'WIN' if total_pnl > 0 else 'LOSS'
    return result, round(total_pnl / capital * 100, 2), total_pnl, row['Close']


def simulate_both(row, atr, first_bar_strong=False):
    """Both Gap 1 and Gap 2 fixes combined."""
    capital    = CAPITAL_PER_TRADE * 1.15 if (FIRST_BAR_QUALITY and first_bar_strong) else CAPITAL_PER_TRADE
    do_partial = (not FIRST_BAR_QUALITY) or first_bar_strong

    entry  = row['Open']
    sl     = round(entry * (1 - STOP_PCT / 100), 2)
    one_r  = entry * (1 + STOP_PCT / 100)
    hit_1r = row['High'] >= one_r

    half_cap       = capital / 2
    partial_locked = (STOP_PCT / 100 * half_cap) if (hit_1r and do_partial) else 0.0
    rem_cap        = (half_cap if (hit_1r and do_partial) else capital)

    if row['Low'] <= sl:
        if hit_1r and do_partial:
            rest_loss = -STOP_PCT / 100 * rem_cap
            total_pnl = round(partial_locked + rest_loss, 2)
            return 'PARTIAL_STOP', round(total_pnl / capital * 100, 2), total_pnl, sl
        return 'STOP', round(-STOP_PCT, 2), round(-STOP_PCT * capital / 100, 2), sl

    # Gap 2: slow-bleed cut (fires first — if no momentum + negative, exit early)
    no_momentum = row['High'] < entry * (1 + GAP2_MAX_GAIN_PCT / 100)
    went_neg    = row['Low']  <= entry * (1 - GAP2_LOSS_PCT / 100)
    if no_momentum and went_neg:
        early_exit = entry * (1 - GAP2_EXIT_PCT / 100)
        rest_pnl   = (early_exit - entry) / entry * rem_cap
        total_pnl  = round(partial_locked + rest_pnl, 2)
        return 'GAP2_CUT', round(total_pnl / capital * 100, 2), total_pnl, early_exit

    # Gap 1: % trail in the dead zone
    pct_activate = entry * (1 + GAP1_ACTIVATE_PCT / 100)
    if row['High'] >= pct_activate:
        pct_trail = row['High'] * (1 - GAP1_TRAIL_PCT / 100)
        if pct_trail > entry and row['Close'] < pct_trail:
            atr_trail  = row['High'] - ATR_TRAIL_MULT * atr
            exit_price = max(pct_trail, atr_trail) if row['Close'] < atr_trail else pct_trail
            rest_pnl   = (exit_price - entry) / entry * rem_cap
            total_pnl  = round(partial_locked + rest_pnl, 2)
            result     = 'GAP1_TRAIL' if exit_price == pct_trail else 'WIN'
            return result, round(total_pnl / capital * 100, 2), total_pnl, exit_price

    trail_stop = row['High'] - ATR_TRAIL_MULT * atr
    if row['Close'] < trail_stop:
        rest_pnl  = (trail_stop - entry) / entry * rem_cap
        total_pnl = round(partial_locked + rest_pnl, 2)
        result    = 'WIN' if total_pnl > 0 else 'FADE'
        return result, round(total_pnl / capital * 100, 2), total_pnl, trail_stop

    fade_stop = row['High'] - ATR_FADE_MULT * atr
    if row['Close'] < fade_stop:
        rest_pnl  = (row['Close'] - entry) / entry * rem_cap
        total_pnl = partial_locked + rest_pnl
        if total_pnl > 0:
            return 'FADE_EXIT', round(total_pnl / capital * 100, 2), round(total_pnl, 2), row['Close']

    rest_pnl  = (row['Close'] - entry) / entry * rem_cap
    total_pnl = round(partial_locked + rest_pnl, 2)
    result    = 'WIN' if total_pnl > 0 else 'LOSS'
    return result, round(total_pnl / capital * 100, 2), total_pnl, row['Close']


# ── Backtest one symbol, all four scenarios ──────────────────────────────────
def backtest_symbol(symbol, spy_regime):
    df = yf.download(symbol, start=START_DATE, end=END_DATE, progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if len(df) < 60:
        print(f"  {symbol}: insufficient data"); return None

    df     = add_atr(df)
    merged = df.join(spy_regime, how='left')
    merged['spy_chg'] = merged['spy_chg'].fillna(0)
    merged['regime']  = merged['regime'].fillna('NORMAL')

    rows = []
    for i in range(22, len(merged)):
        row     = merged.iloc[i]
        spy_chg = float(row['spy_chg'])
        regime  = str(row['regime'])

        grade, score, reasons, first_bar_strong = grade_day(i, merged, spy_chg, regime)
        if grade == 'SKIP':
            continue

        atr = float(row['atr'])
        r_base, pp_base, p_base, ex_base = simulate_baseline(row, atr, first_bar_strong)
        r_g2,   pp_g2,   p_g2,   ex_g2  = simulate_gap2(row, atr, first_bar_strong)
        r_g1,   pp_g1,   p_g1,   ex_g1  = simulate_gap1(row, atr, first_bar_strong)
        r_both, pp_both, p_both, ex_both = simulate_both(row, atr, first_bar_strong)

        # Classify what happened in dead zone (for analysis)
        high_gain_pct = (row['High'] - row['Open']) / row['Open'] * 100
        low_pnl_pct   = (row['Low']  - row['Open']) / row['Open'] * 100

        rows.append({
            'date':           merged.index[i].strftime('%Y-%m-%d'),
            'year':           merged.index[i].year,
            'symbol':         symbol,
            'grade':          grade,
            'entry':          round(row['Open'], 2),
            'high_gain_pct':  round(high_gain_pct, 2),
            'low_pnl_pct':    round(low_pnl_pct, 2),
            'atr':            round(atr, 2),
            # Baseline
            'base_result':    r_base,
            'base_pnl':       p_base,
            # Gap 2 only
            'g2_result':      r_g2,
            'g2_pnl':         p_g2,
            # Gap 1 only
            'g1_result':      r_g1,
            'g1_pnl':         p_g1,
            # Both
            'both_result':    r_both,
            'both_pnl':       p_both,
        })

    return pd.DataFrame(rows) if rows else None


# ── Summary stats helper ─────────────────────────────────────────────────────
def stats(df, col_pnl, col_result):
    n    = len(df)
    wins = df[df[col_pnl] > 0]
    wr   = len(wins) / n * 100 if n else 0
    tot  = df[col_pnl].sum()
    avg  = df[col_pnl].mean()
    return n, wr, tot, avg


# ── Print comparison report ──────────────────────────────────────────────────
def print_report(df):
    n_years = max(1, (pd.to_datetime(df['date'].max()) -
                      pd.to_datetime(df['date'].min())).days / 365)

    n, wr_b, tot_b, avg_b = stats(df, 'base_pnl', 'base_result')
    _,  wr_g2, tot_g2, avg_g2 = stats(df, 'g2_pnl', 'g2_result')
    _,  wr_g1, tot_g1, avg_g1 = stats(df, 'g1_pnl', 'g1_result')
    _,  wr_bo, tot_bo, avg_bo = stats(df, 'both_pnl', 'both_result')

    print(f"\n{'='*72}")
    print(f"  GAP FIX BACKTEST  {START_DATE} → {END_DATE}")
    print(f"  Symbols : {', '.join(SYMBOLS)}")
    print(f"  Trades  : {n}  |  {n_years:.1f} years  |  {n/n_years:.0f} trades/year")
    print(f"{'='*72}")

    print(f"\n  {'Scenario':<22} {'WR':>6} {'Avg/trade':>10} {'Total P&L':>12} {'Ann P&L':>10}  vs Baseline")
    print(f"  {'─'*65}")

    def row_str(label, wr, avg, tot, is_base=False):
        ann = tot / n_years
        diff_ann = '' if is_base else f'  {(tot-tot_b)/n_years:+,.0f}/yr'
        diff_avg = '' if is_base else f'  ({avg-avg_b:+.2f}/trade)'
        print(f"  {label:<22} {wr:>5.1f}% {avg:>+9.2f} {tot:>+12,.0f} {ann:>+10,.0f}{diff_ann}{diff_avg}")

    row_str('Baseline (current)',   wr_b,  avg_b,  tot_b,  True)
    row_str('+ Gap 2 (slow-bleed)', wr_g2, avg_g2, tot_g2)
    row_str('+ Gap 1 (% trail)',    wr_g1, avg_g1, tot_g1)
    row_str('Both fixes combined',  wr_bo, avg_bo, tot_bo)

    # ── Gap 2 deep-dive ─────────────────────────────────────────────────────
    print(f"\n  GAP 2 DEEP-DIVE  (slow-bleed losers cut early)")
    print(f"  {'─'*65}")
    triggered = df[df['g2_result'] == 'GAP2_CUT']
    not_trig  = df[df['g2_result'] != 'GAP2_CUT']

    # How many of those triggered were actual losses in baseline?
    if len(triggered):
        actual_loss  = triggered[triggered['base_pnl'] <= 0]
        actual_win   = triggered[triggered['base_pnl'] > 0]
        avg_base_cut = triggered['base_pnl'].mean()
        avg_g2_cut   = triggered['g2_pnl'].mean()
        saved        = triggered['g2_pnl'].sum() - triggered['base_pnl'].sum()
        print(f"  Trades where Gap2 fired   : {len(triggered)}")
        print(f"  → Were actual losses (good cuts) : {len(actual_loss)}  ({len(actual_loss)/len(triggered)*100:.0f}%)")
        print(f"  → Were actual winners (false pos): {len(actual_win)}   ({len(actual_win)/len(triggered)*100:.0f}%)")
        print(f"  Avg baseline P&L when Gap2 fires : ${avg_base_cut:+.2f}")
        print(f"  Avg Gap2 exit P&L                : ${avg_g2_cut:+.2f}")
        print(f"  Net P&L saved vs baseline        : ${saved:+,.0f} over {n_years:.1f} yrs  (${saved/n_years:+,.0f}/yr)")

        # False positive cost: winners killed by Gap2
        if len(actual_win):
            fp_cost = actual_win['g2_pnl'].sum() - actual_win['base_pnl'].sum()
            print(f"  False positive cost              : ${fp_cost:+,.0f} (winners exited early)")
    else:
        print(f"  Gap2 never triggered (check thresholds)")

    # ── Gap 1 deep-dive ─────────────────────────────────────────────────────
    print(f"\n  GAP 1 DEEP-DIVE  (% trail protecting +1.5% to +{ATR_TRAIL_MULT*5:.0f}% peak range)")
    print(f"  {'─'*65}")
    trig_g1 = df[df['g1_result'] == 'GAP1_TRAIL']
    if len(trig_g1):
        actual_loss_g1 = trig_g1[trig_g1['base_pnl'] <= 0]
        actual_win_g1  = trig_g1[trig_g1['base_pnl'] > 0]
        saved_g1       = trig_g1['g1_pnl'].sum() - trig_g1['base_pnl'].sum()
        avg_exit_high  = trig_g1['high_gain_pct'].mean()
        print(f"  Trades where Gap1 trail fired : {len(trig_g1)}")
        print(f"  → Saved from loss (good)      : {len(actual_loss_g1)}  ({len(actual_loss_g1)/len(trig_g1)*100:.0f}%)")
        print(f"  → Exited winning trade early  : {len(actual_win_g1)}  ({len(actual_win_g1)/len(trig_g1)*100:.0f}%)")
        print(f"  Avg peak gain when trail fired : {avg_exit_high:+.1f}%")
        print(f"  Avg baseline P&L when fires    : ${trig_g1['base_pnl'].mean():+.2f}")
        print(f"  Avg Gap1 exit P&L              : ${trig_g1['g1_pnl'].mean():+.2f}")
        print(f"  Net P&L change                 : ${saved_g1:+,.0f} over {n_years:.1f} yrs  (${saved_g1/n_years:+,.0f}/yr)")

        # Verify: trades that peaked at >8% (ATR zone) — Gap1 should not affect them
        big_movers = trig_g1[trig_g1['high_gain_pct'] > 8.0]
        print(f"  Big movers (>8%) hit by Gap1   : {len(big_movers)}  (should be 0 — ATR fires first)")
    else:
        print(f"  Gap1 trail never triggered (check thresholds)")

    # Dead zone hit rate (baseline): how often do trades peak in 1.5%-5% range?
    dead_zone = df[(df['high_gain_pct'] >= 1.5) & (df['high_gain_pct'] < 5.0)]
    print(f"\n  Dead-zone trades (peaked +1.5% to +5%) in baseline: {len(dead_zone)} / {n} ({len(dead_zone)/n*100:.0f}%)")
    if len(dead_zone):
        dz_losses = dead_zone[dead_zone['base_pnl'] <= 0]
        print(f"  Of those, lost money at EOD    : {len(dz_losses)} ({len(dz_losses)/len(dead_zone)*100:.0f}%)  — these are the rescue targets")
        print(f"  Avg EOD P&L in dead zone       : ${dead_zone['base_pnl'].mean():+.2f}")
        print(f"  Avg Gap1 P&L in dead zone      : ${dead_zone['g1_pnl'].mean():+.2f}")

    # ── By year ─────────────────────────────────────────────────────────────
    print(f"\n  BY YEAR — P&L comparison:")
    print(f"  {'Year':<6} {'Base':>10} {'+ Gap2':>10} {'+ Gap1':>10} {'Both':>10}  G2 lift   G1 lift")
    print(f"  {'─'*65}")
    for yr in sorted(df['year'].unique()):
        sub = df[df['year'] == yr]
        b   = sub['base_pnl'].sum()
        g2  = sub['g2_pnl'].sum()
        g1  = sub['g1_pnl'].sum()
        bo  = sub['both_pnl'].sum()
        print(f"  {yr:<6} {b:>+10,.0f} {g2:>+10,.0f} {g1:>+10,.0f} {bo:>+10,.0f}  {g2-b:>+7,.0f}  {g1-b:>+7,.0f}")

    # ── By symbol ────────────────────────────────────────────────────────────
    print(f"\n  BY SYMBOL — combined fix impact:")
    print(f"  {'Sym':<6} {'N':>5} {'Base WR':>8} {'Base Avg':>9} {'Both WR':>8} {'Both Avg':>9} {'Lift/yr':>9}")
    print(f"  {'─'*65}")
    for sym in SYMBOLS:
        sub = df[df['symbol'] == sym]
        if sub.empty: continue
        nb, wrb, totb, avgb = stats(sub, 'base_pnl', 'base_result')
        _,  wrbo, totbo, avgbo = stats(sub, 'both_pnl', 'both_result')
        yrs = max(1, (pd.to_datetime(sub['date'].max()) -
                      pd.to_datetime(sub['date'].min())).days / 365)
        lift_yr = (totbo - totb) / yrs
        print(f"  {sym:<6} {nb:>5} {wrb:>7.0f}% {avgb:>+9.2f} {wrbo:>7.0f}% {avgbo:>+9.2f} {lift_yr:>+9,.0f}")

    # ── Verdict ─────────────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print(f"  VERDICT")
    print(f"{'='*72}")

    g2_lift_yr  = (tot_g2 - tot_b) / n_years
    g1_lift_yr  = (tot_g1 - tot_b) / n_years
    bo_lift_yr  = (tot_bo - tot_b) / n_years
    g2_avg_lift = avg_g2 - avg_b
    g1_avg_lift = avg_g1 - avg_b

    print(f"\n  Gap 2 (slow-bleed cut):")
    if g2_lift_yr > 50:
        print(f"  ✅  +${g2_lift_yr:,.0f}/yr  ({g2_avg_lift:+.2f}/trade)  — structural improvement, IMPLEMENT")
    elif g2_lift_yr > 0:
        print(f"  ⚠️  +${g2_lift_yr:,.0f}/yr  ({g2_avg_lift:+.2f}/trade)  — marginal, monitor more")
    else:
        print(f"  ❌  ${g2_lift_yr:,.0f}/yr — hurts more than helps, adjust thresholds")

    print(f"\n  Gap 1 (% trail):")
    if g1_lift_yr > 50:
        print(f"  ✅  +${g1_lift_yr:,.0f}/yr  ({g1_avg_lift:+.2f}/trade)  — structural improvement, IMPLEMENT")
    elif g1_lift_yr > 0:
        print(f"  ⚠️  +${g1_lift_yr:,.0f}/yr  ({g1_avg_lift:+.2f}/trade)  — marginal, monitor more")
    else:
        print(f"  ❌  ${g1_lift_yr:,.0f}/yr — hurts more than helps, adjust thresholds")

    print(f"\n  Combined:")
    if bo_lift_yr > 100:
        print(f"  ✅  +${bo_lift_yr:,.0f}/yr  — additive lift, both fixes validated")
    else:
        print(f"  ⚠️  +${bo_lift_yr:,.0f}/yr  — review per-fix contributions above")

    print(f"\n  Note: Daily-bar proxy ±20% vs intraday. Gap2 exit at fixed -1.2% is conservative")
    print(f"  (real rule exits closer to -1%). Gap1 may over-fire on some EOD recoveries.")
    print(f"{'='*72}\n")


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print(f"\nLoading SPY regime {START_DATE} → {END_DATE}...")
    spy_regime = build_spy_regime(START_DATE, END_DATE)

    all_dfs = []
    for sym in SYMBOLS:
        print(f"  {sym}...", end=' ', flush=True)
        df_sym = backtest_symbol(sym, spy_regime)
        if df_sym is not None and not df_sym.empty:
            all_dfs.append(df_sym)
            nb, wrb, totb, avgb = stats(df_sym, 'base_pnl', 'base_result')
            print(f"{nb} trades | baseline WR {wrb:.0f}% | P&L ${totb:+,.0f}")
        else:
            print("no qualifying trades")

    if all_dfs:
        df_all = pd.concat(all_dfs, ignore_index=True).sort_values('date')
        print_report(df_all)
    else:
        print("No trades found.")
