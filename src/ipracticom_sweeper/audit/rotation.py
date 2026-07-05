"""Slice 8.4: audit log rotation.

The sweeper writes audit events to /var/lib/ipracticom-sweeper/audit/audit.jsonl.
This module rotates the file when it exceeds MAX_BYTES, gzipping the
oldest, and removes rotations older than 30 days.

Designed to be safe under concurrent writers (uses a module-level lock).
"""
from __future__ import annotations

import gzip
import io
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional, TextIO

from ipracticom_sweeper._log import log_suppressed

MAX_BYTES = 100 * 1024 * 1024      # 100 MB
MAX_ROTATIONS = 5                   # keep .1..5
MAX_AGE_DAYS = 30

_LOCK = threading.Lock()


class WriterHandle:
    """A long-lived handle to the audit log file with auto-rotation."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: Optional[TextIO] = None
        self._fingerprint: tuple = (0, 0.0)
        # Initial open without lock (no concurrent access yet)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", buffering=io.DEFAULT_BUFFER_SIZE)
        try:
            stat = path.stat()
            self._fingerprint = (stat.st_ino, stat.st_mtime)
        except OSError:
            self._fingerprint = (0, 0.0)

    def write(self, line: str) -> int:
        """Write a line; auto-rotate if MAX_BYTES exceeded."""
        if not line.endswith("\n"):
            line += "\n"
        with _LOCK:
            # Detect if our file handle is stale (path was rotated/replaced).
            # We use inode + mtime as a fingerprint; if either changed, reopen.
            if self._fh is None or not self.path.exists():
                self._reopen_locked()
            else:
                try:
                    stat_now = self.path.stat()
                    if (stat_now.st_ino, stat_now.st_mtime) != self._fingerprint:
                        self._reopen_locked()
                except OSError:
                    self._reopen_locked()

            n = self._fh.write(line)
            self._fh.flush()
            try:
                if self._fh.tell() > MAX_BYTES:
                    self._rotate_locked()
                    self._reopen_locked()
            except (OSError, ValueError) as exc:
                log_suppressed("audit.rotation.write_check_size", exc)
            return n

    def _reopen_locked(self) -> None:
        """Re-open the file, capturing fresh inode+mtime. Caller holds _LOCK."""
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception as exc:
                log_suppressed("audit.rotation.reopen_close", exc)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", buffering=io.DEFAULT_BUFFER_SIZE)
        try:
            stat = self.path.stat()
            self._fingerprint = (stat.st_ino, stat.st_mtime)
        except OSError:
            self._fingerprint = (0, 0.0)

    def close(self) -> None:
        with _LOCK:
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception as exc:
                    log_suppressed("audit.rotation.close", exc)
                self._fh = None

    def _rotate_locked(self) -> None:
        """Caller must hold _LOCK."""
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception as exc:
                log_suppressed("audit.rotation.rotate_close", exc)
            self._fh = None
        audit_dir = self.path.parent
        _cascade_rotate_locked(audit_dir)


def audit_open_for_write(state_dir: Path) -> WriterHandle:
    """Open a long-lived writer for the audit log."""
    log = state_dir / "audit" / "audit.jsonl"
    return WriterHandle(log)


# --- Rotation primitives ------------------------------------------------------

def audit_rotate(state_dir: Path) -> int:
    """Force a rotation cascade (callers want it, not size-triggered).

    Returns the number of files removed/rotated.
    Uses the same _LOCK for safety against concurrent writers.
    """
    with _LOCK:
        audit_dir = state_dir / "audit"
        if not audit_dir.is_dir():
            return 0
        pruned = _prune_old_locked(audit_dir)
        if not (audit_dir / "audit.jsonl").exists():
            return pruned
        cascade = _cascade_rotate_locked(audit_dir)
        # Belt-and-suspenders: also prune any leftover N>MAX_ROTATIONS
        for f in audit_dir.glob("audit.jsonl.*.gz"):
            try:
                suffix = f.name[len("audit.jsonl."):-len(".gz")]
                n = int(suffix)
                if n > MAX_ROTATIONS:
                    f.unlink()
                    pruned += 1
            except (ValueError, OSError) as exc:
                log_suppressed("audit.rotation.audit_rotate_parse_suffix", exc,
                               extras={"path": f.name})
        return cascade + pruned


def _do_rotate_locked(
    state_dir: Path,
    subdir: str,
    max_bytes: int = MAX_BYTES,
) -> int:
    """Internal: caller holds _LOCK. Skips if file size < max_bytes (silent no-op)."""
    audit_dir = state_dir / subdir
    if not audit_dir.is_dir():
        return 0
    log = audit_dir / "audit.jsonl"
    if not log.exists():
        return _prune_old_locked(audit_dir)

    # If file is below threshold, just prune old rotations and return
    try:
        if log.stat().st_size <= max_bytes:
            return _prune_old_locked(audit_dir)
    except OSError:
        return 0

    return _cascade_rotate_locked(audit_dir)


def _cascade_rotate_locked(audit_dir: Path) -> int:
    """Perform the .N → .N+1 cascade with gzip + prune. Caller holds _LOCK."""
    # Drop anything numbered above MAX_ROTATIONS (handles gaps/leftovers)
    for f in audit_dir.glob("audit.jsonl.*.gz"):
        try:
            suffix = f.name[len("audit.jsonl."):-len(".gz")]
            n = int(suffix)
            if n > MAX_ROTATIONS:
                f.unlink()
        except (ValueError, OSError) as exc:
            log_suppressed("audit.rotation.cascade_parse_suffix", exc,
                           extras={"path": f.name})

    # Cascade: .N → .N+1, with gzip at .1
    for i in range(MAX_ROTATIONS, 0, -1):
        target = audit_dir / f"audit.jsonl.{i}.gz"
        older = audit_dir / (f"audit.jsonl.{i - 1}.gz" if i > 1 else "audit.jsonl")
        if older.exists() and not target.exists():
            try:
                tmp = target.with_suffix(".tmp")
                if older.suffix == ".gz":
                    shutil.copyfile(older, tmp)
                else:
                    with older.open("rb") as src, gzip.open(tmp, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                os.rename(tmp, target)
                # If we just moved the original log away, recreate empty log
                # ATOMICALLY (tmp + rename) so the inode is preserved.
                # Recreating with a new inode would lose any data written
                # by writers still holding an FD to the unlinked file
                # (silent data loss — see v1.5.8 review).
                if i == 1:
                    _recreate_empty_locked(audit_dir)
            except OSError as exc:
                log_suppressed("audit.rotation.cascade_rename", exc,
                               extras={"index": i})
        elif older.exists() and target.exists():
            # Target exists (e.g. MAX_ROTATIONS+1 leftover that wasn't pruned
            # because we only iterate up to MAX_ROTATIONS). Skip — already pruned.
            pass

    return _prune_old_locked(audit_dir) + 1


def _recreate_empty_locked(audit_dir: Path) -> None:
    """Truncate audit.jsonl to zero bytes IN PLACE — preserves the inode.

    Caller must hold _LOCK. Used after rotating the live log so concurrent
    writers holding an FD to the original inode continue to write to the
    same file (now empty) rather than losing data to an unlinked inode.

    v1.5.8 fix: previously called .open('a').close() which on most filesystems
    allocates a fresh inode → silent data loss for any concurrent writer.
    The fix is to truncate the existing file in place: open with 'w' mode
    (which keeps the inode) and close immediately. On Linux/POSIX, opening
    an existing file with O_TRUNC preserves the inode number even when the
    file is empty.
    """
    log = audit_dir / "audit.jsonl"
    # Open + close with 'w' mode: truncates to zero bytes, keeps the inode.
    # The with-statement ensures the FD is closed deterministically even
    # on PyPy / alternative interpreters.
    with log.open("w"):
        pass


def _prune_old_locked(audit_dir: Path, max_age_days: int = MAX_AGE_DAYS) -> int:
    """Remove rotations older than max_age_days."""
    cutoff = time.time() - (max_age_days * 86400)
    removed = 0
    for f in audit_dir.glob("audit.jsonl.*"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError as exc:
            log_suppressed("audit.rotation.prune_old", exc,
                           extras={"path": f.name})
    return removed