#!/usr/bin/env bash
# EE49904 — ASTRA-sim starter kit smoke test
# You are ready to start the assignment only when this script passes to the end.
#   bash smoke_test.sh
# 5 checks, usually under 1 minute.

set -uo pipefail
ROOT="${ASTRA_SIM_ROOT:-$HOME/astra-sim}"
HERE="$(cd "$(dirname "$0")" && pwd)"
# The kit interpreter is the one setup.sh created. If we silently fall through to the system python3 when it
# is missing, **you get a green light from something that is not the kit environment** — this is exactly how, in this
# course, two macs with different Pythons installed different Sionna versions and both passed. Say which Python is
# used, and if it is missing, stop instead of guessing. (Exit code 125 = environment not set up; distinct from the FAIL count.)
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  if [ "${EE_ALLOW_SYSTEM_PYTHON:-0}" = "1" ]; then
    PY=python3
    echo "  ⚠ No virtual environment — running with the system python3 (EE_ALLOW_SYSTEM_PYTHON=1)."
    echo "    This is not a kit-environment result — do not use it in a verification record."
  else
    {
      echo "Virtual environment not found: $PY"
      echo "  The cause is one of two things."
      echo "    1) setup.sh has not been run yet            → bash setup.sh"
      echo "    2) setup.sh was run with a different path   → ASTRA_SIM_ROOT=<that path> bash smoke_test.sh"
      echo "  To force the system python3, set EE_ALLOW_SYSTEM_PYTHON=1 (the result is not a kit-environment result)."
    } >&2
    exit 125
  fi
fi
PASS=0; FAIL=0
ok(){ printf "  \033[32mpass\033[0m  %s\n" "$1"; PASS=$((PASS+1)); }
no(){ printf "  \033[31mFAIL\033[0m  %s\n     → %s\n" "$1" "$2"; FAIL=$((FAIL+1)); }

echo "=============================================================="
echo " ASTRA-sim starter kit smoke test    ASTRA_SIM_ROOT=$ROOT"
echo " python  $PY"
# Report this line with your results: it says which simulator produced your numbers.
echo " commit  $(git -C "$ROOT" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
echo "=============================================================="

# --- 1. Build outputs ---
echo; echo "[1/5] Build outputs"
# One check, so that the count at the end matches the five steps above it.
MISSING=""
for b in AstraSim_Analytical_Congestion_Unaware AstraSim_Analytical_Congestion_Aware; do
  [ -x "$ROOT/build/astra_analytical/build/bin/$b" ] || MISSING="$MISSING $b"
done
if [ -z "$MISSING" ]; then ok "both analytical backends built"
else no "backend binaries missing:$MISSING" "re-run setup.sh"; fi

# --- 2. Workload generator (protobuf) ---
echo; echo "[2/5] Workload generator"
GEN_OUT=$(cd "$ROOT/examples/workload/microbenchmarks" && \
  PYTHONPATH="$ROOT" "$PY" generator_scripts/all_reduce.py --npus-count 4 --coll-size 2 2>&1)
if [ -f "$ROOT/examples/workload/microbenchmarks/all_reduce/4npus_2MB/all_reduce.0.et" ]; then
  ok "Chakra ET generation (4npus, 2MB)"
else
  no "Chakra ET generation" "check the protobuf Python package: $PY -m pip install protobuf
     output: $(echo "$GEN_OUT" | tail -2)"
fi

# --- 3. Single run ---
echo; echo "[3/5] Single simulator run"
OUT=$(cd "$ROOT" && ./build/astra_analytical/build/bin/AstraSim_Analytical_Congestion_Unaware \
  --workload-configuration=examples/workload/microbenchmarks/reduce_scatter/4npus_1MB/reduce_scatter \
  --system-configuration=examples/system/native_collectives/Ring_4chunks.json \
  --network-configuration=examples/network/analytical/Ring_4npus.yml \
  --remote-memory-configuration=examples/remote_memory/analytical/no_memory_expansion.json 2>&1)
NS=$(echo "$OUT" | grep -oE "Wall time: [0-9]+" | tail -1 | grep -oE "[0-9]+")
if [ -n "${NS:-}" ] && [ "$NS" -gt 0 ]; then ok "reduce_scatter 4npus/1MB → ${NS} ns"
else no "simulator run" "$(echo "$OUT" | tail -3)"; fi

# --- 4. Sweep driver ---
echo; echo "[4/5] Sweep driver (run_sweep.py)"
SW=$("$PY" "$HERE/run_sweep.py" --root "$ROOT" --collectives all_reduce --npus 8 \
      --sizes-mb 1,64 --topologies Ring:50 --impls ring --out /tmp/_smoke.csv 2>&1)
if [ -f /tmp/_smoke.csv ] && [ "$(wc -l < /tmp/_smoke.csv)" -ge 3 ]; then ok "results.csv created"
else no "run_sweep.py" "$(echo "$SW" | tail -3)"; fi

# --- 5. Model sanity: does doubling the bandwidth shorten the large-message time? ---
echo; echo "[5/5] Model sanity check (bandwidth ↑ → time ↓)"
"$PY" "$HERE/run_sweep.py" --root "$ROOT" --collectives all_reduce --npus 8 \
      --sizes-mb 64 --topologies Ring:50,Ring:100 --impls ring --out /tmp/_bw.csv >/dev/null 2>&1
if [ -f /tmp/_bw.csv ]; then
  R=$("$PY" - <<'PY'
import csv
r={int(float(x["bandwidth_GBps"])): float(x["us"]) for x in csv.DictReader(open("/tmp/_bw.csv"))}
if 50 in r and 100 in r and r[100] > 0:
    print(f"{r[50]/r[100]:.2f} {r[50]:.0f} {r[100]:.0f}")
PY
)
  set -- $R
  if [ -n "${1:-}" ]; then
    RATIO=$1
    if awk "BEGIN{exit !($RATIO > 1.5 && $RATIO < 2.2)}"; then
      ok "${RATIO}x faster at 50→100 GB/s (64MB: $2µs → $3µs) — bandwidth-dominated regime OK"
    else
      no "speedup ratio $RATIO" "outside the 1.5-2.2 range — check the configuration"
    fi
  else no "sanity check" "could not parse the results"; fi
else no "sanity check" "/tmp/_bw.csv not found"; fi

echo
echo "=============================================================="
printf " passed %d / failed %d\n" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && echo " Ready — start from Question 1 in BRIEF.md." \
                  || echo " Fix the FAIL items first. If you are stuck, send this output as is to the TA."
echo "=============================================================="
exit "$FAIL"
