"""File descriptor monitor.

Reads /proc/sys/fs/file-nr to get system-wide FD usage
(allocated, unused, max). Walks /proc/[pid]/fd/ to get per-process
FD counts. Used to catch 'too many open files' before apps crash.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import os

from .._log import log_suppressed


@dataclass
class FdSystemStats:
    """System-wide file descriptor usage."""

    allocated: int
    unused: int
    max: int
    used_percent: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "allocated": self.allocated,
            "unused": self.unused,
            "max": self.max,
            "used_percent": self.used_percent,
        }


def parse_proc_fs_filenr(content: str) -> FdSystemStats:
    """Parse /proc/sys/fs/file-nr: 'allocated unused maximum'.

    used = allocated - unused (per kernel semantics)
    used% = used / max * 100
    """
    try:
        parts = content.split()
        if len(parts) < 3:
            return FdSystemStats(allocated=0, unused=0, max=0, used_percent=0.0)
        allocated = int(parts[0])
        unused = int(parts[1])
        max_fd = int(parts[2])
        used = max(0, allocated - unused)
        if max_fd <= 0:
            used_pct = 0.0
        else:
            used_pct = min(100.0, (used / max_fd) * 100.0)
        return FdSystemStats(
            allocated=allocated,
            unused=unused,
            max=max_fd,
            used_percent=round(used_pct, 2),
        )
    except (ValueError, IndexError):
        return FdSystemStats(allocated=0, unused=0, max=0, used_percent=0.0)


def collect_fd_system() -> FdSystemStats:
    """Read /proc/sys/fs/file-nr and return system FD stats."""
    try:
        with open("/proc/sys/fs/file-nr") as f:
            return parse_proc_fs_filenr(f.read().strip())
    except (OSError, ValueError):
        return FdSystemStats(allocated=0, unused=0, max=0, used_percent=0.0)


def collect_top_fd_processes(top_n: int = 5) -> list[dict]:
    """Return top N processes by open-FD count."""
    counts: list[tuple[int, str, int]] = []
    for pid_dir in os.listdir("/proc"):
        if not pid_dir.isdigit():
            continue
        pid = int(pid_dir)
        fd_dir = f"/proc/{pid}/fd"
        try:
            names = os.listdir(fd_dir)
        except (OSError, PermissionError) as e:
            log_suppressed("fd_check_proc_read", e)
            continue
        if not names:
            continue
        # Get process name
        try:
            with open(f"/proc/{pid}/comm") as f:
                name = f.read().strip()
        except (OSError, PermissionError):
            name = "?"
        counts.append((pid, name, len(names)))

    counts.sort(key=lambda x: x[2], reverse=True)
    return [
        {"pid": p, "name": n, "fd_count": c}
        for p, n, c in counts[:top_n]
    ]


def evaluate(values: dict, rules: dict) -> str:
    """Return 'ok' | 'warn' | 'crit'.

    crit if used% > 95, warn if used% > 80.
    """
    used_pct = values.get("system", {}).get("used_percent", 0)
    warn_pct = rules.get("fd_check", {}).get("warn_percent", 80)
    crit_pct = rules.get("fd_check", {}).get("crit_percent", 95)
    if used_pct > crit_pct:
        return "crit"
    if used_pct > warn_pct:
        return "warn"
    return "ok"
