# auto_trader.py v2 — Consolidated single-process auto trader
# Absorbs: trader.py (monitoring, WhatsApp commands, evening summary)
#          scheduler.py (catalyst scan, voice summary, nightly learning)
# Depends on: bridge.py (IBKR gateway), tunnel.py (WhatsApp webhook)
# Command: python auto_trader.py

from dotenv import load_dotenv
load_dotenv()

import os, json, time, requests, yfinance as yf, pandas as pd, numpy as np, subprocess
from datetime import datetime, date, timedelta
import pytz, pyttsx3, io, base64, threading
import matplotlib
matplotlib.use('Agg')
import mplfinance as mpf
import anthropic
from apscheduler.schedulers.background import BackgroundScheduler
from database import (
    init_db, log_trade_entry, log_trade_exit,
    get_open_trades, get_daily_pnl, get_win_rate,
    update_trade_stop, update_trade_shares, get_trade_entry_date, get_today_trades,
    get_strategy_weights, get_today_entry_counts,
    get_sector_grade, log_scan_candidate, enrich_scan_log,
)
from catalyst_detector import run_catalyst_scan
from learner import run_learning_cycle
from options.learner_options import run_options_learning_cycle
from portfolio_status import format_all as _portfolio_all

ET = pytz.timezone('America/New_York')

# ── Credentials ───────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
VIEWER_CHAT_IDS  = {5225043215}   # Ruhi — REGIME, STATUS, chart analysis only
ANTHROPIC_KEY    = os.getenv('ANTHROPIC_KEY')
BRIDGE           = os.getenv("BRIDGE_URL", "http://127.0.0.1:8000")
TG_API           = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
_ai              = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ── Settings ──────────────────────────────────────────────
SCAN_INTERVAL     = 300      # 5 min
MONITOR_INTERVAL  = 30       # 30 sec — fast position monitor (hard stop checks only)
TOTAL_CAPITAL     = 10000    # max capital deployed across all positions
MAX_OPEN_TRADES   = 5        # 5 × $2,000 = $10K fully deployed
MAX_DAILY_BULL_TRADES  = 20  # recycling: slot limit (MAX_OPEN_TRADES=5) is the real cap
MAX_DAILY_BEAR_TRADES  = 20  # recycling: slot limit (MAX_OPEN_TRADES=5) is the real cap
MAX_PREMARKET_TRADES   = 2       # pre-market earnings gap entries (sub-cap inside bull daily count)
PREMARKET_GAP_LARGE    = 6.0    # large-cap (price ≥ $150): mega/large caps rarely move more — 6% is a real surprise
PREMARKET_GAP_MID      = 8.0    # mid-cap  ($50–$149):  more volatile, need stronger signal to avoid gap-and-crap
PREMARKET_GAP_SMALL    = 10.0   # small-cap (< $50):     highest bar — these can gap 20%+ on noise
PREMARKET_VOL_MIN      = 200_000  # pre-market volume floor — liquidity gate
PREMARKET_HOLD_PCT     = 0.97   # gap must hold ≥ 97% of its pre-market high (not fading)
MAX_RISK_PCT        = 8.0    # hard ATR cap — removed 2% floor, trust the ATR fully
MIN_RR              = 2.5    # min reward:risk for entry qualification
MAX_LOSS_PER_TRADE  = 150    # dollar circuit breaker: $150 = 5% of $3,000 position (raised May 19)
DAILY_PROFIT_TARGET = 400    # at +$400 session P&L: protect gains, no new entries
EOD_CLOSE_HOUR      = 15     # 3:45pm ET EOD close — exit unless conviction to hold overnight
EOD_CLOSE_MINUTE    = 45
MAX_RSI_5M        = 85   # skip entry if 5-min RSI >85 — intraday candle is exhausted
MIN_VOLUME_RATIO  = 1.3
MAX_PER_SECTOR    = 5        # max open positions per sector
TG_POLL_OFFSET    = 0        # tracks last processed Telegram update
ATR_PERIOD        = 14
ATR_STOP_MULT     = 2.0      # initial stop: entry - 2×ATR (swing needs breathing room)
ATR_TRAIL_MULT    = 1.5      # trail: 1.5×ATR below rolling session high
ATR_FADE_MULT     = 1.0      # momentum fade: drop > 1×ATR from session high
PCT_TRAIL_ACTIVATE = 1.5     # % trail activates at +1.5% gain (protects dead zone below ATR activation)
PCT_TRAIL_GAP      = 0.5     # trail 0.5% below session high (Gap 1 fix — validated May 2026)
MAX_HOLD_DAYS     = 1        # hard max hold: exit any position after 1 business day (~24h)
NO_ENTRY_BEFORE   = 10       # wait until 10:00am — let opening range establish
NO_ENTRY_AFTER    = 15       # no new entries at/after 3:00pm ET
MIN_REGIME_SCANS  = 2        # regime must be confirmed for N consecutive scans before entry
MIN_TODAY_GAIN    = 3.0      # stock must be up ≥3% today — capture early-stage moves, not extended
MAX_DAILY_LOSS    = 200      # stop new entries if daily P&L < -$200
LUNCH_AVOID_START = (11, 30) # no new entries from 11:30am ET (lunch chop)
LUNCH_AVOID_END   = (12, 45) # resume entries at 12:45pm ET
# P&L protection: once session peak ≥ $200, if it drops 25% → cut non-runners
# Validated May 2026: +$34/month, 0 false positives, +$539/yr
# P&L protection and afternoon gate use % of capital so thresholds scale automatically
# when TOTAL_CAPITAL grows — no manual update needed at go-live or scaling events
PL_PROTECT_PEAK_PCT = 2.0    # trigger when peak session P&L ≥ 2% of capital ($200 at $10K)
PL_PROTECT_PEAK     = TOTAL_CAPITAL * PL_PROTECT_PEAK_PCT / 100
PL_PROTECT_DROP     = 0.25   # 25% drawdown from peak fires the cut
# Afternoon gate: no new entries (long or short) after 12pm if morning realized ≥ 1.5% of capital
# Data: afternoon LONG 44.8% WR / -$0.91 avg, afternoon SHORT 18.2% WR / -$6.56 avg
AFTERNOON_GATE_HOUR      = 12
AFTERNOON_GATE_PCT       = 1.5   # gate threshold: 1.5% of capital ($150 at $10K)
AFTERNOON_GATE_THRESHOLD = TOTAL_CAPITAL * AFTERNOON_GATE_PCT / 100
FIRST_BAR_QUALITY = True     # strong first 30-min bar → +15% capital + enable partial exit
ORB_ENTRY_CUTOFF  = (11, 30) # ORB signal only valid before 11:30am — late breaks are just resistance

# ── US Market Holidays 2026 (NYSE observed dates) ─────────
US_HOLIDAYS_2026 = {
    date(2026, 1,  1),   # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Presidents Day
    date(2026, 4,  3),   # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7,  3),   # Independence Day (observed, Jul 4 = Saturday)
    date(2026, 9,  7),   # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
}

# ── Power-play batting order: sector priority for slot allocation ─────────────
# Lower number = higher priority (gets a slot first in morning power-play window)
# Built from 3-day scan_log data: catalyst stocks 2× more likely to be top movers;
# CONSUMER historically weakest (67% WR, +2.7% avg max vs 100% WR for other sectors).
# STRONG sectors (SEMIS/NUCLEAR/DEFENCE) open; CONSUMER protects tail.
# Revisit quarterly as scan_log.actual_day_high_pct data accumulates.
_SLOT_SECTOR_PRIORITY = {
    'SEMIS': 0, 'SEMI': 0, 'NUCLEAR': 0, 'DEFENCE': 0,  # STRONG — open the innings
    'TECH': 1, 'AI_TECH': 1, 'CLOUD': 1, 'QUANTUM_CRYPTO': 1,  # top-order
    'CLEAN_ENERGY': 1, 'COMMODITIES': 1, 'OTHER': 1,    # mid-order
    'BIOTECH': 2, 'ENERGY': 2, 'FINTECH': 2,             # WEAK — lower-order
    'CONSUMER': 3,                                        # tail — weakest mover historically
}

# ── Persistence ───────────────────────────────────────────
_DIR              = os.path.dirname(os.path.abspath(__file__))
TRADED_TODAY_FILE = os.path.join(_DIR, 'traded_today.json')

# ── In-memory state ───────────────────────────────────────
traded_today      = set()
open_positions    = {}       # sym → trade_id
_gateway_unstable_until = None   # datetime: no new entries until this time (post-reconnect freeze)
price_history     = {}       # trade_id → [prices]
session_high      = {}       # trade_id → highest price seen (LONG trades)
session_low       = {}       # trade_id → lowest price seen (SHORT trades)
atr_cache         = {}       # sym → (date_str, atr_value)
daily_bull_count        = 0
daily_bear_count        = 0
daily_sympathy_count    = 0       # separate cap — sympathy plays on top of regular bull slots
active_sympathy_triggers = {}     # sym → {trigger, trigger_move, gap} — populated at open
sympathy_scan_done      = False   # fires once per day at market open
pm_scan_done            = False   # pre-market scan fires once per day
catalyst_priority  = []       # symbols from today's catalyst scan
_longs_paused      = False    # set by PAUSE LONGS / WATCH — blocks new long entries only
_watch_mode        = False    # set by WATCH — sends regime snapshots every 30 min
_watch_last_sent   = None     # datetime of last WATCH regime update
tg_update_id       = 0        # Telegram polling offset
regime_history     = []       # last N regime readings for confirmation
spy_open_price     = None     # SPY price at market open (set on first post-open scan)
sector_strength    = {}       # ETF ticker → % change today, updated each scan
key_levels         = {}       # sym → {pm_high, pm_low, prior_close, orb_high, orb_low}
trade_entry_times  = {}       # trade_id → datetime of entry (for no-move exit)
earnings_cache     = {}       # symbol → (date_str, days_to_next_earnings)
partial_done_trades     = {}  # trade_id → locked_pnl_usd — trades that had 50% sold at 1R
first_bar_strong_trades = {}  # trade_id → bool — entry was on a strong first-bar day
_last_regime        = None   # last valid get_regime() result — held when SPY bars are empty
peak_session_pnl    = 0.0    # highest session P&L seen today (realized + unrealized)
pl_protect_active   = False  # True when peak has dropped 25% from ≥$200 — cut non-runners
_morning_pnl_snap   = None   # P&L frozen at first post-noon scan — afternoon gate uses this
_daily_loss_alerted = False  # ensures circuit breaker Telegram fires once per day only

# ── Bear exclusions — stocks with insufficient bear backtest WR ──
BEAR_EXCLUDED = {'RDW'}   # RDW: 60% bear WR (below 80% threshold), bull-only addition

# ── ETF symbols — no earnings calendar, skip earnings gate ────────
ETF_SYMBOLS = {
    'XLE', 'XBI', 'SMH', 'ITA', 'XLK', 'XLF', 'XLI', 'XLU', 'XLY',
    'XME', 'XAR', 'URA', 'GDX', 'ARKK',
}

# ── Sympathy plays — sector follower boost on mega-cap earnings beats ──
# When a trigger beats earnings big (>5%), sector peers gap in sympathy.
# Backtest: 5% trigger + 2% gap → 79% WR, +$40/trade, 0% stop rate (bull only).
# UNH excluded: MLR is company-specific, no shared sector driver (52% WR).
SYMPATHY_MAP = {
    'NVDA':  ['SMCI', 'AMD', 'LRCX', 'MU', 'AMAT', 'MRVL', 'QCOM', 'AVGO'],
    'META':  ['SNAP', 'PINS', 'GOOGL', 'TTD'],
    'MSFT':  ['CRM', 'ORCL', 'NOW', 'DDOG', 'PLTR'],
    'AAPL':  ['QCOM', 'AVGO', 'KEYS', 'SWKS'],
    'AMZN':  ['SHOP', 'MELI', 'OKTA'],
    'GOOGL': ['META', 'SNAP', 'PINS', 'TTD'],
    'XOM':   ['CVX', 'COP', 'SLB', 'HAL', 'OXY', 'PSX', 'VLO'],
    'CVX':   ['XOM', 'COP', 'SLB', 'HAL', 'OXY'],
    'JPM':   ['BAC', 'GS', 'MS', 'WFC', 'C', 'SCHW'],
    'GS':    ['MS', 'JPM', 'BAC', 'SCHW'],
    'WMT':   ['TGT', 'COST', 'DG', 'DLTR'],
    'HD':    ['LOW'],
}
SYMPATHY_TRIGGER_THRESH   = 0.05   # trigger stock must gap >5% on earnings day
SYMPATHY_GAP_THRESH       = 0.02   # sympathy stock must gap >2% same direction
SYMPATHY_SCORE_BOOST      = 20     # points added to grade_setup score
MAX_DAILY_SYMPATHY_TRADES = 2      # separate cap — additive on top of regular bull cap

# ── Universe ──────────────────────────────────────────────
FULL_UNIVERSE = list(dict.fromkeys([
    # ── Tier 1+2: 62%+ win rate (v6 confirmed) ───────────
    'AAPL', 'PLTR', 'COHR', 'IONQ', 'HOOD', 'JPM', 'IREN', 'NUTX',
    'LITE', 'VST', 'ITA', 'NFLX', 'ORCL', 'OKLO', 'AMZN', 'GOOGL',
    'CRM', 'QBTS',
    # ── Tier 3: 54-59% win rate, positive avg ────────────
    'TOST', 'AVGO', 'NBIS', 'CLS', 'RKLB', 'CNQ',
    # ── Borderline: 50%, positive avg, good sample ───────
    'AMD', 'RKT', 'NU',
    # ── Mega cap ──────────────────────────────────────────
    'MSFT', 'META', 'GS',
    # ── Catalyst-only (scan for events) ──────────────────
    'CRWV', 'SMCI', 'RBRK', 'AI', 'RGTI',
    'USAR', 'FSLR', 'CCJ', 'UUUU', 'DNN',
    'LLY', 'NTLA', 'BEAM',
    'APLD', 'SOUN', 'BBAI',
    # ── Gap-and-go confirmed (5Y backtest: 56-60% WR) ────
    'ON', 'LRCX', 'DDOG', 'MDB',
    # ── Momentum / sector-leader ──────────────────────────
    'POET', 'EOSE', 'INDI', 'NVDA', 'INTC', 'TSLA',
    # ── Energy: oil/gas/pipelines — macro + sector ETF days ──
    'CVX', 'XOM', 'OXY', 'SLB', 'HAL', 'DVN', 'XLE',
    # ── Healthcare / Pharma: catalyst + FDA days ─────────
    'UNH', 'MRNA', 'PFE', 'ABBV', 'ISRG', 'DXCM', 'HIMS', 'XBI',
    # ── Consumer Discretionary: sentiment + retail days ──
    'COST', 'NKE', 'SBUX', 'CMG', 'UBER',
    # ── Financials expanded: rate-sensitive + crypto ─────
    'BAC', 'C', 'WFC', 'V', 'MA', 'COIN',
    # ── Defence / Industrial: geopolitical + infra ───────
    'RTX', 'LMT', 'NOC', 'CAT', 'DE',
    # ── Semiconductor expansion ───────────────────────────
    'QCOM', 'MRVL', 'KLAC', 'AMAT', 'MU', 'SMH',
    # ── Clean Energy / EV / Battery materials ────────────
    'LAC', 'RIVN', 'NIO', 'CHPT',
    # ── Commodities / Mining ─────────────────────────────
    'FCX', 'NEM', 'MP',
    # ── May 1 2026 additions (5Y backtest validated) ──────
    # APP  94% WR $74 EV — Applovin, AI advertising momentum
    # MARA 75% WR $96 EV — Bitcoin miner, high variance/high EV, 15.5 trades/yr
    # ARM  93% WR $105 EV — ARM Holdings, IPO 2023 (limited history, probation)
    # AXON 98% WR $67 EV — textbook behaviour, cleanest setup stock tested
    # SHOP 95% WR $84 EV — Shopify, e-commerce momentum (Canadian co.)
    'APP', 'MARA', 'ARM', 'AXON', 'SHOP',
    # ── May 4 2026 additions (full suite validated: bull+bear+WF+stress+MC) ──
    # MSTR 93% WR $91 EV — MicroStrategy, BTC treasury; gap-and-go bad (17%) but full stack 93%
    # ONDS 92% WR $106 EV — Ondas Holdings, drone/rail autonomy (4yr data, probation)
    # RDW  87% WR $107 EV — Redwire Corp, space tech; BULL ONLY (bear 60% WR too thin)
    # VERI 80% WR $99 EV — Veritone, AI platform; bear 90% WR — both directions
    'MSTR', 'ONDS', 'RDW', 'VERI',
    # ── May 6 2026 re-additions (re-backtested with full signal stack) ──
    # JOBY 87% WR $81 EV — eVTOL/aviation. Was dropped at 38% WR (gap-and-go only).
    #      Full stack (ORB+VWAP+RS+volume): 87% WR, all 6 years profitable, 13 trades/yr
    'JOBY',
    # ── May 24 2026 — DNA-screened expansion (batch_backtest.py validated) ──
    # 49/49 cleared: DNA screen + full 5yr backtest + IS/OOS split + stress test
    # Avg WR 90.3% full, avg OOS_WR 89.5% | ⚠️ BSX/HOLX (OOS 60-67%), CIFR (N=22)
    # HIGH_VOL (7): gap fills 70% — wait for VWAP reclaim, tight trail
    'CLSK','WULF','HUT','ARRY','RIOT','EQT','CIFR',
    # INSTITUTIONAL (24): gaps stick 75% — extended no-move timer
    'CPNG','SITM','KTOS','ACLS','CTRA','CACI','FTNT','IBKR','ONTO','SAIC',
    'BWXT','SAIA','HWM','GDDY','EW','KKR','TPR','GILD','GE','TXT',
    'YUM','BSX','HOLX','TT',
    # MOMENTUM (18): standard behavior
    'UPST','CELH','HL','ZM','DUOL','RBLX','WFRD','TTD','TWLO','AG',
    'DOCU','ZS','HUBS','OKTA','DECK','LULU','PANW','AEM',
    # ── May 26 2026 — deep-validated additions (bull+bear+OOS+stress+DNA) ─────
    # AEHR  90.4% WR bull (N=73) / 100% WR bear (N=14) — SEMIS, MOMENTUM cluster
    # APD   100% WR bull (N=9)  / 100% WR bear (N=9)  — COMMODITIES, INSTITUTIONAL — ⚠️ N=9, ~1.4 trades/yr, monitor to N=30
    # HXL   95% WR bull (N=20) / 100% WR bear (N=26) — DEFENCE, INSTITUTIONAL — monitor to N=30
    # SSYS  94.4% WR bull (N=54) / 89.7% WR bear (N=29) — TECH, MOMENTUM cluster
    'AEHR', 'APD', 'HXL', 'SSYS',
    # ── May 29 2026 — friend-sourced candidates (batch_backtest + bear validated) ──
    # CRDO  93.3% bull WR / 100% bear / OOS 90.9% — AI networking silicon, $794/yr
    # OUST  85.9% bull WR / 96% bear  / OOS 88.6% — Lidar/autonomy, $1216/yr
    # AXTI  89.1% bull WR / 100% bear / OOS 79.2% — Compound semis (GaAs), $893/yr
    # MU    84.8% bull WR / 100% bear / OOS 80.5% — Micron DRAM, 79 trades, $660/yr
    # NBIS  86.1% bull WR / 100% bear / OOS 86.1% — Nebius AI cloud ⚠️ listed Sep 2024
    # FPS   SKIPPED — 3 months history only (IPO Feb 2026), N=5 trades
    'CRDO', 'OUST', 'AXTI', 'MU', 'NBIS',
    # DROPPED — confirmed underperformers (gap-and-go backtest):
    # SMR(24%), SNOW(33%), CRWD(33%)
    # PANW(43% gap-and-go only → re-added May 24 via full A/A+ backtest: 93.2% WR)
    # MS(43%), AFRM(43%), ACHR(44%)
    # SOFI(50% neg avg), HPE(50% neg avg)
]))

# ── DNA Cluster sets (dna_analysis.py, May 2026 — re-run quarterly) ──────────
# HIGH_VOL: gap fills 70% intraday, trend fades after big moves (ATR ~8%)
#   → L1: require VWAP reclaim on gap-up days (penalise naked ORB)
#   → L3: tighter ATR trail (1.0× vs 1.5×) — lock gains fast, don't wait for continuation
HIGH_VOL_SYMBOLS = frozenset([
    'AI','APLD','APP','BBAI','BEAM','CHPT','DNN','EOSE','INDI','IONQ',
    'IREN','JOBY','LAC','MARA','NTLA','NU','NUTX','ONDS','POET','QBTS',
    'RDW','RGTI','RIVN','RKLB','RKT','SOUN','TOST','VERI',
    # May 24 2026 additions (DNA batch)
    'ARRY','CIFR','CLSK','EQT','HUT','RIOT','WULF',
])
# INSTITUTIONAL: gaps stick 75%, slow grind, multi-day continuation at 3d (ATR ~3%)
#   → L3: extend no-move exit timer (300 min vs 240 min) — these consolidate before continuing
INSTITUTIONAL_SYMBOLS = frozenset([
    'AAPL','ABBV','AMAT','AVGO','AXON','BAC','C','CAT','CNQ','COST',
    'CVX','DE','DVN','GOOGL','GS','HAL','HOOD','INTC','ISRG','ITA',
    'JPM','KLAC','LMT','LRCX','MA','MSFT','NKE','NOC','OKLO','ON',
    'OXY','PFE','QCOM','RTX','SBUX','SLB','SMH','UNH','V','VST',
    'WFC','XBI','XLE','XOM',
    # May 24 2026 additions (DNA batch)
    'ACLS','BSX','BWXT','CACI','CPNG','CTRA','EW','FTNT','GE','GDDY',
    'GILD','HOLX','HWM','IBKR','KKR','KTOS','ONTO','SAIC','SAIA','SITM',
    'TPR','TT','TXT','YUM',
    # May 26 2026 additions — ATR ~2-3%, gap_fill 0.46-0.48, institutional grind
    'APD','HXL',
    # May 29 2026 — large-cap, institutional-grade semis
    'MU',
])
# MOMENTUM: standard behavior (gap-go 71%, ATR ~5%) — no cluster overrides needed
# OUTLIER: USAR only — anomalous behavior, treat as MOMENTUM (baseline)

def get_dna_cluster(symbol):
    if symbol in HIGH_VOL_SYMBOLS:      return 'HIGH_VOL'
    if symbol in INSTITUTIONAL_SYMBOLS: return 'INSTITUTIONAL'
    return 'MOMENTUM'

# ── Sector map ────────────────────────────────────────────
SECTOR_MAP = {
    # TECH: AI chips, semis, cloud, mega-cap
    'AAPL':'TECH','MSFT':'TECH','AMZN':'TECH','GOOGL':'TECH','META':'TECH',
    'AMD':'TECH','AVGO':'TECH','NFLX':'TECH','NVDA':'TECH','INTC':'TECH','TSLA':'TECH',
    'COHR':'TECH','LITE':'TECH','CLS':'TECH','SMCI':'TECH','CRWV':'TECH',
    'PLTR':'TECH','NBIS':'TECH','AI':'TECH','CRM':'TECH','ORCL':'TECH','RBRK':'TECH',
    'DDOG':'TECH','MDB':'TECH','ON':'TECH','SOUN':'TECH','BBAI':'TECH',
    # SEMIS: semiconductor equipment and design
    'LRCX':'SEMIS','QCOM':'SEMIS','MRVL':'SEMIS','KLAC':'SEMIS',
    'AMAT':'SEMIS','MU':'SEMIS','SMH':'SEMIS','INDI':'SEMIS','POET':'SEMIS',
    # NUCLEAR: small modular reactors, uranium
    'OKLO':'NUCLEAR','CCJ':'NUCLEAR','UUUU':'NUCLEAR','DNN':'NUCLEAR',
    # FINTECH: neo-banks, payments, trading apps, financials
    'NU':'FINTECH','RKT':'FINTECH','JPM':'FINTECH','GS':'FINTECH',
    'TOST':'FINTECH','BAC':'FINTECH','C':'FINTECH',
    'WFC':'FINTECH','V':'FINTECH','MA':'FINTECH',
    # COIN reclassified: crypto exchange trades like BTC miners (MARA/MSTR) — QUANTUM_CRYPTO
    # HOOD reclassified: high-beta retail trading app — trades like momentum TECH, not bank stocks
    'COIN':'QUANTUM_CRYPTO','HOOD':'TECH',
    # BIOTECH: pharma, gene editing, healthcare
    'LLY':'BIOTECH','NTLA':'BIOTECH','BEAM':'BIOTECH','NUTX':'BIOTECH',
    'UNH':'BIOTECH','MRNA':'BIOTECH','PFE':'BIOTECH','ABBV':'BIOTECH',
    'ISRG':'BIOTECH','DXCM':'BIOTECH','HIMS':'BIOTECH','XBI':'BIOTECH',
    # QUANTUM_CRYPTO: quantum computing + crypto infrastructure
    'IONQ':'QUANTUM_CRYPTO','QBTS':'QUANTUM_CRYPTO','RGTI':'QUANTUM_CRYPTO',
    'IREN':'QUANTUM_CRYPTO','APLD':'QUANTUM_CRYPTO',
    # ENERGY: oil, gas, oilfield services
    'CVX':'ENERGY','XOM':'ENERGY','OXY':'ENERGY','SLB':'ENERGY',
    'HAL':'ENERGY','DVN':'ENERGY','XLE':'ENERGY',
    'FSLR':'ENERGY','EOSE':'ENERGY','VST':'ENERGY','CNQ':'ENERGY',
    # DEFENCE: aerospace, defence, industrials
    'RTX':'DEFENCE','LMT':'DEFENCE','NOC':'DEFENCE','CAT':'DEFENCE',
    'DE':'DEFENCE','ITA':'DEFENCE',
    # CONSUMER: discretionary + staples
    'COST':'CONSUMER','NKE':'CONSUMER','SBUX':'CONSUMER','CMG':'CONSUMER',
    'UBER':'CONSUMER',
    # DEFENCE: aerospace, defence, industrials (extended)
    'RKLB':'DEFENCE','JOBY':'DEFENCE',
    # CONSUMER: discretionary + travel
    'USAR':'CONSUMER',
    # CLEAN_ENERGY: EV, battery materials, charging
    'LAC':'CLEAN_ENERGY','RIVN':'CLEAN_ENERGY','NIO':'CLEAN_ENERGY','CHPT':'CLEAN_ENERGY',
    # COMMODITIES: mining, metals, gold
    'FCX':'COMMODITIES','NEM':'COMMODITIES','MP':'COMMODITIES',
    # Remainder → OTHER (still tradeable, just uncapped)
    # ── May 1 2026 additions ──────────────────────────────
    'APP':'TECH',       # Applovin — AI advertising / ad-tech
    'MARA':'QUANTUM_CRYPTO',  # Marathon Digital — Bitcoin miner
    'ARM':'SEMIS',      # ARM Holdings — chip architecture
    'AXON':'DEFENCE',   # Axon Enterprise — defence/law enforcement tech
    'SHOP':'CONSUMER',  # Shopify — e-commerce platform
    # ── May 4 2026 additions ──────────────────────────────
    'MSTR':'QUANTUM_CRYPTO',  # MicroStrategy — institutional BTC treasury
    'ONDS':'DEFENCE',   # Ondas Holdings — drone/rail autonomy
    'RDW':'DEFENCE',    # Redwire Corp — space tech (BULL ONLY — bear 60% WR)
    'VERI':'TECH',      # Veritone — AI platform / voice AI
    # ── May 24 2026 — DNA batch (49 symbols) ─────────────────
    # HIGH_VOL cluster
    'CLSK':'QUANTUM_CRYPTO','WULF':'QUANTUM_CRYPTO','HUT':'QUANTUM_CRYPTO',
    'RIOT':'QUANTUM_CRYPTO','CIFR':'QUANTUM_CRYPTO',   # crypto miners
    'ARRY':'CLEAN_ENERGY',                              # solar tracking
    'EQT':'ENERGY',                                     # natural gas
    # INSTITUTIONAL cluster
    'KTOS':'DEFENCE','CACI':'DEFENCE','SAIC':'DEFENCE','BWXT':'DEFENCE',
    'HWM':'DEFENCE','GE':'DEFENCE','TXT':'DEFENCE',     # defense/aerospace
    'SITM':'SEMIS','ACLS':'SEMIS','ONTO':'SEMIS',       # semiconductor equipment
    'FTNT':'TECH','GDDY':'TECH','IBKR':'FINTECH',
    'KKR':'FINTECH','CPNG':'CONSUMER','SAIA':'CONSUMER',
    'TPR':'CONSUMER','YUM':'CONSUMER','DECK':'CONSUMER',
    'CTRA':'ENERGY',
    'EW':'BIOTECH','GILD':'BIOTECH','BSX':'BIOTECH','HOLX':'BIOTECH',
    'TT':'DEFENCE',   # Trane Technologies — industrial/HVAC
    # MOMENTUM cluster
    'UPST':'FINTECH','CELH':'CONSUMER','LULU':'CONSUMER',
    'ZM':'TECH','DUOL':'TECH','RBLX':'TECH','TTD':'TECH','TWLO':'TECH',
    'DOCU':'TECH','ZS':'TECH','HUBS':'TECH','OKTA':'TECH','PANW':'TECH',
    'WFRD':'ENERGY',
    'HL':'COMMODITIES','AG':'COMMODITIES','AEM':'COMMODITIES',
    # ── May 26 2026 — new additions ───────────────────────────────────────────
    'AEHR':'SEMIS',         # semiconductor burn-in test equipment (STRONG +15)
    'APD':'COMMODITIES',    # industrial gases (O2, H2, N2) — INSTITUTIONAL, neutral
    'HXL':'DEFENCE',        # carbon fiber composites for F-35/Airbus/military (STRONG +15)
    'SSYS':'TECH',          # industrial 3D printing systems — MOMENTUM (STRONG +15)
    # ── May 29 2026 additions ────────────────────────────────────────────────────
    'CRDO':'SEMIS',         # Credo Technology — AI networking silicon (SerDes/AEC) (STRONG +15)
    'OUST':'TECH',          # Ouster — lidar sensors, autonomous vehicles
    'AXTI':'SEMIS',         # AXT Inc — compound semiconductor substrates (GaAs/InP/Ge) (STRONG +15)
    'MU'  :'SEMIS',         # Micron Technology — DRAM/NAND memory semis (STRONG +15)
    'NBIS':'TECH',          # Nebius Group — AI cloud infra (ex-Yandex) ⚠️ Sep 2024 IPO, monitor
}

