# research_fish_finder_weather_advisory.py — Aug 5 2026
#
# Backtests the "Fish Finder / Weather Advisory" redesign of the equity
# Regime-Adaptive Suite (live design: auto_trader.py REGIME_STRATEGY_MAP +
# _regime_adaptive_signal_fires, shipped Aug 5, see CLAUDE.md).
#
# Today's live design: the Weather Report (regime, SPY/QQQ/VIX rule stack) picks
# ONE strategy template globally, applied to all 241 symbols at once. A symbol's
# own signals only get checked against WHATEVER template today's weather picked —
# never against a template that might actually fit that symbol better.
#
# This redesign inverts that:
#   - Fish Finder (Layer 1): every symbol is tested against ALL THREE templates
#     every scan, using its own live signals (own ADX / RSI / Keltner position) —
#     not gated by which one template the market-wide regime happened to select.
#   - Weather Advisory (Layer 2): the Weather Report stops being a hard router and
#     becomes a soft modifier — it can tighten/loosen each template's threshold per
#     regime, but never bans a template outright the way today's live design does.
#   - Tie-break ("more conviction wins", user's own framing): when a symbol
#     qualifies for more than one template at once (impossible under the live
#     design, since only one template is ever tested) — normalize each template's
#     confirming signal as how far past ITS OWN threshold it is, pick the highest.
#
# Does NOT touch REGIME_STRATEGY_MAP / _regime_adaptive_signal_fires / live
# auto_trader.py at all. Runs on top of the rebuilt equity_replay.py (Aug 5 v2,
# calls real live functions under a frozen clock) — NOT a new hand-approximated
# script, to avoid recreating the exact "sim doesn't match live" problem this
# whole redesign thread started from.
#
# Status: Part 2 of the equity replay rebuild plan. layer1_candidates/
# effective_thresholds/conviction_score/resolve_tie below are pure functions,
# safe to test standalone before equity_replay.py's Part-1 checkpoints all land
# (they take a plain `sig` dict, no replay dependency). run_old_design/
# run_new_design (bottom) DO depend on the rebuilt replay and must not be run
# until equity_replay.py clears its own checkpoint 3+.
#
# Command: venv/bin/python research_fish_finder_weather_advisory.py --start ... --end ...

import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TEMPLATES = ('ADX_TREND', 'KELTNER_REVERT', 'RSI_REVERT')

# Live defaults — mirror auto_trader.py:3490-3505 exactly. Layer 2 varies these
# per regime (below); baseline (all deltas 0) must reproduce live's own thresholds.
BASE_THRESHOLDS = {
    'adx_min':   25,
    'rsi_hi':    70,   # RSI_REVERT SHORT fires above this
    'rsi_lo':    30,   # RSI_REVERT LONG fires below this
}

# Weather Advisory — regime -> per-template threshold delta, additive on
# BASE_THRESHOLDS. ALL START AT 0 (= Weather Advisory OFF, pure Fish Finder).
# This is the sweep's parameter space, NOT a pre-baked guess — do not read
# significance into these starting at 0, that's deliberate (checkpoint 6 tests
# Fish Finder alone before any Weather Advisory tightening is even considered).
WEATHER_ADVISORY = {
    'STRONG':   {'adx_delta': 0, 'rsi_delta': 0},
    'NORMAL':   {'adx_delta': 0, 'rsi_delta': 0},
    'CAUTIOUS': {'adx_delta': 0, 'rsi_delta': 0},
    'WEAK':     {'adx_delta': 0, 'rsi_delta': 0},
    'CHOPPY':   {'adx_delta': 0, 'rsi_delta': 0},
}

# Data-motivated variant of BASE_THRESHOLDS (Aug 6 2026 analysis, H1 2025 window,
# 416 real Fish Finder RSI_REVERT trades already logged): SHORT-side (rsi_hi=70)
# showed a clean, monotonic, large-n pattern — the [70,75) bucket alone (n=83,
# the single largest bucket) lost -$53.67 at 13.3% WR, while every bucket above
# 75 was profitable. Raising rsi_hi to 75 would have taken RSI_REVERT SHORT from
# +$7.69 to +$61.36 in this window (removes the losing bucket, keeps the rest) --
# a real effect size on a large sample, not a single-point curve-fit. LONG-side
# (rsi_lo=30) showed NO clean pattern (oscillating: 0-15 good, 15-20 bad, 20-25
# great, 25-30 bad) -- classic noise signature, NOT changed. This is a
# HYPOTHESIS to verify via a full replay (bucket analysis doesn't capture how a
# threshold change alters which candidates WIN the conviction tie-break in the
# first place), not a conclusion -- compare TUNED_THRESHOLDS vs BASE_THRESHOLDS
# results before trusting either.
TUNED_THRESHOLDS = dict(BASE_THRESHOLDS, rsi_hi=75)

