"""
bridge_rithmic.py  —  FastAPI bridge to Rithmic via async_rithmic
Port 8001 (IBKR bridge uses 8000)

Same API contract as bridge.py:
  GET  /                        health + connection status
  GET  /connected               simple connected flag
  GET  /quote/{symbol}          live quote (MNQ always resolves to front month)
  GET  /portfolio               current futures positions
  GET  /account                 account equity / balance
  POST /order                   place MARKET or LIMIT order
  GET  /order/{order_id}/status order fill status
  POST /cancel_all              cancel all open orders
  GET  /history/{symbol}        historical OHLCV bars (5-min or daily)
"""

import asyncio
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Query
from pydantic import BaseModel

# ── load root .env ────────────────────────────────────────
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from async_rithmic import RithmicClient
from async_rithmic.enums import (
    DataType, OrderType, TransactionType, OrderDuration,
    OrderPlacement, TimeBarType,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("bridge_rithmic")

# ── config ────────────────────────────────────────────────
RITHMIC_USER        = os.getenv('RITHMIC_USER')
RITHMIC_PASSWORD    = os.getenv('RITHMIC_PASSWORD')
RITHMIC_SYSTEM_NAME = os.getenv('RITHMIC_SYSTEM_NAME', 'Rithmic Futures Test54')
RITHMIC_SERVER_URL  = os.getenv('RITHMIC_SERVER_URL', 'rituz00100.rithmic.com:443')
BRIDGE_PORT         = int(os.getenv('RITHMIC_BRIDGE_PORT', '8001'))
EXCHANGE            = 'CME'

# ── shared state ──────────────────────────────────────────
client: Optional[RithmicClient] = None
_account_id: Optional[str]      = None
_front_month: Optional[str]     = None   # e.g. "MNQM6"
_quotes: dict                   = {}     # front_month_symbol -> {bid, ask, last, volume, ts}
_order_statuses: dict           = {}     # order_id -> {status, filled, avg_price}


# ── tick callback — keeps quote cache fresh ───────────────
async def _on_tick(data: dict):
    sym = data.get('symbol')
    if not sym:
        return
    q = _quotes.setdefault(sym, {})
    dt = data.get('data_type')

    if dt == DataType.LAST_TRADE:
        if 'trade_price' in data:
            q['last']   = data['trade_price']
        if 'volume' in data:
            q['volume'] = data['volume']
        if 'vwap' in data:
            q['vwap']   = data['vwap']

    elif dt == DataType.BBO:
        if 'bid_price' in data:
            q['bid'] = data['bid_price']
        if 'ask_price' in data:
            q['ask'] = data['ask_price']

    q['ts'] = datetime.utcnow().isoformat()


# ── order notification callback ───────────────────────────
async def _on_exchange_order(data):
    oid = getattr(data, 'user_tag', None) or data.get('user_tag')
    if not oid:
        return
    _order_statuses[str(oid)] = {
        'status':    getattr(data, 'status', data.get('status', 'unknown')),
        'filled':    getattr(data, 'filled_quantity', data.get('filled_quantity', 0)),
        'avg_price': getattr(data, 'avg_fill_price', data.get('avg_fill_price', 0.0)),
    }


# ── lifespan: connect on startup, disconnect on shutdown ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, _account_id, _front_month

    log.info("Connecting to Rithmic [%s] @ %s", RITHMIC_SYSTEM_NAME, RITHMIC_SERVER_URL)

    client = RithmicClient(
        user=RITHMIC_USER,
        password=RITHMIC_PASSWORD,
        system_name=RITHMIC_SYSTEM_NAME,
        app_name="TradingBot",
        app_version="1.0",
        url=RITHMIC_SERVER_URL,
        manual_or_auto=OrderPlacement.AUTO,
    )
    client.on_tick                    += _on_tick
    client.on_exchange_order_notification += _on_exchange_order

    await client.connect()
    log.info("Rithmic connected")

    # Cache account ID
    try:
        accounts = await client.list_accounts()
        if accounts:
            _account_id = accounts[0].get('account_id') or accounts[0].get('fcm_account_id')
            log.info("Account ID: %s", _account_id)
    except Exception as e:
        log.warning("Could not fetch account ID: %s", e)

    # Resolve MNQ front month and subscribe to live quotes
    try:
        _front_month = await client.get_front_month_contract('MNQ', EXCHANGE)
        log.info("Front month contract: %s", _front_month)
        await client.subscribe_to_market_data(
            _front_month, EXCHANGE,
            DataType.LAST_TRADE | DataType.BBO,
        )
        log.info("Subscribed to market data for %s", _front_month)
    except Exception as e:
        log.warning("Could not subscribe to market data: %s", e)

    yield

    # Shutdown
    if _front_month:
        try:
            await client.unsubscribe_from_market_data(
                _front_month, EXCHANGE, DataType.LAST_TRADE | DataType.BBO
            )
        except Exception:
            pass
    await client.disconnect()
    log.info("Rithmic disconnected")


app = FastAPI(title="Rithmic Bridge", lifespan=lifespan)


# ── helpers ───────────────────────────────────────────────
def _clean(v):
    if v is None:
        return None
    try:
        f = float(v)
        return None if f in (float('inf'), float('-inf'), float('nan')) else round(f, 4)
    except (TypeError, ValueError):
        return None

def _is_connected() -> bool:
    if client is None:
        return False
    return all(p.is_connected for p in client.plants.values())


# ── endpoints ─────────────────────────────────────────────

@app.get("/")
async def health():
    return {
        "status":       "running",
        "connected":    _is_connected(),
        "account":      _account_id or "unknown",
        "front_month":  _front_month or "unknown",
        "mode":         "paper" if "Test" in (RITHMIC_SYSTEM_NAME or "") else "LIVE",
    }


@app.get("/connected")
async def connected():
    return {"connected": _is_connected()}


@app.get("/quote/{symbol}")
async def get_quote(symbol: str):
    sym = symbol.upper()
    # MNQ (or NQ) always resolves to front month
    lookup = _front_month if sym in ('MNQ', 'NQ') else sym
    q = _quotes.get(lookup or sym, {})

    bid  = _clean(q.get('bid'))
    ask  = _clean(q.get('ask'))
    last = _clean(q.get('last'))
    mid  = round((bid + ask) / 2, 2) if bid and ask else None
    best = last or mid or bid or ask

    return {
        "symbol":       sym,
        "contract":     lookup,
        "last":         last,
        "bid":          bid,
        "ask":          ask,
        "best_price":   best,
        "vwap":         _clean(q.get('vwap')),
        "volume":       q.get('volume'),
        "ts":           q.get('ts'),
        "note":         "live" if best else "no data yet — market may be closed",
    }


@app.get("/portfolio")
async def get_portfolio():
    try:
        positions = await client.list_positions()
        result = []
        for p in (positions or []):
            qty = p.get('net_quantity', 0)
            if qty == 0:
                continue
            result.append({
                "symbol":        p.get('symbol'),
                "contract":      p.get('symbol'),
                "qty":           qty,
                "avgCost":       _clean(p.get('open_average_price')),
                "marketPrice":   _clean(p.get('last_fill_price')),
                "unrealizedPnL": _clean(p.get('open_pnl')),
                "realizedPnL":   _clean(p.get('closed_pnl')),
            })
        return result
    except Exception as e:
        log.error("portfolio error: %s", e)
        return []


@app.get("/account")
async def get_account():
    try:
        summaries = await client.list_account_summary()
        result = {}
        for s in (summaries or []):
            if 'net_liq' in s:
                result['NetLiquidation'] = _clean(s['net_liq'])
            if 'open_balance' in s:
                result['TotalCashValue'] = _clean(s['open_balance'])
            if 'available_buying_power' in s:
                result['BuyingPower'] = _clean(s['available_buying_power'])
            if 'open_pnl' in s:
                result['UnrealizedPnL'] = _clean(s['open_pnl'])
        return result
    except Exception as e:
        log.error("account error: %s", e)
        return {}


@app.get("/history/{symbol}")
async def get_history(
    symbol:   str,
    days:     int = Query(default=5,   description="Number of trading days to fetch"),
    bar_size: str = Query(default="5", description="Bar size in minutes (e.g. '5', '1', '15', 'daily')"),
):
    sym     = symbol.upper()
    lookup  = _front_month if sym in ('MNQ', 'NQ') else sym
    end_dt  = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days + 2)   # +2 for weekends

    if bar_size == 'daily':
        btype, periods = TimeBarType.DAILY_BAR, 1
    else:
        btype, periods = TimeBarType.MINUTE_BAR, int(bar_size)

    try:
        bars = await client.get_historical_time_bars(
            symbol=lookup or sym,
            exchange=EXCHANGE,
            start_time=start_dt,
            end_time=end_dt,
            bar_type=btype,
            bar_type_periods=periods,
        )
        return [
            {
                "date":   b.get('bar_end_datetime') or b.get('datetime', ''),
                "open":   _clean(b.get('open_price')),
                "high":   _clean(b.get('high_price')),
                "low":    _clean(b.get('low_price')),
                "close":  _clean(b.get('close_price')),
                "volume": b.get('volume', 0),
            }
            for b in (bars or [])
        ]
    except Exception as e:
        log.error("history error: %s", e)
        return []