# ── Sector ETF proxies for relative strength ─────────────────
SECTOR_ETF_MAP = {
    # Data-driven from 2yr correlation analysis (backtest_enhanced.py, Jun 2 2026)
    # Correlation vs previous choice shown where upgraded
    'TECH':          'XLK',    # 0.511 corr — confirmed best (unchanged)
    'SEMIS':         'SOXX',   # 0.646 corr — upgraded from SMH (0.632)
    'FINTECH':       'XLF',    # 0.637 corr — confirmed best (unchanged)
    'ENERGY':        'XLE',    # 0.617 corr — confirmed best (unchanged)
    'BIOTECH':       'IBB',    # 0.366 corr — upgraded from XBI (0.334)
    'NUCLEAR':       'URA',    # 0.783 corr — upgraded from NLR (0.778)
    'DEFENCE':       'XAR',    # 0.521 corr — upgraded from ITA (0.501)
    'QUANTUM_CRYPTO':'BITQ',   # 0.703 corr — major upgrade from QQQ (0.432)
    'CONSUMER':      'XRT',    # 0.387 corr — upgraded from XLY (0.386, marginal)
    'CLEAN_ENERGY':  'QCLN',   # 0.496 corr — upgraded from ICLN (0.413)
    'COMMODITIES':   'GDX',    # 0.618 corr — upgraded from GLD (0.472)
    'OTHER':         'SPY',
}

def get_symbol_sector(symbol):
    return SECTOR_MAP.get(symbol, 'OTHER')

def get_open_sector_counts():
    """Count open trades per sector from DB."""
    counts = {}
    for t in get_open_trades():
        sec = get_symbol_sector(t['symbol'])
        counts[sec] = counts.get(sec, 0) + 1
    return counts

def update_sector_strength():
    """Fetch sector ETF % changes once per scan — identifies which sectors lead today."""
    global sector_strength
    etfs = list(set(SECTOR_ETF_MAP.values()))
    try:
        raw = yf.download(etfs, period='2d', interval='1d', progress=False, auto_adjust=True)
        closes = raw['Close'] if isinstance(raw.columns, pd.MultiIndex) else raw
        strength = {}
        for etf in etfs:
            try:
                col = closes[etf].dropna() if etf in closes.columns else pd.Series()
                if len(col) >= 2:
                    strength[etf] = round((float(col.iloc[-1]) - float(col.iloc[-2])) / float(col.iloc[-2]) * 100, 2)
            except Exception:
                pass
        sector_strength = strength
    except Exception as e:
        log(f"Sector strength error: {e}")

# ─────────────────────────────────────────────────────────
# PERSISTENCE HELPERS
# ─────────────────────────────────────────────────────────
def load_traded_today():
    try:
        if os.path.exists(TRADED_TODAY_FILE):
            with open(TRADED_TODAY_FILE) as f:
                data = json.load(f)
            if data.get('date') == date.today().isoformat():
                return set(data.get('symbols', []))
    except:
        pass
    return set()

def save_traded_today():
    try:
        with open(TRADED_TODAY_FILE, 'w') as f:
            json.dump({'date': date.today().isoformat(), 'symbols': list(traded_today)}, f)
    except:
        pass

# ─────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────
def log(msg):
    print(f"[{datetime.now(ET).strftime('%H:%M:%S')}] {msg}")

def send_telegram(msg):
    try:
        requests.post(f"{TG_API}/sendMessage",
                      json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg},
                      timeout=10)
        log(f"TG: {msg[:60]}")
    except Exception as e:
        log(f"TG error: {e}")

def send_telegram_to(chat_id, msg):
    try:
        requests.post(f"{TG_API}/sendMessage",
                      json={'chat_id': chat_id, 'text': msg},
                      timeout=10)
        log(f"TG→{chat_id}: {msg[:80]}")
    except Exception as e:
        log(f"TG error (to {chat_id}): {e}")

def speak(text):
    try:
        engine = pyttsx3.init()
        engine.setProperty('rate', 165)
        engine.say(text)
        engine.runAndWait()
    except Exception as e:
        log(f"Voice error: {e}")

# ─────────────────────────────────────────────────────────
# MARKET TIMING
# ─────────────────────────────────────────────────────────
def is_market_open():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    if now.date() in US_HOLIDAYS_2026:
        return False
    if now.hour < 9 or (now.hour == 9 and now.minute < 31):
        return False
    if now.hour >= 16:
        return False
    return True

def is_premarket_window():
    """True 9:20–9:29am ET — final pre-open window for earnings gap entries."""
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    if now.date() in US_HOLIDAYS_2026:
        return False
    return now.hour == 9 and 20 <= now.minute <= 29

def is_entry_window():
    now = datetime.now(ET)
    if not is_market_open():
        return False
    if now.hour < NO_ENTRY_BEFORE:
        return False
    if now.hour >= NO_ENTRY_AFTER:
        return False
    # Avoid lunch chop — institutions step away, price action is noise
    t = (now.hour, now.minute)
    if LUNCH_AVOID_START <= t < LUNCH_AVOID_END:
        return False
    return True

# ─────────────────────────────────────────────────────────
# IB HISTORICAL DATA — bridge first, yfinance fallback
# ─────────────────────────────────────────────────────────
def get_ib_daily(symbol, duration='60 D'):
    """Fetch daily OHLCV from IB bridge → pandas DataFrame."""
    try:
        r = requests.get(
            f"{BRIDGE}/history/{symbol}",
            params={'duration': duration, 'bar_size': '1 day'},
            timeout=15
        )
        bars = r.json()
        if not bars:
            return None
        df = pd.DataFrame(bars)
        df['date']  = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        df.columns  = [c.capitalize() for c in df.columns]  # Open/High/Low/Close/Volume
        return df
    except:
        return None

def get_ib_intraday(symbol, duration='5 D', bar_size='5 mins'):
    """Fetch intraday OHLCV from IB bridge → pandas DataFrame."""
    try:
        r = requests.get(
            f"{BRIDGE}/history/{symbol}",
            params={'duration': duration, 'bar_size': bar_size},
            timeout=15
        )
        bars = r.json()
        if not bars:
            return None
        df = pd.DataFrame(bars)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        df.columns = [c.capitalize() for c in df.columns]
        return df
    except:
        return None

# ─────────────────────────────────────────────────────────
# ATR — IB daily data, cached per symbol per day
# ─────────────────────────────────────────────────────────
def get_atr(symbol):
    today = date.today().isoformat()
    if symbol in atr_cache and atr_cache[symbol][0] == today:
        return atr_cache[symbol][1]
    try:
        # Try IB first (accurate exchange data)
        df = get_ib_daily(symbol, duration=f'{ATR_PERIOD + 5} D')
        if df is None or len(df) < ATR_PERIOD:
            df = yf.Ticker(symbol).history(period=f'{ATR_PERIOD + 5}d')
        high = df['High']
        low  = df['Low']
        prev = df['Close'].shift(1)
        tr   = pd.concat([high - low,
                          (high - prev).abs(),
                          (low  - prev).abs()], axis=1).max(axis=1)
        atr  = round(float(tr.rolling(ATR_PERIOD).mean().iloc[-1]), 4)
        atr_cache[symbol] = (today, atr)
        return atr
    except:
        return None

# ─────────────────────────────────────────────────────────
# REGIME — VWAP + VIX direction + QQQ breadth
# ─────────────────────────────────────────────────────────
def _bridge_df(symbol, duration='1 D', bar_size='5 mins'):
    """Fetch IBKR bars from bridge → DataFrame with DatetimeIndex."""
    r = requests.get(f"{BRIDGE}/history/{symbol}",
                     params={'duration': duration, 'bar_size': bar_size},
                     timeout=15)
    r.raise_for_status()
    bars = r.json()
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    df.rename(columns={c: c.capitalize() for c in df.columns}, inplace=True)
    if bar_size == '1 day':
        df['Date'] = pd.to_datetime(df['Date'])
    else:
        df['Date'] = pd.to_datetime(df['Date'], utc=True).dt.tz_convert(ET)
    return df.set_index('Date').sort_index()


def get_regime():
    global _last_regime
    try:
        # ── Primary signals — real-time IBKR data via bridge ─────────────────
        # Wrap each call: timeout/error → empty DataFrame so yfinance fallback fires
        try:
            spy_intra = _bridge_df('SPY', '1 D', '5 mins')
        except Exception:
            spy_intra = pd.DataFrame()
        try:
            qqq_intra = _bridge_df('QQQ', '1 D', '5 mins')
        except Exception:
            qqq_intra = pd.DataFrame()
        try:
            spy_daily = _bridge_df('SPY', '5 D', '1 day')
        except Exception:
            spy_daily = pd.DataFrame()

        # SPY/QQQ — yfinance fallback when bridge returns empty (after close or post-restart)
        if spy_intra.empty or len(spy_intra) < 2:
            spy_raw = yf.Ticker('SPY').history(period='1d', interval='5m')
            if not spy_raw.empty:
                spy_raw.index = spy_raw.index.tz_convert(ET)
                spy_intra = spy_raw
        if qqq_intra.empty or len(qqq_intra) < 2:
            qqq_raw = yf.Ticker('QQQ').history(period='1d', interval='5m')
            if not qqq_raw.empty:
                qqq_raw.index = qqq_raw.index.tz_convert(ET)
                qqq_intra = qqq_raw
        if spy_daily.empty:
            spy_d_raw = yf.Ticker('SPY').history(period='5d', interval='1d')
            if not spy_d_raw.empty:
                spy_d_raw.index = spy_d_raw.index.tz_localize(ET) if spy_d_raw.index.tzinfo is None else spy_d_raw.index.tz_convert(ET)
                spy_daily = spy_d_raw

        # VIX — bridge (Index contract) with yfinance fallback if no CBOE subscription
        try:
            vix_intra = _bridge_df('VIX', '1 D', '5 mins')
            if vix_intra.empty:
                raise ValueError('empty')
        except Exception:
            vix_raw = yf.Ticker('^VIX').history(period='1d', interval='5m')
            vix_raw.index = vix_raw.index.tz_convert(ET)
            vix_intra = vix_raw

        if spy_intra.empty or 'Close' not in spy_intra.columns or len(spy_intra) < 2:
            raise ValueError("SPY intraday bars empty or incomplete — market may not have opened yet")
        spy_price = float(spy_intra['Close'].iloc[-1])

        # Prev close: exclude today's bar (present after close in both IBKR and yfinance daily)
        today_d = datetime.now(ET).date()
        spy_daily_prev = spy_daily[spy_daily.index.date < today_d] if not spy_daily.empty else spy_daily
        spy_prev = (float(spy_daily_prev['Close'].iloc[-1]) if not spy_daily_prev.empty
                    else float(spy_intra['Open'].iloc[0]))
        spy_chg  = (spy_price - spy_prev) / spy_prev * 100
        vix_val  = float(vix_intra['Close'].iloc[-1])

        # SPY VWAP
        tp   = (spy_intra['High'] + spy_intra['Low'] + spy_intra['Close']) / 3
        vwap = float((tp * spy_intra['Volume']).cumsum().iloc[-1] /
                     spy_intra['Volume'].cumsum().iloc[-1])
        spy_above_vwap = spy_price > vwap

        # VIX direction (last 30 min = 6 bars)
        vix_rising = (len(vix_intra) >= 6 and
                      float(vix_intra['Close'].iloc[-1]) > float(vix_intra['Close'].iloc[-6]))

        # QQQ vs SPY — tech leading or lagging
        qqq_leading = True
        if not qqq_intra.empty and len(qqq_intra) >= 2:
            qqq_chg       = (float(qqq_intra['Close'].iloc[-1]) - float(qqq_intra['Open'].iloc[0])) / float(qqq_intra['Open'].iloc[0]) * 100
            spy_intra_chg = (spy_price - float(spy_intra['Open'].iloc[0])) / float(spy_intra['Open'].iloc[0]) * 100
            qqq_leading   = qqq_chg >= spy_intra_chg - 0.3

        # Choppiness — >40% bar reversals on a flat tape
        chop = False
        if len(spy_intra) >= 6:
            diffs   = spy_intra['Close'].diff().dropna()
            changes = sum(1 for i in range(1, len(diffs)) if diffs.iloc[i] * diffs.iloc[i-1] < 0)
            chop    = changes / len(diffs) > 0.4 and abs(spy_chg) < 0.3

        # Base regime
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

        # Downgrade one level per negative signal
        order = ['STRONG', 'NORMAL', 'CAUTIOUS', 'WEAK']
        if regime not in ('CHOPPY', 'WEAK'):
            if not spy_above_vwap:
                regime = order[min(order.index(regime) + 1, len(order) - 1)]
            if vix_rising:
                regime = order[min(order.index(regime) + 1, len(order) - 1)]
            if not qqq_leading and regime == 'STRONG':
                regime = 'NORMAL'

        # ── ES/NQ futures — informational only, yfinance ok (not a regime gate) ──
        es_chg = nq_chg = 0.0
        try:
            today_d = datetime.now(ET).date()
            es_raw  = yf.Ticker('ES=F').history(period='2d', interval='5m')
            nq_raw  = yf.Ticker('NQ=F').history(period='2d', interval='5m')
            if not es_raw.empty:
                es_raw.index = es_raw.index.tz_convert(ET)
                es_prev = es_raw[es_raw.index.date < today_d]['Close']
                if not es_prev.empty:
                    es_chg = round((float(es_raw['Close'].iloc[-1]) - float(es_prev.iloc[-1])) / float(es_prev.iloc[-1]) * 100, 2)
            if not nq_raw.empty:
                nq_raw.index = nq_raw.index.tz_convert(ET)
                nq_prev = nq_raw[nq_raw.index.date < today_d]['Close']
                if not nq_prev.empty:
                    nq_chg = round((float(nq_raw['Close'].iloc[-1]) - float(nq_prev.iloc[-1])) / float(nq_prev.iloc[-1]) * 100, 2)
        except Exception:
            pass

        # ── Market breadth — IWM + MDY via bridge ─────────────────────────────
        broad_advance = True
        breadth_weak  = False
        try:
            iwm_5m    = _bridge_df('IWM', '1 D', '5 mins')
            mdy_5m    = _bridge_df('MDY', '1 D', '5 mins')
            iwm_daily = _bridge_df('IWM', '3 D', '1 day')
            mdy_daily = _bridge_df('MDY', '3 D', '1 day')
            iwm_now   = float(iwm_5m['Close'].iloc[-1])
            mdy_now   = float(mdy_5m['Close'].iloc[-1])
            iwm_prev  = float(iwm_daily['Close'].iloc[-1])
            mdy_prev  = float(mdy_daily['Close'].iloc[-1])
            iwm_chg   = (iwm_now - iwm_prev) / iwm_prev * 100
            mdy_chg   = (mdy_now - mdy_prev) / mdy_prev * 100
            broad_advance = iwm_chg > 0 and mdy_chg > 0
            breadth_weak  = iwm_chg < -0.5 and mdy_chg < -0.5
            if breadth_weak and regime not in ('CHOPPY', 'WEAK'):
                regime = order[min(order.index(regime) + 1, len(order) - 1)]
        except Exception:
            pass

        extra = {
            'vwap': round(vwap, 2), 'spy_above_vwap': spy_above_vwap,
            'vix_rising': vix_rising, 'qqq_leading': qqq_leading,
            'es_chg': es_chg, 'nq_chg': nq_chg,
            'broad_advance': broad_advance, 'breadth_weak': breadth_weak,
        }
        result = regime, round(spy_chg, 2), round(vix_val, 2), extra
        _last_regime = result   # save last valid reading
        return result

    except Exception as e:
        log(f"Regime error: {e}")
        if _last_regime is not None:
            log(f"Regime error: holding last valid regime ({_last_regime[0]}) — SPY bars unavailable")
            return _last_regime
        return 'NORMAL', 0, 18, {}

# ─────────────────────────────────────────────────────────
# IBKR RECONCILIATION — sync DB with real positions
# ─────────────────────────────────────────────────────────
def get_ibkr_positions():
    try:
        r = requests.get(f"{BRIDGE}/portfolio", timeout=10)
        return {p['symbol']: p for p in r.json()}
    except:
        return {}

def reconcile_with_ibkr():
    ibkr = get_ibkr_positions()
    if not ibkr:
        return  # bridge unreachable — skip, don't corrupt DB

    db_trades  = get_open_trades()
    db_symbols = {t['symbol']: t for t in db_trades}

    # IBKR has position, DB doesn't → orphaned: close it immediately.
    # We do NOT adopt orphans as strategy positions — they were never scored/sized
    # by the system, and adopting them caused the May 26 2026 mass-entry incident.
    for sym, pos in ibkr.items():
        qty = int(pos['qty'])
        if sym not in db_symbols and qty != 0:
            # Long orphan → SELL, Short orphan → BUY
            close_side = 'SELL' if qty > 0 else 'BUY'
            close_qty  = abs(qty)
            log(f"Reconcile: {sym} in IBKR (qty={qty}) but missing from DB → closing orphan immediately")
            send_telegram(f"⚠️ Reconcile: {sym} orphan position ({qty} shares) found — closing now")
            closed = False

            # Attempt 1: market order (fast, works when data subscription exists)
            try:
                r_close = requests.post(
                    f"{BRIDGE}/order",
                    json={'symbol': sym, 'qty': close_qty,
                          'side': close_side, 'order_type': 'MARKET'},
                    timeout=10,
                )
                status = r_close.json().get('status', '')
                log(f"Reconcile: {sym} market close → {status}")
                if status not in ('Cancelled', 'Inactive', ''):
                    closed = True
            except Exception as _re:
                log(f"Reconcile: {sym} market order failed ({_re})")

            # Attempt 2: limit order with yfinance price (bypasses data subscription gate)
            if not closed:
                price = get_live_price(sym)
                if price:
                    # Limit slightly aggressive to ensure fill
                    lmt = round(price * 1.005 if close_side == 'BUY' else price * 0.995, 2)
                    try:
                        r_lmt = requests.post(
                            f"{BRIDGE}/order",
                            json={'symbol': sym, 'qty': close_qty,
                                  'side': close_side, 'order_type': 'LIMIT',
                                  'limit_price': lmt},
                            timeout=10,
                        )
                        status = r_lmt.json().get('status', '')
                        log(f"Reconcile: {sym} limit close @ {lmt} → {status}")
                        if status not in ('Cancelled', 'Inactive', ''):
                            closed = True
                    except Exception as _re2:
                        log(f"Reconcile: {sym} limit order failed ({_re2})")

            if not closed:
                send_telegram(f"🚨 Reconcile: {sym} orphan could not be closed — MANUAL ACTION REQUIRED")
                log(f"Reconcile: {sym} all close attempts failed — manual close required")

    # DB has open trade, IBKR doesn't → closed externally (manual close, partial fill, etc.)
    for sym, trade in db_symbols.items():
        if sym not in ibkr:
            price = get_live_price(sym) or trade['entry_price']
            log(f"Reconcile: {sym} in DB but gone from IBKR → marking closed")
            send_telegram(f"⚠️ Reconcile: {sym} closed outside auto_trader (manual? partial fill?) — marking closed at ${price:.2f}")
            log_trade_exit(trade['id'], price, 'Position closed outside auto_trader')
            if sym in open_positions:
                del open_positions[sym]

# ─────────────────────────────────────────────────────────
# LIVE PRICE
# ─────────────────────────────────────────────────────────
def get_live_price(symbol):
    try:
        r = requests.get(f"{BRIDGE}/quote/{symbol}", timeout=5)
        d = r.json()
        # Only trust bridge price if order book is live (bid or ask present).
        # A non-None 'last' with no bid/ask means IBKR returned a stale cached
        # trade print — common for scanner-discovered small-caps without an active
        # data subscription. Fall through to yfinance in that case.
        if d.get('best_price') and (d.get('bid') or d.get('ask')):
            return d['best_price']
    except:
        pass
    try:
        df = yf.Ticker(symbol).history(period='1d', interval='1m')
        if not df.empty:
            return round(float(df['Close'].iloc[-1]), 2)
    except:
        pass
    return None

def check_first_bar_quality(df5_today, day_open, avg_vol):
    """True if first 30-min bar is strong: up >1% from open AND volume >1.3× expected pace."""
    if not FIRST_BAR_QUALITY or avg_vol is None or avg_vol <= 0:
        return False
    try:
        first_30 = df5_today.between_time('09:30', '09:59')
        if len(first_30) < 3:
            return False
        close_30 = float(first_30['Close'].iloc[-1])
        vol_30   = float(first_30['Volume'].sum())
        move_pct = (close_30 - day_open) / day_open * 100
        expected = avg_vol * (30 / 390)
        vol_r    = vol_30 / expected if expected > 0 else 1.0
        return close_30 > day_open and move_pct > 1.0 and vol_r > 1.3
    except Exception:
        return False

