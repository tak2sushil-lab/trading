#!/bin/bash
# Independent watchdog — runs every 5 min from launchd, OUTSIDE the traders, so it
# still fires when they are dead. That independence is the point: a watchdog inside
# the process it watches cannot report that process dying.
TRADING_DIR="/Users/sushil/trading"
set -a; source "$TRADING_DIR/.env"; set +a
exec "$TRADING_DIR/venv/bin/python" -m futures.heartbeat --check
