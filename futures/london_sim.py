"""
futures/london_sim.py — London session (3:00am–9:00am ET) MNQ strategy simulator.

Fully independent of sim_replay.py. Pluggable:
  Add live trading:  set LONDON_ENABLED = True  in futures_trader.py
  Remove entirely:   set LONDON_ENABLED = False, delete this file

London session definition:
  IB formation:   3:00am–4:00am ET  (must be CLEAN — not trending)
  Entry gate:     4:00am ET         (IB fully formed, no entries during formation)
  EOD hard close: 9:00am ET         (30 min before NY RTH, avoids NY open chaos)
  DLL:            $250 separate from NY (London learns independently)
  Max trades:     2

Three signals tested independently and in every combination:
  A — IB range break:    price CLOSES above London IB high (LONG) or below IB low (SHORT)
  B — Overnight VWAP:    price above cumulative 7pm→now VWAP (LONG) or below (SHORT)
  C — Overnight bias:    overnight pos ≥ 0.85 → LONG, ≤ 0.20 → SHORT (strong directional close)

Grade system (London-specific A+ required — no A entries here):
  base=50, A=+30, B=+20, C=+20
  A alone:   50+30 = 80 → A+ (range break alone is sufficient — sniper shot)
  B alone:   50+20 = 70 → A  (skip — VWAP alone insufficient)
  C alone:   50+20 = 70 → A  (skip — bias alone insufficient)
  AB, AC:    100      → A+
  BC:        90       → A+
  ABC:       120      → A+

★ CALIBRATE labels mark values not yet validated by data. Locked values are marked DATA.

Champion config (Jun 15 2026): stop=2.0×ATR, target=6.0×ATR, BE=0.10×ATR
  5.5yr result: $22,045 / 42.7% WR / MaxDD $287 / never lost a year
  vs old baseline (1.5/3.0/0.50): $10,918 → +102% improvement

Usage:
  venv/bin/python futures/london_sim.py --signals A --no-ib-clean  # champion, all data
  venv/bin/python futures/london_sim.py --signals A --no-ib-clean --start 2025-01-01  # recent
  venv/bin/python futures/london_sim.py --compare --no-ib-clean    # all 7 signal combos
  venv/bin/python futures/london_sim.py --stats                    # data distribution
  venv/bin/python futures/london_sim.py --signals A --no-ib-clean --detail  # trade-by-trade
  venv/bin/python futures/london_sim.py --be-mult 0.30             # test BE sensitivity
"""

import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datetime as _dt
import pandas as pd

from futures.collect_bars import load_bars

# ── Instrument ─────────────────────────────────────────────────────────────────

POINT_VALUE   = 2.00     # MNQ: $2/point
TICK_SIZE     = 0.25     # 0.25 index points
TICK_VALUE    = 0.50     # $0.50 per tick
COMMISSION_RT = 1.24     # round-turn per contract

# ── Session boundaries (all times are ET, index already converted by load_bars) ─

LONDON_IB_START  = _dt.time( 3,  0)   # London equity open 8am GMT = 3am ET
LONDON_IB_END    = _dt.time( 4,  0)   # IB formation window close
LONDON_ENTRY     = _dt.time( 4,  0)   # first allowed entry
LONDON_EOD       = _dt.time( 9,  0)   # hard close before NY RTH

OVN_START_HOUR   = 19                  # overnight VWAP/range: 7pm ET prev calendar day

# ── IB quality gates ── ★ CALIBRATE after first --stats run ──────────────────

LONDON_MIN_IB_BARS  = 6        # min bars in 3:00–4:00 window (5-min → 12 max)
LONDON_MIN_IB_RANGE = 20.0     # ★ CALIBRATE: minimum IB width in points
LONDON_IB_CLEAN     = False    # DATA: trending IB close → continuation (+175% P&L vs clean filter)

# ── Risk ──────────────────────────────────────────────────────────────────────

MAX_DAILY_TRADES   = 2
MAX_DAILY_LOSS     = 1250.0   # $5k account, 25% DLL (CLI: --dll)
MAX_RISK_PER_TRADE = 250.0    # $5k account, 5% risk/trade (CLI: --risk)
MAX_CONTRACTS      = 2
MIN_RR             = 2.0
MAX_STOP_PTS       = 150.0    # skip if ATR-based stop > 150pts

# ── ATR multiples ── ★ CALIBRATE (same as NY to start) ───────────────────────

STOP_ATR_MULT   = 2.0    # DATA Jun 15: wider stop → less noise, higher WR. 1.5→2.0 +$5,591/5.5yr
TARGET_ATR_MULT = 6.0    # DATA Jun 15: pure trail — target almost never fires. 3.0→6.0 drives improvement

# ── ATR-relative trail thresholds ── ★ CALIBRATE ─────────────────────────────
# Using ATR-relative (not absolute pts) so these scale with London's smaller moves.
# At London ATR ≈ 30pts: BE@15pts, wide-trail activates@30pts(trails9pts behind peak),
# tight-trail activates@45pts(trails6pts behind peak).

BE_ATR_MULT         = 0.10   # DATA Jun 15: protect entry at +5pts. Fakeouts exit flat vs -$200 stop. +$5,536/5.5yr
TRAIL_WIDE_ATR      = 1.00   # ★ CALIBRATE: +1.0×ATR → activate wide trail (not yet tuned)
TRAIL_WIDE_GAP_ATR  = 0.30   # ★ CALIBRATE: trail 0.30×ATR behind peak (not yet tuned)
TRAIL_TIGHT_ATR     = 1.50   # ★ CALIBRATE: +1.5×ATR → tighten trail (not yet tuned)
TRAIL_TIGHT_GAP_ATR = 0.20   # ★ CALIBRATE: trail 0.20×ATR behind peak (not yet tuned)

# ── No-move exit ── ★ CALIBRATE ──────────────────────────────────────────────

NO_MOVE_MINUTES = 60      # ★ CALIBRATE: 60 min (London is a 5-hr session)
NO_MOVE_MAX_PTS = 20.0    # above this → trade is moving, let it run
NO_MOVE_MIN_PTS = -10.0   # below this → SL will manage it

# ── Signal scoring ── ★ CALIBRATE weights after first run ─────────────────────

SCORE_BASE   = 50
SCORE_A      = 30    # IB range break — fires alone (50+30=80=A+)
SCORE_B      = 20    # overnight VWAP — needs A or C as co-signal
SCORE_C      = 20    # overnight bias strength — needs A or B as co-signal
GRADE_A_PLUS = 80    # minimum for entry
GRADE_A      = 65    # A but not A+ → skip in London

# ── Overnight bias thresholds (match sim_replay.py) ───────────────────────────

