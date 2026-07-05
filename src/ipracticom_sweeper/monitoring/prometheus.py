"""Sprint 17 — Prometheus metrics export.

Provides:
- render_metrics(snapshot, runs, repairs) -> str  (text/plain Prometheus format)
- register_metrics_route(app) — wires GET /metrics onto a Flask app
- Optional bearer auth via env SWEEPER_METRICS_TOKEN
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional


METRICS_HELP = {
    "sweeper_check_status": "Status of each sweeper check (1=ok, 2=warn, 3=crit, 0=disabled/unknown)",
    "sweeper_check_duration_ms": "Duration of the last check in milliseconds",
    "sweeper_pipeline_runs_total": "Total number of pipeline runs",
    "sweeper_pipeline_duration_seconds": "Histogram of pipeline run durations",
    "sweeper_repair_executions_total": "Total number of repair executions",
    "sweeper_repair_success_total": "Total number of successful repair executions",
    "sweeper_defcon": "Current DEFCON level (1-5)",
    "sweeper_self_health": "Self-monitor health (1=ok, 0=degraded)",
}


def _escape_label_value(s: str) -> str:
    """Escape a label value per Prometheus text format."""
    return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")


def render_metrics(
    snapshot: dict[str, Any] | None = None,
    runs_total: int = 0,
    repairs_total: int = 0,
    repairs_success: int = 0,
    pipeline_durations: list[float] | None = None,
    defcon: int = 5,
    self_health: int = 1,
) -> str:
    """Render the current state as a Prometheus text-format response."""
    snapshot = snapshot or {}
    durations = list(pipeline_durations or [])
    lines: list[str] = []

    # Per-check metrics
    for name, h in METRICS_HELP.items():
        if name in (
            "sweeper_check_status",
            "sweeper_check_duration_ms",
        ):
            lines.append(f"# HELP {name} {h}")
            lines.append(f"# TYPE {name} gauge")

    for check_name, status in _walk_checks(snapshot):
        code = {"ok": 1, "warn": 2, "crit": 3}.get(status, 0)
        lines.append(
            f'sweeper_check_status{{check="{_escape_label_value(check_name)}"}} {code}'
        )

    # Pipeline counters
    lines.append(f"# HELP sweeper_pipeline_runs_total {METRICS_HELP['sweeper_pipeline_runs_total']}")
    lines.append("# TYPE sweeper_pipeline_runs_total counter")
    lines.append(f"sweeper_pipeline_runs_total {runs_total}")

    # Histogram of pipeline durations
    lines.append(f"# HELP sweeper_pipeline_duration_seconds {METRICS_HELP['sweeper_pipeline_duration_seconds']}")
    lines.append("# TYPE sweeper_pipeline_duration_seconds histogram")
    buckets = (0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0)
    counts = _bucket_counts(durations, buckets)
    cumulative = 0
    for b, c in zip(buckets, counts):
        cumulative += c
        lines.append(f'sweeper_pipeline_duration_seconds_bucket{{le="{b}"}} {cumulative}')
    lines.append(f'sweeper_pipeline_duration_seconds_bucket{{le="+Inf"}} {len(durations)}')
    lines.append(f"sweeper_pipeline_duration_seconds_sum {sum(durations):.3f}")
    lines.append(f"sweeper_pipeline_duration_seconds_count {len(durations)}")

    # Repair counters
    lines.append(f"# HELP sweeper_repair_executions_total {METRICS_HELP['sweeper_repair_executions_total']}")
    lines.append("# TYPE sweeper_repair_executions_total counter")
    lines.append(f"sweeper_repair_executions_total {repairs_total}")
    lines.append(f"# HELP sweeper_repair_success_total {METRICS_HELP['sweeper_repair_success_total']}")
    lines.append("# TYPE sweeper_repair_success_total counter")
    lines.append(f"sweeper_repair_success_total {repairs_success}")

    # DEFCON + self-health gauges
    lines.append(f"# HELP sweeper_defcon {METRICS_HELP['sweeper_defcon']}")
    lines.append("# TYPE sweeper_defcon gauge")
    lines.append(f"sweeper_defcon {defcon}")
    lines.append(f"# HELP sweeper_self_health {METRICS_HELP['sweeper_self_health']}")
    lines.append("# TYPE sweeper_self_health gauge")
    lines.append(f"sweeper_self_health {self_health}")

    return "\n".join(lines) + "\n"


def _walk_checks(snapshot: dict) -> list[tuple[str, str]]:
    """Yield (check_name, status) for every check in the snapshot."""
    out: list[tuple[str, str]] = []
    checks = snapshot.get("checks") or snapshot.get("results") or {}
    if isinstance(checks, dict):
        for name, body in checks.items():
            status = "unknown"
            if isinstance(body, dict):
                status = body.get("status", "unknown")
            out.append((name, status))
    return out


def _bucket_counts(values: list[float], buckets: tuple[float, ...]) -> list[int]:
    """Count values falling into each bucket (le, exclusive of higher)."""
    return [sum(1 for v in values if v <= b) for b in buckets]


def register_metrics_route(
    app,
    snapshot_provider=None,
) -> None:
    """Register a GET /metrics endpoint on a Flask app.

    Optional bearer auth: if env SWEEPER_METRICS_TOKEN is set, requests
    must include "Authorization: Bearer <token>".

    `snapshot_provider` is an optional callable returning a dict. If not
    provided, an empty dict is used (degraded mode).
    """
    from flask import Response, request, abort

    @app.route("/metrics")
    def _metrics():
        token = os.environ.get("SWEEPER_METRICS_TOKEN", "").strip()
        if token:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {token}":
                abort(401)
        # Best-effort: pull snapshot from the running app
        snapshot = {}
        if snapshot_provider is not None:
            try:
                snapshot = snapshot_provider() or {}
            except Exception:
                snapshot = {}
        body = render_metrics(snapshot=snapshot)
        return Response(body, mimetype="text/plain; version=0.0.4; charset=utf-8")
