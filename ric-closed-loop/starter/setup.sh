#!/usr/bin/env bash
# EE49904 Term Project — RIC closed-loop control starter kit setup
#
#   bash setup.sh                    # default location ~/ee49904-ric
#   RIC_ROOT=~/work/ric bash setup.sh
#
# What it does: dependencies → clone FlexRIC → **apply the stateful plant patch** → build (incl. Python SDK)
# Supported: Ubuntu/Debian (incl. WSL2) · DGX OS and other apt-based Linux
# Takes: 3-8 minutes.  No GPU needed.
#
# macOS is not supported — FlexRIC's E2AP uses the Linux kernel SCTP.
# Mac users cannot use WSL2, so use a Linux VM (UTM/multipass) or the shared
# Linux host that the TA points you to.

set -euo pipefail
ROOT="${RIC_ROOT:-$HOME/ee49904-ric}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PATCHDIR="$(cd "$HERE/../patch" && pwd)"
REPO="https://github.com/openaicellular/flexric.git"

say(){ printf "\n\033[1m[setup]\033[0m %s\n" "$1"; }
die(){ printf "\n\033[1;31m[ABORT]\033[0m %s\n" "$1" >&2; exit 1; }

# ---------- 0. Platform ----------
say "Checking the environment"
[ "$(uname -s)" = "Linux" ] || die "Linux is required (E2AP uses kernel SCTP). On macOS use a VM."
echo "  $(uname -srm) / cores $(nproc) / RAM $(free -g | awk '/^Mem:/{print $2}')GB"
grep -qi microsoft /proc/version 2>/dev/null && echo "  Running under WSL2."
case "$ROOT" in /mnt/[a-z]/*) die "The install path is on a Windows drive. Set RIC_ROOT to a path under \$HOME.";; esac

# Check SCTP up front. Without it the build succeeds but nothing runs, so it is better to say so now.
if python3 - <<'PY' >/dev/null 2>&1
import socket; socket.socket(socket.AF_INET, socket.SOCK_SEQPACKET, 132).close()
PY
then echo "  SCTP: available"
else
  echo "  ! Cannot create an SCTP socket. Run 'sudo modprobe sctp' and check again."
  echo "    (The build continues. SCTP is needed at run time.)"
fi

# ---------- 1. Dependencies ----------
say "Build dependencies"
command -v apt-get >/dev/null || die "This is not an apt-based distribution. See the manual list in the README."
SUDO=""; [ "$(id -u)" != "0" ] && SUDO="sudo"
APT_PACKAGES="git cmake make gcc g++ pkg-config swig python3 python3-dev python3-venv libsctp-dev bison flex libtool autoconf"

# apt is called only when something is missing. It takes a system-wide lock, and on a freshly installed
# Ubuntu (WSL2 or a VM) the automatic updates usually hold that lock for the first minutes after boot.
# An unconditional apt-get here made every re-run of setup.sh depend on nobody else using apt at that
# moment — measured 2026-09-22: setup died after 1 s with "Could not get lock", exit code 100.
MISSING=""
for p in $APT_PACKAGES; do
  [ "$(dpkg-query -W -f='${db:Status-Status}' "$p" 2>/dev/null)" = "installed" ] || MISSING="$MISSING $p"
done

# apt-get that waits (up to 10 min) when another apt or dpkg holds the lock, instead of dying.
# apt's own DPkg::Lock::Timeout does not cover the package-list lock that 'apt-get update' takes
# (checked on apt 2.8.3, Ubuntu 24.04), so the waiting is done here. No pipes: see lesson on SIGPIPE.
apt_wait(){
  local deadline=$(( $(date +%s) + 600 )) out rc who pid n=0
  while :; do
    if out=$($SUDO apt-get "$@" 2>&1); then
      [ -z "$out" ] || printf '%s\n' "$out"
      return 0
    else
      rc=$?
    fi
    if [[ $out != *"Could not get lock"* ]]; then
      printf '%s\n' "$out" >&2
      return "$rc"
    fi
    who="another process"; pid=""
    if [[ $out =~ held\ by\ process\ ([0-9]+)\ \(([^\)]*)\) ]]; then
      pid="${BASH_REMATCH[1]}"; who="${BASH_REMATCH[2]}, pid $pid"
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      die "apt is still locked by $who after 10 minutes.
       Another package manager is running. On a freshly booted Ubuntu this is usually the automatic
       updates, which finish on their own — let it finish, then run setup.sh again.
       To see what it is:  ps -o pid,etime,cmd -p ${pid:-<pid>}"
    fi
    [ $(( n % 4 )) -eq 0 ] && echo "  apt is busy ($who) — waiting for it to finish (up to 10 min), then retrying"
    n=$(( n + 1 ))
    sleep 15
  done
}

if [ -z "$MISSING" ]; then
  echo "  all build dependencies are already installed — apt not needed"
else
  echo "  installing:$MISSING"
  apt_wait update -qq || die "apt-get update failed (output above)."
  # shellcheck disable=SC2086  # MISSING is a word list on purpose
  apt_wait install -y -qq $MISSING || die "apt-get install failed (output above)."
fi
[ -f /usr/include/netinet/sctp.h ] || die "netinet/sctp.h is missing — libsctp-dev failed to install"
echo "  cmake $(cmake --version | awk 'NR==1{print $3}') / swig $(swig -version | awk '/SWIG Version/{print $3}')"

# ---------- 2. Source ----------
say "FlexRIC source → $ROOT/flexric"
mkdir -p "$ROOT"
[ -d "$ROOT/flexric/.git" ] || git clone --depth 1 "$REPO" "$ROOT/flexric"
# The patch replaces whole files, so it silently goes stale if upstream changes them. Say which commit
# this is. (The mirror's HEAD has not moved since 2023-12-07; if it ever does, you will see it here.)
VERIFIED_SHA="1f04cc558ebc8da9de6a620762bc02f5db4ecb4a"
HAVE_SHA=$(git -C "$ROOT/flexric" rev-parse HEAD 2>/dev/null || echo unknown)
if [ "$HAVE_SHA" = "$VERIFIED_SHA" ]; then
  echo "  FlexRIC commit ${HAVE_SHA:0:12} — the one this kit is verified on"
else
  echo "  ! FlexRIC commit ${HAVE_SHA:0:12} is not the one this kit is verified on (${VERIFIED_SHA:0:12})."
  echo "    The build continues. If smoke test check 2 fails, upstream has changed the patched files:"
  echo "    rm -rf $ROOT/flexric && git clone $REPO $ROOT/flexric && git -C $ROOT/flexric checkout $VERIFIED_SHA"
  echo "    and run setup.sh again. Report the commit you used with your results."
fi

# ---------- 3. Patch ----------
say "Applying the stateful plant patch"
# For why this is needed, see the header of patch/ee49904_plant.h and BRIEF.md §1.
bash "$PATCHDIR/apply_patch.sh" "$ROOT/flexric"

# ---------- 4. Build ----------
# Build without an executable stack. Why this is needed:
#
#   Since glibc 2.41, making the stack executable at dlopen time is **refused**
#   (Ubuntu 26.04 etc.). FlexRIC uses GCC nested functions, whose default implementation
#   builds trampoline code **on the stack**, so the libraries demand an RWE stack.
#   What passed silently on 24.04 (glibc 2.39) blows up on 26.04 in three layers:
#     · directly linked libe42_xapp_shared.so → ImportError: cannot enable executable stack
#     · the 8 dlopen-ed service models        → load_plugin_ag: Assertion 'handle != NULL' (SIGABRT)
#     · at run time                           → SIGSEGV, PC is a stack address (0x7ffe…)
#   Clearing only the marker with patchelf just moves the symptom one layer down. The right fix is
#   to put trampolines **on the heap** (-ftrampoline-impl=heap, GCC 14+) and force a non-exec stack in the linker.
#   — measured 2026-08-26 on x86_64 WSL2 / Ubuntu 26.04.
#
# GCC 13 (Ubuntu 24.04) does not have this option, so we **ask the compiler** before using it.
# **The two flags go together.** With only `-Wl,-z,noexecstack` the linker clears the exec-stack marker,
# but trampolines still land on the stack, so the run dies with a **SIGSEGV whose PC is a stack address** —
# exactly the same wrong answer as clearing the marker with patchelf (README §11). So the linker flag is
# turned on only when trampolines can move to the heap; otherwise **both stay off and upstream defaults are kept.**
say "Checking the compiler (avoiding an executable stack)"
EE_TRAMPOLINE=""; EE_LDFLAGS=""; EE_RWE_STRICT=0
GLIBC_VER=$(ldd --version 2>/dev/null | awk 'NR==1{print $NF}')
if printf 'int main(void){return 0;}\n' | cc -ftrampoline-impl=heap -x c - -o /dev/null 2>/dev/null; then
  EE_TRAMPOLINE="-ftrampoline-impl=heap"
  EE_LDFLAGS="-Wl,-z,noexecstack"
  EE_RWE_STRICT=1
  echo "  -ftrampoline-impl=heap is available — trampolines go on the heap and the stack is linked non-exec"
else
  echo "  This compiler does not support -ftrampoline-impl=heap (older than GCC 14; here: $(cc --version 2>/dev/null | awk 'NR==1'))."
  # With this compiler we have to rely on the upstream default (executable stack). Whether glibc allows
  # that is the fork in the road — from 2.41 on it is refused at dlopen time, so this combination cannot work.
  if awk -v v="${GLIBC_VER:-0}" 'BEGIN{split(v,a,"."); exit !(a[1]>2 || (a[1]==2 && a[2]>=41))}'; then
    die "FlexRIC will not run with this combination.
       compiler: $(cc --version 2>/dev/null | awk 'NR==1')  (older than GCC 14 — cannot put trampolines on the heap)
       glibc:    $GLIBC_VER                       (2.41 or newer — refuses an executable stack at dlopen time)
       Change one of the two:
         · Install GCC 14 or newer:  sudo apt install gcc-14 g++-14 && export CC=gcc-14 CXX=g++-14
         · Use Ubuntu 24.04, the verified combination (the distribution recommended in the BRIEF)"
  fi
  echo "    → The linker flag is left out too and upstream defaults are used. glibc $GLIBC_VER allows an"
  echo "      executable stack, so this works. (With the linker flag alone, trampolines stay on the stack"
  echo "      while only the marker is cleared, giving a run-time SIGSEGV — so the two are enabled together.)"
fi

# CMakeCache remembers earlier flags. If a tree rebuilt by hand or configured by an old setup is left
# as is, cmake falls back to the cached configuration — so when the flags change, build/ is thrown away.
BD="$ROOT/flexric/build"
FLAGMARK="$BD/.ee49904-build-flags"
WANT="trampoline=${EE_TRAMPOLINE:-none} ldflags=$EE_LDFLAGS"
if [ -f "$BD/CMakeCache.txt" ] && [ "$(cat "$FLAGMARK" 2>/dev/null)" != "$WANT" ]; then
  echo "  The previous build was configured with different flags; removing build/ and building from scratch"
  rm -rf "$BD"
fi

say "Building (incl. Python xApp SDK) — this takes a few minutes"
mkdir -p "$BD"
t0=$(date +%s)
( cd "$BD" \
  && cmake -DXAPP_MULTILANGUAGE=ON \
           -DCMAKE_C_FLAGS="$EE_TRAMPOLINE" \
           -DCMAKE_CXX_FLAGS="$EE_TRAMPOLINE" \
           -DCMAKE_SHARED_LINKER_FLAGS="$EE_LDFLAGS" \
           -DCMAKE_EXE_LINKER_FLAGS="$EE_LDFLAGS" .. > "$ROOT/cmake.log" 2>&1 \
  && make -j"$(nproc)" > "$ROOT/make.log" 2>&1 ) \
  || die "Build failed. Last 20 lines of the log:
$(tail -20 "$ROOT/make.log" 2>/dev/null)"
printf '%s\n' "$WANT" > "$FLAGMARK"
echo "  build took $(( $(date +%s) - t0 )) s"

# Verify in the build outputs that the flags really took effect. If we do not look here, the symptom moves to
# run time, where it shows three faces (ImportError, assert, SIGSEGV) and hides that there is a single cause.
check_rwe(){
  if ! command -v readelf >/dev/null 2>&1; then
    echo "  ! readelf not found; skipping the RWE check (sudo apt install binutils)"
    return 0
  fi
  local bad="" f n=0
  for f in "$@"; do
    [ -f "$f" ] || continue
    n=$((n+1))
    if readelf -lW "$f" 2>/dev/null | grep -q 'GNU_STACK.*RWE'; then
      bad="$bad
       $f"
    fi
  done
  if [ -n "$bad" ]; then
    # If RWE remains in a build that moved trampolines to the heap, the flags did not take effect → abort.
    # In any other build (GCC 13 etc.) RWE is **normal** — glibc allows it, and 2.41 or newer was
    # already filtered out above. Dying here would break a machine that currently works fine.
    if [ "${EE_RWE_STRICT:-0}" = "1" ]; then
      die "Some build outputs still have an executable stack (RWE):$bad

       The build was set to put trampolines on the heap, yet RWE remains — the flags were not applied,
       or the upstream build overrode them with its own linker options.
       On glibc 2.41 or newer, dlopen of these libraries is refused and
       the Python xApp dies with ImportError / assert / SIGSEGV.
       Diagnose:    readelf -lW <file> | grep GNU_STACK
       Workaround:  reinstall on Ubuntu 24.04, the verified combination"
    fi
    echo "  · RWE remains — this is **normal** with this compiler"
    echo "    (Trampolines are placed on the stack, and glibc $GLIBC_VER allows that.)"
    return 0
  fi
  echo "  OK  no RWE — all $n checked files have a non-exec GNU_STACK"
}
say "Checking for leftover executable stack (RWE) — build tree"
check_rwe $(find "$BD" -type f -name '*.so*' 2>/dev/null) \
          "$BD/examples/ric/nearRT-RIC" "$BD/examples/emulator/agent/emu_agent_gnb"

B="$BD"
for x in examples/ric/nearRT-RIC examples/emulator/agent/emu_agent_gnb; do
  [ -x "$B/$x" ] || die "Missing build output: $x"
done
SDK="$B/src/xApp/swig"
[ -f "$SDK/xapp_sdk.py" ] || die "The Python xApp SDK was not generated (check SWIG)"

# FlexRIC looks up the service model shared libraries and the config file **at run time** in
#   /usr/local/lib/flexric/   ·   /usr/local/etc/flexric/flexric.conf
# If they are not there, every process dies immediately with
#   Error finding path /usr/local/lib/flexric/
# (the upstream assert message itself says "Did you forget to: sudo make install ?").
#
# The C executables can change the paths with -p / -c, but **the Python xApp cannot** —
# init() in the SWIG wrapper hard-codes argc=1, argv=NULL and takes no arguments at all
# (src/xApp/swig/swig_wrapper.cpp). So install is the only way.
say "Installing (needs sudo) — required for the Python xApp to find the service models"
( cd "$BD" && $SUDO make install > "$ROOT/install.log" 2>&1 ) \
  || die "make install failed. Log: $ROOT/install.log"
NSM=$(ls /usr/local/lib/flexric/*.so 2>/dev/null | wc -l)
[ "$NSM" -ge 5 ] || die "No service models in /usr/local/lib/flexric/ (install failed)"
[ -f /usr/local/etc/flexric/flexric.conf ] || die "/usr/local/etc/flexric/flexric.conf is missing"
echo "  $NSM service models + flexric.conf installed"

# The installed service models are the very files that get dlopen-ed — check once more here.
say "Checking for leftover executable stack (RWE) — installed files"
check_rwe /usr/local/lib/flexric/*.so

cat > "$ROOT/env.sh" <<EOF
# Use it with source:  source $ROOT/env.sh
export RIC_ROOT="$ROOT"
export RIC_BUILD="$B"
export XAPP_SDK_PATH="$SDK"
# So that an xApp you write yourself can 'import xapp_sdk'. (The starter xApp adds this path on its own.)
case ":\${PYTHONPATH:-}:" in *":$SDK:"*) ;; *) export PYTHONPATH="$SDK\${PYTHONPATH:+:\$PYTHONPATH}" ;; esac
EOF

cat <<EOF

======================================================================
 Installation complete.
   RIC_ROOT       $ROOT
   XAPP_SDK_PATH  $SDK

 Do this first, every time:
   source $ROOT/env.sh

 Next step:
   bash smoke_test.sh

 Manual run (installed, so no extra arguments needed):
   \$RIC_BUILD/examples/ric/nearRT-RIC
   \$RIC_BUILD/examples/emulator/agent/emu_agent_gnb
======================================================================
EOF
