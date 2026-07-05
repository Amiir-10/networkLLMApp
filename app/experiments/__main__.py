"""CLI: python -m app.experiments run experiments/s1-baseline-llama31.yaml [--reps N]"""

from __future__ import annotations

import argparse
import json

from app.experiments.runner import ExperimentRunner
from app.experiments.specs import ExperimentSpec


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.experiments")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="run (or extend) an experiment from a spec YAML")
    run_p.add_argument("spec", help="path to the experiment spec YAML")
    run_p.add_argument("--reps", type=int, default=None,
                       help="override spec.repetitions for this invocation")

    agg_p = sub.add_parser("aggregate", help="recompute stats+plots from existing reps only")
    agg_p.add_argument("spec", help="path to the experiment spec YAML")

    args = parser.parse_args()
    spec = ExperimentSpec.load(args.spec)
    if getattr(args, "reps", None):
        spec.repetitions = args.reps
    runner = ExperimentRunner(spec)

    # `aggregate` recomputes stats/plots from reps already on disk — no lab needed.
    agg = runner.aggregate() if args.cmd == "aggregate" else runner.run()

    print(json.dumps(agg, indent=1))
    print(f"\nResults: {runner.out_dir}\nPlots:   {runner.out_dir / 'plots'}")


if __name__ == "__main__":
    main()
