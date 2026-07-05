"""Tests for multi-channel approval push fan-out (notify/approvals.py)."""
from __future__ import annotations

import asyncio

import pytest

from ipracticom_sweeper import config as cfg
from ipracticom_sweeper.notify import approvals


@pytest.fixture
def no_legacy(monkeypatch):
    """Silence the legacy env channels so tests isolate the multi-bot store."""
    monkeypatch.setattr(cfg, "notifications_enabled", lambda: False)
    monkeypatch.setattr(cfg, "telegram_bot_token", lambda: "")
    monkeypatch.setattr(cfg, "telegram_chat_id", lambda: "")
    monkeypatch.setattr(cfg, "slack_webhook_url", lambda: "")


def _proposal() -> dict:
    return {
        "id": "pid123",
        "action": "drop_caches",
        "server": "h1",
        "reason": "mem high",
        "proposed_command": "echo 3 > /proc/sys/vm/drop_caches",
        "problem": {"severity": "warn", "metrics": {"mem": 91}},
    }


def test_no_channels_returns_empty(monkeypatch, no_legacy):
    monkeypatch.setattr(approvals._store, "has_any_bot", lambda: False)
    monkeypatch.setattr(approvals._store, "telegram_bots", lambda: [])
    monkeypatch.setattr(approvals._store, "slack_bots", lambda: [])
    out = asyncio.run(approvals.notify_approval_request(_proposal()))
    assert out == {}


def test_fanout_to_two_telegram_bots_with_keyboard(monkeypatch, no_legacy):
    sent = []

    async def fake_tg(text, token, chat_id, reply_markup=None):
        sent.append((token, chat_id, reply_markup))
        return True

    monkeypatch.setattr(approvals, "_send_telegram_html", fake_tg)
    monkeypatch.setattr(approvals._store, "has_any_bot", lambda: True)
    monkeypatch.setattr(
        approvals._store, "telegram_bots",
        lambda: [
            {"id": "b1", "bot_token": "t1", "chat_id": "c1"},
            {"id": "b2", "bot_token": "t2", "chat_id": "c2"},
        ],
    )
    monkeypatch.setattr(approvals._store, "slack_bots", lambda: [])

    out = asyncio.run(approvals.notify_approval_request(_proposal()))
    assert out == {"telegram:b1": True, "telegram:b2": True}
    assert len(sent) == 2
    # The inline keyboard must carry the pid in its callback_data.
    assert "pid123" in str(sent[0][2])
    assert "appr:approve:pid123" in str(sent[0][2])


def test_broken_sender_does_not_raise(monkeypatch, no_legacy):
    async def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(approvals, "_send_telegram_html", boom)
    monkeypatch.setattr(approvals._store, "has_any_bot", lambda: True)
    monkeypatch.setattr(
        approvals._store, "telegram_bots",
        lambda: [{"id": "b1", "bot_token": "t1", "chat_id": "c1"}],
    )
    monkeypatch.setattr(approvals._store, "slack_bots", lambda: [])

    out = asyncio.run(approvals.notify_approval_request(_proposal()))
    assert out == {"telegram:b1": False}


def test_result_fanout_slack_failure_carries_detail(monkeypatch, no_legacy):
    sent = []

    async def fake_slack(msg, token, channel):
        sent.append((msg, token, channel))
        return True

    monkeypatch.setattr(approvals, "_send_slack_app", fake_slack)
    monkeypatch.setattr(approvals._store, "has_any_bot", lambda: True)
    monkeypatch.setattr(approvals._store, "telegram_bots", lambda: [])
    monkeypatch.setattr(
        approvals._store, "slack_bots",
        lambda: [{"id": "s1", "bot_token": "xoxb-1", "channel": "#ops"}],
    )

    out = asyncio.run(
        approvals.notify_approval_result(
            _proposal(), {"success": False, "error": "boom"}, ok=False
        )
    )
    assert out == {"slack:s1": True}
    assert "boom" in str(sent[0][0])          # failure detail present in blocks
    assert "Human" in str(sent[0][0])         # escalation present
