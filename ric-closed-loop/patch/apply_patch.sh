#!/usr/bin/env bash
# EE49904 — apply the patch that gives the FlexRIC emulated E2 node "state".
#
#   bash apply_patch.sh /path/to/flexric
#
# What it does:
#   1) copy ee49904_plant.{c,h} into examples/emulator/agent/
#   2) replace sm_mac.c · sm_slice.c with the patched versions (originals kept as *.orig)
#   3) add the plant source to the emulator CMakeLists
#
# Why file replacement instead of a diff: a context diff breaks as soon as upstream touches anything
# nearby, whereas a replaced file either compiles or does not, so a failure shows up immediately.
# In return, if upstream changes these two files a lot the patched versions go stale — which is why the version is pinned.
#
# LICENSE
# -------
# This script is EE49904 course material (2026). What it installs is not all the
# same thing: sm_mac.c and sm_slice.c are modified copies of FlexRIC files and
# carry FlexRIC's **OAI Public License v1.1**, while ee49904_plant.{c,h} were
# written from scratch for this course. Each file says which it is at the top.
# The binary you build from the result is a FlexRIC derivative either way.
#   upstream: https://gitlab.eurecom.fr/mosaic5g/flexric

set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
FLEXRIC="${1:-}"
[ -n "$FLEXRIC" ] || { echo "usage: bash apply_patch.sh /path/to/flexric" >&2; exit 1; }
AG="$FLEXRIC/examples/emulator/agent"
[ -d "$AG" ] || { echo "[ABORT] $AG does not exist — is the FlexRIC path correct?" >&2; exit 1; }

say(){ printf "\033[1m[patch]\033[0m %s\n" "$1"; }

say "copy plant → $AG"
cp "$SRC/ee49904_plant.h" "$SRC/ee49904_plant.c" "$AG/"

for f in sm_mac.c sm_slice.c; do
  if [ ! -f "$AG/$f.orig" ]; then
    cp "$AG/$f" "$AG/$f.orig"
    say "original kept: $f.orig"
  fi
  cp "$SRC/$f" "$AG/$f"
  say "replaced: $f"
done

CM="$AG/CMakeLists.txt"
if grep -q "ee49904_plant.c" "$CM"; then
  say "CMakeLists already updated — skipping"
else
  cp "$CM" "$CM.orig"
  # insert the plant before sm_gtp.c in the test_agent_obj list
  awk '{ if ($0 ~ /^[[:space:]]*sm_gtp\.c[[:space:]]*$/ && !done) { print "  ee49904_plant.c"; done=1 } print }' \
      "$CM.orig" > "$CM"
  grep -q "ee49904_plant.c" "$CM" || { echo "[ABORT] automatic CMakeLists edit failed — add ee49904_plant.c to test_agent_obj by hand" >&2; exit 1; }
  say "CMakeLists updated (original: CMakeLists.txt.orig)"
fi

# The plant uses pthread. Upstream already links it, but make it explicit.
if ! grep -q "EE49904_PLANT_THREADS" "$CM"; then
  printf '\n# EE49904: plant uses pthreads\nset(EE49904_PLANT_THREADS 1)\nfind_package(Threads REQUIRED)\n' >> "$CM"
fi

cat <<EOF

Patch applied.

To revert:
  cd $AG && for f in sm_mac.c sm_slice.c CMakeLists.txt; do
    [ -f \$f.orig ] && mv \$f.orig \$f; done && rm -f ee49904_plant.[ch]

Rebuild:
  cd $FLEXRIC/build && cmake -DXAPP_MULTILANGUAGE=ON .. && make -j\$(nproc)
EOF
