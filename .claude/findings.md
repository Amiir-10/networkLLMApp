# Demo-2 Implementation — Findings

## ⚠️ SESSION-ENVIRONMENT BUG — DO NOT CALL THE `advisor` TOOL (2026-05-31 #2)
In THIS environment, every `advisor()` call kills the running agent instance — the turn ends and
Amir has to re-prompt to continue. It burned >60% of the session's usage across repeated restarts
before we caught it. **Next session: do NOT call advisor. Do the design reasoning inline.** The one
genuine design fork advisor was for (the PTY/WebSocket transport) is already fully resolved and
written below — no consult needed. If a new hard fork appears, reason it out in the response, or ask
Amir directly via the question tool; never call advisor here.

---


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

---

## Phase 2c — session 2026-05-31 #2 progress + design

### DONE this session (committed `5f7da08`)
- `POST /rules` endpoint in main.py. Verified byte-identical to LLM block_traffic via parity test.
  Reuses `chat._node_ip_map` (imported into main.py) + `validate_node_args` + `security.block`.
- Parity test script saved at `/tmp/parity_test.py` (ephemeral /tmp — recreate from the commit body
  if needed; run: `PYTHONPATH=/home/amir/thesis/networkLLMApp .venv/bin/python /tmp/parity_test.py`).

### Verified container facts (for the terminal)
- fw (`firewalld-fw:latest`): has `/usr/bin/bash` AND `/usr/bin/sh`. Use **`/usr/bin/bash`**.
- pc1/pc2/pc3/router (`alpine:3.20`): only `/bin/sh` (busybox), NO bash. Use **`/bin/sh`**.
- So shell selection: `"/usr/bin/bash" if node.role == "firewall" else "/bin/sh"`.

### GOTCHA: running backend is stale
- The uvicorn `:8000` process was started ~4h before this session, so it does NOT have `POST /rules`
  (or the upcoming ws route) loaded. The code is proven via in-process TestClient, not via the live
  process. **Next session: restart the backend** so the new routes load — but the lab is long-lived,
  so just restart uvicorn, do NOT touch the lab. Restart pattern (NOTE the `;` not `&&` after pkill):
  `pkill -f "uvicorn app.main" ; sleep 1 ; cd /home/amir/thesis/networkLLMApp && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/uvicorn.log 2>&1 &`
  (background it; NEVER pipe its stdout through head). Then `curl -s :8000/health`.
  If lab itself is stale/broken: destroy+redeploy fresh (sudo -n /home/amir/.local/bin/containerlab), never restart it.

