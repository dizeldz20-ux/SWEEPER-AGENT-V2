"""Pending repair approvals.

When the pipeline wants to run a repair classified as `needs_approval`,
it writes a proposal to /var/lib/ipracticom-sweeper/pending_repairs/
instead of executing it. The operator reviews via /approvals and either
approves (we run the repair and log it) or rejects (we mark the
proposal as rejected and audit-log it).

File layout:
    pending_repairs/
      <id>.json      # proposal awaiting decision
      approved/      # moved here after approval
      rejected/      # moved here after rejection
    audit/
      repairs.jsonl  # every auto + approved-by-user repair
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .._log import log_suppressed
from typing import Any

# Paths are configurable so tests can sandbox into a tmp dir without
# leaking fake audit entries into the production /var/lib store.
import os

_BASE_STATE = Path(
    os.environ.get(
        "IPRACTICOM_SWEEPER_STATE_DIR", "/var/lib/ipracticom-sweeper"
    )
)
PENDING_DIR = _BASE_STATE / "pending_repairs"
APPROVED_DIR = PENDING_DIR / "approved"
REJECTED_DIR = PENDING_DIR / "rejected"

# Proposal IDs are produced by uuid.uuid4().hex[:12] (see create_proposal).
# Validating strictly prevents path-traversal via crafted URL params.
_PID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _validate_pid(pid: str) -> None:
    """Reject pids with path-traversal or shell-meta characters.

    Raises ValueError on bad input. Empty string, NUL bytes, slashes, `..`,
    leading dots etc. are all rejected.
    """
    if not isinstance(pid, str) or not _PID_RE.match(pid):
        raise ValueError(f"invalid pid: {pid!r}")


# v1.5.15 (C-2 fix): per-proposal flock so the status-check + execute_repair
# decision is atomic. Without this, two concurrent /approvals/<pid>/approve
# requests both pass the "is pending" check, both call execute_repair, and
# the repair runs twice (e.g. service_restart on a prod PBX = double restart).
# The lock file lives next to the proposal JSON and is acquired as a real OS
# fcntl.flock — releases automatically if the process dies.
import contextlib
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows dev only; target is Linux
    # No fcntl on Windows. _proposal_lock's AttributeError branch handles a
    # None here, so the dashboard degrades to a no-op lock (documented below).
    fcntl = None  # type: ignore[assignment]


@contextlib.contextmanager
def _proposal_lock(pid: str):
    """Exclusive flock on <pid>.lock for the duration of the with-block.

    Used by the approval/reject routes to serialize decisions per proposal.
    Falls back to a no-op on platforms without fcntl (e.g. Windows) so tests
    still pass — the dashboard does not run on Windows in production.
    """
    _validate_pid(pid)
    lock_path = PENDING_DIR / f"{pid}.lock"
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    fd = None
    try:
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except (OSError, AttributeError):
            # fcntl missing (Windows) — proceed without the lock; the
            # locking is a defense-in-depth, not the only guard.
            pass
        yield fd
    finally:
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except (OSError, AttributeError) as exc:
                log_suppressed("pending.proposal_unlock", exc)
            os.close(fd)
AUDIT_LOG = _BASE_STATE / "audit" / "repairs.jsonl"


@dataclass
class RepairProposal:
    """A pending repair that needs operator approval."""

    id: str
    action: str
    kwargs: dict[str, Any]
    reason: str          # human-readable: why the diagnose suggested this
    problem: dict[str, Any] | None  # originating problem (for context)
    proposed_command: str  # the exact command we WOULD run if approved
    snapshot_id: str | None  # if a pre-action snapshot was taken
    created_at: str
    created_at_ts: float
    status: str = "pending"  # pending | approved | rejected | executed | failed
    server: str = ""         # host the problem was detected on (for multi-channel push)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ensure_dirs() -> None:
    for d in (PENDING_DIR, APPROVED_DIR, REJECTED_DIR):
        d.mkdir(parents=True, exist_ok=True)
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)


def create_proposal(
    *,
    action: str,
    kwargs: dict[str, Any],
    reason: str,
    problem: dict[str, Any] | None = None,
    proposed_command: str,
    snapshot_id: str | None = None,
    server: str = "",
) -> RepairProposal:
    """Write a new pending proposal and return it."""
    _ensure_dirs()
    now = datetime.now(timezone.utc)
    proposal = RepairProposal(
        id=uuid.uuid4().hex[:12],
        action=action,
        kwargs=kwargs or {},
        reason=reason,
        problem=problem,
        proposed_command=proposed_command,
        snapshot_id=snapshot_id,
        created_at=now.isoformat(),
        created_at_ts=now.timestamp(),
        status="pending",
        server=server or "",
    )
    path = PENDING_DIR / f"{proposal.id}.json"
    path.write_text(json.dumps(proposal.to_dict(), indent=2, default=str, ensure_ascii=False))
    path.chmod(0o640)
    return proposal


def list_pending() -> list[RepairProposal]:
    """List all proposals currently awaiting decision."""
    _ensure_dirs()
    out: list[RepairProposal] = []
    for p in sorted(PENDING_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text())
            out.append(RepairProposal(**data))
        except Exception as e:
            log_suppressed("pending_list_read", e)
            continue
    out.sort(key=lambda x: x.created_at_ts, reverse=True)
    return out


def find_pending(action: str, server: str) -> RepairProposal | None:
    """Return the first pending proposal matching (action, server), or None.

    Used for dedup by the pipeline: when a problem persists across sweeps we
    must not create a second proposal (or re-push an approval request) for a
    repair that is already awaiting a human decision.
    """
    want_server = server or ""
    for p in list_pending():
        if p.action == action and (p.server or "") == want_server:
            return p
    return None


def get_proposal(pid: str) -> RepairProposal | None:
    """Read a single proposal by id. Looks in pending/, approved/, rejected/.

    Returns None if not found anywhere.
    """
    _validate_pid(pid)
    _ensure_dirs()
    for base in (PENDING_DIR, APPROVED_DIR, REJECTED_DIR):
        path = base / f"{pid}.json"
        if path.exists():
            try:
                return RepairProposal(**json.loads(path.read_text()))
            except Exception as e:
                log_suppressed("pending_get_proposal", e)
                continue
    return None


def set_status(pid: str, status: str) -> RepairProposal | None:
    """Update the proposal's status in-place."""
    _validate_pid(pid)
    p = get_proposal(pid)
    if p is None:
        return None
    p.status = status
    path = PENDING_DIR / f"{pid}.json"
    path.write_text(json.dumps(p.to_dict(), indent=2, default=str, ensure_ascii=False))
    return p


