"""Uptime / boot time monitor.

Reads /proc/stat (btime) to detect reboots. A reboot is a hard reset of all
process state, so:
  - Any "stale" alerts from before the boot are no longer relevant
  - The state store should be told to reset its in-memory caches
  - Repeated reboots in a short window = instability (kernel panic, OOM, etc.)

This is one of the cheapest monitors we have: just one file read.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any


_PROC_STAT = Path("/proc/stat")


def get_boot_time() -> float | None:
    """Read btime (Unix epoch seconds when the system booted).

    Returns None if /proc/stat is unreadable or btime is missing
    (e.g. running on macOS for tests).
    """
    try:
        with _PROC_STAT.open() as f:
            for line in f:
                # btime is always on its own line
                if line.startswith("btime "):
                    return float(line.split()[1])
    except (OSError, ValueError):
        return None
    return None


def get_uptime_seconds() -> float | None:
    """Seconds since boot. Returns None if we can't determine boot time."""
    boot = get_boot_time()
    if boot is None:
        return None
    return max(0.0, time.time() - boot)


def format_uptime(seconds: float) -> str:
    """Human-friendly uptime: '5d 3h', '47m 12s', etc."""
    if seconds < 0:
        return "unknown"
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def collect() -> dict[str, Any]:
    """Collect uptime metrics. Always returns a dict (may have None values)."""
    boot = get_boot_time()
    now = time.time()
    uptime = (now - boot) if boot is not None else None
    return {
        "boot_time": boot,
        "boot_time_iso": _iso(boot) if boot is not None else None,
        "uptime_seconds": uptime,
        "uptime_human": format_uptime(uptime) if uptime is not None else "unknown",
        "collected_at": now,
    }


def evaluate(values: dict[str, Any], rules: dict) -> str:
    """Apply rules. Default: warn if uptime is suspiciously short (frequent reboot).

    rules shape (all optional):
        uptime:
          short_uptime_warn_seconds: 300     # < 5 min = warn
          short_uptime_crit_seconds: 60      # < 1 min = crit
    """
    uptime = values.get("uptime_seconds")
    if uptime is None:
        # Can't tell — don't alert, just skip
        return "ok"

    uptime_rules = rules.get("uptime", {}) if isinstance(rules, dict) else {}
    crit_threshold = uptime_rules.get("short_uptime_crit_seconds", 60)
    warn_threshold = uptime_rules.get("short_uptime_warn_seconds", 300)

    if uptime < crit_threshold:
        return "crit"
    if uptime < warn_threshold:
        return "warn"
    return "ok"


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
