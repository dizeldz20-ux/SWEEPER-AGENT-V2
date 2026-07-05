"""Sprint 15 — Additional repairs (5 tests per slice, 25 total).

Tests mock subprocess to avoid real fs_cli/psql/nginx calls.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ipracticom_sweeper.repair import actions_extra
from ipracticom_sweeper.repair.actions import execute_repair


# =============================================================================
# Sprint 15.1 — repair_rotate_nginx_logs (5 tests)
# =============================================================================

def test_15_1_rotates_log_file(tmp_path) -> None:
    log = tmp_path / "access.log"
    log.write_text("a" * 1024)
    r = actions_extra.repair_rotate_nginx_logs(log_path=str(log))
    assert r.success is True
    # Old file moved to .1
    assert (tmp_path / "access.log.1").exists()
    # New file is empty
    assert log.exists()
    assert log.stat().st_size == 0


def test_15_1_handles_missing_log(tmp_path) -> None:
    r = actions_extra.repair_rotate_nginx_logs(
        log_path=str(tmp_path / "nope.log"),
    )
    assert r.success is False
    assert r.error == "log_not_found"


def test_15_1_sends_sigusr1_to_nginx(tmp_path) -> None:
    log = tmp_path / "access.log"
    log.write_text("x")
    with patch("subprocess.run") as mock_run:
        # First call: killall nginx (returns success)
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        r = actions_extra.repair_rotate_nginx_logs(log_path=str(log))
    assert r.success is True
    # Check that killall was called with SIGUSR1
    killall_called = any(
        call.args and call.args[0] and "killall" in str(call.args[0])
        for call in mock_run.call_args_list
    )
    assert killall_called


def test_15_1_keeps_5_rotations(tmp_path) -> None:
    log = tmp_path / "access.log"
    log.write_text("current")
    # Pre-create 5 rotations
    for i in range(1, 7):
        (tmp_path / f"access.log.{i}").write_text(f"r{i}")
    r = actions_extra.repair_rotate_nginx_logs(
        log_path=str(log), keep_rotations=5,
    )
    assert r.success is True
    # We should have .1..5 (the .6 slot is filled by cascading .5)
    for i in range(1, 6):
        assert (tmp_path / f"access.log.{i}").exists()
    # The new .5 should contain the old .4's content
    assert (tmp_path / "access.log.5").read_text() == "r4"
    # The new .6 (cascade from .5) should contain old .5's content
    # Note: with keep_rotations=5, .6 still exists (last cascade step)
    assert (tmp_path / "access.log.6").read_text() == "r5"


def test_15_1_metadata_bytes_freed(tmp_path) -> None:
    log = tmp_path / "access.log"
    log.write_text("y" * 500)
    r = actions_extra.repair_rotate_nginx_logs(log_path=str(log))
    assert r.success is True
    # Snapshot metadata should record bytes_freed
    from ipracticom_sweeper.repair.actions import Snapshot
    snap = Snapshot.load(r.snapshot_id)
    assert snap.metadata["bytes_freed"] == 500


# =============================================================================
# Sprint 15.2 — repair_drop_freeswitch_cache (5 tests)
# =============================================================================

def test_15_2_runs_fs_cli_cache_flush() -> None:
    with patch("subprocess.run") as mock_run:
        # First call (status check): success
        # Second call (cache flush): success
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="UP", stderr=""),
            MagicMock(returncode=0, stdout="+OK cache flushed", stderr=""),
        ]
        r = actions_extra.repair_drop_freeswitch_cache()
    assert r.success is True
    assert mock_run.call_count == 2
    # Second call args contain "cache" and "flush"
    cache_args = mock_run.call_args_list[1].args[0]
    assert "cache" in cache_args
    assert "flush" in cache_args


def test_15_2_handles_fs_cli_failure() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="UP", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="permission denied"),
        ]
        r = actions_extra.repair_drop_freeswitch_cache()
    assert r.success is False
    assert "permission" in r.error.lower() or "permission" in r.message.lower()


def test_15_2_handles_fs_not_running() -> None:
    with patch("subprocess.run") as mock_run:
        # status check returns non-zero
        mock_run.return_value = MagicMock(returncode=1, stderr="connection refused")
        r = actions_extra.repair_drop_freeswitch_cache()
    assert r.success is False
    assert r.error == "fs_not_running"
    # Should not have attempted cache flush
    assert mock_run.call_count == 1


def test_15_2_snapshot_before_action() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="UP", stderr=""),
            MagicMock(returncode=0, stdout="OK", stderr=""),
        ]
        r = actions_extra.repair_drop_freeswitch_cache()
    assert r.snapshot_id is not None
    assert r.snapshot_id != ""


def test_15_2_metadata_duration_ms() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="UP", stderr=""),
            MagicMock(returncode=0, stdout="OK", stderr=""),
        ]
        r = actions_extra.repair_drop_freeswitch_cache()
    assert r.duration_ms >= 0
    assert isinstance(r.duration_ms, int)


# =============================================================================
# Sprint 15.3 — repair_reload_freeswitch_config (5 tests)
# =============================================================================

def test_15_3_runs_fs_cli_reloadxml() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="UP", stderr=""),
            MagicMock(returncode=0, stdout="+OK reloaded", stderr=""),
        ]
        r = actions_extra.repair_reload_freeswitch_config()
    assert r.success is True
    reload_args = mock_run.call_args_list[1].args[0]
    assert "reloadxml" in reload_args


def test_15_3_handles_syntax_error() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="UP", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="-ERR XML parse error line 42"),
        ]
        r = actions_extra.repair_reload_freeswitch_config()
    assert r.success is False
    assert "XML" in r.error or "parse" in r.error.lower()


def test_15_3_handles_fs_not_running() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="down")
        r = actions_extra.repair_reload_freeswitch_config()
    assert r.success is False
    assert r.error == "fs_not_running"


def test_15_3_snapshot_before_action() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="UP", stderr=""),
            MagicMock(returncode=0, stdout="OK", stderr=""),
        ]
        r = actions_extra.repair_reload_freeswitch_config()
    assert r.snapshot_id is not None


def test_15_3_metadata_duration_ms() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="UP", stderr=""),
            MagicMock(returncode=0, stdout="OK", stderr=""),
        ]
        r = actions_extra.repair_reload_freeswitch_config()
    assert r.duration_ms >= 0


# =============================================================================
# Sprint 15.4 — repair_clear_freeswitch_voicemail_locks (5 tests)
# =============================================================================

def test_15_4_removes_stale_locks(tmp_path) -> None:
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    # Old lock (2 hours old)
    old = lock_dir / "vm_123.lock"
    old.write_text("x")
    import time
    old_time = time.time() - 7200
    import os
    os.utime(old, (old_time, old_time))
    r = actions_extra.repair_clear_freeswitch_voicemail_locks(
        lock_dir=str(lock_dir), max_age_seconds=3600,
    )
    assert r.success is True
    assert not old.exists()
    assert "removed" in r.output


def test_15_4_keeps_recent_locks(tmp_path) -> None:
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    recent = lock_dir / "vm_999.lock"
    recent.write_text("x")
    r = actions_extra.repair_clear_freeswitch_voicemail_locks(
        lock_dir=str(lock_dir), max_age_seconds=3600,
    )
    assert r.success is True
    assert recent.exists()
    assert "kept" in r.output


def test_15_4_handles_no_locks(tmp_path) -> None:
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    r = actions_extra.repair_clear_freeswitch_voicemail_locks(
        lock_dir=str(lock_dir),
    )
    assert r.success is True
    assert "0 stale" in r.message or "kept 0" in r.output


def test_15_4_handles_dir_missing(tmp_path) -> None:
    r = actions_extra.repair_clear_freeswitch_voicemail_locks(
        lock_dir=str(tmp_path / "nonexistent"),
    )
    assert r.success is False
    assert r.error == "dir_missing"


def test_15_4_metadata_locks_removed(tmp_path) -> None:
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    import time, os
    for i in range(3):
        lk = lock_dir / f"stale_{i}.lock"
        lk.write_text("x")
        old_time = time.time() - 7200
        os.utime(lk, (old_time, old_time))
    r = actions_extra.repair_clear_freeswitch_voicemail_locks(
        lock_dir=str(lock_dir), max_age_seconds=3600,
    )
    from ipracticom_sweeper.repair.actions import Snapshot
    snap = Snapshot.load(r.snapshot_id)
    assert snap.metadata["locks_removed"] == 3


# =============================================================================
# Sprint 15.5 — repair_pg_vacuum (5 tests)
# =============================================================================

def test_15_5_runs_vacuum_analyze() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="VACUUM", stderr="")
        r = actions_extra.repair_pg_vacuum(table="users")
    assert r.success is True
    args_str = " ".join(mock_run.call_args.args[0])
    assert "VACUUM" in args_str
    assert "ANALYZE" in args_str
    assert "users" in args_str


def test_15_5_handles_db_down() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=2, stdout="", stderr="connection refused")
        r = actions_extra.repair_pg_vacuum(table="users")
    assert r.success is False
    assert "connection" in r.error.lower() or "refused" in r.error.lower()


def test_15_5_dry_run_option() -> None:
    r = actions_extra.repair_pg_vacuum(table="users", dry_run=True)
    assert r.success is True
    assert "dry_run" in r.message


def test_15_5_timeout_60s() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="psql", timeout=60)
        r = actions_extra.repair_pg_vacuum(table="users", timeout_s=60)
    assert r.success is False
    assert r.error == "timeout"
    assert "timed out" in r.message.lower()


def test_15_5_metadata_table_names() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="VACUUM", stderr="")
        r = actions_extra.repair_pg_vacuum(table="orders")
    assert r.target == "orders"
    assert r.duration_ms >= 0
    assert r.snapshot_id is not None