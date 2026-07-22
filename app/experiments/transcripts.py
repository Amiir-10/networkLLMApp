"""Human-readable Markdown transcripts of each repetition's LLM conversation.

The supervisor wants the model's actual outputs available for the thesis.
Everything is already captured machine-readably (reps/rep-<n>.json and
trace.jsonl) — this module renders it readable/quotable: one Markdown file
per repetition under data/experiments/<id>/transcripts/, regenerated
idempotently from the rep JSONs, so existing experiments can be backfilled
with `python -m app.experiments aggregate <spec>` (no lab needed).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.experiments.specs import ExperimentSpec

KIND_LABELS = {
    "D": "Describe (probe)",
    "A": "Apply (mutation)",
    "U": "Undo (mutation)",
    "V": "Verify",
}


def _fence(obj: object) -> str:
    return "```json\n" + json.dumps(obj, indent=1, default=str) + "\n```"


def render_rep_markdown(spec: ExperimentSpec, rep: dict) -> str:
    """Render one repetition record (a parsed reps/rep-<n>.json) as Markdown."""
    lines: list[str] = [
        f"# {spec.id} — repetition {rep.get('rep')}",
        "",
        f"- **Scenario:** {spec.scenario}",
        f"- **Model:** {spec.model}",
        f"- **Options:** `{json.dumps(spec.options)}`",
    ]
    if not rep.get("complete"):
        lines.append("- **INCOMPLETE** — this repetition was aborted mid-run "
                     "and is excluded from aggregate statistics.")
    metrics = rep.get("metrics")
    if metrics:
        lines += ["", "## Metrics", "", _fence(metrics)]

    for record in rep.get("steps", []):
        n = record.get("step", 0) + 1
        kind = record.get("kind", "?")
        lines += [
            "",
            f"## Step {n} — {kind} ({KIND_LABELS.get(kind, 'unknown')})",
            "",
            f"**Prompt:** {record.get('prompt')}",
            "",
            "**LLM response:**",
            "",
            "> " + str(record.get("response") or "(empty)").replace("\n", "\n> "),
        ]
        tool_calls = record.get("tool_calls") or []
        if tool_calls:
            lines += ["", f"**Tool calls ({len(tool_calls)}):**", "", _fence(tool_calls)]
        else:
            lines += ["", "**Tool calls:** none"]

        blocked = sorted(pair for pair, ok in (record.get("matrix") or {}).items() if not ok)
        rules = record.get("rules") or {}
        drop_count = len(rules.get("forward_rules", [])) + len(rules.get("zone_rules", []))
        lines += [
            "",
            f"**After this step:** {drop_count} DROP rule(s); "
            + (f"blocked pairs: {', '.join(f'`{p}`' for p in blocked)}"
               if blocked else "all probed pairs reachable"),
            "",
            f"**Wall time:** {record.get('wall_s', 0):.1f}s",
        ]
    lines.append("")
    return "\n".join(lines)


def write_transcripts(spec: ExperimentSpec, out_dir: Path) -> list[Path]:
    """(Re)write transcripts/rep-<n>.md for every rep JSON under out_dir."""
    transcript_dir = out_dir / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for rep_file in sorted((out_dir / "reps").glob("rep-*.json")):
        rep = json.loads(rep_file.read_text())
        target = transcript_dir / f"{rep_file.stem}.md"
        target.write_text(render_rep_markdown(spec, rep))
        written.append(target)
    return written
