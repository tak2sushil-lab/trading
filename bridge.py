# bridge.py — trading server + Claude API + WhatsApp via Twilio
# Reads all credentials from .env file
# Command: python bridge.py

from dotenv import load_dotenv
load_dotenv()  # loads .env file from same folder

import os
from ib_async import IB, Stock, Index, Option, Contract, ComboLeg, MarketOrder, LimitOrder, ScannerSubscription, WshEventData
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import asyncio
import math
import re
import anthropic
import json
import requests as http_requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

# ── Load credentials from .env ────────────────────────────
ANTHROPIC_KEY    = os.getenv("ANTHROPIC_KEY")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
IBKR_HOST        = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT        = int(os.getenv("IBKR_PORT", "4002"))
IBKR_ACCOUNT     = os.getenv("IBKR_ACCOUNT", "")   # must be set in prod .env — Individual account only
BRIDGE_PORT      = int(os.getenv("BRIDGE_PORT", "8000"))
TG_API           = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ── Clients ───────────────────────────────────────────────
app = FastAPI(title="IBKR Trading Bridge")
ib  = IB()
ai  = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ── Historical data cache ─────────────────────────────────
# key: "SYMBOL:duration:bar_size"  value: {'ts': datetime, 'bars': [...]}
_hist_cache = {}
_CACHE_TTL  = {
    '1 day':  86400,   # daily bars → refresh once per day
    '1 week':  86400,
    '5 mins':   300,   # intraday → refresh every 5 min
    '1 min':     60,
}

# ── IV Rank cache ──────────────────────────────────────────
# key: symbol  value: {'ts': datetime, 'result': dict}
# IBKR reqHistoricalDataAsync for 1Y of daily IV takes 30-60s under pacing.
# Cache for 4 hours — IV rank doesn't change meaningfully intraday.
_iv_rank_cache: dict = {}
_IV_RANK_CACHE_TTL = 4 * 3600  # 4 hours

# Allow browser to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Connect to IB Gateway ────────────────────────────────
async def _connect_ibkr() -> bool:
    """Try each clientId until one connects. Returns True on success."""
    for cid in range(10, 20):
        try:
            await ib.connectAsync(host=IBKR_HOST, port=IBKR_PORT, clientId=cid)
            # reqAccountUpdates populates ib.portfolio() with prices + P&L
            ib.reqAccountUpdates(True, '')
            await asyncio.sleep(3)
            # Subscribe streaming market data for any held positions so portfolio
            # prices stay live (reqAccountUpdates alone doesn't guarantee price pushes)
            for pos in ib.positions():
                if pos.position != 0 and pos.contract.secType == 'STK':
                    try:
                        await ib.qualifyContractsAsync(pos.contract)
                        ib.reqMktData(pos.contract, '', False, False)
                    except Exception:
                        pass
            return True
        except Exception:
            continue
    return False

async def _reconnect_loop():
    """Background task: re-connects whenever IBKR drops the link."""
    await asyncio.sleep(15)          # give startup a head-start
    _disconnect_count = 0            # consecutive 30s cycles without connection
    while True:
        await asyncio.sleep(30)
        if not ib.isConnected():
            _hist_cache.clear()      # stale prices must not be served after reconnect
            _iv_rank_cache.clear()
            _disconnect_count += 1
            print("[RECONNECT] IBKR disconnected — flushing cache and reconnecting...")
            if _disconnect_count == 1:
                send_telegram_alert("⚠️ IBKR Gateway disconnected — auto-reconnecting")
            elif _disconnect_count == 6:   # 3 min of failed attempts
                send_telegram_alert("🚨 IBKR reconnect failing (3min) — check Gateway/IBC immediately")
            ok = await _connect_ibkr()
            if ok:
                print("[RECONNECT] ✅ Reconnected to IB Gateway")
                send_telegram_alert("✅ IBKR Gateway reconnected")
                _disconnect_count = 0
            else:
                print("[RECONNECT] ⚠️  Still not connected — will retry in 30s")
        else:
            _disconnect_count = 0    # reset on each healthy check

@app.on_event("startup")
async def startup():
    ok = await _connect_ibkr()
    mode = 'paper' if IBKR_PORT == 4002 else 'LIVE'
    if ok:
        print(f"✅ Connected to IB Gateway ({mode} trading)")
    else:
        print(f"⚠️  Could not connect to IB Gateway ({mode}) — will retry automatically")
    print("✅ Telegram alerts ready (no tunnel needed)")
    asyncio.create_task(_reconnect_loop())

# ── Helper: clean float values ────────────────────────────
def clean(value):
    if value is None:
        return None
    try:
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 2)
    except:
        return None

# ── Health check ──────────────────────────────────────────
@app.get("/")
async def health():
    return {
        "status":    "running",
        "connected": ib.isConnected(),
        "account":   ib.wrapper.accounts[0] if ib.isConnected() else "none",
        "mode":      "paper" if IBKR_PORT == 4002 else "LIVE"
    }

@app.get("/connected")
async def connected():
    return {"connected": ib.isConnected()}