# ─────────────────────────────────────────────────────────
# INTRADAY SIGNALS
# ─────────────────────────────────────────────────────────
def get_intraday_signals(symbol, spy_chg=0):
    try:
        # 5-min intraday: yfinance (IB rate limits prevent scanning 60+ stocks every 5 min)
        df5 = yf.Ticker(symbol).history(period='5d', interval='5m')

        # Daily bars: IB first (accurate, cached 24h) → yfinance fallback
        df1d = get_ib_daily(symbol, duration='60 D')
        if df1d is None or len(df1d) < 20:
            df1d = yf.Ticker(symbol).history(period='60d', interval='1d')

        if df5.empty or df1d.empty or len(df1d) < 20:
            return None

        # Isolate today's 5-min bars — all intraday metrics (VWAP, HOD, open_p) must
        # reset each session; using 5-day cumulative produces wrong values
        _df5_tz = df5.copy()
        _df5_tz.index = (_df5_tz.index.tz_convert(ET) if _df5_tz.index.tz
                         else _df5_tz.index.tz_localize('UTC').tz_convert(ET))
        _today = _df5_tz[_df5_tz.index.date == datetime.now(ET).date()]

        price     = float(df5['Close'].iloc[-1])
        open_p    = float(_today['Open'].iloc[0]) if not _today.empty else float(df5['Open'].iloc[-50])
        intra_chg = (price - open_p) / open_p * 100

        avg_vol   = df1d['Volume'].rolling(20).mean().iloc[-2]
        now       = datetime.now(ET)
        mins_open = max(1, (now.hour - 9) * 60 + now.minute - 30)
        # RTH-only volume — pre-market bars inflate ratio by 5-10x on catalyst days
        _rth_today = _today.between_time('09:30', '16:00') if not _today.empty else _today
        _today_vol = float(_rth_today['Volume'].sum()) if not _rth_today.empty else float(df5['Volume'].iloc[-1])
        vol_ratio  = (_today_vol * (390 / mins_open)) / avg_vol if avg_vol > 0 else 1

        close    = df1d['Close']
        ma20     = float(close.rolling(20).mean().iloc[-1])
        ema8     = float(close.ewm(span=8).mean().iloc[-1])
        ema21    = float(close.ewm(span=21).mean().iloc[-1])
        above_ma = price > ma20
        uptrend  = price > ema8 > ema21
        ema_touch = abs(price - ema21) / price * 100 < 2.5

        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rsi   = round(float(100 - (100 / (1 + gain.iloc[-1] / loss.iloc[-1]))), 1) if loss.iloc[-1] != 0 else 100.0

        # 5-min RSI — intraday exhaustion check (resets each session, much more sensitive)
        d5    = df5['Close'].diff()
        g5    = d5.clip(lower=0).rolling(14).mean()
        l5    = (-d5.clip(upper=0)).rolling(14).mean()
        rsi_5m = round(float(100 - (100 / (1 + g5.iloc[-1] / l5.iloc[-1]))), 1) if l5.iloc[-1] != 0 else 50.0

        fvg_count = 0
        h = df5['High'].values
        l = df5['Low'].values
        c = df5['Close'].values
        for i in range(1, len(df5) - 1):
            if h[i-1] < l[i+1]:
                if (l[i+1] - h[i-1]) / c[i] * 100 >= 0.15:
                    fvg_count += 1

        r_high    = float(df1d['High'].iloc[-3:].max())
        r_low     = float(df1d['Low'].iloc[-3:].min())
        range_pct = (r_high - r_low) / r_low * 100
        is_tight  = range_pct < 5
        prev_chg  = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100

        # ── VWAP (intraday, resets each session) ─────────────
        # Compute on today's bars only — 5-day cumsum produces a meaningless multi-day VWAP
        _vwap_src = _today.copy() if not _today.empty else df5.iloc[-78:].copy()
        _vwap_src['typical'] = (_vwap_src['High'] + _vwap_src['Low'] + _vwap_src['Close']) / 3
        _vwap_src['vwap']    = ((_vwap_src['typical'] * _vwap_src['Volume']).cumsum()
                                / _vwap_src['Volume'].cumsum())
        # Keep df5 vwap column populated for downstream references
        df5['typical'] = (df5['High'] + df5['Low'] + df5['Close']) / 3
        df5['vwap']    = (_vwap_src['vwap'].reindex(df5.index, method='ffill')
                          if not _today.empty else
                          (df5['typical'] * df5['Volume']).cumsum() / df5['Volume'].cumsum())
        vwap           = round(float(_vwap_src['vwap'].iloc[-1]), 2)
        above_vwap     = price > vwap
        # VWAP reclaim: last bar crossed above VWAP from below
        vwap_reclaim   = (len(_vwap_src) >= 2 and
                          float(_vwap_src['Close'].iloc[-1]) > float(_vwap_src['vwap'].iloc[-1]) and
                          float(_vwap_src['Close'].iloc[-2]) <= float(_vwap_src['vwap'].iloc[-2]))

        # ── Bull flag: surge → tight consolidation → breakout ─
        is_bull_flag = False
        if len(df5) >= 15:
            pole  = df5.iloc[-14:-5]   # flagpole bars
            base  = df5.iloc[-5:]      # consolidation bars
            pole_move    = (float(pole['High'].max()) - float(pole['Open'].iloc[0])) \
                           / max(float(pole['Open'].iloc[0]), 0.01) * 100
            base_high    = float(base['High'].max())
            base_low     = float(base['Low'].min())
            base_range   = (base_high - base_low) / max(float(base['Close'].mean()), 0.01) * 100
            # Pole ≥2%, base tight <2%, price near/breaking base high
            is_bull_flag = (pole_move >= 2.0 and base_range < 2.0
                            and price >= base_high * 0.998)

        # ── High of day break ─────────────────────────────────
        # Use today's bars only — 5-day max treats Monday's spike as today's HOD
        _hod_src  = _today if not _today.empty else df5
        hod        = float(_hod_src['High'].max())
        prior_hod  = float(_hod_src['High'].iloc[:-2].max()) if len(_hod_src) > 2 else hod
        hod_break  = price >= prior_hod * 0.999 and price >= hod * 0.995

        # ── Opening Range Breakout (ORB) ──────────────────────
        # Opening range = first 15 minutes (9:30–9:44am ET)
        # After 10am we check if price has broken above that range
        orb_high = orb_low = None
        orb_break = False
        today_open_price = None
        today_lod        = None
        try:
            today_bars = _today  # already filtered to today's ET bars
            # Use first 15-min window: 9:30–9:44
            orb_window = today_bars[
                (today_bars.index.hour == 9) &
                (today_bars.index.minute >= 30) &
                (today_bars.index.minute < 45)
            ]
            if len(orb_window) >= 2:
                orb_high  = round(float(orb_window['High'].max()), 2)
                orb_low   = round(float(orb_window['Low'].min()), 2)
                # ORB signal only valid before 11:30am — after that it's just resistance
                now_t     = (datetime.now(ET).hour, datetime.now(ET).minute)
                orb_break = (price > orb_high and price >= orb_high * 0.998
                             and now_t < ORB_ENTRY_CUTOFF)
                # Store in global key_levels
                if symbol not in key_levels:
                    key_levels[symbol] = {}
                key_levels[symbol].update({'orb_high': orb_high, 'orb_low': orb_low})
            # Today's opening print + intraday low for gap-and-crap detection
            if len(today_bars) > 0:
                today_open_price = round(float(today_bars['Open'].iloc[0]), 2)
                today_lod        = round(float(today_bars['Low'].min()), 2)
        except Exception:
            pass

        # ── Relative strength vs SPY ──────────────────────────
        rs_vs_spy  = round(prev_chg - spy_chg, 2)   # positive = beating SPY today

        # ── Last 5m candle quality ────────────────────────────
        last_o = float(df5['Open'].iloc[-1])
        last_c = float(df5['Close'].iloc[-1])
        last_h = float(df5['High'].iloc[-1])
        last_l = float(df5['Low'].iloc[-1])
        candle_range  = last_h - last_l
        candle_body   = abs(last_c - last_o)
        body_ratio    = candle_body / candle_range if candle_range > 0 else 0
        is_bullish_candle = last_c > last_o and body_ratio >= 0.6
        is_doji           = body_ratio < 0.2
        is_hammer         = (last_c > last_o and candle_range > 0 and
                             (last_o - last_l) > candle_body * 2 and
                             (last_h - last_c) < candle_body * 0.5)

        # ── Low of day break (bear) ───────────────────────────
        lod           = float(_hod_src['Low'].min())
        prior_lod     = float(_hod_src['Low'].iloc[:-2].min()) if len(_hod_src) > 2 else lod
        lod_break     = price <= prior_lod * 1.001 and price <= lod * 1.005

        # ── ORB break downward (bear) ─────────────────────────
        orb_break_down = False
        if orb_low:
            now_t2 = (datetime.now(ET).hour, datetime.now(ET).minute)
            orb_break_down = (price < orb_low and price >= orb_low * 0.998
                              and now_t2 < ORB_ENTRY_CUTOFF)

        # ── VWAP rejection (bear): rallied to VWAP then failed ─
        vwap_rejection = (len(df5) >= 2 and
                          float(df5['Close'].iloc[-1]) < float(df5['vwap'].iloc[-1]) and
                          float(df5['Close'].iloc[-2]) >= float(df5['vwap'].iloc[-2]))

        # ── Bear flag: pole down → tight consolidation → breakdown
        is_bear_flag = False
        if len(df5) >= 15:
            bpole     = df5.iloc[-14:-5]
            bbase     = df5.iloc[-5:]
            pole_drop = (float(bpole['Open'].iloc[0]) - float(bpole['Low'].min())) \
                        / max(float(bpole['Open'].iloc[0]), 0.01) * 100
            bbase_hi  = float(bbase['High'].max())
            bbase_lo  = float(bbase['Low'].min())
            bbase_rng = (bbase_hi - bbase_lo) / max(float(bbase['Close'].mean()), 0.01) * 100
            is_bear_flag = (pole_drop >= 2.0 and bbase_rng < 2.0
                            and price <= bbase_lo * 1.002)

        # ── Bearish candle ────────────────────────────────────
        is_bearish_candle = last_c < last_o and body_ratio >= 0.6

        # ── 15-min timeframe alignment ────────────────────────
        # All three must agree before entering on 5m signal
        aligned_15m      = True   # default True — don't penalise if data unavailable
        aligned_15m_bear = True
        try:
            df15 = yf.Ticker(symbol).history(period='5d', interval='15m')
            if not df15.empty:
                df15.index = df15.index.tz_convert(ET)
                df15_today = df15[df15.index.date == datetime.now(ET).date()].copy()
                if len(df15_today) >= 3:
                    df15_today['tp']   = (df15_today['High'] + df15_today['Low'] + df15_today['Close']) / 3
                    df15_today['vwap'] = (df15_today['tp'] * df15_today['Volume']).cumsum() / df15_today['Volume'].cumsum()
                    p15    = float(df15_today['Close'].iloc[-1])
                    v15    = float(df15_today['vwap'].iloc[-1])
                    e20_15 = float(df15_today['Close'].ewm(span=20).mean().iloc[-1])
                    aligned_15m      = p15 > v15 and p15 > e20_15
                    aligned_15m_bear = p15 < v15 and p15 < e20_15
        except Exception:
            pass

        # First-bar quality: check live via df5 data already fetched above
        df5_tz = _df5_tz  # reuse already-converted tz-aware df
        df5_today    = df5_tz[df5_tz.index.date == datetime.now(ET).date()]
        day_open_fbq = float(df5_today['Open'].iloc[0]) if len(df5_today) > 0 else 0
        first_bar_strong = check_first_bar_quality(df5_today, day_open_fbq, float(avg_vol)) if day_open_fbq > 0 else False

        # ── Burst timing signals (validated May 2026, 136 trades) ─────────────
        # burst_age_min:    minutes since first high-volume ignition bar (999=none found)
        # consec_new_highs: consecutive bars making new HODs in last 5 bars (long momentum)
        # consec_new_lows:  consecutive bars making new LODs in last 5 bars (short momentum)
        burst_age_min    = 999
        consec_new_highs = 0
        consec_new_lows  = 0
        if not _rth_today.empty and len(_rth_today) >= 5:
            try:
                _avg_bar_vol = float(_rth_today['Volume'].mean())
                _ignition_thresh = _avg_bar_vol * 2.0
                _now_et = datetime.now(ET)
                for _bt, _bar in _rth_today.iterrows():
                    if float(_bar['Volume']) > _ignition_thresh:
                        burst_age_min = round((_now_et - _bt).total_seconds() / 60, 1)
                        break
                # Consecutive new highs (long): count from end of last 5 bars
                # Baseline = HOD before the last 5 bars so we measure *fresh* highs
                _last5 = _rth_today.tail(5)
                _prior = _rth_today.iloc[:-5]
                _rmax  = float(_prior['High'].max()) if not _prior.empty else 0.0
                for _, _b in _last5.iterrows():
                    if float(_b['High']) > _rmax:
                        _rmax = float(_b['High']); consec_new_highs += 1
                    else:
                        consec_new_highs = 0
                # Consecutive new lows (short): mirror — baseline from prior bars
                # (same pattern as consec_new_highs — prevents first bar always counting)
                _rmin = float(_prior['Low'].min()) if not _prior.empty else float('inf')
                for _, _b in _last5.iterrows():
                    if float(_b['Low']) < _rmin:
                        _rmin = float(_b['Low']); consec_new_lows += 1
                    else:
                        consec_new_lows = 0
            except Exception:
                pass  # burst signals are informational — never block on failure

        return {
            'price': round(price, 2), 'intra_chg': round(intra_chg, 2),
            'prev_chg': round(prev_chg, 2), 'vol_ratio': round(vol_ratio, 2),
            'above_ma': above_ma, 'uptrend': uptrend, 'ema_touch': ema_touch,
            'rsi': rsi, 'rsi_5m': rsi_5m, 'fvg_count': fvg_count,
            'is_tight': is_tight, 'range_pct': round(range_pct, 2),
            'vwap': vwap, 'above_vwap': above_vwap, 'vwap_reclaim': vwap_reclaim,
            'is_bull_flag': is_bull_flag, 'hod_break': hod_break,
            'orb_break': orb_break, 'orb_high': orb_high, 'orb_low': orb_low,
            'lod_break': lod_break, 'orb_break_down': orb_break_down,
            'vwap_rejection': vwap_rejection, 'is_bear_flag': is_bear_flag,
            'rs_vs_spy': rs_vs_spy,
            'is_bullish_candle': is_bullish_candle, 'is_hammer': is_hammer,
            'is_bearish_candle': is_bearish_candle,
            'is_doji': is_doji, 'aligned_15m': aligned_15m,
            'aligned_15m_bear': aligned_15m_bear,
            'today_open': today_open_price, 'today_lod': today_lod,
            'today_hod':  round(hod, 2),
            'first_bar_strong': first_bar_strong,
            'burst_age_min':    burst_age_min,
            'consec_new_highs': consec_new_highs,
            'consec_new_lows':  consec_new_lows,
        }
    except:
        return None

# ─────────────────────────────────────────────────────────
# STOP / TARGET — ATR-based, adapts to each stock's volatility
# ─────────────────────────────────────────────────────────
def calc_sl_target(symbol, price, side='LONG'):
    risk_pct = 5.0
    reward   = risk_pct * MIN_RR   # 12.5% display target — no profit cap, strategy rides trail
    if side == 'LONG':
        sl     = round(price * 0.95, 2)
        target = round(price * (1 + reward / 100), 2)
    else:
        sl     = round(price * 1.05, 2)
        target = round(price * (1 - reward / 100), 2)
    return sl, target, risk_pct, round(reward, 2), MIN_RR

# ─────────────────────────────────────────────────────────
# EARNINGS HELPER
# ─────────────────────────────────────────────────────────
def get_days_to_earnings(symbol):
    today_str = date.today().isoformat()
    if symbol in earnings_cache and earnings_cache[symbol][0] == today_str:
        return earnings_cache[symbol][1]
    days = None
    try:
        cal = yf.Ticker(symbol).calendar
        if isinstance(cal, pd.DataFrame) and not cal.empty:
            for col in cal.columns:
                try:
                    ed = pd.Timestamp(col).date()
                    d  = (ed - date.today()).days
                    if d >= -1:
                        days = d
                        break
                except Exception:
                    continue
        elif isinstance(cal, dict):
            for key in ('Earnings Date', 'earningsDate'):
                val = cal.get(key)
                if val:
                    try:
                        ed = pd.Timestamp(val[0] if isinstance(val, list) else val).date()
                        d  = (ed - date.today()).days
                        if d >= -1:
                            days = d
                    except Exception:
                        pass
                    break
    except Exception:
        pass
    earnings_cache[symbol] = (today_str, days)
    return days

# ─────────────────────────────────────────────────────────
# GRADE SETUP
# ─────────────────────────────────────────────────────────
def grade_setup(sig, regime, sl, target, price, rr, symbol=None, is_catalyst=False):
    score   = 0
    reasons = []
    w = get_strategy_weights()  # learner-adjusted multipliers (default 1.0)

    # Earnings gate — hard skip within 3 days: IV crush, gap risk, binary event
    # None means data unavailable — skip conservatively (AMD bypass incident May 5 2026)
    # ETFs have no earnings calendar — skip this gate entirely for them
    if symbol and symbol not in ETF_SYMBOLS:
        dte = get_days_to_earnings(symbol)
        if dte is None:
            # Fix 2: unknown date → allow if stock already running hard (post-earnings move resolved).
            # Conservative skip hurt COHR +13%, AEHR +11% (Jun 2 2026). Stock up >5% on 3x+ vol
            # with unknown earnings = calendar gap, not upcoming binary event.
            intra = sig.get('intra_chg', 0) or 0
            vol   = sig.get('vol_ratio', 1) or 1
            if intra < 5.0 or vol < 3.0:
                return 'SKIP', ['Earnings date unknown — skip (low conviction, unknown risk)'], 0
            # else: running hard → likely post-earnings, allow with note
        elif 0 <= dte <= 3:
            return 'SKIP', [f'Earnings in {dte}d — skip binary event'], 0

    if not sig['above_ma']:
        return 'SKIP', ['Below MA'], 0
    # 5m RSI — scoring only, not a hard gate (same principle as daily RSI)
    # High 5m RSI = late to the party but can still run; penalise rather than block
    # Large-caps (>$100) are liquid even at 1x volume — lower threshold
    vol_threshold = 1.0 if sig.get('price', 0) > 100 else MIN_VOLUME_RATIO
    if sig['vol_ratio'] < vol_threshold:
        return 'SKIP', [f'Volume {sig["vol_ratio"]:.1f}x too low'], 0
    if rr < MIN_RR:
        return 'SKIP', [f'R:R 1:{rr} below min 1:{MIN_RR}'], 0
    if regime in ('CHOPPY', 'CAUTIOUS'):
        if is_catalyst:
            pass  # Fix 1: catalyst stocks bypass CAUTIOUS/CHOPPY — market-independent move
        else:
            return 'SKIP', [f'{regime} — no trades'], 0
    # Must be moving today — no entering flat or declining stocks
    today_gain = sig.get('prev_chg', 0)
    if today_gain < MIN_TODAY_GAIN:
        # Sympathy stocks use 2% gap threshold (confirmed at detect time) — bypass 3% gate
        if symbol and symbol in active_sympathy_triggers and today_gain >= SYMPATHY_GAP_THRESH * 100:
            pass
        else:
            return 'SKIP', [f'Only +{today_gain:.1f}% today (need ≥{MIN_TODAY_GAIN}%)'], 0

    # ── Gap-and-crap filter (day-1 prop rule) ─────────────────
    # If price is 5%+ below today's opening print, the gap has been distributed.
    # Dead-cat bounces inside this pattern look like VWAP reclaims — they fail.
    today_open = sig.get('today_open')
    if today_open and price < today_open * 0.95:
        pct_below = (today_open - price) / today_open * 100
        return 'SKIP', [f'Gap-and-crap: -{pct_below:.1f}% below today open (${today_open})'], 0

    # ── Failed ORB on gap day ──────────────────────────────────
    # Stock gapped up >5%, ORB low was breached intraday, still below open = distribution
    today_lod = sig.get('today_lod')
    orb_low   = sig.get('orb_low')
    if (today_open and orb_low and today_lod
            and today_gain > 5.0
            and today_lod < orb_low
            and price < today_open):
        return 'SKIP', [f'Failed gap: ORB low ${orb_low} violated, below open ${today_open}'], 0

    if sig['fvg_count'] >= 10:
        score += 30; reasons.append(f'{sig["fvg_count"]} FVGs')
    elif sig['fvg_count'] >= 5:
        score += 20; reasons.append(f'{sig["fvg_count"]} FVGs')
    elif sig['fvg_count'] >= 1:
        score += 10; reasons.append(f'{sig["fvg_count"]} FVGs')

    if sig['vol_ratio'] >= 2.0:
        score += round(25 * w['volume']); reasons.append(f'{sig["vol_ratio"]:.1f}x vol')
    elif sig['vol_ratio'] >= 1.5:
        score += round(15 * w['volume']); reasons.append(f'{sig["vol_ratio"]:.1f}x vol')
    else:
        score += round(5  * w['volume']); reasons.append(f'{sig["vol_ratio"]:.1f}x vol')

    if sig['uptrend'] and sig['ema_touch']:
        score += 20; reasons.append('EMA pullback in uptrend')
    elif sig['uptrend']:
        score += 10; reasons.append('Uptrend')

    # Daily RSI — scoring only, no longer a hard gate (high RSI = momentum, not overbought)
    # RSI 65-75 bucket has 42.5% WR historically — no bonus, no penalty
    if 45 <= sig['rsi'] <= 65:
        score += round(20 * w['rsi']); reasons.append(f'RSI {sig["rsi"]} ideal')
    elif 65 < sig['rsi'] <= 75:
        reasons.append(f'RSI {sig["rsi"]} elevated (neutral)')  # 42.5% WR — skip bonus
    elif 75 < sig['rsi'] <= 80:
        score += round(5  * w['rsi']); reasons.append(f'RSI {sig["rsi"]} strong momentum')
    else:
        score += round(5  * w['rsi']); reasons.append(f'RSI {sig["rsi"]} (trending)')

    # 5m RSI — scoring only, penalise exhaustion but don't block
    rsi5m = sig.get('rsi_5m', 50)
    if rsi5m > MAX_RSI_5M:
        score -= round(20 * w['rsi']); reasons.append(f'5m RSI {rsi5m} exhausted (-20)')
    elif rsi5m > 75:
        score -= round(10 * w['rsi']); reasons.append(f'5m RSI {rsi5m} elevated (-10)')

    if sig['is_tight']:
        score += 10; reasons.append(f'Tight range {sig["range_pct"]:.1f}%')

    # Reward stocks already moving strongly today
    if today_gain >= 5.0:
        score += 30; reasons.append(f'+{today_gain:.1f}% today')
    elif today_gain >= 3.0:
        score += 20; reasons.append(f'+{today_gain:.1f}% today')
    elif today_gain >= 1.5:
        score += 10; reasons.append(f'+{today_gain:.1f}% today')

    # ── Price action gate — must have at least one pro pattern ──
    # Exception: strong momentum (up 5%+ AND beating SPY by 3%+) is itself the signal
    rs          = sig.get('rs_vs_spy', 0)
    strong_momo = today_gain >= 5.0 and rs >= 3.0
    has_pattern = (sig.get('vwap_reclaim') or sig.get('is_bull_flag')
                   or sig.get('hod_break') or sig.get('orb_break') or strong_momo)
    if not has_pattern:
        return 'SKIP', ['No pattern — wait for ORB/VWAP reclaim/bull flag/HOD break'], 0

    # ── Score the pattern ──────────────────────────────────────
    if sig.get('orb_break'):
        score += 30; reasons.append('ORB breakout ✓')   # most reliable — scored highest

    if sig.get('vwap_reclaim'):
        score += 25; reasons.append('VWAP reclaim ✓')
    elif sig.get('above_vwap'):
        score += 10; reasons.append('Above VWAP')

    if sig.get('is_bull_flag'):
        score += 25; reasons.append('Bull flag ✓')

    if sig.get('hod_break'):
        score += 20; reasons.append('HOD break ✓')

    # ── DNA cluster modifier (Layer 1) ────────────────────────
    # HIGH_VOL: gaps fill 70% intraday. ORB at gap high = likely entering before pullback.
    # Penalise naked ORB on gap-up days; reward VWAP reclaim (pullback confirmed + recovery).
    # INSTITUTIONAL: gaps hold 75% — ORB more reliable, small bonus.
    if symbol:
        dna = get_dna_cluster(symbol)
        if dna == 'HIGH_VOL':
            if sig.get('orb_break') and not sig.get('vwap_reclaim'):
                score -= 15
                reasons.append('HIGH_VOL: ORB-15 — gap fills 70%, wait for VWAP reclaim')
            if sig.get('vwap_reclaim'):
                score += 15
                reasons.append('HIGH_VOL: VWAP+15 — pullback confirmed ✓')
        elif dna == 'INSTITUTIONAL':
            if sig.get('orb_break'):
                score += 5
                reasons.append('INST: ORB+5 — gap sticks 75%')

    # ── Relative strength vs SPY ──────────────────────────────
    rs = sig.get('rs_vs_spy', 0)
    if rs >= 5:
        score += round(20 * w['momentum']); reasons.append(f'RS +{rs:.1f}% vs SPY')
    elif rs >= 2:
        score += round(10 * w['momentum']); reasons.append(f'RS +{rs:.1f}% vs SPY')
    elif rs < 0:
        score -= round(10 * w['momentum']); reasons.append(f'RS {rs:.1f}% vs SPY (lagging)')

    # ── Pre-market high — cleared = overnight resistance gone, strong confirmation ──
    if symbol and symbol in key_levels:
        pm_high = key_levels[symbol].get('pm_high')
        if pm_high:
            if price >= pm_high * 1.001:
                score += 15; reasons.append(f'Above PM high ${pm_high} ✓')
            elif price >= pm_high * 0.998:
                score += 5;  reasons.append(f'Testing PM high ${pm_high}')
            else:
                score -= 5;  reasons.append(f'Below PM high ${pm_high} (resistance)')

    # ── Sector ETF strength ───────────────────────────────────
    if symbol and sector_strength:
        sec = get_symbol_sector(symbol)
        etf = SECTOR_ETF_MAP.get(sec, 'SPY')
        etf_chg = sector_strength.get(etf, 0)
        if etf_chg >= 1.5:
            score += round(15 * w['sector']); reasons.append(f'{etf} +{etf_chg:.1f}% leading')
        elif etf_chg >= 0.5:
            score += round(5  * w['sector']); reasons.append(f'{etf} +{etf_chg:.1f}%')
        elif etf_chg <= -1.0:
            score -= round(10 * w['sector']); reasons.append(f'{etf} {etf_chg:.1f}% weak sector')

    # ── Sector historical grade (learner writes nightly, reflects 30d WR) ─
    if symbol:
        sec_name  = get_symbol_sector(symbol)
        sec_grade = get_sector_grade(sec_name)
        if sec_grade == 'STRONG':
            score += round(15 * w['sector']); reasons.append(f'{sec_name} sector STRONG +15')
        elif sec_grade == 'WEAK':
            score -= round(20 * w['sector']); reasons.append(f'{sec_name} sector WEAK -20')

    if regime == 'STRONG':
        score += 15; reasons.append('Strong market')
    elif regime == 'NORMAL':
        score += 5

    if rr >= 4:
        score += 10; reasons.append(f'R:R 1:{rr} excellent')
    elif rr >= MIN_RR:
        score += 5;  reasons.append(f'R:R 1:{rr}')

    # ── Candlestick quality ───────────────────────────────────
    if sig.get('is_hammer'):
        score += 15; reasons.append('Hammer candle — reversal confirmation ✓')
    elif sig.get('is_bullish_candle'):
        score += 10; reasons.append('Strong bullish candle ✓')
    elif sig.get('is_doji') and sig.get('above_vwap'):
        score -= 5;  reasons.append('Doji at key level — indecision (-5)')

    # ── 15-minute timeframe alignment ────────────────────────
    if sig.get('aligned_15m') is True:
        score += 10; reasons.append('15m aligned (price > 15m VWAP & EMA20) ✓')
    elif sig.get('aligned_15m') is False:
        score -= 15; reasons.append('15m counter-trend (-15)')

    # ── Opening print respect ─────────────────────────────────
    # Holding above today's open = gap is being sustained (institutional buying)
    # Below today's open = gap is under pressure (selling into retail)
    if today_open:
        if price >= today_open:
            score += 10; reasons.append('Holding above today open ✓')
        elif price < today_open * 0.98:
            score -= 10; reasons.append(f'Below today open ${today_open} (-10)')

    # Sympathy boost — sector follower on mega-cap earnings beat
    if symbol and symbol in active_sympathy_triggers:
        info = active_sympathy_triggers[symbol]
        score += SYMPATHY_SCORE_BOOST
        reasons.append(f"Sympathy: {info['trigger']} +{info['trigger_move']:.0f}% earnings ✓ (+{SYMPATHY_SCORE_BOOST})")

    # ── Burst timing (fine-tuning May 2026, 136 trades validated) ────────────
    # Data: burst 30-90m → 71.1% WR; stale >150m → 41.9% WR | 2 consec highs → 75% WR
    _burst = sig.get('burst_age_min', 999)
    _chod  = sig.get('consec_new_highs', 0)
    if _burst < 30:
        score -= 5;  reasons.append(f'Burst {_burst:.0f}m (unconfirmed <30m, -5)')
    elif _burst <= 90:
        pass  # sweet spot — no change
    elif _burst <= 150:
        score -= 10; reasons.append(f'Burst aging {_burst:.0f}m (-10)')
    elif _burst < 999:
        score -= 20; reasons.append(f'Stale burst {_burst:.0f}m (>150m, -20)')
    if _chod >= 2 and _chod <= 4:
        score += 10; reasons.append(f'{_chod} consec new highs ✓ (+10)')
    elif _chod == 1:
        score -= 5;  reasons.append('1 consec high (false breakout risk, -5)')
    elif _chod == 0:
        score -= 10; reasons.append('No consec new highs (momentum stalled, -10)')
    # _chod == 5: extended run, 46.2% WR — no bonus, no penalty

    grade = 'A+' if score >= 80 else 'A' if score >= 65 else 'B' if score >= 50 else 'C'
    return grade, reasons, score

