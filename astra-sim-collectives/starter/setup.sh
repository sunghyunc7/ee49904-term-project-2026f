#!/usr/bin/env bash
# EE49904 Term Project — ASTRA-sim starter kit setup
#
#   bash setup.sh              # install to the default location ~/astra-sim
#   ASTRA_SIM_ROOT=~/work/astra-sim bash setup.sh
#
# Supported: Ubuntu/Debian (including WSL2), macOS (Homebrew)
# Time: clone ~30 s + build 4-10 min (depends on core count)

set -euo pipefail
ROOT="${ASTRA_SIM_ROOT:-$HOME/astra-sim}"
REPO="https://github.com/astra-sim/astra-sim.git"

say(){ printf "\n\033[1m[setup]\033[0m %s\n" "$1"; }
die(){ printf "\n\033[1;31m[ABORT]\033[0m %s\n" "$1" >&2; exit 1; }

# ---------- 0. Environment pre-check ----------
say "Environment check"
case "$ROOT" in
  /mnt/[a-z]/*) die "The install path is on a Windows drive ($ROOT).
       Builds under /mnt/c in WSL are 5-10x slower. Set ASTRA_SIM_ROOT to a path under \$HOME.";;
esac
UNAME=$(uname -s)
if [ "$UNAME" = "Linux" ]; then
  CORES=$(nproc); MEMGB=$(free -g | awk '/^Mem:/{print $2}')
  echo "  cores $CORES / RAM ${MEMGB}GB"
  if grep -qi microsoft /proc/version 2>/dev/null && [ "$MEMGB" -lt 8 ]; then
    cat <<'EOF'
  ! The default WSL2 memory allocation is small. The build will be slow.
    On Windows, create (or edit) %USERPROFILE%\.wslconfig :
        [wsl2]
        memory=16GB
        processors=12
    Save it, run  wsl --shutdown  in PowerShell, then reopen WSL.
    (You can continue as is, but the build will take a long time.)
EOF
  fi
else
  CORES=$(sysctl -n hw.ncpu); echo "  cores $CORES (macOS)"
fi

# ---------- 1. Dependencies ----------
# Ask what is missing before reaching for a package manager — and therefore before asking for a
# password. On a machine that already has the toolchain (a lab server, or simply a second run) this
# kit needs no root at all, which is what BRIEF.md promises. The other kits in this course behave
# the same way. The Debian packages, for reference:
APT_PACKAGES="build-essential cmake ninja-build git python3 python3-pip python3-venv protobuf-compiler libprotobuf-dev"

deps_ok() {
  for t in cmake git protoc python3; do command -v "$t" >/dev/null 2>&1 || return 1; done
  command -v c++ >/dev/null 2>&1 || command -v g++ >/dev/null 2>&1 || return 1
  python3 -c "import venv" >/dev/null 2>&1 || return 1
  # libprotobuf-dev installs headers and no command of its own, and their path differs between
  # distributions. Rather than guess at one, ask the compiler — it cannot be wrong about this.
  if [ "$UNAME" = "Linux" ]; then
    printf '#include <google/protobuf/message.h>\nint main(){return 0;}\n' > "$DEPTMP/probe.cc"
    ${CXX:-c++} -fsyntax-only "$DEPTMP/probe.cc" >/dev/null 2>&1 || return 1
  fi
  return 0
}

DEPTMP="$(mktemp -d)"
trap 'rm -rf "$DEPTMP"' EXIT
say "Dependencies"
if deps_ok; then
  echo "  everything needed is already here — nothing to install (no root, no password)"
elif [ "$UNAME" = "Linux" ]; then
  command -v apt-get >/dev/null || die "Not an apt-based distribution, and something is missing.
       Install the equivalents of: $APT_PACKAGES
       The manual list with versions is in starter/README.md."
  SUDO=""; [ "$(id -u)" != "0" ] && SUDO="sudo"
  if [ -n "$SUDO" ] && ! command -v sudo >/dev/null 2>&1; then
    die "Some dependencies are missing and this account cannot install them.
       Ask whoever administers this machine for:
         $APT_PACKAGES
       Nothing else here needs root — once they are installed, run setup.sh again."
  fi
  echo "  something is missing — installing (${SUDO:+sudo }apt-get; you may be asked for your password)"
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq $APT_PACKAGES
else
  command -v brew >/dev/null || die "Homebrew is required: https://brew.sh"
  brew list cmake      >/dev/null 2>&1 || brew install cmake
  brew list ninja      >/dev/null 2>&1 || brew install ninja
  brew list protobuf   >/dev/null 2>&1 || brew install protobuf
fi
deps_ok || die "a dependency is still missing after the install step.
       Needed: a C++17 compiler, cmake, git, protoc, the protobuf headers, python3 with venv.
       On Debian or Ubuntu: $APT_PACKAGES"
echo "  cmake $(cmake --version | head -1 | awk '{print $3}') / protoc $(protoc --version | awk '{print $2}')"

# ---------- 2. Source ----------
# Pinned. run_sweep.py and smoke_test.sh depend on the upstream command-line flags and on the
# examples/ layout, so a moving HEAD can break this kit — and two teams on two revisions get two
# sets of numbers. To try a different revision deliberately:  ASTRA_SIM_REF=<commit> bash setup.sh
PINNED_SHA="518bd513ae110428cd62eb60efc0f3993fd53c70"
REF="${ASTRA_SIM_REF:-$PINNED_SHA}"
say "Fetching ASTRA-sim source → $ROOT"
if [ -d "$ROOT/.git" ]; then
  HAVE_SHA=$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)
  if [ "$HAVE_SHA" = "$REF" ]; then
    echo "  already present at ${HAVE_SHA:0:12} — the pinned revision; skipped"
  else
    echo "  ! already present at ${HAVE_SHA:0:12}, which is not the pinned revision (${REF:0:12})."
    echo "    The build continues with what is there, and your numbers may differ from other teams'."
    echo "    To install the pinned revision:  mv $ROOT $ROOT.old && bash setup.sh"
    echo "    Whichever you use, report it with your results."
  fi
else
  # Built in a scratch directory and moved into place only on success, so a failed fetch never
  # leaves a half-installed $ROOT behind.
  TMP="$ROOT.partial.$$"
  rm -rf "$TMP"
  git init -q -b main "$TMP"
  git -C "$TMP" remote add origin "$REPO"
  if git -C "$TMP" fetch -q --depth 1 origin "$REF" 2>/dev/null; then
    CHECKOUT=FETCH_HEAD
  else
    # Some mirrors refuse to serve a bare commit. Fall back to a full fetch (slower, ~430 MB).
    # The wording stays neutral: this also lands here when the network itself is the problem, and
    # in that case the full fetch fails too and the message below is the one that matters.
    echo "  could not fetch that revision on its own — trying a full fetch instead"
    git -C "$TMP" fetch -q origin || { rm -rf "$TMP"; die "could not fetch from $REPO.
       Check your network, then run setup.sh again."; }
    CHECKOUT="$REF"
  fi
  git -C "$TMP" checkout -q "$CHECKOUT" 2>/dev/null \
    || git -C "$TMP" checkout -q "origin/$REF" 2>/dev/null \
    || { rm -rf "$TMP"; die "'$REF' is not a revision of $REPO."; }
  git -C "$TMP" submodule update -q --init --recursive --depth 1 \
    || { rm -rf "$TMP"; die "could not fetch the submodules (Chakra and the network backends)."; }
  mv "$TMP" "$ROOT"
  echo "  ASTRA-sim at $(git -C "$ROOT" rev-parse --short=12 HEAD)"
fi

# ---------- 3. Python environment (for the workload generator) ----------
# The Python version determines the protobuf runtime version. protoc stamps its own version into
# the generated code (gencode), and if the runtime is older than that, **the load itself is refused**:
#   VersionError: gencode 7.35.1 runtime 6.33.6 — Runtime version cannot be older than gencode
# The default macOS python3 is the CommandLineTools 3.9, where pip can only go up to protobuf 6.x.
# But Homebrew protoc is 35.x (gencode 7.35.x) → the whole workload generator dies.
# (Measured: the same kit passed on a mac with python 3.14, which got protobuf 7.36, and failed on a mac with 3.9.)
say "Python virtual environment"
PYV=$(python3 -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null || echo 0)
PYBIN="$(command -v python3)"
if [ "${PYV:-0}" -lt 310 ]; then
  for c in python3.13 python3.12 python3.11 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    command -v "$c" >/dev/null 2>&1 || continue
    V=$("$c" -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null) || continue
    [ "${V:-0}" -ge 310 ] && { PYBIN="$c"; break; }
  done
  if [ "$PYBIN" = "$(command -v python3)" ] && [ "$UNAME" != "Linux" ] && command -v brew >/dev/null 2>&1; then
    echo "  default python3 is $(python3 -V 2>&1), which cannot match the protobuf runtime — installing a newer one"
    brew install python@3.13 >/dev/null 2>&1 || brew install python@3.13 || true
    for c in "$(brew --prefix)/opt/python@3.13/bin/python3.13" python3.13; do
      command -v "$c" >/dev/null 2>&1 && { PYBIN="$c"; break; }
    done
  fi
  [ "$PYBIN" != "$(command -v python3)" ] || die "Python 3.10 or newer is required (now $(python3 -V 2>&1)).
       Reading the gencode produced by protoc $(protoc --version | awk '{print $2}') needs a recent protobuf runtime,
       and that runtime installs only on 3.10 or newer.
       macOS:  brew install python@3.13   ·  Ubuntu:  sudo apt install python3.12 python3.12-venv"
  echo "  using $PYBIN instead of the default python3"
fi
# Reusing an existing venv that was created with an old Python would make the decision above pointless.
if [ -d "$ROOT/.venv" ]; then
  EXV=$("$ROOT/.venv/bin/python" -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null || echo 0)
  if [ "$EXV" -lt 310 ]; then
    echo "  existing virtual environment was created with Python $((EXV/100)).$((EXV%100)) — recreating it"
    rm -rf "$ROOT/.venv"
  fi
fi
[ -d "$ROOT/.venv" ] || "$PYBIN" -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install -q --upgrade pip
"$ROOT/.venv/bin/pip" install -q --upgrade protobuf
echo "  $("$ROOT/.venv/bin/python" -V 2>&1) / $("$ROOT/.venv/bin/python" -c 'import google.protobuf as p; print("protobuf", p.__version__)')"

# ---------- 3.5. Missing <cstdint> in a vendored header (GCC 15+) ----------
# Since GCC 15 the standard headers no longer include <cstdint> transitively. The cxxopts.hpp vendored
# by astra-sim uses uint8_t without including <cstdint>, so the whole compile step dies:
#   error: 'uint8_t' does not name a type; did you mean 'wint_t'?
# It passes silently on Ubuntu 24.04 (GCC 13) and macOS (clang), and blows up only on 26.04 (GCC 15.2)
# — measured 2026-08-26 on x86_64 WSL2. We do not wait for an upstream fix; we apply an idempotent patch here.
# (If the include is already there, or upstream has fixed it, this does nothing.)
say "Checking vendored headers (<cstdint>)"
python3 - "$ROOT" <<'PY' || die "Header patch failed — check the messages above."
import pathlib, re, sys

root = pathlib.Path(sys.argv[1])
MARK = "// EE49904 patch: GCC 15+ no longer pulls <cstdint> in transitively"
INT_RE = re.compile(r"\b(u?int(8|16|32|64)_t)\b")

targets = sorted(root.rglob("cxxopts.hpp"))
if not targets:
    print("  cxxopts.hpp not found — skipped (the upstream layout may have changed)")
    raise SystemExit(0)

patched = skipped = 0
for f in targets:
    try:
        src = f.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError as e:
        print("  ! read failed %s (%s)" % (f, e)); continue
    if re.search(r"#\s*include\s*<cstdint>", src) or re.search(r"#\s*include\s*<stdint\.h>", src):
        skipped += 1; continue
    if not INT_RE.search(src):
        skipped += 1; continue
    lines = src.splitlines(keepends=True)
    ins = None
    for i, ln in enumerate(lines):                      # before the first #include
        if ln.lstrip().startswith("#include"):
            ins = i; break
    if ins is None:                                     # no include: after the include guard
        for i, ln in enumerate(lines):
            s = ln.lstrip()
            if s.startswith("#pragma once") or s.startswith("#define"):
                ins = i + 1; break
    if ins is None:
        ins = 0
    lines.insert(ins, MARK + "\n#include <cstdint>\n")
    try:
        f.write_text("".join(lines), encoding="utf-8", errors="surrogateescape")
    except OSError as e:
        print("  ! write failed %s (%s)" % (f, e)); raise SystemExit(1)
    patched += 1
    print("  patch  %s" % f)

if patched == 0:
    print("  already fine (%d checked) — skipped" % skipped)

# Verify that the patch actually stuck. If it fails silently, the cause gets pushed into the build log.
for f in targets:
    src = f.read_text(encoding="utf-8", errors="surrogateescape")
    if INT_RE.search(src) and not re.search(r"#\s*include\s*<(cstdint|stdint\.h)>", src):
        print("  ! %s still has no <cstdint>" % f); raise SystemExit(1)
PY

# ---------- 4. Build ----------
# The upstream build.sh calls `nproc` to pick its parallelism. macOS has no such command, so the
# build dies as soon as it starts (`build.sh: line 32: nproc: command not found` — measured on 2 macs).
# Instead of making you install coreutils, we put a one-line shim that gives the same answer at the front of PATH.
if ! command -v nproc >/dev/null 2>&1; then
  mkdir -p "$ROOT/.bin"
  printf '#!/bin/sh\nsysctl -n hw.ncpu 2>/dev/null || echo 4\n' > "$ROOT/.bin/nproc"
  chmod +x "$ROOT/.bin/nproc"
  export PATH="$ROOT/.bin:$PATH"
  echo "  nproc not found — created a shim ($ROOT/.bin/nproc → $(nproc) cores)"
fi

# Since protobuf 22, protobuf sits on top of abseil. But the upstream CMakeLists by default uses the
# old FindProtobuf module and links only `-lprotobuf` → every abseil symbol is left unresolved:
#   ld: symbol(s) not found ... absl::lts_2026xxxx::log_internal::MakeCheckOpString ...
# The protoc on Ubuntu 24.04 is 3.21, so it does not have this problem (which is why Spark and WSL pass),
# while Homebrew protoc is 35.x, and linking broke on 2 macs — measured.
# Upstream already has an escape hatch: PROTOBUF_FROM_SOURCE=True switches to `find_package(protobuf CONFIG)`
# and links `protobuf::libprotobuf`, so abseil comes along as a transitive dependency.
# (The name says "from source", but what it really means is "use the protobuf CMake config package".)
PROTOC_MAJOR=$(protoc --version 2>/dev/null | awk '{print $2}' | cut -d. -f1)
# CONFIG mode can only be turned on when the protobuf CMake config package actually exists.
# libprotobuf-dev on Ubuntu does not ship that file (exactly the situation the upstream comment describes),
# so turning it on unconditionally would break the currently working Linux build at the configure step.
PB_CONFIG=""
for p in "$( (brew --prefix protobuf) 2>/dev/null)/lib/cmake/protobuf" \
         /usr/local/lib/cmake/protobuf /usr/lib/cmake/protobuf /usr/lib/*/cmake/protobuf; do
  [ -f "$p/protobuf-config.cmake" ] && { PB_CONFIG="$p"; break; }
