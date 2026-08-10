#!/usr/bin/env bash
# vast-provision-gpu.sh — Path A: prepare the rented GPU instance FROM YOUR PC.
# Installs Ollama on the instance (if missing), makes sure it is serving, and
# pulls the requested model(s). Idempotent — safe to re-run.
#
# Model downloads run DETACHED ON THE INSTANCE (nohup), so a dropped SSH
# connection, a closed terminal, or a sleeping laptop cannot kill them.
# Your terminal shows live progress (percent, GB, speed, ETA) the whole time;
# re-running this script simply re-attaches to a download already in flight,
# and interrupted downloads resume where they left off.
#
# Usage:
#   scripts/vast-provision-gpu.sh <model> [model ...]
#   e.g. scripts/vast-provision-gpu.sh qwen2.5-coder:32b
#
# Reads scripts/vast-instance.env (SSH_HOST, SSH_PORT, SSH_USER).
# Called automatically by ./experiment.sh --gpu and ./app.sh --gpu.

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
# Ollama registers bare names as name:latest — normalize so the ready-check
# below matches what /api/tags will actually report.
MODELS=()
for m in "$@"; do
  [[ "$m" == *:* ]] || m="$m:latest"
  MODELS+=("$m")
done

SSH="ssh -p $SSH_PORT -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new $SSH_USER@$SSH_HOST"

echo ">>> provisioning $SSH_USER@$SSH_HOST:$SSH_PORT with models: ${MODELS[*]}"

# ── Phase 1: install + serve + START downloads (detached; returns fast) ──
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
  elif pgrep -f "ollama pull $m" >/dev/null 2>&1; then
    echo "ok: $m download already running — re-attaching to it"
  else
    safe="${m//[^A-Za-z0-9._-]/-}"
    nohup ollama pull "$m" > "/tmp/nllm-pull-$safe.log" 2>&1 &
    echo "started: $m downloading detached on the instance (log /tmp/nllm-pull-$safe.log)"
  fi
done
REMOTE

# ── Phase 2: watch from the laptop until every model is ready ────────────
# One short SSH poll every 5s; killing THIS side never kills the download.
for m in "${MODELS[@]}"; do
  ssh_errs=0
  while true; do
    status=$($SSH bash -s -- "$m" 2>/dev/null <<'EOF' | tail -n 1
m="$1"
safe="${m//[^A-Za-z0-9._-]/-}"
log="/tmp/nllm-pull-$safe.log"
if curl -sf http://localhost:11434/api/tags 2>/dev/null | grep -q "\"name\":\"$m\""; then
  echo READY; exit 0
fi
if ! pgrep -f "ollama pull $m" >/dev/null 2>&1; then
  last=$(tr '\r' '\n' < "$log" 2>/dev/null | sed 's/\x1b\[[0-9;?]*[A-Za-z]//g' | grep -v '^[[:space:]]*$' | tail -n 1)
  echo "DEAD ${last:-no log found}"; exit 0
fi
line=$(tr '\r' '\n' < "$log" 2>/dev/null \
  | sed 's/\x1b\[[0-9;?]*[A-Za-z]//g; s/▕[^▏]*▏//' \
  | grep -E '[0-9]+%' | tail -n 1)
echo "PULL ${line:-download starting...}"
EOF
    ) || status="SSHERR"
    case "$status" in
      READY)
        [[ -t 1 ]] && printf '\n'
        echo "ok: $m ready on the GPU"
        break ;;
      DEAD*)
        [[ -t 1 ]] && printf '\n'
        echo "FAIL: download of $m stopped without finishing. Last log line:"
        echo "  ${status#DEAD }"
        echo "Re-run this script — downloads resume where they left off."
        exit 1 ;;
      PULL*)
        ssh_errs=0
        msg="${status#PULL }"
        if [[ -t 1 ]]; then printf '\r  %s: %-60.60s' "$m" "$msg"; else echo "  $m: $msg"; fi ;;
      *)
        ssh_errs=$((ssh_errs + 1))
        if [[ $ssh_errs -ge 12 ]]; then
          [[ -t 1 ]] && printf '\n'
          echo "FAIL: cannot reach the instance for ~1 minute — check it is still running."
          echo "The download itself is NOT lost: it continues on the instance;"
          echo "re-run this script once the connection is back."
          exit 1
        fi
        if [[ -t 1 ]]; then printf '\r  %s: %-60.60s' "$m" "(connection blip, retrying...)"; fi ;;
    esac
    sleep 5
  done
done

echo ">>> provisioning done"