# ─────────────────────────────────────────────────────────
# GRADE BEAR SETUP — inverted signal stack for shorts
# ─────────────────────────────────────────────────────────
def grade_bear_setup(sig, regime, sl, target, price, rr, symbol=None):
    score   = 0
    reasons = []
    w = get_strategy_weights()

    # Earnings gate — same hard skip (earnings = binary event, both directions)
    # ETFs have no earnings calendar — skip this gate entirely for them
    if symbol and symbol not in ETF_SYMBOLS:
        dte = get_days_to_earnings(symbol)
        if dte is None:
            # Bears: unknown earnings = skip (could gap up on surprise, killing short)
            return 'SKIP', ['Earnings date unknown — skipping short (gap-up risk)'], 0
        if 0 <= dte <= 3:
            return 'SKIP', [f'Earnings in {dte}d — skip binary event'], 0

    # Hard gates (inverted from bull)
    if sig['above_ma']:
        return 'SKIP', ['Above MA20 — not a short candidate'], 0
    vol_threshold = 1.0 if sig.get('price', 0) > 100 else MIN_VOLUME_RATIO
    if sig['vol_ratio'] < vol_threshold:
        return 'SKIP', [f'Volume {sig["vol_ratio"]:.1f}x too low'], 0
    if rr < MIN_RR:
        return 'SKIP', [f'R:R 1:{rr} below min 1:{MIN_RR}'], 0
    if regime != 'WEAK':
        return 'SKIP', [f'{regime} — bear engine only runs on WEAK days'], 0
    today_chg = sig.get('prev_chg', 0)
    if today_chg > -MIN_TODAY_GAIN:
        return 'SKIP', [f'Only {today_chg:.1f}% today (need ≤-{MIN_TODAY_GAIN}%)'], 0

    # ── Pattern gate — mirrors bull requirement ────────────────────────────────
    # Strong distribution (≤-5% AND underperforming SPY by 2%+) counts as the signal itself
    strong_dist = today_chg <= -5.0 and sig.get('rs_vs_spy', 0) <= -2.0
    has_bear_pattern = (sig.get('orb_break_down') or sig.get('vwap_rejection')
                        or sig.get('lod_break') or sig.get('is_bear_flag') or strong_dist)
    if not has_bear_pattern:
        return 'SKIP', ['No bear pattern — need ORB breakdown/VWAP rejection/LOD break/bear flag'], 0

    # ── Entry signal scoring ──────────────────────────────
    if sig.get('orb_break_down'):
        score += 25; reasons.append('ORB break down')
    if sig.get('vwap_rejection'):
        score += 25; reasons.append('VWAP rejection')
    elif not sig['above_vwap']:
        score += 10; reasons.append('Below VWAP')
    if sig.get('lod_break'):
        score += 20; reasons.append('LOD break')
    if sig.get('is_bear_flag'):
        score += 20; reasons.append('Bear flag')

    # ── DNA cluster modifier (Layer 1, short side) ────────────
    # Mirror of bull-side logic — inverted direction.
    # HIGH_VOL: gap-down fills back UP 70% of the time. ORB breakdown at the gap low
    # = entering before the bounce. Require VWAP rejection (bounce happened, rejected) first.
    # INSTITUTIONAL: gap-down sticks 75% — ORB breakdown more reliable, small bonus.
    if symbol:
        dna = get_dna_cluster(symbol)
        if dna == 'HIGH_VOL':
            if sig.get('orb_break_down') and not sig.get('vwap_rejection'):
                score -= 15
                reasons.append('HIGH_VOL: ORB↓-15 — gap fills 70%, wait for VWAP rejection')
            if sig.get('vwap_rejection'):
                score += 15
                reasons.append('HIGH_VOL: VWAP reject+15 — bounce failed ✓')
        elif dna == 'INSTITUTIONAL':
            if sig.get('orb_break_down'):
                score += 5
                reasons.append('INST: ORB↓+5 — gap-down sticks 75%')

    # FVG count (gaps down = air pockets to fill)
    if sig['fvg_count'] >= 10:
        score += 20; reasons.append(f'{sig["fvg_count"]} FVGs (downside)')
    elif sig['fvg_count'] >= 5:
        score += 10; reasons.append(f'{sig["fvg_count"]} FVGs')

    # Daily RSI — weak RSI supports short
    rsi = sig['rsi']
    if rsi < 30:
        score += 15; reasons.append(f'RSI {rsi} (deeply oversold momentum)')
    elif rsi < 45:
        score += 10; reasons.append(f'RSI {rsi} (weak)')
    elif rsi > 60:
        score -= 15; reasons.append(f'RSI {rsi} (too strong — avoid short)')

    # 5m RSI: if < 20, bounce risk is high
    rsi_5m = sig.get('rsi_5m', 50)
    if rsi_5m < 20:
        score -= 15; reasons.append(f'5m RSI {rsi_5m} (oversold — bounce risk)')
    elif rsi_5m < 35:
        score += 5;  reasons.append(f'5m RSI {rsi_5m} (weak intraday)')

    # Volume — selling pressure confirmation
    vol = sig['vol_ratio']
    if vol >= 5:
        score += 20; reasons.append(f'Vol {vol:.1f}x surge (distribution)')
    elif vol >= 3:
        score += 12; reasons.append(f'Vol {vol:.1f}x')
    elif vol >= 2:
        score += 6;  reasons.append(f'Vol {vol:.1f}x')

    # Today's decline magnitude
    if today_chg <= -5:
        score += 20; reasons.append(f'{today_chg:.1f}% strong distribution')
    elif today_chg <= -3:
        score += 12; reasons.append(f'{today_chg:.1f}% declining')
    elif today_chg <= -1.5:
        score += 6;  reasons.append(f'{today_chg:.1f}% declining')

    # Relative weakness vs SPY (negative = weaker than market = better short)
    rs = sig.get('rs_vs_spy', 0)
    if rs <= -2:
        score += 15; reasons.append(f'RS {rs:+.1f}% (weak vs SPY)')
    elif rs <= -1:
        score += 8;  reasons.append(f'RS {rs:+.1f}%')
    elif rs >= 2:
        score -= 10; reasons.append(f'RS {rs:+.1f}% (outperforming — avoid short)')

    # Sector weakness (ETF down = tailwind for short)
    if symbol and sector_strength:
        sec     = get_symbol_sector(symbol)
        etf     = SECTOR_ETF_MAP.get(sec, 'SPY')
        etf_chg = sector_strength.get(etf, 0)
        if etf_chg <= -1.5:
            score += 15; reasons.append(f'{etf} {etf_chg:.1f}% weak sector')
        elif etf_chg <= -0.5:
            score += 5;  reasons.append(f'{etf} {etf_chg:.1f}%')
        elif etf_chg >= 1.0:
            score -= 10; reasons.append(f'{etf} +{etf_chg:.1f}% strong sector (risk)')

    # Sector historical grade — inverted for shorts
    # WEAK sector (BIOTECH/ENERGY historically bad on longs) = short-friendly tailwind +15
    # STRONG sector (SEMIS/NUCLEAR/TECH historically dominant) = shorting against the trend -20
    if symbol:
        sec_name  = get_symbol_sector(symbol)
        sec_grade = get_sector_grade(sec_name)
        if sec_grade == 'WEAK':
            score += round(15 * w['sector']); reasons.append(f'{sec_name} sector WEAK (short-friendly) +15')
        elif sec_grade == 'STRONG':
            score -= round(20 * w['sector']); reasons.append(f'{sec_name} sector STRONG (short-risky) -20')

    # WEAK regime confirmation
    score += 15; reasons.append('WEAK regime confirmed')

    # Bearish candle
    if sig.get('is_bearish_candle'):
        score += 10; reasons.append('Bearish candle')
    elif sig.get('is_doji') and not sig.get('above_vwap'):
        score -= 5;  reasons.append('Doji — indecision')

    # 15m bear alignment (price < 15m VWAP and < EMA20)
    if sig.get('aligned_15m_bear') is True:
        score += 10; reasons.append('15m bear-aligned (below VWAP & EMA20) ✓')
    elif sig.get('aligned_15m_bear') is False:
        score -= 10; reasons.append('15m not bear-aligned')

    if rr >= 4:
        score += 10; reasons.append(f'R:R 1:{rr} excellent')
    elif rr >= MIN_RR:
        score += 5;  reasons.append(f'R:R 1:{rr}')

    # ── Burst timing for shorts (age validated: stale >150m → 18.2% WR) ──────
    _burst_s = sig.get('burst_age_min', 999)
    if _burst_s <= 90:
        pass  # fresh burst — no change
    elif _burst_s <= 150:
        score -= 10; reasons.append(f'Short burst aging {_burst_s:.0f}m (-10)')
    elif _burst_s < 999:
        score -= 20; reasons.append(f'Short stale burst {_burst_s:.0f}m (>150m, -20)')

    grade = 'A+' if score >= 80 else 'A' if score >= 65 else 'B' if score >= 50 else 'C'
    return grade, reasons, score

# ─────────────────────────────────────────────────────────
# PLACE TRADE
# ─────────────────────────────────────────────────────────
def place_trade(symbol, price, shares, sl, target, strategy, grade,
                rsi=0, vol_ratio=0, confidence=75, sector='OTHER', side='LONG',
                limit_price=None, outside_rth=False):
    try:
        # Buying power pre-check — prevents hard-reject on live account.
        # Paper has $3M+ paper BP so this never blocks in paper mode.
        # Adds a 5% buffer over position cost to account for spread/price movement.
        try:
            _acct  = requests.get(f"{BRIDGE}/account", timeout=4).json()
            _bp    = float(_acct.get('BuyingPower', 999999) or 999999)
            _cost  = price * shares * 1.05  # 5% buffer
            if _bp < _cost:
                log(f"  {symbol}: Buying power ${_bp:,.0f} < position cost ${_cost:,.0f} — skipping")
                send_telegram(f"⚠️ Buying power insufficient: {symbol} needs ${_cost:,.0f}, have ${_bp:,.0f}")
                return None
        except Exception:
            pass  # If account query fails, proceed rather than block a valid trade

        order_side = 'BUY' if side == 'LONG' else 'SELL'
        payload = {'symbol': symbol, 'qty': shares, 'side': order_side, 'order_type': 'MARKET'}
        if limit_price:
            payload['order_type']  = 'LIMIT'
            payload['limit_price'] = limit_price
        if outside_rth:
            payload['outside_rth'] = True
        r = requests.post(f"{BRIDGE}/order", json=payload, timeout=10)
        if r.status_code != 200:
            log(f"  {symbol}: Order rejected — bridge {r.status_code}: {r.text[:120]}")
            return None
        if not r.text or not r.text.strip():
            log(f"  {symbol}: Order failed — bridge returned empty response (200 but no body)")
            return None
        order_id = r.json().get('orderId')
        if not order_id:
            log(f"  {symbol}: No orderId returned — order submission failed")
            return None

        # Poll for fill confirmation: limit orders get more attempts (fills can take longer)
        poll_attempts = 8 if limit_price else 3
        filled = False
        for _ in range(poll_attempts):
            time.sleep(2)
            try:
                r2 = requests.get(f"{BRIDGE}/order/{order_id}/status", timeout=5)
                d  = r2.json()
                if d.get('status') == 'Filled':
                    # Use actual fill qty and price — live orders can partially fill.
                    # Paper always fills 100% so these will match what was requested.
                    # Explicit None check: 0 is a valid (bad) fill qty, not a missing value.
                    _fqty  = d.get('filled')
                    _fpx   = d.get('avgFillPrice')
                    filled_qty   = int(_fqty)   if _fqty  is not None else shares
                    filled_price = float(_fpx)  if (_fpx  is not None and float(_fpx) > 0) else price
                    # Zero-qty fill = IBKR reporting a bad state — skip before partial check
                    if filled_qty == 0:
                        log(f"  {symbol}: Zero-qty fill reported — skipping")
                        return None
                    if filled_qty < shares:
                        log(f"  {symbol}: Partial fill {filled_qty}/{shares} sh @ ${filled_price:.2f} — recording actual qty")
                        send_telegram(f"⚠️ {symbol}: Partial fill {filled_qty}/{shares} shares @ ${filled_price:.2f}")
                        shares = filled_qty
                    elif filled_qty > shares:
                        log(f"  {symbol}: Overfill {filled_qty}/{shares} sh — capping at requested qty")
                        filled_qty = shares
                    price = filled_price
                    filled = True
                    break
                if d.get('status') in ('Cancelled', 'Inactive'):
                    # IBKR paper sometimes fills then reports Cancelled during a gateway
                    # reconnect. Before giving up, confirm no position exists.
                    # Must check sign: LONG fill = positive qty, SHORT fill = negative qty.
                    ibkr_chk = get_ibkr_positions()
                    ibkr_qty = ibkr_chk.get(symbol, {}).get('qty', 0) or 0
                    position_match = (side == 'LONG' and ibkr_qty > 0) or (side == 'SHORT' and ibkr_qty < 0)
                    if position_match:
                        log(f"  {symbol}: Order {order_id} {d['status']} but {side} position confirmed in IBKR — recording fill")
                        price  = ibkr_chk[symbol].get('avgCost', price)
                        shares = abs(int(ibkr_qty))   # use actual filled qty from IBKR
                        filled = True
                        break
                    log(f"  {symbol}: Order {order_id} {d['status']} — not recording")
                    return None
            except Exception:
                pass
        if not filled:
            log(f"  {symbol}: Fill not confirmed after 6s — skipping DB entry")
            return None

        trade_id = log_trade_entry(
            symbol=symbol, entry_price=price, shares=shares,
            target_price=target, stop_price=sl, setup_type=strategy,
            rsi=rsi, volume_ratio=vol_ratio, sector=sector,
            earnings_days=999, confidence=confidence, order_id=str(order_id),
            side=side
        )
        if trade_id:
            trade_entry_times[trade_id] = datetime.now(ET)
            # Capture HOD at entry from 5-min bars (best-effort, non-blocking)
            try:
                import sqlite3 as _sq
                _mdb = _sq.connect(os.path.join(_DIR, 'market_data.db'))
                _now_utc = datetime.utcnow().strftime('%Y-%m-%d %H:%M')
                _rth_et = ET.localize(datetime(datetime.now(ET).year, datetime.now(ET).month, datetime.now(ET).day, 9, 30))
                _rth_start = _rth_et.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M')
                _hod_row = _mdb.execute(
                    "SELECT MAX(high) FROM bars_5m WHERE symbol=? AND ts_utc>=? AND ts_utc<=?",
                    (symbol, _rth_start, _now_utc + ':59')
                ).fetchone()
                _mdb.close()
                if _hod_row and _hod_row[0]:
                    _conn = __import__('sqlite3').connect(os.path.join(_DIR, 'trades.db'))
                    _conn.execute('UPDATE trades SET hod_at_entry=? WHERE id=?', (_hod_row[0], trade_id))
                    _conn.commit()
                    _conn.close()
            except Exception:
                pass  # HOD capture is optional — never block a trade entry
        return trade_id
    except Exception as e:
        log(f"Place trade error {symbol}: {e}")
        return None

# ─────────────────────────────────────────────────────────
# MONITOR OPEN TRADES — handles both LONG and SHORT positions
# ─────────────────────────────────────────────────────────
def monitor_open_trades(regime='NORMAL', confirmed_scans=1):
    trades = get_open_trades()
    if not trades:
        return []

    exits = []
    now   = datetime.now(ET)

    for trade in trades:
        tid    = trade['id']
        sym    = trade['symbol']
        shares = trade['shares']
        entry  = trade['entry_price']
        sl     = trade['stop_price']
        side   = trade.get('side', 'LONG')
        is_short = (side == 'SHORT')

        price = get_live_price(sym)
        if not price:
            continue

        if tid not in price_history:
            price_history[tid] = []
        price_history[tid].append(price)
        if len(price_history[tid]) > 20:
            price_history[tid].pop(0)

        if is_short:
            session_low[tid] = min(session_low.get(tid, price), price)
            pnl_pct = (entry - price) / entry * 100   # positive when price drops
            pnl_usd = (entry - price) * shares
        else:
            session_high[tid] = max(session_high.get(tid, price), price)
            pnl_pct = (price - entry) / entry * 100
            pnl_usd = (price - entry) * shares

        atr = get_atr(sym) or (entry * 0.02)

        # ── ATR trailing stop ─────────────────────────────────
        # HIGH_VOL cluster: tighter trail (1.0×) — trend fades fast (46.8% next-day continuation)
        # All others: standard 1.5× trail
        trail_mult = 1.0 if sym in HIGH_VOL_SYMBOLS else ATR_TRAIL_MULT
        if is_short:
            trail_threshold = entry - atr              # 1 ATR of profit on short
            if price <= trail_threshold:
                atr_trail = round(session_low[tid] + trail_mult * atr, 2)
                if atr_trail < sl:                     # SL moves down for shorts
                    sl = atr_trail
                    update_trade_stop(tid, sl)
                    log(f"  {sym} ↓SHORT: ATR trail → ${sl} ({pnl_pct:+.1f}%)")
            # % trail for shorts: symmetric with long PCT trail, uses session_low
            # Fires in dead zone (+1.5% to ~+5%) before ATR trail activates
            # Was completely absent for shorts — bug fix May 2026
            if pnl_pct >= PCT_TRAIL_ACTIVATE:
                pct_trail_sl = round(session_low[tid] * (1 + PCT_TRAIL_GAP / 100), 2)
                if pct_trail_sl < entry and pct_trail_sl < sl:
                    sl = pct_trail_sl
                    update_trade_stop(tid, sl)
                    log(f"  {sym} ↓SHORT: PCT trail → ${sl} ({pnl_pct:+.1f}%)")
        else:
            trail_threshold = entry + atr
            if price >= trail_threshold:
                atr_trail = round(session_high[tid] - trail_mult * atr, 2)
                if atr_trail > sl:
                    sl = atr_trail
                    update_trade_stop(tid, sl)
                    log(f"  {sym}: ATR trail → ${sl} ({pnl_pct:+.1f}%)")

            # % trail: activates at +1.5%, trails 0.5% below session high
            # Fires in the dead zone (+1.5% to ~+5%) where ATR trail won't activate
            # For big movers where ATR trail already set a higher SL, max() keeps the better one
            if pnl_pct >= PCT_TRAIL_ACTIVATE:
                pct_trail_sl = round(session_high[tid] * (1 - PCT_TRAIL_GAP / 100), 2)
                if pct_trail_sl > entry and pct_trail_sl > sl:
                    sl = pct_trail_sl
                    update_trade_stop(tid, sl)
                    log(f"  {sym}: PCT trail → ${sl} ({pnl_pct:+.1f}%)")

        # ── Break-even stop: once +2.5% profit ───────────────
        if pnl_pct >= 2.5:
            if is_short and sl > entry:                # SL already above entry = ok
                risk_dist = sl - entry
                be_sl = round(entry + max(risk_dist * 0.5, 0.05), 2)  # tighten ABOVE entry
                if be_sl < sl:
                    sl = be_sl
                    update_trade_stop(tid, sl)
                    log(f"  {sym} ↓SHORT: Break-even → ${sl} ({pnl_pct:+.1f}%)")
            elif not is_short and sl < entry:
                risk_dist = entry - sl
                be_sl = round(entry + max(risk_dist * 0.5, 0.05), 2)
                if be_sl > sl:
                    sl = be_sl
                    update_trade_stop(tid, sl)
                    log(f"  {sym}: Break-even stop → ${sl} ({pnl_pct:+.1f}%)")

        # ── Fetch 5-min bars ──────────────────────────────────
        df5             = None
        vwap_val        = None
        above_vwap      = None
        prev_above_vwap = None
        if is_market_open():
            try:
                df5 = yf.Ticker(sym).history(period='1d', interval='5m')
                if len(df5) >= 3:
                    df5['typical'] = (df5['High'] + df5['Low'] + df5['Close']) / 3
                    df5['vwap']    = (df5['typical'] * df5['Volume']).cumsum() / df5['Volume'].cumsum()
                    vwap_val        = round(float(df5['vwap'].iloc[-1]), 2)
                    above_vwap      = float(df5['Close'].iloc[-1]) > float(df5['vwap'].iloc[-1])
                    prev_above_vwap = float(df5['Close'].iloc[-2]) > float(df5['vwap'].iloc[-2])
            except Exception:
                pass

        # ── 5-min trailing stop ───────────────────────────────
        if is_market_open() and pnl_pct >= 3.0 and df5 is not None and len(df5) >= 3:
            try:
                if is_short:
                    intra_trail = round(float(df5['High'].iloc[-3:-1].max()), 2)
                    if intra_trail < sl:
                        sl = intra_trail
                        update_trade_stop(tid, sl)
                        log(f"  {sym} ↓SHORT: 5m trail → ${sl} ({pnl_pct:+.1f}%)")
                else:
                    intra_trail = round(float(df5['Low'].iloc[-3:-1].min()), 2)
                    if intra_trail > sl:
                        sl = intra_trail
                        update_trade_stop(tid, sl)
                        log(f"  {sym}: 5m trail → ${sl} ({pnl_pct:+.1f}%)")
            except Exception:
                pass

        # ── Partial exit at 1R (5% gain) — only on strong first-bar entries ──
        if (is_market_open() and pnl_pct >= 5.0
                and first_bar_strong_trades.get(tid, False)
                and tid not in partial_done_trades and shares >= 2):
            half = shares // 2
            cover_side = 'BUY' if is_short else 'SELL'
            try:
                requests.post(f"{BRIDGE}/order", json={
                    'symbol': sym, 'qty': half,
                    'side': cover_side, 'order_type': 'MARKET'
                }, timeout=10)
                locked = round(pnl_usd / shares * half, 2)
                partial_done_trades[tid] = locked
                update_trade_shares(tid, shares - half)
                try:
                    import sqlite3 as _sq3p
                    _cp = _sq3p.connect(os.path.join(_DIR, 'trades.db'))
                    _cp.execute('UPDATE trades SET partial_exited=1 WHERE id=?', (tid,))
                    _cp.commit()
                    _cp.close()
                except Exception:
                    pass
                tag = '↓SHORT' if is_short else ''
                log(f"  {sym} {tag}: PARTIAL EXIT {half}sh @ ${price} +${locked:.0f} locked — trailing {shares - half}sh")
                exits.append({'sym': sym, 'price': price, 'entry': entry,
                              'pnl': locked, 'pnl_pct': pnl_pct, 'pnl_usd': locked,
                              'reason': f'Partial exit 50% at 1R ({pnl_pct:+.1f}%)', 'side': side})
                shares = shares - half
            except Exception as e:
                log(f"Partial exit error {sym}: {e}")

        # ── Exit decisions ────────────────────────────────────
        exit_reason = None

        # 0a. P&L protection: peak session P&L ≥$200 dropped 25% — cut non-runners
        # Non-runner = pnl < -0.3% (never moved in our favour, don't hold hoping)
        # Applies both LONG and SHORT. Free the capital for better next setup.
        if pl_protect_active and pnl_pct < -0.3:
            exit_reason = (f'P&L protection: session peak ${peak_session_pnl:.0f} '
                           f'dropped 25% — cutting non-runner ({pnl_pct:+.1f}%)')

        # 0b. Regime flip exit: cover losing shorts when market turns NORMAL/STRONG
        # Requires 3 consecutive scans (symmetric with 3-scan bear entry requirement)
        # 2-scan rule caused 6/24 premature covers (+$754/yr improvement with 3-scan)
        if (not exit_reason and is_short and regime in ('NORMAL', 'STRONG')
                and confirmed_scans >= 3 and pnl_pct < 0):
            exit_reason = (f'Regime flip {regime} (x{confirmed_scans}) — '
                           f'covering losing short ({pnl_pct:+.1f}% / ${pnl_usd:+.0f})')

        # 0c. Long flip-exit: exit losing longs when market turns WEAK (x3 consecutive)
        # Mirror of short flip-exit. Jun 9 2026: entered 3 SEMIS longs at NORMAL x2,
        # regime degraded CAUTIOUS→WEAK x3 at 10:27am — stops hit 10:31-11:22am.
        # Exiting at WEAK x3 saves ~50% of eventual stop loss on distribution days.
        if (not exit_reason and not is_short
                and regime == 'WEAK' and confirmed_scans >= 3 and pnl_pct < 0):
            exit_reason = (f'Regime flip WEAK (x{confirmed_scans}) — '
                           f'exiting losing long ({pnl_pct:+.1f}% / ${pnl_usd:+.0f})')

        # 1. Hard stop
        if is_short:
            if price >= sl:
                exit_reason = f'Short stop ${sl} hit ({pnl_pct:+.1f}% / ${pnl_usd:+.0f})'
        else:
            if price <= sl:
                exit_reason = f'Stop ${sl} hit ({pnl_pct:+.1f}% / ${pnl_usd:+.0f})'

        # 1b. Manual trade: exit at user-specified profit target
        if not exit_reason and trade.get('setup_type') == 'MANUAL' and not is_short:
            manual_target = trade.get('target_price') or 0
            if manual_target and price >= manual_target:
                exit_reason = f'Manual target ${manual_target} reached ({pnl_pct:+.1f}%)'

        # 2. Circuit breaker
        if not exit_reason and pnl_usd <= -MAX_LOSS_PER_TRADE:
            exit_reason = f'Circuit breaker: -${MAX_LOSS_PER_TRADE} hit (${pnl_usd:+.0f})'

        # 3. VWAP signal: cross above = cover short / cross below = exit long
        if not exit_reason and is_market_open() and pnl_pct > 0.5 and above_vwap is not None:
            if is_short and above_vwap and prev_above_vwap is False:
                exit_reason = f'VWAP cross above ${vwap_val} — short momentum gone ({pnl_pct:+.1f}%)'
            elif not is_short and not above_vwap and prev_above_vwap is True:
                exit_reason = f'VWAP cross below ${vwap_val} ({pnl_pct:+.1f}% / ${pnl_usd:+.0f})'

        # 4. Momentum fade (bounce for shorts, drop for longs)
        if not exit_reason and pnl_pct > 0.3:
            if is_short:
                rise = price - session_low.get(tid, price)
                if rise > ATR_FADE_MULT * atr:
                    exit_reason = f'Short fade {ATR_FADE_MULT}×ATR bounce ({pnl_pct:+.1f}%)'
            else:
                drop = session_high.get(tid, price) - price
                if drop > ATR_FADE_MULT * atr:
                    exit_reason = f'Momentum fade {ATR_FADE_MULT}×ATR from high ({pnl_pct:+.1f}%)'

        # 5. No-move exit
        # INSTITUTIONAL cluster: 300 min — these consolidate longer before continuing (vol_clustering 0.26)
        # All others: 240 min standard
        if not exit_reason and is_market_open():
            entry_dt = trade_entry_times.get(tid)
            if entry_dt:
                mins_held = (now - entry_dt).total_seconds() / 60
                no_move_limit = 300 if sym in INSTITUTIONAL_SYMBOLS else 240
                if mins_held >= no_move_limit and -0.3 <= pnl_pct <= 2.0:
                    if pnl_usd > 0:
                        # Profitable but flat — lock breakeven as stop, hold for potential breakout
                        be_stop = round(entry * 1.0005, 2) if not is_short else round(entry * 0.9995, 2)
                        # Guard: stop must be below current price (long) — prevents immediate fire
                        # when stock is barely positive (e.g. +0.04%: be_stop > price → exits instantly)
                        price_ok = (not is_short and be_stop < price) or (is_short and be_stop > price)
                        if price_ok and abs(sl - be_stop) > 0.01:
                            update_trade_stop(tid, be_stop)
                            sl = be_stop
                            log(f"  {sym}: No-move {mins_held:.0f}min (+${pnl_usd:.0f}) — locking breakeven stop ${be_stop}")
                    else:
                        exit_reason = f'No-move exit: flat {mins_held:.0f}min ({pnl_pct:+.1f}%)'

        # 6. EOD close — shorts ALWAYS cover (no overnight shorts)
        if not exit_reason and is_market_open() and (now.hour, now.minute) >= (EOD_CLOSE_HOUR, EOD_CLOSE_MINUTE):
            if is_short:
                exit_reason = f'EOD: cover short — no overnight shorts ({pnl_pct:+.1f}%)'
            else:
                conviction = pnl_pct > 1.5 and above_vwap is True
                if not conviction:
                    vwap_tag   = 'above VWAP' if above_vwap else 'below VWAP'
                    exit_reason = f'EOD: no overnight conviction ({pnl_pct:+.1f}% {vwap_tag})'

        # 7. Hard time stop (longs only — shorts already covered EOD)
        if not exit_reason and not is_short:
            entry_date = get_trade_entry_date(tid)
            if entry_date:
                bdays_held = int(np.busday_count(entry_date, date.today().isoformat()))
                if bdays_held >= MAX_HOLD_DAYS:
                    exit_reason = f'Max hold {bdays_held}bd — time exit ({pnl_pct:+.1f}%)'

        direction_tag = ' ↓SHORT' if is_short else ''
        vwap_tag = f' VWAP${vwap_val}' if vwap_val else ''
        log(f"  {sym}{direction_tag}: ${price} ({pnl_pct:+.1f}% / ${pnl_usd:+.0f}) SL=${sl}{vwap_tag} ATR=${atr:.2f} "
            f"→ {'EXIT' if exit_reason else 'HOLD'}")

        if exit_reason:
            try:
                ibkr_pos = get_ibkr_positions()
                ibkr_qty = abs(ibkr_pos.get(sym, {}).get('qty', 0) or 0)
                if ibkr_qty <= 0:
                    log(f"  {sym}: SKIP exit — no IBKR position, closing DB only")
                    log_trade_exit(tid, price, 'DB cleanup — no IBKR position')
                    open_positions.pop(sym, None)
                    continue
                close_side = 'BUY' if is_short else 'SELL'
                close_qty  = min(shares, int(ibkr_qty))
                # DB write first — if IBKR call fails, reconcile_with_ibkr() corrects state
                if is_short:
                    peak = session_low.get(tid, entry)
                    _max_gain_pct = (entry - peak) / entry * 100 if peak < entry else 0.0
                else:
                    peak = session_high.get(tid, entry)
                    _max_gain_pct = (peak - entry) / entry * 100 if peak > entry else 0.0
                pnl = log_trade_exit(tid, price, exit_reason, max_gain_pct=_max_gain_pct)
                requests.post(f"{BRIDGE}/order", json={
                    'symbol': sym, 'qty': close_qty,
                    'side': close_side, 'order_type': 'MARKET'
                }, timeout=10)
                exits.append({
                    'sym': sym, 'price': price, 'entry': entry,
                    'pnl': pnl, 'pnl_pct': pnl_pct, 'pnl_usd': round(pnl_usd, 2),
                    'reason': exit_reason, 'side': side
                })
                for d in (price_history, session_high, session_low, open_positions):
                    d.pop(tid if tid in d else sym, None)
            except Exception as e:
                log(f"Exit error {sym}: {e}")

    return exits

