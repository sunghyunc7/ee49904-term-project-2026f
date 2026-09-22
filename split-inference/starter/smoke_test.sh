#!/usr/bin/env bash
# EE49904 — split inference starter kit smoke test
# You are ready to start the project only when this script passes all the way through.
#   bash smoke_test.sh          (about 2-4 min)

set -uo pipefail
VENV="${SPLIT_VENV:-$HOME/split-inference-venv}"
HERE="$(cd "$(dirname "$0")" && pwd)"
# The kit interpreter is the one setup.sh created. If we silently fall through to the system python3 when it is missing,
# **a green light comes from somewhere that is not the kit environment** — in this course two Macs really did pass with different Pythons
# and different Sionna installs, through exactly this path. State which Python is in use, and if it is missing,
# stop instead of guessing. (Exit code 125 = environment not set up. Distinct from the failed-check count.)
PY="$VENV/bin/python"
if [ ! -x "$PY" ]; then
  if [ "${EE_ALLOW_SYSTEM_PYTHON:-0}" = "1" ]; then
    PY=python3
    echo "  ⚠ No virtualenv found; running with the system python3 (EE_ALLOW_SYSTEM_PYTHON=1)."
    echo "    This result is not from the kit environment — do not use it as a verification record."
  else
    {
      echo "Virtualenv not found: $PY"
      echo "  The cause is one of two things."
      echo "    1) setup.sh has not been run yet          → bash setup.sh"
      echo "    2) setup.sh was run with another path     → SPLIT_VENV=<that path> bash smoke_test.sh"
      echo "  To force the system python3, set EE_ALLOW_SYSTEM_PYTHON=1 (the result is not from the kit environment)."
    } >&2
    exit 125
  fi
fi
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
PASS=0; FAIL=0
ok(){ printf "  \033[32mpass\033[0m  %s\n" "$1"; PASS=$((PASS+1)); }
no(){ printf "  \033[31mFAIL\033[0m  %s\n     → %s\n" "$1" "$2"; FAIL=$((FAIL+1)); }

echo "=============================================================="
echo " split inference starter kit smoke test"
echo " SPLIT_VENV=$VENV"
echo " python  $PY"
echo "=============================================================="

# Pin the Pythons this script launches so they do not fight each other for cores.
# (--selftest also pins internally, but the inline checks here must run under the same
#  conditions for the checks to be comparable with one another.)
EE_THREADS=${EE_SPLIT_THREADS:-$( "$PY" -c 'import os;print(max(1,min(4,(os.cpu_count() or 4)//2)))' )}
export OMP_NUM_THREADS="$EE_THREADS" MKL_NUM_THREADS="$EE_THREADS" OPENBLAS_NUM_THREADS="$EE_THREADS"
echo " threads $EE_THREADS (change with EE_SPLIT_THREADS)"

# --- 1. Runtime ---
echo; echo "[1/5] Runtime"
V=$("$PY" -c "import torch,torchvision;print(torch.__version__,torchvision.__version__)" 2>&1)
if [ $? -eq 0 ]; then ok "torch/torchvision $V"; else no "PyTorch import" "run setup.sh first: $V"; fi

# --- 2. Splitting models into stages ---
echo; echo "[2/5] Splitting models into stages"
OUT=$("$PY" - <<PYCODE 2>&1
import sys; sys.path.insert(0,"$HERE")
import torch, splitlib as S
for name in S.MODELS:
    names, mods = S.build_model(name)
    x = torch.randn(*S.INPUT_SHAPE)
    with torch.no_grad():
        h = x
        for m in mods: h = m(h)
    assert h.shape == (1,1000), (name, h.shape)
    print(f"OK {name} stages={len(names)} out={tuple(h.shape)}")
PYCODE
)
if echo "$OUT" | grep -q "OK mobilenet_v3_large"; then
  echo "$OUT" | sed 's/^/     /'; ok "all 3 models give the expected output (1,1000)"
else no "model splitting" "$(echo "$OUT" | tail -3)"; fi

# --- 3. Split point sweep ---
echo; echo "[3/5] Split point sweep (split_bench.py)"
SW=$("$PY" "$HERE/split_bench.py" --models resnet18 --bandwidth 5,50 --rtt 20 \
      --device-slowdown 8 --out "$TMP/r.csv" 2>&1)
