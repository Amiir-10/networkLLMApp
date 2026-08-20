import asyncio
import fcntl
import json
import os
import pty
import re
import struct
import subprocess
import termios
import time
from pathlib import Path

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.engines.topology import TopologyEngine, LabAlreadyRunning, LabNotFound
from app.engines.security import SecurityEngine
from app.lab.models import Scenario
from app.chat import call_ollama, validate_node_args, dispatch_tool, MAX_TOOL_ITERATIONS, _node_ip_map, resolve_firewall, OLLAMA_URL, OLLAMA_NUM_CTX, salvage_content_tool_calls
from app.prompts import build_system_prompt
from app.metrics import MetricsCollector

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = REPO_ROOT / "scenarios"
LAB_WORK_DIR = REPO_ROOT / "labs"

app = FastAPI(title="networkLLMApp", version="0.0.5")
ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Starlette's ServerErrorMiddleware sits OUTSIDE CORSMiddleware, so a bare
    # 500 from an unhandled exception carries no CORS headers and the browser
    # masks the real error as an opaque "NetworkError". Attach the headers here
    # so the GUI can display the actual detail.
    headers = {}
    origin = request.headers.get("origin")
    if origin in ALLOWED_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"},
        headers=headers,
    )
topology = TopologyEngine(work_dir=LAB_WORK_DIR)
security = SecurityEngine()
metrics = MetricsCollector()

_active_scenario: Scenario | None = None
_active_model: str = "llama3.1:8b"
# Full-fidelity chat history: user turns, assistant turns WITH their
# tool_calls, and the tool result messages. Persisting only the prose (the
# old behaviour) showed models a conversation where actions apparently happen
# by assertion — qwen2.5 imitated that pattern within a few turns and stopped
# tool calling entirely, fabricating results in prose instead.
_conversation_history: list[dict] = []


def _trim_history() -> None:
    """Keep the rendered prompt safely under num_ctx. Ollama truncates an
    over-long prompt silently from the TOP — system prompt and tool schemas
    go first — so we drop the OLDEST whole user-turn groups ourselves instead.
    Rough estimate: ~3 chars/token; budget leaves room for system prompt,
    tool schemas, and the response."""
    budget_chars = int(OLLAMA_NUM_CTX * 0.7) * 3
    def total() -> int:
        return sum(len(json.dumps(m, default=str)) for m in _conversation_history)
    while _conversation_history and total() > budget_chars:
        cut = 1
        while cut < len(_conversation_history) and _conversation_history[cut].get("role") != "user":
            cut += 1
        print(f"[chat] history over budget — dropping oldest turn ({cut} message(s))", flush=True)
        del _conversation_history[:cut]


# Deterministic response-acceptance guards. Prompt instructions alone do not
# hold (verified live 2026-08-20: qwen2.5:14b drifted into Chinese and claimed
# an unexecuted block DESPITE explicit system-prompt rules against both). A
# rejected response is fed back with a corrective system message and re-asked,
# bounded by MAX_CORRECTIVE_RETRIES; rejected text never enters the shared
# history, so it cannot teach later turns the bad pattern.
MAX_CORRECTIVE_RETRIES = 2
_NON_ENGLISH_RE = re.compile(
    r"[一-鿿぀-ヿ가-힯฀-๿Ѐ-ӿ؀-ۿ]"
)
_ACTION_CLAIM_RE = re.compile(
    # completed-action claims: "I have blocked", "I ran", …
    r"\bI\s*(?:'ve|have|had)?\s*(?:now\s+|just\s+|successfully\s+|already\s+)*"
    r"(?:block|unblock|allow|restor|flush|remov|re-?enabl|appli|add|ran|execut|perform|scann)\w*"
    # passive completion claims: "traffic is now blocked", …
    r"|(?:has\s+been|have\s+been|is\s+now|are\s+now)\s+"
    r"(?:blocked|unblocked|allowed|restored|removed|applied|re-?enabled|flushed|dropped)"
    # announced-intent-then-yield: "I will add a rule", "Let's proceed with
    # blocking" followed by no tool call (observed live — future tense slips
    # past the completion patterns and nothing happens)
    r"|\b(?:I\s+will|I'll|let'?s|let\s+me|proceeding\s+to|going\s+to)\s+(?:now\s+)?"
    r"(?:proceed\s+(?:with|to)\s+)?"
    r"(?:block|unblock|allow|restor|flush|remov|re-?enabl|appli|add|run|test|scan)",
    re.I,
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "lab_active": _active_scenario is not None,
        "scenario": _active_scenario.name if _active_scenario else None,
        "firewall_connected": security.connected,
        "firewalls": security.firewall_ids(),
    }


