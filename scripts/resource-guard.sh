#!/usr/bin/env bash
# resource-guard.sh — emergency watchdog against memory-pressure freezes.
#
# WHY THIS EXISTS: on 2026-08-05 a full lab bring-up (13 containers + backend
# + Ollama model in RAM) drove the laptop into swap-thrash (load avg 14) and
# nearly crashed the whole system. This guard is the safety net: if available
# RAM drops below the critical threshold, it tears the whole stack down
# (./shutdown.sh) rather than letting the machine freeze.
#
# Usage:
#   scripts/resource-guard.sh [PID]     # watch until PID exits (or forever)
#
# run-experiment.sh and healthcheck.sh --smoke start this automatically.
# Env: CRIT_MB (default 1200) — teardown trigger; INTERVAL (default 5s).
# Log: /tmp/nllm-resource-guard.log
#
# Manual emergency stop at any time: ./shutdown.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRIT_MB="${CRIT_MB:-1200}"
INTERVAL="${INTERVAL:-5}"
WATCH_PID="${1:-}"
LOG="/tmp/nllm-resource-guard.log"

echo "[$(date '+%F %T')] guard up: teardown if MemAvailable < ${CRIT_MB}MB${WATCH_PID:+, watching PID $WATCH_PID}" >> "$LOG"

while :; do
    if [[ -n "$WATCH_PID" ]] && ! kill -0 "$WATCH_PID" 2>/dev/null; then
        echo "[$(date '+%F %T')] watched PID $WATCH_PID gone — guard exiting" >> "$LOG"
        exit 0
    fi
    avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
    if (( avail < CRIT_MB )); then
        echo "[$(date '+%F %T')] EMERGENCY: MemAvailable ${avail}MB < ${CRIT_MB}MB — tearing the stack down" >> "$LOG"
        "$REPO_ROOT/shutdown.sh" >> "$LOG" 2>&1
        echo "[$(date '+%F %T')] emergency teardown finished (see above for what survived)" >> "$LOG"
        exit 1
    fi
    sleep "$INTERVAL"
done
