#!/usr/bin/env python3
"""
MNQ Live Decoder — 24/7 market microstructure analyzer.

Runs every 60 seconds across all CME sessions (pauses 5–6pm ET maintenance only).
Decodes market state per session, applies proposed entry-quality rules,
tracks simulated trades, and builds conviction data day over day.

Sessions handled:
  GLOBEX    18:00–02:59 ET  overnight drift / range building
  LONDON    03:00–09:29 ET  IB formation, first signal
  NY_OPEN   09:30–10:29 ET  ORB / VWAP anchor
  MIDDAY    10:30–11:59 ET  primary entry window
  LUNCH     12:00–12:59 ET  reduced entries
  AFTERNOON 13:00–15:29 ET  secondary entry window
  EOD       15:30–16:59 ET  closing character (next-day bias)
  MAINT     17:00–17:59 ET  CME maintenance — only true close, sleep

KPIs captured every scan (full set for entry/exit decisions):
  price, VWAP, extension, VWAP slope
  phase (Markup/Distribution/Markdown/Accumulation/Consolidation)
  RSI(14), momentum bars, trend strength (ADX proxy)
  volume character (RVOL vs session avg), order flow (vol×direction)
  ATR(14), session high/low, range position (0–100%)
  overnight high/low, prior close, gap
  IB range + close position (London)
  ORB range + break direction (NY)
  distribution bar flag, signal staleness

Hypotheses under test (numbers are guesses — sim validates them):
  ext_limit  = 70pts above VWAP → LONG blocked
  dist_mult  = 1.8× vol spike + red bar at session high → distribution
  stale_scans = 5 consecutive scans same signal → stale

Log:  logs/live_rule_sim.log
Run:  venv/bin/python futures/live_rule_sim.py   (launchd: com.sushil.trading.mnq_decoder)
"""

import os, sys, time, json, requests
from datetime import datetime, date, timedelta

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_DIR, '..'))   # must come before futures.* imports
from dotenv import load_dotenv
import pytz
from futures.gate_audit import log_block as _ga_block, log_enter as _ga_enter

load_dotenv(os.path.join(_DIR, '..', '.env'))

ET       = pytz.timezone('America/New_York')
BRIDGE   = 'http://localhost:8000'
SYMBOL   = 'MNQ'
LOG_PATH = os.path.join(_DIR, '..', 'logs', 'live_rule_sim.log')
TG_TOKEN = os.getenv('FUTURES_TELEGRAM_TOKEN', '')
TG_CHAT  = os.getenv('FUTURES_TELEGRAM_CHAT_ID', '')
CM       = '20260918'

# ── Hypothesis thresholds (sim will tell us if these are right) ───────────────
EXT_LIMIT        = 70.0    # pts above VWAP → LONG blocked (is 70 right?)
DIST_VOL_MULT    = 1.8     # vol spike multiplier  (is 1.8× right?)
DIST_PROX_PTS    = 20.0    # within N pts of session high = distribution zone
DIST_BLOCK_SCANS = 2       # scans to block after dist bar (is 2 right?)
STALE_SCANS      = 5       # signal stale after N unacted scans (is 5 right?)

# ── Trade sim ─────────────────────────────────────────────────────────────────
BE_PTS     = 30.0
TRAIL_PTS  = 60.0
TRAIL_GAP  = 20.0
TIGHT_PTS  = 85.0
TIGHT_GAP  = 10.0
TARGET_PTS = 99.0
STOP_PTS   = 36.0
POINT_VAL  = 2.0

# ── Global state ──────────────────────────────────────────────────────────────
_pos              = None
_daily_pnl        = 0.0
_sim_trades       = []
_dist_block_left  = 0
_last_signal      = None
_signal_streak    = 0
_dist_bars_today  = []
_session_date     = None
_prior_close      = None
_overnight_hi     = None
_overnight_lo     = None
_ib_hi            = None     # London IB
_ib_lo            = None
_orb_hi           = None     # NY ORB
_orb_lo           = None
_last_session     = None
_scan_count       = 0

# ── Outcome tracking — forward price check after each decision ────────────────
# Each item: {ts, signal, price, verdict, gate, due_15m, due_30m, s15, s30}
_pending_outcomes: list = []

# ── Session stats — accumulates across scans, summarised at session transition ─
_sess_stats: dict = {
    'session': None, 'start': None,
    'vwap_prev': None, 'vwap_crossings': 0,
    'max_ext_long': 0.0, 'max_ext_short': 0.0,
    'phases': {}, 'rvols': [],
    'sig_long': 0, 'sig_short': 0, 'sig_none': 0,
    'enters': 0, 'skips': 0, 'skip_gates': {},
    'scan_count': 0,
}


# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

DATA_LOG = os.path.join(_DIR, '..', 'logs', 'decoder_data.jsonl')   # one JSON record per scan

def log(msg: str):
    ts   = datetime.now(ET).strftime('%H:%M:%S')
    # stdout only — launchd captures to log file via StandardOutPath
    print(f'[{ts}] {msg}', flush=True)

def _reset_sess_stats(session: str):
    global _sess_stats
    _sess_stats = {
        'session': session, 'start': datetime.now(ET).strftime('%H:%M'),
        'vwap_prev': None, 'vwap_crossings': 0,
        'max_ext_long': 0.0, 'max_ext_short': 0.0,
        'phases': {}, 'rvols': [],
        'sig_long': 0, 'sig_short': 0, 'sig_none': 0,
        'enters': 0, 'skips': 0, 'skip_gates': {},
        'scan_count': 0,
    }