def _load_scenario(scenario_name: str) -> Scenario:
    scenario_file = SCENARIO_DIR / f"{scenario_name}.yaml"
    if not scenario_file.exists():
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_name}' not found")
    raw = yaml.safe_load(scenario_file.read_text())
    return Scenario.model_validate(raw)


def _deploy_and_connect(scenario: Scenario, scenario_name: str) -> dict:
    """Deploy the lab and connect the security engine to the firewall.

    Shared by lab_start and lab_reset. May raise LabAlreadyRunning / RuntimeError
    from topology.start; callers translate those to HTTP errors.
    """
    result = topology.start(scenario)
    # Connect EVERY firewall node (central-hub has one; two-subnet-ixp has two).
    fw_health: dict[str, dict] = {}
    for fw_node in (n for n in scenario.nodes if n.role == "firewall"):
        mgmt_ip = topology.get_mgmt_ip(scenario_name, fw_node.id)
        if not mgmt_ip:
            result.setdefault("firewall_warnings", []).append(
                f"{fw_node.id}: could not resolve mgmt IP"
            )
            continue
        security.connect(fw_node.id, mgmt_url=f"http://{mgmt_ip}:8080")
        for _ in range(30):
            try:
                fw_health[fw_node.id] = security.health(fw_node.id)
                break
            except Exception:
                time.sleep(1)
        else:
            result.setdefault("firewall_warnings", []).append(
                f"{fw_node.id}: firewalld API did not become ready in 30s"
            )
    result["firewall"] = fw_health
    return result


@app.post("/lab/start/{scenario_name}")
def lab_start(scenario_name: str) -> dict:
    global _active_scenario, _conversation_history
    _conversation_history = []
    scenario = _load_scenario(scenario_name)
    try:
        result = _deploy_and_connect(scenario, scenario_name)
    except LabAlreadyRunning as exc:
        if _active_scenario is not None:
            # A lab this backend actually manages is up — a genuine
            # double-start, so the 409 is correct.
            raise HTTPException(status_code=409, detail=str(exc))
        # Containers exist but this backend knows of no live lab: stale
        # leftovers from a backend restart or a host reboot (docker's restart
        # policy resurrects them; the firewalls land in the dbus crash-loop).
        # Recover the same way run-experiment.sh does — destroy + redeploy,
        # never restart in place.
        try:
            topology.stop(scenario_name)
        except (LabNotFound, RuntimeError):
            pass
        security.disconnect()
        try:
            result = _deploy_and_connect(scenario, scenario_name)
        except LabAlreadyRunning as exc2:
            raise HTTPException(status_code=409, detail=str(exc2))
        except RuntimeError as exc2:
            raise HTTPException(status_code=500, detail=str(exc2))
        result["recovered_from_stale_lab"] = True
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    _active_scenario = scenario
    return result


@app.post("/lab/stop/{scenario_name}")
def lab_stop(scenario_name: str) -> dict:
    global _active_scenario, _conversation_history
    _conversation_history = []
    try:
        topology.stop(scenario_name)
    except LabNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    _active_scenario = None
    security.disconnect()
    return {"status": "stopped", "scenario": scenario_name}


@app.post("/lab/reset/{scenario_name}")
def lab_reset(scenario_name: str) -> dict:
    """Full clean slate: destroy + redeploy + clear all in-memory state.

    Deliberately a destroy+redeploy, NOT a container restart — restarting a
    long-lived lab triggers the firewalld dbus crash-loop. Teardown is
    best-effort so a reset still works from a half-broken lab; a genuinely
    still-running lab would surface as LabAlreadyRunning on the redeploy.
    """
    global _active_scenario, _conversation_history
    scenario = _load_scenario(scenario_name)

    # 1. Clear in-memory LLM state (Ollama is stateless per request).
    _conversation_history = []
    # 2. Best-effort destroy of whatever is currently up.
    try:
        topology.stop(scenario_name)
    except (LabNotFound, RuntimeError):
        pass
    security.disconnect()
    _active_scenario = None

    # 3. Redeploy fresh.
    try:
        result = _deploy_and_connect(scenario, scenario_name)
    except LabAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    _active_scenario = scenario
    result["reset"] = True
    return result


@app.get("/lab/state")
def lab_state() -> dict:
    return topology.state()


