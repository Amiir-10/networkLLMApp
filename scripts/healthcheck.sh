#!/usr/bin/env bash
# healthcheck.sh — verify the EXPERIMENT stack is ready to run (headless).
# Verify-only except: --smoke may start the backend and deploys/tears down a lab.
#
# Usage:
#   scripts/healthcheck.sh                        # static checks
#   scripts/healthcheck.sh --model llama3.1:70b   # + check Ollama serves that tag
#   scripts/healthcheck.sh --smoke                # + deploy central-hub, probe, tear down
#
# Exit 0 = ready. Non-zero = at least one FAIL line above tells you what to fix.
# Complements scripts/doctor.sh (dev-oriented, includes GUI); this one is for
# experiment hosts (PC or vast.ai instance) and exercises the real lab path.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

RED=$'\033[31m'; GREEN=$'\033[32m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
fails=0
ok()  { printf "  ${GREEN}[OK]${RESET}   %s\n" "$1"; }
bad() { printf "  ${RED}[FAIL]${RESET} %s\n" "$1"; fails=$((fails+1)); }
section() { printf "\n${BOLD}%s${RESET}\n" "$1"; }

MODEL=""
SMOKE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="${2:?--model needs a tag}"; shift 2 ;;
    --smoke) SMOKE=1; shift ;;
    *) echo "unknown arg: $1"; exit 2 ;;
  esac
done

BACKEND="${BACKEND:-http://localhost:8000}"
OLLAMA="${OLLAMA_URL_BASE:-http://localhost:11434}"

# ─── Python env ──────────────────────────────────────────────────
section "Python environment"
if [[ -x .venv/bin/python ]]; then
  if .venv/bin/python -c "import fastapi, uvicorn, httpx, pydantic, yaml, matplotlib, prompt_replay" 2>/dev/null; then
    ok "venv + all experiment deps importable (incl. matplotlib, prompt_replay)"
  else
    bad "venv deps missing/broken — run scripts/setup.sh"
  fi
else
  bad ".venv missing — run scripts/setup.sh"
fi

# ─── Docker + containerlab ───────────────────────────────────────
section "Docker + containerlab"
if docker info >/dev/null 2>&1; then
  ok "docker daemon reachable ($(docker --version | sed 's/,.*//'))"
else
  bad "docker daemon unreachable"
fi
CLAB="${CONTAINERLAB_BIN:-$(command -v containerlab || echo "$HOME/.local/bin/containerlab")}"
if [[ -x "$CLAB" ]]; then
  ok "containerlab at $CLAB"
  if [[ $EUID -eq 0 ]]; then
    ok "running as root — no sudo rule needed"
  elif sudo -n "$CLAB" version >/dev/null 2>&1; then
    ok "sudo -n rule works"
  else
    bad "sudo -n $CLAB fails — run scripts/setup.sh (sudoers rule)"
  fi
else
  bad "containerlab not found (CONTAINERLAB_BIN=$CLAB)"
fi
for img in firewalld-fw:latest weblab:latest alpine:3.20 postgres:16-alpine; do
  if docker image inspect "$img" >/dev/null 2>&1; then
    ok "image $img"
  else
    bad "image $img missing — run scripts/setup.sh"
  fi
done

# ─── Ollama ──────────────────────────────────────────────────────
section "Ollama ($OLLAMA)"
if curl -sf "$OLLAMA/api/tags" -o /tmp/hc-ollama-tags.json 2>/dev/null; then
  ok "API responding"
  if [[ -n "$MODEL" ]]; then
    if grep -q "\"$MODEL\"" /tmp/hc-ollama-tags.json; then
      ok "model $MODEL present"
    else
      bad "model $MODEL NOT present — ollama pull $MODEL (on the host serving 11434)"
    fi
  fi
else
  bad "Ollama not responding on $OLLAMA (local daemon down, or tunnel not up)"
fi

# ─── Smoke: real lab round-trip ──────────────────────────────────
if [[ $SMOKE -eq 1 && $fails -eq 0 ]]; then
  section "Smoke test (central-hub deploy → probe → destroy)"
  # Resource guard (2026-08-05 near-crash): refuse without RAM headroom, and
  # arm the emergency-teardown watchdog for the duration of the smoke test.
  MIN_FREE_MB="${MIN_FREE_MB:-4000}"
  AVAIL_MB=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
  if (( AVAIL_MB < MIN_FREE_MB )); then
    bad "only ${AVAIL_MB}MB RAM available — smoke deploy needs ${MIN_FREE_MB}MB (close apps, or MIN_FREE_MB=... to override)"
  else
    ok "${AVAIL_MB}MB RAM available"
    nohup "$REPO_ROOT/scripts/resource-guard.sh" $$ > /dev/null 2>&1 &
    ok "resource guard armed (log: /tmp/nllm-resource-guard.log)"
  fi
fi
if [[ $SMOKE -eq 1 && $fails -eq 0 ]]; then
  STARTED_BACKEND=0
  if ! curl -sf "$BACKEND/health" -o /tmp/hc-health.json; then
    echo "  backend down — starting detached (log: /tmp/nllm-backend.log)"
    nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 \
        > /tmp/nllm-backend.log 2>&1 &
    STARTED_BACKEND=1
    up=0
    for i in $(seq 1 30); do
      sleep 1
      curl -sf "$BACKEND/health" -o /tmp/hc-health.json && { up=1; break; }
    done
    [[ $up -eq 1 ]] || bad "backend did not come up in 30s — /tmp/nllm-backend.log"
  fi
  if [[ $fails -eq 0 ]]; then
    if curl -sf -X POST "$BACKEND/lab/reset/central-hub" --max-time 600 -o /dev/null; then
      ok "lab central-hub deployed (via /lab/reset)"
      if docker exec clab-central-hub-pc1 ping -c 1 -W 2 pc2 >/dev/null 2>&1; then
        ok "probe pc1 → pc2 succeeded (docker exec ping)"
      else
        bad "probe pc1 → pc2 failed — lab up but data path broken"
      fi
      if curl -sf -X POST "$BACKEND/lab/stop/central-hub" --max-time 300 -o /dev/null; then
        ok "lab torn down"
      else
        bad "lab teardown failed — clean up manually (docker ps | grep clab-)"
      fi
    else
      bad "lab deploy failed — /tmp/nllm-backend.log"
    fi
    if [[ $STARTED_BACKEND -eq 1 ]]; then
      pkill -f "[u]vicorn app.main:app" 2>/dev/null
      ok "stopped the backend this check started"
    fi
  fi
elif [[ $SMOKE -eq 1 ]]; then
  section "Smoke test"
  bad "skipped — fix the failures above first"
fi

# ─── Summary ─────────────────────────────────────────────────────
printf "\n"
if (( fails == 0 )); then
  printf "${GREEN}${BOLD}READY${RESET} — all checks passed\n"
  exit 0
else
  printf "${RED}${BOLD}NOT READY${RESET} — %d check(s) failed\n" "$fails"
  exit 1
fi
