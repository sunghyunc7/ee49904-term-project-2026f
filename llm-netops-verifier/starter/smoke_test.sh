#!/usr/bin/env bash
# EE49904 — LLM NetOps + verifier starter kit smoke test
#
#   source ~/ee49904-netops/env.sh && bash smoke_test.sh
#   bash smoke_test.sh --no-llm      # checks 1-6 only: no model server needed
#
# 9 checks, about 3-6 minutes (most of it Batfish loading snapshots).
#
# The checks are ordered so that a failure tells you where the problem is, and they deliberately
# separate three facts that are easy to confuse:
#
#   * check 2  the verifier is REACHABLE
#   * check 4  the verifier DETECTS a defect that is known to be there
#   * check 5  the verifier ACCEPTS a change that is known to be correct
#
# A kit that passes 2 and fails 4 looks healthy and measures nothing.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
NO_LLM=0
[ "${1:-}" = "--no-llm" ] && NO_LLM=1

PASS=0; FAIL=0; SKIP=0
ok(){   printf "  \033[32mpass\033[0m  %s\n" "$1"; PASS=$((PASS+1)); }
no(){   printf "  \033[31mFAIL\033[0m  %s\n" "$1"; FAIL=$((FAIL+1)); }
skip(){ printf "  \033[33mskip\033[0m  %s\n" "$1"; SKIP=$((SKIP+1)); }

# Run one python phase and report whatever it says. Everything the phase prints that is not the
# RESULT line is kept and shown on failure — a check with no diagnostic output is a check you
# cannot act on.
PHASE_OUT=""
phase(){
  local name="$1" label="$2" out rc line
  out="$(python3 -m netops.smoke "$name" 2>&1)"; rc=$?
  PHASE_OUT="$out"          # the caller may need to tell two failure causes apart
  line="$(printf '%s\n' "$out" | grep -m1 '^RESULT: ')"
  if [ -z "$line" ]; then
    no "$label"
    printf '%s\n' "$out" | tail -12 | sed 's/^/        /'
    return 1
  fi
  if [ "${line#RESULT: pass }" != "$line" ]; then
    ok "$label — ${line#RESULT: pass }"
  else
    no "$label"
    printf '%s\n' "${line#RESULT: fail }" | sed 's/^/        /'
    printf '%s\n' "$out" | grep -v '^RESULT: ' | tail -8 | sed 's/^/        /'
  fi
  return $rc
}

# The venv holds pybatfish. Forgetting to source env.sh is the single most common way to get a
# scary-looking failure out of a healthy kit, so activate it here when it is not already active —
# and say so, because a script that fixes your shell silently teaches you nothing about your shell.
if ! python3 -c "import pybatfish" 2>/dev/null; then
  ENVFILE="${EE_ROOT:-$HOME/ee49904-netops}/env.sh"
  if [ -f "$ENVFILE" ]; then
    set +u; . "$ENVFILE"; set -u
    echo "  (activated the venv for this run: source $ENVFILE)"
    echo "  Your own shell still needs that line — run_loop.py will not activate it for you."
  fi
fi

echo "=============================================================="
echo " LLM NetOps + verifier — starter kit smoke test"
echo " Batfish : ${EE_BF_HOST:-localhost}"
echo " LLM     : ${EE_LLM_HOST:-http://localhost:11434}  model ${EE_LLM_MODEL:-gpt-oss}"
echo "=============================================================="

echo; echo "[1/9] offline checks — merge rules, answer extraction, budget"
if OUT="$(python3 -m netops.selftest 2>&1)"; then
  ok "$(printf '%s' "$OUT" | tail -1 | sed 's/^ *//')"
else
  no "netops.selftest"
  printf '%s\n' "$OUT" | grep -E "FAIL|failed" | sed 's/^/        /'
fi

echo; echo "[2/9] Batfish service"
phase service "Batfish reachable"
BF_OK=$?

if [ "$BF_OK" -ne 0 ]; then
  echo
  echo "  Checks 3-6 and 8-9 cannot run."
  # Two different causes, two different fixes — do not offer both at once.
  if printf '%s\n' "$PHASE_OUT" | grep -q "virtual environment is not active"; then
    echo "  The venv is not active. Batfish itself may be perfectly fine."
  else
    echo "  Batfish is not answering. Start it again with:"
    echo "    bash setup.sh      # seconds, not minutes, the second time — it reuses what it installed"
    echo "  (After a reboot of the machine this is expected: Batfish does not come back by itself.)"
  fi
  echo
  echo "=============================================================="
  printf " pass %d / FAIL %d / skip %d\n" "$PASS" "$FAIL" "$SKIP"
  echo "=============================================================="
  exit 1
fi

echo; echo "[3/9] the base network parses"
phase reference "reference snapshot"

echo; echo "[4/9] the verifier DETECTS a known defect  ← the check that matters"
phase detects "wrong-direction ACL must be rejected"

echo; echo "[5/9] the verifier ACCEPTS a known-good change"
phase accepts "correct-direction ACL must be accepted"

echo; echo "[6/9] the second intent is a real task"
phase intent "block-guest unmet before the fix, met after"

if [ "$NO_LLM" -eq 1 ]; then
  echo; echo "[7/9] LLM server";        skip "--no-llm"
  echo; echo "[8/9] one full iteration"; skip "--no-llm"
  echo; echo "[9/9] replay";            skip "--no-llm"
else
  echo; echo "[7/9] LLM server"
  phase llm "model server reachable"
  LLM_OK=$?

  if [ "$LLM_OK" -ne 0 ]; then
    echo; echo "[8/9] one full iteration"; skip "no model server"
    echo; echo "[9/9] replay";             skip "no model server"
  else
    echo; echo "[8/9] one full iteration — model proposes, verifier answers"
    phase end-to-end "generate → apply → verify"

    echo; echo "[9/9] replay — the recorded run reproduces with the server switched off"
    phase replay "replay costs nothing"
  fi
fi

echo
echo "=============================================================="
printf " pass %d / FAIL %d / skip %d\n" "$PASS" "$FAIL" "$SKIP"
if [ "$FAIL" -eq 0 ] && [ "$SKIP" -eq 0 ]; then
  echo " Ready — start with Question 1 in BRIEF.md."
elif [ "$FAIL" -eq 0 ]; then
  echo " The offline half is ready. Bring up what was skipped before you start measuring."
else
  echo " Fix the failures above first. Check 4 failing means nothing else can be trusted."
fi
echo "=============================================================="
exit "$FAIL"
