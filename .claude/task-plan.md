# Demo-2 Implementation — Task Plan

Source plan: vault `01_Projects/Bachelors-Project/Notes/Demo-2-Feature-Plan-2026-05-30.md`
Session scope (2026-05-31): **Phase 1 (refactor + gate + commit) + start Phase 2** (IPv6 first).
Gate execution: I bring up the lab + backend + frontend and drive the gate via the REST/chat API.

## Status legend: [ ] todo · [~] in progress · [x] done · [!] blocked

## Phase 0 — Prep
- [x] Read Demo-2 plan + core source files
- [x] Confirm baseline state: Day-7/7.5 work was uncommitted on top of `c9b9d9a day-6`
- [x] Commit baseline → `2f2a877 day-7.5: ...` (clean reference for the gate)
- [x] Set up .claude/task-plan.md + findings.md

## Phase 1 — Engine refactor (behavior-preserving) [GATE before features] — DONE
- [x] Create `app/engines/{__init__,topology,security,netconfig}.py`
- [x] topology.py: TopologyEngine owns containerlab lifecycle (start/stop/exec/state/get_mgmt_ip + _to_containerlab)
- [x] netconfig.py: configure_nodes + launch_pc_listener + docker_exec helpers extracted verbatim; disable_ipv6 no-op stub
- [x] security.py: SecurityEngine wrapping FirewalldDriver + run_image_scan → block/allow/list_rules/flush/health/scan + connect/disconnect lifecycle
- [x] Re-point chat.py dispatch + main.py routes at engines; deleted app/lab/driver.py; NO behaviour change
- [x] GATE (lab up, driven via API + direct dispatch):
  - [x] (a) DEMO.md: block router→pc1 → 100% loss → "allow it again" → 0% loss → 0 drops (LLM path)
  - [x] (b) FRAGILE (deterministic dispatch): block two pairs → flush → 0 drops
  - [x] (c) single-pair block → allow → 0 drops; direction-swap allow → 0 drops; ping scoping (pc1 blocked, pc3 ok) + recovery
  - [x] (d) vuln scan on fw → 7 findings, no error
  - All identical to pre-refactor (commit `2f2a877`)
- [x] Commit Phase 1 → (committing now)

## Phase 2 — Must-haves [start this session]
- [x] 2a. Disable IPv6: sysctls on every node in _to_containerlab + netconfig.disable_ipv6 belt-and-suspenders.
      BEFORE: every node had global 3fff:172:20:20::x + link-local. AFTER fresh redeploy: `ip -6 addr` empty on ALL 5 nodes,
      ip_forward still 1 on fw, IPv4 block/allow/ping regression PASS. Commit next.
- [x] 2b. Reset button. Backend POST /lab/reset/{scenario} = best-effort destroy + clear history + fresh redeploy
      (NOT restart); shared _deploy_and_connect helper (lab_start behaviour unchanged). Frontend: amber Reset button +
      confirm + ChatPane remount-key to clear visible chat. VERIFIED: seeded DROP + fw container 2631286b... → reset →
      new fw 12936b03..., drops cleared, ip -6 empty on all 5, fw reconnected; frontend tsc exit 0.
