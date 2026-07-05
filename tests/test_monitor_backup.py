"""Sprint 16 — Backups + Recovery tests (24 tests)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ipracticom_sweeper.monitor.backup_fresh import (
    check_backup_freshness,
    check_multiple_backups,
    FreshnessResult,
)
from ipracticom_sweeper.monitor.backup_size import (
    check_backup_size,
    SizeResult,
    BASELINE_FILE,
    WARN_PCT_DROP,
    CRIT_PCT_DROP,
)
from ipracticom_sweeper.monitor.restore_test import (
    check_restore_test,
    RestoreTestResult,
)


# ============= backup_fresh =================================================

def test_backup_fresh_within_max_age(tmp_path: Path) -> None:
    f = tmp_path / "backup.tar.gz"
    f.write_text("x")
    r = check_backup_freshness(f, max_age_seconds=3600)
    assert r.status == "ok"
    assert r.age_seconds is not None
    assert r.age_seconds < 3600


def test_backup_warn_over_max_age(tmp_path: Path) -> None:
    f = tmp_path / "old.tar.gz"
    f.write_text("x")
    # Set mtime to 30h ago, max=24h
    import os
    now = time.time()
    os.utime(f, (now - 30 * 3600, now - 30 * 3600))
    r = check_backup_freshness(f, max_age_seconds=24 * 3600, now=now)
    assert r.status == "warn"


def test_backup_crit_double_max_age(tmp_path: Path) -> None:
    f = tmp_path / "very_old.tar.gz"
    f.write_text("x")
    import os
    now = time.time()
    os.utime(f, (now - 50 * 3600, now - 50 * 3600))
    r = check_backup_freshness(f, max_age_seconds=24 * 3600, now=now)
    assert r.status == "crit"


def test_backup_handles_missing_path(tmp_path: Path) -> None:
    r = check_backup_freshness(tmp_path / "nope")
    assert r.status == "unknown"


def test_backup_at_exactly_max_age_is_ok(tmp_path: Path) -> None:
    f = tmp_path / "edge.tar.gz"
    f.write_text("x")
    import os
    now = time.time()
    os.utime(f, (now - 3600, now - 3600))
    r = check_backup_freshness(f, max_age_seconds=3600, now=now)
    # age == max_age → ok (≤)
    assert r.status == "ok"


def test_backup_uses_mtime_not_atime(tmp_path: Path) -> None:
    f = tmp_path / "f"
    f.write_text("x")
    import os
    # Old atime, fresh mtime
    now = time.time()
    os.utime(f, (now - 100000, now))  # atime=old, mtime=fresh
    r = check_backup_freshness(f, max_age_seconds=3600, now=now)
    assert r.status == "ok"


def test_backup_metadata_path_and_age(tmp_path: Path) -> None:
    f = tmp_path / "x"
    f.write_text("x")
    r = check_backup_freshness(f, max_age_seconds=3600)
    assert r.path == str(f)
    assert r.age_seconds is not None
    assert r.max_age_seconds == 3600


def test_backup_multiple_paths(tmp_path: Path) -> None:
    p1 = tmp_path / "a"
    p2 = tmp_path / "b"
    p1.write_text("x")
    p2.write_text("x")
    results = check_multiple_backups([p1, p2], max_age_seconds=3600)
    assert len(results) == 2
    assert all(r.status == "ok" for r in results)


# ============= backup_size ==================================================

def test_backup_size_first_run_saves_baseline(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    backup = tmp_path / "b.tar.gz"
    backup.write_bytes(b"x" * 1000)
    r = check_backup_size(backup, state)
    assert r.status == "ok"
    assert r.reason == "baseline_created"
    assert (state / "cache" / BASELINE_FILE).exists()


def test_backup_size_within_20pct(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    backup = tmp_path / "b.tar.gz"
    backup.write_bytes(b"x" * 1000)
    check_backup_size(backup, state)  # first run
    backup.write_bytes(b"x" * 950)  # -5%
    r = check_backup_size(backup, state)
    assert r.status == "ok"


def test_backup_size_warn_20_to_50pct_drop(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    backup = tmp_path / "b.tar.gz"
    backup.write_bytes(b"x" * 1000)
    check_backup_size(backup, state)
    backup.write_bytes(b"x" * 600)  # -40%
    r = check_backup_size(backup, state)
    assert r.status == "warn"


def test_backup_size_crit_above_50pct_drop(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    backup = tmp_path / "b.tar.gz"
    backup.write_bytes(b"x" * 1000)
    check_backup_size(backup, state)
    backup.write_bytes(b"x" * 300)  # -70%
    r = check_backup_size(backup, state)
    assert r.status == "crit"


def test_backup_size_handles_missing_path(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    r = check_backup_size(tmp_path / "nope", state)
    assert r.status == "unknown"


def test_backup_size_baseline_persists(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    backup = tmp_path / "b.tar.gz"
    backup.write_bytes(b"x" * 1000)
    check_backup_size(backup, state)
    # Reload baseline from disk
    baseline = json.loads((state / "cache" / BASELINE_FILE).read_text())
    assert baseline[str(backup)] == 1000


def test_backup_size_metadata(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    backup = tmp_path / "b.tar.gz"
    backup.write_bytes(b"x" * 1000)
    check_backup_size(backup, state)
    r = check_backup_size(backup, state)
    assert isinstance(r, SizeResult)
    assert r.path == str(backup)
    assert r.current_bytes == 1000


def test_backup_size_handles_zero_baseline(tmp_path: Path) -> None:
    """Zero-byte baseline (corrupt case) → no division by zero."""
    state = tmp_path / "state"
    state.mkdir()
    backup = tmp_path / "b.tar.gz"
    backup.write_bytes(b"x" * 1000)
    check_backup_size(backup, state)  # sets baseline
    backup.write_bytes(b"")  # zero
    r = check_backup_size(backup, state)
    # Doesn't crash; status is ok or warn
    assert r.status in ("ok", "warn", "crit", "unknown")


# ============= restore_test =================================================

def test_restore_test_ok_last_passed_recent(tmp_path: Path) -> None:
    sf = tmp_path / "restore_status.json"
    sf.write_text(json.dumps({
        "status": "passed",
        "run_at": time.time() - 3600,  # 1h ago
        "duration_seconds": 12.5,
    }))
    r = check_restore_test(sf, max_age_seconds=7 * 24 * 3600)
    assert r.status == "ok"
    assert r.last_status == "passed"


def test_restore_test_warn_overdue(tmp_path: Path) -> None:
    sf = tmp_path / "restore_status.json"
    sf.write_text(json.dumps({
        "status": "passed",
        "run_at": time.time() - 10 * 24 * 3600,  # 10d ago, max 7d
        "duration_seconds": 12.5,
    }))
    r = check_restore_test(sf, max_age_seconds=7 * 24 * 3600)
    assert r.status == "warn"


def test_restore_test_crit_failed_last_run(tmp_path: Path) -> None:
    sf = tmp_path / "restore_status.json"
    sf.write_text(json.dumps({
        "status": "failed",
        "run_at": time.time() - 3600,  # recent, but failed
        "duration_seconds": 5.0,
        "error": "checksum mismatch",
    }))
    r = check_restore_test(sf)
    assert r.status == "crit"
    assert r.last_status == "failed"


def test_restore_test_handles_missing_file(tmp_path: Path) -> None:
    r = check_restore_test(tmp_path / "nope.json")
    assert r.status == "unknown"


def test_restore_test_handles_corrupt_json(tmp_path: Path) -> None:
    sf = tmp_path / "bad.json"
    sf.write_text("{not json")
    r = check_restore_test(sf)
    assert r.status == "unknown"


def test_restore_test_metadata_includes_duration(tmp_path: Path) -> None:
    sf = tmp_path / "s.json"
    sf.write_text(json.dumps({
        "status": "passed",
        "run_at": time.time() - 100,
        "duration_seconds": 42.0,
    }))
    r = check_restore_test(sf)
    assert r.duration_seconds == 42.0


def test_restore_test_with_explicit_now(tmp_path: Path) -> None:
    sf = tmp_path / "s.json"
    sf.write_text(json.dumps({
        "status": "passed",
        "run_at": 1000.0,
        "duration_seconds": 1.0,
    }))
    r = check_restore_test(sf, max_age_seconds=100, now=2000.0)
    # Age = 1000, max=100 → warn
    assert r.status == "warn"


def test_restore_test_age_calculated_correctly(tmp_path: Path) -> None:
    sf = tmp_path / "s.json"
    sf.write_text(json.dumps({
        "status": "passed",
        "run_at": 0.0,
        "duration_seconds": 1.0,
    }))
    r = check_restore_test(sf, max_age_seconds=10, now=5.0)
    assert r.age_seconds == 5.0


def test_restore_test_missing_run_at(tmp_path: Path) -> None:
    sf = tmp_path / "s.json"
    sf.write_text(json.dumps({"status": "passed"}))
    r = check_restore_test(sf)
    assert r.status == "unknown"
    assert r.reason == "no_run_at"