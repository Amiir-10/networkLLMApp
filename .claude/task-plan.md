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
- [ ] 2a. Disable IPv6 at topology-generator level (sysctls on every node) + netconfig.disable_ipv6 belt-and-suspenders; verify `ip -6 addr` empty on every node; commit
- [ ] 2b. Reset button (destroy+redeploy + clear history) — if time
- [ ] 2c. Console/debug page (react-router + PTY WebSocket + xterm.js + fw rule panel) — likely next session

## Write-back
- [ ] Update vault README + session-log + decisions-log before session ends