# Regime eligibility per template (Aug 6 2026, same analysis session): ADX_TREND
# showed NO clean per-symbol-ADX threshold pattern that explains its Fish-Finder
# degradation (best bucket was 30-35, not "higher ADX = better" monotonically) --
# the problem isn't the per-symbol cutoff, it's that a trend signal on one stock
# needs SOME market-wide trend-conducive backdrop to mean anything; a threshold
# tweak can't fix "right signal, wrong day." Reversion templates showed no such
# regime-dependency in the data (RSI_REVERT flipped PROFITABLE specifically by
# being freed from CHOPPY-only gating) -- so only ADX_TREND gets eligibility
# restricted, matching live's own STRONG/NORMAL/WEAK mapping for that template;
# reversion templates stay eligible every regime.
TEMPLATE_ELIGIBLE_REGIMES = {
    'ADX_TREND':      {'STRONG', 'NORMAL', 'WEAK'},
    'KELTNER_REVERT': {'STRONG', 'NORMAL', 'CAUTIOUS', 'WEAK', 'CHOPPY'},
    'RSI_REVERT':      {'STRONG', 'NORMAL', 'CAUTIOUS', 'WEAK', 'CHOPPY'},
}

# Breadth gate (Aug 6 2026, same session): scored all 3088 real FISHFINDER_
# ADX_TREND trades against the universe breadth (% of the 241-symbol universe
# green intraday) AT EACH TRADE'S OWN ENTRY TIMESTAMP, using bars already cached
# by equity_replay's preload — no new backtest run needed to test this
# hypothesis. Result: a strong, clean TAIL effect, not a linear one (raw Pearson
# corr(-0.056) understates it) — the 70-100% breadth bucket alone (n=737, the
# single largest bucket) lost -$1,099.80, MORE than ADX_TREND's entire category
# total (-$990.72); every bucket <60% was flat-to-positive. Inflection sits
# around 60-70% (50-60% bucket: +$286; 60-70% bucket: -$308) — threshold set at
# the data's own turn, not a round number. Mechanism (plausible, not proven):
# very high breadth likely means the broad move is already mature/crowded, so
# buying an individual stock's "trend" there is chasing an extended move, not
# joining an early one. NOTE: originally hypothesized universe CORRELATION
# would show the opposite pattern (high correlation = "real" macro trend =
# good for trend-following) -- the data disproved that too: low correlation
# (idiosyncratic days, 0.1-0.3) was the best bucket (+$411), high correlation
# (0.5-1.0) the worst (-$826), same shape as breadth. Both point the same way;
# breadth is used here as the live gate since it's cheaper to compute every
# scan cycle (simple % positive vs a 60-symbol trailing correlation matrix) —
# correlation is available as a documented follow-up, not dropped, just not
# the first thing wired in given the two appear to capture a similar effect.
TEMPLATE_BREADTH_MAX = {'ADX_TREND': 0.60}   # None/absent = no breadth cap


def universe_breadth(bars5_cache, universe_syms, now, today):
    """% of universe_syms with positive intraday (open-to-now) change, as of
    `now` on `today`. Pure function of already-cached bar data — same
    computation used to score the 3088 trades above, now usable live in a
    scan cycle. Returns None if too few symbols have data (fails open — a
    breadth gate that can't compute shouldn't silently block everything)."""
    pos, n = 0, 0
    for s in universe_syms:
        b = bars5_cache.get(s)
        if b is None:
            continue
        today_bars = b[(b.index.date == today) & (b.index <= now)]
        if len(today_bars) < 2:
            continue
        chg = (today_bars['Close'].iloc[-1] - today_bars['Open'].iloc[0]) / today_bars['Open'].iloc[0]
        pos += chg > 0
        n += 1
    return (pos / n) if n >= 30 else None


TEMPLATE_CORR_MAX = {'ADX_TREND': 0.35}   # None/absent = no correlation cap