done
if [ "${PROTOC_MAJOR:-0}" -ge 22 ] 2>/dev/null && [ -n "$PB_CONFIG" ]; then
  export PROTOBUF_FROM_SOURCE=True
  echo "  protoc $(protoc --version | awk '{print $2}') (abseil-based) → linking in CMake config mode"
  echo "    config package: $PB_CONFIG"
  if [ "$UNAME" != "Linux" ] && command -v brew >/dev/null 2>&1; then
    export CMAKE_PREFIX_PATH="$(brew --prefix):${CMAKE_PREFIX_PATH:-}"
  fi
  # CMakeCache remembers the previous mode. If the mode changes, the cache must be dropped so find_package runs again.
  BDIR="$ROOT/build/astra_analytical/build"
  if [ -f "$BDIR/CMakeCache.txt" ] && [ ! -f "$BDIR/.ee49904-protobuf-config" ]; then
    echo "  previous build cache was made with the old link mode — deleting it"
    rm -rf "$BDIR"
  fi
  mkdir -p "$BDIR" && touch "$BDIR/.ee49904-protobuf-config"
elif [ "${PROTOC_MAJOR:-0}" -ge 22 ] 2>/dev/null; then
  cat <<'EOF'
  ! protoc is 22 or newer (abseil-based), but the protobuf CMake config package was not found.
    Building like this gives the following error at the link step:
        ld: symbol(s) not found ... absl::...::log_internal::MakeCheckOpString ...
    macOS:  brew install protobuf   (the config package is installed with it)
    Linux:  use the distribution protobuf, or build it from source and re-run with PROTOBUF_FROM_SOURCE=True.
    (Continuing anyway — if it fails, read the message above first.)
