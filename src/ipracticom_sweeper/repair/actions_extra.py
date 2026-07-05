"""Sprint 15 — Additional repair actions.

Five new repair functions for common operational issues:
- nginx log rotation (SIGUSR1 graceful reopen)
- FreeSWITCH cache flush (fs_cli)
- FreeSWITCH XML reload (fs_cli reloadxml)
- FreeSWITCH voicemail lock cleanup
- PostgreSQL VACUUM ANALYZE

All follow the same pattern as actions.py: snapshot → execute → RepairResult.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .actions import (
    RepairResult,
    Snapshot,
    _new_snapshot,
    register,
    SNAPSHOT_DIR,
    _VALID_SQL_IDENTIFIER,
    _VALID_SYSTEMD_UNIT,
)

from .._log import log_suppressed


# --- repair_rotate_nginx_logs ----------------------------------------------

@register("rotate_nginx_logs")
def repair_rotate_nginx_logs(
    log_path: str = "/var/log/nginx/access.log",
    keep_rotations: int = 5,
) -> RepairResult:
    """Rotate nginx access log by renaming + sending SIGUSR1 to nginx.

    Keeps the last `keep_rotations` rotated files; older ones are deleted.
    """
    snap = _new_snapshot(
        action="repair_rotate_nginx_logs",
        target=log_path,
        log_path=log_path,
        keep_rotations=keep_rotations,
        rotated_at=datetime.now(timezone.utc).isoformat(),
    )
    snap.save()

    start = time.time()
    log_p = Path(log_path)

    if not log_p.exists():
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="repair_rotate_nginx_logs",
            target=log_path,
            success=False,
            snapshot_id=snap.id,
            message=f"nginx log not found: {log_path}",
            duration_ms=duration,
            error="log_not_found",
        )

    try:
        # Rotate: access.log → access.log.1, .1 → .2, ..., .keep_rotations+1 deleted
        size_before = log_p.stat().st_size
        # First, explicitly delete any rotation older than what we'll keep
        for old in log_p.parent.glob(f"{log_p.name}.*"):
            try:
                idx = int(old.suffix.lstrip(".").split(".")[-1])
            except (ValueError, IndexError) as e:
                log_suppressed("actions_extra_rotation_parse", e)
                continue
            if idx > keep_rotations:
                old.unlink()
        # Cascade: .keep → .keep+1 (gets deleted above), ..., .1 → .2, current → .1
        for i in range(keep_rotations, 0, -1):
            older = log_p.with_suffix(f".log.{i}")
            newer = log_p.with_suffix(f".log.{i+1}")
            if older.exists():
                # Delete destination first if it exists (pre-cleanup may miss)
                if newer.exists():
                    newer.unlink()
                os.rename(str(older), str(newer))
        # Move current to .1
        os.rename(str(log_p), str(log_p.with_suffix(".log.1")))
        # Recreate empty file
        log_p.touch()

        # Send SIGUSR1 to nginx master to reopen log files (best effort)
        try:
            subprocess.run(
                ["killall", "-USR1", "nginx"],
                capture_output=True, timeout=5, check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            log_suppressed("actions_extra_nginx_reload", e)

        duration = int((time.time() - start) * 1000)
        snap.metadata["bytes_freed"] = size_before
        snap.metadata["rotated_to"] = str(log_p.with_suffix(".log.1"))
        snap.save()  # persist updated metadata
        return RepairResult(
            action="repair_rotate_nginx_logs",
            target=log_path,
            success=True,
            snapshot_id=snap.id,
            message=f"rotated {log_path} ({size_before} bytes)",
            duration_ms=duration,
            output=str(log_p.with_suffix(".log.1")),
        )
    except Exception as e:
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="repair_rotate_nginx_logs",
            target=log_path,
            success=False,
            snapshot_id=snap.id,
            message=f"rotation failed: {e}",
            duration_ms=duration,
            error=str(e),
        )


# --- repair_drop_freeswitch_cache ------------------------------------------

@register("drop_freeswitch_cache")
def repair_drop_freeswitch_cache(
    fs_cli_path: str = "fs_cli",
    host: str = "127.0.0.1",
    port: int = 8021,
    password: str = "",
) -> RepairResult:
    """Flush FreeSWITCH core cache via `fs_cli cache flush`."""
    snap = _new_snapshot(
        action="repair_drop_freeswitch_cache",
        target=f"{host}:{port}",
        host=host, port=port,
        executed_at=datetime.now(timezone.utc).isoformat(),
    )
    snap.save()

    start = time.time()
    try:
        status_proc = subprocess.run(
            [fs_cli_path, "-H", host, "-P", str(port), "-p", password, "status"],
            capture_output=True, text=True, timeout=5,
        )
        if status_proc.returncode != 0:
            duration = int((time.time() - start) * 1000)
            return RepairResult(
                action="repair_drop_freeswitch_cache",
                target=f"{host}:{port}",
                success=False,
                snapshot_id=snap.id,
                message="FreeSWITCH not running",
                duration_ms=duration,
                error="fs_not_running",
            )

        cache_proc = subprocess.run(
            [fs_cli_path, "-H", host, "-P", str(port), "-p", password, "cache", "flush"],
            capture_output=True, text=True, timeout=10,
        )
        duration = int((time.time() - start) * 1000)
        if cache_proc.returncode != 0:
            return RepairResult(
                action="repair_drop_freeswitch_cache",
                target=f"{host}:{port}",
                success=False,
                snapshot_id=snap.id,
                message=f"fs_cli cache flush failed: {cache_proc.stderr.strip()[:200]}",
                duration_ms=duration,
                error=cache_proc.stderr.strip()[:200],
            )

        return RepairResult(
            action="repair_drop_freeswitch_cache",
            target=f"{host}:{port}",
            success=True,
            snapshot_id=snap.id,
            message=f"FreeSWITCH cache flushed via {fs_cli_path}",
            duration_ms=duration,
            output=cache_proc.stdout.strip()[:200],
        )
    except subprocess.TimeoutExpired:
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="repair_drop_freeswitch_cache",
            target=f"{host}:{port}",
            success=False,
            snapshot_id=snap.id,
            message="fs_cli timeout",
            duration_ms=duration,
            error="timeout",
        )
    except FileNotFoundError:
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="repair_drop_freeswitch_cache",
            target=f"{host}:{port}",
            success=False,
            snapshot_id=snap.id,
            message=f"fs_cli not found at {fs_cli_path}",
            duration_ms=duration,
            error="fs_cli_not_found",
        )


# --- repair_reload_freeswitch_config ---------------------------------------

@register("reload_freeswitch_config")
def repair_reload_freeswitch_config(
    fs_cli_path: str = "fs_cli",
    host: str = "127.0.0.1",
    port: int = 8021,
    password: str = "",
) -> RepairResult:
    """Reload FreeSWITCH XML config via `fs_cli reloadxml`."""
    snap = _new_snapshot(
        action="repair_reload_freeswitch_config",
        target=f"{host}:{port}",
        host=host, port=port,
        reloaded_at=datetime.now(timezone.utc).isoformat(),
    )
    snap.save()

    start = time.time()
    try:
        status_proc = subprocess.run(
            [fs_cli_path, "-H", host, "-P", str(port), "-p", password, "status"],
            capture_output=True, text=True, timeout=5,
        )
        if status_proc.returncode != 0:
            duration = int((time.time() - start) * 1000)
            return RepairResult(
                action="repair_reload_freeswitch_config",
                target=f"{host}:{port}",
                success=False,
                snapshot_id=snap.id,
                message="FreeSWITCH not running",
                duration_ms=duration,
                error="fs_not_running",
            )

        reload_proc = subprocess.run(
            [fs_cli_path, "-H", host, "-P", str(port), "-p", password, "reloadxml"],
            capture_output=True, text=True, timeout=10,
        )
        duration = int((time.time() - start) * 1000)
        if reload_proc.returncode != 0:
            err = reload_proc.stderr.strip()[:200] or "reloadxml returned non-zero"
            return RepairResult(
                action="repair_reload_freeswitch_config",
                target=f"{host}:{port}",
                success=False,
                snapshot_id=snap.id,
                message=f"reloadxml failed: {err}",
                duration_ms=duration,
                error=err,
            )

        return RepairResult(
            action="repair_reload_freeswitch_config",
            target=f"{host}:{port}",
            success=True,
            snapshot_id=snap.id,
            message="FreeSWITCH config reloaded",
            duration_ms=duration,
            output=reload_proc.stdout.strip()[:200],
        )
    except subprocess.TimeoutExpired:
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="repair_reload_freeswitch_config",
            target=f"{host}:{port}",
            success=False,
            snapshot_id=snap.id,
            message="fs_cli timeout",
            duration_ms=duration,
            error="timeout",
        )
    except FileNotFoundError:
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="repair_reload_freeswitch_config",
            target=f"{host}:{port}",
            success=False,
            snapshot_id=snap.id,
            message=f"fs_cli not found at {fs_cli_path}",
            duration_ms=duration,
            error="fs_cli_not_found",
        )


# --- repair_clear_freeswitch_voicemail_locks -------------------------------

@register("clear_freeswitch_voicemail_locks")
def repair_clear_freeswitch_voicemail_locks(
    lock_dir: str = "/var/lib/freeswitch/storage/voicemail/.locks",
    max_age_seconds: int = 3600,
) -> RepairResult:
    """Remove stale voicemail lock files older than max_age_seconds."""
    snap = _new_snapshot(
        action="repair_clear_freeswitch_voicemail_locks",
        target=lock_dir,
        lock_dir=lock_dir, max_age_seconds=max_age_seconds,
        scanned_at=datetime.now(timezone.utc).isoformat(),
    )
    snap.save()

    start = time.time()
    lock_path = Path(lock_dir)

    if not lock_path.exists():
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="repair_clear_freeswitch_voicemail_locks",
            target=lock_dir,
            success=False,
            snapshot_id=snap.id,
            message=f"lock dir not found: {lock_dir}",
            duration_ms=duration,
            error="dir_missing",
        )

    try:
        now = time.time()
        removed: list[str] = []
        kept: list[str] = []
        for fp in lock_path.iterdir():
            if not fp.is_file():
                continue
            age = now - fp.stat().st_mtime
            if age > max_age_seconds:
                fp.unlink()
                removed.append(fp.name)
            else:
                kept.append(fp.name)
        duration = int((time.time() - start) * 1000)
        snap.metadata["locks_removed"] = len(removed)
        snap.metadata["locks_kept"] = len(kept)
        snap.save()  # persist updated metadata
        return RepairResult(
            action="repair_clear_freeswitch_voicemail_locks",
            target=lock_dir,
            success=True,
            snapshot_id=snap.id,
            message=f"removed {len(removed)} stale lock(s), kept {len(kept)} recent",
            duration_ms=duration,
            output=f"removed={len(removed)} kept={len(kept)}",
        )
    except Exception as e:
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="repair_clear_freeswitch_voicemail_locks",
            target=lock_dir,
            success=False,
            snapshot_id=snap.id,
            message=f"lock cleanup failed: {e}",
            duration_ms=duration,
            error=str(e),
        )


# --- repair_pg_vacuum ------------------------------------------------------

@register("pg_vacuum")
def repair_pg_vacuum(
    table: str = "",
    connection_string: str = "postgresql://localhost/postgres",
    analyze: bool = True,
    timeout_s: int = 60,
    dry_run: bool = False,
) -> RepairResult:
    """Run VACUUM [ANALYZE] on a table (or all tables if empty)."""
    snap = _new_snapshot(
        action="repair_pg_vacuum",
        target=table or "(all tables)",
        table=table, analyze=analyze, dry_run=dry_run,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    snap.save()

    start = time.time()
    if dry_run:
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="repair_pg_vacuum",
            target=table or "(all tables)",
            success=True,
            snapshot_id=snap.id,
            message=f"dry_run: would VACUUM {table or 'all tables'}",
            duration_ms=duration,
        )

    table_clause = table if table else ""
    if table and not _VALID_SQL_IDENTIFIER.match(table):
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="pg_vacuum",
            target=table,
            success=False,
            snapshot_id=snap.id,
            message=f"invalid table identifier: {table!r}",
            duration_ms=duration,
            error="invalid_table_name",
        )
    analyze_clause = "ANALYZE" if analyze else ""
    sql = f"VACUUM {analyze_clause} {table_clause}".strip()

    try:
        proc = subprocess.run(
            ["psql", connection_string, "-c", sql],
            capture_output=True, text=True, timeout=timeout_s,
        )
        duration = int((time.time() - start) * 1000)
        if proc.returncode != 0:
            err = proc.stderr.strip()[:200] or "psql returned non-zero"
            return RepairResult(
                action="repair_pg_vacuum",
                target=table or "(all tables)",
                success=False,
                snapshot_id=snap.id,
                message=f"VACUUM failed: {err}",
                duration_ms=duration,
                error=err,
            )
        return RepairResult(
            action="repair_pg_vacuum",
            target=table or "(all tables)",
            success=True,
            snapshot_id=snap.id,
            message=f"VACUUM {analyze_clause} {table or 'all tables'} completed",
            duration_ms=duration,
            output=proc.stdout.strip()[:200],
        )
    except subprocess.TimeoutExpired:
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="repair_pg_vacuum",
            target=table or "(all tables)",
            success=False,
            snapshot_id=snap.id,
            message=f"VACUUM timed out after {timeout_s}s",
            duration_ms=duration,
            error="timeout",
        )
    except FileNotFoundError:
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="repair_pg_vacuum",
            target=table or "(all tables)",
            success=False,
            snapshot_id=snap.id,
            message="psql not found",
            duration_ms=duration,
            error="psql_not_found",
        )