_OVN_COMPRESS = 50.0   # overnight range < 50pts → both directions allowed
_OVN_SKIP_LO  = 0.20   # London open in [0.20, 0.40) → ambiguous, skip
_OVN_SKIP_HI  = 0.40
_OVN_TREND_HI = 0.85   # pos ≥ 0.85 → overnight LONG bias
_OVN_TREND_LO = 0.20   # pos ≤ 0.20 → overnight SHORT bias

# ── NYSE/CME holidays 2021-2026 ───────────────────────────────────────────────

US_HOLIDAYS = {
    _dt.date(2021,  1,  1), _dt.date(2021,  1, 18), _dt.date(2021,  2, 15),
    _dt.date(2021,  4,  2), _dt.date(2021,  5, 31), _dt.date(2021,  7,  5),
    _dt.date(2021,  9,  6), _dt.date(2021, 11, 25), _dt.date(2021, 12, 24),
    _dt.date(2022,  1, 17), _dt.date(2022,  2, 21), _dt.date(2022,  4, 15),
    _dt.date(2022,  5, 30), _dt.date(2022,  6, 19), _dt.date(2022,  7,  4),
    _dt.date(2022,  9,  5), _dt.date(2022, 11, 24), _dt.date(2022, 12, 26),
    _dt.date(2023,  1,  2), _dt.date(2023,  1, 16), _dt.date(2023,  2, 20),
    _dt.date(2023,  4,  7), _dt.date(2023,  5, 29), _dt.date(2023,  6, 19),
    _dt.date(2023,  7,  4), _dt.date(2023,  9,  4), _dt.date(2023, 11, 23),
    _dt.date(2023, 12, 25),
    _dt.date(2024,  1,  1), _dt.date(2024,  1, 15), _dt.date(2024,  2, 19),
    _dt.date(2024,  3, 29), _dt.date(2024,  5, 27), _dt.date(2024,  6, 19),
    _dt.date(2024,  7,  4), _dt.date(2024,  9,  2), _dt.date(2024, 11, 28),
    _dt.date(2024, 12, 25),
    _dt.date(2025,  1,  1), _dt.date(2025,  1,  9), _dt.date(2025,  1, 20),
    _dt.date(2025,  2, 17), _dt.date(2025,  4, 18), _dt.date(2025,  5, 26),
    _dt.date(2025,  6, 19), _dt.date(2025,  7,  4), _dt.date(2025,  9,  1),
    _dt.date(2025, 11, 27), _dt.date(2025, 12, 25),
    _dt.date(2026,  1,  1), _dt.date(2026,  1, 19), _dt.date(2026,  2, 16),
    _dt.date(2026,  4,  3), _dt.date(2026,  5, 25), _dt.date(2026,  6, 19),
    _dt.date(2026,  7,  3), _dt.date(2026,  9,  7), _dt.date(2026, 11, 26),
    _dt.date(2026, 12, 25),
}


# ── Utilities ──────────────────────────────────────────────────────────────────

def _tick_round(v: float) -> float:
    return round(round(v / TICK_SIZE) * TICK_SIZE, 2)


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < 2:
        return 10.0
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low']  - df['close'].shift()).abs(),
    ], axis=1).max(axis=1)
    v = tr.rolling(period, min_periods=2).mean().iloc[-1]
    return float(v) if pd.notna(v) and v > 0 else 10.0


def pnl_dollars(entry: float, exit_p: float, side: str, contracts: int) -> float:
    pts = (exit_p - entry) if side == 'LONG' else (entry - exit_p)
    return round(pts * POINT_VALUE * contracts - COMMISSION_RT, 2)


def calc_sl_target(price: float, atr: float, side: str,
                   stop_mult: float, target_mult: float) -> tuple[float, float]:
    if side == 'LONG':
        sl     = _tick_round(price - atr * stop_mult)
        target = _tick_round(price + atr * target_mult)
    else:
        sl     = _tick_round(price + atr * stop_mult)
        target = _tick_round(price - atr * target_mult)
    return sl, target


def calc_contracts(price: float, sl: float, risk_pt: float = MAX_RISK_PER_TRADE) -> int:
    stop_pts = abs(price - sl)
    if stop_pts <= 0:
        return 1
    risk_per_c = (stop_pts / TICK_SIZE) * TICK_VALUE
    return max(1, min(int(risk_pt / risk_per_c), MAX_CONTRACTS))


# ── Overnight bias (London-specific) ──────────────────────────────────────────

def compute_overnight_bias_london(
    all_bars: pd.DataFrame,
    trade_date: _dt.date,
) -> tuple[str, bool, float]:
    """
    Compute overnight directional bias for the London session.

    Range:    prev calendar day 7pm ET  →  today 3am ET
    Position: where does the London open (3:00am) sit within overnight H-L?

    Returns (bias, skip_day, ovn_pos):
      bias     = 'LONG' | 'SHORT' | 'BOTH'
      skip_day = True  → overnight range compressed, no directional edge
      ovn_pos  = 0.0–1.0 position within range (-1.0 = unavailable)
    """
    prev_cal = trade_date - _dt.timedelta(days=1)

    # Index is already in ET. Use date/time component comparisons directly.
    night_mask = (
        ((all_bars.index.date == prev_cal) &
         (all_bars.index.time >= _dt.time(OVN_START_HOUR, 0))) |
        ((all_bars.index.date == trade_date) &
         (all_bars.index.time <  _dt.time(3, 0)))
    )
    night_bars = all_bars[night_mask]

    if len(night_bars) < 4:
        return 'BOTH', False, -1.0

    ovn_high  = float(night_bars['high'].max())
    ovn_low   = float(night_bars['low'].min())
    ovn_range = ovn_high - ovn_low

    if ovn_range < _OVN_COMPRESS:
        # Compressed overnight — no directional bias but still tradeable on Signal A/B.
        # Signal C won't fire (ovn_pos=-1.0 fails both sig_c checks naturally).
        return 'BOTH', False, -1.0

    # London open = first bar at 3:00am today
    lon_open_mask = (all_bars.index.date == trade_date) & \
                    (all_bars.index.time >= _dt.time(3, 0)) & \
                    (all_bars.index.time <  _dt.time(3, 10))
    lon_open_bars = all_bars[lon_open_mask]
    if lon_open_bars.empty:
        return 'BOTH', False, -1.0

    lon_open = float(lon_open_bars['open'].iloc[0])
    pos = max(0.0, min(1.0, (lon_open - ovn_low) / ovn_range))

    # Check strong directional bias BEFORE skip zone (LONG/SHORT take priority).
    if pos >= _OVN_TREND_HI:
        return 'LONG', False, round(pos, 3)
    if pos <= _OVN_TREND_LO:
        return 'SHORT', False, round(pos, 3)
    # (0.20, 0.40) → ambiguous zone between SHORT threshold and mid-range → skip
    if _OVN_SKIP_LO < pos < _OVN_SKIP_HI:
        return 'BOTH', True, round(pos, 3)
    # [0.40, 0.85) → price in mid-to-upper overnight range → BOTH allowed
    return 'BOTH', False, round(pos, 3)


