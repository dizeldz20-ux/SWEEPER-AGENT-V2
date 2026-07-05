"""Systemd service health.

Uses `systemctl` to check service state. Read-only — does NOT restart.
"""

from __future__ import annotations

import subprocess
from typing import Any


def _run_systemctl(args: list[str]) -> tuple[int, str, str]:
    """Run systemctl, return (rc, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["systemctl"] + args,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return 127, "", "systemctl not found"
    except Exception as e:
        return 1, "", str(e)


def list_failed_units() -> list[dict[str, Any]]:
    """Return all units currently in 'failed' state."""
    # --plain strips the leading status bullet ("●") that systemctl prints
    # even with --no-legend; without it the columns shift by one and no
    # failed unit is ever detected (silent-OK). Verified on systemd 255.
    rc, out, _ = _run_systemctl(["--failed", "--no-pager", "--no-legend", "--plain"])
    if rc != 0:
        return []

    failed = []
    for line in out.strip().split("\n"):
        if not line.strip():
            continue
        # Format: UNIT LOAD ACTIVE SUB DESCRIPTION
        parts = line.split(None, 4)
        if len(parts) >= 4 and parts[2] == "failed":
            failed.append({
                "unit": parts[0],
                "active": parts[2],
                "sub": parts[3],
                "description": parts[4] if len(parts) > 4 else "",
            })
    return failed


def check_service_active(name: str) -> bool:
    """Check if a service is currently active."""
    rc, _, _ = _run_systemctl(["is-active", "--quiet", f"{name}.service"])
    return rc == 0


def check_services(services: list[str]) -> dict[str, bool]:
    """Check a list of service names; return {name: is_active}."""
    return {name: check_service_active(name) for name in services}


def collect(critical_services: list[str] = None) -> dict[str, Any]:
    """Collect service health snapshot."""
    critical_services = critical_services or []
    failed = list_failed_units()
    critical_status = check_services(critical_services)

    return {
        "failed_units": failed,
        "failed_count": len(failed),
        "critical_services_checked": list(critical_status.keys()),
        "critical_services_down": [
            name for name, up in critical_status.items() if not up
        ],
    }


def evaluate(values: dict[str, Any], rules: dict) -> str:
    """Any critical service down = crit. Any failed = warn."""
    if values["critical_services_down"]:
        return "crit"
    if values["failed_count"] > 0:
        return "warn"
    return "ok"