# ─────────────────────────────────────────────────────────
# FAST POSITION MONITOR — hard stop checks every 30 sec
# Only checks price vs stop_price and circuit breaker.
# All other exit logic (VWAP, trail, no-move, EOD) stays in the 5-min monitor_open_trades().
# ─────────────────────────────────────────────────────────
def fast_monitor_positions():
    trades = get_open_trades()
    if not trades:
        return
    for trade in trades:
        tid    = trade['id']
        sym    = trade['symbol']
        shares = trade['shares']
        entry  = trade['entry_price']
        sl     = trade['stop_price']
        side   = trade.get('side', 'LONG')
        is_short = (side == 'SHORT')

        price = get_live_price(sym)
        if not price:
            continue

        if is_short:
            pnl_usd = (entry - price) * shares
            stop_hit = price >= sl
        else:
            pnl_usd = (price - entry) * shares
            stop_hit = price <= sl

        circuit_hit = pnl_usd <= -MAX_LOSS_PER_TRADE

        if stop_hit or circuit_hit:
            reason = (f'Fast stop: ${sl} hit ({pnl_usd:+.0f})' if stop_hit
                      else f'Fast circuit breaker: -${MAX_LOSS_PER_TRADE} hit (${pnl_usd:+.0f})')
            try:
                ibkr_pos = get_ibkr_positions()
                ibkr_qty = abs(ibkr_pos.get(sym, {}).get('qty', 0) or 0)
                # DB write first — if IBKR call fails, reconcile_with_ibkr() corrects state
                if is_short:
                    _peak = session_low.get(tid, entry)
                    _max_gain = (entry - _peak) / entry * 100 if _peak < entry else 0.0
                else:
                    _peak = session_high.get(tid, entry)
                    _max_gain = (_peak - entry) / entry * 100 if _peak > entry else 0.0
                pnl = log_trade_exit(tid, price, reason, max_gain_pct=_max_gain)
                if ibkr_qty > 0:
                    close_side = 'BUY' if is_short else 'SELL'
                    requests.post(f"{BRIDGE}/order", json={
                        'symbol': sym, 'qty': min(shares, int(ibkr_qty)),
                        'side': close_side, 'order_type': 'MARKET'
                    }, timeout=10)
                for d in (price_history, session_high, session_low, open_positions):
                    d.pop(tid if tid in d else sym, None)
                direction = '↓SHORT' if is_short else ''
                log(f"  ⚡ FAST EXIT {sym} {direction}: {reason} | PnL ${pnl:+.2f}")
                send_telegram(f"⚡ FAST EXIT {sym}: {reason}\nPnL ${pnl:+.2f}")
            except Exception as e:
                log(f"Fast exit error {sym}: {e}")


# ─────────────────────────────────────────────────────────
# CHART INTELLIGENCE — mplfinance generation + Claude vision
# ─────────────────────────────────────────────────────────

def _generate_chart_b64(df, title, vwap_series=None):
    """Generate a candlestick chart as base64 PNG. Returns None on failure."""
    try:
        ap = []
        if vwap_series is not None and len(vwap_series) == len(df):
            ap.append(mpf.make_addplot(vwap_series, color='blue', width=1.2, label='VWAP'))
        buf = io.BytesIO()
        mpf.plot(df, type='candle', style='charles', title=title,
                 volume=True, addplot=ap if ap else None,
                 figsize=(10, 6), savefig=dict(fname=buf, format='png', dpi=100))
        buf.seek(0)
        return base64.b64encode(buf.read()).decode('utf-8')
    except Exception as e:
        log(f"Chart gen error: {e}")
        return None

def _claude_analyse_image(b64_image, prompt, media_type='image/png'):
    """Send a base64 image to Claude and return the text response."""
    try:
        resp = _ai.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=512,
            messages=[{'role': 'user', 'content': [
                {'type': 'image', 'source': {'type': 'base64',
                                              'media_type': media_type,
                                              'data': b64_image}},
                {'type': 'text', 'text': prompt}
            ]}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log(f"Claude vision error: {e}")
        return None

def _chart_alignment_check(sym, entry_price, sl, strategy):
    """
    LOG MODE: generate 5m + 1h charts for sym, ask Claude if 1h trend supports
    the 5m entry. Logs YES/NO + reason. Does NOT block the trade.
    Runs in a background thread — zero latency impact on scan loop.
    """
    try:
        df5  = yf.Ticker(sym).history(period='1d',  interval='5m')
        df1h = yf.Ticker(sym).history(period='10d', interval='1h')
        if df5.empty or df1h.empty or len(df5) < 5 or len(df1h) < 5:
            return

        # Compute VWAP on 5m
        df5 = df5.copy()
        df5['typical'] = (df5['High'] + df5['Low'] + df5['Close']) / 3
        vwap = (df5['typical'] * df5['Volume']).cumsum() / df5['Volume'].cumsum()

        b64_5m = _generate_chart_b64(df5[['Open','High','Low','Close','Volume']],
                                     f'{sym} 5m intraday', vwap_series=vwap)
        b64_1h = _generate_chart_b64(df1h[['Open','High','Low','Close','Volume']],
                                     f'{sym} 1h (10 days)')
        if not b64_5m or not b64_1h:
            return

        # Ask Claude about 1h alignment
        prompt_1h = (f"You are a technical trading analyst. This is a 1-hour chart of {sym} "
                     f"over the last 10 trading days. The system just entered a LONG at ${entry_price} "
                     f"(stop ${sl}, strategy: {strategy}). "
                     f"Does the 1h trend and structure SUPPORT this long entry? "
                     f"Answer with YES or NO on the first line, then one sentence of reasoning.")
        answer_1h = _claude_analyse_image(b64_1h, prompt_1h)

        # Ask Claude about 5m setup quality
        prompt_5m = (f"This is a 5-minute intraday chart of {sym} with VWAP. "
                     f"A LONG entry was taken at ${entry_price}. "
                     f"Rate the setup quality 1-5 and describe the pattern in one sentence.")
        answer_5m = _claude_analyse_image(b64_5m, prompt_5m)

        aligned = 'YES' in (answer_1h or '').upper()
        log(f"  [CHART GATE LOG] {sym} | 1h aligned: {'✅' if aligned else '❌'} | {answer_1h}")
        log(f"  [CHART GATE LOG] {sym} | 5m quality: {answer_5m}")
    except Exception as e:
        log(f"Chart alignment check error {sym}: {e}")

# ─────────────────────────────────────────────────────────
# TELEGRAM COMMAND HANDLER
# ─────────────────────────────────────────────────────────
def poll_telegram_commands():
    global tg_update_id, daily_bull_count, daily_sympathy_count
    global _longs_paused, _watch_mode, _watch_last_sent
    try:
        r = requests.get(f"{TG_API}/getUpdates",
                         params={'offset': tg_update_id, 'timeout': 0},
                         timeout=5)
        updates = r.json().get('result', [])
        for update in updates:
            tg_update_id = update['update_id'] + 1
            msg  = update.get('message', {})
            sender_id   = msg.get('chat', {}).get('id')
            sender_name = msg.get('chat', {}).get('first_name', '')
            is_owner    = str(sender_id) == str(TELEGRAM_CHAT_ID)
            is_viewer   = sender_id in VIEWER_CHAT_IDS
            if not is_owner and not is_viewer:
                continue   # ignore unknown senders
            chat_type = msg.get('chat', {}).get('type', 'group')
            reply_to = sender_id if chat_type == 'private' else TELEGRAM_CHAT_ID
            text = msg.get('text', '').strip().upper()

            # ── Photo messages: handle before the text guard ──────
            photo = msg.get('photo')
            if photo:
                try:
                    file_id   = photo[-1]['file_id']
                    r_file    = requests.get(f"{TG_API}/getFile",
                                             params={'file_id': file_id}, timeout=10)
                    file_path = r_file.json()['result']['file_path']
                    img_bytes = requests.get(
                        f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}",
                        timeout=15).content
                    b64       = base64.b64encode(img_bytes).decode('utf-8')
                    ext       = file_path.rsplit('.', 1)[-1].lower()
                    media_type = 'image/jpeg' if ext in ('jpg', 'jpeg') else f'image/{ext}'
                    send_telegram_to(reply_to, "Analysing chart...")
                    caption = msg.get('caption', '')
                    prompt  = (
                        "You are an expert technical trading analyst. Analyse this trading chart. "
                        "Identify: (1) trend direction and strength, (2) key support/resistance levels, "
                        "(3) volume story — confirming or diverging, (4) chart pattern if any "
                        "(ORB, flag, VWAP reclaim, breakout, etc.), (5) is this a clean setup to trade "
                        "or one to avoid, and why. Be concise — 5 bullet points max."
                        + (f"\n\nContext from user: {caption}" if caption else "")
                    )
                    analysis = _claude_analyse_image(b64, prompt, media_type=media_type)
                    send_telegram_to(reply_to, analysis or "Could not analyse chart — try again.")
                except Exception as ex:
                    send_telegram_to(reply_to, f"Chart analysis error: {ex}")
                continue   # photo handled — skip text command processing

            if not text:
                continue
            log(f"TG command: {text}")

            # ── Viewer gate: read-only commands only ──────────────
            if is_viewer and text not in ('REGIME', 'STATUS', 'STATUS ALL', 'HELP'):
                continue

            if text == 'HELP':
                send_telegram_to(reply_to, '\n'.join([
                    "📊 *Equity commands:*",
                    "STATUS            — P&L, open positions, 30d win rate",
                    "REGIME            — market condition + full snapshot",
                    "TODAY             — today's closed trades + per-trade P&L",
                    "PAUSE/STOP/CANCEL — halt ALL new entries (monitor stays on)",
                    "PAUSE LONGS       — block longs only, shorts still active",
                    "WATCH             — PAUSE LONGS + regime update every 30 min",
                    "RESUME            — re-enable all entries",
                    "BUY <SYMBOL> [%]  — manual buy (e.g. BUY TSLA 2.5)",
                    "SELL <SYMBOL>     — close one position (e.g. SELL TSLA)",
                    "CLOSEALL          — close ALL open positions now",
                    "BLOCK <SYMBOL>    — skip symbol for rest of day",
                    "",
                    "📡 *Options commands (prefix OPT):*",
                    "OPT STATUS        — options portfolio overview",
                    "OPT POSITIONS     — per-position Greeks + stop stage",
                    "OPT NEWS          — signal leaderboard (top 10)",
                    "OPT NEWS <sym>    — per-ticker signals + narrative",
                    "OPT BUY <symbol>  — spread/LEAP calculator",
                    "OPT CLOSE <sym>   — close an options position",
                    "OPT PAUSE/RESUME  — halt/resume options scanning",
                    "",
                    "📊 *OPT NEWS leaderboard columns:*",
                    "Sigs — total MEDIUM+HIGH signals (last 5 days)",
                    "H    — how many were HIGH quality (catalyst-level)",
                    "IVR  — IV Rank: <30=cheap ✅  30-50=moderate  >50=expensive ⚠️",
                    "Rec  — ACT!  cheap IV + strong signal → buy now",
                    "       LEAP  cheap IV, fewer signals → LEAP candidate",
                    "       SPR   moderate IV → spreads only",
                    "       WAIT  elevated IV → wait for pullback",
                    "       SKIP  IV too expensive or wrong direction",
                    "       PUT   bearish setup, IV reasonable → puts ok",
                    "       OBS   mixed signals → observe only",
                    "       ?     IV not yet fetched → check before acting",
                    "",
                    "📖 *REGIME decoder:*",
                    "STRONG  — clear bull run, enter freely",
                    "NORMAL  — healthy market, standard entries",
                    "CHOPPY  — mixed signals, be selective",
                    "CAUTIOUS— weakening, reduce size",
                    "WEAK    — bear conditions, no new longs",
                    "",
                    "VIX < 18        — calm, options cheap ✅",
                    "VIX 18–25       — normal fear, watch trend",
                    "VIX > 25        — high fear, pause entries ⚠️",
                    "",
                    "ES/NQ green     — market expects higher open",
                    "ES/NQ red       — market expects lower open",
                    "QQQ leading     — tech outperforming, momentum day",
                    "QQQ lagging     — tech weak, defensive tone",
                    "Broad advance   — all sectors rising, rally is real",
                    "Mixed breadth   — only large caps moving, be selective",
                    "Broad weakness  — widespread selling, avoid new longs",
                ]))

            elif text == 'STATUS':
                ibkr    = get_ibkr_positions()
                wr      = get_win_rate(days=30)
                live_positions = {s: p for s, p in ibkr.items() if (p.get('qty') or 0) != 0}
                daily_rpnl  = get_daily_pnl()
                losses = daily_rpnl['trades'] - daily_rpnl['wins']
                n_open = len(live_positions)
                pos_lines = []
                live_upnl = 0.0
                total_invest = 0.0
                for sym, pos in sorted(live_positions.items()):
                    qty      = int(abs(pos.get('qty', 0) or 0))
                    avg      = pos.get('avgCost', 0) or 0
                    mkt      = pos.get('marketPrice') or 0
                    # IBKR doesn't push marketPrice immediately after restart — use live quote
                    if not mkt and avg:
                        mkt = get_live_price(sym) or avg
                    is_short = (pos.get('qty', 0) or 0) < 0
                    upnl     = pos.get('unrealizedPnL') or 0
                    if not upnl and avg and mkt:
                        upnl = (avg - mkt if is_short else mkt - avg) * qty
                    live_upnl    += upnl
                    total_invest += avg * qty
                    pnl_pct = ((mkt - avg) / avg * 100) if avg else 0
                    if is_short:
                        pnl_pct = -pnl_pct
                    side_tag = '(S)' if is_short else ''
                    pos_lines.append(
                        f"{sym}{side_tag} x{qty}  ${mkt:.2f}"
                        f"  {upnl:+.0f} ({pnl_pct:+.1f}%)"
                    )
                total_pnl = round(live_upnl + daily_rpnl['pnl'], 2)
                lines = [
                    f"Status | {datetime.now(ET).strftime('%H:%M ET')}",
                    f"P&L {total_pnl:+.2f} | R: {daily_rpnl['pnl']:+.2f} | uPnL: {live_upnl:+.2f}",
                    f"Trades: {daily_rpnl['trades']} ({daily_rpnl['wins']}W {losses}L) | Open: {n_open} | 30d WR: {wr:.0f}%",
                ]
                if pos_lines:
                    lines.append('')
                    lines.extend(pos_lines)
                    lines.append(f"Invested: ${total_invest:,.0f}")
                else:
                    lines.append('No open positions.')
                send_telegram_to(reply_to, '\n'.join(lines))

            elif text == 'STATUS ALL':
                send_telegram_to(reply_to, _portfolio_all())

            elif text in ('CANCEL', 'STOP', 'PAUSE'):
                send_telegram("New entries paused. Monitoring open trades only. Send RESUME to re-enable.")
                with open(os.path.join(_DIR, 'trading_blocked.json'), 'w') as f:
                    json.dump({'date': date.today().isoformat(), 'blocked': True,
                               'reason': 'User sent CANCEL via Telegram'}, f)

            elif text == 'PAUSE LONGS':
                _longs_paused = True
                _watch_mode   = False
                send_telegram(
                    "⏸ Longs paused — new LONG entries blocked, shorts still active.\n"
                    "Regime will still print each scan. Send RESUME to re-enable."
                )

            elif text == 'WATCH':
                _longs_paused    = True
                _watch_mode      = True
                _watch_last_sent = None
                send_telegram(
                    "👁 WATCH mode — longs paused, regime update every 30 min.\n"
                    "Shorts still active. Send RESUME to exit watch mode."
                )

            elif text == 'RESUME':
                _longs_paused = False
                _watch_mode   = False
                block_file = os.path.join(_DIR, 'trading_blocked.json')
                if os.path.exists(block_file):
                    os.remove(block_file)
                send_telegram("✅ Resumed — scanning for both longs and shorts.")

            elif text.startswith('BUY '):
                # Accept: BUY TSLA  or  BUY TSLA 2.5  or  BUY TSLA 2.5%
                parts = text.split()
                sym   = parts[1].upper()
                profit_pct = 1.0
                if len(parts) >= 3:
                    try:
                        profit_pct = float(parts[2].replace('%', ''))
                    except ValueError:
                        send_telegram(f"BUY {sym}: invalid profit % '{parts[2]}' — use e.g. BUY TSLA 2.5")
                        continue
                price = get_live_price(sym)
                if not price:
                    send_telegram(f"BUY {sym}: could not get live price — order not placed.")
                else:
                    sl     = round(price * 0.95, 2)
                    target = round(price * (1 + profit_pct / 100), 2)
                    shares = max(1, int(100 / (price * 0.05)))  # $100 max risk, same as system
                    tid = place_trade(sym, price, shares, sl, target,
                                      strategy='MANUAL', grade='B',
                                      confidence=99, sector='OTHER', side='LONG')
                    if tid:
                        traded_today.add(sym)
                        save_traded_today()
                        daily_bull_count += 1
                        send_telegram(
                            f"MANUAL BUY {sym} | {shares}sh @ ${price}\n"
                            f"Target: ${target} (+{profit_pct}%) | Stop: ${sl} (-5%)\n"
                            f"Will auto-exit at +{profit_pct}% profit."
                        )
                    else:
                        send_telegram(f"BUY {sym}: order placed but fill not confirmed — check IBKR.")

            elif text.startswith('SELL '):
                sym    = text.split()[1].upper()
                trades = get_open_trades()
                match  = [t for t in trades if t['symbol'] == sym]
                if not match:
                    send_telegram(f"SELL {sym}: no open position found.")
                else:
                    t     = match[0]
                    price = get_live_price(sym)
                    if not price:
                        send_telegram(f"SELL {sym}: could not get live price.")
                    else:
                        is_short   = t.get('side', 'LONG') == 'SHORT'
                        close_side = 'BUY' if is_short else 'SELL'
                        ibkr_pos   = get_ibkr_positions()
                        ibkr_qty   = abs(ibkr_pos.get(sym, {}).get('qty', 0) or 0)
                        qty        = min(t['shares'], int(ibkr_qty)) if ibkr_qty > 0 else t['shares']
                        try:
                            # DB first — if IBKR call fails, reconcile_with_ibkr() corrects state
                            pnl     = log_trade_exit(t['id'], price, 'Manual close via Telegram SELL')
                            pnl_pct = ((t['entry_price'] - price) if is_short else (price - t['entry_price'])) / t['entry_price'] * 100
                            if ibkr_qty > 0:
                                requests.post(f"{BRIDGE}/order",
                                              json={'symbol': sym, 'qty': qty,
                                                    'side': close_side, 'order_type': 'MARKET'},
                                              timeout=10)
                            else:
                                log(f"SELL {sym}: no IBKR position — DB-only close")
                            for d in (price_history, session_high, session_low):
                                d.pop(t['id'], None)
                            open_positions.pop(sym, None)
                            send_telegram(
                                f"SOLD {sym} | {qty}sh @ ${price}\n"
                                f"Entry: ${t['entry_price']} | P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)"
                            )
                        except Exception as ex:
                            send_telegram(f"SELL {sym}: error — {ex}")

            elif text == 'CLOSEALL':
                trades = get_open_trades()
                if not trades:
                    send_telegram("No open positions to close.")
                else:
                    ibkr_pos = get_ibkr_positions()
                    lines    = ["CLOSEALL"]
                    total_pnl = 0.0
                    for t in trades:
                        sym   = t['symbol']
                        price = get_live_price(sym)
                        if not price:
                            lines.append(f"  {sym}: skip (no price)")
                            continue
                        is_short   = t.get('side', 'LONG') == 'SHORT'
                        close_side = 'BUY' if is_short else 'SELL'
                        ibkr_qty   = abs(ibkr_pos.get(sym, {}).get('qty', 0) or 0)
                        qty        = min(t['shares'], int(ibkr_qty)) if ibkr_qty > 0 else t['shares']
                        try:
                            # DB first — if IBKR call fails, reconcile_with_ibkr() corrects state
                            pnl     = log_trade_exit(t['id'], price, 'Manual CLOSEALL via Telegram')
                            pnl_pct = ((t['entry_price'] - price) if is_short else (price - t['entry_price'])) / t['entry_price'] * 100
                            if ibkr_qty > 0:
                                requests.post(f"{BRIDGE}/order",
                                              json={'symbol': sym, 'qty': qty,
                                                    'side': close_side, 'order_type': 'MARKET'},
                                              timeout=10)
                            else:
                                log(f"CLOSEALL {sym}: no IBKR position — DB-only close")
                            for d in (price_history, session_high, session_low):
                                d.pop(t['id'], None)
                            open_positions.pop(sym, None)
                            total_pnl += pnl or 0
                            lines.append(f"  {sym}: ${price} | ${pnl:+.2f} ({pnl_pct:+.1f}%)")
                        except Exception as ex:
                            lines.append(f"  {sym}: ERROR {ex}")
                    lines.append(f"Total P&L: ${total_pnl:+.2f}")
                    send_telegram('\n'.join(lines))

            elif text == 'REGIME':
                try:
                    regime, spy_chg, vix_val, extra = get_regime()
                    vix_rising   = extra.get('vix_rising', False)
                    qqq_leading  = extra.get('qqq_leading', False)
                    above_vwap   = extra.get('spy_above_vwap', False)
                    broad_adv    = extra.get('broad_advance', True)
                    breadth_weak = extra.get('breadth_weak', False)
                    es_chg       = extra.get('es_chg', 0.0)
                    nq_chg       = extra.get('nq_chg', 0.0)

                    # VIX interpretation
                    if vix_val < 18:
                        vix_note = 'calm market, options cheap'
                    elif vix_val < 25:
                        vix_note = 'normal fear, watch direction'
                    else:
                        vix_note = 'high fear — pause new entries'
                    vix_arrow = 'rising ⚠️' if vix_rising else 'falling ✅'

                    # Futures interpretation
                    if es_chg > 0 and nq_chg > 0:
                        fut_note = 'market expects higher open'
                    elif es_chg < 0 and nq_chg < 0:
                        fut_note = 'market expects lower open'
                    elif nq_chg > es_chg:
                        fut_note = 'tech leading, risk-on tone'
                    else:
                        fut_note = 'tech lagging, mixed open'

                    # QQQ note
                    qqq_note = ('tech outperforming — momentum day ✅'
                                if qqq_leading else
                                'tech lagging — defensive tone ⚠️')

                    # Breadth interpretation
                    if broad_adv:
                        breadth_str  = 'broad advance ✅'
                        breadth_note = 'small+mid caps rising — all sectors in, healthy rally'
                    elif breadth_weak:
                        breadth_str  = 'broad weakness ⚠️'
                        breadth_note = 'small+mid caps falling — widespread selling, avoid new longs'
                    else:
                        breadth_str  = 'mixed'
                        breadth_note = 'only large caps moving — selective market, wait for confirmation'

                    send_telegram_to(reply_to, '\n'.join([
                        f"Regime: {regime} | {datetime.now(ET).strftime('%H:%M ET')}",
                        f"SPY: {spy_chg:+.2f}% | VIX: {vix_val:.1f} {vix_arrow} ({vix_note})",
                        f"SPY {'above' if above_vwap else 'below'} VWAP | QQQ {qqq_note}",
                        f"ES: {es_chg:+.2f}% | NQ: {nq_chg:+.2f}% — {fut_note}",
                        f"Breadth: {breadth_str} — {breadth_note}",
                    ]))
                except Exception as ex:
                    send_telegram_to(reply_to, f"Regime fetch error: {ex}")

            elif text == 'TODAY':
                closed = get_today_trades()
                if not closed:
                    send_telegram("No closed trades today yet.")
                else:
                    lines = [f"Today's trades ({len(closed)}):"]
                    for t in closed:
                        tag     = '↓' if t['side'] == 'SHORT' else '↑'
                        outcome = 'W' if t['status'] == 'WIN' else 'L'
                        lines.append(
                            f"  {tag}{t['symbol']} {outcome} ${t['pnl']:+.2f} "
                            f"({t['entry']}→{t['exit']})"
                        )
                    total = sum(t['pnl'] for t in closed)
                    wins  = sum(1 for t in closed if t['status'] == 'WIN')
                    lines.append(f"Total: ${total:+.2f} | {wins}/{len(closed)} wins")
                    send_telegram('\n'.join(lines))

            elif text.startswith('BLOCK '):
                sym = text.split()[1].upper()
                traded_today.add(sym)
                save_traded_today()
                send_telegram(f"BLOCK {sym}: skipping for rest of today. Resets at midnight.")

    except Exception as e:
        log(f"TG poll error: {e}")

def is_trading_blocked():
    block_file = os.path.join(_DIR, 'trading_blocked.json')
    if not os.path.exists(block_file):
        return False, None
    try:
        with open(block_file) as f:
            data = json.load(f)
        if data.get('date') == date.today().isoformat():
            return data.get('blocked', False), data.get('reason')
    except:
        pass
    return False, None

