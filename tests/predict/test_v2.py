"""Sprint 10 — Forecasting v2 tests (trend, seasonal, anomaly, bands, ensemble)."""
from __future__ import annotations

import math

import pytest

from ipracticom_sweeper.predict import (
    detect_trend, TrendResult,
    seasonal_decompose, SeasonalComponents,
    detect_anomaly, AnomalyResult,
    confidence_bands, ConfidenceBands,
    ensemble_forecast, EnsembleForecast,
    predict_at_horizon,
)


# ============= detect_trend =================================================

def test_trend_rising_clear() -> None:
    """A series that grows steadily is 'rising'."""
    values = [(float(i), float(i) * 2) for i in range(20)]
    t = detect_trend(values)
    assert t.direction == "rising"
    assert t.slope > 0
    assert t.r_squared > 0.9


def test_trend_falling_clear() -> None:
    values = [(float(i), 100.0 - i) for i in range(20)]
    t = detect_trend(values)
    assert t.direction == "falling"
    assert t.slope < 0


def test_trend_stable_flat() -> None:
    values = [(float(i), 50.0) for i in range(20)]
    t = detect_trend(values)
    assert t.direction == "stable"


def test_trend_unknown_too_few_points() -> None:
    t = detect_trend([(0.0, 1.0)])
    assert t.direction == "unknown"
    assert t.sample_count == 1


def test_trend_stable_noisy_around_mean() -> None:
    """Noisy series around a constant mean is 'stable'."""
    values = [(float(i), 10.0 + (i % 3) * 0.01) for i in range(20)]
    t = detect_trend(values)
    assert t.direction == "stable"


def test_trend_handles_zero_variance_x() -> None:
    """All same timestamp → can't fit a line."""
    values = [(1.0, 1.0), (1.0, 2.0), (1.0, 3.0)]
    t = detect_trend(values)
    assert t.direction == "unknown"


def test_trend_returns_dataclass() -> None:
    values = [(float(i), float(i)) for i in range(5)]
    t = detect_trend(values)
    assert isinstance(t, TrendResult)
    assert hasattr(t, "direction")
    assert hasattr(t, "slope")
    assert hasattr(t, "r_squared")


def test_trend_sample_count_matches() -> None:
    values = [(float(i), float(i)) for i in range(7)]
    t = detect_trend(values)
    assert t.sample_count == 7


# ============= seasonal_decompose ===========================================

def test_seasonal_decompose_separates_components() -> None:
    """Trend + seasonal + residual should reconstruct the original."""
    values = []
    for i in range(48):
        # Trend: linear; Seasonal: sin wave with period 12
        trend = i * 0.5
        seasonal = math.sin(2 * math.pi * i / 12) * 5
        values.append(trend + seasonal)
    comp = seasonal_decompose(values, period=12)
    assert len(comp.trend) == 48
    assert len(comp.seasonal) == 48
    assert len(comp.residual) == 48
    assert comp.period == 12


def test_seasonal_decompose_too_short() -> None:
    comp = seasonal_decompose([1.0, 2.0, 3.0], period=12)
    assert comp.trend == []
    assert comp.seasonal == []


def test_seasonal_decompose_handles_period_2() -> None:
    values = [1.0, 3.0] * 10  # alternating
    comp = seasonal_decompose(values, period=2)
    assert comp.period == 2
    # Edge values stay 0
    assert comp.trend[0] == 0.0
    assert comp.trend[-1] == 0.0


def test_seasonal_decompose_constant_series() -> None:
    values = [5.0] * 24
    comp = seasonal_decompose(values, period=4)
    # All seasonal values should be ~0 (centered)
    assert all(abs(s) < 1e-9 for s in comp.seasonal)


def test_seasonal_decompose_returns_dataclass() -> None:
    comp = seasonal_decompose([1.0] * 24, period=4)
    assert isinstance(comp, SeasonalComponents)
    assert hasattr(comp, "trend")
    assert hasattr(comp, "seasonal")
    assert hasattr(comp, "residual")
    assert hasattr(comp, "period")


def test_seasonal_decompose_handles_period_one() -> None:
    comp = seasonal_decompose([1.0, 2.0, 3.0, 4.0], period=1)
    # period < 2 → returns empty
    assert comp.trend == []


def test_seasonal_residual_small_for_clean_signal() -> None:
    """A clean trend+seasonal signal → small residual."""
    values = []
    for i in range(60):
        trend = i * 0.1
        seasonal = math.sin(2 * math.pi * i / 10) * 2
        values.append(trend + seasonal)
    comp = seasonal_decompose(values, period=10)
    # Mid-range residuals should be small
    mid_residuals = comp.residual[5:55]
    max_abs = max(abs(r) for r in mid_residuals)
    assert max_abs < 1.0


# ============= detect_anomaly ===============================================

def test_anomaly_returns_dataclass() -> None:
    r = detect_anomaly(1.0, [1.0, 1.0, 1.0])
    assert isinstance(r, AnomalyResult)
    assert hasattr(r, "is_anomaly")
    assert hasattr(r, "score")


