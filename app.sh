#!/usr/bin/env bash
# app.sh — MASTER script for the demo app (GUI preview). One command:
# checks + installs missing dependencies, starts backend (:8000) and the GUI
# dev server (:5173), and prints the URL.
#
# Usage:
#   ./app.sh                  # bring the app up (chat uses your LOCAL Ollama)
#   ./app.sh --gpu [model]    # chat runs on the rented vast.ai GPU instead:
#                             # provisions the instance (default model
#                             # llama3.1:8b — pass another to pull it too),
#                             # opens the tunnel, then starts the app.
#                             # Model downloads run detached on the instance
#                             # with live progress here; disconnects can't
#                             # kill them.
#   ./shutdown.sh             # stop everything (backend, GUI, any lab)
#
# Deliberately does NOT deploy a lab: you pick the scenario in the GUI's
# dropdown when you want it. A lab deploy is the RAM-heavy step (13 containers
# — it once nearly froze this laptop), so keep ~6 GB free before deploying,
# and know that ./shutdown.sh is the emergency stop.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

GPU=0
GPU_MODEL="llama3.1:8b"   # the GUI's default chat model (app/main.py)
for arg in "$@"; do
  case "$arg" in
    --gpu) GPU=1 ;;
    --*) echo "usage: $0 [--gpu [model]]"; exit 2 ;;
    *) GPU_MODEL="$arg" ;;
  esac
done

# ── 1. Dependencies (backend, images) ────────────────────────────────────
echo "=== [1/3] dependencies (scripts/setup.sh) ==="
scripts/setup.sh

# GUI deps are not part of setup.sh (experiments are headless) — handle here.
if [[ ! -d gui/node_modules ]]; then
  echo "installing GUI deps (gui/node_modules)..."
  ( cd gui && { [[ -f package-lock.json ]] && npm ci --silent || npm install --silent; } )
fi

# ── 1b. GPU mode: provision instance + tunnel (same flow as experiment.sh) ─
if [[ $GPU -eq 1 ]]; then
  echo "=== [1b/3] GPU: provision instance + tunnel (model: $GPU_MODEL) ==="
  scripts/vast-provision-gpu.sh "$GPU_MODEL"

  # Local port 11434 must be free for the tunnel. If the local Ollama service
  # holds it, stop it (asks for your sudo password — that's expected).
  if ss -ltn 'sport = :11434' 2>/dev/null | tail -n +2 | grep -q .; then
    if curl -sf http://localhost:11434/api/tags -o /tmp/app-local-tags.json 2>/dev/null \
       && grep -q "\"$GPU_MODEL\"" /tmp/app-local-tags.json; then
      echo "note: something on :11434 already serves $GPU_MODEL — assuming an existing tunnel, reusing it."
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
    curl -sf http://localhost:11434/api/tags -o /tmp/app-tags.json 2>/dev/null && break
    [[ $i == 30 ]] && { echo "FAIL: tunnel did not come up in 30s — see /tmp/nllm-tunnel.log"; exit 1; }
  done
  grep -q "\"$GPU_MODEL\"" /tmp/app-tags.json \
    || { echo "FAIL: tunnel is up but $GPU_MODEL is not in the remote model list — provisioning failed?"; exit 1; }
  echo "ok: remote GPU reachable through localhost:11434, $GPU_MODEL available"
fi

# ── 2. Backend ───────────────────────────────────────────────────────────
echo "=== [2/3] backend :8000 ==="
if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
  echo "ok: backend already up"
else
  nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 \
      > /tmp/nllm-backend.log 2>&1 &
  for i in $(seq 1 30); do
    sleep 1
    curl -sf http://localhost:8000/health >/dev/null 2>&1 && break
    [[ $i == 30 ]] && { echo "FAIL: backend did not come up in 30s — /tmp/nllm-backend.log"; exit 1; }
  done
  echo "ok: backend started (log /tmp/nllm-backend.log)"
fi

# ── 3. GUI dev server ────────────────────────────────────────────────────
echo "=== [3/3] GUI :5173 ==="
if curl -sf http://localhost:5173 >/dev/null 2>&1; then
  echo "ok: GUI already up"
else
  ( cd gui && nohup npm run dev > /tmp/nllm-frontend.log 2>&1 & )
  for i in $(seq 1 30); do
    sleep 1
    curl -sf http://localhost:5173 >/dev/null 2>&1 && break
    [[ $i == 30 ]] && { echo "FAIL: GUI did not come up in 30s — /tmp/nllm-frontend.log"; exit 1; }
  done
  echo "ok: GUI started (log /tmp/nllm-frontend.log)"
fi

MEM_AVAIL=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
if [[ $GPU -eq 1 ]]; then
  CHAT_LINE="Chat runs on the rented GPU ($GPU_MODEL) through the tunnel.
    If you provisioned a non-default model, pick it in the GUI's model list.
    After the session: kill \$(cat /tmp/nllm-tunnel.pid); sudo systemctl start ollama"
else
  CHAT_LINE="Chat needs Ollama running locally: systemctl status ollama"
fi
cat <<EOF

READY — open http://localhost:5173
  - Pick a scenario in the GUI dropdown to deploy a lab (RAM-heavy: have ~6 GB
    free; currently available: ${MEM_AVAIL} MB).
  - ${CHAT_LINE}
  - Stop everything: ./shutdown.sh
EOF
