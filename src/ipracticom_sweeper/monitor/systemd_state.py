"""Sprint 12.2 — systemd unit state detector.

Wraps `systemctl show` to get LoadState + ActiveState + UnitFileState
and decides:
- active → ok
- inactive → warn
- failed → crit
- masked → crit
- Unit not found → unknown
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class SystemdStateResult:
    status: str            # ok | warn | crit | unknown | disabled
    active_state: str
    load_state: str
    unit_file_state: str
    unit: str
    error: str = ""


def _run_systemctl(unit: str) -> tuple[int, str]:
    """Run `systemctl show <unit>`. Returns (rc, stdout)."""
    try:
        r = subprocess.run(
            ["systemctl", "show", unit, "--no-pager"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode, r.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return -1, ""


def _parse_show(stdout: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def check_unit_state(
    unit: str,
    systemctl_runner=None,
) -> SystemdStateResult:
    """Determine the state of a systemd unit."""
    if not unit:
        return SystemdStateResult(
            status="disabled",
            active_state="",
            load_state="",
            unit_file_state="",
            unit="",
            error="no_unit",
        )
    if systemctl_runner is None:
        systemctl_runner = lambda u: _run_systemctl(u)  # noqa: E731
    rc, stdout = systemctl_runner(unit)
    if rc == -1:
        return SystemdStateResult(
            status="unknown",
            active_state="",
            load_state="",
            unit_file_state="",
            unit=unit,
            error="systemctl_unavailable",
        )
    parsed = _parse_show(stdout)
    active = parsed.get("ActiveState", "")
    load = parsed.get("LoadState", "")
    file_state = parsed.get("UnitFileState", "")

    if "not-found" in load:
        return SystemdStateResult(
            status="unknown",
            active_state=active,
            load_state=load,
            unit_file_state=file_state,
            unit=unit,
            error="not_found",
        )

    if active == "active":
        status = "ok"
    elif active == "failed":
        status = "crit"
    elif file_state == "masked" or load == "masked":
        status = "crit"
    elif active in ("inactive", "deactivating"):
        # Disabled at boot is warn
        if file_state == "disabled":
            status = "warn"
        else:
            status = "warn"
    else:
        status = "warn"

    return SystemdStateResult(
        status=status,
        active_state=active,
        load_state=load,
        unit_file_state=file_state,
        unit=unit,
    )