# ── Get live quote ────────────────────────────────────────
@app.get("/quote/{symbol}")
async def get_quote(symbol: str):
    contract = Stock(symbol.upper(), 'SMART', 'USD')
    await ib.qualifyContractsAsync(contract)
    ticker = ib.reqMktData(contract, snapshot=True)
    await asyncio.sleep(3)

    bid   = clean(ticker.bid)
    ask   = clean(ticker.ask)
    last  = clean(ticker.last)
    close = clean(ticker.close)
    best_price = last or bid or ask or close

    return {
        "symbol":     symbol.upper(),
        "last":       last,
        "bid":        bid,
        "ask":        ask,
        "close":      close,
        "best_price": best_price,
        "note":       "live price" if last else "delayed/close price — market closed or no data subscription"
    }

# ── Get portfolio positions ───────────────────────────────
@app.get("/portfolio")
async def get_portfolio():
    # ib.portfolio() has live prices via reqAccountUpdates; prefer it.
    # ib.positions() is more reliable for qty/cost but has no price data.
    # Merge: use portfolio() prices when available, positions() as qty fallback.
    pf_items  = ib.portfolio()
    price_map = {p.contract.symbol: p for p in pf_items}

    positions = ib.positions()
    if not positions:
        ib.reqPositions()
        await asyncio.sleep(2)
        positions = ib.positions()

    if positions:
        result = []
        for p in positions:
            if p.position == 0 or p.contract.secType != 'STK':
                continue
            sym = p.contract.symbol
            pf  = price_map.get(sym)
            result.append({
                "account":       p.account,
                "symbol":        sym,
                "qty":           p.position,
                "avgCost":       clean(p.avgCost),
                "marketPrice":   clean(pf.marketPrice)   if pf else None,
                "marketValue":   clean(pf.marketValue)   if pf else None,
                "unrealizedPnL": clean(pf.unrealizedPNL) if pf else None,
                "realizedPnL":   clean(pf.realizedPNL)   if pf else None,
            })
        return result

    # Last resort: portfolio cache only (no positions() data)
    return [{
        "symbol":        p.contract.symbol,
        "qty":           p.position,
        "avgCost":       clean(p.averageCost),
        "marketPrice":   clean(p.marketPrice),
        "marketValue":   clean(p.marketValue),
        "unrealizedPnL": clean(p.unrealizedPNL),
        "realizedPnL":   clean(p.realizedPNL),
    } for p in pf_items if p.position != 0 and p.contract.secType == 'STK']

# ── Get account summary ───────────────────────────────────
@app.get("/account")
async def get_account():
    summary = await ib.accountSummaryAsync()
    result  = {}
    for item in summary:
        # Filter to configured account only — avoids mixing TFSA and Individual balances
        if IBKR_ACCOUNT and item.account != IBKR_ACCOUNT:
            continue
        if item.tag in ['NetLiquidation', 'TotalCashValue',
                        'BuyingPower', 'UnrealizedPnL']:
            try:
                result[item.tag] = round(float(item.value), 2)
            except:
                result[item.tag] = None
    return result

# ── Historical OHLCV data ────────────────────────────────
@app.get("/history/{symbol}")
async def get_history(
    symbol:   str,
    duration: str = Query(default="60 D",  description="e.g. '60 D', '1 Y', '2 Y'"),
    bar_size: str = Query(default="1 day", description="e.g. '1 day', '5 mins', '1 min'"),
    rth:      bool = Query(default=True,   description="Regular trading hours only"),
):
    sym = symbol.upper()
    cache_key = f"{sym}:{duration}:{bar_size}"
    ttl = _CACHE_TTL.get(bar_size, 300)

    # Return cached data if still fresh
    if cache_key in _hist_cache:
        age = (datetime.utcnow() - _hist_cache[cache_key]['ts']).total_seconds()
        if age < ttl:
            return _hist_cache[cache_key]['bars']

    # VIX is a CBOE index — requires Index contract, not Stock
    if sym == 'VIX':
        contract = Index('VIX', 'CBOE', 'USD')
    else:
        contract = Stock(sym, 'SMART', 'USD')
    await ib.qualifyContractsAsync(contract)

    def _bar_to_dict(b):
        d = b.date
        return {
            'date':   d.isoformat() if hasattr(d, 'isoformat') else str(d),
            'open':   clean(b.open),
            'high':   clean(b.high),
            'low':    clean(b.low),
            'close':  clean(b.close),
            'volume': int(b.volume) if b.volume else 0,
        }

    # For '2 Y' daily: chain two 1-year requests (IBKR max is 1 Y per call)
    if duration == '2 Y' and bar_size == '1 day':
        # Year 1: last 12 months
        bars1 = await ib.reqHistoricalDataAsync(
            contract, endDateTime='', durationStr='1 Y',
            barSizeSetting='1 day', whatToShow='TRADES',
            useRTH=rth, formatDate=1, keepUpToDate=False
        )
        # Year 2: 12-24 months ago
        end2 = (datetime.now() - timedelta(days=365)).strftime('%Y%m%d %H:%M:%S')
        bars2 = await ib.reqHistoricalDataAsync(
            contract, endDateTime=end2, durationStr='1 Y',
            barSizeSetting='1 day', whatToShow='TRADES',
            useRTH=rth, formatDate=1, keepUpToDate=False
        )
        result = sorted(
            [_bar_to_dict(b) for b in (bars2 or [])] +
            [_bar_to_dict(b) for b in (bars1 or [])],
            key=lambda x: x['date']
        )
    else:
        bars   = await ib.reqHistoricalDataAsync(
            contract, endDateTime='', durationStr=duration,
            barSizeSetting=bar_size, whatToShow='TRADES',
            useRTH=rth, formatDate=1, keepUpToDate=False
        )
        result = [_bar_to_dict(b) for b in (bars or [])]

    _hist_cache[cache_key] = {'ts': datetime.utcnow(), 'bars': result}
    return result

