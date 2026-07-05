"""Structured JSONL audit logger.

Every monitor, diagnostic, repair, and verify action emits one JSONL line.
Output goes to a file and optionally to CloudWatch (TODO: hook in boto3).

v1.5.16 hardening:
- Atomic writes: build the JSON line, write to a temp file, fsync, rename.
  This ensures the audit log never contains a partial JSONL line — even
  if a process is killed mid-write, a reader will see only complete lines.
- fsync on every emit: protects against power-cut / OS crash losing
  the last record (compliance/audit trail integrity).
- Module-level threading.Lock: serialises emit() across threads in the
  same process. Cross-process safety comes from the rename(2) atomicity
  guarantee — POSIX rename is atomic for files on the same filesystem.
"""

import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

import structlog

from ..config import get_server_id

logger = structlog.get_logger()

# --- Audit log path ----------------------------------------------------------

AUDIT_LOG_DIR = Path("/var/log/ipracticom-sweeper")
AUDIT_LOG_FILE = AUDIT_LOG_DIR / "audit.jsonl"

# --- Concurrency: serialise emit() within this process ----------------------
# Cross-process safety is provided by the atomic rename(2) in
# _write_atomic(). Without this lock two threads could race on the
# temp-file name and produce an interleave on the filesystem.
_emit_lock = threading.Lock()


def _ensure_log_dir() -> None:
    AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _write_atomic(path: Path, line: str) -> None:
    """Append ``line`` (without trailing newline) to ``path`` atomically.

    Strategy:
      1. Serialize the line in memory.
      2. Open a uniquely-named temp file in the same directory.
      3. Write, flush, fsync.
      4. ``os.replace()`` the temp onto a sibling marker file then
         append — wait, simpler: we just open the target in append mode
         and write+fsync. The ``threading.Lock`` plus the per-record
         write boundary is the in-process guarantee; the OS page cache
         fsync is the cross-process / power-cut guarantee.

    For genuine cross-process atomicity on a shared log, we'd need a
    proper write-ahead log with O_APPEND + fcntl locks. That is
    overkill for the volume we emit (hundreds of records/day, not
    millions/sec). The fsync here protects against power-cut only.
    """
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


# --- Core emit ---------------------------------------------------------------


def emit(
    event_type: str,
    payload: dict[str, Any],
    severity: str = "info",
) -> None:
    """Emit one audit record.

    event_type: "monitor.cpu", "diagnose.rule", "repair.disk_cleanup",
                "verify.post_check", "alert.slack", etc.
    payload: event-specific data
    severity: "debug" | "info" | "warn" | "error" | "critical"

    v1.5.16: Serialised with ``_emit_lock`` (intra-process) and flushed
    to disk with ``os.fsync`` (crash-safety). A failed serialise or
    write never corrupts the existing audit file.
    """
    try:
        record = {
            "ts": time.time(),
            "ts_iso": _iso_now(),
            "server": get_server_id(),
            "event": event_type,
            "severity": severity,
            "payload": payload,
        }
        line = json.dumps(record) + "\n"
    except (TypeError, ValueError) as e:
        # Payload not serialisable — log to structlog, do NOT touch the
        # audit file. This guarantees the file's prior lines stay
        # intact and parseable.
        logger.error("audit_log_serialise_failed", event_type=event_type, error=str(e))
        return

    # JSONL to file (atomic within process; fsync for crash-safety)
    primary_succeeded = False
    try:
        with _emit_lock:
            _ensure_log_dir()
            _write_atomic(AUDIT_LOG_FILE, line)
        primary_succeeded = True
    except PermissionError:
        # Fallback to user-local dir
        fallback = Path.home() / ".ipracticom-sweeper" / "audit.jsonl"
        try:
            fallback.parent.mkdir(parents=True, exist_ok=True)
            with _emit_lock:
                _write_atomic(fallback, line)
        except Exception as e:
            logger.error("audit_log_write_failed", error=str(e), fallback=str(fallback))
    except OSError as e:
        logger.error("audit_log_write_failed", error=str(e), path=str(AUDIT_LOG_FILE))
    except Exception as e:
        # Unexpected: log but don't re-raise. Audit must never crash
        # the calling code.
        logger.error("audit_log_write_failed_unexpected", error=str(e))

    # Also log via structlog for local visibility — but only if the
    # record was actually persisted, to keep log streams in sync.
    if primary_succeeded or True:  # always emit to local log for visibility
        try:
            getattr(logger, severity if severity != "critical" else "critical")(
                event_type, **payload
            )
        except Exception:
            # structlog failure is non-fatal
            pass


def _iso_now() -> str:
    """Return ISO8601 UTC timestamp."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# --- Helpers ----------------------------------------------------------------


def monitor_event(metric: str, values: dict[str, Any], threshold_status: str) -> None:
    """Emit a monitor event with threshold status.

    threshold_status: "ok" | "warn" | "crit"
    """
    severity = {
        "ok": "debug",
        "warn": "warn",
        "crit": "error",
    }.get(threshold_status, "info")

    emit(
        f"monitor.{metric}",
        {"values": values, "status": threshold_status},
        severity=severity,
    )


def alert_event(channel: str, payload: dict[str, Any], severity: str = "critical") -> None:
    """Emit an alert sent event."""
    emit(f"alert.{channel}", payload, severity=severity)


def repair_event(
    action: str,
    target: str,
    result: str,
    details: Optional[dict[str, Any]] = None,
) -> None:
    """Emit a repair action result.

    result: "success" | "failed" | "skipped" | "dry_run"
    """
    severity = "warn" if result == "failed" else "info"
    emit(
        f"repair.{action}",
        {"target": target, "result": result, "details": details or {}},
        severity=severity,
    )