# ── Signal scoring ─────────────────────────────────────────────────────────────

def grade_london_entry(
    sig_a: bool,
    sig_b: bool,
    sig_c: bool,
    signals: str,
) -> tuple[int, str]:
    """
    Grade a London entry based on active signals.

    signals:  subset string of 'ABC' controlling which signals are included.
    Returns (score, grade) where grade is 'A+' | 'A' | 'SKIP'.
    """
    score = SCORE_BASE
    if 'A' in signals and sig_a:
        score += SCORE_A
    if 'B' in signals and sig_b:
        score += SCORE_B
    if 'C' in signals and sig_c:
        score += SCORE_C

    if score >= GRADE_A_PLUS:
        return score, 'A+'
    if score >= GRADE_A:
        return score, 'A'
    return score, 'SKIP'


def _setup_label(sig_a: bool, sig_b: bool, sig_c: bool, side: str) -> str:
    parts = []
    if sig_a: parts.append('RNG')   # range break
    if sig_b: parts.append('VWAP')  # VWAP confirmation
    if sig_c: parts.append('BIAS')  # overnight bias
    tag = '+'.join(parts) if parts else 'BASE'
    return f'LON_{side[0]}_{tag}'


# ── Day simulation ─────────────────────────────────────────────────────────────

def simulate_london_day(
    all_bars:        pd.DataFrame,
    trade_date:      _dt.date,
    signals:         str   = 'ABC',
    stop_mult:       float = STOP_ATR_MULT,
    target_mult:     float = TARGET_ATR_MULT,
    ib_clean:        bool  = LONDON_IB_CLEAN,
    min_ib_range:    float = LONDON_MIN_IB_RANGE,
    be_mult:         float = BE_ATR_MULT,
    trail_wide_atr:  float = TRAIL_WIDE_ATR,
    trail_tight_atr: float = TRAIL_TIGHT_ATR,
    ib_align_gate:   bool  = False,
    dll:             float = MAX_DAILY_LOSS,
    risk_per_trade:  float = MAX_RISK_PER_TRADE,
) -> tuple[list[dict], str, float, dict]:
    """
    Simulate one London session.
    Returns (trades, daily_bias, ovn_pos, day_stats).
    day_stats holds diagnostic counts for --stats mode.
    """
    date_str  = trade_date.isoformat()
    day_stats: dict = {
        'date': date_str, 'skip_reason': None,
        'ib_range': 0.0, 'ib_clean': False,
        'bias': 'BOTH', 'ovn_pos': -1.0,
    }

    if trade_date in US_HOLIDAYS:
        day_stats['skip_reason'] = 'holiday'
        return [], 'HOLIDAY', -1.0, day_stats

    # ── Overnight bias ────────────────────────────────────────────────────────
    daily_bias, skip_day, ovn_pos = compute_overnight_bias_london(all_bars, trade_date)
    day_stats['bias']    = daily_bias
    day_stats['ovn_pos'] = ovn_pos

    if skip_day:
        day_stats['skip_reason'] = 'ovn_compress'
        return [], daily_bias, ovn_pos, day_stats

    # ── IB formation bars (3:00–3:55 ET) ─────────────────────────────────────
    day_mask = (all_bars.index.date == trade_date)
    day_all  = all_bars[day_mask]

    ib_mask  = (day_all.index.time >= LONDON_IB_START) & \
               (day_all.index.time <  LONDON_IB_END)
    ib_bars  = day_all[ib_mask]

    if len(ib_bars) < LONDON_MIN_IB_BARS:
        day_stats['skip_reason'] = 'ib_bars_low'
        return [], daily_bias, ovn_pos, day_stats

    ib_high  = float(ib_bars['high'].max())
    ib_low   = float(ib_bars['low'].min())
    ib_range = ib_high - ib_low
    day_stats['ib_range'] = ib_range

    if ib_range < min_ib_range:
        day_stats['skip_reason'] = 'ib_thin'
        return [], daily_bias, ovn_pos, day_stats

    # Always compute IB close position (0.0 = closed at IB low, 1.0 = closed at IB high)
    last_ib_close = float(ib_bars['close'].iloc[-1])
    ib_close_pos  = (last_ib_close - ib_low) / ib_range if ib_range > 0 else 0.5
    day_stats['ib_close_pos'] = round(ib_close_pos, 3)

    # Clean IB: last bar's close must be in the middle 60% of the range.
    # An IB close at an extreme means price TRENDED through the IB hour — not a range.
    if ib_clean and (ib_close_pos > 0.80 or ib_close_pos < 0.20):
        day_stats['skip_reason'] = 'ib_dirty'
        return [], daily_bias, ovn_pos, day_stats
    day_stats['ib_clean'] = True

    # ── Entry bars (4:00–8:55 ET) ─────────────────────────────────────────────
    entry_mask = (day_all.index.time >= LONDON_ENTRY) & \
                 (day_all.index.time <  LONDON_EOD)
    entry_bars = day_all[entry_mask]

    if entry_bars.empty:
        day_stats['skip_reason'] = 'no_entry_bars'
        return [], daily_bias, ovn_pos, day_stats

    # ── Cumulative overnight VWAP (7pm prev_cal → end of IB) for Signal B ────
    # Include IB bars in base so that by 4am the VWAP reflects all overnight history.
    prev_cal  = trade_date - _dt.timedelta(days=1)
    base_mask = (
        ((all_bars.index.date == prev_cal) &
         (all_bars.index.time >= _dt.time(OVN_START_HOUR, 0))) |
        ((all_bars.index.date == trade_date) &
         (all_bars.index.time <  LONDON_ENTRY))
    )
    vwap_base = all_bars[base_mask]

    if not vwap_base.empty:
        tp_v    = (vwap_base['high'] + vwap_base['low'] + vwap_base['close']) / 3
        cum_tpv = float((tp_v * vwap_base['volume']).sum())
        cum_vol = float(vwap_base['volume'].sum())
    else:
        cum_tpv = 0.0
        cum_vol = 0.0

    # ── 2-day ATR from bars BEFORE today's London session ────────────────────
    atr_mask = (all_bars.index.date >= trade_date - _dt.timedelta(days=2)) & \
               (all_bars.index.date <  trade_date)
    atr = compute_atr(all_bars[atr_mask])

    # Signal C is fixed for the whole day (computed once from overnight bias)
    sig_c_bull = (ovn_pos >= _OVN_TREND_HI)
    sig_c_bear = (ovn_pos <= _OVN_TREND_LO and ovn_pos >= 0)

    # ── Bar-by-bar loop ───────────────────────────────────────────────────────
    trades:      list[dict] = []
    position:    dict | None = None
    trade_count: int = 0
    daily_pnl:   float = 0.0

    for i in range(len(entry_bars)):
        bar = entry_bars.iloc[i]
        t   = entry_bars.index[i].time()

        # Extend cumulative VWAP with this bar
        bar_tp  = (float(bar['high']) + float(bar['low']) + float(bar['close'])) / 3
        bar_vol = float(bar['volume'])
        cum_tpv += bar_tp  * bar_vol
        cum_vol += bar_vol
        ovn_vwap = cum_tpv / cum_vol if cum_vol > 0 else None

        # ── Manage open position ──────────────────────────────────────────────
        if position is not None:
            entry_p   = position['entry']
            sl        = position['sl']
            target    = position['target']
            side      = position['side']
            is_short  = (side == 'SHORT')
            contracts = position['contracts']
            p_atr     = position['atr']
            peak      = position['peak']

            exit_price  = None
            exit_reason = None

            # 1. Hard stop (SL from PREVIOUS bar's trail — no same-bar race)
            if not is_short and float(bar['low'])  <= sl:
                exit_price, exit_reason = sl, 'stop'
            elif is_short and float(bar['high']) >= sl:
                exit_price, exit_reason = sl, 'stop'

            # 2. Daily circuit breaker (realized + floating)
            if exit_reason is None:
                cur_pts = (float(bar['close']) - entry_p) if not is_short \
                          else (entry_p - float(bar['close']))
                cur_usd = cur_pts * POINT_VALUE * contracts - COMMISSION_RT
                if daily_pnl + cur_usd <= -dll:
                    exit_price, exit_reason = float(bar['close']), 'dll_circuit'

            # 3. Target hit
            if exit_reason is None:
                if not is_short and float(bar['high']) >= target:
                    exit_price, exit_reason = target, 'target'
                elif is_short and float(bar['low'])  <= target:
                    exit_price, exit_reason = target, 'target'

            # 4. VWAP cross (only if in profit)
            if exit_reason is None and ovn_vwap is not None:
                cur_pts = (float(bar['close']) - entry_p) if not is_short \
                          else (entry_p - float(bar['close']))
                if cur_pts > 0:
                    if not is_short and float(bar['close']) < ovn_vwap:
                        exit_price, exit_reason = float(bar['close']), 'vwap_cross'
                    elif is_short and float(bar['close']) > ovn_vwap:
                        exit_price, exit_reason = float(bar['close']), 'vwap_cross'

            # 5. No-move exit (5-min bars, so 1 bar = 5 min)
            if exit_reason is None:
                bars_open = i - position['entry_idx']
                cur_pts   = (float(bar['close']) - entry_p) if not is_short \
                            else (entry_p - float(bar['close']))
                if (bars_open * 5 >= NO_MOVE_MINUTES and
                        NO_MOVE_MIN_PTS <= cur_pts <= NO_MOVE_MAX_PTS):
                    exit_price, exit_reason = float(bar['close']), 'no_move'

            # 6. EOD hard close
            if exit_reason is None and i == len(entry_bars) - 1:
                exit_price, exit_reason = float(bar['close']), 'eod'

            # Trail update (applies to NEXT bar's stop check)
            if exit_price is None:
                peak = max(peak, float(bar['high'])) if not is_short \
                       else min(peak, float(bar['low']))
                position['peak'] = peak

                pnl_from_peak = (peak - entry_p) if not is_short \
                                else (entry_p - peak)

                be_pts    = p_atr * be_mult
                wide_pts  = p_atr * trail_wide_atr
                wide_gap  = p_atr * TRAIL_WIDE_GAP_ATR
                tight_pts = p_atr * trail_tight_atr
                tight_gap = p_atr * TRAIL_TIGHT_GAP_ATR

                if pnl_from_peak >= be_pts:
                    be_sl = _tick_round(entry_p + TICK_SIZE) if not is_short \
                            else _tick_round(entry_p - TICK_SIZE)
                    if (not is_short and be_sl > sl) or (is_short and be_sl < sl):
                        sl = be_sl

                if pnl_from_peak >= wide_pts:
                    w_sl = _tick_round(peak - wide_gap) if not is_short \
                           else _tick_round(peak + wide_gap)
                    if (not is_short and w_sl > sl) or (is_short and w_sl < sl):
                        sl = w_sl

                if pnl_from_peak >= tight_pts:
                    t_sl = _tick_round(peak - tight_gap) if not is_short \
                           else _tick_round(peak + tight_gap)
                    if (not is_short and t_sl > sl) or (is_short and t_sl < sl):
                        sl = t_sl

                position['sl'] = sl

            if exit_price is not None:
                net = pnl_dollars(entry_p, exit_price, side, contracts)
                daily_pnl += net
                trades.append({
                    'date':       date_str,
                    'entry_time': position['entry_time'],
                    'exit_time':  t.strftime('%H:%M'),
                    'side':       side,
                    'setup':      position['setup'],
                    'grade':      position['grade'],
                    'entry':      entry_p,
                    'exit':       exit_price,
                    'sl_init':    position['sl_init'],
                    'target':     target,
                    'contracts':  contracts,
                    'pnl':        net,
                    'exit_reason': exit_reason,
                    'ib_range':     ib_range,
                    'ib_close_pos': ib_close_pos,
                    'ovn_pos':      ovn_pos,
                    'atr':          p_atr,
                })
                position = None

        # ── Look for new entry ────────────────────────────────────────────────
        if position is None and trade_count < MAX_DAILY_TRADES:
            price = float(bar['close'])

            # Signal A: IB range break (close outside IB)
            sig_a_bull = price > ib_high
            sig_a_bear = price < ib_low

            # Signal B: price vs cumulative overnight VWAP
            sig_b_bull = (price > ovn_vwap) if ovn_vwap is not None else False
            sig_b_bear = (price < ovn_vwap) if ovn_vwap is not None else False

            for side in ('LONG', 'SHORT'):
                # Respect directional bias
                if daily_bias == 'LONG'  and side == 'SHORT': continue
                if daily_bias == 'SHORT' and side == 'LONG':  continue

                sig_a = sig_a_bull if side == 'LONG' else sig_a_bear
                sig_b = sig_b_bull if side == 'LONG' else sig_b_bear
                sig_c = sig_c_bull if side == 'LONG' else sig_c_bear

                score, grade = grade_london_entry(sig_a, sig_b, sig_c, signals)
                if grade != 'A+':
                    continue

                # IB alignment gate: skip counter-momentum breaks on trending IB days.
                # If London IB trended up (closed in top 20%) → only allow LONG.
                # If London IB trended down (closed in bottom 20%) → only allow SHORT.
                if ib_align_gate:
                    if side == 'SHORT' and ib_close_pos > 0.80:
                        continue
                    if side == 'LONG'  and ib_close_pos < 0.20:
                        continue

                sl, target = calc_sl_target(price, atr, side, stop_mult, target_mult)
                stop_pts   = abs(price - sl)

                if stop_pts <= 0 or stop_pts > MAX_STOP_PTS:
                    continue

                rr = abs(target - price) / stop_pts
                if rr < MIN_RR - 0.01:
                    continue

                contracts = calc_contracts(price, sl, risk_per_trade)
                position  = {
                    'entry':      price,
                    'sl':         sl,
                    'sl_init':    sl,
                    'target':     target,
                    'side':       side,
                    'contracts':  contracts,
                    'entry_time': t.strftime('%H:%M'),
                    'entry_idx':  i,
                    'peak':       price,
                    'atr':        atr,
                    'setup':      _setup_label(sig_a, sig_b, sig_c, side),
                    'grade':      grade,
                }
                trade_count += 1
                break   # one entry per bar

    return trades, daily_bias, ovn_pos, day_stats


