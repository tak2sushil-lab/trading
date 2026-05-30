"""
WEAK Signal Strength Adaptive Confirmation — Backtest
======================================================
Question: On post-lunch WEAK signals (12:45–2pm), does a "high-confidence"
first scan (SPY < -0.7%, breadth weak, VIX rising, QQQ lagging) hold its
direction well enough that 2 scans is sufficient — vs the current 3?

Two analyses:
  Part 1 — 5-min precision (59 days): Regime stability test.
            Given strong vs marginal first WEAK scan post-lunch, does the
            regime hold WEAK at T+5 and T+10? If strong signals hold 90%+,
            2 scans is safe. If they reverse 30%+, 3 scans is still needed.

  Part 2 — Daily proxy (3 years): Larger sample.
            On days where SPY drops past -0.7% by 1pm, measure close-of-day
            return. Compare to days where SPY is only -0.3% to -0.5% at 1pm.
            Proxy for "did entering at 2 scans vs 3 scans matter?"

Usage: venv/bin/python backtest_weak_confirmation.py
"""

import yfinance as yf
import pandas as pd
import numpy as np
import pytz
from datetime import timedelta

ET = pytz.timezone('America/New_York')

WEAK_THRESHOLD       = -0.5   # SPY chg from open to classify as WEAK
STRONG_WEAK_SPY      = -0.7   # strong WEAK: SPY below this
POST_LUNCH_START     = (12, 45)
POST_LUNCH_END       = (14, 0)


# ── Part 1: 5-min regime stability (59 days) ─────────────────────────────────

def classify_regime_simple(spy_chg, vix_now, vix_prev, qqq_chg, iwm_chg, mdy_chg):
    breadth_weak = iwm_chg < -0.5 and mdy_chg < -0.5
    vix_rising   = vix_now > vix_prev
    qqq_lagging  = qqq_chg < spy_chg - 0.3

    if spy_chg < WEAK_THRESHOLD:
        regime = 'WEAK'
    elif spy_chg >= 0.5:
        regime = 'STRONG'
    elif spy_chg >= 0:
        regime = 'NORMAL'
    else:
        regime = 'CAUTIOUS'

    # Breadth downgrade
    if breadth_weak and regime not in ('WEAK',):
        regime = 'WEAK'

    is_strong_weak = (
        spy_chg < STRONG_WEAK_SPY and
        breadth_weak and
        vix_rising and
        qqq_lagging
    )
    return regime, is_strong_weak, breadth_weak, vix_rising, qqq_lagging


