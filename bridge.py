# bridge.py — trading server + Claude API + WhatsApp via Twilio
# Reads all credentials from .env file
# Command: python bridge.py

from dotenv import load_dotenv
load_dotenv()  # loads .env file from same folder

import os
from ib_async import IB, Stock, Index, MarketOrder, LimitOrder, ScannerSubscription
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import asyncio
import math
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

# Allow browser to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Connect to IB Gateway on startup ─────────────────────
@app.on_event("startup")
async def startup():
    for cid in range(10, 20):
        try:
            await ib.connectAsync(host=IBKR_HOST, port=IBKR_PORT, clientId=cid)
            break
        except Exception:
            continue
    print(f"✅ Connected to IB Gateway ({'paper' if IBKR_PORT == 4002 else 'LIVE'} trading)")
    print("✅ Telegram alerts ready (no tunnel needed)")

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
    items = ib.portfolio()
    return [{
        "symbol":        p.contract.symbol,
        "qty":           p.position,
        "avgCost":       clean(p.averageCost),
        "marketPrice":   clean(p.marketPrice),
        "marketValue":   clean(p.marketValue),
        "unrealizedPnL": clean(p.unrealizedPNL),
        "realizedPnL":   clean(p.realizedPNL),
    } for p in items if p.position != 0]

# ── Get account summary ───────────────────────────────────
@app.get("/account")
async def get_account():
    summary = await ib.accountSummaryAsync()
    result  = {}
    for item in summary:
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

# ── Place an order ────────────────────────────────────────
class OrderRequest(BaseModel):
    symbol:      str
    qty:         int
    side:        str
    order_type:  str   = "MARKET"
    limit_price: float = None

@app.post("/order")
async def place_order(req: OrderRequest):
    contract = Stock(req.symbol.upper(), 'SMART', 'USD')
    await ib.qualifyContractsAsync(contract)

    if req.order_type == "LIMIT" and req.limit_price:
        order = LimitOrder(req.side.upper(), req.qty, req.limit_price)
    else:
        order = MarketOrder(req.side.upper(), req.qty)

    trade = ib.placeOrder(contract, order)
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

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
