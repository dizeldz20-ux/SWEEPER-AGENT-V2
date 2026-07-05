"""SMART disk health monitor.

Shells out to `smartctl -A -H` per disk to read SMART attributes and
health assessment. Used to predict disk failures days in advance.

Requires the `smartmontools` package (apt install smartmontools).
If smartctl is missing, returns empty list (graceful degradation).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import re
import shutil
import subprocess

from .._log import log_suppressed


@dataclass
class SmartDiskHealth:
    """Result of inspecting a single disk's SMART data."""

    device: str
    reallocated_sectors: int
    temperature_c: int | None
    overall_assessment: str | None
    parse_error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "reallocated_sectors": self.reallocated_sectors,
            "temperature_c": self.temperature_c,
            "overall_assessment": self.overall_assessment,
            "parse_error": self.parse_error,
        }


# Regex to match lines like "  5 Reallocated_Sector_Ct   0x0033   200   200   140    Pre-fail  Always       -       0"
ATTR_RE = re.compile(
    r"^\s*(\d+)\s+(\w+)\s+0x[0-9a-fA-F]+\s+\d+\s+\d+\s+\d+\s+\S+\s+\S+\s+\S+\s+(\S+)\s*$"
)
TEMP_RE = re.compile(
    r"^\s*194\s+Temperature_Celsius\s+0x[0-9a-fA-F]+\s+\d+\s+\d+\s+\d+\s+\S+\s+\S+\s+\S+\s+(\d+)\s*$"
)
HEALTH_RE = re.compile(r"SMART overall-health self-assessment test result:\s*(\w+)")


def parse_smartctl_output(device: str, output: str) -> SmartDiskHealth:
    """Parse `smartctl -A -H /dev/X` output into SmartDiskHealth."""
    reallocated = 0
    temperature: int | None = None
    overall: str | None = None

    for line in output.splitlines():
        # Try temperature first (specific)
        m = TEMP_RE.match(line)
        if m:
            try:
                temperature = int(m.group(1))
            except (ValueError, IndexError) as e:
                log_suppressed("smart_check_temperature_parse", e)
            continue
        # Try health line
        m = HEALTH_RE.search(line)
        if m:
            overall = m.group(1)
            continue
        # Try Reallocated_Sector_Ct (ID 5)
        if "Reallocated_Sector_Ct" in line:
            parts = line.split()
            # The RAW_VALUE is the last column
            try:
                reallocated = int(parts[-1])
            except (ValueError, IndexError) as e:
                log_suppressed("smart_check_reallocated_parse", e)

    return SmartDiskHealth(
        device=device,
        reallocated_sectors=reallocated,
        temperature_c=temperature,
        overall_assessment=overall,
        parse_error=None,
    )


def collect_smart_health(devices: list[str]) -> list[SmartDiskHealth]:
    """Collect SMART health for each device.

    Returns empty list if smartctl binary is not installed.
    Per-device errors (e.g. permission denied) are captured as parse_error.
    """
    smartctl = shutil.which("smartctl")
    if not smartctl:
        return []

    results: list[SmartDiskHealth] = []
    for device in devices:
        try:
            proc = subprocess.run(
                [smartctl, "-A", "-H", device],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0 and not proc.stdout:
                results.append(SmartDiskHealth(
                    device=device, reallocated_sectors=0,
                    temperature_c=None, overall_assessment=None,
                    parse_error=f"smartctl exit {proc.returncode}: {proc.stderr.strip()[:200]}",
                ))
                continue
            health = parse_smartctl_output(device, proc.stdout)
            results.append(health)
        except subprocess.TimeoutExpired:
            results.append(SmartDiskHealth(
                device=device, reallocated_sectors=0,
                temperature_c=None, overall_assessment=None,
                parse_error="smartctl timeout (10s)",
            ))
        except Exception as e:
            results.append(SmartDiskHealth(
                device=device, reallocated_sectors=0,
                temperature_c=None, overall_assessment=None,
                parse_error=f"{type(e).__name__}: {e}",
            ))
    return results


def evaluate(values: dict, rules: dict) -> str:
    """Return overall status: 'ok' | 'warn' | 'crit'.

    crit if any reallocated > 100 OR overall_assessment = FAILED
    warn if any reallocated > 0 OR temperature > 55°C
    """
    disks = values.get("disks", [])
    if not disks:
        return "ok"
    crit_threshold = rules.get("smart", {}).get("reallocated_crit", 100)
    warn_threshold = rules.get("smart", {}).get("reallocated_warn", 1)
    temp_warn = rules.get("smart", {}).get("temp_warn_c", 55)

    has_crit = any(
        d.get("reallocated_sectors", 0) > crit_threshold
        or d.get("overall_assessment") == "FAILED"
        for d in disks
    )
    has_warn = any(
        d.get("reallocated_sectors", 0) >= warn_threshold
        or (d.get("temperature_c") is not None and d["temperature_c"] > temp_warn)
        for d in disks
    )
    if has_crit:
        return "crit"
    if has_warn:
        return "warn"
    return "ok"
