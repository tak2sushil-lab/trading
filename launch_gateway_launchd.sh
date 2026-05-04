#!/bin/bash
# Wrapper for launchd — starts IB Gateway via IBC in foreground
# launchd monitors this process; on crash launchd restarts automatically

TRADING_DIR="/Users/sushil/trading"
IBC_DIR="$HOME/ibc"

# Load credentials from .env
set -a
source "$TRADING_DIR/.env"
set +a

# Validate
if [[ -z "$IBKR_USERNAME" || -z "$IBKR_PASSWORD" ]]; then
    echo "ERROR: IBKR_USERNAME or IBKR_PASSWORD not set in .env"
    exit 1
fi

# Kill any stale IBC/Gateway process before starting fresh
pkill -f "ibcalpha.ibc.IbcGateway" 2>/dev/null
sleep 3

# Export IBC environment
export TWSUSERID="$IBKR_USERNAME"
export TWSPASSWORD="$IBKR_PASSWORD"
export TWS_MAJOR_VRSN="10.45"
export IBC_INI="$IBC_DIR/config.ini"
export IBC_PATH="$IBC_DIR"
export TWS_PATH="$HOME/Applications"
export TRADING_MODE="paper"
export TWOFA_TIMEOUT_ACTION="restart"
export LOG_PATH="$IBC_DIR/logs"
export JAVA_PATH=""

mkdir -p "$IBC_DIR/logs"
mkdir -p "$TRADING_DIR/logs"

echo "$(date): Starting IB Gateway via IBC (paper, foreground for launchd)..."

# exec replaces this shell with gatewaystartmacos.sh — launchd monitors it
# -inline keeps the process in foreground so launchd can track and restart
exec "$IBC_DIR/gatewaystartmacos.sh" -inline
