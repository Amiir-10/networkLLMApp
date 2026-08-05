#!/usr/bin/env bash
# experiment.sh — MASTER script for experiment runs. One command does it all:
# checks + installs missing dependencies, (optionally) provisions the rented
# GPU and opens the tunnel, then brings up backend+lab and runs the spec.
#
# Usage:
#   ./experiment.sh <spec> [reps]                # local CPU run
#   ./experiment.sh --gpu <spec> [reps]          # run against the rented vast.ai GPU
#   ./experiment.sh --check <spec>               # environment health check only
#   ./experiment.sh --gpu --check <spec>         # + provision GPU and tunnel, no run
#
# What --gpu adds, in order:
#   1. scripts/vast-provision-gpu.sh <spec's model>  (installs Ollama + pulls the
#      model ON THE INSTANCE — nothing manual)
#   2. frees local port 11434 (stops the local Ollama service — asks for sudo)
#   3. starts the auto-reconnect tunnel in the background (scripts/vast-tunnel.sh,
#      log /tmp/nllm-tunnel.log) and waits until the remote model answers
# Then both modes hand over to ./run-experiment.sh, which does: RAM-guard
# preflight + resource watchdog + backend + lab deploy + the actual run.
#
# After a --gpu session: the tunnel keeps running for follow-up runs.
#   Stop it:            kill $(cat /tmp/nllm-tunnel.pid)
#   Restore local LLM:  sudo systemctl start ollama
#   Stop everything:    ./shutdown.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

GPU=0
PASSTHRU=()
for arg in "$@"; do
  case "$arg" in
    --gpu) GPU=1 ;;
    *) PASSTHRU+=("$arg") ;;
  esac
done
# Find the spec among the remaining args (the non-flag argument).
SPEC=""
for a in "${PASSTHRU[@]:-}"; do
  [[ "$a" == --* ]] && continue
  [[ "$a" =~ ^[0-9]+$ ]] && continue
  SPEC="$a"; break
done
[[ -n "$SPEC" && -f "$SPEC" ]] || { echo "usage: $0 [--gpu] [--check] <spec.yaml> [reps]  (spec not found: '${SPEC:-none}')"; exit 2; }

# ── 1. Dependencies: check-then-install (idempotent) ─────────────────────
echo "=== [1/3] dependencies (scripts/setup.sh) ==="
scripts/setup.sh

# ── 2. GPU path: provision instance + tunnel ─────────────────────────────
if [[ $GPU -eq 1 ]]; then
  echo "=== [2/3] GPU: provision instance + tunnel ==="
  MODEL="$(.venv/bin/python - "$SPEC" <<'EOF'
import sys, yaml
print(yaml.safe_load(open(sys.argv[1])).get("model", "llama3.1:8b"))
EOF
)"
  echo "spec model: $MODEL"
  scripts/vast-provision-gpu.sh "$MODEL"

  # Local port 11434 must be free for the tunnel. If the local Ollama service
  # holds it, stop it (asks for your sudo password — that's expected).
  if ss -ltn 'sport = :11434' 2>/dev/null | tail -n +2 | grep -q .; then
    if curl -sf http://localhost:11434/api/tags -o /tmp/exp-local-tags.json 2>/dev/null \
       && grep -q "\"$MODEL\"" /tmp/exp-local-tags.json; then
      echo "note: something on :11434 already serves $MODEL — assuming an existing tunnel, reusing it."
    else
      echo "local Ollama holds :11434 — stopping it for the tunnel (sudo)..."
      sudo systemctl stop ollama
      sleep 1
      ss -ltn 'sport = :11434' 2>/dev/null | tail -n +2 | grep -q . \
        && { echo "FAIL: :11434 still occupied after stopping ollama — check: ss -ltnp | grep 11434"; exit 1; }
    fi
  fi

  # Start the auto-reconnect tunnel detached (skip if one already serves us).
  if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    nohup scripts/vast-tunnel.sh > /tmp/nllm-tunnel.log 2>&1 &
    echo $! > /tmp/nllm-tunnel.pid
    echo "tunnel starting (pid $(cat /tmp/nllm-tunnel.pid), log /tmp/nllm-tunnel.log)..."
  fi
  for i in $(seq 1 30); do
    sleep 1
    curl -sf http://localhost:11434/api/tags -o /tmp/exp-tags.json 2>/dev/null && break
    [[ $i == 30 ]] && { echo "FAIL: tunnel did not come up in 30s — see /tmp/nllm-tunnel.log"; exit 1; }
  done
  grep -q "\"$MODEL\"" /tmp/exp-tags.json \
    || { echo "FAIL: tunnel is up but $MODEL is not in the remote model list — provisioning failed?"; exit 1; }
  echo "ok: remote GPU reachable through localhost:11434, $MODEL available"
else
  echo "=== [2/3] local mode (no --gpu) ==="
fi

# ── 3. Hand over to the runner wrapper (guarded) ─────────────────────────
echo "=== [3/3] run (RAM guard + backend + lab + experiment) ==="
exec ./run-experiment.sh "${PASSTHRU[@]}"
