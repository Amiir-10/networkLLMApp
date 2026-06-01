import asyncio
import fcntl
import json
import os
import pty
import struct
import subprocess
import termios
import time
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.engines.topology import TopologyEngine, LabAlreadyRunning, LabNotFound
from app.engines.security import SecurityEngine
from app.lab.models import Scenario
from app.chat import call_ollama, validate_node_args, dispatch_tool, MAX_TOOL_ITERATIONS, _node_ip_map
from app.prompts import build_system_prompt
from app.metrics import MetricsCollector

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = REPO_ROOT / "scenarios"
LAB_WORK_DIR = REPO_ROOT / "labs"

app = FastAPI(title="networkLLMApp", version="0.0.5")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
topology = TopologyEngine(work_dir=LAB_WORK_DIR)
security = SecurityEngine()
metrics = MetricsCollector()

_active_scenario: Scenario | None = None
_active_model: str = "llama3.1:8b"
_conversation_history: list[dict] = []


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "lab_active": _active_scenario is not None,
        "firewall_connected": security.connected,
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
    fw_node = next((n for n in scenario.nodes if n.role == "firewall"), None)
    if fw_node:
        mgmt_ip = topology.get_mgmt_ip(scenario_name, fw_node.id)
        if mgmt_ip:
            import time as _time
            security.connect(mgmt_url=f"http://{mgmt_ip}:8080")
            for _ in range(30):
                try:
                    result["firewall"] = security.health()
                    break
                except Exception:
                    _time.sleep(1)
            else:
                result["firewall_warning"] = "firewalld API did not become ready in 30s"
    return result


@app.post("/lab/start/{scenario_name}")
def lab_start(scenario_name: str) -> dict:
    global _active_scenario, _conversation_history
    _conversation_history = []
    scenario = _load_scenario(scenario_name)
    try:
        result = _deploy_and_connect(scenario, scenario_name)
    except LabAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc))
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
        out.append(f"{src} -> {dst} ({proto})")
    return out


@app.get("/rules")
def get_rules() -> dict:
    if not security.connected:
        return {"forward_rules": [], "zone_rules": [], "parsed": []}
    return security.list_rules()


class RuleRequest(BaseModel):
    src: str
    dst: str
    proto: str = "icmp"
    port: int | None = None


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
    result = security.block(src_ip, dst_ip, req.proto, req.port)
    return {"status": "added", "src": req.src, "dst": req.dst, "proto": req.proto, "port": req.port, "result": result}


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

    for iteration in range(MAX_TOOL_ITERATIONS + 1):
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
            ollama_resp = call_ollama(req.model, messages)
        except Exception as e:
            # Roll back the user turn appended above. A failed call (e.g. a
            # cold-start timeout) must not leave a dangling user message in the
            # shared history — a thread with consecutive user turns and no
            # assistant/tool replies was the root cause of allow_traffic
            # silently no-op'ing on a later "re-enable" turn.
            if _conversation_history and _conversation_history[-1].get("role") == "user":
                _conversation_history.pop()
            raise HTTPException(status_code=502, detail=f"Ollama error: {e}")

        now = time.time()
        if first_llm_at is None:
            first_llm_at = now
        interaction["llm_response_at"] = now
        interaction["prompt_eval_count"] = ollama_resp.get("prompt_eval_count")
        interaction["eval_count"] = ollama_resp.get("eval_count")

        msg = ollama_resp.get("message", {})
        tool_calls = msg.get("tool_calls", [])
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
            interaction["prose_response"] = msg.get("content", "")
            interaction["execution_success"] = True
            metrics.record(interaction)

            assistant_content = msg.get("content", "")
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

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", {})

            per_tool = metrics.start_interaction(req.model, req.message)
            per_tool["tool_call"] = name
            per_tool["tool_args"] = str(args)

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

            messages.append({"role": "tool", "content": json.dumps(tool_output)})

        interaction["execution_success"] = True
        metrics.record(interaction)

    final_content = msg.get("content", "") or "Actions completed."
    _conversation_history.append({"role": "assistant", "content": final_content})

    return {
        "response": final_content,
        "tool_calls": all_tool_results,
        "metrics": {
            "llm_latency_s": round((first_llm_at or prompt_sent_at) - prompt_sent_at, 3),
            "total_latency_s": round(time.time() - prompt_sent_at, 3),
        },
    }


@app.get("/metrics/session")
def get_session_metrics() -> dict:
    return {"session_id": metrics.session_id, "interactions": metrics.get_session_metrics()}


@app.get("/metrics/models")
def get_model_summary() -> list[dict]:
    return metrics.get_model_summary()
