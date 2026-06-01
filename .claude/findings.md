# Demo-2 Implementation — Findings

## advisor tool — corrected diagnosis (2026-05-31 #2)
Earlier this session it LOOKED like calling `advisor()` killed the agent. Root-cause review of the
transcript shows the opposite: **`advisor()` was never actually emitted.** Each "advisor" turn ended
with prose like "Now the advisor consult…" and NO tool call — i.e. I announced the call and then
yielded the turn, forcing Amir to re-prompt. That announce-then-yield loop is what burned ~60% of the
session, not the advisor tool. **Lesson: the tool is fine; the bug was behavioral — actually EMIT the
tool call instead of narrating it.** Caveat: advisor forwards the ENTIRE conversation, so on a very
long session the call is slow/large — prefer calling it EARLY (small context) rather than late. (This
correction was confirmed by actually running advisor at the end of session #2.)

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

---

## Phase 2c part 2 — implementation notes (2026-06-01, committed `0a2db33`)
Shipped the PTY-over-WebSocket terminal. Two deliberate deviations from the design above, both
improvements verified against the live lab:

1. **Output via a single `asyncio.Queue` + one writer task** (not `create_task(ws.send_text)` per read).
   The per-read create_task approach fires overlapping `ws.send_text` coroutines under bursty output
   (e.g. `cat bigfile`), and starlette/uvicorn don't guard a single ws against concurrent sends →
   interleaved frames / "concurrent call to send" errors. `on_readable` now does `put_nowait`; a lone
   `pump_output` task drains the queue and awaits sends sequentially. Same transport (real PTY +
   `loop.add_reader`, no thread), just ordered/serialised egress.

2. **`preexec_fn` (setsid + `ioctl(0, TIOCSCTTY)`) instead of `start_new_session=True`.** Resize was a
   no-op with start_new_session: `docker exec` forwards window size via SIGWINCH on ITS controlling tty,
   but a bare setsid leaves the child with no controlling tty. Making the slave (fd 0/1/2) the
   controlling terminal makes `TIOCSWINSZ` on master deliver SIGWINCH → docker resizes the container tty.
   Confirmed: `stty size` on fw returns the resized `40 120`. Also set a sane initial winsize on master
   before Popen.

Verification: `/tmp/ws_verify.py` (recreate from this if /tmp cleared) — websockets 16.0 client, drives
pc1 + fw, asserts the 6 gate points. ALL PASS; no orphaned `docker exec` after teardown (`pgrep` clean).

## Phase 2c part 3 — frontend `/console` (2026-06-01, committed `44bb5a5`)
- Deps added: `react-router-dom @xterm/xterm @xterm/addon-fit`.
- `main.tsx`: BrowserRouter, `/`=App (untouched), `/console`=ConsolePage, floating pill toggle (fixed +
  pointer-events-none wrapper so App's h-screen layout is unchanged).
- `ConsoleTerminal.tsx`: xterm + FitAddon over the ws. onData→`{type:input}`, ResizeObserver→fit+
  `{type:resize}`, onmessage→term.write. Keyed by node so switching nodes tears down the old ws/PTY.
- `ConsolePage.tsx`: full-screen reused TopologyPane (own health poll + GET /rules), `onNodeClick`→460px
  slide-in panel. fw also gets `FirewallPanel` (live DROP list + add-rule form → `api.addRule`→POST /rules).
- `api.ts`: `addRule(src,dst,proto="icmp")` + `wsConsoleUrl(scenario,node)`; new `ws://localhost:8000` base.
- Verified: tsc+vite build clean (188 modules), dev server serves `/` and `/console`. NOT browser-clicked
  (no browser automation here) — Amir's one manual check: open :5173/console, click pc1 (shell, `ip -6 addr`
  empty, `ping pc2`), click fw (add rule via form, confirm it shows in the `/` topology view).
- Stack left running for that check: uvicorn :8000 + fresh central-hub lab + `vite` dev :5173 (if the dev
  bg task survives the session; else `cd gui && npm run dev`).

## Phase 3b — real PC services + port-aware firewall (2026-06-01, `42a527f` + `16cbcdf`)
Amir's call: **real images, not simulated-in-alpine** ("I want realism").
- **Hard constraint discovered:** every node MUST have a shell + `ip` (the console `docker exec` shell + L3
  config both need them). So **scratch images are out** — `traefik/whoami` (the plan's pc2) has neither.
  nginx:alpine and postgres:16-alpine ARE alpine-based (busybox sh + ip), so they work. pc2 = 2nd nginx.
- **Model:** Node gained `idle` (default True = `sleep infinity` keepalive; set False when the image runs a
  service as PID 1), `env` (→ clab node env), `launch` (shell cmd run detached after L3 — generalizes the old
  :8080 nc listener, now unused by central-hub), `ports` (declared listening set; feeds port-aware UI/scan).
- **Pulls:** `nginx:alpine`, `postgres:16-alpine` pulled (needed internet once; now cached). postgres needs
  `POSTGRES_HOST_AUTH_METHOD=trust` or it exits; it listens on *:5432. `docker exec` runs as root on both
  (no USER set), so L3 config + console work. nginx official CMD is `daemon off;` so it stays PID 1.
- **Port-aware:** fw-api already builds `port port="X" protocol="tcp"` rich-rules and parse_rich_rule reads
  `port`, so it was pure app plumbing: optional `port` through FirewalldDriver.block → SecurityEngine.block →
  block_traffic tool + POST /rules + console form (port field shown for tcp/udp). **allow() NOT touched** — it
  clears every drop for the src/dst pair, rebuilding each delete (incl. its port) from the parsed rule.
- **Verify recipe (deterministic, no LLM):** `wget` pc2→pc1 (nginx title), `nc -w3 pc1→pc3 5432`, `pg_isready`
  on pc3; POST /rules tcp:80 pc2→pc1 ⇒ HTTP blocked, `ping` still 0% loss, pg still reachable; then
  `FirewalldDriver(fw_mgmt).allow(pc2_ip, pc1_ip)` ⇒ drops `[]`, HTTP restored. fw mgmt ip via
  `docker inspect clab-central-hub-fw --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'`.
- **NOT done:** 3a (scenario dropdown), 3c (multi-image dockerscan), dnsmasq/UDP. Frontend topology.ts still
  hardcodes central-hub nodes; it does not yet display the per-node service/ports (cosmetic; do in 3a).

## LESSON: `pkill -f "uvicorn app.main"` kills its OWN shell (exit 144)
`pkill -f <pat>` matches full command lines — the shell running the pkill has `<pat>` in its own argv,
so pkill SIGTERMs its parent shell before the rest of the `;`-chain runs (looks like "exit 144, no
output", and uvicorn never actually restarts). This is the real cause of the "144" noise, separate from
pkill's normal non-zero-exit-on-no-match. **Fix: bracket-trick the pattern so it can't match the pkill
command line itself:** `pkill -f "[u]vicorn app.main"`. Verified it stops uvicorn cleanly and the chain
continues. (Restart still uses `;` not `&&`, and never pipe server stdout through `head`.)