# ── Futures historical bars ───────────────────────────────
@app.get("/history/futures/{symbol}")
async def get_futures_history(
    symbol:   str,
    duration: str = Query(default="1 Y",   description="e.g. '60 D', '6 M', '1 Y'"),
    bar_size: str = Query(default="5 mins", description="e.g. '5 mins', '1 hour', '1 day'"),
    rth:      bool = Query(default=True,   description="Regular trading hours only"),
):
    """
    Fetch continuous futures bars from IBKR.
    Uses ContFuture (IBKR continuous contract) for clean historical data.
    useRTH defaults False — futures trade 23h/day, RTH filter removes most bars.
    Returns bars as {ts, open, high, low, close, volume} for collect_bars.py.
    """
    from ib_async import ContFuture
    sym = symbol.upper()   # e.g. 'MNQ'

    exchanges = {'MNQ': 'CME', 'NQ': 'CME', 'ES': 'CME', 'MES': 'CME',
                 'YM': 'CBOT', 'MYM': 'CBOT', 'RTY': 'CME', 'M2K': 'CME'}
    exchange  = exchanges.get(sym, 'CME')

    # ContFuture = IBKR's continuous adjusted contract — best for backtesting
    contract = ContFuture(sym, exchange=exchange, currency='USD')
    try:
        qualified = await ib.qualifyContractsAsync(contract)
        if not qualified:
            return {'error': f'Could not qualify ContFuture {sym}', 'bars': []}
    except Exception as e:
        return {'error': f'Contract qualification failed: {e}', 'bars': []}

    def _bar_to_dict(b):
        d = b.date
        return {
            'ts':     d.isoformat() if hasattr(d, 'isoformat') else str(d),
            'open':   clean(b.open),
            'high':   clean(b.high),
            'low':    clean(b.low),
            'close':  clean(b.close),
            'volume': int(b.volume) if b.volume else 0,
        }

    # useRTH=False: futures trade 23h — RTH filter would drop nearly all intraday bars
    use_rth = rth if bar_size == '1 day' else False
    try:
        bars = await ib.reqHistoricalDataAsync(
            contract, endDateTime='', durationStr=duration,
            barSizeSetting=bar_size, whatToShow='TRADES',
            useRTH=use_rth, formatDate=1, keepUpToDate=False
        )
        return {'symbol': sym, 'bars': [_bar_to_dict(b) for b in (bars or [])]}
    except Exception as e:
        return {'error': str(e), 'bars': []}


# ── Place an order ────────────────────────────────────────
class OrderRequest(BaseModel):
    symbol:      str
    qty:         int
    side:        str
    order_type:  str   = "MARKET"
    limit_price: float = None
    outside_rth: bool  = False   # True for pre/after-market limit orders

@app.post("/order")
async def place_order(req: OrderRequest):
    contract = Stock(req.symbol.upper(), 'SMART', 'USD')
    await ib.qualifyContractsAsync(contract)

    if req.order_type == "LIMIT" and req.limit_price:
        order = LimitOrder(req.side.upper(), req.qty, req.limit_price)
    else:
        order = MarketOrder(req.side.upper(), req.qty)

    if req.outside_rth:
        order.outsideRth = True   # IBKR extended-hours flag
    if IBKR_ACCOUNT:
        order.account = IBKR_ACCOUNT   # pin to Individual account — never TFSA

    trade = ib.placeOrder(contract, order)

    # Subscribe persistent streaming data so get_live_price() sees live prices
    # for the entire session — covers both LONG entries and SHORT entries.
    # Idempotent: ib_insync silently reuses an existing subscription for the
    # same contract, so exit orders (which share the same contract) are harmless.
    ib.reqMktData(contract, '', False, False)

    await asyncio.sleep(1)
    return {
        "status":    "submitted",
        "symbol":    req.symbol.upper(),
        "side":      req.side.upper(),
        "qty":       req.qty,
        "orderId":   trade.order.orderId,
        "orderType": req.order_type
    }