def _update_sess_stats(session: str, ext: float, rvol: float,
                       phase: str, vwap: float, signal, verdict: str,
                       block_gates: list):
    """Called every scan to accumulate session-level stats."""
    global _sess_stats
    if _sess_stats['session'] != session:
        return
    s = _sess_stats
    s['scan_count'] += 1
    s['max_ext_long']  = max(s['max_ext_long'],  ext if ext > 0 else 0.0)
    s['max_ext_short'] = min(s['max_ext_short'], ext if ext < 0 else 0.0)
    s['phases'][phase] = s['phases'].get(phase, 0) + 1
    s['rvols'].append(rvol)
    # VWAP crossing: sign changed from previous scan
    if s['vwap_prev'] is not None:
        if (s['vwap_prev'] > 0) != (ext > 0):
            s['vwap_crossings'] += 1
    s['vwap_prev'] = ext
    if signal == 'LONG':   s['sig_long']  += 1
    elif signal == 'SHORT': s['sig_short'] += 1
    else:                   s['sig_none']  += 1
    if verdict == 'ENTER':  s['enters'] += 1
    elif verdict == 'SKIP':
        s['skips'] += 1
        for g in block_gates:
            s['skip_gates'][g] = s['skip_gates'].get(g, 0) + 1

def _emit_session_summary(end_session: str, end_time: str):
    """Write a SESSION summary record when a session ends."""
    s = _sess_stats
    if not s['session'] or s['scan_count'] == 0:
        return
    avg_rvol = sum(s['rvols']) / len(s['rvols']) if s['rvols'] else 0
    dominant_phase = max(s['phases'], key=s['phases'].get) if s['phases'] else 'UNKNOWN'
    # Character: choppy if VWAP crossed ≥3 times, trending if dominant phase MARKUP/MARKDOWN
    character = ('CHOPPY'   if s['vwap_crossings'] >= 3 else
                 'TRENDING' if dominant_phase in ('MARKUP', 'MARKDOWN') else
                 'RANGING')
    log_record({
        'type':           'SESSION',
        'date':           str(date.today()),
        'session':        s['session'],
        'start':          s['start'],
        'end':            end_time,
        'scans':          s['scan_count'],
        'character':      character,
        'vwap_crossings': s['vwap_crossings'],
        'max_ext_long':   round(s['max_ext_long'], 1),
        'max_ext_short':  round(s['max_ext_short'], 1),
        'avg_rvol':       round(avg_rvol, 3),
        'dominant_phase': dominant_phase,
        'phase_counts':   s['phases'],
        'sig_long':       s['sig_long'],
        'sig_short':      s['sig_short'],
        'sig_none':       s['sig_none'],
        'enters':         s['enters'],
        'skips':          s['skips'],
        'skip_gates':     s['skip_gates'],
    })
    log(f'  📋 SESSION {s["session"]} → {character}  '
        f'VWAP×{s["vwap_crossings"]}  '
        f'ext±{max(s["max_ext_long"], abs(s["max_ext_short"])):.0f}pts  '
        f'enters={s["enters"]}  skips={s["skips"]}')

def _check_outcomes(now: datetime, price: float):
    """Score pending decisions against actual price movement."""
    global _pending_outcomes
    remaining = []
    for o in _pending_outcomes:
        scored_15 = o.get('s15', False)
        scored_30 = o.get('s30', False)

        for mins, key, flag in [(15, 'due_15m', 's15'), (30, 'due_30m', 's30')]:
            if not o.get(flag) and now >= o[f'due_{mins}m']:
                pts     = (price - o['price']) * (1 if o['signal'] == 'LONG' else -1)
                correct = (pts < 0) if o['verdict'] == 'SKIP' else (pts > 0)
                log_record({
                    'type':           'OUTCOME',
                    'window_min':     mins,
                    'decision_ts':    o['ts'],
                    'session':        o['session'],
                    'signal':         o['signal'],
                    'verdict':        o['verdict'],
                    'gate':           o['gate'],
                    'decision_price': o['price'],
                    'outcome_price':  round(price, 2),
                    'pts_moved':      round(pts, 2),
                    'gate_correct':   int(correct),
                })
                o[flag] = True

        if o.get('s15') and o.get('s30'):
            continue   # fully scored — drop it
        remaining.append(o)
    _pending_outcomes = remaining

def _queue_outcome(now: datetime, signal: str, price: float,
                   verdict: str, gate: str, session: str):
    """Queue a decision for forward outcome scoring at +15min and +30min."""
    _pending_outcomes.append({
        'ts':      now.isoformat(),
        'signal':  signal,
        'price':   price,
        'verdict': verdict,
        'gate':    gate,
        'session': session,
        'due_15m': now + timedelta(minutes=15),
        'due_30m': now + timedelta(minutes=30),
        's15':     False,
        's30':     False,
    })

def log_record(record: dict):
    """Write structured JSON record for post-session analysis."""
    with open(DATA_LOG, 'a') as f:
        f.write(json.dumps(record) + '\n')

def tg(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
            json={'chat_id': TG_CHAT, 'text': f'🔬 DECODER | {msg}'},
            timeout=5
        )
    except Exception:
        pass


# ── Session detection ─────────────────────────────────────────────────────────
def get_session(h: int, m: int) -> str:
    hm = h * 60 + m
    if 17*60 <= hm < 18*60:   return 'MAINT'
    if 18*60 <= hm or hm < 3*60: return 'GLOBEX'
    if 3*60  <= hm < 9*60+30: return 'LONDON'
    if 9*60+30 <= hm < 10*60+30: return 'NY_OPEN'
    if 10*60+30 <= hm < 12*60:   return 'MIDDAY'
    if 12*60 <= hm < 13*60:      return 'LUNCH'
    if 13*60 <= hm < 15*60+30:   return 'AFTERNOON'
    return 'EOD'


