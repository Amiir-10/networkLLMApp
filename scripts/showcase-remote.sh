#!/usr/bin/env bash
# showcase-remote.sh — runs ON the vast.ai VM instance. Stands up the ENTIRE
# app for the public showcase: full stack (docker, containerlab, venv, lab
# images, Ollama + model) + the backend serving the built GUI on 0.0.0.0:8000
# as a systemd service, then deploys the demo lab and prints the public URL.
#
# Invoked by scripts/vast-showcase.sh from the PC — you normally never run
# this by hand. Direct usage (on the VM, repo already present):
#   SHOWCASE_PASSWORD=<pw> scripts/showcase-remote.sh [model] [scenario]
#     model    default: qwen2.5:14b
#     scenario default: two-subnet-ixp   ("none" = skip the lab deploy)
#
# Idempotent — re-run after any failure. Requires a VM-type instance
# (vms_enabled=true): normal vast.ai instances cannot run containerlab.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MODEL="${1:-qwen2.5:14b}"
SCENARIO="${2:-two-subnet-ixp}"
# NOTE: no apostrophes in a ${var:?message} — bash parses quotes INSIDE the
# expansion word, and a lone quote silently swallows the following lines.
: "${SHOWCASE_PASSWORD:?SHOWCASE_PASSWORD must be set (the supervisor login password)}"

PORT=8000
UNIT=/etc/systemd/system/showcase-backend.service

bold() { printf '\033[1m%s\033[0m\n' "$1"; }

# ── 0. Preconditions ──────────────────────────────────────────────────────
[[ -f gui/dist/index.html ]] || {
  echo "FAIL: gui/dist/index.html missing — the GUI build is rsynced from the PC."
  echo "      Run scripts/vast-showcase.sh up from the PC (it builds + syncs)."
  exit 1
}
if ! systemctl list-units >/dev/null 2>&1; then
  echo "FAIL: no systemd — this is NOT a VM-type instance. The lab (docker-in-"
  echo "      docker via containerlab) cannot run here. Rent with vms_enabled=true."
  exit 1
fi

# ── 1a. Python >=3.11 + offline wheelhouse ────────────────────────────────
# Two instance realities discovered 2026-08-21: (a) requirements.lock.txt is
# frozen on Python 3.12 (the contourpy pin needs >=3.11) but Ubuntu 22.04
# ships 3.10; (b) this host's proxy MITMs files.pythonhosted.org with an
# internal CA, so pip cannot download ANYTHING over TLS. Fix: python3.12 via
# deadsnakes (PPA endpoint verified un-intercepted) + pip resolving OFFLINE
# from the wheelhouse/ directory rsynced up with the repo (wheels downloaded
# on the PC, where TLS verifies properly).
bold ">>> [1a/5] python >=3.11 + wheelhouse"
PYBIN=python3.12
if ! command -v "$PYBIN" >/dev/null; then
  if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)'; then
    PYBIN=python3
  else
    apt-get update -qq
    apt-get install -y -qq software-properties-common
    add-apt-repository -y ppa:deadsnakes/ppa >/dev/null
    apt-get install -y -qq python3.12 python3.12-venv
  fi
fi
if [[ ! -x .venv/bin/python ]] \
   || ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
  rm -rf .venv
  "$PYBIN" -m venv .venv
fi
echo "  ok: venv on $(.venv/bin/python --version)"
if [[ -d wheelhouse ]]; then
  export PIP_NO_INDEX=1 PIP_FIND_LINKS="$REPO_ROOT/wheelhouse"
  echo "  ok: pip pinned to offline wheelhouse ($(ls wheelhouse | wc -l) wheels)"
fi