# Same Aug 6 2026 analysis as the breadth gate, run against the SAME 3088
# real trades: mean pairwise universe correlation (trailing 20 daily returns,
# 60-symbol sample) showed the SAME tail shape as breadth, but flipped from
# what was originally hypothesized -- low correlation (idiosyncratic days,
# 0.1-0.3) was the BEST zone (+$411 combined), high correlation (0.5-1.0) the
# WORST (-$826). Flip point sits between 0.3 (+$303) and 0.4 (-$543) -- 0.35
# chosen as the midpoint of that observed flip, not a round number. Breadth
# was wired in first because it's cheaper (O(n) vs an O(n^2) correlation
# matrix) -- this is the "does correlation actually do BETTER, given it's
# more expensive" test the breadth choice deferred.
def universe_correlation(daily_cache, universe_syms, today, sample_every=4, window=20):
    """Mean pairwise correlation of trailing `window` daily returns across a
    `sample_every`-th subsample of universe_syms (speed — full 240x240 every
    scan cycle is unnecessary when a ~60-symbol sample gives the same read,
    per the diagnostic this was validated against). Returns None if too few
    symbols have enough history (fails open, same convention as breadth)."""
    import numpy as np
    sample = universe_syms[::sample_every]
    rets = {}
    for s in sample:
        d = daily_cache.get(s)
        if d is None:
            continue
        hist = d[d.index.date < today].tail(window)
        if len(hist) < window - 5:
            continue
        rets[s] = hist['Close'].pct_change().dropna().values
    if len(rets) < 20:
        return None
    minlen = min(len(v) for v in rets.values())
    if minlen < 5:
        return None
    mat = np.array([v[-minlen:] for v in rets.values()])
    corr = np.corrcoef(mat)
    iu = np.triu_indices_from(corr, k=1)
    return float(np.nanmean(corr[iu]))

# Normalization divisors for conviction_score — "how many points past threshold
# counts as one full unit of conviction," per template. Fixed starting constants
# (not fitted to data yet) chosen from the live thresholds' own typical range
# (ADX 25-50 is roughly the live system's observed working band; RSI extremes
# 70-100/0-30 likewise) — a reasonable first cut, explicitly flagged as revisable
# once real backtest data exists to calibrate against, not a finished answer.
ADX_CONVICTION_SCALE = 15.0
RSI_CONVICTION_SCALE = 15.0


def effective_thresholds(regime, base=None):
    """base +/- WEATHER_ADVISORY[regime]. At all-zero deltas, returns base
    unchanged — this is the equivalence checkpoint 5 needs to pass."""
    base = dict(base or BASE_THRESHOLDS)
    mods = WEATHER_ADVISORY.get(regime, {})
    return {
        'adx_min': base['adx_min'] + mods.get('adx_delta', 0),
        'rsi_hi':  base['rsi_hi']  + mods.get('rsi_delta', 0),
        'rsi_lo':  base['rsi_lo']  - mods.get('rsi_delta', 0),
    }


# Keltner starvation fix (Aug 8 2026): the flat conviction=1.0 was diagnosed
# NOT as a quality-ranking problem (real ATR band-distance showed ~0
# correlation to P&L in every period, full history -- that fix was tested and
# rejected) but as a TIE-BREAK FREQUENCY problem. Under live, CAUTIOUS-regime
# scans run KELTNER_REVERT exclusively (no competition -- only one strategy
# per regime). Under Fish Finder the same CAUTIOUS-regime symbols usually
# ALSO qualify for ADX_TREND, and Keltner's flat 1.0 sits almost exactly at
# ADX_TREND's own median winning conviction (1.00, IQR 0.47-1.67, n=15,276)
# -- a real coin-flip fight, not a landslide loss. Result: NEW captures only
# 84 of the 713 CAUTIOUS-regime trades OLD places on the identical days
# (11.8%). A trailing-P&L health gate (same mechanism that fixed WEAK-regime
# ADX_TREND) was tested on Keltner and REJECTED -- noisy, non-monotonic
# across window sizes, mostly worse than baseline (this is a selection-rate
# problem, not a signal-decay-over-time problem, so a decay-shaped fix
# doesn't apply). KELTNER_CONVICTION=2.0 beats 82.3% of ADX_TREND's observed
# conviction distribution while staying inside RSI_REVERT's own max (2.0) --
# a principled target (recover most of the collision losses) rather than an
# arbitrary large number. NOT YET VALIDATED via a real re-run as of writing.
KELTNER_CONVICTION = 2.0


