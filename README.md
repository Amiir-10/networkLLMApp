# networkLLMApp

LLM-driven simulated network for the bachelor's thesis. A FastAPI backend orchestrates a containerlab topology (every device = one Linux container); a chat interface lets an LLM drive the firewall via typed tool-calls.

Linux-only (kernel namespaces + bridges). Tested on Ubuntu 24.04.

## Status

Day 1 scaffold.

## Run

```
.venv/bin/uvicorn app.main:app --reload
```

Then `curl http://localhost:8000/health`.
