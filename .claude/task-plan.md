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

## Phase 3 — Nice-to-haves (NEXT, not started)
- 3a. Topology dropdown: `GET /scenarios` enumerates scenarios/*.yaml; `<select>` bound to a scenario state var that Start/Reset/Stop/console key off. Generalizes the hardcoded-central-hub console.
- 3b. Real PC services: per-node service/cmd/ports in scenario YAML → netconfig.launch_service() after L3 (pc1=nginx:alpine:80, pc2=traefik/whoami:80, pc3=postgres:16-alpine:5432, + dnsmasq:53). Port-aware block/allow. **Any new image still gets IPv6 disabled automatically.**
- 3c. Multi-image dockerscan (low priority): security.scan accept list/"all", iterate, aggregate.

## Write-back — DONE 2026-05-31 (session #1)
- [x] session-log.md (## 2026-05-31 #1), README (Brain Dump + status), decisions-log (impl entry), Demo-2 plan status header

## Session summary (2026-05-31)
Shipped Phase 1 (engine refactor) + 2a (IPv6) + 2b (Reset). 4 commits: 2f2a877, 623073f, 9a19088, b1fd7c7.
NEXT SESSION: Phase 2c console/debug page — react-router /console + PTY-over-WebSocket (docker SDK exec_run
socket=True, fallback pty.openpty+subprocess) + xterm.js per node + fw rule view/add panel calling
security.block/list (SAME engine method as block_traffic). Then Phase 3 (scenarios dropdown, real PC services,
multi-scan). Engine layer (app/engines/security.py, topology.py) is the seam the console plugs into.
Stack left running: uvicorn :8000 + fresh central-hub lab (IPv6-off, 0 drops).
