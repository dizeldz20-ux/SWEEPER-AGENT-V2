"""Slice 8.5: combine all self-resilience signals into one snapshot section.

Exposed via /api/snapshot and /healthz so the operator can see at a
glance whether the sweeper itself is healthy.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from .._log import log_suppressed


def _state_dir_pct(state_dir: Path) -> Optional[float]:
    """Disk usage % of the state dir's mount."""
    from ipracticom_sweeper.monitor.self_disk import _disk_usage_pct
    try:
        return _disk_usage_pct(str(state_dir))
    except Exception as e:
        log_suppressed("self_snapshot_state_dir_pct", e)
        return None


def _audit_size_bytes(state_dir: Path) -> int:
    """Size of the current audit log file."""
    log = state_dir / "audit" / "audit.jsonl"
    try:
        return log.stat().st_size if log.exists() else 0
    except OSError as e:
        log_suppressed("self_snapshot_audit_size", e)
        return 0


def _bot_token_status(state_dir: Path) -> str:
    """Read the last persisted bot token status."""
    from ipracticom_sweeper.telegram_bot.health import TokenHealthTracker
    try:
        tracker = TokenHealthTracker(state_dir=state_dir)
        return tracker.last_status
    except Exception as e:
        log_suppressed("self_snapshot_bot_token", e)
        return "unknown"


def _watchdog_restart_count(state_dir: Path) -> int:
    """Read watchdog restart count from disk."""
    path = state_dir / "watchdog_restarts.json"
    if not path.exists():
        return 0
    try:
        import json
        data = json.loads(path.read_text())
        from datetime import datetime, timezone
        now = time.time()
        count = 0
        for ts in data.get("restarts", []):
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                if now - t < 3600:
                    count += 1
            except Exception as e:
                log_suppressed("self_snapshot_recent_count", e)
        return count
    except Exception as e:
        log_suppressed("self_snapshot_watchdog_count", e)
        return 0


def _uptime_seconds() -> float:
    """Best-effort uptime estimate from /proc/uptime."""
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError):
        return 0.0


def build_self_section(state_dir: Optional[Path] = None) -> dict:
    """Aggregate all self-resilience signals into one dict.

    Never raises — degrades gracefully if any check fails.
    """
    if state_dir is None:
        from ipracticom_sweeper.monitor.self_disk import find_state_dir
        state_dir = find_state_dir()

    section: dict = {"degraded": False}

    # State-dir disk %
    try:
        section["state_dir_pct"] = _state_dir_pct(state_dir)
    except Exception:
        section["state_dir_pct"] = None
        section["degraded"] = True

    # Audit log size
    try:
        section["audit_size_bytes"] = _audit_size_bytes(state_dir)
    except Exception:
        section["audit_size_bytes"] = None
        section["degraded"] = True

    # Bot connectivity — every configured Telegram + Slack bot. Read from the
    # cache written by the sweep's self phase (bot_connectivity.check_and_persist)
    # so this read-path never fires live network probes. Falls back to the legacy
    # single-token tracker when no cached probe exists yet (e.g. first run).
    try:
        from ipracticom_sweeper.monitor import bot_connectivity
        cached = bot_connectivity.load_result(state_dir)
        if cached is None:
            section["bots"] = None
            section["bot_token_status"] = _bot_token_status(state_dir)
        else:
            section["bots"] = {
                "status": cached.get("status", "unknown"),
                "defcon": cached.get("defcon", 5),
                "summary": cached.get("summary", ""),
                "counts": cached.get("counts", {}),
                "configured": cached.get("configured", 0),
                "checked_at": cached.get("checked_at"),
                "items": cached.get("bots", []),
            }
            # Keep the legacy scalar field for back-compat: worst-of-all status.
            section["bot_token_status"] = cached.get("status", "unknown")
    except Exception:
        section["bots"] = None
        section["bot_token_status"] = "unknown"
        section["degraded"] = True

    # Watchdog restart count (last hour)
    try:
        section["watchdog_restart_count"] = _watchdog_restart_count(state_dir)
    except Exception:
        section["watchdog_restart_count"] = 0
        section["degraded"] = True

    # Uptime
    section["uptime_seconds"] = _uptime_seconds()

    # Defcon: worst of all self signals
    defcon = 5
    pct = section.get("state_dir_pct")
    if pct is not None:
        if pct >= 95:
            defcon = min(defcon, 2)
        elif pct >= 80:
            defcon = min(defcon, 4)
    if section.get("bot_token_status") == "crit":
        defcon = min(defcon, 2)
    if section.get("watchdog_restart_count", 0) >= 3:
        defcon = min(defcon, 2)
    section["self_defcon"] = defcon

    # Summary line for dashboard / Telegram
    parts = []
    if section.get("state_dir_pct") is not None:
        parts.append(f"disk:{section['state_dir_pct']:.0f}%")
    parts.append(f"audit:{section['audit_size_bytes'] // (1024 * 1024)}MB")
    parts.append(f"bot:{section['bot_token_status']}")
    parts.append(f"watchdog:{section['watchdog_restart_count']}/h")
    section["summary"] = " | ".join(parts)

    return section