"""iostat I/O latency monitor.

Shells out to `iostat -dx 1 2` to get per-device I/O stats. Parses
the second sample (first is garbage from boot).

Requires the `sysstat` package (apt install sysstat).
If iostat is missing, returns empty list (graceful degradation).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import shutil
import subprocess

from .._log import log_suppressed


@dataclass
class IostatDevice:
    """Per-device I/O stats."""

    device: str
    rps: float       # reads per second
    wps: float       # writes per second
    r_await_ms: float  # read latency
    w_await_ms: float  # write latency
    util_percent: float  # %util (1.00 = fully busy)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "rps": self.rps,
            "wps": self.wps,
            "r_await_ms": self.r_await_ms,
            "w_await_ms": self.w_await_ms,
            "util_percent": self.util_percent,
        }


def parse_iostat_output(output: str) -> list[IostatDevice]:
    """Parse `iostat -dx 1 2` output, return per-device I/O stats.

    Skips the first sample (boot/load garbage) and uses the second.
    Filters out zero-activity devices (loop, idle, etc).
    """
    devices: list[IostatDevice] = []

    # Find the LAST device stats header line and parse from there.
    # iostat -dx output has format:
    #   ...header lines...
    #   Device  r/s  w/s  rkB/s  wkB/s  ...  r_await  w_await  ...  %util
    #   loop0   0.00  0.00  ...    (first sample)
    #   sda     0.50  2.00  ...    (first sample)
    #   ---     (blank between samples)
    #   loop0   0.00  0.00  ...    (second sample)
    #   sda     0.50  2.00  ...    (second sample)

    # We want the LAST occurrence of the header line, then everything after.
    lines = output.splitlines()
    header_idx = -1
    for i, line in enumerate(lines):
        if line.strip().startswith("Device ") and "r/s" in line and "w/s" in line:
            header_idx = i
    if header_idx < 0:
        return []

    # Find the position of each column from the header
    header = lines[header_idx]
    def col(name: str) -> int:
        return header.split().index(name)

    idx_device = col("Device")
    idx_rps = col("r/s")
    idx_wps = col("w/s")
    idx_r_await = col("r_await")
    idx_w_await = col("w_await")
    idx_util = col("%util")

    # Parse from header_idx+1 to end, but keep only the LAST "block" (second sample)
    # Strategy: parse all data lines after header, then keep only the last 2*device_count
    data_lines: list[str] = []
    for line in lines[header_idx + 1:]:
        if not line.strip():
            continue
        parts = line.split()
        # Skip non-data lines (Linux summary headers etc.)
        if len(parts) < max(idx_device, idx_util) + 1:
            continue
        # Skip Linux summary headers like "Linux 5.x.x ..."
        if parts[idx_device].startswith("Linux") or "cpu" in line.lower()[:10]:
            continue
        data_lines.append(line)

    # header_idx is the LAST "Device" header, i.e. the start of the SECOND
    # `iostat -dx 1 2` sample, so data_lines already holds ONLY that sample.
    # The old code additionally sliced off the first half of these lines
    # ("skip the first half"), which silently dropped half the real devices
    # of the second sample — a busy sda in the dropped half was never checked
    # (silent-OK). Parse every device line in the second sample; loop/idle
    # devices are removed by the zero-activity filter below.
    if not data_lines:
        return []
    for line in data_lines:
        parts = line.split()
        try:
            device = parts[idx_device]
            rps = float(parts[idx_rps])
            wps = float(parts[idx_wps])
            r_await = float(parts[idx_r_await])
            w_await = float(parts[idx_w_await])
            util = float(parts[idx_util])
        except (ValueError, IndexError) as e:
            log_suppressed("iostat_parse", e)
            continue

        # Filter out zero-activity devices
        if rps == 0 and wps == 0:
            continue

        devices.append(IostatDevice(
            device=device,
            rps=rps, wps=wps,
            r_await_ms=r_await, w_await_ms=w_await,
            util_percent=util,
        ))

    return devices


def collect_iostat() -> list[IostatDevice]:
    """Run iostat and parse output. Returns empty list if binary missing."""
    iostat = shutil.which("iostat")
    if not iostat:
        return []

    try:
        proc = subprocess.run(
            [iostat, "-dx", "1", "2"],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return []
        return parse_iostat_output(proc.stdout)
    except (subprocess.TimeoutExpired, OSError):
        return []


def evaluate(values: dict, rules: dict) -> str:
    """Return 'ok' | 'warn' | 'crit'.

    crit if await > 200ms OR util > 95% on any device,
    warn if await > 50ms OR util > 80% on any device.
    """
    devices = values.get("devices", [])
    if not devices:
        return "ok"
    await_crit = rules.get("iostat", {}).get("await_crit_ms", 200)
    await_warn = rules.get("iostat", {}).get("await_warn_ms", 50)
    util_warn = rules.get("iostat", {}).get("util_warn_percent", 80)
    util_crit = rules.get("iostat", {}).get("util_crit_percent", 95)

    has_crit = False
    has_warn = False
    for d in devices:
        max_await = max(d.get("r_await_ms", 0) or 0, d.get("w_await_ms", 0) or 0)
        if max_await > await_crit or (d.get("util_percent") or 0) > util_crit:
            has_crit = True
        elif max_await > await_warn or (d.get("util_percent") or 0) > util_warn:
            has_warn = True

    if has_crit:
        return "crit"
    if has_warn:
        return "warn"
    return "ok"
