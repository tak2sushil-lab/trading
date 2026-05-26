#!/bin/bash
# Wrapper for launchd — kills stale bridge on THIS environment's port, then starts fresh.
# Works for both UAT (port 8000) and Prod (port 8001) — BRIDGE_PORT comes from .env.
TRADING_DIR="$(cd "$(dirname "$0")" && pwd)"
set -a; source "$TRADING_DIR/.env"; set +a
BRIDGE_PORT="${BRIDGE_PORT:-8000}"

# Kill only the bridge process holding this environment's port (not the other env's bridge)
lsof -ti :"$BRIDGE_PORT" | xargs kill -9 2>/dev/null
sleep 2

exec "$TRADING_DIR/venv/bin/python" -u "$TRADING_DIR/bridge.py"
