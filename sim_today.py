# sim_today.py — replay today's intraday data through auto_trader signal logic
# Answers: would we have entered MSFT/NVDA today, at what price, and what P&L?
# Command: venv/bin/python sim_today.py
#          venv/bin/python sim_today.py MSFT NVDA AAPL

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, date
import pytz
import sys

ET = pytz.timezone('America/New_York')

# ── Settings (mirror auto_trader.py) ─────────────────────────────────────────
ATR_PERIOD        = 14
ATR_STOP_MULT     = 2.0
ATR_TRAIL_MULT    = 1.5
ATR_FADE_MULT     = 1.0
MIN_RR            = 2.5
MAX_RISK_PCT      = 8.0
MIN_VOLUME_RATIO  = 1.3
MAX_RSI_5M        = 85
MIN_TODAY_GAIN    = 1.5
NO_ENTRY_BEFORE   = 10
NO_ENTRY_AFTER    = 15
LUNCH_AVOID_START = (11, 30)
LUNCH_AVOID_END   = (13, 30)
ORB_ENTRY_CUTOFF  = (11, 30)
EOD_CLOSE_HOUR    = 15
EOD_CLOSE_MINUTE  = 45
CAPITAL_PER_TRADE = 1000   # base; grade_capital() overrides per-trade
MAX_LOSS_PER_TRADE = 100   # $100 risk per trade (5% of $2000 position)

# ── Tunable exit parameters (patched by sim_tune.py for A/B testing) ─────────
# Validated Apr 26 via 3-week A/B: these beat baseline in every week
NO_MOVE_MINUTES   = 240    # min hold before no-move exit fires (150→240 validated Apr 26 A/B: +$81/2wk)
NO_MOVE_UPPER_PCT = 2.0    # no-move fires if pnl ≤ this % (was 0.8%)
BE_TRIGGER_PCT    = 2.5    # set break-even stop once profit reaches this % (was 0.5%)
PARTIAL_EXIT      = True   # take 50% off at 1R (5% gain), ride rest with trail

def grade_capital(grade, has_catalyst=False):
    """Mirror auto_trader get_position_capital — dynamic sizing by grade."""
    if grade == 'A+' and has_catalyst: return 2000
    if grade == 'A+':                  return 1800
    if grade == 'A'  and has_catalyst: return 1600
    return 1400

SYMBOLS = sys.argv[1:] if len(sys.argv) > 1 else ['MSFT', 'NVDA']

# ── Sector ETF map for scoring ────────────────────────────────────────────────
SECTOR_ETF_MAP = {
    'NVDA': 'XLK', 'MSFT': 'XLK', 'AAPL': 'XLK', 'AMD':  'XLK', 'AVGO': 'XLK',
    'CRM':  'XLK', 'SMCI': 'XLK', 'CRWV': 'XLK', 'BBAI': 'XLK', 'POET': 'XLK',
    'IONQ': 'XLK', 'SOUN': 'XLK', 'PLTR': 'XLK', 'META': 'XLC', 'AMZN': 'XLY',
    'TSLA': 'XLY', 'HOOD': 'XLF', 'EOSE': 'XLE', 'RKLB': 'XLI', 'OKLO': 'XLU',
}

# ── Tunable constants (also patched by sim_tune.py) ──────────────────────────
BLOCK_CAUTIOUS = True    # treat CAUTIOUS like WEAK — block new entries (validated Apr 26)

# Early catalyst entry — bypass 10am / 75-bar guards for high-conviction gap plays
EARLY_ENTRY_ENABLED = False  # allow entry from 9:35am on catalyst gap stocks (tested Apr 26 — failed, gap-and-crap risk outweighs upside)
EARLY_ENTRY_GAP_PCT = 6.0    # minimum pre-market gap % to qualify
EARLY_ENTRY_VOL_MIN = 3.0    # minimum annualized volume ratio at time of entry

# Use last available trading day (handles weekends / holidays transparently)
def last_trading_date():
    spy = yf.Ticker('SPY').history(period='5d', interval='5m')
    if spy.empty:
        return datetime.now(ET).date()
    spy.index = spy.index.tz_convert(ET)
    return spy.index[-1].date()

SIM_DATE = last_trading_date()

def _intraday_period():
    """Shortest yfinance period string that reaches SIM_DATE's 5m bars.
    yfinance max for 5m interval is 60 days."""
    days_back = (date.today() - SIM_DATE).days + 4   # +4 safety margin
    if days_back <= 5:  return '5d'
    if days_back <= 30: return '30d'
    return '60d'


