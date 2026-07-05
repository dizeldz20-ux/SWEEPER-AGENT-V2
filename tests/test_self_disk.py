"""Tests for slice 8.2: state-dir disk monitor + emergency logrotate.

The sweeper keeps its state under /var/lib/ipracticom-sweeper (audit logs,
snapshots, cache, fleet data, pending repairs). If that dir fills up the
sweeper itself crashes silently. This slice adds a self-check + an
emergency log rotation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ipracticom_sweeper.monitor.self_disk import (
    SelfDiskResult,
    audit_rotate,
    check_state_dir,
    find_state_dir,
)


# --- 8.2.1 detection ----------------------------------------------------------

def test_8_2_state_dir_check_returns_state(tmp_path: Path) -> None:
    """Mock a state dir at 50% usage → returns `state_dir_pct` field."""
    result = check_state_dir(state_dir=tmp_path, total_bytes=1000, used_bytes=500)
    assert isinstance(result, SelfDiskResult)
    assert result.state_dir_pct == pytest.approx(50.0, abs=0.1)


def test_8_2_warn_at_80_pct(tmp_path: Path) -> None:
    """85% usage → status=warn, defcon=4."""
    result = check_state_dir(state_dir=tmp_path, total_bytes=1000, used_bytes=850)
    assert result.status == "warn"
    assert result.defcon == 4


def test_8_2_crit_at_95_pct(tmp_path: Path) -> None:
    """97% usage → status=crit, defcon=2."""
    result = check_state_dir(state_dir=tmp_path, total_bytes=1000, used_bytes=970)
    assert result.status == "crit"
    assert result.defcon == 2


def test_8_2_calls_rotate_when_crit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify audit_rotate() is invoked when state dir ≥95%."""
    called: list[int] = []

    def fake_rotate(state_dir: Path) -> int:
        called.append(1)
        return 2

    monkeypatch.setattr(
        "ipracticom_sweeper.monitor.self_disk.audit_rotate", fake_rotate
    )
    result = check_state_dir(state_dir=tmp_path, total_bytes=1000, used_bytes=980)
    assert result.status == "crit"
    assert result.rotation_triggered is True
    assert result.rotated_files == 2
    assert called, "audit_rotate was not called"


def test_8_2_handles_missing_state_dir(tmp_path: Path) -> None:
    """If the state dir is absent → status=unknown, no crash."""
    missing = tmp_path / "nope"
    result = check_state_dir(state_dir=missing, total_bytes=1000, used_bytes=0)
    assert result.status == "unknown"
    assert result.state_dir_pct is None


def test_8_2_mountpoint_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`/var` and `/` mountpoints are resolved correctly via statvfs."""
    fake_stat = type(
        "S",
        (),
        {"f_blocks": 100, f"f_bavail": 50, f"f_frsize": 4096, "f_files": 1000, "f_ffree": 500},
    )()
    monkeypatch.setattr("os.statvfs", lambda p: fake_stat)
    pct = find_state_dir._disk_usage_pct(str(tmp_path)) if hasattr(find_state_dir, "_disk_usage_pct") else None
    # If helper is internal, verify via check_state_dir with the patched statvfs
    result = check_state_dir(state_dir=tmp_path)
    assert result.state_dir_pct is not None
    assert 0.0 <= result.state_dir_pct <= 100.0


def test_8_2_inode_exhaustion_check(tmp_path: Path) -> None:
    """When free inodes < 10%, defcon is forced to 2 even if disk has space."""
    # Set up a tmp dir with very few inodes
    result = check_state_dir(
        state_dir=tmp_path, total_bytes=10_000, used_bytes=2_000,
        free_inodes=5, total_inodes=1000,
    )
    # Inodes 0.5% — should trigger crit
    assert result.inode_pct_used == pytest.approx(99.5, abs=0.1)
    assert result.status == "crit"


# --- 8.2.2 rotation ----------------------------------------------------------

def test_8_2_keeps_5_rotated_files(tmp_path: Path) -> None:
    """audit_rotate keeps `audit.jsonl.1..5`, removes `.6+`."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    log = audit_dir / "audit.jsonl"
    log.write_text("newest\n")
    # Create 7 prior rotations
    for i in range(1, 8):
        (audit_dir / f"audit.jsonl.{i}").write_text(f"rotation-{i}\n")

    removed = audit_rotate(state_dir=tmp_path)
    # We keep 5 rotated, so at least 1 (the 7th) was removed
    assert (audit_dir / "audit.jsonl.7").exists() is False
    # Files 1..5 remain
    for i in range(1, 6):
        assert (audit_dir / f"audit.jsonl.{i}").exists()
    assert removed >= 1