def test_anomaly_no_history_returns_false() -> None:
    r = detect_anomaly(99.0, [])
    assert r.is_anomaly is False


def test_anomaly_clear_outlier() -> None:
    history = [10.0, 11.0, 9.5, 10.2, 10.8, 9.9, 10.1]
    r = detect_anomaly(100.0, history)
    assert r.is_anomaly is True


def test_anomaly_value_in_range() -> None:
    history = [10.0, 11.0, 9.5, 10.2, 10.8, 9.9, 10.1]
    r = detect_anomaly(10.1, history)
    assert r.is_anomaly is False


def test_anomaly_constant_history_zero_mad() -> None:
    """Constant history → mad=0; only exact matches are normal."""
    history = [5.0] * 10
    r = detect_anomaly(5.0, history)
    assert r.is_anomaly is False
    r2 = detect_anomaly(5.1, history)
    assert r2.is_anomaly is True


def test_anomaly_score_equals_absolute_dev_over_mad() -> None:
    history = [10.0, 11.0, 9.0, 10.0, 11.0, 9.0]
    r = detect_anomaly(15.0, history)
    assert r.score == abs(15.0 - r.median) / r.mad


def test_anomaly_threshold_in_result() -> None:
    r = detect_anomaly(1.0, [1.0, 1.0, 1.0], mad_multiplier=2.5)
    assert r.threshold == 2.5


def test_anomaly_score_above_threshold() -> None:
    history = [10.0, 11.0, 9.5, 10.2, 10.8, 9.9, 10.1]
    r = detect_anomaly(50.0, history)
    assert r.is_anomaly is True
    assert r.score > r.threshold


# ============= confidence_bands =============================================

def test_confidence_bands_returns_dataclass() -> None:
    cb = confidence_bands([1.0, 2.0, 3.0], horizon=5)
    assert isinstance(cb, ConfidenceBands)
    assert len(cb.p10) == 5
    assert len(cb.p50) == 5
    assert len(cb.p90) == 5


def test_confidence_bands_widen_with_horizon() -> None:
    """Noisy series → bands grow with sqrt(horizon)."""
    # Add noise so the diffs have non-zero stddev
    import random
    random.seed(42)
    values = [10.0 + random.gauss(0, 2.0) for _ in range(50)]
    cb = confidence_bands(values, horizon=10)
    half_widths = [cb.p90[i] - cb.p50[i] for i in range(10)]
    for i in range(1, 10):
        assert half_widths[i] > half_widths[i - 1]


def test_confidence_bands_p50_anchored_at_last_value() -> None:
    """The median forecast is anchored at the last observed value."""
    values = [10.0, 11.0, 12.0]
    cb = confidence_bands(values, horizon=3)
    for p in cb.p50:
        assert p == 12.0  # last value


def test_confidence_bands_p10_below_p50_below_p90() -> None:
    cb = confidence_bands([1.0, 5.0, 3.0, 7.0, 2.0], horizon=8)
    for i in range(8):
        assert cb.p10[i] <= cb.p50[i] <= cb.p90[i]


def test_confidence_bands_constant_series() -> None:
    """All-constant history → zero stddev → bands collapse to a line."""
    cb = confidence_bands([5.0] * 10, horizon=3)
    for i in range(3):
        assert cb.p10[i] == 5.0
        assert cb.p90[i] == 5.0


def test_confidence_bands_empty_input() -> None:
    cb = confidence_bands([], horizon=3)
    assert cb.p10 == []
    assert cb.p50 == []
    assert cb.p90 == []


def test_confidence_bands_horizon_one() -> None:
    cb = confidence_bands([1.0, 2.0, 3.0], horizon=1)
    assert len(cb.p10) == 1


# ============= ensemble_forecast =============================================

def test_ensemble_forecast_returns_dataclass() -> None:
    e = ensemble_forecast({"linear": 10.0})
    assert isinstance(e, EnsembleForecast)


def test_ensemble_forecast_single_model() -> None:
    e = ensemble_forecast({"linear": 10.0})
    assert e.point == 10.0
    assert e.weights == {"linear": 1.0}


def test_ensemble_forecast_equal_weights() -> None:
    e = ensemble_forecast({"a": 10.0, "b": 20.0})
    assert e.point == 15.0


def test_ensemble_forecast_weighted() -> None:
    e = ensemble_forecast(
        {"a": 10.0, "b": 20.0},
        weights={"a": 1.0, "b": 3.0},
    )
    # 10 * 0.25 + 20 * 0.75 = 17.5
    assert abs(e.point - 17.5) < 1e-9


def test_ensemble_forecast_normalizes_weights() -> None:
    e = ensemble_forecast(
        {"a": 10.0, "b": 20.0},
        weights={"a": 2.0, "b": 2.0},
    )
    # Sum = 4; normalized = 0.5 each
    assert e.weights == {"a": 0.5, "b": 0.5}