- [~] 2c. Console/debug page (react-router + PTY WebSocket + xterm.js + fw rule panel) — IN PROGRESS (session 2026-05-31 #2)

## Phase 2c — Console/debug page (session 2026-05-31 #2)
Decisions (Amir, this session):
- Transport = **pty.openpty() + `docker exec -it`** (zero new Python deps; real kernel PTY; "actually a real console"). NOT the docker SDK.
- UI = functional & clean (current minimal Tailwind look).
- Commit cadence = **incremental: backend commit (verified) → frontend commit (verified)** — guards against usage running out mid-way.

### Backend
- [x] **commit 1 = `5f7da08`** `POST /rules` in main.py: body {src,dst,proto=icmp}; guards lab+fw connected; validate_node_args; resolve via chat._node_ip_map; calls `security.block(...)` (SAME method as block_traffic — the single-surface payoff). VERIFIED by deterministic in-process parity test (`/tmp/parity_test.py`, run with `PYTHONPATH=/home/amir/thesis/networkLLMApp .venv/bin/python /tmp/parity_test.py`): form-added DROP byte-identical to LLM-added DROP (same rich-rule string + parsed fields), proto omitted == icmp, fw left clean. PARITY TEST PASS.
- [x] **commit 1b = `0a2db33`** `GET /ws/console/{scenario}/{node}` (async) — PTY terminal. Implemented per the findings design with TWO deviations (both improvements, see findings "Phase 2c part 2 — implementation notes"):
      (1) output goes through a single `asyncio.Queue` drained by one writer task instead of `create_task(ws.send_text)` per read — guarantees ordered, non-overlapping sends (concurrent send_text on one ws is unsafe under bursty output);
      (2) `start_new_session=True` replaced by `preexec_fn` doing `setsid()` + `ioctl(0, TIOCSCTTY)` — makes the slave the child's controlling TTY so `docker exec` forwards SIGWINCH and **resize actually propagates into the container** (it was a no-op without this). Plus a sane initial TIOCSWINSZ before Popen.
- [x] requirements.txt unchanged (pty/termios/fcntl/struct/subprocess all stdlib).
- [x] VERIFIED via `/tmp/ws_verify.py` (websockets 16.0 in .venv) against live central-hub: pc1 real shell, `ip -6 addr` empty, ping pc2 0% loss; fw `firewall-cmd --state` running, `stty size`=40 120 (resize honored); unknown node closes 4404; no orphaned `docker exec` after close. ALL PASS. Committed 1b.

### Frontend (commit 2 = `44bb5a5`) — DONE
- [x] deps: react-router-dom @xterm/xterm @xterm/addon-fit.
- [x] main.tsx: BrowserRouter; routes `/`=App (unchanged behaviour), `/console`=ConsolePage; floating pill toggle (fixed + pointer-events-none wrapper → no layout shift to App).
- [x] TopologyPane: added optional `onNodeClick` prop (non-breaking) → ReactFlow onNodeClick.
- [x] pages/ConsolePage.tsx: full-screen TopologyPane (reuse, no chat); own health poll + rules fetch; click node → slide-in 460px panel.
- [x] components/ConsoleTerminal.tsx: xterm + FitAddon + ws; onData→input frame, ResizeObserver→fit+resize frame, onmessage→term.write; remount-per-node (React key) for a fresh PTY; status dot.
- [x] fw panel (ConsolePage FirewallPanel): live DROP list + add-rule form (api.addRule → POST /rules); refetch after add. `/` view mirrors (both read /rules).
- [x] api.ts: addRule(src,dst,proto) + wsConsoleUrl(scenario,node).
- [x] VERIFIED: tsc + vite build clean (188 modules); dev server serves `/` and `/console` (SPA fallback 200). ws protocol = the one proven live via /tmp/ws_verify.py against pc1/fw; form path = the already-parity-tested POST /rules → GET /rules mirror. **In-browser click-through NOT run (no browser automation in this env)** — that is the one open manual check for Amir. Committed 2.

NOTE (Phase-3 carry): console reuses central-hub-specific TopologyPane (hardcoded node positions). Generalizing clickable nodes from arbitrary scenarios is Phase 3 (scenario dropdown) — acceptable for 2c single-scenario.

## Phase 2c — COMPLETE (2026-06-01, session #3)
All three parts shipped + committed: `042317e` POST /rules, `0a2db33` ws PTY terminal, `44bb5a5` /console frontend.
Backend fully verified live; frontend verified by build + dev-server + proven API/ws layers. Only open item: a
human in-browser click-through of /console (no browser tooling here).

## Phase 3 — Nice-to-haves
- [ ] 3a. Topology dropdown: `GET /scenarios` enumerates scenarios/*.yaml; `<select>` bound to a scenario state var that Start/Reset/Stop/console key off. Generalizes the hardcoded-central-hub console. NOT STARTED.
- [x] **3b. Real PC services — DONE (2026-06-01, session #3), commits `42a527f` + `16cbcdf`.** Amir chose **real images** over simulated-in-alpine. Node model gained `idle`/`env`/`launch`/`ports`; `_to_containerlab` omits `sleep infinity` when `idle:false` so an image's service runs as PID 1; `env` passed to the clab node; the old hardcoded :8080 listener generalized to `netconfig.launch_service` (YAML `launch`). central-hub: pc1/pc2=`nginx:alpine` (:80), pc3=`postgres:16-alpine` (:5432, `POSTGRES_HOST_AUTH_METHOD=trust`). **Plan deviation:** `traefik/whoami` (the plan's pc2) is a scratch image → no shell/`ip` → breaks the console + L3, so pc2 is a 2nd nginx (also sets up the port-block demo). dnsmasq/UDP deferred (no base-alpine dnsmasq; offline apk risk). **Port-aware block:** threaded an optional `port` through `FirewalldDriver.block`→`SecurityEngine.block`→`block_traffic` tool + `POST /rules` + the console form (fw-api already built port rich-rules; `allow()` untouched — clears by pair incl. port). **Verified live:** ip-6 empty on the new images; pc2→pc1 nginx + pc1→pc3:5432 routed through fw; block tcp:80 pc2→pc1 ⇒ HTTP blocked while ICMP+postgres survive; allow clears it; core ICMP demo intact.
- [x] **3c. Multi-image dockerscan — DONE (2026-06-02, `962980f`).** `vulnerability_scan` `target` now accepts a single
      node id, a comma/space-separated list, or `"all"`. Tool schema stays a single STRING (llama3.1:8b won't reliably
      emit a JSON array); backend expands it deterministically into node ids (same rule as `resolve_firewall`). Targets
      resolve to UNIQUE images, scanned once each (pc1/pc2 both nginx:alpine → 1 scan, both nodes under `nodes`).
      New `SecurityEngine.scan_images()` dedupes + aggregates `by_severity_total` across SUCCESSFUL scans only (an
      errored/unpullable image is in its own entry but skipped in the total). `scan(image)` kept. **Verified:** 7
      deterministic dispatch cases (`/tmp/test_3c_logic.py`) + a REAL dockerscan e2e (`/tmp/test_3c_real.py`: pc1+pc2 →
      one nginx:alpine scan, 6 findings, total correct). Browser note: `api.ts sendChat` has NO client timeout, so a
      single ~2min scan survives the chat path; `"scan all"` (~4×2min) risks the browser idle timeout → functional but
      slow, realistic demo invocation is 1–3 named nodes. No frontend / no new REST endpoint — scan stays LLM-only.

## Write-back — DONE 2026-05-31 (session #1)
- [x] session-log.md (## 2026-05-31 #1), README (Brain Dump + status), decisions-log (impl entry), Demo-2 plan status header

## Session 2026-06-01 #2 — 3a + 2nd scenario (two-subnet-ixp) + visual overhaul
Plan: `~/.claude/plans/jiggly-marinating-tide.md`. Amir's choices: fully-real end-to-end 2nd
scenario (two mirror subnets, each `3 PCs → L2 switch → internal fw → router`, routers peering
through a real IXP shared subnet), real Alpine L2 switches, TWO live firewalls (rules target one,
resolved deterministically in backend), subnet "cloud" visual for all scenarios.

- [x] **Phase A — de-risk spike (throwaway, no commit).** PASS. (1) Alpine `ip link … master br0`
      forwards L2 across containerlab veth (0% loss), switch keeps `/bin/sh`, and the standard
      `disable_ipv6()` flush clears the stray link-local (in the real pipeline disable_ipv6 runs
      BEFORE ifaces come up, so it never appears). (2) Two `firewalld-fw:latest` containers come up
      in ~1s, both answer `:8080` independently, a DROP on one is invisible on the other.
- [x] **Phase B — multi-firewall security engine + GET /scenarios** (behavior-preserving).
      `SecurityEngine` now keys FirewalldDriver by fw id (`_fws: dict`); every rule method takes
      optional `fw_id` defaulting to the sole firewall (central-hub unchanged); `list_rules(None)`
      aggregates across firewalls tagging each parsed rule with `firewall`; `flush(None)` flushes all.
      `chat.resolve_firewall(src_ip, scenario)` picks the enforcing fw by source subnet (NEVER the
      model). `_deploy_and_connect` connects EVERY fw node. `POST /rules` + `RuleRequest.firewall`
      + dispatch use the resolution. `/health` adds `firewalls`. New `GET /scenarios`.
      LLM tool schema deliberately UNCHANGED (model-driven targeting is the droppable tail).
      GATE: `/tmp/gate_b.py` deployed central-hub, drove block/allow/two-pair-flush/port-block
      deterministically (no LLM) — all PASS, drops tag `fw`, describe text byte-identical (no
      `[on fw]` for a single fw). Vuln scan skipped (scanner untouched). Lab torn down clean.
- [x] **Phase C — switches + multi-subnet routing + `scenarios/two-subnet-ixp.yaml`.**
      Model: `Interface.ip` now optional (switch L2 ports), `Route{to,via}`, `Node.routes`,
      validator `ip_required_off_switch` (only switches may be IP-less). `_to_containerlab`: routers
      get `ip_forward=1` (+ kept-alive); switches = idle alpine bridge. `configure_nodes`: switch
      branch builds `br0` + enslaves all data eth's (no L3), other roles apply `node.routes` after
      ifaces. None-IP guards added to `_node_ip_map`, `describe_state`, `_describe_active_drops`.
      `scenarios/two-subnet-ixp.yaml`: 13 nodes, 2 mirror subnets (LAN 10.10.1/10.20.1.0/24) each
      `3 PCs → switch → fw → router`, routers peer over IXP `100.64.0.0/24` (an `ixp` switch node).
      **VERIFIED on the live lab:** 0 netconfig warnings; `ip -6 addr` empty on all 13; all 3
      switches bridging; cross-subnet ping pc1a→pc1b 0% loss (full 7-hop IXP path); cross-subnet
      nginx ("Welcome to nginx!") + postgres ("accepting connections"); both fw running; shells into
      switcha/ixp; **multi-fw targeting**: block pc1a→pc1b lands tagged `fwa` → ping 100% loss while
      pc2a→pc1b survives → allow clears it → ping restored. Committed.
- [x] **Phase D — data-driven frontend + subnet clouds + scenario dropdown + multi-fw display. DONE
      (build + logic verified; in-browser visual is Amir's check).**
      Backend: `GET /scenarios/{name}` (full graph) + `scenario` added to `/health`. Frontend rewritten
      data-driven: new `gui/src/topology.ts` `buildTopology(graph)` — derives RF device nodes (dagre
      LR auto-layout), **subnet "cloud" background nodes** (one per subnet ≥2 members; boundary
      fw/router naturally overlap two clouds), wire edges (handles picked from post-layout direction),
      ip→node map, firewallIds, and **BFS `pathWires`/`pathNodes`** for the ping animation. `@dagrejs/dagre`
      added. `TopologyPane` now takes a `topology` prop, renders clouds + a switch icon, filters drop
      chips per-firewall (`rule.firewall`), and animates the BFS path (stop-marker at the wire reaching
      the first firewall when blocked). `App.tsx`: scenario `<select>` (from `GET /scenarios`, disabled
      while a lab runs), builds topology from the selected scenario, follows `health.scenario` after a
      reload. `ConsolePage.tsx`: follows the LIVE `health.scenario`, per-firewall rule panel that targets
      the clicked firewall (`addRule(...,firewall)`). `api.ts`: `fetchScenarios`/`fetchScenario`,
      `ParsedRule.firewall`, `HealthResponse.scenario`, `addRule` firewall arg.
      **VERIFIED:** `tsc --noEmit` clean; `vite build` clean (189 modules); dev server serves `/`+`/console`;
      `buildTopology` unit-tested vs BOTH live graphs — central-hub (5 nodes/4 edges/[fw]/pc1→fw→pc2/4
      clouds) and two-subnet-ixp (13/12/[fwa,fwb]/full 9-node IXP path/LAN-A+LAN-B+IXP clouds), all nodes
      positioned + clouds sized. **NOTE:** layout/visual intentionally CHANGED (the overhaul); ping
      animation BEHAVIOUR preserved (same wires light). Every subnet (incl. transit /30s + central-hub's
      per-pc subnets) currently gets a cloud — literal to Amir's "all devices in a subnet in a cloud";
      a 1-line threshold tweak if it reads busy. **Open: Amir's in-browser pass** (no browser tooling here).
      3c (multi-image dockerscan) still deferred.
- [x] **Phase D review fixes** (`bf89ba9`, `b0f2c14`): integrated Chat/Console **tab bar** (state lifted to an
      `App` shell; console previews topology pre-lab — fixes the blank canvas); **cloud = subnet not device**
      (threshold ≥3 members → central-hub 0 clouds, two-subnet-ixp = LAN-A/LAN-B/IXP); **named cloud labels**
      `LAN · <cidr>` / `IXP · <cidr>`. Real two-fw route verified over HTTP; central-hub fw gate re-confirmed
      byte-identical post-Phase-C. ONLY open = Amir's first in-browser render pass. See
      vault [[Demo-2-Phase-D-Handoff-2026-06-01]].

## Session summary (2026-05-31)
Shipped Phase 1 (engine refactor) + 2a (IPv6) + 2b (Reset). 4 commits: 2f2a877, 623073f, 9a19088, b1fd7c7.
NEXT SESSION: Phase 2c console/debug page — react-router /console + PTY-over-WebSocket (docker SDK exec_run
socket=True, fallback pty.openpty+subprocess) + xterm.js per node + fw rule view/add panel calling
security.block/list (SAME engine method as block_traffic). Then Phase 3 (scenarios dropdown, real PC services,
multi-scan). Engine layer (app/engines/security.py, topology.py) is the seam the console plugs into.
Stack left running: uvicorn :8000 + fresh central-hub lab (IPv6-off, 0 drops).
