# test_connection.py — run this FIRST to verify IB Gateway is working
# Command: python test_connection.py

from ib_async import IB

ib = IB()
try:
    ib.connect('127.0.0.1', 4002, clientId=99)
    print("✅ Connected to IB Gateway!")
    print(f"   Account: {ib.wrapper.accounts}")

    positions = ib.positions()
    print(f"   Positions: {len(positions)} holdings")
    print("✅ Everything looks good — ready to trade!")

except Exception as e:
    print(f"❌ Connection failed: {e}")
    print("   → Is IB Gateway running?")
    print("   → Is port 4002 enabled in Gateway API settings?")
    print("   → Is 127.0.0.1 in Trusted IPs?")
finally:
    ib.disconnect()
    print("   Disconnected.")