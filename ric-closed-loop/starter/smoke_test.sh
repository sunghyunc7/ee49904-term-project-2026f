#!/usr/bin/env bash
# EE49904 — RIC closed-loop control starter kit smoke test
#
#   source ~/ee49904-ric/env.sh && bash smoke_test.sh
#
# 8 checks, about 3-4 minutes. The last three check **whether the patch really closed the loop**.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PATCHDIR="$(cd "$HERE/../patch" && pwd)"
# This kit relies on environment variables (RIC_ROOT · RIC_BUILD · XAPP_SDK_PATH), not on a venv.
# The defaults match those of setup.sh, so it usually just works, but a student who ran setup.sh with another
# path and did not source env.sh **looks in the wrong place and gets "build outputs missing"**.
# If the environment is not set yet and env.sh exists, source it on their behalf, but print that we did so
# (fixing it silently means the student never learns the state of their own shell).
if [ -z "${RIC_ROOT:-}" ]; then
  ENVFILE="$HOME/ee49904-ric/env.sh"
  if [ -f "$ENVFILE" ]; then
    set +u; . "$ENVFILE"; set -u
    echo "  (Environment enabled for this run only: source $ENVFILE)"
    echo "  Your own shell needs that line too — xapp_slice_ctrl.py will not do it for you."
  fi
fi
ROOT="${RIC_ROOT:-$HOME/ee49904-ric}"
B="${RIC_BUILD:-$ROOT/flexric/build}"
SDK="${XAPP_SDK_PATH:-$B/src/xApp/swig}"
# setup.sh ran sudo make install, so the default paths are used.
# (The Python xApp cannot take -p, which is why install is mandatory.)
SMDIR="/usr/local/lib/flexric"
CONF="/usr/local/etc/flexric/flexric.conf"
ARGS=""
TMP=$(mktemp -d); PASS=0; FAIL=0
ok(){ printf "\r  \033[32mpass\033[0m  %s\n" "$1"; PASS=$((PASS+1)); }
no(){ printf "  \033[31mFAIL\033[0m  %s\n     → %s\n" "$1" "$2"; FAIL=$((FAIL+1)); }
# A bare exit code means nothing to a student. Name only the ones that come up often.
xrc_hint(){
  case "${1:-}" in
    0)   printf '' ;;
    124) printf ' = timeout' ;;
    134) printf ' = SIGABRT' ;;
    139) printf ' = SIGSEGV' ;;
    137) printf ' = SIGKILL' ;;
    *)   printf '' ;;
  esac
}

