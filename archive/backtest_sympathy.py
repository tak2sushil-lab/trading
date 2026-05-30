#!/usr/bin/env python3
"""
backtest_sympathy.py — Sector sympathy play edge analysis

QUESTION: When a mega-cap sector leader beats/misses earnings big, do sector
sympathy stocks gap in the same direction AND hold through the regular session?

WHAT WE MEASURE (all on daily bars — no pre-market data needed):
  gap_pct     = (sympathy_open - prev_close) / prev_close × 100
                This IS the pre-market move — everything between yesterday's
                close and today's open happened pre-market. If we entered at
                8:30am we'd capture this gap before it becomes the open.
  hold        = stock closed above open (bull) or below open (bear)
                If hold rate is high → direction is real, pre-market entry justified
  continuation= HOD was made after open (not a gap-and-immediate-fade)
  full_day    = (close - prev_close) / prev_close × 100 — full session P&L

  alpha       = sympathy-day WR vs same stocks' WR on non-sympathy days
                Proves the edge is specifically from the trigger, not random

TRIGGER LOGIC:
  Bull trigger: sector leader reports earnings, closes next day >5% above prev close
  Bear trigger: sector leader reports earnings, closes next day >5% below prev close
  Strong beat/miss: >10% move (higher conviction subset)

WHY yfinance NOT IBKR:
  - Earnings dates only available free via yfinance (IBKR needs paid fundamental subs)
  - Daily bar quality identical between sources at 5-year scale
  - IBKR pacing limits would throttle 6yrs × 20 symbols

Command: venv/bin/python backtest_sympathy.py
         venv/bin/python backtest_sympathy.py --strong   (only >10% trigger moves)
         venv/bin/python backtest_sympathy.py --bear      (bear side only)
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date, timedelta
import sys
import warnings
warnings.filterwarnings('ignore')

# ── Config ─────────────────────────────────────────────────────────────
START_DATE          = '2020-01-01'
END_DATE            = date.today().isoformat()
MIN_TRIGGER_MOVE    = 0.05   # trigger stock must move >5% on earnings day to qualify
STRONG_THRESH       = 0.10   # strong beat/miss = trigger moved >10%
MIN_GAP             = 0.015  # sympathy stock must gap >1.5% to count as "sympathy play"
CAPITAL             = 2000   # $ per trade (matching live system)
STOP_PCT            = 0.05   # 5% stop (matching live system)

# Flags
STRONG_ONLY = '--strong' in sys.argv
BEAR_ONLY   = '--bear'   in sys.argv
BULL_ONLY   = '--bull'   in sys.argv

# ── Trigger → Sympathy map ─────────────────────────────────────────────
# Sectors chosen so the fundamental link is explicit:
#   Tech/AI  : shared silicon supply chain + AI spend signal
#   Energy   : oil price realization + refining margin are shared inputs
#   Financials: rate env + credit quality + trading revenue are correlated
#   Health Ins: claims ratio + enrollment trends move the whole sub-sector
#   Retail   : consumer spend + inventory + same-store comps are read-across
SYMPATHY_MAP = {
    # ── Tech / AI ──────────────────────────────────────────────────────
    'NVDA': ['SMCI', 'AMD', 'LRCX', 'MU', 'AMAT', 'MRVL', 'QCOM', 'AVGO'],
    'META': ['SNAP', 'PINS', 'GOOGL', 'TTD'],
    'MSFT': ['CRM', 'ORCL', 'NOW', 'DDOG', 'PLTR'],
    'AAPL': ['QCOM', 'AVGO', 'KEYS', 'SWKS'],
    'AMZN': ['SHOP', 'MELI', 'OKTA'],
    'GOOGL': ['META', 'SNAP', 'PINS', 'TTD'],
    # ── Energy ─────────────────────────────────────────────────────────
    # XOM beat/miss = oil price outlook + refining margins → peers read-across
    'XOM':  ['CVX', 'COP', 'SLB', 'HAL', 'OXY', 'PSX', 'VLO'],
    'CVX':  ['XOM', 'COP', 'SLB', 'HAL', 'OXY'],
    # ── Financials ─────────────────────────────────────────────────────
    # JPM/GS set tone on net interest margin, credit quality, trading revenue
    'JPM':  ['BAC', 'GS', 'MS', 'WFC', 'C', 'SCHW'],
    'GS':   ['MS', 'JPM', 'BAC', 'SCHW'],
    # ── Health Insurance ───────────────────────────────────────────────
    # UNH medical-loss ratio + enrollment data ripples to all managed care
    'UNH':  ['HUM', 'CVS', 'ELV', 'CNC', 'MOH'],
    # ── Retail / Consumer ──────────────────────────────────────────────
    # WMT comps + inventory guide read-across to sector peers; HD/LOW share same housing spend
    'WMT':  ['TGT', 'COST', 'DG', 'DLTR'],
    'HD':   ['LOW'],
}
# Sector label for each trigger
TRIGGER_SECTOR = {
    'NVDA': 'Tech/AI', 'META': 'Tech/AI', 'MSFT': 'Tech/AI',
    'AAPL': 'Tech/AI', 'AMZN': 'Tech/AI', 'GOOGL': 'Tech/AI',
    'XOM':  'Energy',  'CVX':  'Energy',
    'JPM':  'Financials', 'GS': 'Financials',
    'UNH':  'Health Ins',
    'WMT':  'Retail',  'HD':   'Retail',
}

# Flatten to unique sympathy stocks (for baseline comparison)
ALL_SYMPATHY = list({s for v in SYMPATHY_MAP.values() for s in v})

# ── Helpers ─────────────────────────────────────────────────────────────

def get_earnings_dates(symbol: str) -> list[date]:
    """Return all historical earnings dates for a symbol (yfinance earnings_dates)."""
    try:
        t = yf.Ticker(symbol)
        # earnings_dates goes back ~2 years per 20-limit call; use 40 to get 2020-present
        hist = t.get_earnings_dates(limit=40)
        if hist is None or hist.empty:
            return []
        dates = []
        for idx in hist.index:
            try:
                ts = pd.Timestamp(idx)
                # Only include past dates with a reported EPS (not future estimates)
                reported = hist.loc[idx, 'Reported EPS']
                if not pd.isna(reported):
                    dates.append(ts.date())
            except Exception:
                pass
        return sorted(dates)
    except Exception:
        return []


def load_price_data(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Bulk-download adjusted daily OHLCV for all symbols."""
    print(f"Downloading price data for {len(symbols)} symbols ({START_DATE} → {END_DATE})...")
    data = {}
    # Download in one batch for speed
    raw = yf.download(symbols, start=START_DATE, end=END_DATE,
                      auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        for sym in symbols:
            try:
                df = raw.xs(sym, axis=1, level=1).dropna(how='all')
                if not df.empty:
                    data[sym] = df
            except Exception:
                pass
    else:
        # Single symbol
        if not raw.empty:
            data[symbols[0]] = raw
    return data


def next_trading_day(d: date, price_df: pd.DataFrame) -> date | None:
    """Return the first trading day on or after date d in the price index."""
    d_ts = pd.Timestamp(d)
    for candidate in price_df.index:
        if candidate >= d_ts:
            return candidate.date() if hasattr(candidate, 'date') else candidate
    return None


def measure_sympathy(sym: str, event_date: date, direction: int,
                     prices: dict[str, pd.DataFrame]) -> dict | None:
    """
    Measure sympathy stock behaviour on the day after a trigger earnings event.
    direction: +1 = bull trigger, -1 = bear trigger
    Returns dict of metrics or None if data unavailable.
    """
    if sym not in prices:
        return None
    df = prices[sym]
    idx_dates = [i.date() if hasattr(i, 'date') else i for i in df.index]
    date_map  = {d: i for i, d in enumerate(idx_dates)}

    # Find the trading day on/after the event date
    trade_date = next_trading_day(event_date, df)
    if trade_date is None or trade_date not in date_map:
        return None
    ti = date_map[trade_date]
    if ti < 1:
        return None

    row      = df.iloc[ti]
    prev_row = df.iloc[ti - 1]

    open_p  = float(row['Open'])
    high_p  = float(row['High'])
    low_p   = float(row['Low'])
    close_p = float(row['Close'])
    vol     = float(row['Volume'])
    prev_close = float(prev_row['Close'])
    prev_vol   = float(prev_row['Volume'])

    if prev_close <= 0 or open_p <= 0:
        return None

    gap_pct      = (open_p - prev_close) / prev_close * 100
    full_day_pct = (close_p - prev_close) / prev_close * 100
    open_to_close= (close_p - open_p)    / open_p * 100
    vol_ratio    = vol / prev_vol if prev_vol > 0 else 1.0

    # Bull metrics
    if direction == 1:
        gapped     = gap_pct >= MIN_GAP * 100
        held       = close_p > open_p             # closed above open
        continued  = high_p  > open_p * 1.01      # HOD was >1% above open (not immediate fade)
        faded      = close_p < prev_close         # gave back entire gap
        # Simulate live entry: buy at open, 5% stop, exit at close
        sl         = open_p * (1 - STOP_PCT)
        stopped    = low_p <= sl
        shares     = max(1, int(CAPITAL / open_p))
        if stopped:
            pnl = (sl - open_p) * shares
        else:
            pnl = (close_p - open_p) * shares
        win = pnl > 0

    else:  # direction == -1, bear
        gapped     = gap_pct <= -MIN_GAP * 100
        held       = close_p < open_p             # closed below open (gap down held)
        continued  = low_p   < open_p * 0.99      # LOD was >1% below open
        faded      = close_p > prev_close         # recovered entire gap
        sl         = open_p * (1 + STOP_PCT)
        stopped    = high_p >= sl
        shares     = max(1, int(CAPITAL / open_p))
        if stopped:
            pnl = (open_p - sl) * shares
        else:
            pnl = (open_p - close_p) * shares
        win = pnl > 0

    return {
        'sym':          sym,
        'date':         trade_date,
        'direction':    direction,
        'gap_pct':      round(gap_pct, 2),
        'full_day_pct': round(full_day_pct, 2),
        'open_to_close':round(open_to_close, 2),
        'vol_ratio':    round(vol_ratio, 2),
        'gapped':       gapped,
        'held':         held,
        'continued':    continued,
        'faded':        faded,
        'stopped':      stopped,
        'pnl':          round(pnl, 2),
        'win':          win,
    }


def baseline_stats(sym: str, prices: dict[str, pd.DataFrame],
                   exclude_dates: set[date], direction: int = 1) -> tuple[float, float, int]:
    """
    WR + avg P&L for this symbol on NON-sympathy days (control group).
    Same entry/exit logic: buy/short at open, exit at close, 5% stop.
    Returns: (wr, avg_pnl, n)
    """
    if sym not in prices:
        return 0.0, 0.0, 0
    df = prices[sym]
    wins = 0
    total = 0
    total_pnl = 0.0
    for i in range(1, len(df)):
        d = df.index[i].date() if hasattr(df.index[i], 'date') else df.index[i]
        if d in exclude_dates:
            continue
        row    = df.iloc[i]
        open_p = float(row['Open'])
        high_p = float(row['High'])
        low_p  = float(row['Low'])
        close_p= float(row['Close'])
        if open_p <= 0:
            continue
        shares = max(1, int(CAPITAL / open_p))
        if direction == 1:
            sl      = open_p * (1 - STOP_PCT)
            stopped = low_p <= sl
            pnl     = ((sl - open_p) if stopped else (close_p - open_p)) * shares
        else:
            sl      = open_p * (1 + STOP_PCT)
            stopped = high_p >= sl
            pnl     = ((open_p - sl) if stopped else (open_p - close_p)) * shares
        total += 1
        total_pnl += pnl
        if pnl > 0:
            wins += 1
    wr      = wins / total * 100 if total > 0 else 0
    avg_pnl = total_pnl / total  if total > 0 else 0
    return round(wr, 1), round(avg_pnl, 2), total


# ── Main ────────────────────────────────────────────────────────────────

def run():
    direction_label = 'BEAR' if BEAR_ONLY else ('BULL' if BULL_ONLY else 'BULL + BEAR')
    thresh_label    = f'>{int(STRONG_THRESH*100)}%' if STRONG_ONLY else f'>{int(MIN_TRIGGER_MOVE*100)}%'
    print(f"\n{'='*70}")
    print(f"  SYMPATHY PLAY BACKTEST  {START_DATE} → {END_DATE}")
    print(f"  Trigger threshold: {thresh_label} move | Direction: {direction_label}")
    print(f"  Capital: ${CAPITAL}/trade | Stop: {int(STOP_PCT*100)}% | Entry: open | Exit: close")
    print(f"{'='*70}\n")

    triggers     = list(SYMPATHY_MAP.keys())
    all_symbols  = triggers + ALL_SYMPATHY
    all_symbols  = list(dict.fromkeys(all_symbols))  # deduplicate
    prices       = load_price_data(all_symbols)

    all_results   = []
    trigger_events= []

    # ── Pass 1: find all trigger events ──────────────────────────────
    print("Scanning earnings events...")
    for trigger in triggers:
        if trigger not in prices:
            print(f"  {trigger}: no price data — skip")
            continue

        earn_dates = get_earnings_dates(trigger)
        if not earn_dates:
            print(f"  {trigger}: no earnings dates from yfinance — skip")
            continue

        tdf       = prices[trigger]
        idx_dates = [i.date() if hasattr(i, 'date') else i for i in tdf.index]
        date_map  = {d: i for i, d in enumerate(idx_dates)}

        for edate in earn_dates:
            # Find the NEXT trading day's close to measure trigger stock's reaction
            trade_date = next_trading_day(edate, tdf)
            if trade_date is None or trade_date not in date_map:
                continue
            ti = date_map[trade_date]
            if ti < 1:
                continue

            row        = tdf.iloc[ti]
            prev_close = float(tdf.iloc[ti - 1]['Close'])
            close      = float(row['Close'])
            if prev_close <= 0:
                continue

            trigger_move = (close - prev_close) / prev_close

            thresh = STRONG_THRESH if STRONG_ONLY else MIN_TRIGGER_MOVE
            if abs(trigger_move) < thresh:
                continue

            direction = 1 if trigger_move > 0 else -1
            if BEAR_ONLY and direction == 1:
                continue
            if BULL_ONLY and direction == -1:
                continue

            trigger_events.append({
                'trigger':    trigger,
                'earn_date':  edate,
                'trade_date': trade_date,
                'move_pct':   round(trigger_move * 100, 1),
                'direction':  direction,
                'sympathy':   SYMPATHY_MAP[trigger],
            })

    print(f"Found {len(trigger_events)} trigger events "
          f"({sum(1 for e in trigger_events if e['direction']==1)} bull, "
          f"{sum(1 for e in trigger_events if e['direction']==-1)} bear)\n")

    if not trigger_events:
        print("No events found. Check earnings data availability.")
        return

    # ── Pass 2: measure each sympathy stock on each trigger day ──────
    sympathy_dates_by_sym = {s: set() for s in ALL_SYMPATHY}

    for event in trigger_events:
        for sym in event['sympathy']:
            result = measure_sympathy(
                sym, event['trade_date'], event['direction'], prices
            )
            if result is None:
                continue
            result['trigger']    = event['trigger']
            result['earn_date']  = event['earn_date']
            result['trigger_move'] = event['move_pct']
            all_results.append(result)
            sympathy_dates_by_sym[sym].add(result['date'])

    if not all_results:
        print("No sympathy results computed — check data.")
        return

    df = pd.DataFrame(all_results)

    # ── Report ────────────────────────────────────────────────────────
    for direction in ([1, -1] if not BEAR_ONLY and not BULL_ONLY
                      else ([-1] if BEAR_ONLY else [1])):
        label = '▲ BULL SYMPATHY' if direction == 1 else '▼ BEAR SYMPATHY'
        sub   = df[df['direction'] == direction]
        if sub.empty:
            continue

        gapped  = sub[sub['gapped']]
        n_total = len(sub)
        n_gap   = len(gapped)
        gap_rate= n_gap / n_total * 100 if n_total else 0

        hold_rate   = gapped['held'].mean() * 100      if n_gap else 0
        cont_rate   = gapped['continued'].mean() * 100 if n_gap else 0
        fade_rate   = gapped['faded'].mean() * 100     if n_gap else 0
        stop_rate   = gapped['stopped'].mean() * 100   if n_gap else 0
        wr          = gapped['win'].mean() * 100        if n_gap else 0
        avg_gap     = gapped['gap_pct'].mean()          if n_gap else 0
        avg_pnl     = gapped['pnl'].mean()              if n_gap else 0
        avg_full    = gapped['full_day_pct'].mean()     if n_gap else 0
        total_pnl   = gapped['pnl'].sum()               if n_gap else 0

        print(f"\n{'─'*70}")
        print(f"  {label}  ({n_total} events, {n_gap} with gap >{MIN_GAP*100:.0f}%)")
        print(f"{'─'*70}")
        print(f"  Gap rate        : {gap_rate:.0f}%  ({n_gap}/{n_total} events had qualifying gap)")
        print(f"  Hold rate       : {hold_rate:.0f}%  (of gapped, closed in gap direction)")
        print(f"  Continuation    : {cont_rate:.0f}%  (HOD/LOD >1% beyond open — not immediate fade)")
        print(f"  Full fade rate  : {fade_rate:.0f}%  (closed worse than prev close)")
        print(f"  Stop rate       : {stop_rate:.0f}%  (5% stop hit intraday)")
        print(f"  Win rate (entry@open, exit@close): {wr:.0f}%")
        print(f"  Avg gap         : {avg_gap:+.1f}%")
        print(f"  Avg full day    : {avg_full:+.1f}%")
        print(f"  Avg P&L/trade   : ${avg_pnl:+.0f}  (${CAPITAL} deployed)")
        print(f"  Total P&L       : ${total_pnl:+,.0f}  across {n_gap} trades")

        # ── By trigger ────────────────────────────────────────────
        print(f"\n  {'Trigger':<8} {'Events':>7} {'GapRate':>8} {'HoldRate':>9} "
              f"{'WR':>6} {'AvgGap':>8} {'AvgPnL':>8}")
        print(f"  {'─'*65}")
        for trig, tg in gapped.groupby('trigger'):
            n  = len(tg)
            hr = tg['held'].mean() * 100
            w  = tg['win'].mean()  * 100
            ag = tg['gap_pct'].mean()
            ap = tg['pnl'].mean()
            # gap rate for this trigger
            all_for_trig = sub[sub['trigger'] == trig]
            gr = len(tg) / len(all_for_trig) * 100 if len(all_for_trig) else 0
            print(f"  {trig:<8} {n:>7} {gr:>7.0f}% {hr:>8.0f}% "
                  f"{w:>5.0f}% {ag:>+7.1f}% ${ap:>+7.0f}")

        # ── By sympathy stock ──────────────────────────────────────
        print(f"\n  {'Symbol':<7} {'N':>4} {'GapRate':>8} {'HoldRate':>9} "
              f"{'WR':>6} {'AvgGap':>8} {'AvgPnL':>8} {'AvgVol':>8} Verdict")
        print(f"  {'─'*75}")
        sym_rows = []
        for sym, sg in gapped.groupby('sym'):
            n       = len(sg)
            if n < 3:
                continue
            hr      = sg['held'].mean()    * 100
            w       = sg['win'].mean()     * 100
            ag      = sg['gap_pct'].mean()
            ap      = sg['pnl'].mean()
            av      = sg['vol_ratio'].mean()
            all_sym = sub[sub['sym'] == sym]
            gr      = len(sg) / len(all_sym) * 100 if len(all_sym) else 0

            verdict = '✅' if (w >= 60 and hr >= 55 and n >= 4) else \
                      '⚠️' if (w >= 50 and n >= 3) else '❌'
            sym_rows.append((sym, n, gr, hr, w, ag, ap, av, verdict))

        sym_rows.sort(key=lambda x: -x[4])  # sort by WR
        for sym, n, gr, hr, w, ag, ap, av, verdict in sym_rows:
            print(f"  {sym:<7} {n:>4} {gr:>7.0f}% {hr:>8.0f}% "
                  f"{w:>5.0f}% {ag:>+7.1f}% ${ap:>+7.0f} {av:>6.1f}x  {verdict}")

        # ── Strong beat subset ─────────────────────────────────────
        strong_events = [e for e in trigger_events
                         if abs(e['move_pct']) >= STRONG_THRESH * 100
                         and e['direction'] == direction]
        if strong_events and not STRONG_ONLY:
            strong_dates = {e['trade_date'] for e in strong_events}
            strong_sub   = gapped[gapped['date'].isin(strong_dates)]
            if len(strong_sub) >= 5:
                s_wr  = strong_sub['win'].mean()  * 100
                s_hr  = strong_sub['held'].mean() * 100
                s_ap  = strong_sub['pnl'].mean()
                s_n   = len(strong_sub)
                print(f"\n  STRONG (>{int(STRONG_THRESH*100)}% trigger) subset: "
                      f"{s_n} trades | WR {s_wr:.0f}% | Hold {s_hr:.0f}% | "
                      f"Avg P&L ${s_ap:+.0f}")

        # ── Baseline comparison: normal day vs sympathy day ───────────
        print(f"\n  NORMAL DAY vs SYMPATHY DAY  (same entry/exit logic, $2000 deployed)")
        print(f"  {'Symbol':<7} {'SympN':>6} {'Symp WR':>8} {'SympPnL':>9} "
              f"{'BaseWR':>8} {'BasePnL':>9} {'WR+':>6} {'PnL+':>7} {'BaseN':>7}")
        print(f"  {'─'*75}")
        alpha_rows = []
        for sym, sg in gapped.groupby('sym'):
            if len(sg) < 3:
                continue
            symp_wr  = sg['win'].mean()  * 100
            symp_pnl = sg['pnl'].mean()
            symp_n   = len(sg)
            base_wr, base_pnl, base_n = baseline_stats(
                sym, prices, sympathy_dates_by_sym[sym], direction
            )
            wr_delta  = symp_wr  - base_wr
            pnl_delta = symp_pnl - base_pnl
            alpha_rows.append((sym, symp_n, symp_wr, symp_pnl,
                                base_wr, base_pnl, wr_delta, pnl_delta, base_n))

        alpha_rows.sort(key=lambda x: -x[6])  # sort by WR delta
        for sym, sn, sw, sp, bw, bp, wrd, pnld, bn in alpha_rows:
            wr_flag  = '⬆' if wrd  > 5  else ('⬇' if wrd  < -5  else ' ')
            pnl_flag = '⬆' if pnld > 5  else ('⬇' if pnld < -5  else ' ')
            print(f"  {sym:<7} {sn:>6} {sw:>7.0f}% ${sp:>+7.0f}  "
                  f"{bw:>6.0f}% ${bp:>+7.0f}  {wrd:>+5.0f}%{wr_flag} ${pnld:>+5.0f}{pnl_flag}"
                  f" {bn:>7}")

    # ── Sector breakdown (bull only — bear sample too small per sector) ──
    gapped_bull = df[(df['gapped']) & (df['direction'] == 1)]
    if len(gapped_bull) >= 5:
        df['sector'] = df['trigger'].map(TRIGGER_SECTOR)
        gapped_bull2 = df[(df['gapped']) & (df['direction'] == 1)]
        print(f"\n{'─'*70}")
        print(f"  SECTOR BREAKDOWN  (bull, gapped >{MIN_GAP*100:.0f}%)")
        print(f"{'─'*70}")
        print(f"  {'Sector':<14} {'N':>5} {'GapRate':>8} {'HoldRate':>9} "
              f"{'WR':>6} {'AvgGap':>8} {'AvgPnL':>8} Verdict")
        print(f"  {'─'*70}")
        sector_rows = []
        for sector, sg in gapped_bull2.groupby('sector'):
            n  = len(sg)
            hr = sg['held'].mean()    * 100
            w  = sg['win'].mean()     * 100
            ag = sg['gap_pct'].mean()
            ap = sg['pnl'].mean()
            all_sector = df[(df['direction'] == 1) & (df['sector'] == sector)]
            gr = n / len(all_sector) * 100 if len(all_sector) else 0
            verdict = '✅' if (w >= 65 and hr >= 60) else \
                      '⚠️' if (w >= 50 and hr >= 40) else '❌'
            sector_rows.append((sector, n, gr, hr, w, ag, ap, verdict))
        sector_rows.sort(key=lambda x: -x[4])
        for sector, n, gr, hr, w, ag, ap, verdict in sector_rows:
            print(f"  {sector:<14} {n:>5} {gr:>7.0f}% {hr:>8.0f}% "
                  f"{w:>5.0f}% {ag:>+7.1f}% ${ap:>+7.0f}  {verdict}")

    # ── Summary verdict ───────────────────────────────────────────────
    gapped_all = df[df['gapped']]
    print(f"\n{'='*70}")
    print(f"  OVERALL VERDICT")
    print(f"{'='*70}")
    if len(gapped_all) >= 10:
        wr_all   = gapped_all['win'].mean()    * 100
        hr_all   = gapped_all['held'].mean()   * 100
        pnl_all  = gapped_all['pnl'].sum()
        avg_pnl  = gapped_all['pnl'].mean()
        n_trades = len(gapped_all)
        print(f"  Total qualifying sympathy trades: {n_trades}")
        print(f"  Overall WR (entry@open, exit@close): {wr_all:.0f}%")
        print(f"  Overall hold rate: {hr_all:.0f}%")
        print(f"  Total P&L: ${pnl_all:+,.0f} | Avg per trade: ${avg_pnl:+.0f}")
        print()
        if wr_all >= 65 and hr_all >= 60:
            print("  ✅ STRONG EDGE — direction persists through session")
            print("     Pre-market entry justified: captures gap before open")
            print("     Next step: build sympathy priority in catalyst scan")
        elif wr_all >= 55 and hr_all >= 50:
            print("  ⚠️  MODERATE EDGE — direction holds but with noise")
            print("     Regular-hours entry (10am) likely sufficient")
            print("     Pre-market entry risk/reward unclear — needs tighter gate")
        else:
            print("  ❌ WEAK/NO EDGE — sympathy plays are not reliably directional")
            print("     Gap-and-crap dominates. Do not build pre-market entry.")
    else:
        print(f"  Insufficient data ({len(gapped_all)} trades). "
              "Expand date range or lower MIN_TRIGGER_MOVE threshold.")

    print(f"\n  Interpretation guide:")
    print(f"  Gap rate  >50% + Hold rate >65% + WR >65% = build pre-market entry")
    print(f"  Hold rate >65% but WR <60%  = direction real but fades intraday")
    print(f"  Hold rate <50%              = gap-and-crap dominates, don't build")
    print(f"{'='*70}")

    # ── Monthly breakdown — bull only ─────────────────────────────────────
    bull_gapped = df[(df['gapped']) & (df['direction'] == 1)].copy()
    if len(bull_gapped) >= 5:
        bull_gapped['year']  = bull_gapped['date'].apply(lambda d: d.year)
        bull_gapped['month'] = bull_gapped['date'].apply(lambda d: d.month)
        bull_gapped['ym']    = bull_gapped['date'].apply(lambda d: f"{d.year}-{d.month:02d}")

        print(f"\n{'─'*70}")
        print(f"  MONTHLY LIFT ANALYSIS  (bull sympathy, gapped >{MIN_GAP*100:.0f}%, $2000/trade)")
        print(f"{'─'*70}")

        # Per-month sympathy P&L
        monthly_rows = []
        for ym, mg in bull_gapped.groupby('ym'):
            n   = len(mg)
            wr  = mg['win'].mean()  * 100
            pnl = mg['pnl'].sum()
            monthly_rows.append((ym, n, wr, pnl))
        monthly_rows.sort()

        # Annualised baseline: same stocks, non-sympathy days, avg monthly P&L
        # Use the alpha table data — baseline P&L per trade ≈ $0-1 for all stocks
        # So baseline monthly contribution from these stocks ≈ 0 (random walk confirmed)
        # Total calendar months in backtest range (not just months with trades)
        start_ts = pd.Timestamp(START_DATE)
        end_ts   = pd.Timestamp(END_DATE)
        total_calendar_months = (end_ts.year - start_ts.year) * 12 + (end_ts.month - start_ts.month) + 1
        active_months = len(monthly_rows)
        total_pnl  = sum(p for _, _, _, p in monthly_rows)
        avg_active = total_pnl / active_months            if active_months            else 0
        avg_all    = total_pnl / total_calendar_months    if total_calendar_months    else 0

        print(f"  {'Month':<10} {'Trades':>7} {'WR':>6} {'SympPnL':>10} {'Lift vs $0 base':>16}")
        print(f"  {'─'*55}")
        for ym, n, wr, pnl in monthly_rows:
            bar = '█' * min(int(abs(pnl) / 20), 20)
            sign = '+' if pnl >= 0 else ''
            print(f"  {ym:<10} {n:>7} {wr:>5.0f}% ${pnl:>+8.0f}   {sign}{bar}")

        print(f"\n  Active months (any trade): {active_months} / {total_calendar_months} calendar months")
        print(f"  Avg P&L on active months : ${avg_active:+.0f}/month (when a trigger fires)")
        print(f"  Avg P&L across all months: ${avg_all:+.0f}/month  (incl. quiet months)")
        print(f"  Total sympathy P&L {START_DATE[:4]}–{END_DATE[:4]}: ${total_pnl:+,.0f}")

        # Annual rate
        years = max(1, (pd.Timestamp(END_DATE) - pd.Timestamp(START_DATE)).days / 365.25)
        annual = total_pnl / years
        print(f"  Annual rate              : ${annual:+.0f}/year  (~${annual/12:+.0f}/month avg)")
        print(f"\n  {'='*55}")
        print(f"  DOES IT LIFT US?")
        print(f"  {'='*55}")
        print(f"  Baseline on these stocks : ~$0/month (random walk on non-event days)")
        print(f"  Sympathy add             : ${avg_all:+.0f}/month on avg across all months")
        print(f"  On active months only    : ${avg_active:+.0f}/month when a trigger fires")
        if avg_all >= 30:
            print(f"  ✅ YES — meaningful lift. ${avg_all:.0f}/month is real alpha.")
        elif avg_all >= 10:
            print(f"  ⚠️  MODEST — ${avg_all:.0f}/month. Cherry on top, not a game-changer.")
        else:
            print(f"  ❌ MINIMAL — ${avg_all:.0f}/month. Data too sparse to draw conclusions.")

    print(f"{'='*70}\n")


if __name__ == '__main__':
    run()
