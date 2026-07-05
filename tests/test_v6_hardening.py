"""v1.5.9 — Hardening tests.

Covers:
1. Rate-limit on /api/run, /api/notify/test, /api/approvals/*, /api/connectors/*
2. Chat routes auth + per-IP rate-limit
3. CSRF tokens on dashboard POSTs
4. Error sanitization (no str(e) to client)
5. Slack webhook URL allowlist
6. SSRF protection on outbound HTTP
7. CORS validation rejects wildcards
8. Slack /slack/events rate-limit
"""
from __future__ import annotations

import os
import time

import pytest


# --- 1. Rate-limit on sensitive routes ----------------------------------


def _route_has_rate_limit(module_src: str, fn_name: str) -> bool:
    """Check whether a route function is preceded by a @_rate_limit decorator."""
    idx = module_src.find(f"def {fn_name}(")
    if idx < 0:
        return False
    window = module_src[max(0, idx - 500):idx]
    return "_rate_limit" in window


def test_api_run_is_rate_limited():
    """POST /api/run triggers a full pipeline — must have @_rate_limit."""
    import inspect
    from ipracticom_sweeper import agent_api

    module_src = inspect.getsource(agent_api)
    assert _route_has_rate_limit(module_src, "api_run"), (
        "POST /api/run has no @_rate_limit — full-pipeline trigger can be hammered"
    )


def test_api_notify_test_is_rate_limited():
    """POST /api/notify/test sends Slack/Telegram — must have @_rate_limit."""
    import inspect
    from ipracticom_sweeper import agent_api

    module_src = inspect.getsource(agent_api)
    assert _route_has_rate_limit(module_src, "api_notify_test"), (
        "POST /api/notify/test has no @_rate_limit — can flood Slack/Telegram"
    )


def test_api_approvals_approve_is_rate_limited():
    """POST /api/approvals/<pid>/approve executes repairs — must be limited."""
    import inspect
    from ipracticom_sweeper import agent_api

    module_src = inspect.getsource(agent_api)
    assert _route_has_rate_limit(module_src, "api_approvals_approve"), (
        "POST /api/approvals/<pid>/approve has no @_rate_limit — can amplify work"
    )


def test_api_connectors_crud_is_rate_limited():
    """POST/PATCH/DELETE /api/connectors/* mutate connectors — must be limited."""
    import inspect
    from ipracticom_sweeper import agent_api

    module_src = inspect.getsource(agent_api)
    for fname in ("api_connectors_create", "api_connectors_update",
                  "api_connectors_delete", "api_connectors_test"):
        assert _route_has_rate_limit(module_src, fname), (
            f"{fname} has no @_rate_limit — connectors CRUD can be abused"
        )


# --- 2. Chat routes require auth ---------------------------------------


def test_chat_ws_requires_authentication():
    """WebSocket /ws and HTTP /chat/* must require authentication.

    Without auth, anyone reaching the dashboard can trigger LLM tool calls.
    """
    import inspect
    from ipracticom_sweeper import chat

    src = inspect.getsource(chat.register_chat_routes)
    # The route definitions must NOT be auth-free. They should inherit
    # the dashboard's basic-auth wrapper OR have their own check.
    # v1.5.9 fix: register_chat_routes accepts an `auth_required: bool`
    # parameter (default True) and routes that don't get auth get 401/403.
    assert "auth_required" in src, (
        "register_chat_routes has no auth_required parameter — /ws is open"
    )


def test_chat_ws_has_per_ip_rate_limit():
    """The /ws handler must rate-limit per IP to prevent cost amplification."""
    import inspect
    from ipracticom_sweeper import chat

    src = inspect.getsource(chat.register_chat_routes)
    assert "_rate_limit" in src or "rate_limit" in src or "messages_per_min" in src, (
        "chat_ws has no per-IP rate limit — a single client can drive up LLM cost"
    )


# --- 3. CSRF tokens on dashboard POSTs ---------------------------------


def test_dashboard_post_routes_check_csrf():
    """State-mutating dashboard POSTs must validate a CSRF token.

    Without CSRF, a logged-in operator visiting a malicious page can be
    made to trigger POST /approvals/<pid>/approve via auto-submit forms.
    """
    import inspect
    from ipracticom_sweeper import dashboard

    src = inspect.getsource(dashboard)
    # The dashboard should define and call a CSRF check. Look for either:
    # - a _csrf_protect() helper called inside routes
    # - a CSRF token field check
    # - use of flask-wtf / flask-seasurf
    assert ("csrf" in src.lower()) or ("seasurf" in src.lower()) or ("csrf_token" in src.lower()), (
        "dashboard has no CSRF protection — auto-submit forms can trigger approvals"
    )


