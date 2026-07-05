"""Predictive analytics: linear regression + threshold crossing + v2 features."""
from .linear import linear_regression, predict_at
from .analyzer import predict_crossing, Prediction
from .v2 import (
    TrendResult, detect_trend,
    SeasonalComponents, seasonal_decompose,
    AnomalyResult, detect_anomaly,
    ConfidenceBands, confidence_bands,
    EnsembleForecast, ensemble_forecast,
    predict_at_horizon,
)

__all__ = [
    "linear_regression", "predict_at",
    "predict_crossing", "Prediction",
    "TrendResult", "detect_trend",
    "SeasonalComponents", "seasonal_decompose",
    "AnomalyResult", "detect_anomaly",
    "ConfidenceBands", "confidence_bands",
    "EnsembleForecast", "ensemble_forecast",
    "predict_at_horizon",
]
