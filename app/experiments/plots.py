"""Graphs for a completed experiment: matplotlib PNGs regenerated on every run."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from app.experiments.stats import summarize  # noqa: E402


def _per_step_series(reps: list[dict], key: str) -> tuple[list[float], list[float], list[float]]:
    """mean + CI bounds per step across repetitions (CI collapses to the mean
    while only one rep exists)."""
    n_steps = len(reps[0][key])
    means, lo, hi = [], [], []
    for i in range(n_steps):
        s = summarize([r[key][i] for r in reps])
        means.append(s["mean"])
        ci = s["ci95"] or [s["mean"], s["mean"]]
        lo.append(ci[0])
        hi.append(ci[1])
    return means, lo, hi


def _step_axis(ax, step_kinds: list[str]) -> None:
    ax.set_xticks(range(len(step_kinds)))
    ax.set_xticklabels([f"{i + 1}\n{k}" for i, k in enumerate(step_kinds)])
    ax.set_xlabel("prompt step")


def plot_exposure(reps: list[dict], step_kinds: list[str], out: Path) -> None:
    means, lo, hi = _per_step_series(reps, "exposure_per_step")
    fig, ax = plt.subplots(figsize=(7, 4))
    x = range(len(means))
    ax.plot(x, means, marker="o", label=f"mean (k={len(reps)})")
    ax.fill_between(x, lo, hi, alpha=0.25, label="95% CI")
    ax.set_ylim(-0.05, 1.05)
    ax.set_ylabel("security exposure\n(reachable pairs / total)")
    _step_axis(ax, step_kinds)
    ax.legend()
    ax.set_title("Security exposure per step")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_time_per_step(reps: list[dict], step_kinds: list[str], out: Path) -> None:
    means, lo, hi = _per_step_series(reps, "wall_per_step_s")
    fig, ax = plt.subplots(figsize=(7, 4))
    x = list(range(len(means)))
    yerr = [[m - a for m, a in zip(means, lo)], [b - m for m, b in zip(means, hi)]]
    ax.bar(x, means, yerr=yerr, capsize=4)
    ax.set_ylabel("time to think (s)")
    _step_axis(ax, step_kinds)
    ax.set_title("LLM wall-clock per step (mean ± 95% CI)")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_summary(aggregate: dict, out: Path) -> None:
    """Scalar metrics with CI error bars, on their natural scales."""
    entries = [
        ("exposure\n(end)", aggregate["exposure_end"]),
        ("exposure\n(worst case)", aggregate["exposure_worst_case"]),
        ("efficiency\n(rules/minimal)", aggregate["efficiency_mean"]),
        ("time to think\n(s, /10)", _scaled(aggregate["time_to_think_s"], 0.1)),
    ]
    entries = [(label, s) for label, s in entries if s and s["mean"] is not None]
    fig, ax = plt.subplots(figsize=(7, 4))
    x = range(len(entries))
    means = [s["mean"] for _, s in entries]
    errs = []
    for _, s in entries:
        if s["ci95"]:
            errs.append((s["mean"] - s["ci95"][0], s["ci95"][1] - s["mean"]))
        else:
            errs.append((0.0, 0.0))
    yerr = [[e[0] for e in errs], [e[1] for e in errs]]
    ax.bar(x, means, yerr=yerr, capsize=4)
    ax.set_xticks(list(x))
    ax.set_xticklabels([label for label, _ in entries])
    sat = aggregate.get("satisfiable", {})
    sat_txt = f"satisfiability: {sat.get('rate'):.0%} of {sat.get('n')} reps" \
        if sat and sat.get("rate") is not None else "satisfiability: n/a"
    ax.set_title(f"Metric summary (mean ± 95% CI) — {sat_txt}")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def _scaled(s: dict, factor: float) -> dict:
    if not s or s["mean"] is None:
        return s
    out = dict(s)
    out["mean"] = s["mean"] * factor
    if s.get("std") is not None:
        out["std"] = s["std"] * factor
    if s.get("ci95"):
        out["ci95"] = [s["ci95"][0] * factor, s["ci95"][1] * factor]
    return out


def render_all(reps: list[dict], step_kinds: list[str], aggregate: dict, plot_dir: Path) -> list[Path]:
    plot_dir.mkdir(parents=True, exist_ok=True)
    outs = []
    for name, fn in (("exposure.png", plot_exposure),
                     ("time_per_step.png", plot_time_per_step)):
        path = plot_dir / name
        fn(reps, step_kinds, path)
        outs.append(path)
    path = plot_dir / "summary.png"
    plot_summary(aggregate, path)
    outs.append(path)
    return outs
