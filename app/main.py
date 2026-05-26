import time
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.lab.driver import ContainerlabLabDriver, LabAlreadyRunning, LabNotFound
from app.lab.models import Scenario
from app.firewall.driver import FirewalldDriver
from app.chat import call_ollama, validate_node_args, dispatch_tool, SYSTEM_PROMPT
from app.metrics import MetricsCollector

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = REPO_ROOT / "scenarios"
LAB_WORK_DIR = REPO_ROOT / "labs"

app = FastAPI(title="networkLLMApp", version="0.0.3")
lab_driver = ContainerlabLabDriver(work_dir=LAB_WORK_DIR)
metrics = MetricsCollector()

_active_scenario: Scenario | None = None
_firewall_driver: FirewalldDriver | None = None
_active_model: str = "llama3.1:8b"


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "lab_active": _active_scenario is not None,
        "firewall_connected": _firewall_driver is not None,
    }


def _load_scenario(scenario_name: str) -> Scenario:
    scenario_file = SCENARIO_DIR / f"{scenario_name}.yaml"
    if not scenario_file.exists():
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_name}' not found")
    raw = yaml.safe_load(scenario_file.read_text())
    return Scenario.model_validate(raw)


@app.post("/lab/start/{scenario_name}")
def lab_start(scenario_name: str) -> dict:
    global _active_scenario, _firewall_driver
    scenario = _load_scenario(scenario_name)
    try:
        result = lab_driver.start(scenario)
    except LabAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    _active_scenario = scenario
    fw_node = next((n for n in scenario.nodes if n.role == "firewall"), None)
    if fw_node:
        mgmt_ip = lab_driver.get_mgmt_ip(scenario_name, fw_node.id)
        if mgmt_ip:
            import time as _time
            _firewall_driver = FirewalldDriver(mgmt_url=f"http://{mgmt_ip}:8080")
            for _ in range(30):
                try:
                    fw_health = _firewall_driver.health()
                    result["firewall"] = fw_health
                    break
                except Exception:
                    _time.sleep(1)
            else:
                result["firewall_warning"] = "firewalld API did not become ready in 30s"

    return result


@app.post("/lab/stop/{scenario_name}")
def lab_stop(scenario_name: str) -> dict:
    global _active_scenario, _firewall_driver
    try:
        lab_driver.stop(scenario_name)
    except LabNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    _active_scenario = None
    _firewall_driver = None
    return {"status": "stopped", "scenario": scenario_name}


@app.get("/lab/state")
def lab_state() -> dict:
    return lab_driver.state()


class ChatRequest(BaseModel):
    message: str
    model: str = "llama3.1:8b"


@app.post("/chat")
def chat(req: ChatRequest) -> dict:
    global _active_model
    if not _active_scenario:
        raise HTTPException(status_code=400, detail="No lab is running. Start a lab first.")
    if not _firewall_driver:
        raise HTTPException(status_code=400, detail="Firewall driver not connected.")

    _active_model = req.model
    interaction = metrics.start_interaction(req.model, req.message)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": req.message},
    ]

    try:
        ollama_resp = call_ollama(req.model, messages)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama error: {e}")

    interaction["llm_response_at"] = time.time()
    interaction["prompt_eval_count"] = ollama_resp.get("prompt_eval_count")
    interaction["eval_count"] = ollama_resp.get("eval_count")

    msg = ollama_resp.get("message", {})
    tool_calls = msg.get("tool_calls", [])

    if not tool_calls:
        interaction["prose_response"] = msg.get("content", "")
        interaction["execution_success"] = True
        metrics.record(interaction)
        return {
            "response": msg.get("content", ""),
            "tool_calls": [],
            "metrics": {
                "llm_latency_s": round(interaction["llm_response_at"] - interaction["prompt_sent_at"], 3),
            },
        }

    results = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name", "")
        args = fn.get("arguments", {})

        interaction["tool_call"] = name
        interaction["tool_args"] = str(args)

        validation_err = validate_node_args(args, _active_scenario)
        if validation_err:
            interaction["validation_passed"] = False
            interaction["validation_error"] = validation_err
            interaction["execution_success"] = False
            metrics.record(interaction)
            results.append({"tool": name, "error": validation_err})
            continue

        interaction["validation_passed"] = True
        try:
            tool_result = dispatch_tool(
                name, args, _active_scenario, _firewall_driver, lab_driver,
            )
            interaction["tool_executed_at"] = time.time()
            interaction["execution_result"] = str(tool_result)[:500]
            interaction["execution_success"] = True
            results.append({"tool": name, "args": args, "result": tool_result})
        except Exception as e:
            interaction["tool_executed_at"] = time.time()
            interaction["execution_result"] = str(e)[:500]
            interaction["execution_success"] = False
            results.append({"tool": name, "args": args, "error": str(e)})

    metrics.record(interaction)

    total_latency = time.time() - interaction["prompt_sent_at"]
    return {
        "response": msg.get("content", ""),
        "tool_calls": results,
        "metrics": {
            "llm_latency_s": round(interaction["llm_response_at"] - interaction["prompt_sent_at"], 3),
            "total_latency_s": round(total_latency, 3),
        },
    }


@app.get("/metrics/session")
def get_session_metrics() -> dict:
    return {"session_id": metrics.session_id, "interactions": metrics.get_session_metrics()}


@app.get("/metrics/models")
def get_model_summary() -> list[dict]:
    return metrics.get_model_summary()
