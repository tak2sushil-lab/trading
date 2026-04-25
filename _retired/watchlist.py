# watchlist.py — complete stock universe
# Updated with blue chips + full catalyst scanning universe

# ── Tech priority stocks ──────────────────────────────────
AI_CHIPS = [
    'NVDA', 'AMD', 'CRWV', 'NBIS', 'SMCI',
    'AVGO', 'COHR', 'LITE', 'ON', 'CLS'
]

CLOUD_SOFTWARE = [
    'SNOW', 'CRM', 'ORCL', 'PANW', 'CRWD',
    'PLTR', 'RBRK', 'S', 'PATH', 'AI'
]

TECH_PRIORITY = AI_CHIPS + CLOUD_SOFTWARE

# ── BLUE CHIP CORE — always screened ─────────────────────
# Highest liquidity, reliable momentum on strong days
BLUE_CHIPS = [
    # Mega cap tech
    'AAPL',   # Apple
    'MSFT',   # Microsoft — proven +5% mover on strong days
    'AMZN',   # Amazon
    'GOOGL',  # Alphabet
    'META',   # Meta — AI + social momentum
    'TSLA',   # Tesla — high beta, big moves
    'NVDA',   # Nvidia — AI leader
    'AMD',    # AMD — semi leader

    # Financial blue chips — move on earnings season
    'JPM',    # JPMorgan — earnings catalyst
    'GS',     # Goldman Sachs
    'MS',     # Morgan Stanley

    # Other large cap momentum
    'NFLX',   # Netflix — content catalyst
    'UBER',   # Uber — AV/robotaxi catalyst
    'AVGO',   # Broadcom — AI chip
]

# ── PROVEN PERFORMERS — v6 backtest winners (57%+ win rate) ──
PROVEN_STOCKS = [
    # Tier 1: 70%+ win rate
    'AAPL',   # 100% win
    'PLTR',   # 86% win  - AI tech
    'COHR',   # 82% win  - SEMI
    'IONQ',   # 80% win  - quantum ← v5 new
    'HOOD',   # 75% win  - fintech
    'JPM',    # 75% win  - financial ← v5 new
    'IREN',   # 75% win  - crypto miner ← v5 new
    'NUTX',   # 73% win  - biotech
    'LITE',   # 67% win  - SEMI
    'VST',    # 67% win  - energy
    'ITA',    # 67% win  - defence
    'NFLX',   # 67% win  - consumer ← v5 new
    # Tier 2: 60-66% win rate
    'ORCL',   # 64% win  - cloud
    'OKLO',   # 63% win  - nuclear ← v5 new (27 trades)
    'AMZN',   # 62% win  - mega cap ← v5 new
    'GOOGL',  # 62% win  - mega cap ← v5 new
    'CRM',    # 62% win  - cloud ← v5 new
    'QBTS',   # 60% win  - quantum ← v5 new
    # Tier 3: 55-59% win rate
    'TOST',   # 57% win  - fintech
    'AVGO',   # 56% win  - SEMI
    'NBIS',   # 55% win  - AI tech
    'CLS',    # 54% win  - SEMI
    'RKLB',   # 54% win  - space
]

# ── CONFIRMED UNDERPERFORMERS — do not trade ──────────────
# NVDA 0%, SMR 24%, MSTR 17%, SNOW 33%, CRWD 33%, TSLA 33%
# UBER 33%, JOBY 38%, PANW 43%, MS 43%, AFRM 43%, ACHR 44%
# SOFI 50% (negative avg), HPE 50% (negative avg)

# ── FINAL DAILY UNIVERSE — screened every morning ─────────
FINAL_UNIVERSE = list(dict.fromkeys(
    PROVEN_STOCKS + ['MSFT', 'META', 'AMD', 'CNQ', 'RKT']
))

# ── CATALYST UNIVERSE — scanned for events/news ───────────
# ALL stocks monitored for catalysts
# If catalyst found → temporarily added to today's screen
CATALYST_UNIVERSE = [
    # From your original watchlist — all US tradeable stocks
    # Mid cap and small cap included here for catalyst plays

    # AI / Tech
    'NVDA', 'AMD', 'MSFT', 'AAPL', 'AVGO', 'ORCL',
    'PLTR', 'NBIS', 'CRWV', 'SMCI', 'CLS', 'COHR',
    'LITE', 'PANW', 'CRWD', 'SNOW', 'CRM', 'RBRK',
    'PATH', 'AI', 'IONQ', 'QBTS', 'RGTI',

    # Fintech
    'HOOD', 'SOFI', 'AFRM', 'TOST', 'NU', 'RKT',

    # Defence / Space
    'ITA', 'RKLB', 'USAR', 'RDW', 'UMAC',

    # Energy
    'VST', 'CNQ', 'CVE', 'ENB', 'FSLR', 'NEE',

    # Nuclear
    'SMR', 'OKLO', 'CCJ', 'UUUU', 'DNN', 'URA',

    # Biotech
    'LLY', 'NTLA', 'BEAM', 'TMDX', 'NPCE', 'NUTX',

    # Crypto related
    'MSTR', 'IREN', 'APLD', 'HIVE',

    # Mega cap
    'AMZN', 'TSLA', 'META', 'GOOGL', 'NFLX', 'UBER',
    'JPM', 'GS', 'MS',

    # Mid cap momentum
    'HOOD', 'HPE', 'HBM', 'PONY', 'UMAC', 'SERV',
    'APLD', 'BMNR', 'IREN', 'DNA', 'SOUN', 'BBAI',
    'ACHR', 'JOBY', 'RDW', 'ONDS',

    # Gap-and-go confirmed (5Y backtest)
    'ON', 'LRCX', 'DDOG', 'MDB',

    # Photonics / optical semiconductors — POET momentum
    'POET', 'VIAV', 'IIVI',

    # Semiconductor mid-cap — INDI
    'INDI', 'WOLF', 'ALGM',
]

