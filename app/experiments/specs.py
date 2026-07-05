"""Experiment + task specs — the machine-checkable benchmark format.

Follows the task-spec design from the vault's Methodology-Brainstorm-2026-06-12:
each mutation step (A/U) carries its expected reachability delta and the
minimal rule count, so satisfiability and efficiency are computed against
authored ground truth rather than judged by an LLM.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

StepKind = Literal["D", "A", "U", "V"]


class StepExpect(BaseModel):
    """Expected reachability AFTER this step, as a delta from all-reachable:
    ordered [src, dst] PC pairs expected to be UNreachable. Everything else
    is expected reachable."""
    unreachable: list[list[str]] = Field(default_factory=list)


class Step(BaseModel):
    kind: StepKind
    prompt: str
    # Ground truth — meaningful on mutations (A/U). D steps are probes.
    expect: StepExpect | None = None
    # Efficiency denominator: minimal DROP rules needed for this step's goal.
    minimal_rules: int | None = None


class ExperimentSpec(BaseModel):
    id: str
    scenario: str
    model: str = "llama3.1:8b"
    repetitions: int = 3
    backend_url: str = "http://localhost:8000"
    # Ollama options passed through /chat (temperature fixed low: GeNet 2024
    # found "creativity" degrades intent fulfilment, and low temp shrinks
    # variance across repetitions).
    options: dict = Field(default_factory=lambda: {"temperature": 0.0})
    chat_timeout_s: float = 300.0
    sequence: list[Step]

    @classmethod
    def load(cls, path: str | Path) -> "ExperimentSpec":
        raw = yaml.safe_load(Path(path).read_text())
        return cls.model_validate(raw)

    def final_expect(self) -> StepExpect | None:
        """Expected end state = the last step that declares one."""
        for step in reversed(self.sequence):
            if step.expect is not None:
                return step.expect
        return None
