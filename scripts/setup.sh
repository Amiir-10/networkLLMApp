#!/usr/bin/env bash
# setup.sh — idempotent check-then-install for the EXPERIMENT stack (headless).
#
# Works on two host types:
#   * Amir's PC (non-root; sudo prompted only where unavoidable)
#   * a rented vast.ai VM instance (root, fresh Ubuntu — installs everything)
#
# Usage:
#   scripts/setup.sh                                   # deps + images, no Ollama
#   scripts/setup.sh --with-ollama llama3.1:70b ...    # + install Ollama + pull models
#
# What it does, in order (each step: CHECK first, INSTALL only if missing):
#   1. system packages: python3 + venv, git, curl, rsync
#   2. Docker engine (get.docker.com when missing)
#   3. containerlab (official installer when missing) + sudoers rule (non-root only)
#   4. Python venv + deps from requirements.lock.txt (fallback: requirements.txt)
#   5. lab images: firewalld-fw:latest + weblab:latest (builds), alpine:3.20 +
#      postgres:16-alpine (pulls)
#   6. [--with-ollama] Ollama install + model pulls
#
# The GUI (node/vite) is NOT installed — experiments are headless. Use
# scripts/install.sh for the full dev setup on a workstation.
# Verify afterwards with: scripts/healthcheck.sh [--model <tag>] [--smoke]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
step() { printf "\n${BOLD}>>> %s${RESET}\n" "$1"; }
ok()   { printf "  ${GREEN}ok:${RESET} %s\n" "$1"; }
inst() { printf "  ${YELLOW}installing:${RESET} %s\n" "$1"; }

IS_ROOT=0; [[ $EUID -eq 0 ]] && IS_ROOT=1
SUDO=""; [[ $IS_ROOT -eq 0 ]] && SUDO="sudo"

