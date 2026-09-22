#!/usr/bin/env bash
# EE49904 Term Project — split inference starter kit setup
#
#   bash setup.sh                 # creates the virtualenv at the default location ~/split-inference-venv
#   SPLIT_VENV=~/work/venv bash setup.sh
#
# Supported: Ubuntu/Debian (incl. WSL2), macOS
# Takes: 2-6 min (mostly the PyTorch download)

set -euo pipefail
VENV="${SPLIT_VENV:-$HOME/split-inference-venv}"
HERE="$(cd "$(dirname "$0")" && pwd)"

say(){ printf "\n\033[1m[setup]\033[0m %s\n" "$1"; }
die(){ printf "\n\033[1;31m[ABORT]\033[0m %s\n" "$1" >&2; exit 1; }

# ---------- 0. Environment check ----------
say "Environment check"
UNAME=$(uname -s)
case "$HERE" in
  /mnt/[a-z]/*) echo "  ! This folder is on a Windows drive (/mnt/…).
    Measurements will be slow and noisy. Move the folder under \$HOME and run again.";;
esac
if [ "$UNAME" = "Linux" ]; then
  CORES=$(nproc); MEMGB=$(free -g | awk '/^Mem:/{print $2}')
  echo "  cores $CORES / RAM ${MEMGB}GB / free disk $(df -h "$HOME" | awk 'NR==2{print $4}')"
  grep -qi microsoft /proc/version 2>/dev/null && echo "  WSL2 environment."
  command -v python3 >/dev/null || die "python3 not found: sudo apt install python3 python3-venv"
  python3 -c "import venv" 2>/dev/null || {
    say "Installing python3-venv"
    SUDO=""; [ "$(id -u)" != "0" ] && SUDO="sudo"
    $SUDO apt-get update -qq && $SUDO apt-get install -y -qq python3-venv python3-pip
  }
else
  echo "  macOS $(sw_vers -productVersion 2>/dev/null) / cores $(sysctl -n hw.ncpu)"
  command -v python3 >/dev/null || die "python3 not found: brew install python"
fi
echo "  python $(python3 -c 'import sys;print(".".join(map(str,sys.version_info[:3])))')"

# ---------- 1. Virtualenv ----------
say "Virtualenv → $VENV"
[ -d "$VENV" ] || python3 -m venv "$VENV"
PY="$VENV/bin/python"; PIP="$VENV/bin/pip"
"$PIP" install -q --upgrade pip

# ---------- 2. PyTorch (CPU-only) ----------
# Important: a plain `pip install torch` on Linux drags in the whole set of NVIDIA CUDA
# libraries and the virtualenv grows past 5 GB. This project never uses a GPU, so we use
# the CPU-only index (about 500 MB). The macOS wheel is CPU/MPS anyway, so no difference.
say "Installing PyTorch (CPU-only)"
CPU_INDEX="https://download.pytorch.org/whl/cpu"
if curl -fsS -o /dev/null --max-time 15 "$CPU_INDEX/" 2>/dev/null; then
  echo "  using the CPU-only index: $CPU_INDEX"
  "$PIP" install -q --index-url "$CPU_INDEX" torch torchvision \
    || die "install from the CPU index failed"
else
  echo "  ! Cannot reach the CPU index; installing from the default PyPI."
  echo "    On Linux this also pulls the CUDA libraries and uses several GB (it still works)."
  "$PIP" install -q torch torchvision || die "PyTorch install failed"
fi

say "Remaining dependencies"
"$PIP" install -q numpy

# ---------- 3. Import check ----------
say "Runtime check"
"$PY" - <<'PYCODE' || die "PyTorch import failed"
import torch, torchvision
print(f"  torch {torch.__version__} / torchvision {torchvision.__version__}")
print(f"  threads {torch.get_num_threads()} / CUDA {torch.cuda.is_available()} (False is expected)")
PYCODE

# ---------- 4. Pre-fetch model weights ----------
# The default experiments use random weights and need no download. But the first time
# you use --pretrained (accuracy experiments — extensions B and C) torchvision goes out to
# fetch the checkpoint, and on a slow line that one fetch eats 20+ minutes of the run — in
# the 2026-08-26 WSL2 measurement this was what pushed the first run past 1200 s; with a warm
# cache the same run took 50 s. Fetch once at install time (about 170 MB; skipped if cached).
say "Pre-fetching model weights (about 170 MB — skipped if already cached)"
"$PY" - <<'PYCODE'
import sys
try:
    import torchvision.models as tv
except Exception as e:
    print(f"  ! torchvision import failed — skipping ({e})"); sys.exit(0)
ok = fail = 0
for fn in (tv.resnet18, tv.resnet50, tv.mobilenet_v3_large):
    try:
        fn(weights="DEFAULT")
        print(f"  OK  {fn.__name__}")
        ok += 1
    except Exception as e:
        print(f"  ! {fn.__name__} download failed: {type(e).__name__}")
        fail += 1
if fail:
    print("  If the network is blocked you can skip this for now —")
    print("  the default experiments use random weights, so they are not affected.")
    print("  Before using --pretrained, run setup.sh once more while online.")
PYCODE

# ---------- 5. Verify ----------
say "Verifying the install"
SZ=$(du -sh "$VENV" 2>/dev/null | cut -f1); echo "  virtualenv size $SZ"
CACHE=$(du -sh "$HOME/.cache/torch/hub/checkpoints" 2>/dev/null | cut -f1)
[ -n "${CACHE:-}" ] && echo "  weights cache $CACHE ($HOME/.cache/torch/hub/checkpoints)"

cat <<EOF

======================================================================
 Setup complete.  SPLIT_VENV=$VENV

 Next steps:
   bash smoke_test.sh
   $PY split_bench.py --help
   $PY split_serve.py --help

 For convenience in later sessions:
   export SPLIT_VENV="$VENV"
   alias spy="$PY"
======================================================================
EOF
