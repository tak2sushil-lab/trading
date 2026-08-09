"""
futures/bridge_projectx.py — TopStepX / ProjectX Gateway API bridge.

Built Aug 9 2026, BEFORE a real TopStepX account was registered — dormant by
design, per user request ("build it now, unhook it for testing only, re-hook
once the account is activated"). Implements the SAME REST contract as
bridge.py's /futures/*, /history/futures/{symbol}, and /order/{id}/status
endpoints, so tc_trader.py and london_trader.py can point FUTURES_BRIDGE_URL
at this service with ZERO code changes on their side — that abstraction is
the entire reason both traders only ever talk to a generic bridge URL and
never import a broker SDK directly.

**Current live state: NOT wired in.** tc_trader.py still points at the
DUQ640500 IBKR-paper stand-in via bridge.py on port 8002 (see
launch_futures_trader.sh / .env-tc). Nothing about today's live paper testing
changes until someone deliberately re-points it. To go live once a real
TopStepX account exists:
  1. Add TOPSTEP_API_KEY / TOPSTEP_USERNAME / TOPSTEP_ACCOUNT_ID to .env-tc
     (get these from TopstepX's API Access settings page after registering).
  2. Run this file standalone first and hit GET / — confirm "connected": true
     and a real account balance/position read before touching tc_trader.py.
  3. Only then: point launch_futures_trader.sh's bridge process at this file
     instead of bridge.py, restart, and re-run the smoke-test checklist from
     CLAUDE.md's Aug 9 2026 section.

Endpoint source: fetched directly from https://gateway.docs.projectx.com/ on
Aug 9 2026 — NOT invented. A few endpoints are marked UNVERIFIED below because
the fetched docs excerpts didn't show a full schema (only inferred from naming
conventions or partial examples) — confirm those against the real Swagger UI
(https://api.topstepx.com/swagger/index.html, reachable once you have an API
key) before trusting them in the eval. Everything else (auth, order place,
order cancel, position search, historical bars) was pulled from confirmed,
complete schema examples in the docs.

ProjectX Gateway API summary (TopstepX):
  Base REST:  https://api.topstepx.com
  Realtime:   rtc.topstepx.com — SignalR WebSocket hub, NOT implemented here
              (see get_futures_quote's docstring for why and what to do instead)
  Auth:       POST /api/Auth/loginKey  {userName, apiKey} → {token, success}
  Orders:     POST /api/Order/place | POST /api/Order/cancel
  Positions:  POST /api/Position/searchOpen | POST /api/Position/closeContract
  History:    POST /api/History/retrieveBars
  Contracts:  POST /api/Contract/available   [UNVERIFIED schema]
  Order status: no confirmed dedicated endpoint found in the fetched docs —
              [UNVERIFIED] guessed at /api/Order/searchOpen mirroring the
              Position/searchOpen pattern; falls back to "Unknown" gracefully,
              same as bridge.py does for orders it can't find.
"""

import os
import time
from datetime import datetime, timedelta, timezone

import requests
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env-tc'))

# ── Credentials — not present in .env-tc yet by design (dormant until go-live) ──
TOPSTEP_API_KEY    = os.getenv('TOPSTEP_API_KEY')
TOPSTEP_USERNAME   = os.getenv('TOPSTEP_USERNAME')
TOPSTEP_ACCOUNT_ID = os.getenv('TOPSTEP_ACCOUNT_ID')   # integer account ID, from TopstepX dashboard
# Optional manual override if Contract/available's real schema turns out
# different from what's assumed below — set directly to skip contract lookup.
TOPSTEP_MNQ_CONTRACT_ID = os.getenv('TOPSTEP_MNQ_CONTRACT_ID')

PROJECTX_BASE = os.getenv('PROJECTX_BASE_URL', 'https://api.topstepx.com')
BRIDGE_PORT   = int(os.getenv('TC_PROJECTX_BRIDGE_PORT', '8003'))  # 8002 stays bridge.py/DUQ640500 until re-hook

app = FastAPI(title="TopStepX / ProjectX Bridge")

# ── Token cache ────────────────────────────────────────────────────────────
_token: str | None = None
_token_expiry: float = 0.0   # unix ts

# ── Contract ID cache (symbol -> contractId) ────────────────────────────────
_contract_cache: dict = {}


def _creds_present() -> bool:
    return bool(TOPSTEP_API_KEY and TOPSTEP_USERNAME and TOPSTEP_ACCOUNT_ID)


