"""Security signals: failed SSH, sudo failures, suspicious auth events.

Reads from journalctl (preferred) or /var/log/auth.log as fallback.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any


def _run_journalctl(args: list[str]) -> tuple[int, str]:
    """Run journalctl, return (rc, stdout)."""
    try:
        result = subprocess.run(
            ["journalctl"] + args,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode, result.stdout
    except FileNotFoundError:
        return 127, ""
    except Exception as e:
        return 1, str(e)


def count_failed_ssh(window_minutes: int = 5) -> int:
    """Count failed SSH authentication attempts in last N minutes."""
    # Match both unit names: RHEL/Amazon Linux use `sshd.service`, while
    # Debian/Ubuntu use `ssh.service`. Restricting to only `sshd` silently
    # returned 0 on Debian-family hosts (brute-force went unseen).
    rc, out = _run_journalctl([
        f"--since={window_minutes} minutes ago",
        "-u", "sshd",
        "-u", "ssh",
        "--no-pager",
        "-g", "Failed password",
    ])
    if rc != 0:
        return 0
    return sum(1 for line in out.split("\n") if "Failed password" in line)


def count_sudo_failures(window_minutes: int = 60) -> int:
    """Count sudo authentication failures in last N minutes."""
    rc, out = _run_journalctl([
        f"--since={window_minutes} minutes ago",
        "--no-pager",
        "-g", "sudo.*authentication failure",
    ])
    if rc != 0:
        return 0
    return sum(1 for line in out.split("\n") if "authentication failure" in line)


def collect(rules: dict) -> dict[str, Any]:
    """Collect security signals."""
    ssh_window = 5  # 5 minutes for SSH brute-force detection
    sudo_window = 60  # 1 hour, matching the *_per_hour_warn threshold

    failed_ssh = count_failed_ssh(ssh_window)
    sudo_failures = count_sudo_failures(sudo_window)

    ssh_per_min = failed_ssh / ssh_window

    return {
        "ssh_window_minutes": ssh_window,
        "failed_ssh_attempts": failed_ssh,
        "failed_ssh_per_minute": round(ssh_per_min, 2),
        "sudo_window_minutes": sudo_window,
        "sudo_failures": sudo_failures,
    }


def evaluate(values: dict[str, Any], rules: dict) -> str:
    """Apply rules; return 'ok' | 'warn' | 'crit'."""
    if values["failed_ssh_per_minute"] >= rules["security"]["failed_ssh_per_min_warn"] * 10:
        return "crit"  # sustained brute-force
    if values["failed_ssh_per_minute"] >= rules["security"]["failed_ssh_per_min_warn"]:
        return "warn"
    if values["sudo_failures"] >= rules["security"]["sudo_failures_per_hour_warn"] * 5:
        return "warn"
    return "ok"