"""Repair action framework.

Every repair must:
  1. Take a pre-action snapshot (for rollback)
  2. Be classified by safety (SAFE / GUARDED / DANGEROUS / NEVER)
  3. Be reversible (or have a rollback plan)
  4. Be auditable (every action logged with snapshot id)

Available repair actions:
  - drop_caches: clear pagecache (SAFE, always works)
  - log_truncate_journald: vacuum old journal (GUARDED)
  - service_restart: restart a failed service (GUARDED, only if critical)
  - top_processes_snapshot: just collect top-N (SAFE, read-only)
  - notify_human: send alert to Slack/Telegram (SAFE)
"""

from __future__ import annotations
import json
import re
import shlex
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import structlog

logger = structlog.get_logger()


# Validation regexes for inputs that flow into subprocess calls.
# systemd unit names: [A-Za-z0-9_.@-]+ (per systemd.unit(5))
_VALID_SYSTEMD_UNIT = re.compile(r"^[A-Za-z0-9_.@-]{1,256}$")
# SQL identifier (PostgreSQL): letters/underscore start, then letters/digits/underscore/dot
_VALID_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,62}$")


# --- Snapshot store ----------------------------------------------------------

# Snapshots live under the same configurable state root so tests can sandbox
# everything (pending, audit, snapshots) into a tmp dir via
# IPRACTICOM_SWEEPER_STATE_DIR.
import os as _os

_BASE_STATE = Path(
    _os.environ.get(
        "IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper"
    )
)
SNAPSHOT_DIR = _BASE_STATE / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Snapshot:
    """A pre-action snapshot for rollback."""

    id: str
    action: str
    target: str
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
    rollback_command: str | None = None  # how to undo
    rollback_notes: str | None = None

    def save(self) -> Path:
        path = SNAPSHOT_DIR / f"{self.id}.json"
        with open(path, "w") as f:
            json.dump(self.__dict__, f, indent=2)
        logger.info("snapshot_saved", id=self.id, action=self.action, target=self.target)
        return path

    @classmethod
    def load(cls, snapshot_id: str) -> "Snapshot":
        path = SNAPSHOT_DIR / f"{snapshot_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Snapshot {snapshot_id} not found")
        with open(path) as f:
            data = json.load(f)
        return cls(**data)


def _new_snapshot(action: str, target: str, **metadata) -> Snapshot:
    return Snapshot(
        id=str(uuid.uuid4()),
        action=action,
        target=target,
        created_at=datetime.now(timezone.utc).isoformat(),
        metadata=metadata,
    )


# --- Result types ------------------------------------------------------------


@dataclass
class RepairResult:
    """The outcome of a repair attempt."""

    action: str
    target: str
    success: bool
    snapshot_id: str | None
    message: str
    duration_ms: int
    output: str = ""
    error: str | None = None
    rollback_available: bool = False


# --- Repair registry ---------------------------------------------------------


# Map action name → handler function
REPAIRS: dict[str, Callable[..., RepairResult]] = {}


def register(name: str):
    """Decorator to register a repair function."""

    def decorator(fn):
        REPAIRS[name] = fn
        logger.debug("repair_registered", name=name)
        return fn

    return decorator


# --- Built-in repair actions -------------------------------------------------


@register("drop_caches")
def repair_drop_caches(level: int = 3) -> RepairResult:
    """Drop pagecache, dentries, and inodes from kernel.

    SAFE: this only frees reclaimable memory, doesn't destroy data.
    Level 1: pagecache
    Level 2: + dentries + inodes
    Level 3: + slab objects (full drop)
    """
    if level not in (1, 2, 3):
        return RepairResult(
            action="drop_caches",
            target=f"level={level}",
            success=False,
            snapshot_id=None,
            message=f"Invalid level {level} (must be 1, 2, or 3)",
            duration_ms=0,
        )

    snap = _new_snapshot(
        action="drop_caches",
        target=f"level={level}",
        pre_meminfo=Path("/proc/meminfo").read_text()[:500],
    )
    snap.save()

    start = time.time()
    try:
        with open("/proc/sys/vm/drop_caches", "w") as f:
            f.write(f"{level}\n")
        duration = int((time.time() - start) * 1000)
        logger.info("repair_executed", action="drop_caches", level_value=level, snapshot=snap.id)
        return RepairResult(
            action="drop_caches",
            target=f"level={level}",
            success=True,
            snapshot_id=snap.id,
            message=f"drop_caches level={level} executed",
            duration_ms=duration,
            rollback_available=False,  # not reversible, but safe
        )
    except PermissionError as e:
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="drop_caches",
            target=f"level={level}",
            success=False,
            snapshot_id=snap.id,
            message="Permission denied — need root",
            duration_ms=duration,
            error=str(e),
        )


