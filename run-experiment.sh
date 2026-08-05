#!/usr/bin/env bash
# run-experiment.sh — one-command wrapper for the thesis experiment runner.
#
# Usage:
#   ./run-experiment.sh                                               # interactive: topology -> sequence -> reps
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
# Env-overridable for remote setups (vast.ai tunnel keeps the defaults).
BACKEND="${BACKEND:-http://localhost:8000}"
OLLAMA="${OLLAMA_URL_BASE:-http://localhost:11434}"

CHECK_ONLY=0
if [[ "${1:-}" == "--check" ]]; then CHECK_ONLY=1; shift; fi

# ── Interactive mode: no spec argument → topology → sequence → reps ─────
# Menu data comes from the spec files themselves (experiments/*.yaml): the
# topology list is the distinct `scenario` values, because ground truth in a
# spec is authored against one topology's node names — topology cannot be a
# free runtime override.
pick_interactive() {
    [[ -x "$PY" ]] || { echo "FAIL: venv python not found at $PY"; exit 1; }
    # TSV per spec: path, scenario, model, default reps, step-kind pattern, reps done.
    local table
    table="$("$PY" - <<'EOF'
import glob, json, pathlib, yaml
for path in sorted(glob.glob("experiments/*.yaml")):
    s = yaml.safe_load(open(path))
    kinds = " ".join(step["kind"] for step in s.get("sequence", []))
    done = 0
    for rep in pathlib.Path(f"data/experiments/{s['id']}/reps").glob("rep-*.json"):
        try:
            done += bool(json.loads(rep.read_text()).get("complete"))
        except Exception:
            pass
    print("\t".join([path, s["scenario"], s.get("model", "llama3.1:8b"),
                     str(s.get("repetitions", 3)), kinds, str(done)]))
EOF
)"
    [[ -n "$table" ]] || { echo "FAIL: no spec files found in experiments/"; exit 1; }

    # 1. Topology (skip the question when only one exists).
    local topos topo
    mapfile -t topos < <(cut -f2 <<<"$table" | sort -u)
    if [[ ${#topos[@]} -eq 1 ]]; then
        topo="${topos[0]}"
        echo "topology: $topo (only one with specs)"
    else
        echo "Topologies with experiment specs:"
        local i=1; for t in "${topos[@]}"; do echo "  $i) $t"; i=$((i+1)); done
        local pick
        while :; do
            read -rp "Pick topology [1-${#topos[@]}]: " pick
            [[ "$pick" =~ ^[0-9]+$ && "$pick" -ge 1 && "$pick" -le ${#topos[@]} ]] && break
        done
        topo="${topos[$((pick-1))]}"
    fi

    # 2. Sequence: specs authored for that topology.
    local rows
    mapfile -t rows < <(awk -F'\t' -v t="$topo" '$2 == t' <<<"$table")
    echo "Prompt sequences for '$topo':"
    local i=1 row
    for row in "${rows[@]}"; do
        IFS=$'\t' read -r r_path _ r_model r_reps r_kinds r_done <<<"$row"
        printf "  %d) %-40s %-12s %-20s (%s reps done)\n" \
            "$i" "$(basename "$r_path" .yaml)" "$r_kinds" "$r_model" "$r_done"
        i=$((i+1))
    done
    local pick
    if [[ ${#rows[@]} -eq 1 ]]; then
        pick=1
        echo "  -> only one, taking it"
    else
        while :; do
            read -rp "Pick sequence [1-${#rows[@]}]: " pick
            [[ "$pick" =~ ^[0-9]+$ && "$pick" -ge 1 && "$pick" -le ${#rows[@]} ]] && break
        done
    fi
    local d_path d_reps
    IFS=$'\t' read -r d_path _ _ d_reps _ _ <<<"${rows[$((pick-1))]}"

    # 3. Iterations (these APPEND to existing reps).
    local reps_in
    while :; do
        read -rp "Repetitions to run now [default ${d_reps}]: " reps_in
        [[ -z "$reps_in" || ( "$reps_in" =~ ^[0-9]+$ && "$reps_in" -ge 1 ) ]] && break
    done

    SPEC="$d_path"
    REPS="$reps_in"
    local flag=""
    [[ $CHECK_ONLY == 1 ]] && flag="--check "
    echo
    echo "equivalent command: $0 ${flag}${SPEC}${REPS:+ $REPS}"
    echo
}

if [[ -z "${1:-}" ]]; then
    pick_interactive
else
    SPEC="$1"
    REPS="${2:-}"
fi
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

# ── 2b. Resource guard ───────────────────────────────────────────────────
# 2026-08-05: a full lab bring-up (13 containers + backend + Ollama model in
# RAM) swap-thrashed the laptop to a near-crash (load avg 14). Two layers:
# refuse to deploy without headroom, and run a watchdog that tears everything
# down (./shutdown.sh) if free RAM hits critical during deploy/run.
MIN_FREE_MB="${MIN_FREE_MB:-6000}"
AVAIL_MB=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
if (( AVAIL_MB < MIN_FREE_MB )); then
    echo "FAIL: only ${AVAIL_MB}MB RAM available; lab deploy needs ${MIN_FREE_MB}MB headroom."
    echo "      Close apps (browser tabs, editors) and retry, or lower the bar consciously:"
    echo "      MIN_FREE_MB=4000 $0 $*"
    exit 1
fi
echo "ok: ${AVAIL_MB}MB RAM available (guard threshold ${MIN_FREE_MB}MB)"
nohup "$REPO/scripts/resource-guard.sh" $$ > /dev/null 2>&1 &
echo "ok: resource guard armed (auto-teardown below ${CRIT_MB:-1200}MB free; log: /tmp/nllm-resource-guard.log)"

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
