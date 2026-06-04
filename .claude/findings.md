# Demo-2 Implementation — Findings

## 2026-06-04 #2 — Root-cause confirmation of the 3 testing-pass bugs (systematic-debugging, before any fix)

Deployed both scenarios fresh via the backend (`POST /lab/start`, so `configure_nodes` runs). Evidence:

### Bug #1 (IPv6 "still enabled") — NODE IPv6 IS ALREADY DEAD; only the mgmt network still declares IPv6
- `ip -6 addr` is **empty on all 13 nodes** of `two-subnet-ixp` AND all 5 of `central-hub`.
- Race test: an `alpine` container on the `clab` net with ONLY the creation-time `disable_ipv6`
  sysctls (no runtime flush) gets eth0 = IPv4-only, `GlobalIPv6=invalid IP`. So the
  topology-generator sysctls **alone** already prevent mgmt eth0 IPv6 — the runtime flush is redundant.
- The ONLY residual IPv6: the containerlab **default mgmt network** `clab` is created with
  `EnableIPv6=true`, subnet `3fff:172:20:20::/64`. The generated `*.clab.yml` emits **no `mgmt:` block**,
  so containerlab uses its IPv6-enabled default. No container uses the v6 subnet (sysctls kill it).
- ⇒ Amir's "ping by name over IPv6" does NOT reproduce in current code. The topology-generator-level
  hardening that matches his standing rule = add a `mgmt:` block that disables IPv6 on the network,
  removing the latent `3fff:` subnet entirely.

### Bug #2 (block bypassed in console) — CONFIRMED: management-network bypass, NOT IPv6
- Container names resolve via Docker embedded DNS (127.0.0.11) / clab `/etc/hosts` to the **mgmt IPv4**
  (`172.20.20.x`). containerlab only knows mgmt IPs; data-plane IPs are added post-deploy by netconfig,
  so nothing maps a name → data IP.
- The mgmt bridge connects every container directly and **never traverses the firewall**.
- Proof on `central-hub` with DROP pc1→pc2 active:
  - `ping 10.99.20.10` (data IP, crosses fw) → **100% loss (BLOCKED)** ✓
  - `ping pc2` (→ mgmt 172.20.20.6) → **0% loss (BYPASS)**, 0.05ms (direct bridge)
- ⇒ Killing IPv6 will NOT fix this — by-name would just use mgmt IPv4 and still bypass. The note's
  "disabling IPv6 is enough for #2" is WRONG. Real fix is a design call (make names resolve to
  data-plane IPs so console-by-name traverses the fw, vs. accept console verification uses data IPs).
- Note: the `ping_test` TOOL already pings the data IP (`_node_ip_map`), so the LLM/tool path is correct
  and was verified working; only a human typing `ping <name>` in the console PTY hits the bypass.

### Bug #3 (chat wiped on console↔chat nav) — CONFIRMED: frontend unmount, no rehydrate
- `App.tsx` renders `<ChatView>` only when `view==="chat"`; switching tabs **unmounts** ChatView→ChatPane.
- `ChatPane` holds `messages` in local `useState([])`; remount re-inits to empty, no rehydrate on mount.
- Navigation never calls `/chat/reset` (only the Clear button + lab Reset do). Backend
  `_conversation_history` survives. No GET-history endpoint exists.
- Fix options: (a) lift `messages` into `App` (survives tab nav, no backend change), or
  (b) add `GET /chat/history` + rehydrate on mount (also survives a page reload; needs tool_calls/metrics
  shape mapping).

---


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

## Phase A spike facts (2026-06-01 #2) — verified, reusable for Phase C
- **L2 switch = Alpine + `ip link add br0 type bridge` + `ip link set ethN master br0`.** Forwards
  across containerlab point-to-point veth (confirmed pcL→pcR 0% loss through the bridge). The
  switch keeps a real `/bin/sh` for the console PTY. **No IP on the switch's data ports** (pure L2).
