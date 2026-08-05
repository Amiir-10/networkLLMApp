# Environment Manifest — networkLLMApp

Versions of the KNOWN-WORKING environment, captured 2026-08-05 on Amir's PC.
A fresh install (local or vast.ai) should match these or stay as close as
possible. `scripts/setup.sh` installs against this manifest; `scripts/healthcheck.sh`
verifies the result.

## Runtime

| Component | Version | Notes |
|---|---|---|
| Python | 3.12.3 | venv at `.venv`, created from system python3.12 |
| pip deps | see `requirements.lock.txt` | full transitive freeze; `prompt-replay` is a git-SHA dep (needs git + github.com reach at install time) |
| Docker | 29.4.2 | daemon required (lab containers + image builds) |
| containerlab | 0.75.0 | local install: `/home/amir/.local/bin/containerlab` (non-root needs passwordless sudoers for that exact path); root installs (vast VM) land on PATH — `CONTAINERLAB_BIN` env overrides |
| Ollama | 0.22.1 | serves on :11434; only needed on whichever host runs inference |

## Docker images (lab)

| Image | Source |
|---|---|
| `firewalld-fw:latest` | built from `firewall-image/` (setup.sh builds it) |
| `weblab:latest` | built from `weblab-image/` (setup.sh builds it) |
| `alpine:3.20` | Docker Hub |
| `postgres:16-alpine` | Docker Hub |
| base images | `debian:bookworm-slim` (firewall), `nginx:alpine` (weblab) — pulled implicitly by the builds |

## Ollama models

| Tag | Disk | Where it runs |
|---|---|---|
| `llama3.1:8b` | ~4.9 GB | CPU-capable (baseline) |
| `qwen2.5-coder:7b` | ~4.7 GB | CPU-capable |
| `qwen2.5-coder:32b` | ~20 GB | GPU (24 GB+ VRAM) |
| `llama3.1:70b` | ~43 GB | GPU (48 GB-class VRAM) |

## Not needed for experiments

- **GUI (`gui/`, Node v22.22.2, Vite dev server :5173)** — experiments are fully
  headless (runner → backend HTTP → docker exec probes). Never install Node on
  a rented instance.
- `dockerscan` — demo-only vulnerability scan tool, unused by the runner.

## Env overrides (added 2026-08-05)

| Var | Default | Purpose |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434/api/chat` | backend → Ollama (full /api/chat URL) |
| `OLLAMA_URL_BASE` | `http://localhost:11434` | run-experiment.sh health check base URL |
| `BACKEND` | `http://localhost:8000` | run-experiment.sh backend base URL |
| `CONTAINERLAB_BIN` | `which containerlab` → `/home/amir/.local/bin/containerlab` | clab binary path; sudo auto-skipped when root |
