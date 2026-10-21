#!/usr/bin/env bash
# Autoware Backpropagator — one-command install (Linux / macOS).
#
#   ./install.sh                  install for Offline mode and report what this machine still needs
#   ./install.sh --online         also build the C++ live probe and require everything Online mode needs
#   ./install.sh --dev            also install the test and lint tools
#   ./install.sh --python PATH    use this Python (default: the newest python3.x >= 3.10 found)
#
# Exit code 0 = ready. Anything else = a problem, printed with the command that fixes it.
# It fixes what it can on its own: a missing venv module, an old or broken .venv, a stale probe image.

# `sh install.sh` runs dash on Debian/Ubuntu, which lacks what follows: run again with bash.
if [ -z "${BASH_VERSION:-}" ]; then
  if command -v bash >/dev/null 2>&1; then exec bash "$0" "$@"; fi
  echo "install.sh needs bash"; exit 2
fi
set -uo pipefail
cd "$(dirname "$0")" || { echo "cannot enter $(dirname "$0")"; exit 2; }

ONLINE=0
DEV=0
PY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --online) ONLINE=1 ;;
    --dev) DEV=1 ;;
    --python) shift; PY="${1:-}" ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see ./install.sh --help)"; exit 2 ;;
  esac
  shift
done

if [ -t 1 ]; then BOLD=$'\e[1m'; RED=$'\e[31m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; DIM=$'\e[2m'; OFF=$'\e[0m'
else BOLD=; RED=; GREEN=; YELLOW=; DIM=; OFF=; fi
step() { printf '%s▸ %s%s\n' "$BOLD" "$1" "$OFF"; }
ok()   { printf '  %s✓%s %s\n' "$GREEN" "$OFF" "$1"; }
note() { printf '  %s!%s %s\n' "$YELLOW" "$OFF" "$1"; }
fail() { printf '  %s✗ %s%s\n    fix: %s\n' "$RED" "$1" "$OFF" "$2"; exit 1; }

# The install command for a package on this system (apt, dnf, pacman, zypper, brew).
pkg() {  # pkg <apt names> <dnf names> <pacman names> <brew names>
  if command -v apt-get >/dev/null 2>&1; then echo "sudo apt install $1"
  elif command -v dnf >/dev/null 2>&1; then echo "sudo dnf install $2"
  elif command -v pacman >/dev/null 2>&1; then echo "sudo pacman -S $3"
  elif command -v zypper >/dev/null 2>&1; then echo "sudo zypper install $2"
  elif command -v brew >/dev/null 2>&1; then echo "brew install $4"
  else echo "install $2 with your package manager"; fi
}

if [ "$(id -u)" = 0 ] && [ -n "${SUDO_USER:-}" ]; then
  note "running with sudo: .venv and the project files will belong to root — run ./install.sh without sudo"
fi

step "Python 3.10 or newer"
new_enough() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; }
if [ -z "$PY" ]; then
  for cand in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python; do
    if command -v "$cand" >/dev/null 2>&1 && new_enough "$cand"; then PY="$(command -v "$cand")"; break; fi
  done
fi
if [ -z "$PY" ]; then
  OLD="$(command -v python3 || command -v python || true)"
  [ -n "$OLD" ] && fail "$("$OLD" -V 2>&1) is too old and no python3.10+ was found" "$(pkg python3 python3 python python@3.12)"
  fail "python3 not found" "$(pkg "python3 python3-venv" python3 python python@3.12)"
fi
new_enough "$PY" || fail "$("$PY" -V 2>&1) is too old" "install Python 3.10 or newer, or pass --python"
PYVER="$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
ok "$("$PY" -V 2>&1) ($PY)"

step "Virtual environment (.venv)"
# An old or broken .venv (made by a Python that is gone, or copied from another machine): start again.
if [ -e .venv ] && ! { [ -x .venv/bin/python ] && new_enough .venv/bin/python && .venv/bin/python -c 'import pip' >/dev/null 2>&1; }; then
  BROKEN=".venv.broken-$(date +%Y%m%d-%H%M%S)"
  mv .venv "$BROKEN" && note "the old .venv did not work; moved it to $BROKEN and made a new one"
fi
if [ ! -x .venv/bin/python ]; then
  if "$PY" -c 'import venv, ensurepip' >/dev/null 2>&1; then
    "$PY" -m venv .venv || fail "could not create .venv" "check write permission in $(pwd)"
  elif command -v uv >/dev/null 2>&1; then  # no venv module (Debian/Ubuntu without python3-venv): uv has its own
    note "the Python venv module is missing; using uv instead"
    uv venv --quiet --seed --python "$PY" .venv || fail "uv could not create .venv" "$(pkg python3-venv python3 python python)"
  else  # still no sudo needed: a venv without pip, then pip from its official bootstrap script
    note "the Python venv module is missing ($(pkg "python${PYVER}-venv" python3 python python)); setting up pip without it"
    rm -rf .venv
    "$PY" -m venv --without-pip .venv >/dev/null 2>&1 \
      || fail "could not create .venv without the venv module" "$(pkg "python${PYVER}-venv" python3 python python)"
    if ! GETPIP_ERR="$(.venv/bin/python -c 'import urllib.request; urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", ".venv/get-pip.py")' 2>&1)"; then
      case "$GETPIP_ERR" in
        *CERTIFICATE*) fail "this system has no CA certificates, so HTTPS downloads fail" \
                         "$(pkg "ca-certificates python${PYVER}-venv" ca-certificates ca-certificates ca-certificates)" ;;
        *) fail "could not download pip's bootstrap script ($(printf '%s' "$GETPIP_ERR" | tail -1 | cut -c1-80))" \
                "$(pkg "python${PYVER}-venv" python3 python python)   (or check the internet connection / proxy)" ;;
      esac
    fi
    .venv/bin/python .venv/get-pip.py --quiet || fail "pip bootstrap failed" "$(pkg "python${PYVER}-venv" python3 python python)"
  fi
