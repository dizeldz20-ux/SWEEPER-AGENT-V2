"""Tests for CPU monitor module."""

import pytest

from ipracticom_sweeper.monitor import cpu


def test_get_load_average_returns_three_values():
    load = cpu.get_load_average()
    assert "load_1min" in load
    assert "load_5min" in load
    assert "load_15min" in load
    assert load["load_1min"] >= 0
    assert load["load_5min"] >= 0
    assert load["load_15min"] >= 0


def test_get_load_average_includes_proc_counts():
    load = cpu.get_load_average()
    assert load["total_procs"] > 0
    assert load["running_procs"] >= 0


def test_get_cpu_times_has_required_fields():
    times = cpu.get_cpu_times()
    required = {"user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"}
    assert required <= set(times.keys())
    assert all(v >= 0 for v in times.values())


def test_get_cpu_cores_positive():
    assert cpu.get_cpu_cores() > 0


def test_collect_returns_full_snapshot():
    snap = cpu.collect()
    assert "load_5min_per_core" in snap
    assert "cores" in snap
    assert "steal_percent" in snap
    assert "iowait_percent" in snap
    assert snap["cores"] > 0
    assert 0 <= snap["steal_percent"] <= 100
    assert 0 <= snap["iowait_percent"] <= 100


def test_evaluate_returns_valid_status():
    rules = {"cpu": {
        "load_avg_5min_warn": 2.0,
        "load_avg_5min_crit": 5.0,
        "iowait_percent_warn": 20.0,
        "steal_percent_warn": 10.0,
    }}
    snap = cpu.collect()
    status = cpu.evaluate(snap, rules)
    assert status in ("ok", "warn", "crit")


def test_evaluate_triggers_crit_on_high_load():
    rules = {"cpu": {
        "load_avg_5min_warn": 2.0,
        "load_avg_5min_crit": 5.0,
        "iowait_percent_warn": 20.0,
        "steal_percent_warn": 10.0,
    }}
    high_load = {"load_5min_per_core": 10.0, "steal_percent": 0, "iowait_percent": 0}
    assert cpu.evaluate(high_load, rules) == "crit"


def test_evaluate_triggers_warn_on_high_steal():
    rules = {"cpu": {
        "load_avg_5min_warn": 2.0,
        "load_avg_5min_crit": 5.0,
        "iowait_percent_warn": 20.0,
        "steal_percent_warn": 10.0,
    }}
    high_steal = {"load_5min_per_core": 0.5, "steal_percent": 50.0, "iowait_percent": 0}
    assert cpu.evaluate(high_steal, rules) == "warn"


def test_evaluate_returns_ok_for_normal():
    rules = {"cpu": {
        "load_avg_5min_warn": 2.0,
        "load_avg_5min_crit": 5.0,
        "iowait_percent_warn": 20.0,
        "steal_percent_warn": 10.0,
    }}
    normal = {"load_5min_per_core": 0.5, "steal_percent": 0.5, "iowait_percent": 2.0}
    assert cpu.evaluate(normal, rules) == "ok"