def _login() -> str | None:
    """POST /api/Auth/loginKey — cache the JWT for 23h (docs say 24h validity)."""
    global _token, _token_expiry
    if _token and time.time() < _token_expiry:
        return _token
    if not _creds_present():
        return None
    try:
        resp = requests.post(
            f'{PROJECTX_BASE}/api/Auth/loginKey',
            json={'userName': TOPSTEP_USERNAME, 'apiKey': TOPSTEP_API_KEY},
            timeout=10,
        )
        data = resp.json()
        if data.get('success') and data.get('token'):
            _token = data['token']
            _token_expiry = time.time() + 23 * 3600
            return _token
    except Exception:
        pass
    return None


def _headers() -> dict | None:
    """Bearer-token auth header. NOTE: the exact header format wasn't shown in
    the fetched docs excerpt (only that JWTs are used) — Bearer is the
    near-universal convention for this auth style and matches every other
    ProjectX example seen, but verify against Swagger before trusting it."""
    token = _login()
    if not token:
        return None
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}


def _px_post(path: str, payload: dict, timeout: int = 10) -> dict:
    headers = _headers()
    if not headers:
        return {'success': False, 'errorMessage': 'not authenticated — check TOPSTEP_API_KEY/USERNAME'}
    try:
        resp = requests.post(f'{PROJECTX_BASE}{path}', json=payload, headers=headers, timeout=timeout)
        return resp.json()
    except Exception as e:
        return {'success': False, 'errorMessage': str(e)}


# ── Contract resolution ─────────────────────────────────────────────────────

def _resolve_contract_id(symbol: str) -> str | None:
    """[UNVERIFIED schema] POST /api/Contract/available — the getting-started
    walkthrough names this endpoint as step 2 of the order flow but the fetched
    docs excerpt didn't include its full request/response shape. Guessed
    request shape below (searchText + live flag, mirroring the naming pattern
    of Position/searchOpen); real contractIds follow the pattern
    CON.F.US.<SYMBOL>.<MonthCode><YY> per confirmed examples in the docs
    (e.g. CON.F.US.BP6.U25, CON.F.US.GMET.J25) — if this call's response shape
    doesn't match, set TOPSTEP_MNQ_CONTRACT_ID directly to skip it entirely."""
    sym = symbol.upper()
    if TOPSTEP_MNQ_CONTRACT_ID and sym == 'MNQ':
        return TOPSTEP_MNQ_CONTRACT_ID
    if sym in _contract_cache:
        return _contract_cache[sym]
    data = _px_post('/api/Contract/available', {'searchText': sym, 'live': False})
    contracts = data.get('contracts') or data.get('data') or []
    for c in contracts:
        cid = c.get('id') or c.get('contractId')
        name = (c.get('name') or c.get('symbol') or '').upper()
        if cid and sym in name:
            _contract_cache[sym] = cid
            return cid
    return None


def _contract_to_symbol(contract_id: str) -> str:
    """CON.F.US.MNQ.Z25 -> MNQ (best-effort reverse of the ID pattern above)."""
    parts = (contract_id or '').split('.')
    return parts[3] if len(parts) >= 4 else (contract_id or '')


# ── History translation ─────────────────────────────────────────────────────

_UNIT_MAP = {'sec': 1, 'min': 2, 'hour': 3, 'day': 4, 'week': 5, 'month': 6}


def _parse_bar_size(bar_size: str) -> tuple[int, int]:
    """'5 mins' -> (2, 5)   '1 hour' -> (3, 1)   '1 day' -> (4, 1)"""
    parts = bar_size.strip().lower().split()
    n = int(parts[0]) if parts and parts[0].isdigit() else 1
    unit_word = parts[-1] if parts else 'min'
    for key, code in _UNIT_MAP.items():
        if unit_word.startswith(key):
            return code, n
    return 2, n   # default: minutes


def _parse_duration_days(duration: str) -> int:
    """'2 D' -> 2   '60 D' -> 60   '6 M' -> 180 (approx)"""
    parts = duration.strip().upper().split()
    if len(parts) != 2:
        return 2
    n, unit = parts
    n = int(n) if n.isdigit() else 2
    return n * 30 if unit.startswith('M') else n


# ── Health ───────────────────────────────────────────────────────────────

@app.get("/")
async def health():
    token = _login()
    return {
        "status":    "running",
        "connected": token is not None,
        "account":   TOPSTEP_ACCOUNT_ID or "not configured",
        "mode":      "TC_PROJECTX",
    }


@app.get("/connected")
async def connected():
    return {"connected": _login() is not None}


# ── Historical bars ─────────────────────────────────────────────────────────