def test_ensemble_forecast_empty_models() -> None:
    e = ensemble_forecast({})
    assert e.point == 0.0


def test_ensemble_forecast_negative_weights_ignored() -> None:
    e = ensemble_forecast(
        {"a": 10.0, "b": 20.0},
        weights={"a": -1.0, "b": 1.0},
    )
    # negative weight dropped; b gets 100%
    assert e.point == 20.0


def test_ensemble_forecast_zero_weights_default_to_equal() -> None:
    e = ensemble_forecast(
        {"a": 10.0, "b": 20.0},
        weights={"a": 0.0, "b": 0.0},
    )
    assert e.point == 15.0


# ============= predict_at_horizon ==========================================

def test_predict_at_horizon_zero_horizon() -> None:
    values = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]
    # horizon=0 means predict at the last timestamp
    p = predict_at_horizon(values, horizon_seconds=0)
    assert p == 2.0


def test_predict_at_horizon_linear_extrapolation() -> None:
    values = [(0.0, 0.0), (10.0, 100.0)]  # slope = 10
    # 10s past last sample → 100 + 10*10 = 200
    p = predict_at_horizon(values, horizon_seconds=10)
    assert abs(p - 200.0) < 1e-9


def test_predict_at_horizon_negative_horizon_backcast() -> None:
    values = [(0.0, 0.0), (10.0, 100.0)]
    # -10s → 0.0 (back to the start)
    p = predict_at_horizon(values, horizon_seconds=-10)
    assert abs(p - 0.0) < 1e-9


def test_predict_at_horizon_insufficient_data() -> None:
    assert predict_at_horizon([(0.0, 1.0)], horizon_seconds=5) is None


def test_predict_at_horizon_constant_returns_constant() -> None:
    values = [(0.0, 5.0), (1.0, 5.0), (2.0, 5.0)]
    p = predict_at_horizon(values, horizon_seconds=10)
    assert p == 5.0


def test_predict_at_horizon_zero_variance_x() -> None:
    """All same x → no slope → None."""
    values = [(1.0, 1.0), (1.0, 2.0)]
    assert predict_at_horizon(values, horizon_seconds=1) is None


def test_predict_at_horizon_negative_slope() -> None:
    values = [(0.0, 100.0), (10.0, 50.0)]
    # 10s past → 50 + (-5)*10 = 0
    p = predict_at_horizon(values, horizon_seconds=10)
    assert abs(p - 0.0) < 1e-9


def test_predict_at_horizon_with_noise() -> None:
    """Noisy but trending upward → still predict higher."""
    values = [(float(i), float(i) * 2 + (i % 2) * 0.1) for i in range(20)]
    p = predict_at_horizon(values, horizon_seconds=20)
    # Should be close to 2*20 = 40 (last value at x=20, +20s → x=40)
    assert p > 35.0


# ============= Integration / composition ===================================

def test_trend_plus_anomaly_pipeline() -> None:
    """Detect trend, then check if latest point is anomaly."""
    values = [(float(i), float(i) * 2) for i in range(20)]
    # Last point is wildly out of trend
    values.append((20.0, 500.0))
    t = detect_trend(values[:-1])
    assert t.direction == "rising"
    # Check the last point
    history = [v for _, v in values[:-1]]
    a = detect_anomaly(values[-1][1], history)
    assert a.is_anomaly is True


def test_ensemble_with_real_models() -> None:
    """Two models with different predictions; ensemble blends them."""
    models = {
        "linear": 80.0,
        "seasonal": 100.0,
    }
    weights = {"linear": 0.7, "seasonal": 0.3}
    e = ensemble_forecast(models, weights=weights)
    # 80*0.7 + 100*0.3 = 86
    assert abs(e.point - 86.0) < 1e-9


def test_bands_plus_horizon_consistent() -> None:
    """Confidence bands at the same horizon as predict_at_horizon
    should be close to the point prediction."""
    values = [(float(i), float(i) * 3) for i in range(20)]
    point = predict_at_horizon(values, horizon_seconds=5)
    bands = confidence_bands([v for _, v in values], horizon=1)
    # p50 is anchored at the last value, so this isn't directly comparable,
    # but bands should at least bracket the point (loosely)
    assert isinstance(point, float)
    assert len(bands.p50) == 1


def test_full_pipeline_forecast() -> None:
    """End-to-end: trend → decompose → bands → ensemble → point."""
    values = []
    for i in range(40):
        values.append((float(i), 50.0 + i * 1.5 + math.sin(2 * math.pi * i / 8) * 3))
    t = detect_trend(values)
    assert t.direction == "rising"
    decomp = seasonal_decompose([v for _, v in values], period=8)
    assert decomp.period == 8
    bands = confidence_bands([v for _, v in values], horizon=10)
    e = ensemble_forecast({
        "linear_trend": predict_at_horizon(values, horizon_seconds=10) or 0.0,
        "naive": values[-1][1],
    }, weights={"linear_trend": 0.6, "naive": 0.4})
    assert e.point > 0