# ── Bridge helpers ─────────────────────────────────────────────────────────────
def get_bars(days: int = 2):
    """Returns bars for the current CME trading session.

    CME futures day starts at 6pm ET the prior evening, not at midnight.
    Filtering by calendar date would miss 6pm–midnight GLOBEX bars and
    anchor the VWAP wrongly for London and early GLOBEX scans.
    We use a session cutoff of 18:00 ET the previous calendar day instead.
    """
    try:
        r    = requests.get(
            f'{BRIDGE}/history/futures/{SYMBOL}?duration={days}+D&bar_size=5+mins'
            f'&rth=false&contract_month={CM}', timeout=15
        )
        raw  = r.json()
        raw  = raw if isinstance(raw, list) else raw.get('bars', raw.get('data', []))
        out  = []
        now_et = datetime.now(ET)
        # Session cutoff: 6pm today if we're past 6pm, else 6pm yesterday
        if now_et.hour >= 18:
            cutoff = now_et.replace(hour=18, minute=0, second=0, microsecond=0)
        else:
            cutoff = (now_et - timedelta(days=1)).replace(
                hour=18, minute=0, second=0, microsecond=0)
        for b in raw:
            ts  = datetime.fromisoformat(
                (b.get('time') or b.get('ts', '')).replace('Z', '+00:00')
            ).astimezone(ET)
            if ts >= cutoff:
                out.append({'t': ts, 'o': b['open'], 'h': b['high'],
                            'l': b['low'],  'c': b['close'], 'v': b.get('volume', 0)})
        return out
    except Exception as e:
        log(f'  bridge error: {e}')
        return []

def get_prior_close():
    """Fetch prior trading day's last bar close."""
    try:
        r   = requests.get(
            f'{BRIDGE}/history/futures/{SYMBOL}?duration=3+D&bar_size=5+mins'
            f'&rth=false&contract_month={CM}', timeout=15
        )
        raw = r.json()
        raw = raw if isinstance(raw, list) else raw.get('bars', raw.get('data', []))
        today = date.today()
        prev  = [b for b in raw
                 if datetime.fromisoformat(
                     (b.get('time') or b.get('ts', '')).replace('Z','+00:00')
                 ).astimezone(ET).date() < today]
        return float(prev[-1]['close']) if prev else None
    except Exception:
        return None


# ── KPI computations ──────────────────────────────────────────────────────────
def compute_vwap(bars):
    num = den = 0.0
    out = []
    for b in bars:
        num += b['c'] * b['v']
        den += b['v']
        out.append(num / den if den else b['c'])
    return out

def vwap_slope(vwaps, n: int = 5) -> float:
    return (vwaps[-1] - vwaps[-n]) / n if len(vwaps) >= n else 0.0

def compute_atr(bars, n: int = 14) -> float:
    if len(bars) < 2:
        return 10.0
    trs = [max(b['h'] - b['l'],
               abs(b['h'] - bars[i-1]['c']),
               abs(b['l'] - bars[i-1]['c']))
           for i, b in enumerate(bars[1:], 1)]
    trs = trs[-n:]
    return sum(trs) / len(trs) if trs else 10.0

