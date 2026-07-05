"""Regression for v1.5.12 round-2 dashboard split.

Splits the bulk of dashboard.py (2158 lines) into two sibling helper modules:
  - dashboard_notify.py      → notifications env + telegram/slack tests
  - dashboard_maintenance.py → hostname validator + actor + secret redactor
                              + safe error response

Both helper modules MUST be importable without Flask app context and MUST
expose the same public symbols that dashboard.py re-exports so existing
tests that patch `ipracticom_sweeper.dashboard._validate_slack_webhook_url`
keep working.
"""

import importlib
import re


def test_dashboard_notify_module_imports_without_flask():
    """dashboard_notify is a pure-Python module — no Flask app required."""
    mod = importlib.import_module("ipracticom_sweeper.dashboard_notify")
    assert mod is not None
    # Pure module — must not bring Flask views into its namespace.
    assert not hasattr(mod, "app"), "dashboard_notify must not hold a Flask app"


def test_dashboard_maintenance_module_imports_without_flask():
    mod = importlib.import_module("ipracticom_sweeper.dashboard_maintenance")
    assert mod is not None
    assert not hasattr(mod, "app"), "dashboard_maintenance must not hold a Flask app"


def test_notify_exposes_public_symbols_via_dashboard():
    """dashboard.py keeps back-compat aliases so old `from .dashboard import`
    code paths keep working."""
    dash = importlib.import_module("ipracticom_sweeper.dashboard")
    for name in (
        "_read_notifications_env",
        "_write_notifications_env",
        "_test_telegram",
        "_validate_slack_webhook_url",
        "_test_slack",
        "NOTIFICATIONS_ENV_FILE",
    ):
        assert hasattr(dash, name), f"dashboard lost public symbol {name}"


def test_maintenance_exposes_public_symbols_via_dashboard():
    dash = importlib.import_module("ipracticom_sweeper.dashboard")
    for name in (
        "_validate_hostname",
        "_actor_from_request",
        "_redact_secrets",
        "_safe_error_response",
        "_HOSTNAME_RE",
        "_SECRET_KEYS",
    ):
        assert hasattr(dash, name), f"dashboard lost public symbol {name}"


def test_validate_slack_webhook_url_blocks_ssrf_attempts():
    """The validator must reject non-Slack URLs with the SSRF_BLOCKED marker.

    The split MUST preserve this critical security check — regressions here
    would let attackers repoint notifications at attacker-controlled endpoints.
    """
    from ipracticom_sweeper.dashboard_notify import _validate_slack_webhook_url

    # Allowed
    ok, _ = _validate_slack_webhook_url(
        "https://hooks.slack.com/services/T000/B000/XXX"
    )
    assert ok is True, "real Slack URL should pass"

    # Blocked: wrong scheme
    ok, msg = _validate_slack_webhook_url("http://hooks.slack.com/services/T000/B000/XXX")
    assert ok is False and "SSRF_BLOCKED" in msg

    # Blocked: wrong host
    ok, msg = _validate_slack_webhook_url("https://evil.com/services/T000/B000/XXX")
    assert ok is False and "SSRF_BLOCKED" in msg

    # Blocked: wrong path
    ok, msg = _validate_slack_webhook_url("https://hooks.slack.com/other/path")
    assert ok is False

    # Empty allowed
    ok, _ = _validate_slack_webhook_url("")
    assert ok is True


def test_validate_hostname_rejects_path_traversal():
    """Hostname validator must reject path-traversal-style hosts.

    The split MUST keep this validator strict — it's the gatekeeper for the
    agent API's hostname parameter on the machine actions.
    """
    from ipracticom_sweeper.dashboard_maintenance import _validate_hostname

    # Allowed
    _validate_hostname("pbx-01.example.com")
    _validate_hostname("host_1")

    # Blocked
    for bad in ("../etc/passwd", "host; rm -rf /", "", "a" * 100, "host name"):
        try:
            _validate_hostname(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"_validate_hostname({bad!r}) should have raised")


def test_safe_error_response_returns_correlation_id_not_raw_error():
    """Client-facing errors must NOT leak raw exception strings.

    Returns correlation_id for support lookup, never str(exc).
    """
    from flask import Flask
    from ipracticom_sweeper.dashboard_maintenance import _safe_error_response

    app = Flask(__name__)
    with app.app_context():
        response, status = _safe_error_response(RuntimeError("secret internal detail"))
    assert status >= 400
    payload = response.get_json()
    assert "correlation_id" in payload
    # Raw error message must NOT appear in any user-facing field
    serialized = str(payload)
    assert "secret internal detail" not in serialized


def test_dashboard_under_2000_lines_after_split():
    """After both rounds, dashboard.py should drop below 2000 lines.

    Round 1 (v1.5.12): -23 lines via dashboard_helpers.
    Round 2 (this release): notify (-140) + maintenance (-65) → ~-205 more.
    """
    import pathlib

    p = pathlib.Path(
        importlib.import_module("ipracticom_sweeper.dashboard").__file__
    )
    lines = len(p.read_text().splitlines())
    # Allow some slack — final number should be ~1920, cap at 2000 to detect drift.
    assert lines < 2000, f"dashboard.py still {lines} lines; expected <2000"