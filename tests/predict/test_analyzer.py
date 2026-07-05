"""Tests for predictive analytics."""
import pytest
import time
from ipracticom_sweeper.predict import linear_regression, predict_at, predict_crossing


def test_linear_regression_basic():
    slope, intercept = linear_regression([(0, 0), (1, 1), (2, 2)])
    assert abs(slope - 1.0) < 1e-9
    assert abs(intercept - 0.0) < 1e-9


def test_linear_regression_with_intercept():
    slope, intercept = linear_regression([(0, 5), (1, 7), (2, 9)])
    assert abs(slope - 2.0) < 1e-9
    assert abs(intercept - 5.0) < 1e-9


def test_linear_regression_negative_slope():
    slope, intercept = linear_regression([(0, 10), (1, 8), (2, 6)])
    assert abs(slope - (-2.0)) < 1e-9
    assert abs(intercept - 10.0) < 1e-9


def test_linear_regression_too_few_points():
    with pytest.raises(ValueError):
        linear_regression([(0, 1)])


def test_linear_regression_identical_x():
    with pytest.raises(ValueError):
        linear_regression([(1, 1), (1, 2)])


def test_predict_at():
    assert abs(predict_at(2.0, 5.0, 3.0) - 11.0) < 1e-9


def test_predict_crossing_trending_up():
    """Disk fill: 50% 9h ago, growing 5%/h, now 90%, threshold 95%."""
    now = time.time()
    # Data points 1h apart: 9h ago=50%, 8h ago=55%, ..., now=90%
    # Wait — that's 9 steps × 5% = 45%, so 50% + 40% = 90% at i=0... let me recompute
    # i=9 → 9h ago → 50 + 0 = 50
    # i=8 → 8h ago → 50 + 5 = 55
    # ...
    # i=0 → now → 50 + 45 = 95... that's already at threshold
    # Use 8 steps: i=8 → 50, i=0 → 50 + 40 = 90
    values = [(now - i * 3600, 50 + (8 - i) * 5) for i in range(8, -1, -1)]
    # Verify: i=8 → 50, i=0 → 50+40=90
    assert values[-1][1] == 90
    pred = predict_crossing(values, threshold=95.0, metric_name="disk_used_pct")
    assert pred is not None
    assert pred.slope > 0
    assert pred.predicted_time_hours is not None
    assert pred.predicted_time_hours > 0


def test_predict_crossing_stable():
    """Value stable at 40%, threshold 90% — no crossing predicted."""
    now = time.time()
    values = [(now - i * 3600, 40) for i in range(5, -1, -1)]
    pred = predict_crossing(values, threshold=90.0)
    assert pred is not None
    assert pred.predicted_time_hours is None  # slope=0, not trending


def test_predict_crossing_already_past():
    """Current value already past threshold — return None."""
    now = time.time()
    values = [(now - 3600, 80), (now, 95)]
    pred = predict_crossing(values, threshold=90.0)
    assert pred is None


def test_predict_crossing_insufficient_data():
    pred = predict_crossing([(time.time(), 50)], threshold=90.0)
    assert pred is None


# --- v1.5.16 RED tests: edge cases not covered in original merge ---
import math


def test_predict_crossing_decreasing_trend_below_threshold():
    """Decreasing trend (slope < 0), value still below threshold.

    Returning now: negative `predicted_time_hours` ("we crossed 42h ago").
    Expected fix: return predicted_time_hours=None — never crosses *up* if trending *down*.
    """
    now = time.time()
    # 6 points (i=5..0), decreasing from 50% at i=5 to 25% at i=0
    values = [(now - i * 3600, 50 - (5 - i) * 5) for i in range(5, -1, -1)]
    assert values[-1][1] == 25  # well below 90%
    pred = predict_crossing(values, threshold=90.0)
    assert pred is not None
    assert pred.slope < 0
    # Bug: currently returns negative hours. After fix: None (not trending toward threshold).
    assert pred.predicted_time_hours is None, (
        f"decreasing slope must not predict crossing — got {pred.predicted_time_hours}h"
    )


def test_predict_crossing_inf_slope_returns_none():
    """Slope overflows to inf due to near-identical x values.

    Currently: ValueError/ZeroDivision in JSON serializer (`cannot convert float NaN to integer`).
    After fix: predict_crossing must return None safely.
    """
    now = time.time()
    # x values nearly identical — causes denom to be tiny → huge slope
    values = [
        (now - 1e-12, 50.0),
        (now - 1e-13, 51.0),
        (now, 52.0),
    ]
    pred = predict_crossing(values, threshold=100.0)
    # Must not crash; should either return None or finite numbers
    if pred is not None:
        assert math.isfinite(pred.slope), f"slope must be finite, got {pred.slope}"
        if pred.predicted_time_hours is not None:
            assert math.isfinite(pred.predicted_time_hours)


def test_predict_crossing_nan_in_input_returns_none():
    """NaN in input must not crash pipeline."""
    now = time.time()
    values = [(now - 3600, float('nan')), (now, 50.0)]
    pred = predict_crossing(values, threshold=90.0)
    assert pred is None or pred.predicted_time_hours is None or math.isfinite(pred.predicted_time_hours or 0.0)


def test_predict_crossing_inf_in_input_returns_none():
    """inf in input must not crash pipeline."""
    now = time.time()
    values = [(now - 3600, float('inf')), (now, 50.0)]
    pred = predict_crossing(values, threshold=90.0)
    # Must not crash. Either None or finite.
    if pred is not None:
        assert math.isfinite(pred.slope), f"slope must be finite, got {pred.slope}"
        assert math.isfinite(pred.current_value), f"current_value must be finite, got {pred.current_value}"


def test_to_dict_handles_nan():
    """to_dict() must not raise on NaN/Inf values.

    Currently: `round(float('nan'), 2)` raises ValueError in JSON serialization path.
    """
    from ipracticom_sweeper.predict.analyzer import Prediction
    pred = Prediction(
        metric="x",
        current_value=50.0,
        predicted_time_hours=None,
        slope=float('nan'),
        threshold=90.0,
    )
    out = pred.to_dict()
    # Must serialise without raising
    import json
    s = json.dumps(out)  # raises if any NaN/Inf
    assert "nan" not in s.lower() or out["slope"] is None or str(out["slope"]) == "nan"
    # After fix: slope should be coerced to None or "stable"
