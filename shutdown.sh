#!/usr/bin/env bash
# shutdown.sh — cleanly stop EVERYTHING the thesis stack runs.
#
# Usage: ./shutdown.sh
#
# Order matters:
#   1. Stop the lab through the backend API while the backend is still alive
#      (graceful containerlab destroy).
#   2. Sweep any surviving clab-* containers directly — covers the stale-lab
#      class: a dead backend, or a host reboot where docker's restart policy
#      resurrected old lab containers with no backend state (the 409 / dbus
#      crash-loop source). Force-remove, never stop: `restart: always`
#      resurrects stopped containers.
#   3. Kill the backend (uvicorn :8000) and the GUI dev server (vite :5173).
#   4. Verify: both ports closed, zero clab containers. Exit nonzero if
#      anything survived.
#
# pkill patterns use the bracket trick ([u]vicorn) so pkill cannot match its
# own command line, and no required step is chained on a kill's exit code.
set -uo pipefail

BACKEND="http://localhost:8000"
FAIL=0

# ── 1. Graceful lab stop via the API ─────────────────────────────────────
HEALTH="$(curl -sf --max-time 5 "$BACKEND/health" 2>/dev/null || true)"
if [[ -n "$HEALTH" ]]; then
    SCENARIO="$(sed -n 's/.*"scenario":[[:space:]]*"\([^"]*\)".*/\1/p' <<<"$HEALTH")"
    if grep -q '"lab_active":[[:space:]]*true' <<<"$HEALTH" && [[ -n "$SCENARIO" ]]; then
        echo "lab '$SCENARIO' active — stopping via API (takes a minute)..."
        curl -sf -X POST "$BACKEND/lab/stop/$SCENARIO" --max-time 300 > /dev/null \
            && echo "ok: lab stopped" \
            || echo "warn: API stop failed — the container sweep below will clean up"
    else
        echo "ok: backend up, no active lab"
    fi
else
    echo "ok: backend not responding (already down, or lab containers are stale)"
fi

# ── 2. Sweep surviving lab containers ────────────────────────────────────
STALE="$(docker ps -aq --filter "name=clab-" 2>/dev/null || true)"
if [[ -n "$STALE" ]]; then
    echo "found $(wc -l <<<"$STALE") clab container(s) — force-removing..."
    CLAB="$(command -v containerlab || true)"
    if [[ -n "$CLAB" ]]; then
        # sudo needs the binary's full path (not in root's PATH).
        sudo "$CLAB" destroy --all --cleanup 2>/dev/null \
            || echo "warn: containerlab destroy failed — falling back to docker rm"
    fi
    # rm -f (not stop): restart policies resurrect stopped containers.
    SURVIVORS="$(docker ps -aq --filter "name=clab-" 2>/dev/null || true)"
    [[ -n "$SURVIVORS" ]] && docker rm -f $SURVIVORS > /dev/null 2>&1
    echo "ok: lab containers removed"
else
    echo "ok: no clab containers"
fi

# ── 3. Kill backend + frontend ───────────────────────────────────────────
pkill -f "[u]vicorn app.main" 2>/dev/null && echo "ok: backend (uvicorn) killed" \
    || echo "ok: no backend process"
pkill -f "networkLLMApp/gui.*[v]ite" 2>/dev/null && echo "ok: frontend (vite) killed" \
    || echo "ok: no frontend process"

# ── 4. Verify everything is actually down ────────────────────────────────
sleep 1
if curl -sf --max-time 3 "$BACKEND/health" > /dev/null 2>&1; then
    echo "FAIL: something still answers on :8000"; FAIL=1
else
    echo "verified: :8000 closed"
fi
if curl -sf --max-time 3 "http://localhost:5173" > /dev/null 2>&1; then
    echo "FAIL: something still answers on :5173"; FAIL=1
else
    echo "verified: :5173 closed"
fi
LEFT="$(docker ps -aq --filter "name=clab-" 2>/dev/null || true)"
if [[ -n "$LEFT" ]]; then
    echo "FAIL: clab containers still present:"; docker ps -a --filter "name=clab-" --format '  {{.Names}}  {{.Status}}'; FAIL=1
else
    echo "verified: no clab containers"
fi

if [[ $FAIL == 0 ]]; then
    echo "all clean — safe to walk away."
else
    echo "shutdown INCOMPLETE — see FAIL lines above."
fi
exit $FAIL
