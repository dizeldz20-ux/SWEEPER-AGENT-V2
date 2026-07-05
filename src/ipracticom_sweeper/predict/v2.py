"""Sprint 10 — Forecasting v2: trend, seasonality, anomaly, confidence, ensemble.

Complements the v1 linear-regression predictor (`predict.linear`,
`predict.analyzer`) with:
  - TrendDetector: classifies a series as rising / falling / stable
  - SeasonalDecomposer: separates trend + seasonal + residual
  - AnomalyDetector: flags outliers via MAD (median absolute deviation)
  - ConfidenceBands: produces p10/p50/p90 forecast intervals
  - EnsembleForecaster: blends multiple models for a robust point forecast
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Iterable, Optional


# --- Trend detector ---------------------------------------------------------

@dataclass
class TrendResult:
    direction: str        # "rising" | "falling" | "stable" | "unknown"
    slope: float          # units per second
    r_squared: float      # fit quality [0..1]
    sample_count: int


def detect_trend(values: list[tuple[float, float]]) -> TrendResult:
    """Classify a time-series trend.

    Uses a simple OLS fit over (timestamp, value). A series is "stable" if
    |slope| is below `slope_tolerance` or the fit is poor (R² < 0.3).
    """
    if len(values) < 2:
        return TrendResult(direction="unknown", slope=0.0, r_squared=0.0,
                           sample_count=len(values))

    n = len(values)
    xs = [t for t, _ in values]
    ys = [v for _, v in values]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    cov = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x == 0:
        return TrendResult(direction="unknown", slope=0.0, r_squared=0.0,
                           sample_count=n)

    slope = cov / var_x
    intercept = mean_y - slope * mean_x

    # R² = 1 - SS_res/SS_tot
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    if ss_tot == 0:
        r_sq = 1.0 if all(y == mean_y for y in ys) else 0.0
    else:
        ss_res = sum((ys[i] - (slope * xs[i] + intercept)) ** 2 for i in range(n))
        r_sq = max(0.0, 1.0 - ss_res / ss_tot)

    # Tolerance scaled to the data range
    y_range = max(ys) - min(ys)
    slope_tolerance = max(1e-9, y_range / (max(xs) - min(xs) + 1) * 0.01)

    if r_sq < 0.3 or abs(slope) < slope_tolerance:
        direction = "stable"
    elif slope > 0:
        direction = "rising"
    else:
        direction = "falling"

    return TrendResult(direction=direction, slope=slope,
                       r_squared=r_sq, sample_count=n)


# --- Seasonal decomposition -------------------------------------------------

@dataclass
class SeasonalComponents:
    trend: list[float] = field(default_factory=list)
    seasonal: list[float] = field(default_factory=list)
    residual: list[float] = field(default_factory=list)
    period: int = 0


def seasonal_decompose(values: list[float], period: int) -> SeasonalComponents:
    """Decompose a 1D series into trend + seasonal + residual.

    Trend: centered moving average of length `period`.
    Seasonal: average of detrended values per position-in-cycle.
    Residual: original - trend - seasonal.
    """
    n = len(values)
    comp = SeasonalComponents(period=period)
    if n < period * 2 or period < 2:
        return comp

    half = period // 2
    # Trend = centered moving average
    comp.trend = [0.0] * n
    for i in range(half, n - half):
        window = values[i - half:i + half + 1]
        comp.trend[i] = sum(window) / len(window)
    # Edge values stay 0; consumer should treat them as missing

    # Detrended = values - trend (only where trend is defined)
    detrended = [values[i] - comp.trend[i] if comp.trend[i] != 0.0 else 0.0
                 for i in range(n)]

    # Seasonal = average of detrended per position-in-cycle
    cycle_sums: dict[int, list[float]] = {j: [] for j in range(period)}
    for i in range(n):
        j = i % period
        if comp.trend[i] != 0.0:
            cycle_sums[j].append(detrended[i])

    cycle_avgs = {j: (sum(vs) / len(vs) if vs else 0.0) for j, vs in cycle_sums.items()}
    # Center seasonal to mean 0
    mean_seasonal = sum(cycle_avgs.values()) / period if period > 0 else 0.0
    comp.seasonal = [cycle_avgs[i % period] - mean_seasonal for i in range(n)]

    # Residual
    comp.residual = [values[i] - comp.trend[i] - comp.seasonal[i]
                     if comp.trend[i] != 0.0 else 0.0
                     for i in range(n)]

    return comp


# --- Anomaly detection (MAD) ------------------------------------------------

@dataclass
class AnomalyResult:
    is_anomaly: bool
    score: float            # how many MADs from median
    median: float
    mad: float
    threshold: float        # MAD multiplier used (e.g. 3.5)


def detect_anomaly(
    new_value: float,
    history: list[float],
    mad_multiplier: float = 3.5,
) -> AnomalyResult:
    """Flag `new_value` as anomalous vs `history` using Median Absolute Deviation.

    Returns AnomalyResult with score = (|new - median| / mad).
    """
    if not history:
        return AnomalyResult(is_anomaly=False, score=0.0, median=0.0,
                             mad=0.0, threshold=mad_multiplier)

    median = statistics.median(history)
    deviations = [abs(x - median) for x in history]
    mad = statistics.median(deviations) if deviations else 0.0

    if mad == 0.0:
        # If mad is zero (constant series), any non-zero deviation is anomaly
        if new_value == median:
            return AnomalyResult(is_anomaly=False, score=0.0, median=median,
                                 mad=0.0, threshold=mad_multiplier)
        return AnomalyResult(is_anomaly=True, score=float("inf"),
                             median=median, mad=0.0, threshold=mad_multiplier)

    score = abs(new_value - median) / mad
    return AnomalyResult(
        is_anomaly=score > mad_multiplier,
        score=score,
        median=median,
        mad=mad,
        threshold=mad_multiplier,
    )


# --- Confidence bands (p10/p50/p90) -----------------------------------------

@dataclass
class ConfidenceBands:
    p10: list[float]
    p50: list[float]
    p90: list[float]


def confidence_bands(
    values: list[float],
    horizon: int,
    residual_std_factor: float = 1.0,
) -> ConfidenceBands:
    """Forecast horizon steps with simple bootstrap-style p10/p50/p90 bands.

    Uses the last value as the anchor and the historical residual stddev
    to expand the band. The bands widen with sqrt(horizon) (random-walk
    assumption).
    """
    if not values:
        return ConfidenceBands(p10=[], p50=[], p90=[])

    last = values[-1]
    # Stddev of first-differences is a cheap proxy for noise
    if len(values) >= 2:
        diffs = [values[i] - values[i - 1] for i in range(1, len(values))]
        sigma = statistics.pstdev(diffs)
    else:
        sigma = 0.0

    p10: list[float] = []
    p50: list[float] = []
    p90: list[float] = []
    for h in range(1, horizon + 1):
        band = sigma * math.sqrt(h) * residual_std_factor
        p10.append(last - 1.282 * band)   # z=1.282 ≈ 10th percentile
        p50.append(last)
        p90.append(last + 1.282 * band)
    return ConfidenceBands(p10=p10, p50=p50, p90=p90)


# --- Ensemble forecaster ----------------------------------------------------

@dataclass
class EnsembleForecast:
    point: float
    components: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    horizon: int = 0


def ensemble_forecast(
    models: dict[str, float],
    weights: Optional[dict[str, float]] = None,
) -> EnsembleForecast:
    """Combine multiple model predictions into one.

    `models` is {model_name: predicted_value}.
    `weights` is {model_name: weight}; missing weights default to 1.0.
    Weights are normalized to sum to 1.
    """
    if not models:
        return EnsembleForecast(point=0.0)

    if weights is None:
        weights = {}

    # Normalize
    w_sum = sum(max(0.0, weights.get(name, 1.0)) for name in models)
    if w_sum == 0:
        w_sum = len(models)
        norm_w = {name: 1.0 / len(models) for name in models}
    else:
        norm_w = {name: max(0.0, weights.get(name, 1.0)) / w_sum
                  for name in models}

    point = sum(models[name] * norm_w[name] for name in models)
    return EnsembleForecast(
        point=point,
        components=dict(models),
        weights=norm_w,
    )


# --- Forecast horizon helper ------------------------------------------------

def predict_at_horizon(
    values: list[tuple[float, float]],
    horizon_seconds: float,
) -> Optional[float]:
    """Predict the y-value `horizon_seconds` past the last sample.

    Uses OLS slope/intercept from the (timestamp, value) series.
    Returns None if insufficient data or zero variance.
    """
    if len(values) < 2:
        return None
    n = len(values)
    xs = [t for t, _ in values]
    ys = [v for _, v in values]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x == 0:
        return None
    slope = cov / var_x
    intercept = mean_y - slope * mean_x
    target_x = xs[-1] + horizon_seconds
    return slope * target_x + intercept