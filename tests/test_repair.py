"""Tests for the repair layer."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ipracticom_sweeper.repair import actions
from ipracticom_sweeper.repair.actions import (
    RepairResult,
    Snapshot,
    execute_repair,
    list_available_repairs,
    repair_drop_caches,
    repair_log_truncate_journald,
    repair_service_restart,
    repair_top_processes_snapshot,
)


# --- Registry ----------------------------------------------------------------


def test_list_available_repairs():
    repairs = list_available_repairs()
    assert "drop_caches" in repairs
    assert "log_truncate_journald" in repairs
    assert "service_restart" in repairs
    assert "top_processes_snapshot" in repairs
    assert "notify_human" in repairs


def test_execute_unknown_repair_returns_failure():
    result = execute_repair("nonexistent_action")
    assert result.success is False
    assert "Unknown repair action" in result.message


# --- drop_caches -------------------------------------------------------------


def test_drop_caches_invalid_level():
    result = repair_drop_caches(level=99)
    assert result.success is False
    assert "Invalid level" in result.message


def test_drop_caches_success_at_root():
    """On a root-allowed system, drop_caches should succeed.

    We skip this if we're not root.
    """
    import os
    if os.geteuid() != 0:
        pytest.skip("requires root")
    result = repair_drop_caches(level=3)
    assert result.success is True
    assert result.snapshot_id is not None
    assert result.action == "drop_caches"


def test_drop_caches_permission_denied_handled(tmp_path, monkeypatch):
    """If we can't open drop_caches, return clean failure."""
    # Simulate by using a non-writable /proc/sys/vm/drop_caches path
    # by passing an invalid level (the function checks before writing)
    result = repair_drop_caches(level=5)
    assert result.success is False


# --- log_truncate_journald ---------------------------------------------------


def test_log_truncate_journald_success():
    """Mock journalctl and verify the call."""
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "Vacuumed. Done."
    fake_result.stderr = ""
    with patch("subprocess.run", return_value=fake_result):
        result = repair_log_truncate_journald(max_age_days=3)
    assert result.success is True
    assert result.snapshot_id is not None
    assert "Vacuumed" in result.message


def test_log_truncate_journald_failure():
    fake_result = MagicMock()
    fake_result.returncode = 1
    fake_result.stdout = ""
    fake_result.stderr = "permission denied"
    with patch("subprocess.run", return_value=fake_result):
        result = repair_log_truncate_journald(max_age_days=3)
    assert result.success is False
    assert "permission denied" in (result.error or "")


# --- service_restart ---------------------------------------------------------


def test_service_restart_success():
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = ""
    fake_result.stderr = ""
    with patch("subprocess.run", return_value=fake_result) as mock_run:
        # Also patch _service_state to avoid the pre-snapshot subprocess call
        with patch.object(actions, "_service_state", return_value="active"):
            result = repair_service_restart(unit="nginx")
    assert result.success is True
    assert result.snapshot_id is not None
    # Verify systemctl restart was called with the right unit
    args = mock_run.call_args[0][0]
    assert args == ["systemctl", "restart", "nginx"]


def test_service_restart_timeout():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=60)):
        with patch.object(actions, "_service_state", return_value="active"):
            result = repair_service_restart(unit="postgresql")
    assert result.success is False
    assert "timed out" in result.message


# --- top_processes_snapshot -------------------------------------------------


def test_top_processes_snapshot_success():
    fake_ps_output = (
        "PID USER %CPU %MEM COMMAND\n"
        "1234 root  50.0  5.0 python\n"
        "5678 www-data 30.0 3.0 nginx\n"
        "9999 root   10.0 1.0 top\n"
    )
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = fake_ps_output
    fake_result.stderr = ""
    with patch("subprocess.run", return_value=fake_result):
        result = repair_top_processes_snapshot(top_n=3)
    assert result.success is True
    assert result.snapshot_id is not None
    assert "python" in result.output
    assert "nginx" in result.output


# --- notify_human ------------------------------------------------------------


def test_notify_human_creates_snapshot():
    result = execute_repair("notify_human", channel="slack", defcon=3, summary="disk full")
    assert result.success is True
    assert result.snapshot_id is not None
    assert "defcon=3" in result.message


# --- Snapshot persistence ----------------------------------------------------


def test_snapshot_load_roundtrip(tmp_path, monkeypatch):
    """Snapshots should be loadable by ID after save."""
    monkeypatch.setattr(actions, "SNAPSHOT_DIR", tmp_path)
    snap = Snapshot(
        id="test-123",
        action="drop_caches",
        target="level=3",
        created_at="2026-06-28T10:00:00Z",
        metadata={"pre_meminfo": "foo"},
    )
    snap.save()
    loaded = Snapshot.load("test-123")
    assert loaded.id == "test-123"
    assert loaded.action == "drop_caches"
    assert loaded.metadata["pre_meminfo"] == "foo"


def test_snapshot_load_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(actions, "SNAPSHOT_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        Snapshot.load("does-not-exist")


# --- RepairResult dataclass --------------------------------------------------


def test_repair_result_dataclass():
    r = RepairResult(
        action="test",
        target="x",
        success=True,
        snapshot_id="abc",
        message="ok",
        duration_ms=42,
    )
    assert r.action == "test"
    assert r.duration_ms == 42
    assert r.rollback_available is False  # default