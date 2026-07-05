"""Tests for slice 8.3: Telegram bot token health probe.

The sweeper's notifications stop working if the Telegram bot token is
revoked, the bot is blocked, or the network is broken. This slice adds
a probe that runs periodically and alerts the operator on persistent
failure (not on a single transient blip).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ipracticom_sweeper.telegram_bot.health import (
    BotHealthResult,
    TokenHealthTracker,
    probe_bot_token,
    should_alert_admin,
)


# --- 8.3.1 probing ------------------------------------------------------------

def test_8_3_getme_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock Telegram getMe returning a bot.name → status=ok."""

    def fake_getme(url: str, token: str, timeout: float) -> tuple[int, dict]:
        return 200, {"ok": True, "result": {"username": "test_bot", "id": 123}}

    monkeypatch.setattr(
        "ipracticom_sweeper.telegram_bot.health._http_getme", fake_getme
    )
    result = probe_bot_token("test-token")
    assert result.status == "ok"
    assert result.bot_username == "test_bot"


def test_8_3_invalid_token_returns_crit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock Telegram 401 Unauthorized → status=crit."""

    def fake_getme(url: str, token: str, timeout: float) -> tuple[int, dict]:
        return 401, {"ok": False, "error_code": 401}

    monkeypatch.setattr(
        "ipracticom_sweeper.telegram_bot.health._http_getme", fake_getme
    )
    result = probe_bot_token("bad-token")
    assert result.status == "crit"
    assert result.error_code == 401


def test_8_3_network_timeout_returns_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock timeout → status=warn (transient)."""

    def fake_getme(url: str, token: str, timeout: float) -> tuple[int, dict]:
        raise TimeoutError("connect timed out")

    monkeypatch.setattr(
        "ipracticom_sweeper.telegram_bot.health._http_getme", fake_getme
    )
    result = probe_bot_token("test-token")
    assert result.status == "warn"


# --- 8.3.2 consecutive-failure tracking ---------------------------------------

def test_8_3_3_failures_in_a_row_alerts(tmp_path: Path) -> None:
    """3 consecutive crit → should_alert_admin → True."""
    tracker = TokenHealthTracker(state_dir=tmp_path)
    for _ in range(3):
        tracker.record(status="crit", error_code=401)
    assert should_alert_admin(tracker, threshold=3) is True


def test_8_3_does_not_alert_on_first_failure(tmp_path: Path) -> None:
    """1 failure alone is not enough to alert."""
    tracker = TokenHealthTracker(state_dir=tmp_path)
    tracker.record(status="crit", error_code=401)
    assert should_alert_admin(tracker, threshold=3) is False


def test_8_3_recovery_resets_counter(tmp_path: Path) -> None:
    """Token fixed → consecutive-failure counter resets to 0."""
    tracker = TokenHealthTracker(state_dir=tmp_path)
    tracker.record(status="crit", error_code=401)
    tracker.record(status="crit", error_code=401)
    tracker.record(status="ok")
    assert tracker.consecutive_failures == 0


def test_8_3_works_without_token_configured(tmp_path: Path) -> None:
    """No token env → status=disabled, not crit."""
    # Don't pass a token → tracker / probe should not be in crit state
    tracker = TokenHealthTracker(state_dir=tmp_path)
    result = tracker.probe_if_configured(token=None)
    assert result.status == "disabled"


# --- 8.3.3 integration with snapshot/healthz ---------------------------------

def test_8_3_health_endpoint_exposes_bot_status(tmp_path: Path) -> None:
    """TokenHealthTracker persists status; loadable for /healthz response."""
    tracker = TokenHealthTracker(state_dir=tmp_path)
    tracker.record(status="ok", bot_username="my_bot")
    loaded = TokenHealthTracker(state_dir=tmp_path)
    assert loaded.last_status == "ok"
    assert loaded.last_bot_username == "my_bot"