if [ -f "$TMP/r.csv" ] && [ "$(wc -l < "$TMP/r.csv")" -ge 5 ]; then
  ok "results.csv written ($(( $(wc -l < "$TMP/r.csv") - 1 )) rows)"
else no "split_bench.py" "$(echo "$SW" | tail -3)"; fi

# --- 4. Live harness (server+client) + pacer calibration ---
echo; echo "[4/5] Live harness (split_serve.py)"
# The calibrator gives a **reference value**, not a guaranteed ceiling — one payload size, measured with
# nothing else running. (Before 2026-09-19 the harness left Nagle's algorithm on, and on Linux the
# delayed-ACK stall made calibration stop near 10 Mb/s on machines that pace far higher; see _nodelay
# in split_serve.py.) So this check only asks "did calibration run" (the value never fails the kit).
CAL=$("$PY" "$HERE/split_serve.py" --selftest --calibrate 5,10,25,50,100 2>&1)
if echo "$CAL" | grep -q "reference value"; then
  echo "$CAL" | grep -E "^ +[0-9]+ +[0-9.]+|reference value" | sed 's/^/     /'
  ok "pacer calibration done (the above is a reference value — the effective bandwidth in each run report is authoritative)"
else no "pacer calibration" "$(echo "$CAL" | tail -4)"; fi

# --iters uses the default (20). With 4 iterations, minus warm-up, there are 3 samples and a single
# scheduling hiccup becomes the error as is. --selftest pins the server and client threads, so the two
# processes do not fight over the same cores (measured 2026-08-26: +63~83% → +25.3%).
SV=$("$PY" "$HERE/split_serve.py" --selftest --model resnet18 --split layer2 \
      --bandwidth 10 --rtt 10 2>&1)
ERR=$(echo "$SV" | grep -oE "error +[+-]?[0-9.]+" | grep -oE "[+-]?[0-9.]+$")
if [ -n "${ERR:-}" ]; then
  echo "$SV" | grep -E "measured end-to-end|predicted|error|effective bandwidth" | sed 's/^/     /'
  # Tolerance 35%: the remaining error is not a defect to remove but a **gap to explain** (serialization,
  # deserialization, RTT, effective bandwidth) — exactly what the BRIEF asks students to explain.
  if awk "BEGIN{exit !(($ERR < 0 ? -($ERR) : $ERR) < 35)}"; then
    ok "error vs predicted ${ERR}% (at 10 Mb/s, within 35%)"
  else
    no "error vs predicted ${ERR}%" "The machine is busy, or pacing did not reach the target.
     Close other programs and retry. Look at the 'effective bandwidth' line of the report first —
     far below target means a pacing limit; close to target means compute contention."
  fi
else no "split_serve.py --selftest" "$(echo "$SV" | tail -5)"; fi

# --- 5. Model sanity: as bandwidth rises the optimal split point moves from the device side to the network side ---
echo; echo "[5/5] Model sanity check (bandwidth ↑ → offloading wins)"
R=$("$PY" - <<PYCODE 2>&1
import sys; sys.path.insert(0,"$HERE")
import splitlib as S
names, mods = S.build_model("resnet18")
S.set_split_index(names)
t,sh,ne,inn,_ = S.profile_stages(mods, repeats=8)
def best(bw):
    return min(S.split_points(names),
               key=lambda sp: S.total_latency_s(sp,t,ne,inn,"fp32",bw,20,20.0,8.0))
lo, hi = best(1.0), best(500.0)
print(f"{lo} {hi}")
PYCODE
)
set -- $R
if [ "${1:-}" = "all_local" ] && [ "${2:-}" = "input" ]; then
  ok "1 Mb/s → all_local, 500 Mb/s → input (crossover OK)"
else
  no "crossover check (got: ${1:-?} / ${2:-?})" "local execution should win at 1 Mb/s and remote at 500 Mb/s"
fi

echo
echo "=============================================================="
printf " passed %d / failed %d\n" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && echo " Ready — start with Question 1 in BRIEF.md." \
                  || echo " Fix the failed items first. If you are stuck, send this output as is to the TA."
echo "=============================================================="
exit "$FAIL"
