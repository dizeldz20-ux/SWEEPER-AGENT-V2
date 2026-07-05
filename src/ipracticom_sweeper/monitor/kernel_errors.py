"""Kernel error detector: Oops, MCE, segfaults.

Reads recent dmesg/journalctl output and flags hardware-level kernel
errors and segfaults. Used to catch silent hardware failures and
application crashes.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import re
import shutil
import subprocess

from .._log import log_suppressed


@dataclass
class KernelError:
    """A single kernel-level error event."""

    kind: str  # "kernel_oops" | "machine_check_exception" | "segfault"
    severity: str  # "crit" | "warn"
    message: str
    count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "count": self.count,
        }


# Patterns - applied case-insensitive
PATTERNS = [
    (re.compile(r"\bOops:\s*\d+", re.IGNORECASE), "kernel_oops", "crit"),
    (re.compile(r"BUG:\s*unable to handle", re.IGNORECASE), "kernel_bug", "crit"),
    (re.compile(r"Machine Check Exception", re.IGNORECASE), "machine_check_exception", "crit"),
    (re.compile(r"segfault at", re.IGNORECASE), "segfault", "warn"),
    (re.compile(r"kernel panic", re.IGNORECASE), "kernel_panic", "crit"),
    (re.compile(r"\bMCE\b", re.IGNORECASE), "machine_check_exception", "crit"),
]


def parse_dmesg_output(output: str, window_minutes: int = 60) -> list[KernelError]:
    """Parse dmesg/journalctl output into kernel error events.

    Lines matching known kernel-error patterns are collected. Multiple
    matches of the same kind are aggregated into a single entry with count.
    """
    by_kind: dict[str, KernelError] = {}

    for line in output.splitlines():
        for pattern, kind, severity in PATTERNS:
            if pattern.search(line):
                if kind not in by_kind:
                    by_kind[kind] = KernelError(
                        kind=kind,
                        severity=severity,
                        message=line.strip()[:300],
                        count=1,
                    )
                else:
                    by_kind[kind].count += 1

    return list(by_kind.values())


def collect_kernel_errors(window_minutes: int = 5) -> dict[str, Any]:
    """Collect kernel errors from the last `window_minutes`.

    Tries dmesg first (requires CAP_SYSLOG), falls back to journalctl.
    Returns a dict suitable for snapshot values: {errors: [...], available: bool}.
    """
    output = ""

    # Try dmesg
    dmesg = shutil.which("dmesg")
    if dmesg:
        try:
            proc = subprocess.run(
                [dmesg, "--since", f"{window_minutes} min ago", "--level", "err,crit,alert,emerg"],
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0:
                output = proc.stdout
        except (subprocess.TimeoutExpired, OSError) as e:
            log_suppressed("kernel_errors_dmesg", e)

    # Fallback to journalctl
    if not output:
        journalctl = shutil.which("journalctl")
        if journalctl:
            try:
                proc = subprocess.run(
                    [journalctl, "--since", f"{window_minutes} min ago", "-p", "err..emerg", "--no-pager", "-q"],
                    capture_output=True, text=True, timeout=5,
                )
                if proc.returncode == 0:
                    output = proc.stdout
            except (subprocess.TimeoutExpired, OSError) as e:
                log_suppressed("kernel_errors_journalctl", e)

    errors = parse_dmesg_output(output, window_minutes=window_minutes)
    return {
        "errors": [e.to_dict() for e in errors],
        "available": bool(dmesg or shutil.which("journalctl")),
        "window_minutes": window_minutes,
    }


def evaluate(values: dict, rules: dict) -> str:
    """Return overall status: 'ok' | 'warn' | 'crit'.

    crit if any kernel_oops / kernel_panic / machine_check_exception,
    warn if any segfault.
    """
    errors = values.get("errors", [])
    if not errors:
        return "ok"
    has_crit = any(e.get("severity") == "crit" for e in errors)
    has_warn = any(e.get("severity") == "warn" for e in errors)
    if has_crit:
        return "crit"
    if has_warn:
        return "warn"
    return "ok"
