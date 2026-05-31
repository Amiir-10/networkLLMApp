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
- [ ] 2c. Console/debug page (react-router + PTY WebSocket + xterm.js + fw rule panel) — NEXT SESSION (large)

## Write-back — DONE 2026-05-31
- [x] session-log.md (## 2026-05-31 #1), README (Brain Dump + status), decisions-log (impl entry), Demo-2 plan status header

## Session summary (2026-05-31)
Shipped Phase 1 (engine refactor) + 2a (IPv6) + 2b (Reset). 4 commits: 2f2a877, 623073f, 9a19088, b1fd7c7.
NEXT SESSION: Phase 2c console/debug page — react-router /console + PTY-over-WebSocket (docker SDK exec_run
socket=True, fallback pty.openpty+subprocess) + xterm.js per node + fw rule view/add panel calling
security.block/list (SAME engine method as block_traffic). Then Phase 3 (scenarios dropdown, real PC services,
multi-scan). Engine layer (app/engines/security.py, topology.py) is the seam the console plugs into.
Stack left running: uvicorn :8000 + fresh central-hub lab (IPv6-off, 0 drops).
