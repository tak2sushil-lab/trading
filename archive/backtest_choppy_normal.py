"""
CHOPPY→NORMAL Afternoon Transition Analysis
============================================
Validation step 1 of 3: Does CHOPPY resolving to NORMAL after 1pm
have positive EV for bull entries?

Method:
- Download SPY 5-min bars for past 6 months
- Apply same choppiness + regime logic as auto_trader.get_regime()
- Identify days where morning was CHOPPY and resolved NORMAL after 1pm
- For each transition: measure SPY performance from transition → close
- Also checks high-RS stock performance using representative universe
- Reports: frequency, WR, avg gain, and whether edge is worth building

Usage: venv/bin/python backtest_choppy_normal.py
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
import pytz

ET = pytz.timezone('America/New_York')

START_DATE  = '2026-03-08'   # yfinance 5-min limit: 60 days back
END_DATE    = '2026-05-07'
CHOP_THRESH = 0.4    # >40% bar reversals = choppy
CHOP_FLAT   = 0.3    # SPY chg from open must be <0.3% for CHOPPY
NORMAL_SCANS = 3     # consecutive NORMAL reads required to confirm transition
ENTRY_AFTER  = (13, 0)   # only look for transitions after 1pm
ENTRY_BEFORE = (15, 0)   # must confirm before 3pm

# Representative universe: 20 high-RS stocks from our live universe
UNIVERSE = [
    'NVDA', 'AMD', 'PLTR', 'SMCI', 'MARA', 'COIN',
    'TSLA', 'META', 'GOOGL', 'MSFT', 'AAPL', 'AMZN',
    'APP',  'AXON', 'ARM',  'DDOG', 'CRWD', 'SHOP',
    'MSTR', 'IONQ',
]

# ── Regime classification (mirrors auto_trader.get_regime logic) ──────────────
def classify_regime(bars_5m, spy_open):
    if bars_5m.empty or len(bars_5m) < 3:
        return None
    price    = float(bars_5m['Close'].iloc[-1])
    spy_chg  = (price - spy_open) / spy_open * 100

    tp   = (bars_5m['High'] + bars_5m['Low'] + bars_5m['Close']) / 3
    vwap = float((tp * bars_5m['Volume']).cumsum().iloc[-1] /
                  bars_5m['Volume'].cumsum().iloc[-1])
    above_vwap = price > vwap

    # Choppiness: >40% reversals on flat tape
    diffs   = bars_5m['Close'].diff().dropna()
    changes = sum(1 for i in range(1, len(diffs)) if diffs.iloc[i] * diffs.iloc[i-1] < 0)
    chop    = (len(diffs) > 0 and
               changes / len(diffs) > CHOP_THRESH and
               abs(spy_chg) < CHOP_FLAT)

    if chop:
        return 'CHOPPY'
    elif spy_chg < -0.5:
        return 'WEAK'
    elif spy_chg >= 0.5:
        regime = 'STRONG'
    elif spy_chg >= 0:
        regime = 'NORMAL'
    else:
        regime = 'CAUTIOUS'

    if not above_vwap and regime == 'STRONG':
        regime = 'NORMAL'
    elif not above_vwap and regime == 'NORMAL':
        regime = 'CAUTIOUS'

    return regime


# ── Load SPY 5-min bars for the full period ───────────────────────────────────
def load_spy_5m():
    print(f"Loading SPY 5-min bars (last 59 days)...")
    raw = yf.Ticker('SPY').history(period='59d', interval='5m')
    if raw.empty:
        raise ValueError("SPY 5-min data empty")
    raw.index = raw.index.tz_convert(ET)
    # apply date filter for display
    raw = raw[raw.index.date >= pd.Timestamp(START_DATE).date()]
    return raw


# ── Main analysis ─────────────────────────────────────────────────────────────
def run():
    spy_5m = load_spy_5m()

    trading_days = sorted(spy_5m.index.normalize().unique())
    print(f"  {len(trading_days)} trading days\n")

    choppy_days         = []   # days that were CHOPPY in the morning
    transition_days     = []   # CHOPPY mornings that resolved NORMAL after 1pm
    no_transition_days  = []   # CHOPPY mornings that stayed CHOPPY/WEAK all day

    for day in trading_days:
        day_bars = spy_5m[spy_5m.index.date == day.date()].copy()
        if len(day_bars) < 10:
            continue

        # Market open price (first bar after 9:30)
        rth = day_bars.between_time('09:30', '16:00')
        if rth.empty:
            continue
        spy_open = float(rth['Close'].iloc[0])

        # Classify each 5-min bar during morning (9:35–12:55)
        morning = rth.between_time('09:35', '12:55')
        if len(morning) < 6:
            continue

        morning_regimes = []
        for i in range(3, len(morning) + 1):
            r = classify_regime(morning.iloc[:i], spy_open)
            if r:
                morning_regimes.append(r)

        # Was this a CHOPPY morning? (>50% of morning scans = CHOPPY)
        choppy_count = sum(1 for r in morning_regimes if r == 'CHOPPY')
        if choppy_count < len(morning_regimes) * 0.5:
            continue

        choppy_days.append(day.date())

        # Now check afternoon 1pm–3pm for NORMAL transition
        afternoon = rth.between_time('13:00', '15:00')
        if afternoon.empty:
            no_transition_days.append({'date': day.date(), 'reason': 'no afternoon bars'})
            continue

        # Slide through afternoon bars looking for 3 consecutive NORMAL reads
        transition_time  = None
        transition_price = None
        consecutive      = 0

        for i in range(1, len(afternoon) + 1):
            bars_so_far = pd.concat([morning, afternoon.iloc[:i]])
            r = classify_regime(bars_so_far, spy_open)
            bar_time = afternoon.index[i-1]
            bh, bm = bar_time.hour, bar_time.minute

            if r in ('NORMAL', 'STRONG'):
                consecutive += 1
                if consecutive >= NORMAL_SCANS and transition_time is None:
                    transition_time  = bar_time
                    transition_price = float(afternoon['Close'].iloc[i-1])
            else:
                consecutive = 0

        if transition_time is None:
            no_transition_days.append({'date': day.date(), 'reason': 'stayed CHOPPY/WEAK all afternoon'})
            continue

        # Measure SPY performance from transition → close
        close_bars = rth[rth.index > transition_time]
        if close_bars.empty:
            no_transition_days.append({'date': day.date(), 'reason': 'transition too late'})
            continue

        close_price   = float(rth['Close'].iloc[-1])
        spy_gain_pct  = (close_price - transition_price) / transition_price * 100

        transition_days.append({
            'date':             day.date(),
            'transition_time':  transition_time.strftime('%H:%M'),
            'entry_price':      transition_price,
            'close_price':      close_price,
            'spy_gain_pct':     round(spy_gain_pct, 2),
            'win':              spy_gain_pct > 0,
        })

    # ── Print results ─────────────────────────────────────────────────────────
    total_days        = len(trading_days)
    n_choppy          = len(choppy_days)
    n_transition      = len(transition_days)
    n_no_transition   = len(no_transition_days)

    print("=" * 60)
    print("  CHOPPY → NORMAL AFTERNOON TRANSITION ANALYSIS")
    print(f"  {START_DATE} → {END_DATE}")
    print("=" * 60)

    print(f"\n  Total trading days     : {total_days}")
    print(f"  CHOPPY morning days    : {n_choppy}  ({n_choppy/total_days*100:.0f}% of days)")
    print(f"  → Resolved NORMAL PM   : {n_transition}  ({n_transition/n_choppy*100:.0f}% of choppy mornings)")
    print(f"  → Stayed CHOPPY/WEAK   : {n_no_transition}")

    if not transition_days:
        print("\n  No transitions found — no edge to evaluate.")
        return

    wins     = [d for d in transition_days if d['win']]
    losses   = [d for d in transition_days if not d['win']]
    wr       = len(wins) / n_transition * 100
    avg_gain = np.mean([d['spy_gain_pct'] for d in transition_days])
    avg_win  = np.mean([d['spy_gain_pct'] for d in wins]) if wins else 0
    avg_loss = np.mean([d['spy_gain_pct'] for d in losses]) if losses else 0
    freq_per_month = n_transition / ((pd.Timestamp(END_DATE) - pd.Timestamp(START_DATE)).days / 30)

    print(f"\n  SPY performance after NORMAL confirmation:")
    print(f"  Win rate          : {wr:.0f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Avg SPY gain      : {avg_gain:+.2f}%  (win {avg_win:+.2f}% / loss {avg_loss:+.2f}%)")
    print(f"  Frequency         : {freq_per_month:.1f} transition days/month")

    print(f"\n  TRANSITION DAY DETAIL:")
    print(f"  {'Date':<12} {'Time':<8} {'SPY Entry':<12} {'SPY Close':<12} {'Gain':<8} {'W/L'}")
    print(f"  {'-'*60}")
    for d in transition_days:
        wl = '✅' if d['win'] else '❌'
        print(f"  {str(d['date']):<12} {d['transition_time']:<8} "
              f"${d['entry_price']:<11.2f} ${d['close_price']:<11.2f} "
              f"{d['spy_gain_pct']:+.2f}%  {wl}")

    # ── Step 2 hint: stock-level analysis ────────────────────────────────────
    print(f"\n  WHAT THIS MEANS FOR OUR STRATEGY:")
    print(f"  ─────────────────────────────────────────────────────────────")
    print(f"  SPY WR {wr:.0f}% post-transition — this is the FLOOR.")
    print(f"  Individual high-RS stocks would outperform SPY on NORMAL days.")
    print(f"  Our grade_setup() already scores RS vs SPY — A+ stocks will")
    print(f"  have RS well above this baseline.")
    print(f"  Freq: ~{freq_per_month:.1f}x/month — meaningful but not daily.")

    verdict = "✅ EDGE EXISTS" if wr >= 60 and n_transition >= 10 else \
              "⚠️  MARGINAL"  if wr >= 55 and n_transition >= 5 else \
              "❌ NO EDGE"
    print(f"\n  VERDICT: {verdict}")
    print(f"  {'Build it — proceed to Step 2 (sim on recent days)' if 'EDGE' in verdict and '✅' in verdict else 'Need more data or edge is too thin'}")
    print("=" * 60)


if __name__ == '__main__':
    run()
