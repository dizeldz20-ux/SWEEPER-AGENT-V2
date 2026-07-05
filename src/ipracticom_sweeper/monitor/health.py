"""Agent self-health: heartbeat tracker.

The agent writes a heartbeat after every pipeline run. If the heartbeat
is stale (older than expected), something is wrong:
  - The systemd timer stopped firing
  - The pipeline is hanging
  - The host is in such bad shape we can't even write a heartbeat

A separate `check_health()` reads the heartbeat and reports:
  - fresh: ran recently (within expected interval × 2)
  - stale: missed a run
  - missing: never ran
  - corrupt: file unreadable
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def collect_local_metrics() -> dict[str, Any]:
    """Snapshot the local host's resource usage via psutil.

    Returns a dict suitable for storing under heartbeat["extra"]. If psutil
    raises (e.g. permission error in a sandbox), returns a dict with an
    "error" key instead of crashing the pipeline.
    """
    try:
        import psutil
    except ImportError:
        return {"error": "psutil not installed"}

    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        cpu_count = psutil.cpu_count(logical=True) or 1
        vm = psutil.virtual_memory()
        du = psutil.disk_usage("/")
        net = psutil.net_io_counters()
        boot_ts = psutil.boot_time()
        now_ts = time.time()
        uptime_seconds = max(0.0, now_ts - boot_ts)

        from datetime import datetime, timezone
        booted_iso = datetime.fromtimestamp(boot_ts, tz=timezone.utc).isoformat()

        return {
            "cpu": {
                "percent": float(cpu_percent),
                "cores": int(cpu_count),
            },
            "memory": {
                "percent": float(vm.percent),
                "used_mb": round(vm.used / (1024 * 1024), 1),
                "total_mb": round(vm.total / (1024 * 1024), 1),
            },
            "disk": {
                "percent": float(du.percent),
                "used_gb": round(du.used / (1024 ** 3), 1),
                "total_gb": round(du.total / (1024 ** 3), 1),
            },
            "network": {
                "bytes_sent": int(net.bytes_sent),
                "bytes_recv": int(net.bytes_recv),
            },
            "uptime_seconds": float(uptime_seconds),
            "booted_at": booted_iso,
        }
    except Exception as e:
        return {"error": f"psutil collection failed: {e}"}


# Standard system path; we fall back to user-local if we can't write there.
SYSTEM_HEARTBEAT_DIR = Path("/var/lib/ipracticom-sweeper")
SYSTEM_HEARTBEAT_FILE = SYSTEM_HEARTBEAT_DIR / "heartbeat.json"


@dataclass
class HealthStatus:
    state: str  # "fresh" | "stale" | "missing" | "corrupt"
    last_run_ts: float | None
    last_run_iso: str | None
    last_defcon: int | None
    age_seconds: float | None
    expected_max_age: float
    reason: str | None = None

    @property
    def is_healthy(self) -> bool:
        return self.state == "fresh"

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "last_run_ts": self.last_run_ts,
            "last_run_iso": self.last_run_iso,
            "last_defcon": self.last_defcon,
            "age_seconds": self.age_seconds,
            "expected_max_age": self.expected_max_age,
            "reason": self.reason,
            "is_healthy": self.is_healthy,
        }


def _heartbeat_path() -> Path:
    """Pick the best writable path for the heartbeat file."""
    try:
        SYSTEM_HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
        # Touch a probe file to check writability
        probe = SYSTEM_HEARTBEAT_DIR / ".probe"
        probe.write_text("ok")
        probe.unlink()
        return SYSTEM_HEARTBEAT_FILE
    except (OSError, PermissionError):
        fallback = Path.home() / ".ipracticom-sweeper" / "heartbeat.json"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        return fallback


def record_run(
    defcon: int | None = None,
    problems_found: int | None = None,
    repairs_attempted: int | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Write a heartbeat after a successful pipeline run.

    Returns the path the heartbeat was written to.
    """
    path = _heartbeat_path()
    # If the caller didn't pass extra, auto-collect local psutil metrics.
    if extra is None:
        extra = collect_local_metrics()
    record = {
        "ts": time.time(),
        "ts_iso": _iso_now(),
        "defcon": defcon,
        "problems_found": problems_found,
        "repairs_attempted": repairs_attempted,
        "extra": extra,
    }
    # v1.5.8 fix: atomic write (tmp + os.replace). Previously write_text()
    # was non-atomic — a SIGKILL mid-write left a truncated JSON that
    # check_health would misread as "corrupt" and falsely flag the agent
    # as broken.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2))
    os.replace(tmp, path)
    return path


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _read_heartbeat(path: Path) -> dict[str, Any] | None | str:
    """Returns parsed dict, or None if missing, or 'corrupt' if unreadable."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return "corrupt"


def check_health(
    expected_interval_seconds: float = 300.0,
    path: Path | None = None,
    now: float | None = None,
) -> HealthStatus:
    """Inspect the heartbeat and report freshness.

    expected_interval_seconds: how often the agent should run (default 5 min).
                                The threshold for "stale" is 2x this.
    """
    path = path or _heartbeat_path()
    expected_max_age = expected_interval_seconds * 2.0
    now = now if now is not None else time.time()

    record = _read_heartbeat(path)
    if record == "corrupt":
        return HealthStatus(
            state="corrupt",
            last_run_ts=None,
            last_run_iso=None,
            last_defcon=None,
            age_seconds=None,
            expected_max_age=expected_max_age,
            reason="heartbeat file is unreadable (corrupt JSON or IO error)",
        )
    if record is None:
        return HealthStatus(
            state="missing",
            last_run_ts=None,
            last_run_iso=None,
            last_defcon=None,
            age_seconds=None,
            expected_max_age=expected_max_age,
            reason="no heartbeat file found — agent has never run, or path is unwritable",
        )

    last_ts = record.get("ts")
    if not isinstance(last_ts, (int, float)):
        return HealthStatus(
            state="corrupt",
            last_run_ts=None,
            last_run_iso=None,
            last_defcon=None,
            age_seconds=None,
            expected_max_age=expected_max_age,
            reason=f"heartbeat 'ts' is not numeric: {last_ts!r}",
        )

    age = now - last_ts
    last_defcon = record.get("defcon")
    if age < 0:
        # Clock went backwards — treat as fresh (don't false-alarm)
        return HealthStatus(
            state="fresh",
            last_run_ts=last_ts,
            last_run_iso=record.get("ts_iso"),
            last_defcon=last_defcon if isinstance(last_defcon, int) else None,
            age_seconds=age,
            expected_max_age=expected_max_age,
            reason="clock skew detected; treating as fresh",
        )

    if age > expected_max_age:
        return HealthStatus(
            state="stale",
            last_run_ts=last_ts,
            last_run_iso=record.get("ts_iso"),
            last_defcon=last_defcon if isinstance(last_defcon, int) else None,
            age_seconds=age,
            expected_max_age=expected_max_age,
            reason=f"last run was {age:.0f}s ago, threshold is {expected_max_age:.0f}s",
        )

    return HealthStatus(
        state="fresh",
        last_run_ts=last_ts,
        last_run_iso=record.get("ts_iso"),
        last_defcon=last_defcon if isinstance(last_defcon, int) else None,
        age_seconds=age,
        expected_max_age=expected_max_age,
    )


def evaluate(values: dict[str, Any], rules: dict) -> str:
    """Apply rules to a collected heartbeat snapshot.

    values keys:
        - state: "fresh" | "stale" | "missing" | "corrupt"
        - age_seconds: float | None

    rules shape (all optional):
        health:
          stale_warn_seconds: 600     # default 2x default interval
          stale_crit_seconds: 1800    # default 6x default interval
    """
    state = values.get("state", "missing")
    if state == "fresh":
        return "ok"
    health_rules = rules.get("health", {}) if isinstance(rules, dict) else {}
    crit_threshold = health_rules.get("stale_crit_seconds", 1800)
    warn_threshold = health_rules.get("stale_warn_seconds", 600)

    age = values.get("age_seconds")
    if age is None:
        # missing or corrupt — treat as warn (could be disk issue, not agent death)
        return "warn"

    if age > crit_threshold:
        return "crit"
    if age > warn_threshold:
        return "warn"
    return "ok"


def collect(path: Path | None = None) -> dict[str, Any]:
    """Collect a self-health snapshot suitable for the monitor pipeline."""
    status = check_health(path=path)
    return {
        "state": status.state,
        "last_run_ts": status.last_run_ts,
        "last_run_iso": status.last_run_iso,
        "last_defcon": status.last_defcon,
        "age_seconds": status.age_seconds,
        "is_healthy": status.is_healthy,
        "collected_at": time.time(),
    }
