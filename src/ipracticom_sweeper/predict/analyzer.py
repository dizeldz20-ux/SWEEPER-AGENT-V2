"""Time-series analyzer: predict when threshold will be crossed.

This module owns the canonical `Prediction` dataclass. If you need
to serialize one for an HTTP response, call `.to_dict()` — keeps the
shape in one place.

v1.5.16: Hardened against NaN/Inf inputs and decreasing slopes.
- NaN/Inf in values or in slope/intercept → return None (don't crash pipeline).
- Decreasing slope when current < threshold → predicted_time_hours=None
  (we never cross UP if trending DOWN — predicting a negative ETA is meaningless).
- `to_dict()` coerces non-finite floats to None so JSON serialisation never raises.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Any

from .linear import linear_regression, predict_at


def _is_finite(x: float) -> bool:
    """True iff x is a real, finite number (not NaN, not ±Inf)."""
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


@dataclass
class Prediction:
    metric: str
    current_value: float
    predicted_time_hours: float | None  # None if not trending toward threshold
    slope: float
    threshold: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise for JSON / HTTP responses.

        Rounds floats so log lines and downstream consumers don't
        chase floating-point dust (10⁻⁹ slope noise).

        v1.5.16: Non-finite floats (NaN/Inf) are coerced to None so that
        ``json.dumps`` never raises with ``Out of range float values are
        not JSON compliant``. This is the right thing to do at the
        serialisation boundary — we either send a real number or null.
        """
        def _safe(v: float | None, places: int) -> float | None:
            if v is None:
                return None
            if not _is_finite(v):
                return None
            return round(v, places)

        return {
            "metric": self.metric,
            "current_value": _safe(self.current_value, 2),
            "predicted_time_hours": _safe(self.predicted_time_hours, 1),
            "slope": _safe(self.slope, 6),
            "threshold": _safe(self.threshold, 2),
        }


def predict_crossing(
    values: list[tuple[float, float]],
    threshold: float,
    metric_name: str = "value",
) -> Prediction | None:
    """Given a time-series of (timestamp, value) and a threshold, predict when
    the threshold will be crossed.

    Returns None if data is insufficient, value is already past threshold,
    any value is non-finite, or the regression is degenerate (Inf/NaN slope).

    v1.5.16 semantics: a *decreasing* slope (negative) when the current
    value is *below* the threshold means we never cross UP — return None
    for ``predicted_time_hours`` rather than a negative number that says
    "we already crossed N hours ago" (which is nonsense for a threshold
    we are moving *away* from).
    """
    if len(values) < 2:
        return None

    # Reject any non-finite inputs early — these propagate through
    # sum/sum_xy and turn the regression into NaN.
    for ts, val in values:
        if not _is_finite(ts) or not _is_finite(val):
            return None
    if not _is_finite(threshold):
        return None

    current_ts, current_val = values[-1]
    if current_val >= threshold:
        return None

    try:
        slope, intercept = linear_regression(values)
    except ValueError:
        return None

    # If the regression produced a non-finite slope (overflow / catastrophic
    # cancellation), we can't reason about crossing time. Bail safely.
    if not (_is_finite(slope) and _is_finite(intercept)):
        return Prediction(
            metric=metric_name,
            current_value=current_val,
            predicted_time_hours=None,
            slope=0.0,
            threshold=threshold,
        )

    if abs(slope) < 1e-9:
        # Not trending (or numerically zero) — no crossing predicted
        return Prediction(
            metric=metric_name,
            current_value=current_val,
            predicted_time_hours=None,
            slope=slope,
            threshold=threshold,
        )

    # v1.5.16: Negative slope means we are moving *away* from the threshold.
    # The "crossing" target_ts is in the past — not useful. Report None.
    if slope < 0:
        return Prediction(
            metric=metric_name,
            current_value=current_val,
            predicted_time_hours=None,
            slope=slope,
            threshold=threshold,
        )

    # Time (in seconds from now) until y=threshold
    # threshold = slope * t + intercept => t = (threshold - intercept) / slope
    target_ts = (threshold - intercept) / slope
    seconds_until = target_ts - current_ts
    hours_until = seconds_until / 3600

    # Final guard: if arithmetic somehow produced non-finite (shouldn't,
    # but defence in depth), null it out instead of crashing later.
    if not _is_finite(hours_until):
        hours_until = None  # type: ignore[assignment]

    return Prediction(
        metric=metric_name,
        current_value=current_val,
        predicted_time_hours=hours_until,
        slope=slope,
        threshold=threshold,
    )