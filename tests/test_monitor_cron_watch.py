"""Sprint 13.1 — cron watcher tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ipracticom_sweeper.monitor.cron_watch import (
    check_cron_changes,
    CronWatchResult,
    _fingerprint,
    _collect_fingerprints,
    _load_baseline,
    _save_baseline,
    BASELINE_FILE,
    CRON_DIRS,
)


def _setup_cron_dirs(tmp_path: Path, files: dict[str, str]) -> None:
    """Create a fake /etc/cron.d-style layout under tmp_path/etc/cron.d/."""
    base = tmp_path / "etc"
    cron_d = base / "cron.d"
    cron_d.mkdir(parents=True)
    for name, content in files.items():
        (cron_d / name).write_text(content)


# ============= Basic OK / disabled =========================================

def test_cron_disabled_when_no_dirs(tmp_path: Path) -> None:
    """No cron dirs present → disabled (not ok, not crit)."""
    result = check_cron_changes(state_dir=tmp_path, cron_dirs=())
    assert result.status == "disabled"


def test_cron_first_run_creates_baseline(tmp_path: Path) -> None:
    _setup_cron_dirs(tmp_path, {"existing_job": "* * * * * root /bin/true\n"})
    result = check_cron_changes(state_dir=tmp_path)
    assert result.status == "ok"
    assert result.reason == "baseline created"
    # Baseline file should be saved
    assert (tmp_path / "cache" / BASELINE_FILE).exists()


def test_cron_second_run_no_changes_ok(tmp_path: Path) -> None:
    _setup_cron_dirs(tmp_path, {"existing_job": "* * * * *\n"})
    # First run saves baseline
    check_cron_changes(state_dir=tmp_path)
    # Second run with no changes → ok
    result = check_cron_changes(state_dir=tmp_path)
    assert result.status == "ok"
    assert result.new_files == []
    assert result.modified_files == []


# ============= Detection ====================================================

def test_cron_warn_new_file(tmp_path: Path) -> None:
    _setup_cron_dirs(tmp_path, {"existing": "1\n"})
    cron_dir = str(tmp_path / "etc" / "cron.d")
    check_cron_changes(state_dir=tmp_path, cron_dirs=(cron_dir,))
    # Add a new file
    (tmp_path / "etc" / "cron.d" / "new_attacker").write_text("malicious\n")
    result = check_cron_changes(state_dir=tmp_path, cron_dirs=(cron_dir,))
    assert result.status == "warn"
    assert any("new_attacker" in p for p in result.new_files)


def test_cron_warn_modified_file(tmp_path: Path) -> None:
    _setup_cron_dirs(tmp_path, {"existing": "original\n"})
    cron_dir = str(tmp_path / "etc" / "cron.d")
    check_cron_changes(state_dir=tmp_path, cron_dirs=(cron_dir,))
    # Modify the file
    (tmp_path / "etc" / "cron.d" / "existing").write_text("MODIFIED\n")
    result = check_cron_changes(state_dir=tmp_path, cron_dirs=(cron_dir,))
    assert result.status == "warn"
    assert any("existing" in p for p in result.modified_files)


def test_cron_crit_modified_root_crontab(tmp_path: Path) -> None:
    """A change to /var/spool/cron/root is critical."""
    # Set up minimal cron dirs
    _setup_cron_dirs(tmp_path, {"job": "1\n"})
    # Place a fake root crontab
    root_ct = tmp_path / "var" / "spool" / "cron" / "root"
    root_ct.parent.mkdir(parents=True)
    root_ct.write_text("original crontab\n")

    # First run: baseline
    first = check_cron_changes(
        state_dir=tmp_path,
        cron_dirs=(str(tmp_path / "etc" / "cron.d"),),
        root_crontab=str(root_ct),
    )
    assert first.status == "ok"

    # Tamper
    root_ct.write_text("malicious crontab\n")
    result = check_cron_changes(
        state_dir=tmp_path,
        cron_dirs=(str(tmp_path / "etc" / "cron.d"),),
        root_crontab=str(root_ct),
    )
    assert result.status == "crit"
    assert result.root_crontab_changed is True


def test_cron_warn_deleted_file(tmp_path: Path) -> None:
    _setup_cron_dirs(tmp_path, {"a": "1\n", "b": "2\n"})
    cron_dir = str(tmp_path / "etc" / "cron.d")
    check_cron_changes(state_dir=tmp_path, cron_dirs=(cron_dir,))
    # Delete one
    (tmp_path / "etc" / "cron.d" / "b").unlink()
    result = check_cron_changes(state_dir=tmp_path, cron_dirs=(cron_dir,))
    assert result.status == "warn"
    assert any("b" in p for p in result.deleted_files)


# ============= Metadata =====================================================

def test_cron_returns_dataclass() -> None:
    r = CronWatchResult(status="ok")
    assert hasattr(r, "new_files")
    assert hasattr(r, "modified_files")
    assert hasattr(r, "deleted_files")
    assert hasattr(r, "root_crontab_changed")
    assert hasattr(r, "status")


def test_cron_metadata_diff_per_file(tmp_path: Path) -> None:
    _setup_cron_dirs(tmp_path, {"job1": "1\n"})
    cron_dir = str(tmp_path / "etc" / "cron.d")
    check_cron_changes(state_dir=tmp_path, cron_dirs=(cron_dir,))
    # Add + modify
    (tmp_path / "etc" / "cron.d" / "new").write_text("2\n")
    (tmp_path / "etc" / "cron.d" / "job1").write_text("changed\n")
    result = check_cron_changes(state_dir=tmp_path, cron_dirs=(cron_dir,))
    # Should have a non-empty new + modified list
    assert len(result.new_files) >= 1
    assert len(result.modified_files) >= 1


# ============= Helpers ======================================================

def test_fingerprint_deterministic(tmp_path: Path) -> None:
    f = tmp_path / "f"
    f.write_text("hello")
    a = _fingerprint(f)
    b = _fingerprint(f)
    assert a == b
    # SHA-256 hex
    assert len(a) == 64


def test_fingerprint_changes_on_content(tmp_path: Path) -> None:
    f = tmp_path / "f"
    f.write_text("v1")
    a = _fingerprint(f)
    f.write_text("v2")
    b = _fingerprint(f)
    assert a != b


def test_fingerprint_none_for_missing(tmp_path: Path) -> None:
    assert _fingerprint(tmp_path / "no") is None


def test_baseline_load_save(tmp_path: Path) -> None:
    snap = {"/a": "hash1", "/b": "hash2"}
    _save_baseline(tmp_path, snap)
    loaded = _load_baseline(tmp_path)
    assert loaded == snap


def test_baseline_load_missing(tmp_path: Path) -> None:
    assert _load_baseline(tmp_path) is None


def test_baseline_load_corrupt(tmp_path: Path) -> None:
    p = tmp_path / "cache" / BASELINE_FILE
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    assert _load_baseline(tmp_path) is None


# ============= Edge cases ===================================================

def test_cron_handles_no_root_crontab(tmp_path: Path) -> None:
    """Missing root crontab is fine."""
    _setup_cron_dirs(tmp_path, {"job": "1\n"})
    result = check_cron_changes(
        state_dir=tmp_path,
        cron_dirs=(str(tmp_path / "etc" / "cron.d"),),
        root_crontab=str(tmp_path / "var" / "spool" / "cron" / "root"),
    )
    assert result.status == "ok"


def test_cron_handles_baseline_with_extra_files(tmp_path: Path) -> None:
    """If baseline has files that no longer exist, treat as deleted."""
    _setup_cron_dirs(tmp_path, {"job": "1\n"})
    check_cron_changes(state_dir=tmp_path)
    # Manually inject a fake entry into baseline
    bp = tmp_path / "cache" / BASELINE_FILE
    baseline = json.loads(bp.read_text())
    baseline["/old/file"] = "old-hash"
    bp.write_text(json.dumps(baseline))
    result = check_cron_changes(state_dir=tmp_path)
    # Old file is now "deleted" → warn
    assert result.status == "warn"
    assert "/old/file" in result.deleted_files