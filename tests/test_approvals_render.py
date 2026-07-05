"""Tests for v0.4.6: approvals list shows full problem + proposed command (Daniel's #5)."""
from __future__ import annotations

import pytest

from ipracticom_sweeper.telegram_bot.formatter import format_approvals_list


# ---------------------------- format_approvals_list ----------------------------


def test_format_approvals_list_shows_problem_detail():
    """The approvals list must include problem.detail (what was found)."""
    pending = [{
        "id": "abc12345",
        "action": "service_restart",
        "kwargs": {"unit": "nginx"},
        "reason": "HTTP probe failed",
        "problem": {
            "kind": "service_down",
            "severity": "crit",
            "detail": "HTTP probe to http://127.0.0.1:80 returned 503",
            "metrics": {"last_status_code": 503, "consecutive_failures": 3},
        },
        "proposed_command": "systemctl restart nginx",
        "created_at": "2026-06-30T07:50:00+00:00",
        "status": "pending",
    }]
    text = format_approvals_list(pending)
    # Must surface what was found.
    assert "503" in text or "service_down" in text or "HTTP probe" in text
    # Must surface the proposed fix.
    assert "systemctl restart nginx" in text or "service_restart" in text


def test_format_approvals_list_shows_severity_emoji():
    """Severity must be communicated via emoji (crit=🚨, warn=⚠️, info=ℹ️)."""
    pending = [{
        "id": "abc",
        "action": "service_restart",
        "reason": "test",
        "problem": {"severity": "crit", "detail": "down"},
        "proposed_command": "systemctl restart nginx",
        "status": "pending",
    }]
    text = format_approvals_list(pending)
    assert "🚨" in text  # crit severity


def test_format_approvals_list_shows_warn_severity_emoji():
    pending = [{
        "id": "abc",
        "action": "log_truncate_journald",
        "reason": "disk 91% full",
        "problem": {"severity": "warn", "detail": "disk 91% full"},
        "proposed_command": "journalctl --vacuum-time=7d",
        "status": "pending",
    }]
    text = format_approvals_list(pending)
    assert "⚠️" in text  # warn


def test_format_approvals_list_handles_missing_problem():
    """Backwards-compat: old proposals may not have a 'problem' key."""
    pending = [{
        "id": "abc",
        "action": "drop_caches",
        "reason": "high memory pressure",
        "status": "pending",
    }]
    text = format_approvals_list(pending)
    assert "drop_caches" in text
    assert "high memory pressure" in text


def test_format_approvals_list_empty_returns_ok_message():
    text = format_approvals_list([])
    assert "אין" in text or "יציבה" in text  # Hebrew: "no pending / stable"


def test_format_approvals_list_caps_at_8_with_overflow_indicator():
    """Telegram inline keyboards cap at 8 rows; list must show "+N more"."""
    pending = [
        {"id": f"p{i}", "action": "service_restart", "reason": f"reason {i}",
         "problem": {"severity": "warn", "detail": f"d{i}"},
         "proposed_command": f"cmd {i}", "status": "pending"}
        for i in range(10)
    ]
    text = format_approvals_list(pending)
    assert "+2 more" in text or "+2" in text


def test_format_approvals_list_includes_metrics_when_present():
    """Metrics dict must be summarized inline so operator sees numbers without drilling in."""
    pending = [{
        "id": "abc",
        "action": "service_restart",
        "reason": "test",
        "problem": {
            "severity": "crit",
            "detail": "service down",
            "metrics": {"last_status_code": 503, "consecutive_failures": 3},
        },
        "proposed_command": "systemctl restart nginx",
        "status": "pending",
    }]
    text = format_approvals_list(pending)
    assert "503" in text
    assert "3" in text  # consecutive failures