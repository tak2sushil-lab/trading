#!/usr/bin/env python3
"""
close_one_option.py — One-off script to close a single orphaned options spread via bridge.
These positions are CLOSED in DB (SYSTEM_RESET) but still open in IBKR paper.
Does NOT update DB. Just places the close order and confirms fill.

Usage: venv/bin/python close_one_option.py <SYMBOL>
"""

import sys, time, requests

BRIDGE = 'http://localhost:8000'

# All orphaned spreads still open in IBKR (DB already shows them as CLOSED)
POSITIONS = {
    'C':    {'strategy': 'BULL_SPREAD', 'expiry': '20260717', 'long_strike': 145.0, 'short_strike': 155.0, 'right': 'C', 'contracts': 2},
    'QBTS': {'strategy': 'BULL_SPREAD', 'expiry': '20260717', 'long_strike': 27.0,  'short_strike': 33.0,  'right': 'C', 'contracts': 5},
    'IONQ': {'strategy': 'BULL_SPREAD', 'expiry': '20260717', 'long_strike': 65.0,  'short_strike': 80.0,  'right': 'C', 'contracts': 4},
    'IREN': {'strategy': 'BULL_SPREAD', 'expiry': '20260717', 'long_strike': 70.0,  'short_strike': 85.0,  'right': 'C', 'contracts': 5},
    'HAL':  {'strategy': 'BULL_SPREAD', 'expiry': '20260717', 'long_strike': 42.0,  'short_strike': 45.0,  'right': 'C', 'contracts': 5},
    'AMZN': {'strategy': 'BULL_SPREAD', 'expiry': '20260717', 'long_strike': 255.0, 'short_strike': 275.0, 'right': 'C', 'contracts': 2},
    'AFRM': {'strategy': 'BULL_SPREAD', 'expiry': '20260724', 'long_strike': 77.0,  'short_strike': 90.0,  'right': 'C', 'contracts': 4},
    'NVDA': {'strategy': 'BULL_SPREAD', 'expiry': '20260724', 'long_strike': 220.0, 'short_strike': 240.0, 'right': 'C', 'contracts': 2},
    'SOFI': {'strategy': 'BULL_SPREAD', 'expiry': '20260724', 'long_strike': 18.5,  'short_strike': 21.0,  'right': 'C', 'contracts': 5},
}

def get_quote(sym, expiry, strike, right):
    try:
        r = requests.get(f'{BRIDGE}/options/quote/{sym}/{expiry}/{strike}/{right}', timeout=10)
        return r.json() if r.ok else None
    except Exception as e:
        print(f'  Quote error: {e}')
        return None

def close_spread(sym):
    t = POSITIONS.get(sym)
    if not t:
        print(f'Unknown symbol: {sym}. Available: {", ".join(POSITIONS)}')
        return

    print(f'\n── Closing {sym} {t["strategy"]} {t["long_strike"]}/{t["short_strike"]} {t["right"]} x{t["contracts"]} ──')

    lq = get_quote(sym, t['expiry'], t['long_strike'], t['right'])
    sq = get_quote(sym, t['expiry'], t['short_strike'], t['right'])
    if not lq or not sq:
        print('  ❌ Could not get quotes — aborting')
        return

    long_bid  = lq.get('bid') or 0
    long_ask  = lq.get('ask') or 0
    short_bid = sq.get('bid') or 0
    short_ask = sq.get('ask') or 0
    long_mid  = (long_bid + long_ask) / 2
    short_mid = (short_bid + short_ask) / 2

    natural_credit = round(long_bid - short_ask, 2)
    spread_mid     = round(long_mid - short_mid, 2)
    limit_credit   = max(0.01, natural_credit)

    print(f'  Long  {t["long_strike"]}{t["right"]}:  bid={long_bid}  ask={long_ask}')
    print(f'  Short {t["short_strike"]}{t["right"]}: bid={short_bid} ask={short_ask}')
    print(f'  Natural credit: ${natural_credit:.2f}  |  Mid: ${spread_mid:.2f}  |  Limit: ${limit_credit:.2f}')
    print(f'  Expected receive: ${round(limit_credit * 100 * t["contracts"], 2):.2f}')

    # Close as two individual single-leg orders to avoid IBKR's BAG "riskless combo" limit.
    # SELL the long leg (145C), BUY the short leg (155C) — same net result as a combo.
    legs = [
        {'action': 'SELL', 'strike': t['long_strike'],  'price': long_bid,  'label': f"long {t['long_strike']}{t['right']}"},
        {'action': 'BUY',  'strike': t['short_strike'], 'price': short_ask, 'label': f"short {t['short_strike']}{t['right']}"},
    ]

    order_ids = []
    for leg in legs:
        payload = {
            'symbol':      sym,
            'expiry':      t['expiry'],
            'strike':      leg['strike'],
            'right':       t['right'],
            'qty':         t['contracts'],
            'action':      leg['action'],
            'order_type':  'LIMIT',
            'limit_price': leg['price'],
        }
        print(f'\n  → {leg["action"]} {leg["label"]} x{t["contracts"]} @ {leg["price"]}...')
        try:
            r = requests.post(f'{BRIDGE}/options/order', json=payload, timeout=60)
            resp = r.json()
        except Exception as e:
            print(f'  ❌ Bridge error: {e}')
            return

        print(f'  Bridge response: {resp}')
        if 'orderId' not in resp:
            print(f'  ❌ No orderId — aborting')
            return
        order_ids.append(resp['orderId'])
        time.sleep(2)  # small gap between legs

    print(f'\n  Both legs submitted: {order_ids} — waiting 30s for fills...')
    time.sleep(30)

    all_filled = True
    for oid in order_ids:
        try:
            sr = requests.get(f'{BRIDGE}/order/{oid}/status', timeout=10)
            st_data = sr.json() if sr.ok else {}
        except Exception:
            st_data = {}
        st = st_data.get('status', 'Unknown')
        filled_qty = st_data.get('filled', 0)
        print(f'  Order #{oid}: status={st} filled={filled_qty}')
        if st not in ('Filled',) and filled_qty < t['contracts']:
            all_filled = False

    if all_filled:
        print(f'\n  ✅ {sym} spread CLOSED successfully (both legs filled)')
    else:
        # Portfolio fallback
        try:
            pr = requests.get(f'{BRIDGE}/portfolio/options', timeout=10)
            opts = pr.json() if pr.ok else []
            still_open = any(
                p.get('symbol') == sym
                and p.get('expiry') == t['expiry']
                and abs(float(p.get('strike', 0)) - t['long_strike']) < 0.01
                and float(p.get('qty', 0)) > 0
                for p in opts
            )
            if not still_open:
                print(f'\n  ✅ {sym} long leg gone from IBKR portfolio — close confirmed')
            else:
                print(f'\n  ⚠️  Not all legs confirmed filled. Check IBKR portal manually.')
        except Exception as e:
            print(f'  Portfolio check error: {e}')

if __name__ == '__main__':
    sym = sys.argv[1].upper() if len(sys.argv) > 1 else 'C'
    close_spread(sym)
