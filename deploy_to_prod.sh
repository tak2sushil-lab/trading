#!/bin/bash
# deploy_to_prod.sh — copy UAT codebase to Prod
# Always dry-run first: ./deploy_to_prod.sh --dry-run
# Then deploy: ./deploy_to_prod.sh
#
# What is NOT deployed (stays prod-specific):
#   .env          — prod has its own credentials + ports
#   *.db *.sqlite — prod accumulates its own trade history
#   venv/         — prod uses its own venv (or symlink)
#   logs/         — separate log streams
#   .git/         — not a git repo in prod
#   __pycache__   — rebuilt on first run

set -e
SRC="/Users/sushil/trading/"
DST="/Users/sushil/trading-prod/"

DRY_RUN=""
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN="--dry-run"
    echo ""
    echo "  ── DRY RUN — no files will be changed ──"
fi

echo ""
echo "  Syncing UAT → Prod"
echo "  From: $SRC"
echo "  To:   $DST"
echo ""

rsync -av $DRY_RUN \
    --exclude='.git/' \
    --exclude='.env' \
    --exclude='*.db' \
    --exclude='*.sqlite' \
    --exclude='venv/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='logs/' \
    --exclude='velocity_logs/' \
    --exclude='sim_tune_cache*' \
    "$SRC" "$DST"

if [[ -n "$DRY_RUN" ]]; then
    echo ""
    echo "  Dry run complete. Run without --dry-run to deploy."
    exit 0
fi

echo ""
echo "  ✓ Deploy complete."
echo ""
echo "  Next: restart prod services"
echo "    launchctl kickstart -k gui/\$(id -u)/com.sushil.trading-prod.bridge"
echo "    launchctl kickstart -k gui/\$(id -u)/com.sushil.trading-prod.autotrader"
echo "  (Do NOT restart the gateway unless you intend to — it forces a re-login)"
