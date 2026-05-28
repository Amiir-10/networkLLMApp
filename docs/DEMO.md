# Supervisor Demo — networkLLMApp (Phase 0, central-hub)

5-minute walkthrough for the 2026-05-30 supervisor sync. Built around the **central-hub** scenario: 3 PCs on three LAN subnets + an edge router on a WAN subnet, all centrally routed through a firewalld-managed firewall.

---

## Stack overview (30s — what to say)

> "The system is a FastAPI backend that drives a containerlab topology of Docker containers. The user types intent in plain language; the LLM (Ollama, llama3.1:8b by default) decides which firewall tool to call; the tool dispatches against a firewalld API running inside the firewall container; the resulting network effect is visible in the React UI and verifiable with raw `docker exec ping`."

Components, top-down:
- **React + Vite GUI** at `http://localhost:5173` — topology view (left) + chat (right).
- **FastAPI backend** at `http://127.0.0.1:8000` — `/chat`, `/lab/start`, `/lab/stop`, `/metrics`.
- **Ollama** at `http://localhost:11434` — local LLM, no cloud dependency.
- **containerlab** orchestrates Docker containers per scenario YAML.
- **firewalld container** exposes a thin REST API (`fw-api.py`) over its native rich-rule syntax.

---

## Pre-flight (do before the meeting, not in the meeting)

```bash
cd /home/amir/thesis/networkLLMApp
scripts/doctor.sh        # all green
```

Make sure both models are warm:
```bash
ollama run llama3.1:8b 'hi' --verbose 2>&1 | tail -1
ollama run qwen2.5-coder:7b 'hi' --verbose 2>&1 | tail -1
```
First call per model is ~30s (cold load); after this they stay in memory for ~5 min.

---

## Live demo flow

### 0. Cold start (30s)

Two terminals. Don't talk while they boot — explain the layout instead.

```bash
# Terminal 1
scripts/run-backend.sh    # uvicorn on :8000

# Terminal 2
scripts/run-frontend.sh   # vite on :5173, opens browser
```

### 1. Start the lab (1 min)

In the browser, click **"Start Lab"**. Takes ~25-30s — narrate while it deploys:

> "containerlab is bringing up five Docker containers — three PC nodes, one router, one firewalld instance — and wiring four point-to-point links. Once firewalld's API is reachable the backend connects and the indicator goes green."

When the topology renders, walk through the star:
- **fw (center)** — firewalld in the middle, all forwarding goes through it.
- **pc1 (top)** — LAN-A, `10.99.10.10/24`.
- **pc2 (right)** — LAN-B, `10.99.20.10/24`.
- **pc3 (bottom)** — LAN-C, `10.99.30.10/24`.
- **router (left)** — WAN-side, `203.0.113.2/24` (TEST-NET-3 makes it visually "external").

### 2. The headline workflow — block + verify + allow + verify (2 min)

Type in chat:

> **"block ping from router to pc1"**

What to point at:
- LLM responds in natural language: "I blocked ping traffic from the router to pc1..."
- Click the tool-call disclosure — show the resolved arguments (`src: router, dst: pc1, proto: icmp`) and the resulting rich rule.
- Router→pc1 edge fades to gray in the topology.

Open Terminal 3 and prove it:
```bash
docker exec clab-central-hub-router ping -c 2 -W 2 10.99.10.10
# 100% packet loss

docker exec clab-central-hub-router ping -c 2 10.99.30.10
# 0% loss — the block is correctly scoped to pc1
```

Back to chat:

> **"now allow it again"**

Multi-turn memory in action — the LLM resolves "it" from prior context. The driver deletes the matching DROP rule (Day 4 bug fix) then adds the ACCEPT. Edge returns to green.

Re-run the same ping to prove recovery:
```bash
docker exec clab-central-hub-router ping -c 2 10.99.10.10
# 0% loss
```

### 3. Topology introspection (1 min)

> **"describe the network"**

The `describe_state` tool returns a structured snapshot — the LLM walks through each node, its subnet, and active firewall rules. **The system prompt is built at request time with the actual scenario's node list injected**, so the LLM never hallucinates a non-existent node.

### 4. Metrics (30s)

In Terminal 3:
```bash
curl -s http://127.0.0.1:8000/metrics/models | python3 -m json.tool
```

Per-model summary: total interactions, mean LLM latency, prompt/completion token counts. Switch the model picker in the GUI from `llama3.1:8b` to `qwen2.5-coder:7b`, send the same prompt, point at the latency/token-count differences in the next `/metrics/models` call.

### 5. Shutdown (30s)

Click **"Stop Lab"** — containers destroy cleanly, indicators reset.

---

## Backup plan if the live demo glitches

A pre-recorded screencast of the full flow above lives at `docs/demo-screencast.mp4` (todo: record before the meeting). Switch to it without breaking flow if anything stalls > 30s.

---

## Talking points the supervisor may probe

- **"Why this topology?"** — Star = closest realistic shape to a small-office SOC where one firewall is the choke point. The 4th leg (router on TEST-NET-3) lets us demo "block traffic from the internet" without needing real public IPs.
- **"What if the user asks for something the LLM doesn't have a tool for?"** — The tool-use loop iterates up to 3× and ends with a prose response. Out-of-scope requests get a polite "I can only do block/allow/list/ping/describe right now."
- **"How do you stop the LLM hallucinating node names?"** — Two layers: (a) every tool call hits `validate_node_args` which checks against `scenario.nodes`; (b) the system prompt injects the current scenario's node list at request time (see `app/prompts.py:build_system_prompt`).
- **"What's next?"** — Phase 1 expands the topology to FRRouting (real routing daemon, OSPF/BGP), adds more intent classes (NAT, port forwarding, allow-only-from-source-port), and runs the NetConfEval benchmark to pick the v1 base model.

---

## Related
- [[How-To-Run]] — full step-by-step run guide
- [[Day-5-Handoff-2026-05-28]] — pre-session plan for the topology change
- [[Bachelors-Project]] — parent project