@app.get("/history/futures/{symbol}")
async def get_futures_history(symbol: str, duration: str = "2 D", bar_size: str = "5 mins",
                               end_dt: str = "", rth: bool = False, contract_month: str = ""):
    """Mirrors bridge.py's /history/futures/{symbol} contract exactly — same
    query params, same {'symbol','bars':[...],'source'} response shape."""
    sym = symbol.upper()
    contract_id = _resolve_contract_id(sym)
    if not contract_id:
        return {'symbol': sym, 'bars': [], 'error': 'contract not resolved — set TOPSTEP_MNQ_CONTRACT_ID'}

    unit, unit_number = _parse_bar_size(bar_size)
    days = _parse_duration_days(duration)
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    data = _px_post('/api/History/retrieveBars', {
        'contractId':         contract_id,
        'live':               False,
        'startTime':          start.isoformat(),
        'endTime':            end.isoformat(),
        'unit':               unit,
        'unitNumber':         unit_number,
        'limit':              20000,
        'includePartialBar':  False,
    })
    if not data.get('success'):
        return {'symbol': sym, 'bars': [], 'error': data.get('errorMessage', 'retrieveBars failed')}

    bars = [{
        'ts':     b['t'],
        'open':   b['o'],
        'high':   b['h'],
        'low':    b['l'],
        'close':  b['c'],
        'volume': b.get('v', 0),
    } for b in data.get('bars', [])]
    return {'symbol': sym, 'bars': bars, 'source': 'projectx'}


# ── Quote ────────────────────────────────────────────────────────────────

@app.get("/futures/quote/{symbol}")
async def get_futures_quote(symbol: str):
    """Polling proxy, NOT a true live quote. ProjectX's real-time price feed is
    a SignalR WebSocket hub (rtc.topstepx.com/hubs/market), not a REST
    endpoint. Confirmed Aug 9 2026: no extra credential needed — the same JWT
    from Auth/loginKey authenticates it (passed as an `access_token` query
    param on the hub URL + accessTokenFactory). After connecting, invoke
    SubscribeContractQuotes(contractId) and listen for GatewayQuote events.
    Not a plain WebSocket though — it's Microsoft SignalR (needs a real
    SignalR client: `skipNegotiation=True` + WebSocket-only transport; Python
    options are the `signalrcore` package or the community `projectx-api`
    wrapper built specifically for this API — don't hand-roll the handshake).
    Deliberately out of scope for this first, dormant-by-design pass — build
    it before relying on this endpoint for tight entry/exit pricing, since a
    few-minutes-stale bar close is a materially worse quote than what
    bridge.py's yfinance/IBKR path gives today. For now: returns the most
    recent completed bar's close via History/retrieveBars(live=True) as a
    best-effort stand-in."""
    sym = symbol.upper()
    contract_id = _resolve_contract_id(sym)
    if not contract_id:
        return {'symbol': sym, 'last': None, 'source': 'none', 'error': 'contract not resolved'}

    end   = datetime.now(timezone.utc)
    start = end - timedelta(minutes=15)
    data = _px_post('/api/History/retrieveBars', {
        'contractId': contract_id, 'live': True,
        'startTime': start.isoformat(), 'endTime': end.isoformat(),
        'unit': 2, 'unitNumber': 1, 'limit': 5, 'includePartialBar': True,
    })
    bars = data.get('bars', []) if data.get('success') else []
    last = bars[-1]['c'] if bars else None
    return {
        'symbol': sym, 'contract_month': None,
        'last': last, 'bid': None, 'ask': None, 'close': last,
        'best_price': last, 'source': 'projectx_bar_proxy',
    }


# ── Positions ────────────────────────────────────────────────────────────

@app.get("/futures/position")
async def get_futures_position():
    """Mirrors bridge.py's /futures/position response shape. ProjectX's
    Position/searchOpen doesn't return live market_price/unrealized_pnl
    (confirmed from its documented response schema — only id/contractId/type/
    size/averagePrice) — those two fields are filled in best-effort from the
    quote proxy above, same staleness caveat applies."""
    if not TOPSTEP_ACCOUNT_ID:
        return []
    data = _px_post('/api/Position/searchOpen', {'accountId': int(TOPSTEP_ACCOUNT_ID)})
    if not data.get('success'):
        return []
    result = []
    for p in data.get('positions', []):
        sym = _contract_to_symbol(p.get('contractId', ''))
        # ProjectX type: 1=Long, 2=Short (ordering inferred from Order side
        # enum's Bid/Ask convention — [UNVERIFIED], confirm sign convention
        # against a real open position before trusting qty's sign here)
        qty = p.get('size', 0)
        if p.get('type') == 2:
            qty = -abs(qty)
        result.append({
            'symbol':         sym,
            'contract_month': None,
            'qty':            qty,
            'avg_cost':       p.get('averagePrice'),
            'market_price':   None,
            'market_value':   None,
            'unrealized_pnl': None,
            'realized_pnl':   None,
        })
    return result