# ── Scenario runner ────────────────────────────────────────────────────────────

def _run_scenario(
    all_bars:        pd.DataFrame,
    trade_dates:     list,
    signals:         str,
    stop_mult:       float,
    target_mult:     float,
    label:           str,
    verbose:         bool  = False,
    ib_clean:        bool  = LONDON_IB_CLEAN,
    min_ib_range:    float = LONDON_MIN_IB_RANGE,
    be_mult:         float = BE_ATR_MULT,
    trail_wide_atr:  float = TRAIL_WIDE_ATR,
    trail_tight_atr: float = TRAIL_TIGHT_ATR,
    ib_align_gate:   bool  = False,
    dll:             float = MAX_DAILY_LOSS,
    risk_per_trade:  float = MAX_RISK_PER_TRADE,
) -> dict:
    all_trades: list[dict] = []

    for trade_date in trade_dates:
        trades, bias, pos, _ = simulate_london_day(
            all_bars, trade_date,
            signals=signals, stop_mult=stop_mult, target_mult=target_mult,
            ib_clean=ib_clean, min_ib_range=min_ib_range,
            be_mult=be_mult, trail_wide_atr=trail_wide_atr,
            trail_tight_atr=trail_tight_atr,
            ib_align_gate=ib_align_gate,
            dll=dll, risk_per_trade=risk_per_trade,
        )
        all_trades.extend(trades)

        if verbose and trades:
            bias_tag = f'[{bias} {pos:.2f}]' if pos >= 0 else f'[{bias}]'
            row      = '  |  '.join(
                f'{t["setup"]}({t["pnl"]:+.0f})'
                for t in trades
            )
            wins = sum(1 for t in trades if t['pnl'] > 0)
            net  = sum(t['pnl'] for t in trades)
            print(f'  {trade_date}  {bias_tag:14}  '
                  f'{len(trades)}t  {wins}W  ${net:+.2f}   {row}')

    n   = len(all_trades)
    wins = sum(1 for t in all_trades if t['pnl'] > 0)
    wr   = wins / n * 100 if n else 0.0
    tot  = sum(t['pnl'] for t in all_trades)
    avg  = tot / n if n else 0.0

    # Max drawdown (dollar)
    cumulative = 0.0
    peak_cum   = 0.0
    max_dd     = 0.0
    for t in all_trades:
        cumulative += t['pnl']
        if cumulative > peak_cum:
            peak_cum = cumulative
        dd = peak_cum - cumulative
        if dd > max_dd:
            max_dd = dd

    return {
        'label': label, 'n': n, 'wr': wr,
        'total': tot, 'avg': avg, 'dd': max_dd,
        'df': pd.DataFrame(all_trades) if all_trades else None,
    }


