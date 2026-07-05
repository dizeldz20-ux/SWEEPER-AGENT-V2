"""Tests for Slack action handler."""
import time
from ipracticom_sweeper.slack_actions import (
    SlackActionHandler,
    SlackAction,
    SlackActionType,
)


def test_handle_acknowledge():
    h = SlackActionHandler()
    a = SlackAction(SlackActionType.ACKNOWLEDGE, "fp1", "alice", time.time())
    result = h.handle(a)
    assert result["status"] == "acknowledged"
    assert h.is_acked("fp1") is True


def test_handle_silence():
    h = SlackActionHandler()
    a = SlackAction(SlackActionType.SILENCE, "fp1", "alice", time.time())
    h.handle(a)
    assert h.is_silenced("fp1") is True


def test_silence_expires():
    h = SlackActionHandler()
    a = SlackAction(SlackActionType.SILENCE, "fp1", "alice", timestamp=1000.0)
    h.handle(a)
    # 2 hours later
    assert h.is_silenced("fp1", now=1000.0 + 7200) is False


def test_handle_run_repair():
    h = SlackActionHandler()
    a = SlackAction(SlackActionType.RUN_REPAIR, "fp1", "alice", time.time())
    result = h.handle(a)
    assert result["status"] == "repair_triggered"


def test_action_log():
    h = SlackActionHandler()
    assert h.action_count() == 0
    a1 = SlackAction(SlackActionType.ACKNOWLEDGE, "fp1", "alice", time.time())
    a2 = SlackAction(SlackActionType.SILENCE, "fp2", "bob", time.time())
    h.handle(a1)
    h.handle(a2)
    assert h.action_count() == 2


def test_unknown_action():
    h = SlackActionHandler()
    # Bypass enum to test edge case
    a = SlackAction("garbage", "fp1", "alice", time.time())
    result = h.handle(a)
    assert result["status"] == "unknown_action"
