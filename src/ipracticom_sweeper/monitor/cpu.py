"""CPU metrics: load average, steal time, iowait.

Reads from /proc/loadavg (load) and /proc/stat (CPU times including
steal/iowait — visible only on virtualized hosts).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def get_load_average() -> dict[str, float]:
    """Read /proc/loadavg. Returns 1min, 5min, 15min + running/total procs."""
    with open("/proc/loadavg") as f:
        parts = f.read().split()
    return {
        "load_1min": float(parts[0]),
        "load_5min": float(parts[1]),
        "load_15min": float(parts[2]),
        "running_procs": int(parts[3].split("/")[0]),
        "total_procs": int(parts[3].split("/")[1]),
    }


def get_cpu_times() -> dict[str, int]:
    """Read aggregate /proc/stat line (the 'cpu' line, not per-CPU).

    Returns jiffies (clock ticks) per state. Convert to percent via deltas
    across two samples if you need rates; this just returns the snapshot.
    """
    with open("/proc/stat") as f:
        line = f.readline()
    parts = line.split()
    # cpu user nice system idle iowait irq softirq steal guest guest_nice
    fields = [
        "user", "nice", "system", "idle", "iowait",
        "irq", "softirq", "steal", "guest", "guest_nice",
    ]
    return {name: int(parts[i + 1]) for i, name in enumerate(fields)}


def get_cpu_cores() -> int:
    """Return logical CPU core count."""
    return os.cpu_count() or 1


def collect() -> dict[str, Any]:
    """Collect all CPU metrics into one dict."""
    load = get_load_average()
    cores = get_cpu_cores()
    times = get_cpu_times()

    total = sum(times.values())
    if total > 0:
        steal_pct = (times["steal"] / total) * 100.0
        iowait_pct = (times["iowait"] / total) * 100.0
        idle_pct = (times["idle"] / total) * 100.0
    else:
        steal_pct = iowait_pct = idle_pct = 0.0

    return {
        "load_1min": load["load_1min"],
        "load_5min": load["load_5min"],
        "load_15min": load["load_15min"],
        "load_5min_per_core": round(load["load_5min"] / cores, 2),
        "cores": cores,
        "steal_percent": round(steal_pct, 2),
        "iowait_percent": round(iowait_pct, 2),
        "idle_percent": round(idle_pct, 2),
        "running_procs": load["running_procs"],
        "total_procs": load["total_procs"],
    }


def evaluate(values: dict[str, Any], rules: dict) -> str:
    """Apply rules to values; return 'ok' | 'warn' | 'crit'."""
    load = values["load_5min_per_core"]
    if load >= rules["cpu"]["load_avg_5min_crit"]:
        return "crit"
    if load >= rules["cpu"]["load_avg_5min_warn"]:
        return "warn"
    if values["steal_percent"] >= rules["cpu"]["steal_percent_warn"]:
        return "warn"
    if values["iowait_percent"] >= rules["cpu"]["iowait_percent_warn"]:
        return "warn"
    return "ok"