def _describe_active_drops() -> list[str]:
    """Live DROP rules as node-labelled strings, e.g. ['pc1 -> pc2 (icmp)'].

    Returns [] when a lab is up but no drops exist, so the prompt can say so.
    """
    if not security.connected or _active_scenario is None:
        return []
    ip_to_node = {
        iface.ip.split("/")[0]: n.id
        for n in _active_scenario.nodes
        for iface in n.interfaces
        if iface.ip
    }
    try:
        parsed = security.list_rules().get("parsed", [])
    except Exception:
        return []
    out: list[str] = []
    for r in parsed:
        if r.get("action") != "drop":
            continue
        src = ip_to_node.get(r.get("src_ip"), r.get("src_ip") or "any")
        dst = ip_to_node.get(r.get("dst_ip"), r.get("dst_ip") or "any")
        proto = r.get("proto") or "all"
        line = f"{src} -> {dst} ({proto})"
        # Label the enforcing firewall only when several exist, so a
        # single-firewall scenario's prompt text stays byte-identical.
        fw = r.get("firewall")
        if fw and len(security.firewall_ids()) > 1:
            line += f" [on {fw}]"
        out.append(line)
    return out


@app.get("/scenarios")
def list_scenarios() -> list[dict]:
    """Enumerate the scenarios/*.yaml the frontend dropdown can choose from.
    Reads name + description cheaply (no full deploy); central-hub sorts first."""
    out: list[dict] = []
    for f in sorted(SCENARIO_DIR.glob("*.yaml")):
        try:
            raw = yaml.safe_load(f.read_text()) or {}
            out.append({"name": raw.get("name", f.stem),
                        "description": raw.get("description", "")})
        except Exception:
            out.append({"name": f.stem, "description": ""})
    out.sort(key=lambda s: (s["name"] != "central-hub", s["name"]))
    return out


@app.get("/scenarios/{scenario_name}")
def get_scenario(scenario_name: str) -> dict:
    """Full node/interface graph for one scenario (the frontend derives the
    topology, subnets, link graph and layout from this — no YAML on the client).
    Reads the YAML; does NOT require the lab to be running."""
    return _load_scenario(scenario_name).model_dump()


@app.get("/rules")
def get_rules() -> dict:
    if not security.connected:
        return {"forward_rules": [], "zone_rules": [], "parsed": []}
    return security.list_rules()


@app.post("/rules/flush")
def flush_rules() -> dict:
    """Deterministic clean-slate for the rule set (no LLM in the loop).

    Same engine method the LLM's flush_rules tool dispatches to — the
    experiment runner uses this between repetitions so every rep starts from
    an identical firewall state without paying for a full lab redeploy.
    """
    if not security.connected:
        raise HTTPException(status_code=400, detail="Firewall driver not connected.")
    return {"status": "flushed", "result": security.flush()}


class RuleRequest(BaseModel):
    src: str
    dst: str
    proto: str = "icmp"
    port: int | None = None
    # Optional explicit target firewall (node id). When omitted, the backend
    # resolves it deterministically from the source's subnet (resolve_firewall).
    firewall: str | None = None


@app.post("/rules")
def add_rule(req: RuleRequest) -> dict:
    """Add a firewall DROP rule from the console form.

    This is the single-surface payoff of the engine refactor: it resolves node
    IDs to IPs and calls `security.block(...)` — the EXACT same engine method the
    LLM's `block_traffic` tool dispatches to (app.chat.dispatch_tool) — using the
    same node validation, the same `_node_ip_map`, and the same `proto` default
    ("icmp"). So a rule added via the form and a rule added via the LLM for the
    same intent are byte-identical, and both mirror into GET /rules (the one
    source the `/` topology view reads).
    """
    if not _active_scenario:
        raise HTTPException(status_code=400, detail="No lab is running. Start a lab first.")
    if not security.connected:
        raise HTTPException(status_code=400, detail="Firewall driver not connected.")

    validation_err = validate_node_args({"src": req.src, "dst": req.dst}, _active_scenario)
    if validation_err:
        raise HTTPException(status_code=400, detail=validation_err)

    ip_map = _node_ip_map(_active_scenario)
    src_ip = ip_map.get(req.src)
    dst_ip = ip_map.get(req.dst)
    fw_id = req.firewall or resolve_firewall(src_ip, _active_scenario)
    result = security.block(src_ip, dst_ip, req.proto, req.port, fw_id=fw_id)
    return {"status": "added", "src": req.src, "dst": req.dst, "proto": req.proto,
            "port": req.port, "firewall": fw_id, "result": result}