def layer1_candidates(sig, thresholds, keltner_conviction=1.0):
    """Fish Finder: every (template, side, conviction) this symbol qualifies for
    RIGHT NOW under its own signals, at the given (possibly Weather-Advisory-
    adjusted) thresholds. Condition SHAPE copied 1:1 from
    _regime_adaptive_signal_fires (auto_trader.py:3490-3505) — same field, same
    comparison direction per side — so that at BASE_THRESHOLDS this reproduces
    the live function's boolean exactly; only the threshold VALUES are now
    parameterized instead of hardcoded, so Weather Advisory can vary them without
    touching auto_trader.py at all.
    Returns [] if sig is missing required fields (fails closed, not open —
    unlike live's regime-forced single-template check, this function is asked
    about all three every time, so a silent field-miss must not look like "no
    qualifying template" only for the one it happened to check first)."""
    out = []

    adx = sig.get('adx')
    if adx is not None and adx > thresholds['adx_min']:
        if sig.get('chg_5d_up'):
            out.append({'template': 'ADX_TREND', 'side': 'LONG',
                       'conviction': (adx - thresholds['adx_min']) / ADX_CONVICTION_SCALE})
        if sig.get('chg_5d_down'):
            out.append({'template': 'ADX_TREND', 'side': 'SHORT',
                       'conviction': (adx - thresholds['adx_min']) / ADX_CONVICTION_SCALE})

    if sig.get('above_keltner_upper'):
        # Live gives no numeric band-distance, only the boolean — conviction
        # here is a fixed unit (1.0) rather than continuously scaled. Flagged
        # explicitly: this is a coarser conviction estimate than ADX_TREND/
        # RSI_REVERT get, not a considered design choice — fast-follow would be
        # extending get_intraday_signals' return dict with the raw band distance
        # (a live-code change, out of scope for this research-only pass).
        out.append({'template': 'KELTNER_REVERT', 'side': 'SHORT', 'conviction': keltner_conviction})
    if sig.get('below_keltner_lower'):
        out.append({'template': 'KELTNER_REVERT', 'side': 'LONG', 'conviction': keltner_conviction})

    rsi = sig.get('rsi')
    if rsi is not None:
        if rsi > thresholds['rsi_hi']:
            out.append({'template': 'RSI_REVERT', 'side': 'SHORT',
                       'conviction': (rsi - thresholds['rsi_hi']) / RSI_CONVICTION_SCALE})
        if rsi < thresholds['rsi_lo']:
            out.append({'template': 'RSI_REVERT', 'side': 'LONG',
                       'conviction': (thresholds['rsi_lo'] - rsi) / RSI_CONVICTION_SCALE})

    return out


# Fallback priority when two candidates land within TIE_EPSILON of each other —
# trend-following first, matching the live system's own STRONG/NORMAL default
# bias. Logged (not silent) whenever it actually decides an outcome, since
# epsilon-ties are exactly the case where "more conviction wins" is least
# meaningful and a hidden tie-break would be the least defensible part of this
# whole design if audited later.
TIE_EPSILON = 0.05
TIE_PRIORITY = {'ADX_TREND': 0, 'KELTNER_REVERT': 1, 'RSI_REVERT': 2}


def resolve_tie(candidates, log_fn=None):
    """Returns the single highest-conviction candidate, or None if candidates
    is empty. candidates = layer1_candidates() output for one symbol, one scan."""
    if not candidates:
        return None
    ranked = sorted(candidates, key=lambda c: c['conviction'], reverse=True)
    if len(ranked) > 1 and (ranked[0]['conviction'] - ranked[1]['conviction']) < TIE_EPSILON:
        ranked.sort(key=lambda c: (TIE_PRIORITY.get(c['template'], 99), -c['conviction']))
        if log_fn:
            log_fn(f"  tie-break: {[c['template'] for c in ranked[:2]]} within "
                  f"{TIE_EPSILON} conviction — priority order used")
    return ranked[0]


# ── Old vs. new design harness ───────────────────────────────────────────────
# Both arms run through the IDENTICAL rebuilt equity_replay.replay_run() — same
# historical data, same monitor_open_trades/exit stack, same FillSimulator, same
# universe, same date range. The only difference is which function fires inside
# the "regime-adaptive suite" slot each scan cycle — that's the one thing being
# tested. Import lazily (only when actually running a replay, not for --selftest
# or when this module is imported just for its pure functions) so the pure
# functions above stay usable without pulling in the replay dependency.

# Size multiplier keyed by WINNING TEMPLATE, not by regime — under Fish Finder a
# symbol can win via any template regardless of today's weather, so the live
# design's per-REGIME size_mult (REGIME_STRATEGY_MAP, auto_trader.py:3482-3488)
# has no single regime to attach to any more. Re-anchoring it to the template
# instead preserves the live system's own existing risk calibration per
# STRATEGY (RSI_REVERT stays half-size — "thinnest backtest evidence" per
# CLAUDE.md — regardless of which regime surfaced it) rather than silently
# changing the risk philosophy while also changing the selection mechanism.
TEMPLATE_SIZE_MULT = {'ADX_TREND': 1.0, 'KELTNER_REVERT': 1.0, 'RSI_REVERT': 0.5}