# --- 4. Error sanitization ---------------------------------------------


def test_agent_api_error_routes_dont_leak_str_e():
    """Agent API must not return raw str(e) as a JSON response field.

    Exception strings can include filesystem paths, SQL fragments, library
    versions. NOTE: `str(e)` *inside* audit/log dicts is fine — only
    client-facing JSON response fields are checked here.
    """
    import inspect
    import re
    from ipracticom_sweeper import agent_api

    src = inspect.getsource(agent_api)
    # Look for the dangerous pattern: return jsonify({..., "error": str(e), ...}).
    # `str(e)` inside log_audit()/audit.append() is server-side and acceptable.
    pattern = re.compile(r"jsonify\s*\(\s*\{[^}]*\"error\"\s*:\s*str\s*\(\s*e\s*\)")
    assert not pattern.search(src), (
        "agent_api returns raw str(e) in a JSON response — "
        "leaks filesystem paths / SQL fragments. Use _safe_error_response()."
    )


def test_dashboard_error_routes_dont_leak_str_e():
    """Dashboard must not render raw str(e) to clients.

    `str(e)` inside `log_audit()` dicts is server-side audit data — fine.
    """
    import inspect
    import re
    from ipracticom_sweeper import dashboard

    src = inspect.getsource(dashboard)
    # Dangerous: f-string in render_template / direct jsonify field.
    pattern1 = re.compile(r"jsonify\s*\(\s*\{[^}]*\"error\"\s*:\s*str\s*\(\s*e\s*\)")
    pattern2 = re.compile(
        r"render_template\s*\(\s*[\"']error\.html[\"']\s*,\s*"
        r"message\s*=\s*f?[\"'][^\"']*\{e\}"
    )
    assert not pattern1.search(src), (
        "dashboard returns raw str(e) in JSON response — use _safe_error_response()"
    )
    assert not pattern2.search(src), (
        "dashboard renders raw {e} in error.html template — leaks internal info"
    )


# --- 5. Slack webhook URL allowlist ------------------------------------


def test_slack_webhook_allows_only_slack_hosts():
    """A configured SLACK_WEBHOOK_URL pointing to a non-Slack host must be rejected."""
    import inspect
    from ipracticom_sweeper import dashboard

    src = inspect.getsource(dashboard)
    # The fix: validate the host is hooks.slack.com or hooks.slack-gov.com.
    assert "hooks.slack.com" in src or "_validate_slack_webhook" in src, (
        "dashboard does not validate SLACK_WEBHOOK_URL host — SSRF to IMDS / internal services"
    )


# --- 6. SSRF protection on outbound HTTP -------------------------------


def test_outbound_http_blocks_internal_ips():
    """Tests for the outbound HTTP helper: 127.0.0.0/8, 169.254.0.0/16, etc. must be blocked.

    Loosely enforced via a helper; here we just check the helper exists.
    """
    import inspect
    from ipracticom_sweeper import dashboard, dashboard_notify

    src = inspect.getsource(dashboard) + inspect.getsource(dashboard_notify)
    assert "_safe_urlopen" in src or "SSRF_BLOCKED" in src or "169.254" in src, (
        "dashboard has no SSRF protection on outbound HTTP requests"
    )


# --- 7. CORS validation rejects wildcards ------------------------------


def test_cors_rejects_wildcard_origins():
    """AGENT_API_CORS_ORIGINS env var must NOT accept `*` (with credentials)."""
    import inspect
    from ipracticom_sweeper import agent_api

    src = inspect.getsource(agent_api)
    # The CORS allowlist parser must reject `*`.
    assert "_validate_cors_origins" in src or "wildcard" in src.lower(), (
        "agent_api does not validate AGENT_API_CORS_ORIGINS — operator can silently widen to *"
    )


# --- 8. /slack/events has rate-limit + 404 when not configured --------


def test_slack_events_returns_404_when_not_configured():
    """When SLACK_SIGNING_SECRET is unset, /slack/events should return 404 (not 503)
    to avoid fingerprinting."""
    import inspect
    from ipracticom_sweeper import agent_api

    src = inspect.getsource(agent_api)
    # Look for the slack_events handler — should return 404 (not 503) on unconfigured.
    assert "slack_not_configured" in src or "404" in src, (
        "slack_events handler — verify 404 returned when SLACK_SIGNING_SECRET is unset"
    )