# ── Order fill status ────────────────────────────────────
@app.get("/order/{order_id}/status")
async def get_order_status(order_id: int):
    for trade in ib.trades():
        if trade.order.orderId == order_id:
            return {
                "orderId":       order_id,
                "status":        trade.orderStatus.status,
                "filled":        trade.orderStatus.filled,
                "remaining":     trade.orderStatus.remaining,
                "avgFillPrice":  clean(trade.orderStatus.avgFillPrice),
            }
    return {"orderId": order_id, "status": "Unknown", "filled": 0}

# ── Cancel all orders ─────────────────────────────────────
@app.post("/cancel_all")
async def cancel_all():
    ib.reqGlobalCancel()
    return {"status": "all orders cancelled"}

# ── Dynamic momentum scanner ─────────────────────────────
@app.get("/scan/momentum")
async def scan_momentum(
    min_price: float = Query(default=5.0,   description="Min stock price"),
    max_price: float = Query(default=200.0, description="Max stock price"),
    min_pct:   float = Query(default=3.0,   description="Min % gain to qualify"),
    rows:      int   = Query(default=50,    description="Candidates per scan"),
):
    """
    Scan IBKR universe for top pre-market/intraday momentum movers.
    Uses IBKR scanner to rank symbols, yfinance to compute actual % change
    (IBKR's distance field returns empty strings — yfinance is the reliable fallback).
    """
    seen_syms = {}   # sym → {rank, scan}
    errors    = []

    for scan_code in ['TOP_PERC_GAIN', 'HOT_BY_VOLUME']:
        try:
            sub  = ScannerSubscription(
                numberOfRows=rows,
                instrument='STK',
                locationCode='STK.US.MAJOR',
                scanCode=scan_code,
                abovePrice=min_price,
                belowPrice=max_price,
                aboveVolume=100000,
                stockTypeFilter='CORP',   # exclude ETFs, REITs, CEFs — stocks only
            )
            data = await ib.reqScannerDataAsync(sub)
            for item in data:
                sym = item.contractDetails.contract.symbol
                if not sym.isalpha() or len(sym) > 5:
                    continue
                rank = item.rank if item.rank is not None else 999
                if sym not in seen_syms or rank < seen_syms[sym]['rank']:
                    seen_syms[sym] = {'rank': rank, 'scan': scan_code}
        except Exception as e:
            errors.append(f"{scan_code}: {e}")

    if not seen_syms:
        if errors:
            print(f"⚠️  Momentum scan errors: {errors}")
        return []

    # Compute actual % change via yfinance (period='2d' gives yesterday + today's current bar)
    symbols = list(seen_syms.keys())
    results = []
    try:
        loop = asyncio.get_event_loop()
        raw  = await loop.run_in_executor(
            None,
            lambda: yf.download(symbols, period='2d', interval='1d',
                                 progress=False, auto_adjust=True)
        )
        if isinstance(raw.columns, pd.MultiIndex):
            closes = raw['Close']
            opens  = raw['Open']
        else:
            # Single ticker returns flat columns
            closes = raw[['Close']].rename(columns={'Close': symbols[0]})
            opens  = raw[['Open']].rename(columns={'Open': symbols[0]})

        for sym in symbols:
            try:
                col = closes[sym].dropna() if sym in closes.columns else pd.Series()
                if len(col) < 2:
                    continue
                prev_close = float(col.iloc[-2])
                last_price = float(col.iloc[-1])
                if prev_close <= 0 or last_price < min_price or last_price > max_price:
                    continue
                pct = round((last_price - prev_close) / prev_close * 100, 2)
                if pct < min_pct:
                    continue
                results.append({
                    'symbol':     sym,
                    'pct_change': pct,
                    'price':      round(last_price, 2),
                    'scan':       seen_syms[sym]['scan'],
                    'rank':       seen_syms[sym]['rank'],
                })
            except Exception:
                pass
    except Exception as e:
        errors.append(f"yfinance batch: {e}")

    results.sort(key=lambda x: -x['pct_change'])

    if errors:
        print(f"⚠️  Momentum scan errors: {errors}")

    print(f"📊 Momentum scan: {len(seen_syms)} IBKR symbols → {len(results)} qualify (≥{min_pct}%)")
    return results[:25]

# ── Tools for Claude ──────────────────────────────────────
TOOLS = [
    {
        "name": "get_quote",
        "description": "Get current price of a US stock",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker e.g. AAPL"}
            },
            "required": ["symbol"]
        }
    },
    {
        "name": "get_portfolio",
        "description": "Get all portfolio positions and holdings",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_account",
        "description": "Get account balance, buying power, and P&L",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "place_order",
        "description": "Buy or sell a US stock via IBKR paper trading",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol":      {"type": "string"},
                "qty":         {"type": "integer"},
                "side":        {"type": "string", "enum": ["BUY", "SELL"]},
                "order_type":  {"type": "string", "enum": ["MARKET", "LIMIT"]},
                "limit_price": {"type": "number"}
            },
            "required": ["symbol", "qty", "side", "order_type"]
        }
    },
    {
        "name": "cancel_all_orders",
        "description": "Cancel all open/pending orders immediately",
        "input_schema": {"type": "object", "properties": {}}
    }
]