# ── 1b. Sideloaded lab images ─────────────────────────────────────────────
# Same MITM proxy also intercepts Docker Hub (registry-1.docker.io +
# production.cloudflare.docker.com), so builds/pulls fail TLS. The PC's
# built-and-verified images ship in images-cache/ (rsynced with the repo);
# loading them here means setup.sh finds every image present and never
# touches the Hub. Also more deterministic: the demo runs the EXACT images
# the lab was verified with locally.
if compgen -G "images-cache/*.tar.gz" >/dev/null && command -v docker >/dev/null; then
  need_load=0
  for img in firewalld-fw:latest weblab:latest alpine:3.20 postgres:16-alpine; do
    docker image inspect "$img" >/dev/null 2>&1 || { need_load=1; break; }
  done
  if [[ $need_load -eq 1 ]]; then
    bold ">>> [1b/5] loading sideloaded lab images"
    for tb in images-cache/*.tar.gz; do
      gunzip -c "$tb" | docker load
    done
  else
    echo "  ok: all lab images already present"
  fi
fi

# ── 1c. GPU driver self-heal ──────────────────────────────────────────────
# This VM boots with a stale nvidia kernel module (580.95) while userspace is
# 580.173 → nvidia-smi fails "Driver/library version mismatch" and Ollama
# silently falls back to 100% CPU (~67 s/turn instead of ~3.5 s). The on-disk
# module matches userspace, so a reload fixes it. Self-heal on every run in
# case the VM rebooted.
if command -v nvidia-smi >/dev/null && ! nvidia-smi >/dev/null 2>&1; then
  bold ">>> [1c/5] nvidia driver mismatch — reloading kernel module"
  systemctl stop ollama 2>/dev/null || true
  rmmod nvidia_uvm nvidia_drm nvidia_modeset nvidia 2>/dev/null || true
  modprobe nvidia && modprobe nvidia_uvm
  nvidia-smi --query-gpu=name --format=csv,noheader || {
    echo "FAIL: GPU still unavailable after module reload — chat would run on CPU (unusably slow)."; exit 1; }
  echo "  ok: GPU back"
fi

# ── 1d. Ollama keep-alive ─────────────────────────────────────────────────
# Default keep_alive unloads the model after ~4 idle minutes; the next
# request then pays a ~1 min VRAM reload — at the event that reads as a hang.
# Pin the model in memory permanently.
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/showcase.conf <<'EOF'
[Service]
Environment=OLLAMA_KEEP_ALIVE=-1
EOF
systemctl daemon-reload

# ── 1e. Ollama early + model-source probe ─────────────────────────────────
# Install ollama BEFORE setup.sh so we can decide where the model comes from.
# Hosts like this one MITM ollama's R2 blob CDN, making `ollama pull`
# impossible; the PC then sideloads the model store instead. Probe: TLS to
# ollama's R2 bucket host must present a legitimate CA. Exit 42 tells
# vast-showcase.sh to rsync the model and re-run (fully automatic).
bold ">>> [1e/5] ollama + model source"
if ! command -v ollama >/dev/null; then
  curl -fsSL https://ollama.com/install.sh | sh
fi
systemctl enable --now ollama 2>/dev/null || true
for i in $(seq 1 20); do curl -sf localhost:11434/api/tags >/dev/null && break; sleep 1; done
if curl -sf localhost:11434/api/tags | grep -q "\"$MODEL\""; then
  echo "  ok: $MODEL already present"
else
  R2_HOST=dd20bb891979d25aebc8bec07b2b3bbc.r2.cloudflarestorage.com
  issuer=$(echo | timeout 10 openssl s_client -connect "$R2_HOST:443" -servername "$R2_HOST" 2>/dev/null \
           | grep -o "issuer=.*" | head -1 || true)
  if [[ "$issuer" == *"Internal Proxy"* || "$issuer" == *"unknown"* ]]; then
    echo "  model CDN is MITM'd on this host ($issuer)"
    echo "NEED_MODEL_SIDELOAD"
    exit 42
  fi
  echo "  ok: model CDN reachable — setup.sh will pull $MODEL"
fi

# ── 1. Full stack (idempotent) ────────────────────────────────────────────
bold ">>> [1/5] full stack + model $MODEL (scripts/setup.sh)"
scripts/setup.sh --with-ollama "$MODEL"

# ── 2. Backend as a systemd service on 0.0.0.0:8000 ───────────────────────
bold ">>> [2/5] backend service (0.0.0.0:$PORT, basic-auth on)"
cat > "$UNIT" <<EOF
[Unit]
Description=networkLLMApp showcase backend (GUI + API, basic-auth)
After=network-online.target docker.service ollama.service

[Service]
WorkingDirectory=$REPO_ROOT
Environment=SHOWCASE_PASSWORD=$SHOWCASE_PASSWORD
Environment=SHOWCASE_MODEL=$MODEL
ExecStart=$REPO_ROOT/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
chmod 600 "$UNIT"   # the unit embeds the password
systemctl daemon-reload
systemctl enable --now showcase-backend.service
systemctl restart showcase-backend.service   # pick up a re-synced tree/password

CURL_AUTH=(-u "demo:$SHOWCASE_PASSWORD")
for i in $(seq 1 30); do
  curl -sf "${CURL_AUTH[@]}" "http://localhost:$PORT/health" >/dev/null && break
  [[ $i -eq 30 ]] && { echo "FAIL: backend did not come up"; journalctl -u showcase-backend -n 30 --no-pager; exit 1; }
  sleep 1
done
# Auth must actually gate: an unauthenticated request has to bounce.
[[ "$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/health")" == 401 ]] \
  || { echo "FAIL: basic auth NOT enforced"; exit 1; }
curl -sf "${CURL_AUTH[@]}" "http://localhost:$PORT/" | grep -qi "<html" \
  || { echo "FAIL: GUI not served at /"; exit 1; }
echo "  ok: backend up, auth enforced, GUI served"

# ── 3. Demo lab ───────────────────────────────────────────────────────────
if [[ "$SCENARIO" != "none" ]]; then
  bold ">>> [3/5] deploy demo lab: $SCENARIO"
  free_mb=$(free -m | awk '/^Mem:/{print $7}')
  if (( free_mb < 6000 )); then
    echo "  WARN: only ${free_mb}MB free RAM — the 13-container lab may struggle."
  fi
  state=$(curl -sf "${CURL_AUTH[@]}" "http://localhost:$PORT/health")
  if grep -q '"lab_active":true' <<<"$state"; then
    echo "  ok: a lab is already active — leaving it"
  else
    curl -sf "${CURL_AUTH[@]}" -X POST "http://localhost:$PORT/lab/start/$SCENARIO" \
      || { echo "FAIL: lab deploy failed"; exit 1; }
    echo
    echo "  ok: lab $SCENARIO deployed"
  fi
else
  bold ">>> [3/5] lab deploy skipped (scenario=none)"
fi

# ── 3b. Warm the model into VRAM ──────────────────────────────────────────
# Restart ollama so the keep-alive drop-in applies, then trigger one load:
# with OLLAMA_KEEP_ALIVE=-1 the model stays resident, so the supervisor's
# first question answers in seconds, not after a ~1 min cold load.
bold ">>> [3b/5] warming $MODEL into VRAM (stays resident)"
systemctl restart ollama
for i in $(seq 1 15); do curl -sf localhost:11434/api/tags >/dev/null && break; sleep 1; done
# num_ctx MUST match what app/chat.py sends (OLLAMA_NUM_CTX, default 16384):
# a context-size mismatch forces a runner reload on the first real chat.
curl -s --max-time 300 localhost:11434/api/generate \
  -d "{\"model\":\"$MODEL\",\"prompt\":\"\",\"options\":{\"num_ctx\":${OLLAMA_NUM_CTX:-16384}}}" >/dev/null || true
ollama ps | tail -n +2 | grep -q "GPU" && echo "  ok: model resident on GPU" \
  || echo "  WARN: model not on GPU — check nvidia-smi / ollama ps"

# ── 4. cloudflared on standby (fallback link) ─────────────────────────────
bold ">>> [4/5] cloudflared fallback binary"
if ! command -v cloudflared >/dev/null; then
  arch=$(uname -m); case "$arch" in x86_64) cfarch=amd64 ;; aarch64) cfarch=arm64 ;; *) cfarch="" ;; esac
  if [[ -n "$cfarch" ]]; then
    curl -fsSL -o /usr/local/bin/cloudflared \
      "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$cfarch" \
      && chmod +x /usr/local/bin/cloudflared \
      && echo "  ok: cloudflared installed (standby)" \
      || echo "  WARN: cloudflared download failed — fallback link unavailable until retried"
  else
    echo "  WARN: unsupported arch $arch for cloudflared"
  fi
else
  echo "  ok: cloudflared present"
fi

# ── 5. The public URL ─────────────────────────────────────────────────────
bold ">>> [5/5] public address"
# vast.ai writes the instance env (incl. port mappings) to /etc/environment.
set +u; source /etc/environment 2>/dev/null; set -u
EXT_PORT="${VAST_TCP_PORT_8000:-}"
PUB_IP="${PUBLIC_IPADDR:-$(curl -fsS --max-time 10 ifconfig.me 2>/dev/null || true)}"
echo
if [[ -n "$EXT_PORT" && -n "$PUB_IP" ]]; then
  echo "SHOWCASE_URL=http://$PUB_IP:$EXT_PORT"
else
  echo "SHOWCASE_URL=unknown"
  echo "  Port 8000 has no external mapping ($([[ -n "$PUB_IP" ]] && echo "IP=$PUB_IP" || echo "no public IP either"))."
  echo "  Either the instance was created WITHOUT '-p 8000:8000' in its docker"
  echo "  options (check the IP Port Info button on cloud.vast.ai) — or use the"
  echo "  fallback:  scripts/vast-showcase.sh tunnel"
fi
