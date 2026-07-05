"""Cost correlation: AWS pricing + waste detection."""
from .pricing import PRICING, get_hourly_price, get_monthly_price
from .analyzer import analyze_instance, suggest_smaller, CostAnalysis

__all__ = [
    "PRICING",
    "get_hourly_price",
    "get_monthly_price",
    "analyze_instance",
    "suggest_smaller",
    "CostAnalysis",
]