WITH_OLLAMA=0
MODELS=()
if [[ "${1:-}" == "--with-ollama" ]]; then
  WITH_OLLAMA=1; shift
  MODELS=("$@")
  [[ ${#MODELS[@]} -eq 0 ]] && MODELS=(llama3.1:8b)
fi

# ─── 1. System packages ───────────────────────────────────────────
step "System packages (python3+venv, git, curl, rsync)"
missing_pkgs=()
command -v python3 >/dev/null || missing_pkgs+=(python3)
python3 -c 'import venv' 2>/dev/null || missing_pkgs+=(python3-venv)
command -v git   >/dev/null || missing_pkgs+=(git)
command -v curl  >/dev/null || missing_pkgs+=(curl)
command -v rsync >/dev/null || missing_pkgs+=(rsync)
if [[ ${#missing_pkgs[@]} -eq 0 ]]; then
  ok "all present ($(python3 --version 2>&1))"
else
  command -v apt-get >/dev/null || { echo "FAIL: missing ${missing_pkgs[*]} and no apt-get — install manually."; exit 1; }
  inst "${missing_pkgs[*]}"
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq "${missing_pkgs[@]}"
fi

# ─── 2. Docker ────────────────────────────────────────────────────
step "Docker engine"
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  ok "$(docker --version)"
elif command -v docker >/dev/null; then
  echo "  docker installed but daemon unreachable — trying to start it"
  $SUDO systemctl start docker 2>/dev/null || $SUDO service docker start 2>/dev/null || true
  docker info >/dev/null 2>&1 || { echo "FAIL: docker daemon still unreachable (group membership? 'newgrp docker' or relogin)"; exit 1; }
  ok "daemon started"
else
  inst "docker via get.docker.com"
  curl -fsSL https://get.docker.com | $SUDO sh
  if [[ $IS_ROOT -eq 0 ]]; then
    $SUDO usermod -aG docker "$USER"
    echo "  NOTE: added $USER to the docker group — log out/in (or 'newgrp docker') before re-running."
  fi
  docker info >/dev/null 2>&1 || { echo "Re-run setup.sh after the group change takes effect."; exit 1; }
fi

# ─── 3. containerlab ─────────────────────────────────────────────
step "containerlab"
CLAB="${CONTAINERLAB_BIN:-$(command -v containerlab || echo "$HOME/.local/bin/containerlab")}"
if [[ -x "$CLAB" ]]; then
  ok "$("$CLAB" version 2>/dev/null | grep -E '^\s*version' | head -1 | tr -s ' ' || echo "$CLAB")"
else
  inst "containerlab (official installer)"
  if [[ $IS_ROOT -eq 1 ]]; then
    bash -c "$(curl -sL https://get.containerlab.dev)"
    CLAB="$(command -v containerlab)"
  else
    mkdir -p "$HOME/.local/bin"
    bash -c "$(curl -sL https://get.containerlab.dev)" -- -b "$HOME/.local/bin"
    CLAB="$HOME/.local/bin/containerlab"
  fi
  [[ -x "$CLAB" ]] || { echo "FAIL: containerlab install did not produce a binary"; exit 1; }
fi
# Passwordless sudo rule — only relevant for non-root hosts (the backend calls
# 'sudo -n containerlab'; as root it skips sudo entirely).
if [[ $IS_ROOT -eq 0 ]]; then
  if sudo -n "$CLAB" version >/dev/null 2>&1; then
    ok "sudoers NOPASSWD rule works for $CLAB"
  else
    inst "/etc/sudoers.d/containerlab (will prompt for password)"
    echo "$USER ALL=(root) NOPASSWD: $CLAB" | $SUDO tee /etc/sudoers.d/containerlab >/dev/null
    $SUDO chmod 440 /etc/sudoers.d/containerlab
    sudo -n "$CLAB" version >/dev/null 2>&1 || { echo "FAIL: sudoers rule still not effective"; exit 1; }
  fi
fi

# ─── 4. Python venv + deps ───────────────────────────────────────
step "Python venv + deps"
if [[ ! -d .venv ]]; then
  inst ".venv"
  python3 -m venv .venv
fi
.venv/bin/pip install --quiet --upgrade pip
if [[ -f requirements.lock.txt ]]; then
  .venv/bin/pip install --quiet -r requirements.lock.txt
  ok "deps installed from requirements.lock.txt (frozen)"
else
  echo "  WARN: requirements.lock.txt missing — falling back to requirements.txt (unpinned transitives)"
  .venv/bin/pip install --quiet -r requirements.txt
fi

# ─── 5. Lab images ───────────────────────────────────────────────
step "Lab docker images"
for spec in "firewalld-fw:latest firewall-image" "weblab:latest weblab-image"; do
  read -r tag dir <<<"$spec"
  if docker image inspect "$tag" >/dev/null 2>&1; then
    ok "$tag present"
  else
    inst "building $tag from $dir/"
    docker build -t "$tag" "$dir/"
  fi
done
for img in alpine:3.20 postgres:16-alpine; do
  if docker image inspect "$img" >/dev/null 2>&1; then
    ok "$img present"
  else
    inst "pulling $img"
    docker pull -q "$img"
  fi
done

# ─── 6. Ollama (optional) ────────────────────────────────────────
if [[ $WITH_OLLAMA -eq 1 ]]; then
  step "Ollama + models: ${MODELS[*]}"
  if ! command -v ollama >/dev/null; then
    inst "ollama via ollama.com/install.sh"
    curl -fsSL https://ollama.com/install.sh | sh
  fi
  if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "  starting ollama serve (detached, log: /tmp/ollama-serve.log)"
    ( $SUDO systemctl start ollama 2>/dev/null ) || { nohup ollama serve > /tmp/ollama-serve.log 2>&1 & }
    for i in $(seq 1 15); do
      sleep 1
      curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && break
      [[ $i == 15 ]] && { echo "FAIL: ollama did not come up in 15s"; exit 1; }
    done
  fi
  # `|| true`: with zero models pulled, grep exits 1 and set -e would abort here.
  present=$(curl -sf http://localhost:11434/api/tags | grep -oE '"name":"[^"]+"' | sed 's/"name":"\(.*\)"/\1/' || true)
  for m in "${MODELS[@]}"; do
    if grep -qx "$m" <<<"$present"; then
      ok "$m already pulled"
    else
      inst "pulling $m (large models take a while — 70b is ~43 GB)"
      ollama pull "$m"
    fi
  done
fi

step "Setup complete"
echo "  Verify: scripts/healthcheck.sh${MODELS:+ --model ${MODELS[0]}} [--smoke]"