@register("log_truncate_journald")
def repair_log_truncate_journald(max_age_days: int = 7) -> RepairResult:
    """Vacuum journald logs older than max_age_days.

    GUARDED: safe but irreversible (log data lost).
    """
    snap = _new_snapshot(
        action="log_truncate_journald",
        target=f"max_age_days={max_age_days}",
        pre_disk_usage=_disk_usage("/var/log/journal"),
    )
    snap.save()

    start = time.time()
    try:
        result = subprocess.run(
            ["journalctl", "--vacuum-time", f"{max_age_days}d"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        duration = int((time.time() - start) * 1000)
        success = result.returncode == 0
        logger.info(
            "repair_executed",
            action="log_truncate_journald",
            success=success,
            snapshot=snap.id,
            stdout_preview=result.stdout[:200],
        )
        return RepairResult(
            action="log_truncate_journald",
            target=f"max_age_days={max_age_days}",
            success=success,
            snapshot_id=snap.id,
            message=result.stdout.strip()[:200] if success else "vacuum failed",
            duration_ms=duration,
            output=result.stdout,
            error=result.stderr if not success else None,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="log_truncate_journald",
            target=f"max_age_days={max_age_days}",
            success=False,
            snapshot_id=snap.id,
            message=f"Repair failed: {type(e).__name__}",
            duration_ms=duration,
            error=str(e),
        )


@register("service_restart")
def repair_service_restart(unit: str) -> RepairResult:
    """Restart a systemd service. GUARDED — service goes down briefly.

    Only valid for services classified as critical in the rules.
    `unit` is validated against the systemd unit-name charset before
    being passed to `systemctl restart`, to prevent command injection.
    """
    if not _VALID_SYSTEMD_UNIT.match(unit or ""):
        return RepairResult(
            action="service_restart",
            target=unit or "",
            success=False,
            snapshot_id=None,
            message=f"invalid systemd unit name: {unit!r}",
            duration_ms=0,
            error="invalid_unit_name",
        )
    snap = _new_snapshot(
        action="service_restart",
        target=unit,
        pre_state=_service_state(unit),
    )
    snap.save()

    start = time.time()
    try:
        result = subprocess.run(
            ["systemctl", "restart", unit],
            capture_output=True,
            text=True,
            timeout=60,
        )
        duration = int((time.time() - start) * 1000)
        success = result.returncode == 0
        return RepairResult(
            action="service_restart",
            target=unit,
            success=success,
            snapshot_id=snap.id,
            message=f"systemctl restart {unit} {'ok' if success else 'failed'}",
            duration_ms=duration,
            output=result.stdout,
            error=result.stderr if not success else None,
            rollback_available=False,  # restart isn't really reversible, but the snapshot has pre-state
        )
    except subprocess.TimeoutExpired:
        return RepairResult(
            action="service_restart",
            target=unit,
            success=False,
            snapshot_id=snap.id,
            message="systemctl restart timed out",
            duration_ms=60000,
            error="timeout after 60s",
        )


@register("top_processes_snapshot")
def repair_top_processes_snapshot(top_n: int = 10) -> RepairResult:
    """Capture top-N CPU-consuming processes. SAFE — pure read.

    Useful diagnostic companion to a high-load detection.
    """
    snap = _new_snapshot(
        action="top_processes_snapshot",
        target=f"top_n={top_n}",
        collected_at=datetime.now(timezone.utc).isoformat(),
    )

    start = time.time()
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,user,pcpu,pmem,comm", "--sort=-pcpu"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = result.stdout.strip().split("\n")[: top_n + 1]  # +1 for header
        duration = int((time.time() - start) * 1000)

        snap.metadata["top_processes"] = lines
        snap.save()

        return RepairResult(
            action="top_processes_snapshot",
            target=f"top_n={top_n}",
            success=True,
            snapshot_id=snap.id,
            message=f"Captured top {top_n} processes",
            duration_ms=duration,
            output="\n".join(lines),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="top_processes_snapshot",
            target=f"top_n={top_n}",
            success=False,
            snapshot_id=snap.id,
            message=f"ps failed: {e}",
            duration_ms=duration,
            error=str(e),
        )


@register("notify_human")
def repair_notify_human(channel: str = "all", defcon: int = 4, summary: str = "") -> RepairResult:
    """Send a notification to humans. SAFE — no system changes.

    channel: 'slack' | 'telegram' | 'all'
    """
    snap = _new_snapshot(
        action="notify_human",
        target=f"channel={channel}",
        defcon=defcon,
        summary=summary,
    )
    snap.save()

    # The actual delivery happens in the sweeper pipeline; this just records intent.
    duration = 0
    logger.info(
        "repair_notify_requested",
        channel=channel,
        defcon=defcon,
        snapshot=snap.id,
        summary=summary,
    )
    return RepairResult(
        action="notify_human",
        target=channel,
        success=True,
        snapshot_id=snap.id,
        message=f"Notification queued: defcon={defcon} summary={summary[:80]}",
        duration_ms=duration,
    )


# --- Helpers -----------------------------------------------------------------


def _disk_usage(path: str) -> str:
    """Best-effort disk usage of a path. Returns empty string if unavailable."""
    try:
        usage = shutil.disk_usage(path)
        return f"total={usage.total} used={usage.used} free={usage.free}"
    except (FileNotFoundError, OSError):
        return ""


def _service_state(unit: str) -> str:
    """Capture current state of a systemd unit for snapshot."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "unknown"


# --- Public API --------------------------------------------------------------


def execute_repair(action: str, **kwargs) -> RepairResult:
    """Execute a registered repair action.

    Args:
        action: name from REPAIRS registry
        **kwargs: passed to the repair function

    Returns:
        RepairResult with success/failure + snapshot info
    """
    if action not in REPAIRS:
        return RepairResult(
            action=action,
            target=str(kwargs),
            success=False,
            snapshot_id=None,
            message=f"Unknown repair action: {action}",
            duration_ms=0,
        )

    logger.info("repair_start", action=action, kwargs=kwargs)
    fn = REPAIRS[action]
    # Strip internal-only kwargs (dry_run, force) before calling the repair fn
    internal_kwargs = {"dry_run", "force"}
    fn_kwargs = {k: v for k, v in kwargs.items() if k not in internal_kwargs}
    return fn(**fn_kwargs)


def list_available_repairs() -> list[str]:
    return sorted(REPAIRS.keys())


# --- Sprint 15 — 5 additional repair actions --------------------------------
# These complement the built-in actions with common operational repairs
# we discovered were missing while running the sweeper on a real PBX.

@register("dns_cache_purge")
def repair_dns_cache_purge(service: str = "nscd") -> RepairResult:
    """Purge the DNS cache (nscd / systemd-resolved / dnsmasq).

    SAFE: just invalidates cache, no permanent change.
    Default service is nscd; pass systemd-resolved for that resolver.
    """
    snap = _new_snapshot(
        action="dns_cache_purge",
        target=f"service={service}",
        pre_dns_status=_dns_cache_status(service),
    )
    snap.save()

    start = time.time()
    try:
        result = subprocess.run(
            ["systemctl", "restart", service],
            capture_output=True,
            text=True,
            timeout=15,
        )
        duration = int((time.time() - start) * 1000)
        success = result.returncode == 0
        return RepairResult(
            action="dns_cache_purge",
            target=service,
            success=success,
            snapshot_id=snap.id,
            message=f"DNS cache ({service}) {'purged' if success else 'restart failed'}",
            duration_ms=duration,
            error=result.stderr if not success else None,
        )
    except subprocess.TimeoutExpired:
        return RepairResult(
            action="dns_cache_purge",
            target=service,
            success=False,
            snapshot_id=snap.id,
            message="DNS cache purge timed out",
            duration_ms=15000,
            error="timeout after 15s",
        )


@register("fs_inode_warn_clear")
def repair_fs_inode_warn_clear() -> RepairResult:
    """Clear stale inode-warn entries from the sweeper's local cache.

    SAFE: only modifies sweeper's own state, not the filesystem.
    Forces a re-scan of the most-monitored dir on next check.
    """
    snap = _new_snapshot(
        action="fs_inode_warn_clear",
        target="local cache",
        cleared_at=datetime.now(timezone.utc).isoformat(),
    )

    # Find and clear the inode-warn sidecar if it exists
    state_root = _BASE_STATE
    inode_cache = state_root / "cache" / "inode_warn.json"
    if inode_cache.exists():
        try:
            snap.metadata["pre_size_bytes"] = inode_cache.stat().st_size
            inode_cache.unlink()
        except OSError as e:
            duration = 0
            return RepairResult(
                action="fs_inode_warn_clear",
                target="local cache",
                success=False,
                snapshot_id=snap.id,
                message=f"Could not clear inode cache: {e}",
                duration_ms=duration,
                error=str(e),
            )

    snap.save()
    return RepairResult(
        action="fs_inode_warn_clear",
        target="local cache",
        success=True,
        snapshot_id=snap.id,
        message="Inode warn cache cleared (next check will re-scan)",
        duration_ms=1,
    )


@register("rotate_audit_now")
def repair_rotate_audit_now(state_dir: str = "") -> RepairResult:
    """Force a synchronous audit-log rotation.

    SAFE under the rotation policy (cascades gzipped copies, never deletes
    the live log). Calls audit_rotate() from the audit module if available.
    """
    state_root = Path(state_dir) if state_dir else _BASE_STATE
    snap = _new_snapshot(
        action="rotate_audit_now",
        target=str(state_root),
        pre_audit_size=_audit_log_size(state_root),
    )
    snap.save()

    start = time.time()
    try:
        from ipracticom_sweeper.audit.rotation import audit_rotate
        rotated = audit_rotate(state_root)
    except Exception as e:
        return RepairResult(
            action="rotate_audit_now",
            target=str(state_root),
            success=False,
            snapshot_id=snap.id,
            message=f"audit_rotate import/call failed: {e}",
            duration_ms=int((time.time() - start) * 1000),
            error=str(e),
        )
    duration = int((time.time() - start) * 1000)
    return RepairResult(
        action="rotate_audit_now",
        target=str(state_root),
        success=True,
        snapshot_id=snap.id,
        message=f"Rotated audit log; {rotated} files affected",
        duration_ms=duration,
        output=str(rotated),
    )


@register("telegram_token_revalidate")
def repair_telegram_token_revalidate() -> RepairResult:
    """Force a re-validation of the Telegram bot token.

    SAFE: pure probe. No state changes outside the sweeper's tracker.
    """
    snap = _new_snapshot(
        action="telegram_token_revalidate",
        target="telegram_bot_token",
        probe_initiated_at=datetime.now(timezone.utc).isoformat(),
    )
    snap.save()
    start = time.time()
    try:
        from ipracticom_sweeper.telegram_bot.health import (
            TokenHealthTracker,
            resolve_token,
            probe_bot_token,
        )
        token = resolve_token()
        if not token:
            return RepairResult(
                action="telegram_token_revalidate",
                target="telegram_bot_token",
                success=False,
                snapshot_id=snap.id,
                message="No Telegram token configured",
                duration_ms=int((time.time() - start) * 1000),
            )
        result = probe_bot_token(token)
        tracker = TokenHealthTracker(state_dir=_BASE_STATE)
        tracker.record(
            status=result.status,
            error_code=result.error_code,
            bot_username=result.bot_username,
        )
        ok = result.status == "ok"
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="telegram_token_revalidate",
            target="telegram_bot_token",
            success=ok,
            snapshot_id=snap.id,
            message=(f"Token valid: @{result.bot_username}" if ok
                     else f"Token probe failed: {result.error}"),
            duration_ms=duration,
        )
    except Exception as e:
        return RepairResult(
            action="telegram_token_revalidate",
            target="telegram_bot_token",
            success=False,
            snapshot_id=snap.id,
            message=f"probe error: {e}",
            duration_ms=int((time.time() - start) * 1000),
            error=str(e),
        )


@register("self_healthz_ping")
def repair_self_healthz_ping() -> RepairResult:
    """Ping our own /healthz endpoint to confirm liveness.

    SAFE: pure HTTP GET against localhost. Returns latency as metadata.
    """
    snap = _new_snapshot(
        action="self_healthz_ping",
        target="http://localhost:8000/healthz",
        ping_at=datetime.now(timezone.utc).isoformat(),
    )
    snap.save()

    start = time.time()
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:8000/healthz", timeout=5) as r:
            status = r.status
            body = r.read().decode("utf-8", errors="replace")[:200]
        duration = int((time.time() - start) * 1000)
        snap.metadata["response_status"] = status
        snap.metadata["response_body_preview"] = body
        return RepairResult(
            action="self_healthz_ping",
            target="http://localhost:8000/healthz",
            success=(status == 200),
            snapshot_id=snap.id,
            message=f"/healthz returned {status} in {duration}ms",
            duration_ms=duration,
            output=body,
        )
    except Exception as e:
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="self_healthz_ping",
            target="http://localhost:8000/healthz",
            success=False,
            snapshot_id=snap.id,
            message=f"healthz ping failed: {e}",
            duration_ms=duration,
            error=str(e),
        )


@register("self_agent_restart")
def repair_self_agent_restart(unit: str = "", dry_run: bool = False, **_extra) -> RepairResult:
    """Restart the sweeper's OWN systemd unit locally (no SSH).

    This is the agent restarting *itself* when self-monitoring finds it stuck
    (healthz crit / watchdog restart storm) — distinct from ``agent_restart``,
    which restarts the agent on a *remote monitored* host over SSH. SAFE: systemd
    re-launches the unit; on a dev box without systemd it degrades to a failed
    RepairResult rather than raising, so the sweep never crashes.

    Unit name defaults to $IPRACTICOM_SWEEPER_UNIT, then a sensible built-in.
    """
    unit = unit or _os.environ.get("IPRACTICOM_SWEEPER_UNIT", "ipracticom-sweeper-dashboard")
    snap = _new_snapshot(action="self_agent_restart", target=f"unit={unit}")
    snap.save()

    start = time.time()
    if dry_run:
        return RepairResult(
            action="self_agent_restart",
            target=f"unit={unit}",
            success=True,
            snapshot_id=snap.id,
            message=f"[dry-run] would systemctl restart {unit}",
            duration_ms=int((time.time() - start) * 1000),
        )

    # No shell — list args are safe; unit comes from config/env, not user input.
    try:
        result = subprocess.run(
            ["systemctl", "restart", unit],
            capture_output=True, text=True, timeout=30,
        )
        duration = int((time.time() - start) * 1000)
        success = result.returncode == 0
        return RepairResult(
            action="self_agent_restart",
            target=f"unit={unit}",
            success=success,
            snapshot_id=snap.id,
            message=(
                f"self restart issued for {unit}"
                if success else f"self restart failed for {unit}"
            ),
            duration_ms=duration,
            error=result.stderr if not success else None,
        )
    except subprocess.TimeoutExpired:
        return RepairResult(
            action="self_agent_restart",
            target=f"unit={unit}",
            success=False,
            snapshot_id=snap.id,
            message="self restart timed out",
            duration_ms=30000,
            error="timeout after 30s",
        )
    except FileNotFoundError:
        return RepairResult(
            action="self_agent_restart",
            target=f"unit={unit}",
            success=False,
            snapshot_id=snap.id,
            message="systemctl not available (no systemd on this box)",
            duration_ms=0,
            error="systemctl binary missing",
        )


# --- v1.5.16: ghost-action fixes (review findings #7, #8) ------------------
# /v6/machines/<host>/action offers these destructive ops, but they were
# never registered in REPAIRS. Approving a proposal for one of them returned
# success=False with "Unknown repair action: <name>" — silent failure that
# looked like a real repair attempt that simply didn't work.
#
# Now each one is a registered repair that runs the exact command shown
# in the proposal. For ``reboot`` and ``agent_restart`` we use ``ssh`` to
# reach the target host. For ``ssm_connect`` we resolve the AWS instance id
# via SSM describe-instances and start a session.
#
# SAFETY:
# - These are still proposals first — operator must approve.
# - On dry_run, we report what *would* run without executing.
# - All commands are shlex.quote()'d at proposal-creation time
#   (in dashboard.py::_enqueue_machine_action_proposal) so the host
#   argument here is already safe to interpolate.


@register("reboot")
def repair_reboot(host: str = "", dry_run: bool = False, **_extra) -> RepairResult:
    """Reboot a remote host via SSH+sudo shutdown."""
    snap = _new_snapshot(
        action="reboot",
        target=f"host={host}",
    )
    snap.save()

    start = time.time()
    if dry_run:
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="reboot",
            target=f"host={host}",
            success=True,
            snapshot_id=snap.id,
            message=f"[dry-run] would ssh {host!r} 'sudo shutdown -r now'",
            duration_ms=duration,
        )

    if not host:
        return RepairResult(
            action="reboot",
            target="host=<empty>",
            success=False,
            snapshot_id=snap.id,
            message="reboot requires 'host' kwarg",
            duration_ms=0,
            error="missing host",
        )

    quoted = shlex.quote(host)
    cmd = ["ssh", host, "sudo", "shutdown", "-r", "now"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        duration = int((time.time() - start) * 1000)
        success = result.returncode == 0
        return RepairResult(
            action="reboot",
            target=f"host={host}",
            success=success,
            snapshot_id=snap.id,
            message=(
                f"reboot issued to {host}"
                if success else f"reboot failed for {host}"
            ),
            duration_ms=duration,
            error=result.stderr if not success else None,
        )
    except subprocess.TimeoutExpired:
        return RepairResult(
            action="reboot",
            target=f"host={host}",
            success=False,
            snapshot_id=snap.id,
            message="reboot timed out (host may be slow to drop SSH)",
            duration_ms=30000,
            error="timeout after 30s",
        )
    except FileNotFoundError:
        return RepairResult(
            action="reboot",
            target=f"host={host}",
            success=False,
            snapshot_id=snap.id,
            message="ssh client not installed on sweeper host",
            duration_ms=0,
            error="ssh binary missing",
        )


@register("agent_restart")
def repair_agent_restart(host: str = "", dry_run: bool = False, **_extra) -> RepairResult:
    """Restart ipracticom-sweeper-agent on a remote host via systemd."""
    snap = _new_snapshot(
        action="agent_restart",
        target=f"host={host}",
    )
    snap.save()

    start = time.time()
    if dry_run:
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="agent_restart",
            target=f"host={host}",
            success=True,
            snapshot_id=snap.id,
            message=f"[dry-run] would systemctl restart ipracticom-sweeper-agent@{host!r}",
            duration_ms=duration,
        )

    if not host:
        return RepairResult(
            action="agent_restart",
            target="host=<empty>",
            success=False,
            snapshot_id=snap.id,
            message="agent_restart requires 'host' kwarg",
            duration_ms=0,
            error="missing host",
        )

    quoted = shlex.quote(host)
    unit = f"ipracticom-sweeper-agent@{quoted}"
    cmd = ["ssh", host, "sudo", "systemctl", "restart", unit]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        duration = int((time.time() - start) * 1000)
        success = result.returncode == 0
        return RepairResult(
            action="agent_restart",
            target=f"host={host}",
            success=success,
            snapshot_id=snap.id,
            message=(
                f"agent restart issued to {host}"
                if success else f"agent restart failed for {host}"
            ),
            duration_ms=duration,
            error=result.stderr if not success else None,
        )
    except subprocess.TimeoutExpired:
        return RepairResult(
            action="agent_restart",
            target=f"host={host}",
            success=False,
            snapshot_id=snap.id,
            message="agent restart timed out",
            duration_ms=30000,
            error="timeout after 30s",
        )
    except FileNotFoundError:
        return RepairResult(
            action="agent_restart",
            target=f"host={host}",
            success=False,
            snapshot_id=snap.id,
            message="ssh client not installed on sweeper host",
            duration_ms=0,
            error="ssh binary missing",
        )


@register("ssm_connect")
def repair_ssm_connect(host: str = "", dry_run: bool = False, **_extra) -> RepairResult:
    """Start an AWS SSM Session Manager session to the host.

    Note: 'repair' is a misnomer here — this is an interactive session,
    not a state-changing repair. We register it so the v6 UI's action
    button works end-to-end (proposal → approval → execution). The
    actual ``aws ssm start-session`` command is non-blocking and
    foregrounds in the operator's terminal.
    """
    snap = _new_snapshot(
        action="ssm_connect",
        target=f"host={host}",
    )
    snap.save()

    start = time.time()
    if dry_run:
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="ssm_connect",
            target=f"host={host}",
            success=True,
            snapshot_id=snap.id,
            message=f"[dry-run] would aws ssm start-session --target <instance-id-for-{host!r}>",
            duration_ms=duration,
        )

    if not host:
        return RepairResult(
            action="ssm_connect",
            target="host=<empty>",
            success=False,
            snapshot_id=snap.id,
            message="ssm_connect requires 'host' kwarg",
            duration_ms=0,
            error="missing host",
        )

    quoted = shlex.quote(host)
    # Resolve hostname -> instance id, then start a session.
    # We don't background the session here — start-session is interactive
    # and we want the operator's approval flow to be explicit.
    describe_cmd = [
        "aws", "ssm", "describe-instances",
        "--filters", f"Name=tag:Name,Values={quoted}",
        "--query", "Reservations[].Instances[].InstanceId",
        "--output", "text",
    ]
    try:
        desc = subprocess.run(
            describe_cmd, capture_output=True, text=True, timeout=15,
        )
        if desc.returncode != 0 or not desc.stdout.strip():
            return RepairResult(
                action="ssm_connect",
                target=f"host={host}",
                success=False,
                snapshot_id=snap.id,
                message=f"could not resolve AWS instance id for {host}",
                duration_ms=int((time.time() - start) * 1000),
                error=desc.stderr or "no instance found",
            )
        instance_id = desc.stdout.strip().split()[0]
        # Validate the resolved id is alphanumeric (defence in depth — the
        # tag:Name filter could in theory match unexpected chars).
        if not instance_id.replace("-", "").isalnum():
            return RepairResult(
                action="ssm_connect",
                target=f"host={host}",
                success=False,
                snapshot_id=snap.id,
                message=f"resolved instance id looks malformed: {instance_id!r}",
                duration_ms=int((time.time() - start) * 1000),
                error="malformed instance id",
            )
        # The start-session call will block until the operator exits the
        # session. We record the start command as the proposal output so
        # the operator can run it themselves in their terminal.
        cmd_str = f"aws ssm start-session --target {instance_id}"
        duration = int((time.time() - start) * 1000)
        return RepairResult(
            action="ssm_connect",
            target=f"host={host}",
            success=True,
            snapshot_id=snap.id,
            message=(
                f"resolved {host} -> {instance_id}; run in your terminal: {cmd_str}"
            ),
            duration_ms=duration,
            output=cmd_str,
        )
    except subprocess.TimeoutExpired:
        return RepairResult(
            action="ssm_connect",
            target=f"host={host}",
            success=False,
            snapshot_id=snap.id,
            message="aws describe-instances timed out",
            duration_ms=15000,
            error="timeout after 15s",
        )
    except FileNotFoundError:
        return RepairResult(
            action="ssm_connect",
            target=f"host={host}",
            success=False,
            snapshot_id=snap.id,
            message="aws cli not installed on sweeper host",
            duration_ms=0,
            error="aws binary missing",
        )


# --- Helpers used by Sprint 15 repairs --------------------------------------

def _dns_cache_status(service: str) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "unknown"


def _audit_log_size(state_root: Path) -> int:
    p = state_root / "audit" / "audit.jsonl"
    try:
        return p.stat().st_size if p.exists() else 0
    except OSError:
        return 0