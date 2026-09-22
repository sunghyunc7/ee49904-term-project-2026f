#!/usr/bin/env bash
# EE49904 — twin-trained control starter kit smoke test
# The kit is ready for the assignment only when this script passes to the end.
#   bash smoke_test.sh          (about 1-3 min)

set -uo pipefail
VENV="${TWIN_VENV:-$HOME/twin-venv}"
HERE="$(cd "$(dirname "$0")" && pwd)"
# The interpreter of the kit is the one setup.sh built. If it is missing and we silently fall through to the
# system python3, **a green light comes from outside the kit environment** — this is exactly the path by which two macs
# in this course installed different Sionna versions under different Pythons and still passed. State which Python is used,
# and if it is missing, stop instead of guessing. (Exit code 125 = environment not set up; distinct from the failed-check count.)
PY="$VENV/bin/python"
if [ ! -x "$PY" ]; then
  if [ "${EE_ALLOW_SYSTEM_PYTHON:-0}" = "1" ]; then
    PY=python3
    echo "  ⚠ No venv found; running with the system python3 (EE_ALLOW_SYSTEM_PYTHON=1)."
    echo "    This result is not from the kit environment — do not use it as a verification record."
  else
    {
      echo "Virtual environment not found: $PY"
      echo "  The cause is one of two things."
      echo "    1) setup.sh has not been run yet         → bash setup.sh"
      echo "    2) setup.sh was run with another path    → TWIN_VENV=<that path> bash smoke_test.sh"
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
echo " twin-trained control starter kit smoke test"
echo " TWIN_VENV=$VENV"
echo " python  $PY"
echo "=============================================================="

# --- 1. Runtime ---
echo; echo "[1/5] Runtime"
V=$("$PY" -c "
import sionna, torch, sionna.rt as rt, mitsuba as mi
rt.load_scene()                      # the variant is fixed at the first scene load
print(sionna.__version__, torch.__version__, mi.variant())" 2>&1)
if [ $? -eq 0 ]; then
  echo "     sionna/torch/mitsuba: $V"
  case "$V" in *llvm*) ok "Sionna import (Mitsuba CPU backend)";;
                    *) ok "Sionna import (Mitsuba backend $V — may not be CPU)";; esac
else no "Sionna import" "Run setup.sh first: $V"; fi

# --- 2. Geometry layer ---
echo; echo "[2/5] Geometry layer — Sionna RT radio map"
G=$("$PY" - <<PYCODE 2>&1
import sys; sys.path.insert(0,"$HERE")
import numpy as np, twinlib as T
snr, c, meta = T.radio_map_snr("simple_street_canyon", cell_size=4.0,
                               samples_per_tx=10**5, tx_dbm=20.0)
v = np.isfinite(snr)
q = np.nanpercentile(snr,[5,50,95])
print(f"OK cells={snr.size} valid={int(v.sum())} snr_p5={q[0]:.1f} p50={q[1]:.1f} p95={q[2]:.1f}")
PYCODE
)
if echo "$G" | grep -q "^OK"; then echo "     ${G#OK }"; ok "Radio map built + SNR grid"
else no "Radio map" "$(echo "$G" | tail -3)"; fi

# --- 3. Link layer ---
echo; echo "[3/5] Link layer — per-MCS BLER table (light settings, about 20 s)"
L=$("$PY" - <<PYCODE 2>&1
import sys; sys.path.insert(0,"$HERE")
import numpy as np, twinlib as T
grid = np.arange(0.,25.,3.)
tab = T.build_bler_table(grid, k=256, batch_size=100, max_mc_iter=6,
                         num_target_block_errors=25, verbose=False)
np.savez("$TMP/link.npz", grid=grid, tab=tab)
thr=[]
for m in range(tab.shape[0]):
    idx = np.argmax(tab[m]<=0.1) if (tab[m]<=0.1).any() else -1
    thr.append(grid[idx] if idx>=0 else np.nan)
print("thr " + " ".join(f"{t:.0f}" for t in thr))
# The threshold SNR must grow with the MCS index (if it decreases, the table is wrong)
print("MONO", int(all(a<=b for a,b in zip(thr,thr[1:]))))
PYCODE
)
if echo "$L" | grep -q "MONO 1"; then
  echo "     10% BLER threshold SNR (dB): $(echo "$L" | grep '^thr' | cut -d' ' -f2-)"
  ok "BLER table built + threshold SNR monotonically increasing"
else no "BLER table" "$(echo "$L" | tail -3)"; fi

# --- 4. Evaluation on the twin ---
echo; echo "[4/5] Policy evaluation on the twin (the target must be met)"
E=$("$PY" - <<PYCODE 2>&1
import sys; sys.path.insert(0,"$HERE")
import numpy as np, twinlib as T
z=np.load("$TMP/link.npz"); grid,tab=z["grid"],z["tab"]
snr,c,_ = T.radio_map_snr("simple_street_canyon", cell_size=4.0,
                          samples_per_tx=10**5, tx_dbm=20.0)
pol = T.derive_policy(grid, tab, 0.1)
s,_ = T.walk_trajectory(snr, c, steps=2500, speed_mps=20.0, dt_s=0.01, seed=0)
r = T.evaluate(s, s, grid, tab, pol, target_bler=0.1, seed=1)
np.savez("$TMP/traj.npz", s=s)
print(f"OK thr={r['throughput']:.3f} bler={r['bler']:.3f} viol={int(r['violation'])}")
PYCODE
)
if echo "$E" | grep -q "^OK"; then
  echo "     ${E#OK }"
  if echo "$E" | grep -q "viol=0"; then ok "BLER target met on the twin"
  else no "Target violated on the twin" "Something is wrong with the policy derivation or the BLER table"; fi
else no "Twin evaluation" "$(echo "$E" | tail -3)"; fi

# --- 5. Key phenomenon: the twin is optimistic ---
echo; echo "[5/5] Model sanity — does BLER rise when delay and estimation error are added"
D=$("$PY" - <<PYCODE 2>&1
import sys; sys.path.insert(0,"$HERE")
import numpy as np, twinlib as T
z=np.load("$TMP/link.npz"); grid,tab=z["grid"],z["tab"]
s=np.load("$TMP/traj.npz")["s"]
pol=T.derive_policy(grid,tab,0.1)
clean = T.evaluate(s,s,grid,tab,pol,0.1,seed=1)["bler"]
obs = T.stale_and_noisy(s, delay_steps=20, est_std_db=2.0, seed=7)
field = T.evaluate(s,obs,grid,tab,pol,0.1,seed=1)["bler"]
print(f"OK clean={clean:.3f} field={field:.3f}")
print("WORSE", int(field > clean + 0.01))
PYCODE
)
if echo "$D" | grep -q "WORSE 1"; then
  echo "     $(echo "$D" | grep '^OK' | cut -d' ' -f2-)"
  ok "BLER rise under field conditions confirmed — the twin is optimistic"
else no "Sanity check" "BLER did not rise even with delay and error added: $(echo "$D" | tail -2)"; fi

echo
echo "=============================================================="
printf " passed %d / failed %d\n" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && echo " Ready — start from Question 1 in BRIEF.md." \
                  || echo " Fix the failed items first. If you are stuck, send this output as-is to the TA."
echo "=============================================================="
exit "$FAIL"
