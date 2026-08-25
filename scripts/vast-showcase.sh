#!/usr/bin/env bash
# vast-showcase.sh — host the ENTIRE app (GUI + backend + lab + Ollama) on the
# rented vast.ai VM instance and get ONE password-protected link to send to
# the supervisor. Runs on YOUR PC.
#
# Usage:
#   scripts/vast-showcase.sh [up] [model] [scenario]   # default: qwen2.5:14b two-subnet-ixp
#   scripts/vast-showcase.sh status                    # health + the URL again
#   scripts/vast-showcase.sh tunnel                    # FALLBACK link (cloudflare quick
#                                                      # tunnel) if the open port fails
#   scripts/vast-showcase.sh down                      # stop app + lab on the instance
#
# Prereqs (see docs/RUNBOOK-SHOWCASE.md for the full walkthrough):
#   * a VM-type instance (vms_enabled=true) rented WITH '-p 8000:8000' in its
#     docker options — the open port CANNOT be added after creation
#   * scripts/vast-instance.env filled with its SSH_HOST/SSH_PORT
#   * node/npm on this PC (the GUI is built here, not on the instance)
#
# The supervisor logs in with ANY username + the password this script prints.
# The password persists in scripts/showcase-password (gitignored).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE="$REPO_ROOT/scripts/vast-instance.env"
[[ -f "$ENV_FILE" ]] || { echo "FAIL: $ENV_FILE missing — copy vast-instance.env.example and fill it in."; exit 1; }
# shellcheck source=/dev/null
source "$ENV_FILE"
: "${SSH_HOST:?SSH_HOST unset in vast-instance.env}"
: "${SSH_PORT:?SSH_PORT unset}"
: "${SSH_USER:=root}"
: "${REMOTE_REPO_DIR:=/root/networkLLMApp}"

SSH=(ssh -p "$SSH_PORT" -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "$SSH_USER@$SSH_HOST")

CMD="${1:-up}"
bold() { printf '\033[1m%s\033[0m\n' "$1"; }

# ── password (generated once, reused on re-runs) ──────────────────────────
PW_FILE="$REPO_ROOT/scripts/showcase-password"
password() {
  if [[ ! -s "$PW_FILE" ]]; then
    tr -dc 'a-z0-9' </dev/urandom | head -c 12 > "$PW_FILE"
    chmod 600 "$PW_FILE"
  fi
  cat "$PW_FILE"
}

print_access() {  # $1 = URL line ("SHOWCASE_URL=...")
  local url="${1#SHOWCASE_URL=}"
  echo
  bold "=== ACCESS ==="
  if [[ "$url" == "unknown" || -z "$url" ]]; then
    echo "  Open port not mapped."
  else
    echo "  Direct link (testing only):  $url"
  fi
  echo "  Login:     any username (e.g. demo)"
  echo "  Password:  $(password)"
  echo
  bold "  THE DEMO RUNS EXCLUSIVELY OVER THE CLOUDFLARE TUNNEL (point 6):"
  echo "  the venue firewall (BearingPoint) only passes ports 80/443, so the"
  echo "  raw vast.ai link (random high port) will NOT work there. Run:"
  echo "    scripts/vast-showcase.sh tunnel"
  echo "  and send the https://…trycloudflare.com link it prints."
  echo
  echo "  Keep the instance running until after the event — destroying it kills the link."
}

