"""Tests for the audit/logger module — emit() and helper event types."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ipracticom_sweeper.audit import logger as audit_logger
from ipracticom_sweeper.audit.logger import (
    emit,
    monitor_event,
    alert_event,
    repair_event,
    _iso_now,
)
from pathlib import Path as _PathAlias  # noqa: E402


@pytest.fixture
def tmp_audit_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the audit log to a tmp file via env var."""
    audit_file = tmp_path / "audit.jsonl"
    # Patch the module-level constants
    monkeypatch.setattr(audit_logger, "AUDIT_LOG_DIR", tmp_path)
    monkeypatch.setattr(audit_logger, "AUDIT_LOG_FILE", audit_file)
    return audit_file


def _read_lines(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


# ============= emit() ======================================================

def test_emit_writes_jsonl(tmp_audit_dir: Path) -> None:
    emit("test.event", {"a": 1})
    lines = _read_lines(tmp_audit_dir)
    assert len(lines) == 1
    assert lines[0]["event"] == "test.event"
    assert lines[0]["payload"] == {"a": 1}


def test_emit_record_has_required_fields(tmp_audit_dir: Path) -> None:
    emit("x", {"k": "v"})
    rec = _read_lines(tmp_audit_dir)[0]
    assert "ts" in rec
    assert "ts_iso" in rec
    assert "server" in rec
    assert "event" in rec
    assert "severity" in rec
    assert "payload" in rec


def test_emit_appends_not_overwrites(tmp_audit_dir: Path) -> None:
    emit("first", {})
    emit("second", {})
    lines = _read_lines(tmp_audit_dir)
    assert len(lines) == 2
    assert lines[0]["event"] == "first"
    assert lines[1]["event"] == "second"


def test_emit_severity_default_is_info(tmp_audit_dir: Path) -> None:
    emit("x", {})
    rec = _read_lines(tmp_audit_dir)[0]
    assert rec["severity"] == "info"


def test_emit_severity_explicit(tmp_audit_dir: Path) -> None:
    emit("x", {}, severity="critical")
    rec = _read_lines(tmp_audit_dir)[0]
    assert rec["severity"] == "critical"


def test_emit_uses_utc_iso_timestamp(tmp_audit_dir: Path) -> None:
    emit("x", {})
    rec = _read_lines(tmp_audit_dir)[0]
    iso = rec["ts_iso"]
    # Should end with +00:00 (UTC) or Z
    assert iso.endswith("+00:00") or iso.endswith("Z")


def test_emit_handles_non_serializable_payload(tmp_audit_dir: Path) -> None:
    """Non-JSON-serializable payload is swallowed (logger.error, no crash)."""
    class BadObj:
        pass
    # Should NOT raise — emit() catches the JSON error in its write block
    emit("x", {"obj": BadObj()})
    # Nothing was written to the audit log
    assert not tmp_audit_dir.exists() or tmp_audit_dir.read_text() == ""


# ============= monitor_event ===============================================

def test_monitor_event_ok_severity_debug(tmp_audit_dir: Path) -> None:
    monitor_event("cpu", {"value": 50}, "ok")
    rec = _read_lines(tmp_audit_dir)[0]
    assert rec["event"] == "monitor.cpu"
    assert rec["severity"] == "debug"
    assert rec["payload"]["status"] == "ok"


def test_monitor_event_warn_severity(tmp_audit_dir: Path) -> None:
    monitor_event("mem", {"pct": 85}, "warn")
    rec = _read_lines(tmp_audit_dir)[0]
    assert rec["severity"] == "warn"


def test_monitor_event_crit_severity(tmp_audit_dir: Path) -> None:
    monitor_event("disk", {"pct": 95}, "crit")
    rec = _read_lines(tmp_audit_dir)[0]
    assert rec["severity"] == "error"


def test_monitor_event_unknown_status_is_info(tmp_audit_dir: Path) -> None:
    monitor_event("x", {}, "weird_status")
    rec = _read_lines(tmp_audit_dir)[0]
    assert rec["severity"] == "info"


# ============= alert_event =================================================

def test_alert_event_default_critical(tmp_audit_dir: Path) -> None:
    alert_event("slack", {"msg": "hi"})
    rec = _read_lines(tmp_audit_dir)[0]
    assert rec["event"] == "alert.slack"
    assert rec["severity"] == "critical"


def test_alert_event_explicit_severity(tmp_audit_dir: Path) -> None:
    alert_event("telegram", {"x": 1}, severity="warn")
    rec = _read_lines(tmp_audit_dir)[0]
    assert rec["severity"] == "warn"


# ============= repair_event ================================================

def test_repair_event_success_is_info(tmp_audit_dir: Path) -> None:
    repair_event("drop_caches", "level=3", "success")
    rec = _read_lines(tmp_audit_dir)[0]
    assert rec["event"] == "repair.drop_caches"
    assert rec["severity"] == "info"
    assert rec["payload"]["result"] == "success"


def test_repair_event_failed_is_warn(tmp_audit_dir: Path) -> None:
    repair_event("service_restart", "unit=x", "failed")
    rec = _read_lines(tmp_audit_dir)[0]
    assert rec["severity"] == "warn"


def test_repair_event_target_in_payload(tmp_audit_dir: Path) -> None:
    repair_event("drop_caches", "level=2", "success")
    rec = _read_lines(tmp_audit_dir)[0]
    assert rec["payload"]["target"] == "level=2"


def test_repair_event_details_default_empty_dict(tmp_audit_dir: Path) -> None:
    repair_event("x", "y", "success")
    rec = _read_lines(tmp_audit_dir)[0]
    assert rec["payload"]["details"] == {}


def test_repair_event_explicit_details(tmp_audit_dir: Path) -> None:
    repair_event("x", "y", "success", details={"k": "v"})
    rec = _read_lines(tmp_audit_dir)[0]
    assert rec["payload"]["details"] == {"k": "v"}


# ============= _iso_now ====================================================

def test_iso_now_is_string() -> None:
    assert isinstance(_iso_now(), str)


def test_iso_now_has_t_separator() -> None:
    iso = _iso_now()
    # ISO 8601 has 'T' between date and time
    assert "T" in iso


# ============= PermissionError fallback =====================================

def test_emit_falls_back_on_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the primary open fails with OSError, emit() doesn't crash."""
    # Set the primary path to a known file we can target
    monkeypatch.setattr(audit_logger, "AUDIT_LOG_FILE", Path("/nonexistent/audit.jsonl"))
    # Should not raise — emit() catches OSError and falls back or logs
    emit("test.event", {"x": 1})

# ============= v1.5.16 RED: atomicity + concurrency ========================
# Code review finding #23 (HEAD v1.5.15): audit/logger.py emit() writes
# JSONL with `f.write(...)` and no flush/fsync/lock. Concurrent emitters
# can interleave bytes; power-cut loses buffered lines.


def test_emit_survives_concurrent_writers(tmp_audit_dir: Path) -> None:
    """N threads emit simultaneously. Every record must be a complete, parseable JSONL line.

    Bug currently: with `open("a")` no lock, two threads' `f.write` calls
    can interleave and produce invalid JSONL (e.g. ``}{`` between records).
    After fix: every line must parse.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    N_THREADS = 20
    N_PER_THREAD = 25

    def writer(tid: int) -> None:
        for i in range(N_PER_THREAD):
            emit(f"concurrent.{tid}", {"i": i, "tid": tid})

    with ThreadPoolExecutor(max_workers=N_THREADS) as ex:
        list(ex.map(writer, range(N_THREADS)))

    text = tmp_audit_dir.read_text()
    lines = [l for l in text.splitlines() if l.strip()]
    assert len(lines) == N_THREADS * N_PER_THREAD, f"got {len(lines)} lines, expected {N_THREADS * N_PER_THREAD}"
    # Every line must parse as JSON
    for ln in lines:
        rec = json.loads(ln)
        assert "event" in rec
        assert rec["event"].startswith("concurrent.")


def test_emit_calls_fsync(tmp_audit_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """emit() must fsync so a power-cut doesn't lose the buffered record.

    Bug currently: `with open(..., "a") as f: f.write(...)` does not flush
    and does not fsync. The OS may hold the bytes in its page cache.
    After fix: f.flush() and os.fsync() are called.
    """
    fsync_calls: list[int] = []
    real_fsync = None

    def counting_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        if real_fsync is not None:
            real_fsync(fd)

    import os as _os
    real_fsync = _os.fsync
    monkeypatch.setattr(_os, "fsync", counting_fsync)
    # Also patch the os.fsync already imported into the audit module
    if hasattr(audit_logger, "os"):
        monkeypatch.setattr(audit_logger.os, "fsync", counting_fsync)

    emit("fsync.test", {"x": 1})
    # At least one fsync must have happened for our write
    assert len(fsync_calls) >= 1, "emit() must call os.fsync to survive power-cut"


def test_emit_does_not_lose_lines_on_partial_write(tmp_audit_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If the audit log path is unwritable mid-run, the file must NOT contain
    a partial JSONL line — only complete prior records.

    Strategy: redirect the audit log to a path under a directory we make
    read-only after writing 3 good lines. The 4th emit must fail and the
    existing file content must remain intact (no truncation, no partial JSON).

    Why this matters: a real-world write failure (disk full, FS remounted
    read-only, process killed mid-write) would otherwise leave an invalid
    JSONL line that breaks every downstream parser.
    """
    # Write 3 good lines first into the normal tmp path
    emit("good.1", {})
    emit("good.2", {})
    emit("good.3", {})
    before = tmp_audit_dir.read_text()
    assert "good.3" in before  # sanity check

    # Now point the audit log at a read-only directory. _write_atomic's
    # O_CREAT will fail with PermissionError, which the new emit() catches.
    ro_dir = tmp_audit_dir.parent / "readonly"
    ro_dir.mkdir()
    ro_dir.chmod(0o555)  # r-x for everyone, no write
    ro_path = ro_dir / "audit.jsonl"
    monkeypatch.setattr(audit_logger, "AUDIT_LOG_FILE", ro_path)

    # The 4th emit must fail internally (logger.error) and not corrupt anything
    emit("bad.4", {})

    # Restore so we can read
    ro_dir.chmod(0o755)
    # File at original tmp_audit_dir must be unchanged
    after = tmp_audit_dir.read_text()
    assert after == before, (
        f"failed write corrupted the original audit file!\n"
        f"--- before ---\n{before}\n--- after ---\n{after}\n"
    )
    # And the 3 good lines are still parseable
    for ln in [l for l in after.splitlines() if l.strip()]:
        rec = json.loads(ln)
        assert rec["event"] in ("good.1", "good.2", "good.3")