def archive(pid: str, subdir: str) -> bool:
    """Move a proposal file into approved/ or rejected/."""
    _validate_pid(pid)
    src = PENDING_DIR / f"{pid}.json"
    if not src.exists():
        return False
    dst = (APPROVED_DIR if subdir == "approved" else REJECTED_DIR) / f"{pid}.json"
    shutil.move(str(src), str(dst))
    return True


def log_audit(entry: dict[str, Any]) -> None:
    """Append an audit entry to the repairs log. Atomic write per line."""
    _ensure_dirs()
    entry = {**entry, "logged_at": datetime.now(timezone.utc).isoformat()}
    line = json.dumps(entry, default=str, ensure_ascii=False) + "\n"
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(line)


def cleanup_stale_pending(max_age_seconds: float = 7 * 24 * 3600) -> int:
    """Archive pending proposals older than max_age_seconds. Returns count."""
    _ensure_dirs()
    cutoff = time.time() - max_age_seconds
    moved = 0
    for p in list(PENDING_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text())
            ts = float(data.get("created_at_ts") or 0)
        except Exception:
            ts = 0
        if ts > 0 and ts < cutoff:
            data["status"] = "expired"
            (REJECTED_DIR / p.name).write_text(
                json.dumps(data, indent=2, default=str, ensure_ascii=False)
            )
            p.unlink()
            moved += 1
    return moved