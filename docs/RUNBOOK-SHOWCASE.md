# Runbook — Showcase hosting: the WHOLE app on a vast.ai VM, one link for the supervisor

Goal: the supervisor opens **one URL** (from any browser, no setup) and gets the
full app — GUI, chat/LLM, live lab, PTY consoles — hosted entirely on a rented
vast.ai VM instance. Protected by a password you send along with the link.

Everything below is one script pair:
- `scripts/vast-showcase.sh` (your PC) — builds the GUI, syncs the repo, prints the link
- `scripts/showcase-remote.sh` (the instance) — full-stack install + systemd service

## 1. Rent the instance (web UI, ~5 min)

The full app needs docker + containerlab, so it MUST be a **VM-type instance**
— normal vast.ai instances are themselves containers and can't run the lab.
The public link uses vast.ai's **open ports**, which can ONLY be set at
creation time.

On https://cloud.vast.ai:

1. **SSH key first**: https://cloud.vast.ai/manage-keys/ — a VM created
   without your key is permanently inaccessible.
2. Templates → pick **"Ubuntu 22.04 VM"** (the VM template, not a docker one).
3. Edit the template before renting:
   - **Docker options / Ports**: add `-p 8000:8000`  ← this IS the public link;
     it cannot be added later.
   - **Disk**: ≥ 60 GB (fixed at creation; stack + qwen2.5:14b ≈ 25 GB, headroom for images).
4. In the offer search, set **Extra Filters**: `vms_enabled=true`.
   Pick an offer like the usual ones: ≥ 16 GB GPU VRAM (qwen2.5:14b),
   **≥ 32 GB RAM** (the lab is 13 containers), reliability > 0.99, cheap $/h.
5. Rent → wait until Running → note the SSH command (Connect button), e.g.
   `ssh -p 15129 root@ssh7.vast.ai`.

CLI alternative (same filters as RUNBOOK-VAST-VM.md, plus the port):

```bash
vastai search offers 'vms_enabled=true gpu_ram>=16 cpu_ram>=32 reliability>0.99 rentable=true' -o dph
vastai create instance <OFFER_ID> --disk 60 --ssh --direct --env '-p 8000:8000'
vastai ssh-url <INSTANCE_ID>     # -> ssh://root@HOST:PORT
```

## 2. Point the repo at it (your PC)

```bash
cd ~/thesis/networkLLMApp
cp scripts/vast-instance.env.example scripts/vast-instance.env   # if not already there
# fill in SSH_HOST / SSH_PORT from step 1; REMOTE_REPO_DIR=/root/networkLLMApp
```

## 3. One command

```bash
scripts/vast-showcase.sh up          # defaults: qwen2.5:14b + two-subnet-ixp lab
```

First run ≈ 10–30 min (docker, containerlab, images, model pull). It is
idempotent — if anything fails, fix and re-run. At the end it prints:

```
=== SEND THIS TO THE SUPERVISOR ===
  Link:      http://<PUBLIC_IP>:<PORT>
  Login:     any username (e.g. demo)
  Password:  <generated, stored in scripts/showcase-password>
```

Use the open-port link only for your own testing. Verify it from a phone on
mobile data first — that proves it works outside your network.

## 4. The EVENT link — cloudflare tunnel (mandatory, point 6)

**The demo runs exclusively over the cloudflare tunnel**: the BearingPoint
venue firewall passes only ports 80/443, so the raw vast.ai link (random high
port) will NOT open there.

```bash
scripts/vast-showcase.sh tunnel
```

Starts a Cloudflare quick tunnel on the instance and prints an
`https://….trycloudflare.com` link (same password) — **this is the link to
send the supervisor**. Caveat: the URL changes whenever the tunnel restarts —
send it close to the event.

Abuse protection is app-side (quick tunnels take no Cloudflare WAF/rate
rules): the backend locks an IP after 5 failed passwords (until backend
restart — `systemctl restart showcase-backend` unlocks) and rate-limits to 40
requests / 10 s per IP (429 above that), keyed on `CF-Connecting-IP` so it
sees real client addresses through the tunnel.

