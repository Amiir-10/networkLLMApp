#!/usr/bin/env bash
# sync-results.sh — pull experiment results from a vast.ai instance to this PC.
# The supervisor's results-safety requirement: run periodically (see
# scripts/install-sync-timer.sh) so nothing is ever lost with the instance.
#
# Usage:
#   scripts/sync-results.sh            # incremental pull
#   scripts/sync-results.sh --final    # pull + verify remote/local file counts match
#
# Reads scripts/vast-instance.env (SSH_HOST, SSH_PORT, SSH_USER, REMOTE_REPO_DIR).
#
# Results land in data/experiments-remote/<UTC-date>/ — NEVER in data/experiments/.
# Rep numbering is filesystem-derived, so merging two hosts' results under the
# same spec id would collide. Rule: one spec id runs on exactly ONE host, ever.
# Aggregate synced results locally with:
#   .venv/bin/python -m app.experiments aggregate <spec>   (after copying a spec's
#   folder into a scratch data dir — see docs/RUNBOOK-VAST-VM.md)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/scripts/vast-instance.env"
[[ -f "$ENV_FILE" ]] || { echo "FAIL: $ENV_FILE missing — copy vast-instance.env.example and fill it in."; exit 1; }
# shellcheck source=/dev/null
source "$ENV_FILE"
: "${SSH_HOST:?SSH_HOST unset in vast-instance.env}"
: "${SSH_PORT:?SSH_PORT unset}"
: "${SSH_USER:=root}"
: "${REMOTE_REPO_DIR:?REMOTE_REPO_DIR unset}"

FINAL=0
[[ "${1:-}" == "--final" ]] && FINAL=1

DEST="$REPO_ROOT/data/experiments-remote/$(date -u +%F)"
mkdir -p "$DEST"
SSH_CMD="ssh -p $SSH_PORT -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
REMOTE="$SSH_USER@$SSH_HOST"
SRC="$REMOTE:$REMOTE_REPO_DIR/data/experiments/"

echo "[$(date '+%F %T')] syncing $SRC -> $DEST"
# --partial keeps interrupted large files resumable; -z helps on json/text.
rsync -az --partial --timeout=60 -e "$SSH_CMD" "$SRC" "$DEST/" \
  || { echo "FAIL: rsync failed (instance down? env stale?)"; exit 1; }
echo "[$(date '+%F %T')] sync ok"

if [[ $FINAL -eq 1 ]]; then
  echo "--final: verifying file counts"
  remote_count=$($SSH_CMD "$REMOTE" "find '$REMOTE_REPO_DIR/data/experiments' -type f | wc -l")
  local_count=$(find "$DEST" -type f | wc -l)
  echo "  remote: $remote_count files   local: $local_count files"
  if [[ "$remote_count" -eq "$local_count" ]]; then
    echo "  MATCH — safe to destroy the instance."
  else
    echo "  MISMATCH — do NOT destroy the instance yet. Re-run and compare."
    exit 1
  fi
  echo "Synced specs:"
  find "$DEST" -maxdepth 1 -mindepth 1 -type d -printf '  %f\n' 2>/dev/null || true
fi
