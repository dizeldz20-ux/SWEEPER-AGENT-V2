"""Sprint 17 — Prometheus exporter tests."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

# Try to import Flask; if missing, skip the route tests but still
# exercise the pure render_metrics function.
try:
    from flask import Flask
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

from ipracticom_sweeper.monitoring.prometheus import (
    render_metrics,
    register_metrics_route,
    METRICS_HELP,
    _walk_checks,
    _bucket_counts,
)


# ============= render_metrics pure tests ===================================

def test_metrics_returns_text_plain_format() -> None:
    body = render_metrics()
    # Prometheus exposition: every line is either HELP, TYPE, or value
    for line in body.splitlines():
        assert line.startswith("# ") or line.startswith("sweeper_"), f"Bad line: {line!r}"


def test_metrics_includes_check_metrics() -> None:
    snapshot = {
        "checks": {
            "fs01_process": {"status": "ok"},
            "fs06_sip_peers": {"status": "warn"},
            "fs13_log_disk": {"status": "crit"},
        }
    }
    body = render_metrics(snapshot=snapshot)
    assert 'sweeper_check_status{check="fs01_process"} 1' in body
    assert 'sweeper_check_status{check="fs06_sip_peers"} 2' in body
    assert 'sweeper_check_status{check="fs13_log_disk"} 3' in body


def test_metrics_includes_pipeline_runs_counter() -> None:
    body = render_metrics(runs_total=42)
    assert "sweeper_pipeline_runs_total 42" in body
    assert "# TYPE sweeper_pipeline_runs_total counter" in body


def test_metrics_includes_pipeline_duration_histogram() -> None:
    body = render_metrics(pipeline_durations=[0.1, 0.5, 1.5, 3.0])
    assert "# TYPE sweeper_pipeline_duration_seconds histogram" in body
    assert "sweeper_pipeline_duration_seconds_bucket" in body
    # Sum + count
    assert "sweeper_pipeline_duration_seconds_sum 5.100" in body
    assert "sweeper_pipeline_duration_seconds_count 4" in body


def test_metrics_includes_repair_counters() -> None:
    body = render_metrics(repairs_total=10, repairs_success=8)
    assert "sweeper_repair_executions_total 10" in body
    assert "sweeper_repair_success_total 8" in body


def test_metrics_includes_defcon_and_self_health() -> None:
    body = render_metrics(defcon=3, self_health=1)
    assert "sweeper_defcon 3" in body
    assert "sweeper_self_health 1" in body


def test_metrics_handles_empty_snapshot() -> None:
    body = render_metrics(snapshot={})
    # No check lines but counters still present
    assert "sweeper_pipeline_runs_total 0" in body
    assert "sweeper_repair_executions_total 0" in body


def test_metrics_handles_none_snapshot() -> None:
    body = render_metrics(snapshot=None)
    assert "sweeper_pipeline_runs_total 0" in body


# ============= HELP lines ===================================================

def test_metrics_includes_help_lines() -> None:
    body = render_metrics()
    for metric_name in (
        "sweeper_check_status",
        "sweeper_pipeline_runs_total",
        "sweeper_repair_executions_total",
    ):
        assert f"# HELP {metric_name}" in body, f"Missing HELP for {metric_name}"


def test_metrics_help_text_not_empty() -> None:
    """Every metric in METRICS_HELP has a non-empty description."""
    for name, desc in METRICS_HELP.items():
        assert desc, f"{name} has empty help text"


# ============= _walk_checks =================================================

def test_walk_checks_yields_named_pairs() -> None:
    snap = {
        "checks": {
            "fs01": {"status": "ok"},
            "fs02": {"status": "warn"},
        }
    }
    pairs = list(_walk_checks(snap))
    assert ("fs01", "ok") in pairs
    assert ("fs02", "warn") in pairs


def test_walk_checks_handles_results_key() -> None:
    snap = {"results": {"foo": {"status": "crit"}}}
    assert ("foo", "crit") in list(_walk_checks(snap))


def test_walk_checks_handles_missing_status() -> None:
    snap = {"checks": {"foo": {}}}
    assert ("foo", "unknown") in list(_walk_checks(snap))


# ============= _bucket_counts ===============================================

def test_bucket_counts_basic() -> None:
    counts = _bucket_counts([0.05, 0.3, 1.5, 5.0], (0.1, 0.5, 1.0, 5.0))
    assert counts == [1, 2, 2, 4]


def test_bucket_counts_empty() -> None:
    assert _bucket_counts([], (1.0, 5.0)) == [0, 0]


# ============= Route registration (Flask) ===================================

@pytest.mark.skipif(not HAS_FLASK, reason="Flask not installed")
def test_metrics_route_returns_200() -> None:
    app = Flask(__name__)
    register_metrics_route(app, snapshot_provider=lambda: {})
    client = app.test_client()
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert b"sweeper_pipeline_runs_total" in resp.data


@pytest.mark.skipif(not HAS_FLASK, reason="Flask not installed")
def test_metrics_route_no_auth_required_by_default() -> None:
    app = Flask(__name__)
    register_metrics_route(app, snapshot_provider=lambda: {})
    os.environ.pop("SWEEPER_METRICS_TOKEN", None)
    client = app.test_client()
    resp = client.get("/metrics")
    assert resp.status_code == 200


@pytest.mark.skipif(not HAS_FLASK, reason="Flask not installed")
def test_metrics_route_optional_bearer_auth() -> None:
    app = Flask(__name__)
    register_metrics_route(app, snapshot_provider=lambda: {})
    os.environ["SWEEPER_METRICS_TOKEN"] = "secret123"
    try:
        client = app.test_client()
        # Missing token → 401
        resp = client.get("/metrics")
        assert resp.status_code == 401
        # With correct token → 200
        resp = client.get("/metrics", headers={"Authorization": "Bearer secret123"})
        assert resp.status_code == 200
    finally:
        os.environ.pop("SWEEPER_METRICS_TOKEN", None)


@pytest.mark.skipif(not HAS_FLASK, reason="Flask not installed")
def test_metrics_route_handles_collect_failure() -> None:
    app = Flask(__name__)

    def boom():
        raise RuntimeError("boom")

    register_metrics_route(app, snapshot_provider=boom)
    client = app.test_client()
    resp = client.get("/metrics")
    assert resp.status_code == 200
    # Body should not be empty
    assert b"sweeper_pipeline_runs_total" in resp.data