- **IPv6 on bridged nodes:** the creation-time sysctls + `netconfig.disable_ipv6()` (runs FIRST in
  `configure_nodes`, setting `all.disable_ipv6=1` before any iface comes up) keep `ip -6 addr`
  empty. In a topology that skips the post-deploy flush a link-local can slip onto a brought-up
  eth — so for the switch branch, do bridge setup AFTER disable_ipv6 (which the loop already does).
- **Two `firewalld-fw:latest` coexist fine** (fresh containers; the dbus crash-loop is only about
  RESTARTING a long-lived one). Both answer `firewall-cmd --state` + `:8080` in ~1s, fully isolated.

## fw-api edge (not a bug to fix now): `allow(proto="tcp")` with no port 500s
`FirewalldDriver.allow(src,dst,"tcp")` removes drops by pair fine, then tries to ADD an ACCEPT with
a bare `protocol value="tcp"` → fw-api 500. **The real re-enable path never hits this**: the app/3b
recipe calls `allow(src,dst)` (default icmp), and allow() clears EVERY drop for the pair regardless
of proto — so a tcp:80 drop is cleared by an icmp-proto allow. Don't "fix" by passing proto on allow.

## Phase B — multi-firewall engine (2026-06-01 #2)
- `SecurityEngine._fws: dict[fw_id, FirewalldDriver]`. `_resolve(fw_id)`: explicit id → that fw;
  None + exactly one fw → that fw (central-hub unchanged); None + several → raise (caller must
  resolve first). `list_rules(None)` aggregates + tags each parsed rule `firewall`; `flush(None)`
  flushes ALL. `chat.resolve_firewall(src_ip, scenario)` = fw whose iface subnet contains src_ip,
  else first fw — deterministic, the model never chooses (LLM tool schema left unchanged on purpose).
- Call sites updated: `_deploy_and_connect` loops every fw node; `POST /rules`+`RuleRequest.firewall`;
  `dispatch_tool` block/allow; `_describe_active_drops` adds `[on <fw>]` ONLY when >1 fw connected
  (single-fw prompt stays byte-identical). `/health` gains `firewalls: [...]`. New `GET /scenarios`.

## Phase C — switches + multi-subnet routing + two-subnet-ixp (2026-06-01 #2)
- **Model:** `Interface.ip: str|None` (switch ports L2-only); `Route{to:cidr, via:ip}`; `Node.routes`;
  `Node.ip_required_off_switch` validator (non-switch ifaces MUST have ip). None-IP would crash
  `_node_ip_map`/`describe_state`/`_describe_active_drops` — all three now guard `if iface.ip`.
- **topology `_to_containerlab`:** `role in (firewall, router)` → `ip_forward=1`; sleep-infinity for
  every non-firewall idle node (so routers/switches stay alive, fw runs firewalld as PID 1).
- **netconfig switch branch:** `ip link add br0 type bridge; set br0 up; for ethN in data ifaces:
  set master br0 + up; continue` (no L3/routes/launch). Other roles apply `node.routes` via
  `ip route replace <to> via <via>` after iface config. Runs AFTER disable_ipv6 → bridged ports
  stay IPv6-free (verified `ip -6 addr` empty on all 13 incl. switches).
- **two-subnet-ixp addressing (locked):** LAN-A 10.10.1.0/24 (pc*a .11/.12/.13, fwa .1), A-transit
  10.10.255.0/30 (fwa .1, routera .2), IXP 100.64.0.0/24 (routera .1, routerb .2, via `ixp` switch),
  B-transit 10.20.255.0/30, LAN-B 10.20.1.0/24. routerA routes: 10.10.1.0/24→fwa, 10.20.0.0/16→
  routerb; routerB mirror. fw default via its router. **Block lands at the SOURCE subnet's firewall**
  (resolve_firewall by src subnet): block pc1a→pc1b ⇒ DROP on fwa.
- **Verified live:** cross-subnet ping/HTTP/postgres all work through the IXP; multi-fw block/allow
  surgical + correct. 0 netconfig warnings on a clean deploy.

