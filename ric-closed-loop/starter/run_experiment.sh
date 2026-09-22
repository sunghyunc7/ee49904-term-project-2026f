#!/usr/bin/env bash
# EE49904 — one experiment = one fresh RIC + one fresh E2 node + one xApp run.
#
#   source ~/ee49904-ric/env.sh
#   bash run_experiment.sh --open-loop --duration 60 --out open.csv
#   bash run_experiment.sh --gain 2.0 --period 100 --duration 60 --out closed.csv
#   bash run_experiment.sh --ind-period 2 --gain 2.0 --period 100 --out ind2.csv
#
# Every argument is handed to the xApp unchanged. The script only adds what is around it:
#   1. kills any nearRT-RIC / emu_agent_gnb left from an earlier run (a busy SCTP port looks like a broken build)
#   2. starts the RIC, then the E2 node with EE49904_PLANT_DT_MS = your --ind-period (default 10),
#      so that one plant tick lasts exactly one indication period
#   3. runs the xApp once
#   4. stops both, and tells you if either of them died before the xApp finished
#
# Why a fresh session each time: upstream FlexRIC has a bug in which a pending event left behind by one
# xApp is touched by the next xApp's first CONTROL ACK (assoc_rb_tree_extract: Assertion failed, SIGABRT).
# Open-loop followed by closed-loop in the same RIC session hits it in roughly six runs out of ten.
# One session per xApp avoids it, and also gives every run the same initial plant state.
#
# Other knobs (environment):
#   XAPP=my_xapp.py     run a different xApp file (default: xapp_slice_ctrl.py next to this script)
#   LOGDIR=some/dir     where ric.log, agent.log and xapp.log go (default: ./logs/<timestamp>)
#   QUIET=0             show everything on the terminal. By default the five lines the SDK prints for every
#                       control message are hidden here so the xApp's table stays readable; xapp.log always
#                       has the full output.
#
# A sweep is then an ordinary loop:
#   for g in 1 2 5 10 20 50; do bash run_experiment.sh --gain $g --period 100 --out g$g.csv; done

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
B="${RIC_BUILD:?RIC_BUILD is not set — run: source ~/ee49904-ric/env.sh}"
SDK="${XAPP_SDK_PATH:-$B/src/xApp/swig}"
XAPP="${XAPP:-$HERE/xapp_slice_ctrl.py}"
LOGDIR="${LOGDIR:-$PWD/logs/$(date +%Y%m%d-%H%M%S)}"
[ -f "$XAPP" ] || { echo "[run] xApp not found: $XAPP" >&2; exit 2; }
[ $# -gt 0 ]   || { sed -n '2,8p' "$0"; exit 2; }

# --ind-period N  or  --ind-period=N  decides the plant tick. The xApp validates the value itself.
IND=10; DUR=60; prev=""
for a in "$@"; do
  case "$prev" in --ind-period) IND="$a";; --duration) DUR="$a";; esac
  case "$a" in --ind-period=*) IND="${a#*=}";; --duration=*) DUR="${a#*=}";; esac
  prev="$a"
done
case "$IND" in 1|2|5|10) ;; *) echo "[run] --ind-period must be 1, 2, 5 or 10 (got '$IND')" >&2; exit 2;; esac

# Terminal view only. The SDK's C code prints these for every control message — 5 lines per decision.
CHATTER='^(ADD NVS DL SLICE|DEL DL SLICE|\[xApp\]: CONTROL|\[xApp\]: Successfully received CONTROL-ACK)'
show(){ if [ "${QUIET:-1}" = "0" ]; then cat; else grep --line-buffered -v -E "$CHATTER"; fi; }

R=""; A=""
stop_all(){
  [ -n "$A" ] && kill "$A" 2>/dev/null
  [ -n "$R" ] && kill "$R" 2>/dev/null
  pkill -x nearRT-RIC 2>/dev/null; pkill -x emu_agent_gnb 2>/dev/null
  wait 2>/dev/null
}
# pkill -x matches the process name only. -f would also kill an editor or a `tail -f` whose
# command line merely mentions one of these names.
trap stop_all EXIT
trap 'exit 130' INT TERM

mkdir -p "$LOGDIR"
pkill -x nearRT-RIC 2>/dev/null; pkill -x emu_agent_gnb 2>/dev/null; sleep 1

# `exec` inside the subshell makes $! the PID of the binary itself, not of a wrapper shell.
( cd "$B" && exec ./examples/ric/nearRT-RIC ) >"$LOGDIR/ric.log" 2>&1 & R=$!
sleep 4
kill -0 "$R" 2>/dev/null || { echo "[run] nearRT-RIC did not start:" >&2; tail -8 "$LOGDIR/ric.log" >&2; exit 3; }
( cd "$B" && EE49904_PLANT_DT_MS="$IND" exec ./examples/emulator/agent/emu_agent_gnb ) >"$LOGDIR/agent.log" 2>&1 & A=$!
sleep 6
kill -0 "$A" 2>/dev/null || { echo "[run] emu_agent_gnb did not start:" >&2; tail -8 "$LOGDIR/agent.log" >&2; exit 3; }
echo "[run] RIC pid $R, E2 node pid $A, plant tick ${IND} ms, logs in $LOGDIR"

# timeout: the SDK can hang on exit if the RIC went away underneath it.
T=$(awk -v d="$DUR" 'BEGIN{printf "%d", d + 45}')
XAPP_SDK_PATH="$SDK" PYTHONFAULTHANDLER=1 PYTHONUNBUFFERED=1 \
  timeout "$T" python3 "$XAPP" "$@" 2>&1 | tee "$LOGDIR/xapp.log" | show
RC=${PIPESTATUS[0]}

kill -0 "$A" 2>/dev/null || { echo "[run] ! emu_agent_gnb died during the run — see $LOGDIR/agent.log" >&2; [ "$RC" -eq 0 ] && RC=4; }
kill -0 "$R" 2>/dev/null || { echo "[run] ! nearRT-RIC died during the run — see $LOGDIR/ric.log" >&2;       [ "$RC" -eq 0 ] && RC=4; }
case "$RC" in
  0)   ;;
  124) echo "[run] ! the xApp was killed after ${T} s (timeout)" >&2;;
  134) echo "[run] ! the xApp aborted (SIGABRT). The assert and the Python line are in $LOGDIR/xapp.log" >&2;;
  *)   echo "[run] ! exit code $RC" >&2;;
esac
NADD=$(grep -c 'SLICE ADD' "$LOGDIR/agent.log" 2>/dev/null); NCL=$(grep -c 'CLAMPED' "$LOGDIR/agent.log" 2>/dev/null)
echo "[run] agent log: ${NADD:-0} SLICE ADD, ${NCL:-0} CLAMPED"
exit "$RC"
