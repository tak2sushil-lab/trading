# tunnel.py — ngrok tunnel for WhatsApp webhook
# Reads ngrok token from .env file
# Command: python tunnel.py

from dotenv import load_dotenv
load_dotenv()  # loads .env file from same folder

import os
from pyngrok import conf, ngrok

# ── Load token from .env ──────────────────────────────────
NGROK_TOKEN = os.getenv("NGROK_TOKEN")

if not NGROK_TOKEN:
    print("❌ NGROK_TOKEN not found in .env file!")
    print("   Add this line to your .env file:")
    print("   NGROK_TOKEN=your-token-here")
    exit()

# ── Start tunnel ──────────────────────────────────────────
conf.get_default().auth_token = NGROK_TOKEN

print("\n🌐 Starting ngrok tunnel...")
tunnel = ngrok.connect(8000)

print("=" * 50)
print(f"✅ Tunnel URL: {tunnel.public_url}")
print(f"✅ Webhook:    {tunnel.public_url}/whatsapp")
print("=" * 50)
print("\n⚠  Copy the Webhook URL above into Twilio:")
print("   Messaging → Try it out → Send a WhatsApp message")
print("   → Sandbox Settings → When a message comes in")
print("\nKeep this window open — press CTRL+C to stop\n")

try:
    import signal, time
    signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    while True:
        time.sleep(60)
except KeyboardInterrupt:
    print("\n🌐 Tunnel stopped.")
    ngrok.disconnect(tunnel.public_url)
