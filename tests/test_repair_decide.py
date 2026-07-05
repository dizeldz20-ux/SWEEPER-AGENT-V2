"""Tests for the shared approval-decision helper (repair/decide.py)."""
from __future__ import annotations

import pytest

from ipracticom_sweeper.repair import block, decide
from ipracticom_sweeper.repair import pending as pm


def _mk(server="h1", action="drop_caches"):
    return pm.create_proposal(
        action=action, kwargs={"level": 3}, reason="r",
        proposed_command="c", server=server,
    )


def _fake_result(success: bool):
    return type("R", (), {
        "action": "drop_caches", "target": "mem", "success": success,
        "message": "ok" if success else "nope",
        "error": None if success else "boom", "rollback_available": False,
    })()


def test_approve_success_pushes_result_no_block(monkeypatch):
    p = _mk()
    pushed = []

    async def fake_notify(proposal, result, ok):
        pushed.append((proposal, result, ok))
        return {}

    monkeypatch.setattr("ipracticom_sweeper.notify.approvals.notify_approval_result", fake_notify)
    monkeypatch.setattr("ipracticom_sweeper.repair.actions.execute_repair", lambda a, **k: _fake_result(True))

    out = decide.approve(p.id, actor="daniel")
    assert out["ok"] is True
    assert out["status"] == "executed"
    assert len(pushed) == 1 and pushed[0][2] is True
    assert pushed[0][1]["operator"] == "daniel"
    assert block.is_blocked("drop_caches", "h1") is False


def test_approve_failure_blocks_and_escalates(monkeypatch):
    p = _mk()
    pushed = []

    async def fake_notify(proposal, result, ok):
        pushed.append((proposal, result, ok))
        return {}

    monkeypatch.setattr("ipracticom_sweeper.notify.approvals.notify_approval_result", fake_notify)
    monkeypatch.setattr("ipracticom_sweeper.repair.actions.execute_repair", lambda a, **k: _fake_result(False))

    out = decide.approve(p.id, actor="daniel")
    assert out["ok"] is False
    assert out["status"] == "failed"
    assert len(pushed) == 1 and pushed[0][2] is False
    assert block.is_blocked("drop_caches", "h1") is True   # escalation block set


def test_reject_notifies_and_no_block(monkeypatch):
    p = _mk()
    pushed = []

    async def fake_rej(proposal, reason, actor):
        pushed.append((proposal, reason, actor))
        return {}

    monkeypatch.setattr("ipracticom_sweeper.notify.approvals.notify_approval_rejected", fake_rej)

    out = decide.reject(p.id, actor="daniel", reason="not needed")
    assert out["ok"] is True
    assert out["status"] == "rejected"
    assert len(pushed) == 1 and pushed[0][1] == "not needed"
    assert block.is_blocked("drop_caches", "h1") is False   # rejection never blocks


def test_reject_requires_reason(monkeypatch):
    p = _mk()
    out = decide.reject(p.id, actor="daniel", reason="  ")
    assert out["ok"] is False
    assert out["error"] == "reason_required"


def test_approve_missing_returns_not_found():
    out = decide.approve("deadbeef", actor="x")
    assert out["ok"] is False
    assert out["error"] == "not_found"


def test_approve_already_decided_returns_conflict(monkeypatch):
    p = _mk()
    pm.set_status(p.id, "executed")
    out = decide.approve(p.id, actor="x")
    assert out["ok"] is False
    assert out["error"] == "already_decided"