EOF
fi

say "Build (analytical backend) — takes a few minutes"
t0=$(date +%s)
( cd "$ROOT" && ./build/astra_analytical/build.sh ) || die "Build failed — check the log above.
       If it fails with 'does not name a type' on uint8_t / int64_t, the cause is the GCC 15+ header cleanup.
       Step 3.5 above fixes cxxopts.hpp, but the same thing can happen in another vendored header —
       add #include <cstdint> at the very top of that file and re-run (or use Ubuntu 24.04)."
echo "  build took $(( $(date +%s) - t0 )) s"

# ---------- 5. Verification ----------
say "Verifying the install"
for b in AstraSim_Analytical_Congestion_Unaware AstraSim_Analytical_Congestion_Aware; do
  P="$ROOT/build/astra_analytical/build/bin/$b"
  [ -x "$P" ] && echo "  OK  $b" || die "build output missing: $P"
done

# Actually import the Python gencode that protoc produced during the build. If we do not check it here,
# the smoke test shows three checks failing separately (workload generator, sweep,
# sanity check) and hides the fact that there is one cause — that is exactly what happened in our measurements.
PB2="$ROOT/extern/graph_frontend/chakra/schema/protobuf"
if [ -f "$PB2/et_def_pb2.py" ]; then
  if ERR=$(cd "$PB2" && "$ROOT/.venv/bin/python" -c "import et_def_pb2" 2>&1); then
    echo "  OK  et_def_pb2 import (protoc gencode ↔ Python runtime match)"
  else
    echo "  ! could not load the gencode. Upgrading the protobuf runtime and retrying."
    "$ROOT/.venv/bin/pip" install -q --upgrade protobuf
    if ERR=$(cd "$PB2" && "$ROOT/.venv/bin/python" -c "import et_def_pb2" 2>&1); then
      echo "  OK  et_def_pb2 import (fixed with protobuf $("$ROOT/.venv/bin/python" -c 'import google.protobuf as p;print(p.__version__)'))"
    else
      die "The protoc gencode and the Python protobuf runtime do not match:
$(printf '%s' "$ERR" | tail -3)
       The runtime cannot be older than the gencode. In most cases the Python in the virtual environment
       is too old to install a recent protobuf (now $("$ROOT/.venv/bin/python" -V 2>&1)).
       Recreate it with Python 3.10 or newer:  rm -rf $ROOT/.venv && bash setup.sh"
    fi
  fi
fi

cat <<EOF

======================================================================
 Install complete.  ASTRA_SIM_ROOT=$ROOT

 Next steps:
   bash smoke_test.sh                 # 5-minute check — it must pass
   python3 run_sweep.py --root "$ROOT" --help

 For later sessions, it is convenient to put this in your shell config (~/.bashrc etc.):
   export ASTRA_SIM_ROOT="$ROOT"
$([ -x "$ROOT/.bin/nproc" ] && printf ' \n macOS note: the upstream build.sh calls nproc, which macOS does not have.\n   When rebuilding by hand, put the shim at the front of PATH:\n     export PATH="%s/.bin:$PATH"\n' "$ROOT")
======================================================================
EOF
