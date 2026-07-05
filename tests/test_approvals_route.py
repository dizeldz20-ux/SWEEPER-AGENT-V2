"""Tests for /approvals dashboard routes."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ipracticom_sweeper.dashboard import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def pending_tmp(tmp_path, monkeypatch):
    """Use a tmp directory for pending repairs."""
    pending_dir = tmp_path / "pending"
    approved = pending_dir / "approved"
    rejected = pending_dir / "rejected"
    audit = tmp_path / "audit" / "repairs.jsonl"

    from ipracticom_sweeper.repair import pending as pending_mod
    monkeypatch.setattr(pending_mod, "PENDING_DIR", pending_dir)
    monkeypatch.setattr(pending_mod, "APPROVED_DIR", approved)
    monkeypatch.setattr(pending_mod, "REJECTED_DIR", rejected)
    monkeypatch.setattr(pending_mod, "AUDIT_LOG", audit)

    return {"pending": pending_dir, "approved": approved, "rejected": rejected, "audit": audit}


def test_approvals_list_empty(client, pending_tmp):
    r = client.get("/approvals")
    assert r.status_code == 200
    assert "אין תיקונים הממתינים לאישור" in r.get_data(as_text=True)


def test_approvals_list_shows_pending(client, pending_tmp):
    from ipracticom_sweeper.repair.pending import create_proposal
    create_proposal(
        action="service_restart", kwargs={"unit": "nginx"},
        reason="nginx down", proposed_command="systemctl restart nginx",
    )
    r = client.get("/approvals")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "service_restart" in body
    assert "nginx down" in body


def test_approval_detail_page(client, pending_tmp):
    from ipracticom_sweeper.repair.pending import create_proposal
    p = create_proposal(
        action="service_restart", kwargs={"unit": "postgres"},
        reason="postgres unresponsive", proposed_command="systemctl restart postgres",
        problem={"kind": "service_down", "severity": "crit", "detail": "503 errors"},
    )
    r = client.get(f"/approvals/{p.id}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "postgres" in body
    assert "503 errors" in body
    assert "אשר והפעל" in body
    assert "דחה" in body


def test_approval_approve_executes_and_logs(client, pending_tmp, monkeypatch):
    from ipracticom_sweeper.repair.pending import create_proposal
    p = create_proposal(
        action="service_restart", kwargs={"unit": "fake-unit"},
        reason="test", proposed_command="systemctl restart fake-unit",
    )

    # Mock execute_repair to avoid actually restarting anything
    fake_result = type("R", (), {
        "action": "service_restart", "target": "fake-unit", "success": True,
        "snapshot_id": "snap-123", "message": "restarted", "error": None,
        "duration_ms": 42,
    })()

    with patch("ipracticom_sweeper.repair.execute_repair", return_value=fake_result):
        r = client.post(f"/approvals/{p.id}/approve", follow_redirects=False)

    # Should redirect to dashboard
    assert r.status_code == 302
    # Proposal should be archived to approved/
    assert (pending_tmp["approved"] / f"{p.id}.json").exists()
    assert not (pending_tmp["pending"] / f"{p.id}.json").exists()
    # Audit log should contain approved + executed entries
    audit_lines = pending_tmp["audit"].read_text().splitlines()
    audit_kinds = [json.loads(line)["kind"] for line in audit_lines]
    assert "repair_approved" in audit_kinds
    assert "repair_executed" in audit_kinds


def test_approval_reject_archives_without_executing(client, pending_tmp):
    from ipracticom_sweeper.repair.pending import create_proposal
    p = create_proposal(
        action="service_restart", kwargs={"unit": "x"},
        reason="test", proposed_command="c",
    )
    r = client.post(f"/approvals/{p.id}/reject", data={"reason": "not needed"}, follow_redirects=False)
    assert r.status_code == 302
    assert (pending_tmp["rejected"] / f"{p.id}.json").exists()
    assert not (pending_tmp["pending"] / f"{p.id}.json").exists()
    audit_lines = pending_tmp["audit"].read_text().splitlines()
    assert json.loads(audit_lines[-1])["kind"] == "repair_rejected"


def test_approval_approve_404_for_missing(client, pending_tmp):
    r = client.post("/approvals/nonexistent/approve")
    assert r.status_code == 404


def test_approval_double_action_blocked(client, pending_tmp):
    from ipracticom_sweeper.repair.pending import create_proposal, set_status, archive
    p = create_proposal(
        action="service_restart", kwargs={"unit": "x"},
        reason="r", proposed_command="c",
    )
    # Simulate "already rejected" — update status BEFORE archiving so the
    # on-disk proposal reflects reality (status field is what the route reads).
    set_status(p.id, "rejected")
    archive(p.id, "rejected")
    r = client.post(f"/approvals/{p.id}/approve")
    assert r.status_code == 409