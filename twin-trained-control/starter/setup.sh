#!/usr/bin/env bash
# EE49904 Term Project — twin-trained control starter kit setup
#
#   bash setup.sh                      # default location ~/twin-venv
#   TWIN_VENV=~/work/venv bash setup.sh
#
# Supported: Ubuntu/Debian (including WSL2), macOS
# Time: 3-10 min (mostly download time). No GPU needed.

set -euo pipefail
VENV="${TWIN_VENV:-$HOME/twin-venv}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# ---------- Pinned versions ----------
# Every team installs the same stack, so that numbers can be compared across teams and against
# the course's verification runs. Mitsuba and Dr.Jit are pinned by sionna-rt itself.
# Pinning sionna alone is not enough: sionna-rt (the ray tracer) and torch would still float.
PIN_SIONNA="2.1.0"
PIN_SIONNA_RT="2.1.0"
PIN_TORCH="2.14.0"
PIN_MISS=0          # set to 1 if any pin could not be honored; reported at the end

say(){ printf "\n\033[1m[setup]\033[0m %s\n" "$1"; }
die(){ printf "\n\033[1;31m[ABORT]\033[0m %s\n" "$1" >&2; exit 1; }

# ---------- 0. Environment check ----------
say "Environment check"
UNAME=$(uname -s)
case "$HERE" in
  /mnt/[a-z]/*) echo "  ! This folder is on a Windows drive (/mnt/…). Move it under \$HOME.";;
esac
if [ "$UNAME" = "Linux" ]; then
  echo "  cores $(nproc) / RAM $(free -g | awk '/^Mem:/{print $2}')GB / free disk $(df -h "$HOME" | awk 'NR==2{print $4}')"
  grep -qi microsoft /proc/version 2>/dev/null && echo "  WSL2 environment."
  command -v python3 >/dev/null || die "python3 not found: sudo apt install python3 python3-venv"
  python3 -c "import venv" 2>/dev/null || {
    SUDO=""; [ "$(id -u)" != "0" ] && SUDO="sudo"
    say "Installing python3-venv"; $SUDO apt-get update -qq
    $SUDO apt-get install -y -qq python3-venv python3-pip
  }
else
  echo "  macOS $(sw_vers -productVersion 2>/dev/null) / cores $(sysctl -n hw.ncpu)"
  command -v python3 >/dev/null || die "python3 not found: brew install python"
fi
echo "  python $(python3 -c 'import sys;print(".".join(map(str,sys.version_info[:3])))') ($(command -v python3))"

# If the Python version is not pinned, each machine gets a different Sionna. Measured: on one mac
# the venv was built with the python 3.9 from /Library/Developer/CommandLineTools and got an old Sionna
# (mitsuba variant `llvm_ad_rgb`), while another mac used homebrew 3.14 and got the new one
# (`llvm_ad_mono_polarized`). If the same kit installs different things on different machines,
# student results cannot be compared.
# Sionna 2.x requires Python 3.11 or newer. With 3.10 pip does not fail — it silently resolves to
# Sionna 1.2.x, a different major version (measured on Ubuntu 22.04, whose default python3 is 3.10).
PYV=$(python3 -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])')
if [ "$PYV" -lt 311 ]; then
  ALT=""
  for c in python3.13 python3.12 python3.11 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    command -v "$c" >/dev/null 2>&1 || continue
    V=$("$c" -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null) || continue
    [ "${V:-0}" -ge 311 ] && { ALT="$c"; break; }
  done
  # If missing, install it. brew install llvm is also done automatically below, so asking the student
  # for a manual install only here would be inconsistent, and in testing it really stopped here (macbook = 3.9 only).
  if [ -z "$ALT" ] && [ "$UNAME" != "Linux" ] && command -v brew >/dev/null 2>&1; then
    echo "  No Python 3.11+ found; installing (brew install python@3.13)"
    brew install python@3.13 >/dev/null 2>&1 || brew install python@3.13 || true
    for c in "$(brew --prefix)/opt/python@3.13/bin/python3.13" python3.13; do
      command -v "$c" >/dev/null 2>&1 && { ALT="$c"; break; }
    done
  fi
  if [ -n "$ALT" ]; then
    echo "  ! Default python3 is too old for Sionna 2.x; using $ALT"
    PYBIN="$ALT"
  elif [ "$PYV" -ge 310 ]; then
    # No 3.11+ and perhaps no sudo (a shared server). Do not block: continue unpinned and say so.
    echo "  ! Python 3.10 only: Sionna $PIN_SIONNA needs 3.11+. Continuing UNPINNED — pip will pick an older Sionna."
    echo "    To get the course versions:  sudo apt install python3.11 python3.11-venv  (Ubuntu 22.04),"
    echo "    then remove $VENV and run this script again."
    PYBIN="$(command -v python3)"; PIN_MISS=1
  else
    die "Sionna 2.x needs Python 3.11 or newer (now $(python3 -V 2>&1)).
       macOS:  brew install python@3.13   ·  Ubuntu 22.04:  sudo apt install python3.11 python3.11-venv
       Install it, then run this script again."
  fi
else
  PYBIN="$(command -v python3)"
fi

# ---------- 1. Virtual environment ----------
say "Virtual environment → $VENV"
if [ -d "$VENV" ]; then
  # Reusing an existing venv built with an old Python would make the check above meaningless.
  EXV=$("$VENV/bin/python" -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null || echo 0)
  NEWV=$("$PYBIN" -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])')
  if [ "$EXV" -lt 310 ] || { [ "$EXV" -lt 311 ] && [ "$NEWV" -ge 311 ]; }; then
    echo "  Existing venv was built with Python $((EXV/100)).$((EXV%100)) — rebuilding it"
    rm -rf "$VENV"
    "$PYBIN" -m venv "$VENV"
  fi
else
  "$PYBIN" -m venv "$VENV"
fi
PY="$VENV/bin/python"; PIP="$VENV/bin/pip"
"$PIP" install -q --upgrade pip
echo "  $("$PY" -V 2>&1)"

# ---------- 2. PyTorch first, CPU-only ----------
# Sionna 2.x is built on PyTorch. Left alone, on Linux the full set of CUDA libraries
# comes along and the venv grows past 5 GB. This assignment uses no GPU, so install the CPU wheel first,
# then install Sionna so that it reuses the already-satisfied dependency.
say "Installing PyTorch (CPU-only) — before Sionna"
CPU_INDEX="https://download.pytorch.org/whl/cpu"
if curl -fsS -o /dev/null --max-time 15 "$CPU_INDEX/" 2>/dev/null; then
  echo "  Using the CPU-only index"
  TORCH_FROM="--index-url $CPU_INDEX"
else
  echo "  ! Cannot reach the CPU index; installing from default PyPI (several GB on Linux)."
  TORCH_FROM=""
fi
# $TORCH_FROM is left unquoted on purpose: it is either empty or two words (macOS ships bash 3.2,
# where an empty array under `set -u` is an error).
if [ "$PIN_MISS" = "0" ] && "$PIP" install -q $TORCH_FROM "torch==$PIN_TORCH"; then
  echo "  torch $PIN_TORCH (pinned)"
else
  [ "$PIN_MISS" = "0" ] && echo "  ! torch==$PIN_TORCH is not available for this platform/Python; installing the current torch instead."
  PIN_MISS=1
  "$PIP" install -q $TORCH_FROM torch || die "torch install failed"
fi

say "Installing Sionna (PHY + RT)"
if [ "$PIN_MISS" = "0" ] && "$PIP" install -q "sionna==$PIN_SIONNA" "sionna-rt==$PIN_SIONNA_RT"; then
  echo "  sionna $PIN_SIONNA / sionna-rt $PIN_SIONNA_RT (pinned)"
else
  [ "$PIN_MISS" = "0" ] && echo "  ! The pinned Sionna cannot be installed here; installing whatever pip resolves."
  PIN_MISS=1
  "$PIP" install -q sionna || die "sionna install failed"
fi

# ---------- 2b. LLVM backend of Dr.Jit (macOS and Linux) ----------
# Sionna RT traces rays on the CPU through the **LLVM backend** of Mitsuba/Dr.Jit, and Dr.Jit loads
# libLLVM at run time. The wheels do not ship it. Without it the import dies right away like this:
#   ImportError: jit_init_thread_state(): the LLVM backend is inactive because the LLVM
#   shared library ("libLLVM.so" / "libLLVM.dylib") could not be found!
# macOS: never there. Install LLVM with Homebrew and tell Dr.Jit where it is.
#   (Measured on 2 macs — this was the only reason this kit failed on macOS.)
# Linux: often there only because some other package pulled it in. A clean image — a fresh WSL2
#   distribution, a shared server — has none (measured on a clean Ubuntu 22.04: setup stopped here).
#   So ask Dr.Jit first; only if it finds nothing, look for the library, install it if it is
#   missing, and record its path. On a machine where Dr.Jit already finds LLVM this step does nothing.
LLVM_LIB=""
LLVM_PROBE='import sys, drjit as dr; sys.exit(0 if dr.has_backend(dr.JitBackend.LLVM) else 1)'

llvm_ok(){      # $1 = library to try ("" = Dr.Jit's own search). True if the LLVM backend comes up.
  if [ -n "${1:-}" ]; then DRJIT_LIBLLVM_PATH="$1" "$PY" -c "$LLVM_PROBE" >/dev/null 2>&1
  else "$PY" -c "$LLVM_PROBE" >/dev/null 2>&1; fi
}

find_llvm(){    # Sets LLVM_LIB to the first libLLVM the backend really comes up with. False if none does.
  local f real seen=" "
  # No `ls`, `head` or `grep -q` here: under `set -o pipefail` those end a script on an unmatched
  # glob or a closed pipe. An unmatched glob simply stays a literal and fails the -f test.
  for f in $( { ldconfig -p 2>/dev/null || /sbin/ldconfig -p 2>/dev/null || true; } | awk '/libLLVM/{print $NF}' ) \
           /usr/lib/llvm-*/lib/libLLVM*.so* /usr/lib/*-linux-gnu/libLLVM*.so* \
           /usr/lib64/libLLVM*.so* /usr/lib/libLLVM*.so* /usr/local/lib/libLLVM*.so* \
           "$VENV"/llvm/usr/lib/llvm-*/lib/libLLVM*.so* "$VENV"/llvm/usr/lib/*-linux-gnu/libLLVM*.so*; do
    [ -f "$f" ] || continue
    real=$(readlink -f "$f" 2>/dev/null || echo "$f")
    case "$seen" in *" $real "*) continue;; esac
    seen="$seen$real "
    if llvm_ok "$real"; then LLVM_LIB="$real"; return 0; fi
  done
  return 1
}

llvm_pkg(){     # A libllvm package this distribution offers. Versions verified with this kit come first.
  local v
  for v in 18 17 20 15 19 16 14; do
    [ -n "$(apt-cache show "libllvm$v" 2>/dev/null)" ] && { echo "libllvm$v"; return 0; }
  done
  return 1
}

if [ "$UNAME" != "Linux" ]; then
  say "macOS — Dr.Jit LLVM backend"
  for p in "$(brew --prefix llvm 2>/dev/null)/lib/libLLVM.dylib" \
           /opt/homebrew/opt/llvm/lib/libLLVM.dylib \
           /usr/local/opt/llvm/lib/libLLVM.dylib; do
    [ -f "$p" ] && { LLVM_LIB="$p"; break; }
  done
  if [ -z "$LLVM_LIB" ]; then
    command -v brew >/dev/null || die "Homebrew is required (to install LLVM): https://brew.sh"
    echo "  libLLVM.dylib not found; installing (brew install llvm — several minutes, about 1.5 GB)"
    brew install llvm || die "brew install llvm failed — install it manually and run again."
    LLVM_LIB="$(brew --prefix llvm)/lib/libLLVM.dylib"
  fi
  [ -f "$LLVM_LIB" ] || die "libLLVM.dylib not found (expected at: $LLVM_LIB)"
else
  say "Linux — Dr.Jit LLVM backend"
  if llvm_ok ""; then
    echo "  Dr.Jit finds libLLVM by itself — nothing to do"
  elif find_llvm; then
    echo "  Dr.Jit's own search finds no libLLVM, but this one works: $LLVM_LIB"
  elif "$PY" -c "import sionna.rt" >/dev/null 2>&1; then
    echo "  No libLLVM, but Sionna RT already runs on another backend (GPU) — leaving the system alone"
  else
    echo "  No usable libLLVM on this system."
    if command -v apt-get >/dev/null 2>&1 && LLVM_PKG=$(llvm_pkg); then
      SUDO=""; [ "$(id -u)" != "0" ] && SUDO="sudo"
      echo "  Installing $LLVM_PKG  (${SUDO:+sudo }apt-get install — a 30 MB download, 120 MB on disk)"
      if { [ -z "$SUDO" ] || command -v sudo >/dev/null 2>&1; } && $SUDO apt-get install -y -qq "$LLVM_PKG"; then
        :
      else
        # No root (a shared server): the package can still be unpacked into the venv.
        echo "  ! System install not possible here. Unpacking $LLVM_PKG into $VENV/llvm instead (no root needed)."
        ( mkdir -p "$VENV/llvm" && cd "$VENV/llvm" && apt-get download "$LLVM_PKG" >/dev/null 2>&1 \
            && for d in ./libllvm*.deb; do dpkg -x "$d" . && rm -f "$d"; done ) || true
      fi
      # Prefer Dr.Jit's own search: a recorded path goes stale when the system upgrades LLVM.
      llvm_ok "" || find_llvm || true
    fi
    if [ -n "$LLVM_LIB" ]; then
      echo "  Using $LLVM_LIB"
    elif llvm_ok ""; then
      echo "  Installed — Dr.Jit now finds libLLVM by itself"
    else
      echo "  ! Still no usable libLLVM. Sionna RT will not import (checked in the next step)."
      echo "    Ubuntu 24.04:  sudo apt install libllvm18    ·  Ubuntu 22.04:  sudo apt install libllvm15"
      echo "    Fedora:        sudo dnf install llvm-libs    ·  no root: ask the administrator for one of these"
      echo "    Then run this script again."
    fi
  fi
fi

# Record the library for later shells. Only when a path had to be chosen: always on macOS, and on
# Linux only if Dr.Jit's own search fails.
if [ -n "$LLVM_LIB" ]; then
  export DRJIT_LIBLLVM_PATH="$LLVM_LIB"
  echo "  DRJIT_LIBLLVM_PATH=$LLVM_LIB"
  # Shells the student opens later need it too. Append it to the venv activate script.
  if ! grep -q DRJIT_LIBLLVM_PATH "$VENV/bin/activate" 2>/dev/null; then
    printf '\n# EE49904: LLVM backend for Sionna RT (added by setup.sh)\n[ -f "%s" ] && export DRJIT_LIBLLVM_PATH="%s"\n' \
      "$LLVM_LIB" "$LLVM_LIB" >> "$VENV/bin/activate"
    echo "  Also written to activate (applies automatically in new shells)"
  fi
  # activate alone is not enough — smoke_test.sh and student scripts usually
  # call $VENV/bin/python directly, and then activate never runs.
  # sitecustomize is always imported when the interpreter starts, so it covers both paths.
  # The path is used only while the file exists: a dead DRJIT_LIBLLVM_PATH switches Dr.Jit's own
  # search off, which would turn an LLVM upgrade into a broken kit.
  SITE="$("$PY" -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])')"
  cat > "$SITE/sitecustomize.py" <<PYEOF
# EE49904 (generated by setup.sh) — location of the LLVM backend for Dr.Jit.
# The Mitsuba/Dr.Jit wheels do not include libLLVM, so without this the sionna.rt import can fail.
import os
if os.path.exists("$LLVM_LIB"):
    os.environ.setdefault("DRJIT_LIBLLVM_PATH", "$LLVM_LIB")
PYEOF
  # sitecustomize alone is not enough either: Debian and Ubuntu ship their own
  # /usr/lib/python3.X/sitecustomize.py, which comes first on sys.path and hides the one above
  # (measured: setup passed, then smoke_test.sh failed on the import). A .pth file in the venv is
  # read at every interpreter start no matter what, so write the same thing there as well.
  printf 'import os; os.path.exists("%s") and os.environ.setdefault("DRJIT_LIBLLVM_PATH", "%s")\n' \
    "$LLVM_LIB" "$LLVM_LIB" > "$SITE/ee49904_llvm.pth"
  echo "  Wrote sitecustomize.py and ee49904_llvm.pth ($SITE)"
  # Check it the way a later shell will see it: a fresh interpreter, without the variable exported.
  if ( unset DRJIT_LIBLLVM_PATH; "$PY" -c "$LLVM_PROBE" >/dev/null 2>&1 ); then
    echo "  A fresh interpreter finds the LLVM backend — recorded correctly"
  else
    echo "  ! A fresh interpreter still does not find the LLVM backend. Until this is fixed, run"
    echo "      export DRJIT_LIBLLVM_PATH=\"$LLVM_LIB\""
    echo "    in every new shell before using the kit, and tell the TA."
  fi
fi

# ---------- 3. Verify ----------
say "Verifying the install"
"$PY" - <<'PYCODE' || die "import failed. If the error above names libLLVM, install it
       (Ubuntu 24.04: sudo apt install libllvm18 · Ubuntu 22.04: sudo apt install libllvm15 · macOS: brew install llvm)
       and run this script again."
import torch, sionna, sionna.phy, sionna.rt, mitsuba as mi
from importlib.metadata import version
print(f"  sionna {sionna.__version__} / sionna-rt {version('sionna-rt')} / torch {torch.__version__}")
print(f"  mitsuba {mi.__version__} variant={mi.variant()}   (llvm_* = CPU, cuda_* = this machine's GPU — both are fine)")
print(f"  CUDA {torch.cuda.is_available()} (False is expected — the link layer is CPU-only)")
PYCODE
echo "  venv size $(du -sh "$VENV" 2>/dev/null | cut -f1)"
if [ "$PIN_MISS" = "0" ]; then
  echo "  Versions match the course pin (sionna $PIN_SIONNA / sionna-rt $PIN_SIONNA_RT / torch $PIN_TORCH)."
else
  echo "  ! UNPINNED install: the versions above differ from the course pin"
  echo "    (sionna $PIN_SIONNA / sionna-rt $PIN_SIONNA_RT / torch $PIN_TORCH). The kit may still work, but your"
  echo "    numbers can differ from other teams'. State the versions above in your report."
fi

cat <<EOF

======================================================================
 Install complete.  TWIN_VENV=$VENV

 Next steps:
   bash smoke_test.sh
   $PY build_twin.py --scene simple_street_canyon --tx-dbm 20 --out twin.npz
   $PY run_policy.py --twin twin.npz --delay 0,5,20,50 --est-std 0,2 --speed 20

 For convenience in later sessions:
   export TWIN_VENV="$VENV"
$([ -n "$LLVM_LIB" ] && printf ' \n LLVM note: Sionna RT uses %s.\n   It is recorded in the venv, so no extra setup is needed.\n   If you move LLVM elsewhere, update DRJIT_LIBLLVM_PATH.\n' "$LLVM_LIB")
======================================================================
EOF