Note: `up` also sideloads the vulnerability-scan tool (`tools-cache/` →
`~/.local/bin/dockerscan` + CVE DB) — scans work on the instance now.

## 5. Event-day checklist

- `scripts/vast-showcase.sh status` — backend active + lab active + URL.
- Open the link in a browser, log in, send one chat message end-to-end.
- Do NOT destroy the instance until the event is over (billing runs, but the
  link dies with the instance).
- The lab survives supervisor mistakes: the GUI's scenario dropdown can stop /
  restart labs, and `up` can be re-run any time.

## 6. Pausing between now and the event (don't pay for idle days)

Two ways to not pay GPU rent for a week. **Recommended: destroy + re-provision
per day** — `up` now rebuilds everything on ANY fresh VM instance in one
command, models included.

### Option A (recommended): destroy now, re-provision each demo day

Zero billing while idle. The link changes each time (new IP/port), so you send
the supervisor a fresh link on the morning of each day — factor that into how
you communicate with him.

```bash
# --- when you're done for now ---
scripts/vast-showcase.sh down
vastai destroy instance <INSTANCE_ID>     # or the web console's Destroy
vastai show instances                     # MUST show it gone (billing stops)

# --- morning of the test day / event day (~30-60 min before you need it) ---
# 1. rent a fresh VM instance per section 1 (SSH key, Ubuntu VM template,
#    '-p 8000:8000' in docker options, vms_enabled=true, disk >= 60GB)
# 2. update scripts/vast-instance.env with the new SSH_HOST/SSH_PORT
# 3. ONE command:
scripts/vast-showcase.sh up llama3.1:8b
# 4. it ends with the link + password -> phone-test it -> send to the supervisor
```

Timing for step 3, measured: ~15 min on a host with a clean network; up to
~45 min when the host MITMs the CDNs (wheels + docker images + the model all
sideload from this PC automatically — `up` detects it and does the right
thing, including pulling nothing you don't already have locally; if the model
is missing from THIS PC it tells you to `ollama pull` it first).

### Option B: stop (not destroy) and restart on demo day

Stopped instances bill **storage only** (~$0.10–0.15/GB/month — cents/day for
a 126 GB disk), and the link/IP usually survives. **The catch, per vast.ai's
own FAQ: while stopped, the GPU can be rented to someone else, and your
restart then hangs until it frees — possibly not on your schedule.** Fine for
a day or two; risky as the only plan for the event itself.

```bash
scripts/vast-showcase.sh down        # graceful: stop app + lab first
# then the STOP button on cloud.vast.ai (or: vastai stop instance <ID>)

# demo day: START button (may wait if the GPU is taken!) then:
scripts/vast-showcase.sh up llama3.1:8b     # re-heals GPU driver, restarts all,
                                            # re-warms, prints the current link
```

If a restart hangs on a taken GPU: destroy it and fall back to Option A —
`up` on a fresh instance is the reliable path.

**Either way, after ANY start/restart, always run `up` once** — it is
idempotent, self-heals the GPU driver (this VM boots with a stale nvidia
module), redeploys the lab, re-warms the model, and prints the current link
(the port mapping can change across restarts).

## 7. After the event

```bash
scripts/vast-showcase.sh down        # stop app + lab
vastai destroy instance <INSTANCE_ID>   # billing stops ONLY on destroy
vastai show instances                   # confirm gone
```

## How it fits together (for future sessions)

- The backend serves the **built GUI** (`gui/dist`) itself on one port —
  `app/main.py` mounts it when the build exists; `gui/src/api.ts` uses
  same-origin relative URLs in production builds (dev on :5173 unchanged).
- `SHOWCASE_PASSWORD` env → HTTP basic auth over everything, plus an auth
  cookie so the PTY console WebSocket is covered too. Unset = no auth (local dev).
- vast.ai maps internal port 8000 to a public `IP:PORT`; inside the VM the
  mapping is in `/etc/environment` (`VAST_TCP_PORT_8000`, `PUBLIC_IPADDR`) —
  also visible via the instance's **IP Port Info** button on cloud.vast.ai.

## Related

[[RUNBOOK-VAST-VM]] · [[RUNBOOK-VAST-TUNNEL]] · [[DEMO]]