# WEAK-regime ADX_TREND health gate (Aug 7 2026 OOS-investigation follow-up):
# post-hoc slicing of the full 2024-2026 FISHFINDER_ADX_TREND trades against
# their own real entry-time regime found the 2026 YTD OOS loss (-$615.81
# category total) is 89% concentrated in WEAK-regime entries specifically
# (-$813.91/1133 trades there alone, both LONG and SHORT simultaneously
# unprofitable -- new, doesn't happen in any other period). A trailing-N-
# trading-day P&L gate on WEAK-regime ADX_TREND specifically (mirrors Book
# Health Selector's mechanism, scoped to this one template+regime combo
# instead of the whole book) tested via a window sweep (2-25 days) on that
# same sliced data shows a real plateau at 5-9 days -- not a single lucky
# number -- recovering baseline -$419.94 to +$237..+$740 across the whole
# 4-period window while cutting 2026's damage from -$813.91 to roughly
# -$20..-$180. NOTE: that sweep was post-hoc (removed already-placed trades
# from the ledger after the fact) -- it does NOT account for a freed
# MAX_OPEN_TRADES slot going to a different candidate instead. This flag
# wires the gate into the actual scanner so a real re-run can confirm the
# effect survives that dynamic. window=7 chosen as the plateau's midpoint,
# not its single best point (window=6 tested marginally higher in the
# post-hoc slice, plateau spans 5-9 -- picking the extremum would be the
# same one-window-cherry-pick risk the corr/breadth gates are already
# flagged for).
WEAK_HEALTH_WINDOW = 7   # trading days; None/0 disables the gate


def _weak_adx_health_ok(con, weak_adx_trade_ids, today_str, window):
    """True (trade allowed) if the trailing `window` TRADING DAYS of P&L from
    already-CLOSED WEAK-regime FISHFINDER_ADX_TREND trades (exit_date strictly
    before today -- no lookahead into today's own open trades) is > 0, or if
    there isn't yet enough closed history to judge (cold-start ON, matching
    every other health gate in this codebase's convention)."""
    if not window or not weak_adx_trade_ids:
        return True
    placeholders = ','.join('?' * len(weak_adx_trade_ids))
    rows = con.execute(
        f"SELECT exit_date, pnl FROM trades WHERE id IN ({placeholders}) "
        f"AND status IN ('WIN','LOSS') AND exit_date < ? ORDER BY exit_date",
        list(weak_adx_trade_ids) + [today_str]
    ).fetchall()
    if not rows:
        return True
    daily = {}
    for exit_date, pnl in rows:
        daily[exit_date] = daily.get(exit_date, 0.0) + (pnl or 0.0)
    distinct_days = sorted(daily.keys())
    if len(distinct_days) < window:
        return True   # cold start — not enough trading-day history yet
    trailing_days = distinct_days[-window:]
    return sum(daily[d] for d in trailing_days) > 0


