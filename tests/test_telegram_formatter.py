"""Tests for v0.4.2 formatters."""
from __future__ import annotations

import pytest

from ipracticom_sweeper.telegram_bot.formatter import (
    DEFCON_EMOJI,
    escape_html,
    format_approval_result,
    format_approvals_list,
    format_connector_detail,
    format_connectors_list,
    format_dashboard,
    format_error,
    format_fleet_host,
    format_fleet_list,
    format_history_catalog,
    format_problems,
    format_security,
    format_snapshot,
)


# ---------------------------- v0.4.1 still works ----------------------------

def test_escape_html_escapes_metachars():
    assert escape_html("<b>&") == "&lt;b&gt;&amp;"
    assert escape_html('"x"') == "&quot;x&quot;"


def test_format_snapshot_no_problems():
    out = format_snapshot({"defcon": 5, "modules": {}})
    assert "DEFCON 5" in out
    assert "אין בעיות" in out


def test_format_snapshot_with_problems():
    out = format_snapshot({
        "defcon": 3,
        "modules": {"disk": {"status": "warn", "details": "/ 92%"}},
    })
    assert "DEFCON 3" in out
    assert "disk" in out
    assert "92%" in out


def test_format_snapshot_truncates_at_5_problems():
    mods = {f"m{i}": {"status": "warn", "details": "x"} for i in range(10)}
    out = format_snapshot({"defcon": 3, "modules": mods})
    assert "...+5 more" in out


def test_format_problems_empty():
    out = format_problems({"defcon": 5, "modules": {}})
    assert "אין בעיות" in out


def test_format_security_summary():
    out = format_security({
        "ssh_drift": ["a", "b", "c", "d", "e"],
        "suid_changes": [],
        "ports": [{"port": 22, "service": "ssh"}],
    })
    assert "SSH config drift" in out
    assert "+2 more" in out
    assert "ports" in out.lower()


def test_format_error_empty():
    assert "שגיאה" in format_error()


def test_format_error_with_reason():
    out = format_error("timeout")
    assert "timeout" in out


# ---------------------------- v0.4.2 new formatters ----------------------------

def test_format_dashboard_delegates_to_snapshot():
    snap = {"defcon": 4, "modules": {}}
    assert format_dashboard(snap) == format_snapshot(snap)


def test_format_history_catalog_empty():
    out = format_history_catalog({"metrics": [], "hosts": []})
    assert "אין מטריקות" in out


def test_format_history_catalog_with_metrics():
    out = format_history_catalog({
        "metrics": ["cpu_percent", "memory_percent"],
        "hosts": ["localhost"],
    })
    assert "cpu_percent" in out
    assert "memory_percent" in out


def test_format_history_catalog_truncates_at_20():
    out = format_history_catalog({
        "metrics": [f"m{i}" for i in range(30)],
        "hosts": [],
    })
    assert "+10 more" in out


def test_format_approvals_list_empty():
    out = format_approvals_list([])
    assert "אין תיקונים" in out


def test_format_approvals_list_with_proposals():
    pending = [
        {"id": "abc123def456", "action": "service_restart", "reason": "nginx down"},
        {"id": "xyz789ghi012", "action": "drop_caches", "reason": "high memory"},
    ]
    out = format_approvals_list(pending)
    assert "2 תיקונים" in out
    assert "abc123de" in out  # first 8 chars
    assert "service_restart" in out


def test_format_approval_result_success():
    out = format_approval_result({
        "ok": True, "status": "executed", "message": "service restarted"
    })
    assert "✅" in out
    assert "executed" in out


def test_format_approval_result_failure():
    out = format_approval_result({
        "ok": False, "status": "failed", "error": "unit not found"
    })
    assert "❌" in out


def test_format_connectors_list_empty():
    out = format_connectors_list([])
    assert "אין מחברים" in out


def test_format_connectors_list_with_data():
    out = format_connectors_list([
        {"name": "prod-web", "instance_id": "i-1234", "region": "il-central-1", "status": "ok"},
        {"name": "prod-db", "instance_id": "i-5678", "region": "il-central-1", "status": "error"},
    ])
    assert "prod-web" in out
    assert "prod-db" in out
    assert "i-1234" in out


def test_format_connector_detail():
    out = format_connector_detail({
        "name": "prod-web",
        "instance_id": "i-1234",
        "region": "il-central-1",
        "status": "ok",
        "tags": {"env": "prod"},
        "last_collected_at": 1782761586.0,
        "last_error": None,
    })
    assert "prod-web" in out
    assert "i-1234" in out
    assert "env=prod" in out


def test_format_fleet_list_empty():
    out = format_fleet_list([])
    assert "אין מארחים" in out


def test_format_fleet_list_with_hosts():
    out = format_fleet_list([
        {"name": "local", "kind": "local", "status": "ok"},
        {"name": "prod", "kind": "connector", "status": "warn"},
    ])
    assert "local" in out
    assert "prod" in out


def test_format_fleet_host_local():
    out = format_fleet_host({
        "name": "local", "kind": "local", "status": "ok",
        "defcon": 4, "problems_found": 1, "repairs_attempted": 0,
        "last_seen": "2026-06-29T19:33:06+00:00",
    })
    assert "local" in out
    assert "defcon: 4" in out
    assert "problems: 1" in out


def test_format_fleet_host_connector():
    out = format_fleet_host({
        "name": "prod", "kind": "connector", "status": "error",
        "instance_id": "i-aaaa", "region": "il-central-1",
        "last_error": "SSM unreachable",
    })
    assert "prod" in out
    assert "i-aaaa" in out
    assert "SSM unreachable" in out