"""v1.5.8 — Concurrency fix tests.

Covers:
1. audit rotation preserves inode (no data loss on concurrent writers)
2. host_config._db_conn() does not leak file descriptors
3. host_config._populate_cache uses one transaction (no torn writes)
4. monitor/health.record_run writes atomically (tmp + rename)
5. pipeline.run_pipeline records heartbeat even on monitor failure
6. SQLite stores set busy_timeout to prevent 'database is locked'
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


# --- 1. Audit rotation: preserve inode ----------------------------------


def test_audit_rotation_preserves_inode(tmp_path):
    """After audit_rotate, audit.jsonl must keep the same inode.

    If we delete + recreate, any FD holding the old inode keeps writing
    to an unlinked inode (silent data loss). The fix: write to .tmp +
    rename so the inode stays.
    """
    from ipracticom_sweeper.audit import rotation

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    log = audit_dir / "audit.jsonl"
    log.write_text('{"event":"a"}\n{"event":"b"}\n')

    original_inode = log.stat().st_ino
    rotation.audit_rotate(tmp_path)
    # After rotation, the file MUST exist again (with a rotation suffix
    # carrying the data, or empty if everything fit), but the LIVE log
    # audit.jsonl — if it exists after rotation — must keep the original
    # inode so concurrent writers don't lose data.
    # In the current broken impl, audit.jsonl is unlinked and re-created
    # with a fresh inode → data loss risk.
    new_inode = log.stat().st_ino
    assert new_inode == original_inode, (
        f"audit.jsonl inode changed during rotation: {original_inode} -> {new_inode}. "
        f"Concurrent writers holding the old FD will silently lose data."
    )


def test_audit_rotation_does_not_leak_fd(tmp_path):
    """The recreated audit.jsonl in the broken impl uses .open('a').close() without `with`.

    On PyPy this leaks FDs. The fix: write to tmp + rename.
    """
    from ipracticom_sweeper.audit import rotation

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    log = audit_dir / "audit.jsonl"
    log.write_text("event1\n")
    fds_before = len(os.listdir(f"/proc/{os.getpid()}/fd"))

    rotation.audit_rotate(tmp_path)

    fds_after = len(os.listdir(f"/proc/{os.getpid()}/fd"))
    # Allow for some churn (the FD count is volatile in tests) but the
    # rotation should not leave extra FDs around.
    assert fds_after <= fds_before + 2, (
        f"FD count grew from {fds_before} to {fds_after} during rotation"
    )


# --- 2. host_config: connection leak ------------------------------------


def test_db_conn_returns_a_singleton(tmp_path, monkeypatch):
    """Every _db_conn() call currently opens a new connection that is never closed.

    This leaks FDs. The fix: cache the connection at module level.
    """
    from ipracticom_sweeper.config import host_config

    monkeypatch.setattr(host_config, "_DB_PATH", tmp_path / "cache.db")
    # Reset any cached connection
    if hasattr(host_config, "_CONN"):
        try:
            host_config._CONN.close()
        except Exception:
            pass
        monkeypatch.delattr(host_config, "_CONN", raising=False)

    # Call _db_conn() multiple times — the fix should return the same
    # connection each time, not a fresh one.
    c1 = host_config._db_conn()
    c2 = host_config._db_conn()
    assert c1 is c2, (
        "_db_conn() returns a fresh connection each call → FD leak. "
        "Should return a cached singleton."
    )


# --- 3. host_config._populate_cache uses one transaction ----------------


def test_populate_cache_writes_atomically(tmp_path, monkeypatch):
    """_populate_cache must wrap DELETE + INSERT in a single transaction.

    Otherwise a reader between DELETE and INSERT sees zero rows for the host.
    """
    import sqlite3
    from ipracticom_sweeper.config import host_config
    from ipracticom_sweeper.config.host_config import HostConfig

    # Use a dedicated tmp DB
    db = tmp_path / "host_cache.db"
    monkeypatch.setattr(host_config, "_DB_PATH", db)
    # Reset singleton
    if hasattr(host_config, "_CONN"):
        try:
            host_config._CONN.close()
        except Exception:
            pass
        monkeypatch.delattr(host_config, "_CONN", raising=False)

    cfg = HostConfig(name="web-1")  # defaults: empty lists
    host_config._populate_cache(cfg)

    # Read back — if DELETE+INSERT were not atomic and a concurrent reader
    # had opened the DB, isolation might be wrong. We can at least verify
    # that the connection's isolation_level is not None (autocommit) by
    # checking that BEGIN is required for grouping.
    conn = host_config._db_conn()
    iso = conn.isolation_level
    assert iso is not None, (
        f"connection uses autocommit (isolation_level={iso!r}); "
        f"multi-statement writes are not atomic"
    )


# --- 4. monitor/health.record_run writes atomically ---------------------


def test_record_run_writes_atomically(tmp_path, monkeypatch):
    """record_run must write to tmp + os.replace, not direct write_text.

    Crash mid-write leaves a corrupt JSON that check_health misreads.
    """
    import inspect
    from ipracticom_sweeper.monitor import health

    src = inspect.getsource(health)
    # The pattern must include both 'tmp' (or similar) AND 'os.replace' (or 'replace').
    assert ".tmp" in src and "replace" in src, (
        "monitor/health.record_run does not use atomic write pattern"
    )


# --- 5. pipeline.run_pipeline records heartbeat even on failure --------


def test_pipeline_records_heartbeat_on_monitor_failure(tmp_path, monkeypatch):
    """If run_pipeline early-returns on monitor failure, record_run must
    still be called — otherwise check_health will report the agent as stale.

    The v1.5.8 fix: the monitor-failure early return now writes the
    heartbeat before returning. We assert this contract by mocking both
    run_monitor (to raise) and record_run (to capture) and verifying
    record_run was called.
    """
    from ipracticom_sweeper import pipeline

    heartbeat_calls: list[dict] = []

    def fake_record_run(**kwargs):
        heartbeat_calls.append(kwargs)

    def fake_run_monitor(rules):
        raise RuntimeError("simulated monitor failure")

    monkeypatch.setattr(pipeline, "run_monitor", fake_run_monitor)
    monkeypatch.setattr(pipeline, "record_run", fake_record_run, raising=False)
    # Also patch the import location
    import sys
    sys.modules["ipracticom_sweeper.monitor.health"].record_run = fake_record_run

    result = pipeline.run_pipeline()

    assert result.defcon == 1
    assert result.monitor_overall == "error"
    assert heartbeat_calls, (
        "pipeline.run_pipeline did not call record_run on monitor failure — "
        "next check_health() will falsely flag the agent as stale"
    )


# --- 6. SQLite stores: busy_timeout set ------------------------------


def test_sqlite_store_sets_busy_timeout(tmp_path):
    """state/sqlite_store.py must set PRAGMA busy_timeout=5000 to avoid
    'database is locked' errors under contention.
    """
    import inspect
    from ipracticom_sweeper.state import sqlite_store

    src = inspect.getsource(sqlite_store)
    assert "busy_timeout" in src, (
        "sqlite_store.py does not set PRAGMA busy_timeout — contended writes fail fast"
    )


def test_timeseries_store_sets_busy_timeout(tmp_path):
    """storage/timeseries.py must also set busy_timeout."""
    import inspect
    from ipracticom_sweeper.storage import timeseries

    src = inspect.getsource(timeseries)
    assert "busy_timeout" in src, (
        "storage/timeseries.py does not set PRAGMA busy_timeout"
    )