def _make_new_design_scanner(get_thresholds_fn=None, eligible_regimes=None, breadth_max=None,
                             corr_max=None, weak_health_window=None, keltner_conviction=1.0):
    """Returns a function with the EXACT signature/contract of the live
    _scan_regime_adaptive(regime, open_trades) (auto_trader.py:3508-3597), for
    monkeypatching onto at._scan_regime_adaptive. Bookkeeping (traded_today,
    save_traded_today, open_positions, first_bar_strong_trades, daily_bull/
    bear_count, _l3_pending, entries/Telegram) copied 1:1 from the live function
    — the only structural difference is per-symbol template SELECTION (Fish
    Finder: test all 3 templates via layer1_candidates+resolve_tie, using
    per-regime thresholds from Weather Advisory) instead of one regime-wide
    fixed template. Because side is no longer fixed for the whole scan, the
    live function's single `break` on daily_bull/bear cap becomes a per-symbol
    `continue` here — a different symbol later in scan_order might want the
    OTHER side, which isn't capped.

    eligible_regimes: optional dict {template: set(regimes)}, default
    TEMPLATE_ELIGIBLE_REGIMES (the hybrid design). Pass {} / all-regimes-allowed
    to reproduce pure Fish Finder (Checkpoint 6's original, unrestricted arm).

    breadth_max: optional dict {template: max_breadth}, default
    TEMPLATE_BREADTH_MAX. Pass {} to disable the breadth gate entirely.

    corr_max: optional dict {template: max_correlation}, default None (off).
    Pass TEMPLATE_CORR_MAX to enable — mutually testable against breadth_max,
    not combined by default (both target the same mechanism; combining them
    is a separate question from "which one alone works better").

    weak_health_window: optional int, default None (off). Pass
    WEAK_HEALTH_WINDOW to enable the trailing-N-day WEAK-regime ADX_TREND
    health gate — see its comment above for the data behind it."""
    get_thresholds_fn = get_thresholds_fn or effective_thresholds
    eligible_regimes = TEMPLATE_ELIGIBLE_REGIMES if eligible_regimes is None else eligible_regimes
    breadth_max = TEMPLATE_BREADTH_MAX if breadth_max is None else breadth_max
    corr_max = {} if corr_max is None else corr_max

    # Closure-persisted (survives across scan calls for the life of this
    # replay run) — trade ids this scanner itself placed as WEAK-regime
    # ADX_TREND, in entry order. No DB schema change needed: the scanner
    # already knows `regime` and `strategy_name` at the moment of entry,
    # which is the only place this information exists.
    weak_adx_trade_ids = []
    health_cache = {}   # date_str -> bool, computed once per calendar day

    def scanner(regime, open_trades):
        at = sys.modules.get('auto_trader') or __import__('auto_trader')
        thresholds = get_thresholds_fn(regime)
        scan_order = at.catalyst_priority + [s for s in at.FULL_UNIVERSE if s not in at.catalyst_priority]
        entries, attempted = [], []

        weak_health_ok = True
        if weak_health_window and regime == 'WEAK':
            er = sys.modules.get('equity_replay')
            today_str = str(er.FakeDatetime._now.date())
            if today_str not in health_cache:
                con = er._REAL_CONNECT(er.REPLAY_DB_PATH)
                try:
                    health_cache[today_str] = _weak_adx_health_ok(
                        con, weak_adx_trade_ids, today_str, weak_health_window)
                finally:
                    con.close()
            weak_health_ok = health_cache[today_str]

        # Computed ONCE per scan cycle (not per symbol) — both are market-wide
        # readings, same cost class as get_regime() itself.
        breadth = None
        corr = None
        if breadth_max or corr_max:
            er = sys.modules.get('equity_replay')
            if er is not None:
                now = er.FakeDatetime._now
                universe_syms = [s for s in at.FULL_UNIVERSE if s in er._bars5]
                if breadth_max:
                    breadth = universe_breadth(er._bars5, universe_syms, now, now.date())
                if corr_max:
                    corr = universe_correlation(er._daily, universe_syms, now.date())

        for symbol in scan_order:
            if symbol in at.traded_today:
                continue
            if any(t['symbol'] == symbol for t in open_trades):
                continue
            if len(open_trades) + len(entries) + len(attempted) >= at.MAX_OPEN_TRADES:
                break
            try:
                sig = at.get_intraday_signals(symbol)
                if sig is None:
                    continue
                price = sig['price']
                if price < 5 or price > 800:
                    continue

                cands = layer1_candidates(sig, thresholds, keltner_conviction)
                # Weather Advisory eligibility gate: a template can still fire
                # in an off-regime if not restricted (reversion templates,
                # default), but ADX_TREND only competes in a trend-conducive
                # regime — see TEMPLATE_ELIGIBLE_REGIMES for the data behind this.
                cands = [c for c in cands
                        if regime in eligible_regimes.get(c['template'],
                                                            {'STRONG','NORMAL','CAUTIOUS','WEAK','CHOPPY'})]
                # Breadth / correlation gates — see TEMPLATE_BREADTH_MAX /
                # TEMPLATE_CORR_MAX comments for the data. Both fail open
                # (None) rather than blocking everything if uncomputable.
                if breadth is not None:
                    cands = [c for c in cands
                            if breadth <= breadth_max.get(c['template'], 1.0)]
                if corr is not None:
                    cands = [c for c in cands
                            if corr <= corr_max.get(c['template'], 1.0)]
                winner = resolve_tie(cands)
                if winner is None:
                    continue
                strategy_name, side = winner['template'], winner['side']
                is_weak_adx = (regime == 'WEAK' and strategy_name == 'ADX_TREND')
                if is_weak_adx and not weak_health_ok:
                    continue
                size_mult = TEMPLATE_SIZE_MULT[strategy_name]
                setup_tag = f"FISHFINDER_{strategy_name}"

                if side == 'LONG' and at.daily_bull_count >= at.MAX_DAILY_BULL_TRADES:
                    continue
                if side == 'SHORT' and at.daily_bear_count >= at.MAX_DAILY_BEAR_TRADES:
                    continue

                sl, target, risk_pct, reward_pct, rr = at.calc_sl_target(symbol, price, side)
                sector   = at.get_symbol_sector(symbol)
                deployed = at.get_deployed_capital()
                capital  = at.get_position_capital('A', False, deployed) * size_mult
                if capital < 100:
                    continue
                risk_per_share = round((price - sl) if side == 'LONG' else (sl - price), 4)
                atr_shares = int(at.MAX_LOSS_PER_TRADE / risk_per_share) if risk_per_share > 0 else int(capital / price)
                shares = max(1, min(int(capital / price), atr_shares))

                attempted.append(symbol)
                trade_id = at.place_trade(
                    symbol, price, shares, sl, target, setup_tag, 'A',
                    rsi=sig['rsi'], vol_ratio=sig['vol_ratio'],
                    confidence=int(sig.get('adx') or 50), sector=sector, side=side,
                )
                if trade_id:
                    if is_weak_adx:
                        weak_adx_trade_ids.append(trade_id)
                    at.traded_today.add(symbol)
                    at.save_traded_today()
                    at.open_positions[symbol] = trade_id
                    at.first_bar_strong_trades[trade_id] = False
                    if side == 'LONG':
                        at.daily_bull_count += 1
                    else:
                        at.daily_bear_count += 1
                    entries.append({'symbol': symbol, 'price': price, 'shares': shares,
                                    'sl': sl, 'target': target, 'side': side})
                    at._l3_pending[trade_id] = {
                        'sym': symbol, 'entry_time': at.datetime.now(at.ET),
                        'entry_price': price, 'direction': side,
                    }
            except Exception:
                pass  # matches live's per-symbol fail-soft (auto_trader.py:3588-3589)

        if entries:
            at.send_telegram(f"FISHFINDER — {len(entries)} entries")
        return entries

    return scanner


