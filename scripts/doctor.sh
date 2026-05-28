#!/usr/bin/env bash
# Read-only health check for the networkLLMApp dev environment.
# Exits 0 if everything required to run is present; non-zero otherwise.
#
# Usage:
#   scripts/doctor.sh           # full check
#   scripts/doctor.sh backend   # backend-only checks
#   scripts/doctor.sh frontend  # frontend-only checks

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MODE="${1:-all}"

RED=$'\033[31m'
GREEN=$'\033[32m'
YELLOW=$'\033[33m'
BOLD=$'\033[1m'
RESET=$'\033[0m'

fail_count=0
warn_count=0

ok()   { printf "  ${GREEN}[OK]${RESET}   %s\n" "$1"; }
warn() { printf "  ${YELLOW}[WARN]${RESET} %s\n" "$1"; warn_count=$((warn_count+1)); }
bad()  { printf "  ${RED}[FAIL]${RESET} %s\n" "$1"; fail_count=$((fail_count+1)); }

section() { printf "\n${BOLD}%s${RESET}\n" "$1"; }

want_backend()  { [[ "$MODE" == "all" || "$MODE" == "backend" ]]; }
want_frontend() { [[ "$MODE" == "all" || "$MODE" == "frontend" ]]; }

# ─── System binaries ──────────────────────────────────────────────
if want_backend; then
  section "System binaries"

  if command -v python3 >/dev/null; then
    ok "python3: $(python3 --version 2>&1)"
  else
    bad "python3 not found"
  fi

  if command -v docker >/dev/null; then
    ok "docker: $(docker --version 2>&1 | head -1)"
  else
    bad "docker not found"
  fi

  if docker info >/dev/null 2>&1; then
    ok "docker daemon reachable"
  else
    bad "docker daemon not reachable (is the service running? are you in the 'docker' group?)"
  fi

  if command -v containerlab >/dev/null; then
    ok "containerlab: $(containerlab version 2>&1 | grep -E '^\s*version' | head -1 | tr -s ' ')"
  else
    bad "containerlab not in PATH"
  fi

  # sudo's secure_path typically doesn't include ~/.local/bin, so we must pass
  # the absolute binary path to actually exercise the NOPASSWD rule.
  clab_path=$(command -v containerlab 2>/dev/null || true)
  if [[ -n "$clab_path" ]] && sudo -n "$clab_path" version >/dev/null 2>&1; then
    ok "sudoers NOPASSWD rule for containerlab"
  else
    bad "sudo -n $clab_path fails — /etc/sudoers.d/containerlab missing or path mismatch"
  fi
fi

# ─── Python venv ──────────────────────────────────────────────────
if want_backend; then
  section "Python environment"

  if [[ -d .venv ]]; then
    ok ".venv exists"
  else
    bad ".venv missing — run scripts/install.sh"
  fi

  if [[ -x .venv/bin/python ]]; then
    if .venv/bin/python -c "import fastapi, uvicorn, httpx, pydantic, yaml" 2>/dev/null; then
      ok "backend Python deps importable (fastapi, uvicorn, httpx, pydantic, yaml)"
    else
      bad "backend Python deps missing — run scripts/install.sh"
    fi
  fi
fi

# ─── Frontend ─────────────────────────────────────────────────────
if want_frontend; then
  section "Frontend"

  if command -v node >/dev/null; then
    node_major=$(node -v | sed 's/^v\([0-9]*\).*/\1/')
    if [[ "$node_major" -ge 20 ]]; then
      ok "node: $(node -v)"
    else
      warn "node $(node -v) is older than v20; vite may complain"
    fi
  else
    bad "node not found"
  fi

  if command -v npm >/dev/null; then
    ok "npm: $(npm -v)"
  else
    bad "npm not found"
  fi

  if [[ -d gui/node_modules ]]; then
    ok "gui/node_modules present"
  else
    bad "gui/node_modules missing — run scripts/install.sh"
  fi
fi

# ─── Ollama ───────────────────────────────────────────────────────
if want_backend; then
  section "Ollama"

  if curl -s -o /dev/null -w '%{http_code}' http://localhost:11434/api/tags 2>/dev/null | grep -q '^200$'; then
    ok "Ollama API responding on :11434"
    models=$(curl -s http://localhost:11434/api/tags | grep -oE '"name":"[^"]+"' | sed 's/"name":"\(.*\)"/\1/')
    for m in llama3.1:8b qwen2.5-coder:7b; do
      if grep -qx "$m" <<<"$models"; then
        ok "model present: $m"
      else
        bad "model not pulled: $m (run: ollama pull $m)"
      fi
    done
  else
    bad "Ollama not responding on :11434 (start it: 'ollama serve' or systemctl --user start ollama)"
  fi
fi

# ─── Firewall container image ─────────────────────────────────────
if want_backend; then
  section "Firewall container image"

  if docker image inspect firewalld-fw:latest >/dev/null 2>&1; then
    ok "Docker image 'firewalld-fw:latest' present"
  else
    bad "Docker image 'firewalld-fw:latest' missing — run scripts/install.sh (or 'docker build -t firewalld-fw:latest firewall-image/')"
  fi
fi

# ─── Ports ────────────────────────────────────────────────────────
section "Ports"

port_free() {
  local port=$1
  if command -v ss >/dev/null; then
    ! ss -ltn "sport = :$port" 2>/dev/null | tail -n +2 | grep -q .
  else
    ! lsof -i ":$port" -sTCP:LISTEN >/dev/null 2>&1
  fi
}

if want_backend; then
  if port_free 8000; then ok "port 8000 free (backend)"; else warn "port 8000 already in use"; fi
fi
if want_frontend; then
  if port_free 5173; then ok "port 5173 free (frontend)"; else warn "port 5173 already in use"; fi
fi

# ─── Summary ──────────────────────────────────────────────────────
printf "\n"
if (( fail_count == 0 )); then
  printf "${GREEN}${BOLD}All checks passed${RESET}"
  (( warn_count > 0 )) && printf " (${YELLOW}%d warnings${RESET})" "$warn_count"
  printf "\n"
  exit 0
else
  printf "${RED}${BOLD}%d check(s) failed${RESET}" "$fail_count"
  (( warn_count > 0 )) && printf ", ${YELLOW}%d warnings${RESET}" "$warn_count"
  printf "\n"
  exit 1
fi
