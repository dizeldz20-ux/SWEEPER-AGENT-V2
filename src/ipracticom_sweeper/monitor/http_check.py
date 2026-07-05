"""HTTP endpoint healthcheck collector.

Probes a list of HTTP(S) endpoints and reports status code, response
time, and any transport errors. Used to detect site outages and slow
upstream services.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import httpx


@dataclass
class HttpEndpointResult:
    """Result of probing a single HTTP endpoint."""

    name: str
    url: str
    status_code: int | None
    response_time_ms: int | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "status_code": self.status_code,
            "response_time_ms": self.response_time_ms,
            "error": self.error,
        }


def _probe_one(endpoint: dict) -> HttpEndpointResult:
    """Probe a single endpoint. Returns a result with status/error populated."""
    url = endpoint.get("url", "")
    name = endpoint.get("name", url)
    timeout = endpoint.get("timeout", 5.0)
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        elapsed = resp.elapsed.total_seconds() * 1000 if resp.elapsed else 0
        return HttpEndpointResult(
            name=name,
            url=url,
            status_code=resp.status_code,
            response_time_ms=int(elapsed),
            error=None,
        )
    except httpx.HTTPError as e:
        return HttpEndpointResult(
            name=name,
            url=url,
            status_code=None,
            response_time_ms=None,
            error=f"{type(e).__name__}: {e}",
        )


def collect_http_endpoints(endpoints: list[dict]) -> list[HttpEndpointResult]:
    """Probe a list of HTTP endpoints sequentially.

    Each endpoint is a dict: {url, name?, timeout?}.
    Returns a list of HttpEndpointResult, one per endpoint, in the same order.
    Transport errors are caught and reported, not raised.
    """
    return [_probe_one(ep) for ep in endpoints]


def evaluate(values: dict, rules: dict) -> str:
    """Return overall status: 'ok' | 'warn' | 'crit'.

    'crit' if any endpoint unreachable or 5xx, 'warn' if any 4xx or slow.
    """
    endpoints = values.get("endpoints", [])
    if not endpoints:
        return "ok"
    has_crit = False
    has_warn = False
    for ep in endpoints:
        if ep.get("error"):
            has_crit = True
        elif ep.get("status_code") is not None and 500 <= ep["status_code"] < 600:
            has_crit = True
        elif ep.get("status_code") is not None and 400 <= ep["status_code"] < 500:
            has_warn = True
        else:
            slow_ms = rules.get("http", {}).get("slow_response_ms", 2000)
            if ep.get("response_time_ms") and ep["response_time_ms"] > slow_ms:
                has_warn = True
    if has_crit:
        return "crit"
    if has_warn:
        return "warn"
    return "ok"
