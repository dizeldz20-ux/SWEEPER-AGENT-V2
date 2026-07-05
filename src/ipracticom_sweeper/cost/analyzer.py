"""Cost correlation: CPU usage vs instance waste."""
from __future__ import annotations
from dataclasses import dataclass
from .pricing import get_monthly_price, PRICING


@dataclass
class CostAnalysis:
    instance_type: str
    monthly_cost: float
    avg_cpu_percent: float
    wasted_monthly: float  # dollars wasted due to over-provisioning


def analyze_instance(instance_type: str, avg_cpu_percent: float) -> CostAnalysis | None:
    """Calculate wasted spend if CPU avg < 50% (rough heuristic)."""
    monthly = get_monthly_price(instance_type)
    if monthly is None:
        return None
    if avg_cpu_percent < 0 or avg_cpu_percent > 100:
        return None

    if avg_cpu_percent >= 50:
        wasted = 0.0
    else:
        # Assume we could run at 50% CPU on a smaller instance
        # Waste = monthly * (1 - avg_cpu/50) / 2
        wasted = round(monthly * (1 - avg_cpu_percent / 50) / 2, 2)

    return CostAnalysis(
        instance_type=instance_type,
        monthly_cost=monthly,
        avg_cpu_percent=avg_cpu_percent,
        wasted_monthly=wasted,
    )


def suggest_smaller(instance_type: str) -> str | None:
    """Suggest one size smaller in same family, or None if already smallest."""
    if instance_type not in PRICING:
        return None
    # Parse family and size: e.g. "t3.xlarge" -> ("t3", "xlarge")
    parts = instance_type.split(".")
    if len(parts) != 2:
        return None
    family, size = parts
    sizes = ["nano", "micro", "small", "medium", "large", "xlarge", "2xlarge"]
    try:
        idx = sizes.index(size)
    except ValueError:
        return None
    if idx == 0:
        return None  # already smallest
    smaller = f"{family}.{sizes[idx - 1]}"
    if smaller in PRICING:
        return smaller
    return None