# ── Execute tool call ─────────────────────────────────────
async def run_tool(name, inp):
    if   name == "get_quote":
        return await get_quote(inp["symbol"])
    elif name == "get_portfolio":
        return await get_portfolio()
    elif name == "get_account":
        return await get_account()
    elif name == "place_order":
        req = OrderRequest(**inp)
        return await place_order(req)
    elif name == "cancel_all_orders":
        return await cancel_all()
    return {"error": f"Unknown tool: {name}"}

# ── Core Claude chat function ─────────────────────────────
async def ask_claude(user_msg, history):
    msgs = history + [{"role": "user", "content": user_msg}]

    while True:
        response = ai.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system="""You are a trading assistant connected to IBKR paper trading.
Help the user check prices, review portfolio, and place trades.
Keep responses concise and clear. No markdown formatting — plain text only.
No asterisks, no bold, no bullet symbols. Just clean plain text.
Always mention this is PAPER TRADING when placing orders.""",
            tools=TOOLS,
            messages=msgs
        )

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"  🔧 {block.name}({block.input})")
                    result = await run_tool(block.name, block.input)
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     json.dumps(result)
                    })
            msgs.append({"role": "assistant", "content": response.content})
            msgs.append({"role": "user",      "content": tool_results})
        else:
            reply = response.content[0].text
            msgs.append({"role": "assistant", "content": reply})
            return reply, msgs

# ── Web UI chat endpoint ──────────────────────────────────
class ChatRequest(BaseModel):
    messages: list
    history:  list = []

@app.post("/chat")
async def chat(req: ChatRequest):
    reply, history = await ask_claude(req.messages[0]["content"], req.history)
    return {"reply": reply, "history": history}

# ── Send Telegram alert ───────────────────────────────────
def send_telegram_alert(message: str):
    try:
        http_requests.post(
            f"{TG_API}/sendMessage",
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': message},
            timeout=10
        )
        print(f"📱 Telegram alert sent: {message}")
    except Exception as e:
        print(f"❌ Telegram alert failed: {e}")

@app.post("/alert")
async def send_alert(data: dict):
    send_telegram_alert(data.get("message", ""))
    return {"status": "sent"}

# ═══════════════════════════════════════════════════════════
# OPTIONS ENDPOINTS
# ═══════════════════════════════════════════════════════════

# ── Options chain ─────────────────────────────────────────
@app.get("/options/chain/{symbol}")
async def get_options_chain(
    symbol: str,
    expiry: str = Query(default=None, description="YYYYMMDD — if omitted returns nearest 4 expiries"),
    right:  str = Query(default="C",  description="C=call P=put"),
):
    """
    Return available strikes for a symbol/expiry.
    If expiry omitted, returns the next 4 expiry dates and all strikes for each.
    """
    sym      = symbol.upper()
    stk      = Stock(sym, 'SMART', 'USD')
    await ib.qualifyContractsAsync(stk)
    con_id   = stk.conId

    chains   = await ib.reqSecDefOptParamsAsync(sym, '', 'STK', con_id)
    if not chains:
        return {"error": f"No options chain data for {sym}"}

    # Use SMART or first available exchange
    chain    = next((c for c in chains if c.exchange == 'SMART'), chains[0])
    from datetime import date as _date, datetime as _datetime
    today    = _date.today()

    def _dte(e):
        return (_datetime.strptime(e, '%Y%m%d').date() - today).days

    expiries = sorted(chain.expirations)

    if expiry:
        target_expiries = [expiry] if expiry in expiries else []
    else:
        # Return expiries useful for spreads (18-50 DTE) + LEAPs (360-750 DTE)
        target_expiries = [e for e in expiries if 18 <= _dte(e) <= 50 or 360 <= _dte(e) <= 750]
        if not target_expiries:
            target_expiries = expiries[:8]   # fallback if nothing in range

    if not target_expiries:
        return {"error": f"Expiry {expiry} not found. Available: {expiries[:8]}"}

    result = []
    for exp in target_expiries:
        result.append({
            "expiry":  exp,
            "right":   right.upper(),
            "strikes": sorted(chain.strikes),
        })
    return {"symbol": sym, "chain": result}


