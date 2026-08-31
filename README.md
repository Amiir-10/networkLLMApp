# networkLLMApp — NetSec Zero-Trust Co-pilot

Natural-language network security management. A chat interface backed by a local LLM (via Ollama) translates plain-English requests — "block all traffic between pc1a and pc2a", "scan pc1a for vulnerabilities", "test ping between pc1b and pc1a" — into real firewall and diagnostic actions, executed against a live containerlab network where every device (PC, switch, firewall, router) is a Linux container.

Linux-only (relies on kernel namespaces and bridges).

## Features

- **Chat-driven firewall control** — the LLM calls typed tools (block/allow traffic, flush rules, ping tests, vulnerability scans, shell commands) that are validated and executed deterministically by the backend; results are grounded in the real containers, never fabricated.
- **Live topology view** — React GUI renders the network graph, active firewall rules, and vulnerable nodes in real time.
- **Per-node consoles** — real persistent shells into any container, straight from the browser.
- **In-lab web browser** — page loads run inside a chosen lab PC and traverse the firewalls, so blocks are visible as real timeouts.
- **Vulnerability scanning** — container images are scanned for CVEs; flagged nodes turn red in the topology and can be fixed through chat.

## Requirements

- Linux host with Docker
- Python 3.12
- Node.js + npm (for the GUI)
- [containerlab](https://containerlab.dev/)
- [Ollama](https://ollama.com/) with at least one tool-calling model (e.g. `ollama pull llama3.1:8b`)

`scripts/setup.sh` checks for and installs the backend-side dependencies automatically.

## How to run

```bash
./app.sh
```

That single command installs anything missing, starts the backend on `:8000` and the GUI on `:5173`, and prints the URL. Then, in the GUI:

1. Pick a scenario from the dropdown (e.g. `two-subnet-ixp`) and start the lab. First deploy builds/pulls the container images and takes a few minutes; keep ~6 GB of RAM free.
2. Chat with the assistant: *"Please block communication between pc1b and pc2b"*, *"test ping between pc1b and pc1a"*, *"scan pc1a for vulnerabilities"*.
3. Verify effects yourself in the **Console** tab (real shells) or the **Browser** tab (page loads from inside a PC).

To stop everything (backend, GUI, and any running lab):

```bash
./shutdown.sh
```

## Running on a cloud instance (one shareable link)

The whole stack (GUI + backend + lab + Ollama) can be hosted on a rented GPU VM — vast.ai, Azure, or any Ubuntu 22.04+ VM you can SSH into as root — producing a single password-protected URL that works from any browser.

**Instance requirements:** a real VM (not a container — the lab needs Docker + containerlab inside), ≥ 16 GB GPU VRAM, ≥ 32 GB RAM, ≥ 60 GB disk. On vast.ai: pick the *Ubuntu 22.04 VM* template, filter offers with `vms_enabled=true`, and add `-p 8000:8000` to the docker options **at creation time** (the open port cannot be added later).

**1. Point the repo at the instance** (on your PC):

```bash
cp scripts/vast-instance.env.example scripts/vast-instance.env
# edit it: SSH_HOST / SSH_PORT from the instance's SSH command
```

**2. Deploy — one command:**

```bash
scripts/vast-showcase.sh up qwen2.5:14b        # model and scenario are optional args
```

This builds the GUI locally, rsyncs the repo, installs the full stack on the instance (Docker, containerlab, Python, Ollama + model, lab images), starts the backend as a systemd service, deploys the lab, and prints the link plus a password. Idempotent — re-run it after any failure or code change. First run takes 10–30 min; later runs are minutes. Log in with any username and the printed password (kept in `scripts/showcase-password`, gitignored).

**3. HTTPS link on port 443** (for networks that only pass 80/443):

```bash
scripts/vast-showcase.sh tunnel
```

Starts a Cloudflare quick tunnel on the instance and prints an `https://….trycloudflare.com` URL fronting the app. The URL changes on every tunnel restart, so generate it shortly before sharing. Note: free quick tunnels cap each request at ~100 s, so keep individual chat actions short (e.g. scan one node at a time, not `all`).

**Other commands:**

```bash
scripts/vast-showcase.sh status   # health check + print the URL again
scripts/vast-showcase.sh down     # stop the app + lab on the instance
```

`down` stops the services but the instance keeps billing — destroy it in the provider's console when finished.

## Architecture

```
React GUI (:5173) ──→ FastAPI backend (:8000) ──→ Ollama (LLM, :11434)
                             │                       │ typed tool calls
                             ▼                       ▼
                      containerlab lab  ◀──  tool dispatch (firewalld REST API,
                      (Docker containers)     docker exec, image scanner)
```

The backend owns all state changes: the LLM only *chooses* tools; argument validation, firewall selection, and execution are deterministic Python.

## Repo layout

```
app/            FastAPI backend: chat loop, tool dispatch, lab driver, scanner
gui/            React + Vite frontend
scenarios/      containerlab topologies (YAML)
firewall-image/ firewalld-based firewall container image
weblab-image/   PC image (nginx, curl, lynx)
weblab-vuln-image/ deliberately vulnerable PC image (for the scan-and-fix demo)
wan-image/      NAT gateway image (real internet uplink through the firewalls)
scripts/        setup, healthcheck, deployment helpers
experiments/    reproducible experiment runner (thesis evaluation)
docs/           demo walkthrough and security hardening notes
```

## Experiments

The thesis evaluation runs headless via the experiment runner:

```bash
python -m app.experiments run experiments/<spec>.yaml
```

Each spec defines a model, scenario, and task sequence; runs produce per-turn metrics (satisfiability, safety, latency) with means and confidence intervals over k repetitions.
