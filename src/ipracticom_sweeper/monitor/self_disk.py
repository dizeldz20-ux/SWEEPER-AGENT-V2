"""Slice 8.2: monitor the sweeper's own state directory.

If the state dir fills up, the sweeper crashes silently. This module
checks state-dir disk usage + inodes, and triggers an emergency
audit log rotation when ≥95% full.
"""
from __future__ import annotations

import gzip
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .._log import log_suppressed

DEFAULT_WARN_PCT = 80.0
DEFAULT_CRIT_PCT = 95.0
DEFAULT_INODE_WARN_PCT = 90.0
MAX_ROTATIONS = 5


@dataclass
class SelfDiskResult:
    """Outcome of a self-disk check."""

    status: str  # ok | warn | crit | unknown
    defcon: int
    state_dir_pct: Optional[float]
    inode_pct_used: Optional[float]
    rotation_triggered: bool
    rotated_files: int
    path: Optional[Path]
    error: Optional[str] = None


def find_state_dir() -> Path:
    """Locate the sweeper's state directory.

    Honours the canonical IPRACTICOM_SWEEPER_STATE_DIR (used by
    config/paths.py and monitor/checks.py); the legacy SWEEPER_STATE_DIR
    is still accepted so an operator override is never silently ignored.
    """
    env = os.environ.get("IPRACTICOM_SWEEPER_STATE_DIR") or os.environ.get("SWEEPER_STATE_DIR")
    if env:
        return Path(env)
    return Path("/var/lib/ipracticom-sweeper")


def _disk_usage_pct(path: str) -> Optional[float]:
    """Return disk usage % for the mount containing `path`."""
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        if total <= 0:
            return None
        used = total - free
        return (used / total) * 100.0
    except (OSError, AttributeError):
        return None


def _inode_usage_pct(path: str) -> Optional[float]:
    """Return inode usage % for the mount containing `path`."""
    try:
        st = os.statvfs(path)
        if st.f_files <= 0:
            return None
        used = st.f_files - st.f_favail
        return (used / st.f_files) * 100.0
    except (OSError, AttributeError):
        return None


def check_state_dir(
    state_dir: Optional[Path] = None,
    total_bytes: Optional[int] = None,
    used_bytes: Optional[int] = None,
    free_inodes: Optional[int] = None,
    total_inodes: Optional[int] = None,
    warn_pct: float = DEFAULT_WARN_PCT,
    crit_pct: float = DEFAULT_CRIT_PCT,
    inode_warn_pct: float = DEFAULT_INODE_WARN_PCT,
) -> SelfDiskResult:
    """Inspect the sweeper's state dir.

    Two ways to call:
      - Production: state_dir only — auto-resolves via statvfs
      - Tests: pass total_bytes/used_bytes directly to avoid filesystem
    """
    if state_dir is None:
        state_dir = find_state_dir()

    if not state_dir.exists():
        return SelfDiskResult(
            status="unknown",
            defcon=3,
            state_dir_pct=None,
            inode_pct_used=None,
            rotation_triggered=False,
            rotated_files=0,
            path=state_dir,
            error="state_dir_missing",
        )

    # Disk percent — use explicit args if given, else statvfs
    if total_bytes is not None and used_bytes is not None:
        if total_bytes <= 0:
            disk_pct: Optional[float] = None
        else:
            disk_pct = (used_bytes / total_bytes) * 100.0
    else:
        disk_pct = _disk_usage_pct(str(state_dir))

    # Inode percent
    inode_pct: Optional[float] = None
    if free_inodes is not None and total_inodes is not None:
        if total_inodes <= 0:
            inode_pct = None
        else:
            used_inodes = total_inodes - free_inodes
            inode_pct = (used_inodes / total_inodes) * 100.0
    else:
        inode_pct = _inode_usage_pct(str(state_dir))

    # Severity
    status = "ok"
    defcon = 5
    if disk_pct is not None:
        if disk_pct >= crit_pct:
            status = "crit"
            defcon = 2
        elif disk_pct >= warn_pct:
            status = "warn"
            defcon = 4

    # Inode override: if inode pressure is severe, force crit
    if inode_pct is not None and inode_pct >= inode_warn_pct:
        defcon = 2
        status = "crit"

    # Emergency rotation on crit
    rotation_triggered = False
    rotated_files = 0
    if status == "crit":
        rotated_files = audit_rotate(state_dir=state_dir)
        rotation_triggered = True

    return SelfDiskResult(
        status=status,
        defcon=defcon,
        state_dir_pct=disk_pct,
        inode_pct_used=inode_pct,
        rotation_triggered=rotation_triggered,
        rotated_files=rotated_files,
        path=state_dir,
    )


def audit_rotate(state_dir: Path, max_rotations: int = MAX_ROTATIONS) -> int:
    """Rotate `audit/audit.jsonl` → `.1`, dropping `.6+`.

    Returns the number of files removed.
    """
    audit_dir = state_dir / "audit"
    if not audit_dir.is_dir():
        return 0
    log = audit_dir / "audit.jsonl"
    if not log.exists():
        return 0

    removed = 0

    # Drop anything beyond max_rotations
    for i in range(max_rotations + 1, max_rotations + 10):
        stale = audit_dir / f"audit.jsonl.{i}"
        if stale.exists():
            try:
                stale.unlink()
                removed += 1
            except OSError as e:
                log_suppressed("self_disk_rotate_unlink", e)

    # Cascade: .N → .N+1
    for i in range(max_rotations, 0, -1):
        older = audit_dir / f"audit.jsonl.{i - 1 if i > 1 else 'jsonl'}" if i > 1 else log
        target = audit_dir / f"audit.jsonl.{i}"
        if older.exists():
            try:
                # If previous one was already gzipped, leave it; else compress
                if str(older).endswith(".jsonl") and i > 1:
                    tmp_target = target.with_suffix(target.suffix + ".tmp")
                    with older.open("rb") as src, gzip.open(tmp_target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    tmp_target.rename(target)
                else:
                    older.rename(target)
            except OSError as e:
                log_suppressed("self_disk_rotate_rename", e)

    # If the original log is still there (cascade top didn't move it),
    # move it to .1 + gzip
    if log.exists():
        first = audit_dir / "audit.jsonl.1"
        if not first.exists():
            try:
                tmp = audit_dir / "audit.jsonl.1.tmp"
                with log.open("rb") as src, gzip.open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                tmp.rename(first)
                log.unlink()
            except OSError as e:
                log_suppressed("self_disk_rotate_promote", e)

    return removed