# Remove duplicates
CATALYST_UNIVERSE = list(dict.fromkeys(CATALYST_UNIVERSE))

# ── Sector mapping ────────────────────────────────────────
SECTORS = {
    'AI_TECH':    ['NVDA', 'AMD', 'PLTR', 'CRWV', 'NBIS', 'SMCI', 'PATH', 'AI'],
    'MEGA_TECH':  ['MSFT', 'AAPL', 'AMZN', 'GOOGL', 'META', 'TSLA'],
    'SEMI':       ['AVGO', 'COHR', 'LITE', 'ON', 'CLS', 'NVDA', 'AMD'],
    'CLOUD':      ['SNOW', 'CRM', 'ORCL', 'RBRK', 'PANW', 'CRWD', 'S'],
    'DEFENCE':    ['ITA', 'USAR', 'RKLB', 'RDW', 'UMAC'],
    'ENERGY':     ['CNQ', 'CVE', 'ENB', 'VST', 'FSLR', 'NEE', 'EOSE'],
    'NUCLEAR':    ['SMR', 'OKLO', 'CCJ', 'UUUU', 'DNN', 'URA'],
    'QUANTUM':    ['IONQ', 'QBTS', 'RGTI'],
    'BIOTECH':    ['LLY', 'NTLA', 'BEAM', 'TMDX', 'NPCE', 'NUTX'],
    'FINTECH':    ['HOOD', 'SOFI', 'AFRM', 'TOST', 'NU', 'RKT'],
    'CRYPTO':     ['MSTR', 'IREN', 'APLD', 'HIVE', 'BMNR'],
    'SPACE':      ['RKLB', 'JOBY', 'ACHR', 'PONY'],
    'MINING':     ['HBM', 'LAC', 'TGB', 'NVA'],
    'CONSUMER':   ['AAPL', 'AMZN', 'TSLA', 'SHOP', 'NFLX'],
    'FINANCIAL':  ['JPM', 'GS', 'MS'],
    'MOBILITY':   ['UBER', 'JOBY', 'ACHR', 'PONY'],
}

# ── Macro themes ──────────────────────────────────────────
MACRO_THEMES = {
    'ai_boom':       ['NVDA', 'AMD', 'MSFT', 'PLTR', 'CRWV', 'NBIS', 'META'],
    'defence_war':   ['ITA', 'USAR', 'RKLB', 'RDW', 'UMAC'],
    'energy_crisis': ['CNQ', 'CVE', 'ENB', 'VST', 'FSLR', 'NEE'],
    'nuclear_power': ['SMR', 'OKLO', 'CCJ', 'UUUU', 'DNN', 'URA'],
    'crypto_rally':  ['MSTR', 'IREN', 'APLD', 'HIVE'],
    'earnings_season':['JPM', 'GS', 'MS', 'NFLX', 'AAPL', 'MSFT'],
}

# ── Catalyst type definitions ─────────────────────────────
CATALYST_TYPES = {
    'earnings_beat':   'Earnings beat — stock up 5%+ after report',
    'gap_up':          'Gap up 3%+ at open — institutional buying',
    'volume_surge':    'Volume 5x+ normal — unusual activity',
    'analyst_upgrade': 'Major analyst upgrade with price target raise',
    'news_catalyst':   'Breaking news — contract, FDA, partnership',
    'sector_rotation': 'Sector momentum — peers all moving same direction',
}

if __name__ == '__main__':
    print(f"Universe Summary:")
    print(f"  Blue Chips:        {len(BLUE_CHIPS)} stocks (always screened)")
    print(f"  Proven Stocks:     {len(PROVEN_STOCKS)} stocks (backtest winners)")
    print(f"  Final Universe:    {len(FINAL_UNIVERSE)} stocks (daily screen)")
    print(f"  Catalyst Universe: {len(CATALYST_UNIVERSE)} stocks (event scanning)")
    print(f"\nFinal Universe (daily screen):")
    for s in FINAL_UNIVERSE:
        tag = '⭐' if s in PROVEN_STOCKS else ('🔵' if s in BLUE_CHIPS else '')
        print(f"  {tag} {s}")