# These two processes hold SCTP ports. If they survive an earlier run, the next run's
# nearRT-RIC dies in bind() (assert `rc != -1` in endpoint_ric.c).
# Clean up by name as well to be sure — PIDs alone miss subshells and orphaned processes.
reap(){ pkill -f '[n]earRT-RIC' 2>/dev/null; pkill -f '[e]mu_agent_gnb' 2>/dev/null; sleep 1; }
kill_if(){ [ -n "${1:-}" ] && [ "${1:-0}" -gt 1 ] 2>/dev/null && kill "$1" 2>/dev/null; return 0; }
cleanup(){ kill_if "${RIC_PID:-}"; kill_if "${AG_PID:-}"; reap; wait 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT

# List of listening SCTP endpoints (for diagnostics)
sctp_eps(){ ss -lna --sctp 2>/dev/null | tail -n +2 || sed -n '2,$p' /proc/net/sctp/eps 2>/dev/null; }

echo "=============================================================="
echo " RIC closed-loop starter kit smoke test"
echo " RIC_ROOT=$ROOT"
echo "=============================================================="

# --- 1. Build outputs + service models ---
echo; echo "[1/8] Build outputs"
MISSING=""
for x in examples/ric/nearRT-RIC examples/emulator/agent/emu_agent_gnb; do
  [ -x "$B/$x" ] || MISSING="$MISSING $x"
done
NSM=$(ls "$SMDIR"/*_sm.so 2>/dev/null | wc -l)
if [ -z "$MISSING" ] && [ "$NSM" -ge 5 ] && [ -f "$CONF" ]; then
  ok "executables + $NSM installed service models ($SMDIR)"
else
  no "build/install outputs" "missing:$MISSING / $NSM SMs in $SMDIR / conf $( [ -f "$CONF" ] && echo present || echo missing).
     Run setup.sh again (including sudo make install)."
fi

# --- 2. Is the patch applied? ---
echo; echo "[2/8] Stateful plant patch applied?"
AG="$ROOT/flexric/examples/emulator/agent"
if [ -f "$AG/ee49904_plant.c" ] && grep -q "ee49904_plant.h" "$AG/sm_mac.c" 2>/dev/null \
   && grep -q "ee_plant_set_shares" "$AG/sm_slice.c" 2>/dev/null; then
  ok "sm_mac.c · sm_slice.c are wired to the plant"
else
  no "patch not applied" "Run bash ../patch/apply_patch.sh $ROOT/flexric, then rebuild.
     (Without the patch the indications are random numbers and the closed loop never closes.)"
fi

# --- 3. Standalone plant verification (no SCTP needed) ---
echo; echo "[3/8] Standalone plant verification"
if cc -O2 -o "$TMP/test_plant" "$PATCHDIR/test_plant.c" "$PATCHDIR/ee49904_plant.c" \
      -I"$PATCHDIR" -lm -lpthread 2>"$TMP/cc.log"; then
  if "$TMP/test_plant" > "$TMP/plant.log" 2>&1; then
    grep -E "^  pass" "$TMP/plant.log" | sed 's/^/   /'
    ok "all 4 plant checks pass (control changes the KPIs)"
  else
    no "plant checks" "$(grep -E 'FAIL' "$TMP/plant.log" | head -3)"
  fi
else no "plant compile" "$(tail -5 "$TMP/cc.log")"; fi

# --- 4. SCTP ---
echo; echo "[4/8] SCTP (E2AP transport)"
if python3 -c "import socket;socket.socket(socket.AF_INET,socket.SOCK_SEQPACKET,132).close()" 2>/dev/null; then
  ok "SCTP socket created"
else
  no "no SCTP" "Run sudo modprobe sctp and retry. FlexRIC has no TCP fallback.
     Checks 5-8 below cannot run on this host."
  echo; printf " passed %d / failed %d — run again on a Linux host with SCTP.\n" "$PASS" "$FAIL"; exit "$FAIL"
fi

# --- 5. Bring up the triangle + receive indications ---
echo; echo "[5/8] nearRT-RIC + patched E2 node + Python xApp"

# 5a. Clean up processes left over from an earlier run. Without this, from the second run on
#     nearRT-RIC dies instantly on a bind() failure (EADDRINUSE).
if pgrep -f '[n]earRT-RIC' >/dev/null 2>&1 || pgrep -f '[e]mu_agent_gnb' >/dev/null 2>&1; then
  echo "  · FlexRIC processes from a previous run are still alive; cleaning them up"
fi
reap

# exec replaces the subshell with the binary → $! becomes the PID of the real process.
# (With the old form `( cd .. && ./bin )&` we would capture the subshell PID, the kill would miss,
#  and the orphaned binary would keep holding the port.)
RICLOG="$TMP/ric.log"; AGLOG="$TMP/agent.log"
( cd "$B"; exec ./examples/ric/nearRT-RIC ) >"$RICLOG" 2>&1 & RIC_PID=$!

# Check that it is actually alive instead of using a fixed sleep.
for _ in $(seq 1 20); do
  sleep 0.5
  kill -0 $RIC_PID 2>/dev/null || break
  grep -q "E2AP" "$TMP/ric.log" 2>/dev/null && break
done

if ! kill -0 $RIC_PID 2>/dev/null; then
  no "nearRT-RIC failed to start" "$(tail -6 "$TMP/ric.log")
     $(grep -q 'init_sctp_conn_server' "$TMP/ric.log" 2>/dev/null \
       && echo '→ bind() failed. This is almost always a port already in use (EADDRINUSE), caused by
        a nearRT-RIC/emu_agent_gnb that survived an earlier run.
        Run pkill -f nearRT-RIC; pkill -f emu_agent_gnb  and then run this again.' \
       || echo '→ First check whether /usr/local/lib/flexric is installed (check 1).')
     SCTP endpoints listening now:
$(sctp_eps | sed 's/^/       /' | head -6)"
else
  ( cd "$B"; exec ./examples/emulator/agent/emu_agent_gnb ) >"$TMP/agent.log" 2>&1 & AG_PID=$!
  sleep 6
  if kill -0 $RIC_PID 2>/dev/null && kill -0 $AG_PID 2>/dev/null \
     && ! grep -q "Resending Setup Request" "$TMP/agent.log" 2>/dev/null; then
    ok "RIC and E2 node are up (E2 Setup complete)"
  elif kill -0 $RIC_PID 2>/dev/null && kill -0 $AG_PID 2>/dev/null; then
    no "E2 Setup not complete" "Both processes are alive, but the agent keeps resending the Setup Request.
     --- agent.log ---
$(tail -5 "$TMP/agent.log")"
  else
    no "startup failed" "--- ric.log ---
$(tail -5 "$TMP/ric.log")
--- agent.log ---
$(tail -5 "$TMP/agent.log")"
  fi
fi

if ! kill -0 ${RIC_PID:-0} 2>/dev/null || ! kill -0 ${AG_PID:-0} 2>/dev/null; then
  no "xApp receives indications" "Skipped: the RIC/E2 node is not alive."
  no "control delivery" "Fix the startup failure above first."
  no "closed-loop effect" "Fix the startup failure above first."
  echo; echo "=============================================================="
  printf " passed %d / failed %d\n" "$PASS" "$FAIL"
  echo "=============================================================="
  exit "$FAIL"
fi

# The demand period is 30 s. With 25 s you do not cover one full period and miss the open-loop worst point.
DUR=40
echo; echo "[6/8] Does the Python xApp receive MAC indications? (open-loop baseline)"
OPEN="$TMP/open.csv"
XAPP_SDK_PATH="$SDK" timeout $((DUR+30)) python3 "$HERE/xapp_slice_ctrl.py" \
    --open-loop --duration $DUR --out "$OPEN" >"$TMP/open.log" 2>&1
ORC=$?
if [ -f "$OPEN" ] && [ "$(wc -l < "$OPEN")" -gt 5 ]; then
  ok "Python xApp received MAC indications ($(( $(wc -l < "$OPEN") - 1 )) samples)"
else
  no "xApp receives indications (xApp exit code $ORC$(xrc_hint $ORC))" "$(tail -8 "$TMP/open.log")"
fi

# --- 6b. Close the open-loop session and bring the RIC/E2 node up again ---
# Upstream bug: a pending event left over after unsubscription is touched by the next xApp's CONTROL ACK, giving
#   e2ap_handle_control_ack_xapp → rm_pending_event_ev → bi_map_extract_right
#   assoc_rb_tree_extract: Assertion failed → SIGABRT
# It happens only for **the first control xApp right after an open-loop xApp attached to and left a fresh RIC
# session**, with about 60% probability. A restart makes it go away (measured 2026-08-26: 0 aborts in 3 runs with
# restart / abort on the 1st of 5 open-loop→control pairs without restart). One session per xApp — this also matches the student workflow.
echo "  · Closing the open-loop session and restarting the RIC/E2 node (one session per xApp)"
kill_if "${AG_PID:-}"; kill_if "${RIC_PID:-}"
reap
RICLOG="$TMP/ric2.log"; AGLOG="$TMP/agent2.log"
( cd "$B"; exec ./examples/ric/nearRT-RIC ) >"$RICLOG" 2>&1 & RIC_PID=$!
for _ in $(seq 1 20); do
  sleep 0.5
  kill -0 $RIC_PID 2>/dev/null || break
  grep -q "E2AP" "$RICLOG" 2>/dev/null && break
done
if kill -0 ${RIC_PID:-0} 2>/dev/null; then
  ( cd "$B"; exec ./examples/emulator/agent/emu_agent_gnb ) >"$AGLOG" 2>&1 & AG_PID=$!
  sleep 6
fi
if ! kill -0 ${RIC_PID:-0} 2>/dev/null || ! kill -0 ${AG_PID:-0} 2>/dev/null; then
  no "control delivery" "Could not bring the RIC/E2 node up again after the open-loop run.
     Usually an earlier process has not released the port yet — wait a moment and run again.
--- ric2.log ---
$(tail -6 "$RICLOG" 2>/dev/null)
--- agent2.log ---
$(tail -6 "$AGLOG" 2>/dev/null)"
  no "closed-loop effect" "Fix the restart failure above first."
  echo; echo "=============================================================="
  printf " passed %d / failed %d\n" "$PASS" "$FAIL"
  echo "=============================================================="
  exit "$FAIL"
fi
echo "  · RIC/E2 node restart complete"

# --- 6. Did the control reach the agent? (check this before judging the effect) ---
echo; echo "[7/8] Control delivery — is the SLICE control applied to the plant?"
CLOSED="$TMP/closed.csv"
XAPP_SDK_PATH="$SDK" timeout $((DUR+30)) python3 "$HERE/xapp_slice_ctrl.py" \
    --gain 2.0 --period 100 --duration $DUR --out "$CLOSED" >"$TMP/closed.log" 2>&1
CRC=$?

# The agent log is the only direct evidence. From the CSV alone you cannot tell "the loop runs but has
# no effect" apart from "the control never arrived in the first place".
# With 0 matches grep -c prints "0" and returns **exit code 1**. So appending `|| echo 0` makes the
# value "0\n0" and [ dies with `integer expected` — and it breaks **only the diagnostics of the FAIL path**
# (when the control arrives the count is not 0, so it never shows on the pass path).
NRESET=$(grep -c "plant reset" "$AGLOG" 2>/dev/null); NRESET=${NRESET:-0}
NADD=$(grep -c "SLICE ADD" "$AGLOG" 2>/dev/null); NADD=${NADD:-0}
SPAN=$(grep "SLICE ADD" "$AGLOG" 2>/dev/null \
       | sed -n 's/.*applied \([0-9.]*\)\/.*/\1/p' \
       | awk 'NR==1{mn=mx=$1} {if($1<mn)mn=$1; if($1>mx)mx=$1} END{if(NR)printf "%.1f %.1f %.1f", mn, mx, mx-mn; else print "0 0 0"}')
set -- $SPAN
SMIN=${1:-0}; SMAX=${2:-0}; SSPAN=${3:-0}
# Look at the exit status of the xApp first. Judging by SLICE ADD in the agent log alone prints **"pass" even
# when the xApp aborted midway** — the controls sent before it died already exceed 10.
if [ "$CRC" -ne 0 ]; then
  no "control xApp exited abnormally (exit $CRC$(xrc_hint $CRC)) — SLICE ADD x${NADD}" \
     "Even if the control arrived, the xApp did not survive to the end. The CSV from this run cannot be trusted.
     If it is SIGABRT and the log shows assoc_rb_tree_extract, it is the RIC session reuse problem —
     this script restarts the RIC/E2 node between checks 6 and 7. If it still happens, run it again.
--- closed.log (xApp) ---
$(tail -20 "$TMP/closed.log")
--- agent2.log ---
$(tail -8 "$AGLOG")"
elif [ "$NADD" -ge 10 ] && awk "BEGIN{exit !($SSPAN > 10)}" 2>/dev/null; then
  ok "SLICE control applied ${NADD} times, share0 moved over ${SMIN}% ~ ${SMAX}% (${NRESET} resets)"
elif [ "$NADD" -ge 10 ]; then
  no "control arrived but share barely moved (${SMIN}% ~ ${SMAX}%)" \
     "The gain is too small or the error units are off.
     Check the backlog unit in decide() — bsr is in **bytes**, the gain is per **Mbit**."
else
  no "SLICE control did not reach the agent (SLICE ADD x${NADD})" \
     "--- closed.log (xApp) ---
$(tail -20 "$TMP/closed.log")
--- agent2.log ---
$(tail -8 "$AGLOG")
--- ric2.log ---
$(tail -5 "$RICLOG")"
fi

# --- 7. Does the closed loop really close? (the reason this brief exists) ---
echo; echo "[8/8] Closed-loop effect — does control reduce the backlog?"
R=$(python3 - "$OPEN" "$CLOSED" <<'PY' 2>/dev/null
import csv, sys
def peak(p):
    try:
        rows = list(csv.DictReader(open(p)))
        return max(int(r["backlog0_bytes"]) + int(r["backlog1_bytes"]) for r in rows) / 1e6
    except Exception:
        return -1.0
o, c = peak(sys.argv[1]), peak(sys.argv[2])
print(f"{o:.2f} {c:.2f}")
PY
)
set -- $R
O=${1:--1}; C=${2:--1}
if awk "BEGIN{exit !($O > 0 && $C >= 0 && $C < $O * 0.7)}" 2>/dev/null; then
  ok "open-loop peak backlog ${O} MB → closed-loop ${C} MB (control works)"
else
  no "no closed-loop effect (open loop ${O} MB / closed loop ${C} MB)" \
     "If check 7 passed but this one failed, the control arrives but is weak —
     look at the gain or the error units. If check 7 failed too, start from the patch/build (check 2).
     A closed-loop value of -1.00 means the CSV was never written (i.e. the xApp died).
--- closed.log (xApp) ---
$(tail -25 "$TMP/closed.log")
--- agent2.log ---
$(tail -10 "$AGLOG")"
fi

echo
echo "=============================================================="
printf " passed %d / failed %d\n" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && echo " Ready — start from Question 1 in BRIEF.md." \
                  || echo " Fix the failed items first."
echo "=============================================================="
exit "$FAIL"
