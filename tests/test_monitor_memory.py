"""Tests for memory monitor module."""

import pytest

from ipracticom_sweeper.monitor import memory


def test_collect_returns_total_and_used():
    snap = memory.collect()
    assert snap["ram_total_kb"] > 0
    assert snap["ram_used_kb"] >= 0
    assert snap["ram_available_kb"] >= 0
    # used + available should equal total (within rounding)
    assert abs(snap["ram_used_kb"] + snap["ram_available_kb"] - snap["ram_total_kb"]) < 1024


def test_collect_percent_in_valid_range():
    snap = memory.collect()
    assert 0 <= snap["ram_used_percent"] <= 100
    assert 0 <= snap["swap_used_percent"] <= 100


def test_evaluate_crit_on_extreme_memory():
    rules = {"memory": {"used_percent_warn": 80.0, "used_percent_crit": 95.0, "swap_used_percent_warn": 50.0}}
    values = {"ram_used_percent": 99.0, "swap_used_percent": 0}
    assert memory.evaluate(values, rules) == "crit"


def test_evaluate_warn_on_high_memory():
    rules = {"memory": {"used_percent_warn": 80.0, "used_percent_crit": 95.0, "swap_used_percent_warn": 50.0}}
    values = {"ram_used_percent": 85.0, "swap_used_percent": 0}
    assert memory.evaluate(values, rules) == "warn"


def test_evaluate_warn_on_swap_usage():
    rules = {"memory": {"used_percent_warn": 80.0, "used_percent_crit": 95.0, "swap_used_percent_warn": 50.0}}
    values = {"ram_used_percent": 50.0, "swap_used_percent": 60.0}
    assert memory.evaluate(values, rules) == "warn"


def test_evaluate_ok_normal():
    rules = {"memory": {"used_percent_warn": 80.0, "used_percent_crit": 95.0, "swap_used_percent_warn": 50.0}}
    values = {"ram_used_percent": 30.0, "swap_used_percent": 0}
    assert memory.evaluate(values, rules) == "ok"