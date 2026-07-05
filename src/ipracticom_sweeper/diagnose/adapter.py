"""Adapter from monitor output to diagnose input.

The monitor layer produces a snapshot like:
    snapshot["modules"]["cpu"]["values"] = {
        "load_5min": 0.59,
        "iowait_percent": 0.24,
        ...
    }

The diagnose layer expects each module's findings to look like:
    findings["cpu"]["metrics"] = {
        "load_avg_5min": 0.59,
        "iowait_percent": 0.24,
        ...
    }

This module does the translation + field name normalization. Each monitor
module has slightly different field names; the adapter is where we agree on
canonical names that the diagnose engine uses.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


def adapt_for_diagnose(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Convert monitor snapshot → diagnose-compatible findings dict.

    Returns:
        {
            "cpu": {"metrics": {...}},
            "memory": {"metrics": {...}},
            "disk": {"metrics": {...}},
            "services": {"metrics": {...}},
            "security": {"metrics": {...}},
        }
    """
    modules = snapshot.get("modules", {})
    findings: dict[str, Any] = {}

    # --- CPU ---
    cpu_values = modules.get("cpu", {}).get("values", {})
    if cpu_values:
        findings["cpu"] = {
            "metrics": {
                "load_avg_5min": cpu_values.get("load_5min"),
                "load_avg_5min_per_core": cpu_values.get("load_5min_per_core"),
                "iowait_percent": cpu_values.get("iowait_percent"),
                "steal_percent": cpu_values.get("steal_percent"),
                "cores": cpu_values.get("cores"),
            }
        }

    # --- Memory ---
    mem_values = modules.get("memory", {}).get("values", {})
    if mem_values:
        findings["memory"] = {
            "metrics": {
                "used_percent": mem_values.get("ram_used_percent"),
                "swap_used_percent": mem_values.get("swap_used_percent"),
                "available_kb": mem_values.get("ram_available_kb"),
            }
        }

    # --- Disk ---
    disk_values = modules.get("disk", {}).get("values", {})
    if disk_values:
        # Normalize mountpoint field name: monitor uses "mount", diagnose expects "mountpoint"
        raw_mounts = disk_values.get("mounts", [])
        normalized_mounts = []
        for m in raw_mounts:
            if not isinstance(m, dict):
                continue
            normalized_mounts.append({
                "mountpoint": m.get("mount"),
                "filesystem": m.get("filesystem"),
                "used_percent": m.get("used_percent"),
                "inode_used_percent": m.get("inode_used_percent"),
                "read_only": m.get("read_only", False),
                "options": "ro" if m.get("read_only") else "rw",
            })
        findings["disk"] = {
            "metrics": {
                "mounts": normalized_mounts,
                "mount_count": disk_values.get("mount_count"),
            }
        }

    # --- Services ---
    svc_values = modules.get("services", {}).get("values", {})
    if svc_values:
        failed = svc_values.get("failed_units", [])
        # Normalize: monitor returns list of strings (unit names), diagnose expects list of dicts
        if failed and isinstance(failed[0], str):
            failed = [{"unit": name} for name in failed]
        findings["services"] = {
            "metrics": {
                "failed_units": failed,
                "failed_count": svc_values.get("failed_count"),
            }
        }

    # --- Security ---
    sec_values = modules.get("security", {}).get("values", {})
    if sec_values:
        findings["security"] = {
            "metrics": {
                "failed_ssh_per_min": sec_values.get("failed_ssh_per_minute"),
                "sudo_failures_per_hour": sec_values.get("sudo_failures"),
            }
        }

    # --- Network (passed through even if not used by diagnose yet) ---
    net_values = modules.get("network", {}).get("values", {})
    if net_values:
        findings["network"] = {
            "metrics": {
                "rx_drops_total": net_values.get("rx_drops_total"),
                "tx_drops_total": net_values.get("tx_drops_total"),
                "close_wait_count": net_values.get("close_wait_count"),
                "listen_count": net_values.get("listen_count"),
            }
        }

    # --- Logs (passed through) ---
    log_values = modules.get("logs", {}).get("values", {})
    if log_values:
        findings["logs"] = {
            "metrics": {
                "error_rate_per_min": log_values.get("error_rate_per_minute"),
                "oom_count": log_values.get("oom_count"),
                "by_priority": log_values.get("by_priority", {}),
            }
        }

    logger.debug("adapter_complete", modules=list(findings.keys()))
    return findings