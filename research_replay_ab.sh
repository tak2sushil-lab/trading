#!/bin/zsh
# Day-trader replay A/B — every arm runs the REAL auto_trader decision chain via equity_replay.py,
# split into two date chunks so arms run in parallel at low priority (live services keep the CPU).
#
#   ./research_replay_ab.sh PRESET TAG A_START A_END B_START B_END
#   venv/bin/python research_replay_score.py TAG BASE_ARM
#
# PRESET sep30    — reproduces the Sep 30 2026 review exactly (that night the override was ON and
#                   the volatility stop OFF in live code):
#     ./research_replay_ab.sh sep30 sep30 2026-08-04 2026-08-31 2026-09-01 2026-09-29
#     venv/bin/python research_replay_score.py sep30 base
# PRESET voltrial — the volatility-stop trial review: live config, flat 5% stop vs volatility stop:
#     ./research_replay_ab.sh voltrial oct 2026-10-01 2026-10-14 2026-10-15 2026-10-28
#     venv/bin/python research_replay_score.py oct fixed
#
# ⚠️ Never start before 2026-08-04: before it bars_5m volume is the DataBento backfill (~2-5% of
# consolidated) while daily averages are consolidated, so the volume gate blocks almost everything.
# Every switch an arm does not set mirrors auto_trader.py as shipped (equity_replay prints them).
cd /Users/sushil/trading
PRESET=$1; TAG=$2
A_START=$3; A_END=$4; B_START=$5; B_END=$6
if [[ -z $B_END ]]; then echo "usage: $0 PRESET TAG A_START A_END B_START B_END"; exit 1; fi
typeset -A ARMS
case $PRESET in
  sep30)
    ARMS[base]="--override --no-vol-risk"
    ARMS[fresh]="--override --no-vol-risk --fresh-max-5d 18"
    ARMS[vol]="--override --vol-risk"
    ARMS[thrust]="--no-override --no-vol-risk --thrust-priority"
    ARMS[all]="--no-override --vol-risk --thrust-priority --fresh-max-5d 18" ;;
  voltrial)
    ARMS[fixed]="--no-vol-risk"
    ARMS[vol]="--vol-risk" ;;
  *) echo "unknown preset $PRESET (sep30 | voltrial)"; exit 1 ;;
esac
OUT=research_out/replay_$TAG
mkdir -p $OUT
for arm in ${(k)ARMS}; do
  for chunk in A B; do
    if [[ $chunk == A ]]; then S=$A_START; E=$A_END; else S=$B_START; E=$B_END; fi
    REPLAY_DB_SUFFIX="_${TAG}_${arm}_${chunk}" PYTHONUNBUFFERED=1 nice -n 10 venv/bin/python equity_replay.py \
      --start $S --end $E ${=ARMS[$arm]} > $OUT/${arm}_${chunk}.log 2>&1 &
  done
done
wait
echo "ALL DONE $(date) — score with: venv/bin/python research_replay_score.py $TAG <base arm>"
