from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException

from app.lab.driver import ContainerlabLabDriver, LabAlreadyRunning, LabNotFound
from app.lab.models import Scenario

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = REPO_ROOT / "scenarios"
LAB_WORK_DIR = REPO_ROOT / "labs"

app = FastAPI(title="networkLLMApp", version="0.0.2")
driver = ContainerlabLabDriver(work_dir=LAB_WORK_DIR)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _load_scenario(scenario_name: str) -> Scenario:
    scenario_file = SCENARIO_DIR / f"{scenario_name}.yaml"
    if not scenario_file.exists():
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_name}' not found")
    raw = yaml.safe_load(scenario_file.read_text())
    return Scenario.model_validate(raw)


@app.post("/lab/start/{scenario_name}")
def lab_start(scenario_name: str) -> dict:
    scenario = _load_scenario(scenario_name)
    try:
        return driver.start(scenario)
    except LabAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/lab/stop/{scenario_name}")
def lab_stop(scenario_name: str) -> dict:
    try:
        driver.stop(scenario_name)
    except LabNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"status": "stopped", "scenario": scenario_name}


@app.get("/lab/state")
def lab_state() -> dict:
    return driver.state()