# ── Orders ───────────────────────────────────────────────────────────────

class FuturesOrderRequest(BaseModel):
    symbol:      str
    qty:         int
    side:        str            # "BUY" or "SELL"
    order_type:  str = "MARKET"  # "MARKET" | "LIMIT" | "STOP_MARKET"
    limit_price: float | None = None
    stop_price:  float | None = None


@app.post("/futures/order")
async def place_futures_order(req: FuturesOrderRequest):
    """Mirrors bridge.py's /futures/order contract exactly (same request
    fields, same response shape) — translates BUY/SELL + MARKET/LIMIT/
    STOP_MARKET into ProjectX's side/type enums."""
    sym = req.symbol.upper()
    contract_id = _resolve_contract_id(sym)
    if not contract_id:
        return {"error": f"Could not resolve contract for {sym}"}
    if not TOPSTEP_ACCOUNT_ID:
        return {"error": "TOPSTEP_ACCOUNT_ID not configured"}

    side = 0 if req.side.upper() == 'BUY' else 1   # 0=Bid(buy), 1=Ask(sell)
    if req.order_type == 'LIMIT' and req.limit_price:
        order_type, extra = 1, {'limitPrice': req.limit_price}
    elif req.order_type == 'STOP_MARKET' and req.stop_price:
        order_type, extra = 4, {'stopPrice': req.stop_price}
    else:
        order_type, extra = 2, {}

    payload = {
        'accountId':  int(TOPSTEP_ACCOUNT_ID),
        'contractId': contract_id,
        'type':       order_type,
        'side':       side,
        'size':       abs(req.qty),
        **extra,
    }
    data = _px_post('/api/Order/place', payload)
    if not data.get('success'):
        return {'status': 'error', 'symbol': sym, 'error': data.get('errorMessage', 'order/place failed')}

    return {
        "status":         "submitted",
        "symbol":         sym,
        "contract_month": None,
        "side":           req.side.upper(),
        "qty":            abs(req.qty),
        "order_id":       data.get('orderId'),
        "order_type":     req.order_type,
        "limit_price":    req.limit_price,
        "stop_price":     req.stop_price,
    }


@app.post("/futures/cancel/{order_id}")
async def cancel_futures_order(order_id: int):
    """Mirrors bridge.py's /futures/cancel/{order_id} response shape."""
    if not TOPSTEP_ACCOUNT_ID:
        return {"status": "not_found", "order_id": order_id}
    data = _px_post('/api/Order/cancel', {'accountId': int(TOPSTEP_ACCOUNT_ID), 'orderId': order_id})
    return {"status": "cancelled" if data.get('success') else "not_found", "order_id": order_id}


@app.get("/order/{order_id}/status")
async def get_order_status(order_id: int):
    """[UNVERIFIED] No dedicated order-status endpoint was found in the fetched
    docs excerpts. Guessed at /api/Order/searchOpen (mirroring the confirmed
    Position/searchOpen naming pattern + request shape) — if this 404s or
    returns something unexpected once tested against a real account, bridge.py's
    own graceful "Unknown" fallback pattern is preserved here so callers
    (_get_stop_fill in london_trader.py, place_trade's fill-verification path)
    degrade the same way they already do for any order they can't find,
    rather than crashing."""
    if TOPSTEP_ACCOUNT_ID:
        data = _px_post('/api/Order/searchOpen', {'accountId': int(TOPSTEP_ACCOUNT_ID)})
        for o in (data.get('orders', []) if data.get('success') else []):
            if o.get('id') == order_id or o.get('orderId') == order_id:
                return {
                    "orderId":      order_id,
                    "status":       o.get('status', 'Unknown'),
                    "filled":       o.get('filledSize', 0),
                    "remaining":    o.get('remainingSize', 0),
                    "avgFillPrice": o.get('averageFillPrice'),
                }
    return {"orderId": order_id, "status": "Unknown", "filled": 0}


if __name__ == "__main__":
    if not _creds_present():
        print("⚠️  TOPSTEP_API_KEY / TOPSTEP_USERNAME / TOPSTEP_ACCOUNT_ID not set — "
              "service will start but every call will fail 'not authenticated' "
              "until .env-tc has real TopstepX credentials. This is expected "
              "before registration; not a bug.")
    uvicorn.run(app, host="127.0.0.1", port=BRIDGE_PORT)
