# networkLLMApp

LLM-driven simulated network for the bachelor's thesis. A FastAPI backend orchestrates a containerlab topology (every device = one Linux container); the chat layer (Day 3+) will let an LLM drive the firewall via typed tool-calls.

Linux-only (kernel namespaces + bridges). Tested on Ubuntu 24.04 + kernel 6.17.

## Status

Day 2 of 5 done — `LabDriver` + first real routed scenario working end-to-end.

## What works today

```
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Endpoints:
- `GET  /health` → `{"status":"ok"}`
- `POST /lab/start/{scenario_name}` → deploys `scenarios/{name}.yaml` via containerlab; returns node list
- `POST /lab/stop/{scenario_name}` → tears down via `containerlab destroy --cleanup`
- `GET  /lab/state` → containerlab inspect JSON for all running labs

End-to-end demo (in a second terminal):

```
curl -X POST http://127.0.0.1:8000/lab/start/small-soc
docker exec clab-small-soc-pc1 ping -c 2 10.99.1.10   # cross-subnet via firewall
curl -X POST http://127.0.0.1:8000/lab/stop/small-soc
```

## Architecture

```
FastAPI endpoints  ─┐
                    ├──→  LabDriver (Protocol)  ──→  ContainerlabLabDriver  ──→  containerlab CLI
scenarios/*.yaml  ──┘                                                              │
                                                                                   └──→  Docker containers
```

The `LabDriver` Protocol means future Kathará / docker-compose / etc. drivers are drop-in: nothing in `app/main.py` cares which orchestrator is underneath.

## Repo layout

```
app/
├── main.py              # FastAPI app + 4 endpoints
└── lab/
    ├── __init__.py
    ├── models.py        # Pydantic: Scenario, Node, Interface
    └── driver.py        # LabDriver Protocol + ContainerlabLabDriver

scenarios/
└── small-soc.yaml       # 2 PCs + 1 firewall, routed topology

labs/                    # (gitignored) generated containerlab topology files
.venv/                   # (gitignored) Python venv
```

## Requirements (machine state)

- Docker (user in `docker` group)
- containerlab binary on `$PATH` (this repo assumes `/home/amir/.local/bin/containerlab`)
- `sudo -n containerlab` works without prompt (NOPASSWD sudoers rule scoped to that binary)
- Python 3.12 venv with `fastapi uvicorn[standard] httpx pydantic pyyaml`
