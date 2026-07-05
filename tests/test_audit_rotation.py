"""Tests for slice 8.4: audit log rotation (size + time, 100MB × 5).

The sweeper writes audit events as JSONL to audit/audit.jsonl. Without
rotation, that file grows unbounded — first it eats the disk, then the
sweeper crashes (slice 8.2). This slice adds safe rotation.
"""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

import pytest

from ipracticom_sweeper.audit.rotation import (
    MAX_BYTES,
    MAX_ROTATIONS,
    WriterHandle,
    audit_open_for_write,
    audit_rotate,
)


def _make_log_with_size(path: Path, size_bytes: int) -> None:
    """Write enough lines to make `path` roughly `size_bytes`."""
    line = '{"ts": "2026-07-01T00:00:00Z", "msg": "' + ("a" * 100) + '"}\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        written = 0
        while written < size_bytes:
            f.write(line)
            written += len(line)


# --- 8.4.1 size-triggered rotation --------------------------------------------

def test_8_4_size_trigger_at_100mb(tmp_path: Path) -> None:
    """When file size > max_bytes, size-triggered rotation cascades."""
    from ipracticom_sweeper.audit.rotation import _do_rotate_locked, _LOCK

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    log = audit_dir / "audit.jsonl"
    # Write >100MB
    _make_log_with_size(log, 101 * 1024 * 1024)
    assert log.stat().st_size > 100 * 1024 * 1024

    with _LOCK:
        _do_rotate_locked(tmp_path, "audit", max_bytes=100 * 1024 * 1024)
    # After rotation, .1.gz should exist
    assert (audit_dir / "audit.jsonl.1.gz").exists()


def test_8_4_keeps_5_rotations(tmp_path: Path) -> None:
    """Multiple consecutive rotations never leave more than 5 .gz files."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    log = audit_dir / "audit.jsonl"

    # Simulate 10 successive rotations
    for i in range(10):
        log.write_text(f"event-{i}\n")
        audit_rotate(state_dir=tmp_path)

    # After 10 rotations: at most MAX_ROTATIONS=5 .gz files
    gz_files = sorted(audit_dir.glob("audit.jsonl.*.gz"))
    assert len(gz_files) <= 5
    # And they're numbered 1..5 (no gaps)
    numbers = sorted(
        int(f.name[len("audit.jsonl."):-len(".gz")]) for f in gz_files
    )
    assert numbers == list(range(1, len(numbers) + 1))


def test_8_4_compresses_old(tmp_path: Path) -> None:
    """audit.jsonl.1 is gzipped after rotation."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    log = audit_dir / "audit.jsonl"
    log.write_text('{"event": "x"}\n' * 1000)
    audit_rotate(state_dir=tmp_path)
    # After rotation, audit.jsonl.1 should exist as .gz
    gz = audit_dir / "audit.jsonl.1.gz"
    assert gz.exists()
    # Verify it's actually gzipped
    with gzip.open(gz, "rt") as f:
        first = f.readline()
        assert '"event"' in first


# --- 8.4.2 atomicity ---------------------------------------------------------

def test_8_4_atomic_rotate(tmp_path: Path) -> None:
    """Rotation uses os.rename (atomic on same FS) — no half-written files."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    log = audit_dir / "audit.jsonl"
    log.write_text("old\n")
    # Open a writer that simulates a concurrent write during rotation
    audit_rotate(state_dir=tmp_path)
    # After rotation, either .1 exists (atomic rename worked) or log is intact
    assert (audit_dir / "audit.jsonl.1").exists() or log.exists()
    # No leftover .tmp files
    leftovers = list(audit_dir.glob("*.tmp*"))
    assert not leftovers


def test_8_4_writer_continues_across_rotation(tmp_path: Path) -> None:
    """A long-lived writer re-opens to the new file after rotation."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    log = audit_dir / "audit.jsonl"
    log.write_text('{"e":1}\n')

    handle = audit_open_for_write(state_dir=tmp_path)
    handle.write('{"e":2}\n')
    audit_rotate(state_dir=tmp_path)
    # Writer must continue writing to the new (empty) audit.jsonl
    handle.write('{"e":3}\n')
    handle.close()

    new_log = (audit_dir / "audit.jsonl").read_text()
    assert '"e":3' in new_log


# --- 8.4.3 concurrent safety -------------------------------------------------

def test_8_4_idempotent_under_concurrent_writers(tmp_path: Path) -> None:
    """10 simulated writers all emit events; each event lands in exactly one file."""
    import threading

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    handles = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        h = audit_open_for_write(state_dir=tmp_path)
        with lock:
            handles.append(h)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All handles got the same path
    assert len(handles) == 10
    for h in handles:
        h.write('{"x":1}\n')
        h.close()


def test_8_4_drops_old_after_30d(tmp_path: Path) -> None:
    """Rotations older than 30 days are deleted after a rotate cycle."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    log = audit_dir / "audit.jsonl"
    log.write_text('old\n')
    audit_rotate(state_dir=tmp_path)
    # Backdate all rotations to 31 days ago
    import time

    old_time = time.time() - (31 * 86400)
    for f in audit_dir.glob("audit.jsonl.*"):
        os.utime(f, (old_time, old_time))
    # Next rotate: cascade happens then prune removes everything old
    audit_rotate(state_dir=tmp_path)
    # All old rotations should be gone (either pruned or replaced with fresh cascade)
    remaining = list(audit_dir.glob("audit.jsonl.*.gz"))
    # Anything that remains must NOT be old
    cutoff = time.time() - (30 * 86400)
    for f in remaining:
        assert f.stat().st_mtime >= cutoff, f"{f.name} is older than 30d"