def get_regime():
    try:
        p = _intraday_period()
        spy_raw = yf.Ticker('SPY').history(period=p, interval='5m')
        spy_raw.index = spy_raw.index.tz_convert(ET)
        spy5 = spy_raw[spy_raw.index.date == SIM_DATE]

        spyd = yf.Ticker('SPY').history(period='60d')
        vix_raw = yf.Ticker('^VIX').history(period=p, interval='5m')
        vix_raw.index = vix_raw.index.tz_convert(ET)
        vix5 = vix_raw[vix_raw.index.date == SIM_DATE]
        qqq_raw = yf.Ticker('QQQ').history(period=p, interval='5m')
        qqq_raw.index = qqq_raw.index.tz_convert(ET)
        qqq5 = qqq_raw[qqq_raw.index.date == SIM_DATE]

        spy_now  = float(spy5['Close'].iloc[-1])
        # prev close = last daily bar BEFORE SIM_DATE
        spyd_before = spyd[spyd.index.date < SIM_DATE]
        spy_prev = float(spyd_before['Close'].iloc[-1]) if not spyd_before.empty else float(spyd['Close'].iloc[-2])
        spy_chg  = (spy_now - spy_prev) / spy_prev * 100
        vix_val  = float(vix5['Close'].iloc[-1])

        tp   = (spy5['High'] + spy5['Low'] + spy5['Close']) / 3
        vwap = float((tp * spy5['Volume']).cumsum().iloc[-1] / spy5['Volume'].cumsum().iloc[-1])
        above_vwap = spy_now > vwap

        vix_rising = (len(vix5) >= 6 and
                      float(vix5['Close'].iloc[-1]) > float(vix5['Close'].iloc[-6]))

        qqq_leading = True
        if len(qqq5) >= 2:
            qqq_chg  = (float(qqq5['Close'].iloc[-1]) - float(qqq5['Open'].iloc[0])) / float(qqq5['Open'].iloc[0]) * 100
            spy_ichg = (spy_now - float(spy5['Open'].iloc[0])) / float(spy5['Open'].iloc[0]) * 100
            qqq_leading = qqq_chg >= spy_ichg - 0.3

        chop = False
        if len(spy5) >= 6:
            diffs = spy5['Close'].diff().dropna()
            revs  = sum(1 for i in range(1, len(diffs)) if diffs.iloc[i] * diffs.iloc[i-1] < 0)
            chop  = revs / len(diffs) > 0.4 and abs(spy_chg) < 0.3

        if chop:
            regime = 'CHOPPY'
        elif spy_chg < -0.5 or vix_val > 28:
            regime = 'WEAK'
        elif spy_chg >= 0.5 and vix_val < 22:
            regime = 'STRONG'
        elif spy_chg >= 0 and vix_val < 25:
            regime = 'NORMAL'
        else:
            regime = 'CAUTIOUS'

        order = ['STRONG', 'NORMAL', 'CAUTIOUS', 'WEAK']
        if regime not in ('CHOPPY', 'WEAK'):
            if not above_vwap:
                regime = order[min(order.index(regime) + 1, len(order) - 1)]
            if vix_rising:
                regime = order[min(order.index(regime) + 1, len(order) - 1)]
            if not qqq_leading and regime == 'STRONG':
                regime = 'NORMAL'

        return regime, round(spy_chg, 2), round(vix_val, 2)

    except Exception as e:
        print(f"  Regime error: {e}")
        return 'NORMAL', 0.0, 18.0


def atr_from_daily(df):
    h, l, c = df['High'], df['Low'], df['Close']
    tr  = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    val = tr.rolling(ATR_PERIOD).mean().iloc[-1]
    return round(float(val), 4) if not np.isnan(val) else None


def calc_sl_target(price, daily_df, atr):
    sl      = round(price * 0.95, 2)          # 5% fixed stop — $100 risk on $2000 position
    risk_pct = 5.0
    target  = round(price * (1 + risk_pct * MIN_RR / 100), 2)   # 12.5% at 2.5R (display only)
    rr      = MIN_RR
    return sl, target, risk_pct, rr


