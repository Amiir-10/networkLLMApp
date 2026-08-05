#!/usr/bin/env bash
# vast-tunnel.sh — Path A: forward the rented GPU's Ollama to localhost:11434.
# Keeps the tunnel alive with auto-reconnect; leave it running in its own
# terminal (or tmux pane) for the whole experiment round.
#
# Usage: scripts/vast-tunnel.sh
# Reads scripts/vast-instance.env (SSH_HOST, SSH_PORT, SSH_USER).
#
# After it connects, EVERYTHING on this PC that talks to localhost:11434
# (backend, run-experiment.sh) transparently uses the remote GPU — no code or
# env changes needed.
#
# GOTCHA: a local Ollama daemon owns 11434 and the tunnel then can't bind.
# This script detects that and tells you to stop it first.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/scripts/vast-instance.env"
[[ -f "$ENV_FILE" ]] || { echo "FAIL: $ENV_FILE missing — copy vast-instance.env.example and fill it in."; exit 1; }
# shellcheck source=/dev/null
source "$ENV_FILE"
: "${SSH_HOST:?SSH_HOST unset in vast-instance.env}"
: "${SSH_PORT:?SSH_PORT unset}"
: "${SSH_USER:=root}"

LOCAL_PORT="${LOCAL_PORT:-11434}"

if ss -ltn "sport = :$LOCAL_PORT" 2>/dev/null | tail -n +2 | grep -q .; then
  echo "FAIL: localhost:$LOCAL_PORT is already in use (a local Ollama daemon?)."
  echo "Stop it first:  sudo systemctl stop ollama    (re-enable later: sudo systemctl start ollama)"
  exit 1
fi

echo "Tunnel: localhost:$LOCAL_PORT -> $SSH_USER@$SSH_HOST:$SSH_PORT -> instance 11434"
echo "Verify in another terminal:  curl -s localhost:$LOCAL_PORT/api/tags"
echo "Ctrl-C to stop."

while :; do
  ssh -p "$SSH_PORT" "$SSH_USER@$SSH_HOST" \
      -N -L "$LOCAL_PORT:localhost:11434" \
      -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
      -o ExitOnForwardFailure=yes -o ConnectTimeout=10 \
      -o StrictHostKeyChecking=accept-new \
      && break   # clean exit (Ctrl-C) → stop
  echo "[$(date '+%T')] tunnel dropped — reconnecting in 5s (Ctrl-C to abort)"
  sleep 5
done
