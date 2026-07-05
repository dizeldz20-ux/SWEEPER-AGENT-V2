"""Tests for escalation + blocked endpoints on the JSON API."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from ipracticom_sweeper.agent_api import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def pending_tmp(tmp_path, monkeypatch):
    pending_dir = tmp_path / "pending"
    from ipracticom_sweeper.repair import pending as pending_mod
    monkeypatch.setattr(pending_mod, "PENDING_DIR", pending_dir)
    monkeypatch.setattr(pending_mod, "APPROVED_DIR", pending_dir / "approved")
    monkeypatch.setattr(pending_mod, "REJECTED_DIR", pending_dir / "rejected")
    monkeypatch.setattr(pending_mod, "AUDIT_LOG", tmp_path / "audit" / "repairs.jsonl")
    return pending_dir


def _fail_result():
    return type("R", (), {
        "action": "service_restart", "target": "x", "success": False,
        "message": "no", "error": "boom", "rollback_available": False,
        "duration_ms": 1, "snapshot_id": None,
    })()


def test_approve_failure_creates_block_and_notifies(client, pending_tmp, monkeypatch):
    from ipracticom_sweeper.repair.pending import create_proposal
    from ipracticom_sweeper.repair import block

    p = create_proposal(
        action="service_restart", kwargs={"unit": "x"}, reason="r",
        proposed_command="c", server="web-9",
    )

    pushed = []

    async def fake_notify(proposal, result, ok):
        pushed.append(ok)
        return {}

    monkeypatch.setattr("ipracticom_sweeper.notify.approvals.notify_approval_result", fake_notify)
    with patch("ipracticom_sweeper.repair.actions.execute_repair", return_value=_fail_result()):
        r = client.post(f"/api/approvals/{p.id}/approve")

    assert r.status_code == 200
    assert r.get_json()["ok"] is False
    assert block.is_blocked("service_restart", "web-9") is True   # escalation block
    assert pushed == [False]                                      # failure push fired


def test_approve_success_notifies_no_block(client, pending_tmp, monkeypatch):
    from ipracticom_sweeper.repair.pending import create_proposal
    from ipracticom_sweeper.repair import block

    p = create_proposal(
        action="drop_caches", kwargs={"level": 3}, reason="r",
        proposed_command="c", server="web-9",
    )
    ok_result = type("R", (), {
        "action": "drop_caches", "target": "mem", "success": True,
        "message": "freed", "error": None, "rollback_available": False,
        "duration_ms": 1, "snapshot_id": None,
    })()

    pushed = []

    async def fake_notify(proposal, result, ok):
        pushed.append(ok)
        return {}

    monkeypatch.setattr("ipracticom_sweeper.notify.approvals.notify_approval_result", fake_notify)
    with patch("ipracticom_sweeper.repair.actions.execute_repair", return_value=ok_result):
        r = client.post(f"/api/approvals/{p.id}/approve")

    assert r.get_json()["ok"] is True
    assert block.is_blocked("drop_caches", "web-9") is False
    assert pushed == [True]


def test_blocked_list_and_unblock(client, pending_tmp):
    from ipracticom_sweeper.repair import block

    block.block("drop_caches", "h5", reason="prev failure")

    r = client.get("/api/approvals/blocked")
    assert r.status_code == 200
    body = r.get_json()
    assert body["count"] == 1
    entry = body["blocked"][0]
    assert entry["action"] == "drop_caches"
    assert entry["server"] == "h5"
    key = entry["key"]

    r2 = client.delete(f"/api/approvals/blocked/{key}")
    assert r2.status_code == 200
    assert block.is_blocked("drop_caches", "h5") is False

    r3 = client.delete(f"/api/approvals/blocked/{key}")
    assert r3.status_code == 404
