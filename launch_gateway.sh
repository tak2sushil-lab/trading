#!/bin/bash
# Starts IB Gateway via IBC using credentials from .env
# Run this BEFORE start_trading.sh (or it's called automatically by it)

TRADING_DIR="/Users/sushil/trading"
IBC_DIR="$HOME/ibc"
LOG_DIR="$TRADING_DIR/logs"

# ── Load .env ─────────────────────────────────────────────
set -a
source "$TRADING_DIR/.env"
set +a

# ── Guard: already running? ───────────────────────────────
if pgrep -f "java.*ibc/config.ini" > /dev/null 2>&1; then
    echo "IBC/Gateway already running — skipping launch"
    exit 0
fi

# ── Validate credentials ──────────────────────────────────
if [[ -z "$IBKR_USERNAME" || -z "$IBKR_PASSWORD" ]]; then
    echo "ERROR: IBKR_USERNAME or IBKR_PASSWORD not set in .env"
    exit 1
fi

# ── Pass credentials to IBC via env vars ──────────────────
export TWSUSERID="$IBKR_USERNAME"
export TWSPASSWORD="$IBKR_PASSWORD"
export TWS_MAJOR_VRSN="10.45"
export IBC_INI="$IBC_DIR/config.ini"
export IBC_PATH="$IBC_DIR"
export TWS_PATH="$HOME/Applications"
export TRADING_MODE="paper"
export TWOFA_TIMEOUT_ACTION="restart"
export LOG_PATH="$IBC_DIR/logs"
export JAVA_PATH=""   # use IB Gateway's bundled JRE

mkdir -p "$IBC_DIR/logs"

echo "Starting IB Gateway via IBC (paper)..."
nohup "$IBC_DIR/gatewaystartmacos.sh" -inline >> "$IBC_DIR/logs/ibc.log" 2>&1 &
IBC_PID=$!
echo "IBC started (PID $IBC_PID) — Gateway will be ready in ~30s"
echo "Log: $IBC_DIR/logs/ibc.log"
