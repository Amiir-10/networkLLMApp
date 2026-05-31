import json
import time
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.engines.topology import TopologyEngine, LabAlreadyRunning, LabNotFound
from app.engines.security import SecurityEngine
from app.lab.models import Scenario
from app.chat import call_ollama, validate_node_args, dispatch_tool, MAX_TOOL_ITERATIONS
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


@app.post("/lab/start/{scenario_name}")
def lab_start(scenario_name: str) -> dict:
    global _active_scenario, _conversation_history
    _conversation_history = []
    scenario = _load_scenario(scenario_name)
    try:
        result = topology.start(scenario)
    except LabAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    _active_scenario = scenario
    fw_node = next((n for n in scenario.nodes if n.role == "firewall"), None)
    if fw_node:
        mgmt_ip = topology.get_mgmt_ip(scenario_name, fw_node.id)
        if mgmt_ip:
            import time as _time
            security.connect(mgmt_url=f"http://{mgmt_ip}:8080")
            for _ in range(30):
                try:
                    fw_health = security.health()
                    result["firewall"] = fw_health
                    break
                except Exception:
                    _time.sleep(1)
            else:
                result["firewall_warning"] = "firewalld API did not become ready in 30s"

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