# ─────────────────────────────────────────────────────────
# SYMPATHY TRIGGER DETECTION — runs once at market open
# ─────────────────────────────────────────────────────────
def detect_sympathy_triggers():
    """
    Runs once at 9:35–9:40am. Checks if any SYMPATHY_MAP trigger gapped >5%
    on earnings today (reported after previous close). Qualifying sympathy stocks
    (sector peers that gapped >2%) are added to active_sympathy_triggers and
    prepended to catalyst_priority so they rank above regular setups.
    Bull only — bear sympathy has insufficient edge (58% WR, excluded).
    """
    global active_sympathy_triggers, catalyst_priority
    active_sympathy_triggers = {}

    fired_triggers = []
    for trigger in SYMPATHY_MAP:
        try:
            hist = yf.Ticker(trigger).history(period='2d', interval='1d')
            if len(hist) < 2:
                continue
            prev_close = float(hist['Close'].iloc[-2])
            today_open = float(hist['Open'].iloc[-1])
            if prev_close <= 0:
                continue
            move = (today_open - prev_close) / prev_close
            if move < SYMPATHY_TRIGGER_THRESH:
                continue
            fired_triggers.append((trigger, round(move * 100, 1)))
            log(f"  💫 SYMPATHY TRIGGER: {trigger} +{move*100:.1f}% at open — scanning basket")
        except Exception as e:
            log(f"  Sympathy detect {trigger}: {e}")

    if not fired_triggers:
        return

    for trigger, trigger_move in fired_triggers:
        for sym in SYMPATHY_MAP[trigger]:
            if sym in active_sympathy_triggers:
                continue  # already flagged by another trigger
            try:
                hist = yf.Ticker(sym).history(period='2d', interval='1d')
                if len(hist) < 2:
                    continue
                prev_close = float(hist['Close'].iloc[-2])
                today_open = float(hist['Open'].iloc[-1])
                if prev_close <= 0:
                    continue
                gap = (today_open - prev_close) / prev_close
                if gap < SYMPATHY_GAP_THRESH:
                    log(f"    {sym}: gap {gap*100:+.1f}% < {SYMPATHY_GAP_THRESH*100:.0f}% threshold — skip")
                    continue
                active_sympathy_triggers[sym] = {
                    'trigger':       trigger,
                    'trigger_move':  trigger_move,
                    'gap':           round(gap * 100, 1),
                }
                if sym not in catalyst_priority:
                    catalyst_priority.insert(0, sym)
                log(f"    ✅ {sym}: gap {gap*100:+.1f}% — sympathy candidate")
            except Exception as e:
                log(f"    Sympathy check {sym}: {e}")

    if active_sympathy_triggers:
        syms = ', '.join(active_sympathy_triggers.keys())
        triggers_str = ', '.join(f"{t} +{m:.0f}%" for t, m in fired_triggers)
        send_telegram(f"💫 SYMPATHY ALERT\nTrigger: {triggers_str}\nWatching: {syms}")


# ─────────────────────────────────────────────────────────
# PRE-MARKET CATALYST SCAN — earnings gap plays only, 9:20–9:29am
# ─────────────────────────────────────────────────────────
def _scan_premarket_catalyst(open_trades):
    """
    Scan for stocks gapping ≥10% on earnings (released previous evening).
    Enter via LIMIT order before 9:30am open. Max 2 positions, half size, 6% stop.
    3 gates must all pass: gap size, volume, gap-hold.
    """
    global daily_bull_count, traded_today, pm_scan_done
    pm_scan_done = True

    log(f"\n{'='*55}")
    log(f"PRE-MARKET SCAN 9:20am — earnings gap plays (LIMIT orders, outsideRth)")

    today     = date.today()
    scan_order = catalyst_priority + [s for s in FULL_UNIVERSE if s not in catalyst_priority]
    candidates = []

    for symbol in scan_order:
        if symbol in traded_today:
            continue
        if any(t['symbol'] == symbol for t in open_trades):
            continue
        if daily_bull_count >= MAX_DAILY_BULL_TRADES:
            break
        if len(open_trades) + len(candidates) >= MAX_OPEN_TRADES:
            break

        try:
            # ── Fetch pre-market 1-min bars from IBKR (live, no delay) ───
            # Fallback to yfinance if bridge unavailable
            pm_price = pm_high = pm_vol = prev_close = None
            try:
                r = requests.get(
                    f"{BRIDGE}/history/{symbol}",
                    params={'duration': '1 D', 'bar_size': '1 min', 'rth': 'false'},
                    timeout=12
                )
                bars = r.json()
                if bars and len(bars) >= 5:
                    df = pd.DataFrame(bars)
                    df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert(ET)
                    df = df.set_index('date').sort_index()
                    df.columns = [c.capitalize() for c in df.columns]

                    reg = df[(df.index.hour >= 9) & (df.index.hour < 16)
                             & (df.index.date < today)]
                    pm  = df[(df.index.date == today) & (
                        (df.index.hour < 9) | ((df.index.hour == 9) & (df.index.minute < 30))
                    )]
                    if not reg.empty and not pm.empty and len(pm) >= 3:
                        prev_close = float(reg['Close'].iloc[-1])
                        pm_price   = float(pm['Close'].iloc[-1])
                        pm_high    = float(pm['High'].max())
                        pm_vol     = int(pm['Volume'].sum())
            except Exception:
                pass

            # Fallback: yfinance (15-min delayed but better than nothing)
            if pm_price is None:
                raw = yf.Ticker(symbol).history(period='2d', interval='1m', prepost=True)
                if raw.empty or len(raw) < 10:
                    continue
                raw.index = raw.index.tz_convert(ET)
                reg_bars  = raw[(raw.index.date < today) & (raw.index.hour >= 9) & (raw.index.hour < 16)]
                pm_bars   = raw[(raw.index.date == today) & (
                    (raw.index.hour < 9) | ((raw.index.hour == 9) & (raw.index.minute < 30))
                )]
                if reg_bars.empty or pm_bars.empty or len(pm_bars) < 3:
                    continue
                prev_close = float(reg_bars['Close'].iloc[-1])
                pm_price   = float(pm_bars['Close'].iloc[-1])
                pm_high    = float(pm_bars['High'].max())
                pm_vol     = int(pm_bars['Volume'].sum())

            if None in (pm_price, pm_high, pm_vol, prev_close):
                continue

            # Price range
            if pm_price < 5 or pm_price > 800:
                continue

            # ── Gate 1: gap threshold — 3-tier by price ──────────────────
            # Large-cap ≥$150: 6%  |  Mid-cap $50-149: 8%  |  Small-cap <$50: 10%
            if   pm_price >= 150: gap_min = PREMARKET_GAP_LARGE
            elif pm_price >= 50:  gap_min = PREMARKET_GAP_MID
            else:                 gap_min = PREMARKET_GAP_SMALL
            gap_pct = (pm_price - prev_close) / prev_close * 100
            if gap_pct < gap_min:
                continue

            # ── Gate 2: pre-market volume ≥ floor ────────────────────────
            if pm_vol < PREMARKET_VOL_MIN:
                continue

            # ── Gate 3: price holding near PM high (not fading) ──────────
            if pm_high > 0 and pm_price < pm_high * PREMARKET_HOLD_PCT:
                continue

            # Scoring
            score = 0
            reasons = []

            if gap_pct >= 20:
                score += 40; reasons.append(f'Gap +{gap_pct:.1f}% massive')
            elif gap_pct >= 15:
                score += 30; reasons.append(f'Gap +{gap_pct:.1f}% strong')
            else:
                score += 20; reasons.append(f'Gap +{gap_pct:.1f}%')

            if pm_vol >= 500_000:
                score += 20; reasons.append(f'{pm_vol/1e6:.1f}M PM vol')
            elif pm_vol >= 200_000:
                score += 15; reasons.append(f'{pm_vol/1000:.0f}K PM vol')
            else:
                score += 10; reasons.append(f'{pm_vol/1000:.0f}K PM vol')

            hold_pct = pm_price / pm_high * 100
            if hold_pct >= 99:
                score += 25; reasons.append('Gap at PM high ✓')
            elif hold_pct >= 97:
                score += 15; reasons.append(f'Gap holding {hold_pct:.0f}% of PM high')

            # Sector ETF tailwind (reuse sector_strength from last update)
            sector = get_symbol_sector(symbol)
            etf    = SECTOR_ETF_MAP.get(sector, 'SPY')
            etf_chg = sector_strength.get(etf, 0)
            if etf_chg >= 1.0:
                score += 10; reasons.append(f'{etf} +{etf_chg:.1f}% sector up')

            grade = 'A+' if score >= 55 else 'A' if score >= 40 else 'SKIP'
            if grade == 'SKIP':
                log(f"  {symbol}: PM gap {gap_pct:+.1f}% (gate {gap_min}%) score={score} — below threshold")
                continue

            candidates.append({
                'symbol': symbol, 'price': pm_price, 'pm_high': pm_high,
                'pm_vol': pm_vol, 'gap_pct': gap_pct, 'prev_close': prev_close,
                'grade': grade, 'score': score, 'reasons': reasons, 'sector': sector,
            })

        except Exception as e:
            log(f"  PM scan {symbol}: {e}")

    candidates.sort(key=lambda x: -x['score'])
    log(f"Pre-market: {len(candidates)} qualified gap candidates")

    entries    = []
    attempted  = 0   # orders submitted this cycle (incl. failures) — prevents MAX_OPEN bypass
    open_count = len(open_trades)

    for pick in candidates:
        if len(entries) >= MAX_PREMARKET_TRADES:
            break
        if open_count + len(entries) + attempted >= MAX_OPEN_TRADES:
            break
        if daily_bull_count >= MAX_DAILY_BULL_TRADES:
            break

        sym   = pick['symbol']
        price = pick['price']

        # 6% stop (wider than regular 5% — PM price wicks on thin volume)
        sl          = round(price * 0.94, 2)
        target      = round(price * 1.15, 2)   # 15% target on gap plays
        rr          = round((target - price) / (price - sl), 1)
        # Half position size: $1,000 cap, also ATR-bounded by $100 max risk
        risk_ps     = round(price - sl, 4)
        atr_shares  = int(MAX_LOSS_PER_TRADE / risk_ps) if risk_ps > 0 else 0
        shares      = max(1, min(int(1000 / price), atr_shares))
        limit_price = round(price * 1.005, 2)   # 0.5% above PM price — fills before 9:30am

        log(f"  ⭐ {pick['grade']} {sym} [{pick['sector']}] PM ${price} gap {pick['gap_pct']:+.1f}% "
            f"vol {pick['pm_vol']/1000:.0f}K | LIMIT ${limit_price} SL ${sl} T ${target} "
            f"R:R 1:{rr} | {', '.join(pick['reasons'])}")

        attempted += 1
        trade_id = place_trade(
            sym, price, shares, sl, target,
            'EARNINGS_GAP', pick['grade'],
            vol_ratio=round(pick['pm_vol'] / 100_000, 1),
            confidence=pick['score'], sector=pick['sector'],
            limit_price=limit_price, outside_rth=True,
        )

        if trade_id:
            traded_today.add(sym)
            save_traded_today()
            open_positions[sym] = trade_id
            first_bar_strong_trades[trade_id] = True  # PM gaps are inherently strong (≥6% filter)
            daily_bull_count += 1
            entries.append(pick | {'shares': shares, 'limit_price': limit_price, 'sl': sl})
            time.sleep(1)

    if entries:
        lines = [f"⭐ PRE-MARKET {len(entries)} — earnings gaps (LIMIT outsideRth)"]
        for e in entries:
            lines.append(f"  {e['grade']} {e['symbol']} gap {e['gap_pct']:+.1f}% "
                         f"x{e['shares']} LIMIT ${e['limit_price']} SL ${e['sl']}")
        send_telegram('\n'.join(lines))

    return []


# ─────────────────────────────────────────────────────────
# MAIN SCAN
# ─────────────────────────────────────────────────────────
def run_scan():
    global daily_bull_count, daily_bear_count, traded_today, regime_history, spy_open_price
    global sympathy_scan_done, _gateway_unstable_until

    # ── Gateway stability gate ─────────────────────────────────────────────
    # Layer 1: is the bridge connected right now?
    _entries_allowed = True
    try:
        bst = requests.get(f"{BRIDGE}/", timeout=5).json()
        if not bst.get('connected', False):
            log("⚠️ Gateway not connected — reconcile skipped, entries blocked this cycle")
            _gateway_unstable_until = datetime.now(ET) + timedelta(minutes=10)
            send_telegram("⚠️ IBKR gateway disconnected — entries paused for 10 min")
            _entries_allowed = False
    except Exception as _be:
        log(f"⚠️ Bridge unreachable ({_be}) — entries blocked this cycle")
        _entries_allowed = False

    # Layer 2: post-reconnect cooldown (10 min freeze after any disconnect event)
    if _entries_allowed and _gateway_unstable_until:
        if datetime.now(ET) < _gateway_unstable_until:
            mins_left = int((_gateway_unstable_until - datetime.now(ET)).total_seconds() / 60) + 1
            log(f"⚠️ Post-reconnect freeze: {mins_left} min remaining — monitoring only")
            _entries_allowed = False
        else:
            _gateway_unstable_until = None   # freeze expired

    # Reconcile DB with IBKR (only if gateway is up)
    if _entries_allowed:
        reconcile_with_ibkr()

        # Layer 3: position parity — if counts still differ after reconcile, don't enter
        ibkr_pos  = get_ibkr_positions()
        ibkr_count = len([p for p in ibkr_pos.values() if int(p.get('qty', 0)) != 0])
        db_count   = len(get_open_trades())
        if ibkr_count != db_count:
            log(f"⚠️ Position mismatch after reconcile: IBKR={ibkr_count} DB={db_count} — entries blocked this cycle")
            send_telegram(f"⚠️ Position mismatch IBKR={ibkr_count} DB={db_count} — entries paused, check positions")
            _entries_allowed = False

    # Update sector ETF strengths once per scan cycle
    update_sector_strength()

    regime, spy_chg, vix, extra = get_regime()
    open_trades = get_open_trades()
    daily       = get_daily_pnl()

    # Track regime stability — rolling window of last N readings
    regime_history.append(regime)
    if len(regime_history) > 6:
        regime_history.pop(0)
    confirmed_scans = 0
    for _r in reversed(regime_history):
        if _r == regime:
            confirmed_scans += 1
        else:
            break

    # Track SPY price from market open — set once on first post-open scan
    now = datetime.now(ET)
    if is_market_open() and spy_open_price is None:
        try:
            spy_data = yf.Ticker('SPY').history(period='1d', interval='1m')
            if not spy_data.empty:
                first_bar_date = spy_data.index[0].date()
                if first_bar_date == now.date():
                    spy_open_price = round(float(spy_data['Open'].iloc[0]), 2)
                    log(f"SPY open price locked: ${spy_open_price}")
                else:
                    log(f"SPY open: skipped — data is from {first_bar_date}, not today")
        except Exception:
            pass

    # Sympathy trigger detection — once per day, first post-open scan
    if is_market_open() and not sympathy_scan_done:
        detect_sympathy_triggers()
        sympathy_scan_done = True

    spy_above_open = True
    if spy_open_price:
        try:
            spy_now = yf.Ticker('SPY').history(period='1d', interval='1m')['Close'].iloc[-1]
            spy_above_open = float(spy_now) >= spy_open_price * 0.998  # allow 0.2% noise
        except Exception:
            pass

    vwap_str   = f"VWAP {'↑' if extra.get('spy_above_vwap') else '↓'}"
    vix_str    = f"VIX {vix:.1f}{'↑' if extra.get('vix_rising') else '↓'}"
    qqq_str    = f"QQQ {'lead' if extra.get('qqq_leading') else 'lag'}"
    es_chg     = extra.get('es_chg', 0)
    nq_chg     = extra.get('nq_chg', 0)
    fut_str    = f"ES {es_chg:+.1f}% NQ {nq_chg:+.1f}%"
    breadth_str = 'breadth ✓' if extra.get('broad_advance') else ('breadth WEAK' if extra.get('breadth_weak') else 'breadth ~')
    log(f"\n{'='*55}")
    log(f"SCAN | Regime: {regime} (x{confirmed_scans}) | SPY {spy_chg:+.1f}% {'↑open' if spy_above_open else '↓open'} | {vwap_str} | {vix_str} | {qqq_str}")
    log(f"      Futures: {fut_str} | {breadth_str}")

    # ── WATCH mode: send regime snapshot every 30 min ─────────────────────
    global _watch_last_sent
    if _watch_mode and is_market_open():
        now_et = datetime.now(ET)
        if _watch_last_sent is None or (now_et - _watch_last_sent).total_seconds() >= 1800:
            _watch_last_sent = now_et
            open_syms = ', '.join(t['symbol'] for t in open_trades) if open_trades else 'none'
            pause_note = ' ⏸ LONGS PAUSED' if _longs_paused else ''
            send_telegram(
                f"👁 WATCH update | {now_et.strftime('%H:%M ET')}{pause_note}\n"
                f"Regime: {regime} (x{confirmed_scans}) | SPY {spy_chg:+.1f}%\n"
                f"{vwap_str} | {vix_str}\n"
                f"Open positions: {open_syms}\n"
                f"Send RESUME to re-enable longs."
            )

    # Live session P&L = realized today + current unrealized
    try:
        portfolio_snap = requests.get(f"{BRIDGE}/portfolio", timeout=8).json()
        unrealized_now = sum(p.get('unrealizedPnL', 0) or 0 for p in portfolio_snap if (p.get('qty') or 0) != 0)
    except Exception:
        unrealized_now = 0
    session_pnl = daily['pnl'] + unrealized_now

    symp_str = f" Sympathy {daily_sympathy_count}/{MAX_DAILY_SYMPATHY_TRADES}" if active_sympathy_triggers else ""
    log(f"Open: {len(open_trades)} | Bull {daily_bull_count}/{MAX_DAILY_BULL_TRADES} Bear {daily_bear_count}/{MAX_DAILY_BEAR_TRADES}{symp_str} today | Realized: ${daily['pnl']:+.2f} | Session: ${session_pnl:+.2f}")

    # P&L protection: track session peak, fire when peak ≥$200 drops 25%
    global peak_session_pnl, pl_protect_active
    if session_pnl > peak_session_pnl:
        peak_session_pnl = session_pnl
    if (not pl_protect_active
            and peak_session_pnl >= PL_PROTECT_PEAK
            and session_pnl < peak_session_pnl * (1 - PL_PROTECT_DROP)):
        pl_protect_active = True
        log(f"⚠️  P&L PROTECTION ACTIVE: peak ${peak_session_pnl:.0f} ({PL_PROTECT_PEAK_PCT:.1f}% of capital) "
            f"→ now ${session_pnl:.0f} (-{(peak_session_pnl - session_pnl) / peak_session_pnl * 100:.0f}%) — will cut non-runners")

    # Always monitor open trades
    exits = []

    # Pre-market window: fire once per day at 9:20–9:29am for earnings gap entries
    if is_premarket_window() and not pm_scan_done:
        if _entries_allowed:
            _scan_premarket_catalyst(open_trades)
        return  # no monitoring needed — no open positions pre-market

    if not _entries_allowed:
        log("Gateway unstable / position mismatch — monitoring only, no new entries")
        exits = monitor_open_trades(regime, confirmed_scans)
    elif not is_entry_window():
        log("Outside entry window — monitoring only")
        exits = monitor_open_trades(regime, confirmed_scans)
    elif is_trading_blocked()[0]:
        log(f"Trading blocked — monitoring only")
        exits = monitor_open_trades(regime, confirmed_scans)
    elif regime == 'CHOPPY':
        log(f"CHOPPY market — monitoring only, no new entries")
        exits = monitor_open_trades(regime, confirmed_scans)
    elif regime == 'WEAK':
        # Require 3 consecutive WEAK scans before any bear entry (all-day rule)
        # Eliminates false signals from brief dips, lunch noise, and quick regime flips
        if confirmed_scans < 3:
            log(f"WEAK market — need 3 confirmed scans (have {confirmed_scans}) — monitoring only")
            exits = monitor_open_trades(regime, confirmed_scans)
        else:
            log(f"WEAK market — routing to bear strategy (short scan)")
            exits = _scan_and_enter_bear(regime, spy_chg, open_trades, confirmed_scans)
    elif not spy_above_open:
        log(f"SPY below open price (${spy_open_price}) — no new longs until market recovers")
        exits = monitor_open_trades(regime, confirmed_scans)
    elif len(open_trades) >= MAX_OPEN_TRADES:
        log(f"Max open trades ({MAX_OPEN_TRADES}) — monitoring only")
        exits = monitor_open_trades(regime, confirmed_scans)
    elif daily_bull_count >= MAX_DAILY_BULL_TRADES:
        log(f"Max bull trades ({MAX_DAILY_BULL_TRADES}) reached")
        exits = monitor_open_trades(regime, confirmed_scans)
    elif session_pnl >= DAILY_PROFIT_TARGET:
        log(f"✅ Daily target +${session_pnl:.0f} hit — protecting gains, no new entries")
        exits = monitor_open_trades(regime, confirmed_scans)
    elif confirmed_scans < MIN_REGIME_SCANS:
        log(f"Regime {regime} only confirmed {confirmed_scans}x — waiting for stability")
        exits = monitor_open_trades(regime, confirmed_scans)
    else:
        exits = _scan_and_enter(regime, spy_chg, open_trades, confirmed_scans)

    # Batched WhatsApp exit message
    if exits:
        real_exits = [x for x in exits if 'Partial' not in x['reason']]
        partials   = [x for x in exits if 'Partial' in x['reason']]
        if real_exits:
            wins      = [x for x in real_exits if x['pnl'] and x['pnl'] > 0]
            total_pnl = sum(x['pnl'] for x in real_exits if x['pnl'])
            lines     = [f"CLOSES {len(real_exits)} | {len(wins)}W/{len(real_exits)-len(wins)}L | ${total_pnl:+.2f}"]
            for x in real_exits:
                e = '✅' if x['pnl'] and x['pnl'] > 0 else '❌'
                lines.append(f"  {e} {x['sym']} ${x['entry']}→${x['price']} ${x['pnl']:+.2f} ({x['pnl_pct']:+.1f}%)")
            send_telegram('\n'.join(lines))
        if partials:
            lines = [f"PARTIAL EXITS {len(partials)} (50% locked in)"]
            for x in partials:
                lines.append(f"  ✅ {x['sym']} 50% @ ${x['price']} P&L ${x['pnl']:+.2f}")
            send_telegram('\n'.join(lines))

def get_deployed_capital():
    """Sum of capital currently locked in open positions."""
    return sum(t['shares'] * t['entry_price'] for t in get_open_trades())

def get_position_capital(grade, is_catalyst, deployed, first_bar_strong=False):
    """Dynamic allocation — $2,000 max per trade; 5 trades = $10K fully deployed.
    Strong first-bar days get +15% capital (backtest-validated structural edge)."""
    remaining = TOTAL_CAPITAL - deployed
    if remaining < 200:
        return 0
    if is_catalyst and grade == 'A+':
        alloc = 2000   # top catalyst
    elif grade == 'A+':
        alloc = 1800   # strong momentum
    elif is_catalyst and grade == 'A':
        alloc = 1600   # catalyst, decent grade
    else:
        alloc = 1400   # solid A setup
    if first_bar_strong:
        alloc = int(alloc * 1.15)
    return round(min(alloc, remaining), 2)

def _scan_catalyst_override(open_trades):
    """
    Scan for isolated catalyst plays (earnings/news gap-and-go) on WEAK market days.
    These are market-independent: a stock up 6%+ on earnings goes up regardless of SPY.
    Rules: gap 6%+ from prev close, still above open price now, volume 2x+, A+ grade only.
    Position size is halved since market backdrop is not supportive.
    """
    global daily_bull_count, traded_today

    now = datetime.now(ET)
    # Only run in entry window and after opening range
    if not is_entry_window():
        return []

    # Build scan list: dynamic (momentum scanner) picks first, then full universe
    scan_order = catalyst_priority + [s for s in FULL_UNIVERSE if s not in catalyst_priority]
    entries   = []
    attempted = 0   # orders submitted this cycle (incl. failures) — prevents MAX_OPEN bypass

    for symbol in scan_order:
        if symbol in traded_today:
            continue
        if any(t['symbol'] == symbol for t in open_trades):
            continue
        if daily_bull_count >= MAX_DAILY_BULL_TRADES:
            break
        if len(open_trades) + len(entries) + attempted >= MAX_OPEN_TRADES:
            break

        try:
            # Quick check: is this stock gapping 6%+ with 2x+ volume?
            hist = yf.Ticker(symbol).history(period='2d', interval='1d')
            if len(hist) < 2:
                continue
            prev_close  = float(hist['Close'].iloc[-2])
            today_open  = float(hist['Open'].iloc[-1])
            today_vol   = float(hist['Volume'].iloc[-1])
            avg_vol     = float(yf.Ticker(symbol).history(period='30d')['Volume'].mean())
            gap_pct     = (today_open - prev_close) / prev_close * 100
            vol_ratio   = today_vol / avg_vol if avg_vol > 0 else 1

            # Must be gapping 6%+ with at least 2x volume — confirmed catalyst
            if gap_pct < 6.0 or vol_ratio < 2.0:
                continue

            sig = get_intraday_signals(symbol)
            if sig is None:
                continue

            price = sig['price']
            # Stock must still be above its open price (gap not fading)
            if price < today_open * 0.99:
                continue

            sl, target, risk_pct, reward_pct, rr = calc_sl_target(symbol, price, 'LONG')
            grade, reasons, score = grade_setup(sig, 'NORMAL', sl, target, price, rr, symbol=symbol)

            # Catalyst override requires A+ only
            if grade != 'A+':
                continue

            sector  = get_symbol_sector(symbol)
            # Half position size on WEAK market day
            deployed = get_deployed_capital()   # entries already in DB via log_trade_entry
            capital  = get_position_capital(grade, True, deployed) * 0.5
            if capital < 100:
                continue
            # ATR-normalized: size so actual stop risk ≤ MAX_LOSS_PER_TRADE (halved for WEAK day)
            risk_per_share = round(price - sl, 4)
            atr_shares     = int((MAX_LOSS_PER_TRADE * 0.5) / risk_per_share) if risk_per_share > 0 else int(capital / price)
            shares         = max(1, min(int(capital / price), atr_shares))

            log(f"  ⚡ CATALYST OVERRIDE {symbol} gap {gap_pct:+.1f}% vol {vol_ratio:.1f}x — entering despite WEAK market")

            attempted += 1
            trade_id = place_trade(
                symbol, price, shares, sl, target,
                'CATALYST_OVERRIDE', grade,
                rsi=sig['rsi'], vol_ratio=sig['vol_ratio'],
                confidence=score, sector=sector,
            )
            if trade_id:
                traded_today.add(symbol)
                save_traded_today()
                open_positions[symbol] = trade_id
                first_bar_strong_trades[trade_id] = sig.get('first_bar_strong', False)
                daily_bull_count += 1
                entries.append({'symbol': symbol, 'price': price, 'shares': shares,
                                'sl': sl, 'target': target, 'gap_pct': gap_pct,
                                'vol_ratio': vol_ratio})

        except Exception as e:
            log(f"  Catalyst override error {symbol}: {e}")

    if entries:
        lines = [f"⚡ CATALYST OVERRIDE — {len(entries)} isolated plays (WEAK mkt)"]
        for e in entries:
            lines.append(f"  {e['symbol']} gap {e['gap_pct']:+.1f}% | {e['shares']}sh @ ${e['price']} | SL${e['sl']} T${e['target']}")
        send_telegram('\n'.join(lines))

    return []

