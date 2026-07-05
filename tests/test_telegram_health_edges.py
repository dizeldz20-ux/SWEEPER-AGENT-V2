"""Tests for the TokenHealthTracker class in telegram_bot/health.py."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ipracticom_sweeper.telegram_bot.health import (
    TokenHealthTracker,
    BotHealthResult,
    should_alert_admin,
    resolve_token,
    probe_bot_token,
    CONSECUTIVE_FAIL_THRESHOLD,
)


@pytest.fixture
def tmp_state(tmp_path: Path) -> Path:
    return tmp_path


# ============= TokenHealthTracker basics ===================================

def test_tracker_state_file_after_first_record(tmp_state: Path) -> None:
    t = TokenHealthTracker(state_dir=tmp_state)
    t.record(status="ok")
    assert (tmp_state / "telegram_bot_health.json").exists()


def test_tracker_initial_state(tmp_state: Path) -> None:
    t = TokenHealthTracker(state_dir=tmp_state)
    assert t.last_status == "unknown"
    assert t.consecutive_failures == 0


def test_tracker_record_increments_failures_on_crit(tmp_state: Path) -> None:
    t = TokenHealthTracker(state_dir=tmp_state)
    t.record(status="crit", error_code=401)
    t.record(status="crit", error_code=401)
    assert t.consecutive_failures == 2


def test_tracker_record_resets_on_ok(tmp_state: Path) -> None:
    t = TokenHealthTracker(state_dir=tmp_state)
    t.record(status="crit", error_code=401)
    t.record(status="crit", error_code=401)
    assert t.consecutive_failures == 2
    t.record(status="ok")
    assert t.consecutive_failures == 0


def test_tracker_record_warn_increments(tmp_state: Path) -> None:
    t = TokenHealthTracker(state_dir=tmp_state)
    t.record(status="warn", error_code=500)
    assert t.consecutive_failures == 1


def test_tracker_persists_across_instances(tmp_state: Path) -> None:
    t1 = TokenHealthTracker(state_dir=tmp_state)
    t1.record(status="crit", error_code=401)
    t1.record(status="crit", error_code=401)
    # New instance reads from disk
    t2 = TokenHealthTracker(state_dir=tmp_state)
    assert t2.consecutive_failures == 2


def test_tracker_corrupt_state_recovers(tmp_state: Path) -> None:
    (tmp_state / "telegram_bot_health.json").write_text("not json{")
    # Should not raise
    t = TokenHealthTracker(state_dir=tmp_state)
    assert t.last_status == "unknown"
    assert t.consecutive_failures == 0


def test_tracker_records_last_check_time(tmp_state: Path) -> None:
    t = TokenHealthTracker(state_dir=tmp_state)
    t.record(status="ok")
    assert t.last_checked_at is not None


def test_tracker_history_grows_with_records(tmp_state: Path) -> None:
    t = TokenHealthTracker(state_dir=tmp_state)
    for _ in range(10):
        t.record(status="ok")
    # In-memory history grows unbounded; on-disk is capped
    assert len(t.history) == 10


def test_tracker_on_disk_caps_at_50(tmp_state: Path) -> None:
    t = TokenHealthTracker(state_dir=tmp_state)
    for _ in range(100):
        t.record(status="ok")
    # Re-read from disk
    on_disk = json.loads((tmp_state / "telegram_bot_health.json").read_text())
    assert len(on_disk["history"]) <= 50


# ============= should_alert_admin ==========================================

def test_should_alert_admin_true_after_threshold(tmp_state: Path) -> None:
    t = TokenHealthTracker(state_dir=tmp_state)
    for _ in range(CONSECUTIVE_FAIL_THRESHOLD):
        t.record(status="crit", error_code=401)
    assert should_alert_admin(t) is True


def test_should_alert_admin_false_below_threshold(tmp_state: Path) -> None:
    t = TokenHealthTracker(state_dir=tmp_state)
    t.record(status="crit", error_code=401)
    assert should_alert_admin(t) is False


def test_should_alert_admin_false_when_last_is_ok(tmp_state: Path) -> None:
    t = TokenHealthTracker(state_dir=tmp_state)
    for _ in range(CONSECUTIVE_FAIL_THRESHOLD):
        t.record(status="crit", error_code=401)
    t.record(status="ok")  # resets
    assert should_alert_admin(t) is False


def test_should_alert_admin_threshold_override(tmp_state: Path) -> None:
    t = TokenHealthTracker(state_dir=tmp_state)
    t.record(status="crit", error_code=401)
    # With threshold=1, even one crit should alert
    assert should_alert_admin(t, threshold=1) is True


# ============= resolve_token ================================================

def test_resolve_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    assert resolve_token() == "test-token-123"


def test_resolve_token_handles_empty_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "  ")
    result = resolve_token()
    assert result is None or result.strip() == ""


def test_resolve_token_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    # No env set → None (or file fallback if exists)
    result = resolve_token()
    assert result is None or isinstance(result, str)


# ============= probe_bot_token ==============================================

def test_probe_bot_token_ok() -> None:
    fake_resp = (200, {"ok": True, "result": {"id": 12345, "is_bot": True, "username": "testbot"}})
    with patch("ipracticom_sweeper.telegram_bot.health._http_getme", return_value=fake_resp):
        r = probe_bot_token(token="valid-token")
    assert r.status == "ok"
    assert r.bot_username == "testbot"


def test_probe_bot_token_detects_401() -> None:
    """401 raises HTTPError → caught → status=crit."""
    import urllib.error
    err = urllib.error.HTTPError(url="", code=401, msg="Unauthorized", hdrs={}, fp=None)
    with patch("ipracticom_sweeper.telegram_bot.health._http_getme", side_effect=err):
        r = probe_bot_token(token="revoked-token")
    assert r.status == "crit"
    assert r.error_code == 401


def test_probe_bot_token_detects_5xx() -> None:
    import urllib.error
    err = urllib.error.HTTPError(url="", code=503, msg="Unavailable", hdrs={}, fp=None)
    with patch("ipracticom_sweeper.telegram_bot.health._http_getme", side_effect=err):
        r = probe_bot_token(token="any-token")
    assert r.status in ("warn", "crit")


def test_probe_bot_token_handles_network_error() -> None:
    with patch("ipracticom_sweeper.telegram_bot.health._http_getme",
               side_effect=OSError("nope")):
        r = probe_bot_token(token="any-token")
    assert r.status == "warn"


def test_probe_bot_token_no_token() -> None:
    r = probe_bot_token(token="")
    assert r.status == "disabled"


# ============= BotHealthResult =============================================

def test_bot_health_result_dataclass() -> None:
    r = BotHealthResult(
        status="ok", error_code=None,
        bot_username="testbot", latency_ms=100.0,
    )
    assert r.status == "ok"
    assert r.bot_username == "testbot"
    assert r.latency_ms == 100.0


# ============= probe_if_configured =========================================

def test_probe_if_configured_no_token(tmp_state: Path) -> None:
    t = TokenHealthTracker(state_dir=tmp_state)
    r = t.probe_if_configured(token=None)
    assert r.status == "disabled"
    # No probe was made, no history appended
    assert t.consecutive_failures == 0


def test_probe_if_configured_with_token_records(tmp_state: Path) -> None:
    t = TokenHealthTracker(state_dir=tmp_state)
    fake_resp = (200, {"ok": True, "result": {"username": "x"}})
    with patch("ipracticom_sweeper.telegram_bot.health._http_getme", return_value=fake_resp):
        t.probe_if_configured(token="good")
    assert t.last_status == "ok"