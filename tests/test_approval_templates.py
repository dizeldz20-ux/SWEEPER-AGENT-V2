"""Unit tests for the unified bilingual approval message templates."""
from __future__ import annotations

from ipracticom_sweeper.notify import templates


def _proposal() -> dict:
    return {
        "id": "a1b2c3d4",
        "action": "log_truncate_journald",
        "kwargs": {"max_age_days": 7},
        "reason": "disk 94% on /var",
        "problem": {
            "kind": "disk",
            "severity": "crit",
            "detail": "disk 94% on /var",
            "metrics": {"disk.used_percent": 94},
        },
        "proposed_command": "journalctl --vacuum-time=7d\n  → deletes logs older than 7d",
        "server": "prod-pbx-01",
        "status": "pending",
    }


def test_request_text_has_host_action_and_id():
    t = templates.approval_request_text(_proposal())
    assert "prod-pbx-01" in t
    assert "log_truncate_journald" in t
    assert "a1b2c3d4" in t


def test_request_text_is_bilingual():
    t = templates.approval_request_text(_proposal())
    assert "אישור" in t          # Hebrew
    assert "Approval" in t        # English


def test_request_text_shows_fix_and_detection():
    t = templates.approval_request_text(_proposal())
    assert "journalctl" in t      # proposed fix
    assert "94" in t              # detection metric


def test_request_text_unknown_host_when_missing_server():
    p = _proposal()
    p["server"] = ""
    t = templates.approval_request_text(p)
    assert "unknown" in t.lower()


def test_result_text_success():
    p = _proposal()
    r = {"action": p["action"], "success": True, "message": "freed 3.2GB"}
    t = templates.approval_result_text(p, r, ok=True)
    assert "✅" in t
    assert "prod-pbx-01" in t
    assert "freed 3.2GB" in t


def test_result_text_failure_has_human_escalation():
    p = _proposal()
    r = {"action": p["action"], "success": False, "error": "exit 1"}
    t = templates.approval_result_text(p, r, ok=False)
    assert "בן אדם" in t          # Hebrew escalation
    assert "Human" in t           # English escalation
    assert "exit 1" in t


def test_request_blocks_has_action_buttons_with_pid():
    blocks = templates.approval_request_blocks(_proposal())
    assert isinstance(blocks, list)
    values, action_ids = [], []
    for b in blocks:
        if b.get("type") == "actions":
            for el in b.get("elements", []):
                values.append(el.get("value"))
                action_ids.append(el.get("action_id"))
    assert "a1b2c3d4" in values
    assert "approve" in action_ids
    assert "reject" in action_ids


def test_result_blocks_failure_mentions_human():
    p = _proposal()
    r = {"success": False, "error": "boom"}
    blocks = templates.approval_result_blocks(p, r, ok=False)
    blob = str(blocks)
    assert "בן אדם" in blob or "Human" in blob
    assert "boom" in blob