def run_part1():
    print("=" * 65)
    print("  PART 1: 5-MIN REGIME STABILITY (59 trading days)")
    print("=" * 65)
    print("Loading SPY/QQQ/IWM/MDY/VIX 5-min bars...")

    spy_5m = yf.Ticker('SPY').history(period='59d', interval='5m')
    qqq_5m = yf.Ticker('QQQ').history(period='59d', interval='5m')
    iwm_5m = yf.Ticker('IWM').history(period='59d', interval='5m')
    mdy_5m = yf.Ticker('MDY').history(period='59d', interval='5m')
    vix_5m = yf.Ticker('^VIX').history(period='59d', interval='5m')

    for df in [spy_5m, qqq_5m, iwm_5m, mdy_5m, vix_5m]:
        if not df.empty:
            df.index = df.index.tz_convert(ET)

    trading_days = sorted(spy_5m.index.normalize().unique())
    print(f"  {len(trading_days)} trading days\n")

    # Results: for each post-lunch first-WEAK scan, did regime hold at T+5 and T+10?
    results = []

    for day in trading_days:
        day_date = day.date()
        spy_day  = spy_5m[spy_5m.index.date == day_date]
        qqq_day  = qqq_5m[qqq_5m.index.date == day_date]
        iwm_day  = iwm_5m[iwm_5m.index.date == day_date]
        mdy_day  = mdy_5m[mdy_5m.index.date == day_date]
        vix_day  = vix_5m[vix_5m.index.date == day_date]

        rth_spy = spy_day.between_time('09:30', '16:00')
        if len(rth_spy) < 5:
            continue

        spy_open = float(rth_spy['Close'].iloc[0])

        # Post-lunch window bars
        pl_spy = spy_day.between_time(f'{POST_LUNCH_START[0]:02d}:{POST_LUNCH_START[1]:02d}',
                                       f'{POST_LUNCH_END[0]:02d}:{POST_LUNCH_END[1]:02d}')
        if len(pl_spy) < 3:
            continue

        first_weak_found = False

        for i, (ts, row) in enumerate(pl_spy.iterrows()):
            spy_chg = (float(row['Close']) - spy_open) / spy_open * 100

            # Get matching QQQ/IWM/MDY/VIX bar
            try:
                qqq_open = float(qqq_day.between_time('09:30','09:35')['Close'].iloc[0])
                qqq_chg  = (float(qqq_day.loc[ts:ts,'Close'].iloc[0]) - qqq_open) / qqq_open * 100 if not qqq_day.loc[ts:ts].empty else spy_chg
            except: qqq_chg = spy_chg

            try:
                iwm_open = float(iwm_day.between_time('09:30','09:35')['Close'].iloc[0])
                iwm_chg  = (float(iwm_day.loc[ts:ts,'Close'].iloc[0]) - iwm_open) / iwm_open * 100 if not iwm_day.loc[ts:ts].empty else 0
            except: iwm_chg = 0

            try:
                mdy_open = float(mdy_day.between_time('09:30','09:35')['Close'].iloc[0])
                mdy_chg  = (float(mdy_day.loc[ts:ts,'Close'].iloc[0]) - mdy_open) / mdy_open * 100 if not mdy_day.loc[ts:ts].empty else 0
            except: mdy_chg = 0

            try:
                vix_bars  = vix_day[vix_day.index <= ts]['Close']
                vix_now   = float(vix_bars.iloc[-1])
                vix_prev  = float(vix_bars.iloc[-2]) if len(vix_bars) >= 2 else vix_now
            except: vix_now = vix_prev = 18.0

            regime, is_strong, bw, vix_r, qqq_l = classify_regime_simple(
                spy_chg, vix_now, vix_prev, qqq_chg, iwm_chg, mdy_chg)

            if regime != 'WEAK' or first_weak_found:
                continue

            # First WEAK scan found in post-lunch window
            first_weak_found = True

            # Check T+5 (next bar) and T+10 (bar after that)
            future_bars = pl_spy.iloc[i+1:i+3]
            regimes_after = []
            for j, (ts2, row2) in enumerate(future_bars.iterrows()):
                spy_chg2 = (float(row2['Close']) - spy_open) / spy_open * 100
                try:
                    qqq_chg2 = (float(qqq_day.loc[ts2:ts2,'Close'].iloc[0]) - qqq_open) / qqq_open * 100 if not qqq_day.loc[ts2:ts2].empty else spy_chg2
                except: qqq_chg2 = spy_chg2
                r2, _, _, _, _ = classify_regime_simple(spy_chg2, vix_now, vix_now, qqq_chg2, iwm_chg, mdy_chg)
                regimes_after.append(r2)

            held_t5  = len(regimes_after) >= 1 and regimes_after[0] == 'WEAK'
            held_t10 = len(regimes_after) >= 2 and regimes_after[1] == 'WEAK'

            results.append({
                'date':       day_date,
                'time':       ts.strftime('%H:%M'),
                'spy_chg':    round(spy_chg, 2),
                'breadth_w':  bw,
                'vix_rising': vix_r,
                'qqq_lag':    qqq_l,
                'strong':     is_strong,
                'held_t5':    held_t5,
                'held_t10':   held_t10,
            })

    if not results:
        print("  No post-lunch WEAK signals found in 59-day window.")
        return

    df = pd.DataFrame(results)

    # Split by SPY depth alone — this is the real signal
    deep   = df[df['spy_chg'] <= -0.7]          # SPY clearly weak
    mid    = df[(df['spy_chg'] > -0.7) & (df['spy_chg'] <= -0.4)]   # grey zone
    shallow= df[df['spy_chg'] > -0.4]           # breadth-driven, SPY barely moved

    # Original 4-factor split for comparison
    strong   = df[df['strong']]
    marginal = df[~df['strong']]

    print(f"  Post-lunch WEAK signals found: {len(df)}")
    print(f"  → By SPY depth:")
    print(f"      Deep   (SPY ≤ -0.7%)   : {len(deep)}")
    print(f"      Mid    (-0.4% to -0.7%): {len(mid)}")
    print(f"      Shallow (SPY > -0.4%)  : {len(shallow)} ← breadth-driven, SPY barely moved")
    print(f"  → By 4-factor rule: {len(strong)} high-conf / {len(marginal)} marginal")

    def stats(subset, label):
        if subset.empty:
            print(f"\n  {label}: no events")
            return
        n   = len(subset)
        t5  = int(subset['held_t5'].sum())
        t10 = int(subset['held_t10'].sum())
        print(f"\n  {label} (n={n}):")
        print(f"    Held WEAK at T+5  (2-scan entry): {t5}/{n} = {t5/n*100:.0f}%")
        print(f"    Held WEAK at T+10 (3-scan entry): {t10}/{n} = {t10/n*100:.0f}%")
        print(f"    Avg SPY chg: {subset['spy_chg'].mean():+.2f}%")
        rev = subset[~subset['held_t5']]
        if not rev.empty:
            print(f"    ⚠️  Reversals at T+5: {rev[['date','time','spy_chg']].to_string(index=False)}")

    stats(deep,    "DEEP WEAK   (SPY ≤ -0.7%)")
    stats(mid,     "MID  WEAK   (-0.4% to -0.7%)")
    stats(shallow, "SHALLOW WEAK (SPY > -0.4%, breadth-driven)")

    print(f"\n  REAL FINDING:")
    if not deep.empty:
        t5_deep = deep['held_t5'].mean() * 100
        t5_mid  = mid['held_t5'].mean()  * 100 if not mid.empty else 0
        t5_sh   = shallow['held_t5'].mean() * 100 if not shallow.empty else 0
        print(f"  Deep WEAK held at T+5:    {t5_deep:.0f}%  ← 2-scan entry safe?")
        print(f"  Mid  WEAK held at T+5:    {t5_mid:.0f}%  ← borderline")
        print(f"  Shallow WEAK held at T+5: {t5_sh:.0f}%  ← keep 3 scans")
        if t5_deep >= 90:
            print(f"  ✅ SPY ≤ -0.7% alone is sufficient fast-track criterion — simpler than 4 factors")
        elif t5_deep >= 75:
            print(f"  ⚠️  SPY ≤ -0.7% borderline — needs 2-year data to confirm")
        else:
            print(f"  ❌ SPY depth alone not predictive — keep 3 scans for all")