### WebSocket PTY terminal — FULL DESIGN (ready to implement, NO advisor needed)
Decision (Amir): transport = `pty.openpty()` + `docker exec -it` (real kernel PTY, "actually a real
console"; zero new deps — pty/termios/fcntl/struct/os/signal/subprocess all stdlib). NOT the docker SDK.

Why this is correct & safe for uvicorn's event loop:
- `pty.openpty()` returns (master_fd, slave_fd) — a real PTY pair. `docker exec -it` with the slave as
  stdin/stdout/stderr gives the container a genuine TTY (line editing, colors, `clear`, vim all work).
- The master_fd is a plain OS fd → set it non-blocking (`os.set_blocking(master_fd, False)`) and register
  `loop.add_reader(master_fd, on_readable)`. This is the IDIOMATIC async-without-threads bridge: the
  event loop wakes us when the PTY has output; we `os.read` and `await ws.send_bytes/text`. NO
  run_in_executor, NO thread, NO blocking the loop. (This is cleaner than the docker SDK's hijacked
  socket, which is why we switched off the plan's original "primary".)

Endpoint sketch (`@app.websocket("/ws/console/{scenario_name}/{node}")`, async def):
1. `await ws.accept()`.
2. Validate: `_active_scenario` set and `node` ∈ its node ids; else `await ws.close(code=4404)`.
   (scenario_name should match _active_scenario.name; if not, close 4404.)
3. `shell = "/usr/bin/bash" if role=="firewall" else "/bin/sh"`; `container = f"clab-{scenario_name}-{node}"`.
4. `master, slave = pty.openpty()`.
5. `proc = subprocess.Popen(["docker","exec","-it",container,shell], stdin=slave, stdout=slave,
   stderr=slave, start_new_session=True)`. Then `os.close(slave)` (parent keeps master only).
6. `os.set_blocking(master, False)`.
7. Output pump: `loop = asyncio.get_running_loop()`; define `on_readable()` that does
   `data = os.read(master, 65536)`; if empty/OSError → schedule close; else
   `asyncio.create_task(ws.send_bytes(data))` (or decode+send_text). Register `loop.add_reader(master, on_readable)`.
   - Simplicity option that also works: instead of add_reader, an `async` reader task doing
     `await loop.run_in_executor(None, os.read, master, 65536)` in a loop. add_reader is preferred (no
     executor thread); pick add_reader.
8. Input loop: `while True: msg = await ws.receive()` — handle text (JSON). Protocol (frontend xterm):
   - `{"type":"input","data":"..."}` → `os.write(master, data.encode())`.
   - `{"type":"resize","cols":C,"rows":R}` → `fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH",R,C,0,0))`.
   (Or simpler: send raw keystrokes as ws text = input, and a separate small JSON only for resize.
   Cleanest is the tagged-frame protocol above; xterm onData→input frame, onResize→resize frame.)
9. Teardown (finally / on WebSocketDisconnect / on read-EOF): `loop.remove_reader(master)`;
   `proc.terminate()` (then kill after grace); `os.close(master)`. Wrap everything so one disconnect
   never leaks a PTY or a docker exec process.

Frontend ws URL: `ws://localhost:8000/ws/console/${scenario}/${node}` (api.ts helper `wsConsoleUrl`).
CORS note: WebSocket isn't governed by the CORSMiddleware allow_origins; ws from :5173 → :8000 is fine.

Verify ws (commit 1b gate), driven by a tiny python `websockets` client OR just by the frontend xterm:
- connect to pc1 → send `ip -6 addr\n` → output shows NO inet6 (proves 2a end-to-end in a real shell).
- send `ping -c2 10.99.20.10\n` (pc2) → `0% packet loss`.
- connect to fw → `firewall-cmd --state\n` → `running`.
- resize → `stty size` reflects new rows/cols.
- close tab → backend log shows clean teardown, no orphaned `docker exec` (`docker exec` count stable).

### Frontend (commit 2) — unchanged from task-plan
react-router-dom + @xterm/xterm + @xterm/addon-fit; main.tsx BrowserRouter; `/`=App (verbatim current
behaviour), `/console`=ConsolePage (full-screen reused TopologyPane + click→panel: terminal for any node,
rules list + add-rule form for fw). TopologyPane gets an OPTIONAL `onNodeClick` prop (non-breaking).
api.ts: `addRule(src,dst,proto)` (POST /rules) + `wsConsoleUrl(scenario,node)`. After add-rule, refetch
GET /rules; the `/` view mirrors automatically (same source). Then VERIFY + commit 2.

Phase-3 carry: TopologyPane hardcodes central-hub node positions (gui/src/topology.ts INITIAL_NODES).
Console reuses it → single-scenario only for 2c. Generalizing to arbitrary scenarios = Phase 3 (dropdown). OK for now.

---

## Phase 2c — session 2026-05-31 #2 progress + design

### DONE this session (committed `5f7da08`)
- `POST /rules` endpoint in main.py. Verified byte-identical to LLM block_traffic via parity test.
  Reuses `chat._node_ip_map` (imported into main.py) + `validate_node_args` + `security.block`.
- Parity test script saved at `/tmp/parity_test.py` (ephemeral /tmp — recreate from this design if
  needed; run: `PYTHONPATH=/home/amir/thesis/networkLLMApp .venv/bin/python /tmp/parity_test.py`).

### Verified container facts (for the terminal)
- fw (`firewalld-fw:latest`): has `/usr/bin/bash` AND `/usr/bin/sh`. Use **`/usr/bin/bash`**.
- pc1/pc2/pc3/router (`alpine:3.20`): only `/bin/sh` (busybox), NO bash. Use **`/bin/sh`**.
- Shell selection: `"/usr/bin/bash" if node.role == "firewall" else "/bin/sh"`.

### GOTCHA: running backend is stale
- The uvicorn `:8000` process was started ~4h before this session, so it does NOT have `POST /rules`
  (or the upcoming ws route) loaded. Code is proven via in-process TestClient, not the live process.
- NEXT SESSION: restart uvicorn so new routes load. Lab is long-lived — restart ONLY uvicorn, do NOT
  touch the lab. Pattern (NOTE `;` not `&&` after pkill — pkill exit 144 breaks &&):
  `pkill -f "uvicorn app.main" ; sleep 1 ; (cd /home/amir/thesis/networkLLMApp && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/uvicorn.log 2>&1 &)`
  then `sleep 2; curl -s :8000/health`. NEVER pipe its stdout through head.
  If the lab itself is stale/broken: destroy+redeploy fresh (sudo -n /home/amir/.local/bin/containerlab), never restart it.

### WebSocket PTY terminal — FULL DESIGN (ready to implement, NO advisor needed)
Transport (Amir's pick) = `pty.openpty()` + `docker exec -it` (real kernel PTY = "actually a real
console"; zero new deps — pty/termios/fcntl/struct/os/subprocess all stdlib). NOT the docker SDK.

Why it's correct & safe for uvicorn's loop:
- pty.openpty() -> (master_fd, slave_fd), a real PTY pair. `docker exec -it` with slave as stdio gives
  the container a genuine TTY (line edit, colors, clear, vim all work).
- master_fd is a plain OS fd -> set non-blocking (os.set_blocking(master,False)) and register
  loop.add_reader(master, on_readable). Idiomatic async-without-threads: loop wakes us on output,
  we os.read + await ws.send. NO run_in_executor, NO thread, loop never blocks. (Cleaner than the
  docker SDK hijacked socket, which is why we dropped the plan's original "primary".)

Endpoint (`@app.websocket("/ws/console/{scenario_name}/{node}")`, async def):
1. await ws.accept()
2. Validate _active_scenario set and node in its ids (and scenario_name == _active_scenario.name);
   else await ws.close(code=4404).
3. shell = "/usr/bin/bash" if role=="firewall" else "/bin/sh"; container=f"clab-{scenario_name}-{node}"
4. master, slave = pty.openpty()
5. proc = subprocess.Popen(["docker","exec","-it",container,shell], stdin=slave, stdout=slave,
   stderr=slave, start_new_session=True); os.close(slave)
6. os.set_blocking(master, False)
7. Output: loop=asyncio.get_running_loop(); on_readable() does data=os.read(master,65536); empty/OSError
   -> schedule close; else asyncio.create_task(ws.send_text(data.decode(errors="replace"))).
   loop.add_reader(master, on_readable).
8. Input: while True: msg = await ws.receive_text(); parse JSON frame:
   {"type":"input","data":s} -> os.write(master, s.encode())
   {"type":"resize","cols":C,"rows":R} -> fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH",R,C,0,0))
9. Teardown (finally / WebSocketDisconnect / read-EOF): loop.remove_reader(master); proc.terminate()
   (kill after grace); os.close(master). One disconnect must never leak a PTY or docker exec proc.

Frontend ws URL: ws://localhost:8000/ws/console/${scenario}/${node}. WebSocket bypasses CORSMiddleware
allow_origins, so :5173 -> :8000 ws is fine.

Verify ws (commit 1b gate) via a tiny python `websockets` client (pip in .venv if missing) OR the xterm UI:
- pc1: send input frame `ip -6 addr\n` -> output has NO inet6 (proves 2a in a real shell).
- pc1: `ping -c2 10.99.20.10\n` -> 0% packet loss.
- fw: `firewall-cmd --state\n` -> running.
- resize frame -> `stty size\n` reflects new rows/cols.
- close -> backend log clean teardown, no orphaned docker exec (`docker ps`/exec count stable).

### Frontend (commit 2)
react-router-dom + @xterm/xterm + @xterm/addon-fit. main.tsx BrowserRouter; `/`=App (verbatim current
behaviour), `/console`=ConsolePage (full-screen reused TopologyPane + click->panel: terminal for any
node; rules list + add-rule form for fw). TopologyPane gets OPTIONAL `onNodeClick` prop (non-breaking).
api.ts: addRule(src,dst,proto) (POST /rules) + wsConsoleUrl(scenario,node). After add, refetch GET /rules;
`/` view mirrors automatically (same source). VERIFY (see handoff vault note) then commit 2.

Phase-3 carry: TopologyPane hardcodes central-hub node positions (gui/src/topology.ts INITIAL_NODES) ->
console is single-scenario for 2c. Generalizing to arbitrary scenarios = Phase 3 (dropdown). OK for now.
