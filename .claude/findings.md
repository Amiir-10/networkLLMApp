# Demo-2 Implementation — Findings

## Repo facts (verified 2026-05-31)
- Repo: `/home/amir/thesis/networkLLMApp`, branch `main` (trunk-based: every day-N commit lands on main).
- Stack: FastAPI `app/` (uvicorn :8000), React+Vite `gui/` (:5173), containerlab Docker lab, Ollama :11434.
- Ollama tags present: `llama3.1:8b` (resolves ✓), `llama3.1:latest`, `qwen2.5-coder:7b`, `llama3.2:3b`. Demo default = `llama3.1:8b`.
- Lab control: `sudo -n /home/amir/.local/bin/containerlab`. Never restart a long-lived lab (fw dbus crash-loop) — destroy+redeploy.
- Container naming: `clab-<scenario>-<node>`. Active scenario: `central-hub` (5 nodes: fw, pc1/2/3, router).

## Baseline (pre-refactor reference)
- Commit `2f2a877 day-7.5` captures the exact behaviour the Phase-1 gate must reproduce.
- At session start: lab DOWN, backend DOWN, frontend DOWN, Ollama UP.

## Current architecture (what the refactor wraps)
- `app/lab/driver.py` — `ContainerlabLabDriver`: `_to_containerlab` (YAML gen), start/stop/state/exec/get_mgmt_ip,
  `_configure_node` (per-node ip addr/link/route + `_wait_for_firewalld` + PC `:8080` listener via PC_LISTENER_SCRIPT).
- `app/firewall/driver.py` — `FirewalldDriver` (block/allow/list_rules/flush/health) + `parse_rich_rule`.
  **FRAGILE**: `allow()` clears EVERY matching DROP for a src/dst pair in either direction, rebuilding each
  delete from the parsed rule. Do NOT alter this logic in the refactor.
- `app/scanner.py` — `run_image_scan(image)` (dockerscan; needs `~/.local/bin/dockerscan` + db).
- `app/chat.py` — TOOLS (7), `call_ollama`, `validate_node_args`, `dispatch_tool(name,args,scenario,firewall_driver,lab_driver)`.
- `app/main.py` — routes; holds module globals `lab_driver`, `_firewall_driver`, `_active_scenario`,
  `_conversation_history`, `_active_model`; `_describe_active_drops()` injects live DROPs into the prompt.
- `app/prompts.py` — `build_system_prompt(scenario, active_drops)`; active-drops as ground-truth DATA.

## Refactor design (behavior-preserving = delegation, not rewrite)
- `engines/topology.py`: TopologyEngine OWNS the containerlab lifecycle (move ContainerlabLabDriver here; keep code identical). `_configure_node` calls netconfig.
- `engines/netconfig.py`: `configure_node(...)`, `launch_service(...)` extracted verbatim from `_configure_node`; `disable_ipv6` added in Phase 2a (no-op in Phase 1).
- `engines/security.py`: SecurityEngine composes FirewalldDriver (kept in firewall/driver.py, logic untouched) + run_image_scan. `connect(mgmt_url)`/`disconnect()`; exposes block/allow/list_rules/flush/health/scan. Single CRUD surface for LLM + console.
- chat.py `dispatch_tool` → takes `security, topology` engines; `vulnerability_scan` calls `security.scan(image)`.
- main.py: replace `_firewall_driver` with `security` engine; `_describe_active_drops` uses `security.list_rules()`.

## Open risks
- The gate's fragile path (block two pairs → flush) is the regression-prone seam. Must verify, not assume.
- dockerscan binary may not be installed → vuln-scan gate step (d) could be a no-op/error; verify dockerscan presence first.

## Advisor execution guards (2026-05-31)
1. **netconfig extraction is the only non-mechanical seam.** `_docker_exec`/`_wait_for_firewalld`/`_docker_exec_detached`
   use ZERO instance state → lift to module-level functions in netconfig, import the same ones into topology.
   Then configure/launch are verbatim relocations, not reimplementations that can drift.
2. **Gate tests WIRING, not logic.** Driver logic untouched ⇒ only dispatch→engine→driver wiring can regress.
   Firewall mutation is LLM-only (no REST block/allow). So verify fragile cases (two-pair→flush, direction-swap→allow)
   by calling `dispatch_tool`/security engine DIRECTLY from Python against the live fw — deterministic. Keep /chat + DEMO.md
   as the integration story on top, NOT as the regression proof (model variance ≠ regression).
3. **import + /health BEFORE the lab deploy.** Start backend, hit /health (2s) to catch wiring/import errors before
   eating a ~30s sudo deploy.
4. **Preserve _firewall_driver lifecycle EXACTLY** in SecurityEngine.connect/disconnect: 30×1s health-retry in lab_start,
   disconnect→None on lab_stop, None-safe early returns in _describe_active_drops + GET /rules. This is where wiring drifts silently.
- Keep IPv6 OUT of Phase 1 diff (2a changes _to_containerlab output → lands after Phase 1 commit).
- dockerscan absent: baseline errored identically ⇒ "same as pre-refactor" still holds; don't block installing mid-gate.

## Post-implementation notes (2026-05-31)
- Gate fully closed: all 7 tools exercised post-refactor (block/allow/flush/list/ping/vuln + describe_state via /chat "describe the network" → all 5 nodes).
- Nuance on "lab_start behaviour unchanged" (2b): _active_scenario is now set AFTER _deploy_and_connect (was before the fw-connect loop). End state identical; only delta = a ~1-3s window where /health reports lab_active:false instead of true during fw connect. Immaterial (frontend gates on labReady = labActive && fwConnected, both false during connect). Noted for accuracy; not a real behaviour change.
