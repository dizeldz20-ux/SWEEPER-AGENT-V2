"""Simple linear regression in stdlib (no numpy)."""
from __future__ import annotations


def linear_regression(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Returns (slope, intercept) for y = slope*x + intercept.

    Raises ValueError if fewer than 2 points or all x values are identical.
    """
    if len(points) < 2:
        raise ValueError("Need at least 2 points")
    n = len(points)
    sum_x = sum(p[0] for p in points)
    sum_y = sum(p[1] for p in points)
    sum_xy = sum(p[0] * p[1] for p in points)
    sum_x2 = sum(p[0] ** 2 for p in points)
    denom = n * sum_x2 - sum_x ** 2
    if denom == 0:
        raise ValueError("All x values are identical")
    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept


def predict_at(slope: float, intercept: float, x: float) -> float:
    return slope * x + intercept
