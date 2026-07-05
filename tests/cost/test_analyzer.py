"""Tests for cost correlation."""
import pytest
from ipracticom_sweeper.cost import (
    get_hourly_price,
    get_monthly_price,
    analyze_instance,
    suggest_smaller,
)


def test_pricing_known_instance():
    assert get_hourly_price("t3.medium") == 0.0416
    assert get_monthly_price("t3.medium") == round(0.0416 * 730, 2)


def test_pricing_unknown_instance():
    assert get_hourly_price("unknown.type") is None
    assert get_monthly_price("unknown.type") is None


def test_analyze_high_cpu_no_waste():
    a = analyze_instance("t3.large", avg_cpu_percent=80)
    assert a is not None
    assert a.wasted_monthly == 0.0


def test_analyze_low_cpu_waste():
    a = analyze_instance("t3.large", avg_cpu_percent=10)
    assert a is not None
    assert a.wasted_monthly > 0


def test_analyze_unknown_instance():
    assert analyze_instance("unknown.type", 50) is None


def test_analyze_invalid_cpu():
    assert analyze_instance("t3.large", -10) is None
    assert analyze_instance("t3.large", 150) is None


def test_suggest_smaller_xlarge_to_large():
    assert suggest_smaller("t3.xlarge") == "t3.large"


def test_suggest_smaller_already_smallest():
    assert suggest_smaller("t3.nano") is None


def test_suggest_smaller_unknown():
    assert suggest_smaller("unknown.type") is None
