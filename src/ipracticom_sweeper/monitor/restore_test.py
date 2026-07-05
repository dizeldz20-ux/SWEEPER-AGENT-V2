"""Sprint 16.3 — restore-test cron result parser.

Reads a status file written by the periodic restore-test cron job
(/var/lib/ipracticom-sweeper/restore_status.json). The file is updated
after each restore attempt.

If the last run was over N days ago, → warn. If the last run failed,
→ crit. If the file is missing, → unknown (never run).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class RestoreTestResult:
    status: str           # "ok" | "warn" | "crit" | "unknown"
    last_status: Optional[str]      # "passed" | "failed" | None
    last_run_at: Optional[float]
    age_seconds: Optional[float]
    duration_seconds: Optional[float]
    reason: str = ""


def check_restore_test(
    status_file: Path = Path("/var/lib/ipracticom-sweeper/restore_status.json"),
    max_age_seconds: float = 7 * 24 * 3600,
    now: Optional[float] = None,
) -> RestoreTestResult:
    """Parse the restore status file and assess its freshness + outcome."""
    if not status_file.exists():
        return RestoreTestResult(
            status="unknown",
            last_status=None,
            last_run_at=None,
            age_seconds=None,
            duration_seconds=None,
            reason="status_file_missing",
        )
    try:
        data = json.loads(status_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return RestoreTestResult(
            status="unknown",
            last_status=None,
            last_run_at=None,
            age_seconds=None,
            duration_seconds=None,
            reason=f"parse_failed: {e}",
        )

    last_status = data.get("status")  # "passed" or "failed"
    last_run_at = data.get("run_at")
    duration = data.get("duration_seconds")

    if last_run_at is None:
        return RestoreTestResult(
            status="unknown",
            last_status=last_status,
            last_run_at=None,
            age_seconds=None,
            duration_seconds=duration,
            reason="no_run_at",
        )

    current = now if now is not None else time.time()
    age = max(0.0, current - float(last_run_at))

    if last_status == "failed":
        status = "crit"
    elif age > max_age_seconds:
        status = "warn"
    elif last_status == "passed" and age <= max_age_seconds:
        status = "ok"
    else:
        status = "warn"

    return RestoreTestResult(
        status=status,
        last_status=last_status,
        last_run_at=float(last_run_at),
        age_seconds=age,
        duration_seconds=duration,
    )