@app.websocket("/ws/console/{scenario_name}/{node}")
async def console_ws(websocket: WebSocket, scenario_name: str, node: str) -> None:
    """A real PTY shell into a lab container, bridged over a WebSocket.

    Transport = a genuine kernel PTY (`pty.openpty()`) wrapped around
    `docker exec -it <container> <shell>`, NOT a simulation: line editing,
    colours, `clear`, `vim`, `ping`, `firewall-cmd` all work for real. Output
    is pumped async-without-threads — `loop.add_reader(master, …)` wakes us on
    container output, we enqueue it, and a single writer task drains the queue
    so sends stay ordered and never overlap on the one socket. Protocol is JSON
    frames: {"type":"input","data":str} and {"type":"resize","cols":int,"rows":int}.
    """
    await websocket.accept()

    scenario = _active_scenario
    if scenario is None or scenario.name != scenario_name:
        await websocket.close(code=4404)
        return
    node_obj = next((n for n in scenario.nodes if n.id == node), None)
    if node_obj is None:
        await websocket.close(code=4404)
        return

    # fw has /usr/bin/bash; alpine PCs/router are busybox (sh only).
    shell = "/usr/bin/bash" if node_obj.role == "firewall" else "/bin/sh"
    container = f"clab-{scenario_name}-{node}"

    master, slave = pty.openpty()
    # Sane initial window size; the frontend sends a resize frame on connect.
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))

    def _child_setup() -> None:
        # New session + make the slave (now fd 0/1/2) the controlling TTY, so
        # the `docker exec` client receives SIGWINCH on TIOCSWINSZ and forwards
        # the resize into the container's TTY. Without this, resize is a no-op.
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen(
        ["docker", "exec", "-it", container, shell],
        stdin=slave, stdout=slave, stderr=slave,
        preexec_fn=_child_setup,
    )
    os.close(slave)
    os.set_blocking(master, False)

    loop = asyncio.get_running_loop()
    out_queue: asyncio.Queue = asyncio.Queue()

    def on_readable() -> None:
        try:
            data = os.read(master, 65536)
        except OSError:
            data = b""
        if data:
            out_queue.put_nowait(data)
        else:
            # EOF: the container shell exited / the pty closed.
            loop.remove_reader(master)
            out_queue.put_nowait(None)

    loop.add_reader(master, on_readable)

    async def pump_output() -> None:
        try:
            while True:
                data = await out_queue.get()
                if data is None:
                    break
                await websocket.send_text(data.decode(errors="replace"))
        finally:
            # Unblock the input loop's receive_text() when the shell ended.
            try:
                await websocket.close()
            except RuntimeError:
                pass

    writer = asyncio.create_task(pump_output())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ftype = frame.get("type")
            if ftype == "input":
                try:
                    os.write(master, frame.get("data", "").encode())
                except OSError:
                    break
            elif ftype == "resize":
                try:
                    cols = int(frame.get("cols", 80))
                    rows = int(frame.get("rows", 24))
                    fcntl.ioctl(
                        master, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0),
                    )
                except (OSError, ValueError):
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        try:
            loop.remove_reader(master)
        except (ValueError, OSError):
            pass
        out_queue.put_nowait(None)
        writer.cancel()
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        try:
            os.close(master)
        except OSError:
            pass


class ChatRequest(BaseModel):
    message: str
    model: str = "llama3.1:8b"
    # Ollama sampling options (temperature, seed, ...) — used by the experiment
    # runner to fix temperature across repetitions. None = Ollama defaults,
    # so GUI behavior is unchanged.
    options: dict | None = None


@app.post("/chat/reset")
def chat_reset() -> dict:
    global _conversation_history
    _conversation_history = []
    return {"status": "cleared"}