def _scan_and_enter(regime, spy_chg, open_trades, confirmed_scans=1):
    global daily_bull_count, traded_today

    # ── Manual longs-paused gate (PAUSE LONGS / WATCH commands) ───────────
    if _longs_paused:
        log("LONGS PAUSED (manual override) — monitoring only. Send RESUME to re-enable.")
        return monitor_open_trades(regime, confirmed_scans)

    # ── Daily max loss brake ───────────────────────────────────
    try:
        portfolio = requests.get(f"{BRIDGE}/portfolio", timeout=8).json()
        unrealized = sum(p.get('unrealizedPnL', 0) or 0 for p in portfolio if (p.get('qty') or 0) != 0)
    except Exception:
        unrealized = 0
    realized    = get_daily_pnl()
    daily_total = realized.get('pnl', 0) + unrealized
    if daily_total <= -MAX_DAILY_LOSS:
        log(f"⛔ Daily max loss hit (${daily_total:.0f}) — protecting capital, no new entries")
        global _daily_loss_alerted
        if not _daily_loss_alerted:
            send_telegram(f"⛔ Daily loss limit hit: ${daily_total:.0f} (limit -${MAX_DAILY_LOSS})\nNo new entries for rest of day.")
            _daily_loss_alerted = True
        return monitor_open_trades(regime, confirmed_scans)

    # ── Afternoon gate: protect morning gains, skip new longs after 12pm ──────
    # Afternoon LONG: 44.8% WR / -$0.91 avg on recycled capital (vs 58.5% morning)
    # Snapshot P&L once at first post-noon scan — frozen for rest of day so afternoon
    # losses from pre-noon trades don't re-open the gate
    global _morning_pnl_snap
    now_et = datetime.now(ET)
    if now_et.hour >= AFTERNOON_GATE_HOUR and _morning_pnl_snap is None:
        _morning_pnl_snap = peak_session_pnl  # session peak (realized + unrealized) — not realized-only
        log(f"⏰ Morning P&L snapshot: ${_morning_pnl_snap:.0f} peak session (frozen at {now_et.strftime('%H:%M')})")
    morning_pnl = _morning_pnl_snap if _morning_pnl_snap is not None else peak_session_pnl
    if now_et.hour >= AFTERNOON_GATE_HOUR and morning_pnl >= AFTERNOON_GATE_THRESHOLD:
        log(f"⏰ Afternoon gate: morning peak ${morning_pnl:.0f} ≥ ${AFTERNOON_GATE_THRESHOLD:.0f} ({AFTERNOON_GATE_PCT:.1f}% of capital) — no new longs after 12pm")
        return monitor_open_trades(regime, confirmed_scans)

    # ── Recycled slot gate: block new longs after 12:30 if any slot was vacated ──
    # Recycled LONG after 12:30: 15.4% WR / -$11.17 avg (vs 61.3% baseline, May 2026)
    RECYCLED_CUTOFF_HOUR, RECYCLED_CUTOFF_MIN = 12, 30
    if (now_et.hour > RECYCLED_CUTOFF_HOUR or
            (now_et.hour == RECYCLED_CUTOFF_HOUR and now_et.minute >= RECYCLED_CUTOFF_MIN)):
        open_count = len(open_trades)
        if daily_bull_count > open_count:  # a slot was vacated today
            log(f"⏰ Recycled slot gate: {daily_bull_count} long entries today, {open_count} open — no new longs after 12:30")
            return monitor_open_trades(regime, confirmed_scans)

    # Dynamic picks = symbols in catalyst_priority but NOT in fixed FULL_UNIVERSE
    dynamic_picks = [s for s in catalyst_priority if s not in FULL_UNIVERSE]

    # Build scan order: catalyst picks first, then rest of universe
    scan_order = catalyst_priority + [s for s in FULL_UNIVERSE if s not in catalyst_priority]
    candidates = []

    for symbol in scan_order:
        if symbol in traded_today:
            continue
        if any(t['symbol'] == symbol for t in open_trades):
            continue

        # Dynamic (unknown) stocks need strong confirmation — volatile at open
        is_dynamic = symbol in dynamic_picks
        if is_dynamic and regime != 'STRONG' and not (regime == 'NORMAL' and confirmed_scans >= 3):
            continue

        sig = get_intraday_signals(symbol, spy_chg=spy_chg)
        if sig is None:
            continue

        price = sig['price']
        if price < 5 or price > 800:
            continue

        side = 'LONG'
        if side == 'LONG' and sig['intra_chg'] < -5:
            continue

        sl, target, risk_pct, reward_pct, rr = calc_sl_target(symbol, price, side)
        is_catalyst  = symbol in catalyst_priority
        grade, reasons, score = grade_setup(sig, regime, sl, target, price, rr, symbol=symbol, is_catalyst=is_catalyst)

        _now         = datetime.now(ET)
        _sector      = get_symbol_sector(symbol)

        if grade in ('SKIP', 'C'):
            try:
                log_scan_candidate(
                    _now.strftime('%Y-%m-%d'), _now.strftime('%H:%M'),
                    symbol, 'LONG', regime, price, grade, score,
                    reasons[0] if reasons else None,
                    sig['vol_ratio'], sig['rsi'], sig['intra_chg'], _sector,
                    is_catalyst=is_catalyst, entered=False,
                )
            except Exception:
                pass
            continue
        # SPY negative intraday → only take A+ setups
        if spy_chg < 0 and grade != 'A+':
            try:
                log_scan_candidate(
                    _now.strftime('%Y-%m-%d'), _now.strftime('%H:%M'),
                    symbol, 'LONG', regime, price, grade, score,
                    'SPY negative — A+ only',
                    sig['vol_ratio'], sig['rsi'], sig['intra_chg'], _sector,
                    is_catalyst=is_catalyst, entered=False,
                )
            except Exception:
                pass
            continue

        # Dynamic (unknown) stocks require A+ regardless — too risky at lower grades
        if symbol in dynamic_picks and grade != 'A+':
            continue

        # Float gate for scanner-discovered stocks not in validated universe.
        # Very low float (<500K shares) = can't fill $1,400+ without large slippage.
        # Universe symbols are pre-validated and bypass this check.
        if symbol in dynamic_picks:
            try:
                _float = yf.Ticker(symbol).info.get('floatShares', 0) or 0
                if 0 < _float < 500_000:
                    log(f"  SKIP {symbol} — float {_float/1e3:.0f}K too thin for position sizing")
                    continue
            except Exception:
                pass  # if yfinance fails, allow — don't block on data fetch failure

        is_sympathy  = symbol in active_sympathy_triggers
        candidates.append({
            'symbol': symbol, 'price': price, 'grade': grade, 'score': score,
            'side': side, 'sl': sl, 'target': target, 'risk_pct': risk_pct, 'rr': rr,
            'reasons': reasons, 'fvg_count': sig['fvg_count'],
            'vol_ratio': sig['vol_ratio'], 'rsi': sig['rsi'],
            'intra_chg': sig['intra_chg'], 'is_catalyst': is_catalyst,
            'is_sympathy': is_sympathy,
            'first_bar_strong': sig.get('first_bar_strong', False),
            'burst_age_min':    sig.get('burst_age_min', 999),
            'consec_new_highs': sig.get('consec_new_highs', 0),
            'today_hod':        sig.get('today_hod', price),
            'sector': _sector, 'scan_time': _now.strftime('%H:%M'),
            'scan_date': _now.strftime('%Y-%m-%d'),
        })

    # ── Power-play batting order ────────────────────────────────────────────────
    # Tier: sympathy A+ → catalyst A+ → catalyst A → universe A+ → universe A
    # Within tier: sector strength (pitch report) → intra_chg (player form today)
    #              → vol_ratio (fitness/conviction) → score (player rating)
    # Data: catalyst flag predicts top-half movers at 53% vs 25% for ambient.
    # intra_chg is the individual "pitch report" — how fast is this stock already
    # moving? Score does NOT discriminate at A+ level (both halves avg ~215).
    # CONSUMER deprioritised: 67% WR, +2.7% avg max vs 100%/10%+ for other sectors.
    grade_order = {'A+': 0, 'A': 1, 'B': 2}
    candidates.sort(key=lambda x: (
        0 if (x['is_sympathy'] and x['grade'] == 'A+') else
        1 if (x['is_catalyst'] and x['grade'] == 'A+') else
        2 if (x['is_catalyst'] and x['grade'] == 'A')  else
        3 if x['grade'] == 'A+' else 4,
        _SLOT_SECTOR_PRIORITY.get(x.get('sector', 'OTHER'), 1),  # pitch report
        -x['intra_chg'],   # player form: harder mover goes first
        -x['vol_ratio'],   # fitness: volume conviction
        -x['score'],       # player rating: tiebreaker only
    ))

    _n_cat = sum(1 for c in candidates if c['is_catalyst'])
    log(f"Found {len(candidates)} valid setups ({_n_cat} catalyst) — batting order: "
        + " | ".join(f"{c['symbol']}({c['intra_chg']:+.1f}%{'⚡' if c['is_catalyst'] else ''})"
                     for c in candidates[:8]))

    # Log all qualified candidates with exact batting rank so bench players are traceable
    for _rank, _c in enumerate(candidates):
        if _rank < MAX_OPEN_TRADES:
            _reason = f"Slot #{_rank+1} in batting order"
        else:
            _reason = f"Bench #{_rank+1} — awaiting slot"
        _hod  = _c.get('today_hod') or _c['price']
        _pvh  = round((_c['price'] - _hod) / _hod * 100, 2) if _hod else None
        try:
            log_scan_candidate(
                _c['scan_date'], _c['scan_time'], _c['symbol'], 'LONG', regime,
                _c['price'], _c['grade'], _c['score'], _reason,
                _c['vol_ratio'], _c['rsi'], _c['intra_chg'], _c.get('sector'),
                is_catalyst=_c['is_catalyst'], entered=False,
                burst_age_min=_c.get('burst_age_min', 999),
                consec_new_highs=_c.get('consec_new_highs', 0),
                today_hod=_hod,
                price_vs_hod_pct=_pvh,
            )
        except Exception:
            pass

    entries       = []
    attempted     = 0   # orders submitted this cycle (incl. failures) — prevents MAX_OPEN bypass
    open_count    = len(open_trades)
    sector_counts = get_open_sector_counts()

    for pick in candidates:
        if open_count + len(entries) + attempted >= MAX_OPEN_TRADES:
            break
        if daily_bull_count >= MAX_DAILY_BULL_TRADES:
            break
        if pick['grade'] not in ('A+', 'A'):
            break

        sym    = pick['symbol']
        sector = get_symbol_sector(sym)

        # Sympathy cap — separate from regular bull cap (additive slots)
        if pick['is_sympathy'] and daily_sympathy_count >= MAX_DAILY_SYMPATHY_TRADES:
            log(f"  SKIP {sym} — sympathy cap ({MAX_DAILY_SYMPATHY_TRADES}) reached")
            continue

        # Enforce sector concentration limit (OTHER bucket is uncapped)
        if sector != 'OTHER' and sector_counts.get(sector, 0) >= MAX_PER_SECTOR:
            log(f"  SKIP {sym} — {sector} sector full ({MAX_PER_SECTOR} positions)")
            continue

        price    = pick['price']
        deployed = get_deployed_capital()   # entries already in DB via log_trade_entry
        capital  = get_position_capital(pick['grade'], pick['is_catalyst'], deployed,
                                        pick.get('first_bar_strong', False))
        if capital <= 0:
            log(f"  Capital cap reached (${deployed:,.0f}/${TOTAL_CAPITAL:,} deployed)")
            break
        # ATR-normalized: size so actual stop risk ≤ MAX_LOSS_PER_TRADE
        risk_per_share = round(price - pick['sl'], 4)
        atr_shares     = int(MAX_LOSS_PER_TRADE / risk_per_share) if risk_per_share > 0 else int(capital / price)
        shares         = max(1, min(int(capital / price), atr_shares))

        if pick['is_sympathy']:
            info     = active_sympathy_triggers[sym]
            strategy = 'SYMPATHY'
            tag      = '💫'
        elif pick['is_catalyst']:
            strategy = 'CATALYST'
            tag      = '⚡'
        else:
            strategy = 'FVG_FILL' if pick['fvg_count'] > 5 else 'MOMENTUM'
            tag      = '🎯'

        log(f"  {tag} {pick['grade']} {sym} [{sector}] ${price} | "
            f"Vol {pick['vol_ratio']:.1f}x | RSI {pick['rsi']} | R:R 1:{pick['rr']} | ATR stop")

        attempted += 1  # count this slot before we know fill outcome
        trade_id = place_trade(
            sym, price, shares, pick['sl'], pick['target'],
            strategy, pick['grade'],
            rsi=pick['rsi'], vol_ratio=pick['vol_ratio'],
            confidence=pick['score'], sector=sector,
        )

        if trade_id:
            traded_today.add(sym)
            save_traded_today()
            open_positions[sym] = trade_id
            first_bar_strong_trades[trade_id] = pick.get('first_bar_strong', False)
            daily_bull_count  += 1
            if pick['is_sympathy']:
                daily_sympathy_count += 1
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            entries.append(pick | {'shares': shares, 'sector': sector, 'tag': tag})
            # Update scan_log: mark this candidate as entered and link trade_id
            # SQLite doesn't support ORDER BY/LIMIT in UPDATE directly — use subquery
            try:
                import sqlite3 as _sq3
                _scan_date = pick.get('scan_date', datetime.now(ET).strftime('%Y-%m-%d'))
                _conn = _sq3.connect('trades.db')
                _conn.execute('''UPDATE scan_log SET entered=1, entry_trade_id=?
                                 WHERE id = (
                                     SELECT id FROM scan_log
                                     WHERE symbol=? AND scan_date=? AND direction='LONG' AND entered=0
                                     ORDER BY id DESC LIMIT 1
                                 )''',
                              (trade_id, sym, _scan_date))
                _conn.commit()
                _conn.close()
            except Exception:
                pass
        else:
            traded_today.add(sym)
            save_traded_today()
            # LOG MODE: chart alignment check runs in background, never blocks entry
            threading.Thread(
                target=_chart_alignment_check,
                args=(sym, price, pick['sl'], strategy),
                daemon=True
            ).start()
            time.sleep(2)

    # Monitor existing trades too
    exits = monitor_open_trades(regime, confirmed_scans)

    # Batched WhatsApp entry message
    if entries:
        lines = [f"NEW TRADES {len(entries)} | Bull {daily_bull_count}/{MAX_DAILY_BULL_TRADES}"]
        for e in entries:
            lines.append(f"  {e.get('tag','🎯')}{e['grade']} {e['symbol']} [{e.get('sector','?')}] x{e['shares']} @${e['price']} "
                         f"SL${e['sl']} T${e['target']} R:R 1:{e['rr']}")
        send_telegram('\n'.join(lines))

    return exits

# ─────────────────────────────────────────────────────────
# BEAR SCAN — WEAK days: scan for short setups
# ─────────────────────────────────────────────────────────
def _scan_and_enter_bear(regime, spy_chg, open_trades, confirmed_scans=1):
    global daily_bear_count, traded_today

    # ── Daily max loss brake ───────────────────────────────────
    try:
        portfolio = requests.get(f"{BRIDGE}/portfolio", timeout=8).json()
        unrealized = sum(p.get('unrealizedPnL', 0) or 0 for p in portfolio if (p.get('qty') or 0) != 0)
    except Exception:
        unrealized = 0
    realized    = get_daily_pnl()
    daily_total = realized.get('pnl', 0) + unrealized
    if daily_total <= -MAX_DAILY_LOSS:
        log(f"⛔ Daily max loss hit (${daily_total:.0f}) — protecting capital, no new entries")
        global _daily_loss_alerted
        if not _daily_loss_alerted:
            send_telegram(f"⛔ Daily loss limit hit: ${daily_total:.0f} (limit -${MAX_DAILY_LOSS})\nNo new entries for rest of day.")
            _daily_loss_alerted = True
        return monitor_open_trades(regime, confirmed_scans)

    # ── Afternoon gate: no new shorts after 12pm if morning was profitable ────
    # Afternoon SHORT: 18.2% WR / -$6.56 avg — catastrophic vs 51.5% morning
    # Uses same frozen snapshot as bull gate — set once at first post-noon scan
    global _morning_pnl_snap
    now_et = datetime.now(ET)
    if now_et.hour >= AFTERNOON_GATE_HOUR and _morning_pnl_snap is None:
        _morning_pnl_snap = peak_session_pnl  # session peak (realized + unrealized) — not realized-only
        log(f"⏰ Morning P&L snapshot: ${_morning_pnl_snap:.0f} peak session (frozen at {now_et.strftime('%H:%M')})")
    morning_pnl = _morning_pnl_snap if _morning_pnl_snap is not None else peak_session_pnl
    if now_et.hour >= AFTERNOON_GATE_HOUR and morning_pnl >= AFTERNOON_GATE_THRESHOLD:
        log(f"⏰ Afternoon gate: morning peak ${morning_pnl:.0f} ≥ ${AFTERNOON_GATE_THRESHOLD:.0f} ({AFTERNOON_GATE_PCT:.1f}% of capital) — no new shorts after 12pm")
        return monitor_open_trades(regime, confirmed_scans)

    # ── Recycled slot gate: block new shorts after 12:30 if any slot was vacated ──
    # Recycled SHORT after 12:30: 16-20% WR (vs 60.7% at 10am, May 2026)
    RECYCLED_CUTOFF_HOUR, RECYCLED_CUTOFF_MIN = 12, 30
    if (now_et.hour > RECYCLED_CUTOFF_HOUR or
            (now_et.hour == RECYCLED_CUTOFF_HOUR and now_et.minute >= RECYCLED_CUTOFF_MIN)):
        open_count = len(open_trades)
        if daily_bear_count > open_count:  # a slot was vacated today
            log(f"⏰ Recycled slot gate: {daily_bear_count} short entries today, {open_count} open — no new shorts after 12:30")
            return monitor_open_trades(regime, confirmed_scans)

    scan_order = catalyst_priority + [s for s in FULL_UNIVERSE if s not in catalyst_priority]
    candidates = []

    for symbol in scan_order:
        if symbol in traded_today:
            continue
        if symbol in BEAR_EXCLUDED:
            continue
        if get_symbol_sector(symbol) == 'ENERGY':  # 0% WR short, -$17.73 avg (4 trades May 2026)
            continue
        if any(t['symbol'] == symbol for t in open_trades):
            continue

        sig = get_intraday_signals(symbol, spy_chg=spy_chg)
        if sig is None:
            continue

        price = sig['price']
        if price < 5 or price > 800:
            continue

        # Skip stocks already surging — not short candidates
        if sig['intra_chg'] > 5:
            continue

        sl, target, risk_pct, reward_pct, rr = calc_sl_target(symbol, price, 'SHORT')
        is_catalyst  = symbol in catalyst_priority
        grade, reasons, score = grade_bear_setup(sig, regime, sl, target, price, rr, symbol=symbol)
        _now_b       = datetime.now(ET)
        _sector_b    = get_symbol_sector(symbol)

        if grade in ('SKIP', 'C'):
            try:
                log_scan_candidate(
                    _now_b.strftime('%Y-%m-%d'), _now_b.strftime('%H:%M'),
                    symbol, 'SHORT', regime, price, grade, score,
                    reasons[0] if reasons else None,
                    sig['vol_ratio'], sig['rsi'], sig['intra_chg'], _sector_b,
                    is_catalyst=is_catalyst, entered=False,
                )
            except Exception:
                pass
            continue
        # SPY recovering intraday → only highest-conviction shorts
        if spy_chg > 0 and grade != 'A+':
            try:
                log_scan_candidate(
                    _now_b.strftime('%Y-%m-%d'), _now_b.strftime('%H:%M'),
                    symbol, 'SHORT', regime, price, grade, score,
                    'SPY recovering — A+ only',
                    sig['vol_ratio'], sig['rsi'], sig['intra_chg'], _sector_b,
                    is_catalyst=is_catalyst, entered=False,
                )
            except Exception:
                pass
            continue

        # Float gate: scanner-discovered short candidates need tradeable float
        if symbol not in FULL_UNIVERSE:
            try:
                _float = yf.Ticker(symbol).info.get('floatShares', 0) or 0
                if 0 < _float < 500_000:
                    log(f"  SKIP {symbol} SHORT — float {_float/1e3:.0f}K too thin for position sizing")
                    continue
            except Exception:
                pass

        candidates.append({
            'symbol': symbol, 'price': price, 'grade': grade, 'score': score,
            'side': 'SHORT', 'sl': sl, 'target': target, 'risk_pct': risk_pct, 'rr': rr,
            'reasons': reasons, 'fvg_count': sig['fvg_count'],
            'vol_ratio': sig['vol_ratio'], 'rsi': sig['rsi'],
            'intra_chg': sig['intra_chg'], 'is_catalyst': is_catalyst,
            'first_bar_strong': sig.get('first_bar_strong', False),
            'burst_age_min':    sig.get('burst_age_min', 999),
            'consec_new_highs': sig.get('consec_new_highs', 0),
            'today_hod':        sig.get('today_hod', price),
            'sector': _sector_b, 'scan_time': _now_b.strftime('%H:%M'),
            'scan_date': _now_b.strftime('%Y-%m-%d'),
        })

    # Bear batting order: biggest fallers first within grade — mirrors LONG power-play logic
    # intra_chg is negative for shorts; sort ascending (most negative = biggest fall = first)
    grade_order = {'A+': 0, 'A': 1, 'B': 2}
    candidates.sort(key=lambda x: (
        grade_order.get(x['grade'], 3),
        x['intra_chg'],    # most negative (biggest faller) first — no negation
        -x['vol_ratio'],
        -x['score'],
    ))

    log(f"Bear scan: {len(candidates)} short candidates")

    for _rank_b, _c in enumerate(candidates):
        _reason_b = (f"Slot #{_rank_b+1} in batting order" if _rank_b < MAX_OPEN_TRADES
                     else f"Bench #{_rank_b+1} — awaiting slot")
        _hod_b = _c.get('today_hod') or _c['price']
        _pvh_b = round((_c['price'] - _hod_b) / _hod_b * 100, 2) if _hod_b else None
        try:
            log_scan_candidate(
                _c['scan_date'], _c['scan_time'], _c['symbol'], 'SHORT', regime,
                _c['price'], _c['grade'], _c['score'], _reason_b,
                _c['vol_ratio'], _c['rsi'], _c['intra_chg'], _c.get('sector'),
                is_catalyst=_c['is_catalyst'], entered=False,
                burst_age_min=_c.get('burst_age_min', 999),
                consec_new_highs=_c.get('consec_new_highs', 0),
                today_hod=_hod_b,
                price_vs_hod_pct=_pvh_b,
            )
        except Exception:
            pass

    entries       = []
    attempted     = 0   # orders submitted this cycle (incl. failures)
    open_count    = len(open_trades)
    sector_counts = get_open_sector_counts()

    for pick in candidates:
        if open_count + len(entries) + attempted >= MAX_OPEN_TRADES:
            break
        if daily_bear_count >= MAX_DAILY_BEAR_TRADES:
            break
        if pick['grade'] not in ('A+', 'A'):
            break

        sym    = pick['symbol']
        sector = get_symbol_sector(sym)

        if sector != 'OTHER' and sector_counts.get(sector, 0) >= MAX_PER_SECTOR:
            log(f"  SKIP {sym} — {sector} sector full ({MAX_PER_SECTOR} positions)")
            continue

        price    = pick['price']
        deployed = get_deployed_capital()
        capital  = get_position_capital(pick['grade'], pick.get('is_catalyst', False), deployed,
                                        pick.get('first_bar_strong', False))
        if capital <= 0:
            log(f"  Capital cap reached (${deployed:,.0f}/${TOTAL_CAPITAL:,} deployed)")
            break
        # For shorts: SL is above entry, so risk per share = sl - price
        risk_per_share = round(pick['sl'] - price, 4)
        atr_shares     = int(MAX_LOSS_PER_TRADE / risk_per_share) if risk_per_share > 0 else int(capital / price)
        shares         = max(1, min(int(capital / price), atr_shares))

        log(f"  ↓ {pick['grade']} {sym} [{sector}] ${price} | "
            f"Vol {pick['vol_ratio']:.1f}x | RSI {pick['rsi']} | R:R 1:{pick['rr']} | "
            f"SL${pick['sl']} | {', '.join(pick['reasons'][:3])}")

        attempted += 1  # count before fill outcome
        trade_id = place_trade(
            sym, price, shares, pick['sl'], pick['target'],
            'BEAR_MOMENTUM', pick['grade'],
            rsi=pick['rsi'], vol_ratio=pick['vol_ratio'],
            confidence=pick['score'], sector=sector, side='SHORT'
        )

        if trade_id:
            traded_today.add(sym)
            save_traded_today()
            open_positions[sym] = trade_id
            first_bar_strong_trades[trade_id] = pick.get('first_bar_strong', False)
            daily_bear_count  += 1
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            entries.append(pick | {'shares': shares, 'sector': sector})
            try:
                import sqlite3 as _sq3
                _scan_date = pick.get('scan_date', datetime.now(ET).strftime('%Y-%m-%d'))
                _conn = _sq3.connect('trades.db')
                _conn.execute('''UPDATE scan_log SET entered=1, entry_trade_id=?
                                 WHERE id = (
                                     SELECT id FROM scan_log
                                     WHERE symbol=? AND scan_date=? AND direction='SHORT' AND entered=0
                                     ORDER BY id DESC LIMIT 1
                                 )''',
                              (trade_id, sym, _scan_date))
                _conn.commit()
                _conn.close()
            except Exception:
                pass
            time.sleep(2)
        else:
            traded_today.add(sym)
            save_traded_today()

    exits = monitor_open_trades(regime, confirmed_scans)

    if entries:
        lines = [f"↓ BEAR TRADES {len(entries)} | Bear {daily_bear_count}/{MAX_DAILY_BEAR_TRADES}"]
        for e in entries:
            lines.append(f"  ↓{e['grade']} {e['symbol']} [{e.get('sector','?')}] x{e['shares']} @${e['price']} "
                         f"SL${e['sl']} T${e['target']} R:R 1:{e['rr']}")
        send_telegram('\n'.join(lines))

    return exits

# ─────────────────────────────────────────────────────────
# SCHEDULED TASKS
# ─────────────────────────────────────────────────────────
def get_premarket_pct(sym):
    """Returns pre-market % change vs previous close, or None if unavailable."""
    try:
        data = yf.Ticker(sym).history(period='1d', interval='1m', prepost=True)
        if data.empty:
            return None
        # tz-aware filter: only today's pre-market bars (before 9:30am ET)
        data.index = data.index.tz_convert(ET)
        today = datetime.now(ET).date()
        pm = data[(data.index.date == today) & (data.index.hour < 9) |
                  ((data.index.date == today) & (data.index.hour == 9) & (data.index.minute < 30))]
        if pm.empty:
            return None
        pm_last = float(pm['Close'].iloc[-1])
        hist = yf.Ticker(sym).history(period='5d', interval='1d')
        if len(hist) < 2:
            return None
        prev_close = float(hist['Close'].iloc[-2])
        return round((pm_last - prev_close) / prev_close * 100, 2)
    except Exception:
        return None

def premarket_early_scan():
    """4:30am ET — first look at pre-market movers. Pros scan here."""
    if date.today() in US_HOLIDAYS_2026:
        log("PRE-MARKET SCAN skipped — market holiday")
        return
    global catalyst_priority
    log("PRE-MARKET SCAN (4:30am) — identifying overnight movers...")
    scan_universe = list(dict.fromkeys(FULL_UNIVERSE))
    pm_results = []
    for sym in scan_universe:
        pct = get_premarket_pct(sym)
        if pct is not None and pct >= 2.0:
            pm_results.append((sym, pct))
    pm_results.sort(key=lambda x: -x[1])

    if pm_results:
        # Seed catalyst_priority with pre-market leaders
        pm_syms = [s for s, _ in pm_results]
        catalyst_priority = pm_syms + [s for s in catalyst_priority if s not in pm_syms]
        lines = []
        for i, (s, p) in enumerate(pm_results[:8]):
            tag = '' if s in FULL_UNIVERSE else ' 🆕'
            si_str = ''
            if i < 5:
                try:
                    info = yf.Ticker(s).info
                    si = info.get('shortPercentOfFloat')
                    if si:
                        si_str = f" | SI {si*100:.0f}%"
                except Exception:
                    pass
            lines.append(f"  {s}: +{p:.1f}% pre-mkt{tag}{si_str}")
        msg = f"🌅 Pre-market watchlist ({len(pm_results)} movers):\n" + '\n'.join(lines)
        log(msg)
        send_telegram(msg)
    else:
        log("  No significant pre-market movers (all <2%)")
        send_telegram("🌅 Pre-market: quiet — no movers >2% yet")

