#!/usr/bin/env bash
# EE49904 — LLM NetOps + verifier starter kit setup
#
#   bash setup.sh
#   source ~/ee49904-netops/env.sh      # every new shell
#   bash smoke_test.sh
#
# What this installs: a Python virtualenv with pybatfish, and a Batfish service in a container.
# What it does NOT install: the language model.
#
# This runs on the department's shared server. Model and verifier both live there, so there is
# no tunnel and no Docker of yours: Batfish runs under udocker, which needs no root and no
# daemon. No GPU of your own is needed.
#
# (The script also works where a Docker daemon is available — that is how the course verifies
# the kit on its own machines — but the server is the path the course supports.)

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="${EE_ROOT:-$HOME/ee49904-netops}"
VENV="$ROOT/venv"
BF_CONTAINER="${EE_BF_CONTAINER:-ee49904-batfish}"
BF_IMAGE="batfish/allinone"
BF_HOST="${EE_BF_HOST:-localhost}"
BF_PORT="${EE_BF_PORT:-9996}"
# One Batfish can serve several people, but only if they do not share a network name —
# init_snapshot(overwrite=True) would otherwise let one team's snapshot replace another's.
BF_NETWORK="${EE_BF_NETWORK:-ee49904-${USER:-$(id -un 2>/dev/null || echo anon)}}"

say(){ printf "\n\033[1m%s\033[0m\n" "$1"; }
warn(){ printf "  \033[33m!!\033[0m %s\n" "$1"; }
die(){ printf "\n\033[31m%s\033[0m\n" "$1"; exit 1; }

mkdir -p "$ROOT"

