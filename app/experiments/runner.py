"""The k-repetition experiment loop.

The runner is an HTTP client of the running backend (the same /chat surface
the GUI uses — single-shared-surface principle) plus direct docker-exec
probes for ground truth. Results are flushed to disk as they happen and a
rerun of the same spec APPENDS repetitions instead of clobbering — that is
how the confidence interval narrows over time.

data/experiments/<spec-id>/
├── spec.yaml          frozen copy of the spec that produced these results
├── trace.jsonl        prompt-replay record of every /chat call
├── reps/rep-<n>.json  per-repetition step records + computed metrics
├── aggregate.json     stats over all complete reps (recomputed every run)
└── plots/*.png        regenerated every run
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from app.experiments import plots
from app.experiments.metrics import compute_rep_metrics
from app.experiments.prober import pc_ips, probe_matrix
from app.experiments.replay import JsonlStore, Recorder
from app.experiments.specs import ExperimentSpec
from app.experiments.stats import summarize, wilson

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EXPERIMENTS_DATA = REPO_ROOT / "data" / "experiments"


class ExperimentRunner:
    def __init__(self, spec: ExperimentSpec):
        self.spec = spec
        self.out_dir = EXPERIMENTS_DATA / spec.id
        self.rep_dir = self.out_dir / "reps"
        self.rep_dir.mkdir(parents=True, exist_ok=True)
        self.client = httpx.Client(base_url=spec.backend_url, timeout=spec.chat_timeout_s)
        self.recorder = Recorder(
            store=JsonlStore(self.out_dir / "trace.jsonl"),
            metadata={"experiment": spec.id, "scenario": spec.scenario, "model": spec.model},
        )
        self.pcs: dict[str, str] = {}

    # ---------- environment ----------

    def preflight(self) -> None:
        health = self.client.get("/health").json()
        if not health.get("lab_active") or health.get("scenario") != self.spec.scenario:
            raise RuntimeError(
                f"Lab must be running scenario '{self.spec.scenario}' "
                f"(health says: {health}). Start it: POST /lab/start/{self.spec.scenario}"
            )
        if not health.get("firewall_connected"):
            raise RuntimeError("Firewall driver not connected — POST /lab/reset and retry.")
        scenario = self.client.get(f"/scenarios/{self.spec.scenario}").json()
        self.pcs = pc_ips(scenario)
        if len(self.pcs) < 2:
            raise RuntimeError(f"Scenario has <2 PC nodes: {self.pcs}")
        self._freeze_spec()

    def _freeze_spec(self) -> None:
        """Pin the spec next to its results; refuse to mix results from a
        changed spec under the same id."""
        frozen = self.out_dir / "spec.yaml"
        current = json.dumps(self.spec.model_dump(), sort_keys=True, default=str)
        if frozen.exists():
            existing = json.dumps(
                ExperimentSpec.load(frozen).model_dump(), sort_keys=True, default=str)
            # repetitions may differ between invocations; compare the rest
            if self._without_reps(existing) != self._without_reps(current):
                raise RuntimeError(
                    f"Spec changed but id '{self.spec.id}' already has results in "
                    f"{self.out_dir}. Use a new id (results must not mix specs).")
        else:
            import yaml
            frozen.write_text(yaml.safe_dump(self.spec.model_dump(), sort_keys=False))

    @staticmethod
    def _without_reps(dumped: str) -> str:
        d = json.loads(dumped)
        d.pop("repetitions", None)
        return json.dumps(d, sort_keys=True)

    # ---------- state reset ----------

    def reset_state(self) -> None:
        """Fast path between reps: clear rules + chat history, verify a clean
        baseline. Full lab reset (destroy + redeploy — NEVER restart, dbus
        crash-loop) only if the baseline probe shows a dirty network."""
        self.client.post("/chat/reset")
        self.client.post("/rules/flush")
        baseline = probe_matrix(self.spec.scenario, self.pcs)
        if all(baseline.values()):
            return
        print(f"[runner] dirty baseline ({sum(not v for v in baseline.values())} "
              f"unreachable pairs) — full lab reset...")
        self.client.post(f"/lab/reset/{self.spec.scenario}",
                         timeout=httpx.Timeout(600.0))
        baseline = probe_matrix(self.spec.scenario, self.pcs)
        if not all(baseline.values()):
            bad = [k for k, v in baseline.items() if not v]
            raise RuntimeError(f"Baseline still dirty after lab reset: {bad}")

    # ---------- the loop ----------

    def _chat(self, message: str) -> tuple[dict, float]:
        payload = {"message": message, "model": self.spec.model,
                   "options": self.spec.options}
        t0 = time.time()
        try:
            resp = self.client.post("/chat", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as first_err:
            # One retry: cold model load can 502 the first call of a session.
            print(f"[runner] /chat failed ({first_err}); retrying once...")
            t0 = time.time()
            resp = self.client.post("/chat", json=payload)
            resp.raise_for_status()
        wall = time.time() - t0
        data = resp.json()
        self.recorder.record(request=payload, response=data)
        return data, wall

    def _existing_reps(self) -> list[int]:
        return sorted(int(p.stem.split("-")[1]) for p in self.rep_dir.glob("rep-*.json"))

    def run_repetition(self, rep_no: int) -> dict:
        rep_file = self.rep_dir / f"rep-{rep_no}.json"
        self.reset_state()
        step_records: list[dict] = []
        for idx, step in enumerate(self.spec.sequence):
            print(f"[runner] rep {rep_no} step {idx + 1}/{len(self.spec.sequence)} "
                  f"({step.kind}): {step.prompt!r}")
            data, wall = self._chat(step.prompt)
            record = {
                "step": idx,
                "kind": step.kind,
                "prompt": step.prompt,
                "response": data.get("response"),
                "tool_calls": data.get("tool_calls", []),
                "wall_s": wall,
                "matrix": probe_matrix(self.spec.scenario, self.pcs),
                "rules": self.client.get("/rules").json(),
            }
            step_records.append(record)
            # Flush progress after every step so a crash loses at most one step.
            rep_file.write_text(json.dumps(
                {"rep": rep_no, "complete": False, "steps": step_records},
                indent=1, default=str))
        rep = {
            "rep": rep_no,
            "complete": True,
            "steps": step_records,
            "metrics": compute_rep_metrics(self.spec, self.pcs, step_records),
        }
        rep_file.write_text(json.dumps(rep, indent=1, default=str))
        return rep

    def run(self) -> dict:
        self.preflight()
        existing = self._existing_reps()
        start = (existing[-1] + 1) if existing else 1
        print(f"[runner] {self.spec.id}: {len(existing)} existing rep(s); "
              f"running {self.spec.repetitions} more (rep {start}..."
              f"{start + self.spec.repetitions - 1})")
        for rep_no in range(start, start + self.spec.repetitions):
            self.run_repetition(rep_no)
        return self.aggregate()

    # ---------- aggregation ----------

    def aggregate(self) -> dict:
        reps = []
        for p in sorted(self.rep_dir.glob("rep-*.json")):
            data = json.loads(p.read_text())
            if data.get("complete"):
                reps.append(data["metrics"])
        if not reps:
            raise RuntimeError("No complete repetitions to aggregate.")
        sat_known = [r["satisfiable"] for r in reps if r["satisfiable"] is not None]
        agg = {
            "experiment": self.spec.id,
            "scenario": self.spec.scenario,
            "model": self.spec.model,
            "repetitions": len(reps),
            "satisfiable": wilson(sum(sat_known), len(sat_known)),
            "exposure_end": summarize([r["exposure_end"] for r in reps]),
            "exposure_worst_case": summarize([r["exposure_worst_case"] for r in reps]),
            "efficiency_mean": summarize([r["efficiency_mean"] for r in reps]),
            "converge_prompts": summarize(
                [r["converge_prompts"] for r in reps if r["converge_prompts"] is not None]),
            "converge_failures": sum(1 for r in reps if r["converge_prompts"] is None),
            "time_to_think_s": summarize([r["time_to_think_s"] for r in reps]),
        }
        (self.out_dir / "aggregate.json").write_text(json.dumps(agg, indent=1))
        step_kinds = [s.kind for s in self.spec.sequence]
        plots.render_all(reps, step_kinds, agg, self.out_dir / "plots")
        return agg
