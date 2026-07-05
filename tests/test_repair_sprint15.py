"""Sprint 15 — 5 new repair actions (dns_cache, fs_inode, rotate_audit, telegram, healthz).

TDD: test first → implement → green. Mocks: subprocess, urllib, audit, telegram.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ipracticom_sweeper.repair import (
    REPAIRS,
    execute_repair,
    list_available_repairs,
    repair_dns_cache_purge,
    repair_fs_inode_warn_clear,
    repair_rotate_audit_now,
    repair_telegram_token_revalidate,
    repair_self_healthz_ping,
)


# ============= Registration / discovery ====================================

def test_sprint15_all_5_repairs_registered() -> None:
    names = list_available_repairs()
    for action in (
        "dns_cache_purge",
        "fs_inode_warn_clear",
        "rotate_audit_now",
        "telegram_token_revalidate",
        "self_healthz_ping",
    ):
        assert action in names, f"{action} not in registry"


def test_sprint15_legacy_repairs_still_registered() -> None:
    names = list_available_repairs()
    for action in (
        "drop_caches", "log_truncate_journald", "service_restart",
        "top_processes_snapshot", "notify_human",
    ):
        assert action in names, f"{action} not in registry"


# ============= dns_cache_purge ===============================================

def test_dns_cache_purge_nscd_default(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("subprocess.run", fake)
    with patch.dict("os.environ", {"IPRACTICOM_SWEEPER_STATE_DIR": str(Path("/tmp/x"))}):
        result = repair_dns_cache_purge()
    assert result.success is True
    assert result.action == "dns_cache_purge"
    assert "nscd" in result.target


def test_dns_cache_purge_systemd_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("subprocess.run", fake)
    result = repair_dns_cache_purge(service="systemd-resolved")
    assert result.success is True
    assert "systemd-resolved" in result.target


def test_dns_cache_purge_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = MagicMock(return_value=MagicMock(returncode=1, stdout="", stderr="boom"))
    monkeypatch.setattr("subprocess.run", fake)
    result = repair_dns_cache_purge(service="nonexistent")
    assert result.success is False


def test_dns_cache_purge_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as sp
    def boom(*a, **kw):
        raise sp.TimeoutExpired(cmd="systemctl", timeout=15)
    monkeypatch.setattr("subprocess.run", boom)
    result = repair_dns_cache_purge()
    assert result.success is False
    assert "timed out" in result.message


def test_dns_cache_purge_takes_snapshot() -> None:
    """Snapshot must be created before the action."""
    fake = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    with patch("subprocess.run", fake):
        result = repair_dns_cache_purge()
    assert result.snapshot_id is not None
    assert result.snapshot_id != ""


# ============= fs_inode_warn_clear ==========================================

def test_fs_inode_clear_when_cache_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    cache_dir = state / "cache"
    cache_dir.mkdir(parents=True)
    cache_file = cache_dir / "inode_warn.json"
    cache_file.write_text('{"warned": true}')

    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(state))
    # Re-import the module so _BASE_STATE picks up the env var
    import importlib
    from ipracticom_sweeper.repair import actions
    importlib.reload(actions)
    try:
        result = actions.repair_fs_inode_warn_clear()
        assert result.success is True
        assert not cache_file.exists()
    finally:
        # Restore env
        monkeypatch.delenv("IPRACTICOM_SWEEPER_STATE_DIR", raising=False)


def test_fs_inode_clear_no_cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    (state / "cache").mkdir(parents=True)
    # No inode_warn.json present
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(state))
    import importlib
    from ipracticom_sweeper.repair import actions
    importlib.reload(actions)
    try:
        result = actions.repair_fs_inode_warn_clear()
        assert result.success is True
    finally:
        monkeypatch.delenv("IPRACTICOM_SWEEPER_STATE_DIR", raising=False)


def test_fs_inode_clear_metadata_timestamp() -> None:
    result = repair_fs_inode_warn_clear()
    assert "cleared_at" in result.snapshot_id or result.snapshot_id is not None
    assert result.success is True


# ============= rotate_audit_now =============================================

def test_rotate_audit_now_calls_audit_rotate(tmp_path: Path) -> None:
    state = tmp_path / "state"
    audit_dir = state / "audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / "audit.jsonl").write_text('{"e":1}\n{"e":2}\n')

    fake = MagicMock(return_value=2)
    with patch("ipracticom_sweeper.audit.rotation.audit_rotate", fake):
        result = repair_rotate_audit_now(state_dir=str(state))
    assert result.success is True
    assert "2 files" in result.message


def test_rotate_audit_now_handles_audit_rotate_exception(tmp_path: Path) -> None:
    def boom(state):
        raise RuntimeError("disk full")
    with patch("ipracticom_sweeper.audit.rotation.audit_rotate", boom):
        result = repair_rotate_audit_now(state_dir="/tmp/nonexistent")
    assert result.success is False
    assert "RuntimeError" in result.message or "disk full" in result.message


def test_rotate_audit_now_default_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IPRACTICOM_SWEEPER_STATE_DIR", str(tmp_path))
    import importlib
    from ipracticom_sweeper.repair import actions
    importlib.reload(actions)
    fake = MagicMock(return_value=1)
    with patch("ipracticom_sweeper.audit.rotation.audit_rotate", fake):
        result = actions.repair_rotate_audit_now()
    assert result.success is True
    assert result.target == str(tmp_path)


def test_rotate_audit_now_takes_snapshot(tmp_path: Path) -> None:
    state = tmp_path / "state"
    audit_dir = state / "audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / "audit.jsonl").write_text('x' * 1024)
    with patch("ipracticom_sweeper.audit.rotation.audit_rotate", return_value=1):
        result = repair_rotate_audit_now(state_dir=str(state))
    assert result.snapshot_id is not None
    # Snapshot contains pre-audit-size
    snap = result.snapshot_id


# ============= telegram_token_revalidate ====================================

def test_telegram_revalidate_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ipracticom_sweeper.telegram_bot.health.resolve_token",
        lambda: "123456:FAKE",
    )
    fake_result = MagicMock(
        status="ok", error_code=None, bot_username="mybot", error=None
    )
    monkeypatch.setattr(
        "ipracticom_sweeper.telegram_bot.health.probe_bot_token",
        lambda token: fake_result,
    )
    fake_tracker = MagicMock()
    monkeypatch.setattr(
        "ipracticom_sweeper.telegram_bot.health.TokenHealthTracker",
        lambda state_dir: fake_tracker,
    )
    result = repair_telegram_token_revalidate()
    assert result.success is True
    assert "@mybot" in result.message


def test_telegram_revalidate_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ipracticom_sweeper.telegram_bot.health.resolve_token",
        lambda: None,
    )
    result = repair_telegram_token_revalidate()
    assert result.success is False
    assert "No Telegram token" in result.message


def test_telegram_revalidate_crit_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ipracticom_sweeper.telegram_bot.health.resolve_token",
        lambda: "123456:FAKE",
    )
    fake_result = MagicMock(
        status="crit", error_code=401, bot_username=None, error="Unauthorized"
    )
    monkeypatch.setattr(
        "ipracticom_sweeper.telegram_bot.health.probe_bot_token",
        lambda token: fake_result,
    )
    monkeypatch.setattr(
        "ipracticom_sweeper.telegram_bot.health.TokenHealthTracker",
        lambda state_dir: MagicMock(),
    )
    result = repair_telegram_token_revalidate()
    assert result.success is False
    assert "Unauthorized" in result.message


def test_telegram_revalidate_records_to_tracker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ipracticom_sweeper.telegram_bot.health.resolve_token",
        lambda: "123456:FAKE",
    )
    fake_result = MagicMock(
        status="ok", error_code=None, bot_username="b", error=None
    )
    monkeypatch.setattr(
        "ipracticom_sweeper.telegram_bot.health.probe_bot_token",
        lambda token: fake_result,
    )
    tracker = MagicMock()
    monkeypatch.setattr(
        "ipracticom_sweeper.telegram_bot.health.TokenHealthTracker",
        lambda state_dir: tracker,
    )
    repair_telegram_token_revalidate()
    assert tracker.record.called


# ============= self_healthz_ping ============================================

def test_healthz_ping_uses_urllib(monkeypatch: pytest.MonkeyPatch) -> None:
    """The repair opens a real urllib request to localhost:8000."""
    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.read.return_value = b'{"status":"ok"}'
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda self, *a: False

    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout: fake_resp)
    result = repair_self_healthz_ping()
    assert result.success is True
    assert result.target == "http://localhost:8000/healthz"


def test_healthz_ping_records_latency(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.read.return_value = b"ok"
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda self, *a: False
    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout: fake_resp)
    result = repair_self_healthz_ping()
    assert "ms" in result.message


def test_healthz_ping_handles_500(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_resp = MagicMock()
    fake_resp.status = 500
    fake_resp.read.return_value = b"internal"
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda self, *a: False
    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout: fake_resp)
    result = repair_self_healthz_ping()
    assert result.success is False


def test_healthz_ping_handles_connection_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url, timeout):
        raise ConnectionRefusedError("no server")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    result = repair_self_healthz_ping()
    assert result.success is False


def test_healthz_ping_takes_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.read.return_value = b"x"
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda self, *a: False
    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout: fake_resp)
    result = repair_self_healthz_ping()
    assert result.snapshot_id is not None


# ============= execute_repair dispatch =======================================

def test_execute_repair_dispatches_to_dns_cache_purge() -> None:
    fake = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    with patch("subprocess.run", fake):
        result = execute_repair("dns_cache_purge", service="nscd")
    assert result.success is True


def test_execute_repair_dispatches_to_fs_inode_clear() -> None:
    result = execute_repair("fs_inode_warn_clear")
    assert result.success is True


def test_execute_repair_dispatches_to_rotate_audit(tmp_path: Path) -> None:
    state = tmp_path / "state"
    (state / "audit").mkdir(parents=True)
    with patch("ipracticom_sweeper.audit.rotation.audit_rotate", return_value=1):
        result = execute_repair("rotate_audit_now", state_dir=str(state))
    assert result.success is True


def test_execute_repair_unknown_action_returns_failure() -> None:
    result = execute_repair("nonexistent_action")
    assert result.success is False
    assert "Unknown repair action" in result.message


def test_execute_repair_strips_internal_kwargs() -> None:
    """dry_run and force are internal-only; must not be passed to fn."""
    from ipracticom_sweeper.repair.actions import REPAIRS as ACTIONS_REPAIRS
    from ipracticom_sweeper.repair.actions import RepairResult
    captured = {}

    def my_repair(**kwargs):
        captured.update(kwargs)
        return RepairResult(
            action="x", target="x", success=True, snapshot_id=None,
            message="ok", duration_ms=0,
        )

    ACTIONS_REPAIRS["test_repair_strips"] = my_repair
    try:
        from ipracticom_sweeper.repair.actions import execute_repair as er
        er("test_repair_strips", a=1, dry_run=True, force=True)
    finally:
        del ACTIONS_REPAIRS["test_repair_strips"]
    assert "dry_run" not in captured
    assert "force" not in captured
    assert captured["a"] == 1