@app.post("/chat")
def chat(req: ChatRequest) -> dict:
    global _active_model
    if not _active_scenario:
        raise HTTPException(status_code=400, detail="No lab is running. Start a lab first.")
    if not security.connected:
        raise HTTPException(status_code=400, detail="Firewall driver not connected.")

    _active_model = req.model
    _trim_history()
    turn_start = len(_conversation_history)
    _conversation_history.append({"role": "user", "content": req.message})

    # Inject the live DROP rules into the system prompt as ground-truth data so
    # the model re-enables the exact pair/proto the user blocked (it otherwise
    # hallucinates the wrong src node or downgrades proto to "all" on re-enable).
    active_drops = _describe_active_drops()
    messages = [
        {"role": "system", "content": build_system_prompt(_active_scenario, active_drops)}
    ] + _conversation_history

    all_tool_results: list[dict] = []
    first_llm_at: float | None = None
    prompt_sent_at = time.time()

    # Two separate budgets: tool rounds (unchanged semantics — bounds how many
    # execute-and-reprompt cycles a turn may take) and corrective retries (the
    # acceptance guard below; rejected responses don't consume tool rounds).
    iteration = 0
    tool_rounds = 0
    corrective_retries = 0
    while True:
        print(
            f"[chat] iter={iteration} sending {len(messages)} messages; "
            f"last role={messages[-1].get('role')!r}",
            flush=True,
        )
        if iteration > 0:
            print(
                f"[chat] iter={iteration} tail messages: "
                f"{json.dumps(messages[-3:], default=str)[:1500]}",
                flush=True,
            )

        interaction = metrics.start_interaction(req.model, req.message)

        try:
            ollama_resp = call_ollama(req.model, messages, req.options)
        except Exception as e:
            # Roll back the ENTIRE in-flight turn (user message and any
            # already-persisted assistant/tool messages from earlier
            # iterations). A failed call (e.g. a cold-start timeout) must not
            # leave a half-finished turn in the shared history — a thread with
            # consecutive user turns and no assistant/tool replies was the
            # root cause of allow_traffic silently no-op'ing on a later
            # "re-enable" turn.
            del _conversation_history[turn_start:]
            raise HTTPException(status_code=502, detail=f"Ollama error: {e}")

        now = time.time()
        if first_llm_at is None:
            first_llm_at = now
        interaction["llm_response_at"] = now
        interaction["prompt_eval_count"] = ollama_resp.get("prompt_eval_count")
        interaction["eval_count"] = ollama_resp.get("eval_count")

        msg = ollama_resp.get("message", {})
        tool_calls = msg.get("tool_calls", [])
        salvaged = False
        if not tool_calls:
            # Model wrote a tool call as JSON text instead of emitting it
            # (qwen2.5 failure mode) — recover and EXECUTE it so the claim
            # becomes the action instead of a fabricated result.
            salvaged_calls, cleaned = salvage_content_tool_calls(msg.get("content", ""))
            if salvaged_calls:
                salvaged = True
                msg = {"role": "assistant", "content": cleaned, "tool_calls": salvaged_calls}
                tool_calls = salvaged_calls
                print(
                    f"[chat] iter={iteration} SALVAGED {len(salvaged_calls)} tool call(s) "
                    f"written as JSON text: {[c['function']['name'] for c in salvaged_calls]}",
                    flush=True,
                )
        print(
            f"[chat] iter={iteration} response role={msg.get('role')!r} "
            f"content={msg.get('content', '')!r} tool_calls={len(tool_calls)} "
            f"done_reason={ollama_resp.get('done_reason')!r}",
            flush=True,
        )
        if iteration > 0 and not tool_calls and not msg.get("content"):
            print(
                f"[chat] iter={iteration} FULL RESPONSE (empty content + no tool calls): "
                f"{json.dumps(ollama_resp, default=str)[:2000]}",
                flush=True,
            )

        if not tool_calls:
            assistant_content = msg.get("content", "")

            # Acceptance guard: reject and re-ask instead of returning (and
            # persisting) a response that breaks a hard invariant.
            not_english = bool(_NON_ENGLISH_RE.search(assistant_content))
            false_claim = bool(
                not all_tool_results and _ACTION_CLAIM_RE.search(assistant_content)
            )
            if (not_english or false_claim) and corrective_retries < MAX_CORRECTIVE_RETRIES:
                corrective_retries += 1
                iteration += 1
                if not_english:
                    reason = "response not in English"
                    # Keep the draft visible — the model must translate it.
                    messages.append({"role": "assistant", "content": assistant_content})
                    messages.append({
                        "role": "user",
                        "content": "Rewrite your previous reply in English only. "
                                   "Keep exactly the same information.",
                    })
                else:
                    reason = "claimed an action but no tool ran this turn"
                    # Do NOT append the bad draft: at low temperature the model
                    # anchors on it and repeats it verbatim (observed live —
                    # two system-role corrections were ignored word-for-word).
                    # A user-role correction with a fresh slate works instead.
                    messages.append({
                        "role": "user",
                        "content": "Your reply was discarded: you claimed an action or "
                                   "result, but you called NO tool, so nothing was "
                                   "executed and no result exists. My request was: "
                                   f"\"{req.message}\". If it requires an action or "
                                   "test, emit the appropriate tool call(s) NOW. "
                                   "Only describe an action as done after a tool call "
                                   "executed it.",
                    })
                print(
                    f"[chat] iter={iteration} REJECTED response ({reason}) — "
                    f"corrective retry {corrective_retries}/{MAX_CORRECTIVE_RETRIES}",
                    flush=True,
                )
                interaction["prose_response"] = f"[retracted: {reason}] {assistant_content}"
                interaction["execution_success"] = True
                metrics.record(interaction)
                continue

            if false_claim:
                # Retries exhausted and the model still claims an unexecuted
                # action: deterministically tell the truth. The disclaimer is
                # returned AND persisted, so neither the user nor later turns
                # inherit the false claim.
                assistant_content += (
                    "\n\n⚠️ Note: no tool was actually executed this turn, so no "
                    "change or test happened on the network. Please ask again to "
                    "perform the action."
                )
                print("[chat] retries exhausted — false-claim disclaimer appended", flush=True)

            interaction["prose_response"] = assistant_content
            interaction["execution_success"] = True
            metrics.record(interaction)

            _conversation_history.append({"role": "assistant", "content": assistant_content})

            return {
                "response": assistant_content,
                "tool_calls": all_tool_results,
                "metrics": {
                    "llm_latency_s": round(first_llm_at - prompt_sent_at, 3),
                    "total_latency_s": round(time.time() - prompt_sent_at, 3),
                },
            }

        messages.append(msg)
        _conversation_history.append(msg)

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", {})

            per_tool = metrics.start_interaction(req.model, req.message)
            per_tool["tool_call"] = name
            per_tool["tool_args"] = str(args)
            per_tool["salvaged"] = 1 if salvaged else 0

            validation_err = validate_node_args(args, _active_scenario)
            if validation_err:
                per_tool["validation_passed"] = False
                per_tool["validation_error"] = validation_err
                per_tool["execution_success"] = False
                metrics.record(per_tool)
                tool_output = {"error": validation_err}
                all_tool_results.append({"tool": name, "error": validation_err})
            else:
                per_tool["validation_passed"] = True
                try:
                    tool_result = dispatch_tool(
                        name, args, _active_scenario, security, topology,
                    )
                    per_tool["tool_executed_at"] = time.time()
                    per_tool["execution_result"] = str(tool_result)[:500]
                    per_tool["execution_success"] = True
                    metrics.record(per_tool)
                    tool_output = tool_result
                    all_tool_results.append({"tool": name, "args": args, "result": tool_result})
                except Exception as e:
                    per_tool["tool_executed_at"] = time.time()
                    per_tool["execution_result"] = str(e)[:500]
                    per_tool["execution_success"] = False
                    metrics.record(per_tool)
                    tool_output = {"error": str(e)}
                    all_tool_results.append({"tool": name, "args": args, "error": str(e)})

            tool_msg = {"role": "tool", "content": json.dumps(tool_output)}
            messages.append(tool_msg)
            _conversation_history.append(tool_msg)

        interaction["salvaged"] = 1 if salvaged else 0
        interaction["execution_success"] = True
        metrics.record(interaction)

        iteration += 1
        tool_rounds += 1
        if tool_rounds > MAX_TOOL_ITERATIONS:
            break

    # Tool-round budget exhausted: the assistant msg + tool results of the
    # final round are already persisted above — history stays as-is.
    final_content = msg.get("content", "") or "Actions completed."

    return {
        "response": final_content,
        "tool_calls": all_tool_results,
        "metrics": {
            "llm_latency_s": round((first_llm_at or prompt_sent_at) - prompt_sent_at, 3),
            "total_latency_s": round(time.time() - prompt_sent_at, 3),
        },
    }


@app.get("/models")
def list_models() -> dict:
    """Models installed on whatever Ollama answers :11434 — the local service
    or the GPU tunnel. Drives the GUI's model dropdown."""
    base = OLLAMA_URL.rsplit("/api/", 1)[0]
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{base}/api/tags")
            resp.raise_for_status()
            names = [m["name"] for m in resp.json().get("models", [])]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ollama unreachable: {exc}") from exc
    return {"models": names}


@app.get("/metrics/session")
def get_session_metrics() -> dict:
    return {"session_id": metrics.session_id, "interactions": metrics.get_session_metrics()}


@app.get("/metrics/models")
def get_model_summary() -> list[dict]:
    return metrics.get_model_summary()
