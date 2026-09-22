#!/usr/bin/env bash
# EE49904 — diagnostic script that extracts the cause in one go when the closed loop dies (for TAs/instructor)
#
#   source ~/ee49904-ric/env.sh && bash debug_closed_loop.sh
#
# Unlike smoke_test.sh it passes no verdict. **It shows all output as is.**
# When the xApp dies with SIGABRT the cause is one of three, and the logs tell them apart:
#   (a) the agent died first          → assert/core at the end of agent.log
#   (b) assert inside the SDK         → "Assertion ... failed" in closed.log
#   (c) heap error (free/malloc)      → "free(): ..." or "malloc(): ..." in closed.log
# faulthandler is enabled, so for (b)(c) the Python stack is printed as well.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
B="${RIC_BUILD:?source env.sh first}"
SDK="${XAPP_SDK_PATH:-$B/src/xApp/swig}"
T=$(mktemp -d)
DUR="${DUR:-40}"

cleanup(){ kill ${R:-0} ${A:-0} 2>/dev/null
           pkill -f '[n]earRT-RIC' 2>/dev/null; pkill -f '[e]mu_agent_gnb' 2>/dev/null; }
trap cleanup EXIT

echo "== Cleaning up leftover processes =="
pkill -f '[n]earRT-RIC' 2>/dev/null; pkill -f '[e]mu_agent_gnb' 2>/dev/null; sleep 1

echo "== Starting RIC / E2 node =="
( cd "$B"; exec ./examples/ric/nearRT-RIC ) >"$T/ric.log" 2>&1 & R=$!
sleep 4
( cd "$B"; exec ./examples/emulator/agent/emu_agent_gnb ) >"$T/agent.log" 2>&1 & A=$!
sleep 6
kill -0 $R 2>/dev/null && kill -0 $A 2>/dev/null || { echo "startup failed"; tail -20 "$T/ric.log" "$T/agent.log"; exit 1; }
echo "  ok (RIC pid $R, agent pid $A)"

run(){   # run <label> <extra args...>
  local lab="$1"; shift
  echo
  echo "=============================================================="
  echo " $lab"
  echo "=============================================================="
  XAPP_SDK_PATH="$SDK" PYTHONFAULTHANDLER=1 \
    timeout $((DUR+30)) python3 "$HERE/xapp_slice_ctrl.py" "$@" \
    --duration "$DUR" --out "$T/${lab}.csv" >"$T/${lab}.log" 2>&1
  local rc=$?
  echo "  exit code $rc  $( [ $rc -ge 128 ] && echo "(signal $((rc-128)) — 134=SIGABRT, 139=SIGSEGV)" )"
  echo "  --- last 30 lines of the xApp log ---"
  tail -30 "$T/${lab}.log" | sed 's/^/    /'
  if [ -f "$T/${lab}.csv" ]; then
    echo "  --- CSV $(( $(wc -l < "$T/${lab}.csv") - 1 )) rows, peak backlog ---"
    python3 - "$T/${lab}.csv" <<'PY' | sed 's/^/    /'
import csv,sys
rows=list(csv.DictReader(open(sys.argv[1])))
tot=[int(r["backlog0_bytes"])+int(r["backlog1_bytes"]) for r in rows]
sh=[float(r["share0"]) for r in rows]
print(f"peak {max(tot)/1e6:.2f} MB / mean {sum(tot)/len(tot)/1e6:.2f} MB")
print(f"share0 {min(sh):.1f}% ~ {max(sh):.1f}%")
PY
  else
    echo "  No CSV — the xApp died before writing it."
  fi
  echo "  --- last 15 lines of agent.log ---"
  tail -15 "$T/agent.log" | sed 's/^/    /'
  echo "  --- agent alive? ---"
  kill -0 $A 2>/dev/null && echo "    alive" || echo "    ★ dead — this is the cause"
}

run open   --open-loop
run closed --gain 2.0 --period 100

echo
echo "=============================================================="
echo " Agent log summary"
echo "=============================================================="
printf "  plant reset  : %s times\n" "$(grep -c 'plant reset' "$T/agent.log" 2>/dev/null)"
printf "  SLICE ADD    : %s times\n" "$(grep -c 'SLICE ADD'   "$T/agent.log" 2>/dev/null)"
echo "  applied share0, first/last 5:"
grep 'SLICE ADD' "$T/agent.log" 2>/dev/null | sed -n 's/.*applied \([0-9.]*\)\/.*/\1/p' \
  | { head -5; echo "    ..."; } | sed 's/^/    /'
grep 'SLICE ADD' "$T/agent.log" 2>/dev/null | sed -n 's/.*applied \([0-9.]*\)\/.*/\1/p' \
  | tail -5 | sed 's/^/    /'
echo
echo "  Full logs: $T   (not deleted when this shell exits)"
trap - EXIT; cleanup
