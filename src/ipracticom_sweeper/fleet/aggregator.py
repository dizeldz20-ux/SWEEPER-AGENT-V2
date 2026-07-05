"""Fleet aggregator: multi-host overview."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HostSummary:
    host_id: str
    defcon: int
    defcon_label: str
    problems_count: int
    last_seen: float
    modules: dict[str, str] = field(default_factory=dict)


@dataclass
class FleetSummary:
    total_hosts: int
    healthy: int  # DEFCON 5
    warning: int   # DEFCON 3-4
    critical: int  # DEFCON 1-2
    hosts: list[HostSummary] = field(default_factory=list)

    @property
    def overall_defcon(self) -> int:
        if self.critical > 0:
            return 2
        if self.warning > 0:
            return 4
        return 5


def ssm_to_aggregator_format(connector_name: str, ssnap) -> dict[str, Any]:
    """Convert an SSM HostSnapshot to the shape aggregate() expects.

    The remote collect script returns:
        {host, collected_at, uptime_seconds, load:{1m,5m,15m},
         memory:{total_kb,available_kb,used_percent}, disk:{used_percent,path},
         top_processes, failed_units, kernel, boot_id}

    The aggregator wants:
        {server, defcon, defcon_label, problems_found, ts, modules}

    If the snapshot is unavailable (SSM failed), we still return a dict with
    defcon=1 (critical, "we can't reach it") and reason="..." so the host
    shows up in the fleet view with a clear error badge.
    """
    if not getattr(ssnap, "available", False):
        return {
            "server": connector_name,
            "defcon": 1,
            "defcon_label": "red",
            "problems_found": 1,
            "ts": time.time(),
            "modules": {"ssm": "crit"},
            "_reason": getattr(ssnap, "reason", "unavailable"),
        }

    data = getattr(ssnap, "data", {}) or {}
    load = data.get("load") or {}
    mem = data.get("memory") or {}
    disk = data.get("disk") or {}
    failed_units = data.get("failed_units") or []

    load_5m = float(load.get("5m") or 0)
    mem_used = float(mem.get("used_percent") or 0)
    disk_used = float(disk.get("used_percent") or 0)
    failed_n = len(failed_units)

    # Per-module status flags. Thresholds mirror the local box's rules/default.yaml
    cpu_status = "warn" if load_5m >= 4 else "ok"
    mem_status = "crit" if mem_used >= 95 else ("warn" if mem_used >= 80 else "ok")
    disk_status = "crit" if disk_used >= 95 else ("warn" if disk_used >= 80 else "ok")
    services_status = "crit" if failed_n > 0 else "ok"

    problems = sum(1 for s in (cpu_status, mem_status, disk_status, services_status) if s != "ok")

    # Overall DEFCON from worst module
    if "crit" in (cpu_status, mem_status, disk_status, services_status):
        defcon, label = 2, "red"
    elif "warn" in (cpu_status, mem_status, disk_status, services_status):
        defcon, label = 4, "yellow"
    else:
        defcon, label = 5, "green"

    # Prefer the remote's own collected_at timestamp if present, else now.
    # Note: the remote script writes ISO 8601 strings like "2026-06-29T07:00:00Z",
    # not unix timestamps. We pass the raw value through and let the template
    # decide how to display it (the modal shows the ISO string verbatim).
    ts_raw = data.get("collected_at") or data.get("ts")
    if not ts_raw:
        ts = time.time()
    elif isinstance(ts_raw, (int, float)):
        ts = float(ts_raw)
    else:
        # ISO string — store as-is for the modal to format; aggregator only needs
        # it for sort/display, and datetime.fromisoformat is forgiving enough.
        ts = time.time()  # fallback; modal uses the raw string anyway

    return {
        "server": connector_name,
        "defcon": defcon,
        "defcon_label": label,
        "problems_found": problems,
        "ts": ts,
        "modules": {
            "cpu": cpu_status,
            "memory": mem_status,
            "disk": disk_status,
            "services": services_status,
        },
        "_raw": data,  # keep raw data for the modal
    }


def aggregate(snapshots: list[dict[str, Any]]) -> FleetSummary:
    """Combine multiple agent snapshots into a fleet summary.

    Expected snapshot shape:
        {
            "server": "host1",
            "defcon": 4,
            "defcon_label": "yellow",
            "problems_found": 2,
            "ts": 1234567890.0,
            "modules": {"cpu": "ok", "memory": "warn", ...}
        }
    """
    hosts = []
    healthy = warning = critical = 0

    for snap in snapshots:
        host_id = snap.get("server", "unknown")
        defcon = snap.get("defcon", 5)
        summary = HostSummary(
            host_id=host_id,
            defcon=defcon,
            defcon_label=snap.get("defcon_label", "green"),
            problems_count=snap.get("problems_found", 0),
            last_seen=snap.get("ts", 0.0),
            modules=snap.get("modules", {}),
        )
        hosts.append(summary)
        if defcon <= 2:
            critical += 1
        elif defcon <= 4:
            warning += 1
        else:
            healthy += 1

    # Sort by defcon (worst first), then by host_id
    hosts.sort(key=lambda h: (h.defcon, h.host_id))

    return FleetSummary(
        total_hosts=len(snapshots),
        healthy=healthy,
        warning=warning,
        critical=critical,
        hosts=hosts,
    )