def _run_replay(start, end, scanner_override=None, label=''):
    """Runs the rebuilt equity_replay over [start, end]. If scanner_override is
    given, monkeypatches at._scan_regime_adaptive for the duration (save/restore,
    matching every other equity_replay.py patch pattern) — otherwise the real,
    untouched live function runs. Returns equity_replay's daily summaries list."""
    import equity_replay as er
    import auto_trader as at

    er.set_now(er.ET.localize(__import__('datetime').datetime.combine(
        __import__('datetime').date.fromisoformat(start), __import__('datetime').time(9, 35))))
    print(f'[{label}] preloading bars for {len(at.FULL_UNIVERSE)} symbols…')
    import pandas as pd
    er.preload(at.FULL_UNIVERSE, start, str(pd.Timestamp(end) + pd.Timedelta(days=1)))
    print(f'[{label}]   {len(er._bars5)} symbols with 5-min bars, {len(er._daily)} with daily')

    _orig_scanner = at._scan_regime_adaptive
    if scanner_override is not None:
        at._scan_regime_adaptive = scanner_override
    try:
        with er.sqlite_guard():
            summaries = er.replay_run(start, end)
    finally:
        at._scan_regime_adaptive = _orig_scanner

    # NOT sum(s['pnl'] for s in summaries): each day's summary comes from
    # at.get_daily_pnl(), which filters WHERE entry_date=<that day> -- so a
    # trade held overnight (entered day N, exited day N+k) never lands in
    # ANY day's summary: day N's snapshot is taken before it exits (still
    # OPEN, filtered out), and day N+k's summary only covers trades that
    # ENTERED on day N+k. Confirmed on the Aug 2026 full-history run: 54
    # multi-day-held trades worth +$2,500.87 were completely invisible to
    # this accumulation while still being correctly recorded in the DB --
    # the real total (query DB directly) was +$2,361.32, not the -$140
    # the old sum(summaries) math reported. Query the replay DB directly
    # instead -- ground truth, not a derived accumulation.
    con = er._REAL_CONNECT(er.REPLAY_DB_PATH)
    row = con.execute(
        "SELECT COUNT(*), SUM(pnl), SUM(status='WIN') FROM trades WHERE status IN ('WIN','LOSS')"
    ).fetchone()
    con.close()
    n, total, wins = row[0] or 0, row[1] or 0.0, row[2] or 0
    print(f"[{label}] Trades: {n} | WR: {wins}/{n} = {wins/n*100:.1f}%" if n else f'[{label}] Trades: 0')
    print(f"[{label}] Total P&L: ${total:+,.0f}")
    return summaries


def run_old_design(start, end):
    """Control arm — the real, untouched live _scan_regime_adaptive."""
    return _run_replay(start, end, scanner_override=None, label='OLD (live)')