## Phase D — data-driven frontend (2026-06-01 #2)
- **`gui/src/topology.ts` is now a builder, not constants.** `buildTopology(graph)` → `{deviceNodes,
  cloudNodes, edges, ipToNodeId, firewallIds, wireIdForHop, pathWires, pathNodes}`. Layout = `@dagrejs/dagre`
  rankdir LR (both scenarios are trees → clean). Edge handles chosen post-layout from relative x/y.
- **Subnet clouds = non-interactive background RF nodes** (`type:"cloud"`, zIndex 0, selectable/draggable
  false, `pointer-events-none`), sized to the bbox of their members + padding. A boundary device (fw/router)
  is a member of multiple subnets → sits in the overlap of multiple clouds (reads correctly). One cloud per
  subnet with ≥2 members — currently includes transit /30s + central-hub's per-pc /24s (literal to Amir's
  request; tweak the `ids.length < 2` guard or add a "≥2 non-boundary members" rule if too busy).
- **Switch membership:** a switch has no IP, so it joins the subnet of its IP-bearing neighbours (look at
  each neighbour's interface facing the switch).
- **`TopologyPane` takes a `topology` prop now** (App/ConsolePage build it). Drop chips render under the fw
  whose id == `rule.firewall` (multi-fw). Ping animation: BFS `pathWires`; if blocked, highlight up to the
  wire reaching the first firewall on the path + stop-marker (at edge midpoint now, not the fw-end).
- **Scenario plumbing:** `GET /scenarios/{name}` (full `model_dump`) + `scenario` in `/health`. `App` has the
  dropdown (disabled while a lab runs; follows `health.scenario` after reload); `ConsolePage` follows the LIVE
  `health.scenario` (the console acts on the running lab) and its fw panel targets the clicked firewall.
- **Verify without a browser:** `tsc`+`vite build` clean; ran `buildTopology` via `npx tsx` against both live
  graphs (counts/ids/paths/clouds all correct). The visual itself (cloud aesthetics, dagre layout, animation
  playback) needs Amir's browser pass — no browser tooling in this env (same as the 2c console).

## Phase D review fixes (2026-06-01 #2, commit bf89ba9)
Amir's review of the first Phase-D cut → three fixes:
- **Console blank canvas:** `ConsolePage` built topology only from the live `health.scenario`, so with no
  lab running it had nothing to draw. Fix = lifted ALL shared state into `App` as one shell; `ChatView`
  + `ConsoleView` are prop-driven bodies sharing the same topology built from the SELECTED scenario →
  console shows a preview pre-lab (live shells gated on `labReady` with a hint). `pages/ConsolePage.tsx`
  removed; `PingEvent` moved to `api.ts`.
- **Nav integration:** floating pill → a real top **tab bar** (Chat | Console) in the unified header with
  the scenario dropdown + lab controls + status. `main.tsx` no longer uses react-router (state-based view;
  react-router-dom is now an unused dep — fine to leave or prune later).
- **Cloud = subnet not device:** raised the cloud threshold to `MIN_CLOUD_MEMBERS = 3` in `topology.ts`.
  central-hub (each PC alone on its own /24 + the shared gateway) → 0 clouds; two-subnet-ixp → LAN-A,
  LAN-B, IXP only (transit /30s excluded). NOTE for Amir: central-hub PCs are genuinely on 3 separate
  /24s, so there's no single shared subnet to wrap — that's why it shows no cloud, not a bug.

## LESSON: `pkill -f "uvicorn app.main"` kills its OWN shell (exit 144)
`pkill -f <pat>` matches full command lines — the shell running the pkill has `<pat>` in its own argv,
so pkill SIGTERMs its parent shell before the rest of the `;`-chain runs (looks like "exit 144, no
output", and uvicorn never actually restarts). This is the real cause of the "144" noise, separate from
pkill's normal non-zero-exit-on-no-match. **Fix: bracket-trick the pattern so it can't match the pkill
command line itself:** `pkill -f "[u]vicorn app.main"`. Verified it stops uvicorn cleanly and the chain
continues. (Restart still uses `;` not `&&`, and never pipe server stdout through `head`.)

### REFINEMENT (2026-06-01 #2): the bracket-trick FAILS if the restart shares the line
`pkill -f "[u]vicorn app.main" ; sleep 1 ; (.venv/bin/uvicorn app.main:app ...)` still exited 144 and
killed the backend without restarting it. Why: the bracket trick only stops the pattern matching the
*pkill arg* — but the SAME shell command line also contains the restart's literal `uvicorn app.main:app`,
so `pkill -f` matches the parent shell's own cmdline (it holds the whole line) and SIGTERMs it before the
restart runs. **Fix: run the kill and the restart as SEPARATE Bash calls** (different shell processes).
Worked first try once split: call 1 = `pkill -f "[u]vicorn app.main"`; call 2 = the `(uvicorn … &); curl`.

## Phase 3c — multi-image dockerscan (2026-06-02, `962980f`)
- **The scan target is the IMAGE, not the node** (scanner.py docstring). So multi-scan dedupes by image: in
  central-hub 5 nodes = 4 unique images (nginx×2, postgres, alpine, firewalld-fw); in two-subnet-ixp **13 nodes = only
  4 unique images** (nginx×4, postgres×2, alpine×5, firewalld-fw×2). `"scan all"` of the 13-node lab is 4 scans, not 13.
- **`SecurityEngine.scan_images(images)`** dedupes (order-preserving) → `run_image_scan` each unique image →
  `{"images_scanned", "scans":[per-image summary], "by_severity_total"}`. `by_severity_total` SUMS only scans without
  an `"error"` key (run_image_scan returns `{"error":...}`, never raises — one unpullable image must not break the batch).
  `scan(image)` (single) kept for back-compat.
- **`vulnerability_scan` tool**: schema stays a single `target` STRING on purpose — llama3.1:8b won't reliably emit a
  JSON array (the standing prompt-nudges-don't-hold rule). Backend parses: `"all"/"network"/"everything"/"*"` → every
  node; else `re.split(r"[,\s]+")` → node ids. Resolves to unique images recording `nodes` per image; returns the
  scan_images result + `targets`. Unknown/empty target → error, no scan run.
- **Browser path**: `gui/src/api.ts sendChat` is a plain `fetch` with **no AbortController/timeout** → a single ~2min
  scan is NOT artificially aborted (browser network idle timeout ~300s applies). So single/few-node scan survives the
  full `/chat` round-trip. `"scan all"` ≈ 4×2min sequential → can exceed the browser idle timeout; functional-but-slow,
  not the realistic demo invocation. No frontend change, no REST endpoint — scan is still LLM-only via the chat tool.
- Tests (recreate from these if /tmp cleared): `/tmp/test_3c_logic.py` (7 stubbed dispatch cases, instant) +
  `/tmp/test_3c_real.py` (real dockerscan, pc1+pc2 → one nginx scan; ~2min; no lab needed — scan reads the local image) +
  `/tmp/test_3c_llm.py` (the LLM-emission check — Ollama IS in-env, so this path is testable here unlike the browser passes).
- **LLM-emission CONFIRMED (2026-06-02, llama3.1:8b):** the new behavior was the model emitting a parseable `target`, not the
  dispatch logic. Drove `call_ollama` (real system prompt, no lab) with 3 phrasings: "scan on **all** nodes" → `target="all"`;
  "scan **pc1 and pc2**" → `target="pc1, pc2"`; "audit the **whole network**" → `target="all"`. All three parse cleanly to
  node ids → the string-not-array schema choice holds against the actual model. So 3c is verified at LLM + dispatch + scanner.

### Backend state desync if you tear a lab down OUTSIDE the backend
`containerlab destroy` (any teardown not via `POST /lab/stop`) leaves the backend's in-memory
`_active_scenario` + security connections stale → `/health` keeps reporting `lab_active:true` with the
old scenario/firewalls though no containers exist. Fix = restart uvicorn, OR always stop via
`POST /lab/stop/{scenario}`. (Hit during Phase D verification.)