def _bridge_health_check():
    """
    Test that IBKR is actually serving SPY bars — not just that the bridge process is up.
    Called at 8:15am before any scanning. Auto-restarts bridge once if data is stale/empty.
    Returns True if healthy, False if restart also failed (Telegram alert sent).
    """
    def _spy_bar_count(timeout_s=6):
        try:
            r = requests.get(f"{BRIDGE}/history/SPY",
                             params={'duration': '1 D', 'bar_size': '5 mins'},
                             timeout=timeout_s)
            return len(r.json()) if r.status_code == 200 else 0
        except Exception:
            return 0

    bars = _spy_bar_count()
    if bars >= 5:
        log(f"  Bridge health: OK ({bars} SPY bars)")
        return True

    log(f"  Bridge health: SPY returned {bars} bars — restarting bridge...")
    send_telegram("⚠️ Bridge pre-flight: SPY data unavailable — auto-restarting bridge now...")
    try:
        uid = os.getuid()
        subprocess.run(
            ['launchctl', 'kickstart', '-k', f'gui/{uid}/com.sushil.trading.bridge'],
            timeout=15, check=True
        )
    except Exception as e:
        log(f"  Bridge restart failed: {e}")
        send_telegram(f"❌ Bridge restart failed: {e} — monitor manually")
        return False

    time.sleep(20)
    bars_after = _spy_bar_count(timeout_s=10)
    if bars_after >= 5:
        log(f"  Bridge restarted successfully ({bars_after} SPY bars)")
        send_telegram(f"✅ Bridge restarted — {bars_after} SPY bars. Good to go.")
        return True

    log(f"  Bridge still unhealthy after restart ({bars_after} bars) — regime will use yfinance fallback")
    send_telegram("⚠️ Bridge still slow after restart — running on yfinance fallback today. Monitor.")
    return False


def morning_catalyst_scan():
    if date.today() in US_HOLIDAYS_2026:
        log("CATALYST SCAN skipped — market holiday")
        return
    global catalyst_priority
    log("CATALYST SCAN (8:15am) — refreshing watchlist before open...")
    _bridge_health_check()

    # ── 0. Refresh pre-market (now closer to open, more accurate) ─
    premarket_lines = []
    premarket_syms  = []
    scan_universe   = list(dict.fromkeys(FULL_UNIVERSE + list(set(
        s for s in catalyst_priority if s not in FULL_UNIVERSE
    ))))
    log(f"  Pre-market refresh: checking {len(scan_universe)} stocks...")
    pm_results = []
    for sym in scan_universe:
        pct = get_premarket_pct(sym)
        if pct is not None and pct >= 2.0:
            pm_results.append((sym, pct))
    pm_results.sort(key=lambda x: -x[1])
    for sym, pct in pm_results[:10]:
        tag = '' if sym in FULL_UNIVERSE else ' 🆕'
        premarket_syms.append(sym)
        premarket_lines.append(f"  {sym}: +{pct:.1f}% pre-mkt{tag}")
    if premarket_syms:
        log(f"  Pre-market movers ({len(premarket_syms)}): {premarket_syms}")

    # ── Key levels for top 5 pre-market movers ────────────────
    key_level_lines = []
    for sym_kl, pct_kl in pm_results[:5]:
        try:
            kl_data = yf.Ticker(sym_kl).history(period='1d', interval='1m', prepost=True)
            if not kl_data.empty:
                kl_data.index = kl_data.index.tz_convert(ET)
                today_kl = datetime.now(ET).date()
                pm_bars = kl_data[
                    ((kl_data.index.date == today_kl) & (kl_data.index.hour < 9)) |
                    ((kl_data.index.date == today_kl) & (kl_data.index.hour == 9) & (kl_data.index.minute < 30))
                ]
                if not pm_bars.empty:
                    pm_high_kl = round(float(pm_bars['High'].max()), 2)
                    hist_kl = yf.Ticker(sym_kl).history(period='5d', interval='1d')
                    prior_close_kl = round(float(hist_kl['Close'].iloc[-2]), 2) if len(hist_kl) >= 2 else None
                    if sym_kl not in key_levels:
                        key_levels[sym_kl] = {}
                    key_levels[sym_kl].update({
                        'pm_high': pm_high_kl,
                        'prior_close': prior_close_kl or pm_high_kl,
                    })
                    prior_str = f" | Prev ${prior_close_kl}" if prior_close_kl else ""
                    key_level_lines.append(f"  {sym_kl}: PM high ${pm_high_kl}{prior_str} → ORB entry above ${pm_high_kl}")
        except Exception:
            pass

    # ── 1. Dynamic IBKR momentum scan ─────────────────────
    dynamic_syms  = []
    dynamic_lines = []
    try:
        r = requests.get(f"{BRIDGE}/scan/momentum", timeout=45)
        if r.status_code == 200:
            movers = r.json()
            for m in movers:
                sym = m['symbol']
                pct = m['pct_change']
                dynamic_syms.append(sym)
                tag = '' if sym in FULL_UNIVERSE else ' 🆕'
                dynamic_lines.append(f"  {sym}: +{pct}%{tag}")
            log(f"  Dynamic movers ({len(dynamic_syms)}): {dynamic_syms[:10]}")
        else:
            log(f"  Dynamic scan HTTP {r.status_code}")
    except Exception as e:
        log(f"  Dynamic scan error: {e}")

    # ── 2. Fixed catalyst scan (earnings, gap-up, volume surge) ──
    static_syms = []
    try:
        picks       = run_catalyst_scan()
        static_syms = [p['symbol'] for p in picks if isinstance(p, dict) and 'symbol' in p]
        log(f"  Static catalyst picks ({len(static_syms)}): {static_syms[:10]}")
    except Exception as e:
        log(f"  Static catalyst scan error: {e}")

    # ── 3. Merge: pre-market first → dynamic → static ────────
    # Pre-market movers get top priority (known before open = best setups)
    seen = set()
    combined = []
    for sym in premarket_syms + dynamic_syms + static_syms:
        if sym not in seen:
            seen.add(sym)
            combined.append(sym)
    catalyst_priority = combined

    log(f"Priority list ({len(catalyst_priority)}): {catalyst_priority[:12]}")

    # ── 4. Telegram alert ─────────────────────────────────
    msg_parts = []
    if premarket_syms:
        msg_parts.append(
            f"🌅 Pre-market movers ({len(premarket_syms)}):\n"
            + '\n'.join(premarket_lines[:6])
        )
    if dynamic_syms:
        new_syms = [s for s in dynamic_syms if s not in FULL_UNIVERSE]
        msg_parts.append(
            f"🔥 Momentum scan: {len(dynamic_syms)} movers"
            + (f" ({len(new_syms)} new)\n" if new_syms else "\n")
            + '\n'.join(dynamic_lines[:6])
        )
    if static_syms:
        msg_parts.append(f"⚡ Catalyst signals: {', '.join(static_syms[:6])}")
    if key_level_lines:
        msg_parts.append("📐 Key levels for top picks:\n" + '\n'.join(key_level_lines))
    if not msg_parts:
        msg_parts.append("📭 No pre-market or catalyst signals today")

    # ── 5. Pre-event macro alert (sector sweep + direction read) ──────────
    try:
        from futures.macro_calendar import classify_date as _classify
        from collections import defaultdict as _dd
        _today = date.today()
        _tomorrow = _today + timedelta(days=1)
        while _tomorrow.weekday() >= 5:
            _tomorrow += timedelta(days=1)
        _tomorrow_type = _classify(_tomorrow)
        _is_pre_event  = (_tomorrow_type == 'HIGH_IMPACT')

        if _is_pre_event:
            # Count pre-market movers by sector
            _sector_hits = _dd(list)
            for _s, _p in pm_results:
                if _p >= 5.0:
                    _sector_hits[SECTOR_MAP.get(_s, 'OTHER')].append((_s, _p))

            # Find concentrated sector
            _top_sector = max(_sector_hits, key=lambda s: len(_sector_hits[s])) if _sector_hits else None
            _top_count  = len(_sector_hits[_top_sector]) if _top_sector else 0
            _total_big  = sum(1 for _, _p in pm_results if _p >= 7.0)
            _total_med  = sum(1 for _, _p in pm_results if _p >= 5.0)

            # Direction read from gap size + coordination
            if _total_big >= 5 and _top_count >= 2:
                _pattern = "DISTRIBUTION — coordinated sector pump, no earnings"
                _action  = "→ Chasing longs into this = buying into institutional exit\n→ Wait for WEAK x3 → short opportunity instead"
                _icon    = "⚠️"
            elif _total_med >= 4 and _top_count >= 2:
                _pattern = "POSSIBLE DISTRIBUTION — moderate breadth, watch for fade"
                _action  = "→ Be selective on longs, tighten stops\n→ Monitor regime closely in first 30 min"
                _icon    = "ℹ️"
            elif _total_big <= 2:
                _pattern = "QUIET GRIND — low breadth, likely accumulation"
                _action  = "→ Organic moves tend to hold\n→ Standard entry rules apply"
                _icon    = "✅"
            else:
                _pattern = "MIXED — check regime at open"
                _action  = "→ Tighten stops on existing positions\n→ Wait for regime to settle post-IB"
                _icon    = "ℹ️"

            _sector_lines = []
            for _sec, _syms in sorted(_sector_hits.items(), key=lambda x: -len(x[1]))[:4]:
                _names = ', '.join(f"{_sy}+{_pc:.0f}%" for _sy, _pc in sorted(_syms, key=lambda x: -x[1])[:3])
                _sector_lines.append(f"  {_sec}: {len(_syms)} stocks ({_names})")

            _alert = (
                f"{_icon} PRE-{_tomorrow_type.replace('_',' ')} DAY ({_tomorrow.strftime('%b %d')})\n"
                f"Pre-mkt breadth: {_total_big} stocks >7%  |  {_total_med} stocks >5%\n"
            )
            if _sector_lines:
                _alert += "Sector concentration:\n" + '\n'.join(_sector_lines) + "\n"
            _alert += (
                f"Pattern: {_pattern}\n"
                f"{_action}\n\n"
                f"Commands:  PAUSE LONGS  |  WATCH  |  RESUME"
            )
            send_telegram(_alert)
            log(f"Pre-event alert sent: {_tomorrow} ({_total_big} stocks >7%, pattern={_pattern[:20]})")
    except Exception as _e:
        log(f"Pre-event alert error: {_e}")

    send_telegram('\n\n'.join(msg_parts))

def morning_voice_summary():
    if date.today() in US_HOLIDAYS_2026:
        return
    log("Morning voice summary")
    try:
        r       = requests.get(f"{BRIDGE}/account", timeout=10)
        account = r.json()
        pnl     = account.get('UnrealizedPnL', 0) or 0
        net_liq = account.get('NetLiquidation', 0) or 0
        buying  = account.get('BuyingPower', 0) or 0
        wr_30d  = get_win_rate(days=30)
        positions = get_ibkr_positions()
        pnl_word  = "up" if pnl >= 0 else "down"
        speak(
            f"Good morning! Auto trader active. "
            f"Account value {net_liq:,.0f} dollars. "
            f"You are {pnl_word} {abs(pnl):,.0f} dollars unrealized. "
            f"Buying power {buying:,.0f} dollars. "
            f"Holding {len(positions)} positions. "
            f"30 day win rate is {wr_30d:.0f} percent. "
            f"Good luck today!"
        )
    except Exception as e:
        log(f"Voice summary error: {e}")

def evening_summary():
    if date.today() in US_HOLIDAYS_2026:
        log("Evening summary skipped — market holiday")
        return
    log("Evening summary")
    daily      = get_daily_pnl()
    wr_30      = get_win_rate(days=30)
    wr_day     = (daily['wins'] / daily['trades'] * 100) if daily['trades'] > 0 else 0
    emoji      = '✅' if daily['pnl'] > 0 else '❌'
    open_trades = get_open_trades()
    deployed   = get_deployed_capital()
    hold_msg   = f"\n📦 {len(open_trades)} positions held overnight (${deployed:,.0f} deployed)" if open_trades else ""
    send_telegram(
        f"{emoji} EOD Summary\n"
        f"Trades: {daily['trades']} | Wins: {daily['wins']} | WR: {wr_day:.0f}%\n"
        f"P&L today: ${daily['pnl']:+.2f}\n"
        f"30d win rate: {wr_30:.0f}%"
        f"{hold_msg}"
    )

def nightly_learning():
    if date.today() in US_HOLIDAYS_2026:
        log("Nightly learning skipped — market holiday")
        return
    log("NIGHTLY LEARNING — analysing trades...")
    try:
        run_learning_cycle()
        w = get_strategy_weights()
        msg = (
            f"🧠 Nightly learning complete\n"
            f"RSI: {w['rsi']:.1f}x | Vol: {w['volume']:.1f}x | "
            f"Momentum: {w['momentum']:.1f}x | Sector: {w['sector']:.1f}x"
        )
        send_telegram(msg)
        log("Learning complete.")
    except Exception as e:
        log(f"Learning error: {e}")

    # Enrich today's scan_log with actual day outcomes
    try:
        enriched = enrich_scan_log()
        if enriched:
            log(f"Scan log enriched: {enriched} candidates updated with actual day performance")
    except Exception as e:
        log(f"Scan log enrichment error: {e}")

    # Options side — runs independently after equity learner
    try:
        run_options_learning_cycle()
    except Exception as e:
        log(f"Options learning error: {e}")

def chart_gate_weekly_review():
    """Every Friday 4:30pm — parse CHART GATE LOG lines, cross-ref DB outcomes,
    send Telegram checkpoint so we don't forget to evaluate the gate."""
    if date.today() in US_HOLIDAYS_2026:
        return
    try:
        log_file = os.path.join(_DIR, 'logs', 'auto_trader.log')
        if not os.path.exists(log_file):
            return

        # Parse all CHART GATE LOG lines from the log file
        yes_trades, no_trades = [], []
        with open(log_file, 'r') as f:
            for line in f:
                if '[CHART GATE LOG]' not in line or '1h aligned:' not in line:
                    continue
                try:
                    # Format: [CHART GATE LOG] TSLA | 1h aligned: ✅/❌ | <reason>
                    parts  = line.split('[CHART GATE LOG]')[1].strip()
                    sym    = parts.split('|')[0].strip()
                    aligned = '✅' in parts
                    reason  = parts.split('|')[2].strip() if '|' in parts else ''
                    (yes_trades if aligned else no_trades).append((sym, reason))
                except Exception:
                    continue

        total = len(yes_trades) + len(no_trades)
        if total == 0:
            send_telegram("📊 Chart Gate Weekly Review\nNo gate log entries yet — no bull entries this week had chart checks.")
            return

        # Cross-ref NO trades with DB outcomes
        conn   = __import__('sqlite3').connect(os.path.join(_DIR, 'trades.db'))
        c      = conn.cursor()
        no_outcomes = []
        for sym, reason in no_trades:
            c.execute('''SELECT status, pnl FROM trades
                         WHERE symbol=? AND setup_type NOT IN ('MANUAL','RECONCILED')
                         AND status IN ('WIN','LOSS')
                         ORDER BY id DESC LIMIT 1''', (sym,))
            row = c.fetchone()
            outcome = f"{row[0]} ${row[1]:+.2f}" if row else "open/unknown"
            no_outcomes.append(f"  ❌ {sym}: {outcome} — {reason[:60]}")
        conn.close()

        no_loss_count = sum(1 for s in no_outcomes if 'LOSS' in s)
        lines = [
            f"📊 Chart Gate Weekly Review | {datetime.now(ET).strftime('%b %d')}",
            f"Total gate checks: {total} | YES: {len(yes_trades)} | NO: {len(no_trades)}",
            f"",
            f"NO calls ({len(no_trades)}) — were they right to flag?",
        ] + (no_outcomes if no_outcomes else ["  None this week"]) + [
            f"",
            f"NO calls that were losses: {no_loss_count}/{len(no_trades)}",
            f"",
            f"{'⚠️ Sample too small — extend review 1 more week.' if total < 25 else '✅ Enough data — consider activating gate if NO→LOSS rate >60%.'}",
            f"Reply GATE ON to activate or ignore to extend."
        ]
        send_telegram('\n'.join(lines))
        log(f"Chart gate weekly review sent: {total} checks, {len(no_trades)} NO calls")
    except Exception as e:
        log(f"Chart gate review error: {e}")

def reset_daily_state():
    """Midnight reset — clears per-day counters so next session starts clean."""
    global traded_today, daily_bull_count, daily_bear_count, pm_scan_done
    global atr_cache, regime_history, spy_open_price, trade_entry_times, earnings_cache
    global partial_done_trades, first_bar_strong_trades, key_levels, sector_strength
    global daily_sympathy_count, active_sympathy_triggers, sympathy_scan_done
    global peak_session_pnl, pl_protect_active, _morning_pnl_snap, _daily_loss_alerted
    traded_today             = set()
    daily_bull_count         = 0
    daily_bear_count         = 0
    daily_sympathy_count     = 0
    active_sympathy_triggers = {}
    sympathy_scan_done       = False
    pm_scan_done             = False
    atr_cache          = {}
    regime_history     = []
    spy_open_price     = None
    trade_entry_times   = {}
    earnings_cache      = {}
    partial_done_trades     = {}
    first_bar_strong_trades = {}
    key_levels              = {}
    sector_strength     = {}
    peak_session_pnl    = 0.0
    pl_protect_active   = False
    _morning_pnl_snap   = None
    _daily_loss_alerted = False
    save_traded_today()
    log("Daily state reset for new trading day")

# ─────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────
if __name__ == '__main__':
    # Singleton guard — prevent two instances running simultaneously during launchctl restart.
    # The lock is held for the lifetime of the process and released automatically on exit.
    import fcntl as _fcntl
    _LOCK_PATH = os.path.join(_DIR, 'auto_trader.lock')
    _lockfd = open(_LOCK_PATH, 'w')
    try:
        _fcntl.flock(_lockfd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    except BlockingIOError:
        print("ERROR: Another auto_trader instance is already running — exiting to prevent duplicate orders.")
        sys.exit(1)

    if os.getenv('TRADING_MODE', 'paper') == 'live':
        if os.getenv('PROD_EQUITY_ENABLED', 'false').lower() != 'true':
            log("PROD_EQUITY_ENABLED is not 'true' in .env — exiting. Set it to enable live equity trading.")
            sys.exit(0)

    init_db()
    traded_today = load_traded_today()
    if traded_today:
        log(f"Restored traded_today: {len(traded_today)} symbols")
    counts = get_today_entry_counts()
    daily_bull_count = counts['bull']
    daily_bear_count = counts['bear']
    if daily_bull_count or daily_bear_count:
        log(f"Restored daily counts: Bull {daily_bull_count} Bear {daily_bear_count}")

    # Restore sympathy count separately (not in get_today_entry_counts)
    import sqlite3 as _sqlite3
    _sc_conn = _sqlite3.connect(os.path.join(_DIR, 'trades.db'))
    daily_sympathy_count = (_sc_conn.execute(
        "SELECT COUNT(*) FROM trades WHERE entry_date=date('now') "
        "AND status IN ('OPEN','WIN','LOSS') AND setup_type='SYMPATHY'"
    ).fetchone() or [0])[0]
    _sc_conn.close()
    if daily_sympathy_count:
        log(f"Restored daily_sympathy_count: {daily_sympathy_count}")

    # Fix D: if restarting after pre-market window (9:20–9:29am), mark it done
    _now_et = datetime.now(ET)
    if not (_now_et.hour == 9 and 20 <= _now_et.minute <= 29):
        pm_scan_done = True   # window already closed or not yet open — won't re-fire pre-mkt scan

    # Restore trade_entry_times, partial_done_trades, and session_high/session_low for open trades
    _conn = _sqlite3.connect(os.path.join(_DIR, 'trades.db'))
    _rows = _conn.execute(
        "SELECT id, entry_date, entry_time, partial_exited, symbol, side FROM trades WHERE status='OPEN'"
    ).fetchall()
    _conn.close()
    for _tid, _edate, _etime, _partial, _sym, _side in _rows:
        try:
            _dt_str = f"{_edate} {_etime}" if _etime else f"{_edate} 09:30:00"
            _naive  = datetime.strptime(_dt_str, '%Y-%m-%d %H:%M:%S')
            trade_entry_times[_tid] = ET.localize(_naive)
        except Exception:
            pass
        if _partial:
            partial_done_trades[_tid] = 0.0  # locked amount unknown after restart; key presence blocks re-trigger

        # Fix B: seed session_high/session_low from today's actual OHLC so trailing
        # stops have the correct reference point on restart (not just current price).
        try:
            _bars = yf.Ticker(_sym).history(period='1d', interval='1m')
            if not _bars.empty:
                _today_bars = _bars[_bars.index.date == datetime.now(ET).date()]
                if not _today_bars.empty:
                    if (_side or 'LONG') == 'LONG':
                        session_high[_tid] = round(float(_today_bars['High'].max()), 2)
                    else:
                        session_low[_tid]  = round(float(_today_bars['Low'].min()), 2)
        except Exception:
            pass  # falls back to lazy init on first monitor cycle — acceptable

    if trade_entry_times:
        log(f"Restored entry times for {len(trade_entry_times)} open trades")
    if partial_done_trades:
        log(f"Restored partial_done_trades: {len(partial_done_trades)} trades already partially exited")
    if session_high or session_low:
        log(f"Seeded session_high/low for {len(session_high) + len(session_low)} open positions")

    # Restore peak_session_pnl and afternoon gate from today's realized trades + current unrealized.
    # Without this, a mid-day restart resets peak to 0, breaking P&L protection and the afternoon gate.
    try:
        import sqlite3 as _sqlite3
        _pr_conn = _sqlite3.connect(os.path.join(_DIR, 'trades.db'))
        _realized_snap = (_pr_conn.execute(
            "SELECT COALESCE(SUM(pnl),0) FROM trades "
            "WHERE entry_date=date('now') AND status IN ('WIN','LOSS','CLOSED') AND setup_type!='RECONCILED'"
        ).fetchone() or [0])[0] or 0.0
        _pr_conn.close()
        try:
            _pf_snap = requests.get(f"{BRIDGE}/portfolio", timeout=5).json()
            _unreal_snap = sum((p.get('unrealizedPnL') or 0) for p in _pf_snap if (p.get('qty') or 0) != 0)
        except Exception:
            _unreal_snap = 0.0
        _restored_peak = float(_realized_snap) + float(_unreal_snap)
        if _restored_peak > peak_session_pnl:
            peak_session_pnl = _restored_peak
            log(f"Restored peak_session_pnl: ${peak_session_pnl:.0f} "
                f"(realized ${_realized_snap:.0f} + unrealized ${_unreal_snap:.0f})")
        # Seed afternoon gate if restarting after noon
        _now_et_r = datetime.now(ET)
        if _now_et_r.hour >= AFTERNOON_GATE_HOUR and _morning_pnl_snap is None and peak_session_pnl > 0:
            _morning_pnl_snap = peak_session_pnl
            log(f"Restored _morning_pnl_snap: ${_morning_pnl_snap:.0f} (post-noon restart — gate active)")
    except Exception as _pe:
        log(f"Warning: could not restore peak_session_pnl: {_pe}")

    print("\n⚡ TriVega Equity — Auto Trader")
    print("=" * 55)
    print(f"Scan interval:    Every {SCAN_INTERVAL//60} min")
    print(f"Max open trades:  {MAX_OPEN_TRADES}")
    print(f"Max daily trades: Bull {MAX_DAILY_BULL_TRADES} / Bear {MAX_DAILY_BEAR_TRADES} (independent)")
    print(f"Total capital:    ${TOTAL_CAPITAL:,} (dynamic per position)")
    print(f"Stop method:      5% fixed | partial exit 50% at 1R (+5%) | trail rest")
    print(f"Trail method:     ATR × {ATR_TRAIL_MULT} + 5m bar low at 3%+ profit")
    print(f"Exit signals:     VWAP cross ↓ | ATR fade | EOD 3:45pm conviction | circuit breaker")
    print(f"No fixed target:  ride winners until signal fires — no capped % exits")
    print(f"EOD close:        {EOD_CLOSE_HOUR}:{EOD_CLOSE_MINUTE:02d} ET — close unless profit>1.5% AND above VWAP")
    print(f"Circuit breaker:  ${MAX_LOSS_PER_TRADE}/trade | ${MAX_DAILY_LOSS}/day loss | +${DAILY_PROFIT_TARGET}/day profit")
    print(f"Max hold:         {MAX_HOLD_DAYS} business day backstop (EOD close fires first)")
    print(f"Min today gain:   {MIN_TODAY_GAIN}% (only enter stocks moving today)")
    print(f"Lunch avoid:      {LUNCH_AVOID_START[0]}:{LUNCH_AVOID_START[1]:02d}–{LUNCH_AVOID_END[0]}:{LUNCH_AVOID_END[1]:02d} ET (no entries during chop)")
    print(f"Entry patterns:   ORB | VWAP reclaim | Bull flag | HOD break | RS vs SPY")
    print(f"Entry cutoff:     {NO_ENTRY_AFTER}:00 ET | Min R:R 1:{MIN_RR}")
    print("=" * 55)
    print("Scheduled: pre-mkt 4:30am | catalyst 8:15am | voice 9am | EOD 4:30pm | learning 11pm | reset midnight")
    print("Telegram:  HELP | STATUS | REGIME | TODAY | BUY <SYM> [%] | SELL <SYM> | CLOSEALL | PAUSE | RESUME | BLOCK <SYM>")
    print("Press CTRL+C to stop\n")

    # Background scheduler for timed tasks
    sched = BackgroundScheduler(timezone=ET)
    sched.add_job(premarket_early_scan,   'cron',     day_of_week='mon-fri', hour=4,  minute=30)
    sched.add_job(morning_catalyst_scan,  'cron',     day_of_week='mon-fri', hour=8,  minute=15)
    sched.add_job(morning_voice_summary,  'cron',     day_of_week='mon-fri', hour=9,  minute=0)
    sched.add_job(evening_summary,        'cron',     day_of_week='mon-fri', hour=16, minute=30)
    sched.add_job(chart_gate_weekly_review, 'cron',   day_of_week='fri',     hour=16, minute=35)
    sched.add_job(nightly_learning,       'cron',     day_of_week='mon-fri', hour=23, minute=0)
    sched.add_job(reset_daily_state,      'cron',                            hour=0,  minute=1)
    sched.add_job(poll_telegram_commands, 'interval', seconds=15)
    sched.start()

    send_telegram(f"⚡ TriVega Equity · Online | ${TOTAL_CAPITAL:,} cap | max {MAX_OPEN_TRADES} positions")

    _last_full_scan = 0.0

    while True:
        try:
            now_ts = time.time()
            if is_market_open():
                if now_ts - _last_full_scan >= SCAN_INTERVAL:
                    run_scan()
                    _last_full_scan = time.time()
                else:
                    fast_monitor_positions()
                time.sleep(MONITOR_INTERVAL)
            else:
                now = datetime.now(ET)
                if now.hour >= 16:
                    daily = get_daily_pnl()
                    log(f"Market closed. Bull: {daily_bull_count} Bear: {daily_bear_count} | P&L ${daily['pnl']:+.2f} | sleeping until tomorrow...")
                elif now.hour < 9 or (now.hour == 9 and now.minute < 31):
                    log("Pre-market — waiting for open...")
                time.sleep(SCAN_INTERVAL)
        except KeyboardInterrupt:
            daily = get_daily_pnl()
            send_telegram(f"⚡ TriVega Equity · Stopped\nBull: {daily_bull_count} Bear: {daily_bear_count} | P&L: ${daily['pnl']:+.2f}")
            sched.shutdown()
            break
        except Exception as e:
            log(f"Scan error: {e}")
            time.sleep(30)
