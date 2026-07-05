"""Sprint 16.1 — backup freshness probe.

Detects when a backup file (or directory) is older than its maximum
allowed age. Uses mtime (not atime) to avoid false positives from
file reads.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


@dataclass
class FreshnessResult:
    status: str               # "ok" | "warn" | "crit" | "unknown"
    age_seconds: Optional[float]
    max_age_seconds: float
    path: str
    reason: str = ""


def _age_seconds(path: Path) -> Optional[float]:
    """Return seconds since last mtime. None if path doesn't exist."""
    if not path.exists():
        return None
    return max(0.0, time.time() - path.stat().st_mtime)


def check_backup_freshness(
    path: Union[str, Path],
    max_age_seconds: float = 24 * 3600,
    now: Optional[float] = None,
) -> FreshnessResult:
    """Check whether a backup file/dir is fresh.

    - age ≤ max_age → ok
    - max_age < age ≤ 2*max_age → warn
    - age > 2*max_age → crit
    - missing → unknown
    """
    p = Path(path)
    age = _age_seconds(p)
    if age is None:
        return FreshnessResult(
            status="unknown",
            age_seconds=None,
            max_age_seconds=max_age_seconds,
            path=str(p),
            reason="path_missing",
        )
    # Use injected "now" for testability
    if now is not None:
        age = max(0.0, now - p.stat().st_mtime)
    if age <= max_age_seconds:
        status = "ok"
    elif age <= 2 * max_age_seconds:
        status = "warn"
    else:
        status = "crit"
    return FreshnessResult(
        status=status,
        age_seconds=age,
        max_age_seconds=max_age_seconds,
        path=str(p),
    )


def check_multiple_backups(
    paths: list[Union[str, Path]],
    max_age_seconds: float = 24 * 3600,
) -> list[FreshnessResult]:
    """Check several backups. Returns one result per path."""
    return [check_backup_freshness(p, max_age_seconds) for p in paths]