def simulate(symbol, regime, spy_chg):
    print(f"\n{'─'*62}")
    print(f"  {symbol}")
    print(f"{'─'*62}")

    try:
        daily = yf.Ticker(symbol).history(period='60d', interval='1d')
        intra = yf.Ticker(symbol).history(period=_intraday_period(), interval='5m')
    except Exception as e:
        print(f"  Data error: {e}")
        return

    if daily.empty or intra.empty or len(daily) < ATR_PERIOD + 5:
        print(f"  Insufficient data")
        return

    # Sector ETF intraday % change at each bar — for scoring
    sector_etf  = SECTOR_ETF_MAP.get(symbol, 'SPY')
    etf_pct     = {}   # ts → % change from day open
    etf_times   = []
    try:
        etf_raw = yf.Ticker(sector_etf).history(period=_intraday_period(), interval='5m')
        etf_raw.index = etf_raw.index.tz_convert(ET)
        etf_day = etf_raw[etf_raw.index.date == SIM_DATE]
        if not etf_day.empty:
            etf_open = float(etf_day['Open'].iloc[0])
            if etf_open > 0:
                for ts_e, row_e in etf_day.iterrows():
                    etf_pct[ts_e] = (float(row_e['Close']) - etf_open) / etf_open * 100
                etf_times = sorted(etf_pct.keys())
    except Exception:
        pass

    # Earnings gate — skip if earnings within 3 days
    try:
        cal = yf.Ticker(symbol).calendar
        dte = None
        if isinstance(cal, pd.DataFrame) and not cal.empty:
            for col in cal.columns:
                try:
                    ed = pd.Timestamp(col).date()
                    d  = (ed - SIM_DATE).days
                    if d >= -1:
                        dte = d; break
                except Exception:
                    continue
        elif isinstance(cal, dict):
            for key in ('Earnings Date', 'earningsDate'):
                val = cal.get(key)
                if val:
                    try:
                        ed = pd.Timestamp(val[0] if isinstance(val, list) else val).date()
                        d  = (ed - SIM_DATE).days
                        if d >= -1:
                            dte = d
                    except Exception:
                        pass
                    break
        if dte is not None and 0 <= dte <= 3:
            print(f"  SKIP — earnings in {dte}d (binary event risk)")
            return
    except Exception:
        pass

    intra.index = intra.index.tz_convert(ET)
    today_date  = SIM_DATE
    df5         = intra[intra.index.date == today_date].copy()

    if df5.empty:
        print(f"  No intraday data for {today_date}")
        return

    atr = atr_from_daily(daily)
    if not atr:
        print(f"  ATR unavailable")
        return

    prev_close  = float(daily['Close'].iloc[-2]) if len(daily) >= 2 else float(df5['Open'].iloc[0])
    day_open    = float(df5['Open'].iloc[0])
    final_close = float(df5['Close'].iloc[-1])
    day_gain    = (final_close - prev_close) / prev_close * 100
    avg_vol     = float(daily['Volume'].rolling(20).mean().iloc[-2]) if len(daily) >= 21 else None

    # ORB: first 15 min (9:30–9:44)
    orb_w    = df5[(df5.index.hour == 9) & (df5.index.minute >= 30) & (df5.index.minute < 45)]
    orb_high = round(float(orb_w['High'].max()), 2) if len(orb_w) >= 2 else None
    orb_low  = round(float(orb_w['Low'].min()),  2) if len(orb_w) >= 2 else None

    # Pre-market high (4:00–9:29am) — key overnight resistance level
    pm_high = None
    try:
        pm_raw = yf.Ticker(symbol).history(period=_intraday_period(), interval='5m', prepost=True)
        pm_raw.index = pm_raw.index.tz_convert(ET)
        pm_bars = pm_raw[
            (pm_raw.index.date == today_date) &
            ((pm_raw.index.hour < 9) | ((pm_raw.index.hour == 9) & (pm_raw.index.minute < 30)))
        ]
        if not pm_bars.empty:
            pm_high = round(float(pm_bars['High'].max()), 2)
    except Exception:
        pass

    # Daily MAs + RSI (fixed from daily bars — doesn't change bar by bar)
    close_d = daily['Close']
    ma20    = float(close_d.rolling(20).mean().iloc[-1])
    ema8    = float(close_d.ewm(span=8).mean().iloc[-1])
    ema21   = float(close_d.ewm(span=21).mean().iloc[-1])
    d_d     = close_d.diff()
    g_d     = d_d.clip(lower=0).rolling(14).mean()
    l_d     = (-d_d.clip(upper=0)).rolling(14).mean()
    rsi_d   = round(float(100 - (100 / (1 + g_d.iloc[-1] / l_d.iloc[-1]))), 1) if l_d.iloc[-1] else 50.0
    # 3-day range from daily bars — tight base = potential breakout
    if len(daily) >= 3:
        dr_high  = float(daily['High'].iloc[-3:].max())
        dr_low   = float(daily['Low'].iloc[-3:].min())
        is_tight = (dr_high - dr_low) / dr_low * 100 < 5.0
    else:
        is_tight = False

    pm_str = f"PM high ${pm_high}" if pm_high else "PM high n/a"
    print(f"  Prev close ${prev_close:.2f} → Open ${day_open:.2f} → Close ${final_close:.2f} ({day_gain:+.1f}%)")
    print(f"  ATR ${atr:.2f} | ORB ${orb_low}–${orb_high} | {pm_str} | MA20 ${ma20:.2f} | Daily RSI {rsi_d:.0f}")
    print(f"  Regime: {regime} | SPY {spy_chg:+.1f}%")
    print()

    # ── Bar-by-bar replay ─────────────────────────────────────────────────────
    in_trade     = False
    exited_today = False   # one trade per symbol per day — no re-entry after stop
    entry_price  = sl = target = session_high = entry_time = None
    risk_held    = 0.0     # risk_per_share at entry — for BE stop and partial exit
    shares       = 0
    shares_orig  = 0       # original share count before any partial exits
    partial_done = False
    partial_locked_usd = 0.0
    capital      = CAPITAL_PER_TRADE   # updated at entry with dynamic sizing
    events       = []
    skip_counts  = {}
    first_skip   = {}

    for i, (ts, bar) in enumerate(df5.iterrows()):
        t     = (ts.hour, ts.minute)
        price = float(bar['Close'])
        tstr  = ts.strftime('%H:%M')

        # ── In trade: check exits ──────────────────────────────────────────────
        if in_trade:
            session_high = max(session_high, float(bar['High']))
            pnl_pct      = (price - entry_price) / entry_price * 100

            # ATR trail — activates once 1×ATR in profit
            if price >= entry_price + atr:
                new_trail = round(session_high - ATR_TRAIL_MULT * atr, 2)
                if new_trail > sl:
                    events.append(f"    → {tstr}  ATR trail raised to ${new_trail:.2f}  ({pnl_pct:+.1f}%)")
                    sl = new_trail

            # Break-even stop — trigger at BE_TRIGGER_PCT; offset scales with trigger
            if pnl_pct >= BE_TRIGGER_PCT and sl < entry_price:
                # Tight trigger (0.5%): minimal $0.05 buffer — just noise avoidance
                # Relaxed trigger (1.5%+): use half the risk distance — meaningful lock
                be_offset = risk_held * 0.5 if BE_TRIGGER_PCT >= 1.0 else 0.05
                be_sl = round(entry_price + max(be_offset, 0.05), 2)
                if be_sl > sl:
                    events.append(f"    → {tstr}  Break-even stop → ${be_sl:.2f}  ({pnl_pct:+.1f}%)")
                    sl = be_sl

            # Partial exit at 1R — lock 50% of position, trail the rest
            if PARTIAL_EXIT and not partial_done and risk_held > 0 and shares >= 2:
                if price >= entry_price + risk_held:
                    half   = shares // 2
                    locked = round(risk_held * half, 2)
                    events.append(f"    → {tstr}  PARTIAL EXIT {half}/{shares_orig}sh @ ${price:.2f}  +${locked:.2f} locked (1R)")
                    partial_locked_usd += locked
                    shares      -= half
                    partial_done = True

            # 5-min trail — kicks in at 3%+ profit
            if pnl_pct >= 3.0 and i >= 2:
                bar_trail = round(float(df5['Low'].iloc[max(0, i-2):i].min()), 2)
                if bar_trail > sl:
                    events.append(f"    → {tstr}  5m trail raised to ${bar_trail:.2f}  ({pnl_pct:+.1f}%)")
                    sl = bar_trail

            # VWAP for exit check
            sub          = df5.iloc[:i+1].copy()
            sub['tp']    = (sub['High'] + sub['Low'] + sub['Close']) / 3
            sub['vwap']  = (sub['tp'] * sub['Volume']).cumsum() / sub['Volume'].cumsum()
            vwap_now     = float(sub['vwap'].iloc[-1])
            above_now    = price > vwap_now
            above_prev   = (float(sub['Close'].iloc[-2]) > float(sub['vwap'].iloc[-2])
                            if len(sub) >= 2 else True)

            ep_out = price
            reason = None

            if float(bar['Low']) <= sl:
                ep_out = sl
                reason = f'Stop hit @ ${sl:.2f}'
            elif pnl_pct > 0.5 and not above_now and above_prev:
                reason = f'VWAP cross below ${vwap_now:.2f}'
            elif (session_high - price) > ATR_FADE_MULT * atr and pnl_pct > 0.3:
                reason = f'Momentum fade from session high ${session_high:.2f}'
            elif (entry_time is not None and
                  (ts - entry_time).total_seconds() / 60 >= NO_MOVE_MINUTES and
                  -0.3 <= pnl_pct <= NO_MOVE_UPPER_PCT):
                mins_held = int((ts - entry_time).total_seconds() / 60)
                reason = f'No-move exit: flat {mins_held}min ({pnl_pct:+.1f}%)'
            elif t >= (EOD_CLOSE_HOUR, EOD_CLOSE_MINUTE):
                if not (pnl_pct > 1.5 and above_now):
                    reason = 'EOD — no overnight conviction'

            if reason:
                usd_f = (ep_out - entry_price) * shares + partial_locked_usd
                pnl_f = usd_f / capital * 100
                tag   = '✅ WIN' if usd_f > 0 else '❌ LOSS'
                events.append(f"  ■ {tstr}  ${ep_out:.2f}  EXIT   {reason}")
                lock_str = f'  (incl. ${partial_locked_usd:+.2f} partial)' if partial_done else ''
                events.append(f"           → {tag}  {pnl_f:+.2f}%  ${usd_f:+.2f}{lock_str}")
                in_trade     = False
                exited_today = True
            continue

        # ── Not in trade: scan for entry ───────────────────────────────────────
        if exited_today:
            continue

        blocked = {'CHOPPY', 'WEAK'} | ({'CAUTIOUS'} if BLOCK_CAUTIOUS else set())
        if regime in blocked:
            continue

        # Early catalyst: gap ≥6% + vol ≥3x at 9:35–9:59am → bypass 10am + 75-bar guards
        gap_pct_now   = (price - prev_close) / prev_close * 100
        mins_open_now = max(1, (ts.hour - 9) * 60 + ts.minute - 30)
        vol_early     = round(df5.iloc[:i+1]['Volume'].sum() * (390 / mins_open_now) / avg_vol, 2) if avg_vol else 0.0
        is_early_catalyst = (EARLY_ENTRY_ENABLED and i >= 1 and
                              t[0] == 9 and t[1] >= 35 and
                              gap_pct_now >= EARLY_ENTRY_GAP_PCT and
                              vol_early >= EARLY_ENTRY_VOL_MIN)

        in_window = (NO_ENTRY_BEFORE <= ts.hour < NO_ENTRY_AFTER and
                     not (LUNCH_AVOID_START <= t < LUNCH_AVOID_END))
        if not in_window and not is_early_catalyst:
            continue
        if not is_early_catalyst and i < 15:
            continue

        sub         = df5.iloc[:i+1].copy()
        sub['tp']   = (sub['High'] + sub['Low'] + sub['Close']) / 3
        sub['vwap'] = (sub['tp'] * sub['Volume']).cumsum() / sub['Volume'].cumsum()
        vwap        = round(float(sub['vwap'].iloc[-1]), 2)
        above_vwap  = price > vwap
        vwap_reclaim = (len(sub) >= 2 and
                        float(sub['Close'].iloc[-1]) > float(sub['vwap'].iloc[-1]) and
                        float(sub['Close'].iloc[-2]) <= float(sub['vwap'].iloc[-2]))

        # 5-min RSI
        d5    = sub['Close'].diff()
        g5    = d5.clip(lower=0).rolling(14).mean()
        l5    = (-d5.clip(upper=0)).rolling(14).mean()
        rsi5m = round(float(100 - (100 / (1 + g5.iloc[-1] / l5.iloc[-1]))), 1) if l5.iloc[-1] else 50.0

        # Annualised volume ratio
        mins_open = max(1, (ts.hour - 9) * 60 + ts.minute - 30)
        vol_ratio = round((sub['Volume'].sum() * (390 / mins_open)) / avg_vol, 2) if avg_vol else 1.0

        # Today's gain so far (prev_close → current price)
        today_gain_now = (price - prev_close) / prev_close * 100

        # ORB break
        orb_break = (orb_high is not None and price > orb_high and
                     price >= orb_high * 0.998 and t < ORB_ENTRY_CUTOFF)

        # HOD break
        hod       = float(sub['High'].max())
        prior_hod = float(sub['High'].iloc[:-2].max()) if len(sub) > 2 else hod
        hod_break = price >= prior_hod * 0.999 and price >= hod * 0.995

        # Bull flag: pole ≥2% then tight base <2%
        is_bull_flag = False
        if len(sub) >= 15:
            pole       = sub.iloc[-14:-5]
            base       = sub.iloc[-5:]
            pole_move  = (float(pole['High'].max()) - float(pole['Open'].iloc[0])) / max(float(pole['Open'].iloc[0]), 0.01) * 100
            base_range = (float(base['High'].max()) - float(base['Low'].min())) / max(float(base['Close'].mean()), 0.01) * 100
            is_bull_flag = pole_move >= 2.0 and base_range < 2.0 and price >= float(base['High'].max()) * 0.998

        above_ma_now = price > ma20
        uptrend_now  = price > ema8 > ema21
        rs_vs_spy    = round(today_gain_now - spy_chg, 2)
        strong_momo  = today_gain_now >= 5.0 and rs_vs_spy >= 3.0
        has_pattern  = orb_break or vwap_reclaim or is_bull_flag or hod_break or strong_momo
        vol_thresh   = 1.0 if price > 100 else MIN_VOLUME_RATIO

        # Hard gates
        skip = None
        if not above_ma_now:
            skip = 'Below MA20'
        elif vol_ratio < vol_thresh:
            skip = f'Volume {vol_ratio:.1f}x (need ≥{vol_thresh:.1f}x)'
        elif today_gain_now < MIN_TODAY_GAIN:
            skip = f'Only +{today_gain_now:.1f}% today (need ≥{MIN_TODAY_GAIN}%)'
        elif not has_pattern:
            skip = 'No pattern (ORB / VWAP reclaim / bull flag / HOD break)'

        if skip:
            skip_counts[skip] = skip_counts.get(skip, 0) + 1
            if skip not in first_skip:
                first_skip[skip] = (tstr, price)
            continue

        # SL / Target / R:R
        sl_val, tgt_val, risk_pct, rr = calc_sl_target(price, daily, atr)
        if rr < MIN_RR:
            skip = f'R:R 1:{rr:.1f} below min 1:{MIN_RR}'
            skip_counts[skip] = skip_counts.get(skip, 0) + 1
            if skip not in first_skip:
                first_skip[skip] = (tstr, price)
            continue

        # FVG count — 3-bar institutional imbalance gaps on 5m chart
        fvg_count = 0
        fh = sub['High'].values; fl = sub['Low'].values; fc = sub['Close'].values
        for fi in range(1, len(sub) - 1):
            if fh[fi-1] < fl[fi+1]:
                if (fl[fi+1] - fh[fi-1]) / max(fc[fi], 0.01) * 100 >= 0.15:
                    fvg_count += 1

        ema_touch = abs(price - ema21) / price * 100 < 2.5

        # Grade / score
        score    = 0
        patterns = []
        if orb_break:
            score += 30; patterns.append('ORB ✓')
        if vwap_reclaim:
            score += 25; patterns.append('VWAP reclaim ✓')
        elif above_vwap:
            score += 10; patterns.append('Above VWAP')
        if is_bull_flag:
            score += 25; patterns.append('Bull flag ✓')
        if hod_break:
            score += 20; patterns.append('HOD break ✓')
        if rs_vs_spy >= 5:
            score += 20; patterns.append(f'RS +{rs_vs_spy:.1f}% vs SPY')
        elif rs_vs_spy >= 2:
            score += 10; patterns.append(f'RS +{rs_vs_spy:.1f}% vs SPY')
        elif rs_vs_spy < 0:
            score -= 10; patterns.append(f'RS {rs_vs_spy:.1f}% lagging SPY')
        if vol_ratio >= 2.0:
            score += 25; patterns.append(f'{vol_ratio:.1f}x vol')
        elif vol_ratio >= 1.5:
            score += 15; patterns.append(f'{vol_ratio:.1f}x vol')
        else:
            score += 5;  patterns.append(f'{vol_ratio:.1f}x vol')
        if fvg_count >= 10:
            score += 30; patterns.append(f'{fvg_count} FVGs')
        elif fvg_count >= 5:
            score += 20; patterns.append(f'{fvg_count} FVGs')
        elif fvg_count >= 1:
            score += 10; patterns.append(f'{fvg_count} FVGs')
        if uptrend_now and ema_touch:
            score += 20; patterns.append('EMA pullback in uptrend')
        elif uptrend_now:
            score += 10; patterns.append('Uptrend')
        if 45 <= rsi_d <= 65:
            score += 20; patterns.append(f'Daily RSI {rsi_d:.0f} ideal')
        elif 65 < rsi_d <= 80:
            score += 10; patterns.append(f'Daily RSI {rsi_d:.0f} momentum')
        else:
            score += 5;  patterns.append(f'Daily RSI {rsi_d:.0f}')
        if is_tight:
            score += 10; patterns.append('Tight range ✓')

        # 5m RSI — contextual penalty, not a gate
        if rsi5m > MAX_RSI_5M:
            score -= 20; patterns.append(f'5m RSI {rsi5m:.0f} exhausted (-20)')
        elif rsi5m > 75:
            score -= 10; patterns.append(f'5m RSI {rsi5m:.0f} elevated (-10)')

        # PM high — above it means overnight resistance cleared
        if pm_high:
            if price >= pm_high * 1.001:
                score += 15; patterns.append(f'Above PM high ${pm_high} ✓')
            elif price >= pm_high * 0.998:
                score += 5;  patterns.append(f'Testing PM high ${pm_high}')
            else:
                score -= 5;  patterns.append(f'Below PM high ${pm_high} (resistance)')

        if regime == 'STRONG':
            score += 15
        elif regime == 'NORMAL':
            score += 5
        if today_gain_now >= 5.0:
            score += 30
        elif today_gain_now >= 3.0:
            score += 20
        elif today_gain_now >= 1.5:
            score += 10
        if rr >= 4:
            score += 10
        elif rr >= MIN_RR:
            score += 5

        # ── Candlestick quality (last completed 5m bar) ───────────────────────
        last_o = float(bar['Open']); last_c = float(bar['Close'])
        last_h = float(bar['High']); last_l = float(bar['Low'])
        candle_range = last_h - last_l
        candle_body  = abs(last_c - last_o)
        body_ratio   = candle_body / candle_range if candle_range > 0 else 0
        is_bullish_candle = last_c > last_o and body_ratio >= 0.6
        is_doji           = body_ratio < 0.2
        is_hammer         = (last_c > last_o and candle_range > 0 and
                              (last_o - last_l) > candle_body * 2 and
                              (last_h - last_c) < candle_body * 0.5)
        if is_hammer:
            score += 15; patterns.append('Hammer ✓')
        elif is_bullish_candle:
            score += 10; patterns.append('Bullish candle ✓')
        elif is_doji and above_vwap:
            score -= 5;  patterns.append('Doji at key level (-5)')

        # ── 15m alignment ────────────────────────────────────────────────────
        try:
            df15        = yf.Ticker(symbol).history(period=_intraday_period(), interval='15m')
            df15.index  = df15.index.tz_convert(ET)
            df15_today  = df15[df15.index.date == today_date]
            df15_now    = df15_today[df15_today.index <= ts]
            if len(df15_now) >= 5:
                tp15    = (df15_now['High'] + df15_now['Low'] + df15_now['Close']) / 3
                v15     = float((tp15 * df15_now['Volume']).cumsum().iloc[-1] / df15_now['Volume'].cumsum().iloc[-1])
                e20_15  = float(df15_now['Close'].ewm(span=20).mean().iloc[-1])
                p15     = float(df15_now['Close'].iloc[-1])
                if p15 > v15 and p15 > e20_15:
                    score += 10; patterns.append('15m aligned ✓')
                else:
                    score -= 15; patterns.append('15m counter-trend (-15)')
        except Exception:
            pass  # no 15m data — don't penalise

        # ── Sector ETF strength ───────────────────────────────────────────────
        etf_chg = 0.0
        if etf_times:
            valid = [t for t in etf_times if t <= ts]
            if valid:
                etf_chg = etf_pct[valid[-1]]
        if etf_chg >= 1.5:
            score += 15; patterns.append(f'{sector_etf} +{etf_chg:.1f}% leading ✓')
        elif etf_chg >= 0.5:
            score += 5;  patterns.append(f'{sector_etf} +{etf_chg:.1f}% sector up')
        elif etf_chg < -0.5:
            score -= 10; patterns.append(f'{sector_etf} {etf_chg:.1f}% weak sector (-10)')

        grade = 'A+' if score >= 80 else 'A' if score >= 65 else 'B' if score >= 50 else 'C'

        if is_early_catalyst and grade != 'A+':
            skip = f'Early entry: need A+ (score {score} < 80) — too risky pre-10am'
            skip_counts[skip] = skip_counts.get(skip, 0) + 1
            if skip not in first_skip:
                first_skip[skip] = (tstr, price)
            continue
        if grade in ('B', 'C'):
            skip = f'Grade {grade} (score {score}) — need A or A+'
            skip_counts[skip] = skip_counts.get(skip, 0) + 1
            if skip not in first_skip:
                first_skip[skip] = (tstr, price)
            continue
        if spy_chg < 0 and grade != 'A+':
            skip = f'Grade {grade} skipped — SPY negative, need A+'
            skip_counts[skip] = skip_counts.get(skip, 0) + 1
            if skip not in first_skip:
                first_skip[skip] = (tstr, price)
            continue

        # ── ENTRY ──────────────────────────────────────────────────────────────
        gap_now      = (price - prev_close) / prev_close * 100
        is_catalyst  = gap_now >= 4.0 and vol_ratio >= 2.0
        capital      = grade_capital(grade, is_catalyst)
        in_trade     = True
        entry_price  = price
        sl           = sl_val
        target       = tgt_val
        session_high = price
        entry_time   = ts
        # ATR-normalized: size so actual stop risk ≤ MAX_LOSS_PER_TRADE
        risk_per_share = round(price - sl_val, 4)
        atr_shares     = int(MAX_LOSS_PER_TRADE / risk_per_share) if risk_per_share > 0 else int(capital / price)
        shares         = max(1, min(int(capital / price), atr_shares))
        risk_held      = risk_per_share
        shares_orig    = shares
        partial_done   = False
        partial_locked_usd = 0.0
        cat_tag      = ' ⚡CATALYST' if is_catalyst else ''
        early_tag    = ' 🌅EARLY-9AM' if is_early_catalyst else ''

        events.append(f"  ▶ {tstr}  ${price:.2f}  ENTER  Grade {grade}{cat_tag}{early_tag}  score={score}  capital=${capital:,}")
        events.append(f"       Patterns : {' | '.join(patterns)}")
        events.append(f"       SL ${sl:.2f} | Target ${target:.2f} | R:R 1:{rr:.1f} | {shares} sh × ${capital:,}")

    # ── Print results ──────────────────────────────────────────────────────────
    if skip_counts:
        total_skips = sum(skip_counts.values())
        print(f"  SCAN LOG — {total_skips} bars rejected across {len(df5)} total bars:")
        for reason, count in sorted(skip_counts.items(), key=lambda x: -x[1]):
            ex_t, ex_p = first_skip.get(reason, ('?', 0))
            print(f"    ✗ {count:>3}×  {reason}  (first: {ex_t} ${ex_p:.2f})")
        print()

    if events:
        print(f"  EVENTS:")
        for e in events:
            print(e)
    else:
        print(f"  → No entry triggered today")

    # Final state
    if in_trade:
        last_price = float(df5['Close'].iloc[-1])
        pnl_u = (last_price - entry_price) * shares + partial_locked_usd
        pnl_p = pnl_u / capital * 100
        tag   = '📈' if pnl_p > 0 else '📉'
        print(f"\n  ⏳ STILL OPEN (no exit signal fired)")
        print(f"     Entry ${entry_price:.2f} | Last ${last_price:.2f} | {tag} {pnl_p:+.2f}% | ${pnl_u:+.2f}")
    elif not events:
        print(f"\n  VERDICT: SKIP — no bar qualified for entry today")


def main():
    print(f"\n{'='*62}")
    print(f"  SIMULATION — {SIM_DATE}  {'(last trading day)' if SIM_DATE != date.today() else ''}")
    print(f"  Symbols : {', '.join(SYMBOLS)}")
    print(f"  Capital : up to $2,000/trade | Stop: 5% fixed | Partial exit at 1R")
    print(f"{'='*62}")

    print("\nFetching market regime...")
    regime, spy_chg, vix = get_regime()
    print(f"Regime: {regime} | SPY {spy_chg:+.1f}% | VIX {vix:.1f}")

    if regime in ('CHOPPY', 'WEAK'):
        print(f"⚠️  {regime} market — live system skips new entries (showing sim anyway)")

    for sym in SYMBOLS:
        simulate(sym, regime, spy_chg)

    print(f"\n{'='*62}\n")


if __name__ == '__main__':
    main()