# ── place order ───────────────────────────────────────────
class OrderRequest(BaseModel):
    symbol:      str
    qty:         int
    side:        str             # BUY or SELL
    order_type:  str = "MARKET"  # MARKET or LIMIT
    limit_price: Optional[float] = None


@app.post("/order")
async def place_order(req: OrderRequest):
    sym    = req.symbol.upper()
    lookup = _front_month if sym in ('MNQ', 'NQ') else sym
    oid    = str(uuid.uuid4())[:16]

    txn = TransactionType.BUY if req.side.upper() == 'BUY' else TransactionType.SELL
    otype = OrderType.MARKET if req.order_type.upper() == 'MARKET' else OrderType.LIMIT

    kwargs = {}
    if otype == OrderType.LIMIT and req.limit_price:
        kwargs['price'] = req.limit_price

    try:
        result = await client.submit_order(
            order_id=oid,
            symbol=lookup or sym,
            exchange=EXCHANGE,
            qty=req.qty,
            transaction_type=txn,
            order_type=otype,
            **kwargs,
        )
        log.info("Order submitted: %s %s %s x%d @ %s",
                 oid, req.side.upper(), lookup, req.qty, req.order_type)
        return {
            "status":    "submitted",
            "symbol":    sym,
            "contract":  lookup,
            "side":      req.side.upper(),
            "qty":       req.qty,
            "orderId":   oid,
            "orderType": req.order_type.upper(),
        }
    except Exception as e:
        log.error("place_order error: %s", e)
        return {"status": "error", "message": str(e)}


@app.get("/order/{order_id}/status")
async def get_order_status(order_id: str):
    cached = _order_statuses.get(order_id)
    if cached:
        return {"orderId": order_id, **cached}

    # Try fetching directly from Rithmic
    try:
        order = await client.get_order(user_tag=order_id)
        if order:
            return {
                "orderId":   order_id,
                "status":    order.get('status', 'unknown'),
                "filled":    order.get('filled_quantity', 0),
                "avg_price": _clean(order.get('avg_fill_price')),
            }
    except Exception:
        pass

    return {"orderId": order_id, "status": "Unknown", "filled": 0}


@app.post("/cancel_all")
async def cancel_all():
    try:
        await client.cancel_all_orders()
        return {"status": "all orders cancelled"}
    except Exception as e:
        log.error("cancel_all error: %s", e)
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    uvicorn.run("bridge_rithmic:app", host="0.0.0.0", port=BRIDGE_PORT, reload=False)
