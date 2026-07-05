"""Sprint 16.2 — backup size sanity check.

Detects when a current backup's size has dropped significantly
compared to a saved baseline. The first run saves the baseline
and returns ok.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


BASELINE_FILE = "backup_size_baseline.json"
WARN_PCT_DROP = 20.0   # 20% drop = warn
CRIT_PCT_DROP = 50.0   # 50% drop = crit


@dataclass
class SizeResult:
    status: str            # "ok" | "warn" | "crit" | "disabled"
    current_bytes: Optional[int]
    baseline_bytes: Optional[int]
    pct_change: Optional[float]
    path: str
    reason: str = ""


def _get_size(path: Path) -> Optional[int]:
    """Total bytes of a file or directory tree."""
    if not path.exists():
        return None
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        total = 0
        for child in path.rglob("*"):
            if child.is_file():
                total += child.stat().st_size
        return total
    return None


def _load_baseline(state_dir: Path) -> dict[str, int]:
    path = state_dir / "cache" / BASELINE_FILE
    if not path.exists():
        return {}
    try:
        return {k: int(v) for k, v in json.loads(path.read_text()).items()}
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return {}


def _save_baseline(state_dir: Path, baseline: dict[str, int]) -> None:
    path = state_dir / "cache" / BASELINE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline, indent=2))


def check_backup_size(
    path: Union[str, Path],
    state_dir: Path,
) -> SizeResult:
    """Check whether a backup's size has dropped from baseline.

    First run (no baseline): saves the current size as baseline and returns ok.
    """
    p = Path(path)
    current = _get_size(p)
    if current is None:
        return SizeResult(
            status="unknown",
            current_bytes=None,
            baseline_bytes=None,
            pct_change=None,
            path=str(p),
            reason="path_missing",
        )

    baseline = _load_baseline(state_dir)
    if str(p) not in baseline:
        baseline[str(p)] = current
        _save_baseline(state_dir, baseline)
        return SizeResult(
            status="ok",
            current_bytes=current,
            baseline_bytes=current,
            pct_change=0.0,
            path=str(p),
            reason="baseline_created",
        )

    base = baseline[str(p)]
    if base == 0:
        pct = 0.0
    else:
        pct = ((current - base) / base) * 100.0

    if pct <= -CRIT_PCT_DROP:
        status = "crit"
    elif pct <= -WARN_PCT_DROP:
        status = "warn"
    else:
        status = "ok"

    # Update baseline to current (track growth too)
    baseline[str(p)] = current
    _save_baseline(state_dir, baseline)

    return SizeResult(
        status=status,
        current_bytes=current,
        baseline_bytes=base,
        pct_change=pct,
        path=str(p),
    )
