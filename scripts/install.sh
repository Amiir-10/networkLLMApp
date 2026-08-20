#!/usr/bin/env bash
# Project-level setup. No sudo required.
# Assumes: python3.12 (with venv module), node 20+, npm, docker, ollama, containerlab.
# If any of those are missing, run scripts/bootstrap.sh first.
#
# Idempotent — safe to re-run after pulling new code.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BOLD=$'\033[1m'
RESET=$'\033[0m'
step() { printf "\n${BOLD}>>> %s${RESET}\n" "$1"; }

# ─── 1. Python venv ───────────────────────────────────────────────
step "Python venv (.venv) + backend deps"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  echo "    created .venv"
else
  echo "    .venv already exists"
fi

.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
echo "    installed: $(tr '\n' ' ' < requirements.txt)"

# ─── 2. Frontend deps ─────────────────────────────────────────────
step "Frontend deps (gui/node_modules)"

(
  cd gui
  if [[ -f package-lock.json ]]; then
    npm ci --silent
  else
    npm install --silent
  fi
)
echo "    installed npm packages in gui/"

# ─── 3. Firewall container image ──────────────────────────────────
step "Firewall container image (firewalld-fw:latest)"

if docker image inspect firewalld-fw:latest >/dev/null 2>&1; then
  echo "    image firewalld-fw:latest already built — skipping (delete it to force rebuild)"
else
  docker build -t firewalld-fw:latest firewall-image/
fi

# ─── 4. Ollama models ─────────────────────────────────────────────
step "Ollama models"

if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "    Ollama not responding on :11434 — start it ('ollama serve' or 'systemctl --user start ollama') and re-run."
  echo "    Skipping model pulls."
else
  # `|| true`: with zero models pulled, grep exits 1 and set -e would abort here.
  present=$(curl -sf http://localhost:11434/api/tags | grep -oE '"name":"[^"]+"' | sed 's/"name":"\(.*\)"/\1/' || true)
  for m in llama3.1:8b qwen2.5-coder:7b; do
    if grep -qx "$m" <<<"$present"; then
      echo "    $m: already present"
    else
      echo "    pulling $m ..."
      ollama pull "$m"
    fi
  done
fi

# ─── Done ─────────────────────────────────────────────────────────
step "Install complete"
echo "    Run 'scripts/doctor.sh' to verify, then 'scripts/run-backend.sh' and 'scripts/run-frontend.sh' in two terminals."
