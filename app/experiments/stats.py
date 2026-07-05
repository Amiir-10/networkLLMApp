"""Aggregation across repetitions: mean, sample std, 95% confidence intervals.

Student's t is used for the CI because k (repetitions) is small; more
repetitions -> smaller t and smaller s/sqrt(k) -> narrower interval, which is
exactly the supervisor's "more experiments = lower confidence interval".
PASS/FAIL metrics aggregate as a proportion with a Wilson score interval.
Stdlib-only on purpose (no scipy dependency for a t-table).
"""

from __future__ import annotations

import math
import statistics

# Two-sided 95% critical values of Student's t by degrees of freedom.
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
        7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179,
        13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101,
        19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064,
        25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042}


def t95(df: int) -> float:
    if df <= 0:
        return float("nan")
    if df in _T95:
        return _T95[df]
    return 1.96 if df > 30 else _T95[max(k for k in _T95 if k <= df)]


def summarize(values: list[float]) -> dict:
    """mean / sample std / 95% CI for one metric over the repetitions."""
    vals = [v for v in values if v is not None]
    n = len(vals)
    if n == 0:
        return {"n": 0, "mean": None, "std": None, "ci95": None}
    mean = statistics.fmean(vals)
    if n == 1:
        return {"n": 1, "mean": mean, "std": None, "ci95": None}
    std = statistics.stdev(vals)  # sample std (n-1)
    half = t95(n - 1) * std / math.sqrt(n)
    return {"n": n, "mean": mean, "std": std, "ci95": [mean - half, mean + half]}


def wilson(successes: int, n: int, z: float = 1.96) -> dict:
    """Wilson score interval for a PASS/FAIL proportion."""
    if n == 0:
        return {"n": 0, "rate": None, "ci95": None}
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return {"n": n, "rate": p, "ci95": [center - half, center + half]}
