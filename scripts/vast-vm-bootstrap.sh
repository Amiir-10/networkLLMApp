#!/usr/bin/env bash
# vast-vm-bootstrap.sh — Path B: run ON a fresh vast.ai VM instance (root,
# Ubuntu) to stand up the ENTIRE experiment stack there.
#
# Usage (on the VM, after `git clone` of the repo):
#   cd networkLLMApp && scripts/vast-vm-bootstrap.sh [model ...]
#     default models: llama3.1:70b qwen2.5-coder:32b
#
# What it runs, in order:
#   1. scripts/setup.sh --with-ollama <models>   (docker, containerlab, venv,
#      lab images, ollama + model pulls — idempotent)
#   2. scripts/healthcheck.sh --model <first model> --smoke
#   3. prints the run command
#
# Prereqs handled elsewhere: SSH key added BEFORE instance creation (a VM
# without your key is permanently inaccessible), disk >= 100 GB at creation
# (cannot be resized), repo cloned. See docs/RUNBOOK-VAST-VM.md.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ $EUID -ne 0 ]]; then
  echo "NOTE: expected to run as root on a vast.ai VM. Continuing anyway (sudo rules apply)."
fi

MODELS=("$@")
[[ ${#MODELS[@]} -eq 0 ]] && MODELS=(llama3.1:70b qwen2.5-coder:32b)

echo ">>> Bootstrap: full stack + models: ${MODELS[*]}"
scripts/setup.sh --with-ollama "${MODELS[@]}"

echo ">>> Health check (includes lab smoke test)"
scripts/healthcheck.sh --model "${MODELS[0]}" --smoke

cat <<EOF

>>> READY. Run experiments INSIDE tmux (SSH drops must not kill runs):
      ./run-experiment.sh experiments/s1-baseline-llama31-70b.yaml 1   # calibration rep FIRST
      ./run-experiment.sh experiments/s1-baseline-llama31-70b.yaml 5

    On your PC meanwhile:
      scripts/install-sync-timer.sh          # 10-min result pulls
      scripts/sync-results.sh --final        # before destroying the instance
EOF