# ── Part 2: Daily proxy, 3 years ─────────────────────────────────────────────

def run_part2():
    print("\n" + "=" * 65)
    print("  PART 2: DAILY PROXY — 2-YEAR HORIZON (2024–2026)")
    print("=" * 65)
    print("  Proxy: SPY intraday drop at 1pm ET vs close return")
    print("  Logic: 'high confidence WEAK' = SPY clearly down by midday")
    print("  Loading SPY 1-hour bars...")

    spy_1h = yf.Ticker('SPY').history(start='2024-05-07', end='2026-05-07', interval='1h')
    if spy_1h.empty:
        # fallback: try shorter period
        spy_1h = yf.Ticker('SPY').history(period='720d', interval='1h')
    if spy_1h.empty:
        print("  1h data unavailable")
        return

    spy_1h.index = spy_1h.index.tz_convert(ET)
    trading_days = sorted(spy_1h.index.normalize().unique())
    print(f"  {len(trading_days)} trading days\n")

    strong_events   = []   # SPY < -0.7% at 1pm → close performance
    marginal_events = []   # SPY -0.3% to -0.5% at 1pm → close performance
    flat_events     = []   # SPY within ±0.3% at 1pm → control group

    for day in trading_days:
        day_date = day.date()
        day_bars = spy_1h[spy_1h.index.date == day_date]
        rth      = day_bars.between_time('09:30', '16:00')
        if len(rth) < 5:
            continue

        spy_open = float(rth['Close'].iloc[0])

        # Bar at or just after 1pm
        pm_bars = rth.between_time('13:00', '13:59')
        if pm_bars.empty:
            continue
        spy_1pm = float(pm_bars['Close'].iloc[-1])
        spy_chg_1pm = (spy_1pm - spy_open) / spy_open * 100

        # Close
        spy_close = float(rth['Close'].iloc[-1])
        spy_chg_1pm_to_close = (spy_close - spy_1pm) / spy_1pm * 100

        event = {
            'date':           day_date,
            'spy_chg_1pm':    round(spy_chg_1pm, 2),
            'chg_to_close':   round(spy_chg_1pm_to_close, 2),
            'continued_down': spy_chg_1pm_to_close < 0,
        }

        if spy_chg_1pm <= -0.7:
            strong_events.append(event)
        elif -0.5 >= spy_chg_1pm > -0.7:
            marginal_events.append(event)

    def show_stats(events, label):
        if not events:
            print(f"\n  {label}: no events")
            return
        n   = len(events)
        wr  = sum(1 for e in events if e['continued_down']) / n * 100
        avg = np.mean([e['chg_to_close'] for e in events])
        avg_win  = np.mean([e['chg_to_close'] for e in events if e['continued_down']]) if any(e['continued_down'] for e in events) else 0
        avg_loss = np.mean([e['chg_to_close'] for e in events if not e['continued_down']]) if any(not e['continued_down'] for e in events) else 0
        print(f"\n  {label}")
        print(f"    N                   : {n}")
        print(f"    % continued lower   : {wr:.0f}%  ({sum(1 for e in events if e['continued_down'])}W / {n - sum(1 for e in events if e['continued_down'])}L)")
        print(f"    Avg SPY 1pm→close   : {avg:+.2f}%  (down days {avg_win:+.2f}% / up days {avg_loss:+.2f}%)")
        freq = n / (len(trading_days) / 12)
        print(f"    Frequency           : ~{freq:.1f}x/month")

    show_stats(strong_events,   "HIGH-CONFIDENCE (SPY ≤ -0.7% at 1pm)")
    show_stats(marginal_events, "MARGINAL        (SPY -0.5% to -0.7% at 1pm)")

    print(f"\n  INTERPRETATION:")
    if strong_events and marginal_events:
        wr_s = sum(1 for e in strong_events if e['continued_down']) / len(strong_events) * 100
        wr_m = sum(1 for e in marginal_events if e['continued_down']) / len(marginal_events) * 100
        print(f"  Strong WEAK continued lower: {wr_s:.0f}%")
        print(f"  Marginal WEAK continued lower: {wr_m:.0f}%")
        diff = wr_s - wr_m
        if diff >= 8:
            print(f"  ✅ Strong signals {diff:+.0f}pp more reliable — fast-track has genuine edge")
        elif diff >= 3:
            print(f"  ⚠️  Modest {diff:+.0f}pp difference — edge exists but thin")
        else:
            print(f"  ❌ No meaningful difference — strength of first scan doesn't predict reliability")
    print("=" * 65)


if __name__ == '__main__':
    run_part1()
    run_part2()
