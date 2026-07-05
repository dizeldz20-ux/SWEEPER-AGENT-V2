"""Diagnose HTTP endpoint results.

Converts a list of HttpEndpointResult into Problem objects for the
diagnosis engine. Rules (thresholds) come from the YAML config.
"""
from __future__ import annotations
from typing import Any

from ipracticom_sweeper.diagnose.engine import Problem, RepairSafety


def diagnose_http(findings: dict, rules: dict) -> list[Problem]:
    """Analyze HTTP endpoint results and produce problems.

    Defaults (overridable via rules["http"]):
        slow_response_ms: 2000
    """
    http_rules = rules.get("http", {})
    slow_ms = http_rules.get("slow_response_ms", 2000)

    metrics = findings.get("metrics", {})
    endpoints = metrics.get("endpoints", [])

    problems: list[Problem] = []
    for ep in endpoints:
        name = ep.get("name", ep.get("url", "endpoint"))
        status = ep.get("status_code")
        rt_ms = ep.get("response_time_ms")
        error = ep.get("error")

        # Transport error (no status) → CRIT
        if error:
            problems.append(Problem(
                module="http",
                kind="http_endpoint_unreachable",
                severity="crit",
                detail=f"Endpoint {name} unreachable: {error}",
                metrics={"url": ep.get("url"), "error": error},
                suggested_repair="notify_human",
                repair_safety=RepairSafety.NEVER,
            ))
            continue

        # 5xx → CRIT
        if status is not None and 500 <= status < 600:
            problems.append(Problem(
                module="http",
                kind="http_endpoint_server_error",
                severity="crit",
                detail=f"Endpoint {name} returned {status}",
                metrics={"url": ep.get("url"), "status_code": status},
                suggested_repair="notify_human",
                repair_safety=RepairSafety.NEVER,
            ))
            continue

        # 4xx → WARN
        if status is not None and 400 <= status < 500:
            problems.append(Problem(
                module="http",
                kind="http_endpoint_client_error",
                severity="warn",
                detail=f"Endpoint {name} returned {status}",
                metrics={"url": ep.get("url"), "status_code": status},
                suggested_repair="notify_human",
                repair_safety=RepairSafety.NEVER,
            ))
            continue

        # Slow response → WARN
        if rt_ms is not None and rt_ms > slow_ms:
            problems.append(Problem(
                module="http",
                kind="http_endpoint_slow",
                severity="warn",
                detail=f"Endpoint {name} slow: {rt_ms}ms (threshold {slow_ms}ms)",
                metrics={"url": ep.get("url"), "response_time_ms": rt_ms, "threshold_ms": slow_ms},
                suggested_repair="notify_human",
                repair_safety=RepairSafety.NEVER,
            ))

    return problems
