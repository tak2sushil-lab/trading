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
# PRESET regime_l3 — choppy/cautious hard-skip and the T+5 check vs the live config:
#     ./research_replay_ab.sh regime_l3 rl3 2026-08-04 2026-08-31 2026-09-01 2026-09-29
#     venv/bin/python research_replay_score.py rl3 base
# PRESET voltrial — the volatility-stop trial review: live config, flat 5% stop vs volatility stop:
#     ./research_replay_ab.sh voltrial oct 2026-10-01 2026-10-14 2026-10-15 2026-10-28
#     venv/bin/python research_replay_score.py oct fixed
#
# ⚠️ Never start before 2026-08-04: before it bars_5m volume is the DataBento backfill (~2-5% of
# consolidated) while daily averages are consolidated, so the volume gate blocks almost everything.
# Every switch an arm does not set mirrors auto_trader.py as shipped (equity_replay prints them).
#
# ⚠️ Oct 1 2026 — a 10-process batch started at 15:20 slowed the live Clockwork job so much that its
# 15:40-15:49 entry run finished at 15:52 and entered nothing that night. So: this refuses to start
# during market hours (09:25-16:15 ET, weekdays) unless FORCE=1, and every replay runs under
# `taskpolicy -b` (macOS background QoS: lowest CPU and disk priority), so live services win.
# ⚠️ Never edit this file while an instance is running: zsh reads a script as it executes it, and
# an edit mid-run garbled the tail of a live run (Oct 1 2026). Copy it instead.
cd /Users/sushil/trading
PRESET=$1; TAG=$2
A_START=$3; A_END=$4; B_START=$5; B_END=$6
if [[ -z $B_END ]]; then echo "usage: $0 PRESET TAG A_START A_END B_START B_END"; exit 1; fi
NOW_ET=$(TZ=America/New_York date +%H%M); DOW=$(TZ=America/New_York date +%u)
if [[ $DOW -le 5 && $NOW_ET -ge 0925 && $NOW_ET -le 1615 && -z $FORCE ]]; then
  echo "refusing: market hours ($NOW_ET ET). Replays starve the live engines — run after 16:15 ET or set FORCE=1."
  exit 1
fi
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
  regime_l3)   # Oct 1 2026: choppy/cautious hard-skip and the T+5 check, vs the live config
    ARMS[base]=""
    ARMS[no_block]="--regime-hard-skip none"
    ARMS[block_choppy_only]="--regime-hard-skip CHOPPY"
    ARMS[block_cautious_only]="--regime-hard-skip CAUTIOUS"
    ARMS[no_l3]="--no-l3" ;;
  hod)   # Oct 1 2026: one consistent belief about the day's high (ranking vs Layer 2)
    ARMS[base]=""
    ARMS[rank_pullback]="--hod-rule pullback_first"
    ARMS[merged]="--hod-rule pullback_first --no-l2-hod"
    ARMS[pullback_only]="--hod-rule pullback_first --no-l2-hod --pullback-only"
    ARMS[no_l2]="--no-l2" ;;
  hod2)  # Oct 1 2026: keep the at-the-high preference but make Layer 2 a stall detector
    ARMS[stall_only]="--l2-hod-failed-only" ;;
  hodtrial)  # pullback_first went live Oct 2 2026 — review: live (base) vs the old at-the-high order
    ARMS[base]=""
    ARMS[at_high]="--hod-rule at_high_first" ;;
  *) echo "unknown preset $PRESET (sep30 | voltrial | regime_l3 | hod | hod2 | hodtrial)"; exit 1 ;;
esac
OUT=research_out/replay_$TAG
mkdir -p $OUT
for arm in ${(k)ARMS}; do
  for chunk in A B; do
    if [[ $chunk == A ]]; then S=$A_START; E=$A_END; else S=$B_START; E=$B_END; fi
    REPLAY_DB_SUFFIX="_${TAG}_${arm}_${chunk}" PYTHONUNBUFFERED=1 taskpolicy -b nice -n 10 venv/bin/python equity_replay.py \
      --start $S --end $E ${=ARMS[$arm]} > $OUT/${arm}_${chunk}.log 2>&1 &
  done
done
wait
echo "ALL DONE $(date) — score with: venv/bin/python research_replay_score.py $TAG <base arm>"
