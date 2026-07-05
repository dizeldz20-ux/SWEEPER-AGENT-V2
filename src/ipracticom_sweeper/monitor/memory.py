"""Memory metrics: RAM and swap from /proc/meminfo."""

from __future__ import annotations

from typing import Any

from .._log import log_suppressed


def _read_meminfo() -> dict[str, int]:
    """Parse /proc/meminfo into a {key: kB} dict."""
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, _, value = line.partition(":")
            # value like "16384256 kB"
            value = value.strip()
            if value.endswith(" kB"):
                value = value[:-3]
            try:
                info[key.strip()] = int(value)
            except ValueError as e:
                log_suppressed("memory_meminfo_parse", e)
                continue
    return info


def collect() -> dict[str, Any]:
    """Collect RAM + swap metrics, both absolute and percent."""
    info = _read_meminfo()

    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", info.get("MemFree", 0))
    used = total - available
    used_pct = (used / total * 100.0) if total else 0.0

    swap_total = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    swap_used = swap_total - swap_free
    swap_used_pct = (swap_used / swap_total * 100.0) if swap_total else 0.0

    # Buffers and cache — context, not always alertable
    buffers = info.get("Buffers", 0)
    cached = info.get("Cached", 0)
    sreclaim = info.get("SReclaimable", 0)

    return {
        "ram_total_kb": total,
        "ram_used_kb": used,
        "ram_available_kb": available,
        "ram_used_percent": round(used_pct, 2),
        "swap_total_kb": swap_total,
        "swap_used_kb": swap_used,
        "swap_used_percent": round(swap_used_pct, 2),
        "buffers_kb": buffers,
        "cached_kb": cached + sreclaim,
        "dirty_kb": info.get("Dirty", 0),
        "writeback_kb": info.get("Writeback", 0),
    }


def evaluate(values: dict[str, Any], rules: dict) -> str:
    """Apply rules; return 'ok' | 'warn' | 'crit'."""
    ram = values["ram_used_percent"]
    if ram >= rules["memory"]["used_percent_crit"]:
        return "crit"
    if ram >= rules["memory"]["used_percent_warn"]:
        return "warn"
    if values["swap_used_percent"] >= rules["memory"]["swap_used_percent_warn"]:
        return "warn"
    return "ok"