# ── Single option quote with Greeks ──────────────────────
@app.get("/options/quote/{symbol}/{expiry}/{strike}/{right}")
async def get_option_quote(symbol: str, expiry: str, strike: float, right: str):
    """
    Delayed bid/ask/mid + Greeks for a single option contract.
    Uses reqMarketDataType(3) — 15-min delayed, no OPRA subscription required.
    expiry: YYYYMMDD, right: C or P
    """
    sym      = symbol.upper()
    contract  = Option(sym, expiry, strike, right.upper(), 'SMART', '', 'USD')
    qualified = await ib.qualifyContractsAsync(contract)
    q = qualified[0] if qualified else None
    if not q or not getattr(q, 'conId', None):
        return {"error": f"Could not qualify {sym} {expiry} {strike} {right}"}

    ib.reqMarketDataType(3)   # delayed — OPRA subscription active, may need until next market open
    # genericTickList='100' requests option model computation (tick type 53 → modelGreeks).
    # Without it, IBKR may not push Greeks at all on a snapshot request.
    # 10s wait: underlying price needed first, then model calc
    ticker   = ib.reqMktData(q, genericTickList='100', snapshot=True)
    await asyncio.sleep(10)
    ib.reqMarketDataType(1)   # reset to live for equity quotes

    bid  = clean(ticker.bid)
    ask  = clean(ticker.ask)
    last = clean(ticker.last)
    mid  = round((bid + ask) / 2, 4) if bid and ask else None

    greeks = ticker.modelGreeks
    delta  = clean(greeks.delta)  if greeks else None
    gamma  = clean(greeks.gamma)  if greeks else None
    theta  = clean(greeks.theta)  if greeks else None
    vega   = clean(greeks.vega)   if greeks else None
    iv     = clean(greeks.impliedVol) if greeks else None

    return {
        "symbol":  sym,
        "expiry":  expiry,
        "strike":  strike,
        "right":   right.upper(),
        "bid":     bid,
        "ask":     ask,
        "mid":     mid,
        "last":    last,
        "spread":  round(ask - bid, 4) if bid and ask else None,
        "delta":   delta,
        "gamma":   gamma,
        "theta":   theta,
        "vega":    vega,
        "iv":      iv,
    }


# ── IV Rank ───────────────────────────────────────────────
@app.get("/options/iv_rank/{symbol}")
async def get_iv_rank(symbol: str):
    """
    Calculate IV Rank = (current_IV - 52w_low) / (52w_high - 52w_low) × 100
    Uses IBKR historical OPTION_IMPLIED_VOLATILITY bars for the underlying stock.
    Result is cached 4 hours — IBKR reqHistoricalDataAsync takes 30-60s under pacing.
    """
    sym = symbol.upper()

    # Serve cached value if still fresh
    cached = _iv_rank_cache.get(sym)
    if cached:
        age = (datetime.utcnow() - cached['ts']).total_seconds()
        if age < _IV_RANK_CACHE_TTL:
            return cached['result']

    stk = Stock(sym, 'SMART', 'USD')
    await ib.qualifyContractsAsync(stk)

    bars = await ib.reqHistoricalDataAsync(
        stk, endDateTime='', durationStr='1 Y',
        barSizeSetting='1 day', whatToShow='OPTION_IMPLIED_VOLATILITY',
        useRTH=True, formatDate=1, keepUpToDate=False
    )
    if not bars or len(bars) < 20:
        return {"symbol": sym, "iv_rank": None, "error": "Insufficient IV history"}

    iv_vals    = [b.close for b in bars if b.close and b.close > 0]
    current_iv = round(iv_vals[-1] * 100, 2)
    iv_52w_high = round(max(iv_vals) * 100, 2)
    iv_52w_low  = round(min(iv_vals) * 100, 2)
    iv_rank     = round((iv_vals[-1] - min(iv_vals)) /
                        (max(iv_vals) - min(iv_vals)) * 100, 1) if max(iv_vals) != min(iv_vals) else 50.0

    result = {
        "symbol":      sym,
        "current_iv":  current_iv,
        "iv_52w_high": iv_52w_high,
        "iv_52w_low":  iv_52w_low,
        "iv_rank":     iv_rank,
        "note":        "buy when rank<30, sell when rank>60",
    }
    _iv_rank_cache[sym] = {'ts': datetime.utcnow(), 'result': result}
    return result


@app.get("/options/iv_history/{symbol}")
async def get_iv_history(symbol: str):
    """
    Return 1 year of daily implied-volatility bars as a time series.
    Used by backtester_options.py to reconstruct historical IV rank at each date.
    Values are raw decimals (e.g. 0.47 = 47% IV).
    """
    sym = symbol.upper()
    stk = Stock(sym, 'SMART', 'USD')
    await ib.qualifyContractsAsync(stk)

    bars = await ib.reqHistoricalDataAsync(
        stk, endDateTime='', durationStr='1 Y',
        barSizeSetting='1 day', whatToShow='OPTION_IMPLIED_VOLATILITY',
        useRTH=True, formatDate=1, keepUpToDate=False
    )
    if not bars:
        return {"symbol": sym, "bars": [], "error": "No IV data returned"}

    result = [
        {"date": str(b.date), "iv": round(b.close, 6)}
        for b in bars if b.close and b.close > 0
    ]
    return {"symbol": sym, "count": len(result), "bars": result}


# ── Place options order (single leg or combo spread) ──────
class OptionsOrderRequest(BaseModel):
    symbol:      str
    expiry:      str             # YYYYMMDD
    strike:      float
    right:       str             # C or P
    qty:         int
    action:      str             # BUY or SELL
    order_type:  str  = "LIMIT"
    limit_price: float = None
    # Spread (second leg) — omit for single-leg orders
    short_expiry:  str   = None
    short_strike:  float = None
    short_right:   str   = None
    net_debit:     float = None  # positive = debit spread (you pay), negative = credit