# ---------------------------------------------------------------- environment notes
say "0. environment"
UNAME="$(uname -s)"
echo "  os        $UNAME $(uname -m)"
echo "  python3   $(python3 --version 2>&1)"
if grep -qi microsoft /proc/version 2>/dev/null; then
  echo "  WSL2      yes"
  case "$HERE" in
    /mnt/c/*|/mnt/d/*)
      warn "you are working under /mnt/c — file access there is 5-10x slower."
      warn "copy this kit into your Linux home (~/) and work from there." ;;
  esac
fi

# ---------------------------------------------------------------- python
say "1. Python environment"
# `python3 -m venv` fails on a machine without the python3-venv package — which is the state of
# the shared servers, where you cannot apt-get it either. The failure is worth catching loudly:
# if the venv is missing, a later `pip install` quietly falls back to a --user install and the
# script looks like it succeeded while nothing is isolated. So: fall back to virtualenv, then
# *prove* that the packages landed inside $VENV before going on.
if [ ! -f "$VENV/bin/activate" ]; then
  rm -rf "$VENV"
  if python3 -m venv "$VENV" >/dev/null 2>"$ROOT/.venv.err" && [ -f "$VENV/bin/activate" ]; then
    echo "  created $VENV (python3 -m venv)"
  else
    rm -rf "$VENV"
    warn "python3 -m venv is unavailable here (python3-venv is not installed)."
    warn "falling back to virtualenv — no root needed."
    python3 -m pip install --quiet --user virtualenv >/dev/null 2>&1 \
      || die "could not install virtualenv. With root you would instead run:
    sudo apt-get install -y python3-venv python3-pip"
    python3 -m virtualenv "$VENV" >/dev/null 2>&1 && [ -f "$VENV/bin/activate" ] \
      || die "virtualenv could not create $VENV. See $ROOT/.venv.err"
    echo "  created $VENV (virtualenv)"
  fi
fi
# shellcheck disable=SC1091
. "$VENV/bin/activate"
case "$(command -v python3)" in
  "$VENV"/*) ;;
  *) die "the virtualenv did not activate — python3 is still $(command -v python3).
    Remove $VENV and run this script again." ;;
esac
python3 -m pip install --quiet --upgrade pip >/dev/null 2>&1
if python3 -m pip install --quiet pybatfish; then
  PB_AT="$(python3 -c 'import os,pybatfish;print(os.path.realpath(pybatfish.__file__))' 2>/dev/null)"
  case "$PB_AT" in
    "$(cd "$VENV" && pwd)"/*) echo "  pybatfish $(python3 -c 'import pybatfish; print(getattr(pybatfish,"__version__","?"))')" ;;
    *) die "pybatfish was installed outside the virtualenv:
    $PB_AT
    That is the silent --user fallback. Remove $VENV, then run this script again." ;;
  esac
else
  die "pip install pybatfish failed. Check your network and try again."
fi

# ---------------------------------------------------------------- batfish
say "2. Batfish service"

bf_answers(){   # $1 host, $2 port — any HTTP answer means the service is up
  python3 - "$1" "$2" <<'PY' >/dev/null 2>&1
import sys, urllib.request, urllib.error
try:
    urllib.request.urlopen("http://%s:%s/v2/question_templates" % (sys.argv[1], sys.argv[2]), timeout=3)
except urllib.error.HTTPError:
    pass
PY
}

if bf_answers "$BF_HOST" "$BF_PORT"; then
  echo "  something already answers on $BF_HOST:$BF_PORT — reusing it"
  echo "  your snapshots stay yours: they are filed under the network name $BF_NETWORK"
elif ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  # No usable Docker. udocker runs the same image with no root and no daemon, which is the
  # situation on the shared servers: Docker is absent and sudo is not available.
  if command -v docker >/dev/null 2>&1; then
    warn "the docker daemon is not reachable — falling back to udocker."
  else
    echo "  docker not found — using udocker (no root, no daemon)"
  fi

  if ! command -v udocker >/dev/null 2>&1; then
    python3 -m pip install --quiet udocker >/dev/null 2>&1 \
      || die "pip install udocker failed. Check your network and try again."
  fi
  # udocker pulls over HTTP through either the curl binary or pycurl, and the shared servers ship
  # wget only — so pycurl has to stand in. This check sits outside the install above on purpose:
  # an account that already had udocker needs pycurl just as much, and the pull fails with
  # "need curl or pycurl" if it is missing. Ask the interpreter, not the shell: pycurl is a module.
  if ! command -v curl >/dev/null 2>&1 && ! python3 -c "import pycurl" >/dev/null 2>&1; then
    echo "  no curl and no pycurl — installing pycurl (udocker pulls with it)"
    python3 -m pip install --quiet pycurl >/dev/null 2>&1
    python3 -c "import pycurl" >/dev/null 2>&1 \
      || warn "neither curl nor pycurl is available — the pull below will probably fail."
  fi
  command -v udocker >/dev/null 2>&1 \
    || die "udocker installed but is not on PATH. Re-source $ROOT/env.sh and try again."

  if udocker inspect "$BF_CONTAINER" >/dev/null 2>&1; then
    echo "  container $BF_CONTAINER already created"
  else
    echo "  pulling $BF_IMAGE (about 1.4 GB, once)…"
    udocker pull "$BF_IMAGE" >/dev/null 2>"$ROOT/.udocker.err" \
      || die "udocker pull failed. Last lines of $ROOT/.udocker.err:
$(tail -4 "$ROOT/.udocker.err" 2>/dev/null | sed 's/^/    /')"
    udocker create --name="$BF_CONTAINER" "$BF_IMAGE" >/dev/null 2>>"$ROOT/.udocker.err" \
      || die "udocker create failed. See $ROOT/.udocker.err"
  fi

  # Two flags this image will not start without:
  #   --execmode=R2  the default (P1) rewrites syscalls with PRoot and this image does not
  #                  survive it; R2 runs it under runc. R2 shares the host's network, which
  #                  is also why there is no -p mapping here and none is possible.
  #   --workdir=/    the image's entrypoint uses relative paths and must start from /.
  udocker setup --execmode=R2 "$BF_CONTAINER" >/dev/null 2>>"$ROOT/.udocker.err" \
    || warn "could not set execmode R2 — the container will probably fail to start."
  nohup udocker run --workdir=/ "$BF_CONTAINER" > "$ROOT/batfish.log" 2>&1 &
  echo $! > "$ROOT/batfish.pid"
  echo "  started $BF_CONTAINER (pid $(cat "$ROOT/batfish.pid" 2>/dev/null)) — log: $ROOT/batfish.log"
  printf "  waiting for the service"
  READY=0
  for _ in $(seq 1 60); do
    printf "."
    if bf_answers "$BF_HOST" "$BF_PORT"; then READY=1; break; fi
    sleep 2
  done
  echo
  if [ "$READY" -eq 1 ]; then
    echo "  Batfish answered on $BF_PORT"
  else
    warn "Batfish did not answer. Last lines of $ROOT/batfish.log:"
    tail -12 "$ROOT/batfish.log" 2>/dev/null | sed 's/^/      /'
    warn "on a shared server, port $BF_PORT may already belong to another team — udocker"
    warn "cannot remap it. Ask for the shared endpoint and set EE_BF_HOST instead."
  fi
else
  if docker ps --format '{{.Names}}' | grep -qx "$BF_CONTAINER"; then
    echo "  container $BF_CONTAINER already running"
  else
    docker rm -f "$BF_CONTAINER" >/dev/null 2>&1

    # Anonymous pulls need no credentials, but Docker consults its credential helper anyway.
    # On macOS that helper talks to the login keychain, and if the keychain cannot prompt —
    # a non-interactive shell, an ssh session, a script run in the background — the pull dies
    # with "error getting credentials … the current session does not allow user interaction".
    # (Measured on two Macs.) A throwaway DOCKER_CONFIG with no credsStore avoids the helper
    # entirely.
    export DOCKER_CONFIG="${DOCKER_CONFIG:-$ROOT/.docker}"
    mkdir -p "$DOCKER_CONFIG"
    # Set credsStore/credHelpers **explicitly to empty values**. With just `{}` the Docker CLI may
    # fall back to the platform default helper — in our tests `{}` alone did not prevent it.
    printf '{"auths":{},"credsStore":"","credHelpers":{}}\n' > "$DOCKER_CONFIG/config.json"

    # batfish/allinone is published for linux/amd64 only. On arm64 hosts Docker will warn and
    # then run it under emulation — which works on macOS (Docker Desktop ships Rosetta) and
    # frequently does not on arm64 Linux without binfmt/qemu installed. Ask explicitly so the
    # failure is about emulation rather than about a silent platform mismatch.
    PLATFORM=""
    case "$(uname -m)" in aarch64|arm64) PLATFORM="--platform linux/amd64" ;; esac
    [ -n "$PLATFORM" ] && echo "  host is $(uname -m); the image is amd64-only → requesting emulation"

    if docker image inspect "$BF_IMAGE" >/dev/null 2>&1; then
      echo "  image already present — skipping the pull"
    else
      echo "  pulling $BF_IMAGE (about 1.4 GB, once)…"
      if ! docker pull $PLATFORM "$BF_IMAGE" >/dev/null 2>"$ROOT/.docker-pull.err"; then
        warn "pull failed:"
        sed 's/^/      /' "$ROOT/.docker-pull.err" | head -4
        if grep -q "getting credentials\|keychain" "$ROOT/.docker-pull.err" 2>/dev/null; then
          cat <<EOF
      ↑ This is the macOS keychain, not the network. Docker asks its credential helper for
        credentials even though this image is public, and the helper cannot prompt from a
        non-interactive shell (a script, an ssh session, a background job).
        Fix it once, by hand, in a Terminal window you are looking at:

            docker pull $PLATFORM $BF_IMAGE

        Then re-run this script — it will find the image locally and skip the pull.
EOF
        fi
      fi
    fi
    if docker run -d --name "$BF_CONTAINER" $PLATFORM -p 9996:9996 -p 9997:9997 \
         "$BF_IMAGE" >/dev/null 2>"$ROOT/.docker-run.err"; then
      echo "  started $BF_CONTAINER on ports 9996/9997"
    else
      warn "could not start the container:"
      sed 's/^/      /' "$ROOT/.docker-run.err" | head -4
    fi

    # Wait for the SERVICE, not for the port. Docker publishes the host port as soon as the
    # container is created, so a bare TCP connect succeeds even when the process inside is dead
    # — this check used to pass instantly on a container that never came up. Poll the HTTP API
    # instead, and give up early if the container has already exited.
    printf "  waiting for the service"
    READY=0
    for _ in $(seq 1 45); do
      printf "."
      if [ "$(docker inspect -f '{{.State.Running}}' "$BF_CONTAINER" 2>/dev/null)" != "true" ]; then
        echo; warn "the container exited while starting up. Last lines of its log:"
        docker logs --tail 12 "$BF_CONTAINER" 2>&1 | sed 's/^/      /'
        if [ -n "$PLATFORM" ]; then
          warn "on arm64 this usually means amd64 emulation is unavailable"
          warn "(the log above then reads 'exec format error')."
          if [ "$UNAME" = "Linux" ]; then
            warn "register it, then run this script again:"
            warn "    docker run --privileged --rm tonistiigi/binfmt --install amd64"
            warn "The registration does not survive a reboot. The distribution's qemu-user-static"
            warn "package is not a substitute: on Ubuntu 24.04 its qemu crashes the JVM in this image."
          else
            warn "in Docker Desktop, check that Rosetta (x86_64/amd64 emulation) is switched on."
          fi
        fi
        break
      fi
      if python3 - <<'PY' >/dev/null 2>&1
import urllib.request
# Any HTTP answer means the service is up; 400/404 are fine, a refused connection is not.
try:
    urllib.request.urlopen("http://localhost:9996/v2/question_templates", timeout=3)
except urllib.error.HTTPError:
    pass
PY
      then READY=1; break; fi
      sleep 2
    done
    echo
    if [ "$READY" -eq 1 ]; then
      echo "  Batfish answered on 9996"
    else
      warn "Batfish did not answer. smoke_test.sh check 2 will tell you the same thing."
      warn "  docker logs $BF_CONTAINER   ← start here"
    fi
  fi
fi

# ---------------------------------------------------------------- llm reachability
say "3. the shared model server"
LLM_HOST="${EE_LLM_HOST:-http://localhost:11434}"
if python3 - "$LLM_HOST" <<'PY' >/dev/null 2>&1
import json, sys, urllib.request
urllib.request.urlopen(sys.argv[1].rstrip("/") + "/api/tags", timeout=8).read()
PY
then
  echo "  reachable at $LLM_HOST"
  echo "  (nothing further to do)"
else
  warn "not reachable at $LLM_HOST."
  cat <<EOF

  The model is served on this machine, so this should have answered. It usually means the
  serving process is down — after a reboot it has to be started again. Ask on the KLMS Q&A
  board or your TA rather than starting one yourself.

  Everything that does not need the model still works meanwhile:

      bash smoke_test.sh --no-llm     # the six checks that need no model server
EOF
fi

# ---------------------------------------------------------------- env.sh
say "4. env.sh"
cat > "$ROOT/env.sh" <<EOF
# EE49904 LLM NetOps + verifier — source this in every new shell
. "$VENV/bin/activate"
export EE_KIT="$HERE"
export EE_BF_HOST="\${EE_BF_HOST:-$BF_HOST}"
export EE_BF_PORT="\${EE_BF_PORT:-$BF_PORT}"
export EE_BF_NETWORK="\${EE_BF_NETWORK:-$BF_NETWORK}"
export EE_LLM_HOST="\${EE_LLM_HOST:-$LLM_HOST}"
export EE_LLM_MODEL="\${EE_LLM_MODEL:-gpt-oss}"
EOF
echo "  wrote $ROOT/env.sh"

cat <<EOF

Next:

    source $ROOT/env.sh
    bash smoke_test.sh            # 9 checks
    bash smoke_test.sh --no-llm   # the 6 that need no model server

EOF
