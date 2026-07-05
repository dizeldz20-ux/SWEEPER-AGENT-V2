"""Slack interactive approve/reject wired to the real decision flow."""
from __future__ import annotations

import pytest

from ipracticom_sweeper.repair import block
from ipracticom_sweeper.repair import pending as pm
from ipracticom_sweeper.slack_actions.commands import SlackCommandHandler
from ipracticom_sweeper.slack_actions.endpoint import SlackEndpoint
from ipracticom_sweeper.slack_actions.handler import (
    SlackAction,
    SlackActionHandler,
    SlackActionType,
)


def _mk(server="h1", action="drop_caches"):
    return pm.create_proposal(
        action=action, kwargs={"level": 3}, reason="r",
        proposed_command="c", server=server,
    )


@pytest.fixture
def stub_exec(monkeypatch):
    """Stub execute_repair (success) and silence the fan-out."""
    def _apply(success=True):
        fake = type("R", (), {
            "action": "drop_caches", "target": "mem", "success": success,
            "message": "ok" if success else "no",
            "error": None if success else "boom", "rollback_available": False,
        })()
        monkeypatch.setattr("ipracticom_sweeper.repair.actions.execute_repair", lambda a, **k: fake)

        async def noop(*a, **k):
            return {}

        monkeypatch.setattr("ipracticom_sweeper.notify.approvals.notify_approval_result", noop)
        monkeypatch.setattr("ipracticom_sweeper.notify.approvals.notify_approval_rejected", noop)
    return _apply


def test_endpoint_maps_approve_reject_action_ids():
    ep = SlackEndpoint()
    payload = {
        "type": "block_actions",
        "user": {"username": "daniel"},
        "actions": [{"action_id": "approve", "value": "pid9"}],
    }
    action = ep.payload_to_action(payload, timestamp=0.0)
    assert action.action_type == SlackActionType.APPROVE
    assert action.fingerprint == "pid9"
    assert action.user == "daniel"


def test_block_action_approve_executes(stub_exec):
    stub_exec(success=True)
    p = _mk()
    h = SlackActionHandler()
    res = h.handle(SlackAction(SlackActionType.APPROVE, p.id, "daniel", 0.0))
    assert res["ok"] is True
    assert pm.get_proposal(p.id).status == "executed"


def test_block_action_reject_archives_no_block(stub_exec):
    stub_exec()
    p = _mk()
    h = SlackActionHandler()
    res = h.handle(SlackAction(SlackActionType.REJECT, p.id, "daniel", 0.0))
    assert res["ok"] is True
    assert pm.get_proposal(p.id).status == "rejected"
    assert block.is_blocked("drop_caches", "h1") is False


def test_slash_approve_executes(stub_exec):
    stub_exec(success=True)
    p = _mk()
    h = SlackCommandHandler()
    r = h.handle_message(f"/approve {p.id}", user="daniel")
    assert "אושר" in r.text
    assert pm.get_proposal(p.id).status == "executed"


def test_slash_approve_failure_mentions_human(stub_exec):
    stub_exec(success=False)
    p = _mk()
    h = SlackCommandHandler()
    r = h.handle_message(f"/approve {p.id}", user="daniel")
    assert "בן אדם" in r.text
    assert block.is_blocked("drop_caches", "h1") is True


def test_slash_reject_archives(stub_exec):
    stub_exec()
    p = _mk()
    h = SlackCommandHandler()
    r = h.handle_message(f"/reject {p.id} not needed", user="daniel")
    assert "נדחתה" in r.text
    assert pm.get_proposal(p.id).status == "rejected"
