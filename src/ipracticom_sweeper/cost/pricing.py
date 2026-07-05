"""AWS EC2 pricing catalog (us-east-1, approximate on-demand $/hour)."""
from __future__ import annotations


# Source: AWS pricing pages (approximate, verify before quoting to customers)
PRICING = {
    "t3.nano": 0.0052,
    "t3.micro": 0.0104,
    "t3.small": 0.0208,
    "t3.medium": 0.0416,
    "t3.large": 0.0832,
    "t3.xlarge": 0.1664,
    "t3.2xlarge": 0.3328,
    "m5.large": 0.096,
    "m5.xlarge": 0.192,
    "m5.2xlarge": 0.384,
    "c5.large": 0.085,
    "c5.xlarge": 0.17,
}


HOURS_PER_MONTH = 730  # standard billing assumption


def get_hourly_price(instance_type: str) -> float | None:
    """Returns $/hour for the given instance type, or None if unknown."""
    return PRICING.get(instance_type)


def get_monthly_price(instance_type: str) -> float | None:
    hourly = get_hourly_price(instance_type)
    if hourly is None:
        return None
    return round(hourly * HOURS_PER_MONTH, 2)