fi
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --quiet --upgrade pip || fail "pip upgrade failed" "check the internet connection / proxy (pip honours HTTPS_PROXY)"
EXTRAS=""; [ "$DEV" = 1 ] && EXTRAS="[dev]"
python -m pip install --quiet -e ".${EXTRAS}" || fail "pip install failed" "check the internet connection / proxy, then run ./install.sh again"
ok "$(adviewer --version)"

if [ "$ONLINE" = 1 ]; then
  step "Online mode: Docker and the live probe"
  command -v docker >/dev/null 2>&1 || fail "docker not found" "install Docker: https://docs.docker.com/engine/install/  (macOS / Windows: Docker Desktop)"
  if ! DOCKER_ERR="$(docker info 2>&1 >/dev/null)"; then
    case "$DOCKER_ERR" in
      *ermission*denied*)
        if id -nG "${USER:-$(id -un)}" 2>/dev/null | grep -qw docker || getent group docker 2>/dev/null | grep -qw "${USER:-$(id -un)}"; then
          fail "you are in the docker group, but this session started before" "log out and in again (or run: newgrp docker), then ./install.sh --online"
        fi
        fail "no permission to use Docker" "sudo usermod -aG docker \$USER, then log out and in, then ./install.sh --online" ;;
      *) if [ "$(uname)" = Darwin ]; then fail "Docker is not running" "start Docker Desktop, then ./install.sh --online"; fi
         fail "the Docker daemon is not running" "sudo systemctl start docker   (and: sudo systemctl enable docker)" ;;
    esac
  fi
  ROOT="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)"
  FREE_KB="$(df -Pk "${ROOT:-/}" 2>/dev/null | awk 'NR==2 {print $4}')"
  if [ -n "${FREE_KB:-}" ] && [ "$FREE_KB" -lt 4000000 ]; then
    fail "only $((FREE_KB / 1000000)) GB free for Docker (${ROOT:-/}); the probe image needs about 2 GB to build" \
      "docker system prune   (removes stopped containers and unused images), then ./install.sh --online"
  fi
  printf '  %sbuilding adviewer-probe:dev (ROS 2 Jazzy + CycloneDDS + the probe, 1–3 min)…%s\n' "$DIM" "$OFF"
  adviewer probe build >.venv/probe-build.log 2>&1 \
    || fail "probe image build failed" "see $(pwd)/.venv/probe-build.log"
  ok "probe image adviewer-probe:dev"
  step "Environment check (online)"
  adviewer doctor --online
  CODE=$?
else
  step "Environment check (Offline mode needs only Python — the rest matters for running Autoware)"
  adviewer doctor
  CODE=0
fi

echo
if [ "$CODE" = 0 ]; then
  printf '%sReady.%s Next:\n' "$GREEN$BOLD" "$OFF"
else
  printf '%sInstalled, but Online mode is not ready yet — fix the ✗ lines above and run ./install.sh --online again.%s\n' "$RED$BOLD" "$OFF"
  printf 'Offline mode already works:\n'
fi
cat <<'EOF'
  source .venv/bin/activate
  cd /path/to/your/project && adviewer init     # adviewer.yaml + VS Code tasks
  adviewer extract && adviewer fetch-source     # Autoware's files (from the Docker image + git)
  adviewer up                                   # http://127.0.0.1:8765 (VS Code: Simple Browser)
  # no Docker, or no room for the image (5 GB download)? `adviewer init --from-source`, then skip extract
EOF
exit "$CODE"