# ── Stats printer ──────────────────────────────────────────────────────────────

def _print_stats(
    all_bars:     pd.DataFrame,
    trade_dates:  list,
    ib_clean:     bool  = LONDON_IB_CLEAN,
    min_ib_range: float = LONDON_MIN_IB_RANGE,
) -> None:
    print(f'\n=== London Session Data Distribution ===')
    print(f'Period: {trade_dates[0]} → {trade_dates[-1]}  '
          f'({len(trade_dates)} trading days)\n')

    skip_reasons: dict[str, int] = {}
    ib_ranges: list[float]       = []
    bias_counts = {'LONG': 0, 'SHORT': 0, 'BOTH': 0, 'skip': 0}
    by_year: dict[int, dict]     = {}

    for trade_date in trade_dates:
        yr = trade_date.year
        if yr not in by_year:
            by_year[yr] = {'total': 0, 'tradeable': 0, 'ib_ranges': []}
        by_year[yr]['total'] += 1

        _, _, _, ds = simulate_london_day(
            all_bars, trade_date,
            signals='', ib_clean=ib_clean, min_ib_range=min_ib_range,
        )

        skip = ds['skip_reason']
        if skip:
            skip_reasons[skip] = skip_reasons.get(skip, 0) + 1
        else:
            ib_ranges.append(ds['ib_range'])
            by_year[yr]['ib_ranges'].append(ds['ib_range'])
            by_year[yr]['tradeable'] += 1

        bias = ds['bias']
        if bias in ('LONG', 'SHORT', 'BOTH'):
            bias_counts[bias] += 1
        else:
            bias_counts['skip'] = bias_counts.get('skip', 0) + 1

    total_days = len(trade_dates)
    tradeable  = len(ib_ranges)

    print(f'Valid IB days (tradeable): {tradeable} / {total_days}'
          f'  ({tradeable / total_days * 100:.1f}%)\n')

    print('Skip reasons:')
    for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1]):
        pct = count / total_days * 100
        bar = '█' * int(pct / 2)
        print(f'  {reason:<22} {count:4d}  ({pct:5.1f}%)  {bar}')

    print(f'\nOvernight bias distribution:')
    for bias, count in sorted(bias_counts.items(), key=lambda x: -x[1]):
        pct = count / total_days * 100
        print(f'  {bias:<8} {count:4d}  ({pct:5.1f}%)')

    if ib_ranges:
        ib_s = pd.Series(ib_ranges)
        print(f'\nIB range distribution ({tradeable} valid days):')
        brackets = [(0, 20), (20, 40), (40, 60), (60, 100), (100, 150), (150, 9999)]
        for lo, hi in brackets:
            n   = int(((ib_s >= lo) & (ib_s < hi)).sum())
            lbl = f'{lo}–{hi}pts' if hi < 9999 else f'>{lo}pts'
            bar = '█' * (n * 30 // tradeable) if tradeable else ''
            print(f'  {lbl:<13} {n:4d}  ({n / tradeable * 100:5.1f}%)  {bar}')
        print(f'\n  Median: {ib_s.median():.1f}pts  '
              f'P25: {ib_s.quantile(0.25):.1f}pts  '
              f'P75: {ib_s.quantile(0.75):.1f}pts  '
              f'P90: {ib_s.quantile(0.90):.1f}pts')

    print(f'\nBy year:')
    hdr = f'  {"Year":<6}{"Days":>5}{"Tradeable":>11}{"Pct":>7}{"Med IB":>9}'
    print(hdr)
    print('  ' + '─' * (len(hdr) - 2))
    for yr in sorted(by_year):
        d   = by_year[yr]
        pct = d['tradeable'] / d['total'] * 100 if d['total'] else 0
        med = pd.Series(d['ib_ranges']).median() if d['ib_ranges'] else 0.0
        print(f'  {yr:<6}{d["total"]:>5}{d["tradeable"]:>11}{pct:>6.1f}%{med:>9.1f}')

    print(f'\n★ Key calibration targets:')
    print(f'  LONDON_MIN_IB_RANGE={min_ib_range:.0f}pts — adjust until ~50-60% of days are tradeable')
    print(f'  LONDON_IB_CLEAN={ib_clean} — if removing too many days, set --no-ib-clean')
    print(f'  OVN_COMPRESS ({_OVN_COMPRESS}pts) — reduce if BOTH% > 40%')


# ── IB quality analysis ────────────────────────────────────────────────────────

def _analyze_ib_quality(df: pd.DataFrame) -> None:
    """
    Blood test: break London trades down by IB close position × trade direction.

    IB close position (ib_close_pos):
      0.0–0.20  → IB trended DOWN (closed near the session low)
      0.20–0.40 → lower middle
      0.40–0.60 → ranging / balanced IB
      0.60–0.80 → upper middle
      0.80–1.00 → IB trended UP (closed near the session high)

    Alignment:
      LONG  on IB-up  day (pos>0.65) = momentum continuation = "Aligned"
      SHORT on IB-down day (pos<0.35) = momentum continuation = "Aligned"
      LONG  on IB-down day           = counter-momentum       = "Counter"
      SHORT on IB-up  day            = counter-momentum       = "Counter"
      Middle IB (0.35–0.65)          = ranging day            = "Neutral"
    """
    if df is None or df.empty or 'ib_close_pos' not in df.columns:
        print('  No ib_close_pos data available (re-run with updated sim).')
        return

    print(f'\n  {"─"*64}')
    print(f'  IB CLOSE POSITION BLOOD TEST  ({len(df)} trades)\n')

    # ── By IB pos bracket ────────────────────────────────────────────────────
    brackets = [
        ('Bottom 20%  (IB→DOWN)', 0.00, 0.20),
        ('20%–40%     (low-mid)',  0.20, 0.40),
        ('40%–60%     (ranging)',  0.40, 0.60),
        ('60%–80%     (high-mid)', 0.60, 0.80),
        ('Top 20%     (IB→UP)',    0.80, 1.01),
    ]
    print(f'  {"Bracket":<28} {"N":>4} {"WR%":>6} {"P&L":>10} {"Avg":>8}')
    print(f'  {"─"*28} {"─"*4} {"─"*6} {"─"*10} {"─"*8}')
    for label, lo, hi in brackets:
        sub = df[(df['ib_close_pos'] >= lo) & (df['ib_close_pos'] < hi)]
        if sub.empty:
            continue
        w   = (sub['pnl'] > 0).sum()
        wr  = w / len(sub) * 100
        tot = sub['pnl'].sum()
        avg = tot / len(sub)
        print(f'  {label:<28} {len(sub):>4} {wr:>5.1f}% {tot:>+10.2f} {avg:>+8.2f}')

    # ── Aligned vs Counter breakdown ──────────────────────────────────────────
    print(f'\n  ALIGNMENT vs IB MOMENTUM\n')

    def _tag(row):
        p, s = row['ib_close_pos'], row['side']
        if p >= 0.65:
            return 'Counter-LONG' if s == 'SHORT' else 'Aligned-LONG'
        if p <= 0.35:
            return 'Counter-SHORT' if s == 'LONG' else 'Aligned-SHORT'
        return 'Neutral (ranging IB)'

    df = df.copy()
    df['align_tag'] = df.apply(_tag, axis=1)

    order = ['Aligned-LONG', 'Aligned-SHORT', 'Neutral (ranging IB)',
             'Counter-LONG', 'Counter-SHORT']
    print(f'  {"Category":<22} {"N":>4} {"WR%":>6} {"P&L":>10} {"Avg":>8}')
    print(f'  {"─"*22} {"─"*4} {"─"*6} {"─"*10} {"─"*8}')
    for tag in order:
        sub = df[df['align_tag'] == tag]
        if sub.empty:
            continue
        w   = (sub['pnl'] > 0).sum()
        wr  = w / len(sub) * 100
        tot = sub['pnl'].sum()
        avg = tot / len(sub)
        marker = '  ←' if 'Aligned' in tag else ('  ✗' if 'Counter' in tag else '')
        print(f'  {tag:<22} {len(sub):>4} {wr:>5.1f}% {tot:>+10.2f} {avg:>+8.2f}{marker}')

    # ── By side within trending IB days (pos<0.20 or pos>0.80) ───────────────
    trending = df[(df['ib_close_pos'] < 0.20) | (df['ib_close_pos'] > 0.80)]
    ranging  = df[(df['ib_close_pos'] >= 0.20) & (df['ib_close_pos'] <= 0.80)]
    print(f'\n  TRENDING IB DAYS (pos<0.20 or >0.80): {len(trending)} trades')
    for side in ('LONG', 'SHORT'):
        sub = trending[trending['side'] == side]
        if sub.empty:
            continue
        w   = (sub['pnl'] > 0).sum()
        wr  = w / len(sub) * 100
        tot = sub['pnl'].sum()
        print(f'    {side:<7} {len(sub):>3}t  {w}/{len(sub)}W  WR={wr:.1f}%  ${tot:+.2f}  avg ${tot/len(sub):+.2f}')
    print(f'  RANGING IB DAYS  (pos 0.20–0.80):     {len(ranging)} trades')
    for side in ('LONG', 'SHORT'):
        sub = ranging[ranging['side'] == side]
        if sub.empty:
            continue
        w   = (sub['pnl'] > 0).sum()
        wr  = w / len(sub) * 100
        tot = sub['pnl'].sum()
        print(f'    {side:<7} {len(sub):>3}t  {w}/{len(sub)}W  WR={wr:.1f}%  ${tot:+.2f}  avg ${tot/len(sub):+.2f}')

    # ── Entry time breakdown ──────────────────────────────────────────────────
    if 'entry_time' in df.columns:
        print(f'\n  ENTRY TIME QUALITY (London session 4am–9am ET)\n')
        df['entry_hour'] = df['entry_time'].str[:2].astype(int)
        windows = [(4, 5, '04:00–05:00'), (5, 6, '05:00–06:00'),
                   (6, 7, '06:00–07:00'), (7, 8, '07:00–08:00'),
                   (8, 9, '08:00–09:00')]
        print(f'  {"Window":<14} {"N":>4} {"WR%":>6} {"P&L":>10} {"Avg":>8}')
        print(f'  {"─"*14} {"─"*4} {"─"*6} {"─"*10} {"─"*8}')
        for lo, hi, label in windows:
            sub = df[(df['entry_hour'] >= lo) & (df['entry_hour'] < hi)]
            if sub.empty:
                continue
            w   = (sub['pnl'] > 0).sum()
            wr  = w / len(sub) * 100
            tot = sub['pnl'].sum()
            avg = tot / len(sub)
            print(f'  {label:<14} {len(sub):>4} {wr:>5.1f}% {tot:>+10.2f} {avg:>+8.2f}')
        print(f'  ← Early entries (4-5am) = fresh IB break. Late (7-8am) = exhausted move.')


# ── Shared results printer ─────────────────────────────────────────────────────

def _print_results(r: dict, detail: bool = False) -> None:
    print(f'{"─"*64}')
    print(f'  Trades: {r["n"]}  |  WR: {r["wr"]:.1f}%  |  '
          f'P&L: ${r["total"]:+.2f}  |  Avg: ${r["avg"]:+.2f}  |  MaxDD: ${r["dd"]:.2f}')

    df = r.get('df')
    if df is None or df.empty:
        print('  No trades generated.')
        return

    print(f'\n  By exit reason:')
    for reason, grp in df.groupby('exit_reason'):
        w = (grp['pnl'] > 0).sum()
        print(f'    {reason:<16} {len(grp):3d}t  {w}/{len(grp)}W  '
              f'${grp["pnl"].sum():+.2f}  avg ${grp["pnl"].mean():+.2f}')

    print(f'\n  By side:')
    for side, grp in df.groupby('side'):
        w = (grp['pnl'] > 0).sum()
        print(f'    {side:<7} {len(grp):3d}t  {w}/{len(grp)}W  '
              f'${grp["pnl"].sum():+.2f}  avg ${grp["pnl"].mean():+.2f}')

    print(f'\n  By setup:')
    for setup, grp in df.groupby('setup'):
        w = (grp['pnl'] > 0).sum()
        print(f'    {setup:<20} {len(grp):3d}t  {w}/{len(grp)}W  '
              f'${grp["pnl"].sum():+.2f}')

    print(f'\n  Year breakdown:')
    for yr in sorted(df['date'].str[:4].unique()):
        yrdf = df[df['date'].str[:4] == yr]
        yw   = (yrdf['pnl'] > 0).sum()
        print(f'    {yr}:  {len(yrdf):3d}t  {yw}/{len(yrdf)}W  '
              f'${yrdf["pnl"].sum():+.2f}  avg ${yrdf["pnl"].mean():+.2f}')

    if detail:
        print(f'\n  Individual trades:')
        print(f'  {"Date":<12}{"In":>6}{"Out":>6}{"Side":>7}  '
              f'{"Setup":<22}{"G":>3}{"ATR":>6}{"Entry":>9}{"Exit":>9}{"P&L":>9}  Reason')
        for _, t in df.iterrows():
            print(f'  {t["date"]:<12}{t["entry_time"]:>6}{t["exit_time"]:>6}'
                  f'{t["side"]:>7}  {t["setup"]:<21}'
                  f'{t["grade"]:>3}{t["atr"]:>6.0f}'
                  f'{t["entry"]:>9.2f}{t["exit"]:>9.2f}'
                  f'${t["pnl"]:>+8.2f}  {t["exit_reason"]}')


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description='London session MNQ simulator')
    ap.add_argument('--start',        default='2021-01-01',
                    help='Backtest start date (default: 2021-01-01)')
    ap.add_argument('--end',          default=_dt.date.today().isoformat(),
                    help='Backtest end date (default: today)')
    ap.add_argument('--signals',      default='ABC',
                    help='Signal subset — any combo of A, B, C (default: ABC)')
    ap.add_argument('--compare',      action='store_true',
                    help='Run all 7 signal combinations and print comparison table')
    ap.add_argument('--stats',        action='store_true',
                    help='Print data distribution stats (no trading)')
    ap.add_argument('--detail',       action='store_true',
                    help='Print every trade in output')
    ap.add_argument('--stop-mult',    type=float, default=STOP_ATR_MULT,
                    metavar='N', help=f'ATR stop multiplier (default: {STOP_ATR_MULT})')
    ap.add_argument('--target-mult',  type=float, default=TARGET_ATR_MULT,
                    metavar='N', help=f'ATR target multiplier (default: {TARGET_ATR_MULT})')
    ap.add_argument('--no-ib-clean',     action='store_true',
                    help='Disable IB clean filter (allow trending IB)')
    ap.add_argument('--min-ib',          type=float, default=LONDON_MIN_IB_RANGE,
                    metavar='N', help=f'Min IB range in pts (default: {LONDON_MIN_IB_RANGE})')
    ap.add_argument('--be-mult',         type=float, default=BE_ATR_MULT,
                    metavar='N', help=f'BE trail activation (ATR mult, default: {BE_ATR_MULT})')
    ap.add_argument('--trail-wide-atr',  type=float, default=TRAIL_WIDE_ATR,
                    metavar='N', help=f'Wide trail activation (ATR mult, default: {TRAIL_WIDE_ATR})')
    ap.add_argument('--trail-tight-atr', type=float, default=TRAIL_TIGHT_ATR,
                    metavar='N', help=f'Tight trail activation (ATR mult, default: {TRAIL_TIGHT_ATR})')
    ap.add_argument('--analyze',         action='store_true',
                    help='Show IB close position blood test (WR by IB bracket × direction)')
    ap.add_argument('--ib-align-gate',   action='store_true',
                    help='Enable IB directional alignment gate (trending IB→restrict direction)')
    ap.add_argument('--dll',             type=float, default=MAX_DAILY_LOSS,
                    metavar='N', help=f'Daily loss limit in $ (default: {MAX_DAILY_LOSS})')
    ap.add_argument('--risk',            type=float, default=MAX_RISK_PER_TRADE,
                    metavar='N', help=f'$ risk per trade for sizing (default: {MAX_RISK_PER_TRADE})')
    args = ap.parse_args()

    ib_clean        = not args.no_ib_clean
    min_ib_range    = args.min_ib
    be_mult         = args.be_mult
    trail_wide_atr  = args.trail_wide_atr
    trail_tight_atr = args.trail_tight_atr
    ib_align_gate   = args.ib_align_gate
    dll             = args.dll
    risk_per_trade  = args.risk

    print(f'\n=== London Session Replay: {args.start} → {args.end} ===')
    print(f'Session:  3:00am–9:00am ET  |  IB: 3:00–4:00 ET  '
          f'|  Min IB: {min_ib_range:.0f}pts  |  IB clean: {"ON" if ib_clean else "OFF"}')
    print(f'DLL: ${dll:.0f}  |  Risk/trade: ${risk_per_trade:.0f}  '
          f'|  Max trades: {MAX_DAILY_TRADES}  '
          f'|  Stop: {args.stop_mult}×ATR  Target: {args.target_mult}×ATR')
    print(f'Overnight bias: pos≥{_OVN_TREND_HI}→LONG, ≤{_OVN_TREND_LO}→SHORT'
          f'  |  Trail: BE={be_mult}×ATR, Wide={trail_wide_atr}×, '
          f'Tight={trail_tight_atr}×\n')

    # Load bars (5 extra days for overnight warmup before start_dt)
    start_dt   = _dt.date.fromisoformat(args.start)
    load_start = (start_dt - _dt.timedelta(days=5)).isoformat()
    end_cutoff = (_dt.date.fromisoformat(args.end) + _dt.timedelta(days=1)).isoformat()

    print(f'Loading MNQ bars {load_start} → {end_cutoff} ...')
    all_bars = load_bars('MNQ', start=load_start, end=end_cutoff)
    if all_bars.empty:
        print('No bars found. Run: venv/bin/python futures/collect_bars.py --update')
        return

    print(f'Bars loaded: {len(all_bars):,}  '
          f'({all_bars.index[0].date()} → {all_bars.index[-1].date()})\n')

    # Find trade dates: weekdays that have London-window bars AND are in range
    london_window_mask = (all_bars.index.time >= LONDON_IB_START) & \
                         (all_bars.index.time <  LONDON_EOD)
    london_dates = set(all_bars[london_window_mask].index.date)
    trade_dates  = sorted(
        d for d in london_dates
        if start_dt <= d <= _dt.date.fromisoformat(args.end)
        and d.weekday() < 5
        and d not in US_HOLIDAYS
    )

    if not trade_dates:
        print('No London session days found in this range.')
        print('  Check DB coverage: venv/bin/python futures/collect_bars.py --summary')
        return

    print(f'London trading days found: {len(trade_dates)}'
          f'  ({trade_dates[0]} → {trade_dates[-1]})\n')

    # ── Stats mode ─────────────────────────────────────────────────────────────
    if args.stats:
        _print_stats(all_bars, trade_dates, ib_clean=ib_clean, min_ib_range=min_ib_range)
        return

    kw = dict(stop_mult=args.stop_mult, target_mult=args.target_mult,
              ib_clean=ib_clean, min_ib_range=min_ib_range,
              be_mult=be_mult, trail_wide_atr=trail_wide_atr,
              trail_tight_atr=trail_tight_atr,
              ib_align_gate=ib_align_gate,
              dll=dll, risk_per_trade=risk_per_trade)

    # ── Compare mode: all 7 signal combinations ────────────────────────────────
    if args.compare:
        combos  = ['A', 'B', 'C', 'AB', 'AC', 'BC', 'ABC']
        results = [_run_scenario(all_bars, trade_dates, sig, label=sig, **kw)
                   for sig in combos]

        print(f'{"="*72}')
        print(f'  SIGNAL COMPARISON  ({args.start} → {args.end}, '
              f'{len(trade_dates)} trading days)')
        print(f'{"="*72}')
        print(f'  {"Sig":<5} {"Trades":>7} {"WR%":>7} {"P&L":>11}'
              f' {"Avg/trade":>10} {"MaxDD":>9}')
        print(f'  {"─"*5} {"─"*7} {"─"*7} {"─"*11} {"─"*10} {"─"*9}')
        for r in results:
            wr_s = f'{r["wr"]:.1f}%'
            print(f'  {r["label"]:<5} {r["n"]:>7} {wr_s:>7} '
                  f'${r["total"]:>+10.2f} ${r["avg"]:>+9.2f} ${r["dd"]:>8.2f}')

        # Per-year for ABC
        abc = results[-1]
        if abc['df'] is not None:
            print(f'\n  Year breakdown (ABC signals):')
            df = abc['df']
            for yr in sorted(df['date'].str[:4].unique()):
                yrdf = df[df['date'].str[:4] == yr]
                yw   = (yrdf['pnl'] > 0).sum()
                print(f'    {yr}:  {len(yrdf):3d}t  {yw}/{len(yrdf)}W  '
                      f'${yrdf["pnl"].sum():+.2f}  avg ${yrdf["pnl"].mean():+.2f}')

        print(f'\n  ★ CALIBRATE: Run --stats first to understand IB range distribution.')
        print(f'     Thin data years (< 20 trades) — extend with --start 2025-01-01.')
        return

    # ── Normal mode ────────────────────────────────────────────────────────────
    signals_upper = args.signals.upper()
    align_tag = '  [IB-ALIGN-GATE ON]' if ib_align_gate else ''
    print(f'Signals: {signals_upper}{align_tag}\n')
    r = _run_scenario(
        all_bars, trade_dates, signals_upper, label=signals_upper,
        verbose=args.detail, **kw,
    )
    _print_results(r, detail=args.detail)
    if args.analyze and r.get('df') is not None:
        _analyze_ib_quality(r['df'])

    print(f'\n  ★ CALIBRATE: All ★-marked constants in london_sim.py need validation.')
    print(f'     Run --stats to calibrate IB gates.')
    print(f'     Run --compare to find the best signal combination.')
    print(f'     Run --start 2025-01-01 for matching-weather years (2025-2026).')


if __name__ == '__main__':
    main()
