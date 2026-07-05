"""Maintenance helpers — hostname validator + secret redactor + safe error response.

Pure-Python module where possible: no Flask views, no global `app` reference.
Flask is required only for `_safe_error_response` (uses `jsonify`) — this is
acceptable since Flask is the host framework anyway.

The parent dashboard.py imports from here and re-exports for back-compat.
"""

from __future__ import annotations

import json as _json
import os
import re
import uuid as _uuid
from pathlib import Path
from typing import Any

import structlog
from flask import jsonify

_log = structlog.get_logger("ipracticom.dashboard.maintenance")

# Hostname validation: prevent path traversal via <host> URL params.
# Mirrors host_config._validate_host_name (kept here to avoid a circular import).
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _validate_hostname(host: str) -> None:
    """Reject hostnames with path-traversal or shell-meta characters.

    Raises ValueError on bad input. Empty string, NUL bytes, slashes, `..`, etc.
    are all rejected.
    """
    if not isinstance(host, str) or not _HOSTNAME_RE.match(host):
        raise ValueError(f"invalid hostname: {host!r}")


# Mirror of agent_api._redact_secrets. Keeps audit-log writes from leaking
# passwords/tokens that operators pass through RepairProposal.kwargs.
_SECRET_KEYS = frozenset({
    "password", "passwd", "pwd", "secret", "token", "api_key",
    "apikey", "access_key", "secret_key", "private_key", "auth",
    "authorization", "credential", "credentials", "ssh_key", "ssl_key",
})


def _redact_secrets(d: dict[str, Any] | None) -> dict[str, Any]:
    """Redact values for keys that look like they carry credentials/secrets.

    Recursively walks dicts and lists. Returns a new dict — does not mutate.
    """
    def scrub(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: ("***REDACTED***" if k.lower() in _SECRET_KEYS else scrub(val))
                    for k, val in v.items()}
        if isinstance(v, list):
            return [scrub(x) for x in v]
        return v

    return scrub(d or {})


# v1.5.9: error sanitization helper for the dashboard. Replaces raw str(e)
# in user-facing responses with a generic "internal_error" message +
# correlation id. The full exception is logged server-side.
_dashboard_logger = structlog.get_logger("ipracticom.dashboard")


def _safe_error_response(exc: BaseException, status: int = 500, extra: dict | None = None) -> tuple[Any, int]:
    """Return a sanitized JSON error response with a correlation id."""
    corr_id = _uuid.uuid4().hex[:8]
    _dashboard_logger.error(
        "dashboard_error_response",
        correlation_id=corr_id,
        error_class=type(exc).__name__,
        error=str(exc))
    body: dict[str, Any] = {
        "error": "internal_error",
        "correlation_id": corr_id,
    }
    if extra:
        body.update(extra)
    return jsonify(body), status


def _save_maintenance_state(host: str, state: dict | None) -> dict | None:
    """Persist a maintenance entry to a JSON sidecar under state dir.

    Lightweight, additive: writes /var/lib/ipracticom-sweeper/maintenance/<host>.json.
    Returns the previous state (or None) for idempotent toggling.
    """
    _validate_hostname(host)
    base = Path(os.environ.get(
        "IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper")) / "maintenance"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{host}.json"
    prev = None
    if path.exists():
        try:
            prev = _json.loads(path.read_text())
        except Exception:
            prev = None
    if state is None:
        if path.exists():
            path.unlink()
    else:
        path.write_text(_json.dumps(state, ensure_ascii=False, default=str))
    return prev


def _get_maintenance_state(host: str) -> dict | None:
    """Read the maintenance state for a host (or None if not under maintenance)."""
    _validate_hostname(host)
    path = Path(os.environ.get(
        "IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper")) / "maintenance" / f"{host}.json"
    if not path.exists():
        return None
    try:
        return _json.loads(path.read_text())
    except Exception:
        return None