case "$CMD" in
# ── up: build GUI here, sync repo, provision + serve there ────────────────
up)
  MODEL="${2:-qwen2.5:14b}"
  SCENARIO="${3:-two-subnet-ixp}"

  bold ">>> [1/3] building the GUI locally (gui/dist)"
  command -v npm >/dev/null || { echo "FAIL: npm missing on this PC (the GUI builds here)"; exit 1; }
  ( cd gui
    [[ -d node_modules ]] || { [[ -f package-lock.json ]] && npm ci --silent || npm install --silent; }
    npm run build --silent )
  [[ -f gui/dist/index.html ]] || { echo "FAIL: GUI build produced no gui/dist/index.html"; exit 1; }

  # dockerscan (the vulnerability_scan tool's binary + CVE DB) ships from this
  # PC — it was never installed on instances, so scans errored "dockerscan not
  # found" there (supervisor point 4). tools-cache/ is gitignored and rsynced
  # with the repo; showcase-remote.sh installs it to ~/.local/bin + ~/.dockerscan.
  mkdir -p tools-cache
  if [[ -x "$HOME/.local/bin/dockerscan" ]]; then
    cp -u "$HOME/.local/bin/dockerscan" tools-cache/dockerscan
    [[ -f "$HOME/.dockerscan/cve-db.sqlite" ]] && cp -u "$HOME/.dockerscan/cve-db.sqlite" tools-cache/cve-db.sqlite
  else
    echo "WARN: ~/.local/bin/dockerscan missing on this PC — the instance's vulnerability_scan tool will not work."
  fi

  bold ">>> [2/3] syncing repo -> $SSH_USER@$SSH_HOST:$REMOTE_REPO_DIR"
  rsync -az --delete \
    -e "ssh -p $SSH_PORT -o StrictHostKeyChecking=accept-new" \
    --exclude .git --exclude .venv --exclude gui/node_modules \
    --exclude '__pycache__' --exclude data/ --exclude labs/ \
    --exclude scripts/vast-instance.env --exclude scripts/showcase-password \
    "$REPO_ROOT/" "$SSH_USER@$SSH_HOST:$REMOTE_REPO_DIR/"

  bold ">>> [3/3] provisioning + starting on the instance (idempotent, ~10-30 min first run)"
  # Sideload $MODEL from this PC's ollama store — used when the instance's
  # host MITMs ollama's blob CDN so `ollama pull` cannot work there.
  sideload_model() {
    local model="$1" store=/usr/share/ollama/.ollama/models
    local name="${model%%:*}" tag="${model#*:}"
    [[ "$model" == *:* ]] || tag=latest
    local manifest="$store/manifests/registry.ollama.ai/library/$name/$tag"
    if [[ ! -f "$manifest" ]]; then
      echo "FAIL: $model is not in this PC's ollama store — run:  ollama pull $model"
      echo "      then re-run:  scripts/vast-showcase.sh up $model"
      exit 1
    fi
    bold ">>> sideloading $model from this PC (instance host MITMs the model CDN)"
    local blobs
    blobs=$(python3 -c "
import json
m = json.load(open('$manifest'))
print('\n'.join('sha256-' + d['digest'].split(':')[1] for d in m['layers'] + [m['config']]))")
    "${SSH[@]}" "mkdir -p $store/blobs $store/manifests/registry.ollama.ai/library/$name"
    ( cd "$store" && rsync -a --partial --inplace --info=progress2 \
        -e "ssh -p $SSH_PORT -o StrictHostKeyChecking=accept-new" \
        $(sed 's|^|blobs/|' <<<"$blobs") "$SSH_USER@$SSH_HOST:$store/blobs/" )
    rsync -a -e "ssh -p $SSH_PORT" "$manifest" \
      "$SSH_USER@$SSH_HOST:$store/manifests/registry.ollama.ai/library/$name/$tag"
    "${SSH[@]}" "chown -R ollama:ollama $store"
  }
  remote_up() {
    set +e
    out=$("${SSH[@]}" "cd $REMOTE_REPO_DIR && SHOWCASE_PASSWORD='$(password)' scripts/showcase-remote.sh '$MODEL' '$SCENARIO'" | tee /dev/stderr)
    rc=$?
    set -e
  }
  remote_up
  if [[ $rc -eq 42 ]]; then      # remote asked for the model via sideload
    sideload_model "$MODEL"
    remote_up
  fi
  [[ $rc -eq 0 ]] || {
    echo; echo "FAIL: remote provisioning failed — re-run 'scripts/vast-showcase.sh up' after fixing (it is idempotent)."; exit 1; }
  print_access "$(grep -o 'SHOWCASE_URL=.*' <<<"$out" | tail -1)"
  ;;

# ── status ────────────────────────────────────────────────────────────────
status)
  out=$("${SSH[@]}" "
    systemctl is-active showcase-backend.service || true
    curl -s -u demo:\$(systemctl show showcase-backend -p Environment --value | sed -n 's/.*SHOWCASE_PASSWORD=\([^ ]*\).*/\1/p') http://localhost:8000/health || true
    echo
    set +u; source /etc/environment 2>/dev/null
    if [[ -n \"\${VAST_TCP_PORT_8000:-}\" && -n \"\${PUBLIC_IPADDR:-}\" ]]; then
      echo SHOWCASE_URL=http://\$PUBLIC_IPADDR:\$VAST_TCP_PORT_8000
    else
      echo SHOWCASE_URL=unknown
    fi" | tee /dev/stderr)
  print_access "$(grep -o 'SHOWCASE_URL=.*' <<<"$out" | tail -1)"
  ;;

# ── tunnel: cloudflare quick tunnel — THE event link (port 443) ───────────
# The demo runs exclusively over this link: the venue firewall only passes
# 80/443. The URL rotates on tunnel restart — send it close to the event.
tunnel)
  bold ">>> starting cloudflare quick tunnel on the instance"
  url=$("${SSH[@]}" '
    command -v cloudflared >/dev/null || { echo "FAIL: cloudflared missing — run: scripts/vast-showcase.sh up" >&2; exit 1; }
    cat > /etc/systemd/system/showcase-tunnel.service <<EOF
[Unit]
Description=cloudflare quick tunnel for the showcase app
After=showcase-backend.service

[Service]
ExecStart=/usr/local/bin/cloudflared tunnel --no-autoupdate --url http://localhost:8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now showcase-tunnel.service >/dev/null 2>&1
    systemctl restart showcase-tunnel.service
    for i in $(seq 1 30); do
      u=$(journalctl -u showcase-tunnel --since "-2 min" --no-pager 2>/dev/null | grep -o "https://[a-z0-9-]*\.trycloudflare\.com" | tail -1)
      [[ -n "$u" ]] && { echo "$u"; exit 0; }
      sleep 2
    done
    echo "FAIL: no tunnel URL after 60s" >&2; exit 1')
  echo
  bold "=== EVENT LINK (cloudflare, port 443 — send THIS to the supervisor) ==="
  echo "  Link:      $url"
  echo "  Login:     any username (e.g. demo)"
  echo "  Password:  $(password)"
  echo
  echo "  NOTE: this URL changes every time the tunnel restarts — send it close to"
  echo "  the event. The venue firewall blocks the raw open-port link (point 6)."
  ;;

# ── down ──────────────────────────────────────────────────────────────────
down)
  "${SSH[@]}" "
    systemctl disable --now showcase-tunnel.service 2>/dev/null || true
    systemctl disable --now showcase-backend.service 2>/dev/null || true
    cd $REMOTE_REPO_DIR 2>/dev/null && ./shutdown.sh 2>/dev/null || true
    echo 'stopped (instance still rented — destroy it on cloud.vast.ai to stop billing)'"
  ;;

*)
  echo "usage: $0 [up [model] [scenario] | status | tunnel | down]"; exit 2 ;;
esac