@app.post("/options/order")
async def place_options_order(req: OptionsOrderRequest):
    """
    Place a single-leg option order or a two-leg combo (bull spread).
    For spreads: provide short_strike + short_expiry + net_debit.
    Always uses LIMIT orders — never market orders on options.
    """
    sym = req.symbol.upper()

    # ── Single leg ────────────────────────────────────────
    if req.short_strike is None:
        contract  = Option(sym, req.expiry, req.strike, req.right.upper(), 'SMART', '', 'USD')
        qualified = await ib.qualifyContractsAsync(contract)
        if not qualified:
            return {"error": f"Could not qualify {sym} {req.expiry} {req.strike} {req.right}"}
        if req.limit_price is None:
            return {"error": "limit_price required — never use market orders on options"}
        order = LimitOrder(req.action.upper(), req.qty, req.limit_price)
        if IBKR_ACCOUNT:
            order.account = IBKR_ACCOUNT
        trade = ib.placeOrder(contract, order)
        await asyncio.sleep(1)
        return {
            "status":    "submitted",
            "type":      "single_leg",
            "symbol":    sym,
            "expiry":    req.expiry,
            "strike":    req.strike,
            "right":     req.right.upper(),
            "action":    req.action.upper(),
            "qty":       req.qty,
            "limit":     req.limit_price,
            "orderId":   trade.order.orderId,
        }

    # ── Two-leg combo (bull spread) ───────────────────────
    long_leg  = Option(sym, req.expiry,       req.strike,       req.right.upper(),       'SMART', '', 'USD')
    short_leg = Option(sym, req.short_expiry or req.expiry,
                            req.short_strike, (req.short_right or req.right).upper(), 'SMART', '', 'USD')

    qualified = await ib.qualifyContractsAsync(long_leg, short_leg)
    if len(qualified) < 2 or not getattr(qualified[0], 'conId', None) or not getattr(qualified[1], 'conId', None):
        return {"error": f"Could not qualify {sym} {req.expiry} ${req.strike}/{req.short_strike} — strike not listed for this expiry"}

    combo           = Contract()
    combo.symbol    = sym
    combo.secType   = 'BAG'
    combo.currency  = 'USD'
    combo.exchange  = 'SMART'

    buy_leg         = ComboLeg()
    buy_leg.conId   = long_leg.conId
    buy_leg.ratio   = 1
    buy_leg.action  = 'BUY'
    buy_leg.exchange= 'SMART'

    sell_leg        = ComboLeg()
    sell_leg.conId  = short_leg.conId
    sell_leg.ratio  = 1
    sell_leg.action = 'SELL'
    sell_leg.exchange='SMART'

    # For opening: long_leg=BUY, short_leg=SELL, order=BUY (debit)
    # For closing: long_leg=SELL, short_leg=BUY, order=SELL (credit)
    order_action = (req.action or 'BUY').upper()
    buy_leg.action  = 'BUY'  if order_action == 'BUY' else 'SELL'
    sell_leg.action = 'SELL' if order_action == 'BUY' else 'BUY'

    combo.comboLegs = [buy_leg, sell_leg]

    if req.net_debit is None:
        return {"error": "net_debit required for spread orders"}

    order = LimitOrder(order_action, req.qty, abs(round(req.net_debit, 2)))
    order.tif = 'DAY'
    order.transmit = True
    if IBKR_ACCOUNT:
        order.account = IBKR_ACCOUNT
    # type=1 triggers paper fill simulation for BAG orders.
    # Keep it active for 15s — resetting too early kills the simulator before it fills.
    ib.reqMarketDataType(1)
    trade = ib.placeOrder(combo, order)
    await asyncio.sleep(15)  # paper BAG fill simulation typically needs 5-15s
    # stay on type 1 — OPRA subscription now active
    return {
        "status":      "submitted",
        "type":        "bull_spread",
        "symbol":      sym,
        "long_leg":    f"{req.expiry} ${req.strike} {req.right.upper()}",
        "short_leg":   f"{req.short_expiry or req.expiry} ${req.short_strike} {(req.short_right or req.right).upper()}",
        "qty":         req.qty,
        "net_debit":   req.net_debit,
        "orderId":     trade.order.orderId,
    }


# ── IBKR news feed ───────────────────────────────────────
@app.get("/options/news_providers")
async def get_news_providers():
    """List all IBKR news providers available on this account."""
    providers = await ib.reqNewsProvidersAsync()
    return {
        "count":     len(providers),
        "providers": [{"code": p.code, "name": p.name} for p in providers],
    }