def compute_rsi(bars, n: int = 14) -> float:
    if len(bars) < n + 1:
        return 50.0
    closes = [b['c'] for b in bars[-(n+1):]]
    gains  = [max(0.0, closes[i] - closes[i-1]) for i in range(1, len(closes))]
    losses = [max(0.0, closes[i-1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(gains)/n, sum(losses)/n
    return 100.0 - (100.0 / (1 + ag/al)) if al else 100.0

def compute_adx_proxy(bars, n: int = 10) -> float:
    """ADX-like trend strength 0–100 from directional bar count."""
    if len(bars) < n:
        return 0.0
    recent = bars[-n:]
    up   = sum(1 for b in recent if b['c'] > b['o'])
    down = sum(1 for b in recent if b['c'] < b['o'])
    return abs(up - down) / n * 100.0

def range_position(price, lo, hi) -> float:
    """Where is price in session range? 0 = at low, 100 = at high."""
    if hi == lo:
        return 50.0
    return round((price - lo) / (hi - lo) * 100, 1)

def momentum_str(bars, n: int = 6) -> str:
    return ''.join('▲' if b['c'] >= b['o'] else '▼' for b in bars[-n:])

def consecutive_run(bars) -> tuple:
    """Returns (green_streak, red_streak) at tail of bars."""
    g = r = 0
    for b in reversed(bars[-8:]):
        if b['c'] >= b['o']:
            if r: break
            g += 1
        else:
            if g: break
            r += 1
    return g, r

def detect_phase(bars, vwaps, idx) -> str:
    if idx < 8:
        return 'EARLY'
    win   = bars[max(0, idx-9): idx+1]
    price = bars[idx]['c']
    vwap  = vwaps[idx]
    ext   = price - vwap
    slope = vwap_slope(vwaps)
    sh    = max(b['h'] for b in bars[:idx+1])

    vols  = [b['v'] for b in win]
    avg_v = sum(vols)/len(vols) if vols else 1

    dist  = sum(1 for b in win if b['c'] < b['o']
                and b['v'] > avg_v * 1.5 and b['h'] >= sh - DIST_PROX_PTS)
    if dist >= 1 and price >= sh - DIST_PROX_PTS:
        return 'DISTRIBUTION'

    highs = [b['h'] for b in win]
    lows  = [b['l'] for b in win]
    hh    = all(highs[i] >= highs[i-1] for i in range(1, len(highs)))
    ll    = all(lows[i]  <= lows[i-1]  for i in range(1, len(lows)))

    if ext > 15 and slope > 0 and hh:   return 'MARKUP'
    if ext < -15 and slope < 0 and ll:  return 'MARKDOWN'

    rngs   = [b['h'] - b['l'] for b in win]
    avg_rng = sum(rngs)/len(rngs)
    if abs(ext) < 35 and avg_rng < 20:  return 'CONSOLIDATION'
    if ext > 0 and not hh:              return 'RECOVERY'
    if ext < 0 and not ll:              return 'BASING'
    return 'NEUTRAL'

def order_flow(bars, idx) -> str:
    """Classify recent order flow: BUYING / SELLING / MIXED / QUIET"""
    if idx < 5:
        return 'QUIET'
    win   = bars[max(0,idx-4):idx+1]
    vols  = [b['v'] for b in win]
    avg_v = sum(vols)/len(vols) if vols else 1
    heavy = [(b['c'] >= b['o'], b['v']) for b in win if b['v'] > avg_v * 1.3]
    if not heavy:
        return 'QUIET'
    buys  = sum(1 for up, _ in heavy if up)
    sells = sum(1 for up, _ in heavy if not up)
    if buys > sells:   return 'BUYING'
    if sells > buys:   return 'SELLING'
    return 'MIXED'

def check_distribution_bar(bars, idx):
    if idx < 5:
        return False, 0.0, 0
    rv    = [bars[i]['v'] for i in range(idx-5, idx)]
    avg_v = sum(rv)/len(rv) if rv else 0
    if not avg_v:
        return False, 0.0, 0
    sh    = max(b['h'] for b in bars[:idx+1])
    b     = bars[idx]
    ratio = b['v'] / avg_v
    flag  = ratio >= DIST_VOL_MULT and b['c'] < b['o'] and b['h'] >= sh - DIST_PROX_PTS
    return flag, ratio, int(avg_v)

def detect_signal(bars, vwaps, idx) -> str | None:
    if idx < 3:
        return None
    p, v = bars[idx]['c'], vwaps[idx]
    p1   = bars[idx-1]['c']
    p2   = bars[idx-2]['c']
    if p > v and p > p1 and p1 > p2:  return 'LONG'
    if p < v and p < p1 and p1 < p2:  return 'SHORT'
    return None


# ── IB / ORB tracking ─────────────────────────────────────────────────────────
def update_ib(bars):
    """Track London IB (3am–4am ET)."""
    global _ib_hi, _ib_lo
    ib = [b for b in bars if 3 <= b['t'].hour < 4]
    if ib:
        _ib_hi = max(b['h'] for b in ib)
        _ib_lo = min(b['l'] for b in ib)

def update_orb(bars):
    """Track NY ORB (9:30–10:00am ET, first 30 min)."""
    global _orb_hi, _orb_lo
    orb = [b for b in bars if b['t'].hour == 9 and b['t'].minute >= 30]
    if orb:
        _orb_hi = max(b['h'] for b in orb)
        _orb_lo = min(b['l'] for b in orb)


# ── Position management ────────────────────────────────────────────────────────
def open_pos(side: str, price: float, session: str):
    global _pos
    stop = (price - STOP_PTS) if side == 'LONG' else (price + STOP_PTS)
    _pos = {'side': side, 'entry': price, 'stop': stop,
            'peak': price, 'be': False, 'trail': False, 'session': session}
    msg = (f'SIM ENTRY {side} @{price:.2f}  [{session}]\n'
           f'Stop={stop:.2f}  Target={price+TARGET_PTS:.2f}  BE@{price+BE_PTS:.2f}')
    log(f'  📍 {msg}')
    tg(msg)

def update_pos(price: float):
    global _pos, _daily_pnl, _sim_trades
    if not _pos:
        return
    p    = _pos
    mult = 1 if p['side'] == 'LONG' else -1
    ppts = (price - p['entry']) * mult
    if (p['side']=='LONG' and price > p['peak']) or \
       (p['side']=='SHORT' and price < p['peak']):
        p['peak'] = price
    peak_pts = (p['peak'] - p['entry']) * mult

    if not p['be'] and peak_pts >= BE_PTS:
        p['stop'], p['be'] = p['entry'], True
        log(f'  BE triggered → stop={p["stop"]:.2f}')

    if peak_pts >= TRAIL_PTS:
        t = (p['peak']-TRAIL_GAP) if p['side']=='LONG' else (p['peak']+TRAIL_GAP)
        if (p['side']=='LONG' and t>p['stop']) or (p['side']=='SHORT' and t<p['stop']):
            p['stop'] = t
            if not p['trail']:
                p['trail'] = True
                log(f'  Trail(20) → stop={p["stop"]:.2f}')

    if peak_pts >= TIGHT_PTS:
        t = (p['peak']-TIGHT_GAP) if p['side']=='LONG' else (p['peak']+TIGHT_GAP)
        if (p['side']=='LONG' and t>p['stop']) or (p['side']=='SHORT' and t<p['stop']):
            p['stop'] = t

    reason = None
    if p['side'] == 'LONG':
        if price <= p['stop']:    reason = f'stop @{p["stop"]:.2f}'
        elif ppts >= TARGET_PTS:  reason = f'target +{ppts:.0f}pts'
    else:
        if price >= p['stop']:    reason = f'stop @{p["stop"]:.2f}'
        elif ppts >= TARGET_PTS:  reason = f'target +{ppts:.0f}pts'

    if reason:
        pnl = ppts * POINT_VAL
        _daily_pnl += pnl
        _sim_trades.append({'side':p['side'],'entry':p['entry'],'exit':price,
                            'pts':ppts,'usd':pnl,'reason':reason,'session':p['session']})
        msg = (f'SIM EXIT {p["side"]}  {p["entry"]:.2f}→{price:.2f}\n'
               f'{ppts:+.0f}pts  ${pnl:+.2f}  ({reason})\nDay: ${_daily_pnl:+.2f}')
        log(f'  🏁 {msg}')
        tg(msg)
        _pos = None


# ── Daily reset ────────────────────────────────────────────────────────────────
def daily_reset():
    global _daily_pnl, _sim_trades, _dist_block_left, _last_signal
    global _signal_streak, _dist_bars_today, _session_date
    global _prior_close, _overnight_hi, _overnight_lo
    global _ib_hi, _ib_lo, _orb_hi, _orb_lo, _pos, _last_session
    global _pending_outcomes, _sess_stats

    today = date.today()
    if _session_date == today:
        return
    _session_date     = today
    _daily_pnl        = 0.0
    _sim_trades       = []
    _dist_block_left  = 0
    _last_signal      = None
    _signal_streak    = 0
    _dist_bars_today  = []
    _pos              = None
    _ib_hi = _ib_lo   = None
    _orb_hi = _orb_lo = None
    _overnight_hi     = None
    _overnight_lo     = None
    _last_session     = None
    _pending_outcomes = []           # drop carry-over outcomes from prior day
    _reset_sess_stats(None)          # blank slate for new day's first session
    _prior_close      = get_prior_close()
    log(f'  Daily reset — prior close={_prior_close}')


# ── Main scan ─────────────────────────────────────────────────────────────────
def scan():
    global _dist_block_left, _last_signal, _signal_streak
    global _dist_bars_today, _overnight_hi, _overnight_lo, _last_session, _scan_count

    now     = datetime.now(ET)
    h, m    = now.hour, now.minute
    session = get_session(h, m)

    daily_reset()

    bars = get_bars(days=2)
    if not bars:
        log(f'  [{session}] no bars')
        return

    # Update IB/ORB trackers
    update_ib(bars)
    update_orb(bars)

    # Overnight high/low (all bars before NY open)
    ovn = [b for b in bars if b['t'].hour < 9 or (b['t'].hour == 9 and b['t'].minute < 30)]
    if ovn:
        _overnight_hi = max(b['h'] for b in ovn)
        _overnight_lo = min(b['l'] for b in ovn)

    # NY bars: strictly today's calendar date, 9:30am onwards.
    # Must also filter by date because get_bars() now includes prior-day 6pm–midnight
    # bars (hours 18–23 pass the "hour > 9" test without the date guard).
    today_et = date.today()
    ny_bars = [b for b in bars
               if b['t'].date() == today_et and
               (b['t'].hour > 9 or (b['t'].hour == 9 and b['t'].minute >= 30))]

    # Use all session bars for overnight windows (VWAP anchors from 6pm),
    # NY bars for intraday sessions (VWAP anchors from 9:30am).
    active_bars = ny_bars if ny_bars and session not in ('GLOBEX','LONDON') else bars
    if not active_bars:
        active_bars = bars

    vwaps  = compute_vwap(active_bars)
    idx    = len(active_bars) - 1
    last   = active_bars[idx]
    price  = last['c']
    vwap   = vwaps[idx]
    ext    = price - vwap
    slope  = vwap_slope(vwaps)
    slope_sym = '↑' if slope > 0.5 else ('↓' if slope < -0.5 else '→')

    sh  = max(b['h'] for b in active_bars)
    sl  = min(b['l'] for b in active_bars)
    rpos = range_position(price, sl, sh)
    atr  = compute_atr(active_bars)
    rsi  = compute_rsi(active_bars)
    adx  = compute_adx_proxy(active_bars)
    mom  = momentum_str(active_bars)
    flow = order_flow(active_bars, idx)
    phase = detect_phase(active_bars, vwaps, idx)
    g, r  = consecutive_run(active_bars)
    streak = f'{g}▲' if g > 1 else (f'{r}▼' if r > 1 else '—')

    rv    = [b['v'] for b in active_bars]
    avg_v = sum(rv)/len(rv) if rv else 1
    rvol  = last['v'] / avg_v if avg_v else 1.0
    vol_label = ('DRY' if rvol < 0.3 else 'THIN' if rvol < 0.7 else
                 'NORMAL' if rvol < 1.3 else 'ELEVATED' if rvol < 2.0 else 'SPIKE')

    # Gap from prior close
    gap_str = ''
    if _prior_close and session in ('LONDON','NY_OPEN','MIDDAY'):
        gap = price - _prior_close
        gap_str = f'Gap {gap:+.0f}pts from prior close {_prior_close:.2f}'

    # IB / ORB reference
    ib_str = orb_str = ''
    if _ib_hi:
        ib_cp = (price - _ib_lo) / (_ib_hi - _ib_lo) * 100 if _ib_hi != _ib_lo else 50
        ib_str = f'IB {_ib_lo:.0f}–{_ib_hi:.0f} ({_ib_hi-_ib_lo:.0f}pts)  @{ib_cp:.0f}%'
    if _orb_hi:
        orb_str = f'ORB {_orb_lo:.0f}–{_orb_hi:.0f} ({_orb_hi-_orb_lo:.0f}pts)'

    # Distribution bar check
    is_dist, dist_ratio, avg_vol = check_distribution_bar(active_bars, idx)
    if is_dist:
        _dist_block_left = DIST_BLOCK_SCANS
        _dist_bars_today.append({'t': last['t'].strftime('%H:%M'), 'price': price,
                                 'vol': last['v'], 'ratio': dist_ratio})
        log(f'  🚨 DIST BAR  vol={last["v"]}  {dist_ratio:.1f}×avg  '
            f'price={price:.2f}  blocking {DIST_BLOCK_SCANS} scans')
        tg(f'🚨 DIST BAR [{session}] {last["t"].strftime("%H:%M")}\n'
           f'price={price:.2f}  vol={last["v"]} ({dist_ratio:.1f}× avg)\n'
           f'Phase={phase}  ext={ext:+.0f}pts')

    # Score any pending outcomes now that we have fresh price
    _check_outcomes(now, price)

    # Update open position.
    # Snapshot before update — prevents same-bar re-entry if stop fires this scan.
    had_pos = bool(_pos)
    if _pos:
        update_pos(price)

    # ── Print decode ──────────────────────────────────────────────────────────
    sep = '─' * 62
    _scan_count += 1

    # Session transition: emit summary of outgoing session, start new stats bucket
    if session != _last_session:
        if _last_session:
            _emit_session_summary(_last_session, last['t'].strftime('%H:%M'))
        _reset_sess_stats(session)
        log('═' * 62)
        log(f'  ▶ SESSION: {session}')
        if _ib_hi:   log(f'    {ib_str}')
        if _orb_hi:  log(f'    {orb_str}')
        if gap_str:  log(f'    {gap_str}')
        log('═' * 62)
        _last_session = session

    log(sep)
    log(f'  {last["t"].strftime("%H:%M")} [{session}] │ MNQ {price:.2f} │ {phase}')
    log(f'  VWAP {vwap:.2f} ({ext:+.0f}pts) {slope_sym}  ATR {atr:.1f}  RSI {rsi:.0f}  ADX {adx:.0f}')
    log(f'  Vol {last["v"]:,} ({vol_label} {rvol:.1f}×)  Flow: {flow}  Momentum: {mom}  Run: {streak}')
    log(f'  Range {sl:.0f}–{sh:.0f}  Pos {rpos:.0f}%  '
        + (f'  IB: {ib_str}' if ib_str else '')
        + (f'  ORB: {orb_str}' if orb_str else ''))

    if _pos:
        ppts = (price - _pos['entry']) * (1 if _pos['side']=='LONG' else -1)
        log(f'  IN POS: {_pos["side"]} @{_pos["entry"]:.2f}  '
            f'uPnL={ppts:+.0f}pts(${ppts*POINT_VAL:+.2f})  stop={_pos["stop"]:.2f}')

    # ── Entry logic ───────────────────────────────────────────────────────────
    # Use had_pos (before this scan's update_pos) so a stop firing this bar
    # doesn't immediately allow re-entry at the same price.
    can_enter = session in ('NY_OPEN','MIDDAY','AFTERNOON') and not had_pos
    signal    = detect_signal(active_bars, vwaps, idx) if can_enter else None

    # Only update streak during entry windows — LUNCH/GLOBEX must not reset it.
    # A LONG signal that built up through MIDDAY should still be stale in AFTERNOON.
    if can_enter:
        if signal == _last_signal:
            _signal_streak += 1
        else:
            _signal_streak = 1 if signal else 0
        _last_signal = signal

    # ── Existing system gates (mirrored here so we can validate them too) ────
    # Gate X1: RVOL gate (system uses 0.3× threshold — is it right?)
    rx1 = ('🚫', f'RVOL {rvol:.2f}× < 0.3 threshold') if rvol < 0.30 \
          else ('✅', f'RVOL {rvol:.2f}× passes 0.3 gate')

    # Gate X2: Regime proxy (STRONG/NORMAL/QUIET from ADX + phase)
    regime_proxy = ('STRONG' if adx > 60 and phase == 'MARKUP' else
                    'QUIET'  if adx < 20 else 'NORMAL')
    rx2 = ('✅', f'regime={regime_proxy}') if regime_proxy != 'QUIET' \
          else ('⚠️', f'regime=QUIET (low conviction)')

    # Gate X3: BE proximity — if in pos, how far to BE/trail/target?
    be_str = trail_str = tgt_str = ''
    if _pos:
        ppts     = (price - _pos['entry']) * (1 if _pos['side']=='LONG' else -1)
        be_str   = f'BE in {BE_PTS-ppts:.0f}pts' if not _pos['be'] else 'BE ✅'
        trail_str = f'Trail in {TRAIL_PTS-ppts:.0f}pts' if not _pos['trail'] else 'Trail ✅'
        tgt_str  = f'Target in {TARGET_PTS-ppts:.0f}pts'

    # Gate X4: Signal grade proxy (mirrors A+/A scoring — based on ext, flow, momentum)
    if signal:
        score = 50
        score += 15 if flow == ('BUYING' if signal=='LONG' else 'SELLING') else 0
        score += 10 if g >= 2 and signal == 'LONG' else 0
        score += 10 if r >= 2 and signal == 'SHORT' else 0
        score += 15 if regime_proxy == 'STRONG' else 0
        score -= 10 if vol_label in ('DRY','THIN') else 0
        score -= 20 if phase == 'DISTRIBUTION' and signal == 'LONG' else 0
        grade = 'A+' if score >= 80 else ('A' if score >= 65 else ('B' if score >= 50 else 'C'))
        rx4 = ('✅', f'grade={grade} ({score}pts)') if grade in ('A+','A') \
              else ('🚫', f'grade={grade} ({score}pts) — below A threshold')
    else:
        grade, score, rx4 = '', 0, ('—', 'no signal')

    if signal:
        # Proposed new gates
        if signal == 'LONG':
            ra = ('🚫', f'ext {ext:+.0f} > {EXT_LIMIT:.0f}pt limit') if ext > EXT_LIMIT \
                 else ('✅', f'ext {ext:+.0f}pts OK')
        else:
            ra = ('🚫', f'ext {ext:+.0f} < -{EXT_LIMIT:.0f}pt limit') if ext < -EXT_LIMIT \
                 else ('✅', f'ext {ext:+.0f}pts OK')

        rb = ('🚫', f'dist block ({_dist_block_left} scans)') \
             if _dist_block_left > 0 and signal == 'LONG' else ('✅', 'no dist block')

        rc = ('🚫', f'stale ({_signal_streak} scans)') if _signal_streak > STALE_SCANS \
             else ('✅', f'fresh ({_signal_streak} scans)')

        # All gates combined
        all_gates = {'X1_rvol': rx1, 'X2_regime': rx2, 'X4_grade': rx4,
                     'A_ext': ra, 'B_dist': rb, 'C_stale': rc}

        existing_block  = rx1[0] == '🚫' or rx4[0] == '🚫'
        proposed_block  = ra[0] == '🚫' or rb[0] == '🚫' or rc[0] == '🚫'
        any_block       = existing_block or proposed_block

        log(f'  Signal: {signal}  Grade: {grade} ({score}pts)')
        log(f'  — Existing gates —')
        log(f'    X1-rvol:    {rx1[0]} {rx1[1]}')
        log(f'    X2-regime:  {rx2[0]} {rx2[1]}')
        log(f'    X4-grade:   {rx4[0]} {rx4[1]}')
        log(f'  — Proposed gates —')
        log(f'    A-ext:      {ra[0]} {ra[1]}')
        log(f'    B-dist:     {rb[0]} {rb[1]}')
        log(f'    C-stale:    {rc[0]} {rc[1]}')

        if any_block:
            ex_reasons  = [r for k,(icon,r) in all_gates.items() if icon=='🚫' and k.startswith('X')]
            new_reasons = [r for k,(icon,r) in all_gates.items() if icon=='🚫' and not k.startswith('X')]
            block_gate_names = [k for k,(icon,_) in all_gates.items() if icon=='🚫']
            verdict = 'SKIP'
            log(f'  VERDICT: ❌ SKIP')
            if ex_reasons:  log(f'    existing gates: {" | ".join(ex_reasons)}')
            if new_reasons: log(f'    proposed gates: {" | ".join(new_reasons)}')
            log(f'  → What happens next validates which gate(s) were right')
            gate_str = ';'.join(block_gate_names)
            _queue_outcome(now, signal, price, 'SKIP', gate_str, session)
            try: _ga_block('DECODER', 'MNQ', signal, gate_str or 'UNKNOWN', grade, price, session)
            except Exception: pass
        else:
            block_gate_names = []
            verdict = 'ENTER'
            log(f'  VERDICT: ✅ {signal} — all gates pass, entering sim @{price:.2f}')
            _queue_outcome(now, signal, price, 'ENTER', 'none', session)
            try: _ga_enter('DECODER', 'MNQ', signal, grade, price, session)
            except Exception: pass
            open_pos(signal, price, session)

        if be_str: log(f'  Exit track: {be_str}  {trail_str}  {tgt_str}')
        _update_sess_stats(session, ext, rvol, phase, vwap, signal, verdict, block_gate_names)
    else:
        verdict = 'NO_SIGNAL'
        block_gate_names = []
        all_gates = {}
        grade, score = '', 0
        log(f'  Signal: none')
        _update_sess_stats(session, ext, rvol, phase, vwap, None, 'NO_SIGNAL', [])

    log(sep)

    # ── Structured JSON record — every KPI, every gate, every scan ───────────
    log_record({
        'ts':           last['t'].isoformat(),
        'date':         str(date.today()),
        'session':      session,
        'price':        price,
        'vwap':         round(vwap, 2),
        'vwap_ext':     round(ext, 2),
        'vwap_slope':   round(slope, 3),
        'atr':          round(atr, 2),
        'rsi':          round(rsi, 1),
        'adx':          round(adx, 1),
        'phase':        phase,
        'regime_proxy': regime_proxy,
        'vol':          last['v'],
        'vol_label':    vol_label,
        'rvol':         round(rvol, 3),
        'flow':         flow,
        'momentum':     mom,
        'green_run':    g,
        'red_run':      r,
        'session_hi':   sh,
        'session_lo':   sl,
        'range_pos':    rpos,
        'ib_hi':        _ib_hi,
        'ib_lo':        _ib_lo,
        'orb_hi':       _orb_hi,
        'orb_lo':       _orb_lo,
        'prior_close':  _prior_close,
        'ovn_hi':       _overnight_hi,
        'ovn_lo':       _overnight_lo,
        'dist_flag':    is_dist,
        'dist_ratio':   round(dist_ratio, 2),
        'dist_block':   _dist_block_left,
        'signal':       signal,
        'signal_streak':_signal_streak,
        'grade':        grade,
        'grade_score':  score,
        # Gate verdicts: pass=True, fail=False, na=None
        'gate_X1_rvol':   rx1[0] == '✅' if signal else None,
        'gate_X2_regime': rx2[0] == '✅' if signal else None,
        'gate_X4_grade':  rx4[0] == '✅' if signal else None,
        'gate_A_ext':     ra[0] == '✅'  if signal else None,
        'gate_B_dist':    rb[0] == '✅'  if signal else None,
        'gate_C_stale':   rc[0] == '✅'  if signal else None,
        'verdict':        verdict if signal else 'NO_SIGNAL',
        # Position state
        'in_pos':      _pos is not None,
        'pos_side':    _pos['side']  if _pos else None,
        'pos_entry':   _pos['entry'] if _pos else None,
        'pos_upnl':    round((price-_pos['entry'])*(1 if _pos and _pos['side']=='LONG' else -1), 2) if _pos else None,
        'pos_be_done': _pos['be']    if _pos else None,
    })

    if _dist_block_left > 0:
        _dist_block_left -= 1


# ── EOD summary ────────────────────────────────────────────────────────────────
def eod_summary():
    log('═' * 62)
    log(f'  SIM DAY END — {date.today()}')
    log(f'  Trades: {len(_sim_trades)}  P&L: ${_daily_pnl:+.2f}')
    for t in _sim_trades:
        log(f'    {t["side"]} [{t["session"]}]  {t["entry"]:.2f}→{t["exit"]:.2f}  '
            f'{t["pts"]:+.0f}pts  ${t["usd"]:+.2f}  ({t["reason"]})')
    log(f'  Distribution bars: {len(_dist_bars_today)}')
    for d in _dist_bars_today:
        log(f'    {d["t"]}  price={d["price"]:.2f}  vol={d["vol"]}  {d["ratio"]:.1f}×avg')
    log('═' * 62)
    tg(f'📊 SIM EOD — {date.today()}\n'
       f'Trades: {len(_sim_trades)}  P&L: ${_daily_pnl:+.2f}\n'
       f'Dist bars: {len(_dist_bars_today)}\n'
       + '\n'.join(f'{t["side"]} [{t["session"]}] {t["pts"]:+.0f}pts ${t["usd"]:+.2f}'
                   for t in _sim_trades))


# ── Main loop ─────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    log('═' * 62)
    log(f'  MNQ LIVE DECODER 24/7 — started {datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")}')
    log(f'  Hypotheses: ext<{EXT_LIMIT}pts | dist>{DIST_VOL_MULT}× | stale>{STALE_SCANS}scans')
    log(f'  Pauses: 17:00–18:00 ET (CME maintenance only)')
    log('═' * 62)
    tg(f'🔬 MNQ Decoder 24/7 ON\n'
       f'Ext limit={EXT_LIMIT}pts | dist={DIST_VOL_MULT}× | stale={STALE_SCANS}scans\n'
       f'Decoding every 60s. Only dark hour: 5–6pm ET.')

    last_eod_date = None

    while True:
        try:
            now     = datetime.now(ET)
            h, m    = now.hour, now.minute
            weekday = now.weekday()   # 0=Mon … 4=Fri, 5=Sat, 6=Sun

            # CME weekend close: Friday 5pm ET → Sunday 6pm ET
            # Saturday is entirely dark; don't scan at all.
            is_weekend = (
                (weekday == 4 and h >= 17) or   # Friday 5pm+
                weekday == 5 or                  # all of Saturday
                (weekday == 6 and h < 18)        # Sunday before 6pm
            )
            if is_weekend:
                if weekday == 6:     days_ahead = 0   # wake today at 18:00
                elif weekday == 5:   days_ahead = 1   # wake tomorrow (Sunday) at 18:00
                else:                days_ahead = 2   # Friday → Sunday
                wake = (now + timedelta(days=days_ahead)).replace(
                    hour=18, minute=0, second=5, microsecond=0)
                secs = (wake - now).total_seconds()
                log(f'  CME weekend — sleeping {secs/3600:.1f}hrs until Sunday 6pm ET')
                time.sleep(max(secs, 60))
                continue

            # CME daily maintenance: 5–6pm on weekdays (Mon–Thu; Fri covered above)
            if h == 17:
                wake = now.replace(hour=18, minute=0, second=5)
                secs = (wake - now).total_seconds()
                log(f'  CME maintenance 5–6pm ET — sleeping {secs/60:.0f}min')
                time.sleep(max(secs, 60))
                continue

            # EOD summary once per day (after 4pm, before 5pm)
            today = date.today()
            if h == 16 and last_eod_date != today:
                if _pos:
                    bars = get_bars()
                    if bars:
                        update_pos(bars[-1]['c'])
                    # Force-close any position still open at 4pm — sim is day-only.
                    if _pos:
                        close_price = bars[-1]['c'] if bars else _pos['entry']
                        ppts = (close_price - _pos['entry']) * (1 if _pos['side']=='LONG' else -1)
                        pnl  = ppts * POINT_VAL
                        _daily_pnl += pnl
                        _sim_trades.append({
                            'side': _pos['side'], 'entry': _pos['entry'],
                            'exit': close_price,  'pts': ppts, 'usd': pnl,
                            'reason': 'EOD force-close', 'session': _pos['session']
                        })
                        log(f'  🔔 EOD force-close {_pos["side"]} {_pos["entry"]:.2f}→{close_price:.2f}  {ppts:+.0f}pts')
                        _pos = None
                eod_summary()
                last_eod_date = today

            scan()
            time.sleep(60)

        except KeyboardInterrupt:
            log('  Interrupted')
            eod_summary()
            break
        except Exception as e:
            log(f'  ERROR: {e}')
            time.sleep(60)
