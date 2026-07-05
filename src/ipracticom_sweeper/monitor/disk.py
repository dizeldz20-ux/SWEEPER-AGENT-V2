"""Disk metrics: usage, inode, mount health.

Parses `df -hP` for usage and `df -iP` for inode counts.
Read-only mounts get flagged as anomalies.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

from .._log import log_suppressed


def _run_df(flag: str = "") -> list[dict[str, Any]]:
    """Run `df` with the given flag, return parsed lines."""
    cmd = ["df", "-P"]
    if flag:
        cmd.append(flag)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []

    lines = out.strip().split("\n")
    if len(lines) < 2:
        return []

    results = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        # Filesystem 1024-blocks Used Available Capacity Mounted-on
        filesystem, size, used, avail, capacity, mount = parts[:6]
        try:
            size_kb = int(size)
            used_kb = int(used)
            avail_kb = int(avail)
        except ValueError as e:
            log_suppressed("disk_df_parse", e)
            continue
        results.append({
            "filesystem": filesystem,
            "size_kb": size_kb,
            "used_kb": used_kb,
            "available_kb": avail_kb,
            "used_percent": (used_kb / size_kb * 100.0) if size_kb else 0.0,
            "mount": mount,
        })
    return results


def _is_read_only(mount: str) -> bool:
    """Check /proc/mounts for read-only flag on a mount point."""
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 4:
                    continue
                if parts[1] == mount:
                    return "ro" in parts[3].split(",")
    except Exception as e:
        log_suppressed("disk_is_readonly", e)
    return False


def collect() -> dict[str, Any]:
    """Collect disk usage for all mounted filesystems."""
    usage = _run_df()
    inode = _run_df("-i")

    # Index inodes by filesystem
    inode_by_fs = {row["filesystem"]: row for row in inode}

    mounts = []
    for row in usage:
        fs = row["filesystem"]
        inode_row = inode_by_fs.get(fs, {})
        inodes_total = inode_row.get("size_kb", 0)  # df -i uses 'inodes' field
        inodes_used = inode_row.get("used_kb", 0)
        # df -i uses Inodes/IUsed/IFree/IIUsed% — different schema
        inode_used_pct = 0.0
        if inodes_total:
            inode_used_pct = (inodes_used / inodes_total) * 100.0

        mounts.append({
            "filesystem": fs,
            "mount": row["mount"],
            "size_kb": row["size_kb"],
            "used_kb": row["used_kb"],
            "used_percent": round(row["used_percent"], 2),
            "inode_used_percent": round(inode_used_pct, 2),
            "read_only": _is_read_only(row["mount"]),
        })

    # Sort by used_percent desc so most-pressured mounts surface first
    mounts.sort(key=lambda m: m["used_percent"], reverse=True)

    return {
        "mounts": mounts,
        "mount_count": len(mounts),
    }


def evaluate(values: dict[str, Any], rules: dict) -> str:
    """Check all mounts; worst status wins."""
    crit = rules["disk"]["used_percent_crit"]
    warn = rules["disk"]["used_percent_warn"]
    ro_mounts = set(rules["disk"].get("read_only_mounts", []))

    worst = "ok"
    for m in values["mounts"]:
        # RO mounts that should be RO but aren't
        if m["mount"] in ro_mounts and not m["read_only"]:
            worst = _worse(worst, "warn")

        if m["used_percent"] >= crit:
            worst = _worse(worst, "crit")
        elif m["used_percent"] >= warn:
            worst = _worse(worst, "warn")

        inode_pct = m["inode_used_percent"]
        if inode_pct >= rules["disk"]["inode_used_percent_warn"]:
            worst = _worse(worst, "warn")

    return worst


def _worse(a: str, b: str) -> str:
    rank = {"ok": 0, "warn": 1, "crit": 2}
    return b if rank[b] > rank[a] else a