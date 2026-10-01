#!/bin/zsh
# Usage: ./watch_daytrader.sh [MAX_WAIT_SECONDS=480] [FROM_LOG_LINE]
#   Start with FROM = $(wc -l < logs/auto_trader.log) to see only new events; each run prints
#   NEXTLINE=<n> for the next call. Used for the Oct 1-2 2026 live watches.
# Day-trader live watch: waits up to MAX_WAIT seconds for new decision lines (entries, Layer 2/3,
# exits, trail moves, errors) after line FROM, then prints them
# plus open positions. Prints NEXTLINE=<n> for the next call.
cd /Users/sushil/trading
MAXWAIT=${1:-480}; FROM=${2:-1}
PAT='trail →|Break-even|PARTIAL|SCAN \| Regime|Found [0-9]+ valid|🎯|⚡ A|L2 |L3 |SKIP [A-Z]|NEW TRADES|FAST EXIT|→ EXIT|Stop .* hit|position confirmed|No-move|VWAP cross|Momentum fade|Circuit|Regime flip|Daily max loss|Afternoon gate|Recycled slot|Max open|Capital cap|Scan error|Traceback|Exit error|Bridge unreachable|Gateway'
t=0
while (( t < MAXWAIT )); do
  n=$(tail -n +$FROM logs/auto_trader.log | grep -E "$PAT" | grep -vc "PRE-MKT\|TG poll\|Found 0 valid\|SCAN | Regime")
  (( n > 0 )) && break
  sleep 15; (( t += 15 ))
done
sleep 5
TOTAL=$(wc -l < logs/auto_trader.log | tr -d ' ')
tail -n +$FROM logs/auto_trader.log | grep -E "$PAT" | grep -v "PRE-MKT\|TG poll" | cut -c1-170
echo "--- open positions @ $(date +%H:%M:%S):"
sqlite3 trades.db "SELECT id, symbol, setup_type, entry_time, entry_price, stop_price, shares FROM trades WHERE status='OPEN'" | while IFS='|' read id sym st et ep sp sh; do
  px=$(curl -s -m 5 "localhost:8000/quote/$sym" | /Users/sushil/trading/venv/bin/python -c "import json,sys; d=json.load(sys.stdin); print(d.get('last') or d.get('price') or d.get('close') or '')" 2>/dev/null)
  echo "  #$id $sym $st in@$et $ep stop $sp x$sh | now ${px:-?}"
done
echo "--- closed today: $(sqlite3 trades.db "SELECT COUNT(*)||' trades, P&L $'||ROUND(SUM(pnl),2) FROM trades WHERE entry_date=date('now','localtime') AND status!='OPEN'")"
echo "NEXTLINE=$((TOTAL+1))"