def run_new_design(start, end, get_thresholds_fn=None, eligible_regimes=None,
                   breadth_max=None, corr_max=None, weak_health_window=None,
                   keltner_conviction=1.0, label='NEW (fish finder)'):
    """Fish Finder + Weather Advisory arm."""
    scanner = _make_new_design_scanner(get_thresholds_fn, eligible_regimes, breadth_max,
                                        corr_max, weak_health_window, keltner_conviction)
    return _run_replay(start, end, scanner_override=scanner, label=label)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--selftest', action='store_true',
                    help='run layer1_candidates/resolve_tie against synthetic sig dicts, no replay needed')
    ap.add_argument('--old', action='store_true', help='run only the old (live) design')
    ap.add_argument('--new', action='store_true', help='run only the new (Fish Finder) design')
    ap.add_argument('--compare', action='store_true', help='run both, old then new')
    ap.add_argument('--hybrid', action='store_true',
                    help='new design + TEMPLATE_ELIGIBLE_REGIMES gating (implies --new)')
    ap.add_argument('--tuned-rsi', action='store_true',
                    help='use TUNED_THRESHOLDS (rsi_hi=75) instead of BASE_THRESHOLDS (implies --new)')
    ap.add_argument('--breadth-gate', action='store_true',
                    help='apply TEMPLATE_BREADTH_MAX (ADX_TREND blocked above 60%% universe breadth) (implies --new)')
    ap.add_argument('--corr-gate', action='store_true',
                    help='apply TEMPLATE_CORR_MAX (ADX_TREND blocked above 0.35 universe correlation) (implies --new)')
    ap.add_argument('--weak-health-gate', action='store_true',
                    help='apply the trailing-7-trading-day WEAK-regime ADX_TREND health gate '
                         '(implies --new) — see WEAK_HEALTH_WINDOW comment for the data behind it')
    ap.add_argument('--keltner-boost', action='store_true',
                    help='raise KELTNER_REVERT conviction from flat 1.0 to KELTNER_CONVICTION=2.0 '
                         '(implies --new) — see KELTNER_CONVICTION comment for the data behind it')
    ap.add_argument('--start'); ap.add_argument('--end')
    a = ap.parse_args()

    if a.selftest:
        cases = [
            ('pure ADX_TREND LONG', {'adx': 40, 'chg_5d_up': True, 'chg_5d_down': False,
                                     'rsi': 50, 'above_keltner_upper': False, 'below_keltner_lower': False}),
            ('pure RSI_REVERT SHORT', {'adx': 15, 'chg_5d_up': False, 'chg_5d_down': False,
                                       'rsi': 82, 'above_keltner_upper': False, 'below_keltner_lower': False}),
            ('overlap: ADX_TREND LONG + RSI_REVERT SHORT (conflicting)',
             {'adx': 30, 'chg_5d_up': True, 'chg_5d_down': False,
              'rsi': 72, 'above_keltner_upper': False, 'below_keltner_lower': False}),
            ('nothing qualifies', {'adx': 10, 'chg_5d_up': False, 'chg_5d_down': False,
                                   'rsi': 50, 'above_keltner_upper': False, 'below_keltner_lower': False}),
        ]
        th = effective_thresholds('NORMAL')
        print(f'BASE_THRESHOLDS at NORMAL (all deltas 0, should equal live 25/70/30): {th}')
        assert th == {'adx_min': 25, 'rsi_hi': 70, 'rsi_lo': 30}, 'baseline equivalence broken'
        print()
        for name, sig in cases:
            cands = layer1_candidates(sig, th)
            winner = resolve_tie(cands, log_fn=print)
            print(f'{name}: candidates={cands}')
            print(f'  -> winner: {winner}\n')
        print('selftest OK')
    elif a.old or a.new or a.compare or a.hybrid or a.tuned_rsi or a.breadth_gate or a.corr_gate \
            or a.weak_health_gate or a.keltner_boost:
        if not (a.start and a.end):
            ap.error('--start/--end required')
        if a.old or a.compare:
            run_old_design(a.start, a.end)
        if a.new or a.compare or a.hybrid or a.tuned_rsi or a.breadth_gate or a.corr_gate \
                or a.weak_health_gate or a.keltner_boost:
            thresholds_fn = None
            label_bits = []
            if a.tuned_rsi:
                thresholds_fn = lambda regime: effective_thresholds(regime, base=TUNED_THRESHOLDS)
                label_bits.append('tuned-rsi75')
            eligible = TEMPLATE_ELIGIBLE_REGIMES if a.hybrid else \
                {'ADX_TREND': {'STRONG','NORMAL','CAUTIOUS','WEAK','CHOPPY'},
                 'KELTNER_REVERT': {'STRONG','NORMAL','CAUTIOUS','WEAK','CHOPPY'},
                 'RSI_REVERT': {'STRONG','NORMAL','CAUTIOUS','WEAK','CHOPPY'}}
            if a.hybrid:
                label_bits.append('hybrid')
            breadth = TEMPLATE_BREADTH_MAX if a.breadth_gate else {}
            if a.breadth_gate:
                label_bits.append('breadth60')
            corr = TEMPLATE_CORR_MAX if a.corr_gate else {}
            if a.corr_gate:
                label_bits.append('corr35')
            weak_health = WEAK_HEALTH_WINDOW if a.weak_health_gate else None
            if a.weak_health_gate:
                label_bits.append('weakhealth7')
            keltner_conviction = KELTNER_CONVICTION if a.keltner_boost else 1.0
            if a.keltner_boost:
                label_bits.append('keltnerboost2.0')
            label = 'NEW (fish finder' + (' + ' + '+'.join(label_bits) if label_bits else '') + ')'
            run_new_design(a.start, a.end, get_thresholds_fn=thresholds_fn,
                          eligible_regimes=eligible, breadth_max=breadth, corr_max=corr,
                          weak_health_window=weak_health, keltner_conviction=keltner_conviction,
                          label=label)
    else:
        ap.print_help()