@app.get("/options/news/{symbol}")
async def get_ibkr_news(
    symbol: str,
    hours:  int = Query(default=6, description="How many hours back to search"),
    limit:  int = Query(default=20, description="Max articles to return"),
):
    """
    Fetch recent news for a symbol via IBKR's news feed.
    Uses all providers subscribed on this account.
    Paper accounts often have Globe Newswire + PR Newswire free.
    Paid providers (Briefing.com, Dow Jones) activate automatically once subscribed.
    """
    sym = symbol.upper()
    stk = Stock(sym, 'SMART', 'USD')
    await ib.qualifyContractsAsync(stk)
    if not stk.conId:
        return {"symbol": sym, "news": [], "error": "Could not qualify contract"}

    providers = await ib.reqNewsProvidersAsync()
    if not providers:
        return {"symbol": sym, "news": [], "providers": [],
                "note": "No news providers available — check IBKR account subscriptions"}

    provider_codes = '+'.join(p.code for p in providers)
    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(hours=hours)

    # Return type annotation in ib_async is wrong; wrapper accumulates into a list
    articles = await ib.reqHistoricalNewsAsync(
        conId=stk.conId,
        providerCodes=provider_codes,
        startDateTime=start_dt,
        endDateTime=end_dt,
        totalResults=limit,
        historicalNewsOptions=[],
    )

    if not articles:
        return {
            "symbol":    sym,
            "providers": [p.code for p in providers],
            "news":      [],
            "note":      "No articles in time window — provider may require subscription",
        }

    news = [
        {
            "headline":  re.sub(r'^\{[^}]+\}', '', a.headline).strip(),
            "time":      a.time.isoformat(),
            "provider":  a.providerCode,
            "articleId": a.articleId,
        }
        for a in articles
    ]
    return {
        "symbol":    sym,
        "providers": [p.code for p in providers],
        "count":     len(news),
        "news":      news,
    }


# ── Open options positions ────────────────────────────────
@app.get("/portfolio/options")
async def get_options_portfolio():
    """Return all open options positions with unrealized P&L."""
    items = ib.portfolio()
    opts  = []
    for p in items:
        if p.contract.secType not in ('OPT', 'BAG') or p.position == 0:
            continue
        c = p.contract
        opts.append({
            "symbol":        c.symbol,
            "expiry":        getattr(c, 'lastTradeDateOrContractMonth', None),
            "strike":        getattr(c, 'strike', None),
            "right":         getattr(c, 'right', None),
            "qty":           p.position,
            "avgCost":       clean(p.averageCost),
            "marketPrice":   clean(p.marketPrice),
            "marketValue":   clean(p.marketValue),
            "unrealizedPnL": clean(p.unrealizedPNL),
        })
    return opts


# ── Wall Street Horizon corporate events ─────────────────
@app.get("/options/wsh_events/{symbol}")
async def get_wsh_events(
    symbol: str,
    days: int = Query(default=60, description="How many days ahead to search"),
):
    """
    Fetch upcoming corporate events from Wall Street Horizon for a symbol.
    Requires WSH subscription in IBKR (fee waived on most accounts).
    Returns earnings, analyst days, conferences, guidance events.
    """
    sym = symbol.upper()
    stk = Stock(sym, 'SMART', 'USD')
    await ib.qualifyContractsAsync(stk)
    if not stk.conId:
        return {"symbol": sym, "events": [], "error": "Could not qualify contract"}

    start_dt = datetime.now().strftime('%Y%m%d')
    end_dt   = (datetime.now() + timedelta(days=days)).strftime('%Y%m%d')

    wsh = WshEventData(
        conId     = stk.conId,
        startDate = start_dt,
        endDate   = end_dt,
        totalLimit = 20,
    )

    try:
        raw = await ib.getWshEventDataAsync(wsh)
    except Exception as e:
        return {"symbol": sym, "events": [], "error": str(e)}

    if not raw:
        return {"symbol": sym, "events": [],
                "note": "No WSH data returned — check IBKR subscription"}

    # WSH returns a JSON string; parse and normalise
    try:
        data = json.loads(raw)
    except Exception:
        return {"symbol": sym, "raw_text": raw[:500], "events": [],
                "note": "Could not parse WSH response — raw_text shows first 500 chars"}

    # Normalise into a flat list regardless of WSH response envelope shape
    events_raw = data if isinstance(data, list) else data.get("data", data.get("events", []))

    # Map WSH event types to our catalyst_calendar types
    type_map = {
        "earnings":          "EARNINGS",
        "earnings release":  "EARNINGS",
        "dividend":          "SECTOR_EVENT",
        "analyst day":       "ANALYST_EVENT",
        "analyst/investor":  "ANALYST_EVENT",
        "conference":        "CONFERENCE",
        "product":           "PRODUCT_LAUNCH",
        "guidance":          "EARNINGS_SIGNAL",
        "fda":               "MACRO_EVENT",
        "split":             "SECTOR_EVENT",
    }

    events = []
    for ev in events_raw:
        raw_type   = str(ev.get("wshEventType") or ev.get("type") or "").lower()
        event_type = next((v for k, v in type_map.items() if k in raw_type), "SECTOR_EVENT")
        event_date = (ev.get("startDate") or ev.get("date") or "")[:10]  # YYYY-MM-DD
        if not event_date:
            continue
        events.append({
            "event_type":   event_type,
            "event_name":   ev.get("description") or ev.get("title") or raw_type.title(),
            "event_date":   event_date,
            "importance":   ev.get("importance") or "Medium",
            "wsh_raw_type": raw_type,
        })

    return {
        "symbol": sym,
        "count":  len(events),
        "events": sorted(events, key=lambda x: x["event_date"]),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=BRIDGE_PORT)
