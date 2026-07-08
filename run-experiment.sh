#!/usr/bin/env bash
# run-experiment.sh — one-command wrapper for the thesis experiment runner.
#
# Usage:
#   ./run-experiment.sh experiments/s1-baseline-llama31.yaml          # run spec.repetitions reps
#   ./run-experiment.sh experiments/s1-baseline-llama31.yaml 5        # run 5 reps (override)
#   ./run-experiment.sh --check experiments/s1-baseline-llama31.yaml  # health checks only, no run
#
# What it does before running:
#   1. Ollama up + the spec's model pulled
#   2. Backend :8000 up (starts it detached via nohup if down — NEVER pipe
#      uvicorn through head/filters; that 500s every request)
#   3. The spec's scenario deployed & firewall connected (deploys via
#      POST /lab/start, or destroy+redeploys via POST /lab/reset on mismatch —
#      containers are never restarted in place: fw dbus crash-loop)
# Then: python -m app.experiments run <spec> [--reps N]
# Reruns APPEND repetitions (rep numbering continues) — that is how the CI narrows.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"
PY="$REPO/.venv/bin/python"
BACKEND="http://localhost:8000"
OLLAMA="http://localhost:11434"

CHECK_ONLY=0
if [[ "${1:-}" == "--check" ]]; then CHECK_ONLY=1; shift; fi
SPEC="${1:?usage: $0 [--check] <spec.yaml> [reps]}"
REPS="${2:-}"
[[ -f "$SPEC" ]] || { echo "FAIL: spec file not found: $SPEC"; exit 1; }
[[ -x "$PY" ]] || { echo "FAIL: venv python not found at $PY"; exit 1; }

# Pull scenario + model out of the spec (single source of truth).
read -r SCENARIO MODEL < <("$PY" - "$SPEC" <<'EOF'
import sys, yaml
s = yaml.safe_load(open(sys.argv[1]))
print(s["scenario"], s.get("model", "llama3.1:8b"))
EOF
)
echo "spec:     $SPEC"
echo "scenario: $SCENARIO"
echo "model:    $MODEL"

# ── 1. Ollama ────────────────────────────────────────────────────────────
if ! curl -sf "$OLLAMA/api/tags" -o /tmp/ollama-tags.json; then
    echo "FAIL: Ollama is not responding on $OLLAMA — start it first (systemctl status ollama / 'ollama serve')."
    exit 1
fi
if ! grep -q "\"$MODEL\"" /tmp/ollama-tags.json; then
    echo "FAIL: model '$MODEL' not found in Ollama. Pull it: ollama pull $MODEL"
    exit 1
fi
echo "ok: Ollama up, $MODEL present"

# ── 2. Backend ───────────────────────────────────────────────────────────
if ! curl -sf "$BACKEND/health" -o /tmp/nllm-health.json; then
    echo "backend down — starting detached (log: /tmp/nllm-backend.log)..."
    nohup "$REPO/.venv/bin/uvicorn" app.main:app --host 0.0.0.0 --port 8000 \
        > /tmp/nllm-backend.log 2>&1 &
    for i in $(seq 1 30); do
        sleep 1
        curl -sf "$BACKEND/health" -o /tmp/nllm-health.json && break
        [[ $i == 30 ]] && { echo "FAIL: backend did not come up in 30s — see /tmp/nllm-backend.log"; exit 1; }
    done
fi
echo "ok: backend up on :8000"

# ── 3. Lab: right scenario deployed + firewall connected ────────────────
lab_state() { "$PY" - <<'EOF'
import json
h = json.load(open("/tmp/nllm-health.json"))
print(("active" if h.get("lab_active") else "inactive"),
      h.get("scenario") or "-", ("fw-ok" if h.get("firewall_connected") else "fw-down"))
EOF
}
read -r ACTIVE CUR_SCENARIO FW < <(lab_state)
if [[ "$ACTIVE" == "inactive" ]]; then
    # /lab/start 409s if containers from a previous backend process are still
    # up (backend restart loses lab state) — fall back to destroy+redeploy.
    echo "no lab active — deploying $SCENARIO (takes a few minutes)..."
    curl -sf -X POST "$BACKEND/lab/start/$SCENARIO" --max-time 600 > /dev/null \
        || { echo "start conflicted (stale containers?) — destroy+redeploy..."
             curl -sf -X POST "$BACKEND/lab/reset/$SCENARIO" --max-time 600 > /dev/null \
                 || { echo "FAIL: lab reset failed — check /tmp/nllm-backend.log"; exit 1; }; }
elif [[ "$CUR_SCENARIO" != "$SCENARIO" || "$FW" != "fw-ok" ]]; then
    echo "lab is '$CUR_SCENARIO' ($FW), need '$SCENARIO' — destroy+redeploy (never restart)..."
    curl -sf -X POST "$BACKEND/lab/reset/$SCENARIO" --max-time 600 > /dev/null \
        || { echo "FAIL: lab reset failed — check /tmp/nllm-backend.log"; exit 1; }
fi
curl -sf "$BACKEND/health" -o /tmp/nllm-health.json
read -r ACTIVE CUR_SCENARIO FW < <(lab_state)
[[ "$ACTIVE" == "active" && "$CUR_SCENARIO" == "$SCENARIO" && "$FW" == "fw-ok" ]] \
    || { echo "FAIL: lab not healthy after deploy: $ACTIVE $CUR_SCENARIO $FW"; exit 1; }
echo "ok: lab '$SCENARIO' active, firewall connected"

if [[ $CHECK_ONLY == 1 ]]; then
    echo "--check: environment healthy, not running. Drop --check to run."
    exit 0
fi

# ── 4. Run ───────────────────────────────────────────────────────────────
echo "starting run $(date '+%H:%M:%S') — S1-class reps take ~6-7 min each on CPU Ollama"
exec "$PY" -m app.experiments run "$SPEC" ${REPS:+--reps "$REPS"}
