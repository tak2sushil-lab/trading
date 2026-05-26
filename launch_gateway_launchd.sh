#!/bin/bash
# Wrapper for launchd — starts IB Gateway via IBC in foreground
# launchd monitors this process; on crash launchd restarts automatically

TRADING_DIR="$(cd "$(dirname "$0")" && pwd)"
IBC_DIR="$HOME/ibc"

# Load credentials from .env — sets IBKR_USERNAME, IBKR_PASSWORD, TRADING_MODE, etc.
set -a
source "$TRADING_DIR/.env"
set +a

# Validate
if [[ -z "$IBKR_USERNAME" || -z "$IBKR_PASSWORD" ]]; then
    echo "ERROR: IBKR_USERNAME or IBKR_PASSWORD not set in .env"
    exit 1
fi

if [[ -z "$TRADING_MODE" ]]; then
    echo "ERROR: TRADING_MODE not set in .env (should be 'paper' or 'live')"
    exit 1
fi

# Export IBC environment — all values come from .env
export TWSUSERID="$IBKR_USERNAME"
export TWSPASSWORD="$IBKR_PASSWORD"
export TWS_MAJOR_VRSN="10.45"
export IBC_INI="${IBC_INI:-$IBC_DIR/config.ini}"
export IBC_PATH="$IBC_DIR"
export TWS_PATH="${TWS_PATH:-$HOME/Applications}"
export TWS_SETTINGS_PATH="${TWS_SETTINGS_PATH:-}"
export TWOFA_TIMEOUT_ACTION="restart"
export LOG_PATH="$IBC_DIR/logs"
export JAVA_PATH=""

# Kill only the stale process for THIS env's config — not the other env's gateway
pkill -f "$IBC_INI" 2>/dev/null
sleep 3

mkdir -p "$IBC_DIR/logs"
mkdir -p "$TRADING_DIR/logs"

echo "$(date): Starting IB Gateway via IBC (mode=$TRADING_MODE, foreground for launchd)..."

# exec replaces this shell with gatewaystartmacos.sh — launchd monitors it
# -inline keeps the process in foreground so launchd can track and restart
exec "$IBC_DIR/gatewaystartmacos.sh" -inline
