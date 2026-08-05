#!/usr/bin/env bash
# vast-provision-gpu.sh — Path A: prepare the rented GPU instance FROM YOUR PC.
# Installs Ollama on the instance (if missing), makes sure it is serving, and
# pulls the requested model(s). Idempotent — safe to re-run.
#
# Usage:
#   scripts/vast-provision-gpu.sh <model> [model ...]
#   e.g. scripts/vast-provision-gpu.sh qwen2.5-coder:32b
#
# Reads scripts/vast-instance.env (SSH_HOST, SSH_PORT, SSH_USER).
# Called automatically by ./experiment.sh --gpu (pulls the spec's model).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/scripts/vast-instance.env"
[[ -f "$ENV_FILE" ]] || { echo "FAIL: $ENV_FILE missing — copy vast-instance.env.example and fill it in."; exit 1; }
# shellcheck source=/dev/null
source "$ENV_FILE"
: "${SSH_HOST:?SSH_HOST unset in vast-instance.env}"
: "${SSH_PORT:?SSH_PORT unset}"
: "${SSH_USER:=root}"

[[ $# -ge 1 ]] || { echo "usage: $0 <model> [model ...]"; exit 2; }
MODELS=("$@")

SSH="ssh -p $SSH_PORT -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new $SSH_USER@$SSH_HOST"

echo ">>> provisioning $SSH_USER@$SSH_HOST:$SSH_PORT with models: ${MODELS[*]}"
$SSH bash -s -- "${MODELS[@]}" <<'REMOTE'
set -euo pipefail
echo "--- on instance: $(hostname) ---"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo "warn: nvidia-smi not available"

if ! command -v ollama >/dev/null; then
  echo "installing ollama..."
  curl -fsSL https://ollama.com/install.sh | sh
else
  echo "ok: ollama installed ($(ollama --version 2>/dev/null || true))"
fi

# Make sure the daemon is serving (systemd on VMs; plain background process on
# docker-template instances without systemd).
if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "starting ollama serve..."
  (systemctl start ollama 2>/dev/null) || (nohup ollama serve > /tmp/ollama-serve.log 2>&1 &)
  for i in $(seq 1 20); do
    sleep 1
    curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && break
    [ "$i" = 20 ] && { echo "FAIL: ollama did not come up in 20s"; exit 1; }
  done
fi
echo "ok: ollama serving on :11434"

present=$(curl -sf http://localhost:11434/api/tags | grep -oE '"name":"[^"]+"' | sed 's/"name":"\(.*\)"/\1/')
for m in "$@"; do
  if printf '%s\n' "$present" | grep -qx "$m"; then
    echo "ok: $m already pulled"
  else
    echo "pulling $m (large models take a while; 70b is ~43 GB)..."
    ollama pull "$m"
  fi
done
echo "--- instance ready ---"
REMOTE

echo ">>> provisioning done"
