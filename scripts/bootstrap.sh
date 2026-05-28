#!/usr/bin/env bash
# System-level bring-up for a fresh Ubuntu/Debian machine.
# Run once per machine. Idempotent — safe to re-run.
# Needs sudo. Will prompt for your password when required.
#
# What this does:
#   1. apt install python3.12 + venv, curl, git, build-essential
#   2. Install Node 20 via nodesource if missing or too old
#   3. Install Docker engine if missing, add current user to 'docker' group
#   4. Install Ollama if missing
#   5. Install containerlab to ~/.local/bin if missing
#   6. Write /etc/sudoers.d/containerlab so the backend can run it NOPASSWD
#
# Does NOT pull Ollama models or install Python/npm/firewall-image — that's scripts/install.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BOLD=$'\033[1m'
YELLOW=$'\033[33m'
RESET=$'\033[0m'
step() { printf "\n${BOLD}>>> %s${RESET}\n" "$1"; }
note() { printf "${YELLOW}    note:${RESET} %s\n" "$1"; }

# ─── Sanity: Debian-family only ───────────────────────────────────
if ! command -v apt-get >/dev/null; then
  echo "bootstrap.sh only supports Debian/Ubuntu (apt-get not found)."
  echo "On other distros, install python3.12 + venv, node 20+, docker, ollama, containerlab manually,"
  echo "then run scripts/install.sh."
  exit 1
fi

if [[ $EUID -eq 0 ]]; then
  echo "Don't run bootstrap.sh as root. Run as your normal user; sudo will be invoked as needed."
  exit 1
fi

# ─── 1. apt packages ──────────────────────────────────────────────
step "apt packages (python3.12, venv, curl, git, build-essential, ca-certificates)"

sudo apt-get update -qq
sudo apt-get install -y -qq \
  python3 python3-venv python3-pip \
  curl git ca-certificates gnupg lsb-release \
  build-essential

# ─── 2. Node.js 20+ via nodesource if missing or too old ──────────
step "Node.js 20+"

need_node=0
if ! command -v node >/dev/null; then
  need_node=1
else
  major=$(node -v | sed 's/^v\([0-9]*\).*/\1/')
  if [[ "$major" -lt 20 ]]; then
    need_node=1
    note "found node $(node -v), upgrading to 20.x"
  else
    echo "    node $(node -v) already installed"
  fi
fi

if [[ "$need_node" -eq 1 ]]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y -qq nodejs
  echo "    installed node $(node -v)"
fi

# ─── 3. Docker engine ─────────────────────────────────────────────
step "Docker engine"

if command -v docker >/dev/null; then
  echo "    docker already installed: $(docker --version)"
else
  curl -fsSL https://download.docker.com/linux/$(. /etc/os-release && echo "$ID")/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/$(. /etc/os-release && echo "$ID") \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  echo "    installed docker"
fi

if id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
  echo "    user $USER already in 'docker' group"
else
  sudo usermod -aG docker "$USER"
  note "added $USER to 'docker' group — log out and back in (or run 'newgrp docker') before docker commands work without sudo."
fi

# ─── 4. Ollama ────────────────────────────────────────────────────
step "Ollama"

if command -v ollama >/dev/null; then
  echo "    ollama already installed: $(ollama --version 2>&1 | head -1)"
else
  curl -fsSL https://ollama.com/install.sh | sh
  echo "    installed ollama"
fi

# ─── 5. containerlab to ~/.local/bin ──────────────────────────────
step "containerlab"

mkdir -p "$HOME/.local/bin"
CLAB_BIN="$HOME/.local/bin/containerlab"

if [[ -x "$CLAB_BIN" ]]; then
  echo "    containerlab already installed: $($CLAB_BIN version 2>&1 | grep -E '^\s*version' | head -1 | tr -s ' ')"
else
  # The official installer drops to /usr/bin by default. We want a per-user
  # install at ~/.local/bin so the sudoers rule is scoped to one binary path.
  arch=$(dpkg --print-architecture)
  case "$arch" in
    amd64) arch_tag=amd64 ;;
    arm64) arch_tag=arm64 ;;
    *) echo "    unsupported arch: $arch"; exit 1 ;;
  esac
  curl -fsSL -o /tmp/clab.tar.gz \
    "https://github.com/srl-labs/containerlab/releases/download/v0.75.0/containerlab_0.75.0_linux_${arch_tag}.tar.gz"
  tar -xzf /tmp/clab.tar.gz -C /tmp containerlab
  mv /tmp/containerlab "$CLAB_BIN"
  chmod +x "$CLAB_BIN"
  rm -f /tmp/clab.tar.gz
  echo "    installed containerlab v0.75.0 to $CLAB_BIN"
fi

if ! echo "$PATH" | tr ':' '\n' | grep -qx "$HOME/.local/bin"; then
  note "~/.local/bin is not in your PATH. Add this to ~/.bashrc:  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# ─── 6. sudoers rule for containerlab ─────────────────────────────
step "sudoers NOPASSWD rule for containerlab"

SUDOERS_FILE=/etc/sudoers.d/containerlab
SUDOERS_LINE="$USER ALL=(root) NOPASSWD: $CLAB_BIN"

if sudo test -f "$SUDOERS_FILE" && sudo grep -qF "$SUDOERS_LINE" "$SUDOERS_FILE"; then
  echo "    $SUDOERS_FILE already has the correct rule"
else
  # Write to a temp file, validate with visudo -cf BEFORE installing it, then move.
  # An invalid sudoers file can lock you out of sudo — always validate first.
  tmpfile=$(mktemp)
  echo "$SUDOERS_LINE" > "$tmpfile"
  if sudo visudo -cf "$tmpfile" >/dev/null; then
    sudo install -m 0440 -o root -g root "$tmpfile" "$SUDOERS_FILE"
    echo "    installed $SUDOERS_FILE"
  else
    echo "    visudo validation FAILED for the generated rule — refusing to install."
    rm -f "$tmpfile"
    exit 1
  fi
  rm -f "$tmpfile"
fi

# ─── Done ─────────────────────────────────────────────────────────
step "Bootstrap complete"
echo "    Next: 'scripts/install.sh' to install Python deps, npm deps, build the firewall image, and pull Ollama models."
echo "    If 'docker' was newly installed, log out and back in (or 'newgrp docker') before continuing."
