"""Process health: zombies, stuck procs, top CPU consumers."""

from __future__ import annotations

import os
from typing import Any

from .._log import log_suppressed


def _read_proc_stat() -> list[dict[str, Any]]:
    """Read all /proc/[pid]/stat files. Returns basic info per process."""
    results = []
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except Exception:
        return []

    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat") as f:
                stat = f.read()
            # /proc/[pid]/stat format: pid (comm) state ppid ...
            # comm is between ( and ) which may contain spaces — find last )
            rpar = stat.rfind(")")
            if rpar < 0 or rpar + 2 >= len(stat):
                continue
            fields = stat[rpar + 2:].split()
            state = fields[0]
            utime = int(fields[11])
            stime = int(fields[12])
            starttime = int(fields[19])
            results.append({
                "pid": int(pid),
                "state": state,
                "utime_jiffies": utime,
                "stime_jiffies": stime,
                "starttime_jiffies": starttime,
            })
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError) as e:
            log_suppressed("processes_proc_stat", e)
            continue

    return results


def count_by_state() -> dict[str, int]:
    """Count processes by /proc state (R, S, D, Z, ...)."""
    procs = _read_proc_stat()
    counts = {}
    for p in procs:
        counts[p["state"]] = counts.get(p["state"], 0) + 1
    return counts


def collect() -> dict[str, Any]:
    """Collect process health snapshot."""
    states = count_by_state()

    return {
        "total_processes": sum(states.values()),
        "states": states,
        "zombie_count": states.get("Z", 0),
        "uninterruptible_count": states.get("D", 0),
        "running_count": states.get("R", 0),
    }


def evaluate(values: dict[str, Any], rules: dict) -> str:
    """Apply rules; return 'ok' | 'warn' | 'crit'."""
    if values["zombie_count"] >= rules["processes"]["zombie_count_warn"] * 5:
        return "crit"
    if values["zombie_count"] >= rules["processes"]["zombie_count_warn"]:
        return "warn"
    # High D-state count = I/O hang risk
    if values["uninterruptible_count"] >= 50:
        return "warn"
    return "ok"