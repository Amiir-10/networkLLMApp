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
