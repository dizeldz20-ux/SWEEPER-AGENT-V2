"""Sprint 13.1 — /etc/cron.* watcher.

Detects new files in /etc/cron.d/, modifications to /var/spool/cron/root,
or any change to /etc/cron.* against a saved baseline. Returns a
CronWatchResult with diff details.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


BASELINE_FILE = "cron_baseline.json"
CRON_DIRS = ("/etc/cron.d", "/etc/cron.daily", "/etc/cron.hourly",
             "/etc/cron.weekly", "/etc/cron.monthly")
ROOT_CRONTAB = "/var/spool/cron/root"


@dataclass
class CronWatchResult:
    status: str               # "ok" | "warn" | "crit" | "disabled"
    new_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    root_crontab_changed: bool = False
    reason: str = ""


def _fingerprint(path: Path) -> Optional[str]:
    """SHA-256 of a file's content. None if unreadable."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _collect_fingerprints(cron_dirs: tuple[str, ...] = CRON_DIRS,
                          root_crontab: str = ROOT_CRONTAB) -> dict[str, Optional[str]]:
    """Snapshot {path: sha256} of every file under cron dirs + root crontab."""
    out: dict[str, Optional[str]] = {}
    for d in cron_dirs:
        p = Path(d)
        if not p.is_dir():
            continue
        for child in p.iterdir():
            if child.is_file():
                out[str(child)] = _fingerprint(child)
    rc = Path(root_crontab)
    if rc.exists():
        out[str(rc)] = _fingerprint(rc)
    return out


def _load_baseline(state_dir: Path) -> Optional[dict[str, Optional[str]]]:
    path = state_dir / "cache" / BASELINE_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_baseline(state_dir: Path, snapshot: dict[str, Optional[str]]) -> None:
    path = state_dir / "cache" / BASELINE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2))


def check_cron_changes(state_dir: Path,
                      cron_dirs: tuple[str, ...] = CRON_DIRS,
                      root_crontab: str = ROOT_CRONTAB) -> CronWatchResult:
    """Diff current cron state against the saved baseline.

    First run (no baseline): saves and returns ok.
    """
    # If no cron dir exists, disable
    if not any(Path(d).is_dir() for d in cron_dirs):
        return CronWatchResult(
            status="disabled",
            reason="no cron dirs present",
        )

    current = _collect_fingerprints(cron_dirs, root_crontab)
    baseline = _load_baseline(state_dir)

    if baseline is None:
        # First run: save and return ok
        _save_baseline(state_dir, current)
        return CronWatchResult(
            status="ok",
            reason="baseline created",
        )

    # Diff
    new_files: list[str] = []
    modified_files: list[str] = []
    deleted_files: list[str] = []

    for path, fp in current.items():
        if path not in baseline:
            new_files.append(path)
        elif baseline[path] != fp:
            modified_files.append(path)
    for path in baseline:
        if path not in current:
            deleted_files.append(path)

    # Root crontab is crit; new/modified cron.d is warn; deleted is warn
    root_changed = bool(
        root_crontab in new_files
        or root_crontab in modified_files
    )

    if root_changed:
        status = "crit"
    elif new_files or modified_files or deleted_files:
        status = "warn"
    else:
        status = "ok"

    # Persist updated baseline
    _save_baseline(state_dir, current)

    return CronWatchResult(
        status=status,
        new_files=new_files,
        modified_files=modified_files,
        deleted_files=deleted_files,
        root_crontab_changed=root_changed,
    )
