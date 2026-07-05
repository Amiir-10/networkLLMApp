"""Per-repetition metric computation — the supervisor's 5 metrics.

Definitions follow the vault's Methodology-Brainstorm-2026-06-12 §3. All
metrics are computed from deterministic evidence gathered during the run
(probe matrices + GET /rules snapshots + wall-clock), never from LLM output.
"""

from __future__ import annotations

from app.experiments.prober import expected_matrix
from app.experiments.specs import ExperimentSpec


def exposure(matrix: dict[str, bool]) -> float:
    """Security exposure = reachable ordered pairs / total ordered pairs."""
    return sum(matrix.values()) / len(matrix) if matrix else 0.0


def drop_count(rules: dict) -> int:
    return sum(1 for r in rules.get("parsed", []) if r.get("action") == "drop")


def compute_rep_metrics(spec: ExperimentSpec, pcs: dict[str, str],
                        step_records: list[dict]) -> dict:
    """step_records[i] corresponds to spec.sequence[i] and carries
    'matrix', 'rules', 'wall_s' as gathered by the runner."""
    exposures = [exposure(r["matrix"]) for r in step_records]

    # Satisfiability: end-state probe matrix == authored expected end state.
    final = spec.final_expect()
    satisfiable = None
    if final is not None and step_records:
        satisfiable = step_records[-1]["matrix"] == expected_matrix(pcs, final.unreachable)

    # Per-mutation goal check + efficiency (rules added vs authored minimum).
    mutations: list[dict] = []
    for step, rec in zip(spec.sequence, step_records):
        if step.kind not in ("A", "U"):
            continue
        m: dict = {"kind": step.kind, "prompt": step.prompt}
        if step.expect is not None:
            m["goal_achieved"] = rec["matrix"] == expected_matrix(pcs, step.expect.unreachable)
        if step.minimal_rules:  # efficiency only defined when minimum >= 1
            m["efficiency"] = drop_count(rec["rules"]) / step.minimal_rules
        mutations.append(m)

    # Time to converge (supervisor def: "how many prompts to reach a specific
    # end state"): smallest 1-based prompt index i such that the end state
    # holds from step i through the end of the sequence. None = never = FAIL.
    converge_prompts = None
    if final is not None:
        want = expected_matrix(pcs, final.unreachable)
        for i in range(len(step_records)):
            if all(r["matrix"] == want for r in step_records[i:]):
                converge_prompts = i + 1
                break

    efficiencies = [m["efficiency"] for m in mutations if "efficiency" in m]
    return {
        "exposure_per_step": exposures,
        "exposure_worst_case": max(exposures) if exposures else None,
        "exposure_end": exposures[-1] if exposures else None,
        "satisfiable": satisfiable,
        "mutations": mutations,
        "efficiency_mean": sum(efficiencies) / len(efficiencies) if efficiencies else None,
        "converge_prompts": converge_prompts,
        "time_to_think_s": sum(r["wall_s"] for r in step_records),
        "wall_per_step_s": [r["wall_s"] for r in step_records],
    }
