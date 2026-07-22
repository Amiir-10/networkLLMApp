<!-- BEGIN:fable5-thinking-principles -->
# Thinking Principles (left by Claude Fable 5, 2026-07-06)

This is Amir's bachelor's thesis repo (networkLLMApp: FastAPI backend + React/Vite GUI + containerlab lab + Ollama). If you are a non-Claude agent (Codex, etc.), read the full reasoning playbook before doing anything: `06_Metadata/AI-Context/thinking-playbook.md` in the vault (`/media/amir/SecondBrain/Brain/second-brain`). Claude sessions also load the compact tier from `~/.claude/thinking-principles.md` automatically.

The ten most load-bearing principles, inline:

1. Distrust descriptions of reality — buy the cheap direct observation (curl, screenshot, live query, the actually-sent/deployed artifact) before building on any claim.
2. A bug report's stated cause is a symptom description, not a diagnosis. Reproduce first; prefer the experiment that discriminates between hypotheses; drop a theory the moment evidence breaks it.
3. Verify at the layer the failure class lives in — a green typecheck/build proves nothing about runtime bugs. Load the real page, click with real input, make the real call.
4. "Worked once" is not "works" (survive N sequential uses); "verified" covers only the paths actually exercised.
5. A new constraint is a global change — audit every path it touches (every write path, every policy, every dereference), not just the one you are editing.
6. Operations with write side effects run only from the ONE action that owns them; passive surfaces get read-only counterparts.
7. To record that an operation happened, write an explicit marker — never infer it from the presence of data the operation may or may not have produced.
8. Checkpoint every paid/rate-limited loop before its first full run; resume idempotently.
9. Review feedback oscillating on one predicate = a product decision wearing a bug costume — take explicit options to Amir instead of flipping the code.
10. With Amir: ask clarifying questions before acting; never re-open decisions the logs mark settled; persist results to MD files IN PARALLEL with the work (his usage limit can kill a session mid-task); never commit or push without his explicit go-ahead in that session.
<!-- END:fable5-thinking-principles -->

## Repo-specific gotchas (hard-won; sources: `.claude/findings.md` + vault mistakes log)

- **`pkill -f "uvicorn app.main"` kills its OWN shell** (exit 144) — the pattern matches the shell's own cmdline. Bracket-trick it: `pkill -f "[u]vicorn app.main"`. And even bracket-tricked, the kill and the restart must be **separate Bash calls** — if the restart's literal `uvicorn app.main:app` shares the command line, pkill matches the parent shell anyway and the backend dies without restarting.
- **Never pipe a long-running server's stdout through `head -N`** — it silently breaks the server once the pipe closes (every chat request 500'd for a day until restarted without the filter). Redirect to a log file instead.
- **Backend restart drops the firewall connection.** Reconnect via `POST /lab/reset`, not lab stop/start. But don't anchor on this playbook: `GET /rules` returning 200 proves the connection is healthy — if so, the problem is elsewhere.
- **Node NAMES resolve to the management network, not the data plane.** `ping <name>` in a console uses the mgmt bridge (172.20.20.x) and bypasses the firewall entirely — a "block didn't work" report by-name is usually this. Real path tests use data-plane IPs; the LLM `ping_test` tool already does.
- **Temperature 0 for experiments.** Experiment specs (`experiments/*.yaml`) pass `options: {temperature: 0}` through `ChatRequest.options` → `call_ollama`. Never run a metric-producing experiment at default temperature.
- **Never restart a long-lived lab** — the fw container dbus crash-loops on restart (stale `/run/dbus/pid`). Destroy + redeploy fresh: `sudo -n /home/amir/.local/bin/containerlab`.
- **Tearing a lab down outside the backend desyncs `/health`** — it keeps reporting `lab_active:true` with stale firewalls. Always stop via `POST /lab/stop/{scenario}`, or restart uvicorn after an external destroy.
- **Browser "TypeError: NetworkError" = masked HTTP 500.** `curl -s :8000/chat` to see the real status before theorizing about CORS or connectivity.
- **Prompt nudges don't hold for local-LLM tool-calling** (llama3.1:8b violated an explicit "never use proto all" rule). Enforce with data injection + deterministic backend parsing, not system-prompt rules. Same reason `vulnerability_scan.target` is a string the backend parses, not a JSON array.
- **IPv6 disabled in ALL containers, always** (Amir's standing rule). Enforce at topology-generator level for any image; verify `ip -6 addr` is empty on every node.
- **Protocol-blocking demos need cross-site pairs** on `two-subnet-ixp` (A↔B), or the block never traverses a firewall and no-ops. Web PCs run `weblab:latest` (HTTP 80 + HTTPS 443 + socat UDP echo 9999) — build the image before deploy.
- **Do not alter `FirewalldDriver.allow()` logic**, and don't pass `proto` on allow — the default-icmp allow already clears every DROP for the pair regardless of proto; `allow(proto="tcp")` with no port 500s in fw-api.
