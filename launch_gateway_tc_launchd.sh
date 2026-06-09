#!/bin/bash
# TC gateway launcher — IB Gateway for DUQ640500 on port 4003
# Separate from the main gateway (DU9952463 on 4002).

TRADING_DIR="$(cd "$(dirname "$0")" && pwd)"
IBC_DIR="$HOME/ibc"

# Load shared vars first, then TC overrides (IBKR credentials, port, settings path)
set -a
source "$TRADING_DIR/.env"
source "$TRADING_DIR/.env-tc"
set +a

if [[ -z "$IBKR_USERNAME" || -z "$IBKR_PASSWORD" || "$IBKR_PASSWORD" == "FILL_IN_HERE" ]]; then
    echo "ERROR: IBKR_USERNAME or IBKR_PASSWORD not set in .env-tc"
    exit 1
fi

export TWSUSERID="$IBKR_USERNAME"
export TWSPASSWORD="$IBKR_PASSWORD"
export TWS_MAJOR_VRSN="10.45"
export IBC_INI="$IBC_DIR/config-tc.ini"
export IBC_PATH="$IBC_DIR"
export TWS_PATH="${TWS_PATH:-$HOME/Applications}"
export TWS_SETTINGS_PATH="${TWS_SETTINGS_PATH:-$HOME/Jts-tc}"
export TWOFA_TIMEOUT_ACTION="restart"
export LOG_PATH="$IBC_DIR/logs"
export JAVA_PATH=""

pkill -f "$IBC_INI" 2>/dev/null
sleep 3

mkdir -p "$IBC_DIR/logs"
mkdir -p "$TRADING_DIR/logs"

echo "$(date): Starting TC gateway (DUQ640500, port 4003)..."
exec "$IBC_DIR/gatewaystartmacos.sh" -inline
