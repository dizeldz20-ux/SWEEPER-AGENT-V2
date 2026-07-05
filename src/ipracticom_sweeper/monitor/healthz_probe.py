"""Sprint 12.1 — active HTTP /healthz probe.

Issues an HTTP GET to a service's configured healthz endpoint.
- 2xx → ok
- 5xx → warn
- timeout / connection error / non-2xx (other) → crit (hung)
- Disabled when healthz_path not configured.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class HealthzResult:
    status: str
    status_code: Optional[int]
    latency_ms: Optional[float]
    url: str
    error: str = ""


def probe_healthz(
    url: str,
    timeout: float = 5.0,
    latency_warn_ms: float = 2000.0,
) -> HealthzResult:
    """Probe a single healthz URL."""
    if not url:
        return HealthzResult(
            status="disabled",
            status_code=None,
            latency_ms=None,
            url="",
            error="no_url_configured",
        )
    started = time.time()
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            elapsed_ms = (time.time() - started) * 1000
            if 200 <= code < 300:
                if elapsed_ms > latency_warn_ms:
                    return HealthzResult(
                        status="warn",
                        status_code=code,
                        latency_ms=elapsed_ms,
                        url=url,
                        error="slow_response",
                    )
                return HealthzResult(
                    status="ok",
                    status_code=code,
                    latency_ms=elapsed_ms,
                    url=url,
                )
            if 500 <= code < 600:
                return HealthzResult(
                    status="warn",
                    status_code=code,
                    latency_ms=elapsed_ms,
                    url=url,
                )
            return HealthzResult(
                status="crit",
                status_code=code,
                latency_ms=elapsed_ms,
                url=url,
            )
    except urllib.error.HTTPError as e:
        elapsed_ms = (time.time() - started) * 1000
        # HTTPError is also a response with a status code
        code = e.code
        if 500 <= code < 600:
            return HealthzResult(status="warn", status_code=code, latency_ms=elapsed_ms, url=url, error=str(e.reason)[:200])
        return HealthzResult(status="crit", status_code=code, latency_ms=elapsed_ms, url=url, error=str(e.reason)[:200])
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        elapsed_ms = (time.time() - started) * 1000
        return HealthzResult(
            status="crit",
            status_code=None,
            latency_ms=elapsed_ms,
            url=url,
            error=str(e)[:200],
        )


def probe_healthz_list(
    services: list[dict],
    timeout: float = 5.0,
) -> list[HealthzResult]:
    """Probe each service in the list.

    Each service dict: {"name": "x", "url": "http://..."}
    Services without a url are reported as disabled.
    """
    results: list[HealthzResult] = []
    for svc in services:
        url = svc.get("healthz") or svc.get("url") or ""
        results.append(probe_healthz(url, timeout=timeout))
    return results
