#!/usr/bin/env bash
# app.sh — MASTER script for the demo app (GUI preview). One command:
# checks + installs missing dependencies, starts backend (:8000) and the GUI
# dev server (:5173), and prints the URL.
#
# Usage:
#   ./app.sh          # bring the app up
#   ./shutdown.sh     # stop everything (backend, GUI, any lab)
#
# Deliberately does NOT deploy a lab: you pick the scenario in the GUI's
# dropdown when you want it. A lab deploy is the RAM-heavy step (13 containers
# — it once nearly froze this laptop), so keep ~6 GB free before deploying,
# and know that ./shutdown.sh is the emergency stop.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

# ── 1. Dependencies (backend, images) ────────────────────────────────────
echo "=== [1/3] dependencies (scripts/setup.sh) ==="
scripts/setup.sh

# GUI deps are not part of setup.sh (experiments are headless) — handle here.
if [[ ! -d gui/node_modules ]]; then
  echo "installing GUI deps (gui/node_modules)..."
  ( cd gui && { [[ -f package-lock.json ]] && npm ci --silent || npm install --silent; } )
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
cat <<EOF

READY — open http://localhost:5173
  - Pick a scenario in the GUI dropdown to deploy a lab (RAM-heavy: have ~6 GB
    free; currently available: ${MEM_AVAIL} MB).
  - Chat needs Ollama running locally: systemctl status ollama
  - Stop everything: ./shutdown.sh
EOF
